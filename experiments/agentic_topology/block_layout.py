"""Turn a generalized Voronoi graph into conformal four-sided patches.

Every Voronoi cell is an annulus between its own site boundary (the *wall*) and
a closed chain of bisector branches (the *ring*).  Cutting the annulus at an
ordered set of anchors produces four-sided patches whose shared entities - ring
sections and spokes - carry one identity, so the block complex built on top of
them is conformal by construction.

Anchors are chosen from geometry only: branch endpoints (the junctions), wall
curvature extrema and, where a patch still turns too far, the station that
halves its wall turning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import layers as layer_module
from sites import Site
from voronoi_graph import Diagram


class LayoutError(RuntimeError):
    """Raised when the Voronoi graph cannot be cut into annular cells."""


@dataclass(frozen=True)
class RingStep:
    """One branch traversal inside a cell's ring."""

    branch: int
    reversed: bool


@dataclass
class Cell:
    site: int
    steps: tuple[RingStep, ...]
    ring: np.ndarray
    offsets: tuple[float, ...]
    lengths: tuple[float, ...]

    @property
    def ring_length(self) -> float:
        return float(sum(self.lengths))

    def station_of(self, branch: int, position: float) -> float:
        """Ring arc length of a station given as a branch arc length."""
        for index, step in enumerate(self.steps):
            if step.branch != branch:
                continue
            local = self.lengths[index] - position if step.reversed else position
            return self.offsets[index] + local
        raise LayoutError(f"branch {branch} is not part of this cell")

    def branch_station(self, station: float) -> tuple[int, float]:
        """Inverse of :meth:`station_of`: ring arc length to branch position."""
        value = station % self.ring_length
        for index, step in enumerate(self.steps):
            local = value - self.offsets[index]
            if -1e-12 <= local <= self.lengths[index] + 1e-12:
                local = min(max(local, 0.0), self.lengths[index])
                position = self.lengths[index] - local if step.reversed else local
                return step.branch, position
        raise LayoutError("ring station outside the cell")


@dataclass
class Anchor:
    """A ring station shared by the two cells that meet at a branch.

    ``position`` is a design variable: the optimiser slides an anchor along its
    branch, so identity comes from ``serial`` (or from the junction it pins)
    rather than from the current coordinate.
    """

    branch: int
    position: float
    kind: str
    junction: int | None = None
    hint: tuple[int, float] | None = None
    serial: int = -1
    # A second ``(site, wall station)`` pin for the cell on the other side of
    # the branch: a wake anchor pins the trailing edge on the body and the
    # wake's exit point on the outer boundary.
    extra_hint: tuple[int, float] | None = None
    # A fixed anchor is a scaffold element (a wake's ring crossing), not a
    # design variable of the relaxation.
    fixed: bool = False

    @property
    def key(self) -> tuple:
        """Identity shared by every cell that sees this station."""
        if self.junction is not None:
            return ("junction", self.junction)
        return ("anchor", self.serial)

    @property
    def movable(self) -> bool:
        return self.junction is None and not self.fixed

    def hint_for(self, site: int) -> float | None:
        for candidate in (self.hint, self.extra_hint):
            if candidate is not None and candidate[0] == site:
                return float(candidate[1])
        return None


@dataclass
class Cut:
    """One spoke: a ring anchor plus its gate on the cell's own wall."""

    cell: int
    anchor: Anchor
    ring_station: float
    wall_station: float
    ring_point: np.ndarray
    wall_point: np.ndarray
    pinned: bool = False
    clearance: float = 0.0
    projected_station: float = 0.0

    def unpin(self) -> None:
        self.pinned = False
        self.wall_station = self.projected_station


@dataclass
class Patch:
    """A four-sided region bounded by wall, spoke, ring and spoke."""

    index: int
    cell: int
    site: int
    first_cut: int
    second_cut: int
    wall: np.ndarray
    ring: np.ndarray
    anticlockwise: bool
    ring_start: float = 0.0
    ring_end: float = 0.0
    wall_start: float = 0.0
    wall_end: float = 0.0

    @property
    def corners(self) -> np.ndarray:
        return np.asarray(
            [self.wall[0], self.wall[-1], self.ring[-1], self.ring[0]]
        )

    def wall_turning(self) -> float:
        return g2.total_turning(self.wall)

    def ring_turning(self) -> float:
        return g2.total_turning(self.ring)

    def boundary(self) -> np.ndarray:
        """Closed polygon of the patch, anticlockwise."""
        loop = np.vstack([self.wall, self.ring[::-1], self.wall[:1]])
        if g2.signed_area(loop) < 0.0:
            loop = loop[::-1].copy()
        return loop


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _slot_map(diagram: Diagram) -> dict[tuple[int, int], tuple[int, int]]:
    result: dict[tuple[int, int], tuple[int, int]] = {}
    for branch in diagram.branches:
        for side, end in enumerate(branch.ends):
            if end is not None:
                result[end] = (branch.index, side)
    return result


def build_cells(diagram: Diagram) -> list[Cell]:
    slots = _slot_map(diagram)
    cells: list[Cell] = []
    for site in diagram.sites:
        nodes = [
            (junction.index, sector)
            for junction in diagram.junctions
            for sector in range(junction.degree)
            if junction.sites[sector] == site.index
        ]
        if not nodes:
            cells.append(_loop_cell(diagram, site))
            continue
        cells.append(_chained_cell(diagram, site, nodes, slots))
    return cells


def _loop_cell(diagram: Diagram, site: Site) -> Cell:
    loops = [
        branch
        for branch in diagram.branches
        if site.index in branch.pair and branch.closed
    ]
    if len(loops) != 1:
        raise LayoutError(
            f"site {site.name!r} has {len(loops)} closed bisector loops and no "
            "junction; only a single annular cell is supported"
        )
    branch = loops[0]
    path = branch.path
    reversed_step = g2.signed_area(path) < 0.0
    ring = path[::-1].copy() if reversed_step else path
    length = g2.total_length(ring)
    return Cell(
        site.index,
        (RingStep(branch.index, reversed_step),),
        ring,
        (0.0,),
        (length,),
    )


