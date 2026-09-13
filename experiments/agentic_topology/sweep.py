"""Sweep / submapping construction for four-sided domains.

A four-sided domain is two opposite *guide* sides joined by two compatible
*end* sides.  A translational cyclic pair is one kind of compatible end pair;
an inlet and an outlet are another.  Nothing here looks at a chain's name: the
four corners are chosen from the domain's own chain joins and from high-turning
points, the assignment of guides and ends comes from compatibility and corner
geometry, and the sweep direction is whatever the geometry says it is.

The correspondence between the two guides is a monotone map found by bounded
relaxation of a dimensionless objective - rib orthogonality against both
guides, smooth column spacing, and attraction of a cut to the geometric feature
that created it.  It is initialised from normalised arc length and is checked
afterwards for monotonicity and for crossing ribs; a mapping that fails is
rejected with a structured reason rather than repaired by guesswork.

The result is a wall band along every guide that is a wall, an H-grid core
between the fronts, and shared cut identities through the whole stack, written
into the same ``patch_graph.PatchGraph`` the external path uses.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
import patch_graph as pg
import planar_domain as pdm


class SweepError(RuntimeError):
    """Raised when a four-sided construction cannot be completed."""


@dataclass(frozen=True)
class SweepOptions:
    corner_turning: float = 40.0
    section_turning: float = 35.0
    feature_attraction: float = 0.5
    orthogonality_weight: float = 1.0
    spacing_weight: float = 1.0e-3
    maximum_column_metric: float = 48.0
    maximum_columns: int = 48
    relax_sweeps: int = 24
    relax_grid: int = 9
    relax_refinements: int = 3
    margin: float = 0.02
    end_tolerance: float = 1.0e-6


# ---------------------------------------------------------------------------
# Sides
# ---------------------------------------------------------------------------


@dataclass
class Side:
    """One of the four sides: a contiguous run of boundary segments."""

    points: np.ndarray
    segment_labels: tuple[str, ...]

    @property
    def length(self) -> float:
        return g2.total_length(self.points)

    @property
    def names(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name in self.segment_labels:
            if not seen or seen[-1] != name:
                seen.append(name)
        return tuple(seen)

    def joins(self) -> list[float]:
        cumulative = g2.cumulative_length(self.points)
        result = []
        for index in range(1, len(self.segment_labels)):
            if self.segment_labels[index] != self.segment_labels[index - 1]:
                result.append(float(cumulative[index]))
        return result

    def label_at(self, station: float) -> str:
        cumulative = g2.cumulative_length(self.points)
        index = int(np.searchsorted(cumulative, float(station), side="right")) - 1
        index = min(max(index, 0), len(self.segment_labels) - 1)
        return self.segment_labels[index]

    def reversed(self) -> "Side":
        return Side(
            self.points[::-1].copy(), tuple(reversed(self.segment_labels))
        )


@dataclass
class FourSided:
    sides: tuple[Side, Side, Side, Side]
    guides: tuple[int, int]
    ends: tuple[int, int]
    periodic: bool
    translation: np.ndarray | None
    corner_deviation: float
    score: float

    @property
    def end_at_zero(self) -> Side:
        return self.sides[(self.guides[0] + 3) % 4]

    @property
    def end_at_one(self) -> Side:
        return self.sides[(self.guides[0] + 1) % 4]

    def described(self) -> dict:
        return {
            "guides": [list(self.sides[index].names) for index in self.guides],
            "ends": [list(self.sides[index].names) for index in self.ends],
            "periodic_ends": self.periodic,
            "translation": (
                None
                if self.translation is None
                else [float(self.translation[0]), float(self.translation[1])]
            ),
            "corner_deviation_degrees": self.corner_deviation,
            "score": self.score,
            "side_lengths": [side.length for side in self.sides],
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _loop_segments(domain: pdm.PlanarDomain):
    points: list[np.ndarray] = []
    labels: list[str] = []
    for chain in domain.outer.chains:
        for index in range(len(chain.points) - 1):
            points.append(chain.points[index])
            labels.append(chain.name)
    points.append(points[0])
    return np.asarray(points), labels


def _candidate_corners(points, labels, options: SweepOptions):
    turning = np.degrees(g2.turning_angles(points, closed=True))
    count = len(points) - 1
    joins = [
        index
        for index in range(count)
        if labels[index] != labels[(index - 1) % count]
    ]
    if not joins:
        joins = [0]
    extra = sorted(
        (
            (abs(float(turning[index])), index)
            for index in range(count)
            if index not in joins
            and abs(float(turning[index])) >= options.corner_turning
        ),
        reverse=True,
    )
    limit = max(0, 8 - len(joins))
    return sorted(joins + [index for _value, index in extra[:limit]]), turning


def _build_sides(points, labels, choice):
    count = len(points) - 1
    sides = []
    for order in range(4):
        start = choice[order]
        stop = choice[(order + 1) % 4]
        indices = [start]
        cursor = start
        while cursor != stop:
            cursor = (cursor + 1) % count
            indices.append(cursor)
        if len(indices) < 2:
            return None
        side_points = points[indices]
        if g2.total_length(side_points) <= 0.0:
            return None
        sides.append(
            Side(side_points, tuple(labels[index] for index in indices[:-1]))
        )
    return tuple(sides)


def _compatible_ends(domain: pdm.PlanarDomain, first: Side, second: Side):
    if len(first.names) == 1 and len(second.names) == 1:
        one = domain.chain(first.names[0])
        other = domain.chain(second.names[0])
        if one.role == "cyclic" and one.neighbour == other.name:
            return True, True, domain.translation(one.name), "reciprocal cyclic pair"
    walls = sorted(
        {
            name
            for name in (*first.names, *second.names)
            if domain.chain(name).is_wall
        }
    )
    if walls:
        return False, False, None, f"end sides contain wall chains {walls}"
    return True, False, None, "non-wall end chains"


def detect(
    domain: pdm.PlanarDomain, options: SweepOptions | None = None
) -> tuple[FourSided | None, list[dict]]:
    """Find the best four-sided reading of a simply connected domain."""
    settings = options or SweepOptions()
    reasons: list[dict] = []
    if not domain.simply_connected:
        reasons.append(
            {
                "kind": "not_simply_connected",
                "holes": domain.hole_count,
                "detail": "a sweepable domain has no interior hole",
            }
        )
        return None, reasons
    points, labels = _loop_segments(domain)
    candidates, turning = _candidate_corners(points, labels, settings)
    if len(candidates) < 4:
        reasons.append(
            {
                "kind": "too_few_corners",
                "candidates": len(candidates),
                "detail": "fewer than four chain joins or sharp turning points",
            }
        )
        return None, reasons
    best: FourSided | None = None
    for choice in itertools.combinations(candidates, 4):
        sides = _build_sides(points, labels, choice)
        if sides is None:
            continue
        deviation = float(
            np.mean([abs(abs(float(turning[index])) - 90.0) for index in choice])
        )
        for guides, ends in (((0, 2), (1, 3)), ((1, 3), (0, 2))):
            compatible, periodic, translation, reason = _compatible_ends(
                domain, sides[ends[0]], sides[ends[1]]
            )
            if not compatible:
                record = {
                    "kind": "incompatible_ends",
                    "ends": [list(sides[ends[0]].names), list(sides[ends[1]].names)],
                    "detail": reason,
                }
                if record not in reasons:
                    reasons.append(record)
                continue
            end_length = sides[ends[0]].length + sides[ends[1]].length
            guide_length = sides[guides[0]].length + sides[guides[1]].length
            score = deviation + 20.0 * end_length / max(guide_length, 1.0e-12)
            if not periodic:
                score += 5.0
            candidate = FourSided(
                sides, guides, ends, periodic, translation, deviation, score
            )
            if best is None or candidate.score < best.score:
                best = candidate
    if best is None:
        reasons.append(
            {
                "kind": "no_four_sided_reading",
                "detail": "no choice of four corners gave a compatible end pair",
            }
        )
    return best, reasons


# ---------------------------------------------------------------------------
# Correspondence
# ---------------------------------------------------------------------------


@dataclass
class Correspondence:
    first: np.ndarray
    second: np.ndarray
    features: np.ndarray
    monotone: bool = True
    crossing: list[int] = field(default_factory=list)
    objective: float = 0.0
    sweeps: int = 0

    @property
    def columns(self) -> int:
        return len(self.first) - 1

    def described(self) -> dict:
        return {
            "columns": self.columns,
            "monotone": self.monotone,
            "crossing_columns": list(self.crossing),
            "objective": self.objective,
            "relaxation_sweeps": self.sweeps,
            "first_fractions": [
                float(value / max(self.first[-1], 1e-30)) for value in self.first
            ],
            "second_fractions": [
                float(value / max(self.second[-1], 1e-30)) for value in self.second
            ],
        }


def _tangent(points: np.ndarray, cumulative: np.ndarray, station: float) -> np.ndarray:
    total = float(cumulative[-1])
    value = min(max(float(station), 0.0), total)
    index = int(np.searchsorted(cumulative, value, side="right")) - 1
    index = min(max(index, 0), len(points) - 2)
    delta = points[index + 1] - points[index]
    norm = float(np.linalg.norm(delta))
    return delta / norm if norm > 0.0 else np.array([1.0, 0.0])


def section_features(points: np.ndarray, options: SweepOptions) -> list[float]:
    """Stations where the accumulated turning of a guide exceeds the limit."""
    if len(points) < 3:
        return []
    turning = np.abs(np.degrees(g2.turning_angles(points, closed=False)))
    cumulative = g2.cumulative_length(points)
    if float(np.sum(turning)) <= options.section_turning:
        return []
    accumulated = 0.0
    result = []
    for index, value in enumerate(turning):
        accumulated += float(value)
        if accumulated >= options.section_turning:
            result.append(float(cumulative[index + 1]))
            accumulated = 0.0
    return result


def initial_cuts(
    first: np.ndarray,
    second: np.ndarray,
    metric,
    options: SweepOptions,
    mandatory_first=(),
    mandatory_second=(),
) -> Correspondence:
    """Propose monotone cut fractions from features, joins and metric length."""
    length_first = g2.total_length(first)
    length_second = g2.total_length(second)
    proposals = [
        float(station) / length_first
        for station in (*section_features(first, options), *mandatory_first)
    ]
    proposals.extend(
        float(station) / length_second
        for station in (*section_features(second, options), *mandatory_second)
    )
    separation = 1.0 / max(options.maximum_columns, 1)
    merged: list[float] = []
    for value in sorted(proposals):
        if value <= separation or value >= 1.0 - separation:
            continue
        if merged and value - merged[-1] < separation:
            continue
        merged.append(value)
    stations = _enforce_metric_limit(
        first, second, [0.0, *merged, 1.0], metric, options
    )
    feature = np.asarray(stations, dtype=np.float64)
    return Correspondence(
        feature * length_first, feature * length_second, feature.copy()
    )


def _enforce_metric_limit(first, second, stations, metric, options: SweepOptions):
    length_first = g2.total_length(first)
    length_second = g2.total_length(second)
    result = list(stations)
    for _pass in range(6):
        if len(result) - 1 >= options.maximum_columns:
            break
        extended = [result[0]]
        changed = False
        for start, stop in zip(result, result[1:]):
            section = g2.polyline_section(
                first, start * length_first, stop * length_first
            )
            other = g2.polyline_section(
                second, start * length_second, stop * length_second
            )
            worst = max(metric.metric_length(section), metric.metric_length(other))
            if worst > options.maximum_column_metric:
                extended.append(0.5 * (start + stop))
                changed = True
            extended.append(stop)
        result = extended
        if not changed:
            break
    return result


def relax_correspondence(
    first: np.ndarray,
    second: np.ndarray,
    correspondence: Correspondence,
    options: SweepOptions,
) -> Correspondence:
    """Slide the interior cuts so ribs meet both guides as squarely as possible."""
    length_first = g2.total_length(first)
    length_second = g2.total_length(second)
    cumulative_first = g2.cumulative_length(first)
    cumulative_second = g2.cumulative_length(second)
    values_first = np.array(correspondence.first, dtype=np.float64)
    values_second = np.array(correspondence.second, dtype=np.float64)
    count = len(values_first)

    def rib_cost(index: int) -> float:
        start = g2.sample_at_arclength(first, [values_first[index]])[0]
        stop = g2.sample_at_arclength(second, [values_second[index]])[0]
        direction = stop - start
        norm = float(np.linalg.norm(direction))
        if norm <= 0.0:
            return 1.0e6
        unit = direction / norm
        one = _tangent(first, cumulative_first, values_first[index])
        other = _tangent(second, cumulative_second, values_second[index])
        cost = options.orthogonality_weight * (
            float(np.dot(unit, one)) ** 2 + float(np.dot(unit, other)) ** 2
        )
        target = correspondence.features[index]
        cost += options.feature_attraction * (
            (values_first[index] / length_first - target) ** 2
            + (values_second[index] / length_second - target) ** 2
        )
        return cost

    def spacing_cost(index: int) -> float:
        cost = 0.0
        for values, total in (
            (values_first, length_first),
            (values_second, length_second),
        ):
            for low in (index - 1, index):
                if low < 0 or low + 1 >= count:
                    continue
                span = (values[low + 1] - values[low]) / total
                if span <= 0.0:
                    return 1.0e6
                cost += options.spacing_weight / span
        return cost

    def local(index: int) -> float:
        return rib_cost(index) + spacing_cost(index)

    sweeps = 0
    for sweeps in range(1, options.relax_sweeps + 1):
        moved = 0.0
        for index in range(1, count - 1):
            for values, total in (
                (values_first, length_first),
                (values_second, length_second),
            ):
                low = values[index - 1] + options.margin * total
                high = values[index + 1] - options.margin * total
                if high <= low:
                    continue
                best = float(values[index])
                best_cost = local(index)
                window = (low, high)
                for _refine in range(options.relax_refinements):
                    for candidate in np.linspace(
                        window[0], window[1], options.relax_grid
                    ):
                        values[index] = float(candidate)
                        cost = local(index)
                        if cost < best_cost - 1.0e-15:
                            best_cost = cost
                            best = float(candidate)
                    width = (window[1] - window[0]) / (options.relax_grid - 1)
                    window = (max(low, best - width), min(high, best + width))
                    if window[1] <= window[0]:
                        break
                moved += abs(float(values[index]) - best)
                values[index] = best
        if moved <= 1.0e-12 * (length_first + length_second):
            break
    monotone = bool(
        np.all(np.diff(values_first) > 0.0) and np.all(np.diff(values_second) > 0.0)
    )
    crossing = []
    for index in range(count - 1):
        quad = np.asarray(
            [
                g2.sample_at_arclength(first, [values_first[index]])[0],
                g2.sample_at_arclength(first, [values_first[index + 1]])[0],
                g2.sample_at_arclength(second, [values_second[index + 1]])[0],
                g2.sample_at_arclength(second, [values_second[index]])[0],
            ]
        )
        if not g2.strictly_convex(quad):
            crossing.append(index)
    objective = float(sum(rib_cost(index) for index in range(count)))
    return Correspondence(
        values_first,
        values_second,
        correspondence.features,
        monotone,
        crossing,
        objective,
        sweeps,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def insert_open_stations(points: np.ndarray, stations) -> tuple[np.ndarray, list[int]]:
    """Open-curve wrapper around :func:`layers.insert_stations`."""
    return layer_module.insert_stations(points, stations, closed=False)


@dataclass
class Row:
    """One curve of the sweep stack, sampled at every cut station."""

    name: str
    role: str
    points: np.ndarray
    indices: list[int]
    boundary: Side | None = None


@dataclass
class SweepResult:
    graph: pg.PatchGraph
    four_sided: FourSided
    correspondence: Correspondence
    rows: list[Row]
    layer_faces: set
    notes: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _guide_front(
    points: np.ndarray,
    side: Side,
    domain: pdm.PlanarDomain,
    metric,
    options: layer_module.LayerOptions,
    obstacles,
    *,
    fluid_left: bool,
    protected=(),
):
    """Clearance-limited offset of one open guide, sampled like the guide itself."""
    if not any(domain.chain(name).is_wall for name in side.names):
        return None, []
    sign = 1.0 if fluid_left else -1.0
    normals = sign * g2.vertex_normals(points, closed=False)
    walls = [chain.points for chain in domain.wall_chains()]
    upper = metric.layer_height / max(options.clearance_fraction, 1.0e-6)
    feature = layer_module.local_feature_size(
        walls, points, normals, upper=upper, steps=options.feature_steps
    )
    inner = layer_module.local_feature_size(
        walls, points, -normals, upper=upper, steps=options.feature_steps
    )
    target = np.minimum(metric.layer_height, options.clearance_fraction * feature)
    floor = options.minimum_band_cells * metric.first
    target = np.minimum(
        target, np.maximum(options.curvature_fraction * inner, floor)
    )
    arclength = g2.cumulative_length(points)
    notes: list[str] = []
    shrink = 1.0
    for attempt in range(options.shrink_attempts + 1):
        heights = layer_module.slope_limited(
            target * shrink, arclength, options.slope_limit, closed=False
        )
        offset = g2.offset_polyline(
            points, sign * heights, closed=False, limit=options.miter_limit
        )
        offset, stuck = layer_module.flatten_offset_loops(
            points, offset, protected, closed=False
        )
        usable = (
            not stuck
            and g2.is_simple(offset, closed=False)
            and not g2.paths_cross(offset, points)
        )
        for other in obstacles:
            usable = usable and not g2.paths_cross(offset, other)
        if usable:
            if attempt:
                notes.append(
                    f"guide front for {list(side.names)} reduced to {shrink:.3f} "
                    f"of the requested height so it stays simple"
                )
            return offset, notes
        shrink *= options.shrink_factor
    return None, [
        f"no admissible guide front for {list(side.names)}; the offset folds"
    ]


def _project_end(point: np.ndarray, side: Side) -> tuple[np.ndarray, float]:
    result = g2.closest_on_polyline(side.points, [point])
    return result.point[0], float(result.arclength[0])


def build(
    domain: pdm.PlanarDomain,
    four: FourSided,
    metric,
    *,
    options: SweepOptions | None = None,
    layer_options: layer_module.LayerOptions | None = None,
) -> SweepResult:
    """Build the band/core patch graph of a detected four-sided domain."""
    settings = options or SweepOptions()
    layer_settings = layer_options or layer_module.LayerOptions()
    guide_a = four.sides[four.guides[0]]
    guide_b = four.sides[four.guides[1]]
    end_zero = four.end_at_zero
    end_one = four.end_at_one
    notes: list[str] = []
    failures: list[dict] = []

    first = guide_a.points
    second_side = guide_b.reversed()
    second = second_side.points
    correspondence = initial_cuts(
        first,
        second,
        metric,
        settings,
        mandatory_first=guide_a.joins(),
        mandatory_second=second_side.joins(),
    )
    correspondence = relax_correspondence(first, second, correspondence, settings)
    if not correspondence.monotone:
        failures.append(
            {
                "stage": "sweep_correspondence",
                "reason": "the relaxed guide correspondence is not monotone",
                "first": [float(value) for value in correspondence.first],
                "second": [float(value) for value in correspondence.second],
            }
        )
    if correspondence.crossing:
        failures.append(
            {
                "stage": "sweep_correspondence",
                "reason": "ribs cross or invert in these columns",
                "columns": list(correspondence.crossing),
            }
        )

    # Cut stations become vertices of the guide before it is offset, so a front
    # vertex is exactly the offset of its guide vertex and the rib is normal.
    wall_a, index_a = insert_open_stations(first, correspondence.first)
    wall_b, index_b = insert_open_stations(second, correspondence.second)
    front_a = front_b = None
    if layer_settings.enabled:
        front_a, front_notes = _guide_front(
            wall_a,
            guide_a,
            domain,
            metric,
            layer_settings,
            [],
            fluid_left=True,
            protected=index_a,
        )
        notes.extend(front_notes)
        front_b, front_notes = _guide_front(
            wall_b,
            second_side,
            domain,
            metric,
            layer_settings,
            [front_a] if front_a is not None else [],
            fluid_left=False,
            protected=index_b,
        )
        notes.extend(front_notes)
    rows: list[Row] = [Row(guide_a.names[0], "wall", wall_a, index_a, guide_a)]
    if front_a is not None:
        rows.append(
            Row(f"front:{guide_a.names[0]}", "front", front_a, list(index_a))
        )
    if front_b is not None:
        rows.append(
            Row(f"front:{second_side.names[0]}", "front", front_b, list(index_b))
        )
    rows.append(Row(second_side.names[0], "wall", wall_b, index_b, second_side))

    graph = pg.PatchGraph(euler_characteristic=domain.euler_characteristic)
    columns = correspondence.columns
    keys: list[list[tuple]] = []
    for row_index, row in enumerate(rows):
        row_keys = []
        cumulative = g2.cumulative_length(row.points)
        for column in range(columns + 1):
            point = row.points[row.indices[column]]
            station = float(cumulative[row.indices[column]])
            if row.boundary is not None:
                constraint = pg.Constraint(
                    "chain", row.boundary.label_at(station), station
                )
            else:
                constraint = pg.Constraint("guide", row.name, station)
            key = ("sweep", row_index, column)
            graph.add_vertex(key, point, constraint=constraint, provenance=row.role)
            row_keys.append(key)
        keys.append(row_keys)

    end_stations = _end_stations(
        graph, keys, rows, end_zero, end_one, failures, settings
    )
    if four.periodic:
        failures.extend(
            _enforce_periodic_ends(graph, keys, rows, domain, end_zero, end_one)
        )

    layer_faces: set = set()
    for row_index, row in enumerate(rows[:-1]):
        upper = rows[row_index + 1]
        roles = {row.role, upper.role}
        role = "layer" if roles == {"wall", "front"} else "core"
        for column in range(columns):
            lower_path = row.points[
                row.indices[column] : row.indices[column + 1] + 1
            ]
            upper_path = upper.points[
                upper.indices[column] : upper.indices[column + 1] + 1
            ]
            _add_horizontal(graph, keys[row_index], row, column, lower_path)
            _add_horizontal(graph, keys[row_index + 1], upper, column, upper_path)
            for side_column in (column, column + 1):
                _add_rib(
                    graph,
                    keys,
                    row_index,
                    side_column,
                    columns,
                    end_zero,
                    end_one,
                    end_stations,
                )
            face = graph.add_face(
                (
                    keys[row_index][column],
                    keys[row_index][column + 1],
                    keys[row_index + 1][column + 1],
                    keys[row_index + 1][column],
                ),
                role=role,
                provenance="sweep",
            )
            if role == "layer":
                layer_faces.add(face.key)
    return SweepResult(
        graph, four, correspondence, rows, layer_faces, notes, failures
    )


def _add_horizontal(graph, row_keys, row: Row, column: int, path: np.ndarray) -> None:
    boundary = None
    kind = "line"
    points: tuple = ()
    if row.boundary is not None:
        cumulative = g2.cumulative_length(row.points)
        boundary = row.boundary.label_at(float(cumulative[row.indices[column]]))
    interior = path[1:-1]
    if len(interior):
        kind = "polyLine"
        points = tuple((float(x), float(y)) for x, y in interior)
    graph.add_edge(
        row_keys[column],
        row_keys[column + 1],
        path=path,
        kind=kind,
        points=points,
        boundary=boundary,
        role="wall" if row.role == "wall" else "front",
        provenance="sweep guide" if row.role == "wall" else "layer front",
    )


def _add_rib(
    graph, keys, row_index, column, columns, end_zero, end_one, end_stations
) -> None:
    lower = keys[row_index][column]
    upper = keys[row_index + 1][column]
    start = graph.vertices[lower].point
    stop = graph.vertices[upper].point
    boundary = None
    path = np.asarray([start, stop])
    kind = "line"
    points: tuple = ()
    if column in (0, columns):
        side = end_zero if column == 0 else end_one
        stations = end_stations[column]
        section = g2.polyline_section(
            side.points, stations[row_index], stations[row_index + 1]
        )
        if len(section) >= 2:
            if float(np.linalg.norm(section[0] - start)) > float(
                np.linalg.norm(section[-1] - start)
            ):
                section = section[::-1].copy()
            section = section.copy()
            section[0] = start
            section[-1] = stop
            path = section
            interior = path[1:-1]
            if len(interior):
                kind = "polyLine"
                points = tuple((float(x), float(y)) for x, y in interior)
        boundary = side.label_at(
            0.5 * (stations[row_index] + stations[row_index + 1])
        )
    graph.add_edge(
        lower,
        upper,
        path=path,
        kind=kind,
        points=points,
        boundary=boundary,
        role="layer_spoke"
        if _is_layer_rib(graph, keys, row_index, column)
        else "core_rib",
        provenance="sweep rib",
    )


def _is_layer_rib(graph, keys, row_index, column) -> bool:
    lower = graph.vertices[keys[row_index][column]].provenance
    upper = graph.vertices[keys[row_index + 1][column]].provenance
    return "wall" in (lower, upper) and "front" in (lower, upper)


def _end_stations(graph, keys, rows, end_zero, end_one, failures, settings):
    """Put every end rib vertex on its end chain and check the ordering.

    A wall guide already ends on the end chain, but a layer front only does when
    the wall meets the end squarely.  Projecting is the honest fix: the vertex
    moves onto the boundary and the row geometry follows it, so the emitted
    boundary edges really do lie on the supplied chain.
    """
    result = {}
    columns = len(keys[0]) - 1
    for column, side in ((0, end_zero), (columns, end_one)):
        stations = []
        for row_index, row in enumerate(rows):
            key = keys[row_index][column]
            point = graph.vertices[key].point
            projected, station = _project_end(point, side)
            offset = float(np.linalg.norm(projected - point))
            if offset > 0.25 * max(side.length, 1.0e-30):
                failures.append(
                    {
                        "stage": "sweep_end_projection",
                        "reason": "an end rib vertex is far from its end chain",
                        "chain": list(side.names),
                        "offset": offset,
                        "row": row_index,
                    }
                )
            graph.vertices[key].point = projected
            row.points[row.indices[column]] = projected
            stations.append(station)
        if stations != sorted(stations) and stations != sorted(
            stations, reverse=True
        ):
            failures.append(
                {
                    "stage": "sweep_end_projection",
                    "reason": "end rib vertices are out of order along the end chain",
                    "chain": list(side.names),
                    "stations": [float(value) for value in stations],
                }
            )
        result[column] = stations
    return result


def _enforce_periodic_ends(graph, keys, rows, domain, end_zero, end_one):
    """Make the far end rib an exact translate of the near one."""
    failures = []
    columns = len(keys[0]) - 1
    translation = domain.translation(end_zero.names[0])
    for row_index, row in enumerate(rows):
        near = keys[row_index][0]
        far = keys[row_index][columns]
        wanted = graph.vertices[near].point + translation
        drift = float(np.linalg.norm(wanted - graph.vertices[far].point))
        if drift > 1.0e-3 * max(end_one.length, 1.0e-30):
            failures.append(
                {
                    "stage": "sweep_periodic",
                    "reason": "the two end ribs are not translates of each other",
                    "row": row_index,
                    "drift": drift,
                    "translation": [float(translation[0]), float(translation[1])],
                }
            )
        graph.vertices[far].point = wanted
        row.points[row.indices[columns]] = wanted
        graph.periodic_vertices[near] = far
        graph.periodic_vertices[far] = near
    return failures
