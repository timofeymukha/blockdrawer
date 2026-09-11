"""Per-block and per-interface mesh quality heuristics.

These metrics are computed from the topology, edge curves, and grading that
BlockDrawer stores, without running ``blockMesh``. They approximate what a
mesher would check by eye: corner angles of the first cell at every block
corner, cell aspect ratios, cell-to-cell growth, and cell size jumps across
shared edges. They are heuristics that point an operator or agent at the
block to fix; OpenFOAM's ``checkMesh`` remains the authority (see
``blockdrawer.checkmesh``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .commands import edge_text
from .domain import Block, EdgeKey, edge_key
from .model import MeshModel


@dataclass(frozen=True)
class QualityThresholds:
    """Warning limits. They are deliberately conservative screening values."""

    min_angle: float = 30.0
    max_angle: float = 150.0
    non_orthogonality: float = 65.0
    cell_aspect_ratio: float = 100.0
    cell_growth_ratio: float = 1.3
    interface_size_ratio: float = 2.5

    @classmethod
    def from_overrides(cls, **overrides: float | None) -> "QualityThresholds":
        """Build thresholds from keyword overrides, ignoring ``None`` values."""
        return cls(**{
            key: value for key, value in overrides.items() if value is not None
        })


@dataclass(frozen=True)
class CornerQuality:
    vertex: str
    angle: float
    x_width: float
    y_width: float

    @property
    def aspect_ratio(self) -> float:
        low = min(self.x_width, self.y_width)
        high = max(self.x_width, self.y_width)
        return math.inf if low <= 0.0 else high / low


@dataclass(frozen=True)
class BlockQuality:
    block_id: str
    cells: tuple[int, int, int]
    area: float
    corners: tuple[CornerQuality, ...]
    min_angle: float
    max_angle: float
    non_orthogonality: float
    equiangle_skewness: float
    max_cell_aspect_ratio: float
    max_cell_growth_ratio: float
    min_cell_size: float
    max_cell_size: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterfaceQuality:
    """Cell size compatibility across one shared (internal) edge."""

    edge: EdgeKey
    blocks: tuple[str, str]
    size_ratios: tuple[float, float]
    max_size_ratio: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    blocks: tuple[BlockQuality, ...]
    interfaces: tuple[InterfaceQuality, ...]
    thresholds: QualityThresholds
    total_cells: int
    warnings: tuple[str, ...] = field(default=())

    def summary(self) -> dict[str, Any]:
        if not self.blocks:
            return {"blocks": 0, "total_cells": 0, "warning_count": 0}
        return {
            "blocks": len(self.blocks),
            "total_cells": self.total_cells,
            "min_angle": min(block.min_angle for block in self.blocks),
            "max_angle": max(block.max_angle for block in self.blocks),
            "max_non_orthogonality": max(
                block.non_orthogonality for block in self.blocks
            ),
            "max_equiangle_skewness": max(
                block.equiangle_skewness for block in self.blocks
            ),
            "max_cell_aspect_ratio": max(
                block.max_cell_aspect_ratio for block in self.blocks
            ),
            "max_cell_growth_ratio": max(
                block.max_cell_growth_ratio for block in self.blocks
            ),
            "min_cell_size": min(block.min_cell_size for block in self.blocks),
            "max_cell_size": max(block.max_cell_size for block in self.blocks),
            "max_interface_size_ratio": max(
                (interface.max_size_ratio for interface in self.interfaces),
                default=1.0,
            ),
            "warning_count": len(self.warnings),
        }

    def to_data(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "thresholds": asdict(self.thresholds),
            "blocks": [
                {
                    **{
                        key: value for key, value in asdict(block).items()
                        if key != "corners"
                    },
                    "cells": list(block.cells),
                    "warnings": list(block.warnings),
                    "corners": [
                        {**asdict(corner), "aspect_ratio": corner.aspect_ratio}
                        for corner in block.corners
                    ],
                }
                for block in self.blocks
            ],
            "interfaces": [
                {
                    "edge": edge_text(interface.edge),
                    "blocks": list(interface.blocks),
                    "size_ratios": list(interface.size_ratios),
                    "max_size_ratio": interface.max_size_ratio,
                    "warnings": list(interface.warnings),
                }
                for interface in self.interfaces
            ],
            "warnings": list(self.warnings),
        }


def assess_quality(
    model: MeshModel,
    thresholds: QualityThresholds | None = None,
) -> QualityReport:
    """Compute block and interface quality metrics for the whole model."""
    limits = thresholds or QualityThresholds()
    model.validate()
    blocks = tuple(_block_quality(model, block, limits) for block in model.blocks)
    interfaces = tuple(
        _interface_quality(model, current, occurrences, limits)
        for current, occurrences in model.edge_occurrences().items()
        if len(occurrences) == 2
    )
    warnings: list[str] = []
    for block in blocks:
        warnings.extend(block.warnings)
    for interface in interfaces:
        warnings.extend(interface.warnings)
    total_cells = sum(
        nx * ny * nz
        for nx, ny, nz in (model.block_cell_counts(block) for block in model.blocks)
    )
    return QualityReport(blocks, interfaces, limits, total_cells, tuple(warnings))


def format_quality(report: QualityReport) -> str:
    """Render a quality report as compact terminal text."""
    summary = report.summary()
    lines = [
        f"Quality: {summary['blocks']} block(s), {summary['total_cells']} cells, "
        f"{summary['warning_count']} warning(s)",
    ]
    if report.blocks:
        lines.append(
            f"  corner angles {_num(summary['min_angle'])}° .. "
            f"{_num(summary['max_angle'])}°, max non-orthogonality "
            f"{_num(summary['max_non_orthogonality'])}°, max equiangle skewness "
            f"{_num(summary['max_equiangle_skewness'])}"
        )
        lines.append(
            f"  cell sizes {_num(summary['min_cell_size'])} .. "
            f"{_num(summary['max_cell_size'])}, max cell aspect ratio "
            f"{_num(summary['max_cell_aspect_ratio'])}, max cell growth ratio "
            f"{_num(summary['max_cell_growth_ratio'])}, max size jump across "
            f"internal edges {_num(summary['max_interface_size_ratio'])}"
        )
        lines.append("")
        lines.append(
            "Blocks (id | cells | angles min/max | non-orth | skew | "
            "aspect | growth | cell size min..max):"
        )
        for block in report.blocks:
            nx, ny, nz = block.cells
            flag = " !" if block.warnings else ""
            lines.append(
                f"  {block.block_id} | {nx} {ny} {nz} | "
                f"{_num(block.min_angle)}/{_num(block.max_angle)} | "
                f"{_num(block.non_orthogonality)} | "
                f"{_num(block.equiangle_skewness)} | "
                f"{_num(block.max_cell_aspect_ratio)} | "
                f"{_num(block.max_cell_growth_ratio)} | "
                f"{_num(block.min_cell_size)}..{_num(block.max_cell_size)}{flag}"
            )
    if report.interfaces:
        lines.append("")
        lines.append("Internal edges (edge | blocks | size jump at each end):")
        for interface in report.interfaces:
            flag = " !" if interface.warnings else ""
            lines.append(
                f"  {edge_text(interface.edge)} | {','.join(interface.blocks)} | "
                f"{_num(interface.size_ratios[0])}, "
                f"{_num(interface.size_ratios[1])}{flag}"
            )
    lines.append("")
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in report.warnings)
    else:
        lines.append("No warnings at the current thresholds.")
    limits = report.thresholds
    lines.append(
        f"Thresholds: angle {_num(limits.min_angle)}..{_num(limits.max_angle)}°, "
        f"non-orthogonality {_num(limits.non_orthogonality)}°, aspect "
        f"{_num(limits.cell_aspect_ratio)}, growth {_num(limits.cell_growth_ratio)}, "
        f"interface jump {_num(limits.interface_size_ratio)}"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Block metrics
# ---------------------------------------------------------------------------


def _block_quality(
    model: MeshModel, block: Block, limits: QualityThresholds
) -> BlockQuality:
    edges = [edge_key(*block.directed_edge(index)) for index in range(4)]
    nx, ny, nz = model.block_cell_counts(block)
    corners: list[CornerQuality] = []
    for index in range(4):
        vertex_id = block.vertices[index]
        outgoing = block.directed_edge(index)
        incoming = block.directed_edge((index + 3) % 4)
        first_direction = _first_cell_direction(model, outgoing)
        second_direction = _first_cell_direction(model, (incoming[1], incoming[0]))
        angle = _angle_between(first_direction, second_direction)
        # Local edge 0 and 2 are x edges; 1 and 3 are y edges.
        x_edge = edges[index] if index % 2 == 0 else edges[(index + 3) % 4]
        y_edge = edges[index] if index % 2 == 1 else edges[(index + 3) % 4]
        corners.append(CornerQuality(
            vertex_id,
            angle,
            model.edge_width_at_vertex(x_edge, vertex_id),
            model.edge_width_at_vertex(y_edge, vertex_id),
        ))

    angles = [corner.angle for corner in corners]
    min_angle = min(angles)
    max_angle = max(angles)
    non_orthogonality = max(abs(90.0 - angle) for angle in angles)
    skewness = max((max_angle - 90.0) / 90.0, (90.0 - min_angle) / 90.0, 0.0)
    max_aspect = max(corner.aspect_ratio for corner in corners)

    growth = 1.0
    sizes: list[float] = []
    for current in edges:
        values = model.edge_grading_values(current)
        ratio = values.cell_ratio
        growth = max(growth, ratio, 1.0 / ratio if ratio > 0 else math.inf)
        sizes.extend((values.start_width, values.end_width))

    corner_points = [
        (model.vertices[identifier].x, model.vertices[identifier].y)
        for identifier in block.vertices
    ]
    area = _polygon_area(corner_points)

    warnings: list[str] = []
    for corner in corners:
        if corner.angle < limits.min_angle or corner.angle > limits.max_angle:
            warnings.append(
                f"Block {block.id}: corner angle {_num(corner.angle)}° at "
                f"{corner.vertex} is outside {_num(limits.min_angle)}..."
                f"{_num(limits.max_angle)}°"
            )
    if non_orthogonality > limits.non_orthogonality and not any(
        "corner angle" in warning for warning in warnings
    ):
        warnings.append(
            f"Block {block.id}: non-orthogonality {_num(non_orthogonality)}° "
            f"exceeds {_num(limits.non_orthogonality)}°"
        )
    worst_corner = max(corners, key=lambda corner: corner.aspect_ratio)
    if worst_corner.aspect_ratio > limits.cell_aspect_ratio:
        warnings.append(
            f"Block {block.id}: cell aspect ratio {_num(worst_corner.aspect_ratio)} "
            f"at {worst_corner.vertex} exceeds {_num(limits.cell_aspect_ratio)} "
            f"(x width {_num(worst_corner.x_width)}, y width "
            f"{_num(worst_corner.y_width)})"
        )
    if growth > limits.cell_growth_ratio:
        worst_edge = max(
            edges,
            key=lambda current: max(
                model.edge_grading_values(current).cell_ratio,
                1.0 / model.edge_grading_values(current).cell_ratio,
            ),
        )
        warnings.append(
            f"Block {block.id}: cell-to-cell growth {_num(growth)} on edge "
            f"{edge_text(worst_edge)} exceeds {_num(limits.cell_growth_ratio)}"
        )

    return BlockQuality(
        block.id,
        (nx, ny, nz),
        area,
        tuple(corners),
        min_angle,
        max_angle,
        non_orthogonality,
        skewness,
        max_aspect,
        growth,
        min(sizes),
        max(sizes),
        tuple(warnings),
    )


def _first_cell_direction(
    model: MeshModel, directed: tuple[str, str]
) -> tuple[float, float]:
    """Return the vector from ``directed[0]`` to the first mesh node along it."""
    current = edge_key(*directed)
    cells = model.edge_cells[current]
    canonical_index = 1 if directed == current else cells - 1
    fraction = model.edge_node_fraction(current, canonical_index)
    start = model.vertices[directed[0]]
    point = model.edge_point(current, fraction)
    return point[0] - start.x, point[1] - start.y


def _angle_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    cross = first[0] * second[1] - first[1] * second[0]
    dot = first[0] * second[0] + first[1] * second[1]
    return math.degrees(math.atan2(abs(cross), dot))


def _polygon_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


# ---------------------------------------------------------------------------
# Interface metrics
# ---------------------------------------------------------------------------


def _interface_quality(
    model: MeshModel,
    current: EdgeKey,
    occurrences: list,
    limits: QualityThresholds,
) -> InterfaceQuality:
    (first_block, _, _), (second_block, _, _) = occurrences
    ratios: list[float] = []
    for vertex_id in current:
        first_width = model.edge_width_at_vertex(
            _transverse_edge(first_block, current, vertex_id), vertex_id
        )
        second_width = model.edge_width_at_vertex(
            _transverse_edge(second_block, current, vertex_id), vertex_id
        )
        low = min(first_width, second_width)
        high = max(first_width, second_width)
        ratios.append(math.inf if low <= 0.0 else high / low)
    max_ratio = max(ratios)
    warnings: tuple[str, ...] = ()
    if max_ratio > limits.interface_size_ratio:
        worst = current[ratios.index(max_ratio)]
        warnings = (
            f"Edge {edge_text(current)}: cell size jumps by {_num(max_ratio)} "
            f"between {first_block.id} and {second_block.id} at {worst} "
            f"(threshold {_num(limits.interface_size_ratio)})",
        )
    return InterfaceQuality(
        current,
        (first_block.id, second_block.id),
        (ratios[0], ratios[1]),
        max_ratio,
        warnings,
    )


def _transverse_edge(block: Block, shared: EdgeKey, vertex_id: str) -> EdgeKey:
    """Return the block edge that meets ``shared`` at ``vertex_id``."""
    for index in range(4):
        candidate = edge_key(*block.directed_edge(index))
        if candidate != shared and vertex_id in candidate:
            return candidate
    raise ValueError(  # pragma: no cover - blocks always have two edges per vertex
        f"Block {block.id} has no transverse edge at {vertex_id}"
    )


def _num(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return format(value, ".4g")
