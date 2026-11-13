# Scaffold spike: cross field or medial axis

A decision memo. The question is whether the topology engine should keep its
scaffold — a raster Voronoi diagram with one site per closed loop — and
extend it, or change it. This spike measures a challenger and the
incumbent's obvious extension. Part 3 then asks whether an existing all-quad
mesher, used as a black box, can give the scaffold instead. It changes
nothing in the engine or in `blockdrawer/`.

Everything here runs from a clean checkout. NumPy and Pillow are required,
and `scipy` is used where it can be imported: `ndimage` labels the
partition and measures thinness, and `spatial.Voronoi` builds the medial
axis. The cross-field solver and the tracer use NumPy only.

```bash
python experiments/scaffold_spike/selftest.py                 # square, annulus, L: solver and tracer
python experiments/scaffold_spike/run.py backward_step --n 500
python experiments/scaffold_spike/run.py grooved_cylinder --n 320 --zoom=-0.8,-0.8,0.8,0.8
python experiments/scaffold_spike/run.py naca0012 --n 400 --zoom=0.75,-0.25,1.25,0.15
python experiments/scaffold_spike/run.py three_rotated_ellipses --n 320
python experiments/scaffold_spike/run.py 30p30n --n 700 --zoom=0.55,-0.15,0.95,0.1
python experiments/scaffold_spike/run.py CASE --n N --rotate 17 # stability under rotation
python experiments/scaffold_spike/graph.py naca0012 --n 400   # planar graph and faces of the partition
python experiments/scaffold_spike/medial.py grooved_cylinder --spacing 0.01 --rho 3 --zoom=0.2,-0.2,0.6,0.2
python experiments/scaffold_spike/extract.py naca0012 --n 800 --band 0.05 --out /tmp/naca.json --zoom=0.85,-0.2,1.2,0.07
# Part 3: gmsh as a black box (skips when no gmsh binary is found)
python experiments/scaffold_spike/quadmesh.py naca0012 --mesher quadqs --sizing uniform --n 40 --out /tmp/naca.json --zoom=0.8,-0.25,1.25,0.1
python experiments/scaffold_spike/quadmesh.py grooved_cylinder --mesher frontal --sizing curvature --n 40
python experiments/scaffold_spike/quadmesh.py three_rotated_ellipses --mesher quadqs --sizing uniform --n 80 --band
```

The commands write pictures and JSON to `output/`, which git ignores (the
research `.gitignore` rule). The figures this memo cites are copies kept in
`figures/`; the commands above regenerate them.

Geometry comes from the repository:

- backward step: written in `cases.py`, 2:1, inlet 5H, outlet 20H.
- grooved cylinder: `cylinder1.dat` from the skill's examples, in a 6×6 box.
- NACA 0012 at 5°: `naca0012_a5.dat`, in a box $[-1.5, 2.5]\times[-1.5, 1.5]$.
- `three_rotated_ellipses`: the engine fixture, in a box $[-3,3]\times[-2,2]$.
- 30P30N: the engine's normalised slat, main and flap, in a box $[-1,2.2]\times[-1.2,1]$.

## Part 1: a boundary-aligned cross field

### What was built (`crossfield.py`)

- **Representation.** A cross is stored as $z = e^{4i\theta}$ on a raster of
  `n` pixels along the longer side.
- **Boundary condition.** Fluid pixels next to a solid pixel take $z$ from
  the tangent of their nearest boundary segment (Dirichlet).
- **Interior.** The interior solves
  $$\Delta z = 0$$
  by conjugate gradients, matrix-free, with relative residual $10^{-9}$.
  The cross directions are then
  $$\theta = \tfrac{1}{4}\arg z + k\,\tfrac{\pi}{2}.$$
- **Singularities.** The winding of $\arg z$ round every 2×2 pixel cell
  gives index $\pm\tfrac{1}{4}$: $+\tfrac{1}{4}$ is valence 3, $-\tfrac{1}{4}$ is valence 5.
- **Corner rule (one rule for every corner).** A boundary vertex turning by
  more than 30° is a corner of fluid angle $\alpha$. It gets
  $$k = \max\left(1, \operatorname{round}\frac{\alpha}{90^\circ}\right)$$
  sectors and therefore $k-1$ separatrices. Each separatrix leaves along
  the field-aligned direction nearest the even subdivision
  $\alpha\,j/k$ of the corner's sector. With this rule:

  | Corner | Fluid angle | $k$ | Separatrices |
  |---|---|---|---|
  | box corner | 90° | 1 | 0 |
  | reflex step corner | 270° | 3 | 2 |
  | NACA trailing edge | ≈345° | 4 | 3 (the wake line and the two normals) |
  | 30P30N cusp | ≈360° | 4 | 3 |

