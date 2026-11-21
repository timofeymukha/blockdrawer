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
   folds a cell, adds a face above 70 degrees, raises the worst face past
   the field's, takes the skewness above 4 or takes cells past checkMesh's
   aspect ratio of 1000 is undone. Before spending, a block the field
   leaves above skewness 4 or with faces above 70 degrees is repaired by
   the same moves, and a block above aspect ratio 1000 first by releasing
   a strip through it towards its members' own gradings (``grading=field``'s
   series), which carries no wall width to where the field wants long
   cells. The spent state is verified against the
   repaired field state at 256 cells a side (real counts where faces are
   steep) and spending is undone where it added faces; both verdicts are
   reported, measured at the real counts (every cell). Strips the spent state leaves coarse between finer neighbours
   at both ends are split and the budget is spent again when that lowers
   the jumps.

The two edges of a ``cyclic`` pair (:mod:`blockdrawer.cyclic`) are one
count component and one strip here, oriented so the shared ratio runs the
same physical way on both, so a sized pair meshes face by face. The model's
own count components do not join a pair, so a strip that holds a cyclic
edge is not split (its partner would not be).

With ``grading=field`` (the default; ``grading=strip`` leaves it out) a
last pass gives each strip member its own series at the final count
(``_FieldGrading``): a wall spoke starts at the first width asked, an edge
far from walls relaxes to what the field wants there, within the shear,
interface-jump and sampled-grid limits of ``_FieldGrading.settle``; a strip
that would raise the session's average non-orthogonality by more than
``FIELD_MEAN_RISE`` and buy no first width, jump or worst face keeps one
series (the long sides of thin blocks); counts are not changed.

Counts and gradings are written directly: spacing links are kept and the
ones left out of sync are reported. Everything is deterministic and applied
as one validated edit; on any failure the model is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from typing import Any, Sequence

from .cyclic import CyclicMatch, cyclic_pairs
from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .session import from_data, to_data
from .sampling import ASPECT_LIMIT as _ASPECT_LIMIT, aspect_worse
from .smooth import VERIFY_CELLS  # the verification's resolution, smooth's


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
# checkMesh fails a mesh above this face skewness.
SKEWNESS_LIMIT = 4.0
# ... and above this cell aspect ratio (sampling.ASPECT_LIMIT, 1000).
ASPECT_LIMIT = _ASPECT_LIMIT
# The per-move guard's resolution: smooth's verification resolution. At 64
# a side it was blind to blocks of hundreds of cells (the fine 30P30N gained
# 7 and 53 faces above 70 degrees); at 256 the fine 30P30N sizes in 259 s
# against 212 s.
GUARD_CELLS = VERIFY_CELLS
# No strip is graded beyond this log total ratio (a refit at hundreds of
# cells asked for exp(800)).
MAX_LOG_RATIO = 50.0
# The returned state is verified at smooth's resolution against the
# field's repaired state: no more faces above 70 degrees, the worst face
# at most this many degrees higher.
VERIFY_TOLERANCE = 0.5
# Blocks whose worst face exceeds this at 256 a side are verified at their
# real counts.
HOT_ANGLE = 65.0
VERIFY_ROUNDS = 40
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
# grading=field (_FieldGrading): two opposite sides of a block in one strip
# may be graded apart only so far that the lines across the block tilt by
# at most this angle (node displacement along the sides over the block's
# height), and the sampled guard lets no block's worst face pass it (or the
# shared state's worst, if lower) unless it was there already. At 20 a
# column from a blunt base kept 20.4 degrees; it read 50 without the guard.
SHEAR_ANGLE = 20.0
# Node indices the displacement is measured at, at most.
SHEAR_SAMPLES = 64
# grading=field may not leave an interface jump above this (the quality
# screen's first count) or above the spent state's worst where it touches.
JUMP_LIMIT = 2.5
# How far a strip goes from its shared ratio to its members' own, tried in
# turn: all the way, half, a quarter.
FIELD_MODES = (1.0, 0.5, 0.25)
# Rounds of lowering bounds to the partners' widths (_FieldGrading.tighten).
TIGHTEN_ROUNDS = 20
# grading=field shears no block for nothing (``_FieldGrading.settle``): a
# strip's move that raises the session's average face non-orthogonality
# (checkMesh's measure, sampled: the rise over its blocks and their
# neighbours times their share of the session's faces) by more than this
# many degrees is taken only when it buys something - a band spoke's first
# height nearer the one asked by FIELD_GAIN of it, fewer interface jumps
# above JUMP_LIMIT at its members' ends, or a worst face there lower by more
# than VERIFY_TOLERANCE. A U-bend's band and core blocks, 5 long and 0.1 to
# 0.5 thick, bought none of these and raised the session's average by 4.2
# degrees a strip (checkMesh 11.7 where one ratio per strip gives 3.3).
# Measured on the moves the pass took before this guard: a NACA 4412 wake
# strip's quarter step raised its 20 blocks' average by 1.6 degrees and the
# session's by 0.51; a limit on the blocks' own average refused it and the
# polished maximum ended at 62.2 degrees for 45.1, so the limit is on the
# session's average, at the benchmark's own per-session tolerance of a
# degree. None leaves the guard out.
FIELD_MEAN_RISE = 1.0
FIELD_GAIN = 0.01


# checkMesh's aspect ratio limit as size keeps it (shared with smooth's polish).
_aspect_worse = aspect_worse


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
    # "directional" (this module) or "distance" (item 3's wall-distance
    # sizing, size_distance.py).
    method: str = "directional"
    # "field" (the default since its corpus measurement, README): each
    # member is graded by the field where it lies, within the strip's
    # count and SHEAR_ANGLE of its neighbours (_FieldGrading); "strip":
    # every member of a strip keeps the one total ratio spending left.
    grading: str = "field"
    # Cell-size caps in boxes: (xmin, ymin, xmax, ymax, hx, hy), the largest
    # cell extent in x and in y there (math.inf: no cap that way). See
    # SizeField.cap_size and _Edge.cap.
    caps: tuple[tuple[float, float, float, float, float, float], ...] = ()

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
        if self.method not in ("directional", "distance"):
            raise TopologyError("method must be directional or distance")
        if self.method == "distance" and self.caps:
            raise TopologyError("cap boxes apply to method=directional only")
        if self.method == "distance" and self.keep_counts:
            raise TopologyError("keep_counts applies to method=directional only")
        if self.grading not in ("strip", "field"):
            raise TopologyError("grading must be strip or field")
        for xmin, ymin, xmax, ymax, hx, hy in self.caps:
            if not (xmin < xmax and ymin < ymax):
                raise TopologyError(f"a cap box needs xmin < xmax and ymin < ymax, got "
                                    f"{xmin:g},{ymin:g},{xmax:g},{ymax:g}")
            if not (hx > 0.0 and hy > 0.0) or (math.isinf(hx) and math.isinf(hy)):
                raise TopologyError("a cap needs a positive size")
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
    verification: dict = field(default_factory=dict)
    splits: list[dict] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    jumps: dict[str, Any] = field(default_factory=dict)
    worst_jumps: list[dict] = field(default_factory=list)
    bands: list[dict] = field(default_factory=list)
    worst_interface: float = 1.0
    worst_aspect: float = 1.0
    unsynchronized_links: int = 0
    budget_factor: float = 1.0
    # True when the budget stopped spending (or coarsened the field): a
    # larger one would buy more continuity. False: spending ran out of
    # moves that lower the jump cost (or the guard refused them) first.
    budget_binding: bool | None = None
    # grading=field: how many strips took their members' own gradings,
    # half of them, or kept the shared ratio (None with grading=strip).
    field_grading: dict | None = None
    # The components spending raised, the most cells first ("EDGE A->B +N").
    raised: list[str] = field(default_factory=list)
    # The field's cells solved again after continuity splits (None: none).
    field_cells_after_splits: int | None = None
    # Per cap box: sampling.cell_extents of the result and the caps asked.
    caps: list[dict] = field(default_factory=list)
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
            "verification": self.verification,
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
            "budget_binding": self.budget_binding,
            "field_grading": self.field_grading,
            "raised": self.raised,
            "field_cells_after_splits": self.field_cells_after_splits,
            "caps": self.caps,
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
        self.caps = tuple(options.caps)
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

    def cap_size(self, x: float, y: float, ux: float, uy: float, strict: bool = False) -> float:
        """The size the cap boxes holding (x, y) allow along the unit
        direction u (inf: none). A box capping the extent in x at hx and in
        y at hy allows 1 / sqrt((ux / hx)^2 + (uy / hy)^2) - hy along y, hx
        along x, more along a slanted edge, whose cells reach the cap's
        extent over a longer length - the walls' own ellipse form.
        ``strict``: inside the box, not on its sides (within 1e-9 of its
        size)."""
        best = math.inf
        for xmin, ymin, xmax, ymax, hx, hy in self.caps:
            if strict:
                tx, ty = 1.0e-9 * (xmax - xmin), 1.0e-9 * (ymax - ymin)
                inside = xmin + tx < x < xmax - tx and ymin + ty < y < ymax - ty
            else:
                inside = xmin <= x <= xmax and ymin <= y <= ymax
            if not inside:
                continue
            q = (ux / hx) ** 2 + (uy / hy) ** 2
            if q > 0.0:
                best = min(best, 1.0 / math.sqrt(q))
        return best

    def size(self, x: float, y: float, ux: float, uy: float) -> float:
        """The smallest size any wall or cap box asks for at (x, y) along the unit direction u."""
        if self.caps:
            return min(self._wall_size(x, y, ux, uy), self.cap_size(x, y, ux, uy))
        return self._wall_size(x, y, ux, uy)

    def _wall_size(self, x: float, y: float, ux: float, uy: float) -> float:
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
    # The smallest size a cap box allows along the edge, for an edge of a
    # block with cells in the box (inf: none; ``_block_caps``). A block's
    # cells take their widths from its two opposite edges (transfinite
    # interpolation), and one geometric series has cell widths linear in the
    # distance along the edge, so the cells inside the box stay within the
    # cap when both end widths of both edges do: both ends are bounded by it
    # (_field_seeds), whether or not they lie in the box.
    cap: float = math.inf


def _sample_edge(model: MeshModel, current: EdgeKey, field: SizeField, options: SizeOptions) -> _Edge:
    length = model.edge_length(current)
    samples = max(8, min(MAX_SAMPLES, math.ceil(length / (0.25 * options.core_size))))
    geometry = model.edge_geometry.get(current)
    if geometry is not None:
        samples = max(samples, min(MAX_SAMPLES, 4 * len(geometry.points)))
    fractions = tuple(index / samples for index in range(samples + 1))
    points = model.edge_points(current, fractions)
    if field.caps:
        # Sample where the edge enters, crosses and leaves a cap box, so a
        # box thinner than the sample spacing is not stepped over.
        extra = _cap_fractions(points, fractions, field.caps)
        if extra:
            fractions = tuple(sorted(set(fractions) | extra))
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


