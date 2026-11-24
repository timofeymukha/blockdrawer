# Benchmark round 3 (Nov 19-20 2026)

Code: `review-followups` at 3a8540d (24 commits after round 2: fix batches
1-4 of `ROUND2.md` - reports and skill text, the shape step's limits and the
polish's guards, sizing with `grading=field` as the default and the aspect
repair, the construction operators `add_ring side=`, `fan=false`,
`add_rows`, relative `add_vertex`, and the skill's fourth example, a hand
C-grid with a wake band). Protocol: `TRIAL.md`, deliverables in
`CASE/trial3/`. One fresh Opus agent at high effort per case, one at a time,
two cores. Two new cases (`bump_channel`, `triangle`) are the held-out part:
no row in the route table and no example built for them. `airfoil_tunnel`
and `cascade` are no longer unseen, since the fourth example is the pattern
their round-2 reports asked for. checkMesh numbers are from my own reruns
(`bin/verify CASE trial3`). The four cheap cases were then run twice more
(`trial3b`, `trial3c`) to measure the spread between agents.

## Results

| case | route | blocks | cells (field) | max non-orth (avg) | skew | aspect | worst jump (>2.5) | first width | own script | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bump_channel (new) | engine run dropped; operators: open ring on each wall, one fill | 52 | 66,928 (53,040) | 31.93 (14.14) | 0.99 | 29 | 2.50 (0) | -5%..+5% | no | 5.5 min | 134k |
| triangle (new) | operators after the C-grid example: ring with fans, wake rows, far field by rows, two fills; no shape step | 346 | 31,708 (28,906) | 30.04 (12.29) | 0.86 | 648 | 2.24 (0) | -4% | no | 5 min | 136k |
| nozzle | engine run dropped; operators: two open rings, one fill | 56 | 14,878 (12,802) | 12.01 (2.77) | 0.41 | 26 | 1.48 (0) | -5%..+5% | no | 3.5 min | 123k |
| cylinder_channel | operators: ring, band to box, grid | 28 | 16,720 (16,176) | 34.88 (5.05) | 0.65 | 26 | 1.71 (0) | -5%..+5% | no | 2.5 min | 119k |
| u_bend | operators: open ring on the divider, O-layer fill, grid, rows; shape step discarded; two hand gradings | 20 | 23,754 (21,766) | 39.58 (2.69) | 0.52 | 34 | 1.88 (0) | exact | no | 8 min | 152k |
| y_junction | operators: open rings on three wall chains, five-sided fill at the junction, fills | 27 | 60,163 (50,358) | 31.95 (8.94) | 0.63 | 33 | 2.50 (0) | -29%..+5% | no | 7.3 min | 155k |
| airfoil_tunnel | operators after the C-grid example: ring `fan=false`, wake rows, rows to the walls; shape step dropped | 430 | 101,666 (89,306) | 31.64 (2.41) | 1.05 | 93 | 2.63 (1) | airfoil -7%..+3%; walls within 2% | no | 8 min | 148k |
| bluff_ground | operators after the C-grid example: ring, wake rows, rows to ground, top and inlet, two fills; shape step skipped; three `size` runs | 357 | 165,264 (168,578) | 38.99 (3.91) | 0.61 | 303 | 2.50 (0) | body -5%..+5%; ground -7%..+3% | no | 15.5 min | 160k |
| cascade | operators: ring `fan=false`, wake rows, stations with offset, three fills; shape step skipped | 78 | 57,974 (39,146) | 56.05 (26.59) | 2.71 | 26 | 2.23 (0) | -5%..+5% | 20 lines, three station fractions | 12 min | 177k |
| blunt_plate | operators after the C-grid example: ring `fan=false`, three-strip wake rows, far field by rows; default chain, no hand grading | 561 | 64,604 (53,044) | 37.82 (4.31) | 0.59 | 893 | 2.52 (2) | -5%..+5% | no | 8.5 min | 129k |
| naca4412 | engine C-grid (1 run, 8 min) + default chain | 250 | 205,506 (139,686) | 45.12 (7.32) | 0.87 | 57 | 3.90 (8) | -5%..+5% | no | 17 min | 151k |
| three_cylinders | engine `--hull` (1 run, 96 s) + shape with `shape_pair_ratio=2.5`, size, polish | 198 | 49,342 (34,911) | 67.94 (21.69) | 3.46 | 18 | 3.71 (9) | -5%..+5% | no | 10.5 min | 138k |
| high_lift | operators only (engine not allowed): three rings with open corners, fills for the coves and slots, wake rows, far field by rows; one rebuild, 14 hand fixes | 1144 | 293,522 (186,653) | 66.19 (13.23) | 1.98 | 941 | **8.22 (87; 22 above 5)** | flap -22%..+116%; main -14%..+98%; slat -5% on its spokes | no | 58 min | 275k |

Twelve of thirteen meet every target: `Mesh OK`, no face above 70 degrees,
skewness under 4, aspect ratio under 1000, no jump above 5. `high_lift`
misses one: 22 interface jumps above 5 (worst 8.22). It is the first
`high_lift` trial to pass checkMesh with no face above 70 (round 1: Mesh OK
with a scripted build of 259 commands; round 2: failed). No trial failed
checkMesh at the end. Total: 161 minutes, 2.00M tokens; the eleven cases
of round 2 took 151 minutes and 1.73M tokens (round 2: 189 minutes, 1.74M).

## Round 2 against round 3, the eleven common cases

One trial per case and round, by different agents: a difference in one row
is a tool effect only where the report ties it to a tool (last column). The
spread section below says how far two agents differ with no tool change.

| case | max non-orth (avg) | worst jump (>2.5) | cells | time (min) | tokens | what changed, and why |
|---|---|---|---|---|---|---|
| nozzle | 11.48 (2.79) -> 12.01 (2.77) | 1.79 -> 1.48 | 14,620 -> 14,878 | 3.5 -> 3.5 | 120k -> 123k | tool: `add_ring side=` on the open walls, 16 commands for 45; the time went to an engine run the route table asked for |
| cylinder_channel | 41.57 (3.10) -> 34.88 (5.05) | 1.88 -> 1.71 | 20,513 -> 16,720 | 3 -> 2.5 | 105k -> 119k | default chain with no hold; fewer cells from the default sizing |
| u_bend | 42.61 (7.03) -> 39.58 (2.69) | 1.50 -> 1.88 | 22,700 -> 23,754 | 5.5 -> 8 | 125k -> 152k | tool: open ring on the divider; the default grading sheared the band blocks (average 16.06) and was repaired by hand |
| y_junction | 39.97 (11.74) -> 31.95 (8.94) | 1.33 -> 2.50 | 40,828 -> 60,163 | 5.5 -> 7.3 | 130k -> 155k | wall bands for the first time (open rings, five-sided fill); first width up to 29% fine |
| airfoil_tunnel | 30.95 (5.52) -> 31.64 (2.41) | 2.50 (0) -> 2.63 (1) | 113,164 -> 101,666 | 10.5 -> 8 | 144k -> 148k | tool and example: 15 commands for 36 |
| bluff_ground | 44.18 (4.72) -> 38.99 (3.91) | 3.52 (6) -> 2.50 (0) | 237,166 -> 165,264 | 14 -> 15.5 | 137k -> 160k | example followed; aspect 452 -> 303; six minutes on a `size` result that changed with the asked width |
| cascade | 60.12 (28.01) -> 56.05 (26.59) | 2.46 -> 2.23 | 53,248 -> 57,974 | 22 -> 12 | 237k -> 177k | tool: `fan=false`, `add_rows`, 22 commands for 52; three topologies tried |
| blunt_plate | 31.45 (7.14) -> 37.82 (4.31) | 2.32 (0) -> 2.52 (2) | 91,581 -> 64,604 | 8 -> 8.5 | 130k -> 129k | tool: the default chain passes with no hand grading (round 2: default failed checkMesh on aspect 1847); aspect 893 is close to the limit |
| naca4412 | 45.12 -> 45.12 | 3.90 (8) -> 3.90 (8) | 205,506 -> 205,506 | 16 -> 17 | 131k -> 151k | identical: same engine build, and the default chain is now round 2's explicit one |
| three_cylinders | 64.66 (17.44) -> 67.94 (21.69) | 4.18 (25) -> 3.71 (9) | 52,553 -> 49,342 | 27 -> 10.5 | 177k -> 138k | skill text: the engine crash and its remedy named, `shape_pair_ratio` named; one engine run for two |
| high_lift | 71.50 (25.76), fails -> 66.19 (13.23), Mesh OK | 16.97 (135; 57) -> 8.22 (87; 22) | 293,053 -> 293,522 | 74 -> 58 | 301k -> 275k | tool: open corners, `add_rows` wakes and far field; faces above 70: 396 -> 0; skewness 4.07 -> 1.98 |
| sum | | | 1,144,932 -> 1,053,393 | 189 -> 151 | 1.74M -> 1.73M | |

