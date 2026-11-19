"""Global smoothing of a finished block layout: move interior vertices only.

Once a topology exists nothing else optimises where its vertices sit;
``repair_block`` is local and greedy. :func:`smooth_model` moves every free
vertex - one not on a boundary edge, inside the requested scope and not
held - to lower checkMesh's face non-orthogonality over every block,
carrying its curved edges by the similarity ``move_vertex carry_curves``
uses. Topology, counts, grading and boundary vertices are untouched.

The judge is the sampled interior of :mod:`blockdrawer.sampling` at the
real cell counts and grading: each block is sampled at a subset of its
real nodes that always keeps the first two cells at each end of every edge,
so the thin wall cells are measured as they are. Each face costs
``tan(phi)**power`` for its non-orthogonality ``phi``, a barrier at 90
degrees, and a folded sampled cell or a block corner flatter than the
original makes a position inadmissible.

Two phases run as Gauss-Seidel pattern searches over the free vertices
(eight directions, a step that halves when nothing improves):

1. the sum of face costs is lowered, but a move is taken only when it
   does not raise the worst face of the blocks it touches - a sum alone
   trades a few terrible cells for many good ones;
2. the worst faces are lowered directly: the vertices of the blocks
   holding the worst faces are moved to lower that maximum (then the sum).

A wall band keeps its height: every internal edge from a vertex on a
``wall`` patch to a free vertex (a band spoke) keeps its length within
``band_tolerance``, by projecting each trial position onto the annulus
about the wall vertex - the spoke may swing towards the wall normal but not
shorten. Grading is stored as a ratio, so a spoke's first cell keeps its
width with its length.

With ``keep_lines`` an interior vertex on a straight block line (two
opposite ``line`` edges, collinear) is searched along that line only, and
one on two such lines does not move, so prescribed wake lines and grid rows
stay straight. In shape mode every edge keeps ``shape_length_ratio`` of its
input length; ``shape_pair_ratio`` keeps the length ratio of every interface
pair (two edges continuing each other across a shared edge) within that
factor of its input, and ``shape_spread_ratio`` keeps every count
component's length spread (longest over shortest member) from growing past
that factor. The result reports the shortest and longest edge over its
input length in both modes, and in shape mode the largest pair-ratio change
and spread growth.

The result is verified at full resolution (:func:`sample_model`) and
applied only when the model stays untangled, its severe-face count does not
rise and its maximum non-orthogonality does not rise; otherwise the
displacements are scaled back by halves and the first scaled result that
passes is applied, or nothing is.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Iterable, Sequence

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .sampling import FOLD_CELLS, SEVERE_NON_ORTHOGONALITY, ModelSample, folded_blocks, sample_model
from .session import from_data, to_data
from .slide import SLIDE_TOLERANCE


INF = math.inf
# Resolution of the full verification, as the quality report's default.
VERIFY_CELLS = 256
# A trial block corner may not come closer to 0 or 180 degrees than 20
# (this sine) unless the original corner already was: a new 178-degree block
# corner is a first cell the corner screen rightly flags.
MIN_CORNER_SINE = math.sin(math.radians(20.0))
# Displacement scales tried when the full verification refuses the result.
FALLBACK_SCALES = (0.5, 0.25, 0.125)
# The polish's ceiling on an interface jump that is below it (the benchmark's
# target: no jump above 5).
SIZE_CEILING = 5.0
# Shape mode's default floor on an edge's length over its input length.
SHAPE_LENGTH_RATIO = 0.1
# Shape mode's default limit on how far an interface pair's length ratio
# may leave its input value (0: unbounded).
SHAPE_PAIR_RATIO = 0.0
# Shape mode's default limit on how far a count component's length spread
# (longest over shortest member) may grow over its input value (0: unbounded).
SHAPE_SPREAD_RATIO = 0.0
# Two straight edges at a vertex are one block line when their directions
# differ by at most this (degrees).
LINE_ANGLE = 1.0


@dataclass(frozen=True)
class SmoothOptions:
    """Tuning of the optimiser; the defaults are what the measurements chose."""

    samples: int = 8          # sampled segments per edge besides the end cells
    power: float = 2.0        # face cost tan(phi)**power
    sweeps: int = 40          # phase-1 sweeps at most
    minmax_rounds: int = 60   # phase-2 rounds at most
    minmax_blocks: int = 6    # blocks whose vertices phase 2 moves per round
    step: float = 0.15        # first trial step, as a fraction of the shortest incident edge
    min_step: float = 1.0e-3  # smallest step, as a fraction of the first one
    converged: float = 1.0e-4  # a sweep gaining less than this fraction of the sum ends a phase
    band_tolerance: float = 0.05
    # A band spoke may turn away from the wall normal up to this angle
    # (degrees), or not at all beyond its own when that is larger.
    wall_angle: float = 0.0
    # Cell sizes: at every vertex the first-cell widths of any two edges -
    # an interface jump or a corner cell's aspect - may grow by this fraction,
    # or up to ``size_cap`` where they were below it.
    size_slack: float = 0.1
    size_cap: float = 2.5
    # An interface jump below this may not cross it either, however far it
    # was above ``size_cap`` (0: no ceiling): the slack let a jump of 4.73
    # grow to 5.20, past the benchmark's target.
    size_ceiling: float = SIZE_CEILING
    aspect_cap: float = 100.0  # the same for a corner cell's aspect ratio (0: unconstrained)
    # Every edge keeps its length within this fraction (0: unbounded), so a
    # thin strip such as a wake band is not widened tenfold.
    length_tolerance: float = 0.5
    verify_cells: int = VERIFY_CELLS
    # ``real`` judges the mesh the stored counts and grading define;
    # ``shape`` judges the count-free block map - every block at
    # ``shape_cells`` even parameter fractions a side, untangled at
    # ``fold_cells`` - with no size limits (there are no cells to keep) and
    # only ``shape_length_ratio`` on the lengths, the wall-band rule kept.
    # Its result leaves the counts and grading stale: sizing has to follow.
    mode: str = "real"
    shape_cells: int = 8
    fold_cells: int = FOLD_CELLS
    # A shape result whose stored counts and grading would fold a real cell
    # is refused unless this is set (because ``size`` will follow).
    allow_stale_fold: bool = False
    # In shape mode every edge keeps at least this fraction of its input
    # length (0: unbounded): with no cells to protect, the count-free
    # optimum can still collapse an edge, and sizing then has to resolve
    # the sliver with a forced-fine count.
    shape_length_ratio: float = SHAPE_LENGTH_RATIO
    # In shape mode the length ratio of every interface pair - two edges
    # continuing each other through a vertex across a shared edge, the
    # pairs whose first-cell widths the quality screen's size jump compares
    # (``MeshModel.interface_pairs``) - stays within this factor of its
    # input value (0: unbounded): an edge shortened against the edge it
    # continues is a size jump ``size`` has to grade away.
    shape_pair_ratio: float = SHAPE_PAIR_RATIO
    # In shape mode the length spread of every count component - its
    # longest member over its shortest, the cell-size ratio its one shared
    # count puts between them before any grading - may grow to at most this
    # factor of its input value (0: unbounded). A spread that falls (the
    # square cylinder's kites shrinking together) is never limited.
    shape_spread_ratio: float = SHAPE_SPREAD_RATIO
    # An interior vertex on a straight block line - two internal edges at it
    # that share no block, both ``line`` and collinear within ``LINE_ANGLE``
    # degrees - moves only along that line; on two such lines it stays.
    keep_lines: bool = False
    # Boundary vertices on a smooth path of one patch slide along it
    # (:mod:`blockdrawer.slide`): corners, patch junctions and held vertices
    # stay; a wall vertex carries its band spoke's free end with it.
    slide: bool = False


@dataclass(frozen=True)
class SmoothResult:
    """What a smoothing did, before and after, at full sampled resolution."""

    applied: bool
    reason: str
    before: dict[str, Any]
    after: dict[str, Any]
    free_vertices: int
    moved: int
    max_displacement: float
    band_spokes: int
    max_band_change: float
    max_edge_change: float
    scale: float
    sweeps: int
    minmax_rounds: int
    held: tuple[str, ...] = field(default_factory=tuple)
    positions: dict[str, tuple[float, float]] = field(default_factory=dict, repr=False)
    mode: str = "real"
    # In shape mode: the mesh the stored counts and grading define, before and after.
    real_before: dict[str, Any] | None = None
    real_after: dict[str, Any] | None = None
    # Sliding boundary vertices: how many could, how many did, the largest
    # slide (arc length) and the largest distance of a re-cut boundary edge
    # from the input boundary, absolute and over its chain's length.
    sliders: int = 0
    slid: int = 0
    max_slide: float = 0.0
    boundary_deviation: float = 0.0
    boundary_deviation_relative: float = 0.0
    # The shortest and longest edge over its input length, and those edges.
    min_edge_ratio: float = 1.0
    min_edge: str | None = None
    max_edge_ratio: float = 1.0
    max_edge: str | None = None
    # With keep_lines: free vertices moved only along their straight block
    # line, and those that stayed because they sit on two.
    line_vertices: int = 0
    line_fixed: int = 0
    # The largest factor by which an interface pair's length ratio left its
    # input value (1 when none moved), and that pair (``a-b/c-d``).
    max_pair_change: float = 1.0
    max_pair: str | None = None
    # In shape mode: the largest factor by which a count component's length
    # spread grew (1 when none did), and its shortest member (``a-b``).
    max_spread_growth: float = 1.0
    spread_edge: str | None = None

    def to_data(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self.mode != "real":
            extra = {"real_before": self.real_before, "real_after": self.real_after}
        if self.sliders:
            extra.update({
                "sliders": self.sliders,
                "slid": self.slid,
                "max_slide": self.max_slide,
                "boundary_deviation": self.boundary_deviation,
                "boundary_deviation_relative": self.boundary_deviation_relative,
            })
        return {
            "mode": self.mode,
            **extra,
            "applied": self.applied,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "free_vertices": self.free_vertices,
            "moved": self.moved,
            "max_displacement": self.max_displacement,
            "band_spokes": self.band_spokes,
            "max_band_change": self.max_band_change,
            "max_edge_change": self.max_edge_change,
            "min_edge_ratio": self.min_edge_ratio,
            "min_edge": self.min_edge,
            "max_edge_ratio": self.max_edge_ratio,
            "max_edge": self.max_edge,
            "line_vertices": self.line_vertices,
            "line_fixed": self.line_fixed,
            "max_pair_change": self.max_pair_change,
            "max_pair": self.max_pair,
            "max_spread_growth": self.max_spread_growth,
            "spread_edge": self.spread_edge,
            "scale": self.scale,
            "sweeps": self.sweeps,
            "minmax_rounds": self.minmax_rounds,
        }


def _verdict(sample: ModelSample) -> dict[str, Any]:
    summary = sample.summary()
    return {
        "max_non_orthogonality": summary.get("max_non_orthogonality", 0.0),
        "average_non_orthogonality": summary.get("average_non_orthogonality", 0.0),
        "severe_faces": summary.get("severe_faces", 0),
        "folded_cells": summary.get("folded_cells", 0),
        "worst_block": summary.get("worst_block"),
        "max_skewness": summary.get("max_skewness", 0.0),
    }


# checkMesh fails a mesh whose face skewness exceeds this.
SKEWNESS_LIMIT = 4.0


def _no_worse(after: dict[str, Any], before: dict[str, Any]) -> bool:
    return (
        after["folded_cells"] == 0
        and after["severe_faces"] <= before["severe_faces"]
        and after["max_non_orthogonality"] <= before["max_non_orthogonality"] + 1.0e-9
        and not (
            after.get("max_skewness", 0.0) > SKEWNESS_LIMIT
            and before.get("max_skewness", 0.0) <= SKEWNESS_LIMIT
        )
    )


# ---------------------------------------------------------------------------
# The optimiser's own sampled state
# ---------------------------------------------------------------------------


def _indices(cells: int, samples: int) -> tuple[int, ...]:
    """Real node indices kept along an edge: two real cells at each end and
    at ``samples`` even stations, symmetric so either traversal keeps the
    same set. Faces are measured only between real cells (consecutive
    indices): a face between merged cells is no mesh face, and across an
    interface of thin first cells and long merged ones it read 75 degrees
    where checkMesh read 43."""
    starts = {0, cells - 2}
    for k in range(1, samples):
        starts.add(round(k * cells / samples) - 1)
    keep: set[int] = set()
    for start in starts:
        for index in (start, start + 1, start + 2):
            if 0 <= index <= cells:
                keep.add(index)
                keep.add(cells - index)
    keep.update((0, cells))
    return tuple(sorted(keep))


@dataclass
class _BlockEval:
    cost: float
    worst: float  # largest tan(phi)**2 of the block's own faces
    sides: list   # per side: (edge, canonical, nodes, centres) in canonical order


class _State:
    def __init__(self, model: MeshModel, options: SmoothOptions) -> None:
        self.options = options
        self.half_power = options.power / 2.0
        self.coords: dict[str, complex] = {
            vertex.id: complex(vertex.x, vertex.y) for vertex in model.vertices.values()
        }
        self.blocks = list(model.blocks)
        self.edge_samples: dict[EdgeKey, list[tuple[float, complex]]] = {}
        # Per edge, in canonical order: whether each sampled interval is one real cell.
        self.edge_real: dict[EdgeKey, list[bool]] = {}
        self.vertex_edges: dict[str, list[EdgeKey]] = {}
        self.vertex_blocks: dict[str, list[int]] = {}
        self.block_edges: list[list[EdgeKey]] = []
        self.edge_blocks: dict[EdgeKey, list[int]] = {}
        for position, block in enumerate(self.blocks):
            edges = [edge_key(*block.directed_edge(index)) for index in range(4)]
            self.block_edges.append(edges)
            for current in edges:
                self.edge_blocks.setdefault(current, []).append(position)
            for vertex_id in block.vertices:
                self.vertex_blocks.setdefault(vertex_id, []).append(position)
        for current in self.edge_blocks:
            if options.mode == "shape":
                count = options.shape_cells
                fractions = tuple(index / count for index in range(count + 1))
                self.edge_real[current] = [True] * count
            else:
                cells = model.edge_cells[current]
                indices = _indices(cells, options.samples)
                self.edge_real[current] = [
                    second - first == 1 for first, second in zip(indices, indices[1:])
                ]
                fractions = model.edge_node_fractions(current, indices)
            points = model.edge_points(current, fractions)
            self.edge_samples[current] = [
                (fraction, complex(x, y)) for fraction, (x, y) in zip(fractions, points)
            ]
            for vertex_id in current:
                self.vertex_edges.setdefault(vertex_id, []).append(current)
        # The interpolation is linear in the edge points and corners with
        # weights fixed by the graded fractions, which a similarity keeps:
        # they are computed once.
        self.weights = [self._weights(position) for position in range(len(self.blocks))]
        self.min_sine = [
            [min(MIN_CORNER_SINE, value) for value in self._corner_sines(position)]
            for position in range(len(self.blocks))
        ]
        self.evals: list[_BlockEval | None] = [
            self._evaluate(position) for position in range(len(self.blocks))
        ]
        self.interfaces: dict[EdgeKey, tuple[float, float]] = {
            current: self._interface(current, {})
            for current, owners in self.edge_blocks.items() if len(owners) == 2
        }

    # -- geometry ----------------------------------------------------------

    def _corner_sines(self, position: int) -> list[float]:
        points = [self.coords[vertex_id] for vertex_id in self.blocks[position].vertices]
        sines = []
        for index in range(4):
            a = points[index - 1]
            b = points[index]
            c = points[(index + 1) % 4]
            first = b - a
            second = c - b
            lengths = abs(first) * abs(second)
            sines.append(
                (first.conjugate() * second).imag / lengths if lengths > 0.0 else -1.0
            )
        return sines

    def _local(self, current: EdgeKey, start: str) -> list[tuple[float, complex]]:
        samples = self.edge_samples[current]
        if start == current[0]:
            return samples
        return [(1.0 - fraction, point) for fraction, point in reversed(samples)]

    def _weights(self, position: int) -> list[list[tuple[float, ...]]]:
        v0, v1, v2, v3 = self.blocks[position].vertices
        bottom = [fraction for fraction, _ in self._local(edge_key(v0, v1), v0)]
        right = [fraction for fraction, _ in self._local(edge_key(v1, v2), v1)]
        top = [fraction for fraction, _ in self._local(edge_key(v3, v2), v3)]
        left = [fraction for fraction, _ in self._local(edge_key(v0, v3), v0)]
        rows = []
        for rf, lf in zip(right, left):
            row = []
            for bf, tf in zip(bottom, top):
                w00 = (1.0 - bf) * (1.0 - lf)
                w10 = bf * (1.0 - rf)
                w11 = tf * rf
                w01 = (1.0 - tf) * lf
                inverse = 1.0 / (w00 + w10 + w11 + w01)
                w00 *= inverse
                w10 *= inverse
                w11 *= inverse
                w01 *= inverse
                bw, tw, lw, rw = w00 + w10, w11 + w01, w00 + w01, w10 + w11
                row.append((
                    bw, tw, lw, rw,
                    w00 / 3.0 - 2.0 / 3.0 * (bw * (1.0 - bf) + lw * (1.0 - lf)),
                    w10 / 3.0 - 2.0 / 3.0 * (bw * bf + rw * (1.0 - rf)),
                    w11 / 3.0 - 2.0 / 3.0 * (tw * tf + rw * rf),
                    w01 / 3.0 - 2.0 / 3.0 * (tw * (1.0 - tf) + lw * lf),
                ))
            rows.append(row)
        return rows

    def _real(self, current: EdgeKey, start: str) -> list[bool]:
        flags = self.edge_real[current]
        return flags if start == current[0] else flags[::-1]

    def _evaluate(self, position: int) -> _BlockEval | None:
        """The block's face cost and worst face, or ``None`` when inadmissible."""
        block = self.blocks[position]
        sines = self._corner_sines(position)
        for sine, floor in zip(sines, self.min_sine[position]):
            if sine < floor or sine <= 1.0e-9:
                return None
        v0, v1, v2, v3 = block.vertices
        c00, c10, c11, c01 = (self.coords[v] for v in block.vertices)
        bottom = self._local(edge_key(v0, v1), v0)
        right = self._local(edge_key(v1, v2), v1)
        top = self._local(edge_key(v3, v2), v3)
        left = self._local(edge_key(v0, v3), v0)
        x_real = self._real(edge_key(v0, v1), v0)
        y_real = self._real(edge_key(v1, v2), v1)
        weights = self.weights[position]
        bottom_points = [point for _fraction, point in bottom]
        top_points = [point for _fraction, point in top]
        nodes = []
        for row_weights, (_rf, rp), (_lf, lp) in zip(weights, right, left):
            nodes.append([
                kb * bp + kt * tp + kl * lp + kr * rp
                + k00 * c00 + k10 * c10 + k11 * c11 + k01 * c01
                for (kb, kt, kl, kr, k00, k10, k11, k01), bp, tp
                in zip(row_weights, bottom_points, top_points)
            ])
        centres: list[list[complex]] = []
        for row, upper in zip(nodes, nodes[1:]):
            line = []
            for a in range(len(row) - 1):
                p0 = row[a]
                p1 = row[a + 1]
                p2 = upper[a + 1]
                p3 = upper[a]
                # Two triangles of the fan from p0: the shoelace area and
                # centroid of the quadrilateral, in fewer operations.
                ux, uy = p1.real - p0.real, p1.imag - p0.imag
                vx, vy = p2.real - p0.real, p2.imag - p0.imag
                wx, wy = p3.real - p0.real, p3.imag - p0.imag
                first = ux * vy - uy * vx
                second = vx * wy - vy * wx
                area = first + second
                if area <= 0.0:
                    return None
                line.append((first * (p0 + p1 + p2) + second * (p0 + p2 + p3)) / (3.0 * area))
            centres.append(line)
        cost = 0.0
        worst = 0.0
        half = self.half_power
        for j, line in enumerate(centres):
            if not y_real[j]:
                continue
            lower = nodes[j]
            upper = nodes[j + 1]
            for i in range(len(line) - 1):
                if not (x_real[i] and x_real[i + 1]):
                    continue
                # Face up the grid between cells (j, i) and (j, i + 1); its
                # right-hand normal (ey, -ex) points to +x.
                edge = upper[i + 1] - lower[i + 1]
                delta = line[i + 1] - line[i]
                nx, ny = edge.imag, -edge.real
                dot = delta.real * nx + delta.imag * ny
                if dot <= 0.0:
                    return None
                cross = delta.real * ny - delta.imag * nx
                value = (cross * cross) / (dot * dot)
                cost += value ** half
                if value > worst:
                    worst = value
        for j in range(len(centres) - 1):
            if not (y_real[j] and y_real[j + 1]):
                continue
            row = nodes[j + 1]
            here = centres[j]
            above = centres[j + 1]
            for i in range(len(here)):
                if not x_real[i]:
                    continue
                # Face along +x between cells (j, i) and (j + 1, i); its
                # left-hand normal (-ey, ex) points to +y.
                edge = row[i + 1] - row[i]
                delta = above[i] - here[i]
                nx, ny = -edge.imag, edge.real
                dot = delta.real * nx + delta.imag * ny
                if dot <= 0.0:
                    return None
                cross = delta.real * ny - delta.imag * nx
                value = (cross * cross) / (dot * dot)
                cost += value ** half
                if value > worst:
                    worst = value
        last_row = len(nodes) - 1
        last_column = len(nodes[0]) - 1
        natural = (
            ((v0, v1), list(nodes[0]), list(centres[0])),
            ((v1, v2), [row[last_column] for row in nodes], [row[-1] for row in centres]),
            ((v3, v2), list(nodes[last_row]), list(centres[-1])),
            ((v0, v3), [row[0] for row in nodes], [row[0] for row in centres]),
        )
        sides = []
        for index, ((first, _second), side_nodes, side_cells) in enumerate(natural):
            current = edge_key(*block.directed_edge(index))
            if first != current[0]:
                side_nodes.reverse()
                side_cells.reverse()
            sides.append((current, block.directed_edge(index) == current, side_nodes, side_cells))
        return _BlockEval(cost, worst, sides)

    def _side(self, position: int, current: EdgeKey, evals: dict[int, _BlockEval | None]):
        evaluation = evals[position] if position in evals else self.evals[position]
        if evaluation is None:
            return None
        for side in evaluation.sides:
            if side[0] == current:
                return side
        return None  # pragma: no cover - every block has its four edges

    def _interface(
        self, current: EdgeKey, evals: dict[int, _BlockEval | None]
    ) -> tuple[float, float]:
        owner, other = sorted(self.edge_blocks[current])
        first = self._side(owner, current, evals)
        second = self._side(other, current, evals)
        if first is None or second is None:
            return INF, INF
        _edge, canonical, nodes, own_cells = first
        other_cells = second[3]
        cost = 0.0
        worst = 0.0
        half = self.half_power
        real = self.edge_real[current]
        for k, (own, neighbour) in enumerate(zip(own_cells, other_cells)):
            if not real[k]:
                continue
            edge = nodes[k + 1] - nodes[k]
            normal = -1j * edge if canonical else 1j * edge
            value = _tan2(neighbour - own, normal)
            if value == INF:
                return INF, INF
            cost += value ** half
            if value > worst:
                worst = value
        return cost, worst

    # -- moves -------------------------------------------------------------

    def _move(self, vertex_id: str, target: complex) -> list:
        """Move a vertex in the sampled state; returns what restores it."""
        old = self.coords[vertex_id]
        saved = [(vertex_id, old, [])]
        if target == old:
            return saved
        for current in self.vertex_edges.get(vertex_id, ()):
            other = self.coords[current[1] if current[0] == vertex_id else current[0]]
            chord = old - other
            if abs(chord) <= 0.0:
                continue
            factor = (target - other) / chord
            samples = self.edge_samples[current]
            saved[0][2].append((current, samples))
            self.edge_samples[current] = [
                (fraction, other + factor * (point - other)) for fraction, point in samples
            ]
        self.coords[vertex_id] = target
        return saved

    def _restore(self, saved: list) -> None:
        for item in reversed(saved):
            if item[0] == "slide":
                _tag, vertex_id, s_old, rotation_old, scratch_old = item
                self.s[vertex_id] = s_old
                self.rotation[vertex_id] = rotation_old
                for current, (x, y, geometry) in scratch_old.items():
                    if current in self.scratch.vertices:
                        self.scratch.vertices[current].x, self.scratch.vertices[current].y = x, y
                    elif geometry is None:
                        self.scratch.edge_geometry.pop(current, None)
                    else:
                        self.scratch.edge_geometry[current] = geometry
                continue
            vertex_id, old, edges = item
            self.coords[vertex_id] = old
            for current, samples in edges:
                self.edge_samples[current] = samples

    # -- sliding boundary vertices ------------------------------------------

    def attach_slides(self, model: MeshModel, chains: dict) -> None:
        from .slide import neighbours

        self.chains = chains
        self.scratch = from_data(to_data(model)) if chains else None
        self.s = {v: chain.stations[chain.index[v]] for v, chain in chains.items()}
        self.s0 = dict(self.s)
        self.rotation = {v: 1.0 + 0j for v in chains}
        self.tangent0 = {v: chain.tangent(self.s[v]) for v, chain in chains.items()}
        self.slide_edges = {}
        for v, chain in chains.items():
            before, after = neighbours(chain, v)
            self.slide_edges[v] = (before, after, edge_key(before, v), edge_key(v, after))

    def station(self, vertex_id: str, chain) -> float:
        if vertex_id in self.s and self.chains.get(vertex_id) is chain:
            return self.s[vertex_id]
        return chain.stations[chain.index[vertex_id]]

    def slide_bounds(self, vertex_id: str) -> tuple[float, float]:
        chain = self.chains[vertex_id]
        before, after, _e1, _e2 = self.slide_edges[vertex_id]
        low = self.station(before, chain)
        high = self.station(after, chain)
        if chain.closed and chain.index[after] == 0:
            high = chain.length
        return low, high

    def _move_slide(self, vertex_id: str, s_new: float) -> list:
        """Slide a boundary vertex to arc length ``s_new`` of its chain: its two
        boundary edges re-cut from the original path, its other edges carried."""
        from .slide import oriented

        chain = self.chains[vertex_id]
        before, after, first_edge, second_edge = self.slide_edges[vertex_id]
        low, high = self.slide_bounds(vertex_id)
        old = self.coords[vertex_id]
        target = chain.point(s_new)
        scratch_old = {
            vertex_id: (self.scratch.vertices[vertex_id].x, self.scratch.vertices[vertex_id].y, None),
            first_edge: (0.0, 0.0, self.scratch.edge_geometry.get(first_edge)),
            second_edge: (0.0, 0.0, self.scratch.edge_geometry.get(second_edge)),
        }
        saved: list = [("slide", vertex_id, self.s[vertex_id], self.rotation[vertex_id], scratch_old)]
        moved_edges = []
        for current in self.vertex_edges.get(vertex_id, ()):
            if current in (first_edge, second_edge):
                continue
            other = self.coords[current[1] if current[0] == vertex_id else current[0]]
            chord = old - other
            if abs(chord) <= 0.0:
                continue
            factor = (target - other) / chord
            samples = self.edge_samples[current]
            moved_edges.append((current, samples))
            self.edge_samples[current] = [
                (fraction, other + factor * (point - other)) for fraction, point in samples
            ]
        self.scratch.vertices[vertex_id].x, self.scratch.vertices[vertex_id].y = target.real, target.imag
        for current, a, b, s0, s1 in ((first_edge, before, vertex_id, low, s_new),
                                      (second_edge, vertex_id, after, s_new, high)):
            geometry = oriented(chain.recut(s0, s1), a, b)
            if geometry is None:
                self.scratch.edge_geometry.pop(current, None)
            else:
                self.scratch.edge_geometry[current] = geometry
            samples = self.edge_samples[current]
            moved_edges.append((current, samples))
            fractions = [fraction for fraction, _point in samples]
            points = self.scratch.edge_points(current, fractions)
            self.edge_samples[current] = [
                (fraction, complex(x, y)) for fraction, (x, y) in zip(fractions, points)
            ]
        saved.append((vertex_id, old, moved_edges))
        self.coords[vertex_id] = target
        self.s[vertex_id] = s_new
        self.rotation[vertex_id] = chain.tangent(s_new) / self.tangent0[vertex_id]
        return saved

    def local_many(self, vertex_ids, limit: float = INF):
        positions = sorted({p for v in vertex_ids for p in self.vertex_blocks.get(v, ())})
        evals: dict[int, _BlockEval | None] = {}
        cost = 0.0
        worst = 0.0
        for position in positions:
            evaluation = self._evaluate(position)
            evals[position] = evaluation
            if evaluation is None:
                return INF, INF, evals, {}
            cost += evaluation.cost
            worst = max(worst, evaluation.worst)
            if cost >= limit:
                return INF, INF, evals, {}
        interfaces: dict[EdgeKey, tuple[float, float]] = {}
        for position in positions:
            for current in self.block_edges[position]:
                if current in interfaces or len(self.edge_blocks[current]) != 2:
                    continue
                value = self._interface(current, evals)
                interfaces[current] = value
                if value[0] == INF:
                    return INF, INF, evals, interfaces
                cost += value[0]
                worst = max(worst, value[1])
        return cost, worst, evals, interfaces

    def current_local_many(self, vertex_ids) -> tuple[float, float]:
        positions = sorted({p for v in vertex_ids for p in self.vertex_blocks.get(v, ())})
        cost = 0.0
        worst = 0.0
        seen: set[EdgeKey] = set()
        for position in positions:
            evaluation = self.evals[position]
            if evaluation is None:
                return INF, INF
            cost += evaluation.cost
            worst = max(worst, evaluation.worst)
            for current in self.block_edges[position]:
                if current in seen or len(self.edge_blocks[current]) != 2:
                    continue
                seen.add(current)
                value = self.interfaces[current]
                cost += value[0]
                worst = max(worst, value[1])
        return cost, worst

    def local(self, vertex_id: str, limit: float = INF) -> tuple[float, float, dict, dict]:
        """Cost and worst tan^2 over the vertex's blocks and their interfaces;
        evaluation stops (returning ``inf``) once the cost reaches ``limit``."""
        positions = self.vertex_blocks.get(vertex_id, ())
        evals: dict[int, _BlockEval | None] = {}
        cost = 0.0
        worst = 0.0
        for position in positions:
            evaluation = self._evaluate(position)
            evals[position] = evaluation
            if evaluation is None:
                return INF, INF, evals, {}
            cost += evaluation.cost
            worst = max(worst, evaluation.worst)
            if cost >= limit:
                return INF, INF, evals, {}
        interfaces: dict[EdgeKey, tuple[float, float]] = {}
        for position in positions:
            for current in self.block_edges[position]:
                if current in interfaces or len(self.edge_blocks[current]) != 2:
                    continue
                value = self._interface(current, evals)
                interfaces[current] = value
                if value[0] == INF:
                    return INF, INF, evals, interfaces
                cost += value[0]
                worst = max(worst, value[1])
        return cost, worst, evals, interfaces

    def current_local(self, vertex_id: str) -> tuple[float, float]:
        positions = self.vertex_blocks.get(vertex_id, ())
        cost = 0.0
        worst = 0.0
        seen: set[EdgeKey] = set()
        for position in positions:
            evaluation = self.evals[position]
            if evaluation is None:
                return INF, INF
            cost += evaluation.cost
            worst = max(worst, evaluation.worst)
            for current in self.block_edges[position]:
                if current in seen or len(self.edge_blocks[current]) != 2:
                    continue
                seen.add(current)
                value = self.interfaces[current]
                cost += value[0]
                worst = max(worst, value[1])
        return cost, worst

    def commit(self, evals: dict, interfaces: dict) -> None:
        for position, evaluation in evals.items():
            self.evals[position] = evaluation
        self.interfaces.update(interfaces)

    def block_worst(self, position: int) -> float:
        evaluation = self.evals[position]
        if evaluation is None:
            return INF
        worst = evaluation.worst
        for current in self.block_edges[position]:
            if len(self.edge_blocks[current]) == 2:
                worst = max(worst, self.interfaces[current][1])
        return worst

    def global_worst(self) -> float:
        return max((self.block_worst(position) for position in range(len(self.blocks))), default=0.0)

    def total_cost(self) -> float:
        return sum(
            (evaluation.cost if evaluation is not None else INF) for evaluation in self.evals
        ) + sum(value[0] for value in self.interfaces.values())


