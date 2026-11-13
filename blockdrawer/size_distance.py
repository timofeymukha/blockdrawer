"""Item 3's sizing (commit 9a84f29), kept behind ``size method=distance``.

The directional field in ``size.py`` is the default and better on every
session measured but one: on the fine 30P30N this version gave 452,791
cells with no face above 70 degrees after the polish, which the
directional field's own state cannot reach. It is kept unchanged so that
case stays reproducible; ``size.size_model`` calls it, verifies its result
and refuses skewness past 4 as for the default.

Set every cell count and grading of a session from a few physical inputs.

``size_model`` needs nothing but what a session has: the patches of type
``wall``, the geometry, and the opposite-edge count components. It works in
the geometry's units.

1. **A size field** from the wall distance ``d``: the wall-normal series
   ``first + (growth - 1) * d`` (each wall patch may carry its own first
   width), capped at ``core_size``. Distances come from the sampled wall
   paths through a bucket grid, so a query costs a few buckets.
2. **Two-ended strips are split.** An edge whose wanted size peaks well
   inside it - wall to wall across a channel or a slot - cannot be served
   by one geometric series, and multi-section grading is outside
   BlockDrawer's scope; its strip is cut with the conformal ``split_edge``
   where the size peaks, so each half grades from its own wall.
3. **Wall-normal and tangential edges** are told apart without roles: an
   edge along which the wall distance changes by at least half its chord
   runs away from a wall (a band or core spoke, a wake line); every other
   edge runs along one. Tangential sizes are floored at ``wall_aspect``
   first widths (capped where the edge curves, so one cell turns at most
   15 degrees).
4. **Counts per component** from the edges' demands - the integral of
   ``ds / size`` along each - by the ``max`` or the length-weighted ``mean``
   rule, scaled down uniformly to a cell budget when one is given.
5. **Grading.** Every wall-normal edge carries one geometric series from
   the size its finer end wants; with ``interface_growth`` the widths are
   then matched across every block interface, finest first, between
   wall-normal edges that continue each other (they leave the shared
   vertex in nearly opposite directions), a block's opposite side taking
   the same series when its length is within a factor of two, and a
   component's count raised where a series would exceed the cap. What one
   series still cannot match is reported, not guessed. Tangential edges
   stay uniform: graded through the chains, a far-field piece handed its
   fractions down to a wall piece five times shorter in the research
   engine.

Spacing links are left as they are: ``size`` writes counts and gradings
directly and reports the links its widths left out of sync (its interface
matching is the global form of what links do; ``synchronize_spacing_links``
re-imposes a link afterwards). Everything is deterministic and applied as
one validated edit; on any failure the model is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from typing import Any, Iterable, Sequence

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .session import from_data, to_data


# An edge whose wall distance changes by at least this fraction of its
# chord runs away from a wall.
NORMAL_SLOPE = 0.5
# A tangential cell may turn at most this much along a curved edge.
MAX_CELL_TURNING = math.radians(15.0)
# Two wall-normal edges at a vertex continue each other across a block
# interface when they leave it at least this far apart.
LINK_ANGLE = math.radians(90.0)
# A block's opposite side takes the same series only within this length ratio.
OPPOSITE_LENGTH_RATIO = 2.0
# A strip is two-ended when the size it wants inside exceeds the size at
# both its ends by this factor.
SPLIT_RATIO = 4.0
SPLIT_ROUNDS = 4
# Which wall-normal edges start from a wanted width: ``wall`` - those leaving
# a wall vertex, at its first width - or ``field`` - every one, at the size
# the field wants at its finer end; and whether the opposite wall-normal
# sides of a block are given one series (``_unify_opposite``): ``always``,
# ``interface`` (only with interface matching) or ``never``. Measured over
# six sessions after shape, size and polish, ``wall``/``always`` gave the
# lowest maximum non-orthogonality on five (backward step 19.6 against
# 43.6, grooved tandem 28.1 against 44.0); ``field`` gave lower interface
# jumps on 30P30N (38 against 57) and the grooved tandem (129 against 259).
SEEDS = "wall"
UNIFY = "always"
# The count a strip is given before it is split, so the cut lands close to
# the wanted fraction (split_edge snaps to a mesh node).
SPLIT_RESOLUTION = 1000


@dataclass(frozen=True)
class SizeOptions:
    first_width: float
    core_size: float
    growth: float = 1.2
    wall_aspect: float | None = None
    interface_growth: float | None = None
    count_rule: str = "max"
    budget: int | None = None
    patch_first_widths: tuple[tuple[str, float], ...] = ()
    split: bool = True
    minimum_cells: int = 1
    maximum_cells: int = 2000

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
    components: int
    raised: list[dict] = field(default_factory=list)
    splits: list[dict] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    mismatches: list[dict] = field(default_factory=list)
    mismatch_count: int = 0
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
            "components": self.components,
            "raised": self.raised,
            "splits": self.splits,
            "refused": self.refused,
            "mismatches": self.mismatches,
            "mismatch_count": self.mismatch_count,
            "bands": self.bands,
            "worst_interface_size_ratio": self.worst_interface,
            "worst_cell_aspect_ratio": self.worst_aspect,
            "unsynchronized_spacing_links": self.unsynchronized_links,
            "budget_factor": self.budget_factor,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# The size field
# ---------------------------------------------------------------------------


class SizeField:
    """``size(p) = min over walls (first_w + (growth - 1) * d_w(p))``, capped at the core."""

    def __init__(self, model: MeshModel, options: SizeOptions) -> None:
        self.growth = options.growth
        self.core = options.core_size
        overrides = dict(options.patch_first_widths)
        walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
        segments: list[tuple[float, float, float, float, float]] = []
        for current, name in sorted(model.edge_boundaries.items()):
            if name not in walls:
                continue
            first = overrides.get(name, options.first_width)
            points = _path(model, current, 32)
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                segments.append((ax, ay, bx, by, first))
        self.segments = segments
        self.minimum_first = min((item[4] for item in segments), default=options.first_width)
        # Beyond this distance from every wall the field is the core size.
        self.reach = (self.core - self.minimum_first) / (self.growth - 1.0)
        self.buckets: dict[tuple[int, int], list[int]] = {}
        if not segments:
            self.bucket = 1.0
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

    def query(self, x: float, y: float) -> tuple[float, float]:
        """The nearest wall distance (``inf`` beyond the field's reach) and the size."""
        if not self.segments:
            return math.inf, self.core
        i0 = math.floor(x / self.bucket)
        j0 = math.floor(y / self.bucket)
        best_size = math.inf
        best_distance = math.inf
        slope = self.growth - 1.0
        rings = int(self.reach / self.bucket) + 2
        for ring in range(rings + 1):
            bound = max(0.0, (ring - 1) * self.bucket)
            if (
                self.minimum_first + slope * bound >= min(best_size, self.core)
                and bound >= min(best_distance, self.reach)
            ):
                break
            for i in range(i0 - ring, i0 + ring + 1):
                for j in (range(j0 - ring, j0 + ring + 1) if abs(i - i0) == ring else (j0 - ring, j0 + ring)):
                    for index in self.buckets.get((i, j), ()):
                        ax, ay, bx, by, first = self.segments[index]
                        distance = _segment_distance(x, y, ax, ay, bx, by)
                        if distance < best_distance:
                            best_distance = distance
                        value = first + slope * distance
                        if value < best_size:
                            best_size = value
                if ring == 0:
                    break
        return best_distance, min(best_size, self.core)

    def size(self, x: float, y: float) -> float:
        return self.query(x, y)[1]

    def distance(self, x: float, y: float) -> float:
        return self.query(x, y)[0]


def _segment_distance(x, y, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    if length <= 0.0:
        return math.hypot(x - ax, y - ay)
    t = ((x - ax) * dx + (y - ay) * dy) / length
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def _path(model: MeshModel, current: EdgeKey, minimum: int) -> list[tuple[float, float]]:
    geometry = model.edge_geometry.get(current)
    count = minimum
    if geometry is not None:
        count = max(minimum, 4 * len(geometry.points))
    elif minimum > 1:
        count = 1
    fractions = tuple(index / count for index in range(count + 1))
    return list(model.edge_points(current, fractions))


def _turning(points: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for a, b, c in zip(points, points[1:], points[2:]):
        u = (b[0] - a[0], b[1] - a[1])
        v = (c[0] - b[0], c[1] - b[1])
        if u == (0.0, 0.0) or v == (0.0, 0.0):
            continue
        total += abs(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))
    return total


# ---------------------------------------------------------------------------
# Demands
# ---------------------------------------------------------------------------


@dataclass
class _Edge:
    key: EdgeKey
    length: float
    normal: bool
    demand: float            # cells the field asks for along the edge
    end_sizes: tuple[float, float]  # wanted cell size at key[0], key[1]
    peak: float              # the largest wanted size inside
    peak_fraction: float     # where, as a length fraction from key[0]


def _measure_edge(model: MeshModel, current: EdgeKey, field: SizeField, options: SizeOptions) -> _Edge:
    length = model.edge_length(current)
    samples = max(8, min(200, math.ceil(length / (0.25 * options.core_size))))
    geometry = model.edge_geometry.get(current)
    if geometry is not None:
        samples = max(samples, 4 * len(geometry.points))
    fractions = tuple(index / samples for index in range(samples + 1))
    points = model.edge_points(current, fractions)
    queried = [field.query(x, y) for x, y in points]
    sizes = [size for _distance, size in queried]
    # Away from a wall when the wall distance varies along the edge by at
    # least half its length (both ways for a wall-to-wall strip); beyond the
    # field's reach the distance no longer matters.
    distances = [min(distance, field.reach) for distance, _size in queried]
    variation = 0.0
    within = 0.0
    for (x0, y0), (x1, y1), d0, d1 in zip(points, points[1:], distances, distances[1:]):
        if d0 < field.reach or d1 < field.reach:
            within += math.hypot(x1 - x0, y1 - y0)
            variation += abs(d1 - d0)
    # Only the part of the edge within the field's reach counts: a far-field
    # line fifteen chords long that leaves a band is graded like a spoke.
    normal = within > 0.0 and variation >= NORMAL_SLOPE * within
    if not normal and options.wall_aspect is not None:
        floor = min(options.core_size, options.wall_aspect * options.first_width)
        turning = _turning(points)
        if turning > 0.0:
            floor = min(floor, length / turning * MAX_CELL_TURNING)
        sizes = [max(value, floor) for value in sizes]
    # Integrate ds / size exactly for a size linear on each piece.
    demand = 0.0
    arc = [0.0]
    for (x0, y0), (x1, y1), s0, s1 in zip(points, points[1:], sizes, sizes[1:]):
        piece = math.hypot(x1 - x0, y1 - y0)
        arc.append(arc[-1] + piece)
        if abs(s1 - s0) <= 1.0e-12 * max(s0, s1):
            demand += piece / s0
        else:
            demand += piece * math.log(s1 / s0) / (s1 - s0)
    peak_index = max(range(len(sizes)), key=lambda index: (sizes[index], -abs(index - samples / 2)))
    total = arc[-1] or 1.0
    return _Edge(
        current, length, normal, demand, (sizes[0], sizes[-1]),
        sizes[peak_index], arc[peak_index] / total,
    )


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


def cells_for_series(first: float, length: float, ratio: float) -> int:
    if first <= 0.0 or length <= first:
        return 1
    if ratio <= 1.0 + 1.0e-12:
        return int(math.ceil(length / first))
    return int(math.ceil(math.log(1.0 + length * (ratio - 1.0) / first) / math.log(ratio)))


def _components(model: MeshModel, edges: Iterable[EdgeKey]) -> list[list[EdgeKey]]:
    seen: set[EdgeKey] = set()
    found = []
    for current in edges:
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
    field = SizeField(working, options)
    note = "" if field.has_walls else (
        "no patch of type wall: every cell is sized at core_size; set a patch's "
        "type to wall to grade towards it"
    )
    splits: list[dict] = []
    refused: list[str] = []
    if options.split and field.has_walls:
        for _round in range(SPLIT_ROUNDS):
            if not _split_two_ended(working, field, options, splits, refused, blocks, bbox):
                break
    scope = _scope_edges(working, blocks, bbox)
    measured = {current: _measure_edge(working, current, field, options) for current in working.edges()}
    components = _components(working, sorted(measured))
    sized = [members for members in components if any(member in scope for member in members)]
    targets = []
    for members in sized:
        demands = [measured[member].demand for member in members]
        if options.count_rule == "max":
            target = max(demands)
        else:
            weights = [measured[member].length for member in members]
            total = sum(weights)
            target = sum(w * d for w, d in zip(weights, demands)) / total if total > 0.0 else 1.0
        targets.append(target)
    factor = _budget_factor(working, sized, targets, options)
    counts: dict[EdgeKey, int] = dict(working.edge_cells)
    for members, target in zip(sized, targets):
        value = int(min(options.maximum_cells, max(options.minimum_cells, round(target * factor))))
        for member in members:
            counts[member] = value
    graded, raised, mismatches, mismatch_count = _grade(
        working, measured, counts, scope, options
    )
    # Write the result: counts on every sized component, gradings on the
    # edges in scope.
    for current, value in counts.items():
        working.edge_cells[current] = value
    for current in scope:
        ratio = graded.get(current)
        if ratio is None or abs(ratio - 1.0) <= 1.0e-12:
            working.edge_grading.pop(current, None)
        else:
            working.edge_grading[current] = ratio
    working.validate()
    from .quality import assess_quality

    report = assess_quality(working)
    result = SizeResult(
        total_cells=_total_cells(working),
        cells_before=cells_before,
        components=len(sized),
        raised=raised,
        splits=splits,
        refused=refused,
        mismatches=mismatches[:16],
        mismatch_count=mismatch_count,
        bands=_bands(working, measured),
        worst_interface=report.summary().get("max_interface_size_ratio", 1.0),
        worst_aspect=report.summary().get("max_cell_aspect_ratio", 1.0),
        unsynchronized_links=sum(
            1 for link in working.spacing_links if not working.spacing_link_is_synchronized(link)
        ),
        budget_factor=factor,
        note=note,
    )
    _replace(model, working)
    return result


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
            min(options.maximum_cells, max(options.minimum_cells, round(target * factor)))
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


def _split_two_ended(model, field, options, splits, refused, blocks, bbox) -> bool:
    """Split the first two-ended strip found; ``True`` when one was split."""
    scope = _scope_edges(model, blocks, bbox)
    done: set[EdgeKey] = set()
    for current in sorted(scope):
        if current in done:
            continue
        measured = _measure_edge(model, current, field, options)
        ends = max(measured.end_sizes)
        if measured.peak < SPLIT_RATIO * ends or not (0.05 < measured.peak_fraction < 0.95):
            continue
        component = model.edge_constraint_component(current)
        done.update(component)
        saved = from_data(to_data(model))
        try:
            for member in component:
                model.edge_cells[member] = SPLIT_RESOLUTION
                model.edge_grading.pop(member, None)
            split = model.split_edge(current, measured.peak_fraction)
        except (TopologyError, ValueError) as exc:
            model.__dict__.update(saved.__dict__)
            refused.append(
                f"two-ended strip of {'-'.join(current)} (size {ends:.3g} at its ends, "
                f"{measured.peak:.3g} inside) not split: {exc}"
            )
            continue
        splits.append({
            "edge": "-".join(current),
            "fraction": measured.peak_fraction,
            "end_size": ends,
            "peak_size": measured.peak,
            "new_vertices": list(split.split_vertex_ids),
            "new_blocks": list(split.new_block_ids),
        })
        return True
    return False


def _grade(model, measured, counts, scope, options):
    """One geometric series per wall-normal edge, matched across interfaces.

    Returns canonical total ratios per edge, the components raised, and the
    interface mismatches left.
    """
    normal = {current for current, item in measured.items() if item.normal}
    lengths = {current: item.length for current, item in measured.items()}
    cap = options.interface_growth
    raised: dict[int, tuple[int, int]] = {}
    components = _components(model, sorted(measured))
    component_of = {member: index for index, members in enumerate(components) for member in members}
    mismatches: list[dict] = []
    fixed: dict[EdgeKey, tuple[str, float, float]] = {}
    for _round in range(8):
        fixed, bumps, mismatches = _propagate(
            model, measured, counts, normal, lengths, component_of, cap, options
        )
        if not bumps:
            break
        for index, cells in bumps.items():
            before = counts[components[index][0]]
            if cells <= before:
                continue
            for member in components[index]:
                counts[member] = cells
            raised[index] = (raised.get(index, (before, cells))[0], cells)
    if UNIFY == "always" or (UNIFY == "interface" and cap is not None):
        # Matched across interfaces, the two long sides of a block can start
        # a hundred times apart (a wake strip read 88 degrees).
        fixed = _unify_opposite(model, measured, counts, normal, lengths, fixed)
    graded: dict[EdgeKey, float] = {}
    for current, (start, width, ratio) in fixed.items():
        cells = counts[current]
        if cells < 2 or abs(ratio - 1.0) < 1.0e-12:
            continue
        total = ratio ** (cells - 1)
        graded[current] = total if start == current[0] else 1.0 / total
    raised_list = [
        {"edges": ["-".join(item) for item in components[index][:4]], "from": before, "to": after}
        for index, (before, after) in sorted(raised.items())
    ]
    mismatches.sort(key=lambda item: -item["ratio"])
    return graded, raised_list, mismatches, len(mismatches)


def _unify_opposite(model, measured, counts, normal, lengths, fixed):
    """Give the opposite wall-normal sides of a block one series.

    Two long sides of one block graded from different first widths shear
    every cell between them: a wake strip's two lines, one starting at the
    trailing edge at a thousandth and one at the band front at a tenth,
    read 88 degrees in the sampled check. Opposite wall-normal sides whose
    lengths are within ``OPPOSITE_LENGTH_RATIO`` are grouped (through every
    block they share), each grouped edge is graded from its end nearer a
    wall, and the group takes the geometric mean of its members' first
    widths. This runs after the propagation and feeds no count back: fed
    back, the shared fractions shrank the first cells round by round.

    Orienting the group through the blocks instead of by the field, or
    grouping a wall side with the spoke opposite it, measured worse: the
    fine 30P30N kept 153 faces above 70 degrees after the polish against
    none, and grading wall sides put 5e-5 cells along its walls. The one
    case it costs is a block in a wall corner, uniform on its two wall
    sides and graded on its spokes (the backward step's foot, 19.6 degrees).
    """
    parent: dict[EdgeKey, EdgeKey] = {}

    def find(item):
        while parent.setdefault(item, item) != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for block in model.blocks:
        keys = [edge_key(*block.directed_edge(index)) for index in range(4)]
        for first, second in ((keys[0], keys[2]), (keys[1], keys[3])):
            if first in normal and second in normal and first in fixed and second in fixed:
                a, b = lengths[first], lengths[second]
                if max(a, b) <= OPPOSITE_LENGTH_RATIO * min(a, b):
                    parent[find(first)] = find(second)
    groups: dict[EdgeKey, list[EdgeKey]] = {}
    for current in sorted(parent):
        groups.setdefault(find(current), []).append(current)
    result = dict(fixed)
    for members in groups.values():
        if len(members) < 2:
            continue
        starts = {}
        logs = []
        for current in members:
            item = measured[current]
            start = current[0] if item.end_sizes[0] <= item.end_sizes[1] else current[1]
            starts[current] = start
            v, w, ratio = fixed[current]
            cells = counts[current]
            first = w if v == start else w * ratio ** max(cells - 1, 0)
            logs.append(math.log(max(first, 1.0e-300)))
        shared = math.exp(sum(logs) / len(logs))
        for current in members:
            ratio = series_ratio(shared, lengths[current], counts[current])
            if ratio < 1.0:
                ratio = 1.0
            result[current] = (starts[current], shared, ratio)
    return result


def _leaving(model: MeshModel, current: EdgeKey, vertex: str) -> complex:
    fraction = 1.0e-3 if current[0] == vertex else 1.0 - 1.0e-3
    ((x, y),) = model.edge_points(current, (fraction,))
    here = model.vertices[vertex]
    step = complex(x - here.x, y - here.y)
    return step / abs(step) if abs(step) > 0.0 else step


def _propagate(model, measured, counts, normal, lengths, component_of, cap, options):
    """The finest-first width propagation over the wall-normal edges."""
    walls = _wall_vertices(model)
    # at_vertex[v][s]: (block position, edge adjacent to s at v) for each block.
    at_vertex: dict[str, dict[EdgeKey, list[tuple[int, EdgeKey]]]] = {}
    opposite: dict[tuple[EdgeKey, str], list[tuple[EdgeKey, str]]] = {}
    for position, block in enumerate(model.blocks):
        keys = [edge_key(*block.directed_edge(index)) for index in range(4)]
        corners = block.vertices
        for k in range(4):
            v = corners[k]
            before = keys[(k - 1) % 4]
            after = keys[k]
            at_vertex.setdefault(v, {}).setdefault(before, []).append((position, after))
            at_vertex.setdefault(v, {}).setdefault(after, []).append((position, before))
            a, b = corners[k], corners[(k + 1) % 4]
            d, c = corners[(k - 1) % 4], corners[(k + 2) % 4]
            opposite.setdefault((keys[k], a), []).append((keys[(k + 2) % 4], d))
            opposite.setdefault((keys[k], b), []).append((keys[(k + 2) % 4], c))
    links: dict[tuple[EdgeKey, str], set[tuple[EdgeKey, str]]] = {}
    if cap is not None:
        directions: dict[tuple[EdgeKey, str], complex] = {}

        def leaving(current, v):
            key = (current, v)
            if key not in directions:
                directions[key] = _leaving(model, current, v)
            return directions[key]

        for v, by_side in sorted(at_vertex.items()):
            for _side, items in sorted(by_side.items()):
                for i in range(len(items)):
                    if items[i][1] not in normal:
                        continue
                    best, best_dot = None, math.cos(LINK_ANGLE)
                    for j in range(len(items)):
                        if i == j or items[i][0] == items[j][0] or items[j][1] not in normal:
                            continue
                        dot = (leaving(items[i][1], v).conjugate() * leaving(items[j][1], v)).real
                        if dot < best_dot:
                            best, best_dot = items[j][1], dot
                    if best is not None:
                        links.setdefault((items[i][1], v), set()).add((best, v))
                        links.setdefault((best, v), set()).add((items[i][1], v))
    queue: list[tuple] = []
    order = 0
    # The seeds: a wall-normal edge leaving a wall vertex starts at the
    # wall's first width (the band spokes, a wake line from a trailing
    # edge); every wall-normal edge also offers its uniform width. Seeding
    # every wall-normal edge at the field's size instead graded the two
    # sides of a block from different widths and sheared the backward
    # step's rectangles to 61 degrees.
    for current in sorted(normal):
        item = measured[current]
        at_wall = [index for index, v in enumerate(current) if v in walls]
        if SEEDS == "field" and len(at_wall) != 1:
            at_wall = [0 if item.end_sizes[0] <= item.end_sizes[1] else 1]
        if len(at_wall) == 1:
            end = at_wall[0]
            heapq.heappush(queue, (item.end_sizes[end], 0, order, current, current[end])); order += 1
        uniform = lengths[current] / max(counts[current], 1)
        for v in current:
            heapq.heappush(queue, (uniform, 1, order, current, v)); order += 1
    width: dict[tuple[EdgeKey, str], float] = {}
    actual: dict[tuple[EdgeKey, str], float] = {}
    fixed: dict[EdgeKey, tuple[str, float, float]] = {}
    bumps: dict[int, int] = {}
    limit = cap if cap is not None else math.inf
    while queue:
        w, soft, _order, current, v = heapq.heappop(queue)
        if (current, v) in width:
            continue
        other = current[0] if current[1] == v else current[1]
        width[(current, v)] = w
        if current not in fixed:
            cells = counts[current]
            length = lengths[current]
            clamped = False
            if soft == 1:
                ratio = 1.0
            else:
                ratio = series_ratio(w, length, cells)
                if ratio < 1.0:
                    # More cells than the end asks for: uniform cells finer
                    # than wanted everywhere, never a series shrinking away
                    # from the fine end (a 0.8 ratio over 29 cells put a
                    # first cell of 6e-6 beside one of 0.003 on 30P30N).
                    ratio = 1.0
                    w = length / max(cells, 1)
                    width[(current, v)] = w
                elif ratio > limit + 1.0e-9:
                    needed = cells_for_series(w, length, limit)
                    if needed > cells and cells < options.maximum_cells:
                        index = component_of[current]
                        bumps[index] = max(bumps.get(index, 0), min(needed, options.maximum_cells))
                    ratio = limit
                    clamped = True
            fixed[current] = (v, w, ratio)
            far = w * ratio ** max(cells - 1, 0) if cells > 1 else w
            actual[(current, v)] = w
            actual[(current, other)] = far
            if soft != 1 and cells > 1 and not clamped:
                for opposite_edge, opposite_vertex in opposite.get((current, v), ()):
                    if (opposite_edge, opposite_vertex) in width or opposite_edge in fixed \
                            or opposite_edge not in normal:
                        continue
                    opposite_length = lengths[opposite_edge]
                    if max(opposite_length, length) > OPPOSITE_LENGTH_RATIO * min(opposite_length, length):
                        continue
                    opposite_cells = counts[opposite_edge]
                    if abs(ratio - 1.0) < 1.0e-12:
                        first = opposite_length / max(opposite_cells, 1)
                    else:
                        first = opposite_length * (ratio - 1.0) / (ratio ** opposite_cells - 1.0)
                    heapq.heappush(queue, (first, 2, order, opposite_edge, opposite_vertex)); order += 1
            if (current, other) not in width and not clamped:
                width[(current, other)] = far
                for linked in links.get((current, other), ()):
                    if linked not in width:
                        heapq.heappush(queue, (far, 2, order, linked[0], linked[1])); order += 1
        for linked in links.get((current, v), ()):
            if linked not in width:
                heapq.heappush(queue, (w, 2, order, linked[0], linked[1])); order += 1
    mismatches = []
    if cap is not None:
        for (current, v), w in sorted(actual.items()):
            for linked in sorted(links.get((current, v), ())):
                w2 = actual.get(linked)
                if w2 is not None and current < linked[0] and max(w, w2) > cap * min(w, w2) * (1.0 + 1.0e-6):
                    mismatches.append({
                        "edges": ["-".join(current), "-".join(linked[0])],
                        "vertex": v,
                        "widths": [w, w2],
                        "ratio": max(w, w2) / min(w, w2),
                    })
    return fixed, bumps, mismatches


def _wall_vertices(model: MeshModel) -> set[str]:
    walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    return {v for current, name in model.edge_boundaries.items() if name in walls for v in current}


def _bands(model: MeshModel, measured: dict) -> list[dict]:
    """Per wall patch: the wall-normal edges leaving it, their cells and first widths."""
    walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    by_patch: dict[str, set[str]] = {}
    for current, name in model.edge_boundaries.items():
        if name in walls:
            by_patch.setdefault(name, set()).update(current)
    bands = []
    for name in sorted(by_patch):
        vertices = by_patch[name]
        cells = []
        firsts = []
        for current, item in measured.items():
            if not item.normal or current not in model.edge_cells:
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
