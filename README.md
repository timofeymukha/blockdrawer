# BlockDrawer

BlockDrawer is a small graphical editor for structured 2D block topologies with
straight, circular-arc, piecewise-linear, or spline edges. It exports an OpenFOAM
`blockMeshDict`; OpenFOAM's `blockMesh` does the actual meshing. Independent
reference-geometry curves can be drawn alongside the topology and used as smooth
projection targets.

The editor starts with one quadrilateral block. Vertices can be dragged, exterior
edges can be extended into new blocks, and each edge displays its uniform cell
subdivisions. The drawing is extruded between `zMin` and `zMax` when exported. A
single z cell is the default for pseudo-2D cases.

## Run

BlockDrawer requires Python 3.10 or newer with Tkinter. There are no third-party
runtime dependencies.

```bash
python -m blockdrawer
```

On Linux, Tkinter may be packaged separately by the operating system (for example,
`python3-tk`). It is included with the standard Python installers for Windows and
macOS. An optional installed launcher can be created with:

```bash
python -m pip install .
blockdrawer
```

### Browser editor

The same editor also runs in a browser tab, served by a small local server
from the standard library. It has the same toolbar, menus, modes, panels, and
shortcuts as the Tk window, draws with an anti-aliased canvas, and updates
live when a script or agent edits the open session file.

```bash
python -m blockdrawer.web mesh-blocks.json      # or: blockdrawer-web
```

The server listens on a loopback port chosen at random, prints its URL, and
opens it; the URL contains a per-launch token so other pages cannot drive the
editor. Use `--port` to pin the port and `--no-browser` to skip opening a tab.
File dialogs browse the server's filesystem. When the session file changes on
disk, a banner offers to reload it, which is how an agent editing through
`blockdrawer-cli apply` or the MCP server shows up in the open editor.

Markers scale with the zoom level: vertices, interpolation points, and labels
shrink or hide when their edges are small on screen, and mesh nodes turn into
dots and then vanish as they crowd together. The mesh preview is computed in
the browser from the edge nodes and kept as a cached bitmap, so panning and
zooming a topology with hundreds of thousands of preview cells stays fluid
and vertex drags update the preview without a server round trip for it.

## Edit a topology

- Click and drag a blue vertex to move every block that shares it. Coordinates can
  also be entered exactly in the right-hand panel.
- Press `V` or click **Add vertex**, then click the canvas to create one standalone
  vertex. Press `Esc` to cancel placement. Standalone vertices can be moved and
  edited like block corners, and selected with `N` to complete a missing block.
- Click an edge to edit its number of cells. The small edge markers are the
  graded subdivision locations. Opposite edges in every affected block receive
  the same cell count automatically, including transitive constraints through
  shared edges. When subdivision markers overlap at the current zoom, the canvas
  switches to a density image generated from every exact node: darker regions
  contain more nodes, and zooming in resolves individual locations. This changes
  neither the stored cell count nor export.
- Each edge has independent directional grading. The properties panel shows the
  direction as `start → end`, the geometric edge length, cell-to-cell expansion
  ratio, total end/start expansion ratio, start-cell width, and end-cell width.
  Enter any one of the four grading values and press its **Set** button (or
  `Enter`); the other three are recomputed immediately and the canvas node markers
  move to their graded positions. Widths use drawing-coordinate units before the
  global OpenFOAM scale. Shared edges keep one grading, automatically reversed for
  a neighboring block that traverses the edge in the other direction. Enable
  **Propagate** to apply the setting across the same transitive set of opposite
  edges that receives a propagated cell count; reversed arrows are handled
  automatically.
- Press `L` or click **Link spacing** to enter a grading-focused mode. Select two
  edges that share a vertex; the second edge is regraded so its touching cell
  width matches the first edge. A teal marker identifies every linked pair.
  Cell-count changes propagate along the complete chain of spacing links without
  changing linked follower counts. Grading changes attempt the same
  synchronization, but an unattainable linked width no longer rejects the
  requested grading: followers retain their prior grading and the affected links
  are marked out of sync. The normal Properties panel reports link status. The
  focused panel shows cell count, the four grading representations, endpoint
  links, removal controls, and **Synchronize links from this edge**, while hiding
  edge geometry and interpolation-point controls. Geometry edits also leave links
  out of sync; use **Synchronize** afterwards with the desired driver edge
  selected. Link creation, cell-count propagation, and explicit synchronization
  still reject conflicting or unattainable chains atomically and report the
  limiting edge length and cell count. Press `L` or `Esc` to leave the mode.
- Use the selected edge's **Type** control to choose `line`, `arc`, `polyLine`, or
  `spline`.
  An arc has one purple interpolation point through which its circle passes. A
  polyline has one or more numbered purple points joined by straight segments. A
  spline is a smooth Catmull-Rom curve passing through every numbered
  purple point. Drag points or enter exact X/Y coordinates; point-list types also
  support **Add**, **Remove**, and **Reset**. Reset distributes every existing
  point equidistantly along the straight line between the edge vertices. The
  **Point count** field accepts any positive integer; applying it replaces the
  list with that many points at the same equidistant chord positions.
- Select an exterior edge and press **Add block**, or double-click that edge. The
  new block extrudes along that edge's outward normal, using the source block's
  average perpendicular thickness. The newly generated opposite edge inherits the
  source edge grading in the corresponding physical direction. It remains
  selected, making a row of blocks quick to create even when the source block is
  skewed. Adding the block preserves the current pan and zoom; use **Fit view**
  explicitly when the complete expanded topology should be framed.
- Press `N` to create a block from four existing vertices. Click the vertices in
  any order; staged vertices are shown in purple and numbered, and the fourth
  valid selection creates the block immediately. Click a staged vertex again to
  deselect it, or press `Esc` to cancel the mode. The four vertices must form a
  convex, consistently oriented block topology.
- Select an edge and press `S` to split its complete conformal block strip. A
  purple marker appears on the selected edge; click or drag anywhere along that
  edge to position it. Mouse release only leaves the marker in place, so it can be
  repositioned as often as needed. The exact percentage can also be entered under
  **Current split (%)**. Press `Enter` or click **Execute split** to apply the cut;
  `Esc` cancels before the topology changes. The cut propagates through every
  block connected by the opposite-edge constraint, so no hanging vertices are
  created. Cell counts are divided at the nearest existing mesh node; a one-cell
  edge becomes two one-cell edges. Splitting is one undoable action.
