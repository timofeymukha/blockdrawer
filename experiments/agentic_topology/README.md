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
declared shape target or whose topology forces an unacceptable size jump - and
the run prints which of `topology_valid`, `untangled`, `within_shape_targets`
and `sizing_feasible` failed. Misses of the sizing limits by the mesh the
default counts define are printed separately as informational, because counts
and grading are a later stage. The PNG and the JSON are always written so the
result can be read either way; the session is written whenever the result is
`admissible`.

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
   it: the repeated `--curve NAME=PATH` external input and an internal-flow
   case.

   **The outer boundary is explicit.** An external case either supplies it -
   `--outer NAME:ROLE=PATH`, repeated in anticlockwise order with consecutive
   chains joined end to end, so a C-shaped boundary is an upstream cap, two
   legs and an outlet - or has it fabricated from `--farfield-shape`: a circle
   (one chain, `--farfield-name`) or a rectangle whose four sides carry their
   own names and roles (`--farfield-sides`, default
   `bottom:farfield,outlet:outlet,top:farfield,inlet:inlet`, anticlockwise from
   the lower-left corner; `--farfield-box` places it absolutely instead of
   `--farfield-scale`). Every chain becomes its own patch with its role's
   patch type. The outer loop is one Voronoi site, and two rules make its
   chains meshable: every **chain break** is a gate whether or not the boundary
   turns there, and every **convex corner** of the outer loop is a reflex gate
   whose spoke follows the mitre of the distance level set, the same rule a
   wall's reflex corner obeys - the classical O-grid in a box, where two
   blocks share each corner at 45 degrees. No block edge straddles two patch
   names. A wall chain on the outer boundary is exported as a wall but gets no
   band from this producer, which is reported.

2. **Size metric** (`sizing.py`). One scalar field decides every count and every
   grading value: `size(d) = min(core_size, first_width + (growth - 1) * d)`,
   where `d` is the distance to the nearest wall. That is the boundary-layer
   geometric series in closed form. The requested band height is a separate,
   geometric input - a fraction of the domain scale, by default the 0.256 at
   which the default sizing's series reaches the core size - so the topology
   does not change with the sizing; the series height of the sizing actually in
   use is available instead (`layer_height_ratio=None`).