- **Separatrices.** Every singularity emits its field-aligned directions,
  found on a circle of radius $2.5h$. Every separatrix is traced with RK2
  at step $h/2$. A trace ends at the wall, at a singularity (within
  $1.5h$), or at a length limit of three times the raster perimeter, which
  marks it as non-closing.
- **Partition.** The traces are rasterised (one pixel, dilated once) and
  the remaining fluid is labelled into patches.
- **Patch measures.**
  - *Nodes per patch:* the singularities, corners, wall ends of traces and
    trace crossings within four pixels of the patch. This is a proxy for
    the number of sides.
  - *Thinness:* $4r^2/A$, with $r$ the patch's inscribed radius and $A$ its
    area. It is about $w/L$ for a $w\times L$ rectangle and 1 for a square.
  - *Near-coincident:* a pair of traces sharing more than 12 pixels.
- **Self-test** (`selftest.py`, five tests, all pass):
  - a square: no singularity, one 4-node patch, the field axis-aligned to $10^{-6}$;
  - an annulus: no singularity, a polar field;
  - an L: two separatrices from the reflex corner, ending at $(1,0)$ and $(0,1)$, cutting three quads;
  - a trace in the square: reaches the wall on its row;
  - determinism.

### Measurements

Runtimes are for single runs on one core. The 1.5× and rotated runs ran ten
at a time, so their times are inflated.

| Case (raster n) | Singularities (index: count) | Separatrices; ends | Near-coincident pairs | Patches | 4-node patches | Thinnest $4r^2/A$ | Stable under 17° / 1.5× | Time |
|---|---|---|---|---|---|---|---|---|
| backward step (500) | none | 2; all at walls | 0 | 3 | 3 | 0.045 (the 20H downstream channel) | yes / yes (3 patches each) | 0.2 s |
| grooved cylinder (320) | −¼: 4 | 44; 37 wall, 7 singularity | 9 | 45 | 12 | 0.098 | singularities 4/4 matched both ways; patches 45 / 38 rotated / 46 finer | 2.0 s |
| NACA 0012 at 5° (400) | −¼: 2 (both at the nose) | 10; all at walls | 1 | 10 | see graph.py below | 0.121 | singularities 2/2 both ways; patches 10 / 6 rotated / 7 finer | 3.5 s |
| three rotated ellipses (320) | −¼: 12 | 54; 45 wall, 9 singularity | 3 | 48 | 10 | 0.027 | singularities 12/12 both ways; patches 48 / 37 rotated / 66 finer | 1.3 s |
| 30P30N (700) | +¼: 1, −¼: 5 | 38; 36 wall, 2 singularity | 11 | 28 | 6 | 0.044 | singularities 6/6 both ways; patches 28 / 25 rotated / 37 finer | 19.6 s |

"Matched" means the same index within 5% of the domain size, after
rotating the rotated run back. No trace hit the length limit in any run, so
no limit cycle was seen.

The singularity indices also add up as the theory requires: corner
contributions plus singular indices equal the Euler characteristic. The
three ellipses in a box need $-3$ in singularities after the four box
corners' $+1$, and get exactly $12\times(-\tfrac14)$.

### What the field does where the engine uses templates

- **Reflex step corner** (`figures/backward_step_n500.png`, `figures/backward_step_n500_zoom0.png`).
  The two separatrices from the corner cut the domain into exactly the
  three-row H-grid a person draws: three quads, no singularity. The engine
  refuses this case (`sweep_correspondence`).
- **Trailing edge** (`figures/naca0012_n400.png`, `figures/naca0012_n400_zoom0.png`). The
  $k=4$ trailing edge sends the wake line to the outlet plus one normal up
  and one down. The two −¼ singularities sit just above and below the nose.
  Together that is the classic C/H blocking, with the wake cut and the
  nose singularity pair produced by the field rather than by `wake.py`.
- **Cove** (`figures/30p30n_n700_zoom1.png`). The cove fills with a structured array:
  horizontal separatrices from the cove's corners cross to the flap, and a
  −¼ singularity sits at the cove lip and one at the flap nose. That is what
  `cove_anchors` and the pocket arrays do by hand.
- **Slots and walls** (`figures/30p30n_n700_zoom0.png`). Several separatrices from
  different singularities run parallel to the walls a few pixels apart.
  These are the near-coincident pairs (11 on 30P30N), and they cut thin
  strips (thinnest 0.044). This is the cross-field version of our slivers.