def _chained_cell(diagram: Diagram, site: Site, nodes, slots) -> Cell:
    junctions = diagram.junctions
    start = nodes[0]
    steps: list[RingStep] = []
    visited: set[tuple[int, int]] = set()
    node = start
    while True:
        if node in visited:
            raise LayoutError(
                f"the ring of site {site.name!r} revisits a junction sector"
            )
        visited.add(node)
        junction_index, sector = node
        junction = junctions[junction_index]
        slot = (sector + 1) % junction.degree
        entry = slots.get((junction_index, slot))
        if entry is None:
            raise LayoutError(
                f"junction {junction_index} slot {slot} has no traced branch"
            )
        branch_index, side = entry
        branch = diagram.branches[branch_index]
        steps.append(RingStep(branch_index, side == 1))
        other = branch.ends[1 - side]
        if other is None:
            raise LayoutError("an open branch cannot close a cell ring")
        next_junction, next_slot = other
        degree = junctions[next_junction].degree
        if junctions[next_junction].sites[next_slot] == site.index:
            node = (next_junction, next_slot)
        else:
            node = (next_junction, (next_slot - 1) % degree)
            if junctions[next_junction].sites[node[1]] != site.index:
                raise LayoutError(
                    "a branch arrived at a junction sector that does not carry "
                    f"site {site.name!r}"
                )
        if node == start:
            break
    if len(visited) != len(nodes):
        raise LayoutError(
            f"site {site.name!r} has a ring with more than one component"
        )
    ring, offsets, lengths = _assemble_ring(diagram, steps)
    if g2.signed_area(ring) < 0.0:
        steps = [RingStep(step.branch, not step.reversed) for step in reversed(steps)]
        ring, offsets, lengths = _assemble_ring(diagram, steps)
    return Cell(site.index, tuple(steps), ring, tuple(offsets), tuple(lengths))


def _assemble_ring(diagram: Diagram, steps):
    pieces = []
    offsets = []
    lengths = []
    position = 0.0
    for step in steps:
        path = diagram.branches[step.branch].path
        piece = path[::-1] if step.reversed else path
        offsets.append(position)
        length = g2.total_length(piece)
        lengths.append(length)
        position += length
        pieces.append(piece[:-1])
    ring = np.vstack([*pieces, pieces[0][:1]])
    return ring, offsets, lengths


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def junction_anchors(diagram: Diagram) -> list[Anchor]:
    anchors: list[Anchor] = []
    for branch in diagram.branches:
        if branch.closed:
            continue
        length = branch.length
        for position, end in zip((0.0, length), branch.ends):
            anchors.append(
                Anchor(branch.index, position, "junction", end[0] if end else None)
            )
    return anchors


# Two forced anchors on one branch closer than this fraction of the ring
# separation floor are one cut pinned on both walls (``AnchorSet._merge``).
MERGE_FRACTION = 0.1


# A curvature anchor marks an extremum: the smoothed curvature falls by this
# fraction within this fraction of the perimeter on both sides of it (a 1.5:1
# ellipse's tip falls 22 percent within five percent, a 2:1 ellipse's 49;
# ripples on a plateau of the Gaussian-smoothed curvature are below 1e-5).
PEAK_DROP = 0.02
PEAK_WINDOW = 0.05
# Width of the Gaussian that turns vertex turning into curvature, as a
# fraction of the perimeter: the old resampling's smoothing reach.
CURVATURE_SMOOTHING = 0.004


