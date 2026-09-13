# Implementation prompt for Opus

You are working in the BlockDrawer repository. Read `AGENTS.md` completely
before making changes, then inspect the existing uncommitted research prototype
in `experiments/agentic_topology/colored_medial_axis.py` and its README. Preserve
useful existing work; do not discard or rewrite it blindly.

## Objective

Implement the next research prototype for **geometry-generic, automatic
quadrilateral block-topology construction around multiple disjoint 2D bodies**.
The intended product is a hybrid workflow:

1. Algorithms construct and validate most of the topology deterministically.
2. An AI agent inspects a compact graph and quality diagnostics and makes only
   the genuinely ambiguous local decisions.
3. The result is emitted as an ordinary editable BlockDrawer session.

The supplied 30P30N three-element airfoil is only an acceptance fixture. Do not
specialize the algorithm, thresholds, feature detection, graph shape, or code to
airfoils, to three components, or to the names `slat`, `main`, and `flap`.

## Current state and observed result

The existing prototype accepts repeated `--curve NAME=PATH` point lists and:

- closes finite trailing-edge gaps for planar-domain analysis;
- constructs a raster generalized Voronoi diagram whose sites are all body
  boundaries plus a rectangular farfield;
- extracts component-coloured interfaces and three-way junctions;
- projects junction spokes to incident boundary sites;
- traces and simplifies interface branches;
- uses dynamic programming to seek an ordered wall/interface correspondence in
  which every strip sector has strictly convex corners.

On the normalized 30P30N curves, the graph has four three-way junctions and six
interface branches. Its connectivity remained unchanged in a sweep of wall-strip
widths 0.001, 0.002, and 0.004. The strip matcher resolves the uncomplicated
farfield-facing regions, but rejects two gap-side patches:

- the slat side of the slat/main interface;
- the main-element side of the main/flap interface.

Nearest-point correspondence collapses several samples at sharp features and
creates degenerate slivers. Uniform arc-length correspondence still produces
inverted corner polygons in those two patches. Blind recursive subdivision does
not fix the sign of the local mapping Jacobian. Do not restore any of those
approaches as a pretend solution.

## Required implementation

Turn each unresolved region into a small, explicit constrained quadrangulation
problem. A failed region is bounded by:

- one ordered section of a body or farfield boundary;
- one ordered generalized-Voronoi interface branch;
- two endpoint spokes;
- any shared interface vertices already imposed by adjacent regions.

Implement a general local solver that may insert a small number of regular
Steiner vertices and non-four-valence topology singularities. It must search for
a valid all-quad decomposition instead of assuming that the region is a single
one-to-one strip.

A suitable implementation strategy is:

1. Normalize all geometry by a robust domain scale. Derive every tolerance and
   sampling density from scale, local feature size, curvature, or raster spacing.
2. Detect intervals where a proposed strip has negative/near-zero Jacobian,
   crossed spokes, non-monotone closest-point order, excessive turning, or poor
   aspect ratio. These intervals define candidate singularity zones.
3. Generate candidate split anchors from geometry, not names or fixed
   coordinates: gap throats, medial-axis turning points, curvature extrema,
   visibility changes, and endpoints of invalid-Jacobian intervals are useful
   candidates.
4. Search a small discrete space of planar split graphs. Permit interior
   valence-3/5 singularities when required by the quadrilateral index balance.
   Use Euler/index constraints to reject impossible graphs before geometric
   optimization. A deterministic branch-and-bound search is acceptable; an
   optional research-only MILP dependency is acceptable if it has a clean
   diagnostic when unavailable.
5. For each discrete candidate, solve/relax vertex positions while boundary
   vertices remain constrained to their curves. Optimize a scale-independent
   objective containing at least inverted-cell barriers, minimum corner angle,
   aspect ratio, spoke crossing, interface alignment, and unnecessary block
   count. A harmonic/Winslow-style initialization followed by bounded local
   optimization is reasonable.
6. Select the simplest valid layout, not merely the candidate with the largest
   number of blocks. Return structured rejection reasons when none is valid.

You may instead implement a constrained triangulation followed by topology-aware
triangle pairing and Steiner repair if it meets the same invariants. Do not use
an unconstrained Delaunay/recombination result that loses the supplied boundary
and interface graph.

The global generalized-Voronoi construction is inspired by medial-axis blocking
and TopMaker-style methods. Cross-field/separatrix or integer-grid-map ideas may
be used locally, but a full production-grade global parameterization is not
required for this prototype:

- https://ntrs.nasa.gov/citations/20040073477
- https://www.sciencedirect.com/science/article/abs/pii/S0010448515000998
- https://arxiv.org/abs/1708.02316
- https://www.graphics.rwth-aachen.de/publication/03197/

## Conformity and BlockDrawer requirements

The completed topology must be conformal:

- An interface path and its subdivision vertices have one identity shared by
  the regions on both sides.
