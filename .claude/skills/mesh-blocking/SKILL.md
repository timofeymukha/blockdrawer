---
name: mesh-blocking
description: How to build, judge and fix a 2D block-structured mesh with BlockDrawer for OpenFOAM blockMesh or a spectral-element solver - choosing O, C or H topology, wall bands and far field, sizing and grading, and the validation ladder ending in checkMesh. Use whenever asked to mesh a geometry, improve or repair a block topology, or review a session.
---

# Mesh blocking with BlockDrawer

You are producing a **block topology**, not a mesh of cells: a set of strictly
convex quadrilateral blocks whose opposite edges share cell counts, whose
boundary edges carry named patches, and whose curved edges follow the supplied
geometry exactly. The session is a JSON file; `blockdrawer-cli` and the
`blockdrawer` MCP tools edit it, and `blockMeshDict` is exported from it.
Get the topology right first; counts and grading are a later, cheaper stage.

Everything below is the methodology. Tool mechanics are in the MCP tool
descriptions and `blockdrawer-cli commands`. **This skill is kept in step with
the topology engine in `experiments/agentic_topology/`: when a template, an
acceptance term or an option changes there, update the decision points here.**

## 1. Collect before drawing

- **Geometry**: one closed point list per solid body (`x y` per line), or a
  CAD file assembled into closed contours. On the engine's thirteen test
  fixtures, orientation, start point and point density do not change the
  block graph, and so do a 24-gon and a 40-point ellipse; a 180-point 10:1
  ellipse with midpoints inserted and a NACA 0012 with 35 points per side
  listed backwards still end in a different, invalid graph, because the
  relaxation takes a different path from a state equal to 1e-12
  (`HeldOutEquivalentInputTests`). So when a run is refused or unresolved,
  retry with the list reversed and with a shifted start before changing
  anything else, and keep the best verdict. Gate positions can also move by a
  raster pixel between orientations, which has flipped one fixture's
  verdict: judge a candidate in the orientation you export. Keep the
  supplied points; never resample geometry from a picture or a raster.
- **Physics**: Reynolds number and the target first-point $y^+$; Mach and angle
  of attack are solver inputs - keep the chord aligned and impose incidence
  through the free-stream velocity. Far-field distance: at least 30 chords for
  external aerodynamics; a few body sizes is enough for a topology test.
- **Boundaries and roles**: which chains are `wall`, `inlet`, `outlet`,
  `farfield`, `symmetry`, `cyclic`; their names. A cyclic pair needs identical
  point distributions related by one translation.
- **Solver**: OpenFOAM finite volume, or a spectral-element code where every
  block cell is a curved element of order $p$ - then counts are small, block
  curvature must be exact, and quality is judged per element.
- **Span**: pseudo-2D (`z_min_patch_type=empty z_max_patch_type=empty`, one
  cell in $z$; the topology engine writes its sessions this way) or a periodic
  slab (reciprocal `cyclic` spanwise patches, `z_cells` from the spanwise
  resolution).

## 2. Take the brief as given

The person asking has usually decided the domain already: the far-field
shape and distance, the patch names and their roles, the free-stream
direction, the first-cell height or the $y^+$ it comes from, the band
height. Those are inputs, not choices for you to make, and each maps onto
the engine directly:

| In the brief | Engine input |
|---|---|
| far field: a circle of radius $R$ about a point | `--farfield-shape circle --farfield-radius R --farfield-center X,Y`; `--farfield-outlet DEGREES` cuts it into cap, legs and an outlet arc about the wake direction when a C-grid is wanted |
| far field: a C-shape, or a rectangle | `--farfield-shape cshape` / `rectangle` with the same radius and centre |
| far field: these point lists, these patch names | `--outer NAME:ROLE=PATH` per chain, anticlockwise, joined end to end; the names become the patches |
| internal flow: these wall / inlet / outlet point lists | the same `--outer` chains with no `--curve`: the chains are the whole boundary and the sweep producer builds it (roles `wall`, `inlet`, `outlet`, `symmetry`; a `cyclic` pair is refused there, the built-in `--case periodic_hill` shows it) |
| free-stream direction, incidence | `--wake-direction dx,dy` (also selects which sharp vertices get a wake); incidence stays in the velocity |
| boundary layer: first cell, band height | `--first-width W`, `--layer-height H` (absolute, in the geometry's units); `--core-size` for the far cells |
| wall-parallel stretching | `--wall-aspect 25`: cells along the wall 25 first widths long, the usual RANS boundary-layer stretching; without it the wall is resolved as finely along as across |
| which edge decides a count | `--count-rule max`: the finest demand in an equality component wins, so a wall piece tied to the far field is resolved as the wall asks (a real mesh); the default `mean` lets the long far-field pieces decide and under-resolves such walls |
| cell sizes continuous across blocks | `--interface-growth 1.25`: the wall-normal edges are graded finest first so the cells match across every block interface (a band's last cell starts the core spoke, a thin wake strip's width starts its neighbour), the cell-to-cell ratio capped at 1.25 by raising counts; the report's `sizing.interface_grading.mismatches` names the interfaces one series per edge cannot match |
| patch names | `rename_boundary OLD NEW` on the session afterwards |

What the engine then *builds* follows from the geometry - how many bodies,
which have sharp edges, how they sit - not from a topology you pick; the
next section says what each configuration gets and what it refuses, so you
can read the report. Only where the brief leaves something open do the
defaults there apply (a smooth single body meshes well inside a circle; a
sharp trailing edge wants a C-shape or a cut circle so its wake reaches an
outlet; a cluster far from the far field wants `--hull`), and then say in
the deliverable which choice you made and why.

## 3. What the engine builds, per configuration

Each entry names the construction the topology engine builds or refuses
for a configuration, with the measured reach, so the engine's report tells
you which case you are in and what to expect. None of them asks you to
choose a far field: that came with the brief.

- **Smooth body (cylinder, ellipse), external flow**: an O-grid - a wall band,
  a core to a ring, and far-field patches - inside whatever far field the
  brief gives. In a rectangle with inlet, outlet and two far-field sides
  each corner is a gate whose spoke follows the corner bisector, so two
  blocks share it at 45 degrees: the classical O-in-a-box, not a defect.
  The engine builds it for a square cylinder in a box with equal margins
  too (a body corner and a box corner on one diagonal become one spoke),
  and for ellipses up to 12:1, whose tips are pinned block corners like a
  sharp corner; a 15:1 ellipse still fails its band at the tips.
- **Sharp trailing edge** (fluid sector of at least 250 degrees at one wall
  vertex): a **C-grid**. The band continues past the edge as a two-sided wake
  band to the outlet, its lines fanning from the wake line at the
  `--wake-spread` slope (default 0.05; the edges get the full slope, the
  lines between slopes spaced evenly by rank) so every strip widens
  downstream by the same amount -
  a band kept at its body width for fifteen chords puts first-cell-thin
  cells beside chord-long ones (aspect 1e4 on 30P30N); the far-field core
  is cut open along it. The edge then carries four blocks at right angles. Never leave a
  seam wedge or a fan at a production trailing edge: it collapses the
  body-normal lines into a downstream fan. Engine: `--wake`; refusals list the
  reason under `medial.wakes`. Give `--wake-direction` whenever the body has
  another sharp vertex (a sharp leading edge): without it every vertex sharp
  enough gets a wake along its own bisector, upstream included.
- **Sharp trailing edge upstream of another body** (tandem foils, a slat
  ahead of a main element): the wake band crosses the gap and ends on the
  downstream body's band. Its centreline is that body's spoke at the
  stagnation gate, and its two fronts continue as two band spokes beside that
  gate, so the wake wraps the nose exactly as an embedded C-grid does; the
  count across the wake is then the count along those two nose pieces, which
  the engine reports as a wake-landing coupling, not a defect. The landing
  needs the band across the wake: the strips beyond the ring end at the
  landing gate at the angle the wake line makes with the front, slivers
  under 35 degrees. So a wake that would graze the band along the wake
  direction (a slat's edge onto the main's upper surface, which runs almost
  along the free stream there) is *aimed*: at the ring its far leg turns
  towards the body, a degree at a time, to the first direction whose
  estimated incidence passes with a margin, and the writer still refuses
  under 35 degrees measured on the real band (the record's `landing` gives
  the aim, `landing_incidence_degrees` the measure). Engine: the
  same `--wake`; the plan record's `target` says `body` or `outer`. With a
  circle far field two teardrop foils in tandem get both wakes; with a C-shaped
  far field the annular construction still fails at the downstream foil's
  tail, where the far-field chain gates crowd it - that is the far-field
  limit, not the wake's.
- **Several bodies with a far field many chords away**: add `--hull`. The
  engine then builds the near field inside the cluster's level set (the
  hull, six band heights out) with the annular construction and grows the
  far field as level sets of the whole cluster beyond it, cut open along the
  wake that leaves the cluster; the medial ring no longer sits at half the
  far-field distance and the far field's chain breaks no longer gate the
  bodies. Tandem foils at fifteen chords in a C-shaped far field build this
  way with both wakes (`make research-tandem-15c`); the reason for a refusal
  is under `medial.hull`.
