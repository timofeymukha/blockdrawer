# BlockDrawer contributor guide

## Product boundary

BlockDrawer is a graphical editor for straight-edged, 2D block topologies that are
extruded into OpenFOAM hexahedra. It does **not** generate a mesh. Its primary
artifact is a valid `system/blockMeshDict`; OpenFOAM's `blockMesh` remains the
mesher.

The current scope intentionally excludes curved edges, grading, named boundary
patches, and 3D editing. Preserve extension points for those features instead of
encoding them in the canvas widgets.

## Stack and commands

- Python 3.10+.
- Tkinter/ttk for the cross-platform GUI (part of the normal Python installer).
- Python standard library only at runtime and in tests; no compiled dependencies.
- Launch: `python -m blockdrawer`
- Tests: `python -m unittest discover -s tests -v`
- Supplied OpenFOAM 2606 integration check: `make integration-test`
- For another installation, set `BLOCKMESH_COMMAND` to a command prefix that can
  accept `-case <path>`, then run the test suite.

## Architecture

- `blockdrawer/model.py`: authoritative topology and validation. Vertices are
  shared objects referenced by counter-clockwise quadrilateral blocks. Edges are
  derived from block vertex pairs, not duplicated as geometry.
- `blockdrawer/session.py`: versioned JSON persistence. Keep this independent of
  Tk so sessions can be tested and converted headlessly.
- `blockdrawer/foam.py`: deterministic `blockMeshDict` serialization. Each 2D
  vertex is emitted at `zMin` and `zMax`; a block becomes one `hex`, with the
  editable edge counts and the global z cell count.
- `blockdrawer/history.py`: bounded, complete-model snapshots for atomic undo/redo.
  Snapshotting is appropriate because one topology operation can update multiple
  constrained edges. A mouse drag is recorded once, on release.
- `blockdrawer/app.py`: Tkinter views and interaction only. It calls model APIs;
  do not put topology propagation or OpenFOAM serialization rules in the GUI.
- `tests/`: model, persistence, and export tests. Tests must not require a display.

## Topology invariants

- A block stores four distinct vertex IDs in counter-clockwise order and must
  remain strictly convex.
- A topological edge is the canonical, unordered pair of its vertex IDs. It is a
  boundary edge when used by one block and internal when used by two.
- Opposite edges of every quadrilateral must have the same cell count. Calling
  `MeshModel.set_edge_cells()` updates the complete transitive constraint
  component, including shared edges in neighboring blocks.
- A new block is appended only across a boundary edge. It shares that edge and
  translates both endpoints by the same outward-normal vector. The distance is the
  source block's average perpendicular thickness at the edge endpoints, producing
  a convex rectangular neighbor even for skewed source blocks. Reuse coincident
  existing vertices when possible.
- `MeshModel.add_block_from_vertices()` accepts four distinct existing vertex IDs
  in any order, sorts them counter-clockwise, requires a strictly convex block,
  and rejects duplicate/non-manifold/misoriented topology. Existing edge cell
  counts take precedence and opposite-edge constraints propagate through the new
  block.
- In the GUI, `N` starts four-vertex block selection and `Esc` cancels it. Staged
  vertices are numbered and clicking one again deselects it. Only successful
  completion enters history; it is one undoable action.
- Removing an edge removes every incident block (one for a boundary edge, two for
  an internal edge) and prunes only vertices/edge data unused by surviving blocks.
  This is one undoable operation.
- A topology always contains at least one block. Reject any edge deletion whose
  complete incident-block set would leave zero blocks (including deleting the
  shared edge when exactly two blocks remain).
- Moving a shared vertex updates every incident block. Invalid/inverted moves are
  rejected and rolled back.
- The z direction is not drawn. `zCells` defaults to 1 and `zMin`/`zMax` default
  to 0/1.

## Data and compatibility

Session files contain a format marker and integer version. Add migrations (or a
clear unsupported-version error) when the shape changes; never silently reinterpret
old data. JSON is a project/session format, not an OpenFOAM format.

`blockMeshDict` output uses straight edges and `simpleGrading (1 1 1)`. An empty
`boundary` list deliberately lets `blockMesh` create its default outer patch until
boundary editing is implemented.

## Working conventions

- Keep model operations deterministic and UI-free.
- Route every new mutation through the application history and add a history test.
- Preserve automatic Tk/OS DPI scaling. Custom canvas dimensions must use the
  application's display scale; manual UI scale is layered on top of system DPI.
- Add or adjust headless tests for every topology or serialization change.
- Do not call OpenFOAM during normal editing; export is cheap and deterministic.
- Before changing vertex ordering, verify both block orientation and OpenFOAM hex
  ordering with the integration test.
- Update this file and the README when scope, stack, commands, or file formats
  change so a future agent can resume quickly.
