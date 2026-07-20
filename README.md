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

## Edit a topology

- Click and drag a blue vertex to move every block that shares it. Coordinates can
  also be entered exactly in the right-hand panel.
- Press `V` or click **Add vertex**, then click the canvas to create one standalone
  vertex. Press `Esc` to cancel placement. Standalone vertices can be moved and
  edited like block corners, and selected with `N` to complete a missing block.
- Click an edge to edit its number of cells. The small edge markers are the
  graded subdivision locations. Opposite edges in every affected block receive
  the same cell count automatically, including transitive constraints through
  shared edges.
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
  skewed.
- Press `N` to create a block from four existing vertices. Click the vertices in
  any order; staged vertices are shown in purple and numbered, and the fourth
  valid selection creates the block immediately. Click a staged vertex again to
  deselect it, or press `Esc` to cancel the mode. The four vertices must form a
  convex, consistently oriented block topology.
- Press `B` or click **Set boundaries** to switch the right-hand panel to boundary
  editing. Add a named patch, select it in the list, then click exterior edges to
  assign them; clicking an edge already assigned to the active patch unassigns it,
  and clicking an edge with another color reassigns it. Internal edges are not
  selectable in this mode. Every patch receives a persistent unique color that is
  also shown on its assigned edges. Press `B` or `Esc`, or click **Done
  boundaries**, to return to normal editing.
- Boundary types are `patch`, `wall`, `symmetry`, `empty`, and `cyclic`. For a
  cyclic boundary, choose its neighbouring patch and apply the type. BlockDrawer
  configures the reciprocal pairing in one undoable operation; changing or
  removing one member returns its former partner to `patch`. Ordinary cyclic
  translation or rotation is inferred by OpenFOAM from the matching patches.
  Export checks that cyclic partners both have edges with matching lengths and
  subdivisions.
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
- Set z cells and extrusion extents in the global properties on the right.
- Press `Enter` or keypad `Enter` in an editable property field to apply the
  corresponding values, just like its **Set** or **Apply** button.
- Press `Ctrl+A` in a text field to select its complete contents (`Cmd+A` also
  works on macOS).
- Use **Edit → Undo/Redo**, the toolbar buttons, or the configured shortcuts. The
  defaults are `Ctrl+Z`/`Ctrl+Y` on Linux and Windows, and
  `Command+Z`/`Command+Shift+Z` on macOS. A complete vertex or interpolation-point
  drag is one undo action, as is an edge-type, point-list, propagated grading, or
  propagated edge-count edit.
- Select an edge and press `Delete`, `Backspace`, or `X` to remove that edge and
  every incident block. The selection panel also shows an explicit delete button
  and the number of blocks that will be removed. Deletion is one undoable action;
  unrelated blocks are preserved. A deletion that would leave no blocks is
  disabled, so the final block can never be removed.

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
Poly-spline edges, multi-section grading, front/back boundary selection, advanced
coupled-patch transforms, and 3D editing are deliberately outside the current
scope. The unselected front/back faces continue to use OpenFOAM's default patch.

## Preferences and shortcuts

BlockDrawer creates a human-editable JSON preferences file on first launch:

- Linux: `~/.blockdrawer`
- macOS: `~/.blockdrawer`
- Windows: `%APPDATA%\BlockDrawer\config.json`

Mesh geometry and extrusion values remain in the session JSON; this preferences
file is for application-wide behavior. `ui.scale` accepts `"auto"` or a multiplier
from `0.5` through `4`. `ui.showBlockMesh`, `ui.showGeometry`,
`ui.showEdgeNodes`, and `ui.showEdgeInterpolationPoints` are booleans for the
corresponding visibility toggles. Changes made through **View** update the file
immediately. Manual edits are loaded on the next launch.

Every keyboard action is configurable. A Linux/Windows default file looks like:

```json
{
  "format": "blockDrawerConfig",
  "version": 1,
  "ui": {
    "scale": "auto",
    "showBlockMesh": true,
    "showGeometry": true,
    "showEdgeNodes": true,
    "showEdgeInterpolationPoints": true
  },
  "shortcuts": {
    "new_session": ["Ctrl+N"],
    "open_session": ["Ctrl+O"],
    "save_session": ["Ctrl+S"],
    "save_session_as": ["Ctrl+Shift+S"],
    "export_block_mesh_dict": ["Ctrl+E"],
    "undo": ["Ctrl+Z"],
    "redo": ["Ctrl+Y", "Ctrl+Shift+Z"],
    "delete_edge": ["Delete", "Backspace", "NumpadDelete", "X"],
    "new_block": ["N"],
    "add_vertex": ["V"],
    "set_boundaries": ["B"],
    "project": ["P"],
    "toggle_geometry": ["G"],
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

**File → Save** writes a versioned BlockDrawer JSON session containing all
vertices (including standalone ones), quadrilateral blocks, edge cell counts,
optional edge geometry, per-edge grading, boundary definitions and assignments,
reference-geometry curves, and extrusion settings. Format version 5 adds named
reference curves, their ordered point lists, and their point-marker visibility;
version 4 stores named boundaries and their colors, version 3 added non-uniform
total expansion ratios, and version 2 introduced interpolation points. Versions
1–4 remain loadable through explicit migrations.

**Export blockMeshDict** writes an OpenFOAM dictionary with:

- two z planes for every drawn vertex, including standalone vertices not yet used
  by a block;
- one `hex` per quadrilateral;
- the propagated x/y counts and global z count;
- matching lower/upper `arc`, `polyLine`, or `spline` definitions for each
  non-straight 2D edge;
- implicit straight edges and a 12-value `edgeGrading` entry on every block;
- one four-vertex extruded side face for every assigned 2D boundary edge, grouped
  into the configured OpenFOAM patches; and
- OpenFOAM's default patch for unassigned side faces and the front/back faces.

Reference-geometry curves are intentionally omitted from `blockMeshDict`; they
are editing guides and projection targets rather than exported mesh entities.

Copy or save it as `system/blockMeshDict` in an OpenFOAM case, then run:

```bash
blockMesh
```

The UI never invokes `blockMesh` and never writes `polyMesh`; it only previews the
block topology and its edge subdivisions.

## Tests

All normal tests are headless and use the standard library:

```bash
python -m unittest discover -s tests -v
```

An optional integration test asks a real OpenFOAM installation to parse and mesh
generated multi-block, graded, circular-arc, polyline, spline, named-patch, and
cyclic dictionaries:

```bash
BLOCKMESH_COMMAND='apptainer exec /path/to/openfoam.sif blockMesh' \
  python -m unittest discover -s tests -v
```

For the OpenFOAM 2606 image supplied alongside this repository, its environment
setup is already captured by:

```bash
make integration-test
```

See [AGENTS.md](AGENTS.md) for the architecture, invariants, and contributor resume
guide.
