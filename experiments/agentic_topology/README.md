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

`make research-periodic-hill`, `make research-30p30n`, `make research-cases`
and `make research-test` run exactly those. The exit code is 1 for anything
short of `resolved` - which includes a valid, untangled result that misses a
declared quality target - and the run prints which of `topology_valid`,
`untangled` and `within_quality_targets` failed. The PNG and the JSON are always
written so the result can be read either way; the session is written whenever
the result is `admissible`.

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

4. **Sharp-feature cavities** (`fan_cavity.py`). This stage runs on the
   assembled patch graph, after whichever producer wrote it, and it reads only
   incidence, provenance and geometry - so it is listed here, with the layers it
   repairs, but it happens after steps 5 and 6.

   A wall vertex whose fluid
   sector is much wider than 180 degrees cannot sit inside a block edge: its
   single offset point lies on the bisector, which leans away from both wall
   sides, and the two band blocks that share it fold. The feature needs *more
   incident sectors*, which is a discrete change - and a discrete change to a
   planar block topology only means anything if the replacement is valid **in
   the plane**, not merely well shaped on its own faces.

   So a feature owns a **cavity**: the faces incident to it, grown across
   interior edges until the wall band of another chain would be absorbed. The
   cavity's ordered boundary comes from patch-graph incidence, never from a
   label; everything inside it may be rewritten and everything outside is fixed
   context. Typically the cavity is the two band blocks plus the two core
   patches behind them, an eight-corner cycle
   `feature, wall, front, ring, ring, ring, front, wall`.

   Alternatives are generated as *templates* written against that cycle and the
   position of the feature in it - `band` (two sectors), `seam` (three sectors:
   one front vertex per incident wall side plus a wedge to the medial anchor),
   `fan2`..`fan4` (a fan contained in the band), and `through_fan3` (a short
   C-grid-style cut that removes the front vertex and reaches between the two
   neighbouring medial vertices). Each is placed by intrinsic parameters only:
   a fraction of the fluid sector and a fraction of the room available along
   *that ray*, so a placement is invariant under translation and rotation and
   equivariant under a uniform scale.

   Every candidate is built on a **copy** of the graph and checked before
   anything is committed:

   * every new vertex strictly inside the cavity;
   * every affected face strictly convex, positively oriented, and above a hard
     floor of 0.15 on the scaled corner Jacobian - a corner of about 8.6
     degrees. The floor used to be 0.02, a 1.1 degree corner; every synthetic
     fixture is unchanged for any floor up to 0.2, and on 30P30N the old value
     admitted seam wedges with 3.7 and 6.7 degree corners at the flap and main
     trailing edges;
   * the replacement faces tile the cavity exactly, compared against the
     *curved* cavity outline, not the straight corner quadrilaterals;
   * each cavity-boundary edge used once and each interior edge twice;
   * no new or modified path in a forbidden relation - proper crossing,
     T-junction, collinear overlap, unexpected touch or collapsed segment - with
     another new path, the cavity boundary, the unaffected graph, or the
     supplied body and outer-boundary point lists;
   * Euler characteristic and total index preserved on the whole graph;
   * no new structural problem anywhere;
   * no merge of a wall/front/ring cell-count component with a layer or core
     spoke component inside a boundary layer (see below).

   A rejected candidate leaves the graph with the topology signature it had. An
   accepted one is applied atomically. Adjacent features cannot rewrite the same
   faces: the second is refused with that reason. A replacement face is a band
   block only when it is bounded by a wall edge *and* a front edge; a seam wedge
   touches the wall at one vertex and a rebuilt core patch does not touch it at
   all, so both are core blocks. (Every new face used to inherit `layer` from
   the cavity, which drew the two rebuilt core patches behind the 30P30N flap
   as a band covering a third of the near field and judged them by the lenient
   boundary-layer aspect-ratio rule.)

   The **cell-count coupling** check is why a contained fan is not a boundary
   layer. BlockDrawer forces opposite edges of a block to share a cell count, so
   the equality components of that relation decide the resolution, and all the
   spokes of a wall chain are already one component. An odd-sector fan that
   stops at the front links a front or ring edge into that component, which
   forces the whole chain's near-wall thickness resolution to equal a streamwise
   one. That is a property of the topology, so it is measured and refused rather
   than discovered later as bad aspect ratios. `seam` and `fan2` do not couple;
   `fan3` and `through_fan3` do, and say so.

   A cavity is also opened, whatever the sector, wherever it would contain a
   block below the hard quality floor - a folded or slivered block is not a mesh
   block wherever it came from. Worst cavity first, deterministically.

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

   Before any count is chosen, `graph.sizing_structure` in the report states
   what the topology alone forces on every later sizing. Two edges in one
   equality component carry the same count whatever it is, so the ratio of
   their geometric lengths is a lower bound on the cell-size jump between them
   under uniform grading; a component holding both a tangential edge (wall,
   front, ring) and a normal one (band spoke, core spoke, sweep rib) ties a
   streamwise resolution to a wall-normal one; and a component holding both
   band spokes and core spokes ties the boundary layer's thickness resolution
   to the depth of the core behind it. These are properties of the topology
   and the vertex positions, measured before the metric is consulted, and they
   are the sizing statements a topology stage can be held to. The metric-based
   interface size ratio measured afterwards is largely a consequence of them:

   | case | worst length ratio in one component | roles in it |
   | ---- | ----------------------------------- | ----------- |
   | 30P30N | 855 | band spokes and core spokes |
   | `narrow_gap_tip` | 100 | band spokes and core spokes |
   | `sharp_bodies` | 35 | band spokes and core spokes |
   | `single_ellipse` | 4.7 | wall, front and ring of one O-grid sector |

   The first three rows are the seam wedge: its opposite sides are a band
   spoke and a core spoke, so committing it merges the chain's wall-normal
   band count with its core radial count all the way to the medial ring.