def _width(state: "_State", per_chord: dict, current: EdgeKey, vertex_id: str) -> float:
    chord = abs(state.coords[current[0]] - state.coords[current[1]])
    return max(chord * per_chord.get((current, vertex_id), 0.0), 1.0e-300)


def _tan2(delta: complex, normal: complex) -> float:
    """tan^2 of the angle between ``delta`` and ``normal``; ``inf`` at 90 degrees or more."""
    dot = (delta.conjugate() * normal).real
    if dot <= 0.0:
        return INF
    cross = (delta.conjugate() * normal).imag
    return (cross * cross) / (dot * dot)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def boundary_vertices(model: MeshModel) -> set[str]:
    found: set[str] = set()
    for current, occurrences in model.edge_occurrences().items():
        if len(occurrences) == 1:
            found.update(current)
    return found


def wall_vertices(model: MeshModel) -> set[str]:
    walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    found: set[str] = set()
    for current, name in model.edge_boundaries.items():
        if name in walls:
            found.update(current)
    return found


@dataclass(frozen=True)
class Spoke:
    """An internal edge from a wall vertex to a free vertex: a band spoke."""

    wall: str
    length: float           # its chord, to be kept within ``band_tolerance``
    normal: complex | None  # the wall's normal at the wall vertex, into the fluid
    misalignment: float     # its angle from that normal, radians, not to grow


