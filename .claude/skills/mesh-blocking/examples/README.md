# Four worked examples

Run one end to end before your own case. Each batch file says how to start
(`#!` line), the build commands, the follow-on lines (`#>`) and the
checkMesh result it reached. Run from this folder (the curve files are
beside the batches):

```bash
blockdrawer-cli new /tmp/x/step.json --box -5 0 0 0.15
blockdrawer-cli apply /tmp/x/step.json --in-place --quiet -f backward_step.txt
blockdrawer-cli apply /tmp/x/step.json --in-place --quiet "size 0.005 0.25 growth=1.1 wall_aspect=30"
blockdrawer-cli check /tmp/x/step.json
```

| Batch | What it shows | Commands | Result (checkMesh) |
|---|---|---|---|
| `backward_step.txt` | an H-grid from station lists with one skip box; six patches by box | 8 | 27 blocks; `size`: 21,750 cells, Mesh OK, 0 deg |
| `grooved_tandem.txt` | polar patches with grooves left in the body, `add_band ... to_box` to a square, an H-grid conforming to both squares | 14 | 251 blocks; shape, size, polish: 47,616 cells, Mesh OK, max 20.7 deg (the hand-scripted mesh: 62,712 cells, 42.7) |
| `naca0012.txt` | `add_ring` with a trailing-edge fan, two mitred bands, `to_box` to the far field, a placeholder first block removed | 12 | 190 blocks; shape, size, polish: 60,976 cells at a first width of 1e-4 chords, Mesh OK, max 49.6 deg |
| `naca0012_cgrid.txt` | the same airfoil as a C-grid: `add_ring ... fan=false` (the trailing edge open), the wake band by `add_rows ... spread=10` to the outlet, the far field as `add_rows` from the front to the inlet, top and bottom with `far_ratio=2` (the far row's own distribution: neighbouring columns within 2 times), corner vertices by `add_vertex x_of= y_of=`, two `add_fill` | 18 | 443 blocks; shape, size (first width 5e-4), polish: 14,469 cells, Mesh OK, max 37.7 deg, skewness 0.61, aspect 111. At the fan example's 1e-4: 38,300 cells, Mesh OK, 0 faces above 70, max 55.3 deg, skewness 0.69, aspect 376. Without `far_ratio` the far row is the chain's image: the straight columns above the trailing edge (0.05 wide) carried the wake's first streamwise cell (4e-4) to the top - 14,393 cells, max 38.1, aspect 308 at 5e-4; 41,152 cells, 11 faces above 70, max 76.2, skewness 0.89, aspect 978 at 1e-4 |

Measured when the operators were added (the commit that introduced them;
the README's "Construction operators" paragraph carries those numbers; the
NACA and grooved-tandem rows re-measured with `grading=field` the default,
the NACA row again when the ring stopped writing splines that run
backwards, the C-grid row when its far field took `far_ratio=2`), with BlockDrawer's `check` on OpenFOAM v2606. Rebuilt independently with
other sizes: grooved tandem `size 0.002 0.5 growth=1.15 wall_aspect=10`
117,085 cells, Mesh OK, max 23.3; NACA `size 1e-4 1.0 growth=1.15
wall_aspect=20` 129,984 cells, Mesh OK, max 48.5.

The fan example passes checkMesh but collapses the body-normal lines into a
downstream fan. A wake band by hand is `naca0012_cgrid.txt`: the trailing
edge left open (`fan=false`), its two spokes carried to the outlet
(`add_rows`). Two round-2 trials did it before these commands existed - the
fan's middle spoke removed, wake vertices and blocks typed - and both
re-expressed with them build the same blocks (airfoil in a tunnel: 31
commands for 36, Mesh OK, max 25.4 deg; cascade: 37 for 52, max 60.6). For a
single body in an external C-shaped far field the engine's C-grid is the
measured alternative (`../engine.md`).

Files: `cylinder1.dat`, `cylinder2.dat` (D = 1, six 0.25-deep grooves, centres
(0, 0) and (3, 0), from the grooved tandem trial), `naca0012_a5.dat` (NACA
0012, closed trailing edge, chord 1, rotated 5 degrees about x = 0.25).
