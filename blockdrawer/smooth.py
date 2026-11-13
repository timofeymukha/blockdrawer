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
from .sampling import SEVERE_NON_ORTHOGONALITY, ModelSample, sample_model
from .session import from_data, to_data


INF = math.inf
# Resolution of the full verification, as the quality report's default.
VERIFY_CELLS = 256
# A trial block corner may not come closer to 0 or 180 degrees than 20
# (this sine) unless the original corner already was: a new 178-degree block
# corner is a first cell the corner screen rightly flags.
MIN_CORNER_SINE = math.sin(math.radians(20.0))
# Displacement scales tried when the full verification refuses the result.
FALLBACK_SCALES = (0.5, 0.25, 0.125)


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
    aspect_cap: float = 100.0  # the same for a corner cell's aspect ratio (0: unconstrained)
    # Every edge keeps its length within this fraction (0: unbounded), so a
    # thin strip such as a wake band is not widened tenfold.
    length_tolerance: float = 0.5
    verify_cells: int = VERIFY_CELLS
    # ``real`` judges the mesh the stored counts and grading define;
    # ``shape`` judges the count-free block map - every block at
    # ``shape_cells`` even parameter fractions a side, untangled at
    # ``fold_cells`` - with no length or size limits (there are no cells to
    # keep), the wall-band rule kept. Its result leaves the counts and
    # grading stale: sizing has to follow.
    mode: str = "real"
    shape_cells: int = 8
    fold_cells: int = 32
    # A shape result whose stored counts and grading would fold a real cell
    # is refused unless this is set (because ``size`` will follow).
    allow_stale_fold: bool = False


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

    def to_data(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self.mode != "real":
            extra = {"real_before": self.real_before, "real_after": self.real_after}
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
        for vertex_id, old, edges in saved:
            self.coords[vertex_id] = old
            for current, samples in edges:
                self.edge_samples[current] = samples

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
    options: SmoothOptions,
) -> tuple[dict[str, tuple[float, float]], int, int]:
    state = _State(model, options)
    if any(evaluation is None for evaluation in state.evals):
        # A block already folds or carries a flat corner: the barrier cannot
        # be crossed from inside, so the optimiser does not start.
        return {}, 0, 0
    allowance = math.radians(options.wall_angle)
    anchors = {
        vertex_id: [
            (state.coords[spoke.wall], replace(spoke, misalignment=max(spoke.misalignment, allowance)))
            for spoke in items
        ]
        for vertex_id, items in spokes.items()
    }
    first_step: dict[str, float] = {}
    for vertex_id in free:
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

    def add_pair(at: str, first: EdgeKey, second: EdgeKey, cap: float) -> None:
        ratio = _width(state, width_per_chord, first, at) / _width(
            state, width_per_chord, second, at
        )
        ratio = max(ratio, 1.0 / ratio)
        # Below the cap a ratio may grow to it, not past it; above it, by
        # the slack at most.
        limit = cap if ratio <= cap else ratio * (1.0 + options.size_slack)
        size_pairs.setdefault(at, []).append((first, second, limit))

    # The pairs the quality screen compares: across every internal edge the
    # transverse edges of its two blocks at each end (the size jump), and
    # the two edges of each block corner (the corner cell's aspect ratio).
    for current, owners in state.edge_blocks.items():
        if len(owners) != 2:
            continue
        for at in current:
            transverse = []
            for position in owners:
                for other in state.block_edges[position]:
                    if other != current and at in other:
                        transverse.append(other)
                        break
            if len(transverse) == 2 and transverse[0] != transverse[1]:
                add_pair(at, transverse[0], transverse[1], options.size_cap)
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

    def keeps_lengths(vertex_id: str, target: complex) -> bool:
        if options.mode == "shape":
            return True  # no cells yet: sizing follows the shape
        incident = state.vertex_edges.get(vertex_id, ())
        if options.length_tolerance > 0.0:
            for current in incident:
                other = state.coords[current[1] if current[0] == vertex_id else current[0]]
                ratio = abs(target - other) / chords[current]
                if ratio > stretch or ratio * stretch < 1.0:
                    return False
        moved = set(incident)
        old = state.coords[vertex_id]
        state.coords[vertex_id] = target
        try:
            for current in incident:
                for at in current:
                    for first, second, limit in size_pairs.get(at, ()):
                        if first not in moved and second not in moved:
                            continue
                        ratio = _width(state, width_per_chord, first, at) / _width(
                            state, width_per_chord, second, at
                        )
                        if ratio > limit or ratio * limit < 1.0:
                            return False
        finally:
            state.coords[vertex_id] = old
        return True

    def search(vertex_id: str, minmax: bool) -> bool:
        h = step[vertex_id]
        if h <= options.min_step * first_step[vertex_id]:
            return False
        cost, worst = state.current_local(vertex_id)
        base = state.coords[vertex_id]
        best = None
        for direction in _DIRECTIONS:
            target = _project(base + h * direction, anchors.get(vertex_id, []), options.band_tolerance)
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

    sweeps = 0
    for sweeps in range(1, options.sweeps + 1):
        before = state.total_cost()
        moved = sum(1 for vertex_id in free if search(vertex_id, False))
        after = state.total_cost()
        if moved == 0 or before - after <= options.converged * before:
            break

    rounds = 0
    if options.minmax_rounds > 0 and free:
        free_set = set(free)
        for vertex_id in free:
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
            moved = sum(1 for vertex_id in candidates if search(vertex_id, True))
            if moved == 0:
                break
        # Phase 2 lowers maxima at some cost to the sum; a last phase-1 pass
        # recovers the sum without raising any block's worst face again.
        for vertex_id in free:
            step[vertex_id] = max(step[vertex_id], 0.25 * first_step[vertex_id])
        for _polish in range(options.sweeps):
            before = state.total_cost()
            moved = sum(1 for vertex_id in free if search(vertex_id, False))
            after = state.total_cost()
            if moved == 0 or before - after <= options.converged * before:
                break

    positions: dict[str, tuple[float, float]] = {}
    for vertex_id in free:
        point = state.coords[vertex_id]
        original = model.vertices[vertex_id]
        if point != complex(original.x, original.y):
            positions[vertex_id] = (point.real, point.imag)
    return positions, sweeps, rounds


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
    model.validate()
    free = select_free(model, blocks=blocks, bbox=bbox, hold=hold)
    spokes = band_spokes(model, free)
    shape = options.mode == "shape"

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
        if working is not None:
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
        )

    real_after: dict[str, Any] | None = None

    model_before = {vertex_id: (vertex.x, vertex.y) for vertex_id, vertex in model.vertices.items()}
    lengths_before = {current: model.edge_length(current) for current in model.edges()}
    if not free:
        return result(False, "nothing to gain: no free vertex (every vertex in scope is on a boundary or held)")
    if before["folded_cells"]:
        return result(
            False,
            f"not smoothed: {before['folded_cells']} sampled cell(s) already fold; untangle "
            "the topology first (repair_block, split_edge or new vertex positions)",
        )
    positions, sweeps, rounds = _optimise(model, free, spokes, options)
    if not positions:
        return result(
            False,
            f"nothing to gain: no move of the {len(free)} free vertex(es) lowers the sampled "
            "face non-orthogonality",
            sweeps=sweeps, rounds=rounds,
        )
    stale_refusals: list[float] = []
    for scale in (1.0, *FALLBACK_SCALES):
        scaled = {
            vertex_id: (
                model_before[vertex_id][0] + scale * (x - model_before[vertex_id][0]),
                model_before[vertex_id][1] + scale * (y - model_before[vertex_id][1]),
            )
            for vertex_id, (x, y) in positions.items()
        }
        working = from_data(to_data(model))
        try:
            working.move_vertices(scaled, carry_curves=True)
        except (TopologyError, ValueError):
            continue
        after = judge(working)
        if not _no_worse(after, before):
            continue
        if shape:
            real_after = _verdict(sample_model(working, options.verify_cells))
            if real_after["folded_cells"] and not real_before["folded_cells"] \
                    and not options.allow_stale_fold:
                stale_refusals.append(scale)
                continue
        gained = (
            after["max_non_orthogonality"] < before["max_non_orthogonality"] - 1.0e-6
            or after["severe_faces"] < before["severe_faces"]
            or after["average_non_orthogonality"] < before["average_non_orthogonality"] - 1.0e-6
        )
        if not gained:
            break
        model.move_vertices(scaled, carry_curves=True)
        reason = (
            f"smoothed {len(scaled)} of {len(free)} free vertices"
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
        return result(True, reason, after, scaled, scale, sweeps, rounds, working)
    if stale_refusals:
        return result(
            False,
            "not applied: the smoothed shape would fold real cells under the stored counts "
            "and grading; pass allow_stale_fold=true when size follows",
            sweeps=sweeps, rounds=rounds,
        )
    return result(
        False,
        "nothing to gain: the optimised positions did not pass the full sampled check "
        "(untangled, no more severe faces, no higher maximum) at any scale",
        sweeps=sweeps, rounds=rounds,
    )
