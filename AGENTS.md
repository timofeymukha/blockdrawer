# BlockDrawer contributor guide

## Product boundary

BlockDrawer is a graphical editor for 2D block topologies with straight,
circular-arc, piecewise-linear, or spline edges that are extruded into OpenFOAM
hexahedra. Independent point-list reference curves can guide topology design. It
does **not** generate a mesh. Its primary artifact is a valid
`system/blockMeshDict`; OpenFOAM's `blockMesh` remains the mesher.

The current scope intentionally excludes polySpline edges, multi-section grading,
cyclicAMI/ACMI transforms, and 3D editing. The two extrusion-face patches are
configured as global export settings rather than selectable canvas geometry.
Preserve extension points for additional patch properties instead of encoding
them in the canvas widgets.

## Stack and commands

- Python 3.10+.
- Tkinter/ttk for the cross-platform GUI (part of the normal Python installer).
- Python standard library only at runtime and in tests; no compiled dependencies.
  The one exception is optional: PNG rendering imports Pillow when present and
  otherwise fails with a message pointing at SVG output.
- Launch: `python -m blockdrawer`
- Headless work: `python -m blockdrawer.cli COMMAND` (installed as
  `blockdrawer-cli`). Subcommands: `new`, `describe`, `quality`, `render`,
  `export`, `validate`, `check`, `apply`, and `commands`.
- Agent tools: `python -m blockdrawer.mcp_server` (installed as
  `blockdrawer-mcp`; needs `pip install blockdrawer[mcp]`). Keep the MCP tool
  surface and the CLI in step: both must go through `commands.py`.
- Meshing methodology for agents: `.claude/skills/mesh-blocking/`.
  `SKILL.md` is the procedure (at most 250 lines, a test keeps it there):
  Steps 1-8 - collect, choose the route from a table of measured outcomes,
  build (operators by hand or the engine), shape, size (`method=distance`
  only as a comparison), polish, judge, deliver - each with its
  command, what to read and the numbers that mean go on. Reference files
  opened from those steps: `engine.md` (the engine per configuration, its
  options and report), `sizing.md` (inputs, `verification`, measured
  limits, sizing by hand), `failures.md` (signature -> tool) and
  `examples/` (four operator batches - the C-grid one with a wake band by `fan=false` and `add_rows` - with their follow-on lines and
  checkMesh results; `tests/test_skill.py` builds them and checks that every
  command, flag, option and Makefile target named in code spans exists). The
  MCP instructions point at the steps by name and carry mechanics only.
  **Every change to the topology engine that alters what it builds, refuses
  or measures - a new template, far-field option, acceptance term or CLI
  flag - updates `engine.md` (and the route table when the route changes) in
  the same commit**, and the MCP instructions when the agent workflow
  changes; a test checks the pointer.
- Browser editor: `python -m blockdrawer.web [SESSION]` (installed as
  `blockdrawer-web`). Standard library only; it opens a tab on a loopback
  port and must stay functionally equivalent to the Tk editor.
- Symmetrize a session: `python symmetrize_session.py SESSION --axis x|y`
- Tests: `python -m unittest discover -s tests -v`
- Supplied OpenFOAM 2606 integration check: `make integration-test`
- For another installation, set `BLOCKMESH_COMMAND` to a command prefix that can
  accept `-case <path>`, then run the test suite.
- Research prototypes: `make research-test`, `make research-periodic-hill`,
  `make research-cases` and `make research-30p30n`. These live in
  `experiments/`, need NumPy and Pillow, and are excluded from the normal suite; see
  `experiments/agentic_topology/README.md`. The research command line is
  `python experiments/agentic_topology/research_cli.py
  {run,describe,cavities,candidates,apply,focus}`. `run` prints one line per stage to stderr
  as it starts, with the seconds since the run began (`pipeline.PROGRESS`,
  set only by `run`; `mark` for a stage, `stage` for an opt-in trial
  enclosing others, two levels shown).

## Architecture

- `blockdrawer/domain.py`: dependency-light dataclasses, result records, edge type
  aliases, `TopologyError`, and canonical `edge_key()`. Import domain types from
  here in serializers and algorithms; `model.py` re-exports them only for backward
  compatibility.
- `blockdrawer/model.py`: authoritative model state, edge/curve evaluation, and
  global validation. `spline_hooks(path)` lists the segments of a
  Catmull-Rom path that run backwards along their own chord (a segment much
  shorter than its neighbour takes a tangent several times its length and
  loops); `hook_culprit` names the control point to remove (the end of the
  hooked segment beside its longer neighbour), `unhooked_points` removes
  those points pass by pass - all of a pass at once, both ends where the
  neighbours are equal, so a path and its reverse keep the same points -
  until nothing hooks (the construction operators' rule: no length ratio
  is chosen), and `hooked_spline_edges`/`describe_hook`/`hooked_edges_text`
  give the edge, segment and point for `size`'s and `smooth`'s fold
  refusals and `quality`'s warning. `MeshModel.interface_pairs()` is the one definition of the
  interface pairs (across every internal edge, at each end, the two blocks'
  transverse edges, `transverse_edge`) that `quality`'s size jump, `size`
  and `smooth` use. Vertices may be standalone or shared by counter-clockwise
  quadrilateral blocks. Edges are derived only from block vertex pairs. Optional
  geometry, grading, boundaries, and reference curves are keyed or stored here.
  Repeated spline evaluation uses bounded, value-keyed path evaluators that are
  never persisted; bulk consumers use `edge_points()` and
  `edge_node_fractions()` to reuse geometry and grading setup.
- `blockdrawer/grading.py`: pure, numerically stable conversions among total
  expansion, cell-to-cell expansion, and start/end widths.
- `blockdrawer/spacing.py`: UI-independent persistent endpoint spacing links,
  endpoint-width solving, chain traversal, synchronization, and topology-remap
  helpers. Geometry mutations do not invoke this propagation.
- `blockdrawer/topology.py`: the `TopologyOperationsMixin` implementation of
  block add/remove, conformal split, and conformal combine. These compound
  mutations own their rollback logic; the public methods remain available on
  `MeshModel` through composition.
- `blockdrawer/reference_geometry.py`: reference-curve CRUD and model-level
  projection orchestration. `blockdrawer/projection.py` contains the independent
  cubic intersection, closest-point, and fitted-spline numerical algorithms.
  The fit-ranking proxy uses coefficient-only cubic spans; exact bounded spans
  remain reserved for geometric closest-point measurements and pruning.
- `blockdrawer/preview.py`: UI-independent, visualization-only implementation of
  OpenFOAM's edge-weighted transfinite block interpolation and a bounded cache
  keyed by the mesh state that affects sampled points. It must never mutate the
  model or become an export dependency. Preview construction accumulates row and
  column bounds while building the sampled grid rather than rescanning lines.
- `blockdrawer/geometry.py`: UI-independent parsing of reference-geometry point
  files. Reference curves themselves are named model entities, independent of
  block vertices and OpenFOAM edge geometry.
- `blockdrawer/session.py`: versioned JSON persistence. Keep this independent of
  Tk so sessions can be tested and converted headlessly.
- `blockdrawer/symmetry.py`: UI-independent whole-session half-mirroring. It
  retains the selected source half and rebuilds the other half with deterministic
  `mirror_` IDs, preserving mesh-edge metadata in physical path direction.
  `symmetrize_session.py` is its thin command-line entry point.
- `blockdrawer/commands.py`: the UI-independent command registry. Every public
  model mutation is a named `CommandSpec` with typed parameters; commands are
  parsed from shell-like text (`set_edge_cells v0-v1 20`) or JSON objects
  (`{"op": ..., ...}`), coerced against the current model, and applied.
  `apply_commands()` works on a copy and is atomic for the whole batch. This is
  the single vocabulary shared by the CLI, batch files, and any future MCP
  server; add new model operations here as well as to the model.
  `batch_digest()` is the quiet account of a batch the MCP `edit_session`
  returns by default and `apply --quiet` prints: commands counted by name,
  vertex/block/curve/patch IDs created and removed (a before/after diff of
  the models, consecutive runs written `v4..v93` by `compress_ids`), and
  per-command notes for the results worth reading (`_note`: repairs,
  splits, merges, fits, spacing links left out of sync), capped at
  `MAX_NOTES`. A new command whose result an agent must read adds a case to
  `_note`. A note's values are scalars, short strings or flat lists, since
  the text digest elides nested ones: `size`'s (`_size_note`) gives
  `total_cells`, `field_cells`, `budget`, `budget_binding`, the field and
  result verdicts in one `verification` string (faces above 70, worst
  face, skewness, aspect ratio and, above 1000, the cells and block), `first_width` per wall
  patch (smallest..largest first cell height normal to the wall at its
  band spokes, `quality.wall_first_widths`, against the asked one in
  percent, and the two spokes named), `worst_jump` (quality's interface measure on the sized session,
  with the counts above 2.5/5/20), `jump_cost`, `splits`, `moves`, `guard`,
  `refused`, `note`, empty ones left out. Parameter kinds `blocks` (block IDs) and `numbers` (a list of
  floats, e.g. a bbox) serve `smooth`; `locations` (`_coerce_locations`:
  `x,y` points or vertex IDs, separated by `;`, each ID its vertex's
  position) serves `add_ring`/`add_stations` `points=`. `resolve_position` gives
  `add_vertex` and `move_vertex` (`_POSITION`) positions relative to
  existing vertices: polar `r`/`theta` about `center` or the vertex
  `about`, else each coordinate from exactly one of the number, `at`
  (both), `x_of`/`y_of`, or (moving) the vertex's own; `dx`/`dy` added
  last; a coordinate given twice or missing is refused by name.
- `blockdrawer/command_line.py`: shared shell-like tokenization for textual
  commands and OpenFOAM command prefixes. POSIX hosts use normal shell escaping;
  Windows preserves path backslashes while retaining POSIX quote grouping for
  WSL, container, and SSH wrappers around the Linux OpenFOAM tools.
- `blockdrawer/describe.py`: complete JSON description of a model plus a compact
  text rendering. Entities carry the same IDs the command registry accepts.
  Sections are named in `SECTIONS`; `resolve_sections` also takes the
  aliases of `SECTION_ALIASES` (`patches` for `boundaries`), for the CLI's
  `--section` and the MCP `sections` alike.
  `query_description()` restricts it to the union of `only` IDs (space- or
  comma-separated, `resolve_only`; an ID naming nothing is an error), the blocks
  overlapping a `bbox` (sampled curved edges included) and the vertices in
  it, and an edge's opposite-edge count `component` (adding a `component`
  section), and adds `unassigned_chains` (exterior edges without a patch
  joined into open or closed chains). A filtered view keeps every patch that
  owns a kept edge, with its edge list restricted and `total_edges` given,
  and a named edge (either vertex order) lists all its control points.
