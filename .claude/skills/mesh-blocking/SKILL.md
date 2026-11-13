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

The brief's far field, patch names, first cell and band height are inputs,
not choices. Where it leaves something open, choose, and say so in the
deliverable.

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

Rules that follow from the table:

- **A domain the operators can state** (stations, polar patches, bands along
  walls, rings round curves, boxes) - build it by hand with the operators. It
  is minutes, and every result above came out Mesh OK.
- **A single body, a cluster of smooth bodies, a simple internal domain, a
  sharp airfoil in a C-shaped far field** - try the engine first
  (`engine.md`), in the background (minutes per run); fall back to the
  operators on a refusal.
- **Multi-element, cove, groove** - the engine builds 30P30N with defects
  left; anything with pockets the operators can state goes by hand.
- A session written by a layout script that computes vertices is the last
  resort, when no operator can express a region (two-way conform across a
  box whose sides carry different stations, a loop fill - neither exists).

## Step 3 - Build

**By hand** (`examples/README.md` has three complete batches):

1. `blockdrawer-cli new SESSION --box XMIN YMIN XMAX YMAX` (MCP
   `new_session` with `box`) - every session has one block; put it where a grid cell will reuse it, or remove it at
   the end with `remove_edge`.
2. `import_curve FILE name=NAME` for every body and chain.
3. The layout, from the wall outwards:
   - `add_grid xs=... ys=... skip=...` - Cartesian stations, cells in skip
     boxes, inside `outside=` curves or inside existing blocks left out.
   - `add_grid center=X,Y rs=... thetas=... period=DEG outside=BODY` - a
     polar patch (arcs), for O-rings and grooves.
   - `add_ring CURVE HEIGHT count=N` - a wall band round a closed curve, a
     fan at each sharp corner (trailing edge).
   - `add_band bbox=... height=H rows=N` - more rows along the unassigned
     exterior edges in a box; `add_band bbox=... to_box=...` - carry them to
     a rectangle (put spokes at its corners).
   - `add_grid xs=... ys=... outside=BODIES` again for the H-grid round what
     is built: existing vertices on its cell sides become stations.
4. `set_boundary_by NAME type=wall curves=BODY`, `set_boundary_by NAME
   type=patch bbox=...` per far-field side; `between=A,B` for a chain.
5. `set_export_settings z_min_patch_type=empty z_max_patch_type=empty`.

Run the batch with `blockdrawer-cli apply SESSION --in-place --quiet -f
build.txt` (MCP: `edit_session commands_file=... in_place=true`). It is
atomic. Go on when the digest lists the blocks and patches you meant and
`describe SESSION --unassigned --section summary` (MCP `describe_session`
with `unassigned=true`) shows no unassigned exterior edge. A refusal names
the block or edge: change a station, not the tool. Look at every corner and
gap: `render SESSION -o x.png --bounds ...` (MCP `render_session`).

**By the engine**: `engine.md` - the command, the options the brief maps
to, and the report terms. Go on when `admissible` is true; `resolved` is
better. The engine writes `empty` z patches and its own patch names (merge
with `set_edge_boundary`, rename with `rename_boundary`).

Reading cost: `describe` and `quality_report` answer compactly by default;
on a session of hundreds of blocks ask for a region (`--bbox`, MCP `bbox`),
one block (`--only`), or one edge's count component (`--component EDGE`)
instead of the whole description.

## Step 4 - Shape

`smooth mode=shape allow_stale_fold=true` moves the free interior vertices
on the count-free block map (all faces real) before any count exists;
wall-band spokes keep their length and do not turn from the wall normal.
Read `reason`: the count-free maximum non-orthogonality before and after.
It is applied only when that verdict is no worse; "nothing to gain" is
normal on an H-grid of rectangles. (Engine square cylinder: checkMesh 87.7
-> 54.6 deg, 1344 -> 0 faces above 70; README, `smooth`.)

## Step 5 - Size

`size FIRST CORE growth=G wall_aspect=A` (inputs in `sizing.md`) sets every
count and grading from the wall distance, matches widths across interfaces
and spends a cell budget (default 1.5 times the field's own cells, `budget=`)
on continuity. Read:

- `total_cells`, `budget`, `field_cells` - what you are paying for.
- `verification.field` and `verification.result` - faces above 70 and the
  worst face for the field's state and the result. Spending never adds to
  them; the field's own are not removed.
- `worst_interfaces`, `interface_jumps` (above 2.5, 5, 20), `splits`,
  `refused`, `guard`, `note`.

Go on when `verification.result` has no face above 70. **If
`verification.field` has faces above 70**, size again from the same shaped
session with `method=distance` (item 3's wall-distance sizing), polish both,
and keep the one `check` finds fewer such faces in, then the better jump
histogram. This rests on one session (fine 30P30N: distance 0 faces at
68.8 deg, directional 6 at 85.8; README, `size`). A `size refused` error
means the counts would take checkMesh's skewness past 4 in a tapered block:
split that block or keep the counts. Jumps that spending leaves are mostly
topological (`sizing.md`).

## Step 6 - Polish

`smooth` - the real-count polish: interior vertices move to lower the face
non-orthogonality checkMesh measures, never raising a block's worst face,
wall-band spokes within 5 percent of their length, edges within 50 percent,
no size jump past 2.5 created; applied only when the sampled check at 256
cells a side is no worse. `hold=V1,V2` keeps vertices you placed on purpose;
`blocks=`/`bbox=` limit it. Read `reason`.

## Step 7 - Judge

0. `validate SESSION` (MCP `validate_session`): loads, valid, exports.
1. `quality SESSION` (MCP `quality_report`): the **verdict** first - valid
   and exports, untangled, worst non-orthogonality and where, faces above 70,
   max skewness. These come from every block interior sampled at its real
   counts (coarsened above 256 a side) and measured as checkMesh measures
   them; then the worst blocks and edges and the 20 worst warnings. The
   corner screen beside it (corner angles, aspect, growth, interface jumps)
   is measured at block corners and hides interior distortion; size jumps
   and aspect ratios are only there.
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

- Commands: `blockdrawer-cli commands`, MCP `list_commands`. Batches are
  atomic.
- Engine: `experiments/agentic_topology/README.md` (algorithm, measured
  tables, open limits) and `engine.md` here.
- Earlier hand-built cases, when present in the repository root `cases/`:
  `naca0012_cgrid`, `a_airfoil`, `30p30n` (layout modules and batches
  written before the operators existed).