def curvature_anchors(
    diagram: Diagram,
    cells: list[Cell],
    *,
    scale: float,
    samples: int = 256,
    prominence: float = 2.5,
    limit: int = 6,
    corner_turn: float = math.radians(45.0),
) -> list[Anchor]:
    """Wall curvature extrema projected outwards onto the cell ring.

    The curvature is the supplied vertices' turning convolved with a narrow
    periodic Gaussian (``CURVATURE_SMOOTHING`` of the perimeter) and evaluated
    on ``samples`` uniform stations: a function of the geometry alone, so a
    dense input point list does not turn discretisation noise into anchors and
    where the list starts does not matter.  (It used to be measured on a
    uniform resampling of the polyline, which cuts the supplied corners
    wherever its samples fall, and the ripples that left on a circular nose
    became anchors that followed the start point.)  Each peak is placed between
    the stations by a parabola.  The smoothing width and the prominence
    threshold are relative, so the result follows translation, rotation,
    uniform scaling, point-list reversal and the start point.
    """
    anchors: list[Anchor] = []
    for cell in cells:
        site = diagram.sites[cell.site]
        if site.curve.kind != "wall":
            continue
        loop = site.curve.loop()
        loop_stations = g2.cumulative_length(loop)
        perimeter = float(loop_stations[-1])
        vertex_count = len(loop) - 1
        if perimeter <= 0.0 or vertex_count < 3:
            continue
        theta = np.abs(g2.turning_angles(loop, closed=True))[:vertex_count]
        sigma = CURVATURE_SMOOTHING * perimeter
        grid = np.arange(samples) * (perimeter / samples)
        offsets = (grid[:, None] - loop_stations[None, :vertex_count] + 0.5 * perimeter) % perimeter - 0.5 * perimeter
        kernel = np.exp(-0.5 * (offsets / sigma) ** 2) / (sigma * math.sqrt(2.0 * math.pi))
        smoothed = kernel @ theta
        median = float(np.median(smoothed))
        if median <= 0.0:
            continue
        nodes = grid
        # A peak must be an extremum of the curvature, not a ripple on a
        # plateau: on a circular nose every resampled point sits at the same
        # curvature give or take where the samples fall between the supplied
        # vertices, and those ripples - which follow the list's start point -
        # used to become anchors.  The smoothed curvature has to fall by
        # ``PEAK_DROP`` on both sides within ``PEAK_WINDOW`` of the perimeter.
        reach = max(2, int(round(PEAK_WINDOW * samples)))
        count = len(nodes)

        def falls_away(index: int) -> bool:
            left = [smoothed[(index - step) % count] for step in range(1, reach + 1)]
            right = [smoothed[(index + step) % count] for step in range(1, reach + 1)]
            return min(left) <= (1.0 - PEAK_DROP) * smoothed[index] and min(right) <= (1.0 - PEAK_DROP) * smoothed[index]

        peaks = [
            index
            for index in range(len(nodes))
            if smoothed[index] >= prominence * median
            and smoothed[index] >= smoothed[index - 1]
            and smoothed[index] >= smoothed[(index + 1) % len(nodes)]
            and falls_away(index)
        ]
        peaks.sort(key=lambda index: -smoothed[index])
        stations = np.concatenate((grid, [perimeter]))
        # A sharp corner is a curvature peak of its own; the resampled peak
        # lands a step away from the vertex, and the corner anchor already
        # names that feature.  Skip peaks within two steps of one.
        total = float(stations[-1])
        step = total / samples
        turning = g2.turning_angles(loop, closed=True)
        corner_stations = g2.cumulative_length(loop)[:-1][np.abs(turning) >= corner_turn]
        for index in peaks[:limit]:
            # The resampling starts at the list's first point, so a peak
            # sample sits up to half a step from the true extremum depending
            # on where the list starts.  A parabola through the peak and its
            # neighbours places it to a small fraction of a step, which makes
            # the anchor station independent of the start point and of the
            # direction the list runs in.
            before = float(smoothed[(index - 1) % count])
            here = float(smoothed[index])
            after = float(smoothed[(index + 1) % count])
            denominator = before - 2.0 * here + after
            shift = 0.0
            if denominator < 0.0:
                shift = min(0.5, max(-0.5, 0.5 * (before - after) / denominator))
            station = (float(stations[index]) + shift * step) % total
            if len(corner_stations):
                gaps = np.abs(corner_stations - station)
                gaps = np.minimum(gaps, total - gaps)
                if float(np.min(gaps)) <= 2.0 * step:
                    continue
            anchor = anchor_from_wall(
                diagram, cell, station, "curvature"
            )
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def corner_anchors(
    diagram: Diagram, cells: list[Cell], *, minimum_turn: float = math.radians(45.0)
) -> list[Anchor]:
    """Mandatory anchors at sharp convex wall corners.

    A sharp corner is the closest wall point of a whole angular sector, so a
    patch whose wall section runs through one cannot be a well-shaped
    quadrilateral.  Pinning a gate exactly on the corner keeps every wall
    section smooth.
    """
    anchors: list[Anchor] = []
    for cell in cells:
        site = diagram.sites[cell.site]
        loop = site.curve.loop()
        turning = g2.turning_angles(loop, closed=True)
        stations = g2.cumulative_length(loop)[:-1]
        fluid = math.pi + site.curve.fluid_sign * turning
        for index, angle in enumerate(turning):
            if abs(angle) < minimum_turn:
                continue
            if float(fluid[index]) < layer_module.REFLEX_FLUID_ANGLE:
                continue  # reflex corners are anchored by reflex_anchors()
            anchor = anchor_from_wall(
                diagram, cell, float(stations[index]), "corner"
            )
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def reflex_anchors(diagram: Diagram, cells: list[Cell]) -> list[Anchor]:
    """Mandatory anchors at every reflex wall corner.

    A vertex the fluid sees at less than ``layers.REFLEX_FLUID_ANGLE`` has no
    tangent disk: the level set of the wall distance has a mitre there, and
    the band's spoke must run along the bisector to it, so the corner has to
    be a gate whatever the separation rule says, and its pin is never given
    up.
    """
    anchors: list[Anchor] = []
    for cell in cells:
        site = diagram.sites[cell.site]
        # The outer boundary is judged with its own fluid sign: a convex
        # corner of a rectangular far field gives the fluid 90 degrees, and
        # the distance level set has a mitre there just as at a wall's
        # reflex corner, so the corner is a gate with its spoke on the
        # bisector.  A sampled circle turns far too little to qualify.
        loop = site.curve.loop()
        # A rounded reflex corner - a tight concave bend - counts as a reflex
        # vertex at its turning midpoint (``layers.effective_fluid_angles``).
        fluid = layer_module.effective_fluid_angles(loop, site.curve.fluid_sign)[: len(loop) - 1]
        stations = g2.cumulative_length(loop)[:-1]
        for index in np.nonzero(fluid < layer_module.REFLEX_FLUID_ANGLE)[0]:
            anchor = anchor_from_wall(
                diagram, cell, float(stations[index]), "reflex"
            )
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def chain_anchors(diagram: Diagram, cells: list[Cell], *, outer_ray: bool = False) -> list[Anchor]:
    """Mandatory anchors where one named boundary chain meets the next.

    A block edge carries exactly one patch name, so a loop made of several
    chains - a far field with an inlet, an outlet and two sides, or a C-shaped
    boundary whose cap meets its legs tangentially - needs a gate at every
    chain break whether or not the boundary turns there.
    """
    anchors: list[Anchor] = []
    for cell in cells:
        site = diagram.sites[cell.site]
        for station in site.chain_breaks:
            anchor = anchor_from_wall(diagram, cell, float(station), "chain", outer_ray=outer_ray)
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def anchor_from_wall(
    diagram: Diagram, cell: Cell, wall_station: float, kind: str, *, outer_ray: bool = False
) -> Anchor | None:
    """Anchor a wall station on the cell ring where its spoke is squarest.

    Two candidates: the closest ring point, where the spoke meets the ring
    squarely, and the first ring crossing of the station's outward ray -
    the bisector of the two outward normals: the normal on a smooth wall,
    the exterior bisector at a convex corner, the mitre direction at a
    reflex one - where the spoke leaves the wall squarely.  The one whose
    straight spoke deviates least from both normals, judged by the larger
    of its two deviations, is taken.  Near a ring that follows the wall the
    two agree; beside a second body, where the ring passes close behind a
    foil and the closest ring point of its nose lay behind its tail, and in
    front of a far ring, where every nose station has the same closest ring
    point, the ray wins.  A convex or reflex corner always takes the ray,
    since its closest point is one of a whole arc; the outer boundary always
    keeps the closest point, since its ring is in front of every station.
    """
    site = diagram.sites[cell.site]
    point = site.curve.point_at(wall_station)
    closest: tuple[float, int, float] | None = None
    for step in cell.steps:
        branch = diagram.branches[step.branch]
        result = g2.closest_on_polyline(branch.path, np.asarray([point]))
        distance = float(result.distance[0])
        if closest is None or distance < closest[0]:
            closest = (distance, step.branch, float(result.arclength[0]))
    if closest is None:
        return None
    direction = outward_direction(site, wall_station)
    if direction is None or (site.curve.kind != "wall" and not outer_ray):
        return Anchor(closest[1], closest[2], kind, None, (cell.site, wall_station))
    hit: tuple[float, int, float] | None = None
    for step in cell.steps:
        branch = diagram.branches[step.branch]
        for t, arclength in _ray_polyline_hits(point, direction, branch.path):
            if hit is None or t < hit[0]:
                hit = (t, step.branch, arclength)
    if hit is None:
        chosen = closest
    elif kind in ("corner", "reflex") or site.curve.kind != "wall":
        # Corners always take the ray; so does a hull's chain break, whose
        # closest ring point at a pointed end of the hull is arbitrary.
        chosen = hit
    else:
        # The closest point is the default; the ray replaces it only when it
        # is clearly squarer.  Where both are nearly square the choice is
        # immaterial to the spoke but not to the gate's exact station, and a
        # band front at a blunt tip is sensitive to that.
        closest_deviation = _spoke_deviation(diagram, point, direction, closest[1], closest[2])
        ray_deviation = _spoke_deviation(diagram, point, direction, hit[1], hit[2])
        chosen = hit if ray_deviation + RAY_MARGIN < closest_deviation else closest
    return Anchor(chosen[1], chosen[2], kind, None, (cell.site, wall_station))


# The outward ray replaces the closest ring point as a station's anchor only
# when its spoke is squarer by at least this much.
RAY_MARGIN = math.radians(15.0)


