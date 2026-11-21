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

- **Geometry**: one closed point list per body (`x y` per line, the loop may be left implied),
  far-field chains if given. Keep the points; never resample.
- **Physics**: Reynolds number and target first-point $y^+$ (first width in `sizing.md`);
  incidence goes in the solver's velocity unless the brief gives rotated geometry. Far field:
  the brief's; at least 30 chords externally.
- **Patches**: every chain's name and role (`wall`, `patch`, `symmetry`, `cyclic` pairs:
  identical point distributions, one translation apart).
- **Solver**: OpenFOAM finite volume, or spectral elements of order $p$ (small counts, exact
  block curvature; `sizing.md`).
- **Span**: pseudo-2D - `set_export_settings z_min_patch_type=empty
  z_max_patch_type=empty` (with `patch` z faces checkMesh's aspect ratio
  includes the span) - or a periodic slab with reciprocal `cyclic` z patches.

The brief's far field, patch names, first cell and band height are inputs, not
choices. Where it leaves something open, choose (band height: `sizing.md`), and say so.

## Step 2 - Choose the route

Choose by what has been measured, not by what a route intends:

| Case | Route | What it took | Result (checkMesh) | Source |
|---|---|---|---|---|
| Backward step, 2:1 | operators by hand | 8 commands, ~3 min (a hand script: 161 lines, 8 min) | 27 blocks; after `size` 21,750 cells, Mesh OK, 0 deg. Engine: 3 runs, refused (`sweep_correspondence: ribs cross or invert`) | `examples/backward_step.txt` |
| Channel with a cavity | one `add_grid`, after the backward-step example | 3.4 min | 36 blocks, 71,850 cells, Mesh OK, 0 deg | benchmark round 4 |
| Two grooved cylinders in tandem | operators by hand | 14 commands, ~6 min (a hand script: 299 lines, 25 min, max 42.7 deg) | 251 blocks; after shape/size/polish 47,616 cells, Mesh OK, max 20.5 deg. Engine: 5 runs of 11-23 min, refused (no groove template) | `examples/grooved_tandem.txt` |
| NACA 0012 at 5 deg, box far field | operators by hand | 12 commands, ~10 min | 60,976 cells at first width 1e-4, Mesh OK, max 49.6 deg | `examples/naca0012.txt` |
| | engine C-grid, r = 15, band 0.1 | 320 s | 185 blocks, resolved, Mesh OK 44.1 deg | `engine.md`, engine README table |
| Single sharp airfoil, C-shaped far field (A-airfoil) | engine C-grid | 263 s | 200 blocks, resolved, Mesh OK 45.1 deg | engine README table |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| Three staggered cylinders (a cluster) | engine hull, or by hand: a ring and `to_box` per body, one H-grid round the boxes, 16 `move_vertex` snaps | hand: 12 min | engine: Mesh OK, 67.9 deg (average 21.7), skewness 3.46; by hand: 42.6 deg (10.2), 0.93 | benchmark rounds 3 and 4, one trial each |
| Cylinder with a splitter plate (concave body corners) | ring round the body, mitred at the junctions; each junction wall's front carried along the other wall by `add_rows conform`; wake rows, far field by rows | 31 min | 461 blocks, 91,114 cells, Mesh OK, max 52.6 deg (average 15.1) | benchmark round 4 |
| Elbow with a guide vane | ring on the vane, polar `add_grid` with `outside=`, four fills | 11 min | 92 blocks, 35,575 cells, Mesh OK, max 33.1 deg | benchmark round 4 |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70; defects left: the cove seam sliver (b21/b31 at v12), the slot strips | `engine.md`; review measurements in README (`smooth`, `size`) |
| Internal flow with a body (cylinder in a channel) | operators by hand | 14 build commands, 4.5 min | 36 blocks, 21,824 cells, Mesh OK, 0 faces above 70, max 35.6 deg | benchmark round 1, one trial |
| Duct, varying section, cross lines near the wall normals (nozzle) | operators: an open ring per wall with stations, one fill | 16 commands, 3-3.5 min (three agents) | Mesh OK, max 11.5 to 12.0 deg. The engine's sweep: `resolved`, but one core block with near-vertical lines - `resolved` does not test the brief | benchmark round 3, three trials |
| Junction (Y-shaped channels) | operators: open rings on the wall chains, the sharp corner open with a station straight across the branch from it; a five-sided fill, or four-sided fills with the mitre's bisector | 7-15.5 min | round 4, three trials: 19, 30, 38 blocks, Mesh OK, max 32.1 / 25.0 / 24.9 deg. The corner fanned instead: 8 min lost, five jumps above 5, a spoke 82% fine | benchmark rounds 3 and 4 |
| Periodic passage round a body (compressor cascade) | operators by hand: ring, wake strips, `add_stations offset=`, fills | 52 commands, 22 min | 56 blocks, 53,248 cells, Mesh OK, max 60.1 deg (average 28.0), skewness 2.52. Round 1's trailing-edge fan: 64.7, skewness 3.26 | benchmark rounds 1 and 2; the engine refuses a `cyclic` pair from the command line |
| Multi-element airfoil with coves, engine not allowed | operators by hand plus two vertex scripts (cove pocket fill, hull) | 259 commands, 64 min | 538 blocks, 378,785 cells, Mesh OK, max 62.3 deg. **Weakness**: by operators alone (round 2, fills for coves and far field) checkMesh failed: 949 blocks, skewness 4.07 at a far-field corner, 391 faces above 70 in one sheared cove fill | benchmark rounds 1 and 2, one trial each |

