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
    bases_done: set = set()
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
                pair = tuple(sorted((index, other)))
                if pair in bases_done:
                    # The base record covers both corners; this corner's own
                    # record is dropped.
                    records.pop()
                    continue
                bases_done.add(pair)
                first, second = (
                    (index, other)
                    if (stations[other] - stations[index]) % total <= settings.blunt_ratio * total
                    else (other, index)
                )
                _plan_base(record, layout, settings, cell_index, cell, site, loop, stations, angles, first, second, outer_index, outer, outer_loop)
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
            other_index = [s for s in branch.pair if s != cell.site][0]
            other = diagram.sites[other_index]
            # Where the wake ends: on the outer boundary when the ring branch
            # it crosses borders the outer cell, otherwise on the band of the
            # body on the other side of that branch - a downstream element,
            # whose nose the wake meets.
            if outer_index in branch.pair:
                ends = [item for item in _ray_crossings(point, direction, outer_loop) if item[0] > t_ring]
                if not ends:
                    record["reason"] = "the wake ray does not reach the outer boundary beyond the ring"
                    continue
                t_end, _arclength, _segment = ends[0]
                end_point = point + t_end * direction
                end_station = float(outer.curve.closest(end_point[None, :]).arclength[0])
                target = {
                    "kind": "outer",
                    "cell": len(layout.cells) - 1,
                    "site": outer.name,
                    "site_index": outer_index,
                    "wall_station": end_station,
                    "point": [float(v) for v in end_point],
                }
            else:
                other_cell = next(
                    (i for i, item in enumerate(layout.cells) if item.site == other_index), None
                )
                ends = [item for item in _ray_crossings(point, direction, other.curve.loop()) if item[0] > t_ring]
                if other_cell is None or not ends:
                    record["reason"] = (
                        f"the wake crosses the ring into the cell of {other.name!r} "
                        "but does not reach its wall"
                    )
                    continue
                t_end, _arclength, _segment = ends[0]
                end_point = point + t_end * direction
                end_station = float(other.curve.closest(end_point[None, :]).arclength[0])
                target = {
                    "kind": "body",
                    "cell": other_cell,
                    "site": other.name,
                    "site_index": other_index,
                    "wall_station": end_station,
                    "point": [float(v) for v in end_point],
                }
            blocked = None
            for third in diagram.sites[:-1]:
                if third.index in (cell.site, target["site_index"]):
                    continue
                hits = [item for item in _ray_crossings(point, direction, third.curve.loop()) if item[0] < t_end]
                if hits:
                    blocked = f"the wake ray meets the body {third.name!r}"
                    break
            if blocked is None:
                for other_branch in diagram.branches:
                    if other_branch.index == branch_index:
                        continue
                    hits = [item for item in _ray_crossings(point, direction, other_branch.path) if item[0] < t_end]
                    if hits:
                        blocked = (
                            f"the wake ray crosses medial branch {other_branch.index} before it ends"
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
                    "target": target,
                    "exit_station": target["wall_station"],
                    "exit_point": list(target["point"]),
                    "exit_chain": diagram.sites[target["site_index"]].chain_at(target["wall_station"]).name,
                    "wake_length": float(t_end),
                    "planned": settings.enabled,
                }
            )
            if not settings.enabled:
                record["reason"] = "wakes are disabled"
    return records


def _plan_base(record, layout, settings, cell_index, cell, site, loop, stations, angles, first, second, outer_index, outer, outer_loop):
    """Plan the wake of a blunt base: two parallel wake lines from its corners.

    The base is the short wall segment from ``first`` to ``second`` (loop
    order).  Its band continues downstream as the wake's core strip between
    the two wake lines, and the flank bands continue beside them as the two
    wake bands, so the record carries two ring crossings and two exits.  The
    direction is the base's outward normal, or the fixed one when it lies
    within sixty degrees of that normal.
    """
    diagram = layout.diagram
    total = float(stations[-1])
    points = [np.asarray(loop[first], dtype=np.float64), np.asarray(loop[second], dtype=np.float64)]
    base = points[1] - points[0]
    width = float(np.linalg.norm(base))
    record.update(
        {
            "kind": "base",
            "corners": [int(first), int(second)],
            "wall_stations": [float(stations[first]), float(stations[second])],
            "points": [[float(v) for v in point] for point in points],
            "fluid_angles_degrees": [float(math.degrees(angles[first])), float(math.degrees(angles[second]))],
            "base_width": width,
            "vertex": int(first),
            "wall_station": float(stations[first]),
            "point": [float(v) for v in points[0]],
        }
    )
    if width <= 0.0:
        record["reason"] = "the base has no width"
        return
    normal = site.curve.fluid_sign * np.array([base[1], -base[0]]) / width
    direction = normal
    if settings.direction is not None:
        direction = np.asarray(settings.direction, dtype=np.float64)
        direction = direction / np.linalg.norm(direction)
        if float(np.dot(direction, normal)) < 0.5:
            record["reason"] = "the requested wake direction leaves the base's outward sector"
            return
    record["direction"] = [float(direction[0]), float(direction[1])]
    rings = []
    exits = []
    for point in points:
        own = _ray_crossings(point, direction, cell.ring)
        if len(own) != 1:
            record["reason"] = f"a base wake ray crosses the body's own ring {len(own)} times; exactly one is needed"
            return
        t_ring, ring_station, _segment = own[0]
        branch_index, position = cell.branch_station(ring_station)
        branch = diagram.branches[branch_index]
        if outer_index not in branch.pair:
            other = diagram.sites[[s for s in branch.pair if s != cell.site][0]]
            record["reason"] = f"the base wake crosses the ring into the cell of {other.name!r}; a base wake ending on another body is not built yet"
            return
        ends = [item for item in _ray_crossings(point, direction, outer_loop) if item[0] > t_ring]
        if not ends:
            record["reason"] = "a base wake ray does not reach the outer boundary beyond the ring"
            return
        t_end, _arclength, _segment = ends[0]
        end_point = point + t_end * direction
        for third in diagram.sites[:-1]:
            if third.index == cell.site:
                continue
            if any(item[0] < t_end for item in _ray_crossings(point, direction, third.curve.loop())):
                record["reason"] = f"a base wake ray meets the body {third.name!r}"
                return
        for other_branch in diagram.branches:
            if other_branch.index == branch_index:
                continue
            if any(item[0] < t_end for item in _ray_crossings(point, direction, other_branch.path)):
                record["reason"] = f"a base wake ray crosses medial branch {other_branch.index} before it ends"
                return
        rings.append({"branch": int(branch_index), "position": float(position), "station": float(ring_station), "point": [float(v) for v in point + t_ring * direction]})
        exits.append({"station": float(outer.curve.closest(end_point[None, :]).arclength[0]), "point": [float(v) for v in end_point], "length": float(t_end)})
    if rings[0]["branch"] != rings[1]["branch"]:
        record["reason"] = "the two base wake lines cross different medial branches"
        return
    chains = {outer.chain_at(item["station"]).name for item in exits}
    if len(chains) != 1:
        record["reason"] = "the two base wake lines leave through different outer chains"
        return
    record.update(
        {
            "rings": rings,
            "exits": exits,
            "ring_branch": rings[0]["branch"],
            "ring_position": rings[0]["position"],
            "ring_station": rings[0]["station"],
            "ring_point": rings[0]["point"],
            "target": {
                "kind": "outer", "cell": len(layout.cells) - 1, "site": outer.name,
                "site_index": outer_index, "wall_station": exits[0]["station"], "point": exits[0]["point"],
            },
            "exit_station": exits[0]["station"],
            "exit_point": exits[0]["point"],
            "exit_chain": chains.pop(),
            "wake_length": max(item["length"] for item in exits),
            "planned": settings.enabled,
        }
    )
    if not settings.enabled:
        record["reason"] = "wakes are disabled"