- The two resulting portions of every affected edge retain the source edge type.
  Arcs are divided exactly, polyLines retain their piecewise-linear path, and
  splines are resampled from the original curve. Existing cell-to-cell grading is
  retained on both portions; a split placed on an existing graded node preserves
  the original selected-edge node locations to numerical precision. New internal
  cut edges use grading interpolated from the block's transverse sides. Boundary
  assignments on a split exterior edge are copied to both resulting edges.
- Select an internal edge and press `Shift+S` to perform the reverse operation.
  Every pair of blocks across the connected conformal cut is combined, preventing
  hanging vertices when the cut crosses multiple block rows. The removed outer
  edge segments are joined with their cell counts summed. Compatible grading is
  reconstructed from the first and last cell widths, so edges produced by a
  previous graded split recover their node distribution to numerical precision.
  Collinear lines, same-circle arcs, and polyLines retain their exact paths;
  splines and mixed curve types are joined as close spline approximations. Two
  boundary segments must have the same boundary assignment before they can be
  joined. Combining is one undoable action and is distinct from deleting an edge.
- Press `B` or click **Set boundaries** to switch the right-hand panel to boundary
  editing. Add a named patch, select it in the list, then click exterior edges to
  assign them; clicking an edge already assigned to the active patch unassigns it,
  and clicking an edge with another color reassigns it. Internal edges are not
  selectable in this mode. Every patch receives a persistent unique color that is
  also shown on its assigned edges. Press `B` or `Esc`, or click **Done
  boundaries**, to return to normal editing.
- Boundary types are `patch`, `wall`, `symmetry`, `empty`, and `cyclic`. For a
  cyclic boundary, choose its neighbouring patch. Type and neighbour selections
  apply immediately; there is no separate Apply button. BlockDrawer configures
  the reciprocal pairing in one undoable operation; changing or removing one
  member returns its former partner to `patch`. Ordinary cyclic translation or
  rotation is inferred by OpenFOAM from the matching patches. OpenFOAM couples
  face *i* of one patch with face *i* of the other, so export pairs the edges by
  geometry, never by vertex names: it finds the one translation (or, failing
  that, rotation) that maps every edge of the first patch onto an edge of the
  second, lists the second patch's faces in its partners' order, and starts
  each hex at the corner that makes the two blockMesh faces of every pair an
  opposite side pair running the same way (the order blockMesh writes their
  cell faces in). A pair with no such motion is refused naming the edges left
  without a partner, and a pair whose corresponding edges place a mesh node
  apart by more than OpenFOAM's matching tolerance (different counts, grading
  or curves) is refused naming both edges.
- Press `E`, choose **File → Export blockMeshDict…**, or click **Export** to open
  the Export panel. It contains the z cell count, z extents, scale, and the names
  and types of the automatic zMin/zMax patches. These values are applied when a
  destination is chosen and the dictionary exports successfully; there is no
  separate Apply step. The two patch names must be distinct from each other and
  from side-boundary names. Selecting `cyclic` for either z face sets both faces
  to `cyclic` and writes their reciprocal neighbour patches automatically. Press
  `E` or `Esc` to close the panel.
- Click **Add curve** to create a named reference-geometry curve with two initial
  points, or **Import curve…** to create one from a text point list. Reference
  curves are smooth Catmull-Rom interpolants through every ordered input point.
  They use labeled teal dashed lines, visually distinct from block edges, mesh
  vertices, and edge control points. Manually created curves initially show
  numbered square points; imported curves initially hide them so dense point
  lists remain readable.
- Select a reference curve or point to edit its name and exact coordinates.
  Use **Show curve points** in Properties to toggle that curve's square point
  markers. Points can also be dragged, inserted after the selected point,
  removed while at least two remain, or replaced from another point file.
  Replacing a curve from a file hides its markers. Press `X` while a curve is
  selected to delete the complete curve; use **Remove point** to remove only the
  selected point. These edits, including point visibility, are undoable.
  Point files contain one `x y` or `x, y` pair per line; blank lines and text
  following `#` are ignored. For example:

  ```text
  # inlet profile
  0.0, 0.0
  0.5, 0.15
  1.0, 0.0
  ```

- Use **View → Block mesh** and **View → Geometry** to show or hide the two
  representations independently. These choices persist as application
  preferences; `G` toggles the geometry layer by default. **View → Mesh
  subdivision nodes** independently controls the graded node markers on block
  edges, while **View → Edge interpolation points** controls the purple arc,
  polyLine, and spline point markers. Both marker choices persist without
  changing the mesh or edge definitions. **Fit view** frames whichever
  representations are visible.
- Press `P` or choose **Edit → Project onto geometry** to enter projection mode.
  First select one or more mesh vertices or mesh edges; the two entity types
  cannot be mixed in one operation. Click **Next: select target curves**, select
  one or more teal reference curves, choose a direction, and apply. Clicking a
  staged entity again deselects it, **Back to mesh entities** revises the first
  stage, and `Esc` cancels the workflow without changing the model.
- **Along x** and **Along y** move each source point parallel to that axis to the
  nearest curve intersection. **Orthogonal** uses the true shortest path to the
  smooth reference curves.
- Projecting a vertex moves that vertex. Projecting an edge moves both endpoints;
  polyLine and spline interpolation points are projected as well. Arc endpoints
  and their single interpolation point are projected together. The edge remains
  an arc when those points still define a circle, and becomes a spline if they
  become collinear. Invalid or inverted topology rejects and rolls back the
  complete operation. A successful projection is one undoable edit.
- For edge selections, enable **Fit edge as spline** to replace every selected
  edge with a through-point spline that follows the geometry section between its
  projected endpoints. The projection panel exposes the relative tolerance and
  maximum interpolation-point count for each edge. They default to `1e-8` of the
  fitted section scale (with an absolute `1e-12` floor) and 250 points. The
  bounded fitter adds points one at a time around the current worst section and
  retains the best valid measured result, discarding candidates whose adjacent
  points would be numerically coincident; it also preserves the reference curve's
  own knots when that gives an exact, compact fit. Both endpoints must land on
  the same reference curve. On a closed curve, the branch closest to the original
  edge is used; an equally close ambiguity is rejected instead of being guessed.
  Reaching the point cap is a successful operation: the status bar reports the
  measured maximum geometric distance, its absolute target, and whether that
  target was met. A result that invalidates the topology is still rejected and
  rolled back atomically. Fit is unavailable for vertex-only projection. For
  very dense fitted splines, the canvas shows a representative subset of smaller
  purple point markers to stay responsive; the complete point list remains
  available by index and is saved and exported without decimation.
