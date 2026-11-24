# Benchmark round 6 (Nov 22-23 2026)

Code: `review-followups` at 49ffc0d - batches 12 and 13 of `ROUND5.md`,
"Fixes after round 5": cap boxes in `size` (`cap=`, `cap_x=`, `cap_y=`) and
`quality --bbox`, the guards' real-count reports, `add_ring counts=`,
`facing=`, the ring note pairing wall stations with front vertices,
`to_x`/`to_y`/`stations=` at a vertex, the blunt-base wake recipe in
`failures.md`, and the skill's rows for round 5's cases. Protocol:
`TRIAL.md` as in round 5, deliverables in `CASE/trial6/`. One fresh Opus
agent at high effort per case, one at a time, two cores. Three new held-out
cases (`ellipse_incidence`, `tube_pair_periodic`, `flap_airfoil`: no row in
the route table, no example, no reference row); round 5's three new cases
now have rows. checkMesh numbers are from my own reruns (`bin/verify CASE
trial6`). `splitter_cylinder`, round 5's one missed target, was run twice
more (`trial6b`, `trial6c`).

**A contamination, found at the third trial.** Subagents of the session
that ran the round are handed its cached copy of the repository's
`AGENTS.md`, and that copy carried three sentences of a patch that batch
13 measured and did not commit (a smooth band vertex "takes the wall's
normal", `to_box` ends a spoke at the corner exactly, a row on the target
line "has that coordinate exactly"). The `flap_airfoil` agent quoted one.
Trials 1 to 3 had the text in context; from trial 4 on the launch prompt
says that copy may be out of date and that the skill, `bd commands` and
the tool's own reports are the authority. None of the three sentences
describes a route; two of the first three trials reported the very defect
the sentences deny (a corner $10^{-12}$ off, a far row at
7.999999999999999), so the text cost them minutes, it did not help them.

Also found: three session files of the batch-13 developer lay in
`three_cylinders/geometry/` since Nov 22 16:35 (`three_boxes_base.json`,
`three_boxes_s2.json`, `ubend_nosnap.json`). The case's trial did not
mention them. Moved out after the trial
(`/tmp/garbage/blockdrawer-bench/verify-round6/stray/`).

## Results

| case | route | blocks | cells | x ref | max non-orth (avg) | skew | aspect | jumps >2.5 / >5 (worst) | first width | time |
|---|---|---|---|---|---|---|---|---|---|---|
| ellipse_incidence (new) | ring with stations by normal every 25 degrees, wake rows, far field by rows; default chain, `cap_y` for the wake | 466 | 27,982 | - | 23.57 (5.42) | 0.52 | 79 | 5 / 0 (3.68) | -5%..+5% | 10.8 min |
| tube_pair_periodic (new) | ring and `to_box` per tube (`counts=`), one periodic H-grid; no shape step | 255 | 17,194 | - | 41.50 (4.39) | 0.54 | 21 | 0 / 0 (2.32) | -5%..+5% | 9.2 min |
| flap_airfoil (new) | two rings `fan=false` (`counts=`), slot fill down to a free vertex, wake rows (`stations=` by vertex), far field by rows; no shape step; `set_first_width` | 1118 | 151,929 | - | 51.18 (9.52) | 1.85 | 478 | 25 / 0 (4.88) | exact | 44 min |
| orifice_channel | one `add_grid`; `size` with `cap_y` for the jet, `set_first_width opposite=follow` | 41 | 64,052 | 1.14 | 0 (0) | 0 | 27 | 0 / 0 | exact | 3.8 min |
| eccentric_annulus | two rings, stations paired on bipolar lines (own script), two fills; no shape step | 128 | 24,030 | 0.99 | 11.24 (3.74) | 0.32 | - | 0 / 0 (1.68) | -5%..+5% | 6 min |
| cavity_channel | one `add_grid` with block lines on the cap box's sides; `size` with `cap_y`, `set_first_width opposite=follow` | 44 | 37,010 | 0.52 | 0 (0) | 0 | 53 | 0 / 0 (1.11) | exact | 1.7 min |
| splitter_cylinder | ring with mitred junctions (`counts=`), far field by `add_grid`, wake by one `add_rows`; shape step; `cap_y` | 263 | 120,409 | 1.32 | 48.87 (12.71) | 1.46 | 604 | 20 / 0 (5.00) | -6%..+5% | 16.5 min |
| elbow_vane | the row's polar grid, cap boxes from 45-degree stations (a script); polish with `keep_lines=true` | 116 | 44,048 | 1.24 | 31.17 (5.65) | 0.56 | 27 | 0 / 0 (2.33) | -5%..+5% | 9 min |
| bump_channel | open ring per wall, stations paired by hand (point symmetry, foot lines along the mitres), one fill; no shape step | 76 | 39,096 | 0.87 | 32.70 (10.21) | 0.72 | 27 | 4 / 0 (2.60) | -10%..+1% | 7 min |
| triangle | ring with all three corners open, the blunt-base fill recipe, wake and far-field rows; polish with `hold_line` | 300 | 32,930 | 1.04 | 17.40 (5.72) | 0.50 | 376 | 0 / 0 (2.50) | -19%..+1% | 7 min |
| nozzle | open ring per wall, one fill; no shape step | 48 | 14,706 | 1.01 | 11.78 (2.82) | 0.45 | 26 | 0 / 0 (1.60) | -5%..+5% | 2.5 min |
| cylinder_channel | ring, band to box, grid; default chain with `hold_box`/`hold_along=x` on the wake line | 32 | 17,998 | 1.08 | 35.07 (2.66) | 0.60 | 26 | 0 / 0 (1.71) | -5%..+5% | 5 min |
| u_bend | open ring, O-layer on a half square, two grids; default chain | 20 | 23,754 | 1.04 | 37.45 (2.97) | 0.47 | 26 | 0 / 0 (1.87) | -5%..+5% | 4 min |
| y_junction | the row's recipe; default chain, one `split_edge` | 50 | 54,960 | 1.04 | 24.79 (7.44) | 0.72 | 26 | 0 / 0 (2.50) | -7%..+5% | 9 min |
| airfoil_tunnel | ring `fan=false`, wake rows, rows to the walls, two fills; `hold_box` on the wake band in both smooth steps; one trailing-edge spoke moved | 287 | 125,004 | 1.23 | 26.54 (4.70) | 0.99 | 36 | 3 / 0 (2.90) | -5%..+5% | 15 min |
| bluff_ground | H-grid with a boxed nose; `budget=205000`; one `split_edge`, transition stations, `set_first_width` | 81 | 186,597 | 1.32 | 51.69 (5.15) | 0.90 | 255 | 4 / 0 (2.55) | exact | 22 min |
| cascade | ring `fan=false` (`counts=16,6,16`), a stagnation strip to the inlet, wake rows, fills; `hold_box` in the shape step | 110 | 44,664 | 0.97 | 54.41 (26.45) | 2.72 | 23 | 0 / 0 (2.31) | -5%..+5% | 14 min |
| blunt_plate | ring with both base corners open, the blunt-base fill recipe, wake and far-field rows; default chain | 359 | 82,043 | 1.17 | 20.29 (3.61) | 0.61 | 309 | 15 / 0 (4.29) | -5%..+5% | 11 min |
| tandem_airfoils | two rings, wake rows (`to_y=` and `stations=` by vertex), far field by rows, fills; two `split_edge`; `set_first_width` | 690 | 127,740 | 1.08 | 37.54 (5.93) | 0.66 | 206 | 29 / 0 (4.13) | exact | 27 min |
| naca4412 | **by hand**: the skill's C-grid example and a half-annulus fill to the inlet arc; `set_first_width` | 385 | 113,484 | 0.55 | 38.62 (3.86) | 0.61 | 248 | 24 / 0 (3.92) | exact | 11 min |
| three_cylinders | by hand: ring and `to_box` per body, one H-grid, 8 vertex snaps; shape with `shape_pair_ratio=2.5` | 75 | 49,319 | 1.09 | 32.71 (8.54) | 0.93 | 23 | 0 / 0 (2.50) | -5%..+5% | 6.6 min |
| high_lift | operators only: three rings, the blunt-base recipe on the flap, cove and slot fills, two wake strips along typed reference curves, wake rows, far field by rows; no shape step, no global polish | 2118 | 332,467 | 1.51 | 69.08 (22.67) | 1.88 | 793 | **121 / 46 (19.46)** | **main -41%..0%; flap -11%..+62%**; slat 0..+10% | 74 min |

