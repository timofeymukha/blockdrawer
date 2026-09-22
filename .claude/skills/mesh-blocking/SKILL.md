---
name: mesh-blocking
description: How to build, judge and fix a 2D block-structured mesh with BlockDrawer for OpenFOAM blockMesh or a spectral-element solver - choosing O, C or H topology, wall bands and far field, sizing and grading, and the validation ladder ending in checkMesh. Use whenever asked to mesh a geometry, improve or repair a block topology, or review a session.
---

# Mesh blocking with BlockDrawer

You are producing a **block topology**, not a mesh of cells: a set of strictly
convex quadrilateral blocks whose opposite edges share cell counts, whose
boundary edges carry named patches, and whose curved edges follow the supplied
geometry exactly. The session is a JSON file; `blockdrawer-cli` and the
`blockdrawer` MCP tools edit it, and `blockMeshDict` is exported from it.
Get the topology right first; counts and grading are a later, cheaper stage.

Everything below is the methodology. Tool mechanics are in the MCP tool
descriptions and `blockdrawer-cli commands`. **This skill is kept in step with
the topology engine in `experiments/agentic_topology/`: when a template, an
acceptance term or an option changes there, update the decision points here.**

## 1. Collect before drawing

- **Geometry**: one closed point list per solid body (`x y` per line), or a
  CAD file assembled into closed contours. Orientation does not matter; the
  tools normalise it. Keep the supplied points; never resample geometry from a
  picture or a raster.
- **Physics**: Reynolds number and the target first-point $y^+$; Mach and angle
  of attack are solver inputs - keep the chord aligned and impose incidence
  through the free-stream velocity. Far-field distance: at least 30 chords for
  external aerodynamics; a few body sizes is enough for a topology test.
- **Boundaries and roles**: which chains are `wall`, `inlet`, `outlet`,
  `farfield`, `symmetry`, `cyclic`; their names. A cyclic pair needs identical
  point distributions related by one translation.
- **Solver**: OpenFOAM finite volume, or a spectral-element code where every
  block cell is a curved element of order $p$ - then counts are small, block
  curvature must be exact, and quality is judged per element.
- **Span**: pseudo-2D (`z_min_patch_type=empty z_max_patch_type=empty`, one
  cell in $z$) or a periodic slab (reciprocal `cyclic` spanwise patches,
  `z_cells` from the spanwise resolution).

## 2. Choose the topology

Decide these before placing a single vertex. Each rule names the construction
the topology engine builds or refuses, so the engine's report tells you which
case you are in.

- **Smooth body (cylinder, ellipse), external flow**: an O-grid - a wall band,
  a core to a ring, and far-field patches. A rectangular outer boundary with
  inlet, outlet and two far-field sides is fine: each corner is a gate whose
  spoke follows the corner bisector, so two blocks share it at 45 degrees. That
  is the classical O-in-a-box; do not fight it.
- **Sharp trailing edge** (fluid sector of at least 250 degrees at one wall
  vertex): a **C-grid**. The band continues past the edge as a two-sided wake
  band along a straight wake line to the outlet; the far-field core is cut open
  along it. The edge then carries four blocks at right angles. Never leave a
  seam wedge or a fan at a production trailing edge: it collapses the
  body-normal lines into a downstream fan. Engine: `--wake`; refusals list the
  reason under `medial.wakes`.
- **Blunt base** (two convex corners within about one percent of the
  perimeter): a base template - the base is a wall edge whose block continues
  downstream as the wake core, and the two corner spokes bound the wake band.
  The engine refuses a wake from a single base corner; build the base by hand
  until the template exists.
- **Multi-element bodies** (slat, main, flap): the *effective airfoil* is a
  chain of wall pieces and **bridges** - straight cuts across a cove mouth or
  from a cusp to the next element - and the regions the chain skips are
  **pockets** filled by their own quad arrays. Each element keeps its own
  collar; an element's wake ends on the next element's band or on the outlet.
  Narrow gaps between elements take one strip of core blocks between the two
  bands rather than two half-cores around a medial line (engine: `--span-gaps`,
  opt-in, limited reach).
- **Internal flow** (channel, periodic hill, diffuser): a sweep between the two
  walls - columns from one wall band to the other, ends on inlet and outlet or
  on the cyclic pair. Corners of the domain are block corners; a guide wall's
  curvature is followed by the columns.
- **Concave corner** (fluid sector below about 157 degrees): the corner is a
  gate and its spoke runs along the bisector; the two adjacent blocks meet it at
  45 degrees. Acceptable; a single corner block with a 90-degree corner is
  better and is a documented follow-up.
- **Singularities** (interior vertices of valence 3 or 5) are where fans,
  wakes and gap strips need them. Keep them off the wall; the total index is
  fixed by the domain's Euler characteristic, so every extra valence-5 vertex
  needs a valence-3 partner somewhere.

## 3. Build outward from the wall

Layers, from the inside out: wall - **collar** (the boundary-layer band, a
mitred normal offset) - near-field marched offsets - an algebraic blend - the
medial ring or a transition circle - the far field. The collar's front, not
the wall, is the inner boundary of the core.