def _block_caps(model: MeshModel, edges: dict, caps) -> None:
    """Give every edge of a block with cells in a cap box the cap along its
    direction (``_Edge.cap``). A block has cells in a box when a node of
    its count-free 16 x 16 map lies strictly inside the box or a box corner
    lies inside the block's outline (a box smaller than one map cell)."""
    from .sampling import block_grid

    for block in model.blocks:
        nodes = block_grid(model, block, uniform=16).nodes
        outline = ([point for point in nodes[0]] + [row[-1] for row in nodes[1:]]
                   + [point for point in reversed(nodes[-1][:-1])] + [row[0] for row in reversed(nodes[1:-1])])
        hit = []
        for box in caps:
            xmin, ymin, xmax, ymax = box[:4]
            tx, ty = 1.0e-9 * (xmax - xmin), 1.0e-9 * (ymax - ymin)
            inside = any(xmin + tx < x < xmax - tx and ymin + ty < y < ymax - ty
                         for row in nodes for x, y in row)
            if not inside:
                inside = any(_in_polygon(corner, outline)
                             for corner in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)))
            if inside:
                hit.append(box)
        if not hit:
            continue
        for index in range(4):
            current = edge_key(*block.directed_edge(index))
            item = edges[current]
            points = model.edge_points(current, tuple(k / 8 for k in range(9)))
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                norm = math.hypot(x1 - x0, y1 - y0)
                if norm <= 0.0:
                    continue
                ux, uy = (x1 - x0) / norm, (y1 - y0) / norm
                for _xmin, _ymin, _xmax, _ymax, hx, hy in hit:
                    q = (ux / hx) ** 2 + (uy / hy) ** 2
                    if q > 0.0:
                        item.cap = min(item.cap, 1.0 / math.sqrt(q))


def _in_polygon(point, outline) -> bool:
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _cap_fractions(points, fractions, caps) -> set[float]:
    """Edge fractions where the sampled path enters, crosses the middle of
    and leaves each cap box (Liang-Barsky clipping of each sample segment)."""
    found: set[float] = set()
    for (x0, y0), (x1, y1), f0, f1 in zip(points, points[1:], fractions, fractions[1:]):
        dx, dy = x1 - x0, y1 - y0
        for xmin, ymin, xmax, ymax, _hx, _hy in caps:
            low, high = 0.0, 1.0
            for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
                if p == 0.0:
                    if q < 0.0:
                        low, high = 1.0, 0.0
                        break
                    continue
                t = q / p
                if p < 0.0:
                    low = max(low, t)
                else:
                    high = min(high, t)
            if low < high:
                for t in (low, 0.5 * (low + high), high):
                    found.add(f0 + (f1 - f0) * t)
    return {value for value in found if 0.0 < value < 1.0}


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
    transverse edges: the pairs the quality screen's size jump compares
    (:meth:`MeshModel.interface_pairs`)."""
    links: dict[tuple[EdgeKey, str], list[tuple[EdgeKey, str]]] = {}
    for _shared, vertex, first_edge, second_edge in model.interface_pairs():
        first, second = (first_edge, vertex), (second_edge, vertex)
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


def _cyclic_matches(model: MeshModel) -> list[CyclicMatch]:
    """The partner edges of every cyclic pair that corresponds edge by edge
    (one that does not is left alone: export refuses it with the reason)."""
    if not any(boundary.kind == "cyclic" for boundary in model.boundaries.values()):
        return []
    try:
        pairs = cyclic_pairs(model, check_nodes=False)
    except TopologyError:
        return []
    return [match for pair in pairs for match in pair.matches]


def count_components(model: MeshModel) -> list[list[EdgeKey]]:
    """The opposite-edge count components, those holding the two edges of
    a cyclic pair joined: a pair must keep one count. Each component is
    one count; ``smooth``'s shape mode limits their length spread."""
    seen: set[EdgeKey] = set()
    found = []
    for current in sorted(model.edge_cells):
        if current in seen:
            continue
        members = sorted(model.edge_constraint_component(current))
        seen.update(members)
        found.append(members)
    matches = _cyclic_matches(model)
    if not matches:
        return found
    parent = list(range(len(found)))
    index_of = {member: index for index, members in enumerate(found) for member in members}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for match in matches:
        a, b = root(index_of[match.first]), root(index_of[match.second])
        if a != b:
            parent[max(a, b)] = min(a, b)
    joined: dict[int, list[EdgeKey]] = {}
    for index, members in enumerate(found):
        joined.setdefault(root(index), []).extend(members)
    return [sorted(members) for _root, members in sorted(joined.items())]


_components = count_components


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
    if options.method == "distance":
        return _size_by_distance(model, options, blocks, bbox)
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
    skew_floor, fold_floor = _floors(model)
    if options.keep_counts:
        # The counts stay: spending may regrade strips for continuity but
        # pays for no raise (its budget is the cells there are), so the
        # regrades an earlier size made are made again, not discarded.
        state, moves, guard = _spend(working, state, options, field_cells, skew_floor, fold_floor)
        state["budget_binding"] = None
    else:
        budget = options.budget if options.budget else int(round(DEFAULT_BUDGET * field_cells))
        state, moves, guard = _spend(working, state, options, budget, skew_floor, fold_floor)
        spent_cells = _state_cells(working, state)
        splits_before = len(splits)
        if options.split:
            state, moves, guard = _continuity_splits(
                working, field_, state, moves, guard, options, budget, blocks, bbox, splits, refused,
                skew_floor, fold_floor,
            )
    field_grading = None
    if options.grading == "field":
        state, field_grading = _field_pass(working, state, options)
    _write(working, state, options)
    folded = state.get("verification", {}).get("result", {}).get("folded_blocks") or []
    if folded and fold_floor == 0:
        raise TopologyError(_fold_refusal(working, folded))
    skewness = _max_skewness(working)
    if skewness > SKEWNESS_LIMIT >= skew_floor:
        raise TopologyError(
            f"size refused: the sized mesh's sampled face skewness {skewness:.3g} exceeds "
            f"checkMesh's limit of {SKEWNESS_LIMIT:g} where the input's was {skew_floor:.3g}; "
            "the block it names is too tapered for the counts the field gives it - raise "
            "core_size's resolution there, split the block or keep the counts"
        )
    result = _report(working, cells_before, state["components"], splits, refused, state["factor"], note, options)
    result.budget = budget
    result.field_cells = field_cells
    result.field_jump_cost = field_cost
    result.jump_cost = _jump_cost(state, options)
    result.moves = moves
    result.guard = guard
    result.verification = _reported(working, state.get("verification", {}))
    result.budget_binding = state.get("budget_binding")
    result.field_grading = field_grading
    result.raised = _raised(moves)
    if not options.keep_counts and len(splits) > splits_before:
        # The field was solved again on the split blocks: ``field_cells``
        # is the first field's (the budget's base), this is the last one's.
        result.field_cells_after_splits = state.get("field_cells")
        made = [item["edge"] for item in splits[splits_before:]]
        result.note = "; ".join(filter(None, [result.note, (
            f"continuity splits of {', '.join(made[:4])}{' and more' if len(made) > 4 else ''} took the "
            f"cells from {spent_cells:,} after spending to {result.total_cells:,}; the field solved again "
            f"on the split blocks has {state.get('field_cells', 0):,} cells against {field_cells:,} "
            "before them, and the budget was spent again from there: a larger budget can end with "
            "fewer cells than a smaller one"
        )]))
    if options.caps:
        result.caps = _cap_report(working, options)
        over = [item["text"] for item in result.caps if not item["held"]]
        if over:
            result.note = "; ".join(filter(None, [result.note, "cap boxes not held: " + "; ".join(over)]))
    spokes = _spoke_misses(working, options)
    if result.raised:
        result.raised = _raised_through_spokes(working, moves, spokes)
    if field_grading is not None:
        members = field_grading.pop("_refused_members", {})
        off = _off_walls(result.bands, 0.10)
        refused_strips = (field_grading["refused_by_jumps"] + field_grading["refused_by_grid"]
                          + field_grading.get("refused_by_shear", 0))
        cause = (
            "the field pass was dropped (the verification found it worse than the spent state)"
            if field_grading.get("dropped")
            else f"{refused_strips} strip(s) kept their shared ratio (refused by the jump, grid or shear guard)"
            if refused_strips else ""
        )
        if off and cause:
            named = "" if field_grading.get("dropped") else _refused_spokes(members, spokes)
            if not named and not field_grading.get("dropped"):
                # The spokes off are in no refused strip: say where else to look.
                named = "no refused strip holds the spokes off" + (
                    " (a raise through them: raised)"
                    if any("through band spokes" in item for item in result.raised) else "")
            result.note = "; ".join(filter(None, [result.note, (
                f"{cause}: first widths more than 10 percent off the one asked on {'; '.join(off)}"
                + (f" - {named}" if named else "")
            )]))
    field_faces = result.verification.get("field", {}).get("severe_faces", 0)
    if field_faces:
        result.note = "; ".join(filter(None, [result.note, (
            f"the field's own state has {field_faces} face(s) above 70 degrees "
            "(verification.field) that spending does not remove; polish and check "
            "first, then see failures.md for the blocks check names"
        )]))
    high = result.verification.get("result", {}).get("high_aspect_blocks") or []
    if high:
        cells_above = sum(item[2] for item in high)
        named = ", ".join(f"{block} ({ratio:.0f}, {count} cells)" for block, ratio, count in high[:5])
        more = f" and {len(high) - 5} more block(s)" if len(high) > 5 else ""
        result.note = "; ".join(filter(None, [result.note, (
            f"{cells_above} cell(s) above checkMesh's aspect ratio limit of {ASPECT_LIMIT:g} "
            f"left in {named}{more} (verification.result): checkMesh fails; no repair move "
            "lowered them - grade the thin side of each block coarser at the end where its cells "
            "are long (set_edge_grading ... start_width) or raise the count along the long side"
        )]))
    _replace(model, working)
    return result