**Twenty-one of twenty-two meet every target.** `high_lift` misses two
(46 jumps above 5; first widths) and is a cells finding at 1.51 times its
reference. `splitter_cylinder`, round 5's miss, meets every target (worst
jump 4.9996, at the polish's ceiling). The three new cases meet every
target at the first trial, in 11, 9 and 44 minutes. Total: 316 minutes and
about 3.2M tokens for the 22 (round 5's nineteen: 262 minutes, 2.90M).

Repeats:

| trial | far field and junction | blocks | cells | x ref | max (avg) | aspect | jumps >2.5 / >5 (worst) | first width | time |
|---|---|---|---|---|---|---|---|---|---|
| splitter_cylinder 6 | far field one `add_grid`, wake one `add_rows`; mitre column rebuilt with `counts=` | 263 | 120,409 | 1.32 | 48.87 (12.71) | 604 | 20 / 0 (5.00) | -6%..+5% | 16.5 min |
| splitter_cylinder 6b | the row's route (far field by rows), each junction a 4x3 fill; one `split_edge` of the mitre spoke | 764 | 105,653 | 1.16 | 43.15 (8.63) | 888 | 2 / 0 (2.82) | -5%..+5% | 21.5 min |
| splitter_cylinder 6c | cylinder front carried along the plate by rows, a near-field box, far field one `add_grid` | 276 | 110,559 | 1.21 | 47.91 (11.25) | 256 | 51 / 0 (5.00) | -6%..+5% | 18 min |

Twenty-four trials in all: 356 minutes, about 3.5M tokens (round 5's
twenty-one: 279 minutes, 3.19M).

## Round 5 against round 6, the nineteen common cases

| case | cells | max (avg) | jumps >2.5 / >5 | what changed |
|---|---|---|---|---|
| orifice_channel | 56,208 -> 64,052 | 3.95 (0.33) -> 0 (0) | 0 -> 0 | the jet's cell cap by `cap_y` in one run (two hand counts and a script before); station offset compared at three values |
| tandem_airfoils | 118,320 -> 127,740 | 40.23 (4.37) -> 37.54 (5.93) | 18 / 0 -> 29 / 0 | 690 blocks for 1058; vertex-named rows; 228,418 cells until two hand splits of a two-ended column |
| eccentric_annulus | 24,297 -> 24,030 | 11.74 (3.56) -> 11.24 (3.74) | 0 -> 0 | the same pairing, by the agent's own script again; `facing=` read and not used |
| cavity_channel | 95,879 -> 37,010 | 0 -> 0 | 0 -> 0 | the shear layer's cap as a `cap_y` box with block lines on its sides instead of band rows everywhere |
| splitter_cylinder | 83,763 -> 120,409 | 43.58 (9.32) -> 48.87 (12.71) | 26 / 3 -> 20 / 0 | every target met; the far field a grid, 263 blocks for 722; the budget binding |
| elbow_vane | 47,394 -> 44,048 | 40.34 (9.11) -> 31.17 (5.65) | 2 / 0 -> 0 | the row's polar grid followed |
| bump_channel | 44,720 -> 39,096 | 33.02 (11.28) -> 32.70 (10.21) | 0 -> 4 / 0 | the foot lines along the mitres |
| triangle | 35,813 -> 32,930 | 28.17 (8.27) -> 17.40 (5.72) | 6 / 0 -> 0 | open corners and the blunt-base fill (batch 13) in place of fans |
| nozzle | 14,534 -> 14,706 | 11.79 (2.79) -> 11.78 (2.82) | 0 -> 0 | the same build |
| cylinder_channel | 16,720 -> 17,998 | 34.93 (4.45) -> 35.07 (2.66) | 0 -> 0 | a station one box width downstream so the shape step does not shear the wake blocks |
| u_bend | 23,754 -> 23,754 | 37.45 (2.97) -> 37.45 (2.97) | 0 -> 0 | identical |
| y_junction | 63,360 -> 54,960 | 24.85 (9.93) -> 24.79 (7.44) | 0 -> 0 | 81,200 cells until one hand split of a two-ended strip |
| airfoil_tunnel | 120,416 -> 125,004 | 27.22 (4.26) -> 26.54 (4.70) | 0 -> 3 / 0 | the same layout |
| bluff_ground | 141,828 -> 186,597 | 40.73 (3.67) -> 51.69 (5.15) | 0 -> 4 / 0 | another topology (an H-grid with a boxed nose), two layouts lost to `to_box`'s corner snapping; first width exact for +28% |
| cascade | 46,137 -> 44,664 | 56.26 (26.32) -> 54.41 (26.45) | 2 / 0 -> 0 | `counts=` for 27 typed fractions; first width within 5% for -23% |
| blunt_plate | 69,872 -> 82,043 | 31.38 (4.71) -> 20.29 (3.61) | 10 / 0 -> 15 / 0 | open base corners and the blunt-base fill in place of kites |
| naca4412 | 135,191 -> 113,484 | 69.56 (11.24) -> 38.62 (3.86) | 0 -> 24 / 0 | by hand for the first time, in 11 minutes; the engine's best is 205,506 / 45.12 (7.32) |
| three_cylinders | 45,261 -> 49,319 | 37.02 (9.27) -> 32.71 (8.54) | 0 -> 0 | the pair limit in the shape step (here the default shape step left 9 jumps above 5) |
| high_lift | 219,594 -> 332,467 | 68.30 (14.12) -> 69.08 (22.67) | 68 / 0 -> 121 / 46 | 2118 blocks for 1822; no global polish (it did not finish in 10 minutes); a two-ended strip from the main trailing edge |

Over the nineteen: cells summed 1,403,061 -> 1,534,311 (+9.4%); without
`high_lift` 1,183,467 -> 1,201,844 (+1.6%). Mean of the averages 7.37 ->
7.10; without `high_lift` 6.99 -> 6.23. The maximum is lower on thirteen,
equal on two and higher on four (`bluff_ground` +11.0, `splitter_cylinder`
+5.3, `high_lift` +0.8, `cylinder_channel` +0.1). Jumps above 5 summed
3 -> 46, all of them `high_lift`'s. Cases meeting every target: 18 -> 18
(not the same eighteen).

What the reports tie to a tool of batches 12 and 13:

- **Cap boxes** (`cap_y`): used wherever a brief fixes a cell size in a
  region - `ellipse_incidence`, `orifice_channel`, `cavity_channel`,
  `splitter_cylinder`, `triangle` - each in one `size` run, with no script
  and no hand count. `cavity_channel` came out at half its reference.
- **The blunt-base fill** (`failures.md`): `triangle` 28.2 -> 17.4 and
  `blunt_plate` 31.4 -> 20.3, "worked as written at the first attempt";
  `high_lift`'s flap likewise.
- **`counts=`**: `tube_pair_periodic`, `flap_airfoil`, `cascade`,
  `splitter_cylinder`, `blunt_plate`, `high_lift`. No trial typed
  fractions for equal counts.
- **The ring note's pairs**: open corners' spoke ends were read from the
  note without probing (`flap_airfoil`, `blunt_plate`, `tandem_airfoils`).
  It does not say which station a `normals=` or `points=` entry became
  (`ellipse_incidence`, `eccentric_annulus`: a listing by hand each).
- **`to_y=V`, `stations=V`**: `flap_airfoil`, `tandem_airfoils`.
- **`facing=`**: tried by no one. `eccentric_annulus` and `cascade` read
  what it does and named it as not what they need ("one wall's normal",
  "serves one wall only"); `bump_channel` asked for one that understands
  a mitred corner.
- **The real-count reports of the guards**: no trial delivered a face
  above 70 that `size` or `smooth` had reported absent.

## Gaps reported, by case

Costs are the agents' own estimates.

### ellipse_incidence (new)
1. The ring note does not map `normals=` angles to vertex IDs (2 min, a
   listing).
2. `count=` leaves each rounded end two blocks; stations by normal every
   25 degrees instead.
3. `far_ratio` left 24 columns 0.04 wide to y = ±8.
4. `apply -c` (the agent's own error; the flag does not exist).

### tube_pair_periodic (new)
1. A `new --box` placeholder on a grid cell was reused by `to_box` and
   left the grid non-conformal (2 min; Step 3's advice on the placeholder
   is incomplete).
2. **A `normals=` spoke 0.0013 off its box line: 4 `move_vertex`.**
3. **A `to_box` corner $10^{-12}$ off: the patch selection by box left 2
   edges.**
4. The cyclic pair's mismatch shows only at `check`; `validate` is not
   named in Step 3 for periodic sides (3 min).

### flap_airfoil (new)
1. **The slot entrance ahead of the flap nose has no recipe**: two layouts
   failed (78 and 61 faces above 70), 15 min, then a fill down to a free
   vertex below. `add_fill` built a 16-degree block corner without a note.
2. **`far_ratio`'s far row at y = 7.999999999999999: the patch selection
   by box left 13 edges** (3 min).
3. Vertex IDs shift when a station count changes (2 min).
4. Sixteen thin columns carried to the top.
5. `size` left the main 13 percent fine (a raise through band spokes);
   `set_first_width` made it exact.

### orifice_channel
1. The skill gives no station offset beside the plate: three compared
   (70,340 / 64,052 / 67,130 cells).
2. Step 6 does not say which `opposite=` (`follow` on an orthogonal
   H-grid; `keep` tilts by half a degree).
3. Renders: a reference curve with sharp corners is drawn with loops
   (round 5's gap 15, still there).

### eccentric_annulus
1. **A pairing normal to both walls: the agent's own Möbius script again
   (2 min).** `facing=` gives one wall's normal, 21 degrees off the other.
2. The ring note does not say where each station is (1 min).
3. `add_fill`'s "NOT the only reading" for the complementary arc of a
   closed front: a false alarm.

### cavity_channel
1. `size` alone left the bottom wall 7 percent coarse; Step 5 should say
   that a brief requiring the first width ends with `set_first_width`
   (it is in Step 6, which is skipped when there is no polish).
2. The corner screen's smallest cell after `set_first_width` is not named
   by edge.

### splitter_cylinder
1. **A sliver column at the junction mitre**: the mitre's front edge is
   0.0175 long over a wall edge of 0.111; the ring note gives no lengths
   and `add_rows`' thinnest-column note named another (3 min, rebuilt
   with `counts=`).
2. `add_rows`' rows are images of the chain; the text reads as lines at
   x = station (1 min).
3. `apply --quiet` with a here-document: "No commands given" (`--stdin`).
4. `budget=90000` and `keep_counts` both wrecked the first widths
   (-38%..+531%), as the skill warns (3 min).

### elbow_vane
1. The starting box was reused as a grid cell and then removed by habit:
   a hole (1 min; the placeholder advice again).
2. `apply -c` again.
3. The free polish bends the radial lines the brief requires;
   `keep_lines=true` costs 1.8 degrees.
4. **The vane's caps needed a script** (3 min): no station at a turning
   angle round a rounded end; fill sides are straight, so the front arcs
   were set by hand.

### bump_channel
1. Fill corners by ID broke when a station count changed (1 min).
2. **A sliver where a fill line leaves a mitred foot: two rebuilds, 3
   min.** The line from a concave corner's mitred front should keep near
   the mitre's direction; the station on the other wall has to be placed
   for it.
3. The shape step does not help a symmetric fill.

### triangle
1. `remove_edge` of the base's band block prunes the base's wall
   stations; the recipe's `side1=E1,C1,C2,E2` cannot name them (1 min).
2. **A one-edge base is a two-ended strip from base to outlet: raised 69
   -> 249 (+12,600 cells), the split refused for the far outlet piece.**
   A mid-base station cured it (2 min).
3. IDs shift on a rebuild (1 min).

### nozzle
1. The shape step does not help; the row could say skip.

### cylinder_channel
1. `add_band bbox=` picked up the unassigned wall with the front: "not
   one connected chain" (1 min).
2. **The shape step tilts the wake's start vertex and shears the 1.9-long
   wake blocks whole** (average 5.09). A station one box width downstream:
   2.66 (3 min).
3. `budget` cannot switch spending off.

### u_bend
1. **A front vertex 0.0013 off at the tangent joint: 2 `move_vertex`**
   (third round running).
2. `to_box` on a half square needs a station facing each box side too
   (refused; 1 min).
3. IDs shift when a station is added (1 min).

### y_junction
1. The recipe's station across the branch makes a fill corner a
   straight-angle point (refused; 2 min).
2. **A strip fine at opposite ends on different members: 81,200 cells
   (1.54 times the reference) until `split_edge a_1-a_2 0.5` gave 54,960.
   `size` did not list it as refused** (3 min).
3. The fill's default facing stations bunched the corner's thin rows on
   the far side (1 min).

### airfoil_tunnel
1. **The upper trailing-edge spoke (`fan=false`) is normal to the suction
   side, 15 degrees off the cross-wake line: the first wake block is
   sheared.** `move_vertex r_42 x_of=r_0 carry_curves=true`: average 5.56
   -> 4.70 (5 min). The rule is only in `failures.md`, under layout
   scripts.
2. The chained shape, size and polish went to the background (3 min idle).
3. `budget=100000` gave 0.97 times the reference's cells with the average
   over its limit; the excess went into the wake band's cross count.

### bluff_ground
1. **`to_box`'s corner snapping is not documented: two layouts lost** (the
   wake line bent; corners of 23 and 157 degrees), 7 min.
2. **A long single geometric series: 388,582 cells (2.7 times the
   reference) until transition stations cut it to 205k** (4 min).
3. **A two-ended diagonal at the gap entry: jump 5.65, the split refused
   for a far sliver, `keep_counts` 5.81; a hand `split_edge v17-v28 0.5`
   removed every jump** (5 min).
4. The default budget lands at 1.59 times the reference.

### cascade
1. A cross line from the stagnation strip's vertex to the periodic side:
   25 faces above 70 until the paired station moved upstream (3 min).
2. **The stagger's shear is built into a single-row periodic H-layout**:
   the average is about 26 whatever is done (5 min of analysis). A
   helical (index-offset) periodic pairing has no operator.
3. The shape step pinched the wake band and the strip: `hold_box` (the
   skill covered it; 3 min).
4. The periodic station's x by a one-line script (the mean of two
   fronts): "`facing=` serves one wall only".

