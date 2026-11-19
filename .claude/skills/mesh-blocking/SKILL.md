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
| `engine.md` | you run the topology engine (`research_cli.py`): what it builds per configuration, its options, its report |
| `sizing.md` | you choose `size`'s inputs, read its `verification`, or size by hand |
| `failures.md` | `quality` or `check` names a defect: signature -> the tool that acts on it |
| `examples/README.md` | before your own case: three batches to run end to end |

Tool mechanics (arguments, defaults) are in `blockdrawer-cli commands` and
the MCP `list_commands` tool. **This skill is kept in step with the topology
engine in `experiments/agentic_topology/`: when a template, an acceptance
term or an option changes there, update `engine.md` and, if the route
changes, the table below.**

## Step 1 - Collect the inputs

- **Geometry**: one closed point list per body (`x y` per line, the loop may
  be left implied), far-field chains if given. Keep the supplied points;
  never resample from a picture.
- **Physics**: Reynolds number and target first-point $y^+$ (first width in
  `sizing.md`); incidence goes in the solver's velocity, the chord stays
  aligned unless the brief gives rotated geometry. Far field: what the brief
  says; at least 30 chords for external aerodynamics, a few body sizes for a
  topology test.
- **Patches**: every chain's name and role (`wall`, `patch` for
  inlet/outlet/far field, `symmetry`, `cyclic` pairs with identical point
  distributions related by one translation).
- **Solver**: OpenFOAM finite volume, or spectral elements of order $p$ (then
  counts are small and block curvature must be exact; `sizing.md`).
- **Span**: pseudo-2D - `set_export_settings z_min_patch_type=empty
  z_max_patch_type=empty` (with `patch` z faces checkMesh's aspect ratio
  includes the span) - or a periodic slab with reciprocal `cyclic` z patches.

The brief's far field, patch names, first cell and band height are inputs, not
choices. Where it leaves something open, choose, and say so in the deliverable.

## Step 2 - Choose the route

Choose by what has been measured, not by what a route intends:

| Case | Route | What it took | Result (checkMesh) | Source |
|---|---|---|---|---|
| Backward step, 2:1 | operators by hand | 8 commands, ~3 min | 27 blocks; after `size` 21,750 cells, Mesh OK, 0 deg | `examples/backward_step.txt` |
| | engine | 3 runs | refused: `sweep_correspondence: ribs cross or invert` (the sweep takes the step into the lower guide) | trial `backward_step/REPORT.md` |
| | hand script (before the operators) | 161-line script, 8 min | 36 blocks, 23,598 cells, Mesh OK, 0 deg | same report |
| Two grooved cylinders in tandem | operators by hand | 14 commands, ~6 min | 251 blocks; after shape/size/polish 47,616 cells, Mesh OK, max 20.5 deg | `examples/grooved_tandem.txt` |
| | engine | 5 runs, 11-23 min each | refused: no groove (pocket) template | trial of Oct 26 (`grooved_tandem/`) |
| | hand script | 299 lines, 25 min | 251 blocks, 62,712 cells, Mesh OK, max 42.7 deg | `grooved_tandem.session.json` |
| NACA 0012 at 5 deg, box far field | operators by hand | 12 commands, ~10 min | 59,908 cells at first width 1e-4, Mesh OK, max 48.6 deg | `examples/naca0012.txt` |
| | engine C-grid, r = 15, band 0.1 | 320 s | 185 blocks, resolved, Mesh OK 44.1 deg | `engine.md`, engine README table |
| Single sharp airfoil, C-shaped far field (A-airfoil) | engine C-grid | 263 s | 200 blocks, resolved, Mesh OK 45.1 deg | engine README table |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70; defects left: the cove seam sliver (b21/b31 at v12), the slot strips | `engine.md`; review measurements in README (`smooth`, `size`) |
| Internal flow with a body (cylinder in a channel) | operators by hand | 14 build commands, 4.5 min | 36 blocks, 21,824 cells, Mesh OK, 0 faces above 70, max 35.6 deg | benchmark round 1, one trial |
| Junction (Y-shaped channels) | operators by hand; the branch's blocks from vertices | 23 build commands, 4.5 min | 12 blocks, 41,946 cells, Mesh OK, max 40.0 deg (no wall bands: fine columns run through the junction) | benchmark round 1, one trial |
| Periodic passage round a body (compressor cascade) | operators by hand; stations typed and projected, blocks from vertices | 104 commands, 17 min | 44 blocks, 62,788 cells, Mesh OK, max 64.7 deg (average 28.8), skewness 3.26 | benchmark round 1, one trial; the engine refuses a `cyclic` pair from the command line |
| Multi-element airfoil with coves, engine not allowed | operators by hand plus two vertex scripts (cove pocket fill, hull) | 259 commands, 64 min | 538 blocks, 378,785 cells, Mesh OK, 0 faces above 70, max 62.3 deg, skewness 3.59 | benchmark round 1, one trial |

