"""Single-body C-grid: level sets of the body and its wake, no medial ring.

For one body with a sharp trailing edge in a C-shaped far field the annular
construction is the wrong scaffold.  Its one ring sits at half the far-field
distance, so every core block is a pie slice from a short wall section to a
ring section many chords long, and behind the trailing edge the body's level
sets curve round the edge while a C-grid's continue straight along the wake.
What a C-grid actually is - the hand-built A-airfoil mesh shows it - is the
family of level sets of the *slit body*, the wall together with its wake line:

    wall  ->  band front  ->  further level sets at growing heights  ->  far field

with the wake line cutting every level open.  Around the body a level set is
the wall's own clearance-limited offset; along the wake it is a straight line
parallel to the wake; the two meet at the trailing edge on the *mitre* of the
wall side and the wake, so the chain of mitre points from the edge outwards is
the cross-wake line every level ends on.  The far field is the outer boundary
itself: the outermost level set is joined to it by straight spokes, the body
part mapped onto the cap by arc-length fraction, and the two corners of the
outlet are single-block corners.  Blocks by kind:

    band        (gate_i, gate_i+1, F1_i+1, F1_i)                    wall to level 1
    level k     (Fk-1_i, Fk-1_i+1, Fk_i+1, Fk_i)                    level k-1 to k
    wake k      (in_k-1, W_k-1, W_k, in_k) and the same below      the wake band
    far body    (FK_i, FK_i+1, O_i+1, O_i)                          level K to the cap
    far wake    (in_K, W_K, C_top, J_top) and the same below       leg, outlet piece

``in_k``/``out_k`` are the mitre points at height ``H_k`` above and below the
edge, ``W_k`` where the level-k wake line meets the outlet, ``O_i`` the cap
stations, ``J`` the cap-leg joins and ``C`` the outlet corners.  The trailing
edge carries four blocks; the outlet corners hold one block each (index +1),
which with the edge's -2 keeps the annulus at total index zero.

The gates come from the annular layout (features, turning and band-shape
refinement); the level sets from ``layers.build_front`` with the wake mitre
as the seam directions; the wake line from ``wake.plan``.  Everything is
validated by the same graph checks, coverage, session emission and sampled
grids as the annular result, and the pipeline commits the C-grid only when
that whole chain passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
import patch_graph as pg
import wake as wake_module


@dataclass(frozen=True)
class CGridOptions:
    # Height ratio between successive level sets.
    growth: float = 3.0
    # No level set higher than this fraction of the smallest distance from
    # the wall to the outer boundary.
    reach: float = 0.35
    # At most this many level sets above the band.
    max_levels: int = 4

    def __post_init__(self) -> None:
        if not math.isfinite(self.growth) or self.growth <= 1.0:
            raise ValueError("the level growth must be finite and above 1")
        if not math.isfinite(self.reach) or not 0.0 < self.reach < 1.0:
            raise ValueError("the reach must lie strictly between 0 and 1")
        if self.max_levels < 0:
            raise ValueError("max_levels must be non-negative")


@dataclass
class CGridResult:
    graph: pg.PatchGraph | None
    record: dict = field(default_factory=dict)
    fronts: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CGridError(pg.GraphError):
    """The C-grid does not apply here; the reason is the message.

    ``intervals`` are the wall-station intervals ``(start, end)`` of the band
    blocks that were inadmissible, so the caller can cut the body's gate
    layout exactly there and try again; ``sharp`` marks a failure at a sharp
    wall feature, where a cut cannot help.
    """

    def __init__(self, message: str, *, intervals=(), sharp: bool = False):
        super().__init__(message)
        self.intervals = tuple((float(a), float(b)) for a, b in intervals)
        self.sharp = bool(sharp)


# ---------------------------------------------------------------------------
# Applicability and geometry helpers
# ---------------------------------------------------------------------------


def mitre_directions(loop: np.ndarray, index: int, wake_direction, fluid_sign: float):
    """Offset directions of the two seam points where wall sides meet the wake.

    Each direction is scaled so that ``vertex + h * direction`` lies at
    distance ``h`` from its wall side and from the wake line: the bisector of
    the fluid sub-sector between that side and the wake, over the sine of half
    that sub-sector.  ``incoming`` is the side the loop arrives on.
    """
    directions = g2.segment_directions(loop)
    count = len(loop) - 1
    incoming = directions[(index - 1) % count]
    outgoing = directions[index % count]
    w = np.asarray(wake_direction, dtype=np.float64)
    w = w / np.linalg.norm(w)
    # Fluid normals of the two sides at the edge.
    normal_in = -fluid_sign * np.array([-incoming[1], incoming[0]])
    normal_out = -fluid_sign * np.array([-outgoing[1], outgoing[0]])
    result = []
    for side, normal in ((-incoming, normal_in), (outgoing, normal_out)):
        bisector = side + w
        if np.linalg.norm(bisector) < 1e-12:
            bisector = normal.copy()
        bisector = bisector / np.linalg.norm(bisector)
        # The fluid sub-sector is on the side of the wake line where the
        # wall side's fluid normal points.
        if (w[0] * bisector[1] - w[1] * bisector[0]) * (w[0] * normal[1] - w[1] * normal[0]) < 0.0:
            bisector = -bisector
        sine = abs(float(side[0] * bisector[1] - side[1] * bisector[0]))
        if sine < 1e-6:
            raise CGridError("the wake leaves the trailing edge along a wall side")
        result.append(bisector / sine)
    return result[0], result[1]


def _outer_chain_breaks(site) -> dict[str, float]:
    return {chain.name: chain.start for chain in site.chains}


def _station_of(site, point) -> float:
    return float(site.curve.closest(np.asarray(point, dtype=np.float64)[None, :]).arclength[0])


def _outer_section(site, first: float, second: float, style: str):
    path = site.curve.section(first, second, forward=True)
    curve = site.curve.edge_curve(first, second, forward=True, style=style)
    return path, curve


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build(layout, domain, metric, wake_record: dict, *, layer_options=None,
          options: CGridOptions | None = None, wall_edge_style: str = "polyLine") -> CGridResult:
    """Build the single-body C-grid or raise ``CGridError`` with the reason."""
    from dataclasses import replace as _replace

    settings = options or CGridOptions()
    layer_settings = _replace(layer_options or layer_module.LayerOptions(), inscribed_cap=False)
    diagram = layout.diagram
    if len(layout.cells) != 2:
        raise CGridError("the C-grid producer takes exactly one body")
    cell_index = wake_record["cell"]
    cell = layout.cells[cell_index]
    site = diagram.sites[cell.site]
    outer = diagram.sites[len(diagram.sites) - 1]
    names = [chain.name for chain in outer.chains]
    roles = {chain.name: chain.role for chain in outer.chains}
    if len(outer.chains) != 4 or list(roles.values()).count("outlet") != 1:
        raise CGridError("the C-grid needs a four-chain outer boundary with one outlet: cap, leg, outlet, leg")
    outlet_index = next(i for i, name in enumerate(names) if roles[name] == "outlet")
    leg_before = outer.chains[(outlet_index - 1) % 4]
    leg_after = outer.chains[(outlet_index + 1) % 4]
    cap = outer.chains[(outlet_index + 2) % 4]
    outlet = outer.chains[outlet_index]
    total_outer = outer.curve.length()
    # Stations of the four boundary vertices on the outer curve.
    c_bot = outlet.start                       # leg_before -> outlet
    c_top = (outlet.start + outlet.length) % total_outer   # outlet -> leg_after
    j_top = (leg_after.start + leg_after.length) % total_outer  # leg_after -> cap
    j_bot = cap.start + cap.length             # cap -> leg_before
    j_bot %= total_outer

    # Gates on the wall in loop order, starting at the trailing edge.  The
    # layout's gates supply the stations; the edge itself is inserted here,
    # since the annular relaxation may have released its pin, and any gate
    # within half a percent of the perimeter of it is dropped.
    te_station = float(wake_record["wall_station"])
    total_wall = site.curve.length()

    def gap(a: float, b: float) -> float:
        return min((a - b) % total_wall, (b - a) % total_wall)

    stations = sorted(
        {float(cut.wall_station) for cut in layout.cuts[cell_index] if gap(cut.wall_station, te_station) > 0.005 * total_wall}
    )
    stations = sorted(stations + [te_station])
    te_order = stations.index(te_station)
    order = [(te_order + k) % len(stations) for k in range(len(stations))]
    stations = [stations[i] for i in order]          # stations[0] is the edge
    count = len(stations)
    if count < 4:
        raise CGridError("the body has fewer than four gates")

    loop = site.curve.loop()
    direction = np.asarray(wake_record["direction"], dtype=np.float64)
    te_point = np.asarray(wake_record["point"], dtype=np.float64)
    te_index = int(wake_record["vertex"])
    u_in, u_out = mitre_directions(loop, te_index, direction, site.curve.fluid_sign)

    # Level heights: the band, then geometric growth up to the reach.
    distance_to_outer = float(np.min(outer.curve.distance(loop)))
    heights = [float(metric.layer_height)]
    while len(heights) <= settings.max_levels:
        candidate = heights[-1] * settings.growth
        if candidate > settings.reach * distance_to_outer:
            break
        heights.append(candidate)

    # Level sets of the wall with the wake mitre as the seam at the edge.
    wall_loops = [loop_.points() for loop_ in domain.holes]
    caps = [layer_settings.clearance_fraction * distance_to_outer] * count
    fronts: list[layer_module.Front] = []
    notes: list[str] = []
    for level, height in enumerate(heights, start=1):
        # The band is judged block by block as always.  A higher level set is
        # the pure clearance-limited offset: its blocks lie between two
        # fronts, not between wall and front, so the band checks do not apply
        # and the level blocks are judged below.
        exempt = {0, count - 1} if level == 1 else set(range(count))
        try:
            front = layer_module.build_front(
                loop, stations, fluid_sign=site.curve.fluid_sign, wall_loops=wall_loops,
                requested=height, options=layer_settings, obstacles=[],
                station_caps=caps, floor_height=min(layer_module.floor_height(layer_settings, metric), 0.5 * height),
                exempt_orders=exempt, scaffold=None, site=cell.site, name=f"{site.name} level {level}",
                allow_scale=False, seam_directions={0: (u_in, u_out)},
            )
        except layer_module.LayerError as error:
            if level == 1:
                raise CGridError(
                    f"no band front: {error}",
                    intervals=[(stations[o % count], stations[(o + 1) % count]) for o in error.orders],
                    sharp=error.sharp,
                )
            notes.append(f"level {level} at height {height:.4g} not admissible ({error}); stopping at {level - 1} levels")
            break
        if not front.is_seam(0):
            raise CGridError("the trailing edge carries no seam; the wake cannot start there")
        # Higher levels are judged after the vertex mapping below.
        fronts.append(front)
    levels = len(fronts)
    if levels == 0:
        raise CGridError("no admissible level set")

    # --- open level polylines and the vertex map ---------------------------
    # Level k is the slit body's level set: the wall's offset from the out
    # mitre round the body to the in mitre, cut where it would enter the wake
    # band.  Level 1 keeps the wall-normal correspondence of its gates (the
    # band must be orthogonal); every higher level places its vertices at the
    # same arc-length fractions as level 1, so a sector never narrows with
    # height - carried along wall normals, the sector beside the trailing
    # edge closes as the surface offsets converge on the wake offsets.
    def open_level(front: layer_module.Front, height: float) -> np.ndarray:
        """The slit body's level set: the wall offset minus what lies inside
        the wake band, from the out mitre round the body to the in mitre."""
        te_idx = front.gate_indices[0]
        poly = np.vstack([front.front[te_idx:-1], front.front[:te_idx + 1]])  # starts and ends at the raw edge offset
        out_m = np.asarray(front.point_at(0, "out"), dtype=np.float64)
        in_m = np.asarray(front.point_at(0, "in"), dtype=np.float64)
        # Distance to the wake ray from the edge: points closer than the
        # height are nearer the wake than the wall and belong to the wake's
        # offset, which is the straight wake front, not to this piece.
        rel = poly - te_point
        along = rel @ direction
        across = np.abs(rel[:, 0] * direction[1] - rel[:, 1] * direction[0])
        to_ray = np.where(along >= 0.0, across, np.linalg.norm(rel, axis=1))
        keep = to_ray >= (1.0 - 1e-6) * height
        # Only the runs adjacent to the edge are trimmed; the far side of the
        # body is never inside the band.
        i = 0
        while i < len(poly) and not keep[i]:
            i += 1
        j = len(poly) - 1
        while j >= 0 and not keep[j]:
            j -= 1
        core = poly[i:j + 1] if i <= j else poly[:0]
        return np.vstack([out_m, core, in_m])

    def fraction_positions(level_poly: np.ndarray, points) -> list[float]:
        total = g2.total_length(level_poly)
        return [float(g2.closest_on_polyline(level_poly, np.asarray(point)[None, :]).arclength[0]) / total for point in points]

    level_polys = [open_level(front, height) for front, height in zip(fronts, heights)]
    # Level 1 vertices: the gates' normal offsets, mitres at the ends.
    first = fronts[0]
    level_points: list[list[np.ndarray]] = [
        [np.asarray(first.point_at(0, "out"))] + [np.asarray(first.front[first.gate_indices[i]]) for i in range(1, count)] + [np.asarray(first.point_at(0, "in"))]
    ]
    fractions = fraction_positions(level_polys[0], level_points[0])
    fractions[0], fractions[-1] = 0.0, 1.0
    if any(b <= a for a, b in zip(fractions, fractions[1:])):
        raise CGridError("the band front's gate points are not ordered along the front")
    for poly_k in level_polys[1:]:
        total = g2.total_length(poly_k)
        level_points.append([g2.sample_at_arclength(poly_k, [f * total])[0] for f in fractions])
    # Judge every level's blocks against the level below; drop levels that fail.
    kept = 1
    while kept < levels:
        lower, upper = level_points[kept - 1], level_points[kept]
        bad = [
            n for n in range(count)
            if not g2.strictly_convex(np.asarray([lower[n], lower[n + 1], upper[n + 1], upper[n]]))
        ]
        if bad:
            notes.append(
                f"level {kept + 1} at height {heights[kept]:.4g} gives a non-convex block in sector(s) {bad[:4]} "
                f"of {count}; stopping at {kept} levels"
            )
            break
        kept += 1
    levels = kept
    fronts = fronts[:levels]
    level_polys = level_polys[:levels]
    level_points = level_points[:levels]
    fraction_stations = [[f * g2.total_length(poly_k) for f in fractions] for poly_k in level_polys]

    def level_piece(k: int, n: int) -> np.ndarray:
        """Level-k polyline between vertices n and n+1 (0 = out mitre)."""
        a, b = fraction_stations[k - 1][n], fraction_stations[k - 1][n + 1]
        piece = g2.polyline_section(level_polys[k - 1], a, b)
        piece[0] = level_points[k - 1][n]
        piece[-1] = level_points[k - 1][n + 1]
        return piece

    graph = pg.PatchGraph(
        euler_characteristic=domain.euler_characteristic,
        boundary_roles={chain.name: chain.role for chain in domain.chains()},
    )

    def key_gate(i):
        return ("gate", cell_index, "station", i)

    def key_level(k, n):
        # n runs 0 (out mitre) .. count (in mitre) along the open level.
        if n == 0:
            return ("front", cell_index, "level", k, 0, "out")
        if n == count:
            return ("front", cell_index, "level", k, 0, "in")
        return ("front", cell_index, "level", k, n)

    def key_wake(k, side):
        return ("wake", cell_index, "outlet", k, side)

    def key_outer(label):
        return ("outer", "corner", label) if isinstance(label, str) else ("outer", "cap", int(label[1]))

    outer_station: dict = {}

    # Wall gates in wall order: gate i is vertex n = i of every level for
    # 1 <= i < count; the edge (i = 0) is vertex 0 on the out side and vertex
    # count on the in side.
    for i, station in enumerate(stations):
        graph.add_vertex(key_gate(i), site.curve.point_at(station),
                         constraint=pg.Constraint("chain", site.name, station), provenance="gate")
    mitre_in = [te_point]
    mitre_out = [te_point]
    for k in range(1, levels + 1):
        for n in range(count + 1):
            point = level_points[k - 1][n]
            if n in (0, count):
                graph.add_vertex(key_level(k, n), point, constraint=pg.Constraint("guide", f"cross-wake:{site.name}", float(k)), provenance="wake mitre")
            else:
                graph.add_vertex(key_level(k, n), point, constraint=pg.Constraint("guide", f"front:{site.name}:{k}", float(n)), provenance="level set" if k > 1 else "layer front")
        mitre_out.append(np.asarray(level_points[k - 1][0]))
        mitre_in.append(np.asarray(level_points[k - 1][count]))

    # Wake exits: the level-k wake lines through the mitre points meet the outer boundary.
    outer_loop = outer.curve.loop()
    exits: dict[tuple, tuple[np.ndarray, float]] = {}
    for k in range(levels + 1):
        for side, chain_points in (("in", mitre_in), ("out", mitre_out)):
            origin = chain_points[k]
            hits = wake_module._ray_crossings(origin, direction, outer_loop)
            if not hits:
                raise CGridError(f"the level {k} wake line does not reach the outer boundary")
            t, arclength, _segment = hits[0]
            point = origin + t * direction
            station = _station_of(outer, point)
            if outer.chain_at(station).name != outlet.name:
                raise CGridError(f"the level {k} wake line leaves the domain through {outer.chain_at(station).name!r}, not the outlet")
            exits[(k, side)] = (point, station)
    # Along the outlet (forward, from c_bot to c_top) the exits must read
    # in_K ... in_1, W, out_1 ... out_K: the in side faces c_bot.
    outlet_order = [exits[(k, "in")][1] for k in range(levels, 0, -1)] + [exits[(0, "in")][1]] + [exits[(k, "out")][1] for k in range(1, levels + 1)]
    if any(b <= a for a, b in zip(outlet_order, outlet_order[1:])):
        raise CGridError("the wake lines are not ordered along the outlet; a wake line crosses another")
    if not (c_bot < min(outlet_order) and max(outlet_order) < c_top):
        raise CGridError("a wake line leaves the outlet beyond its corners")
    for k in range(levels + 1):
        for side in ("in", "out"):
            point, station = exits[(k, side)]
            label = ("W", k, side) if k else ("W", 0)
            if k == 0 and side == "out":
                continue
            key = key_wake(k, side) if k else key_wake(0, "centre")
            graph.add_vertex(key, point, constraint=pg.Constraint("chain", outlet.name, station), provenance="wake exit")
            outer_station[key] = station
    # Outer corners and joins, and the cap stations for the body sectors.
    for label, station in (("c_top", c_top), ("c_bot", c_bot), ("j_top", j_top), ("j_bot", j_bot)):
        graph.add_vertex(key_outer(label), outer.curve.point_at(station),
                         constraint=pg.Constraint("chain", outer.chain_at(station + 1e-9 * total_outer).name, station), provenance="outer corner")
        outer_station[key_outer(label)] = station
    # Both loops are anticlockwise: leaving the edge along the out side, the
    # body is on the left, so the out side faces the leg the outer loop
    # reaches first after the outlet - leg_after and j_top - and the in side
    # faces leg_before and j_bot.  The cap chain runs forward from j_top to
    # j_bot, in step with the body from out to in; fraction 0 is j_top.
    cap_length = cap.length
    cap_stations = [(cap.start + f * cap_length) % total_outer for f in fractions]
    outer_keys = []
    for n, station in enumerate(cap_stations):
        if n == 0:
            outer_keys.append(key_outer("j_top"))
        elif n == len(cap_stations) - 1:
            outer_keys.append(key_outer("j_bot"))
        else:
            key = key_outer(("cap", n))
            graph.add_vertex(key, outer.curve.point_at(station), constraint=pg.Constraint("chain", cap.name, station), provenance="far-field station")
            outer_keys.append(key)
    cap_station_of = dict(zip(outer_keys, cap_stations))

    # --- edges and faces ------------------------------------------------
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

    # Wall edges and band blocks, then level blocks.  Sector n runs from
    # vertex n to n+1 along each level; on the wall that is gate n to gate n+1
    # with the edge as gate 0 = gate count.
    for n in range(count):
        i, j = n, (n + 1) % count
        wall_path = layer_module.ring_slice(fronts[0].wall, fronts[0].gate_indices[i], fronts[0].gate_indices[j])
        kind, points = ("line", ()) if len(wall_path) <= 2 else (wall_edge_style, tuple((float(x), float(y)) for x, y in wall_path[1:-1]))
        poly(key_gate(i), key_gate(j), wall_path, "wall", "supplied point list", boundary=site.section_chain(stations[i], stations[j]).name, kind=kind, points=points)
        for k in range(1, levels + 1):
            a, b = key_level(k, n), key_level(k, n + 1)
            poly(a, b, level_piece(k, n), "front", f"level set {k}" if k > 1 else "clearance-limited offset")
            lower_a = key_gate(i) if k == 1 else key_level(k - 1, n)
            lower_b = key_gate(j) if k == 1 else key_level(k - 1, n + 1)
            spoke_role = "layer_spoke" if k == 1 else "core_spoke"
            line(lower_a, a, spoke_role, "wall normal" if k == 1 else "level spoke")
            line(lower_b, b, spoke_role, "wall normal" if k == 1 else "level spoke")
            graph.add_face((lower_a, lower_b, b, a), role="layer" if k == 1 else "core", provenance="boundary-layer band" if k == 1 else f"level {k} block")
    # Wake band blocks, per level and side.
    centre_keys = [key_gate(0)] + [key_level(k, count) for k in range(1, levels + 1)]
    centre_out = [key_gate(0)] + [key_level(k, 0) for k in range(1, levels + 1)]
    exit_keys_in = [key_wake(0, "centre")] + [key_wake(k, "in") for k in range(1, levels + 1)]
    exit_keys_out = [key_wake(0, "centre")] + [key_wake(k, "out") for k in range(1, levels + 1)]
    line(key_gate(0), key_wake(0, "centre"), "wake", "wake separatrix")
    for k in range(1, levels + 1):
        for chain_keys, exit_keys in ((centre_keys, exit_keys_in), (centre_out, exit_keys_out)):
            line(chain_keys[k], exit_keys[k], "wake_front", f"wake level set {k}")
            a_station = outer_station[exit_keys[k - 1]]
            b_station = outer_station[exit_keys[k]]
            first, second = (a_station, b_station) if (b_station - a_station) % total_outer < total_outer / 2 else (b_station, a_station)
            path, curve = _outer_section(outer, first, second, wall_edge_style)
            if first == a_station:
                poly(exit_keys[k - 1], exit_keys[k], path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
            else:
                poly(exit_keys[k], exit_keys[k - 1], path, "wall", "supplied point list", boundary=outlet.name, kind=curve.kind, points=curve.points)
            graph.add_face((chain_keys[k - 1], exit_keys[k - 1], exit_keys[k], chain_keys[k]), role="wake", provenance=f"wake band level {k}")
    # Far field: body sectors to the cap, two wake-side blocks to the legs.
    top_keys_body = [key_level(levels, n) for n in range(count + 1)]
    for n in range(len(top_keys_body) - 1):
        a, b = top_keys_body[n], top_keys_body[n + 1]
        oa, ob = outer_keys[n], outer_keys[n + 1]
        line(a, oa, "core_spoke", "far-field spoke")
        line(b, ob, "core_spoke", "far-field spoke")
        sa, sb = cap_station_of[oa], cap_station_of[ob]
        # The cap runs forward from j_top to j_bot, so from oa to ob.
        path, curve = _outer_section(outer, sa, sb, wall_edge_style)
        poly(oa, ob, path, "wall", "supplied point list", boundary=cap.name, kind=curve.kind, points=curve.points)
        graph.add_face((a, b, ob, oa), role="core", provenance="far-field sector")
    # Wake-side far blocks: (out_K, W_K_out, c_top, j_top) and (in_K, W_K_in, c_bot, j_bot).
    for side_key, exit_key, corner, join, leg, from_exit_to_corner in (
        (key_level(levels, 0), key_wake(levels, "out"), key_outer("c_top"), key_outer("j_top"), leg_after, True),
        (key_level(levels, count), key_wake(levels, "in"), key_outer("c_bot"), key_outer("j_bot"), leg_before, False),
    ):
        exit_station = outer_station[exit_key]
        corner_station = outer_station[corner]
        join_station = outer_station[join]
        if from_exit_to_corner:
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

    record = {
        "site": site.name, "levels": levels, "heights": heights[:levels],
        "gates": count, "wake_direction": [float(v) for v in direction],
        "distance_to_outer": distance_to_outer,
        "mitre_in": [float(v) for v in u_in], "mitre_out": [float(v) for v in u_out],
    }
    return CGridResult(graph, record, fronts, notes)
