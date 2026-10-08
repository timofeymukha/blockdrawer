"""Local repair of one block by the moves an agent would try by hand.

``repair_block`` measures a block's corner angles, tries a deterministic set
of local moves - moving one of its interior vertices, combining it with a
neighbour across an internal edge, splitting one of its edges - each on a
copy of the model, keeps the one that raises the worst corner angle of the
block and its neighbours the most, and applies it. The corner-angle screen
of :mod:`blockdrawer.quality` is the judge, measured from the first mesh
cell so curved edges and grading count; ``checkMesh`` remains the authority
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

MOVES = ("move", "combine", "split")
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

    @property
    def worst(self) -> float:
        return min(self.min_angle, 180.0 - self.max_angle, self.interior)

    def key(self) -> tuple[float, float]:
        return (self.worst, self.min_angle)


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


def _grid_worst(grid: list[list[tuple[float, float]]]) -> float:
    """The worst corner, as distance from 0 or 180 degrees, over the grid's cells; -inf when a cell folds."""
    worst = 180.0
    for row, upper in zip(grid, grid[1:]):
        for a in range(len(row) - 1):
            quad = (row[a], row[a + 1], upper[a + 1], upper[a])
            area = 0.0
            for (x0, y0), (x1, y1) in zip(quad, quad[1:] + quad[:1]):
                area += x0 * y1 - x1 * y0
            if area <= 0.0:
                return -math.inf
            for index in range(4):
                here = quad[index]
                before = quad[index - 1]
                after = quad[(index + 1) % 4]
                ax, ay = before[0] - here[0], before[1] - here[1]
                bx, by = after[0] - here[0], after[1] - here[1]
                norms = math.hypot(ax, ay) * math.hypot(bx, by)
                if norms <= 0.0:
                    return -math.inf
                cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / norms))
                angle = math.degrees(math.acos(cosine))
                worst = min(worst, angle, 180.0 - angle)
    return worst


def _score(model: MeshModel, box: tuple[float, float, float, float], limits: QualityThresholds) -> RepairScore:
    angles_min: list[float] = []
    angles_max: list[float] = []
    interior = 180.0
    blocks = {block.id: block for block in model.blocks}
    for identifier in _blocks_in(model, box):
        quality = block_quality(model, identifier, limits)
        angles_min.append(quality.min_angle)
        angles_max.append(quality.max_angle)
        interior = min(interior, _grid_worst(_block_grid(model, blocks[identifier])))
    if not angles_min:
        return RepairScore(0.0, 180.0, -math.inf)
    return RepairScore(min(angles_min), max(angles_max), interior)


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
    """Apply the local move that most raises the worst corner angle around ``block_id``.

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

    best: tuple[tuple[float, float], str, dict[str, Any], RepairScore] | None = None
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
        if score.worst < before.worst + MIN_GAIN:
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
                        {"vertex": vertex_id, "x": x, "y": y},
                        lambda working, v=vertex_id, x=x, y=y: working.move_vertex(v, x, y),
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

    if best is None:
        return RepairResult(
            block_id, None, {}, before, before, considered, neighbourhood,
            reason=(
                f"no local move raises the worst cell around {block_id} by {MIN_GAIN:g} degrees "
                f"(corners {before.min_angle:.1f} to {before.max_angle:.1f} degrees, interior "
                f"{before.interior:.1f}; {considered} candidates): the defect is in the "
                "topology round it, not in a vertex"
            ),
            rejected=tuple(rejected[:8]),
        )
    _key, operation, arguments, after = best
    if operation == "move_vertex":
        model.move_vertex(arguments["vertex"], arguments["x"], arguments["y"])
    elif operation == "combine_blocks":
        model.combine_blocks(tuple(arguments["edge"]))
    else:
        model.split_edge(tuple(arguments["edge"]), arguments["fraction"])
    model.validate()
    return RepairResult(
        block_id, operation, arguments, before, after, considered, neighbourhood,
        reason=(
            f"{operation} took the corners around {block_id} from "
            f"{before.min_angle:.1f}..{before.max_angle:.1f} (interior {before.interior:.1f}) to "
            f"{after.min_angle:.1f}..{after.max_angle:.1f} (interior {after.interior:.1f}) degrees"
        ),
    )
