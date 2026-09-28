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

import copy
import math
from dataclasses import dataclass, field, replace

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
import spanned
import cgrid
import hull as hull_module
import sweep as sweep_module
import voronoi_graph
import wake


@dataclass(frozen=True)
class PipelineOptions:
    grid_width: int = 700
    # The fabricated outer boundary, used when ``run_external`` gets no
    # explicit ``outer``: a circle (one chain) or a rectangle whose four sides
    # carry their own names and roles, anticlockwise from the lower-left
    # corner (bottom, right, top, left); ``farfield_box`` places the rectangle
    # absolutely instead of scaling the body frame.
    farfield_scale: float = 3.0
    farfield_shape: str = "circle"
    farfield_name: str = "farfield"
    farfield_sides: tuple = pdm.DEFAULT_RECTANGLE_SIDES
    farfield_box: tuple | None = None
    farfield_radius: float | None = None
    farfield_center: tuple | None = None
    wall_edge_style: str = "polyLine"
    coverage_samples: int = 400
    reference_curves: bool = True
    band_repairs: int = 2
    evaluate_grid: bool = True
    check_edge_crossings: bool = True
    # Band height as a fraction of the domain scale: a geometric parameter of
    # the topology, so the block shapes do not change with the cell sizing.
    # ``None`` asks for the metric's wall-normal series height instead.  The
    # default is exactly the series height of the default sizing (0.256), so
    # the default results are unchanged; a different sizing now leaves the
    # band, and every block shape, where it was.
    layer_height_ratio: float | None = sizing.DEFAULT_LAYER_HEIGHT_RATIO
    # An absolute band height in the geometry's units overrides the ratio.
    layer_height: float | None = None
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
    structure: pg.StructureLimits = field(default_factory=pg.StructureLimits)
    span: spanned.SpanOptions = field(default_factory=spanned.SpanOptions)
    wake: wake.WakeOptions = field(default_factory=wake.WakeOptions)
    cgrid: cgrid.CGridOptions = field(default_factory=cgrid.CGridOptions)
    # Hull far field for several bodies: the near field is the annular
    # construction inside the cluster's level set, the far field its level
    # sets beyond it (``hull.py``).
    hull: hull_module.HullOptions = field(default_factory=hull_module.HullOptions)


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
    spanning: spanned.SpanResult | None = None
    span_notes: list = field(default_factory=list)
    wakes: wake.WakeResult | None = None
    wake_notes: list = field(default_factory=list)
    cgrid: dict | None = None
    hull: dict | None = None
    cavity: object | None = None
    graph: pg.PatchGraph | None = None
    metric: sizing.SizeMetric | None = None
    counts: sizing.CountAssignment | None = None
    problems: list = field(default_factory=list)
    coverage: dict | None = None
    model: object | None = None
    identifiers: dict = field(default_factory=dict)
    refused: list = field(default_factory=list)
    # ``shape`` samples every block at uniform fractions and is judged against
    # the shape limits only; ``grid`` samples the graded mesh nodes the
    # session defines and carries the sizing report.
    shape: grid_quality.GridReport | None = None
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
        """No sampled transfinite cell is inverted, on either grid."""
        if not self.topology_valid:
            return False
        for report in (self.shape, self.grid):
            if report is not None and not report.untangled:
                return False
        return True

    @property
    def within_shape_targets(self) -> bool:
        """Every enabled *shape* limit is met on the uniform shape grid.

        Non-orthogonality, skewness and wall misalignment are properties of
        the block map, sampled independently of any cell count or grading.
        """
        if self.shape is None:
            return self.topology_valid
        return self.shape.within_quality_targets

    @property
    def structure_failures(self) -> list:
        """What the topology alone forces on any later sizing, when too much."""
        if self.graph is None:
            return []
        return pg.structure_failures(pg.sizing_structure(self.graph), self.options.structure)

    @property
    def sizing_feasible(self) -> bool:
        """No equality component forces an unacceptable size jump or coupling."""
        return self.graph is not None and not self.structure_failures

    @property
    def within_sizing_targets(self) -> bool:
        """Informational: the mesh the assigned counts define meets the sizing limits.

        Not part of ``resolved``: counts and grading are a later stage that an
        agent adjusts from flow considerations, and this report tells it what
        the default metric produced.
        """
        if self.grid is None:
            return self.topology_valid
        return self.grid.within_quality_targets

    @property
    def admissible(self) -> bool:
        """Valid topology and no folded cell: a session may be written.

        Deliberately weaker than ``resolved``.  A research candidate that is a
        real mesh but misses a declared target is still worth writing out and
        looking at; one that is tangled, crossed or uncovered is not.
        """
        return self.topology_valid and self.untangled

    @property
    def resolved(self) -> bool:
        """Admissible, within the shape targets and structurally sizable."""
        return self.admissible and self.within_shape_targets and self.sizing_feasible

    @property
    def quality_failures(self) -> list:
        """Everything that keeps an admissible result from being resolved.

        Shape failures carry the ``QualityFailure`` records of the shape grid;
        structural failures are dictionaries of the same shape.  Both have a
        ``described()``-compatible reading through ``described_failures``.
        """
        found: list = [] if self.shape is None else list(self.shape.quality_failures)
        found.extend(self.structure_failures)
        return found

    def described_failures(self) -> list[dict]:
        return [
            item.described() if hasattr(item, "described") else dict(item)
            for item in self.quality_failures
        ]

    @property
    def sizing_failures(self) -> list:
        """Informational misses of the counts grid against the sizing limits."""
        return [] if self.grid is None else list(self.grid.quality_failures)


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------------------
# External flow
# ---------------------------------------------------------------------------


