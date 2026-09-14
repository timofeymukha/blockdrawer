# Opus execution prompt: medial core-spoke guides and front compatibility

You are continuing the BlockDrawer agentic-topology research prototype from
the repository's current committed tree. This is an implementation task: write
executable code and tests, run the acceptance cases, and report measurements.
Do not merely return another design or status report. Do not commit or push
unless the user explicitly asks.

Read `AGENTS.md` completely before changing code. Then read:

- `references/topology_state_of_the_art.md`
- `experiments/agentic_topology/README.md`
- every current module and test under `experiments/agentic_topology/`
- especially `external_topology.py`, `layers.py`, `block_layout.py`,
  `colored_medial_axis.py`, `voronoi_graph.py`, `sites.py`, `geometry2d.py`,
  `patch_graph.py`, `fan_cavity.py`, `pipeline.py`, `sizing.py`,
  `grid_quality.py`, `session_emit.py`, and `research_cli.py`
- the production contracts in `blockdrawer/model.py`,
  `blockdrawer/preview.py`, and `blockdrawer/foam.py`

Keep the research implementation outside the production package and preserve
the standard-library-only production runtime.

## Verified starting point

The current implementation has 77 passing research tests and 410 passing
production tests, with 32 expected OpenFOAM skips.

The periodic-hill case is topology-valid and untangled:

- 48 blocks and 13,288 cells;
- zero sampled inverted cells;
- valid round-tripped BlockDrawer session, render, and `blockMeshDict`;
- minimum scaled Jacobian about 0.525;
- maximum non-orthogonality about 58.3 degrees;
- interface size ratio about 3.479, missing its 2.5 target;
- first-cell-width error about 0.387, missing its 0.25 target.

Do not change the quality limits to make this case green. Preserve the current
distinction between `topology_valid`, `untangled`, `admissible`, and `resolved`.

For 30P30N, all three elements now retain boundary-layer bands and all four
sharp features receive valid seam/cavity treatment. The candidate graph has
115 faces and no non-convex feature faces, but it is still invalid because of
exactly two localized relations:

1. A wall path and its front path make an `unexpected_touch` near
   `(0.6456861757, 0.0263163304)`:

   ```text
   wall:
     (gate, 0, anchor, 5) -- (gate, 0, anchor, 15)
   front:
     (front, 0, anchor, 5) -- (front, 0, anchor, 15, out)
   ```

2. A straight core spoke crosses the neighboring curved front near
   `(0.8019631213, 0.0221454729)`:

   ```text
   core_spoke:
     (front, 1, anchor, 24) -- (ring, anchor, 24)
   front:
     (front, 1, anchor, 14) -- (front, 1, anchor, 24)
   ```

No current 30P30N session or sampled grid exists. Any existing
`output/30p30n-session.json` is a stale older baseline and must not be treated
as a result of the current run.

The previous response proposed giving the core spoke an interior guide curve
that follows the medial construction rather than connecting its endpoints with
an unconstrained straight chord. That is a promising and general hypothesis
for the second conflict, but it has not been implemented and does not by itself
resolve the separate wall/front touch.

Two simple front changes have already been measured as worse and must not be
repeated:

- globally tightening the interior scaffold cap increases the 30P30N problem
  count and creates inverted cells in a generic synthetic fixture;
- restoring the sharp-height floor after slope limiting does not remove the
  touch and breaks the flap seam.

## Objective

Implement a geometry-generic guided core-spoke construction, integrate its
curved paths through `PatchGraph`, BlockDrawer session emission, validation,
sizing, and sampled-grid evaluation, and independently repair or precisely
classify the remaining wall/front contact.

The 30P30N geometry is an integration fixture, not the product. No branch may
inspect case names, element names (`slat`, `main`, `flap`), known coordinates,
body count, chord direction, fixed gate/anchor indices, or horizontal flow
direction.

## 1. Derive a core-spoke guide from medial geometry

Do not draw an arbitrary spline around the reported intersection. A spoke is a
coordinate line connecting one layer front to the corresponding medial/ring
anchor. Derive candidate paths from the distance geometry that created that
anchor.

The preferred construction to investigate is a characteristic of the distance
field for the owning site:

1. Start at the ring/medial anchor.
2. Use the owning site's closest-point/distance gradient already available in
   `sites.py` and the medial diagram.
