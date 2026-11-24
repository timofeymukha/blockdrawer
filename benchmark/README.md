# Agent-workflow benchmark: the record

The held-out benchmark behind the meshing skill
(`.claude/skills/mesh-blocking/`) and the measurements the main README
quotes as "benchmark round N". One fresh agent meshes one case from the
skill alone; the reviewer reruns `checkMesh` on what it delivers, files the
agent's report, and a tool or skill change goes in only when two unrelated
cases need it or it is a general primitive. Six rounds, Nov 18-23 2026.

## What is here

| file | what it is |
|---|---|
| `TRIAL.md` | the protocol every trial agent is given: tools, rules, targets, deliverables, report sections |
| `REFERENCE.md` | per case, the best result known that meets the targets (the economy target is read against it) |
| `ROUND1.md` ... `ROUND6.md` | one ledger per round: results, gaps per case, recurring gaps, agent-to-agent spread, and "Fixes after round N" with each fix batch's rule and verification numbers |
| `make_cases.py` | generates every case's `geometry/*.dat`, `geometry_preview.png` and `README.md` (the brief); needs NumPy and Matplotlib |
| `bin/lp`, `bin/bd`, `bin/verify` | the low-profile runner (two pinned cores, lowest priority, OpenFOAM on the path), the CLI through it, and the reviewer's re-check of a trial |
| `patches/` | two patches the ledgers measured and did not take (`ROUND2.md`, `ROUND3.md`, `ROUND4.md` refer to them) |

## What is not here

The case folders - generated geometry and every trial's deliverables
(`CASE/trialN/`: session, command batch, kept OpenFOAM case, renders), about
2 GB - stay where the benchmark ran, `/tmp/timofey/data/blockdrawer/benchmark/`.
The ledgers, `TRIAL.md` and the wrappers name that location, the repository
at `/tmp/timofey/code/blockDrawer` and scratch under `/tmp/garbage/`: they are
a record of what was run, not portable scripts. To run it elsewhere, copy
this folder, edit the paths in `bin/lp` (OpenFOAM's `bashrc`, `PYTHONPATH`,
the cores given to `taskset`) and `BENCH` in `TRIAL.md`, and run
`bin/lp python3 make_cases.py`.

## The rounds

| round | code | cases | trials | cases meeting every target |
|---|---|---|---|---|
| 1 | 399bc5d | 8 | 8 | see `ROUND1.md` (targets were extended after it) |
| 2 | 9ee981f | 11 | 11 | see `ROUND2.md` |
| 3 | 3a8540d | 13 | 21 | 12 |
| 4 | 657cef2 | 16 | 20 | 14 |
| 5 | 0a83d82 / f8e7dfd | 19 | 21 | 18 |
| 6 | 49ffc0d | 22 | 24 | 21 |

Each round added cases with no row in the skill's route table, no example
and no reference row; in rounds 4, 5 and 6 those passed at first trial. A
round's code is the commit its trials ran on; the fixes it led to are in
its own ledger and are the next round's code.

## Where it stopped

`ROUND6.md`, "Fixes after round 6" and "Where the loop stops": batch 14
(commits 1948a6e to a6a1b8e) was verified on the builds of rounds 5 and 6
and has not been tested on unseen cases - a round 7 would be its first such
test. Open, in order: the high-lift case (uneven between agents, slow above
about 1500 blocks), the splitter plate's mitre spoke, the remaining
construction candidates.