# The wall normal binds a spoke only where the wall is smooth: at a corner
# turning more than this (degrees) the spokes of a fan or seam spread on
# purpose, and on 30P30N holding them to the bisector cost 17 severe faces.
SMOOTH_WALL_TURN = 30.0


def _wall_normal(model: MeshModel, wall: str, walls: set[str], spoke: complex) -> complex | None:
    """The wall normal at a smooth wall vertex, on the spoke's side; ``None``
    at a corner or where the wall ends."""
    here = model.vertices[wall]
    tangents = []
    for current, name in sorted(model.edge_boundaries.items()):
        if name not in walls or wall not in current:
            continue
        fraction = 1.0e-4 if current[0] == wall else 1.0 - 1.0e-4
        ((x, y),) = model.edge_points(current, (fraction,))
        tangent = complex(x - here.x, y - here.y)
        if abs(tangent) > 0.0:
            tangents.append(tangent / abs(tangent))
    if len(tangents) != 2:
        return None
    straight = -tangents[0] / tangents[1]
    if abs(math.degrees(math.atan2(straight.imag, straight.real))) > SMOOTH_WALL_TURN:
        return None
    chord = tangents[0] - tangents[1]
    if abs(chord) <= 1.0e-12:
        return None
    normal = 1j * chord / abs(chord)
    if (normal.conjugate() * spoke).real < 0.0:
        normal = -normal
    return normal


