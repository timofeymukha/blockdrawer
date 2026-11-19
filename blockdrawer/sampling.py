"""Block interiors sampled at their real cell counts, and what checkMesh would see.

The corner screen of :mod:`blockdrawer.quality` measures the first cell at
each block corner. A block with curved or crossed spokes hides its worst
cells between the corners - one block read 18 degrees there and 68 in
checkMesh - so this module builds every block's mesh nodes at its real cell
counts and grading with blockMesh's edge-weighted transfinite interpolation
(the preview's), coarsened only above ``max_cells`` a side, and measures:

* every sampled cell's corner angles, whether it folds (non-positive area or
  a collapsed side), and whether it is *severe* - a corner within
  ``SEVERE_ANGLE`` of 0 or 180 degrees;
* every face's non-orthogonality the way checkMesh measures it: the angle
  between the vector joining the two cells' centroids and the face normal,
  across the faces inside a block and the faces on a shared block edge. A
  shared-edge face counts for the block that comes first in block order,
  because blockMesh numbers cells block by block and makes the lower cell
  of a face its owner - so the per-block counts line up with checkMesh's
  ``nonOrthoFaces`` set mapped to blocks. A face on a ``cyclic`` patch is
  coupled, as checkMesh treats it: measured against its partner cell's
  centre carried across the pair's motion (:mod:`blockdrawer.cyclic`), on
  both sides, and not with the boundary-face skewness formula.

At the real counts the face measure reproduces checkMesh's maximum
non-orthogonality on the extruded mesh; above ``max_cells`` a side it is
measured on coarser cells, which smooths grading and curvature a little.
:mod:`blockdrawer.repair` judges its candidates with the same grids.
Everything here is UI-free and standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Collection, Sequence

from .cyclic import CyclicPair, cyclic_pairs
from .domain import Block, EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .preview import _block_mesh_point, _directed_edge_samples, _sample_indices


Point = tuple[float, float]
Grid = Sequence[Sequence[Point]]

# Blocks are sampled at their real counts up to this many cells a side.
DEFAULT_MAX_CELLS = 64
# checkMesh's threshold for a severely non-orthogonal face.
SEVERE_NON_ORTHOGONALITY = 70.0
# A sampled cell is severe when a corner is within this many degrees of 0 or
# 180: for a sheared cell that is checkMesh's 70 degrees of non-orthogonality.
SEVERE_ANGLE = 90.0 - SEVERE_NON_ORTHOGONALITY
# checkMesh's cell aspect ratio limit (primitiveMesh::aspectThreshold_): a
# cell above it is a "High aspect ratio cells found" failure.
ASPECT_LIMIT = 1000.0


@dataclass(frozen=True)
class BlockGrid:
    """One block's sampled mesh nodes: ``nodes[row][column]``, rows along y."""

    block_id: str
    nodes: tuple[tuple[Point, ...], ...]
    x_cells: int
    y_cells: int
    # The real node indices kept along x and y (empty: every node, or the
    # count-free shape grid).
    x_indices: tuple[int, ...] = ()
    y_indices: tuple[int, ...] = ()

    @property
    def sampled_cells(self) -> tuple[int, int]:
        return len(self.nodes[0]) - 1, len(self.nodes) - 1

    @property
    def coarsened(self) -> bool:
        return self.sampled_cells != (self.x_cells, self.y_cells)


def _uniform_samples(model: MeshModel, directed: tuple[str, str], cells: int):
    """Edge samples at ``cells`` even parameter fractions, ignoring counts and grading."""
    current = edge_key(*directed)
    fractions = tuple(index / cells for index in range(cells + 1))
    canonical = fractions if directed == current else tuple(1.0 - value for value in fractions)
    points = model.edge_points(current, canonical)
    return tuple(zip(fractions, points))