# ---------------------------------------------------------------------------
# Layout preparation: the wake anchor on the ring
# ---------------------------------------------------------------------------


# Anchors the layout added for its own reasons - turning, curvature, the
# seeds of an empty cell - and may give up beside a wake; feature anchors
# (corners, reflex corners, chain breaks, junctions) stay.
_OPTIONAL_KINDS = frozenset({"turning", "curvature", "seed"})


def prepare(layout, records: list[dict]) -> list[str]:
    """Pin the wake anchors on a trial layout before the bands are built.

    The trailing edge's own feature anchor - projected onto the ring at the
    closest point - is replaced by an anchor exactly where the wake crosses the
    ring, pinned to the trailing edge on the body and to the exit point on the
    outer boundary (or the stagnation station on a downstream body).  A blunt
    base pins one such anchor per corner.  The anchors are fixed, so the
    relaxation neither moves them nor their gates.  Callers own the trial
    layout.
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
        anchors = layout.anchors
        removed: list = []
        if record.get("kind") == "base":
            pins = [
                (record["wall_stations"][k], ring, (outer_index, record["exits"][k]["station"]))
                for k, ring in enumerate(record["rings"])
            ]
        else:
            target = record.get("target") or {"site_index": outer_index, "wall_station": record["exit_station"]}
            pins = [
                (
                    record["wall_station"],
                    {"branch": record["ring_branch"], "position": record["ring_position"], "station": record["ring_station"], "point": record["ring_point"]},
                    (int(target["site_index"]), float(target["wall_station"])),
                )
            ]
        keys = []
        for station, ring, extra in pins:
            anchor = _pin_anchor(layout, anchors, cell_index, cell, site, station, ring, extra, removed, notes, record)
            keys.append(list(anchor.key))
        record["anchor_key"] = keys[0]
        record["anchor_keys"] = keys
        target = record.get("target") or {}
        if target.get("kind") == "body":
            _drop_landing_neighbours(layout, anchors, diagram, record, target, removed, notes)
        notes.append(
            f"{site.name}: {'base' if record.get('kind') == 'base' else 'wake'} from wall station "
            f"{record['wall_station']:.6g} along ({record['direction'][0]:.3f}, {record['direction'][1]:.3f}) "
            f"to {record['exit_chain']!r}, ring branch {record['ring_branch']}"
        )
    layout.cuts = layout.anchors.cuts()
    layout.patches = block_layout.build_patches(diagram, layout.cells, layout.cuts)
    layout.refresh()
    return notes


def _pin_anchor(layout, anchors, cell_index, cell, site, station, ring, extra, removed, notes, record):
    """Replace the feature anchor at ``station`` by a fixed wake anchor at ``ring``."""
    import block_layout

    total = site.curve.length()
    # Remove the feature's existing anchor at this wall station.  The wake
    # anchor takes its place on the ring - the same place, when the feature
    # anchor sits on the corner's bisector and the wake follows it - so the
    # removed anchor's cut must not count as a neighbour in the crowding
    # bookkeeping below.
    for cut in layout.cuts[cell_index]:
        anchor = cut.anchor
        # The feature's anchor is matched by the station it was made for as
        # well as by its gate's: a corner whose pin the relaxation released
        # to relieve crowding sits away from the corner on the wall.
        hinted = anchor.hint_for(cell.site)
        gaps = [(cut.wall_station - station) % total] + ([] if hinted is None else [(hinted - station) % total])
        if all(min(gap, total - gap) > 1e-9 * total for gap in gaps):
            continue
        if anchor.junction is not None:
            raise pg.GraphError("the trailing edge is a medial junction gate")
        if anchor.kind == "wake":
            raise pg.GraphError("the trailing edge already carries a wake anchor")
        anchors.by_branch[anchor.branch] = [item for item in anchors.by_branch[anchor.branch] if item is not anchor]
        anchors._keys.discard(anchor.key)
        removed.append(anchor.key)
    anchor = block_layout.Anchor(
        int(ring["branch"]), float(ring["position"]), "wake", None, (cell.site, station),
        extra_hint=(int(extra[0]), float(extra[1])), fixed=True,
    )
    # The wake anchor is forced onto the ring, so the layout's own floor -
    # neighbouring cuts at least ``ring_separation`` clearances apart - is
    # not checked against it.  Optional anchors that would now sit inside
    # that floor are dropped: the refinement put them beside the trailing
    # edge to bound the turning of blocks the wake replaces.
    wake_clearance = float(np.linalg.norm(np.asarray(ring["point"]) - site.curve.point_at(station)))
    crowded = []
    for cut in layout.cuts[cell_index]:
        other = cut.anchor
        if other.key in removed or not other.movable or other.kind not in _OPTIONAL_KINDS:
            continue
        gap = (cut.ring_station - float(ring["station"])) % cell.ring_length
        gap = min(gap, cell.ring_length - gap)
        if gap < anchors.ring_separation * min(cut.clearance, wake_clearance):
            crowded.append(other)
    for other in crowded:
        anchors.by_branch[other.branch] = [item for item in anchors.by_branch[other.branch] if item is not other]
        anchors._keys.discard(other.key)
        removed.append(other.key)
    if crowded:
        notes.append(f"{site.name}: dropped {len(crowded)} optional anchor(s) crowding the wake anchor on the ring")
    # Stations are rebuilt from the anchor lists, so the crowding bookkeeping
    # does not see the removed anchors any more.
    anchors._stations = {index: [] for index in range(len(layout.cells))}
    for other_cuts in layout.cuts:
        for cut in other_cuts:
            if cut.anchor.key in removed:
                continue
            anchors._stations[cut.cell].append((cut.ring_station, cut.wall_station, cut.clearance))
    if not anchors.add(anchor, force=True):
        raise pg.GraphError("the wake anchor coincides with an existing ring cut")
    return anchor


def _drop_landing_neighbours(layout, anchors, diagram, record, target, removed, notes):
    """On the downstream body drop optional gates inside the wake's landing span."""
    layout.cuts = anchors.cuts()
    cell_b = int(target["cell"])
    site_b = diagram.sites[layout.cells[cell_b].site]
    total_b = site_b.curve.length()
    wake_clearance = float(np.linalg.norm(np.asarray(record["ring_point"]) - np.asarray(record["point"])))
    span = 0.5 * wake_clearance
    landing = float(target["wall_station"])
    anchor_keys = {tuple(key) for key in record["anchor_keys"]}
    crowded_b = []
    for cut in layout.cuts[cell_b]:
        other = cut.anchor
        if other.key in anchor_keys or not other.movable or other.kind not in _OPTIONAL_KINDS:
            continue
        gap = (cut.wall_station - landing) % total_b
        if min(gap, total_b - gap) < span:
            crowded_b.append(other)
    for other in crowded_b:
        anchors.by_branch[other.branch] = [item for item in anchors.by_branch[other.branch] if item is not other]
        anchors._keys.discard(other.key)
        removed.append(other.key)
    if crowded_b:
        notes.append(f"{site_b.name}: dropped {len(crowded_b)} optional gate(s) inside the wake's landing span")
        anchors._stations = {index: [] for index in range(len(layout.cells))}
        for other_cuts in layout.cuts:
            for cut in other_cuts:
                if cut.anchor.key in removed:
                    continue
                anchors._stations[cut.cell].append((cut.ring_station, cut.wall_station, cut.clearance))


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
        cell_index = record["cell"]
        cell = layout.cells[cell_index]
        cuts = layout.cuts[cell_index]
        front = fronts.get(cell_index)
        if front is None:
            continue
        # A trailing edge's anchor has a wake front on each side; a base's
        # first anchor only on the in side and its second only on the out.
        if record.get("kind") == "base":
            jobs = [(tuple(record["anchor_keys"][0]), ("in",)), (tuple(record["anchor_keys"][1]), ("out",))]
        else:
            jobs = [(tuple(record["anchor_key"]), ("in", "out"))]
        for key, labels in jobs:
            position = next((i for i, cut in enumerate(cuts) if cut.anchor.key == key), None)
            if position is None or (record.get("kind") != "base" and not front.is_seam(position)):
                continue
            moved |= _make_room_at(layout, cell, cuts, position, front, record, labels, settings, notes)
    if moved:
        layout.cuts = layout.anchors.cuts()
        layout.patches = block_layout.build_patches(diagram, layout.cells, layout.cuts)
        layout.refresh()
    return notes


