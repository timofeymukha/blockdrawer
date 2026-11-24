# Benchmark round 2 (Nov 18-19 2026)

Code: `review-followups` at 9ee981f (16 commits after round 1: shape floor,
periodic export and sizing, report fixes, skill route rows, band and ring
fixes, `grading=field`, `add_fill`, `add_stations`). Protocol: `TRIAL.md` as
revised (deliverables in `CASE/trial2/`, more numbers asked). One fresh Opus
agent at high effort per case, one at a time, two cores. Three new cases
(`nozzle`, `airfoil_tunnel`, `bluff_ground`) have no row in the skill's
route table. checkMesh numbers are from my own reruns (`bin/verify CASE
trial2`).

## Results

| case | route | blocks | cells (field) | max non-orth (avg) | skew | aspect | worst jump (>2.5) | first width | own script | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nozzle (new) | operators: stations, bands, one fill | 44 | 14,620 (12,580) | 11.48 (2.79) | 0.45 | 27 | 1.79 (0) | -10%..-0% | no | 3.5 min | 120k |
| airfoil_tunnel (new) | operators: ring, fan removed, wake blocks, two fills, four conforming grids | 208 | 113,164 (95,556) | 30.95 (5.52) | 0.53 | 80 | 2.50 (0) | airfoil -17%..+0% on normal spokes; walls -10%..-0% | no | 10.5 min | 144k |
| bluff_ground (new) | operators: ring with normal stations, band to box, three-part H-grid | 131 | 237,166 (158,964) | 44.18 (4.72) | 1.08 | 452 | 3.52 (6) | body -10%..-1%; ground -10%..-0% | no | 14 min | 137k |
| cylinder_channel | operators: polar band, band to box, H-grid | 28 | 20,513 (19,792) | 41.57 (3.10) | 0.50 | 28 | 1.88 (0) | -7%..+0% | no | 3 min | 105k |
| u_bend | operators: polar tip band, outer-wall band, blocks by coordinates | 26 | 22,700 (22,100) | 42.61 (7.03) | 0.51 | 28 | 1.50 (0) | -10%..-0% | no | 5.5 min | 125k |
| y_junction | operators: H-grid, branch from vertices (same topology as round 1) | 12 | 40,828 (40,480) | 39.97 (11.74) | 0.88 | 34 | 1.33 (0) | -14%..+12% along edges; at most the asked width normal to walls | coordinates by one-liner | 5.5 min | 130k |
| cascade | operators: ring with fan removed, wake strips, stations with offset, fills | 56 | 53,248 (42,917) | 60.12 (28.01) | 2.52 | 27 | 2.46 (0) | -15%..+4% on spokes | no (coordinates by hand) | 22 min | 237k |
| blunt_plate | operators: ring with nose station, band to box, four-part H-grid; `grading=field` plus two hand gradings | 134 | 91,581 (66,074) | 31.45 (7.14) | 0.82 | 479 | 2.32 (0) | -20%..-2% | no | 8 min | 130k |
| naca4412 | engine C-grid (1 run, 8.5 min) + shape / `size grading=field` / polish, no hand grading | 250 | 205,506 (139,686) | 45.12 (7.32) | 0.87 | 57 | 3.90 (8) | -5%..+5% on normal spokes | no | 16 min | 131k |
| three_cylinders | engine `--hull` (2 runs, first crashed) + shape at floor 0.3, two `combine_blocks`, size, polish | 186 | 52,553 (37,175) | 64.66 (17.44) | 3.35 | 24 | 4.18 (25) | a,c -14%..+3%; b -24%..-7% after polish | no | 27 min | 177k |
| high_lift | operators only (engine not allowed): three rings, 11 fills, three wake guide curves typed by eye; Step 4 refused, left out | 949 | 293,053 (277,225) | **71.50 (25.76), 396 faces above 70** | **4.07: checkMesh fails** | 156 | **16.97 (135; 57 above 5)** | flap -85%..-63%; main -15%..+247%; slat -25%..+378% (all spokes, my measure) | no | 74 min | 301k |

Ten of eleven: `Mesh OK`, no face above 70 degrees, skewness under 4, no jump
above 5. `high_lift` fails three targets (skewness 4.065 on 10 faces at a
far-field corner, 396 faces above 70 of which 391 in one cove block, 57
jumps above 5). No trial wrote a vertex script. Total: 189 minutes, 1.74M
tokens.

## Round 1 against round 2, the eight common cases

One trial per case and round, by different agents: a difference in one row
is a tool effect only where the report ties it to a tool (last column).

| case | max non-orth | worst jump (>2.5) | cells | time (min) | tokens | what changed, and why |
|---|---|---|---|---|---|---|
| cylinder_channel | 35.62 -> 41.57 | 1.84 -> 1.88 | 21,824 -> 20,513 | 4.5 -> 3 | 121k -> 105k | agent's choice: straight wake rows held, eight spokes |
| u_bend | 20.88 -> 42.61 | 1.97 -> 1.50 | 24,010 -> 22,700 | 4.5 -> 5.5 | 121k -> 125k | agent's choice: a topology with 45-degree corners; no tool involved |
| y_junction | 39.96 -> 39.97 | 1.54 -> 1.33 | 41,946 -> 40,828 | 4.5 -> 5.5 | 126k -> 130k | same band-less H-grid (the route row was read as the design) |
| blunt_plate | 26.96 -> 31.45 | 3.07 (4) -> 2.32 (0) | 87,033 -> 91,581 | 13 -> 8 | 154k -> 130k | tool: `tips=true` replaced the helper script, `grading=field` two of four hand gradings; default sizing failed checkMesh on aspect ratio |
| cascade | 64.66 -> 60.12 | 2.87 (3) -> 2.46 (0) | 62,788 -> 53,248 | 17 -> 22 | 209k -> 237k | tool: periodic pair right at once, 52 build commands for 104, wake band for the fan; skewness 3.26 -> 2.52 |
| naca4412 | 67.01 -> 45.12 | 4.43 (27) -> 3.90 (8) | 208,278 -> 205,506 | 20.5 -> 16 | 137k -> 131k | tool: `grading=field`, no hand grading |
| three_cylinders | 68.05 -> 64.66 | 3.62 (7) -> 4.18 (25) | 46,464 -> 52,553 | 22 -> 27 | 173k -> 177k | Step 4 run this time (floor raised to 0.3 by the agent); mixed |
| high_lift | 62.33, Mesh OK -> 71.50, **fails** | 4.06 (33) -> 16.97 (135) | 378,785 -> 293,053 | 64 -> 74 | 373k -> 301k | 72 commands and no script for 259 commands of which 192 scripted; the far field by fills is worse than round 1's scripted hull |
| sum | | | | 150 -> 161 | 1.41M -> 1.34M | |