def _spoke_deviation(diagram: Diagram, point, direction, branch_index: int, arclength: float) -> float:
    """The larger of the straight spoke's angles to the wall and ring normals."""
    path = diagram.branches[branch_index].path
    ring_point = g2.sample_at_arclength(path, [arclength])[0]
    spoke = ring_point - point
    length = float(np.linalg.norm(spoke))
    if length <= 0.0:
        return math.pi
    spoke = spoke / length
    total = g2.total_length(path)
    step = max(1.0e-3 * total, 1.0e-12)
    before, after = g2.sample_at_arclength(
        path, [max(arclength - step, 0.0), min(arclength + step, total)]
    )
    tangent = after - before
    norm = float(np.linalg.norm(tangent))
    ring_deviation = 0.0 if norm <= 0.0 else abs(math.asin(max(-1.0, min(1.0, float(np.dot(spoke, tangent / norm))))))
    wall_deviation = math.acos(max(-1.0, min(1.0, float(np.dot(spoke, direction)))))
    return max(wall_deviation, ring_deviation)


def outward_direction(site: Site, wall_station: float) -> np.ndarray | None:
    """Unit bisector of the outward (fluid-side) normals just before and after a station."""
    total = site.curve.length()
    step = 1.0e-6 * total
    before = site.curve.fluid_normal((wall_station - step) % total)
    after = site.curve.fluid_normal((wall_station + step) % total)
    direction = before + after
    norm = float(np.linalg.norm(direction))
    if norm <= 1.0e-9:
        direction = before
        norm = float(np.linalg.norm(direction))
        if norm <= 0.0:
            return None
    return direction / norm


def _ray_polyline_hits(origin, direction, poly) -> list[tuple[float, float]]:
    """Ray crossings of a polyline as ``(distance along the ray, arc length)``."""
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
    return [
        (float(t[i]), float(cumulative[i] + u[i] * lengths[i])) for i in np.flatnonzero(valid)
    ]


class AnchorSet:
    """Accepted ring anchors, kept far enough apart on ring and on wall.

    An anchor belongs to a branch, so the two cells that share the branch see
    the same station: accepting one creates a conformal cut on both sides.  An
    anchor is rejected when it would crowd an existing cut on either incident
    ring or either incident wall.
    """

    def __init__(
        self,
        diagram: Diagram,
        cells: list[Cell],
        *,
        scale: float,
        ring_separation: float = 0.5,
        gate_balance: float = 0.35,
        floor_by_wall_gap: bool = False,
    ):
        self.diagram = diagram
        self.cells = cells
        self.scale = scale
        self.ring_separation = ring_separation
        self.gate_balance = gate_balance
        self.floor_by_wall_gap = floor_by_wall_gap
        self.cell_of_site = {cell.site: index for index, cell in enumerate(cells)}
        self.by_branch: dict[int, list[Anchor]] = {
            branch.index: [] for branch in diagram.branches
        }
        self.rejected: list[tuple[Anchor, str]] = []
        self._keys: set[tuple] = set()
        self._serial = 0
        self._stations: dict[int, list[tuple[float, float]]] = {
            index: [] for index in range(len(cells))
        }

    def _cells_of(self, branch_index: int) -> list[int]:
        pair = self.diagram.branches[branch_index].pair
        return [self.cell_of_site[site] for site in pair]

    def _measure(self, cell_index: int, anchor: Anchor):
        cell = self.cells[cell_index]
        site = self.diagram.sites[cell.site]
        station = cell.station_of(anchor.branch, anchor.position)
        point = g2.sample_at_arclength(cell.ring, [station])[0]
        projected = site.curve.closest(np.asarray([point]))
        clearance = float(projected.distance[0])
        raw = float(projected.arclength[0])
        hinted = anchor.hint_for(cell.site)
        if hinted is not None:
            wall_station = hinted % site.curve.length()
            return (
                station,
                wall_station,
                point,
                site.curve.point_at(wall_station),
                True,
                clearance,
                raw,
            )
        return (station, raw, point, projected.point[0], False, clearance, raw)

    def add(self, anchor: Anchor, *, force: bool = False) -> bool:
        if any(
            existing.key == anchor.key for existing in self.by_branch[anchor.branch]
        ):
            return False
        if anchor.junction is not None and anchor.key in self._keys:
            # A junction is a station on every branch that ends there; record it
            # on this branch too so that both incident cells can see the cut.
            self.by_branch[anchor.branch].append(anchor)
            self.by_branch[anchor.branch].sort(key=lambda item: item.position)
            return True
        measurements = []
        for cell_index in self._cells_of(anchor.branch):
            cell = self.cells[cell_index]
            site = self.diagram.sites[cell.site]
            station, wall_station, _, _, _, clearance, _ = self._measure(
                cell_index, anchor
            )
            total = site.curve.length()
            for other_ring, other_wall, other_clearance in self._stations[cell_index]:
                ring_gap = _cyclic_gap(station, other_ring, cell.ring_length)
                if ring_gap <= 1e-9 * self.scale:
                    self.rejected.append((anchor, "coincides with an existing cut"))
                    return False
                # The floor protects against a core patch that is tall and
                # thin: its ring section must not be much shorter than the
                # clearance.  Separation follows the local feature size, so
                # inside a narrow gap neighbouring spokes may be close.  A
                # producer that does not build its core on the ring - the
                # C-grid, whose levels are the body's own offsets - judges
                # the floor against the wall gap too, so refining a body's
                # gates is not refused because the ring is far away.
                wall_gap = _cyclic_gap(wall_station, other_wall, total)
                bound = min(clearance, other_clearance)
                if self.floor_by_wall_gap:
                    bound = min(bound, wall_gap)
                limit = self.ring_separation * bound
                # In wall-gap mode the outer cell keeps only the coincidence
                # check: its patches taper towards the ring by nature - the
                # ring is their inner side - and the producer does not build
                # on them.
                judged = site.curve.kind == "wall" or not self.floor_by_wall_gap
                if force and ring_gap < limit and self._merge(anchor, cell_index, station, limit):
                    # Pinned onto the existing cut instead of beside it.
                    return True
                if not force and judged and ring_gap < limit:
                    self.rejected.append(
                        (
                            anchor,
                            f"crowds an existing cut on {site.name!r} "
                            f"(ring gap {ring_gap:.4g} < {limit:.4g})",
                        )
                    )
                    return False
            measurements.append((cell_index, station, wall_station, clearance))
        for cell_index, station, wall_station, clearance in measurements:
            self._stations[cell_index].append((station, wall_station, clearance))
        anchor.serial = self._serial
        self._serial += 1
        self._keys.add(anchor.key)
        self.by_branch[anchor.branch].append(anchor)
        self.by_branch[anchor.branch].sort(key=lambda item: item.position)
        return True

    def _merge(self, anchor: Anchor, cell_index: int, station: float, limit: float) -> bool:
        """Pin a forced anchor onto an existing cut that crowds it, if one can take it.

        Two forced anchors a sliver apart on one branch - a square body's
        corner and the corner of a box with equal margins, both on the box
        diagonal - are one spoke: kept as two near-coincident cuts, the
        unpinned one's projection onto the other wall flips across the
        diagonal and the gates wind round the box almost twice.  The existing
        anchor takes the new one's wall pin as its second pin, as a wake
        anchor pins both of its walls.
        """
        # Only ordinary wall anchors merge.  A wake anchor is a scaffold
        # element the wake writer finds by its own identity, and a fixed or
        # doubly pinned anchor has no pin to spare.
        if anchor.hint is None or anchor.fixed or anchor.extra_hint is not None or anchor.kind == "wake":
            return False
        cell = self.cells[cell_index]
        for existing in self.by_branch[anchor.branch]:
            if existing.junction is not None or existing.fixed or existing.kind == "wake":
                continue
            gap = _cyclic_gap(cell.station_of(existing.branch, existing.position), station, cell.ring_length)
            # Only near-coincident anchors are one spoke (the square in its
            # box: 2 to 5 percent of the floor); a forced anchor merely inside
            # the floor (a teardrop tip 40 percent of it from an outlet
            # corner) keeps its own cut.
            if gap >= MERGE_FRACTION * limit:
                continue
            if existing.hint_for(anchor.hint[0]) is not None:
                return False
            if existing.hint is None:
                existing.hint = anchor.hint
            elif existing.extra_hint is None:
                existing.extra_hint = anchor.hint
            else:
                return False
            # Refresh the crowding record of the existing cut on that wall.
            for other_index in self._cells_of(existing.branch):
                if self.cells[other_index].site != anchor.hint[0]:
                    continue
                measured = self._measure(other_index, existing)
                records = self._stations[other_index]
                for position, (ring, _wall, clearance) in enumerate(records):
                    if abs(ring - measured[0]) <= 1.0e-12 * self.scale:
                        records[position] = (ring, measured[1], clearance)
            self.rejected.append((anchor, "merged into an existing forced cut on the same branch"))
            return True
        return False

    def cuts(self) -> list[list[Cut]]:
        result: list[list[Cut]] = []
        for cell_index, cell in enumerate(self.cells):
            site = self.diagram.sites[cell.site]
            cuts: list[Cut] = []
            seen: set[tuple] = set()
            for step in cell.steps:
                for anchor in self.by_branch[step.branch]:
                    if anchor.key in seen:
                        continue
                    seen.add(anchor.key)
                    measured = self._measure(cell_index, anchor)
                    cuts.append(Cut(cell_index, anchor, *measured))
            cuts.sort(key=lambda item: item.ring_station)
            if len(cuts) >= 2:
                relax_gates(site, cuts, self.gate_balance)
            self._stations[cell_index] = [
                (cut.ring_station, cut.wall_station, cut.clearance) for cut in cuts
            ]
            result.append(cuts)
        return result


