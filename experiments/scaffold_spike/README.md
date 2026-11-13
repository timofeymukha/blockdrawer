# Scaffold spike: cross field or medial axis

A decision memo. The question is whether the topology engine should keep its
scaffold — a raster Voronoi diagram with one site per closed loop — and
extend it, or change it. This spike measures a challenger and the
incumbent's obvious extension. It changes nothing in the engine or in
`blockdrawer/`.

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
```

The pictures and JSON go to `output/`; the figures cited below are
committed there.

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

- **Reflex step corner** (`backward_step_n500.png`, `backward_step_n500_zoom0.png`).
  The two separatrices from the corner cut the domain into exactly the
  three-row H-grid a person draws: three quads, no singularity. The engine
  refuses this case (`sweep_correspondence`).
- **Trailing edge** (`naca0012_n400.png`, `naca0012_n400_zoom0.png`). The
  $k=4$ trailing edge sends the wake line to the outlet plus one normal up
  and one down. The two −¼ singularities sit just above and below the nose.
  Together that is the classic C/H blocking, with the wake cut and the
  nose singularity pair produced by the field rather than by `wake.py`.
- **Cove** (`30p30n_n700_zoom1.png`). The cove fills with a structured array:
  horizontal separatrices from the cove's corners cross to the flap, and a
  −¼ singularity sits at the cove lip and one at the flap nose. That is what
  `cove_anchors` and the pocket arrays do by hand.
- **Slots and walls** (`30p30n_n700_zoom0.png`). Several separatrices from
  different singularities run parallel to the walls a few pixels apart.
  These are the near-coincident pairs (11 on 30P30N), and they cut thin
  strips (thinnest 0.044). This is the cross-field version of our slivers.
- **Grooves** (`grooved_cylinder_n320_zoom0.png`). Every groove corner
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
  ρ (figures `medial_*_zoom0.png`).
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

## Recommendation

**Do not commit to (b) yet, and do not spend on (a) first.**

The cross field gets the hard part right by construction, and stably:
- the singular structure the templates hard-code;
- the step's three rows;
- the wake line;
- the cove array.

The open part is turning separatrices into blocks. Its first test failed
on every case with a body (NACA, grooved cylinder, three ellipses),
because of near-coincident separatrices.

Next round:
1. The extraction alone, on the NACA, the grooved cylinder and 30P30N:
   - snap separatrices within a band height of each other;
   - extend T-junctions;
   - clip at a band offset.
2. Gate it on a valid session and `checkMesh` for NACA and 30P30N.

If that passes with no more blocks and no worse slivers than the engine,
go to (c), then (b). If near-coincident separatrices on 30P30N cannot be
merged without new slivers, keep the medial scaffold for the near field
and lean on the construction operators for the domains it refuses: they
build the step and the grooves today in 8 and 14 commands. In that case
(a) is worth its cost only for coves and L-channels.

The evidence that would change this:
- extraction succeeding at a block count near the engine's;
- or, against the cross field, separatrix counts on 30P30N that keep
  rising with resolution.

Measured here: everything in the tables. Not measured: anything about a
cross-field mesh's checkMesh quality (no session was built), the cost
estimates, and the self-branch consumption (read, not coded).