def _field_pass(working: MeshModel, state: dict, options: SizeOptions) -> tuple[dict, dict]:
    """``grading=field``: after spending, give each strip's members their own
    gradings at the final counts where the sampled grid and the interface
    jumps allow (``_FieldGrading``). Counts and the spent shared ratios are
    the starting point, so the cells are the spent state's. The result is
    verified like spending's; a pass that adds a face above 70 degrees, a
    fold, more than ``VERIFY_TOLERANCE`` on the worst face or skewness past
    4 is dropped whole."""
    grader = _FieldGrading(working, state["strips"], state["edges"], state["bounds"], options, state["ratios"],
                           state.get("released"))
    grader.tighten(state["counts"], state["links"])
    grader.settle(working, state["counts"], state["scope"], state["links"])
    ratios = dict(state["ratios"])
    ratios.update(grader.ratios(state["counts"]))
    trial_state = {**state, "ratios": ratios}
    record = {
        "strips": len(grader.mode),
        "own": sum(1 for mode in grader.mode if mode == 1.0),
        "part": sum(1 for mode in grader.mode if 0.0 < mode < 1.0),
        "shared": sum(1 for mode in grader.mode if mode == 0.0),
        "refused_by_jumps": grader.refused["jump"],
        "refused_by_grid": grader.refused["grid"],
        "refused_by_shear": grader.refused["shear"],
        # Each strip kept on its shared ratio by a guard: one member edge
        # (its smallest) and the guard, "EDGE (jump|grid|shear)".
        "refused_strips": [
            f"{'-'.join(min(grader.strips[index]))} ({reason})"
            for index, reason in sorted(getattr(grader, "refused_strips", {}).items(),
                                        key=lambda item: min(grader.strips[item[0]]))
        ],
        "_refused_members": {index: (reason, sorted(grader.strips[index]))
                             for index, reason in getattr(grader, "refused_strips", {}).items()},
    }
    spent = state.get("verification", {}).get("result")
    if spent is None:
        return trial_state, record
    trial = from_data(to_data(working))
    _write(trial, trial_state, options)
    summary = _verify(trial, _neighbours(trial))[1]
    if (summary["severe_faces"] > spent["severe_faces"]
            or summary["folded_cells"] > spent["folded_cells"]
            or summary["max_non_orthogonality"] > spent["max_non_orthogonality"] + VERIFY_TOLERANCE
            or summary["max_skewness"] > SKEWNESS_LIMIT >= spent["max_skewness"]
            or _aspect_worse(spent["max_aspect_ratio"], spent["high_aspect_cells"],
                             summary["max_aspect_ratio"], summary["high_aspect_cells"])):
        record["dropped"] = "the verification at 256 cells a side found the pass worse than the spent state"
        return state, record
    trial_state["verification"] = {**state["verification"], "result": _brief(summary)}
    return trial_state, record


def _raised(moves: list[dict], largest: int = 3) -> list[str]:
    """The components spending (and the shape repair) raised, the most cells
    first: "EDGE FROM->TO +CELLS" for the ``largest`` few, one entry each."""
    total: dict[str, list] = {}
    for move in moves:
        if move.get("component") is None or "to" not in move or "cells" not in move:
            continue
        item = total.setdefault(move.get("edge", str(move["component"])), [move["from"], move["to"], 0])
        item[1] = move["to"]
        item[2] += move["cells"]
    ranked = sorted(total.items(), key=lambda entry: (-entry[1][2], entry[0]))[:largest]
    return [f"{edge} {first}->{last} +{cells:,}" for edge, (first, last, cells) in ranked]


def _off_walls(bands: list[dict], limit: float) -> list[str]:
    """Wall patches whose first heights at the band spokes leave the one
    asked by more than ``limit`` (a fraction): "PATCH LOW..HIGH for ASKED
    (-X%..+Y%)"."""
    found = []
    for band in bands:
        asked = band.get("asked")
        if not asked or not band.get("first_width"):
            continue
        low, high = band["first_width"]
        if max(abs(low / asked - 1.0), abs(high / asked - 1.0)) > limit:
            found.append(f"{band['patch']} {low:.3g}..{high:.3g} for {asked:.3g} "
                         f"({100.0 * (low / asked - 1.0):+.0f}%..{100.0 * (high / asked - 1.0):+.0f}%)")
    return found


# The note names at most this many strips, and spokes per strip.
NAMED_STRIPS = 4
NAMED_SPOKES = 3
# A spoke is off when its first height leaves the one asked by more than this.
OFF_FRACTION = 0.10


def _spoke_misses(model: MeshModel, options: SizeOptions) -> dict[EdgeKey, tuple[str, float]]:
    """Every band spoke (:func:`blockdrawer.quality.band_spokes`): its wall
    patch and its first height's relative miss of the one asked there."""
    from .quality import band_spokes, spoke_height

    overrides = dict(options.patch_first_widths)
    found = {}
    for current, vertex, name, normal in band_spokes(model)[0]:
        asked = overrides.get(name, options.first_width)
        miss = spoke_height(model, current, vertex, normal) / asked - 1.0
        if current not in found or abs(miss) > abs(found[current][1]):
            found[current] = (name, miss)
    return found


def _refused_spokes(members: dict, spokes: dict[EdgeKey, tuple[str, float]]) -> str:
    """The strips a guard kept on their shared ratio that leave band spokes
    more than ``OFF_FRACTION`` off: "strip of EDGE (jump): A-B -22%, ..."."""
    items = []
    for index, (reason, edges) in members.items():
        off = sorted(((miss, edge) for edge in edges
                      for _name, miss in [spokes.get(edge, ("", 0.0))] if abs(miss) > OFF_FRACTION),
                     key=lambda item: (-abs(item[0]), item[1]))
        if off:
            items.append((-max(abs(miss) for miss, _edge in off), "-".join(min(edges)), reason, off))
    items.sort()
    parts = []
    for _key, edge, reason, off in items[:NAMED_STRIPS]:
        named = ", ".join(f"{'-'.join(spoke)} {100.0 * miss:+.0f}%" for miss, spoke in off[:NAMED_SPOKES])
        more = f" and {len(off) - NAMED_SPOKES} more" if len(off) > NAMED_SPOKES else ""
        parts.append(f"strip of {edge} ({reason}): {named}{more}")
    if not parts:
        return ""
    more = f"; {len(items) - NAMED_STRIPS} more strip(s)" if len(items) > NAMED_STRIPS else ""
    return "the strips and the spokes they leave off: " + "; ".join(parts) + more


def _raised_through_spokes(model: MeshModel, moves: list[dict],
                           spokes: dict[EdgeKey, tuple[str, float]]) -> list[str]:
    """``_raised``'s entries, each followed - where the component it raised
    holds band spokes - by the wall patches and the spokes' first heights
    against the one asked: a raise through a band's spokes gives every spoke
    the component's count, and where the field pass cannot grade each from
    the wall the first width moves with it."""
    component_of = {}
    for members in count_components(model):
        for member in members:
            component_of[member] = members
    named = {"-".join(member): member for member in component_of}
    entries = []
    for text in _raised(moves):
        members = component_of.get(named.get(text.split(" ", 1)[0]), [])
        hit: dict[str, list[float]] = {}
        for member in members:
            if member in spokes:
                name, miss = spokes[member]
                hit.setdefault(name, []).append(miss)
        if hit:
            text += " through band spokes of " + ", ".join(
                f"{name} ({100.0 * min(values):+.0f}%..{100.0 * max(values):+.0f}%)"
                for name, values in sorted(hit.items()))
        entries.append(text)
    return entries


def _cap_report(model: MeshModel, options: SizeOptions) -> list[dict]:
    """Each cap box's largest cell extents in the sized mesh
    (``sampling.cell_extents``: cells whose centre lies in the box, real
    counts) against its caps, and a short text."""
    from .sampling import cell_extents

    report = []
    for xmin, ymin, xmax, ymax, hx, hy in options.caps:
        found = cell_extents(model, (xmin, ymin, xmax, ymax))
        held = found["max_x_extent"] <= hx * (1.0 + 1.0e-6) and found["max_y_extent"] <= hy * (1.0 + 1.0e-6)
        parts = []
        for axis, cap in (("x", hx), ("y", hy)):
            if not math.isinf(cap):
                value = found[f"max_{axis}_extent"]
                at = found[f"{axis}_at"]
                parts.append(f"{axis} {value:.4g} for {cap:g}"
                             + (f" ({found[f'{axis}_block']} at {at[0]:.4g},{at[1]:.4g})"
                                if value > cap * (1.0 + 1.0e-6) and at else ""))
        box = ",".join(f"{value:g}" for value in (xmin, ymin, xmax, ymax))
        report.append({**found, "cap_x": None if math.isinf(hx) else hx,
                       "cap_y": None if math.isinf(hy) else hy, "held": held,
                       "text": f"box {box}: {', '.join(parts)}"})
    return report


def _brief(summary: dict) -> dict:
    """The verdict ``size`` reports for a state (``verification``)."""
    return {
        "severe_faces": summary.get("severe_faces", 0),
        "max_non_orthogonality": summary.get("max_non_orthogonality", 0.0),
        "max_skewness": summary.get("max_skewness", 0.0),
        "folded_cells": summary.get("folded_cells", 0),
        "max_aspect_ratio": summary.get("max_aspect_ratio", 0.0),
        "worst_aspect_block": summary.get("worst_aspect_block"),
        "high_aspect_cells": summary.get("high_aspect_cells", 0),
        "high_aspect_blocks": summary.get("high_aspect_blocks", []),
        "folded_blocks": summary.get("folded_blocks", []),
    }


def _fold_refusal(model: MeshModel, folded: list) -> str:
    """Why ``size`` refuses a result that folds where its input did not:
    the blocks, and the spline edges among theirs that hook back along
    their chord (``model.hooked_edges_text``), the usual cause."""
    from .model import hooked_edges_text

    cells = sum(count for _block, count in folded)
    named = ", ".join(f"{block} ({count})" for block, count in folded[:8])
    more = f" and {len(folded) - 8} more" if len(folded) > 8 else ""
    text = (
        f"size refused: the sized mesh folds {cells} sampled cell(s) in {named}{more} where the "
        "input at its own counts folds none, and no regrade or count raise of those blocks "
        "unfolded them"
    )
    hooked = hooked_edges_text(model, [block for block, _count in folded])
    if hooked:
        text += ": " + hooked + ", then size again"
    else:
        text += "; render the blocks and see failures.md"
    return text


def _neighbours(model: MeshModel) -> dict[str, set[str]]:
    users: dict[EdgeKey, list[str]] = {}
    for block in model.blocks:
        for k in range(4):
            users.setdefault(edge_key(*block.directed_edge(k)), []).append(block.id)
    neighbours: dict[str, set[str]] = {}
    for ids in users.values():
        for block in ids:
            neighbours.setdefault(block, set()).update(ids)
    return neighbours


