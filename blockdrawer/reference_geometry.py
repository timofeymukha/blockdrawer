"""Reference-curve editing and mesh projection operations."""

from __future__ import annotations

import math
from typing import Iterable

from .domain import (
    EdgeGeometry,
    EdgeKey,
    GeometryCurve,
    ProjectionResult,
    TopologyError,
    edge_key,
)
from .projection import (
    DEFAULT_FIT_MAX_POINTS,
    FIT_RELATIVE_TOLERANCE,
    ProjectionError,
    ProjectionLocation,
    ReferenceProjector,
    SplineFit,
)


class ReferenceGeometryMixin:
    """Manage reference curves and project mesh entities onto them."""

    def add_geometry_curve(
        self,
        points: Iterable[tuple[float, float]],
        *,
        name: str | None = None,
        show_points: bool = True,
    ) -> GeometryCurve:
        """Add a smooth reference curve through ordered points."""
        identifier = self._next_id("g", self.geometry_curves)
        curve_name = self._next_geometry_curve_name() if name is None else name
        curve = GeometryCurve(
            identifier,
            curve_name,
            self._normalized_geometry_points(points),
            show_points,
        )
        self._validate_geometry_curve(curve)
        if any(existing.name == curve.name
               for existing in self.geometry_curves.values()):
            raise TopologyError(f"Geometry curve name {curve.name!r} already exists")
        self.geometry_curves[identifier] = curve
        return curve

    def remove_geometry_curve(self, curve_id: str) -> None:
        if curve_id not in self.geometry_curves:
            raise TopologyError(f"Unknown geometry curve {curve_id!r}")
        del self.geometry_curves[curve_id]

    def set_geometry_curve_name(self, curve_id: str, name: str) -> None:
        curve = self._geometry_curve(curve_id)
        replacement = GeometryCurve(
            curve.id, name, curve.points, curve.show_points
        )
        self._validate_geometry_curve(replacement)
        if any(
            identifier != curve_id and existing.name == replacement.name
            for identifier, existing in self.geometry_curves.items()
        ):
            raise TopologyError(
                f"Geometry curve name {replacement.name!r} already exists"
            )
        self.geometry_curves[curve_id] = replacement

    def replace_geometry_curve_points(
        self,
        curve_id: str,
        points: Iterable[tuple[float, float]],
    ) -> None:
        curve = self._geometry_curve(curve_id)
        replacement = GeometryCurve(
            curve.id,
            curve.name,
            self._normalized_geometry_points(points),
            curve.show_points,
        )
        self._validate_geometry_curve(replacement)
        self.geometry_curves[curve_id] = replacement

    def set_geometry_curve_point_visibility(
        self, curve_id: str, visible: bool
    ) -> None:
        curve = self._geometry_curve(curve_id)
        if not isinstance(visible, bool):
            raise TopologyError("Geometry point visibility must be true or false")
        self.geometry_curves[curve_id] = GeometryCurve(
            curve.id, curve.name, curve.points, visible
        )

    def set_geometry_curve_point(
        self,
        curve_id: str,
        index: int,
        x: float,
        y: float,
    ) -> None:
        curve = self._geometry_curve(curve_id)
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(curve.points):
            raise TopologyError("Geometry point index is out of range")
        points = list(curve.points)
        points[index] = (x, y)
        self.replace_geometry_curve_points(curve_id, points)

    def add_geometry_curve_point(
        self,
        curve_id: str,
        after_index: int,
    ) -> int:
        """Insert after a point, extrapolating when appending at the end."""
        curve = self._geometry_curve(curve_id)
        if isinstance(after_index, bool) or not isinstance(after_index, int) \
                or not 0 <= after_index < len(curve.points):
            raise TopologyError("Geometry point index is out of range")
        points = list(curve.points)
        left = points[after_index]
        if after_index + 1 < len(points):
            right = points[after_index + 1]
            inserted = (
                (left[0] + right[0]) / 2.0,
                (left[1] + right[1]) / 2.0,
            )
        else:
            previous = points[after_index - 1]
            inserted = (
                left[0] + left[0] - previous[0],
                left[1] + left[1] - previous[1],
            )
        new_index = after_index + 1
        points.insert(new_index, inserted)
        self.replace_geometry_curve_points(curve_id, points)
        return new_index

    def remove_geometry_curve_point(self, curve_id: str, index: int) -> None:
        curve = self._geometry_curve(curve_id)
        if len(curve.points) <= 2:
            raise TopologyError("A geometry curve needs at least two points")
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(curve.points):
            raise TopologyError("Geometry point index is out of range")
        points = list(curve.points)
        del points[index]
        self.replace_geometry_curve_points(curve_id, points)

    def geometry_curve_point(
        self,
        curve_id: str,
        fraction: float,
    ) -> tuple[float, float]:
        """Evaluate a reference curve from its first to its last point."""
        curve = self._geometry_curve(curve_id)
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise TopologyError("Curve fraction must be between 0 and 1")
        return self._spline_path_point(curve.points, fraction)

    def geometry_curve_render_points(
        self,
        curve_id: str,
        *,
        samples_per_span: int = 4,
    ) -> tuple[tuple[float, float], ...]:
        """Sample every curve span while retaining all defining points."""
        curve = self._geometry_curve(curve_id)
        self._validate_samples_per_span(samples_per_span)
        return self._spline_path_render_points(curve.points, samples_per_span)

    @staticmethod
    def _validate_samples_per_span(samples_per_span: int) -> None:
        if isinstance(samples_per_span, bool) \
                or not isinstance(samples_per_span, int) \
                or samples_per_span < 1:
            raise TopologyError("Curve samples per span must be a positive integer")

    @classmethod
    def _spline_path_render_points(
        cls,
        path: tuple[tuple[float, float], ...] | list[tuple[float, float]],
        samples_per_span: int,
    ) -> tuple[tuple[float, float], ...]:
        """Sample each Catmull-Rom span and retain its exact endpoints."""
        result = [path[0]]
        for segment in range(len(path) - 1):
            for sample in range(1, samples_per_span + 1):
                if sample == samples_per_span:
                    result.append(path[segment + 1])
                else:
                    result.append(cls._catmull_rom_segment_point(
                        path,
                        segment,
                        sample / samples_per_span,
                    ))
        return tuple(result)

    def project_to_geometry(
        self,
        curve_ids: Iterable[str],
        direction: str,
        *,
        vertex_ids: Iterable[str] = (),
        edges: Iterable[EdgeKey] = (),
        fit: bool = False,
        fit_relative_tolerance: float = FIT_RELATIVE_TOLERANCE,
        fit_max_points: int = DEFAULT_FIT_MAX_POINTS,
    ) -> ProjectionResult:
        """Project vertices or complete edge definitions as one atomic edit."""
        if not isinstance(fit, bool):
            raise TopologyError("Projection fit must be true or false")
        selected_vertices = tuple(dict.fromkeys(vertex_ids))
        selected_edges_list: list[EdgeKey] = []
        for selected in edges:
            try:
                first, second = selected
            except (TypeError, ValueError) as exc:
                raise TopologyError(
                    "Each selected projection edge needs two vertex IDs"
                ) from exc
            if not isinstance(first, str) or not isinstance(second, str):
                raise TopologyError(
                    "Each selected projection edge needs two vertex IDs"
                )
            current = edge_key(first, second)
            if current not in selected_edges_list:
                selected_edges_list.append(current)
        selected_edges = tuple(selected_edges_list)
        if bool(selected_vertices) == bool(selected_edges):
            raise TopologyError(
                "Select either one or more vertices or one or more edges, not both"
            )
        if fit and selected_vertices:
            raise TopologyError("Fit is available only when projecting edges")
        for vertex_id in selected_vertices:
            if not isinstance(vertex_id, str) or vertex_id not in self.vertices:
                raise TopologyError(f"Unknown projection vertex {vertex_id!r}")
        for current in selected_edges:
            if current not in self.edge_cells:
                raise TopologyError(f"Unknown projection edge {current!r}")

        selected_curve_ids = tuple(dict.fromkeys(curve_ids))
        if not selected_curve_ids:
            raise TopologyError("Select at least one reference curve")
        for curve_id in selected_curve_ids:
            if not isinstance(curve_id, str) or curve_id not in self.geometry_curves:
                raise TopologyError(
                    f"Unknown projection reference curve {curve_id!r}"
                )
        try:
            projector = ReferenceProjector(
                self.geometry_curves[curve_id].points
                for curve_id in selected_curve_ids
            )
        except ProjectionError as exc:
            raise TopologyError(str(exc)) from exc

        def projected_locations(
            point: tuple[float, float], description: str
        ) -> tuple[ProjectionLocation, ...]:
            try:
                return projector.project_locations(point, direction)
            except ProjectionError as exc:
                raise TopologyError(
                    f"Cannot project {description}: {exc}"
                ) from exc

        def projected_point(
            point: tuple[float, float], description: str
        ) -> tuple[float, float]:
            return projected_locations(point, description)[0].point

        moved_vertex_ids: list[str] = list(selected_vertices)
        if selected_edges:
            for current in selected_edges:
                for vertex_id in current:
                    if vertex_id not in moved_vertex_ids:
                        moved_vertex_ids.append(vertex_id)

        projected_vertices: dict[str, tuple[float, float]] = {}
        projected_vertex_locations: dict[
            str, tuple[ProjectionLocation, ...]
        ] = {}
        projected_geometry: dict[EdgeKey, EdgeGeometry] = {}
        for vertex_id in moved_vertex_ids:
            vertex = self.vertices[vertex_id]
            locations = projected_locations(
                (vertex.x, vertex.y), f"vertex {vertex_id}"
            )
            projected_vertex_locations[vertex_id] = locations
            projected_vertices[vertex_id] = locations[0].point
        fits: list[tuple[EdgeKey, SplineFit]] = []
        for current in selected_edges:
            if fit:
                source_samples = tuple(
                    self.edge_point(current, index / 64.0)
                    for index in range(65)
                )
                candidate_fits: list[SplineFit] = []
                fit_errors: list[ProjectionError] = []
                for start_location in projected_vertex_locations[current[0]]:
                    for end_location in projected_vertex_locations[current[1]]:
                        if start_location.curve_index != end_location.curve_index:
                            continue
                        try:
                            candidate_fits.append(projector.fit_spline(
                                start_location,
                                end_location,
                                source_samples,
                                relative_tolerance=fit_relative_tolerance,
                                max_interpolation_points=fit_max_points,
                                minimum_point_separation=(
                                    self.COORDINATE_TOLERANCE
                                ),
                            ))
                        except ProjectionError as exc:
                            fit_errors.append(exc)
                if not candidate_fits:
                    if fit_errors:
                        detail = str(fit_errors[0])
                    else:
                        detail = (
                            "A fitted edge's endpoints must project onto the "
                            "same reference curve"
                        )
                    raise TopologyError(
                        f"Cannot fit edge {current[0]}—{current[1]}: {detail}"
                    )
                spline_fit = min(
                    candidate_fits,
                    key=lambda candidate: (
                        candidate.score,
                        candidate.max_error,
                        len(candidate.points),
                    ),
                )
                projected_geometry[current] = EdgeGeometry(
                    "spline", spline_fit.points
                )
                fits.append((current, spline_fit))
                continue
            geometry = self.edge_geometry.get(current)
            if geometry is None:
                continue
            projected_geometry[current] = EdgeGeometry(
                geometry.kind,
                tuple(
                    projected_point(
                        point,
                        f"interpolation point {index + 1} on edge "
                        f"{current[0]}—{current[1]}",
                    )
                    for index, point in enumerate(geometry.points)
                ),
            )

        previous_vertices = {
            vertex_id: (
                self.vertices[vertex_id].x,
                self.vertices[vertex_id].y,
            )
            for vertex_id in moved_vertex_ids
        }
        previous_geometry = dict(self.edge_geometry)
        converted_arcs: list[EdgeKey] = []
        try:
            for vertex_id, (x, y) in projected_vertices.items():
                self.vertices[vertex_id].x = x
                self.vertices[vertex_id].y = y
            self.edge_geometry.update(projected_geometry)

            # Three projected points do not always remain circular. Preserve a
            # valid arc, otherwise use a through-point spline rather than losing
            # the projected interpolation point or creating invalid OpenFOAM.
            for current, geometry in projected_geometry.items():
                if geometry.kind != "arc":
                    continue
                try:
                    self._validate_edge_geometry(current, geometry)
                except (TopologyError, ValueError):
                    replacement = EdgeGeometry("spline", geometry.points)
                    self._validate_edge_geometry(current, replacement)
                    self.edge_geometry[current] = replacement
                    converted_arcs.append(current)
            self.validate()
        except (TopologyError, ValueError) as exc:
            for vertex_id, (x, y) in previous_vertices.items():
                self.vertices[vertex_id].x = x
                self.vertices[vertex_id].y = y
            self.edge_geometry = previous_geometry
            if isinstance(exc, TopologyError):
                raise
            raise TopologyError(str(exc)) from exc

        worst_fit = max(
            (spline_fit for _current, spline_fit in fits),
            key=lambda spline_fit: spline_fit.max_error,
            default=None,
        )
        return ProjectionResult(
            tuple(moved_vertex_ids),
            selected_edges,
            len(moved_vertex_ids) + sum(
                len(geometry.points)
                for geometry in projected_geometry.values()
            ),
            tuple(converted_arcs),
            tuple(current for current, _fit in fits),
            sum(len(spline_fit.points) for _current, spline_fit in fits),
            worst_fit.max_error if worst_fit is not None else None,
            worst_fit.tolerance if worst_fit is not None else None,
            all(
                spline_fit.max_error <= spline_fit.tolerance
                for _current, spline_fit in fits
            ),
        )
