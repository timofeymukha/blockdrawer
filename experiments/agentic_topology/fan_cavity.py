"""Cavity-aware repair of sharp wall features.

A sharp convex wall feature cannot be meshed by sliding the vertices of the
topology that is already there: a 350 degree fluid sector shared by two blocks
gives two 175 degree block corners wherever the gates sit.  The feature needs
*more incident sectors*, which is a discrete change, and a discrete change to a
planar block topology is only meaningful if the replacement is valid **in the
plane**, not merely locally well shaped.

This module therefore does three things that a local fan constructor cannot.

1. It names the *cavity* a feature owns - the faces incident to the feature,
   grown across interior edges until the wall bands of other chains would be
   absorbed - and extracts its ordered boundary from patch-graph incidence.
   Everything the replacement is allowed to touch is inside that cavity;
   everything else is fixed context it must not intersect.
2. It builds the replacement on a **copy** of the graph and validates it
   completely - coverage, convexity, planarity against the cavity boundary,
   against the unaffected graph, against the original body and outer boundary
   geometry, Euler and index balance, and the cell-count equality components -
   before anything is committed.  A rejected candidate leaves the graph with
   the same topology signature it had.
3. It generates a deterministic set of *alternatives* rather than one fan:
   different templates, different sector counts and different continuous
   placements, all ranked by a single objective, with every rejection recorded
   so an agent can see which failure needs a different placement and which
   needs a different topology.

The templates are written against the ordered cavity boundary and the position
of the feature in it, so nothing here knows about airfoils, element names,
coordinates, body counts or a streamwise direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from blockdrawer import preview as bd_preview

import geometry2d as g2
import patch_graph as pg

# Edge roles that run *along* a boundary or a front, and roles that run across
# the near-wall region.  A cell-count equality component that contains both is
# a component that forces the boundary-layer thickness resolution to equal a
# streamwise resolution, which is why they are named here rather than left
# implicit.
# Two points closer than this fraction of the cavity diagonal are the same
# point; every geometric decision here is taken against a length derived
# from it.
TOUCH_RATIO = 1.0e-9

TANGENTIAL_ROLES = frozenset({"wall", "front", "ring"})
NORMAL_ROLES = frozenset({"layer_spoke", "core_spoke"})


@dataclass(frozen=True)
class CavityOptions:
    """Everything the cavity stage may decide, in one place."""

    enabled: bool = True
    # A wall vertex is a feature when its fluid sector is at least this wide -
    # a smooth wall reads about 180 degrees - and the blocks sharing it are
    # that obtuse on average.
    feature_sector_degrees: float = 200.0
    feature_corner_degrees: float = 135.0
    # Independently of any sector, open a cavity wherever it would contain a
    # block that is below the hard quality floor: a folded or slivered block is
    # not a mesh block, and the cavity is the smallest thing that can replace
    # it without touching anything else.
    repair_poor_faces: bool = True
    maximum_sectors: int = 4
    growth_depth: int = 2
    # Hard geometric tolerances, both as fractions of the cavity diagonal.
    touch_ratio: float = 1.0e-9
    minimum_edge_ratio: float = 1.0e-3
    # Hard floor on the worst affected face; below this the candidate is not
    # a mesh block whatever its other merits.
    minimum_quality: float = 0.02
    # Soft objective weights.  Quality is in [-1, 1] and dominates.
    clearance_weight: float = 0.30
    clearance_reference: float = 0.05
    regularity_weight: float = 0.10
    sampled_weight: float = 0.35
    complexity_weight: float = 0.01
    # A candidate must beat the construction already in the graph by this much
    # before it is applied, so an equal-scoring alternative never churns.
    improvement_margin: float = 1.0e-6
    # Reject a candidate that would merge a tangential and a normal cell-count
    # component inside a boundary-layer band.  See ``_count_coupling``.
    allow_count_coupling: bool = False
    # Deterministic parameter grids.
    reach_samples: int = 9
    lean_samples: int = 9
    spread_samples: int = 9
    inner_samples: int = 7
    apex_samples: int = 7
    cut_samples: int = 9
    # Pairs of (feature label, template name).  When a feature is named here
    # only that template is enumerated for it, so an agent selects among the
    # validated alternatives the diagnostics already reported instead of
    # placing vertices; an unnamed feature is decided by the ranking.
    choices: tuple = ()
    # How many locally best placements are validated in full, and how many
    # validated candidates are reported.
    validation_budget: int = 96
    # How many valid placements of one template are scored before the best is
    # taken; the objective, not the screening order, decides the winner.
    measured_placements: int = 16
    reported_candidates: int = 8
    sampled_cells: int = 6


# ---------------------------------------------------------------------------
# Cavities
# ---------------------------------------------------------------------------


@dataclass
class Cavity:
    """The part of a patch graph one wall feature is allowed to rewrite."""

    vertex: pg.VertexKey
    depth: int
    faces: tuple[tuple, ...]
    boundary: tuple[pg.VertexKey, ...]
    interior: tuple[pg.VertexKey, ...]
    polygon: np.ndarray
    fluid_angle: float
    incident_faces: int
    has_layer: bool

    @property
    def size(self) -> int:
        return len(self.boundary)

    @property
    def scale(self) -> float:
        span = np.max(self.polygon, axis=0) - np.min(self.polygon, axis=0)
        value = float(math.hypot(float(span[0]), float(span[1])))
        return value if value > 0.0 else 1.0

    @property
    def area(self) -> float:
        return abs(g2.polygon_area(self.polygon[:-1]))

    def described(self) -> dict:
        return {
            "feature": list(self.vertex),
            "point": [float(self.polygon[0][0]), float(self.polygon[0][1])],
            "depth": self.depth,
            "scale": self.scale,
            "fluid_angle_degrees": self.fluid_angle,
            "incident_faces": self.incident_faces,
            "boundary_corners": [list(key) for key in self.boundary],
            "boundary_size": self.size,
            "interior_vertices": [list(key) for key in self.interior],
            "faces": [list(key) for key in self.faces],
            "contains_boundary_layer": self.has_layer,
        }


def boundary_sector(graph: pg.PatchGraph, vertex: pg.VertexKey) -> float | None:
    """Fluid-side angle at a vertex of the graph's outer boundary, in radians.

    Measured from the two boundary edges themselves rather than from the block
    corners, so it is a property of the domain, not of the current topology:
    a smooth wall reads about 180 degrees however many blocks meet there, and a
    cusp reads its real sector.
    """
    edges = [
        key
        for key, edge in graph.edges.items()
        if vertex in key and edge.boundary is not None
    ]
    if len(edges) != 2:
        return None
    partners = []
    for key in edges:
        other = key[1] if key[0] == vertex else key[0]
        path = graph.edges[key].directed_path(vertex)
        step = path[1] - path[0]
        if float(np.linalg.norm(step)) <= 0.0:
            step = graph.vertices[other].point - graph.vertices[vertex].point
        partners.append(step / max(float(np.linalg.norm(step)), 1.0e-30))
    first = math.atan2(float(partners[0][1]), float(partners[0][0]))
    second = math.atan2(float(partners[1][1]), float(partners[1][0]))
    forward = (second - first) % (2.0 * math.pi)
    # Which of the two sectors is the fluid one cannot be read from a mean
    # direction: at a cusp both incident blocks lie almost along the solid
    # wedge.  The blocks themselves settle it - the interior angles they
    # contribute at this vertex tile the fluid sector - so take the branch of
    # the tangent measurement that agrees with their sum.  The block corners
    # are chords of the boundary edges, which is why their sum is used to
    # choose a branch rather than reported as the sector itself.
    spanned = 0.0
    for face in graph.faces:
        if vertex not in face.corners:
            continue
        points = graph.corner_points(face)
        angles = g2.quad_corner_angles(points)
        spanned += float(angles[list(face.corners).index(vertex)])
    if spanned <= 0.0:
        return None
    backward = 2.0 * math.pi - forward
    if abs(forward - spanned) <= abs(backward - spanned):
        return forward
    return backward


def poor_faces(graph: pg.PatchGraph, options: CavityOptions) -> dict:
    """Faces below the hard quality floor, with their index."""
    found = {}
    for index, face in enumerate(graph.faces):
        points = graph.corner_points(face)
        value = face_quality(
            points if g2.polygon_area(points) >= 0.0 else points[::-1]
        )
        if value < options.minimum_quality:
            found[tuple(face.key)] = (index, value)
    return found


def feature_corners(graph: pg.PatchGraph, options: CavityOptions) -> list[dict]:
    """Wall vertices whose cavity is worth rewriting.

    Two independent reasons, both measured from the graph rather than from the
    input.  Either the vertex carries a genuinely wide convex fluid sector and
    the blocks sharing it are that obtuse on average - the sharp-feature case -
    or the cavity around it holds a block that is below the hard quality floor,
    which is a block that is folded or slivered and therefore not a mesh block
    at all, wherever it came from.
    """
    boundary = graph.boundary_vertices()
    counts: dict[pg.VertexKey, int] = {}
    incident: dict[pg.VertexKey, float] = {}
    for face in graph.faces:
        points = graph.corner_points(face)
        value = face_quality(
            points if g2.polygon_area(points) >= 0.0 else points[::-1]
        )
        for corner in face.corners:
            if corner not in boundary:
                continue
            counts[corner] = counts.get(corner, 0) + 1
            incident[corner] = min(incident.get(corner, math.inf), value)
    nearby = _poor_neighbourhood(graph, options, boundary)
    found = []
    for key in sorted(boundary, key=str):
        count = counts.get(key, 0)
        if count < 1:
            continue
        sector = boundary_sector(graph, key)
        degrees = 0.0 if sector is None else math.degrees(sector)
        sharp = (
            sector is not None
            and degrees >= options.feature_sector_degrees
            and degrees / count >= options.feature_corner_degrees
        )
        worst = float(min(incident.get(key, math.inf), nearby.get(key, math.inf)))
        broken = options.repair_poor_faces and worst < options.minimum_quality
        if not sharp and not broken:
            continue
        found.append(
            {
                "vertex": key,
                "fluid_angle": degrees,
                "incident_faces": count,
                "mean_corner": degrees / count,
                "worst_incident_face_quality": (
                    None if worst is math.inf else worst
                ),
                "has_folded_face": worst <= 0.0,
                "reason": "wide sector" if sharp else "invalid block in the cavity",
            }
        )
    # Worst first, so the most broken region is rewritten before anything that
    # merely wants a better corner distribution.
    found.sort(
        key=lambda item: (
            item["worst_incident_face_quality"]
            if item["worst_incident_face_quality"] is not None
            else math.inf,
            -item["mean_corner"],
            str(item["vertex"]),
        )
    )
    return found


def _poor_neighbourhood(graph, options: CavityOptions, boundary) -> dict:
    """Boundary vertices whose depth-2 cavity would contain an invalid block.

    A block one ring away from the wall - a core patch between a layer front
    and the medial scaffold - is inside the cavity of the wall vertices its
    corners are attached to, so those vertices own the repair even though the
    bad block does not touch them.
    """
    if not options.repair_poor_faces:
        return {}
    result: dict = {}
    adjacency: dict = {}
    for key in graph.edges:
        adjacency.setdefault(key[0], set()).add(key[1])
        adjacency.setdefault(key[1], set()).add(key[0])
    for _key, (index, value) in poor_faces(graph, options).items():
        touched: set = set()
        for corner in graph.faces[index].corners:
            if corner in boundary:
                touched.add(corner)
                continue
            touched.update(adjacency.get(corner, set()) & boundary)
        for corner in touched:
            result[corner] = min(result.get(corner, math.inf), value)
    return result


def build_cavity(
    graph: pg.PatchGraph, vertex: pg.VertexKey, *, depth: int
) -> tuple[Cavity | None, str]:
    """Grow the cavity a feature owns and extract its ordered boundary."""
    incidence = graph.edge_faces()
    selected = {
        index for index, face in enumerate(graph.faces) if vertex in face.corners
    }
    if not selected:
        return None, "the feature is not a corner of any face"
    for _step in range(max(0, depth - 1)):
        grown: set[int] = set()
        for index in sorted(selected):
            for key in graph.face_edges(graph.faces[index]):
                if graph.edges[key].boundary is not None:
                    continue
                for other in incidence.get(key, ()):
                    if other in selected:
                        continue
                    neighbour = graph.faces[other]
                    if any(
                        graph.edges[item].boundary is not None
                        for item in graph.face_edges(neighbour)
                    ):
                        # Absorbing a face that carries a named patch would let
                        # one feature consume another chain's wall interval.
                        continue
                    grown.add(other)
        if not grown:
            break
        selected |= grown
    faces = [graph.faces[index] for index in sorted(selected)]
    cycle, reason = _boundary_cycle(graph, faces)
    if cycle is None:
        return None, reason
    if vertex not in cycle:
        return None, "the feature is interior to its own cavity"
    position = cycle.index(vertex)
    cycle = tuple(cycle[position:] + cycle[:position])
    inner = {corner for face in faces for corner in face.corners} - set(cycle)
    polygon = _cavity_polygon(graph, cycle)
    if polygon is None:
        return None, "the cavity boundary has a missing edge"
    boundary_vertices = graph.boundary_vertices()
    first = graph.edge_key(cycle[0], cycle[1])
    last = graph.edge_key(cycle[-1], cycle[0])
    if (
        graph.edges[first].boundary is None
        or graph.edges[last].boundary is None
    ):
        return None, "the feature does not sit between two named boundary edges"
    if any(key in graph.periodic_vertices for key in inner):
        return None, "the cavity interior carries a periodic correspondence"
    sector = boundary_sector(graph, vertex)
    totals = 0.0 if sector is None else math.degrees(sector)
    cavity = Cavity(
        vertex,
        depth,
        tuple(face.key for face in faces),
        cycle,
        tuple(sorted(inner, key=str)),
        polygon,
        totals,
        sum(1 for face in faces if vertex in face.corners),
        any(face.role == "layer" for face in faces),
    )
    if cavity.size % 2:
        return None, (
            f"the cavity boundary has {cavity.size} corners; an odd cycle "
            "cannot be filled with quadrilaterals"
        )
    tangles = g2.self_conflicts(
        polygon, closed=True, tolerance=TOUCH_RATIO * cavity.scale
    )
    if tangles:
        return None, (
            "the cavity boundary is not simple: "
            f"{tangles[0]['kind']} between its own segments, so no replacement "
            "inside it can be planar. A larger cavity would be needed to "
            "contain the tangle."
        )
    if len(boundary_vertices & set(inner)):
        return None, "the cavity interior touches the domain boundary"
    return cavity, ""


def _boundary_cycle(graph: pg.PatchGraph, faces) -> tuple[list | None, str]:
    """Ordered corner cycle of a set of faces, or the reason there is none."""
    counted: dict[tuple, int] = {}
    for face in faces:
        for key in graph.face_edges(face):
            counted[key] = counted.get(key, 0) + 1
    rim = [key for key, count in counted.items() if count == 1]
    if len(rim) < 4:
        return None, "the cavity has no usable boundary"
    links: dict[pg.VertexKey, list[pg.VertexKey]] = {}
    for first, second in rim:
        links.setdefault(first, []).append(second)
        links.setdefault(second, []).append(first)
    if any(len(value) != 2 for value in links.values()):
        return None, "the cavity boundary is not a single simple cycle"
    start = min(links, key=str)
    cycle = [start]
    previous = None
    current = start
    while True:
        options = [item for item in links[current] if item != previous]
        if not options:
            return None, "the cavity boundary walk dead-ends"
        following = options[0]
        if following == start:
            break
        cycle.append(following)
        previous, current = current, following
        if len(cycle) > len(rim):
            return None, "the cavity boundary does not close"
    if len(cycle) != len(rim):
        return None, "the cavity boundary has more than one component"
    points = np.asarray([graph.vertices[key].point for key in cycle])
    if g2.polygon_area(points) < 0.0:
        cycle.reverse()
    return cycle, ""


def _cavity_polygon(graph: pg.PatchGraph, cycle) -> np.ndarray | None:
    pieces = []
    for index in range(len(cycle)):
        first = cycle[index]
        second = cycle[(index + 1) % len(cycle)]
        key = graph.edge_key(first, second)
        edge = graph.edges.get(key)
        if edge is None:
            return None
        pieces.append(edge.directed_path(first)[:-1])
    return np.vstack([*pieces, pieces[0][:1]])


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
#
# A template is a pure combinatorial description written against the cavity
# boundary: ``("rim", k)`` is the boundary corner k steps anticlockwise from
# the feature, ``("new", name)`` is a vertex the template introduces.  Roles on
# the new interior edges record what the edge is *for*, which is what the
# cell-count coupling test reads; nothing downstream branches on them.


@dataclass(frozen=True)
class Template:
    name: str
    sectors: int
    size: int
    new_vertices: tuple[str, ...]
    faces: tuple[tuple, ...]
    roles: dict


def _rim(index: int) -> tuple:
    return ("rim", index)


def _new(name: str) -> tuple:
    return ("new", name)


def band_template(size: int) -> Template | None:
    """The ordinary two-block band, with the core patches when they are in."""
    if size == 6:
        return Template(
            "band",
            2,
            6,
            (),
            (
                (_rim(0), _rim(1), _rim(2), _rim(3)),
                (_rim(5), _rim(0), _rim(3), _rim(4)),
            ),
            {(_rim(0), _rim(3)): "layer_spoke"},
        )
    if size != 8:
        return None
    mid = _new("mid")
    return Template(
        "band",
        2,
        8,
        ("mid",),
        (
            (_rim(7), _rim(0), mid, _rim(6)),
            (_rim(0), _rim(1), _rim(2), mid),
            (mid, _rim(2), _rim(3), _rim(4)),
            (_rim(6), mid, _rim(4), _rim(5)),
        ),
        {
            (_rim(0), mid): "layer_spoke",
            (mid, _rim(2)): "front",
            (_rim(6), mid): "front",
            (mid, _rim(4)): "core_spoke",
        },
    )


def seam_template(size: int) -> Template | None:
    """One front vertex per incident wall side plus a wedge to the core.

    The feature gains two incident *normal* edges, both anchored on the core
    boundary, so the boundary layer's thickness resolution never joins a
    streamwise cell-count component.  This needs the core patches inside the
    cavity, so it only exists on an eight-corner cavity.
    """
    if size != 8:
        return None
    incoming = _new("seam_in")
    outgoing = _new("seam_out")
    return Template(
        "seam",
        3,
        8,
        ("seam_in", "seam_out"),
        (
            (_rim(7), _rim(0), incoming, _rim(6)),
            (_rim(0), outgoing, _rim(4), incoming),
            (_rim(0), _rim(1), _rim(2), outgoing),
            (outgoing, _rim(2), _rim(3), _rim(4)),
            (_rim(6), incoming, _rim(4), _rim(5)),
        ),
        {
            (_rim(0), incoming): "layer_spoke",
            (_rim(0), outgoing): "layer_spoke",
            (incoming, _rim(6)): "front",
            (outgoing, _rim(2)): "front",
            (incoming, _rim(4)): "core_spoke",
            (outgoing, _rim(4)): "core_spoke",
        },
    )


def fan_template(sectors: int, size: int) -> Template | None:
    """``sectors`` blocks meeting at the feature, contained in the band.

    The residual region between the sector fan and the front is closed by a
    second fan centred on the front vertex opposite the feature, so the
    construction stays inside the band and never reaches the core.
    """
    if sectors < 2 or size not in (6, 8):
        return None
    if size == 6:
        mid = _rim(3)
        after, before, wall_before = _rim(2), _rim(4), _rim(5)
        new_names: list[str] = []
        roles: dict = {}
        core: tuple = ()
    else:
        mid = _new("mid")
        after, before, wall_before = _rim(2), _rim(6), _rim(7)
        new_names = ["mid"]
        roles = {
            (mid, _rim(2)): "front",
            (_rim(6), mid): "front",
            (mid, _rim(4)): "core_spoke",
        }
        core = (
            (mid, _rim(2), _rim(3), _rim(4)),
            (_rim(6), mid, _rim(4), _rim(5)),
        )
    rays = [_new(f"ray{index}") for index in range(2, sectors + 1)]
    apexes = [_new(f"apex{index}") for index in range(2, sectors)]
    faces = [(_rim(0), _rim(1), after, rays[0])]
    for index in range(sectors - 2):
        faces.append((_rim(0), rays[index], apexes[index], rays[index + 1]))
    faces.append((_rim(0), rays[-1], before, wall_before))
    outer = [after, *apexes, before]
    for index in range(sectors - 1):
        faces.append((mid, outer[index], rays[index], outer[index + 1]))
    faces.extend(core)
    for ray in rays:
        roles[(_rim(0), ray)] = "layer_spoke"
    new_names.extend(name for _kind, name in rays + apexes)
    return Template(
        f"fan{sectors}", sectors, size, tuple(new_names), tuple(faces), roles
    )


def through_fan_template(size: int) -> Template | None:
    """Three sectors at the feature with the middle one cutting the core.

    This is the short C-grid-style cut: the front vertex opposite the feature
    disappears and the apex of the middle sector is anchored between the two
    neighbouring medial vertices, so the fan reaches through the layer instead
    of stopping at it.  It needs the core patches, so it is an eight-corner
    construction only; other sector counts would need a transition strip
    between the sector chain and the core boundary, which this stage does not
    build.
    """
    if size != 8:
        return None
    ray_after = _new("ray2")
    ray_before = _new("ray3")
    apex = _new("apex2")
    return Template(
        "through_fan3",
        3,
        8,
        ("ray2", "ray3", "apex2"),
        (
            (_rim(0), _rim(1), _rim(2), ray_after),
            (_rim(0), ray_after, apex, ray_before),
            (_rim(0), ray_before, _rim(6), _rim(7)),
            (apex, ray_after, _rim(2), _rim(3)),
            (apex, _rim(3), _rim(4), _rim(5)),
            (apex, _rim(5), _rim(6), ray_before),
        ),
        {
            (_rim(0), ray_after): "layer_spoke",
            (_rim(0), ray_before): "layer_spoke",
        },
    )


def templates(options: CavityOptions, size: int) -> list[Template]:
    """Every alternative that fits a cavity of this many boundary corners."""
    found = [
        band_template(size),
        seam_template(size),
        through_fan_template(size),
    ]
    for sectors in range(2, max(2, options.maximum_sectors) + 1):
        found.append(fan_template(sectors, size))
    return [item for item in found if item is not None]


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """The intrinsic frame a placement is expressed in.

    ``ray(fraction, radius)`` walks the fluid sector from the cavity boundary
    edge leaving the feature towards ``boundary[1]`` round to the one leaving
    it towards ``boundary[-1]``, in the cavity's own anticlockwise sense, so
    fraction 0 and 1 are the two wall sides and everything between is inside.
    ``radius`` is a fraction of the distance to the cavity boundary *along that
    ray*, not of a fixed length: a cavity that is a long sliver in one
    direction and tight in another is then sampled sensibly in both, and the
    same parameters mean the same thing at any position, orientation or scale.
    """

    corner: np.ndarray
    start: float
    sweep: float
    height: float
    polygon: np.ndarray

    def direction(self, fraction: float) -> np.ndarray:
        angle = self.start + fraction * self.sweep
        return np.asarray([math.cos(angle), math.sin(angle)])

    def reach(self, fraction: float) -> float:
        """Distance from the feature to the cavity boundary along one ray."""
        step = self.direction(fraction)
        first = self.polygon[:-1]
        second = self.polygon[1:]
        edge = second - first
        denominator = step[0] * edge[:, 1] - step[1] * edge[:, 0]
        safe = np.where(np.abs(denominator) > 0.0, denominator, 1.0)
        delta = first - self.corner
        distance = (delta[:, 0] * edge[:, 1] - delta[:, 1] * edge[:, 0]) / safe
        along = (delta[:, 0] * step[1] - delta[:, 1] * step[0]) / safe
        floor = 1.0e-9 * self.height
        usable = (
            (np.abs(denominator) > 0.0)
            & (distance > floor)
            & (along >= 0.0)
            & (along <= 1.0)
        )
        if not bool(np.any(usable)):
            return self.height
        return float(np.min(distance[usable]))

    def ray(self, fraction: float, radius: float) -> np.ndarray:
        return self.corner + radius * self.reach(fraction) * self.direction(
            fraction
        )

    def place(self, point) -> tuple[float, float] | None:
        """The (fraction, radius) a point corresponds to, when it is inside."""
        step = np.asarray(point, dtype=np.float64) - self.corner
        length = float(np.linalg.norm(step))
        if length <= 0.0 or self.sweep <= 0.0:
            return None
        angle = math.atan2(float(step[1]), float(step[0]))
        fraction = ((angle - self.start) % (2.0 * math.pi)) / self.sweep
        if not 0.0 <= fraction <= 1.0:
            return None
        available = self.reach(fraction)
        if available <= 0.0:
            return None
        return fraction, length / available


def build_frame(graph: pg.PatchGraph, cavity: Cavity) -> Frame:
    """The intrinsic frame of a cavity: sector directions and available room.

    The reference length is the distance from the feature to the part of the
    cavity boundary it does *not* sit on, which is exactly how far a placement
    may travel before it leaves the cavity.  Using the distance to a
    neighbouring corner instead would scale with how far apart the gates are,
    which has nothing to do with the room in front of the feature.
    """
    corner = graph.vertices[cavity.vertex].point
    after = _leaving_direction(graph, cavity.vertex, cavity.boundary[1])
    before = _leaving_direction(graph, cavity.vertex, cavity.boundary[-1])
    start = math.atan2(float(after[1]), float(after[0]))
    finish = math.atan2(float(before[1]), float(before[0]))
    # The cavity cycle is anticlockwise, so its interior is to the left of the
    # boundary direction and the interior angle at the feature is swept
    # anticlockwise from the outgoing edge to the incoming one.
    sweep = (finish - start) % (2.0 * math.pi)
    far = _far_boundary(graph, cavity)
    height = float(g2.distance_to_polyline(far, corner[None, :])[0])
    return Frame(
        corner,
        start,
        sweep,
        max(height, 1.0e-9 * cavity.scale),
        np.asarray(cavity.polygon, dtype=np.float64),
    )


def _far_boundary(graph: pg.PatchGraph, cavity: Cavity) -> np.ndarray:
    """The cavity boundary without the two edges the feature lies on."""
    pieces = []
    for index in range(1, cavity.size - 1):
        first = cavity.boundary[index]
        second = cavity.boundary[index + 1]
        edge = graph.edges[graph.edge_key(first, second)]
        pieces.append(edge.directed_path(first)[:-1])
    last = cavity.boundary[cavity.size - 1]
    pieces.append(graph.vertices[last].point[None, :])
    return np.vstack(pieces)


def _leaving_direction(
    graph: pg.PatchGraph, first: pg.VertexKey, second: pg.VertexKey
) -> np.ndarray:
    """Unit tangent of an edge where it leaves ``first``."""
    edge = graph.edges[graph.edge_key(first, second)]
    path = edge.directed_path(first)
    step = path[1] - path[0]
    length = float(np.linalg.norm(step))
    if length <= 0.0:
        step = path[-1] - path[0]
        length = max(float(np.linalg.norm(step)), 1.0e-30)
    return step / length


def _samples(count: int, low: float, high: float, extra=()) -> list[float]:
    values = {float(item) for item in extra}
    values.update(float(item) for item in np.linspace(low, high, max(2, count)))
    return sorted(values)


def placements(
    template: Template, frame: Frame, cavity: Cavity, graph: pg.PatchGraph, options
):
    """Every deterministic continuous placement of one template.

    Radii are fractions of the room along their own ray, so the grids below are
    dimensionless and the same for every cavity.
    """
    if template.name == "band":
        if not template.new_vertices:
            yield ({}, {})
            return
        radii = _samples(options.reach_samples, 0.15, 0.9)
        leans = _samples(options.lean_samples, 0.15, 0.85)
        for lean in leans:
            for radius in radii:
                yield (
                    {"lean": lean, "radius": radius},
                    {"mid": frame.ray(lean, radius)},
                )
        for lean, radius in _original_places(graph, cavity, frame):
            yield (
                {"lean": lean, "radius": radius},
                {"mid": frame.ray(lean, radius)},
            )
        return
    if template.name == "seam":
        radii = _samples(options.reach_samples, 0.15, 0.9)
        spreads = _samples(options.spread_samples, 0.04, 0.46)
        for spread in spreads:
            for radius in radii:
                yield (
                    {"spread": spread, "radius": radius},
                    {
                        "seam_out": frame.ray(spread, radius),
                        "seam_in": frame.ray(1.0 - spread, radius),
                    },
                )
        return
    if template.name == "through_fan3":
        inners = _samples(options.inner_samples, 0.1, 0.8)
        cuts = _samples(options.cut_samples, 0.2, 0.9)
        anchor = graph.vertices[cavity.boundary[4]].point
        for inner in inners:
            for cut in cuts:
                yield (
                    {"inner": inner, "cut": cut},
                    {
                        "ray2": frame.ray(1.0 / 3.0, inner),
                        "ray3": frame.ray(2.0 / 3.0, inner),
                        "apex2": frame.corner + cut * (anchor - frame.corner),
                    },
                )
        return
    sectors = template.sectors
    wants_mid = "mid" in template.new_vertices
    radii = _samples(options.reach_samples, 0.2, 0.9) if wants_mid else [0.0]
    inners = _samples(options.inner_samples, 0.08, 0.6)
    apexes = _samples(options.apex_samples, 0.15, 0.85)
    for radius in radii:
        for inner in inners:
            for apex in apexes:
                if wants_mid and apex >= radius:
                    continue
                points = {}
                if wants_mid:
                    points["mid"] = frame.ray(0.5, radius)
                for index in range(2, sectors + 1):
                    points[f"ray{index}"] = frame.ray(
                        (index - 1) / sectors, inner
                    )
                for index in range(2, sectors):
                    points[f"apex{index}"] = frame.ray(
                        (index - 0.5) / sectors, apex
                    )
                parameters = {"inner": inner, "apex": apex}
                if wants_mid:
                    parameters["radius"] = radius
                yield (parameters, points)


def _original_places(graph: pg.PatchGraph, cavity: Cavity, frame: Frame):
    """The parameters of the vertices already in the cavity.

    Including them guarantees the existing placement is always among the
    samples, so a template can reproduce what is there rather than only
    approximate it.
    """
    found = []
    for key in cavity.interior:
        place = frame.place(graph.vertices[key].point)
        if place is not None:
            found.append(place)
    return found


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One fully resolved replacement for a cavity, valid or not."""

    identifier: str
    template: str
    sectors: int
    depth: int
    parameters: dict
    new_vertices: dict = field(default_factory=dict)
    faces: tuple = ()
    edge_roles: dict = field(default_factory=dict)
    edge_paths: dict = field(default_factory=dict)
    quality: float = -1.0
    sampled_quality: float | None = None
    clearance: float = 0.0
    regularity: float = 0.0
    score: float = -math.inf
    worst_face: int | None = None
    rejections: list = field(default_factory=list)
    index_before: int | None = None
    index_after: int | None = None
    face_count: int = 0

    @property
    def valid(self) -> bool:
        return not self.rejections

    def described(self) -> dict:
        return {
            "id": self.identifier,
            "template": self.template,
            "sectors": self.sectors,
            "cavity_depth": self.depth,
            "parameters": {
                name: float(value) for name, value in self.parameters.items()
            },
            "faces": self.face_count,
            "accepted": self.valid,
            "score": None if self.score == -math.inf else float(self.score),
            "score_components": {
                "minimum_affected_face_quality": float(self.quality),
                "worst_affected_face": self.worst_face,
                "sampled_minimum_scaled_jacobian": (
                    None
                    if self.sampled_quality is None
                    else float(self.sampled_quality)
                ),
                "clearance_over_cavity_scale": float(self.clearance),
                "edge_and_angle_regularity": float(self.regularity),
            },
            "index_sum_before": self.index_before,
            "index_sum_after": self.index_after,
            "rejections": list(self.rejections),
            "new_vertices": [
                {"vertex": list(key), "point": [float(p[0]), float(p[1])]}
                for key, p in sorted(self.new_vertices.items(), key=lambda i: str(i[0]))
            ],
        }


