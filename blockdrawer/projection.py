"""Numerical projection onto smooth 2D reference curves."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
import math
from typing import Iterable, Sequence


Point = tuple[float, float]
Polynomial = tuple[float, ...]
PROJECTION_DIRECTIONS = ("orthogonal", "x", "y")
FIT_RELATIVE_TOLERANCE = 1.0e-8
FIT_ABSOLUTE_TOLERANCE = 1.0e-12
DEFAULT_FIT_MAX_POINTS = 250
FIT_ERROR_SAMPLES_PER_SPAN = 8
FIT_MINIMUM_POINT_SEPARATION = 1.0e-9
_ROOT_TOLERANCE = 1.0e-13
_PARAMETER_TOLERANCE = 1.0e-12
_PROJECTION_TIE_RELATIVE_TOLERANCE = 1.0e-12
_PROJECTION_TIE_ABSOLUTE_TOLERANCE = 1.0e-13


class ProjectionError(ValueError):
    """Raised when a point has no valid projection onto the target curves."""


@dataclass(frozen=True)
class ProjectionLocation:
    """A projected point and its stable location on one target curve."""

    point: Point
    curve_index: int
    span_parameter: float
    distance: float


@dataclass(frozen=True)
class SplineFit:
    """Adaptive through-point spline approximation of a curve section."""

    points: tuple[Point, ...]
    max_error: float
    tolerance: float
    curve_index: int
    branch: str
    score: float


@dataclass(frozen=True)
class _CubicSegment:
    """One polynomial Catmull-Rom span, with exact coordinate bounds."""

    x: tuple[float, float, float, float]
    y: tuple[float, float, float, float]
    bounds: tuple[float, float, float, float]

    @classmethod
    def from_path(cls, path: Sequence[Point], index: int) -> _CubicSegment:
        first = path[index]
        second = path[index + 1]
        before = (
            path[index - 1]
            if index > 0
            else (
                2.0 * first[0] - second[0],
                2.0 * first[1] - second[1],
            )
        )
        after = (
            path[index + 2]
            if index + 2 < len(path)
            else (
                2.0 * second[0] - first[0],
                2.0 * second[1] - first[1],
            )
        )
        x = _catmull_rom_coefficients(
            before[0], first[0], second[0], after[0]
        )
        y = _catmull_rom_coefficients(
            before[1], first[1], second[1], after[1]
        )
        min_x, max_x = _polynomial_range(x)
        min_y, max_y = _polynomial_range(y)
        return cls(x, y, (min_x, min_y, max_x, max_y))

    @classmethod
    def from_coefficients(
        cls,
        x: Polynomial,
        y: Polynomial,
    ) -> _CubicSegment:
        """Construct a bounded cubic from polynomial coefficients."""
        padded_x = tuple(x) + (0.0,) * (4 - len(x))
        padded_y = tuple(y) + (0.0,) * (4 - len(y))
        cubic_x = padded_x[:4]
        cubic_y = padded_y[:4]
        min_x, max_x = _polynomial_range(cubic_x)
        min_y, max_y = _polynomial_range(cubic_y)
        return cls(cubic_x, cubic_y, (min_x, min_y, max_x, max_y))

    def point(self, parameter: float) -> Point:
        return (
            _polynomial_value(self.x, parameter),
            _polynomial_value(self.y, parameter),
        )

    def closest_parameter(self, point: Point) -> float:
        shifted_x = (self.x[0] - point[0], *self.x[1:])
        shifted_y = (self.y[0] - point[1], *self.y[1:])
        derivative_x = _polynomial_derivative(self.x)
        derivative_y = _polynomial_derivative(self.y)
        stationarity = _polynomial_add(
            _polynomial_multiply(shifted_x, derivative_x),
            _polynomial_multiply(shifted_y, derivative_y),
        )
        candidates = (0.0, 1.0, *_roots_in_unit_interval(stationarity))
        return min(
            candidates,
            key=lambda parameter: _distance_squared(
                point, self.point(parameter)
            ),
        )


@dataclass(frozen=True)
class _ReferenceCurve:
    path: tuple[Point, ...]
    segments: tuple[_CubicSegment, ...]
    closed: bool
    bounds: tuple[float, float, float, float]

    @classmethod
    def from_path(cls, path: tuple[Point, ...]) -> _ReferenceCurve:
        segments = tuple(
            _CubicSegment.from_path(path, index)
            for index in range(len(path) - 1)
        )
        min_x = min(segment.bounds[0] for segment in segments)
        min_y = min(segment.bounds[1] for segment in segments)
        max_x = max(segment.bounds[2] for segment in segments)
        max_y = max(segment.bounds[3] for segment in segments)
        coordinate_scale = max(
            1.0,
            *(abs(value) for point in path for value in point),
        )
        closed = math.dist(path[0], path[-1]) \
            <= 1.0e-9 * coordinate_scale
        return cls(path, segments, closed, (min_x, min_y, max_x, max_y))

    @property
    def span_count(self) -> int:
        return len(self.segments)

    def point(self, span_parameter: float) -> Point:
        if self.closed:
            normalized = span_parameter % self.span_count
        else:
            normalized = min(
                float(self.span_count), max(0.0, span_parameter)
            )
        if normalized >= self.span_count:
            return self.segments[-1].point(1.0)
        segment_index = min(int(math.floor(normalized)), self.span_count - 1)
        local = normalized - segment_index
        return self.segments[segment_index].point(local)

    def interval_coefficients(
        self, start: float, end: float
    ) -> tuple[Polynomial, Polynomial]:
        """Return one curve-span polynomial over a route interval [0, 1]."""
        middle = (start + end) / 2.0
        base = math.floor(middle)
        if self.closed:
            segment_index = base % self.span_count
        else:
            segment_index = min(max(base, 0), self.span_count - 1)
        local_start = start - base
        local_end = end - base
        segment = self.segments[segment_index]
        return (
            _polynomial_affine(
                segment.x, local_start, local_end - local_start
            ),
            _polynomial_affine(
                segment.y, local_start, local_end - local_start
            ),
        )


@dataclass(frozen=True)
class _SegmentReference:
    curve_index: int
    segment_index: int
    segment: _CubicSegment


@dataclass(frozen=True)
class _RouteSegment:
    """One original target cubic restricted to a fitted route interval."""

    start: float
    end: float
    cubic: _CubicSegment


@dataclass(frozen=True)
class _FitDeviation:
    """Maximum sampled geometric deviation and where to refine it."""

    error: float
    route_parameter: float


class ReferenceProjector:
    """Project any number of points onto a fixed set of reference curves."""

    def __init__(self, curves: Iterable[Sequence[Point]]) -> None:
        reference_curves: list[_ReferenceCurve] = []
        segments: list[_SegmentReference] = []
        for curve in curves:
            path = tuple(curve)
            if len(path) < 2:
                raise ProjectionError(
                    "A projection target needs at least two points"
                )
            if any(
                len(point) != 2
                or not all(math.isfinite(value) for value in point)
                for point in path
            ):
                raise ProjectionError(
                    "Projection target coordinates must be finite point pairs"
                )
            reference = _ReferenceCurve.from_path(path)
            curve_index = len(reference_curves)
            reference_curves.append(reference)
            segments.extend(
                _SegmentReference(curve_index, index, segment)
                for index, segment in enumerate(reference.segments)
            )
        if not segments:
            raise ProjectionError("Select at least one reference curve")
        self._curves = tuple(reference_curves)
        self._segments = tuple(segments)

    def project(self, point: Point, direction: str) -> Point:
        return self.project_location(point, direction).point

    def project_location(
        self, point: Point, direction: str
    ) -> ProjectionLocation:
        return self.project_locations(point, direction)[0]

    def project_locations(
        self, point: Point, direction: str
    ) -> tuple[ProjectionLocation, ...]:
        """Return every equally-near location, retaining curve intersections."""
        if direction not in PROJECTION_DIRECTIONS:
            raise ProjectionError(
                f"Unsupported projection direction {direction!r}"
            )
        if len(point) != 2 or not all(math.isfinite(value) for value in point):
            raise ProjectionError("The point to project must be a finite pair")
        normalized = float(point[0]), float(point[1])
        if direction == "orthogonal":
            return self._orthogonal_projections(normalized)
        return self._axis_projections(normalized, direction)

    def fit_spline(
        self,
        start: ProjectionLocation,
        end: ProjectionLocation,
        source_samples: Sequence[Point],
        *,
        relative_tolerance: float = FIT_RELATIVE_TOLERANCE,
        max_interpolation_points: int = DEFAULT_FIT_MAX_POINTS,
        minimum_point_separation: float = FIT_MINIMUM_POINT_SEPARATION,
    ) -> SplineFit:
        """Fit the target section between two projected edge endpoints."""
        if not math.isfinite(relative_tolerance) or relative_tolerance <= 0.0:
            raise ProjectionError("Fit tolerance must be a positive number")
        if isinstance(max_interpolation_points, bool) \
                or not isinstance(max_interpolation_points, int) \
                or max_interpolation_points < 1:
            raise ProjectionError(
                "Maximum fit points must be a positive integer"
            )
        if not math.isfinite(minimum_point_separation) \
                or minimum_point_separation < 0.0:
            raise ProjectionError(
                "Minimum fit point separation must be a non-negative number"
            )
        if start.curve_index != end.curve_index:
            raise ProjectionError(
                "A fitted edge's endpoints must project onto the same "
                "reference curve"
            )
        if not 0 <= start.curve_index < len(self._curves):
            raise ProjectionError("Projected endpoint has an unknown target curve")
        if len(source_samples) < 2 or any(
            len(point) != 2
            or not all(math.isfinite(value) for value in point)
            for point in source_samples
        ):
            raise ProjectionError(
                "Curve-branch selection needs at least two finite source samples"
            )
        if math.dist(start.point, end.point) <= FIT_ABSOLUTE_TOLERANCE:
            raise ProjectionError(
                "Fitted edge endpoints project to the same location"
            )

        curve = self._curves[start.curve_index]
        direct_delta = end.span_parameter - start.span_parameter
        branch = "open"
        score: float
        if curve.closed:
            forward_delta = direct_delta % curve.span_count
            backward_delta = forward_delta - curve.span_count
            if abs(forward_delta) <= _PARAMETER_TOLERANCE:
                raise ProjectionError(
                    "Fitted edge endpoints do not define a non-empty closed-curve "
                    "section"
                )
            forward_score = self._branch_score(
                curve, start.span_parameter, forward_delta, source_samples
            )
            backward_score = self._branch_score(
                curve, start.span_parameter, backward_delta, source_samples
            )
            score_scale = max(
                FIT_ABSOLUTE_TOLERANCE * FIT_ABSOLUTE_TOLERANCE,
                forward_score,
                backward_score,
            )
            if abs(forward_score - backward_score) <= 1.0e-12 * score_scale:
                raise ProjectionError(
                    "The two closed-curve branches are equally close to this "
                    "edge; move the edge nearer the intended branch"
                )
            if forward_score < backward_score:
                delta = forward_delta
                branch = "forward"
                score = forward_score
            else:
                delta = backward_delta
                branch = "backward"
                score = backward_score
        else:
            delta = direct_delta
            if abs(delta) <= _PARAMETER_TOLERANCE:
                raise ProjectionError(
                    "Fitted edge endpoints do not define a curve section"
                )
            score = self._branch_score(
                curve, start.span_parameter, delta, source_samples
            )

        return self._fit_route(
            curve,
            start,
            end,
            delta,
            branch,
            score,
            relative_tolerance,
            max_interpolation_points,
            minimum_point_separation,
        )

    @staticmethod
    def _branch_score(
        curve: _ReferenceCurve,
        start: float,
        delta: float,
        source_samples: Sequence[Point],
    ) -> float:
        breaks = _route_break_parameters(start, delta)
        intervals = tuple(zip(breaks, breaks[1:]))
        polynomials = tuple(
            curve.interval_coefficients(
                start + delta * first,
                start + delta * second,
            )
            for first, second in intervals
        )
        return sum(
            min(
                _point_to_polynomial_curve_distance_squared(
                    x_coefficients, y_coefficients, source
                )
                for x_coefficients, y_coefficients in polynomials
            )
            for source in source_samples
        )

    def _fit_route(
        self,
        curve: _ReferenceCurve,
        start: ProjectionLocation,
        end: ProjectionLocation,
        delta: float,
        branch: str,
        score: float,
        relative_tolerance: float,
        max_interpolation_points: int,
        minimum_point_separation: float,
    ) -> SplineFit:
        target_breaks = _route_break_parameters(start.span_parameter, delta)
        seed_points = [
            start.point if index == 0
            else end.point if index == len(target_breaks) - 1
            else curve.point(start.span_parameter + delta * parameter)
            for index, parameter in enumerate(target_breaks)
        ]
        min_x = min(point[0] for point in seed_points)
        min_y = min(point[1] for point in seed_points)
        max_x = max(point[0] for point in seed_points)
        max_y = max(point[1] for point in seed_points)
        route_length = sum(
            math.dist(first, second)
            for first, second in zip(seed_points, seed_points[1:])
        )
        fit_scale = max(
            math.hypot(max_x - min_x, max_y - min_y),
            route_length,
            math.dist(start.point, end.point),
        )
        tolerance = max(
            FIT_ABSOLUTE_TOLERANCE,
            relative_tolerance * fit_scale,
        )

        route_segments = _fit_route_segments(
            curve, start.span_parameter, delta, target_breaks
        )
        target_samples = _fit_target_samples(route_segments)
        node_parameters = [
            0.0,
            _fit_initial_parameter(target_samples),
            1.0,
        ]
        nodes = _fit_route_nodes(
            curve, start, end, delta, node_parameters
        )
        deviation = _maximum_fit_deviation(
            node_parameters, nodes, route_segments, target_samples
        )

        best_nodes: list[Point] | None = None
        best_deviation: _FitDeviation | None = None
        if _fit_nodes_are_separated(nodes, minimum_point_separation):
            best_nodes = nodes
            best_deviation = deviation
        if 1 <= len(target_breaks) - 2 <= max_interpolation_points:
            native_nodes = _fit_route_nodes(
                curve, start, end, delta, target_breaks
            )
            if _fit_nodes_are_separated(
                native_nodes, minimum_point_separation
            ):
                native_deviation = _maximum_fit_deviation(
                    target_breaks,
                    native_nodes,
                    route_segments,
                    target_samples,
                )
                if best_deviation is None \
                        or native_deviation.error < best_deviation.error:
                    best_nodes = native_nodes
                    best_deviation = native_deviation
                if native_deviation.error <= tolerance:
                    return SplineFit(
                        tuple(native_nodes[1:-1]),
                        native_deviation.error,
                        tolerance,
                        start.curve_index,
                        branch,
                        score,
                    )
        next_measurement = min(max_interpolation_points, 4)
        while deviation.error > tolerance \
                and len(node_parameters) - 2 < max_interpolation_points:
            proxy = _parametric_fit_deviation(
                node_parameters, nodes, route_segments, target_samples
            )
            candidates = _fit_insertion_candidates(
                proxy.route_parameter,
                node_parameters,
            )
            evaluated: list[
                tuple[float, float, list[float], list[Point], _FitDeviation]
            ] = []
            for candidate in candidates:
                trial_parameters = sorted((*node_parameters, candidate))
                trial_nodes = _fit_route_nodes(
                    curve, start, end, delta, trial_parameters
                )
                if not _fit_nodes_are_separated(
                    trial_nodes, minimum_point_separation
                ):
                    continue
                trial_deviation = _parametric_fit_deviation(
                    trial_parameters,
                    trial_nodes,
                    route_segments,
                    target_samples,
                )
                evaluated.append((
                    trial_deviation.error,
                    candidate,
                    trial_parameters,
                    trial_nodes,
                    trial_deviation,
                ))
            if not evaluated:
                break
            _error, _candidate, node_parameters, nodes, deviation = min(
                evaluated,
                key=lambda item: (item[0], item[1]),
            )
            point_count = len(node_parameters) - 2
            if point_count >= next_measurement \
                    or point_count == max_interpolation_points:
                deviation = _maximum_fit_deviation(
                    node_parameters, nodes, route_segments, target_samples
                )
                uniform_parameters = [
                    index / (point_count + 1)
                    for index in range(point_count + 2)
                ]
                uniform_nodes = _fit_route_nodes(
                    curve, start, end, delta, uniform_parameters
                )
                measured_candidates = [(deviation, nodes)]
                if _fit_nodes_are_separated(
                    uniform_nodes, minimum_point_separation
                ):
                    uniform_deviation = _maximum_fit_deviation(
                        uniform_parameters,
                        uniform_nodes,
                        route_segments,
                        target_samples,
                    )
                    measured_candidates.append((
                        uniform_deviation, uniform_nodes
                    ))
                measured = min(
                    measured_candidates, key=lambda item: item[0].error
                )
                if best_deviation is None \
                        or measured[0].error < best_deviation.error:
                    best_deviation, best_nodes = measured
                if best_deviation is not None \
                        and best_deviation.error <= tolerance:
                    break
                next_measurement = min(
                    max_interpolation_points,
                    max(point_count + 1, point_count * 4),
                )
            else:
                # Retain the last measured geometric error as the stopping
                # condition while the cheap proxy ranks candidate insertions.
                deviation = _FitDeviation(
                    best_deviation.error
                    if best_deviation is not None else deviation.error,
                    proxy.route_parameter,
                )

        if best_nodes is None or best_deviation is None:
            raise ProjectionError(
                "The fitted curve section is too short to place a valid "
                "spline interpolation point"
            )
        return SplineFit(
            tuple(best_nodes[1:-1]),
            best_deviation.error,
            tolerance,
            start.curve_index,
            branch,
            score,
        )

    def _orthogonal_projections(
        self, point: Point
    ) -> tuple[ProjectionLocation, ...]:
        best_locations: list[ProjectionLocation] = []
        best_distance = math.inf
        for reference in self._segments:
            segment = reference.segment
            if _bounds_distance_squared(point, segment.bounds) \
                    > best_distance * best_distance:
                continue
            parameter = segment.closest_parameter(point)
            candidate = segment.point(parameter)
            distance = math.sqrt(_distance_squared(point, candidate))
            location = ProjectionLocation(
                candidate,
                reference.curve_index,
                reference.segment_index + parameter,
                distance,
            )
            comparison = _compare_projection_distances(
                distance, best_distance, point, candidate
            )
            if comparison < 0:
                best_locations = [location]
                best_distance = distance
            elif comparison == 0:
                _append_distinct_location(best_locations, location)
        assert best_locations
        return tuple(best_locations)

    def _axis_projections(
        self, point: Point, direction: str
    ) -> tuple[ProjectionLocation, ...]:
        fixed_index = 1 if direction == "x" else 0
        moving_index = 0 if direction == "x" else 1
        fixed_value = point[fixed_index]
        best_locations: list[ProjectionLocation] = []
        best_distance = math.inf
        for reference in self._segments:
            segment = reference.segment
            bounds = segment.bounds
            lower = bounds[fixed_index]
            upper = bounds[fixed_index + 2]
            coordinate_scale = max(1.0, abs(lower), abs(upper), abs(fixed_value))
            tolerance = _ROOT_TOLERANCE * coordinate_scale
            if fixed_value < lower - tolerance or fixed_value > upper + tolerance:
                continue

            fixed_polynomial = segment.y if fixed_index == 1 else segment.x
            shifted = (
                fixed_polynomial[0] - fixed_value,
                *fixed_polynomial[1:],
            )
            if max(abs(value) for value in shifted) <= tolerance:
                parameters = (segment.closest_parameter(point),)
            else:
                parameters = _roots_in_unit_interval(shifted)
            for parameter in parameters:
                evaluated = segment.point(parameter)
                candidate = (
                    (evaluated[0], point[1])
                    if direction == "x"
                    else (point[0], evaluated[1])
                )
                distance = abs(candidate[moving_index] - point[moving_index])
                location = ProjectionLocation(
                    candidate,
                    reference.curve_index,
                    reference.segment_index + parameter,
                    distance,
                )
                comparison = _compare_projection_distances(
                    distance, best_distance, point, candidate
                )
                if comparison < 0:
                    best_locations = [location]
                    best_distance = distance
                elif comparison == 0:
                    _append_distinct_location(best_locations, location)

        if not best_locations:
            axis = "horizontal" if direction == "x" else "vertical"
            raise ProjectionError(
                f"No selected reference curve intersects the {axis} "
                "projection line through this point"
            )
        return tuple(best_locations)


def _fit_route_segments(
    curve: _ReferenceCurve,
    route_start: float,
    delta: float,
    breaks: Sequence[float],
) -> tuple[_RouteSegment, ...]:
    """Build the target's exact cubic pieces along a selected route."""
    result: list[_RouteSegment] = []
    for first, second in zip(breaks, breaks[1:]):
        x, y = curve.interval_coefficients(
            route_start + delta * first,
            route_start + delta * second,
        )
        result.append(_RouteSegment(
            first,
            second,
            _CubicSegment.from_coefficients(x, y),
        ))
    return tuple(result)


