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

This file is the procedure. Open a reference file only when a step sends you there:
`engine.md` (the topology engine, `research_cli.py`: per configuration, options, report),
`sizing.md` (`size`'s inputs, its `verification`, sizing by hand), `failures.md` (a defect
`quality` or `check` names -> the tool), `examples/README.md` (four batches, end to end).

Tool mechanics (arguments, defaults): `blockdrawer-cli commands`, MCP `list_commands`.

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
| Backward step, 2:1 | operators by hand | 8 commands, ~3 min | 27 blocks; after `size` 21,750 cells, Mesh OK, 0 deg. Engine refused (ribs cross) | `examples/backward_step.txt` |
| Channel with a cavity | one `add_grid`, after the backward-step example | 3.4 min | 36 blocks, 71,850 cells, Mesh OK, 0 deg | round 4 |
| Channel with an orifice plate | one `add_grid` (the cavity row), stations off the plate (Step 3) | 6 min | 34 blocks, 56,208 cells, Mesh OK, max 3.95 deg | round 5 |
| Two grooved cylinders in tandem | operators by hand | 14 commands, ~6 min | 251 blocks; after shape/size/polish 47,616 cells, Mesh OK, max 20.5 deg. Engine refused (no groove template) | `examples/grooved_tandem.txt` |
| NACA 0012 at 5 deg, box far field | operators by hand | 12 commands, ~10 min | 60,976 cells at first width 1e-4, Mesh OK, max 49.6 deg | `examples/naca0012.txt` |
| Sharp airfoil, C-shaped far field (NACA 4412) | by hand: the C-grid example, a half-annulus fill to the inlet arc, `set_first_width` | 19 build commands | 385 blocks, 113,484 cells, Mesh OK, max 38.6 deg (average 3.9); the engine's best 205,506 cells, 45.1 (7.3) | round 6 |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| Three staggered cylinders (a cluster) | by hand: a box per body (`to_box`), its sides on lines shared with the others, a ring station by `normals=` wherever another box's line crosses; one H-grid; `move_vertex` snaps | 8-12 min | three hand trials 42.6, 37.0, 32.7 deg; engine hull 67.9 | rounds 3 to 6 |
| Cylinder with a splitter plate (concave body corners) | ring mitred at the junctions, each junction a fill (`failures.md`); wake rows, far field by rows | 31 min | Mesh OK, max 52.6 (round 4), 48.9, 43.2, 47.9 deg (round 6) | rounds 4 to 6 |
| Elbow with a guide vane | ring on the vane, polar `add_grid` with `outside=`, fills; a rounded body end: a box from the cap's 45-degree stations; `keep_lines=true` in the polish where the brief asks radial lines | 11 min | Mesh OK, max 33.1 and 31.4 deg | rounds 4 to 6 |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70 | `engine.md` |
| Cylinder in a channel | operators by hand: ring, band to a box, grid | 4.5 min | Mesh OK, max 35.6 and 35.1 deg | rounds 1 to 6 |
| Duct with cross lines near the wall normals (nozzle) | an open ring per wall with stations, one fill; no shape step | 16 commands, 3-3.5 min | Mesh OK, max 11.8 deg; the engine's sweep resolved with near-vertical core lines | rounds 3 to 6 |
| Junction (Y-shaped channels) | open rings; the open corner's chain first (`fan=false`), `points=CORNER_ID` across the branch; the corner a station on a fill side, not its corner; split the corner's spokes (`failures.md`) | 5.6-11.2 min | four trials: max 24.8 to 25.8 deg, first widths within 7% | rounds 3 to 6 |
| Periodic passage round a body (compressor cascade) | by hand: ring `fan=false`, wake strips, `add_stations offset=`, fills | 22 min | Mesh OK, max 54.4 deg; the stagger sets the average, about 26, in a single-row periodic layout | rounds 1 to 6 |
| Multi-element airfoil with coves, engine not allowed | operators only: three rings, cove and slot fills, wake rows, far field by rows | 59 min | round 5: 1822 blocks, 219,594 cells, Mesh OK, max 68.3 deg, no jump above 5 | rounds 1, 2, 5 |
| Two airfoils in tandem | two rings `fan=false`, wake rows (`spread_about`), far field by rows, two fills | 16 min | 1058 blocks, 118,320 cells, Mesh OK, max 40.2 deg | round 5 |
| Eccentric annulus | a ring on each wall (`side=in` outside), stations paired on lines normal to both walls, two half-annulus fills | 6.6 min | 96 blocks, 24,297 cells, Mesh OK, max 11.7 deg | round 5 |

A row records what benchmark trials built and measured; a weakness it names is
not to be copied. Rules that follow from the table; the first that matches decides:

1. **A periodic (`cyclic`) passage with a body in it, internal flow with a body, a junction,
   a turn round a divider tip, a duct whose brief asks for cross lines near the wall normals** -
   by hand. The engine refuses a `cyclic` pair from the command line and a reflex corner on a
   guide wall; its sweep puts ribs where it needs them. Every such case above was Mesh OK by hand.
