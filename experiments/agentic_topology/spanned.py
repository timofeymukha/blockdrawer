"""Span a narrow body-body medial branch with one strip of core blocks.

In the annular construction every medial branch is a block edge: a body's core
patches run from its band front to the branch, and the body on the other side
has its own patches up to the same branch.  Inside a narrow gap that stacks
five layers - band, half-core, medial line, half-core, band - into a slot a
few band heights wide, and every three-way junction becomes an interior vertex
of valence six.

Where a branch is *spanned* the branch is no longer an edge.  The two
half-cores become a single strip of blocks between the two fronts, one block
per anchor interval, whose rungs join the two bodies' front points that share
an anchor.  Each junction of a spanned branch dissolves: with the two other
branches at the junction still rings, the region around it is the hexagon

    f_a_J, f_a_1, ring_A1, ring_B1, f_b_1, f_b_J

which two quadrilaterals fill - an *extended strip block* ``(f_a_J, f_a_1,
f_b_1, f_b_J)`` and a *mouth block* ``(f_a_1, f_b_1, ring_B1, ring_A1)`` whose
far edge is the medial polyline through the junction.  The far-field cell's
two patches beside the junction merge into one, and the junction vertex, its
far-field gate and the ring edges along the spanned branch disappear.  The
junction's index of -2 moves to the two front vertices one gate out from the
mouth, which become valence five; every other vertex stays regular.

A branch is spanned as a whole or not at all: switching mode inside a branch
would need the strip to become two layers again at the switch.  A junction
with more than one narrow branch is not handled yet and keeps its rings, and
the reason is recorded.  Everything is built on a copy of the graph and
validated before it is applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import patch_graph as pg


@dataclass(frozen=True)
class SpanOptions:
    enabled: bool = False
    # A body-body branch is narrow when its smallest clearance - the distance
    # from the branch to either wall - is below this multiple of the requested
    # band height: the two bands would not fit beside a ring there.
    clearance_ratio: float = 1.0
    # At the mouth the vectors from the two closest wall points towards the
    # medial branch must oppose each other by at least this angle. Annular
    # junction gates generally face the far field and cannot see each other
    # across a straight rung.
    mouth_angle: float = 110.0
    # Ring anchors stay inside the first ring interval beside the junction,
    # no farther than this fraction of the distance between the mouth gates.
    mouth_reach: float = 0.3

    def __post_init__(self):
        if not np.isfinite(self.clearance_ratio) or self.clearance_ratio <= 0:
            raise ValueError("span clearance ratio must be positive and finite")
        if not np.isfinite(self.mouth_angle) or not 0 < self.mouth_angle < 180:
            raise ValueError("span mouth angle must be between 0 and 180 degrees")
        if not np.isfinite(self.mouth_reach) or self.mouth_reach <= 0:
            raise ValueError("span mouth reach must be positive and finite")


@dataclass
class SpanResult:
    graph: pg.PatchGraph | None
    branches: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def spanned(self) -> list[int]:
        return [item["branch"] for item in self.branches if item.get("spanned")]


def _ring_key(anchor_key) -> tuple:
    return ("ring", *anchor_key)


def _gate_key(cell: int, anchor_key) -> tuple:
    return ("gate", cell, *anchor_key)


def plan(layout, metric, options: SpanOptions | None = None) -> list[dict]:
    """Decide, per medial branch, whether it is spanned, with the reason."""
    settings = options or SpanOptions()
    diagram = layout.diagram
    records: list[dict] = []
    for branch in diagram.branches:
        record = {"branch": branch.index, "spanned": False}
        pair = list(branch.pair)
        record["sites"] = [diagram.sites[index].name for index in pair]
        if branch.closed or any(end is None for end in branch.ends):
            record["reason"] = "a closed or open branch has no junction to dissolve"
            records.append(record)
            continue
        if any(diagram.sites[index].curve.kind != "wall" for index in pair):
            record["reason"] = "a body-to-farfield branch keeps its ring"
            records.append(record)
            continue
        clearance = min(
            float(np.min(diagram.sites[index].curve.distance(branch.path)))
            for index in pair
        )
        record["minimum_clearance"] = clearance
        record["clearance_over_band_height"] = (
            clearance / metric.layer_height if metric.layer_height > 0 else None
        )
        if clearance >= settings.clearance_ratio * metric.layer_height:
            record["reason"] = (
                "the gap is wider than the requested band height; the branch "
                "keeps its ring"
            )
            records.append(record)
            continue
        record["spanned"] = settings.enabled
        if not settings.enabled:
            record["reason"] = "spanning is disabled"
        records.append(record)
    # A junction can dissolve only if exactly one of its branches is spanned.
    by_junction: dict[int, list[int]] = {}
    for record in records:
        if not record["spanned"]:
            continue
        branch = diagram.branches[record["branch"]]
        for end in branch.ends:
            by_junction.setdefault(end[0], []).append(branch.index)
    for junction, indices in by_junction.items():
        if len(indices) <= 1:
            continue
        for record in records:
            if record["branch"] in indices and record["spanned"]:
                record["spanned"] = False
                record["reason"] = (
                    f"junction {junction} has {len(indices)} narrow branches; "
                    "dissolving a junction with more than one is not built yet"
                )
    return records


def prepare(layout, records: list[dict], options: SpanOptions) -> list[str]:
    """Place mutually facing mouths on a trial layout before building bands.

    Each end searches its first branch interval for opposing wall normals.
    The mouth gates are the wall projections there; the old junction gates
    move halfway towards the next gate inside the gap. New ring anchors and
    their far-field gates are placed together. These are variables of the
    spanned construction, so the annular objective must not relax them again.
    Callers own the trial layout and discard it if any later check fails.
    """
    import block_layout

    diagram = layout.diagram
    cell_of_site = {cell.site: index for index, cell in enumerate(layout.cells)}
    notes: list[str] = []
    for record in records:
        if not record.get("spanned"):
            continue
        branch = diagram.branches[record["branch"]]
        cells = [cell_of_site[site] for site in branch.pair]
        sequence = _ordered_anchors(layout, branch.index, cells[0])
        if len(sequence) < 3:
            raise pg.GraphError("a gap needs an interior anchor between its mouths")
        for junction_key, inner_key in ((sequence[0], sequence[1]), (sequence[-1], sequence[-2])):
            junction = diagram.junctions[junction_key[1]]
            third = set(junction.sites) - set(branch.pair)
            if len(third) != 1 or diagram.sites[next(iter(third))].curve.kind == "wall":
                raise pg.GraphError("a mouth beside a third wall needs a separate transition template")
            cuts_a = {cut.anchor.key: cut for cut in layout.cuts[cells[0]]}
            endpoint = _end_station(branch, junction.point)
            inside = cuts_a[inner_key].anchor.position
            projections = _mouth_projections(diagram, branch, endpoint, inside, options.mouth_angle)
            width = float(np.linalg.norm(projections[0].point[0] - projections[1].point[0]))
            for cell_index, projection in zip(cells, projections):
                cell = layout.cells[cell_index]
                site = diagram.sites[cell.site]
                total = site.curve.length()
                cuts = layout.cuts[cell_index]
                positions = _cuts_by_key(cuts)
                order = positions[junction_key]
                # Ring order, rather than shortest arc length, determines the
                # physical side of a junction even across the cyclic wrap.
                inward = 1 if cuts[(order + 1) % len(cuts)].anchor.key == inner_key else -1
                neighbour_key = _neighbour_off_branch(layout, cell_index, junction_key, set(sequence))
                neighbour = cuts[positions[neighbour_key]]
                ring_branch = diagram.branches[neighbour.anchor.branch]
                if ring_branch.index == branch.index or not any(
                    end is not None and end[0] == junction_key[1] for end in ring_branch.ends
                ):
                    raise pg.GraphError("no ring interval is available beside the junction")
                station = float(projection.arclength[0])
                inner_station = cuts[positions[inner_key]].wall_station
                span = ((inner_station - station) * inward) % total
                available = ((inner_station - neighbour.wall_station) * inward) % total
                if span <= 1e-7 * total or span >= available:
                    raise pg.GraphError("the facing mouth would collide with or pass an existing wall gate")
                junction_cut = cuts[order]
                junction_cut.wall_station = (station + inward * span * 0.5) % total
                junction_cut.wall_point = site.curve.point_at(junction_cut.wall_station)
                ring_end = _end_station(ring_branch, junction.point)
                ring_span = neighbour.anchor.position - ring_end
                distance = min(options.mouth_reach * width, abs(ring_span) * 0.5)
                anchor = block_layout.Anchor(
                    ring_branch.index,
                    ring_end + np.sign(ring_span) * distance,
                    "mouth",
                    hint=(cell.site, station),
                )
                if not layout.anchors.add(anchor, force=True):
                    raise pg.GraphError("a mouth anchor coincides with an existing ring cut")
                # Add just this cut: rebuilding all cuts would restore the
                # annular projections at the junctions we have just moved.
                for other_site in ring_branch.pair:
                    other_cell = cell_of_site[other_site]
                    measured = layout.anchors._measure(other_cell, anchor)
                    layout.cuts[other_cell].append(block_layout.Cut(other_cell, anchor, *measured))
                notes.append(
                    f"junction {junction_key[1]}: facing mouth on {site.name} "
                    f"at wall station {station:.6g}, ring branch {ring_branch.index}"
                )
            layout.refresh()
    return notes


def _end_station(branch, point) -> float:
    return (0.0 if np.linalg.norm(branch.path[0] - point) < np.linalg.norm(branch.path[-1] - point)
            else g2.total_length(branch.path))


def _mouth_projections(diagram, branch, endpoint, inside, angle):
    """First opposing-wall location in the end interval; no monotonicity assumed."""
    stations = np.linspace(endpoint, inside, 129)
    points = g2.sample_at_arclength(branch.path, stations)
    closest = [diagram.sites[site].curve.closest(points) for site in branch.pair]
    directions = [(points - item.point) / np.maximum(item.distance[:, None], 1e-300) for item in closest]
    dots = np.sum(directions[0] * directions[1], axis=1)
    matches = np.flatnonzero(dots <= np.cos(np.radians(angle)))
    if not len(matches) or matches[0] == len(stations) - 1:
        raise pg.GraphError("no mutually facing mouth fits before the next branch anchor")
    point = points[int(matches[0])]
    return [diagram.sites[site].curve.closest(point[None, :]) for site in branch.pair]


def _cuts_by_key(cuts) -> dict[tuple, int]:
    return {cut.anchor.key: position for position, cut in enumerate(cuts)}


def _front_keys(graph: pg.PatchGraph, cell: int, anchor_key) -> tuple[tuple, tuple]:
    """A cell's (incoming, outgoing) front vertex keys at one anchor."""
    single = ("front", cell, *anchor_key)
    if single in graph.vertices:
        return single, single
    incoming = ("front", cell, *anchor_key, "in")
    outgoing = ("front", cell, *anchor_key, "out")
    if incoming in graph.vertices and outgoing in graph.vertices:
        return incoming, outgoing
    raise pg.GraphError(f"cell {cell} has no front vertex at {anchor_key!r}")