def _fit_target_samples(
    segments: Sequence[_RouteSegment],
) -> tuple[tuple[float, Point], ...]:
    """Return deterministic samples for the geometric minimax objective."""
    result: list[tuple[float, Point]] = []
    for segment_index, segment in enumerate(segments):
        first_sample = 0 if segment_index == 0 else 1
        for sample in range(first_sample, FIT_ERROR_SAMPLES_PER_SPAN + 1):
            local = sample / FIT_ERROR_SAMPLES_PER_SPAN
            route_parameter = (
                segment.start
                + local * (segment.end - segment.start)
            )
            result.append((route_parameter, segment.cubic.point(local)))
    return tuple(result)


def _fit_initial_parameter(
    target_samples: Sequence[tuple[float, Point]],
) -> float:
    """Choose the first point at the route's greatest straight-chord error."""
    start = target_samples[0][1]
    end = target_samples[-1][1]
    candidates = target_samples[1:-1]
    if not candidates:
        return 0.5
    parameter, _point = max(
        candidates,
        key=lambda item: (
            _point_to_line_segment_distance_squared(item[1], start, end),
            -abs(item[0] - 0.5),
        ),
    )
    if not _PARAMETER_TOLERANCE < parameter < 1.0 - _PARAMETER_TOLERANCE:
        return 0.5
    return parameter


