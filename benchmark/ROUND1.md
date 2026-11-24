# Benchmark round 1 (Nov 18 2026)

Tools and skill at commit `399bc5d` (branch `review-followups`). One fresh
Opus agent per case at high effort, protocol in `TRIAL.md`. The checkMesh
numbers are from my own rerun (`bin/verify CASE`), not from the agent's
report. Time and tokens are the harness's figures for the agent.

## Results

| case | route | blocks | cells | checkMesh | faces > 70 | max non-orth | max skew | worst jump | script fallback | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cylinder_channel | operators, 14 build commands | 36 | 21,824 | Mesh OK | 0 | 35.62 | 0.84 | 1.84 | no | 4.5 min | 121k |
| naca4412 | engine C-grid (1 run, 488 s on two cores), then shape/size/polish and 2 gradings by hand | 250 | 208,278 | Mesh OK | 0 | 67.01 | 0.85 | 4.43 (27 above 2.5) | no | 20.5 min | 137k |
| blunt_plate | operators, 20 commands incl. 4 gradings by hand | 119 | 87,033 | Mesh OK | 0 | 26.96 | 0.74 | 3.07 (4 above 2.5) | no (a helper script for ring stations by normal angle) | 13 min | 154k |
| y_junction | operators, 23 build commands (blocks from vertices for the branch) | 12 | 41,946 | Mesh OK | 0 | 39.96 | 1.20 | 1.54 | no (one fraction worked out by hand) | 4.5 min | 126k |
| cascade | operators, 104 commands (vertices typed and projected, blocks from vertices, 7 periodic pairs matched by hand) | 44 | 62,788 (field 41,421) | Mesh OK, cyclic pair accepted | 0 | 64.66 (average 28.77) | 3.26 | 2.87 (3 above 2.5) | no vertex script; a read-only helper printed the pair-matching commands | 17 min | 209k |
| high_lift | operators (engine not allowed), 259 commands of which 192 written by two scripts of the agent's own | 538 | 378,785 (field 258,978) | Mesh OK | 0 | 62.33 (average 12.21) | 3.59 | 4.06 (33 above 2.5) | **yes**: the main cove pocket (transfinite fill between fronts) and the convex hull round the cluster | 64 min | 373k |
| u_bend | operators, 13 build commands | 32 | 24,010 (field 21,230) | Mesh OK | 0 | 20.88 | 0.80 | 1.97 | no | 4.5 min | 121k |
| three_cylinders | engine `--hull` (1 run, 108 s), then size / polish with sliding / size / polish; Step 4 left out on purpose | 204 | 46,464 (field 33,522) | Mesh OK | 0 | 68.05 (average 16.71) | 3.13 | 3.62 (7 above 2.5) | no | 22 min | 173k |

## Gaps reported, by case

Each gap carries the case that reported it; a gap named by two unrelated cases
is a candidate for a tool or skill change.

### cylinder_channel

1. Route rules overlap: "a domain the operators can state" and "a simple
   internal domain - try the engine first" both match, no precedence, no table
   row for internal flow with a body. (skill)
2. First widths land at 4.05e-4 .. 5.23e-4 for a 5e-4 target and the quiet
   `size` digest elides them (`first_width=…`). (tool output, `sizing.md`)
3. `smooth mode=shape` bends the H-grid's straight lines where they meet the
   O-ring spokes (the square became an octagon, wake lines kinked by up to
   0.01 in y); Step 4's "nothing to gain is normal on an H-grid" did not hold.
   Suggested: interior vertices held on a line. (tool, skill)
4. `quality --json` gives `max_interface_size_ratio` without the edge. (tool
   output)
5. Renders show cells only with `--preview --no-nodes`, which the skill does
   not name. (skill) - added to `TRIAL.md` for the later trials.
6. Wake resolution is not a sizing input: the wake strip is sized at the core
   size. Suggested: a per-region core size. (tool)

### naca4412

1. Engine run 488 s on two cores against 184-187 s in the README (core count
   not stated there). (doc)
