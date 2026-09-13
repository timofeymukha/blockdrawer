# Next-stage implementation prompt for Opus

You are working in the BlockDrawer repository. Read `AGENTS.md` completely,
then read all of these before changing code:

- `references/topology_state_of_the_art.md`
- `experiments/agentic_topology/README.md`
- the existing modules and tests under `experiments/agentic_topology/`
- `blockdrawer/preview.py`, `blockdrawer/quality.py`, `blockdrawer/spacing.py`,
  `blockdrawer/commands.py`, and `blockdrawer/foam.py`

Preserve the successful existing research prototype. Do not replace working
medial/Voronoi graph construction, conformity checks, diagnostics, session
emission, or invariance tests without a demonstrated reason.

Do not commit or push unless the user explicitly asks.

## Objective

Implement the next executable research stage toward a geometry-generic,
agent-assisted 2D CFD block-topology generator. The existing prototype proves
that a generalized medial/Voronoi graph can connect multiple bodies and emit a
valid conformal BlockDrawer topology. Its 30P30N result is topologically valid
but produces a poor mesh because it lacks explicit boundary-layer topology,
general singularity placement, metric-based cell counts, and quality evaluation
of the actual transfinite grid.

This iteration must establish a shared domain/topology representation that can
handle both:

1. external flow around zero or more closed bodies inside an outer boundary;
2. simply connected internal flow with named wall, inlet/outlet, symmetry, or
   translational-periodic boundary chains.

Use two complementary automatic paths rather than forcing every geometry
through one construction:

- a generic sweep/submapping path for recognizably four-sided internal domains;
- boundary-layer fronts plus the existing medial graph for multi-body and
  non-sweepable residual cores.

The classical periodic hill is the internal-flow acceptance fixture. The
30P30N geometry remains the difficult external-flow fixture. Neither may be
special-cased by name or coordinate.

High-order/spectral geometry is out of scope for this iteration. Preserve exact
input point lists and BlockDrawer edge curves, but optimize the ordinary
blockMesh-style transfinite grid that BlockDrawer already previews.

## Important diagnosis from the current result

The current `block_layout.py` assumes every Voronoi cell is an annulus and every
patch has one wall side, one medial-ring side, and two straight spokes. That
restriction guarantees conformity but cannot express:

- interior-only connecting blocks;
- a feature fan or C-grid seam at a sharp trailing edge;
- splitting one valence-six medial junction into lower-valence singularities;
- separatrix reconnection, strip collapse, or general core patches;
- a simply connected internal-flow channel without an artificial farfield.

The four valence-six junctions in the 30P30N result are mathematically valid:
the three-hole fluid domain has Euler characteristic -2, and the four vertices
carry the required total negative index. They concentrate that index, however,
and are not automatically the best quality layout. Likewise, the approximately
176-degree corners at sharp trailing edges are not repairable by sliding the
existing gates; the topology needs additional incident sectors or a C-grid
seam.

Do not try to solve these limitations merely by increasing coordinate-descent
sweeps, weakening quality limits, adding more wall samples, or assigning more
cells to the same topology.

## Existing periodic-hill fixture

`synthetic_cases.periodic_hill()` now supplies a first-class internal domain:

- hill height `H = 1`;
- streamwise period `Lx/H = 9`;
- flat upper wall `Ly/H = 3.036`;
- the classical six-piece Almeida lower-wall polynomial, mirrored across the
  period;
- directed `bottom_wall`, `periodic_right`, `top_wall`, and `periodic_left`
  boundary chains forming one anticlockwise loop;
- reciprocal left/right cyclic metadata and translation `(9, 0)`.

`PeriodicHillGeometryTests` locks down the geometry, loop orientation, patch
roles, and periodic mapping. Do not replace it with a sinusoidal hill or a
different parameterized-hill variant. You may refine its data classes if the
new domain API requires it, but retain those invariants and tests.