def farfield_spec(settings: PipelineOptions) -> pdm.FarfieldSpec:
    return pdm.FarfieldSpec(
        settings.farfield_shape,
        settings.farfield_scale,
        settings.farfield_name,
        settings.farfield_sides,
        settings.farfield_box,
        settings.farfield_radius,
        settings.farfield_center,
    )


def run_external(names, loops, options: PipelineOptions | None = None, *, outer=None):
    """Bodies as closed point lists; the outer boundary supplied or fabricated.

    ``outer`` is a sequence of chains - ``planar_domain.Chain`` objects or
    ``(name, role, points)`` tuples - in anticlockwise order, joined end to
    end.  Without it the options' far field is fabricated around the bodies.
    Either way every chain becomes its own patch, and the chain breaks and
    corners of the outer loop become block vertices.
    """
    settings = options or PipelineOptions()
    result = PipelineResult(settings, "external")
    try:
        circle = None
        if outer is None:
            outer, circle = pdm.fabricate_outer(loops, farfield_spec(settings))
        result.domain = pdm.from_bodies(names, loops, outer=outer)
        result.problems.extend(result.domain.problems())
        # The body frame is measured on the supplied point lists, as the
        # fabricated far field is, so the scale every tolerance follows does
        # not depend on how the domain closes the loops.
        result.sites, result.scale = site_module.sites_from_domain(
            result.domain, circle=circle, frame=site_module.domain_frame(loops)
        )
    except Exception as error:
        result.errors.append({"stage": "domain", "error": _describe(error)})
        result.analysis = build_analysis(result)
        return result
    if settings.hull.enabled:
        built = _try_hull(result, names, loops, settings)
        if built is not None:
            return built
    try:
        result.diagram = voronoi_graph.build_diagram(
            result.sites, scale=result.scale, grid_width=settings.grid_width
        )
    except Exception as error:
        result.errors.append({"stage": "voronoi_graph", "error": _describe(error)})
    if result.diagram is not None:
        try:
            result.metric = _metric(result.domain, settings)
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
    if settings.wake.enabled:
        result = _try_cgrid(result, settings)
    if (
        result.layout is not None
        and result.metric is not None
        and not (result.cgrid or {}).get("applied")
    ):
        result = _try_wakes(result, settings)
    if settings.span.enabled and result.layout is not None and result.metric is not None:
        result = _try_spanning(result, settings)
    return result