def _cyclic_gap(first: float, second: float, total: float) -> float:
    difference = abs(first - second) % total
    return min(difference, total - difference)


# ---------------------------------------------------------------------------
# Cuts and patches
# ---------------------------------------------------------------------------


def relax_gates(site: Site, cuts: list[Cut], balance: float) -> None:
    """Spread collapsed gates along the wall while keeping the ring order.

    A convex wall corner is the closest point of a whole angular sector, so
    plain closest-point projection maps several ring anchors onto one station.
    This forces the stations into ring order and then spreads them until no
    wall section is shorter than ``balance`` times the local clearance (capped
    by the average section).  Correspondence therefore stays at the closest
    point wherever that is well conditioned and degrades to a local arc-length
    spread only inside the shadow of a sharp feature.  Gates that came from a
    wall feature are held fixed unless they contradict the ring order or crowd
    each other, in which case the weaker feature gives up its pin.
    """
    total = site.curve.length()
    count = len(cuts)
    for _ in range(count + 1):
        order, unwrapped, winding = _unwrap_gates(cuts, total, clamp_unpinned=True)
        if winding <= 1.0 + 1e-9:
            break
        # A pinned feature gate can contradict the ring order.  Give up the
        # weakest pin - the one entered through the longest jump - and retry.
        deltas = np.diff(np.asarray([unwrapped[0]] + unwrapped))
        candidates = [
            (float(deltas[position]), index)
            for position, index in enumerate(order)
            if cuts[index].pinned and _releasable(cuts[index])
        ]
        if not candidates:
            raise LayoutError(
                f"the gates on {site.name!r} advance {winding:.3f} times around "
                "the wall; the closest-point order is not compatible with the "
                "ring order"
            )
        cuts[max(candidates)[1]].unpin()
    else:  # pragma: no cover - defensive
        raise LayoutError(f"gate relaxation on {site.name!r} did not converge")
    for _ in range(count + 1):
        stations = np.asarray(unwrapped + [unwrapped[0] + total])
        spans = np.diff(stations)
        clearance = np.asarray([cuts[index].clearance for index in order])
        scales = np.minimum(clearance, np.roll(clearance, -1))
        floors = min(max(balance, 0.0), 0.9) * np.minimum(scales, total / count)
        # Pinned gates crowd each other when the run between them is too short
        # for the gates it has to hold: the two pins themselves, plus every
        # unpinned gate between them, which spreading can only place inside
        # that run.  Adjacent pins are the special case of an empty run.
        pinned_positions = [
            position for position in range(count) if cuts[order[position]].pinned
        ]
        crowded = []
        if len(pinned_positions) >= 2 and any(
            _releasable(cuts[order[position]]) for position in pinned_positions
        ):
            for index, position in enumerate(pinned_positions):
                following = pinned_positions[(index + 1) % len(pinned_positions)]
                steps = (following - position) % count
                if steps == 0:
                    steps = count
                run = float(sum(spans[(position + k) % count] for k in range(steps)))
                needed = float(
                    sum(floors[(position + k) % count] for k in range(steps))
                )
                if run < needed:
                    crowded.append((run, position, following))
        if not crowded:
            break
        # Two pinned feature gates cannot both stay when they crowd each other;
        # the weaker feature gives up its pin and is spread instead.
        _run, worst, partner = min(crowded)
        first = cuts[order[worst]]
        second = cuts[order[partner]]
        weaker = (
            second
            if _pin_priority(second) <= _pin_priority(first)
            else first
        )
        if not _releasable(weaker):
            # Both ends of the crowded run are scaffold pins that cannot move;
            # spreading between them is all that is left.
            break
        weaker.unpin()
        order, unwrapped, _ = _unwrap_gates(cuts, total, clamp_unpinned=True)
    if float(np.min(spans - floors)) >= 0.0:
        return
    fixed = [0] + [
        position
        for position, index in enumerate(order)
        if position > 0 and cuts[index].pinned
    ]
    for first, second in zip(fixed, fixed[1:] + [count]):
        if second - first <= 1:
            continue
        span = float(stations[second] - stations[first])
        run = _spread(
            spans[first:second],
            np.minimum(floors[first:second], 0.9 * span / (second - first)),
            span,
            site,
        )
        stations[first + 1 : second] = stations[first] + np.cumsum(run)[:-1]
    for position, index in enumerate(order):
        cuts[index].wall_station = float(stations[position] % total)
        cuts[index].wall_point = site.curve.point_at(cuts[index].wall_station)


