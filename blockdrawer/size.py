"""Set every cell count and grading of a session so cell sizes are continuous.

``size_model`` needs nothing but what a session has: the patches of type
``wall``, the geometry and the opposite-edge count components. It works in
the geometry's units and knows no construction roles.

1. **A directional size field.** Each wall segment asks, at distance ``d``,
   for ``first + (growth - 1) * d`` across it (along its normal, or along
   the line to its nearest point past an end) and for ``wall_aspect`` first
   widths along it, capped at ``core_size``; the size wanted along a
   direction ``u`` is the smallest any wall asks along ``u``. So the cells
   along a wall shrink towards a corner whose other wall's normal runs
   along it (the backward step's lip), and no edge has to be classified as
   wall-normal or tangential.
2. **Width bounds, finest first.** Every edge end gets the largest cell
   width it may carry: the field's size along the edge there, lowered by
   a Dijkstra-like propagation over two relations - across every block
   interface the two transverse edges at a vertex (the pairs the quality
   screen compares) may differ by at most ``interface_growth``, and along
   an edge the width may grow by at most ``growth - 1`` per unit length
   (a geometric series grows linearly in width with distance). Both
   relations only enlarge the value they pass on, so every end is settled
   once and the bounds are the gradation-limited field on the edge graph.
3. **Two-ended strips are split.** An edge whose wanted size (the field
   along it, capped from both ends by the gradation) peaks well inside
   cannot be served by one geometric series, and multi-section grading is
   outside BlockDrawer's scope: its strip is cut with ``split_edge`` at
   the peak, and the bounds are recomputed.
4. **Counts and series.** Each edge demands the integral of ``ds / size``
   along it; each component takes the largest (or the length-weighted
   mean) demand. Each edge then carries the one geometric series whose end
   widths have the ratio of its end bounds - its total ratio is the ratio
   of the bounds - and whatever surplus its count gives makes both ends
   finer by the same factor.
5. **A cell budget buys continuity.** The state so far is the field's own
   (a budget below ``DEFAULT_BUDGET`` times its cells coarsens the field to
   budget / ``DEFAULT_BUDGET`` first). A short edge given many cells by its
   component (a thin wake strip carrying the band's count) is much finer
   than its neighbours, which the field does not see. The rest of the
   budget (by default half the field's cells again) is spent greedily on
   the jump cost - over every interface pair the quality screen compares,
   the square of how far log(width ratio) exceeds log(``interface_growth``):
   free regrades of one strip first, then the component raise (its strips
   refitted) with the largest drop per cell, stopping at the first raise
   the budget cannot pay, so from the field's own state up the cost never
   rises with the budget. Every
   move is checked on the sampled grid of the blocks it touches; one that
   folds a cell, adds a face above 70 degrees or takes the skewness above
   4 is undone. Before spending, a block the field leaves above skewness 4
   or with faces above 70 degrees is repaired by the same moves; strips the
   spent state leaves coarse between finer neighbours at both ends are
   split and the budget is spent again when that lowers the jumps.

Counts and gradings are written directly: spacing links are kept and the
ones left out of sync are reported. Everything is deterministic and applied
as one validated edit; on any failure the model is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from typing import Any, Sequence

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .session import from_data, to_data


# A strip is two-ended when the size it wants inside exceeds the size at
# both its ends by this factor (measured in the README).
SPLIT_RATIO = 2.5
SPLIT_ROUNDS = 8
# Without a budget, continuity may spend this multiple of the cells the
# field alone asks for (measured in the README).
DEFAULT_BUDGET = 1.5
# A count raise adds this fraction of the count (at least one cell).
RAISE_STEP = 0.1
# Regrades try the strip's log ratio moved by these multiples of log(cap).
REGRADE_STEPS = (-1.0, -0.5, 0.5, 1.0)
# checkMesh fails a mesh above this face skewness; the sampled guard's resolution.
SKEWNESS_LIMIT = 4.0
GUARD_CELLS = 64
# No strip is graded beyond this log total ratio (a refit at hundreds of
# cells asked for exp(800)).
MAX_LOG_RATIO = 50.0
# Repairs of a block the field leaves above skewness 4 or 70 degrees.
SHAPE_REPAIRS = 30
# Rounds of splitting strips the spent budget leaves coarse at both ends.
CONTINUITY_SPLIT_ROUNDS = 3
# Strips split per sizing pass before the sizing is solved again.
SPLITS_PER_ROUND = 8
# Whether opposite sides share one grading along their strip.
STRIP_SHARE = True
# Opposite sides of a block share one grading only within this length ratio.
OPPOSITE_LENGTH_RATIO = 2.0
# The count a strip is given before it is split, so the cut lands close to
# the wanted fraction (split_edge snaps to a mesh node).
SPLIT_RESOLUTION = 1000
# Samples per edge for the demand integral, at most.
MAX_SAMPLES = 200


@dataclass(frozen=True)
class SizeOptions:
    first_width: float
    core_size: float
    growth: float = 1.2
    wall_aspect: float | None = None
    interface_growth: float | None = 1.25
    count_rule: str = "max"
    budget: int | None = None
    patch_first_widths: tuple[tuple[str, float], ...] = ()
    split: bool = True
    minimum_cells: int = 1
    maximum_cells: int = 2000
    # Keep every count and recompute the gradings from the current
    # geometry - after a smoothing changed the edge lengths.
    keep_counts: bool = False

    def check(self, model: MeshModel) -> None:
        if not (self.first_width > 0.0 and self.core_size >= self.first_width):
            raise TopologyError("size needs 0 < first_width <= core_size")
        if self.growth <= 1.0:
            raise TopologyError("size needs growth > 1")
        if self.interface_growth is not None and self.interface_growth <= 1.0:
            raise TopologyError("size needs interface_growth > 1")
        if self.wall_aspect is not None and self.wall_aspect < 1.0:
            raise TopologyError("size needs wall_aspect >= 1")
        if self.count_rule not in ("max", "mean"):
            raise TopologyError("count_rule must be max or mean")
        for name, width in self.patch_first_widths:
            if name not in model.boundaries:
                raise TopologyError(f"Unknown patch {name!r} in a first-width override")
            if not (0.0 < width <= self.core_size):
                raise TopologyError(f"first width for {name} must be in (0, core_size]")


@dataclass
class SizeResult:
    total_cells: int
    cells_before: int
    blocks: int
    components: int
    budget: int | None = None
    moves: list = field(default_factory=list)
    guard: str = ""
    field_cells: int = 0
    field_jump_cost: float = 0.0
    jump_cost: float = 0.0
    splits: list[dict] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    jumps: dict[str, Any] = field(default_factory=dict)
    worst_jumps: list[dict] = field(default_factory=list)
    bands: list[dict] = field(default_factory=list)
    worst_interface: float = 1.0
    worst_aspect: float = 1.0
    unsynchronized_links: int = 0
    budget_factor: float = 1.0
    note: str = ""

    def to_data(self) -> dict[str, Any]:
        return {
            "total_cells": self.total_cells,
            "cells_before": self.cells_before,
            "blocks": self.blocks,
            "components": self.components,
            "budget": self.budget,
            "field_cells": self.field_cells,
            "field_jump_cost": self.field_jump_cost,
            "jump_cost": self.jump_cost,
            "moves": len(self.moves),
            "shape_repairs": sum(1 for move in self.moves if move["kind"] == "shape"),
            "count_raises": sum(1 for move in self.moves if move["kind"] == "raise"),
            "regrades": sum(1 for move in self.moves if move["kind"] == "regrade"),
            "guard": self.guard,
            "splits": self.splits,
            "refused": self.refused,
            "interface_jumps": self.jumps,
            "worst_interfaces": self.worst_jumps,
            "bands": self.bands,
            "worst_interface_size_ratio": self.worst_interface,
            "worst_cell_aspect_ratio": self.worst_aspect,
            "unsynchronized_spacing_links": self.unsynchronized_links,
            "budget_factor": self.budget_factor,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# The directional size field
# ---------------------------------------------------------------------------


class SizeField:
    """The cell size the walls ask for at a point along a direction."""

    def __init__(self, model: MeshModel, options: SizeOptions) -> None:
        self.growth = options.growth
        self.core = options.core_size
        self.aspect = options.wall_aspect
        overrides = dict(options.patch_first_widths)
        walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
        segments: list[tuple[float, float, float, float, float]] = []
        for current, name in sorted(model.edge_boundaries.items()):
            if name not in walls:
                continue
            first = overrides.get(name, options.first_width)
            points = _path(model, current, 32)
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                if (ax, ay) != (bx, by):
                    segments.append((ax, ay, bx, by, first))
        self.segments = segments
        self.minimum_first = min((item[4] for item in segments), default=options.first_width)
        self.reach = (self.core - self.minimum_first) / (self.growth - 1.0)
        self.buckets: dict[tuple[int, int], list[int]] = {}
        self.bucket = 1.0
        if not segments:
            return
        lengths = sorted(math.hypot(s[2] - s[0], s[3] - s[1]) for s in segments)
        median = lengths[len(lengths) // 2] or 1.0
        self.bucket = max(2.0 * median, self.reach / 24.0)
        for index, (ax, ay, bx, by, _first) in enumerate(segments):
            for i in range(math.floor(min(ax, bx) / self.bucket), math.floor(max(ax, bx) / self.bucket) + 1):
                for j in range(math.floor(min(ay, by) / self.bucket), math.floor(max(ay, by) / self.bucket) + 1):
                    self.buckets.setdefault((i, j), []).append(index)

    @property
    def has_walls(self) -> bool:
        return bool(self.segments)

    def size(self, x: float, y: float, ux: float, uy: float) -> float:
        """The smallest size any wall asks for at (x, y) along the unit direction u."""
        if not self.segments:
            return self.core
        i0 = math.floor(x / self.bucket)
        j0 = math.floor(y / self.bucket)
        slope = self.growth - 1.0
        best = self.core
        rings = int(self.reach / self.bucket) + 2
        for ring in range(rings + 1):
            bound = max(0.0, (ring - 1) * self.bucket)
            if self.minimum_first + slope * bound >= best:
                break
            for i in range(i0 - ring, i0 + ring + 1):
                for j in (range(j0 - ring, j0 + ring + 1) if abs(i - i0) == ring else (j0 - ring, j0 + ring)):
                    for index in self.buckets.get((i, j), ()):
                        value = self._segment_size(index, x, y, ux, uy, slope)
                        if value < best:
                            best = value
                if ring == 0:
                    break
        return best

    def _segment_size(self, index: int, x: float, y: float, ux: float, uy: float, slope: float) -> float:
        ax, ay, bx, by, first = self.segments[index]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = ((x - ax) * dx + (y - ay) * dy) / length2
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        px, py = ax + t * dx, ay + t * dy
        ox, oy = x - px, y - py
        distance = math.hypot(ox, oy)
        across = first + slope * distance
        if across >= self.core:
            return self.core
        # Across the wall is along its segment's normal, also past the
        # segment's end: there a point beyond a convex corner sees the other
        # wall's normal (the backward step's lip grades the upstream wall
        # towards it). The line to the nearest point instead turned along
        # the wall itself past every vertex of a polyline wall and asked
        # for first-width cells along the whole 30P30N main.
        norm = math.sqrt(length2)
        nx, ny = -dy / norm, dx / norm
        # Along the wall the cells keep the wall aspect as they grow away
        # from it, up to the core size: an isotropic layer put 0.055 cells
        # streamwise in the middle of the backward step's channel, where the
        # hand mesh carried 0.3.
        along = across if self.aspect is None else min(self.core, self.aspect * across)
        c = ux * nx + uy * ny
        s = ux * ny - uy * nx
        return 1.0 / math.sqrt((c / across) ** 2 + (s / along) ** 2)


def _path(model: MeshModel, current: EdgeKey, minimum: int) -> list[tuple[float, float]]:
    geometry = model.edge_geometry.get(current)
    count = 1 if geometry is None else max(minimum, 4 * len(geometry.points))
    fractions = tuple(index / count for index in range(count + 1))
    return list(model.edge_points(current, fractions))


# ---------------------------------------------------------------------------
# Per-edge geometry and the bounds
# ---------------------------------------------------------------------------


@dataclass
class _Edge:
    key: EdgeKey
    length: float
    arc: list[float]              # cumulative length at each sample
    field: list[float]            # the field's size along the edge at each sample


def _sample_edge(model: MeshModel, current: EdgeKey, field: SizeField, options: SizeOptions) -> _Edge:
    length = model.edge_length(current)
    samples = max(8, min(MAX_SAMPLES, math.ceil(length / (0.25 * options.core_size))))
    geometry = model.edge_geometry.get(current)
    if geometry is not None:
        samples = max(samples, min(MAX_SAMPLES, 4 * len(geometry.points)))
    fractions = tuple(index / samples for index in range(samples + 1))
    points = model.edge_points(current, fractions)
    arc = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        arc.append(arc[-1] + math.hypot(x1 - x0, y1 - y0))
    sizes = []
    for index, (x, y) in enumerate(points):
        a = points[max(0, index - 1)]
        b = points[min(len(points) - 1, index + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(tx, ty) or 1.0
        sizes.append(field.size(x, y, tx / norm, ty / norm))
    return _Edge(current, arc[-1] or length, arc, sizes)


def _profile(edge: _Edge, start: float, end: float, slope: float, core: float) -> list[float]:
    """The size wanted along an edge: the field, capped by the gradation from both end bounds."""
    total = edge.arc[-1]
    return [
        min(value, start + slope * s, end + slope * (total - s), core)
        for value, s in zip(edge.field, edge.arc)
    ]


def _demand(edge: _Edge, profile: list[float]) -> float:
    """The integral of ds / size, exact for a size linear on each piece."""
    demand = 0.0
    for s0, s1, h0, h1 in zip(edge.arc, edge.arc[1:], profile, profile[1:]):
        piece = s1 - s0
        if abs(h1 - h0) <= 1.0e-12 * max(h0, h1):
            demand += piece / h0
        else:
            demand += piece * math.log(h1 / h0) / (h1 - h0)
    return demand


def _links(model: MeshModel) -> dict[tuple[EdgeKey, str], list[tuple[EdgeKey, str]]]:
    """Across every internal edge, at each of its ends, the two blocks'
    transverse edges: the pairs the quality screen's size jump compares."""
    occurrences = model.edge_occurrences()
    links: dict[tuple[EdgeKey, str], list[tuple[EdgeKey, str]]] = {}
    for shared, used in sorted(occurrences.items()):
        if len(used) != 2:
            continue
        for vertex in shared:
            transverse = []
            for block, _index, _directed in used:
                for index in range(4):
                    candidate = edge_key(*block.directed_edge(index))
                    if candidate != shared and vertex in candidate:
                        transverse.append(candidate)
                        break
            if len(transverse) == 2 and transverse[0] != transverse[1]:
                first, second = (transverse[0], vertex), (transverse[1], vertex)
                links.setdefault(first, []).append(second)
                links.setdefault(second, []).append(first)
    return links