2. One face at 70.61 degrees at the inlet/bottom far-field corner (b247) that
   the skill's remedies did not remove: `size method=distance`, which Step 5
   prescribes when the field has faces above 70, gave 88,555 cells with 2088
   faces above 70 (max 81.49); `repair_block b247` said "nothing to repair"
   (it judges angles; the face is in cells 0.0005 wide and 0.25 tall). Cause:
   the C-grid's streamwise count component carries the wake-start width
   (0.0004) out to the far-field corner at (0.5, -15) and `size` keeps it
   there. Fixed by hand: `set_edge_grading ... start_width 0.004` on the two
   corner edges. (tool: `size`; skill: Step 5's `method=distance` rule rests
   on one session and is wrong here)
3. Same cause, not fixed: a column of cells 0.00035 wide and 0.25 tall from
   the wake start to the top and bottom far field (aspect 766 in the quality
   screen); 208k cells where the field alone needs 140k. (tool: `size`)
4. `engine.md` does not say that four `--outer` chains with one outlet drive
   the C-grid; the agent read `cgrid.py` to find out. (doc)
5. `describe --section patches` is not a section (`boundaries` is). (minor)

### blunt_plate

Route note: the agent skipped the engine although the table sends a single
body there - the blunt-base template needs the base within 2 percent of the
perimeter (here 3.8), and a run costs 8 minutes.

1. `add_ring count=N` spreads stations by arc length, so the nose tip (radius
   0.008) got none and `to_box` failed: "the spokes of v49 and v48 reach
   opposite sides of the box". Suggested: a forced station at a curvature
   peak. (tool: `add_ring`)
2. With a tip station, `to_box` snapped a box corner to the tip spoke: "Block
   b55 ... would not be strictly convex". The agent wrote a helper script to
   find the arc fractions where the nose normal points at 140 and 120 degrees
   and passed them as `fractions=`. Suggested: stations by normal angle; a
   refusal that names the corner and spoke. (tool: `add_ring`, `add_band`)
3. One `add_grid` for the H-grid round the box fails when the box sides carry
   different stations ("a grid vertex at (-0.6, -0.04) would fall inside the
   existing edge"); built in four parts. The skill reads as one call. (skill,
   and the missing two-way conform the skill already names)
4. `size` left jumps of 10.9 and 9.4: the column above and below each base
   corner carries the corner's 2e-4 spacing to y = +-6 beside a column that
   starts at 0.002; `budget=180000` changed nothing. Four `set_edge_grading`
   by hand removed the jumps above 5; the thin columns remain (aspect 1469 in
   the quality screen, 776 in checkMesh). **Same cause as naca4412 gaps 2-3.**
   (tool: `size`)
5. `smooth mode=shape` moved the prescribed wake lines (v81 from (1.6, 0.04)
   to (1.575, 0.027)); rerun with `hold=`. Step 4 does not mention `hold`.
   **Same as cylinder_channel gap 3.** (tool, skill)
6. Spline control points are drawn in a purple that reads as a patch colour.
   (render)

### y_junction

My reading of the render (`trial1/junction.png`): every target is met, but the
topology is a plain H-grid without wall bands, so the branch's two wall-fine
columns run down through the main channel to the bottom wall and the
streamwise counts grade down to 0.0026 at the junction. The targets do not
measure that waste; the next round should record cells against `size`'s
`field_cells`.

1. Vertex IDs had to be probed (apply, describe) before
   `add_block_from_vertices` could name the grid's vertices. Suggested:
   blocks from coordinates, reusing coincident vertices as `add_grid` does.
   (tool)
2. No operator states a channel at an angle: `add_grid` is Cartesian or
   polar. The branch came from `add_vertex` and `add_block_from_vertices`;
   `split_edge` cuts both walls at one fraction, which is not perpendicular
   here, corrected with `slide_vertex` and a fraction worked out by hand.
   Suggested: a rotated frame for `add_grid`, a perpendicular split. (tool)
3. The skill does not say whether to regrade (`size ... keep_counts=true`)
   after the polish moved vertices. (skill)
4. Route table and rules: no internal junction row, two rules match.
   **Same as cylinder_channel gap 1.** (skill)