- **Grooves** (`figures/grooved_cylinder_n320_zoom0.png`). Every groove corner
  ($k=3$) sends one separatrix radially and one tangentially. The tangential
  ones from neighbouring grooves nearly coincide on rings around the body
  (9 pairs). The result is many 2- and 3-node patches, against the hand
  build's clean pockets.

### End-to-end proof: not reached

`graph.py` turns the separatrices into a planar graph: nodes at
singularities, corners, wall ends (projected onto the boundary) and exact
segment crossings, arcs between them, faces by half-edge traversal.
Corners are turns above 45°; a turn within 45° of straight is a hanging
(T-junction) node.

| Case | Nodes | Arcs | Faces | Clean quads | Result |
|---|---|---|---|---|---|
| L, backward step | 8 | 10 | 4 | 3 | the outer face aside, exactly the 3 quads |
| NACA (n=400) | 16 | 26 | 10 | 5 | two 3-corner faces, one 12-corner face; the face round the airfoil does not close |
| grooved cylinder | 77 | 178 | 87 | 31 | 27 two-corner faces; nine traversals do not close |
| three ellipses | 112 | 233 | 121 | 75 | six traversals do not close |

The traversals that do not close are the finding. Near-coincident
separatrices overlap without crossing cleanly, and nodes merged at the
$1.5h$ snapping tolerance then leave arcs whose directions contradict
their endpoints. The arc set is not planar, so the next-half-edge map is
not a permutation. No session could be written from it.

Three things stand between the partition and a block layout:

1. merging or snapping near-coincident separatrices;
2. extending traces through T-junctions;
3. cutting the wall-hugging strips into proper bands.

None of these was attempted within the time box.

## Part 2: self-branches of the medial axis (`medial.py`)

The full medial axis comes from the Voronoi diagram of boundary samples at
`spacing`. One pruning rule, stated:

- an edge is kept when both its Voronoi vertices are in the fluid, and
- its two generating samples lie on different loops (a *between* branch),
  or on the same loop with arc separation along it greater than $\rho$
  times the local radius (a *self*-branch).

| Case | Window | Self-branch length in window, ρ=3: base / spacing÷1.5 / 17° | Same, ρ=1.5 | Time |
|---|---|---|---|---|
| backward step | $[-1,1]^2$ around the step | 2.173 / 2.158 / 2.173 | 3.552 / 3.549 / 3.554 | 0.1 s |
| grooved cylinder | one groove | 0.177 / 0.178 / 0.177 | 0.399 / 0.401 / 0.399 | 0.15 s |
| 30P30N main cove | $[0.62,0.82]\times[-0.06,0.03]$ | 0 / 0 / 0 | 0.093 / 0.092 / 0.093 | 0.2 s |

- **Stability.** The branches are stable under rotation and resolution (a
  spread under 1%).
- **They appear.** The step and the groove carry self-branches at either
  ρ (`figures/medial_*_zoom0.png`).
- **The cove is a pruning decision, not a fact of the geometry.** The
  30P30N cove is wide and shallow relative to its mouth, so its branch
  appears at ρ=1.5 and not at ρ=3.

What the engine would need to consume self-branches. This is from reading
the code; none of it was coded.

- **Where one-site-per-loop is built in.**
  - `sites.Site` owns exactly one closed loop.
  - `voronoi_graph` labels raster pixels by `argmin` over sites (line 109),
    so a pixel can never be equidistant to two points of one loop.
  - The bisector tracing (`_bisect_cell_exit`) and the per-cell gate
    placement in `block_layout` (one cell per body, gates on its ring)
    assume a cell is one body's region.
  - Splitting a loop into several sites at its concave features is
    possible, but the pieces then share endpoints, and every
    "cell = body" assumption in `block_layout`, `layers` (band per body)
    and `patch_graph` (band/front roles per body) would need a cell made
    of several sites.
- **What self-branches would replace.**
  - `block_layout.cove_anchors`;
  - `layers.tight_concave_runs` and `effective_fluid_angles` as a rounded reflex corner rule;
  - the pocket special cases;
  - possibly part of `hold_corner_shadows`.
- **What stays.** `wake.py` (3,553 lines), `hull.py` (1,149), `cgrid.py`
  (563) and `fan_cavity.py` (1,957). Self-branches do nothing for wakes,
  far fields or seams at convex sharp corners: convex corners already end
  branches.
- **Rough size.** Sites, diagram and gate placement are about 3,000 lines
  (`sites.py` 477, `voronoi_graph.py` 786, `block_layout.py` 1,741), plus
  the role propagation in `patch_graph`.

