"""UI-independent structured mesh preview construction and caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable

from .domain import Block, EdgeKey, TopologyError, edge_key
from .model import MeshModel


Point = tuple[float, float]
Polyline = tuple[Point, ...]


@dataclass(frozen=True)
class MeshPreview:
    """A lightweight collection of per-block interior mesh lines."""

    polylines: tuple[Polyline, ...]
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
    points use a Coons-patch blend of the four boundaries. This is deliberately
    a visual approximation: it does not create or validate inter-block cells.
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
    return MeshPreview(
        tuple(polylines),
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

    bottom = tuple(
        _directed_edge_node(model, directed_edges[0], index)
        for index in x_indices
    )
    right = tuple(
        _directed_edge_node(model, directed_edges[1], index)
        for index in y_indices
    )
    top = tuple(
        _directed_edge_node(model, directed_edges[2], x_cells - index)
        for index in x_indices
    )
    left = tuple(
        _directed_edge_node(model, directed_edges[3], y_cells - index)
        for index in y_indices
    )

    vertices = tuple(model.vertices[identifier] for identifier in block.vertices)
    corners = tuple((vertex.x, vertex.y) for vertex in vertices)
    rows: list[Polyline] = []
    matrix: list[Polyline] = []
    for y_position, y_index in enumerate(y_indices):
        v = y_index / y_cells
        row = tuple(
            _coons_point(
                x_index / x_cells,
                v,
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


def _directed_edge_node(
    model: MeshModel,
    directed: tuple[str, str],
    local_index: int,
) -> Point:
    current = edge_key(*directed)
    cells = model.edge_cells[current]
    canonical_index = (
        local_index if directed == current else cells - local_index
    )
    fraction = model.edge_node_fraction(current, canonical_index)
    return model.edge_point(current, fraction)


def _coons_point(
    u: float,
    v: float,
    bottom: Point,
    right: Point,
    top: Point,
    left: Point,
    corners: tuple[Point, Point, Point, Point],
) -> Point:
    c00, c10, c11, c01 = corners
    bilinear_x = (
        (1.0 - u) * (1.0 - v) * c00[0]
        + u * (1.0 - v) * c10[0]
        + u * v * c11[0]
        + (1.0 - u) * v * c01[0]
    )
    bilinear_y = (
        (1.0 - u) * (1.0 - v) * c00[1]
        + u * (1.0 - v) * c10[1]
        + u * v * c11[1]
        + (1.0 - u) * v * c01[1]
    )
    return (
        (1.0 - v) * bottom[0] + v * top[0]
        + (1.0 - u) * left[0] + u * right[0]
        - bilinear_x,
        (1.0 - v) * bottom[1] + v * top[1]
        + (1.0 - u) * left[1] + u * right[1]
        - bilinear_y,
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