The formula source is the Almeida baseline reproduced in Appendix A of Gehrke
and Rung, *International Journal for Numerical Methods in Fluids* 94 (2022),
DOI `10.1002/fld.5085`. The standard domain dimensions are also catalogued by
the NASA Turbulence Modeling Resource periodic-hills dataset.

## Required architecture

### 1. Explicit planar-domain model

Add a research-only domain representation independent of the current
body-site list. It must contain:

- one oriented outer loop and zero or more oriented hole loops;
- stable named boundary chains that partition those loops;
- boundary role/type;
- reciprocal periodic-pair metadata and translation where applicable;
- original point-list geometry without raster replacement;
- deterministic normalization under translation, rotation, and uniform scale.

An adapter must convert the existing repeated `--curve NAME=PATH` external-flow
input into this representation, so all current cases keep working. An internal
domain must not acquire a fabricated farfield or be interpreted as a solid
body.

Validate connected endpoints, loop orientation, absence of adjacent duplicate
points, hole containment, nonintersection, unique names, reciprocal periodic
pairs, and geometric compatibility of paired chains.

### 2. General quadrilateral patch graph

Introduce a research intermediate representation for topology before building
`block_complex.Complex`. It must support arbitrary quadrilateral faces and not
encode the annular wall/ring/spoke pattern in its data model. Represent at
least:

- topological vertices with geometric constraints (fixed point, boundary-chain
  parameter, guide-curve parameter, or free interior point);
- shared edges with an oriented geometric path and boundary role;
- quadrilateral faces;
- vertex valence and discrete index/charge;
- periodic correspondence between boundary vertices and edges;
- provenance explaining which algorithm or discrete operation created an
  entity.

Provide deterministic validation for Euler characteristic, face-edge
incidence, boundary cycles, periodic compatibility, duplicate edges, hanging
vertices, orientation, and geometric intersections. Conversion to the existing
`Complex` and then to `MeshModel` must preserve identity and curve orientation.

Keep the existing annular layout as one producer/adapter where it remains
useful; do not make it the general representation.

### 3. Sweep/submapping candidate for internal flows

Implement a generic detector and constructor for a four-sided domain: two
opposite guide/wall chains connected by two compatible end chains. A
translational periodic pair is one valid kind of compatible end pair, but the
code must also be testable with ordinary inlet/outlet chains.

For such a domain:

- establish a monotone correspondence between the two long chains;
- propose streamwise cuts at geometric features, curvature/turning changes,
  metric-length limits, and intervals where a coarser block would fail quality;
- create an H-grid-like core with shared cut identities;
- preserve matching topology and cell counts on periodic sides;
- reject non-monotone, crossed, or inverted mappings with structured reasons.

This is a general sweep/submapping algorithm. It must not look for the words
`hill`, `bottom`, or `top`, assume that the periodic vector is horizontal, or
use the periodic-hill breakpoint coordinates as topology cuts. Rotated,
translated, uniformly scaled, and reversed equivalent domains must produce an
equivalent topology signature and quality.

### 4. Boundary-layer fronts

Add an explicit near-wall topology stage. For each selected wall chain, build a
clearance-limited front inside the fluid. A reasonable starting law is

```text
front_height(s) = min(requested_height, clearance_fraction * local_clearance(s))
```

but implement it through documented options and robust geometric checks. Use
wall normals and the distance/medial information already available. Prevent
self-intersection, front crossing, inverted sectors, and collisions between
fronts. Where a requested layer cannot fit, reduce it smoothly or emit a
structured failure; never silently cross another boundary.

For a sweepable internal domain, create separate wall-layer bands and a core
between their fronts. For closed smooth bodies, create an O-grid-like band. At
sharp convex wall features, especially zero-thickness cusps, generate and rank
feature-fan or C-grid-seam candidates. A user-supplied preferred flow/wake
direction may guide candidate ranking, but correctness must not depend on one.
Without a direction, try deterministic geometric candidates and report their
scores.

Smooth wall vertices should remain topologically regular wherever possible.
Do not claim that keeping a single spoke at a cusp is a boundary layer.