## What each scaffold gives by construction

| | Medial scaffold (today) | Medial + self-branches | Cross-field partition |
|---|---|---|---|
| Reflex corners, steps, L-channels | refused or special-cased | a branch per corner region (measured) | separatrices by the corner rule (measured: step, L, cove) |
| Grooves, coves | pinned anchors (`cove_anchors`), pockets | branches (groove yes; cove depends on ρ) | arrays across them (measured), but near-coincident rings round grooves |
| Wakes and trailing edges | templates (`wake.py`, `cgrid.py`) | templates | the wake line and nose singularities from the field (measured on NACA) |
| Far field round clusters | `hull.py` | `hull.py` | separatrices to the box (measured), patch count resolution-dependent |
| Blocks out of the box | yes, validated | yes | **no** — extraction not solved here |
| Bands | `layers.py` per body | same | not by construction (hugging separatrices are accidental strips) |

## Which engine stages survive a change

- **Survive both changes.** Band construction (`layers`, as an offset
  inside the fluid), sizing (`sizing.py`, and production `size` on the
  session), verdicts (`grid_quality`, acceptance), the sampled smoother,
  `session_emit`.
- **Replaced by a cross-field scaffold.**
  - `sites`, `voronoi_graph`, the gate and cell machinery of `block_layout`;
  - the wake, hull and C-grid templates (about 5,300 lines);
  - the cavity repair of seams, `fan_cavity` (1,957), whose singular
    structure the field fixes instead.

## Risks, as measured

- **Near-coincident separatrices.** 9 on the grooved cylinder and 11 on
  30P30N at the base resolution; the count moves with resolution and
  rotation (30P30N: 11 / 12 finer / 0 rotated). This is the main risk: it
  is what broke the graph.
- **Partition instability.** Singularity sets were stable in all five
  cases under both perturbations, but the patch counts were not:
  three ellipses 48 / 37 / 66, NACA 10 / 6 / 7.
- **Limit cycles.** None seen: no trace hit the length limit. That is
  unproven beyond these five cases.
- **Corner rule.** One rule handled the step, the trailing edge and the
  cove corners without a per-case choice. The 30P30N cusps ($k=4$, three
  separatrices each) add separatrices that mostly become the thin strips.
- **Raster resolution.** 30P30N needed $h \approx 0.0046$ (700 px). The slots
  are a few pixels wide there, and a run took 20 s single-core, 125 s at
  1.5× under load. A triangle mesh would be the next step.

## Rough cost of the three options

These are agent rounds of the size of this review's items, estimated from
this spike and from reading the code, not measured.

- **(a) Keep the medial scaffold, add self-branches: 3–4 rounds.**
  Multi-site loops in `sites`/`voronoi_graph`, cells of several sites in
  `block_layout`, `layers` and `patch_graph`, then retiring `cove_anchors`
  and the tight-concave rules. It buys the step, L-channels and coves. It
  keeps every wake, hull and C-grid template. The cove branch depends on a
  pruning threshold (measured), so a rule still decides.
- **(b) Replace the scaffold with the cross-field partition: 5–7 rounds,
  high risk.** Quad-layout extraction (merging near-coincident
  separatrices, T-junction extension, band insertion) is the unsolved
  part here. Then gates and roles for sizing, a triangle field for thin
  slots, and re-baselining the corpus. It removes about 7,000 lines of
  templates if it works.
- **(c) Hybrid: a cross field outside the bands, for the far field and the
  wakes: 4–5 rounds.** The same extraction problem, in the simpler region
  outside the bands, where the NACA field already shows the C/H structure.
  The bands, the near-body medial gates and `cove_anchors` stay.

## Item 8b: extraction - gate G1 failed

### What was built (`extract.py`)

1. **The band is clipped first.** Each body is offset by the band height
   $H$ (a plain offset along vertex normals, not the engine's `layers.py`,
   with a round join of radius $H$ at a convex corner sharper than 60°).
   The field is solved and traced only outside these fronts, and the band
   is meant to be one row of blocks between wall and front. A front
   interval lying entirely on a round join gets its middle as a node, so
   the band there is a kite.
2. **Merge rule** (one length scale, $M = H/2$):
   - graph nodes closer than $M$ become one node;
   - a separatrix ends where it comes within $M$ of a boundary after having
     been farther, and one that never leaves that strip is dropped;
   - a singularity within $M$ of a boundary is moved onto it.

   What this gives up is alignment within $M$ of walls and fronts: a trace
   there is cut instead of followed. At the cut a block corner can sit up
   to 45° off the field.
