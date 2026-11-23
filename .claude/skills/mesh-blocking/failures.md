# Failure signatures and the tools that act on them

Open this when `quality` or `check` names a defect (`SKILL.md` Step 7).
Find the blocks first: `check SESSION` (MCP `check_mesh`) maps checkMesh's
`nonOrthoFaces`, `skewFaces` and other sets to blocks, worst first, with
bounds; `quality SESSION --worst 5` ranks blocks by folded cells, then
sampled non-orthogonality; `render SESSION -o x.png --bounds ...` (padded)
shows why.

## First moves, in order

1. **`smooth`** on the whole session (or `blocks=`/`bbox=`): it takes what
   moving interior vertices can take - on the engine's 30P30N 60 faces above
   70 to none, max 77.9 -> 68.7; on its 475k-cell sizing 8725 faces to 4716
   (README, `smooth`). Where it stops - a seam wedge whose apex is fixed by a
   wall corner, a size jump it may not create - `reason` says so and the
   topology is the remedy. `hold=` (IDs), `hold_box=` or `hold_line=A,B`
   (the vertices on the segment) keep vertices you placed on purpose;
   `hold_along=line` (or `x`, `y`) lets them move along that line only. A
   held line costs degrees (triangle 24.8 -> 27.3, cylinder in a
   channel 35 -> 41).
2. **`repair_block ID`** on the worst block, repeated: it tries moving the
   block's interior vertices (curved edges carried), merging it across an
   internal edge, splitting its edges and regrading an edge to its opposite
   edge's series, judges each on the block and its neighbours by the corner
   screen and the sampled interior at real counts, and applies the best (a
   worst cell half a degree better with no more severely sheared cells, or
   a tenth of them removed with the worst cell kept), never one that adds a
   face above 70 or raises the worst face (real counts; "nothing applied"
   names the best refused). Stop when it reports a structural defect or "nothing to
   repair" (no severe cell, every angle at least 45 degrees from
   degenerate); the remaining defects are then size jumps or aspect ratios,
   which it does not judge.
3. **A count or grading by hand** (`sizing.md`, "Sizing by hand") for what
   the quality screen's size jumps and aspects name.
4. **A different topology** for what none of these moves.

## Signature -> tool