def _label(key) -> str:
    return "_".join(str(part) for part in key)


def _vertex_key(cavity: Cavity, name: str) -> tuple:
    return ("cavity", name, *cavity.vertex)


def resolve(template: Template, cavity: Cavity, points: dict):
    """Turn a template's symbolic faces into graph keys and a role table."""
    mapping: dict = {}
    for index in range(cavity.size):
        mapping[("rim", index)] = cavity.boundary[index]
    new_vertices = {}
    for name in template.new_vertices:
        key = _vertex_key(cavity, name)
        mapping[("new", name)] = key
        new_vertices[key] = np.asarray(points[name], dtype=np.float64)
    faces = tuple(
        tuple(mapping[item] for item in corners) for corners in template.faces
    )
    roles = {}
    for (first, second), role in template.roles.items():
        roles[(mapping[first], mapping[second])] = role
    return faces, new_vertices, roles


def face_quality(points: np.ndarray) -> float:
    """Worst signed, side-normalised corner cross product of a quadrilateral."""
    crosses = g2.quad_corner_crosses(points)
    sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    pairs = sides * np.roll(sides, -1)
    sign = 1.0 if g2.polygon_area(points) >= 0.0 else -1.0
    safe = np.where(pairs > 0.0, pairs, 1.0)
    return float(np.min(sign * crosses / safe))