2. **A sharp airfoil in a C-shaped far field**: by hand from `examples/naca0012_cgrid.txt` (NACA
   4412: 113,484 cells, 38.6 deg, against the engine's best 205,506 and 45.1). **A
   smooth single body, a plain internal domain with no body and no periodic pair** - try the engine
   first (`engine.md`), in the foreground with a timeout above its run; fall back to the
   operators on a refusal. A blunt base wider than about two percent of the perimeter has no
   template there. **A cluster of smooth bodies**: by hand where they fit boxes of one H-grid.
3. **Multi-element, cove, groove** - the engine builds 30P30N with defects left; operators alone
   took an hour (round 5); a groove or pocket: by hand.
4. **Anything else the operators can state** (stations on curves, polar
   patches, bands, rings, boxes, fills between fronts) - by hand: minutes.
5. A layout script that computes vertices is the last resort, when no operator
   can express a region (a curve no operator makes, e.g. an offset hull).

## Step 3 - Build

**By hand** (`examples/README.md` has four complete batches):

1. `blockdrawer-cli new SESSION --box XMIN YMIN XMAX YMAX` (MCP `new_session`
   with `box`): one block, away from the geometry (an operator refuses blocks over it);
   `remove_edge` it once the first operator has built (the only block cannot be removed).
   A box an operator reused is part of the mesh: removing it leaves a hole, and `to_box`
   reusing a grid cell left the grid non-conformal.
2. `import_curve FILE name=NAME` for every body and chain.
3. The layout, from the wall outwards:
   - `add_grid xs=... ys=... skip=X0,Y0,X1,Y1,...` - Cartesian stations, cells in skip
     boxes (four numbers a box, one list), inside `outside=` curves or existing blocks left out;
     `add_grid center=X,Y rs=... thetas=... period=DEG` - a polar patch (arcs).
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a fan
     above 60 degrees of turning (one block to 90); `tips=true` adds a station at
     each rounded nose, `normals=140,180,220` where the outward normal has those
     angles (`near=X,Y` where it has several); `fan=false` leaves
     a sharp trailing edge open; `counts=14,14` edges per section between stations;
     `facing=V1;V2` stations on another wall's normals there (hand pairings did better). The
     note pairs front and wall IDs and names each `normals=`/`points=` station's vertex.
   - Internal flow, no block yet: `add_ring WALL1,WALL2 H side=left` - curves
     joined end to start, the band on the fluid's side; stations at ends, joints,
     corners, `xs=`, `points=X,Y;V` (nearest curve point: `xs=` crossing twice, a
     vertex opposite) and `count=`; end spokes run along an inlet curve ending
     there, else normal (the note says where; `start=0,1 end=0,1` set them).
   - A long band beside a mitred or tilted spoke, a long edge into a wall face: a station
     about a band height from it (a channel width for a long straight band), or the tilt
     shears the whole block (bump 41.3 -> 33.0 deg), the edge is one series (orifice:
     104,767 cells, 48,508 with).
   - A concave domain corner: no block line from it to a convex wall feature (a
     divider tip, a body) - they would share one graded strip (`failures.md`).
     A concave body corner (a plate on a cylinder): the ring mitres it; carry
     each wall's front along the other wall by `add_rows ... conform` (splitter row).
   - `add_rows edges=A-B,B-C direction=1,0 to_x=X stations=...` (`rows=N
     growth=G`, `to_y=`, `distance=`) - a chain carried along a direction as
     rows: an open trailing edge's spokes to the outlet (`spread=10` widens
     them, else `size` carries the wall spacing out; `examples/naca0012_cgrid.txt`);
     `to_y=V` takes V's y. An open trailing edge's spokes are normal to their sides: move
     the one off the cross-wake line, `move_vertex V x_of=W carry_curves=true` (airfoil
     tunnel: average 5.56 -> 4.70). A blunt base with both corners open: `failures.md`.
     `between=A,B` takes the shortest unassigned chain: assign the wall first.
     Never from a fan's front; the note names the thinnest column; `far_ratio=2` is mixed
     (`failures.md`).
   - `add_band bbox=... height=H rows=N` - more rows along the unassigned
     exterior edges in a box; `to_box=...` carries them to a rectangle, each box corner
     moved onto the nearer spoke beside it: aim a ring station at each corner (`normals=`),
     and on a half square one facing each box side.
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
     vertices - never type their coordinates (a refusal names the `move_vertex` for a vertex off its line).
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
blocks lying over one another and a cyclic pair that does not match: run it after pairing.

**By the engine**: `engine.md` - the command, the brief's options, the report. Go on when
`admissible`; `resolved` is better. Its z patches are `empty`; rename its patches (`rename_boundary`).

