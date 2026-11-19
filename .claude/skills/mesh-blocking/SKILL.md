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

Tool mechanics (arguments, defaults): `blockdrawer-cli commands`, MCP
`list_commands`. **Kept in step with the engine (`experiments/agentic_topology/`):
a template, acceptance term or option change there updates `engine.md`
and, if the route changes, the table below.**

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
| Backward step, 2:1 | operators by hand | 8 commands, ~3 min (a hand script: 161 lines, 8 min) | 27 blocks; after `size` 21,750 cells, Mesh OK, 0 deg | `examples/backward_step.txt` |
| | engine | 3 runs | refused: `sweep_correspondence: ribs cross or invert` (the sweep takes the step into the lower guide) | trial `backward_step/REPORT.md` |
| Two grooved cylinders in tandem | operators by hand | 14 commands, ~6 min (a hand script: 299 lines, 25 min, max 42.7 deg) | 251 blocks; after shape/size/polish 47,616 cells, Mesh OK, max 20.5 deg | `examples/grooved_tandem.txt` |
| | engine | 5 runs, 11-23 min each | refused: no groove (pocket) template | trial of Oct 26 (`grooved_tandem/`) |
| NACA 0012 at 5 deg, box far field | operators by hand | 12 commands, ~10 min | 59,908 cells at first width 1e-4, Mesh OK, max 48.6 deg | `examples/naca0012.txt` |
| | engine C-grid, r = 15, band 0.1 | 320 s | 185 blocks, resolved, Mesh OK 44.1 deg | `engine.md`, engine README table |
| Single sharp airfoil, C-shaped far field (A-airfoil) | engine C-grid | 263 s | 200 blocks, resolved, Mesh OK 45.1 deg | engine README table |
| Smooth bodies, clusters, simple channels | engine | seconds | e.g. `two_circles` 32 blocks resolved, Mesh OK 63.3 deg; `periodic_hill` resolved 36.2 deg | engine README table |
| 30P30N (slat, main, flap) | engine (`--wake --hull`) | minutes per run | 203 blocks, admissible not resolved; 5,486 cells, Mesh OK, max 77.9 deg, 60 faces above 70; defects left: the cove seam sliver (b21/b31 at v12), the slot strips | `engine.md`; review measurements in README (`smooth`, `size`) |
| Internal flow with a body (cylinder in a channel) | operators by hand | 14 build commands, 4.5 min | 36 blocks, 21,824 cells, Mesh OK, 0 faces above 70, max 35.6 deg | benchmark round 1, one trial |
| Junction (Y-shaped channels) | operators by hand; the branch's blocks from vertices | 23 build commands, 4.5 min | 12 blocks, 41,946 cells, Mesh OK, max 40.0 deg. **Weakness, not a design**: no wall bands, so the wall's fine columns run through the junction; a banded junction is untried | benchmark rounds 1 and 2, one trial each |
| Periodic passage round a body (compressor cascade) | operators by hand: ring, wake strips, `add_stations offset=`, fills | 52 commands, 22 min | 56 blocks, 53,248 cells, Mesh OK, max 60.1 deg (average 28.0), skewness 2.52. Round 1's trailing-edge fan: 64.7, skewness 3.26 | benchmark rounds 1 and 2; the engine refuses a `cyclic` pair from the command line |
| Multi-element airfoil with coves, engine not allowed | operators by hand plus two vertex scripts (cove pocket fill, hull) | 259 commands, 64 min | 538 blocks, 378,785 cells, Mesh OK, max 62.3 deg. **Weakness**: by operators alone (round 2, fills for coves and far field) checkMesh failed: 949 blocks, skewness 4.07 at a far-field corner, 391 faces above 70 in one sheared cove fill | benchmark rounds 1 and 2, one trial each |

