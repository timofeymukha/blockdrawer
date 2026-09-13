# Agentic topology experiments

Research prototypes that are intentionally separate from BlockDrawer's
standard-library-only runtime.  They have to earn their way into the model and
command API through concrete geometry experiments first.

## What this prototype does

`colored_medial_axis.py` turns an arbitrary finite collection of disjoint closed
2D point-list bodies into a **conformal all-quadrilateral block topology** and
writes it as an ordinary, editable BlockDrawer session.

```text
python experiments/agentic_topology/fetch_30p30n.py
python experiments/agentic_topology/colored_medial_axis.py \
  --curve slat=experiments/agentic_topology/geometry/30P-30N-Slat-Normalized.dat \
  --curve main=experiments/agentic_topology/geometry/30P-30N-Main-Normalized.dat \
  --curve flap=experiments/agentic_topology/geometry/30P-30N-Flap-Normalized.dat \
  --width 900 \
  --output output/30p30n.png \
  --json output/30p30n.json \
  --session output/30p30n-session.json \
  --session-render output/30p30n-session.png \
  --block-mesh-dict output/blockMeshDict
```

`make research-30p30n` runs exactly that, and `make research-test` runs the
self-tests.  The exit code is 1 when any region is left unresolved; the PNG and
the JSON are still written so the failure can be inspected.

## Pipeline

1. **Sites and domain frame** (`sites.py`).  Bodies are closed point lists,
   normalised to anticlockwise winding with their points kept verbatim.  The
   domain centre is the arc-length weighted centroid of every boundary and the
   domain scale is twice the largest distance from it, so both follow
   translation, rotation and uniform scaling.  The trial farfield is a circle by
   default (rotation invariant, exported as exact arcs); `--farfield-shape
   rectangle` is available.  Bodies are then sorted into a canonical, purely
   geometric order so a permuted input produces the same session.

2. **Generalized Voronoi graph** (`voronoi_graph.py`).  A raster of nearest-site
   labels decides *connectivity only*: how many junctions exist, which sites
   meet at each one and which site pairs own a bisector branch.  Every
   coordinate is then recomputed analytically - junctions by Gauss-Newton on the
   equidistance residuals (residual ~1e-16 of the scale), branches by a
   predictor/corrector walk along the exact bisector with a step proportional to
   the local clearance.  The result is resolution independent inside the range
   where the raster resolves the same graph.

3. **Annular cells and four-sided patches** (`block_layout.py`).  Each Voronoi
   cell is an annulus between its own boundary (the *wall*) and a closed chain
   of branches (the *ring*).  Cutting the annulus at ordered *anchors* produces
   four-sided patches: wall section, spoke, ring section, spoke.  An anchor
   belongs to a branch, so the two cells sharing that branch see the same
   station and the layout is conformal by construction.  Anchors come from
   geometry only: junctions, sharp wall corners, wall curvature extrema, and -
   where a patch still turns too far - the station that halves its wall turning.
   Anchor separation is measured against the *local clearance*, so a narrow gap
   may carry closely spaced spokes while an open wall may not.

4. **Gate relaxation.**  Plain closest-point projection collapses at a sharp
   convex corner, because the corner is the closest wall point of a whole
   angular sector.  Gates are therefore pinned wherever they come from a wall
   feature and spread - keeping the ring order and a local minimum separation -
   wherever the projection collapsed.  A pinned gate that contradicts the ring
   order gives up its pin, and a genuinely non-monotone correspondence is
   reported rather than guessed.

5. **Relaxation and splitting** (`patch_solver.py`).  Every ring anchor and gate
   vertex is one bound-constrained design variable on its own curve.  Bounded
   coordinate descent minimises a scale-free objective built from

   * the scaled Jacobian of each block (inverted-cell barrier plus minimum
     corner angle),
   * an aspect-ratio penalty above a dimensionless limit,
   * the angle between each spoke and the wall normal into the fluid,
   * the spoke length against the local clearance.

   Only when relaxation has converged and a block is still inadmissible does the
   solver change the discrete graph, by inserting one more anchor inside the
   worst patch.  The search therefore returns the simplest valid layout rather
   than the one with the most blocks, and reports structured rejection reasons
   when none is valid.

