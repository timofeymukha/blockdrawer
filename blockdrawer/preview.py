"""UI-independent structured mesh preview construction and caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable

from .domain import Block, EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .render_cache import Bounds


Point = tuple[float, float]
Polyline = tuple[Point, ...]
EdgeSample = tuple[float, Point]


@dataclass(frozen=True)
class MeshPreview:
    """A lightweight collection of per-block interior mesh lines."""

    polylines: tuple[Polyline, ...]
    polyline_bounds: tuple[Bounds, ...]
    block_count: int
    sampled_node_count: int
    coarsening: int

    @property
    def line_count(self) -> int:
        return len(self.polylines)


class MeshPreviewCache:
    """Small LRU cache keyed only by mesh geometry and preview resolution."""

    def __init__(self, *, capacity: int = 4) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) \
                or capacity < 1:
            raise ValueError("Preview cache capacity must be a positive integer")
        self.capacity = capacity
        self._entries: OrderedDict[Hashable, MeshPreview] = OrderedDict()

    def get(
        self,
        model: MeshModel,
        coarsening: int,
    ) -> tuple[MeshPreview, bool]:
        """Return a preview and whether it came from the cache."""
        _validate_coarsening(coarsening)
        key = (coarsening, mesh_preview_signature(model))
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached, True

        preview = build_mesh_preview(model, coarsening)
        self._entries[key] = preview
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return preview, False

    def clear(self) -> None:
        self._entries.clear()


def mesh_preview_signature(model: MeshModel) -> Hashable:
    """Return the immutable subset of model state that affects a preview."""
    used_vertices = {
        identifier
        for block in model.blocks
        for identifier in block.vertices
    }
    vertices = tuple(
        (
            identifier,
            model.vertices[identifier].x,
            model.vertices[identifier].y,
        )
        for identifier in sorted(used_vertices)
    )
    blocks = tuple((block.id, block.vertices) for block in model.blocks)
    edges = tuple(
        _edge_signature(model, current)
        for current in model.edges()
    )
    return vertices, blocks, edges


def build_mesh_preview(model: MeshModel, coarsening: int = 1) -> MeshPreview:
    """Build coarsened interior grid lines for every quadrilateral block.

    Boundary node locations follow each edge's curve and grading. Interior
    points use the 2D specialization of OpenFOAM's edge-weighted transfinite
    interpolation. This is deliberately a visualization: it does not create or
    validate inter-block cells.
    """
    _validate_coarsening(coarsening)
    polylines: list[Polyline] = []
    polyline_bounds: list[Bounds] = []
    sampled_node_count = 0
    for block in model.blocks:
        block_lines, block_bounds, node_count = _build_block_preview(
            model, block, coarsening
        )
        polylines.extend(block_lines)
        polyline_bounds.extend(block_bounds)
        sampled_node_count += node_count
    sampled_polylines = tuple(polylines)
    return MeshPreview(
        sampled_polylines,
        tuple(polyline_bounds),
        len(model.blocks),
        sampled_node_count,
        coarsening,
    )


def _build_block_preview(
    model: MeshModel,
    block: Block,
    coarsening: int,
) -> tuple[tuple[Polyline, ...], tuple[Bounds, ...], int]:
    directed_edges = tuple(block.directed_edge(index) for index in range(4))
    edges = tuple(edge_key(*directed) for directed in directed_edges)
    x_cells = model.edge_cells[edges[0]]
    y_cells = model.edge_cells[edges[1]]
    x_indices = _sample_indices(x_cells, coarsening)
    y_indices = _sample_indices(y_cells, coarsening)

    bottom_direction = directed_edges[0]
    right_direction = directed_edges[1]
    top_direction = (directed_edges[2][1], directed_edges[2][0])
    left_direction = (directed_edges[3][1], directed_edges[3][0])
    bottom = _directed_edge_samples(model, bottom_direction, x_indices)
    right = _directed_edge_samples(model, right_direction, y_indices)
    top = _directed_edge_samples(model, top_direction, x_indices)
    left = _directed_edge_samples(model, left_direction, y_indices)

    vertices = tuple(model.vertices[identifier] for identifier in block.vertices)
    corners = tuple((vertex.x, vertex.y) for vertex in vertices)
    rows: list[Polyline] = []
    row_bounds: list[Bounds] = []
    matrix: list[Polyline] = []
    column_min_x = [float("inf")] * len(x_indices)
    column_min_y = [float("inf")] * len(x_indices)
    column_max_x = [float("-inf")] * len(x_indices)
    column_max_y = [float("-inf")] * len(x_indices)
    for y_position, y_index in enumerate(y_indices):
        row_points: list[Point] = []
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for x_position, _x_index in enumerate(x_indices):
            point = _block_mesh_point(
                bottom[x_position],
                right[y_position],
                top[x_position],
                left[y_position],
                corners,
            )
            row_points.append(point)
            x, y = point
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            if x < column_min_x[x_position]:
                column_min_x[x_position] = x
            if x > column_max_x[x_position]:
                column_max_x[x_position] = x
            if y < column_min_y[x_position]:
                column_min_y[x_position] = y
            if y > column_max_y[x_position]:
                column_max_y[x_position] = y
        row = tuple(row_points)
        matrix.append(row)
        if y_index not in (0, y_cells):
            rows.append(row)
            row_bounds.append((min_x, min_y, max_x, max_y))

    columns: list[Polyline] = []
    column_bounds: list[Bounds] = []
    for x_position, x_index in enumerate(x_indices):
        if x_index in (0, x_cells):
            continue
        columns.append(tuple(row[x_position] for row in matrix))
        column_bounds.append((
            column_min_x[x_position],
            column_min_y[x_position],
            column_max_x[x_position],
            column_max_y[x_position],
        ))
    return (
        tuple((*rows, *columns)),
        tuple((*row_bounds, *column_bounds)),
        len(x_indices) * len(y_indices),
    )


def _directed_edge_samples(
    model: MeshModel,
    directed: tuple[str, str],
    local_indices: tuple[int, ...],
) -> tuple[EdgeSample, ...]:
    current = edge_key(*directed)
    cells = model.edge_cells[current]
    follows_canonical = directed == current
    canonical_indices = tuple(
        index if follows_canonical else cells - index
        for index in local_indices
    )
    canonical_fractions = model.edge_node_fractions(
        current, canonical_indices
    )
    points = model.edge_points(current, canonical_fractions)
    return tuple(
        (
            fraction if follows_canonical else 1.0 - fraction,
            point,
        )
        for fraction, point in zip(canonical_fractions, points)
    )


def _block_mesh_point(
    bottom: EdgeSample,
    right: EdgeSample,
    top: EdgeSample,
    left: EdgeSample,
    corners: tuple[Point, Point, Point, Point],
) -> Point:
    """Reproduce blockMesh's edge-weighted interpolation in the 2D plane.

    OpenFOAM blends three normalized contributions: the pair of x edges, the
    pair of y edges, and the four z edges. In a pseudo-2D extrusion, the z-edge
    points reduce to the four 2D corners. Curved-edge corrections are then
    added with the same x/y edge weights.
    """
    bottom_fraction, bottom_point = bottom
    right_fraction, right_point = right
    top_fraction, top_point = top
    left_fraction, left_point = left
    c00, c10, c11, c01 = corners

    corner_00 = (1.0 - bottom_fraction) * (1.0 - left_fraction)
    corner_10 = bottom_fraction * (1.0 - right_fraction)
    corner_11 = top_fraction * right_fraction
    corner_01 = (1.0 - top_fraction) * left_fraction
    inverse_weight_sum = 1.0 / (
        corner_00 + corner_10 + corner_11 + corner_01
    )
    corner_00 *= inverse_weight_sum
    corner_10 *= inverse_weight_sum
    corner_11 *= inverse_weight_sum
    corner_01 *= inverse_weight_sum

    bottom_weight = corner_00 + corner_10
    top_weight = corner_11 + corner_01
    left_weight = corner_00 + corner_01
    right_weight = corner_10 + corner_11

    bottom_x = c00[0] + bottom_fraction * (c10[0] - c00[0])
    bottom_y = c00[1] + bottom_fraction * (c10[1] - c00[1])
    top_x = c01[0] + top_fraction * (c11[0] - c01[0])
    top_y = c01[1] + top_fraction * (c11[1] - c01[1])
    left_x = c00[0] + left_fraction * (c01[0] - c00[0])
    left_y = c00[1] + left_fraction * (c01[1] - c00[1])
    right_x = c10[0] + right_fraction * (c11[0] - c10[0])
    right_y = c10[1] + right_fraction * (c11[1] - c10[1])

    x_contribution = bottom_weight * bottom_x + top_weight * top_x
    y_contribution = left_weight * left_x + right_weight * right_x
    z_contribution = (
        corner_00 * c00[0]
        + corner_10 * c10[0]
        + corner_11 * c11[0]
        + corner_01 * c01[0]
    )
    x = (x_contribution + y_contribution + z_contribution) / 3.0
    x += (
        bottom_weight * (bottom_point[0] - bottom_x)
        + top_weight * (top_point[0] - top_x)
        + left_weight * (left_point[0] - left_x)
        + right_weight * (right_point[0] - right_x)
    )

    x_contribution = bottom_weight * bottom_y + top_weight * top_y
    y_contribution = left_weight * left_y + right_weight * right_y
    z_contribution = (
        corner_00 * c00[1]
        + corner_10 * c10[1]
        + corner_11 * c11[1]
        + corner_01 * c01[1]
    )
    y = (x_contribution + y_contribution + z_contribution) / 3.0
    y += (
        bottom_weight * (bottom_point[1] - bottom_y)
        + top_weight * (top_point[1] - top_y)
        + left_weight * (left_point[1] - left_y)
        + right_weight * (right_point[1] - right_y)
    )
    return x, y


def _sample_indices(cells: int, coarsening: int) -> tuple[int, ...]:
    indices = list(range(0, cells + 1, coarsening))
    if indices[-1] != cells:
        indices.append(cells)
    return tuple(indices)


def _edge_signature(model: MeshModel, current: EdgeKey) -> Hashable:
    geometry = model.edge_geometry.get(current)
    geometry_signature = (
        None if geometry is None else (geometry.kind, geometry.points)
    )
    return (
        current,
        model.edge_cells[current],
        model.edge_grading.get(current, 1.0),
        geometry_signature,
    )


def _validate_coarsening(coarsening: int) -> None:
    if isinstance(coarsening, bool) or not isinstance(coarsening, int) \
            or coarsening < 1:
        raise TopologyError("Preview coarsening must be a positive integer")