def band_spokes(model: MeshModel, free: Iterable[str]) -> dict[str, list[Spoke]]:
    """For each free vertex, its internal edges to wall vertices (band spokes)."""
    wall_patches = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    walls = wall_vertices(model)
    occurrences = model.edge_occurrences()
    free = set(free)
    spokes: dict[str, list[Spoke]] = {}
    fans: dict[str, int] = {}
    for current, used in occurrences.items():
        if len(used) == 2:
            for wall in current:
                if wall in walls:
                    fans[wall] = fans.get(wall, 0) + 1
    for current, used in occurrences.items():
        if len(used) != 2:
            continue
        for wall, other in (current, current[::-1]):
            if wall in walls and other in free:
                a = model.vertices[wall]
                b = model.vertices[other]
                offset = complex(b.x - a.x, b.y - a.y)
                # Only a wall vertex's single spoke is held to the normal; the
                # spokes of a fan spread on purpose.
                normal = (
                    _wall_normal(model, wall, wall_patches, offset)
                    if fans.get(wall, 0) == 1 else None
                )
                angle = 0.0 if normal is None else abs(math.atan2(
                    (normal.conjugate() * offset).imag, (normal.conjugate() * offset).real
                ))
                spokes.setdefault(other, []).append(Spoke(wall, abs(offset), normal, angle))
    return spokes


