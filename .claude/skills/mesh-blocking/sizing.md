# Sizing and grading

Open this when you choose `size`'s inputs (`SKILL.md` Step 5), read its
`verification`, or have to size part of a session by hand. Measured tables
are in the repository README's `size` paragraphs; the numbers quoted here
point there.

## The physical inputs

- **First cell** from the target $y^+$ with a screening friction coefficient
  $C_f \approx 0.003$:

$$
u_\tau/U_\infty \approx \sqrt{C_f/2}, \qquad
y_1^+ \approx Re_c \sqrt{C_f/2}\;\frac{y_1}{c}.
$$

  For a spectral-element mesh the first interior collocation point sits at
  about $0.064$ element heights for $p = 7$, so the first element height is
  $y_1 / 0.064$.
- **Growth** at most 1.2 to 1.3 per cell inside the band (`growth=`).
- **Band height** from the boundary layer where profiles exist: 1.5 to 2
  times the largest $\delta_{99}$ on the body, usually the suction side near
  the trailing edge (A-airfoil at 13 degrees: $\delta_{99} = 0.094c$, band
  0.15 chords; `cases/a_airfoil/sizing.md`). "One to a few percent of the
  chord" is only the attached-flow default.
- **Cells along a wall**: `wall_aspect=25` makes them 25 first widths long,
  the usual RANS stretching; without it the wall is resolved as finely
  along as across.
- **Core size**: the largest cell, away from walls (`core_size`).

## `size`, argument by argument

`size FIRST CORE [growth=1.2] [wall_aspect=A] [count_rule=max|mean]
[interface_growth=R] [budget=N] [first_widths=PATCH=W,...] [split=true]
[method=directional|distance] [keep_counts=true] [grading=strip|field]
[blocks=...] [bbox=...]`

- The directional field: across each `wall` patch the series
  `first + (growth - 1) d`, along it `wall_aspect` first widths, capped at
  `core_size`. Only patches of type `wall` grade; a session without one is
  sized at `core_size` and the note says so.
- Widths are bounded across every block interface by `interface_growth`
  (default 1.25 with `directional`, unmatched with `distance`); opposite
  sides of a strip of similar blocks share one series, so rectangles are not
  sheared; a strip fine at both ends is split where its size peaks unless a
  piece would be shorter than one wanted cell (`refused` says so).
- `count_rule=max` lets the finest demand in a count component decide;
  `mean` the length-weighted mean (coarser where a short wall piece shares
  a component with long far-field edges).
- `budget`: total cells; the default is 1.5 times the field's own. The
  field takes budget/1.5 (coarsened when that is smaller than its own), the
  rest buys continuity: free regrades and count raises taken greedily by the
  drop in interface jumps per cell. From the field's own state up, more
  budget never raises the jump cost. `budget_binding` says whether the
  budget stopped the spending; when it is false a larger budget changes
  nothing (three cylinders: `budget=75000` gave the default's mesh).
- The first width is a bound, not a value: a spoke shares its count with
  its component and its strip's series (one total ratio for every member,
  so a spoke's first width is in proportion to its length), and the
  surplus makes the wall end finer. `first_width` in the result gives each
  wall patch's smallest and largest first cell height normal to the wall
  at its band spokes against the one asked, naming the two spokes (edges
  leaving the wall more than 45 degrees from its normal, such as a wake
  line from a sharp trailing edge, are left out; `quality` reports the
  same after the polish); round 1 measured, along every edge leaving the
  wall, 4.05e-4 .. 5.23e-4 for 5e-4 and 0.00144 .. 0.00193 for 0.002 (up
  to 28 percent finer). A spoke leaving the wall obliquely (a
  trailing-edge fan, a corner) gets the larger size the field asks along
  it. For a hard $y^+$ ceiling ask a little coarser, try `grading=field`,
  or set the spokes' `start_width` by hand (`set_edge_grading`) as the
  last sizing step: a later `size`, `keep_counts=true` included, regrades
  them.
- `grading=field` (off by default): after spending, each strip member is
  graded at its count by the field where it lies - a wall spoke starts at
  the first width asked, an edge far from walls relaxes instead of
  carrying a wall's clustering out (cells 4e-4 wide and 0.25 tall at a
  far-field corner) - where the shear, the interface jumps (worst not
  above the spent one or 2.5) and the sampled grid allow; counts do not
  change. `field_grading` says how many strips took it. Measured on the 11
  example and benchmark sessions (README): NACA 4412 C-grid max 69.85 ->
  30.98 degrees and its spokes exact; three cylinders, cylinder channel,
  U-bend spokes exact; no session gained a face above 70; blunt plate and
  cascade unchanged at the wall. Compare by `check`.
