"""Local repair of one block by the moves an agent would try by hand.

``repair_block`` measures a block's corner angles, tries a deterministic set
of local moves - moving one of its interior vertices (its curved edges
carried along), combining it with a neighbour across an internal edge,
splitting one of its edges, regrading one of its edges to the series of the
opposite edge - each on a copy of the model, keeps the one that most
improves the block and its neighbours, and applies it. The judge is the
corner-angle screen of :mod:`blockdrawer.quality`, measured from the first
mesh cell so curved edges and grading count, plus the block interiors
sampled at the real cell counts: a move must raise the worst cell by half a
degree, or keep it and cut the number of severely sheared cells by a tenth
- the measure a grading move acts on, since a wedge's apex angle is what it
is however the cells are distributed. ``checkMesh`` remains the authority
on the result.

Vertices on a boundary edge never move: a wall vertex must stay on the
geometry and a far-field vertex on the domain. Everything is UI-free and
deterministic, and nothing is written when no move improves the block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .preview import _block_mesh_point, _directed_edge_samples, _sample_indices
from .quality import QualityThresholds, block_quality
from .session import from_data, to_data

MOVES = ("move", "combine", "split", "grade")
# The interior of every judged block is sampled at its real cell counts, up
# to this many a side, with blockMesh's transfinite interpolation (the
# preview's), so a candidate that folds or shears a block between its
# corners is seen: the corner screen alone passed a merge across a curved
# cove edge that checkMesh read as 180 degrees of non-orthogonality, and a
# sampling of eight cells a side passed a vertex move that folded one real
# cell of a 54-cell edge beside a sliver corner.
INTERIOR_CELLS = 64
# The neighbourhood a repair is judged on: every block with a vertex inside
# the target's neighbourhood box, padded by this fraction - recomputed on
# each candidate, since a split or a merge changes which blocks exist.
NEIGHBOURHOOD_PADDING = 0.2
# A move is applied only when it raises the worst cell by at least this
# many degrees; a chain of hundredth-degree gains is a structural defect
# asking for a different topology, and the agent should hear that.
MIN_GAIN = 0.5
# A sampled cell is severe when a corner is within this many degrees of 0 or
# 180: for a sheared cell that is checkMesh's 70 degrees of non-orthogonality.
# A move that does not raise the worst cell may still be applied when it
# removes at least this fraction of the severe cells - crossed gradings on
# the two spokes of a seam wedge put five thousand such cells into the wedge
# on 30P30N while its 12-degree apex stayed what it was.
SEVERE_ANGLE = 20.0
SEVERE_GAIN = 0.1
# A neighbourhood with no severe cell whose worst cell is this far from
# degenerate (every angle between 45 and 135 degrees) has nothing for a
# local move to repair; the result says so instead of blaming the topology.
SOUND_ANGLE = 45.0
# Displacements tried for a movable vertex, as fractions of its shortest
# incident edge, along eight compass directions and the corner's bisector.
MOVE_STEPS = (0.1, 0.2, 0.35)
SPLIT_FRACTIONS = (0.3, 0.5, 0.7)


@dataclass(frozen=True)
class RepairScore:
    """How far the neighbourhood's worst cell is from degenerate.

    ``worst`` is the smallest of every corner's distance from 0 and from 180
    degrees - a 2-degree corner and the 178-degree corner opposite it in a
    sliver score the same - over the blocks' corners (``min_angle``,
    ``max_angle``, from the first mesh cell) and over every cell of their
    sampled interiors (``interior``); a folded interior cell is ``-inf``.
    """

    min_angle: float
    max_angle: float
    interior: float
    severe: int = 0

    @property
    def worst(self) -> float:
        return min(self.min_angle, 180.0 - self.max_angle, self.interior)

    def improves(self, before: "RepairScore") -> bool:
        """Half a degree on the worst cell, or a tenth fewer severe cells at no cost to it."""
        if self.worst >= before.worst + MIN_GAIN:
            return True
        return (
            self.worst >= before.worst - 1.0e-9
            and self.severe <= before.severe - max(1, math.ceil(SEVERE_GAIN * before.severe))
        )

    def key(self) -> tuple[float, int, float, float]:
        return (math.floor(self.worst / MIN_GAIN), -self.severe, self.worst, self.min_angle)


@dataclass(frozen=True)
class RepairResult:
    block_id: str
    operation: str | None
    arguments: dict[str, Any]
    before: RepairScore
    after: RepairScore
    considered: int
    neighbourhood: tuple[str, ...]
    reason: str = ""
    rejected: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> bool:
        return self.operation is not None


def _neighbourhood_box(model: MeshModel, block_id: str) -> tuple[float, float, float, float]:
    """The box round the target block and every block sharing a vertex with it."""
    target = next((block for block in model.blocks if block.id == block_id), None)
    if target is None:
        raise TopologyError(f"Unknown block {block_id!r}")
    corners = set(target.vertices)
    vertex_ids: set[str] = set()
    for block in model.blocks:
        if corners & set(block.vertices):
            vertex_ids.update(block.vertices)
    xs = [model.vertices[identifier].x for identifier in vertex_ids]
    ys = [model.vertices[identifier].y for identifier in vertex_ids]
    pad_x = NEIGHBOURHOOD_PADDING * max(max(xs) - min(xs), 1e-12)
    pad_y = NEIGHBOURHOOD_PADDING * max(max(ys) - min(ys), 1e-12)
    return (min(xs) - pad_x, min(ys) - pad_y, max(xs) + pad_x, max(ys) + pad_y)


def _blocks_in(model: MeshModel, box: tuple[float, float, float, float]) -> tuple[str, ...]:
    xmin, ymin, xmax, ymax = box
    found = []
    for block in model.blocks:
        for identifier in block.vertices:
            vertex = model.vertices[identifier]
            if xmin <= vertex.x <= xmax and ymin <= vertex.y <= ymax:
                found.append(block.id)
                break
    return tuple(found)


def _block_grid(model: MeshModel, block) -> list[list[tuple[float, float]]]:
    """The block's mesh nodes at its real counts, coarsened only above ``INTERIOR_CELLS`` a side."""
    directed = [block.directed_edge(index) for index in range(4)]
    edges = [edge_key(*item) for item in directed]
    x_cells = model.edge_cells[edges[0]]
    y_cells = model.edge_cells[edges[1]]
    x_indices = _sample_indices(x_cells, max(1, math.ceil(x_cells / INTERIOR_CELLS)))
    y_indices = _sample_indices(y_cells, max(1, math.ceil(y_cells / INTERIOR_CELLS)))
    bottom = _directed_edge_samples(model, directed[0], x_indices)
    right = _directed_edge_samples(model, directed[1], y_indices)
    top = _directed_edge_samples(model, (directed[2][1], directed[2][0]), x_indices)
    left = _directed_edge_samples(model, (directed[3][1], directed[3][0]), y_indices)
    corners = tuple((model.vertices[identifier].x, model.vertices[identifier].y) for identifier in block.vertices)
    return [
        [_block_mesh_point(bottom[a], right[b], top[a], left[b], corners) for a in range(len(x_indices))]
        for b in range(len(y_indices))
    ]