def sampled_face_quality(paths, corners, cells: int) -> float:
    """Minimum scaled Jacobian of the real transfinite grid inside one face.

    ``blockdrawer.preview`` owns the interpolation BlockDrawer previews and
    ``blockMesh`` writes, so it is used here directly rather than re-derived:
    a candidate that looks convex at its four corners but folds a curved wall
    cell has to be visible at this stage, not after a session is emitted.
    """
    import grid_quality

    count = max(2, int(cells))
    fractions = np.linspace(0.0, 1.0, count + 1)
    samples = []
    for path in paths:
        places = g2.sample_at_arclength(path, fractions * g2.total_length(path))
        # ``_block_mesh_point`` takes (node fraction, point) pairs, exactly as
        # ``preview._directed_edge_samples`` produces them.
        samples.append(
            [
                (float(fraction), (float(point[0]), float(point[1])))
                for fraction, point in zip(fractions, places)
            ]
        )
    bottom, right, top, left = samples
    grid = np.empty((count + 1, count + 1, 2), dtype=np.float64)
    for row in range(count + 1):
        for column in range(count + 1):
            grid[row, column] = bd_preview._block_mesh_point(
                bottom[column], right[row], top[column], left[row], corners
            )
    return float(np.min(grid_quality.cell_metrics(grid)["scaled_jacobian"]))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Everything outside a cavity that a replacement must not disturb."""

    graph: pg.PatchGraph
    cavity: Cavity
    rim_edges: dict
    outside_edges: dict
    domain_loops: tuple
    tolerance: float
    minimum_edge: float


def build_context(graph: pg.PatchGraph, cavity: Cavity, domain, options) -> Context:
    rim = {}
    for index in range(cavity.size):
        first = cavity.boundary[index]
        second = cavity.boundary[(index + 1) % cavity.size]
        key = graph.edge_key(first, second)
        rim[key] = graph.edges[key]
    inside = set(cavity.faces)
    interior_keys = set()
    for face in graph.faces:
        if tuple(face.key) not in inside:
            continue
        for key in graph.face_edges(face):
            if key not in rim:
                interior_keys.add(key)
    lower = np.min(cavity.polygon, axis=0)
    upper = np.max(cavity.polygon, axis=0)
    margin = 0.5 * cavity.scale
    outside = {}
    for key, edge in graph.edges.items():
        if key in rim or key in interior_keys:
            continue
        path = edge.path
        if (
            float(np.max(path[:, 0])) < lower[0] - margin
            or float(np.min(path[:, 0])) > upper[0] + margin
            or float(np.max(path[:, 1])) < lower[1] - margin
            or float(np.min(path[:, 1])) > upper[1] + margin
        ):
            continue
        outside[key] = edge
    loops = ()
    if domain is not None:
        loops = tuple(loop.points() for loop in domain.loops)
    return Context(
        graph,
        cavity,
        rim,
        outside,
        loops,
        options.touch_ratio * cavity.scale,
        options.minimum_edge_ratio * cavity.scale,
    )


def _interior_edges(faces) -> dict:
    """Edge keys the replacement creates, with how many faces use each."""
    counted: dict[tuple, int] = {}
    for corners in faces:
        for index in range(4):
            key = pg.PatchGraph.edge_key(corners[index], corners[(index + 1) % 4])
            counted[key] = counted.get(key, 0) + 1
    return counted


def validate(
    candidate: Candidate,
    context: Context,
    options: CavityOptions,
    *,
    existing_ok: bool = False,
):
    """Every hard constraint, recorded rather than raised.

    ``existing_ok`` scores the construction that is already in the cavity,
    whose edges legitimately exist in the graph already.
    """
    cavity = context.cavity
    graph = context.graph
    rejections: list[dict] = []
    points = {**{key: vertex for key, vertex in candidate.new_vertices.items()}}

    # 1. Every new vertex is strictly inside the cavity.
    if points:
        array = np.asarray(list(points.values()))
        inside = g2.points_in_loop(cavity.polygon, array)
        gaps = g2.distance_to_polyline(cavity.polygon, array)
        for (key, point), is_in, gap in zip(points.items(), inside, gaps):
            if not bool(is_in) or float(gap) <= context.tolerance:
                rejections.append(
                    {
                        "reason": "vertex_outside_cavity",
                        "entities": [list(key)],
                        "point": [float(point[0]), float(point[1])],
                        "detail": (
                            "a replacement vertex must lie strictly inside the "
                            "cavity it rewrites"
                        ),
                    }
                )

    # 2. Face incidence: each rim edge used once, each interior edge twice.
    counted = _interior_edges(candidate.faces)
    for key in context.rim_edges:
        if counted.get(key, 0) != 1:
            rejections.append(
                {
                    "reason": "cavity_boundary_not_matched",
                    "entities": [[list(key[0]), list(key[1])]],
                    "detail": (
                        f"the cavity boundary edge is used {counted.get(key, 0)} "
                        "times; it must be used exactly once"
                    ),
                }
            )
    new_keys = []
    for key, uses in sorted(counted.items(), key=str):
        if key in context.rim_edges:
            continue
        new_keys.append(key)
        if uses != 2:
            rejections.append(
                {
                    "reason": "interior_edge_incidence",
                    "entities": [[list(key[0]), list(key[1])]],
                    "detail": f"an interior edge is used {uses} times, not twice",
                }
            )
        if (
            not existing_ok
            and key in graph.edges
            and key not in context.rim_edges
        ):
            rejections.append(
                {
                    "reason": "duplicate_edge",
                    "entities": [[list(key[0]), list(key[1])]],
                    "detail": "the replacement recreates an edge that already exists",
                }
            )

    # 3. Geometry of the new straight edges.
    placed = {**{key: graph.vertices[key].point for key in cavity.boundary}, **points}
    new_paths = {}
    for key in new_keys:
        if key[0] not in placed or key[1] not in placed:
            rejections.append(
                {
                    "reason": "missing_endpoint",
                    "entities": [[list(key[0]), list(key[1])]],
                    "detail": "an interior edge refers to a vertex that is not placed",
                }
            )
            continue
        path = candidate.edge_paths.get(
            key, np.asarray([placed[key[0]], placed[key[1]]])
        )
        new_paths[key] = path
        if g2.total_length(path) < context.minimum_edge:
            rejections.append(
                {
                    "reason": "degenerate_edge",
                    "entities": [[list(key[0]), list(key[1])]],
                    "detail": (
                        "the edge is shorter than "
                        f"{options.minimum_edge_ratio:g} of the cavity diagonal"
                    ),
                }
            )

    # 4. Convexity, orientation and coverage.
    total = 0.0
    worst = math.inf
    worst_face = None
    for index, corners in enumerate(candidate.faces):
        if len(set(corners)) != 4:
            rejections.append(
                {
                    "reason": "degenerate_face",
                    "entities": [[list(key) for key in corners]],
                    "detail": "a face repeats a corner",
                }
            )
            continue
        array = np.asarray([placed[key] for key in corners if key in placed])
        if len(array) != 4:
            continue
        area = g2.polygon_area(array)
        outline = _face_outline(context, placed, corners, candidate.edge_paths)
        total += abs(g2.polygon_area(outline[:-1]))
        if not g2.strictly_convex(array):
            angles = [
                float(math.degrees(value)) for value in g2.quad_corner_angles(array)
            ]
            rejections.append(
                {
                    "reason": "non_convex_face",
                    "entities": [[list(key) for key in corners]],
                    "corner_angles_degrees": angles,
                    "detail": "MeshModel.validate() refuses a non-convex block",
                }
            )
        value = face_quality(array if area >= 0.0 else array[::-1])
        if value < worst:
            worst = value
            worst_face = index
    candidate.quality = -1.0 if worst is math.inf else float(worst)
    candidate.worst_face = worst_face
    if candidate.quality < options.minimum_quality:
        rejections.append(
            {
                "reason": "quality_below_minimum",
                "observed": candidate.quality,
                "limit": options.minimum_quality,
                "face": worst_face,
                "detail": (
                    "the worst affected face is below the hard floor on the "
                    "scaled corner Jacobian"
                ),
            }
        )
    if cavity.area > 0.0:
        error = abs(total - cavity.area) / cavity.area
        if error > 1.0e-6:
            rejections.append(
                {
                    "reason": "cavity_not_covered",
                    "observed": float(error),
                    "detail": (
                        "the replacement faces do not tile the cavity: their "
                        f"area differs by {error:.3e} of it"
                    ),
                }
            )

    # 5. Planarity of every new path against everything it can reach.
    checks = []
    keys = list(new_paths)
    for first in range(len(keys)):
        for second in range(first + 1, len(keys)):
            checks.append((keys[first], new_paths[keys[first]], keys[second],
                           new_paths[keys[second]], "new_edge"))
    for key, path in new_paths.items():
        for other, edge in context.rim_edges.items():
            checks.append((key, path, other, edge.path, "cavity_boundary"))
        for other, edge in context.outside_edges.items():
            checks.append((key, path, other, edge.path, "graph_edge"))
    for key, path, other, other_path, kind in checks:
        shared = [placed[item] for item in set(key) & set(other) if item in placed]
        for conflict in g2.path_conflicts(
            path, other_path, tolerance=context.tolerance, shared=shared, limit=1
        ):
            rejections.append(
                {
                    "reason": f"{conflict['kind']}_with_{kind}",
                    "relation": conflict["kind"],
                    "entities": [
                        [list(key[0]), list(key[1])],
                        [list(other[0]), list(other[1])],
                    ],
                    "point": (
                        None
                        if conflict.get("point") is None
                        else [
                            float(conflict["point"][0]),
                            float(conflict["point"][1]),
                        ]
                    ),
                    "detail": (
                        "a replacement edge and an existing entity are related "
                        f"by {conflict['kind']}"
                    ),
                }
            )
    for key, path in new_paths.items():
        # A spoke legitimately starts on the wall it is normal to, so its own
        # endpoints are allowed contacts; anything else is a real breach.
        ends = [placed[item] for item in key if item in placed]
        for index, loop in enumerate(context.domain_loops):
            for conflict in g2.path_conflicts(
                path, loop, tolerance=context.tolerance, shared=ends, limit=1
            ):
                rejections.append(
                    {
                        "reason": f"{conflict['kind']}_with_domain_boundary",
                        "relation": conflict["kind"],
                        "entities": [[list(key[0]), list(key[1])], [f"loop{index}"]],
                        "point": (
                            None
                            if conflict.get("point") is None
                            else [
                                float(conflict["point"][0]),
                                float(conflict["point"][1]),
                            ]
                        ),
                        "detail": (
                            "a replacement edge touches the supplied body or "
                            "outer boundary geometry"
                        ),
                    }
                )
    candidate.rejections = rejections
    return rejections


def measure(candidate: Candidate, context: Context, options: CavityOptions) -> None:
    """Fill in the soft score components of a candidate that passed validation."""
    graph = context.graph
    cavity = context.cavity
    placed = {
        **{key: graph.vertices[key].point for key in cavity.boundary},
        **candidate.new_vertices,
    }
    counted = _interior_edges(candidate.faces)
    new_paths = {
        key: candidate.edge_paths.get(
            key, np.asarray([placed[key[0]], placed[key[1]]])
        )
        for key in counted
        if key not in context.rim_edges
    }
    # Clearance from everything the replacement is not incident to: the
    # unaffected graph edges near the cavity, and the supplied body and outer
    # boundary geometry.  A candidate that only just avoids a crossing is worse
    # than one that stays away from it, even when both are valid.
    clearance = math.inf
    for key, path in new_paths.items():
        for other, edge in context.outside_edges.items():
            if set(key) & set(other):
                continue
            clearance = min(clearance, g2.path_separation(path, edge.path))
        interior = path[1:-1] if len(path) > 2 else np.empty((0, 2))
        for loop in context.domain_loops:
            if len(interior):
                clearance = min(
                    clearance,
                    float(np.min(g2.distance_to_polyline(loop, interior))),
                )
    candidate.clearance = (
        1.0 if clearance is math.inf else float(clearance / cavity.scale)
    )
    lengths = [g2.total_length(p) for p in new_paths.values()]
    ratio = (min(lengths) / max(lengths)) if lengths and max(lengths) > 0.0 else 1.0
    angles = []
    for corners in candidate.faces:
        array = np.asarray([placed[key] for key in corners])
        angles.extend(float(math.degrees(v)) for v in g2.quad_corner_angles(array))
    spread = min(angles) / 90.0 if angles else 0.0
    candidate.regularity = float(0.5 * ratio + 0.5 * min(spread, 1.0))
    candidate.sampled_quality = _sampled(candidate, context, placed, options)
    candidate.face_count = len(candidate.faces)
    extra = max(0, len(candidate.faces) - len(cavity.faces))
    # ``quality`` already covers every retained core patch in the cavity: a
    # depth-2 cavity contains the two core faces behind the band, so a
    # replacement that fixes the wall by ruining the core cannot score well.
    candidate.score = float(
        candidate.quality
        + options.sampled_weight * (candidate.sampled_quality or 0.0)
        + options.clearance_weight
        * min(candidate.clearance / max(options.clearance_reference, 1e-12), 1.0)
        + options.regularity_weight * candidate.regularity
        - options.complexity_weight * extra
    )


def _sampled(candidate, context, placed, options) -> float | None:
    graph = context.graph
    worst = math.inf
    for corners in candidate.faces:
        try:
            paths = _face_paths(
                graph, context, placed, corners, candidate.edge_paths
            )
        except KeyError:
            return None
        array = np.asarray([placed[key] for key in corners])
        if g2.polygon_area(array) < 0.0:
            corners = tuple(reversed(corners))
            paths = _face_paths(
                graph, context, placed, corners, candidate.edge_paths
            )
            array = array[::-1]
        worst = min(
            worst,
            sampled_face_quality(
                paths,
                tuple((float(p[0]), float(p[1])) for p in array),
                options.sampled_cells,
            ),
        )
    return None if worst is math.inf else float(worst)


def _face_outline(context, placed, corners, stored=None) -> np.ndarray:
    """Closed outline of a face, following curved cavity-boundary edges.

    The coverage test compares this against the cavity outline, so a candidate
    that tiles the straight-line quadrilaterals but leaves a sliver against a
    curved wall is not mistaken for an exact tiling.
    """
    pieces = []
    for index in range(4):
        first = corners[index]
        second = corners[(index + 1) % 4]
        key = pg.PatchGraph.edge_key(first, second)
        if stored and key in stored:
            array = stored[key]
            path = array if key[0] == first else array[::-1]
        elif key in context.rim_edges:
            path = context.rim_edges[key].directed_path(first)
        else:
            path = np.asarray([placed[first], placed[second]])
        pieces.append(np.asarray(path)[:-1])
    return np.vstack([*pieces, pieces[0][:1]])


def _face_paths(graph, context, placed, corners, stored=None):
    """Bottom, right, top and left paths of a face, in preview's convention."""

    def path(first, second):
        key = pg.PatchGraph.edge_key(first, second)
        if stored and key in stored:
            array = stored[key]
            return array if key[0] == first else array[::-1]
        edge = context.rim_edges.get(key)
        if edge is not None:
            return edge.directed_path(first)
        return np.asarray([placed[first], placed[second]])

    return (
        path(corners[0], corners[1]),
        path(corners[1], corners[2]),
        path(corners[3], corners[2]),
        path(corners[0], corners[3]),
    )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_candidate(
    graph: pg.PatchGraph, cavity: Cavity, candidate: Candidate
) -> pg.PatchGraph:
    """Return a new graph with the cavity replaced; never mutate the original."""
    clone = graph.copy()
    rim = {
        clone.edge_key(
            cavity.boundary[index], cavity.boundary[(index + 1) % cavity.size]
        )
        for index in range(cavity.size)
    }
    clone.remove_faces(cavity.faces)
    stale_edges, stale_vertices = clone.unused_entities(
        keep_edges=rim, keep_vertices=cavity.boundary
    )
    clone.discard(stale_edges, stale_vertices)
    for key, point in candidate.new_vertices.items():
        clone.add_vertex(
            key,
            point,
            constraint=pg.FREE,
            provenance=f"cavity {candidate.template}",
        )
    for corners in candidate.faces:
        for index in range(4):
            first = corners[index]
            second = corners[(index + 1) % 4]
            key = clone.edge_key(first, second)
            if key in clone.edges:
                continue
            role = candidate.edge_roles.get(
                (first, second), candidate.edge_roles.get((second, first), "fan")
            )
            clone.add_edge(
                first,
                second,
                path=np.asarray(
                    [clone.vertices[first].point, clone.vertices[second].point]
                ),
                role=role,
                provenance=f"cavity {candidate.template}",
            )
    for corners in candidate.faces:
        clone.add_face(
            corners,
            role="layer" if cavity.has_layer else "core",
            provenance=f"cavity {candidate.template}",
        )
    return clone