## Gaps reported, by case

Each line: the gap, the time the agent gave for it, and where the fix would
sit (tool, skill, engine, protocol). My own checks are marked as such.

### bump_channel (new case)

Corner angles 58.4 .. 120.9. 18 commands, 7 batches, none failed.

1. Route rule 2 sends an internal duct to the engine first; the engine's
   result was admissible, not resolved, and only one of the four bump feet
   was a block corner. Dropped. ~2 min. (skill route, engine)
2. The default shape step collapsed core edges (`min_edge_ratio` 0.228,
   `max_pair_change` 6.07); rerun with `shape_pair_ratio=2.5` from Step 4's
   text. ~1 min. (tool default)
3. No operator pairs stations across a channel (stations at the normals
   from the opposite wall); by hand. ~2 min. (tool)
4. A reference curve's interpolation dips at a corner in the render
   (cosmetic, known).

### triangle (new case)

Corner angles 60.0 .. 136.5. 19 commands, 8 batches, none failed. The agent
checked the wake requirement itself (largest cell 0.049 across the wake);
I did not recheck that number.

1. The fan fronts' clustering is carried to the far field by straight
   `add_rows` columns: thin bands of fine lines to y = +-8 and to the
   inlet, aspect 648. It is the weakness the C-grid example states, copied.
   (tool, example)
2. No command reports the largest cell width in a direction inside a box;
   the local size requirement was checked by parsing `describe --json`.
   (tool)
3. `between=` picks the shortest unassigned chain, so the wall must be
   assigned first; the skill does not say so. (skill)
4. Step 4 (shape) left out of the chain without a stated reason.

### nozzle

Corner angles 76.4 .. 103.6. 16 commands (round 2: 45), 6 batches, none
failed.

1. Route rule 2 sends it to the engine; the result is "resolved" but the
   contraction is one core block with near-vertical lines, which misses the
   brief ("lines near the wall normals"). ~2 min. Second case this round
   (bump_channel 1). (skill route, engine)
2. The digest hides vertex names reused by `size` splits after the
   placeholder block was removed (round 2 cascade 8 again). (tool output)
3. No rule of thumb for the band height in internal flow. (skill)
4. The fill's core lines are vertical, not along the band spokes; the
   polish bends them (kink up to 11.6 degrees at the front). No cost.

### cylinder_channel

Corner angles 50.6 .. 111.0. 13 commands, 5 batches, none failed.

1. The polish moves the first cell width by up to 5% (`band_tolerance`);
   Step 6 does not name the knob. ~1 min. (skill)
2. Renders cluttered by control points; `--no-control-points` is not in
   `TRIAL.md` or the skill. ~1 min. (protocol)
3. The shape step made the square round the cylinder a lopsided octagon
   (accepted by the agent; round 2's agent held the wake rows instead).
4. The wake is coarse: `size` has no way to ask for a wake size.

### u_bend

Corner angles 47.3 .. 92.6. 27 commands, 16 batches, 1 failed, two builds.

1. `add_band to_box` refuses a chain whose end vertex lies on the box edge
   ("vertex v12 is not inside the box"). ~3 min. (tool)
2. Open `add_ring`: the spoke at a line/arc tangent junction is tilted
   0.28 degrees (the mitre averages the first arc segment); fixed with
   `move_vertex x_of`. ~1 min. (tool)
3. No skill rule for concave 90-degree domain corners: the first topology
   had 47,982 cells and 45-degree corners; rebuilt at half the cells.
   ~4 min. Round 2 u_bend 1 again. (skill)
4. **The default `grading=field` sheared long thin rectangles**: the 5 x
   0.15 band blocks at 28 degrees inside, checkMesh average 16.06.
   `grading=strip` gives 2.72 but the first width 28% fine. Fixed by hand
   with two `set_edge_grading ... propagate=true`. ~5 min. (tool)
   **My check** (same build): default (field) 41.87 max, average 16.06,
   first widths exact; `grading=strip` 42.30, average 2.72, first widths
   divider -28%, outer -9%. On `bump_channel`: field 14.14 average, first
   widths exact; strip 12.69, top wall -26%. The field grading is right on
   the spoke strips and wrong on the streamwise strip of long thin blocks;
   the rule that made it the default had no term on the average.
5. The polish moves the first width 5%; `hold` + `keep_counts` + regrade.
   ~3 min. (tool, skill)
6. No station at a point on `add_ring` (`xs=0` crosses twice; fractions by
   a one-liner). ~1 min. (tool)
7. The shape step gave no gain and was discarded.

### y_junction

Corner angles 54.4 .. 124. 27 + 3 commands, 12 batches, 4 failed.

1. `add_ring` takes one curve: the wall chains were joined outside the
   tool. ~2 min. (tool)
2. A first box under a ring: "Blocks sharing edge overlap or are
   misoriented". ~1 min. (message)
3. An open `add_ring` needs a count although its corners give the
   stations. <1 min. (tool)
4. No station relative to a vertex (`xs=` takes numbers only); a vertex
   2e-5 off a side was counted "inside the region" by `add_fill` although
   the side tolerance is a thousandth. ~2 min, two failed applies. (tool)
5. Ring vertex IDs shift with the station count. ~1 min. (tool)
6. No banded-junction example. (skill)
7. First width up to 29% fine with the default grading; the `size` note
   does not say what pulled it down. (tool)

### airfoil_tunnel

Corner angles 55.3 .. 123.6. 13 + 2 commands (round 2: 36), 8 batches, none
failed.

1. The shape step bent the straight rows upstream (`max_pair_change`
   6.81); dropped. ~2 min. (tool, skill)
2. A dense stripe of thin cells from the trailing edge to both walls, and
   the chordwise spacing carried in straight columns: the example's
   weakness. (tool, example)
3. The wall's clustering is carried along the wake band to the outlet (one
   series per strip). (tool)
