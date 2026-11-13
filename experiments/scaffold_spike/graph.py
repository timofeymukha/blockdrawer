"""From separatrices to a planar graph: nodes (singularities, corners, trace
ends snapped to the boundary, trace crossings), arcs between them, faces by
half-edge traversal, and per face its corners (turns above ``CORNER``) and
hanging nodes (turns below it: T-junctions a block layout would have to
extend a trace through).

python experiments/scaffold_spike/graph.py CASE [--n N]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
import crossfield  # noqa: E402

CORNER = math.radians(45.0)


def _project(point, loops):
    best = None
    for li, loop in enumerate(loops):
        a = loop
        b = np.roll(loop, -1, axis=0)
        d = b - a
        t = np.clip(((point - a) * d).sum(axis=1) / np.maximum((d * d).sum(axis=1), 1e-300), 0, 1)
        q = a + t[:, None] * d
        dist = np.linalg.norm(q - point, axis=1)
        k = int(np.argmin(dist))
        if best is None or dist[k] < best[0]:
            best = (dist[k], li, k, t[k], q[k])
    return best


def _intersect(p, q, r, s):
    d1, d2 = q - p, s - r
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-15:
        return None
    t = ((r[0] - p[0]) * d2[1] - (r[1] - p[1]) * d2[0]) / den
    u = ((r[0] - p[0]) * d1[1] - (r[1] - p[1]) * d1[0]) / den
    if 0 < t < 1 and 0 < u < 1:
        return t, u
    return None


def build(res):
    tol = 1.5 * res.h
    loops = res.loops
    # polylines with node positions along them: (param index, point)
    lines = []
    for tr in res.traces:
        pts = np.asarray(tr["points"], dtype=float)
        if len(pts) < 3:
            continue
        if tr["end"] == "boundary":
            _d, li, k, t, q = _project(pts[-1], loops)
            pts = np.vstack([pts, q])
        lines.append({"pts": pts, "cuts": [0.0, float(len(pts) - 1)]})
    # boundary polylines: each loop closed
    bounds = [{"pts": np.vstack([loop, loop[:1]]), "cuts": [], "closed": True} for loop in loops]
    # nodes from loop corners and trace endpoints on loops
    for c in res.corners:
        p = np.array(c[:2])
        _d, li, k, t, q = _project(p, loops)
        bounds[li]["cuts"].append(k + t)
    for line in lines:
        for end in (line["pts"][0], line["pts"][-1]):
            d, li, k, t, q = _project(end, loops)
            if d < tol:
                bounds[li]["cuts"].append(k + t)
    # trace-trace crossings (skip near the shared start)
    for a in range(len(lines)):
        for b in range(a + 1, len(lines)):
            P, Q = lines[a]["pts"], lines[b]["pts"]
            pmin, pmax = P.min(axis=0) - tol, P.max(axis=0) + tol
            qmin, qmax = Q.min(axis=0), Q.max(axis=0)
            if (qmin > pmax).any() or (qmax < pmin).any():
                continue
            for i in range(len(P) - 1):
                lo, hi = np.minimum(P[i], P[i + 1]), np.maximum(P[i], P[i + 1])
                cand = np.nonzero((np.minimum(Q[:-1], Q[1:]) <= hi).all(axis=1)
                                  & (np.maximum(Q[:-1], Q[1:]) >= lo).all(axis=1))[0]
                for j in cand:
                    hit = _intersect(P[i], P[i + 1], Q[j], Q[j + 1])
                    if hit is None:
                        continue
                    point = P[i] + hit[0] * (P[i + 1] - P[i])
                    if np.linalg.norm(point - P[0]) < 4 * res.h and np.linalg.norm(point - Q[0]) < 4 * res.h:
                        continue
                    lines[a]["cuts"].append(i + hit[0])
                    lines[b]["cuts"].append(j + hit[1])
    # node registry by position
    nodes = []

    def node(p):
        for k, q in enumerate(nodes):
            if np.linalg.norm(q - p) < tol:
                return k
        nodes.append(np.asarray(p, dtype=float))
        return len(nodes) - 1

    def at(pts, s):
        i = min(int(math.floor(s)), len(pts) - 2)
        return pts[i] + (s - i) * (pts[i + 1] - pts[i])

    arcs = []
    for poly in lines + bounds:
        pts = poly["pts"]
        cuts = sorted(set(round(c, 9) for c in poly["cuts"]))
        if poly.get("closed"):
            if not cuts:
                continue
            n = len(pts) - 1
            cuts = cuts + [cuts[0] + n]
        for s0, s1 in zip(cuts, cuts[1:]):
            a = node(at(pts, s0 % (len(pts) - 1)) if poly.get("closed") else at(pts, s0))
            b = node(at(pts, s1 % (len(pts) - 1)) if poly.get("closed") else at(pts, s1))
            inner = []
            k = int(math.floor(s0)) + 1
            while k < s1:
                inner.append(pts[k % (len(pts) - 1)] if poly.get("closed") else pts[k])
                k += 1
            path = [nodes[a]] + inner + [nodes[b]]
            if a == b and len(inner) < 2:
                continue
            arcs.append({"a": a, "b": b, "path": np.asarray(path), "boundary": bool(poly.get("closed"))})
    return nodes, arcs


def faces(nodes, arcs):
    """Half-edge face traversal; each face: list of (node, incoming dir, outgoing dir)."""
    out = {}
    halfs = []
    for k, arc in enumerate(arcs):
        for forward in (True, False):
            path = arc["path"] if forward else arc["path"][::-1]
            start = arc["a"] if forward else arc["b"]
            end = arc["b"] if forward else arc["a"]
            d0 = path[1] - path[0]
            d1 = path[-1] - path[-2]
            halfs.append({"arc": k, "start": start, "end": end, "out": math.atan2(d0[1], d0[0]),
                          "in": math.atan2(d1[1], d1[0]), "used": False, "path": path})
            out.setdefault(start, []).append(len(halfs) - 1)
    for start in out:
        out[start].sort(key=lambda h: halfs[h]["out"])
    result = []
    for h0 in range(len(halfs)):
        if halfs[h0]["used"]:
            continue
        face = []
        h = h0
        for _ in range(10000):
            halfs[h]["used"] = True
            end = halfs[h]["end"]
            back = (halfs[h]["in"] + math.pi)
            options = out[end]
            # next half-edge: the first clockwise from the reverse direction
            best = None
            for g in options:
                if halfs[g]["arc"] == halfs[h]["arc"] and halfs[g]["end"] == halfs[h]["start"] and len(options) > 1:
                    continue
                turn = (back - halfs[g]["out"]) % (2 * math.pi)
                if turn <= 1e-12:
                    turn = 2 * math.pi
                if best is None or turn < best[0]:
                    best = (turn, g)
            g = best[1]
            interior = best[0]
            face.append((end, interior))
            h = g
            if h == h0:
                break
        result.append(face)
    return result


def analyse(res):
    nodes, arcs = build(res)
    found = faces(nodes, arcs)
    stats = {"nodes": len(nodes), "arcs": len(arcs), "faces": len(found)}
    corners_hist = {}
    hanging = 0
    quads = 0
    for face in found:
        corners = sum(1 for _n, angle in face if angle < math.pi - CORNER)
        hang = sum(1 for _n, angle in face if abs(angle - math.pi) <= CORNER)
        hanging += hang
        corners_hist[corners] = corners_hist.get(corners, 0) + 1
        if corners == 4 and hang == 0:
            quads += 1
    stats.update({"face_corners": {str(k): v for k, v in sorted(corners_hist.items())},
                  "hanging_nodes": hanging, "clean_quads": quads})
    return nodes, arcs, found, stats


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(cases.CASES))
    parser.add_argument("--n", type=int, default=320)
    args = parser.parse_args(argv)
    res = crossfield.run(cases.CASES[args.case]()["loops"], n=args.n, name=args.case, with_partition=False)
    _nodes, _arcs, _faces, stats = analyse(res)
    print(json.dumps(stats))
    return stats


if __name__ == "__main__":
    main()
