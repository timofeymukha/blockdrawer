# Benchmark round 5 (Nov 21 2026)

Code: `review-followups` at 0a83d82 for the first three trials and f8e7dfd
for the rest (the difference is the batch digest's text only; no session
differs): batches 10 and 11 of `ROUND4.md`, "Fixes after round 4" - the
shear guard as the default of `grading=field`, `set_first_width`, the
per-block average in `quality`, the overlap check, the `add_fill` and
`add_rows` corrections, unhooked fronts, `near=`, `points=`, `between=`,
and the skill's rows for round 4's cases. Protocol: `TRIAL.md` as in round
4, deliverables in `CASE/trial5/`. One fresh Opus agent at high effort per
case, one at a time, two cores. Three new held-out cases
(`orifice_channel`, `tandem_airfoils`, `eccentric_annulus`: no row in the
route table, no example, no reference row); round 4's three new cases now
have rows and are no longer unseen. checkMesh numbers are from my own
reruns (`bin/verify CASE trial5`). `y_junction`, the case whose round-4
spread came from the agent's topology, was run twice more (`trial5b`,
`trial5c`).

## Results

| case | route | blocks | cells (field) | x ref | max non-orth (avg) | skew | aspect | jumps >2.5 / >5 (worst) | first width | time | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| orifice_channel (new) | one `add_grid` (the cavity row); `size`, two counts by hand for the jet, `set_first_width` | 34 | 56,208 (48,508) | - | 3.95 (0.33) | 0.04 | 31 | 0 / 0 (1.20) | exact | 6 min | 141k |
| tandem_airfoils (new) | two rings `fan=false`, wake rows with `spread_about`, a pocket of rows between the rear ring and the front wake, far field by rows (`far_ratio=2`), two fills; no shape step; polish with `hold_line` | 1058 | 118,320 (96,375) | - | 40.23 (4.37) | 0.64 | 282 | 18 / 0 (4.38) | -5%..+5% | 16 min | 153k |
| eccentric_annulus (new) | ring on the cylinder, ring `side=in` on the outer wall, stations paired on bipolar lines (a 30-line script for the station points), two half-annulus fills; default chain, `set_first_width` | 96 | 24,297 (20,559) | - | 11.74 (3.56) | 0.24 | 21 | 0 / 0 (2.28) | exact | 6.6 min | 127k |
| cavity_channel | one `add_grid`; `size`, `set_first_width opposite=follow` | 29 | 95,879 (95,879) | 1.33 | 0 (0) | 0 | 25 | 0 / 0 (1.24) | exact | 4.7 min | 127k |
| splitter_cylinder | the route table's row: ring with mitred junctions, wake rows, carries split in two with a fill over the cylinder's back; shape with `shape_pair_ratio=2.5` | 722 | 83,763 (56,067) | 0.92 | 43.58 (9.32) | 1.14 | 607 | **26 / 3 (9.62)** | -8%..+5% | 29 min | 181k |
| elbow_vane | the agent's own layout (not the row's polar grid): open ring per wall, ring on the vane, a fill per passage, boxes round the caps; no shape step; `set_first_width` | 78 | 47,394 (39,890) | 1.33 | 40.34 (9.11) | 1.37 | 25 | 2 / 0 (2.64) | exact | 21.6 min | 211k |
| bump_channel | open ring per wall with stations paired by hand and one a band height either side of each foot, one fill; default chain | 88 | 44,720 (37,296) | 0.67 | 33.02 (11.28) | 0.68 | 43 | 0 / 0 (2.50) | -5%..+6% | 9.4 min | 141k |
| triangle | ring with fans at all three corners, wake and inlet rows, far field by rows (`far_ratio=2`), two fills; shape with the pair limit; `budget=36000` | 324 | 35,813 (23,940) | 1.13 | 28.17 (8.27) | 0.90 | 84 | 6 / 0 (3.26) | -5%..+15% | 16.8 min | 149k |
| nozzle | open ring per wall, one fill; `size`, polish | 36 | 14,534 (12,506) | 0.98 | 11.79 (2.79) | 0.45 | 26 | 0 / 0 (1.60) | -5%..+5% | 2.6 min | 113k |
| cylinder_channel | ring, band to box, grid; default chain with the wake centreline held | 28 | 16,720 (16,176) | 1.00 | 34.93 (4.45) | 0.58 | 26 | 0 / 0 (1.74) | -5%..+5% | 3.8 min | 119k |
| u_bend | open ring, O-layer on a half square, two grids; **default chain, no hand line** | 20 | 23,754 (22,050) | 1.00 | 37.45 (2.97) | 0.47 | 26 | 0 / 0 (1.87) | -5%..+5% | 3 min | 121k |
| y_junction | three open rings, the open corner a side station of one 2x4 fill; default chain, `budget=80000` | 32 | 63,360 (53,289) | 1.05 | 24.85 (9.93) | 0.74 | 27 | 0 / 0 (1.95) | -5%..+5% | 9.7 min | 156k |
| airfoil_tunnel | ring `fan=false`, wake rows, rows to the walls (`far_ratio=2`, the last row the wall band), two fills; default chain with `hold_box` on the wake band | 296 | 120,416 (93,014) | 1.18 | 27.22 (4.26) | 0.58 | 39 | 0 / 0 (2.50) | -5%..+5% on all three walls | 17.7 min | 143k |
| bluff_ground | open ring between the base corners, a grid downstream with the base as its side, ground band by rows, grid and fill; no shape step; `budget=200000`; one count and `set_first_width patch=ground`; polish with `hold_line` | 206 | 141,828 (133,266) | 0.86 | 40.73 (3.67) | 0.54 | 523 | 0 / 0 (2.25) | ground exact; body -5%..+28% | 11.5 min | 150k |
| cascade | ring `fan=false` with equal counts per side (27 typed fractions), wake rows, stations with `offset=`, five fills; default chain with the wake lines held | 86 | 46,137 (39,318) | 0.80 | 56.26 (26.32) | 2.68 | 30 | 2 / 0 (2.75) | -23%..-6% | 8.6 min | 171k |
| blunt_plate | ring with kites at the base corners, wake rows, far field by rows (`far_ratio=2`), two fills; default chain, `keep_counts`, `set_first_width` | 441 | 69,872 (53,525) | 1.08 | 31.38 (4.71) | 0.89 | 99 | 10 / 0 (3.91) | exact | 12.9 min | 139k |
| naca4412 | engine C-grid (1 run, 8 min) + `size` + polish | 250 | 135,191 (133,678) | 0.66 | 69.56 (11.24) | 1.01 | 111 | 0 / 0 (2.50) | -5%..+5% | 14.8 min | 138k |
| three_cylinders | by hand (the row's second route): ring and `to_box` per body, one H-grid, 12 vertex snaps; default chain, `set_first_width` | 76 | 45,261 (35,751) | 0.92 | 37.02 (9.27) | 0.93 | 21 | 0 / 0 (2.37) | exact | 7.8 min | 146k |
| high_lift | operators only: three rings, cove and slot fills, a band over the main, wake rows, a 32x2 fill, far field by rows; no shape step; two hand gradings for one jump | 1822 | 219,594 (196,124) | 0.75 | 68.30 (14.12) | 1.31 | 859 | 68 / 0 (5.00) | flap -11%..+4%; main -9%..+5%; slat -11%..+5% | 59 min | 275k |

**Eighteen of nineteen meet every target**: `Mesh OK`, no face above 70
degrees, skewness under 4, aspect ratio under 1000, no jump above 5, cells
and average inside the economy limits. `splitter_cylinder` misses the
jumps (three above 5). `high_lift` meets every target for the first time
in five rounds (its worst jump is 4.9999, at the polish's ceiling, and
one jump of 8.1 took two hand gradings). The three new cases meet every
target at the first trial, in 6, 16 and 7 minutes. Total: 262 minutes,
2.90M tokens; the sixteen cases of round 4 took 233 minutes and 2.48M
tokens (round 4: 235 minutes, 2.52M).

Repeats:

| trial | blocks | cells | x ref | max (avg) | jumps >2.5 | first width | junction | budget | time |
|---|---|---|---|---|---|---|---|---|---|
| y_junction 5 | 32 | 63,360 | 1.05 | 24.85 (9.93) | 0 | within 5% | one 2x4 fill, the open corner a side station | 80,000 | 9.7 min |
| y_junction 5b | 28 | 80,062 | 1.33 | 24.86 (9.26) | 0 | within 5.5% | five-sided fill, two splits | default | 5.6 min |
| y_junction 5c | 31 | 52,599 | 0.87 | 25.83 (7.82) | 2 | within 5% | two four-sided fills, three splits | 60,000 | 11.2 min |

Twenty-one trials in all: 279 minutes, 3.19M tokens.

## Round 4 against round 5, the sixteen common cases

| case | cells | max (avg) | jumps >2.5 / >5 | first width | what changed |
|---|---|---|---|---|---|
| cavity_channel | 71,850 -> 95,879 | 0 (0) -> 0 (0) | 0 -> 0 | exact -> exact | three hand gradings -> one `set_first_width`; band rows of 0.1 cost the cells |
| splitter_cylinder | 91,114 -> 83,763 | 52.58 (15.13) -> 43.58 (9.32) | 45 / 0 -> 26 / 3 | +21% -> -8%..+5% | the row's pattern, no folding front (batch 11); aspect 954 -> 607; three jumps above 5 at the wake band's outer edge |
| elbow_vane | 35,575 -> 47,394 | 33.05 (5.54) -> 40.34 (9.11) | 0 -> 2 / 0 | within 5% -> exact | another agent's layout; five variants, none as good as round 4's |
| bump_channel | 66,576 -> 44,720 | 33.21 (17.31) -> 33.02 (11.28) | 0 -> 0 | exact -> within 6% | stations beside the feet, found through the per-block average (batch 10) |
| triangle | 36,464 -> 35,813 | 28.45 (9.46) -> 28.17 (8.27) | 14 / 0 -> 6 / 0 | -8% -> +15% | aspect 599 -> 84; a lower budget (Step 5); base-corner lines 17 degrees off |
| nozzle | 14,878 -> 14,534 | 12.24 (2.71) -> 11.79 (2.79) | 0 -> 0 | exact -> within 5% | the same build |
| cylinder_channel | 19,452 -> 16,720 | 41.31 (4.07) -> 34.93 (4.45) | 0 -> 0 | exact -> within 5% | default sizing in place of strip, a count by hand and `keep_counts` |
| u_bend | 22,914 -> 23,754 | 38.39 (3.25) -> 37.45 (2.97) | 0 -> 0 | within 5% | **no hand line**: round 4's default `size` gave 11.7 here |
| y_junction | 84,741 -> 63,360 | 32.10 (12.66) -> 24.85 (9.93) | 0 -> 0 | exact -> within 5% | the row's recipe; no extra first-width step |
| airfoil_tunnel | 120,620 -> 120,416 | 35.28 (3.93) -> 27.22 (4.26) | 0 -> 0 | airfoil -22% -> within 5% | wake band held by `hold_box` |
| bluff_ground | 165,684 -> 141,828 | 42.84 (5.31) -> 40.73 (3.67) | 9 / 0 -> 0 / 0 | body exact -> +28% at two spokes | 14 hand gradings -> one count and one `set_first_width`; another topology (an open ring) |
| cascade | 63,777 -> 46,137 | 55.12 (28.84) -> 56.26 (26.32) | 2 / 0 -> 2 / 0 | within 5% -> -23% | equal counts per side of the ring; first width not corrected |
| blunt_plate | 68,571 -> 69,872 | 37.13 (3.99) -> 31.38 (4.71) | 14 / 0 -> 10 / 0 | -22% -> exact | `set_first_width`; aspect 378 -> 99 |
| naca4412 | 137,410 -> 135,191 | 60.03 (15.45) -> 69.56 (11.24) | 0 -> 0 | within 5% | band 0.1 for 0.06; average inside the economy limit; maximum 0.4 under 70 |
| three_cylinders | 59,178 -> 45,261 | 42.55 (10.22) -> 37.02 (9.27) | 0 -> 0 | within 5% -> exact | the default shape step in place of the pair limit (Step 4's corrected line) |
| high_lift | 303,173 -> 219,594 | 63.73 (15.55) -> 68.30 (14.12) | 55 / 11 -> 68 / 0 | -31% -> -11% | no jump above 5; skewness 2.49 -> 1.31; 1822 blocks for 802 |

Over the sixteen: cells summed 1,361,977 -> 1,204,236 (-11.6%); mean of
the averages 9.59 -> 8.23; the maximum lower on ten and higher on four
(`elbow_vane` +7.3, `naca4412` +9.5, `high_lift` +4.6, `cascade` +1.1);
jumps above 5 summed 11 -> 3; cases meeting every target 14 -> 15.

What the reports tie to a tool of batches 10 and 11:

- **The shear guard**: no trial of the round reported the field pass's
  shear. `u_bend` ran the default chain and delivered 2.97 with nothing by
  hand; `cylinder_channel` and `y_junction` likewise. In round 4 six
  trials reported it and each lost 2 to 5 minutes or the first width.
- **`set_first_width`** was used in eight trials (`orifice_channel`,
  `eccentric_annulus`, `cavity_channel`, `elbow_vane`, `three_cylinders`,
  `blunt_plate`, `bluff_ground` on the ground, and `high_lift`'s agent
  asked for its like on an interface). Where round 4 wrote 3, 13 and 14
  hand lines, round 5 wrote one. It does not serve a spoke that continues
  as a straight line to the outlet (`bluff_ground`: 51 degrees of tilt
  with `keep`, nine jumps with `follow`).
- **The per-block average** was how `bump_channel` (41.3 -> 33.0, average
  15.9 -> 11.3), `y_junction` 5c (average 10.5 -> 7.8) and
  `cylinder_channel` found which blocks carried the average.
- **The lower budget** (Step 5's new line) was used in five trials. It
  cut cells on four and was rejected on two for what it does to the first
  width or a region's cell cap (below).
- **The route table's new rows**: `three_cylinders` went by hand and beat
  the engine again (37.0 for 67.9); `cavity_channel`, `orifice_channel`
  (through the cavity's row) and `splitter_cylinder` followed theirs;
  `elbow_vane` did not and came out worse than the row.
- **The overlap check, the unhooked fronts and the `add_rows` fix**: no
  trial met a silent overlap, a folding front or the misleading refusal.

## Gaps reported, by case

(Minutes are the agents' own estimates.)

### orifice_channel (new)
1. A long edge running into a plate face is graded in one series: 402
   columns, 104,767 cells, until stations a band height off the faces
   (2 min).
2. A cell-size cap in a region cannot be given to `size` nor read from
   `quality`; two counts by hand and a script to check (3 min).

### tandem_airfoils (new)
1. `to_y` cannot name a vertex: a 17-digit coordinate typed (2 min).
2. `add_ring`'s note does not name an open corner's two spoke ends
   (2 to 3 min).
3. `far_ratio` leaves a run of thin columns to the far field.
4. The digest of `smooth` listed 842 blocks as created and removed (the
   regression of batch 11, fixed during the round).
5. `smooth` 4 min 45 s on 1058 blocks.

### eccentric_annulus (new)
1. The ring's front IDs do not follow the wall stations' order (the
   first two are swapped): one fill refused, a describe and a script to
   pair them (3 min).
2. No operator places stations on one wall facing those of another: a
   30-line script (3 min).
3. `add_fill`'s "NOT the only reading" on a half annulus where either
   reading is the same blocks.
4. Every radial column carries the count the wide gap needs (an O-grid).

### cavity_channel
1. Two skip boxes: `skip=` twice is "Duplicate argument"; the help does
   not say eight numbers (1 min).
2. A lower budget broke the shear layer's cell cap (0.0104 to 0.0117 for
   0.01) and the growth (1.239 for 1.15) (3 min).
3. The reference curve is drawn as a smooth spline and overshoots at the
   sharp lips.

### splitter_cylinder
1. `far_ratio` leaves thin columns over a front that runs nearly along
   the carry direction; the carry split in two with a fill (3 min).
2. Ring front IDs near a mitre are not the wall ID plus the count (2 min).
3. The mitre's end leaves a 116-degree front corner: 186 faces above 70
   after `size` until the mitre end was moved onto the line between its
   neighbours (5 min).
4. `move_vertex` without `carry_curves=true` hooked a front spline; the
   next `size` refused a fold (2 min).
5. The default shape step at `max_pair_change` 14.0 (Step 4's signal is
   15) left 31 jumps above 5 after `size`; the pair limit 7 (4 min).
6. Three jumps above 5 at the wake band's top edge near the outlet (the
   band spokes' 39 cells against a coarse far-field row); a larger budget
   made six.
7. `size` raised the 0.06 plate-end face from 31 to 130 cells (+8,217)
   and would not split the strip.
8. The wake's cell cap has no measuring command (2 min).

### elbow_vane
1. No recipe for a rounded body end in a channel; the row does not say
   how the caps join the grid (8 min).
2. The shape step hurt (average 11.5 for 9.5) (2 min).
3. First widths 16 percent fine after the chain: the field pass was
   dropped whole by its verification, which Step 6's sentence on the
   polish's 5 percent does not cover; `set_first_width` repaired it.
4. The box face's position by experiment (3 min).

### bump_channel
1. Stations paired across the channel by hand (5 min).
2. A band mitred at a concave corner shears the whole band block beside
   it (averages of 30 to 36 degrees over a 162-cell block) until a
   station one band height from the foot (4 min, two layouts).
3. `quality`'s text prints "max size jump 2.5" for 2.49999 and no counts.
4. The reference curve overshoots at the feet in renders.

### triangle
1. The shape step turns the base-corner spokes the brief requires;
   holding the lines cost a jump of 5.51 (3 min).
2. The default budget lands at 1.52 times the reference (1 min).
3. Fan fronts carried to the far field against the skill's "never";
   `far_ratio` handled it (aspect 84).

### nozzle
1. The row's numbers are after the polish (25 degrees after `size`).

### cylinder_channel
1. The shape step makes the box an octagon: the wake strips start as
   chevrons (average 4.45; 2.7 to 2.95 with the box's rear held, at 20
   percent more cells) (3 min).
2. `size` does not say why the held variants need 20 percent more field
   cells.
3. Step 4 gives no syntax for `hold_box` / `hold_along`.

### u_bend
1. The front vertex 0.0013 off the tangent joint: `move_vertex x=0
   carry_curves=true`, from the refusal's own text (1 min; the fifth
   trial of the case).
2. `normals=` is refused on an open ring (1 min).

### y_junction (three trials)
1. The open corner as a corner of the fill leaves its 130 degrees in one
   block (34.75 for 24.85); the row does not say it must be a side
   station (4 min).
2. The open corner's count coupling: -51 percent and 21k cells until
   `failures.md`'s split; two trials say it belongs in the recipe, and
   line 47 names the wrong edge (3 min, 1 min).
3. A mitred spoke's tilt shears a long band block (24 degrees averaged
   over a 5-long block); a split near the junction took the average from
   10.5 to 7.8 (2 min).
4. A lower budget coarsened the first width by 20 percent (5b); Step 5
   says "at the same quality".

### airfoil_tunnel
1. The shape step pinched the wake band to a third (`min_edge_ratio`
   0.18); `hold_box` kept it at 15k cells more (5 min). Step 4 names a
   wake line, not a wake band.
2. Columns over the trailing edge and the nose row carry fine cells
   across the domain (1.18 times the cells).
3. Step 5 does not show `first_widths=PATCH=W`.

### bluff_ground
1. A raise through the ground band (a 0.0616 edge in a component of 0.025
   strips): ground 68 percent fine, 15k cells; one count and
   `set_first_width` (4 min).
2. `set_first_width` on the body tilts the base-corner lines (51 degrees)
   or adds nine jumps: kept to the ground (2 min).
3. A `points=` station landed far from the point given, silently (1 min).

### cascade
1. Equal station counts on the two sides of a ring: 27 typed fractions
   (3 min).
2. A leading-edge strip gave a 28-degree corner; one fill instead (3 min).
3. The unheld shape step breaks the wake band (0.10 / 20).
4. Matched periodic stations shear the wake and outlet cells (average 26).

### blunt_plate
1. One jump of 5.03 at the outlet end between the wake's outer column and
   the far-field rows; `keep_counts` after the polish brought it to 3.91
   where `failures.md` says split (2 min).
2. `keep_counts` left the first width 10 percent fine; `set_first_width`.

### naca4412
1. **The guard's blind spot**: `size` verified "0 faces above 70, max
   63.68" and the polish "63.68 -> 62.19" where the real counts have one
   face at 70.77 after `size` and checkMesh reads 69.56 after the polish.
   The face is shared with a neighbour coarsened at 256 cells a side
   (3 min).
2. `repair_block` (judging at 64 a side) reported an improvement and made
   the block 82.7 degrees with seven faces above 70 at real counts
   (2 min).
3. `TRIAL.md`'s `build.txt` assumes a batch; the engine route has none.

### three_cylinders
1. `to_box` spoke ends land up to 0.0018 off the point their station's
   normal aims at: 12 snaps (2 min; 16 in round 4).
2. Snap IDs shift with what the session holds; asks for `move_vertex` by
   position (3 min).
3. The row does not say how to place the boxes (2 min).

### high_lift
1. The flap's blunt base: no way to start its wake (four `add_vertex` and
   three hand blocks) (4 min; the third time in three rounds).
2. A fan front facing away from the carry must be taken into a fill
   first; one fill refused (4 min).
3. A 0.012-wide far-field column inherited 124 cells from the cove
   fill's cross component: aspect 5738, 4486 cells above 1000, until the
   carry went to the inlet (12 min). `far_ratio` does not widen the
   columns at its pinned ends, which nothing says.
4. Far-row vertex IDs depend on `rows=`: probe applies (5 min).
5. `size` 4.5 minutes, `smooth` 9, a far-field batch over 3, near 2000
   blocks: about 20 minutes of waiting.
6. One jump of 8.12 between the 124-cell column and its 11-cell
   neighbour: single hand gradings made faces above 70; a pair worked
   (6 min).

## Recurring gaps, round 5

| # | gap | trials (cases) | cost |
|---|---|---|---|
| 1 | The sampled guard under-reads a face shared with a coarsened block: `size` and `smooth` report a maximum 6 to 7 degrees under the real one, `repair_block` applies a move that is worse | 1 (`naca4412`), but a guard that says "0 faces above 70" with one there | a face above 70 delivered unseen, but for the polish |
| 2 | A cell-size cap in a region can be neither given to `size` nor read from `quality` | 4 (`orifice_channel`, `cavity_channel`, `splitter_cylinder`, `triangle`) | 2 to 3 min and a script each |
| 3 | The first width after the chain: the field pass dropped whole (-11 to -18%), `keep_counts`, a raise through a band; `set_first_width` repairs it except where a spoke continues as a line to the outlet | 9 (`orifice`, `annulus`, `cavity`, `elbow_vane`, `blunt_plate`, `bluff_ground`, `cascade`, `three_cylinders`, `high_lift`) | one line where it works; +28% and -23% left where it was not used or could not be |
| 4 | A jump above 5 where a fine carried column meets a coarse far-field row or column near a boundary | 3 (`splitter_cylinder` 9.6, `blunt_plate` 5.03, `high_lift` 8.1); round 4's `bluff_ground` | the round's only missed target; 2 to 6 min of hand grading |
| 5 | The shape step moves what the brief fixes: a wake band pinched, fans rotated, a box made an octagon, a wake twisted; its signal for the pair limit is not a threshold | 6 (`airfoil_tunnel`, `triangle`, `cylinder_channel`, `cascade`, `elbow_vane`, `splitter_cylinder`); 8 of 21 trials left the step out | 2 to 5 min each |
| 6 | A long band block beside a mitred or tilted spoke takes its shear along its whole length | 2 (`bump_channel`, `y_junction` 5c) | 4 and 2 min; 4 to 8 degrees of maximum or 3 of average |
| 7 | Stations of one wall facing those of another, or equal counts on two sides of a ring | 3 (`bump_channel`, `eccentric_annulus`, `cascade`) | 3 to 5 min, a script once |
| 8 | Which front vertex belongs to which wall station: open corners' spoke ends, a mitre's end, the first stations | 3 (`tandem_airfoils`, `eccentric_annulus`, `splitter_cylinder`) | 2 to 3 min each |
| 9 | Positions that cannot be named: `to_y` at a vertex, `move_vertex` by position, far-row ends by a stable name | 3 (`tandem_airfoils`, `three_cylinders`, `high_lift`) | 2 to 5 min each |
| 10 | The budget: the default lands 1.5 to 1.6 times a reference, a lower one coarsens the first width and breaks a region's cap | 4 (`triangle`, `y_junction` 5b 5c, `cavity_channel`) | 1 to 3 min; Step 5's "at the same quality" is not true |
| 11 | Spokes a little off where they were aimed: `to_box` (0.0018), the tangent joint (0.0013) | 2 (`three_cylinders`, `u_bend`), each for the second round or more | 12 snaps; 1 min |
| 12 | Thin columns carried to the far field that `far_ratio` cannot widen (a run of equal columns, the pinned ends) | 4 (`tandem_airfoils`, `splitter_cylinder`, `blunt_plate`, `high_lift`) | cells; once 12 min and 4486 cells above aspect 1000 |
| 13 | No start for the wake of a blunt base whose corners are open | 1 (`high_lift`), third round | 4 min, seven hand commands |
| 14 | Run time near 1000 to 2000 blocks | 3 (`high_lift`, `tandem_airfoils`, `splitter_cylinder`) | 5 to 20 min of waiting |
| 15 | Renders draw a reference curve as a spline that overshoots at sharp corners | 2 (`cavity_channel`, `bump_channel`) | none; reads as a defect |

## Spread between agents: `y_junction`, three trials

Round 4: three topologies, maxima 32.1 / 25.0 / 24.9, first widths exact /
17 percent fine / within 6, cells 1.46 : 1.26 : 1. Round 5: three
junction fills again (one 2x4 fill, a five-sided fill, two four-sided
fills), and **the quality is the same in all three**: maxima 24.85 / 24.86
/ 25.83, averages 9.9 / 9.3 / 7.8, every first width within 5.5 percent,
no jump above 5. The cells still run 1.2 : 1.52 : 1 (63,360 / 80,062 /
52,599), and the reports say why: the budget each agent chose (80,000,
the default, 60,000). The recipe in the route table is what all three
followed; what it leaves open is the budget.

## Reading

1. **The round's fixes held.** The shear, the dominant gap of round 4,
   was reported by no one; the first width, the second, is one command
   where it took up to fourteen lines; the three operator defects that
   cost round 4 the most (a silent overlap, two false refusals, folding
   fronts) did not appear. Sixteen cases at the same time and tokens as
   round 4 came out with 12 percent fewer cells, a lower mean average and
   one more case meeting every target.
2. **Unseen cases keep working.** Three new geometries, one of them two
   bodies with a wake passing over the second, met every target at the
   first trial with no engine and one 30-line script between them.
3. **`high_lift` is inside the targets, at their edge**: a jump at
   4.9999, a maximum of 68.3, an hour of which a third is waiting.
4. **The guards are the open defect.** `size` returned a face above 70
   that its own verification did not see (`naca4412`), and the polish
   hid it by luck. It is one case; it is also the tool's one hard
   promise.
5. **What agents cannot say**: a cell size in a region (four cases, a
   script each), a station facing another wall's (three), a position by
   name (three). Each is minutes, each recurs.
6. **The shape step is the weakest step.** Eight of twenty-one trials
   left it out, six reported that it moved what the brief fixes, and its
   one signal is unreliable. Where it works (junctions, bumps, clusters:
   60 to 25 degrees) nothing replaces it.
7. **What is not better**: `elbow_vane` (an agent's own layout against a
   row it did not follow), `cascade` (the periodic shear no layout here
   removes), and `naca4412`, which is my doing: see below.

## The NACA 4412 and a line I had put in the skill

`naca4412` came out at 69.56 degrees and 135,191 cells where round 3 had
45.12 at 205,506 from the same engine layout. Round 3 ran the shape step.
Round 4's agent tried it, read "47 percent more cells, four jumps above
5" after `size`, did not polish, and left it out; I had that sentence put
into Step 4 by batch 11; round 5's agent read it and left the step out.
The engine's session of this round through the whole chain, by my own
run: with the step 205,506 cells, 45.12 (7.32), 8 jumps above 2.5 and
none above 5 after the polish (4 above 5 after `size`); without it
135,191, 69.56 (11.24). Step 4 now says an engine session needs the step,
with both numbers (09e03de, mine). The lesson is the one `sizing.md`
already carries: jumps are judged after the polish.

## The confirmation owed on the round-5 hand builds: the shear guard holds

Rule: the batch 10 brief's (`ROUND4.md`, "Batch 10"), unchanged. Inputs:
the construction part of the 20 hand builds of the round (all but
`naca4412`), each with its trial's own `size` inputs, through the default
chain (shape step, `size`, polish), on the code before the guard
(657cef2) and on today's. Scripts and rows:
`/tmp/garbage/blockdrawer-bench/verify-round5/` (`inputs5.py`, `c5.sh`,
`rows`).

| term | result |
|---|---|
| no checkMesh failure, face above 70, cell above aspect 1000 or skewness crossing 3.5 gained; cells within 5% | holds (cells equal on all 20) |
| no maximum more than 5 degrees higher; more than 2 higher on at most three | holds: largest rise 0.04; two fall by a degree |
| no session without a jump above 5 gains one; their sum does not rise | holds: 44 -> 44 |
| jumps above 2.5 summed rise by at most 2% | holds: 252 -> 250 |
| no average more than 1 degree higher | holds: none higher |
| the mean of the averages falls by at least 1 degree | holds: 8.34 -> 7.15 (-1.19) |
| first heights: no wall patch 5 points worse; mean of the worst not higher | holds: 38.66 -> 38.61 |

Fourteen of the twenty size to the same state. The six that change:
`u_bend` 17.23 -> 2.97, `y_junction` 11.09 / 11.27 / 11.03 -> 9.93 / 9.26
/ 7.82, `elbow_vane` 14.52 -> 12.00, `bluff_ground` 3.49 -> 2.87. **The
guard is confirmed and stays the default.** Its limits are as recorded:
it does nothing for `bump_channel` (11.3), `splitter_cylinder` (9.5) or
the cascade.

## Candidates for the next fix batch

By the rule of the benchmark (two unrelated cases, or a general
primitive), in the order of what the round lost to them:

1. **Guards at the real counts** (gap 1): `size`'s verification,
   `smooth`'s and `repair_block`'s judge must not read a face shared with
   a coarsened block lower than it is. One case, and the one promise
   `size` makes.
2. **A cell size in a region** (gap 2; four cases): a cap as an input of
   `size`, and the cells' extents in a box from `quality`.
3. **The jump where a fine carried column meets a coarse far-field
   neighbour** (gap 4; three cases and round 4's): the round's only
   missed target. In `size`, under a rule on the 78 known inputs with
   batch 9's bands; and (gap 12, four cases) a way for `add_rows` not to
   carry a run of thin columns, or its pinned end columns, to the far
   boundary.
4. **Stations and names** (gaps 7, 8, 9; seven cases): stations on one
   wall facing another wall's, a count per section of a ring, the ring's
   note pairing wall stations with front vertices (open corners' spoke
   ends, a mitre's end), `to_x` / `to_y` at a vertex.
5. **Spokes where they are aimed** (gap 11; two cases, each for the second
   round): the normal of the curve the wall edge follows, not of the
   polyline's segments. Batch 11's rule for it asked every old build to
   replay unchanged, and the one that did not was a build made round the
   defect; the rule needs restating on what the builds give, not on their
   IDs.
6. **Skill text**: the junction row (the open corner a side station, the
   corner's chain ringed first, the split part of the recipe, `failures.md`
   line 47 naming the spoke), a station beside a mitred spoke on a long
   band (gap 6), what to hold in the shape step and how (`hold_box`; gap
   5), Step 5's budget line (what a lower budget also does), Step 6's
   sentence on the first width (the field pass dropped whole) and
   `set_first_width` as the last step where a brief asks an exact width,
   the nozzle, elbow and three-cylinders rows, `skip=` with eight numbers,
   `first_widths=` in Step 5; and a cap on `SKILL.md`'s characters beside
   its lines (16.9k at round 3, 20.0k now).
7. **Run time** (gap 14): the sampled guard is two thirds of `size` on a
   large session; item 1 will not make it cheaper.
8. **Renders**: a reference curve with sharp corners drawn through its
   points (gap 15).

Not taken, by the rule: the wake start of a blunt base with open corners
(`high_lift` only, though three rounds running), a staggered periodic
pairing (`cascade` only), `set_first_width` where a spoke runs on as a
line (`bluff_ground` only), the shape step's own behaviour (no general
formulation proposed; text only).

## Fixes after round 5

### Batch 12: sizing - what the guards report, a size cap in a region, the far-field jump (commits 99e42fe, 9cdba4f, 2f2be19, f8e3a3b)

Four local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round5a/BRIEF.md`; `construct.py` untouched.
Rules for items 1 and 3 were fixed in the brief on the 78 known inputs
(the 58 of batch 10 and round 5's 20) under the default chain.

- **The guards' blind spot** (99e42fe): measured first. Ten of the 78
  inputs coarsen a block at 256 cells a side; a face on an edge shared
  with a coarsened block reads low, because the merged cell's centre is
  too far from it, and `_verify` re-read at the real counts only blocks
  already above 65 degrees. Two sized sessions were misread, both the
  engine's NACA 4412 (round 2's: 64.98 / 0 faces reported, 71.02 / 1
  real; round 5's: 63.68 / 0 against 70.77 / 1); every polished session
  of the 78 was within half a degree.
  - Guards deciding at the real counts **failed the rule**: round 2's
    NACA gained two jumps above 5 (0 -> 2; sum 138 -> 140), and round
    5's NACA meshed worse (74.2 degrees and two faces above 70 after the
    polish, for 69.56 and none). Kept out.
  - What went in is the reporting: `size`'s `verification`, `smooth`'s
    before and after and `repair_block`'s reason give the worst face and
    the faces above 70 at the real counts. No session changes. On the
    reproducer `size` now says "result 1 faces >70, max 70.77" and the
    polish "70.77 -> 69.56 ... faces above 70 degrees 1 -> 0 (real
    counts)". Cost: 1 to 2 percent of wall-clock on the largest sessions.
  - So the agent is told; `size` still returns the face. Open.
- **A cell size in a region** (9cdba4f): `size ... cap=X0,Y0,X1,Y1,H`
  (`cap_x=`, `cap_y=` for one direction; five numbers a box) limits the
  field in the box, so counts, budget and continuity see it; a lower
  budget does not coarsen a capped component. `quality --bbox X0,Y0,X1,Y1`
  gives the largest cell extent in x and y among cells centred in the box,
  with the block and place.

  | build | cells | largest cell in the box (cap) | first width | max | the trial's own |
  |---|---|---|---|---|---|
  | cavity_channel 5, `cap_y` 0.01 | 71,850 | 0.0079 | exact | 0 | 95,879 cells at the default budget |
  | the same at `budget=72000` | 49,160 | 0.0079 | exact | 0 | without the cap 48,833 cells and 0.0104 (the trial's miss) |
  | orifice_channel 5, `cap_y` 0.02 | 55,313 | 0.0199 | exact | 0.52 | 56,208 cells, 3.95 degrees, two counts by hand |

  `quality --bbox` on the four delivered sessions gives what the trials'
  scripts gave: cavity 0.0079, orifice 0.0198, triangle 0.081, splitter
  0.066.
- **The jump where a fine carried column meets a coarse row** (2f2be19):
  reproduced on the three round-5 builds through their trials' chains
  (9.62, 5.03, 8.12). At each, a strip of many members sharing one count
  ends wide (or fine and uniform) at the band's outer edge against a few
  cells graded at the growth limit; the split that would help is refused
  as a sliver. Two general moves (regrading both strips of the costliest
  pairs together; a continuity split for an edge short of its width at
  one end) changed none of the three. **The rule's purpose term fails; no
  default changed.** `failures.md` has the signature with the two hand
  remedies as measured: `size ... keep_counts=true` after the polish
  (`blunt_plate`: 5.03 -> 3.91, maximum 28.4 -> 31.4) and a pair of
  gradings across the interface (`high_lift`: 8.12 -> 5.00).
- **Text and caps** (f8e3a3b): Step 5 (`first_widths=PATCH=W`; a lower
  budget also coarsens the field, first widths included; `cap=` and
  `quality --bbox`; the verification is at real counts), Step 6 (the
  first width 11 to 18 percent off where the field pass was dropped;
  `set_first_width` as the last step and where it does not serve).
  `SKILL.md` is 250 lines and 20,051 characters, and a test now caps the
  characters at 20,100.
- **My checks** (`/tmp/garbage/blockdrawer-bench/verify-batch12/`, old
  code from an archive of 09e03de, through the command line): suite 696
  tests OK; the NACA reproducer (`size`, the polish, checkMesh,
  `repair_block`); 17 default chains (16 round-5 inputs and the NACA with
  the shape step) equal to their rows before the batch; the four cap
  reproducers; `quality --bbox` on the four trials; the 1058-block tandem
  chain 659 s before, 663 after. Every number agreed.
- **Sent back and fixed** (b4dda11): `repair_block` on the polished NACA
  session still applied its move while reporting "max_face 69.56 ->
  82.74, severe_faces 0 -> 7". It now takes a candidate only when, at the
  real counts over the block and its neighbours, no face above 70 is
  gained and the worst face rises by at most half a degree; otherwise
  "nothing applied", with the best refused candidate's numbers. On the
  reproducer nothing is applied and the session is unchanged (my run); on
  four sessions where it repaired before it still does (the developer's
  table; my run on `y_junction` 5c: the same move), and on `triangle` 5
  it takes the next-best move where the old one raised the worst face by
  1.7 degrees. Suite 697 tests OK. One call takes 761 s on the 1822-block
  high-lift session, before and after: not looked into.
- **Not verified by anyone**: the reporting change on the 65 inputs not
  rerun (their decision code is unchanged); caps in boxes not aligned
  with block lines beyond unit cases; caps on the two wake briefs.
- **Left open**: `size` returning a face above 70 it now reports; the
  far-field jump (the round's one missed target).

### Batch 13: construction - stations and names, spokes where aimed, thin far columns, the blunt-base wake, skill text (commits 364f8cc, 2019618, 1e5bef1, 423c590)

Four local commits by one Opus developer from
`/tmp/garbage/blockdrawer-dev/round5b/BRIEF.md` (interrupted once by a
usage limit, resumed); `size.py`, `smooth.py`, `sampling.py` and
`quality.py` untouched. Two of the five items were measurements under
rules fixed in the brief, and both failed them: no default and no
existing build changes in this batch.

- **Stations and names** (364f8cc), four additions, none changing what an
  existing command builds:
  - `add_ring ... counts=14,14`: edges per section between consecutive
    stations. The cascade's 27 typed fractions become
    `fractions=0.524428 counts=14,14`: same vertex IDs and blocks, every
    vertex within 1.3e-5 (the trial's rounding).
  - `add_rows ... to_y=V` / `to_x=V` and vertex IDs in `stations=`: the
    tandem's `to_y=-0.11961770602216258` is `to_y=f_82`, the same session
    bit for bit.
  - The ring's note pairs wall stations with front vertices: runs
    (`i_0->i_25, i_1->i_24, i_2..i_23->i_26..i_47`), each open corner's
    two spoke ends with the side they stand on, each fan's and each
    mitre's front. Round 5's trials probed for these three times. On the
    34 rings of the round-5 builds the note is 101 to 367 characters
    (median 276; my count).
  - `facing=POINTS` (`add_ring`, `add_stations`): a station where the
    normal of another reference curve at each point meets this one.
    **It does not do what the two cases wanted.** Through the trials' own
    chains: `eccentric_annulus` 24,388 cells, 17.32 degrees (5.68) for
    the trial's 24,297, 11.74 (3.56); `bump_channel` 47,190 cells, 85.96
    degrees and 275 faces above 70 in one block for 44,720 and 33.02; the
    reverse pairing on the bump is refused (a station inside a mitre's
    reach). My third probe, the nozzle: refused, the lower wall's normal
    18 degrees upstream misses the upper wall. The hand pairings are
    lines making equal angles with both walls' normals; sent back as a
    measurement under a rule (follow-up below).
- **Spokes where they are aimed** (measured, kept out): one defect, not
  two - a smooth band vertex takes the bisector of the chords to its
  neighbouring points, off the wall's normal by up to half the turn of
  the segment beside it (the U-bend's joint, 0.0013; three cylinders'
  `normals=` stations carried `to_box`, up to 0.0029). The fix (the
  curve's normal from circles through neighbouring points) built both
  purpose builds: `u_bend` 5 without its two `move_vertex` as delivered,
  `three_cylinders` 5 without its 12 snaps 47,004 cells, 36.90 (9.05) for
  45,261, 37.02 (9.27). Under the brief's rule 38 builds moved; of 34 run
  through their chains 33 held every term and `splitter_cylinder` 5 rose
  43.58 -> 48.10 degrees (jumps above 5 3 -> 9); the same build with its
  band 0.1001 high for 0.1 gives 44.33 (3 -> 7). **The rule fails on one
  build; not committed** (the patch is in the developer's scratch
  `item2tree/`). The snaps stay the remedy, the skill says how many.
  What this shows beside: the splitter's chain moves 4.5 degrees and six
  jumps above 5 for a change of a thousandth in its construction.
- **Thin far columns** (2019618): `add_rows ... far_min=F`, a floor on
  the far row's column widths. On the three builds whose agents reported
  thin columns, at F = 0.5 and 0.2, **the rule fails at both**: the gain
  term on one build of three each time, and maxima up to 41.8 degrees
  higher (`blunt_plate` 5 at 0.5: 85,782 cells, 73.13 degrees, a face
  above 70, two jumps above 5, for 69,872 and 31.38; `splitter_cylinder`
  5 at 0.2: jumps above 5 3 -> 11). The floor tilts every column line
  between the chain and the far row. The option exists, off, documented
  with the numbers; the `far_ratio` note now says what it does not widen
  (+84 characters on 13 commands of six round-5 builds).
- **The wake of a blunt base with both corners open** (1e5bef1, text
  only): no new operator is needed - `remove_edge` the base's band block,
  two `add_vertex at=E dx= dy=`, one `add_fill` with the chain
  spoke-base-spoke as one side, `add_rows` from the new side. On
  `high_lift` 5's flap, three commands for seven; through the trial's
  chain 217,786 cells, Mesh OK, 68.30 (14.11), no jump above 5, first
  widths as delivered (219,594, 68.30 (14.12)). In `failures.md`.
- **Skill text** (423c590): round 5's rows (orifice, tandem, annulus, the
  operators-only high-lift, three cylinders by boxes, the elbow's second
  layout), the junction row as a recipe with the split of the corner's
  spokes, a station a band height from a mitred or tilted spoke, what the
  shape step moves and the holds for it, `skip=` as four numbers a box,
  `counts=`, `to_y=V`. Paid for by dropping the reference-file table, the
  hand-script times, engine refusal codes, the 30P30N "defects left" text
  and numbers that `sizing.md` or `failures.md` already carry. 249 lines,
  20,088 characters.
- **My checks** (`/tmp/garbage/blockdrawer-bench/verify-batch13/`, old
  code from an archive of b4dda11): suite 710 tests OK; the construction
  of every round-3/4/5 `build.txt` that starts from a box (59; one more,
  `bluff_ground` 4, stops at the same command on both, as since batch 11)
  identical on old and new code, vertex for vertex; through the command
  line and the whole chain `annulus_facing`, `bump_A`, `bump_R`,
  `blunt_plate` at `far_min=0.5`, `splitter_cylinder` at 0.2 and the
  high-lift fill - every number as the developer reported; the cascade
  and tandem constructions compared; refusals of `counts=` (wrong number
  of sections, with `count`) and `facing=` (a point on no curve) read; the
  skill's new numbers looked up in this ledger and the block counts in
  the sessions; the docs name no code that is not committed.
- **Not verified by anyone**: the spoke fix on `high_lift` 3, 4, 5 and
  `tandem_airfoils` 5 (not run once a term had failed); `far_min` at
  other values; `counts=` beyond the cascade and unit tests.
- **Left open**: spokes off their aim (a general fix exists and fails one
  build of 34 by 2.5 degrees over the band); thin far columns (two
  options measured now, `far_ratio` and `far_min`, neither a default).
- **Follow-up 1, `facing=` by the equal-angle pairing** (49ffc0d,
  README only): the pairing in which the line between two stations makes
  equal and opposite angles with both walls' normals (the two contact
  points of a circle touching both walls) was measured under a rule fixed
  before (`round5b/FOLLOWUP1.md`): replace the one wall's normal only if
  the annulus, the bump and the nozzle all hold.

  | case | equal angles | one wall's normal (committed) | the trial's hand pairing |
  |---|---|---|---|
  | `eccentric_annulus` | 24,297 cells, 11.76 (3.64) | 24,388, 17.32 (5.68) | 24,297, 11.74 (3.56) |
  | `nozzle` (my probe) | the ten `xs=` stations, to 4e-15 | refused | cross lines by `xs=` |
  | `bump_channel`, top facing bottom | refused: pairs cross beside the bump's feet, a 0.020 edge at a mitre | 47,190, 85.96, 275 faces above 70 | 44,720, 33.02 (11.28) |
  | `bump_channel`, bottom facing top | 46,160, 72.31 (16.9), 27 faces above 70 | refused | - |

  **The rule fails on the bump ("builds"); `facing=` stays as committed
  in 364f8cc**, the equal-angle code is not in the tree (saved in the
  developer's scratch, `item2tree/construct_equal_angle.py`). I reran the
  annulus and both bump rows on that code from a scratch tree: as
  reported. Reading: on smooth walls the equal-angle pairing is the hand
  pairing (the annulus's bipolar lines, the nozzle's cross lines) and the
  one wall's normal is not; beside a wall's corners neither is - there
  the trial's pairs are a person's compromise. A candidate only if round
  6 asks for pairings again: equal angles with stations beside a corner
  left to the corner's own.