| Signature | Cause | Tool |
|---|---|---|
| Faces above 70 in one tapered block after the polish, also in `size`'s `verification.field` | the counts the field gives that block | `repair_block`; else split the block (`split_edge`); `size ... method=distance` only as a comparison kept when `check` prefers it (better on the fine 30P30N, worse on NACA 4412 and three cylinders) |
| A face above 70 in cells far thinner than tall, at a far-field corner | wall-fine spacing carried out along a count component (the C-grid's streamwise component) | `set_edge_grading EDGE start_width W` on the far edges, then `check` |
| `size refused`: skewness past 4 | a tapered block with few cells across | `split_edge` through it, or `size ... keep_counts=true` on counts you chose |
| checkMesh's average non-orthogonality several times what the block angles explain; the maximum and every limit pass | the blocks' shape (a sheared or tapered block, stations paired across a passage), or a strip graded apart that bought a first width, a jump or a worst face; since batch 10 `size` no longer grades the long sides of thin blocks apart for nothing (U-bend 11.7 -> 3.3) | `quality`'s verdict names the blocks that make the average (`average_blocks`: each block's average and share); render them; a shape: Step 4 or the topology; graded apart: `size ... grading=strip` on a copy and `check` both (strip gives up the first width; `set_first_width` after it) |
| A wall's first width more than 10 percent off after `size` and the polish; `size`'s note names refused strips and the spokes they leave off, or `raised` says "through band spokes" | a guard kept the spokes' strip on one ratio, or a raise gave them a far count (round 4: 12 of 20 trials graded spokes by hand, up to 13 lines) | `set_first_width W` as the last sizing step (`patch=` to choose): the first widths and the jumps before and after; `check` |
| Two opposite edges graded towards opposite ends | crossed gradings shear every cell between them; the corner screen does not see it | `repair_block` (regrade move) or `set_edge_grading` |
| Size jump around a short edge carrying many cells | the edge shares a count component with a long fine edge; counts are never lowered | `set_edge_cells` on that component, then `size FIRST CORE keep_counts=true` |
| Jump beside an edge that wants fine cells at both ends, or a strip fine at opposite ends on different members | one geometric series cannot serve it (multi-section grading is out of scope) | `size` cuts it unless a piece is under half a wanted cell or the cut folds a block; its note names each strip left uncut with the `split_edge EDGE FRACTION` that would cut it: run that before `size` (or vary the fraction), then `size` again |
| Jumps above 5 that stay at a larger budget (`budget_binding` false) | topological: beside a fan or a corner left open, or across a channel one block thick - the budget buys continuity only where a count raise or regrade reaches (high-lift, round 3: 22 above 5 at `budget=450000`, not binding) | `split_edge` the strip where the widths jump, or add a block across the channel; not the budget |
| One jump above 5 left by `size` and the polish where a fine column carried to a boundary meets a coarse far-field row or column (splitter plate 9.62, blunt plate 5.03, high-lift 8.12; `describe --component EDGE` on both sides) | each side shares one series with a long strip: the wake column's (52 to 66 members, one count) is coarse at its far members' outer edge, the neighbour's (8 to 12 cells) is graded at the growth limit, or the wrong way (high-lift: fine at its far end); no single or paired regrade, one-end continuity split or budget moved it (batch 12, README) | after the polish: `size FIRST CORE keep_counts=true` (blunt plate 5.03 -> 3.91, no jump above 5, checkMesh 28.4 -> 31.4) or a pair of gradings at the vertex, the fine side's end coarser and the coarse side's finer (high-lift: `end_width 0.0016` on the 124-cell edge with `start_width 0.0072` on the 11-cell one, 8.12 -> 5.00; either alone left 5.74 or 6.36) |
| A wall's first width 24-28 percent fine and a jump of 7.18 next to a corner left open (`fan=false`), Y-junction | the fill at the open corner puts one wall band's rows in a count component with the other wall's along-wall count: `size` grades them as one strip (`describe --component EDGE` shows it) | `split_edge` the corner's spokes - the band edges from the open corner to its two front vertices, which the ring's note names (`open corner C: F1 on its A side, F2 on its B side`): `split_edge C-F1 0.5`, `split_edge C-F2 0.5` - before `size`; part of the junction's recipe (round 5, trial 5b; without it a wall 51 percent fine and 21k cells more) |
| Tangential cells far finer than the first width along a block line from a concave domain corner into a convex tip (U-bend: 3.8e-5 beside a first width of 0.002); checkMesh passes | the line puts the corner's and the tip's clustering in one count component and one graded strip: the corner's fine cells run down it into the tip | rebuild: Cartesian blocks in the corner, their lines parallel to both walls, and the band round the tip closed on a square (an O-layer: `add_fill` to a half square, `add_grid` round it): U-bend 23,754 cells, average non-orthogonality 2.7, against 79,462 and 24.3 with the diagonal lines |
| Aspect ratio near or above 1000 in thin far-field columns: from a convex corner's fan, a trailing edge's or a base's fine spacing | `add_rows` maps the chain to the far row affinely, so a column is as wide at the outer boundary as on the chain; a fan's front edges take the spoke's wall-normal count (high-lift: 0.0006 wide carried 15, 3593 cells above 1000 after `size`, worst 8071) | open the corner (`fan=false`, `open_at=X,Y`) or carry the columns from a row beyond the fan; `add_rows`' note names its thinnest column and the far row's narrowest before `size`. `add_rows ... far_ratio=2` (neighbouring far-row columns within 2 times) widens a thin column beside wide ones: C-grid example 978 -> 376 at a first width of 1e-4, 41,152 -> 38,300 cells; round-3 builds airfoil tunnel 93 -> 52, bluff body over ground 303 -> 87 (but +6.4 percent cells and a jump above 5), high-lift 3873 (1738 cells, failing) -> 950, Mesh OK (jumps above 5 17 -> 19). It does nothing where every column is thin (triangle: the apex fan front alone carried to the inlet, 648 -> 518) or where the column is no thinner than its neighbours but holds many cells (blunt plate: above the base corners, 893 -> 1000) - not recommended for every far field. Nor does it widen a run of equally thin columns or a column between two pinned points (the note says so); `far_min=F`, a floor at F times the mean column width, failed its rule at 0.5 and 0.2 on three round-5 builds (README: the column lines tilt) |
| The wake of a blunt base whose two corners are open (`open_at` both): no carry direction lies in both corners' wedges, and with the base's band block removed the base is no edge to carry | the chain spoke-base-spoke is three edges only once blocks stand on them | `remove_edge` the base's band block; `add_vertex at=E1 dx=DX dy=DY id=A` and `id=D` for the two spoke ends E1, E2 (the wake direction, a short way); `add_fill E1,E2,D,A side1=E1,C1,C2,E2` (the spoke, the base C1-C2 and the other spoke as one side: three blocks, their stations facing it); `set_edge_boundary C1-C2 WALL`; `add_rows between=A,D ...` to the outlet. `remove_edge` of the base's band block prunes the base's own wall stations (C1..C2 lose the vertices between them), so side1 names only what is left, and a base of one edge is a two-ended strip from base to outlet: give it two edges, a station at its middle (`add_vertex between=C1,C2 id=M`, then `side1=E1,C1,M,C2,E2`; `triangle` trial 6: 300 blocks, 32,930 cells, Mesh OK, max 17.40 (5.72)). High-lift flap, round 5's build with two `add_vertex` and the fill for its four `add_vertex` and three blocks: 217,786 cells, Mesh OK, max 68.3, no jump above 5, first widths as delivered (219,594, 68.3) |
| The slot entrance ahead of a flap nose (two-element airfoil): the region between the main element's lower front, the slot strip and the flap's lower front has no carry direction; the trial's two layouts with a steep lower chain there left faces above 70 | the far field's lower chain turns sharply at the slot | the slot strip as one fill between the two fronts, then a fill down to a free vertex below the main element, so the lower chain has no steep edge: `add_fill m_164,m_170,f_61,f_67 id=s`, `add_vertex 0.94 -0.125 id=P`, `add_fill m_161,m_164,f_70,P id=e` (stations on both rings facing each other: `points=` on the main's lower surface, `normals=` on the flap). `flap_airfoil` trial 6: 1118 blocks; size, polish, `set_first_width`: 151,929 cells, Mesh OK, max 51.18 (average 9.52), no jump above 5, first widths exact |
| The pocket between tandem bodies (the rear body below the front one's wake) | the gap is a region no single carry fills | carry the rear's upper front (and its wake band's upper side) up to the level of the front's lower trailing-edge spoke end, `add_rows between=REAR_NOSE_STATION,WAKE_VERTEX direction=0,1 to_y=FRONT_SPOKE_END rows=1`, then the front's wake band over it (`conform` takes the pocket's top vertices as stations), and fill the column between the airfoils (`add_fill`). `tandem_airfoils` trial 6: 690 blocks; shape, size, polish, `set_first_width`: 127,740 cells, Mesh OK, max 37.54 (5.93) with its two hand splits of the column between the airfoils (`size` now makes the one at the front trailing edge itself: without both, 143,960 cells, 35.73) |
| A junction of a plate on a cylinder (concave body corner): the rows carried from each junction wall along the other (`add_rows conform`) are hard to state | the ring mitres the corner; the corner region is four-sided | a fill per junction with its far corner level with the two fronts: `add_vertex x_of=F_PLATE y_of=F_BODY id=ct`, `add_fill MITRE_FRONT,F_PLATE,ct,F_BODY`. `splitter_cylinder` trial 6b: 764 blocks, 105,653 cells, Mesh OK, max 43.15 (8.63), no jump above 5, first widths within 5 percent. The mitre spoke there is fine at the wall and, through the fill's strip, at the front: split it before `size` (`split_edge r_0-r_42 0.5` in 6b; without it 11 jumps above 5 and a first width 17 times the one asked - `size` does not find it two-ended) |
| A sharp or rounded wall corner that is not a block corner | the band block spans the corner | `split_edge` so the corner is a vertex |
| A gate in the wrong place along the wall: a band block sheared although its spokes are fine | the wall vertex sits off where its spoke wants it | `smooth slide=true` (slides it along the wall, re-cut from the input wall, carrying the band spoke), or `slide_vertex V fraction=F` by hand and then move the spoke's front with `move_vertex` |
| A wake or far-field block with a corner near 180 degrees | a ring vertex in the wrong place | `move_vertex` (with `carry_curves=true` for curved edges) or `split_edge` with a centre vertex |
| A folded band block at a sharp convex feature | the feature needs more incident blocks | a fan (`add_band` and `add_ring` make one above 60 degrees of turning), a seam or a wake (engine) - not a thinner band |
| `add_fill` refused: "opposite sides need as many edges" | the side stations do not pair up | the `split_edge` or station the refusal names (the short side's segment facing the extra station), or remove a station from the long side |
| `add_fill` refused: "a triangle (pentagon) is filled as 3 (5) blocks round a centre" | side k's edges must be the sum of spokes k-1 and k+1: the side counts sum to an odd number, or one side is as long as the rest | add or remove a station on a side; or fill four sides with a corner in between |
| `add_fill` refused: a fill block "would not be strictly convex" | stations on opposite sides do not face each other, or a side bends further than one transfinite map follows | move or add stations; fill the region in parts (a side corner in between) |
| `add_fill` refused: "bound no region" or "ambiguous" | the corners are not in order round the region, or a side runs two ways between its corners | reorder the corners; name that side's vertices with `side1=...` |
| `add_fill`'s note: "NOT the only reading" | another side choice also bounds a region (round 4: a walk round the elbow's hole where a straight side was meant); the fill took the one with the most existing sides | the side the note names (`sideK=...` as printed) when the other was meant; a chain through a wall vertex left open is followed along its one edge without a patch (the slat cove's front) |
| `add_ring` refused: "the outward normal points at ANGLE degrees at N places" | a thin body, square corners or a cambered blade has that normal more than once | `near=X,Y` (one point per angle) or the station itself by `points=X,Y` |
| `add_band` refused: "would fold ... concave corner" | a mitre at a concave corner moves the offset end past a short edge beside it | the height the refusal names, or merge the short edge (`combine_blocks`, a station moved) and band again |
| A fan at a trailing edge (`add_ring`, `add_band`) | the body-normal lines collapse into a downstream fan: Mesh OK, but a poor wake | a wake band: `add_ring ... fan=false` (or `open_at=X,Y`), then `add_rows` its two spokes to the outlet (`examples/naca0012_cgrid.txt`); external flow: also the engine's C-grid (`engine.md`) |
| A core spoke crossing a band front in a narrow gap | the gap holds too many layers | engine `--span-gaps`, or move the ring |
| A 3-degree corner in a seam wedge at a trailing edge | the medial anchor is outside the feature's sector | the engine's wake construction (`--wake`) |
| Component length ratio far above 20 in the engine report | band-to-core coupling (a topology defect); far-field fan-out alone is informational | change the topology |
| A band that thins to nothing on a thin body | the annular producer's inscribed-disk cap | the engine's C-grid producer |
| A gate slid off a sharp vertex | the sharp vertex must be a block corner | pin it and re-spread the gates (engine `candidates`/`apply --move`), or `split_edge` |
| `quality`/`size` aspect ratio above 1000 ("High aspect ratio cells found" in checkMesh) in a strip running from a wall to an outlet | wall spacing carried along a strip far from the wall | `size` releases such strips itself (a blunt plate's wake: 1846 before, 585 now); where its note still names blocks, a `start_width` by hand on the named block's thin side at its outlet end (`set_edge_grading`), then `quality` |
| `size refused: the sized mesh folds ... the spline edge(s) EDGE (segment k of n, ... control point i) run backwards`; the same in `smooth mode=shape`'s count-free fold refusal; `quality`'s warning `spline edge EDGE ... runs backwards` | a spline whose short end segment sits beside a long one loops back (Catmull-Rom); cells graded into the loop fold (round-2 high-lift ring edges, 0.0014 beside 0.0165). The operators no longer write such splines, nor a band front turning back to reach a mitred or tilted end spoke (round 4: the splitter's concave junctions, a high-lift band beside `end=`); an engine session or a hand edit can | `remove_control_point EDGE i` on the edge and its band front (or `set_edge_type EDGE line`), then `size` again |
| checkMesh aspect ratio in the tens of thousands on a pseudo-2D case | `patch` z faces: the span counts | `set_export_settings z_min_patch_type=empty z_max_patch_type=empty` |
| Negative volumes beside a mitred station in a script-built layout | spline interior points from the plain normal while the end stations carry the mitre | build interior points from the local normal and correct them linearly onto both end stations (the operators already do) |

## A hand-built sharp trailing edge (layout scripts)

Two rules a layout script must keep, invisible until checkMesh fails
(`cases/a_airfoil`): the collar spokes at the trailing-edge end of each side
carry the **cross-wake direction**, not the corner bisector, or the band
blocks fold across the cut; and a normal offset of height $h$ from a side
meeting the edge at half-wedge angle $\beta$ overshoots the cross-wake line
by $h \sin\beta$, so the first station on each side is held out from the
edge by more than that.

## What the tools do not check

Every block is checked strictly convex when it is made and by `validate`,
and two blocks on the same side of a shared edge are refused. Blocks lying
over one another without sharing an edge (a grid over a band) are refused by
the operator that would make them (`add_grid` leaves such a cell out and
names it), and `validate`, `quality` (verdict and warnings) and `check` name
them; what that check does not see is two thin blocks crossing near their
ends with no corner, edge sample or interior point of either inside the
other. Render every corner, gap and bridge, and run `check`.