def block_grid(
    model: MeshModel,
    block: Block,
    max_cells: int | None = DEFAULT_MAX_CELLS,
    *,
    uniform: int | None = None,
) -> BlockGrid:
    """The block's mesh nodes at its real counts, coarsened only above ``max_cells`` a side.

    Coarsening keeps every n-th node counted from both ends of an edge, so
    two blocks sharing an edge sample the same nodes on it; a coarsened side
    then has from about half to twice ``max_cells`` cells. ``None`` (or 0)
    samples every real node. With ``uniform`` the block is sampled at that
    many even parameter fractions a side instead, independent of every
    count and grading: the block's *shape* grid.
    """
    directed = [block.directed_edge(index) for index in range(4)]
    edges = [edge_key(*item) for item in directed]
    corners = tuple(
        (model.vertices[identifier].x, model.vertices[identifier].y)
        for identifier in block.vertices
    )
    if uniform:
        bottom = _uniform_samples(model, directed[0], uniform)
        right = _uniform_samples(model, directed[1], uniform)
        top = _uniform_samples(model, (directed[2][1], directed[2][0]), uniform)
        left = _uniform_samples(model, (directed[3][1], directed[3][0]), uniform)
        nodes = tuple(
            tuple(
                _block_mesh_point(bottom[a], right[b], top[a], left[b], corners)  # type: ignore[arg-type]
                for a in range(uniform + 1)
            )
            for b in range(uniform + 1)
        )
        return BlockGrid(block.id, nodes, uniform, uniform)
    x_cells = model.edge_cells[edges[0]]
    y_cells = model.edge_cells[edges[1]]
    limit = max_cells if max_cells else None
    x_step = 1 if limit is None else max(1, math.ceil(x_cells / limit))
    y_step = 1 if limit is None else max(1, math.ceil(y_cells / limit))
    x_indices = _sample_indices(x_cells, x_step)
    y_indices = _sample_indices(y_cells, y_step)
    bottom = _directed_edge_samples(model, directed[0], x_indices)
    right = _directed_edge_samples(model, directed[1], y_indices)
    top = _directed_edge_samples(model, (directed[2][1], directed[2][0]), x_indices)
    left = _directed_edge_samples(model, (directed[3][1], directed[3][0]), y_indices)
    nodes = tuple(
        tuple(
            _block_mesh_point(bottom[a], right[b], top[a], left[b], corners)  # type: ignore[arg-type]
            for a in range(len(x_indices))
        )
        for b in range(len(y_indices))
    )
    return BlockGrid(block.id, nodes, x_cells, y_cells, tuple(x_indices), tuple(y_indices))


@dataclass(frozen=True)
class CellAngles:
    """Corner angles over a grid's cells.

    ``worst`` is the smallest distance of any corner from 0 or 180 degrees,
    ``-inf`` when a cell folds (or when measuring stopped at the first fold).
    """

    cells: int
    folded: int
    severe: int
    min_angle: float
    max_angle: float
    folded_at: Point | None = None

    @property
    def worst(self) -> float:
        if self.folded:
            return -math.inf
        return min(self.min_angle, 180.0 - self.max_angle)


def cell_angles(
    nodes: Grid, *, severe_angle: float = SEVERE_ANGLE, stop_at_fold: bool = False
) -> CellAngles:
    """Measure every cell of ``nodes``; ``stop_at_fold`` returns at the first fold."""
    measured = _measure_cells(nodes, severe_angle, stop_at_fold, centroids=False)
    return measured[0]


@dataclass
class _AspectTally:
    """checkMesh's cell aspect ratio over a grid's cells
    (``primitiveMeshTools::cellClosedness``).

    checkMesh sums the magnitudes of each Cartesian component of a cell's
    face area vectors and, over the mesh's solved directions, takes the
    largest sum over the smallest. A side face over the segment (ex, ey)
    extruded by the depth has the area vector (ey, -ex) times the depth and
    the two z faces (0, 0, area). With both z patches ``empty`` only x and y
    are solved and the depth cancels: max(sum |ex|, sum |ey|) over the
    smaller. Otherwise all three are, and checkMesh also takes the
    hydraulic ratio sum / 6 / volume^(2/3). The measure is Cartesian, so a
    long thin cell at 45 degrees reads 1.

    A sampled cell of a coarsened grid spans several real cells; its sides
    are divided by the real cells they span before measuring, which assumes
    the real cells inside it are alike.
    """

    depth: float | None
    worst: float = 0.0
    worst_at: Point | None = None
    high: int = 0

    def add(self, quad: Sequence[Point], area: float, along: int, across: int) -> None:
        sum_x = sum_y = 0.0
        for index in range(4):
            (x0, y0), (x1, y1) = quad[index], quad[(index + 1) % 4]
            # Sides 0 and 2 run along x (spanning ``along`` real cells),
            # sides 1 and 3 along y (``across``).
            span = along if index % 2 == 0 else across
            sum_x += abs(y1 - y0) / span
            sum_y += abs(x1 - x0) / span
        low, high = min(sum_x, sum_y), max(sum_x, sum_y)
        if self.depth is None:
            ratio = high / low if low > 0.0 else math.inf
        else:
            area = area / (along * across)
            sums = (sum_x * self.depth, sum_y * self.depth, 2.0 * area)
            ratio = max(sums) / min(sums) if min(sums) > 0.0 else math.inf
            volume = area * self.depth
            if volume > 0.0:
                ratio = max(ratio, sum(sums) / 6.0 / volume ** (2.0 / 3.0))
        if ratio > ASPECT_LIMIT:
            self.high += along * across
        if ratio > self.worst or self.worst_at is None:
            self.worst = max(self.worst, ratio)
            self.worst_at = (
                0.25 * (quad[0][0] + quad[1][0] + quad[2][0] + quad[3][0]),
                0.25 * (quad[0][1] + quad[1][1] + quad[2][1] + quad[3][1]),
            )


