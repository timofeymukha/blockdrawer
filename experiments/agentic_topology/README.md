# Agentic topology experiments

Research prototypes that are deliberately separate from BlockDrawer's
standard-library-only runtime. They have to earn their way into the model and
command API through concrete geometry experiments first.

## What this prototype does

It turns a **planar fluid domain** - an oriented outer loop, zero or more hole
loops, and named directed boundary chains - into a **conformal all-quadrilateral
block topology with explicit near-wall bands**, and writes it as an ordinary,
editable BlockDrawer session. Two domain families are supported by two
producers that share one intermediate representation:

| family | geometry | producer |
| ------ | -------- | -------- |
| external | zero or more disjoint closed bodies inside an outer boundary | exact generalized medial graph + wall bands |
| internal | one simply connected domain with named wall / inlet / outlet / symmetry / cyclic chains | sweep / submapping + wall bands |

```bash
python experiments/agentic_topology/research_cli.py run --case periodic_hill \
  --output out/hill.png --json out/hill.json --session out/hill-session.json

python experiments/agentic_topology/fetch_30p30n.py
python experiments/agentic_topology/research_cli.py run \
  --curve slat=experiments/agentic_topology/geometry/30P-30N-Slat-Normalized.dat \
  --curve main=experiments/agentic_topology/geometry/30P-30N-Main-Normalized.dat \
  --curve flap=experiments/agentic_topology/geometry/30P-30N-Flap-Normalized.dat \
  --width 700 --output out/30p30n.png --json out/30p30n.json
```

`make research-periodic-hill`, `make research-30p30n` and `make research-test`
run exactly those. The exit code is 1 when anything is left unresolved; the PNG
and the JSON are still written so the failure can be read.

## Pipeline

1. **Planar domain** (`planar_domain.py`). One outer loop, zero or more hole
   loops, partitioned into named directed chains that carry a role and, for a
   translational cyclic pair, reciprocal partner metadata and a translation.
   **The fluid is on the left of every chain**, so the outer loop is
   anticlockwise, holes are clockwise, and offsetting a chain to its left always
   moves into the fluid. Validation covers connected endpoints, orientation,
   coincident points, hole containment, self-intersection, unique names,
   reciprocal cyclic pairs and the geometric compatibility of paired chains.
   Normalisation makes translation, rotation, uniform scaling and reversal of the
   whole boundary representation produce the same structure. Two adapters feed
   it: the repeated `--curve NAME=PATH` external input (which is the only place a
   far field is fabricated) and an internal-flow case.

2. **Size metric** (`sizing.py`). One scalar field decides every count and every
   grading value: `size(d) = min(core_size, first_width + (growth - 1) * d)`,
   where `d` is the distance to the nearest wall. That is the boundary-layer
   geometric series in closed form, and the requested band height is the
   thickness at which the series reaches the isotropic core size.

3. **Boundary-layer fronts** (`layers.py`). For every wall chain a front is
   offset into the fluid by

   ```text
   height(s) = min(requested, clearance_fraction * local_feature_size(s))
   ```

   The *local feature size* is the radius of the largest disk tangent to the wall
   at `s` that still fits between the walls, found by bisection on the wall
   distance field - a property of the geometry, not of a raster. Only wall
   chains limit it, so a periodic end does not strangle the layer at the ends of
   a period. The height is also capped by the wall's own inside feature size
   (its radius of curvature), slope limited along the wall, floored at a few
   first cells, and shrunk globally while the offset self-intersects or crosses
   another boundary. Micro-loops that a large offset makes on a densely sampled
   concave stretch are flattened against a window-averaged wall direction.

4. **Sharp features.** A wall vertex whose fluid sector is wider than about 250
   degrees cannot sit inside a block edge: its single offset point lies on the
   bisector, which leans away from both wall sides, and the two band blocks that
   share it fold. Two constructions are implemented:
   * a **band seam** - one front vertex per incident wall side plus a wedge block
     back to the medial anchor, giving the feature **three** incident blocks
     instead of two. Index balanced: the wall vertex -1, the two front vertices
     +1 each, the medial anchor -1.
   * a **contained feature fan** - five band blocks replacing two inside the band
     hexagon, also index balanced.
   Both are measured before they are committed and both report a scaled Jacobian
   and corner angles either way. See *Remaining limits* for where they fail.