9. **Sampled-grid quality** (`grid_quality.py`). Block-corner measures are cheap
   early filters only. Every sampled node of every block is built with
   **BlockDrawer's own** edge-weighted transfinite interpolation - the private
   helpers of `blockdrawer.preview` are imported on purpose so the evaluation
   cannot drift from what the editor previews and `blockMesh` writes - and every
   sampled cell is measured: signed and scaled Jacobian, minimum and maximum
   angle, non-orthogonality, equiangle skewness, aspect ratio split into
   deliberate boundary-layer anisotropy and unintended distortion, wall-normal
   alignment, first-cell width error, interface size jumps, and the block and
   logical indices of every worst cell.

   Acceptance is reported in four separate terms, and the report carries an
   immutable copy of the limits it was judged against so it can never be
   compared with different defaults later:

   | term | meaning |
   | ---- | ------- |
   | `topology_valid` | incidence, planarity, exact domain coverage and periodic compatibility hold, and `MeshModel.validate()` accepted the session |
   | `untangled` | no sampled cell is inverted and the minimum scaled Jacobian is positive |
   | `within_quality_targets` | every **enabled** declared limit is met; `null` disables one explicitly rather than hiding it behind a huge number |
   | `admissible` | `topology_valid and untangled` - a real mesh, so a session is written for inspection even when it misses a target |

   `resolved` is `admissible and within_quality_targets`. Each miss is reported
   as a record giving the metric, the observed value, the limit, the comparison
   direction, the block and the location, so "below target" is never a summary
   word. A tangled, crossed or uncovered candidate writes no session at all;
   `run` exits 1 for anything short of `resolved` and prints which term failed.

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
| `cavities` | every sharp-feature cavity, its ordered boundary, and each validated alternative with its score components and rejection reasons |
| `candidates` | ranked candidate topology moves with Euler/index screening and rejection reasons |
| `apply --move ID` | apply exactly one candidate and print a before/after comparison |
| `focus --block bN` | a small diagnostic window and its blocks, optionally rendered |

Re-quantisation is the same `run` with different metric flags -
`--first-width-ratio`, `--core-size-ratio`, `--growth`, `--cell-budget` - and
`--split CELL:CUT` forces an anchor where an agent asks for one. Because the
run is stateless, that is also how `apply` realises a move.

`--max-wall-turning DEGREES` exposes the largest wall turning one annular
patch may span before it is cut (default 100). It is the single largest lever
on near-wall orthogonality, and it is not monotone, which is why the default
has not been changed:

| limit | `single_ellipse` blocks / misalignment | `concave_and_convex` | `narrow_gap_tip` misalignment |
| ----- | -------------------------------------- | -------------------- | ----------------------------- |
| 100 deg | 12 / 35.8 deg | admissible | 5.2 deg |
| 45 deg | 33 / 14.8 deg | **inverted cells** (min scaled Jacobian -0.334) | 5.5 deg |
| 30 deg | 45 / 6.3 deg | admissible | 21.7 deg |

A tighter limit adds anchors, and the extra anchors expose the same front and
gate placement problems the remaining limits describe.

`moves.py` generates and screens five families of candidate operation:
`wall_feature_cavity`, `split_patch`, `split_singularity`, `collapse_strip` and
`insert_separatrix`. Every candidate carries its index change, whether the
change is balanced, an estimated complexity and a score. Moves this iteration
cannot construct are still generated and screened, and carry `implemented:
false` with the exact reason - which is more useful to an agent than silence.

`wall_feature_cavity` is not a proposal: each one has already been constructed
on a copy of the graph and validated completely, so an agent chooses among
alternatives that are known to work rather than placing vertices. Applying one
sets `cavity.choices` - a `(feature label, template name)` pair - and re-runs,
which keeps the command line stateless. A refused alternative carries the exact
reason, including the two conflicting entity ids for a crossing.

## Results

### Periodic hill (internal-flow acceptance)

`synthetic_cases.periodic_hill()` is the classical Almeida channel: hill height
`H = 1`, streamwise period `Lx/H = 9`, flat upper wall at `Ly/H = 3.036`, the
published six-piece lower-wall polynomial mirrored across the period.

| measure | value | declared limit |
| ------- | ----- | -------------- |
| blocks | 48 (32 boundary-layer band, 16 core) | |
| vertices / edges | 68 / 115 | |
| singularities | 4 - the four domain corners, each with one block | |
| sampled cells | 13 288, **0 inverted** | none admissible |
| minimum scaled Jacobian | 0.525 | must be positive |
| sampled angle range | 31.8 deg .. 148.3 deg | |
| maximum non-orthogonality | 58.3 deg | 70 deg - **passes** |
| maximum equiangle skewness | 0.648 | 0.85 - **passes** |
| aspect ratio (boundary layer / unintended) | 2.85 / 7.18 | 100 unintended - **passes** |
| maximum wall misalignment | 15.3 deg | 25 deg - **passes** |
| maximum first-cell width error | **0.387** | 0.25 - **misses by 55 percent** |
| maximum interface size ratio | **3.48** | 2.5 - **misses by 39 percent** |
| patches | `bottom_wall`, `top_wall` as `wall`; `periodic_left`/`periodic_right` as a reciprocal `cyclic` pair | |

So the periodic hill is `topology_valid`, `untangled` and `admissible`, and it
is **not** `resolved`: it misses two of the six declared limits, and the run
says which, by how much and where. Those two misses were there before and were
hidden by an acceptance rule that only looked for inverted cells. The limits
have not been moved to make them go away; the sizing work that would fix them is
listed under *Remaining limits*. The session, rendering and `blockMeshDict` are
still written, because an admissible result is worth looking at.

The domain is simply connected with no fabricated far field and no solid-body
hole. Periodic vertices, edges, cell counts and grading match by construction:
the far end rib is the exact translate of the near one, and the two ribs sit in
the same opposite-edge equality component. The session reloads with an identical
topology signature, renders, and serialises to a 112 kB `blockMeshDict` with
reciprocal `neighbourPatch` entries. The construction is invariant under
translation, rotation, uniform scaling and reversal of the complete boundary
representation, and the sharp-feature cavity stage finds nothing to do on it -
the four domain corners carry 90 degree sectors.

### 30P30N (external-flow acceptance) - a smaller, precise failure

The stable medial relationships are preserved: the same **four junctions and six
branches**, with junction equidistance residuals at round-off, and **all three
elements carry a complete boundary-layer band**. 115 blocks, 142 vertices, 259
edges, total index -8 against the required `4 * chi = -8` for a three-hole
domain.

What each cavity did, taken from `output/30p30n.json`. "Before" is the worst
block in the cavity as the producer left it; "after" is the worst affected face
of the best candidate, judged against the 0.15 floor:

| feature | sector | before | best candidate | after | outcome |
| ------- | ------ | ------ | -------------- | ----- | ------- |
| `main` trailing edge | 352.2 deg | -0.209 | `seam` | 0.061 | **refused**: a 3.5 degree corner; `band` 0.043, every fan non-convex. The producer's folded construction stays and is reported as two non-convex core patches |
| `slat` trailing edge | 351.8 deg | -0.177 | `seam` | 0.252 | applied. **`fan3` scored higher and was refused for merging the chain's cell-count components**; `through_fan3` valid at 0.245 |
| `slat` cusp | 335.2 deg | -0.026 | `seam` | 0.291 | applied; `band` valid at 0.179, `fan3` refused for the same merge |
| `flap` trailing edge | 284.2 deg | -0.056 | `seam` | 0.064 | **refused**: a 3.7 degree corner; everything else non-convex. The producer's folded wedge stays and is reported |

At the former floor of 0.02 the two refused seams were accepted, which is how
the previous iteration could say that every sharp feature carried a valid
construction: the wedge from the flap trailing edge reaches back to a medial
anchor half a chord away and closes with a 3.7 degree corner there, and the
main trailing-edge wedge is 0.005 long with a 6.7 degree corner. Those are not
mesh blocks, so the stage now says so. What the two refusals mean is that the
seam template, which reaches from the feature to the medial ring, is the wrong
construction wherever the ring is far away or squeezed into a gap; the next
alternative needs a wake-style cut rather than a wedge.

The slat trailing edge is still the case the cavity design was built for: the
**highest scoring** candidate there is a contained three-sector fan, and it is
refused - not for anything visible in its own five faces, which are fine, but
because committing it would put a front and a ring edge into the same
cell-count component as the chain's band spokes. A lower-scoring but
structurally sound seam is applied instead.

The run is **not `topology_valid`**, for five localised reasons: the three
non-convex faces above, and two relations between a layer front and the medial
scaffold behind it:

| remaining problem | where | cause |
| ----------------- | ----- | ----- |
| `touching_edges` between a `wall` edge and a `front` edge | `(0.6457, 0.0263)`, main-element vertex 40 | the 90 degree concave corner at the top of the cove cut. The tangent-disk feature size is exactly zero at a reflex vertex, so the front height is 0.0 there and the neighbouring stations rise linearly from it. `peanut_body` reproduces this |
| `crossing_edges` between a `core_spoke` and a `front` edge | `(0.8020, 0.0221)`, in the flap gap | the gap is 0.0104 wide and the main/flap medial branch passes 0.006 from the main trailing edge. Both bands, both half-core strips and the medial ring are stacked inside it; the flap front's height cap changes threefold between a gate and the interior, so the front bulges past the straight spoke. `skimming_tail` is the small version |

The second one is exactly the kind of defect a four-corner test cannot see - the
corner quadrilateral there is convex - and it is only visible because edges that
share a vertex are checked against each other and because the front is compared
as the polyline it really is.

A front is already capped at nine tenths of the distance to the core scaffold,
which is a guard against reaching past it; that guard does not help here because
the offending bulge is between two gates where the *gate* cap - a third of the
gate-to-ring distance - is much tighter than the interior one. Making the two
consistent was tried and **measured**, and it is worse, which is why it was not
adopted:

| interior cap | 30P30N graph problems | `concave_and_convex` |
| ------------ | --------------------- | -------------------- |
| 0.9 of the distance to the ring (adopted) | 2: one touch, one crossing; all four features repaired | valid, 0 inverted cells |
| 0.5 | 6: two non-convex faces, one touch, three crossings; only three features repaired | - |
| 0.35, the same as the gate cap | 6, the same shape | 2 inverted sampled cells |

Thinning the band everywhere moves the defect rather than removing it: the main
element's trailing-edge cavity then has no valid alternative at all. The real
fix is to let the core spoke follow the scaffold instead of being a straight
line, which needs an interior guide curve and is listed as a remaining limit.

### Baseline comparison

| measure | 44-block baseline | pre-cavity attempt | cavity stage, floor 0.02 | this iteration, floor 0.15 |
| ------- | ----------------- | ------------------ | ------------------------ | -------------------------- |
| blocks | 44 | 107 | 115 | 115 |
| bands | none | slat and main only | all three elements | **all three elements** |
| sharp features with a valid construction | 0 | 1 of 4 | 4 of 4, two of them 4 degree slivers | **2 of 4**, honestly |
| non-convex faces | 0 reported (the test was four corners) | 4 | 0 | 3 |
| edge crossings | not checked between edges sharing a vertex | 10 | 1 | **1** |
| inverted sampled cells | **3** | no session | no session |
| minimum sampled scaled Jacobian | -0.197 | no session | no session |
| maximum interface size ratio | 87.15 | no session | no session |
| block-corner warnings | 49 | | |