def _try_hull(result: PipelineResult, names, loops, settings: PipelineOptions):
    """The hull far field: near field inside the cluster's level set, levels beyond.

    Returns the complete admissible result, or ``None`` with the reason
    recorded under ``result.hull`` so the annular construction runs instead.
    """
    record: dict = {"attempted": False, "applied": False}
    result.hull = record
    if result.domain is None or result.domain.hole_count < 2:
        record["reason"] = "the hull far field is for two or more bodies; one body is the C-grid's case"
        return None
    try:
        metric = _metric(result.domain, settings)
        # The requested band height follows the domain scale by default, far
        # field included; the hull is a near-field construct, so it is capped
        # by the body frame's radius.
        _centre, radius = site_module.domain_frame(loops)
        height = min(settings.hull.height_ratio * metric.layer_height, float(radius))
        hull_points = None
        for _attempt in range(4):
            try:
                hull_points = hull_module.cluster_level_set(loops, height, resolution=settings.hull.resolution)
                break
            except hull_module.HullError as error:
                record.setdefault("notes", []).append(f"height {height:.4g}: {error}")
                height *= 1.5
        if hull_points is None:
            raise hull_module.HullError("no single hull around every body up to five times the requested height")
        record.update({"attempted": True, "hull_height": float(height), "hull_points": len(hull_points) - 1})
        # The near field is built as the C-grid builds its band: the band
        # height is a geometric input of the layout (sliver-free band
        # blocks, at most 45 degrees of wall turning per block) and the
        # inscribed-disk cap is off, since this producer is handed the band
        # height.  The hull carries the outer boundary's chains, at the same
        # fractions, so a closed far field maps chain onto chain.
        near_settings = replace(
            settings,
            hull=replace(settings.hull, enabled=False),
            layer_height=metric.layer_height,
            layout=replace(
                settings.layout,
                band_height=metric.layer_height if settings.layer.enabled else None,
                max_band_turning=settings.layout.max_band_turning if settings.layout.max_band_turning is not None else math.radians(45.0),
            ),
            layer=replace(settings.layer, inscribed_cap=False),
        )
        hull_points = hull_module.align_start(hull_points, result.sites[-1].curve.point_at(0.0))
        hull_chains = hull_module.mirrored_chains(hull_points, result.sites[-1])
        record["hull_chains"] = [name for name, _role, _points in hull_chains]
        near = run_external(names, loops, near_settings, outer=hull_chains)
        record["near"] = {
            "blocks": None if near.graph is None else len(near.graph.faces),
            "admissible": near.admissible,
            "wakes": None if near.wakes is None else [
                {"site": item["site"], "applied": item["applied"], "target": (item.get("target") or {}).get("kind"), "reason": item.get("reason")}
                for item in near.wakes.records
            ],
        }
        if not near.admissible:
            record["near"]["problems"] = near.problems[:4]
            record["near"]["failures"] = near.failures[:4]
            record["near"]["errors"] = near.errors[:4]
            raise hull_module.HullError("the near field inside the hull is not admissible")
        outer_site = result.sites[-1]
        built = hull_module.extend(
            near, result.domain, outer_site, loops, hull_points=hull_points, hull_height=height,
            options=settings.hull, wall_edge_style=settings.wall_edge_style,
        )
        trial = PipelineResult(
            settings, "external", domain=result.domain, scale=result.scale, sites=result.sites,
            diagram=near.diagram, layout=near.layout, solve=near.solve, metric=metric,
            problems=list(result.domain.problems()), assembly=near.assembly, wakes=near.wakes,
            cavity=near.cavity, cgrid=near.cgrid,
        )
        trial.graph = built.graph
        trial.wake_notes = list(near.wake_notes)
        _finish(trial, replace(settings, evaluate_grid=True))
        record.update(built.record)
        record["notes"] = record.get("notes", []) + built.notes
        if not trial.admissible:
            record["problems"] = trial.problems[:6]
            record["errors"] = trial.errors[:4]
            record["coverage"] = trial.coverage
            record["grid"] = {
                "shape_inverted": None if trial.shape is None else trial.shape.inverted_cells,
                "counts_inverted": None if trial.grid is None else trial.grid.inverted_cells,
                "worst_cells": [] if trial.shape is None else trial.shape.worst_cells[:4],
            }
            raise hull_module.HullError("the hull far field failed coverage, export or sampled-grid validation")
    except Exception as error:
        record["reason"] = f"hull far field reverted: {_describe(error)}"
        return None
    record["applied"] = True
    trial.hull = record
    trial.analysis = build_analysis(trial)
    return trial


