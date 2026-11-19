# Three worked examples

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
| `grooved_tandem.txt` | polar patches with grooves left in the body, `add_band ... to_box` to a square, an H-grid conforming to both squares | 14 | 251 blocks; shape, size, polish: 47,616 cells, Mesh OK, max 20.5 deg (the hand-scripted mesh: 62,712 cells, 42.7) |
| `naca0012.txt` | `add_ring` with a trailing-edge fan, two mitred bands, `to_box` to the far field, a placeholder first block removed | 12 | 190 blocks; shape, size, polish: 59,908 cells at a first width of 1e-4 chords, Mesh OK, max 48.6 deg |

Measured when the operators were added (the commit that introduced them;
the README's "Construction operators" paragraph carries the same numbers),
with BlockDrawer's `check` on OpenFOAM v2606. Rebuilt independently with
other sizes: grooved tandem `size 0.002 0.5 growth=1.15 wall_aspect=10`
117,085 cells, Mesh OK, max 23.3; NACA `size 1e-4 1.0 growth=1.15
wall_aspect=20` 129,984 cells, Mesh OK, max 48.5.

The NACA example is a topology exercise: its trailing edge is a fan, which
passes checkMesh but collapses the body-normal lines into a downstream fan.
For a production wake round a single body in an external far field the
engine's C-grid builds one (`../engine.md`). Inside a channel or a periodic
passage (route rule 1: operators) two round-2 trials built a wake band by
hand: `add_ring`, the fan's middle spoke removed (`remove_edge`), wake
strips to the outlet from `add_vertex` and `add_block_from_vertices`, then
fills (and, in the tunnel, conforming grids) round them (airfoil in a tunnel: 208 blocks, Mesh
OK, max 31.0 deg; compressor cascade: 56 blocks, max 60.1).

Files: `cylinder1.dat`, `cylinder2.dat` (D = 1, six 0.25-deep grooves, centres
(0, 0) and (3, 0), from the grooved tandem trial), `naca0012_a5.dat` (NACA
0012, closed trailing edge, chord 1, rotated 5 degrees about x = 0.25).
