"""End-to-end construction for both domain families.

Two producers write into the same general patch graph:

* **external** - the exact generalized medial graph relaxes an annular layout
  around any number of disjoint bodies, then every wall chain is peeled off
  into a clearance-limited boundary-layer band and the core patches start at
  the band front;
* **internal** - a four-sided reading of a simply connected domain gives a
  monotone guide correspondence, wall bands along the guides and a swept H-grid
  core between the fronts.

Everything after that point is shared: patch-graph validation, an independent
coverage test, metric cell-count quantisation, boundary-layer grading, session
emission, and quality measured on the actual sampled transfinite grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import block_layout
import external_topology
import fan_cavity
import geometry2d as g2
import grid_quality
import layers as layer_module
import patch_graph as pg
import patch_solver
import planar_domain as pdm
import session_emit
import sites as site_module
import sizing
import sweep as sweep_module
import voronoi_graph


@dataclass(frozen=True)
class PipelineOptions:
    grid_width: int = 700
    farfield_scale: float = 3.0
    farfield_shape: str = "circle"
    farfield_name: str = "farfield"
    wall_edge_style: str = "polyLine"
    coverage_samples: int = 400
    reference_curves: bool = True
    band_repairs: int = 2
    evaluate_grid: bool = True
    check_edge_crossings: bool = True
    forced_splits: tuple = ()
    layout: block_layout.LayoutOptions = field(
        default_factory=block_layout.LayoutOptions
    )
    solver: patch_solver.SolverOptions = field(
        default_factory=patch_solver.SolverOptions
    )
    layer: layer_module.LayerOptions = field(
        default_factory=layer_module.LayerOptions
    )
    sizing: sizing.SizingOptions = field(default_factory=sizing.SizingOptions)
    sweep: sweep_module.SweepOptions = field(
        default_factory=sweep_module.SweepOptions
    )
    cavity: fan_cavity.CavityOptions = field(
        default_factory=fan_cavity.CavityOptions
    )
    grid: grid_quality.GridOptions = field(default_factory=grid_quality.GridOptions)


@dataclass
class PipelineResult:
    options: PipelineOptions
    family: str
    domain: pdm.PlanarDomain | None = None
    scale: float = 1.0
    sites: list = field(default_factory=list)
    diagram: object | None = None
    layout: object | None = None
    solve: patch_solver.SolveResult = field(
        default_factory=patch_solver.SolveResult
    )
    four_sided: object | None = None
    correspondence: object | None = None
    sweep_result: object | None = None
    assembly: object | None = None
    cavity: object | None = None
    graph: pg.PatchGraph | None = None
    metric: sizing.SizeMetric | None = None
    counts: sizing.CountAssignment | None = None
    problems: list = field(default_factory=list)
    coverage: dict | None = None
    model: object | None = None
    identifiers: dict = field(default_factory=dict)
    refused: list = field(default_factory=list)
    grid: grid_quality.GridReport | None = None
    analysis: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @property
    def layer_blocks(self) -> set[str]:
        """Session block ids of the deliberately anisotropic near-wall blocks.

        Derived from the face order rather than from a stored face key, because
        the cavity stage removes and appends faces.
        """
        if self.graph is None:
            return set()
        return {
            f"b{index}"
            for index, face in enumerate(self.graph.faces)
            if face.role == "layer"
        }

    @property
    def covered(self) -> bool:
        return bool(
            self.coverage
            and self.coverage["uncovered_samples"] == 0
            and self.coverage["overlapping_samples"] == 0
            and self.coverage["outside_samples"] == 0
        )

    @property
    def topology_valid(self) -> bool:
        """The patch graph is planar, covers the fluid, and became a session.

        This is the weakest useful statement: incidence and boundary cycles are
        sound, no edge crosses another, the faces tile the domain exactly, the
        periodic correspondence is reciprocal, and ``MeshModel.validate()``
        accepted the result.  It says nothing about quality.
        """
        return bool(
            not self.errors
            and not self.failures
            and not self.problems
            and self.model is not None
            and self.covered
        )

    @property
    def untangled(self) -> bool:
        """No sampled transfinite cell is inverted."""
        if not self.topology_valid:
            return False
        return self.grid is None or self.grid.untangled

    @property
    def within_quality_targets(self) -> bool:
        """Every enabled limit in ``options.grid`` is met."""
        if self.grid is None:
            return self.topology_valid
        return self.grid.within_quality_targets

    @property
    def admissible(self) -> bool:
        """Valid topology and no folded cell: a session may be written.

        Deliberately weaker than ``resolved``.  A research candidate that is a
        real mesh but misses a declared quality target is still worth writing
        out and looking at; one that is tangled, crossed or uncovered is not.
        """
        return self.topology_valid and self.untangled

    @property
    def resolved(self) -> bool:
        """Admissible *and* within every declared quality target."""
        return self.admissible and self.within_quality_targets

    @property
    def quality_failures(self) -> list:
        return [] if self.grid is None else list(self.grid.quality_failures)


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------------------
# External flow
# ---------------------------------------------------------------------------


def run_external(names, loops, options: PipelineOptions | None = None):
    settings = options or PipelineOptions()
    result = PipelineResult(settings, "external")
    try:
        result.domain = pdm.from_bodies(
            names,
            loops,
            farfield_shape=settings.farfield_shape,
            farfield_scale=settings.farfield_scale,
            farfield_name=settings.farfield_name,
        )
        result.problems.extend(result.domain.problems())
        result.sites, result.scale = site_module.build_sites(
            names,
            loops,
            farfield_shape=settings.farfield_shape,
            farfield_scale=settings.farfield_scale,
            farfield_name=settings.farfield_name,
        )
    except Exception as error:
        result.errors.append({"stage": "domain", "error": _describe(error)})
        result.analysis = build_analysis(result)
        return result
    try:
        result.diagram = voronoi_graph.build_diagram(
            result.sites, scale=result.scale, grid_width=settings.grid_width
        )
    except Exception as error:
        result.errors.append({"stage": "voronoi_graph", "error": _describe(error)})
    if result.diagram is not None:
        try:
            result.layout = block_layout.build_layout(result.diagram, settings.layout)
            result.solve = patch_solver.solve(result.layout, settings.solver)
            _apply_forced_splits(result, settings)
        except Exception as error:
            result.errors.append({"stage": "layout", "error": _describe(error)})
    if result.layout is not None:
        try:
            result.metric = _metric(result.domain, settings)
            result.assembly = _assemble_external(result, settings)
            result.graph = result.assembly.graph
            result.failures.extend(result.assembly.failures)
            if not result.solve.converged:
                result.failures.extend(result.solve.failures)
        except Exception as error:
            result.errors.append({"stage": "bands", "error": _describe(error)})
    _finish(result, settings)
    return result


def _apply_forced_splits(result: PipelineResult, settings: PipelineOptions) -> None:
    """Insert the anchors an agent asked for, then relax again."""
    applied = 0
    for cell, order in settings.forced_splits:
        patch = next(
            (
                item
                for item in result.layout.patches
                if item.cell == int(cell) and item.first_cut == int(order)
            ),
            None,
        )
        if patch is None:
            result.solve.failures.append(
                {
                    "reason": "requested split target does not exist",
                    "cell": int(cell),
                    "cut": int(order),
                }
            )
            continue
        if patch_solver.split_patch(result.layout, patch, settings.solver):
            applied += 1
    if applied:
        patch_solver.relax(result.layout, settings.solver)
        result.layout.refresh()
        result.solve.splits += applied
        result.solve.history.append(
            {
                "attempt": "requested split",
                "patches": len(result.layout.patches),
                "applied": applied,
            }
        )


def _assemble_external(result: PipelineResult, settings: PipelineOptions):
    """Assemble bands, splitting a patch whose band block cannot be built."""
    assembly = external_topology.build_graph(
        result.layout,
        result.domain,
        result.metric,
        options=settings.layer,
        wall_edge_style=settings.wall_edge_style,
    )
    for _attempt in range(settings.band_repairs):
        if not assembly.failures:
            break
        progressed = False
        for failure in assembly.failures:
            cell = failure.get("cell")
            for order in failure.get("gate_orders", ()):
                patch = next(
                    (
                        item
                        for item in result.layout.patches
                        if item.cell == cell and item.first_cut == order
                    ),
                    None,
                )
                if patch is None:
                    continue
                if patch_solver.split_patch(result.layout, patch, settings.solver):
                    progressed = True
        if not progressed:
            break
        patch_solver.relax(result.layout, settings.solver)
        result.layout.refresh()
        result.solve.splits += 1
        result.solve.history.append(
            {
                "attempt": "band repair",
                "patches": len(result.layout.patches),
                "reason": "a boundary-layer band block was inadmissible",
            }
        )
        assembly = external_topology.build_graph(
            result.layout,
            result.domain,
            result.metric,
            options=settings.layer,
            wall_edge_style=settings.wall_edge_style,
        )
    return assembly


# ---------------------------------------------------------------------------
# Internal flow
# ---------------------------------------------------------------------------


def run_internal(domain: pdm.PlanarDomain, options: PipelineOptions | None = None):
    settings = options or PipelineOptions()
    result = PipelineResult(settings, "internal")
    result.domain = domain
    try:
        result.problems.extend(domain.problems())
        result.scale = domain.scale
        result.metric = _metric(domain, settings)
    except Exception as error:
        result.errors.append({"stage": "domain", "error": _describe(error)})
        result.analysis = build_analysis(result)
        return result
    four, reasons = sweep_module.detect(domain, settings.sweep)
    result.four_sided = four
    if four is None:
        result.failures.append(
            {
                "stage": "sweep_detection",
                "reason": "no four-sided reading of this domain",
                "candidates": reasons,
            }
        )
        result.analysis = build_analysis(result)
        result.analysis["sweep_rejections"] = reasons
        return result
    try:
        result.sweep_result = sweep_module.build(
            domain,
            four,
            result.metric,
            options=settings.sweep,
            layer_options=settings.layer,
        )
        result.graph = result.sweep_result.graph
        result.correspondence = result.sweep_result.correspondence
        result.failures.extend(result.sweep_result.failures)
    except Exception as error:
        result.errors.append({"stage": "sweep", "error": _describe(error)})
    _finish(result, settings)
    if result.sweep_result is not None:
        result.analysis["sweep"]["rejections"] = reasons
    return result


# ---------------------------------------------------------------------------
# Shared tail
# ---------------------------------------------------------------------------


def _metric(domain: pdm.PlanarDomain, settings: PipelineOptions) -> sizing.SizeMetric:
    return sizing.SizeMetric(
        settings.sizing,
        domain.scale,
        [chain.points for chain in domain.wall_chains()],
    )


def _finish(result: PipelineResult, settings: PipelineOptions) -> None:
    if result.graph is not None:
        # Sharp wall features are repaired the same way whichever producer
        # wrote the graph: the cavity stage only reads incidence, provenance
        # and geometry.
        try:
            repair = fan_cavity.repair(
                result.graph, result.domain, options=settings.cavity
            )
            result.cavity = repair
            result.graph = repair.graph
        except Exception as error:  # pragma: no cover - reported, not raised
            result.errors.append({"stage": "fan_cavity", "error": _describe(error)})
        result.problems.extend(
            result.graph.problems(check_geometry=settings.check_edge_crossings)
        )
        try:
            result.coverage = pg.coverage(
                result.graph, result.domain, samples=settings.coverage_samples
            )
        except Exception as error:  # pragma: no cover - reported, not raised
            result.errors.append({"stage": "coverage", "error": _describe(error)})
    if (
        result.graph is not None
        and not result.problems
        and not result.failures
        and result.covered
    ):
        try:
            result.counts = sizing.assign_counts(
                result.graph, result.metric, settings.sizing
            )
            requests = sizing.layer_grading_requests(result.graph, result.metric)
            model, identifiers, refused = session_emit.build_model_from_graph(
                result.graph,
                result.domain,
                counts=result.counts.counts,
                grading=requests if settings.sizing.graded_layers else (),
                reference_curves=settings.reference_curves,
            )
            result.model = model
            result.identifiers = identifiers
            result.refused = refused
        except Exception as error:
            result.errors.append({"stage": "session", "error": _describe(error)})
    if result.model is not None and settings.evaluate_grid:
        try:
            result.grid = grid_quality.evaluate(
                result.model,
                options=settings.grid,
                layer_blocks=result.layer_blocks,
                first_width=result.metric.first if result.metric else None,
                topology_valid=result.topology_valid,
            )
        except Exception as error:  # pragma: no cover - reported, not raised
            result.errors.append({"stage": "grid_quality", "error": _describe(error)})
    result.analysis = build_analysis(result)


def run(names, loops, options: PipelineOptions | None = None) -> PipelineResult:
    """Backwards-compatible entry point for the external-flow case."""
    return run_external(names, loops, options)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _point(value) -> list[float]:
    return [float(value[0]), float(value[1])]


def singularities(result: PipelineResult) -> list[dict]:
    if result.graph is None:
        return []
    records = result.graph.singularities()
    for record in records:
        key = tuple(record["vertex"])
        record["session_vertex"] = result.identifiers.get(key)
    return records


def build_analysis(result: PipelineResult) -> dict:
    settings = result.options
    domain = result.domain
    analysis: dict = {
        "algorithm": (
            "explicit planar domain, clearance-limited boundary-layer fronts, "
            + (
                "exact generalized medial scaffold for the core"
                if result.family == "external"
                else "sweep/submapping core between the fronts"
            )
            + ", metric cell counts, sampled transfinite-grid quality"
        ),
        "family": result.family,
        "stage_errors": list(result.errors),
        "failures": list(result.failures),
        "domain": None,
        "graph": None,
        "sweep": None,
        "medial": None,
        "layers": None,
        "sizing": None,
        "coverage": result.coverage,
        "singularities": singularities(result),
        "quality": None,
        "session": None,
        "sharp_feature_cavities": (
            None if result.cavity is None else fan_cavity.described(result.cavity)
        ),
        "acceptance": {
            "topology_valid": result.topology_valid,
            "untangled": result.untangled,
            "within_quality_targets": result.within_quality_targets,
            "admissible": result.admissible,
            "resolved": result.resolved,
            "definitions": ACCEPTANCE_TERMS,
        },
        "resolved": result.resolved,
    }
    if domain is not None:
        analysis["domain"] = {
            "name": domain.name,
            "scale": result.scale,
            "holes": domain.hole_count,
            "euler_characteristic": domain.euler_characteristic,
            "simply_connected": domain.simply_connected,
            "signature": domain.signature(),
            "problems": domain.problems(),
            "chains": [
                {
                    "name": chain.name,
                    "role": chain.role,
                    "patch_type": chain.patch_type,
                    "neighbour": chain.neighbour,
                    "length": chain.length,
                    "points": len(chain.points),
                }
                for chain in domain.chains()
            ],
            "minimum_wall_gaps": _wall_gaps(domain),
        }
    if result.graph is not None:
        analysis["graph"] = {
            **result.graph.summary(),
            "problems": result.problems,
            "constraint_components": len(pg.constraint_components(result.graph)),
            # Count-independent: what any later choice of cell counts inherits
            # from this topology.  Reported even when no counts are assigned.
            "sizing_structure": pg.sizing_structure(result.graph),
        }
    if result.metric is not None:
        analysis["sizing"] = {
            "metric": result.metric.described(),
            "components": (
                [] if result.counts is None else result.counts.components
            ),
            "total_cells": 0 if result.counts is None else result.counts.total_cells,
            "budget_factor": (
                1.0 if result.counts is None else result.counts.budget_factor
            ),
            "unattainable_counts": (
                [] if result.counts is None else result.counts.unattainable
            ),
            "refused_grading": result.refused,
        }
    if result.family == "external":
        analysis["medial"] = _medial_section(result)
        analysis["layers"] = _layer_section(result)
    else:
        analysis["sweep"] = _sweep_section(result)
        analysis["layers"] = _sweep_layer_section(result)
    if result.grid is not None:
        analysis["quality"] = result.grid.described()
    if result.model is not None:
        analysis["session"] = {
            "topology_signature": session_emit.topology_signature(result.model),
            "corner_quality": session_emit.quality_summary(result.model),
            "boundaries": {
                name: {
                    "kind": boundary.kind,
                    "neighbour_patch": boundary.neighbour_patch,
                    "edges": len(result.model.boundary_edges(name)),
                }
                for name, boundary in sorted(result.model.boundaries.items())
            },
        }
    analysis["limitations"] = LIMITATIONS
    return analysis


ACCEPTANCE_TERMS = {
    "topology_valid": (
        "patch-graph incidence, planarity, domain coverage and periodic "
        "compatibility all hold and MeshModel.validate() accepted the session"
    ),
    "untangled": "no sampled transfinite cell is inverted",
    "within_quality_targets": (
        "every enabled limit in the GridOptions the report carries is met"
    ),
    "admissible": (
        "topology_valid and untangled: a real mesh, so a session is written "
        "for inspection even when it misses a quality target"
    ),
    "resolved": "admissible and within_quality_targets",
}


# Emitted into every JSON report.  Keep this in step with the "Remaining
# limits" section of the research README; it is the same list, shorter.
LIMITATIONS = [
    "The layer front and the medial scaffold are not reconciled: a front is "
    "capped at a third of the gate-to-ring distance at each gate but at nine "
    "tenths of the distance to the ring between gates, so a curved front can "
    "bulge past a straight core spoke. Tightening the interior cap was "
    "measured and is worse.",
    "A reflex wall corner carries a bisector spoke to the level set's mitre, "
    "so the two band blocks beside it meet the wall at half the fluid angle; "
    "the corner-block alternative is not written yet.",
    "The seam wedge's opposite sides are a band spoke and a core spoke, so "
    "every seam merges the chain's wall-normal band count with its core radial "
    "count; graph.sizing_structure reports the length ratio that forces.",
    "The 30P30N block graph depends on the raster width although its medial "
    "junctions do not; every synthetic fixture is raster-invariant.",
    "The through-cut cavity template is constructed for three sectors only. "
    "More sectors need a transition strip between the sector chain and the "
    "core boundary, which this stage reports rather than builds.",
    "A fan that stops at the layer front merges the wall-tangential and "
    "wall-normal cell-count components of its whole chain, so it is refused "
    "inside a boundary-layer band rather than silently degrading the grading.",
    "A cavity replacement straightens the interior front edges of the two band "
    "intervals beside the feature it repairs; the supplied wall point list is "
    "untouched because it lies on the cavity boundary.",
    "The medial core is still one annulus per body: a cell whose ring has "
    "several disjoint components is reported, not decomposed.",
    "A band block's first cell follows the local band thickness, so the "
    "first-cell width varies inside a block wherever the clearance does.",
    "Core spokes and sweep ribs are straight; no interior guide curve is "
    "fitted to a separatrix yet.",
    "Cross-field separatrix production and a global quantisation solver are "
    "not implemented; counts come from equality components only, which is why "
    "neighbouring components can disagree at an interface.",
]


def _wall_gaps(domain: pdm.PlanarDomain) -> list[dict]:
    walls = domain.wall_chains()
    gaps = []
    for first in range(len(walls)):
        for second in range(first + 1, len(walls)):
            gaps.append(
                {
                    "chains": [walls[first].name, walls[second].name],
                    "distance": g2.minimum_separation(
                        walls[first].points, walls[second].points
                    ),
                }
            )
    return gaps


def _medial_section(result: PipelineResult) -> dict | None:
    diagram = result.diagram
    if diagram is None:
        return None
    sites = result.sites
    return {
        "raster_width": diagram.raster.width,
        "raster_pixel": diagram.raster.pixel,
        "note": (
            "the raster only decides connectivity; junctions and branches are "
            "recomputed analytically"
        ),
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
            }
            for junction in diagram.junctions
        ],
        "branches": [
            {
                "index": branch.index,
                "sites": [sites[index].name for index in branch.pair],
                "length": branch.length,
                "closed": branch.closed,
            }
            for branch in diagram.branches
        ],
        "solver": {
            "converged": result.solve.converged,
            "worst_corner_scaled_jacobian": result.solve.worst_quality,
            "relaxation_sweeps": result.solve.sweeps,
            "anchor_splits": result.solve.splits,
            "history": result.solve.history,
            "failures": result.solve.failures,
        },
    }


def _layer_section(result: PipelineResult) -> dict | None:
    assembly = result.assembly
    if assembly is None:
        return None
    fronts = []
    for cell, front in sorted(assembly.fronts.items()):
        fronts.append(
            {
                "chain": front.name,
                "cell": cell,
                "minimum_height": front.minimum_height,
                "maximum_height": front.maximum_height,
                "requested_height": result.metric.layer_height,
                "global_shrink": front.shrink,
                "notes": front.notes,
            }
        )
    return {
        "law": "height(s) = min(requested, clearance_fraction * local_feature_size(s))",
        "clearance_fraction": result.options.layer.clearance_fraction,
        "slope_limit": result.options.layer.slope_limit,
        "requested_height": result.metric.layer_height if result.metric else None,
        "fronts": fronts,
        "sharp_feature_seams": assembly.seams,
        "sharp_feature_repairs": (
            []
            if result.cavity is None
            else [
                {
                    "feature": record["feature"],
                    "template": record.get("applied_template"),
                    "fluid_angle_degrees": record["fluid_angle_degrees"],
                    "replaced_faces": record.get("replaced_faces"),
                    "new_faces": record.get("new_faces"),
                }
                for record in result.cavity.applied
            ]
        ),
        "notes": assembly.notes,
        "failures": assembly.failures,
    }


def _sweep_section(result: PipelineResult) -> dict | None:
    if result.four_sided is None:
        return None
    section = {"four_sided": result.four_sided.described(), "rejections": []}
    if result.correspondence is not None:
        section["correspondence"] = result.correspondence.described()
    if result.sweep_result is not None:
        section["rows"] = [
            {"name": row.name, "role": row.role, "points": len(row.points)}
            for row in result.sweep_result.rows
        ]
        section["notes"] = result.sweep_result.notes
    return section


def _sweep_layer_section(result: PipelineResult) -> dict | None:
    if result.sweep_result is None:
        return None
    fronts = []
    for row in result.sweep_result.rows:
        if row.role != "front":
            continue
        fronts.append({"guide": row.name, "points": len(row.points)})
    return {
        "law": "height(s) = min(requested, clearance_fraction * local_feature_size(s))",
        "clearance_fraction": result.options.layer.clearance_fraction,
        "requested_height": result.metric.layer_height if result.metric else None,
        "fronts": fronts,
        "notes": result.sweep_result.notes,
    }