def _count_coupling(graph: pg.PatchGraph) -> list[dict]:
    """Equality components that tie a tangential resolution to a normal one.

    BlockDrawer forces opposite edges of a block to carry the same cell count,
    so the components of that relation decide the mesh resolution.  A component
    holding both a wall/front/ring edge and a layer/core spoke forces the
    boundary-layer thickness resolution to equal a streamwise one, and because
    all the spokes of a wall chain are already one component, a single such
    merge destroys the whole chain's near-wall grading.  That is a property of
    the topology, so it is measured here rather than discovered later as bad
    aspect ratios.
    """
    found = []
    for component in pg.constraint_components(graph):
        roles = {graph.edges[key].role for key in component}
        tangential = sorted(roles & TANGENTIAL_ROLES)
        normal = sorted(roles & NORMAL_ROLES)
        if tangential and normal:
            found.append(
                {
                    "tangential_roles": tangential,
                    "normal_roles": normal,
                    "edges": len(component),
                    "example": [
                        [list(key[0]), list(key[1])] for key in component[:4]
                    ],
                }
            )
    return found


# ---------------------------------------------------------------------------
# The repair stage
# ---------------------------------------------------------------------------


@dataclass
class RepairResult:
    graph: pg.PatchGraph
    cavities: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def unresolved(self) -> list:
        return [record for record in self.cavities if record.get("applied") is None
                and not record.get("kept_existing")]