3. Trace adaptively toward the wall while the nearest feature is well-defined.
4. Locate the first exact intersection with the layer front.
5. Use that intersection as the front endpoint of the spoke.

For a smooth region this should reduce to the normal characteristic. Where the
closest feature changes, it may become a piecewise-smooth guide. Step size and
termination tolerances must derive from local clearance, path length, and
domain scale.

If the characteristic meets the intended front interval away from its existing
vertex, do not pretend it met the old endpoint. Insert a stable front vertex
and split the adjacent front/core edge and faces conformally. If it meets the
wrong interval, leaves the owning medial cell, reaches a singular projection,
or cannot reach the front monotonically, reject it with a structured reason.

You may also enumerate a small number of mathematically defined alternatives,
such as a constrained Hermite guide using front-normal and ring-normal tangent
conditions, or a visibility/geodesic path inside the residual polygon. These
must be documented as alternatives and subjected to the same hard validation;
they may not be coordinate-tuned escape paths.

## 2. Treat the guided spoke as real edge geometry

`PatchGraph.PGEdge` already carries `kind`, `path`, and interpolation points,
and `session_emit.py` already forwards supported curved edge types to
BlockDrawer. Use that path consistently rather than keeping one geometry for
validation and another for export.

Requirements:

- exact endpoint agreement and canonical orientation;
- deterministic simplification that retains all geometrically necessary
  points;
- the same shared path for both incident faces;
- no proper crossing, unexpected touch, overlap, T-junction, or collapsed
  segment with any graph or physical-boundary path;
- the complete path remains inside the union of the intended adjacent core
  regions;
- face outlines and domain coverage use the curved path;
- metric length, cell sizing, and grading use its actual length;
- session round-trip preserves the curve direction and points;
- BlockDrawer preview and research grid evaluation see the same edge curve.

Changing a straight chord into a curve can make corner-only quadrilaterals look
valid while folding their transfinite interiors. Therefore every accepted
guide must be checked with the actual sampled edge-weighted transfinite grid.
No inverted sampled cell is admissible.

The guide should normally improve alignment and avoid an obstruction without
hugging another edge. Include a scale-normalized clearance term and a modest
curvature/complexity penalty in candidate ranking, but keep intersection,
coverage, incidence, and inversion as hard constraints.

## 3. Preserve topology when the guide hits inside a front edge

An interior front intersection is a topological event. Implement it atomically:

- split the front edge at the traced station;
- add the corresponding spoke endpoint identity;
- split or rebuild the affected core face or local residual polygon into valid
  quadrilaterals;
- split the wall-layer interval too only when conformity requires it;
- preserve boundary identity and the supplied wall point list;
- preserve Euler characteristic and total discrete index;
- do not merge wall-tangential and wall-normal cell-count components;
- reject an odd or otherwise unquadrangulable residual region explicitly.

Build and validate the complete candidate on a graph copy before applying it.
A rejected candidate must leave the graph topology signature unchanged.

Expose guide alternatives through the research candidate vocabulary with a
stable ID, affected entities, method, stations, score components, and all
rejection reasons. An agent should choose among validated guide/topology
alternatives, not place control points manually.

## 4. Resolve the independent wall/front contact

The wall/front `unexpected_touch` is not a core-spoke problem. Diagnose its
actual cause from the continuous paths: distinguish at least offset collapse,
slope-limiter propagation, polyline flattening, seam endpoint replacement, and
an interval that has no positive-clearance offset.

Add an interval-level front validity check before graph assembly. It must test
the full front path against its corresponding wall path and neighboring wall
intervals, not only endpoint heights and quadrilateral corners.

Attempt a deterministic local repair that adjusts only a scale-aware
neighborhood of the contact while enforcing:

- positive wall/front separation along the entire interval;
- the configured height-slope limit;
- no new front self-intersection;
- valid neighboring seams and feature cavities;
- positive layer-block corner and sampled-cell Jacobians;
- retention of a useful minimum layer thickness.

If no such placement exists, introduce a local station/cut and rebuild the
affected band/core interval, or return a structured topology requirement. Do
not silently collapse the front onto the wall, remove the body's layer band, or
apply a global shrink justified only by 30P30N.

## 5. Required generic tests

Use synthetic geometry before relying on 30P30N. Add deterministic tests for:

1. A straight spoke crossing a curved front while a medial-gradient guide is
   valid.