def aspect_depth(model: MeshModel) -> float | None:
    """The cell depth checkMesh's aspect ratio sees, ``None`` when both z
    patches are ``empty`` (a 2D mesh, where the depth does not count)."""
    if model.z_min_patch_type == "empty" and model.z_max_patch_type == "empty":
        return None
    return (model.z_max - model.z_min) / model.z_cells


def _spans(indices: Sequence[int], count: int) -> list[int]:
    """Real cells per sampled cell along one direction."""
    if not indices:
        return [1] * count
    return [second - first for first, second in zip(indices, indices[1:])]


def _measure_cells(
    nodes: Grid, severe_angle: float, stop_at_fold: bool, *, centroids: bool,
    aspect: _AspectTally | None = None, spans: tuple[Sequence[int], Sequence[int]] = ((), ()),
) -> tuple[CellAngles, list[list[Point]]]:
    if aspect is not None:
        x_spans = _spans(spans[0], len(nodes[0]) - 1)
        y_spans = _spans(spans[1], len(nodes) - 1)
    acos = math.acos
    degrees = math.degrees
    hypot = math.hypot
    low = 180.0
    high = 0.0
    folded = 0
    severe = 0
    cells = 0
    folded_at: Point | None = None
    centres: list[list[Point]] = []
    for j, (row, upper) in enumerate(zip(nodes, nodes[1:])):
        centre_row: list[Point] = []
        for a in range(len(row) - 1):
            quad = (row[a], row[a + 1], upper[a + 1], upper[a])
            cells += 1
            area = 0.0
            sum_x = 0.0
            sum_y = 0.0
            for (x0, y0), (x1, y1) in zip(quad, quad[1:] + quad[:1]):
                cross = x0 * y1 - x1 * y0
                area += cross
                sum_x += (x0 + x1) * cross
                sum_y += (y0 + y1) * cross
            if centroids:
                if area != 0.0:
                    centre_row.append((sum_x / (3.0 * area), sum_y / (3.0 * area)))
                else:
                    centre_row.append((
                        0.25 * (quad[0][0] + quad[1][0] + quad[2][0] + quad[3][0]),
                        0.25 * (quad[0][1] + quad[1][1] + quad[2][1] + quad[3][1]),
                    ))
            bad = area <= 0.0
            cell_low = 180.0
            cell_high = 0.0
            if not bad:
                for index in range(4):
                    here = quad[index]
                    before = quad[index - 1]
                    after = quad[(index + 1) % 4]
                    ax, ay = before[0] - here[0], before[1] - here[1]
                    bx, by = after[0] - here[0], after[1] - here[1]
                    norms = hypot(ax, ay) * hypot(bx, by)
                    if norms <= 0.0:
                        bad = True
                        break
                    cosine = (ax * bx + ay * by) / norms
                    angle = degrees(acos(-1.0 if cosine < -1.0 else 1.0 if cosine > 1.0 else cosine))
                    if angle < cell_low:
                        cell_low = angle
                    if angle > cell_high:
                        cell_high = angle
            if bad:
                folded += 1
                if folded_at is None:
                    folded_at = (
                        0.25 * (quad[0][0] + quad[1][0] + quad[2][0] + quad[3][0]),
                        0.25 * (quad[0][1] + quad[1][1] + quad[2][1] + quad[3][1]),
                    )
                if stop_at_fold:
                    return CellAngles(cells, folded, severe, low, high, folded_at), centres
                continue
            if aspect is not None:
                aspect.add(quad, 0.5 * area, x_spans[a], y_spans[j])
            if cell_low < low:
                low = cell_low
            if cell_high > high:
                high = cell_high
            if min(cell_low, 180.0 - cell_high) < severe_angle:
                severe += 1
        if centroids:
            centres.append(centre_row)
    return CellAngles(cells, folded, severe, low, high, folded_at), centres