**Size the collar from the boundary layer, not from habit.** Where measured or
computed profiles exist, the band height is 1.5 to 2 times the largest
$\delta_{99}$ on the body, usually the suction side near the trailing edge; on
the A-airfoil at 13 degrees that is 0.15 chords ($\delta_{99} = 0.094c$ at
$x/c = 0.99$). "One to a few percent of the chord" is only the attached-flow
default when no profile is available. If a profile is truncated before
$U = 0.99\,U_e$, fit $\delta^*$, $\theta$ and the shape factor and extrapolate;
say so.

**Use the topology engine first** where it applies - single bodies, several
disjoint smooth bodies, simply connected internal domains - **and know its
measured reach**: on a sharp-edged airfoil it is admissible with a C-shaped
outer boundary up to about 6 chords and inadmissible at 8, 10 and 15, and the
wake is refused at every distance (the ring-clearance rule of its wake scales
with the far-field distance while the anchor spacing does not). A 15-chord
external-aerodynamics far field is therefore a hand build today; run the
engine at a small far field anyway to read its acceptance report and its
diagnosis of the trailing-edge cavity.

```bash
python experiments/agentic_topology/research_cli.py run \
  --curve body=body.dat --wake \
  --farfield-shape rectangle --farfield-box=-15,-15,31,15 \
  --farfield-sides bottom:farfield,outlet:outlet,top:farfield,inlet:inlet \
  --layer-height-ratio 0.01 \
  --session out/session.json --json out/report.json --output out/topology.png
```

Or supply the outer boundary chain by chain, anticlockwise and joined end to
end, for a C-shaped far field: `--outer cap:farfield=cap.dat --outer
bottom:farfield=bottom.dat --outer outlet:outlet=outlet.dat --outer
top:farfield=top.dat`. The band request scales with the domain, so a far-away
boundary needs an explicit `--layer-height-ratio`. Read the report's
acceptance terms - `topology_valid`, `untangled`, `within_shape_targets`,
`sizing_feasible`, `admissible`, `resolved` - and the refusal reasons; the
`describe`, `cavities`, `candidates`, `apply --move` and `focus` subcommands
let you choose among validated alternatives instead of placing vertices.

**Where the engine cannot** (multi-element wakes, blunt bases, pockets), write
geometry code, not clicks: a layout module that computes every vertex, curve
and block independently of BlockDrawer, and a generator that emits an
auditable `.jsonl` command batch and applies it in one atomic step:

```bash
blockdrawer-cli apply case.json --in-place -f build_commands.jsonl
```

Through the MCP the same batch is `edit_session` with
`commands_file="build_commands.jsonl"` and `in_place=true`; inline
`commands` are for a handful of corrections, not for a generated topology.

Two rules decide a hand-built sharp trailing edge, and neither is visible
until checkMesh fails. The collar spokes at the trailing-edge end of each side
must carry the **cross-wake direction** (the direction of the cut through the
edge), not the corner bisector, or the band blocks fold across the cut. And a
normal offset of height $h$ from a side that meets the edge at half-wedge
angle $\beta$ overshoots the cross-wake line by $h \sin\beta$, so the first
station on each side must be held out from the edge by more than that, with a
straight front edge, so every band block stays on its own side of the cut.

The commands that carry a hand-built topology: `add_vertex`,
`add_block_from_vertices`, `set_edge_type spline|polyLine|arc`,
`set_control_point_count` and `set_control_point` for curved edges,
`import_curve` plus `project ... fit=true` to put wall edges exactly on the
supplied geometry, `add_boundary`, `set_boundary_type`, `set_edge_boundary`,
`set_export_settings`. Prefer `split_edge` and `combine_blocks` for conformal
refinement of a strip over rebuilding blocks by hand. Commit the batch with
the session so the topology can be regenerated and reviewed.

## 4. Size and grade - after the topology is right

- **First cell** from the target $y^+$ with a screening friction coefficient
  $C_f \approx 0.003$:

$$
u_\tau/U_\infty \approx \sqrt{C_f/2}, \qquad
y_1^+ \approx Re_c \sqrt{C_f/2}\;\frac{y_1}{c}.
$$

  For a spectral-element mesh the first interior collocation point sits at
  about $0.064$ element heights for $p = 7$, so the first element height is
  $y_1 / 0.064$.
- **Growth** at most 1.2 to 1.3 per cell inside the band:
  `set_edge_grading v0-v1 start_width 0.002` on the band spokes; grading
  propagates through the opposite-edge constraint component when asked
  (`propagate=true`).
- **Counts are shared** along every constraint component (opposite edges of
  every block through the chain). `describe` lists the components. A
  component that holds a wall-tangent edge and a band-normal spoke ties
  streamwise resolution to boundary-layer resolution: that is a topology
  defect - fix the blocks, not the grading. A component whose long edge is a
  far-field arc and whose short edge is a wall segment only means large
  far-field cells, which is normal.