def _fit_route_nodes(
    curve: _ReferenceCurve,
    start: ProjectionLocation,
    end: ProjectionLocation,
    delta: float,
    parameters: Sequence[float],
) -> list[Point]:
    """Evaluate ordered interpolation nodes, retaining projected endpoints."""
    return [
        start.point if index == 0
        else end.point if index == len(parameters) - 1
        else curve.point(start.span_parameter + delta * parameter)
        for index, parameter in enumerate(parameters)
    ]


def _fit_nodes_are_separated(
    nodes: Sequence[Point],
    minimum_separation: float,
) -> bool:
    return all(
        math.dist(first, second) > minimum_separation
        for first, second in zip(nodes, nodes[1:])
    )


def _parametric_fit_deviation(
    node_parameters: Sequence[float],
    nodes: Sequence[Point],
    target_segments: Sequence[_RouteSegment],
    target_samples: Sequence[tuple[float, Point]],
) -> _FitDeviation:
    """Return a fast correspondence-based proxy for candidate ranking.

    The final reported error uses geometric closest-point distances. This proxy
    deliberately avoids root solving so hundreds of one-at-a-time candidate
    insertions remain interactive.
    """
    fitted = tuple(
        _CubicSegment.from_path(nodes, index)
        for index in range(len(nodes) - 1)
    )
    target_ends = tuple(segment.end for segment in target_segments)
    worst = _FitDeviation(0.0, 0.5)

    for route_parameter, target_point in target_samples:
        fitted_index = min(
            len(fitted) - 1,
            max(0, bisect.bisect_right(
                node_parameters, route_parameter
            ) - 1),
        )
        first = node_parameters[fitted_index]
        second = node_parameters[fitted_index + 1]
        local = (route_parameter - first) / (second - first)
        distance = math.dist(target_point, fitted[fitted_index].point(local))
        if distance > worst.error:
            worst = _FitDeviation(distance, route_parameter)

    for fitted_index, fitted_segment in enumerate(fitted):
        first = node_parameters[fitted_index]
        second = node_parameters[fitted_index + 1]
        for sample in range(1, FIT_ERROR_SAMPLES_PER_SPAN):
            local = sample / FIT_ERROR_SAMPLES_PER_SPAN
            route_parameter = first + local * (second - first)
            target_index = min(
                len(target_segments) - 1,
                bisect.bisect_left(target_ends, route_parameter),
            )
            target = target_segments[target_index]
            target_local = (
                (route_parameter - target.start)
                / (target.end - target.start)
            )
            distance = math.dist(
                fitted_segment.point(local),
                target.cubic.point(target_local),
            )
            if distance > worst.error:
                worst = _FitDeviation(distance, route_parameter)
    return worst