The previous iteration's 30P30N session is measured with the same code in
`output/baseline-30p30n-grid.json`. Note that the session it measures was
written to `output/30p30n-session.json` by that older run: because the current
run is not admissible it writes no session at all, so **if that file is present
it is the old baseline, not a new result**. The two facts about it worth stating
are the ones block-corner quality could not see:

* `blockdrawer.quality` reported 49 warnings, 10.27 deg minimum first-cell
  angle, 88.41 deg maximum non-orthogonality, 0.982 equiangle skewness, 56.9
  maximum corner-cell aspect ratio and an 87.2 maximum interface size ratio;
* the sampled transfinite grid contains **three inverted cells**, a minimum
  scaled Jacobian of -0.197 and a 269 deg cell angle. The topology was valid;
  the mesh it defines was not.

That is why this iteration emits no 30P30N session. It is closer - every sharp
feature is now a valid construction, all three bands are built, and the graph
problems are down from ten crossings (and, in the attempt before the fans, four
folded faces) to one touch and one crossing - but a topology with an edge
crossing is not a mesh, and writing it out as though it were would repeat
exactly the mistake the baseline made.

## Generality

No step inspects a case, chain or body name to choose topology. There is no
assumed streamwise direction, unit scale, body count or junction count; every
tolerance derives from the domain scale, the local feature size, curvature,
metric size or numerical precision. The self-tests cover one ellipse, two
circles of unequal radius, three rotated ellipses, a concave body with a convex
one, four bodies, a body with a sharp tip, a sharp tip aimed into a narrow gap
beside a second body, a body whose only sharp features are two concave corners,
a sharp tail skimming a second body, a rectangular channel with inlet and
outlet, and the periodic hill - plus permuted, reversed, translated, rotated
and 1000x scaled copies.

`narrow_gap_tip` is the sharp-feature regression fixture. Its tip carries a 315
degree fluid sector and the medial scaffold bends hard around the 0.11-wide gap,
so the room the tip has is set by the neighbouring body's front and by the
medial ring - both outside the two blocks being replaced. Built without the
cavity stage the topology has two non-convex faces; with it the feature carries
a valid three-sector seam and the graph is clean. The cavity unit tests use a
hand-built four-row strip whose bottom wall carries a spike, so every coordinate
in them is written down in the test file.

Two fixtures reproduce the 30P30N failure modes in small geometry and are
**expected to fail** today. `peanut_body` is the union of two orthogonal
circles: smooth everywhere except two 90 degree reflex corners at the waist.
No disk can be tangent to a wall at a reflex vertex, so the tangent-disk local
feature size the layer stage uses is exactly zero there and the front collapses
onto the wall; the band cannot be built and the reported failure names the two
waist gates. `skimming_tail` is a sharp tail whose lower flank runs
horizontally two percent of the scale above a hull whose highest point lies
just downstream of the tip, the configuration a deployed flap makes with the
main element; the hull's band cannot be built in the slot. Each carries a
stable test on where the failure is located and an `expectedFailure` on
admissibility, so the moment a construction handles the fixture the suite
reports an unexpected success and the test has to be flipped. Every synthetic
fixture gives the same block graph at raster widths 400, 700 and 1000, and a
test guards that; 30P30N does not (see the remaining limits).

## Dependencies and limits

NumPy and Pillow are research dependencies only and must never become
BlockDrawer runtime dependencies. No `numpy.linalg` factorisation and no BLAS
level-3 product is used: `linalg_lite.py` carries a small Gaussian elimination
and a conjugate-gradient Laplacian solve, so the prototype also runs on
installations whose LAPACK/BLAS build is broken.

### Remaining limits

* **The front and the medial scaffold are not yet reconciled.** A front is
  capped at a fraction of the local feature size, at the wall's own radius of
  curvature, at the gate-to-ring distance *at each gate*, and at nine tenths of
  the distance to the ring elsewhere. Those caps are not consistent with one
  another, so between two gates a front can bulge past a core spoke that starts
  at one of them - which is the remaining 30P30N crossing. Making them
  consistent at the tighter value was measured and costs two inverted sampled
  cells on `concave_and_convex`; the real fix is to let the core spoke follow
  the scaffold rather than a straight line, which needs an interior guide curve.