def _bounds(
    edges: dict[EdgeKey, _Edge],
    seeds: dict[tuple[EdgeKey, str], float],
    links: dict,
    options: SizeOptions,
) -> dict[tuple[EdgeKey, str], float]:
    """The largest width every edge end may carry, finest first.

    Relaxations only enlarge what they pass on (by ``interface_growth``
    across an interface, by ``growth - 1`` per unit length along an edge),
    so the first time an end is popped its value is final.
    """
    cap = options.interface_growth
    slope = options.growth - 1.0
    settled: dict[tuple[EdgeKey, str], float] = {}
    queue = [(value, key[0], key[1]) for key, value in seeds.items()]
    heapq.heapify(queue)
    while queue:
        value, current, vertex = heapq.heappop(queue)
        key = (current, vertex)
        if key in settled:
            continue
        settled[key] = value
        other = current[1] if current[0] == vertex else current[0]
        along = value + slope * edges[current].length
        if (current, other) not in settled and along < seeds.get((current, other), math.inf):
            heapq.heappush(queue, (along, current, other))
        if cap is not None:
            for linked in links.get(key, ()):
                if linked not in settled:
                    heapq.heappush(queue, (value * cap, linked[0], linked[1]))
    return settled


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------


def series_ratio(first: float, length: float, cells: int) -> float:
    """Cell-to-cell ratio of ``cells`` geometric cells from ``first`` filling ``length``."""
    if cells < 2 or first <= 0.0 or length <= 0.0:
        return 1.0
    uniform = length / cells
    if abs(first - uniform) <= 1.0e-12 * uniform:
        return 1.0
    growing = first < uniform

    def total(q: float) -> float:
        if abs(q - 1.0) < 1.0e-12:
            return first * cells
        if cells * math.log(q) > 700.0:
            return math.inf
        return first * (q ** cells - 1.0) / (q - 1.0)

    low, high = (1.0, 2.0) if growing else (0.5, 1.0)
    if growing:
        while total(high) < length and high < 1.0e6:
            high *= 2.0
    else:
        while total(low) > length and low > 1.0e-6:
            low *= 0.5
    for _ in range(100):
        middle = 0.5 * (low + high)
        # The filled length grows with the ratio whichever way the series runs.
        if total(middle) < length:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def _end_widths(length: float, cells: int, total: float) -> tuple[float, float]:
    """First and last cell widths of ``cells`` geometric cells of total ratio ``total``."""
    if cells < 2 or abs(total - 1.0) < 1.0e-12:
        width = length / max(cells, 1)
        return width, width
    ratio = total ** (1.0 / (cells - 1))
    first = length * (ratio - 1.0) / (ratio ** cells - 1.0)
    return first, first * total


