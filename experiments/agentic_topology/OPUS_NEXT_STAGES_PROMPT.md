# Opus implementation prompt: globally valid feature fans and truthful acceptance

You are continuing the BlockDrawer agentic-topology research prototype from
commit `1a0b0ab` (`Expand agentic topology construction prototype`). Work in the
existing repository and modify the code; do not merely write a design or another
literature review.

Read `AGENTS.md` completely before changing anything. Then read:

- `references/topology_state_of_the_art.md`
- `experiments/agentic_topology/README.md`
- every module and test under `experiments/agentic_topology/`
- especially `external_topology.py`, `layers.py`, `patch_graph.py`,
  `grid_quality.py`, `moves.py`, `pipeline.py`, and `research_cli.py`
- the relevant production contracts in `blockdrawer/preview.py`,
  `blockdrawer/quality.py`, `blockdrawer/model.py`, and `blockdrawer/foam.py`

Preserve the working periodic-hill path, the generalized medial/Voronoi
relationships, exact input curves, BlockDrawer session compatibility, and the
existing diagnostics. Do not commit or push unless the user explicitly asks.

## Where the prototype actually stands

The production suite passes: 410 tests, with 32 expected skips. The research
suite passes: 57 tests.

The classical periodic-hill run is executable and deterministic:

- 48 blocks and 13,288 cells;
- zero sampled inverted cells;
- valid BlockDrawer session, rendering, and `blockMeshDict`;
- reciprocal translational periodic patches;
- minimum sampled scaled Jacobian about 0.525;
- maximum sampled non-orthogonality about 58.3 degrees;
- maximum interface size ratio about 3.48;
- maximum first-cell-width relative error about 0.387.

The last two values exceed the declared `GridOptions` defaults of 2.5 and 0.25.
Nevertheless, `GridReport.admissible` currently checks only
`inverted_cells == 0`. Therefore the code and documentation overstate what the
periodic hill passes. Fix this rather than weakening thresholds or hiding the
metrics.

For 30P30N, the original second-stage result had 101 blocks and stopped on four
non-convex patch faces. The latest generic fan integration in
`external_topology.py` now proposes and applies three locally convex
sharp-feature fans and removes those four non-convex failures. It produces 107
candidate blocks, but patch-graph validation now correctly stops on ten edge
crossings around the inserted fans. No session is emitted.

The crossings are localized: fan spokes or the modified adjacent core edges
cross nearby front, ring, core, or neighboring gate-to-ring edges. The current
fan search scores only its five local faces and two neighboring core corners.
It does not test the candidate against the full embedded cavity or patch graph.
This is the primary task.

## Objective

Turn sharp-feature fan insertion into a geometry-generic, globally valid planar
topology operation. A candidate must be rejected or repaired before insertion
if any of its new or modified paths cross existing graph geometry, leave its
local cavity, reverse a boundary/front segment, overlap an edge, create an
uncovered wedge, or make any affected face non-convex.

Then make grid acceptance faithfully enforce the declared limits and expose
structured failures. Keep the periodic-hill result usable as a generated
candidate even if it is classified as below target quality. If the fan work is
completed robustly and time remains, implement the next most useful general
discrete move needed by the resulting worst region; do not add permissive
stubs.

The 30P30N geometry is a test fixture, not the product. No branch may inspect
case names, element names (`slat`, `main`, `flap`), coordinates, the number of
bodies, chord direction, or known gate indices to select topology.

## 1. Build a real fan-cavity operation

Do not patch the current ten intersections one at a time. Define the local
topological cavity owned by a sharp wall feature and construct the replacement
inside that cavity.

At minimum:

1. Identify the ordered boundary of the affected cavity from patch-graph
   incidence and provenance, not from case-specific labels.
2. Include the wall feature, the two adjacent wall/front intervals, their core
   connections, and every neighboring face or edge whose embedding can be
   changed by the fan.
3. Remove the old seam/band/core pieces conceptually, build the complete
   replacement on a copy, and validate the cavity before committing it.
4. Check all new and modified paths against:
   - the cavity boundary;
   - unaffected global graph edges;
   - one another;
   - original solid-body and outer-boundary geometry.
5. Permit only intended shared endpoints and shared complete edges. Reject
   proper crossings, collinear partial overlap, T-junctions, near-zero edges,
   duplicate edges, and paths that leave the fluid region.
6. Verify ordered face incidence, positive orientation, strict convexity where
   required by `MeshModel`, Euler/index preservation, and complete cavity
   coverage without a hole or overlap.