# Pins that name a scaffold element rather than a wall feature - a wake's
# trailing edge and a chain break of the outer boundary - are never released:
# the gate has to be exactly there for the construction to exist at all.
_UNRELEASABLE = frozenset({"wake", "chain"})


def _releasable(cut: Cut) -> bool:
    return cut.anchor.kind not in _UNRELEASABLE


_PIN_PRIORITY = {
    "wake": 7,
    "chain": 6,
    "reflex": 5,
    "corner": 4,
    "curvature": 3,
    "turning": 2,
    "seed": 1,
}


def _pin_priority(cut: Cut) -> int:
    return _PIN_PRIORITY.get(cut.anchor.kind, 0)


def _unwrap_gates(cuts: list[Cut], total: float, *, clamp_unpinned: bool = False):
    """Ring-ordered gate stations forced to be non-decreasing.

    With ``clamp_unpinned``, and only when the plain reading winds the gates
    more than once round the wall, an unpinned gate - whose closest-point
    station is not authoritative, since spreading places it - is held inside
    the run between the pinned gates around it: a projection just behind its
    predecessor, or just beyond the next pin, takes a zero step instead of
    the spurious full turn that reading it forward gives.  A configuration
    that is already in ring order is returned as it is.  (The first clamp
    treated every unpinned step longer than half the wall as a step back,
    which also collapsed legitimate long steps - a wall with two gates
    always has one - and made the layout of an ellipse follow the raster
    and the orientation; the second measured gates from the start of their
    run, so a gate on the pin's own station at the end of the ring order -
    one full turn on - was read as a step back.)
    """
    count = len(cuts)
    start = next((index for index, cut in enumerate(cuts) if cut.pinned), 0)
    order = [(start + offset) % count for offset in range(count)]
    # Steps are taken between raw stations, never from the accumulated value:
    # two gates on one wall station (several ring anchors projecting onto a
    # convex corner) are a zero step, and ``(w - (w + k * total)) % total``
    # rounds to just below ``total`` - one spurious full turn.  A step within
    # rounding of a full turn is that zero step.
    tolerance = 1.0e-9 * total

    def forward(station: float, origin: float) -> float:
        step = (station - origin) % total
        return 0.0 if step > total - tolerance else step

    stations = [cuts[index].wall_station for index in order]
    unwrapped = [stations[0]]
    for position in range(1, count):
        unwrapped.append(unwrapped[-1] + forward(stations[position], stations[position - 1]))
    winding = (unwrapped[-1] - unwrapped[0]) / total
    if not clamp_unpinned or winding <= 1.0 + 1.0e-9:
        return order, unwrapped, winding
    # The runs between consecutive pinned gates; with one pin or none, the
    # whole loop from the first gate round to itself.
    pins = [position for position in range(count) if cuts[order[position]].pinned] or [0]
    unwrapped = [stations[0]] + [0.0] * (count - 1)
    for number, first in enumerate(pins):
        last = pins[number + 1] if number + 1 < len(pins) else count
        end = stations[last % count] if len(pins) > 1 else stations[first]
        length = total if len(pins) == 1 else forward(end, stations[first])
        offset, previous = 0.0, stations[first]
        for position in range(first + 1, last):
            station = stations[position]
            step = forward(station, previous)
            if offset + step > length:
                # Outside the run: just beyond the next pin, or behind its
                # predecessor (read forward, nearly a full turn), whichever
                # is nearer.
                if offset + step - length <= total - step:
                    step, station = length - offset, end
                else:
                    step, station = 0.0, previous
            offset += step
            previous = station
            unwrapped[position] = unwrapped[first] + offset
        if last < count:
            unwrapped[last] = unwrapped[first] + length
    return order, unwrapped, (unwrapped[-1] - unwrapped[0]) / total


def _spread(spans, floor, total: float, site: Site) -> np.ndarray:
    """Raise every span to its floor while keeping their sum at ``total``."""
    surplus = float(np.sum(np.maximum(spans - floor, 0.0)))
    deficit = float(np.sum(np.maximum(floor - spans, 0.0)))
    if surplus <= deficit:
        raise LayoutError(
            f"the wall of {site.name!r} is too short for {len(spans)} separated "
            "gates in one run"
        )
    factor = (surplus - deficit) / surplus
    return np.where(spans < floor, floor, floor + (spans - floor) * factor)


def build_patches(
    diagram: Diagram, cells: list[Cell], cuts: list[list[Cut]]
) -> list[Patch]:
    patches: list[Patch] = []
    for cell_index, cell in enumerate(cells):
        site = diagram.sites[cell.site]
        cell_cuts = cuts[cell_index]
        for index, cut in enumerate(cell_cuts):
            following = cell_cuts[(index + 1) % len(cell_cuts)]
            wall = site.curve.section(
                cut.wall_station, following.wall_station, forward=True
            )
            ring = _ring_section(cell, cut.ring_station, following.ring_station)
            loop = np.vstack([wall, ring[::-1], wall[:1]])
            patches.append(
                Patch(
                    len(patches),
                    cell_index,
                    cell.site,
                    index,
                    (index + 1) % len(cell_cuts),
                    wall,
                    ring,
                    g2.signed_area(loop) > 0.0,
                    cut.ring_station,
                    following.ring_station,
                    cut.wall_station,
                    following.wall_station,
                )
            )
    return patches


def _ring_section(cell: Cell, start: float, end: float) -> np.ndarray:
    return g2.loop_section(cell.ring, start, end, forward=True)


def _turning_midpoint(loop: np.ndarray, start: float, length: float) -> float | None:
    """Arc length from ``start`` at which half the turning over ``length`` is reached.

    The turning is a density along the closed loop: vertex ``k``'s absolute
    turning is spread uniformly from the middle of its incoming segment to
    the middle of its outgoing one, so the turning between two stations, and
    the station halving it, vary continuously with the stations.
    """
    stations = g2.cumulative_length(loop)
    total = float(stations[-1])
    count = len(loop) - 1
    turning = np.abs(g2.turning_angles(loop, closed=True))[:count]
    whole = float(np.sum(turning))
    if total <= 0.0 or whole <= 0.0:
        return None
    middles = 0.5 * (stations[:count] + stations[1:count + 1])
    # Breakpoints of the cumulative turning, over two periods.
    xs = np.concatenate(([middles[-1] - total], middles))
    values = np.concatenate(([0.0], np.cumsum(turning)))
    xs = np.concatenate((xs, xs[1:] + total))
    values = np.concatenate((values, values[1:] + whole))
    first = float(start) % total
    if first < xs[0]:
        first += total
    low = float(np.interp(first, xs, values))
    high = float(np.interp(first + length, xs, values))
    if high - low <= 0.0:
        return None
    target = 0.5 * (low + high)
    # The inverse of a non-decreasing piecewise-linear map: the first station
    # reaching the target.
    index = int(np.searchsorted(values, target, side="left"))
    index = min(max(index, 1), len(values) - 1)
    span = values[index] - values[index - 1]
    fraction = 0.0 if span <= 0.0 else (target - values[index - 1]) / span
    station = xs[index - 1] + fraction * (xs[index] - xs[index - 1])
    return float(station - first)


