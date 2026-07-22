"""Shared BlockDrawer domain types with no model or UI dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


class TopologyError(ValueError):
    """Raised when an operation would create an invalid block topology."""


@dataclass
class Vertex:
    id: str
    x: float
    y: float


@dataclass(frozen=True)
class Block:
    id: str
    vertices: tuple[str, str, str, str]

    def directed_edge(self, index: int) -> tuple[str, str]:
        return self.vertices[index], self.vertices[(index + 1) % 4]


@dataclass(frozen=True)
class EdgeGeometry:
    """Optional geometry attached to a topological edge.

    Straight edges are implicit and therefore absent from ``edge_geometry``.
    Keeping interpolation points as an ordered tuple leaves room for OpenFOAM
    spline and polyline edge types without changing the topology representation.
    """

    kind: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class EdgeGradingValues:
    """Equivalent grading representations for one directed edge."""

    length: float
    cell_ratio: float
    total_ratio: float
    start_width: float
    end_width: float


@dataclass(frozen=True)
class Boundary:
    """One named OpenFOAM boundary patch and its display metadata."""

    name: str
    kind: str
    color: str
    neighbour_patch: str | None = None


@dataclass(frozen=True)
class GeometryCurve:
    """A named reference-geometry curve through ordered 2D points."""

    id: str
    name: str
    points: tuple[tuple[float, float], ...]
    show_points: bool = True


EdgeKey: TypeAlias = tuple[str, str]
EdgeOccurrence: TypeAlias = tuple[Block, int, tuple[str, str]]


@dataclass(frozen=True, order=True)
class SpacingLink:
    """Pair two topological edge endpoints at one shared vertex."""

    vertex: str
    first_edge: EdgeKey
    second_edge: EdgeKey


@dataclass(frozen=True)
class ProjectionResult:
    """Summary of one atomic mesh-to-reference projection operation."""

    vertex_ids: tuple[str, ...]
    edges: tuple[EdgeKey, ...]
    projected_point_count: int
    converted_arcs: tuple[EdgeKey, ...]
    fitted_edges: tuple[EdgeKey, ...] = ()
    fit_interpolation_point_count: int = 0
    max_fit_error: float | None = None
    fit_tolerance: float | None = None
    fit_tolerance_met: bool = True


@dataclass(frozen=True)
class EdgeSplitResult:
    """Summary of one conformal split through an opposite-edge strip."""

    source_edge: EdgeKey
    fraction: float
    first_cells: int
    second_cells: int
    affected_edges: tuple[EdgeKey, ...]
    split_vertex_ids: tuple[str, ...]
    selected_segments: tuple[EdgeKey, EdgeKey]
    cut_edges: tuple[EdgeKey, ...]
    new_block_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlockCombineResult:
    """Summary of one atomic conformal block-combination operation."""

    source_edge: EdgeKey
    removed_edges: tuple[EdgeKey, ...]
    removed_vertex_ids: tuple[str, ...]
    merged_edges: tuple[EdgeKey, ...]
    merged_block_ids: tuple[str, ...]
    removed_block_ids: tuple[str, ...]


def edge_key(first: str, second: str) -> EdgeKey:
    """Return the canonical identity of an undirected topological edge."""
    if first == second:
        raise TopologyError("An edge must connect two distinct vertices")
    return (first, second) if first < second else (second, first)