### blunt_plate
1. The base corner's fine column is carried to y = ±6 (40 cells, 0.06
   wide) and the base wake strip is raised (47 -> 173): 20k of the 27k
   cells spent.
2. Four two-ended strips not split (the note says so).
3. A regeneration went to the background (3 min idle).

### tandem_airfoils
1. **The two-ended column between the airfoils: 228,418 cells (1.93 times
   the reference), the split refused for a far piece 0.346 long where
   0.351 was wanted; two hand `split_edge`: 127,740** (4 min).
2. The pocket between the airfoils has no recipe (5 min).
3. `far_ratio` did nothing on three of the four row sets.
4. First widths 15 to 27 percent fine after `size` and the polish (band
   spoke strips kept their shared ratio); `set_first_width` made them
   exact.

### naca4412
1. **Route rule 2 ("a sharp airfoil in a C far field: the engine first")
   points away from the cheaper and better route.** By hand: 113,484
   cells, 38.62 (3.86), 11 minutes; the engine's best 205,506, 45.12
   (7.32).
2. `add_rows` ends only on a straight line: the far row's end was 0.0035
   off the inlet arc's end (2 `move_vertex`).
3. `far_ratio` left ten columns 0.05 wide to y = ±15.

### three_cylinders
1. **`set_boundary_by curves=cylinder_b` took 5 of the 6 wall edges**: it
   missed the edge across the closed curve's closing segment (2 min).
   Reproduced by me: `verify-round6/sbb/cons.txt`, edge `v20-v25` of the
   second ring (from the 45-degree station to the 344-degree one); the
   other two cylinders' edges across their seams are taken.