def _non_orthogonality(
    owner: Point, neighbour: Point, start: Point, end: Point, outward_right: bool
) -> float:
    """checkMesh's angle between the centre-to-centre vector and the face normal."""
    dx = neighbour[0] - owner[0]
    dy = neighbour[1] - owner[1]
    ex = end[0] - start[0]
    ey = end[1] - start[1]
    nx, ny = (ey, -ex) if outward_right else (-ey, ex)
    norms = math.hypot(dx, dy) * math.hypot(nx, ny)
    if norms <= 0.0:
        return 90.0
    cosine = (dx * nx + dy * ny) / norms
    return math.degrees(math.acos(-1.0 if cosine < -1.0 else 1.0 if cosine > 1.0 else cosine))


@dataclass
class _FaceTally:
    faces: int = 0
    severe: int = 0
    worst: float = 0.0
    worst_at: Point | None = None
    total: float = 0.0
    skewness: float = 0.0
    skew_at: Point | None = None

    def skew(self, value: float, start: Point, end: Point) -> None:
        if value > self.skewness or self.skew_at is None:
            self.skewness = max(self.skewness, value)
            self.skew_at = (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))

    def add(self, value: float, start: Point, end: Point) -> None:
        self.faces += 1
        # checkMesh averages the cosines and reports the angle of the mean.
        self.total += math.cos(math.radians(value))
        if value > SEVERE_NON_ORTHOGONALITY:
            self.severe += 1
        if value > self.worst or self.worst_at is None:
            self.worst = max(self.worst, value)
            self.worst_at = (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))


def _skewness(owner: Point, neighbour: Point | None, start: Point, end: Point) -> float:
    """checkMesh's face skewness (primitiveMeshTools::faceSkewness) on the
    extruded face over the segment start-end: how far the line between the
    two cell centres passes from the face centre, over the face's extent in
    that direction. ``neighbour`` None is a boundary face, measured as
    checkMesh's boundaryFaceSkewness does."""
    cx, cy = 0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1])
    px, py = cx - owner[0], cy - owner[1]
    ex, ey = end[0] - start[0], end[1] - start[1]
    length = math.hypot(ex, ey)
    if length <= 0.0:
        return 0.0
    nx, ny = ey / length, -ex / length
    if neighbour is None:
        along = nx * px + ny * py
        dx, dy = nx * along, ny * along
        floor = 0.4
    else:
        dx, dy = neighbour[0] - owner[0], neighbour[1] - owner[1]
        floor = 0.2
    denominator = nx * dx + ny * dy
    factor = (nx * px + ny * py) / denominator if denominator != 0.0 else 0.0
    sx, sy = px - factor * dx, py - factor * dy
    size = math.hypot(sx, sy)
    if size <= 0.0:
        return 0.0
    hx, hy = sx / size, sy / size
    extent = max(
        floor * math.hypot(dx, dy),
        abs(hx * (start[0] - cx) + hy * (start[1] - cy)),
        abs(hx * (end[0] - cx) + hy * (end[1] - cy)),
    )
    return size / extent if extent > 0.0 else 0.0