def _maximum_fit_deviation(
    node_parameters: Sequence[float],
    nodes: Sequence[Point],
    target_segments: Sequence[_RouteSegment],
    target_samples: Sequence[tuple[float, Point]],
) -> _FitDeviation:
    """Estimate the symmetric geometric distance between two ordered curves.

    Point-to-cubic distances are solved exactly. Sampling is performed in both
    directions, which catches both a target section missed by the fit and a
    fitted spline that overshoots away from the target. Only the corresponding
    span and its immediate neighbours are considered so a close, unrelated
    branch cannot hide an error on the selected route.
    """
    fitted = tuple(
        _CubicSegment.from_path(nodes, index)
        for index in range(len(nodes) - 1)
    )
    target_ends = tuple(segment.end for segment in target_segments)
    worst = _FitDeviation(0.0, 0.5)

    for route_parameter, point in target_samples:
        fitted_index = min(
            len(fitted) - 1,
            max(0, bisect.bisect_right(
                node_parameters, route_parameter
            ) - 1),
        )
        closest = fitted[fitted_index].point(
            fitted[fitted_index].closest_parameter(point)
        )
        distance = math.dist(point, closest)
        for index in (fitted_index - 1, fitted_index + 1):
            if not 0 <= index < len(fitted) \
                    or _bounds_distance_squared(
                        point, fitted[index].bounds
                    ) > distance * distance:
                continue
            candidate = fitted[index].point(
                fitted[index].closest_parameter(point)
            )
            distance = min(distance, math.dist(point, candidate))
        if distance > worst.error:
            worst = _FitDeviation(distance, route_parameter)

    for fitted_index, fitted_segment in enumerate(fitted):
        first_parameter = node_parameters[fitted_index]
        second_parameter = node_parameters[fitted_index + 1]
        for sample in range(1, FIT_ERROR_SAMPLES_PER_SPAN):
            local = sample / FIT_ERROR_SAMPLES_PER_SPAN
            point = fitted_segment.point(local)
            route_parameter = (
                first_parameter
                + local * (second_parameter - first_parameter)
            )
            target_index = min(
                len(target_segments) - 1,
                bisect.bisect_left(target_ends, route_parameter),
            )
            best_distance = math.inf
            best_route_parameter = route_parameter
            candidate_indices = [target_index]
            candidate_indices.extend(
                index
                for index in (target_index - 1, target_index + 1)
                if 0 <= index < len(target_segments)
            )
            for index in candidate_indices:
                target = target_segments[index]
                if _bounds_distance_squared(point, target.cubic.bounds) \
                        > best_distance * best_distance:
                    continue
                target_local = target.cubic.closest_parameter(point)
                distance = math.dist(
                    point, target.cubic.point(target_local)
                )
                if distance < best_distance:
                    best_distance = distance
                    best_route_parameter = (
                        target.start
                        + target_local * (target.end - target.start)
                    )
            if best_distance > worst.error:
                worst = _FitDeviation(
                    best_distance, best_route_parameter
                )
    return worst