2. A characteristic reaching the intended front edge away from its existing
   endpoint and causing a conformal local split.
3. A guide reaching the wrong interval or leaving its medial cell and being
   rejected atomically.
4. A guided edge crossing a remote edge and being rejected.
5. Shared curved-spoke geometry surviving PatchGraph-to-`MeshModel` conversion,
   session round-trip, and reversed canonical orientation.
6. Sampled transfinite evaluation rejecting a curved guide whose block corners
   are valid but whose interior is inverted.
7. A front touching its wall between two valid endpoints being detected before
   final graph assembly.
8. A local front repair removing that contact without breaking an adjacent
   seam or another body.
9. An infeasible positive-clearance interval producing a precise failure rather
   than a collapsed band.
10. Translation, rotation, uniform-scale, input-reversal, and reasonable
    resampling invariance for both guide tracing and contact repair.
11. Euler/index, coverage, boundary, and cell-count-component preservation for
    every topology-changing candidate.
12. Every existing synthetic external and internal fixture remaining at least
    as valid and untangled as its current baseline.

Keep all existing research and production tests passing.

## 6. Acceptance runs

Run the complete synthetic corpus, periodic hill, and 30P30N. Report stage
timings and candidate counts as well as final metrics.

The periodic hill must remain a deterministic 48-block, topology-valid,
untangled, periodic, reloadable, renderable, and serializable result. Its two
known quality-target misses must remain explicit unless a genuinely general
improvement changes them.

For 30P30N, first reproduce the two exact baseline conflicts. The primary
acceptance target is then:

- zero graph crossings, unexpected touches, overlaps, T-junctions, non-convex
  faces, hanging vertices, and uncovered regions;
- all three boundary-layer bands and all four sharp-feature repairs retained;
- a valid `MeshModel` and newly written BlockDrawer session;
- zero inverted cells in the complete sampled transfinite grid;
- deterministic output across repeated runs;
- stable behavior under reasonable input resampling;
- no case-specific branch or coordinate.

If the result becomes admissible but misses quality targets, write the session
for inspection and report every miss. Do not claim that topological validity
alone makes it a good mesh.

If the guided spoke removes only the second conflict, the run remains
unresolved until the wall/front contact is repaired or reduced to a precise
topology requirement. Report both issues independently rather than saying the
case is solved.

## 7. Performance and artifacts

The current 30P30N run already takes several minutes. Cache distance queries,
path bounds, traced characteristics, and unchanged validation results. Use
cheap broad-phase and topology checks before sampled-grid scoring. Avoid an
unrestricted Cartesian product of guide control points.

Use fresh or run-specific ignored output directories for acceptance runs. A
failed run must never advertise an older session at the requested path as a
new artifact. Do not destructively delete arbitrary user files; emit explicit
current-run artifact status or a manifest.

## Constraints

- No case-name, body-name, coordinate, body-count, fixed-index, orientation, or
  chord-direction specialization.
- No weakening of domain, PatchGraph, `MeshModel`, coverage, or sampled-grid
  validation.
- No removal of difficult elements, layer bands, feature cavities, or exact
  wall points.
- No assumption that a curved spoke is valid merely because it avoids the
  reported crossing.
- No required OpenFOAM, Gmsh, SciPy, Shapely, or other compiled dependency in
  the test suites. Optional research oracles must skip cleanly.
- Generated sessions, plots, reports, cases, manifests, and caches remain out
  of Git.
- Preserve extension points for future cross-field/separatrix tracing, but do
  not implement a global cross-field solver in this iteration.

## Deliverables

Deliver executable code, tests, and documentation for:

1. medial/distance-derived core-spoke guide candidates;
2. complete curved-edge validation, export, sizing, and sampled-grid handling;
3. atomic conformal insertion when a guide hits inside a front interval;
4. interval-level wall/front-contact diagnosis and local repair;
5. agent-readable alternatives and rejection reasons;
6. reproducible synthetic, periodic-hill, and 30P30N results.

At the end, state plainly:

- whether each of the two original 30P30N conflicts is gone;
- whether the graph is topology-valid;
- whether a new session was written in this run;
- whether its sampled grid is untangled;
- every quality target passed or missed;
- exact test counts and timings;
- the smallest remaining obstruction, if any.

A precise unresolved result is preferable to a false success. Spend the quota
on executable topology, geometric constraints, regression tests, and measured
acceptance—not another survey.
