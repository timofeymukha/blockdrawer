"""Block creation, removal, conformal splitting, and combination operations.

The mixin keeps MeshModel's long-standing public API while isolating compound
topology mutations and their atomic rollback logic from core data validation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Iterable

from .domain import (
    Block,
    BlockCombineResult,
    EdgeGeometry,
    EdgeKey,
    EdgeOccurrence,
    EdgeSplitResult,
    TopologyError,
    edge_key,
)
from .grading import _finite_expansion_ratio


@dataclass(frozen=True)
class _CombinePairPlan:
    kept_block_id: str
    removed_block_id: str
    merged_block: Block
    joins: tuple[
        tuple[tuple[str, str], tuple[str, str], tuple[str, str]],
        tuple[tuple[str, str], tuple[str, str], tuple[str, str]],
    ]


@dataclass(frozen=True)
class _CombinedEdgePlan:
    edge: EdgeKey
    source_edges: tuple[EdgeKey, EdgeKey]
    cells: int
    geometry: EdgeGeometry | None
    total_ratio: float
    boundary: str | None


class TopologyOperationsMixin:
    """Compound topology operations supplied to MeshModel."""

    def split_edge(self, selected: EdgeKey, fraction: float) -> EdgeSplitResult:
        """Split every block in an opposite-edge constraint strip atomically.

        ``fraction`` follows the selected edge's canonical direction. Each
        affected opposite edge uses the corresponding (possibly reversed)
        fraction, so the inserted vertices form a conformal cut without hanging
        topology. Existing subdivisions are divided at the nearest original mesh
        node; a one-cell strip necessarily becomes two one-cell strips.
        """
        selected = edge_key(*selected)
        first_cells, second_cells = self.edge_split_cell_counts(
            selected, fraction
        )

        orientations = self._edge_constraint_orientations(selected)
        affected = tuple(
            current for current in self.edges() if current in orientations
        )
        affected_set = set(affected)
        selected_cells = self.edge_cells[selected]
        if any(self.edge_cells[current] != selected_cells for current in affected):
            raise TopologyError("The split strip has inconsistent edge subdivisions")

        block_plans: dict[str, tuple[int, int]] = {}
        for block in self.blocks:
            indices = tuple(
                index
                for index in range(4)
                if edge_key(*block.directed_edge(index)) in affected_set
            )
            if not indices:
                continue
            if len(indices) != 2 or (indices[1] - indices[0]) % 4 != 2:
                raise TopologyError(
                    "The selected split strip branches or crosses itself inside "
                    f"block {block.id}"
                )
            block_plans[block.id] = (indices[0], indices[1])

        local_fraction: dict[EdgeKey, float] = {}
        split_points: dict[EdgeKey, tuple[float, float]] = {}
        split_cells: dict[EdgeKey, tuple[int, int]] = {}
        split_geometry: dict[
            EdgeKey, tuple[EdgeGeometry | None, EdgeGeometry | None]
        ] = {}
        split_grading: dict[EdgeKey, tuple[float, float]] = {}
        for current in affected:
            reversed_direction = orientations[current]
            current_fraction = 1.0 - fraction if reversed_direction else fraction
            current_first_cells = (
                second_cells if reversed_direction else first_cells
            )
            current_second_cells = (
                first_cells if reversed_direction else second_cells
            )
            point = self.edge_point(current, current_fraction)
            first_vertex = self.vertices[current[0]]
            second_vertex = self.vertices[current[1]]
            if self._coordinates_match(
                point[0], point[1], first_vertex.x, first_vertex.y
            ) or self._coordinates_match(
                point[0], point[1], second_vertex.x, second_vertex.y
            ):
                raise TopologyError("The split point is too close to an edge endpoint")
            local_fraction[current] = current_fraction
            split_points[current] = point
            split_cells[current] = (
                current_first_cells, current_second_cells
            )
            split_geometry[current] = self._split_edge_geometry(
                current, current_fraction
            )
            split_grading[current] = self._split_edge_grading(
                self.edge_total_expansion(current),
                selected_cells,
                current_first_cells,
                current_second_cells,
            )

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        geometry_before = dict(self.edge_geometry)
        grading_before = dict(self.edge_grading)
        spacing_links_before = set(self.spacing_links)
        edge_boundaries_before = dict(self.edge_boundaries)

        try:
            split_vertices = {
                current: self._vertex_at_or_new(*split_points[current])
                for current in affected
            }
            split_vertex_ids = tuple(dict.fromkeys(
                vertex.id for vertex in split_vertices.values()
            ))

            used_block_ids = {block.id for block in self.blocks}
            rebuilt_blocks: list[Block] = []
            new_block_ids: list[str] = []
            cut_plans: list[
                tuple[EdgeKey, int, str, str, float]
            ] = []
            for block in blocks_before:
                plan = block_plans.get(block.id)
                if plan is None:
                    rebuilt_blocks.append(block)
                    continue
                first, second, third, fourth = block.vertices
                edges = tuple(
                    edge_key(*block.directed_edge(index)) for index in range(4)
                )
                new_block_id = self._next_id("b", used_block_ids)
                used_block_ids.add(new_block_id)
                new_block_ids.append(new_block_id)

                if set(plan) == {0, 2}:
                    lower = split_vertices[edges[0]].id
                    upper = split_vertices[edges[2]].id
                    rebuilt_blocks.extend((
                        Block(block.id, (first, lower, upper, fourth)),
                        Block(new_block_id, (lower, second, third, upper)),
                    ))
                    directed_cut = (lower, upper)
                    transverse_cells = cells_before[edges[1]]
                    block_fraction = self._edge_fraction_in_direction(
                        edges[0], first, second, local_fraction[edges[0]]
                    )
                    left_ratio = self.edge_expansion_in_direction(first, fourth)
                    right_ratio = self.edge_expansion_in_direction(second, third)
                    cut_ratio = self._log_blended_ratio(
                        left_ratio, right_ratio, block_fraction
                    )
                else:
                    right = split_vertices[edges[1]].id
                    left = split_vertices[edges[3]].id
                    rebuilt_blocks.extend((
                        Block(block.id, (first, second, right, left)),
                        Block(new_block_id, (left, right, third, fourth)),
                    ))
                    directed_cut = (left, right)
                    transverse_cells = cells_before[edges[0]]
                    block_fraction = self._edge_fraction_in_direction(
                        edges[1], second, third, local_fraction[edges[1]]
                    )
                    lower_ratio = self.edge_expansion_in_direction(first, second)
                    upper_ratio = self.edge_expansion_in_direction(fourth, third)
                    cut_ratio = self._log_blended_ratio(
                        lower_ratio, upper_ratio, block_fraction
                    )
                cut_plans.append((
                    edge_key(*directed_cut),
                    transverse_cells,
                    directed_cut[0],
                    directed_cut[1],
                    cut_ratio,
                ))

            self.blocks = rebuilt_blocks
            actual_edges = set(self.edges())

            new_cells = {
                current: cells
                for current, cells in cells_before.items()
                if current in actual_edges and current not in affected_set
            }

            def assign_cells(current: EdgeKey, cells: int) -> None:
                existing = new_cells.get(current)
                if existing is not None and existing != cells:
                    raise TopologyError(
                        f"Split creates conflicting subdivisions on edge {current!r}"
                    )
                new_cells[current] = cells

            segments: dict[EdgeKey, tuple[EdgeKey, EdgeKey]] = {}
            for current in affected:
                split_id = split_vertices[current].id
                first_segment = edge_key(current[0], split_id)
                second_segment = edge_key(split_id, current[1])
                segments[current] = (first_segment, second_segment)
                current_first_cells, current_second_cells = split_cells[current]
                if any(
                    segment in cells_before and segment not in affected_set
                    for segment in (first_segment, second_segment)
                ):
                    raise TopologyError(
                        "The split would overlap an existing topology edge"
                    )
                assign_cells(first_segment, current_first_cells)
                assign_cells(second_segment, current_second_cells)
            for cut, cells, _first, _second, _ratio in cut_plans:
                if cut in cells_before:
                    raise TopologyError(
                        "The split would overlap an existing topology edge"
                    )
                assign_cells(cut, cells)
            if set(new_cells) != actual_edges:
                raise TopologyError(
                    "The split did not produce complete edge subdivision data"
                )
            self.edge_cells = new_cells

            new_geometry = {
                current: geometry
                for current, geometry in geometry_before.items()
                if current in actual_edges and current not in affected_set
            }
            for current in affected:
                split_id = split_vertices[current].id
                directed_segments = (
                    (current[0], split_id),
                    (split_id, current[1]),
                )
                for segment, directed, geometry in zip(
                    segments[current], directed_segments, split_geometry[current]
                ):
                    if geometry is None:
                        continue
                    if segment != directed:
                        geometry = self._reversed_edge_geometry(geometry)
                    existing = new_geometry.get(segment)
                    if existing is not None and existing != geometry:
                        raise TopologyError(
                            f"Split creates conflicting geometry on edge {segment!r}"
                        )
                    new_geometry[segment] = geometry
            self.edge_geometry = new_geometry

            new_grading = {
                current: ratio
                for current, ratio in grading_before.items()
                if current in actual_edges and current not in affected_set
            }

            def assign_grading(current: EdgeKey, ratio: float) -> None:
                if ratio == 1.0:
                    return
                existing = new_grading.get(current)
                if existing is not None and not math.isclose(
                    existing, ratio, rel_tol=1.0e-12, abs_tol=0.0
                ):
                    raise TopologyError(
                        f"Split creates conflicting grading on edge {current!r}"
                    )
                new_grading[current] = ratio

            for current in affected:
                split_id = split_vertices[current].id
                directed_segments = (
                    (current[0], split_id),
                    (split_id, current[1]),
                )
                for segment, directed, ratio in zip(
                    segments[current], directed_segments, split_grading[current]
                ):
                    assign_grading(
                        segment, ratio if segment == directed else 1.0 / ratio
                    )
            for cut, _cells, first_id, second_id, directed_ratio in cut_plans:
                canonical_ratio = (
                    directed_ratio
                    if cut == (first_id, second_id)
                    else 1.0 / directed_ratio
                )
                assign_grading(cut, canonical_ratio)
            self.edge_grading = new_grading

            spacing_replacements: dict[
                tuple[EdgeKey, str], EdgeKey | None
            ] = {}
            for current in affected:
                first_segment, second_segment = segments[current]
                spacing_replacements[(current, current[0])] = first_segment
                spacing_replacements[(current, current[1])] = second_segment
            self._remap_spacing_link_endpoints(spacing_replacements)

            new_boundaries = {
                current: name
                for current, name in edge_boundaries_before.items()
                if current in actual_edges and current not in affected_set
            }
            for current in affected:
                name = edge_boundaries_before.get(current)
                if name is None:
                    continue
                for segment in segments[current]:
                    existing = new_boundaries.get(segment)
                    if existing is not None and existing != name:
                        raise TopologyError(
                            f"Split creates conflicting boundaries on edge {segment!r}"
                        )
                    new_boundaries[segment] = name
            self.edge_boundaries = new_boundaries
            self._prune_boundary_assignments()
            self.validate()

            selected_segments = segments[selected]
            return EdgeSplitResult(
                selected,
                float(fraction),
                first_cells,
                second_cells,
                affected,
                split_vertex_ids,
                selected_segments,
                tuple(dict.fromkeys(plan[0] for plan in cut_plans)),
                tuple(new_block_ids),
            )
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_geometry = geometry_before
            self.edge_grading = grading_before
            self.spacing_links = spacing_links_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def edge_split_cell_counts(
        self, selected: EdgeKey, fraction: float
    ) -> tuple[int, int]:
        """Return the two subdivision counts selected by a split fraction."""
        selected = edge_key(*selected)
        if selected not in self.edge_cells:
            raise TopologyError(f"Unknown edge {selected!r}")
        if not math.isfinite(fraction) or not 0.0 < fraction < 1.0:
            raise TopologyError("The split fraction must be strictly between 0 and 1")
        cells = self.edge_cells[selected]
        if cells == 1:
            return 1, 1
        first_cells = min(
            range(1, cells),
            key=lambda index: (
                abs(self.edge_node_fraction(selected, index) - fraction),
                index,
            ),
        )
        return first_cells, cells - first_cells

    def _split_edge_geometry(
        self, current: EdgeKey, fraction: float
    ) -> tuple[EdgeGeometry | None, EdgeGeometry | None]:
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return None, None
        if geometry.kind == "arc":
            return (
                EdgeGeometry("arc", (self.edge_point(current, fraction / 2.0),)),
                EdgeGeometry(
                    "arc", (self.edge_point(current, (fraction + 1.0) / 2.0),)
                ),
            )
        if geometry.kind == "polyLine":
            first = self.vertices[current[0]]
            second = self.vertices[current[1]]
            path = [
                (first.x, first.y), *geometry.points, (second.x, second.y)
            ]
            lengths = [
                math.hypot(end[0] - start[0], end[1] - start[1])
                for start, end in zip(path, path[1:])
            ]
            total_length = sum(lengths)
            split_point = self.edge_point(current, fraction)
            left_points: list[tuple[float, float]] = []
            right_points: list[tuple[float, float]] = []
            traversed = 0.0
            for point, preceding_length in zip(geometry.points, lengths):
                traversed += preceding_length
                point_fraction = traversed / total_length
                if math.dist(point, split_point) <= self.COORDINATE_TOLERANCE:
                    continue
                if point_fraction < fraction:
                    left_points.append(point)
                else:
                    right_points.append(point)
            if not left_points:
                left_points.append(self.edge_point(current, fraction / 2.0))
            if not right_points:
                right_points.append(
                    self.edge_point(current, (fraction + 1.0) / 2.0)
                )
            return (
                EdgeGeometry("polyLine", tuple(left_points)),
                EdgeGeometry("polyLine", tuple(right_points)),
            )

        point_count = len(geometry.points)
        if point_count == 1:
            left_count = right_count = 1
        else:
            left_count = max(
                1, min(point_count - 1, int(math.floor(
                    point_count * fraction + 0.5
                )))
            )
            right_count = point_count - left_count
        left_points = tuple(
            self.edge_point(
                current, fraction * index / (left_count + 1)
            )
            for index in range(1, left_count + 1)
        )
        right_points = tuple(
            self.edge_point(
                current,
                fraction + (1.0 - fraction) * index / (right_count + 1),
            )
            for index in range(1, right_count + 1)
        )
        return (
            EdgeGeometry("spline", left_points),
            EdgeGeometry("spline", right_points),
        )

    @staticmethod
    def _split_edge_grading(
        total_ratio: float,
        cells: int,
        first_cells: int,
        second_cells: int,
    ) -> tuple[float, float]:
        if cells == 1 or total_ratio == 1.0:
            return 1.0, 1.0
        log_cell_ratio = math.log(total_ratio) / (cells - 1)
        return (
            _finite_expansion_ratio((first_cells - 1) * log_cell_ratio),
            _finite_expansion_ratio((second_cells - 1) * log_cell_ratio),
        )

    @staticmethod
    def _reversed_edge_geometry(geometry: EdgeGeometry) -> EdgeGeometry:
        if geometry.kind == "arc":
            return geometry
        return EdgeGeometry(geometry.kind, tuple(reversed(geometry.points)))

    @staticmethod
    def _edge_fraction_in_direction(
        current: EdgeKey,
        first_id: str,
        second_id: str,
        canonical_fraction: float,
    ) -> float:
        return (
            canonical_fraction
            if current == (first_id, second_id)
            else 1.0 - canonical_fraction
        )

    @staticmethod
    def _log_blended_ratio(
        first_ratio: float, second_ratio: float, fraction: float
    ) -> float:
        logarithm = (
            (1.0 - fraction) * math.log(first_ratio)
            + fraction * math.log(second_ratio)
        )
        return _finite_expansion_ratio(logarithm)

    def can_combine_edge(self, selected: EdgeKey) -> bool:
        """Return whether ``selected`` is an internal two-block interface."""
        try:
            current = edge_key(*selected)
        except (TypeError, TopologyError):
            return False
        return len(self.edge_occurrences().get(current, ())) == 2

    def combine_blocks(self, selected: EdgeKey) -> BlockCombineResult:
        """Remove a conformal internal cut and merge each block pair across it.

        The selected edge's connected cut is followed through four-block
        junctions so the operation cannot leave hanging vertices. Consecutive
        outer edges are joined, their cell counts are summed, and their geometry,
        grading, and boundary assignment are retained when representable by one
        BlockMesh edge.
        """
        selected = edge_key(*selected)
        occurrences = self.edge_occurrences()
        if len(occurrences.get(selected, ())) != 2:
            raise TopologyError(
                "Blocks can only be combined across an internal edge shared by "
                "exactly two blocks"
            )

        cut_edges = self._combine_cut_component(selected, occurrences)
        cut_set = set(cut_edges)
        block_order = {
            block.id: index for index, block in enumerate(self.blocks)
        }
        pair_plans = tuple(
            self._combine_pair_plan(
                current, occurrences[current], block_order
            )
            for current in cut_edges
        )

        combined_edge_plans: dict[EdgeKey, _CombinedEdgePlan] = {}
        for pair in pair_plans:
            for first, second, target_direction in pair.joins:
                plan = self._combined_edge_plan(
                    first, second, target_direction
                )
                existing = combined_edge_plans.get(plan.edge)
                if existing is None:
                    if plan.edge in self.edge_cells \
                            and plan.edge not in plan.source_edges:
                        raise TopologyError(
                            "Combining these blocks would overlap an existing edge"
                        )
                    combined_edge_plans[plan.edge] = plan
                    continue
                if set(existing.source_edges) != set(plan.source_edges) \
                        or existing.cells != plan.cells \
                        or existing.geometry != plan.geometry \
                        or not math.isclose(
                            existing.total_ratio,
                            plan.total_ratio,
                            rel_tol=1.0e-11,
                            abs_tol=0.0,
                        ) \
                        or existing.boundary != plan.boundary:
                    raise TopologyError(
                        "The connected cut has inconsistent edge settings"
                    )

        source_join_edges = {
            current
            for plan in combined_edge_plans.values()
            for current in plan.source_edges
        }
        removed_topology_edges = cut_set | source_join_edges
        replaced_blocks = {
            pair.kept_block_id: pair.merged_block for pair in pair_plans
        }
        removed_block_ids = {
            pair.removed_block_id for pair in pair_plans
        }

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        geometry_before = dict(self.edge_geometry)
        grading_before = dict(self.edge_grading)
        spacing_links_before = set(self.spacing_links)
        edge_boundaries_before = dict(self.edge_boundaries)

        try:
            self.blocks = [
                replaced_blocks.get(block.id, block)
                for block in blocks_before
                if block.id not in removed_block_ids
            ]
            actual_edges = set(self.edges())

            self.edge_cells = {
                current: cells
                for current, cells in cells_before.items()
                if current in actual_edges
                and current not in removed_topology_edges
            }
            for current, plan in combined_edge_plans.items():
                if current not in actual_edges:
                    raise TopologyError(
                        "Combined edge data does not match the resulting topology"
                    )
                existing = self.edge_cells.get(current)
                if existing is not None and existing != plan.cells:
                    raise TopologyError(
                        f"Combined edge {current!r} has conflicting cell counts"
                    )
                self.edge_cells[current] = plan.cells
            if set(self.edge_cells) != actual_edges:
                raise TopologyError(
                    "Combining blocks did not produce complete edge cell data"
                )

            self.edge_geometry = {
                current: geometry
                for current, geometry in geometry_before.items()
                if current in actual_edges
                and current not in removed_topology_edges
            }
            for current, plan in combined_edge_plans.items():
                if plan.geometry is not None:
                    self.edge_geometry[current] = plan.geometry

            self.edge_grading = {
                current: ratio
                for current, ratio in grading_before.items()
                if current in actual_edges
                and current not in removed_topology_edges
            }
            for current, plan in combined_edge_plans.items():
                if not math.isclose(
                    plan.total_ratio, 1.0, rel_tol=1.0e-13, abs_tol=0.0
                ):
                    self.edge_grading[current] = plan.total_ratio

            spacing_replacements: dict[
                tuple[EdgeKey, str], EdgeKey | None
            ] = {
                (current, vertex): None
                for current in cut_edges
                for vertex in current
            }
            for plan in combined_edge_plans.values():
                for source in plan.source_edges:
                    for vertex in source:
                        spacing_replacements[(source, vertex)] = (
                            plan.edge if vertex in plan.edge else None
                        )
            self._remap_spacing_link_endpoints(spacing_replacements)

            self.edge_boundaries = {
                current: name
                for current, name in edge_boundaries_before.items()
                if current in actual_edges
                and current not in removed_topology_edges
            }
            for current, plan in combined_edge_plans.items():
                if plan.boundary is not None:
                    self.edge_boundaries[current] = plan.boundary

            used_vertices = {
                vertex_id
                for block in self.blocks
                for vertex_id in block.vertices
            }
            candidate_vertices = {
                vertex_id for current in cut_edges for vertex_id in current
            }
            removed_vertex_ids = tuple(
                vertex_id
                for vertex_id in self.vertices
                if vertex_id in candidate_vertices
                and vertex_id not in used_vertices
            )
            self.vertices = {
                vertex_id: vertex
                for vertex_id, vertex in self.vertices.items()
                if vertex_id not in removed_vertex_ids
            }
            self._prune_boundary_assignments()
            self.validate()
            return BlockCombineResult(
                selected,
                cut_edges,
                removed_vertex_ids,
                tuple(combined_edge_plans),
                tuple(pair.kept_block_id for pair in pair_plans),
                tuple(pair.removed_block_id for pair in pair_plans),
            )
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_geometry = geometry_before
            self.edge_grading = grading_before
            self.spacing_links = spacing_links_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def _combine_cut_component(
        self,
        selected: EdgeKey,
        occurrences: dict[EdgeKey, list[EdgeOccurrence]],
    ) -> tuple[EdgeKey, ...]:
        incident_edges: dict[str, list[EdgeKey]] = {}
        block_edges: dict[str, set[EdgeKey]] = {}
        for block in self.blocks:
            current_edges = {
                edge_key(*block.directed_edge(index)) for index in range(4)
            }
            block_edges[block.id] = current_edges
            for current in current_edges:
                for vertex_id in current:
                    incident_edges.setdefault(vertex_id, []).append(current)

        component = {selected}
        pending = deque([selected])
        while pending:
            current = pending.popleft()
            current_blocks = {
                occurrence[0].id for occurrence in occurrences[current]
            }
            for vertex_id in current:
                continuations: list[EdgeKey] = []
                for candidate in dict.fromkeys(incident_edges[vertex_id]):
                    if candidate == current \
                            or len(occurrences.get(candidate, ())) != 2:
                        continue
                    candidate_blocks = {
                        occurrence[0].id
                        for occurrence in occurrences[candidate]
                    }
                    if current_blocks.intersection(candidate_blocks):
                        continue
                    if self._combine_edges_continue(
                        vertex_id,
                        current,
                        candidate,
                        current_blocks,
                        candidate_blocks,
                        block_edges,
                    ):
                        continuations.append(candidate)
                new_continuations = [
                    candidate
                    for candidate in dict.fromkeys(continuations)
                    if candidate not in component
                ]
                if len(new_continuations) > 1:
                    raise TopologyError(
                        "The selected internal cut branches and cannot be combined"
                    )
                for candidate in new_continuations:
                    component.add(candidate)
                    pending.append(candidate)

        ordered = tuple(
            current for current in self.edges() if current in component
        )
        block_use: dict[str, int] = {}
        for current in ordered:
            if len(occurrences.get(current, ())) != 2:
                raise TopologyError(
                    "Every edge in a combined cut must be internal"
                )
            for occurrence in occurrences[current]:
                block_use[occurrence[0].id] = (
                    block_use.get(occurrence[0].id, 0) + 1
                )
        if any(count != 1 for count in block_use.values()):
            raise TopologyError(
                "The selected cut turns or crosses itself and cannot be combined"
            )

        for vertex_id in {
            value for current in ordered for value in current
        }:
            local_cut_edges = [
                current for current in ordered if vertex_id in current
            ]
            if len(local_cut_edges) > 2:
                raise TopologyError(
                    "The selected internal cut branches and cannot be combined"
                )
            covered_blocks = {
                occurrence[0].id
                for current in local_cut_edges
                for occurrence in occurrences[current]
            }
            incident_blocks = {
                block.id for block in self.blocks if vertex_id in block.vertices
            }
            if incident_blocks != covered_blocks:
                raise TopologyError(
                    "Combining this edge would leave a hanging topology vertex"
                )
        return ordered

    @staticmethod
    def _combine_edges_continue(
        vertex_id: str,
        current: EdgeKey,
        candidate: EdgeKey,
        current_blocks: set[str],
        candidate_blocks: set[str],
        block_edges: dict[str, set[EdgeKey]],
    ) -> bool:
        connections: set[tuple[str, str]] = set()
        for first_block in current_blocks:
            for second_block in candidate_blocks:
                shared = block_edges[first_block].intersection(
                    block_edges[second_block]
                )
                if any(
                    vertex_id in shared_edge
                    and shared_edge not in (current, candidate)
                    for shared_edge in shared
                ):
                    connections.add((first_block, second_block))
        return (
            len(connections) == 2
            and {first for first, _second in connections} == current_blocks
            and {second for _first, second in connections} == candidate_blocks
        )

    def _combine_pair_plan(
        self,
        cut: EdgeKey,
        occurrences: list[EdgeOccurrence],
        block_order: dict[str, int],
    ) -> _CombinePairPlan:
        blocks = tuple(occurrence[0] for occurrence in occurrences)
        directed_outer_edges = [
            block.directed_edge(index)
            for block in blocks
            for index in range(4)
            if edge_key(*block.directed_edge(index)) != cut
        ]
        outgoing: dict[str, str] = {}
        for first, second in directed_outer_edges:
            if first in outgoing:
                raise TopologyError(
                    "The two selected blocks do not have one simple outer boundary"
                )
            outgoing[first] = second
        start = directed_outer_edges[0][0]
        cycle: list[str] = []
        current = start
        for _ in range(6):
            if current in cycle or current not in outgoing:
                raise TopologyError(
                    "The two selected blocks do not form one mergeable region"
                )
            cycle.append(current)
            current = outgoing[current]
        if current != start or len(set(cycle)) != 6:
            raise TopologyError(
                "The two selected blocks do not form one mergeable region"
            )

        corners = [vertex_id for vertex_id in cycle if vertex_id not in cut]
        if len(corners) != 4 or len(set(corners)) != 4:
            raise TopologyError(
                "Combining the selected blocks would not produce a quadrilateral"
            )
        corner_start = min(range(4), key=lambda index: corners[index])
        corners = corners[corner_start:] + corners[:corner_start]
        kept, removed = sorted(
            blocks, key=lambda block: block_order[block.id]
        )
        merged = Block(kept.id, tuple(corners))  # type: ignore[arg-type]
        self._validate_convex_ccw(merged)

        joins = []
        for vertex_id in cut:
            index = cycle.index(vertex_id)
            previous_id = cycle[index - 1]
            next_id = cycle[(index + 1) % len(cycle)]
            joins.append((
                (previous_id, vertex_id),
                (vertex_id, next_id),
                (previous_id, next_id),
            ))
        return _CombinePairPlan(
            kept.id,
            removed.id,
            merged,
            tuple(joins),  # type: ignore[arg-type]
        )

    def _combined_edge_plan(
        self,
        first: tuple[str, str],
        second: tuple[str, str],
        target_direction: tuple[str, str],
    ) -> _CombinedEdgePlan:
        if first[1] != second[0] \
                or target_direction != (first[0], second[1]):
            raise TopologyError("Combined edge segments are not consecutive")
        first_edge = edge_key(*first)
        second_edge = edge_key(*second)
        target = edge_key(*target_direction)
        first_boundary = self.edge_boundaries.get(first_edge)
        second_boundary = self.edge_boundaries.get(second_edge)
        if first_boundary != second_boundary:
            raise TopologyError(
                "Adjacent boundary edge segments must use the same boundary "
                "before their blocks can be combined"
            )

        geometry = self._combined_edge_geometry(first, second)
        first_start, _first_end = self._directed_edge_widths(first)
        _second_start, second_end = self._directed_edge_widths(second)
        directed_ratio = _finite_expansion_ratio(
            math.log(second_end) - math.log(first_start)
        )
        canonical_ratio = (
            directed_ratio
            if target == target_direction
            else 1.0 / directed_ratio
        )
        if geometry is not None and target != target_direction:
            geometry = self._reversed_edge_geometry(geometry)
        return _CombinedEdgePlan(
            target,
            (first_edge, second_edge),
            self.edge_cells[first_edge] + self.edge_cells[second_edge],
            geometry,
            canonical_ratio,
            first_boundary,
        )

    def _directed_edge_widths(
        self, directed: tuple[str, str]
    ) -> tuple[float, float]:
        current = edge_key(*directed)
        values = self.edge_grading_values(current)
        if current == directed:
            return values.start_width, values.end_width
        return values.end_width, values.start_width

    def _directed_edge_geometry(
        self, directed: tuple[str, str]
    ) -> EdgeGeometry | None:
        current = edge_key(*directed)
        geometry = self.edge_geometry.get(current)
        if geometry is None or current == directed:
            return geometry
        return self._reversed_edge_geometry(geometry)

    def _combined_edge_geometry(
        self,
        first: tuple[str, str],
        second: tuple[str, str],
    ) -> EdgeGeometry | None:
        first_geometry = self._directed_edge_geometry(first)
        second_geometry = self._directed_edge_geometry(second)
        joint = self.vertices[first[1]]
        joint_point = (joint.x, joint.y)
        first_kind = first_geometry.kind if first_geometry is not None else "line"
        second_kind = (
            second_geometry.kind if second_geometry is not None else "line"
        )

        if first_kind == second_kind == "line":
            start = self.vertices[first[0]]
            end = self.vertices[second[1]]
            first_dx = joint.x - start.x
            first_dy = joint.y - start.y
            second_dx = end.x - joint.x
            second_dy = end.y - joint.y
            cross = first_dx * second_dy - first_dy * second_dx
            scale = math.hypot(first_dx, first_dy) * math.hypot(
                second_dx, second_dy
            )
            if scale > 0.0 and math.isclose(
                cross,
                0.0,
                rel_tol=0.0,
                abs_tol=self.COORDINATE_TOLERANCE * scale,
            ) and first_dx * second_dx + first_dy * second_dy > 0.0:
                return None
            return EdgeGeometry("polyLine", (joint_point,))

        if first_kind == second_kind == "arc" \
                and self._directed_arcs_are_compatible(first, second):
            return EdgeGeometry("arc", (joint_point,))

        if first_kind in ("line", "polyLine") \
                and second_kind in ("line", "polyLine"):
            first_points = (
                first_geometry.points if first_geometry is not None else ()
            )
            second_points = (
                second_geometry.points if second_geometry is not None else ()
            )
            return EdgeGeometry(
                "polyLine", (*first_points, joint_point, *second_points)
            )

        if first_kind == second_kind == "spline":
            assert first_geometry is not None and second_geometry is not None
            return EdgeGeometry(
                "spline",
                (*first_geometry.points, joint_point, *second_geometry.points),
            )

        first_samples = self._directed_edge_join_samples(first)
        second_samples = self._directed_edge_join_samples(second)
        return EdgeGeometry(
            "spline",
            (*first_samples[1:-1], joint_point, *second_samples[1:-1]),
        )

    def _directed_edge_join_samples(
        self, directed: tuple[str, str]
    ) -> tuple[tuple[float, float], ...]:
        current = edge_key(*directed)
        kind = self.edge_type(current)
        if kind == "line":
            samples = tuple(
                self.edge_point(current, index / 2.0) for index in range(3)
            )
        else:
            samples = self.edge_render_points(
                current,
                arc_segments=16,
                spline_samples_per_span=2,
            )
        return samples if current == directed else tuple(reversed(samples))

    def _directed_arcs_are_compatible(
        self,
        first: tuple[str, str],
        second: tuple[str, str],
    ) -> bool:
        def directed_circle(
            directed: tuple[str, str]
        ) -> tuple[float, float, float, float]:
            current = edge_key(*directed)
            geometry = self.edge_geometry[current]
            center_x, center_y, radius, start, sweep = self._arc_circle(
                current, geometry
            )
            if current != directed:
                start += sweep
                sweep = -sweep
            return center_x, center_y, radius, start, sweep

        first_x, first_y, first_radius, _first_start, first_sweep = (
            directed_circle(first)
        )
        second_x, second_y, second_radius, _second_start, second_sweep = (
            directed_circle(second)
        )
        scale = max(first_radius, second_radius, 1.0)
        return (
            math.hypot(first_x - second_x, first_y - second_y)
            <= self.COORDINATE_TOLERANCE * scale
            and math.isclose(
                first_radius,
                second_radius,
                rel_tol=1.0e-9,
                abs_tol=self.COORDINATE_TOLERANCE,
            )
            and first_sweep * second_sweep > 0.0
            and abs(first_sweep + second_sweep) < 2.0 * math.pi - 1.0e-10
        )

    def add_block(self, selected: EdgeKey) -> Block:
        """Append a block along a boundary edge's outward normal."""
        selected = edge_key(*selected)
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            raise TopologyError(f"Unknown edge {selected!r}")
        if len(occurrences) != 1:
            raise TopologyError("A block can only be added to a boundary edge")

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        grading_before = dict(self.edge_grading)
        spacing_links_before = set(self.spacing_links)
        edge_boundaries_before = dict(self.edge_boundaries)

        try:
            source, index, directed = occurrences[0]
            first_id, second_id = directed
            previous_id = source.vertices[(index - 1) % 4]
            next_id = source.vertices[(index + 2) % 4]
            first = self.vertices[first_id]
            second = self.vertices[second_id]
            previous = self.vertices[previous_id]
            following = self.vertices[next_id]

            edge_x = second.x - first.x
            edge_y = second.y - first.y
            edge_length = math.hypot(edge_x, edge_y)
            if edge_length <= self.COORDINATE_TOLERANCE:
                raise TopologyError("Cannot extrude a zero-length edge")

            # Counter-clockwise blocks have their interior to the left of each
            # directed edge. The right normal therefore points outward. Use the
            # average perpendicular thickness of the source block so both new
            # vertices receive exactly the same normal displacement.
            inward_x = -edge_y / edge_length
            inward_y = edge_x / edge_length
            outward_x = -inward_x
            outward_y = -inward_y
            first_thickness = (
                (previous.x - first.x) * inward_x
                + (previous.y - first.y) * inward_y
            )
            second_thickness = (
                (following.x - second.x) * inward_x
                + (following.y - second.y) * inward_y
            )
            extrusion_distance = (first_thickness + second_thickness) / 2.0
            if extrusion_distance <= self.COORDINATE_TOLERANCE:
                raise TopologyError("Source block has no positive normal thickness")

            new_first = self._vertex_at_or_new(
                first.x + outward_x * extrusion_distance,
                first.y + outward_y * extrusion_distance,
            )
            new_second = self._vertex_at_or_new(
                second.x + outward_x * extrusion_distance,
                second.y + outward_y * extrusion_distance,
            )
            new_block = Block(
                self._next_id("b", (block.id for block in self.blocks)),
                (second_id, first_id, new_first.id, new_second.id),
            )
            if any(set(block.vertices) == set(new_block.vertices)
                   for block in self.blocks):
                raise TopologyError("That block already exists")

            shared_cells = cells_before[selected]
            side_edge = edge_key(previous_id, first_id)
            side_cells = cells_before[side_edge]
            self.blocks.append(new_block)
            for current in self.edges():
                self.edge_cells.setdefault(current, self.DEFAULT_EDGE_CELLS)

            new_edges = [
                edge_key(*new_block.directed_edge(i)) for i in range(4)
            ]
            source_boundary = edge_boundaries_before.get(selected)
            self.edge_cells[new_edges[0]] = shared_cells
            self.edge_cells[new_edges[2]] = shared_cells
            self.edge_cells[new_edges[1]] = side_cells
            self.edge_cells[new_edges[3]] = side_cells
            if new_edges[2] not in cells_before:
                source_ratio = self.edge_expansion_in_direction(
                    first_id, second_id
                )
                self._set_edge_expansion_in_direction(
                    new_first.id, new_second.id, source_ratio
                )
            self.set_edge_cells(selected, shared_cells)
            self.set_edge_cells(new_edges[1], side_cells)
            self._prune_boundary_assignments()
            if source_boundary is not None \
                    and self.is_boundary_edge(new_edges[2]) \
                    and new_edges[2] not in self.edge_boundaries:
                self.edge_boundaries[new_edges[2]] = source_boundary
            self.validate()
            return new_block
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_grading = grading_before
            self.spacing_links = spacing_links_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def add_block_from_vertices(self, vertex_ids: Iterable[str]) -> Block:
        """Create a block from four existing vertices supplied in any order."""
        identifiers = list(vertex_ids)
        if len(identifiers) != 4 or len(set(identifiers)) != 4:
            raise TopologyError("Select four distinct vertices for the new block")
        unknown = [
            identifier for identifier in identifiers
            if identifier not in self.vertices
        ]
        if unknown:
            raise TopologyError(f"Unknown vertices: {', '.join(unknown)}")

        ordered = self._counter_clockwise_vertices(identifiers)
        new_block = Block(
            self._next_id("b", (block.id for block in self.blocks)),
            ordered,
        )
        if any(set(block.vertices) == set(new_block.vertices)
               for block in self.blocks):
            raise TopologyError("That block already exists")
        self._validate_convex_ccw(new_block)

        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        grading_before = dict(self.edge_grading)
        edge_boundaries_before = dict(self.edge_boundaries)
        try:
            self.blocks.append(new_block)
            new_edges = [
                edge_key(*new_block.directed_edge(index))
                for index in range(4)
            ]
            for current in new_edges:
                self.edge_cells.setdefault(current, self.DEFAULT_EDGE_CELLS)

            # Existing edge counts take precedence over defaults. Adding the
            # block merges each opposite-edge constraint component, so use the
            # first existing count in each pair and propagate it atomically.
            for first, second in ((new_edges[0], new_edges[2]),
                                  (new_edges[1], new_edges[3])):
                if first in cells_before:
                    cells = cells_before[first]
                elif second in cells_before:
                    cells = cells_before[second]
                else:
                    cells = self.DEFAULT_EDGE_CELLS
                self.set_edge_cells(first, cells)

            self._prune_boundary_assignments()
            self.validate()
            return new_block
        except Exception:
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_grading = grading_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def remove_edge(self, selected: EdgeKey) -> list[Block]:
        """Remove an edge and every block incident to it.

        Edges are derived topology. Corners belonging to removed blocks are
        pruned when no surviving block uses them, while unrelated standalone
        vertices are preserved. At least one block must remain.
        """
        selected = edge_key(*selected)
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            raise TopologyError(f"Unknown edge {selected!r}")

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        geometry_before = dict(self.edge_geometry)
        grading_before = dict(self.edge_grading)
        spacing_links_before = set(self.spacing_links)
        edge_boundaries_before = dict(self.edge_boundaries)
        removed_ids = {occurrence[0].id for occurrence in occurrences}
        if len(removed_ids) >= len(self.blocks):
            raise TopologyError("At least one block must remain in the topology")
        removed = [
            block for block in self.blocks if block.id in removed_ids
        ]

        try:
            self.blocks = [
                block for block in self.blocks if block.id not in removed_ids
            ]
            used_vertices = {
                vertex_id
                for block in self.blocks
                for vertex_id in block.vertices
            }
            removed_vertices = {
                vertex_id
                for block in removed
                for vertex_id in block.vertices
            }
            # Preserve standalone vertices unrelated to the deleted blocks.
            # Only newly orphaned corners from those blocks are pruned.
            pruned_vertices = removed_vertices - used_vertices
            self.vertices = {
                vertex_id: vertex
                for vertex_id, vertex in self.vertices.items()
                if vertex_id not in pruned_vertices
            }
            surviving_edges = set(self.edges())
            self.edge_cells = {
                current: cells
                for current, cells in self.edge_cells.items()
                if current in surviving_edges
            }
            self.edge_geometry = {
                current: geometry
                for current, geometry in self.edge_geometry.items()
                if current in surviving_edges
            }
            self.edge_grading = {
                current: ratio
                for current, ratio in self.edge_grading.items()
                if current in surviving_edges
            }
            self._prune_spacing_links()
            self._prune_boundary_assignments()
            self.validate()
            return removed
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_geometry = geometry_before
            self.edge_grading = grading_before
            self.spacing_links = spacing_links_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def _prune_boundary_assignments(self) -> None:
        """Discard assignments whose edges disappeared or became internal."""
        occurrences = self.edge_occurrences()
        self.edge_boundaries = {
            current: name
            for current, name in self.edge_boundaries.items()
            if len(occurrences.get(current, ())) == 1
        }

    def can_remove_edge(self, selected: EdgeKey) -> bool:
        """Return whether removing an edge would leave at least one block."""
        try:
            selected = edge_key(*selected)
        except (TypeError, TopologyError):
            return False
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            return False
        removed_ids = {occurrence[0].id for occurrence in occurrences}
        return len(removed_ids) < len(self.blocks)
