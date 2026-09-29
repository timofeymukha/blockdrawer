"""Hull far field: level sets of the whole body cluster beyond the near field.

The annular construction is right between bodies and wrong far from them: its
one medial ring sits at half the far-field distance, so the core blocks are
pie slices and the far field's chain breaks gate the bodies at whatever wall
point happens to be closest.  The single-body C-grid escapes that by building
its outer core as the level sets of the body.  This module does the same for
any number of bodies:

1. the *hull* is the level set of the cluster - the wall distance function of
   all bodies together - at a height ``hull_ratio`` times the band height,
   one closed curve around every body once the height exceeds half the widest
   gap between them;
2. the near field is the annular construction run with the hull as its outer
   boundary (one far-field chain), so bands, seams, cavities, gap spanning and
   wakes that end on a downstream body are all built by the existing producer,
   and a wake that leaves the cluster exits through the hull;
3. beyond the hull the level sets of the *slit hull* - the hull with the exit
   wake bands cut through it - are built at geometrically growing heights,
   exactly as the C-grid builds them above its band, and the outermost level
   is joined to the real outer boundary by straight spokes; the wake bands
   run on along their wake lines to the outlet.

Every level set of the cluster beyond the hull is the hull offset by the
height difference, since the distance function's level sets are parallel, so
step 3 needs nothing but the hull polyline and the wake exits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2


@dataclass(frozen=True)
class HullOptions:
    enabled: bool = False
    # Hull height as a multiple of the band height: with the annular
    # clearance fraction of 0.35 the medial ring between a body and the hull
    # sits at half the hull height, and the band it allows is 0.35 times
    # that, so six band heights leave the band its full height.
    height_ratio: float = 6.0
    # Level heights above the hull grow by this factor, up to ``reach`` of
    # the distance from the hull to the outer boundary, at most ``max_levels``.
    # Two rather than the C-grid's three: the hull already starts high, and
    # a gentler growth fits two levels under the reach where three fits one.
    growth: float = 2.0
    reach: float = 0.35
    max_levels: int = 4
    # Raster width for the level-set extraction.
    resolution: int = 400

    def __post_init__(self) -> None:
        if not math.isfinite(self.height_ratio) or self.height_ratio <= 0.0:
            raise ValueError("the hull height ratio must be finite and positive")
        if not math.isfinite(self.growth) or self.growth <= 1.0:
            raise ValueError("the level growth must be finite and above 1")
        if not math.isfinite(self.reach) or not 0.0 < self.reach < 1.0:
            raise ValueError("the reach must lie strictly between 0 and 1")
        if self.max_levels < 0:
            raise ValueError("max_levels must be non-negative")
        if self.resolution < 16:
            raise ValueError("the resolution must be at least 16")


class HullError(RuntimeError):
    """The hull far field does not apply here; the reason is the message."""


# ---------------------------------------------------------------------------
# The cluster's level set
# ---------------------------------------------------------------------------


def distance_to_walls(loops, points) -> np.ndarray:
    """Distance from each point to the nearest wall of any loop."""
    query = np.atleast_2d(np.asarray(points, dtype=np.float64))
    best = np.full(len(query), np.inf)
    for loop in loops:
        closed = g2.close_loop(np.asarray(loop, dtype=np.float64), 0.0)
        for start in range(0, len(query), 20000):
            chunk = query[start:start + 20000]
            best[start:start + 20000] = np.minimum(
                best[start:start + 20000], g2.distance_to_polyline(closed, chunk)
            )
    return best


def _marching_squares(field_values: np.ndarray, level: float):
    """Closed contours of a scalar raster at ``level`` as index-space loops.

    Cells are the squares between four raster nodes; each crossed cell edge
    holds one interpolated point; the segments per cell are joined into loops
    through the shared edge points.  Saddle cells take the two segments that
    keep the high corners apart, which is enough for a distance field.
    """
    rows, columns = field_values.shape
    inside = field_values >= level
    points: dict[tuple, np.ndarray] = {}

    def edge_point(r0, c0, r1, c1):
        key = (r0, c0, r1, c1) if (r0, c0) <= (r1, c1) else (r1, c1, r0, c0)
        if key not in points:
            a = float(field_values[r0, c0])
            b = float(field_values[r1, c1])
            t = 0.5 if a == b else (level - a) / (b - a)
            points[key] = np.asarray([r0 + t * (r1 - r0), c0 + t * (c1 - c0)])
        return key

    adjacency: dict[tuple, list[tuple]] = {}

    def connect(first, second):
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)

    for r in range(rows - 1):
        for c in range(columns - 1):
            code = (
                (1 if inside[r, c] else 0)
                | (2 if inside[r, c + 1] else 0)
                | (4 if inside[r + 1, c + 1] else 0)
                | (8 if inside[r + 1, c] else 0)
            )
            if code in (0, 15):
                continue
            top = (r, c, r, c + 1)
            right = (r, c + 1, r + 1, c + 1)
            bottom = (r + 1, c, r + 1, c + 1)
            left = (r, c, r + 1, c)
            edges = {"top": top, "right": right, "bottom": bottom, "left": left}
            table = {
                1: [("left", "top")], 2: [("top", "right")], 3: [("left", "right")],
                4: [("right", "bottom")], 5: [("left", "top"), ("right", "bottom")],
                6: [("top", "bottom")], 7: [("left", "bottom")], 8: [("bottom", "left")],
                9: [("top", "bottom")], 10: [("top", "right"), ("bottom", "left")],
                11: [("right", "bottom")], 12: [("right", "left")], 13: [("top", "right")],
                14: [("left", "top")],
            }
            for first, second in table[code]:
                key_a = edge_point(*edges[first])
                key_b = edge_point(*edges[second])
                connect(key_a, key_b)
    loops = []
    seen: set = set()
    for start in adjacency:
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        previous, current = None, start
        while True:
            candidates = [item for item in adjacency[current] if item != previous]
            if not candidates:
                break
            following = candidates[0]
            if following == start:
                break
            if following in seen:
                break
            loop.append(following)
            seen.add(following)
            previous, current = current, following
        if len(loop) >= 3:
            loops.append(np.asarray([points[key] for key in loop]))
    return loops


def cluster_level_set(loops, height: float, *, resolution: int = 400, spacing: float | None = None) -> np.ndarray:
    """The outer level set of the cluster's wall distance at ``height``.

    A closed anticlockwise polyline, resampled to about ``spacing`` between
    points (a fifth of the height by default) and projected onto the exact
    level set.  Raises ``HullError`` when the level set is not one closed
    curve around every body - the height is below half the widest gap.
    """
    walls = [np.asarray(loop, dtype=np.float64) for loop in loops]
    stacked = np.vstack(walls)
    low = np.min(stacked, axis=0) - 1.5 * height
    high = np.max(stacked, axis=0) + 1.5 * height
    span = float(np.max(high - low))
    pixel = span / (resolution - 1)
    counts = np.maximum(np.ceil((high - low) / pixel).astype(int) + 1, 4)
    xs = low[0] + pixel * np.arange(counts[0])
    ys = low[1] + pixel * np.arange(counts[1])
    grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")
    samples = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    field_values = distance_to_walls(walls, samples).reshape(grid_x.shape)
    contours = _marching_squares(field_values, height)
    if not contours:
        raise HullError(f"no level set of the cluster at height {height:.4g}")
    world = []
    for contour in contours:
        points = np.column_stack([low[0] + contour[:, 0] * pixel, low[1] + contour[:, 1] * pixel])
        world.append(points)
    # The hull is the loop that contains every body; the widest gap must be
    # bridged for there to be one.
    hulls = [
        closed for closed in (g2.close_loop(points, 0.0) for points in world)
        if len(closed) >= 4 and all(_contains(closed, wall[0]) for wall in walls)
    ]
    if len(hulls) != 1:
        raise HullError(
            f"the level set at height {height:.4g} is {len(contours)} curve(s), "
            f"{len(hulls)} of them around every body; raise the height above half the widest gap"
        )
    closed = g2.orient_anticlockwise(hulls[0])
    # Resample and project onto the exact level set: each point moves along
    # the wall-distance gradient until its distance is the height.
    step = spacing if spacing is not None else 0.2 * height
    total = g2.total_length(closed)
    count = max(int(round(total / step)), 12)
    stations = np.linspace(0.0, total, count, endpoint=False)
    points = g2.sample_at_arclength(closed, stations)
    for _ in range(3):
        nearest = _closest_on_walls(walls, points)
        offset = points - nearest
        distance = np.linalg.norm(offset, axis=1)
        safe = np.where(distance > 0.0, distance, 1.0)
        points = points + offset / safe[:, None] * (height - distance)[:, None]
    # Closed: the last point repeats the first, as a boundary chain expects.
    return np.vstack([points, points[:1]])


def _contains(closed: np.ndarray, point) -> bool:
    """Even-odd test of a point against a closed polyline."""
    x, y = float(point[0]), float(point[1])
    a = closed[:-1]
    b = closed[1:]
    crosses = (a[:, 1] > y) != (b[:, 1] > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.sum(crosses & (x < x_at)) % 2 == 1)


def _closest_on_walls(walls, points) -> np.ndarray:
    query = np.atleast_2d(np.asarray(points, dtype=np.float64))
    best_distance = np.full(len(query), np.inf)
    best_point = np.zeros_like(query)
    for wall in walls:
        closed = g2.close_loop(wall, 0.0)
        result = g2.closest_on_polyline(closed, query)
        better = result.distance < best_distance
        best_distance = np.where(better, result.distance, best_distance)
        best_point[better] = result.point[better]
    return best_point


def align_start(hull_points: np.ndarray, target) -> np.ndarray:
    """The closed hull re-started at its point closest to ``target``.

    Marching squares starts a contour wherever the raster scan meets it;
    the outer boundary's start is mapped onto the hull's, so the hull starts
    at the point closest to the outer boundary's start instead.
    """
    loop = np.asarray(hull_points, dtype=np.float64)
    if np.allclose(loop[0], loop[-1]):
        loop = loop[:-1]
    query = np.asarray(target, dtype=np.float64)
    index = int(np.argmin(np.linalg.norm(loop - query[None, :], axis=1)))
    rolled = np.roll(loop, -index, axis=0)
    return np.vstack([rolled, rolled[:1]])


def mirrored_chains(hull_points: np.ndarray, outer, walls=None) -> list[tuple[str, str, np.ndarray]]:
    """The hull split into chains named and rolled like the outer boundary's.

    Each outer chain break is placed on the hull by the kink-anchored map
    (``map_stations``) from the outer boundary onto the hull - by plain
    fraction of the perimeter without ``walls`` - so the far field maps every
    hull chain onto the outer chain of the same name and a closed far field
    stays chain-consistent block by block.
    """
    loop = np.asarray(hull_points, dtype=np.float64)
    if not np.allclose(loop[0], loop[-1]):
        loop = np.vstack([loop, loop[:1]])
    total = float(g2.total_length(loop))
    outer_total = float(outer.curve.length())
    chains = list(outer.chains)
    if len(chains) <= 1:
        name = chains[0].name if chains else "farfield"
        role = chains[0].role if chains else "farfield"
        return [(name, role, loop)]
    starts = [chain.start % outer_total for chain in chains]
    if walls is not None:
        outer_loop = outer.curve.loop()
        hull_starts = [st % total for st in map_stations(outer_loop, starts, loop, [np.asarray(w, dtype=np.float64) for w in walls], origin=loop[0])]
    else:
        hull_starts = [(st / outer_total) * total % total for st in starts]
    result = []
    for index, chain in enumerate(chains):
        start = hull_starts[index]
        end = hull_starts[(index + 1) % len(chains)]
        piece = g2.loop_section(loop, start, end, forward=True)
        result.append((chain.name, chain.role, np.asarray(piece)))
    return result


def _owner_switches(walls, loop: np.ndarray):
    """Stations where a closed loop's nearest body changes, with the transitions.

    Every level set of the cluster has a kink where the nearest body switches
    - the exterior medial axis between two bodies crosses it there - and the
    kinks of one level correspond to those of another in cyclic order.
    Returns ``[(station, from_body, to_body), ...]`` in loop order.
    """
    points = np.asarray(loop, dtype=np.float64)[:-1]
    distances = np.column_stack([g2.distance_to_polyline(g2.close_loop(wall, 0.0), points) for wall in walls])
    owners = np.argmin(distances, axis=1)
    stations = g2.cumulative_length(np.asarray(loop, dtype=np.float64))
    total = float(stations[-1])
    switches = []
    count = len(points)
    for i in range(count):
        j = (i + 1) % count
        if owners[i] != owners[j]:
            station = 0.5 * (stations[i] + (stations[j] if j else total))
            switches.append((float(station % total), int(owners[i]), int(owners[j])))
    return switches, total


def map_stations(hull_loop: np.ndarray, hull_stations, level_loop: np.ndarray, walls, *, origin=None):
    """Stations on a level loop corresponding to stations on the hull.

    Between consecutive kinks each hull piece maps onto the level piece with
    the same body transition by arc-length fraction, so a level vertex never
    lands across a kink from its hull vertex, which twisted the far-field
    sectors at the waist between two bodies.  When the two loops' transition
    sequences differ - a body no longer reaches the level set - the whole
    loops map by fraction from ``origin`` (the level point closest to it, or
    the hull start).
    """
    hull = np.asarray(hull_loop, dtype=np.float64)
    level = np.asarray(level_loop, dtype=np.float64)
    hull_switches, hull_total = _owner_switches(walls, hull)
    level_switches, level_total = _owner_switches(walls, level)
    stations = [float(v) % hull_total for v in hull_stations]
    hull_seq = [(a, b) for _s, a, b in hull_switches]
    level_seq = [(a, b) for _s, a, b in level_switches]
    aligned = None
    if hull_seq and len(hull_seq) == len(level_seq):
        for shift in range(len(level_seq)):
            if level_seq[shift:] + level_seq[:shift] == hull_seq:
                aligned = shift
                break
    if aligned is None:
        anchor = hull[0] if origin is None else np.asarray(origin, dtype=np.float64)
        start = float(g2.closest_on_polyline(level, anchor[None, :]).arclength[0])
        return [(start + (st / hull_total) * level_total) % level_total for st in stations]
    hull_kinks = [item[0] for item in hull_switches]
    level_kinks = [level_switches[(aligned + i) % len(level_switches)][0] for i in range(len(level_switches))]
    result = []
    for st in stations:
        # The hull piece the station lies in: from kink j forward to kink j+1.
        offsets = [(st - kink) % hull_total for kink in hull_kinks]
        j = int(np.argmin(offsets))
        piece_hull = (hull_kinks[(j + 1) % len(hull_kinks)] - hull_kinks[j]) % hull_total or hull_total
        piece_level = (level_kinks[(j + 1) % len(level_kinks)] - level_kinks[j]) % level_total or level_total
        fraction = offsets[j] / piece_hull
        result.append((level_kinks[j] + fraction * piece_level) % level_total)
    return result


def _owner_switches_open(walls, path: np.ndarray):
    """Stations along an open path where the nearest body changes."""
    points = np.asarray(path, dtype=np.float64)
    distances = np.column_stack([g2.distance_to_polyline(g2.close_loop(wall, 0.0), points) for wall in walls])
    owners = np.argmin(distances, axis=1)
    stations = g2.cumulative_length(points)
    switches = []
    for i in range(len(points) - 1):
        if owners[i] != owners[i + 1]:
            switches.append((float(0.5 * (stations[i] + stations[i + 1])), int(owners[i]), int(owners[i + 1])))
    return switches, float(stations[-1])


def map_open(path_from: np.ndarray, stations_from, path_to: np.ndarray, walls):
    """Stations on one open path corresponding to stations on another.

    The ends correspond, and so does every kink where the nearest body
    changes, when both paths change bodies in the same order; each piece
    between anchors maps by arc-length fraction.  Otherwise the whole paths
    map by fraction.
    """
    switches_from, total_from = _owner_switches_open(walls, path_from)
    switches_to, total_to = _owner_switches_open(walls, path_to)
    anchors_from = [0.0] + [item[0] for item in switches_from] + [total_from]
    anchors_to = [0.0] + [item[0] for item in switches_to] + [total_to]
    if [item[1:] for item in switches_from] != [item[1:] for item in switches_to]:
        anchors_from, anchors_to = [0.0, total_from], [0.0, total_to]
    result = []
    for st in stations_from:
        st = min(max(float(st), 0.0), total_from)
        j = max(int(np.searchsorted(anchors_from, st, side="right")) - 1, 0)
        j = min(j, len(anchors_from) - 2)
        piece_from = anchors_from[j + 1] - anchors_from[j]
        fraction = (st - anchors_from[j]) / piece_from if piece_from > 0.0 else 0.0
        result.append(anchors_to[j] + fraction * (anchors_to[j + 1] - anchors_to[j]))
    return result


# ---------------------------------------------------------------------------
# The far field beyond the hull
# ---------------------------------------------------------------------------


@dataclass
class HullResult:
    graph: object | None
    record: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _lateral(direction, normal, origin, point) -> tuple[float, float]:
    """Along-wake and left-of-wake coordinates of a point about an origin."""
    rel = np.asarray(point, dtype=np.float64) - np.asarray(origin, dtype=np.float64)
    return float(rel @ direction), float(rel @ normal)


def _station_of(site, point) -> float:
    return float(site.curve.closest(np.asarray(point, dtype=np.float64)[None, :]).arclength[0])


def _outer_section(site, first: float, second: float, style: str):
    path = site.curve.section(first, second, forward=True)
    curve = site.curve.edge_curve(first, second, forward=True, style=style)
    return path, curve


def extend(near, domain, outer, loops, *, hull_points: np.ndarray, hull_height: float,
           options: HullOptions | None = None, wall_edge_style: str = "polyLine") -> HullResult:
    """Grow the far field from the near-field result's hull to the outer boundary.

    ``near`` is the annular result built with the hull as its outer boundary,
    ``domain`` and ``outer`` the real domain and its outer site, ``loops`` the
    body point lists.  One wake may leave the cluster through the hull; its
    band runs on to the outlet and cuts every level open, as in the C-grid.
    Raises ``HullError`` with the reason when the construction does not apply.
    """
    import patch_graph as pg
    import wake as wake_module

    settings = options or HullOptions()
    graph_near = near.graph
    if graph_near is None or near.layout is None or near.wakes is None:
        raise HullError("the near field has no graph")
    hull_loop = np.asarray(hull_points, dtype=np.float64)
    if not np.allclose(hull_loop[0], hull_loop[-1]):
        hull_loop = np.vstack([hull_loop, hull_loop[:1]])
    hull_total = float(g2.total_length(hull_loop))
    walls = [np.asarray(loop, dtype=np.float64) for loop in loops]


    # The exit wake: at most one wake leaves through the hull.  None means a
    # closed far field - every level a loop, joined chain by chain to the
    # outer boundary.
    exits_records = [item for item in near.wakes.applied if (item.get("target") or {}).get("kind") == "outer"]
    if any(item.get("kind") == "base" for item in exits_records):
        raise HullError("a blunt base's wake leaving the cluster is not built in the hull far field yet")
    if len(exits_records) > 1:
        raise HullError(f"the hull far field takes at most one wake leaving the cluster, found {len(exits_records)}")
    if not exits_records:
        return _extend_closed(near, domain, outer, walls, hull_loop, hull_height, settings, wall_edge_style)
    record = exits_records[0]
    # The real outer boundary: cap, leg, outlet, leg.
    names = [chain.name for chain in outer.chains]
    roles = {chain.name: chain.role for chain in outer.chains}
    if len(outer.chains) != 4 or list(roles.values()).count("outlet") != 1:
        raise HullError("a wake leaving the cluster needs a four-chain outer boundary with one outlet: cap, leg, outlet, leg")
    outlet_index = next(i for i, name in enumerate(names) if roles[name] == "outlet")
    leg_before = outer.chains[(outlet_index - 1) % 4]
    leg_after = outer.chains[(outlet_index + 1) % 4]
    cap = outer.chains[(outlet_index + 2) % 4]
    outlet = outer.chains[outlet_index]
    total_outer = outer.curve.length()
    c_bot = outlet.start
    c_top = (outlet.start + outlet.length) % total_outer
    j_top = (leg_after.start + leg_after.length) % total_outer
    j_bot = (cap.start + cap.length) % total_outer
    cell_c = len(near.layout.cells) - 1
    key = tuple(record["anchor_key"])
    gate_w = ("gate", cell_c, *key)
    outer_in = ("wake", cell_c, *key, "gate", "in")
    outer_out = ("wake", cell_c, *key, "gate", "out")
    for vertex in (gate_w, outer_in, outer_out):
        if vertex not in graph_near.vertices:
            raise HullError(f"the near field lacks the wake exit vertex {vertex!r}")
    direction = np.asarray(record["direction"], dtype=np.float64)
    direction = direction / np.linalg.norm(direction)
    normal = np.array([-direction[1], direction[0]])       # left of the wake: the out side
    point_w = graph_near.vertices[gate_w].point
    _along, h_out = _lateral(direction, normal, point_w, graph_near.vertices[outer_out].point)
    _along, h_in = _lateral(direction, normal, point_w, graph_near.vertices[outer_in].point)
    h_in = -h_in
    if h_out <= 0.0 or h_in <= 0.0:
        raise HullError("the wake band's exits do not bracket the wake line on the hull")

    # Hull vertices of the near field in hull order; the base runs from the
    # out exit round the cluster to the in exit.
    hull_names = _hull_chain_names(graph_near)
    hull_vertices = set()
    for edge in graph_near.edges.values():
        if edge.boundary in hull_names:
            hull_vertices.update(edge.key)
    if not hull_vertices:
        raise HullError("the near field has no edges on the hull")
    station = {v: float(g2.closest_on_polyline(hull_loop, graph_near.vertices[v].point[None, :]).arclength[0]) for v in hull_vertices}
    s_out, s_in, s_w = station[outer_out], station[outer_in], station[gate_w]
    # Forward along the hull the exits read in, centre, out; the base goes
    # the long way round from out to in, so the centre is not on it.
    if (s_w - s_out) % hull_total <= (s_in - s_out) % hull_total:
        raise HullError("the wake exits are not ordered in, centre, out along the hull")
    ordered = sorted(hull_vertices - {gate_w}, key=lambda v: (station[v] - s_out) % hull_total)
    if ordered[0] != outer_out or ordered[-1] != outer_in:
        raise HullError("the hull vertices do not run from the out exit round to the in exit")
    base_keys = ordered                                   # outer_out ... outer_in
    base_length = (s_in - s_out) % hull_total
    fractions = [((station[v] - s_out) % hull_total) / base_length for v in base_keys]
    fractions[0], fractions[-1] = 0.0, 1.0
    if any(b <= a for a, b in zip(fractions, fractions[1:])):
        raise HullError("the hull vertices are not ordered along the hull")
    count = len(base_keys) - 1                            # sectors

    # Level heights above the walls, geometric growth up to the reach.
    distance_to_outer = float(np.min(outer.curve.distance(hull_loop)))
    heights: list[float] = []
    while len(heights) < settings.max_levels:
        candidate = hull_height * settings.growth ** (len(heights) + 1)
        if candidate > settings.reach * (hull_height + distance_to_outer):
            break
        heights.append(candidate)
    if not heights:
        raise HullError("no level set fits between the hull and the outer boundary")

    # Open level polylines: the cluster's level set, cut where it lies inside
    # the wake band's offset, with the mitre points at the ends.
    def open_level(height: float):
        loop = cluster_level_set(walls, height, resolution=settings.resolution)
        growth = height - hull_height
        along = (loop - point_w) @ direction
        across = (loop - point_w) @ normal
        limit = np.where(across >= 0.0, h_out + growth, h_in + growth)
        inside = (along >= 0.0) & (np.abs(across) < (1.0 - 1e-6) * limit)
        if not np.any(inside) or np.all(inside):
            raise HullError(f"the level set at height {height:.4g} does not cross the wake band")
        # Rotate so the kept run is contiguous: start after the last inside point.
        body = loop[:-1]
        inside = inside[:-1]
        first_out = None
        n = len(body)
        for i in range(n):
            if inside[i - 1] and not inside[i]:
                first_out = i
                break
        order = [(first_out + i) % n for i in range(n)]
        kept = [body[i] for i in order if not inside[i]]
        mitres = {}
        for side, sign, h_side in (("out", 1.0, h_out), ("in", -1.0, h_in)):
            origin = point_w + sign * (h_side + growth) * normal
            hits = wake_module._ray_crossings(origin, direction, loop)
            if not hits:
                raise HullError(f"the level {height:.4g} wake edge on the {side} side does not meet the level set")
            t, _arc, _seg = hits[0]
            mitres[side] = origin + t * direction
        return np.vstack([mitres["out"], np.asarray(kept), mitres["in"]])

    level_polys = []
    notes: list[str] = []
    for height in heights:
        try:
            level_polys.append(open_level(height))
        except HullError as error:
            notes.append(f"level at height {height:.4g} not built ({error}); stopping at {len(level_polys)} level(s)")
            break
    if not level_polys:
        raise HullError("no admissible level set above the hull")
    heights = heights[:len(level_polys)]

    base_points = [graph_near.vertices[v].point for v in base_keys]
    base_path = g2.loop_section(hull_loop, s_out, s_in, forward=True)
    base_stations = [((station[v] - s_out) % hull_total) for v in base_keys]
    base_stations[0], base_stations[-1] = 0.0, float(g2.total_length(base_path))
    level_points = []
    fraction_stations = []
    for poly_k in level_polys:
        # The open base maps onto the open level: ends onto the mitres, the
        # kinks where the nearest body changes onto each other, the pieces
        # between by arc-length fraction.
        stations_k = map_open(base_path, base_stations, poly_k, walls)
        stations_k[0], stations_k[-1] = 0.0, float(g2.total_length(poly_k))
        if any(b <= a for a, b in zip(stations_k, stations_k[1:])):
            raise HullError("the hull's vertices do not map onto a level in order")
        fraction_stations.append(stations_k)
        level_points.append([g2.sample_at_arclength(poly_k, [st])[0] for st in stations_k])
    # Drop levels whose blocks against the level below are not convex.
    kept = 0
    while kept < len(level_polys):
        lower = base_points if kept == 0 else level_points[kept - 1]
        upper = level_points[kept]
        bad = [n for n in range(count) if not g2.strictly_convex(np.asarray([lower[n], lower[n + 1], upper[n + 1], upper[n]]))]
        if bad:
            notes.append(f"level {kept + 1} at height {heights[kept]:.4g} gives a non-convex block in sector(s) {bad[:4]} of {count}; stopping at {kept} level(s)")
            break
        kept += 1
    if kept == 0:
        raise HullError("the first level above the hull gives non-convex blocks: " + "; ".join(notes[-1:]))
    levels = kept
    level_polys, level_points, heights = level_polys[:levels], level_points[:levels], heights[:levels]
    fraction_stations = fraction_stations[:levels]

    def level_piece(k: int, n: int) -> np.ndarray:
        a, b = fraction_stations[k - 1][n], fraction_stations[k - 1][n + 1]
        piece = g2.polyline_section(level_polys[k - 1], a, b)
        piece[0] = level_points[k - 1][n]
        piece[-1] = level_points[k - 1][n + 1]
        return piece

    # --- the graph: the near field, hull edges made interior, then the levels
    graph = _copy_near_field(graph_near, domain, pg)

    def key_level(k, n):
        # n runs 0 (out mitre) .. count (in mitre); keys stay comparable.
        return ("hull", "level", k, int(n))

    def key_exit(k, side):
        return ("hull", "exit", k, side)

    def key_outer(label):
        return ("hull", "corner", label) if isinstance(label, str) else ("hull", "cap", int(label))

    outer_station: dict = {}
    for k in range(1, levels + 1):
        for n in range(count + 1):
            point = level_points[k - 1][n]
            if n in (0, count):
                graph.add_vertex(key_level(k, n), point, constraint=pg.Constraint("guide", "cross-wake:hull", float(k)), provenance="wake mitre")
            else:
                graph.add_vertex(key_level(k, n), point, constraint=pg.Constraint("guide", f"front:hull:{k}", float(n)), provenance="level set")

    # Wake exits on the outlet: the centre line, the band edges (level 0) and
    # every level's mitre lines.
    mitre_in = [graph.vertices[outer_in].point] + [level_points[k - 1][count] for k in range(1, levels + 1)]
    mitre_out = [graph.vertices[outer_out].point] + [level_points[k - 1][0] for k in range(1, levels + 1)]
    outer_loop = outer.curve.loop()
    exits: dict[tuple, tuple[np.ndarray, float]] = {}
    for k in range(levels + 1):
        for side, chain_points in (("in", mitre_in), ("out", mitre_out)):
            origin = chain_points[k]
            hits = wake_module._ray_crossings(origin, direction, outer_loop)
            if not hits:
                raise HullError(f"the level {k} wake line does not reach the outer boundary")
            t, _arclength, _segment = hits[0]
            point = origin + t * direction
            st = _station_of(outer, point)
            if outer.chain_at(st).name != outlet.name:
                raise HullError(f"the level {k} wake line leaves through {outer.chain_at(st).name!r}, not the outlet")
            exits[(k, side)] = (point, st)
    hits = wake_module._ray_crossings(point_w, direction, outer_loop)
    if not hits:
        raise HullError("the wake line does not reach the outer boundary")
    centre_point = point_w + hits[0][0] * direction
    centre_station = _station_of(outer, centre_point)
    if outer.chain_at(centre_station).name != outlet.name:
        raise HullError("the wake line leaves the domain off the outlet")
    outlet_order = [exits[(k, "in")][1] for k in range(levels, -1, -1)] + [centre_station] + [exits[(k, "out")][1] for k in range(0, levels + 1)]
    if any(b <= a for a, b in zip(outlet_order, outlet_order[1:])):
        raise HullError("the wake lines are not ordered along the outlet; a wake line crosses another")
    if not (c_bot < min(outlet_order) and max(outlet_order) < c_top):
        raise HullError("a wake line leaves the outlet beyond its corners")
    graph.add_vertex(key_exit(0, "centre"), centre_point, constraint=pg.Constraint("chain", outlet.name, centre_station), provenance="wake exit")
    outer_station[key_exit(0, "centre")] = centre_station
    for k in range(levels + 1):
        for side in ("in", "out"):
            point, st = exits[(k, side)]
            graph.add_vertex(key_exit(k, side), point, constraint=pg.Constraint("chain", outlet.name, st), provenance="wake exit")
            outer_station[key_exit(k, side)] = st
    for label, st in (("c_top", c_top), ("c_bot", c_bot), ("j_top", j_top), ("j_bot", j_bot)):
        graph.add_vertex(key_outer(label), outer.curve.point_at(st),
                         constraint=pg.Constraint("chain", outer.chain_at(st + 1e-9 * total_outer).name, st), provenance="outer corner")
        outer_station[key_outer(label)] = st
    cap_path = outer.curve.section(cap.start, (cap.start + cap.length) % total_outer, forward=True)
    cap_local = map_open(level_polys[-1], fraction_stations[-1], cap_path, walls)
    cap_stations = [(cap.start + st) % total_outer for st in cap_local]
    cap_stations[0], cap_stations[-1] = j_top, j_bot
    outer_keys = []
    for n, st in enumerate(cap_stations):
        if n == 0:
            outer_keys.append(key_outer("j_top"))
        elif n == len(cap_stations) - 1:
            outer_keys.append(key_outer("j_bot"))
        else:
            k_ = key_outer(n)
            graph.add_vertex(k_, outer.curve.point_at(st), constraint=pg.Constraint("chain", cap.name, st), provenance="far-field station")
            outer_keys.append(k_)
    cap_station_of = dict(zip(outer_keys, cap_stations))

    def line(first, second, role, provenance):
        graph.add_edge(first, second, path=np.asarray([graph.vertices[first].point, graph.vertices[second].point]), role=role, provenance=provenance)

    def poly(first, second, path, role, provenance, boundary=None, kind=None, points=None):
        path = np.asarray(path, dtype=np.float64).copy()
        path[0] = graph.vertices[first].point
        path[-1] = graph.vertices[second].point
        if kind is None:
            interior = path[1:-1]
            kind = "line" if len(interior) == 0 else "polyLine"
            points = tuple((float(x), float(y)) for x, y in interior)
        graph.add_edge(first, second, path=path, kind=kind, points=points, role=role, provenance=provenance, boundary=boundary)

    # Level blocks: sector n between vertices n and n+1 of consecutive levels.
    for n in range(count):
        for k in range(1, levels + 1):
            a, b = key_level(k, n), key_level(k, n + 1)
            poly(a, b, level_piece(k, n), "front", f"level set {k}")
            lower_a = base_keys[n] if k == 1 else key_level(k - 1, n)
            lower_b = base_keys[n + 1] if k == 1 else key_level(k - 1, n + 1)
            line(lower_a, a, "core_spoke", "level spoke")
            line(lower_b, b, "core_spoke", "level spoke")
            graph.add_face((lower_a, lower_b, b, a), role="core", provenance=f"hull level {k} block")
    # Wake strips: the centre band, then one strip per level and side.
    line(gate_w, key_exit(0, "centre"), "wake", "wake separatrix")
    line(outer_in, key_exit(0, "in"), "wake_front", "wake band front")
    line(outer_out, key_exit(0, "out"), "wake_front", "wake band front")
    for first, second in ((key_exit(0, "in"), key_exit(0, "centre")), (key_exit(0, "centre"), key_exit(0, "out"))):
        path, curve = _outer_section(outer, outer_station[first], outer_station[second], wall_edge_style)
        poly(first, second, path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
    graph.add_face((outer_in, gate_w, key_exit(0, "centre"), key_exit(0, "in")), role="wake", provenance="wake band beyond the hull")
    graph.add_face((gate_w, outer_out, key_exit(0, "out"), key_exit(0, "centre")), role="wake", provenance="wake band beyond the hull")
    chain_in = [outer_in] + [key_level(k, count) for k in range(1, levels + 1)]
    chain_out = [outer_out] + [key_level(k, 0) for k in range(1, levels + 1)]
    for k in range(1, levels + 1):
        for side, chain_keys in (("in", chain_in), ("out", chain_out)):
            line(chain_keys[k], key_exit(k, side), "wake_front", f"wake level set {k}")
            first, second = (key_exit(k - 1, side), key_exit(k, side)) if side == "out" else (key_exit(k, side), key_exit(k - 1, side))
            path, curve = _outer_section(outer, outer_station[first], outer_station[second], wall_edge_style)
            poly(first, second, path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
            graph.add_face((chain_keys[k - 1], key_exit(k - 1, side), key_exit(k, side), chain_keys[k]), role="wake", provenance=f"wake band level {k}")
    # Far field: the top level to the cap, and the two wake-side blocks.
    top_keys = [key_level(levels, n) for n in range(count + 1)]
    for n in range(count):
        a, b = top_keys[n], top_keys[n + 1]
        oa, ob = outer_keys[n], outer_keys[n + 1]
        line(a, oa, "core_spoke", "far-field spoke")
        line(b, ob, "core_spoke", "far-field spoke")
        path, curve = _outer_section(outer, cap_station_of[oa], cap_station_of[ob], wall_edge_style)
        poly(oa, ob, path, "wall", "supplied point list", boundary=cap.name, kind=curve.kind, points=curve.points)
        graph.add_face((a, b, ob, oa), role="core", provenance="far-field sector")
    for side_key, exit_key, corner, join, leg, exit_first in (
        (key_level(levels, 0), key_exit(levels, "out"), key_outer("c_top"), key_outer("j_top"), leg_after, True),
        (key_level(levels, count), key_exit(levels, "in"), key_outer("c_bot"), key_outer("j_bot"), leg_before, False),
    ):
        exit_station = outer_station[exit_key]
        corner_station = outer_station[corner]
        join_station = outer_station[join]
        if exit_first:
            path, curve = _outer_section(outer, exit_station, corner_station, wall_edge_style)
            poly(exit_key, corner, path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
            path, curve = _outer_section(outer, corner_station, join_station, wall_edge_style)
            poly(corner, join, path, "wall", "supplied point list", boundary=leg.name, kind=curve.kind, points=curve.points)
        else:
            path, curve = _outer_section(outer, corner_station, exit_station, wall_edge_style)
            poly(corner, exit_key, path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
            path, curve = _outer_section(outer, join_station, corner_station, wall_edge_style)
            poly(join, corner, path, "wall", "supplied point list", boundary=leg.name, kind=curve.kind, points=curve.points)
        line(side_key, join, "core_spoke", "cross-wake line to the far field")
        graph.add_face((side_key, exit_key, corner, join), role="core", provenance="far-field wake block")

    result_record = {
        "hull_height": float(hull_height), "hull_vertices": len(base_keys),
        "levels": levels, "heights": [float(h) for h in heights],
        "distance_to_outer": distance_to_outer,
        "exit_wake": record["site"], "band_half_widths": {"in": h_in, "out": h_out},
    }
    return HullResult(graph, result_record, notes)


def _hull_chain_names(graph_near) -> set:
    """The near field's outer chains - the hull's mirrored chains, not the walls."""
    return {name for name, role in graph_near.boundary_roles.items() if role != "wall"}


def _copy_near_field(graph_near, domain, pg):
    """The near-field graph with the hull's edges made interior."""
    graph = pg.PatchGraph(
        euler_characteristic=domain.euler_characteristic,
        boundary_roles={chain.name: chain.role for chain in domain.chains()},
    )
    hull_names = _hull_chain_names(graph_near)
    for vkey, vertex in graph_near.vertices.items():
        constraint = vertex.constraint
        if constraint.kind == "chain" and constraint.target in hull_names:
            constraint = pg.Constraint("guide", "hull", constraint.parameter)
        graph.add_vertex(vkey, vertex.point, constraint=constraint, provenance=vertex.provenance)
    for ekey, edge in graph_near.edges.items():
        if edge.boundary in hull_names:
            graph.add_edge(ekey[0], ekey[1], path=edge.path, kind=edge.kind, points=edge.points, role="front", provenance="hull level set")
        else:
            graph.add_edge(ekey[0], ekey[1], path=edge.path, kind=edge.kind, points=edge.points, role=edge.role, provenance=edge.provenance, boundary=edge.boundary)
    for face in graph_near.faces:
        graph.add_face(face.corners, role=face.role, provenance=face.provenance)
    return graph