4. `render --no-labels` does not exist; `smooth` ran past the default
   120 s command timeout. 0.5 min. (tool, protocol)

### bluff_ground

Corner angles 49.8 .. 129.8. 19 commands, 14 batches, none failed.

1. **`size` is unstable to the asked first width**: at 5e-4, 202,218
   cells and the ground 31% fine ("the verification at 256 cells a side
   found the pass worse than the spent state": the field pass was dropped
   as a whole); at 4.9e-4 on the ground, 165,264 cells and the ground
   exact. ~6 min, three `size` runs. The result has fewer cells than its
   own `field_cells`, unexplained. (tool)
2. A hand regrade following `sizing.md` moved the jump elsewhere (4.36).
   ~3 min. (skill, tool)
3. The default polish bent the base-corner lines (y 0.35 -> 0.340);
   `hold=` with 20 vertex IDs. ~2 min. Wants hold by line or pattern.
   (tool)
4. The ground's columns run to the top: 1226 faces on the top patch,
   aspect 303. No way to end a band's columns. (tool)
5. The shape step was skipped (`max_pair_change` 6.93).
6. Thin strips from the fan tips to the outlet; `spread=` is symmetric
   only. (tool)

### cascade

Corner angles 34.0 .. 142.7. 22 commands (round 2: 52), 17 batches, none
failed, three topologies.

1. The first topology (a stagnation strip by `add_rows` leaving the curved
   front near-tangentially) had 14 faces above 70 at the nose. ~6 min.
   No upstream recipe for a cascade. (skill)
2. The shape step did harm on both topologies (`min_edge_ratio` at the
   floor, `max_pair_change` 19.6; `shape_length_ratio=0.4` from the skill
   still twisted); skipped. ~3 min. (tool)
3. No per-side station counts on `add_ring`; `normals=255` is ambiguous
   (the normal occurs twice) and cannot be restricted to a section.
   ~4 min and the helper script. (tool)
4. The periodic stations' x values were typed from `describe` (the mean of
   two vertices' x). ~2 min. (tool)
5. A shallow corner where the nose column met the periodic chain; rebuilt.
   ~3 min. (agent's choice)
6. `render --no-labels` missing (second trial). (tool)
7. `size` will not split a strip holding the cyclic edge (known, no harm).

### blunt_plate

Corner angles 52.4 .. 127.5. 19 commands, 8 batches, none failed.

1. `describe --bbox` wants four arguments where commands take
   `bbox=a,b,c,d`. ~1 min. (tool)
2. The far-field columns above the base corners carry the base's first
   streamwise cell to y = +-6: aspect 893, little margin. The example's
   weakness, fourth trial. (tool, example)
3. `quality` prints the corner screen's "max cell aspect ratio 1105"
   beside the verdict's 893.3: it reads as a checkMesh failure. 0.5 min.
   (tool output)
4. Control points are drawn in the outlet patch's colour (round 2 cascade
   7 again). (render)
5. The shape step tilted the corner wake lines (accepted).
6. No route row for a blunt base in an external rectangle. (skill)
7. Three two-ended strips were not split (sliver refusals; information).

### naca4412

Corner angles 53.0 .. 125.9. Engine plus 3 commands, none failed.

1. No sanctioned way to wait for an 8-minute engine run: the skill says
   background and poll, the trial rules forbid the poll, a foreground call
   is capped at 10 minutes. ~3 min and 8 idle. Round 2 naca4412 1 again.
   (protocol, skill)
2. `engine.md` does not say the C-grid accepts four `--outer` chains
   (found by reading `cgrid.py`). ~3 min. (skill)
3. The shape step: `min_edge_ratio` 0.157, `max_pair_change` 15.7; worst
   jump 5.14 after `size`, 3.90 after the polish.
4. 205k cells: a far-field column carries the trailing edge's streamwise
   spacing to y = +-15 (round 2 naca4412 6; batch 5 could not cut it).

### three_cylinders

Corner angles 21.7 .. 157.8. Engine plus 3 commands, 6 batches, none
failed.

1. The far-field fan strips kink at the hull and leave sliver strips
   (skewness 3.46, worst face 67.9). Round 2 three_cylinders 4 again.
   (engine)
2. Step 4 gives `shape_pair_ratio` for jumps but not the skewness trade:
   default 64.7 / 3.835 / 19 jumps, pair 2.5 67.94 / 3.459 / 10. ~3 min.
   (skill)
3. The polish moves the first width 5% (fourth report). (tool, skill)
4. `smooth slide=true` raised the skewness 3.46 -> 3.73; dropped.
5. `quality`'s text verdict does not name the block of the maximum
   skewness (found by a script over `--json`). ~1 min. (tool output)
6. No band height in the brief and no rule of thumb. (skill)

### high_lift

Corner angles 23.5 .. 156.1. 37 build commands, then shape
(`shape_pair_ratio=2.5`), `size 1e-4 0.5 growth=1.15 wall_aspect=25`, 14
hand commands, polish. About 40 applies, about 12 failed, one full rebuild.
The slat's 9.43e-4 "first width" is on the line from the open cusp across
the cove fill, not a band spoke.

1. **Fan fronts carried to the far field break the aspect limit**: a
   convex-corner fan's front edges take the spoke's wall-normal count, and
   `add_rows ... to_y=-15` carried them as columns 0.0006 wide with 9
   cells across; the first `size` left 3593 cells above 1000 (worst 8071).
   Fixed by opening the corners (a mitred spoke, an open cusp inside a
   fill) and rebuilding. ~15 min. (tool: warn; skill: fan fronts must not
   head far-field columns)
2. `add_fill` read a side through a wall vertex as open and silently made
   a vertex 0.0003 from the existing one; fixed by `side1=`. ~3 min.
   (tool: warn when a new station falls beside an existing vertex)
3. A mitre at a chosen corner takes remove, `move_vertex`,
   `add_block_from_vertices`; removing a block also removed its wall edge.
   2 min. (tool: a mitre option on `add_ring`)
4. The cove fill against the cusp fan: three failed fills; solved by
   opening the cusp, at the cost of a jump of 7.6 there. ~6 min.
5. `add_rows` refuses the target as a station. 1 min. (tool)
6. Stations matched by hand across long fills (the 35 x 1 slot fill). ~5
   min. (tool: stations from an existing chain)
7. One batch took 1 min 43 s at about 1150 blocks (corner fills and
   `set_boundary_by bbox=`). Not diagnosed. (performance)