Rules that follow from the table; the first that matches decides:

1. **A periodic (`cyclic`) passage with a body in it, internal flow with a
   body, a junction** - build it by hand with the operators. The engine refuses
   a `cyclic` pair from the command line and a reflex corner on a guide wall;
   every such case above came out Mesh OK by hand.
2. **A single body, a cluster of smooth bodies, a simple internal domain with
   no body and no periodic pair, a sharp airfoil in a C-shaped far field** -
   try the engine first (`engine.md`), in the background (a C-grid run took 8
   minutes on two cores); fall back to the operators on a refusal. A blunt base
   wider than about two percent of the perimeter has no template there
   (`engine.md`).
3. **Multi-element, cove, groove** - the engine builds 30P30N with defects
   left; by hand it took an hour and two vertex scripts; a groove or pocket the
   operators can state goes by hand.
4. **Anything else the operators can state** (stations on curves, polar
   patches, bands, rings, boxes, fills between fronts) - by hand: minutes.
5. A layout script that computes vertices is the last resort, when no operator
   can express a region (two-way conform across a box whose sides carry
   different stations; a curve no operator makes, e.g. an offset hull).

## Step 3 - Build

**By hand** (`examples/README.md` has three complete batches):

1. `blockdrawer-cli new SESSION --box XMIN YMIN XMAX YMAX` (MCP `new_session`
   with `box`): one block; put it where a grid cell reuses it, or `remove_edge` it.
2. `import_curve FILE name=NAME` for every body and chain.
3. The layout, from the wall outwards:
   - `add_grid xs=... ys=... skip=...` - Cartesian stations, cells in skip
     boxes, inside `outside=` curves or inside existing blocks left out.
   - `add_grid center=X,Y rs=... thetas=... period=DEG outside=BODY` - a
     polar patch (arcs), for O-rings and grooves.
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a
     fan above 60 degrees of turning (one block to 90); `tips=true` adds a
     station at each rounded nose, `normals=140,180,220` where the outward
     normal has those angles, `corner=DEG` sets which vertices are corners.
   - `add_band bbox=... height=H rows=N` - more rows along the unassigned
     exterior edges in a box; `add_band bbox=... to_box=...` - carry them to
     a rectangle (spokes at its corners: ring stations by `normals=` aimed
     at them).
   - `add_stations CURVE xs=... id=x` (`count=`, `between=`, `fractions=`):
     standalone stations; `offset=DX,DY` copies them onto a periodic partner.
   - `add_fill A,B,C,D` - blocks between what is built (a slot, a pocket,
     half an annulus; three or five corners: blocks round a centre), corners
     in order round it: a side is the exterior chain joining its corners,
     else open (straight or along `curve3=CURVE`) through the standalone
     vertices on it or new stations facing the opposite side's; `side2=...`
     names one. A count mismatch or a fold is refused, saying what to change.
   - `add_block_from_vertices A,B points=X,Y;X,Y` - one block, corners by ID or coordinates.
   - `add_grid xs=... ys=... outside=BODIES` again for the H-grid round what
     is built: existing vertices on its cell sides become stations.
4. `set_boundary_by NAME type=wall curves=BODY`, `set_boundary_by NAME
   type=patch bbox=...` per far-field side; `between=A,B` for a chain.
5. `set_export_settings z_min_patch_type=empty z_max_patch_type=empty`.

Run the batch with `blockdrawer-cli apply SESSION --in-place --quiet -f build.txt`
(MCP: `edit_session commands_file=... in_place=true`). It is atomic. Go on
when the digest lists the blocks and patches you meant and `describe SESSION
--unassigned --section summary` (MCP `describe_session` with
`unassigned=true`) shows no unassigned exterior edge. A refusal names the
block or edge: change a station, not the tool. Look at every corner and gap:
`render SESSION -o x.png --bounds ...` (MCP `render_session`).

**By the engine**: `engine.md` - the command, the options the brief maps
to, and the report terms. Go on when `admissible` is true; `resolved` is
better. The engine writes `empty` z patches and its own patch names (merge
with `set_edge_boundary`, rename with `rename_boundary`).