3. **T-junctions.** A node at which a fluid face turns by less than 45° is
   extended: a trace leaves it into the face along the cross direction most
   normal to the face's side, and runs to a boundary or a singularity. This
   repeats until no face has one, or for 12 rounds.
4. **Session.** Graph nodes become vertices and four-corner faces become
   blocks. Arcs become polyLine edges through the trace. Band blocks run
   from wall stations under each front node to the front, with spline wall
   edges.

### Gate G1: NACA 0012 at 5° in the box - fails

The band height was 0.05 chord, as for the operators' NACA.

| Raster | Singularities | Fluid faces after 12 rounds (corners: count) | Hanging nodes left | Blocks written | Session |
|---|---|---|---|---|---|
| n = 400 ($H$ = 5 px) | 4 × −¼ (nose pair, tail pair) | 4: 12, 3: 1 | 2 | 16 | refused: block b13 not strictly convex |
| n = 800 ($H$ = 10 px) | 4 × −¼ | 4: 9, 5: 2, 3: 1, 2: 2 | 2 | 13 | refused: block b11 not strictly convex |

For comparison, the operators' NACA is 190 blocks, Mesh OK at 48.6°, and
the engine's C-grid is 185 blocks, Mesh OK at 44.1°.

Figures:
- `figures/extract_naca0012_n800_field.png` – the field outside the front;
- `figures/extract_naca0012_n800_graph.png` – the faces, green = four
  corners, other colours = not;
- `figures/extract_naca0012_n800_graph_zoom0.png` – the trailing edge.

What it shows:

- **Away from the trailing edge the layout is right.** With the band
  clipped, no separatrix hugs the airfoil any more. The nose pair of
  singularities sits off the front with short separatrices onto it. The
  far field is nine to twelve clean quads of an H/C topology. The wake is
  a band of two separatrices from the tail pair to the outlet: a wake
  line, not a fan.
- **The trailing edge is where it fails, at both resolutions.**
  - The two tail singularities sit about $0.7H$ outside the round join
      ($1.7H$ from the trailing edge).
  - Their separatrices, the round-join midpoint's extension and the
    merge-snapped landings cut faces with two, three and five corners
    there.
  - The two remaining T-junction nodes sit on the front inside the round
    join, so every extension from them lands back on the front within $M$
    and is cut. They never resolve.
  - The quads written next to them are not convex.
- **Doubling the raster did not fix it**, so this is not a resolution
  limit: the trailing-edge band and the singular pair behind it are a
  construction problem. The engine solves exactly this with its wake
  template (the band continuing as a wake band). The cross field did not
  produce it from the round join by itself, and a sharp front corner (a
  mitred trailing edge) would put a 172° band corner at the wall.

Per the gate, G2 (grooved cylinder) and G3 (30P30N) were not run. The
block-count stability check applies only to passing cases, so there is
none.

## Part 3: an all-quad mesher as the scaffold (`quadmesh.py`)

The question: can an existing all-quad mesher, used as a black box, give a
general block scaffold? The mesher is the installed gmsh
(`5.0.0-git-b1baeffb8`); nothing was built or downloaded.

### What was built

- **Geometry to mesher input.** Each loop is split at corners turning more
  than 30°; straight pieces become `Line`s, curved pieces `Spline`s through
  the supplied points. A smooth body is split into four pieces, a body with
  one corner into two. One physical curve per patch.
- **Meshers.** The same settings for every case.
  - `quadqs`: gmsh's quasi-structured quad mesher, `-algo quadqs`.
  - `frontal`: frontal-Delaunay for quads, recombined and subdivided
    (`-algo delquad`, `RecombineAll`, `SubdivisionAlgorithm 1`). This is
    always all quads.
  - Sizing: either `curvature` (size = longer domain side / n, refined by
    curvature at 16 elements per $2\pi$, down to a fortieth of that size)
    or `uniform` (that one size).
  - Kernel: the built-in one, or OpenCASCADE (`occ`).
- **Base complex.**
  - An interior vertex is irregular when its valence is not 4. A boundary
    vertex is irregular when its quad count is not round(fluid angle / 90°).
  - Separatrices leave every irregular vertex and every boundary corner
    along each incident edge. They go straight through regular vertices
    and stop at the next irregular or boundary vertex.
  - Patches are flood-filled across uncut edges. A patch whose rim has four
    corners is a block.
- **Session.**
  - Blocks are counter-clockwise. Each edge's cell count is its chain
    length, so opposite sides agree by construction.
  - Wall chains become splines and other chains polyLines, each with about
    40 points.
  - Boundary chains get their patch names.
  - The model's own validation decides `valid`.
