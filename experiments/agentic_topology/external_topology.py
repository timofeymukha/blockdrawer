"""External flow: medial scaffold plus wall bands, written as a patch graph.

The generalized medial graph and its relaxed annular layout stay as they were -
they remain the only construction here that reliably connects several disjoint
bodies and a far field.  What changes is that the physical walls are no longer
the boundary of the core: every wall chain is peeled off into a
clearance-limited boundary-layer band first, and the core patches start at the
band's front.

Sharp convex wall features get a **band seam** here: a single offset point at a
feature whose fluid sector is wider than about 250 degrees lies on the bisector,
which leans away from both wall sides, and the two band blocks that share it
fold over each other.  The seam gives the feature one front vertex per incident
wall side plus a wedge block back to the medial anchor, so the feature carries
three incident blocks instead of two.

That is only the *first* topology offered at a sharp feature.  Whether it is the
one that survives is decided afterwards by ``fan_cavity``, which owns every
feature cavity, generates cavity-compatible alternatives, validates each one
against the whole embedded graph and applies at most one atomically.  Nothing in
this module builds a fan any more, and nothing here drops a whole chain's band
because one wedge folds: an unrepairable feature is reported as a located cavity
instead.

Nothing in this module is annulus specific in its output.  It writes vertices,
shared edges and quadrilateral faces into ``patch_graph.PatchGraph``, which is
also what the sweep/submapping producer writes, so both paths share validation,
cell-count quantisation, session emission and quality evaluation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
import patch_graph as pg


@dataclass
class AssemblyResult:
    graph: pg.PatchGraph
    fronts: dict[int, layer_module.Front]
    layer_faces: set[tuple]
    fans: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    seams: list[dict] = field(default_factory=list)


def _ring_key(cut) -> tuple:
    return ("ring", *cut.anchor.key)


def _gate_key(cell: int, cut) -> tuple:
    return ("gate", cell, *cut.anchor.key)


def _front_key(cell: int, cut, side: str | None = None) -> tuple:
    if side is None:
        return ("front", cell, *cut.anchor.key)
    return ("front", cell, *cut.anchor.key, side)


def _front_keys(cell: int, cut, front, order: int) -> tuple[tuple, tuple]:
    """The front vertices a gate presents to its incoming and outgoing block."""
    if front is not None and front.is_seam(order):
        return _front_key(cell, cut, "in"), _front_key(cell, cut, "out")
    key = _front_key(cell, cut)
    return key, key


def _path_curve(path: np.ndarray, style: str = "polyLine") -> tuple[str, tuple]:
    interior = path[1:-1]
    if len(interior) == 0:
        return "line", ()
    return style, tuple((float(x), float(y)) for x, y in interior)


def build_graph(
    layout,
    domain,
    metric,
    *,
    options: layer_module.LayerOptions | None = None,
    wall_edge_style: str = "polyLine",
    allow_scale: bool = False,
) -> AssemblyResult:
    """Assemble bands, seams, fans and core patches into one patch graph.

    ``allow_scale`` lets a front that no local repair admits fall back to a
    uniformly thinner band instead of failing; the pipeline passes it on its
    last band-repair round, after inserting anchors has been tried.
    """
    settings = options or layer_module.LayerOptions()
    diagram = layout.diagram
    notes: list[str] = []
    graph = pg.PatchGraph(
        euler_characteristic=domain.euler_characteristic,
        boundary_roles={chain.name: chain.role for chain in domain.chains()},
    )
    # Obstacles for the fronts: every hole as one closed loop - a hole made of
    # several wall chains must not be mistaken for several other walls - plus
    # any wall chain of the outer boundary.
    wall_loops = [loop.points() for loop in domain.holes] + [
        chain.points for chain in domain.outer.chains if chain.is_wall
    ]
    outer_walls = [chain.name for chain in domain.outer.chains if chain.is_wall]
    if outer_walls:
        notes.append(
            "outer wall chain(s) "
            + ", ".join(outer_walls)
            + " get no boundary-layer band: the external producer bands whole "
            "hole loops only"
        )
    fronts: dict[int, layer_module.Front] = {}
    gate_stations: dict[int, list[float]] = {}
    layer_faces: set[tuple] = set()
    fans: list[dict] = []
    seams: list[dict] = []
    failures: list[dict] = []

    for cell_cuts in layout.cuts:
        for cut in cell_cuts:
            graph.add_vertex(
                _ring_key(cut),
                cut.ring_point,
                constraint=pg.Constraint("guide", "medial", cut.ring_station),
                provenance=f"medial:{cut.anchor.kind}",
            )

    for cell_index, cell in enumerate(layout.cells):
        site = diagram.sites[cell.site]
        cuts = layout.cuts[cell_index]
        if not settings.enabled or site.curve.kind != "wall":
            continue
        caps = [
            settings.clearance_fraction
            * float(np.linalg.norm(cut.ring_point - cut.wall_point))
            for cut in cuts
        ]
        stations, snapped = _snap_sharp_gates(site, cuts, settings)
        gate_stations[cell_index] = stations
        notes.extend(snapped)
        # A band block that touches a sharp wall feature belongs to the cavity
        # stage, which will replace it.  Judging it here - and abandoning the
        # whole chain's boundary layer when it cannot be made convex by
        # thinning - would reject a topology that is never built.
        exempt = _feature_orders(site, stations, settings)
        try:
            front = layer_module.build_front(
                site.curve.loop(),
                stations,
                fluid_sign=site.curve.fluid_sign,
                wall_loops=wall_loops,
                requested=metric.layer_height,
                options=settings,
                obstacles=[front.front for front in fronts.values()],
                station_caps=caps,
                floor_height=layer_module.floor_height(settings, metric),
                exempt_orders=exempt,
                scaffold=cell.ring,
                site=cell.site,
                name=site.name,
                allow_scale=allow_scale,
            )
            notes.extend(front.notes)
            fronts[cell_index] = front
            seams.extend(_seam_records(front, cuts, cell_index, site))
        except layer_module.LayerError as error:
            failures.append(
                {
                    "stage": "boundary_layer_front",
                    "chain": site.name,
                    "cell": cell_index,
                    "gate_orders": [] if error.sharp else list(error.orders),
                    "sharp_feature": error.sharp,
                    "gate_points": [
                        [
                            float(cuts[order].wall_point[0]),
                            float(cuts[order].wall_point[1]),
                        ]
                        for order in error.orders
                        if order < len(cuts)
                    ],
                    "reason": str(error),
                }
            )

    for cell_index, cell in enumerate(layout.cells):
        site = diagram.sites[cell.site]
        cuts = layout.cuts[cell_index]
        count = len(cuts)
        front = fronts.get(cell_index)
        for index, cut in enumerate(cuts):
            point = (
                front.wall[front.gate_indices[index]]
                if front is not None
                else cut.wall_point
            )
            station = (
                gate_stations[cell_index][index]
                if cell_index in gate_stations
                else cut.wall_station
            )
            graph.add_vertex(
                _gate_key(cell_index, cut),
                point,
                constraint=pg.Constraint(
                    "chain", site.chain_at(station).name, station
                ),
                provenance="gate",
            )
            if front is None:
                continue
            incoming, outgoing = _front_keys(cell_index, cut, front, index)
            graph.add_vertex(
                incoming,
                front.point_at(index, "in"),
                constraint=pg.Constraint("guide", f"front:{site.name}", float(index)),
                provenance="layer front",
            )
            if outgoing != incoming:
                graph.add_vertex(
                    outgoing,
                    front.point_at(index, "out"),
                    constraint=pg.Constraint(
                        "guide", f"front:{site.name}", float(index)
                    ),
                    provenance="layer front seam",
                )

        for index in range(count):
            following = (index + 1) % count
            first = cuts[index]
            second = cuts[following]
            gate_first = _gate_key(cell_index, first)
            gate_second = _gate_key(cell_index, second)
            ring_first = _ring_key(first)
            ring_second = _ring_key(second)
            if front is not None:
                wall_path = layer_module.ring_slice(
                    front.wall,
                    front.gate_indices[index],
                    front.gate_indices[following],
                )
                wall_kind, wall_points = _path_curve(wall_path, wall_edge_style)
                station_first = gate_stations[cell_index][index]
                station_second = gate_stations[cell_index][following]
            else:
                wall_path = site.curve.section(
                    first.wall_station, second.wall_station, forward=True
                )
                wall_kind, wall_points = _wall_geometry(
                    site, first, second, wall_edge_style
                )
                station_first = first.wall_station
                station_second = second.wall_station
            # The section lies inside one named chain because every chain
            # break is a gate; the chain is read at the section's midpoint.
            chain = site.section_chain(station_first, station_second)
            graph.add_edge(
                gate_first,
                gate_second,
                path=wall_path,
                kind=wall_kind,
                points=wall_points,
                boundary=chain.name,
                role="wall",
                provenance="supplied point list",
            )
            outer_first, outer_second = gate_first, gate_second
            if front is not None:
                outer_first = _front_keys(cell_index, first, front, index)[1]
                outer_second = _front_keys(cell_index, second, front, following)[0]
                front_path = layer_module.ring_slice(
                    front.front,
                    front.gate_indices[index],
                    front.gate_indices[following],
                ).copy()
                front_path[0] = graph.vertices[outer_first].point
                front_path[-1] = graph.vertices[outer_second].point
                kind, points = _path_curve(front_path)
                graph.add_edge(
                    outer_first,
                    outer_second,
                    path=front_path,
                    kind=kind,
                    points=points,
                    role="front",
                    provenance="clearance-limited offset",
                )
                for gate_key, front_key in (
                    (gate_first, outer_first),
                    (gate_second, outer_second),
                ):
                    _add_line(
                        graph, gate_key, front_key, "layer_spoke", "wall normal"
                    )
                face = graph.add_face(
                    (gate_first, gate_second, outer_second, outer_first),
                    role="layer",
                    provenance="boundary-layer band",
                )
                layer_faces.add(face.key)
            ring_path = g2.loop_section(
                cell.ring, first.ring_station, second.ring_station, forward=True
            )
            kind, points = _path_curve(ring_path)
            graph.add_edge(
                ring_first,
                ring_second,
                path=ring_path,
                kind=kind,
                points=points,
                role="ring",
                provenance="medial branch",
            )
            for outer, ring in ((outer_first, ring_first), (outer_second, ring_second)):
                _add_line(graph, outer, ring, "core_spoke", "annular cut")
            graph.add_face(
                (outer_first, outer_second, ring_second, ring_first),
                role="core",
                provenance="annular core patch",
            )
        if front is not None:
            for order in sorted(front.seams):
                cut = cuts[order]
                incoming, outgoing = _front_keys(cell_index, cut, front, order)
                graph.add_face(
                    (_gate_key(cell_index, cut), outgoing, _ring_key(cut), incoming),
                    role="core",
                    provenance="sharp-feature band seam",
                )
    return AssemblyResult(graph, fronts, layer_faces, fans, notes, failures, seams)


def _wall_geometry(site, first, second, style: str) -> tuple[str, tuple]:
    curve = site.curve.edge_curve(
        first.wall_station, second.wall_station, forward=True, style=style
    )
    return curve.kind, curve.points


def _snap_sharp_gates(site, cuts, settings):
    """Move a gate exactly onto a sharp wall feature it is already next to.

    A wall vertex whose fluid sector is wider than ``sharp_fluid_angle`` cannot
    sit inside a block edge: the two band blocks that would span it offset in
    almost opposite directions and fold.  The gate nearest such a vertex is
    therefore moved onto it, provided the move keeps the gate order, and the
    move is reported.
    """
    stations = [float(cut.wall_station) for cut in cuts]
    if len(stations) < 3:
        return stations, []
    loop = site.curve.loop()
    cumulative = g2.cumulative_length(loop)
    total = float(cumulative[-1])
    angles = layer_module.fluid_angles(loop, site.curve.fluid_sign)
    limit = math.radians(settings.sharp_fluid_angle)
    notes: list[str] = []
    for index in range(len(loop) - 1):
        if float(angles[index]) < limit:
            continue
        station = float(cumulative[index])
        order = sorted(range(len(stations)), key=lambda item: stations[item])
        gaps = [
            min((station - value) % total, (value - station) % total)
            for value in stations
        ]
        nearest = int(np.argmin(gaps))
        if gaps[nearest] <= 1.0e-12 * total:
            continue
        position = order.index(nearest)
        before = stations[order[(position - 1) % len(order)]]
        after = stations[order[(position + 1) % len(order)]]
        if (station - before) % total <= 0.0 or (after - station) % total <= 0.0:
            notes.append(
                f"{site.name}: a sharp wall feature at arc length {station:.6f} "
                f"cannot become a gate without reordering the cuts"
            )
            continue
        notes.append(
            f"{site.name}: gate moved from {stations[nearest]:.6f} onto the sharp "
            f"feature at {station:.6f} "
            f"({math.degrees(float(angles[index])):.1f} degree fluid sector)"
        )
        stations[nearest] = station
    return stations, notes


def _feature_orders(site, stations, settings) -> set[int]:
    """Gate orders whose band block touches a sharp convex wall feature."""
    augmented, gate_indices = layer_module.insert_stations(
        site.curve.loop(), stations, closed=True
    )
    angles = layer_module.fluid_angles(augmented, site.curve.fluid_sign)
    count = len(gate_indices)
    exempt: set[int] = set()
    for order, index in enumerate(gate_indices):
        if math.degrees(float(angles[index])) < settings.feature_fluid_angle:
            continue
        exempt.update({(order - 1) % count, order})
    return exempt


def _seam_records(front, cuts, cell_index: int, site) -> list[dict]:
    """Describe every band seam, including the ones whose wedge folds.

    The wedge only closes when the medial anchor lies inside the feature's own
    fluid sector.  When the medial branch leaves the feature off to one side -
    which is what happens at a trailing edge whose wake branch is not aligned
    with the surface bisector - the wedge folds.  That used to cost the whole
    chain its boundary layer; now the folded wedge is left in place as a
    located, invalid cavity for ``fan_cavity`` to replace or to report.
    """
    records: list[dict] = []
    for order in sorted(front.seams):
        cut = cuts[order]
        quad = np.asarray(
            [
                front.wall[front.gate_indices[order]],
                front.point_at(order, "out"),
                cut.ring_point,
                front.point_at(order, "in"),
            ]
        )
        convex = bool(g2.strictly_convex(quad))
        record = {
            "chain": site.name,
            "gate": order,
            "cell": cell_index,
            "point": [float(cut.wall_point[0]), float(cut.wall_point[1])],
            "incident_blocks_at_feature": 3,
            "wedge_is_convex": convex,
            "corner_angles_degrees": [
                float(math.degrees(value)) for value in g2.quad_corner_angles(quad)
            ],
            "index_change": (
                "wall vertex -1, two front vertices +1 each, medial anchor -1"
            ),
        }
        if not convex:
            record["problem"] = (
                "the wedge block back to the medial anchor folds: the anchor is "
                "not inside this feature's fluid sector. The cavity stage owns "
                "this feature and reports which alternatives it rejected."
            )
        records.append(record)
    return records


def _add_line(graph, first, second, role: str, provenance: str) -> None:
    graph.add_edge(
        first,
        second,
        path=np.asarray(
            [graph.vertices[first].point, graph.vertices[second].point]
        ),
        role=role,
        provenance=provenance,
    )