def _components(model: MeshModel) -> list[list[EdgeKey]]:
    seen: set[EdgeKey] = set()
    found = []
    for current in sorted(model.edge_cells):
        if current in seen:
            continue
        members = sorted(model.edge_constraint_component(current))
        seen.update(members)
        found.append(members)
    return found


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def size_model(
    model: MeshModel,
    options: SizeOptions,
    *,
    blocks: Sequence[str] | None = None,
    bbox: Sequence[float] | None = None,
) -> SizeResult:
    """Size ``model`` in place; see the module docstring."""
    options.check(model)
    model.validate()
    cells_before = _total_cells(model)
    working = from_data(to_data(model))
    field_ = SizeField(working, options)
    note = "" if field_.has_walls else (
        "no patch of type wall: every cell is sized at core_size; set a patch's "
        "type to wall to grade towards it"
    )
    splits: list[dict] = []
    refused: list[str] = []
    state = _solve(working, field_, options, blocks, bbox)
    if options.split and not options.keep_counts:
        for _attempt in range(SPLIT_ROUNDS):
            if not state["two_ended"]:
                break
            saved = from_data(to_data(working))
            made = len(splits)
            if not _split_strips(working, field_, state, options, splits, refused):
                break
            trial = _solve(working, field_, options, blocks, bbox)
            # A split must not leave a worse interface than the one it was
            # meant to remove: the round is undone when the jump cost rises.
            if _jump_cost(trial, options) > _jump_cost(state, options) * (1.0 + 1.0e-9):
                working.__dict__.update(saved.__dict__)
                for item in splits[made:]:
                    refused.append(
                        f"split of {item['edge']} undone: it raised the interface jump cost"
                    )
                del splits[made:]
                break
            state = trial
    moves: list[dict] = []
    guard = ""
    budget = None
    field_cells = _state_cells(working, state)
    field_cost = _jump_cost(state, options)
    skew_floor = _max_skewness(model)
    if not options.keep_counts:
        budget = options.budget if options.budget else int(round(DEFAULT_BUDGET * field_cells))
        state, moves, guard = _spend(working, state, options, budget, skew_floor)
        if options.split:
            state, moves, guard = _continuity_splits(
                working, field_, state, moves, guard, options, budget, blocks, bbox, splits, refused,
                skew_floor,
            )
    _write(working, state, options)
    skewness = _max_skewness(working)
    if skewness > SKEWNESS_LIMIT >= skew_floor:
        raise TopologyError(
            f"size refused: the sized mesh's sampled face skewness {skewness:.3g} exceeds "
            f"checkMesh's limit of {SKEWNESS_LIMIT:g} where the input's was {skew_floor:.3g}; "
            "the block it names is too tapered for the counts the field gives it - raise "
            "core_size's resolution there, split the block or keep the counts"
        )
    result = _report(working, cells_before, state["components"], splits, refused, state["factor"], note)
    result.budget = budget
    result.field_cells = field_cells
    result.field_jump_cost = field_cost
    result.jump_cost = _jump_cost(state, options)
    result.moves = moves
    result.guard = guard
    _replace(model, working)
    return result


def _write(working: MeshModel, state: dict, options: SizeOptions) -> None:
    counts, scope = state["counts"], state["scope"]
    for current, value in counts.items():
        working.edge_cells[current] = value
    ratios = state.get("ratios") or _ratios(state["strips"], state["edges"], state["bounds"], options)
    for current in scope:
        total = ratios[current]
        if counts[current] < 2 or abs(total - 1.0) <= 1.0e-9:
            working.edge_grading.pop(current, None)
        else:
            working.edge_grading[current] = total
    working.validate()