- `blockdrawer/quality.py`: mesher-style heuristics computed from the stored
  topology, curves, and grading: first-cell corner angles, non-orthogonality,
  equiangle skewness, corner cell aspect ratios, cell-to-cell growth, and cell
  size jumps across internal edges. Thresholds are a dataclass so callers can
  tune them; `checkMesh` remains the authority. With `interior_cells` (the
  CLI and MCP default `DEFAULT_INTERIOR_CELLS` = 256; the library default 0
  keeps the corner screen alone for existing callers such as the research
  pipeline) every block carries an `interior` record from
  `blockdrawer.sampling`, the summary an `interior` block, and `verdict()`
  the numbers a hand-built session is judged by: valid (validates and
  exports), untangled, real counts (no block coarsened), worst sampled
  non-orthogonality with block and location, severe faces, skewness and
  the cell aspect ratio with its block and the cells above 1000. Folded
  cells, interior non-orthogonality above the threshold and cells above
  checkMesh's aspect limit become warnings naming the block; a spline
  edge with a segment running backwards (`model.spline_hooks`, any
  session: engine, hand edits) is warned of with the segment and the
  control point to remove, ranked as an angle defect 90 degrees from 90,
  and counted in the summary's `hooked_spline_edges`. Every
  warning carries a rank (folded first, then angle defects by degrees from
  90, then size defects by ratio to their limit); `ranked_warnings(N)` and
  `max_warnings` list the worst N, and `worst_blocks` ranks by folded cells
  then sampled non-orthogonality when sampled. `wall_first_widths` (the
  report's `first_widths`, also `size`'s `bands`) gives per wall patch the
  first cell height normal to the wall at its band spokes: at a wall
  vertex an edge on no wall patch is a spoke of the wall edge there whose
  inward normal (end tangent of curved edges) it is closest to, when within
  `SPOKE_CONE` (45 degrees, inclusive, so a right-angled corner's bisector
  counts); the height is its first node's above that edge's tangent line;
  the extremes' spokes are named and the edges left out counted
  (`other_edges`): a sharp edge's wake line, an edge continuing the wall's
  line.