def _cgrid_layout(result: PipelineResult, settings: PipelineOptions):
    """The gate layout the C-grid is built from.

    The C-grid takes only the body's gate stations from the layout; its core
    is the body's own offsets, not the medial ring.  So the gates are chosen
    by what the C-grid's blocks need - a band block without sliver corners at
    the band height, wall turning of at most 45 degrees per block so the
    far-field spokes stay near the normal - and the anchor floor is judged
    against the wall gap, not against a ring that may be many chords away.
    The annular result keeps the layout its own options built.
    """
    options = replace(
        settings.layout,
        band_height=result.metric.layer_height if settings.layer.enabled else None,
        max_band_turning=(
            settings.layout.max_band_turning
            if settings.layout.max_band_turning is not None
            else math.radians(45.0)
        ),
        floor_by_wall_gap=True,
    )
    layout = block_layout.build_layout(result.diagram, options)
    solve = patch_solver.solve(layout, settings.solver)
    return layout, solve


def _build_cgrid(trial: PipelineResult, plan: dict, settings: PipelineOptions):
    """Build the C-grid, cutting the gate layout where its band block fails.

    The same repair the annular assembly has: a band block that folds or is
    not convex names its wall interval, the patch there is split, the layout
    relaxed, and the C-grid built again - at most ``band_repairs`` times.  A
    failure at a sharp feature, or one that names no interval, is final.
    """
    repairs = 0
    while True:
        try:
            built = cgrid.build(
                trial.layout, trial.domain, trial.metric, plan,
                layer_options=settings.layer, options=settings.cgrid,
                wall_edge_style=settings.wall_edge_style,
            )
            return built, repairs
        except cgrid.CGridError as error:
            if repairs >= settings.band_repairs or error.sharp or not error.intervals:
                raise
            progressed = False
            for start, end in error.intervals:
                patch = _patch_at_wall(trial.layout, plan["cell"], start, end)
                if patch is not None and patch_solver.split_patch(trial.layout, patch, settings.solver):
                    progressed = True
            if not progressed:
                raise
            patch_solver.relax(trial.layout, settings.solver)
            trial.layout.refresh()
            repairs += 1
            trial.solve.splits += 1
            trial.solve.history.append(
                {
                    "attempt": "C-grid band repair",
                    "patches": len(trial.layout.patches),
                    "reason": "a boundary-layer band block was inadmissible",
                }
            )


def _patch_at_wall(layout, cell_index: int, start: float, end: float):
    """The patch of ``cell_index`` whose wall section holds the interval's middle."""
    site = layout.diagram.sites[layout.cells[cell_index].site]
    total = site.curve.length()
    middle = (start + 0.5 * ((end - start) % total)) % total
    for patch in layout.patches:
        if patch.cell != cell_index:
            continue
        span = (patch.wall_end - patch.wall_start) % total
        if span <= 0.0:
            span = total
        if (middle - patch.wall_start) % total <= span:
            return patch
    return None