def incumbent_candidate(graph: pg.PatchGraph, cavity: Cavity) -> Candidate:
    """The construction already in the cavity, scored like any alternative."""
    faces = tuple(
        tuple(face.corners)
        for face in graph.faces
        if tuple(face.key) in set(cavity.faces)
    )
    vertices = {key: graph.vertices[key].point for key in cavity.interior}
    roles = {}
    paths = {}
    for corners in faces:
        for index in range(4):
            first = corners[index]
            second = corners[(index + 1) % 4]
            key = graph.edge_key(first, second)
            edge = graph.edges.get(key)
            if edge is None:
                continue
            roles[(first, second)] = edge.role
            paths[key] = edge.path
    return Candidate(
        "existing",
        "existing",
        cavity.incident_faces,
        cavity.depth,
        {},
        new_vertices=vertices,
        faces=faces,
        edge_roles=roles,
        edge_paths=paths,
        face_count=len(faces),
    )


def enumerate_candidates(
    graph: pg.PatchGraph, cavity: Cavity, context: Context, options: CavityOptions
) -> list[Candidate]:
    """Validate a deterministic set of alternatives, best first."""
    frame = build_frame(graph, cavity)
    label = _label(cavity.vertex)
    wanted = {
        str(name)
        for key, name in (tuple(item) for item in options.choices)
        if str(key) == label
    }
    found: list[Candidate] = []
    for template in templates(options, cavity.size):
        if wanted and template.name not in wanted:
            continue
        ranked: list[tuple] = []
        for parameters, points in placements(
            template, frame, cavity, graph, options
        ):
            faces, new_vertices, roles = resolve(template, cavity, points)
            placed = {
                **{key: graph.vertices[key].point for key in cavity.boundary},
                **new_vertices,
            }
            local = math.inf
            for corners in faces:
                array = np.asarray([placed[key] for key in corners])
                if g2.polygon_area(array) < 0.0:
                    array = array[::-1]
                local = min(local, face_quality(array))
            ranked.append((local, parameters, faces, new_vertices, roles))
        ranked.sort(key=lambda item: (-item[0], sorted(item[1].items())))
        best: Candidate | None = None
        rejected: Candidate | None = None
        measured = 0
        for local, parameters, faces, new_vertices, roles in ranked[
            : max(1, options.validation_budget)
        ]:
            candidate = Candidate(
                f"fan_cavity:{_label(cavity.vertex)}:{template.name}",
                template.name,
                template.sectors,
                cavity.depth,
                parameters,
                new_vertices=new_vertices,
                faces=faces,
                edge_roles=roles,
                face_count=len(faces),
            )
            validate(candidate, context, options)
            if candidate.valid:
                # Rank the surviving placements by the objective, not by the
                # order they were screened in: local face quality only decides
                # which placements are worth validating.
                measure(candidate, context, options)
                measured += 1
                if best is None or candidate.score > best.score:
                    best = candidate
                if measured >= options.measured_placements:
                    break
                continue
            if rejected is None or local > rejected.quality:
                rejected = candidate
        found.append(best if best is not None else rejected)
    return [item for item in found if item is not None]


