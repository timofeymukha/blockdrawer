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
| | engine C-grid, r = 15, band 0.1 | 320 s | 185 blocks, resolved, Mesh OK 44.1 deg | `engine.md`, engine README table |
| Single sharp airfoil, C-shaped far field (A-airfoil) | engine C-grid | 263 s | 200 blocks, resolved, Mesh OK 45.1 deg | engine README table |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| Three staggered cylinders (a cluster) | engine hull, or by hand: a box per body (`to_box`), its sides on lines it shares with the others, a ring station (`normals=`, the angle to the point) wherever another box's line crosses; one H-grid; 12-16 `move_vertex` snaps | hand: 8-12 min | engine: Mesh OK, 67.9 deg (average 21.7); two hand trials: 42.6 and 37.0 deg | rounds 3 to 5 |
| Cylinder with a splitter plate (concave body corners) | ring round the body, mitred at the junctions; each junction wall's front carried along the other wall by `add_rows conform`; wake rows, far field by rows | 31 min | 461 blocks, 91,114 cells, Mesh OK, max 52.6 deg (average 15.1) | round 4 |
| Elbow with a guide vane | ring on the vane, polar `add_grid` with `outside=`, four fills; a rounded body end: a box from the cap's 45-degree stations | 11 min | 92 blocks, 35,575 cells, Mesh OK, max 33.1 deg; another layout 40.3 | rounds 4 and 5 |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70 | `engine.md` |
| Internal flow with a body (cylinder in a channel) | operators by hand | 14 build commands, 4.5 min | 36 blocks, 21,824 cells, Mesh OK, 0 faces above 70, max 35.6 deg | round 1 |
| Duct, varying section, cross lines near the wall normals (nozzle) | operators: an open ring per wall with stations, one fill | 16 commands, 3-3.5 min | Mesh OK, max 11.5 to 12.0 deg after the polish (25 after `size`). The engine's sweep: `resolved`, but one core block with near-vertical lines - `resolved` does not test the brief | round 3 |
| Junction (Y-shaped channels) | open rings; the open corner's chain first (`fan=false`), the station across the branch `points=CORNER_ID`; the corner a station on a side of the junction's fill, not its corner (34.75 deg for 24.85); split the corner's spoke (`failures.md`; without: a wall 51% fine, 21k cells more) | 5.6-11.2 min | round 5, three trials: max 24.85 / 24.86 / 25.83 deg, first widths within 5.5%; 63,360 / 80,062 / 52,599 cells by the budget chosen | rounds 3 to 5 |
| Periodic passage round a body (compressor cascade) | operators by hand: ring, wake strips, `add_stations offset=`, fills | 52 commands, 22 min | 56 blocks, 53,248 cells, Mesh OK, max 60.1 deg (average 28.0), skewness 2.52. Round 1's trailing-edge fan: 64.7, skewness 3.26 | rounds 1 and 2 |
| Multi-element airfoil with coves, engine not allowed | operators only: three rings, cove and slot fills, wake rows, far field by rows | 59 min | round 5: 1822 blocks, 219,594 cells, Mesh OK, max 68.3 deg, no jump above 5 | rounds 1, 2, 5 |
| Two airfoils in tandem | two rings `fan=false`, wake rows (`spread_about`), far field by rows, two fills | 16 min | 1058 blocks, 118,320 cells, Mesh OK, max 40.2 deg | round 5 |
| Eccentric annulus | a ring on each wall (`side=in` outside), stations paired on lines normal to both walls, two half-annulus fills | 6.6 min | 96 blocks, 24,297 cells, Mesh OK, max 11.7 deg | round 5 |

