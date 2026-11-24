# How a benchmark trial is run

You mesh one case with BlockDrawer, working from the repository's meshing
skill. The trial measures the workflow as it is, so what you could not do, and
what cost you time, is as valuable as the mesh.

## Before you start

1. Run `date` and note the start time.
2. Read `/tmp/timofey/code/blockDrawer/.claude/skills/mesh-blocking/SKILL.md`
   and follow its steps. Open its reference files when a step sends you there.
3. Read the case brief: `CASE/README.md` and `CASE/geometry_preview.png`.

## Tools

- `BENCH=/tmp/timofey/data/blockdrawer/benchmark`
- `$BENCH/bin/bd SUBCOMMAND ...` is `blockdrawer-cli` (where the skill writes
  `blockdrawer-cli X`, run `$BENCH/bin/bd X`). OpenFOAM is already set up
  inside it, so `bd check` runs `blockMesh` and `checkMesh`.
- `$BENCH/bin/lp COMMAND ...` runs anything else (Python, the topology
  engine's `research_cli.py`) on the same two cores at low priority.
- Use the command line, not the BlockDrawer MCP tools.

## Rules

- **Low profile.** This is a shared machine. Run every Python, BlockDrawer and
  OpenFOAM command through `bd` or `lp`; one process at a time; nothing in the
  background in parallel; no test suites; at most two engine runs.
- **Tools as they are.** Do not edit anything under
  `/tmp/timofey/code/blockDrawer` and do not use git.
- **Where to write.** Deliverables in `CASE/TRIAL/` (`TRIAL` is named in your
  launch message, e.g. `trial2`); scratch in
  `/tmp/garbage/blockdrawer-bench/CASE/TRIAL/`. Nowhere else.
  Renders that show cells need `bd render ... --preview --no-nodes
  --no-control-points`.
- **What not to read.** Any `trial*` directory other than your own - other
  cases' and this case's earlier ones - the `ROUND*.md` files, and scratch
  left by earlier trials under `/tmp/garbage`. The skill's own `examples/`
  are part of the workflow and are fine.
- **Waiting.** A long command (an engine run, a `size` on a large session)
  runs in the foreground with a timeout of up to 10 minutes; never in the
  background with a polling loop. The shell may still move a
  foreground command to the background after about three minutes: then wait
  for its notification and start nothing else meanwhile.
- **Time cap.** Given in your launch message. When you reach it, stop and
  deliver the best state you have, with what is still wrong.

## Targets

- Patches: one per chain, named after the chain's file; `wall` chains and
  bodies of type `wall`, `inlet`/`outlet`/`farfield` of type `patch`, a
  `cyclic` pair as reciprocal `cyclic` patches. No unassigned exterior edge.
- The topology requirements in the brief.
- The brief's sizing inputs through the skill's sizing step.
- `checkMesh`: `Mesh OK`, no face above 70 degrees of non-orthogonality,
  maximum skewness under 4.
- Cell-size jumps across block interfaces: none above 5; report how many are
  above 2.5.
- Economy: `$BENCH/REFERENCE.md` gives, for most cases, the cells
  and the average non-orthogonality of the best result known. More than 1.5
  times its cells, or an average more than 1.5 times its average plus 2
  degrees, is a finding: report it with its cause (which blocks, which
  choice of topology or sizing). It is not a reason to pass the time cap.

A target you miss is a finding: report it with its numbers, do not hide it.

## Deliver in `CASE/TRIAL/`

- `session.json` - the final session.
- `build.txt` - the command batch (or batches, in order) that regenerates the
  session from `bd new`. If you had to compute vertices in a script of your
  own, deliver the script instead and say so in the report. For the engine route: the engine
  command as comment lines, then the batch applied to its session.
- `case/` - the kept case with `blockMeshDict`, `log.blockMesh` and
  `log.checkMesh` (`bd check SESSION --case DIR`).
- Renders: one overview and a close-up of every corner, gap and wake start.
- The report, as the text of your final message (do not write it to a file;
  it is filed for you), with these sections:
  1. **Route** - operators by hand, engine, or a script of your own, and why.
  2. **Topology** - outward from the walls, in a few lines.
  3. **Numbers** - a table: blocks, cells and `field_cells` (from `size`),
     `Mesh OK` or not, faces above 70 degrees, maximum and average
     non-orthogonality (cells and average also as a ratio to the case's row
     in `REFERENCE.md`), maximum skewness, maximum aspect ratio (checkMesh),
     the corner-angle range (`quality`), the first cell width reached on
     each wall patch against the one asked (smallest and largest), interface
     jumps above 2.5 / 5 / 20 and the worst one.
  4. **Requirements** - each requirement of the brief: met or not, and where
     to see it.
  5. **Effort** - start and end time, number of commands in the final batch,
     number of batches you applied in total (failed ones included), engine
     runs.
  6. **Script fallback** - whether you wrote your own vertex computations, for
     which region, and why no operator could state it.
  7. **Gaps** - a numbered list of everything that cost you time or quality.
     For each: what you were trying to do, the command and the relevant output
     (verbatim, short), roughly how many minutes it cost, and what would have
     removed it (mark this as your suggestion). Include skill text that was
     wrong, missing or misleading, with the file and line.
  8. **What worked** - briefly, the steps that needed no second attempt.