def select_free(
    model: MeshModel,
    *,
    blocks: Sequence[str] | None = None,
    bbox: Sequence[float] | None = None,
    hold: Sequence[str] = (),
) -> list[str]:
    """Free vertices in model order: used by a block, not on a boundary edge,
    within the scope and not held."""
    used = []
    seen: set[str] = set()
    for block in model.blocks:
        for vertex_id in block.vertices:
            if vertex_id not in seen:
                seen.add(vertex_id)
                used.append(vertex_id)
    fixed = boundary_vertices(model) | set(hold)
    for vertex_id in hold:
        if vertex_id not in model.vertices:
            raise TopologyError(f"Unknown vertex {vertex_id!r}")
    scope: set[str] | None = None
    if blocks:
        known = {block.id: block for block in model.blocks}
        unknown = [identifier for identifier in blocks if identifier not in known]
        if unknown:
            raise TopologyError(f"Unknown block(s) {', '.join(unknown)}")
        scope = {vertex_id for identifier in blocks for vertex_id in known[identifier].vertices}
    if bbox is not None:
        if len(bbox) != 4:
            raise TopologyError("bbox needs four numbers: xmin, ymin, xmax, ymax")
        xmin, ymin, xmax, ymax = (float(value) for value in bbox)
        inside = {
            vertex_id for vertex_id in seen
            if xmin <= model.vertices[vertex_id].x <= xmax
            and ymin <= model.vertices[vertex_id].y <= ymax
        }
        scope = inside if scope is None else scope & inside
    return [
        vertex_id for vertex_id in used
        if vertex_id not in fixed and (scope is None or vertex_id in scope)
    ]


def straight_lines(model: MeshModel, vertices: Iterable[str]) -> dict[str, complex | None]:
    """The straight block lines through the given vertices.

    A vertex lies on a block line when two of its edges share no block (the
    opposite edges of a four-block vertex), are both ``line`` and are
    collinear within ``LINE_ANGLE`` degrees. Returned per vertex on one line:
    the line's unit direction; on two or more distinct lines: ``None`` (it
    cannot move). Vertices on no line are left out."""
    edge_blocks: dict[EdgeKey, set[int]] = {}
    vertex_edges: dict[str, list[EdgeKey]] = {}
    for position, block in enumerate(model.blocks):
        for index in range(4):
            current = edge_key(*block.directed_edge(index))
            owners = edge_blocks.setdefault(current, set())
            if not owners:
                for vertex_id in current:
                    vertex_edges.setdefault(vertex_id, []).append(current)
            owners.add(position)
    tolerance = math.sin(math.radians(LINE_ANGLE))
    found: dict[str, complex | None] = {}
    for vertex_id in vertices:
        here = model.vertices[vertex_id]
        origin = complex(here.x, here.y)
        rays = []
        for current in vertex_edges.get(vertex_id, ()):
            if len(edge_blocks[current]) != 2 or model.edge_geometry.get(current) is not None:
                continue
            other = model.vertices[current[1] if current[0] == vertex_id else current[0]]
            offset = complex(other.x, other.y) - origin
            if abs(offset) > 0.0:
                rays.append((current, offset / abs(offset)))
        directions: list[complex] = []
        for k, (first, a) in enumerate(rays):
            for second, b in rays[k + 1:]:
                if edge_blocks[first] & edge_blocks[second]:
                    continue
                # Opposite rays: a is close to -b.
                if (a.conjugate() * b).real >= 0.0 or abs((a.conjugate() * b).imag) > tolerance:
                    continue
                direction = (a - b) / abs(a - b)
                if not any(abs((direction.conjugate() * known).imag) <= tolerance for known in directions):
                    directions.append(direction)
        if len(directions) == 1:
            found[vertex_id] = directions[0]
        elif directions:
            found[vertex_id] = None
    return found


# ---------------------------------------------------------------------------
# The optimiser
# ---------------------------------------------------------------------------


_DIRECTIONS = tuple(complex(math.cos(k * math.pi / 4.0), math.sin(k * math.pi / 4.0)) for k in range(8))


def _project(
    target: complex, spokes: list[tuple[complex, Spoke]], tolerance: float
) -> complex | None:
    """Pull ``target`` into every spoke's length annulus and angular sector
    about the wall normal, or ``None`` when that is impossible."""
    if not spokes:
        return target
    for _round in range(6):
        for centre, spoke in spokes:
            offset = target - centre
            distance = abs(offset)
            if distance <= 0.0:
                return None
            if spoke.normal is not None:
                relative = offset / spoke.normal
                angle = math.atan2(relative.imag, relative.real)
                if abs(angle) > spoke.misalignment:
                    turned = math.copysign(spoke.misalignment, angle)
                    offset = spoke.normal * complex(math.cos(turned), math.sin(turned)) * distance
            low = spoke.length * (1.0 - tolerance)
            high = spoke.length * (1.0 + tolerance)
            if distance < low:
                offset *= low / distance
            elif distance > high:
                offset *= high / distance
            target = centre + offset
    for centre, spoke in spokes:
        offset = target - centre
        if abs(abs(offset) / spoke.length - 1.0) > tolerance * (1.0 + 1.0e-6):
            return None
        if spoke.normal is not None:
            relative = offset / spoke.normal
            if abs(math.atan2(relative.imag, relative.real)) > spoke.misalignment + 1.0e-9:
                return None
    return target