- Use the mouse wheel to zoom—including deep inspection of tightly spaced
  imported points—middle/right-drag to pan, and **Fit view** to frame the complete
  topology.
- Press `Enter` or keypad `Enter` in an editable property field to apply the
  corresponding value. In Export mode this opens the export destination dialog.
- Press `Ctrl+A` in a text field to select its complete contents (`Cmd+A` also
  works on macOS).
- Use **Edit → Undo/Redo**, the toolbar buttons, or the configured shortcuts. The
  defaults are `Ctrl+Z`/`Ctrl+Y` on Linux and Windows, and
  `Command+Z`/`Command+Shift+Z` on macOS. A complete vertex or interpolation-point
  drag is one undo action, as is an edge-type, point-list, propagated grading, or
  propagated edge-count edit. Creating, removing, or synchronizing a spacing link
  is likewise one undoable action.
- Select an edge and press `Delete`, `Backspace`, or `X` to remove that edge and
  every incident block. The selection panel also shows an explicit delete button
  and the number of blocks that will be removed. Deletion is one undoable action;
  unrelated blocks are preserved. A deletion that would leave no blocks is
  disabled, so the final block can never be removed.

## Mesh preview

Press `M` or enable **View → Mesh preview** to draw a visualization-only
structured grid inside every block. Boundary nodes follow the configured cell
counts, directional grading, and line/arc/polyLine/spline geometry. Interior
points use the 2D specialization of OpenFOAM's edge-weighted transfinite
interpolation, including the actual graded fractions on all four block edges.
This preview does not create a `polyMesh`, validate cell quality, or replace
OpenFOAM's `blockMesh`.

The **View** menu also controls the independent visibility of vertex-ID labels,
numeric edge cell-count labels, mesh subdivision nodes, and curved-edge
interpolation points. Hiding a label does not affect selection or mesh data.

While preview is active, the right panel exposes a positive **Coarsening factor**.
A value of `1` uses every edge subdivision; `10` uses every tenth subdivision
counted from both ends of each edge (so neighbouring blocks keep the same
nodes on their shared edge), while always retaining block corners. Press
`Enter` or **Apply coarsening** to
update it. The panel reports sampled-node and interior-line counts together with
construction and canvas-drawing time. Preview data is cached by block geometry,
edge geometry, grading, cell counts, and coarsening, so hiding and reopening an
unchanged preview reuses it immediately. The cache deliberately ignores
boundaries, reference curves, and selection state and retains only one sampled
grid to bound memory use.

## High-resolution displays

BlockDrawer opts into per-monitor DPI awareness on Windows and honors Tk's system
scaling on Windows, macOS, and Linux. Fonts, the property panel, canvas vertices,
mesh markers, line weights, and the initial window size use that detected scale.

If the desktop environment reports an unhelpful DPI—or larger controls are simply
more comfortable—choose **View → UI scale** and select 125%, 150%, or 200%. These
choices multiply the system scale rather than replacing it, while **System
(automatic)** restores the detected value. This selection is saved in the
application preferences. The properties sidebar scrolls when its controls exceed
the available window height.

BlockDrawer rejects vertex moves that would invert or collapse a block, make an
arc point collinear with its endpoints, or collapse adjacent point-list entries.
Poly-spline edges, multi-section grading, advanced coupled-patch transforms, and
3D editing are deliberately outside the current scope. The zMin/zMax faces are
global export patches rather than selectable 2D canvas edges.

## Preferences and shortcuts

BlockDrawer creates a human-editable JSON preferences file on first launch:

- Linux: `~/.blockdrawer`
- macOS: `~/.blockdrawer`
- Windows: `%APPDATA%\BlockDrawer\config.json`

Mesh geometry and extrusion values remain in the session JSON; this preferences
file is for application-wide behavior. `recentFiles` stores up to ten session
paths, most recent first. `ui.scale` accepts `"auto"` or a multiplier from `0.5`
through `4`. `ui.showBlockMesh`, `ui.showGeometry`,
`ui.showMeshPreview`, `ui.showVertexIds`, `ui.showEdgeCellCounts`,
`ui.showEdgeNodes`, and `ui.showEdgeInterpolationPoints` are booleans for the
corresponding visibility toggles. `ui.previewCoarsening` is a positive integer.
Changes made through **View**, the preview panel, or the recent-session menu
update the file immediately. Manual edits are loaded on the next launch.

Every keyboard action is configurable. A Linux/Windows default file looks like:

```json
{
  "format": "blockDrawerConfig",
  "version": 4,
  "ui": {
    "scale": "auto",
    "showBlockMesh": true,
    "showGeometry": true,
    "showVertexIds": true,
    "showEdgeCellCounts": true,
    "showEdgeNodes": true,
    "showEdgeInterpolationPoints": true,
    "showMeshPreview": false,
    "previewCoarsening": 1
  },
  "recentFiles": [],
  "shortcuts": {
    "new_session": ["Ctrl+N"],
    "open_session": ["Ctrl+O"],
    "save_session": ["Ctrl+S"],
    "save_session_as": ["Ctrl+Shift+S"],
    "export_block_mesh_dict": ["E"],
    "undo": ["Ctrl+Z"],
    "redo": ["Ctrl+Y", "Ctrl+Shift+Z"],
    "delete_edge": ["Delete", "Backspace", "NumpadDelete", "X"],
    "split_edge": ["S"],
    "execute_split": ["Enter", "NumpadEnter"],
    "combine_blocks": ["Shift+S"],
    "new_block": ["N"],
    "add_vertex": ["V"],
    "set_boundaries": ["B"],
    "link_spacing": ["L"],
    "project": ["P"],
    "toggle_geometry": ["G"],
    "toggle_mesh_preview": ["M"],
    "cancel": ["Esc"],
    "fit_view": []
  }
}
```

macOS defaults use `Cmd` instead of `Ctrl` for document and history actions.
Supported modifiers are `Ctrl`, `Shift`, `Alt`, `Option`, `Cmd`, and `Meta`;
supported keys include letters, digits, `F1`–`F24`, arrows, and the named keys in
the example. Multiple entries give an action multiple shortcuts. An empty list
disables its keyboard shortcut, while an omitted action inherits the current
platform default. Conflicting or malformed shortcuts cause BlockDrawer to use
defaults for that launch and report the problem without overwriting the file.

