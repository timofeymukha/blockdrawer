# The topology engine, per configuration

Open this when Step 2 of `SKILL.md` sends you to the engine
(`experiments/agentic_topology/research_cli.py`). Measured numbers here are
from the engine README's tables (`experiments/agentic_topology/README.md`,
"Results") unless marked otherwise; that README is the reference for the
algorithm and the open limits.

## Running it

```bash
python experiments/agentic_topology/research_cli.py run \
  --curve airfoil=airfoil.dat --wake --wake-direction 0.9744,0.2250 \
  --farfield-shape cshape --farfield-radius 15 --farfield-center 1,0 \
  --layer-height 0.15 --first-width 0.001 --core-size 1.0 \
  --session out/session.json --json out/report.json --output out/topology.png
```

- A run takes seconds on a synthetic body and minutes on an airfoil at
  fifteen chords (A-airfoil 263 s, NACA 0012 C-grid 320 s; NACA 4412 C-grid
  8.5 min on two cores at low priority): start it in the background and
  poll, or give it a timeout above that. `run` prints a line to stderr as
  each stage starts, with the seconds since the start (`[engine] 6.3 s
  grid_quality`; a trial's stages as `cgrid/sizing`).
- Give sizes in the geometry's units (`--first-width`, `--core-size`,
  `--layer-height`); the ratio forms multiply the whole domain's scale (the
  report's `domain.domain_scale`), which on a 15-chord far field gives a
  first cell of about 0.08 chords.
- Sessions come with `empty` z patches. A fabricated C-shaped far field is
  written as the patches `cap`, `top`, `bottom`, `outlet`, a rectangle as its
  four named sides: merge with `set_edge_boundary`, rename with
  `rename_boundary`.
- The `describe`, `cavities`, `candidates`, `apply --move` and `focus`
  subcommands choose among validated alternatives instead of placing
  vertices.
- `--smooth` runs `smooth mode=shape` before the engine sizes and `smooth`
  after: on the corpus it lowered checkMesh's maximum on every run it moved,
  turned `hull_tandem15` resolved and dropped `wake_blunt` from resolved to
  admissible (engine README, "Production smoother"; measured before shape
  mode's edge-length floor, `shape_length_ratio`, which now applies there
  too) - compare both verdicts.
  After the engine, the production steps (`SKILL.md` Steps 4-7) apply to its
  session like any other.

## The brief, as engine inputs

| In the brief | Engine input |
|---|---|
| far field: a circle of radius $R$ about a point | `--farfield-shape circle --farfield-radius R --farfield-center X,Y`; `--farfield-outlet DEGREES` cuts it into cap, legs and an outlet arc about the wake direction so the C-grid applies |
| far field: a C-shape, or a rectangle | `--farfield-shape cshape` / `rectangle`, same radius and centre |
| far field: these point lists, these names | `--outer NAME:ROLE=PATH` per chain, anticlockwise, joined end to end; the names become the patches |
| internal flow: wall / inlet / outlet lists | the same `--outer` chains and no `--curve`: the sweep producer builds it (roles `wall`, `inlet`, `outlet`, `symmetry`; a `cyclic` pair is refused there) |
| free-stream direction | `--wake-direction dx,dy` (also selects which sharp vertices get a wake) |
| first cell, band height, far cells | `--first-width W`, `--layer-height H`, `--core-size C` |
| wall-parallel stretching | `--wall-aspect 25` |
| which edge decides a count | `--count-rule max` (the finest demand in a component wins) |
| cell sizes continuous across blocks | `--interface-growth 1.25`; the engine's own sizing - the production `size` can replace it afterwards |

## Acceptance terms in the report

`topology_valid`, `grids_evaluated`, `untangled`, `within_shape_targets`,
`sizing_feasible`, `admissible`, `resolved`; refusals carry their reasons
(`trial_crashes` lists defects, never geometry). The shape grid (eight uniform
cells a block side) gives count-independent non-orthogonality, skewness and
wall misalignment; a fold check at 32 cells a side guards `untangled`, and
`acceptance.untangled_by_grid` says which grid folded - a fold on the counts
grid alone comes from the default counts, so change those, not the topology.
`sizing.feasibility.failures_without_exemptions` shows what the far-field
fan-out and wake-landing exemptions waive. With `--no-grid-quality` the
annular result is not sampled: its grid terms are false and no session is
written; an opt-in trial (`--wake`, the C-grid, `--hull`, `--span-gaps`)
always samples its own grids. These terms exist only for engine output.

## Inputs that should not matter

On the thirteen fixtures, orientation, start point and point density do not
change the block graph; a 10:1 ellipse densified and a coarse NACA 0012
reversed still end in a different graph (`HeldOutEquivalentInputTests`,
expected failures). When a run is refused or unresolved, retry with the list
reversed and with a shifted start before changing anything else, keep the
best verdict, and judge a candidate in the orientation you export.

## Per configuration

