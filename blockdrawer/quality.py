"""Per-block and per-interface mesh quality heuristics.

These metrics are computed from the topology, edge curves, and grading that
BlockDrawer stores, without running ``blockMesh``. They approximate what a
mesher would check by eye: corner angles of the first cell at every block
corner, cell aspect ratios, cell-to-cell growth, and cell size jumps across
shared edges. They are heuristics that point an operator or agent at the
block to fix; OpenFOAM's ``checkMesh`` remains the authority (see
``blockdrawer.checkmesh``).

With ``interior_cells`` the report also samples every block interior at its
real cell counts (:mod:`blockdrawer.sampling`, capped at that many cells a
side) and adds what the corners cannot see: folded cells, severe cells and
checkMesh's face non-orthogonality, with a short verdict a hand-built
session can be judged by.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .commands import edge_text
from .domain import Block, EdgeKey, TopologyError, edge_key
from .model import MeshModel, describe_hook, hook_culprit, hooked_spline_edges, transverse_edge
from .sampling import ASPECT_LIMIT, SEVERE_NON_ORTHOGONALITY, BlockSample, ModelSample, sample_model


# The interior sampling cap the CLI and the MCP server use by default: at
# 256 cells a side the 475k-cell 30P30N session is sampled in about a
# second and reproduces checkMesh's maximum non-orthogonality and severe
# face count exactly. The library default stays off for existing callers.
DEFAULT_INTERIOR_CELLS = 256
# Text output lists at most this many warnings unless asked for more.
DEFAULT_MAX_WARNINGS = 20

# Warning ranks: folded cells first, then angle defects by their distance
# from 90 degrees, then size defects by how far past the limit they are.
_FOLD, _ANGLE, _SIZE = 0, 1, 2


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
    interior: BlockSample | None = None
    # One (tier, magnitude) rank per warning, for the worst-first listing.
    warning_ranks: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class InterfaceQuality:
    """Cell size compatibility across one shared (internal) edge."""

    edge: EdgeKey
    blocks: tuple[str, str]
    size_ratios: tuple[float, float]
    max_size_ratio: float
    warnings: tuple[str, ...] = ()
    warning_ranks: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class QualityReport:
    blocks: tuple[BlockQuality, ...]
    interfaces: tuple[InterfaceQuality, ...]
    thresholds: QualityThresholds
    total_cells: int
    warnings: tuple[str, ...] = field(default=())
    interior: ModelSample | None = None
    export_error: str | None = None
    warning_ranks: tuple[tuple[int, float], ...] = ()
    # Per wall patch, the first cell heights normal to the wall at its band
    # spokes (:func:`wall_first_widths`).
    first_widths: tuple[dict[str, Any], ...] = ()
    # Spline edges with a segment running backwards along its chord
    # (``model.spline_hooks``), as ``A-B``.
    hooked_splines: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        if not self.blocks:
            return {"blocks": 0, "total_cells": 0, "warning_count": 0}
        data = {
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
            # How many internal edges carry a first-cell size jump above
            # each level: one worst number hides whether it is one edge or
            # a hundred.
            "interface_jumps": {
                "above_2_5": sum(1 for item in self.interfaces if item.max_size_ratio > 2.5),
                "above_5": sum(1 for item in self.interfaces if item.max_size_ratio > 5.0),
                "above_20": sum(1 for item in self.interfaces if item.max_size_ratio > 20.0),
            },
            "warning_count": len(self.warnings),
            "hooked_spline_edges": len(self.hooked_splines),
        }
        if self.interior is not None:
            data["interior"] = self.interior.summary()
        return data

    def verdict(self) -> dict[str, Any]:
        """The few numbers a hand-built session is judged by.

        ``valid`` means the topology validated and the dictionary exports
        (``None`` when the report was made without interior sampling, which
        is when the export is tried);
        ``untangled`` that no sampled cell folds, at the real counts when
        ``real_counts`` (no block was coarsened by the sampling cap). The
        non-orthogonality and the severe face count are checkMesh's
        measures on the sampled cells. Without interior sampling those
        entries are ``None``: the corner screen cannot decide them.
        """
        data: dict[str, Any] = {
            # The topology always validated (the report raises otherwise);
            # export is checked with the interior sampling.
            "valid": (self.export_error is None) if self.interior is not None else None,
            "export_error": self.export_error,
            "sampled": self.interior is not None,
            "untangled": None,
            "real_counts": None,
            "max_non_orthogonality": None,
            "worst_block": None,
            "worst_face": None,
            "severe_faces": None,
            "severe_threshold": SEVERE_NON_ORTHOGONALITY,
            "folded_cells": None,
            # checkMesh's face skewness; it fails a mesh above 4.
            "max_skewness": None,
            "worst_skew_block": None,
            # checkMesh's cell aspect ratio; it fails a mesh above 1000.
            "max_aspect_ratio": None,
            "worst_aspect_block": None,
            "high_aspect_cells": None,
            "aspect_limit": ASPECT_LIMIT,
        }
        if self.interior is not None and self.blocks:
            summary = self.interior.summary()
            data.update({
                "untangled": summary["folded_cells"] == 0,
                "real_counts": summary["coarsened_blocks"] == 0,
                "max_non_orthogonality": summary["max_non_orthogonality"],
                "worst_block": summary["worst_block"],
                "worst_face": summary["worst_face"],
                "severe_faces": summary["severe_faces"],
                "folded_cells": summary["folded_cells"],
                "max_skewness": summary["max_skewness"],
                "worst_skew_block": summary["worst_skew_block"],
                "max_aspect_ratio": summary["max_aspect_ratio"],
                "worst_aspect_block": summary["worst_aspect_block"],
                "high_aspect_cells": summary["high_aspect_cells"],
            })
        return data

    def worst_blocks(self, count: int) -> tuple[BlockQuality, ...]:
        """The ``count`` worst blocks, worst first.

        With interior sampling a block ranks by its folded cells, then its
        sampled face non-orthogonality; otherwise by the corner screen's
        non-orthogonality.
        """
        def key(block: BlockQuality) -> tuple:
            if block.interior is not None:
                return (
                    -block.interior.folded_cells,
                    -block.interior.max_non_orthogonality,
                    -block.non_orthogonality,
                    block.block_id,
                )
            return (-block.non_orthogonality, -block.equiangle_skewness, block.block_id)

        ordered = sorted(self.blocks, key=key)
        return tuple(ordered[: max(0, count)])

    def ranked_warnings(self, count: int | None = None) -> tuple[list[str], int]:
        """Warnings worst first - folded cells, then angle defects by their
        distance from 90 degrees, then size defects by how far past their
        limit - capped at ``count``, and how many were left out."""
        ranks = self.warning_ranks or tuple((_SIZE, 0.0) for _ in self.warnings)
        order = sorted(
            range(len(self.warnings)), key=lambda index: (ranks[index][0], -ranks[index][1], index)
        )
        if count is not None:
            order = order[: max(0, count)]
        return [self.warnings[index] for index in order], len(self.warnings) - len(order)

    def worst_interfaces(self, count: int) -> tuple[InterfaceQuality, ...]:
        """The ``count`` internal edges with the largest size jump, worst first."""
        ordered = sorted(
            self.interfaces, key=lambda interface: (-interface.max_size_ratio, interface.blocks)
        )
        return tuple(ordered[: max(0, count)])

    def to_data(
        self,
        *,
        summary_only: bool = False,
        worst: int | None = None,
        max_warnings: int | None = None,
    ) -> dict[str, Any]:
        """Structured report; ``summary_only`` drops the tables, ``worst`` keeps
        only the N worst blocks and internal edges (the summary stays whole),
        ``max_warnings`` lists only the N worst warnings, worst first, and
        counts the rest in ``warnings_omitted``."""
        blocks = self.blocks if worst is None else self.worst_blocks(worst)
        interfaces = self.interfaces if worst is None else self.worst_interfaces(worst)
        if max_warnings is None:
            warnings, omitted = list(self.warnings), 0
        else:
            warnings, omitted = self.ranked_warnings(max_warnings)
        data: dict[str, Any] = {
            "summary": self.summary(),
            "verdict": self.verdict(),
            "thresholds": asdict(self.thresholds),
            "warnings": warnings,
            "first_widths": [dict(item) for item in self.first_widths],
        }
        if max_warnings is not None:
            data["warnings_omitted"] = omitted
        if summary_only:
            return data
        data["blocks"] = [
                {
                    **{
                        key: getattr(block, key) for key in _BLOCK_FIELDS
                    },
                    "cells": list(block.cells),
                    "warnings": list(block.warnings),
                    "corners": [
                        {**asdict(corner), "aspect_ratio": corner.aspect_ratio}
                        for corner in block.corners
                    ],
                    **(
                        {"interior": block.interior.to_data()}
                        if block.interior is not None else {}
                    ),
                }
                for block in blocks
            ]
        data["interfaces"] = [
                {
                    "edge": edge_text(interface.edge),
                    "blocks": list(interface.blocks),
                    "size_ratios": list(interface.size_ratios),
                    "max_size_ratio": interface.max_size_ratio,
                    "warnings": list(interface.warnings),
                }
                for interface in interfaces
            ]
        if worst is not None:
            data["listed"] = {"blocks": len(blocks), "interfaces": len(interfaces), "worst": int(worst)}
        return data


_BLOCK_FIELDS = (
    "block_id", "cells", "area", "min_angle", "max_angle", "non_orthogonality",
    "equiangle_skewness", "max_cell_aspect_ratio", "max_cell_growth_ratio",
    "min_cell_size", "max_cell_size", "warnings",
)


def assess_quality(
    model: MeshModel,
    thresholds: QualityThresholds | None = None,
    *,
    interior_cells: int = 0,
) -> QualityReport:
    """Compute block and interface quality metrics for the whole model.

    ``interior_cells`` samples every block's interior at its real counts,
    coarsened above that many cells a side, adds the sampled measures and
    their warnings, and checks that the dictionary exports; 0 (the default)
    keeps the corner screen alone.
    """
    limits = thresholds or QualityThresholds()
    model.validate()
    interior: ModelSample | None = None
    samples: dict[str, BlockSample] = {}
    export_error: str | None = None
    if interior_cells and interior_cells > 0:
        interior = sample_model(model, interior_cells)
        samples = {sample.block_id: sample for sample in interior.blocks}
        from .foam import block_mesh_dict

        try:
            block_mesh_dict(model)
        except (TopologyError, ValueError) as exc:
            export_error = str(exc)
    blocks = tuple(
        _block_quality(model, block, limits, samples.get(block.id)) for block in model.blocks
    )
    interfaces = tuple(
        _interface_quality(model, current, occurrences, limits)
        for current, occurrences in model.edge_occurrences().items()
        if len(occurrences) == 2
    )
    warnings: list[str] = []
    ranks: list[tuple[int, float]] = []
    for item in (*blocks, *interfaces):
        warnings.extend(item.warnings)
        ranks.extend(item.warning_ranks)
    hooked = hooked_spline_edges(model)
    for current, segments, lengths in hooked:
        # A spline that reverses along its chord turns through 180 degrees:
        # ranked with the worst angle defects (90 from 90).
        warnings.append(
            f"spline edge {describe_hook(current, segments, lengths)} runs backwards along its "
            "chord: cells graded into the loop fold; remove that control point "
            f"(remove_control_point {'-'.join(current)} {hook_culprit(lengths, segments[0])}) or "
            "make the edge a line"
        )
        ranks.append((_ANGLE, 90.0))
    total_cells = sum(
        nx * ny * nz
        for nx, ny, nz in (model.block_cell_counts(block) for block in model.blocks)
    )
    return QualityReport(
        blocks, interfaces, limits, total_cells, tuple(warnings),
        interior, export_error, tuple(ranks), tuple(wall_first_widths(model)),
        tuple("-".join(current) for current, _segments, _lengths in hooked),
    )


def block_quality(
    model: MeshModel, block_id: str, thresholds: QualityThresholds | None = None
) -> BlockQuality:
    """The quality of one block, by ID, without assessing the whole model."""
    block = next((item for item in model.blocks if item.id == block_id), None)
    if block is None:
        raise TopologyError(f"Unknown block {block_id!r}")
    return _block_quality(model, block, thresholds or QualityThresholds())


def format_quality(
    report: QualityReport,
    *,
    summary_only: bool = False,
    worst: int | None = None,
    max_warnings: int | None = DEFAULT_MAX_WARNINGS,
) -> str:
    """Render a quality report as compact terminal text.

    ``summary_only`` prints the headline lines, warnings and thresholds without
    the per-block and per-edge tables; ``worst`` limits the tables to the N
    worst blocks (by sampled, else corner, non-orthogonality) and internal
    edges (by size jump); ``max_warnings`` lists only the N worst warnings
    (``None`` lists them all).
    """
    summary = report.summary()
    blocks = report.blocks if worst is None else report.worst_blocks(worst)
    interfaces = report.interfaces if worst is None else report.worst_interfaces(worst)
    if summary_only:
        blocks = ()
        interfaces = ()
    lines = [
        f"Quality: {summary['blocks']} block(s), {summary['total_cells']} cells, "
        f"{summary['warning_count']} warning(s)",
    ]
    verdict = report.verdict()
    if verdict["sampled"] and report.blocks:
        interior = summary["interior"]
        state = (
            "valid and exports" if verdict["valid"]
            else f"does NOT export: {verdict['export_error']}"
        )
        tangle = (
            "untangled" if verdict["untangled"]
            else f"TANGLED ({verdict['folded_cells']} folded cell(s))"
        )
        where = (
            "real counts" if verdict["real_counts"]
            else f"at most {interior['max_cells_per_side']} cells a side, "
            f"{interior['coarsened_blocks']} block(s) coarsened"
        )
        worst_face = verdict["worst_face"]
        lines.append(
            f"Verdict: {state}; {tangle} ({where}); max non-orthogonality "
            f"{_num(verdict['max_non_orthogonality'])}° in {verdict['worst_block']}"
            + (f" at ({_num(worst_face[0])}, {_num(worst_face[1])})" if worst_face else "")
            + f"; {verdict['severe_faces']} face(s) above "
            f"{_num(verdict['severe_threshold'])}°; max skewness {_num(verdict['max_skewness'])}"
            f" in {verdict['worst_skew_block']}"
            + (" (checkMesh fails above 4)" if verdict["max_skewness"] > 4.0 else "")
            + f"; max aspect ratio {_num(verdict['max_aspect_ratio'])} in {verdict['worst_aspect_block']}"
            + (
                f" ({verdict['high_aspect_cells']} cell(s) above {_num(ASPECT_LIMIT)}: checkMesh fails)"
                if verdict["high_aspect_cells"] else ""
            )
        )
        lines.append(
            f"  interior (sampled, checkMesh's face measure): cell angles "
            f"{_num(interior['min_angle'])}° .. {_num(interior['max_angle'])}°, "
            f"{interior['severe_cells']} severe cell(s), {interior['folded_cells']} "
            f"folded cell(s) in {interior['folded_blocks']} block(s)"
        )
    elif report.blocks:
        lines.append(
            "Verdict: corner screen only (interior not sampled); checkMesh decides"
        )
    if report.blocks:
        # The corner screen measures the first cells at the block corners,
        # not checkMesh's quantities: labelled so that its aspect ratio
        # (1105 beside a verdict of 893 on a round-3 trial) is not read as a
        # checkMesh failure.
        lines.append(
            f"  corner screen (first cells at block corners, not checkMesh's measures): "
            f"angles {_num(summary['min_angle'])}° .. "
            f"{_num(summary['max_angle'])}°, max non-orthogonality "
            f"{_num(summary['max_non_orthogonality'])}°, max equiangle skewness "
            f"{_num(summary['max_equiangle_skewness'])}"
        )
        lines.append(
            f"  corner screen: cell sizes {_num(summary['min_cell_size'])} .. "
            f"{_num(summary['max_cell_size'])}, max corner-cell aspect ratio "
            f"{_num(summary['max_cell_aspect_ratio'])}, max cell growth ratio "
            f"{_num(summary['max_cell_growth_ratio'])}, max size jump across "
            f"internal edges {_num(summary['max_interface_size_ratio'])}"
        )
    if report.first_widths:
        lines.append("  first cell height normal to the wall at the band spokes:")
        lines.extend("  " + line for line in format_first_widths(list(report.first_widths)))
    if blocks:
        sampled = verdict["sampled"]
        lines.append("")
        lines.append(
            "Blocks (id | cells | angles min/max | non-orth | skew | "
            "aspect | growth | cell size min..max"
            + (" | interior non-orth, severe faces, folded" if sampled else "")
            + ")"
            + (f", the {len(blocks)} worst of {len(report.blocks)}:" if worst is not None else ":")
        )
        for block in blocks:
            nx, ny, nz = block.cells
            flag = " !" if block.warnings else ""
            extra = ""
            if block.interior is not None:
                extra = (
                    f" | {_num(block.interior.max_non_orthogonality)}, "
                    f"{block.interior.severe_faces}, {block.interior.folded_cells}"
                )
            lines.append(
                f"  {block.block_id} | {nx} {ny} {nz} | "
                f"{_num(block.min_angle)}/{_num(block.max_angle)} | "
                f"{_num(block.non_orthogonality)} | "
                f"{_num(block.equiangle_skewness)} | "
                f"{_num(block.max_cell_aspect_ratio)} | "
                f"{_num(block.max_cell_growth_ratio)} | "
                f"{_num(block.min_cell_size)}..{_num(block.max_cell_size)}{extra}{flag}"
            )
    if interfaces:
        lines.append("")
        lines.append(
            "Internal edges (edge | blocks | size jump at each end)"
            + (f", the {len(interfaces)} worst of {len(report.interfaces)}:" if worst is not None else ":")
        )
        for interface in interfaces:
            flag = " !" if interface.warnings else ""
            lines.append(
                f"  {edge_text(interface.edge)} | {','.join(interface.blocks)} | "
                f"{_num(interface.size_ratios[0])}, "
                f"{_num(interface.size_ratios[1])}{flag}"
            )
    lines.append("")
    if report.warnings:
        if max_warnings is None:
            shown, omitted = list(report.warnings), 0
            lines.append("Warnings:")
        else:
            shown, omitted = report.ranked_warnings(max_warnings)
            lines.append(
                "Warnings:" if not omitted
                else f"Warnings, the {len(shown)} worst of {len(report.warnings)}:"
            )
        lines.extend(f"  - {warning}" for warning in shown)
        if omitted:
            lines.append(f"  ... {omitted} more (pass a larger warning limit to list them)")
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
# First cell heights at the walls
# ---------------------------------------------------------------------------

# A band spoke leaves its wall vertex within this many degrees of the inward
# normal of a wall edge there. Excluded by it: a wake line leaving a sharp
# trailing edge (it runs along the edge's bisector, near 90 degrees from both
# walls' normals), an edge running on along the wall's line past its last
# vertex (90 degrees), and edges leaning further than this from the normal,
# along which no first cell height is measured. The bisector spoke of a
# right-angled convex corner is at exactly this angle and is taken; its
# height is cos 45 of its width along the edge.
SPOKE_CONE = 45.0
# The tangent of an edge at its end is taken over this fraction of it.
_TANGENT_FRACTION = 1.0e-4


def band_spokes(
    model: MeshModel,
) -> tuple[list[tuple[EdgeKey, str, str, tuple[float, float]]], dict[str, int]]:
    """The band spokes of every wall patch, as :func:`wall_first_widths`
    reads them: ``(edge, wall vertex, patch, inward unit normal)`` for each
    end of an edge on no wall patch at a wall vertex within
    :data:`SPOKE_CONE` of the inward normal of a wall edge there (the
    closest), and per patch the edge ends at its vertices outside the cone."""
    walls = {name for name, boundary in model.boundaries.items() if boundary.kind == "wall"}
    occurrences = model.edge_occurrences()
    # Every wall vertex: its wall edges as (patch, tangent, inward normal).
    normals: dict[str, list[tuple[str, tuple[float, float], tuple[float, float]]]] = {}
    for current, name in model.edge_boundaries.items():
        if name not in walls or current not in occurrences:
            continue
        _block, _index, directed = occurrences[current][0]
        for vertex in current:
            tx, ty = _tangent(model, current, vertex)
            # The block lies left of its counter-clockwise traversal; the
            # tangent leaving ``vertex`` runs with it from the traversal's
            # start and against it from its end.
            normal = (-ty, tx) if vertex == directed[0] else (ty, -tx)
            normals.setdefault(vertex, []).append((name, (tx, ty), normal))
    found: list[tuple[EdgeKey, str, str, tuple[float, float]]] = []
    others: dict[str, int] = {}
    for current in model.edges():
        if model.edge_boundaries.get(current) in walls:
            continue
        for vertex in current:
            if vertex not in normals:
                continue
            dx, dy = _tangent(model, current, vertex)
            best = None
            for name, _tangent_vector, normal in normals[vertex]:
                angle = math.degrees(math.acos(max(-1.0, min(1.0, dx * normal[0] + dy * normal[1]))))
                if best is None or angle < best[0]:
                    best = (angle, name, normal)
            angle, name, normal = best
            # Inclusive, so the bisector of a right-angled convex corner
            # (45 degrees from both walls' normals) is a spoke of both.
            if angle > SPOKE_CONE + 1.0e-6:
                for patch in {item[0] for item in normals[vertex]}:
                    others[patch] = others.get(patch, 0) + 1
                continue
            found.append((current, vertex, name, normal))
    return found, others


def spoke_height(model: MeshModel, current: EdgeKey, vertex: str, normal: tuple[float, float]) -> float:
    """The height of a spoke's first mesh node from ``vertex`` above the
    wall's tangent line there (``normal`` the wall's inward unit normal)."""
    cells = model.edge_cells[current]
    index = 1 if vertex == current[0] else cells - 1
    px, py = model.edge_point(current, model.edge_node_fraction(current, index))
    origin = model.vertices[vertex]
    return (px - origin.x) * normal[0] + (py - origin.y) * normal[1]


def wall_first_widths(
    model: MeshModel, asked: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Per wall patch: the first cell height normal to the wall at its spokes.

    At every vertex of a wall patch each edge that is on no wall patch is a
    candidate spoke. It is a band spoke of the wall edge at that vertex whose
    inward normal (towards the edge's block, at the vertex, curved edges by
    their end tangent) is closest to the spoke's leaving direction, when that
    angle is at most :data:`SPOKE_CONE`; the spoke is reported under that
    wall edge's patch (:func:`band_spokes`). Its first width is the height of
    its first mesh node above the wall edge's tangent line at the vertex: the
    first cell height normal to the wall, not the width along the edge. An
    edge with both ends on a wall is measured from each end.

    Each entry gives ``patch``, ``spokes``, ``cells`` (fewest and most on
    the spokes), ``first_width`` (smallest and largest height),
    ``first_width_edges`` (the spokes with those two heights), ``other_edges``
    (non-wall edges at the patch's vertices that are no spoke by the cone
    rule) and, when ``asked`` names the patch, ``asked``.
    """
    found, others = band_spokes(model)
    spokes: dict[str, list[tuple[float, str, int]]] = {}
    for current, vertex, name, normal in found:
        height = spoke_height(model, current, vertex, normal)
        spokes.setdefault(name, []).append((height, edge_text(current), model.edge_cells[current]))
    result = []
    for name in sorted(set(spokes) | set(others)):
        items = spokes.get(name, [])
        entry: dict[str, Any] = {"patch": name, "spokes": len(items)}
        if items:
            low = min(items)
            high = max(items)
            entry.update({
                "cells": [min(item[2] for item in items), max(item[2] for item in items)],
                "first_width": [low[0], high[0]],
                "first_width_edges": [low[1], high[1]],
            })
        entry["other_edges"] = others.get(name, 0)
        if asked and name in asked:
            entry["asked"] = asked[name]
        result.append(entry)
    return result