def _optimise(
    model: MeshModel, free: list[str], spokes: dict[str, list[Spoke]],
    options: SmoothOptions, chains: dict | None = None,
    lines: dict[str, complex] | None = None,
) -> tuple[dict[str, tuple[float, float]], int, int, dict[str, float]]:
    state = _State(model, options)
    lines = lines or {}
    chains = chains or {}
    state.attach_slides(model, chains)
    sliders = [vertex_id for vertex_id in chains]
    if any(evaluation is None for evaluation in state.evals):
        # A block already folds or carries a flat corner: the barrier cannot
        # be crossed from inside, so the optimiser does not start.
        return {}, 0, 0, {}
    allowance = math.radians(options.wall_angle)
    spokes_of = {
        vertex_id: [replace(spoke, misalignment=max(spoke.misalignment, allowance)) for spoke in items]
        for vertex_id, items in spokes.items()
    }

    def anchors(vertex_id: str) -> list[tuple[complex, Spoke]]:
        # The wall end and its normal where they are now: a slid wall vertex
        # turns its normal with the wall's tangent.
        out = []
        for spoke in spokes_of.get(vertex_id, ()):
            rotation = state.rotation.get(spoke.wall, 1.0 + 0j) if chains else 1.0 + 0j
            normal = None if spoke.normal is None else spoke.normal * rotation
            out.append((state.coords[spoke.wall], replace(spoke, normal=normal)))
        return out

    fronts: dict[str, list[str]] = {}
    for vertex_id, items in spokes.items():
        for spoke in items:
            fronts.setdefault(spoke.wall, []).append(vertex_id)
    free_set_all = set(free)
    first_step: dict[str, float] = {}
    for vertex_id in list(free) + sliders:
        here = state.coords[vertex_id]
        lengths = [
            abs(state.coords[current[0] if current[1] == vertex_id else current[1]] - here)
            for current in state.vertex_edges.get(vertex_id, ())
        ]
        first_step[vertex_id] = options.step * min(lengths) if lengths else 0.0
    step = dict(first_step)
    chords = {
        current: abs(state.coords[current[0]] - state.coords[current[1]])
        for current in state.edge_samples
    }
    stretch = 1.0 + options.length_tolerance
    # First-cell width per unit chord at each end of each edge: a similarity
    # scales the curve with its chord and grading is a ratio, so a width
    # follows its edge's chord.
    width_per_chord = {
        (current, vertex_id): model.edge_width_at_vertex(current, vertex_id) / chords[current]
        for current in state.edge_samples for vertex_id in current
        if chords[current] > 0.0
    }
    size_pairs: dict[str, list[tuple[EdgeKey, EdgeKey, float]]] = {}

    def add_pair(at: str, first: EdgeKey, second: EdgeKey, cap: float, ceiling: float = 0.0) -> None:
        ratio = _width(state, width_per_chord, first, at) / _width(
            state, width_per_chord, second, at
        )
        ratio = max(ratio, 1.0 / ratio)
        # Below the cap a ratio may grow to it, not past it; above it, by
        # the slack at most, and not past the ceiling when it was below.
        limit = cap if ratio <= cap else ratio * (1.0 + options.size_slack)
        if 0.0 < ratio <= ceiling:
            limit = min(limit, ceiling)
        size_pairs.setdefault(at, []).append((first, second, limit))

    # The pairs the quality screen compares: across every internal edge the
    # transverse edges of its two blocks at each end (the size jump), and
    # the two edges of each block corner (the corner cell's aspect ratio).
    interface_pairs = model.interface_pairs()
    for _shared, at, first, second in interface_pairs:
        add_pair(at, first, second, options.size_cap, options.size_ceiling)
    if options.aspect_cap > 0.0:
        for position, block in enumerate(state.blocks):
            for index in range(4):
                at = block.vertices[index]
                add_pair(
                    at,
                    edge_key(*block.directed_edge(index)),
                    edge_key(*block.directed_edge((index + 3) % 4)),
                    options.aspect_cap,
                )

    floor = options.shape_length_ratio
    pair_limit = options.shape_pair_ratio if options.mode == "shape" else 0.0
    # Per edge, the edges it forms an interface pair with and the pair's
    # input length ratio (this edge over the other).
    pairs_of: dict[EdgeKey, dict[EdgeKey, float]] = {}
    if pair_limit > 0.0:
        for _shared, _at, first, second in interface_pairs:
            if chords[first] > 0.0 and chords[second] > 0.0:
                pairs_of.setdefault(first, {})[second] = chords[first] / chords[second]
                pairs_of.setdefault(second, {})[first] = chords[second] / chords[first]

    spread_limit = options.shape_spread_ratio if options.mode == "shape" else 0.0
    # Per count component (those of size, cyclic pairs joined): its members
    # and their input spread, longest over shortest chord.
    component_of: dict[EdgeKey, int] = {}
    members: list[list[EdgeKey]] = []
    spread0: list[float] = []
    if spread_limit > 0.0:
        from .size import count_components  # size imports this module

        for component in count_components(model):
            kept = [current for current in component if current in chords]
            lengths = [chords[current] for current in kept]
            if not lengths or min(lengths) <= 0.0:
                continue
            for current in kept:
                component_of[current] = len(members)
            members.append(kept)
            spread0.append(max(lengths) / min(lengths))

    def lengths_hold(vertex_ids) -> bool:
        """The length and size limits for the edges of the given vertices,
        where they are now in the sampled state."""
        incident = {current for v in vertex_ids for current in state.vertex_edges.get(v, ())}
        if options.mode == "shape":
            # No cells yet, so no size limits: only the floor on each
            # edge's length (a similarity scales a curve with its chord)
            # and, when asked, the interface pairs' length ratios.
            length = {
                current: abs(state.coords[current[0]] - state.coords[current[1]])
                for current in incident
            }
            if floor > 0.0:
                for current in incident:
                    if length[current] < floor * chords[current]:
                        return False
            if pair_limit > 0.0:
                for current in incident:
                    for other, ratio in pairs_of.get(current, {}).items():
                        if other not in length:
                            length[other] = abs(state.coords[other[0]] - state.coords[other[1]])
                        if length[other] <= 0.0:
                            return False
                        change = length[current] / length[other] / ratio
                        if change > pair_limit or change * pair_limit < 1.0:
                            return False
            if spread_limit > 0.0:
                for index in {component_of[c] for c in incident if c in component_of}:
                    lengths = [abs(state.coords[c[0]] - state.coords[c[1]]) for c in members[index]]
                    if min(lengths) <= 0.0 or max(lengths) > spread_limit * spread0[index] * min(lengths):
                        return False
            return True
        if options.length_tolerance > 0.0:
            for current in incident:
                ratio = abs(state.coords[current[0]] - state.coords[current[1]]) / chords[current]
                if ratio > stretch or ratio * stretch < 1.0:
                    return False
        for current in incident:
            for at in current:
                for first, second, limit in size_pairs.get(at, ()):
                    if first not in incident and second not in incident:
                        continue
                    ratio = _width(state, width_per_chord, first, at) / _width(
                        state, width_per_chord, second, at
                    )
                    if ratio > limit or ratio * limit < 1.0:
                        return False
        return True

    def keeps_lengths(vertex_id: str, target: complex) -> bool:
        if options.mode == "shape" and floor <= 0.0 and pair_limit <= 0.0 and spread_limit <= 0.0:
            return True
        old = state.coords[vertex_id]
        state.coords[vertex_id] = target
        try:
            return lengths_hold([vertex_id])
        finally:
            state.coords[vertex_id] = old

    def search(vertex_id: str, minmax: bool) -> bool:
        h = step[vertex_id]
        if h <= options.min_step * first_step[vertex_id]:
            return False
        cost, worst = state.current_local(vertex_id)
        base = state.coords[vertex_id]
        best = None
        axis = lines.get(vertex_id)
        for direction in (_DIRECTIONS if axis is None else (axis, -axis)):
            target = _project(base + h * direction, anchors(vertex_id), options.band_tolerance)
            if target is not None and axis is not None:
                # A vertex on a straight block line stays on it: a band
                # spoke's projection may only slide it along the line.
                along = (target - base) * axis.conjugate()
                if abs(along.imag) > 1.0e-9 * h:
                    continue
                target = base + axis * along.real
            if target is None or target == base or not keeps_lengths(vertex_id, target):
                continue
            saved = state._move(vertex_id, target)
            new_cost, new_worst, evals, interfaces = state.local(
                vertex_id, INF if minmax else cost
            )
            state._restore(saved)
            if new_cost == INF:
                continue
            if minmax:
                better = new_worst < worst * (1.0 - 1.0e-9) or (
                    new_worst <= worst and new_cost < cost * (1.0 - 1.0e-9)
                )
                key = (new_worst, new_cost)
            else:
                better = new_cost < cost * (1.0 - 1.0e-9) and new_worst <= worst * (1.0 + 1.0e-12)
                key = (new_cost, new_worst)
            if better and (best is None or key < best[0]):
                best = (key, target, evals, interfaces)
        if best is None:
            step[vertex_id] = h * 0.5
            return False
        state._move(vertex_id, best[1])
        state.commit(best[2], best[3])
        return True

    def slide(vertex_id: str, minmax: bool) -> bool:
        """Slide a boundary vertex along its chain, carrying the free end of
        each band spoke on it (length and angle to the wall normal kept)."""
        h = step[vertex_id]
        if h <= options.min_step * first_step[vertex_id]:
            return False
        low, high = state.slide_bounds(vertex_id)
        margin = 0.05 * (high - low)
        here = state.s[vertex_id]
        # A front on a straight block line is not carried off it.
        carried = [
            front for front in fronts.get(vertex_id, ())
            if front in free_set_all and front not in lines
        ]
        pinned_fronts = [front for front in fronts.get(vertex_id, ()) if front not in carried]
        moving = [vertex_id] + carried
        cost, worst = state.current_local_many(moving)
        best = None
        for sign in (-1.0, 1.0):
            s_new = here + sign * h
            if not (low + margin < s_new < high - margin):
                continue
            old = state.coords[vertex_id]
            tangent_old = state.chains[vertex_id].tangent(here)
            saved = state._move_slide(vertex_id, s_new)
            turn = state.chains[vertex_id].tangent(s_new) / tangent_old
            new = state.coords[vertex_id]
            ok = True
            for front in carried:
                target = new + turn * (state.coords[front] - old)
                target = _project(target, anchors(front), options.band_tolerance)
                if target is None:
                    ok = False
                    break
                saved += state._move(front, target)
            if ok:
                for front in pinned_fronts:
                    here_front = state.coords[front]
                    projected = _project(here_front, anchors(front), options.band_tolerance)
                    if projected is None or abs(projected - here_front) > 1.0e-12 * (1.0 + abs(here_front)):
                        ok = False
                        break
            if ok and not lengths_hold(moving):
                ok = False
            if ok and options.mode == "shape" and options.length_tolerance > 0.0:
                # No cells to protect yet, but a boundary edge slid to a
                # sliver hands sizing a forced-fine count: the two re-cut
                # edges keep their length within length_tolerance.
                for current in state.slide_edges[vertex_id][2:]:
                    ratio = abs(state.coords[current[0]] - state.coords[current[1]]) / chords[current]
                    if ratio > stretch or ratio * stretch < 1.0:
                        ok = False
            if ok:
                new_cost, new_worst, evals, interfaces = state.local_many(moving, INF if minmax else cost)
                if new_cost != INF:
                    if minmax:
                        better = new_worst < worst * (1.0 - 1.0e-9) or (
                            new_worst <= worst and new_cost < cost * (1.0 - 1.0e-9)
                        )
                        key = (new_worst, new_cost)
                    else:
                        better = new_cost < cost * (1.0 - 1.0e-9) and new_worst <= worst * (1.0 + 1.0e-12)
                        key = (new_cost, new_worst)
                    if better and (best is None or key < best[0]):
                        best = (key, s_new, evals, interfaces)
            state._restore(saved)
        if best is None:
            step[vertex_id] = h * 0.5
            return False
        old = state.coords[vertex_id]
        tangent_old = state.chains[vertex_id].tangent(here)
        state._move_slide(vertex_id, best[1])
        turn = state.chains[vertex_id].tangent(best[1]) / tangent_old
        new = state.coords[vertex_id]
        for front in carried:
            target = _project(new + turn * (state.coords[front] - old), anchors(front), options.band_tolerance)
            state._move(front, target)
        state.commit(best[2], best[3])
        return True

    def sweep(minmax: bool, vertices) -> int:
        count = 0
        for vertex_id in vertices:
            if vertex_id in chains:
                count += slide(vertex_id, minmax)
            else:
                count += search(vertex_id, minmax)
        return count

    everyone = list(free) + sliders
    sweeps = 0
    for sweeps in range(1, options.sweeps + 1):
        before = state.total_cost()
        moved = sweep(False, everyone)
        after = state.total_cost()
        if moved == 0 or before - after <= options.converged * before:
            break

    rounds = 0
    if options.minmax_rounds > 0 and everyone:
        free_set = set(everyone)
        for vertex_id in everyone:
            step[vertex_id] = max(step[vertex_id], 0.25 * first_step[vertex_id])
        for rounds in range(1, options.minmax_rounds + 1):
            worst_before = state.global_worst()
            # Only the blocks holding the worst face: moving vertices to lower
            # lesser maxima spends the average for nothing the maximum shows.
            ranked = [
                position for position in sorted(
                    range(len(state.blocks)), key=lambda position: -state.block_worst(position)
                )[: options.minmax_blocks]
                if state.block_worst(position) >= worst_before * (1.0 - 1.0e-9)
            ]
            candidates = []
            for position in ranked:
                for vertex_id in state.blocks[position].vertices:
                    if vertex_id in free_set and vertex_id not in candidates:
                        candidates.append(vertex_id)
                # A shared edge's faces also move with the neighbour's corners.
                for current in state.block_edges[position]:
                    for other in state.edge_blocks[current]:
                        for vertex_id in state.blocks[other].vertices:
                            if vertex_id in free_set and vertex_id not in candidates:
                                candidates.append(vertex_id)
            moved = sweep(True, candidates)
            if moved == 0:
                break
        # Phase 2 lowers maxima at some cost to the sum; a last phase-1 pass
        # recovers the sum without raising any block's worst face again.
        for vertex_id in everyone:
            step[vertex_id] = max(step[vertex_id], 0.25 * first_step[vertex_id])
        for _polish in range(options.sweeps):
            before = state.total_cost()
            moved = sweep(False, everyone)
            after = state.total_cost()
            if moved == 0 or before - after <= options.converged * before:
                break

    positions: dict[str, tuple[float, float]] = {}
    for vertex_id in free:
        point = state.coords[vertex_id]
        original = model.vertices[vertex_id]
        if point != complex(original.x, original.y):
            positions[vertex_id] = (point.real, point.imag)
    slides = {
        vertex_id: state.s[vertex_id] for vertex_id in sliders
        if state.s[vertex_id] != state.s0[vertex_id]
    }
    return positions, sweeps, rounds, slides


