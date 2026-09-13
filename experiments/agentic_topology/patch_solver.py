"""Optimise and repair the patch layout until every block is admissible.

Two moves are available and both come from geometry:

* *relaxation* slides ring anchors along their bisector branch and gate
  vertices along their wall.  Each vertex is one bound-constrained variable and
  the objective is scale free, so the same thresholds apply to any geometry at
  any size.  The objective combines an inverted-cell barrier and minimum corner
  angle (through the scaled Jacobian), an aspect-ratio term, a spoke-direction
  term that keeps a cut pointing into the fluid, and an interface-alignment
  term that keeps a spoke close to the shortest path to its anchor.
* *splitting* inserts one more anchor inside the worst remaining patch.  This
  is the only move that changes the discrete graph, so it is tried only after
  relaxation has converged: the search prefers the simplest valid layout, not
  the one with the most blocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
from block_layout import Cut, Layout, LayoutError, build_patches, split_anchor


@dataclass(frozen=True)
class SolverOptions:
    target_quality: float = math.sin(math.radians(25.0))
    accept_quality: float = math.sin(math.radians(2.0))
    split_quality: float = math.sin(math.radians(18.0))
    aspect_limit: float = 40.0
    aspect_weight: float = 0.05
    spoke_cosine: float = math.cos(math.radians(70.0))
    spoke_weight: float = 0.5
    alignment_weight: float = 0.02
    sweeps: int = 40
    margin: float = 0.06
    grid: int = 9
    refinements: int = 3
    max_splits: int = 40


@dataclass
class SolveResult:
    worst_quality: float = -1.0
    sweeps: int = 0
    splits: int = 0
    history: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        return not self.failures


def block_quality(corners) -> float:
    """Minimum scaled Jacobian of a quadrilateral; positive means convex.

    The measure is orientation agnostic - a patch traversed clockwise is as
    good as one traversed anticlockwise - but a self-intersecting corner order
    always scores negative because its corner cross products disagree in sign.
    """
    points = np.asarray(corners, dtype=np.float64)
    crosses = g2.quad_corner_crosses(points)
    sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    pairs = sides * np.roll(sides, -1)
    if float(np.min(pairs)) <= 0.0:
        return -1.0
    sign = 1.0 if g2.polygon_area(points) >= 0.0 else -1.0
    return float(np.min(sign * crosses / pairs))


def _sample(path: np.ndarray, cumulative: np.ndarray, station: float) -> np.ndarray:
    total = float(cumulative[-1])
    value = float(station) % total
    index = int(np.searchsorted(cumulative, value, side="right")) - 1
    index = min(max(index, 0), len(cumulative) - 2)
    span = cumulative[index + 1] - cumulative[index]
    local = (value - cumulative[index]) / span if span > 0.0 else 0.0
    return path[index] + local * (path[index + 1] - path[index])


class Evaluator:
    """Corner-only view of the layout used inside the relaxation loop."""

    def __init__(self, layout: Layout, options: SolverOptions):
        self.layout = layout
        self.options = options
        self.cells = layout.cells
        self.sites = layout.diagram.sites
        self._ring = [g2.cumulative_length(cell.ring) for cell in self.cells]

    def cut_points(self, cell_index: int, cut: Cut):
        cell = self.cells[cell_index]
        station = cell.station_of(cut.anchor.branch, cut.anchor.position)
        ring = _sample(cell.ring, self._ring[cell_index], station)
        curve = self.sites[cell.site].curve
        return curve.point_at(cut.wall_station), ring

    def block_corners(self, cell_index: int, position: int) -> np.ndarray:
        cuts = self.layout.cuts[cell_index]
        first = cuts[position]
        second = cuts[(position + 1) % len(cuts)]
        gate_first, ring_first = self.cut_points(cell_index, first)
        gate_second, ring_second = self.cut_points(cell_index, second)
        return np.asarray([gate_first, gate_second, ring_second, ring_first])

    def quality(self, cell_index: int, position: int) -> float:
        return block_quality(self.block_corners(cell_index, position))

    def block_penalty(self, cell_index: int, position: int) -> float:
        options = self.options
        corners = self.block_corners(cell_index, position)
        total = 0.0
        value = block_quality(corners)
        if value < options.target_quality:
            total += (options.target_quality - value) ** 2
        aspect = g2.quad_aspect_ratio(corners)
        if aspect > options.aspect_limit:
            total += options.aspect_weight * math.log(
                aspect / options.aspect_limit
            ) ** 2
        return total

    def cut_penalty(self, cell_index: int, position: int) -> float:
        """Terms that belong to one spoke rather than to a block."""
        options = self.options
        cell = self.cells[cell_index]
        curve = self.sites[cell.site].curve
        cut = self.layout.cuts[cell_index][position]
        gate, ring = self.cut_points(cell_index, cut)
        direction = ring - gate
        length = float(np.linalg.norm(direction))
        if length <= 0.0:
            return 1.0
        direction = direction / length
        normal = curve.fluid_normal(cut.wall_station)
        cosine = float(direction @ normal)
        total = 0.0
        if cosine < options.spoke_cosine:
            total += options.spoke_weight * (options.spoke_cosine - cosine) ** 2
        if cut.clearance > 0.0:
            total += options.alignment_weight * (length / cut.clearance - 1.0) ** 2
        return total

    def penalty(self, blocks, cuts) -> float:
        total = 0.0
        for cell_index, position in blocks:
            total += self.block_penalty(cell_index, position)
        for cell_index, position in cuts:
            total += self.cut_penalty(cell_index, position)
        return total

    def all_blocks(self):
        return [
            (cell_index, position)
            for cell_index, cell_cuts in enumerate(self.layout.cuts)
            for position in range(len(cell_cuts))
        ]

    def total_penalty(self) -> float:
        blocks = self.all_blocks()
        return self.penalty(blocks, blocks)

    def worst(self) -> tuple[float, tuple[int, int] | None]:
        best_value = math.inf
        best_block = None
        for cell_index, position in self.all_blocks():
            value = self.quality(cell_index, position)
            if value < best_value:
                best_value = value
                best_block = (cell_index, position)
        return best_value, best_block


# ---------------------------------------------------------------------------
# Design variables
# ---------------------------------------------------------------------------


@dataclass
class _Variable:
    kind: str
    apply: object
    read: object
    bounds: object
    blocks: tuple
    cuts: tuple


def _collect_variables(
    layout: Layout, margin: float, free_corner_gates: bool = False
) -> list[_Variable]:
    variables: list[_Variable] = []
    for cell_index, cuts in enumerate(layout.cuts):
        site = layout.diagram.sites[layout.cells[cell_index].site]
        total = site.curve.length()
        count = len(cuts)
        balance = layout.anchors.gate_balance
        for position, cut in enumerate(cuts):
            if cut.pinned and cut.anchor.kind == "corner" and not free_corner_gates:
                # A sharp wall corner should stay a block corner: an edge that
                # runs through the kink describes the boundary badly.  The
                # constraint is released only when no valid layout exists with
                # it in place.
                continue
            previous = cuts[(position - 1) % count]
            following = cuts[(position + 1) % count]
            variables.append(
                _Variable(
                    "gate",
                    _gate_setter(cut, total),
                    _gate_getter(cut),
                    _gate_bounds(
                        cut, previous, following, total, margin, balance, count
                    ),
                    ((cell_index, (position - 1) % count), (cell_index, position)),
                    ((cell_index, position),),
                )
            )
    seen: set[tuple] = set()
    for cell_index, cuts in enumerate(layout.cuts):
        for cut in cuts:
            anchor = cut.anchor
            if not anchor.movable or anchor.key in seen:
                continue
            seen.add(anchor.key)
            blocks: list[tuple[int, int]] = []
            touched: list[tuple[int, int]] = []
            for other_cell, other_cuts in enumerate(layout.cuts):
                for other_position, other_cut in enumerate(other_cuts):
                    if other_cut.anchor.key != anchor.key:
                        continue
                    blocks.append((other_cell, (other_position - 1) % len(other_cuts)))
                    blocks.append((other_cell, other_position))
                    touched.append((other_cell, other_position))
            bounds = _anchor_bounds(layout, anchor, margin)
            if bounds is None:
                continue
            variables.append(
                _Variable(
                    "anchor",
                    _anchor_setter(anchor),
                    _anchor_getter(anchor),
                    bounds,
                    tuple(blocks),
                    tuple(touched),
                )
            )
    return variables


def _gate_setter(cut: Cut, total: float):
    def apply(value: float) -> None:
        cut.wall_station = value % total

    return apply


def _gate_getter(cut: Cut):
    def read() -> float:
        return cut.wall_station

    return read


def _gate_bounds(
    cut: Cut,
    previous: Cut,
    following: Cut,
    total: float,
    margin: float,
    balance: float,
    count: int,
):
    """Bounds that keep a gate ordered and no closer than the local floor.

    The floor follows the local feature size, so a gate inside a narrow gap may
    sit close to its neighbour while one on an open wall may not.  Without it
    the relaxation happily trades block size for corner angle and produces
    slivers.
    """

    def bounds():
        span = (following.wall_station - previous.wall_station) % total
        if span <= 0.0:
            span = total
        first = balance * min(cut.clearance, previous.clearance, total / count)
        second = balance * min(cut.clearance, following.clearance, total / count)
        low = previous.wall_station + max(margin * span, min(first, 0.4 * span))
        high = (
            previous.wall_station
            + span
            - max(margin * span, min(second, 0.4 * span))
        )
        return low, high

    return bounds


def _anchor_setter(anchor):
    def apply(value: float) -> None:
        anchor.position = value

    return apply


def _anchor_getter(anchor):
    def read() -> float:
        return anchor.position

    return read


def _anchor_bounds(layout: Layout, anchor, margin: float):
    branch = layout.diagram.branches[anchor.branch]
    neighbours = layout.anchors.by_branch[anchor.branch]
    length = branch.length

    def bounds():
        ordered = sorted(neighbours, key=lambda item: item.position)
        index = next(
            (
                position
                for position, item in enumerate(ordered)
                if item is anchor
            ),
            None,
        )
        if index is None:
            return anchor.position, anchor.position
        if branch.closed:
            if len(ordered) < 3:
                return anchor.position, anchor.position
            low = ordered[index - 1].position
            span = (ordered[(index + 1) % len(ordered)].position - low) % length
        else:
            if index in (0, len(ordered) - 1):
                return anchor.position, anchor.position
            low = ordered[index - 1].position
            span = ordered[index + 1].position - low
        if span <= 0.0:
            return anchor.position, anchor.position
        start = low + margin * span
        return start, start + (1.0 - 2.0 * margin) * span

    probe = bounds()
    if probe[1] <= probe[0]:
        return None
    return bounds


# ---------------------------------------------------------------------------
# Relaxation
# ---------------------------------------------------------------------------


def snapshot(layout: Layout):
    """Capture every design variable so a failed attempt can be rolled back."""
    gates = [cut.wall_station for cuts in layout.cuts for cut in cuts]
    anchors = [
        anchor.position
        for anchors in layout.anchors.by_branch.values()
        for anchor in anchors
    ]
    return gates, anchors


def restore(layout: Layout, state) -> None:
    gates, anchors = state
    values = iter(gates)
    for cuts in layout.cuts:
        for cut in cuts:
            cut.wall_station = next(values)
    values = iter(anchors)
    for group in layout.anchors.by_branch.values():
        for anchor in group:
            anchor.position = next(values)


def relax(
    layout: Layout, options: SolverOptions, *, free_corner_gates: bool = False
) -> tuple[float, int]:
    evaluator = Evaluator(layout, options)
    variables = _collect_variables(layout, options.margin, free_corner_gates)
    previous_total = math.inf
    sweeps = 0
    for sweeps in range(1, options.sweeps + 1):
        for variable in variables:
            low, high = variable.bounds()
            if high <= low:
                continue
            best_position = variable.read()
            best_value = evaluator.penalty(variable.blocks, variable.cuts)
            window = (low, high)
            for _ in range(options.refinements):
                for candidate in np.linspace(window[0], window[1], options.grid):
                    variable.apply(float(candidate))
                    value = evaluator.penalty(variable.blocks, variable.cuts)
                    if value < best_value - 1e-15:
                        best_value = value
                        best_position = float(candidate)
                width = (window[1] - window[0]) / (options.grid - 1)
                window = (
                    max(low, best_position - width),
                    min(high, best_position + width),
                )
                if window[1] <= window[0]:
                    break
            variable.apply(best_position)
        total = evaluator.total_penalty()
        if total <= 0.0 or abs(previous_total - total) <= 1e-10 * max(total, 1.0):
            break
        previous_total = total
    worst, _ = evaluator.worst()
    return worst, sweeps


# ---------------------------------------------------------------------------
# Full solve
# ---------------------------------------------------------------------------


def solve(layout: Layout, options: SolverOptions | None = None) -> SolveResult:
    settings = options or SolverOptions()
    result = SolveResult()
    for attempt in range(settings.max_splits + 1):
        worst, sweeps = relax(layout, settings)
        released = False
        if worst < settings.accept_quality:
            state = snapshot(layout)
            relaxed, extra = relax(layout, settings, free_corner_gates=True)
            sweeps += extra
            if relaxed > worst:
                worst = relaxed
                released = True
            else:
                restore(layout, state)
        layout.refresh()
        result.worst_quality = worst
        result.sweeps += sweeps
        result.history.append(
            {
                "attempt": attempt,
                "patches": len(layout.patches),
                "worst_scaled_jacobian": worst,
                "relaxation_sweeps": sweeps,
                "released_corner_gates": released,
            }
        )
        if worst >= settings.split_quality:
            return result
        if not _split_worst(layout, settings, result):
            return result
        result.splits += 1
    result.failures.append({"reason": "split budget exhausted"})
    return result


def _split_worst(layout: Layout, settings: SolverOptions, result: SolveResult) -> bool:
    evaluator = Evaluator(layout, settings)
    ranked = sorted(
        (
            (evaluator.quality(patch.cell, patch.first_cut), patch.index, patch)
            for patch in layout.patches
        ),
        key=lambda item: item[0],
    )
    for quality, _index, patch in ranked:
        if quality >= settings.split_quality:
            break
        for by_turning in (True, False):
            candidate = split_anchor(
                layout.diagram, layout.cells, patch, by_turning=by_turning
            )
            if candidate is None or not layout.anchors.add(candidate):
                continue
            try:
                layout.cuts = layout.anchors.cuts()
            except LayoutError as error:
                result.failures.append(
                    {
                        "patch": patch.index,
                        "site": layout.diagram.sites[patch.site].name,
                        "reason": f"split rejected by gate ordering: {error}",
                    }
                )
                return False
            layout.patches = build_patches(layout.diagram, layout.cells, layout.cuts)
            return True
        if quality < settings.accept_quality:
            result.failures.append(_failure_record(layout, patch, quality))
    return False


def _failure_record(layout: Layout, patch, quality: float) -> dict:
    cell_cuts = layout.cuts[patch.cell]
    first = cell_cuts[patch.first_cut]
    second = cell_cuts[patch.second_cut]
    return {
        "patch": patch.index,
        "site": layout.diagram.sites[patch.site].name,
        "scaled_jacobian": quality,
        "minimum_corner_angle_degrees": float(
            math.degrees(np.min(g2.quad_corner_angles(patch.corners)))
        ),
        "wall_length": g2.total_length(patch.wall),
        "ring_length": g2.total_length(patch.ring),
        "wall_turning_degrees": math.degrees(patch.wall_turning()),
        "ring_turning_degrees": math.degrees(patch.ring_turning()),
        "aspect_ratio": g2.quad_aspect_ratio(patch.corners),
        "wall_chain": [[float(x), float(y)] for x, y in patch.wall],
        "ring_chain": [[float(x), float(y)] for x, y in patch.ring],
        "spokes": [
            [
                [float(cut.wall_point[0]), float(cut.wall_point[1])],
                [float(cut.ring_point[0]), float(cut.ring_point[1])],
            ]
            for cut in (first, second)
        ],
        "reason": (
            "relaxation could not reach the acceptance quality and no further "
            "anchor could be inserted without crowding an existing cut"
        ),
    }


def split_patch(layout: Layout, patch, settings: SolverOptions | None = None) -> bool:
    """Insert one anchor inside a named patch and rebuild the cut structure.

    This is the targeted form of :func:`_split_worst`: another stage - the
    boundary-layer front, for instance - has measured that a specific patch is
    inadmissible and asks for it to be divided.
    """
    _settings = settings or SolverOptions()
    for force in (False, True):
        for by_turning in (True, False):
            candidate = split_anchor(
                layout.diagram, layout.cells, patch, by_turning=by_turning
            )
            if candidate is None or not layout.anchors.add(candidate, force=force):
                continue
            try:
                layout.cuts = layout.anchors.cuts()
            except LayoutError:
                return False
            layout.patches = build_patches(layout.diagram, layout.cells, layout.cuts)
            return True
    return False