Reading cost: `describe` and `quality_report` answer compactly by default; on
hundreds of blocks ask for a region (`--bbox`, MCP `bbox`), one block
(`--only`) or one edge's count component (`--component EDGE`).

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on
the count-free block map (all faces real) before any count exists; wall-band
spokes keep their length and do not turn from the wall normal, and no edge ends
shorter than a tenth of its input length (`shape_length_ratio`; unlimited, a
ring edge collapsed to 0.002 of its length and `size` then left a jump of 269).
Read `reason`: the count-free maximum non-orthogonality before and after, and
"edge lengths A (EDGE) to B (EDGE) of their input" - an A at the floor is an
edge the optimum wanted shorter still: render it before sizing. It is applied
only when that verdict is no worse; "nothing to gain" is normal on an H-grid of
rectangles. `hold=V1,V2` keeps vertices you placed on purpose (a wake line, a
fan's middle spoke). `keep_lines=true` moves an interior vertex on a straight
block line (two opposite straight edges, collinear) only along it, and one on
two lines not at all. It is off by default: it also holds every straight spoke
of a ring or band, and once sized it cost 4-16 deg on three of ten sessions
(README, `smooth`). Turn it on where straight lines are the design and few
spokes are straight, else hold the line's vertices. (Engine square cylinder:
real counts 87.7 -> 56.9 deg, 1344 -> 0 faces above 70; sized and polished,
checkMesh 52.2.)

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A` (inputs in `sizing.md`) sets every
count and grading from the wall distance, matches widths across interfaces
and spends a cell budget (default 1.5 times the field's own cells, `budget=`)
on continuity. Read:

- `total_cells`, `budget`, `field_cells`, `budget_binding` - what you pay
  for, and whether a larger budget would buy more continuity.
- `verification` - faces above 70, worst face, skewness, aspect ratio
  (checkMesh fails above 1000, block named) for the field's state and the
  result. Spending adds no face above 70 and does not watch the aspect.
- `first_width` - per wall patch the cell height normal to the wall at the
  spokes, in percent of the one asked, extremes named (`quality` too).
- `worst_jump` (with the counts above 2.5, 5, 20; `quality`'s measure,
  which the polish then changes), `splits`, `refused`, `guard`, `note`.

Go on when `verification.result` has no face above 70. Faces above 70 in
`verification.field` may go in the polish (Step 6; they did on three
cylinders): polish and `check` first. Where `check` still finds them, locate
the block (`check` maps the set) and use `failures.md`. `method=distance` is
not a general retry: it helped on one session (fine 30P30N: 0 faces above 70 at
68.8 deg against 6 at 85.8) and was worse on both others measured (NACA 4412
C-grid: 2,088 faces above 70, max 81.5; three cylinders: skewness 3.85 and a
jump of 19.2 where the default's polish had already cleared the faces). Try it
only as a comparison kept when `check` says so. A `size refused` error means
the counts would take checkMesh's skewness past 4 in a tapered block: split
that block or keep the counts. Jumps that spending leaves are mostly
topological (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face
non-orthogonality checkMesh measures, never raising a block's worst face,
wall-band spokes within 5 percent of their length, edges within 50 percent, no
size jump past 2.5 created; applied only when the sampled check at 256 cells a
side is no worse. `hold=V1,V2` keeps vertices you placed on purpose;
`blocks=`/`bbox=` limit it. Read `reason`. `slide=true` also slides boundary
vertices along their smooth boundary (corners, patch junctions and cyclic
patches stay; edges re-cut from the input boundary, the deviation reported);
measured it changed little (README, `smooth slide=true`), so try it in the
polish and keep it when `check` agrees - not in Step 4, where it made sized
meshes worse.

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid
   and exports, untangled, worst non-orthogonality and where, faces above 70,
   max skewness, max aspect ratio (above 1000 checkMesh fails). These come
   from every block interior sampled at its real counts (coarsened above
   256 a side) and measured as checkMesh measures them; then the worst
   blocks and edges and the 20 worst warnings. The corner screen beside it
   (corner angles, aspect, growth, interface jumps) is measured at block
   corners and hides interior distortion; size jumps are only there.
2. `check SESSION` (MCP `check_mesh`): `blockMesh` then `checkMesh` must
   print `Mesh OK`. It passes with any number of faces above 70 - read the
   severe count and the warnings too. `sets` maps checkMesh's bad faces to
   blocks, worst first, with bounds to render. This is the authority.

Go on when `Mesh OK`, no face above 70 (or the brief's tolerance), skewness
under 4. Otherwise open `failures.md` with the blocks `check` named; after
a fix go back to Step 5 (counts) or Step 6 (shape).

## Step 8 - Deliver

A case directory with the session JSON, the exported `blockMeshDict`
(`export SESSION --case DIR`, MCP `export_block_mesh_dict`), the command batch that regenerates the
session, the retained checkMesh log, and a README: the topology outward from
the wall, patch names and types, first-cell and far-field sizes with their
derivation, the measured numbers, and what is still wrong. Report in the
acceptance vocabulary - valid, untangled, `Mesh OK` with its severe-face
count and maximum, and for engine output `admissible`/`resolved` - never
"good mesh".

## Where things are

- Engine: `experiments/agentic_topology/README.md` (algorithm, measured
  tables, open limits) and `engine.md` here.
- Earlier hand-built cases, when present in the repository root `cases/`:
  `naca0012_cgrid`, `a_airfoil`, `30p30n` (layout modules and batches
  written before the operators existed).
