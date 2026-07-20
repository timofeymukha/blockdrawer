# BlockDrawer

BlockDrawer is a small graphical editor for structured 2D block topologies with
straight, circular-arc, piecewise-linear, or spline edges. It exports an OpenFOAM
`blockMeshDict`; OpenFOAM's `blockMesh` does the actual meshing.

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
  point equidistantly along the straight line between the edge vertices.
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
- Use the mouse wheel to zoom, middle/right-drag to pan, and **Fit view** to frame
  the complete topology.
- Set z cells and extrusion extents in the global properties on the right.
- Use **Edit → Undo/Redo**, the toolbar buttons, or `Ctrl+Z`/`Ctrl+Y`. A complete
  vertex or interpolation-point drag is one undo action, as is an edge-type,
  point-list, propagated grading, or propagated edge-count edit. On macOS,
  `Command+Z` and `Command+Shift+Z` are also available.
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
(automatic)** restores the detected value. The properties sidebar scrolls when
its controls exceed the available window height.

BlockDrawer rejects vertex moves that would invert or collapse a block, make an
arc point collinear with its endpoints, or collapse adjacent point-list entries.
Poly-spline edges, multi-section grading, front/back boundary selection, advanced
coupled-patch transforms, and 3D editing are deliberately outside the current
scope. The unselected front/back faces continue to use OpenFOAM's default patch.

## Save and export

**File → Save** writes a versioned BlockDrawer JSON session containing all
vertices (including standalone ones), quadrilateral blocks, edge cell counts,
optional edge geometry, per-edge grading, boundary definitions and assignments,
and extrusion settings. Format version 4 stores named boundaries and their colors;
version 3 added non-uniform total expansion ratios, and version 2 introduced
interpolation points. Versions 1–3 remain loadable through explicit migrations.

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