def _face_with_corners(graph: pg.PatchGraph, corners) -> pg.PGFace | None:
    wanted = set(corners)
    for face in graph.faces:
        if set(face.corners) == wanted:
            return face
    return None


def _ordered_anchors(layout, branch_index: int, cell_index: int) -> list[tuple]:
    """Anchor keys along one branch in the order a cell's cuts meet them.

    Junction anchors are recognised by their key, because the anchor object a
    cell holds for a junction may belong to another branch that ends there.
    """
    diagram = layout.diagram
    branch = diagram.branches[branch_index]
    ends = {("junction", end[0]) for end in branch.ends}
    cuts = layout.cuts[cell_index]
    positions = [
        index
        for index, cut in enumerate(cuts)
        if cut.anchor.key in ends or cut.anchor.branch == branch_index
    ]
    if len(positions) < 2:
        raise pg.GraphError("a spanned branch needs its two junction cuts")
    # The branch's cuts are contiguous in the cell's cyclic order; rotate so
    # that the run does not wrap.
    count = len(cuts)
    marks = set(positions)
    start = next((position for position in positions if (position - 1) % count not in marks), None)
    if start is None:
        raise pg.GraphError("the cell has no ring interval outside the spanned branch")
    ordered = []
    position = start
    while position in marks:
        ordered.append(cuts[position].anchor.key)
        position = (position + 1) % count
        if position == start:
            break
    return ordered