def _try_cgrid(result: PipelineResult, settings: PipelineOptions) -> PipelineResult:
    """Replace the annular result by the single-body C-grid when it applies.

    One body with one wake and a cap-leg-outlet-leg outer boundary: the
    C-grid is built from the same gates and committed only when the complete
    result - graph, coverage, session, both sampled grids - is admissible.
    Otherwise the annular result stays and the reason is recorded.
    """
    record: dict = {"attempted": False, "applied": False}
    if result.domain is not None and result.domain.hole_count != 1:
        record["reason"] = "the C-grid producer takes exactly one body"
        result.cgrid = record
        result.analysis = build_analysis(result)
        return result
    if result.layout is None or result.metric is None or result.diagram is None:
        record["reason"] = "the C-grid requires a successful domain and layout stage"
        result.cgrid = record
        result.analysis = build_analysis(result)
        return result
    try:
        layout, solve = _cgrid_layout(result, settings)
        plans = [item for item in wake.plan(layout, settings.wake) if item.get("planned")]
    except Exception as error:
        record["reason"] = f"wake planning failed: {_describe(error)}"
        result.cgrid = record
        result.analysis = build_analysis(result)
        return result
    if result.domain is None or result.domain.hole_count != 1:
        record["reason"] = "the C-grid producer takes exactly one body"
    elif len(plans) != 1:
        record["reason"] = f"the C-grid needs exactly one planned wake, found {len(plans)}"
    elif result.errors or result.layout is None:
        # The C-grid takes only the gate stations from the annular layout;
        # the annular relaxation's own patch verdicts do not concern it.
        record["reason"] = "the C-grid requires a successful domain and layout stage"
    if "reason" in record:
        result.cgrid = record
        result.analysis = build_analysis(result)
        return result
    record["attempted"] = True
    trial = PipelineResult(
        settings, "external", domain=result.domain, scale=result.scale,
        sites=result.sites, diagram=result.diagram, layout=layout,
        solve=solve, metric=result.metric, problems=list(result.domain.problems()),
    )
    try:
        built, repairs = _build_cgrid(trial, plans[0], settings)
        record["band_repairs"] = repairs
        trial.graph = built.graph
        trial.assembly = external_topology.AssemblyResult(
            built.graph, {plans[0]["cell"]: built.fronts[0]}, set(), notes=list(built.notes),
        )
        _finish(trial, replace(settings, evaluate_grid=True))
        record.update(built.record)
        record["notes"] = built.notes
        if not trial.admissible:
            record["problems"] = trial.problems
            record["errors"] = trial.errors
            record["coverage"] = trial.coverage
            record["grid"] = {
                "shape_inverted": None if trial.shape is None else trial.shape.inverted_cells,
                "counts_inverted": None if trial.grid is None else trial.grid.inverted_cells,
                "worst_cells": [] if trial.shape is None else trial.shape.worst_cells[:4],
            }
            raise pg.GraphError("the C-grid failed coverage, export or sampled-grid validation")
    except Exception as error:
        record["reason"] = f"C-grid reverted: {_describe(error)}"
        result.cgrid = record
        result.analysis = build_analysis(result)
        return result
    record["applied"] = True
    plans[0]["applied"] = True
    trial.wakes = wake.WakeResult(trial.graph, [dict(plans[0])], [f"wake built into the C-grid with {built.record['levels']} level(s)"])
    trial.cgrid = record
    trial.analysis = build_analysis(trial)
    return trial


def _rewrite(trial: PipelineResult, graph, settings: PipelineOptions):
    """Re-apply the wakes an earlier trial committed to a freshly built graph.

    The graph-level rewrites live on top of the annular construction, so any
    later trial that rebuilds the bands must write them again in order.
    """
    accepted = [] if trial.wakes is None else [dict(item) for item in trial.wakes.applied]
    if not accepted:
        return graph
    for item in accepted:
        item["applied"] = False
    written = wake.apply(
        trial.layout, graph, accepted, trial.assembly.fronts,
        wall_edge_style=settings.wall_edge_style,
    )
    refused = [item for item in written.records if not item["applied"]]
    if refused:
        raise pg.GraphError("an accepted wake could not be rewritten: " + refused[0]["reason"])
    return written.graph


def _try_wakes(result: PipelineResult, settings: PipelineOptions) -> PipelineResult:
    """Commit each wake separatrix only as part of a complete admissible result.

    Every planned feature is tried in turn; a trial rebuilds the bands on a
    copy of the layout with the wake anchor pinned, writes all previously
    accepted wakes plus this one, and must pass coverage, session emission and
    both sampled grids.  A refused wake leaves the result exactly as it was
    and keeps its reason under ``medial.wakes``.
    """
    try:
        records = wake.plan(result.layout, settings.wake)
    except Exception as error:
        result.errors.append({"stage": "wake_plan", "error": _describe(error)})
        return result
    if not any(item["planned"] for item in records):
        result.wakes = wake.WakeResult(result.graph, records)
        result.analysis = build_analysis(result)
        return result
    accepted: list[dict] = []
    notes: list[str] = []
    original_layout = result.layout
    # A wake refused in one pass is tried again after another was accepted:
    # an upstream wake ending on a downstream body may fail only because the
    # downstream body's own trailing edge still carries the seam wedge that
    # its wake replaces.
    queue = [record for record in records if record["planned"]]
    passes = 0
    while queue and passes < 3:
        passes += 1
        deferred: list[dict] = []
        progressed = False
        for record in queue:
            outcome = _try_one_wake(result, settings, record, accepted, original_layout)
            if outcome is None:
                deferred.append(record)
                continue
            result, notes = outcome
            progressed = True
        if not progressed:
            break
        queue = deferred
    for record in queue:
        record["planned"] = False
        record.setdefault("reason", "the wake was not accepted")
    result.wakes = wake.WakeResult(result.graph, records, notes)
    result.analysis = build_analysis(result)
    return result