def repair(graph: pg.PatchGraph, domain, *, options: CavityOptions | None = None):
    """Replace every sharp-feature cavity that a better valid topology fits.

    Nothing is committed until the replacement has been built on a copy and
    passed every structural and geometric check, so a rejected candidate leaves
    the graph with exactly the topology signature it had.
    """
    settings = options or CavityOptions()
    result = RepairResult(graph)
    if not settings.enabled or graph is None:
        return result
    features = feature_corners(graph, settings)
    rewritten: set[tuple] = set()
    current = graph
    baseline_coupling = len(_count_coupling(current))
    for feature in features:
        record = {
            "feature": list(feature["vertex"]),
            "fluid_angle_degrees": feature["fluid_angle"],
            "incident_faces": feature["incident_faces"],
            "mean_corner_degrees": feature["mean_corner"],
            "worst_incident_face_quality": feature["worst_incident_face_quality"],
            "has_folded_face": feature["has_folded_face"],
            "opened_because": feature["reason"],
            "applied": None,
            "kept_existing": False,
        }
        if not any(
            item["vertex"] == feature["vertex"]
            for item in feature_corners(current, settings)
        ):
            # A neighbouring cavity has already replaced the block that made
            # this one a candidate, so there is nothing left to repair.
            record["kept_existing"] = True
            record["rejection"] = (
                "an earlier repair removed the reason this cavity was opened"
            )
            result.cavities.append(record)
            continue
        cavity, reason = build_cavity(
            current, feature["vertex"], depth=settings.growth_depth
        )
        if cavity is None:
            shallow, deep_reason = build_cavity(
                current, feature["vertex"], depth=1
            )
            if shallow is None:
                record["rejection"] = reason or deep_reason
                record["needs"] = "topology"
                result.cavities.append(record)
                continue
            cavity = shallow
        if set(cavity.faces) & rewritten:
            record["rejection"] = (
                "an adjacent feature has already rewritten part of this cavity"
            )
            record["needs"] = "topology"
            record["cavity"] = cavity.described()
            result.cavities.append(record)
            continue
        record["cavity"] = cavity.described()
        context = build_context(current, cavity, domain, settings)
        incumbent = incumbent_candidate(current, cavity)
        validate(incumbent, context, settings, existing_ok=True)
        if incumbent.valid:
            measure(incumbent, context, settings)
        record["existing"] = incumbent.described()
        candidates = enumerate_candidates(current, cavity, context, settings)
        # Screen every validated alternative against the whole graph, not only
        # the first one that fits: an agent reading the report needs to see
        # that a locally excellent fan was refused for merging two cell-count
        # components, and not merely that something else was applied.
        clones: dict[str, pg.PatchGraph] = {}
        for candidate in candidates:
            if not candidate.valid:
                continue
            clone = apply_candidate(current, cavity, candidate)
            problems = _commit_problems(
                current, clone, cavity, settings, baseline_coupling
            )
            if problems:
                candidate.rejections.extend(problems)
                continue
            candidate.index_before = current.total_index()
            candidate.index_after = clone.total_index()
            clones[candidate.identifier] = clone
        ordered = sorted(
            candidates,
            key=lambda item: (not item.valid, -item.score, item.identifier),
        )
        record["candidates"] = [
            item.described() for item in ordered[: settings.reported_candidates]
        ]
        threshold = (
            incumbent.score if incumbent.valid else -math.inf
        ) + settings.improvement_margin
        chosen = next(
            (
                item
                for item in ordered
                if item.valid
                and item.identifier in clones
                and item.score >= threshold
            ),
            None,
        )
        if chosen is None:
            if incumbent.valid:
                record["kept_existing"] = True
                record["rejection"] = (
                    "no validated alternative improves on the construction "
                    "already in this cavity"
                )
                record["needs"] = None
            else:
                record["rejection"] = (
                    "no cavity-compatible alternative is both valid and better "
                    "than the existing construction, which is itself invalid"
                )
                record["needs"] = _needs(ordered)
                record["existing_rejections"] = list(incumbent.rejections)
            result.cavities.append(record)
            continue
        current = clones[chosen.identifier]
        fresh = current.faces[-len(chosen.faces):]
        rewritten.update(tuple(face.key) for face in fresh)
        record["applied"] = chosen.identifier
        record["applied_template"] = chosen.template
        record["index_sum_before"] = chosen.index_before
        record["index_sum_after"] = chosen.index_after
        record["replaced_faces"] = len(cavity.faces)
        record["new_faces"] = len(chosen.faces)
        result.applied.append(record)
        result.cavities.append(record)
        result.notes.append(
            f"{_label(cavity.vertex)}: {chosen.template} replaced "
            f"{len(cavity.faces)} faces with {len(chosen.faces)}; worst "
            f"affected face quality {chosen.quality:.3f}"
        )
    result.graph = current
    return result