7. Apply the replacement atomically. A rejected candidate must leave the graph
   byte-for-byte/topology-signature equivalent to its prior state.

Reuse and improve the robust segment/path predicates in `geometry2d.py` and
`patch_graph.py`. Tolerances must be derived from domain/cavity scale and
floating-point precision. Do not fix crossings by allowing a large epsilon or
by disabling graph validation.

The present fan has three sectors and five faces, but do not hard-code the
assumption that the same connection pattern works at every sharp feature.
Generate a compact deterministic set of cavity-compatible alternatives, such
as different sector counts, fan reach, attachment edge/vertex, or a short
C-grid-style cut into the residual core. Rank only candidates that pass exact
topological and geometric validation.

The geometric search may still optimize intrinsic continuous quantities such
as distance along a feature-to-medial guide, normalized inner radius, apex
position, and attachment parameters. Its objective must include at least:

- the minimum signed/scaled Jacobian of every affected face;
- clearance from nonincident graph and physical-boundary edges;
- edge length and angle regularity;
- the quality of adjacent retained core faces;
- a modest complexity penalty;
- sampled transfinite-grid quality when a provisional model can be built.

Intersection and validity constraints are hard constraints, never soft
penalties. Prefer a precise unresolved cavity with candidate rejection reasons
to a crossed graph or a fabricated session.

## 2. Make fan diagnostics useful to an agent

For each sharp-feature cavity, emit stable structured JSON containing:

- the feature/cavity identity and geometric scale;
- ordered cavity boundary entities;
- discrete alternative type and sector count;
- continuous parameters and score components;
- whether it was accepted;
- every rejection reason, with both conflicting entity IDs for crossings;
- minimum affected-face quality and its face;
- Euler/index balance before and after;
- whether the failure needs a different continuous placement or a different
  topology.

Render accepted fans and rejected candidates distinctly in focused diagnostic
plots. Keep generated images and JSON under the ignored research output
directory. Do not make an agent infer topology from a generic `crossing_edges`
list when the constructor already knows which candidate caused it.

Expose candidate enumeration and atomic application through the existing
research operation vocabulary where practical. The agent should select among a
small number of validated alternatives, not place individual vertices.

## 3. Enforce truthful quality acceptance

Refactor `GridReport` so acceptance evaluates the `GridOptions` that define the
limits. The report must distinguish at least:

- `topology_valid`: incidence, planarity, periodic compatibility, and valid
  BlockDrawer model/session construction;
- `untangled`: no inverted sampled cell and positive minimum signed Jacobian;
- `within_quality_targets`: every enabled declared limit passes;
- `admissible`: document this term precisely and compute it consistently;
- `quality_failures`: structured records giving metric, observed value, limit,
  comparison direction, block/edge/location where available, and severity.

Pass the options used for evaluation into the report or store an immutable
copy of their relevant limits. Do not let a report silently compare against
different defaults later. Define clear semantics for a deliberately disabled
limit rather than using magic huge values.

Pipeline resolution and artifact policy must distinguish topology failure from
quality-target failure. A topologically valid, untangled research candidate
may still be written to a session for inspection while returning a nonzero
acceptance status and saying exactly which targets it misses. A crossed,
overlapping, non-convex, or inverted topology must not be written as a valid
session.

Update the CLI exit status, JSON schema, text summary, README claims, and tests
accordingly. Keep backward-compatible fields where inexpensive; add fields
rather than casually renaming existing JSON keys.

Do not change the default limits merely to make periodic hill green. Improve
its sizing/grading later or report it honestly as valid and untangled but below
the current interface/first-width targets.

## 4. Tests required for the primary work

Add focused, deterministic unit tests that do not encode 30P30N coordinates:

1. A locally convex fan whose spoke crosses an unaffected cavity edge is
   rejected before mutation.
2. Collinear overlap, a T-junction, and a near-endpoint proper crossing are
   classified correctly at multiple translations, rotations, and scales.
3. A valid sharp-cusp cavity accepts a fan, preserves Euler/index balance, and
   converts to a conformal `MeshModel`.
4. Candidate rejection is atomic and deterministic.
5. A case where the best local-Jacobian candidate crosses an edge selects a
   lower-scoring globally valid candidate instead.
6. Multiple nearby sharp features cannot consume the same face/front interval
   or create mutually crossing fans.
7. A fan candidate remains equivalent under rigid transforms, uniform scaling,
   and reversed input-loop representation.