def _try_one_wake(result, settings, record, accepted, original_layout):
    """One wake trial; ``None`` when refused (the record carries the reason)."""
    if result.errors or result.solve.failures or not result.solve.converged:
        record["planned"] = False
        record["reason"] = "a wake requires a successful domain and medial layout stage"
        return None
    trial_records = [dict(item) for item in accepted + [record]]
    for item in trial_records:
        item["applied"] = False
    trial = PipelineResult(
        settings, "external", domain=result.domain, scale=result.scale,
        sites=result.sites, diagram=result.diagram,
        layout=copy.deepcopy(original_layout), solve=result.solve, metric=result.metric,
        problems=list(result.domain.problems()), cgrid=result.cgrid,
    )
    try:
        if not settings.layer.enabled:
            raise pg.GraphError("a wake continues a boundary-layer band; bands are disabled")
        trial.wake_notes = wake.prepare(trial.layout, trial_records)
        trial.assembly = external_topology.build_graph(
            trial.layout, trial.domain, trial.metric, options=settings.layer,
            wall_edge_style=settings.wall_edge_style, allow_scale=True,
        )
        if trial.assembly.failures:
            record["failures"] = trial.assembly.failures
            raise pg.GraphError("the wake layout could not build every wall band")
        # The band heights at the trailing edges are known now: slide any
        # ring anchor out of the wake bands and rebuild once if needed.
        moved = wake.make_room(trial.layout, trial_records, trial.assembly.fronts, settings.wake)
        if moved:
            trial.wake_notes.extend(moved)
            trial.assembly = external_topology.build_graph(
                trial.layout, trial.domain, trial.metric, options=settings.layer,
                wall_edge_style=settings.wall_edge_style, allow_scale=True,
            )
            if trial.assembly.failures:
                record["failures"] = trial.assembly.failures
                raise pg.GraphError("the wake layout could not build every wall band after making room")
        written = wake.apply(
            trial.layout, trial.assembly.graph, trial_records, trial.assembly.fronts,
            wall_edge_style=settings.wall_edge_style,
        )
        refused = [item for item in written.records if not item["applied"]]
        if refused:
            record["failures"] = refused
            raise pg.GraphError(refused[0]["reason"])
        trial.graph = written.graph
        trial.wakes = written
        _finish(trial, replace(settings, evaluate_grid=True))
        if not trial.admissible:
            record["problems"] = trial.problems
            record["errors"] = trial.errors
            record["coverage"] = trial.coverage
            record["grid"] = {
                "shape_inverted": None if trial.shape is None else trial.shape.inverted_cells,
                "counts_inverted": None if trial.grid is None else trial.grid.inverted_cells,
            }
            raise pg.GraphError("the complete wake failed coverage, export or sampled-grid validation")
    except Exception as error:
        record["applied"] = False
        record["reason"] = f"wake reverted, including its anchor: {_describe(error)}"
        return None
    record.update(written.records[-1])
    record.pop("reason", None)
    accepted.append(dict(record))
    return trial, written.notes