## Save and export

**File → Open Recent** lists the ten most recently opened or saved sessions.
Selecting a missing file removes it from the list, and **Clear Menu** removes all
entries.

**File → Save** writes a versioned BlockDrawer JSON session containing all
vertices (including standalone ones), quadrilateral blocks, edge cell counts,
optional edge geometry, per-edge grading, boundary definitions and assignments,
reference-geometry curves, spacing links, and export settings. Format version 7
adds persistent endpoint spacing-link pairs; version 6 added the names and types
of the automatic zMin/zMax patches; version 5 added named reference
curves, their ordered point lists, and their point-marker visibility; version 4
stores named boundaries and their colors, version 3 added non-uniform total
expansion ratios, and version 2 introduced interpolation points. Versions 1–6
remain loadable through explicit migrations.

**Export blockMeshDict** writes an OpenFOAM dictionary with:

- two z planes for every drawn vertex, including standalone vertices not yet used
  by a block;
- one `hex` per quadrilateral;
- the propagated x/y counts and global z count;
- matching lower/upper `arc`, `polyLine`, or `spline` definitions for each
  non-straight 2D edge;
- implicit straight edges and a 12-value `edgeGrading` entry on every block;
- one four-vertex extruded side face for every assigned 2D boundary edge, grouped
  into the configured OpenFOAM patches;
- one lower face per block in the configured zMin patch and one upper face per
  block in the configured zMax patch, including automatic reciprocal
  `neighbourPatch` entries when they are cyclic; and
- OpenFOAM's default patch for unassigned side faces.

Reference-geometry curves are intentionally omitted from `blockMeshDict`; they
are editing guides and projection targets rather than exported mesh entities.

Copy or save it as `system/blockMeshDict` in an OpenFOAM case, then run:

```bash
blockMesh
```

The UI never invokes `blockMesh` and never writes `polyMesh`; its interior mesh
preview is a transient visualization derived independently within each block.

## Symmetrize a session

`symmetrize_session.py` is a headless utility for BlockDrawer session JSON—not an
OpenFOAM mesh. It preserves one half of the editor topology and rebuilds the
other half by reflection. For an airfoil edited above the x-axis:

```bash
python symmetrize_session.py airfoil.json --axis x
```

This writes `airfoil-symmetric.json` beside the input without modifying the
original. Choose another destination or copy the negative half instead:

```bash
python symmetrize_session.py airfoil.json --axis x \
  --source-side negative --output airfoil-updated.json
```

`--axis x` reflects `y → -y`; `--axis y` reflects `x → -x`. The utility mirrors
blocks, standalone mesh vertices, curved-edge interpolation points, cell counts,
directional grading, exterior boundary assignments, and compatible spacing
links. Boundary definitions and export settings are retained, and the mirrored
edges use the same patch names as their source edges. Independent reference
curves are left unchanged.

The symmetry axis must follow block edges: a block with vertices on both sides is
rejected with instructions to split the topology first. A non-straight edge
shared on the axis must itself be reflection-symmetric. Target-side IDs are
regenerated with `mirror_` prefixes. If an output file already exists, pass
`--overwrite` explicitly.

## Command line and agents

Everything the editor stores can be inspected and edited without the GUI, which
also makes BlockDrawer usable by scripts and coding agents. The entry point is
`python -m blockdrawer.cli` (installed as `blockdrawer-cli`). Every subcommand
accepts `--json` for machine-readable output and exits with status 1 and an
`error:` line (or a JSON object with an `error` key) on failure.

```bash
blockdrawer-cli new case.json
blockdrawer-cli new layout.json --box -5 0 0 1   # first block where the layout wants it
blockdrawer-cli describe case.json                 # blocks, edges, vertices, patches, curves, links
blockdrawer-cli describe case.json --json --section edges --only b1
blockdrawer-cli describe case.json --bbox 0.6 -0.1 0.8 0.05   # just the blocks in a region
blockdrawer-cli describe case.json --component v0-v1           # edges sharing v0-v1's cell count
blockdrawer-cli describe case.json --unassigned --section summary   # exterior edges with no patch
blockdrawer-cli quality case.json                  # verdict, interior sampling, corner screen, warnings
blockdrawer-cli render case.json -o case.png --preview --highlight b1 v0-v1
blockdrawer-cli render case.json -o zoom.svg --zoom v4 --margin 0.5
blockdrawer-cli export case.json --case /path/to/openfoam/case
blockdrawer-cli check case.json                    # runs blockMesh and checkMesh
blockdrawer-cli commands                           # lists every editing command
```

### Construction operators

Four registry commands build whole rows and patches of blocks so a layout is
stated, not computed (every one atomic, one history entry, a non-convex block
refused by name; `id=PREFIX` names new vertices `PREFIX_0, ...` and blocks
`PREFIX_b0, ...`):

- `add_grid xs=... ys=... [skip=XMIN,YMIN,XMAX,YMAX ...] [outside=CURVES]` or
  `add_grid center=X,Y rs=... thetas=... [period=DEG]` - blocks from station
  lists, Cartesian or polar (circumferential edges are arcs). A cell is left
  out when its centre is in a skip box, inside an `outside` curve or inside an
  existing block; coincident vertices are reused, and existing exterior
  vertices on a kept cell's side become stations (`conform`, default on), so
  an H-grid closes round O-rings already built. A station that would cut an
  existing edge is refused by name.
- `add_band edges=... | bbox=... height=H [rows=N growth=G]` or `to_box=...` -
  rows of blocks along a chain or loop of exterior edges (`bbox` takes the
  unassigned exterior edges inside it): mitred spokes, a fan of corner blocks
  at a convex corner sharper than 60 degrees, open ends following the
  boundary they meet, offset arcs kept arcs; `to_box` carries every spoke to a
  rectangle, its corners taken by the nearest spokes. A band block that would
  fold - a mitre at a concave corner moves the offset end `height *
  tan(turn/2)` along the edge beside it, past a short edge's length - is
  refused with the corner, the edge and the largest height that builds. An
  offset spline keeps only the points that run forwards between its ends.
- `add_ring CURVE HEIGHT count=N | fractions=...` - a band round a closed
  reference curve: vertices on the curve at its sharp corners and spread by
  arc length, wall edges as splines through the curve's points.