def _slide_chains(model, blocks, bbox, hold) -> dict:
    """The chain of every boundary vertex that may slide, in scope and not held."""
    from .slide import _exterior, chains, slidable

    exterior = _exterior(model)
    used = {vertex for block in model.blocks for vertex in block.vertices}
    scope = set(used)
    if blocks:
        known = {block.id: block for block in model.blocks}
        scope &= {v for identifier in blocks for v in known[identifier].vertices}
    if bbox is not None:
        xmin, ymin, xmax, ymax = (float(value) for value in bbox)
        scope &= {v for v in used if xmin <= model.vertices[v].x <= xmax and ymin <= model.vertices[v].y <= ymax}
    sliders = {
        v for v in sorted(scope)
        if v in exterior and v not in set(hold) and slidable(model, v, exterior) is None
    }
    return chains(model, sliders) if sliders else {}


def _slid(chains: dict, slides: dict, scale: float):
    """Positions and re-cut boundary edges for the slides scaled by ``scale``."""
    from .slide import recut_edges

    by_chain: dict[int, tuple] = {}
    for vertex_id, s in slides.items():
        chain = chains[vertex_id]
        s0 = chain.stations[chain.index[vertex_id]]
        by_chain.setdefault(id(chain), (chain, {}))[1][vertex_id] = s0 + scale * (s - s0)
    recut = {}
    points = {}
    for chain, targets in by_chain.values():
        edges = recut_edges(chain, targets)
        for current, geometry in edges.items():
            if current[0] in targets or current[1] in targets:
                recut[current] = geometry
        for vertex_id, s in targets.items():
            point = chain.point(s)
            points[vertex_id] = (point.real, point.imag)
    return recut, points


def _edge_ratios(
    working: MeshModel, lengths_before: dict[EdgeKey, float], moved: Iterable[str]
) -> tuple[float, str | None, float, str | None]:
    """The smallest and largest length over input length of the edges with a
    moved end, and those edges (``a-b``)."""
    moved = set(moved)
    low, low_edge, high, high_edge = 1.0, None, 1.0, None
    for current, length in lengths_before.items():
        if length <= 0.0 or not (current[0] in moved or current[1] in moved):
            continue
        ratio = working.edge_length(current) / length
        if ratio < low:
            low, low_edge = ratio, f"{current[0]}-{current[1]}"
        if ratio > high:
            high, high_edge = ratio, f"{current[0]}-{current[1]}"
    return low, low_edge, high, high_edge


def _pair_change(
    working: MeshModel, lengths_before: dict[EdgeKey, float],
    pairs: Iterable[tuple[EdgeKey, str, EdgeKey, EdgeKey]], moved: Iterable[str],
) -> tuple[float, str | None]:
    """The largest factor by which an interface pair with a moved end left
    its input length ratio, and that pair (``a-b/c-d``)."""
    moved = set(moved)
    worst, worst_pair = 1.0, None
    for _shared, _at, first, second in pairs:
        if not (moved & {*first, *second}):
            continue
        if lengths_before[first] <= 0.0 or lengths_before[second] <= 0.0:
            continue
        now = working.edge_length(first) / max(working.edge_length(second), 1.0e-300)
        change = now / (lengths_before[first] / lengths_before[second])
        change = max(change, 1.0 / change) if change > 0.0 else math.inf
        if change > worst:
            worst, worst_pair = change, f"{first[0]}-{first[1]}/{second[0]}-{second[1]}"
    return worst, worst_pair


def _spread_growth(
    working: MeshModel, lengths_before: dict[EdgeKey, float], components: Iterable[list[EdgeKey]],
) -> tuple[float, str | None]:
    """The largest factor by which a count component's length spread
    (longest over shortest member) grew over its input value, and that
    component's shortest member now (``a-b``)."""
    worst, worst_edge = 1.0, None
    for component in components:
        kept = [current for current in component if lengths_before.get(current, 0.0) > 0.0]
        if len(kept) < 2 or len(kept) < len(component):
            continue
        old = [lengths_before[current] for current in kept]
        new = [working.edge_length(current) for current in kept]
        if min(new) <= 0.0:
            return math.inf, None
        growth = (max(new) / min(new)) / (max(old) / min(old))
        if growth > worst:
            shortest = kept[new.index(min(new))]
            worst, worst_edge = growth, f"{shortest[0]}-{shortest[1]}"
    return worst, worst_edge


def _boundary_deviation(model: MeshModel, chains: dict, recut: dict) -> tuple[float, float]:
    """The largest distance of a re-cut edge from the input boundary, and
    the largest such distance over its chain's length."""
    from .slide import deviation

    absolute = relative = 0.0
    seen: set[int] = set()
    for chain in chains.values():
        if id(chain) in seen:
            continue
        seen.add(id(chain))
        edges = [current for current in recut if current[0] in chain.index and current[1] in chain.index]
        if not edges:
            continue
        value = deviation(model, chain, edges)
        absolute = max(absolute, value)
        relative = max(relative, value / max(chain.length, 1.0e-300))
    return absolute, relative