def _interior_faces(nodes: Grid, centres: list[list[Point]], tally: _FaceTally) -> None:
    rows = len(centres)
    columns = len(centres[0]) if rows else 0
    for j in range(rows):
        lower = nodes[j]
        upper = nodes[j + 1]
        centre_row = centres[j]
        for i in range(columns - 1):
            # Face between cells (j, i) and (j, i + 1), running up the grid;
            # its right-hand normal points to +x, from the first cell.
            start, end = lower[i + 1], upper[i + 1]
            tally.add(_non_orthogonality(centre_row[i], centre_row[i + 1], start, end, True), start, end)
            tally.skew(_skewness(centre_row[i], centre_row[i + 1], start, end), start, end)
    for j in range(rows - 1):
        line = nodes[j + 1]
        here = centres[j]
        above = centres[j + 1]
        for i in range(columns):
            # Face between cells (j, i) and (j + 1, i), running along +x;
            # its left-hand normal points to +y.
            start, end = line[i], line[i + 1]
            tally.add(_non_orthogonality(here[i], above[i], start, end, False), start, end)
            tally.skew(_skewness(here[i], above[i], start, end), start, end)


def _sides(
    block: Block, nodes: Grid, centres: list[list[Point]]
) -> list[tuple[EdgeKey, bool, list[Point], list[Point]]]:
    """Per block side: its edge, whether the block runs it canonically, and its
    nodes and adjacent cell centres in canonical order."""
    v = block.vertices
    last_row = len(nodes) - 1
    last_column = len(nodes[0]) - 1
    natural = (
        ((v[0], v[1]), list(nodes[0]), list(centres[0])),
        ((v[1], v[2]), [row[last_column] for row in nodes], [row[-1] for row in centres]),
        ((v[3], v[2]), list(nodes[last_row]), list(centres[-1])),
        ((v[0], v[3]), [row[0] for row in nodes], [row[0] for row in centres]),
    )
    sides = []
    for index, ((first, _second), side_nodes, side_cells) in enumerate(natural):
        current = edge_key(*block.directed_edge(index))
        if first != current[0]:
            side_nodes.reverse()
            side_cells.reverse()
        sides.append((current, block.directed_edge(index) == current, side_nodes, side_cells))
    return sides


@dataclass(frozen=True)
class BlockSample:
    """The sampled interior of one block."""

    block_id: str
    sampled_cells: tuple[int, int]
    coarsened: bool
    min_angle: float
    max_angle: float
    folded_cells: int
    severe_cells: int
    max_non_orthogonality: float
    severe_faces: int
    worst_face: Point | None
    folded_at: Point | None = None
    faces: int = 0
    sum_cosines: float = 0.0
    max_skewness: float = 0.0
    worst_skew_face: Point | None = None
    # checkMesh's cell aspect ratio (see _AspectTally): the largest, where,
    # and the cells above ASPECT_LIMIT.
    max_aspect_ratio: float = 0.0
    worst_aspect_cell: Point | None = None
    high_aspect_cells: int = 0

    def to_data(self) -> dict[str, Any]:
        return {
            "sampled_cells": list(self.sampled_cells),
            "coarsened": self.coarsened,
            "min_angle": self.min_angle,
            "max_angle": self.max_angle,
            "folded_cells": self.folded_cells,
            "severe_cells": self.severe_cells,
            "max_non_orthogonality": self.max_non_orthogonality,
            "severe_faces": self.severe_faces,
            "worst_face": list(self.worst_face) if self.worst_face else None,
            "folded_at": list(self.folded_at) if self.folded_at else None,
            "faces": self.faces,
            "max_skewness": self.max_skewness,
            "worst_skew_face": list(self.worst_skew_face) if self.worst_skew_face else None,
            "max_aspect_ratio": self.max_aspect_ratio,
            "worst_aspect_cell": list(self.worst_aspect_cell) if self.worst_aspect_cell else None,
            "high_aspect_cells": self.high_aspect_cells,
            "mean_non_orthogonality": (
                math.degrees(math.acos(max(-1.0, min(1.0, self.sum_cosines / self.faces))))
                if self.faces else 0.0
            ),
        }