def apply(layout, graph: pg.PatchGraph, records: list[dict], *, wall_edge_style="polyLine") -> SpanResult:
    """Return a new graph with every planned branch spanned; never mutate."""
    result = SpanResult(graph, [dict(item) for item in records])
    current = graph
    for record in result.branches:
        if not record["spanned"]:
            continue
        before = current.problems()
        try:
            candidate = _span_branch(layout, current, record["branch"], wall_edge_style)
        except pg.GraphError as error:
            record["spanned"] = False
            record["reason"] = f"could not be spanned: {error}"
            continue
        after = candidate.problems()
        fresh = [item for item in after if item not in before]
        if fresh:
            record["spanned"] = False
            record["reason"] = (
                "spanning introduced graph problems and was reverted: "
                + "; ".join(sorted({item["kind"] for item in fresh}))
            )
            record["problems"] = fresh[:4]
            continue
        record["strip_blocks"] = sum(
            face.provenance in ("spanned gap strip", "spanned gap strip end")
            for face in candidate.faces
        ) - sum(
            face.provenance in ("spanned gap strip", "spanned gap strip end")
            for face in current.faces
        )
        record["junctions_dissolved"] = [
            end[0] for end in layout.diagram.branches[record["branch"]].ends
        ]
        current = candidate
        result.notes.append(
            f"branch {record['branch']} ({'/'.join(record['sites'])}) spanned: "
            f"{record.get('strip_blocks', 0)} strip blocks, junctions dissolved "
            f"{record.get('junctions_dissolved', [])}"
        )
    result.graph = current
    return result