def _solve(working, field_, options, blocks, bbox) -> dict:
    """Counts, bounds and strip ratios for the model as it is: the field's
    bounds, the counts their profiles and strips need (scaled to a budget
    smaller than that), and the strips that are fine at both ends."""
    scope = _scope_edges(working, blocks, bbox)
    edges = {current: _sample_edge(working, current, field_, options) for current in working.edges()}
    links = _links(working)
    strips = _strips(working, edges)
    components = _components(working)
    sized = [members for members in components if any(member in scope for member in members)]
    seeds = _field_seeds(edges)
    bounds = _bounds(edges, seeds, links, options)
    slope = options.growth - 1.0
    counts: dict[EdgeKey, int] = dict(working.edge_cells)
    factor = 1.0
    two_ended: dict[EdgeKey, tuple[float, float]] = {}
    if not options.keep_counts:
        ratios = _ratios(strips, edges, bounds, options)
        targets = _targets(edges, bounds, sized, slope, options, _strip_targets(strips, edges, bounds, ratios, options), two_ended)
        factor = _budget_factor(working, sized, targets, options)
        for members, target in zip(sized, targets):
            value = int(min(options.maximum_cells, max(options.minimum_cells, math.ceil(target * factor - 1.0e-9))))
            for member in members:
                counts[member] = value
    return {
        "counts": counts, "bounds": bounds, "edges": edges, "strips": strips, "scope": scope,
        "components": len(sized), "factor": factor, "links": links, "seeds": seeds,
        "ratios": _ratios(strips, edges, bounds, options),
        "two_ended": {key: value for key, value in two_ended.items() if key in scope},
    }


def _field_seeds(edges: dict[EdgeKey, _Edge]) -> dict[tuple[EdgeKey, str], float]:
    return {
        **{(current, current[0]): item.field[0] for current, item in edges.items()},
        **{(current, current[1]): item.field[-1] for current, item in edges.items()},
    }


def _fit_count(length: float, total: float, start: float, end: float, limit: int, slack: float = 1.0) -> int:
    """The fewest cells whose series of total ratio ``total`` stays within both end bounds."""
    def fits(cells: int) -> bool:
        first, last = _end_widths(length, cells, total)
        return first <= start * slack * (1.0 + 1.0e-9) and last <= end * slack * (1.0 + 1.0e-9)

    if fits(1):
        return 1
    low, high = 1, 2
    while not fits(high) and high < limit:
        low, high = high, min(limit, 2 * high)
    if not fits(high):
        return limit
    while high - low > 1:
        middle = (low + high) // 2
        if fits(middle):
            high = middle
        else:
            low = middle
    return high


def _targets(edges, bounds, components, slope, options, needed, two_ended=None, own=None) -> list[float]:
    """Each component's count: the demand of its edges' profiles, and enough
    cells for each edge's shared series to stay within its end bounds (the
    shared ratio can be less extreme than an edge's own, which left the
    backward step's lip at twice its bound)."""
    targets = []
    for members in components:
        demands = []
        weights = []
        for member in members:
            item = edges[member]
            start, end = bounds[(member, member[0])], bounds[(member, member[1])]
            profile = _profile(item, start, end, slope, options.core_size)
            demand = _demand(item, profile)
            ends = max(profile[0], profile[-1])
            peak_index = max(
                range(len(profile)),
                key=lambda index: (profile[index], -abs(item.arc[index] - 0.5 * item.arc[-1])),
            )
            fraction = item.arc[peak_index] / item.arc[-1]
            # An edge only its own count made fine at both ends is not
            # two-ended: splitting it leaves each half the same count (the
            # O-grid's outer side, tied to the wall's 100 cells, was cut 28
            # times). Its ends must be finer than its own cells are.
            reached = own is None or (
                start < own[(member, member[0])] * (1.0 - 1.0e-6)
                and end < own[(member, member[1])] * (1.0 - 1.0e-6)
            )
            if reached and profile[peak_index] >= SPLIT_RATIO * ends and 0.05 < fraction < 0.95:
                # Fine at both ends: one series cannot serve it, its strip
                # is split; meanwhile it asks only what its profile asks,
                # since a shared series fine at both ends is a uniform one
                # (481 cells on a 1.2-long grooved-tandem edge).
                if two_ended is not None:
                    two_ended[member] = (fraction, profile[peak_index] / ends)
            elif options.count_rule == "max":
                demand = max(demand, needed[member])
            demands.append(demand)
            weights.append(item.length)
        if options.count_rule == "max":
            targets.append(max(demands))
        else:
            total = sum(weights)
            targets.append(sum(w * d for w, d in zip(weights, demands)) / total if total > 0.0 else 1.0)
    return targets


def _strips(model, edges) -> list[dict[EdgeKey, str]]:
    """Strips of edges that must share one grading, each edge with the
    vertex its series starts from.

    Two opposite sides of a block graded differently shear every cell
    between them - a rectangle of the backward step read 75 degrees with
    each side graded from its own bounds. Opposite sides within
    ``OPPOSITE_LENGTH_RATIO`` are grouped through every block they share
    and oriented through the blocks (a side's end maps to the corner it
    faces). Opposite sides further apart keep their own: one side's
    fractions on a side five times shorter put first cells of 2e-6 on a
    30P30N wall in the research engine.
    """
    pairs: dict[tuple[EdgeKey, str], list[tuple[EdgeKey, str]]] = {}
    for block in model.blocks:
        keys = [edge_key(*block.directed_edge(index)) for index in range(4)]
        corners = block.vertices
        for k in range(2):
            first, second = keys[k], keys[k + 2]
            a, b = edges[first].length, edges[second].length
            if max(a, b) > OPPOSITE_LENGTH_RATIO * min(a, b):
                continue
            for here, there in ((corners[k], corners[(k + 3) % 4]),
                                (corners[(k + 1) % 4], corners[(k + 2) % 4])):
                pairs.setdefault((first, here), []).append((second, there))
                pairs.setdefault((second, there), []).append((first, here))
    strips = []
    seen: set[EdgeKey] = set()
    for seed in sorted(edges):
        if seed in seen:
            continue
        orientation = {seed: seed[0]}
        stack = [seed]
        while stack:
            current = stack.pop()
            begin = orientation[current]
            end = current[0] if current[1] == begin else current[1]
            for vertex, keeps in ((begin, True), (end, False)):
                for other, other_vertex in pairs.get((current, vertex), ()):
                    if other in orientation:
                        continue
                    other_end = other[0] if other[1] == other_vertex else other[1]
                    orientation[other] = other_vertex if keeps else other_end
                    stack.append(other)
        seen.update(orientation)
        strips.append(orientation)
    return strips


