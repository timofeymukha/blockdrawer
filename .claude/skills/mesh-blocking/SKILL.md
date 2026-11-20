---
name: mesh-blocking
description: How to build, judge and fix a 2D block-structured mesh with BlockDrawer for OpenFOAM blockMesh or a spectral-element solver - choosing the route (construction operators by hand, or the topology engine), building, shaping, sizing, polishing and the validation ladder ending in checkMesh. Use whenever asked to mesh a geometry, improve or repair a block topology, or review a session.
---

# Mesh blocking with BlockDrawer

You produce a **block topology**: strictly convex quadrilateral blocks whose
opposite edges share cell counts, whose exterior edges carry named patches
and whose curved edges follow the supplied geometry. The session is a JSON
file; `blockdrawer-cli` and the `blockdrawer` MCP tools edit it, and
`blockMeshDict` is exported from it. Topology first; counts and grading are a
later, cheaper stage done by one command (`size`).

This file is the procedure. Open a reference file only when a step sends you
there:

| File | Open it when |
|---|---|
| `engine.md` | you run the topology engine (`research_cli.py`): what it builds per configuration, its options, its report (the algorithm: `experiments/agentic_topology/README.md`) |
| `sizing.md` | you choose `size`'s inputs, read its `verification`, or size by hand |
| `failures.md` | `quality` or `check` names a defect: signature -> the tool that acts on it |
| `examples/README.md` | before your own case: four batches to run end to end |

Tool mechanics (arguments, defaults): `blockdrawer-cli commands`, MCP
`list_commands`. **Kept in step with the engine (`experiments/agentic_topology/`):
a template, acceptance term or option change there updates `engine.md`
and, if the route changes, the table below.**

## Step 1 - Collect the inputs

- **Geometry**: one closed point list per body (`x y` per line, the loop may be
  left implied), far-field chains if given. Keep the points; never resample.
- **Physics**: Reynolds number and target first-point $y^+$ (first width in
  `sizing.md`); incidence goes in the solver's velocity unless the brief gives
  rotated geometry. Far field: the brief's; at least 30 chords externally.
- **Patches**: every chain's name and role (`wall`, `patch`, `symmetry`,
  `cyclic` pairs: identical point distributions, one translation apart).
- **Solver**: OpenFOAM finite volume, or spectral elements of order $p$
  (small counts, exact block curvature; `sizing.md`).
- **Span**: pseudo-2D - `set_export_settings z_min_patch_type=empty
  z_max_patch_type=empty` (with `patch` z faces checkMesh's aspect ratio
  includes the span) - or a periodic slab with reciprocal `cyclic` z patches.

The brief's far field, patch names, first cell and band height are inputs, not
choices. Where it leaves something open, choose (band height: `sizing.md`), and say so.

## Step 2 - Choose the route

Choose by what has been measured, not by what a route intends:

| Case | Route | What it took | Result (checkMesh) | Source |
|---|---|---|---|---|
| Backward step, 2:1 | operators by hand | 8 commands, ~3 min (a hand script: 161 lines, 8 min) | 27 blocks; after `size` 21,750 cells, Mesh OK, 0 deg | `examples/backward_step.txt` |
| | engine | 3 runs | refused: `sweep_correspondence: ribs cross or invert` (the sweep takes the step into the lower guide) | trial `backward_step/REPORT.md` |
| Two grooved cylinders in tandem | operators by hand | 14 commands, ~6 min (a hand script: 299 lines, 25 min, max 42.7 deg) | 251 blocks; after shape/size/polish 47,616 cells, Mesh OK, max 20.5 deg | `examples/grooved_tandem.txt` |
| | engine | 5 runs, 11-23 min each | refused: no groove (pocket) template | trial of Oct 26 (`grooved_tandem/`) |
| NACA 0012 at 5 deg, box far field | operators by hand | 12 commands, ~10 min | 60,976 cells at first width 1e-4, Mesh OK, max 49.6 deg | `examples/naca0012.txt` |
| | engine C-grid, r = 15, band 0.1 | 320 s | 185 blocks, resolved, Mesh OK 44.1 deg | `engine.md`, engine README table |
| Single sharp airfoil, C-shaped far field (A-airfoil) | engine C-grid | 263 s | 200 blocks, resolved, Mesh OK 45.1 deg | engine README table |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70; defects left: the cove seam sliver (b21/b31 at v12), the slot strips | `engine.md`; review measurements in README (`smooth`, `size`) |
| Internal flow with a body (cylinder in a channel) | operators by hand | 14 build commands, 4.5 min | 36 blocks, 21,824 cells, Mesh OK, 0 faces above 70, max 35.6 deg | benchmark round 1, one trial |
| Duct, varying section, cross lines near the wall normals (nozzle) | operators: an open ring per wall with stations, one fill | 16 commands, 3-3.5 min (three agents) | Mesh OK, max 11.5 to 12.0 deg. The engine's sweep: `resolved`, but one core block with near-vertical lines - `resolved` does not test the brief | benchmark round 3, three trials |
| Junction (Y-shaped channels) | operators: open rings (or a band) on the wall chains, the sharp corner open or fanned, fills at the junction | 7-11 min | three banded builds: 24 to 32 blocks, 60,163 to 88,124 cells, Mesh OK, max 29 to 32 deg, first widths within 6% on two (the third 29% fine, likely the open corner: `failures.md`) | benchmark round 3, three trials |
| Periodic passage round a body (compressor cascade) | operators by hand: ring, wake strips, `add_stations offset=`, fills | 52 commands, 22 min | 56 blocks, 53,248 cells, Mesh OK, max 60.1 deg (average 28.0), skewness 2.52. Round 1's trailing-edge fan: 64.7, skewness 3.26 | benchmark rounds 1 and 2; the engine refuses a `cyclic` pair from the command line |
| Multi-element airfoil with coves, engine not allowed | operators by hand plus two vertex scripts (cove pocket fill, hull) | 259 commands, 64 min | 538 blocks, 378,785 cells, Mesh OK, max 62.3 deg. **Weakness**: by operators alone (round 2, fills for coves and far field) checkMesh failed: 949 blocks, skewness 4.07 at a far-field corner, 391 faces above 70 in one sheared cove fill | benchmark rounds 1 and 2, one trial each |

A row records what one trial built and measured; a weakness it names is not
to be copied. Rules that follow from the table; the first that matches decides:

1. **A periodic (`cyclic`) passage with a body in it, internal flow with a
   body, a junction, a turn round a divider tip, a duct whose brief asks for
   cross lines near the wall normals** - by hand with the operators. The engine
   refuses a `cyclic` pair from the command line and a reflex corner on a guide
   wall (any L-shaped channel); its sweep puts ribs where it needs them. Every
   such case above came out Mesh OK by hand.
2. **A single body, a cluster of smooth bodies, a plain internal domain with
   no body and no periodic pair, a sharp airfoil in a C-shaped far field** -
   try the engine first (`engine.md`), in the foreground with a timeout above
   its run (a C-grid took 8 minutes on two cores); fall back to the operators on
   a refusal. A blunt base wider than about two percent of the perimeter has no
   template there (`engine.md`).
3. **Multi-element, cove, groove** - the engine builds 30P30N with defects
   left; by hand it took an hour and two vertex scripts; a groove or pocket the
   operators can state goes by hand.
4. **Anything else the operators can state** (stations on curves, polar
   patches, bands, rings, boxes, fills between fronts) - by hand: minutes.
5. A layout script that computes vertices is the last resort, when no operator
   can express a region (a curve no operator makes, e.g. an offset hull).

## Step 3 - Build

**By hand** (`examples/README.md` has four complete batches):

1. `blockdrawer-cli new SESSION --box XMIN YMIN XMAX YMAX` (MCP `new_session`
   with `box`): one block; put it where a grid cell reuses it, or `remove_edge` it.
