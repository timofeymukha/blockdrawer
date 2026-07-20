# BlockDrawer contributor guide

## Product boundary

BlockDrawer is a graphical editor for 2D block topologies with straight,
circular-arc, piecewise-linear, or spline edges that are extruded into OpenFOAM
hexahedra. It does **not** generate a mesh. Its primary artifact is a valid
`system/blockMeshDict`; OpenFOAM's `blockMesh` remains the mesher.

The current scope intentionally excludes polySpline edges, multi-section grading,
cyclicAMI/ACMI transforms, front/back patch editing, and 3D editing. Preserve
extension points for those features instead of encoding them in the canvas widgets.

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

- `blockdrawer/model.py`: authoritative topology and validation. Vertices may be
  standalone or shared objects referenced by counter-clockwise quadrilateral
  blocks. Edges are derived only from block vertex pairs. Optional `EdgeGeometry`
  is keyed by that shared edge and stores a type plus ordered interpolation points.
  Non-uniform total expansion ratios are stored in `edge_grading`, also keyed by
  shared edge; uniform ratio 1 is implicit. Ordered `Boundary` definitions are
  separate from the zero-or-one `edge_boundaries` assignment per shared edge.
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
  back into `patch`. Boundary mode (`B`) assigns or reassigns an edge to the
  active patch and toggles it off when clicked again. Internal edges are
  unavailable in this mode.
- Straight (`line`) geometry is implicit. An `arc` stores exactly one finite,
  non-collinear interpolation point. A `polyLine` or `spline` stores one or
  more ordered, finite interpolation points, and no adjacent path points may
  coincide. Geometry is shared by every block incident to the topological edge.
- `MeshModel.edge_point()` evaluates lines and arcs geometrically. For polyLines,
  its parameter is cumulative path length. Splines use OpenFOAM's
  through-point Catmull-Rom interpolation with chord-length segment parameters.
- Selecting any curved type creates a deterministic point offset outward from the
  first incident block. GUI points are purple; point-list types are numbered in
  their canonical edge-path order. Points can be selected, moved, inserted, and
  removed while retaining at least one. Reset preserves the current point
  count and distributes the points at equal fractions of the straight vertex-to-
  vertex chord. Each button/property mutation is one history action; a complete
  point drag is recorded once on mouse release.
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
  graded fractions. A persistent Propagate checkbox sweeps the same transitive
  edge component as `set_edge_cells()`, reversing canonical ratios where needed
  so the grading follows one physical block direction. Each Set action is one
  history entry.
- A new block is appended only across a boundary edge. It shares that edge and
  translates both endpoints by the same outward-normal vector. The distance is the
  source block's average perpendicular thickness at the edge endpoints, producing
  a convex rectangular neighbor even for skewed source blocks. Reuse coincident
  existing vertices when possible. A genuinely new opposite edge inherits the
  source edge grading with corresponding endpoints and physical direction; an
  already-existing opposite edge keeps its own grading.
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
  to 0/1.

## Data and compatibility

Session files contain a format marker and integer version. Version 2 adds an
`edgeGeometry` array whose entries contain `vertices`, `type`, and an ordered
`points` array. Version 3 adds an `edgeGrading` array containing canonical vertex
pairs and non-uniform `expansionRatio` values. Version 4 adds ordered `boundaries`
and `edgeBoundaries` arrays. Version 1 straight-edge sessions migrate with empty
geometry; versions 1 and 2 migrate with uniform grading; versions 1–3 migrate
with no named boundaries. Add
migrations (or a clear unsupported-version error) when the shape changes; never
silently reinterpret old data. JSON is a project/session format, not an OpenFOAM
format.

Every 2D vertex, including a standalone one, is emitted at both z planes. Each
non-straight 2D edge is emitted twice: `arc` uses its single point and
`polyLine`/`spline` use ordered point lists, with matching x/y at `zMin` and
`zMax`. Shared edges are emitted only once per z plane. Straight edges remain
implicit. Every block uses the 12-value `edgeGrading` form: the four 2D edge
ratios are duplicated on the lower/upper z planes and all four z edges remain
uniform. Each assigned 2D exterior edge exports as its extruded four-vertex side
face under the named patch. Unassigned side faces and all front/back faces remain
in OpenFOAM's default patch.

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