- **Blunt base** (two convex corners within about two percent of the
  perimeter): the base template - the base is a wall edge whose block
  continues downstream as the wake's core strip, and the two corners' wake
  lines bound it with a wake band continuing each flank band outside. Engine:
  `--wake` builds it (`make research-blunt-foil`); the record's `kind` is
  `base`. The core block beside each wake band meets the flank front at 180
  degrees less the edge angle, so a thin trailing edge leaves that block near
  70 degrees of non-orthogonality in the annular construction. The two wake
  lines bounding the base strip are graded to the first-cell width at the
  corners. A corner whose partner lies a little beyond the two-percent rule
  along a nearly straight wall (a flat-back section) is refused with the
  remedy rather than cut lopsidedly from one corner: raise
  `--wake-blunt-ratio` to build it as a base.
- **Edge or base whose bisector is off the free stream** (a deflected
  flap's base, a slat's trailing edge pointing down onto the main): a block
  side cannot bend inside one block, so the wake leaves along the edge's
  bisector (a base's flank bisector) and turns towards `--wake-direction`
  at **turn stations**, at most `--wake-turn` degrees each (default 50;
  each block beside a station sees half), all before the body's ring. A
  station is a rung across the wake band carried round the body's cell as
  a level between band and ring, like the far field's level sets; the
  record says `turned`, lists `stations` and says where they are under
  `turn_placement`. Within ten degrees of the bisector the wake stays
  straight. The stations go *inside* the ring when they fit (spaced by the
  band height, so a band as thick as the ring clearance allows fits one),
  when the ring does not bend beside the crossing (the medial ring behind a
  convex corner is a fan round it, and the chord from the front's crossing
  to the next gate's ring point meets a wake direction crossing the fan
  obliquely in a sliver - 30P30N's flap base); a level row meeting another
  sharp corner's seam wedge on its way round the cell crosses it along the
  wedge's bisector (30P30N's slat, whose cusp sits beside its edge); else
  *beyond* the ring, in the layer between ring and outer boundary, unless
  that boundary bends beside
  the exit the same way; else *straight*: the wake keeps its bisector
  through the near field and the body it ends on takes it as it arrives
  (the slat's edge onto the main), or the hull's far field turns it (the
  flap's base: through the ring and the hull along the flank bisector).
  Each rung's reason is in `turn_note`; a full refusal asks for a larger
  `--wake-turn` or a farther ring (`--hull-height-ratio`). A wake over
  another body keeps its straight chord.
- **Sharp trailing edge whose wake passes over another body** (a main
  element's wake over its flap): the wake runs through that body's cell as
  a straight chord with a wake strip on each side; the body-side strip is
  tied to the passed body's band by three spokes (where the strip enters
  the cell, halfway along the chord, where it leaves), and the sliver
  between the lens-side strip and the medial arc over the body - a curved
  triangle - is three quads around a centre vertex. The passed body's band
  is thinned to stay clear of the wake band and carries exactly those three
  gates under the wake. Engine: `--wake` with `--hull` (the far ring must
  lie above the body, which only the hull's near-field ring does; with a
  plain far field the chord runs beyond the body's tail); the plan record
  carries `through` and `lens`. Refused, with the reason under
  `medial.wakes`, when the body's wall turns too much under the wake for
  three gates, when a corner or chain break projects onto the lens arc,
  when the lens has a second corner, or when the wake would enter a third
  body's cell. The staggered teardrops (`make research-staggered`) get it
  in the near field inside the hull with the rear's own wake beside it; the
  hull far field *merges* the wakes leaving the cluster into one band - the
  levels cut between the upper band's out edge and the lower's in edge, the
  hull between them continued to the outlet as channel strips
  (`medial.hull.merged_wakes`) - and, when that does not build, cuts along
  one wake with the other's band *ending at the hull* (`ending_wakes`,
  `exit_attempts`), and last rebuilds the near field with the other body's
  wake dropped (`dropped_wakes`). 30P30N's main and flap build this way:
  `--wake --hull --farfield-shape cshape --farfield-radius 15
  --farfield-center 0.8,0 --layer-height 0.03 --first-width 0.001
  --core-size 1.0` gives 153 blocks, admissible, with the main's wake over
  the flap and the flap's base wake (along its flank bisector through the
  ring and the hull) merged into one band to the outlet. It is not
  resolved: the main's trailing-edge cluster carries 85-degree corners. The
  option region is narrow and a run takes eight minutes, so start from
  these values rather than sweeping.
- **Multi-element bodies** (slat, main, flap): the *effective airfoil* is a
  chain of wall pieces and **bridges** - straight cuts across a cove mouth or
  from a cusp to the next element - and the regions the chain skips are
  **pockets** filled by their own quad arrays. Each element keeps its own
  collar; an element's wake ends on the next element's band or on the outlet.
  Narrow gaps between elements take one strip of core blocks between the two
  bands rather than two half-cores around a medial line (engine: `--span-gaps`,
  opt-in, limited reach). The engine's measured state on the full 30P30N
  (slat, main, flap, the main + flap recipe above): admissible, 203
  blocks, **all three wakes**: the main's wake over the flap and the flap's
  base wake (along its flank bisector through the ring and the hull)
  merged into one band to the outlet, and the slat's edge wake turning 41
  degrees at one station inside the ring, its level row through the cusp's
  seam wedge, its far leg aimed 13 degrees down at the main's band and
  landing at 40 degrees of incidence on the upper nose. The slat's cove
  carries two pinned cove cuts - cross-slot lines to the main's lower nose,
  since the cove's own medial axis is not in the diagram - so its band
  blocks turn 45 degrees at most instead of 117. Not resolved: 84-degree
  cells remain at the cusp's seam and the slat edge's seam, the main's
  cove lip corner is a proper mitre (its band used to pinch to a tenth
  there), and the slat's level row ties the
  ring and front counts into one component of length ratio 473. Sized
  with `--wall-aspect 25` (cells along the wall 25 first widths long) the
  run gives 5532 cells and a `checkMesh` that passes: no negative volumes,
  max non-orthogonality 78 degrees on 64 faces, max skewness 3.1, max
  aspect ratio 180 in the far wake. The non-orthogonal faces are where a
  band front arrives at a wake seam almost along the wake front - the
  main's and the slat's trailing edges, the flap's base - which is the
  C-grid's trailing-edge corner (the lower front and the wake front are
  one line when the wall there runs along the wake), plus the main's cove
  lip wedge and the flap's band under the main's wake. Repair those by
  hand (the repair recipe in section 4); the engine's next steps are a
  smooth join of band front and wake front at the seam, the count
  coupling of the level rows and the 12-degree seam wedge at the cove's
  reflex corner. Refined to about
  fifty cells across each band (`--first-width 5e-5 --growth 1.08
  --count-rule max`) the run gives 444k cells, and the cell sizes jump
  by up to 330 across the interfaces of the thin wake strips and of the
  band and core spokes graded from different ends; `--interface-growth
  1.25` grades the wall-normal edges from their neighbours (504k cells,
  `checkMesh` OK, worst interface jump 117, severely non-orthogonal
  faces 7543 from 9335, max aspect ratio 284 from 637). With the main's
  cove lip mitred the fine mesh is 475k cells, max non-orthogonality
  84.7 from 89.5, skewness 2.8 from 3.2; the lip's blocks lost most of
  their severely non-orthogonal faces (635 from 3307) but the seam wedge
  at the cove's reflex corner - two seam spokes converging at 12 degrees
  on one ring anchor far below the pocket - gained them (5210 from 1326)
  once its core spokes were graded from the ring end, so the total rose
  to 8725. What remains are that wedge and the
  two-ended edges - a wake line or hull piece asked for fine cells at
  both ends, which one geometric series cannot give - listed under
  `sizing.interface_grading.mismatches`; those want a split or a
  multi-section grading by hand.
- **Internal flow** (channel, periodic hill, diffuser): a sweep between the two
  walls - columns from one wall band to the other, ends on inlet and outlet or
  on the cyclic pair. Corners of the domain are block corners; a guide wall's
  curvature is followed by the columns. The sweep reads the domain as one
  four-sided region, so a reflex corner on a guide wall - a backward-facing
  step, any L-shaped channel - is refused today (`sweep_correspondence: ribs
  cross or invert`, the step taken into the lower guide, whatever the band
  height, wall-turning limit, raster width or start point): build those by
  hand as an H-grid of rectangles, every domain corner a block corner, the
  step corner a valence-three vertex, one corner block at the step foot
  carrying both bands, one series per row and column. The 2:1 step came out
  Mesh OK at 0 degrees of non-orthogonality in 36 blocks and eight minutes
  that way (`/tmp/timofey/data/blockdrawer/backward_step/`).
- **Concave corner** (fluid sector below about 157 degrees): the corner is a
  gate and its spoke runs along the bisector; the two adjacent blocks meet it at
  45 degrees. Acceptable; a single corner block with a 90-degree corner is
  better and is a documented follow-up. The band keeps the corner's height
  one corner height along each wall (the mitre shadow) and thickens beyond;
  a pocket corner whose band pinched to a tenth, or whose band blocks
  overlap the core patch beside the corner's spoke, is a defect of an
  older engine, not a feature of the geometry.
- **Singularities** (interior vertices of valence 3 or 5) are where fans,
  wakes and gap strips need them. Keep them off the wall; the total index is
  fixed by the domain's Euler characteristic, so every extra valence-5 vertex
  needs a valence-3 partner somewhere.

## 4. Build outward from the wall

Layers, from the inside out: wall - **collar** (the boundary-layer band, a
mitred normal offset) - near-field marched offsets - an algebraic blend - the
medial ring or a transition circle - the far field. The collar's front, not
the wall, is the inner boundary of the core.

**Size the collar from the boundary layer, not from habit.** Where measured or
computed profiles exist, the band height is 1.5 to 2 times the largest
$\delta_{99}$ on the body, usually the suction side near the trailing edge; on
the A-airfoil at 13 degrees that is 0.15 chords ($\delta_{99} = 0.094c$ at
$x/c = 0.99$). "One to a few percent of the chord" is only the attached-flow
default when no profile is available. If a profile is truncated before
$U = 0.99\,U_e$, fit $\delta^*$, $\theta$ and the shape factor and extrapolate;
say so.

**Use the topology engine first** where it applies - single bodies, several
disjoint smooth bodies, simply connected internal domains - **and know its
measured reach**. A single sharp-edged airfoil in a C-shaped far field is
the C-grid producer's case: `--wake` with `--farfield-shape cshape
--farfield-radius R --farfield-center X,Y` (or the same boundary via
`--outer`) and an explicit `--layer-height`. It chooses the body's gates
itself - band blocks without sliver corners at the requested height, at most
45 degrees of wall turning per block, a block whose band still folds cut
again - and builds the band by wall normals, three more level sets at heights
growing by three, the wake band along the given direction spreading at
`--wake-spread`, and straight spokes to the cap; the A-airfoil at fifteen chords comes out `resolved` in
200 blocks with the band at its full height, 12 degrees of wall misalignment
and 51 degrees of non-orthogonality (raster width 700). It resolves at every
raster width from 420 to 1000, start point, small incidence change and rigid
turn tried, but the gate count, and with it the block count (120 to 225),
still follows the raster and the start point. It refuses,
with the reason under `medial.cgrid`, anything but one body, one wake and the
cap-leg-outlet-leg boundary; a rectangle or circle around a single airfoil
falls back to the annular construction, which is admissible only to about
six chords there. Two foils in tandem build in a circle far field with both
wakes (`make research-tandem`), admissible but not resolved at the trailing
edges' seams; multi-element bodies with coves are still hand builds. NACA
0012 and a cambered NACA 4412 resolve in the same C-grid set-up (185 and 225
blocks). A smooth body that misses only its wall misalignment can
take a tighter `--max-wall-turning`: the 2:1 ellipse resolves at 45 degrees
in 24 blocks (10 degrees of misalignment) where the default 100 gives 12
blocks and 27 degrees.

```bash
python experiments/agentic_topology/research_cli.py run \
  --curve airfoil=airfoil.dat --wake --wake-direction 0.9744,0.2250 \
  --farfield-shape cshape --farfield-radius 15 --farfield-center 1,0 \
  --layer-height 0.15 \
  --session out/session.json --json out/report.json --output out/topology.png
```

Or supply the outer boundary chain by chain, anticlockwise and joined end to
end, for a C-shaped far field: `--outer cap:farfield=cap.dat --outer
bottom:farfield=bottom.dat --outer outlet:outlet=outlet.dat --outer
top:farfield=top.dat`. Give the band height absolutely with `--layer-height`
(the ratio form multiplies the whole domain's scale, which the report prints
as `domain.domain_scale`). Read the report's
acceptance terms - `topology_valid`, `grids_evaluated`, `untangled`,
`within_shape_targets`, `sizing_feasible`, `admissible`, `resolved` - and the
refusal reasons (`trial_crashes` lists defects, never geometry); with
`--no-grid-quality` the annular result is not sampled, so its grid terms are
false and it writes no session. An opt-in trial (`--wake`, the C-grid,
`--hull`, `--span-gaps`) always samples its own grids to be judged, so a trial
that commits is fully evaluated and does write its session; the
`describe`, `cavities`, `candidates`, `apply --move` and `focus` subcommands
let you choose among validated alternatives instead of placing vertices.

**Where the engine cannot** (two wake bands leaving a cluster through the
hull - one is dropped,
pockets), write
geometry code, not clicks: a layout module that computes every vertex, curve
and block independently of BlockDrawer, and a generator that emits an
auditable `.jsonl` command batch and applies it in one atomic step:

```bash
blockdrawer-cli apply case.json --in-place -f build_commands.jsonl
```

Through the MCP the same batch is `edit_session` with
`commands_file="build_commands.jsonl"` and `in_place=true`; inline
`commands` are for a handful of corrections, not for a generated topology.

Two rules decide a hand-built sharp trailing edge, and neither is visible
until checkMesh fails. The collar spokes at the trailing-edge end of each side
must carry the **cross-wake direction** (the direction of the cut through the
edge), not the corner bisector, or the band blocks fold across the cut. And a
normal offset of height $h$ from a side that meets the edge at half-wedge
angle $\beta$ overshoots the cross-wake line by $h \sin\beta$, so the first
station on each side must be held out from the edge by more than that, with a
straight front edge, so every band block stays on its own side of the cut.

A hand build starts from `new SESSION --box XMIN YMIN XMAX YMAX` (the
`new_session` tool's `box`), which places the one block every session has
where the layout wants it - the inlet channel, say - instead of moving the
unit square's corners into place. The commands that carry a hand-built
topology: `add_vertex X Y id=NAME`
(name the vertices - `step_foot`, `lip_3` - so the layout script writes
every block, grading and patch command without predicting generated IDs),
`add_block_from_vertices`, `set_edge_type spline|polyLine|arc`,
`set_control_point_count` and `set_control_point` for curved edges,
`import_curve` plus `project ... fit=true` to put wall edges exactly on the
supplied geometry, `add_boundary`, `rename_boundary`, `set_boundary_type`,
`set_edge_boundary`, `set_export_settings`. Prefer `split_edge` and
`combine_blocks` for conformal refinement of a strip over rebuilding blocks by
hand; `split_edge v3-v4 0.5 id=core_mid` names the new vertex (the strip's
others become `core_mid_1`, `core_mid_2`, ...) so the same batch can grade
the halves (`set_edge_grading v3-core_mid end_width 0.017 propagate=true`).
Commit the batch with the session so the topology can be regenerated and
reviewed.

**Running the engine.** A run takes seconds on a synthetic body and five to
twenty minutes on an airfoil at fifteen chords, so start it in the
background and poll, never in a shell call with a two-minute timeout. Its
default sizing is relative to the *domain* scale (about 40 chords for a
15-chord far field), which gives a first cell of 0.08 chords and one cell on
most edges: give the sizes in the geometry's units with `--first-width` and
`--core-size` (and `--layer-height` for the band). A fabricated C-shaped far
field is written as the patches `cap`, `top`, `bottom` and `outlet`, a
rectangle as its four named sides: merge what the case wants as one patch
with `set_edge_boundary` onto one of them and `rename_boundary` it, rather
than rebuilding the assignments. When the result is admissible but not
resolved, do not stop at the report: `quality_report worst=5` names the
blocks that miss, `render_session` zoomed on them shows why, and the
remedies are the ones in section 6 - a sharp or rounded corner that is not
a block corner wants a split there, a wake or far-field block with a
170-degree corner wants its ring vertex moved (`move_vertex`) or the strip
split, a band capped by the inscribed-disk rule in a circular far field
wants the C-grid producer or a hand-built nose. `repair_block ID` does the
local part of this for you: it tries moving the block's interior vertices
(their curved edges carried along), merging it across an internal edge,
splitting its edges and regrading an edge to the series of its opposite
edge, judges each on the block and its neighbours by the corner screen
*and* the sampled transfinite interior at the real cell counts (so a merge
across a curved edge that folds between the corners is refused), and
applies the best - the one that raises the worst cell by half a degree, or
removes a tenth of the severely sheared cells when the worst cell is a
block corner no move can change; repeat on the worst block until it
reports a structural defect, or "nothing to repair" - a neighbourhood with
no severe cell and every angle at least 45 degrees from degenerate, where
the remaining defects are size jumps or aspect ratios that `repair_block`
does not judge: read the quality report and fix those with `split_edge` and
`set_edge_grading`. Crossed gradings are the first thing it finds:
two opposite edges graded towards opposite ends shear every cell between
them while the block's corners stay as they were, and the quality screen's
corner angles do not see it. On the full 30P30N at 475k cells twelve
`repair_block` calls on the blocks checkMesh named (the cove's seam wedge
twice, the slat cusp's dart twice, the cove core twice, the three seam
corners, the flap base) took the severely non-orthogonal faces from 8725
to 138 with the mesh still OK, in one batch of two minutes; an agent doing
the same by hand with `set_edge_grading` and `move_vertex` reached 596 in
36 minutes. On 30P30N main + flap eight repairs took the worst corner from
2 to 14 degrees and checkMesh from 87 to 76 degrees of non-orthogonality,
and there they stop: the cove and slot are sliver fans, and a fan does not
become an array by local moves. A hand-built layout is the fallback, not
the first move. What the local moves cannot do on the full case is listed
in the engine README's 30P30N notes: an edge two blocks need graded
opposite ways (the cove core, a topological defect), a wake strip whose
straight split edges fold its curved blocks, and the two-ended edges that
want a multi-section grading. A *circular* far field with `--wake` falls
back to the annular construction, since the C-grid producer wants a cap,
two legs and an outlet; add `--farfield-outlet 25` and the circle is cut
into those four chains on the circle itself (an `outlet` arc of that
half-angle about the wake direction) and the C-grid applies - a NACA 0012
in a circle of six chords went from 40 degrees of wall misalignment to a
Mesh OK C-grid at 54 degrees of non-orthogonality. The arc must be wider
than the outermost wake level, or the wake line leaves through a leg; on a
circle the block at the smooth chain break beside the wake carries a
near-180-degree corner that wants a `split_edge` and a centre vertex.

## 5. Size and grade - after the topology is right

- **First cell** from the target $y^+$ with a screening friction coefficient
  $C_f \approx 0.003$:

$$
u_\tau/U_\infty \approx \sqrt{C_f/2}, \qquad
y_1^+ \approx Re_c \sqrt{C_f/2}\;\frac{y_1}{c}.
$$

  For a spectral-element mesh the first interior collocation point sits at
  about $0.064$ element heights for $p = 7$, so the first element height is
  $y_1 / 0.064$.
- **Growth** at most 1.2 to 1.3 per cell inside the band:
  `set_edge_grading v0-v1 start_width 0.002` on the band spokes (the small
  cell goes at the first vertex as you write it; write the wall vertex
  first); grading
  propagates through the opposite-edge constraint component when asked
  (`propagate=true`).
- **Counts are shared** along every constraint component (opposite edges of
  every block through the chain). `describe` lists the components. A
  component that holds a wall-tangent edge and a band-normal spoke ties
  streamwise resolution to boundary-layer resolution: that is a topology
  defect - fix the blocks, not the grading. A component whose long edge is a
  far-field arc and whose short edge is a wall segment only means large
  far-field cells, which is normal.
- **Spacing links** (`add_spacing_link driver follower`) keep cell widths
  continuous across block interfaces. Along the wake the rows start with the
  band's widths at the cross-wake line; **do not let them spread to a uniform
  transverse spacing at the outlet**. blockMesh blends the cross-line and the
  outlet distributions in proportion to streamwise distance, so a uniform
  outlet opens the first wake row by an order of magnitude within a few
  chords and produces the worst faces of the mesh (74 degrees and skewness 1.8
  on the A-airfoil). Grade the outlet's transverse edges so the spacing on the
  wake centre line stays of the order of the first streamwise wake cell; that
  alone brought the same mesh to 68 degrees and skewness 0.8.
- **The wall-to-far-field station map** is the largest lever on near-wall
  orthogonality in a hand-built O- or C-grid. Distribute the far-field
  stations by the arc length of the **band front**, not of the wall (a wall
  arc-length map put spokes 67 degrees off the normal on the A-airfoil, the
  front map 52) and not by wall turning (which spreads far-field cell sizes by
  two orders of magnitude).
- Aspect ratio matters inside the band only; interface size jumps should stay
  below about 2.5 inside the near field.

## 6. Validate - the ladder

1. `validate_session`: loads, topologically valid, exports.
2. `quality_report` (`summary_only=true` for the headline lines, `worst=N` for
   the N worst blocks and interfaces): corner angles 30 to 150 degrees,
   non-orthogonality at most 65 degrees, corner-cell aspect at most 100,
   growth at most 1.3, interface size jump at most 2.5. **These are measured
   at the block corners from the first mesh cell.** A block with curved
   spokes hides its interior distortion from them entirely: the A-airfoil
   read 18 degrees here and 68 in checkMesh. Only `check_mesh`, or the
   engine's sampled shape grid, sees the interior.
3. For a hand-built layout, check it **before** it becomes a session: the
   layout module should assert every block strictly convex from its true
   corner points, no two edges crossing (ray tests on the sampled curves), and
   the true corner angles at the wall within limits. Nothing in the production
   tools does this for you.
4. `render_session` zoomed on every trailing edge, corner, gap and bridge
   (`zoom`, `margin`, `preview`, `highlight`; `return_image=false` with
   `output` when the picture is a deliverable rather than something to look
   at). Look for folded or sliver blocks and for spline overshoot.
5. `check_mesh`: `blockMesh` then `checkMesh` must print `Mesh OK`. Read
   maximum non-orthogonality, skewness, aspect ratio and every negative-volume
   or `***` line. This is the authority. **With `patch` z faces checkMesh's
   aspect ratio includes the spanwise direction** (a unit-depth slab of the
   engine's `skimming_tail` read 58,379; the same mesh with `empty` z patches
   20), so set both to `empty` for a pseudo-2D case; where the $z$ faces stay
   non-empty the $z$ thickness is a quality parameter, not a free choice:
   with a first cell of $4.5\times10^{-6}c$ only $0.002 < \Delta z < 0.0045$
   passed.
6. The engine's report, when it built the topology: the shape grid (eight
   uniform cells per block side) gives count-independent non-orthogonality,
   skewness and wall misalignment, as lower bounds of the continuous values;
   a fold check at 32 cells per side guards `untangled` against folds between
   those samples, and `acceptance.untangled_by_grid` says which grid folded -
   an inversion on the counts grid alone comes from the default counts and
   grading, so change those, not the topology; the structure report gives the length ratio every component
   forces before any count exists, and
   `sizing.feasibility.failures_without_exemptions` what the far-field fan-out
   and wake-landing exemptions waive. These acceptance terms
   (`admissible`, `untangled`, `within_shape_targets`, `resolved`) exist only
   for engine output; a hand-built session is judged by `validate_session`,
   the quality screen and `Mesh OK`.

Failure signatures and their fixes:

- **Negative volumes beside a mitred or capped station**: the spline's
  interior points were built from the plain local normal while the end
  stations carry the mitre direction, so the offset curve overshoots and folds
  the cells it bounds. Build the interior points from the local normal offset
  and correct them linearly onto both end stations.
- **A folded band block at a sharp convex feature**: the feature needs more
  incident blocks (seam, fan, wake), not a thinner band.
- **A core spoke crossing a front inside a narrow gap**: the gap holds too
  many layers; span it with one strip or move the ring.
- **A 3-degree corner in a seam wedge at a trailing edge**: the medial anchor
  is not inside the feature's sector; use the wake construction.
- **Length ratio far above 20 in one component**: the report separates
  far-field fan-out (`farfield_fanout_ratio`, informational: a tangential
  component whose longest edge lies on a non-wall outer chain - large far
  cells are normal) from band-to-core coupling, which still fails
  `sizing_feasible` and is a topology defect; every component is judged, not
  only the short `worst` table.
- **A band that thins to nothing on a thin body**: the annular producer capped
  the band at half the local thickness (the inscribed-disk rule) and a nose
  block spanning too much turning; the C-grid producer switches the cap off
  and cuts nose sectors, so run it, or pass more gates, before lowering the
  requested height.
- **A gate that slid off a sharp vertex**: the sharp vertex must be a block
  corner; pin it and re-spread the neighbouring gates.

## 7. Deliver

A case directory with the session JSON, the exported `blockMeshDict`, the
command batch that regenerates the session, the retained `checkMesh` case or
log, and a README that states the topology outward from the wall, the patch
names and types, the first-cell and far-field sizes with their derivation, the
measured quality numbers, and what is still wrong. Report in the acceptance
vocabulary - valid, untangled, within shape targets, structurally feasible,
`Mesh OK` - never "good mesh".

## Where things are

- Editor commands and their arguments: `blockdrawer-cli commands`, or the
  `list_commands` MCP tool. Batches are atomic.
- Topology engine: `experiments/agentic_topology/README.md` for the algorithm,
  the measured results and the open limits; `research_cli.py run|describe|
  cavities|candidates|apply|focus`.
- Worked examples, when present: `cases/naca0012_cgrid` (sharp-edge C-grid with
  a six-block wake), `cases/a_airfoil` (C-grid with the wake deflected 13
  degrees, band sized from measured profiles, `Mesh OK` at 15 chords, built
  entirely by a layout module and a command batch) and `cases/30p30n`
  (three-element C-chain with bridges and pockets, and the diagnosed
  spline-overshoot defect).