def _grid_worst(grid: list[list[tuple[float, float]]]) -> tuple[float, int]:
    """The worst corner, as distance from 0 or 180 degrees, over the grid's cells, and the severe count.

    The worst is ``-inf`` when a cell folds.
    """
    worst = 180.0
    severe = 0
    for row, upper in zip(grid, grid[1:]):
        for a in range(len(row) - 1):
            quad = (row[a], row[a + 1], upper[a + 1], upper[a])
            area = 0.0
            for (x0, y0), (x1, y1) in zip(quad, quad[1:] + quad[:1]):
                area += x0 * y1 - x1 * y0
            if area <= 0.0:
                return -math.inf, severe
            cell_worst = 180.0
            for index in range(4):
                here = quad[index]
                before = quad[index - 1]
                after = quad[(index + 1) % 4]
                ax, ay = before[0] - here[0], before[1] - here[1]
                bx, by = after[0] - here[0], after[1] - here[1]
                norms = math.hypot(ax, ay) * math.hypot(bx, by)
                if norms <= 0.0:
                    return -math.inf, severe
                cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / norms))
                angle = math.degrees(math.acos(cosine))
                cell_worst = min(cell_worst, angle, 180.0 - angle)
            worst = min(worst, cell_worst)
            if cell_worst < SEVERE_ANGLE:
                severe += 1
    return worst, severe