2. **Spokes 0.0003 to 0.0011 off their aim: 8 snaps (3 min)**, and the
   refusal's suggested `move_vertex` named the wrong vertex.
3. `apply`'s error counts commands, not the file's lines (1 min).

### high_lift
1. **The two-ended strip from the main trailing edge's lower spoke through
   the slot strip and the wake rows: 31 of the 46 jumps above 5 and the
   main's first width 41 percent fine.** `size` kept the strip on one
   ratio ("(jump)"). A hand `split_edge` of the slot crossing gave 9 jumps
   and first widths within 1 percent, but folded a fill block at a
   202-degree corner (9 negative volumes); `repair_block` did not finish
   in 300 s; the unsplit state was delivered (15 min).
2. **Run time**: the global polish did not finish in 590 s at 2118 blocks
   and 332k cells; each `size` took 6.5 minutes (three runs). All
   polishing was local (`smooth blocks=`), and the shape step was left
   out.
3. `far_ratio=2` left the band columns 0.005 to 0.02 wide to y = ±15: one
   column at aspect 1263, a rebuild and six hand gradings (12 min).
4. A slot fill refused (stations not facing): the main ring re-stationed
   and the region cut into three fills (6 min).
5. `set_first_width` was not used.

## Recurring gaps, round 6

| # | gap | trials (cases) | cost |
|---|---|---|---|
| 1 | **A two-ended strip** (fine at both ends, or at opposite ends on different members): `size` raises its count through everything it crosses, or leaves a jump, and refuses the split for a far piece a little short of one cell - or does not name the strip at all | 8 trials (`triangle`, `y_junction`, `bluff_ground`, `tandem_airfoils`, `high_lift`, `splitter_cylinder` 6b and 6c; `blunt_plate` informational) | 12,600 to 100,000 cells until a hand `split_edge` (2 to 5 min each); on `high_lift` the round's missed targets |
| 2 | Spokes and rows a little off where they were aimed: a `normals=` or tangent-joint spoke 0.0003 to 0.0013 off its line; a `to_box` corner or a `far_ratio` far row $10^{-12}$ to $10^{-15}$ off, which a box selection then misses | 5 (`tube_pair_periodic` both kinds, `u_bend`, `three_cylinders`, `flap_airfoil`) | 14 snaps; 15 patch edges missed; 1 to 3 min each |
| 3 | A pairing of stations across a passage normal to both walls; a pairing that respects a mitred corner | 3 (`eccentric_annulus`, `cascade`, `bump_channel`) | a script twice, two rebuilds once; 2 to 3 min |
| 4 | Thin columns carried to the far boundary that `far_ratio` does not widen | 6 (`ellipse_incidence`, `flap_airfoil`, `tandem_airfoils`, `naca4412`, `blunt_plate`, `high_lift`) | cells; on `high_lift` aspect 1263 and 12 min |
| 5 | Names that do not survive a rebuild or are not given: IDs shift with a station count; the ring note does not say which vertex a `normals=`/`points=` station became | 6 (`flap_airfoil`, `bump_channel`, `triangle`, `u_bend`; `ellipse_incidence`, `eccentric_annulus`) | 1 to 2 min each |
| 6 | The shape step moves what should stay (a wake's start vertex, a wake band, a strip, a box) | 4 (`cylinder_channel`, `cascade`, `tandem_airfoils`, `elbow_vane` in the polish); six hand builds left the step out, four of them after comparing with it (`tube_pair_periodic`, `eccentric_annulus`, `bump_channel`, `nozzle`) | 3 min each; `hold_box` where the skill names it |
| 7 | Run time on a large session and commands going to the background | 5 (`high_lift`; `airfoil_tunnel`, `blunt_plate`, `tandem_airfoils`, `bluff_ground` idle 3 min each) | on `high_lift` no global polish and a third of the cap |
| 8 | The first width after `size` (a strip on its shared ratio, a raise through spokes) | 6 (`flap_airfoil`, `cavity_channel`, `orifice_channel`, `tandem_airfoils`, `naca4412`, `triangle`) | one `set_first_width` where used; `triangle` -19% and `high_lift` left |
| 9 | Layouts without a recipe: the slot entrance ahead of a flap nose, the pocket between tandem bodies, a rounded cap in a polar grid | 3 (`flap_airfoil` 15 min, `tandem_airfoils` 5, `elbow_vane` 3) | the largest single cost outside `high_lift` |
| 10 | The route table's rule 2 sends a sharp airfoil to the engine | 1 (`naca4412`), but the engine's last case | half the cells, a lower maximum, by hand |
| 11 | The placeholder box (`new --box`) reused or removed wrongly | 2 (`tube_pair_periodic`, `elbow_vane`) | 1 to 2 min |
| 12 | An open trailing-edge spoke (`fan=false`) normal to its surface, not across the wake | 1 (`airfoil_tunnel`) | 0.9 degrees of average, 5 min |
| 13 | `to_box` moving a box corner onto a spoke, unannounced | 1 (`bluff_ground`) | two layouts, 7 min |
| 14 | `set_boundary_by curves=` missing the edge across a closed curve's closing segment | 1 (`three_cylinders`), a defect, reproduced | 2 min |
| 15 | Small text: `apply -c` (two trials, third round), `apply`'s error counting commands, `--stdin`, `validate` for cyclic pairs in Step 3, which `opposite=`, a long single series (`bluff_ground`), the recipe's base stations (`triangle`) | - | 1 min each |

## Spread between agents: `splitter_cylinder`, three trials

Round 5's one trial missed the jumps (three above 5, worst 9.6). Round 6:
three layouts again (a grid far field, the row's rows with junction
fills, a near-field box), and **all three meet every target**: maxima
48.87 / 43.15 / 47.91, averages 12.7 / 8.6 / 11.3, first widths within 6
percent, cells 1.14 : 1 : 1.05 (120,409 / 105,653 / 110,559). Two of the
three end with their worst jump at 4.9996 and 4.9997 - the polish's
ceiling, at the junction - and one at 2.82.

What the three have in common is the junction mitre's strip. Trial 6 met
it as a sliver column (a front edge 0.0175 long over a wall edge of
0.111); 6b as a spoke fine at the wall and, through a shared strip, fine
at the front - the first `size` left the first width there 17 times too
coarse and 13 jumps above 5, and one `split_edge` of the spoke before
`size` cured both for 7,700 cells; 6c as one raise of the band's normal
count round the whole body (23 -> 49, +20,706 cells) bought for
continuity at the mitre, which a lower budget does not buy (8 jumps above
5 at `budget=90000`). It is the round's gap 1 again: a strip fine at both
ends, which a split cures and `size` does not make.

Also from 6c: `shape_pair_ratio=2.5` made this layout worse (2 jumps
above 5, worst 8.38) where Step 4 gives the splitter as the case it
helps; `three_cylinders` found the opposite of Step 4's other example.
The limit's effect follows the layout, not the case.

Other gaps of the repeats: the row's "carry each wall's front along the
other wall by `add_rows ... conform`" is hard to turn into commands (6b:
5 min; a fill with a corner by `add_vertex x_of= y_of=` instead);
`to_box` from the ring front folds at the concave junction and the
refusal's advice, a station, cannot cure it (6c: 3 min); `add_rows`
copies the chain's shape, so row lines over the plate are copies of the
cylinder's arc and kink at the plate's spokes (6c); a wake band without
`spread` because the far rows take absolute stations (6b: aspect 888).