## Gaps reported, by case

### nozzle (new case)

Corner angles 77.4 .. 102.6.

1. No operator puts a wall band on the fluid side of an open wall chain
   (internal flow): throwaway blocks outside each wall by `add_fill`,
   `add_band` from them into the duct, then 23 `remove_edge` to delete the
   throwaway blocks (half the batch). ~5 min. Suggested: `add_band
   curve=CURVE side=...` without a host block, or a remove-blocks command.
   (tool)
2. `add_band` numbers its front vertices out of chain order (`bl_0` at
   x=-0.766, `bl_1` at the inlet); the first `add_fill` took the wrong
   corners. ~2 min. Suggested: chain order, or the front's end vertices in
   the digest. (tool)
3. The band's end spoke left the inlet line (x = -1.00037): the open-end
   rule has no host boundary edge to follow. Fixed with two `move_vertex`.
   (tool)
4. Skill Step 3 has no recipe for a band on an internal open wall. (skill)

### airfoil_tunnel (new case)

Wake band from the trailing edge to the outlet, no fan (requirement met).
Corner angles 56.1 .. 115.9; corner-screen aspect 338.

1. `add_ring` always builds a fan at a sharp trailing edge: probe build,
   `describe --bbox`, `remove_edge` of the fan's middle spoke, wake blocks
   from `add_vertex` + `add_block_from_vertices`. ~3 min. Suggested:
   `add_ring ... fan=false` or a wake option. **Round 1 gap H again** (tool)
2. Exact coordinates of existing vertices typed as 17-digit literals to
   place wake vertices level with band spokes; box stations chosen from a
   coordinate dump. ~2 min. Suggested: `add_vertex at=V dx=..`, a band
   extruded along a direction. (tool)
3. `examples/README.md` says "for a production wake use the engine's C-grid"
   while rule 1 sends internal flow with a body to the operators; no example
   of a hand C-grid with a wake band. ~5 min. (skill)
4. `first_width` counts the wake centreline edge as an airfoil spoke
   (+264%). ~1 min. Suggested: only spokes near the normal, or name the
   edge. **Known (size developer, cascade).** (tool output)
5. Shape smoothing narrowed the wake band at x = 1.6 from 0.1 to 0.041, so
   wake cells 1e-4 across run to the outlet; the trailing edge's streamwise
   clustering runs through the H column to floor and ceiling. Not held with
   `hold=`. **Round 1 gap A (lines meant to stay) and B again.** (tool, skill)

### bluff_ground (new case)

All requirements met; cells 49 percent above `field_cells` (budget binding),
checkMesh aspect 452. Corner angles 46.1 .. 127.1.

1. A one-piece conforming H-grid was refused ("a grid vertex at (-0.03,
   0.05) would fall inside the existing edge ... build the grid in parts");
   the message's advice worked. ~2 min. Skill Step 3 should say conformed
   stations run across the whole grid. (skill)
2. Shape step widened a column fed by a corner block (0.03 -> 0.075); `size`
   then left a jump of 7.17 that `budget=400000` did not lower; `hold=` on
   the column gave 3.28. ~6 min, two `size` runs of 2.3 min. Suggested:
   say when a jump is structural. **Round 1 gap A again (shape step and
   sizing cost).** (tool, skill)
3. `add_ring` builds one square block at a 90-degree corner where the help
   promises a fan; a probe session to find out. ~2 min. (docs)
4. `size` takes 2.3 min per run at 240k cells. (cost)

### cylinder_channel

Round 1: 36 blocks, 21,824 cells, 35.62 (3.08), 0.84, 1.84, 4.5 min, 121k.
This trial chose the variant with straight wake rows (max 41.57) over the
unheld one (35.2, average 5.11, chevron cells to the outlet).

1. Shape smoothing bent the wake lines (square's side midpoints moved out
   0.04, a hexagon, chevron shear along the wake rows) while reporting an
   improvement; caught in the render, redone with `hold=`. ~2 min. **Round 1
   gap A (lines meant to stay) unchanged**: `keep_lines` was not tried.
   (tool, skill)
2. `apply` without `--quiet` still prints `first_width=…`, `verification=
   field=…`; the agent re-ran with `--json`. ~1 min. (My check confirms: the
   fix of dc58fec reached the quiet digest only.) (tool output)
3. `build.txt` with a relative curve path. (trial artefact)
4. Eight spokes: 41.6 degrees in the square's corner blocks; sixteen not
   tried. (choice)

### u_bend

Round 1: 32 blocks, 24,010 cells, 20.88 (2.84), 0.80, 1.97, 4.5 min, 121k.
This trial's topology has diagonals from the tip ring to the inner
rectangle's corners (45-degree block corners): max 42.61, average 7.03 -
worse angles than round 1, same targets met.

1. First topology (diagonals into the concave wall corners) passed checkMesh
   but `size` put 103 tangential cells on each tip arc (cells 3e-5 wide,
   aspect 272-360) and the outer-wall first width reached +38%; seen only in
   per-block counts. Rebuilt with an outer-wall band. ~3 min. Suggested:
   skill rule for concave corners; a `size` note when a wall strip is pulled
   far below `wall_aspect`. (skill, tool)
2. Tangential coupling remains (tip cells grade finer towards the 45-degree
   spokes). (topology)
3. Ring vertices had to be typed to 1e-9 (0.35 cos 45) for `points=`.
   Suggested: polar points, or named grid vertices. (tool)
4. Reference curve drawn as Catmull-Rom bulges at 90-degree corners in
   renders. **Round 1 render issue again.** (render)
5. Non-quiet `size` text elides `verification` and `first_width`. **Second
   case this round** (cylinder_channel 2). (tool output)
6. Shape smoothing nearly a no-op here (43.30 -> 42.61). (note)

### y_junction

Round 1: 12 blocks, 41,946 cells, 39.96 (9.46), 1.20, 1.54, 4.5 min, 126k.
Same H-grid without wall bands as round 1; the route table's round-1 row
("no wall bands: fine columns run through the junction") was read as the
design (gap 4). `add_fill` (five-sided) and `add_stations` were not used.