- `blockdrawer/sampling.py`: the shared interior sampler. `block_grid()`
  builds a block's nodes at its real counts and grading with the preview's
  transfinite interpolation, coarsened above `max_cells` a side by keeping
  every n-th node from both ends (a coarsened side has from about half
  to twice the cap); `cell_angles()` measures corner angles, folds and severe cells
  (a corner within `SEVERE_ANGLE` = 20 degrees of 0 or 180);
  `sample_model()` adds checkMesh's face non-orthogonality - the angle
  between the two cell centroids' vector and the face normal - over faces
  inside blocks and on shared edges, a shared-edge face counted for the
  earlier block as blockMesh's owner, so per-block severe faces line up with
  the `nonOrthoFaces` set mapped to blocks. At the real counts it reproduced
  checkMesh's maximum and severe count on the 30P30N and engine sessions.
  It also measures checkMesh's face skewness (`_skewness`:
  `primitiveMeshTools::faceSkewness` on interior and shared faces,
  `boundaryFaceSkewness` on domain-boundary sides) into `max_skewness` per
  block and in the summary and the quality verdict; it matched checkMesh to
  two decimals on the engine, 30P30N and sized sessions (and overestimates
  the grooved tandem's 0.93 as 1.12). A `cyclic` side is coupled, as in
  checkMesh: both its skewness and non-orthogonality are measured against
  the partner cell's centre carried across the pair's motion
  (`cyclic.match_cyclic_pair`), on both sides (checkMesh counts both halves
  of a severe coupled face); a pair that does not correspond falls back to
  the boundary measure. The boundary formula on the benchmark cascade's
  periodic side read 5.25 where checkMesh read 3.26; coupled it gives
  3.259 = checkMesh, and the sheared periodic strip in the tests matches
  checkMesh's 53 faces above 70 and skewness 0.45. It also measures
  checkMesh's cell aspect ratio (`_AspectTally`,
  `primitiveMeshTools::cellClosedness`: the largest over the smallest sum
  of the face area vectors' Cartesian component magnitudes over the solved
  directions, x and y when both z patches are `empty` (`aspect_depth`),
  else x, y, z and the hydraulic ratio; a coarsened sampled cell's sides
  divided by the real cells they span; `planar=True` takes x and y
  whatever the z patches, as `size` does) into `max_aspect_ratio`,
  `worst_aspect_cell` and `high_aspect_cells` (above `ASPECT_LIMIT` =
  1000, checkMesh's) per block, and the summary's `worst_aspect_block`;
  it equalled checkMesh to two decimals on the eleven round-2 benchmark
  sessions and on a default-sized blunt plate (1846.52, 217 cells).
  `blocks=` samples a
  subset, leaving out faces shared with blocks outside it (and a coupled
  side whose partner's block is left out). `smooth` and `size` refuse a
  result above 4 (checkMesh's limit) from an input below it.
  `repair.py` judges its candidates with `block_grid`/`cell_angles`; item
  2's smoother is meant to use the same code. Pure Python, standard library.
- `blockdrawer/repair.py`: `repair_block`, the local repair an agent would
  make by hand: for one block it tries moving its interior vertices (eight
  directions and the corner bisector, three step sizes, incident curved
  edges carried along), combining it across each internal edge, splitting
  each edge, and regrading each edge to the series of its opposite edge in
  the block's physical direction (crossed opposite gradings shear every
  cell between them whatever the corners say), each on a copy of the model,
  and applies the candidate that most improves the block and its
  neighbours - judged by the corner screen and by the preview's transfinite
  interior sampled at the real cell counts (up to 64 a side, through
  `blockdrawer.sampling`), since the
  corner screen alone passed a merge that folded between the corners and a
  coarser sampling passed a fold of one real cell. A move must raise the
  worst cell by half a degree without adding severe cells, or keep it and
  remove a tenth of the *severe* sampled cells (a corner within 20 degrees
  of 0 or 180, checkMesh's 70 degrees of non-orthogonality on a sheared
  cell): a seam wedge's apex angle is what it is however the cells are
  distributed, and the severe count is what a grading move acts on
  (`RepairScore.improves`; the high-lift trial applied a move that raised
  the worst cell and took the severe count from 1172 to 1193). Boundary vertices never move; with
  no improving move nothing changes and the result says why: a sound
  neighbourhood (no severe cell, every angle at least 45 degrees from
  degenerate - `SOUND_ANGLE`) is reported as nothing to repair, pointing at
  the quality report for size jumps and aspect ratios, and anything else as
  a structural defect. It is a registry command, so the CLI, batches and the MCP server
  all have it; `checkMesh` stays the authority.
- `blockdrawer/smooth.py`: `smooth_model`, the global vertex smoother behind
  the `smooth` registry command. Free vertices are those used by a block, on
  no exterior edge, in the optional `blocks`/`bbox` scope and not in `hold`.
  The judge is checkMesh's face non-orthogonality on the sampled interiors at
  the real counts and grading, with its own sampled state: two real cells at
  each end of every edge and at `samples` stations (`_indices`), faces
  measured only between real cells (merged cells across a thin first-cell
  interface read 75 degrees where checkMesh read 43), interpolation weights
  precomputed because a similarity keeps the graded fractions, and edge
  samples carried by the same similarity as the curves. Phase 1 lowers
  sum(tan^2 phi) by a Gauss-Seidel pattern search (eight directions, halving
  steps) taking a move only when it does not raise the worst face of the
  blocks it touches; phase 2 moves the vertices of the blocks holding the
  global worst face to lower it; a last phase-1 pass recovers the sum.
  Constraints on every trial position: no folded sampled cell, no block
  corner newly within 20 degrees of 0 or 180, every band spoke (an internal
  edge from a vertex of a `wall` patch) within `band_tolerance` (5 percent)
  of its length and, at a smooth wall vertex with a single spoke, no further
  from the wall normal than it was (`SMOOTH_WALL_TURN`, `wall_angle`); every
  edge within `length_tolerance` (50 percent) of its length; the quality
  screen's width pairs (interface jumps, corner aspects) not crossing
  `size_cap` 2.5 / `aspect_cap` 100 where below, else growing by at most
  `size_slack`, an interface jump below `size_ceiling` (`SIZE_CEILING` = 5)
  not past it. The result is verified with `sample_model` at 256 cells a
  side and applied only when untangled, with no more severe faces and no
  higher maximum and - the polish only - not crossing checkMesh's aspect
  ratio limit 1000 nor gaining cells above it (`sampling.aspect_worse`,
  shared with `size`; measured as the sample measures it, the export
  settings' depth included), else scaled back by halves; when the full
  displacement crossed the limit, `smooth_model` runs the optimisation
  once more with the free vertices of the blocks it took past the limit
  held and applies whichever result has fewer faces above 70, then the
  lower maximum (`_smooth_once`, `aspect_blocks` in the result and the
  digest note; `reason` says how the limit bound). `mode=shape` judges
  stale counts (size sets new ones), so the limit does not apply there; one `MeshModel.move_vertices`
  call (atomic, curves carried by the chord similarity), so one history
  entry. Measured trade-offs are in the README. Boundary vertices never move.
  `mode="shape"` (`smooth mode=shape`) runs the same optimiser on the
  count-free block map (`shape_cells` even parameter fractions a side, all
  faces real, untangled at `fold_cells`; `sampling.sample_model(uniform=N)`)
  without the size limits and with one length limit - every edge keeps
  `shape_length_ratio` (`SHAPE_LENGTH_RATIO` = 0.1) of its input length,
  in the search and at every fallback scale, since the count-free optimum
  collapsed a ring edge to 0.0024 of its length (a size jump of 269 after
  `size`) while the square cylinder's gain needs 0.089 - keeps the band
  rule, and reports the stale real-count verdict as
  `real_before`/`real_after`; a stale fold is refused unless
  `allow_stale_fold`. A refusal over folded cells - already folding on the
  count-free map or the real counts, or a stale fold - names the folding
  blocks, most folded first, with their counts (`sampling.folded_blocks`,
  at most `FOLD_BLOCKS_NAMED`); where those blocks have hooked spline
  edges the count-free refusal names them, the segment and the control
  point to remove (`model.hooked_edges_text`, as `size`'s refusal does)
  instead of saying the blocks fold whatever their counts. Both modes report `min_edge_ratio`/`min_edge` and
  `max_edge_ratio`/`max_edge` (edge length over input length, edges with a
  moved end) in the result, the reason text and the digest note. Two more
  shape-mode limits, off by default (`SHAPE_PAIR_RATIO`,
  `SHAPE_SPREAD_RATIO` = 0), held in the search and at every fallback
  scale like the floor: `shape_pair_ratio` keeps every interface pair's
  length ratio (`MeshModel.interface_pairs`, the pairs `quality`'s jump
  compares and `size._links` relaxes over) within that factor of its
  input, `shape_spread_ratio` keeps every count component's spread
  (longest over shortest member, `size.count_components`) from growing
  past that factor; the result, reason and note give `max_pair_change`/
  `max_pair` and `max_spread_growth`/`spread_edge`. Neither passed the
  default rule over the 20-session corpus (README table): pair limits and
  higher floors lose the square cylinder's gain, the spread limit cost the
  NACA 4412 3.5 degrees and a face above 70.
  `keep_lines=True` (off by default in both modes: measured in the README)
  finds each free vertex's straight block lines (`straight_lines`: two
  internal `line` edges at it sharing no block, collinear within
  `LINE_ANGLE` = 1 degree); on one line `search` tries only the two
  directions along it (a band spoke's projection may slide it along the
  line, nothing else) and `slide` does not carry it as a front; on two it
  is dropped from the free set (`line_vertices`, `line_fixed`). `slide=True` adds the boundary
  vertices `slide.slidable` accepts (in scope, not held) as 1D variables:
  `_State.attach_slides` keeps a scratch model, `_move_slide` re-cuts the
  two boundary edges from the chain (`Chain.recut`) and resamples them,
  `slide` (beside `search`) tries +-h of arc length, carries each free band
  spoke end by the wall's tangent rotation (anchors are evaluated where the
  wall vertex is now, normals turned with it), checks `lengths_hold` on
  every moved vertex (and, in shape mode, the two re-cut edges'
  `length_tolerance`), and judges with `local_many`. The application passes
  the re-cut edges to `move_vertices(edge_geometry=...)` and refuses a
  result whose re-cut edges leave the input boundary by more than
  `SLIDE_TOLERANCE` of the chain length; the result reports `sliders`,
  `slid`, `max_slide`, `boundary_deviation(_relative)`. Off by default;
  measured trade-offs in the README.
- `blockdrawer/slide.py`: boundary vertices sliding along their boundary.
  `slidable` (two exterior edges of one non-cyclic patch, boundary turning
  at most `SLIDE_CORNER` = 30 degrees there), `chains` (maximal chains
  between pinned vertices; a loop is pinned at its smallest vertex ID),
  `Chain` (the input path by arc length, exact for lines, polyLines and
  arcs, `DENSE` samples for splines; `recut` gives line/polyLine through
  the original breakpoints, an arc of the same circle, else a spline
  through the original curve sampled at a quarter of its point spacing and
  closing in on old joints; `distance` to the input path), `recut_edges`,
  `deviation`, and the `slide_vertex` registry command (fraction or
  distance towards a neighbour; internal curved edges carried, spokes not).
- `blockdrawer/size.py`: `size_model`, behind the `size` registry command.
  `SizeField.size(x, y, u)` is directional: for each wall segment (bucket
  grid; per-patch first widths) the across size `first + (growth-1) d`
  along the segment's normal - also past its end, so the backward step's
  lip grades the upstream wall - and `wall_aspect` times that along it,
  combined as an ellipse metric; the minimum over segments, capped at the
  core. No edge classification. `_bounds` is a Dijkstra over edge ends:
  seeds are the field size along each edge at its ends; relaxations are the
  quality screen's interface pairs (`_links` over `interface_pairs`,
  factor `interface_growth`) and
  along-edge gradation (`+ (growth-1) L`), both non-decreasing, so each end
  settles once. `_strips` groups opposite sides within
  `OPPOSITE_LENGTH_RATIO` through the blocks with their orientation, and
  the two edges of every cyclic pair (`cyclic.match_cyclic_pair`, end to
  the end it maps onto) so a pair shares one physical grading;
  `_components` joins the count components holding a pair's two edges (the
  model's own components do not: `set_edge_cells` does not propagate across
  a pair), and `_split_strips` refuses a component holding a cyclic edge,
  whose partner the split would not cut;
  `_ratios` gives each strip the one total ratio needing the fewest cells to
  keep every member within its end bounds (`_fit_count`); a mean ratio left
  the step's inlet column needing 313 cells. Counts per component: max of
  the profile demand (integral of ds/size of the field capped by the
  gradation from both bounds) and the strip fit; an edge whose profile
  peaks `SPLIT_RATIO` above both ends is two-ended and `_split_strips` cuts
  up to `SPLITS_PER_ROUND` such strips with `split_edge` at the peak,
  refusing a cut that leaves any piece shorter than one cell of the size
  the field wants along it (a 30P30N cut left 0.00065 where 0.017 was
  wanted; the refusal names the piece by the existing end it runs from
  and the edge it is of, since the undone split's vertex does not exist),
  and undoing a round that raises the jump cost (at most
  `SPLIT_ROUNDS`). That is the field's state; `budget` (default
  `DEFAULT_BUDGET` = 1.5 times its cells; a smaller budget coarsens the
  field to budget/1.5 first) is then spent by `_spend`, a greedy optimiser
  of the jump cost - over the quality screen's interface pairs, the square
  of how far log(width ratio) exceeds log(`interface_growth`). Moves: regrade
  one strip's log ratio by +-0.5/1 log(cap) (free, never coarser than the
  field at an end, never faster than `growth` per cell) first; else the
  component raise (by `RAISE_STEP`, its strips refitted to keep one end's
  width) with the largest drop per cell; it stops at the first raise the
  budget cannot pay, so a smaller budget's moves are a prefix of a larger
  one's and the cost is monotone in budget from 1.5 times the field's cells
  up. Every move is checked on the sampled grid of the touched blocks and
  their neighbours (`sample_model(blocks=...)`, `GUARD_CELLS` = smooth's
  `VERIFY_CELLS`, 256 a side - at 64 it was blind to blocks of hundreds of
  cells and the fine 30P30N gained 7 and 53 faces above 70): a fold, more
  faces above 70 degrees, a worst face above the field's plus
  `VERIFY_TOLERANCE` or skewness crossing 4 undoes it and makes it tabu.
  Before spending, when the input at its own counts folds no cell,
  `repair_folds` takes each block the field state folds (most folded
  first) and regrades a strip through it a quarter, half, three quarters
  or all the way to uniform - past the field's bound at an end if need be
  - taking the move with the fewest folds left, then the smallest change,
  before trying a raise; the fold it removes is a spline edge looping back
  in a short end segment (`model.spline_hooks`) with nodes graded into the
  loop (round-2 high-lift: 46 cells in four main-element ring blocks, all
  removed by four regrades). A result that still folds where the input did
  not is refused by `size_model` naming the blocks and, per hooked spline
  edge among theirs, the segment, its length beside its neighbour's and
  the control point to remove (`_fold_refusal`); the continuity rounds keep
  folds too. Then `repair_shape`, judging at 256 a side, raises a
  component, flattens a strip or gives a side its opposite side's grading
  in the block the field leaves above skewness 4 when the input was below
  (folds refused, faces above 70 allowed: checkMesh fails only the
  skewness), then in the block with the worst face above 70 degrees while
  that count or the worst face falls, then (`repair_aspect`) in each block
  above checkMesh's cell aspect ratio `ASPECT_LIMIT` (1000, measured in the
  plane: `sample_model(planar=True)`, the depth is the export settings'),
  worst first: free moves before raises - a strip through the block
  released all, half or a quarter of the way to its members' own
  `_FieldGrading` series (bounds first lowered to `interface_growth` times
  the partners' widths now; no interface pair at its members' ends past
  the larger of its own jump and `JUMP_LIMIT`), or the regrades above -
  taken when the block's cells above the limit (then its ratio) fall with
  no fold, no new face above 70, skewness not past 4 and the worst face
  within `SHEAR_ANGLE` (or 0.5 above where it was). A released strip keeps
  its per-member ratios (`released`): spending neither regrades nor refits
  it and `grading=field` leaves it (`_FieldGrading(fixed=...)`). Every
  guard - the per-move one, the verification, the continuity rounds,
  `grading=field`'s settle and pass, the repairs themselves - refuses a
  state that crosses 1000 or gains cells above it (`_aspect_worse`); what
  is left is listed in `verification`'s `high_aspect_blocks` and named in
  the note (round-2 blunt plate: field state 1533 -> 559 by two releases,
  result 585 after the polish against 1018, cells 94,784 -> 79,194). The
  repaired field state is the
  reference: `_verify` samples at 256 a side and at real counts every
  block whose worst face there exceeds `HOT_ANGLE` (65; at 256 the fine
  30P30N's b39 read 6 faces above 70 where checkMesh counts 7), and the
  spent state must have no more faces above 70, no more folds, a worst face
  at most `VERIFY_TOLERANCE` (0.5 degrees) higher and skewness not past 4;
  otherwise the components and strips through each offending block (then
  its neighbours, then all) go back to the reference values until it holds
  (`undone_moves`, deterministic). The result's `verification` gives both
  verdicts. `size_model` refuses a result above skewness 4 from an input
  below.
  `_continuity_splits` then splits strips the spent state leaves coarse
  against finer neighbours at both ends (`_stranded`), solves and spends
  again, and keeps the round only when the jump cost falls, the worst
  jump does not rise and the verification still holds against the first
  field state (`CONTINUITY_SPLIT_ROUNDS`). Counts and total
  ratios are written directly (links kept and reported); one validated edit.
  `quality` reports `interface_jumps` (above 2.5, 5, 20) in its summary.
  `keep_counts=true` keeps every count, refits the strips' ratios from the
  bounds and spends with a budget of the cells there are (regrades only, no
  raise), so an earlier sizing's regrades are made again: three cylinders
  after its polish, worst jump 23.5 and 30 above 5 before, 6.53 and 13 now
  (6.15 and 12 before re-sizing). `grading=field` (the default since the
  20-session corpus measurement in the README; `grading=strip` leaves it
  out) adds `_field_pass` after spending:
  one shared ratio per strip makes its members' end widths proportional to
  their lengths (every wall spoke's first width over its length was equal
  within each strip on the benchmark) and carries a wall member's
  clustering to far members (NACA 4412: 0.00036 at a far-field corner where
  the bound was 0.5). `_FieldGrading` gives each member at the final count
  the series keeping its finer-bounded end at its bound (`tighten` lowers
  the bounds to `interface_growth` times the partners' widths), clamped
  finest bound first to `SHEAR_ANGLE` (20) of tilt against the members
  beside it; `settle` takes each strip, finest first, all, half or a
  quarter of the way from the spent shared ratio (`FIELD_MODES`) where the
  worst touched interface jump stays within the spent one or `JUMP_LIMIT`
  (2.5) and the sampled guard at 256 a side finds no new fold, face above
  70, skewness past 4, nor a worst face above min(`SHEAR_ANGLE`, the spent
  state's worst) that was not there; refused strips are retried with their
  refused interface partners; the pass is verified like spending and
  dropped whole if worse. Counts never change. Made the default by a rule
  fixed beforehand (no session gains a face above 70, a jump above 5 or a
  checkMesh failure; corpus jumps above 2.5 do not rise; no maximum +2
  degrees; cells +5 percent; no wall patch's worst first-height deviation
  +5 points and the mean falls): every term held, jumps above 2.5 257 ->
  177, mean worst deviation 20.4 -> 12.7 percent.
- `blockdrawer/construct.py`: construction operators behind the registry
  commands `add_grid`, `add_band`, `add_rows`, `add_ring`, `add_stations`,
  `add_fill` and `set_boundary_by`, derived
  from what the two hand builds coded themselves (coordinate-deduplicated
  vertices, polar points, ray-square hits, offset circles, arcs, edge
  marking). Each runs on a copy (`_atomic`) through `_Build`: vertices
  reused by coordinate (tolerance 1e-9 of the extent) or named
  `ID_k`/next `vN`, blocks ordered and checked strictly convex
  (`_validate_convex_ccw`, refused by name) or reused when the same four
  vertices already form one, counts merged as `add_block_from_vertices`
  does, arc/spline geometry on new edges only, one `validate()`. A new
  vertex closer to another vertex than `NEAR_FRACTION` (1.5e-5) of the
  model's extent but outside the reuse tolerance is named in the note
  (`_Build.near_note`, `ConstructResult.near`; a note, not a refusal): a
  station meant to be that vertex, missed by a rounded fraction or a fill
  side read as open. The fraction lies between the closest pair an
  operator made on the 19 round-3 builds and the skill's examples (3.6e-5)
  and the defects reproduced (3.7e-7, a six-digit fraction on the U-bend;
  8.2e-6, a high-lift fill side beside a wall vertex), at about their
  geometric mean. `add_grid` skips cells by centre (skip boxes, inside `outside` curves,
  inside existing blocks), conforms to existing exterior vertices on kept
  cells' sides (fixed point, stations merged within tolerance) and refuses
  a grid vertex inside an existing straight exterior edge. `_band` builds
  spokes per chain vertex (mitre `1/cos` capped at `MITRE_LIMIT`; a fan of
  `ceil(turn/FAN_SECTOR)` kite blocks at a convex turn above `FAN_ANGLE`;
  open ends along the adjacent boundary edge when within 60 degrees of the
  normal; exact arc tangents) and rows by `growth`; `_band_to_box` snaps a
  box corner to the nearer of the two spokes straddling it; an open
  chain's end on the box's edge sends its spoke along that edge to the
  corner on the band's side (a vertex at a box corner, or an inner chain
  vertex on the edge, is refused). Band blocks
  are checked strictly convex in their construction order
  (`_ordered_convex`, `_Build.block(expect=...)`) before the corners are
  sorted, since sorting turned a folded block into a valid-looking one
  with other edges; a band that would fold is refused before any vertex is
  made, naming the edge, the concave corner whose mitre moves the offset
  end `height * tan(turn/2)` along it past its length, and the largest
  height that builds (bisected over the whole band, `_fold_message`).
  `add_band`'s note gives the front (outermost row, or the box ends of the
  spokes) in the wall chain's order with its two ends (`_front_note`): new
  vertex IDs follow creation order, which is not the chain's. An
  offset curved edge keeps only the interior points running monotonically
  along the chord between its offset ends, clear of both by a thousandth
  (`_monotone_points`, a longest increasing run): a mitre or a wall curving
  tighter than the offset put points behind an end, and the hooked spline
  made the next band fail as misoriented. Every spline an operator writes
  - `add_ring` wall edges, band fronts, `_CurvePath.edge_interior` (fill
  sides along a curve) - goes through `model.unhooked_points`, so none has
  a segment in `spline_hooks` (round 2: 49 of the high-lift build's 202
  splines hooked, a station falling just past a curve point). `_band_to_box` refuses a block
  that would fold before building, naming the spokes and the box corners
  they were snapped to. `fan=false` (`add_band`, `add_ring`) leaves every
  convex corner that would fan open, `open_at` points the chain vertex
  nearest each (refused at a chain end or a corner not convex on the
  band's side): two spokes, each normal to its own side, and no blocks
  between them (`_band`'s `open_corners`; the note names the corners,
  `_opened_note`). `between=A,B` selects the shortest unassigned exterior
  chain joining two vertices (`_select_chain`, `_exterior_path`).
  `add_rows` carries a chain (as `_model_chain` reads it) along a
  direction: each point - vertices and the interior points of curved
  edges - travels to the line `x = to_x`/`y = to_y` (its own distance,
  `reach`) or by `distance`; rows at `stations` (absolute: x, y or
  distance, each row on one line) or at the `rows`/`growth` fractions of
  each point's travel; with `conform` an existing exterior vertex on a
  point's path adds its station. `spread` widens the rows along the
  target line (normal to the direction with `distance`): a point's offset
  from the middle of the chain's extent grows linearly with its travel
  fraction to `spread` times on the far row (the skill's C-grid example:
  with the wake band at constant width the wall's clustering ran to the
  outlet in one `size` strip, aspect 6390). Refused before anything is
  built: a point not reaching the line, a station outside every point's
  travel, a block not strictly convex in construction order (a direction
  into the chain's blocks), a row vertex inside an existing straight
  exterior edge, a block over an existing block or vertex
  (`_refuse_overlap` with a row label). A station on the target line is
  the far row itself: ignored and named in the note. The note always names
  the thinnest column (`_thinnest_column`: its chain edge, that edge's
  length and travel), so a fan front heading a far-field column shows
  before `size` (round-3 high-lift: a 0.0006 front carried 15, 3593 cells
  above aspect 1000 after `size`). The map from chain to row is
  affine, so lines stay lines and polyLines polyLines; arcs (except with
  `distance` and no spread, a translation) and splines become splines
  through the carried points (`unhooked_points`), straight on the target
  line. `add_ring`
  places stations at curve corners (vertices turning more than `corner`,
  default `CORNER_ANGLE`), at `fractions`, at `points` (open and closed,
  and `add_stations`: the curve point - in the section, for an open ring
  or `between` - nearest each point, `_station_points`/
  `_nearest_parameter`; refused naming the candidates when another point
  more than a thousandth of the curve's size away is as near within a
  millionth: a point on a U-bend's axis, a circle's centre), at `normals` (the point where
  the outward normal has each angle: vertex normals bisect their segments
  and vary linearly between vertices, a corner sweeps between its two
  segments' normals in place; refused unless there is exactly one point,
  a straight stretch named; `_normal_station`), with `tips` (off by
  default) at rounded convex corners (`_tip_stations`: vertex turning
  spread over the half segments beside it, a run of vertices about which
  the wall turns convexly at least `TIP_TURN` within `TIP_WINDOW` of the
  perimeter either side, the station at the turning midpoint of the
  window about the run's peak; corners carry no turning there; a tip
  within `TIP_MERGE` of another station is that station) and by arc length
  between all of them, reporting the stations in its `note`. Tips are off
  because on 10:1, 4:1 and 5:1 ellipses they cost 0.4 to 1.1 degrees of
  sampled non-orthogonality at default counts and the blunt plate's
  `to_box` needed the normal stations anyway. `side=in` bands the inside
  of a closed curve. With `side=left|right` the curve is open
  (`_open_ring`: `_CurvePath(force_open=True)`, no closing segment) - or
  several curves given in order (`curve` is a list), joined where each
  starts at the previous one's last point (`_joined_curve`; one that does
  not is refused, saying when it ends there instead: its points run the
  other way), each joint a station whatever it turns -, the
  section `between` two fractions or points: stations at its ends, at
  vertices turning more than `corner`, at `fractions`, at single crossings
  of `xs`/`ys` (`_crossings`, as `add_stations`), `count` spread between
  them (none of these is needed: the ends and corners alone build); wall
  edges by `_CurvePath.edge_interior`; `normals`/`tips` are
  refused there. `start`/`end` (`add_ring` open, `add_band` on an open
  chain; `_Chain.end_spokes`) give an end spoke's direction, turned to the
  band's side, length `height / cos` to the normal, refused when parallel
  to the wall; without them an open ring's ends are normal. `_wall_edges`
  writes both kinds of ring's wall edges. Wall edges are splines
  through the curve's points (at most
  `MAX_SPLINE_POINTS`, none within a thousandth of a station). Curves
  without a repeated end point are closed implicitly; `set_boundary_by
  curves=` agrees: an edge along the closing segment is taken when its
  block lies outside the closed curve (the curve bounds a body: a blunt
  base whose corners are not repeated), not when inside (an open wall whose
  chord bounds the domain, a cavity lid). `add_stations` places standalone
  vertices on a `_CurvePath` (the polyline `add_ring` stations lie on):
  `count` spread by arc length over the curve or a `between` section (two
  fractions, or two points taken to the curve; forward round a closed
  curve), arc-length `fractions`, single crossings of `xs`/`ys`
  (refused unless exactly one in the section, the refusal pointing at
  `points=`) and the nearest points to `points`; ordered along the section,
  station k named `ID_k` (`_Build.vertex(name=...)`; a reused vertex keeps
  its ID and leaves its k unused), `offset` adding a translation so a
  periodic partner chain gets the same stations node for node. A spline
  edge along a curve keeps at most `MAX_SPLINE_POINTS` of its points picked
  symmetrically from both ends (`_symmetric_picks`), so the two chains of a
  periodic pair, cut in opposite directions, keep the same points and
  export as a node-for-node cyclic pair. `add_fill` fills a four-sided
  region (corners in order round it, side k from corner k to k+1): a side
  is its given vertex list, else a chain of exterior edges joining its
  corners (`_exterior_walks`: through vertices with two exterior edges,
  never through another corner), else open - straight or along `curveK`
  (`_CurvePath`: the curve's polyline, closed when its end point repeats
  or its closing chord is no longer than its longest segment; both ways
  round a closed one are candidates) - through the standalone vertices
  within a thousandth of its length (or of the curve's size) or, with
  none, new stations at the opposite side's arc-length fractions.
  `_choose_sides` takes the candidates bounding a region with every
  existing side edge's block outside it and no existing vertex inside,
  most existing sides first, then the counter-clockwise reading (a half
  annulus), else refuses as ambiguous; with no reading left it refuses
  naming, per rejected reading, the existing vertices inside or the side
  edges whose block lies inside (at most `MAX_FILL_REASONS`). The note
  names the standalone vertices each open side took, and adds a NOTE when
  a block it built folds on the count-free map (`sampling.FOLD_CELLS`
  fractions a side; `_uniform_folds`) - a note, not a refusal. Opposite sides must have as many
  edges (`_mismatch_message` names the short side's segments facing the
  long side's extra stations: `split_edge` or a station). Interior
  vertices are blockMesh's edge-weighted transfinite interpolation
  (`preview._block_mesh_point`) of the side stations at their arc-length
  fractions; every block is checked convex before anything is built and
  refused with its would-be ID, column, row and corners; a block over an
  existing block or vertex is refused (`_refuse_overlap`). New side edges
  between two points on the side's curve are splines through its points
  (`_CurvePath.edge_interior`, as `add_ring`'s wall edges). Three or five
  corners (`_build_polygon`): that many four-sided fills (`_build_quad`)
  round a centre vertex at the mean of the side split stations; side k is
  split `σ_(k-1)` edges from its start, the spoke counts σ solving
  `s_k = σ_(k-1) + σ_(k+1)` exactly (`_spoke_counts`, fractions), refused
  unless every σ is a whole number of at least 1 (an even edge total, and
  for a triangle each side shorter than the other two together); each
  spoke is made by the first fill that needs it, with stations facing its
  opposite side, and reused reversed by the next. No engine code.
- `blockdrawer/size_distance.py`: item 3's sizing (commit 9a84f29),
  unchanged, behind `size method=distance`: the scalar wall-distance field,
  wall-normal edges graded from their finer end, tangential edges uniform,
  optional interface matching (unmatched by default from the command), no
  budget spending. `size._size_by_distance` runs it on a copy, applies the
  same skewness refusal and reports `verification` with field == result.
  Kept because it is the only path to the fine 30P30N's 0 faces above 70
  (452,791 cells, 68.83 degrees after the polish); do not develop it.
- `blockdrawer/render.py`: headless SVG/PNG rendering through one layout routine
  and two painters. Labels (vertex IDs, block IDs, cell counts, patch legend)
  are on by default; bounds, zoom-to-entity, and highlights select a region.
- `blockdrawer/checkmesh.py`: writes a minimal case, runs `blockMesh` and
  `checkMesh` through the `BLOCKMESH_COMMAND` prefix convention, and parses the
  logs into counts, geometry statistics, `***` problem lines, single-star
  `warnings` (the severely non-orthogonal face count is also parsed into
  `geometry["severe_non_orthogonal_faces"]`; it never changes `ok`), and FOAM
  FATAL errors. A `checkMesh` prefix is derived by substituting the word `blockMesh`
  unless `CHECKMESH_COMMAND` or an explicit prefix overrides it. The case's
  `controlDict` asks for ASCII, and while the case exists the sets the log
  says checkMesh wrote (`<<Writing ... to set NAME`) are mapped to blocks by
  `blockdrawer/polymesh.py`; a mapping failure is `set_error`, never a
  verdict change.
- `blockdrawer/polymesh.py`: standard-library reader of ASCII polyMesh lists
  (`points`, `faces` or `faceCompactList`, `owner`, `neighbour`, sets; `.gz`
  accepted; binary refused with a message) that keeps big lists as text lines
  and parses entries on demand. `locate_sets()` maps faces to their owner
  cell's block and cells to theirs by cumulative block cell counts in model
  order (blockMesh's numbering), points through a face using them, and gives
  per set the blocks worst first with count, centroid and x-y bounds; for
  `nonOrthoFaces` it recomputes each face's non-orthogonality with
  OpenFOAM's face-centre and pyramid cell-centre formulas. A case whose cell
  count differs from the session's is refused.
- `blockdrawer/cli.py`: argparse front end over the modules above. Every
  subcommand supports `--json`; failures exit 1 with `error: ...` on stderr or
  a JSON object carrying an `error` key.
- `blockdrawer/mcp_server.py`: Model Context Protocol server (`blockdrawer-mcp`,
  stdio by default) exposing the same operations as typed tools:
  `describe_session`, `list_commands`, `edit_session`, `quality_report`,
  `render_session` (returns the PNG inline), `validate_session`,
  `export_block_mesh_dict`, `check_mesh`, and `new_session`. Read tools are
  compact by default because an agent pays per byte: `describe_session` and
  `quality_report` return text (`as_text=false` for full JSON), an
  unfiltered description longer than `COMPACT_DESCRIPTION_CHARACTERS` is cut
  to summary and patches with a note on narrowing it, text quality reports
  list `DEFAULT_WORST` blocks and edges and the 20 worst warnings, and
  `edit_session` returns `batch_digest()` unless `detail=true`. Tools are plain,
  path-based functions so the server is stateless and testable without `mcp`;
  only `build_server()` imports the optional `mcp>=2` package and translates
  BlockDrawer errors into `ToolError` so agents see the real message. The
  repository's `.mcp.json` registers the server for Claude Code.
- `blockdrawer/web/`: the browser editor. `session.py` holds what the Tk app
  keeps outside its window: model, `ModelHistory`, session path, preferences,
  recent files, export, a version counter, and a lock. Its `snapshot()` sends
  sampled edge paths, every exact node position with graded node fractions,
  control points, grading, curves, and links, so the client never evaluates
  curves itself; the only geometry it computes is the transfinite preview
  interpolation from those nodes. `server.py` is a `ThreadingHTTPServer`
  with a JSON API, static files, and a server-sent-events stream that reports
  version changes and edits made to the session file by other tools; every
  `/api` request needs the per-launch token embedded in the page.
  `static/app.js` owns viewport, selection, and modes and ports `editing.py`,
  `panels.py`, and `canvas.py` one for one, including status texts; drags send
  `drag` requests without history and one `drag_end` that records. Registry
  commands are the only mutation path besides drag, undo/redo, open/save, and
  export, so every browser edit is also available to the CLI and MCP server.
  Performance rules: the snapshot computes occurrences, constraint components
  (union-find), and grading once per edge and never calls `edge_length` per
  link endpoint; the dirty flag comes from `ModelHistory.is_at_saved_index()`
  plus the drag flag, not from re-serializing the model. The server never
  builds the mesh preview for the browser: every edge carries all node
  positions and, for graded edges, canonical node fractions, and
  `static/app.js` (`buildPreview`) ports `preview.py`'s edge-weighted
  transfinite interpolation and coarsening sampling; keep the two
  implementations identical (the Playwright check compares them to 1e-6).
  The client caches the canvas size, keeps the preview as a bitmap layer that
  pans and zooms by translated or scaled blits and re-strokes only after the
  view rests for 120 ms, draws the layer as one culled path with sub-pixel
  decimation, batches node markers into one `Path2D` per color, and scales
  markers per entity from the shortest incident on-screen edge
  (`computeDetail`) so zoomed-out topologies stay legible; node markers shrink
  with spacing and disappear below 2.5 px.
- `blockdrawer/config.py`: versioned, human-editable application preferences,
  independent of Tk. It resolves the native per-platform location, validates UI
  scale and shortcut names, merges missing keys from platform defaults, and
  converts readable shortcut notation to Tk event sequences.
- `blockdrawer/foam.py`: deterministic `blockMeshDict` serialization. Each 2D
  vertex is emitted at `zMin` and `zMax`; a block becomes one `hex`, with the
  editable edge counts and the global z cell count. It also generates one
  correctly oriented zMin/zMax boundary face per block. Cyclic pairs go
  through `blockdrawer/cyclic.py`: the second patch's faces are listed in the
  order of their partners, and a block with a coupled side is written from
  the start corner (`_block_rotations`, a mod-4 difference system over the
  blocks) that makes every coupled pair local sides 0/2 or 1/3 running the
  same way, since blockMesh writes a side's cell faces in the block's local
  order (along then through z on sides 0/2, through z then along on 1/3).
  With one z cell only the along-edge direction must agree, the fallback
  when the strict system has no solution. Only the exported hex start
  changes; the model's block order is untouched.
- `blockdrawer/cyclic.py`: the face-by-face correspondence of each reciprocal
  cyclic pair from geometry alone: `match_cyclic_pair` finds the rigid motion
  (a translation first, else a rotation) mapping every edge of the first
  patch onto one of the second, each image running against its partner's
  block traversal as a coupled face must; `check_cyclic_nodes` compares
  every mesh node of each edge with its partner's at OpenFOAM's
  `matchTolerance` (1e-4 of the face size), so counts, grading and curves
  are one check. Both refuse naming the edges. `CyclicMatch.aligned` says
  whether the two canonical edge directions are the same physical
  direction, for code that matches partner gradings.
- `blockdrawer/history.py`: bounded, complete-model snapshots for atomic undo/redo.
  Snapshotting is appropriate because one topology operation can update multiple
  constrained edges. A mouse drag is recorded once, on release.
- `blockdrawer/app.py`: application startup, window/menu construction, session
  commands, history, and shortcut dispatch. The public `BlockDrawerApp` composes
  the following focused Tk mixins while retaining its existing API.
- `blockdrawer/panels.py`: contextual properties-sidebar construction.
- `blockdrawer/editing.py`: boundary, geometry, projection, split/combine, block,
  and vertex editing commands.
- `blockdrawer/canvas.py`: rendering, hit-testing, coordinate transforms, zoom,
  pan, selection, and pointer dragging. One immutable canvas transform captures
  dimensions and viewport state for a redraw; sampled points must reuse it rather
  than crossing into Tcl/Tk for `winfo_width()`/`winfo_height()` per point. Wheel,
  pan-drag, and resize events update viewport state immediately but share one
  redraw timer at a 16 ms interval. Synchronous editing redraws cancel any pending
  viewport timer so a delayed duplicate cannot overwrite newer interaction state.
  Dense edge-node coordinates are evaluated in one batch per edge and rasterized
  from every exact node instead of creating one Tk item per node.
- `blockdrawer/node_raster.py`: pure screen-space accumulation and
  standard-library indexed PNG encoding for dense exact edge-node overlays.
  Coincident nodes increase opacity so the fallback communicates density rather
  than looking like sampled individual nodes.
- `blockdrawer/render_cache.py`: one world-space sampled path, length, and
  bounding box per current topological edge and reference curve. Cache
  signatures contain only defining coordinates/geometry and sampling resolution,
  so selection, naming, boundary, and marker-visibility changes remain hits.
  Entries are replaced when defining points move and pruned when their entity
  disappears.
- `blockdrawer/ui_helpers.py`: shared UI constants and pure parsing/scaling/picking
  helpers. Keep these display-independent enough for headless unit tests.
- `tests/`: model tests are separated from conformal split/combine tests;
  persistence, export, projection, and UI helpers have focused modules. Tests must
  not require a display. CLI and OpenFOAM-runner tests use stub executables and
  captured log text; only `tests/test_openfoam_integration.py` needs
  `BLOCKMESH_COMMAND`, and PNG tests skip without Pillow. `tests/test_web.py`
  drives `WebSession` directly and the HTTP API through `http.client` on an
  ephemeral port; the JavaScript client has no unit tests, so exercise changes
  to it in a real browser (Playwright against `python -m blockdrawer.web
  --no-browser --port 0` works well) and keep `window.__blockdrawer` exposed
  for that purpose.
- `experiments/`: research prototypes outside the runtime package and outside
  `tests/`. `experiments/agentic_topology/` turns an explicit planar fluid
  domain into a conformal quadrilateral block topology with explicit near-wall
  bands and emits an ordinary session. It carries two producers over one
  intermediate representation: an exact generalized medial graph plus wall bands
  for external flow around any number of disjoint bodies, and a sweep/submapping
  core for simply connected internal flow with named wall, inlet, outlet,
  symmetry or translational-periodic chains. Cell counts come from a size metric
  through BlockDrawer's own opposite-edge equality components, and quality is
  measured on every sampled cell of the transfinite grid by reusing
  `blockdrawer.preview`'s interpolation - block-corner measures are only early
  filters. A sharp wall feature owns a *cavity* whose replacement is built on a
  copy of the graph and validated against the whole embedding - coverage,
  convexity, planarity, Euler and index balance and the cell-count components -
  before anything is committed, so a rejected alternative leaves the topology
  untouched and carries its exact reason. Acceptance separates a valid
  topology, an untangled grid, the shape limits measured on a count-free
  uniform sampling of every block, and structural sizing feasibility from the
  equality components; the sizing limits of the mesh the default counts define
  are reported but do not decide `resolved`, because counts and grading are a
  later stage. The report carries the limits it was judged against. `graph.sizing_structure`
  in the report gives the count-independent consequences of the opposite-edge
  equality components before any cell count exists. Generic construction-role
  mixing remains informational in `tangential_normal_couplings` and `coupled`;
  acceptance uses `wall_tangential_normal_couplings` and `wall_coupled`, with
  physical wall/front and band-normal edge witnesses. Producers carry the
  domain's boundary roles on `PatchGraph`; copied graphs preserve them. Front
  chains inherit wall provenance through layer spokes, so a contained fan
  cannot evade the check by disconnecting front counts from wall counts.
  Cavity screening uses the same physical criterion. The component length-ratio
  limit still applies to every component, including core-to-farfield ones.
  The outer boundary is explicit: fabricated as a circle, a circle cut into
  cap, legs and an outlet arc (`--farfield-outlet DEGREES`, about the wake
  direction, so the C-grid producer applies to a circular domain), a C-shape
  or a rectangle whose sides carry their own names and roles, or supplied
  chain by chain with
  `--outer NAME:ROLE=PATH`; chain breaks and convex corners of the outer loop
  are gates, every chain is its own patch, and an outer wall chain is reported
  as not banded. `wake.py` exposes an opt-in `--wake` construction: a straight
  wake separatrix from each sharp trailing edge through the body's ring to the
  outer boundary - or, when the ring crossing lies on the branch between two
  bodies, to the other body's band, where the stagnation gate is pinned and
  the two wake fronts land a band width to either side as new band spokes,
  the far leg aimed at that band when the wake direction would graze it
  (`wake._aim_landing`, `wake.LANDING_INCIDENCE`) -
  with the band continued as a two-sided wake band and the far-field core cut
  open along it - turning, when the wake direction is more than ten degrees
  off the edge's bisector (a base's flank bisector), at stations of at most
  `WakeOptions.turn` degrees (`--wake-turn`, default 50): each station is
  a rung across the band carried round the cell as a level between band
  front and ring (`wake.plan_turns`, `wake.line_path`, `wake.far_path`,
  `wake._turn_levels`), so every patch stays a quad - inside the ring when
  the stations fit and the ring's chord beside the crossing is not a
  sliver (`wake._chord_angle`), a level row crossing another sharp
  corner's seam wedge along its bisector (`wake._write_wedge_rows`), else
  beyond the ring in the outer layer, else straight along
  the bisector to the body it ends on or through the hull for the far
  field to turn (`turn_placement`); a wake over another
  body keeps its straight chord - or, for a blunt base (two convex corners within two percent
  of the perimeter), one record with two fixed anchors and three strips: the
  base itself going on as the wake's core strip between the flank bands - or,
  when the ray crosses into another body's cell and reaches that cell's far
  ring before any wall (a main element's wake over its flap, `through` in
  the record), through that cell: two fixed anchors where the wake enters
  and leaves it, a straight chord with a wake strip on each side, three
  spokes from the body-side strip down to the passed body's band (whose
  band is rebuilt with the wake band as an obstacle), and the lens between
  the lens-side front and the ring arc the chord cuts off written as three
  quads around a centre vertex, since a lens is a curved triangle no spoke
  can end in quads; the passed body carries exactly three gates under the
  wake, so a wall that turns too much there, a fixed gate on the lens arc or
  a second lens corner refuses the wake; wakes are tried in passes so an
  upstream wake can follow a downstream one; a trial first repairs the other
  sharp features' seams with the cavity stage's seam template alone
  (`pipeline._repair_seams_before_wakes`), since a level row through a seam
  wedge leaves the cavity stage nothing to rewrite there afterwards, and
  keeps the layout's relaxed gate stations through every rebuild of the
  cuts (`wake._rebuild_cuts`); trials
  are committed only as complete admissible results and refusals keep their
  reasons under `medial.wakes`. Feature anchors take
  the closest ring point unless the outward ray's spoke is squarer by fifteen
  degrees (corners always take the ray), and gates are cut until
  neighbouring spoke lengths are within a factor of four; both rules moved
  the default corpus and are documented as a re-baseline in the README.
  `cgrid.py` is the single-body C-grid producer: with `--wake` and a C-shaped
  far field it takes the body's gates from a layout of its own (band blocks
  without sliver corners at the band height, at most 45 degrees of wall
  turning per block, the anchor floor judged by the wall gap, a folding band
  block split and retried), builds the level sets of the body and its wake
  line at geometrically growing heights, maps every level above the band by
  arc-length fraction, and joins the outermost level to the cap; it replaces
  the annular result only as a complete admissible result, and the annular
  construction keeps the layout its own options build. Its acceptance case is the
  A-airfoil at fifteen chords (`make research-a-airfoil`, `test_a_airfoil.py`).
  A tight concave bend - concave vertices of local radius under three
  percent of the perimeter turning through a reflex corner's worth together -
  is a rounded reflex corner: anchored and mitred at its turning midpoint
  (`layers.tight_concave_runs`, `effective_fluid_angles`), and the wall in
  a reflex corner's mitre shadow - rounded or sharp, a sharp corner's
  clearance capped by the notch beyond its two walls - carries the
  corner's far clearance and is held at the corner's band height
  (`layers.hold_corner_shadows`), since a band thickening past the mitre
  leaves it along the bisector over the gate's core spoke. A
  tight convex bend - a curvature peak where the wall turns a corner's worth
  within a hundredth of the perimeter on each side, an ellipse tip or an
  airfoil nose - is a rounded convex corner, a forced and pinned gate like a
  sharp corner's (`block_layout.TIP_WINDOW`, anchor kind `tip`). A *cove* -
  a wall band patch turning more than the limit the concave way whose every
  split crowded the short ring piece across its mouth, since the medial axis
  inside a body's own cove is a self-branch the diagram lacks - is cut by
  anchors pinned on the cove wall at equal fractions of its turning and
  spread along that ring piece, fixed like a wake's and outside the ring
  floor, each spoke kept clear of the wall (`block_layout.cove_anchors`,
  anchor kind `cove`); they are cross-slot lines to the body facing the
  cove, as a channel array would have them.
  `hull.py` is the opt-in `--hull` far field for two or more bodies: the
  hull is the cluster's wall-distance level set at six band heights (capped
  by the body frame), carrying the outer boundary's chains at the same
  fractions; the near field is the annular construction with the hull as its
  outer boundary and the C-grid's band settings; beyond it the cluster's
  level sets at growing heights carry the hull's vertices outward - by
  arc-length fraction between the kinks where the nearest body changes, so
  no vertex crosses the waist between two bodies - and join the outer
  boundary by spokes - cut open along one exit wake's band (a sharp edge's
  two halves or a blunt base's three strips) to the outlet, C-grid style,
  or closed and mapped chain onto chain. Two or more exiting wakes merge
  into one band - the levels cut between the uppermost band's out edge and
  the lowermost's in edge, the hull between two bands continued to the
  outlet as channel strips (`medial.hull.merged_wakes`) - and when that
  does not build the pipeline tries each as the cut, the one over another
  body first, with the other wake's band ending at the hull and its exits
  carried outward as level spokes (`hull.extend(exit_site=...)`,
  `medial.hull.ending_wakes`), and only then with the other body's wake
  excluded (`WakeOptions.exclude`, `medial.hull.dropped_wakes`). In both the C-grid and the hull
  far field every wake line beside the axis leaves its mitre point at the
  `--wake-spread` slope (`CGridOptions.wake_spread`,
  `HullOptions.wake_spread`, `wake.spread_direction`; in the hull far field
  the lines of the merged band leaving the hull take slopes spaced evenly
  by rank from minus to plus the spread, channel lines included), so a wake band that
  is thin at the body widens towards the outlet instead of carrying its
  first-cell count for the length of the far field; the sizing stage grades
  the wake strips' streamwise edges and the channel lines from the end
  nearer the wall, `--wall-aspect RATIO` (`SizingOptions.wall_aspect`)
  floors the size metric along tangential edges at that many first widths,
  and `--count-rule max` (`SizingOptions.count_rule`) lets the finest demand
  in an equality component decide its count instead of the length-weighted
  mean; `--interface-growth RATIO` (`SizingOptions.interface_growth`,
  `sizing.propagate_counts_and_grading`) then grades the wall-normal edges
  finest first so cell widths match across block interfaces, one geometric
  series per edge with its ratio capped by raising the component's count,
  and reports the interfaces one series cannot match. The near field is built with the hull as
  one chain first and with the outer boundary's chains mirrored onto it only
  when no wake leaves; a hull's chain breaks are anchored by the inward ray
  (`LayoutOptions.outer_by_ray`). The whole
  graph is validated with the real domain; refusals keep their reason under
  `medial.hull` and the annular construction runs instead.
  `--smooth` (opt-in, `PipelineOptions.smooth`) runs the production
  `blockdrawer.smooth` twice in `pipeline._finish`: `mode=shape` on a
  placeholder-count emission of the graph before `sizing.assign_counts`
  (`_shape_smooth` writes the moved vertices and similarity-carried paths
  back into the graph, rechecks crossings, boundary chains and coverage,
  and restores the graph on any new problem), then the real-count polish
  on the built session before the grids are judged, so the verdict is the
  one of the session written; records `shape_smoothing` and `smoothing`.
  It changes no engine rule and is off by default; its corpus measurement
  is in the engine README.
  `spanned.py` exposes an opt-in `--span-gaps` construction: mutually facing
  mouths replace a narrow body-body medial branch with one core strip and
  dissolve its two junctions into valence-five front vertices. Trials include
  gate placement and band rebuilding on an isolated layout, and commit only
  after graph, coverage, exported session and both sampled grids pass, even
  when ordinary grid evaluation is disabled. Rejections restore the whole
  result and retain their reasons under `medial.spanned_branches`. It stays
  disabled by default: mouths cannot yet cross existing sharp gates or handle
  several narrow branches at one junction. Its `core_rung` to far-field count
  connection is not itself a wall-band direction coupling; the physical
  criterion and unchanged length-ratio limit resolve the spanned circles.
  A band front section that ends at a seam point is trimmed of the hook
  the offset of a flank curving into a sharp corner leaves there
  (`layers.trim_backtrack`); a seam point is offset along the normal of
  the wall chord past any tiny base at the feature and by the arriving
  front's height when that is less than the feature's own
  (`layers._seam_points`, `SEAM_CHORD_FRACTION`), so the front does not jog
  at the seam; the band's offset can use the same windowed
  normals as its level-set trimming (`LayerOptions.normal_window`,
  `--normal-window HEIGHTS`, off by default).
  Front repair reduces the caps of whole failing blocks; repeated failures
  request a patch split, with bounded global height search permitted on the
  caller's final repair round. That search is not a proof of monotone
  admissibility or numerical stability. Verdicts fail closed: with a grid
  not evaluated (`--no-grid-quality`) every grid-dependent term is false and
  no session is written, a fold check at 32 cells per block side guards
  `untangled`, `sizing_feasible` judges every equality component (the
  report adds the verdict without the fan-out and wake-landing exemptions),
  `topology_valid` requires every named boundary edge on its own chain and
  every chain covered (`patch_graph.boundary_problems`), no self-crossing
  edge path, and an exportable `blockMeshDict`; an opt-in stage never
  replaces a result by one with a lower verdict, and an exception inside a
  trial that is not a construction refusal is reported as a crash. Exported
  sessions use `empty` z patches, and an exported spline that leaves its
  validated polyline is written as that polyline. Cell counts integrate the
  size metric exactly per segment and refuse backwards first-cell requests.
  The layout measures the body frame on closed loops, takes curvature from
  the smoothed vertex turning, splits by a turning density, merges
  near-coincident forced anchors, keeps the wall-turning limit through the
  relaxation, clamps an unpinned gate only when the gates would otherwise
  wind twice round a wall, stops splitting after five splits that do not
  raise the worst block, and refines the raster until every junction
  converges, so all 13 fixtures keep their block graph under rotation,
  reflection, reversal, start point, scaling, translation, densification
  and raster width (`EquivalentInputTests` guards five of them); the
  geometry of that graph, and on `concave_and_convex` turned through 180
  degrees the verdict, still follow the orientation. Four held-out coarse
  or thin bodies (`HeldOutEquivalentInputTests`) found what the fixtures
  hid: curvature is now smoothed at least over the vertex spacing with peaks
  located on the curvature itself, the split midpoint comes from a turning
  density on the turning vertices (collinear vertices carry none, a sharp
  corner is a point mass), the solver reads a gate
  past the wall's origin inside its window, a closed ring is traced from its
  least clearance, and a cell is seeded only while it lacks cuts. The
  24-gon and the 40-point ellipse keep their graph under every input; the
  10:1 ellipse densified and the coarse NACA reversed reach the solver
  equal to 1e-12 and leave it in different local optima, so those two stay
  `expectedFailure`. The opt-in trials always sample their own grids, so a
  trial that commits writes its session even under `--no-grid-quality`. A
  regression fixture that reproduces a known failure carries an
  `expectedFailure` test while it fails, so a fix cannot land unnoticed. It may use NumPy and Pillow and keeps its own self-tests beside it.
  It imports BlockDrawer but nothing in `blockdrawer/` may import it, and none
  of its numerical code belongs in the package until it earns its way in. Its
  experimental agent operations stay in `research_cli.py`; do not widen the
  production CLI or MCP surface with them yet.

## Headless and agent workflow

- An edge is always written `first-second` in canonical (sorted) vertex-ID
  order, and accepted in either order. A value given *along* an edge - a
  grading (`start_width` is at the first vertex as written, a ratio runs
  first to second) or a split fraction - follows the order the command was
  written in (`Parameter` kind `directed_edge`); the registry converts to the
  canonical order the model stores. Because vertex IDs may themselves contain
  hyphens, text parsing tries every split against known vertex IDs and asks for
  the JSON list form when that is ambiguous. Blocks, vertices, patches, and
  curves are referenced by ID; curves also accept a unique name.
- `new --box XMIN YMIN XMAX YMAX` (the `new_session` tool's `box`) places
  the first block, since a topology always has one; a hand layout starts from
  that block rather than moving the unit square's corners.
- `apply` is atomic: commands run in order against a copy of the loaded
  session, and nothing is written when any step fails. The error names the
  failing step and repeats the offending command. Without `-o` or `--in-place`
  the result is reported but not saved. `--quiet` (the MCP default) reports
  the batch digest instead of every command's result. Without it each
  command's one-line result (nested values elided) is followed by its
  digest note, when it has one, on an indented `note:` line
  (`format_results`), so the long form shows at least what the quiet one
  does; the MCP `detail=true` log uses the same lines.
- Describe and quality JSON is meant to be diffed between edits. Keep field
  names stable, keep floats unrounded, and add fields rather than renaming them.
  Text output rounds to six significant digits. JSON warning lists keep
  model order unless a cap (`max_warnings`) is asked for, which lists them
  worst first and counts the rest in `warnings_omitted`.
- Quality thresholds are screening heuristics, not OpenFOAM's checks. Corner
  angles are measured between the first mesh cell's edges at each block corner
  so curved edges and grading count; interface ratios compare the transverse
  first-cell widths of the two blocks sharing an internal edge.
- Rendering sizes the canvas by the shown region's aspect ratio when no height
  is given (`render.resolved_size`: between a quarter and twice the width,
  padding and legend band added), reserves a band for the legend so it never covers geometry, culls
  entities outside the requested bounds, offsets cell-count labels outward from
  the first incident block, decimates control-point labels above 250 points,
  and hides node ticks above the canvas's individual-marker limit (200 cells). Highlight fills
  follow sampled curved edges. Do not add rendering paths that depend on Tk.
- `check` exports to a temporary or named case with a minimal `controlDict`,
  `fvSchemes`, and `fvSolution`, then runs `blockMesh` and `checkMesh`. The
  result is ok only when blockMesh succeeds and checkMesh prints `Mesh OK.`;
  every `***` line is surfaced as a problem and every single-star line as a
  warning; `Mesh OK` with 596 faces above 70 degrees is still ok, so the
  severe count is printed on its own line. The face, cell and point sets
  checkMesh wrote are mapped to blocks before a temporary case is deleted
  (`--worst N` blocks per set, `--no-sets`). Logs are always kept in the case
  as `log.blockMesh` and `log.checkMesh` when the case is kept.
  On Windows, the configured Linux-tool wrapper owns any host-to-guest case-path
  translation or mount and must still accept the appended `-case PATH` pair.

## Topology invariants

- Reference geometry consists of named 2D curves with stable IDs and at least
  two ordered finite points. Adjacent points must be distinct. Curves use smooth
  through-point Catmull-Rom interpolation with chord-length segment selection;
  they are saved in sessions and participate in undo/redo, but are never written
  to `blockMeshDict`. Each curve persists its own `show_points` state. New manual
  curves default to visible point markers, while imported or file-replaced point
  lists default to hidden markers. Rendering samples every curve span so dense
  imported geometry is not reduced to a fixed sample count. The point-file format
  is one `x y` or `x, y` pair per line, with blank lines and `#` comments allowed.
  The configured deletion shortcut (which includes `X` by default) removes the
  complete selected reference curve instead of a mesh edge when reference
  geometry has focus. Individual points are removed explicitly from Properties;
  the two-point minimum remains.

- Projection is a staged GUI mode entered with configurable shortcut `P`: first
  select one or more vertices or one or more topological edges (never both), then
  select one or more reference curves, choose a direction, and apply. `Esc`
  cancels either stage without mutation. x/y projection moves parallel to the
  named axis and chooses the nearest curve intersection; orthogonal projection
  chooses the globally nearest point on the smooth Catmull-Rom curves. There is
  no z projection mode because mesh vertices and reference curves store only x/y.
- Vertex projection moves only the selected vertices. Edge projection moves both
  endpoint vertices; it also projects every stored interpolation point for arc,
  polyLine, and spline edges. A projected arc remains an arc when its endpoints
  and interpolation point define a circle. If those three points become
  collinear, it converts to a through-point spline so the projected point is not
  discarded. Projection computes every new point before mutating the topology,
  validates once at the end, rolls back all coordinates and edge geometry on any
  failure, and records a successful operation as one history action.
- Projection's edge-only `fit` option replaces every selected edge geometry with
  a spline over the reference-curve section bounded by its two projected
  endpoints. The endpoints must resolve to the same reference curve. Open curves
  have one section; for closed curves, compare both sections to samples of the
  original edge and reject an equal-score ambiguity. The fitter first tries the
  reference section's native knots, then adds candidate through-points one at a
  time using a cheap maximum-distance proxy; bounded checkpoints measure the
  symmetric geometric deviation in both directions by sampling the curves and
  solving point-to-cubic closest locations. A uniformly spaced candidate guards
  against Catmull-Rom degradation from badly unbalanced point spacing. The GUI
  exposes a positive relative tolerance and a per-edge interpolation-point cap,
  defaulting to `1e-8` and 250. Absolute tolerance is
  `max(1e-12, relativeTolerance * sectionScale)`. Hitting the cap returns the best
  measured fit rather than rejecting the operation. Candidate paths whose
  adjacent points are within the model coordinate tolerance are discarded before
  ranking, preventing a numerically crowded fit from failing later spline
  validation. `ProjectionResult` reports the maximum measured distance, absolute
  target, and whether every fitted edge met its target. Fit calculation, endpoint
  movement, edge replacement, topology validation, and history recording remain
  one atomic operation. Canvas rendering decimates only the markers and labels of
  point lists above 250 entries, always retaining the selected marker; model,
  session, and export data stay complete.

- A block stores four distinct vertex IDs in counter-clockwise order and must
  remain strictly convex.
- Mesh preview is a transient per-block visualization, not a mesher. Each block
  samples the graded nodes of its four directed curved edges and applies the 2D
  specialization of OpenFOAM's edge-weighted transfinite interpolation. In
  particular, blending weights come from all four actual graded edge fractions,
  not uniform logical indices; this must remain consistent with `blockMesh` when
  opposite-edge grading differs. The configured positive coarsening factor keeps
  every nth subdivision index counted from *both* ends of the edge (so the set
  is the same whichever way a block runs the edge and the lines of
  neighbouring blocks meet on shared nodes) and always both endpoints. Only interior logical
  rows and columns are drawn; inter-block cell connectivity is deliberately
  irrelevant.
  The cache signature includes used block vertices, ordered blocks, edge cell
  counts, grading, and edge geometry, but excludes loose vertices, boundaries,
  reference geometry, and selection. The GUI retains one cached sampled grid so
  repeated entry is instant without retaining several potentially large meshes.
- Standalone vertices are valid when their IDs and finite coordinates are unique.
  In the GUI, `V` starts one-shot placement, clicking creates and selects the
  vertex as one history action, and `Esc` cancels. Standalone vertices remain
  draggable and available to `N` block selection.
- A topological edge is the canonical, unordered pair of its vertex IDs. It is a
  boundary edge when used by one block and internal when used by two.
- Named boundaries have an OpenFOAM type and a persistent unique canvas color.
  Only exterior 2D edges can be assigned, and each edge belongs to at most one
  boundary. `cyclic` definitions are reciprocal pairs selected with
  `neighbourPatch`; ordinary `cyclic` geometry is inferred by OpenFOAM, and
  export orders and orients the coupled faces by geometry (never by vertex
  names), refusing a pair that does not correspond face by face. Pairing
  two patches is atomic, while changing/removing one turns its former partner
  back into `patch`. Boundary type and cyclic-neighbour combobox selections apply
  immediately through one history action; there is no separate Apply button.
  Boundary mode (`B`) assigns or reassigns an edge to the active patch and
  toggles it off when clicked again. Internal edges are unavailable in this mode.
- Straight (`line`) geometry is implicit. An `arc` stores exactly one finite,
  non-collinear interpolation point. A `polyLine` or `spline` stores one or
  more ordered, finite interpolation points, and no adjacent path points may
  coincide. Geometry is shared by every block incident to the topological edge.
- `MeshModel.edge_point()` evaluates lines and arcs geometrically. For polyLines,
  its parameter is cumulative path length. Splines use OpenFOAM's
  through-point Catmull-Rom interpolation with chord-length segment parameters.
  Block interiors are intentionally not filled on the canvas: topology is drawn
  as edge outlines so curved edges are not contradicted by a straight-sided
  polygon fill and the background grid remains visible through every block.
  Canvas spline strokes sample every Catmull-Rom span and retain every stored
  interpolation point; never replace this with a fixed sample count over the
  complete edge because fitted splines may contain hundreds of spans.
- Each redraw derives one world-space viewport box with 40 display pixels of
  padding. Wholly outside edge paths, reference curves, preview polylines, and
  vertices are not submitted to Tk; individual edge/geometry labels, control
  points, subdivision nodes, spacing-link legs, and split markers are likewise
  skipped off-screen.
  Culling uses the cached display-path bounds, so it may admit conservative false
  positives but must never hide a visible sampled segment. This does not alter
  marker decimation or model/session/export data.
- Selecting any curved type creates a deterministic point offset outward from the
  first incident block. GUI points are purple; point-list types are numbered in
  their canonical edge-path order. Points can be selected, moved, inserted, and
  removed while retaining at least one. Reset preserves the current point
  count and distributes the points at equal fractions of the straight vertex-to-
  vertex chord. Each button/property mutation is one history action; a complete
  point drag is recorded once on mouse release.
- Point-list edges expose a positive interpolation-point count. Changing it
  replaces the existing list with that many points at equal fractions of the
  straight endpoint chord, matching Reset semantics, and records one history
  action.
- Opposite edges of every quadrilateral must have the same cell count. Calling
  `MeshModel.set_edge_cells()` updates the complete transitive constraint
  component, including shared edges in neighboring blocks.
- Grading is independent per topological edge and directional. The canonical
  `EdgeKey` order is the displayed start-to-end direction. The stored value is
  OpenFOAM's total end-cell/start-cell expansion ratio; export takes the reciprocal
  whenever a block-local edge traverses the shared edge in reverse. For `N` cells,
  the cell-to-cell ratio is `totalRatio**(1/(N-1))`. Start/end widths form the
  corresponding geometric series over the geometric edge length. Lines,
  polyLines, and arcs have exact lengths; spline length is sampled. Widths are in
  unscaled drawing-coordinate units. A one-cell edge is necessarily uniform.
- `MeshModel.set_edge_grading()` accepts cell-to-cell ratio, total ratio,
  start-cell width, or end-cell width. The UI exposes all four with per-field Set
  actions and recomputes the other representations. Canvas node markers use the
  graded fractions. Sparse markers remain individual Tk items; dense markers are
  one non-interactive transparent image per edge built from every exact node, with
  opacity accumulation for nodes sharing screen pixels. This is display-only and
  zooming can resolve individual locations without changing the model. A
  persistent Propagate checkbox sweeps the same transitive edge component as
  `set_edge_cells()`, reversing canonical ratios where needed so the grading
  follows one physical block direction. Each Set action is one history entry.
- A spacing link pairs exactly two distinct topological edges at their one common
  vertex. Each `(edge, vertex)` endpoint may occur in at most one link, while one
  edge may have separate links at both endpoints. Links are symmetric after
  creation, but creating one uses the first selected edge as the driver and
  regrades the second. The linked quantity is the physical grading width touching
  the shared vertex, not total or cell-to-cell expansion ratio.
- Given edge length `L`, cell count `N > 1`, and required endpoint width `w`,
  spacing synchronization solves the unique positive cell ratio satisfying
  `L = w * (1 + q + ... + q**(N-1))`; canonical grading is inverted when the
  linked vertex is the edge's canonical end. One-cell edges have fixed width `L`.
  Unattainable widths, incompatible fixed anchors, and inconsistent closed chains
  reject and roll back link creation, explicit synchronization, and cell-count
  edits. Diagnostics identify the source edge, follower edge, shared vertex,
  requested width, follower length, and follower cell count.
- Cell-count changes first update their normal opposite-edge constraint component;
  every directly affected edge is then a fixed anchor and endpoint-width
  synchronization walks all reachable spacing links. Linked follower cell counts
  never change, and any failure rolls back the complete cell-count edit. Grading
  changes first honor their existing Propagate choice and validate those direct
  grading changes. They then attempt the same link synchronization. If it is
  unattainable, restore every link-driven follower grading while retaining the
  direct grading change; the affected links remain out of sync and the direct
  change is one history action. Successful grading and link propagation also
  remain one history action.
- Persistent spacing links intentionally do not react to vertex, control-point,
  projection, or other geometry mutations. The GUI's explicit **Synchronize links
  from this edge** action re-establishes the linked widths using the selected edge
  as driver. Configurable shortcut `L` enters a focused link mode: two incident
  edge selections create a pair, `Esc`/`L` exits, and Properties exposes only cell
  count, grading, link removal, and synchronization—not edge type or interpolation
  controls. The normal edge Properties panel reports matched or out-of-sync
  endpoint links. Short teal endpoint legs visualize saved pairs.
- Splitting transfers a link on an affected edge to the sub-edge touching the
  original linked endpoint. Combining transfers surviving outer-end links to the
  merged edge and drops links at removed cut vertices; edge deletion prunes links
  whose endpoints disappear. These topology rewrites and their link changes roll
  back atomically.
- `MeshModel.split_edge()` cuts the complete opposite-edge constraint component,
  not only the incident block. Every touched block is replaced by two conformal
  quadrilaterals, shared affected edges receive one shared split vertex, and
  boundary assignments on exterior affected edges are copied to both segments.
  Reject a constraint component that touches all four sides of one block because
  that would branch or cross the cut. A one-cell component becomes two one-cell
  components; otherwise the selected fraction chooses the nearest existing mesh
  node and preserves the original total cell count.
- Edge splitting retains each affected edge's type. Arc halves use points on the
  original circle and polyLine halves retain the exact original path, inserting
  a path midpoint only when a half would otherwise have no interpolation point.
  Spline halves retain at least one point and are sampled from the original
  through-point curve. Sub-edge grading keeps the original cell-to-cell ratio;
  therefore a node-aligned split preserves selected-edge node locations up to
  floating-point error. Each new internal cut edge log-interpolates the directed
  total grading ratios of the block's transverse sides. The complete model
  mutation validates and rolls back atomically.
- In the GUI, configurable shortcut `S` enters split mode only when a mesh edge
  is selected. A purple marker starts at a central existing mesh node, follows
  the closest location on straight or curved edges while clicking/dragging, and
  remains movable after mouse release. Mouse interaction never commits the split;
  the editable `Current split (%)` field, configurable `Enter`/keypad-Enter execute
  action, and `Execute split` button are the only execution paths. `Esc` cancels
  without mutation. A successful strip split is one history action and selects
  the first new internal cut edge.
- `MeshModel.combine_blocks()` is the conformal inverse operation. It accepts only
  an internal edge with two incident blocks, follows the cut through four-block
  junctions, and rejects branches, turns, uncovered incident blocks, and merges
  that would not produce strictly convex quadrilaterals. Each block may occur in
  only one merged pair. Cut vertices and the two consecutive edge segments at
  each cut endpoint are pruned atomically.
- A combined edge sums the two source cell counts. Its total grading ratio is
  reconstructed from the directed first source start-cell width and second source
  end-cell width; this exactly recovers compatible split grading and gives a
  stable approximation otherwise. Collinear lines remain lines, kinked line and
  polyLine combinations become an exact polyLine, compatible same-circle arcs
  remain arcs, spline pairs concatenate their through-points, and mixed curve
  types are sampled into a spline. Boundary assignments must match exactly on the
  two joined segments. `Shift+S` invokes the operation immediately, records one
  history entry, and remains distinct from destructive edge deletion.
- A new block is appended only across a boundary edge. It shares that edge and
  translates both endpoints by the same outward-normal vector. The distance is the
  source block's average perpendicular thickness at the edge endpoints, producing
  a convex rectangular neighbor even for skewed source blocks. Reuse coincident
  existing vertices when possible. A genuinely new opposite edge inherits the
  source edge grading with corresponding endpoints and physical direction; an
  already-existing opposite edge keeps its own grading.
  GUI block addition redraws in the current viewport and must not call
  `fit_view()`; framing the expanded topology is an explicit user action.
  Any boundary assignment on the extruded source edge moves to the new opposite
  exterior edge when possible. Other topology edits prune assignments that have
  become internal or disappeared.
- `MeshModel.add_vertex()` and `split_edge()` take an optional vertex ID (a
  non-empty string without whitespace, not yet used); a split names the vertex
  on the selected edge and the others `ID_1`, `ID_2`, ... in `affected_edges`
  order, so a batch can refer to the halves before it is applied. Without one
  the next free `vN` is taken.
- `MeshModel.add_block_from_vertices()` accepts four distinct existing vertex IDs
  in any order, sorts them counter-clockwise, requires a strictly convex block,
  and rejects duplicate/non-manifold/misoriented topology. Existing edge cell
  counts take precedence and opposite-edge constraints propagate through the new
  block. The registry command also takes `points=x,y;x,y`, each resolved to
  the existing vertex within the construction operators' tolerance
  (`construct.resolve_points`; refused naming the nearest vertex and its
  distance), so a batch need not probe IDs; IDs and points make four.
- In the GUI, `N` starts four-vertex block selection and `Esc` cancels it. Staged
  vertices are numbered and clicking one again deselects it. Only successful
  completion enters history; it is one undoable action.
- Removing an edge removes every incident block (one for a boundary edge, two for
  an internal edge), prunes newly orphaned corners of those blocks, and preserves
  unrelated standalone vertices. Unused cell counts, curved-edge data, and
  grading are pruned. This is one undoable operation.
- A topology always contains at least one block. Reject any edge deletion whose
  complete incident-block set would leave zero blocks (including deleting the
  shared edge when exactly two blocks remain).
- Moving a shared vertex updates every incident block. Invalid/inverted moves and
  moves that invalidate attached curved-edge geometry are rejected and rolled
  back. `move_vertex` with `carry_curves` instead carries every incident curved
  edge with the vertex - its points follow the similarity about the edge's
  other end that maps the old chord onto the new one, so an arc stays an arc
  and a spline keeps its shape relative to its chord; the GUI drag keeps the
  default.
- The z direction is not drawn. `zCells` defaults to 1 and `zMin`/`zMax` default
  to 0/1. Extrusion and scale controls appear only in Export mode, entered with
  the configurable `E` shortcut; editing those fields does not mutate the model
  until a destination is chosen and export succeeds. The zMin/zMax patch names
  default to `zMin`/`zMax`, their types default to `patch`, and their names must
  be distinct from each other and from every selectable side-boundary name. If
  either extrusion patch is selected as `cyclic`, both are normalized to
  `cyclic`; export writes reciprocal `neighbourPatch` entries automatically.
- Session symmetrization treats `--axis x` as `y -> -y` and `--axis y` as
  `x -> -x`; the positive half is the default source and the negative half is an
  option. Source blocks may touch but never straddle the axis. The result retains
  source-side block and vertex IDs, mirrors source-side standalone vertices,
  rebuilds target blocks and vertices with deterministic `mirror_` IDs, and
  reverses mirrored block order to remain counter-clockwise. Cell counts,
  interpolation geometry, directional total grading, exterior boundary
  assignments, and structurally compatible spacing links mirror with their
  source edges. Boundary definitions, export settings, and independent reference
  curves remain unchanged. An axis edge becomes internal and loses any former
  exterior assignment; non-straight axis geometry must equal its own reflection.
  Reject a straddling block, conflicting mirrored metadata, or invalid result
  rather than guessing. The CLI writes a new `*-symmetric.json` path by default
  and requires `--overwrite` before replacing an existing file.

## Data and compatibility

Session files contain a format marker and integer version. Version 2 adds an
`edgeGeometry` array whose entries contain `vertices`, `type`, and an ordered
`points` array. Version 3 adds an `edgeGrading` array containing canonical vertex
pairs and non-uniform `expansionRatio` values. Version 4 adds ordered `boundaries`
and `edgeBoundaries` arrays. Version 5 adds `geometryCurves`, containing stable
IDs, names, ordered 2D point lists, and per-curve `showPoints` flags. A missing
`showPoints` field in an early version 5 file defaults to true. Version 6 adds
the names and types of the automatic `zMinPatch` and `zMaxPatch` under settings.
Version 7 adds `spacingLinks`, whose entries store the common `vertex` and two
canonical edge pairs. Version 6 and older sessions migrate with no spacing links.
Older sessions receive non-conflicting `zMin`/`zMax` patch names and `patch`
types. Version 1
straight-edge sessions migrate with empty geometry; versions 1 and 2 migrate with
uniform grading; versions 1–3 migrate with no named boundaries; versions 1–4
migrate with no reference geometry. Add
migrations (or a clear unsupported-version error) when the shape changes; never
silently reinterpret old data. JSON is a project/session format, not an OpenFOAM
format.

Application preferences are separate from mesh sessions. They use JSON format
`blockDrawerConfig`, version 4, at `~/.blockdrawer` on Linux and macOS, and
`%APPDATA%/BlockDrawer/config.json` on Windows. The file is created with complete
defaults on first launch. Version 4 adds `recentFiles`, an ordered array of at
most ten session paths. Successful opens and saves promote a normalized path,
selecting a missing recent file removes it, and versions 1–3 migrate with an
empty list. `ui.scale` is `auto` or a multiplier from 0.5 to 4;
`ui.showBlockMesh` and `ui.showGeometry` persist the independent canvas layers;
`ui.showVertexIds` and `ui.showEdgeCellCounts` independently persist the canvas
annotation labels and default to visible when missing from an older config;
`ui.showEdgeNodes` and `ui.showEdgeInterpolationPoints` independently persist
the mesh-subdivision and curved-edge control markers. `ui.showMeshPreview`
persists the preview overlay and `ui.previewCoarsening` is a positive integer;
versions 1 and 2 load with preview disabled and factor 1. All are saved when
changed through the View menu or preview panel. Geometry-layer visibility is the
configurable `toggle_geometry` action and defaults to `G`; preview visibility is
`toggle_mesh_preview` and defaults to `M`. Export mode is the configurable
`export_block_mesh_dict` action and defaults to `E`; version 1's untouched
`Ctrl+E`/`Cmd+E` default migrates to `E`, while custom bindings are preserved.
Spacing-link mode is the configurable `link_spacing` action and defaults to `L`.
`shortcuts` maps every application
action to a list of readable combinations such as `Ctrl+S`, `Cmd+Z`, `Delete`,
`B`, `G`, `P`, or `S`; an empty list disables that action. Missing actions inherit
current platform defaults so newer releases can add actions compatibly. Unknown
actions, invalid combinations, and duplicate combinations are rejected without
preventing the GUI from starting; the invalid file is left untouched and
defaults are used for that run.

Every 2D vertex, including a standalone one, is emitted at both z planes. Each
non-straight 2D edge is emitted twice: `arc` uses its single point and
`polyLine`/`spline` use ordered point lists, with matching x/y at `zMin` and
`zMax`. Shared edges are emitted only once per z plane. Straight edges remain
implicit. Every block uses the 12-value `edgeGrading` form: the four 2D edge
ratios are duplicated on the lower/upper z planes and all four z edges remain
uniform. Each assigned 2D exterior edge exports as its extruded four-vertex side
face under the named patch. Unassigned side faces remain in OpenFOAM's default
patch. Every block's lower and upper face is emitted into the configured automatic
zMin and zMax patches; cyclic z patches are reciprocal partners.

## Working conventions

- Keep model operations deterministic and UI-free.
- Route every new mutation through the application history and add a history test.
- Preserve automatic Tk/OS DPI scaling. Custom canvas dimensions must use the
  application's display scale; manual UI scale is layered on top of system DPI.
- Keep shortcut dispatch centralized and config-driven. Menu accelerator labels
  use the first configured combination, and focus guards for editing-oriented
  actions remain in the action handlers rather than the config parser.
- Editable property entries confirm through the same mutation callback as their
  Set/Apply button on both Return and keypad Enter.
- Text-input class bindings make Ctrl+A select all on every platform (and Cmd+A
  on macOS), overriding Tk's X11 cursor-to-start default without affecting the
  canvas.
- Add or adjust headless tests for every topology or serialization change.
- Do not call OpenFOAM during normal editing; export is cheap and deterministic.
- Before changing vertex ordering, verify both block orientation and OpenFOAM hex
  ordering with the integration test.
- Update this file and the README when scope, stack, commands, or file formats
  change so a future agent can resume quickly.