def split_anchor(
    diagram: Diagram, cells: list[Cell], patch: Patch, *, by_turning: bool
) -> Anchor | None:
    """Return a new anchor inside a patch's own ring section.

    The station is the projection of the wall point that halves either the
    patch's turning or its arc length, clamped away from the patch ends so that
    the split cannot produce a zero-length section.
    """
    cell = cells[patch.cell]
    wall_length = g2.total_length(patch.wall)
    if wall_length <= 0.0 or len(patch.wall) < 3:
        return None
    if by_turning:
        # The turning of the whole loop as a density - each vertex's turning
        # spread over its two half-segments - integrated between the gates.
        # Counting only the vertices strictly inside the section made the
        # midpoint jump whenever a gate passed a strongly turning vertex: an
        # ellipse tip's 5.7 degrees moved from one patch to the next as the
        # gate moved by a fraction of a segment, and with it the split by
        # four vertices - which depended on where the point list started.
        site = diagram.sites[cell.site]
        offset = _turning_midpoint(site.curve.loop(), patch.wall_start, wall_length)
        if offset is None:
            return None
    else:
        offset = 0.5 * wall_length
    offset = min(max(offset, 0.1 * wall_length), 0.9 * wall_length)
    source = g2.sample_at_arclength(patch.wall, [offset])[0]
    ring_length = g2.total_length(patch.ring)
    if ring_length <= 0.0:
        return None
    local = float(
        g2.closest_on_polyline(patch.ring, np.asarray([source])).arclength[0]
    )
    local = min(max(local, 0.1 * ring_length), 0.9 * ring_length)
    branch, position = cell.branch_station(patch.ring_start + local)
    site = diagram.sites[cell.site]
    wall_station = (patch.wall_start + offset) % site.curve.length()
    return Anchor(branch, position, "turning", None, (cell.site, wall_station))


@dataclass
class Layout:
    diagram: Diagram
    cells: list[Cell]
    anchors: "AnchorSet"
    cuts: list[list[Cut]]
    patches: list[Patch]
    notes: list[str] = field(default_factory=list)
    # The wall turning one patch may span, as the layout enforced it; the
    # relaxation keeps it (``patch_solver``), or sliding the gates undoes the
    # cuts that turning made.
    wall_turning_limit: float | None = None

    def refresh(self) -> None:
        """Recompute ring geometry and patches from the current variables.

        Gate stations and anchor positions are the optimiser's design
        variables, so this keeps them and rebuilds everything derived.
        """
        for cell_index, cell_cuts in enumerate(self.cuts):
            cell = self.cells[cell_index]
            site = self.diagram.sites[cell.site]
            total = site.curve.length()
            for cut in cell_cuts:
                cut.ring_station = cell.station_of(
                    cut.anchor.branch, cut.anchor.position
                )
                cut.ring_point = g2.sample_at_arclength(
                    cell.ring, [cut.ring_station]
                )[0]
                cut.wall_station %= total
                cut.wall_point = site.curve.point_at(cut.wall_station)
            cell_cuts.sort(key=lambda item: item.ring_station)
        self.patches = build_patches(self.diagram, self.cells, self.cuts)

    def cuts_of(self, anchor_key: tuple) -> list[Cut]:
        return [
            cut
            for cell_cuts in self.cuts
            for cut in cell_cuts
            if cut.anchor.key == anchor_key
        ]

    def patches_of_cut(self, cell_index: int, position: int) -> list[Patch]:
        return [
            patch
            for patch in self.patches
            if patch.cell == cell_index
            and position in (patch.first_cut, patch.second_cut)
        ]


@dataclass(frozen=True)
class LayoutOptions:
    max_wall_turning: float = math.radians(100.0)
    max_ring_turning: float = math.radians(150.0)
    # The requested band height, when known: a patch whose band block - the
    # straight quad of its two gates and their normal offsets at that height -
    # has a corner below ``band_corner`` is cut, because the front repair would
    # otherwise thin the band to nothing there (a nose turning 98 degrees in
    # one block gave 7-degree corners at 0.15 chord).
    band_height: float | None = None
    band_corner: float = math.radians(30.0)
    band_clearance_fraction: float = 0.9
    # With a band, a patch's wall may turn at most this much: the band's
    # first cells lean by about half the turning inside the block, and the
    # wall-misalignment target is 25 degrees.  ``None`` leaves the plain
    # wall-turning limit; the pipeline sets 45 degrees for a C-grid, whose
    # far-field spokes need the finer sectors.
    max_band_turning: float | None = None
    # Judge the anchor floor against the wall gap as well as the clearance
    # (see ``AnchorSet``): for a producer whose core is not built on the ring.
    floor_by_wall_gap: bool = False
    # Anchor the outer boundary's chain breaks by the inward ray rather than
    # the closest ring point: for a hull that wraps the cluster closely, whose
    # pointed ends have a whole arc of stations closest to one ring point.
    outer_by_ray: bool = False
    # The spoke lengths at a patch's two gates may differ by at most this
    # factor.  Leaving a narrow gap the clearance grows quickly, and one
    # block spanning a spoke of a fiftieth of the body and one of half of
    # it has a band that is a sliver at one end and a slab at the other;
    # its graded cells invert.  Cutting until neighbouring spokes are within
    # the factor grades the gates out of the gap geometrically.  Four, not
    # three: a medial junction beside a gap between two circles has 3.8
    # times the gap's clearance, and a cut there was accepted or refused by
    # the raster's noise on the ring floor, which broke invariance under
    # rotation; a jump of four is the narrow-gap case this rule is for.
    max_clearance_ratio: float = 4.0
    ring_separation: float = 0.5
    gate_balance: float = 0.35
    corner_turn: float = math.radians(45.0)
    hard_corner_turn: float = math.radians(70.0)
    curvature_prominence: float = 2.5
    curvature_limit: int = 6
    minimum_cuts: int = 3
    max_refinements: int = 60