- **Pipeline (n = 40).**
  - First `set_export_settings` with empty z patches.
  - Up to 600 blocks: `smooth mode=shape allow_stale_fold=true`, then
    `size FIRST CORE ...`, then `smooth`. Above 600 blocks only `size`
    runs, because shape smoothing a few thousand blocks does not finish in
    the time box.
  - Then `check` (blockMesh and checkMesh).
  - The size arguments are per case and the same for every mesher:
    - step: `0.005 0.25 growth=1.1 wall_aspect=30`
    - grooved cylinder: `0.005 0.5 growth=1.2 wall_aspect=10`
    - NACA: `1e-4 1.0 growth=1.2 wall_aspect=50`
    - ellipses: `0.01958 0.3916 growth=1.2 count_rule=mean`
    - 30P30N: `0.001 1.0 wall_aspect=25`
- **Band first (`--band`).** Every body is replaced by its front, offset
  into the fluid by 5% of the smallest body's extent. The mesher meshes
  outside the fronts. Each front chain then gets a band block down to the
  wall, whose corners are the nearest wall points. Only smooth bodies are
  accepted: a body with a sharp corner is refused, because the offset
  gives no mitre, fan or wake there.
- **Self-test.** `selftest.py` checks the base complex on meshes of known
  structure:
  - a 4×3 grid is one block with counts 3, 3, 4, 4;
  - an L-shaped grid is three blocks cut at its reflex corner;
  - three subdivided quads round a valence-3 vertex are three blocks.

  An end-to-end test runs quadqs on the step and skips when gmsh is absent.

### The matrix

Block counts are given at n = 20 / 40 / 80. Everything else is at n = 40.
"Irregular" is the number of interior irregular vertices, then how many of
them lie within a band height (5% of the smallest body) of a wall, then the
number of irregular boundary vertices. "Thinnest" is the most slender
block: the smaller sum of opposite side lengths over the larger one. The
checkMesh column gives the cells, the maximum non-orthogonality, the faces
above 70° and the worst size jump across block interfaces.

| case | mesher | all quad | irregular | blocks 20/40/80 | thinnest | session | checkMesh after shape, size, polish |
|---|---|---|---|---|---|---|---|
| step | quadqs, curvature | yes | 4 / 0 / 0 | 9 / 24 / 47 | 0.025 | valid | **OK**: 36,331 cells, 25.5°, 0 faces, jump 1.4 |
| step | quadqs, uniform | yes | 0 / 0 / 0 | 3 / 3 / 55 | 0.05 | valid | **OK**: 6 blocks after size, 50,640 cells, 0.0°, 0, 1.0 |
| step | frontal | yes | 43 / 0 / 6 | 3 / 141 / 115 | 0.18 | valid | **OK**: 162 blocks, 89,314 cells, 55.7°, 0, 5.1 |
| grooved cylinder | quadqs, curvature | crash at every size: `cannot project with surface projector` | | | | | |
| grooved cylinder | quadqs, uniform | yes | 14 / 0 / 6 | 95 / 170 / 270 | 0.033 | valid | **OK**: 42,488 cells, 40.6°, 0, 3.0 |
| grooved cylinder | frontal | yes | 461 / 44 / 19 | 1,718 / 3,462 / 10,220 | 0.21 | valid | size only, **OK**: 111,706 cells, 79.1°, **28**, 9.4 |
| NACA 0012 | quadqs, curvature | crash at 20 and 40, timeout at 80 | | | | | |
| NACA 0012 | quadqs, uniform | yes | 7 / 0 / 0 | 61 / 74 / 244 | 0.044 | valid | **OK**: 189,701 cells, 52.7°, 0, **15.0** |
| NACA 0012 | frontal | yes | 264 / 25 / 7 | 659 / 1,975 / 5,205 | 0.17 | valid | size only, **OK**: 528,632 cells, 70.0°, 0, **275** |
| three ellipses | quadqs, curvature | crash at every size | | | | | |
| three ellipses | quadqs, uniform | yes | 6 / 0 / 0 | 77 / 81 / 342 | 0.043 | valid | **OK**: 5,056 cells, 24.4°, 0, 2.6 |
| three ellipses | frontal | yes | 433 / 35 / 22 | 1,245 / 2,659 / 5,312 | 0.16 | valid | size only, **OK**: 4,193 cells, 56.9°, 0, 9.7 |
| three ellipses | quadqs, uniform, band first | crash at 20 and 40; at 80: yes | 44 / – / 6 | – / – / 690 + 41 band | 0.031 | valid | size only (n = 80), **OK**: 5,452 cells, 51.7°, 0, 3.6 |
| 30P30N | quadqs, curvature | crash at 20 and 40, timeout at 80 | | | | | |
| 30P30N | quadqs, uniform | crash at every size | | | | | |
| 30P30N | frontal | yes | 386 / 17 / 18 | 975 / 2,423 / 7,585 | 0.18 | valid | size only, **OK**: 131,069 cells, 76.1°, **5**, **30.4** |