def _ratios(strips, edges, bounds, options) -> dict[EdgeKey, float]:
    """One total ratio per strip: the one that lets the strip's edges stay
    within their end bounds with the fewest cells.

    The geometric mean of the members' own ratios left the backward step's
    inlet column 0.035 at the far end and needed 313 cells to stay fine at
    the lip; the hand mesh graded the whole column from 0.005 to 0.25 in
    79. The ratio is searched over the members' own ratios and an even
    grid between them.
    """
    slack = 1.0
    ratios: dict[EdgeKey, float] = {}
    if not STRIP_SHARE:
        for orientation in strips:
            for current in orientation:
                ratios[current] = bounds[(current, current[1])] / bounds[(current, current[0])]
        return ratios
    for orientation in strips:
        oriented = []
        for current, begin in orientation.items():
            end = current[0] if current[1] == begin else current[1]
            oriented.append((current, begin, bounds[(current, begin)], bounds[(current, end)]))
        logs = sorted({math.log(far / near) for _current, _begin, near, far in oriented})
        if len(logs) == 1:
            best = logs[0]
        else:
            grid = set(logs)
            for step in range(1, 16):
                grid.add(logs[0] + (logs[-1] - logs[0]) * step / 16.0)
            best, best_cells = None, None
            for value in sorted(grid):
                cells = max(
                    _fit_count(edges[current].length, math.exp(value), near, far, options.maximum_cells, slack)
                    for current, _begin, near, far in oriented
                )
                if best_cells is None or cells < best_cells:
                    best, best_cells = value, cells
        for current, begin, _near, _far in oriented:
            ratios[current] = math.exp(best if begin == current[0] else -best)
    return ratios


def _strip_targets(strips, edges, bounds, ratios, options) -> dict[EdgeKey, int]:
    """Cells each edge needs for its strip's series to stay within its bounds."""
    slack = 1.0
    needed = {}
    for orientation in strips:
        for current, begin in orientation.items():
            total = ratios[current]
            needed[current] = _fit_count(
                edges[current].length, total, bounds[(current, current[0])],
                bounds[(current, current[1])], options.maximum_cells, slack,
            )
    return needed


def _actual(edges, ratios, counts) -> dict[tuple[EdgeKey, str], float]:
    """The end widths every edge's series gives at its count."""
    widths = {}
    for current, item in edges.items():
        first, last = _end_widths(item.length, counts[current], ratios[current])
        widths[(current, current[0])] = first
        widths[(current, current[1])] = last
    return widths


def _report(model, cells_before, components, splits, refused, factor, note) -> SizeResult:
    from .quality import assess_quality

    report = assess_quality(model)
    summary = report.summary()
    worst = [
        {"edge": "-".join(item.edge), "blocks": list(item.blocks), "jump": item.max_size_ratio}
        for item in report.worst_interfaces(8)
    ]
    return SizeResult(
        total_cells=_total_cells(model),
        cells_before=cells_before,
        blocks=len(model.blocks),
        components=components,
        splits=splits,
        refused=refused,
        jumps=summary.get("interface_jumps", {}),
        worst_jumps=worst,
        bands=_bands(model),
        worst_interface=summary.get("max_interface_size_ratio", 1.0),
        worst_aspect=summary.get("max_cell_aspect_ratio", 1.0),
        unsynchronized_links=sum(
            1 for link in model.spacing_links if not model.spacing_link_is_synchronized(link)
        ),
        budget_factor=factor,
        note=note,
    )


def _replace(model: MeshModel, working: MeshModel) -> None:
    """Make ``model`` the sized copy, in place, so callers keep their object."""
    restored = from_data(to_data(working))
    model.__dict__.update(restored.__dict__)


def _total_cells(model: MeshModel) -> int:
    total = 0
    for block in model.blocks:
        nx, ny, nz = model.block_cell_counts(block)
        total += nx * ny * nz
    return total


def _scope_edges(model: MeshModel, blocks, bbox) -> set[EdgeKey]:
    chosen = list(model.blocks)
    if blocks:
        known = {block.id for block in model.blocks}
        unknown = [identifier for identifier in blocks if identifier not in known]
        if unknown:
            raise TopologyError(f"Unknown block(s) {', '.join(unknown)}")
        chosen = [block for block in chosen if block.id in set(blocks)]
    if bbox is not None:
        if len(bbox) != 4:
            raise TopologyError("bbox needs four numbers: xmin, ymin, xmax, ymax")
        xmin, ymin, xmax, ymax = (float(value) for value in bbox)
        chosen = [
            block for block in chosen
            if any(
                xmin <= model.vertices[v].x <= xmax and ymin <= model.vertices[v].y <= ymax
                for v in block.vertices
            )
        ]
    return {edge_key(*block.directed_edge(index)) for block in chosen for index in range(4)}


def _budget_factor(model, components, targets, options: SizeOptions) -> float:
    if not options.budget or options.budget <= 0:
        return 1.0
    index_of = {member: position for position, members in enumerate(components) for member in members}

    def total(factor: float) -> int:
        values = [
            min(options.maximum_cells, max(options.minimum_cells, math.ceil(target * factor - 1.0e-9)))
            for target in targets
        ]
        result = 0
        for block in model.blocks:
            first = edge_key(*block.directed_edge(0))
            second = edge_key(*block.directed_edge(1))
            nx = values[index_of[first]] if first in index_of else model.edge_cells[first]
            ny = values[index_of[second]] if second in index_of else model.edge_cells[second]
            result += nx * ny * model.z_cells
        return result

    # The field gets the share of the budget the default leaves it (the
    # default budget is DEFAULT_BUDGET times the field's own cells); the
    # rest is spent on continuity.
    limit = options.budget / DEFAULT_BUDGET
    if total(1.0) <= limit:
        return 1.0
    low, high = 1.0e-3, 1.0
    for _ in range(40):
        middle = 0.5 * (low + high)
        if total(middle) > limit:
            high = middle
        else:
            low = middle
    return low


def _split_strips(model, field_, state, options, splits, refused) -> bool:
    """Split the two-ended strips found, the most two-ended first, at most
    one per count component; ``True`` when one was split."""
    candidates = sorted(state["two_ended"].items(), key=lambda item: (-item[1][1], item[0]))
    done: set[EdgeKey] = set()
    split_any = False
    made = 0
    for current, (fraction, ratio) in candidates[:SPLITS_PER_ROUND * 4]:
        if current in done or current not in model.edge_cells:
            continue
        component = model.edge_constraint_component(current)
        if done & component:
            continue
        done.update(component)
        saved = from_data(to_data(model))
        try:
            for member in component:
                model.edge_cells[member] = SPLIT_RESOLUTION
                model.edge_grading.pop(member, None)
            split = model.split_edge(current, fraction)
        except (TopologyError, ValueError) as exc:
            model.__dict__.update(saved.__dict__)
            text = f"two-ended strip of {'-'.join(current)} (peak {ratio:.3g} times its ends) not split: {exc}"
            if text not in refused:
                refused.append(text)
            continue
        new_edges = [
            item for item in model.edges()
            if any(v in split.split_vertex_ids for v in item) and item not in split.cut_edges
        ]
        # A piece must hold at least one cell of the size wanted where it
        # lies: a cut that leaves a sliver makes a forced-fine source of its
        # own (a 30P30N strip cut at its peak left a 0.001 piece carrying
        # nine cells beside 0.014 cells).
        sliver = None
        for item in new_edges:
            sample = _sample_edge(model, item, field_, options)
            if sample.length < min(sample.field):
                sliver = (item, sample.length, min(sample.field))
                break
        if sliver is not None:
            model.__dict__.update(saved.__dict__)
            text = (
                f"two-ended strip of {'-'.join(current)} not split: the piece "
                f"{'-'.join(sliver[0])} would be {sliver[1]:.3g} long where cells of "
                f"{sliver[2]:.3g} are wanted"
            )
            if text not in refused:
                refused.append(text)
            continue
        splits.append({
            "edge": "-".join(current),
            "fraction": fraction,
            "peak_over_ends": ratio,
            "new_vertices": list(split.split_vertex_ids),
            "new_blocks": list(split.new_block_ids),
        })
        split_any = True
        made += 1
        if made >= SPLITS_PER_ROUND:
            break
    return split_any


