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


def curvature_anchors(
    diagram: Diagram,
    cells: list[Cell],
    *,
    scale: float,
    samples: int = 256,
    prominence: float = 2.5,
    limit: int = 6,
) -> list[Anchor]:
    """Wall curvature extrema projected outwards onto the cell ring.

    Curvature is measured on a uniform arc-length resampling so that a dense
    input point list does not turn discretisation noise into anchors.  Both the
    resampling density and the prominence threshold are relative, so the result
    follows translation, rotation, uniform scaling and point-list reversal.
    """
    anchors: list[Anchor] = []
    for cell in cells:
        site = diagram.sites[cell.site]
        if site.curve.kind != "wall":
            continue
        loop = site.curve.loop()
        uniform = g2.resample(loop, samples + 1)
        curvature = np.abs(g2.discrete_curvature(uniform))
        window = np.array([0.25, 0.5, 0.25])
        smoothed = sum(
            weight * np.roll(curvature, shift)
            for weight, shift in zip(window, (-1, 0, 1))
        )
        median = float(np.median(smoothed))
        if median <= 0.0:
            continue
        nodes = uniform[:-1]
        peaks = [
            index
            for index in range(len(nodes))
            if smoothed[index] >= prominence * median
            and smoothed[index] >= smoothed[index - 1]
            and smoothed[index] >= smoothed[(index + 1) % len(nodes)]
        ]
        peaks.sort(key=lambda index: -smoothed[index])
        stations = g2.cumulative_length(uniform)
        for index in peaks[:limit]:
            anchor = anchor_from_wall(
                diagram, cell, float(stations[index]), "curvature"
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
        fluid = math.pi + site.curve.fluid_sign * g2.turning_angles(loop, closed=True)
        stations = g2.cumulative_length(loop)[:-1]
        for index in np.nonzero(fluid < layer_module.REFLEX_FLUID_ANGLE)[0]:
            anchor = anchor_from_wall(
                diagram, cell, float(stations[index]), "reflex"
            )
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def chain_anchors(diagram: Diagram, cells: list[Cell]) -> list[Anchor]:
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
            anchor = anchor_from_wall(diagram, cell, float(station), "chain")
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def anchor_from_wall(
    diagram: Diagram, cell: Cell, wall_station: float, kind: str
) -> Anchor | None:
    """Project a wall station outwards onto the cell ring, keeping the gate."""
    site = diagram.sites[cell.site]
    point = site.curve.point_at(wall_station)
    best: tuple[float, int, float] | None = None
    for step in cell.steps:
        branch = diagram.branches[step.branch]
        result = g2.closest_on_polyline(branch.path, np.asarray([point]))
        distance = float(result.distance[0])
        if best is None or distance < best[0]:
            best = (distance, step.branch, float(result.arclength[0]))
    if best is None:
        return None
    return Anchor(best[1], best[2], kind, None, (cell.site, wall_station))


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
    ):
        self.diagram = diagram
        self.cells = cells
        self.scale = scale
        self.ring_separation = ring_separation
        self.gate_balance = gate_balance
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
            for other_ring, _wall, other_clearance in self._stations[cell_index]:
                ring_gap = _cyclic_gap(station, other_ring, cell.ring_length)
                if ring_gap <= 1e-9 * self.scale:
                    self.rejected.append((anchor, "coincides with an existing cut"))
                    return False
                # Separation follows the local feature size: inside a narrow gap
                # the bisector is short and neighbouring spokes may be close.
                limit = self.ring_separation * min(clearance, other_clearance)
                if not force and ring_gap < limit:
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
        order, unwrapped, winding = _unwrap_gates(cuts, total)
        if winding <= 1.0 + 1e-9:
            break
        # A pinned feature gate can contradict the ring order.  Give up the
        # weakest pin - the one entered through the longest jump - and retry.
        deltas = np.diff(np.asarray([unwrapped[0]] + unwrapped))
        candidates = [
            (float(deltas[position]), index)
            for position, index in enumerate(order)
            if cuts[index].pinned
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
        crowded = [
            position
            for position in range(count)
            if spans[position] < floors[position]
            and cuts[order[position]].pinned
            and cuts[order[(position + 1) % count]].pinned
        ]
        if not crowded:
            break
        # Two pinned feature gates cannot both stay when they crowd each other;
        # the weaker feature gives up its pin and is spread instead.
        worst = min(crowded, key=lambda position: spans[position])
        first = cuts[order[worst]]
        second = cuts[order[(worst + 1) % count]]
        weaker = (
            second
            if _pin_priority(second) <= _pin_priority(first)
            else first
        )
        weaker.unpin()
        order, unwrapped, _ = _unwrap_gates(cuts, total)
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


def _unwrap_gates(cuts: list[Cut], total: float):
    """Ring-ordered gate stations forced to be non-decreasing."""
    count = len(cuts)
    start = next((index for index, cut in enumerate(cuts) if cut.pinned), 0)
    order = [(start + offset) % count for offset in range(count)]
    unwrapped = [cuts[order[0]].wall_station]
    for index in order[1:]:
        unwrapped.append(
            unwrapped[-1] + (cuts[index].wall_station - unwrapped[-1]) % total
        )
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
    stations = g2.cumulative_length(patch.wall)
    if by_turning:
        turning = np.abs(g2.turning_angles(patch.wall, closed=False))
        if not len(turning) or float(np.sum(turning)) <= 0.0:
            return None
        cumulative = np.concatenate(([0.0], np.cumsum(turning)))
        index = int(np.searchsorted(cumulative, 0.5 * cumulative[-1])) + 1
        offset = float(stations[min(max(index, 1), len(patch.wall) - 2)])
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


def build_layout(diagram: Diagram, options: LayoutOptions | None = None) -> Layout:
    settings = options or LayoutOptions()
    cells = build_cells(diagram)
    anchors = AnchorSet(
        diagram,
        cells,
        scale=diagram.scale,
        ring_separation=settings.ring_separation,
        gate_balance=settings.gate_balance,
    )
    notes: list[str] = []
    for anchor in junction_anchors(diagram):
        anchors.add(anchor, force=True)
    # A named boundary chain ends here and another begins: a block edge has
    # one patch name, so this is a gate whatever the geometry does.
    for anchor in chain_anchors(diagram, cells):
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
    ):
        anchors.add(anchor)
    cuts = anchors.cuts()
    for cell_index, cell_cuts in enumerate(cuts):
        if len(cell_cuts) < 2:
            bootstrap_anchors(diagram, cells, anchors, cell_index)
    cuts = anchors.cuts()
    patches = build_patches(diagram, cells, cuts)
    for _ in range(settings.max_refinements):
        short = {
            index
            for index, cell_cuts in enumerate(cuts)
            if len(cell_cuts) < settings.minimum_cuts
        }
        pending = [
            patch
            for patch in patches
            if patch.cell in short
            or patch.wall_turning() > settings.max_wall_turning
            or patch.ring_turning() > settings.max_ring_turning
        ]
        if not pending:
            break
        pending.sort(key=lambda patch: (patch.cell not in short, -patch.wall_turning()))
        added = False
        for patch in pending:
            for by_turning in (True, False):
                candidate = split_anchor(
                    diagram, cells, patch, by_turning=by_turning
                )
                if candidate is not None and anchors.add(candidate):
                    added = True
                    break
            if added:
                break
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
    return Layout(diagram, cells, anchors, cuts, patches, notes)
