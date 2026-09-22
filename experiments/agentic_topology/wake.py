"""Wake separatrix: a C-grid cut from a sharp trailing edge to the outer boundary.

The annular construction closes a body's band around its trailing edge with a
*seam*: two front vertices, one per wall side, and a wedge block back to the
medial anchor.  At a trailing edge that wedge is the wrong element.  The flow
leaves along the wake, the two band blocks beside the edge should continue
downstream as a two-sided *wake band*, and the whole far-field core should be
cut open along the wake so the C-shaped block rows end on the outer boundary.

The construction here keeps the medial scaffold exactly as it is and adds one
straight scaffold curve, the wake line ``TE -> R -> W``: ``R`` is where the
wake crosses the body's ring, ``W`` where it leaves the domain.  ``R`` is an
ordinary ring anchor pinned on both sides (the trailing edge on the body, the
exit on the outer boundary), so the annular producer already writes the wake
beyond the ring as the far-field spoke ``R-W`` and gives the trailing edge its
seam.  What this module rewrites afterwards is the neighbourhood of that
anchor:

    wedge  (TE, f_out, R, f_in)            ->  two wake blocks
                                                (TE, R, R_in, f_in), (TE, f_out, R_out, R)
    core patches beside the seam           ->  the same patches, ending on the
                                                wake fronts f_in-R_in, f_out-R_out
    far-field patches beside W             ->  the same patches, ending on the
                                                wake fronts R_in-W_in, R_out-W_out,
                                                plus two wake blocks beyond the
                                                ring, (R, W, W_in, R_in), (R, R_out, W_out, W)

``R_in``/``R_out`` are where the two wake fronts - the seam points carried
downstream parallel to the wake - cross the ring, and ``W_in``/``W_out`` where
they meet the outer boundary.  The trailing edge ends up with four blocks
meeting at right angles, the wake fronts inherit the wall band's normal count
through the band spokes, and the wake line lies in the core spoke component.
Every vertex stays regular except the two front vertices at the edge (index
+1, as with the seam) and the edge itself, which now carries four blocks.

Everything is built on a copy and validated; the pipeline commits a wake only
when coverage, session emission and the sampled grids accept the whole
result, and a refused wake leaves the annular construction untouched with the
reason recorded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
import patch_graph as pg


@dataclass(frozen=True)
class WakeOptions:
    enabled: bool = False
    # A wall vertex carries a wake when the fluid sees at least this sector
    # there - a trailing edge or a cusp.  It must not be below the band seam
    # threshold, because the wake replaces the seam wedge.
    fluid_angle: float = 250.0
    # Fixed wake direction ``(dx, dy)`` for every wake, or ``None`` for the
    # bisector of each feature's fluid sector.  A fixed direction must lie
    # inside the sector.
    direction: tuple[float, float] | None = None
    # The wake fronts must cross the ring and the outer boundary at least this
    # fraction of the local band height away from the neighbouring cuts.
    clearance_ratio: float = 0.25
    # A second convex corner (fluid sector at least ``blunt_angle``) within
    # ``blunt_ratio`` of the perimeter along the wall makes the feature one
    # corner of a blunt base, not a trailing edge; a base needs its own
    # template and is refused.
    blunt_ratio: float = 0.02
    blunt_angle: float = 225.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.fluid_angle) or not 180.0 < self.fluid_angle < 360.0:
            raise ValueError("the wake fluid angle must be between 180 and 360 degrees")
        if self.direction is not None:
            vector = np.asarray(self.direction, dtype=np.float64)
            if vector.shape != (2,) or not np.all(np.isfinite(vector)) or np.linalg.norm(vector) <= 0.0:
                raise ValueError("a wake direction is a finite non-zero 2-vector")
            object.__setattr__(self, "direction", (float(vector[0]), float(vector[1])))
        if not math.isfinite(self.clearance_ratio) or self.clearance_ratio < 0.0:
            raise ValueError("the wake clearance ratio must be finite and non-negative")
        if not math.isfinite(self.blunt_ratio) or self.blunt_ratio < 0.0:
            raise ValueError("the blunt ratio must be finite and non-negative")
        if not math.isfinite(self.blunt_angle) or not 180.0 < self.blunt_angle < 360.0:
            raise ValueError("the blunt angle must be between 180 and 360 degrees")


@dataclass
class WakeResult:
    graph: pg.PatchGraph | None
    records: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def applied(self) -> list[dict]:
        return [item for item in self.records if item.get("applied")]


# ---------------------------------------------------------------------------
# Planning: which features get a wake, and where the wake line goes
# ---------------------------------------------------------------------------


def _ray_crossings(origin, direction, poly: np.ndarray) -> list[tuple[float, float, int]]:
    """Proper crossings of a ray with a polyline: ``(t, arc length, segment)``.

    ``t`` is the distance along the ray.  A crossing exactly at the origin is
    not counted, so a ray leaving a boundary vertex does not see that vertex.
    """
    poly = np.asarray(poly, dtype=np.float64)
    if len(poly) < 2:
        return []
    origin = np.asarray(origin, dtype=np.float64)
    direction = np.asarray(direction, dtype=np.float64)
    a = poly[:-1]
    e = poly[1:] - poly[:-1]
    denominator = direction[0] * e[:, 1] - direction[1] * e[:, 0]
    w = a - origin[None, :]
    safe = np.where(np.abs(denominator) > 0.0, denominator, 1.0)
    t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / safe
    u = (w[:, 0] * direction[1] - w[:, 1] * direction[0]) / safe
    lengths = np.linalg.norm(e, axis=1)
    scale = float(np.max(np.abs(poly))) + 1.0
    valid = (np.abs(denominator) > 1e-14 * scale) & (t > 1e-12 * scale) & (u >= 0.0) & (u < 1.0)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    found = [
        (float(t[index]), float(cumulative[index] + u[index] * lengths[index]), int(index))
        for index in np.flatnonzero(valid)
    ]
    found.sort()
    return found


def feature_directions(loop: np.ndarray, fluid_sign: float, threshold: float) -> list[tuple[int, np.ndarray]]:
    """Sharp convex wall vertices and the outward bisector of their fluid sector."""
    angles = layer_module.fluid_angles(loop, fluid_sign)
    directions = g2.segment_directions(loop)
    count = len(loop) - 1
    found = []
    for index in np.flatnonzero(angles >= threshold):
        incoming = directions[(index - 1) % count]
        outgoing = directions[index % count]
        bisector = incoming - outgoing
        norm = float(np.linalg.norm(bisector))
        if norm <= 0.0:
            continue
        found.append((int(index), bisector / norm))
    return found


def plan(layout, options: WakeOptions | None = None) -> list[dict]:
    """One record per sharp feature: the wake line, or the reason it has none."""
    settings = options or WakeOptions()
    diagram = layout.diagram
    outer_index = len(diagram.sites) - 1
    outer = diagram.sites[outer_index]
    outer_loop = outer.curve.loop()
    threshold = math.radians(settings.fluid_angle)
    records: list[dict] = []
    for cell_index, cell in enumerate(layout.cells):
        site = diagram.sites[cell.site]
        if site.curve.kind != "wall":
            continue
        loop = site.curve.loop()
        stations = g2.cumulative_length(loop)
        total = float(stations[-1])
        angles = layer_module.fluid_angles(loop, site.curve.fluid_sign)
        convex = np.flatnonzero(angles >= math.radians(settings.blunt_angle))
        for index, bisector in feature_directions(loop, site.curve.fluid_sign, threshold):
            point = loop[index]
            record = {
                "site": site.name,
                "cell": cell_index,
                "vertex": index,
                "wall_station": float(stations[index]),
                "point": [float(point[0]), float(point[1])],
                "fluid_angle_degrees": float(
                    math.degrees(layer_module.fluid_angles(loop, site.curve.fluid_sign)[index])
                ),
                "planned": False,
                "applied": False,
            }
            records.append(record)
            # A blunt base: another convex corner a few thousandths of the
            # perimeter away.  The wake would leave one corner of the base and
            # run along the other side's band.
            others = [
                int(other) for other in convex
                if other != index
                and min((stations[other] - stations[index]) % total,
                        (stations[index] - stations[other]) % total)
                <= settings.blunt_ratio * total
            ]
            if others:
                other = others[0]
                record["reason"] = (
                    f"blunt trailing edge: vertex {other} ({math.degrees(angles[other]):.1f} degrees) "
                    f"lies {min((stations[other] - stations[index]) % total, (stations[index] - stations[other]) % total):.4g} "
                    "along the wall; a base needs its own template"
                )
                continue
            direction = bisector
            if settings.direction is not None:
                direction = np.asarray(settings.direction) / np.linalg.norm(settings.direction)
                half = 0.5 * (math.radians(record["fluid_angle_degrees"]) - 1e-6)
                if float(np.dot(direction, bisector)) < math.cos(half):
                    record["reason"] = "the requested wake direction leaves the feature's fluid sector"
                    continue
            record["direction"] = [float(direction[0]), float(direction[1])]
            own = _ray_crossings(point, direction, cell.ring)
            if len(own) != 1:
                record["reason"] = (
                    f"the wake ray crosses the body's own ring {len(own)} times; exactly one is needed"
                )
                continue
            t_ring, ring_station, _segment = own[0]
            branch_index, position = cell.branch_station(ring_station)
            branch = diagram.branches[branch_index]
            if outer_index not in branch.pair:
                record["reason"] = (
                    f"the wake crosses the ring into the cell of "
                    f"{diagram.sites[[s for s in branch.pair if s != cell.site][0]].name!r}; "
                    "a wake ending on another body's front is not built yet"
                )
                continue
            exits = _ray_crossings(point, direction, outer_loop)
            exits = [item for item in exits if item[0] > t_ring]
            if not exits:
                record["reason"] = "the wake ray does not reach the outer boundary beyond the ring"
                continue
            t_exit, exit_arclength, _segment = exits[0]
            exit_point = point + t_exit * direction
            exit_station = float(outer.curve.closest(exit_point[None, :]).arclength[0])
            blocked = None
            for other in diagram.sites[:-1]:
                if other.index == cell.site:
                    continue
                hits = [item for item in _ray_crossings(point, direction, other.curve.loop()) if item[0] < t_exit]
                if hits:
                    blocked = f"the wake ray meets the body {other.name!r}"
                    break
            if blocked is None:
                for other_branch in diagram.branches:
                    if other_branch.index == branch_index:
                        continue
                    hits = [item for item in _ray_crossings(point, direction, other_branch.path) if item[0] < t_exit]
                    if hits:
                        blocked = (
                            f"the wake ray crosses medial branch {other_branch.index} before leaving the domain"
                        )
                        break
            if blocked is not None:
                record["reason"] = blocked
                continue
            record.update(
                {
                    "ring_branch": branch_index,
                    "ring_position": float(position),
                    "ring_station": float(ring_station),
                    "ring_point": [float(v) for v in point + t_ring * direction],
                    "exit_station": exit_station,
                    "exit_point": [float(v) for v in exit_point],
                    "exit_chain": outer.chain_at(exit_station).name,
                    "wake_length": float(t_exit),
                    "planned": settings.enabled,
                }
            )
            if not settings.enabled:
                record["reason"] = "wakes are disabled"
    return records


# ---------------------------------------------------------------------------
# Layout preparation: the wake anchor on the ring
# ---------------------------------------------------------------------------


def prepare(layout, records: list[dict]) -> list[str]:
    """Pin the wake anchor on a trial layout before the bands are built.

    The trailing edge's own feature anchor - projected onto the ring at the
    closest point - is replaced by an anchor exactly where the wake crosses the
    ring, pinned to the trailing edge on the body and to the exit point on the
    outer boundary.  The anchor is fixed, so the relaxation neither moves it
    nor its two gates.  Callers own the trial layout.
    """
    import block_layout

    diagram = layout.diagram
    outer_index = len(diagram.sites) - 1
    notes: list[str] = []
    for record in records:
        if not record.get("planned"):
            continue
        cell_index = record["cell"]
        cell = layout.cells[cell_index]
        site = diagram.sites[cell.site]
        total = site.curve.length()
        station = record["wall_station"]
        anchors = layout.anchors
        # Remove the feature's existing projected anchor at this wall station.
        for cut in layout.cuts[cell_index]:
            gap = (cut.wall_station - station) % total
            if min(gap, total - gap) > 1e-9 * total:
                continue
            anchor = cut.anchor
            if anchor.junction is not None:
                raise pg.GraphError("the trailing edge is a medial junction gate")
            if anchor.kind == "wake":
                raise pg.GraphError("the trailing edge already carries a wake anchor")
            anchors.by_branch[anchor.branch] = [
                item for item in anchors.by_branch[anchor.branch] if item is not anchor
            ]
            anchors._keys.discard(anchor.key)
        anchor = block_layout.Anchor(
            record["ring_branch"],
            record["ring_position"],
            "wake",
            None,
            (cell.site, station),
            extra_hint=(outer_index, record["exit_station"]),
            fixed=True,
        )
        # Stations are rebuilt from the anchor lists below, so the crowding
        # bookkeeping does not see the removed anchor any more.
        anchors._stations = {index: [] for index in range(len(layout.cells))}
        for other in layout.cuts:
            for cut in other:
                anchors._stations[cut.cell].append((cut.ring_station, cut.wall_station, cut.clearance))
        if not anchors.add(anchor, force=True):
            raise pg.GraphError("the wake anchor coincides with an existing ring cut")
        record["anchor_key"] = list(anchor.key)
        notes.append(
            f"{site.name}: wake from wall station {station:.6g} along "
            f"({record['direction'][0]:.3f}, {record['direction'][1]:.3f}) to "
            f"{record['exit_chain']!r}, ring branch {record['ring_branch']}"
        )
    layout.cuts = layout.anchors.cuts()
    layout.patches = block_layout.build_patches(diagram, layout.cells, layout.cuts)
    layout.refresh()
    return notes


def make_room(layout, records: list[dict], fronts, options: WakeOptions | None = None) -> list[str]:
    """Slide the ring anchors beside each wake clear of its band, if needed.

    The wake fronts cross the ring a band height to either side of the wake
    anchor.  A neighbouring anchor whose ring station lies inside that span -
    typically a far-field corner whose closest ring point is just behind the
    trailing edge, because the medial ring turns sharply there - is slid along
    its branch until the crossing is ``clearance_ratio`` band heights clear of
    it, provided the anchor is movable and its own next neighbour keeps the
    layout's ring separation.  Callers rebuild the bands when anything moved.
    """
    import block_layout

    settings = options or WakeOptions()
    diagram = layout.diagram
    notes: list[str] = []
    moved = False
    for record in records:
        if not record.get("planned") or "anchor_key" not in record:
            continue
        key = tuple(record["anchor_key"])
        cell_index = record["cell"]
        cell = layout.cells[cell_index]
        cuts = layout.cuts[cell_index]
        position = next((i for i, cut in enumerate(cuts) if cut.anchor.key == key), None)
        front = fronts.get(cell_index)
        if position is None or front is None or not front.is_seam(position):
            continue
        wake_cut = cuts[position]
        direction = np.asarray(record["direction"], dtype=np.float64)
        normal = np.array([-direction[1], direction[0]])
        total = cell.ring_length
        height = 0.0
        crossings: dict[str, float] = {}
        for label in ("in", "out"):
            origin = front.point_at(position, label)
            height = max(height, abs(float(np.dot(origin - wake_cut.wall_point, normal))))
            hits = _ray_crossings(origin, direction, cell.ring)
            if not hits:
                crossings[label] = wake_cut.ring_station
                continue
            # The crossing that belongs to this wake is the one nearest the
            # wake anchor along the ring.
            station = min(
                (float(arclength) for _t, arclength, _s in hits),
                key=lambda value: min((value - wake_cut.ring_station) % total, (wake_cut.ring_station - value) % total),
            )
            crossings[label] = station
        count = len(cuts)
        for label, step in (("in", -1), ("out", 1)):
            neighbour = cuts[(position + step) % count]
            beyond = cuts[(position + 2 * step) % count]
            # The wake front's ring crossing becomes a ring vertex, and the
            # ring piece from it to the neighbouring anchor becomes a block
            # edge: the neighbour must keep the layout's own ring separation
            # from the crossing, not merely stay outside the band.
            margin = max(
                settings.clearance_ratio * height,
                layout.anchors.ring_separation * min(neighbour.clearance, wake_cut.clearance),
            )
            if step < 0:
                have = (wake_cut.ring_station - neighbour.ring_station) % total
                need = (wake_cut.ring_station - crossings[label]) % total + margin
            else:
                have = (neighbour.ring_station - wake_cut.ring_station) % total
                need = (crossings[label] - wake_cut.ring_station) % total + margin
            if have >= need:
                continue
            anchor = neighbour.anchor
            if not anchor.movable:
                raise pg.GraphError(
                    f"a {anchor.kind} anchor sits on the ring inside the wake band of {record['site']}"
                )
            target = (wake_cut.ring_station - need) % total if step < 0 else (wake_cut.ring_station + need) % total
            if step < 0:
                room = (target - beyond.ring_station) % total
            else:
                room = (beyond.ring_station - target) % total
            separation = layout.anchors.ring_separation * min(neighbour.clearance, beyond.clearance)
            if room < separation or beyond is wake_cut:
                raise pg.GraphError(
                    f"no room on the ring for the wake band of {record['site']}: sliding the "
                    f"{anchor.kind} anchor {need - have:.4g} along the ring would crowd the next cut"
                )
            branch, branch_position = cell.branch_station(target)
            if branch != anchor.branch:
                raise pg.GraphError(
                    f"the {anchor.kind} anchor beside the wake of {record['site']} would have to "
                    "leave its medial branch"
                )
            anchor.position = float(branch_position)
            moved = True
            notes.append(
                f"{record['site']}: {anchor.kind} anchor slid {need - have:.4g} along ring branch "
                f"{branch} to clear the wake band"
            )
    if moved:
        layout.cuts = layout.anchors.cuts()
        layout.patches = block_layout.build_patches(diagram, layout.cells, layout.cuts)
        layout.refresh()
    return notes


# ---------------------------------------------------------------------------
# Graph rewrite
# ---------------------------------------------------------------------------


def _face_with_corners(graph: pg.PatchGraph, corners) -> pg.PGFace | None:
    wanted = set(corners)
    for face in graph.faces:
        if set(face.corners) == wanted:
            return face
    return None


def _front_keys(graph: pg.PatchGraph, cell: int, anchor_key) -> tuple[tuple, tuple]:
    single = ("front", cell, *anchor_key)
    if single in graph.vertices:
        return single, single
    incoming = ("front", cell, *anchor_key, "in")
    outgoing = ("front", cell, *anchor_key, "out")
    if incoming in graph.vertices and outgoing in graph.vertices:
        return incoming, outgoing
    raise pg.GraphError(f"cell {cell} has no front vertex at {anchor_key!r}")


def _cyclic_between(value: float, low: float, high: float, total: float) -> bool:
    """True when ``value`` lies strictly inside the forward interval low->high."""
    span = (high - low) % total
    if span <= 0.0:
        span = total
    offset = (value - low) % total
    return 0.0 < offset < span


def _section_crossing(origin, direction, section: np.ndarray, *, clearance: float):
    """The single crossing of a ray with a polyline section, and its arc length.

    The crossing must lie at least ``clearance`` inside the section from both
    ends; otherwise the new vertex would sit on top of a neighbouring cut.
    """
    hits = _ray_crossings(origin, direction, section)
    if len(hits) != 1:
        return None, f"{len(hits)} crossings"
    t, arclength, _segment = hits[0]
    total = g2.total_length(section)
    if arclength < clearance or total - arclength < clearance:
        return None, "the crossing is too close to a neighbouring cut"
    return (t, arclength), ""


def apply(layout, graph: pg.PatchGraph, records: list[dict], fronts, *, wall_edge_style="polyLine") -> WakeResult:
    """Return a new graph with every planned wake written; never mutate."""
    result = WakeResult(graph, [dict(item) for item in records])
    current = graph
    for record in result.records:
        if not record.get("planned"):
            continue
        before = {_problem_signature(item) for item in current.problems()}
        try:
            candidate = _write_wake(layout, current, record, fronts, wall_edge_style)
        except pg.GraphError as error:
            record["applied"] = False
            record["reason"] = f"could not be written: {error}"
            continue
        after = candidate.problems()
        fresh = [item for item in after if _problem_signature(item) not in before]
        if fresh:
            record["applied"] = False
            record["reason"] = (
                "the wake introduced graph problems and was reverted: "
                + "; ".join(sorted({item["kind"] for item in fresh}))
            )
            record["problems"] = fresh[:4]
            continue
        record["applied"] = True
        record.pop("reason", None)
        current = candidate
        result.notes.append(
            f"wake at {record['site']} vertex {record['vertex']}: four blocks at the "
            f"trailing edge, wake band to {record['exit_chain']!r}"
        )
    result.graph = current
    return result


def _write_wake(layout, graph: pg.PatchGraph, record: dict, fronts, wall_edge_style: str) -> pg.PatchGraph:
    diagram = layout.diagram
    cell_a = record["cell"]
    cell_c = len(layout.cells) - 1
    if diagram.sites[layout.cells[cell_c].site].curve.kind == "wall":
        raise pg.GraphError("the last cell is not the outer boundary")
    site_a = diagram.sites[layout.cells[cell_a].site]
    site_c = diagram.sites[layout.cells[cell_c].site]
    key = tuple(record["anchor_key"])
    clone = graph.copy()

    cuts_a = layout.cuts[cell_a]
    cuts_c = layout.cuts[cell_c]
    position_a = next((i for i, cut in enumerate(cuts_a) if cut.anchor.key == key), None)
    position_c = next((i for i, cut in enumerate(cuts_c) if cut.anchor.key == key), None)
    if position_a is None or position_c is None:
        raise pg.GraphError("the wake anchor is missing from one of its cells")
    wake_cut_a = cuts_a[position_a]
    wake_cut_c = cuts_c[position_c]
    prev_a = cuts_a[(position_a - 1) % len(cuts_a)]
    next_a = cuts_a[(position_a + 1) % len(cuts_a)]
    prev_c = cuts_c[(position_c - 1) % len(cuts_c)]
    next_c = cuts_c[(position_c + 1) % len(cuts_c)]
    if prev_a.anchor.key != prev_c.anchor.key or next_a.anchor.key != next_c.anchor.key:
        raise pg.GraphError(
            "the ring neighbours of the wake anchor differ between the body and the "
            "outer boundary; the wake meets the ring beside a junction"
        )
    if prev_a.anchor.key == next_a.anchor.key:
        raise pg.GraphError("the body cell has too few cuts for a wake")

    front = fronts.get(cell_a)
    if front is None:
        raise pg.GraphError("the body has no boundary-layer band; a wake continues a band")
    order = position_a
    if not front.is_seam(order):
        raise pg.GraphError("the trailing edge carries no band seam to continue into a wake")

    # Vertex keys of the annular neighbourhood.
    gate_te = ("gate", cell_a, *key)
    f_in, f_out = _front_keys(clone, cell_a, key)
    ring_r = ("ring", *key)
    gate_w = ("gate", cell_c, *key)
    ring_prev = ("ring", *prev_a.anchor.key)
    ring_next = ("ring", *next_a.anchor.key)
    f_prev_out = _front_keys(clone, cell_a, prev_a.anchor.key)[1]
    f_next_in = _front_keys(clone, cell_a, next_a.anchor.key)[0]
    gate_prev_c = ("gate", cell_c, *prev_c.anchor.key)
    gate_next_c = ("gate", cell_c, *next_c.anchor.key)
    for vertex in (gate_te, f_in, f_out, ring_r, gate_w, ring_prev, ring_next, f_prev_out, f_next_in, gate_prev_c, gate_next_c):
        if vertex not in clone.vertices:
            raise pg.GraphError(f"expected vertex {vertex!r} is missing")

    point_te = clone.vertices[gate_te].point
    point_r = clone.vertices[ring_r].point
    point_w = clone.vertices[gate_w].point
    direction = np.asarray(record["direction"], dtype=np.float64)
    normal = np.array([-direction[1], direction[0]])
    side_in = float(np.dot(clone.vertices[f_in].point - point_te, normal))
    side_out = float(np.dot(clone.vertices[f_out].point - point_te, normal))
    if side_in * side_out >= 0.0:
        raise pg.GraphError("the two seam points lie on the same side of the wake line")
    height = min(abs(side_in), abs(side_out))
    clearance = record_clearance(record) * height

    # Wake fronts through the seam points, crossing the ring section between
    # the neighbouring anchors and then the outer boundary between the
    # neighbouring far-field gates.
    cell = layout.cells[cell_a]
    ring_section = g2.loop_section(cell.ring, prev_a.ring_station, next_a.ring_station, forward=True)
    outer_section = site_c.curve.section(prev_c.wall_station, next_c.wall_station, forward=True)
    outer_total = site_c.curve.length()
    ring_total = cell.ring_length
    new_points: dict[str, np.ndarray] = {}
    stations: dict[str, float] = {}
    for label, front_key in (("in", f_in), ("out", f_out)):
        origin = clone.vertices[front_key].point
        hit, why = _section_crossing(origin, direction, ring_section, clearance=clearance)
        if hit is None:
            raise pg.GraphError(f"the {label} wake front does not cross the ring section once: {why}")
        t, arclength = hit
        new_points[f"ring_{label}"] = origin + t * direction
        stations[f"ring_{label}"] = (prev_a.ring_station + arclength) % ring_total
        hit, why = _section_crossing(new_points[f"ring_{label}"], direction, outer_section, clearance=clearance)
        if hit is None:
            raise pg.GraphError(f"the {label} wake front does not reach the outer boundary once: {why}")
        t, arclength = hit
        new_points[f"outer_{label}"] = new_points[f"ring_{label}"] + t * direction
        stations[f"outer_{label}"] = (prev_c.wall_station + arclength) % outer_total
    # Order along the ring and along the outer boundary: prev, in, R, out, next.
    if not (
        _cyclic_between(stations["ring_in"], prev_a.ring_station, wake_cut_a.ring_station, ring_total)
        and _cyclic_between(stations["ring_out"], wake_cut_a.ring_station, next_a.ring_station, ring_total)
    ):
        raise pg.GraphError("the wake fronts do not bracket the wake on the ring in cut order")
    if not (
        _cyclic_between(stations["outer_in"], prev_c.wall_station, wake_cut_c.wall_station, outer_total)
        and _cyclic_between(stations["outer_out"], wake_cut_c.wall_station, next_c.wall_station, outer_total)
    ):
        raise pg.GraphError("the wake fronts do not bracket the wake on the outer boundary in cut order")
    chain = site_c.chain_at(wake_cut_c.wall_station)
    for label in ("in", "out"):
        if site_c.chain_at(stations[f"outer_{label}"]).name != chain.name:
            raise pg.GraphError("the wake band meets the outer boundary across a chain break")

    # Remove the annular faces that the wake rewrites.
    removed = []
    for corners in (
        (gate_te, f_out, ring_r, f_in),
        (f_prev_out, f_in, ring_r, ring_prev),
        (f_out, f_next_in, ring_next, ring_r),
        (gate_prev_c, gate_w, ring_r, ring_prev),
        (gate_w, gate_next_c, ring_next, ring_r),
    ):
        face = _face_with_corners(clone, corners)
        if face is None:
            raise pg.GraphError(f"expected face {corners!r} is missing around the wake anchor")
        removed.append(face.key)
    clone.remove_faces(removed)

    # New vertices.
    ring_in = ("wake", cell_a, *key, "ring", "in")
    ring_out = ("wake", cell_a, *key, "ring", "out")
    outer_in = ("wake", cell_c, *key, "gate", "in")
    outer_out = ("wake", cell_c, *key, "gate", "out")
    clone.add_vertex(ring_in, new_points["ring_in"], constraint=pg.Constraint("guide", "medial", stations["ring_in"]), provenance="wake front on the ring")
    clone.add_vertex(ring_out, new_points["ring_out"], constraint=pg.Constraint("guide", "medial", stations["ring_out"]), provenance="wake front on the ring")
    clone.add_vertex(outer_in, new_points["outer_in"], constraint=pg.Constraint("chain", chain.name, stations["outer_in"]), provenance="wake front on the outer boundary")
    clone.add_vertex(outer_out, new_points["outer_out"], constraint=pg.Constraint("chain", chain.name, stations["outer_out"]), provenance="wake front on the outer boundary")

    # Ring pieces: prev -> in -> R -> out -> next.
    for first, second, s1, s2 in (
        (ring_prev, ring_in, prev_a.ring_station, stations["ring_in"]),
        (ring_in, ring_r, stations["ring_in"], wake_cut_a.ring_station),
        (ring_r, ring_out, wake_cut_a.ring_station, stations["ring_out"]),
        (ring_out, ring_next, stations["ring_out"], next_a.ring_station),
    ):
        path = g2.loop_section(cell.ring, s1, s2, forward=True)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        kind, points = _path_curve(path)
        clone.add_edge(first, second, path=path, kind=kind, points=points, role="ring", provenance="medial branch")
    # Outer pieces: prev -> in -> W -> out -> next, in the chain's patch.
    for first, second, s1, s2 in (
        (gate_prev_c, outer_in, prev_c.wall_station, stations["outer_in"]),
        (outer_in, gate_w, stations["outer_in"], wake_cut_c.wall_station),
        (gate_w, outer_out, wake_cut_c.wall_station, stations["outer_out"]),
        (outer_out, gate_next_c, stations["outer_out"], next_c.wall_station),
    ):
        path = site_c.curve.section(s1, s2, forward=True)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        curve = site_c.curve.edge_curve(s1, s2, forward=True, style=wall_edge_style)
        clone.add_edge(first, second, path=path, kind=curve.kind, points=curve.points, boundary=chain.name, role="wall", provenance="supplied point list")
    # The wake line and the wake fronts.
    _add_line(clone, gate_te, ring_r, "wake", "wake separatrix")
    wake_far = clone.edges[clone.edge_key(ring_r, gate_w)]
    wake_far.role = "wake"
    wake_far.provenance = "wake separatrix"
    for first, second in ((f_in, ring_in), (f_out, ring_out), (ring_in, outer_in), (ring_out, outer_out)):
        _add_line(clone, first, second, "wake_front", "wake band front")

    # The eight faces.
    clone.add_face((f_prev_out, f_in, ring_in, ring_prev), role="core", provenance="annular core patch")
    clone.add_face((gate_te, ring_r, ring_in, f_in), role="wake", provenance="wake band")
    clone.add_face((gate_te, f_out, ring_out, ring_r), role="wake", provenance="wake band")
    clone.add_face((f_out, f_next_in, ring_next, ring_out), role="core", provenance="annular core patch")
    clone.add_face((gate_prev_c, outer_in, ring_in, ring_prev), role="core", provenance="annular core patch")
    clone.add_face((ring_r, gate_w, outer_in, ring_in), role="wake", provenance="wake band beyond the ring")
    clone.add_face((ring_r, ring_out, outer_out, gate_w), role="wake", provenance="wake band beyond the ring")
    clone.add_face((outer_out, gate_next_c, ring_next, ring_out), role="core", provenance="annular core patch")

    edges, vertices = clone.unused_entities()
    clone.discard(edges, vertices)
    record["wake_points"] = {label: [float(v) for v in point] for label, point in new_points.items()}
    record["band_height_at_edge"] = float(height)
    return clone


def _problem_signature(problem: dict) -> str:
    """A problem without its session block id, which shifts when faces move."""
    import json

    return json.dumps({key: value for key, value in problem.items() if key != "block"}, sort_keys=True, default=str)


def record_clearance(record: dict) -> float:
    return float(record.get("clearance_ratio", WakeOptions.clearance_ratio))


def _path_curve(path: np.ndarray, style: str = "polyLine") -> tuple[str, tuple]:
    interior = path[1:-1]
    if len(interior) == 0:
        return "line", ()
    return style, tuple((float(x), float(y)) for x, y in interior)


def _add_line(graph: pg.PatchGraph, first, second, role: str, provenance: str) -> None:
    graph.add_edge(
        first,
        second,
        path=np.asarray([graph.vertices[first].point, graph.vertices[second].point]),
        role=role,
        provenance=provenance,
    )