5. **External core** (`voronoi_graph.py`, `block_layout.py`, `patch_solver.py`,
   `external_topology.py`). The exact generalized medial graph is unchanged: a
   raster decides *connectivity only* and every coordinate is recomputed
   analytically - junctions by Gauss-Newton on the equidistance residuals,
   branches by a predictor/corrector walk with a clearance-proportional step.
   Each Voronoi cell is an annulus cut at anchors into four-sided patches, and
   bounded coordinate descent relaxes every anchor and gate against a scale-free
   objective. What is new is that the **band front, not the wall**, is the inner
   boundary of those patches.

6. **Internal core** (`sweep.py`). A four-sided reading of a simply connected
   domain is searched for: four corners are chosen from the domain's own chain
   joins and high-turning points, and a pair of opposite sides is accepted as the
   *ends* when they are a reciprocal cyclic pair or contain no wall. The
   correspondence between the two guides is a monotone map found by bounded
   relaxation of a dimensionless objective - rib orthogonality against both
   guides, smooth column spacing, and attraction of each cut to the feature that
   created it. Cuts come from accumulated guide turning, chain joins and the
   metric length limit of a column. A non-monotone or crossing mapping is
   rejected with a structured reason. For a periodic pair the far end rib is
   constructed as the exact translate of the near one and every rib vertex is
   projected onto its end chain.

7. **Patch graph** (`patch_graph.py`). Both producers write into one
   representation: topological vertices with geometric constraints (fixed point,
   boundary-chain parameter, guide-curve parameter, free interior point), shared
   oriented edges with a boundary role and provenance, quadrilateral faces,
   periodic vertex correspondence. It validates Euler characteristic, discrete
   index, face-edge incidence, boundary cycles, orientation, convexity, hanging
   vertices, duplicate and crossing edges - and an independent raster coverage
   test confirms the faces tile the fluid exactly, because `MeshModel.validate()`
   is not a planar coverage test.

8. **Counts and grading** (`sizing.py`). BlockDrawer forces opposite edges of a
   quadrilateral to share a cell count, so the edges fall into equality
   components. Each component gets the single positive integer that minimises the
   length-weighted squared error against the metric lengths of its members -
   which for an equality-only constraint is the weighted mean, rounded. An
   optional global budget scales every component by one factor. Band spokes then
   receive the requested first-cell width through `set_edge_grading`, and a
   request BlockDrawer refuses is reported rather than dropped. No mixed-integer
   dependency is used.

9. **Sampled-grid quality** (`grid_quality.py`). Block-corner measures are cheap
   early filters only. Every sampled node of every block is built with
   **BlockDrawer's own** edge-weighted transfinite interpolation - the private
   helpers of `blockdrawer.preview` are imported on purpose so the evaluation
   cannot drift from what the editor previews and `blockMesh` writes - and every
   sampled cell is measured: signed and scaled Jacobian, minimum and maximum
   angle, non-orthogonality, equiangle skewness, aspect ratio split into
   deliberate boundary-layer anisotropy and unintended distortion, wall-normal
   alignment, first-cell width error, interface size jumps, and the block and
   logical indices of every worst cell. **No inverted sampled cell is
   admissible.**

10. **Session** (`session_emit.py`). Only a graph with no problems and exact
    coverage becomes a `MeshModel`; it is validated, saved, reloaded, validated
    again, rendered headlessly and serialised to `blockMeshDict`. Wall edges keep
    the supplied point list, a circular far field keeps exact arcs, cyclic
    patches are paired reciprocally, and each wall chain is attached as a
    reference curve.

## Agent-facing operations

`research_cli.py` is stateless: an agent's decision is an option delta, never
hidden state.

| command | purpose |
| ------- | ------- |
| `run` | build and write PNG, JSON, session, rendering and `blockMeshDict` |
| `describe` | domain, boundary roles, layer fronts, singularities, separatrix graph, count components, worst cells |
| `candidates` | ranked candidate topology moves with Euler/index screening and rejection reasons |
| `apply --move ID` | apply exactly one candidate and print a before/after comparison |
| `focus --block bN` | a small diagnostic window and its blocks, optionally rendered |

Re-quantisation is the same `run` with different metric flags -
`--first-width-ratio`, `--core-size-ratio`, `--growth`, `--cell-budget` - and
`--split CELL:CUT` forces an anchor where an agent asks for one. Because the
run is stateless, that is also how `apply` realises a move.

`moves.py` generates and screens five families of candidate operation:
`wall_feature_fan`, `split_patch`, `split_singularity`, `collapse_strip` and
`insert_separatrix`. Every candidate carries its index change, whether the
change is balanced, an estimated complexity and a score. Moves this iteration
cannot construct are still generated and screened, and carry `implemented:
false` with the exact reason - which is more useful to an agent than silence.

## Results

### Periodic hill (internal-flow acceptance)