1. The 50-degree corner sets a floor of 40 degrees; a spline opening edge to
   share it was split by `size` at its middle and gave 50.3. ~3 min.
   Suggested: a junction note; a five-sided fill recipe. (skill)
2. `first_width` counts edges running along the wall line and measures
   along the edge, not normal to the wall (0.00225 for 0.002). ~2 min.
   **Third case this round** (airfoil_tunnel 4; size developer). (tool output)
3. Preview renders do not say how much they coarsen. ~1 min. (render)
4. The route table's junction row reads as a recommended design. (skill)
- `grading=field` tried: spokes exact, worst jump 1.33 -> 2.08, average
  non-orthogonality 11.74 -> 14.06; not kept.

### cascade

Round 1: 44 blocks, 62,788 cells, 64.66 (28.77), 3.26, 2.87 (3 above 2.5),
17 min, 209k, a fan at the trailing edge, cyclic export failure worked
round, pairs matched by hand. This trial: wake band instead of a fan,
cyclic pair right at once (`add_stations offset=`, `add_fill`), 52 build
commands against 104. Corner angles 29.2 .. 153.7; average non-orthogonality
unchanged at 28.

1. Choosing a periodic-passage topology with a sharp trailing edge: ~10 min.
   Suggested: an example. (skill)
2. `add_ring` cannot end at a wake cut: `remove_edge` of the fan, two
   `move_vertex`. **Second case this round** (airfoil_tunnel 1). (tool)
3. No operator for a wake line with offset strips: 9 `add_vertex`, 6
   `add_block_from_vertices`, typed coordinates. ~3 min. **Second case**
   (airfoil_tunnel 1-2). (tool)
4. `first_width` counts the wake edge as a spoke (+144%); a script to find
   out. ~3 min. **Fourth case this round.** (tool output)
5. Wake strips carry the wall first width to the outlet (aspect 230);
   `grading=field` reported +277% and was not kept; 8 edges regraded by
   hand, first attempt made a jump of 5.3; 0.039 cells left (core 0.03).
   ~5 min. **Gap B again** (airfoil_tunnel 5, bluff_ground). (tool)
6. Shape smoothing pulled an upstream row onto the floor (Q-U_0 at 0.1);
   rerun with `shape_length_ratio=0.4`. ~2 min. **Gap A again.** (tool)
7. Spline control points drawn in a patch's colour. (render)
8. `size` gives split vertices the IDs of deleted vertices. (tool)
9. Left: a 29-degree corner at a valence-5 vertex, the 60.1-degree face.

### blunt_plate

Round 1: 119 blocks, 87,033 cells, 26.96 (6.12), 0.74, 3.07 (4 above 2.5),
helper script for ring stations, 4 hand gradings, 13 min, 154k. This trial:
no helper (`tips=true` gave the nose station here with count=40), 8 min.

1. One conforming `add_grid` round the box refused (stations of the base on
   one side only); four grids. ~1 min. **Second case this round**
   (bluff_ground 1). Suggested: skill line; or cut station lines at
   existing blocks. (skill, tool)
2. **Default `size` failed checkMesh**: "High aspect ratio cells found, Max
   aspect ratio: 1846.52, number of cells 201" in the shear-layer strips at
   the outlet (wall spacing 2e-4 carried to x = 12). Nothing before `check`
   said it fails. `grading=field` gave 1008 (one cell), two hand
   `start_width` gradings 479. ~3 min. **Gap B, now a checkMesh failure.**
   Suggested: `size`/`quality` warn above 1000; `grading=field` treats 1000
   as a limit. (tool)
3. No `wall_aspect` default named in SKILL.md when the brief gives none.
   (skill)
- Largest cell 0.405 against core 0.3 after `grading=field`.

### naca4412

Round 1: 250 blocks, 208,278 cells, 67.01 (7.15), 0.85, 4.43 (27 above 2.5),
two hand gradings, `method=distance` detour, 20.5 min, 137k. This trial:
`grading=field` from the skill text, no hand grading, 45.12, 8 above 2.5.

1. The engine prints nothing for 8.5 minutes; ~2 min on waiting mechanics.
   Suggested: a stage line per phase. (engine output)
2. No command reports first widths after the polish; `size`'s range
   includes the wake centre line; a script with two failed attempts. ~3 min.
   **Fifth case this round on the `first_width` measure.** (tool output)
3. `refused` names a vertex that does not exist (the would-be split
   vertex). (tool output)