- **Smooth body, external flow**: an O-grid - wall band, core to a ring, far
  field - inside the given far field; in a rectangle each corner is a gate
  whose spoke follows the corner bisector (two blocks meet it at 45 degrees;
  the classical O-in-a-box). Ellipses up to 12:1 are admissible with pinned
  tips, a 15:1 ellipse fails its band (engine README, layout notes). Circle
  in a square box: 12 blocks, resolved, Mesh OK 42.8.
- **Sharp trailing edge** (fluid sector of at least 250 degrees): `--wake`, a
  C-grid. The band continues as a two-sided wake band to the outlet, its
  lines spreading at `--wake-spread` (default 0.05); the edge carries four
  blocks at right angles. Give `--wake-direction` whenever the body has
  another sharp vertex, or every sharp vertex gets a wake along its own
  bisector. The C-grid producer needs one body, one wake and a
  cap-leg-outlet-leg boundary (`--farfield-shape cshape`, or a circle cut by
  `--farfield-outlet`); refusals are under `medial.cgrid`. Measured: A-airfoil
  200 blocks resolved, Mesh OK 45.1, at every raster width 420-1000 (block
  count 200-205); NACA 0012 185 and NACA 4412 at 8 degrees 225 blocks,
  resolved. A rectangle or circle without the cut falls back to the annular
  construction, admissible only to about six chords of far field. A smooth
  body that misses only its wall misalignment can take a tighter
  `--max-wall-turning`.
- **Trailing edge upstream of another body** (tandem foils, slat ahead of a
  main): the wake band ends on the downstream body's band at its stagnation
  gate (the plan record's `target` says `body`); a wake that would graze the
  band is aimed, and refused below 35 degrees of incidence
  (`landing_incidence_degrees`). `make research-tandem`: 51 blocks,
  admissible, Mesh OK 69.1.
- **Several bodies far inside the far field**: `--hull` - the near field
  inside the cluster's level set, the far field as level sets beyond, cut
  along the wake leaving the cluster; refusals under `medial.hull`.
  `make research-tandem-15c`: 122 blocks, admissible, Mesh OK 55.4. Two or
  more exiting wakes merge into one band (`medial.hull.merged_wakes`), else
  end at the hull (`ending_wakes`), else are dropped (`dropped_wakes`).
  A plain (annular) run on three cylinders crashed with `voronoi_graph:
  TraceError: the bisector walk stalled`; the same geometry with `--hull`
  built (one trial, benchmark round 2): on that crash retry with `--hull`
  before changing the geometry.
- **Blunt base** (two convex corners within about two percent of the
  perimeter): the base template, record `kind` `base`;
  `make research-blunt-foil`: 18 blocks, resolved, Mesh OK 77.6. A flat
  back just beyond the rule is refused with the remedy
  `--wake-blunt-ratio`.
- **Edge whose bisector is off the free stream** (deflected flap base,
  drooped slat edge): the wake leaves along the bisector and turns at
  stations of at most `--wake-turn` degrees (default 50), inside the ring
  when they fit, else beyond it, else straight; the record says `turned`,
  `stations`, `turn_placement`, `turn_note`. A refusal asks for a larger
  `--wake-turn` or a farther ring (`--hull-height-ratio`).
- **Wake over another body** (a main's wake over its flap): `--wake --hull`;
  a straight chord through the passed body's cell, three spokes to its band,
  the lens as three quads round a centre vertex (record `through`, `lens`).
  `make research-staggered`: 160 blocks, admissible, Mesh OK 62.1.
- **Multi-element (30P30N)**: `--wake --hull --farfield-shape cshape
  --farfield-radius 15 --farfield-center 0.8,0 --layer-height 0.03
  --first-width 0.001 --core-size 1.0` builds the session
  `30p30n/30p30n-engine.session.json`: 203 blocks, admissible, not resolved
  (old skill; the engine README's table, older, lists 30P30N with default
  options as inadmissible). Measured in this review: 5,486 cells, Mesh OK,
  max 77.9, 60 faces above 70, skewness 3.1; `smooth` takes it to 68.7 with
  none (README, `smooth`). Defects left: the cove seam sliver (v12-v13,
  0.0019 long, b21/b31), the seam wedge at the cove's reflex corner (b39),
  the slot strips carrying the band count (component of v128-v84). Sizing
  it: `sizing.md`; repairing: `failures.md`.
- **Internal flow** (channel, periodic hill, diffuser): a sweep between the
  walls; `straight_channel` and `periodic_hill` resolve (Mesh OK 0.0 and
  36.2). A reflex corner on a guide wall - a backward-facing step, any
  L-shaped channel - is refused (`sweep_correspondence: ribs cross or
  invert`, the join named under `reflex_joins`): build it with the operators
  (`examples/backward_step.txt`).
- **Concave corner** (fluid sector below about 157 degrees): a gate whose
  spoke runs along the bisector, the two blocks beside it at 45 degrees.
  The band keeps the corner's height along each wall in the mitre shadow.
- **Singularities** (interior valence 3 or 5) sit where fans, wakes and gap
  strips need them; their total index is fixed by the domain's Euler
  characteristic. They are also the only place a cell count turns: in a
  conformal H-grid every transverse count runs the whole channel.