- `set_boundary_by NAME [type=T] [curves=...] [bbox=...] [between=A,B]
  [unassigned=true]` - every exterior edge all given selectors choose, the
  patch created when missing. A curve whose end point is not repeated is
  closed as `add_ring` closes it where the mesh lies outside it (a blunt
  base), not where the closing chord bounds the domain itself (a cavity lid).

Measured (`review_operators/` beside the trial data): the 27-block backward
step in 8 commands (`add_grid` with one skip box and six `set_boundary_by`;
`size`: 21,750 cells, Mesh OK, 0 degrees); the grooved tandem in 14 (two
polar grids with `outside` the bodies, two `add_band ... to_box`, one H-grid,
251 blocks with the hand build's patches; shape, size, polish: 47,616 cells,
Mesh OK, max 20.5 degrees against the hand mesh's 42.7); a NACA 0012 at 5
degrees in 12 (`add_ring` with a trailing-edge fan, two mitred bands,
`to_box`; 59,908 cells at a first width of 1e-4, Mesh OK, max 48.6 degrees,
no face above 70). A session starts with one block (`new --box`): put it
where a grid cell will reuse it, or remove it with `remove_edge` at the end.

Edits are applied atomically with `apply`. Commands are short text lines or
JSON objects; edges are written `first-second` in either order, a grading
value or split fraction is read along the edge in the order you wrote it
(`set_edge_grading v9-v2 start_width 0.01` puts the small cell at `v9`), and
several commands may be given at once, read from a file with `-f`, or piped through
`--stdin`. If any command fails nothing is written. `--quiet` reports a
generated batch of thousands of commands in a few lines: the commands
counted by name, the vertex, block, curve and patch IDs created and removed
(runs written `v4..v93`) and the results worth reading - repairs, splits,
merges, fits and spacing links left out of sync.

```bash
blockdrawer-cli apply case.json --in-place \
  "set_edge_cells v0-v1 40" \
  "set_edge_grading v0-v1 start_width 0.002" \
  "set_edge_type v2-v3 arc" "set_control_point v2-v3 0 0.5 1.2" \
  "add_block v1-v2" \
  "add_boundary inlet" "set_edge_boundary v0-v3 inlet" \
  '{"op": "split_edge", "edge": ["v1", "v2"], "fraction": 0.5}'
```

`describe` lists every entity with the IDs the commands accept; `--only`
(IDs space- or comma-separated; one that names nothing is an error; a named
edge lists all its control points), `--bbox`, `--component` and
`--unassigned` narrow it to the part of a large topology being worked on.
`quality` starts with a verdict: the session validates and exports, no
cell folds, the worst non-orthogonality and where it is, and how many faces
exceed 70 degrees. Those numbers come from every block interior sampled at
its real cell counts and grading (`--interior-cells`, default 256 a side;
`--no-interior` skips it) with blockMesh's interpolation, and each face is
measured as `checkMesh` measures it - between the two cell centres and the
face normal; on the 30P30N sessions and three engine outputs the sampled
maximum and severe-face count equalled checkMesh's. The verdict also gives
the maximum face skewness, measured as checkMesh measures it (it fails a
mesh above 4); sampled and checkMesh values agreed to two decimals on the
engine, 30P30N and sized sessions. Faces on a `cyclic` pair are measured as
the coupled faces checkMesh sees, against the partner cell across the pair
(measured as plain boundary faces they read skewness 5.25 on a periodic
cascade where checkMesh read 3.26). Beside it, the corner
screen reports per-block corner angles measured from the first mesh cell,
non-orthogonality, equiangle skewness, corner cell aspect ratios, cell-to-cell
growth, and the cell size jump across every internal edge, with warnings at
adjustable thresholds; the text lists the 20 worst warnings (folded cells,
then angles by their distance from 90 degrees, then size defects by how far
past their limit; `--max-warnings N`, `--all-warnings`). These remain
screening measures; `checkMesh` is the authority. `render` draws the topology with vertex IDs, block IDs, cell
counts, patch colors, reference curves, and spacing links to SVG (standard
library) or PNG (requires Pillow, `pip install blockdrawer[png]`); `--zoom`
frames named entities and `--highlight` emphasizes them. Without `--height`
the canvas follows the shown region's aspect ratio, between a quarter and
twice the width, so a long channel comes out as a wide strip.

`check` closes the loop with OpenFOAM. It writes a minimal ASCII case, runs
`blockMesh` and `checkMesh`, and summarizes cell counts, patch sizes,
non-orthogonality, skewness, aspect ratio, volumes, every `***` problem
line, and the single-star warnings - above all the number of severely
non-orthogonal faces, which `Mesh OK` does not stop. It then reads the sets
`checkMesh` wrote (`nonOrthoFaces`, `skewFaces`, `wrongOrientedFaces`,
`highAspectRatioCells`, ...) back from the case before a temporary case is
deleted and maps each face to its owner cell's block - blockMesh numbers
cells block by block - listing per set the blocks worst first with their
count, the worst face's non-orthogonality, and the centroid and bounds of
the offending faces to pass to `render --bounds` (`--worst N` blocks per
set, `--no-sets` to skip). Set `BLOCKMESH_COMMAND` to any prefix that accepts `-case PATH`; the
`checkMesh` prefix is derived from it unless `CHECKMESH_COMMAND` is set. With
the supplied container:

```bash
make check SESSION=case.json
blockdrawer-cli check case.json --case /tmp/case --json   # keep the case and logs
```

On Windows, native paths in CLI commands and command prefixes retain their
backslashes, while POSIX single/double-quoted groups remain available for WSL,
container, or SSH wrappers around the Linux OpenFOAM applications. Such a
wrapper must accept the appended `-case PATH` arguments and make that generated
host case directory visible inside its Linux environment, translating or
mounting the path when necessary.

### MCP server for coding agents

The same operations are available as Model Context Protocol tools, so Claude
Code or any MCP client can inspect, edit, render, and check sessions directly.
Install the optional dependency and register the server:

```bash
python -m pip install 'blockdrawer[mcp]'      # or: pip install 'mcp>=2,<3' Pillow
blockdrawer-mcp                               # stdio transport, for MCP clients
```

The repository ships a `.mcp.json` that registers the server for Claude Code
when it is started in this directory, including the `BLOCKMESH_COMMAND` for the
supplied OpenFOAM container; edit that entry for another installation. The
tools are `describe_session`, `list_commands`, `edit_session` (atomic command
batches, inline or from a `commands_file` in the same format as
`blockdrawer-cli apply -f`), `quality_report` (`summary_only` for the headline
lines, `worst=N` for the worst blocks and interfaces), `render_session`
(returns the picture inline unless `return_image=false` writes it only),
`validate_session`, `export_block_mesh_dict`, `check_mesh`, and `new_session`.
The read tools answer compactly by default, since an agent pays for every
byte: `describe_session` returns text and, for a large session, only the
summary and patches with a note on how to narrow it (`only`, `bbox`,
`component`, `unassigned`, `sections`); `quality_report` returns the verdict,
the 10 worst blocks and edges and the 20 worst warnings as text;
`edit_session` returns the counted operations, created and removed IDs and
noteworthy results (`detail=true` for every command's result); `as_text=false`
gives the full JSON. On the 203-block 30P30N session this took
`describe_session` from 266 kB to 1.5 kB, `quality_report` from 302 kB to
4.2 kB, and the 3946-command A-airfoil build through `edit_session` from
18 MB to 1 kB. `check_mesh` carries the severe-face count and the sets
mapped to blocks.
Among the commands, `repair_block ID` tries the local moves an agent would
make by hand on one block - moving its interior vertices with their curved
edges carried along, merging it across an internal edge, splitting an edge,
regrading an edge to the series of its opposite edge - and applies the one
that most improves the block and its neighbours, judged on the corner
screen and the sampled transfinite interior by the worst cell and by the
count of severely sheared cells; it reports when no move helps - a sound
neighbourhood (no severe cell, every angle at least 45 degrees from
degenerate) is "nothing to repair", since it judges angles only and size
jumps and aspect ratios belong to the quality report, and anything else means
the defect is in the surrounding topology. `move_vertex ... carry_curves=true`
is the same curve-carrying move on its own.

`smooth` is the global counterpart, to run before `repair_block`: it moves
every free interior vertex (boundary vertices never move; `blocks=`, `bbox=`
and `hold=` narrow the scope), carrying curved edges, to lower checkMesh's
face non-orthogonality over the block interiors sampled at the real counts
and grading - a sum of $\tan^2\varphi$ over the faces, then the worst faces
directly, a move never raising the worst face of the blocks it touches.
It keeps what the measure does not see: every edge from a `wall` vertex keeps
its length within 5 percent (`band_tolerance`) and, at a smooth wall vertex
with one spoke, may turn towards the wall normal but not away from it, so a
boundary-layer band keeps its height and first cell; every edge keeps its
length within 50 percent (`length_tolerance`), so a thin wake strip is not
widened; and no first-cell size jump across an internal edge crosses 2.5
(`size_cap`), nor a corner cell's aspect 100, where it was below. The result
is applied only when the full sampled check stays untangled with no more
faces above 70 degrees and no higher maximum (else the displacement is
scaled back by halves), as one undoable edit; the report gives both
verdicts, the vertices moved and the largest band-edge change, or says there
was nothing to gain. On the engine's 30P30N it took checkMesh from 60 faces
above 70 degrees (max 77.9) to none (max 68.7) in 10 s, and the 475k-cell
version from 8725 to 4716 in 68 s.

`smooth slide=true` also slides boundary vertices along their boundary - a
gate in the wrong place along a wall is what a person moves first. A vertex
slides when its two exterior edges are one patch (not `cyclic`), the
boundary turns at most 30 degrees there and it is not held; corners, patch
junctions and trailing edges never move. Each boundary chain is taken once
from the input session and every edge is re-cut from that path at every
trial: lines stay lines, arcs on one circle stay arcs, polyLines keep their
breakpoints, splines are re-fitted through the original curve. The report
gives the boundary vertices slid and the largest distance of a re-cut edge
from the input boundary; a result past 1e-5 of the chain's length is
refused. A wall vertex carries the free end of its band spoke (length and
angle to the wall normal kept); the real-count limits apply to the wall
edges too, and in `mode=shape` a slid boundary edge keeps its length within
`length_tolerance` as well. `slide_vertex V fraction=F` (or `distance=D`,
`towards=W`) is the same re-cut for one vertex by hand; it does not carry
spokes. Measured with shape, size, polish and checkMesh (polish sliding
only, against no sliding): the fine 30P30N (`method=distance`) 0 faces
above 70 at 68.98 degrees (68.83), coarse 65.81 (65.79), grooved tandem
20.53 with no interface jump above 2.5 (20.51 with four), three ellipses
71.09 with two faces above 70 (72.21, three), three foils 52.72 (55.10),
NACA example 48.59 (48.60); re-cut edges within 4e-7 of the input walls,
every pinned vertex exactly where it was. On the engine corpus with
`--smooth` it resolved no further case (five of seventeen either way): the
cases left admissible are held by wall misalignment, which the smoother
does not minimise, and sliding moved it both ways (periodic hill 22.3 ->
14.3, narrow gap tip 5.3 -> 11.6). Sliding in `mode=shape` as well made
several sessions worse once sized (three rotated ellipses 93 faces above 70,
the fine 30P30N 69.8 degrees), so it is off by default and best kept to the
polish.

`smooth mode=shape` optimises the layout before its cells exist: every block
is judged at eight even parameter fractions a side (untangled at 32),
ignoring the stored counts and grading, with no size limits and the
wall-band rule kept. It is applied when that count-free verdict is no
worse, and reports the mesh the stored counts and grading now give, before
and after - they were chosen for the old shape, so `size` should follow. A
shape whose stale counts would fold a real cell is refused unless
`allow_stale_fold=true` (what the shape-size-polish sequence passes), so a
session is never left tangled by a command that did not say so. On the
engine's square cylinder it took the count-free maximum from 87.6 to 55.6
degrees (51.1 without the length floor below), where the real-count
smoother is held at 86.7 by the cell sizes.

Shape mode's one length limit: no edge ends shorter than
`shape_length_ratio` (default 0.1) of its input length. Unlimited, the
count-free optimum collapsed edges that `size` then had to resolve - a ring
edge of the three-cylinder engine session went from 0.2995 to 0.00073
(ratio 0.0024) and the sizing left a jump of 269; the high-lift hand build's
slat trailing-edge fan kite went to 0.030 of its length and left a jump of
28. The square cylinder's gain, though, is itself a large shrink: its
corner kites' diagonals go to 0.089 of their length. A tenth stops both
collapses and costs the square cylinder 1.3 degrees once sized; 0.05 kept
the square cylinder whole but left the three cylinders a jump of 10. Both
modes report the shortest and longest edge over its input length, with the
edges, in the result (`min_edge_ratio`, `min_edge`, `max_edge_ratio`,
`max_edge`), its `reason` and the batch digest; one at the floor is an
edge the optimum wanted shorter still.

`keep_lines=true` (both modes) keeps straight block lines straight: an
interior vertex with two opposite edges (sharing no block) that are both
`line` and collinear within 1 degree moves only along that line, and one on
two such lines does not move. It does what the benchmark agents did with
`hold=` (the blunt plate's wake-line vertices and the cascade's wake vertex
are all on two lines and stay; free, the cascade's moved 0.14), but it also
holds every straight spoke of a polar patch, ring or band, and once sized
it cost more than 3 degrees on three of the ten sessions measured, so it is
off by default; use it where the straight lines are the design (an H-grid,
a channel) and few spokes are straight, or hold the line's vertices.
Measured with shape (each variant), the session's `size` line, `smooth`
and checkMesh; the count-free maximum before -> after the shape step and
the smallest edge ratio, then checkMesh cells / faces above 70 / maximum
non-orthogonality / worst interface jump (quality screen). "Same" where the
floor never bound and the shape result is the free one; the benchmark
sessions without the agents' `hold=` lists and after-size fixes:

| session | free (before this change) | floor 0.1 (default) | floor 0.1 + `keep_lines` |
|---|---|---|---|
| backward step (example) | nothing to gain; 21,750 / 0 / 0 / 1.07 | same | same |
| grooved tandem (example) | 43.8 -> 23.3, 0.59; 47,616 / 0 / 20.51 / 3.42 | same | 43.8 -> 43.1, 0.21; 57,167 / 0 / 35.56 / 4.93 |
| NACA 0012 (example) | 56.2 -> 48.2, 0.27; 59,908 / 0 / 48.60 / 4.36 | same | 56.2 -> 49.3, 0.25; 57,022 / 0 / 49.02 / 5.13 |
| cylinder in a channel | 42.1 -> 38.9, 0.60; 21,824 / 0 / 35.62 / 1.84 | same | 42.1 -> 42.1, 0.95; 24,228 / 0 / 38.61 / 1.75 |
| blunt plate | 51.3 -> 25.6, 0.63; 88,942 / 0 / 29.53 / 10 | same | 51.3 -> 49.1, 0.29; 86,766 / 0 / 45.66 / 8.06 |
| U-bend | 41.7 -> 19.2, 0.48; 24,010 / 0 / 20.88 / 1.97 | same | 41.7 -> 41.6, 0.95; 20,998 / 0 / 21.70 / 1.49 |
| cascade (cyclic pairs matched) | 70.4 -> 59.8, 0.20; 60,130 / 0 / 60.80 / 10.3 | same | 70.4 -> 68.9, 0.64; 65,034 / 0 / 64.93 / 5.04 |
| high-lift (hand) | 81.8 -> 60.8, 0.030; 332,922 / 0 / 61.27 / 28 | 81.8 -> 60.8, 0.10; 331,921 / 0 / 61.41 / 6.54 | 81.8 -> 80.5, 0.10; 372,346 / 0 / 61.95 / 4.88 |
| three cylinders (engine) | 70.8 -> 62.8, 0.0024; 56,043 / 0 / 64.65 / 269 | 70.8 -> 63.0, 0.10; 56,069 / 0 / 64.83 / 6.15 | 70.8 -> 62.7, 0.10; 55,982 / 0 / 64.38 / 7.75 |
| square cylinder (engine) | 87.6 -> 51.1, 0.089; 4,452 / 0 / 50.86 / 2.13 | 87.6 -> 55.6, 0.10; 5,184 / 0 / 52.16 / 2.05 | same as floor 0.1 |

`size` sets every cell count and grading from a few physical inputs
(`first_width`, `core_size`, `growth`, optional `wall_aspect`,
`interface_growth` (default 1.25), `count_rule`, `budget`, per-patch
`first_widths`, `blocks`/`bbox` scope), using only what a
session has. The size field is directional: every wall segment asks for the
wall-normal series across it and for `wall_aspect` times that along it, and
the size wanted along an edge is the smallest any wall asks in that
direction, so the cells along a wall shrink towards a corner whose other
wall's normal runs along it. Each edge end gets the largest width it may
carry, finest first: within `interface_growth` of the edge continuing it
across a block interface, growing at most `growth - 1` per unit length along
an edge. Opposite sides of a block share one geometric series through every
strip of similar blocks (chosen to need the fewest cells), so no rectangle
is sheared; counts per component are the demand of the size profile and
what that shared series needs to stay within its bounds. The two edges of a
`cyclic` pair are one component and one strip, graded the same physical way,
so a sized periodic pair exports face by face (the benchmark cascade had 7 of
its 9 pairs differ before; matching them took the agent a hand batch). A
strip that wants fine cells at both ends is cut with `split_edge` where the
wanted size peaks (multi-section grading is out of scope), unless a piece
would be shorter than one cell of the size wanted along it or the strip holds
a cyclic edge (its partner would not be cut). That is the field's own state.
A strip a long count component makes fine (a thin wake strip, a seam
sliver) is invisible to it, so `budget` total cells (default 1.5 times the
field's own; a smaller budget first coarsens the field to budget/1.5) are
then spent on continuity: free regrades of a strip and count raises are
taken greedily by the drop in a jump cost (the square of how far each
interface's log width ratio exceeds log `interface_growth`) per cell, until
the first raise the budget cannot pay, so more budget never leaves a higher
cost. A block the field itself leaves above skewness 4 or with faces above
70 degrees is repaired first, judged at 256 cells a side. Each move is
checked on the sampled grid (256 cells a side) of the blocks it touches and
undone if it folds a cell, adds a face above 70 degrees, raises the worst
face past the field's or takes checkMesh's skewness past 4. The result is
then verified against the repaired field state at 256 a side, and at real
counts for every block with a face above 65 degrees (at 256 the fine
30P30N's b39 read 6 faces above 70 where checkMesh counts 7): no more faces
above 70 degrees and a worst face at most 0.5 degrees higher, or the
spending through the offending blocks is undone until that holds. The
report's `verification` gives both verdicts, so an agent sees what the
spending cost without `check`; the faces above 70 the field state itself
has are left to the polish and to hand corrections. A result above
skewness 4 from an input below is refused. Strips the
spent state leaves coarse between finer neighbours at both ends are split
and the budget spent again when that lowers the jumps. `keep_counts=true`
recomputes the gradings only. The report gives the cells, the field's own
cells and jump cost against the result's, whether the budget stopped the
spending (`budget_binding`; false when no move that lowers the jumps was
left or the guard refused them), the interface jumps above 2.5, 5 and 20
and the worst ones, the first widths each wall patch's spokes got against
the one asked (`bands`), the moves, the guard's refusals, the splits and
the links left out of sync; the quiet digest (`apply --quiet`, MCP default)
carries all of it as short strings. The worst jump is `quality`'s own
measure on the sized session: `quality` after a later `smooth` reads a
different one because the polish moves vertices (three cylinders: 6.14
after `size`, 5.59 after the final `smooth`). Measured (shape, size, polish, checkMesh, budget
= the reference's cells; references: hand meshes and the previous fixed
sizing): the backward step 15,204 cells, 0 degrees, worst jump 1.1 (hand
23,598, 1.1); the square cylinder 2.2 against 6.0 and the three foils 9.1
against 34.0; the three ellipses 25.1 against 17.4 (a thin gap strip
beside an edge that needs fine cells at both ends, whose split would leave
a sliver); the grooved tandem 10.3 against the hand's 7.6 at the same
62.5k cells with fewer jumps above 2.5 and 5, 4.7 at twice them; the
coarse 30P30N 64.7 against 56.8 at 15.2k cells with every jump count
lower, 41 at twice the cells. **Fine engine sessions** (hundreds of cells
a block side): on the fine 30P30N spending adds no face above 70 degrees,
but the field state itself has 31 at 87.4 degrees (452,791-cell budget; 71
at the default budget) in the tapered blocks b21, b39 and b53, which the
repair's moves do not lower; after the polish checkMesh counts 6 at 85.8
degrees (12 at the default budget), where the previous fixed sizing gave 0
at 68.8. Check fine sessions and correct those blocks by hand.

`method=distance` keeps item 3's sizing reachable (`size_distance.py`, as
of commit 9a84f29): counts from the wall distance alone, wall-normal edges
graded, tangential ones uniform, no budget spending, `interface_growth`
unmatched unless given. It is verified and reported the same way
(`verification.field` equals `verification.result`). It is a comparison,
not a general retry. On the fine 30P30N (`smooth mode=shape
allow_stale_fold=true`, `size 5e-5 1.0 growth=1.08 wall_aspect=25
count_rule=max method=distance`, `smooth`) it gives 452,791 cells, Mesh OK,
0 faces above 70, max 68.83 degrees, where the default gives 6 at 85.8.
In the first benchmark round two agents followed the earlier advice to
retry with it whenever `verification.field` had faces above 70, and it was
worse on both: the NACA 4412 C-grid 88,555 cells with 2,088 faces above 70
(max 81.5) where one face at 70.6 was left; three cylinders skewness 3.85
and a worst jump of 19.2, where the default's polish had already cleared
the faces. Polish and `check` first; keep a `method=distance` mesh only
when `check` prefers it.

Known limit - counts are never lowered: spending only adds cells, so a
count the field sets too high stays. On the coarse 30P30N the 0.0019-long
sliver v12-v13 gets 9 cells from its component c25, whose wall-side edge
v53-v55 asks for them, and the seam jump b21/b31 reads 64.7; the previous
sizing gave that component 2 cells and read 11.4. Lower such a count by
hand (`set_edge_cells v12-v13 2`) and run `size keep_counts=true`.
The sequence for a finished topology is
`smooth mode=shape allow_stale_fold=true`, `size ...`, `smooth`.
A running server keeps the tool schemas it started with; restart it after
upgrading BlockDrawer. Every tool takes the session path, so agents and the GUI
can share one file: save in the editor, let the agent edit and check, then
reopen.

The server's instructions cover tool mechanics only. The procedure - choose
the route (construction operators or the topology engine), build, shape,
size, polish, judge, deliver - is the repository skill
[`.claude/skills/mesh-blocking/SKILL.md`](.claude/skills/mesh-blocking/SKILL.md),
which Claude Code loads as a project skill and any other agent can read as a
document; its reference files (`engine.md`, `sizing.md`, `failures.md`) and
three worked example batches (`examples/`) sit beside it. It is kept in step
with the topology engine under `experiments/`.

## Tests

All normal tests are headless and use the standard library:

```bash
python -m unittest discover -s tests -v
```

An optional integration test asks a real OpenFOAM installation to parse and mesh
generated multi-block, graded, circular-arc, polyline, spline, named-patch, and
cyclic dictionaries, including conformally split block strips:

```bash
BLOCKMESH_COMMAND='apptainer exec /path/to/openfoam.sif blockMesh' \
  python -m unittest discover -s tests -v
```

For the OpenFOAM 2606 image supplied alongside this repository, its environment
setup is already captured by:

```bash
make integration-test
```

## Research prototypes

`experiments/` holds research code that is deliberately outside the runtime
package and outside the normal test suite. `experiments/agentic_topology/`
turns a planar fluid domain into a conformal all-quadrilateral block topology
with explicit near-wall bands and writes it as an ordinary BlockDrawer session.
It handles two domain families - external flow around any number of disjoint
closed bodies, and simply connected internal flow with named wall, inlet,
outlet, symmetry or translational-periodic boundary chains - and measures the
result on the actual sampled transfinite grid rather than on four block corners.
It needs NumPy and Pillow, which are not BlockDrawer dependencies, and it has
its own self-tests:

```bash
make research-test             # python -m unittest discover -s experiments/agentic_topology -v
make research-periodic-hill    # classical periodic hill, an internal-flow session
make research-cases            # every synthetic fixture, with its report
make research-30p30n           # downloads the 30P30N case and emits its analysis
```

Acceptance is reported in separate terms rather than one word: whether the
topology is valid, whether the sampled grid is untangled, and whether every
declared quality limit is met. A result that is a real mesh but misses a target
is still written out, with each miss naming the metric, the observed value and
the limit; a crossed or tangled one is not written at all.

Its command line also offers `describe`, `cavities`, `candidates`, `apply` and
`focus` so an agent can choose between validated topology alternatives instead
of placing vertices.

See [experiments/agentic_topology/README.md](experiments/agentic_topology/README.md)
for the algorithm and its current limits.

See [AGENTS.md](AGENTS.md) for the architecture, invariants, and contributor resume
guide.