### 5. Residual-core construction and discrete moves

Retain the generalized medial/Voronoi graph as a gap-aware scaffold for
external and non-sweepable regions. Layer fronts, rather than physical walls,
should become the core boundaries after the wall bands are peeled away.

Support at least these deterministic candidate operations in the general patch
graph:

- split a high-valence singularity into lower-valence singularities while
  preserving total index;
- insert a separatrix/cut between compatible constraints;
- create a wall-feature fan or C-grid seam;
- split a poor patch;
- collapse or merge a short regular strip when validity is preserved.

Use Euler/index constraints to reject impossible candidates before geometric
optimization. Rank valid candidates by mesh quality plus a modest complexity
penalty. Expose candidates and rejection reasons in JSON so an agent can make a
genuinely ambiguous choice.

A full global Ginzburg-Landau/MBO cross-field solver is not required in this
iteration. If Gmsh is available, an optional research adapter may use its mesh
or field as a candidate/oracle, but tests must skip cleanly without it and the
core BlockDrawer package must not depend on Gmsh. Preserve an extension point
for a future cross-field/separatrix producer.

### 6. Metric-based cell-count quantization and grading

Replace uniform counts with counts derived from a configurable size metric.
For the initially conformal, no-T-junction patch graph, exploit the fact that
BlockDrawer's opposite-edge constraints partition edges into equality
components. Choose one positive integer for each component by minimizing
weighted error against the metric length of all member edges, optionally under
a global cell budget. Do not introduce a mixed-integer dependency merely to
solve equality-only components.

Assign configurable first-cell width, layer height, and growth ratio on
wall-normal edges. Respect canonical edge orientation when storing total
grading. Use BlockDrawer spacing links or equivalent constraints where they
correctly express equal physical endpoint spacing. Report unattainable sizing
requests instead of hiding them.

The topology and metric must work at multiple resolutions; no fixed count is
allowed to be the reason a test passes.

### 7. Evaluate the actual transfinite grid

The existing solver's `Evaluator` is corner-only and its acceptance threshold
is approximately two degrees. That is not a meaningful mesh-quality test.

For each candidate, construct a provisional `MeshModel` and reuse or faithfully
mirror `blockdrawer.preview` so evaluation sees the same edge-weighted
transfinite interpolation that BlockDrawer and `blockMesh` use. Evaluate every
sampled cell, including curved-edge wall cells. At minimum report:

- minimum signed/scaled Jacobian;
- minimum and maximum cell angle;
- maximum non-orthogonality;
- equiangle skewness;
- aspect ratio, separated into deliberate boundary-layer anisotropy and
  unintended distortion;
- size jumps across internal interfaces;
- wall-normal alignment and first-cell width error;
- the location, block, and logical indices of each worst cell.

No inverted sampled cell is admissible. Use validity-preserving optimization:
try a move, evaluate it, and revert it if it breaks topology, geometry, periodic
compatibility, or quality. Block-corner metrics may remain cheap early filters,
but they cannot be the final acceptance test.

## Agent-facing workflow

The purpose is not to make an agent place hundreds of vertices. Algorithms
should generate and rank valid candidates; an agent should decide only among
compact alternatives.

Add structured operations or research CLI commands that let an agent:

- describe the domain, boundary roles, layer fronts, singularities, separatrix
  graph, count components, and worst cells;
- request candidate topology moves at a named singularity or bad region;
- apply one candidate atomically;
- rerun count/grading assignment and quality optimization;
- render a focused diagnostic around a bad region;
- compare candidate scores and complexity.

Keep these in the research package until the design stabilizes. Do not expand
the production MCP surface with experimental operations yet.

## Required tests and acceptance runs

Keep all current research and production tests passing. Add focused tests for:

1. planar-domain validation and boundary-chain normalization;
2. reciprocal periodic-pair geometry under arbitrary rigid transforms and
   uniform scaling;