## Reading

1. **Batches 12 and 13 did what they were for.** A cell size in a region
   is one option in five trials and no script; the blunt-base fill took
   two cases' maxima down by 11 degrees; equal counts, vertex-named rows
   and the ring note were used as written. Outside `high_lift` the
   eighteen common cases have the same cells (+1.6%), a lower mean
   average (6.99 -> 6.23) and a lower maximum on thirteen.
2. **Unseen cases keep working**: three new geometries - a body at
   incidence, a periodic pair, a two-element airfoil with a slot - met
   every target at the first trial with no engine.
3. **The engine has no case left where it is the better route.** The
   NACA 4412 by hand, from the skill's example, has half the cells of the
   engine's best session and a lower maximum and average.
4. **Two-ended strips are now the largest loss**, in cells on five cases
   and in targets on the sixth. Each time the remedy was one
   `split_edge` that `size` had considered and refused for a piece a few
   percent short of one cell, or had not considered.
5. **`high_lift` went back outside the targets.** Round 5's agent met
   them in 59 minutes at 1822 blocks; this one built 2118 blocks, could
   not run the global polish inside the time, and delivered a strip it
   knew how to cure but could not cure safely. The case is at the limit
   of what the chain does in the time given; its result depends on the
   agent's block count as much as on the tool.