def bootstrap_anchors(
    diagram: Diagram, cells: list[Cell], anchors: "AnchorSet", cell_index: int
) -> bool:
    """Seed a cell that has no cut yet from its own wall parameterisation.

    The stations are equal arc-length fractions measured from the supplied
    point list's first point, which survives translation, rotation, uniform
    scaling and point-list reversal.
    """
    cell = cells[cell_index]
    total = diagram.sites[cell.site].curve.length()
    added = False
    for step in range(4):
        candidate = anchor_from_wall(diagram, cell, step * total / 4.0, "seed")
        if candidate is not None and anchors.add(candidate):
            added = True
    return added


def band_block_corner(diagram: Diagram, patch: Patch, height: float) -> float:
    """Smallest corner angle of the patch's band block at ``height``.

    The block is the straight quad of the two gates and their fluid-normal
    offsets, which is what the emitted block's corners are; the curved wall
    between them does not rescue a 7-degree corner.
    """
    site = diagram.sites[patch.site]
    if site.curve.kind != "wall" or len(patch.wall) < 2:
        return math.pi
    first = patch.wall[0]
    second = patch.wall[-1]
    normal_first = site.curve.fluid_normal(patch.wall_start)
    normal_second = site.curve.fluid_normal(patch.wall_end)
    quad = np.asarray([first, second, second + height * normal_second, first + height * normal_first])
    if abs(g2.signed_area(np.vstack([quad, quad[:1]]))) <= 0.0:
        return 0.0
    return float(np.min(g2.quad_corner_angles(quad)))


def build_layout(diagram: Diagram, options: LayoutOptions | None = None) -> Layout:
    settings = options or LayoutOptions()
    cells = build_cells(diagram)
    anchors = AnchorSet(
        diagram,
        cells,
        scale=diagram.scale,
        ring_separation=settings.ring_separation,
        gate_balance=settings.gate_balance,
        floor_by_wall_gap=settings.floor_by_wall_gap,
    )
    notes: list[str] = []
    for anchor in junction_anchors(diagram):
        anchors.add(anchor, force=True)
    # A named boundary chain ends here and another begins: a block edge has
    # one patch name, so this is a gate whatever the geometry does.
    for anchor in chain_anchors(diagram, cells, outer_ray=settings.outer_by_ray):
        anchors.add(anchor, force=True)
    # A reflex corner is where the level set of the wall distance has its
    # mitre; the band spoke has to run along its bisector, so it is always a
    # gate.
    for anchor in reflex_anchors(diagram, cells):
        anchors.add(anchor, force=True)
    # A wall vertex sharp enough to give the fluid more than 250 degrees must
    # be a block corner: no patch whose wall section runs through it can be a
    # quadrilateral at all, so it outranks the separation rule.
    for anchor in corner_anchors(
        diagram, cells, minimum_turn=settings.hard_corner_turn
    ):
        anchors.add(anchor, force=True)
    for anchor in corner_anchors(
        diagram, cells, minimum_turn=settings.corner_turn
    ):
        anchors.add(anchor)
    for anchor in curvature_anchors(
        diagram,
        cells,
        scale=diagram.scale,
        prominence=settings.curvature_prominence,
        limit=settings.curvature_limit,
        corner_turn=settings.corner_turn,
    ):
        anchors.add(anchor)
    cuts = anchors.cuts()
    for cell_index, cell_cuts in enumerate(cuts):
        if len(cell_cuts) < 2:
            bootstrap_anchors(diagram, cells, anchors, cell_index)
    cuts = anchors.cuts()
    patches = build_patches(diagram, cells, cuts)
    unsplittable: set[tuple] = set()
    for _ in range(settings.max_refinements):
        short = {
            index
            for index, cell_cuts in enumerate(cuts)
            if len(cell_cuts) < settings.minimum_cuts
        }
        def band_defect(patch: Patch) -> float:
            if settings.band_height is None:
                return 0.0
            # A block touching a sharp feature is the seam's, the wake's or
            # the cavity stage's business; its one-sided offsets are not a
            # band block a cut could improve.
            ends = (cuts[patch.cell][patch.first_cut], cuts[patch.cell][patch.second_cut])
            if any(cut.anchor.kind in ("corner", "reflex", "wake") for cut in ends):
                return 0.0
            # The band is clearance-limited: judge the block at the height
            # the front can actually reach here, not at the bare request.
            height = min(settings.band_height, settings.band_clearance_fraction * min(cut.clearance for cut in ends))
            corner = band_block_corner(diagram, patch, height)
            return max(0.0, settings.band_corner - corner)

        turning_limit = (
            min(settings.max_wall_turning, settings.max_band_turning)
            if settings.band_height is not None and settings.max_band_turning is not None
            else settings.max_wall_turning
        )

        def wall_limit_of(patch: Patch) -> float:
            site = diagram.sites[patch.site]
            return turning_limit if site.curve.kind == "wall" else settings.max_wall_turning

        def clearance_jump(patch: Patch) -> float:
            if diagram.sites[patch.site].curve.kind != "wall":
                return 1.0
            ends = (cuts[patch.cell][patch.first_cut], cuts[patch.cell][patch.second_cut])
            low = min(cut.clearance for cut in ends)
            high = max(cut.clearance for cut in ends)
            return high / low if low > 0.0 else 1.0

        pending = [
            patch
            for patch in patches
            if patch.cell in short
            or patch.wall_turning() > wall_limit_of(patch)
            or patch.ring_turning() > settings.max_ring_turning
            or band_defect(patch) > 0.0
            or clearance_jump(patch) > settings.max_clearance_ratio
        ]
        if not pending:
            break
        pending.sort(
            key=lambda patch: (
                patch.cell not in short,
                -(patch.wall_turning() + 4.0 * band_defect(patch)),
            )
        )
        # A patch whose split was rejected in this pass is not tried again
        # until a split elsewhere has changed the cuts; the pass ends when a
        # split is added, the refinement when a whole pass adds nothing.
        added = False
        for patch in pending:
            if (patch.cell, patch.first_cut, patch.second_cut) in unsplittable:
                continue
            for by_turning in (True, False):
                candidate = split_anchor(
                    diagram, cells, patch, by_turning=by_turning
                )
                if candidate is not None and anchors.add(candidate):
                    added = True
                    break
            if added:
                unsplittable.clear()
                break
            unsplittable.add((patch.cell, patch.first_cut, patch.second_cut))
        if not added:
            notes.append(
                "patch turning could not be reduced further without crowding an "
                "existing cut"
            )
            break
        cuts = anchors.cuts()
        patches = build_patches(diagram, cells, cuts)
    for cell_index, cell_cuts in enumerate(cuts):
        if len(cell_cuts) < settings.minimum_cuts:
            raise LayoutError(
                f"cell {diagram.sites[cells[cell_index].site].name!r} has only "
                f"{len(cell_cuts)} cuts; an annulus needs at least "
                f"{settings.minimum_cuts}"
            )
    return Layout(diagram, cells, anchors, cuts, patches, notes, wall_turning_limit=turning_limit)