On hundreds of blocks read a region (`--bbox`), a block (`--only`) or a component (`--component
EDGE`). Two cores: `size` 2-12 min, `smooth` 6 min near 1000 blocks; set a timeout above that.
Above about 1500 blocks (high-lift, 2118: `size` 7.7 min, the polish unfinished at 11.7): fewer
far-field blocks, and polish by `blocks=`/`bbox=` round `check`'s worst blocks.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on the count-free
block map before any count exists; band spokes keep their length and do not turn from the wall
normal; no edge ends below `shape_length_ratio` (0.1) of its input. Read `reason`: the count-free
maximum before and after, `min_edge_ratio` and `max_pair_change`; applied only when no worse.
Optional: on 19 hand builds it lowered checkMesh's maximum by over a degree on 10, raised it on 3
(README). Then:

- What the brief fixes, held here and in the polish (Step 6): it pinches a wake band to a third
  (airfoil tunnel: `hold_box=X0,Y0,X1,Y1` on the band, 15k cells more), turns fans at a blunt base
  (triangle: lines 17 deg off; held, a jump of 5.5), makes a box an octagon (cylinder in a channel),
  tilts the root of a long block (a wake's start: hold it, or a station one block width downstream -
  cylinder in a channel, average 5.09 -> 2.66); `hold_along=x` (or `y`, `line`) slides held vertices along their line.
- `min_edge_ratio` 0.1 and a large `max_pair_change`: `shape_pair_ratio=2.5` made some builds better
  and others worse, the same case either way on repeats: size both, keep the one with no jump
  above 5. An engine session needs the step (NACA 4412: 45.1 deg with it, 69.6 without).
- "already fold ... on the count-free block map": a spline running backwards (`failures.md`).

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A first_widths=PATCH=W` (`sizing.md`; no `wall_aspect`
in the brief: 25) sets every count and grading from the wall distance, matches widths across
interfaces and spends a cell budget (default 1.5 times the field's own cells) on continuity. A
lower `budget=N` cuts cells and coarsens the field, first widths included (Y-junction 80,062
-> 52,382 cells, a first width 20 percent coarse). A cell cap in a region:
`cap_y=XMIN,YMIN,XMAX,YMAX,H` (`cap_x=`, `cap=` both; kept at any budget), read by `quality
--bbox XMIN,YMIN,XMAX,YMAX`. Read:

- `total_cells`, `budget`, `field_cells`, `budget_binding` (did the budget stop spending);
  `verification` - faces above 70, worst face, skewness, aspect ratio (checkMesh fails above
  1000, blocks named) of the field's state and the result, real counts; spending adds neither.
- `first_width` - per wall patch the height normal to the wall at the spokes against the one
  asked; `note` names strips kept on one ratio and `raised` raises through spokes (Step 6).
- `worst_jump` (counts above 2.5, 5, 20), `splits`, `refused`, `guard`. Judge jumps after the
  polish (NACA 4412: 4 above 5 after `size`, none after). `budget_binding=True` with jumps above
  5: one run with a larger budget (three cylinders: 12 -> 6); one that stays: `failures.md`.

A high average: the `quality` verdict names the blocks (`average_blocks`); it is their shape
(Step 4, `failures.md`); `grading=strip` on a copy compares. A brief fixing the first width ends
with `set_first_width FIRST` (Step 6), also with no polish; `opposite=follow` on an orthogonal H-grid.

Go on when `verification.result` has no face above 70. Faces above 70 in `verification.field`
may go in the polish (three cylinders): polish and `check` first.
Where `check` still finds them, locate the block (`check` maps the set) and use `failures.md`
(also for `size refused`). `method=distance` is a comparison, not a retry (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower checkMesh's face
non-orthogonality, never raising a block's worst face, band spokes within 5 percent of their
length, no size jump past 2.5 made, aspect 1000 not crossed (`reason` says which bound). Lines the brief requires a free polish bends: hold them as in
Step 4, `hold_line=A,B;C,D hold_along=line` (along them only; it costs degrees: `failures.md`).
`hold=V1,V2`/`hold_box=` fix vertices, `keep_lines=true` holds every line; `blocks=`/`bbox=`
limit it; `slide=true` slides boundary vertices (if `check` agrees). The first width moves up to
5 percent with the spokes (`band_tolerance`), 11 to 18 where `size`'s note says the field pass
was dropped. Where the brief asks it exact, `set_first_width FIRST` is the last step (counts
kept, jumps reported); not on a spoke running on as a line to the outlet (`sizing.md`). `size
... keep_counts=true` regrades every strip: first width or average got worse (`sizing.md`).

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, no blocks over one another, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid and exports, untangled,
   worst non-orthogonality and where, faces above 70, max skewness and max aspect ratio and
   where (above 1000 checkMesh fails), sampled at real counts as checkMesh measures them; then
   the worst blocks, edges and warnings; the corner screen (jumps only there) hides distortion.
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