5. The H-topology ties the branch's across-count to the bottom wall under the
   mouth: fine columns through the junction block, 249 and 317 streamwise
   cells. Wall bands would avoid it; the agent did not try `add_band` round
   the 310-degree corner. (topology choice; cells, not quality)

### cascade

My reading of the render (`trial1/overview.png`): the thresholds pass, but
this is the weakest mesh of the round so far. Average non-orthogonality is
28.8 degrees (under 10 in the other cases), corner angles run 24.6 .. 157.6,
the passage blocks beside the trailing edge (b29, b30) are sheared into a fan
that collapses on the periodic station x7, and the trailing edge carries a
fan, not a wake band.

1. **Cyclic export depends on vertex names (a defect).** The first build
   failed in blockMesh: "face 1 area does not match neighbour by 41.0477% --
   possible face ordering problem. patch:periodic_lower". `foam.py` writes
   patch faces in edge/block order and each block starts at its smallest
   vertex ID, so the face order of a cyclic pair depends on names;
   `_validate_cyclic_boundaries` compares only sorted counts and lengths.
   Worked round by renaming vertices. ~10 min. (tool: export/validation)
2. `size` ignores cyclic pairs: 7 of 9 partner edges came out with different
   counts or ratios (64 cells at 0.64 against 86 at 0.121); matched by hand.
   ~5 min. (tool: `size`)
3. No operator for matched stations on a pair of translated chains, or for a
   band carried out to a chain (`to_box` only): stations by `add_vertex` and
   `project`, blocks by `add_block_from_vertices`. (tool) Related to
   y_junction gaps 1-2: outside boxes and circles the build falls back to
   vertex-by-vertex.
4. Trailing-edge fan: jump 5.39 beside the fan kite, `size` refused its own
   split; one grading by hand gave 2.83. (tool: `size`; and the fan itself)
5. **`quality` reports skewness 5.249 "(checkMesh fails above 4)" where
   checkMesh reports 3.259** (b29/b30 at the boundary vertex x7). `smooth`
   and `size` refuse results on that measure. (tool: `sampling`)
6. Smoothing moved the wake vertex off the wake line (14-degree kink); fixed
   with `hold=`. **Third case** (cylinder_channel 3, blunt_plate 5). (tool,
   skill)
7. `describe --only b18,b39` prints empty sections without an error (IDs are
   space-separated). (tool output)
8. Route rule "simple internal domain: engine first" is wrong for periodic
   domains, which the engine refuses; no cascade row. **Third case** on the
   route table (cylinder_channel 1, y_junction 4). (skill)

### high_lift

My reading of the renders: wall bands go through both slots and both coves
and the flap cove is a clean pocket (`trial1/closeup_main_cove_step.png`).
The wake requirement is only partly met: the slat and main wakes ride along
the next element's band front, and behind the flap's blunt base the block
lines fan out (`trial1/closeup_flap_te_wake.png`). 538 blocks against the
engine's 203. Corner angles 25.6 .. 153.7; the corner screen's aspect ratio
reaches 5851 in the far field (cells 4e-5 wide and 0.23 tall: wall-fine
spacing carried out again).

Reference (my run, `/tmp/garbage/blockdrawer-bench/high_lift/engine_ref/`):
the engine's 203-block session
(`/tmp/timofey/data/blockdrawer/30p30n/30p30n-engine.session.json`, hull far
field at 15 chords) through the same shape / `size 1e-4 0.5 growth=1.15
wall_aspect=25` / polish: 301,170 cells (field 202,675), Mesh OK, 4 faces
above 70, max 85.4, skewness 2.48, worst jump 87.9. `method=distance` was not
tried on it at these inputs.

1. **No operator fills a region between existing fronts.** Needed for the
   cove pocket, the slot strips and the hull; ~20 min and two scripts
   (`cove_fill.py`: transfinite interpolation between four sides of existing
   vertices; `hull.py`: the cluster loop mapped to an offset convex hull).
   Suggested: `fill_between` (transfinite), a band carried to a curve or
   hull. **Same shortfall as cascade 3, y_junction 2, blunt_plate 3.** (tool)