def _span_branch(layout, graph: pg.PatchGraph, branch_index: int, wall_edge_style: str) -> pg.PatchGraph:
    diagram = layout.diagram
    branch = diagram.branches[branch_index]
    cell_of_site = {cell.site: index for index, cell in enumerate(layout.cells)}
    cell_a = cell_of_site[branch.pair[0]]
    cell_b = cell_of_site[branch.pair[1]]
    clone = graph.copy()
    anchors_a = _ordered_anchors(layout, branch_index, cell_a)
    anchors_b = _ordered_anchors(layout, branch_index, cell_b)
    if set(anchors_a) != set(anchors_b):
        raise pg.GraphError("the two cells disagree about the anchors of the branch")
    if anchors_b != list(reversed(anchors_a)):
        raise pg.GraphError("the branch anchors are not mirrored between the cells")
    # Along the branch, in A's direction: A's block between anchors k, k+1
    # uses A's outgoing front at k and incoming at k+1; B, traversing the
    # branch the other way, uses its outgoing at k+1 and incoming at k.
    sequence = anchors_a
    ring_keys = [_ring_key(key) for key in sequence]

    # 1. Remove both cells' core patches and seam wedges along the branch.
    removed = 0
    for face in list(clone.faces):
        corners = set(face.corners)
        rings = corners & set(ring_keys)
        if not rings or face.role != "core":
            continue
        kinds = sorted(corner[0] for corner in corners)
        if kinds == ["front", "front", "gate", "ring"] and len(rings) == 1:
            # A seam wedge (gate, out, ring, in) whose apex lies on the
            # branch.  The apex goes away with the branch; the wedge is
            # rebuilt below on the other body's front.
            clone.remove_faces([face.key])
            removed += 1
            continue
        if len(rings) == 2 and kinds == ["front", "front", "ring", "ring"]:
            # A core patch of either body along the branch.
            clone.remove_faces([face.key])
            removed += 1
    if removed == 0:
        raise pg.GraphError("no core patch lies along the branch")

    # 2. Strip blocks and rebuilt seam wedges, one per anchor interval.
    for k in range(len(sequence) - 1):
        key_k, key_next = sequence[k], sequence[k + 1]
        a_out = _front_keys(clone, cell_a, key_k)[1]
        a_in = _front_keys(clone, cell_a, key_next)[0]
        b_in = _front_keys(clone, cell_b, key_k)[0]
        b_out = _front_keys(clone, cell_b, key_next)[1]
        for first, second in ((a_out, b_in), (a_in, b_out)):
            _add_line(clone, first, second, "core_rung", "gap strip rung")
        clone.add_face((a_out, a_in, b_out, b_in), role="core", provenance="spanned gap strip")
    for key in sequence[1:-1]:
        for cell, other in ((cell_a, cell_b), (cell_b, cell_a)):
            incoming, outgoing = _front_keys(clone, cell, key)
            if incoming == outgoing:
                continue
            apex = _front_keys(clone, other, key)
            if apex[0] != apex[1]:
                raise pg.GraphError("two seams face each other across the branch")
            gate = _gate_key(cell, key)
            _add_line(clone, incoming, apex[0], "core_rung", "gap seam side")
            _add_line(clone, outgoing, apex[0], "core_rung", "gap seam side")
            clone.add_face((gate, outgoing, apex[0], incoming), role="core", provenance="sharp-feature gap seam")

    # 3. Dissolve the two junctions.
    for end_key, position in ((sequence[0], 0), (sequence[-1], -1)):
        _dissolve_junction(layout, clone, branch_index, end_key, cell_a, cell_b, sequence, position, wall_edge_style)

    # 4. Drop everything nothing refers to any more.
    edges, vertices = clone.unused_entities()
    clone.discard(edges, vertices)
    return clone