def _bands(model: MeshModel) -> list[dict]:
    """Per wall patch: the edges leaving it into the fluid, their cells and first widths."""
    walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    by_patch: dict[str, set[str]] = {}
    on_wall: set[EdgeKey] = set()
    for current, name in model.edge_boundaries.items():
        if name in walls:
            by_patch.setdefault(name, set()).update(current)
            on_wall.add(current)
    bands = []
    for name in sorted(by_patch):
        vertices = by_patch[name]
        cells = []
        firsts = []
        for current in model.edges():
            if current in on_wall:
                continue
            ends = [v for v in current if v in vertices]
            if len(ends) != 1:
                continue
            cells.append(model.edge_cells[current])
            firsts.append(model.edge_width_at_vertex(current, ends[0]))
        if cells:
            bands.append({
                "patch": name,
                "spokes": len(cells),
                "cells": [min(cells), max(cells)],
                "first_width": [min(firsts), max(firsts)],
            })
    return bands


# ---------------------------------------------------------------------------
# Spending a cell budget on continuity
# ---------------------------------------------------------------------------


def _max_jump(state: dict) -> float:
    widths = _actual(state["edges"], state["ratios"], state["counts"])
    return max((max(widths[a] / widths[b], widths[b] / widths[a])
                for a, b in _pair_list(state["links"])), default=1.0)


def _stranded(state: dict, options: SizeOptions) -> dict[EdgeKey, tuple[float, float]]:
    """Edges left coarse at both ends against finer neighbours.

    At each end the width an edge should have is its finest neighbour's
    times ``interface_growth`` (or the field's, if finer); growing from
    both ends at ``growth`` meets inside, and when the size there is
    ``SPLIT_RATIO`` times the ends' and the edge's series misses both, one
    series cannot serve it: a thin strip forced fine by its count on one
    side, a wall on the other (e3's wedge between a thin gap and the
    wall)."""
    cap = options.interface_growth or 1.25
    slope = options.growth - 1.0
    widths = _actual(state["edges"], state["ratios"], state["counts"])
    found = {}
    for current, item in state["edges"].items():
        if current not in state["scope"] or state["counts"][current] < 2:
            continue
        wanted = []
        for vertex in current:
            partners = state["links"].get((current, vertex), ())
            target = state["seeds"][(current, vertex)]
            if partners:
                target = min(target, cap * min(widths[partner] for partner in partners))
            wanted.append(target)
        if not all(widths[(current, vertex)] > want * (1.0 + 1.0e-6)
                   for vertex, want in zip(current, wanted)):
            continue
        profile = _profile(item, wanted[0], wanted[1], slope, options.core_size)
        ends = max(profile[0], profile[-1])
        peak = max(range(len(profile)),
                   key=lambda index: (profile[index], -abs(item.arc[index] - 0.5 * item.arc[-1])))
        fraction = item.arc[peak] / item.arc[-1]
        if profile[peak] >= SPLIT_RATIO * ends and 0.05 < fraction < 0.95:
            found[current] = (fraction, profile[peak] / ends)
    return found


def _continuity_splits(working, field_, state, moves, guard, options, budget, blocks, bbox, splits, refused,
                       skew_floor=None):
    """Split strips the spent state leaves stranded, then solve and spend
    again; a round is kept only when it lowers the jump cost without
    raising the worst jump."""
    for _round in range(CONTINUITY_SPLIT_ROUNDS):
        stranded = _stranded(state, options)
        if not stranded:
            break
        saved = from_data(to_data(working))
        made = len(splits)
        if not _split_strips(working, field_, {"two_ended": stranded}, options, splits, refused):
            break
        trial = _solve(working, field_, options, blocks, bbox)
        trial, trial_moves, trial_guard = _spend(working, trial, options, budget, skew_floor)
        if (_jump_cost(trial, options) >= _jump_cost(state, options)
                or _max_jump(trial) > _max_jump(state) * (1.0 + 1.0e-9)):
            working.__dict__.update(saved.__dict__)
            for item in splits[made:]:
                refused.append(
                    f"split of {item['edge']} undone: after spending the budget again it did "
                    "not lower the interface jumps"
                )
            del splits[made:]
            break
        state, moves, guard = trial, trial_moves, trial_guard
    return state, moves, guard


def _max_skewness(model: MeshModel) -> float:
    from .sampling import sample_model

    return sample_model(model, GUARD_CELLS).summary().get("max_skewness", 0.0)


def _state_cells(model: MeshModel, state: dict) -> int:
    counts = state["counts"]
    total = 0
    for block in model.blocks:
        nx = counts[edge_key(*block.directed_edge(0))]
        ny = counts[edge_key(*block.directed_edge(1))]
        total += nx * ny * model.z_cells
    return total


def _pair_list(links) -> list[tuple[tuple[EdgeKey, str], tuple[EdgeKey, str]]]:
    pairs = []
    for first, others in sorted(links.items()):
        for second in sorted(others):
            if first < second:
                pairs.append((first, second))
    return pairs


def _pair_cost(w1: float, w2: float, cap: float) -> float:
    """Convex in log(jump) above the cap: several medium jumps and one huge
    one are both seen."""
    excess = abs(math.log(w1 / w2)) - math.log(cap)
    return excess * excess if excess > 0.0 else 0.0


def _jump_cost(state: dict, options: SizeOptions) -> float:
    cap = options.interface_growth or 1.25
    widths = _actual(state["edges"], state["ratios"], state["counts"])
    return sum(_pair_cost(widths[a], widths[b], cap) for a, b in _pair_list(state["links"]))


