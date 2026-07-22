"""UI-independent structured mesh preview construction and caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable

from .domain import Block, EdgeKey, TopologyError, edge_key
from .model import MeshModel
from .render_cache import Bounds, points_bounds


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
    sampled_node_count = 0
    for block in model.blocks:
        block_lines, node_count = _build_block_preview(
            model, block, coarsening
        )
        polylines.extend(block_lines)
        sampled_node_count += node_count
    sampled_polylines = tuple(polylines)
    return MeshPreview(
        sampled_polylines,
        tuple(points_bounds(polyline) for polyline in sampled_polylines),
        len(model.blocks),
        sampled_node_count,
        coarsening,
    )


def _build_block_preview(
    model: MeshModel,
    block: Block,
    coarsening: int,
) -> tuple[tuple[Polyline, ...], int]:
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
    bottom = tuple(
        _directed_edge_sample(model, bottom_direction, index)
        for index in x_indices
    )
    right = tuple(
        _directed_edge_sample(model, right_direction, index)
        for index in y_indices
    )
    top = tuple(
        _directed_edge_sample(model, top_direction, index)
        for index in x_indices
    )
    left = tuple(
        _directed_edge_sample(model, left_direction, index)
        for index in y_indices
    )

    vertices = tuple(model.vertices[identifier] for identifier in block.vertices)
    corners = tuple((vertex.x, vertex.y) for vertex in vertices)
    rows: list[Polyline] = []
    matrix: list[Polyline] = []
    for y_position, y_index in enumerate(y_indices):
        row = tuple(
            _block_mesh_point(
                bottom[x_position],
                right[y_position],
                top[x_position],
                left[y_position],
                corners,
            )
            for x_position, x_index in enumerate(x_indices)
        )
        matrix.append(row)
        if y_index not in (0, y_cells):
            rows.append(row)

    columns = [
        tuple(row[x_position] for row in matrix)
        for x_position, x_index in enumerate(x_indices)
        if x_index not in (0, x_cells)
    ]
    return tuple((*rows, *columns)), len(x_indices) * len(y_indices)


def _directed_edge_sample(
    model: MeshModel,
    directed: tuple[str, str],
    local_index: int,
) -> EdgeSample:
    current = edge_key(*directed)
    cells = model.edge_cells[current]
    follows_canonical = directed == current
    canonical_index = local_index if follows_canonical else cells - local_index
    canonical_fraction = model.edge_node_fraction(current, canonical_index)
    local_fraction = (
        canonical_fraction if follows_canonical else 1.0 - canonical_fraction
    )
    return local_fraction, model.edge_point(current, canonical_fraction)


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

    corner_weights = (
        (1.0 - bottom_fraction) * (1.0 - left_fraction),
        bottom_fraction * (1.0 - right_fraction),
        top_fraction * right_fraction,
        (1.0 - top_fraction) * left_fraction,
    )
    weight_sum = sum(corner_weights)
    normalized_corners = tuple(
        weight / weight_sum for weight in corner_weights
    )

    bottom_weight = normalized_corners[0] + normalized_corners[1]
    top_weight = normalized_corners[2] + normalized_corners[3]
    left_weight = normalized_corners[0] + normalized_corners[3]
    right_weight = normalized_corners[1] + normalized_corners[2]

    straight_bottom = _lerp(c00, c10, bottom_fraction)
    straight_top = _lerp(c01, c11, top_fraction)
    straight_left = _lerp(c00, c01, left_fraction)
    straight_right = _lerp(c10, c11, right_fraction)

    def component(axis: int) -> float:
        x_contribution = (
            bottom_weight * straight_bottom[axis]
            + top_weight * straight_top[axis]
        )
        y_contribution = (
            left_weight * straight_left[axis]
            + right_weight * straight_right[axis]
        )
        z_contribution = sum(
            weight * corner[axis]
            for weight, corner in zip(normalized_corners, corners)
        )
        curved_correction = (
            bottom_weight * (bottom_point[axis] - straight_bottom[axis])
            + top_weight * (top_point[axis] - straight_top[axis])
            + left_weight * (left_point[axis] - straight_left[axis])
            + right_weight * (right_point[axis] - straight_right[axis])
        )
        return (
            x_contribution + y_contribution + z_contribution
        ) / 3.0 + curved_correction

    return component(0), component(1)


def _lerp(first: Point, second: Point, fraction: float) -> Point:
    return (
        first[0] + fraction * (second[0] - first[0]),
        first[1] + fraction * (second[1] - first[1]),
    )


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
