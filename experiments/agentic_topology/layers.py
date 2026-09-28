"""Boundary-layer fronts and the wall-feature fan.

A *front* is an explicit curve inside the fluid at a controlled distance from a
wall chain.  Once a front exists the wall band between the wall and the front is
ordinary structured topology, and everything further from the wall sees the
front - not the physical wall - as its boundary.  That is the whole point: the
near-wall region stops being whatever the interior decomposition happens to
produce.

The front is the boundary of the eroded fluid domain: the level set of the
wall distance at a height that varies slowly along the wall,

    height(s) = min(requested_height, clearance_fraction * far_clearance(s))

where the far clearance is the height at which the level set owned by ``s``
collapses against a wall part that is not locally adjacent - another body, or
a distant part of the same wall.  It is found by bisection on the wall distance
field with the wall's own neighbourhood excluded, so it is a property of the
geometry rather than of a raster, and it is finite at a concave corner, where
the plain tangent-disk feature size is zero because no disk is tangent there.
The per-vertex offset is then projected onto the level set, which turns the
shadow of a concave corner into the corner's mitre instead of a fold.  The
height is slope limited along the wall so the front cannot kink, and it is
shrunk globally when the front would still self-intersect or cross another
boundary.

At a sharp convex wall feature one band block would have to span the whole
fluid sector - a 352 degree cusp gives two 176 degree block corners no matter
where the gates slide.  A feature that wide therefore gets a *band seam* here:
one offset point per incident wall side instead of one on the bisector.  That is
only the starting construction; ``fan_cavity`` owns the feature afterwards and
may replace the whole neighbourhood with something better, so nothing in this
module judges - or abandons a chain because of - a block that touches such a
feature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import patch_graph as pg


# Fluid angle below which a wall vertex is a reflex corner rather than a
# smooth bend.  The tangent-disk probe accepts a disk when its wall distance is
# at least 0.98 of its radius, and a disk tangent at a vertex of fluid angle
# ``theta`` reaches ``sin(theta / 2)`` of its radius towards the adjacent edge,
# so below this angle no disk is tangent at all: the level set has a mitre
# there and the corner has to be a gate.  About 157 degrees.
REFLEX_FLUID_ANGLE = 2.0 * math.asin(0.98)


class LayerError(RuntimeError):
    """Raised when a requested boundary-layer front cannot be built.

    ``orders`` names the gate positions whose band block is inadmissible, so a
    caller can insert an anchor exactly there instead of guessing.
    """

    def __init__(self, message: str, *, name: str = "", orders=(), sharp: bool = False):
        super().__init__(message)
        self.name = name
        self.orders = tuple(int(value) for value in orders)
        # ``sharp`` marks a failure caused by a sharp wall feature.  Splitting a
        # patch cannot help there, so a caller must not try.
        self.sharp = bool(sharp)


@dataclass(frozen=True)
class LayerOptions:
    enabled: bool = True
    clearance_fraction: float = 0.35
    slope_limit: float = 0.35
    shrink_factor: float = 0.7
    shrink_attempts: int = 8
    minimum_fraction: float = 0.04
    minimum_band_quality: float = 0.02
    repair_band_quality: float = 0.26
    repair_budget: int = 12
    hard_attempts: int = 6
    # Local repair attempts before the global bisection, and its steps.
    local_attempts: int = 33
    bisection_steps: int = 10
    curvature_fraction: float = 0.8
    # Cap the band at ``curvature_fraction`` times the body's inscribed disk.
    # On a fat body that is its radius of curvature; on a thin airfoil it is
    # half the thickness and would forbid any realistic band, so a producer
    # that is handed the band height as an input switches it off and relies
    # on the block checks instead.
    inscribed_cap: bool = True
    # Optional: offset normals from the wall chord over this fraction of the
    # requested height instead of the plain vertex normals.  A level set at
    # height h does not see wiggles smaller than h, but a vertex normal of a
    # dense point list does, amplified by h.  Off by default: the window also
    # smooths real bends - a cove's band folded with it on - and the C-grid's
    # levels, judged between fronts after the arc-length mapping, do not need
    # it.
    normal_window: float = 0.0
    # Fraction of the distance to the core scaffold a front may reach.
    scaffold_fraction: float = 0.9
    # Arc-length window, in probe heights, within which the wall's own
    # segments do not count as a clearance limit: a corner or bend inside it
    # trims the level set into a mitre rather than collapsing it.
    shadow_window: float = 2.0
    miter_limit: float = 1.5
    # Floor on the band height, as a fraction of the domain scale: a geometric
    # input, so the block shapes do not follow the cell sizing.  ``None``
    # falls back to ``minimum_band_cells`` first-cell widths of the metric.
    # The default equals three first cells of the default sizing.
    floor_ratio: float | None = 0.006
    minimum_band_cells: float = 3.0
    repair_factor: float = 0.6
    feature_steps: int = 26
    # A wall vertex whose fluid sector is at least this wide is owned by the
    # cavity stage: the band blocks beside it are not judged here, because they
    # may be replaced.
    feature_fluid_angle: float = 210.0
    # And one at least this wide gets a band seam straight away, so the cavity
    # stage starts from a three-sector construction rather than a folded one.
    sharp_fluid_angle: float = 250.0


def floor_height(options: LayerOptions, metric) -> float:
    """The smallest band height a producer may request, a length."""
    if options.floor_ratio is not None:
        return float(options.floor_ratio) * float(metric.scale)
    return float(options.minimum_band_cells) * float(metric.first)


# ---------------------------------------------------------------------------
# Local feature size
# ---------------------------------------------------------------------------


def local_feature_size(
    wall_loops, points, normals, *, upper: float, steps: int = 26
) -> np.ndarray:
    """Radius of the largest inscribed disk tangent to the wall at each point.

    Only wall geometry limits the disk.  Inlet, outlet, symmetry and cyclic
    chains are artificial cuts through the fluid and must not shrink a boundary
    layer, which is exactly what would happen at the ends of a periodic domain.
    """
    query = np.atleast_2d(np.asarray(points, dtype=np.float64))
    direction = np.atleast_2d(np.asarray(normals, dtype=np.float64))
    low = np.zeros(len(query))
    high = np.full(len(query), float(upper))
    if not wall_loops:
        return high
    walls = [np.asarray(loop, dtype=np.float64) for loop in wall_loops]
    for _step in range(int(steps)):
        middle = 0.5 * (low + high)
        centres = query + middle[:, None] * direction
        best = np.full(len(query), math.inf)
        for wall in walls:
            best = np.minimum(best, g2.distance_to_polyline(wall, centres))
        fits = best >= 0.98 * middle
        low = np.where(fits, middle, low)
        high = np.where(fits, high, middle)
    return low


def _is_same_wall(loop: np.ndarray, wall: np.ndarray) -> bool:
    """True when ``loop`` and ``wall`` trace the same curve.

    The augmented wall carries the supplied loop's vertices plus inserted
    stations, so every vertex of either lies on the other.
    """
    tolerance = 1.0e-7 * max(g2.total_length(wall), 1.0e-300)
    if float(np.max(g2.distance_to_polyline(loop, wall[:-1]))) > tolerance:
        return False
    return float(np.max(g2.distance_to_polyline(wall, loop[:-1]))) <= tolerance


def other_walls(wall_loops, wall: np.ndarray) -> list[np.ndarray]:
    """Every wall loop except the one ``wall`` is an augmented copy of."""
    return [
        np.asarray(loop, dtype=np.float64)
        for loop in wall_loops
        if not _is_same_wall(np.asarray(loop, dtype=np.float64), wall)
    ]


def far_clearance(
    others,
    wall: np.ndarray,
    normals: np.ndarray,
    *,
    fluid_angles_at: np.ndarray,
    closed: bool,
    upper: float,
    window: float = 2.0,
    steps: int = 26,
    rounded=(),
) -> np.ndarray:
    """Height at which the level set owned by each wall vertex collapses.

    The plain tangent-disk feature size is zero at a reflex vertex and about
    the distance to the corner beside it, because the probe disk touches the
    wall's own adjacent edge.  That contact is the level set being *trimmed*
    by the corner's mitre, not collapsing: the front there is the mitre point,
    which is a perfectly good block vertex.  A collapse is a collision with a
    wall part that is not locally adjacent - another body, or a distant part
    of the same wall - so this probe ignores the wall's own segments within an
    arc-length window of ``window`` times the probe height around each vertex,
    and probes a reflex vertex at its mitre point (``height / sin(angle / 2)``
    along the bisector) instead of at the plain offset point.  For a smooth
    vertex far from any corner it is the tangent-disk feature size exactly.
    """
    points = np.asarray(wall, dtype=np.float64)
    direction = np.asarray(normals, dtype=np.float64)
    count = len(points)
    angles = np.asarray(fluid_angles_at, dtype=np.float64)
    half = 0.5 * np.clip(angles, 1.0e-6, math.pi)
    # A reflex vertex is probed at its mitre point; everywhere else the level
    # set point is the plain offset point.
    reach = np.where(angles < REFLEX_FLUID_ANGLE, 1.0 / np.sin(half), 1.0)
    stations = g2.cumulative_length(points)
    total = float(stations[-1])
    starts = points[:-1]
    vectors = points[1:] - points[:-1]
    lengths_squared = np.einsum("ij,ij->i", vectors, vectors)
    safe = np.where(lengths_squared > 0.0, lengths_squared, 1.0)
    middles = 0.5 * (stations[:-1] + stations[1:])
    half_lengths = 0.5 * np.sqrt(lengths_squared)
    separation = np.abs(stations[:, None] - middles[None, :])
    if closed:
        separation = np.minimum(separation, total - separation)
    others = [np.asarray(loop, dtype=np.float64) for loop in others]

    def own_distance(centres: np.ndarray, radius: np.ndarray) -> np.ndarray:
        offset_x = centres[:, None, 0] - starts[None, :, 0]
        offset_y = centres[:, None, 1] - starts[None, :, 1]
        fraction = (offset_x * vectors[None, :, 0] + offset_y * vectors[None, :, 1]) / safe[None, :]
        np.clip(fraction, 0.0, 1.0, out=fraction)
        offset_x -= fraction * vectors[None, :, 0]
        offset_y -= fraction * vectors[None, :, 1]
        distance = np.sqrt(offset_x * offset_x + offset_y * offset_y)
        excluded = separation <= (window * radius)[:, None] + half_lengths[None, :]
        distance[excluded] = math.inf
        return np.min(distance, axis=1)

    low = np.zeros(count)
    high = np.full(count, float(upper))
    for _step in range(int(steps)):
        middle = 0.5 * (low + high)
        centres = points + (middle * reach)[:, None] * direction
        best = own_distance(centres, middle)
        for loop in others:
            best = np.minimum(best, g2.distance_to_polyline(loop, centres))
        fits = best >= 0.98 * middle
        low = np.where(fits, middle, low)
        high = np.where(fits, high, middle)
    # The wall inside a rounded reflex corner's mitre shadow has no level-set
    # point of its own: its plain offset lands on the other flank, which the
    # probe reads as a collapse a fraction of the corner's clearance away.
    # Those vertices are the mitre's, so they carry the corner's clearance;
    # left at their own value, the slope limit would drag the corner's height
    # down to it - the cove's band thinned to a third at its apex.  Sharp
    # reflex corners keep their shadow's own values: in 30P30N's coves the
    # inherited clearance let the lip's band cross the next element's spokes.
    for index in sorted(set(int(item) for item in rounded)):
        if not angles[index] < REFLEX_FLUID_ANGLE:
            continue
        cot = 1.0 / math.tan(max(0.5 * float(angles[index]), 1.0e-3))
        shadow_reach = 1.5 * float(low[index]) * cot
        gaps = np.abs(stations[:count] - stations[index])
        if closed:
            gaps = np.minimum(gaps, total - gaps)
        shadow = gaps <= shadow_reach
        low = np.where(shadow, np.maximum(low, low[index]), low)
    return low


def trim_to_level_set(
    wall_loops,
    wall: np.ndarray,
    offset: np.ndarray,
    heights: np.ndarray,
    normals: np.ndarray,
    fluid_angles_at: np.ndarray,
    *,
    closed: bool,
    tolerance: float = 0.98,
    steps: int = 40,
) -> np.ndarray:
    """Replace the offset points the level set trims by the mitre of their run.

    The per-vertex offset is the level set only where the vertex's own normal
    reaches it.  In the shadow of a reflex corner, or of a concave bend
    tighter than the height, the offset point lies closer to the adjacent wall
    than the height; the level set there has a single corner, the *mitre*,
    where the offsets of the wall on either side meet.  Every trimmed vertex
    of one contiguous run therefore maps to that one point, which keeps the
    front simple: projecting each point onto the nearest level-set arc would
    put the run's points on the far side's arc and make the front zigzag
    across the bisector.

    The mitre is found on the bisector of the run's sharpest reflex vertex -
    its arc-length midpoint when the run is a smooth bend - by bisection on
    the wall distance, which grows monotonically along the bisector from zero
    at the wall.  For a straight-edged corner this is the classical mitre at
    ``height / sin(angle / 2)``; for curved walls it is exactly the point of
    the eroded domain's boundary at that height.
    """
    result = np.array(offset, dtype=np.float64)
    nodes = len(wall) - 1 if closed else len(wall)
    loops = [np.asarray(loop, dtype=np.float64) for loop in wall_loops]
    target = np.asarray(heights, dtype=np.float64)[:nodes]
    if not loops or nodes == 0:
        return result

    def wall_distance(points: np.ndarray) -> np.ndarray:
        best = np.full(len(points), math.inf)
        for loop in loops:
            best = np.minimum(best, g2.distance_to_polyline(loop, points))
        return best

    trimmed = (wall_distance(result[:nodes]) < tolerance * target) & (target > 0.0)
    if not bool(np.any(trimmed)):
        return result
    angles = np.asarray(fluid_angles_at, dtype=np.float64)
    # A reflex corner's own offset is the straight-edge mitre, which lies
    # beyond the level set when the adjacent wall curves, so it is never
    # "too close" and would split its shadow into two runs with two different
    # mitres.  A run therefore absorbs the reflex vertices next to it, which
    # merges the two sides of a corner into one run centred on the corner.
    reflex = angles[:nodes] < REFLEX_FLUID_ANGLE
    for _pass in range(nodes):
        if closed:
            neighbour = np.roll(trimmed, 1) | np.roll(trimmed, -1)
        else:
            neighbour = np.zeros(nodes, dtype=bool)
            neighbour[1:] |= trimmed[:-1]
            neighbour[:-1] |= trimmed[1:]
        grown = trimmed | (reflex & neighbour)
        if bool(np.array_equal(grown, trimmed)):
            break
        trimmed = grown
    indices = np.nonzero(trimmed)[0]
    runs: list[list[int]] = [[int(indices[0])]]
    for index in indices[1:]:
        if int(index) == runs[-1][-1] + 1:
            runs[-1].append(int(index))
        else:
            runs.append([int(index)])
    if closed and len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == nodes - 1:
        runs[0] = runs.pop() + runs[0]
    for run in runs:
        run_angles = angles[run]
        if float(np.min(run_angles)) < REFLEX_FLUID_ANGLE:
            corner = run[int(np.argmin(run_angles))]
        else:
            corner = run[len(run) // 2]
        base = wall[corner]
        direction = normals[corner]
        height = float(target[corner])
        if height <= 0.0:
            continue
        half = max(0.5 * float(angles[corner]), 0.05)
        reach = height / max(math.sin(min(half, 0.5 * math.pi)), 0.05)

        def distance_at(t: float) -> float:
            return float(wall_distance((base + t * direction)[None, :])[0])

        for _attempt in range(12):
            if distance_at(reach) >= height:
                break
            reach *= 1.5
        else:
            continue
        low, high = 0.0, reach
        for _step in range(int(steps)):
            middle = 0.5 * (low + high)
            if distance_at(middle) < height:
                low = middle
            else:
                high = middle
        result[run] = base + high * direction
    if closed:
        result[-1] = result[0]
    return result


def concave_curvature_cap(
    cap: np.ndarray,
    wall: np.ndarray,
    normals: np.ndarray,
    arclength: np.ndarray,
    fluid_angles_at: np.ndarray,
    far: np.ndarray,
    *,
    closed: bool,
    clearance_fraction: float,
    curvature_fraction: float,
    upper: float,
    steps: int = 26,
    margin: float = 1.5,
) -> np.ndarray:
    """Keep a smooth concave bend's front below its radius of curvature.

    On a smooth stretch the offset at height ``h`` of a wall bending towards
    the fluid with radius ``R`` is an arc of radius ``R - h``: as ``h``
    approaches ``R`` the front turns through the whole bend inside a few
    vertices and the band blocks there fold or cross their spokes, although
    every offset point is still a valid level-set point.  The tangent disk
    against the wall's own geometry measures exactly ``R`` there, so smooth
    vertices are capped at ``curvature_fraction`` of it - the same rule the
    convex side already uses for its radius of curvature.

    A reflex corner and the wall inside its mitre shadow are exempt: there the
    own-wall disk is the distance to the corner, which is the level set being
    trimmed rather than bending, and the height comes from the far clearance.
    """
    result = np.array(cap, dtype=np.float64)
    angles = np.asarray(fluid_angles_at, dtype=np.float64)
    own = local_feature_size([wall], wall, normals, upper=upper, steps=steps)
    stations = np.asarray(arclength, dtype=np.float64)
    total = float(stations[-1])
    exempt = angles < REFLEX_FLUID_ANGLE
    for index in np.nonzero(angles < REFLEX_FLUID_ANGLE)[0]:
        height = min(float(far[index]) * clearance_fraction, float(result[index]))
        cot = 1.0 / math.tan(max(0.5 * float(angles[index]), 1.0e-3))
        reach = margin * height * cot
        gaps = np.abs(stations - stations[index])
        if closed:
            gaps = np.minimum(gaps, total - gaps)
        exempt |= gaps <= reach
    limited = np.minimum(result, curvature_fraction * own)
    return np.where(exempt, result, limited)


def reflex_shadow_cap(
    cap: np.ndarray,
    arclength: np.ndarray,
    fluid_angles_at: np.ndarray,
    gate_indices,
    *,
    closed: bool,
    fraction: float = 0.9,
) -> np.ndarray:
    """Keep every gate out of the mitre shadow of a reflex corner.

    At height ``h`` the level set owned by a reflex vertex of fluid angle
    ``theta`` is the mitre, and the wall within ``h / tan(theta / 2)`` of the
    corner on either side has no front point of its own.  A gate inside that
    shadow would put its spoke onto the mitre too and collapse its band
    block, so the corner's height is capped at the arc length to the nearest
    other gate, in mitre units.  Corners that are themselves gates keep the
    spoke along the bisector.
    """
    result = np.array(cap, dtype=np.float64)
    angles = np.asarray(fluid_angles_at, dtype=np.float64)
    stations = np.asarray(arclength, dtype=np.float64)
    total = float(stations[-1])
    gates = np.asarray(sorted(set(int(index) for index in gate_indices)))
    if len(gates) < 2:
        return result
    gate_stations = stations[gates]
    for index in np.nonzero(angles < REFLEX_FLUID_ANGLE)[0]:
        cot = 1.0 / math.tan(0.5 * float(angles[index]))
        if cot <= 0.0:
            continue
        gaps = np.abs(gate_stations - stations[index])
        if closed:
            gaps = np.minimum(gaps, total - gaps)
        gaps = gaps[gaps > 1.0e-12 * max(total, 1.0e-300)]
        if not len(gaps):
            continue
        result[index] = min(result[index], fraction * float(np.min(gaps)) / cot)
    return result


def slope_limited(
    heights: np.ndarray, arclength: np.ndarray, slope: float, *, closed: bool
):
    """Limit ``|dh/ds|`` so an offset front cannot kink or fold."""
    values = np.array(heights, dtype=np.float64)
    count = len(values)
    if count < 2 or slope <= 0.0:
        return values
    steps = np.diff(arclength)
    for _pass in range(3):
        for index in range(1, count):
            values[index] = min(
                values[index], values[index - 1] + slope * steps[index - 1]
            )
        for index in range(count - 2, -1, -1):
            values[index] = min(values[index], values[index + 1] + slope * steps[index])
        if closed:
            wrap = arclength[-1] - arclength[-2] if count > 1 else 0.0
            first = min(values[0], values[-1] + slope * wrap)
            values[0] = values[-1] = first
    return values


# ---------------------------------------------------------------------------
# Station insertion and slicing
# ---------------------------------------------------------------------------


def insert_stations(
    points: np.ndarray, stations, *, closed: bool = True, snap: float = 0.3
) -> tuple[np.ndarray, list[int]]:
    """Return the curve with the given arc-length stations present as vertices.

    A station that lands within ``snap`` of the shorter segment adjacent to an
    existing vertex is snapped onto it.  Inserting a vertex a thousandth of a
    segment away from another one is worse than moving the station: the two
    normals there are meaningless, and every offset, spoke and block corner
    built on them inherits the noise.
    """
    array = np.asarray(points, dtype=np.float64)
    cumulative = g2.cumulative_length(array)
    total = float(cumulative[-1])
    lengths = g2.segment_lengths(array)
    node_count = len(array) - 1 if closed else len(array)
    if closed:
        before = np.roll(lengths, 1)[:node_count]
        after = lengths[:node_count]
    else:
        before = np.concatenate(([lengths[0]], lengths))
        after = np.concatenate((lengths, [lengths[-1]]))
    tolerance = snap * np.minimum(before, after)
    node_stations = cumulative[:node_count]

    snapped: dict[int, int] = {}
    inserted: list[tuple[float, int]] = []
    for order, value in enumerate(stations):
        station = float(value) % total if closed else min(max(float(value), 0.0), total)
        index = int(np.searchsorted(node_stations, station))
        best = None
        for candidate in (index - 1, index, index + 1):
            node = candidate % node_count if closed else candidate
            if node < 0 or node >= node_count:
                continue
            gap = abs(station - float(node_stations[node]))
            if closed:
                gap = min(gap, total - gap)
            if gap <= tolerance[node] and (best is None or gap < best[0]):
                best = (gap, node)
        if best is not None:
            snapped[order] = best[1]
        else:
            inserted.append((station, order))
    # Two stations must never share a vertex.  When they compete for one, the
    # closer station keeps it and the other is inserted where it actually is,
    # which is the honest answer: those two cuts really are that close.
    claimed: dict[int, tuple[float, int]] = {}
    for order, node in sorted(snapped.items()):
        raw = float(list(stations)[order])
        gap = abs(
            (raw % total if closed else raw) - float(node_stations[node])
        )
        previous = claimed.get(node)
        if previous is None or gap < previous[0]:
            if previous is not None:
                station = float(list(stations)[previous[1]])
                station = station % total if closed else min(max(station, 0.0), total)
                inserted.append((station, previous[1]))
                del snapped[previous[1]]
            claimed[node] = (gap, order)
        else:
            station = float(list(stations)[order])
            station = station % total if closed else min(max(station, 0.0), total)
            inserted.append((station, order))
            del snapped[order]

    merged = [(float(node_stations[index]), 0, index) for index in range(node_count)]
    merged.extend((station, 1, order) for station, order in inserted)
    merged.sort(key=lambda item: (item[0], item[1]))
    result: list[np.ndarray] = []
    node_index: dict[int, int] = {}
    index_of: dict[int, int] = {}
    for _station, kind, payload in merged:
        if kind == 0:
            node_index[payload] = len(result)
            result.append(array[payload])
        else:
            index_of[payload] = len(result)
            result.append(g2.sample_at_arclength(array, [_station])[0])
    for order, node in snapped.items():
        index_of[order] = node_index[node]
    stacked = np.asarray(result)
    if closed:
        stacked = np.vstack([stacked, stacked[:1]])
    return stacked, [index_of[order] for order in range(len(list(stations)))]


def ring_slice(array: np.ndarray, start: int, stop: int) -> np.ndarray:
    """Vertices of a closed array from ``start`` to ``stop`` going forward."""
    count = len(array) - 1
    indices = []
    cursor = start % count
    indices.append(cursor)
    while cursor != stop % count:
        cursor = (cursor + 1) % count
        indices.append(cursor)
    if len(indices) == 1:
        indices = list(range(start % count, start % count + count + 1))
        indices = [item % count for item in indices]
    return array[indices]


# ---------------------------------------------------------------------------
# Fronts
# ---------------------------------------------------------------------------


@dataclass
class Front:
    """One wall band: the augmented wall loop and its offset partner."""

    site: int
    name: str
    wall: np.ndarray
    front: np.ndarray
    heights: np.ndarray
    gate_indices: list[int]
    shrink: float = 1.0
    notes: list[str] = field(default_factory=list)
    seams: dict = field(default_factory=dict)

    def is_seam(self, order: int) -> bool:
        return order in self.seams

    def point_at(self, order: int, side: str) -> np.ndarray:
        """Front vertex a band block sees at one end of its wall section.

        A sharp wall feature carries two front vertices, one per incident wall
        side, because a single offset direction there would put the two
        neighbouring band blocks on opposite sides of the same point.
        """
        seam = self.seams.get(order)
        if seam is None:
            return self.front[self.gate_indices[order]]
        return seam[0] if side == "in" else seam[1]

    @property
    def minimum_height(self) -> float:
        return float(np.min(self.heights))

    @property
    def maximum_height(self) -> float:
        return float(np.max(self.heights))


def build_front(
    wall_loop: np.ndarray,
    stations,
    *,
    fluid_sign: float,
    wall_loops,
    requested: float,
    options: LayerOptions,
    obstacles=(),
    station_caps=None,
    floor_height: float = 0.0,
    exempt_orders=(),
    scaffold=None,
    site: int = 0,
    name: str = "",
    allow_scale: bool = False,
    seam_directions=None,
) -> Front:
    """Offset a closed wall loop into the fluid by a clearance-limited height.

    ``seam_directions`` maps a sharp gate order to the two offset directions
    of its seam points, scaled so that ``wall + height * direction`` lies at
    ``height`` from the wall side and from whatever else bounds that side -
    a wake line - instead of the plain one-sided normals.

    When the local repairs do not converge the front is not admissible as
    the gates stand, and the right response is usually a cut - the caller
    inserts an anchor at the failing block and asks again.  Only with
    ``allow_scale`` does this fall back to one global scale of the height
    found by a bounded bisection search. A failure reports the failing sampled
    scale; it is not a proof that every possible height fails. The caller
    passes it on its last repair round.

    ``fluid_sign`` is +1 when the fluid lies to the right of the stored winding
    (a solid body) and -1 when it lies to the left (an outer boundary), matching
    the site curves the medial stage already uses.
    """
    augmented, gate_indices = insert_stations(wall_loop, stations, closed=True)
    angles = effective_fluid_angles(augmented, fluid_sign)
    if options.normal_window > 0.0:
        # Every feature vertex - reflex or sharp convex, the same threshold
        # the mitre rules use - bounds the smoothing window, and the window
        # never exceeds two percent of the perimeter: the request may be far
        # above what the clearance allows, and a window of that size would
        # smooth real bends away.
        breaks = np.flatnonzero(np.abs(angles - math.pi) > (math.pi - REFLEX_FLUID_ANGLE))
        window = min(
            options.normal_window * max(requested, 1.0e-12),
            0.02 * float(g2.total_length(augmented)),
        )
        normals = -fluid_sign * g2.windowed_normals(augmented, window, closed=True, breaks=breaks)
    else:
        normals = -fluid_sign * g2.vertex_normals(augmented, closed=True)
    upper = max(requested, 1.0e-12) / max(options.clearance_fraction, 1.0e-6)
    angles_at = np.concatenate((angles, angles[:1]))
    # The clearance that limits a band is the distance to walls that are not
    # locally adjacent.  The wall's own corners and bends trim the level set
    # into a mitre instead, which the projection below constructs.
    others = other_walls(wall_loops, augmented)
    feature = far_clearance(
        others,
        augmented,
        normals,
        fluid_angles_at=angles_at,
        closed=True,
        upper=upper,
        window=options.shadow_window,
        steps=options.feature_steps,
        rounded=[run["middle"] for run in tight_concave_runs(augmented, fluid_sign)],
    )
    inner = local_feature_size(
        wall_loops, augmented, -normals, upper=upper, steps=options.feature_steps
    )
    sharp = {
        order: index
        for order, index in enumerate(gate_indices)
        if math.degrees(float(angles[index])) >= options.sharp_fluid_angle
    }
    cap = np.minimum(requested, options.clearance_fraction * feature)
    # A convex wall's own feature size is its radius of curvature: offsetting
    # much further than that turns a short wall section into a long front
    # section and the band block stops being a band.  The inscribed disk is
    # that radius for a fat body but half the thickness for a thin one.
    if options.inscribed_cap:
        cap = np.minimum(
            cap, np.maximum(options.curvature_fraction * inner, float(floor_height))
        )
    if scaffold is not None:
        # The core scaffold is where the core patches start.  A front that
        # reaches past it turns the core patch inside out, and the gate-by-gate
        # cap below cannot see that happening between two gates.  This is a
        # guard, not a design rule: it only bites in the last tenth of the way
        # to the scaffold, so a cove where the scaffold legitimately runs close
        # to the wall still gets whatever band the clearance limit allows.
        cap = np.minimum(
            cap,
            options.scaffold_fraction
            * g2.distance_to_polyline(
                np.asarray(scaffold, dtype=np.float64), augmented
            ),
        )
    if station_caps is not None:
        for order, value in enumerate(station_caps):
            index = gate_indices[order]
            cap[index] = min(cap[index], float(value))
    # The inscribed-disk test legitimately returns zero at a sharp convex
    # vertex - no disk is tangent there - so the clearance limit would collapse
    # the band onto the wall exactly where it is needed.  A floor of a few first
    # cells applies at those vertices only; everywhere else the clearance limit
    # is meaningful and must not be overridden.
    sharp_mask = np.degrees(angles) >= options.sharp_fluid_angle
    sharp_mask = np.concatenate((sharp_mask, sharp_mask[:1]))

    def floored(values: np.ndarray) -> np.ndarray:
        """A sharp vertex never loses its floor, however often it is repaired."""
        return np.where(sharp_mask, np.maximum(values, float(floor_height)), values)

    cap = floored(cap)
    arclength = g2.cumulative_length(augmented)
    cap = concave_curvature_cap(
        cap,
        augmented,
        normals,
        arclength,
        angles_at,
        feature,
        closed=True,
        clearance_fraction=options.clearance_fraction,
        curvature_fraction=options.curvature_fraction,
        upper=upper,
        steps=options.feature_steps,
    )
    cap = floored(cap)
    cap = reflex_shadow_cap(cap, arclength, angles_at, gate_indices, closed=True)
    floor = options.minimum_fraction * requested
    notes: list[str] = []
    shrink = 1.0
    repairs = 0
    stubborn = 0
    previous_hard: list[int] | None = None
    failing: list[int] = []
    cap0 = cap.copy()

    def evaluate(current: np.ndarray) -> dict:
        """Construct the front for one cap vector and judge it completely."""
        # The floor is *not* re-applied after slope limiting.  Doing so was
        # measured: it leaves every synthetic case unchanged but spikes the
        # offset at a cusp, which breaks the seam wedges beside it - on 30P30N
        # the flap's seam stops being constructible and two non-convex core
        # faces come back - and it does not remove the collapse it was aimed at.
        heights = slope_limited(
            np.maximum(current, 0.0), arclength, options.slope_limit, closed=True
        )
        offset = g2.offset_polyline(
            augmented,
            -fluid_sign * heights,
            closed=True,
            limit=options.miter_limit,
        )
        # The offset is the level set only where each normal reaches it; in
        # the shadow of a reflex corner or a tight concave bend it is not, and
        # the level set there is the mitre.
        offset = trim_to_level_set(
            wall_loops, augmented, offset, heights, normals, angles_at, closed=True
        )
        offset[-1] = offset[0]
        offset, stuck = flatten_offset_loops(
            augmented, offset, gate_indices, closed=True
        )
        seams = _seam_points(augmented, sharp, heights, fluid_sign, seam_directions)
        usable = not stuck and _front_is_usable(augmented, offset, obstacles)
        hard: list[int] = []
        soft: list[int] = []
        if usable:
            hard, soft = _band_failures(
                augmented,
                offset,
                gate_indices,
                options.minimum_band_quality,
                options.repair_band_quality,
                exempt_orders,
                seams,
            )
        return {
            "heights": heights,
            "offset": offset,
            "seams": seams,
            "usable": usable,
            "hard": hard,
            "soft": soft,
        }

    def block_vertices(order: int) -> np.ndarray:
        """Augmented-wall indices spanned by one band block, gates included."""
        first = gate_indices[order]
        second = gate_indices[(order + 1) % len(gate_indices)]
        count = len(augmented) - 1
        span = (second - first) % count
        return (first + np.arange(span + 1)) % count

    def finish(state: dict, scale: float, extra: list[str]) -> Front:
        seams = state["seams"]
        if state["soft"]:
            notes.append(
                f"{name}: {len(state['soft'])} band block(s) stay below the repair "
                f"quality; they are strictly convex but poor"
            )
        if scale < 1.0:
            notes.append(
                f"{name}: front height globally reduced to {scale:.3f} of the "
                f"request so the offset stays simple"
            )
        notes.extend(extra)
        if seams:
            notes.append(
                f"{name}: {len(seams)} sharp wall feature(s) carry a band seam - "
                f"two front vertices and a wedge block, so the feature has three "
                f"incident blocks instead of two"
            )
        return Front(
            site,
            name,
            augmented,
            state["offset"],
            state["heights"],
            gate_indices,
            scale,
            notes,
            seams,
        )

    # Stage 1 - local repairs.  A failing band block has the caps of *all* its
    # wall vertices reduced, gates included, so a bend inside the block responds
    # too; reducing only the taller gate left the interior to the slope limiter
    # and made the search wander.
    for _attempt in range(options.local_attempts):
        state = evaluate(cap)
        if not state["usable"]:
            shrink *= options.shrink_factor
            cap = floored(cap * options.shrink_factor)
            if float(np.max(cap)) < floor:
                break
            continue
        hard, soft = state["hard"], state["soft"]
        if hard:
            stubborn = stubborn + 1 if hard == previous_hard else 0
            previous_hard = list(hard)
            if stubborn > options.hard_attempts:
                # The same blocks fail however they are thinned locally: the
                # wall section needs a cut.  Say so unless thinning the whole
                # front is the last move the caller has left.
                if allow_scale:
                    break
                touching = {
                    order
                    for order in hard
                    if order in sharp or (order + 1) % len(gate_indices) in sharp
                }
                raise LayerError(
                    f"band block(s) {hard} of {name!r} stay inadmissible however "
                    f"thin the layer is made locally; "
                    + (
                        "they sit against a sharp wall feature that needs more "
                        "incident sectors"
                        if touching
                        else "the wall section needs a cut, not a smaller height"
                    ),
                    name=name,
                    orders=hard,
                    sharp=bool(touching),
                )
        failing = hard or (soft if repairs < options.repair_budget else [])
        if not failing:
            extra = (
                [
                    f"{name}: {repairs} band block(s) had their front height "
                    f"reduced locally to stay strictly convex"
                ]
                if repairs
                else []
            )
            return finish(state, shrink, extra)
        for order in failing:
            cap[block_vertices(order)] *= options.repair_factor
            repairs += 1
        cap = floored(cap)
        if float(np.max(cap)) < floor:
            break

    if not allow_scale:
        raise LayerError(
            f"no admissible boundary-layer front for {name!r} after {repairs} "
            f"local reductions: band block(s) {failing} still fold or produce a "
            f"non-convex block at {shrink:.4f} of the requested height; the wall "
            f"section needs a cut",
            name=name,
            orders=failing,
        )

    # Stage 2 - one global scale of the original caps, by bisection.  A thin
    # enough band usually follows the wall with convex blocks. The search is
    # bounded, and each accepted sample is checked completely; the discrete
    # trimming and height floor do not guarantee monotone admissibility.
    low, high = 0.0, 1.0
    accepted: dict | None = None
    accepted_scale = 0.0
    thinnest: tuple[float, dict] | None = None
    for _step in range(options.bisection_steps):
        scale = 0.5 * (low + high)
        state = evaluate(floored(cap0 * scale))
        if state["usable"] and not state["hard"]:
            accepted, accepted_scale, low = state, scale, scale
        else:
            high = scale
            thinnest = (scale, state)
    if accepted is not None:
        return finish(
            accepted,
            accepted_scale,
            [
                f"{name}: local repairs did not converge after {repairs} "
                f"reductions; the whole front was scaled to {accepted_scale:.3f} "
                f"of its clearance-limited height instead"
            ],
        )

    # Stage 3 - no sampled height worked: name the thinnest sample's failures.
    scale, state = thinnest if thinnest is not None else (0.0, evaluate(cap0 * 0.0))
    hard = list(state["hard"]) if state["usable"] else []
    touching = {
        order
        for order in hard
        if order in sharp or (order + 1) % len(gate_indices) in sharp
    }
    if not state["usable"]:
        detail = "the front still folds or crosses another boundary"
    elif touching:
        detail = (
            f"band block(s) {hard} sit against a sharp wall feature that needs "
            "more incident sectors"
        )
    else:
        detail = (
            f"band block(s) {hard} stay inadmissible; the wall section needs a "
            "cut, not a smaller height"
        )
    raise LayerError(
        f"no admissible boundary-layer front for {name!r}: at {scale:.4f} of the "
        f"clearance-limited height {detail}",
        name=name,
        orders=hard,
        sharp=bool(touching),
    )


def flatten_offset_loops(
    wall: np.ndarray, offset: np.ndarray, protected=(), *, closed: bool
) -> tuple[np.ndarray, list[int]]:
    """Remove the micro-loops a polyline offset makes on a concave stretch.

    Offsetting a densely sampled curve by a distance much larger than its point
    spacing makes adjacent offset segments cross wherever the curve turns away
    from the offset direction, even when the underlying smooth offset is
    perfectly valid.  Collapsing a vertex that has moved backwards onto its
    predecessor removes the loop without disturbing the vertex correspondence
    that the cut stations rely on; the duplicate point is dropped again when the
    edge geometry is written.  Cut vertices are never collapsed - a backward
    step there is reported so the height can be reduced instead.
    """
    result = np.array(offset, dtype=np.float64)
    keep = set(int(value) for value in protected)
    directions = _window_directions(wall, offset)
    count = len(wall)
    stuck: list[int] = []
    for index in range(1, count):
        reference = directions[index - 1] + directions[index]
        if float(np.dot(result[index] - result[index - 1], reference)) >= 0.0:
            continue
        if index in keep:
            stuck.append(index)
            continue
        result[index] = result[index - 1]
    for index in range(count - 2, -1, -1):
        reference = directions[index] + directions[index + 1]
        if float(np.dot(result[index + 1] - result[index], reference)) >= 0.0:
            continue
        if index in keep:
            stuck.append(index)
            continue
        result[index] = result[index + 1]
    if closed:
        result[-1] = result[0]
    return result, sorted(set(stuck))


def _window_directions(wall: np.ndarray, offset: np.ndarray) -> np.ndarray:
    """Wall direction averaged over a window as wide as the local offset.

    A single segment direction is meaningless where the supplied point list has
    a very short segment next to a normal one, which is exactly where a large
    offset looks as if it had reversed.  The window is the offset distance, the
    only length scale the question is about.
    """
    cumulative = g2.cumulative_length(wall)
    height = np.linalg.norm(offset - wall, axis=1)
    low = np.searchsorted(cumulative, cumulative - height, side="left")
    high = np.searchsorted(cumulative, cumulative + height, side="right") - 1
    low = np.clip(low, 0, len(wall) - 1)
    high = np.clip(high, 0, len(wall) - 1)
    same = high <= low
    low = np.where(same, np.maximum(np.arange(len(wall)) - 1, 0), low)
    high = np.where(same, np.minimum(np.arange(len(wall)) + 1, len(wall) - 1), high)
    delta = wall[high] - wall[low]
    lengths = np.linalg.norm(delta, axis=1)
    return delta / np.where(lengths > 0.0, lengths, 1.0)[:, None]


def _seam_points(wall: np.ndarray, sharp, heights: np.ndarray, fluid_sign: float, overrides=None):
    """One-sided offset points on each side of a sharp wall vertex."""
    directions = g2.segment_directions(wall)
    seams: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    count = len(wall) - 1
    for order, index in sharp.items():
        height = float(heights[index])
        if overrides and order in overrides:
            first, second = overrides[order]
            seams[order] = (
                wall[index] + height * np.asarray(first, dtype=np.float64),
                wall[index] + height * np.asarray(second, dtype=np.float64),
            )
            continue
        incoming = directions[(index - 1) % count]
        outgoing = directions[index % count]
        inward = -fluid_sign * np.array([-incoming[1], incoming[0]])
        outward = -fluid_sign * np.array([-outgoing[1], outgoing[0]])
        seams[order] = (
            wall[index] + height * inward,
            wall[index] + height * outward,
        )
    return seams


def _band_failures(
    wall: np.ndarray,
    front: np.ndarray,
    gate_indices,
    minimum: float,
    repair: float,
    exempt=(),
    seams=None,
) -> tuple[list[int], list[int]]:
    """Gate orders whose band block is inadmissible, and merely poor.

    ``exempt`` names gate orders whose band block will be replaced by a
    wall-feature fan; judging those blocks here would reject a topology that is
    never built.
    """
    count = len(gate_indices)
    skip = set(int(value) for value in exempt)
    failing: list[int] = []
    poor: list[int] = []
    for order in range(count):
        if order in skip:
            continue
        following = (order + 1) % count
        first = gate_indices[order]
        second = gate_indices[following]
        places = seams or {}
        start = (
            places[order][1] if order in places else front[first]
        )
        stop = (
            places[following][0] if following in places else front[second]
        )
        quad = np.asarray([wall[first], wall[second], stop, start])
        section = ring_slice(front, first, second).copy()
        section[0] = start
        section[-1] = stop
        # Each spoke ends where the section starts or stops, so the section
        # segment meeting it there cannot properly cross it - but through
        # rounding of the intersection parameters it can register as if it
        # did, and the block would be thinned for nothing.  That segment is
        # left out; where a seam place stands in for the front point the
        # spoke and the section share no vertex and the whole section counts.
        for index, trimmed in (
            (first, section if order in places else section[1:]),
            (second, section if following in places else section[:-1]),
        ):
            spoke = np.asarray([wall[index], front[index]])
            if g2.paths_cross(trimmed, spoke):
                failing.append(order)
        crosses = g2.quad_corner_crosses(quad)
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        pairs = sides * np.roll(sides, -1)
        sign = 1.0 if g2.polygon_area(quad) >= 0.0 else -1.0
        scaled = float(np.min(sign * crosses / np.where(pairs > 0.0, pairs, 1.0)))
        if scaled < minimum:
            failing.append(order)
        elif scaled < repair:
            poor.append(order)
    return sorted(set(failing)), sorted(set(poor))


def _height(wall: np.ndarray, front: np.ndarray, index: int) -> float:
    return float(np.linalg.norm(front[index] - wall[index]))


def _front_is_usable(wall: np.ndarray, front: np.ndarray, obstacles) -> bool:
    if not g2.is_simple(front, closed=True):
        return False
    if g2.paths_cross(front, wall):
        return False
    for other in obstacles:
        if g2.paths_cross(front, other):
            return False
    return True


def fluid_angles(loop: np.ndarray, fluid_sign: float) -> np.ndarray:
    """Fluid-side angle at every vertex of a closed boundary loop, in radians."""
    turning = g2.turning_angles(loop, closed=True)
    return math.pi + fluid_sign * turning


# A run of concave vertices shorter than this fraction of the perimeter that
# turns through at least a reflex corner's worth is a *rounded reflex
# corner*: for any band taller than its radius the level set there is the
# mitre of the run, so it is treated as one reflex vertex at its turning
# midpoint - anchored, mitred, and exempt from the smooth-bend cap that would
# otherwise thin the band to a fraction of the bend's radius.
TIGHT_BEND_FRACTION = 0.03


def tight_concave_runs(loop: np.ndarray, fluid_sign: float, *, fraction: float = TIGHT_BEND_FRACTION) -> list[dict]:
    """Rounded reflex corners of a closed loop: ``{"middle", "start", "end", "turning"}``.

    A run is a maximal cyclic stretch of consecutive vertices that turn towards
    the fluid; it qualifies when its arc length is at most ``fraction`` of the
    perimeter and its total turning at least ``pi - REFLEX_FLUID_ANGLE``, the
    turning at which a sharp vertex counts as reflex.  ``middle`` is the vertex
    where the cumulative turning reaches half; the run's vertices already
    sharper than the reflex threshold are left to the sharp-corner rules and
    make no run.
    """
    angles = fluid_angles(loop, fluid_sign)
    count = len(loop) - 1
    if count < 3:
        return []
    turning = np.maximum(math.pi - angles[:count], 0.0)
    stations = g2.cumulative_length(loop)
    total = float(stations[-1])
    # A vertex is tight when its local radius of curvature - the mean of its
    # two segments over its turning - is below the run length allowed: a
    # gently concave stretch around a tight bend is not part of the corner.
    lengths = np.diff(stations)
    mean_length = 0.5 * (lengths + np.roll(lengths, 1))
    radius = np.where(turning > 1.0e-9, mean_length / np.where(turning > 1.0e-9, turning, 1.0), math.inf)
    concave = (turning > 1.0e-9) & (angles[:count] >= REFLEX_FLUID_ANGLE) & (radius < fraction * total)
    if not np.any(concave) or np.all(concave):
        return []
    # Start each run after a non-concave vertex.
    start = next(i for i in range(count) if not concave[i])
    runs = []
    index = (start + 1) % count
    steps = 0
    while steps < count:
        if concave[index]:
            run = []
            while concave[index] and steps < count:
                run.append(index)
                index = (index + 1) % count
                steps += 1
            arc = (stations[run[-1]] - stations[run[0]]) % total
            total_turning = float(np.sum(turning[run]))
            if total_turning >= math.pi - REFLEX_FLUID_ANGLE and arc <= fraction * total:
                cumulative = np.cumsum(turning[run])
                middle = run[int(np.searchsorted(cumulative, 0.5 * total_turning))]
                runs.append({"middle": int(middle), "start": int(run[0]), "end": int(run[-1]), "turning": total_turning})
        else:
            index = (index + 1) % count
            steps += 1
    return runs


def effective_fluid_angles(loop: np.ndarray, fluid_sign: float) -> np.ndarray:
    """Fluid angles with every rounded reflex corner folded into its middle vertex."""
    angles = fluid_angles(loop, fluid_sign)
    for run in tight_concave_runs(loop, fluid_sign):
        angles[run["middle"]] = math.pi - run["turning"]
    if len(angles) > len(loop) - 1:
        angles[-1] = angles[0]
    return angles