def _try_spanning(result: PipelineResult, settings: PipelineOptions) -> PipelineResult:
    """Commit a complete admissible construction, including its gate placement.

    A graph-only rollback leaves moved gates and added mouth anchors behind.
    Keep the annular result until coverage, session emission and both sampled
    grids accept the trial. Rejected branches retain their exact reason.
    """
    records = spanned.plan(result.layout, result.metric, settings.span)
    notes = []
    # Rebuilding an annular graph would resurrect any earlier dissolved
    # branches, so each trial constructs all previously accepted spans too.
    accepted = []
    original_layout = result.layout
    for record in records:
        if not record["spanned"]:
            continue
        if result.errors or result.solve.failures or not result.solve.converged:
            record["spanned"] = False
            record["reason"] = "spanning requires a successful domain and medial layout stage"
            continue
        trial_records = [dict(item) for item in accepted + [record]]
        trial = PipelineResult(
            settings, "external", domain=result.domain, scale=result.scale,
            sites=result.sites, diagram=result.diagram,
            layout=copy.deepcopy(original_layout), solve=result.solve, metric=result.metric,
            problems=list(result.domain.problems()), wakes=result.wakes,
            wake_notes=list(result.wake_notes), cgrid=result.cgrid,
        )
        try:
            if not settings.layer.enabled:
                raise pg.GraphError("gap spanning requires boundary-layer fronts")
            trial.span_notes = spanned.prepare(trial.layout, trial_records, settings.span)
            trial.assembly = external_topology.build_graph(
                trial.layout, trial.domain, trial.metric, options=settings.layer,
                wall_edge_style=settings.wall_edge_style, allow_scale=True,
            )
            if trial.assembly.failures:
                record["failures"] = trial.assembly.failures
                raise pg.GraphError("the facing-mouth layout could not build every wall band")
            trial.spanning = spanned.apply(
                trial.layout, _rewrite(trial, trial.assembly.graph, settings), trial_records,
                wall_edge_style=settings.wall_edge_style,
            )
            refused = [item for item in trial.spanning.branches if not item["spanned"]]
            if refused:
                record["failures"] = refused
                raise pg.GraphError(refused[0]["reason"])
            trial.graph = trial.spanning.graph
            # Even a caller omitting ordinary grid evaluation cannot commit
            # a span whose exported curved geometry has not been checked.
            _finish(trial, replace(settings, evaluate_grid=True))
            if not trial.admissible:
                record["problems"] = trial.problems
                record["errors"] = trial.errors
                record["coverage"] = trial.coverage
                record["grid"] = {
                    "shape_inverted": None if trial.shape is None else trial.shape.inverted_cells,
                    "counts_inverted": None if trial.grid is None else trial.grid.inverted_cells,
                }
                raise pg.GraphError("the complete span failed coverage, export or sampled-grid validation")
        except Exception as error:
            record["spanned"] = False
            record["reason"] = f"spanning reverted, including mouth placement: {_describe(error)}"
            continue
        record.update(trial.spanning.branches[-1])
        accepted.append(dict(record))
        notes = trial.spanning.notes
        result = trial
    result.spanning = spanned.SpanResult(result.graph, records, notes)
    result.analysis = build_analysis(result)
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
    for attempt in range(settings.band_repairs):
        if not assembly.failures:
            break
        final = attempt == settings.band_repairs - 1
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
            # On the last round a front that still has no admissible height
            # may be thinned uniformly instead of dropping the whole band.
            allow_scale=final,
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
    if settings.layer_height is not None:
        height = float(settings.layer_height)
    else:
        height = (
            None
            if settings.layer_height_ratio is None
            else float(settings.layer_height_ratio) * domain.scale
        )
    return sizing.SizeMetric(
        settings.sizing,
        domain.scale,
        [chain.points for chain in domain.wall_chains()],
        layer_height=height,
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
            # The shape grid: the block map at uniform fractions, judged
            # against the shape limits only.  Count-independent.
            result.shape = grid_quality.evaluate(
                result.model,
                options=settings.grid.shape_only(),
                layer_blocks=result.layer_blocks,
                topology_valid=result.topology_valid,
                cells=settings.grid.shape_cells,
            )
            # The counts grid: the mesh the assigned counts and grading define,
            # with every limit, as the sizing report.
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
            "within_shape_targets": result.within_shape_targets,
            "sizing_feasible": result.sizing_feasible,
            "admissible": result.admissible,
            "resolved": result.resolved,
            "within_sizing_targets": result.within_sizing_targets,
            "quality_failures": result.described_failures(),
            "structure_limits": settings.structure.described(),
            "definitions": ACCEPTANCE_TERMS,
        },
        "resolved": result.resolved,
    }
    if domain is not None:
        analysis["domain"] = {
            "name": domain.name,
            "scale": result.scale,
            # The frame diameter of the whole domain, outer boundary included:
            # the length --layer-height-ratio and the sizing ratios multiply.
            "domain_scale": domain.scale,
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
            # Informational: how the mesh the default counts define measures
            # up.  Not part of ``resolved``.
            "report": None if result.grid is None else result.grid.described(),
            "within_sizing_targets": result.within_sizing_targets,
            "feasibility": {
                "limits": settings.structure.described(),
                "failures": result.structure_failures,
            },
        }
    if result.family == "external":
        analysis["medial"] = _medial_section(result)
        analysis["layers"] = _layer_section(result)
    else:
        analysis["sweep"] = _sweep_section(result)
        analysis["layers"] = _sweep_layer_section(result)
    if result.shape is not None:
        analysis["quality"] = result.shape.described()
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
    "untangled": (
        "no sampled transfinite cell is inverted, on the uniform shape grid "
        "or on the graded counts grid"
    ),
    "within_shape_targets": (
        "every enabled shape limit - non-orthogonality, skewness, wall "
        "misalignment - is met on the shape grid, which samples every block "
        "at uniform fractions independent of counts and grading"
    ),
    "sizing_feasible": (
        "no opposite-edge equality component forces a length ratio above the "
        "structure limit or ties a physical wall/front tangential resolution "
        "to a boundary-layer normal one; generic core/ring role mixing alone "
        "does not establish wall-band coupling"
    ),
    "admissible": (
        "topology_valid and untangled: a real mesh, so a session is written "
        "for inspection even when it misses a target"
    ),
    "resolved": "admissible and within_shape_targets and sizing_feasible",
    "within_sizing_targets": (
        "informational, not part of resolved: the mesh the default counts and "
        "grading define meets the sizing limits - aspect ratio, interface "
        "size ratio, first-cell width"
    ),
}


