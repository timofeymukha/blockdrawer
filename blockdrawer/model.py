"""UI-independent topology model for BlockDrawer."""

from __future__ import annotations

from collections import deque
import colorsys
import math
import re
from typing import Iterable

from .domain import (
    Block,
    BlockCombineResult,  # noqa: F401 - backward-compatible re-export
    Boundary,
    EdgeGeometry,
    EdgeGradingValues,
    EdgeKey,
    EdgeOccurrence,
    EdgeSplitResult,  # noqa: F401 - backward-compatible re-export
    GeometryCurve,
    ProjectionResult,  # noqa: F401 - backward-compatible re-export
    SpacingLink,
    TopologyError,
    Vertex,
    edge_key,
)
from .grading import (
    _cell_ratio_log_from_start_width,
    _finite_expansion_ratio,
    _grading_from_total_ratio,
)
from .reference_geometry import ReferenceGeometryMixin
from .spacing import SpacingOperationsMixin
from .topology import TopologyOperationsMixin


class MeshModel(
    SpacingOperationsMixin,
    TopologyOperationsMixin,
    ReferenceGeometryMixin,
):
    """A conformal set of quadrilateral blocks with optional curved edges.

    ``edge_cells`` stores the number of intervals along an edge. Canvas markers
    therefore show ``edge_cells - 1`` interior mesh nodes; OpenFOAM receives the
    interval count directly as its block cell count.
    """

    DEFAULT_EDGE_CELLS = 10
    COORDINATE_TOLERANCE = 1.0e-9
    SUPPORTED_EDGE_TYPES = ("line", "arc", "polyLine", "spline")
    MULTI_POINT_EDGE_TYPES = ("polyLine", "spline")
    DEFAULT_CONTROL_POINT_OFFSET_RATIO = 0.2
    GRADING_PARAMETERS = (
        "cell_ratio",
        "total_ratio",
        "start_width",
        "end_width",
    )
    SPLINE_LENGTH_SAMPLES = 512
    SUPPORTED_BOUNDARY_TYPES = ("patch", "symmetry", "wall", "cyclic", "empty")
    BOUNDARY_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    BOUNDARY_COLORS = (
        "#d9485f",
        "#2f9e44",
        "#7b2cbf",
        "#e67700",
        "#0b7285",
        "#c2255c",
        "#5f3dc4",
        "#2b8a3e",
        "#a61e4d",
        "#1864ab",
        "#9c36b5",
        "#087f5b",
    )

    def __init__(self, *, initialize: bool = True) -> None:
        self.vertices: dict[str, Vertex] = {}
        self.blocks: list[Block] = []
        self.edge_cells: dict[EdgeKey, int] = {}
        self.edge_geometry: dict[EdgeKey, EdgeGeometry] = {}
        # Non-uniform total end/start ratios in canonical EdgeKey direction.
        # Uniform grading is implicit, like straight edge geometry.
        self.edge_grading: dict[EdgeKey, float] = {}
        self.spacing_links: set[SpacingLink] = set()
        self.boundaries: dict[str, Boundary] = {}
        self.edge_boundaries: dict[EdgeKey, str] = {}
        self.geometry_curves: dict[str, GeometryCurve] = {}
        self.z_cells = 1
        self.z_min = 0.0
        self.z_max = 1.0
        self.scale = 1.0
        self.z_min_patch_name = "zMin"
        self.z_min_patch_type = "patch"
        self.z_max_patch_name = "zMax"
        self.z_max_patch_type = "patch"

        if initialize:
            self._create_initial_block()

    def _create_initial_block(self) -> None:
        self.vertices = {
            "v0": Vertex("v0", 0.0, 0.0),
            "v1": Vertex("v1", 1.0, 0.0),
            "v2": Vertex("v2", 1.0, 1.0),
            "v3": Vertex("v3", 0.0, 1.0),
        }
        self.blocks = [Block("b0", ("v0", "v1", "v2", "v3"))]
        self.edge_cells = {
            edge: self.DEFAULT_EDGE_CELLS for edge in self.edges()
        }
        self.edge_geometry = {}
        self.edge_grading = {}
        self.spacing_links = set()
        self.boundaries = {}
        self.edge_boundaries = {}
        self.geometry_curves = {}

    def edges(self) -> list[EdgeKey]:
        """Return unique edges in stable block/local-edge order."""
        result: list[EdgeKey] = []
        seen: set[EdgeKey] = set()
        for block in self.blocks:
            for index in range(4):
                current = edge_key(*block.directed_edge(index))
                if current not in seen:
                    seen.add(current)
                    result.append(current)
        return result

    def edge_occurrences(self) -> dict[EdgeKey, list[EdgeOccurrence]]:
        result: dict[EdgeKey, list[EdgeOccurrence]] = {}
        for block in self.blocks:
            for index in range(4):
                directed = block.directed_edge(index)
                result.setdefault(edge_key(*directed), []).append(
                    (block, index, directed)
                )
        return result

    def is_boundary_edge(self, edge: EdgeKey) -> bool:
        occurrences = self.edge_occurrences().get(edge, [])
        return len(occurrences) == 1

    def add_boundary(self, name: str) -> Boundary:
        """Add an unassigned named patch with a unique display color."""
        self._validate_boundary_name(name)
        if name in self.boundaries:
            raise TopologyError(f"Boundary {name!r} already exists")
        if name in (self.z_min_patch_name, self.z_max_patch_name):
            raise TopologyError(
                f"Boundary {name!r} is already used by an extrusion patch"
            )
        boundary = Boundary(name, "patch", self._next_boundary_color())
        self.boundaries[name] = boundary
        self.validate()
        return boundary

    def remove_boundary(self, name: str) -> None:
        """Remove a patch, its assignments, and any cyclic pairing."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        self._detach_cyclic_boundary(name)
        del self.boundaries[name]
        self.edge_boundaries = {
            current: boundary_name
            for current, boundary_name in self.edge_boundaries.items()
            if boundary_name != name
        }
        self.validate()

    def set_boundary_type(
        self,
        name: str,
        kind: str,
        *,
        neighbour_patch: str | None = None,
    ) -> set[str]:
        """Set a patch type, pairing cyclic patches atomically and reciprocally."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        if kind not in self.SUPPORTED_BOUNDARY_TYPES:
            raise TopologyError(f"Unsupported boundary type {kind!r}")
        if kind == "cyclic":
            if neighbour_patch is None:
                raise TopologyError("A cyclic boundary needs a neighbouring patch")
            if neighbour_patch == name:
                raise TopologyError("A cyclic boundary cannot neighbour itself")
            if neighbour_patch not in self.boundaries:
                raise TopologyError(
                    f"Unknown neighbouring patch {neighbour_patch!r}"
                )

        previous = dict(self.boundaries)
        try:
            affected = {name}
            affected.update(self._detach_cyclic_boundary(name))
            if kind == "cyclic":
                assert neighbour_patch is not None
                affected.add(neighbour_patch)
                affected.update(self._detach_cyclic_boundary(neighbour_patch))
                first = self.boundaries[name]
                second = self.boundaries[neighbour_patch]
                self.boundaries[name] = Boundary(
                    name, "cyclic", first.color, neighbour_patch
                )
                self.boundaries[neighbour_patch] = Boundary(
                    neighbour_patch, "cyclic", second.color, name
                )
            else:
                current = self.boundaries[name]
                self.boundaries[name] = Boundary(name, kind, current.color)
            self.validate()
            return affected
        except Exception:
            self.boundaries = previous
            raise

    def set_edge_boundary(self, edge: EdgeKey, name: str | None) -> None:
        """Assign an exterior topological edge to at most one named patch."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if not self.is_boundary_edge(current):
            raise TopologyError("Only exterior edges can be assigned to a boundary")
        if name is None:
            self.edge_boundaries.pop(current, None)
        else:
            if name not in self.boundaries:
                raise TopologyError(f"Unknown boundary {name!r}")
            self.edge_boundaries[current] = name
        self.validate()

    def boundary_edges(self, name: str) -> list[EdgeKey]:
        """Return a patch's assigned edges in stable topology order."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        return [
            current for current in self.edges()
            if self.edge_boundaries.get(current) == name
        ]

    def _detach_cyclic_boundary(self, name: str) -> set[str]:
        """Turn an existing cyclic pair back into ordinary patches."""
        current = self.boundaries[name]
        affected: set[str] = set()
        neighbour = current.neighbour_patch
        if current.kind == "cyclic" and neighbour in self.boundaries:
            other = self.boundaries[neighbour]
            if other.kind == "cyclic" and other.neighbour_patch == name:
                self.boundaries[neighbour] = Boundary(
                    neighbour, "patch", other.color
                )
                affected.add(neighbour)
        self.boundaries[name] = Boundary(name, "patch", current.color)
        return affected

    def _next_boundary_color(self) -> str:
        used = {boundary.color.lower() for boundary in self.boundaries.values()}
        for color in self.BOUNDARY_COLORS:
            if color.lower() not in used:
                return color
        index = len(self.boundaries)
        while True:
            hue = (index * 0.6180339887498949) % 1.0
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.78)
            color = (
                f"#{round(red * 255):02x}{round(green * 255):02x}"
                f"{round(blue * 255):02x}"
            )
            if color not in used:
                return color
            index += 1

    def edge_constraint_component(self, selected: EdgeKey) -> set[EdgeKey]:
        """Find every edge whose count is constrained to equal ``selected``."""
        return set(self._edge_constraint_orientations(selected))

    def _edge_constraint_orientations(
        self, selected: EdgeKey
    ) -> dict[EdgeKey, bool]:
        """Return linked edges and whether canonical grading must be reversed."""
        selected = edge_key(*selected)
        all_edges = self.edges()
        if selected not in set(all_edges):
            raise TopologyError(f"Unknown edge {selected!r}")

        adjacency: dict[EdgeKey, list[tuple[EdgeKey, bool]]] = {
            current: [] for current in all_edges
        }
        for block in self.blocks:
            first, second, third, fourth = block.vertices
            pairs = (
                ((first, second), (fourth, third)),
                ((second, third), (first, fourth)),
            )
            for first_direction, second_direction in pairs:
                first_edge = edge_key(*first_direction)
                second_edge = edge_key(*second_direction)
                first_reversed = first_edge != first_direction
                second_reversed = second_edge != second_direction
                reverse = first_reversed != second_reversed
                adjacency[first_edge].append((second_edge, reverse))
                adjacency[second_edge].append((first_edge, reverse))

        orientations = {selected: False}
        pending = deque([selected])
        while pending:
            current = pending.popleft()
            for neighbor, reverse in adjacency[current]:
                expected = orientations[current] != reverse
                if neighbor in orientations:
                    if orientations[neighbor] != expected:
                        raise TopologyError(
                            "Edge-count constraints have inconsistent directions"
                        )
                    continue
                orientations[neighbor] = expected
                pending.append(neighbor)
        return orientations

    def set_edge_cells(self, edge: EdgeKey, cells: int) -> set[EdgeKey]:
        """Set an edge count and propagate it through all block constraints."""
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            raise TopologyError("The edge cell count must be a positive integer")
        affected = self.edge_constraint_component(edge)
        previous_cells = dict(self.edge_cells)
        previous_grading = dict(self.edge_grading)
        try:
            for current in affected:
                self.edge_cells[current] = cells
                if cells == 1:
                    self.edge_grading.pop(current, None)
            self._propagate_spacing_links(affected)
        except Exception:
            self.edge_cells = previous_cells
            self.edge_grading = previous_grading
            raise
        return affected

    def edge_total_expansion(self, edge: EdgeKey) -> float:
        """Return the end/start cell-width ratio in canonical edge direction."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        return self.edge_grading.get(current, 1.0)

    def edge_expansion_in_direction(self, first: str, second: str) -> float:
        """Return total expansion when traversing ``first`` to ``second``."""
        current = edge_key(first, second)
        ratio = self.edge_total_expansion(current)
        return ratio if current == (first, second) else 1.0 / ratio

    def _set_edge_expansion_in_direction(
        self, first: str, second: str, ratio: float
    ) -> None:
        """Store ``ratio`` for a directed edge without validating the model."""
        current = edge_key(first, second)
        canonical_ratio = ratio if current == (first, second) else 1.0 / ratio
        if canonical_ratio == 1.0:
            self.edge_grading.pop(current, None)
        else:
            self.edge_grading[current] = canonical_ratio

    def edge_length(self, edge: EdgeKey) -> float:
        """Return the geometric edge length in unscaled drawing units."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return math.hypot(second.x - first.x, second.y - first.y)
        if geometry.kind == "arc":
            _, _, radius, _, sweep = self._arc_circle(current, geometry)
            return radius * abs(sweep)
        if geometry.kind == "polyLine":
            path = [
                (first.x, first.y),
                *geometry.points,
                (second.x, second.y),
            ]
            return sum(
                math.hypot(end[0] - start[0], end[1] - start[1])
                for start, end in zip(path, path[1:])
            )

        previous = self.edge_point(current, 0.0)
        length = 0.0
        for index in range(1, self.SPLINE_LENGTH_SAMPLES + 1):
            point = self.edge_point(
                current, index / self.SPLINE_LENGTH_SAMPLES
            )
            length += math.hypot(
                point[0] - previous[0], point[1] - previous[1]
            )
            previous = point
        return length

    def edge_grading_values(self, edge: EdgeKey) -> EdgeGradingValues:
        """Return all equivalent grading inputs in canonical edge direction."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        length = self.edge_length(current)
        total_ratio = self.edge_total_expansion(current)
        cell_ratio, start_width, end_width = _grading_from_total_ratio(
            length, cells, total_ratio
        )
        return EdgeGradingValues(
            length,
            cell_ratio,
            total_ratio,
            start_width,
            end_width,
        )

    def set_edge_grading(
        self,
        edge: EdgeKey,
        parameter: str,
        value: float,
        *,
        propagate: bool = False,
    ) -> EdgeGradingValues:
        """Set grading from any representation and optionally sweep linked edges."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        if parameter not in self.GRADING_PARAMETERS:
            raise TopologyError(f"Unknown grading parameter {parameter!r}")
        if not math.isfinite(value) or value <= 0.0:
            raise TopologyError("Grading values must be positive and finite")

        length = self.edge_length(current)
        if cells == 1:
            expected = length if parameter in ("start_width", "end_width") else 1.0
            if not math.isclose(value, expected, rel_tol=1.0e-10, abs_tol=1.0e-12):
                raise TopologyError(
                    "A one-cell edge can only use uniform grading"
                )
            affected = (
                self._edge_constraint_orientations(current)
                if propagate else {current: False}
            )
            previous = dict(self.edge_grading)
            try:
                for affected_edge in affected:
                    self.edge_grading.pop(affected_edge, None)
                self._propagate_spacing_links(affected)
                self.validate()
            except Exception:
                self.edge_grading = previous
                raise
            return self.edge_grading_values(current)

        if parameter == "total_ratio":
            total_ratio = float(value)
        elif parameter == "cell_ratio":
            logarithm = (cells - 1) * math.log(value)
            total_ratio = _finite_expansion_ratio(logarithm)
        elif parameter == "start_width":
            log_cell_ratio = _cell_ratio_log_from_start_width(
                length, cells, value
            )
            total_ratio = _finite_expansion_ratio(
                (cells - 1) * log_cell_ratio
            )
        else:
            reverse_log_cell_ratio = _cell_ratio_log_from_start_width(
                length, cells, value
            )
            total_ratio = _finite_expansion_ratio(
                -(cells - 1) * reverse_log_cell_ratio
            )

        _grading_from_total_ratio(length, cells, total_ratio)
        orientations = (
            self._edge_constraint_orientations(current)
            if propagate else {current: False}
        )
        previous = dict(self.edge_grading)
        try:
            for affected_edge, reverse in orientations.items():
                ratio = 1.0 / total_ratio if reverse else total_ratio
                if ratio == 1.0:
                    self.edge_grading.pop(affected_edge, None)
                else:
                    self.edge_grading[affected_edge] = ratio
            self._propagate_spacing_links(orientations)
            self.validate()
        except Exception:
            self.edge_grading = previous
            raise
        return self.edge_grading_values(current)

    def edge_node_fraction(self, edge: EdgeKey, node_index: int) -> float:
        """Return one interior node's graded fraction in canonical direction."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        if isinstance(node_index, bool) or not isinstance(node_index, int) \
                or not 0 <= node_index <= cells:
            raise TopologyError("Edge node index is out of range")
        if node_index == 0:
            return 0.0
        if node_index == cells:
            return 1.0
        total_ratio = self.edge_total_expansion(current)
        if total_ratio == 1.0:
            return node_index / cells
        log_cell_ratio = math.log(total_ratio) / (cells - 1)
        if abs(log_cell_ratio) <= 1.0e-14:
            return node_index / cells
        if log_cell_ratio < 0.0:
            return math.expm1(node_index * log_cell_ratio) / math.expm1(
                cells * log_cell_ratio
            )
        return (
            math.exp((node_index - cells) * log_cell_ratio)
            * (-math.expm1(-node_index * log_cell_ratio))
            / (-math.expm1(-cells * log_cell_ratio))
        )

    def edge_type(self, edge: EdgeKey) -> str:
        """Return the OpenFOAM geometry type for ``edge``."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        geometry = self.edge_geometry.get(current)
        return geometry.kind if geometry is not None else "line"

    def set_edge_type(self, edge: EdgeKey, kind: str) -> None:
        """Change an edge between the supported OpenFOAM geometry types.

        A new curved edge receives a deterministic interpolation point offset
        outward from the first incident block.
        """
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if kind not in self.SUPPORTED_EDGE_TYPES:
            raise TopologyError(f"Unsupported edge type {kind!r}")
        if self.edge_type(current) == kind:
            return
        if kind == "line":
            self.edge_geometry.pop(current, None)
            return

        previous = self.edge_geometry.get(current)
        geometry = EdgeGeometry(kind, (self._default_edge_point(current),))
        self.edge_geometry[current] = geometry
        try:
            self._validate_edge_geometry(current, geometry)
        except (TopologyError, ValueError):
            if previous is None:
                self.edge_geometry.pop(current, None)
            else:
                self.edge_geometry[current] = previous
            raise

    def edge_control_points(
        self, edge: EdgeKey
    ) -> tuple[tuple[float, float], ...]:
        """Return the ordered interpolation points for a non-line edge."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        geometry = self.edge_geometry.get(current)
        return geometry.points if geometry is not None else ()

    def arc_point(self, edge: EdgeKey) -> tuple[float, float]:
        """Return the single interpolation point for an arc edge."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind != "arc":
            raise TopologyError(f"Edge {current!r} is not an arc")
        return geometry.points[0]

    def set_arc_point(self, edge: EdgeKey, x: float, y: float) -> None:
        """Move an arc interpolation point, rolling back invalid geometry."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind != "arc":
            raise TopologyError(f"Edge {current!r} is not an arc")
        self.set_edge_control_point(current, 0, x, y)

    def set_edge_control_point(
        self, edge: EdgeKey, index: int, x: float, y: float
    ) -> None:
        """Move one interpolation point, rolling back invalid geometry."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            raise TopologyError(f"Edge {current!r} has no interpolation points")
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Interpolation point coordinates must be finite")
        previous = geometry
        points = list(geometry.points)
        points[index] = (float(x), float(y))
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self.edge_geometry[current] = replacement
        try:
            self._validate_edge_geometry(current, replacement)
        except (TopologyError, ValueError):
            self.edge_geometry[current] = previous
            raise

    def add_edge_control_point(
        self, edge: EdgeKey, after_index: int | None = None
    ) -> int:
        """Insert a point after ``after_index`` and return its new index."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        if after_index is None:
            after_index = len(geometry.points) - 1
        if isinstance(after_index, bool) or not isinstance(after_index, int) \
                or not 0 <= after_index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")

        points = list(geometry.points)
        left = points[after_index]
        right = (
            points[after_index + 1]
            if after_index + 1 < len(points)
            else (
                self.vertices[current[1]].x,
                self.vertices[current[1]].y,
            )
        )
        inserted = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
        new_index = after_index + 1
        points.insert(new_index, inserted)
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement
        return new_index

    def remove_edge_control_point(self, edge: EdgeKey, index: int) -> None:
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        if len(geometry.points) <= 1:
            raise TopologyError(
                f"A {geometry.kind} needs at least one interpolation point"
            )
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")
        points = list(geometry.points)
        del points[index]
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement

    def reset_edge_control_points(self, edge: EdgeKey) -> None:
        """Distribute all interpolation points evenly along the edge chord."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        self._set_equidistant_edge_control_points(
            current, geometry, len(geometry.points)
        )

    def set_edge_control_point_count(self, edge: EdgeKey, count: int) -> None:
        """Set a point-list edge's size and redistribute it along the chord."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise TopologyError(
                "The interpolation point count must be a positive integer"
            )
        self._set_equidistant_edge_control_points(current, geometry, count)

    def _set_equidistant_edge_control_points(
        self,
        current: EdgeKey,
        geometry: EdgeGeometry,
        count: int,
    ) -> None:
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        denominator = count + 1
        points = tuple(
            (
                first.x + (index / denominator) * (second.x - first.x),
                first.y + (index / denominator) * (second.y - first.y),
            )
            for index in range(1, denominator)
        )
        replacement = EdgeGeometry(geometry.kind, points)
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement

    def edge_point(self, edge: EdgeKey, fraction: float) -> tuple[float, float]:
        """Return a point at ``fraction`` along any supported edge type."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise TopologyError("Edge fraction must be between 0 and 1")
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        if fraction == 0.0:
            return first.x, first.y
        if fraction == 1.0:
            return second.x, second.y
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return (
                first.x + fraction * (second.x - first.x),
                first.y + fraction * (second.y - first.y),
            )

        if geometry.kind == "arc":
            center_x, center_y, radius, start_angle, sweep = self._arc_circle(
                current, geometry
            )
            angle = start_angle + fraction * sweep
            return (
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
            )
        if geometry.kind == "spline":
            return self._spline_point(current, geometry, fraction)
        return self._polyline_point(current, geometry, fraction)

    def edge_render_points(
        self,
        edge: EdgeKey,
        *,
        arc_segments: int = 64,
        spline_samples_per_span: int = 4,
    ) -> tuple[tuple[float, float], ...]:
        """Sample an edge for display while retaining all defining points.

        A fixed number of samples over a complete spline can skip most of its
        spans when a fitted edge contains many interpolation points. Sampling
        every span keeps the rendered stroke on the same curve used for mesh
        nodes and guarantees that it passes through every stored point.
        """
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if isinstance(arc_segments, bool) \
                or not isinstance(arc_segments, int) \
                or arc_segments < 1:
            raise TopologyError("Arc render segments must be a positive integer")
        self._validate_samples_per_span(spline_samples_per_span)

        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        endpoints = ((first.x, first.y), (second.x, second.y))
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return endpoints
        if geometry.kind == "polyLine":
            return (endpoints[0], *geometry.points, endpoints[1])
        if geometry.kind == "spline":
            return self._spline_path_render_points(
                (endpoints[0], *geometry.points, endpoints[1]),
                spline_samples_per_span,
            )
        return tuple(
            self.edge_point(current, index / arc_segments)
            for index in range(arc_segments + 1)
        )

    def set_z_cells(self, cells: int) -> None:
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            raise TopologyError("The z cell count must be a positive integer")
        self.z_cells = cells

    def set_z_extents(self, z_min: float, z_max: float) -> None:
        if not (math.isfinite(z_min) and math.isfinite(z_max)):
            raise TopologyError("Z extents must be finite")
        if z_max <= z_min:
            raise TopologyError("zMax must be greater than zMin")
        self.z_min = float(z_min)
        self.z_max = float(z_max)

    def set_export_settings(
        self,
        z_cells: int,
        z_min: float,
        z_max: float,
        scale: float,
        z_min_patch_name: str,
        z_min_patch_type: str,
        z_max_patch_name: str,
        z_max_patch_type: str,
    ) -> None:
        """Set all blockMesh export settings atomically.

        Selecting ``cyclic`` for either extrusion face deliberately pairs both
        faces. Their reciprocal ``neighbourPatch`` entries are generated by the
        OpenFOAM writer and therefore do not need to be stored separately.
        """
        if isinstance(z_cells, bool) or not isinstance(z_cells, int) \
                or z_cells < 1:
            raise TopologyError("The z cell count must be a positive integer")
        if not (math.isfinite(z_min) and math.isfinite(z_max)) \
                or z_max <= z_min:
            raise TopologyError("zMax must be greater than zMin")
        if not math.isfinite(scale) or scale <= 0.0:
            raise TopologyError("Scale must be a positive finite number")

        self._validate_boundary_name(z_min_patch_name)
        self._validate_boundary_name(z_max_patch_name)
        if z_min_patch_name == z_max_patch_name:
            raise TopologyError("The zMin and zMax patches need distinct names")
        conflicts = {
            z_min_patch_name, z_max_patch_name
        }.intersection(self.boundaries)
        if conflicts:
            conflict = sorted(conflicts)[0]
            raise TopologyError(
                f"Patch name {conflict!r} is already used by a side boundary"
            )
        for kind in (z_min_patch_type, z_max_patch_type):
            if kind not in self.SUPPORTED_BOUNDARY_TYPES:
                raise TopologyError(f"Unsupported boundary type {kind!r}")
        if "cyclic" in (z_min_patch_type, z_max_patch_type):
            z_min_patch_type = "cyclic"
            z_max_patch_type = "cyclic"

        previous = (
            self.z_cells,
            self.z_min,
            self.z_max,
            self.scale,
            self.z_min_patch_name,
            self.z_min_patch_type,
            self.z_max_patch_name,
            self.z_max_patch_type,
        )
        self.z_cells = z_cells
        self.z_min = float(z_min)
        self.z_max = float(z_max)
        self.scale = float(scale)
        self.z_min_patch_name = z_min_patch_name
        self.z_min_patch_type = z_min_patch_type
        self.z_max_patch_name = z_max_patch_name
        self.z_max_patch_type = z_max_patch_type
        try:
            self.validate()
        except Exception:
            (
                self.z_cells,
                self.z_min,
                self.z_max,
                self.scale,
                self.z_min_patch_name,
                self.z_min_patch_type,
                self.z_max_patch_name,
                self.z_max_patch_type,
            ) = previous
            raise

    def move_vertex(self, vertex_id: str, x: float, y: float) -> None:
        if vertex_id not in self.vertices:
            raise TopologyError(f"Unknown vertex {vertex_id!r}")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Vertex coordinates must be finite")

        vertex = self.vertices[vertex_id]
        previous = (vertex.x, vertex.y)
        vertex.x, vertex.y = float(x), float(y)
        try:
            self.validate()
        except (TopologyError, ValueError):
            vertex.x, vertex.y = previous
            raise

    def add_vertex(self, x: float, y: float) -> Vertex:
        """Create a standalone vertex that can later be used by a block."""
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Vertex coordinates must be finite")
        for vertex in self.vertices.values():
            if self._coordinates_match(vertex.x, vertex.y, x, y):
                raise TopologyError(
                    f"A vertex already exists at ({x:g}, {y:g})"
                )

        vertex = Vertex(
            self._next_id("v", self.vertices),
            float(x),
            float(y),
        )
        self.vertices[vertex.id] = vertex
        try:
            self.validate()
        except (TopologyError, ValueError):
            del self.vertices[vertex.id]
            raise
        return vertex

    def block_cell_counts(self, block: Block) -> tuple[int, int, int]:
        first = edge_key(*block.directed_edge(0))
        second = edge_key(*block.directed_edge(1))
        return self.edge_cells[first], self.edge_cells[second], self.z_cells

    @classmethod
    def _validate_boundary_name(cls, name: str) -> None:
        if not isinstance(name, str) \
                or cls.BOUNDARY_NAME_PATTERN.fullmatch(name) is None:
            raise TopologyError(
                "A boundary name must start with a letter or underscore and "
                "contain only letters, numbers, and underscores"
            )

    def validate(self) -> None:
        if isinstance(self.z_cells, bool) or not isinstance(self.z_cells, int) \
                or self.z_cells < 1:
            raise TopologyError("The z cell count must be a positive integer")
        if not (math.isfinite(self.z_min) and math.isfinite(self.z_max)) \
                or self.z_max <= self.z_min:
            raise TopologyError("zMax must be greater than zMin")
        if not math.isfinite(self.scale) or self.scale <= 0.0:
            raise TopologyError("Scale must be a positive finite number")
        self._validate_boundary_name(self.z_min_patch_name)
        self._validate_boundary_name(self.z_max_patch_name)
        if self.z_min_patch_name == self.z_max_patch_name:
            raise TopologyError("The zMin and zMax patches need distinct names")
        if self.z_min_patch_type not in self.SUPPORTED_BOUNDARY_TYPES:
            raise TopologyError(
                f"Unsupported boundary type {self.z_min_patch_type!r}"
            )
        if self.z_max_patch_type not in self.SUPPORTED_BOUNDARY_TYPES:
            raise TopologyError(
                f"Unsupported boundary type {self.z_max_patch_type!r}"
            )
        if (self.z_min_patch_type == "cyclic") \
                != (self.z_max_patch_type == "cyclic"):
            raise TopologyError(
                "The zMin and zMax patches must both be cyclic when either is cyclic"
            )
        conflicts = {
            self.z_min_patch_name, self.z_max_patch_name
        }.intersection(self.boundaries)
        if conflicts:
            conflict = sorted(conflicts)[0]
            raise TopologyError(
                f"Patch name {conflict!r} is already used by a side boundary"
            )

        curve_names: set[str] = set()
        for identifier, curve in self.geometry_curves.items():
            if not isinstance(curve, GeometryCurve) or curve.id != identifier:
                raise TopologyError(
                    f"Geometry curve {identifier!r} has invalid definition data"
                )
            self._validate_geometry_curve(curve)
            if curve.name in curve_names:
                raise TopologyError(
                    f"Duplicate geometry curve name {curve.name!r}"
                )
            curve_names.add(curve.name)

        if not self.blocks:
            raise TopologyError("A topology must contain at least one block")

        for vertex in self.vertices.values():
            if not isinstance(vertex.id, str) or not vertex.id:
                raise TopologyError("Every vertex needs a non-empty string ID")
            if not (math.isfinite(vertex.x) and math.isfinite(vertex.y)):
                raise TopologyError(f"Vertex {vertex.id} has invalid coordinates")

        vertex_values = list(self.vertices.values())
        for index, first in enumerate(vertex_values):
            for second in vertex_values[index + 1:]:
                if self._coordinates_match(first.x, first.y, second.x, second.y):
                    raise TopologyError(
                        f"Vertices {first.id} and {second.id} are coincident"
                    )

        block_ids: set[str] = set()
        block_vertex_sets: set[frozenset[str]] = set()
        for block in self.blocks:
            if not isinstance(block.id, str) or not block.id or block.id in block_ids:
                raise TopologyError(f"Invalid or duplicate block ID {block.id!r}")
            block_ids.add(block.id)
            if len(block.vertices) != 4 or len(set(block.vertices)) != 4:
                raise TopologyError(f"Block {block.id} needs four distinct vertices")
            if any(vertex_id not in self.vertices for vertex_id in block.vertices):
                raise TopologyError(f"Block {block.id} references an unknown vertex")
            signature = frozenset(block.vertices)
            if signature in block_vertex_sets:
                raise TopologyError(f"Block {block.id} duplicates another block")
            block_vertex_sets.add(signature)
            self._validate_convex_ccw(block)

        actual_edges = set(self.edges())
        if set(self.edge_cells) != actual_edges:
            missing = actual_edges - set(self.edge_cells)
            extra = set(self.edge_cells) - actual_edges
            raise TopologyError(
                f"Edge cell data does not match topology (missing={missing}, extra={extra})"
            )
        for current, cells in self.edge_cells.items():
            if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
                raise TopologyError(f"Edge {current!r} has an invalid cell count")

        geometry_edges = set(self.edge_geometry)
        if not geometry_edges.issubset(actual_edges):
            extra = geometry_edges - actual_edges
            raise TopologyError(
                f"Edge geometry references unknown topology edges {extra}"
            )
        for current, geometry in self.edge_geometry.items():
            self._validate_edge_geometry(current, geometry)

        grading_edges = set(self.edge_grading)
        if not grading_edges.issubset(actual_edges):
            extra = grading_edges - actual_edges
            raise TopologyError(
                f"Edge grading references unknown topology edges {extra}"
            )
        for current, total_ratio in self.edge_grading.items():
            if total_ratio == 1.0:
                raise TopologyError(
                    f"Uniform grading on edge {current!r} must be implicit"
                )
            _grading_from_total_ratio(
                self.edge_length(current),
                self.edge_cells[current],
                total_ratio,
            )

        self._validate_spacing_links(actual_edges)

        used_colors: set[str] = set()
        for name, boundary in self.boundaries.items():
            self._validate_boundary_name(name)
            if not isinstance(boundary, Boundary) or boundary.name != name:
                raise TopologyError(f"Boundary {name!r} has invalid definition data")
            if boundary.kind not in self.SUPPORTED_BOUNDARY_TYPES:
                raise TopologyError(
                    f"Boundary {name!r} has unsupported type {boundary.kind!r}"
                )
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", boundary.color) is None:
                raise TopologyError(f"Boundary {name!r} has an invalid display color")
            color = boundary.color.lower()
            if color in used_colors:
                raise TopologyError("Boundary display colors must be unique")
            used_colors.add(color)
            if boundary.kind == "cyclic":
                neighbour = boundary.neighbour_patch
                if neighbour is None or neighbour == name \
                        or neighbour not in self.boundaries:
                    raise TopologyError(
                        f"Cyclic boundary {name!r} needs a valid neighbouring patch"
                    )
                partner = self.boundaries[neighbour]
                if partner.kind != "cyclic" or partner.neighbour_patch != name:
                    raise TopologyError(
                        f"Cyclic boundary {name!r} is not paired reciprocally"
                    )
            elif boundary.neighbour_patch is not None:
                raise TopologyError(
                    f"Boundary {name!r} has neighbourPatch but is not cyclic"
                )

        for current, name in self.edge_boundaries.items():
            if current not in actual_edges:
                raise TopologyError(
                    f"Boundary assignment references unknown edge {current!r}"
                )
            if name not in self.boundaries:
                raise TopologyError(
                    f"Boundary assignment references unknown patch {name!r}"
                )
            if not self.is_boundary_edge(current):
                raise TopologyError(
                    f"Internal edge {current!r} cannot be assigned to a boundary"
                )

        for current, occurrences in self.edge_occurrences().items():
            if len(occurrences) > 2:
                raise TopologyError(f"Edge {current!r} is non-manifold")
            if len(occurrences) == 2:
                first_direction = occurrences[0][2]
                second_direction = occurrences[1][2]
                if first_direction != tuple(reversed(second_direction)):
                    raise TopologyError(
                        f"Blocks sharing edge {current!r} overlap or are misoriented"
                    )

        for block in self.blocks:
            block_edges = [edge_key(*block.directed_edge(i)) for i in range(4)]
            if self.edge_cells[block_edges[0]] != self.edge_cells[block_edges[2]]:
                raise TopologyError(f"Block {block.id} has unequal x-edge counts")
            if self.edge_cells[block_edges[1]] != self.edge_cells[block_edges[3]]:
                raise TopologyError(f"Block {block.id} has unequal y-edge counts")

    def _validate_edge_geometry(
        self, current: EdgeKey, geometry: EdgeGeometry
    ) -> None:
        if not isinstance(geometry, EdgeGeometry):
            raise TopologyError(f"Edge {current!r} has invalid geometry data")
        if geometry.kind not in ("arc", *self.MULTI_POINT_EDGE_TYPES):
            raise TopologyError(
                f"Edge {current!r} has unsupported type {geometry.kind!r}"
            )
        if geometry.kind == "arc" and len(geometry.points) != 1:
            raise TopologyError("An arc edge needs exactly one interpolation point")
        if geometry.kind in self.MULTI_POINT_EDGE_TYPES and not geometry.points:
            raise TopologyError(
                f"A {geometry.kind} needs at least one interpolation point"
            )
        for point in geometry.points:
            if len(point) != 2 or not all(math.isfinite(value) for value in point):
                raise TopologyError("Interpolation point coordinates must be finite")
        if geometry.kind == "arc":
            self._arc_circle(current, geometry)
            return

        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        for start, end in zip(path, path[1:]):
            if math.hypot(end[0] - start[0], end[1] - start[1]) \
                    <= self.COORDINATE_TOLERANCE:
                raise TopologyError(
                    f"{geometry.kind} interpolation points must not coincide with "
                    "adjacent points"
                )

    def _geometry_curve(self, curve_id: str) -> GeometryCurve:
        try:
            return self.geometry_curves[curve_id]
        except KeyError as exc:
            raise TopologyError(f"Unknown geometry curve {curve_id!r}") from exc

    @classmethod
    def _normalized_geometry_points(
        cls,
        points: Iterable[tuple[float, float]],
    ) -> tuple[tuple[float, float], ...]:
        normalized: list[tuple[float, float]] = []
        for point in points:
            try:
                x, y = point
            except (TypeError, ValueError) as exc:
                raise TopologyError(
                    "Each geometry point must contain exactly two coordinates"
                ) from exc
            if isinstance(x, bool) or isinstance(y, bool) \
                    or not isinstance(x, (int, float)) \
                    or not isinstance(y, (int, float)) \
                    or not (math.isfinite(x) and math.isfinite(y)):
                raise TopologyError("Geometry point coordinates must be finite")
            normalized.append((float(x), float(y)))
        return tuple(normalized)

    @classmethod
    def _validate_geometry_curve(cls, curve: GeometryCurve) -> None:
        if not isinstance(curve.id, str) or not curve.id:
            raise TopologyError("Every geometry curve needs a non-empty string ID")
        if not isinstance(curve.name, str) or not curve.name.strip() \
                or curve.name != curve.name.strip() or "\n" in curve.name:
            raise TopologyError(
                "A geometry curve name must be non-empty without outer whitespace"
            )
        if not isinstance(curve.show_points, bool):
            raise TopologyError(
                "Geometry point visibility must be true or false"
            )
        if len(curve.points) < 2:
            raise TopologyError("A geometry curve needs at least two points")
        for point in curve.points:
            if len(point) != 2 or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in point
            ):
                raise TopologyError("Geometry point coordinates must be finite")
        for first, second in zip(curve.points, curve.points[1:]):
            if math.hypot(
                second[0] - first[0], second[1] - first[1]
            ) <= cls.COORDINATE_TOLERANCE:
                raise TopologyError("Adjacent geometry points must not coincide")

    def _next_geometry_curve_name(self) -> str:
        used = {curve.name for curve in self.geometry_curves.values()}
        index = 1
        while f"curve{index}" in used:
            index += 1
        return f"curve{index}"

    def _default_edge_point(self, current: EdgeKey) -> tuple[float, float]:
        occurrences = self.edge_occurrences()[current]
        first_id, second_id = occurrences[0][2]
        first = self.vertices[first_id]
        second = self.vertices[second_id]
        dx = second.x - first.x
        dy = second.y - first.y
        length = math.hypot(dx, dy)
        if length <= self.COORDINATE_TOLERANCE:
            raise TopologyError("Cannot curve a zero-length edge")
        # Blocks are counter-clockwise, so their interior is left of each
        # directed edge and the right normal points out of the first block.
        midpoint = ((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
        height = length * self.DEFAULT_CONTROL_POINT_OFFSET_RATIO
        return (
            midpoint[0] + (dy / length) * height,
            midpoint[1] - (dx / length) * height,
        )

    def _polyline_point(
        self, current: EdgeKey, geometry: EdgeGeometry, fraction: float
    ) -> tuple[float, float]:
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in zip(path, path[1:])
        ]
        target = fraction * sum(lengths)
        traversed = 0.0
        for index, length in enumerate(lengths):
            if target <= traversed + length or index == len(lengths) - 1:
                local = min(1.0, max(0.0, (target - traversed) / length))
                start = path[index]
                end = path[index + 1]
                return (
                    start[0] + local * (end[0] - start[0]),
                    start[1] + local * (end[1] - start[1]),
                )
            traversed += length
        return path[-1]

    def _spline_point(
        self, current: EdgeKey, geometry: EdgeGeometry, fraction: float
    ) -> tuple[float, float]:
        """Evaluate OpenFOAM's through-point Catmull-Rom spline."""
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        return self._spline_path_point(path, fraction)

    @staticmethod
    def _spline_path_point(
        path: tuple[tuple[float, float], ...] | list[tuple[float, float]],
        fraction: float,
    ) -> tuple[float, float]:
        """Evaluate a through-point Catmull-Rom path by chord fraction."""
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in zip(path, path[1:])
        ]
        target = fraction * sum(lengths)
        traversed = 0.0
        segment = len(lengths) - 1
        local = 1.0
        for index, length in enumerate(lengths):
            if target <= traversed + length or index == len(lengths) - 1:
                segment = index
                local = min(1.0, max(0.0, (target - traversed) / length))
                break
            traversed += length

        return MeshModel._catmull_rom_segment_point(path, segment, local)

    @staticmethod
    def _catmull_rom_segment_point(
        path: tuple[tuple[float, float], ...] | list[tuple[float, float]],
        segment: int,
        local: float,
    ) -> tuple[float, float]:
        p0 = path[segment]
        p1 = path[segment + 1]
        before = (
            path[segment - 1]
            if segment > 0
            else (2.0 * p0[0] - p1[0], 2.0 * p0[1] - p1[1])
        )
        after = (
            path[segment + 2]
            if segment + 2 < len(path)
            else (2.0 * p1[0] - p0[0], 2.0 * p1[1] - p0[1])
        )
        local_squared = local * local
        local_cubed = local_squared * local
        return (
            0.5 * (
                2.0 * p0[0]
                + (-before[0] + p1[0]) * local
                + (2.0 * before[0] - 5.0 * p0[0]
                   + 4.0 * p1[0] - after[0]) * local_squared
                + (-before[0] + 3.0 * p0[0]
                   - 3.0 * p1[0] + after[0]) * local_cubed
            ),
            0.5 * (
                2.0 * p0[1]
                + (-before[1] + p1[1]) * local
                + (2.0 * before[1] - 5.0 * p0[1]
                   + 4.0 * p1[1] - after[1]) * local_squared
                + (-before[1] + 3.0 * p0[1]
                   - 3.0 * p1[1] + after[1]) * local_cubed
            ),
        )

    def _arc_circle(
        self, current: EdgeKey, geometry: EdgeGeometry
    ) -> tuple[float, float, float, float, float]:
        """Return center, radius, start angle and signed sweep for an arc."""
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        middle_x, middle_y = geometry.points[0]

        # Translate the circumcircle calculation to the first endpoint for
        # better numerical behavior when world coordinates are large.
        bx = middle_x - first.x
        by = middle_y - first.y
        cx = second.x - first.x
        cy = second.y - first.y
        b_squared = bx * bx + by * by
        c_squared = cx * cx + cy * cy
        chord_squared = (cx - bx) ** 2 + (cy - by) ** 2
        scale_squared = max(b_squared, c_squared, chord_squared)
        determinant = 2.0 * (bx * cy - by * cx)
        if scale_squared <= self.COORDINATE_TOLERANCE ** 2 or math.isclose(
            determinant,
            0.0,
            rel_tol=0.0,
            abs_tol=2.0 * self.COORDINATE_TOLERANCE * scale_squared,
        ):
            raise TopologyError(
                "Arc interpolation point must not be collinear with its endpoints"
            )

        relative_center_x = (cy * b_squared - by * c_squared) / determinant
        relative_center_y = (bx * c_squared - cx * b_squared) / determinant
        center_x = first.x + relative_center_x
        center_y = first.y + relative_center_y
        radius = math.hypot(relative_center_x, relative_center_y)
        if not all(math.isfinite(value) for value in (center_x, center_y, radius)):
            raise TopologyError("Arc interpolation point produces invalid geometry")

        start_angle = math.atan2(first.y - center_y, first.x - center_x)
        middle_angle = math.atan2(middle_y - center_y, middle_x - center_x)
        end_angle = math.atan2(second.y - center_y, second.x - center_x)
        full_turn = 2.0 * math.pi
        ccw_to_end = (end_angle - start_angle) % full_turn
        ccw_to_middle = (middle_angle - start_angle) % full_turn
        sweep = (
            ccw_to_end
            if ccw_to_middle <= ccw_to_end + 1.0e-10
            else ccw_to_end - full_turn
        )
        return center_x, center_y, radius, start_angle, sweep

    def _validate_convex_ccw(self, block: Block) -> None:
        points = [self.vertices[vertex_id] for vertex_id in block.vertices]
        cross_products = []
        for index in range(4):
            first = points[index]
            second = points[(index + 1) % 4]
            third = points[(index + 2) % 4]
            cross_products.append(
                (second.x - first.x) * (third.y - second.y)
                - (second.y - first.y) * (third.x - second.x)
            )
        if any(value <= self.COORDINATE_TOLERANCE for value in cross_products):
            raise TopologyError(
                f"Block {block.id} must remain strictly convex and counter-clockwise"
            )

    def _counter_clockwise_vertices(
        self, identifiers: Iterable[str]
    ) -> tuple[str, str, str, str]:
        vertex_ids = list(identifiers)
        center_x = sum(self.vertices[value].x for value in vertex_ids) / 4.0
        center_y = sum(self.vertices[value].y for value in vertex_ids) / 4.0
        ordered = sorted(
            vertex_ids,
            key=lambda value: (
                math.atan2(
                    self.vertices[value].y - center_y,
                    self.vertices[value].x - center_x,
                ),
                value,
            ),
        )
        # Rotate to a stable starting ID without changing counter-clockwise order.
        start = min(range(4), key=lambda index: ordered[index])
        ordered = ordered[start:] + ordered[:start]
        return tuple(ordered)  # type: ignore[return-value]

    def _vertex_at_or_new(self, x: float, y: float) -> Vertex:
        for vertex in self.vertices.values():
            if self._coordinates_match(vertex.x, vertex.y, x, y):
                return vertex
        vertex_id = self._next_id("v", self.vertices)
        vertex = Vertex(vertex_id, x, y)
        self.vertices[vertex_id] = vertex
        return vertex

    @classmethod
    def _coordinates_match(cls, x1: float, y1: float,
                           x2: float, y2: float) -> bool:
        return math.isclose(
            x1, x2, rel_tol=0.0, abs_tol=cls.COORDINATE_TOLERANCE
        ) and math.isclose(
            y1, y2, rel_tol=0.0, abs_tol=cls.COORDINATE_TOLERANCE
        )

    @staticmethod
    def _next_id(prefix: str, identifiers: Iterable[str]) -> str:
        used = set(identifiers)
        index = 0
        while f"{prefix}{index}" in used:
            index += 1
        return f"{prefix}{index}"