3. **Boundary-layer fronts** (`layers.py`). For every wall chain the front is
   the boundary of the eroded fluid domain - the level set of the wall
   distance - at a height that varies slowly along the wall,

   ```text
   height(s) = min(requested, clearance_fraction * far_clearance(s))
   ```

   The *far clearance* is the height at which the level set owned by `s`
   collapses against a wall part that is not locally adjacent: another body,
   or a distant part of the same wall. It is found by bisection on the wall
   distance field with the wall's own segments within an arc-length window of
   two probe heights excluded, and with a reflex vertex probed at its mitre
   point rather than at its plain offset point. For a smooth wall far from any
   corner it is the classical tangent-disk feature size; at a concave corner,
   where no disk is tangent and that feature size is zero, it is finite. Only
   wall chains limit it, so a periodic end does not strangle the layer at the
   ends of a period. The height is also capped by the wall's own radius of
   curvature on both sides - on the convex side because a short wall section
   would otherwise become a long front section, on the concave side because
   the offset of a bend of radius `R` at height `h` is an arc of radius
   `R - h` and the band blocks fold as `h` approaches `R` - by the arc length
   to the nearest gate at a reflex corner so no gate falls into the corner's
   shadow, slope limited along the wall, floored at a few first cells, and
   shrunk globally while the front would still self-intersect or cross another
   boundary. The concave cap is what the old tangent-disk law provided
   implicitly; a reflex corner and its shadow are exempt from it, because
   there the own-wall disk is the level set being trimmed, not bending.

   The per-vertex offset is the level set only where a vertex's own normal
   reaches it. In the shadow of a reflex corner, or of a concave bend tighter
   than the height, the offset points lie closer to the adjacent wall than the
   height; the level set there has one corner, the *mitre*, where the offsets
   of the wall on either side meet. Every trimmed vertex of a contiguous run -
   grown over the reflex vertices beside it, because a corner's own
   straight-edge mitre overshoots a curved level set and would otherwise split
   its shadow in two - maps to that one point, found on the run's bisector by
   bisection on the wall distance. A reflex corner is always a gate, its pin is
   never released by the layout relaxation, and its spoke runs along the
   bisector to the mitre; the two band blocks beside it therefore meet the wall
   at 45 degrees, which is valid but poor, and a corner block at the mitre is
   the natural cavity alternative still to be written.

   Local repair reduces the caps of every wall sample in a failing block,
   including both gates. Repeated hard failures request a patch split from the
   caller. On its final repair round the caller permits a bounded bisection
   search over one scale of the original caps. Every accepted sample passes the
   same front and band checks; an exhausted search reports the failing blocks
   and sampled scale. Trimming and the height floor are discrete, so this is
   not a proof that all possible heights fail.

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
   * no new equality component coupling a physical wall/front tangent count
     with a boundary-layer normal count (see below).

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
   stops at the front links a wall-front edge into that component, which
   forces the whole chain's near-wall thickness resolution to equal a streamwise
   one. That is a property of the topology, so it is measured and refused rather
   than discovered later as bad aspect ratios. `seam` and `fan2` do not couple;
   `fan3` and `through_fan3` do, and say so.

   This is a physical wall-band check, shared by cavity screening and final
   acceptance. Both producers copy the domain boundary roles to the graph;
   wall edges and connected fronts seeded by their layer spokes carry wall
   provenance. Inlet, outlet, symmetry, cyclic and far-field edges are not wall
   tangents just because the producer calls their construction role `wall`.
   Core spokes, ribs and gap rungs are not boundary-layer normals. A component
   mixing local core/ring direction labels remains visible in
   `tangential_normal_couplings` and `coupled`, but does not establish harmful
   band coupling. The additive `wall_tangential_normal_couplings` and
   `wall_coupled` fields supply the acceptance evidence: named wall chains and
   a tangent/normal edge pair in the same component, even outside the short
   worst-ratio table. Coupling different walls is still rejected. The length
   ratio limit of 20 still applies to every component. Old saved reports without
   physical evidence retain their conservative legacy verdict until remeasured.

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

   **Optional gap spanning** (`spanned.py`, `--span-gaps`) replaces the two
   half-core strips along a narrow body-body branch with one strip between
   the wall fronts. Each endpoint dissolves into an extended strip block,
   a mouth block and a merged far-field block. Its valence-six medial junction
   becomes two valence-five front vertices, preserving the total index.
   Body-to-far-field branches remain rings.

   Annular junction gates generally face the far field: simply connecting
   them or walking farther around the bodies makes straight rungs cross the
   bands. The mouth search instead walks the first medial branch interval
   towards the gap until the two wall-to-medial vectors oppose by at least
   110 degrees. Their closest wall points become mouth gates; junction gates
   move halfway towards the next interior gate. New ring anchors sit within
   the first adjacent ring intervals, at most 0.3 mouth widths from the
   junction, with matching far-field projections. The annular objective is
   not applied to these variables afterwards. If a mouth would pass another
   gate, collapse onto a sharp feature, or meet a third wall, it is refused.

   The entire trial, including gate movement, new anchors and rebuilt bands,
   is isolated. Graph validation, coverage, session emission and both sampled
   grids must pass before it replaces the original result. A failure restores
   the original layout and session as well as the graph, and appears under
   `medial.spanned_branches`. Grid validation remains mandatory for a span
   even with `--no-grid-quality`. Merged circular boundary edges follow the
   section containing the removed gate; selecting the opposite arc can leave
   a clean graph but invert the exported grid.

   **Anchors and graded gates.** A wall feature's anchor is the ring point
   its spoke should end on. The closest ring point is the default, since the
   spoke meets the ring squarely there, but it is not outward in general:
   beside a second body the ring passes close behind a foil and the closest
   ring point of its nose lay behind its tail, so the spoke would have run
   through the body; in front of a far ring every nose station has the same
   closest point; and a convex corner is the closest wall point of a whole
   ring arc, so its closest ring point was arbitrary. So the outward ray -
   the bisector of the two outward normals at the station - is cast to the
   ring as well, and its crossing replaces the closest point when its
   straight spoke is squarer to wall and ring by at least fifteen degrees
   (`RAY_MARGIN`); a convex or reflex corner always takes the ray, the outer
   boundary always keeps the closest point. And the spoke lengths at a
   patch's two gates may differ by at most a factor of four
   (`LayoutOptions.max_clearance_ratio`): leaving a narrow gap the clearance
   grows quickly, and one block spanning a sliver of a spoke and a slab had a
   band whose graded cells inverted; cutting until neighbouring spokes are
   within the factor grades the gates out of the gap. Four rather than three
   because a medial junction beside the gap of `two_circles` has 3.8 times
   the gap's clearance, and a cut there was accepted or refused by the
   raster's noise on the ring floor, which broke invariance under rotation. These two rules moved
   the default corpus, deliberately: see *Corpus re-baseline* under Results.

   **Optional wake separatrix** (`wake.py`, `--wake`). The seam is the wrong
   element at a trailing edge: the flow leaves along the wake, so the two band
   blocks beside the edge should continue downstream as a two-sided *wake
   band* and the far-field core should be cut open along it - the C-grid. The
   construction keeps the medial scaffold and adds one straight scaffold line
   from the feature, along the bisector of its fluid sector (or a fixed
   `--wake-direction`), through the body's own ring to the outer boundary.
   The ring crossing is an ordinary anchor pinned on both sides - the edge on
   the body, the exit on the outer boundary - so the annular producer already
   writes the wake beyond the ring as a far-field spoke and gives the edge its
   seam. Once the band exists, the seam points are carried downstream
   parallel to the wake as the *wake fronts*, which cross the ring and the
   outer boundary; the wedge and the four patches around the anchor are then
   rewritten into eight: two wake blocks at the edge, two beyond the ring, and
   the four neighbouring patches ending on the wake fronts. The edge carries
   four blocks meeting at right angles, the wake band inherits the wall band's
   normal count through the band spokes, and the wake line lies in the core
   spoke component. Total index and Euler characteristic are unchanged. A
   neighbouring ring anchor inside the wake band - a far-field corner whose
   closest ring point is just behind the edge, because the medial ring turns
   sharply there - is slid along its branch until the layout's ring separation
   holds, and the bands are rebuilt once. A wake whose ring crossing lies on
   the branch between two bodies ends on the other body's band instead of
   the outer boundary (`target.kind` is `body`): the wake anchor pins the
   trailing edge on one body and the stagnation station - where the wake ray
   meets the wall - on the other, so the annular producer gives the second
   body a gate there whose spoke is the wake line beyond the ring; the two
   wake fronts cross the ring and land on that body's front a band width to
   either side of its front vertex, placed along the front rather than by
   continuing the straight fronts, since the wake meets the body along its
   normal, not along the wake direction; each landing point gets a band
   spoke to the wall, so the wake band wraps the nose as an embedded C-grid
   does, in twelve faces. Optional anchors that would crowd the wake anchor
   on the ring, or the landing gates on the wall, are dropped when the wake
   anchor is pinned. The count across the wake is then the count along the
   two nose pieces, as in every C-grid the count across the wake cut is the
   count along the surface; the structure report lists it as a
   `wake_landing_coupling`, not as a wall coupling. Wakes are tried in
   passes: one refused because the downstream body's own trailing edge still
   carried its seam wedge is tried again once that body's wake is accepted.
   A wake that would cross another medial branch, leave the feature's
   sector, meet the outer boundary across a chain break, or land where the
   downstream body has no room beside the stagnation gate is refused with
   its reason, and a trial is committed only when the complete result passes
   coverage, session emission and both sampled grids; otherwise the annular
   construction stays exactly as it was.

   **Single-body C-grid** (`cgrid.py`, with `--wake` and a C-shaped far
   field). For one body with one sharp trailing edge the medial ring is the
   wrong scaffold: at fifteen chords it sits seven chords out, every core
   block is a pie slice from a short wall section to a ring section many
   chords long, and behind the edge the body's level sets curve round while a
   C-grid's continue along the wake. The C-grid producer takes the annular
   layout's gates and builds the level sets of the *slit body* - the wall
   with its wake line - at geometrically growing heights (`CGridOptions.growth`
   3, up to `reach` 0.35 of the distance to the outer boundary): the band by
   wall-normal offsets, every higher level by arc-length-fraction mapping of
   the level below, so a sector never narrows with height. Each level runs
   from the *out* mitre round the body to the *in* mitre; the mitre points sit
   at the height from both the wall side and the wake line, the chain of them
   is the cross-wake line, and the wake band is the stack of levels along the
   straight wake lines to the outlet. The far field is the outer boundary
   itself: straight spokes to cap stations placed by the same fractions, and
   the two outlet corners are single-block corners (+1 each), which with the
   edge's four blocks (-2) keeps the annulus at total index zero. The result
   replaces the annular one only when the complete chain - graph checks,
   coverage, session, both sampled grids - is admissible; otherwise the reason
   is under `medial.cgrid`. The producer chooses its own gates: it builds a
   second layout from the same diagram with the options its blocks need -
   the band height as a geometric input, so a gate pair whose straight band
   block would have a corner under 30 degrees is cut (`LayoutOptions.band_height`,
   `band_corner`); wall turning of at most 45 degrees per block
   (`max_band_turning`), which is what keeps the band's misalignment within
   the target once the far-field spokes leave the outermost level; and the
   anchor floor judged against the wall gap as well as the clearance
   (`floor_by_wall_gap`), since its core is the body's own offsets and the
   medial ring may be many chords away. A band block that still folds names
   its wall interval and the patch there is split, as the annular assembly
   does, up to `band_repairs` times. The annular construction keeps the
   layout its own options build, so no default fixture moves. Rules that
   came out of it and hold everywhere: the inscribed-disk cap on the band,
   which is half the thickness on a thin body, is optional (`inscribed_cap`)
   and off in the C-grid; a far-field fan-out component - tangential, longest
   edge on a non-wall outer chain or the medial ring - is reported
   (`farfield_fanout_ratio`) but no longer fails `sizing_feasible`; and the
   band's spoke-crossing test leaves out the section segment that ends on the
   spoke, which under rounding registered as a crossing and thinned the block
   for nothing. Offset normals from the wall chord over a fraction of the
   height (`LayerOptions.normal_window`) are available but off: the window
   also smooths real bends - a cove's band folded with it - and the C-grid's
   levels, judged between fronts after the arc-length mapping, do not need it.

   **Hull far field** (`hull.py`, `--hull`, two or more bodies). The annular
   construction is right between bodies and wrong far from them: with the
   far field at fifteen chords its one medial ring sits seven chords out,
   every core block is a pie slice, and the far field's chain breaks gate
   the bodies at whatever wall point happens to be closest - which is what
   refused the tandem's front wake in a C-shaped far field. The hull producer
   does for a cluster what the C-grid does for one body. The *hull* is the
   level set of the cluster's wall distance - all bodies together - at
   `HullOptions.height_ratio` band heights (six, so the medial ring inside
   it, at half that, still allows the full band; capped by the body frame's
   radius), extracted by marching squares on a distance raster and projected
   onto the exact level set; below half the widest gap it is several curves
   and the height is raised. The hull carries the outer boundary's chains
   at the same fractions of the perimeter. The near field is then the
   annular construction run with the hull as its outer boundary, with the
   C-grid's band settings (band height as a layout input, 45 degrees of wall
   turning per block, inscribed-disk cap off), so bands, seams, cavities and
   wakes - one ending on a downstream body, one leaving through the hull -
   are all the existing producer's. Beyond the hull the level sets of the
   cluster at geometrically growing heights (`growth` 2 from the hull, up to
   `reach` 0.35 of the way to the outer boundary) are exact level sets of the
   same distance field - every level set beyond the hull is the hull offset
   by the height difference, since a distance function's level sets are
   parallel - with the hull's vertices carried outward by arc-length
   fraction, the blocks between levels judged for convexity, and the
   outermost level joined to the outer boundary by straight spokes. With a
   wake leaving the cluster the levels are the *slit hull*'s: cut where they
   lie inside the wake band's offset, ending on mitre points at the band's
   edge lines, the wake band's two halves and one strip per level and side
   running to the outlet, the levels mapped onto the cap and two wake-side
   blocks covering the legs, exactly the C-grid's far field with the wake
   band's own width in place of the trailing edge. Without one the far field
   is closed and each hull chain maps onto the outer chain of its name. The
   hull edges become interior fronts, and the whole graph is validated with
   the real domain; otherwise the annular construction runs and the reason
   is under `medial.hull`.

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

   Two grids are measured. The **shape grid** samples every block at a fixed
   number of uniform parametric fractions per side (`shape_cells`, default 8),
   so it describes the block map itself and is identical whatever counts and
   grading are chosen later; it is judged against the *shape* limits only:
   non-orthogonality, equiangle skewness and wall misalignment. The **counts
   grid** samples the graded mesh nodes the session defines and carries the
   *sizing* report: aspect ratio, interface size ratio and first-cell width
   error. Acceptance is reported in these terms, and the report carries an
   immutable copy of the limits it was judged against so it can never be
   compared with different defaults later:

   | term | meaning |
   | ---- | ------- |
   | `topology_valid` | incidence, planarity, exact domain coverage and periodic compatibility hold, and `MeshModel.validate()` accepted the session |
   | `untangled` | no sampled cell is inverted on either grid and the minimum scaled Jacobian is positive |
   | `within_shape_targets` | every **enabled** shape limit is met on the shape grid; `null` disables one explicitly rather than hiding it behind a huge number |
   | `sizing_feasible` | no opposite-edge equality component forces a geometric length ratio above `StructureLimits.max_length_ratio` (default 20) or ties a tangential resolution to a wall-normal one - the count-independent statements of `graph.sizing_structure` |
   | `admissible` | `topology_valid and untangled` - a real mesh, so a session is written for inspection even when it misses a target |
   | `within_sizing_targets` | **informational, not part of `resolved`**: the mesh the default counts and grading define meets the sizing limits |

   `resolved` is `admissible and within_shape_targets and sizing_feasible`: the
   topology stage is held to what it decides - the block map and the equality
   components - and not to the counts, which an agent sets later from flow
   considerations with `set_edge_cells`, `set_edge_grading` and spacing links.
   Each miss is reported as a record giving the metric, the observed value, the
   limit, the comparison direction, the block and the location, so "below
   target" is never a summary word; a structural failure names the component
   and its extreme edges instead of a block. A tangled, crossed or uncovered
   candidate writes no session at all; `run` exits 1 for anything short of
   `resolved` and prints which term failed, then the sizing misses as
   `sizing (informational)`.

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