Every curved case with the OpenCASCADE kernel and quadqs gave a degenerate
mesh:
- NACA: 3,172 of 5,354 nodes coincide;
- grooved cylinder: 4,286 of 6,708;
- ellipses: 5,711 of 10,344;
- 30P30N: 11,346 of 17,844.

Merging the coincident nodes leaves collapsed quads and edges shared by up
to 16 quads, so these are failures, not a writing artefact. On the step,
OCC matches the built-in kernel.

Band first was refused on NACA (one sharp corner), on the grooved cylinder
(24 corners) and on 30P30N (sharp trailing edges). It does not apply to
the step, whose walls are the domain's own boundary.

References, built by hand, with the operators or by the engine:

| case | blocks | max non-orthogonality | faces > 70° | worst jump |
|---|---|---|---|---|
| step | 27 by hand, 36 with the operators | 0° | 0 | 1.1 |
| grooved tandem | 251 / 270 with the operators | 20.5° | 0 | 3.4 |
| NACA 0012 | 190 with the operators, 185 by the engine | 48.6° and 44.1° | 0 | 4.4 (operators) |
| three ellipses | 80 by the engine | 71° | 2–3 | – |
| 30P30N | 203 by the engine | 68.8° | 0 | – |

The ellipses' size arguments give a very coarse mesh (about 5,000 cells)
whichever mesher made the layout, so that row compares layouts, not
resolutions.

Other gmsh settings tried, each on its own, outside the matrix:
- Recombination without subdivision (`RecombinationAlgorithm 1`) left 2
  triangles on NACA. 262 of its 2,052 interior vertices were irregular,
  the same density as `frontal`.
- Blossom full-quad (`RecombinationAlgorithm 3`) aborted with "1D mesh of
  MEdge 6 cannot be divided by 2".
- On 30P30N, none of these stopped quadqs from crashing in "optimize quad
  mesh topology with cavity remeshing":
  - `QuadqsSizemapMethod` 1 or 3;
  - `QuadqsScalingOnTriangulation 0.5`;
  - `QuadqsTopologyOptimizationMethods 0`.

### What the matrix says

![NACA 0012 trailing edge, frontal: a 3-5 pair every few cells, blocks of 2x2 cells](figures/quad_naca_te_frontal.png)

- **An unstructured all-quad mesher is not a scaffold.**
  - `frontal` meshed every case as all quads. Its irregular vertices
    scale with the cell count: on NACA there are 264 at n = 40 and 503 at
    n = 80, 25 of them within a band height of the wall.
  - The base complex is therefore 1,000–10,000 blocks of about 2×2 cells
    (figure above; [30P30N cove](figures/quad_30p30n_cove_frontal.png),
    [slat and slot](figures/quad_30p30n_slat_frontal.png)).
  - The pipeline still reaches Mesh OK on every case, but with 28 faces
    above 70° on the grooved cylinder, 5 on 30P30N and size jumps of 9 to
    275. Those are many tiny blocks sized independently.
- **quadqs gives a coarse layout, but only with a uniform size, and not
  on 30P30N.**
  - With gmsh's own curvature sizing, quadqs crashed on every curved
    body. With the OpenCASCADE kernel it wrote degenerate meshes.
  - With one uniform size and the built-in kernel it meshed four of the
    five cases. Each became a valid session and reached Mesh OK:
    [NACA](figures/quad_naca_quadqs.png),
    [grooves](figures/quad_grooves_quadqs.png),
    [ellipse](figures/quad_ellipse_quadqs.png),
    [step](figures/quad_step_quadqs.png).
  - It crashed on 30P30N at all three sizes with both size settings and
    with the four option changes above.
  - Using the size field to rescue the curved cases is a per-mesher
    setting, the same for all cases. It is reported here as a finding,
    not tuned per case.
- **What quadqs does well.**
  - No interior irregular vertex lies within a band height of any wall.
  - At the NACA trailing edge it puts four quads round the edge and a
    straight wake line downstream
    ([figure](figures/quad_naca_te_quadqs.png)). This is the structure
    item 8b could not get from the cross field.
  - On the step and on the ellipses its layouts are cleaner than the
    references. The step has 3 blocks and 0°. The ellipses have 81 blocks
    and 24°, against the engine's 71°, though at the coarse resolution
    noted above.