- There are no hanging vertices or duplicate parallel topological edges with
  the same endpoint IDs.
- Every block has four distinct, counter-clockwise vertices and passes
  BlockDrawer's existing strict-convexity validation unchanged.
- No internal edge has more than two incident blocks.
- Body-wall and farfield edges have one incident block and receive the correct
  named boundary assignment.
- Original point-list geometry is retained on wall edges as ordered spline or
  polyLine data; raster samples must never replace the source geometry.
- Interface geometry is deterministic and consistently oriented with canonical
  `edge_key()` storage.
- Opposite edge cell counts satisfy BlockDrawer's transitive constraints. Start
  with conservative uniform counts; physical boundary-layer grading is outside
  this prototype's scope.
- Reject overlapping blocks and uncovered core regions using an independent
  geometric/raster coverage check; `MeshModel.validate()` alone is not a complete
  planar coverage test.

When a complete topology exists, construct a `MeshModel(initialize=False)` using
the public domain types, call `model.validate()`, save it with
`blockdrawer.session.save_session()`, reload it, validate again, render it
headlessly, and verify that `blockMeshDict` serialization succeeds. Do not weaken
model validation to admit the generated result.

Add an explicit experimental CLI output option such as `--session PATH`. Do not
write a partial session when unresolved regions remain. The PNG and JSON analysis
must still be produced in failure cases, with the smallest unresolved regions,
candidate graphs tried, and numerical rejection reasons clearly reported.

## Generality requirements

The implementation must support arbitrary finite collections of disjoint closed
point-list bodies, including one, two, three, or more components. It must not
assume:

- airfoil geometry or a chordwise x direction;
- a particular component count, name, input order, or winding direction;
- normalized coordinates or a unit-sized domain;
- sharp or zero-thickness trailing edges;
- a particular generalized-Voronoi graph or junction count;
- that every junction has exactly three sites after raster degeneracy handling;
- fixed numeric coordinates, gap sizes, or hand-selected split locations from
  30P30N.

Make output deterministic under component permutation. Equivalent geometry with
reversed point order, translation, rotation, and uniform scaling must produce an
equivalent topology signature and comparable dimensionless quality values.

Keep NumPy/Pillow and any optimization package confined to the research
prototype. BlockDrawer's package and normal test suite must remain standard-
library-only at runtime. Do not move experimental numerical code into production
modules yet.

## Test geometries

Use synthetic geometries in addition to the 30P30N fixture:

1. One circle or ellipse inside a rectangular farfield.
2. Two separated circles of unequal radius.
3. Three rotated ellipses with unequal gaps.
4. A concave but simple closed body paired with a convex body.
5. Permuted, reversed, translated, rotated, and uniformly scaled copies of at
   least one multi-body case.

Generate synthetic point lists in the test code. Research tests may live beside
the experiment and may skip cleanly when optional research dependencies are not
installed. Do not add NumPy as a dependency of the normal `tests/` suite.

Use the normalized slat/main/flap `.dat` files from
https://github.com/linuxguy123/30P-30N-Validation-Case for the acceptance run.
Do not copy those third-party geometry files into this repository.

## Acceptance criteria

The task is successful when:

1. The synthetic cases complete without special-case names or coordinates.
2. Invariance tests pass for component order, winding, translation, rotation,
   and uniform scaling.
3. The 30P30N analysis retains the stable four-junction/six-branch graph, or a
   documented resolution-independent equivalent if graph extraction is improved.
4. Both formerly unresolved gap-side regions receive valid local
   quadrangulations.
5. A complete 30P30N BlockDrawer session is emitted, reloads, validates, renders,
   and serializes to `blockMeshDict` without overlapping blocks or uncovered
   fluid regions.
6. Output JSON explains the selected singularities, discrete graph, objective
   terms, dimensionless quality, and rejected alternatives sufficiently for an
   agent to make a targeted follow-up edit.
7. Running `python -m unittest discover -s tests -v` still passes.

If criterion 4 or 5 cannot be achieved, do not disguise the failure with a
case-specific patch. Leave the prototype in a clean, executable state and report
the smallest failed region, its boundary chains, the candidate graphs tried, and
the exact invariant or objective that rejected each one. That diagnostic is a
valuable result for the next iteration.

## Deliverables and working style

- Implement and document the research solver under
  `experiments/agentic_topology/`.
- Keep the existing analysis PNG/JSON workflow working.
- Add a reproducible command for the 30P30N run and for synthetic self-tests.
- Keep generated images, sessions, downloaded geometry, and caches out of Git.
- Run the research self-tests and the complete BlockDrawer test suite.
- Summarize the algorithm actually implemented, genericity checks, 30P30N
  outcome, remaining limitations, and exact artifact paths.
- Do not commit or push unless the user explicitly asks.

Spend effort on executable geometry and validation rather than an extended
literature review. The key research question is whether a generic local
singularity-aware quadrangulator can close the two hard regions identified by the
global medial-axis graph.
