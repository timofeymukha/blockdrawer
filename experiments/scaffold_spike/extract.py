"""Item 8b: from the cross field to a BlockDrawer session.

1. **Wall band, clipped.** Each body is offset by the band height ``H`` along
   its vertex normals (a plain mitred offset; at a convex corner sharper than
   ``FAN_TURN`` the offset is a round join of radius ``H``). The cross field is
   solved and traced only outside these fronts, so no separatrix can hug a
   wall. The band is one row of blocks between wall and front, cut wherever
   a node of the outer layout lands on the front; a round join gets
   ``ceil(turn / 90 deg)`` kite blocks, whose front nodes are forced.
2. **Merge.** One length scale, ``MERGE = 0.5 H``: graph nodes closer than
   it are one node, a trace ending within it of a node ends at that node.
3. **Conformal quads.** A node at which a fluid face turns by less than 45
   degrees (a T-junction) is extended: a trace leaves it into the face
   along the cross direction closest to the face side's normal, and runs to
   a boundary or a singularity - the split propagation a block model does.
   Repeated until no face has one.
4. **Session.** Nodes become vertices, four-corner faces blocks, arcs edges
   (polyLine through the trace, line when straight), band blocks between
   wall stations and front nodes (wall edges are splines through the body's
   points), patches by curve and box.

python experiments/scaffold_spike/extract.py CASE [--n N] [--band H] [--rotate DEG] [--out SESSION]
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
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import cases  # noqa: E402
import crossfield  # noqa: E402
from graph import _intersect, _project  # noqa: E402

FAN_TURN = math.radians(60.0)
HANG = math.radians(45.0)


# ---------------------------------------------------------------------------
# 1. fronts
# ---------------------------------------------------------------------------


def front_of(body: np.ndarray, H: float):
    """Offset of a body loop (fluid on its left) by H. Returns front points,
    their wall parameter (index along the body, float) and forced nodes."""
    n = len(body)
    pts, param, forced = [], [], []
    for i in range(n):
        a, p, b = body[i - 1], body[i], body[(i + 1) % n]
        t1 = (p - a) / np.linalg.norm(p - a)
        t2 = (b - p) / np.linalg.norm(b - p)
        n1 = np.array([-t1[1], t1[0]])
        n2 = np.array([-t2[1], t2[0]])
        turn = math.atan2(t1[0] * t2[1] - t1[1] * t2[0], t1 @ t2)
        # fluid on the left: a right turn (negative) is convex for the body
        if -turn > FAN_TURN:
            sweep = -turn
            a1 = math.atan2(n1[1], n1[0])
            steps = max(2, int(math.ceil(math.degrees(sweep) / 5.0)))
            sectors = int(math.ceil(sweep / (math.pi / 2)))
            for s in range(steps + 1):
                ang = a1 - sweep * s / steps
                q = p + H * np.array([math.cos(ang), math.sin(ang)])
                pts.append(q)
                param.append(float(i))
                # forced nodes: ends and the kite corners
                f = s / steps
                if any(abs(f - j / (2 * sectors)) < 0.5 / steps for j in range(2 * sectors + 1)):
                    forced.append(len(pts) - 1)
        else:
            m = n1 + n2
            m = m / np.linalg.norm(m)
            cos = max(0.3, m @ n2)
            pts.append(p + H * m / cos)
            param.append(float(i))
    return np.asarray(pts), np.asarray(param), forced


# ---------------------------------------------------------------------------
# 2-3. graph with merging and T-junction extension
# ---------------------------------------------------------------------------


def build(res, extra_nodes, merge):
    loops = res.loops
    lines = []
    for tr in res.traces:
        pts = np.asarray(tr["points"], dtype=float)
        if len(pts) < 3:
            continue
        if tr["end"] == "boundary":
            _d, _li, _k, _t, q = _project(pts[-1], loops)
            pts = np.vstack([pts, q])
        lines.append({"pts": pts, "cuts": [0.0, float(len(pts) - 1)]})
    bounds = [{"pts": np.vstack([loop, loop[:1]]), "cuts": [], "closed": True} for loop in loops]
    anchors = [np.array(c[:2]) for c in res.corners] + [np.asarray(p) for p in extra_nodes]
    for p in anchors:
        _d, li, k, t, _q = _project(p, loops)
        bounds[li]["cuts"].append(k + t)
    for line in lines:
        for end in (line["pts"][0], line["pts"][-1]):
            d, li, k, t, _q = _project(end, loops)
            if d < merge:
                bounds[li]["cuts"].append(k + t)
    for a in range(len(lines)):
        for b in range(a + 1, len(lines)):
            P, Q = lines[a]["pts"], lines[b]["pts"]
            if (Q.min(0) > P.max(0) + merge).any() or (Q.max(0) < P.min(0) - merge).any():
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
                    if np.linalg.norm(point - P[0]) < merge and np.linalg.norm(point - Q[0]) < merge:
                        continue
                    lines[a]["cuts"].append(i + hit[0])
                    lines[b]["cuts"].append(j + hit[1])
    nodes: list[np.ndarray] = []

    def node(p):
        for k, q in enumerate(nodes):
            if np.linalg.norm(q - p) < merge:
                return k
        nodes.append(np.asarray(p, dtype=float))
        return len(nodes) - 1

    # register anchors first so they win the merge
    for p in anchors:
        node(p)

    def at(pts, s):
        i = min(int(math.floor(s)), len(pts) - 2)
        return pts[i] + (s - i) * (pts[i + 1] - pts[i])

    arcs = []
    for poly in lines + bounds:
        pts = poly["pts"]
        closed = poly.get("closed", False)
        m = len(pts) - 1
        cuts = sorted(set(round(c, 9) for c in poly["cuts"]))
        if closed:
            if not cuts:
                continue
            cuts = cuts + [cuts[0] + m]
        for s0, s1 in zip(cuts, cuts[1:]):
            a = node(at(pts, s0 % m) if closed else at(pts, s0))
            b = node(at(pts, s1 % m) if closed else at(pts, s1))
            if a == b:
                continue
            inner = []
            k = int(math.floor(s0)) + 1
            while k < s1:
                inner.append(pts[k % m] if closed else pts[k])
                k += 1
            arcs.append({"a": a, "b": b, "path": np.asarray([nodes[a]] + inner + [nodes[b]]), "boundary": closed})
    # one arc per node pair and length: drop duplicates (merged traces)
    unique, seen = [], {}
    for arc in arcs:
        key = (min(arc["a"], arc["b"]), max(arc["a"], arc["b"]))
        length = float(np.linalg.norm(np.diff(arc["path"], axis=0), axis=1).sum())
        if any(abs(other - length) < 2 * merge for other in seen.get(key, [])):
            continue
        seen.setdefault(key, []).append(length)
        unique.append(arc)
    return nodes, unique


def faces(nodes, arcs):
    out = {}
    halfs = []
    for k, arc in enumerate(arcs):
        for forward in (True, False):
            path = arc["path"] if forward else arc["path"][::-1]
            d0 = path[1] - path[0]
            d1 = path[-1] - path[-2]
            halfs.append({"arc": k, "start": arc["a"] if forward else arc["b"],
                          "end": arc["b"] if forward else arc["a"],
                          "out": math.atan2(d0[1], d0[0]), "in": math.atan2(d1[1], d1[0]),
                          "path": path, "used": False})
            out.setdefault(halfs[-1]["start"], []).append(len(halfs) - 1)
    found = []
    for h0 in range(len(halfs)):
        if halfs[h0]["used"]:
            continue
        face, path, h = [], [], h0
        ok = True
        for _ in range(4 * len(halfs) + 4):
            halfs[h]["used"] = True
            path.extend(halfs[h]["path"][:-1])
            end = halfs[h]["end"]
            back = halfs[h]["in"] + math.pi
            best = None
            for g in out[end]:
                if g == (h ^ 1) and len(out[end]) > 1:
                    continue
                turn = (back - halfs[g]["out"]) % (2 * math.pi)
                if turn <= 1e-12:
                    turn = 2 * math.pi
                if best is None or turn < best[0]:
                    best = (turn, g)
            face.append((end, best[0], halfs[h]["in"]))
            h = best[1]
            if h == h0:
                break
        else:
            ok = False
        poly = np.asarray(path)
        area = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1])
        found.append({"corners": face, "poly": poly, "area": area, "closed": ok})
    return found


def classify(res, found, fronts):
    """Fluid faces: closed, the sign of the bounded faces, centroid outside every front."""
    outer = max(found, key=lambda f: abs(f["area"]))
    fluid = []
    for f in found:
        if f is outer or not f["closed"]:
            continue
        if np.sign(f["area"]) == np.sign(outer["area"]):
            continue
        c = f["poly"].mean(axis=0)
        inside_front = any(crossfield._inside(c[None, :], [fr])[0] for fr in fronts)
        if inside_front and not crossfield._inside(c[None, :], res.loops)[0]:
            continue
        fluid.append(f)
    return fluid


def hanging(fluid):
    out = []
    for f in fluid:
        for node, angle, incoming in f["corners"]:
            if abs(angle - math.pi) <= HANG:
                out.append((node, f, incoming))
    return out


def extend(res, nodes, hang, merge, max_length):
    added = 0
    stops = [(s[0], s[1]) for s in res.singularities]
    done = set()
    for node, face, incoming in hang:
        if node in done:
            continue
        p = nodes[node]
        best = None
        tangent = complex(math.cos(incoming), math.sin(incoming))
        for k in range(4):
            z = crossfield.sample(res, p[0] + 2 * res.h * math.cos(incoming + math.pi / 2),
                                  p[1] + 2 * res.h * math.sin(incoming + math.pi / 2))
            z = z if z is not None else crossfield.sample(res, *p)
            if z is None:
                break
            th = np.angle(z) / 4 + k * math.pi / 2
            d = complex(math.cos(th), math.sin(th))
            probe = p + 3 * res.h * np.array([d.real, d.imag])
            if not crossfield._inside(probe[None, :], [face["poly"]])[0] and \
                    not crossfield._inside(probe[None, :], [face["poly"][::-1]])[0]:
                continue
            score = abs((d * tangent.conjugate()).real)
            if best is None or score < best[0]:
                best = (score, th)
        if best is None:
            continue
        start = p + 3.0 * res.h * np.array([math.cos(best[1]), math.sin(best[1])])
        pts, kind = crossfield.trace(res, start[0], start[1], best[1], stops, max_length)
        res.traces.append({"points": [tuple(p)] + pts, "from": "extension", "end": kind})
        done.add(node)
        added += 1
    return added


def front_nodes(nodes, front, h):
    out = []
    for k, p in enumerate(nodes):
        d = np.linalg.norm(front - p, axis=1)
        j = int(np.argmin(d))
        if d[j] < 2 * h + 1e-9:
            out.append((j, k))
    return sorted(out)


def arc_midpoints(nodes, fronts, params, bodies, h):
    mids = []
    for front, param, body in zip(fronts, params, bodies):
        on = front_nodes(nodes, front, h)
        nb = len(body)
        for (j0, _k0), (j1, _k1) in zip(on, on[1:] + on[:1]):
            if int(round(param[j0])) % nb != int(round(param[j1])) % nb or j0 == j1:
                continue
            seg = list(range(j0, j1 + 1)) if j1 > j0 else list(range(j0, len(front))) + list(range(0, j1 + 1))
            if len(seg) < 3:
                continue
            mids.append(front[seg[len(seg) // 2]])
    return mids


def clip_to_boundary(res, merge):
    """The merge rule near walls and fronts: a singularity within ``merge``
    of the boundary is moved onto it; a separatrix ends where it comes
    within ``merge`` of the boundary after having been farther; one that
    never leaves that strip is dropped (it would be the boundary again)."""
    from scipy import ndimage

    dist = ndimage.distance_transform_edt(res.fluid) * res.h

    def d_at(x, y):
        i = int((x - res.origin[0]) / res.h)
        j = int((y - res.origin[1]) / res.h)
        ny, nx = res.shape
        if not (0 <= i < nx and 0 <= j < ny):
            return 0.0
        return float(dist[j, i])

    moved = {}
    kept_sing = []
    for x, y, idx in res.singularities:
        if d_at(x, y) < merge:
            _d, _li, _k, _t, q = _project(np.array([x, y]), res.loops)
            moved[(x, y)] = (float(q[0]), float(q[1]))
        else:
            kept_sing.append((x, y, idx))
    traces = []
    for tr in res.traces:
        pts = [tuple(p) for p in tr["points"]]
        start = pts[0]
        if start in moved:
            pts[0] = moved[start]
        left = False
        cut = None
        for k, (x, y) in enumerate(pts[1:], start=1):
            near = d_at(x, y) < merge
            if not near:
                left = True
            elif left:
                cut = k
                break
        if not left:
            continue
        if cut is not None:
            pts = pts[:cut + 1]
            tr = {**tr, "end": "boundary"}
        end = pts[-1]
        for (sx, sy), q in moved.items():
            if math.hypot(end[0] - sx, end[1] - sy) < 2 * res.h:
                pts[-1] = q
                tr = {**tr, "end": "boundary"}
        traces.append({**tr, "points": pts})
    res.singularities = kept_sing
    res.moved_singularities = list(moved.values())
    res.traces = traces


# ---------------------------------------------------------------------------
# 4. session
# ---------------------------------------------------------------------------


def _simplify(path, tol):
    path = np.asarray(path)
    keep = [0]
    for i in range(1, len(path) - 1):
        a, b = path[keep[-1]], path[i + 1]
        d = b - a
        if np.linalg.norm(d) == 0:
            continue
        off = abs(d[0] * (path[i] - a)[1] - d[1] * (path[i] - a)[0]) / np.linalg.norm(d)
        if off > tol:
            keep.append(i)
    keep.append(len(path) - 1)
    return path[keep]


def session(res, nodes, arcs, fluid, bodies, fronts, params, forced_sets, H, names):
    from blockdrawer.session import from_data

    data = {"format": "blockDrawer", "version": 7,
            "settings": {"zCells": 1, "zMin": 0.0, "zMax": 0.1, "scale": 1.0,
                         "zMinPatch": {"name": "front", "type": "empty"},
                         "zMaxPatch": {"name": "back", "type": "empty"}},
            "vertices": [], "blocks": [], "edgeCells": [], "edgeGeometry": [], "edgeGrading": [],
            "spacingLinks": [], "boundaries": [], "edgeBoundaries": [], "geometryCurves": []}
    ids = {}

    def vid(p):
        key = (round(float(p[0]), 10), round(float(p[1]), 10))
        if key not in ids:
            ids[key] = f"v{len(ids)}"
            data["vertices"].append({"id": ids[key], "x": key[0], "y": key[1]})
        return ids[key]

    node_id = [vid(p) for p in nodes]
    edges = {}

    def edge(a, b, kind=None, points=()):
        key = tuple(sorted((a, b)))
        if key in edges:
            return
        edges[key] = True
        data["edgeCells"].append({"vertices": list(key), "cells": 1})
        if kind and len(points):
            pts = list(points) if key[0] == a else list(reversed(points))
            data["edgeGeometry"].append({"vertices": list(key), "type": kind,
                                         "points": [{"x": float(x), "y": float(y)} for x, y in pts]})

    arc_by_pair = {}
    for arc in arcs:
        arc_by_pair.setdefault((arc["a"], arc["b"]), arc)
        arc_by_pair.setdefault((arc["b"], arc["a"]), {"a": arc["b"], "b": arc["a"], "path": arc["path"][::-1]})
    blocks = 0
    bad = []
    for f in fluid:
        corners = [c[0] for c in f["corners"] if c[1] < math.pi - HANG]
        if len(corners) != 4 or len(f["corners"]) != 4:
            bad.append(len(corners))
            continue
        ring = [c[0] for c in f["corners"]]
        quad = [node_id[k] for k in ring]
        data["blocks"].append({"id": f"b{blocks}", "vertices": quad})
        blocks += 1
        for i in range(4):
            a, b = ring[i], ring[(i + 1) % 4]
            arc = arc_by_pair.get((a, b))
            if arc is None:
                edge(node_id[a], node_id[b])
                continue
            simple = _simplify(arc["path"], 0.05 * res.h)
            inner = simple[1:-1]
            edge(node_id[a], node_id[b], "polyLine" if len(inner) else None, inner)
    # band blocks between consecutive front nodes
    wall_edges = []
    for body, front, param, forced in zip(bodies, fronts, params, forced_sets):
        on_front = []
        for k, p in enumerate(nodes):
            d = np.linalg.norm(front - p, axis=1)
            j = int(np.argmin(d))
            if d[j] < 1e-6 + 2 * res.h:
                on_front.append((j, k))
        on_front.sort()
        nb = len(body)
        pairs = list(zip(on_front, on_front[1:] + on_front[:1]))
        skip = set()
        for index, ((j0, k0), (j1, k1)) in enumerate(pairs):
            if index in skip:
                continue
            w0, w1 = param[j0], param[j1]
            if int(round(w0)) % nb == int(round(w1)) % nb:
                # a round join: a kite (wall corner, three front nodes)
                nxt = pairs[(index + 1) % len(pairs)]
                (j2, k2) = nxt[1]
                if int(round(param[j2])) % nb != int(round(w0)) % nb:
                    bad.append("triangle at a round join")
                    continue
                corner = vid(body[int(round(w0)) % nb])
                data["blocks"].append({"id": f"b{blocks}", "vertices": [corner, node_id[k0], node_id[k1], node_id[k2]]})
                blocks += 1
                for (ja, ka), (jb, kb) in (((j0, k0), (j1, k1)), ((j1, k1), (j2, k2))):
                    seg = front[ja:jb + 1] if jb > ja else np.vstack([front[ja:], front[:jb + 1]])
                    inner = _simplify(seg, 0.05 * res.h)[1:-1]
                    edge(node_id[ka], node_id[kb], "polyLine" if len(inner) else None, inner)
                edge(corner, node_id[k0])
                edge(corner, node_id[k2])
                skip.add((index + 1) % len(pairs))
                continue
            seg = list(range(j0, j1 + 1)) if j1 > j0 else list(range(j0, len(front))) + list(range(0, j1 + 1))
            front_path = front[seg]
            a_wall = body[int(round(w0)) % nb]
            b_wall = body[int(round(w1)) % nb]
            fa, fb = node_id[k0], node_id[k1]
            if int(round(w0)) % nb == int(round(w1)) % nb:
                # a kite at a round join: needs a middle front node
                mids = [k for (j, k) in on_front if (j0 < j < j1)]
                bad.append("kite")
                continue
            wa, wb = vid(a_wall), vid(b_wall)
            data["blocks"].append({"id": f"b{blocks}", "vertices": [wa, wb, fb, fa]})
            blocks += 1
            i0, i1 = int(round(w0)) % nb, int(round(w1)) % nb
            wall_idx = list(range(i0, i1 + 1)) if i1 > i0 else list(range(i0, nb)) + list(range(0, i1 + 1))
            wall_pts = body[wall_idx][1:-1]
            edge(wa, wb, "spline" if len(wall_pts) else None, wall_pts[:: max(1, len(wall_pts) // 30)])
            wall_edges.append((wa, wb))
            inner = _simplify(front_path, 0.05 * res.h)[1:-1]
            edge(fa, fb, "polyLine" if len(inner) else None, inner)
            edge(wa, fa)
            edge(wb, fb)
    return data, blocks, bad, wall_edges


# ---------------------------------------------------------------------------


def run(case, n, H, rotate=0.0, out=None, iterations=12):
    case = cases.transformed(case, rotate)
    loops = cases.oriented(case["loops"])
    box, bodies = loops[0], loops[1:]
    fronts, params, forced_sets = [], [], []
    for body in bodies:
        front, param, forced = front_of(body, H)
        fronts.append(front)
        params.append(param)
        forced_sets.append(forced)
    res = crossfield.solve_field([box] + fronts, n, case["name"])
    crossfield.singularities(res)
    crossfield.corners(res)
    crossfield.separatrices(res)
    merge = 0.5 * H
    clip_to_boundary(res, merge)
    extra = [np.asarray(q) for q in getattr(res, "moved_singularities", [])]
    ny, nx = res.shape
    max_length = 3.0 * (nx + ny) * res.h
    history = []
    for it in range(iterations):
        nodes, arcs = build(res, extra, merge)
        found = faces(nodes, arcs)
        fluid = classify(res, found, fronts)
        hang = hanging(fluid)
        corner_hist = {}
        for f in fluid:
            c = sum(1 for _n, a, _i in f["corners"] if a < math.pi - HANG)
            corner_hist[c] = corner_hist.get(c, 0) + 1
        # a front interval that is all round join (both ends on one wall
        # corner) gets its middle as a node: the band there is a kite
        mids = arc_midpoints(nodes, fronts, params, bodies, res.h)
        if mids:
            extra += mids
            nodes, arcs = build(res, extra, merge)
            found = faces(nodes, arcs)
            fluid = classify(res, found, fronts)
            hang = hanging(fluid)
        history.append({"iteration": it, "nodes": len(nodes), "fluid_faces": len(fluid), "hanging": len(hang),
                        "unclosed": sum(1 for f in found if not f["closed"]),
                        "corners": {str(k): v for k, v in sorted(corner_hist.items())}})
        if not hang:
            break
        if extend(res, nodes, hang, merge, max_length) == 0:
            break
    data, blocks, bad, wall_edges = session(res, nodes, arcs, fluid, bodies, fronts, params, forced_sets, H,
                                            case.get("walls"))
    report = {"case": case["name"], "n": n, "band": H, "rotate": rotate,
              "singularities": [[round(s[0], 4), round(s[1], 4), s[2]] for s in res.singularities],
              "traces": len(res.traces), "history": history, "blocks": blocks, "unbuilt_faces": bad}
    valid = None
    if out:
        from blockdrawer.session import from_data

        try:
            model = from_data(data)
            model.validate()
            valid = "valid"
        except Exception as exc:  # noqa: BLE001 - the reason is the finding
            valid = f"invalid: {exc}"
        Path(out).write_text(json.dumps(data))
    report["session"] = valid
    res.graph = (nodes, arcs, fluid)
    return res, report, data


def draw_graph(res, path, window=None, width=1600):
    from PIL import Image, ImageDraw

    nodes, arcs, fluid = res.graph
    ny, nx = res.shape
    x0, y0 = res.origin
    x1, y1 = x0 + nx * res.h, y0 + ny * res.h
    if window:
        x0, y0, x1, y1 = window
    scale = width / (x1 - x0)
    height = int((y1 - y0) * scale)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def px(q):
        return ((q[0] - x0) * scale, height - (q[1] - y0) * scale)

    colours = {3: (255, 200, 200), 4: (210, 235, 210), 5: (255, 230, 170)}
    for f in fluid:
        c = sum(1 for _n, a, _i in f["corners"] if a < math.pi - HANG)
        draw.polygon([px(q) for q in f["poly"]], fill=colours.get(c, (230, 200, 255)))
    for loop in res.loops:
        draw.line([px(q) for q in np.vstack([loop, loop[:1]])], fill=(0, 0, 0), width=2)
    for arc in arcs:
        draw.line([px(q) for q in arc["path"]], fill=(30, 30, 200), width=2)
    for q in nodes:
        c = px(q)
        draw.ellipse([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], fill=(200, 0, 0))
    img.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(cases.CASES))
    parser.add_argument("--n", type=int, default=400)
    parser.add_argument("--band", type=float, default=0.05)
    parser.add_argument("--rotate", type=float, default=0.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--zoom", action="append", default=[])
    args = parser.parse_args(argv)
    res, report, _data = run(cases.CASES[args.case](), args.n, args.band, args.rotate, args.out)
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    import plot

    tag = f"extract_{args.case}_n{args.n}" + (f"_r{args.rotate:g}" if args.rotate else "")
    plot.render(res, out / f"{tag}_field.png")
    draw_graph(res, out / f"{tag}_graph.png")
    for k, zoom in enumerate(args.zoom):
        draw_graph(res, out / f"{tag}_graph_zoom{k}.png", tuple(float(v) for v in zoom.split(",")))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
