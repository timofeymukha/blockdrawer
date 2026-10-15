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
   coincident points (at BlockDrawer's own coordinate tolerance, so the domain
   refuses what the session stage would), hole containment in both directions
   (a body inside another of larger perimeter), self-intersection and
   self-touching loops (a figure-eight, a hairpin, a vertex on its own edge),
   holes touching each other or the outer boundary, unique names,
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
   worst-ratio table. Coupling different walls is rejected except through a
   wake band that lands on the second body, which is reported as a
   `wake_landing_coupling` (step 6); its predicate is still "the component
   touches a wake face", broader than "is the wake cut". The length ratio
   limit of 20 applies to every component except far-field fan-out ones, and
   the verdict without either exemption is reported. Old saved reports without
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

   A junction whose Gauss-Newton refinement stops above 1e-6 of its clearance
   is two junctions the raster merged into one cluster (`four_bodies` at the
   suite's width of 420 lost a branch that way): the diagram is rebuilt on a
   raster 1.5 times finer, up to three times, and any junction still not
   converged is named in `medial.diagram_notes`. The body frame - centre,
   radius, the scale every tolerance follows - is measured on the closed
   loops, so the start point and the direction of a point list no longer move
   it (a blunt foil's frame used to shift by 15 percent with its start point).
   A curvature anchor marks a genuine extremum - the smoothed curvature falls
   by ten percent within three percent of the perimeter on both sides - of
   the vertex turning smoothed by a kernel at least as wide as the vertex
   spacing, so a coarse list is read at its own resolution (every vertex of
   a 24-gon used to be a peak); each peak is located on the curvature itself,
   not on the sample grid, and the anchor limit takes equal peaks together or
   not at all. A patch is split where half its wall turning is reached, with
   the turning taken as a density along the loop - collinear vertices carry
   none, a vertex under a gate gives half to each side, a sharp corner is a
   point mass that a gate on it gives to neither block - so the split no
   longer jumps by four vertices when a gate passes a strongly turning one
   and an inserted midpoint does not move it; the turning a patch is judged
   by stays the sum over its section's vertices. Two forced
   anchors that crowd each other on one branch - a square body's corner and
   the corner of a box with equal margins, both on the box diagonal - become
   one cut pinned on both walls, and the relaxation keeps every patch within
   the wall-turning limit the layout enforced (`patch_solver.SolverOptions.turning_weight`;
   it used to slide an 8:1 ellipse's tip gates off its tips). Gate
   unwrapping clamps an unpinned gate into ring order only when the gates
   would otherwise wind twice round the wall. The solver's splitting stops
   after five consecutive splits that do not raise the worst block
   (`SolverOptions.futile_splits`): when the worst patch cannot be split,
   splitting the next-worst ones one at a time used to spend the whole
   budget of forty relaxations, which took the A-airfoil's C-grid layout half
   an hour at most raster widths without changing its worst block. With
   these, all 13 fixtures give the same block graph under rotation by 37, 90
   and 180 degrees, reflection, scaling by 1000, translation by 1e4, a start
   point a third of the way round, both reversals, densification and a
   repeated closing point (see "Generality").

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

   **Rounded reflex corners.** A run of concave vertices whose local radius
   of curvature is below three percent of the perimeter and whose turning
   adds up to at least a reflex corner's worth (`layers.tight_concave_runs`)
   is one reflex corner at its turning midpoint: for any band taller than
   its radius the level set there is the run's mitre, so the midpoint gets
   the reflex anchor with its bisector spoke, the band is mitred there, and
   the run is exempt from the smooth-bend cap that would otherwise thin the
   band to a fraction of the bend's radius. The wall inside a rounded
   corner's mitre shadow carries the corner's far clearance, since its own
   offset point lands on the other flank and the probe read that as a
   collapse; left at its own value the slope limit dragged the corner's band
   down to a third. Sharp reflex corners keep their shadow's own values: in
   30P30N's coves the inherited clearance let a lip's band cross the next
   element's spokes. Vertices on a straight line are left out when the runs
   are found, so a polygon densified by midpoints gives the same corners (it
   used to break the cove's run at 38 instead of 91 degrees and invert cells).
   The cove of `concave_and_convex` bottoms out in a bend
   of radius 0.03 turning 91 degrees, which the plain rules saw as a smooth
   bend and capped at 0.02; it now carries a full band, and its worst block
   goes from a scaled Jacobian of 0.03 to 0.40. The 45 degrees of wall
   misalignment that remain beside the apex are the mitre's own: the two
   band blocks beside a reflex corner meet the wall at half its fluid angle.

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
   seam. A seam point is offset along the normal of the wall chord past any
   base narrower than a tenth of the band height (`layers.SEAM_CHORD_FRACTION`)
   and by the arriving front's height when that is less than the feature's
   own, so the front reaches the seam without a jog (the 30P30N slat's
   pressure-side band thins under the branch to the main element, and the
   core patch beside the wake front folded at the seam point standing out
   of it). Once the band exists, the seam points are carried downstream
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
   body a gate there whose spoke is the wake line beyond the ring. That ray
   is the wake direction when it meets the body's band steeply enough, and
   otherwise *aimed* at the band (`wake._aim_landing`, the record's
   `landing`): from the ring crossing the far leg turns towards the body, a
   degree at a time to either side up to sixty degrees, to the first
   direction whose incidence on the band - estimated with the band at the
   requested height capped by the clearance rule, its front point on the
   wall's normal at the gate (`wake._landing_estimate`) - reaches
   `wake.LANDING_INCIDENCE` plus a five-degree margin; the ring's chords
   beside the crossing must meet the aimed leg as steeply as a turned
   wake's leg (`wake.RING_CHORD_ANGLE`). The two
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
   sector, meet the outer boundary across a chain break, land where the
   downstream body has no room beside the stagnation gate, or still graze
   that body's band - the wake line meeting its front under 35 degrees
   (`wake.LANDING_INCIDENCE`, measured on the real band and kept as
   `landing_incidence_degrees`), where the strips beyond the ring end in
   slivers at the landing gate whatever the landing points - is refused with
   its reason, and a trial is committed only when the complete result passes
   coverage, session emission and both sampled grids; otherwise the annular
   construction stays exactly as it was. Before the wakes are written a
   trial repairs the seams of the other sharp features with the cavity
   stage's seam template alone (`pipeline._repair_seams_before_wakes`,
   `CavityOptions.templates`): a turning wake's level row goes through
   every seam wedge on its way round the cell and splits it, after which
   the cavity stage on the finished graph can rewrite nothing there - the
   30P30N slat's cusp, whose plain seam left the band block round the
   cove non-convex by its chords before the cove got its cuts, refused
   every wake of the slat that way.
   The features the wakes replace or land on keep their plain seams for
   the writers.

   **Blunt base.** Two convex corners within two percent of the perimeter of
   each other are one base, and the base gets its own template rather than
   a wake from one corner: one record for the pair, a wake line from each
   corner along the base's outward normal (or the fixed direction, within
   sixty degrees of it), two fixed anchors where those lines cross the ring,
   and beyond the base three strips to the outer boundary - the base strip
   between the two wake lines, which carries the base as its wall edge so
   the base's boundary layer is the strip's own grading, and a wake band on
   each side that continues the flank band with its normal count. The band
   block over the base, the core patch above it and the corners' seam wedges
   are replaced by ten faces; each corner ends up with three blocks meeting
   at it. The corner's flank-side front point is where the wake band's outer
   edge leaves the flank front, and the core patch beside it meets that
   turn with an angle of 180 degrees less the flank's angle to the wake -
   the same corner the sharp edge's wake makes - which is why a thin
   trailing edge's neighbouring core block sits near 70 degrees of
   non-orthogonality in the annular construction; the C-grid avoids it with
   its cross-wake line. The base strip's two wake lines are the base's
   wall-normal edges and are graded to the first-cell width at the corners.
   The two-percent rule is a cliff, so a corner whose partner lies up to four
   times as far along a nearly straight wall (at most 30 degrees of turning
   between them) is still read as one corner of a base: its single-corner
   wake is refused with the remedy, `--wake-blunt-ratio`, rather than written
   as a lopsided cut from one corner (a flat-back section with a trailing
   edge of a few percent of the chord). Without `--wake-direction` every
   vertex sharp enough gets a wake along its own bisector, a sharp leading
   edge included; give the direction for such bodies.

   **Wake turn stations** (`turned` in the record). A wake direction more
   than ten degrees off the edge's bisector - a base's flank bisector, so
   a base oblique to its flanks does not fold one flank's front - used to
   fold the core patch beside the wake band at the seam: BlockDrawer's
   blocks are convex by their four corners, so a band side cannot bend
   inside one block. Such a wake now leaves along the bisector and turns
   towards the wake direction at stations, at most `--wake-turn` degrees
   each (default 50; each block beside a station sees half), all before
   the body's ring so the ring is crossed without a kink. A station is a
   vertex on the wake line and on both fronts joined by a rung; the strips
   are split there, and the station is carried round the body's cell as a
   level - every spoke between the band front and the ring split at the
   station's fraction, interpolated from one front to the other, every
   patch cut into a column of quads, each rung a Coons blend of the
   patch's four sides - exactly as the far field stacks its level sets. A
   front leaves its seam along the band's own arrival direction (the
   tangent or the chord of the whole band-front edge, whichever needs the
   larger turn away from the wake, a five-degree margin added) and meets
   the first rung where it does. Stations sit at equal fractions of the
   ring's distance along the bisector, at least one and a half band
   heights apart, within four fifths of it; a wake over another body keeps
   its straight chord and says so under `turn_note`; `--wake-turn 0` keeps
   every wake straight. The C-grid producer builds its own straight wake
   and ignores the stations. **Where the stations go** (`turn_placement`
   in the record) is a ladder, each rung judged before the wake is written
   (`wake.plan_turns`): *inside* the ring as above, unless the stations do
   not fit, or the ring bends beside the crossing so that the chord from
   the front's crossing to the neighbouring cut's ring point meets the
   wake direction under 25 degrees (`wake._chord_angle`,
   `RING_CHORD_ANGLE`: the medial ring behind a convex corner is a fan
   round it, and a wake direction crossing the fan obliquely leaves a
   sliver however the stations sit - the 30P30N flap base's three-degree
   chord); then *beyond* the ring, the wake crossing
   it straight along the bisector and turning in the layer between the
   ring and the outer boundary, the stations carried round the whole
   cluster's outer layer as levels, unless the outer boundary bends beside
   the exit the same way (the hull round the flap's corner does); then
   *straight*: the wake keeps its bisector through the whole near field,
   and either the body it ends on takes it as it arrives, its far leg
   aimed at that body's band, or, when the outer boundary is the cluster's hull, the
   far field turns it - its level sets widen round the band by their own
   growth, and the mitre of a band edge on the outside of the turn is
   found behind the exit when the level set lies there. Anything else is
   refused with every rung's reason and the remedy (raise `--wake-turn`,
   or move the ring with a higher hull). A level row meeting another sharp
   corner's **seam wedge** on its way round the cell crosses it
   (`wake._write_wedge_rows`): the wedge's bisector - the gate's line to
   its ring vertex - becomes a spoke through one point per row, dipped
   towards the gate by `WEDGE_DIP` of the chord's distance from it, and
   the wedge a column of quads on each side of that spoke under a single
   quad at the ring vertex (30P30N's slat: the cusp beside its edge).
   `test_wake.WakeTurnTests` has the teardrop and the blunt base turning
   thirty degrees inside the ring and resolving, the teardrop turning beyond
   the ring with the chord measure patched to a fan, a lens whose tail's
   row crosses its nose's wedge, the chord measure on a fan, and the ladder
   on the teardrop's layout.

   **Wake over another body** (the multi-element wake, `through` in the
   record). A wake whose ray crosses the ring into another body's cell and
   reaches that cell's far ring before any wall - a main element's wake
   over its flap - is written through that cell. The planner follows the
   ray: it must leave the passed body's cell exactly once, into the outer
   cell, and reach the outer boundary; two anchors are pinned, where the
   wake enters the cell (pinned to the trailing edge and to the passed
   body's nearest wall station) and where it leaves (pinned to the passed
   body and to the exit on the outer boundary), and the second is the exit
   the hull far field continues. Through the cell the wake is a straight
   chord from ring to ring with a strip on each side between it and the
   two wake fronts, which run parallel to it from their ring crossings on;
   each front leaves the seam point along the wake, or - where the band
   thins towards the trailing edge, so a front parallel to the wake would
   put a reflex corner on the core patch beside the band - along the band
   front's own tangent, turned five degrees further from the wake. On the
   body side the strip is bounded by the passed body's core, which gets
   three new spokes down to that body's band: where the body-side front
   enters the cell, halfway along the chord, and where it leaves; the
   body's band is rebuilt with the wake band as an obstacle so it stays
   clear of the strip. On the other side lies the *lens*: the region
   between the lens-side front and the arc of the passed body's ring the
   chord cuts off, from the entry crossing to the junction of the three
   cells and on to the exit crossing. A lens is a curved triangle - two
   tips where the front crosses the ring, one corner at the junction - and
   no spoke from its arc to its front can end it in quads, since the tip
   between the first spoke and the arc is a triangle whatever the angles;
   it is written as three quads around a centre vertex, with one new spoke
   from the middle of each side: into the wake's own cell from the arc's
   piece on the entry branch, onto the outer boundary from its piece on
   the exit branch, and down through both strips to the passed body's band
   from the front. The same tip argument means the passed body's wall under
   the wake carries exactly those three gates: every optional gate on the
   lens arc is dropped before the bands are built, a junction is the lens
   corner, and a fixed gate of the passed body there (a corner or chain
   break under the wake) refuses the wake, as does a lens with more than
   one corner, a body whose band cannot be built with three gates under the
   wake (its wall turns too much there), or a wake that enters a third
   body's cell. Beside the wake's own trailing edge, optional gates whose
   core patch against the wake front is not a convex quad are dropped
   rather than slid; a fixed one there refuses.

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
   is the cross-wake line, and the wake band is the stack of levels along
   wake lines that leave their mitre points at the `--wake-spread` slope
   (0.05, a lateral offset per unit length; the centre line keeps the wake
   direction) so the band widens towards the outlet: parallel lines carried
   a slot-limited band of 0.003 chords and its first-cell count for fifteen
   chords on 30P30N main and flap, beside streamwise cells of a chord -
   aspect ratios of 1e4, and checkMesh refused every grading; spread, the
   same case meshes at a 1e-3 first cell with aspect ratio 77. The far field
   is the outer boundary
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
   at the same fractions of the perimeter when the far field is closed: the
   near field is first built with the hull as one chain, since a wake
   leaving the cluster maps onto the cap and outlet by its own construction
   and mirrored chain breaks beside its exit would tangle the hull's gates;
   only when no wake leaves is the near field built again with the chains
   mirrored, so that the closed far field maps chain onto chain. The near
   field is the
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
   fraction between *kinks*: every level set of the cluster has a kink
   where the nearest body changes, the exterior medial axis crossing it,
   and the kinks of one level correspond to those of the next in cyclic
   order, so each piece between kinks maps onto its counterpart and a
   vertex never lands across the waist from its hull vertex (which twisted
   the far-field sectors between two bodies); the outer boundary's own
   kinks place the mirrored chain breaks the same way. The blocks between
   levels are judged for convexity, and the outermost level is joined to
   the outer boundary by straight spokes. With a
   wake leaving the cluster the levels are the *slit hull*'s: cut where they
   lie inside the wake band's offset, ending on mitre points at the band's
   edge lines, the wake band's strips - two halves for a sharp edge, the
   base strip and its two bands for a blunt base - and one strip per level
   and side running to the outlet along lines fanned from the band: the
   lines leaving the hull - the bottom band's in edge, each band's interior
   lines, the channel lines, the top band's out edge - take slopes away
   from the wake direction spaced evenly by rank from minus to plus the
   `--wake-spread` slope, so every strip, however thin at the hull, widens
   towards the outlet by the same amount (lines kept parallel, or fanned in
   proportion to their offset, left the main element's wake strips on
   30P30N four thousandths wide for fifteen chords, aspect 7500), and the
   levels' mitre lines beyond the edges take the full spread - the
   levels mapped onto the cap and two
   wake-side blocks covering the legs, exactly the C-grid's far field with
   the wake band's own width in place of the trailing edge. Two or more
   wakes leaving through the hull **merge into one band**: the levels are
   cut between the uppermost band's out edge and the lowermost's in edge,
   each level's mitres offset from those two bands' axes (the bands'
   offsets exceed the gap between them within the first level, as the
   wakes merge in the flow), every band's strips run to the outlet, and
   the hull between two bands continues to the outlet as a *channel* of
   straight strips - one per hull piece - whose lines keep the wake
   direction, as do a base's lines on the channel side (`merged_wakes`,
   `channel_strips` in the record). The pipeline tries the merged band
   first; when it does not build, each wake in turn carries the cut alone
   (`exit_site` of `hull.extend`) with the others *ending at the hull* -
   their bands stop there and their exit vertices are hull vertices like
   the rest, carried outward as level spokes (`ending_wakes`) - and last
   the near field is rebuilt with the other body's wake excluded. A hull's chain
   breaks are anchored by the inward ray (`LayoutOptions.outer_by_ray`),
   since the closest ring point of a station near the hull's pointed end is
   arbitrary. Without one the far field
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
   metric length limit of a column. A chain join on a guide is a block vertex
   and is pinned through the relaxation, so every guide edge lies on one named
   chain (a slot or a separately named crest on a wall used to be relaxed away
   and exported under the wall's name). A guide that carries no wall - a
   symmetry plane - gets no band, bounds the band of the opposite wall, and its
   row is a boundary row: the blocks beside it are core and its ribs are core
   ribs. A non-monotone or crossing mapping is
   rejected with a structured reason. For a periodic pair the far end rib is
   constructed as the exact translate of the near one and every rib vertex is
   projected onto its end chain.

7. **Patch graph** (`patch_graph.py`). Both producers write into one
   representation: topological vertices with geometric constraints (fixed point,
   boundary-chain parameter, guide-curve parameter, free interior point), shared
   oriented edges with a boundary role and provenance, quadrilateral faces,
   periodic vertex correspondence. It validates Euler characteristic, discrete
   index, face-edge incidence, boundary cycles, orientation, convexity, hanging
   vertices, duplicate and crossing edges, and an edge path crossing itself
   (`self_crossing_edge`) - and an independent raster coverage
   test confirms the faces tile the fluid, because `MeshModel.validate()`
   is not a planar coverage test. The coverage lattice cannot see the band
   scale near a wall, so a second test (`boundary_problems`) measures every
   named boundary edge against its own chain's point list
   (`boundary_off_chain`) and requires the edges carrying each chain's name to
   add up to that chain (`chain_not_covered`): a section exported under the
   wrong patch name, or a wall edge drifting off the supplied geometry, makes
   the result invalid. (The index-sum check is the Euler check in another
   form, `total_index == 4 * euler` for any manifold quad graph; it screens
   nothing on its own.)

8. **Counts and grading** (`sizing.py`). BlockDrawer forces opposite edges of a
   quadrilateral to share a cell count, so the edges fall into equality
   components. Each component gets the single positive integer that minimises the
   length-weighted squared error against the metric lengths of its members -
   which for an equality-only constraint is the weighted mean, rounded. A metric
   length is the integral of `ds / size` along the edge, taken exactly for a
   size varying linearly along each segment (its length over the logarithmic
   mean of the end sizes) after long segments are subdivided at a quarter of
   the core size. The trapezoid of `1/size` at the end vertices it replaces
   overcounted every two-point edge leaving the wall - 1.6 times at the
   default sizing and by up to two orders of magnitude at a first cell of
   1e-5 of the scale, where it clamped every band at 400 cells. An
   optional global budget scales every component by one factor, and
   `sizing.budget_met` says when even the smallest counts exceed it. Band spokes
   then receive the requested first-cell width through `set_edge_grading`
   (and so do the two wake lines of a blunt base's strip, the base's own
   wall-normal edges). A request whose first cell is not smaller than the
   uniform width `length / cells` is refused and the edge left uniform: it
   could only be met by cells shrinking away from the wall, and on 30P30N that
   backwards grading reached a total ratio of 6e-23 and collapsed rows into
   "inverted" cells. Every refused request is reported rather than dropped.
   No mixed-integer dependency is used.

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

   Three grids are measured. The **shape grid** samples every block at a fixed
   number of uniform parametric fractions per side (`shape_cells`, default 8),
   so it describes the block map itself and is identical whatever counts and
   grading are chosen later; it is judged against the *shape* limits only:
   non-orthogonality, equiangle skewness and wall misalignment. Its values are
   measured at that resolution and are lower bounds of the continuous
   extremes. The **fold check** samples the same block map at 32 cells per
   side (`fold_cells`) and is judged for inversion only, because a fold
   between the shape grid's samples - a loop in a curved edge - is invisible
   at eight. The **counts grid** samples the graded mesh nodes the session
   defines and carries the *sizing* report: aspect ratio, interface size ratio
   and first-cell width error. The non-orthogonality measured on these grids
   is the corner-angle deviation from 90 degrees; checkMesh's is face-based,
   and the two agree for parallelograms only (66 against 60 degrees on the
   spanned `two_circles`). Acceptance is reported in these terms, and the
   report carries an immutable copy of the limits it was judged against so it
   can never be compared with different defaults later:

   | term | meaning |
   | ---- | ------- |
   | `topology_valid` | incidence, planarity, exact domain coverage, boundary fidelity hold, `MeshModel.validate()` accepted the session and `blockMeshDict` exports from it (a cyclic pair whose edges do not match fails here) |
   | `grids_evaluated` | the shape grid, the fold check and the counts grid were all measured; when false, every grid-dependent term below is **false**, not vacuously true (`--no-grid-quality` used to report `resolved` and write sessions it had never sampled) |
   | `untangled` | no sampled cell is inverted on the shape grid, the fold check or the counts grid, and the minimum scaled Jacobian is positive |
   | `within_shape_targets` | every **enabled** shape limit is met on the shape grid; `null` disables one explicitly rather than hiding it behind a huge number |
   | `sizing_feasible` | no opposite-edge equality component - every component, not only the short `worst` table - forces a geometric length ratio above `StructureLimits.max_length_ratio` (default 20) or ties a tangential resolution to a wall-normal one - the count-independent statements of `graph.sizing_structure`. Two exemptions apply, and `sizing.feasibility.failures_without_exemptions` reports the verdict without them: a far-field fan-out component (tangential, longest edge on a non-wall *outer* chain - large far cells, as in every O- and C-grid) is not judged, and a coupling of one body's band normals with another body's tangents through a wake band that lands on it is a `wake_landing_coupling`, not a wall coupling. Both were introduced with the case that needed them (the A-airfoil C-grid, `3fdf23e`; the tandem landing wake, `e116b8d`) |
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
   candidate, or one whose grids were not evaluated, writes no session at all;
   `run` exits 1 for anything short of `resolved` and prints which term
   failed, then the sizing misses as `sizing (informational)`.

   An opt-in stage - C-grid, wake, gap span - commits a trial only when it is
   admissible *and* does not lower the verdict of the result it replaces
   (inadmissible < admissible < resolved). The hull far field runs before the
   annular construction it would replace and is not compared. An exception
   inside a trial that is not one of the construction's own refusals
   (`GraphError`, `HullError`, `LayoutError`, `TraceError`, `LayerError`,
   `DomainError`, `SweepError`) is a defect: its reason says "crashed", it is
   listed under `trial_crashes`, and the command line prints it.

10. **Session** (`session_emit.py`). Only a graph with no problems and exact
    coverage becomes a `MeshModel`; it is validated, saved, reloaded, validated
    again, rendered headlessly and serialised to `blockMeshDict`. Wall edges keep
    the supplied point list, a circular far field keeps exact arcs, cyclic
    patches are paired reciprocally, and each wall chain is attached as a
    reference curve. Both extrusion patches are `empty`: the output is a 2-D
    mesh, and a unit-depth slab of `patch` faces failed checkMesh's aspect
    ratio test for sessions that are fine in 2-D. With
    `--wall-edge-type spline` every exported spline is sampled span by span
    with BlockDrawer's own evaluation and compared with the polyline the graph
    validated: one that leaves it by more than 0.2 percent of its chord, or
    crosses itself, is exported as that polyline and listed under
    `session.export_notes` (a teardrop flank's spline overshot by 2.8 percent
    and folded 40 cells once the edge was refined).

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
`--first-width-ratio`, `--core-size-ratio`, `--growth`, `--cell-budget`,
`--wall-aspect RATIO` (the wall-parallel spacing at the wall as a multiple
of the first cell width, flooring the size metric along every tangential
edge, `SizingOptions.wall_aspect`; the wall-normal spokes and the rungs
across a wake keep the isotropic law, and along a curved edge the floor is
capped so that a cell turns at most fifteen degrees,
`SizeMetric.MAX_CELL_TURNING`) - and
`--split CELL:CUT` forces an anchor where an agent asks for one. Because the
run is stateless, that is also how `apply` realises a move: it rebuilds the
very case the arguments describe, explicit `--outer` chains included, with the
move's option delta. `--wake-blunt-ratio FRACTION` sets how far apart two
convex corners may be and still form one blunt base (default 0.02 of the
perimeter); `--wake-turn DEGREES` the largest turn one wake station takes
(default 50, 0 for straight wakes); `--farfield-center X,Y` accepts the
origin.

`--layer-height-ratio FRACTION` sets the requested band height as a fraction
of the domain scale (default 0.256, where the default sizing's wall-normal
series reaches the core size); it is a geometric input, so the block shapes
and the shape verdict do not change with the cell sizing.
`--layer-height-ratio series` uses the series height of the sizing in use. `--max-length-ratio RATIO` sets the
structural feasibility limit. `--normal-window HEIGHTS` averages the wall's
offset normals over an arc-length window of that many band heights (default
0, the plain vertex normals; the band's level-set trimming has used the
windowed normals all along, now the offset does too when it is on).
`--max-wall-turning DEGREES` exposes the largest
wall turning one annular patch may span before it is cut (default 100). It is the single largest lever
on near-wall orthogonality. Measured on this commit, default options otherwise:

| limit | `single_ellipse` blocks / misalignment | `concave_and_convex` blocks / misalignment | `narrow_gap_tip` blocks / misalignment |
| ----- | -------------------------------------- | ------------------------------------------ | -------------------------------------- |
| 100 deg | 12 / 27.1 deg, admissible | 42 / 43.1 deg, admissible | 57 / 21.7 deg, admissible |
| 45 deg | 24 / 10.3 deg, **resolved** | 63 / 43.1 deg, admissible | 69 / 21.7 deg, admissible |
| 30 deg | 48 / 2.9 deg, **resolved** | 63 / 44.5 deg, admissible | 77 / 5.2 deg, admissible |

At the review before this commit 45 degrees folded `concave_and_convex` (a
minimum scaled Jacobian of -0.334), which was the reason the default stayed
at 100; with the turning barrier in the relaxation none of the three folds.
The default has not moved yet: a tighter limit doubles the single ellipse's
block count, `concave_and_convex`'s 43 degrees do not respond to it, and
the whole corpus has not been re-measured at 45.

`moves.py` generates and screens five families of candidate operation:
`wall_feature_cavity`, `split_patch`, `split_singularity`, `collapse_strip` and
`insert_separatrix`. Every candidate carries its index change, whether the
change is balanced, an estimated complexity and a score. Moves this iteration
cannot construct are still generated and screened, and carry `implemented:
false` with the exact reason - which is more useful to an agent than silence.

`wall_feature_cavity` is not a proposal: each one has already been constructed
on a copy of the graph and validated completely, so an agent chooses among
alternatives that are known to work rather than placing vertices. Applying one
sets `cavity.choices` - a `(feature label, template name)` pair, replacing any
earlier choice for that feature - and re-runs, which keeps the command line
stateless; a chosen template is committed whenever it is valid, even when the
construction already there scores higher. A refused alternative carries the exact
reason, including the two conflicting entity ids for a crossing. The
"Euler/index screening" of moves is the Euler check: index balance is an
identity for manifold quad graphs.

## Results

### Current results

Measured on this commit with `research_cli.py run` and default options unless
stated, every written session checked as exported with `blockdrawer-cli check`
(OpenFOAM 2606 `blockMesh` and `checkMesh`, `empty` z patches). Shape
measures are the shape grid's (eight uniform cells per block side); the
`checkMesh` column gives its verdict with its own maximum non-orthogonality
and skewness, and the seconds are wall-clock with six runs sharing the
machine. The subsections after this one record what each construction
measured when it was introduced; where their numbers differ from these
tables, these are current.

| run | blocks | verdict | non-orthogonality | wall misalignment | min. scaled Jacobian | `checkMesh` | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `single_ellipse` | 12 | admissible | 38.8 | 30.6 | 0.78 | Mesh OK (35.1°, skew 0.64) | 3 |
| `two_circles` | 32 | **resolved** | 64.9 | 7.0 | 0.42 | Mesh OK (63.3°, skew 1.42) | 4 |
| `three_rotated_ellipses` | 80 | admissible | 80.0 | 64.6 | 0.17 | Mesh OK (79.4°, skew 2.88) | 16 |
| `concave_and_convex` | 42 | admissible | 65.6 | 43.5 | 0.41 | Mesh OK (60.5°, skew 1.46) | 5 |
| `four_bodies` | 53 | **resolved** | 65.7 | 13.3 | 0.41 | Mesh OK (64.0°, skew 1.00) | 6 |
| `sharp_bodies` | 45 | admissible | 76.2 | 10.7 | 0.24 | Mesh OK (74.9°, skew 1.47) | 6 |
| `narrow_gap_tip` | 57 | admissible | 69.6 | 21.7 | 0.35 | Mesh OK (67.9°, skew 2.07) | 8 |
| `peanut_body` | 24 | admissible | 79.1 | 40.7 | 0.19 | Mesh OK (79.1°, skew 2.35) | 3 |
| `skimming_tail` | 50 | admissible | 67.4 | 67.4 | 0.38 | Mesh OK (64.3°, skew 2.87) | 9 |
| `tandem_foils` | 49 | admissible | 76.4 | 70.5 | 0.23 | Mesh OK (73.3°, skew 1.96) | 17 |
| `blunt_foil` | 15 | admissible | 70.9 | 70.5 | 0.33 | Mesh OK (68.3°, skew 1.13) | 5 |
| `tandem_blunt` | 45 | admissible | 83.3 | 70.5 | 0.12 | Mesh OK (79.5°, skew 2.14) | 14 |
| `staggered_foils` | 47 | inadmissible | - | - | - | - | 21 |
| `straight_channel` (internal) | 48 | **resolved** | 0.0 | 0.0 | 1.00 | Mesh OK (0.0°, skew 0.00) | 2 |
| `periodic_hill` (internal) | 33 | **resolved** | 29.2 | 21.2 | 0.87 | Mesh OK (36.2°, skew 0.30) | 2 |
| `single_ellipse`, rectangle with inlet, outlet and symmetry sides | 18 | **resolved** | 51.2 | 15.0 | 0.63 | Mesh OK (49.1°, skew 1.21) | 2 |
| `two_circles --span-gaps` | 40 | **resolved** | 66.0 | 7.4 | 0.41 | Mesh OK (57.5°, skew 0.76) | 6 |
| `two_circles --hull`, rectangle at scale 8 | 88 | **resolved** | 49.6 | 3.0 | 0.65 | Mesh OK (47.0°, skew 0.73) | 10 |
| `two_circles --hull`, circle at scale 8 | 88 | **resolved** | 42.4 | 3.0 | 0.74 | Mesh OK (36.7°, skew 0.67) | 8 |
| `make research-tandem` (`tandem_foils --wake`) | 51 | admissible | 70.5 | 17.9 | 0.33 | Mesh OK (69.1°, skew 1.59) | 28 |
| `make research-blunt-foil` (`blunt_foil --wake`, band 0.05) | 18 | **resolved** | 67.9 | 17.9 | 0.38 | Mesh OK (77.6°, skew 0.80) | 7 |
| `make research-tandem-15c` (`--wake --hull`, C-shape r = 15) | 122 | admissible | 70.5 | 6.3 | 0.33 | Mesh OK (55.4°, skew 1.14) | 17 |
| `make research-staggered` (`--wake --hull`, band 0.01) | 160 | admissible | 70.9 | 70.5 | 0.33 | Mesh OK (62.1°, skew 1.99) | 71 |
| `make research-a-airfoil` (C-grid, r = 15, raster 700) | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 263 |

Held-out geometries - not used to tune the engine, several of them refused at
the review that preceded this commit:

| run | blocks | verdict | non-orthogonality | wall misalignment | min. scaled Jacobian | `checkMesh` | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| circle in a square box | 12 | **resolved** | 45.6 | 5.6 | 0.70 | Mesh OK (42.8°, skew 0.80) | 2 |
| square cylinder, circle far field | 16 | admissible | 87.0 | 0.4 | 0.05 | Mesh OK (86.8°, skew 1.41) | 14 |
| square cylinder in a box with equal margins | 16 | admissible | 86.8 | 1.6 | 0.06 | Mesh OK (86.6°, skew 1.20) | 11 |
| ellipse 10:1 | 21 | admissible | 73.9 | 73.9 | 0.28 | Mesh OK (63.3°, skew 1.75) | 12 |
| four symmetric circles (degree-4 junction) | 56 | **resolved** | 64.4 | 4.4 | 0.43 | Mesh OK (62.5°, skew 0.91) | 7 |
| three circles in line | 50 | admissible | 74.9 | 5.3 | 0.26 | Mesh OK (73.9°, skew 1.03) | 6 |
| NACA 0012, circle far field | 19 | admissible | 86.4 | 30.7 | 0.06 | Mesh OK (85.6°, skew 0.92) | 20 |
| NACA 0012 `--wake`, circle far field | 22 | admissible | 82.0 | 36.5 | 0.14 | Mesh OK (71.8°, skew 1.45) | 33 |
| NACA 0012 with a blunt trailing edge, `--wake` | 21 | admissible | 82.5 | 36.5 | 0.13 | Mesh OK (84.2°, skew 1.31) | 31 |
| NACA 0012, C-grid r = 15, band 0.1 | 185 | **resolved** | 52.9 | 18.5 | 0.60 | Mesh OK (44.1°, skew 0.74) | 320 |
| NACA 4412 at 8 degrees, C-grid r = 15, band 0.1 | 225 | **resolved** | 58.1 | 11.8 | 0.53 | Mesh OK (48.2°, skew 1.20) | 184 |
| NACA 4412 with the A-airfoil's options exactly | 225 | **resolved** | 54.3 | 14.3 | 0.58 | Mesh OK (44.6°, skew 1.20) | 187 |
| A-airfoil turned 2 degrees against the far field (incidence) | 210 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (48.1°, skew 1.31) | 307 |
| 30P30N, default options | 134 | inadmissible | - | - | - | - | 137 |
| 30P30N main and flap, `--wake --hull`, C-shape r = 15, band 0.02 | 126 | inadmissible | - | - | - | - | 376 |

The A-airfoil acceptance case under changes that should not matter - the
raster width, the band height, the wake direction, half the points, the
point list's start, and the whole set-up turned rigidly - and the Makefile
options otherwise:

| run | blocks | verdict | non-orthogonality | wall misalignment | min. scaled Jacobian | `checkMesh` | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| raster 420 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 282 |
| raster 500 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 280 |
| raster 600 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 284 |
| raster 650 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 281 |
| raster 700 (Makefile) | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 263 |
| raster 750 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 282 |
| raster 800 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 288 |
| raster 900 | 205 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 292 |
| raster 1000 | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 271 |
| raster 700, band 0.12 | 200 | **resolved** | 51.8 | 12.4 | 0.62 | Mesh OK (44.7°, skew 1.34) | 264 |
| raster 700, band 0.18 | 200 | **resolved** | 49.6 | 12.4 | 0.65 | Mesh OK (46.9°, skew 1.24) | 259 |
| raster 700, wake at 10 degrees | 200 | **resolved** | 50.3 | 10.9 | 0.64 | Mesh OK (42.3°, skew 1.27) | 264 |
| raster 700, wake at 16 degrees | 200 | **resolved** | 51.6 | 13.9 | 0.62 | Mesh OK (49.1°, skew 1.34) | 260 |
| raster 700, every second point dropped | 170 | **resolved** | 50.8 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 169 |
| airfoil, far field and wake as explicit chains | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 262 |
| the same turned 2 degrees together | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 258 |
| the same turned 90 degrees together | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 259 |
| raster 700, point list starting at point 468 | 120 | **resolved** | 55.7 | 12.4 | 0.56 | Mesh OK (53.5°, skew 1.09) | 244 |
| raster 700, point list starting at point 741 | 120 | **resolved** | 51.0 | 12.4 | 0.63 | Mesh OK (48.2°, skew 1.18) | 237 |
| raster 700, point list starting at point 1326 | 120 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (43.0°, skew 1.30) | 236 |
| raster 700, closing point dropped | 200 | **resolved** | 50.7 | 12.4 | 0.63 | Mesh OK (45.1°, skew 1.30) | 237 |

Every one of these is resolved and passes `checkMesh`, with 12.4 degrees of
wall misalignment (10.9 and 13.9 with the wake turned to 10 and 16 degrees);
the review before this commit found four of the nine
raster widths and two of the four start points unresolved or refused, and
the 2-degree incidence change refused. The block count still follows the
raster (200 at widths 700 and 1000, 205 at the other seven; 150 to 185
before the gate past the wall's origin was refined) and the start point
(200 from the trailing edge, 120 from three other starts), because the
C-grid's gate count does.

### Explicit outer boundary

```bash
python experiments/agentic_topology/research_cli.py run --case single_ellipse \
  --farfield-shape rectangle --farfield-box=-4,-3,6,3 \
  --farfield-sides floor:symmetry,out:outlet,roof:symmetry,in:inlet
python experiments/agentic_topology/research_cli.py run --case single_ellipse \
  --outer cap:farfield=cap.dat --outer bottom:farfield=bottom.dat \
  --outer outlet:outlet=outlet.dat --outer top:farfield=top.dat
# a circular domain cut into cap, legs and an outlet arc of 25 degrees either
# side of the wake direction, so the C-grid producer applies to it
python experiments/agentic_topology/research_cli.py run --curve foil=naca0012.dat \
  --farfield-radius 6 --farfield-center 0.5,0 --farfield-outlet 25 \
  --wake --wake-direction 1,0 --layer-height 0.1 --first-width 0.001 --core-size 0.5
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

### Blunt trailing edge (base template)

```bash
make research-blunt-foil     # --case blunt_foil --wake, circle far field
```

`synthetic_cases.blunt_foil`: a teardrop of chord one cut off square where
the flanks are 0.02 apart, corners of 250.5 degrees. Default options with
`--layer-height 0.05`:

| case | blocks | verdict |
| --- | --- | --- |
| blunt foil, circle | 24 | admissible, base applied; 25.6 degrees of wall misalignment in one upper-surface band block, half a degree over |
| truncated teardrop of `test_wake` (base 0.01), circle, test options | 27 | **resolved**, base applied |
| blunt foil, C-shape of radius 4 | 30 | admissible, base applied; the core block beside the lower wake band at 79 degrees, the corner property above |
| blunt foil with a 14-degree edge, C-shape | - | refused at the layout stage: the far field's chain gates advance twice round the outer boundary, the annular far-field limit the hull removes for clusters; a base leaving the hull is not built there yet |

Before the template both corners were refused as "blunt trailing edge",
and the layout could not even keep both corners as gates: two pinned
corners a base width apart crowd each other, and the relaxation released
one, leaving a stray corner gate a tenth of the chord along the flank. The
base's two wake anchors are fixed, so both corners stay.

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
admissible, 85 degrees of non-orthogonality at the landing, 39 degrees of
incidence); `tandem_foils(drop=0.2)`, the rear foil a fifth of a chord
under the front wake, would be met at eight degrees on the rear's upper
shoulder and is aimed fourteen degrees down to land at 43 degrees (40
blocks, admissible, `test_a_grazing_wake_is_aimed_at_the_downstream_band`);
`narrow_gap_tip`
refuses it with the reason that the blocker has no room beside the landing
gate for a band 0.034 wide, its front section on one side being 0.020 long.

### Wake over another body (multi-element wake)

```
make research-staggered      # staggered teardrops, --wake --hull, band 0.01, hull 20 bands out
```

`staggered_foils`: the tandem's rear foil placed as a slotted flap sits under
a main element's trailing edge - nose 0.18 chords ahead of the front tail and
0.174 below it, turned nose-up by 25 degrees, so its highest point lies 0.03
chords under the front wake just behind the tail. In the near field inside
the hull (twenty band heights of 0.01 out, so the junction of the two cells
with the hull stands well above the wake band) the front wake crosses the
rear foil's cell as a chord with a strip on each side, the lens between the
lens-side strip and the medial arc is three quads around a centre vertex,
the rear's band takes three spokes under the wake, and the rear's own wake
leaves through the hull:

| case | near-field blocks | front wake | rear wake | near field |
| --- | --- | --- | --- | --- |
| staggered_foils, hull at 0.2 | 93 | **through the rear's cell** | to the hull | **admissible** |

The hull far field then cuts its levels along one of the two exiting wakes:
the front's first (it passes over the rear), which the slit hull's first
level refuses with a non-convex sector, then the rear's, which builds with
the front's wake band ending at the hull, its exits carried outward as
level spokes (197 blocks, admissible, both wakes in the near field;
`medial.hull.exit_attempts` lists both tries and `ending_wakes` the
front). A far field cut along both bands is the next step, and the one
30P30N needs too - its main wake and flap base both leave.

The multi-element wake needs the hull: with the far field many chords away
the rear foil's far ring lies beyond its tail, the chord would run the whole
length of the body and the exit crossing lands at the sharp tail, which the
writer refuses. Three refusals are inherent to the lens: a fixed gate of the
passed body on the lens arc, a lens with a second corner (a far-field chain
break projecting onto the arc, which is what the plain far field gives), and
a passed body whose wall turns too much under the wake for the three gates
it gets there - the teardrop placed with its nose behind the front tail,
whose nose top projects onto the lens arc, is refused that way. Two
geometric refusals come from the passage being too short or the lens too
thin: a rear foil 0.08 chords under the wake with the hull ten band heights
of 0.02 out puts the junction 0.024 above the wake and the lens-side front
only 0.015 wide between its tips.

**30P30N main and flap** (hull six band heights of 0.02 out): the main's
wake over the flap is planned and written - the main-side turning and
curvature gates beside the trailing edge are dropped because the core
patches between them and the wake front were not convex, and the flap's
band under the wake is rebuilt against the wake band - and the graph has
no problems, but the sampled grids refuse it: the slot between the wake
line and the flap's nose is 0.005 chords deep 0.02 behind the trailing
edge, where the wake band (0.0028-0.0045 thick, the main's band at the
edge continued) leaves the flap's band 0.0001-0.0007 and the core between
them less than that, and the graded counts grid folds there (37 inverted
cells in that core patch; the uniform shape grid is clean). The slot wants
the wake band clearance-limited by the flap exactly as the wall bands are
limited by each other - a front that is the offset of the wake line at
0.35 of the clearance, a polyline rather than a straight line - which is
the next refinement of this template.

### Hull far field (opt-in, two or more bodies)

```bash
make research-tandem-15c    # --case tandem_foils --wake --hull, C-shape of radius 15
python experiments/agentic_topology/research_cli.py run --case two_circles \
  --farfield-shape rectangle --farfield-scale 8 --hull
```

| case | annular construction | hull far field |
| --- | --- | --- |
| tandem foils, C-shape of radius 15 about (1, 0), `--wake` | 73 blocks, inadmissible; the front wake refused at the layout stage by the far-field chain gates crowding the rear tail; 112 s | **116 blocks, admissible**, both wakes (front on the rear foil's band, rear through the hull to the outlet), hull at 0.9, levels at 1.8 and 3.6, then the cap; one core block at the rear edge's seam at 70.5 degrees of non-orthogonality, half a degree over the target; 19 s |
| two circles, rectangle at scale 8 | 45 blocks, admissible, 78 degrees | **88 blocks, resolved** |
| two circles, circle at scale 8 | 42 blocks, admissible, 78 degrees | **88 blocks, resolved** once the levels map between the kinks; by plain fraction the peanut-shaped level twisted one sector onto the round boundary to 79 degrees |
| tandem with a blunt rear edge (`tandem_blunt`), C-shape of radius 15, `--wake` | - | **113 blocks, admissible**: the front wake lands on the rear foil, the rear's base leaves the hull as three strips to the outlet |
| 30P30N main and flap, C-shape of radius 15, `--wake`, band 0.02 | 75 blocks, inadmissible | **179 blocks, admissible** when measured, closed far field with four levels (0.24 to 1.92); neither wake applies yet (the main's passes over the flap, the flap's base leaves the hull), and the near field's worst block is at 89 degrees. **Inadmissible on the current commit** (see the remaining limits) |

The near field is the annular producer's own: the hull is just its outer
boundary, so the 51 to 63 near-field blocks are the same bands, wakes and
core patches as before, built in a fraction of the time because the medial
scaffold no longer reaches seven chords out. The default corpus does not
use the hull and is unchanged.

### Corpus re-baseline (outward anchors, clearance-graded gates)

The anchor rule and the clearance-jump rule moved six default fixtures. Every
fixture is admissible after the change, `skimming_tail` for the first time
and `four_bodies` now resolved; `concave_and_convex` keeps its 42 blocks,
its cove apex now a rounded reflex corner with a full band (the row shows
the state after that rule too). Default options; before is the committed
state `89aa41b`.

| fixture | blocks before → after | admissible | resolved | non-orthogonality | wall misalignment | min. scaled Jacobian |
| --- | --- | --- | --- | --- | --- | --- |
| `single_ellipse` | 12 → 12 | yes → yes | no → no | 38.4 → 38.4 | 29.5 → 29.5 | 0.78 → 0.78 |
| `two_circles` | 32 → 32 | yes → yes | yes → yes | 64.9 → 64.9 | 6.9 → 6.9 | 0.43 → 0.43 |
| `three_rotated_ellipses` | 89 → 87 | yes → yes | no → no | 74.6 → 79.6 | 23.2 → 19.8 | 0.27 → 0.18 |
| `concave_and_convex` | 34 → 42 | yes → yes | no → no | 81.5 → 66.3 | 31.3 → 44.8 | 0.15 → 0.40 |
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
non-orthogonality 59.58 degrees, skewness 0.495 and aspect ratio 80.71. At
that checkpoint the production suite ran 410 tests (27 integration tests
skipped there, passing separately against OpenFOAM) and the research suite
113, with the former expected failure for `skimming_tail`, which then passed.

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

No step inspects a case, chain or body name to choose topology. That now
includes the names of the outer chains: the C-grid's and the hull's outlet
checks measure stations from the bottom leg round the outer loop, so naming
the legs differently no longer switches either construction off
(`test_chain_names_do_not_decide_the_c_grid`). There is no assumed
streamwise direction, unit scale, body count or junction count; every
tolerance derives from the domain scale, the local feature size, curvature,
metric size or numerical precision - BlockDrawer's own convexity test
included, which compares a corner's cross product with its two edge lengths,
so a fixture drawn at a thousandth of its size is still a valid topology.

The self-tests cover one ellipse, two circles of unequal radius, three rotated
ellipses, a concave body with a convex one, four bodies, a body with a sharp
tip, a sharp tip aimed into a narrow gap beside a second body, a body whose
only sharp features are two concave corners, a sharp tail skimming a second
body, a blunt-based foil, tandem foils with a sharp and with a blunt rear
edge, staggered foils, a rectangular channel with inlet and outlet, a channel
with a wall and a symmetry plane, the periodic hill, and domains that must be
refused with their reason - a body inside another, a loop touching itself, a
body flush with the outer boundary, points BlockDrawer would merge.

**Equivalent inputs.** Each of the thirteen external fixtures was run as
twelve equivalent inputs: rotations by 37, 90 and 180 degrees, a mirror,
scaling by 1000, a translation by 1e4, the start point a third of the way
round, both reversals (keeping the first point, and plain), every segment
densified, and a repeated closing point. None of them changes its block
graph, at raster widths 420 and 700 alike (graph only); before the fixes
listed here nine of the thirteen did. Six representation details used to
decide the layout, and each is now a function of the geometry:

* the body frame - the scale every tolerance, the far field and the band
  request derive from - is measured on the closed loop; it dropped the
  closing segment and moved by up to 15 percent with the start point;
* bodies are ordered by their area about their own centroid, not on raw
  coordinates, which a translation by 1e4 rounded differently;
* curvature anchors come from the supplied vertices' turning convolved with
  a periodic Gaussian at least as wide as the vertex spacing, each peak
  located where the curvature's slope is zero, not from a resampling that
  starts at the list's first point, and the anchor limit never splits a
  group of equal peaks;
* a patch is split where half its turning is reached, and judged by, the
  turning as a density on the turning vertices - collinear vertices bound no
  span and a sharp corner is a point mass - so a gate passing a strongly
  turning vertex no longer moves the split by several vertices, an inserted
  midpoint no longer moves it at all, and a gate a rounding error past a
  vertex no longer adds that vertex's whole turning to its patch (a 40-gon
  ellipse's tip-to-tip patch read 180 or 198 degrees);
* the relaxation reads a gate whose window runs past the wall's origin
  inside that window, where the stored station had wrapped to zero and the
  window collapsed around it: the one gate past the origin was never refined,
  and which gate that was followed the start point;
* a closed medial ring is traced again from its point of least clearance,
  measured on the bisector, so its origin and sampling follow the geometry
  and not the raster pixel the seed landed on (two lists of one polygon gave
  rings 2e-4 apart, now 6e-8);
* a cell is seeded only if it still has fewer than two cuts after the cells
  before it were seeded: the far-field cell used to seed a ring the body had
  just seeded, giving four or eight spokes by how the two lists' quarter
  points aligned;
* a curvature peak where the wall turns a corner's worth within a hundredth
  of the perimeter on each side - a thin ellipse's tip, an airfoil's nose -
  is a *rounded convex corner*, forced and pinned like a sharp one
  (`block_layout.TIP_WINDOW`, anchor kind `tip`). The solver bug above had
  pinned exactly those gates by accident whenever the list started at the
  tip, as the synthetic ellipses do; refined, they slid a sixth of the nose
  radius off it and a band block wrapped the nose. The rule mirrors
  `layers.tight_concave_runs` for the concave case; it costs the 15:1
  ellipse, admissible by that accident, its band;
* a *cove* - a wall band patch turning more than the limit the concave way
  whose every split crowded the ring - gets pinned cuts after the refinement
  (`block_layout.cove_anchors`, anchor kind `cove`). The medial axis inside
  a body's own cove is a branch between the body and itself, which the
  diagram does not carry, so the whole cove projects onto the short ring
  piece across its mouth: the 30P30N slat's cove turned 117 degrees from
  the cusp to the lip over 0.03 of ring, and its one band block carried
  85-degree cells that the cavity stage's seam template could only hide.
  The patch is cut into `ceil(turning / limit)` blocks by anchors pinned
  on the cove wall at equal fractions of the turning and spread evenly
  along the ring piece - cross-slot lines from the cove to the body facing
  it, as a channel array would have them - fixed like a wake's, outside the
  ring floor; a spoke that would cross the body's own wall (a deep notch)
  takes the nearest visible ring station or is left out with a note. The
  fixture is a dimpled disk with its cove facing a disk at the mouth
  (`CoveCutTests`); the corpus of thirteen fixtures has no such patch and
  is unchanged;
* gate unwrapping steps between raw stations, so two gates on one station
  are a zero step and not a full turn, and an unpinned gate is clamped only
  when the gates would otherwise wind more than once round the wall;
* tight concave runs ignore collinear vertices, so densifying a wall does not
  change which bends are rounded reflex corners.

`EquivalentInputTests` runs five fixtures under the half turn, the mirror,
the shifted start, the plain reversal and densification on every suite run,
comparing the whole graph summary; with an earlier, half-wall form of the
gate clamp back in, six of its subtests fail.

**Off the corpus.** The first six fixes were chosen and measured on the
thirteen fixtures, and did not carry: `HeldOutEquivalentInputTests` runs
four simple bodies that no rule was tuned on, under the same five inputs
plus starts one and five vertices on, and all four failed. Every vertex of
a coarse list was its own curvature peak, since the smoothing was narrower
than the vertex spacing, and the anchors were chosen among near-ties by list
position or by round-off (the same 24-gon generated another way, 9e-16
apart, gave 21 blocks instead of 18). The last six fixes above came from
those bodies, and two of them now pass as ordinary tests:

| body | as generated | equivalent inputs |
| --- | --- | --- |
| circle, 24 points | 12 blocks | 12 under all seven |
| ellipse 2:1, 40 points | 12 blocks | 12 under all seven |
| ellipse 10:1, 180 points | 24 blocks | topology-valid under all seven, in 24, 21 or 12 blocks |
| NACA 0012, 35 points per side | 22 blocks | 22 under six; 14 and topology-invalid reversed |

The two that fail carry `expectedFailure`. Their layouts are identical
across the inputs, their rings agree to 6e-8 and the solver's starting
states to 1e-12; the coordinate-descent relaxation then takes a different
path on a near-tie, ends in a different local optimum (worst scaled
Jacobian 0.0644 against 0.0587 on the NACA), and the band repair after it
ends in a different graph, once an invalid one. That is a knife edge in the
relaxation, not a reading of the list, and it is as open as the fixtures'
own orientation dependence below.

The turning density serves the split midpoint only. Read as the patch
turning and the relaxation's barrier too - half of a vertex's turning on
each side of a gate on it - it was the truer measure, but the layout's
limits were tuned to the polyline sum: half of a 10:1 ellipse's 20-degree
tip vertex fell to each neighbouring patch and the band was lost, and the
`test_wake` sharp teardrop's far-field patch read 100.16 degrees instead of
99.66 against a limit of 100 and lost `resolved`. The flip that motivated
it - a gate a rounding error past a vertex adding that vertex's whole
turning - is gone with the peaks located exactly.

On the thirteen fixtures the block graph is invariant; its geometry is not
quite. Vertex positions follow the bisector polylines' discretisation, whose
walk starts at the junction the raster finds first (a closed ring starts at
its point of least clearance), and the relaxation sweeps its variables in
ring order from there, so the gates of the same graph land up to a pixel
apart in different orientations. Mostly that moves the shape measures
by a few degrees; on two fixtures it is more (see the remaining limits).

**Raster width.** Every fixture gives the same block graph at raster widths
400, 700 and 1000. A junction whose equidistance refinement stops above
1e-6 of its clearance is two junctions whose pixel clusters merged; the
diagram is then rebuilt 1.5 times finer, up to three times, and a junction
that still does not converge is reported in the notes rather than kept
(`four_bodies` at widths 400 and 420 needs one refinement). The A-airfoil
C-grid still follows the raster through its gate count (see its results).

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
deployed flap makes with the main element. It is admissible since the
clearance-jump rule grades the hull's gates out of the slot; its misses are
67 degrees of wall misalignment at the tip's own seam and a forced length
ratio of 111. Each carries a test on where its misses are. The open
`expectedFailure` tests are the four held-out equivalent-input bodies (see
"Equivalent inputs"); the remaining 30P30N failure modes - the flap-gap
crossing and the slot under the main's wake - have no small fixture yet.

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
  discretisation (measured before the review fixes). Every synthetic fixture
  is raster-invariant at widths 400, 700 and 1000, so this is not reproduced
  in a small case; the A-airfoil C-grid's gate count, and with it its block
  count, follows the raster (200 or 205 blocks over widths 420 to 1000) and
  the start point (200 or 120), although every variant measured resolves.
* **The relaxation's path can decide a graph, and a verdict.** A 10:1
  ellipse under its equivalent inputs and a coarse NACA 0012 listed
  backwards reach the solver with the same layout, rings 6e-8 apart and
  penalties equal to 1e-12, and leave it in different local optima; the
  band repair then gives the ellipse 24, 21 or 12 blocks, all valid, and
  the NACA a topology-invalid graph (`HeldOutEquivalentInputTests`, two
  `expectedFailure` tests). The coordinate-descent grid search is path
  dependent; a continuous polish or a convex formulation would be needed to
  close this.
* **`--no-grid-quality` does not reach the opt-in trials.** The C-grid,
  wake, hull and spanning trials always sample their own grids, because they
  are judged before they commit. A trial that commits under the flag
  therefore carries a fully evaluated verdict and writes its session, while
  the annular result alone fails closed and writes none.
* **The geometry of a graph still follows the orientation.** On the
  thirteen fixtures the block graph is the same for every equivalent input,
  but the bisector polylines' walk starts at the junction the raster finds
  first (a closed ring at its least clearance), and the relaxation sweeps
  its gates in ring order from it, so the same graph's gates land up to a
  pixel apart. On `concave_and_convex` a half turn moves the rounded cove's
  anchor half a pixel to the other side of the axis and the relaxation then
  leaves a core patch at the cove mouth with one inverted sampled cell:
  admissible in every other orientation, inadmissible turned through 180
  degrees. `three_rotated_ellipses` has 13 or 65 degrees of wall
  misalignment depending on the orientation. The wake stages inherit it:
  under `--wake`, `blunt_foil` and `sharp_bodies` keep their graph under
  rotation, mirror, start and reversal, `tandem_foils` keeps 51 blocks except
  mirrored (55), and `tandem_blunt` loses both wakes turned through 180 or
  270 degrees - one trial has an inverted sampled cell, the other a front
  crossing a core spoke - where it builds both otherwise. Starting each ring at its
  largest-clearance junction fixes the rotations of the second but not the
  first; an order-free relaxation and bisector vertices placed from a
  canonical start are the remaining work.
* **A band block may span 100 degrees of wall turning**, which is what gives
  the single ellipse its 27 degree wall misalignment under transfinite
  interpolation. The relaxation keeps that limit (a barrier on each band
  patch's turning), so thin ellipses up to 12:1 are admissible with their
  tips pinned as rounded corners; band blocks are still judged by their
  straight corner quadrilateral, so a body much thinner than that at its
  tips can still fail its band (a 15:1 ellipse does; it was admissible when
  the solver happened to skip its tip gates, see "Equivalent inputs"). `--max-wall-turning` exposes the limit; the measured table
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
  rewrite. Several bodies in a C-shaped far field take the hull far field
  instead (`--hull`). The far-field spokes are straight lines
  from the outermost level set to the cap, so their angle to that level set
  is what the sector count allows (51 degrees non-orthogonality on the
  A-airfoil); curved spokes would improve it.
* **The wake separatrix is opt-in and straight.** One line per sharp feature
  or blunt base, from the feature through the body's own ring: to the outer
  boundary, onto a downstream body's band, or through another body's cell
  (lens and three spokes). A wake that would cross another medial branch,
  meet the outer boundary across a chain break, or enter a third body's cell
  is refused with its reason; a curved wake following the medial branch is
  not built. Wake rewrites whose anchors are ring neighbours do not compose:
  each writer expects the untouched annular faces round its anchor, so a
  slotted flap whose own trailing edge sits beside the main's wake fails.
  Without `--wake-direction` every sharp vertex, a sharp leading edge
  included, gets a wake along its own bisector. The wake band is as thick as
  the band at the edge, which the clearance cap makes thin at a sharp tip.
  The blunt-base rule is a cliff at two percent of the perimeter; a base up
  to four times wider along a nearly straight wall is refused with the
  remedy (`--wake-blunt-ratio`) rather than cut from one corner. The base
  strip's wall-normal edges are graded to the first-cell width, but the
  first-cell check does not measure the strip.
* **The hull far field merges the exiting wakes into one band.** With two
  - 30P30N's main and flap, the staggered foils - the levels are cut once,
  between the upper band's out edge and the lower's in edge, and the hull
  between the bands continues to the outlet as channel strips; the wakes
  do merge in the flow, but the channel's hull pieces keep their counts for
  the length of the far field. When the merged band does not build, each
  wake in turn carries the cut alone with the other's band ending at the
  hull, its exits carried outward as level spokes (`medial.hull.ending_wakes`,
  the attempts under `exit_attempts`), and last the near field is rebuilt
  with the other body's wake excluded (`dropped_wakes`). An exit whose far
  field fails (the slit hull's first level behind the passed body can give
  a non-convex sector) yields to the next.
  30P30N's main and flap with `--hull`
  regressed with the gate-unwrapping clamp: its hull's first level gives a
  non-convex block in one of 18 sectors and the annular fallback has an edge
  crossing, where the committed engine's plain pin release built the hull
  (179 blocks, admissible; 174 with the current code and no clamp). Leaving
  the clamp out restores it but loses the
  tandem front wake and gives the A-airfoil's C-grid layout back the
  committed engine's 11 gates at width 500, where that engine left the
  airfoil unresolved. The hull trial is not compared with the
  annular result it replaces; the C-grid, wake and spanning trials are, and
  never lower the verdict.
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