4. `describe --section patches` fails; the section is `boundaries`. (tool)
5. Default render labels cover the near field at 250 blocks. (render, TRIAL.md)
6. A column of fine streamwise cells from the airfoil to y = +-15 (count
   component through the C-grid's vertical lines). **Gap B (cells).** (tool)
7. Step 5 does not say jumps are judged after the polish (4 above 5 -> 0).
   (skill)

### three_cylinders

Round 1: 204 blocks, 46,464 cells, 68.05 (16.71), 3.13, 3.62 (7 above 2.5),
Step 4 left out, 22 min, 173k. This trial ran Step 4 (at a floor of 0.3):
64.66, but 25 jumps above 2.5 (7) and 13 percent more cells. Corner angles
24.9 .. 154.4.

1. Engine crash on the plain annular run (`voronoi_graph: TraceError: the
   bisector walk stalled`); `engine.md` names no remedy; `--hull` guessed.
   ~2 min and one engine run. (engine, skill)
2. Shape smoothing at the default floor pulled the hull ring onto the band
   ring (edge at 0.1): 12 jumps above 5 after `size` (6 at budget 90,000);
   `shape_length_ratio=0.3` gave 0. ~6 min. **Gap A: the floor of 0.1 is too
   low here (my rerun after the fix said the same); second case this round
   that raised it** (cascade 6: 0.4). (tool)
3. The polish pushed a jump past 5 (4.73 -> 5.20; `size_cap` lets a jump
   above 2.5 grow by a tenth); held by `hold=`. ~8 min. (tool)
4. Sliver strips from clustered medial junctions (0.02-0.05 wide, ring
   counts carried to the boundary); `combine_blocks` refused on most
   ("would leave a hanging topology vertex"). ~7 min. (engine)
5. Default budget was binding (12 above 5); `sizing.md`'s "budget=75000 gave
   the default's mesh" reads as if a larger budget never helps. (skill)
6. `describe --section patches` fails. **Second case** (naca4412 4). (tool)

### high_lift

Round 1: 538 blocks, 378,785 cells, Mesh OK, 62.33 (12.21), skewness 3.59,
4.06 (33 above 2.5), two vertex scripts, 64 min, 373k. This trial: no
script and 72 commands, but checkMesh fails. My reading of the renders: the
bands go through both slots and coves and the three wakes have their own
strips (`trial2/flap_te_wake.png`), better than round 1's wakes riding on
the next element's front. The slat-cove fill is one row of 12 long blocks
from the cusp to the main leading edge, strongly sheared
(`trial2/slat_cove_slot.png`; 391 of the 396 faces). The far field is three
transfinite fills carrying every near-field station to the outer boundary
(`trial2/overview.png`): 949 blocks, average non-orthogonality 25.8 (12.2 in
round 1), the skew faces at the (25, 15) corner. First widths (my measure,
all spokes): flap 1.5e-5..3.7e-5 for 1e-4 - 25 cells on a 0.003 band, the
count tied to the fills beyond it.

1. A reflex point inside an `add_fill` side (the flap nose in the cove
   fill): three refusals, then a fill the build accepted whose sampled grid
   folded; two `move_vertex`. ~12 min. Suggested: sample a fill's blocks for
   folds at build time. (tool)
2. `add_fill` "bound no region ... give the corners in order" when a fan
   vertex lay inside the region; the code was read to find out. ~8 min.
   Suggested: name the vertex. (tool message)
3. Transfinite fills squeeze their rows at a trailing edge where the wake
   leaves at a shallow angle to the surface ("stations ... do not face each
   other"); a cut straight down from the trailing edge instead. ~10 min.
   Suggested: a band along a chain that ends on a wake line. **Third case
   this round on the trailing edge** (airfoil_tunnel 1, cascade 2-3). (tool)
4. An open fill side took stations of a neighbouring wake line; every side
   listed explicitly. ~4 min. Suggested: report the vertices an open side
   took. (tool message)
5. Folds inside two main band blocks after `size` (identical gradings);
   `repair_block`: "the defect is in the topology"; two front splines made
   lines and two wall splines refitted. Cause not found. ~6 min. (tool,
   open)
6. Run times at 949 blocks: `size` 3.5 min, `smooth` 6.3 min, one
   `repair_block` 3.2 min; a foreground command moved to the background led
   to a duplicate 14-minute run. **Second case on run time** (bluff_ground
   4; the engine's silence in naca4412 1). (cost)
7. `smooth mode=shape` refused: "225 sampled cell(s) already fold" on the
   count-free map, no block named; Step 4 left out. ~2 min. (tool message,
   skill)
8. Matching the cove fill's 12 edges to the main leading edge fixed its
   corner at x = 0.26 (the sliver block); changing the ring's stations would
   have renamed every later `m_` ID. Suggested: stable station IDs, or a
   fill that splits a side to match. (tool)
9. Far-field corner skewness left (`repair_block` found no move; out of
   time). (topology)
10. Non-quiet `size` text elides `first_width` and `verification`. **Third
    case this round** (cylinder_channel 2, u_bend 5). (tool output)
11. Three wake guide curves typed by eye (about 25 coordinates): no operator
    offsets a front chain as a curve. **Fourth case on typed coordinates**
    (airfoil_tunnel 2, u_bend 3, cascade 3). (tool)

## Recurring gaps, round 2

| # | gap | cases | kind |
|---|---|---|---|
| 1 | the shape step and `size` disagree: the step shortens edges or bends lines for angles, `size` pays in jumps and cells; agents answer with `hold=` or a raised floor (0.3, 0.4); `keep_lines` used by none; on the largest case the step refuses without naming a block | cylinder_channel, airfoil_tunnel, bluff_ground, cascade, three_cylinders, high_lift | tool (`smooth mode=shape`) |
| 2 | wall-fine spacing carried away from the wall: cells 18 to 49 percent above `field_cells`, aspect ratio above checkMesh's 1000 from the default sizing (blunt_plate), a first width six times finer than asked where counts tie (high_lift) | bluff_ground, blunt_plate, cascade, naca4412, airfoil_tunnel, three_cylinders, high_lift | tool (`size`) |
| 3 | `first_width` counts wake and oblique edges and measures along the edge; nothing reports it after the polish | airfoil_tunnel, y_junction, cascade, naca4412 | tool output |
| 4 | non-quiet `size` text elides `first_width` and `verification` | cylinder_channel, u_bend, high_lift | tool output |
| 5 | sharp trailing edge by operators: `add_ring` always fans, no wake band, no band ending on a wake line | airfoil_tunnel, cascade, high_lift | tool (operators) |
| 6 | coordinates of existing vertices typed by hand (wake vertices level with spokes, polar points, offset guide curves) | airfoil_tunnel, u_bend, cascade, high_lift | tool (operators) |
| 7 | one conforming `add_grid` round a box refused; in parts it builds | bluff_ground, blunt_plate | skill line |
| 8 | `describe --section patches` fails (the section is `boundaries`) | naca4412, three_cylinders | tool |
| 9 | long silent commands (engine 8.5 min, `size`/`smooth`/`repair_block` minutes each above 200k cells or 900 blocks) | naca4412, bluff_ground, high_lift | cost, output |
| 10 | skill text: route rows read as designs (y_junction), no hand C-grid or periodic example (airfoil_tunnel, cascade), jumps judged before the polish (naca4412, three_cylinders), engine crash without a remedy (three_cylinders), no `wall_aspect` default (blunt_plate), no band recipe for an open wall (nozzle), no concave-corner rule (u_bend) | as named | skill |
| 11 | the polish lets a jump above 2.5 grow past 5 | three_cylinders | tool (`smooth`) |
| 12 | no band on the fluid side of an open wall chain without a host block (throwaway blocks and 23 `remove_edge`) | nozzle | tool (operators); one case, a general primitive |
| 13 | `add_fill` messages: "bound no region" without the vertex inside, stations an open side took, a fill that folds on the sampled grid accepted; `add_band` front IDs out of chain order | high_lift, nozzle | tool messages |
| 14 | far field round a multi-element cluster by hand: fills carry every station to the boundary (949 blocks, average 25.8 degrees) | high_lift | tool (operators), skill; one case |

Not recurring, kept: `size` reuses deleted vertex IDs (cascade), `refused`
names a vertex that does not exist (naca4412), render issues (reference
curve bulge, control-point colour, labels at 250 blocks, coarsening not
stated), engine sliver strips (three_cylinders).

## Reading

- **What the fixes after round 1 bought.** Construction: no vertex script in
  eleven trials (round 1: two scripts and a helper in eight); the cascade's
  periodic pair right at once in half the commands; three unseen cases
  built in 3.5, 10.5 and 14 minutes. Sizing: NACA 4412 from 67.0 to 45.1
  degrees without a hand grading.
- **What they did not buy.** Time and tokens on the common cases are flat
  (150 -> 161 min, 1.41M -> 1.34M). Cell counts are flat. The shape step
  (gap 1) and the cell surplus (gap 2) are where round 1 left them; each
  still touches six or seven cases.
- **The hard case got worse.** `high_lift` by operators passed in round 1
  with scripts and fails now without them. The fill replaced the script
  for the coves and slots; for the far field a script-built hull was
  replaced by fills that are worse. At least 27 of the 74 minutes went to
  waiting on `size`, `smooth` and `repair_block`, a duplicate run included.
- **Limit of the measurement.** One trial per case. `u_bend` went from 20.9
  to 42.6 degrees with no tool involved, so the spread between agents is of
  the size of several differences in the table. Round 3 should repeat the
  cheap cases three times to put a number on it.

## Fixes after round 2

### Batch 1: reports, messages, skill text (commits 834bf23 .. 09e1c69)

Nothing here changes what a default command builds, sizes or moves: `size`
then `smooth` on the cylinder_channel and cascade trial sessions give
byte-identical sessions with 9ee981f and with the new code (my runs,
`/tmp/garbage/blockdrawer-bench/verify-batch3/`). Main suite: 581 tests OK
(my run). `SKILL.md` 250 lines.

| commit | what | gap |
|---|---|---|
| 834bf23 | `apply` without `--quiet` prints each command's digest note under its result line | 4 |
| 4d04548 | `first_width` is the first cell height normal to the wall at band spokes (edges within 45 degrees of the wall normal, each counted once), extremes named; `quality` reports it for any session | 3 |
| 68c44e1 | checkMesh's cell aspect ratio in the sampler, in `quality`'s verdict and `size`'s verification; a warning above 1000 naming the block. Sampled against checkMesh on the eleven trial sessions: equal to four digits. `size` does not act on it yet | 2 (the report half) |
| 71e07a6 | `describe --section patches` | 8 |
| a2643ed | `smooth` fold refusals name the blocks; `add_fill` says why each reading of the sides was rejected, lists the stations an open side took, notes a block that folds at even spacing; `add_band` note gives the front in chain order; `size` sliver refusal names an existing edge; the fan rule in the help | 13, parts of 1 |
| 139b38f, 09e1c69 | skill: weaknesses in route rows marked "not a design", cascade row from round 2, conforming grid in parts, Step 4 remedies (`hold=`, a higher floor, the count-free fold refusal), jumps judged after the polish, `wall_aspect` 25, a larger budget when binding, run times, the engine crash and `--hull`, wake advice made consistent. 09e1c69 is my correction of two sentences | 7, 9, 10 |
| fb3834b | `research_cli.py run` prints a line per stage on stderr | 9 |

`first_width` on the trial sessions, old against new (developer's table,
spot-checked by me on blunt_plate and the cascade): the wake centre lines
drop out (airfoil_tunnel +264% -> +0%, cascade +144% -> +4%, naca4412
+256% -> +5%); y_junction's corner edges are counted once and normal to
their wall (-18%..-2%, no value above the asked width); high_lift's flap
stays 1.5e-5..1.8e-5 for 1e-4, a real finding. Two high_lift
trailing-edge spokes lie at 44.8 and 47.7 degrees, either side of the cone.

Open from this batch: blunt_plate's field state is already at aspect 1533
before spending (1847 after), so a guard on spending alone would not have
prevented the checkMesh failure; it belongs with the sizing batch.

### Batch 2: the shape step and the polish (commits 3f27acb, 1ed2465)

Question: is there a length rule for `smooth mode=shape` that removes gap 1
by default? Answer, measured: no. Corpus: 20 pre-shape sessions (the eleven
round-2 cases, five round-1 builds, the three skill examples, the engine's
square cylinder), each through shape / `size` / polish under eight rules;
tables in the README and `/tmp/garbage/blockdrawer-dev/shape/full_table.md`.
The rule for a new default was fixed beforehand: against today's default no
session gains a face above 70 or a jump above 5, the total of jumps above
2.5 falls, no maximum rises more than 2 degrees.

| rule | fails on |
|---|---|
| floor 0.3, floor 0.5 | square cylinder: 0 -> 6 / 90 faces above 70, +18 / +26 degrees |
| `keep_lines=true` | jumps above 2.5 rise 68 -> 82 (16 sessions); seven maxima rise 3 to 16 degrees |
| interface-pair ratio within 1.5 (`shape_pair_ratio`) | square cylinder 0 -> 241 faces; round-1 blunt plate 4 -> 6 jumps above 5 |
| interface-pair ratio within 2.5 | square cylinder 0 -> 142 faces; three maxima rise 2.4 to 28 degrees |
| count-component spread within 2.5 (`shape_spread_ratio`, the developer's formulation) | NACA 4412: one face above 70 (checkMesh: 6 at 76.6); round-1 high-lift 5 -> 6 jumps above 5 |

Both limits are committed as options, off. My reading (a jump is a changed
length ratio of two consecutive edges along a block line) holds in part: 16
of the 22 jumps above 5 the default leaves sit on pairs changed 3.8 times
or more, but of 72 pairs changed more than five times only 9 end above 5,
and the square cylinder's whole gain is a pair changed 19 times that ends
at a jump of 2.05. `size` grades most of it away; no count-free limit
separates the two.

What the table says beyond the rule:

- The shape step earns its place on angles: against no step the maximum
  falls by 3 to 32 degrees in seven sessions and rises in one (round-2
  cascade, +2.1). It costs 10 percent of cells over the corpus (1.64M ->
  1.80M) and jumps in five sessions (three cylinders 17 -> 46 above 2.5,
  NACA 4412 2 -> 21, bluff_ground 4 -> 18).
- The chain is sensitive: one option changes `size`'s splits and raises,
  and the final maximum moves by several degrees either way (airfoil_tunnel
  25.5 to 45.3 over the eight rules). For every session but the round-1
  blunt plate and the two high-lifts at least one rule ends with no jump
  above 5. Trying two or three variants and keeping the best by the final
  verdict is what the agents did by hand and is the remedy the skill now
  names (`shape_pair_ratio=2.5`, a higher floor).
- 1ed2465: the polish keeps a width pair below 5 below 5 (`size_ceiling`).
  On the sweep's 95 sized states three changed, all three cylinders (12 ->
  10, 2 -> 0, 2 -> 0 above 5), none worse. Default.
- Default chain failures found by the sweep: round-2 blunt plate fails
  checkMesh on aspect ratio (1018, 2 cells); the round-2 high-lift build
  folds 46 cells after the default `size` (its trial repaired that by
  hand). Both are `size`'s.

My checks: main suite 588 tests OK; three cylinders through the default
chain 64.83 degrees, 46 jumps above 2.5 and 10 above 5; with
`shape_pair_ratio=2.5` 67.26, 13 and 0 (the developer's rows).

Corrections to the notes above: bluff_ground's jump of 7.17 was read after
`size`; the default polish takes it to 4.18, so `hold=` was not needed for
the target. three_cylinders' "floor 0.3 gave 0 jumps above 5" held with the
trial's `budget=90000` and two `combine_blocks`; in the default chain the
floor leaves 2.

### Batch 3: sizing (commits 84e66f4, 9ce2199, c4cc549)

Three changes of what the default `size` returns, each measured on the
20-session corpus of batch 2 (shaped states of its default rule), and a
diagnosis of the cell surplus. Developer's scratch:
`/tmp/garbage/blockdrawer-dev/size/`.

**84e66f4, aspect ratio.** checkMesh's limit of 1000 is treated like
skewness 4. A repair step on the field's state (`repair_aspect`) takes each
block above 1000, worst first, and "releases" a strip through it: its
members go all, half or a quarter of the way from the strip's one shared
ratio to their own series (the series `grading=field` uses), accepted when
no interface jump at its ends rises above 2.5 and its own value, nothing
folds, no face above 70 appears and skewness stays below 4. Every guard
(per move, verification, continuity rounds, the field pass) refuses a state
that crosses 1000. The aspect is measured in the plane. Only the round-2
blunt plate changes (the other 19 sized states are byte-identical): final
94,784 cells / 36.46 / aspect 1018, checkMesh failed -> 79,194 / 36.00 /
585, Mesh OK. This is the general form of the trial's two hand
`start_width` gradings (gap 2, blunt_plate 2).

**9ce2199, folds.** Cause of high_lift gap 5 found: OpenFOAM's Catmull-Rom
spline loops inside a segment much shorter than its neighbour (main ring
edges ending with 0.0014 beside 0.0165), and BlockDrawer evaluates it as
blockMesh does. 49 of the build's 202 spline edges hook; the input is
untangled at its own counts and folds from 32 even cells a side, which is
also the shape step's "225 cells already fold" refusal (gap 7 of that
case). `size` graded four blocks into the loops (46 cells).
`model.spline_hooks` lists such segments; a repair step regrades a strip
through a folded block towards uniform; a result that still folds where
the input did not is refused, naming blocks, edges and the control point to
remove. Round-2 high-lift after the default `size` and polish: folded cells
46 -> 0, faces above 70 2387 -> 1807, maximum 175.9 -> 79.9, checkMesh
failures 4 -> 1 (skewness 4.07 in one far-field block). **The cause is in
the builders** (`add_ring` keeps curve points down to a thousandth of the
chord from a station); the developer left that out because it changes the
examples' documented results. It goes first in the next batch.

**c4cc549, `grading=field` is the default.** Rule fixed beforehand, against
`grading=strip`: no session gains a face above 70, a jump above 5 or a
checkMesh failure; corpus jumps above 2.5 not higher; no maximum more than
2 degrees higher; no session 5 percent more cells; no wall patch's worst
first-height deviation 5 points worse and the corpus mean lower. Result:
every term passes. Faces above 70: none gained (high-lift 1807 -> 1391).
Jumps above 5: none gained (three cylinders 10 -> 4, round-1 blunt plate
4 -> 0, round-1 high-lift 5 -> 1). Jumps above 2.5: 257 -> 177. Largest
rise of a maximum: +1.15 degrees (bluff_ground); NACA 4412 68.60 -> 45.12,
round-2 cascade 68.50 -> 60.57. Cells unchanged (the pass changes no
count). Worst first-height deviation per wall patch: mean 20.4 -> 12.7
percent over 44 patches, none worse by more than a point; most plain walls
end at 5 percent. checkMesh on all 40 final states: only the round-2
high-lift fails, in both chains (skewness 4.07 / 4.05). Not fixed by it:
high-lift's flap stays 84 percent too fine (1.64e-5 for 1e-4), its strip
refused by the jump guard, its count of 25 set by a 45-edge component
reaching 12 long.

**Where the surplus cells are (no code).** Totals over the corpus:

| stage | cells | |
|---|---|---|
| each component at its own profile demand | 1,045,279 | |
| + one count per component, one series per strip (the "strip fit") | +334,759 | +32% |
| = `field_cells` | 1,380,038 | |
| + repair (count raises for faces above 70; NACA 4412 37,550 of it) | +46,809 | |
| + spending on jumps | +358,759 | +26% of field |
| final | 1,784,404 | 1.29 x field, 1.71 x demand |

So the "18 to 49 percent above `field_cells`" of gap 2 is spending, and a
surplus of the same size is already inside `field_cells`. What spending
buys (budget as a multiple of the field's cells, `size`'s own jump
measure): 1.0: 357 jumps above 2.5 and 89 above 5; 1.25: 246 and 58; 1.5:
223 and 45. The first 17 percent of cells remove 6.4 jumps above 2.5 per
percent, the next 12 percent 1.9. Largest single items: NACA 4412's wake
row 280 cells against a demand of 80 (+50,400: one geometric series from
0.0005 to 0.5 over a length of 19) and its far-field column (+47,299);
each high-lift has one component of about 68 edges, 0.011 to 2.17 long,
tying the band spokes through the fills to the far field (+48,657 and
+50,320). An experiment with each member fitted at its own ratio instead
of the strip's lowered field cells only 1.38M -> 1.28M and raised jumps
above 5 from 45 to 82: the cost is one series per edge, not the shared
ratio. Developer's proposal: split a strip where its single-series fit
exceeds its profile demand 1.5 times (upper bound of the saving: the 335k
strip-fit cells, 19 percent of final cells), re-measured under the rule
above.

My checks (`/tmp/garbage/blockdrawer-bench/verify-batch4/`): main suite 595
tests OK. Full default chain at c4cc549 from the pre-shape corpus sessions,
then `check`: round-2 blunt plate Mesh OK, 79,194 cells, 30.93, aspect 509,
first widths -8%..+0%; round-2 cascade Mesh OK, 60.57, skewness 2.54, no
jump above 2.5; three cylinders Mesh OK, 64.77, skewness 3.96, 4 jumps
above 5 (worst 5.77), first widths +0% on all three cylinders; round-2
high-lift: no fold, 1391 faces above 70, 77.26, checkMesh fails on
skewness 4.047 (9 faces) only. `size ... grading=strip` at c4cc549 against
1ed2465's default on the same shaped state: cascade byte-identical, blunt
plate different (the aspect repair), as claimed.

Open from this batch:

- The developer's classification of the strip-fit surplus by profile shape
  (159k two-ended below the split ratio, 122k flat, 57k levelling off) left
  out its own largest class: `classify.out` also holds 532k cells in 16
  "two-ended, peak inside" components, more than the surplus being
  classified, so the script counts something else than the attribution
  table. The proposal needs a classification that sums to the 335k before
  any code.
- The shape step's refusal text still says a count-free fold is there
  "whatever its counts"; with hooked splines as the cause it should name
  the edges, as `size`'s refusal does.
- Three cylinders still ends with 4 jumps above 5 by default.
- high_lift by operators: skewness at a far-field corner, 1391 faces above
  70 in the cove fill, the flap's first width. None is `size`'s to fix; the
  +48k component and the flap count are one topology fact (band stations
  carried through the fills to the far field, gap 14).

### Batch 4: construction operators (commits b2b4673 .. 077e362)

| commit | what | gap |
|---|---|---|
| b2b4673 | no operator writes a spline that runs backwards: control points are removed until `model.spline_hooks` finds nothing (the point at the short segment's end beside its longer neighbour; no length threshold, the test is whether the evaluated spline reverses along its chord). `quality` warns of such an edge in any session; the shape step's count-free fold refusal names them | high_lift 5 and 7 (cause) |
| e5a2131 | `add_ring`/`add_band` `fan=false` or `open_at=X,Y`: a convex corner left open, the band ending at the spoke normal to each side. `add_rows`: a chain of exterior edges carried along a direction to `x = X`, `y = Y` or by a distance as rows of blocks, vertices reused by coordinate, `spread=S` widening the far row | 5 |
| d860dd4 | `add_ring CURVE H side=left|right`: a wall band along an open curve with no host block, stations at `xs=`/`ys=`/`fractions`/`count`, end spokes along `start=`/`end=` | 12 |
| 14c05bd | `add_vertex`/`move_vertex` relative to vertices: `at=V dx= dy=`, `x_of=V y_of=W`, polar `r= theta= about=V` | 6 |
| 077e362 | skill Step 3, a fourth example `naca0012_cgrid.txt` (open trailing edge, wake band by `add_rows`) | 10 |

Hooked spline edges in the rebuilt operator builds, old -> new: NACA 0012
example 12/160 -> 0, airfoil_tunnel 26/80 -> 0, bluff_ground 4/8 -> 0,
blunt_plate 2/16 -> 0, cascade 11/48 -> 0, high_lift 49/202 -> 0; six
builds had none and are byte-identical. Largest distance from the curve
over the changed edges: unchanged to within 1e-6 except high_lift (3.7e-4
-> 5.9e-4 on one edge along a typed wake guide curve). Rule stated before
(no session gains a face above 70, a checkMesh failure or 2 degrees):
passes, largest rise +1.37 degrees (NACA example 48.27 -> 49.64).

Default chain on the round-2 high_lift build, before -> after b2b4673:
checkMesh **fails (skewness 4.047), 1391 faces above 70, 77.26 -> Mesh OK,
skewness 3.93, 198 faces above 70, 71.93**. The shape step, refused before
over 225 count-free folded cells, now runs. The price: 292,143 -> 396,460
cells (budget binding), jumps above 2.5 96 -> 334 and above 5 29 -> 82
after `size` (281 / 76 after the polish, developer's count); flap first
width -84% -> -59%..-6%. airfoil_tunnel: 113,164 -> 128,902 cells, 27.40
-> 26.08.

Trial batches re-expressed with the new commands (developer's scratch
`/tmp/garbage/blockdrawer-dev/ops/evidence/`), default chain and checkMesh:

| case | build commands | result before | after |
|---|---|---|---|
| nozzle | 45 -> 13 | Mesh OK, 14,620 cells, 12.01 | the same 33 blocks, identical numbers |
| airfoil_tunnel | 36 -> 31 | Mesh OK, 128,902 cells, 26.08 | the same 208 blocks, 128,982 cells, 25.43 |
| cascade | 52 -> 37 | Mesh OK, 50,974 cells, 60.57, skewness 2.53 | 50 blocks, 49,182 cells, 60.62, skewness 2.86 |

My checks (`/tmp/garbage/blockdrawer-bench/verify-batch5/`): main suite 617
tests OK. Nozzle rebuilt from the re-expressed batch: Mesh OK, 14,620
cells, 12.01, first widths +0%. High-lift rebuilt from the first 67 lines
of the trial's batch (no hooked edge reported) through the default chain:
the numbers above. The new example: 14,393 cells, Mesh OK, 38.07, aspect
308 at first width 5e-4; at 1e-4 checkMesh fails (aspect 1356, 40 cells,
10 faces above 70).

Two findings from the example, sent back to the developer:

- **The polish crosses the aspect limit.** At 1e-4 `size` returns aspect
  986 and the default `smooth` ends at 1356: `smooth` has no guard on
  checkMesh's 1000. General, the same rule as `size`'s.
- **The example's far field is poor.** The airfoil's 40 stations are
  carried straight up and down to y = +-10 as columns 0.025 wide, so the
  wall's chordwise spacing reaches the far field. It passes only at 5e-4.
  An example is copied by every later agent; it should fan out and pass at
  the fan example's 1e-4.

**Follow-up (commits aaf7bcd, 3a8540d).**

- aaf7bcd: the polish keeps checkMesh's aspect limit by `size`'s rule
  (`sampling.aspect_worse`: a state at or below 1000 may not cross it, one
  above may not gain cells above it). A displacement that breaks it is
  scaled back by halves like the polish's other conditions; when the full
  displacement crossed the limit the optimisation also runs once more with
  the vertices of the offending blocks held, and the better of the two
  results (faces above 70, then the maximum) is applied. `reason` and the
  note's `aspect_blocks` say when it bound. Not applied in `mode=shape`
  (stale counts). The polish can now take twice as long where the limit
  binds (76 s -> 142 s on the example).
- Measured by the developer on 22 sized states, old polish against new:
  the 20 corpus states are byte-identical (my `cmp` of his files: 20
  identical, 2 different). C-grid example at 1e-4: aspect 1356 (40 cells),
  10 faces above 70, 75.42, checkMesh fails -> 978, 11 faces, 76.15, Mesh
  OK, applied at half the displacement. The same build without its wake
  spread, which `size` already leaves at 6003: 339 -> 284 cells above the
  limit, 61.52 -> 63.18, still failing.
- **My rule for this item was broken on the session that changed** (one
  face above 70 more and +0.73 degrees against the old polish). Accepted:
  the old result is the one that failed checkMesh, and the new one is no
  worse than the polish's own input (15 faces above 70, 76.79 after
  `size`). The rule should have been stated against the input, not
  against a result the guard exists to refuse.
- 3a8540d: the example's far field is **unchanged**; my target (Mesh OK at
  1e-4 with no face above 70) is not reached. The example's row and result
  lines now give the 1e-4 numbers (41,152 cells, Mesh OK, 11 faces above
  70, 76.2, skewness 0.89, aspect 978) and call the far field a weakness.
  What stops it: the columns above the airfoil and the trailing edge are
  straight and 0.025 wide to y = 10, so they carry the trailing edge's
  streamwise cell (`size` gives 4e-4 there: both surfaces' across size
  reaches past the 13-degree wedge) to the top. Bands or `to_box` round
  the front and the wake band's sides, which would fan the lines, are
  refused or fold at the concave corner where the band front meets the
  wake band. No operator was added for it (one case).
- My checks (`/tmp/garbage/blockdrawer-bench/verify-batch6/`): main suite
  621 tests OK; the example rebuilt and run through the default chain at
  1e-4: the numbers above, `size`'s field state 26 faces above 70 and
  81.61, its result 15 and 76.79, the polish 11 and 76.15; round-2 cascade
  through the default chain at HEAD byte-identical to the batch-3 result.
  `SKILL.md` 250 lines.

Limit of the benchmark from here on: the fourth example is the pattern two
benchmark cases asked for (airfoil_tunnel, cascade: a hand C-grid with a
wake band), so those two are no longer unseen. Round 3 adds new cases to
keep a held-out part.

Not covered by this batch: gap 14 (far field round a multi-element
cluster), the surplus classification of batch 3, a far field that fans
round a C-grid with an open trailing edge.

Stray processes left by agents of this session, idle wait loops that never
end (`pgrep -f` matching its own shell): PIDs 1610, 30872 (naca4412 trial),
9928, 12809, 15823 (batch 3). Killing them was denied to me by the
permission classifier; they sleep and use no CPU.

### Batch 5: the strip-fit surplus (no commit)

Question: can `size` remove the 335k cells of the strip fit by splitting a
strip where one geometric series cannot follow the profile? Answer,
measured: on simple cases yes, on the large ones no; the corpus total moves
by 1.8 percent. Nothing is committed; the repository stays at 3a8540d.
Scratch `/tmp/garbage/blockdrawer-dev/surplus/` (`item1_tables.md`,
`ratios2_table.md`, `rule5.out`); the patch with its tests is kept as
`patches/fit_split_3a8540d.diff` beside this file.

**Classification (sums to the attribution table, session by session).**
The earlier `classify.py` worked on the final state and counted fits that
`size` never pays for two-ended members; its 532k class is empty in the
field state.

| class | components | surplus | bound with one split |
|---|---|---|---|
| shared strip ratio (the member's own ratio needs far fewer) | 38 | 165,646 | 127,358 |
| fine at one end, then level | 66 | 104,276 | 75,630 |
| two-ended below the split ratio | 43 | 35,170 | 24,656 |
| flat (series held at the finer end's width) | 48 | 27,757 | 23,530 |
| piece of an already split strip | 3 | 1,910 | 1,360 |
| total | 198 | 334,759 | 252,534 |

Go/no-go fixed beforehand (bound at least 150k, ten sessions at 10 percent
of their field cells): go, 252,534 and 17 sessions.

**The split rule (`fit_split`, a strip cut where its single-series fit
needs more than 1.25 times the cells of the best single cut).** Realised on
the field state: 33,090 cells, 13 percent of the bound. What stops the
rest: the round is undone because the field state's jump cost rises (NACA
4412's wake row 280 -> 85, round-2 blunt plate, airfoil_tunnel, cascade,
grooved tandem); the cut would leave a piece shorter than one wanted cell
(NACA 4412, both high-lifts, bluff_ground, round-2 cascade); three
cylinders' continuity check undoes it.

Rule for the default, fixed beforehand, full chain on the 20 sessions:
faces above 70, jumps above 5, checkMesh, maxima (+0.15 at worst), cells
per session: pass; jumps above 2.5 177 -> 170: pass. **Fails:** corpus
final cells 1,784,404 -> 1,752,191, -1.8 percent where 8 was asked; a wall
patch's first height 10 points worse (y_junction's branches, 1.70e-3 for
2e-3). Option condition (five sessions 10 percent lower with no term
broken): four, so no option either.

| session | blocks | cells | max | jumps > 2.5 |
|---|---|---|---|---|
| square cylinder | 16 -> 20 | 5,184 -> 4,204 (-19%) | 52.16 -> 52.16 | 0 -> 0 |
| y_junction | 12 -> 18 | 40,828 -> 20,254 (-50%) | 39.99 -> 39.95 | 0 -> 0 |
| backward step | 36 -> 40 | 21,750 -> 18,800 (-14%) | 0 -> 0 | 0 -> 0 |
| u_bend | 26 -> 34 | 22,700 -> 18,500 (-19%) | 42.29 -> 42.44 | 0 -> 0 |
| cylinder_channel | 28 -> 32 | 17,248 -> 14,432 (-16%) | 34.96 -> 34.96 | 0 -> 0 |
| bluff_ground | 131 -> 158 | 235,582 -> 234,889 (-0.3%) | 45.39 -> 43.89 | 12 -> 5 |

The other 14 sessions are unchanged. Reading: where the surplus is large
in cells (multi-body cases, C-grids) the strips that carry it cannot be cut
without a sliver or a jump, because the same line crosses bands a few
hundredths wide; the surplus there is a property of the topology (stations
carried to the far field), which is what gaps 2 and 14 said. The sizing
stage is not where the remaining cells are to be won. Not verified by me
beyond reading `rule5.out` and the unchanged tree: there is nothing
committed to check.