def _score(model: MeshModel, box: tuple[float, float, float, float], limits: QualityThresholds) -> RepairScore:
    angles_min: list[float] = []
    angles_max: list[float] = []
    interior = 180.0
    severe = 0
    blocks = {block.id: block for block in model.blocks}
    for identifier in _blocks_in(model, box):
        quality = block_quality(model, identifier, limits)
        angles_min.append(quality.min_angle)
        angles_max.append(quality.max_angle)
        block_worst, block_severe = _grid_worst(_block_grid(model, blocks[identifier]))
        interior = min(interior, block_worst)
        severe += block_severe
    if not angles_min:
        return RepairScore(0.0, 180.0, -math.inf)
    return RepairScore(min(angles_min), max(angles_max), interior, severe)


def _copy(model: MeshModel) -> MeshModel:
    return from_data(to_data(model))


def _movable(model: MeshModel, vertex_id: str) -> bool:
    """A vertex every incident edge of which is internal and unassigned."""
    for block in model.blocks:
        if vertex_id not in block.vertices:
            continue
        for index in range(4):
            first, second = block.directed_edge(index)
            if vertex_id not in (first, second):
                continue
            current = edge_key(first, second)
            if model.is_boundary_edge(current) or current in model.edge_boundaries:
                return False
    return True


def _directions(model: MeshModel, block_id: str, vertex_id: str) -> list[tuple[float, float]]:
    """Eight compass directions plus the block corner's interior bisector."""
    directions = [
        (math.cos(k * math.pi / 4.0), math.sin(k * math.pi / 4.0)) for k in range(8)
    ]
    block = next(item for item in model.blocks if item.id == block_id)
    index = block.vertices.index(vertex_id)
    here = model.vertices[vertex_id]
    following = model.vertices[block.vertices[(index + 1) % 4]]
    previous = model.vertices[block.vertices[(index + 3) % 4]]
    legs = []
    for other in (following, previous):
        dx, dy = other.x - here.x, other.y - here.y
        norm = math.hypot(dx, dy)
        if norm > 0.0:
            legs.append((dx / norm, dy / norm))
    if len(legs) == 2:
        bx, by = legs[0][0] + legs[1][0], legs[0][1] + legs[1][1]
        norm = math.hypot(bx, by)
        if norm > 1e-12:
            directions.append((bx / norm, by / norm))
            directions.append((-bx / norm, -by / norm))
    return directions


def _shortest_incident(model: MeshModel, vertex_id: str) -> float:
    lengths: list[float] = []
    for current in model.edge_occurrences():
        if vertex_id in current:
            lengths.append(model.edge_length(current))
    return min(lengths) if lengths else 0.0