A row records what one trial built and measured; a weakness it names is not
to be copied. Rules that follow from the table; the first that matches decides:

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
   can express a region (a curve no operator makes, e.g. an offset hull).

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
   - `add_fill A,B,C,D` - blocks between what is built (a slot, a pocket;
     three or five corners: round a centre), corners in order round it: a
     side is the exterior chain joining its corners, else open (straight or
     along `curve3=CURVE`) through the standalone vertices on it or new
     stations facing the opposite side's; `side2=...` names one.
   - `add_block_from_vertices A,B points=X,Y;X,Y` - one block, corners by ID or coordinates.
   - `add_grid xs=... ys=... outside=BODIES` again for the H-grid round what
     is built: existing vertices on its cell sides become stations. A station
     list runs across the whole grid, so a box whose sides carry different
     stations is refused in one grid ("would fall inside the existing edge"):
     one `add_grid` per side of the box built it (two trials).
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
(`--only`) or one edge's count component (`--component EDGE`). Run time, two
cores at low priority: `size` 2.3 min at 240k cells and 3.5 min at 949
blocks, `smooth` 6.3 min and one `repair_block` 3.2 min at 949 blocks, the
engine's C-grid 8.5 min; set a timeout above that and wait - a second
launch of the same command cost one trial 14 minutes.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices on
the count-free block map before any count exists; band spokes keep their
length and do not turn from the wall normal; no edge ends below `shape_length_ratio`
(0.1) of its input (unlimited, a ring edge went to 0.002 and `size` left a
jump of 269). Read `reason`: the count-free maximum before and after and
"edge lengths A (EDGE) to B (EDGE) of their input" (`min_edge_ratio`). It is
applied only when no worse; "nothing to gain" is normal on rectangles.
Engine square cylinder: real counts 87.7 -> 56.9 deg; sized, checkMesh 52.2.
The step is optional (the 949-block high-lift trial left it out). Then:

- Lines meant to stay straight (a wake line, a column off a corner) bent:
  `hold=V1,V2` on their vertices (one trial: a jump of 7.17 after `size`
  became 3.28), or `keep_lines=true`, which moves a vertex on a straight
  block line only along it - off by default, since it also holds every
  straight band spoke (cost 4-16 deg on three of ten sessions, README).
- `size` leaves a jump above 5 at an edge the step took to the floor
  (`min_edge_ratio` 0.1): rerun the step with a higher
  `shape_length_ratio` (cascade 0.4; three cylinders 0.3: 12 jumps above 5
  -> 0).
- "already fold ... on the count-free block map, in BLOCK (N)": those
  blocks fold at even spacing (the real counts need not: high-lift);
  render and fix them (move a station, split) or leave the step out.

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A` (inputs in `sizing.md`; where the
brief gives no `wall_aspect`, 25, as all eleven round-2 trials) sets every
count and grading from the wall distance, matches widths across interfaces
and spends a cell budget (default 1.5 times the field's own cells, `budget=`)
on continuity. Read:

- `total_cells`, `budget`, `field_cells`, `budget_binding` - what you pay
  for, and whether the budget stopped the spending.
- `verification` - faces above 70, worst face, skewness, aspect ratio
  (checkMesh fails above 1000, block named) for the field's state and the
  result. Spending adds no face above 70 and does not watch the aspect.
- `first_width` - per wall patch the cell height normal to the wall at the
  spokes, in percent of the one asked, extremes named (`quality` too).
- `worst_jump` (with the counts above 2.5, 5, 20), `splits`, `refused`,
  `guard`, `note`. Jumps are judged after the polish, not here: it moves
  vertices (NACA 4412: 4 above 5 after `size`, none after the polish; three
  cylinders: one 4.73 -> 5.20, held with `hold=`). `budget_binding=True`
  with jumps above 5 is worth one run with a larger budget (three
  cylinders: 12 -> 6 above 5); one that stays is the shape's (Step 4) or
  the topology's (`sizing.md`).

Go on when `verification.result` has no face above 70. Faces above 70 in
`verification.field` may go in the polish (Step 6; they did on three
cylinders): polish and `check` first. Where `check` still finds them, locate
the block (`check` maps the set) and use `failures.md`. `method=distance` is
a comparison, not a retry: better on one session of three (`sizing.md`). A
`size refused` error means the counts would take checkMesh's skewness past 4
in a tapered block: split that block or keep the counts.

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face
non-orthogonality checkMesh measures, never raising a block's worst face,
wall-band spokes within 5 percent of their length, edges within 50 percent, no
size jump past 2.5 created; applied only when the sampled check at 256 cells a
side is no worse. `hold=V1,V2` keeps vertices you placed on purpose;
`blocks=`/`bbox=` limit it. Read `reason`. `slide=true` also slides boundary
vertices along their smooth boundary; it changed little (README, `smooth
slide=true`): keep it when `check` agrees, not in Step 4.

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid
   and exports, untangled, worst non-orthogonality and where, faces above 70,
   max skewness, max aspect ratio (above 1000 checkMesh fails), sampled at
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

## Where things are

Engine: `experiments/agentic_topology/README.md` and `engine.md`. Earlier
hand-built cases, when present in the repository root `cases/`:
`naca0012_cgrid`, `a_airfoil`, `30p30n` (written before the operators).