The outer boundary is chosen the same way: `--farfield-shape circle|rectangle`,
`--farfield-scale`, `--farfield-sides`, `--farfield-box`, or an explicit
`--outer NAME:ROLE=PATH` per chain; `--wake` (with `--wake-direction DX,DY`
and `--wake-fluid-angle`) asks for the C-grid wake cut at every sharp
trailing edge. Re-quantisation is the same `run` with different metric flags -
`--first-width-ratio`, `--core-size-ratio`, `--growth`, `--cell-budget` - and
`--split CELL:CUT` forces an anchor where an agent asks for one. Because the
run is stateless, that is also how `apply` realises a move.

`--layer-height-ratio FRACTION` sets the requested band height as a fraction
of the domain scale (default 0.256, where the default sizing's wall-normal
series reaches the core size); it is a geometric input, so the block shapes
and the shape verdict do not change with the cell sizing.
`--layer-height-ratio series` uses the series height of the sizing in use. `--max-length-ratio RATIO` sets the
structural feasibility limit. `--max-wall-turning DEGREES` exposes the largest
wall turning one annular patch may span before it is cut (default 100). It is the single largest lever
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

### Explicit outer boundary

```bash
python experiments/agentic_topology/research_cli.py run --case single_ellipse \
  --farfield-shape rectangle --farfield-box=-4,-3,6,3 \
  --farfield-sides floor:symmetry,out:outlet,roof:symmetry,in:inlet
python experiments/agentic_topology/research_cli.py run --case single_ellipse \
  --outer cap:farfield=cap.dat --outer bottom:farfield=bottom.dat \
  --outer outlet:outlet=outlet.dat --outer top:farfield=top.dat
```