# Emitted into every JSON report.  Keep this in step with the "Remaining
# limits" section of the research README; it is the same list, shorter.
LIMITATIONS = [
    "The annular producer is admissible up to about six chords of far field on "
    "a single sharp-edged body; beyond that the C-grid producer applies, and "
    "only with --wake, exactly one body, one planned wake and a cap-leg-outlet-"
    "leg outer boundary. Its far-field spokes are straight.",
    "The wake separatrix is opt-in (--wake). It is one straight scaffold line "
    "from a sharp feature through the body's own ring to the outer boundary; "
    "a wake that would enter another body's cell, cross another medial branch, "
    "or meet the outer boundary across a chain break is refused with its "
    "reason, and a refused wake leaves the annular construction untouched.",
    "Gap spanning is opt-in. Its core-to-farfield count connection is reported "
    "but is not a wall-band direction coupling. Mouths "
    "must fit before the next branch anchor; sharp gates, a third wall and "
    "junctions with several narrow branches need another transition. Rejected "
    "trials restore gate placement, bands and session as well as the graph.",
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
    "Front repair reduces whole failing blocks, requests cuts and permits a "
    "bounded global height search on the final repair round. It is deterministic "
    "but a last-bit height change still moves sharp_bodies' default cell count "
    "by about eight percent without changing its block graph.",
    "The through-cut cavity template is constructed for three sectors only. "
    "More sectors need a transition strip between the sector chain and the "
    "core boundary, which this stage reports rather than builds.",
    "A fan that stops at the layer front merges the wall-tangential and "
    "wall-normal cell-count components of its whole chain, so it is refused "
    "inside a boundary-layer band rather than silently degrading the grading.",
    "A cavity replacement straightens the interior front edges of the two band "
    "intervals beside the feature it repairs; the supplied wall point list is "
    "untouched because it lies on the cavity boundary.",
    "The default medial core is one annulus per body: a cell whose ring has "
    "several disjoint components is reported, not decomposed.",
    "The outer boundary is one site: its chains become patches and its "
    "corners and chain breaks become gates, but a wall chain on the outer "
    "boundary gets no boundary-layer band from the external producer.",
    "The requested band height and the size metric are fractions of the "
    "domain scale, which grows with the outer boundary's extent; a far-away "
    "outer boundary needs an explicit --layer-height-ratio.",
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
        "spanned_branches": (
            None if result.spanning is None else result.spanning.branches
        ),
        "spanning_options": {
            "enabled": result.options.span.enabled,
            "clearance_ratio": result.options.span.clearance_ratio,
            "mouth_angle": result.options.span.mouth_angle,
            "mouth_reach": result.options.span.mouth_reach,
        },
        "spanning_notes": list(result.span_notes)
        + ([] if result.spanning is None else result.spanning.notes),
        "wakes": None if result.wakes is None else result.wakes.records,
        "cgrid": result.cgrid,
        "hull": result.hull,
        "wake_options": {
            "enabled": result.options.wake.enabled,
            "fluid_angle": result.options.wake.fluid_angle,
            "direction": result.options.wake.direction,
            "clearance_ratio": result.options.wake.clearance_ratio,
        },
        "wake_notes": list(result.wake_notes)
        + ([] if result.wakes is None else result.wakes.notes),
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
