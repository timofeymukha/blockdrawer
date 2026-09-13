"""External flow: medial scaffold plus wall bands, written as a patch graph.

The generalized medial graph and its relaxed annular layout stay as they were -
they remain the only construction here that reliably connects several disjoint
bodies and a far field.  What changes is that the physical walls are no longer
the boundary of the core: every wall chain is peeled off into a
clearance-limited boundary-layer band first, and the core patches start at the
band's front.

Sharp convex wall features get a **band seam**.  A single offset point at a
feature whose fluid sector is wider than about 250 degrees lies on the bisector,
which leans away from both wall sides, and the two band blocks that share it
fold over each other.  The seam gives the feature one front vertex per incident
wall side plus a wedge block back to the medial anchor, so the feature carries
three incident blocks instead of two.  The operation is index balanced: the wall
vertex loses one unit of charge, the two front vertices supply one each, and the
medial anchor becomes valence five.

Nothing in this module is annulus specific in its output.  It writes vertices,
shared edges and quadrilateral faces into ``patch_graph.PatchGraph``, which is
also what the sweep/submapping producer writes, so both paths share validation,
cell-count quantisation, session emission and quality evaluation.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
import patch_graph as pg
import patch_solver


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
) -> AssemblyResult:
    """Assemble bands, seams, fans and core patches into one patch graph."""
    settings = options or layer_module.LayerOptions()
    diagram = layout.diagram
    graph = pg.PatchGraph(euler_characteristic=domain.euler_characteristic)
    wall_loops = [chain.points for chain in domain.wall_chains()]
    fronts: dict[int, layer_module.Front] = {}
    fan_geometries: dict[int, dict[int, dict]] = {}
    gate_stations: dict[int, list[float]] = {}
    layer_faces: set[tuple] = set()
    fans: list[dict] = []
    seams: list[dict] = []
    notes: list[str] = []
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
        # A band block beside a sharp feature may be replaced by a fan, so it
        # must not be judged - or repaired - as if it were going to be built.
        augmented, gate_indices = layer_module.insert_stations(
            site.curve.loop(), stations, closed=True
        )
        exempt: set[int] = set()
        for order in _fan_orders(
            augmented, gate_indices, site.curve.fluid_sign, settings
        ):
            exempt.update({(order - 1) % len(cuts), order})
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
                floor_height=settings.minimum_band_cells * metric.first,
                exempt_orders=exempt,
                site=cell.site,
                name=site.name,
            )
            notes.extend(front.notes)
            accepted: dict[int, dict] = {}
            consumed: set[int] = set()
            for gate, sectors in sorted(
                _select_fans(front, cuts, site, settings).items()
            ):
                previous = (gate - 1) % len(cuts)
                if previous in consumed or gate in consumed:
                    fans.append(
                        {
                            "chain": site.name,
                            "gate": gate,
                            "sectors": sectors,
                            "applied": False,
                            "rejection": (
                                "an adjacent feature fan already consumes this band "
                                "interval"
                            ),
                        }
                    )
                    continue
                geometry = _optimise_fan_geometry(
                    cuts, front, site, gate, sectors, settings
                )
                record = {
                    "chain": site.name,
                    "gate": gate,
                    "sectors": sectors,
                    "point": [
                        float(geometry["places"]["corner"][0]),
                        float(geometry["places"]["corner"][1]),
                    ],
                    "scaled_jacobian": geometry["quality"],
                    "combined_scaled_jacobian": geometry["combined_quality"],
                    "corner_angles_degrees": geometry["angles"],
                    "distance_factor": geometry["distance_factor"],
                    "fan_inner": geometry["fan_inner"],
                    "fan_apex": geometry["fan_apex"],
                }
                if geometry["combined_quality"] < settings.fan_minimum_quality:
                    record["applied"] = False
                    record["rejection"] = (
                        "no contained fan and adjacent-core construction reaches "
                        f"the minimum scaled Jacobian {settings.fan_minimum_quality:g}"
                    )
                    fans.append(record)
                    continue
                consumed.update({previous, gate})
                record["applied"] = True
                record["new_faces"] = 5
                record["replaced_faces"] = 2
                fans.append(record)
                accepted[gate] = geometry
                index = front.gate_indices[gate]
                front.front[index] = geometry["places"]["front_corner"]
                front.heights[index] = float(
                    np.linalg.norm(front.front[index] - front.wall[index])
                )
                # A fan owns the complete feature sector. It has one front
                # vertex and must not also receive the two-vertex seam that was
                # only the fallback topology for this sharp point.
                front.seams.pop(gate, None)
            fronts[cell_index] = front
            if accepted:
                fan_geometries[cell_index] = accepted
            rejected = _invalid_seams(front, cuts, cell_index, site)
            if rejected:
                seams.extend(rejected)
                del fronts[cell_index]
                fan_geometries.pop(cell_index, None)
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
                constraint=pg.Constraint("chain", site.name, station),
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

        consumed: set[int] = set()
        for gate, geometry in sorted(fan_geometries.get(cell_index, {}).items()):
            previous = (gate - 1) % count
            consumed.update({previous, gate})
            _add_fan(
                graph,
                cuts,
                front,
                site,
                cell_index,
                gate,
                geometry,
                layer_faces,
                wall_edge_style,
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
            else:
                wall_path = site.curve.section(
                    first.wall_station, second.wall_station, forward=True
                )
                wall_kind, wall_points = _wall_geometry(
                    site, first, second, wall_edge_style
                )
            graph.add_edge(
                gate_first,
                gate_second,
                path=wall_path,
                kind=wall_kind,
                points=wall_points,
                boundary=site.name,
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
                if index not in consumed:
                    for gate_key, front_key in (
                        (gate_first, outer_first),
                        (gate_second, outer_second),
                    ):
                        _add_line(
                            graph,
                            gate_key,
                            front_key,
                            "layer_spoke",
                            "wall normal",
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
                seams.append(
                    {
                        "chain": site.name,
                        "gate": order,
                        "point": [
                            float(cut.wall_point[0]),
                            float(cut.wall_point[1]),
                        ],
                        "incident_blocks_at_feature": 3,
                        "index_change": (
                            "wall vertex -1, two front vertices +1 each, "
                            "medial anchor -1"
                        ),
                    }
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


def _invalid_seams(front, cuts, cell_index: int, site) -> list[dict]:
    """Seams whose wedge block back to the medial anchor is not a quadrilateral.

    The wedge only closes when the medial anchor lies inside the feature's own
    fluid sector.  When the medial branch leaves the feature off to one side -
    which is exactly what happens at a trailing edge whose wake branch is not
    aligned with the surface bisector - the wedge folds, and the honest answer
    is that this feature needs a fan that reaches the front and propagates its
    cuts into the core: a separatrix, which this stage does not trace.
    """
    rejected: list[dict] = []
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
        if g2.strictly_convex(quad):
            continue
        rejected.append(
            {
                "chain": site.name,
                "gate": order,
                "cell": cell_index,
                "point": [float(cut.wall_point[0]), float(cut.wall_point[1])],
                "applied": False,
                "corner_angles_degrees": [
                    float(math.degrees(value))
                    for value in g2.quad_corner_angles(quad)
                ],
                "rejection": (
                    "the wedge block back to the medial anchor folds: the anchor "
                    "is not inside this feature's fluid sector. The band for this "
                    "chain was dropped; resolving it needs a feature fan that "
                    "reaches the layer front and cuts the core, which this stage "
                    "does not construct."
                ),
            }
        )
    return rejected


def _fan_orders(wall: np.ndarray, gate_indices, fluid_sign: float, settings):
    """Gates on a sharp convex wall feature, with the sectors they should carry."""
    if not settings.fan_enabled:
        return {}
    angles = layer_module.fluid_angles(wall, fluid_sign)
    result: dict[int, int] = {}
    for order, vertex in enumerate(gate_indices):
        sectors = layer_module.fan_sectors(float(angles[vertex]), settings)
        if sectors > 2:
            result[order] = sectors
    return result


def _select_fans(front, cuts, site, settings: layer_module.LayerOptions) -> dict:
    return _fan_orders(
        front.wall, front.gate_indices, site.curve.fluid_sign, settings
    )


def _fan_geometry(cuts, front, site, gate, sectors, settings):
    """Points and face corner lists of a contained three-sector fan."""
    count = len(cuts)
    previous = (gate - 1) % count
    following = (gate + 1) % count
    index = front.gate_indices[gate]
    vertices = front.wall[:-1]
    point = vertices[index]
    towards_previous = vertices[(index - 1) % len(vertices)] - point
    towards_next = vertices[(index + 1) % len(vertices)] - point
    near_next, near_previous, apex = layer_module.fan_points(
        point,
        towards_previous / max(float(np.linalg.norm(towards_previous)), 1e-30),
        towards_next / max(float(np.linalg.norm(towards_next)), 1e-30),
        front.front[index],
        settings,
    )
    places = {
        "corner": point,
        "before": front.wall[front.gate_indices[previous]],
        "after": front.wall[front.gate_indices[following]],
        "front_corner": front.front[index],
        "front_before": front.front[front.gate_indices[previous]],
        "front_after": front.front[front.gate_indices[following]],
        "next": near_next,
        "previous": near_previous,
        "apex": apex,
    }
    faces = (
        ("before", "corner", "previous", "front_before"),
        ("corner", "next", "apex", "previous"),
        ("corner", "after", "front_after", "next"),
        ("front_before", "previous", "apex", "front_corner"),
        ("front_corner", "apex", "next", "front_after"),
    )
    worst = math.inf
    angles = []
    for corners in faces:
        quad = np.asarray([places[name] for name in corners])
        crosses = g2.quad_corner_crosses(quad)
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        pairs = sides * np.roll(sides, -1)
        sign = 1.0 if g2.polygon_area(quad) >= 0.0 else -1.0
        worst = min(
            worst,
            float(np.min(sign * crosses / np.where(pairs > 0.0, pairs, 1.0))),
        )
        angles.append(
            [float(math.degrees(value)) for value in g2.quad_corner_angles(quad)]
        )
    return {
        "places": places,
        "faces": faces,
        "quality": worst,
        "angles": angles,
        "previous": previous,
        "following": following,
        "index": index,
        "sectors": sectors,
    }


def _optimise_fan_geometry(cuts, front, site, gate, sectors, settings):
    """Find a contained feature fan that also leaves convex core patches.

    The unmodified offset point at a nearly closed cusp is a poor design
    variable: its miter direction can point away from the medial scaffold even
    though a compact fan exists. Search only intrinsic quantities here -- a
    distance along the feature-to-medial ray and the two dimensionless fan
    fractions. This makes the operation invariant under rigid transforms and
    uniform scale and, unlike a named wake direction, works for arbitrary wall
    features.
    """

    count = len(cuts)
    previous = (gate - 1) % count
    following = (gate + 1) % count
    index = front.gate_indices[gate]
    corner = front.wall[index]
    original = front.front[index].copy()
    direction = cuts[gate].ring_point - corner
    length = float(np.linalg.norm(direction))
    if length <= 1.0e-30:
        direction = original - corner
        length = float(np.linalg.norm(direction))
    if length <= 1.0e-30:
        direction = np.asarray([1.0, 0.0])
        length = 1.0
    direction = direction / length

    seam = front.seams.get(gate)
    if seam is None:
        height = float(np.linalg.norm(original - corner))
    else:
        height = 0.5 * (
            float(np.linalg.norm(seam[0] - corner))
            + float(np.linalg.norm(seam[1] - corner))
        )
    positive = front.heights[front.heights > 1.0e-14]
    if height <= 1.0e-14 and len(positive):
        height = float(np.median(positive))
    height = max(height, 1.0e-12 * max(site.curve.length(), 1.0))

    distance_values = sorted(
        {1.0, *[float(value) for value in np.geomspace(0.1, 30.0, 36)]}
    )
    inner_values = sorted(
        {
            float(settings.fan_inner),
            *[float(value) for value in np.linspace(0.1, 1.5, 22)],
        }
    )
    apex_values = sorted(
        {
            float(settings.fan_apex),
            *[float(value) for value in np.linspace(0.1, 1.5, 22)],
        }
    )
    best = None
    for distance_factor in distance_values:
        front.front[index] = corner + distance_factor * height * direction
        for fan_inner in inner_values:
            for fan_apex in apex_values:
                candidate_settings = dataclasses.replace(
                    settings, fan_inner=fan_inner, fan_apex=fan_apex
                )
                geometry = _fan_geometry(
                    cuts, front, site, gate, sectors, candidate_settings
                )
                before = patch_solver.block_quality(
                    np.asarray(
                        [
                            front.front[front.gate_indices[previous]],
                            front.front[index],
                            cuts[gate].ring_point,
                            cuts[previous].ring_point,
                        ]
                    )
                )
                after = patch_solver.block_quality(
                    np.asarray(
                        [
                            front.front[index],
                            front.front[front.gate_indices[following]],
                            cuts[following].ring_point,
                            cuts[gate].ring_point,
                        ]
                    )
                )
                combined = min(geometry["quality"], before, after)
                # Prefer quality first. The secondary term makes tied searches
                # choose the least geometric change deterministically.
                displacement = (
                    math.log(max(distance_factor, 1.0e-30)) ** 2
                    + math.log(max(fan_inner / settings.fan_inner, 1.0e-30)) ** 2
                    + math.log(max(fan_apex / settings.fan_apex, 1.0e-30)) ** 2
                )
                rank = (combined, -displacement)
                if best is None or rank > best[0]:
                    stored = dict(geometry)
                    stored["places"] = {
                        name: point.copy() for name, point in geometry["places"].items()
                    }
                    stored.update(
                        {
                            "combined_quality": combined,
                            "adjacent_core_quality": [before, after],
                            "distance_factor": distance_factor,
                            "fan_inner": fan_inner,
                            "fan_apex": fan_apex,
                        }
                    )
                    best = (rank, stored)
    front.front[index] = original
    return best[1]


def _add_fan(
    graph,
    cuts,
    front,
    site,
    cell_index,
    gate,
    geometry,
    layer_faces,
    wall_edge_style,
) -> None:
    """Replace the two band blocks around one sharp feature with five."""
    places = geometry["places"]
    corner_cut = cuts[gate]
    keys = {
        "corner": _gate_key(cell_index, corner_cut),
        "before": _gate_key(cell_index, cuts[geometry["previous"]]),
        "after": _gate_key(cell_index, cuts[geometry["following"]]),
        "front_corner": _front_key(cell_index, corner_cut),
        "front_before": _front_key(cell_index, cuts[geometry["previous"]]),
        "front_after": _front_key(cell_index, cuts[geometry["following"]]),
        "next": ("fan", cell_index, *corner_cut.anchor.key, "next"),
        "previous": ("fan", cell_index, *corner_cut.anchor.key, "previous"),
        "apex": ("fan", cell_index, *corner_cut.anchor.key, "apex"),
    }
    for name in ("next", "previous", "apex"):
        graph.add_vertex(keys[name], places[name], provenance="wall-feature fan")
    for pair in (("before", "front_before"), ("after", "front_after")):
        _add_line(graph, keys[pair[0]], keys[pair[1]], "layer_spoke", "wall normal")
    for first, second, role in (
        ("corner", "previous", "layer_spoke"),
        ("corner", "next", "layer_spoke"),
        ("previous", "front_before", "fan"),
        ("previous", "apex", "fan"),
        ("next", "apex", "fan"),
        ("next", "front_after", "fan"),
        ("apex", "front_corner", "fan"),
    ):
        _add_line(graph, keys[first], keys[second], role, "wall-feature fan")
    for first, second in ((geometry["previous"], gate), (gate, geometry["following"])):
        path = layer_module.ring_slice(
            front.wall, front.gate_indices[first], front.gate_indices[second]
        )
        kind, points = _path_curve(path, wall_edge_style)
        graph.add_edge(
            _gate_key(cell_index, cuts[first]),
            _gate_key(cell_index, cuts[second]),
            path=path,
            kind=kind,
            points=points,
            boundary=site.name,
            role="wall",
            provenance="supplied point list",
        )
    for corners in geometry["faces"]:
        face = graph.add_face(
            tuple(keys[name] for name in corners),
            role="layer",
            provenance="wall-feature fan",
        )
        layer_faces.add(face.key)


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