def _fit_insertion_candidates(
    worst_parameter: float,
    node_parameters: Sequence[float],
) -> tuple[float, ...]:
    """Offer local point placements and let the minimax objective choose."""
    node_index = min(
        len(node_parameters) - 2,
        max(0, bisect.bisect_right(
            node_parameters, worst_parameter
        ) - 1),
    )
    left = node_parameters[node_index]
    right = node_parameters[node_index + 1]
    values = [
        worst_parameter,
        (left + right) / 2.0,
    ]
    # Catmull-Rom tangents couple neighbouring spans. Supplying a few global
    # alternatives prevents a locally bad insertion from repeatedly crowding
    # the current worst location while leaving the rest of the route sparse.
    largest_gap = max(
        zip(node_parameters, node_parameters[1:]),
        key=lambda interval: interval[1] - interval[0],
    )
    values.append((largest_gap[0] + largest_gap[1]) / 2.0)

    result: list[float] = []
    for value in sorted(values):
        if not _PARAMETER_TOLERANCE < value < 1.0 - _PARAMETER_TOLERANCE:
            continue
        if any(
            abs(value - existing) <= _PARAMETER_TOLERANCE
            for existing in node_parameters
        ):
            continue
        if not result or abs(value - result[-1]) > _PARAMETER_TOLERANCE:
            result.append(value)
    return tuple(result)