| case | outer boundary | blocks | outcome |
| ---- | -------------- | ------ | ------- |
| `single_ellipse` | circle (default) | 12 | admissible; shape misses unchanged |
| `single_ellipse` | rectangle, inlet/outlet/top/bottom | 21 | resolved; two 45-degree blocks per corner, four named patches |
| `single_ellipse` | C-shape: cap, two legs, outlet (`synthetic_cases.c_shaped_outer`) | 24 | admissible and within shape targets; far-field fan-out ratio 31 > 20 |
| `two_circles` | rectangle | 20 | resolved |
| every default fixture | circle | unchanged | unchanged block counts and verdicts |

The single-body rectangle used to be inadmissible for a reason unrelated to
the far field: the relaxation's anchor bounds on a *closed* medial branch
start after the seam and were never wrapped, so anchor positions crept past
the branch length and several ring stations sampled the same loop end point.
Positions on a closed branch now wrap; no default result moved.

The tangent-continuous cap/leg joins of the C-shaped boundary are gates
because they are chain breaks, and the two outlet corners are reflex gates of
the outer loop. Its remaining miss is the far-field fan-out length ratio,
which is the open threshold question below, not a defect of the boundary.

### A-airfoil at fifteen chords (single-body acceptance)

```bash
make research-a-airfoil     # downloads the Tohoku coordinates, C-shaped far field, --wake
```

The Aerospatiale A-airfoil (1593 supplied points, sharp trailing edge, 340
degree sector) at Re_c 2.1e6 and 13 degrees incidence, chord 1, in a C-shaped
far field of radius 15 about the trailing edge with the outlet one radius
downstream, the wake along (cos 13°, sin 13°) and the band at 0.15 chords,
1.6 times the measured suction-side boundary-layer thickness. Test options
(raster 420):

| quantity | annular producer before this work | C-grid producer |
| --- | --- | --- |
| verdict | inadmissible (177-degree core patch, wake refused) | **resolved** |
| blocks | 25 | 185 |
| levels | one ring at 7.5 chords | 0.15, 0.45, 1.35, 4.05, then the cap |
| gates on the airfoil | 8 | 35 |
| band height at the gates | 0.0005 to 0.15 | 0.15 everywhere |
| non-orthogonality (shape grid) | - | 51.0 degrees |
| wall misalignment | - | 12.4 degrees |
| minimum scaled Jacobian | - | 0.63 |
| far-field fan-out ratio (informational) | - | 93 |

With the Makefile's raster of 700 the same case resolves in 125 blocks from
23 gates (51 degrees of non-orthogonality, 23 degrees of wall misalignment,
scaled Jacobian 0.63): the gate count still follows the medial ring's
discretisation through the anchor floor, so the sector count is not yet a
function of the body alone.