2. Mitred `add_band` collapses at concave corners beside short edges and
   says nothing; the next band then fails with "the selected edges branch".
   ~10 min and a detection script. (tool: `add_band`)
3. Offset splines hook back at band ends and beside fans (interpolation
   points behind the first vertex); the next band fails with "Blocks sharing
   edge ... overlap or are misoriented". ~5 occurrences, ~10 min, fixed with
   `set_edge_type ... line`. (tool: `_offset_interior`)
4. `move_vertex` without `carry_curves` leaves spline points behind (hooks,
   then "would not be strictly convex"). (tool default, skill)
5. `set_boundary_by curves=flap` misses the closing segment of a loop whose
   closing point is not repeated (the flap's blunt base); `add_ring` does not
   make a 58-degree corner a station. (tool)
6. **`smooth mode=shape` collapsed the slat trailing-edge fan kite** (a
   vertex 0.00017 from its neighbour, a 16-cell edge with a 5e-6 cell, jumps
   of 26-31) and bunched a far-field strip (jumps 10.3 and 6.3). ~15 min.
   Shape mode has no edge-length limit. **Fourth case where the shape
   smoother damages a layout** (cylinder_channel 3, blunt_plate 5,
   cascade 6). (tool)
7. A wedge block in the slat cove entrance gave 2,210 faces above 70;
   `repair_block b8` made it worse (severe cells 1172 -> 1193) and applied
   the move anyway. Fixed by a `split_edge` in the topology. (tool:
   `repair_block`)
8. `size` could not split the slot-rail strip ("split strip branches or
   crosses itself"); 71 cells across a band 0.015 high. (topology coupling)
9. Skill: no hand route for a multi-element cluster with a concave loop;
   `failures.md` on trailing-edge fans does not mention blunt bases. (skill)

### u_bend

1. First widths 0.00144 .. 0.00193 for a requested 0.002. **Same as
   cylinder_channel 2** (also y_junction, cascade in passing). (tool: `size`)
2. The quiet `size` output elides `verification`, `bands` and
   `worst_interfaces`, which Step 5 says to read; rerun with `--json`.
   **Same as cylinder_channel 2.** (tool output)
3. `describe --only b31,b25,b26` prints empty sections. **Same as cascade
   7.** (tool output)
4. Reference curves are drawn as Catmull-Rom through 90-degree corners: a
   bulge outside the domain in the render. (render)
5. `add_band ... to_box` on an open front worked but is not documented.
   (skill)
6. `smooth mode=shape` moved the pass core rows by up to 0.21, leaving
   slanted core lines; checkMesh agreed, kept. **Fifth case** of the shape
   smoother moving lines an engineer would keep straight. (tool)

### three_cylinders

My reading of the render (`trial1/renders/overview_blocks.png`): bands and gap
blocks are as required; the far field is a fan from the hull to the rectangle
with bundles of thin strips to the top and bottom and wedges meeting the
right-hand corners at 21-26 degrees. All the worst faces are out there.

1. **`smooth mode=shape` (Step 4) collapsed a ring to a sliver**: edge
   v118-v14 from 0.2995 to 0.00073, reported as a success; the sizing after
   it gave a worst jump of 244, 24 jumps above 5. Found by rendering. The
   agent left Step 4 out. **Sixth case** against the shape smoother. (tool)
2. `size budget=75000` equals the default: spending stops at the guard, not
   the budget, and nothing says so. `size` reports worst jumps 7.55 / 6.07
   where `quality` gives 4.72 / 3.62 on the same counts after the polish.
   (tool output)
3. `size ... keep_counts=true` discards the regrades spending had made:
   jumps above 5 went 4 -> 8. `sizing.md` does not say so. (skill, tool)
4. `smooth slide=true`, then `size` again, then `smooth` was the real fix
   (skewness 3.57 -> 3.13, worst jump 5.0 -> 3.62); the skill says sliding
   "changed little" and does not say to size again after it. (skill)
5. The Step 5 `method=distance` comparison was worse (skewness 3.85, worst
   jump 19.2) and unnecessary: the polish had already removed the faces above
   70. **Same as naca4412 2.** (skill)
6. The engine's hull far field inside a rectangle is where the quality is
   lowest. Suggested: a box-aligned outer layer. (engine)
7. `bottom` and `outlet` get nearly the same red in renders. (render)

## Round 1 in one view

All eight sessions are `Mesh OK` with no face above 70 degrees, skewness
under 4 and no jump above 5. Six routes were operators, two the engine. One
case (high_lift) needed vertex scripts; one (blunt_plate) a helper for
station positions. 4.5 to 64 minutes per case, about 1.4 million agent tokens
for the round. The thresholds did not separate good meshes from mediocre
ones; what did: average non-orthogonality (2.8 to 28.8), corner-angle range,
the quality screen's aspect ratio (up to 5851), cells against `field_cells`
(1.13 to 1.5), and whether the wake requirement was met.

## Recurring gaps (two or more unrelated cases)

| # | gap | cases | kind |
|---|---|---|---|
| A | `smooth mode=shape` has no edge-length limit and moves lines meant to stay straight: slivers, collapsed fan kites, kinked wake lines | cylinder_channel, blunt_plate, cascade, high_lift, u_bend, three_cylinders | tool |
| B | wall-fine spacing carried to the far field (cells 4e-5 wide and 0.2 tall, aspect in the thousands, 10-50 percent extra cells, a face above 70 at a far corner) | naca4412, blunt_plate, high_lift, three_cylinders; y_junction by topology | tool (`size`) |
| C | outside boxes and circles the build is vertex by vertex: no fill between existing fronts, no rotated grid, no band to a curve or hull, no matched stations on a periodic pair, blocks only from vertex IDs | blunt_plate, y_junction, cascade, high_lift | tool (operators) |
| D | route table and rules: two rules match, no rows for internal flow with a body, junctions, periodic passages, multi-element by hand | cylinder_channel, y_junction, cascade, high_lift (blunt_plate skipped the engine against the table) | skill |
| E | first width drifts up to 28 percent below and 5 percent above the input; the quiet `size` output elides `verification`, `bands`, `worst_interfaces` | cylinder_channel, u_bend; noted by y_junction, cascade, three_cylinders | tool output, `sizing.md` |
| F | Step 5's "retry with `method=distance`" is wrong outside the 30P30N | naca4412, three_cylinders | skill |
| G | `add_ring` stations: none at a curvature peak, none at a 58-degree corner, none by normal direction | blunt_plate, high_lift | tool |
| H | operator builds put a fan at a trailing edge; no wake band by operators | cascade, high_lift (naca4412 got one from the engine) | tool (operators) |
| I | `describe --only a,b` prints empty sections | cascade, u_bend | tool output |

## Defects reported once (clear enough to fix without a second case)

- Cyclic export: face order depends on vertex names; validation compares
  only counts and lengths (cascade 1).
- `quality` skewness 5.249 where checkMesh says 3.259; `smooth` and `size`
  refuse on that measure (cascade 5).
- `size` ignores cyclic partner edges (cascade 2).
- `repair_block` applied a move that raised the severe count (high_lift 7);
  it cannot see a bad face in a thin tall cell (naca4412 2).
- `add_band`: silent collapse at concave corners beside short edges; offset
  splines hooking back at band ends (high_lift 2, 3).
- `set_boundary_by curves=` misses the closing segment of an implied loop
  (high_lift 5).
- `size keep_counts=true` discards spending's regrades; `size` and `quality`
  report different worst jumps; a non-binding budget is not reported
  (three_cylinders 2, 3).

## Fixes after round 1

### Gap A: shape smoother (commit 72403ad, `review-followups`, not pushed)

- `smooth mode=shape` keeps every edge at least `shape_length_ratio`
  (default 0.1) of its input length; both modes report
  `min_edge_ratio`/`min_edge` and `max_edge_ratio`/`max_edge`.
- `keep_lines=true` (off by default): an interior vertex on a straight block
  line moves only along it. Off because it also holds every straight spoke
  of a ring or band: after sizing it cost 4.1 (cascade), 16.1 (blunt plate)
  and 15.1 degrees (grooved tandem) against the floor alone.
- Main suite through `bin/lp`: 528 tests, OK (my run).

My own rerun on `three_cylinders/trial1/engine.session.json`
(`/tmp/garbage/blockdrawer-bench/verify-shape/`): shape with defaults gives
count-free 70.75 -> 63.00, smallest edge ratio exactly 0.1 (v120-v16),
largest 4.77. With the trial's four lines after it (size, smooth slide,
size, smooth):

| | cells | max non-orth | skewness | worst jump (quality) |
|---|---|---|---|---|
| trial 1, Step 4 left out | 46,464 | 68.05 | 3.13 | 3.62 |
| Step 4 with the floor | 55,869 | 64.79 | 3.98 | 5.59 |

So the floor turns a failure (jump 269) into a trade: 3.3 degrees gained for
20 percent more cells, a jump above 5 and skewness at the limit. The edge
sits on the floor, which means the count-free optimum still wants it
shorter: the floor is a guard, not a cure. The cause is that shape mode
optimises angles alone and `size` pays for the lengths. Open: a count-free
term or limit that sees what sizing will pay (for example a block's
opposite-edge length ratio).

### Periodic pairs, reports, skill text (commits 018bb4a .. 786ea40, not pushed)

| commit | what | my check |
|---|---|---|
| 018bb4a | cyclic export pairs and orients coupled faces by geometry (`blockdrawer/cyclic.py`); a pair that does not match node for node is refused with the edges named | cascade trial session with vertices renamed at random and blocks restarted at their smallest ID, 3 seeds: old code blockMesh FATAL 3 of 3 (2.9, 109, 66 percent area mismatch), new code Mesh OK 3 of 3 with identical checkMesh numbers |
| 2f21bb3 | sampled skewness and non-orthogonality treat cyclic faces as coupled | `quality` on the cascade trial session: skewness 3.259 (was 5.249; checkMesh 3.259) |
| fec1794 | `size` gives a cyclic pair one count and one grading | `size 2e-4 0.03 growth=1.15 wall_aspect=25` on the cascade trial session: exports (export now refuses a mismatched pair), 63,955 cells, Mesh OK, 64.46, skewness 3.246 |
| 4996ad5 | `describe --only a,b` accepts commas; an unknown ID is an error | both seen |
| dc58fec | quiet `size` note carries verification, first width per wall patch, worst jump, `budget_binding` | seen on the cascade |
| 786ea40 | skill: `method=distance` is a comparison, not a retry; route rows for internal flow with a body, junction, periodic passage, multi-element by hand; rules ordered | read; 245 lines |

Main suite through `bin/lp`: 539 tests, OK (my run). Fresh exports of all
eight trial sessions are byte-identical to the round-1 dictionaries.

New facts from these checks:

- The quiet note on the cascade reads `first_width=blade 0.00015..0.000456
  for 0.0002 (-25%..+128%)`: a first cell more than twice the one asked was
  invisible in round 1. Open, belongs with gap E in `size`.
- The route rows name the benchmark's domain classes with their round-1
  outcomes, so round 2 is no longer held out for the route decision on
  those classes. Round 2 should add new cases.
- Open model question: `set_edge_cells` and `split_edge` do not act across a
  cyclic pair; `size` now refuses to split a periodic strip and a hand edit
  of one side is refused at export.

### Construction operators and repair (commits fd450d3 .. 7bb50d5, not pushed)

| commit | what |
|---|---|
| fd450d3 | `add_band` refuses a band block that would fold at a concave corner beside a short edge, naming corner and edges and the largest height that builds (it used to build a crossed block silently) |
| 7984eb2 | offset splines keep only the points that run forward between their ends (no hooks) |
| 9d3f86d | `set_boundary_by curves=` takes the closing segment of a body's loop, and no longer closes an open U across its mouth |
| 92185c4 | `add_ring` options: `normals=` (station where the outward normal has an angle), `tips=true`, `corner=DEG`; `to_box` refuses a folding block naming spokes and corners. Defaults unchanged |
| 7bb50d5 | `repair_block`: a gain on the worst cell may not add severe cells |

My checks: main suite through `bin/lp` 548 tests, OK. Replays of the
`cylinder_channel`, `u_bend`, `y_junction` and `blunt_plate` build batches
give the same `blockMeshDict` on this code as on 399bc5d (byte for byte;
`cylinder_channel` and `blunt_plate` also equal to the trial's own).
`add_ring plate 0.04 count=31 normals=140,180,220` builds with "3 station(s)
by normal"; `set_boundary_by flap` assigns all 25 wall edges of a flap ring,
base included. On the blunt plate `tips=true` finds no tip (the 5:1 nose is
not tight enough for the rule), so that case is served by `normals=180`.

Not reproduced by the developer: "a 58-degree corner got no station"
(high_lift 5): the flap's 58.4-degree vertex is a station at the default.

### Gaps B and E: `size ... grading=field` (commit 812cce1, not pushed)

Cause confirmed by the developer's measurements: every member of a strip
shares one total ratio, so a spoke's first width is in proportion to its
length (spread of width over length within a strip exactly 1.000) and a
far-field member carries the wall's clustering (naca4412: 0.000363 at a far
corner where its own series at the same count gives 0.0696).

`grading=field` regrades each strip member by the field where it lies, after
spending, counts unchanged, behind a shear limit, a jump limit and the
sampled guard. Off by default: on 11 sessions nothing got worse on the
checkMesh limits, but it failed the "better where the gap was reported"
clause on two (blunt_plate: guard refused the wake strip, aspect 468 -> 504;
cascade: reported first-width range widened by one oblique spoke).
`keep_counts=true` now regrades for continuity (three_cylinders after its
polish: worst jump 23.5 -> 6.53).

My checks: suite 554 tests OK; `cylinder_channel` default replay
byte-identical to round 1; with the option `cylinder_channel` first widths
+0%..+0% on all three walls, Mesh OK, 35.21 (was 35.62), aspect 21 (25);
`naca4412` trial session re-sized with the option and polished: 211,431
cells, Mesh OK, max 30.98 (trial 1 with two hand gradings: 67.01), skewness
0.86, aspect 67, worst jump 3.04 (4.43), spokes +0% at the wall-normal end.

Still open in `size`: the cell count stays 10-50 percent above `field_cells`
(lowering it needs spending to work per edge); average non-orthogonality
rises on some sessions with the option (y_junction 9.21 -> 13.34, developer's
number); `first_width` measures along the spoke, so oblique spokes inflate
the range; `quality`'s corner aspect ratio and checkMesh's disagree widely
(naca0012: 10,105 against 54).

### Gap C: `add_fill`, `add_stations`, blocks by coordinates (commits 15c4ce6 .. 9ee981f, not pushed)

- `add_fill A,B,C,D`: blocks between four sides (existing exterior chains,
  loose stations, or new stations facing the opposite side; a side may
  follow a reference curve); three and five corners as blocks round a
  centre; mismatched counts, folds and overlaps refused with the fix named.
- `add_stations CURVE xs=|ys=|count=|fractions= [offset=DX,DY] id=NAME`.
- `add_block_from_vertices points=X,Y;...`.
- Fixed on the way: spline edges along a curve kept points picked from one
  end, so the two chains of a periodic pair differed.

Developer's rebuilds in scratch: the high-lift cove pocket (44 commands and
a script) is one `add_fill`; the cascade build 72 -> 24 commands, same 44
blocks, Mesh OK 65.09; the junction 23 -> 22 with no ID probing. Not
expressible yet: an offset or hull curve, a rotated grid frame, a
perpendicular split.

My checks: suite 567 tests OK; `cylinder_channel` and `blunt_plate` replays
byte-identical to round 1, `u_bend` and `y_junction` to the earlier replays.
My own test on the held-out nozzle geometry: two `add_stations`, one
`add_fill` with `curve1=`/`curve3=`, patches, then `size 1e-3 0.04
growth=1.15 wall_aspect=20` and `smooth`: 5 blocks, 19,458 cells, Mesh OK,
max 13.43, skewness 0.46; `first_width` -60%..+0% (the strip-shared ratio
between a 0.4 and a 1.0 spoke: gap E again).

Round 2 runs on 9ee981f.
