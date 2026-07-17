# BlockDrawer

BlockDrawer is a small graphical editor for structured, straight-edged 2D block
topologies. It exports an OpenFOAM `blockMeshDict`; OpenFOAM's `blockMesh` does the
actual meshing.

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
- Click an edge to edit its number of cells. The small edge markers are the
  uniform subdivision locations. Opposite edges in every affected block are
  updated automatically, including transitive constraints through shared edges.
- Select an exterior edge and press **Add block**, or double-click that edge. The
  new block extrudes along that edge's outward normal, using the source block's
  average perpendicular thickness. Its new exterior edge remains selected, making
  a row of blocks quick to create even when the source block is skewed.
- Press `N` to create a block from four existing vertices. Click the vertices in
  any order; staged vertices are shown in purple and numbered, and the fourth
  valid selection creates the block immediately. Click a staged vertex again to
  deselect it, or press `Esc` to cancel the mode. The four vertices must form a
  convex, consistently oriented block topology.
- Use the mouse wheel to zoom, middle/right-drag to pan, and **Fit view** to frame
  the complete topology.
- Set z cells and extrusion extents in the global properties on the right.
- Use **Edit → Undo/Redo**, the toolbar buttons, or `Ctrl+Z`/`Ctrl+Y`. A complete
  vertex drag is one undo action, as is a propagated edge-count edit. On macOS,
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
(automatic)** restores the detected value.

BlockDrawer rejects vertex moves that would invert or collapse a block. Curved
edges, grading, named boundaries, and 3D editing are deliberately outside the
current scope.

## Save and export

**File → Save** writes a versioned BlockDrawer JSON session containing the shared
vertices, quadrilateral blocks, edge cell counts, and extrusion settings.

**Export blockMeshDict** writes an OpenFOAM dictionary with:

- two z planes for every drawn vertex;
- one `hex` per quadrilateral;
- the propagated x/y counts and global z count;
- straight edges and `simpleGrading (1 1 1)`;
- an empty `boundary` list, allowing `blockMesh` to create its default outer patch.

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

An optional integration test asks a real OpenFOAM installation to parse and mesh a
generated multi-block dictionary:

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