8. `GridReport` fails each declared threshold independently and reports the
   correct observed value and limit.
9. The periodic hill is reported as topology-valid and untangled while its
   current interface and first-width misses are explicit.
10. A topology-valid but below-target candidate follows the documented session
    and CLI-exit policy; an inverted or crossed candidate emits no session.

Keep every existing research and production test passing.

## 5. Reproducible acceptance runs

### Generic fixtures

Run all existing synthetic external and internal fixtures. Add at least one
small synthetic two-body or narrow-gap case with a sharp feature close enough
to other graph geometry that local-only fan placement would cross it. This
must be the main fan regression fixture; 30P30N is the final integration test.

For every fixture, validate the patch graph before model construction, validate
the `MeshModel`, evaluate every sampled transfinite cell, round-trip the session
when topology permits it, and include deterministic topology signatures.

### Periodic hill

Run `make research-periodic-hill` (or the equivalent Python invocation on
Windows). Preserve its 48-block topology unless a general change has a measured
reason. Confirm it remains deterministic, topology-valid, untangled, periodic,
reloadable, renderable, and serializable. Report every quality target honestly.

### 30P30N

Run `make research-30p30n` (or the equivalent Python invocation). The minimum
primary acceptance for this stage is:

- the current fan-related edge crossings are either eliminated or returned as
  localized rejected alternatives before graph mutation;
- no non-convex face, proper crossing, T-junction, duplicate edge, uncovered
  cavity, or inverted sampled cell is described as resolved;
- diagnostics name the exact remaining cavity and next required topology move;
- the result is deterministic across repeated runs and stable under reasonable
  input resampling;
- no case-specific logic was introduced.

A complete 30P30N session would be an excellent result, but do not force one by
dropping the flap layer/front, coarsening quality sampling, loosening
validation, or accepting crossed geometry. If the globally valid fans expose a
different unresolved core connection, implement one general candidate move
only when it is motivated by that region and also covered by a synthetic test.

Compare any emitted grid with both known baselines:

- old 44-block baseline: 3 inverted cells, minimum scaled Jacobian about
  -0.197, maximum interface ratio about 87.15;
- pre-fan second-stage attempt: 101 blocks and 4 non-convex faces, therefore no
  valid grid/session.

## 6. Secondary work only after the primary acceptance is sound

If time remains, replace one permissive/stub topology move in `moves.py` with a
real deterministic operation selected from the newly exposed worst region:

- singularity split preserving total index;
- separatrix insertion/reconnection;
- poor-patch split;
- safe regular-strip collapse/merge.

It must operate on the general `PatchGraph`, be atomic, produce explicit
rejection reasons, and have tests that would fail for a no-op stub. Do not try
to superficially implement all four.

Also fix small reproducibility defects encountered along the way, including the
literal `\\n` embedded in the Makefile `.PHONY` declaration. Verify that every
CLI artifact option creates its parent directory consistently. Keep such fixes
separate and covered where useful; they are not substitutes for the topology
work.

## General constraints

- No case-name, body-name, coordinate, body-count, fixed-orientation, or fixed
  gate-index specialization.
- No weakening `PatchGraph.validate()`, `MeshModel.validate()`, or sampled-grid
  checks.
- No pretending that extra cells repair the wrong topology.
- No production dependency changes. NumPy/Pillow and optional research tools
  remain confined to `experiments/agentic_topology/`.
- No Gmsh/OpenFOAM requirement in the normal or research unit suite. If either
  is available, it may be used only as an optional independent check.
- Preserve exact point-list input and ordinary BlockDrawer session/export
  semantics.
- Generated sessions, images, OpenFOAM cases, caches, and fetched geometry stay
  out of Git.
- Prefer exact or scale-aware predicates and deterministic finite candidate
  sets over stochastic searches.
- Record the algorithm actually implemented, remaining limitations, commands
  run, and exact acceptance metrics.

## Deliverables

Deliver executable code, tests, and documentation for:

1. cavity-aware, globally planar sharp-feature fan alternatives;
2. atomic candidate validation/application with agent-readable diagnostics;
3. truthful topology/untangled/quality-target acceptance semantics;
4. regression fixtures proving local quality alone is insufficient;
5. reproducible periodic-hill and 30P30N reports;
6. optionally, one real general discrete topology move if the primary work is
   complete.

At the end, report all commands and test counts, the exact periodic-hill and
30P30N status, which acceptance thresholds pass or fail, and the smallest
remaining topological obstruction. A precise unresolved result is better than
a false success.