* **The front collapses onto the wall at every concave wall corner.** The
  band height is capped by the largest disk tangent to the wall, and no disk
  is tangent at a reflex vertex: the tangent-disk feature size is exactly
  zero at any wall vertex whose fluid angle is below about 157 degrees, and
  roughly the distance to the corner nearby. The remaining 30P30N
  `touching_edges` problem is main-element vertex 40, the 90 degree corner at
  the top of the cove cut, where the front height is 0.0. The internal
  producer uses the same function, so a step or cavity in a channel wall
  would fail the same way; `peanut_body` reproduces it. Re-applying the sharp
  floor after slope limiting was tried earlier and **measured** to make things
  worse, which is consistent: the cause is the definition, not the limiter.
  The fix is the eroded-domain boundary - the level set of the wall distance,
  trimmed at the medial axis - which gives a mitre at a concave corner and one
  consistent cap in a narrow gap.
* **The seam wedge ties the band count to the core depth.** Its opposite sides
  are a band spoke and a core spoke, so every sharp feature repaired with a
  seam merges the chain's wall-normal band count with its core radial count.
  `graph.sizing_structure` reports it as `band_core_depth_couplings` with the
  length ratio it forces (855 on 30P30N). The cavity coupling check does not
  refuse it yet, because every sharp feature currently depends on the seam and
  the through-cut alternative is built for three sectors only.
* **The 30P30N block graph follows the raster.** Doubling the width to 1400
  leaves the four junctions identical to four decimals but gives 106 faces
  and 18 singularities against 115 and 24, with different cavity templates
  chosen: the anchor set and the relaxation depend on the branch
  discretisation. Every synthetic fixture is raster-invariant, so this is not
  yet reproduced in a small case.
* **A band block may span 100 degrees of wall turning**, which is what gives
  the single ellipse its 36 degree wall misalignment under transfinite
  interpolation. `--max-wall-turning` exposes the limit; the measured table
  above shows why the default has not moved.
* **The through-cut cavity template is built for three sectors only.** More
  sectors need a transition strip between the sector chain and the core
  boundary; the template reports that it does not apply rather than guessing.
* **A fan that stops at the layer front is refused inside a band**, because it
  merges the wall-tangential and wall-normal cell-count components of the whole
  chain. That is the honest reason the contained fan is not a boundary-layer
  construction, and it is measured on the graph rather than assumed.
* **A cavity replacement straightens the interior front edges** of the two band
  intervals beside the feature it repairs. The supplied wall point list is
  untouched, because it lies on the cavity boundary and cavity-boundary edges are
  reused exactly.
* The medial core is one annulus per body; a cell whose ring has several
  disjoint components is reported, not decomposed.
* A band block's first cell follows the local band thickness, so the first-cell
  width varies inside a block wherever the clearance does - about 39 percent on
  the periodic hill, which is why it misses that target.
* Core spokes and sweep ribs are straight; no interior guide curve is fitted.
* Cross-field separatrix production and a global quantisation solver are not
  implemented. Counts come from equality components only, which is exact for a
  conformal graph with no T-junctions, but it is also why the periodic hill's
  interface size ratio is 3.48 rather than under 2.5: neighbouring components
  are chosen independently.
* The sweep detector needs four corners from chain joins or high-turning points;
  a four-sided domain supplied as a single smooth chain is reported as
  `too_few_corners`.

## Files

| file | role |
| ---- | ---- |
| `research_cli.py` | command line: `run`, `describe`, `cavities`, `candidates`, `apply`, `focus` |
| `colored_medial_axis.py` | compatibility entry point for the original external-flow command |
| `pipeline.py` | both producers end to end, and the JSON report |
| `planar_domain.py` | oriented loops, named chains, roles, periodic pairs, validation |
| `patch_graph.py` | the general quadrilateral patch graph, validation and coverage |
| `sizing.py` | size metric, equality-component quantisation, grading requests |
| `layers.py` | clearance-limited fronts, offset repair, band seams |
| `fan_cavity.py` | sharp-feature cavities, templates, validation, atomic application |
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