def smooth_model(
    model: MeshModel,
    *,
    blocks: Sequence[str] | None = None,
    bbox: Sequence[float] | None = None,
    hold: Sequence[str] = (),
    options: SmoothOptions | None = None,
) -> SmoothResult:
    """Smooth ``model`` in place when that is no worse; see the module docstring."""
    options = options or SmoothOptions()
    if options.samples < 1 or options.power <= 0.0 or options.band_tolerance < 0.0:
        raise TopologyError("smooth needs samples >= 1, power > 0 and band_tolerance >= 0")
    if options.mode not in ("real", "shape"):
        raise TopologyError(f"smooth mode must be real or shape, got {options.mode!r}")
    if options.shape_length_ratio < 0.0 or options.shape_length_ratio >= 1.0:
        raise TopologyError("smooth needs 0 <= shape_length_ratio < 1")
    if options.shape_pair_ratio != 0.0 and options.shape_pair_ratio <= 1.0:
        raise TopologyError("smooth needs shape_pair_ratio > 1 (or 0: unbounded)")
    if options.shape_spread_ratio != 0.0 and options.shape_spread_ratio <= 1.0:
        raise TopologyError("smooth needs shape_spread_ratio > 1 (or 0: unbounded)")
    model.validate()
    free = select_free(model, blocks=blocks, bbox=bbox, hold=hold)
    lines: dict[str, complex] = {}
    line_fixed = 0
    if options.keep_lines:
        on_lines = straight_lines(model, free)
        line_fixed = sum(1 for direction in on_lines.values() if direction is None)
        free = [vertex_id for vertex_id in free if on_lines.get(vertex_id, 0) is not None]
        lines = {vertex_id: direction for vertex_id, direction in on_lines.items() if direction is not None}
    spokes = band_spokes(model, free)
    shape = options.mode == "shape"
    chains = _slide_chains(model, blocks, bbox, hold) if options.slide else {}

    def judge(candidate: MeshModel) -> dict[str, Any]:
        if not shape:
            return _verdict(sample_model(candidate, options.verify_cells))
        verdict = _verdict(sample_model(candidate, uniform=options.shape_cells))
        # Untangled is judged finer than the shape grid, as the engine does.
        verdict["folded_cells"] = _verdict(
            sample_model(candidate, uniform=options.fold_cells)
        )["folded_cells"]
        return verdict

    before = judge(model)
    real_before = _verdict(sample_model(model, options.verify_cells)) if shape else None

    def result(applied, reason, after=None, positions=None, scale=0.0, sweeps=0, rounds=0,
               working=None):
        positions = positions or {}
        displacement = max(
            (math.hypot(x - model_before[vertex_id][0], y - model_before[vertex_id][1])
             for vertex_id, (x, y) in positions.items()),
            default=0.0,
        )
        band = 0.0
        edges_change = 0.0
        ratios = (1.0, None, 1.0, None)
        pair = (1.0, None)
        spread = (1.0, None)
        if working is not None:
            ratios = _edge_ratios(working, lengths_before, positions)
            pair = _pair_change(working, lengths_before, pairs, positions)
            spread = _spread_growth(working, lengths_before, components)
            for vertex_id, items in spokes.items():
                for spoke in items:
                    a = working.vertices[spoke.wall]
                    b = working.vertices[vertex_id]
                    band = max(band, abs(math.hypot(b.x - a.x, b.y - a.y) / spoke.length - 1.0))
            for current in lengths_before:
                if current[0] in positions or current[1] in positions:
                    edges_change = max(
                        edges_change,
                        abs(working.edge_length(current) / lengths_before[current] - 1.0),
                    )
        return SmoothResult(
            applied, reason, before, after or before, len(free), len(positions),
            displacement, sum(len(items) for items in spokes.values()), band,
            edges_change, scale, sweeps, rounds, tuple(hold), positions,
            options.mode, real_before, real_after if applied else real_before,
            len(chains), slid_count if applied else 0, max_slide if applied else 0.0,
            boundary[0] if applied else 0.0, boundary[1] if applied else 0.0,
            *ratios, len(lines), line_fixed, *pair, *spread,
        )

    real_after: dict[str, Any] | None = None
    slid_count = 0
    max_slide = 0.0
    boundary = (0.0, 0.0)

    model_before = {vertex_id: (vertex.x, vertex.y) for vertex_id, vertex in model.vertices.items()}
    lengths_before = {current: model.edge_length(current) for current in model.edges()}
    pairs = model.interface_pairs()
    if shape:
        from .size import count_components  # size imports this module

        components = count_components(model)
    else:
        components = []
    if not free and not chains:
        return result(False, "nothing to gain: no free vertex (every vertex in scope is on a boundary, "
                      "held" + (f" or on two straight block lines ({line_fixed})" if line_fixed else "") + ")")
    if before["folded_cells"]:
        where = (
            f"on the count-free block map ({options.fold_cells} even fractions a side)" if shape
            else f"at the stored counts and grading ({options.verify_cells} cells a side at most)"
        )
        fold_sample = (
            sample_model(model, uniform=options.fold_cells) if shape
            else sample_model(model, options.verify_cells)
        )
        return result(
            False,
            f"not smoothed: {before['folded_cells']} sampled cell(s) already fold {where}, in "
            f"{folded_blocks(fold_sample)}; untangle those blocks first (repair_block, "
            "split_edge or new vertex positions)"
            + ("; on the count-free map a block folds when its own sides cross, whatever its "
               "counts - the shape step can be left out" if shape else ""),
        )
    positions, sweeps, rounds, slides = _optimise(model, free, spokes, options, chains, lines)
    if not positions and not slides:
        return result(
            False,
            f"nothing to gain: no move of the {len(free)} free vertex(es) lowers the sampled "
            "face non-orthogonality",
            sweeps=sweeps, rounds=rounds,
        )
    stale_refusals: list[tuple[float, str]] = []
    deviation_refusals: list[float] = []
    for scale in (1.0, *FALLBACK_SCALES):
        scaled = {
            vertex_id: (
                model_before[vertex_id][0] + scale * (x - model_before[vertex_id][0]),
                model_before[vertex_id][1] + scale * (y - model_before[vertex_id][1]),
            )
            for vertex_id, (x, y) in positions.items()
        }
        recut, slid_at = _slid(chains, slides, scale)
        for vertex_id, point in slid_at.items():
            scaled[vertex_id] = point
        working = from_data(to_data(model))
        try:
            working.move_vertices(scaled, carry_curves=True, edge_geometry=recut)
        except (TopologyError, ValueError):
            continue
        ratios = _edge_ratios(working, lengths_before, scaled)
        if shape and ratios[0] < options.shape_length_ratio * (1.0 - 1.0e-6):
            continue  # a scaled path can pass closer than either end
        if shape and options.shape_pair_ratio > 0.0 and _pair_change(
            working, lengths_before, pairs, scaled
        )[0] > options.shape_pair_ratio * (1.0 + 1.0e-6):
            continue
        if shape and options.shape_spread_ratio > 0.0 and _spread_growth(
            working, lengths_before, components
        )[0] > options.shape_spread_ratio * (1.0 + 1.0e-6):
            continue
        boundary = _boundary_deviation(working, chains, recut)
        if boundary[1] > SLIDE_TOLERANCE:
            deviation_refusals.append(scale)
            continue
        slid_count = len(slid_at)
        max_slide = max((abs(slides[v] - chains[v].stations[chains[v].index[v]]) * scale for v in slid_at),
                        default=0.0)
        after = judge(working)
        if not _no_worse(after, before):
            continue
        if shape:
            real_sample = sample_model(working, options.verify_cells)
            real_after = _verdict(real_sample)
            if real_after["folded_cells"] and not real_before["folded_cells"] \
                    and not options.allow_stale_fold:
                stale_refusals.append((scale, folded_blocks(real_sample)))
                continue
        gained = (
            after["max_non_orthogonality"] < before["max_non_orthogonality"] - 1.0e-6
            or after["severe_faces"] < before["severe_faces"]
            or after["average_non_orthogonality"] < before["average_non_orthogonality"] - 1.0e-6
        )
        if not gained:
            break
        model.move_vertices(scaled, carry_curves=True, edge_geometry=recut)
        reason = (
            f"smoothed {len(scaled) - len(slid_at)} of {len(free)} free vertices"
            + (f", slid {len(slid_at)} of {len(chains)} boundary vertices along their boundary "
               f"(re-cut edges within {boundary[0]:.3g} of the input boundary)" if chains else "")
            + (f" at {scale:g} of the optimised displacement" if scale < 1.0 else "")
            + f": max non-orthogonality {before['max_non_orthogonality']:.2f} -> "
            f"{after['max_non_orthogonality']:.2f} degrees, average "
            f"{before['average_non_orthogonality']:.2f} -> {after['average_non_orthogonality']:.2f}, "
            f"faces above {SEVERE_NON_ORTHOGONALITY:g} degrees {before['severe_faces']} -> "
            f"{after['severe_faces']}"
        )
        if shape:
            reason = (
                "shape " + reason + " on the count-free block map; the stored counts and "
                "grading now give max non-orthogonality "
                f"{real_before['max_non_orthogonality']:.2f} -> {real_after['max_non_orthogonality']:.2f}, "
                f"{real_before['severe_faces']} -> {real_after['severe_faces']} faces above "
                f"{SEVERE_NON_ORTHOGONALITY:g} degrees, {real_after['folded_cells']} folded cells: they "
                "were chosen for the old shape - run size next"
            )
        reason += (
            f"; edge lengths {ratios[0]:.3g} ({ratios[1]}) to {ratios[2]:.3g} ({ratios[3]}) "
            "of their input"
            + (f" (floor {options.shape_length_ratio:g})" if shape and options.shape_length_ratio > 0.0 else "")
        )
        if shape:
            change, which = _pair_change(working, lengths_before, pairs, scaled)
            growth, shortest = _spread_growth(working, lengths_before, components)
            reason += (
                f"; interface pair length ratios within {change:.3g} of their input"
                + (f" ({which})" if which else "")
                + (f" (limit {options.shape_pair_ratio:g})" if options.shape_pair_ratio > 0.0 else "")
                + f"; count component spreads grown at most {growth:.3g} times"
                + (f" (shortest member {shortest})" if shortest else "")
                + (f" (limit {options.shape_spread_ratio:g})" if options.shape_spread_ratio > 0.0 else "")
            )
        if options.keep_lines:
            reason += (
                f"; {len(lines)} vertices on a straight block line moved only along it, "
                f"{line_fixed} on two stayed"
            )
        return result(True, reason, after, scaled, scale, sweeps, rounds, working)
    if deviation_refusals:
        return result(
            False,
            f"not applied: a re-cut boundary edge would leave the input boundary by more than "
            f"{SLIDE_TOLERANCE:g} of its chain's length",
            sweeps=sweeps, rounds=rounds,
        )
    if stale_refusals:
        return result(
            False,
            "not applied: the smoothed shape would fold real cells under the stored counts "
            f"and grading (at {stale_refusals[0][0]:g} of the optimised displacement, in "
            f"{stale_refusals[0][1]}); pass "
            "allow_stale_fold=true when size follows",
            sweeps=sweeps, rounds=rounds,
        )
    return result(
        False,
        "nothing to gain: the optimised positions did not pass the full sampled check "
        "(untangled, no more severe faces, no higher maximum) at any scale",
        sweeps=sweeps, rounds=rounds,
    )