def _size_by_distance(model, options, blocks, bbox) -> SizeResult:
    """Item 3's sizing (``size_distance``), verified and reported like the
    default: no spending, so the field's state is the result."""
    from . import size_distance

    cells_before = _total_cells(model)
    skew_floor = _max_skewness(model)
    working = from_data(to_data(model))
    old = size_distance.size_model(working, size_distance.SizeOptions(
        first_width=options.first_width,
        core_size=options.core_size,
        growth=options.growth,
        wall_aspect=options.wall_aspect,
        interface_growth=options.interface_growth,
        count_rule=options.count_rule,
        budget=options.budget,
        patch_first_widths=options.patch_first_widths,
        split=options.split,
        minimum_cells=options.minimum_cells,
        maximum_cells=options.maximum_cells,
    ), blocks=blocks, bbox=bbox)
    skewness = _max_skewness(working)
    if skewness > SKEWNESS_LIMIT >= skew_floor:
        raise TopologyError(
            f"size refused: the sized mesh's sampled face skewness {skewness:.3g} exceeds "
            f"checkMesh's limit of {SKEWNESS_LIMIT:g} where the input's was {skew_floor:.3g}"
        )
    brief = _brief(_verify_real(working))
    result = _report(working, cells_before, old.components, old.splits, old.refused, old.budget_factor, old.note,
                     options)
    result.budget = options.budget
    result.field_cells = result.total_cells
    result.verification = {
        "cells_per_side": REPORT_CELLS, "field": brief, "result": dict(brief), "undone_moves": 0,
        "method": "distance",
    }
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
    if field_.caps:
        _block_caps(working, edges, field_.caps)
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
        # A budget below the field's own does not coarsen a component
        # through a cap box: the cap is a requirement, the budget a target.
        capped = [any(edges[member].cap < math.inf for member in members) for members in sized]
        factor = _budget_factor(working, sized, targets, options, capped)
        for members, target, held in zip(sized, targets, capped):
            scale = 1.0 if held else factor
            value = int(min(options.maximum_cells, max(options.minimum_cells, math.ceil(target * scale - 1.0e-9))))
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
        **{(current, current[0]): min(item.field[0], item.cap) for current, item in edges.items()},
        **{(current, current[1]): min(item.field[-1], item.cap) for current, item in edges.items()},
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
            # An edge held by a cap box is fine at both ends because the cap
            # asks it: it is two-ended only when its ends are finer than the
            # cap by SPLIT_RATIO too (a plate face between two walls).
            if (reached and min(profile[peak_index], item.cap) >= SPLIT_RATIO * ends
                    and 0.05 < fraction < 0.95):
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
    # A cyclic pair grades the same physical way: each end of an edge faces
    # the partner's end it maps onto.
    for match in _cyclic_matches(model):
        a, b = match.first, match.second
        ends = ((a[0], b[0]), (a[1], b[1])) if match.aligned else ((a[0], b[1]), (a[1], b[0]))
        for here, there in ends:
            pairs.setdefault((a, here), []).append((b, there))
            pairs.setdefault((b, there), []).append((a, here))
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


def _fractions(log: float, cells: int, indices: Sequence[int]) -> list[float]:
    """Node fractions at ``indices`` of ``cells`` geometric cells of total ratio exp(log)."""
    if cells < 2 or abs(log) < 1.0e-12:
        return [index / cells for index in indices]
    step = log / (cells - 1)
    whole = math.expm1(step * cells)
    return [math.expm1(step * index) / whole for index in indices]


def _shear(first: float, second: float, cells: int, indices: Sequence[int]) -> float:
    """The largest node displacement, as a fraction of the side, between two
    opposite sides of ``cells`` cells graded by the log total ratios given."""
    a = _fractions(first, cells, indices)
    b = _fractions(second, cells, indices)
    return max(abs(x - y) for x, y in zip(a, b))