@dataclass(frozen=True)
class ModelSample:
    """Every block's sampled interior and the totals over the model."""

    blocks: tuple[BlockSample, ...]
    max_cells: int | None

    def summary(self) -> dict[str, Any]:
        if not self.blocks:
            return {"max_cells_per_side": self.max_cells, "sampled_cells": 0}
        worst = max(self.blocks, key=lambda item: item.max_non_orthogonality)
        return {
            "max_cells_per_side": self.max_cells,
            "sampled_cells": sum(item.sampled_cells[0] * item.sampled_cells[1] for item in self.blocks),
            "coarsened_blocks": sum(1 for item in self.blocks if item.coarsened),
            "min_angle": min(item.min_angle for item in self.blocks),
            "max_angle": max(item.max_angle for item in self.blocks),
            "max_non_orthogonality": worst.max_non_orthogonality,
            "worst_block": worst.block_id,
            "worst_face": list(worst.worst_face) if worst.worst_face else None,
            "folded_cells": sum(item.folded_cells for item in self.blocks),
            "folded_blocks": sum(1 for item in self.blocks if item.folded_cells),
            "severe_cells": sum(item.severe_cells for item in self.blocks),
            "severe_faces": sum(item.severe_faces for item in self.blocks),
            "severe_threshold": SEVERE_NON_ORTHOGONALITY,
            "faces": sum(item.faces for item in self.blocks),
            "max_skewness": max(item.max_skewness for item in self.blocks),
            "worst_skew_block": max(self.blocks, key=lambda item: item.max_skewness).block_id,
            "max_aspect_ratio": max(item.max_aspect_ratio for item in self.blocks),
            "worst_aspect_block": max(self.blocks, key=lambda item: item.max_aspect_ratio).block_id,
            "high_aspect_cells": sum(item.high_aspect_cells for item in self.blocks),
            "high_aspect_blocks": sum(1 for item in self.blocks if item.high_aspect_cells),
            "aspect_limit": ASPECT_LIMIT,
            "average_non_orthogonality": math.degrees(math.acos(max(-1.0, min(1.0, (
                sum(item.sum_cosines for item in self.blocks)
                / max(1, sum(item.faces for item in self.blocks))
            ))))) if any(item.faces for item in self.blocks) else 0.0,
        }


def sample_model(
    model: MeshModel,
    max_cells: int | None = DEFAULT_MAX_CELLS,
    *,
    uniform: int | None = None,
    blocks: Collection[str] | None = None,
    planar: bool = False,
) -> ModelSample:
    """Sample every block and measure its cells and faces, shared edges included.

    ``uniform`` samples every block at that many even parameter fractions a
    side instead of its real nodes: the count-free shape of the layout.
    ``blocks`` samples only those blocks; the faces they share with a block
    left out are not measured. ``planar`` measures the aspect ratio in the
    plane, as checkMesh does with both z patches ``empty``, whatever they
    are: the depth's share is the export settings', not the 2D mesh's.
    """
    tallies: list[_FaceTally] = []
    aspects: list[_AspectTally] = []
    depth = None if planar else aspect_depth(model)
    angles: list[CellAngles] = []
    grids: list[BlockGrid] = []
    shared: dict[EdgeKey, list[tuple[int, bool, list[Point], list[Point]]]] = {}
    users: dict[EdgeKey, int] = {}
    if blocks is not None:
        wanted = set(blocks)
        for block in model.blocks:
            for index in range(4):
                current = edge_key(*block.directed_edge(index))
                users[current] = users.get(current, 0) + 1
    for block in model.blocks:
        if blocks is not None and block.id not in wanted:
            continue
        grid = block_grid(model, block, max_cells, uniform=uniform)
        aspect = _AspectTally(depth)
        measured, centres = _measure_cells(
            grid.nodes, SEVERE_ANGLE, False, centroids=True, aspect=aspect,
            spans=(grid.x_indices, grid.y_indices),
        )
        aspects.append(aspect)
        tally = _FaceTally()
        _interior_faces(grid.nodes, centres, tally)
        for current, canonical, side_nodes, side_cells in _sides(block, grid.nodes, centres):
            shared.setdefault(current, []).append((len(grids), canonical, side_nodes, side_cells))
        grids.append(grid)
        angles.append(measured)
        tallies.append(tally)
    coupled = _coupled_partners(model)
    for current, occurrences in shared.items():
        if current in coupled:
            if len(occurrences) == 1:
                _measure_coupled(current, occurrences[0], coupled[current], shared, tallies)
            continue
        if len(occurrences) == 1 and users.get(current, 1) == 1:
            # A side on the domain boundary: checkMesh measures its faces'
            # skewness too.
            (owner, _canonical, side_nodes, owner_cells), = occurrences
            tally = tallies[owner]
            for k, own in enumerate(owner_cells):
                start, end = side_nodes[k], side_nodes[k + 1]
                tally.skew(_skewness(own, None, start, end), start, end)
            continue
        if len(occurrences) != 2:
            continue  # a face shared with a block left out
        (owner, canonical, side_nodes, owner_cells), (_other, _c, other_nodes, other_cells) = sorted(
            occurrences, key=lambda item: item[0]
        )
        if len(owner_cells) != len(other_cells):  # pragma: no cover - counts are shared
            continue
        # The owner's interior is left of its own traversal: when it runs the
        # edge canonically the face normal out of it is the right-hand one.
        tally = tallies[owner]
        for k, (own, nei) in enumerate(zip(owner_cells, other_cells)):
            start, end = side_nodes[k], side_nodes[k + 1]
            tally.add(_non_orthogonality(own, nei, start, end, canonical), start, end)
            tally.skew(_skewness(own, nei, start, end), start, end)
    blocks = tuple(
        BlockSample(
            grid.block_id,
            grid.sampled_cells,
            grid.coarsened,
            measured.min_angle,
            measured.max_angle,
            measured.folded,
            measured.severe,
            tally.worst,
            tally.severe,
            tally.worst_at,
            measured.folded_at,
            tally.faces,
            tally.total,
            tally.skewness,
            tally.skew_at,
            aspect.worst,
            aspect.worst_at,
            aspect.high,
        )
        for grid, measured, tally, aspect in zip(grids, angles, tallies, aspects)
    )
    return ModelSample(blocks, max_cells)