def _needs(candidates) -> str:
    """Whether the cavity wants a different placement or a different topology."""
    for candidate in candidates:
        reasons = {item.get("reason") for item in candidate.rejections}
        if reasons and reasons <= {"quality_below_minimum"}:
            return "placement"
    return "topology"


def _commit_problems(
    before: pg.PatchGraph,
    after: pg.PatchGraph,
    cavity: Cavity,
    options: CavityOptions,
    baseline_coupling: int,
) -> list[dict]:
    """Structural checks that only make sense on the whole replaced graph."""
    found: list[dict] = []
    expected = after.euler_characteristic
    if after.euler() != expected:
        found.append(
            {
                "reason": "euler_characteristic",
                "observed": after.euler(),
                "limit": expected,
                "detail": "the replacement changes the Euler characteristic",
            }
        )
    if after.total_index() != 4 * expected:
        found.append(
            {
                "reason": "index_sum",
                "observed": after.total_index(),
                "limit": 4 * expected,
                "detail": "the replacement does not preserve the total index",
            }
        )
    structural = {
        "non_manifold_edge",
        "broken_boundary_cycle",
        "hanging_vertex",
        "orphan_edge",
        "missing_edge",
        "degenerate_face",
        "face_orientation",
        "unnamed_boundary_edge",
        "interior_edge_with_boundary",
        "degenerate_edge",
    }

    def structural_problems(graph):
        return {
            repr(sorted(item.items(), key=str))
            for item in graph.problems(check_geometry=False)
            if item["kind"] in structural
        }

    fresh = structural_problems(after) - structural_problems(before)
    for item in sorted(fresh):
        found.append(
            {
                "reason": "structural_problem",
                "detail": item[:240],
            }
        )
    if not options.allow_count_coupling and cavity.has_layer:
        coupling = _count_coupling(after)
        if len(coupling) > baseline_coupling:
            found.append(
                {
                    "reason": "count_component_coupling",
                    "components": coupling[:2],
                    "detail": (
                        "the replacement merges a wall/front/ring cell-count "
                        "component with a layer or core spoke component, which "
                        "forces the boundary-layer thickness resolution of the "
                        "whole chain to equal a streamwise resolution"
                    ),
                }
            )
    return found


def described(result: RepairResult) -> dict:
    return {
        "stage": "sharp-feature cavity repair",
        "applied": [
            {
                "feature": record["feature"],
                "template": record.get("applied_template"),
                "replaced_faces": record.get("replaced_faces"),
                "new_faces": record.get("new_faces"),
                "index_sum_before": record.get("index_sum_before"),
                "index_sum_after": record.get("index_sum_after"),
            }
            for record in result.applied
        ],
        "cavities": result.cavities,
        "notes": result.notes,
    }
