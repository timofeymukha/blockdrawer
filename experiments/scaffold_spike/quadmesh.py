"""Part 3: an all-quad mesher as the scaffold.

Geometry -> gmsh input (sharp corners as points, straight pieces as lines,
curved pieces as splines through the supplied points, one physical curve per
patch) -> an all-quad mesh from the installed gmsh binary -> its base complex
(separatrices followed edge by edge from every irregular vertex) -> a
BlockDrawer session.

The same mesher settings for every case: the size is the domain's longer
side over ``--n``, refined by curvature or uniform (``SIZING``), nothing per
case. ``--band`` meshes outside a front offset from each smooth body and
adds the band blocks afterwards.

python experiments/scaffold_spike/quadmesh.py CASE [--mesher quadqs|frontal] [--sizing curvature|uniform]
    [--kernel builtin|occ] [--band] [--n N] [--out SESSION]
Skips with a message when the gmsh binary is not found.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import cases  # noqa: E402

CORNER_TURN = math.radians(30.0)
GMSH_CANDIDATES = ("/tmp/timofey/install/bin/gmsh", "gmsh")


def gmsh_binary():
    for name in GMSH_CANDIDATES:
        path = shutil.which(name) if not os.path.isabs(name) else (name if os.path.exists(name) else None)
        if path:
            return path
    return None


# ---------------------------------------------------------------------------
# patches per case: which loop pieces get which name
# ---------------------------------------------------------------------------


def patch_name(case_name, loop_index, a, b):
    """Name of the boundary piece from a to b on loop ``loop_index``."""
    if loop_index > 0:
        return f"wall{loop_index}"
    if case_name == "backward_step":
        mid = 0.5 * (a + b)
        if abs(mid[0] + 5) < 1e-9:
            return "inlet"
        if abs(mid[0] - 20) < 1e-9:
            return "outlet"
        return "wall0"
    xs = (a[0] + b[0]) / 2
    ys = (a[1] + b[1]) / 2
    if abs(a[0] - b[0]) < 1e-12:
        return "inlet" if xs < 0 else "outlet"
    return "bottom" if ys < 0 else "top"


def corner_indices(loop):
    """Vertices where the loop turns more than CORNER_TURN."""
    n = len(loop)
    corners = []
    for k in range(n):
        a, p, b = loop[k - 1], loop[k], loop[(k + 1) % n]
        t1 = (p - a) / np.linalg.norm(p - a)
        t2 = (b - p) / np.linalg.norm(b - p)
        turn = math.atan2(t1[0] * t2[1] - t1[1] * t2[0], t1 @ t2)
        if abs(turn) > CORNER_TURN:
            corners.append(k)
    return corners


def pieces(loop):
    """Split a loop at corners (turning more than CORNER_TURN); a loop with
    none is split into four pieces of equal point count."""
    n = len(loop)
    corners = corner_indices(loop)
    if not corners:
        corners = [0, n // 4, n // 2, 3 * n // 4]
    elif len(corners) == 1:
        # a closed curve needs two pieces: split opposite the one corner
        corners = sorted({corners[0], (corners[0] + n // 2) % n})
    out = []
    for i, c in enumerate(corners):
        d = corners[(i + 1) % len(corners)]
        idx = list(range(c, d + 1)) if d > c else list(range(c, n)) + list(range(0, d + 1))
        out.append(idx)
    return out


SIZING = {
    # refined by curvature (16 elements per 2 pi), down to a fortieth
    "curvature": lambda size: ["Mesh.MeshSizeMax = %g;" % size, "Mesh.MeshSizeMin = %g;" % (size / 40),
                               "Mesh.MeshSizeFromCurvature = 16;"],
    # one size everywhere
    "uniform": lambda size: ["Mesh.MeshSizeMax = %g;" % size, "Mesh.MeshSizeMin = %g;" % size],
}


def write_geo(case, path, size, extra="", sizing="curvature", kernel="builtin"):
    loops = cases.oriented(case["loops"])
    factory = ['SetFactory("OpenCASCADE");'] if kernel == "occ" else []
    lines = [f"// {case['name']}", *factory, *SIZING[sizing](size),
             "Mesh.MeshSizeExtendFromBoundary = 1;", extra]
    pid = 0
    cid = 0
    point_of = {}
    loop_curves = []
    physical = collections.defaultdict(list)

    def point(p):
        nonlocal pid
        key = (round(float(p[0]), 12), round(float(p[1]), 12))
        if key not in point_of:
            pid += 1
            point_of[key] = pid
            lines.append(f"Point({pid}) = {{{key[0]!r}, {key[1]!r}, 0}};")
        return point_of[key]

    for li, loop in enumerate(loops):
        curves = []
        for idx in pieces(loop):
            pts = loop[idx]
            a, b = pts[0], pts[-1]
            chord = b - a
            off = max((abs(chord[0] * (q - a)[1] - chord[1] * (q - a)[0]) / max(np.linalg.norm(chord), 1e-300)
                       for q in pts[1:-1]), default=0.0)
            cid += 1
            if off < 1e-9 * max(1.0, np.linalg.norm(chord)):
                lines.append(f"Line({cid}) = {{{point(a)}, {point(b)}}};")
            else:
                ids = [point(q) for q in pts]
                lines.append(f"Spline({cid}) = {{{', '.join(map(str, ids))}}};")
            curves.append(cid)
            physical[patch_name(case["name"], li, a, b)].append(cid)
        lines.append(f"Curve Loop({li + 1}) = {{{', '.join(map(str, curves))}}};")
        loop_curves.append(li + 1)
    lines.append(f"Plane Surface(1) = {{{', '.join(map(str, loop_curves))}}};")
    for k, (name, ids) in enumerate(sorted(physical.items()), start=1):
        lines.append(f'Physical Curve("{name}", {k}) = {{{", ".join(map(str, ids))}}};')
    lines.append('Physical Surface("fluid", 100) = {1};')
    Path(path).write_text("\n".join(lines) + "\n")
    return loops


MESHERS = {
    # quasi-structured quads (QuadMeshingTools)
    "quadqs": ["-algo", "quadqs"],
    # frontal-Delaunay for quads, recombined, subdivided: always all quads
    "frontal": ["-algo", "delquad", "-setnumber", "Mesh.RecombineAll", "1",
                "-setnumber", "Mesh.SubdivisionAlgorithm", "1"],
}


def run_gmsh(geo, msh, mesher, timeout=600):
    exe = gmsh_binary()
    if exe is None:
        return None, "gmsh not found"
    cmd = [exe, str(geo), "-2", "-format", "msh2", "-o", str(msh), *MESHERS[mesher], "-nt", "1"]
    t = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout} s"
    elapsed = time.time() - t
    if proc.returncode != 0 or not Path(msh).exists():
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return None, f"gmsh exit {proc.returncode}: {' | '.join(tail)}"
    return elapsed, (proc.stdout + proc.stderr)


def merge_coincident(nodes, quads, segs, tol):
    """Nodes gmsh writes twice at curve joints become one; degenerate quads go."""
    key_of = {}
    remap = {}
    for k in sorted(nodes):
        x, y = nodes[k]
        key = (round(x / tol), round(y / tol))
        remap[k] = key_of.setdefault(key, k)
    nodes = {k: v for k, v in nodes.items() if remap[k] == k}
    quads = [[remap[v] for v in q] for q in quads]
    quads = [q for q in quads if len(set(q)) == 4]
    segs = [(remap[a], remap[b], n) for a, b, n in segs if remap[a] != remap[b]]
    return nodes, quads, segs


def read_msh2(path):
    text = Path(path).read_text().split("\n")
    i = text.index("$Nodes")
    n = int(text[i + 1])
    nodes = {}
    for line in text[i + 2:i + 2 + n]:
        a = line.split()
        nodes[int(a[0])] = (float(a[1]), float(a[2]))
    names = {}
    if "$PhysicalNames" in text:
        j = text.index("$PhysicalNames")
        for line in text[j + 2:j + 2 + int(text[j + 1])]:
            dim, tag, name = line.split(maxsplit=2)
            names[(int(dim), int(tag))] = name.strip('"')
    i = text.index("$Elements")
    m = int(text[i + 1])
    quads, tris, segs = [], [], []
    for line in text[i + 2:i + 2 + m]:
        a = list(map(int, line.split()))
        kind, ntags = a[1], a[2]
        tags = a[3:3 + ntags]
        verts = a[3 + ntags:]
        if kind == 3:
            quads.append(verts)
        elif kind == 2:
            tris.append(verts)
        elif kind == 1:
            segs.append((verts[0], verts[1], names.get((1, tags[0]), str(tags[0]))))
    return nodes, quads, tris, segs


# ---------------------------------------------------------------------------
# base complex
# ---------------------------------------------------------------------------


def base_complex(nodes, quads, segs):
    edge_quads = collections.defaultdict(list)
    vert_quads = collections.defaultdict(list)
    for qi, q in enumerate(quads):
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            edge_quads[frozenset((a, b))].append(qi)
            vert_quads[a].append(qi)
    boundary_edges = {e for e, qs in edge_quads.items() if len(qs) == 1}
    boundary_vertices = {v for e in boundary_edges for v in e}
    xy = {k: np.array(v) for k, v in nodes.items()}

    def fluid_angle(v):
        """Sum of the quad corner angles at boundary vertex v."""
        total = 0.0
        for qi in vert_quads[v]:
            q = quads[qi]
            k = q.index(v)
            a, b = xy[q[k - 1]] - xy[v], xy[q[(k + 1) % 4]] - xy[v]
            norms = np.linalg.norm(a) * np.linalg.norm(b)
            if norms > 0:
                total += math.acos(max(-1.0, min(1.0, a @ b / norms)))
        return total

    irregular = {}
    for v in vert_quads:
        count = len(vert_quads[v])
        if v in boundary_vertices:
            expected = max(1, int(round(fluid_angle(v) / (math.pi / 2))))
            if count != expected or expected != 2:
                irregular[v] = ("boundary", count, expected)
        elif count != 4:
            irregular[v] = ("interior", count, 4)
    # vertex -> neighbour vertices through edges
    neighbours = collections.defaultdict(set)
    for e in edge_quads:
        a, b = tuple(e)
        neighbours[a].add(b)
        neighbours[b].add(a)

    def opposite(prev, v):
        """The edge straight through regular interior vertex v from prev."""
        quads_at = vert_quads[v]
        # the two quads containing edge (prev, v) share it; the straight
        # continuation is the neighbour of v sharing no quad with prev
        prev_quads = set(edge_quads[frozenset((prev, v))])
        for w in neighbours[v]:
            if w == prev:
                continue
            if not (set(edge_quads[frozenset((v, w))]) & prev_quads):
                return w
        return None

    cut = set(boundary_edges)
    for v in irregular:
        for w in neighbours[v]:
            e = frozenset((v, w))
            if e in boundary_edges:
                continue
            prev, cur = v, w
            steps = 0
            while steps < 100000:
                cut.add(frozenset((prev, cur)))
                if cur in irregular or cur in boundary_vertices:
                    break
                nxt = opposite(prev, cur)
                if nxt is None:
                    break
                prev, cur = cur, nxt
                steps += 1
    # patches: flood fill quads across uncut edges
    patch_of = [-1] * len(quads)
    patches = []
    for qi in range(len(quads)):
        if patch_of[qi] >= 0:
            continue
        stack = [qi]
        patch_of[qi] = len(patches)
        members = []
        while stack:
            q = stack.pop()
            members.append(q)
            quad = quads[q]
            for k in range(4):
                e = frozenset((quad[k], quad[(k + 1) % 4]))
                if e in cut:
                    continue
                for other in edge_quads[e]:
                    if patch_of[other] < 0:
                        patch_of[other] = len(patches)
                        stack.append(other)
        patches.append(members)
    return {"irregular": irregular, "patches": patches, "patch_of": patch_of, "cut": cut,
            "boundary_edges": boundary_edges, "edge_quads": edge_quads, "vert_quads": vert_quads,
            "boundary_vertices": boundary_vertices}


def patch_layout(nodes, quads, bc, members):
    """Corners and sides of one patch; None when it is not a 4-corner grid."""
    members_set = set(members)
    count = collections.Counter()
    for q in members:
        for v in quads[q]:
            count[v] += 1
    # patch boundary edges
    edge_count = collections.Counter()
    for q in members:
        quad = quads[q]
        for k in range(4):
            edge_count[frozenset((quad[k], quad[(k + 1) % 4]))] += 1
    rim = [e for e, c in edge_count.items() if c == 1]
    rim_nb = collections.defaultdict(list)
    for e in rim:
        a, b = tuple(e)
        rim_nb[a].append(b)
        rim_nb[b].append(a)
    corners = [v for v in rim_nb if count[v] == 1]
    if len(corners) != 4 or any(len(rim_nb[v]) != 2 for v in rim_nb):
        return None, len(corners)
    # walk the rim from a corner
    start = min(corners)
    loop = [start]
    prev, cur = None, start
    while True:
        options = [w for w in rim_nb[cur] if w != prev]
        nxt = options[0] if prev is not None else sorted(options)[0]
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
        if len(loop) > len(rim) + 2:
            return None, -1
    idx = [loop.index(c) for c in corners]
    idx.sort()
    sides = []
    for i in range(4):
        a, b = idx[i], idx[(i + 1) % 4]
        sides.append(loop[a:b + 1] if b > a else loop[a:] + loop[:b + 1])
    return sides, 4


def to_session(case, nodes, quads, segs, bc):
    from blockdrawer.session import from_data

    data = {"format": "blockDrawer", "version": 7,
            "settings": {"zCells": 1, "zMin": 0.0, "zMax": 0.1, "scale": 1.0,
                         "zMinPatch": {"name": "front", "type": "empty"},
                         "zMaxPatch": {"name": "back", "type": "empty"}},
            "vertices": [], "blocks": [], "edgeCells": [], "edgeGeometry": [], "edgeGrading": [],
            "spacingLinks": [], "boundaries": [], "edgeBoundaries": [], "geometryCurves": []}
    seg_name = {frozenset((a, b)): name for a, b, name in segs}
    vid = {}

    def vertex(v):
        if v not in vid:
            vid[v] = f"v{v}"
            data["vertices"].append({"id": vid[v], "x": nodes[v][0], "y": nodes[v][1]})
        return vid[v]

    edges = {}
    bad = []
    for pi, members in enumerate(bc["patches"]):
        sides, ncorners = patch_layout(nodes, quads, bc, members)
        if sides is None:
            bad.append(ncorners)
            continue
        corner_pts = np.array([nodes[side[0]] for side in sides])
        area = 0.5 * np.sum(corner_pts[:, 0] * np.roll(corner_pts[:, 1], -1) - np.roll(corner_pts[:, 0], -1) * corner_pts[:, 1])
        corner_ids = [vertex(side[0]) for side in sides]
        if area < 0:
            corner_ids = corner_ids[::-1]
        data["blocks"].append({"id": f"b{pi}", "vertices": corner_ids})
        for side in sides:
            a, b = vertex(side[0]), vertex(side[-1])
            key = tuple(sorted((a, b)))
            if key in edges:
                continue
            chain = side if key[0] == a else side[::-1]
            pts = [nodes[v] for v in chain]
            edges[key] = len(chain) - 1
            data["edgeCells"].append({"vertices": list(key), "cells": len(chain) - 1})
            inner = pts[1:-1]
            if inner:
                straight = all(abs((pts[-1][0] - pts[0][0]) * (p[1] - pts[0][1]) - (pts[-1][1] - pts[0][1]) * (p[0] - pts[0][0]))
                               < 1e-10 for p in inner)
                if not straight:
                    on_wall = seg_name.get(frozenset((chain[0], chain[1])), "").startswith("wall")
                    step = max(1, len(inner) // 40)
                    data["edgeGeometry"].append({
                        "vertices": list(key), "type": "spline" if on_wall else "polyLine",
                        "points": [{"x": x, "y": y} for x, y in inner[::step]]})
            name = seg_name.get(frozenset((chain[0], chain[1])))
            if name and all(frozenset((u, w)) in bc["boundary_edges"] for u, w in zip(chain, chain[1:])):
                data["edgeBoundaries"].append({"vertices": list(key), "boundary": name})
    colours = ["#d9485f", "#2f9e44", "#1c7ed6", "#f08c00", "#7048e8", "#0ca678", "#e64980", "#495057"]
    names = sorted({e["boundary"] for e in data["edgeBoundaries"]})
    for k, name in enumerate(names):
        data["boundaries"].append({"name": name, "type": "wall" if name.startswith("wall") else "patch",
                                   "color": colours[k % len(colours)]})
    # opposite sides must agree: from_data validates counts
    try:
        model = from_data(data)
        model.validate()
        status = "valid"
    except Exception as exc:  # noqa: BLE001
        model = None
        status = f"invalid: {exc}"
    return data, model, status, bad


# ---------------------------------------------------------------------------
# band first: the mesher meshes outside an offset front, the band is added
# ---------------------------------------------------------------------------


class BandRefused(Exception):
    pass


def band_case(case, height):
    """The case with every body replaced by its front, ``height`` into the
    fluid along the normal. Only smooth bodies: a sharp corner needs a corner
    construction (mitre, fan, wake) the offset does not give."""
    loops = cases.oriented(case["loops"])
    fronts = []
    for li, loop in enumerate(loops[1:], start=1):
        sharp = corner_indices(loop)
        if sharp:
            raise BandRefused(f"body {li} has {len(sharp)} sharp corner(s)")
        t = np.roll(loop, -1, axis=0) - np.roll(loop, 1, axis=0)
        t /= np.linalg.norm(t, axis=1)[:, None]
        front = loop + height * np.column_stack((-t[:, 1], t[:, 0]))
        if np.min(_distance_to_loop(front, loop)) < 0.99 * height:
            raise BandRefused(f"body {li}: the front at {height:g} folds (curvature radius below the band)")
        fronts.append(front)
    return {"name": case["name"], "loops": [loops[0]] + fronts}


def _distance_to_loop(points, loop):
    a = loop
    b = np.roll(loop, -1, axis=0)
    ab = b - a
    out = np.empty(len(points))
    for i, p in enumerate(points):
        t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.einsum("ij,ij->i", ab, ab), 0, 1)
        out[i] = np.min(np.linalg.norm(a + t[:, None] * ab - p, axis=1))
    return out


def _foot(p, loop, cumulative):
    """Arc-length parameter and point of the wall point nearest ``p``."""
    a = loop
    b = np.roll(loop, -1, axis=0)
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.einsum("ij,ij->i", ab, ab), 0, 1)
    q = a + t[:, None] * ab
    k = int(np.argmin(np.linalg.norm(q - p, axis=1)))
    return cumulative[k] + t[k] * np.linalg.norm(ab[k]), q[k]


def add_band(data, walls, cells):
    """Turn every front chain of ``data`` into a band block down to its wall.
    ``walls`` maps a front patch name to the wall loop it was offset from."""
    from blockdrawer.session import from_data

    xy = {v["id"]: (v["x"], v["y"]) for v in data["vertices"]}
    geometry = {tuple(g["vertices"]): g for g in data["edgeGeometry"]}
    counts = {tuple(e["vertices"]): e["cells"] for e in data["edgeCells"]}
    keep, foot_of = [], {}
    for entry in data["edgeBoundaries"]:
        name = entry["boundary"]
        if name not in walls:
            keep.append(entry)
            continue
        loop = walls[name]
        seg = np.linalg.norm(np.roll(loop, -1, axis=0) - loop, axis=1)
        cumulative = np.concatenate(([0.0], np.cumsum(seg)))
        perimeter = cumulative[-1]
        a, b = entry["vertices"]
        for v in (a, b):
            if v not in foot_of:
                s, q = _foot(np.array(xy[v]), loop, cumulative)
                foot_of[v] = (s, "w" + v)
                data["vertices"].append({"id": "w" + v, "x": float(q[0]), "y": float(q[1])})
        g = geometry.get((a, b))
        mid = np.array([g["points"][len(g["points"]) // 2]["x"], g["points"][len(g["points"]) // 2]["y"]]) if g \
            else 0.5 * (np.array(xy[a]) + np.array(xy[b]))
        sa, sb, sm = foot_of[a][0], foot_of[b][0], _foot(mid, loop, cumulative)[0]
        forward = (sm - sa) % perimeter < (sb - sa) % perimeter
        span = (sb - sa) % perimeter if forward else -((sa - sb) % perimeter)
        stations = sa + span * np.linspace(0, 1, 42)[1:-1]
        pts = [np.array([np.interp(s % perimeter, cumulative, np.append(loop[:, 0], loop[0, 0])),
                         np.interp(s % perimeter, cumulative, np.append(loop[:, 1], loop[0, 1]))]) for s in stations]
        wa, wb = foot_of[a][1], foot_of[b][1]
        corners = [a, b, wb, wa]
        p = np.array([xy[v] if v in xy else next((u["x"], u["y"]) for u in data["vertices"] if u["id"] == v)
                      for v in corners])
        area = 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
        data["blocks"].append({"id": f"band_{a}_{b}", "vertices": corners if area > 0 else corners[::-1]})
        key = tuple(sorted((wa, wb)))
        data["edgeCells"].append({"vertices": list(key), "cells": counts[(a, b)]})
        if key[0] != wa:
            pts = pts[::-1]
        data["edgeGeometry"].append({"vertices": list(key), "type": "spline",
                                     "points": [{"x": float(x), "y": float(y)} for x, y in pts]})
        keep.append({"vertices": list(key), "boundary": name})
        for v, w in ((a, wa), (b, wb)):
            spoke = tuple(sorted((v, w)))
            if spoke not in counts:
                counts[spoke] = cells
                data["edgeCells"].append({"vertices": list(spoke), "cells": cells})
    data["edgeBoundaries"] = keep
    model = from_data(data)
    model.validate()
    return model


def thinnest(nodes, quads, bc):
    worst = None
    for members in bc["patches"]:
        sides, n = patch_layout(nodes, quads, bc, members)
        if sides is None:
            continue
        length = [sum(math.dist(nodes[a], nodes[b]) for a, b in zip(s, s[1:])) for s in sides]
        longer = max(length[0] + length[2], length[1] + length[3])
        if longer <= 0:
            continue
        ratio = min(length[0] + length[2], length[1] + length[3]) / longer
        worst = ratio if worst is None else min(worst, ratio)
    return worst


BAND_FRACTION = 0.05  # band height over the smallest body's extent
BAND_CELLS = 8


def analyse(case_name, mesher, n, workdir, out=None, sizing="curvature", kernel="builtin", band=False):
    case = cases.CASES[case_name]()
    loops = cases.oriented(case["loops"])
    allpts = np.vstack(loops)
    size = float(max(allpts.max(0) - allpts.min(0))) / n
    tag = f"{case_name}_{mesher}_{kernel}_{sizing}_{n}{'_band' if band else ''}"
    geo = Path(workdir) / f"{tag}.geo"
    msh = Path(workdir) / f"{tag}.msh"
    row = {"case": case_name, "mesher": mesher, "kernel": kernel, "sizing": sizing, "n": n, "size": size}
    if band and len(loops) == 1:
        row["mesh"] = "band: no body (the walls are the domain's own boundary)"
        return row, None
    if band:
        height = BAND_FRACTION * min(float(max(loop.max(0) - loop.min(0))) for loop in loops[1:])
        row["band"] = height
        try:
            meshed_case = band_case(case, height)
        except BandRefused as exc:
            row["mesh"] = f"band refused: {exc}"
            return row, None
    else:
        meshed_case = case
    write_geo(meshed_case, geo, size, sizing=sizing, kernel=kernel)
    elapsed, log = run_gmsh(geo, msh, mesher)
    if elapsed is None:
        row["mesh"] = log
        return row, None
    nodes, quads, tris, segs = read_msh2(msh)
    before = len(nodes)
    nodes, quads, segs = merge_coincident(nodes, quads, segs, 1e-9 * max(1.0, size))
    if before - len(nodes) > 0.01 * before:
        row.update({"quads": len(quads), "mesh": f"degenerate: {before - len(nodes)} of {before} nodes coincide"})
        return row, None
    row.update({"t_mesh": round(elapsed, 1), "quads": len(quads), "triangles": len(tris)})
    if tris or not quads:
        row["mesh"] = "not all quads"
        return row, None
    bc = base_complex(nodes, quads, segs)
    irr = bc["irregular"]
    # irregular vertices within a band height (5% of the body size... the
    # wall distance below) of a wall
    walls = [np.array(nodes[a]) for a, b, name in segs if name.startswith("wall")]
    near_wall = 0.05 * (min(float(max(loop.max(0) - loop.min(0))) for loop in loops[1:]) if len(loops) > 1 else 1.0)
    near = 0
    if walls:
        W = np.array(walls)
        for v, info in irr.items():
            if info[0] == "interior" and np.min(np.linalg.norm(W - np.array(nodes[v]), axis=1)) < near_wall:
                near += 1
    row.update({
        "irregular_interior": sum(1 for i in irr.values() if i[0] == "interior"),
        "irregular_boundary": sum(1 for i in irr.values() if i[0] == "boundary" and i[1] != i[2]),
        "interior_irregular_near_wall": near,
        "valences": dict(collections.Counter(f"{i[0][0]}{i[1]}" for i in irr.values() if i[0] == "interior" or i[1] != i[2])),
        "blocks": len(bc["patches"]),
        "thinnest": round(thinnest(nodes, quads, bc) or 0, 4),
    })
    data, model, status, bad = to_session(case, nodes, quads, segs, bc)
    if band and model is not None:
        try:
            model = add_band(data, {f"wall{li}": loops[li] for li in range(1, len(loops))}, BAND_CELLS)
            row["band_blocks"] = sum(1 for b in data["blocks"] if b["id"].startswith("band_"))
        except Exception as exc:  # noqa: BLE001
            model = None
            status = f"invalid band: {exc}"
    row["session"] = status
    row["non_quad_patches"] = len(bad)
    if out and model is not None:
        Path(out).write_text(json.dumps(data))
    return row, (nodes, quads, bc, loops)


def render(result, path, window=None, width=1600):
    from PIL import Image, ImageDraw

    nodes, quads, bc, loops = result
    pts = np.array(list(nodes.values()))
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    if window:
        x0, y0, x1, y1 = window
    scale = width / (x1 - x0)
    height = int((y1 - y0) * scale)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def px(p):
        return ((p[0] - x0) * scale, height - (p[1] - y0) * scale)

    rng = np.random.default_rng(3)
    colours = [tuple(int(v) for v in 150 + 100 * rng.random(3)) for _ in bc["patches"]]
    for qi, q in enumerate(quads):
        draw.polygon([px(nodes[v]) for v in q], fill=colours[bc["patch_of"][qi]], outline=(200, 200, 200))
    for e in bc["cut"]:
        a, b = tuple(e)
        draw.line([px(nodes[a]), px(nodes[b])], fill=(0, 0, 0), width=2)
    for v, info in bc["irregular"].items():
        if info[0] == "boundary" and info[1] == info[2]:
            continue
        c = px(nodes[v])
        colour = (220, 0, 0) if info[1] < info[2] else (0, 80, 255)
        draw.ellipse([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], fill=colour)
    img.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(cases.CASES))
    parser.add_argument("--mesher", choices=sorted(MESHERS), default="quadqs")
    parser.add_argument("--n", type=int, default=40, help="domain's longer side over the mesh size")
    parser.add_argument("--out", default=None)
    parser.add_argument("--zoom", action="append", default=[])
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--sizing", choices=sorted(SIZING), default="curvature")
    parser.add_argument("--kernel", choices=("builtin", "occ"), default="builtin")
    parser.add_argument("--band", action="store_true",
                        help="mesh outside a front offset from each smooth body, then add the band blocks")
    args = parser.parse_args(argv)
    if gmsh_binary() is None:
        print(json.dumps({"case": args.case, "skipped": "gmsh not found"}))
        return 0
    workdir = args.workdir or tempfile.mkdtemp(prefix="quadmesh_")
    row, result = analyse(args.case, args.mesher, args.n, workdir, args.out, args.sizing, args.kernel, args.band)
    if result is not None:
        outdir = HERE / "output"
        outdir.mkdir(exist_ok=True)
        tag = f"quad_{args.case}_{args.mesher}_{args.kernel}_{args.sizing}_n{args.n}{'_band' if args.band else ''}"
        render(result, outdir / f"{tag}.png")
        for k, zoom in enumerate(args.zoom):
            render(result, outdir / f"{tag}_zoom{k}.png", tuple(float(v) for v in zoom.split(",")))
    print(json.dumps(row))
    return 0


if __name__ == "__main__":
    main()
