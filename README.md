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

Seven registry commands build whole rows and patches of blocks so a layout is
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
  unassigned exterior edges inside it): mitred spokes, and where the chain
  turns convexly by more than 60 degrees a fan of corner blocks, one per 90
  degrees of turning or part of it (a square corner gets one block, a sharp
  trailing edge two), open ends following the boundary they meet, offset
  arcs kept arcs; `to_box` carries every spoke to a rectangle, its corners
  taken by the nearest spokes, with no fans. The note gives the front's
  vertices in the chain's order and its two ends (new IDs are numbered in
  creation order, not along the chain). A band block that would
  fold - a mitre at a concave corner moves the offset end `height *
  tan(turn/2)` along the edge beside it, past a short edge's length - is
  refused with the corner, the edge and the largest height that builds. An
  offset spline keeps only the points that run forwards between its ends.
  `fan=false` leaves every corner that would fan open, `open_at=X,Y;...`
  the convex corner nearest each point (`add_ring` takes both): the band
  ends on each side at the spoke normal to that side and the wedge between
  the two spokes is left for what comes next - a wake band. `between=A,B`
  selects the unassigned exterior chain joining two vertices.
- `add_rows edges=... | bbox=... | between=A,B direction=DX,DY to_x=X |
  to_y=Y | distance=D [stations=... | rows=N growth=G]` - rows of blocks
  carrying a chain of exterior edges along a direction: every point of the
  chain (vertices and the interior points of curved edges) moves along it
  to the line `x = X` or `y = Y`, each its own distance, or by `distance`.
  The rows between lie at `stations` (x values with `to_x`, y values with
  `to_y`, distances with `distance`: each row on one such line) or are
  `rows` rows whose heights along every point's path grow by `growth`;
  `spread=S` widens them along the target line, to S times the chain's
  width on the far row (a wake band that widens: carried at constant width
  from a trailing edge, the wall's clustering ran to the outlet in one
  `size` strip, checkMesh aspect ratio 6390 on the skill's C-grid example).
  Vertices are reused by coordinate, and with `conform` (default) an
  existing exterior vertex on a point's path becomes a station; a row
  vertex that would cut an existing edge, a block over an existing block or
  vertex, and a block that would not be strictly convex (a direction into
  the chain's blocks) are refused. The note gives the far row in the
  chain's order. `add_grid` makes rectangles from station lists and
  `add_band to_box` sends spokes along the wall normals to a box; this
  carries a given chain along one direction. A trailing edge left open and
  its two spokes carried downstream is a C-grid's wake band:
  `add_ring airfoil 0.05 count=40 fan=false`, then `add_rows
  bbox=... direction=1,0 to_x=1.6`. Re-expressed round-2 trials, default
  chain and checkMesh against the trial's own batch on the same code: the
  airfoil in a tunnel, the same 208 blocks in 31 commands for 36 (the fan
  removed and three typed vertices and two blocks became one `add_rows`),
  Mesh OK 128,982 cells and 25.43 degrees against 128,902 and 26.08 (the
  vertex IDs differ, so the smoother's order); the cascade, the same 50
  blocks in 37 commands for 52 (the spokes turned across the wake by two
  `move_vertex`, the wake strips one `add_rows ... to_x=2 rows=3`),
  Mesh OK 49,182 cells, 60.62 degrees, skewness 2.86 against 50,974, 60.57
  and 2.53.
- `add_ring CURVE HEIGHT count=N | fractions=... | normals=... [corner=DEG]
  [tips=true] [side=in]` - a band round a closed reference curve: vertices on the
  curve at its corners (turning more than `corner`, default 30 degrees), at
  the given arc-length fractions, where the outward normal points at each
  `normals` angle (degrees from +x; exactly one such point, else refused),
  with `tips=true` at the peak of each rounded convex corner (the wall
  turning 45 degrees within a hundredth of the perimeter either side: an
  elliptic nose, a leading edge), and spread by arc length between them;
  wall edges as splines through the curve's points. No spline an operator
  writes (ring walls, band fronts, fill sides along a curve) runs backwards:
  OpenFOAM's Catmull-Rom spline loops in a segment much shorter than its
  neighbour (a station just past a curve point), so the control point at
  the end of such a segment beside its longer neighbour is left out, pass
  by pass until nothing hooks (`model.unhooked_points`). The result's note lists
  the stations. Tips are off by default: on 10:1, 4:1 and 5:1 ellipses they
  cost 0.4 to 1.1 degrees of sampled non-orthogonality at default counts.
  For `to_box`, aim ring stations at the box corners with `normals=`; a
  band block that would fold against the box is refused with the spokes and
  corners named. `side=in` puts the band inside a closed curve.
- `add_ring CURVE HEIGHT side=left|right count=N | fractions=... | xs=... |
  ys=... [between=F0,F1 | between=X0,Y0,X1,Y1] [start=DX,DY] [end=DX,DY]` -
  a band along an open reference curve (internal flow: a wall chain, no
  host block), on its left or right as its points run: stations at the
  section's two ends, at its corners, at the fractions, where it crosses
  each x or y (exactly once, else refused) and by count between them; wall
  edges are splines through the curve's points; `start`/`end` give the
  first and last spoke's direction (its normal component is the band
  height) so they lie on an inlet or outlet line, else they are normal to
  the wall. `normals` and `tips` need a closed curve. `add_band` takes
  `start`/`end` too. The round-2 nozzle (throwaway fills outside each wall,
  a band from them, 23 `remove_edge`, two `move_vertex` for the end spokes,
  45 commands) is two `add_ring ... side=left xs=... start=0,1 end=0,1`
  and one `add_fill`, 13 commands: the same 33 blocks, vertices within
  1.4e-6 of the trial's (its two hand-moved inlet spokes); default chain
  and checkMesh identical (14,620 cells, Mesh OK, 12.01 degrees).
- `add_stations CURVE count=N [between=F0,F1 | between=X0,Y0,X1,Y1] |
  fractions=... | xs=... | ys=... [offset=DX,DY] [id=NAME]` - standalone
  vertices on a reference curve (on the polyline through its points, where
  `add_ring` puts its stations), ordered along it and named `NAME_0, ...`:
  a count spread by arc length over the curve or a section of it, the given
  arc-length fractions, or where it crosses each x or y (exactly one
  crossing in the section, else refused). `offset` translates every station,
  so the partner chain of a translational periodic pair gets matching
  stations in one command; `add_fill` then takes them as a side's stations.
  A spline edge along a curve keeps its points symmetrically from both ends,
  so the two chains, cut in opposite directions, export as a node-for-node
  cyclic pair. On the round-1 cascade build the periodic stations, the
  passage round the blade, the inlet and the wake region (16 `add_vertex`,
  4 `project`, 12 more `add_vertex` and 26 `add_block_from_vertices`) are
  two `add_stations`, four `add_vertex` and four `add_fill`: same blocks,
  vertices within 1.3e-5; with the trial's shape, size and polish, 59,696
  cells, Mesh OK, max 65.1 degrees, skewness 3.26 (the original batch on
  the same code: 64.4, 3.25).
- `add_fill A,B,C,D [sideK=A,...,B] [curveK=CURVE]` - blocks filling a
  four-sided region between existing vertices, corners in order round it
  (side k from corner k to k+1). A side not given is the chain of exterior
  edges joining its corners with their blocks outside the region (a band
  front, a grid's side), else open: straight, or along `curveK`, through
  the standalone vertices lying on it, or with none through new stations
  facing the opposite side's (a single edge when both are open). Interior
  vertices are blockMesh's transfinite interpolation of the four sides; new
  side edges between points on `curveK` follow it as splines. Opposite
  sides need as many edges, else it is refused naming the sides and where
  a `split_edge` or a station would match them; a block that would not be
  strictly convex is refused with its corners, as is a block over an
  existing block or vertex. Where both readings of the corners bound a
  region (half an annulus between two loops) the counter-clockwise one is
  taken; where none does, the refusal names for each reading the existing
  vertex inside or the side edge whose block lies inside. The note names
  the standalone vertices each open side took, and says when a block it
  built folds on the count-free map (32 even fractions a side) - a note,
  not a refusal. Three or five corners give that many blocks round a centre vertex
  (the triangle and pentagon templates): side k is split where spokes k-1
  and k+1 meet it, which needs its edge count to equal the sum of their
  counts - an even total, and in a triangle each side shorter than the
  other two together; otherwise it is refused with the spoke counts the
  sides give. On the round-1 high-lift build the cove pocket (20 `add_vertex`,
  24 `add_block_from_vertices` and a script) is one `add_fill`, and the
  slot strips three; same blocks, vertices within 1.3e-4 of the script's.
- `set_boundary_by NAME [type=T] [curves=...] [bbox=...] [between=A,B]
  [unassigned=true]` - every exterior edge all given selectors choose, the
  patch created when missing. A curve whose end point is not repeated is
  closed as `add_ring` closes it where the mesh lies outside it (a blunt
  base), not where the closing chord bounds the domain itself (a cavity lid).

One block from corners by coordinates: `add_block_from_vertices bl,br
points=0,1;0.9138,1` takes each point as the existing vertex there (within a
billionth of the drawing's size; a miss is refused naming the nearest vertex
and its distance), so a batch need not look up the IDs `add_grid` gave. The
round-1 junction's branch (two `add_vertex`, a block from probed IDs,
`split_edge` and `slide_vertex`) is two `add_stations` and two blocks: same
vertices to 6e-13, same checkMesh numbers.

A vertex relative to existing ones: `add_vertex at=V dx=0.1 dy=0` (an
offset), `add_vertex x=-0.7 y_of=V` or `x_of=V y_of=W` (level with them),
`add_vertex r=0.35 theta=45 about=V` or `center=X,Y` (a polar point); every
coordinate from exactly one source, `dx`/`dy` added last. `move_vertex`
takes the same forms, a coordinate not given staying as it is
(`move_vertex V dy=0.1` moves it up by 0.1). Four round-2 trials typed
17-digit coordinates of existing vertices or polar points to 1e-9 for
this. Other commands still take numbers (`add_grid` stations, `points=`
of `add_block_from_vertices`, which names vertices by ID anyway).

Measured (`review_operators/` beside the trial data): the 27-block backward
step in 8 commands (`add_grid` with one skip box and six `set_boundary_by`;
`size`: 21,750 cells, Mesh OK, 0 degrees); the grooved tandem in 14 (two
polar grids with `outside` the bodies, two `add_band ... to_box`, one H-grid,
251 blocks with the hand build's patches; shape, size, polish: 47,616 cells,
Mesh OK, max 20.5 degrees against the hand mesh's 42.7); a NACA 0012 at 5
degrees in 12 (`add_ring` with a trailing-edge fan, two mitred bands,
`to_box`; 60,976 cells at a first width of 1e-4, Mesh OK, max 49.6 degrees,
no face above 70, since its ring stopped writing splines that run backwards;
59,908 and 48.3 before). A session starts with one block (`new --box`): put it
where a grid cell will reuse it, or remove it with `remove_edge` at the end.

Splines that run backwards, measured: rebuilt with and without the rule,
the three skill examples and the nine operator builds of benchmark round 2
(the NACA 4412 and three-cylinder sessions come from the engine) apply
unchanged, and every hooked edge goes (NACA example 12 of 160 splines,
airfoil tunnel 26 of 80, bluff ground 4 of 8, blunt plate 2 of 16, cascade
11 of 48, high-lift 49 of 202, ten of them now lines); the other six builds
are byte-identical. The written edges still follow their curves: the
largest distance of a changed wall edge from the curve's polyline rose at
most from 3.7e-4 to 5.9e-4 (high-lift, an edge along a wake guide curve
typed by eye), on the bodies from 1.9e-5 to 2.0e-5 chords (NACA). Default
chain (`smooth mode=shape allow_stale_fold=true`, `size`, `smooth`) and
checkMesh, before -> after: NACA example 59,908 -> 60,976 cells, 48.27 ->
49.64 degrees; cascade 60.57 both; blunt plate 30.93 -> 30.63; bluff ground
identical; airfoil tunnel 113,164 -> 128,902 cells, 27.40 -> 26.08; high-lift
checkMesh failed (skewness 4.05, 1391 faces above 70, 77.26 degrees) -> Mesh
OK (3.93, 198 faces, 71.93) - its shape step, refused before over 225 cells
folding on the count-free map, now runs - at 396,460 cells against 292,143
and 281 interface jumps above 2.5 against 96. No face above 70 gained, no
maximum up by more than 1.4 degrees.

Edits are applied atomically with `apply`. Commands are short text lines or
JSON objects; edges are written `first-second` in either order, a grading
value or split fraction is read along the edge in the order you wrote it
(`set_edge_grading v9-v2 start_width 0.01` puts the small cell at `v9`), and
several commands may be given at once, read from a file with `-f`, or piped through
`--stdin`. If any command fails nothing is written. `--quiet` reports a
generated batch of thousands of commands in a few lines: the commands
counted by name, the vertex, block, curve and patch IDs created and removed
(runs written `v4..v93`) and the results worth reading - repairs, splits,
merges, fits and spacing links left out of sync. Without `--quiet` every
command's result is printed on one line, nested values elided, and a command
that has a digest note (a `size`'s verification verdicts, first widths and
worst jump among them) gets that note on an indented `note:` line beneath,
so the long form never shows less than the quiet one.

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
`--unassigned` narrow it to the part of a large topology being worked on;
`--section` picks sections (`patches` is accepted for `boundaries`).
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
cascade where checkMesh read 3.26). It gives checkMesh's cell aspect ratio
too (`primitiveMeshTools::cellClosedness`: per cell the sums of the
magnitudes of each Cartesian component of its face area vectors, the
largest over the smallest across the solved directions - x and y when both
z patches are `empty`, else also z with the hydraulic ratio), with the
block of the worst cell and the cells above checkMesh's limit of 1000,
each such block warned by name; on the eleven sessions of benchmark round
2 the sampled maximum equalled checkMesh's to two decimals, and on a
default-sized blunt plate it read 1846.52 and 217 cells above 1000 as
checkMesh did. The measure is Cartesian: a thin cell at 45 degrees reads 1.
`size` reports it in its verification but does not act on it. Beside it, the corner
screen reports per-block corner angles measured from the first mesh cell,
non-orthogonality, equiangle skewness, corner cell aspect ratios, cell-to-cell
growth, and the cell size jump across every internal edge, with warnings at
adjustable thresholds; the text lists the 20 worst warnings (folded cells,
then angles by their distance from 90 degrees, then size defects by how far
past their limit; `--max-warnings N`, `--all-warnings`). A spline edge with
a segment running backwards along its chord is warned of in any session
(edge, segment, the control point to remove), ranked with the worst angle
defects, and counted in the summary's `hooked_spline_edges`. It also gives, per
wall patch, the first cell height normal to the wall at the band spokes
(`first_widths`, the same measure `size` reports, so it can be read after
the polish): at each wall vertex an edge on no wall patch is a spoke of the
wall edge there whose inward normal it is closest to, when within 45
degrees of it (a wake line leaving a sharp trailing edge and an edge
running on along the wall's line are not spokes), and its first width is
the height of its first node above that wall edge's tangent at the vertex;
the spokes with the smallest and largest heights are named, and the edges
at the wall left out by the rule are counted. These remain
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
count of severely sheared cells (neither may get worse for a gain in the
other); it reports when no move helps - a sound
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
(`size_cap`), nor a corner cell's aspect 100, where it was below; a jump
above 2.5 may grow by a tenth but not past 5 (`size_ceiling`) when it was
below 5 (the benchmark's target: three cylinders once went 4.73 -> 5.20).
Measured on the 95 distinct sized states of the shape-limit corpus below
(every rule's `size` output, the polish with and without this ceiling):
three changed, all the three cylinders - 12 jumps above 5 -> 10 at the
default shape, 2 -> 0 without the shape step and at floor 0.3 (maximum
68.11 -> 68.13 in one) - and none got worse in faces above 70, jumps above
2.5 or 5, or maximum non-orthogonality. The result
is applied only when the full sampled check stays untangled with no more
faces above 70 degrees and no higher maximum (else the displacement is
scaled back by halves), as one undoable edit; the report gives both
verdicts, the vertices moved and the largest band-edge change, or says there
was nothing to gain. On the engine's 30P30N it took checkMesh from 60 faces
above 70 degrees (max 77.9) to none (max 68.7) in 10 s, and the 475k-cell
version from 8725 to 4716 in 68 s.

The polish keeps checkMesh's cell aspect ratio limit as `size` does: a
result crossing 1000, or gaining cells above it when already above, is
scaled back like a result that fails the rest of the check; when the full
displacement crossed it, the optimisation runs once more with the free
vertices of the blocks it took past the limit held, and the result with
fewer faces above 70 degrees (then the lower maximum) is applied. `reason`
says when the limit bound and the result lists `aspect_blocks`; `mode=shape`
judges stale counts, so the limit does not apply there. Measured with the
polish before and after on the 20 corpus sessions' sized states (default
chain) and the skill's C-grid example sized at a first width of 1e-4: the
20 never crossed the limit and are byte-identical; the C-grid went from
checkMesh failing (aspect 1356, 40 cells; 10 faces above 70, max 75.42) to
Mesh OK (aspect 978, applied at half the displacement; 11 faces above 70,
max 76.15), and without its wake spread from 339 cells above the limit
(6390) to 284 (6194; max 61.52 -> 63.18, the held second run taken) -
still failing, since `size` left it above.

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

Two further limits measure what the step does to lengths in the terms
`size` pays in, both off by default. `shape_pair_ratio=K` keeps the length
ratio of every interface pair - two edges continuing each other through a
vertex across a shared edge, the pairs whose first-cell widths the
quality screen's size jump compares and `size` relaxes over
(`MeshModel.interface_pairs`) - within a factor K of its input value.
`shape_spread_ratio=K` keeps every count component's length spread
(longest over shortest member: the cell-size ratio its one count puts
between them before any grading) from growing past K times its input; a
spread that falls is never limited. Both hold in the search and at every
fallback scale, like the floor, and the result reports the largest change
of each in the result (`max_pair_change`/`max_pair`,
`max_spread_growth`/`spread_edge`), the reason and the digest note. Measured over twenty sessions - the
eleven round-2 benchmark sessions at their pre-shape state (the build
replayed up to the shape line; the two engine sessions as the engine wrote
them) and nine of the ten above (the three-cylinder engine session is the
same file in both rounds) - each with the default chain: the shape step,
the session's `size` line (the brief's widths, `wall_aspect=25` unless the
session's own line said otherwise, no `budget`, `grading=strip` then the default), the
polish, then `quality`'s sampled verdict at 256 cells a side. Each cell
reads: maximum non-orthogonality / faces above 70 / interface jumps above
2.5, above 5. Above 150k cells only no shape, the default, pair 2.5 and
spread 2.5 ran;
the round-2 high-lift session folds after the default `size` (its trial
fixed that by hand) and its shape step is refused (count-free folds), so
its row is the same throughout:

| session | no shape | floor 0.1 (default) | floor 0.3 | floor 0.5 | `keep_lines` | pair 1.5 | pair 2.5 | spread 2.5 |
|---|---|---|---|---|---|---|---|---|
| square cylinder (engine) | 84.5 / 432 / 20, 6 | 52.2 / 0 / 0, 0 | 70.4 / 6 / 0, 0 | 78.5 / 90 / 20, 0 | 52.2 / 0 / 0, 0 | 82.8 / 241 / 20, 0 | 79.9 / 142 / 20, 0 | 52.2 / 0 / 0, 0 |
| Y-junction (R2) | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 | 40.0 / 0 / 0, 0 |
| backward step (example) | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 | 0.0 / 0 / 0, 0 |
| U-bend (R2) | 43.3 / 0 / 0, 0 | 42.6 / 0 / 0, 0 | 42.6 / 0 / 0, 0 | 42.6 / 0 / 0, 0 | 43.3 / 0 / 0, 0 | 42.6 / 0 / 0, 0 | 42.6 / 0 / 0, 0 | 42.6 / 0 / 0, 0 |
| U-bend (R1) | 21.7 / 0 / 0, 0 | 20.9 / 0 / 0, 0 | 20.9 / 0 / 0, 0 | 20.8 / 0 / 0, 0 | 21.7 / 0 / 0, 0 | 21.1 / 0 / 0, 0 | 21.0 / 0 / 0, 0 | 20.9 / 0 / 0, 0 |
| cylinder in a channel (R2) | 38.7 / 0 / 0, 0 | 35.2 / 0 / 0, 0 | 35.2 / 0 / 0, 0 | 35.2 / 0 / 0, 0 | 38.5 / 0 / 0, 0 | 35.5 / 0 / 0, 0 | 35.2 / 0 / 0, 0 | 35.2 / 0 / 0, 0 |
| cylinder in a channel (R1) | 38.7 / 0 / 0, 0 | 35.6 / 0 / 0, 0 | 35.6 / 0 / 0, 0 | 35.6 / 0 / 0, 0 | 38.6 / 0 / 0, 0 | 35.7 / 0 / 0, 0 | 35.6 / 0 / 0, 0 | 35.6 / 0 / 0, 0 |
| nozzle (R2) | 12.3 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 | 11.5 / 0 / 0, 0 |
| cascade (R2) | 66.4 / 0 / 2, 0 | 68.5 / 0 / 0, 0 | 59.3 / 0 / 0, 0 | 60.8 / 0 / 0, 0 | 61.0 / 0 / 0, 0 | 64.2 / 0 / 0, 0 | 63.0 / 0 / 0, 0 | 62.3 / 0 / 0, 0 |
| cascade (R1) | 62.4 / 0 / 4, 2 | 60.7 / 0 / 6, 1 | 60.7 / 0 / 4, 1 | 61.9 / 0 / 3, 0 | 64.2 / 0 / 4, 3 | 62.4 / 0 / 1, 0 | 61.4 / 0 / 3, 0 | 62.1 / 0 / 3, 0 |
| NACA 0012 (example) | 53.9 / 0 / 1, 0 | 48.6 / 0 / 4, 0 | 48.9 / 0 / 5, 0 | 49.6 / 0 / 6, 0 | 49.0 / 0 / 6, 1 | 50.5 / 0 / 4, 0 | 49.5 / 0 / 5, 0 | 48.9 / 0 / 4, 0 |
| blunt plate (R2) | 41.6 / 0 / 0, 0 | 36.5 / 0 / 0, 0 | 36.5 / 0 / 0, 0 | 36.5 / 0 / 0, 0 | 45.9 / 0 / 0, 0 | 41.1 / 0 / 0, 0 | 36.5 / 0 / 0, 0 | 36.5 / 0 / 0, 0 |
| blunt plate (R1) | 46.7 / 0 / 0, 0 | 29.5 / 0 / 8, 4 | 29.5 / 0 / 8, 4 | 29.5 / 0 / 8, 4 | 45.7 / 0 / 12, 3 | 29.8 / 0 / 8, 6 | 29.5 / 0 / 8, 4 | 29.5 / 0 / 8, 4 |
| three cylinders (engine) | 68.1 / 0 / 17, 2 | 64.8 / 0 / 46, 12 | 64.8 / 0 / 30, 2 | 66.4 / 0 / 8, 0 | 64.4 / 0 / 38, 6 | 67.7 / 0 / 5, 0 | 67.3 / 0 / 13, 0 | 64.7 / 0 / 24, 2 |
| grooved tandem (example) | 21.8 / 0 / 0, 0 | 20.5 / 0 / 4, 0 | 20.5 / 0 / 4, 0 | 22.2 / 0 / 4, 0 | 35.6 / 0 / 22, 0 | 22.2 / 0 / 0, 0 | 20.9 / 0 / 4, 0 | 20.5 / 0 / 4, 0 |
| airfoil in a tunnel (R2) | 36.0 / 0 / 0, 0 | 30.9 / 0 / 0, 0 | 29.2 / 0 / 2, 0 | 28.5 / 0 / 0, 0 | 45.3 / 0 / 0, 0 | 25.5 / 0 / 0, 0 | 34.3 / 0 / 0, 0 | 26.5 / 0 / 0, 0 |
| NACA 4412 (engine, R2) | 68.1 / 0 / 2, 2 | 68.6 / 0 / 21, 0 | - | - | - | - | 61.5 / 0 / 4, 0 | 72.1 / 1 / 11, 0 |
| bluff body on ground (R2) | 45.1 / 0 / 4, 0 | 44.2 / 0 / 18, 0 | - | - | - | - | 44.2 / 0 / 15, 0 | 44.2 / 0 / 16, 0 |
| high-lift (R2 hand) | 175.9 / 2387 / 153, 45 | 175.9 / 2387 / 153, 45 | - | - | - | - | 175.9 / 2387 / 153, 45 | 175.9 / 2387 / 153, 45 |
| high-lift (R1 hand) | 62.2 / 0 / 82, 59 | 61.4 / 0 / 25, 5 | - | - | - | - | 61.4 / 0 / 23, 3 | 61.2 / 0 / 25, 6 |

Totals over the 20 sessions (the default against no shape step, pair 2.5,
spread 2.5): cells 1.80M against 1.64M, 1.82M, 1.79M; jumps above 2.5 285
against 285, 248, 248; above 5 67 against 116, 52, 57 (45 of them the
folded high-lift session's in each); faces above 70 2,387 (all the
high-lift session's) against 2,819, 2,529, 2,388. On the 16 sessions every
rule ran: above 2.5 68 (default), 53 (floor 0.3), 49 (0.5), 82
(`keep_lines`), 38 (pair 1.5), 53 (pair 2.5), 43 (spread 2.5).

None replaces the default. A new default had to keep every session's
faces above 70 and jumps above 5 from rising, lower the total of jumps
above 2.5, raise no maximum by more than 2 degrees and keep the square
cylinder's gain within 2 degrees. The floors 0.3 and 0.5 and both pair
limits lose the square cylinder (70.4 to 82.8 degrees, 6 to 241 faces
above 70): its gain is its corner kites' diagonals shrinking to a tenth
against the spokes they continue, a pair ratio changed 19 times that
`size` grades away (worst jump 2.05). Pair 1.5 also adds two jumps above
5 on the round-1 blunt plate and raises three maxima by 2.8 to 4.7
degrees; pair 2.5 raises the three cylinders and the airfoil in a tunnel
by 2.4 and 3.3. `keep_lines` raises seven maxima by 3 to 16 degrees and
adds jumps above 5 on two. The spread limit keeps the square cylinder
exactly (its spreads fall) and passes on the 16 smaller sessions, but on
the NACA 4412 engine session its maximum rises 68.6 -> 72.1 with a face
above 70, and the round-1 high-lift gains one jump above 5 (5 -> 6).
Where the shape step leaves a jump above 5, the pair through it had its
length ratio changed 3.8 to 33 times (all twelve of the three cylinders,
the round-1 cascade's, three of the round-1 high-lift's five), but of the 72 pairs the default
changed more than 5 times only 9 end above 5: the pair ratio marks a risk
that `size` usually grades away, and the round-1 blunt plate's two worst
jumps (10.0) sit on pairs the step did not change at all. Use either
limit where the floor is what an agent would raise (a ring pulled onto the
next ring: the three cylinders go from 12 jumps above 5 to none at pair
1.5 or 2.5, and to 2 at spread 2.5).

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
70 degrees is repaired first, judged at 256 cells a side, and so is every
block above checkMesh's cell aspect ratio limit of 1000 (in the plane; the
z depth is the export settings'): a strip's one shared ratio carries a
wall's first width along all its members, out to where the field asks for
cells hundreds of times longer, so the repair releases a strip through the
block - its members go all, half or a quarter of the way to their own
series (`grading=field`'s, see below), bounded by `interface_growth` times
the widths across each interface - when that leaves no interface jump at
its ends above both 2.5 and what it was, no fold, no new face above 70 and
the worst face within 20 degrees; only when no such free move helps does it
raise a count. Each move is
checked on the sampled grid (256 cells a side) of the blocks it touches and
undone if it folds a cell, adds a face above 70 degrees, raises the worst
face past the field's, takes checkMesh's skewness past 4 or takes the
aspect ratio past 1000 (or, above it, adds cells there); blocks left above
1000 are named in the note and in `verification`'s `high_aspect_blocks`.
When the input at its own counts folds nothing, a block the field state
folds is regraded first, a strip through it a quarter of the way to uniform
or further (past the field's bound at an end if need be), and a result that
still folds is refused: the refusal names the blocks and the spline edges
among theirs that run backwards along their chord, with the control point
to remove. Such an edge is the usual cause - OpenFOAM's Catmull-Rom spline
loops in a segment much shorter than its neighbour, and nodes graded into
the loop come out of order. The round-2 high-lift build has 49 such
edges among the 202 splines of its rings and band fronts (on the main
element a last segment 0.0014 long beside 0.0165); its block map folded at
even spacing only from 32 cells a side, at its input counts not at all, and
the default sizing graded four blocks into the loops (46 folded cells,
negative volumes in blockMesh). Four regrades towards uniform now remove
them all.
On the round-2 blunt plate (whose default sizing failed checkMesh at
1846, 1018 after the polish) two releases of its wake strips took the
field state to 559 and the polished result to 585, with 79,194 cells
against 94,784 and no jump above 2.5; no other session of the 20-session
corpus changed. The result is
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
keeps every count and spends with a budget of the cells there are, so the
strips are regraded for continuity but no count is raised (it used to
recompute the gradings and drop the regrades: three cylinders after its
polish went from a worst jump of 6.15 to 23.5, 30 jumps above 5; now 6.53
and 13). The report gives the cells, the field's own
cells and jump cost against the result's, whether the budget stopped the
spending (`budget_binding`; false when no move that lowers the jumps was
left or the guard refused them), the interface jumps above 2.5, 5 and 20
and the worst ones, the first cell heights normal to each wall patch at
its band spokes against the one asked, with the two spokes of the extremes
named (`bands`; `quality`'s `first_widths` measure, see above), the moves, the guard's refusals, the splits and
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

`grading=field` (the default; `grading=strip` leaves the pass out) addresses two benchmark findings: the
first width of a wall spoke drifted from the one asked (within a strip
every spoke's first width over its length was the same, so short spokes
came out finer: -16 percent on the NACA 4412, -28 on the U-bend), and
wall-fine spacing was carried to the far field (the NACA 4412 C-grid's
wake strip put the trailing edge's 0.00036 at the far-field corner, whose
bound was 0.5). Both come from one total ratio per strip. After spending, a
last pass gives each strip member at its count the series that keeps its
finer-bounded end at its bound - a wall spoke starts at the first width
asked, a far member relaxes - with bounds lowered to `interface_growth`
times the widths across each interface, opposite sides clamped to 20
degrees of tilt, and each strip taken all, half or a quarter of the way
from its spent ratio only where the worst interface jump it touches stays
within the spent one (or 2.5) and the sampled grid gains no fold, no face
above 70, no skewness past 4 and no worst face above the lower of 20
degrees and the spent state's worst that was not there. Counts do not
change. Measured (each session's shaped state, its own `size` line,
`smooth`, `check`; the 11 sessions of the skill examples and benchmark
round 1; cells are equal; no face above 70 in either):

| session | max non-orth | avg | skew | checkMesh aspect | worst jump (>2.5/5/20) | first widths reported |
|---|---|---|---|---|---|---|
| backward step | 0.00 -> 0.00 | 0.00 -> 0.00 | 0.00 -> 0.00 | 31 -> 31 | 1.07 (0/0/0) -> 1.04 (0/0/0) | -2..-2% -> -2..+0% |
| grooved tandem | 20.51 -> 20.67 | 4.70 -> 5.93 | 0.77 -> 0.80 | 43 -> 33 | 3.42 (4/0/0) -> 3.42 (4/0/0) | -50..-1% -> -24..+0% |
| NACA 0012 | 48.60 -> 48.27 | 7.78 -> 8.84 | 2.15 -> 2.12 | 54 -> 54 | 4.36 (4/0/0) -> 4.36 (2/0/0) | -17..+346% -> -13..+344% |
| cylinder channel | 35.62 -> 35.21 | 3.08 -> 3.04 | 0.84 -> 0.83 | 25 -> 21 | 1.84 (0/0/0) -> 2.01 (0/0/0) | -15..-0% -> -0..+0% |
| U-bend | 20.88 -> 20.57 | 2.84 -> 3.00 | 0.80 -> 0.93 | 36 -> 26 | 1.97 (0/0/0) -> 2.50 (0/0/0) | -28..-4% -> -0..+0% |
| Y-junction | 39.96 -> 39.97 | 9.21 -> 13.34 | 1.14 -> 1.13 | 37 -> 29 | 1.55 (0/0/0) -> 1.69 (0/0/0) | -19..+18% -> -10..+30% |
| blunt plate | 29.53 -> 25.58 | 7.87 -> 6.99 | 0.96 -> 0.62 | 468 -> 504 | 10.01 (8/4/0) -> 5.11 (4/2/0) | -13..-4% -> -13..-3% |
| cascade | 60.70 -> 60.68 | 27.23 -> 27.49 | 3.24 -> 3.27 | 29 -> 29 | 7.98 (6/1/0) -> 7.47 (7/1/0) | -16..+68% -> -16..+238% |
| three cylinders | 64.83 -> 64.76 | 18.25 -> 18.07 | 3.97 -> 3.96 | 26 -> 23 | 6.15 (46/12/0) -> 5.75 (16/4/0) | -19..-0% -> -0..+0% |
| NACA 4412 | 69.85 -> 30.98 | 6.48 -> 7.29 | 0.87 -> 0.86 | 80 -> 67 | 3.83 (21/0/0) -> 3.04 (4/0/0) | -16..+254% -> -0..+256% |
| high-lift | 61.41 -> 61.80 | 12.17 -> 11.57 | 3.84 -> 3.65 | 112 -> 95 | 6.54 (25/5/0) -> 5.78 (28/1/0) | -54..+285% -> -43..+385% |

It became the default after a re-measurement with the aspect-ratio and
fold repairs in place, by a rule fixed beforehand: on the 20-session
corpus (the eleven round-2 trials, five round-1 builds, the three skill
examples, the engine's square cylinder; each through the default shape
step, `size` with its own line, the polish) no session may gain a face
above 70, a jump above 5 or a checkMesh failure, the corpus total of jumps
above 2.5 may not rise, no maximum may rise more than 2 degrees, no
session's cells more than 5 percent, no wall patch's worst first-height
deviation (normal to the wall at its band spokes, against the width asked)
more than 5 points, and the corpus mean of that deviation must fall. Every
term held: jumps above 2.5 257 -> 177, mean worst deviation 20.4 -> 12.7
percent over 44 patches, largest maximum rise 1.15 degrees (bluff ground),
cells equal (counts do not change), the one checkMesh failure (round-2
high-lift, skewness 4.07 -> 4.05 in a far-field block) on both sides.
Sampled verdict after the polish, default `strip` -> `field`:

| session | cells | >70 | max (sampled) | jumps >2.5 | >5 | worst first-height deviation, % | checkMesh |
|---|---|---|---|---|---|---|---|
| eng_square_cylinder | 5,184 | 0 -> 0 | 52.16 -> 52.16 | 0 -> 0 | 0 -> 0 | 57 -> 57 (sq) | OK |
| r2_y_junction | 40,828 | 0 -> 0 | 39.97 -> 39.99 | 0 -> 0 | 0 -> 0 | 11 -> 5 (bottom); 18 -> 5 (branch_left); 18 -> 5 (branch_right); 6 -> 5 (top_downstream); 6 -> 5 (top_upstream) | OK |
| ex_backward_step | 21,750 | 0 -> 0 | 0.00 -> 0.00 | 0 -> 0 | 0 -> 0 | 2 -> 0 (lower_wall_downstream); 2 -> 2 (lower_wall_upstream); 2 -> 2 (step); 2 -> 0 (upper_wall) | OK |
| r2_u_bend | 22,700 | 0 -> 0 | 42.61 -> 42.29 | 0 -> 0 | 0 -> 0 | 14 -> 5 (divider); 13 -> 5 (outer_wall) | OK |
| r1_u_bend | 24,010 | 0 -> 0 | 20.88 -> 20.57 | 0 -> 0 | 0 -> 0 | 13 -> 5 (divider); 32 -> 5 (outer_wall) | OK |
| r2_cylinder_channel | 17,248 | 0 -> 0 | 35.22 -> 34.96 | 0 -> 0 | 0 -> 0 | 12 -> 5 (bottom); 14 -> 5 (cylinder); 25 -> 5 (top) | OK |
| r1_cylinder_channel | 21,824 | 0 -> 0 | 35.62 -> 35.21 | 0 -> 0 | 0 -> 0 | 15 -> 5 (bottom); 14 -> 5 (cylinder); 19 -> 5 (top) | OK |
| r2_nozzle | 14,620 | 0 -> 0 | 11.48 -> 12.01 | 0 -> 0 | 0 -> 0 | 14 -> 5 (lower_wall); 14 -> 5 (upper_wall) | OK |
| r2_cascade | 50,778 | 0 -> 0 | 68.50 -> 60.57 | 0 -> 0 | 0 -> 0 | 15 -> 16 (blade) | OK |
| r1_cascade | 55,417 | 0 -> 0 | 60.70 -> 60.68 | 6 -> 7 | 1 -> 1 | 38 -> 38 (blade) | OK |
| ex_naca0012 | 59,908 | 0 -> 0 | 48.60 -> 48.27 | 4 -> 2 | 0 -> 0 | 21 -> 15 (airfoil) | OK |
| r2_blunt_plate | 79,194 | 0 -> 0 | 36.00 -> 30.93 | 0 -> 0 | 0 -> 0 | 17 -> 13 (plate) | OK |
| r1_blunt_plate | 88,942 | 0 -> 0 | 29.53 -> 25.58 | 8 -> 4 | 4 -> 0 | 16 -> 13 (plate) | OK |
| r2_three_cylinders | 56,069 | 0 -> 0 | 64.83 -> 64.77 | 46 -> 16 | 10 -> 4 | 23 -> 5 (cylinder_a); 15 -> 5 (cylinder_b); 14 -> 5 (cylinder_c) | OK |
| ex_grooved_tandem | 47,616 | 0 -> 0 | 20.51 -> 20.67 | 4 -> 4 | 0 -> 0 | 50 -> 24 (cyl1); 49 -> 24 (cyl2) | OK |
| r2_airfoil_tunnel | 113,164 | 0 -> 0 | 30.95 -> 27.40 | 0 -> 0 | 0 -> 0 | 18 -> 5 (airfoil); 14 -> 5 (ceiling); 14 -> 5 (floor) | OK |
| r2_naca4412 | 205,506 | 0 -> 0 | 68.60 -> 45.12 | 21 -> 8 | 0 -> 0 | 17 -> 5 (airfoil) | OK |
| r2_bluff_ground | 235,582 | 0 -> 0 | 44.24 -> 45.39 | 18 -> 12 | 0 -> 0 | 29 -> 29 (body); 14 -> 14 (ground) | OK |
| r2_high_lift | 292,143 | 1807 -> 1391 | 79.91 -> 77.26 | 125 -> 96 | 37 -> 29 | 84 -> 84 (flap); 12 -> 12 (main); 12 -> 11 (slat) | fails (skewness) |
| r1_high_lift | 331,921 | 0 -> 0 | 61.41 -> 61.80 | 25 -> 28 | 5 -> 1 | 15 -> 8 (flap); 56 -> 46 (main); 29 -> 29 (slat) | OK |

It is not the default because it failed the rule set for that (on every
session: no new face above 70, skewness below 4, max non-orthogonality
within 1 degree, worst jump not above the larger of the old and 2.5; and
where the gaps were reported a tighter first-width range and a lower
aspect ratio or cell count) on two sessions. On the blunt plate the band
strip, which runs on into the wake lines behind the base, put 76, 59 and
33 degrees on the wake block at the three steps and was refused, so its
spokes keep their drift and the aspect ratio rose. On the cascade a
trailing-edge spoke leaving the wall obliquely takes the size the
directional field asks along it (0.000676 where the normal spokes have
0.0002), which widens the reported range although the near-normal spokes
are unchanged. The first widths in this table were measured along every
edge leaving the wall (wake lines included); `size` and `quality` now
measure the height normal to the wall at band spokes only. The polish
moves them by up to 5 percent (band spokes within 5 percent of their
length). The pass added 1 to 40 seconds to `size` on these sessions (40
on the high-lift's 241).

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
four worked example batches (`examples/`) sit beside it. It is kept in step
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
