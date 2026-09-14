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
  {run,describe,cavities,candidates,apply,focus}`.

## Architecture

- `blockdrawer/domain.py`: dependency-light dataclasses, result records, edge type
  aliases, `TopologyError`, and canonical `edge_key()`. Import domain types from
  here in serializers and algorithms; `model.py` re-exports them only for backward
  compatibility.
- `blockdrawer/model.py`: authoritative model state, edge/curve evaluation, and
  global validation. Vertices may be standalone or shared by counter-clockwise
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
- `blockdrawer/command_line.py`: shared shell-like tokenization for textual
  commands and OpenFOAM command prefixes. POSIX hosts use normal shell escaping;
  Windows preserves path backslashes while retaining POSIX quote grouping for
  WSL, container, and SSH wrappers around the Linux OpenFOAM tools.
- `blockdrawer/describe.py`: complete JSON description of a model plus a compact
  text rendering. Entities carry the same IDs the command registry accepts.
- `blockdrawer/quality.py`: mesher-style heuristics computed from the stored
  topology, curves, and grading: first-cell corner angles, non-orthogonality,
  equiangle skewness, corner cell aspect ratios, cell-to-cell growth, and cell
  size jumps across internal edges. Thresholds are a dataclass so callers can
  tune them; `checkMesh` remains the authority.
- `blockdrawer/render.py`: headless SVG/PNG rendering through one layout routine
  and two painters. Labels (vertex IDs, block IDs, cell counts, patch legend)
  are on by default; bounds, zoom-to-entity, and highlights select a region.
- `blockdrawer/checkmesh.py`: writes a minimal case, runs `blockMesh` and
  `checkMesh` through the `BLOCKMESH_COMMAND` prefix convention, and parses the
  logs into counts, geometry statistics, `***` problem lines, and FOAM FATAL
  errors. A `checkMesh` prefix is derived by substituting the word `blockMesh`
  unless `CHECKMESH_COMMAND` or an explicit prefix overrides it.
- `blockdrawer/cli.py`: argparse front end over the modules above. Every
  subcommand supports `--json`; failures exit 1 with `error: ...` on stderr or
  a JSON object carrying an `error` key.
- `blockdrawer/mcp_server.py`: Model Context Protocol server (`blockdrawer-mcp`,
  stdio by default) exposing the same operations as typed tools:
  `describe_session`, `list_commands`, `edit_session`, `quality_report`,
  `render_session` (returns the PNG inline), `validate_session`,
  `export_block_mesh_dict`, `check_mesh`, and `new_session`. Tools are plain,
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
  correctly oriented zMin/zMax boundary face per block.
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
  untouched and carries its exact reason. Acceptance separates a valid topology,
  an untangled sampled grid and meeting the declared quality limits, and the
  report carries the limits it was judged against. It may use NumPy and Pillow and keeps its own self-tests beside it.
  It imports BlockDrawer but nothing in `blockdrawer/` may import it, and none
  of its numerical code belongs in the package until it earns its way in. Its
  experimental agent operations stay in `research_cli.py`; do not widen the
  production CLI or MCP surface with them yet.

## Headless and agent workflow

- An edge is always written `first-second` in canonical (sorted) vertex-ID
  order, and accepted in either order. Because vertex IDs may themselves contain
  hyphens, text parsing tries every split against known vertex IDs and asks for
  the JSON list form when that is ambiguous. Blocks, vertices, patches, and
  curves are referenced by ID; curves also accept a unique name.
- `apply` is atomic: commands run in order against a copy of the loaded
  session, and nothing is written when any step fails. The error names the
  failing step and repeats the offending command. Without `-o` or `--in-place`
  the result is reported but not saved.
- Describe and quality JSON is meant to be diffed between edits. Keep field
  names stable, keep floats unrounded, and add fields rather than renaming them.
  Text output rounds to six significant digits.
- Quality thresholds are screening heuristics, not OpenFOAM's checks. Corner
  angles are measured between the first mesh cell's edges at each block corner
  so curved edges and grading count; interface ratios compare the transverse
  first-cell widths of the two blocks sharing an internal edge.
- Rendering reserves a band for the legend so it never covers geometry, culls
  entities outside the requested bounds, offsets cell-count labels outward from
  the first incident block, decimates control-point labels above 250 points,
  and hides node ticks above the canvas's individual-marker limit (200 cells). Highlight fills
  follow sampled curved edges. Do not add rendering paths that depend on Tk.
- `check` exports to a temporary or named case with a minimal `controlDict`,
  `fvSchemes`, and `fvSolution`, then runs `blockMesh` and `checkMesh`. The
  result is ok only when blockMesh succeeds and checkMesh prints `Mesh OK.`;
  every `***` line is surfaced as a problem. Logs are always kept in the case
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
  every nth subdivision index and always both endpoints. Only interior logical
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
  `neighbourPatch`; ordinary `cyclic` geometry is inferred by OpenFOAM. Pairing
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
- `MeshModel.add_block_from_vertices()` accepts four distinct existing vertex IDs
  in any order, sorts them counter-clockwise, requires a strictly convex block,
  and rejects duplicate/non-manifold/misoriented topology. Existing edge cell
  counts take precedence and opposite-edge constraints propagate through the new
  block.
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
  back.
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
