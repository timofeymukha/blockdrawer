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
5. **Forced-fine interfaces are sources.** A short edge given many cells
   by its component (a thin wake strip carrying the band's count) is much
   finer than its bounds; its actual widths are fed back as bounds and the
   propagation runs again, so the blocks beside it are graded from it. The
   bounds only decrease and the counts only increase (they are bounded by
   ``maximum_cells``), so the rounds terminate; ``interface_growth`` above
   one damps them, since an end finer than its bound by less than that
   factor changes no neighbour.

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
# A split is refused when it leaves a piece shorter than this fraction of
# the strip's shortest edge.
SPLIT_MIN_PIECE = 0.2
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
    # Rounds of feeding actual widths back as sources (forced-fine strips
    # grade their neighbours, raising counts). Measured in the README: one
    # round takes e3's worst jump from 45 to 6, and multiplies the 30P30N
    # cells by seventeen.
    continuity_rounds: int = 0

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
    rounds: int = 0
    raised: int = 0
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
            "continuity_rounds": self.rounds,
            "components_raised": self.raised,
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
        return first * cells if abs(q - 1.0) < 1.0e-12 else first * (q ** cells - 1.0) / (q - 1.0)

    low, high = (1.0, 2.0) if growing else (0.5, 1.0)
    if growing:
        while total(high) < length and high < 1.0e6:
            high *= 2.0
    else:
        while total(low) > length and low > 1.0e-6:
            low *= 0.5
    for _ in range(100):
        middle = 0.5 * (low + high)
        if (total(middle) < length) == growing:
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
    attempt = 0
    while True:
        state = _solve(working, field_, options, blocks, bbox)
        attempt += 1
        if options.keep_counts or not options.split or attempt > SPLIT_ROUNDS:
            break
        if not state["two_ended"]:
            break
        if not _split_strips(working, state, options, splits, refused):
            break
    counts, bounds, edges, strips, scope = (
        state["counts"], state["bounds"], state["edges"], state["strips"], state["scope"]
    )
    for current, value in counts.items():
        working.edge_cells[current] = value
    ratios = _ratios(strips, edges, bounds, options)
    for current in scope:
        total = ratios[current]
        if counts[current] < 2 or abs(total - 1.0) <= 1.0e-9:
            working.edge_grading.pop(current, None)
        else:
            working.edge_grading[current] = total
    working.validate()
    result = _report(
        working, edges, cells_before, state["components"], state["rounds"], state["raised"],
        splits, refused, state["factor"], note,
    )
    _replace(model, working)
    return result


def _solve(working, field_, options, blocks, bbox) -> dict:
    """Counts and bounds for the model as it is: field bounds, counts, then
    rounds of feeding the actual widths back until no count rises."""
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
    rounds = 0
    raised = 0
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
    # Feed the actual widths back: an edge its component made finer than
    # its bounds is a source for the blocks beside it. With ``keep_counts``
    # the counts stay and only the bounds - the gradings - follow.
    for rounds in range(1, max(options.continuity_rounds, 1 if options.keep_counts else 0) + 1):
        widths = _actual(edges, _ratios(strips, edges, bounds, options), counts)
        fed = dict(seeds)
        changed = False
        for key, value in widths.items():
            if value < fed.get(key, math.inf):
                fed[key] = value
        new_bounds = _bounds(edges, fed, links, options)
        new_ratios = _ratios(strips, edges, new_bounds, options)
        targets = _targets(
            edges, new_bounds, sized, slope, options,
            _strip_targets(strips, edges, new_bounds, new_ratios, options), two_ended, widths,
        )
        for members, target in zip(sized, [] if options.keep_counts else targets):
            value = int(min(options.maximum_cells, max(options.minimum_cells, math.ceil(target * factor - 1.0e-9))))
            if value > counts[members[0]]:
                for member in members:
                    counts[member] = value
                raised += 1
                changed = True
        stable = all(abs(new_bounds[key] - bounds.get(key, math.inf)) <= 1.0e-12 * new_bounds[key] for key in new_bounds)
        bounds = new_bounds
        seeds = fed
        if not changed and (stable or not options.keep_counts):
            break
    return {
        "counts": counts, "bounds": bounds, "edges": edges, "strips": strips, "scope": scope,
        "components": len(sized), "rounds": rounds, "raised": raised, "factor": factor,
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


def _report(model, edges, cells_before, components, rounds, raised, splits, refused, factor, note) -> SizeResult:
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
        rounds=rounds,
        raised=raised,
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

    if total(1.0) <= options.budget:
        return 1.0
    low, high = 1.0e-3, 1.0
    for _ in range(40):
        middle = 0.5 * (low + high)
        if total(middle) > options.budget:
            high = middle
        else:
            low = middle
    return low


def _split_strips(model, state, options, splits, refused) -> bool:
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
        shortest = min((model.edge_length(item) for item in new_edges), default=math.inf)
        parent = min((saved.edge_length(item) for item in component), default=math.inf)
        if shortest < SPLIT_MIN_PIECE * parent:
            # A cut that leaves a sliver makes a forced-fine source of its
            # own: a 30P30N strip cut at its peak left a 0.001 piece
            # carrying nine cells beside 0.014 cells.
            model.__dict__.update(saved.__dict__)
            text = (
                f"two-ended strip of {'-'.join(current)} not split: the cut would leave a "
                f"piece of {shortest:.3g} on a strip whose shortest edge is {parent:.3g}"
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