def _catmull_rom_coefficients(
    before: float,
    first: float,
    second: float,
    after: float,
) -> tuple[float, float, float, float]:
    return (
        first,
        0.5 * (-before + second),
        0.5 * (2.0 * before - 5.0 * first + 4.0 * second - after),
        0.5 * (-before + 3.0 * first - 3.0 * second + after),
    )


def _polynomial_value(coefficients: Polynomial, value: float) -> float:
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def _polynomial_derivative(coefficients: Polynomial) -> Polynomial:
    return tuple(
        index * coefficient
        for index, coefficient in enumerate(coefficients)
        if index > 0
    ) or (0.0,)


def _polynomial_add(first: Polynomial, second: Polynomial) -> Polynomial:
    length = max(len(first), len(second))
    return tuple(
        (first[index] if index < len(first) else 0.0)
        + (second[index] if index < len(second) else 0.0)
        for index in range(length)
    )


def _polynomial_subtract(first: Polynomial, second: Polynomial) -> Polynomial:
    length = max(len(first), len(second))
    return tuple(
        (first[index] if index < len(first) else 0.0)
        - (second[index] if index < len(second) else 0.0)
        for index in range(length)
    )


def _polynomial_multiply(first: Polynomial, second: Polynomial) -> Polynomial:
    result = [0.0] * (len(first) + len(second) - 1)
    for first_index, first_value in enumerate(first):
        for second_index, second_value in enumerate(second):
            result[first_index + second_index] += first_value * second_value
    return tuple(result)