def _tangent(model: MeshModel, current: EdgeKey, vertex: str) -> tuple[float, float]:
    """The unit tangent of an edge leaving ``vertex``."""
    fraction = _TANGENT_FRACTION if vertex == current[0] else 1.0 - _TANGENT_FRACTION
    x, y = model.edge_point(current, fraction)
    origin = model.vertices[vertex]
    dx, dy = x - origin.x, y - origin.y
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 0.0 else (0.0, 0.0)


def format_first_widths(widths: list[dict[str, Any]]) -> list[str]:
    """One text line per wall patch for :func:`wall_first_widths`."""
    lines = []
    for item in widths:
        if not item["spokes"]:
            lines.append(f"  {item['patch']}: no band spoke ({item['other_edges']} other edge(s))")
            continue
        low, high = item["first_width"]
        text = f"  {item['patch']}: {_num(low)}..{_num(high)}"
        asked = item.get("asked")
        if asked:
            text += f" for {_num(asked)} ({_percent(low, asked)}..{_percent(high, asked)})"
        text += (
            f" at {item['first_width_edges'][0]}..{item['first_width_edges'][1]}, "
            f"{item['spokes']} spoke(s), {item['other_edges']} other edge(s)"
        )
        lines.append(text)
    return lines


def _percent(value: float, asked: float) -> str:
    return f"{int(round(100.0 * (value / asked - 1.0))):+d}%"


