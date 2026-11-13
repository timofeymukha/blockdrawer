"""One command per case: python experiments/scaffold_spike/run.py CASE [--n N] [--rotate DEG] [--zoom x0,y0,x1,y1]"""

from __future__ import annotations

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
import crossfield  # noqa: E402
import plot  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(cases.CASES))
    parser.add_argument("--n", type=int, default=320, help="pixels along the longer side")
    parser.add_argument("--rotate", type=float, default=0.0)
    parser.add_argument("--zoom", action="append", default=[], help="x0,y0,x1,y1 (repeatable)")
    parser.add_argument("--tag", default="")
    args = parser.parse_args(argv)
    case = cases.transformed(cases.CASES[args.case](), args.rotate)
    t = time.time()
    res = crossfield.run(case["loops"], n=args.n, name=args.case)
    res.stats["t_total"] = round(time.time() - t, 2)
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    tag = args.tag or f"{args.case}_n{args.n}" + (f"_r{args.rotate:g}" if args.rotate else "")
    plot.render(res, out / f"{tag}.png")
    for k, zoom in enumerate(args.zoom):
        window = tuple(float(v) for v in zoom.split(","))
        plot.render(res, out / f"{tag}_zoom{k}.png", window=window)
    data = {"case": args.case, "n": args.n, "rotate": args.rotate, "h": res.h, **res.stats,
            "singularity_list": [[round(x, 4), round(y, 4), idx] for x, y, idx in res.singularities]}
    (out / f"{tag}.json").write_text(json.dumps(data, indent=1))
    print(json.dumps({k: v for k, v in data.items() if k != "singularity_list"}))
    return res


if __name__ == "__main__":
    main()