def _make_room_at(layout, cell, cuts, position, front, record, labels, settings, notes) -> bool:
    """Slide the ring neighbours of one wake anchor clear of its wake fronts."""
    import block_layout

    diagram = layout.diagram
    moved = False
    if True:
        wake_cut = cuts[position]
        direction = np.asarray(record["direction"], dtype=np.float64)
        normal = np.array([-direction[1], direction[0]])
        total = cell.ring_length
        height = 0.0
        crossings: dict[str, float] = {}
        for label in labels:
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
            if label not in labels:
                continue
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
    return moved


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
    if record.get("kind") == "base":
        return _write_base_wake(layout, graph, record, fronts, wall_edge_style)
    if (record.get("target") or {}).get("kind") == "body":
        return _write_wake_to_body(layout, graph, record, fronts, wall_edge_style)
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


def _write_base_wake(layout, graph: pg.PatchGraph, record: dict, fronts, wall_edge_style: str) -> pg.PatchGraph:
    """Write the wake of a blunt base: the base continues as the wake's core strip.

    The base is the wall segment between two convex corners ``gate1`` and
    ``gate2``.  Its band block, the core patch over it and the corners' seam
    wedges are replaced by three strips running downstream between the ring
    and the outer boundary::

        p1 ---- rf1 ---- wf1        lower wake band (the lower flank's band goes on)
         |       |        |
        gate1 ---- r1 ----- w1         wake line from the first corner
         |       |        |
        gate2 ---- r2 ----- w2         wake line from the second corner
         |       |        |
        p2 ---- rf2 ---- wf2        upper wake band

    ``p1``/``p2`` are the corners' flank-side front points (the in seam point
    of the first corner, the out seam point of the second, or the single
    front point of a corner that is not sharp), ``r``/``rf`` the ring
    crossings and ``w``/``wf`` the outer exits.  The central strip
    ``(gate1, gate2, r2, r1)`` carries the base as its wall edge, so its cells are
    the base's boundary layer; the two wake bands inherit the flank bands'
    normal counts through the corner spokes.  Each corner ends up with three
    blocks meeting at it: its flank band, its wake band and the strip.
    """
    diagram = layout.diagram
    cell_a = record["cell"]
    cell_c = len(layout.cells) - 1
    if diagram.sites[layout.cells[cell_c].site].curve.kind == "wall":
        raise pg.GraphError("the last cell is not the outer boundary")
    site_c = diagram.sites[layout.cells[cell_c].site]
    key1, key2 = (tuple(key) for key in record["anchor_keys"])
    clone = graph.copy()

    cuts_a = layout.cuts[cell_a]
    cuts_c = layout.cuts[cell_c]
    pos1 = next((i for i, cut in enumerate(cuts_a) if cut.anchor.key == key1), None)
    pos2 = next((i for i, cut in enumerate(cuts_a) if cut.anchor.key == key2), None)
    if pos1 is None or pos2 is None:
        raise pg.GraphError("a base anchor is missing from the body cell")
    if (pos1 + 1) % len(cuts_a) != pos2:
        raise pg.GraphError("the two base anchors are not consecutive gates on the body")
    posc1 = next((i for i, cut in enumerate(cuts_c) if cut.anchor.key == key1), None)
    posc2 = next((i for i, cut in enumerate(cuts_c) if cut.anchor.key == key2), None)
    if posc1 is None or posc2 is None or (posc1 + 1) % len(cuts_c) != posc2:
        raise pg.GraphError("the two base anchors are not consecutive gates on the outer boundary")
    cut1, cut2 = cuts_a[pos1], cuts_a[pos2]
    cutc1, cutc2 = cuts_c[posc1], cuts_c[posc2]
    prev_a = cuts_a[(pos1 - 1) % len(cuts_a)]
    next_a = cuts_a[(pos2 + 1) % len(cuts_a)]
    prev_c = cuts_c[(posc1 - 1) % len(cuts_c)]
    next_c = cuts_c[(posc2 + 1) % len(cuts_c)]
    if prev_a.anchor.key != prev_c.anchor.key or next_a.anchor.key != next_c.anchor.key:
        raise pg.GraphError("the ring neighbours of the base anchors differ between the body and the outer boundary; the base meets the ring beside a junction")
    if prev_a.anchor.key in (key1, key2) or next_a.anchor.key in (key1, key2):
        raise pg.GraphError("the body cell has too few cuts for a base wake")
    front = fronts.get(cell_a)
    if front is None:
        raise pg.GraphError("the body has no boundary-layer band; a base continues a band")

    # Vertex keys.
    gate1, gate2 = ("gate", cell_a, *key1), ("gate", cell_a, *key2)
    f1_in, f1_out = _front_keys(clone, cell_a, key1)
    f2_in, f2_out = _front_keys(clone, cell_a, key2)
    p1, p2 = f1_in, f2_out                       # flank-side front points
    r1, r2 = ("ring", *key1), ("ring", *key2)
    w1, w2 = ("gate", cell_c, *key1), ("gate", cell_c, *key2)
    ring_prev, ring_next = ("ring", *prev_a.anchor.key), ("ring", *next_a.anchor.key)
    f_prev_out = _front_keys(clone, cell_a, prev_a.anchor.key)[1]
    f_next_in = _front_keys(clone, cell_a, next_a.anchor.key)[0]
    gate_prev_c, gate_next_c = ("gate", cell_c, *prev_c.anchor.key), ("gate", cell_c, *next_c.anchor.key)
    for vertex in (gate1, gate2, p1, p2, r1, r2, w1, w2, ring_prev, ring_next, f_prev_out, f_next_in, gate_prev_c, gate_next_c):
        if vertex not in clone.vertices:
            raise pg.GraphError(f"expected vertex {vertex!r} is missing")

    direction = np.asarray(record["direction"], dtype=np.float64)
    normal = np.array([-direction[1], direction[0]])
    point1, point2 = clone.vertices[gate1].point, clone.vertices[gate2].point
    side1 = float(np.dot(clone.vertices[p1].point - point1, normal))
    side2 = float(np.dot(clone.vertices[p2].point - point2, normal))
    base_side = float(np.dot(point2 - point1, normal))
    # The loop runs from the first corner along the base to the second; the
    # first corner's flank front lies on the far side of it from the base.
    if base_side == 0.0 or side1 * base_side > 0.0 or side2 * base_side < 0.0:
        raise pg.GraphError("the flank front points do not lie outside the base on their own sides")
    height = min(abs(side1), abs(side2))
    clearance = record_clearance(record) * height

    # The wake fronts cross the ring section between the neighbours, one on
    # each side of the pair of wake lines, then the outer boundary.
    cell = layout.cells[cell_a]
    ring_total = cell.ring_length
    ring_section = g2.loop_section(cell.ring, prev_a.ring_station, next_a.ring_station, forward=True)
    outer_section = site_c.curve.section(prev_c.wall_station, next_c.wall_station, forward=True)
    outer_total = site_c.curve.length()
    new_points: dict[str, np.ndarray] = {}
    stations: dict[str, float] = {}
    for label, front_key in (("1", p1), ("2", p2)):
        origin = clone.vertices[front_key].point
        hit, why = _section_crossing(origin, direction, ring_section, clearance=clearance)
        if hit is None:
            raise pg.GraphError(f"the wake front of corner {label} does not cross the ring section once: {why}")
        t, arclength = hit
        new_points[f"ring_{label}"] = origin + t * direction
        stations[f"ring_{label}"] = (prev_a.ring_station + arclength) % ring_total
        hit, why = _section_crossing(new_points[f"ring_{label}"], direction, outer_section, clearance=clearance)
        if hit is None:
            raise pg.GraphError(f"the wake front of corner {label} does not reach the outer boundary once: {why}")
        t, arclength = hit
        new_points[f"outer_{label}"] = new_points[f"ring_{label}"] + t * direction
        stations[f"outer_{label}"] = (prev_c.wall_station + arclength) % outer_total
    if not (
        _cyclic_between(stations["ring_1"], prev_a.ring_station, cut1.ring_station, ring_total)
        and _cyclic_between(stations["ring_2"], cut2.ring_station, next_a.ring_station, ring_total)
    ):
        raise pg.GraphError("the wake fronts do not bracket the base's wake lines on the ring in cut order")
    if not (
        _cyclic_between(stations["outer_1"], prev_c.wall_station, cutc1.wall_station, outer_total)
        and _cyclic_between(stations["outer_2"], cutc2.wall_station, next_c.wall_station, outer_total)
    ):
        raise pg.GraphError("the wake fronts do not bracket the base's wake lines on the outer boundary in cut order")
    chain = site_c.chain_at(cutc1.wall_station)
    for station in (stations["outer_1"], stations["outer_2"], cutc2.wall_station):
        if site_c.chain_at(station).name != chain.name:
            raise pg.GraphError("the base's wake meets the outer boundary across a chain break")

    # Remove the annular faces the base rewrites: the band block over the
    # base, the core patch above it, the corners' seam wedges when they have
    # them, the core patches beside, and the outer patches.
    corner_sets = [
        (gate1, gate2, f2_in, f1_out),
        (f1_out, f2_in, r2, r1),
        (f_prev_out, f1_in, r1, ring_prev),
        (f2_out, f_next_in, ring_next, r2),
        (gate_prev_c, w1, r1, ring_prev),
        (w1, w2, r2, r1),
        (w2, gate_next_c, ring_next, r2),
    ]
    if f1_in != f1_out:
        corner_sets.append((gate1, f1_out, r1, f1_in))
    if f2_in != f2_out:
        corner_sets.append((gate2, f2_out, r2, f2_in))
    removed = []
    for corners in corner_sets:
        face = _face_with_corners(clone, corners)
        if face is None:
            raise pg.GraphError(f"expected face {corners!r} is missing around the base")
        removed.append(face.key)
    clone.remove_faces(removed)

    rf1 = ("wake", cell_a, *key1, "ring", "in")
    rf2 = ("wake", cell_a, *key2, "ring", "out")
    wf1 = ("wake", cell_c, *key1, "gate", "in")
    wf2 = ("wake", cell_c, *key2, "gate", "out")
    clone.add_vertex(rf1, new_points["ring_1"], constraint=pg.Constraint("guide", "medial", stations["ring_1"]), provenance="wake front on the ring")
    clone.add_vertex(rf2, new_points["ring_2"], constraint=pg.Constraint("guide", "medial", stations["ring_2"]), provenance="wake front on the ring")
    clone.add_vertex(wf1, new_points["outer_1"], constraint=pg.Constraint("chain", chain.name, stations["outer_1"]), provenance="wake front on the outer boundary")
    clone.add_vertex(wf2, new_points["outer_2"], constraint=pg.Constraint("chain", chain.name, stations["outer_2"]), provenance="wake front on the outer boundary")

    # Ring pieces prev -> rf1 -> r1 (r1 -> r2 stays) r2 -> rf2 -> next.
    for first, second, s1, s2 in (
        (ring_prev, rf1, prev_a.ring_station, stations["ring_1"]),
        (rf1, r1, stations["ring_1"], cut1.ring_station),
        (r2, rf2, cut2.ring_station, stations["ring_2"]),
        (rf2, ring_next, stations["ring_2"], next_a.ring_station),
    ):
        path = g2.loop_section(cell.ring, s1, s2, forward=True)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        kind, points = _path_curve(path)
        clone.add_edge(first, second, path=path, kind=kind, points=points, role="ring", provenance="medial branch")
    # Outer pieces prev_c -> wf1 -> w1 (w1 -> w2 stays) w2 -> wf2 -> next_c.
    for first, second, s1, s2 in (
        (gate_prev_c, wf1, prev_c.wall_station, stations["outer_1"]),
        (wf1, w1, stations["outer_1"], cutc1.wall_station),
        (w2, wf2, cutc2.wall_station, stations["outer_2"]),
        (wf2, gate_next_c, stations["outer_2"], next_c.wall_station),
    ):
        path = site_c.curve.section(s1, s2, forward=True)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        curve = site_c.curve.edge_curve(s1, s2, forward=True, style=wall_edge_style)
        clone.add_edge(first, second, path=path, kind=curve.kind, points=curve.points, boundary=chain.name, role="wall", provenance="supplied point list")
    # Wake lines from the corners; beyond the ring the existing far-field
    # spokes become the wake lines.
    _add_line(clone, gate1, r1, "wake", "wake separatrix")
    _add_line(clone, gate2, r2, "wake", "wake separatrix")
    for ring_key, outer_key in ((r1, w1), (r2, w2)):
        far = clone.edges[clone.edge_key(ring_key, outer_key)]
        far.role = "wake"
        far.provenance = "wake separatrix"
    for first, second in ((p1, rf1), (p2, rf2), (rf1, wf1), (rf2, wf2)):
        _add_line(clone, first, second, "wake_front", "wake band front")

    # The ten faces.
    clone.add_face((f_prev_out, p1, rf1, ring_prev), role="core", provenance="annular core patch")
    clone.add_face((gate1, r1, rf1, p1), role="wake", provenance="wake band")
    clone.add_face((gate1, gate2, r2, r1), role="wake", provenance="base strip")
    clone.add_face((gate2, p2, rf2, r2), role="wake", provenance="wake band")
    clone.add_face((p2, f_next_in, ring_next, rf2), role="core", provenance="annular core patch")
    clone.add_face((gate_prev_c, wf1, rf1, ring_prev), role="core", provenance="annular core patch")
    clone.add_face((r1, w1, wf1, rf1), role="wake", provenance="wake band beyond the ring")
    clone.add_face((r1, r2, w2, w1), role="wake", provenance="base strip beyond the ring")
    clone.add_face((r2, rf2, wf2, w2), role="wake", provenance="wake band beyond the ring")
    clone.add_face((wf2, gate_next_c, ring_next, rf2), role="core", provenance="annular core patch")

    edges, vertices = clone.unused_entities()
    clone.discard(edges, vertices)
    record["wake_points"] = {label: [float(v) for v in point] for label, point in new_points.items()}
    record["band_height_at_edge"] = float(height)
    return clone