6. **What agents still cannot say**: a pairing normal to both walls
   (again a script, and `facing=` as built is declined by the cases it
   was built for), and where exactly a spoke or a row ends (five builds,
   every round since the third).
7. **Not better**: `bluff_ground` (another layout, 11 degrees worse and a
   third more cells; the reference's layout is in no row), the cascade's
   average (26: the layout's, not the tool's).

## Candidates for the next fix batch

By the rule of the benchmark (two unrelated cases, or a general
primitive), in the order of what the round lost to them:

1. **Two-ended strips in `size`** (gap 1; six cases): the split's
   refusal for a far piece within a few percent of one cell, strips fine
   at opposite ends on different members (not detected today), and the
   strip named in the note with the `split_edge` that would cut it when
   the split is refused. Under a rule on the known inputs, stated before
   measuring.
2. **Exactness** (gap 2, the part that moves nothing measurable): a
   `to_box` corner, a far row and a row on a target line take the line's
   coordinate exactly. Then, separately and under its own rule, **the
   spoke's direction at a tangent joint and at a `normals=` station**
   (batch 13's item 2: 33 of 34 builds held; the rule failed on one
   build's maximum).
3. **The pairing normal to both walls** (gap 3; three cases, two rounds):
   the equal-angle pairing measured in batch 13's follow-up, with the
   stations beside a wall's corner left to the corner's own. ROUND5.md
   named this condition for reopening it.
4. **`far_ratio` on a run of equal thin columns** (gap 4; six cases, two
   rounds): batch 13's `far_min` failed its rule by tilting the column
   lines; what the cases ask is fewer columns at the far row (columns
   merged), not wider ones.
5. **Names** (gap 5; six cases): the ring note giving each `normals=` and
   `points=` station's vertex; far-row end names that do not depend on
   the station count.
6. **The `set_boundary_by` defect** (gap 14), with its reproducer.
7. **Skill text**, within the caps: route rule 2 and a hand C-grid row
   for a sharp airfoil (gap 10); the slot entrance and the tandem pocket
   as failures.md recipes (gap 9); the placeholder box (gap 11); the
   cross-wake direction of an open trailing-edge spoke (gap 12); `to_box`
   snapping (gap 13); the wake's start station (`cylinder_channel`); the
   base's two edges in the blunt-base recipe; `set_first_width` as the
   last step in Step 5; the small items of gap 15; Step 4's examples for the pair limit without
   case names (two trials found each the opposite); the splitter row's
   junction as commands.
8. **Run time above 2000 blocks** (gap 7): what the skill should say
   (local polish, a block budget for the layout), and whether the
   global polish can report progress or take a time limit and return its
   best state.

Not taken, by the rule: a helical periodic pairing (`cascade` only), a
station at a turning angle round a rounded end (`elbow_vane` only),
`budget` switching spending off (`cylinder_channel` only), `add_rows`
ending on a curve (`naca4412` only).

## The reference table

`REFERENCE.md` rewritten by its rule (among the trials meeting the
targets, the fewest cells of those within 5 degrees of the lowest maximum
known). New rows: `ellipse_incidence`, `tube_pair_periodic`,
`flap_airfoil`. Rows replaced: `cavity_channel` (37,010), `bump_channel`
(39,096), `triangle` (32,930 at 17.4), `blunt_plate` (82,043 at 20.3: the
69,872-cell build's 31.4 is now more than 5 degrees above the lowest),
`naca4412` (113,484, by hand), `cascade` (44,664), `eccentric_annulus`
(24,030), `splitter_cylinder` (105,653 at 43.2, trial 6b) and
`airfoil_tunnel` (120,416, round 5's: round 6's 26.54 puts the
101,666-cell build's 31.64 a tenth of a degree outside the 5). The other
ten stand.

## Fixes after round 6

### Batch 14: two-ended strips in `size`, exact coordinates, the closing segment, notes, skill text (commits 1948a6e, cf7eb46, ef72ddf, ef0aad8, 4e29a56, 41c876c, a6a1b8e)

Six local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round6a/BRIEF.md` (about 650k tokens, eleven
hours of wall time, most of it the corpus runs on two cores), and one of
mine (a6a1b8e). The last batch of the loop: the user decided on Nov 23
2026 to wrap up after it. Every number below is from my own runs
(`/tmp/garbage/blockdrawer-bench/verify-batch14/`: `rows`, `conscmp.txt`,
`note_rows`) unless it says "developer's".

- **Two-ended strips** (ef0aad8 the note, 4e29a56 the behaviour; gap 1 of
  this round). Three changes to the field's split rounds, none to the
  continuity rounds: a strip whose members are fine at *opposite* ends is
  found (`_opposed_strips`: the smallest wanted size over length at 41
  fractions along the oriented strip, peaking 2.5 times above both ends)
  and split like a two-ended edge; a cut is refused for a piece under half
  a wanted cell (a whole cell before), or when it folds a block; a round
  is judged against the state in which the strips found ask only their
  profiles' cells, not against the one-series fit the split removes (the
  Y-junction's cut, the one the trial made by hand, was undone "for
  raising the jump cost" before).
  **The rule of the brief passes, all three terms.**
  - Term 2, the trials' builds without their hand splits, through their
    own chains, 49ffc0d -> now: `y_junction` 6 81,200 cells, 31.85 (9.76)
    -> 50,624, 24.76 (7.48) (the hand split gave 54,960);
    `bluff_ground` 6 197,801, 55.47, two jumps above 5 -> 183,837, 51.88,
    none; `tandem_airfoils` 6 228,418, 40.00, two jumps above 5
    (developer's) -> 143,960, 35.73 (6.46), none (the hand splits:
    127,740). `splitter_cylinder` 6b is unchanged (82,067 cells, 11 jumps
    above 5, first width far off): its mitre spoke is not two-ended in the
    field's state, the hand split stays the remedy and `failures.md` says
    so. Three of four reach their term.
  - Term 1, the 44 delivered builds: 40 identical, four change and all
    for fewer cells (developer's count; I reran the four on both trees and
    sixteen of the others): `cavity_channel` 5 95,879 -> 71,850 (0
    degrees both), `y_junction` 5b 80,062 -> 56,869 (24.86 -> 24.93, the
    average 9.26 -> 10.21), `y_junction` 5c 52,599 -> 45,338 (25.83 ->
    24.63; jumps above 2.5 2 -> 5, none above 5), `elbow_vane` 6 44,048 ->
    36,267 (31.17 -> 31.40). First widths within 5 points everywhere.
    Unchanged in my runs, old against new or against the delivered
    numbers: `blunt_plate` 5 and 6, `splitter_cylinder` 5 and 6c,
    `naca4412`, `triangle`, `three_cylinders`, `flap_airfoil`,
    `tandem_airfoils`, `cascade`, `u_bend`, `tube_pair_periodic`,
    `ellipse_incidence`, `nozzle`, `cylinder_channel`, `airfoil_tunnel`
    (all trial 6).
  - Term 3, `high_lift` 6 through its `size` line: as 49ffc0d (332,467
    cells, 119 / 46 jumps, worst 19.46), no folded cell, and the strip the
    trial split by hand is named second in the note (`split_edge A-fw_0
    0.4`, refused for a quarter-cell piece; 80 pairs above 5 at its
    members). It passes by its second clause. The developer changed the
    note's order (most pairs above 5 first) after seeing that the strip
    came fifth by peak ratio; that changes text, no decision.
  - Two designs were tried and dropped before this one, under the same
    rule (developer's, in the README): exempting opposed members in the
    solved state itself (high-lift: six jumps above 5 gained), and
    continuity cuts judged by their round alone (it reached the splitter's
    term but raised `blunt_plate` 5 by 11.6 degrees and `tandem` 6 by
    12.7).
  - **What this does not show.** The behaviour was designed and measured
    on the same 44 builds; no held-out round follows it. The four changes
    are all in the direction the trials' hand splits went.
  - **The note** (a6a1b8e, mine). As handed back it named every strip
    left uncut and fired on 17 of 44 builds (developer's count), 12 of my
    25 reruns - most of them sessions meeting every target: strips
    stranded after spending with no pair above 5, or whose split a round
    had tried and undone (`three_cylinders` 6: one such, with the
    `split_edge` to make it anyway). It now shows a strip only when its
    members meet a pair above 5, or when it is two-ended in the field's
    state and no round undid it; `uncut_strips` lists all. Of thirteen
    builds with strips listed, rerun through `size`: fires on six
    (`high_lift` 6, `tandem_airfoils` 6, `blunt_plate` 5,
    `splitter_cylinder` 6, 6c and 6b without its hand split), silent on
    seven, cells as before.
- **Exact coordinates** (cf7eb46; gap 2's exactness part). `to_box` spoke
  ends take their side's coordinate, `add_rows` rows their station or
  target line (with `spread`, `far_ratio`, `far_min` too), `xs=`/`ys=`
  stations their value. Construction of the 82 builds of rounds 3 to 6
  that start from a box, 49ffc0d against now: same vertex IDs, blocks,
  counts, edge types and patches on all; the largest move 5.8e-13 of the
  extent (`cylinder_channel` 3), control points none. `bluff_ground` 4
  stops at the same command on both trees, as since batch 11. Developer's:
  with exact selection boxes `tube_pair_periodic` leaves no edge
  unassigned (4 before), `flap_airfoil` none (13).
- **`set_boundary_by curves=` across the closing segment** (1948a6e; gap
  14). The block's side of the closed curve was read at the mean of its
  corners, which a band block spanning 61 degrees of a circle has inside
  the circle; now at the centre of its transfinite map. My reproducer
  (`verify-round6/sbb/cons.txt`): `cylinder_b` 5 edges on 49ffc0d, 6 now;
  the other two unchanged at 8 and 6.
- **Notes and messages** (ef72ddf): the missed-end refusal names the
  vertex that is off its line (three cylinders: `move_vertex v63 y=-0.65`,
  the trial's own line, for a box corner before); the ring note maps each
  `normals=`/`points=` entry to its vertex; `add_fill` notes a built block
  corner within 20 degrees of 0 or 180 (6 of 173 fills, developer's
  count); `apply` errors give the file's line, `-c` and a here-document
  without `--stdin` say how commands are given; a box over separate chains
  names each piece. Not rerun by me beyond the suite's tests.
- **Skill text** (41c876c). Route rule 2 sends a sharp airfoil in a C far
  field to the hand C-grid; recipes in `failures.md`; Steps 3 to 6 and the
  large-session line; rows refreshed; the three new cases' rows in
  `examples/README.md` because `SKILL.md` had no room (249 lines, 20,028
  characters). Numbers I reran whole: `naca4412` 6 113,484, 38.62 (3.86);
  `cascade` 6 44,664, 54.41 (26.45); `nozzle` 6 11.78; `three_cylinders` 6
  32.71; splitter 6c 47.91; `cylinder_channel` 6 average 2.66, 5.09
  without its x = 0.5 station; `airfoil_tunnel` 6 average 4.70, 5.56
  without its `move_vertex` (line 11); `high_lift` 6 to the end of its `size` line 11.5 and
  13 minutes on two cores (the skill's 7.7 minutes for `size` alone is the
  developer's).
- **Checks**: suite 729 tests OK on 41c876c, 730 OK with a6a1b8e (307 s);
  `tests/test_skill.py` OK; working tree clean; nothing left in the case
  folders; nothing left running.

### Where the loop stops

After this batch the branch is pushed and merged into `master` at the
user's word. Left open, in the order I would take them if the loop were
taken up again: the high-lift case (run time above about 1500 blocks, a
recipe for its far field, three repeat trials); the splitter's mitre
spoke, fine at the front only after spending; the construction items of
this round's candidates not in batch 14 (spokes off their aim under a
restated rule, the pairing normal to both walls, `far_ratio` on runs of
thin columns, names stable under station counts). A round 7 would also be
the first held-out test of batch 14's splitting.