- **What it does badly.**
  - **Its block layout is not stable under refinement.** NACA goes from
    61 to 74 to 244 blocks, the grooved cylinder from 95 to 170 to 270,
    the ellipses from 77 to 81 to 342, and the step from 3 to 3 to 55.
    Each added 3–5 pair sends separatrices across the whole domain one or
    two cells apart. They make the thin strip blocks (thinnest 0.016–0.07)
    that run from the body to the box.
  - **The layout round a body is an H-grid, not a band.**
    - The body is a block-structured rectangle with corners at the
      leading and trailing edges (the ellipse tips likewise).
    - At the NACA leading edge, wall-normal lines fan from that corner
      ([sized](figures/quad_naca_le_sized.png)), and strip separatrices
      land on the wall.
    - After sizing, NACA's worst size jump is **15**, against 4.4 with the
      operators. The jump sits where a strip carrying the wall's first
      cell meets a block that does not. These are the jumps the user asked
      not to have.
- **Band first did not help.**
  - It is refused on any sharp corner, and three of the four bodies have
    them.
  - On the ellipses the mesher crashed outside the front at two of three
    sizes. At the third the layout had 731 blocks for 5,452 cells.

## Part 3 recommendation

**No: gmsh as a black box is not a general scaffold.**
- `frontal` is general but its base complex is the mesh itself.
- `quadqs` is coarse but not general: it crashes on 30P30N and on every
  curved body under its own curvature sizing. Its block count follows the
  mesh size. Its H-type layout round bodies leaves size jumps the sizing
  cannot remove.

**At most a partial scaffold:** a quadqs layout with a uniform size, for a
single smooth or mildly featured body in a box, or for a channel. That is
the step, the grooved cylinder, NACA and the ellipses, all Mesh OK. Even
there it is a first draft, because the block count depends on n. Those are
already the cases the operators handle in 8 to 30 commands.

**The practical answer for now is the agent workflow with the construction
operators**, as the item 8b recommendation already says. The engine stays
frozen.

Evidence that would change this:
- A quadqs (a newer gmsh, not built here) that meshes 30P30N at three sizes
  without crashing, under its own curvature sizing.
- After that, a base-complex simplification on our side that holds the
  block count within about 20% from n = 20 to 80 on all five cases. It
  would merge the parallel 3–5 strips, for example with `combine_blocks`
  along whole strips.
- A worst jump at or below the operators' (4.4 on NACA) after `size`.

Only then would a round wiring quadqs into the engine as its scaffold pay
for itself. The simplification is our own code, about a round, but it is
not worth starting while the 30P30N crash stands.

## Recommendation

**Keep the engine as it is, and rely on the construction operators for
what it refuses.** Do not fund (b), (c) or (a) now. Part 3 adds that an
off-the-shelf all-quad mesher (gmsh) is no general alternative either; see
the Part 3 recommendation above.

- **(b) and (c)** stand on the extraction, and G1 — the simplest body with
  a sharp edge — did not produce a valid session at either resolution. The
  remaining fault sits exactly where the engine's 3,500-line wake template
  works: the band–wake junction at a trailing edge. Getting it from the
  field needs a C-grid-like rule at the trailing edge, which is the
  template again.
- **(a)** costs 3–4 rounds and buys the step, L-channels and coves.
  - Two of those already go by hand with the operators: the step in 8
    commands (Mesh OK, 0°) and the grooved cylinders in 14 (Mesh OK,
    20.5°, against the hand script's 42.7°).
  - The cove branch exists only at a pruning threshold of 1.5, not 3, so a
    rule would still decide whether it is there.

What the spike did establish, and what is worth keeping: the field's
singularity sets are stable under rotation and a 1.5× raster in all five
cases, and away from sharp edges the clipped layout is clean quads.

**First concrete step:** freeze the scaffold. Record in the engine README
and the skill's route table that refusals go to the operators, which is
already the measured route. Keep `experiments/scaffold_spike/` as the
harness any future scaffold proposal must pass: G1, then G2, then G3,
unchanged.

**Evidence that would reopen the choice:**
- a trailing-edge construction that makes `extract.py naca0012` write a
  valid, Mesh OK session without a per-case rule — then (c), with the
  far field and wakes from the field;
- or a refused domain class that the operators cannot state in a few dozen
  commands — then (a), for that class.

### Previous recommendation (item 8, superseded)

Item 8 recommended a round on extraction before choosing; item 8b was that
round, and its gate failed.