def _split_at_arclength(section: np.ndarray, arclength: float):
    """Split a polyline at an arc length: the point and the two parts."""
    cumulative = g2.cumulative_length(section)
    index = int(np.searchsorted(cumulative, arclength, side="right")) - 1
    index = min(max(index, 0), len(section) - 2)
    span = float(cumulative[index + 1] - cumulative[index])
    local = (arclength - float(cumulative[index])) / span if span > 0.0 else 0.0
    point = section[index] + local * (section[index + 1] - section[index])
    first = np.vstack([section[: index + 1], point[None, :]])
    second = np.vstack([point[None, :], section[index + 1 :]])
    return point, first, second


def _write_wake_to_body(layout, graph: pg.PatchGraph, record: dict, fronts, wall_edge_style: str) -> pg.PatchGraph:
    """Write a wake that ends on a downstream body's band.

    The trailing-edge side is the same as for a wake to the outer boundary:
    the seam wedge becomes two wake blocks and the core patches beside it
    end on the wake fronts, which cross the ring between the two bodies.
    Beyond the ring the wake line is the downstream body's own spoke to the
    gate the wake anchor pins on it - its stagnation gate - and the two wake
    fronts run on to that body's band front and become two new band spokes
    there, so the wake band wraps the downstream body's nose exactly as an
    embedded C-grid does::

        ring_out  ---- land_in ---- foot_in
           |     wake    |   band    |
           R ----------- f_b ------ gate_b        (B's spoke, now the wake line)
           |     wake    |   band    |
        ring_in   ---- land_out --- foot_out

    with B's cut order running prev_b, land_in, R, land_out, next_b - the
    reverse of A's, since the two cells traverse their shared branch in
    opposite directions.
    """
    diagram = layout.diagram
    cell_a = record["cell"]
    cell_b = int(record["target"]["cell"])
    if cell_b == cell_a or cell_b >= len(layout.cells):
        raise pg.GraphError("the wake's target cell is not another body")
    site_b = diagram.sites[layout.cells[cell_b].site]
    if site_b.curve.kind != "wall":
        raise pg.GraphError("the wake's target is not a wall")
    key = tuple(record["anchor_key"])
    clone = graph.copy()

    cuts_a = layout.cuts[cell_a]
    cuts_b = layout.cuts[cell_b]
    position_a = next((i for i, cut in enumerate(cuts_a) if cut.anchor.key == key), None)
    position_b = next((i for i, cut in enumerate(cuts_b) if cut.anchor.key == key), None)
    if position_a is None or position_b is None:
        raise pg.GraphError("the wake anchor is missing from one of its cells")
    wake_cut_a = cuts_a[position_a]
    prev_a = cuts_a[(position_a - 1) % len(cuts_a)]
    next_a = cuts_a[(position_a + 1) % len(cuts_a)]
    prev_b = cuts_b[(position_b - 1) % len(cuts_b)]
    next_b = cuts_b[(position_b + 1) % len(cuts_b)]
    if prev_a.anchor.key != next_b.anchor.key or next_a.anchor.key != prev_b.anchor.key:
        raise pg.GraphError(
            "the ring neighbours of the wake anchor differ between the two bodies; "
            "the wake meets the ring beside a junction"
        )
    if prev_a.anchor.key == next_a.anchor.key:
        raise pg.GraphError("the body cell has too few cuts for a wake")

    front_a = fronts.get(cell_a)
    front_b = fronts.get(cell_b)
    if front_a is None or front_b is None:
        raise pg.GraphError("both bodies need a boundary-layer band; a wake continues one band into another")
    if not front_a.is_seam(position_a):
        raise pg.GraphError("the trailing edge carries no band seam to continue into a wake")
    if front_b.is_seam(position_b):
        raise pg.GraphError(f"the wake lands on a sharp feature of {site_b.name!r}")

    # Vertex keys.
    gate_te = ("gate", cell_a, *key)
    f_in, f_out = _front_keys(clone, cell_a, key)
    ring_r = ("ring", *key)
    ring_prev = ("ring", *prev_a.anchor.key)      # = ring vertex of next_b
    ring_next = ("ring", *next_a.anchor.key)      # = ring vertex of prev_b
    f_prev_out = _front_keys(clone, cell_a, prev_a.anchor.key)[1]
    f_next_in = _front_keys(clone, cell_a, next_a.anchor.key)[0]
    gate_b = ("gate", cell_b, *key)
    f_b = _front_keys(clone, cell_b, key)[0]
    fb_prev_out = _front_keys(clone, cell_b, prev_b.anchor.key)[1]
    fb_next_in = _front_keys(clone, cell_b, next_b.anchor.key)[0]
    gate_prev_b = ("gate", cell_b, *prev_b.anchor.key)
    gate_next_b = ("gate", cell_b, *next_b.anchor.key)
    for vertex in (gate_te, f_in, f_out, ring_r, ring_prev, ring_next, f_prev_out, f_next_in,
                   gate_b, f_b, fb_prev_out, fb_next_in, gate_prev_b, gate_next_b):
        if vertex not in clone.vertices:
            raise pg.GraphError(f"expected vertex {vertex!r} is missing")

    point_te = clone.vertices[gate_te].point
    direction = np.asarray(record["direction"], dtype=np.float64)
    normal = np.array([-direction[1], direction[0]])
    side_in = float(np.dot(clone.vertices[f_in].point - point_te, normal))
    side_out = float(np.dot(clone.vertices[f_out].point - point_te, normal))
    if side_in * side_out >= 0.0:
        raise pg.GraphError("the two seam points lie on the same side of the wake line")
    height = min(abs(side_in), abs(side_out))
    clearance = record_clearance(record) * height

    # The wake fronts cross the shared ring section between the neighbours.
    cell = layout.cells[cell_a]
    ring_total = cell.ring_length
    ring_section = g2.loop_section(cell.ring, prev_a.ring_station, next_a.ring_station, forward=True)
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
    if not (
        _cyclic_between(stations["ring_in"], prev_a.ring_station, wake_cut_a.ring_station, ring_total)
        and _cyclic_between(stations["ring_out"], wake_cut_a.ring_station, next_a.ring_station, ring_total)
    ):
        raise pg.GraphError("the wake fronts do not bracket the wake on the ring in cut order")

    # ... and land on B's band front a band width to either side of B's
    # front vertex at the wake gate.  The landing points are placed along
    # the front, not by continuing the straight wake fronts: the wake meets
    # the body along B's spoke, which is B's normal at the stagnation gate,
    # not the wake direction, and a wake arriving obliquely would otherwise
    # put both fronts on one side of the gate.  A's in side lies between
    # prev_a and R on the ring, which in B's order is between R and next_b,
    # so it lands after the wake gate; A's out side lands before it.
    count_b = len(cuts_b)
    gates_b = front_b.gate_indices
    before_b = layer_module.ring_slice(front_b.front, gates_b[(position_b - 1) % count_b], gates_b[position_b]).copy()
    before_b[0] = clone.vertices[fb_prev_out].point
    before_b[-1] = clone.vertices[f_b].point
    after_b = layer_module.ring_slice(front_b.front, gates_b[position_b], gates_b[(position_b + 1) % count_b]).copy()
    after_b[0] = clone.vertices[f_b].point
    after_b[-1] = clone.vertices[fb_next_in].point
    width = height
    length_before = float(g2.total_length(before_b))
    length_after = float(g2.total_length(after_b))
    if length_before < width + clearance or length_after < width + clearance:
        raise pg.GraphError(
            f"{site_b.name!r} has no room beside the wake's landing gate for a band "
            f"{width:.4g} wide (front sections {length_before:.4g} and {length_after:.4g})"
        )
    land_in, before_first, before_second = _split_at_arclength(before_b, length_before - width)
    land_out, after_first, after_second = _split_at_arclength(after_b, width)
    new_points["land_in"] = land_in
    new_points["land_out"] = land_out

    # The feet of the two new band spokes on B's wall, strictly inside the
    # neighbouring wall sections and inside one chain each.
    total_b = site_b.curve.length()
    station_gate_b = float(clone.vertices[gate_b].constraint.parameter)
    station_prev_b = float(clone.vertices[gate_prev_b].constraint.parameter)
    station_next_b = float(clone.vertices[gate_next_b].constraint.parameter)
    feet: dict[str, float] = {}
    for label, land, low, high in (
        ("in", land_in, station_prev_b, station_gate_b),
        ("out", land_out, station_gate_b, station_next_b),
    ):
        foot = float(site_b.curve.closest(land[None, :]).arclength[0])
        if not _cyclic_between(foot, low, high, total_b):
            raise pg.GraphError(f"the {label} wake front's foot on {site_b.name!r} leaves its wall section")
        margin = 0.5 * float(np.linalg.norm(land - site_b.curve.point_at(foot)))
        if min((foot - low) % total_b, (high - foot) % total_b) < record_clearance(record) * margin:
            raise pg.GraphError(f"the {label} wake front's foot on {site_b.name!r} crowds a neighbouring gate")
        feet[label] = foot
        new_points[f"foot_{label}"] = site_b.curve.point_at(foot)
    chain_b = site_b.chain_at(station_gate_b)
    for station in (station_prev_b, feet["in"], feet["out"], station_next_b):
        if site_b.chain_at(station).name != chain_b.name:
            raise pg.GraphError("the wake band lands on the body across a chain break")

    # Remove the faces the wake rewrites: the wedge and two core patches at
    # the edge, and the two core patches and two band blocks at the landing.
    removed = []
    for corners in (
        (gate_te, f_out, ring_r, f_in),
        (f_prev_out, f_in, ring_r, ring_prev),
        (f_out, f_next_in, ring_next, ring_r),
        (fb_prev_out, f_b, ring_r, ring_next),
        (f_b, fb_next_in, ring_prev, ring_r),
        (gate_prev_b, gate_b, f_b, fb_prev_out),
        (gate_b, gate_next_b, fb_next_in, f_b),
    ):
        face = _face_with_corners(clone, corners)
        if face is None:
            raise pg.GraphError(f"expected face {corners!r} is missing around the wake anchor")
        removed.append(face.key)
    clone.remove_faces(removed)

    # New vertices.
    ring_in = ("wake", cell_a, *key, "ring", "in")
    ring_out = ("wake", cell_a, *key, "ring", "out")
    land_in_key = ("wake", cell_b, *key, "front", "in")
    land_out_key = ("wake", cell_b, *key, "front", "out")
    foot_in_key = ("wake", cell_b, *key, "gate", "in")
    foot_out_key = ("wake", cell_b, *key, "gate", "out")
    clone.add_vertex(ring_in, new_points["ring_in"], constraint=pg.Constraint("guide", "medial", stations["ring_in"]), provenance="wake front on the ring")
    clone.add_vertex(ring_out, new_points["ring_out"], constraint=pg.Constraint("guide", "medial", stations["ring_out"]), provenance="wake front on the ring")
    clone.add_vertex(land_in_key, land_in, constraint=pg.Constraint("guide", f"front:{site_b.name}", float(position_b) - 0.5), provenance="wake front landing on the band")
    clone.add_vertex(land_out_key, land_out, constraint=pg.Constraint("guide", f"front:{site_b.name}", float(position_b) + 0.5), provenance="wake front landing on the band")
    clone.add_vertex(foot_in_key, new_points["foot_in"], constraint=pg.Constraint("chain", chain_b.name, feet["in"]), provenance="wake band foot on the wall")
    clone.add_vertex(foot_out_key, new_points["foot_out"], constraint=pg.Constraint("chain", chain_b.name, feet["out"]), provenance="wake band foot on the wall")

    # Ring pieces on the shared branch: prev_a -> in -> R -> out -> next_a.
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
    # B's front pieces: prev_b -> land_in -> f_b -> land_out -> next_b.
    for first, second, path in (
        (fb_prev_out, land_in_key, before_first),
        (land_in_key, f_b, before_second),
        (f_b, land_out_key, after_first),
        (land_out_key, fb_next_in, after_second),
    ):
        path = np.asarray(path, dtype=np.float64)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        kind, points = _path_curve(path)
        clone.add_edge(first, second, path=path, kind=kind, points=points, role="front", provenance="clearance-limited offset")
    # B's wall pieces: prev_b -> foot_in -> gate_b -> foot_out -> next_b.
    for first, second, s1, s2 in (
        (gate_prev_b, foot_in_key, station_prev_b, feet["in"]),
        (foot_in_key, gate_b, feet["in"], station_gate_b),
        (gate_b, foot_out_key, station_gate_b, feet["out"]),
        (foot_out_key, gate_next_b, feet["out"], station_next_b),
    ):
        path = site_b.curve.section(s1, s2, forward=True)
        path[0] = clone.vertices[first].point
        path[-1] = clone.vertices[second].point
        curve = site_b.curve.edge_curve(s1, s2, forward=True, style=wall_edge_style)
        clone.add_edge(first, second, path=path, kind=curve.kind, points=curve.points, boundary=chain_b.name, role="wall", provenance="supplied point list")
    # The wake line - the edge's new spoke and B's existing spoke - and the
    # wake fronts, then B's two new band spokes.
    _add_line(clone, gate_te, ring_r, "wake", "wake separatrix")
    spoke_b = clone.edges[clone.edge_key(ring_r, f_b)]
    spoke_b.role = "wake"
    spoke_b.provenance = "wake separatrix"
    for first, second in ((f_in, ring_in), (f_out, ring_out), (ring_in, land_out_key), (ring_out, land_in_key)):
        _add_line(clone, first, second, "wake_front", "wake band front")
    for first, second in ((foot_in_key, land_in_key), (foot_out_key, land_out_key)):
        _add_line(clone, first, second, "layer_spoke", "wall normal")

    # The twelve faces.
    clone.add_face((f_prev_out, f_in, ring_in, ring_prev), role="core", provenance="annular core patch")
    clone.add_face((gate_te, ring_r, ring_in, f_in), role="wake", provenance="wake band")
    clone.add_face((gate_te, f_out, ring_out, ring_r), role="wake", provenance="wake band")
    clone.add_face((f_out, f_next_in, ring_next, ring_out), role="core", provenance="annular core patch")
    clone.add_face((fb_prev_out, land_in_key, ring_out, ring_next), role="core", provenance="annular core patch")
    clone.add_face((land_in_key, f_b, ring_r, ring_out), role="wake", provenance="wake band beyond the ring")
    clone.add_face((f_b, land_out_key, ring_in, ring_r), role="wake", provenance="wake band beyond the ring")
    clone.add_face((land_out_key, fb_next_in, ring_prev, ring_in), role="core", provenance="annular core patch")
    clone.add_face((gate_prev_b, foot_in_key, land_in_key, fb_prev_out), role="layer", provenance="boundary-layer band")
    clone.add_face((foot_in_key, gate_b, f_b, land_in_key), role="layer", provenance="boundary-layer band")
    clone.add_face((gate_b, foot_out_key, land_out_key, f_b), role="layer", provenance="boundary-layer band")
    clone.add_face((foot_out_key, gate_next_b, fb_next_in, land_out_key), role="layer", provenance="boundary-layer band")

    edges, vertices = clone.unused_entities()
    clone.discard(edges, vertices)
    record["wake_points"] = {label: [float(v) for v in point] for label, point in new_points.items()}
    record["band_height_at_edge"] = float(height)
    record["landing_stations"] = {"in": feet["in"], "out": feet["out"], "gate": station_gate_b}
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