The band used to collapse at the leading edge because one block spanned 98
degrees of nose turning and its straight corner quad had 7-degree corners;
the C-grid's own layout cuts a band block whose corners fall below 30 degrees
at the clearance-limited height and any block turning more than 45 degrees.
The band was also capped at half the local thickness by the inscribed-disk
rule, and its blocks were thinned by a spoke-crossing test that, through
rounding, counted the section's end on the spoke as a crossing. Each of
those was a separate defect, and each showed only on a thin body. The
crossing fix is the one change that reaches the annular construction: the
default corpus is unchanged block for block, and 30P30N goes from 116 to 115
blocks with its verdict unchanged, one spurious band repair fewer.

### Wake separatrix (opt-in)

```bash
python experiments/agentic_topology/research_cli.py run --curve tear=tear.dat --wake \
  --outer cap:farfield=cap.dat --outer bottom:farfield=bottom.dat \
  --outer outlet:outlet=outlet.dat --outer top:farfield=top.dat
```

Measured with the test options (raster 420, 240 coverage samples) on the
teardrop `synthetic_cases.teardrop((0, 0), 0.32, tip_ratio=2.2)`, whose tip
carries a 306-degree fluid sector:

| body and outer boundary | blocks | singularities | non-orthogonality | wall misalignment | min. scaled Jacobian | worst length ratio | verdict |
| ----------------------- | ------ | ------------- | ----------------- | ----------------- | -------------------- | ------------------ | ------- |
| teardrop, circle, seam | 18 | 0 | 63.0 | 63.0 | 0.455 | 6.4 | admissible, shape misses |
| teardrop, circle, **wake** | 22 | 3 | 43.7 | 5.5 | 0.723 | 12.1 | **resolved** |
| teardrop, C-shape, seam | 18 | 0 | 78.2 | 63.0 | 0.204 | 26.8 | admissible, shape misses |
| teardrop, C-shape, **wake** | 22 | 3 | 57.0 | 21.0 | 0.544 | 13.1 | **resolved** |

The three singularities are the two front vertices at the tip (index +1
each, as with the seam) and the tip itself, which carries four blocks instead
of the seam's three. On the C-shaped boundary the two outlet corners' anchors
had to be slid 0.43 and 0.82 along the ring to clear the wake band: their
closest ring points were just behind the tip, and left in place they made the
far-field patches beside the wake slivers with a length ratio above 200. The
wake band is as thick as the band at the tip, 0.037 here, because the
clearance cap thins the band at a sharp convex feature.

When this was measured every other fixture kept its annular construction
under `--wake`, each with a reason: the teardrop tips of `sharp_bodies` and
`narrow_gap_tip` point at the other body, so their wake would cross the ring
into that body's cell - a wake that now ends on that body's band, see *Tandem
foils* below; on 30P30N the slat's trailing edge and cusp and the main's
trailing edge and cove lip all point into the next element, and the flap's
trailing edge is *blunt* - a second convex corner 0.0048 along the wall -
which needs a base template rather than a wake from one corner. 30P30N
therefore stayed at 116 blocks and the same four problems with `--wake`. A
truncated teardrop with a 0.01 base is refused at both corners the same way;
the same body with a sharp tip is resolved.

Two layout rules came out of this work and hold for every run. A curvature
peak within two resampling steps of a sharp corner is the corner's own peak
and is not a separate anchor; and pinned gates crowd each other through the
unpinned gates between them, so the gate relaxation now measures the whole run
between consecutive pins. Wake and chain-break pins are never released by
that relaxation. No default fixture changed under either rule.

### Tandem foils (two-body wake landing)

```bash
make research-tandem      # --case tandem_foils --wake, circle far field
```

`synthetic_cases.tandem_foils`: two sharp-tailed foils of chord one (teardrops
with the tip three radii behind the centre, 39-degree tips), the front one on
the axis with its tail at the origin, the rear one's nose 0.4 chords behind
that tail and the rear foil turned ten degrees nose-up about its nose, so the
front foil's wake, leaving the tail along the axis, arrives at the rear nose.
The rung between the single airfoil and 30P30N: an upstream wake that ends on
a downstream body, and a downstream wake that leaves at an angle.

| far field | blocks | front wake | rear wake | verdict |
| --- | --- | --- | --- | --- |
| circle, default scale, no wake | 61 | - | - | admissible; 84.5 degrees non-orthogonality and 70.5 degrees misalignment at the front tail's seam |
| circle, default scale, `--wake` | 61 | **on the rear foil's band** | to the far field | **admissible**, sizing feasible; 71.0 degrees of non-orthogonality in one core block at the upper medial junction, a degree over the target |
| C-shape, radius 4 about (1, 0), `--wake` | 70 | refused | applied | admissible, not resolved |

Before the anchor rule above the circle case did not build at all: the gates
on the front foil advanced 1.72 times around its wall, because the closest
ring points of its nose stations lay on the branch between the bodies, behind
its tail. With the C-shaped far field the front wake is refused at the layout
stage - "the wall of 'rear' is too short for 6 separated gates in one run":
the four chain breaks of the far field have their closest rear-foil points at
its tail, and with the rear wake pinned there the run between the pins cannot
hold them. That is the annular far field's limit, the one the single-body
C-grid escapes by building its outer core as level sets; a hull-level-set far
field for several bodies is the next step, not a wake defect. `sharp_bodies`
under `--wake` now lands the tip's wake on the disk obliquely (42 blocks,
admissible, 85 degrees of non-orthogonality at the landing); `narrow_gap_tip`
refuses it with the reason that the blocker has no room beside the landing
gate for a band 0.034 wide, its front section on one side being 0.020 long.

### Hull far field (opt-in, two or more bodies)

```bash
make research-tandem-15c    # --case tandem_foils --wake --hull, C-shape of radius 15
python experiments/agentic_topology/research_cli.py run --case two_circles \
  --farfield-shape rectangle --farfield-scale 8 --hull
```