3. a rectangular straight channel with inlet/outlet boundaries;
4. a rotated or reversed version of that channel;
5. the supplied classical periodic-hill fixture;
6. an offset front around a smooth closed body;
7. two walls whose requested fronts must be clearance-limited;
8. a sharp cusp that produces more than the current single-spoke topology;
9. Euler/index preservation when splitting a valence-six singularity;
10. cell-count equality-component quantization;
11. full sampled-grid quality detecting a defect that four-corner quality
    misses;
12. session round-trip, rendering, validation, and `blockMeshDict`
    serialization for both one internal and one external case.

### Periodic-hill acceptance

Generate an ordinary BlockDrawer session for `synthetic_cases.periodic_hill()`.
It must:

- represent a simply connected internal domain with no artificial farfield and
  no solid-body hole;
- assign `bottom_wall` and `top_wall` as walls;
- assign `periodic_left` and `periodic_right` as a reciprocal cyclic pair;
- preserve translationally matching periodic vertices, edges, cell counts, and
  grading;
- contain explicit near-wall bands and a sweepable core;
- have no hanging vertices, overlaps, uncovered fluid region, or inverted
  sampled cells;
- reload, render, validate, and serialize to `blockMeshDict`;
- remain equivalent after translation, rotation, uniform scaling, and reversal
  of the complete boundary representation.

Add a reproducible `make research-periodic-hill` target and write its analysis,
session, rendering, and `blockMeshDict` beneath the ignored research output
directory.

### 30P30N acceptance

Regenerate 30P30N through the new domain and patch-graph path. Preserve the
stable medial relationships unless a changed graph has a documented,
resolution-independent justification. Add wall-layer bands and general
sharp-feature handling; do not special-case airfoil names, element count,
coordinates, or chord direction.

The current baseline has 44 blocks and BlockDrawer reports 49 warnings, about
10.27 degrees minimum first-cell angle, 88.41 degrees maximum
non-orthogonality, 0.982 equiangle skewness, 56.9 maximum corner-cell aspect
ratio, and an 87.2 maximum interface-size ratio. The new result must be compared
against that baseline using the same quality code. It need not be
production-ready in one iteration, but it must materially improve the sampled
grid and interface metrics without hiding wall nodes, coarsening the quality
evaluation, weakening validation, or changing the physical geometry. Report
which remaining defects require another topology change rather than claiming
success from corner-only validity.

## Generality and dependency rules

- No branch may inspect case names such as `periodic_hill`, `slat`, `main`, or
  `flap` to choose topology.
- Do not assume a horizontal streamwise direction, a unit scale, a fixed body
  count, or a particular number of medial junctions.
- Algorithmic tolerances must derive from domain scale, local clearance,
  curvature, metric size, or numerical precision.
- Keep NumPy, Pillow, Gmsh, sparse solvers, and other research dependencies
  confined to `experiments/agentic_topology/`.
- Production BlockDrawer and its normal tests remain Python-standard-library
  only, with Pillow still optional solely for PNG rendering.
- Do not weaken `MeshModel.validate()` or quality checks to admit generated
  results.
- Generated geometry, images, sessions, OpenFOAM cases, and caches stay out of
  Git.

## Failure policy and deliverables

This is research. A precise failure is preferable to a false success. When a
case cannot be completed, keep the implementation executable and emit the
smallest failed region, constraints, candidate operations, Euler/index balance,
metric targets, worst sampled cells, and exact rejection reason. Do not emit a
partial session as though it were complete.

Deliver:

- the explicit domain model and general patch-graph representation;
- the sweep/submapping and boundary-layer-front stages;
- initial discrete topology moves and metric quantization;
- sampled transfinite-grid quality evaluation;
- agent-readable diagnostics and atomic research operations;
- periodic-hill and improved 30P30N acceptance artifacts;
- documentation of the algorithm actually implemented and remaining limits;
- passing research and complete BlockDrawer test suites.

Spend effort on executable geometry, topology invariants, objective functions,
and reproducible diagnostics. Do not spend the quota writing another literature
review.