`synthetic_cases.periodic_hill()` is the classical Almeida channel: hill height
`H = 1`, streamwise period `Lx/H = 9`, flat upper wall at `Ly/H = 3.036`, the
published six-piece lower-wall polynomial mirrored across the period.

| measure | value |
| ------- | ----- |
| blocks | 48 (32 boundary-layer band, 16 core) |
| vertices / edges | 68 / 115 |
| singularities | 4 - the four domain corners, each with one block |
| sampled cells | 13 288, **0 inverted** |
| minimum scaled Jacobian | 0.525 |
| sampled angle range | 31.8 deg .. 148.2 deg |
| maximum non-orthogonality | 58.3 deg |
| maximum equiangle skewness | 0.648 |
| aspect ratio (boundary layer / unintended) | 2.85 / 7.18 |
| maximum wall misalignment | 15.3 deg |
| maximum interface size ratio | 3.48 |
| patches | `bottom_wall`, `top_wall` as `wall`; `periodic_left`/`periodic_right` as a reciprocal `cyclic` pair |

The domain is simply connected with no fabricated far field and no solid-body
hole. Periodic vertices, edges, cell counts and grading match by construction:
the far end rib is the exact translate of the near one, and the two ribs sit in
the same opposite-edge equality component. The session reloads with an identical
topology signature, renders, and serialises to a 112 kB `blockMeshDict` with
reciprocal `neighbourPatch` entries. The construction is invariant under
translation, rotation, uniform scaling and reversal of the complete boundary
representation - the straight-channel test asserts an identical topology
signature after all four.

### 30P30N (external-flow acceptance) - a precise failure

The stable medial relationships are preserved: the same **four junctions and six
branches**, with junction equidistance residuals at round-off. What is new is
that the slat and the main element now carry real boundary-layer bands - 39 of
the 101 blocks are band blocks - and that four sharp trailing-edge features
carry a working band seam, so each of those wall vertices has **three** incident
blocks instead of two. The size metric works too: on the band-free variant
(`--no-layers`) the maximum interface size ratio falls from **87.2 to 10.4**.

The run is still **not resolved**, and the reason is one thing, located exactly.

All three elements end in a sharp trailing edge whose fluid sector is 284, 335
and 352 degrees. Three constructions were built and *measured* there:

| construction | measured result at the 30P30N trailing edges |
| ------------ | ------------------------------------------- |
| plain two-block band | folds: the single offset point lies on the bisector, which leans 142 degrees away from the forward wall direction |
| contained five-block fan | scaled Jacobian about **-0.83**: the band hexagon at a cusp is itself degenerate and cannot hold five quadrilaterals |
| band seam plus a wedge to the medial anchor | works at the wall (three incident blocks) but pushes the defect outward: the medial anchor becomes valence five and the two core patches beside it become slivers |