6. **Block complex and session** (`block_complex.py`, `session_emit.py`).
   Vertices carry an identity shared by everything that touches them, so there
   are no hanging vertices and no duplicate parallel edges.  An independent
   raster coverage check confirms that the blocks tile the fluid region exactly -
   no uncovered core, no overlap, nothing outside the domain - because
   `MeshModel.validate()` is not a planar coverage test.  Only then is a
   `MeshModel` built, validated, saved, reloaded, validated again, rendered
   headlessly and serialised to `blockMeshDict`.  Wall edges keep the supplied
   point list as `polyLine` (or `spline`) data, farfield edges are exact arcs,
   and each body's point list is also attached as a reference curve.

## Result on 30P30N

The normalized slat/main/flap curves give a stable **four-junction,
six-branch** generalized Voronoi graph at raster widths 600, 700, 900 and 1200.
The layout converges with **no anchor splits**: relaxation alone resolves both
regions that the earlier strip matcher rejected - the slat side of the
slat/main interface and the main-element side of the main/flap interface.

| raster width | blocks | junctions | branches | min scaled Jacobian | corner angles   |
| ------------ | ------ | --------- | -------- | ------------------- | --------------- |
| 600          | 42     | 4         | 6        | 0.051               | 9.1 .. 177.1 deg |
| 700          | 46     | 4         | 6        | 0.056               | 9.1 .. 176.8 deg |
| 900          | 44     | 4         | 6        | 0.058               | 9.1 .. 176.7 deg |
| 1200         | 44     | 4         | 6        | 0.057               | 9.1 .. 176.7 deg |
| 1500         | 44     | 4         | 6        | 0.057               | 9.1 .. 176.7 deg |

The only topology singularities are the four Voronoi junctions, where six
blocks meet.  Everything else is a regular interior vertex (four blocks) or a
regular boundary vertex (two blocks).  Coverage is exact, the session reloads
and validates, and `blockMeshDict` export succeeds.

The widest corner sits on a sharp trailing edge and is geometric, not a solver
artefact: the slat cusp has an 8 degree included angle, so the fluid sees 352
degrees at that vertex and the two blocks that share it get about 176 degrees
each.  Moving the gate off the cusp would improve the number and put a kink
inside a block edge instead, which is worse; a sharp corner therefore stays a
block corner, and the gate is released again only when a valid layout is
otherwise impossible.

## Generality

No step looks at component names, counts, input order, winding, coordinates or
an assumed chordwise direction.  The self-tests cover one ellipse, two circles
of unequal radius, three rotated ellipses with unequal gaps, a concave body
paired with a convex one, and four bodies; plus permuted, reversed, translated,
rotated and 1000x scaled copies.  Topology signatures are identical across all
of those and the dimensionless quality agrees to within the raster seed noise.

## Dependencies and limits

The prototype needs NumPy and Pillow.  They are research dependencies only and
must not become BlockDrawer runtime dependencies.  No `numpy.linalg`
factorisation and no BLAS level-3 product is used: `linalg_lite.py` carries a
small Gaussian elimination and a conjugate-gradient Laplacian solve, so the
prototype also runs on installations whose LAPACK/BLAS build is broken.

Known limits:

* one block per patch and uniform cell counts - physical boundary-layer grading
  is out of scope here;
* sites are complete boundary components, so a body's own medial branches are
  handled by patch splitting rather than by the graph;
* spokes are straight chords between a wall gate and its ring anchor;
* a Voronoi cell must be a single annulus; several disjoint ring components are
  reported as a structured layout error rather than guessed.

## Files

| file | role |
| ---- | ---- |
| `colored_medial_axis.py` | command line front end, analysis picture, JSON report |
| `pipeline.py` | end-to-end run and the JSON report contents |
| `sites.py` | boundary curves, domain frame, canonical body order |
| `voronoi_graph.py` | raster connectivity, exact junctions and branch tracing |
| `block_layout.py` | annular cells, anchors, gates, four-sided patches |
| `patch_solver.py` | relaxation objective, bounded coordinate descent, splitting |
| `block_complex.py` | conformal vertex/edge/block complex, coverage check |
| `session_emit.py` | `MeshModel` construction, session IO, quality summary |
| `analysis_plot.py` | Pillow rendering of the analysis picture |
| `geometry2d.py`, `linalg_lite.py` | vectorised geometry and small solvers |
| `synthetic_cases.py`, `test_agentic_topology.py` | generated geometries and self-tests |
| `fetch_30p30n.py` | downloads the third-party acceptance geometry |

Downloaded geometry (`geometry/`) and generated artifacts (`output/`) are
git-ignored.