2. `import_curve FILE name=NAME` for every body and chain.
3. The layout, from the wall outwards:
   - `add_grid xs=... ys=... skip=...` - Cartesian stations, cells in skip
     boxes, inside `outside=` curves or inside existing blocks left out;
     `add_grid center=X,Y rs=... thetas=... period=DEG` - a polar patch (arcs).
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a fan
     above 60 degrees of turning (one block to 90); `tips=true` adds a station at
     each rounded nose, `normals=140,180,220` where the outward normal has those
     angles, `corner=DEG` sets the corners; `fan=false` leaves a sharp trailing edge open.
   - Internal flow, no block yet: `add_ring WALL1,WALL2 H side=left` - curves
     joined end to start, the band on the fluid's side; stations at ends, joints,
     corners, `xs=`, `points=X,Y;V` (nearest curve point: `xs=` crossing twice, a
     vertex opposite) and `count=`; end spokes run along an inlet curve ending
     there, else normal (the note says where; `start=0,1 end=0,1` set them).
   - A concave domain corner: no block line from it to a convex wall feature (a
     divider tip, a body) - they would share one graded strip (`failures.md`).
   - `add_rows edges=A-B,B-C direction=1,0 to_x=X stations=...` (`rows=N
     growth=G`, `to_y=`, `distance=`) - a chain carried along a direction as
     rows: an open trailing edge's spokes to the outlet (`spread=10` widens
     them, else `size` carries the wall spacing out; `examples/naca0012_cgrid.txt`).
     `between=A,B` takes the shortest unassigned chain: assign the wall first.
     Never from a fan's front (high-lift: 3593 cells above aspect 1000); the note
     names the thinnest column. `far_ratio=2`: mixed on five builds (`failures.md`).
   - `add_band bbox=... height=H rows=N` - more rows along the unassigned
     exterior edges in a box; `to_box=...` carries them to a rectangle
     (spokes at its corners: ring stations by `normals=` aimed at them).
   - `add_stations CURVE xs=... id=x` (`count=`, `between=`, `fractions=`,
     `points=`): standalone stations; `offset=DX,DY` copies them to a periodic partner.
   - `add_fill A,B,C,D` - blocks between what is built (three or five
     corners: round a centre), corners in order round it: a side is the
     exterior chain joining its corners, else open (straight or along
     `curve3=CURVE`) through the standalone vertices on it or new stations
     facing the opposite side's; `side2=...` names one.
   - `add_block_from_vertices A,B points=X,Y;X,Y` - one block. `add_vertex
     x_of=V y_of=W`, `at=V dx=...`, `r=... theta=... about=V`: level with,
     offset from, polar about existing vertices - never type their coordinates (a
     band front at a line/arc tangent joint lies 0.0013-0.0026 off the joint).
   - `add_grid xs=... ys=... outside=BODIES` again for the H-grid round what
     is built (existing vertices on its cell sides become stations); a box
     whose sides carry different stations ("would fall inside the existing
     edge"): one `add_grid` per side.
4. `set_boundary_by NAME type=wall curves=BODY`, `set_boundary_by NAME
   type=patch bbox=...` per far-field side; `between=A,B` for a chain.
5. `set_export_settings z_min_patch_type=empty z_max_patch_type=empty`.

Run it: `blockdrawer-cli apply SESSION --in-place --quiet -f build.txt` (MCP
`edit_session commands_file=... in_place=true`), atomic. Go on when the digest
lists the blocks and patches you meant and `describe SESSION --unassigned
--section summary` (MCP `describe_session`) shows no unassigned exterior edge. A refusal
names the block or edge: change a station. Look at every corner: `render` (MCP `render_session`).

**By the engine**: `engine.md` - the command, the brief's options, the report.
Go on when `admissible` is true; `resolved` is better. It writes `empty` z patches
and its own patch names (`set_edge_boundary`, `rename_boundary`).

On hundreds of blocks read a region (`--bbox`), a block (`--only`) or a count
component (`--component EDGE`). Two cores: `size` 2-12 min and `smooth` 6 min
near 1000 blocks, the engine's C-grid 8.5 min; set a timeout above that.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on
the count-free block map before any count exists; band spokes keep their length
and do not turn from the wall normal; no edge ends below `shape_length_ratio`
(0.1) of its input (unlimited, a ring edge went to 0.002, a jump of 269 after
`size`). Read `reason`: the count-free maximum before and after and "edge
lengths A (EDGE) to B (EDGE) of their input" (`min_edge_ratio`); it is applied
only when no worse ("nothing to gain" is normal on rectangles). Engine square
cylinder: 87.7 -> 56.9 deg at real counts, checkMesh 52.2 sized. Optional. Then:

- Lines meant to stay straight (a wake line, a grid's cross lines) bent: here and in
  the polish `hold_line=A,B;C,D hold_along=line` - the vertices on each segment move
  along it only (cylinder in a channel: 35.99 deg; fixed by `hold=` 41.21; free 34.88,
  chevrons). `hold_box=`/`hold=` fix; `keep_lines=true` holds every line (4-16 deg).
- the polish leaves a jump above 5 where the step shrank an edge (`min_edge_ratio`
  near 0.1, `max_pair_change` large): rerun it with `shape_pair_ratio=2.5`, which
  trades the worst face for jumps and skewness (three cylinders, round 3: 64.7 ->
  67.9 deg, skewness 3.84 -> 3.46, jumps above 2.5 19 -> 10), or a higher
  `shape_length_ratio` (cascade 0.4); neither is a default (README).
- "already fold ... on the count-free block map": a spline running backwards
  (`failures.md`), else move a station, split, or leave the step out.

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A` (inputs in `sizing.md`; where the
brief gives no `wall_aspect`, 25, as all eleven round-2 trials) sets every
count and grading from the wall distance, matches widths across interfaces
and spends a cell budget (default 1.5 times the field's own cells, `budget=`)
on continuity. Read:

- `total_cells`, `budget`, `field_cells`, `budget_binding` (did the budget stop spending).
- `verification` - faces above 70, worst face, skewness, aspect ratio
  (checkMesh fails above 1000, blocks named) for the field's state and the
  result. Spending adds no face above 70 nor cells above aspect 1000.
- `first_width` - per wall patch the cell height normal to the wall at the
  spokes, in percent of the one asked, extremes named (`quality` too).
- `worst_jump` (with the counts above 2.5, 5, 20), `splits`, `refused`,
  `guard`, `note`. Jumps are judged after the polish, not here: it moves
  vertices (NACA 4412: 4 above 5 after `size`, none after the polish; it
  keeps a jump below 5 below 5, `size_ceiling`). `budget_binding=True`
  with jumps above 5 is worth one run with a larger budget (three
  cylinders: 12 -> 6 above 5); one that stays is the shape's (Step 4) or
  the topology's. Beside a fan or an open corner, or across a channel one
  block thick, the budget is not the tool (high-lift: 22 above 5 at a budget
  that was not binding): split the strip or change the blocks (`failures.md`).

Go on when `verification.result` has no face above 70. Faces above 70 in
`verification.field` may go in the polish (Step 6; they did on three
cylinders): polish and `check` first. Where `check` still finds them, locate
the block (`check` maps the set) and use `failures.md` (also for `size
refused`). `method=distance` is a comparison, not a retry (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face
non-orthogonality checkMesh measures, never raising a block's worst face,
wall-band spokes within 5 percent of their length, edges within 50 percent, no
size jump past 2.5 created; applied only when the sampled check at 256 cells a
side is no worse and checkMesh's aspect limit 1000 is not crossed (`reason`
says when it bound). `hold=V1,V2`, `hold_box=X0,Y0,X1,Y1` or `hold_line=A,B` keep
vertices placed on purpose (`hold_along=line`, `x`, `y`: along it only); `blocks=`/`bbox=`
limit it. Read `reason`. `slide=true` slides boundary vertices too; keep it when `check` agrees.
The spokes' 5 percent is `band_tolerance`: the first width moves up to 5 percent
with it. Where the brief wants it exact, `size FIRST CORE keep_counts=true` after the
polish regrades the spokes; `check` after it (39 sessions: 7 maxima +1..32 degrees).

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid
   and exports, untangled, worst non-orthogonality and where, faces above 70,
   max skewness and max aspect ratio and where (above 1000 checkMesh fails), sampled at
   real counts (coarsened above 256 a side) as checkMesh measures them; then
   the worst blocks, edges and warnings. The corner screen (corner angles,
   growth, interface jumps - only there) hides interior distortion.
2. `check SESSION` (MCP `check_mesh`): `blockMesh` then `checkMesh` must
   print `Mesh OK`. It passes with any number of faces above 70 - read the
   severe count and the warnings too. `sets` maps checkMesh's bad faces to
   blocks, worst first, with bounds to render. This is the authority.

Go on when `Mesh OK`, no face above 70 (or the brief's tolerance), skewness
under 4. Otherwise open `failures.md` with the blocks `check` named; after
a fix go back to Step 5 (counts) or Step 6 (polish).

## Step 8 - Deliver

A case directory: the session JSON, the `blockMeshDict` (`export SESSION --case DIR`,
MCP `export_block_mesh_dict`), the batch that regenerates the session, the
checkMesh log, a README (topology outward from the wall, patch names and
types, first-cell and far-field sizes and their derivation, the measured
numbers, what is still wrong). Report in the acceptance vocabulary - valid,
untangled, `Mesh OK` with its severe-face count and maximum, engine
`admissible`/`resolved` - never "good mesh".