def _add_line(graph: pg.PatchGraph, first, second, role: str, provenance: str) -> None:
    graph.add_edge(
        first,
        second,
        path=np.asarray([graph.vertices[first].point, graph.vertices[second].point]),
        role=role,
        provenance=provenance,
    )


def _neighbour_off_branch(layout, cell_index: int, junction_key, on_branch: set) -> tuple:
    """The cut next to a junction cut that does not lie on the spanned branch."""
    cuts = layout.cuts[cell_index]
    positions = _cuts_by_key(cuts)
    position = positions[junction_key]
    count = len(cuts)
    for step in (1, -1):
        candidate = cuts[(position + step) % count].anchor.key
        if candidate not in on_branch:
            return candidate
    raise pg.GraphError("every neighbouring cut lies on the spanned branch")


def _dissolve_junction(layout, clone, branch_index, junction_key, cell_a, cell_b, sequence, position, wall_edge_style):
    diagram = layout.diagram
    junction_index = junction_key[1]
    junction = diagram.junctions[junction_index]
    on_branch = set(sequence)
    ring_j = _ring_key(junction_key)
    # A's and B's next anchors away from the branch, and their ring vertices.
    key_a1 = _neighbour_off_branch(layout, cell_a, junction_key, on_branch)
    key_b1 = _neighbour_off_branch(layout, cell_b, junction_key, on_branch)
    if key_a1 == key_b1:
        raise pg.GraphError("the ring branches beside the junction have no interior anchor")
    if key_a1 in on_branch or key_b1 in on_branch:
        raise pg.GraphError("a ring branch beside the junction has no interior anchor")
    ring_a1, ring_b1 = _ring_key(key_a1), _ring_key(key_b1)
    # Front vertices at the junction gate (single) and one gate out.
    a_j = _front_keys(clone, cell_a, junction_key)
    b_j = _front_keys(clone, cell_b, junction_key)
    if a_j[0] != a_j[1] or b_j[0] != b_j[1]:
        raise pg.GraphError("a junction gate carries a seam")
    f_a_j, f_b_j = a_j[0], b_j[0]
    a_1 = _front_keys(clone, cell_a, key_a1)
    b_1 = _front_keys(clone, cell_b, key_b1)
    # Which of the two front points at the next gate faces the junction: the
    # one that shares a face with the junction's front vertex.
    f_a_1 = _facing(clone, a_1, f_a_j)
    f_b_1 = _facing(clone, b_1, f_b_j)
    # The third site's cell (the far field, or another body) at this junction.
    third = [site for site in junction.sites if site not in (layout.cells[cell_a].site, layout.cells[cell_b].site)]
    if len(third) != 1:
        raise pg.GraphError("the junction does not have exactly one other site")
    cell_c = next(index for index, cell in enumerate(layout.cells) if cell.site == third[0])
    gate_c_j = _gate_key(cell_c, junction_key)
    gate_c_a1 = _gate_key(cell_c, key_a1)
    gate_c_b1 = _gate_key(cell_c, key_b1)
    for key in (gate_c_j, gate_c_a1, gate_c_b1, ring_a1, ring_b1):
        if key not in clone.vertices:
            raise pg.GraphError(f"expected vertex {key!r} is missing")
    # Remove A's and B's core patches beside the junction and C's two patches.
    for corners in (
        (f_a_j, f_a_1, ring_a1, ring_j),
        (f_b_j, f_b_1, ring_b1, ring_j),
        (gate_c_a1, gate_c_j, ring_j, ring_a1),
        (gate_c_j, gate_c_b1, ring_b1, ring_j),
    ):
        face = _face_with_corners(clone, corners)
        if face is None:
            raise pg.GraphError(f"expected face {corners!r} is missing at the junction")
        clone.remove_faces([face.key])
    # Merged ring edge through the junction, and merged far-side wall edge.
    ring_edge_a = clone.edges[clone.edge_key(ring_a1, ring_j)]
    ring_edge_b = clone.edges[clone.edge_key(ring_j, ring_b1)]
    ring_path = np.vstack([ring_edge_a.directed_path(ring_a1), ring_edge_b.directed_path(ring_j)[1:]])
    clone.add_edge(ring_a1, ring_b1, path=ring_path, kind="polyLine" if len(ring_path) > 2 else "line",
                   points=tuple((float(x), float(y)) for x, y in ring_path[1:-1]), role="ring",
                   provenance="medial branches through a dissolved junction")
    wall_a = clone.edges[clone.edge_key(gate_c_a1, gate_c_j)]
    wall_b = clone.edges[clone.edge_key(gate_c_j, gate_c_b1)]
    wall_path = np.vstack([wall_a.directed_path(gate_c_a1), wall_b.directed_path(gate_c_j)[1:]])
    kind, points = _merged_wall_geometry(layout, cell_c, key_a1, key_b1, junction_key, wall_path, wall_edge_style)
    clone.add_edge(gate_c_a1, gate_c_b1, path=wall_path, kind=kind, points=points,
                   boundary=wall_a.boundary, role="wall", provenance="supplied point list")
    # The two new blocks.
    _add_line(clone, f_a_1, f_b_1, "core_rung", "gap strip end rung")
    clone.add_face((f_a_j, f_a_1, f_b_1, f_b_j), role="core", provenance="spanned gap strip end")
    clone.add_face((f_a_1, f_b_1, ring_b1, ring_a1), role="core", provenance="gap mouth")
    clone.add_face((gate_c_a1, gate_c_b1, ring_b1, ring_a1), role="core", provenance="annular core patch")


