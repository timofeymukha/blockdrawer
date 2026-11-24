# Benchmark round 4 (Nov 20 2026)

Code: `review-followups` at 657cef2 (batches 6-9 of `ROUND3.md`, "Fixes
after round 3": skill text and notes, the field-grading notes, `add_rows
far_ratio`, the shape step measured on hand builds, `smooth
hold_box/hold_line/hold_along`). Protocol: `TRIAL.md`, deliverables in
`CASE/trial4/`. One fresh Opus agent at high effort per case, one at a
time, two cores. Two changes of protocol against round 3: the economy
target (cells and average non-orthogonality against `REFERENCE.md`, the
best result known per case) and three new held-out cases
(`cavity_channel`, `splitter_cylinder`, `elbow_vane`: no row in the route
table, no example, no reference row). checkMesh numbers are from my own
reruns (`bin/verify CASE trial4`). `u_bend` and `y_junction`, the two
cases whose round-3 spread came from the agent's topology, were run twice
more (`trial4b`, `trial4c`).

## Results

| case | route | blocks | cells (field) | x ref | max non-orth (avg) | skew | aspect | jumps >2.5 / >5 (worst) | first width | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cavity_channel (new) | one `add_grid` after the backward-step example; no shape step, no polish | 36 | 71,850 (71,850) | - | 0 (0) | 0 | 50 | 0 / 0 (1.11) | exact after three hand gradings (`size`: -7%) | 3.4 min | 118k |
| splitter_cylinder (new) | ring round the body with mitred junctions, junction fronts carried by `add_rows conform`, wake rows, far field by rows (`far_ratio=2`), two fills; `budget=95000`; four hand gradings | 461 | 91,114 (61,235) | - | 52.58 (15.13) | 1.06 | 954 | 45 / 0 (4.91) | -3%..+21% | 31 min | 212k |
| elbow_vane (new) | ring on the vane, polar `add_grid` for the bend, Cartesian legs, four fills, a split for the wake line; no shape step; polish with `hold=` | 92 | 35,575 (30,034) | - | 33.05 (5.54) | 0.76 | 27 | 0 / 0 (2.05) | -5%..+5% | 11 min | 183k |
| bump_channel | open ring per wall with hand-paired stations, one fill; default chain, then `keep_counts` | 44 | 66,576 (56,712) | 0.99 | 33.21 (17.31) | 0.74 | 54 | 0 / 0 (2.49) | exact | 5 min | 128k |
| triangle | after the C-grid example: ring `open_at` the apex, wake rows, far field by rows (`far_ratio=2`), two fills; no shape step; polish with `hold_line`; `keep_counts` | 406 | 36,464 (31,034) | 1.15 | 28.45 (9.46) | 1.07 | 599 | 14 / 0 (3.58) | -8%..-3% | 9 min | 149k |
| nozzle | open ring per wall, one fill; default chain, then `keep_counts` | 56 | 14,878 (12,802) | 1.00 | 12.24 (2.71) | 0.41 | 25 | 0 / 0 (1.34) | exact | 3 min | 117k |
| cylinder_channel | ring, band to box, grid; `grading=strip`, a band count by hand, polish with `hold_line`, `keep_counts` | 32 | 19,452 (18,700) | 1.16 | 41.31 (4.07) | 0.45 | 25 | 0 / 0 (2.23) | exact | 4.6 min | 132k |
| u_bend | open ring, rows, O-layer on a half square (the `failures.md` recipe); default chain, then one streamwise series per pass by hand | 20 | 22,914 (21,186) | 0.96 | 38.39 (3.25) | 0.48 | 30 | 0 / 0 (2.40) | -5%..+5% | 7 min | 146k |
| y_junction | open rings on three wall chains, five-sided fill; default chain, then `keep_counts` | 19 | 84,741 (78,379) | 1.41 | 32.10 (12.66) | 0.53 | 25 | 0 / 0 (2.28) | exact | 7 min | 139k |
| airfoil_tunnel | after the C-grid example: ring `fan=false`, wake rows, rows to the walls (`far_ratio=2`), wall bands; default chain | 429 | 120,620 (90,644) | 1.19 | 35.28 (3.93) | 0.56 | 54 | 0 / 0 (2.50) | airfoil -22%..-10%; walls within 5% | 13 min | 133k |
| bluff_ground | ring with one-block fans, rows in four directions (`far_ratio=2`), four fills, ground band by rows; no shape step; `budget=200000`; fourteen hand gradings | 136 | 165,684 (133,124) | 1.00 | 42.84 (5.31) | 0.69 | 87 | 9 / 0 (4.20) | body exact (by hand); ground -5%..+5% | 17 min | 165k |
| cascade | ring `fan=false`, wake rows, upstream strip, stations with `offset=`, two fills; default chain | 46 | 63,777 (54,492) | 1.10 | 55.12 (28.84) | 3.00 | 56 | 2 / 0 (3.71) | -5%..+5% | 9 min | 156k |
| blunt_plate | after the C-grid example: ring with kites, wake rows, far field by rows (`far_ratio=2`); default chain with `hold_line` | 473 | 68,571 (53,276) | 1.06 | 37.13 (3.99) | 0.48 | 378 | 14 / 0 (3.22) | -22%..+2% | 11.5 min | 132k |
| naca4412 | engine C-grid (1 run, 8 min) + `size` + polish | 300 | 137,410 (136,682) | 0.67 | 60.03 (**15.45**) | 1.04 | 127 | 0 / 0 (2.50) | -5%..+5% | 27 min | 143k |
| three_cylinders | **by hand**: ring and band to box per cylinder, H-grid round the three boxes, 16 vertex snaps; shape with `shape_pair_ratio=2.5`, size, polish | 94 | 59,178 (43,932) | 1.20 | 42.55 (10.22) | 0.93 | 21 | 0 / 0 (2.50) | -5%..+5% | 12 min | 166k |
| high_lift | operators only: three rings with open corners, wake bands by `add_band`, fills for coves and slots, far field by rows (`far_ratio=2`); shape with `shape_pair_ratio=2.5`; no hand grading | 802 | 303,173 (233,690) | 1.03 | 63.73 (15.55) | 2.49 | 931 | **55 / 11 (10.5)** | flap -16%..+4%; main -30%..+4%; slat -31%..+5% | 64 min | 305k |

Fourteen of sixteen meet every target. Every trial is `Mesh OK` with no
face above 70 degrees, skewness under 4 and aspect ratio under 1000.
`naca4412` misses the new economy target (average 15.45 against a limit
of 12.95). `high_lift` misses the jumps (11 above 5). The three new cases
meet every target at the first trial. Total: 235 minutes, 2.52M tokens;
the thirteen cases of round 3 took 189 minutes and 2.01M tokens (round 3:
161 minutes, 2.00M).

Repeats:

| trial | blocks | cells | x ref | max (avg) | jumps >2.5 | first width | sizing | time |
|---|---|---|---|---|---|---|---|---|
| u_bend 4 | 20 | 22,914 | 0.96 | 38.39 (3.25) | 0 | -5%..+5% | default, then two hand `total_ratio` lines | 7 min |
| u_bend 4b | 20 | 32,988 | 1.39 | 33.15 (1.77) | 0 | **-27%**..0% | `grading=strip` | 5.6 min |
| u_bend 4c | 20 | 24,398 | 1.03 | 38.63 (2.18) | 0 | -5%..+5% | `grading=strip`, then one hand `start_width` line | 4 min |
| y_junction 4 | 19 | 84,741 | 1.41 | 32.10 (12.66) | 0 | exact | default, then `keep_counts` | 7 min |
| y_junction 4b | 30 | 73,161 | 1.22 | 24.96 (8.87) | 0 | -17%..0% | `grading=strip` | 10 min |
| y_junction 4c | 38 | 58,163 | 0.97 | 24.92 (12.97) | 0 | -6%..+4% | default | 15.5 min |

Twenty trials in all: 270 minutes, 3.15M tokens.

## Round 3 against round 4, the thirteen common cases

| case | cells | max (avg) | jumps >2.5 / >5 | what changed |
|---|---|---|---|---|
| bump_channel | 66,928 -> 66,576 | 31.93 (14.14) -> 33.21 (17.31) | 0 -> 0 | first width exact (`keep_counts`), half a degree of average for it |
| triangle | 31,708 -> 36,464 | 30.04 (12.29) -> 28.45 (9.46) | 0 -> 14 / 0 | apex left open (the `failures.md` row), wake lines held, `keep_counts` (jumps 6 -> 14 for the first width -22% -> -8%) |
| nozzle | 14,878 -> 14,878 | 12.01 (2.77) -> 12.24 (2.71) | 0 -> 0 | the same build; first width exact |
| cylinder_channel | 16,720 -> 19,452 | 34.88 (5.05) -> 41.31 (4.07) | 0 -> 0 | wake and cross lines held straight (batch 9): the 45-degree corner blocks stay; first width exact |
| u_bend | 23,754 -> 22,914 | 39.58 (2.69) -> 38.39 (3.25) | 0 -> 0 | the same topology; round 3 dropped the shape step and graded two edges by hand, round 4 kept it and graded two |
| y_junction | 60,163 -> 84,741 | 31.95 (8.94) -> 32.10 (12.66) | 0 -> 0 | first width exact on every wall (round 3: one wall 29% fine), 41% more cells |
| airfoil_tunnel | 101,666 -> 120,620 | 31.64 (2.41) -> 35.28 (3.93) | 1 -> 0 | shape step kept (round 3 dropped it); a spending raise of 17.5k cells; airfoil first width -7% -> -22% |
| bluff_ground | 165,264 -> 165,684 | 38.99 (3.91) -> 42.84 (5.31) | 0 -> 9 / 0 | 357 -> 136 blocks, aspect 303 -> 87 (`far_ratio`), no twenty-ID hold line; 14 hand gradings |
| cascade | 57,974 -> 63,777 | 56.05 (26.59) -> 55.12 (28.84) | 0 -> 2 / 0 | no script of the agent's own (round 3: 20 lines); shape step kept |
| blunt_plate | 64,604 -> 68,571 | 37.82 (4.31) -> 37.13 (3.99) | 2 -> 14 / 0 | aspect 893 -> 378 (`far_ratio`), wake lines held; one base spoke 22% fine |
| naca4412 | 205,506 -> 137,410 | 45.12 (7.32) -> 60.03 (15.45) | 8 -> 0 | engine run with a band of 0.06 chosen by the agent (300 blocks for 250): fewer cells, no jump above 2.5, a worse wake start and twice the average |
| three_cylinders | 49,342 -> 59,178 | 67.94 (21.69) -> 42.55 (10.22) | 9 -> 0 | **hand build in place of the engine's hull**: skewness 3.46 -> 0.93 |
| high_lift | 293,522 -> 303,173 | 66.19 (13.23) -> 63.73 (15.55) | 87 / 22 -> 55 / 11 | 1144 -> 802 blocks, no rebuild and no hand grading (round 3: one rebuild, 14 hand fixes); aspect 941 -> 931 without hand repair |

Read with round 3's caution: one trial per case, and on a case without a
skill pattern a difference is inside the agent spread unless the report
ties it to a tool. What the reports do tie to a tool:

- `far_ratio=2` (batch 8) was used on six builds without being
  recommended by Step 3: aspect 893 -> 378 (`blunt_plate`), 303 -> 87
  (`bluff_ground`), 93 -> 54 (`airfoil_tunnel`), and `high_lift` under
  1000 with no hand repair.
- `hold_line ... hold_along=line` (batch 9) was used in four trials
  (`triangle`, `cylinder_channel`, `blunt_plate`, and `elbow_vane` with
  `hold=`): the lines the briefs ask for stay straight, at a cost the
  agents measured (triangle 24.77 -> 27.31 degrees; cylinder 35 -> 41).
- Step 4's text on the shape step was followed: `triangle` skipped it for
  the number quoted, `three_cylinders` and `high_lift` went to
  `shape_pair_ratio=2.5` on the signal the step names (after one default
  run each).
- The `failures.md` rows for the U-bend and the Y-junction gave the
  topology or the split directly in five of the six repeats.
- The economy target made agents look at the average: it is how four of
  them found the field pass's shear (below).

What is not better: the maxima and averages of the cases that were
already good. Six of the thirteen have a higher average than round 3, and
the reports show why in each: lines held, first widths made exact
(`keep_counts`), the shape step kept where round 3 dropped it, or the
field pass's shear left in.

## Gaps reported, by case

(Minutes are the agents' own estimates. "Suggestion" is the agent's.)

### cavity_channel (new)
1. `render --bounds` does not take the comma form `describe --bbox` takes.
2. First width 7% fine from whole counts in short band rows; fixed by
   three `set_edge_grading ... start_width propagate=true` (2 min). The
   skill points at `keep_counts`, not at this.
3. 58k of 72k cells are two channel blocks whose columns carry the
   along-wall count across the whole height.

### splitter_cylinder (new)
1. `add_band to_box` cannot serve a concave body corner; the skill has no
   pattern for one (carry each wall's front along the other wall's
   direction) (8 min).
2. `add_ring`'s front spline at a concave mitre ends with a control point
   behind the neighbouring front line: the next block folds, and nothing
   warns of a hook (4 min).
3. Jumps of 8 to 10 at the mitre's front vertex; `combine_blocks` and four
   hand gradings (6 min).
4. A spending raise 24 -> 63 through the band spokes (tied to the wake
   strips by the corner blocks): first widths -64%..+292% at the default
   budget (5 min).
5. Average 15.1: the field pass's shear of long thin strips;
   `grading=strip` gave 34 jumps above 5.
6. The plate's columns carried to the far boundary; `far_ratio` cannot
   widen columns that are all equally thin (aspect 954).
7. Vertex IDs shift after `combine_blocks` (2 min).
8. `normals=45` refused on a body with square corners, with no pointer to
   `points=`.

### elbow_vane (new)
1. `add_grid` conformed to the ring's spoke vertices and built cells over
   the vane's band; **the session validated with overlapping blocks** and
   only the render showed it (2 min).
2. False overlap refusals of `add_fill` on thin curved blocks: the centre
   taken from the corners falls in the neighbour (5 min, two re-layouts).
3. `add_fill` took a long exterior walk for a side meant open (2 min).
4. `normals=` ambiguous on a thin vane.
5. IDs renumber on every re-layout; asks for fill corners by `points=`.

### bump_channel
1. `render --bbox` does not exist.
2. Nothing pairs stations across a channel (2 min of geometry).
3. Core lines kink up to 25 degrees at the band fronts.

### triangle
1. A kite fan's last front edge is perpendicular to its last spoke: a
   carry cannot run through the spoke end, and a thin strip goes to the
   outlet (3 min).
2. A free polish bends the lines the brief requires; Step 6 does not say
   to hold them (2 min).
3. `keep_counts` after the polish is a trade the skill does not quantify.

### nozzle
1. The front end IDs of an open ring are read from the note (two batches).
2. The polish moves the first width 5%: the `keep_counts` step.

### cylinder_channel
1. **Default `size` gave an average of 19.24** on the long channel blocks;
   `grading=strip` 3.52 (3 min).
2. `size` left the 0.02 band at 9 radial cells: growth 1.38 per cell for
   1.15 asked, while the corner screen printed 1.205 (3 min).
3. `keep_counts` after the polish depends on the grading mode (strip:
   first width worse; field: average 3.26 -> 4.07); Step 6 is silent.

### u_bend (three trials)
1. **Default `size`: average 11.67, 12.03 and 20.82** in the long passes;
   strip 3.31, 1.77 and 2.18 with first widths 10 to 27% fine (2 to 5 min
   each). All three found it from the average against the reference.
2. The band front is 0.0013 off a tangent line/arc joint (the mitre of
   the polyline's segments): `add_grid` refuses or cannot be used; a
   `move_vertex` each time (1 to 2 min, all three, and round 3).
3. `keep_counts` after the polish made the first width worse under strip
   (-29 -> -34%, -27 -> -50%).
4. Step 4's "band spokes keep their length" hides two 5% tolerances that
   compound (0.110 and 0.090 on a 0.1 band).
5. `add_fill` corners need IDs; the digest counts a reused block ID once.

### y_junction (three trials)
1. **Default `size`: average 13.23 against 8.87 with strip** (4b); the
   other two kept the default and deliver 12.66 and 12.97.
2. A five-sided fill refused for an odd count; the missing station is a
   midpoint typed as coordinates: no `add_vertex between=A,B` (two trials).
3. The route table's junction row says "open or fanned": the fan was an
   8-minute dead end (five jumps above 5, a spoke 82% fine).
4. The open corner's count coupling: `failures.md`'s `split_edge` fixed
   -28% and a jump of 7.19 in two trials; the third did not need it with a
   station straight across the branch from the open corner.
5. `remove_edge` on the only block is refused; Step 3.1 suggests it.
6. `lp bd ...` did nothing and said nothing: a defect of my wrapper (the
   OpenFOAM `bashrc` sources a file given as a positional argument),
   fixed in `bin/lp` after the round.

### airfoil_tunnel
1. Spending raised the trailing-edge spoke component 25 -> 43 (+17.5k
   cells): 1.19 times the reference; nothing says when a smaller budget
   is worth it.
2. Airfoil first width 10 to 22% fine: two strips refused by the guard,
   which the note does not name.

### bluff_ground
1. `add_rows bbox=` stopped short of a carried spline lying on the line
   (2 min).
2. A spending raise through the fan ties the body spokes to far columns:
   first width -29% (-40% at the default budget); 13 hand gradings (3 min).
   Suggestion: one command for the start width of every spoke of a patch.
3. One jump of 5.06 beside a `far_ratio`-widened fan column (3 min).
4. The shape step: first width -60%, maximum 48.8 for 44.1 (4 min).
5. A lower budget as the way to cut cells (240k -> 166k at the same
   quality) is in `sizing.md` only.

### cascade
1. `normals=270` refused (three places) (1 min).
2. Shared periodic stations force sheared cross lines (average 28.8): no
   staggered pairing of wall and periodic stations.
3. The cyclic pair carries a count across the blade; `size` will not split
   a strip holding a cyclic edge (1.10 times the cells).

### blunt_plate
1. One base spoke 22% fine: two strips refused, not named; `keep_counts`
   made it worse (2 min).
2. Forty ring columns carried to the far boundary; `far_ratio` widens the
   end columns only.

### naca4412
1. **The average (15.45) could not be traced**: neither `quality` nor
   `check` gives a per-block average (10 min, two experiments).
2. The shape step on the engine's session: 47% more cells, four jumps
   above 5, at `max_pair_change` 12.8 (below Step 4's signal of 15). Step
   4's measurement is on hand builds only.
3. `sizing.md`'s NACA 4412 jump numbers are stale.

### three_cylinders
1. `to_box` spoke ends miss the grid lines the other boxes need (the
   spoke follows the mitred normal, not the ray to the target): 16
   `move_vertex` (3 min).
2. `add_band bbox=` selects nothing when the box equals the front's
   extent (1 min).
3. No pattern for staggered bodies in one H-grid (4 min of design).

### high_lift
1. `add_fill` did not walk a front through an open-V vertex and filled a
   straight side without saying so (3 min).
2. `add_band between=A,B`: `start=`/`end=` follow the chain's order, not
   A to B (2 min).
3. A tilted end spoke left a hooked spline on the band front; the shape
   step refused with 1354 folded cells (5 min).
4. `add_rows` refused with "blocks on both sides" where one curved front
   edge made a block non-convex (8 min of bisection).
5. No operator for the wake start of a blunt base whose corner turns more
   than 60 degrees (12 min).
6. The default shape step first, then the pair limit: one cycle of 15 min.
7. Jumps above 5 `size` cannot remove (budget not binding).
8. Waiting: about 30 of 64 minutes on shape, size, polish and check of an
   800-block session (4.5 and 3.5 to 6 minutes a run).

## Recurring gaps, round 4

| # | gap | trials (cases) | cost |
|---|---|---|---|
| 1 | The field pass shears long thin blocks: the default `size` gives an average several times the strip's | 6 reported (`splitter_cylinder`, `cylinder_channel`, `u_bend` x3, `y_junction` 4b); left in unnoticed in at least 2 more | 2 to 5 min each, or the quality |
| 2 | The first width after the chain: the polish's 5%, strips the guard refuses (not named), spending raises through band spokes, and `keep_counts` that helps under `field` and harms under `strip` | 12 (`cavity`, `bump`, `nozzle`, `triangle`, `cylinder_channel`, `u_bend` 4b 4c, `y_junction` 4 4b, `airfoil_tunnel`, `bluff_ground`, `blunt_plate`, `splitter_cylinder`) | 1 to 3 min each; up to 14 hand lines |
| 3 | Cells: spending raises of 10 to 45%, and a lower budget as the lever is in `sizing.md` only | 6 (`airfoil_tunnel`, `bluff_ground`, `y_junction`, `u_bend` 4b, `splitter_cylinder`, `cascade`) | cells; 2 to 5 min |
| 4 | Selections and options that differ between commands: `render --bounds` against `describe --bbox`; `bbox=` chain selection stopping at a spline or at an exact extent | 5 (`cavity`, `bump`, `y_junction` 4c, `bluff_ground`, `three_cylinders`) | 1 to 2 min each |
| 5 | `normals=` refused where the normal occurs more than once (square corners, a thin body, a cambered blade) | 3 (`splitter_cylinder`, `elbow_vane`, `cascade`) | 1 min each |
| 6 | Vertices by ID only: fill corners, a midpoint station, IDs that renumber on a re-layout | 7 (`elbow_vane`, `u_bend` 4, `y_junction` 4 4c, `splitter_cylinder`, `nozzle`, `y_junction` 4b) | 1 to 3 min each |
| 7 | Operators that say the wrong thing or nothing: overlapping blocks validated (`add_grid`), false overlap on thin curved blocks, a long walk or a straight side taken silently (`add_fill`), "blocks on both sides" for a non-convex block (`add_rows`) | 2 cases, 6 instances (`elbow_vane`, `high_lift`) | 2 to 8 min each |
| 8 | Hooked or misplaced front geometry the unhook rule does not catch: a concave mitre's front spline, a tilted end spoke's, a front vertex off a tangent joint | 3 cases (`splitter_cylinder`, `high_lift`, `u_bend` x3) | 1 to 5 min each |
| 9 | Patterns the skill does not carry: a concave body corner, staggered bodies in an H-grid, the junction recipe, station pairing across a channel, a staggered periodic passage, the wake start of a blunt base | 6 cases | 2 to 12 min each |
| 10 | No per-block average in `quality`: an average over the limit cannot be pointed at blocks | 1 reported (`naca4412`); every agent in gap 1 found the blocks by the sampled maximum instead | 10 min |
| 11 | Lines the brief requires are bent by a free polish; Step 6 does not say to hold them | 3 (`triangle`, `bluff_ground`, `blunt_plate`) | 2 min; 1 to 6 degrees for the hold |
| 12 | Waiting on `size` and `smooth` on sessions of hundreds of blocks | 5 (`high_lift`, `naca4412`, `blunt_plate`, `airfoil_tunnel`, `splitter_cylinder`) | 3 to 30 min |

## Spread between agents: the two repeated cases

- **`u_bend`** (round 3: three topologies, cells 1 : 3.3 : 1.7). Round 4:
  the same topology three times (20 blocks, the `failures.md` recipe),
  cells 1 : 1.44 : 1.06, maxima 38.4 / 33.2 / 38.6. What still differs is
  the way round the field pass's shear: hand `total_ratio`, strip alone
  (first width 27% fine), strip with one hand `start_width`. The missing
  pattern of round 3 is closed; the spread left is gap 1.
- **`y_junction`** (round 3: cells 1 : 1.46 : 1.24). Round 4: three
  topologies again (19, 30 and 38 blocks: a five-sided fill; five
  four-sided fills; a five-sided fill with a bisector line and two
  splits), cells 1.46 : 1.26 : 1, maxima 32.1 / 25.0 / 24.9, averages
  12.7 / 8.9 / 13.0. The two with the mitre's bisector or four-sided fills
  reach 25 degrees. The route table's row does not choose between them
  and still offers the fan, which cost one agent 8 minutes.

## Reading

1. **The workflow holds on unseen cases.** Three new geometries (a cavity,
   a body with concave junctions, a vane in an elbow) met every target at
   the first trial in 3, 31 and 11 minutes with no script. The 31 minutes
   are a missing pattern (a concave body corner) and the junction's jumps.
2. **The dominant defect is in `size`, not in construction.** The field
   pass (the default since batch 3 of round 2) makes the first width right
   and tilts every cell of a long thin block. Round 3's targets could not
   see it; the economy target can, and six trials reported it in one
   round. The agents' ways round it all cost the first width or hand
   lines. It was measured in batch 7, and the patch that removes it is
   frozen for the confirmation below.
3. **First width is the second.** Twelve of twenty trials did something
   by hand or by an extra step to get it, and the step the skill offers
   (`keep_counts`) is right under one grading mode and wrong under the
   other.
4. **A hand build beat the engine on its own row.** `three_cylinders` by
   operators: 42.6 degrees for 67.9, average 10.2 for 21.7, skewness 0.93
   for 3.46, in 12 minutes. One trial; the route table sends clusters of
   smooth bodies to the engine first.
5. **`high_lift` is at the edge of the time cap for compute reasons.**
   The build itself needed no rebuild and no hand grading this time; half
   the wall-clock was `size` and `smooth` on 800 blocks.
6. **Batches 8 and 9 were used without being asked for**: `far_ratio` on
   six builds, the line holds on four. The round has no trial where an
   agent tried one of them and gave it up.

## The two confirmations owed on the round-4 hand builds

Inputs: the construction part of the 19 hand builds of the round (all but
`naca4412`, the engine's), each with its trial's own `size` inputs
(`grading=` and `keep_counts=` dropped, since the default chain is what is
measured), through shape step, `size`, polish. A `split_edge` a trial made
after its first `size` is part of the construction (two builds). Chains: H
(the code of the round, 657cef2), X (the same with the frozen patch
`patches/field_shear_e8cdd01.diff` applied to the package), P (H with
`shape_pair_ratio=2.5` in the shape step). Measured with H's `check` and
`quality` in all three. Scripts and rows:
`/tmp/garbage/blockdrawer-bench/verify-round4/` (`inputs4.py`,
`chain4.sh`, `all4.out.rows`).

### The field-shear patch: fails its rule, dropped

Rule: `ROUND3.md`, "Batch 7", with its two amendments.

| term | result |
|---|---|
| no face above 70, cell above aspect 1000 or checkMesh failure gained | holds |
| no session gains a jump above 5 | **fails**: `splitter_cylinder` 4 -> 6 (summed over the 19: 10 -> 10) |
| jumps above 2.5 summed do not rise | **fails**: 155 -> 156 |
| no maximum more than 2 degrees higher | **fails**: `triangle` 28.04 -> 33.87 (three others fall by more than 2: `y_junction` 4b, `bump_channel`, `high_lift`) |
| no average more than 0.5 degrees higher | holds (largest rise 0.49, `triangle`) |
| the mean of the averages falls | holds: 10.21 -> 8.12 |
| first heights: no wall patch 5 points worse, mean not 1 worse | holds: none worse; `airfoil_tunnel` 22.0 -> 13.9%; mean 20.1 -> 19.4 |
| cells within 5 percent | holds (equal on every session) |
| no skewness crossing 3.5 | holds |

What it does where the round reported the shear, at unchanged cells and
first widths:

| session | average, H | average, X | the agent's own way round it |
|---|---|---|---|
| u_bend 4 | 11.67 | 3.20 | 3.25 by two hand `total_ratio` lines |
| u_bend 4b | 12.03 | 1.50 | 1.77 with strip, first width 27% fine |
| u_bend 4c | 11.96 | 2.30 | 2.18 with strip and a hand `start_width` |
| y_junction 4 / 4b / 4c | 12.68 / 14.06 / 12.97 | 11.00 / 11.61 / 11.21 | 4b: 8.87 with strip, first width 17% fine |
| bump_channel | 16.85 | 15.14 | - |
| elbow_vane | 6.13 | 4.26 | - |
| bluff_ground | 5.08 | 3.87 | - |
| splitter_cylinder | 14.20 | 13.98 | none found |
| cylinder_channel (default chain: no holds) | 3.04 | 3.03 | - |

So the patch does what it was written for and the rule still rejects it,
as on the 39 inputs of batch 7 and on a different session (`triangle` 4
here; `y_junction` 3c, `cascade` 3 and `triangle` 3 there by 2.1 degrees).
Two of the three failing terms are counts of jumps, which batch 9 measured
to move by up to 5 on one session for a one percent change of the core
size; the third, `triangle`'s 5.8 degrees, is four times that session's
band. **The patch is dropped as the rule says.** The shear stays the first
recurring gap of the round; a new formulation is a new batch with a rule
whose per-session terms carry the measured bands, and the `triangle`
builds are where to start (the patch makes them worse twice).

### `shape_pair_ratio=2.5` for operator builds: fails its rule, closed

Rule: `ROUND3.md`, "Batch 9".

| term | result |
|---|---|
| no checkMesh failure or face above 70 gained | **fails**: `high_lift` gains 2 faces above 70 (67.92 -> 70.04) |
| jumps above 5 summed at most half of D's (D: 10) | **fails**: 9 (`triangle` 3 -> 0, `high_lift` 3 -> 5) |
| maximum more than 1.5 degrees higher on at most one session in ten, none by more than 5 | **fails**: `three_cylinders` +9.29 (33.26 -> 42.55), `high_lift` +2.12 |
| mean of the averages at most 0.5 higher | holds: 10.21 -> 10.20 |
| cells summed at most 2 percent higher | holds: -0.6% |

Step 4 stays as it is and the question is closed. One thing the chains
show about Step 4's own text: its signal for the pair limit
(`min_edge_ratio` 0.1 with `max_pair_change` above 15) fired on two builds
of the round. On `triangle` the limit was right (jumps 19 / 3 -> 7 / 0,
maximum 28.0 -> 25.9). On `three_cylinders` the agent followed it without
sizing the default-shaped build, and delivered 42.55 degrees where the
default step gives 33.26 (average 9.13 for 10.22, 14 jumps above 2.5 for
none, none above 5 either way, 8% more cells). The line should say to
size both and keep what `check` prefers, not to rerun with the limit.

## Candidates for the next fix batch

By the rule of the benchmark (two unrelated cases, or a general
primitive), in the order of what the round lost to them:

1. **The field pass's shear** (gap 1; four cases). A new formulation, not
   the frozen one: the agents' two suggestions are to measure a strip's
   tilt against the thickness of its blocks across the strip, and to keep
   one series for opposite long sides whose ends are both away from a
   wall's clustering. Develop on the 58 known inputs (39 of batch 7 and
   the 19 of this round); the rule's per-session terms use batch 9's
   bands; confirmation on round 5's builds.
2. **The first width as a result, not a step** (gap 2; ten cases). Name in
   `size`'s note the strips its guard refused and the member holding the
   shared ratio; one command or `size` option that sets the start width of
   every band spoke of a wall patch (`bluff_ground` wrote 13 lines,
   `cavity_channel` 3, `u_bend` 4c 1); Step 6's `keep_counts` advice said
   per grading mode; a per-block average in `quality` (gap 10) so an
   average over the limit can be pointed at blocks.
3. **Operators that mislead** (gaps 7 and 8; four cases). Overlapping
   blocks pass `validate` (`add_grid` over a ring's band): a general
   check. `add_fill`'s overlap test on curved blocks (the centre from the
   corners); the side it takes said in the note when it is a long walk or
   a straight side past an open-V vertex; `add_rows`' "blocks on both
   sides" naming the block that is not convex; band-front splines at a
   concave mitre and beside a tilted end spoke through the unhook rule;
   a spoke at a station where the curve does not turn on the curve's own
   normal (the U-bend's 0.0013, four trials on one case, but a wrong
   normal at any tangent joint).
4. **Selections and positions** (gaps 4, 5, 6; eight cases). `render`
   taking `--bbox` and the comma form; `bbox=` chain selection inclusive
   within the tolerance and across carried splines; `normals=` with a
   section (`between=`) or the nearest of several to a point; `add_fill`
   corners by `points=`; `add_vertex between=A,B`.
5. **Skill text** (gaps 3, 9, 11 and the confirmations): a lower `budget`
   as the lever for cells in Step 5; Step 6 saying to hold the lines a
   brief requires; Step 4's pair-limit line (size both); the junction row
   without the fan and with the measured recipe; a pattern for a concave
   body corner and for bodies staggered in one H-grid; `grading=strip` as
   the comparison when the average is over the reference, until item 1
   lands; the route table's row for a cluster of smooth bodies after one
   more hand trial of `three_cylinders`.
6. **Run time on large sessions** (gap 12): `size` 4.5 minutes and
   `smooth` 3.5 to 6 on 800 blocks. Not measured where the time goes.

Not taken, by the rule: a staggered periodic pairing (`cascade` only), the
wake start of a wide blunt base (`high_lift` and, differently, `triangle`:
watch), `size` splitting a cyclic pair's two edges together (`cascade`
only), station pairing across a channel (`bump_channel` only).

## Protocol notes for round 5

- `bin/lp` no longer runs a file argument through OpenFOAM's `bashrc`
  (`lp bd ...` works).
- The shell moves a foreground command to the background after about
  three minutes whatever its timeout; the launch message says so from
  `naca4412` on. It belongs in `TRIAL.md`.
- `REFERENCE.md` has rows for the three new cases from this round.

## Fixes after round 4

### Batch 10: sizing - the shear guard, the first width, the average (commits f4912d0, 1fa5ecb, c9583c9, 972ef14)

Four local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round4a/BRIEF.md`; `construct.py` and
`smooth.py` untouched. The rule for item 1 was fixed in the brief before
any measurement, on 58 inputs under the default chain: the 39 of batch 7
and the 19 hand builds of round 4.

- **The shear guard** (f4912d0), now the default of `grading=field`. A
  strip's move in the field pass is refused (`refused_by_shear`) when it
  would raise the session's average non-orthogonality by more than one
  degree (the sampled average's rise over the strip's blocks and their
  neighbours, times their share of the session's faces) and buys nothing:
  no band spoke's first height nearer the one asked, no jump above 2.5
  removed at its members' ends, no worst face lowered there. Chosen after
  six other formulations failed on a development set or on the 58 (a
  limit on the neighbourhood's own average took the NACA 4412 from 45.1 to
  62.2 degrees after the polish).
  - Why the dropped patch hurt the triangle: there the field pass's own
    gradings are not shear for nothing. The sized state has faces of 64 to
    75 degrees which the polish removes, and keeping those strips on one
    ratio leaves the polish a worse start.
  - Every term of the rule held: no failure, face above 70, cell above
    aspect 1000 or skewness crossing gained; cells equal on all 58; no
    maximum 2 degrees higher (largest +1.53, `y_junction` 3c); jumps above
    5 summed 94 -> 94, above 2.5 458 -> 458; no average higher; **mean of
    the 58 averages 10.23 -> 9.14** (1.09 for the 1.0 required); mean
    worst first-height deviation 22.07 -> 22.00.

  | session | average before | after | `grading=strip` | first widths after |
  |---|---|---|---|---|
  | u_bend 4 / 4b / 4c | 11.67 / 12.03 / 11.96 | 3.33 / 3.31 / 2.13 | 3.31 / 1.77 / 2.09 | within 5% (strip: 20 to 29% fine) |
  | u_bend 3 (round 3) | 15.84 | 2.96 | 2.64 | within 5% |
  | y_junction 4 / 4b / 4c | 12.68 / 14.06 / 12.97 | 9.51 / 10.11 / 11.79 | - / 9.89 / - | within 6% |
  | y_junction 3b / 3c | 15.23 / 17.28 | 11.61 / 10.50 | | |
  | cylinder_channel 3, lines held along (batch 9's reproducer) | 17.79 | 3.03 | | within 5% |
  | cylinder_channel 4, no shape step, lines held | 6.88 | 3.35 | | within 5% |
  | elbow_vane | 6.13 | 5.31 | | |
  | round-2 u_bend / y_junction / bluff_ground | 8.82 / 13.97 / 8.17 | 6.91 / 13.07 / 7.12 | | |

  **What it does not reach**: 45 of the 58 size to the identical state.
  The guard is on the session's average, so a strip that tilts a small
  share of the mesh passes: `splitter_cylinder` (14.2), `bump_channel`
  (16.85), `u_bend` 3b and 3c (24.1, 16.8) and the cascades are
  unchanged. The threshold was set after seeing these 58 inputs and the
  mean's margin is 0.09 degrees: **one confirmation on round 5's hand
  builds under the same rule is owed**.
- **The first width as a result** (1fa5ecb).
  - `size`'s note names the strips a guard kept on one ratio and the
    spokes they leave off, and `raised` says when a raise went through
    band spokes (`airfoil_tunnel` 4: "r_0-r_42 25->43 +17,556 through band
    spokes of airfoil (-18%..-14%)").
  - New command `set_first_width WIDTH [patch=A,B] [opposite=keep|follow]`:
    every band spoke of the wall patches graded to that first height,
    counts kept; edges opposite a spoke keep their grading (default; the
    tilt is reported) or follow it along the strip. It reports the first
    widths and the jumps before and after.

    | build | before | after one line | the trial's own |
    |---|---|---|---|
    | bluff_ground 4 (`patch=body`) | -32%..+5%, 42.84 (5.317), jumps 8 / 0 | 0%, 42.84 (5.311), 9 / 0 | 13 hand lines: the same numbers |
    | cavity_channel 4 (`opposite=follow`) | -7%, 0 (0) | 0%, 0 (0); `keep`: 1.31 (0.08) | 3 hand lines: 0 (0) |
    | u_bend 4c (`patch=divider`) | -15%, 38.63 (2.182) | 0%, 38.63 (2.180) | 1 hand line: within 5% |
    | blunt_plate 4 (`patch=plate`) | -22%, 37.13 (3.994), 14 / 0 | 0%, 37.13 (3.998), 14 / 0 | left 22% fine |
    | airfoil_tunnel 4 (`patch=airfoil`) | -22%, 35.28 (3.928), 0 / 0 | 0%, 35.28 (3.919), 0 / 0 | left 22% fine |

  - `cylinder_channel` gap 2 (a band at 9 cells and growth 1.38) does not
    reproduce: the ring's spokes get 17 cells at 1.098 per cell; the 9
    cells are its tangential edges. The corner screen's 1.205 belongs to
    another edge, where the field pass keeps the finer end's bound at a
    count it does not change; `quality` now names the edge of its largest
    growth.
- **The average and the blocks that make it** (c9583c9): `quality` gives
  the average non-orthogonality weighted by faces at the real counts and
  the five blocks contributing most (JSON, text verdict, MCP). On
  `u_bend` 4 before the guard it names the three sheared blocks (20.8,
  20.8 and 11.3 degrees for a session average of 11.67).
- **Text** (972ef14): Step 5 (a lower `budget` cuts cells; what the note
  names; the average and where to look), Step 6 (`set_first_width`;
  `keep_counts` per grading mode), `sizing.md`, `failures.md`.
- **My checks**, on my own scripts through the command line
  (`/tmp/garbage/blockdrawer-bench/verify-batch10/`, old code from an
  archive of 657cef2): suite 666 tests OK; `SKILL.md` 250 lines; the 19
  round-4 inputs under the new code, every term of the rule; eight
  round-3 inputs and four of the corpus (`naca4412` and `bluff_ground` of
  round 2 among them), six of these under both codes; the cylinder with
  held lines under both codes, with and without the shape step; the five
  first-width reproducers. Every number agreed with the developer's rows.
  `quality`'s average against checkMesh's on all 43 of my rows: equal to
  three digits on 40, within 0.03 degrees on the cascades (cyclic sides)
  and 0.007 on the round-2 NACA 4412.
- **Not verified by anyone**: `set_first_width` on cyclic walls, with
  spacing links (it writes gradings directly and does not synchronise
  them) and on curved spokes beyond the unit tests; the 39 inputs I did
  not rerun rest on the developer's rows.
- **Left open**: the shear on sessions where no single strip carries a
  degree of the average (above); the splitter's junction jumps.

### Batch 11: construction - operators that mislead, selections, the skill's patterns (commits ca75a23, 8e0fae0, c4a5025, 0a83d82)

Four local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round4b/BRIEF.md`; `size.py` and `smooth.py`
untouched.

- **Blocks over one another** (new `overlap.py`; 0.05 s on the 800-block
  high-lift session). `validate` refuses them, `quality` warns, sets the
  verdict invalid and counts them, `check` prints "BLOCKS OVERLAP", and
  every construction operator refuses a new block over an existing one.
  `elbow_vane`'s polar grid over the vane's band: 50 blocks with 34
  overlapping pairs and "OK" before; now 42 blocks, 8 cells left out with
  a note, none overlapping. A side finding: an `add_band` with a tilted
  start spoke built over the main element's band in silence; now refused.
- **False refusals from a block's corner average**: `add_fill` beside
  thin curved blocks (the overlap test reads the sides as built) and
  `add_rows` from a curved front edge ("blocks on both sides", "not
  strictly convex": both reproducers of `high_lift` now build, 210 and 10
  blocks).
- **The side `add_fill` takes**: an exterior walk goes on through an open
  wall vertex (the slat cove's front is now the default reading), and the
  note says "NOT the only reading" with the other reading as `sideK=...`
  when it runs along another path. First version: 57 of 75 fills flagged
  (a single edge against the straight side between the same vertices);
  sent back, now 3 of 75 (two of them a curved front edge against its
  chord), the cove and the elbow's walk still flagged.
- **Front splines that fold**: a front's end points that leave its end
  more than 45 degrees from the wall's own direction are dropped (every
  sound front on 42 builds turns under 19.4, the hooked ones 80 to 122).
  `splitter_cylinder` without its two `set_edge_type ... line` lines: the
  shape step refused with 20 folded cells, now runs (44.0 -> 40.2).
  `high_lift` without its line: refused with 1354, now runs (84.4 -> 66.9
  count-free, 24 -> 0 faces above 70).
- **A spoke at a tangent line/arc joint: not changed.** The rule fixed in
  the brief fails at its first term: with the straight stretch's normal,
  `u_bend` trial 3c no longer builds (a front vertex lands on the first
  block's corner and is reused). Seven builds would move, by at most
  3.9e-4 of their extent. Kept out; `add_grid`'s and `add_rows`' refusals
  now name the vertex 0.0013 away and `move_vertex V x=0
  carry_curves=true`.
- **Smaller**: `add_ring`'s "no height builds it" says a station on the
  cove is missing; `add_band`'s note says which vertex `start=` and `end=`
  set; the digest counts a block made under a reused ID.
- **Selections and positions**: `render --bbox` and the comma form for
  both spellings; `bbox=` chain selection inclusive within 1e-5 of the
  box's diagonal and through curved edges lying in it, the note naming
  the edges left out at the selection's ends; `add_ring normals=... near=X,Y`
  where a normal occurs more than once (the refusal lists the places);
  `add_fill points=`; `add_vertex between=A,B fraction=F`.
- **Skill text** (c4a5025): route-table rows for the three new cases, the
  junction row without the fan and with the three trials' numbers, the
  three-cylinders row with both routes and rule 2 saying by hand for a
  cluster whose bodies fit boxes of one H-grid; Step 3 (placeholder block,
  concave body corner, the new options); Step 4's pair-limit line (size
  both, keep what `check` prefers) and a line on engine output; Step 6
  (hold the lines a brief requires, with the cost). Every number quoted is
  in this ledger.
- **What changes for existing builds**: 36 of the 38 hand builds of rounds
  3 and 4 and the four examples build to identical sessions. `high_lift`
  trial4 keeps every vertex and block and differs in the control points of
  three front edges (the unhooked fronts). `bluff_ground` trial4 no longer
  replays as written: its line working round the old box selection is
  refused, because the selection now takes the whole line.
- **Where the time goes** (measured, nothing changed): on the 800-block
  session `size` spends 64 percent in the sampled guard
  (`sampling.sample_model`, 1228 calls; spending's guard alone 44
  percent) and 21 percent in the field's edge profiles; `smooth` spends
  65 percent in `_evaluate` (802k trial positions).
- **My checks** (`/tmp/garbage/blockdrawer-bench/verify-batch11/`, old
  code from an archive of 972ef14, everything through the command line):
  suite 686 tests OK; `SKILL.md` 250 lines; the construction of all 38
  builds on both codes (36 identical, the two as said) and again after
  the follow-up (38 of 38 against the previous head); 17 reproducers on
  both codes; `validate` and `quality` on the overlapping session; the
  shape step on both fold reproducers; `between=`, `points=`, the digest,
  the render options; my own count of the fill note on the round-4 builds
  (32 of 43 before the follow-up, 2 of 43 after); the skill's new numbers
  against this ledger.
- **Not verified by anyone**: the elbow's exact failing states (the
  reproducers are a synthetic arc band and an analogue); the overlap
  check on two thin blocks crossing near their ends; the tolerances (45
  degrees, 1e-5 of the diagonal) beyond these builds.
- **Left open**: `SKILL.md` holds its 250 lines by longer lines: 16.9k
  characters at round 3, 20.0k now. The cap needs a second measure. The
  tangent-joint spoke. Speed on large sessions.
- **A regression of this batch, found by round 5's first trials and fixed
  during the round** (f8e7dfd, `batch_digest` only): after `smooth` the
  quiet digest listed every block whose corners moved as created and
  removed ("842 blocks" on a 1058-block session). A block is now the same
  while its ID keeps its corner vertex IDs, or two corners in place.
  Checked on the annulus (no line after `smooth`), the reused-ID case (20
  created, 1 removed) and the suite (687 tests). Round 5's first three
  trials ran at 0a83d82, the rest at f8e7dfd; no session differs.