- `keep_counts=true` keeps every count, recomputes the gradings and
  regrades strips for continuity without raising a count (after moving
  vertices or setting a count by hand).

## Reading `verification`

`verification.field` is the field's state after its own repairs,
`verification.result` what `size` returns; both at 256 cells a side, and at
real counts for every block with a face above 65 degrees. Each move is
checked as it is made, and spending is undone where the result would hold
more faces above 70, a worst face more than 0.5 degrees higher, a fold, or
skewness past 4. So:

- `result` above 70 degrees where `field` was not: cannot happen; report it.
- `field` with faces above 70: the counts the field gives a block leave
  them, and spending does not remove them. Polish (`smooth`) and `check`
  first: the polish may clear them (it did on three cylinders). Where `check` still finds them,
  locate the block (`check` maps the set) and go to `failures.md`; a face in
  cells far thinner than they are tall is wall-fine spacing carried out by
  a count component (NACA 4412: one face at 70.6 at the far-field corner,
  fixed by `set_edge_grading ... start_width` on two edges there).
  `method=distance` is a comparison, not a retry: fine 30P30N 0 faces above
  70 (max 68.83) against the default's 6 at 85.8, but NACA 4412 2,088
  faces above 70 (max 81.5) and three cylinders skewness 3.85 and a jump of
  19.2 where the default's polish had none (README, `method=distance`).

## What the measurements say

(README, `size`; budget = the reference mesh's cells, checkMesh after shape,
size, polish.)

- A pure H-grid is sized as a person would: backward step 0 degrees, worst
  jump 1.1 (hand: the same jump at 23,598 cells).
- Jumps above 5 fall by half or more against the previous fixed sizing and
  the hand mesh (three foils 18 -> 5, coarse 30P30N 105 -> 29, grooved
  tandem 46 -> 22 against the hand).
- What spending cannot fix:
  - **Counts are never lowered.** A sliver whose count the field sets from a
    longer edge in its component: coarse 30P30N, v12-v13 (0.0019 long) gets 9
    cells from its component's wall-side edge v53-v55, the seam jump b21/b31
    reads 64.7; 2 cells read 11.4. Set it by hand (`set_edge_cells v12-v13
    2`) and run `size FIRST CORE keep_counts=true`.
  - A thin strip beside an edge that needs fine cells at both ends, whose
    split would leave a sliver (three ellipses, b67/b73, jump 25).
  - Faces above 70 degrees the field state itself has (fine 30P30N, b21,
    b39, b53): `failures.md`; there `method=distance` was the one that
    removed them.

## Sizing by hand

- `set_edge_cells EDGE N` sets the whole count component (opposite edges
  through every block); `describe SESSION --component EDGE` lists it. A
  component holding a wall-tangent edge and a band-normal spoke ties
  streamwise to boundary-layer resolution: a topology defect, fix the blocks.
  A long far-field arc and a short wall piece in one component only means
  large far cells.
- `set_edge_grading v0-v1 start_width 0.002` puts the small cell at the
  vertex written first; `propagate=true` follows the component in one
  physical direction. Two opposite edges graded towards opposite ends shear
  every cell between them (crossed gradings: `repair_block` regrades them).
- Spacing links (`add_spacing_link DRIVER FOLLOWER`) keep a width continuous
  at a vertex. Along a wake, do not let the rows spread to a uniform
  transverse spacing at the outlet: blockMesh blends the two distributions,
  and a uniform outlet opened the first wake row by an order of magnitude
  (A-airfoil hand build, `cases/a_airfoil`: 74 degrees, skewness 1.8; graded
  outlet 68, 0.8).
- In a hand-built O- or C-grid distribute far-field stations by the arc
  length of the band front, not of the wall (A-airfoil: spokes 67 degrees
  off the normal with the wall map, 52 with the front map;
  `cases/a_airfoil`).
- Aspect ratio matters inside the band only; keep interface jumps below
  about 2.5 in the near field.