def _extend_closed(near, domain, outer, walls, hull_loop, hull_height, settings, wall_edge_style):
    """The closed far field: level loops around the hull, joined chain by chain."""
    import patch_graph as pg

    graph_near = near.graph
    hull_total = float(g2.total_length(hull_loop))
    hull_names = _hull_chain_names(graph_near)
    hull_vertices = set()
    for edge in graph_near.edges.values():
        if edge.boundary in hull_names:
            hull_vertices.update(edge.key)
    if len(hull_vertices) < 3:
        raise HullError("the near field has fewer than three vertices on the hull")
    station = {v: float(g2.closest_on_polyline(hull_loop, graph_near.vertices[v].point[None, :]).arclength[0]) % hull_total for v in hull_vertices}
    base_keys = sorted(hull_vertices, key=lambda v: station[v])
    fractions = [station[v] / hull_total for v in base_keys]
    count = len(base_keys)
    if any(b <= a for a, b in zip(fractions, fractions[1:])):
        raise HullError("two hull vertices share a station")
    outer_total = float(outer.curve.length())

    distance_to_outer = float(np.min(outer.curve.distance(hull_loop)))
    heights: list[float] = []
    while len(heights) < settings.max_levels:
        candidate = hull_height * settings.growth ** (len(heights) + 1)
        if candidate > settings.reach * (hull_height + distance_to_outer):
            break
        heights.append(candidate)
    if not heights:
        raise HullError("no level set fits between the hull and the outer boundary")
    notes: list[str] = []
    level_polys = []
    for height in heights:
        try:
            level_polys.append(cluster_level_set(walls, height, resolution=settings.resolution))
        except HullError as error:
            notes.append(f"level at height {height:.4g} not built ({error}); stopping at {len(level_polys)} level(s)")
            break
    if not level_polys:
        raise HullError("no admissible level set above the hull")
    heights = heights[:len(level_polys)]
    base_points = [graph_near.vertices[v].point for v in base_keys]
    base_stations = [station[v] for v in base_keys]
    level_points = []
    level_stations = []
    for poly_k in level_polys:
        stations_k = map_stations(hull_loop, base_stations, poly_k, walls, origin=base_points[0])
        if any((b - a) % float(g2.total_length(poly_k)) <= 0.0 for a, b in zip(stations_k, stations_k[1:])):
            raise HullError("the hull's vertices do not map onto a level in order")
        level_stations.append(stations_k)
        level_points.append([g2.sample_at_arclength(poly_k, [st])[0] for st in stations_k])
    kept = 0
    while kept < len(level_polys):
        lower = base_points if kept == 0 else level_points[kept - 1]
        upper = level_points[kept]
        bad = [n for n in range(count) if not g2.strictly_convex(np.asarray([lower[n], lower[(n + 1) % count], upper[(n + 1) % count], upper[n]]))]
        if bad:
            notes.append(f"level {kept + 1} at height {heights[kept]:.4g} gives a non-convex block in sector(s) {bad[:4]} of {count}; stopping at {kept} level(s)")
            break
        kept += 1
    if kept == 0:
        raise HullError("the first level above the hull gives non-convex blocks: " + "; ".join(notes[-1:]))
    levels = kept
    level_polys, level_points, level_stations, heights = level_polys[:levels], level_points[:levels], level_stations[:levels], heights[:levels]

    graph = _copy_near_field(graph_near, domain, pg)

    def key_level(k, n):
        return ("hull", "level", k, int(n))

    def key_outer(n):
        return ("hull", "outer", int(n))

    for k in range(1, levels + 1):
        for n in range(count):
            graph.add_vertex(key_level(k, n), level_points[k - 1][n], constraint=pg.Constraint("guide", f"front:hull:{k}", float(n)), provenance="level set")
    outer_loop = outer.curve.loop()
    outer_stations = [st % outer_total for st in map_stations(hull_loop, base_stations, outer_loop, walls, origin=outer_loop[0])]
    # The hull's chain breaks were placed by the inverse of this map, so they
    # land on the outer chain breaks again up to the maps' sampling; snap.
    breaks = [chain.start % outer_total for chain in outer.chains]
    outer_stations = [
        next((value for value in breaks if min(abs(st - value), outer_total - abs(st - value)) <= 1.0e-3 * outer_total), st)
        for st in outer_stations
    ]
    for n, st in enumerate(outer_stations):
        graph.add_vertex(key_outer(n), outer.curve.point_at(st), constraint=pg.Constraint("chain", outer.chain_at(st + 1e-9 * outer_total).name, st), provenance="far-field station")

    def line(first, second, role, provenance):
        graph.add_edge(first, second, path=np.asarray([graph.vertices[first].point, graph.vertices[second].point]), role=role, provenance=provenance)

    def poly(first, second, path, role, provenance, boundary=None, kind=None, points=None):
        path = np.asarray(path, dtype=np.float64).copy()
        path[0] = graph.vertices[first].point
        path[-1] = graph.vertices[second].point
        if kind is None:
            interior = path[1:-1]
            kind = "line" if len(interior) == 0 else "polyLine"
            points = tuple((float(x), float(y)) for x, y in interior)
        graph.add_edge(first, second, path=path, kind=kind, points=points, role=role, provenance=provenance, boundary=boundary)

    for n in range(count):
        m = (n + 1) % count
        for k in range(1, levels + 1):
            a, b = key_level(k, n), key_level(k, m)
            piece = g2.loop_section(level_polys[k - 1], level_stations[k - 1][n], level_stations[k - 1][m], forward=True)
            poly(a, b, piece, "front", f"level set {k}")
            lower_a = base_keys[n] if k == 1 else key_level(k - 1, n)
            lower_b = base_keys[m] if k == 1 else key_level(k - 1, m)
            line(lower_a, a, "core_spoke", "level spoke")
            line(lower_b, b, "core_spoke", "level spoke")
            graph.add_face((lower_a, lower_b, b, a), role="core", provenance=f"hull level {k} block")
        a, b = key_level(levels, n), key_level(levels, m)
        oa, ob = key_outer(n), key_outer(m)
        line(a, oa, "core_spoke", "far-field spoke")
        line(b, ob, "core_spoke", "far-field spoke")
        sa, sb = outer_stations[n], outer_stations[m]
        middle = (sa + 0.5 * ((sb - sa) % outer_total)) % outer_total
        chain = outer.chain_at(middle)
        path = outer.curve.section(sa, sb, forward=True)
        curve = outer.curve.edge_curve(sa, sb, forward=True, style=wall_edge_style)
        poly(oa, ob, path, "wall", "supplied point list", boundary=chain.name, kind=curve.kind, points=curve.points)
        graph.add_face((a, b, ob, oa), role="core", provenance="far-field sector")

    result_record = {
        "hull_height": float(hull_height), "hull_vertices": count, "closed": True,
        "levels": levels, "heights": [float(h) for h in heights],
        "distance_to_outer": distance_to_outer, "exit_wake": None,
    }
    return HullResult(graph, result_record, notes)