def _polynomial_affine(
    coefficients: Polynomial,
    offset: float,
    scale: float,
) -> tuple[float, float, float, float]:
    """Compose a cubic polynomial with ``offset + scale * parameter``."""
    padded = tuple(coefficients) + (0.0,) * (4 - len(coefficients))
    constant, linear, quadratic, cubic = padded[:4]
    return (
        constant
        + linear * offset
        + quadratic * offset * offset
        + cubic * offset * offset * offset,
        scale
        * (
            linear
            + 2.0 * quadratic * offset
            + 3.0 * cubic * offset * offset
        ),
        scale * scale * (quadratic + 3.0 * cubic * offset),
        scale * scale * scale * cubic,
    )


def _maximum_curve_difference(
    target_x: Polynomial,
    target_y: Polynomial,
    fitted_x: Polynomial,
    fitted_y: Polynomial,
) -> tuple[float, float]:
    """Return the exact maximum same-parameter distance of two cubics."""
    difference_x = _polynomial_subtract(target_x, fitted_x)
    difference_y = _polynomial_subtract(target_y, fitted_y)
    distance_squared = _polynomial_add(
        _polynomial_multiply(difference_x, difference_x),
        _polynomial_multiply(difference_y, difference_y),
    )
    candidates = (
        0.0,
        1.0,
        *_roots_in_unit_interval(_polynomial_derivative(distance_squared)),
    )
    parameter = max(
        candidates,
        key=lambda value: _polynomial_value(distance_squared, value),
    )
    squared_error = max(
        0.0, _polynomial_value(distance_squared, parameter)
    )
    return math.sqrt(squared_error), parameter


def _point_to_polynomial_curve_distance_squared(
    x_coefficients: Polynomial,
    y_coefficients: Polynomial,
    point: Point,
) -> float:
    shifted_x = (x_coefficients[0] - point[0], *x_coefficients[1:])
    shifted_y = (y_coefficients[0] - point[1], *y_coefficients[1:])
    distance_squared = _polynomial_add(
        _polynomial_multiply(shifted_x, shifted_x),
        _polynomial_multiply(shifted_y, shifted_y),
    )
    candidates = (
        0.0,
        1.0,
        *_roots_in_unit_interval(_polynomial_derivative(distance_squared)),
    )
    return min(
        max(0.0, _polynomial_value(distance_squared, parameter))
        for parameter in candidates
    )