The four remaining graph problems say exactly that - `b45`, `b47` and `b84` are
`annular core patch` faces with 179.3, 167.9 and 169.8 degree corners at
`(0.79, 0.02)` (the main element's trailing edge) and `(0.09, 0.00)` (the
slat's), and `b75` is the band block at the slat's cusp. The flap's seam is
rejected outright and its band is dropped, which the JSON records with the
measured corner angles.

The band-free variant is the cleanest comparison against the previous iteration,
and it is instructive:

| measure | previous iteration | this iteration, `--no-layers` |
| ------- | ------------------ | ----------------------------- |
| blocks | 44 | 56 |
| **inverted sampled cells** | **3** | **10** |
| minimum sampled scaled Jacobian | -0.197 | -0.259 |
| maximum sampled non-orthogonality | 269.4 deg | 269.7 deg |
| maximum interface size ratio | 87.2 | **10.4** |
| block-corner warnings | 49 | 58 |

Every inverted cell sits at a trailing edge. That is the finding: **the previous
30P30N topology was never a valid mesh** - its 176 degree block corners at curved
cusps fold the transfinite grid, which four-corner quality could not see - and no
amount of sliding gates, splitting patches or assigning cells repairs it, because
a 352 degree fluid sector shared by two blocks is a topological fact.

The rejecting invariants are, in order: `patch_graph`'s strict convexity for the
four core faces beside a seam, and `grid_quality`'s "no inverted sampled cell is
admissible" for the band-free variant. The topology change they demand is the
same one: a feature fan that reaches the layer front and cuts the core, which is
a separatrix. No partial session is written.

### Baseline comparison

The previous iteration's 30P30N session is measured with the same code in
`output/baseline-30p30n-grid.json`. Two facts about it are worth stating because
block-corner quality could not see them:

* `blockdrawer.quality` reported 49 warnings, 10.27 deg minimum first-cell
  angle, 88.41 deg maximum non-orthogonality, 0.982 equiangle skewness, 56.9
  maximum corner-cell aspect ratio and an 87.2 maximum interface size ratio;
* the sampled transfinite grid contains **three inverted cells**, a minimum
  scaled Jacobian of -0.197 and a 269 deg cell angle. The topology was valid;
  the mesh it defines was not.

## Generality

No step inspects a case, chain or body name to choose topology. There is no
assumed streamwise direction, unit scale, body count or junction count; every
tolerance derives from the domain scale, the local feature size, curvature,
metric size or numerical precision. The self-tests cover one ellipse, two
circles of unequal radius, three rotated ellipses, a concave body with a convex
one, four bodies, a body with a sharp tip, a rectangular channel with inlet and
outlet, and the periodic hill - plus permuted, reversed, translated, rotated and
1000x scaled copies.

## Dependencies and limits

NumPy and Pillow are research dependencies only and must never become
BlockDrawer runtime dependencies. No `numpy.linalg` factorisation and no BLAS
level-3 product is used: `linalg_lite.py` carries a small Gaussian elimination
and a conjugate-gradient Laplacian solve, so the prototype also runs on
installations whose LAPACK/BLAS build is broken.

### Remaining limits

* **A zero-thickness cusp still needs a separatrix.** A feature whose fluid
  sector approaches 360 degrees needs three or more incident blocks. Three
  constructions were implemented and *measured*: the plain two-block band folds;
  the contained fan scores a scaled Jacobian of about -0.83 because the band
  hexagon at a cusp is itself degenerate; and the band seam works at the wall but
  makes the medial anchor valence five, which turns the two core patches beside
  it into slivers (179 degree corners on 30P30N). The construction that closes
  the argument gives the feature one front vertex per sector, and those front
  vertices need matching cuts in the core, which propagate through the shared
  medial ring into the neighbouring cell: a separatrix. Tracing one is the next
  stage. Until then a chain whose seam is rejected falls back to a core without
  a band, and the JSON records the measured corner angles and the reason.
* The medial core is one annulus per body; a cell whose ring has several
  disjoint components is reported, not decomposed.
* A band block's first cell follows the local band thickness, so the first-cell
  width varies inside a block wherever the clearance does (about 36 percent on
  the periodic hill).
* Core spokes and sweep ribs are straight; no interior guide curve is fitted.
* Cross-field separatrix production and a global quantisation solver are not
  implemented. Counts come from equality components only, which is exact for a
  conformal graph with no T-junctions.
* The sweep detector needs four corners from chain joins or high-turning points;
  a four-sided domain supplied as a single smooth chain is reported as
  `too_few_corners`.

## Files

| file | role |
| ---- | ---- |
| `research_cli.py` | command line: `run`, `describe`, `candidates`, `apply`, `focus` |
| `colored_medial_axis.py` | compatibility entry point for the original external-flow command |
| `pipeline.py` | both producers end to end, and the JSON report |
| `planar_domain.py` | oriented loops, named chains, roles, periodic pairs, validation |
| `patch_graph.py` | the general quadrilateral patch graph, validation and coverage |
| `sizing.py` | size metric, equality-component quantisation, grading requests |
| `layers.py` | clearance-limited fronts, offset repair, seams and fans |
| `external_topology.py` | medial scaffold plus wall bands, written as a patch graph |
| `sweep.py` | four-sided detection, guide correspondence, H-grid core |
| `moves.py` | candidate discrete operations with Euler/index screening |
| `agent_ops.py` | describe / candidates / compare / focus for an agent |
| `grid_quality.py` | sampled transfinite-grid metrics through `blockdrawer.preview` |
| `voronoi_graph.py` | raster connectivity, exact junctions and branch tracing |
| `block_layout.py` | annular cells, anchors, gates, four-sided patches |
| `patch_solver.py` | relaxation objective, bounded coordinate descent, splitting |
| `session_emit.py` | `MeshModel` construction, session IO, signatures |
| `sites.py` | boundary curves, domain frame, canonical body order |
| `analysis_plot.py` | Pillow rendering of the analysis picture |
| `geometry2d.py`, `linalg_lite.py` | vectorised geometry and small solvers |
| `synthetic_cases.py`, `test_agentic_topology.py` | generated geometries and self-tests |
| `fetch_30p30n.py` | downloads the third-party acceptance geometry |

Downloaded geometry (`geometry/`) and generated artifacts (`output/`) are
git-ignored.