class _FieldGrading:
    """Each strip member's own grading at a count (``grading=field``).

    ``_ratios`` gives a strip one total ratio, so its members' end widths
    are in proportion to their lengths: on the benchmark sessions the first
    width of every wall spoke over its length was the same to 1e-3 within
    each strip, and a far-field member of the NACA 4412 C-grid's wake strip
    carried the trailing edge's 0.00036 to the far-field corner, where its
    bound was 0.5 and its own series at the same count gives 0.07.

    Here every member gets, at the strip's count, the series that keeps its
    finer-bounded end at its bound and grows from there (uniform when the
    count already makes every cell finer than that bound): a wall spoke
    starts at the first width asked whatever its length, and a member far
    from walls relaxes to what the field wants there. Where the bounds
    cannot both be met at that count the finer end keeps its bound. The
    bounds are the field's, lowered by ``tighten`` to the widths the
    members across each interface get. Two opposite sides of a block graded
    differently shear the cells between them: the lines across the block
    tilt by the nodes' displacement along the sides over the block's
    height. Members are fixed finest bound first, and each is clamped to
    within ``SHEAR_ANGLE`` of tilt of every member already fixed beside it
    - its bounds still win over the clamp. The two edges of a cyclic pair
    are one member. ``settle`` then decides per strip how far it goes from
    the shared ratio (``FIELD_MODES``). Values are log total ratios in each
    strip's orientation, cached per count.
    """

    def __init__(self, model: MeshModel, strips, edges, bounds, options: SizeOptions, shared,
                 fixed: dict[int, dict[EdgeKey, float]] | None = None) -> None:
        self.strips = strips
        self.edges = edges
        self.bounds = dict(bounds)
        self.options = options
        self.limit = math.tan(math.radians(SHEAR_ANGLE))
        self.cache: dict[tuple[int, int], dict[EdgeKey, float]] = {}
        # Strips whose members already have their own gradings (the aspect
        # repair's releases, ``_spend``): kept as they are, in each strip's
        # orientation.
        self.fixed = dict(fixed or {})
        strip_of = {member: index for index, orientation in enumerate(strips) for member in orientation}
        self.strip_of = strip_of
        # The strip's one shared log ratio (``_ratios``, in the strip's
        # orientation) and how far each strip goes from it to its members'
        # own: 1 all the way, 0.5 half, 0 not at all (``settle``).
        self.shared = []
        for orientation in strips:
            member = next(iter(orientation))
            value = math.log(shared[member])
            self.shared.append(value if orientation[member] == member[0] else -value)
        self.mode = [1.0] * len(strips)
        # Cyclic partners share one value: union them.
        self.group: dict[EdgeKey, EdgeKey] = {member: member for member in strip_of}
        for match in _cyclic_matches(model):
            a, b = self._root(match.first), self._root(match.second)
            if a != b:
                self.group[max(a, b)] = min(a, b)
        # Opposite sides of a block in one strip: the shear the grading
        # difference adds is the node displacement times length over height.
        self.pairs: dict[int, list[tuple[EdgeKey, EdgeKey, float, float]]] = {}
        for block in model.blocks:
            keys = [edge_key(*block.directed_edge(index)) for index in range(4)]
            corners = block.vertices
            for k in range(2):
                first, second = keys[k], keys[k + 2]
                strip = strip_of.get(first)
                if strip is None or strip_of.get(second) != strip:
                    continue
                orientation = strips[strip]
                # Corner k faces corner k+3 across the block.
                aligned = (orientation[first] == corners[k]) == (orientation[second] == corners[(k + 3) % 4])
                height = 0.5 * (edges[keys[k + 1]].length + edges[keys[(k + 3) % 4]].length)
                length = 0.5 * (edges[first].length + edges[second].length)
                allowed = self.limit * height / length if length > 0.0 else math.inf
                self.pairs.setdefault(strip, []).append(
                    (first, second, 1.0 if aligned else -1.0, allowed))

    def _root(self, member: EdgeKey) -> EdgeKey:
        while self.group[member] != member:
            member = self.group[member]
        return member

    def _window(self, cells: int, member: EdgeKey) -> tuple[float, float, float, float, float]:
        """(lo, hi, near, far, length): the log ratios keeping each end
        within its bound - the begin end for log >= lo, the other for
        log <= hi - with the oriented bounds."""
        orientation = self.strips[self.strip_of[member]]
        begin = orientation[member]
        end = member[0] if member[1] == begin else member[1]
        near, far = self.bounds[(member, begin)], self.bounds[(member, end)]
        length = self.edges[member].length
        lo = (cells - 1) * math.log(series_ratio(near, length, cells))
        hi = -(cells - 1) * math.log(series_ratio(far, length, cells))
        return (max(-MAX_LOG_RATIO, min(MAX_LOG_RATIO, lo)), max(-MAX_LOG_RATIO, min(MAX_LOG_RATIO, hi)),
                near, far, length)

    def logs(self, strip: int, cells: int) -> dict[EdgeKey, float]:
        if strip in self.fixed:
            return self.fixed[strip]
        key = (strip, cells)
        if key not in self.cache:
            self.cache[key] = self._solve(strip, cells)
        own = self.cache[key]
        mode = self.mode[strip]
        if mode == 1.0:
            return own
        shared = self.shared[strip]
        return {member: shared + mode * (value - shared) for member, value in own.items()}

    def tighten(self, counts: dict[EdgeKey, int], links) -> None:
        """Lower each end's bound to ``interface_growth`` times the widths
        its partners across block interfaces have, until nothing changes.

        The field's bounds do not see cells a count forces fine, and the
        shared ratio hid that: a column through the Y-junction's mouth,
        graded like the top-wall spokes beside it, met the branch's fine
        column at 1.65; graded by the field alone it relaxed to 0.014
        against the branch's 0.0018 (7.7). Bounds only fall, so this ends.
        """
        cap = self.options.interface_growth
        if cap is None:
            return
        for _round in range(TIGHTEN_ROUNDS):
            widths = _actual(self.edges, self.ratios(counts), counts)
            changed = False
            for key, partners in sorted(links.items()):
                if key[0] not in self.strip_of or counts[key[0]] < 2:
                    continue
                limit = cap * min(widths[partner] for partner in partners)
                if limit < self.bounds[key] * (1.0 - 1.0e-9):
                    self.bounds[key] = limit
                    changed = True
            if not changed:
                break
            self.cache.clear()

    def settle(self, model: MeshModel, counts: dict[EdgeKey, int], scope, links) -> None:
        """Keep each strip's own gradings only where the sampled grid allows.

        Two opposite sides graded apart tilt the lines across a block; where
        the block's other pair is graded apart too, the transfinite map
        blends the two and the cells near the finer corner shear far more
        than either tilt (a column from a blunt base read 50 degrees with
        each pair within 20). Strips are taken finest bound first, from the
        spent state where every strip shares one ratio; a strip goes the
        first of ``FIELD_MODES`` of the way to its members' own gradings for
        which the worst interface jump at its members' ends stays within the
        spent state's there (or ``JUMP_LIMIT``), and its blocks and their
        neighbours, sampled at ``GUARD_CELLS`` a side, gain no fold, no face
        above 70 degrees, no skewness past 4, and keep their worst face below
        ``SHEAR_ANGLE`` and the shared state's worst face, or at most
        ``VERIFY_TOLERANCE`` above what it was, and - the shear guard - that
        raise the session's average face non-orthogonality (their average's
        rise times their share of the session's faces) by at most
        ``FIELD_MEAN_RISE`` unless the move brings a band spoke's first
        height nearer the one asked, an interface jump at its members' ends
        within ``JUMP_LIMIT`` or the worst face there down; else it keeps the
        shared ratio. The
        worst-face guard alone let a strip along a U-bend's thin blocks tilt
        every cell by up to 29 degrees (its neighbourhood's worst face was
        40 elsewhere): checkMesh average 11.7 where one ratio gave 3.3. A
        refused strip is tried once more together with the refused strips it
        meets across an interface.
        """
        from .sampling import sample_model

        trial = from_data(to_data(model))
        for current, value in counts.items():
            trial.edge_cells[current] = value
        self.mode = [0.0] * len(self.strips)
        self.refused = {"jump": 0, "grid": 0, "shear": 0}

        ratios: dict[EdgeKey, float] = {}

        def write(strip: int) -> None:
            orientation = self.strips[strip]
            logs = self.logs(strip, counts[next(iter(orientation))])
            for current, begin in orientation.items():
                value = logs[current] if begin == current[0] else -logs[current]
                ratios[current] = math.exp(value)
                if counts[current] < 2 or abs(value) <= 1.0e-9:
                    trial.edge_grading.pop(current, None)
                else:
                    trial.edge_grading[current] = math.exp(value)

        for strip in range(len(self.strips)):
            write(strip)
        users: dict[EdgeKey, list[str]] = {}
        for block in trial.blocks:
            for k in range(4):
                users.setdefault(edge_key(*block.directed_edge(k)), []).append(block.id)
        neighbours: dict[str, set[str]] = {}
        for ids in users.values():
            for block in ids:
                neighbours.setdefault(block, set()).update(ids)

        cap = self.options.interface_growth or 1.25

        def width(key: tuple[EdgeKey, str]) -> float:
            current, vertex = key
            first, last = _end_widths(self.edges[current].length, counts[current], ratios[current])
            return first if vertex == current[0] else last

        def jumps(members) -> dict:
            found = {}
            for member in members:
                for vertex in member:
                    for partner in links.get((member, vertex), ()):
                        a, b = width((member, vertex)), width(partner)
                        found[tuple(sorted(((member, vertex), partner)))] = max(a / b, b / a)
            return found

        def verdict(blocks):
            summary = sample_model(trial, GUARD_CELLS, blocks=blocks, planar=True).summary()
            return (summary.get("folded_cells", 0), summary.get("severe_faces", 0),
                    summary.get("max_skewness", 0.0), summary.get("max_non_orthogonality", 0.0),
                    summary.get("max_aspect_ratio", 0.0), summary.get("high_aspect_cells", 0),
                    summary.get("average_non_orthogonality", 0.0), summary.get("faces", 0))

        # Each wall spoke end among the members: (vertex, cosine of its
        # angle to the wall's normal, the first width asked for its patch).
        spoke_ends: dict[EdgeKey, list[tuple[str, float, float]]] = {}
        if FIELD_MEAN_RISE is not None:
            from .quality import _tangent, band_spokes

            overrides = dict(self.options.patch_first_widths)
            for current, vertex, name, normal in band_spokes(model)[0]:
                tx, ty = _tangent(model, current, vertex)
                spoke_ends.setdefault(current, []).append(
                    (vertex, max(0.0, tx * normal[0] + ty * normal[1]),
                     overrides.get(name, self.options.first_width)))

        def first_off(members) -> float:
            """The worst relative miss of the first height asked at the
            members' band spoke ends (the along-edge width times the cosine
            of the spoke's angle to the wall's normal)."""
            worst = 0.0
            for member in members:
                for vertex, cosine, wanted in spoke_ends.get(member, ()):
                    worst = max(worst, abs(width((member, vertex)) * cosine / wanted - 1.0))
            return worst

        def over_limit(members) -> int:
            return sum(1 for value in jumps(members).values() if value > JUMP_LIMIT * (1.0 + 1.0e-9))

        def sheared(before, after, members, off_before, over_before) -> bool:
            """Whether the move shears the blocks for nothing: the session's
            average face non-orthogonality up by more than ``FIELD_MEAN_RISE``
            (the rise of the blocks' and their neighbours' average, times
            their share of the session's faces) with no spoke's first height
            nearer the one asked, no interface jump brought within
            ``JUMP_LIMIT`` and no worst face lowered."""
            if FIELD_MEAN_RISE is None:
                return False
            if (after[6] - before[6]) * after[7] / session_faces <= FIELD_MEAN_RISE:
                return False
            return not (first_off(members) < off_before - FIELD_GAIN
                        or over_limit(members) < over_before
                        or after[3] < before[3] - VERIFY_TOLERANCE)

        def finest(strip: int) -> float:
            return min(min(self.bounds[(member, member[0])], self.bounds[(member, member[1])])
                       for member in self.strips[strip])

        # No block may be sheared past the worst face the shared state
        # already has (a pure H-grid stays at 0 degrees), nor past SHEAR_ANGLE.
        whole = sample_model(trial, GUARD_CELLS, planar=True).summary()
        ceiling = min(SHEAR_ANGLE, whole.get("max_non_orthogonality", 0.0))
        # The faces of the whole session, for a move's share of its average.
        session_faces = max(1, whole.get("faces", 0))
        order = sorted(
            (strip for strip, orientation in enumerate(self.strips)
             if all(member in scope for member in orientation)
             and counts[next(iter(orientation))] > 1 and strip not in self.fixed),
            key=lambda strip: (finest(strip), min(self.strips[strip])),
        )
        # Strips whose members meet across an interface: a far-field strip
        # relaxed alone jumps against the next one the spending graded from
        # it (the NACA 4412's wake strip and the C-arc piece at its far
        # corner), so a refused strip is tried again with its refused
        # partners.
        linked: dict[int, set[int]] = {}
        for (member, _vertex), partners in links.items():
            if member not in self.strip_of:
                continue
            for partner, _other in partners:
                if partner in self.strip_of and self.strip_of[partner] != self.strip_of[member]:
                    linked.setdefault(self.strip_of[member], set()).add(self.strip_of[partner])

        def attempt(group: list[int]) -> str | None:
            members = [member for strip in group for member in self.strips[strip]]
            for strip in group:
                self.mode[strip] = 1.0
            if all(abs(value - self.shared[strip]) <= 1.0e-9
                   for strip in group
                   for value in self.logs(strip, counts[next(iter(self.strips[strip]))]).values()):
                return None
            blocks = sorted({block for member in members for block in users.get(member, ())})
            local = sorted({other for block in blocks for other in neighbours.get(block, ())})
            for strip in group:
                self.mode[strip] = 0.0
            before = verdict(local)
            jumps_before = max(jumps(members).values(), default=1.0)
            off_before, over_before = first_off(members), over_limit(members)
            reason = "jump"
            for mode in FIELD_MODES:
                for strip in group:
                    self.mode[strip] = mode
                    write(strip)
                # The worst interface jump at the strips' ends grows past
                # neither the spent state's nor the quality screen's limit.
                if max(jumps(members).values(), default=1.0) > max(jumps_before, cap, JUMP_LIMIT) * (1.0 + 1.0e-9):
                    continue
                reason = "grid"
                after = verdict(local)
                if (after[0] <= before[0] and after[1] <= before[1]
                        and not (after[2] > SKEWNESS_LIMIT >= before[2])
                        and not _aspect_worse(before[4], before[5], after[4], after[5])
                        and after[3] <= max(ceiling, before[3] + VERIFY_TOLERANCE)):
                    if not sheared(before, after, members, off_before, over_before):
                        return None
                    reason = "shear"
            for strip in group:
                self.mode[strip] = 0.0
                write(strip)
            return reason

        refused: dict[int, str] = {}
        for strip in order:
            reason = attempt([strip])
            if reason is not None:
                refused[strip] = reason
        for strip in order:
            if strip not in refused:
                continue
            group = sorted({strip} | {other for other in linked.get(strip, ()) if other in refused})
            if len(group) > 1 and attempt(group) is None:
                for other in group:
                    refused.pop(other, None)
        for reason in refused.values():
            self.refused[reason] += 1
        self.refused_strips = dict(refused)

    def _solve(self, strip: int, cells: int) -> dict[EdgeKey, float]:
        members = sorted(self.strips[strip])
        if cells < 2:
            return {member: 0.0 for member in members}
        groups: dict[EdgeKey, list[EdgeKey]] = {}
        for member in members:
            groups.setdefault(self._root(member), []).append(member)
        window: dict[EdgeKey, tuple[float, float]] = {}
        target: dict[EdgeKey, float] = {}
        finest: dict[EdgeKey, float] = {}
        for root, items in groups.items():
            values = [self._window(cells, member) for member in items]
            lo = max(value[0] for value in values)
            hi = min(value[1] for value in values)
            near = min(value[2] for value in values)
            far = min(value[3] for value in values)
            if lo > hi:
                # No series meets both bounds at this count: the finer end
                # keeps its bound (a wall spoke its first width) and the
                # other end is over.
                lo = hi = lo if near <= far else hi
            wanted = max(lo, 0.0) if near <= far else min(hi, 0.0)
            window[root] = (lo, hi)
            target[root] = min(hi, max(lo, wanted))
            finest[root] = min(near, far)
        indices = list(range(cells + 1)) if cells <= SHEAR_SAMPLES else sorted(
            {round(step * cells / SHEAR_SAMPLES) for step in range(SHEAR_SAMPLES + 1)})
        beside: dict[EdgeKey, list[tuple[EdgeKey, float, float]]] = {}
        for first, second, sign, allowed in self.pairs.get(strip, ()):
            a, b = self._root(first), self._root(second)
            if a == b or allowed >= 1.0:
                continue
            beside.setdefault(a, []).append((b, sign, allowed))
            beside.setdefault(b, []).append((a, sign, allowed))
        value: dict[EdgeKey, float] = {}
        for root in sorted(groups, key=lambda item: (finest[item], item)):
            lo, hi = window[root]
            wanted = target[root]
            fixed = [(sign * value[other], allowed) for other, sign, allowed in beside.get(root, ())
                     if other in value]
            if fixed and any(_shear(wanted, there, cells, indices) > allowed for there, allowed in fixed):
                low, high = -math.inf, math.inf
                for there, allowed in fixed:
                    a, b = self._reach(there, allowed, cells, indices)
                    low, high = max(low, a), min(high, b)
                wanted = min(high, max(low, wanted)) if low <= high else 0.5 * (low + high)
                wanted = min(hi, max(lo, wanted))
            value[root] = wanted
        return {member: value[self._root(member)] for member in members}

    @staticmethod
    def _reach(there: float, allowed: float, cells: int, indices: Sequence[int]) -> tuple[float, float]:
        """The log ratios within ``allowed`` node displacement of ``there``."""
        ends = []
        for direction in (-1.0, 1.0):
            far = there + direction * 2.0 * MAX_LOG_RATIO
            if _shear(there, far, cells, indices) <= allowed:
                ends.append(far)
                continue
            near_end, far_end = there, far
            for _ in range(40):
                middle = 0.5 * (near_end + far_end)
                if _shear(there, middle, cells, indices) <= allowed:
                    near_end = middle
                else:
                    far_end = middle
            ends.append(near_end)
        return ends[0], ends[1]

    def ratios(self, counts: dict[EdgeKey, int]) -> dict[EdgeKey, float]:
        """Canonical total ratios of every member at ``counts``."""
        result = {}
        for index, orientation in enumerate(self.strips):
            member = next(iter(orientation))
            logs = self.logs(index, counts[member])
            for current, begin in orientation.items():
                value = logs[current]
                result[current] = math.exp(value if begin == current[0] else -value)
        return result