def _spend(model: MeshModel, state: dict, options: SizeOptions, budget: int, skew_floor: float | None = None):
    """Spend cells on continuity, greedily, never taking a move that does
    not pay.

    The global measure is the jump cost: over every interface pair the
    quality screen compares, the square of how far log(width ratio) exceeds
    log(interface_growth). Moves: raise one component's count (every strip
    it holds keeps its ratio, so both ends get finer), at a cost in cells,
    or regrade one strip's shared ratio, free but kept within the field's
    own sizes and its cell-to-cell growth. Free moves come first, then the
    raise with the largest drop per cell, until the budget is spent or no
    move lowers the cost; the greedy stops at the first move the budget
    cannot pay for. Each accepted move is checked on the sampled
    grid of the blocks it touches and their neighbours: a move that folds
    a cell, adds a face above 70 degrees or takes the skewness above 4 when
    it was below is undone and not tried again. ``repair_shape`` runs
    first (see there); ``skew_floor`` is the input's sampled skewness.
    """
    from .sampling import sample_model

    cap = options.interface_growth or 1.25
    edges, strips, seeds = state["edges"], state["strips"], state["seeds"]
    counts = dict(state["counts"])
    scope = state["scope"]
    pairs = _pair_list(state["links"])
    strip_of: dict[EdgeKey, int] = {}
    begin_of: dict[EdgeKey, str] = {}
    logs: list[float] = []
    ratios = state["ratios"]
    for index, orientation in enumerate(strips):
        for current, begin in orientation.items():
            strip_of[current] = index
            begin_of[current] = begin
        member = next(iter(orientation))
        value = math.log(ratios[member])
        logs.append(value if begin_of[member] == member[0] else -value)
    components = _components(model)
    component_of = {member: index for index, members in enumerate(components) for member in members}
    pairs_of_edge: dict[EdgeKey, list[int]] = {}
    for index, (a, b) in enumerate(pairs):
        pairs_of_edge.setdefault(a[0], []).append(index)
        pairs_of_edge.setdefault(b[0], []).append(index)
    blocks_of_edge: dict[EdgeKey, list[str]] = {}
    across: dict[str, list[int]] = {}
    for block in model.blocks:
        keys = [edge_key(*block.directed_edge(k)) for k in range(4)]
        for current in keys:
            blocks_of_edge.setdefault(current, []).append(block.id)
        across.setdefault(component_of[keys[0]], []).append(component_of[keys[1]])
        across.setdefault(component_of[keys[1]], []).append(component_of[keys[0]])

    def total_ratio(current, log=None):
        value = logs[strip_of[current]] if log is None else log
        return math.exp(value if begin_of[current] == current[0] else -value)

    def width(current, vertex, n=None, log=None):
        first, last = _end_widths(edges[current].length, counts[current] if n is None else n,
                                  total_ratio(current, log))
        return first if vertex == current[0] else last

    def cost_of(indices, n_of=None, log_of=None):
        total = 0.0
        for index in indices:
            (e1, v1), (e2, v2) = pairs[index]
            n1 = n_of.get(component_of[e1]) if n_of else None
            n2 = n_of.get(component_of[e2]) if n_of else None
            l1 = log_of.get(strip_of[e1]) if log_of else None
            l2 = log_of.get(strip_of[e2]) if log_of else None
            total += _pair_cost(width(e1, v1, n1, l1), width(e2, v2, n2, l2), cap)
        return total

    def allowed(current, log, n=None):
        # Never coarser at an end than the field wants there (or than it
        # already is, under a budget below the field's own cells), and
        # never growing faster from cell to cell than the field does.
        if abs(log) > MAX_LOG_RATIO:
            return False
        old, length = counts[current], edges[current].length
        n = old if n is None else n
        first, last = _end_widths(length, n, total_ratio(current, log))
        now_first, now_last = _end_widths(length, old, total_ratio(current))
        if first > max(seeds[(current, current[0])], now_first) * (1.0 + 1.0e-9):
            return False
        if last > max(seeds[(current, current[1])], now_last) * (1.0 + 1.0e-9):
            return False
        if n < 2:
            return abs(log) <= 1.0e-12
        step = abs(log) / (n - 1)
        here = abs(logs[strip_of[current]]) / (old - 1) if old > 1 else 0.0
        return step <= max(math.log(options.growth), here) + 1.0e-12

    def refits(strip, n):
        """Ratios a strip can take at ``n`` cells: its own, and the ones that
        keep one end of one of its edges at the width it has now."""
        values = [logs[strip]]
        for member in sorted(strips[strip])[:8]:
            length = edges[member].length
            for vertex in member:
                kept = width(member, vertex)
                log = (n - 1) * math.log(series_ratio(kept, length, n))
                if vertex == member[1]:
                    log = -log
                values.append(log if begin_of[member] == member[0] else -log)
        return [value for value in values if all(allowed(m, value, n) for m in strips[strip])]

    component_pairs = [
        sorted({index for member in members for index in pairs_of_edge.get(member, ())})
        for members in components
    ]
    strip_pairs = [
        sorted({index for member in orientation for index in pairs_of_edge.get(member, ())})
        for orientation in strips
    ]
    strips_of_component: dict[int, list[int]] = {}
    for index, orientation in enumerate(strips):
        strips_of_component.setdefault(component_of[next(iter(orientation))], []).append(index)
    component_blocks = [
        sorted({block for member in members for block in blocks_of_edge.get(member, ())})
        for members in components
    ]
    strip_blocks = [
        sorted({block for member in orientation for block in blocks_of_edge.get(member, ())})
        for orientation in strips
    ]
    neighbours: dict[str, set[str]] = {}
    for users in blocks_of_edge.values():
        for block in users:
            neighbours.setdefault(block, set()).update(users)
    scoped_components = [
        index for index, members in enumerate(components) if any(m in scope for m in members)
    ]
    scoped_strips = [
        index for index, orientation in enumerate(strips) if all(m in scope for m in orientation)
    ]
    trial = from_data(to_data(model))
    _write(trial, {**state, "counts": counts}, options)

    def write_raise(index, value, refit):
        for member in components[index]:
            trial.edge_cells[member] = value
        for strip, log in refit.items():
            write_regrade(strip, log)

    def write_regrade(index, value):
        for member in strips[index]:
            total = total_ratio(member, value)
            if trial.edge_cells[member] < 2 or abs(total - 1.0) <= 1.0e-9:
                trial.edge_grading.pop(member, None)
            else:
                trial.edge_grading[member] = total

    def verdict(blocks):
        summary = sample_model(trial, GUARD_CELLS, blocks=blocks).summary()
        return (summary.get("folded_cells", 0), summary.get("severe_faces", 0),
                summary.get("max_skewness", 0.0), summary.get("max_non_orthogonality", 0.0))

    def guarded(blocks, apply, undo) -> bool:
        local = sorted({other for block in blocks for other in neighbours.get(block, ())})
        before = verdict(local)
        apply()
        after = verdict(local)
        if (after[0] <= before[0] and after[1] <= before[1]
                and not (after[2] > SKEWNESS_LIMIT and before[2] <= SKEWNESS_LIMIT)):
            return True
        undo()
        return False

    cells = _state_cells(model, {"counts": counts})
    moves: list[dict] = []
    tabu: set[tuple] = set()
    refused = 0

    def repair_shape() -> None:
        """Repair what the field's counts break before any cell is spent on
        continuity; the guard then keeps it repaired.

        First a block left above skewness 4 when the input was below (a
        tapered block with few cells across): only folds are refused there,
        since checkMesh fails a mesh above skewness 4 but only counts faces
        above 70 degrees (e3's b77 at five cells across: 4.9 with 10 such
        faces, 3.5 with 15 at seven). Then the block with the worst face
        above 70 degrees, while its count of them or its worst face falls
        and the skewness stays at or below 4. Candidates: raise one of the
        block's two components, take one of its strips towards uniform, or
        give one side the grading of its opposite side.
        """
        nonlocal cells
        by_id = {block.id: block for block in model.blocks}
        skew_phase = skew_floor is not None and skew_floor <= SKEWNESS_LIMIT
        for _repair in range(SHAPE_REPAIRS):
            summary = sample_model(trial, GUARD_CELLS).summary()
            if skew_phase and summary.get("max_skewness", 0.0) > SKEWNESS_LIMIT:
                worst = by_id[summary["worst_skew_block"]]
                skewed = True
            elif summary.get("severe_faces", 0) and summary.get("worst_block") in by_id:
                worst = by_id[summary["worst_block"]]
                skewed = False
            else:
                break
            keys = [edge_key(*worst.directed_edge(k)) for k in range(4)]
            local = sorted(neighbours.get(worst.id, {worst.id}))
            base = verdict(local)
            trials = []
            for index in sorted({component_of[k] for k in keys[:2]}):
                n = counts[components[index][0]]
                raised = min(options.maximum_cells, n + max(1, math.ceil(RAISE_STEP * n)))
                added = (raised - n) * sum(counts[components[other][0]] for other in across.get(index, ()))
                if raised > n and cells + added * model.z_cells <= budget:
                    trials.append(("raise", index, raised, added * model.z_cells))
            values: dict[int, set[float]] = {}
            for index in {strip_of[k] for k in keys}:
                values.setdefault(index, set()).update((0.0, 0.5 * logs[index]))
            for k in range(4):
                # Side k runs from corner k to k+1, its opposite from k+3 to k+2.
                here, there = worst.directed_edge(k), worst.directed_edge((k + 2) % 4)
                key, other = edge_key(*here), edge_key(*there)
                ratio_there = total_ratio(other) if there[0] == other[0] else 1.0 / total_ratio(other)
                wanted = 1.0 / ratio_there
                total = wanted if here[0] == key[0] else 1.0 / wanted
                values.setdefault(strip_of[key], set()).add(
                    math.log(total) if begin_of[key] == key[0] else -math.log(total))
            for index in sorted(values):
                for value in sorted(values[index]):
                    if abs(value - logs[index]) > 1.0e-9 and all(allowed(m, value) for m in strips[index]):
                        trials.append(("regrade", index, value, 0))
            best_trial = None
            for kind, index, value, added in trials:
                if kind == "raise":
                    old = counts[components[index][0]]
                    write_raise(index, value, {})
                    result = verdict(local)
                    write_raise(index, old, {})
                else:
                    old = logs[index]
                    write_regrade(index, value)
                    result = verdict(local)
                    write_regrade(index, old)
                if result[0] > base[0]:
                    continue
                if skewed:
                    score = (result[2],)
                    better = result[2] < base[2] - 1.0e-9
                else:
                    score = (result[1], result[3])
                    better = (
                        (result[1] < base[1] or (result[1] == base[1] and result[3] < base[3] - 0.5))
                        and not (result[2] > SKEWNESS_LIMIT >= base[2])
                    )
                if better and (best_trial is None or score < best_trial[0]):
                    best_trial = (score, kind, index, value, added)
            if best_trial is None:
                break
            _score, kind, index, value, added = best_trial
            if kind == "raise":
                moves.append({"kind": "shape", "component": index, "from": counts[components[index][0]],
                              "to": value, "cells": added})
                write_raise(index, value, {})
                for member in components[index]:
                    counts[member] = value
                cells += added
            else:
                moves.append({"kind": "shape", "strip": index, "from": logs[index], "to": value})
                write_regrade(index, value)
                logs[index] = value

    repair_shape()
    for _iteration in range(20000):
        best = None
        for index in scoped_strips:
            touched = strip_pairs[index]
            if not touched:
                continue
            before = cost_of(touched)
            if before <= 0.0:
                continue
            for step in REGRADE_STEPS:
                value = logs[index] + step * math.log(cap)
                if ("regrade", index, round(value, 9)) in tabu:
                    continue
                if not all(allowed(m, value) for m in strips[index]):
                    continue
                gain = before - cost_of(touched, log_of={index: value})
                if gain > 1.0e-9 and (best is None or gain > best[1]):
                    best = ("regrade", gain, index, value, 0)
        if best is None:
            for index in scoped_components:
                touched = component_pairs[index]
                if not touched:
                    continue
                before = cost_of(touched)
                if before <= 0.0:
                    continue
                n = counts[components[index][0]]
                raised = min(options.maximum_cells, n + max(1, math.ceil(RAISE_STEP * n)))
                if raised <= n or ("raise", index, raised) in tabu:
                    continue
                added = (raised - n) * sum(counts[components[other][0]] for other in across.get(index, ()))
                added *= model.z_cells
                refit: dict[int, float] = {}
                for strip in strips_of_component.get(index, ()):
                    options_here = refits(strip, raised)
                    if not options_here:
                        refit = None
                        break
                    refit[strip] = min(
                        options_here,
                        key=lambda value, strip=strip: cost_of(
                            strip_pairs[strip], n_of={index: raised}, log_of={strip: value}),
                    )
                if refit is None:
                    continue
                gain = before - cost_of(touched, n_of={index: raised}, log_of=refit)
                if gain <= 1.0e-9:
                    continue
                score = gain / max(added, 1)
                if best is None or score > best[1]:
                    best = ("raise", score, index, raised, (added, refit))
        if best is None:
            break
        if best[0] == "raise" and cells + best[4][0] > budget:
            # Stop at the first move the budget cannot pay for: a smaller
            # budget's moves are then a prefix of a larger one's, so more
            # budget never leaves a higher jump cost.
            break
        kind, _score, index, value, added = best
        if kind == "regrade":
            old = logs[index]

            def apply(index=index, value=value):
                write_regrade(index, value)

            def undo(index=index, old=old):
                write_regrade(index, old)

            if not guarded(strip_blocks[index], apply, undo):
                tabu.add(("regrade", index, round(value, 9)))
                refused += 1
                continue
            moves.append({"kind": "regrade", "strip": index, "from": old, "to": value})
            logs[index] = value
        else:
            added, refit = added
            old = counts[components[index][0]]
            old_logs = {strip: logs[strip] for strip in refit}

            def apply(index=index, value=value, refit=refit):
                write_raise(index, value, refit)

            def undo(index=index, old=old, old_logs=old_logs):
                write_raise(index, old, old_logs)

            if not guarded(component_blocks[index], apply, undo):
                # The refitted ratios may be what the sampled check refuses
                # (a tapered block sheared by a steeper series): try the
                # raise with the strips' ratios as they are.
                kept = {strip: logs[strip] for strip in refit}
                plain = refit != kept and (
                    cost_of(component_pairs[index])
                    - cost_of(component_pairs[index], n_of={index: value}) > 1.0e-9
                )
                if not plain or not guarded(
                    component_blocks[index],
                    lambda index=index, value=value: write_raise(index, value, {}),
                    lambda index=index, old=old: write_raise(index, old, {}),
                ):
                    tabu.add(("raise", index, value))
                    refused += 1
                    continue
                refit = {}
            moves.append({"kind": "raise", "component": index, "from": old, "to": value, "cells": added})
            for member in components[index]:
                counts[member] = value
            logs_changed = refit
            for strip, log in logs_changed.items():
                logs[strip] = log
            cells += added
    new_ratios = dict(ratios)
    for index, orientation in enumerate(strips):
        for member in orientation:
            new_ratios[member] = total_ratio(member)
    guard = (
        f"{refused} move(s) refused by the sampled check (a folded cell, a face above "
        "70 degrees or skewness above 4)" if refused else ""
    )
    return {**state, "counts": counts, "ratios": new_ratios}, moves, guard