def _facing(clone, pair, reference) -> tuple:
    """Of a gate's front vertices, the one sharing a face with ``reference``."""
    incoming, outgoing = pair
    if incoming == outgoing:
        return incoming
    for face in clone.faces:
        if reference in face.corners:
            if incoming in face.corners:
                return incoming
            if outgoing in face.corners:
                return outgoing
    raise pg.GraphError("no front vertex at the next gate faces the junction")


def _merged_wall_geometry(layout, cell_c, key_a1, key_b1, junction_key, wall_path, style):
    """Edge kind and points for a far-side wall edge spanning two sections."""
    site = layout.diagram.sites[layout.cells[cell_c].site]
    cuts = layout.cuts[cell_c]
    stations = {cut.anchor.key: cut.wall_station for cut in cuts}
    if all(key in stations for key in (key_a1, key_b1, junction_key)) and hasattr(site.curve, "edge_curve"):
        try:
            total = site.curve.length()
            start, end, middle = (stations[key] for key in (key_a1, key_b1, junction_key))
            forward = (middle - start) % total < (end - start) % total
            curve = site.curve.edge_curve(start, end, forward=forward, style=style)
            return curve.kind, curve.points
        except Exception:  # pragma: no cover - fall back to the sampled path
            pass
    interior = wall_path[1:-1]
    if len(interior) == 0:
        return "line", ()
    return style, tuple((float(x), float(y)) for x, y in interior)