def _actual(edges, ratios, counts) -> dict[tuple[EdgeKey, str], float]:
    """The end widths every edge's series gives at its count."""
    widths = {}
    for current, item in edges.items():
        first, last = _end_widths(item.length, counts[current], ratios[current])
        widths[(current, current[0])] = first
        widths[(current, current[1])] = last
    return widths


def _report(model, cells_before, components, splits, refused, factor, note, options=None) -> SizeResult:
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
        bands=_bands(model, options),
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


def _budget_factor(model, components, targets, options: SizeOptions, capped=None) -> float:
    if not options.budget or options.budget <= 0:
        return 1.0
    index_of = {member: position for position, members in enumerate(components) for member in members}
    held = capped or [False] * len(targets)

    def total(factor: float) -> int:
        values = [
            min(options.maximum_cells, max(options.minimum_cells, math.ceil(
                target * (1.0 if keep else factor) - 1.0e-9)))
            for target, keep in zip(targets, held)
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
    cyclic = {edge for match in _cyclic_matches(model) for edge in (match.first, match.second)}
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
        coupled = sorted(component & cyclic)
        if coupled:
            text = (
                f"two-ended strip of {'-'.join(current)} (peak {ratio:.3g} times its ends) not "
                f"split: it holds the cyclic edge {'-'.join(coupled[0])}, whose partner a split "
                "would not cut"
            )
            if text not in refused:
                refused.append(text)
            continue
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
            # The split is undone, so its vertex does not exist: name the
            # piece by the existing end it runs from and the edge it is of.
            end = next(v for v in sliver[0] if v not in split.split_vertex_ids)
            parent = next((member for member in sorted(component) if end in member), current)
            text = (
                f"two-ended strip of {'-'.join(current)} not split: cutting it at {fraction:.3g} "
                f"would leave the piece of {'-'.join(parent)} at {end} {sliver[1]:.3g} long where "
                f"cells of {sliver[2]:.3g} are wanted"
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


def _bands(model: MeshModel, options: SizeOptions | None = None) -> list[dict]:
    """Per wall patch: the band spokes' cells and first cell heights normal
    to the wall, with the spokes of the smallest and largest named, and the
    first width asked for it (:func:`blockdrawer.quality.wall_first_widths`)."""
    from .quality import wall_first_widths

    asked = None
    if options is not None:
        overrides = dict(options.patch_first_widths)
        asked = {
            name: overrides.get(name, options.first_width)
            for name, boundary in model.boundaries.items() if boundary.kind == "wall"
        }
    return [band for band in wall_first_widths(model, asked) if band["spokes"]]


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
        if min(profile[peak], item.cap) >= SPLIT_RATIO * ends and 0.05 < fraction < 0.95:
            found[current] = (fraction, profile[peak] / ends)
    return found


def _continuity_splits(working, field_, state, moves, guard, options, budget, blocks, bbox, splits, refused,
                       skew_floor=None, fold_floor=None):
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
        solved_cells = _state_cells(working, trial)
        trial, trial_moves, trial_guard = _spend(working, trial, options, budget, skew_floor, fold_floor)
        trial["field_cells"] = solved_cells
        promised = state["verification"]["field"]
        verified = trial["verification"]["result"]
        if (_jump_cost(trial, options) >= _jump_cost(state, options)
                or _max_jump(trial) > _max_jump(state) * (1.0 + 1.0e-9)
                or verified["severe_faces"] > promised["severe_faces"]
                or verified["folded_cells"] > promised["folded_cells"]
                or verified["max_non_orthogonality"] > promised["max_non_orthogonality"] + VERIFY_TOLERANCE
                or _aspect_worse(promised["max_aspect_ratio"], promised["high_aspect_cells"],
                                 verified["max_aspect_ratio"], verified["high_aspect_cells"])):
            working.__dict__.update(saved.__dict__)
            for item in splits[made:]:
                refused.append(
                    f"split of {item['edge']} undone: after spending the budget again it did "
                    "not lower the interface jumps"
                )
            del splits[made:]
            break
        trial["verification"] = {**trial["verification"], "field": promised,
                                 "field_real": state["verification"].get("field_real")}
        state, moves, guard = trial, trial_moves, trial_guard
    return state, moves, guard


def _verify(model: MeshModel, neighbours: dict[str, set[str]]) -> tuple[dict[str, dict], dict]:
    """Per block and in total: faces above 70 degrees, folds, the worst face
    and skewness, at ``VERIFY_CELLS`` a side - and, for every block whose
    worst face there exceeds ``HOT_ANGLE``, at its real counts (sampled with
    its neighbours, so all its faces are measured): at 256 a side the fine
    30P30N's b39 read 6 faces above 70 where checkMesh counts 7. A face above
    70 degrees makes its owner block hot, so the totals count every one."""
    from .sampling import sample_model

    coarse = sample_model(model, VERIFY_CELLS, planar=True)
    blocks = {
        item.block_id: {
            "severe_faces": item.severe_faces,
            "folded_cells": item.folded_cells,
            "max_non_orthogonality": item.max_non_orthogonality,
            "max_skewness": item.max_skewness,
            "max_aspect_ratio": item.max_aspect_ratio,
            "high_aspect_cells": item.high_aspect_cells,
        }
        for item in coarse.blocks
    }
    hot = sorted(block for block, item in blocks.items()
                 if item["max_non_orthogonality"] > HOT_ANGLE or item["folded_cells"])
    coarsened = {item.block_id for item in coarse.blocks if item.coarsened}
    hot = [block for block in hot if block in coarsened or any(
        other in coarsened for other in neighbours.get(block, ()))]
    if hot:
        subset = sorted({other for block in hot for other in neighbours.get(block, {block})} | set(hot))
        real = sample_model(model, None, blocks=subset, planar=True)
        for item in real.blocks:
            if item.block_id in hot:
                blocks[item.block_id] = {
                    "severe_faces": item.severe_faces,
                    "folded_cells": item.folded_cells,
                    "max_non_orthogonality": item.max_non_orthogonality,
                    "max_skewness": max(item.max_skewness, blocks[item.block_id]["max_skewness"]),
                    "max_aspect_ratio": item.max_aspect_ratio,
                    "high_aspect_cells": item.high_aspect_cells,
                }
    summary = {
        "severe_faces": sum(item["severe_faces"] for item in blocks.values()),
        "folded_cells": sum(item["folded_cells"] for item in blocks.values()),
        "max_non_orthogonality": max((item["max_non_orthogonality"] for item in blocks.values()), default=0.0),
        "max_skewness": max((item["max_skewness"] for item in blocks.values()), default=0.0),
        # checkMesh's cell aspect ratio: the repair lowers it below
        # ASPECT_LIMIT where it can, the guards keep it there.
        "max_aspect_ratio": max((item["max_aspect_ratio"] for item in blocks.values()), default=0.0),
        "worst_aspect_block": max(blocks, key=lambda block: blocks[block]["max_aspect_ratio"], default=None),
        "high_aspect_cells": sum(item["high_aspect_cells"] for item in blocks.values()),
        # The blocks above the limit, worst first: [block, ratio, cells above].
        "high_aspect_blocks": [
            [block, blocks[block]["max_aspect_ratio"], blocks[block]["high_aspect_cells"]]
            for block in sorted(blocks, key=lambda block: (-blocks[block]["max_aspect_ratio"], block))
            if blocks[block]["max_aspect_ratio"] > ASPECT_LIMIT
        ],
        # The blocks with folded cells, most first: [block, cells].
        "folded_blocks": [
            [block, blocks[block]["folded_cells"]]
            for block in sorted(blocks, key=lambda block: (-blocks[block]["folded_cells"], block))
            if blocks[block]["folded_cells"]
        ],
        "real_count_blocks": len(hot),
    }
    return blocks, summary


# What size reports as its verification: every real cell (None). The guards
# and the verification that undoes moves judge at VERIFY_CELLS (256 a side,
# real counts for blocks above HOT_ANGLE there): at 256 a NACA 4412 face on an
# edge shared with a coarsened block read 63.7 degrees where it was 70.8, so
# the report said no face above 70 where there was one (round 5); judged at
# the real counts, the corpus NACA 4412 gained two jumps above 5 after the
# polish (batch 12, README), so the decisions stay where they were.
REPORT_CELLS = None


def _verify_real(model: MeshModel) -> dict:
    """The verdict at the real counts (``REPORT_CELLS``): faces above 70,
    folds, the worst face, skewness, aspect ratio, as ``_verify``'s summary."""
    from .sampling import sample_model

    sample = sample_model(model, REPORT_CELLS, planar=True)
    blocks = sample.blocks
    return {
        "severe_faces": sum(item.severe_faces for item in blocks),
        "folded_cells": sum(item.folded_cells for item in blocks),
        "max_non_orthogonality": max((item.max_non_orthogonality for item in blocks), default=0.0),
        "max_skewness": max((item.max_skewness for item in blocks), default=0.0),
        "max_aspect_ratio": max((item.max_aspect_ratio for item in blocks), default=0.0),
        "worst_aspect_block": max(blocks, key=lambda item: item.max_aspect_ratio).block_id if blocks else None,
        "high_aspect_cells": sum(item.high_aspect_cells for item in blocks),
        "high_aspect_blocks": [
            [item.block_id, item.max_aspect_ratio, item.high_aspect_cells]
            for item in sorted(blocks, key=lambda item: (-item.max_aspect_ratio, item.block_id))
            if item.max_aspect_ratio > ASPECT_LIMIT
        ],
        "folded_blocks": [
            [item.block_id, item.folded_cells]
            for item in sorted(blocks, key=lambda item: (-item.folded_cells, item.block_id))
            if item.folded_cells
        ],
    }


def _reported(model: MeshModel, verification: dict) -> dict:
    """``size``'s reported verification: the field's state and the result at
    the real counts (the moves were judged at ``cells_per_side_judged``)."""
    if not verification:
        return {}
    field = verification.get("field_real") or verification.get("field")
    return {
        "cells_per_side": REPORT_CELLS,
        "cells_per_side_judged": verification.get("cells_per_side"),
        "field": field,
        "result": _brief(_verify_real(model)),
        "undone_moves": verification.get("undone_moves", 0),
    }


def _floors(model: MeshModel) -> tuple[float, int]:
    """The input's sampled skewness and folded cells at its own counts."""
    from .sampling import sample_model

    summary = sample_model(model, VERIFY_CELLS).summary()
    return summary.get("max_skewness", 0.0), summary.get("folded_cells", 0)


def _max_skewness(model: MeshModel) -> float:
    from .sampling import sample_model

    return sample_model(model, VERIFY_CELLS).summary().get("max_skewness", 0.0)


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


def _spend(model: MeshModel, state: dict, options: SizeOptions, budget: int, skew_floor: float | None = None,
           fold_floor: int | None = None):
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
    first (see there); ``skew_floor`` is the input's sampled skewness,
    ``fold_floor`` its folded cells.
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

    # Strips the aspect repair gave their members' own gradings
    # (``repair_aspect``): oriented log ratio per member. Spending neither
    # regrades nor refits them; a raise keeps their ratios.
    released: dict[int, dict[EdgeKey, float]] = {}

    def total_ratio(current, log=None):
        if log is None:
            own = released.get(strip_of[current])
            value = logs[strip_of[current]] if own is None else own[current]
        else:
            value = log
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
            # A released strip keeps its members' own ratios.
            total = total_ratio(member, None if index in released else value)
            if trial.edge_cells[member] < 2 or abs(total - 1.0) <= 1.0e-9:
                trial.edge_grading.pop(member, None)
            else:
                trial.edge_grading[member] = total

    def verdict(blocks, cells=GUARD_CELLS):
        summary = sample_model(trial, cells, blocks=blocks, planar=True).summary()
        return (summary.get("folded_cells", 0), summary.get("severe_faces", 0),
                summary.get("max_skewness", 0.0), summary.get("max_non_orthogonality", 0.0),
                summary.get("max_aspect_ratio", 0.0), summary.get("high_aspect_cells", 0))

    # The worst face the result may have: the field's (as repaired) plus
    # VERIFY_TOLERANCE, set once the repair is done.
    guard_ceiling = [math.inf]

    def guarded(blocks, apply, undo) -> bool:
        local = sorted({other for block in blocks for other in neighbours.get(block, ())})
        before = verdict(local)
        apply()
        after = verdict(local)
        if (after[0] <= before[0] and after[1] <= before[1]
                and (after[3] <= before[3] or after[3] <= guard_ceiling[0])
                and not (after[2] > SKEWNESS_LIMIT and before[2] <= SKEWNESS_LIMIT)
                and not _aspect_worse(before[4], before[5], after[4], after[5])):
            return True
        undo()
        return False

    cells = _state_cells(model, {"counts": counts})
    moves: list[dict] = []
    stopped_by_budget = False
    tabu: set[tuple] = set()
    refused = 0

    by_id = {block.id: block for block in model.blocks}

    def shape_trials(worst) -> list[tuple]:
        """The repair's candidate moves for one block: raise one of its two
        components (within the budget), take one of its strips towards
        uniform or half way there, or give one side the grading of its
        opposite side. Released strips are not regraded."""
        keys = [edge_key(*worst.directed_edge(k)) for k in range(4)]
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
            if index in released:
                continue
            for value in sorted(values[index]):
                if abs(value - logs[index]) > 1.0e-9 and all(allowed(m, value) for m in strips[index]):
                    trials.append(("regrade", index, value, 0))
        return trials

    def write_release(index, own):
        for member in strips[index]:
            value = own[member]
            total = math.exp(value if begin_of[member] == member[0] else -value)
            if trial.edge_cells[member] < 2 or abs(total - 1.0) <= 1.0e-9:
                trial.edge_grading.pop(member, None)
            else:
                trial.edge_grading[member] = total

    def try_move(kind, index, value, local):
        """The local verdict with the move made, the trial left as it was."""
        if kind == "raise":
            old = counts[components[index][0]]
            write_raise(index, value, {})
            result = verdict(local, VERIFY_CELLS)
            write_raise(index, old, {})
        elif kind == "release":
            before = released.get(index)
            released[index] = value
            write_release(index, value)
            result = verdict(local, VERIFY_CELLS)
            if before is None:
                del released[index]
            else:
                released[index] = before
            write_regrade(index, logs[index])
        else:
            old = logs[index]
            write_regrade(index, value)
            result = verdict(local, VERIFY_CELLS)
            write_regrade(index, old)
        return result

    def make_move(kind, index, value, added):
        nonlocal cells
        if kind == "raise":
            moves.append({"kind": "shape", "component": index, "edge": "-".join(components[index][0]),
                          "from": counts[components[index][0]],
                          "to": value, "cells": added})
            write_raise(index, value, {})
            for member in components[index]:
                counts[member] = value
            cells += added
        elif kind == "release":
            moves.append({"kind": "shape", "strip": index, "release": release_mode[index]})
            released[index] = value
            write_release(index, value)
        else:
            moves.append({"kind": "shape", "strip": index, "from": logs[index], "to": value})
            write_regrade(index, value)
            logs[index] = value

    def repair_shape() -> None:
        """Repair what the field's counts break before any cell is spent on
        continuity; the guard then keeps it repaired.

        First a block left above skewness 4 when the input was below (a
        tapered block with few cells across): only folds are refused there,
        since checkMesh fails a mesh above skewness 4 but only counts faces
        above 70 degrees (e3's b77 at five cells across: 4.9 with 10 such
        faces, 3.5 with 15 at seven). Then the block with the worst face
        above 70 degrees, while its count of them or its worst face falls
        and the skewness stays at or below 4. Candidates (``shape_trials``):
        raise one of the block's two components, take one of its strips
        towards uniform, or give one side the grading of its opposite side.
        No move may take the block's neighbourhood across checkMesh's
        aspect ratio limit.
        """
        skew_phase = skew_floor is not None and skew_floor <= SKEWNESS_LIMIT
        for _repair in range(SHAPE_REPAIRS):
            summary = sample_model(trial, VERIFY_CELLS, planar=True).summary()
            if skew_phase and summary.get("max_skewness", 0.0) > SKEWNESS_LIMIT:
                worst = by_id[summary["worst_skew_block"]]
                skewed = True
            elif summary.get("severe_faces", 0) and summary.get("worst_block") in by_id:
                worst = by_id[summary["worst_block"]]
                skewed = False
            else:
                break
            local = sorted(neighbours.get(worst.id, {worst.id}))
            base = verdict(local, VERIFY_CELLS)
            best_trial = None
            for kind, index, value, added in shape_trials(worst):
                result = try_move(kind, index, value, local)
                if result[0] > base[0] or _aspect_worse(base[4], base[5], result[4], result[5]):
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
            make_move(kind, index, value, added)

    release_mode: dict[int, float] = {}
    grader: list = []

    def strip_jumps(index) -> list[float]:
        """The interface jumps at the ends of a strip's members."""
        found = []
        for pair in strip_pairs[index]:
            (e1, v1), (e2, v2) = pairs[pair]
            a, b = width(e1, v1), width(e2, v2)
            found.append(max(a / b, b / a))
        return found

    def release_trials(worst) -> list[tuple]:
        """Each strip through the block, its members going all, half or a
        quarter of the way from the shared ratio to their own gradings
        (``grading=field``'s ``_FieldGrading``, each member graded by the
        field where it lies within the shear limit), further than it went
        already. Each member end's bound is first lowered to
        ``interface_growth`` times the widths its partners across block
        interfaces have now (``_FieldGrading.tighten`` against the state as
        it is), so a member beside a band keeps the band's widths and the
        members far from it relax."""
        if not grader:
            grader.append(_FieldGrading(model, strips, edges, state["bounds"], options, ratios))
        own = grader[0]
        links = state["links"]
        keys = [edge_key(*worst.directed_edge(k)) for k in range(4)]
        trials = []
        for index in sorted({strip_of[k] for k in keys}):
            member = next(iter(strips[index]))
            if index not in scoped_strips_set or counts[member] < 2:
                continue
            for current in strips[index]:
                for vertex in current:
                    key = (current, vertex)
                    partners = links.get(key, ())
                    bound = state["bounds"][key]
                    if partners:
                        bound = min(bound, cap * min(width(*partner) for partner in partners))
                    own.bounds[key] = bound
            for cached in [item for item in own.cache if item[0] == index]:
                del own.cache[cached]
            own.shared[index] = logs[index]
            for mode in FIELD_MODES:
                if mode <= release_mode.get(index, 0.0):
                    continue
                own.mode[index] = mode
                value = dict(own.logs(index, counts[member]))
                if all(abs(value[m] - logs[index]) <= 1.0e-9 for m in strips[index]):
                    continue
                trials.append(("release", index, value, mode))
            own.mode[index] = 1.0
        return trials

    def repair_aspect() -> None:
        """Lower checkMesh's cell aspect ratio below ``ASPECT_LIMIT`` in
        each block above it, worst first.

        A cell is long and thin when a strip's one shared ratio carries a
        wall's first width along its members to where the field asks for
        cells hundreds of times longer (the shear layers of a blunt plate
        carried 2e-4 to an outlet with 0.27 long cells). The free moves come
        first: a strip through the block going part of the way to its
        members' own gradings (``release_trials``; the worst interface jump
        at its members' ends may pass neither what it was nor
        ``JUMP_LIMIT``), and the regrades of ``shape_trials``. A count raise
        (shorter long cells, at a cost in cells) is tried only when no free
        move lowers the block's cells above the limit or its worst ratio. No
        move may fold a cell, add a face above 70 degrees, take the skewness
        past 4, or take the neighbourhood's worst face past ``SHEAR_ANGLE``
        (or ``VERIFY_TOLERANCE`` above where it was): members graded apart
        shear the cells between them, which an H-grid's checkMesh failure
        is worth up to that angle (a column whose release read 56 degrees
        took count raises instead). A block no move improves is left, and
        named in the note."""
        exhausted: set[str] = set()
        for _repair in range(SHAPE_REPAIRS):
            sampled = sample_model(trial, VERIFY_CELLS, planar=True)
            targets = sorted(
                (item for item in sampled.blocks
                 if item.max_aspect_ratio > ASPECT_LIMIT and item.block_id not in exhausted
                 and item.block_id in by_id),
                key=lambda item: (-item.max_aspect_ratio, item.block_id),
            )
            if not targets:
                break
            worst = by_id[targets[0].block_id]
            local = sorted(neighbours.get(worst.id, {worst.id}))
            base = verdict(local, VERIFY_CELLS)
            trials = release_trials(worst) + shape_trials(worst)
            best_trial = None
            for paid in (False, True):
                for kind, index, value, added in trials:
                    if (kind == "raise") != paid:
                        continue
                    if kind != "raise":
                        jump_before = strip_jumps(index)
                        saved = released.get(index), logs[index]
                        if kind == "release":
                            released[index] = value
                        else:
                            released.pop(index, None)
                            logs[index] = value
                        jump_after = strip_jumps(index)
                        if saved[0] is None:
                            released.pop(index, None)
                        else:
                            released[index] = saved[0]
                        logs[index] = saved[1]
                        if any(after > max(before, JUMP_LIMIT) * (1.0 + 1.0e-9)
                               for before, after in zip(jump_before, jump_after)):
                            continue
                    result = try_move(kind, index, value, local)
                    if (result[0] > base[0] or result[1] > base[1]
                            or result[2] > SKEWNESS_LIMIT >= base[2]
                            or result[3] > max(SHEAR_ANGLE, base[3] + VERIFY_TOLERANCE)):
                        continue
                    score = (result[5], result[4])
                    better = result[5] < base[5] or (result[5] == base[5] and result[4] < base[4] * (1.0 - 1.0e-3))
                    if better and (best_trial is None or score < best_trial[0]):
                        best_trial = (score, kind, index, value, added)
                if best_trial is not None:
                    break
            if best_trial is None:
                exhausted.add(worst.id)
                continue
            _score, kind, index, value, added = best_trial
            if kind == "release":
                release_mode[index] = added
            make_move(kind, index, value, 0 if kind == "release" else added)

    def repair_folds() -> None:
        """Unfold each block the field's counts and gradings fold when the
        input, at its own counts, folded none - most folded cells first.

        A spline edge whose short end segment loops back along its chord
        (``model.spline_hooks``) is untangled at coarse even spacing and
        folds once nodes are graded into the loop (the round-2 high-lift's
        main-element ring: 46 cells in four blocks). Moves: a strip through
        the block a quarter, half, three quarters or all the way towards
        uniform, past the field's bound at an end if need be (a fold fails
        checkMesh, a coarser end cell does not), which moves its nodes out
        of the loop, before ``shape_trials``' raises; the move with the
        fewest folds left, then the smallest change, is taken when the
        neighbourhood's folded cells fall and it does not take the skewness
        past 4 nor cross the aspect ratio limit (faces above 70 degrees are
        allowed). A block no move unfolds is left; ``size_model`` then
        refuses."""
        exhausted: set[str] = set()
        for _repair in range(SHAPE_REPAIRS):
            sampled = sample_model(trial, VERIFY_CELLS, planar=True)
            targets = sorted(
                (item for item in sampled.blocks
                 if item.folded_cells and item.block_id not in exhausted and item.block_id in by_id),
                key=lambda item: (-item.folded_cells, item.block_id),
            )
            if not targets:
                break
            worst = by_id[targets[0].block_id]
            local = sorted(neighbours.get(worst.id, {worst.id}))
            base = verdict(local, VERIFY_CELLS)
            # Towards uniform in quarters, past the field's bound if need
            # be: a fold fails checkMesh, a coarser end does not.
            keys = [edge_key(*worst.directed_edge(k)) for k in range(4)]
            trials = []
            for index in sorted({strip_of[k] for k in keys}):
                if index in released:
                    continue
                for share in (0.75, 0.5, 0.25, 0.0):
                    trials.append(("regrade", index, share * logs[index], 0))
            trials += [trial_ for trial_ in shape_trials(worst) if trial_[0] == "raise"]
            best_trial = None
            for paid in (False, True):
                for kind, index, value, added in trials:
                    if (kind == "raise") != paid:
                        continue
                    if kind == "regrade" and abs(value - logs[index]) <= 1.0e-9:
                        continue
                    result = try_move(kind, index, value, local)
                    if (result[2] > SKEWNESS_LIMIT >= base[2]
                            or _aspect_worse(base[4], base[5], result[4], result[5])):
                        continue
                    # Fewest folds, then the smallest change of grading.
                    change = abs(value - logs[index]) if kind == "regrade" else math.inf
                    score = (result[0], change, result[1], result[3])
                    if result[0] < base[0] and (best_trial is None or score < best_trial[0]):
                        best_trial = (score, kind, index, value, added)
                if best_trial is not None:
                    break
            if best_trial is None:
                exhausted.add(worst.id)
                continue
            _score, kind, index, value, added = best_trial
            make_move(kind, index, value, added)

    scoped_strips_set = set(scoped_strips)
    if fold_floor == 0:
        repair_folds()
    repair_shape()
    repair_aspect()
    # The field's state as repaired: what the returned state is verified
    # against, at the resolution smooth verifies at.
    reference_counts = dict(counts)
    reference_logs = list(logs)
    repaired = len(moves)
    reference = _verify(trial, neighbours)
    # What the result reports for the field's state: its real counts.
    reference_real = _brief(_verify_real(trial))
    guard_ceiling[0] = reference[1]["max_non_orthogonality"] + VERIFY_TOLERANCE
    for _iteration in range(20000):
        best = None
        for index in scoped_strips:
            touched = strip_pairs[index]
            if not touched or index in released:
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
                    if strip in released:
                        continue
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
            stopped_by_budget = True
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
            moves.append({"kind": "raise", "component": index, "edge": "-".join(components[index][0]),
                          "from": old, "to": value, "cells": added})
            for member in components[index]:
                counts[member] = value
            logs_changed = refit
            for strip, log in logs_changed.items():
                logs[strip] = log
            cells += added
    # Verification: the guard samples 64 cells a side and is blind to faces
    # of blocks with hundreds (the fine 30P30N kept 7 and 53 faces above 70
    # degrees it had promised not to add). Every block whose verified faces
    # break the promise has the components and strips through it put back
    # to the field's values, until the whole mesh keeps it.
    field_blocks, field_summary = reference
    ceiling = field_summary.get("max_non_orthogonality", 0.0) + VERIFY_TOLERANCE
    field_skew = field_summary.get("max_skewness", 0.0)
    field_aspect = field_summary.get("max_aspect_ratio", 0.0), field_summary.get("high_aspect_cells", 0)
    reverted_components: set[int] = set()
    reverted_strips: set[int] = set()
    by_block = {block.id: block for block in model.blocks}
    for _round in range(VERIFY_ROUNDS + 1):
        verified, summary = _verify(trial, neighbours)
        if (summary.get("severe_faces", 0) <= field_summary.get("severe_faces", 0)
                and summary.get("folded_cells", 0) <= field_summary.get("folded_cells", 0)
                and summary.get("max_non_orthogonality", 0.0) <= ceiling
                and not (summary.get("max_skewness", 0.0) > SKEWNESS_LIMIT >= field_skew)
                and not _aspect_worse(*field_aspect, summary.get("max_aspect_ratio", 0.0),
                                      summary.get("high_aspect_cells", 0))):
            break
        offenders = sorted(
            block for block, item in verified.items()
            if item["severe_faces"] > field_blocks[block]["severe_faces"]
            or item["folded_cells"] > field_blocks[block]["folded_cells"]
            or item["max_non_orthogonality"] > ceiling
            or item["max_skewness"] > SKEWNESS_LIMIT >= field_skew
            or _aspect_worse(field_blocks[block]["max_aspect_ratio"], field_blocks[block]["high_aspect_cells"],
                             item["max_aspect_ratio"], item["high_aspect_cells"])
        )
        changed = False
        for ring in range(3):
            if ring == 1:
                offenders = sorted({other for block in offenders for other in neighbours.get(block, ())})
            if ring == 2 or _round == VERIFY_ROUNDS:
                offenders = sorted(by_block)
            these_components: set[int] = set()
            these_strips: set[int] = set()
            for block in offenders:
                keys = [edge_key(*by_block[block].directed_edge(k)) for k in range(4)]
                for key in keys:
                    these_components.add(component_of[key])
                    these_strips.add(strip_of[key])
            for index in list(these_components):
                these_strips.update(strips_of_component.get(index, ()))
            these_components = {
                index for index in these_components
                if counts[components[index][0]] != reference_counts[components[index][0]]
            }
            these_strips = {index for index in these_strips if logs[index] != reference_logs[index]}
            if these_components or these_strips:
                for index in sorted(these_components):
                    member = components[index][0]
                    cells -= (counts[member] - reference_counts[member]) * sum(
                        counts[components[other][0]] for other in across.get(index, ())
                    ) * model.z_cells
                    write_raise(index, reference_counts[member], {})
                    for each in components[index]:
                        counts[each] = reference_counts[each]
                for index in sorted(these_strips):
                    logs[index] = reference_logs[index]
                for index in sorted(these_strips | {
                    strip for component in these_components for strip in strips_of_component.get(component, ())
                }):
                    write_regrade(index, logs[index])
                reverted_components |= these_components
                reverted_strips |= these_strips
                changed = True
                break
        if not changed:
            break
    kept = moves[:repaired] + [
        move for move in moves[repaired:]
        if move.get("component") not in reverted_components and move.get("strip") not in reverted_strips
    ]
    undone = len(moves) - len(kept)
    final = _verify(trial, neighbours)[1]

    verification = {
        "cells_per_side": VERIFY_CELLS,
        "field": _brief(field_summary),
        "result": _brief(final),
        "undone_moves": undone,
        "field_real": reference_real,
    }
    new_ratios = dict(ratios)
    for index, orientation in enumerate(strips):
        for member in orientation:
            new_ratios[member] = total_ratio(member)
    notes = []
    if refused:
        notes.append(f"{refused} move(s) refused by the sampled check (a folded cell, a face above "
                     f"70 degrees, skewness above 4 or cells past aspect ratio {ASPECT_LIMIT:g})")
    if undone:
        notes.append(
            f"{undone} move(s) undone by the verification at {VERIFY_CELLS} cells a side "
            f"(more faces above 70 degrees, a worst face above the field's "
            f"{field_summary.get('max_non_orthogonality', 0.0):.1f} + {VERIFY_TOLERANCE:g}, "
            f"skewness past 4 or cells past aspect ratio {ASPECT_LIMIT:g})"
        )
    state = {
        **state, "counts": counts, "ratios": new_ratios, "verification": verification,
        "budget_binding": stopped_by_budget or state.get("factor", 1.0) < 1.0,
        "released": {index: dict(own) for index, own in released.items()},
    }
    return state, kept, "; ".join(notes)