# ---------------------------------------------------------------------------
# Block metrics
# ---------------------------------------------------------------------------


def _block_quality(
    model: MeshModel,
    block: Block,
    limits: QualityThresholds,
    interior: BlockSample | None = None,
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
    ranks: list[tuple[int, float]] = []
    for corner in corners:
        if corner.angle < limits.min_angle or corner.angle > limits.max_angle:
            warnings.append(
                f"Block {block.id}: corner angle {_num(corner.angle)}° at "
                f"{corner.vertex} is outside {_num(limits.min_angle)}..."
                f"{_num(limits.max_angle)}°"
            )
            ranks.append((_ANGLE, abs(90.0 - corner.angle)))
    if non_orthogonality > limits.non_orthogonality and not any(
        "corner angle" in warning for warning in warnings
    ):
        warnings.append(
            f"Block {block.id}: non-orthogonality {_num(non_orthogonality)}° "
            f"exceeds {_num(limits.non_orthogonality)}°"
        )
        ranks.append((_ANGLE, non_orthogonality))
    worst_corner = max(corners, key=lambda corner: corner.aspect_ratio)
    if worst_corner.aspect_ratio > limits.cell_aspect_ratio:
        ranks.append((_SIZE, worst_corner.aspect_ratio / limits.cell_aspect_ratio))
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
        ranks.append((_SIZE, growth / limits.cell_growth_ratio))
    if interior is not None:
        if interior.folded_cells:
            at = interior.folded_at
            warnings.append(
                f"Block {block.id}: {interior.folded_cells} sampled interior cell(s) "
                "fold between the corners"
                + (f", the first at ({_num(at[0])}, {_num(at[1])})" if at else "")
            )
            ranks.append((_FOLD, float(interior.folded_cells)))
        if interior.max_non_orthogonality > limits.non_orthogonality:
            at = interior.worst_face
            warnings.append(
                f"Block {block.id}: sampled interior non-orthogonality "
                f"{_num(interior.max_non_orthogonality)}° exceeds "
                f"{_num(limits.non_orthogonality)}° ({interior.severe_faces} face(s) "
                f"above {_num(SEVERE_NON_ORTHOGONALITY)}°)"
                + (f" at ({_num(at[0])}, {_num(at[1])})" if at else "")
            )
            ranks.append((_ANGLE, interior.max_non_orthogonality))
        if interior.high_aspect_cells:
            at = interior.worst_aspect_cell
            warnings.append(
                f"Block {block.id}: sampled cell aspect ratio "
                f"{_num(interior.max_aspect_ratio)} exceeds checkMesh's {_num(ASPECT_LIMIT)} "
                f"({interior.high_aspect_cells} cell(s))"
                + (f" at ({_num(at[0])}, {_num(at[1])})" if at else "")
            )
            ranks.append((_SIZE, interior.max_aspect_ratio / limits.cell_aspect_ratio))

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
        interior,
        tuple(ranks),
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
    ranks: tuple[tuple[int, float], ...] = ()
    if max_ratio > limits.interface_size_ratio:
        worst = current[ratios.index(max_ratio)]
        warnings = (
            f"Edge {edge_text(current)}: cell size jumps by {_num(max_ratio)} "
            f"between {first_block.id} and {second_block.id} at {worst} "
            f"(threshold {_num(limits.interface_size_ratio)})",
        )
        ranks = ((_SIZE, max_ratio / limits.interface_size_ratio),)
    return InterfaceQuality(
        current,
        (first_block.id, second_block.id),
        (ratios[0], ratios[1]),
        max_ratio,
        warnings,
        ranks,
    )


# The edge of a block that meets a shared edge at a vertex: one side of an
# interface pair (:meth:`MeshModel.interface_pairs`).
_transverse_edge = transverse_edge


def _num(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return format(value, ".4g")
