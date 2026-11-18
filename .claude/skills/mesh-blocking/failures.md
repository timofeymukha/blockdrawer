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
   topology is the remedy. `hold=` keeps vertices you placed on purpose.
2. **`repair_block ID`** on the worst block, repeated: it tries moving the
   block's interior vertices (curved edges carried), merging it across an
   internal edge, splitting its edges and regrading an edge to its opposite
   edge's series, judges each on the block and its neighbours by the corner
   screen and the sampled interior at real counts, and applies the best (a
   worst cell half a degree better with no more severely sheared cells, or
   a tenth of them removed with the worst cell kept). Stop when it reports a structural defect or "nothing to
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
| Two opposite edges graded towards opposite ends | crossed gradings shear every cell between them; the corner screen does not see it | `repair_block` (regrade move) or `set_edge_grading` |
| Size jump around a short edge carrying many cells | the edge shares a count component with a long fine edge; counts are never lowered | `set_edge_cells` on that component, then `size FIRST CORE keep_counts=true` |
| Jump beside an edge that wants fine cells at both ends | one geometric series cannot serve it (multi-section grading is out of scope) | `split_edge` where the size peaks (`size` refuses a split that leaves a sliver: move the cut by hand) |
| A sharp or rounded wall corner that is not a block corner | the band block spans the corner | `split_edge` so the corner is a vertex |
| A gate in the wrong place along the wall: a band block sheared although its spokes are fine | the wall vertex sits off where its spoke wants it | `smooth slide=true` (slides it along the wall, re-cut from the input wall, carrying the band spoke), or `slide_vertex V fraction=F` by hand and then move the spoke's front with `move_vertex` |
| A wake or far-field block with a corner near 180 degrees | a ring vertex in the wrong place | `move_vertex` (with `carry_curves=true` for curved edges) or `split_edge` with a centre vertex |
| A folded band block at a sharp convex feature | the feature needs more incident blocks | a fan (`add_band` and `add_ring` make one above 60 degrees of turning), a seam or a wake (engine) - not a thinner band |
| `add_fill` refused: "opposite sides need as many edges" | the side stations do not pair up | the `split_edge` or station the refusal names (the short side's segment facing the extra station), or remove a station from the long side |
| `add_fill` refused: a fill block "would not be strictly convex" | stations on opposite sides do not face each other, or a side bends further than one transfinite map follows | move or add stations; fill the region in parts (a side corner in between) |
| `add_fill` refused: "bound no region" or "ambiguous" | the corners are not in order round the region, or a side runs two ways between its corners | reorder the corners; name that side's vertices with `side1=...` |
| `add_band` refused: "would fold ... concave corner" | a mitre at a concave corner moves the offset end past a short edge beside it | the height the refusal names, or merge the short edge (`combine_blocks`, a station moved) and band again |
| A fan at a trailing edge (`add_ring`, `add_band`) | the body-normal lines collapse into a downstream fan: Mesh OK, but a poor wake | for a production wake, the engine's C-grid (`engine.md`) |
| A core spoke crossing a band front in a narrow gap | the gap holds too many layers | engine `--span-gaps`, or move the ring |
| A 3-degree corner in a seam wedge at a trailing edge | the medial anchor is outside the feature's sector | the engine's wake construction (`--wake`) |
| Component length ratio far above 20 in the engine report | band-to-core coupling (a topology defect); far-field fan-out alone is informational | change the topology |
| A band that thins to nothing on a thin body | the annular producer's inscribed-disk cap | the engine's C-grid producer |
| A gate slid off a sharp vertex | the sharp vertex must be a block corner | pin it and re-spread the gates (engine `candidates`/`apply --move`), or `split_edge` |
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
and two blocks on the same side of a shared edge are refused; edges crossing
elsewhere and blocks overlapping without sharing an edge are not checked
before `check` (`add_fill` alone refuses a block over an existing block's
centre or an existing vertex). Render every corner, gap and bridge, and run `check`.
