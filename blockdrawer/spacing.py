"""Persistent endpoint cell-spacing links and grading propagation."""

from __future__ import annotations

from collections import deque
import math
from typing import Iterable

from .domain import EdgeKey, SpacingLink, TopologyError, edge_key
from .grading import (
    _cell_ratio_log_from_start_width,
    _finite_expansion_ratio,
)


class SpacingOperationsMixin:
    """Link incident edge widths without making geometry edits reactive."""

    SPACING_WIDTH_RELATIVE_TOLERANCE = 1.0e-9
    SPACING_WIDTH_ABSOLUTE_TOLERANCE = 1.0e-12

    def spacing_links_for_edge(self, edge: EdgeKey) -> tuple[SpacingLink, ...]:
        """Return the links attached to either endpoint of ``edge``."""
        current = edge_key(*edge)
        return tuple(sorted(
            link
            for link in self.spacing_links
            if current in (link.first_edge, link.second_edge)
        ))

    def spacing_link_at(
        self, edge: EdgeKey, vertex: str
    ) -> SpacingLink | None:
        """Return the optional link using this particular edge endpoint."""
        current = edge_key(*edge)
        for link in self.spacing_links:
            if link.vertex == vertex \
                    and current in (link.first_edge, link.second_edge):
                return link
        return None

    def spacing_linked_component(self, edge: EdgeKey) -> set[EdgeKey]:
        """Return every edge reached through endpoint spacing links."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        reached = {current}
        pending = deque([current])
        while pending:
            source = pending.popleft()
            for link in self.spacing_links_for_edge(source):
                other = self._other_spacing_edge(link, source)
                if other not in reached:
                    reached.add(other)
                    pending.append(other)
        return reached

    def spacing_link_is_synchronized(self, link: SpacingLink) -> bool:
        """Return whether both stored endpoints currently have equal widths."""
        if link not in self.spacing_links:
            raise TopologyError("Unknown spacing link")
        return self._spacing_widths_match(
            self.edge_width_at_vertex(link.first_edge, link.vertex),
            self.edge_width_at_vertex(link.second_edge, link.vertex),
        )

    def add_spacing_link(
        self, first_edge: EdgeKey, second_edge: EdgeKey
    ) -> SpacingLink:
        """Link two incident endpoints and grade the second from the first."""
        driver = edge_key(*first_edge)
        link = self._normalized_spacing_link(first_edge, second_edge)
        if link in self.spacing_links:
            raise TopologyError("Those edge endpoints are already linked")
        for current in (link.first_edge, link.second_edge):
            existing = self.spacing_link_at(current, link.vertex)
            if existing is not None:
                raise TopologyError(
                    f"Edge {current!r} is already spacing-linked at "
                    f"vertex {link.vertex!r}"
                )

        previous_grading = dict(self.edge_grading)
        self.spacing_links.add(link)
        try:
            self._propagate_spacing_links((driver,))
            self.validate()
        except Exception:
            self.spacing_links.remove(link)
            self.edge_grading = previous_grading
            raise
        return link

    def remove_spacing_link(
        self, first_edge: EdgeKey, second_edge: EdgeKey
    ) -> SpacingLink:
        """Remove one exact pair while retaining its current grading."""
        link = self._normalized_spacing_link(first_edge, second_edge)
        if link not in self.spacing_links:
            raise TopologyError("Those edge endpoints are not linked")
        self.spacing_links.remove(link)
        return link

    def synchronize_spacing_links(self, edge: EdgeKey) -> set[EdgeKey]:
        """Use ``edge`` as the driver and synchronize its complete link chain."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        previous = dict(self.edge_grading)
        try:
            affected = self._propagate_spacing_links((current,))
            self.validate()
        except Exception:
            self.edge_grading = previous
            raise
        return affected

    def edge_width_at_vertex(self, edge: EdgeKey, vertex: str) -> float:
        """Return the grading cell width touching ``vertex`` on ``edge``."""
        current = edge_key(*edge)
        if vertex not in current:
            raise TopologyError(
                f"Vertex {vertex!r} is not an endpoint of edge {current!r}"
            )
        values = self.edge_grading_values(current)
        return values.start_width if vertex == current[0] else values.end_width

    def _propagate_spacing_links(
        self, anchors: Iterable[EdgeKey]
    ) -> set[EdgeKey]:
        """Propagate fixed anchor widths through every reachable link."""
        fixed = {edge_key(*current) for current in anchors}
        if not fixed:
            return set()
        unknown = fixed - set(self.edge_cells)
        if unknown:
            raise TopologyError(f"Unknown spacing-link anchor edges {unknown}")
        pending = deque(sorted(fixed))
        while pending:
            current = pending.popleft()
            for vertex in current:
                link = self.spacing_link_at(current, vertex)
                if link is None:
                    continue
                other = self._other_spacing_edge(link, current)
                target_width = self.edge_width_at_vertex(current, vertex)
                if other in fixed:
                    other_width = self.edge_width_at_vertex(other, vertex)
                    if not self._spacing_widths_match(
                        target_width, other_width
                    ):
                        raise TopologyError(
                            "Spacing-link propagation has conflicting widths at "
                            f"vertex {vertex!r}: {target_width:.12g} and "
                            f"{other_width:.12g}"
                        )
                    continue
                self._set_edge_width_at_vertex(other, vertex, target_width)
                fixed.add(other)
                pending.append(other)
        return fixed

    def _set_edge_width_at_vertex(
        self, edge: EdgeKey, vertex: str, width: float
    ) -> None:
        current = edge_key(*edge)
        if vertex not in current:
            raise TopologyError(
                f"Vertex {vertex!r} is not an endpoint of edge {current!r}"
            )
        length = self.edge_length(current)
        cells = self.edge_cells[current]
        if cells == 1:
            if not self._spacing_widths_match(width, length):
                raise TopologyError(
                    f"One-cell edge {current!r} has fixed width {length:.12g} "
                    f"and cannot match {width:.12g} at vertex {vertex!r}"
                )
            self.edge_grading.pop(current, None)
            return
        if not math.isfinite(width) or not 0.0 < width < length:
            raise TopologyError(
                f"Edge {current!r} cannot attain cell width {width:.12g} "
                f"at vertex {vertex!r}"
            )
        local_log_ratio = _cell_ratio_log_from_start_width(
            length, cells, width
        )
        canonical_log_ratio = (
            local_log_ratio if vertex == current[0] else -local_log_ratio
        )
        logarithm = (cells - 1) * canonical_log_ratio
        if abs(logarithm) <= 1.0e-14:
            self.edge_grading.pop(current, None)
        else:
            self.edge_grading[current] = _finite_expansion_ratio(logarithm)

    def _normalized_spacing_link(
        self, first_edge: EdgeKey, second_edge: EdgeKey
    ) -> SpacingLink:
        first = edge_key(*first_edge)
        second = edge_key(*second_edge)
        if first == second:
            raise TopologyError("A spacing link needs two different edges")
        actual_edges = set(self.edge_cells)
        unknown = {first, second} - actual_edges
        if unknown:
            raise TopologyError(f"Spacing link references unknown edges {unknown}")
        shared = set(first) & set(second)
        if len(shared) != 1:
            raise TopologyError(
                "Spacing-linked edges must share exactly one vertex"
            )
        vertex = next(iter(shared))
        ordered = sorted((first, second))
        return SpacingLink(vertex, ordered[0], ordered[1])

    @staticmethod
    def _other_spacing_edge(link: SpacingLink, edge: EdgeKey) -> EdgeKey:
        return (
            link.second_edge if edge == link.first_edge else link.first_edge
        )

    def _spacing_widths_match(self, first: float, second: float) -> bool:
        return math.isclose(
            first,
            second,
            rel_tol=self.SPACING_WIDTH_RELATIVE_TOLERANCE,
            abs_tol=self.SPACING_WIDTH_ABSOLUTE_TOLERANCE,
        )

    def _validate_spacing_links(self, actual_edges: set[EdgeKey]) -> None:
        occupied: set[tuple[EdgeKey, str]] = set()
        for link in self.spacing_links:
            if not isinstance(link, SpacingLink):
                raise TopologyError("Spacing-link data has an invalid entry")
            if link.first_edge >= link.second_edge:
                raise TopologyError("Spacing-link edges are not normalized")
            if link.first_edge not in actual_edges \
                    or link.second_edge not in actual_edges:
                raise TopologyError("Spacing link references an unknown edge")
            if set(link.first_edge) & set(link.second_edge) != {link.vertex}:
                raise TopologyError(
                    "Spacing-linked edges must share their stored vertex"
                )
            for current in (link.first_edge, link.second_edge):
                endpoint = (current, link.vertex)
                if endpoint in occupied:
                    raise TopologyError(
                        f"Edge {current!r} has multiple spacing links at "
                        f"vertex {link.vertex!r}"
                    )
                occupied.add(endpoint)

    def _prune_spacing_links(self) -> None:
        """Drop links whose topology endpoints no longer exist."""
        actual_edges = set(self.edge_cells)
        self.spacing_links = {
            link
            for link in self.spacing_links
            if link.first_edge in actual_edges
            and link.second_edge in actual_edges
            and set(link.first_edge) & set(link.second_edge) == {link.vertex}
        }

    def _remap_spacing_link_endpoints(
        self,
        replacements: dict[tuple[EdgeKey, str], EdgeKey | None],
    ) -> None:
        """Transfer persistent links across a compound topology rewrite."""
        remapped: set[SpacingLink] = set()
        actual_edges = set(self.edge_cells)
        for link in self.spacing_links:
            mapped_edges = []
            for current in (link.first_edge, link.second_edge):
                replacement = replacements.get((current, link.vertex), current)
                if replacement is None or replacement not in actual_edges:
                    break
                mapped_edges.append(replacement)
            if len(mapped_edges) != 2 or mapped_edges[0] == mapped_edges[1]:
                continue
            first, second = sorted(mapped_edges)
            if set(first) & set(second) != {link.vertex}:
                continue
            remapped.add(SpacingLink(link.vertex, first, second))
        self.spacing_links = remapped
        self._validate_spacing_links(actual_edges)