A row records what one trial built and measured; a weakness it names is not
to be copied. Rules that follow from the table; the first that matches decides:

1. **A periodic (`cyclic`) passage with a body in it, internal flow with a body, a junction,
   a turn round a divider tip, a duct whose brief asks for cross lines near the wall normals** -
   by hand. The engine refuses a `cyclic` pair from the command line and a reflex corner on a
   guide wall; its sweep puts ribs where it needs them. Every such case above was Mesh OK by hand.
2. **A single body, a plain internal domain with no body and no periodic pair, a sharp
   airfoil in a C-shaped far field** - try the engine first (`engine.md`), in the foreground
   with a timeout above its run (a C-grid took 8 minutes on two cores); fall back to the
   operators on a refusal. A blunt base wider than about two percent of the perimeter has no
   template there. **A cluster of smooth bodies**: one hand trial beat the engine's hull
   (three cylinders, above); by hand where the bodies fit boxes of one H-grid.
3. **Multi-element, cove, groove** - the engine builds 30P30N with defects left; by hand it
   took an hour and two vertex scripts; a groove or pocket the operators can state: by hand.
4. **Anything else the operators can state** (stations on curves, polar
   patches, bands, rings, boxes, fills between fronts) - by hand: minutes.
5. A layout script that computes vertices is the last resort, when no operator
   can express a region (a curve no operator makes, e.g. an offset hull).

## Step 3 - Build

**By hand** (`examples/README.md` has four complete batches):

1. `blockdrawer-cli new SESSION --box XMIN YMIN XMAX YMAX` (MCP `new_session`
   with `box`): one block; put it where a grid cell reuses it, or away from the
   geometry (an operator refuses blocks over it) and `remove_edge` it once the
   first operator has built (the only block cannot be removed).