# A message about folded cells names at most this many blocks.
FOLD_BLOCKS_NAMED = 5
# The count-free (uniform) grid a fold is looked for on, cells a side: the
# shape step's fold check (smooth's fold_cells default).
FOLD_CELLS = 32


def folded_blocks(sample: ModelSample, limit: int = FOLD_BLOCKS_NAMED) -> str:
    """The blocks of ``sample`` with folded cells, most first, as
    ``b3 (40), b7 (2)``, and how many more there are."""
    folded = sorted(
        ((item.folded_cells, item.block_id) for item in sample.blocks if item.folded_cells),
        key=lambda item: (-item[0], item[1]),
    )
    text = ", ".join(f"{block} ({count})" for count, block in folded[:limit])
    if len(folded) > limit:
        text += f" and {len(folded) - limit} more block(s)"
    return text


def _coupled_partners(model: MeshModel) -> dict[EdgeKey, tuple[EdgeKey, bool, Any]]:
    """Each cyclic edge's partner, whether their canonical directions agree,
    and the map carrying a point from the partner's side to this one. A pair
    that does not correspond (export refuses it) is left to the
    boundary-face measure."""
    if not any(boundary.kind == "cyclic" for boundary in model.boundaries.values()):
        return {}
    try:
        pairs: list[CyclicPair] = cyclic_pairs(model, check_nodes=False)
    except TopologyError:
        return {}
    result: dict[EdgeKey, tuple[EdgeKey, bool, Any]] = {}
    for pair in pairs:
        for match in pair.matches:
            result[match.first] = (match.second, match.aligned, pair.apply_inverse)
            result[match.second] = (match.first, match.aligned, pair.apply)
    return result


def _measure_coupled(
    current: EdgeKey,
    occurrence: tuple[int, bool, list[Point], list[Point]],
    partner: tuple[EdgeKey, bool, Any],
    shared: dict[EdgeKey, list[tuple[int, bool, list[Point], list[Point]]]],
    tallies: list[_FaceTally],
) -> None:
    """checkMesh's measure of one side of a cyclic pair: each face against
    the partner cell's centre carried across the coupling."""
    other, aligned, carry = partner
    owner, canonical, side_nodes, owner_cells = occurrence
    found = shared.get(other)
    if not found or len(found) != 1:
        return  # the partner's block was left out of the sample
    partner_cells = list(found[0][3])
    if len(partner_cells) != len(owner_cells):
        return  # counts differ: the pair would not export
    if not aligned:
        partner_cells.reverse()
    tally = tallies[owner]
    for k, own in enumerate(owner_cells):
        nei = carry(*partner_cells[k])
        start, end = side_nodes[k], side_nodes[k + 1]
        tally.add(_non_orthogonality(own, nei, start, end, canonical), start, end)
        tally.skew(_skewness(own, nei, start, end), start, end)

