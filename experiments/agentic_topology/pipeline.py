"""End-to-end construction: point lists in, block topology and report out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import block_complex
import block_layout
import geometry2d as g2
import patch_solver
import session_emit
import sites as site_module
import voronoi_graph


@dataclass(frozen=True)
class PipelineOptions:
    grid_width: int = 700
    farfield_scale: float = 3.0
    farfield_shape: str = "circle"
    farfield_name: str = "farfield"
    cells: int = 10
    wall_edge_style: str = "polyLine"
    coverage_samples: int = 400
    reference_curves: bool = True
    layout: block_layout.LayoutOptions = field(
        default_factory=block_layout.LayoutOptions
    )
    solver: patch_solver.SolverOptions = field(
        default_factory=patch_solver.SolverOptions
    )


@dataclass
class PipelineResult:
    options: PipelineOptions
    sites: list
    scale: float
    diagram: voronoi_graph.Diagram | None
    layout: block_layout.Layout | None
    complex: block_complex.Complex | None
    solve: patch_solver.SolveResult
    reports: list
    coverage: dict | None
    manifold: list
    model: object | None
    analysis: dict
    errors: list = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return (
            not self.errors
            and self.solve.converged
            and self.model is not None
            and not self.manifold
            and self.coverage is not None
            and self.coverage["uncovered_samples"] == 0
            and self.coverage["overlapping_samples"] == 0
            and self.coverage["outside_samples"] == 0
        )


def run(names, loops, options: PipelineOptions | None = None) -> PipelineResult:
    """Run every stage, keeping whatever completed when one of them fails.

    A construction failure is a research result, so the stages are guarded and
    the report is built from what exists.  Nothing partial is emitted as a
    session.
    """
    settings = options or PipelineOptions()
    errors: list[dict] = []
    sites, scale = site_module.build_sites(
        names,
        loops,
        farfield_shape=settings.farfield_shape,
        farfield_scale=settings.farfield_scale,
        farfield_name=settings.farfield_name,
    )
    diagram = None
    layout = None
    complex_ = None
    reports: list = []
    coverage = None
    manifold: list = []
    model = None
    model_error = None
    solution = patch_solver.SolveResult()
    try:
        diagram = voronoi_graph.build_diagram(
            sites, scale=scale, grid_width=settings.grid_width
        )
    except Exception as error:
        errors.append({"stage": "voronoi_graph", "error": _describe(error)})
    if diagram is not None:
        try:
            layout = block_layout.build_layout(diagram, settings.layout)
        except Exception as error:
            errors.append({"stage": "layout", "error": _describe(error)})
    if layout is not None:
        try:
            solution = patch_solver.solve(layout, settings.solver)
            complex_ = block_complex.build_complex(
                layout, wall_edge_style=settings.wall_edge_style
            )
        except Exception as error:
            errors.append({"stage": "patch_solver", "error": _describe(error)})
    if complex_ is not None:
        reports = block_complex.block_reports(complex_)
        manifold = block_complex.check_manifold(complex_)
        coverage = block_complex.coverage_check(
            layout, complex_, samples=settings.coverage_samples
        )
        covered = (
            coverage["uncovered_samples"] == 0
            and coverage["overlapping_samples"] == 0
            and coverage["outside_samples"] == 0
        )
        if solution.converged and not manifold and covered:
            try:
                model = session_emit.build_model(
                    complex_,
                    diagram,
                    cells=settings.cells,
                    reference_curves=settings.reference_curves,
                )
            except Exception as error:  # pragma: no cover - reported, not raised
                model_error = _describe(error)
    analysis = build_analysis(
        settings,
        sites,
        scale,
        diagram,
        layout,
        complex_,
        solution,
        reports,
        coverage,
        manifold,
        model,
        model_error,
        errors,
    )
    return PipelineResult(
        settings,
        sites,
        scale,
        diagram,
        layout,
        complex_,
        solution,
        reports,
        coverage,
        manifold,
        model,
        analysis,
        errors,
    )


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _point(value) -> list[float]:
    return [float(value[0]), float(value[1])]


def singularities(complex_, identifiers=None) -> list[dict]:
    """Complex vertices whose block valence is not four."""
    incident: dict[tuple, int] = {}
    valences: dict[tuple, int] = {}
    boundaries: dict[tuple, bool] = {}
    for block in complex_.blocks:
        for corner in block.corners:
            incident[corner] = incident.get(corner, 0) + 1
    for key, edge in complex_.edges.items():
        for corner in key:
            valences[corner] = valences.get(corner, 0) + 1
            boundaries[corner] = boundaries.get(corner, False) or (
                edge.boundary is not None
            )
    result = []
    for key, vertex in complex_.vertices.items():
        blocks = incident.get(key, 0)
        valence = valences.get(key, 0)
        boundary = boundaries.get(key, False)
        regular = 2 if boundary else 4
        if blocks == regular:
            continue
        record = {
            "vertex": list(key),
            "kind": vertex.kind,
            "point": _point(vertex.point),
            "incident_blocks": blocks,
            "edge_valence": valence,
            "on_boundary": boundary,
            "regular_block_count": regular,
        }
        if identifiers is not None:
            record["session_vertex"] = identifiers.get(key)
        result.append(record)
    return result


def build_analysis(
    settings,
    sites,
    scale,
    diagram,
    layout,
    complex_,
    solution,
    reports,
    coverage,
    manifold,
    model,
    model_error,
    errors=(),
) -> dict:
    identifiers = (
        {key: f"v{index}" for index, key in enumerate(complex_.vertices)}
        if complex_ is not None
        else {}
    )
    gaps = []
    walls = [site for site in sites if site.curve.kind == "wall"]
    for first in range(len(walls)):
        for second in range(first + 1, len(walls)):
            gaps.append(
                {
                    "curves": [walls[first].name, walls[second].name],
                    "distance": g2.minimum_separation(
                        walls[first].curve.loop(), walls[second].curve.loop()
                    ),
                }
            )
    analysis: dict = {
        "algorithm": (
            "exact generalized Voronoi graph, annular cell decomposition, "
            "relaxed conformal quadrilateral patches"
        ),
        "domain": {
            "scale": scale,
            "farfield_shape": settings.farfield_shape,
            "farfield_scale": settings.farfield_scale,
            "farfield_length": sites[-1].curve.length(),
        },
        "stage_errors": list(errors),
        "raster": {
            "width": diagram.raster.width,
            "height": diagram.raster.height,
            "pixel": diagram.raster.pixel,
            "bounds": list(diagram.raster.bounds),
            "note": (
                "the raster only decides connectivity; junctions and branches "
                "are recomputed analytically"
            ),
        }
        if diagram is not None
        else None,
        "curves": [
            {
                "name": site.name,
                "kind": site.curve.kind,
                "length": site.curve.length(),
                "point_count": len(site.curve.loop()) - 1,
            }
            for site in sites
        ],
        "minimum_gaps": gaps,
    }
    analysis.update(_graph_section(sites, diagram))
    analysis.update(_layout_section(sites, layout))
    analysis["objective"] = {
        "terms": [
            "inverted-cell barrier and minimum corner angle through the "
            "scaled Jacobian",
            "aspect-ratio penalty above a dimensionless limit",
            "spoke direction against the wall normal into the fluid",
            "spoke length against the local clearance",
        ],
        "target_scaled_jacobian": settings.solver.target_quality,
        "accepted_scaled_jacobian": settings.solver.accept_quality,
        "aspect_limit": settings.solver.aspect_limit,
        "aspect_weight": settings.solver.aspect_weight,
        "spoke_cosine_limit": settings.solver.spoke_cosine,
        "spoke_weight": settings.solver.spoke_weight,
        "alignment_weight": settings.solver.alignment_weight,
    }
    analysis["solver"] = {
        "converged": solution.converged,
        "worst_scaled_jacobian": solution.worst_quality,
        "relaxation_sweeps": solution.sweeps,
        "anchor_splits": solution.splits,
        "candidate_graphs": solution.history,
        "failures": solution.failures,
    }
    analysis.update(
        _block_section(complex_, reports, identifiers, coverage, manifold, scale)
    )
    if model is not None:
        analysis["session"] = {
            "topology_signature": session_emit.topology_signature(model),
            "quality": session_emit.quality_summary(model),
            "boundaries": sorted(model.boundaries),
            "edge_cells": settings.cells,
        }
    else:
        analysis["session"] = None
        analysis["session_error"] = model_error
    analysis["limitations"] = [
        "One block per patch and uniform cell counts; boundary-layer grading "
        "is out of scope for this prototype.",
        "Sites are complete boundary components, so a body's own medial "
        "branches are handled by patch splitting rather than by the graph.",
        "Spokes are straight chords between a wall gate and its ring anchor.",
    ]
    return analysis


def _graph_section(sites, diagram) -> dict:
    if diagram is None:
        return {"graph": None}
    return {
        "graph": {
            "junction_count": len(diagram.junctions),
            "branch_count": len(diagram.branches),
            "junctions": [
                {
                    "index": junction.index,
                    "point": _point(junction.point),
                    "sites": [sites[index].name for index in junction.sites],
                    "degree": junction.degree,
                    "clearance": junction.clearance,
                    "equidistance_residual": junction.residual,
                    "raster_pixels": junction.pixel_count,
                }
                for junction in diagram.junctions
            ],
            "branches": [
                {
                    "index": branch.index,
                    "sites": [sites[index].name for index in branch.pair],
                    "length": branch.length,
                    "closed": branch.closed,
                    "ends": [list(end) if end else None for end in branch.ends],
                    "trace_steps": branch.steps,
                }
                for branch in diagram.branches
            ],
            "notes": diagram.notes,
        }
    }


def _layout_section(sites, layout) -> dict:
    if layout is None:
        return {"cells": None, "anchors": None}
    return {
        "cells": [
            {
                "site": sites[cell.site].name,
                "ring_length": cell.ring_length,
                "branches": [step.branch for step in cell.steps],
                "cuts": len(layout.cuts[index]),
            }
            for index, cell in enumerate(layout.cells)
        ],
        "anchors": {
            "accepted": [
                {
                    "branch": anchor.branch,
                    "position": anchor.position,
                    "kind": anchor.kind,
                    "junction": anchor.junction,
                    "identity": list(anchor.key),
                }
                for anchors in layout.anchors.by_branch.values()
                for anchor in anchors
            ],
            "rejected": [
                {
                    "branch": anchor.branch,
                    "position": anchor.position,
                    "kind": anchor.kind,
                    "reason": reason,
                }
                for anchor, reason in layout.anchors.rejected
            ],
        },
    }


def _block_section(complex_, reports, identifiers, coverage, manifold, scale) -> dict:
    if complex_ is None:
        return {
            "blocks": None,
            "singularities": None,
            "quality": None,
            "coverage": coverage,
            "manifold_problems": manifold,
        }
    separation = block_complex.minimum_vertex_separation(complex_)
    return {
        "blocks": [
            {
                "index": report.patch,
                "session_block": f"b{report.patch}",
                "site": report.site,
                "vertices": [
                    identifiers[key] for key in complex_.blocks[report.patch].corners
                ],
                "convex": report.convex,
                "minimum_corner_angle_degrees": math.degrees(report.minimum_angle),
                "maximum_corner_angle_degrees": math.degrees(report.maximum_angle),
                "scaled_jacobian": report.scaled_jacobian,
                "aspect_ratio": report.aspect_ratio,
                "area": report.area,
            }
            for report in reports
        ],
        "singularities": singularities(complex_, identifiers),
        "quality": {
            "block_count": len(complex_.blocks),
            "vertex_count": len(complex_.vertices),
            "edge_count": len(complex_.edges),
            "minimum_scaled_jacobian": min(
                (report.scaled_jacobian for report in reports), default=None
            ),
            "minimum_corner_angle_degrees": (
                math.degrees(min(report.minimum_angle for report in reports))
                if reports
                else None
            ),
            "maximum_corner_angle_degrees": (
                math.degrees(max(report.maximum_angle for report in reports))
                if reports
                else None
            ),
            "maximum_aspect_ratio": max(
                (report.aspect_ratio for report in reports), default=None
            ),
            "minimum_vertex_separation": separation,
            "minimum_vertex_separation_over_scale": separation / scale,
        },
        "coverage": coverage,
        "manifold_problems": manifold,
    }