def repair_block(
    model: MeshModel,
    block_id: str,
    *,
    moves: tuple[str, ...] = MOVES,
    thresholds: QualityThresholds | None = None,
) -> RepairResult:
    """Apply the local move that most improves the cells around ``block_id``.

    Candidates are tried on copies; the chosen one is replayed on ``model``
    and validated. With no improving candidate the model is untouched and
    the result says so.
    """
    limits = thresholds or QualityThresholds()
    unknown = sorted(set(moves) - set(MOVES))
    if unknown:
        raise TopologyError(f"Unknown repair move(s) {unknown}; choose from {list(MOVES)}")
    box = _neighbourhood_box(model, block_id)
    neighbourhood = _blocks_in(model, box)
    before = _score(model, box, limits)
    target = next(block for block in model.blocks if block.id == block_id)

    best: tuple[tuple, str, dict[str, Any], RepairScore] | None = None
    considered = 0
    rejected: list[str] = []

    def consider(operation: str, arguments: dict[str, Any], run) -> None:
        nonlocal best, considered
        considered += 1
        working = _copy(model)
        try:
            run(working)
            working.validate()
        except (TopologyError, ValueError) as error:
            rejected.append(f"{operation} {arguments}: {str(error)[:80]}")
            return
        score = _score(working, box, limits)
        if score.interior == -math.inf:
            rejected.append(f"{operation} {arguments}: folds a sampled cell")
            return
        if not score.improves(before):
            return
        if best is None or score.key() > best[0]:
            best = (score.key(), operation, arguments, score)

    if "move" in moves:
        for vertex_id in target.vertices:
            if not _movable(model, vertex_id):
                continue
            here = model.vertices[vertex_id]
            reach = _shortest_incident(model, vertex_id)
            if reach <= 0.0:
                continue
            for direction in _directions(model, block_id, vertex_id):
                for step in MOVE_STEPS:
                    x = here.x + step * reach * direction[0]
                    y = here.y + step * reach * direction[1]
                    consider(
                        "move_vertex",
                        {"vertex": vertex_id, "x": x, "y": y, "carry_curves": True},
                        lambda working, v=vertex_id, x=x, y=y: working.move_vertex(
                            v, x, y, carry_curves=True
                        ),
                    )
    if "combine" in moves:
        occurrences = model.edge_occurrences()
        for index in range(4):
            current = edge_key(*target.directed_edge(index))
            if len(occurrences.get(current, ())) != 2:
                continue
            consider(
                "combine_blocks",
                {"edge": list(current)},
                lambda working, e=current: working.combine_blocks(e),
            )
    if "split" in moves:
        for index in range(4):
            current = edge_key(*target.directed_edge(index))
            for fraction in SPLIT_FRACTIONS:
                consider(
                    "split_edge",
                    {"edge": list(current), "fraction": fraction},
                    lambda working, e=current, f=fraction: working.split_edge(e, f),
                )
    if "grade" in moves:
        # Each edge may take the series of its opposite edge in the block's
        # physical direction - the two run opposite ways round the block, so
        # one is read reversed.  Crossed opposite gradings shear every cell
        # between them whatever the corners say.
        for first_index, second_index in ((0, 2), (1, 3)):
            first = target.directed_edge(first_index)
            second = target.directed_edge(second_index)
            along = {
                first: model.edge_expansion_in_direction(*first),
                second: model.edge_expansion_in_direction(second[1], second[0]),
            }
            for taker, giver in ((first, second), (second, first)):
                key = edge_key(*taker)
                if model.edge_cells.get(key, 1) < 2:
                    continue
                wanted = along[giver]
                if math.isclose(wanted, along[taker], rel_tol=1.0e-9):
                    continue
                # ``along`` is read in ``first``'s traversal direction; the
                # taker's canonical value is that, or its reciprocal when the
                # canonical order runs the other way.
                directed = taker if taker is first else (taker[1], taker[0])
                canonical = wanted if directed == key else 1.0 / wanted
                consider(
                    "set_edge_grading",
                    {"edge": list(key), "parameter": "total_ratio", "value": canonical},
                    lambda working, k=key, v=canonical: working.set_edge_grading(k, "total_ratio", v),
                )

    if best is None:
        if before.severe == 0 and before.worst >= SOUND_ANGLE:
            reason = (
                f"nothing to repair around {block_id}: no severe cells and every "
                f"corner and sampled cell between {before.min_angle:.1f} and "
                f"{before.max_angle:.1f} degrees ({considered} candidates tried); "
                "repair_block judges angles only - a cell-size jump or an aspect "
                "ratio is the quality report's, fixed with split_edge and "
                "set_edge_grading"
            )
        else:
            reason = (
                f"no local move raises the worst cell around {block_id} by {MIN_GAIN:g} degrees "
                f"or removes a tenth of its {before.severe} severe cells "
                f"(corners {before.min_angle:.1f} to {before.max_angle:.1f} degrees, interior "
                f"{before.interior:.1f}; {considered} candidates): the defect is in the "
                "topology round it, not in a vertex or a grading"
            )
        return RepairResult(
            block_id, None, {}, before, before, considered, neighbourhood,
            reason=reason,
            rejected=tuple(rejected[:8]),
        )
    _key, operation, arguments, after = best
    if operation == "move_vertex":
        model.move_vertex(arguments["vertex"], arguments["x"], arguments["y"], carry_curves=True)
    elif operation == "combine_blocks":
        model.combine_blocks(tuple(arguments["edge"]))
    elif operation == "set_edge_grading":
        model.set_edge_grading(tuple(arguments["edge"]), arguments["parameter"], arguments["value"])
    else:
        model.split_edge(tuple(arguments["edge"]), arguments["fraction"])
    model.validate()
    return RepairResult(
        block_id, operation, arguments, before, after, considered, neighbourhood,
        reason=(
            f"{operation} took the corners around {block_id} from "
            f"{before.min_angle:.1f}..{before.max_angle:.1f} (interior {before.interior:.1f}, "
            f"{before.severe} severe cells) to "
            f"{after.min_angle:.1f}..{after.max_angle:.1f} (interior {after.interior:.1f}, "
            f"{after.severe} severe cells) degrees"
        ),
    )