A row records what benchmark trials built and measured; a weakness it names is
not to be copied. Rules that follow from the table; the first that matches decides:

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
3. **Multi-element, cove, groove** - the engine builds 30P30N with defects left; operators alone
   took an hour (round 5); a groove or pocket: by hand.
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
   - `add_grid xs=... ys=... skip=X0,Y0,X1,Y1,...` - Cartesian stations, cells in skip
     boxes (four numbers a box, one list), inside `outside=` curves or existing blocks left out;
     `add_grid center=X,Y rs=... thetas=... period=DEG` - a polar patch (arcs).
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a fan
     above 60 degrees of turning (one block to 90); `tips=true` adds a station at
     each rounded nose, `normals=140,180,220` where the outward normal has those
     angles (`near=X,Y` where it has several); `fan=false` leaves
     a sharp trailing edge open; `counts=14,14` edges per section between stations;
     `facing=V1;V2` stations where another wall's normals at those points meet this one
     (hand pairings did better: README). The note pairs front and wall IDs.
   - Internal flow, no block yet: `add_ring WALL1,WALL2 H side=left` - curves
     joined end to start, the band on the fluid's side; stations at ends, joints,
     corners, `xs=`, `points=X,Y;V` (nearest curve point: `xs=` crossing twice, a
     vertex opposite) and `count=`; end spokes run along an inlet curve ending
     there, else normal (the note says where; `start=0,1 end=0,1` set them).
   - A long band beside a mitred or tilted spoke, a long edge into a wall face: a station
     about a band height from it (a channel width for a long straight band), or the tilt
     shears the whole block (bump 41.3 -> 33.0 deg, average 15.9 -> 11.3; Y-junction
     average 10.5 -> 7.8), the edge is one series (orifice: 104,767 cells, 48,508 with).
   - A concave domain corner: no block line from it to a convex wall feature (a
     divider tip, a body) - they would share one graded strip (`failures.md`).
     A concave body corner (a plate on a cylinder): the ring mitres it; carry
     each wall's front along the other wall by `add_rows ... conform` (splitter row).
   - `add_rows edges=A-B,B-C direction=1,0 to_x=X stations=...` (`rows=N
     growth=G`, `to_y=`, `distance=`) - a chain carried along a direction as
     rows: an open trailing edge's spokes to the outlet (`spread=10` widens
     them, else `size` carries the wall spacing out; `examples/naca0012_cgrid.txt`);
     `to_y=V` takes V's y. A blunt base with both corners open: `failures.md`.
     `between=A,B` takes the shortest unassigned chain: assign the wall first.
     Never from a fan's front (high-lift: 3593 cells above aspect 1000); the note
     names the thinnest column. `far_ratio=2`: mixed on five builds; a run of thin
     columns stays thin (`failures.md`).
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
     vertices - never type their coordinates (a front at a line/arc joint lies 0.0013
     off it: the refusal names the `move_vertex`).
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

On hundreds of blocks read a region (`--bbox`), a block (`--only`) or a component (`--component
EDGE`). Two cores: `size` 2-12 min, `smooth` 6 min near 1000 blocks; set a timeout above that.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on the count-free
block map before any count exists; band spokes keep their length and do not turn from the wall
normal; no edge ends below `shape_length_ratio` (0.1) of its input. Read `reason`: the count-free
maximum before and after, `min_edge_ratio` and `max_pair_change`; applied only when no worse.
Optional; on 19 hand builds, sized and polished, it lowered checkMesh's maximum by over a degree
against no step on 10 (y-junction 39.1 -> 32.0), raised it on 3 (triangle +12.3) (README). Then:

- What the brief fixes, held here and in the polish (Step 6): it pinches a wake band to a third
  (airfoil tunnel: `hold_box=X0,Y0,X1,Y1` on the band, 15k cells more), turns fans at a blunt base
  (triangle: lines 17 deg off; held, a jump of 5.5), makes a box an octagon (cylinder in a channel);
  `hold_along=x` (or `y`, `line`) lets the held vertices slide along their line.
- `min_edge_ratio` 0.1 and a large `max_pair_change` (not a threshold: splitter, 14.0, 31 jumps
  above 5 after `size`, 7 with the limit): `shape_pair_ratio=2.5` was right on one
  round-4 build (triangle: 28.0 -> 25.9 deg, jumps above 5 3 -> 0) and wrong on another (three
  cylinders: 42.6 deg for 33.3): size both, keep what `check` prefers. An engine session needs
  the step (NACA 4412: 45.1 deg with it, 69.6 without).
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
(Step 4, `failures.md`); `grading=strip` on a copy compares.

Go on when `verification.result` has no face above 70. Faces above 70 in `verification.field`
may go in the polish (three cylinders): polish and `check` first.
Where `check` still finds them, locate the block (`check` maps the set) and use `failures.md`
(also for `size refused`). `method=distance` is a comparison, not a retry (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face non-orthogonality
checkMesh measures, never raising a block's worst face, wall-band spokes within 5 percent of
their length, no size jump past 2.5 created; applied only when the
sampled check is no worse and checkMesh's aspect limit 1000 is not crossed
(`reason` says which bound). Lines the brief requires a free polish bends: hold them as in
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
   the worst blocks, edges and warnings. The corner screen (corner angles, growth, interface
   jumps - only there) hides interior distortion.
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