| case | annular construction | hull far field |
| --- | --- | --- |
| tandem foils, C-shape of radius 15 about (1, 0), `--wake` | 73 blocks, inadmissible; the front wake refused at the layout stage by the far-field chain gates crowding the rear tail; 112 s | **116 blocks, admissible**, both wakes (front on the rear foil's band, rear through the hull to the outlet), hull at 0.9, levels at 1.8 and 3.6, then the cap; one core block at the rear edge's seam at 70.5 degrees of non-orthogonality, half a degree over the target; 16 s |
| two circles, rectangle at scale 8 | 45 blocks, admissible, 78 degrees | **83 blocks, resolved**: 47 degrees of non-orthogonality, scaled Jacobian 0.68, hull at 1.34 and one level at 2.67 |
| two circles, circle at scale 8 | 42 blocks, admissible, 78 degrees | 88 blocks, admissible; 79 degrees in one far-field sector where the peanut-shaped level maps onto the round boundary |

The near field is the annular producer's own: the hull is just its outer
boundary, so the 51 to 63 near-field blocks are the same bands, wakes and
core patches as before, built in a fraction of the time because the medial
scaffold no longer reaches seven chords out. The default corpus does not
use the hull and is unchanged.

### Corpus re-baseline (outward anchors, clearance-graded gates)

The anchor rule and the clearance-jump rule moved six default fixtures. Every
fixture is admissible after the change, `skimming_tail` for the first time
and `four_bodies` now resolved; `concave_and_convex` keeps its 42 blocks with
a poorer apex block in the cove, which the pocket construction planned for
30P30N is meant to replace. Default options; before is the committed state
`89aa41b`.

| fixture | blocks before → after | admissible | resolved | non-orthogonality | wall misalignment | min. scaled Jacobian |
| --- | --- | --- | --- | --- | --- | --- |
| `single_ellipse` | 12 → 12 | yes → yes | no → no | 38.4 → 38.4 | 29.5 → 29.5 | 0.78 → 0.78 |
| `two_circles` | 32 → 32 | yes → yes | yes → yes | 64.9 → 64.9 | 6.9 → 6.9 | 0.43 → 0.43 |
| `three_rotated_ellipses` | 89 → 87 | yes → yes | no → no | 74.6 → 79.6 | 23.2 → 19.8 | 0.27 → 0.18 |
| `concave_and_convex` | 34 → 42 | yes → yes | no → no | 81.5 → 88.3 | 31.3 → 39.0 | 0.15 → 0.03 |
| `four_bodies` | 53 → 56 | yes → yes | no → **yes** | 65.2 → 64.2 | 14.5 → 16.7 | 0.42 → 0.43 |
| `sharp_bodies` | 33 → 45 | yes → yes | no → no | 75.7 → 75.7 | 15.3 → 9.5 | 0.25 → 0.25 |
| `narrow_gap_tip` | 41 → 57 | yes → yes | no → no | 69.3 → 69.8 | 15.8 → 21.7 | 0.35 → 0.35 |
| `peanut_body` | 24 → 24 | yes → yes | no → no | 76.4 → 75.9 | 40.3 → 40.3 | 0.23 → 0.24 |
| `skimming_tail` | 26 → 56 | **no → yes** | no → no | - → 67.4 | - → 67.4 | - → 0.38 |
| `straight_channel` | 48 → 48 | yes → yes | yes → yes | 0 → 0 | 0 → 0 | 1.0 → 1.0 |
| `periodic_hill` | 33 → 33 | yes → yes | yes → yes | 29.2 → 29.2 | 21.2 → 21.2 | 0.87 → 0.87 |

30P30N goes from 116 blocks with an invalid topology to 112 blocks whose
topology is valid but whose counted grid has 14 inverted cells; inadmissible
either way. The A-airfoil C-grid is unchanged at 125 blocks, resolved.

### Gap spanning checkpoint (opt-in)

```bash
python experiments/agentic_topology/research_cli.py run --case two_circles \
  --span-gaps --json out/two-circles-span.json \
  --output out/two-circles-span.png --session out/two-circles-span-session.json
```

The default remains the annular construction. With default numerical options
and a medial raster width of 700:

| case | default → span blocks | worst component length ratio | outcome with spanning requested |
| ---- | --------------------- | ---------------------------- | ------------------------------- |
| `two_circles` | 32 → 40 | 18.37 → 8.70 | resolved; no physical wall-band coupling |
| `concave_and_convex` | 34 → 40 | 15.58 → 6.05 | admissible and structurally feasible; shape misses remain |
| `sharp_bodies` | 33 → 33 | 34.69 unchanged | reverted: mouth collides with an existing wall gate |
| `narrow_gap_tip` | 41 → 41 | 99.97 unchanged | reverted: no facing mouth before the next branch anchor |
| `three_rotated_ellipses` | 89 → 89 | 71.72 unchanged | both candidate branches lack room for a facing mouth |
| `four_bodies` | 53 → 53 | 35.80 unchanged | junctions with several narrow branches need another transition |
| `skimming_tail` | 26 → 26 | 89.53 unchanged | still inadmissible; spanning has no room for a mouth |
| 30P30N | 116 → 116 | 282.93 unchanged | both gap trials revert; the same three non-convex faces and one crossing remain |
| periodic hill | 33 → 33 | 2.94 unchanged | resolved; internal producer unchanged |

`single_ellipse`, `peanut_body` and `straight_channel` are also unchanged.
No previously admissible case becomes inadmissible. The current 30P30N repeat
has 143 vertices, 261 edges and all three bands; older detailed tables below
refer to the earlier 115-block checkpoint, not this repeat.

The spanned `two_circles` has four strip blocks, two dissolved junctions and
four valence-five front vertices; Euler characteristic stays -1 and total
index stays -4. Its shape grid has 2,560 cells, zero inversions, minimum scaled
Jacobian 0.402, maximum non-orthogonality 66.32 degrees and wall misalignment
7.37 degrees. The default counts grid also has zero inversions (3,145 cells).
Its session reloads and exports with the same curved boundary sections.
OpenFOAM 2606 also accepts that session: `blockMesh` produces 3,145 cells,
6,504 points and 12,688 faces; `checkMesh` reports `Mesh OK`, maximum
non-orthogonality 59.58 degrees, skewness 0.495 and aspect ratio 80.71. The
production suite runs 410 tests (27 integration tests skipped there); those
27 pass separately against OpenFOAM. The research suite runs 113 tests with
the former expected failure for `skimming_tail`, which now passes.

The physical coupling audit corrects a false rejection at the previous
checkpoint. The mouth puts a cross-gap `core_rung`, the merged `ring`, and a
far-field `wall` in one nine-edge equality component, but it contains no
physical wall/front tangent and no band-normal spoke. Its length ratio is
only 1.91. This core-to-farfield connection does not tie streamwise wall
resolution to boundary-layer thickness resolution. The original generic
diagnostic remains visible; the new physical coupling count is zero, and
`two_circles` is resolved without changing its geometry or counts. A contained
odd-sector fan still produces two physical couplings through the wall fronts
and is rejected. No numeric threshold was relaxed.

The audit reruns all 11 synthetic cases in both default and spanning modes,
plus 30P30N in both modes. Topology, sampled quality, coverage, bands and cavity
decisions match the preceding checkpoint. The spanned-circle and periodic-hill
sessions are byte-for-byte unchanged. Reports, sessions and dictionaries for
this repeat are under `output/coupling-audit/` and
`output/coupling-audit-baseline/` (generated files, not versioned).

The next geometric step is a gap-end cavity that can consume more than the
first branch interval, beginning with `narrow_gap_tip` and then 30P30N. That
construction is not part of this audit, and spanning remains opt-in.

### Periodic hill (internal-flow acceptance)

`synthetic_cases.periodic_hill()` is the classical Almeida channel: hill height
`H = 1`, streamwise period `Lx/H = 9`, flat upper wall at `Ly/H = 3.036`, the
published six-piece lower-wall polynomial mirrored across the period.

| measure | value | declared limit |
| ------- | ----- | -------------- |
| blocks | 33 (22 boundary-layer band, 11 core) | |
| vertices / edges | 48 / 80 | |
| singularities | 4 - the four domain corners, each with one block | |
| shape grid (8 uniform cells per block side) | 2 112 cells, **0 inverted** | none admissible |
| minimum scaled Jacobian, shape grid | 0.873 (was 0.525 with the tangent-disk front) | must be positive |
| shape-grid angle range | 60.8 deg .. 117.7 deg | |
| maximum non-orthogonality, shape grid | 29.2 deg (was 58.3) | 70 deg - **passes** |
| maximum equiangle skewness, shape grid | 0.325 (was 0.648) | 0.85 - **passes** |
| maximum wall misalignment, shape grid | 21.2 deg (was 15.3) | 25 deg - **passes** |
| structural: worst component length ratio / couplings | 2.94 / none | 20 - **passes** |
| counts grid (default sizing) | 12 768 cells, 0 inverted | informational |
| aspect ratio (boundary layer / unintended), counts grid | 3.13 / 6.91 | 100 unintended - passes |
| maximum first-cell width error, counts grid | 0.19 (was 0.387) | 0.25 - passes |
| maximum interface size ratio, counts grid | **2.88** (was 3.48) | 2.5 - **sizing miss, informational** |
| patches | `bottom_wall`, `top_wall` as `wall`; `periodic_left`/`periodic_right` as a reciprocal `cyclic` pair | |

So the periodic hill is `topology_valid`, `untangled`, `admissible` and
**`resolved`**: on the shape grid it is within every shape limit and its
equality components force a length ratio of only 2.9 with no coupling. It has
33 blocks rather than the 48 of earlier iterations because the sweep's column
count is now decided by a geometric rule - a core column is halved while it is
longer than half the distance between the guides - instead of by the metric
length of the column, so the block count no longer follows the cell sizing;
the shape measures are slightly better than with 16 columns. The one miss left, the interface jump of the mesh
the default counts define, is reported as an informational sizing miss: it is
the band's last cell against the core's first, which belongs to the counts
and grading stage. The level-set front removed the earlier first-cell-width
miss: with the tangent-disk law the band at the hill foot was limited by the
foot's own curvature and its offset was flattened into a wiggle, which made
the first-cell width vary by 39 percent inside one block; the front now
follows the eroded boundary there with a rib at the foot, and every shape
measure improved with it.

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

The run is **not `topology_valid`**, for four localised reasons: the three
non-convex faces above, and one relation between a layer front and the medial
scaffold behind it:

| remaining problem | where | cause |
| ----------------- | ----- | ----- |
| `crossing_edges` between a `core_spoke` and a `front` edge | `(0.8020, 0.0221)`, in the flap gap | the gap is 0.0104 wide and the main/flap medial branch passes 0.006 from the main trailing edge. Both bands, both half-core strips and the medial ring are stacked inside it; the flap front's height cap changes threefold between a gate and the interior, so the front bulges past the straight spoke. `skimming_tail` is the small version |

That defect is exactly the kind a four-corner test cannot see - the corner
quadrilateral there is convex - and it is only visible because edges that
share a vertex are checked against each other and because the front is compared
as the polyline it really is.

The `touching_edges` problem that used to sit beside it is gone. It was
main-element vertex 40, the 90 degree concave corner at the top of the cove
cut, where the tangent-disk feature size is exactly zero and the front height
was 0.0. With the level-set front the corner is a pinned reflex gate, its spoke
runs along the bisector to the mitre, and the main element's front keeps a
positive height everywhere (minimum 0.0022, against a band of up to 0.038).
`peanut_body` is the fixture for it.

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
| wall/front touches | not checked | 1 | 1 | **0** (level-set front) |
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

Two fixtures reproduce the 30P30N failure modes in small geometry.
`peanut_body` is the union of two orthogonal circles: smooth everywhere except
two 90 degree reflex corners at the waist. No disk can be tangent to a wall at
a reflex vertex, so the tangent-disk feature size is exactly zero there; with
that law the band could not be built at all. With the level-set front it is a
valid, untangled 24-block topology whose waist gates are pinned reflex corners
with bisector spokes to the mitre; its quality misses are the 45 degree block
corners that construction leaves at the waist. `skimming_tail` is a sharp tail
whose lower flank runs horizontally two percent of the scale above a hull
whose highest point lies just downstream of the tip, the configuration a
deployed flap makes with the main element; a band cannot be built in the slot
and the fixture is **expected to fail** until the gap topology changes. Each
carries a stable test on where the failure is located and, while it fails, an
`expectedFailure` on admissibility, so the moment a construction handles the
fixture the suite reports an unexpected success and the test has to be
flipped. Every synthetic fixture gives the same block graph at raster widths
400, 700 and 1000, and a test guards that; 30P30N does not (see the remaining
limits).

## Dependencies and limits

NumPy and Pillow are research dependencies only and must never become
BlockDrawer runtime dependencies. No `numpy.linalg` factorisation and no BLAS
level-3 product is used: `linalg_lite.py` carries a small Gaussian elimination
and a conjugate-gradient Laplacian solve, so the prototype also runs on
installations whose LAPACK/BLAS build is broken.

### Remaining limits

* **Gap spanning is opt-in and its mouth has limited reach.** The single strip
  removes a medial edge and preserves the index; its core-to-farfield count
  connection is reported but is not a wall-band direction coupling. A mouth is
  currently confined to the first branch interval; sharp gates, a third wall, or several candidate
  branches at one junction require a different transition. Rejections restore
  the complete pre-span result. It does not yet fix the 30P30N flap gap.
* **The front and the medial scaffold are not yet reconciled.** A front is
  capped at a fraction of the local feature size, at the wall's own radius of
  curvature, at the gate-to-ring distance *at each gate*, and at nine tenths of
  the distance to the ring elsewhere. Those caps are not consistent with one
  another, so between two gates a front can bulge past a core spoke that starts
  at one of them - which is the remaining 30P30N crossing. Making them
  consistent at the tighter value was measured and costs two inverted sampled
  cells on `concave_and_convex`; the real fix is to let the core spoke follow
  the scaffold rather than a straight line, which needs an interior guide curve.
* **A reflex corner leaves 45 degree block corners.** The level-set front
  gives a concave corner its mitre and a bisector spoke, so the two band
  blocks beside it meet the wall at half the fluid angle: 45 degrees for a
  90 degree corner, with 135 degree corners at the mitre. That is valid and
  it is what `peanut_body` now reports as its non-orthogonality and skewness
  misses. The natural alternative is a corner block: the corner becomes a
  one-block boundary vertex, its two neighbouring gates share the mitre as
  their front vertex, and the region between the mitre and the medial ring
  becomes a wedge. It is a cavity template like the seam and has not been
  written yet.
* **The seam wedge ties the band count to the core depth.** Its opposite sides
  are a band spoke and a core spoke, so every sharp feature repaired with a
  seam merges the chain's wall-normal band count with its core radial count.
  `graph.sizing_structure` reports it as `band_core_depth_couplings` with the
  length ratio it forces (855 on 30P30N). The cavity coupling check does not
  refuse it yet, because every sharp feature currently depends on the seam and
  the through-cut alternative is built for three sectors only.
* **Front repair is deterministic but still sensitive to rounding.** Local
  repair now reduces a whole failing block's cap, requests cuts for stubborn
  failures and permits a bounded global height search on the caller's last
  repair round. A last-bit difference between the geometric default height
  and the sizing-series formula still changes `sharp_bodies`' default cell
  count from 4,801 to 4,421 cells (about 8 percent) with the same 33-block
  graph. The search has explicit
  stopping conditions; numerical stability is still a separate open problem.
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
* **The annular producer's reach ends at about six chords of far field on a
  single sharp-edged body**; the C-grid producer takes over there with
  `--wake` and a C-shaped far field, and needs exactly one body, one planned
  wake and the four-chain cap-leg-outlet-leg boundary. A rectangle or a circle
  around a single airfoil still gets the annular construction and its wake
  rewrite. A blunt trailing edge, a wake ending on another body, and the
  multi-element case remain open. The far-field spokes are straight lines
  from the outermost level set to the cap, so their angle to that level set
  is what the sector count allows (51 degrees non-orthogonality on the
  A-airfoil); curved spokes would improve it.
* **The wake separatrix is opt-in and straight.** One line per sharp feature,
  from the feature through the body's own ring to the outer boundary. A wake
  that would enter another body's cell - the slat and main trailing edges of
  30P30N, every teardrop tip in `sharp_bodies` and `narrow_gap_tip` - is
  refused with that reason; a wake ending on another body's front, or a curved
  wake following the medial branch, is not built. The wake band is as thick as
  the band at the edge, which the clearance cap makes thin at a sharp tip.
* **The outer boundary is one site.** Its chains become patches and its
  corners and chain breaks become gates, but a wall chain on the outer
  boundary gets no boundary-layer band from the external producer: the bands
  are built for whole hole loops. A cylinder in a channel therefore has walls
  without bands on the channel; the internal producer's guide-front bands are
  the construction that fits, and joining the two producers is open.
* **The band request and the size metric scale with the domain**, whose frame
  includes the outer boundary. A far-away outer boundary - thirty chords for a
  C-grid - inflates the requested band height until the clearance cap binds;
  pass `--layer-height-ratio` explicitly for such cases. Referencing the band
  to the walls' own scale would change every external result and is a
  separate decision.
* The default medial core is one annulus per body; a cell whose ring has several
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
| `spanned.py` | optional gap strips, facing mouths, junction dissolution and structured refusals |
| `wake.py` | optional wake separatrix: wake anchor, wake bands, C-grid rewrite and structured refusals |
| `cgrid.py` | single-body C-grid: slit-body level sets, mapped levels, wake band, far field on the cap |
| `fetch_a_airfoil.py` | downloads and normalises the A-airfoil acceptance geometry |
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
