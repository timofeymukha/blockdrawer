"""Part 2: the full medial axis of the fluid domain, including the branches a
body has with itself, from the Voronoi diagram of dense boundary samples
(``scipy.spatial.Voronoi``).

Pruning (stated, one rule): a Voronoi edge is kept when both its vertices lie
in the fluid and its two generating samples are either on different loops,
or on the same loop with an arc-length separation along that loop larger
than ``RHO`` times the local radius (distance from the edge to its
generators). Edges from the same loop are the *self-branches* the engine's
one-site-per-loop diagram does not have.

python experiments/scaffold_spike/medial.py CASE [--spacing S] [--rotate DEG] [--zoom x0,y0,x1,y1]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
from crossfield import _inside  # noqa: E402

RHO = 3.0


def medial(loops, spacing, rho=None):
    rho = RHO if rho is None else rho
    from scipy.spatial import Voronoi

    loops = cases.oriented([np.asarray(loop, dtype=float) for loop in loops])
    samples, owner, arc, perimeter = [], [], [], []
    for li, loop in enumerate(loops):
        dense = cases._resample(loop, spacing)
        seg = np.linalg.norm(np.roll(dense, -1, axis=0) - dense, axis=1)
        s = np.concatenate(([0.0], np.cumsum(seg)[:-1]))
        samples.append(dense)
        owner.append(np.full(len(dense), li))
        arc.append(s)
        perimeter.append(seg.sum())
    points = np.vstack(samples)
    owner = np.concatenate(owner)
    arc = np.concatenate(arc)
    vor = Voronoi(points)
    inside = _inside(vor.vertices, loops)
    kept = []
    for (i, j), (a, b) in zip(vor.ridge_points, vor.ridge_vertices):
        if a < 0 or b < 0 or not (inside[a] and inside[b]):
            continue
        pa, pb = vor.vertices[a], vor.vertices[b]
        mid = 0.5 * (pa + pb)
        radius = np.linalg.norm(points[i] - mid)
        if owner[i] != owner[j]:
            kept.append((pa, pb, "between", radius))
            continue
        d = abs(arc[i] - arc[j])
        d = min(d, perimeter[owner[i]] - d)
        if d > rho * radius:
            kept.append((pa, pb, "self", radius))
    return loops, kept


def render(loops, kept, path, window=None, width=1600):
    allpts = np.vstack(loops)
    x0, y0 = allpts.min(axis=0)
    x1, y1 = allpts.max(axis=0)
    if window:
        x0, y0, x1, y1 = window
    scale = width / (x1 - x0)
    height = max(50, int((y1 - y0) * scale))
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def px(p):
        return ((p[0] - x0) * scale, height - (p[1] - y0) * scale)

    for loop in loops:
        draw.polygon([px(p) for p in loop], outline=(0, 0, 0))
    for pa, pb, kind, _r in kept:
        draw.line([px(pa), px(pb)], fill=(220, 0, 0) if kind == "self" else (0, 90, 220), width=2)
    img.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(cases.CASES))
    parser.add_argument("--spacing", type=float, default=0.01)
    parser.add_argument("--rotate", type=float, default=0.0)
    parser.add_argument("--zoom", action="append", default=[])
    parser.add_argument("--tag", default="")
    parser.add_argument("--rho", type=float, default=RHO)
    args = parser.parse_args(argv)
    case = cases.transformed(cases.CASES[args.case](), args.rotate)
    t = time.time()
    loops, kept = medial(case["loops"], args.spacing, args.rho)
    elapsed = time.time() - t
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    tag = args.tag or (f"medial_{args.case}_s{args.spacing:g}_rho{args.rho:g}"
                       + (f"_r{args.rotate:g}" if args.rotate else ""))
    render(loops, kept, out / f"{tag}.png")
    zooms = {}
    for k, zoom in enumerate(args.zoom):
        window = tuple(float(v) for v in zoom.split(","))
        render(loops, kept, out / f"{tag}_zoom{k}.png", window=window)
        # self-branch length inside the window (rotated back for comparisons)
        if args.rotate:
            a = -math.radians(args.rotate)
            rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
            centre = case["rotation"][1]
            back = lambda p: (p - centre) @ rot.T + centre  # noqa: E731
        else:
            back = lambda p: p  # noqa: E731
        x0, y0, x1, y1 = window
        length = 0.0
        for pa, pb, kind, _r in kept:
            if kind != "self":
                continue
            m = back(0.5 * (pa + pb))
            if x0 <= m[0] <= x1 and y0 <= m[1] <= y1:
                length += float(np.linalg.norm(pb - pa))
        zooms[zoom] = round(length, 4)
    data = {
        "case": args.case, "spacing": args.spacing, "rotate": args.rotate, "rho": args.rho, "t": round(elapsed, 2),
        "self_length": round(sum(float(np.linalg.norm(b - a)) for a, b, k, _ in kept if k == "self"), 4),
        "between_length": round(sum(float(np.linalg.norm(b - a)) for a, b, k, _ in kept if k == "between"), 4),
        "self_length_in_windows": zooms,
    }
    (out / f"{tag}.json").write_text(json.dumps(data, indent=1))
    print(json.dumps(data))


if __name__ == "__main__":
    main()