2. `import_curve FILE name=NAME` for every body and chain.
3. The layout, from the wall outwards:
   - `add_grid xs=... ys=... skip=...` - Cartesian stations, cells in skip
     boxes, inside `outside=` curves or inside existing blocks left out;
     `add_grid center=X,Y rs=... thetas=... period=DEG` - a polar patch (arcs).
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a fan
     above 60 degrees of turning (one block to 90); `tips=true` adds a station at
     each rounded nose, `normals=140,180,220` where the outward normal has those
     angles (`near=X,Y` per angle where it has one at several places), `corner=DEG`
     sets the corners; `fan=false` leaves a sharp trailing edge open.
   - Internal flow, no block yet: `add_ring WALL1,WALL2 H side=left` - curves
     joined end to start, the band on the fluid's side; stations at ends, joints,
     corners, `xs=`, `points=X,Y;V` (nearest curve point: `xs=` crossing twice, a
     vertex opposite) and `count=`; end spokes run along an inlet curve ending
     there, else normal (the note says where; `start=0,1 end=0,1` set them).
   - A concave domain corner: no block line from it to a convex wall feature (a
     divider tip, a body) - they would share one graded strip (`failures.md`).
     A concave body corner (a plate on a cylinder): the ring mitres it; carry
     each wall's front along the other wall by `add_rows ... conform` (splitter row).
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
   - `add_fill A,B,C,D` (or `points=X,Y;X,Y;V;...`: corners by position survive a
     re-layout) - blocks between what is built (three or five corners: round a
     centre), corners in order round it: a side is the exterior chain joining its
     corners, else open (straight or along `curve3=CURVE`) through the standalone
     vertices on it or new stations facing the opposite side's; `side2=...` names
     one. Read the note: it says when a side has another reading.
   - `add_block_from_vertices A,B points=X,Y;X,Y` - one block. `add_vertex X Y
     id=NAME`; `x_of=V y_of=W`, `at=V dx=...`, `between=A,B fraction=F`, `r=...
     theta=... about=V`: level with, offset from, between, polar about existing
     vertices - never type their coordinates (a band front at a line/arc tangent joint
     lies 0.0013-0.0026 off it: the refusal names the `move_vertex`).
   - `add_grid xs=... ys=... outside=BODIES` again for the H-grid round what
     is built (existing vertices on its cell sides become stations); a box
     whose sides carry different stations ("would fall inside the existing
     edge"): one `add_grid` per side.
4. `set_boundary_by NAME type=wall curves=BODY`, `set_boundary_by NAME
   type=patch bbox=...` per far-field side; `between=A,B` for a chain.
5. `set_export_settings z_min_patch_type=empty z_max_patch_type=empty`.

Run it: `blockdrawer-cli apply SESSION --in-place --quiet -f build.txt` (MCP `edit_session
commands_file=... in_place=true`), atomic. Go on when the digest lists the blocks and patches
you meant and `describe SESSION --unassigned --section summary` (MCP `describe_session`) shows
no unassigned exterior edge. A refusal names the block or edge: change a station. Look at every
corner: `render SESSION -o x.png --bbox X0,Y0,X1,Y1` (MCP `render_session`); `validate` names
blocks lying over one another.

**By the engine**: `engine.md` - the command, the brief's options, the report. Go on when
`admissible`; `resolved` is better. Its z patches are `empty`; rename its patches (`rename_boundary`).

On hundreds of blocks read a region (`--bbox`), a block (`--only`) or a count component
(`--component EDGE`). Two cores: `size` 2-12 min and `smooth` 6 min near 1000 blocks, the
engine's C-grid 8.5 min; set a timeout above that.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on the count-free
block map before any count exists; band spokes keep their length and do not turn from the wall
normal; no edge ends below `shape_length_ratio` (0.1) of its input (unlimited, a ring edge went
to 0.002, a jump of 269 after `size`). Read `reason`: the count-free maximum before and after,
`min_edge_ratio` and `max_pair_change`; applied only when no worse ("nothing to gain" on rectangles).
Optional; on 19 hand builds, sized and polished, it lowered checkMesh's maximum by over a degree
against no step on 10 (y-junction 39.1 -> 32.0), raised it on 3 (triangle +12.3) (README). Then:

- Lines the brief requires (a wake line, cross lines): held here and in the polish (Step 6).
- `min_edge_ratio` 0.1 with `max_pair_change` above 15: `shape_pair_ratio=2.5` was right on
  one round-4 build (triangle: jumps above 2.5 / 5 19 / 3 -> 7 / 0, max 28.0 -> 25.9) and wrong
  on another (three cylinders: 42.6 deg for the default step's 33.3, no jump above 5 either
  way): size both and keep what `check` prefers. The 19 builds are hand builds; on the
  engine's NACA 4412 the step cost 47% more cells and four jumps above 5.
- "already fold ... on the count-free block map": a spline running backwards
  (`failures.md`), else move a station, split, or leave the step out.

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A` (inputs in `sizing.md`; where the brief gives no
`wall_aspect`, 25, as all eleven round-2 trials) sets every count and grading from the wall
distance, matches widths across interfaces and spends a cell budget (default 1.5 times the
field's own cells) on continuity. A lower `budget=N` is how cells come down towards a reference
(bluff body over ground: 240,083 -> 165,684 cells at the same quality with 200000). Read:

- `total_cells`, `budget`, `field_cells`, `budget_binding` (did the budget stop spending);
  `verification` - faces above 70, worst face, skewness, aspect ratio (checkMesh fails above
  1000, blocks named) of the field's state and the result; spending adds neither.
- `first_width` - per wall patch the height normal to the wall at the spokes against the one
  asked; `note` names strips kept on one ratio and `raised` raises through spokes (Step 6).
- `worst_jump` (counts above 2.5, 5, 20), `splits`, `refused`, `guard`. Judge jumps after the
  polish, which moves vertices (round 2's NACA 4412: 4 above 5 after `size`, none after; it keeps
  a jump below 5 below 5). `budget_binding=True` with jumps above 5: one run with a larger budget
  (three cylinders: 12 -> 6); one that stays is the shape's (Step 4) or the topology's - beside a
  fan, an open corner or across a channel one block thick the budget is not the tool (high-lift:
  22 above 5): `failures.md`.

An average several times the reference's or the block angles': the `quality` verdict names the
blocks that make it (`average_blocks`); `size` no longer shears thin blocks for nothing (U-bend
11.7 -> 3.3), so it is their shape (Step 4, `failures.md`); `grading=strip` on a copy compares.

Go on when `verification.result` has no face above 70. Faces above 70 in `verification.field`
may go in the polish (Step 6; they did on three cylinders): polish and `check` first.
Where `check` still finds them, locate the block (`check` maps the set) and use `failures.md`
(also for `size refused`). `method=distance` is a comparison, not a retry (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face non-orthogonality
checkMesh measures, never raising a block's worst face, wall-band spokes within 5 percent of
their length, edges within 50 percent, no size jump past 2.5 created; applied only when the
sampled check at 256 cells a side is no worse and checkMesh's aspect limit 1000 is not crossed
(`reason` says when it bound). Lines the brief requires (a wake line, cross lines) a free polish
bends: hold them in the shape step and here, `hold_line=A,B;C,D hold_along=line` (along them only;
cost: triangle 24.8 -> 27.3 deg, cylinder in a channel 35 -> 41). `hold=V1,V2`/`hold_box=` fix
vertices, `keep_lines=true` holds every line; `blocks=`/`bbox=` limit it.
`slide=true` slides boundary vertices too; keep it when `check` agrees.
The spokes' 5 percent is `band_tolerance`: the first width moves up to 5 percent with it.
Where the brief wants it exact, `set_first_width FIRST` after the polish grades the band spokes
alone (counts kept) and reports the jumps. `size ... keep_counts=true` regrades every strip: under
`grading=strip` the first width got worse in three trials (U-bend -27 -> -50 percent), under
`field` the average (cylinder in a channel 3.26 -> 4.07).

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, no blocks over one another, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid and exports, untangled,
   worst non-orthogonality and where, faces above 70, max skewness and max aspect ratio and
   where (above 1000 checkMesh fails), sampled at real counts (coarsened above 256 a side) as
   checkMesh measures them; then the worst blocks, edges and warnings. The corner screen (corner
   angles, growth, interface jumps - only there) hides interior distortion.
2. `check SESSION` (MCP `check_mesh`): `blockMesh` then `checkMesh` must print `Mesh OK`. It
   passes with any number of faces above 70 - read the severe count and the warnings too. `sets`
   maps checkMesh's bad faces to blocks, worst first, with bounds to render. The authority.

Go on when `Mesh OK`, no face above 70 (or the brief's tolerance), skewness under 4. Otherwise
open `failures.md` with the blocks `check` named; after a fix go back to Step 5 or Step 6.

## Step 8 - Deliver

A case directory: the session JSON, the `blockMeshDict` (`export SESSION --case DIR`, MCP
`export_block_mesh_dict`), the batch that regenerates the session, the checkMesh log, a README
(topology outward from the wall, patch names and types, first-cell and far-field sizes and their
derivation, the measured numbers, what is still wrong). Report in the acceptance vocabulary -
valid, untangled, `Mesh OK` with its severe-face count and maximum, engine `admissible`/`resolved`
- never "good mesh".