def _roots_in_unit_interval(coefficients: Polynomial) -> tuple[float, ...]:
    """Isolate all real roots on [0, 1] using derivative monotonic spans."""
    scale = max((abs(value) for value in coefficients), default=0.0)
    if scale == 0.0:
        return ()
    normalized = [value / scale for value in coefficients]
    while len(normalized) > 1 and abs(normalized[-1]) <= 1.0e-14:
        normalized.pop()
    if len(normalized) == 1:
        return ()
    if len(normalized) == 2:
        root = -normalized[0] / normalized[1]
        if -_PARAMETER_TOLERANCE <= root <= 1.0 + _PARAMETER_TOLERANCE:
            return (min(1.0, max(0.0, root)),)
        return ()

    derivative = _polynomial_derivative(tuple(normalized))
    critical = _roots_in_unit_interval(derivative)
    knots = _deduplicated_parameters((0.0, *critical, 1.0))
    roots: list[float] = []
    values = [_polynomial_value(tuple(normalized), knot) for knot in knots]
    for knot, value in zip(knots, values):
        if abs(value) <= _ROOT_TOLERANCE:
            roots.append(knot)
    for left, right, left_value, right_value in zip(
        knots, knots[1:], values, values[1:]
    ):
        if left_value == 0.0 or right_value == 0.0 \
                or left_value * right_value >= 0.0:
            continue
        low = left
        high = right
        low_value = left_value
        for _iteration in range(64):
            middle = (low + high) / 2.0
            middle_value = _polynomial_value(tuple(normalized), middle)
            if abs(middle_value) <= _ROOT_TOLERANCE \
                    or high - low <= _PARAMETER_TOLERANCE:
                low = high = middle
                break
            if low_value * middle_value <= 0.0:
                high = middle
            else:
                low = middle
                low_value = middle_value
        roots.append((low + high) / 2.0)
    return _deduplicated_parameters(roots)


def _deduplicated_parameters(values: Iterable[float]) -> tuple[float, ...]:
    result: list[float] = []
    for value in sorted(min(1.0, max(0.0, item)) for item in values):
        if not result or abs(value - result[-1]) > _PARAMETER_TOLERANCE:
            result.append(value)
    return tuple(result)


def _route_break_parameters(start: float, delta: float) -> tuple[float, ...]:
    """Split a curve route wherever it crosses an original spline knot."""
    finish = start + delta
    lower = min(start, finish)
    upper = max(start, finish)
    values = [0.0, 1.0]
    first_integer = math.floor(lower) + 1
    last_integer = math.ceil(upper) - 1
    for knot in range(first_integer, last_integer + 1):
        parameter = (knot - start) / delta
        if _PARAMETER_TOLERANCE < parameter < 1.0 - _PARAMETER_TOLERANCE:
            values.append(parameter)
    return _deduplicated_parameters(values)


def _polynomial_range(coefficients: Polynomial) -> tuple[float, float]:
    candidates = (
        0.0,
        1.0,
        *_roots_in_unit_interval(_polynomial_derivative(coefficients)),
    )
    values = [_polynomial_value(coefficients, value) for value in candidates]
    return min(values), max(values)


def _distance_squared(first: Point, second: Point) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def _point_to_line_segment_distance_squared(
    point: Point,
    start: Point,
    end: Point,
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return _distance_squared(point, start)
    fraction = min(1.0, max(
        0.0,
        ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
        / length_squared,
    ))
    closest = start[0] + fraction * dx, start[1] + fraction * dy
    return _distance_squared(point, closest)


def _compare_projection_distances(
    candidate_distance: float,
    best_distance: float,
    source: Point,
    candidate: Point,
) -> int:
    """Compare distances while retaining numerically tied curve locations."""
    if math.isinf(best_distance):
        return -1
    coordinate_magnitude = max(
        abs(source[0]),
        abs(source[1]),
        abs(candidate[0]),
        abs(candidate[1]),
    )
    tolerance = max(
        _PROJECTION_TIE_ABSOLUTE_TOLERANCE,
        _PROJECTION_TIE_RELATIVE_TOLERANCE
        * max(1.0, candidate_distance, best_distance),
        8.0 * math.ulp(coordinate_magnitude),
    )
    if candidate_distance < best_distance - tolerance:
        return -1
    if abs(candidate_distance - best_distance) <= tolerance:
        return 0
    return 1


def _append_distinct_location(
    locations: list[ProjectionLocation],
    candidate: ProjectionLocation,
) -> None:
    if any(
        location.curve_index == candidate.curve_index
        and abs(location.span_parameter - candidate.span_parameter)
        <= _PARAMETER_TOLERANCE
        for location in locations
    ):
        return
    locations.append(candidate)


def _bounds_distance_squared(
    point: Point,
    bounds: tuple[float, float, float, float],
) -> float:
    min_x, min_y, max_x, max_y = bounds
    dx = max(min_x - point[0], 0.0, point[0] - max_x)
    dy = max(min_y - point[1], 0.0, point[1] - max_y)
    return dx * dx + dy * dy
