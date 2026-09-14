"""Boundary-layer fronts and the wall-feature fan.

A *front* is an explicit curve inside the fluid at a controlled distance from a
wall chain.  Once a front exists the wall band between the wall and the front is
ordinary structured topology, and everything further from the wall sees the
front - not the physical wall - as its boundary.  That is the whole point: the
near-wall region stops being whatever the interior decomposition happens to
produce.

The front height is clearance limited,

    height(s) = min(requested_height, clearance_fraction * local_feature_size(s))

where the local feature size is the radius of the largest disk that is tangent
to the wall at ``s`` and still fits between the walls.  It is found by bisection
on the wall distance field, so it is a property of the geometry rather than of a
raster.  The result is slope limited along the wall so the front cannot kink,
and it is shrunk globally when the offset would still self-intersect or cross
another boundary.

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
    curvature_fraction: float = 0.8
    # Fraction of the distance to the core scaffold a front may reach.
    scaffold_fraction: float = 0.9
    miter_limit: float = 1.5
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
) -> Front:
    """Offset a closed wall loop into the fluid by a clearance-limited height.

    ``fluid_sign`` is +1 when the fluid lies to the right of the stored winding
    (a solid body) and -1 when it lies to the left (an outer boundary), matching
    the site curves the medial stage already uses.
    """
    augmented, gate_indices = insert_stations(wall_loop, stations, closed=True)
    normals = -fluid_sign * g2.vertex_normals(augmented, closed=True)
    upper = max(requested, 1.0e-12) / max(options.clearance_fraction, 1.0e-6)
    feature = local_feature_size(
        wall_loops, augmented, normals, upper=upper, steps=options.feature_steps
    )
    inner = local_feature_size(
        wall_loops, augmented, -normals, upper=upper, steps=options.feature_steps
    )
    angles = fluid_angles(augmented, fluid_sign)
    sharp = {
        order: index
        for order, index in enumerate(gate_indices)
        if math.degrees(float(angles[index])) >= options.sharp_fluid_angle
    }
    cap = np.minimum(requested, options.clearance_fraction * feature)
    # A convex wall's own feature size is its radius of curvature: offsetting
    # much further than that turns a short wall section into a long front
    # section and the band block stops being a band.
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
    floor = options.minimum_fraction * requested
    notes: list[str] = []
    shrink = 1.0
    repairs = 0
    stubborn = 0
    previous_hard: list[int] | None = None
    failing: list[int] = []
    for _attempt in range(options.shrink_attempts * 4 + 1):
        # The floor is *not* re-applied after slope limiting.  Doing so was
        # measured: it leaves every synthetic case unchanged but spikes the
        # offset at a cusp, which breaks the seam wedges beside it - on 30P30N
        # the flap's seam stops being constructible and two non-convex core
        # faces come back - and it does not remove the collapse it was aimed at.
        heights = slope_limited(
            np.maximum(cap, 0.0), arclength, options.slope_limit, closed=True
        )
        offset = g2.offset_polyline(
            augmented,
            -fluid_sign * heights,
            closed=True,
            limit=options.miter_limit,
        )
        offset[-1] = offset[0]
        offset, stuck = flatten_offset_loops(
            augmented, offset, gate_indices, closed=True
        )
        seams = _seam_points(augmented, sharp, heights, fluid_sign)
        if stuck or not _front_is_usable(augmented, offset, obstacles):
            shrink *= options.shrink_factor
            cap = floored(cap * options.shrink_factor)
            if float(np.max(cap)) < floor:
                break
            continue
        hard, soft = _band_failures(
            augmented,
            offset,
            gate_indices,
            options.minimum_band_quality,
            options.repair_band_quality,
            exempt_orders,
            seams,
        )
        if hard:
            stubborn = stubborn + 1 if hard == previous_hard else 0
            previous_hard = list(hard)
            if stubborn > options.hard_attempts:
                touching = {
                    order
                    for order in hard
                    if order in sharp
                    or (order + 1) % len(gate_indices) in sharp
                }
                raise LayerError(
                    f"band block(s) {hard} of {name!r} stay inadmissible however "
                    f"thin the layer is made; "
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
            if soft:
                notes.append(
                    f"{name}: {len(soft)} band block(s) stay below the repair "
                    f"quality; they are strictly convex but poor"
                )
            if shrink < 1.0:
                notes.append(
                    f"{name}: front height globally reduced to {shrink:.3f} of the "
                    f"request so the offset stays simple"
                )
            if repairs:
                notes.append(
                    f"{name}: {repairs} band block(s) had their front height "
                    f"reduced locally to stay strictly convex"
                )
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
                offset,
                heights,
                gate_indices,
                shrink,
                notes,
                seams,
            )
        for order in failing:
            following = (order + 1) % len(gate_indices)
            first = gate_indices[order]
            second = gate_indices[following]
            taller = (
                first
                if _height(augmented, offset, first)
                >= _height(augmented, offset, second)
                else second
            )
            cap[taller] *= options.repair_factor
            repairs += 1
        cap = floored(cap)
        if float(np.max(cap)) < floor:
            break
    raise LayerError(
        f"no admissible boundary-layer front for {name!r}: the clearance-limited "
        f"offset still folds or produces a non-convex band block at "
        f"{shrink:.4f} of the requested height",
        name=name,
        orders=failing,
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


def _seam_points(wall: np.ndarray, sharp, heights: np.ndarray, fluid_sign: float):
    """One-sided offset points on each side of a sharp wall vertex."""
    directions = g2.segment_directions(wall)
    seams: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    count = len(wall) - 1
    for order, index in sharp.items():
        incoming = directions[(index - 1) % count]
        outgoing = directions[index % count]
        height = float(heights[index])
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
        for index in (first, second):
            spoke = np.asarray([wall[index], front[index]])
            if g2.paths_cross(section, spoke):
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