- **Spacing links** (`add_spacing_link driver follower`) keep cell widths
  continuous across block interfaces. Along the wake the rows start with the
  band's widths at the cross-wake line; **do not let them spread to a uniform
  transverse spacing at the outlet**. blockMesh blends the cross-line and the
  outlet distributions in proportion to streamwise distance, so a uniform
  outlet opens the first wake row by an order of magnitude within a few
  chords and produces the worst faces of the mesh (74 degrees and skewness 1.8
  on the A-airfoil). Grade the outlet's transverse edges so the spacing on the
  wake centre line stays of the order of the first streamwise wake cell; that
  alone brought the same mesh to 68 degrees and skewness 0.8.
- **The wall-to-far-field station map** is the largest lever on near-wall
  orthogonality in a hand-built O- or C-grid. Distribute the far-field
  stations by the arc length of the **band front**, not of the wall (a wall
  arc-length map put spokes 67 degrees off the normal on the A-airfoil, the
  front map 52) and not by wall turning (which spreads far-field cell sizes by
  two orders of magnitude).
- Aspect ratio matters inside the band only; interface size jumps should stay
  below about 2.5 inside the near field.

## 5. Validate - the ladder

1. `validate_session`: loads, topologically valid, exports.
2. `quality_report` (`summary_only=true` for the headline lines, `worst=N` for
   the N worst blocks and interfaces): corner angles 30 to 150 degrees,
   non-orthogonality at most 65 degrees, corner-cell aspect at most 100,
   growth at most 1.3, interface size jump at most 2.5. **These are measured
   at the block corners from the first mesh cell.** A block with curved
   spokes hides its interior distortion from them entirely: the A-airfoil
   read 18 degrees here and 68 in checkMesh. Only `check_mesh`, or the
   engine's sampled shape grid, sees the interior.
3. For a hand-built layout, check it **before** it becomes a session: the
   layout module should assert every block strictly convex from its true
   corner points, no two edges crossing (ray tests on the sampled curves), and
   the true corner angles at the wall within limits. Nothing in the production
   tools does this for you.
4. `render_session` zoomed on every trailing edge, corner, gap and bridge
   (`zoom`, `margin`, `preview`, `highlight`; `return_image=false` with
   `output` when the picture is a deliverable rather than something to look
   at). Look for folded or sliver blocks and for spline overshoot.
5. `check_mesh`: `blockMesh` then `checkMesh` must print `Mesh OK`. Read
   maximum non-orthogonality, skewness, aspect ratio and every negative-volume
   or `***` line. This is the authority. **checkMesh's aspect ratio includes
   the empty spanwise direction**, so for a pseudo-2D case the $z$ thickness
   is a quality parameter, not a free choice: with a first cell of
   $4.5\times10^{-6}c$ only $0.002 < \Delta z < 0.0045$ passed.
6. The engine's report, when it built the topology: the shape grid (eight
   uniform cells per block side) gives count-independent non-orthogonality,
   skewness and wall misalignment; the structure report gives the length ratio
   every component forces before any count exists. These acceptance terms
   (`admissible`, `untangled`, `within_shape_targets`, `resolved`) exist only
   for engine output; a hand-built session is judged by `validate_session`,
   the quality screen and `Mesh OK`.

Failure signatures and their fixes:

- **Negative volumes beside a mitred or capped station**: the spline's
  interior points were built from the plain local normal while the end
  stations carry the mitre direction, so the offset curve overshoots and folds
  the cells it bounds. Build the interior points from the local normal offset
  and correct them linearly onto both end stations.
- **A folded band block at a sharp convex feature**: the feature needs more
  incident blocks (seam, fan, wake), not a thinner band.
- **A core spoke crossing a front inside a narrow gap**: the gap holds too
  many layers; span it with one strip or move the ring.
- **A 3-degree corner in a seam wedge at a trailing edge**: the medial anchor
  is not inside the feature's sector; use the wake construction.
- **Length ratio far above 20 in one component**: decide whether it is
  far-field fan-out (acceptable, grade it) or band-to-core coupling (topology).
- **A gate that slid off a sharp vertex**: the sharp vertex must be a block
  corner; pin it and re-spread the neighbouring gates.

## 6. Deliver

A case directory with the session JSON, the exported `blockMeshDict`, the
command batch that regenerates the session, the retained `checkMesh` case or
log, and a README that states the topology outward from the wall, the patch
names and types, the first-cell and far-field sizes with their derivation, the
measured quality numbers, and what is still wrong. Report in the acceptance
vocabulary - valid, untangled, within shape targets, structurally feasible,
`Mesh OK` - never "good mesh".

## Where things are

- Editor commands and their arguments: `blockdrawer-cli commands`, or the
  `list_commands` MCP tool. Batches are atomic.
- Topology engine: `experiments/agentic_topology/README.md` for the algorithm,
  the measured results and the open limits; `research_cli.py run|describe|
  cavities|candidates|apply|focus`.
- Worked examples, when present: `cases/naca0012_cgrid` (sharp-edge C-grid with
  a six-block wake), `cases/a_airfoil` (C-grid with the wake deflected 13
  degrees, band sized from measured profiles, `Mesh OK` at 15 chords, built
  entirely by a layout module and a command batch) and `cases/30p30n`
  (three-element C-chain with bridges and pockets, and the diagnosed
  spline-overshoot defect).