8. `size` left aspect ratios above 1000 in thin far-field columns ("no
   repair move lowered them"); fixed by hand with uniform, finer outer
   rows: 2188 -> 930, 18k cells. ~6 min. (tool)
9. The jumps above 5 did not respond to a larger budget (450,000, not
   binding): they are topological - a fan or open corner beside a band,
   a slot channel one block thick. The skill's budget advice did not
   help. 5 min. (skill)
10. A band over the main upper surface starting at the slot exit would
    fold at the concave mitre; avoided by starting it further on and
    bridging with a 2 x 2 fill.

## Recurring gaps, round 3

A gap counts as recurring when two unrelated cases report it.

| # | gap | cases | where |
|---|---|---|---|
| 1 | Straight far-field columns from `add_rows` carry a fan's, a trailing edge's or a base's fine spacing to the outer boundary: aspect 300-900 with little margin, thin stripes of cells, and on `high_lift` 3593 cells above 1000 and a rebuild. The C-grid example has this weakness and says so; it is copied with the pattern. | triangle, airfoil_tunnel, bluff_ground, blunt_plate, high_lift (naca4412 by the engine) | operator, example |
| 2 | The shape step is skipped, discarded or run only with `shape_pair_ratio=2.5`: it bends rows meant to stay straight and collapses core edges (`max_pair_change` 6 to 20). | skipped: triangle, u_bend, airfoil_tunnel, bluff_ground, cascade; limited: bump_channel, three_cylinders, high_lift | tool default, skill |
| 3 | The default `grading=field` shears long thin blocks (average non-orthogonality 16 for 2.7), while `grading=strip` loses the first width; nothing in between. | u_bend, bump_channel (my check) | tool |
| 4 | `size`'s field pass is dropped as a whole, so the result changes with a 2% change of the asked width; first widths 23-31% fine with no stated cause. | bluff_ground, y_junction | tool |
| 5 | The polish moves the first width up to 5% and bends lines meant to stay straight; `hold=` takes vertex IDs only. | cylinder_channel, u_bend, three_cylinders, bluff_ground, cascade | tool, skill |
| 6 | Route rule 2 sends a simple internal duct to the engine; the result misses the brief. | bump_channel, nozzle | skill |
| 7 | Stations cannot be placed relative to what exists: at a vertex's x, at a point, at an existing chain's vertices, at the opposite wall's normals. | y_junction, cascade, u_bend, bump_channel, high_lift | operator |
| 8 | `add_fill` near an existing vertex: a vertex just off a side counted inside the region, a side through a wall vertex read as open with a new vertex beside the old one. | y_junction, high_lift | operator |
| 9 | Jumps that are topological (a fan or open corner beside a band, a channel one block thick) do not respond to the budget; the skill offers only the budget. | high_lift, three_cylinders (engine slivers) | skill, operator |
| 10 | Render and report details: `--no-labels` missing, `--no-control-points` not named, control points in a patch colour, the corner screen's aspect ratio printed beside the verdict's, the verdict not naming the block of the maximum skewness, `describe --bbox` form, no report of the largest cell in a box. | airfoil_tunnel, cascade, cylinder_channel, blunt_plate, three_cylinders, triangle | tool output, protocol |
| 11 | Skill rows missing: band height rule of thumb, concave domain corners, banded junction, cascade upstream, blunt base in a rectangle, waiting for a long engine run, `--outer` chains for the C-grid. | nozzle, three_cylinders, u_bend, y_junction, cascade, blunt_plate, naca4412 | skill |

## Spread between agents: the four cheap cases, three trials each

Same code, same protocol, three fresh agents per case (`trial3`,
`trial3b`, `trial3c`). All twelve trials meet every target. checkMesh
numbers from my reruns.

| case | trial | topology | blocks | cells | max non-orth (avg) | skew | worst jump (>2.5) | first width | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| nozzle | 3 | two open rings, one fill | 56 | 14,878 | 12.01 (2.77) | 0.41 | 1.48 | -5%..+5% | 3.5 | 123k |
| | 3b | the same, 15 stations | 60 | 14,964 | 11.45 (2.73) | 0.34 | 1.48 | -5%..+5% | 3.5 | 125k |
| | 3c | the same, 21 stations | 84 | 15,480 | 11.75 (2.52) | 0.52 | 1.52 | -5%..+5% | 3 | 119k |
| cylinder_channel | 3 | ring, band to box, grid | 28 | 16,720 | 34.88 (5.05) | 0.65 | 1.71 | -5%..+5% | 2.5 | 119k |
| | 3b | the same (identical mesh from a batch written independently) | 28 | 16,720 | 34.88 (5.05) | 0.65 | 1.71 | -5%..+5% | 3.5 | 125k |
| | 3c | the same, band 0.01, one more column | 32 | 16,715 | 35.01 (3.27) | 0.55 | 1.50 | -5%..+5% | 3 | 119k |
| u_bend | 3 | divider ring, O-layer to a half square, Cartesian corner blocks; hand grading | 20 | 23,754 | 39.58 (2.69) | 0.52 | 1.88 | exact | 8 | 152k |
| | 3b | divider ring, diagonals from the tip band into the concave corners | 16 | 79,462 | 44.97 (24.28) | 1.01 | 2.13 | -5%..+5% | 6 | 132k |
| | 3c | rings on both walls, corners mitred, box round the tip | 34 | 39,822 | 44.98 (16.75) | 1.05 | 2.08 | -5%..+5% | 13 | 170k |
| y_junction | 3 | three open rings, open corner, five-sided fill | 27 | 60,163 | 31.95 (8.94) | 0.63 | 2.50 | -29%..+5% | 7.3 | 155k |
| | 3b | two rings and a bottom band, fan at the corner, hand blocks and two fills | 32 | 88,124 | 29.14 (15.23) | 0.72 | 2.50 | -6%..+5% | 9 | 158k |
| | 3c | three open rings, open corner, rectangle and four fills; one `split_edge`, `budget=110000` | 24 | 74,514 | 31.66 (14.63) | 1.15 | 4.23 (3) | -5%..+5% | 11 | 147k |

- **Where the skill carries a pattern the spread is small.** `nozzle` and
  `cylinder_channel`: three agents, one construction each, cells within
  4%, maximum within 0.6 degrees, time within a minute. Two agents wrote
  different batches for `cylinder_channel` and got the same mesh to the
  last digit.
- **Where it does not, the agent's topology decides.** `u_bend`: three
  topologies, cells 1 : 3.3 : 1.7, average non-orthogonality 2.7 / 24.3 /
  16.8. `y_junction`: three junction cores, cells 1 : 1.46 : 1.24. Every
  one passes the targets, which have no term on cells or on the average.
- A difference between rounds on `u_bend` or `y_junction` below a factor
  of about 1.5 in cells or 10 degrees in the average is inside this
  spread and says nothing about a tool change. On `nozzle` and
  `cylinder_channel` 5% of cells or a degree is already a signal.
- **My check of the averages** (same builds with `grading=strip`):
  `u_bend` 3c 16.75 -> 15.77, so that average is the topology (45-degree
  corners); `y_junction` 3b 15.23 -> 11.02 and 3c 14.63 -> 10.33 with the
  first widths 18-23% fine. The default grading costs 1 to 4 degrees of
  average on ordinary blocks and 13 to 15 on long thin rectangles
  (`u_bend` 3, `cylinder_channel` 3b with held vertices: 18.45 for 3.09),
  and buys the first width.

Gaps the repeats added or confirmed (each line: trials, time, where):

1. Route rule 2 sends a duct to the engine, whose result misses the
   brief: `nozzle` 3, 3b, 3c and `bump_channel` (four reports, about 2 min
   each). `u_bend` 3c: rule 2 against `engine.md`'s refusal of L-shaped
   channels is ambiguous. (skill)
2. An open ring's default end spoke is normal to the first wall segment,
   not along the inlet line: a vertex 4e-4 outside the domain, a
   zero-width `bbox` missing it (`nozzle` 3b, 3c; 1 min each). (tool)
3. An open `add_ring` refused with corner stations only (`y_junction` 3,
   3b, 3c) and one curve per ring, so chains joined by hand (`y_junction`
   3, 3b, 3c). (tool)
4. Stations on an open ring where `xs=` crosses twice, at tangent points,
   opposite a vertex (`u_bend` 3, 3b, 3c; `y_junction` 3b): fractions by
   script each time. (tool)
5. The band front at a line/arc tangent joint is 0.0013 to 0.0026 off the
   joint's x (the mitre averages the first arc segment): a placeholder box
   not reused, a grid cutting the front (`u_bend` 3, 3b, 3c). (tool)
6. Near-coincident vertices: six-digit fractions left a station 4e-6 off
   and broke vertex reuse (`u_bend` 3b; with `y_junction` 3 and
   `high_lift` 2, three cases). (tool)
7. A concave corner's clustering runs down a diagonal into the convex
   tip: tangential cells of 3.8e-5 beside a first width of 0.002, passed
   by checkMesh and by every target; one agent rebuilt (`u_bend` 3c, 8
   min), one delivered it (`u_bend` 3b, the 79,462 cells). (skill: concave
   corners; tool: no floor on how fine a regrade may go, no warning)
8. `size` drops the field pass as a whole after a small change:
   `cylinder_channel` 3c with two held vertices (cylinder 23-30% fine),
   with `bluff_ground`. An open corner ties a band's rows to a wall's
   along-wall count: first `size` 28% fine and a jump of 7.18
   (`y_junction` 3c; probably `y_junction` 3's unexplained 29%). A larger
   budget gave fewer cells and no jump above 5 (`y_junction` 3c). The note
   names none of this. (tool)
9. The shape step moved a wake-line vertex and left chevrons in the cross
   lines (`cylinder_channel` 3, 3b, 3c); holding the line costs the first
   width or the average. Wants a hold on one coordinate or by line. (tool)
10. Notes list a front or a far row out of the order given (`nozzle` 3b,
    `u_bend` 3b); control points clutter the renders in a patch colour
    (`nozzle` 3b, `cylinder_channel` 3, 3c); `describe --section
    component` refused. (tool output)

## Reading

- **The construction operators of batch 4 did what round 2 asked.** Every
  new operator was used on the first try wherever it applied. Commands
  fell (nozzle 45 -> 16, cascade 52 -> 22, airfoil_tunnel 36 -> 15), no
  trial wrote a vertex script, `high_lift` passes checkMesh for the first
  time without one, and the eleven common cases took 151 minutes for 189
  with 8% fewer cells.
- **The quality limits left are of three kinds.** Engine far fields
  (`three_cylinders` 67.9 degrees, `naca4412` 205k cells); topological
  jumps on `high_lift`; and choices of topology the skill does not guide
  (concave corners, junction cores).
- **The clearest tool gap is the far field built from `add_rows`**
  (recurring gap 1): five hand builds carry near-wall spacing to the outer
  boundary in straight columns. It cost `high_lift` a rebuild and leaves
  three cases within 6 to 35% of the aspect limit (941, 893, 648). The C-grid example
  teaches the pattern with the weakness in it.
- **The default chain is not yet a default.** The shape step is skipped or
  limited in eight of thirteen first trials; the default grading shears
  long thin blocks; the field pass is dropped whole on small changes; the
  polish moves the first width 5%. Each was repaired by the agent in one
  to six minutes, each time differently.
- **The targets are too weak to rank results.** `u_bend` 3b passes with
  3.3 times the cells and nine times the average non-orthogonality of
  `u_bend` 3. From round 4 on the ledger should judge cells against the
  case's best known result and the average non-orthogonality as well, and
  `TRIAL.md` should ask the agent to report both against a stated
  reference.
- **What a round can show.** One trial per case separates tool effects
  from agent choice only on cases the skill already carries a pattern
  for. The cheap repeats cost 52 minutes and 1.1M tokens; they are worth
  keeping for the cases without a pattern.

Totals: 21 trials, 213 minutes, 3.09M tokens.

## Candidates for the next fix batch

By the rule (two unrelated cases, or a general primitive), in the order I
would take them:

1. Skill text, no code: concave domain corners (no spoke from a concave
   corner to a convex tip); route rule 2 for ducts whose brief asks for
   normal lines; fan fronts must not head far-field columns; the junction
   row with a measured layout; `band_tolerance` in Step 6; band height
   rule of thumb; waiting for an engine run. Protocol: cells and average
   in the targets, `--no-control-points`.
2. The far field from `add_rows`: a way to end or merge a band's columns
   (or a fan-out far field), and the C-grid example rebuilt on it. A
   warning when a chain edge of a fan's count component heads a column.
3. Sizing: the field pass dropped per strip, not whole; the field
   grading taken only where it buys a first width or a jump, under a rule
   with a term on the average non-orthogonality; the note naming what
   pulled a first width down and which components were raised.
4. The shape step and the polish: hold by line or by one coordinate;
   whether `shape_pair_ratio=2.5` should be the default, measured on the
   corpus under a rule stated first.
5. Open rings: corner stations alone, several curves, the end spoke along
   the boundary chain it meets, stations where `xs=` crosses twice or
   opposite a vertex, the mitre at a tangent joint.
6. `add_fill` and stations beside an existing vertex: snap or warn.
7. Output: front and row order in notes, `render` flags, the corner
   screen's aspect ratio beside the verdict's.

## Fixes after round 3

### Batch 6: skill text, open rings and stations, notes (commits 142480b .. 01a5d45)

Nine local commits on `review-followups` by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round3a/BRIEF.md`; `size.py` and `smooth.py`
untouched.

- **Skill** (01a5d45; `SKILL.md` still 250 lines): no block line from a
  concave domain corner to a convex wall feature (with the `failures.md`
  signature and the U-bend's 79,462 against 23,754 cells); route rule 1
  takes a turn round a divider tip and a duct whose brief asks for
  wall-normal lines, with a measured nozzle row and a banded junction row
  in the table; the engine run in the foreground; fan fronts do not head
  far-field columns; `band_tolerance` and `size ... keep_counts=true` after
  the polish for an exact first width; jumps the budget cannot move; the
  open corner's count coupling; `shape_pair_ratio`'s trade; a band height
  for internal flow stated as what was used (5 to 20 percent of the width),
  not as an optimum; `engine.md`: the C-grid takes `--outer` chains.
- **Open rings**: built from ends and corners alone (499115b); along
  several curves joined end to start, `add_ring A,B H side=left`
  (cfe4cbc); stations at `points=` - coordinates or vertex IDs, the
  nearest curve point, refused when not unique - on `add_ring` and
  `add_stations` (abb5433); an end spoke runs along another reference
  curve ending at the wall's end when within 60 degrees of the normal, and
  the note says where each end spoke ends (cdd27d3).
- **Other operators**: `add_rows` ignores a station on its target and its
  note names the thinnest column, always (dcde48c); `add_band to_box`
  takes an open chain's end on the box's edge, its spoke along that edge
  (142480b); every operator's note names a new vertex closer than 1.5e-5
  of the extent to another vertex and not reused (f5f4170).
- **Output** (76f353a): `quality` labels the corner screen and names the
  block of the maximum skewness; `render --no-labels`; control points grey;
  `describe --bbox a,b,c,d`; `--section component`.

My checks (`/tmp/garbage/blockdrawer-bench/verify-batch7`):

- Main suite 637 tests OK.
- All 19 hand builds of round 3 replayed from `bd new` through their whole
  chain: every one gives the delivered cell count.
- Nozzle: rings with no count and no `start`/`end` end on the inlet and
  outlet lines (bounds x -1 .. 3). Y-junction: `add_ring
  top_downstream,branch_right` and `branch_left,top_upstream` from the
  supplied files, the joint a station, the wrong order refused by name.
  U-bend: five stations by `points=` at the tangent points and the tip;
  `xs=0` refused with a pointer to `points=`; a point equally near both
  passes refused naming both.
- **The new Step 6 advice, measured** (the developer had not): `size ...
  keep_counts=true` on four delivered sessions after their polish.

  | session | first width before -> after | max (avg) | skew | worst jump (>2.5) |
  |---|---|---|---|---|
  | cylinder_channel 3 | -5%..+5% -> exact | 34.88 (5.05) -> 34.70 (5.05) | 0.65 -> 0.67 | 1.71 -> 1.75 |
  | nozzle 3 | -5%..+5% -> exact | 12.01 (2.77) -> 12.24 (2.71) | 0.41 -> 0.41 | 1.48 -> 1.34 |
  | y_junction 3b | -6%..+5% -> exact but one spoke -6% | 29.14 (15.23) -> 28.66 (13.68) | 0.72 -> 0.80 | 2.50 -> 2.34 |
  | three_cylinders 3 | -5%..+5% -> exact | 67.94 (21.69) -> 67.80 (21.16) | 3.46 -> **3.84** | 3.71 (9) -> 2.64 (2) |

  The advice holds; the cost seen is skewness on the engine session (3.46
  -> 3.84, limit 4). Whether this step belongs at the end of the default
  chain is a question for the next batch, under a rule stated first.

Limits I accept: the near-vertex fraction sits between the closest
legitimate pair of the replayed builds (3.6e-5) and the largest defect
(8.2e-6), a factor of about two either way, and it is a note, not a
refusal; `y_junction`'s vertex 2e-5 off a fill side was not reproduced;
the GUI and the web client still draw control points purple.

### Batch 7: the field grading (commits 299bda9, e8cdd01, 9a279a8; the sizing change not accepted)

One Opus developer (the one of the sizing batches, resumed) from
`/tmp/garbage/blockdrawer-dev/round3b/BRIEF.md`. The rules were fixed in
the brief before any measurement, on 39 inputs under the default chain:
the 20-session corpus and the construction part of the 19 round-3 hand
builds with each trial's own `size` inputs.

**What shears the long thin blocks.** Two rules of the field pass
together: a strip's members may take their own series up to 20 degrees of
tilt against their neighbours (`SHEAR_ANGLE`), and `settle` accepts one
strip at a time against a ceiling of min(20, the spent state's worst
face), which on `u_bend` is the full 20 because the worst face is 40
degrees elsewhere. Each strip passes alone; with both pairs of sides of
the same blocks graded apart the transfinite map tilts every cell between
them (sampled worst face in the band blocks 1.7 -> 27.6 degrees). The
strips along the band's long sides hold no wall spoke and no jump depends
on them: they buy nothing.

**Items 1 and 2 (the pass undone per strip; no shear that buys nothing):
measured, not committed.** The formulation kept for the full run: the
pass is undone through the offending blocks' strips first; a strip's move
is rejected when it raises its own blocks' worst face by more than 0.25
degrees and buys neither a first height nor a jump; a three-quarter step
added to the partial moves.

| term of the rule | items 1+2 | item 1 alone |
|---|---|---|
| faces above 70, aspect above 1000, checkMesh | pass | pass |
| no session gains a jump above 5 | **fail**: triangle 9 -> 11, high_lift 3 37 -> 39 | pass |
| corpus jumps above 2.5 do not rise | **fail**: 303 -> 307 | pass |
| no maximum +2 degrees | **fail**: y_junction 3c 32.39 -> 34.49, cascade 3 53.61 -> 55.75, triangle 37.70 -> 39.81 | pass |
| no average +0.5 degrees | **fail**: eng_square_cylinder 9.82 -> 10.81, y_junction 3 8.94 -> 12.37, r2 high_lift 24.70 -> 25.25 | **fail**: y_junction 3 8.94 -> 13.62 |
| the mean of the 39 averages falls | pass, 10.25 -> 9.46 | **fail**, 10.25 -> 10.37 |
| first heights (no patch +5 points, mean not +1) | pass, mean 14.44 -> 13.25 | pass, 14.44 -> 13.48 |
| cells +5% | pass (equal everywhere) | pass |
| u_bend 3: average half way to strip's | pass, 15.84 -> 2.83 (strip 2.64), first widths +-5% | 15.84 |
| cylinder_channel 3b with holds | pass, 18.45 -> 2.75 | not reached |
| cylinder_channel 3c with holds: first widths within 10% | pass, 33.5% -> 5% | pass (average 3.18 -> 9.32) |
| bluff_ground at 5e-4: first widths within 10% | pass, 13.9% -> 5% | fail |

It fails the rule and the condition for an option (six sessions break one
of the first four terms), so nothing went in. Two of the failures are the
rule against itself: where HEAD drops the pass (y_junction 3,
eng_square_cylinder) keeping it buys the first width (29% -> 5% fine) and
costs the average. Three maxima are 2.1 degrees up against a limit of 2,
with the same maximum as HEAD before the polish on two of them.

My reruns (HEAD and the patched tree, `verify-batch7/b7chain.sh`): u_bend
3 15.84 -> 2.83 at 37.64; y_junction 3 8.94 -> 12.37 with every first
width within 5%; y_junction 3c 32.39 -> 34.49, average 17.28 -> 11.48,
jumps above 2.5 2 -> 4; cascade 3 53.61 -> 55.75, jumps 8/3 -> 2/0, and
**skewness 2.84 -> 3.66**, which the developer's table does not carry
(the rule had no skewness term). All of the developer's numbers I reran
came out the same.

The patch is frozen at
`/tmp/timofey/data/blockdrawer/benchmark/patches/field_shear_e8cdd01.diff`
(applies to `e8cdd01`; `size.py`, `quality.py`, `commands.py`, tests).

**Rule for one confirmation on new inputs, fixed now.** The 39 inputs
chose the formulation, so they cannot confirm it. After round 4 the
frozen patch is measured against the code of that day on the
construction part of every round-4 hand build under the default chain
(the trial's own `size` inputs). It becomes the default when every term
of batch 7's rule holds on those builds, with two changes stated here: a
session whose worst first-height deviation the patch brings from above
10 percent to within 10 may gain up to 5 degrees of average; and no
session's skewness crosses 3.5 from below. If it fails, it is dropped.

**Item 3 (`size ... keep_counts=true` as a fourth step): fails its rule;
the advice stays, with its cost** (e8cdd01). On the 39 finals: faces
above 70 on both high-lifts (0 -> 6, 1391 -> 1488), jumps above 5 37 ->
40 on high_lift 3, seven maxima up by 1 to 32 degrees (triangle 37.70 ->
69.93, my rerun the same; high_lift 3 66.24 -> 80.33), corpus jumps above
2.5 303 -> 364, mean worst first-height deviation 14.44 -> 12.48 (not
halved; y_junction 3 29 -> 53%, blunt_plate 3 5 -> 21%). Skewness passed
(three cylinders 3.96 -> 3.29). The developer's rows for my four sessions
of batch 6 equal mine. Step 6 now says `check` after it.

**Item 4 (the note; 299bda9).** `raised` (the three components spending
raised most, "EDGE FROM->TO +CELLS"), `field_cells_after_splits`, a
sentence when continuity splits changed the cells after spending, and
the wall patches left more than 10% off when the pass was dropped or
strips refused. `bluff_ground`'s `total_cells` below `field_cells` is not
a counting defect: one continuity split re-solved the field (162,448
against 168,578) and spending started again. On my reruns: y_junction 3
"the field pass was dropped ...: branch_left 0.00142..0.00149 for 0.002
(-29%..-25%)" and three more walls; y_junction 3c "continuity splits of
b_6-r_4 took the cells from 109,492 after spending to 108,559 ... a
larger budget can end with fewer cells". Two sentences of one note are
joined without a separator (cosmetic, open).

**Skill text (9a279a8, mine).** `sizing.md` said rectangles are not
sheared; it now says the field pass can shear long thin blocks, with the
U-bend's numbers (15.8 against 2.6 with `grading=strip`, strip leaving
the first width up to 19% fine in my run) and when to compare; a
`failures.md` signature for it.

My checks: main suite 640 tests OK at e8cdd01; `SKILL.md` 250 lines; the
code diff of 299bda9 adds result fields and note text only (no sizing
decision reads them); all 19 hand builds of round 3 replayed through their
whole chains at 9a279a8 give the delivered cell counts
(`verify-batch7/replay2.out`).

Left open by this batch: the shear itself (the default is unchanged and
the agent's remedy is `grading=strip` or hand grading); the polish moves
maxima by about 2 degrees for small changes of its input, which makes a
"+2 degrees on no session" term on 39 sessions close to a coin toss for
any sizing change - a later rule should measure that noise first.

### Batch 8: the far field carried from a band (commits 39cf2f5, 589c2cc, f41cb5f, a7f278a)

Four local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round3c/BRIEF.md`; only `construct.py` and
`commands.py` changed in the package.

- **The primitive** (39cf2f5): `add_rows ... far_ratio=R` gives the far row
  its own distribution. Between pinned columns (the chain's two ends, a
  column holding a `conform` vertex) each far-row width is raised to the
  widest's over R per column between them and the stretch scaled back to
  its length; every point's displacement along the target line grows with
  its travel, so column lines stay straight and two carried chains still
  meet at their ends. `spread_about=V` widens about a chain vertex (a wall
  on one side). The note gives the far row's narrowest and widest column
  and both end vertices with coordinates. Off by default. Chosen on the
  C-grid example against a blend towards even columns (29 and 44 percent
  more cells at 1e-4) and against R = 1.2 (8 faces above 70) and 1.5 (21
  percent more cells at 5e-4).
- **The C-grid example rebuilt** (589c2cc), rule fixed in the brief, every
  term held:

  | first width | | cells | max | faces >70 | skew | aspect |
  |---|---|---|---|---|---|---|
  | 5e-4 | old | 14,393 | 38.07 | 0 | 0.57 | 308 |
  | | `far_ratio=2` | 14,469 | 37.74 | 0 | 0.61 | 111 |
  | 1e-4 | old | 41,152 | 76.15 | 11 | 0.89 | 978 |
  | | `far_ratio=2` | 38,300 | 55.30 | 0 | 0.69 | 376 |

- **The five round-3 builds** with `far_ratio=2` on their far-field
  `add_rows` lines only (triangle lines 5-8, airfoil_tunnel 8, 13, 14,
  bluff_ground 8, 12, 13, blunt_plate 10, 13, 14, high_lift 24, 32, 33, 34;
  high_lift without its hand repairs in both), each through its own chain:

  | case | | cells | max (avg) | skew | aspect (cells >1000) | jumps >2.5 / >5 | worst first width | checkMesh |
  |---|---|---|---|---|---|---|---|---|
  | triangle | old | 31,708 | 30.04 (12.29) | 0.86 | 648 | 0 / 0 | 4.3% | OK |
  | | new | 32,368 | 30.64 (12.52) | 1.19 | 518 | 0 / 0 | 23.9% | OK |
  | airfoil_tunnel | old | 101,666 | 31.64 (2.41) | 1.04 | 93 | 1 / 0 | 7.2% | OK |
  | | new | 101,422 | 30.00 (3.34) | 0.85 | 52 | 3 / 0 | 10.4% | OK |
  | bluff_ground | old | 165,264 | 38.99 (3.91) | 0.61 | 303 | 0 / 0 | 5.3% | OK |
  | | new | 175,854 | 40.42 (4.35) | 0.65 | 87 | 2 / 1 | 23.2% | OK |
  | blunt_plate | old | 64,604 | 37.82 (4.31) | 0.59 | 893 | 2 / 0 | 5.0% | OK |
  | | new | 61,155 | 37.90 (4.46) | 0.50 | 1000 | 0 / 0 | 5.0% | OK |
  | high_lift | old | 275,760 | 66.24 (13.95) | 2.00 | 3873 (1738) | 67 / 17 | slat cusp | fails (aspect) |
  | | new | 279,240 | 66.27 (14.50) | 1.84 | 950 (0) | 63 / 19 | slat cusp | OK |

  **The rule for Step 3 to recommend it fails**: the aspect falls 30
  percent or more on three of five (four needed; triangle -20%,
  blunt_plate +12%), bluff_ground gains a jump above 5 and 6.4 percent of
  cells, high_lift two jumps above 5. No new face above 70, no maximum
  +2 degrees, no new checkMesh failure (high_lift passes without its hand
  repairs). So the option is documented with these numbers
  (`failures.md`, Step 3 "mixed on five builds") and not recommended.
- **What it cannot reach**, and why: a chain whose columns are all thin
  (triangle's inlet rows from the apex fan front: a ratio cap moves
  nothing; `spread` is the tool there), and a column of ordinary width
  holding many cells (blunt_plate above the base corners: 0.04 wide, 24
  cells - a count tie, not a width). Ending columns before the outer
  boundary (bluff_ground's 1226 top faces) needs a count change: not
  built, no pattern measured.
- The first width going 4-5% -> 23-24% on triangle and bluff_ground with
  the option is `size`'s (the note of batch 7 says so: "6 strip(s) kept
  their shared ratio (refused by the jump or grid guard)"); two more
  inputs for gap 4.
- **Note order** (a7f278a): `add_rows` lists the far row in the order the
  chain was given.

My checks (`/tmp/garbage/blockdrawer-bench/verify-batch8`, own scripts
through the CLI): main suite 645 tests OK; `SKILL.md` 250 lines; no change
to `size.py` or `smooth.py`; all 19 hand builds of round 3 replayed through
their whole chains give the delivered cell counts (`all.out`); every row of
both tables above reproduced to the digits shown (the C-grid example built
from the committed file at both first widths; the five builds old and new);
no process left.

Left open: the far field of triangle and blunt_plate (spread, or a
topology with fewer columns); ending columns early; `far_ratio` with other
values than 2 on the five; `spread_about` on a real build.

### Batch 9: the shape step and the holds, on hand builds (commits 13b6ce7, 69aca86, 657cef2)

Three local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round3d/BRIEF.md`; in the package only
`smooth.py`, `commands.py` and four separator tokens in `size.py` changed.
Inputs: the construction part of the 19 round-3 hand builds with each
trial's own `size` line (`round3d/inputs`).

- **The chain's noise** (default chain, core size times 0.99, 1, 1.01; the
  band is largest minus smallest of the three). Eight of 19 sessions do
  not move at all. Over the 19:

  | | max | average | skewness | aspect | cells | jumps >2.5 | jumps >5 | worst first width |
  |---|---|---|---|---|---|---|---|---|
  | median band | 0.00 | 0.01 | 0.00 | 0.0 | 173 | 0 | 0 | 0.0 points |
  | largest band | 1.53 (triangle) | 1.01 (cascade) | 0.18 | 262 (high_lift) | 7,890 | 6 | 5 (cascade, triangle) | 12.7 points (cascade) |

  So a per-session term on the maximum needs at least 1.5 degrees of
  room, and the count of jumps above 5 moves by up to 5 on one session
  (cascade 5 / 3 / 0, triangle's worst jump 6.6 / 20.1 / 18.3) for a one
  percent change of the core size: a "no session gains a jump above 5"
  term is inside the noise on the sessions that have any.
- **The shape step on operator builds**: D (the default step), N (no
  step), L (`keep_lines=true`), P (`shape_pair_ratio=2.5`), each followed
  by the trial's `size` line and the default polish. The per-session table
  is in the README (`smooth`, "19 hand builds"). The rule fixed in the
  brief, against D:

  | term | N | L | P |
  |---|---|---|---|
  | checkMesh failures gained | blunt_plate (20 cells above aspect 1000) | blunt_plate (20) | none |
  | faces above 70 gained | none | none | none |
  | jumps above 5 gained | y_junction 3b (0 -> 1) | none | none |
  | cells above aspect 1000 gained | blunt_plate, high_lift 1695 -> 3016 | blunt_plate, high_lift -> 2274 | high_lift 1695 -> 1738 (fails under D too) |
  | maximum above D by more than the band (at least 1 degree) | 10 sessions | 8 | 1 (y_junction 3, +1.44) |
  | ... by more than 5 degrees | 4 (y_junction 3b +10.9) | 1 (airfoil_tunnel +6.0) | none |
  | mean of the 19 averages (D 10.20) | 12.00 | 11.40 | 10.36 |
  | cells summed (D 1,261,954) | -2.7% | -4.3% | +1.4% |
  | bent lines (D 780; half is 390) | 683 | 664 | 750 |

  **None passes; Step 4 keeps the step optional and no default changed.**
  What the table says beyond the rule:
  - The step earns its place on hand builds: against no step it lowers
    the maximum by more than a degree on 10 of 19 (y_junction 39.1 ->
    32.0, 40.1 -> 29.1; bump 39.4 -> 31.0; airfoil_tunnel 31.6 -> 25.2)
    and raises it on 3 (triangle 25.4 -> 37.7 with 9 jumps above 5;
    y_junction 3c 26.7 -> 32.4; bluff_ground 39.0 -> 41.2 with 27 percent
    more cells). The agents who skipped it in round 3 lost degrees on
    most cases and gained on the triangle and the bluff body.
  - The bent-lines term was mis-aimed: with no shape step at all 683 of
    D's 780 bent vertices remain, so the polish bends the lines, not the
    shape step.
  - P is the nearest miss: no failure gained, jumps above 5 summed 49 ->
    17 (triangle 9 -> 0, cascade 3 -> 0, high_lift 37 -> 17), one maximum
    +1.44. It fails on the mean average (+0.16, all of it y_junction 3:
    8.9 -> 14.5), on 43 more cells above aspect 1000 in a session that
    fails under D, and on the bent lines.
  - On builds that stay rectangular (no step, or lines held) `size`'s
    field pass shears the cells: cylinder_channel average 5.1 with the
    step, 19.1 without, 17.8 to 19.2 with any hold, 3.0 with
    `grading=strip` (first width then 20 to 31 percent off). This is the
    mechanism of batch 7, whose patch is frozen for round 4.
  - Step 4 now gives the 19-build result, the signal for the jumps
    (`min_edge_ratio` 0.1 with `max_pair_change` above 15: rerun with
    `shape_pair_ratio=2.5` or size the unshaped build too and keep what
    `check` prefers) and the bluff body's cell cost.
- **Holds by selection** (13b6ce7): `smooth ... hold_box=X0,Y0,X1,Y1[,...]`
  and `hold_line=A,B[;C,D]` (vertex IDs or `x,y` points) select the free
  vertices in a box or on a segment; `hold_along=none` (default) fixes
  them, `line` lets each move only along its segment (a vertex on two
  crossing segments stays), `x`/`y` only along that axis. Both modes;
  selected boundary vertices do not slide; the note gives `held`,
  `line_held` and a `hold_note` for a selection naming no free vertex (not
  a refusal). Defaults unchanged. Reproducers:

  | cylinder_channel 3 (shape step and polish) | cells | max (avg) | worst jump | bent of 16 | held lines' offset |
  |---|---|---|---|---|---|
  | trial's chain | 16,720 | 34.88 (5.05) | 1.71 | 8 | 0.038 |
  | `hold=` eight IDs | 20,296 | 41.21 (19.18) | 2.23 | 6 | 0 |
  | `hold_line=v27,v37;v1,v29;v33,v34` | the same session as `hold=` | | | | |
  | the same with `hold_along=line` | 21,776 | 35.99 (17.79) | 1.93 | 10 | 0 |

  | bluff_ground 3 (polish only) | cells | max (avg) | bent of 261 |
  |---|---|---|---|
  | trial's `hold=` twenty IDs | 165,264 | 38.99 (3.91) | 77 |
  | `hold_box=1.2,0.34,7,0.37,1.2,0.03,7,0.06` | the same session, byte for byte | | |
  | the box with `hold_along=x` | 165,264 | 39.00 (3.84) | 81 |
  | default polish | 165,264 | 39.03 (3.80) | 79 |

  Along-line holds cost one degree on the cylinder where fixed holds cost
  six; either makes the cells rectangular, which the field pass then
  shears (above). Holding only in the polish does not straighten what the
  shape step bent, so the skill says to hold in both steps.
- **Small**: `size`'s note joins its sentences with a semicolon (69aca86).
- **My checks**, all on my own scripts through the command line
  (`/tmp/garbage/blockdrawer-bench/verify-batch9/`): suite 656 tests OK;
  `SKILL.md` 250 lines; `construct.py` untouched and `size.py` changed by
  the separator only; the 19 hand builds replay to their delivered cell
  counts; the bands on triangle, cascade and y_junction 3 (nine chains);
  every chain of triangle, y_junction 3 and 3b, airfoil_tunnel and
  blunt_plate (the sessions that decide each failed term but high_lift's);
  the eight reproducer chains above, with the two "same session" claims
  compared file against file; the option syntax (IDs, points, a bad pair
  count refused, an empty selection noted). Every number agreed.
- **Not verified by anyone**: holds on engine sessions, on curved lines,
  and with `slide=true` on a real session (unit tests only); why
  y_junction 3c ends 53 percent off its first width with the shape step.
  The developer chose the cylinder's held lines (wake line and the two
  cross lines) without the trial's report.
- **One confirmation owed on the round-4 hand builds**, rule fixed now
  (the brief's rule had two terms that could not decide: bent lines, which
  the polish makes, and cells above 1000 in a session that fails anyway).
  Chains D and P on the construction part of every round-4 hand build
  with its own `size` line. Step 4 names `shape_pair_ratio=2.5` as what an
  operator build runs (text only) when, against D: no session gains a
  checkMesh failure or a face above 70; the jumps above 5 summed over the
  sessions are at most half of D's (void, and Step 4 stays, if D's sum is
  below 10); the maximum is more than 1.5 degrees higher on at most one
  session in ten and on none by more than 5; the mean of the averages is
  at most 0.5 degrees higher; the cells summed at most 2 percent higher.
  If it fails, Step 4 stays as it is and the question is closed.
