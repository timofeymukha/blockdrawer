"""A boundary-aligned cross field on a raster, its singularities, the
separatrices traced from singularities and boundary corners, and the
partition they cut.

Representation: $z = e^{4 i \\theta}$ per fluid pixel. Boundary pixels (fluid
pixels next to a solid one) take $z$ from the tangent of their nearest
boundary segment; the interior solves $\\Delta z = 0$ (Jacobi-preconditioned
conjugate gradients, matrix-free, NumPy only); the cross directions are
$\\theta + k \\pi / 2$ with $\\theta = \\arg(z) / 4$.

Corner rule (one rule): a boundary vertex whose polyline turns by more than
``CORNER_TURN`` is a corner of fluid angle $\\alpha$; it gets
$k = \\max(1, \\operatorname{round}(\\alpha / 90^\\circ))$ sectors and
$k - 1$ separatrices, leaving along the field-aligned directions nearest
the even subdivision $\\alpha j / k$ of its sector.

``scipy.ndimage`` (label, distance transform) is used when importable for
the partition statistics; the field and the tracer are NumPy only.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from cases import oriented

CORNER_TURN = math.radians(30.0)


def _wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


@dataclass
class Result:
    name: str
    h: float
    shape: tuple
    origin: tuple
    fluid: np.ndarray
    z: np.ndarray
    segments: np.ndarray
    loops: list
    singularities: list = field(default_factory=list)   # (x, y, index)
    corners: list = field(default_factory=list)          # (x, y, alpha_deg, k)
    traces: list = field(default_factory=list)           # dict(points, start, end_kind)
    stats: dict = field(default_factory=dict)
    labels: np.ndarray | None = None


def _inside(points, loops):
    """Even-odd point-in-domain over all loops."""
    x = points[:, 0]
    y = points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    for loop in loops:
        a = loop
        b = np.roll(loop, -1, axis=0)
        for (x1, y1), (x2, y2) in zip(a, b):
            if y1 == y2:
                continue
            crosses = (y1 > y) != (y2 > y)
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            inside ^= crosses & (xc > x)
    return inside


def solve_field(loops, n=320, name="case"):
    t0 = time.time()
    loops = oriented([np.asarray(loop, dtype=float) for loop in loops])
    allpts = np.vstack(loops)
    lo, hi = allpts.min(axis=0), allpts.max(axis=0)
    h = float(max(hi - lo)) / n
    pad = 3 * h
    nx = int(math.ceil((hi[0] - lo[0] + 2 * pad) / h))
    ny = int(math.ceil((hi[1] - lo[1] + 2 * pad) / h))
    origin = (lo[0] - pad, lo[1] - pad)
    xs = origin[0] + (np.arange(nx) + 0.5) * h
    ys = origin[1] + (np.arange(ny) + 0.5) * h
    X, Y = np.meshgrid(xs, ys)
    fluid = _inside(np.column_stack((X.ravel(), Y.ravel())), loops).reshape(ny, nx)
    segs = []
    for li, loop in enumerate(loops):
        b = np.roll(loop, -1, axis=0)
        for p, q in zip(loop, b):
            if np.linalg.norm(q - p) > 0:
                segs.append((p[0], p[1], q[0], q[1], li))
    segs = np.asarray(segs)
    # boundary pixels: fluid with a solid 4-neighbour (or the raster edge)
    padded = np.pad(fluid, 1, constant_values=False)
    solid_nb = (~padded[:-2, 1:-1]) | (~padded[2:, 1:-1]) | (~padded[1:-1, :-2]) | (~padded[1:-1, 2:])
    boundary = fluid & solid_nb
    interior = fluid & ~boundary
    by, bx = np.nonzero(boundary)
    bp = np.column_stack((xs[bx], ys[by]))
    theta = np.empty(len(bp))
    ax, ay, qx, qy = segs[:, 0], segs[:, 1], segs[:, 2], segs[:, 3]
    dx, dy = qx - ax, qy - ay
    length2 = dx * dx + dy * dy
    seg_angle = np.arctan2(dy, dx)
    for start in range(0, len(bp), 2000):
        chunk = bp[start:start + 2000]
        px = chunk[:, 0:1]
        py = chunk[:, 1:2]
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0, 1)
        d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
        theta[start:start + 2000] = seg_angle[np.argmin(d2, axis=1)]
    z = np.full((ny, nx), np.nan + 0j)
    z[by, bx] = np.exp(4j * theta)
    # Laplace for the interior, Dirichlet on boundary pixels
    index = -np.ones((ny, nx), dtype=int)
    iy, ix = np.nonzero(interior)
    index[iy, ix] = np.arange(len(iy))
    count = len(iy)
    nbrs = []
    rhs = np.zeros(count, dtype=complex)
    for dyy, dxx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        jy, jx = iy + dyy, ix + dxx
        j = index[jy, jx]
        nbrs.append(j)
        known = j < 0
        rhs[known] += z[jy[known], jx[known]]
    nbrs = np.asarray(nbrs)

    def apply(u):
        out = 4.0 * u
        for j in nbrs:
            m = j >= 0
            out[m] -= u[j[m]]
        return out

    u = np.zeros(count, dtype=complex)
    r = rhs - apply(u)
    p = r.copy()
    rr = np.vdot(r, r).real
    iterations = 0
    for iterations in range(1, 20000):
        Ap = apply(p)
        alpha = rr / np.vdot(p, Ap).real
        u += alpha * p
        r -= alpha * Ap
        new = np.vdot(r, r).real
        if math.sqrt(new) < 1e-9 * math.sqrt(np.vdot(rhs, rhs).real):
            break
        p = r + (new / rr) * p
        rr = new
    z[iy, ix] = u
    res = Result(name, h, (ny, nx), origin, fluid, z, segs, loops)
    res.stats["cg_iterations"] = iterations
    res.stats["pixels"] = int(fluid.sum())
    res.stats["t_field"] = round(time.time() - t0, 2)
    return res


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------


def sample(res: Result, x, y):
    """Bilinear z at (x, y); None when any of the four pixels is solid."""
    fx = (x - res.origin[0]) / res.h - 0.5
    fy = (y - res.origin[1]) / res.h - 0.5
    i, j = int(math.floor(fx)), int(math.floor(fy))
    ny, nx = res.shape
    if i < 0 or j < 0 or i + 1 >= nx or j + 1 >= ny:
        return None
    block = res.fluid[j:j + 2, i:i + 2]
    if not block.all():
        return None
    tx, ty = fx - i, fy - j
    z = res.z
    return ((1 - tx) * (1 - ty) * z[j, i] + tx * (1 - ty) * z[j, i + 1]
            + (1 - tx) * ty * z[j + 1, i] + tx * ty * z[j + 1, i + 1])


def singularities(res: Result):
    z = res.z
    f = res.fluid
    a = np.angle(z)
    ok = f[:-1, :-1] & f[:-1, 1:] & f[1:, 1:] & f[1:, :-1]
    a00, a10, a11, a01 = a[:-1, :-1], a[:-1, 1:], a[1:, 1:], a[1:, :-1]
    w = _wrap(a10 - a00) + _wrap(a11 - a10) + _wrap(a01 - a11) + _wrap(a00 - a01)
    w = np.where(ok, np.round(w / (2 * np.pi)), 0)
    jy, ix = np.nonzero(w)
    out = []
    for j, i in zip(jy, ix):
        x = res.origin[0] + (i + 1.0) * res.h
        y = res.origin[1] + (j + 1.0) * res.h
        out.append((x, y, w[j, i] / 4.0))
    res.singularities = out
    return out


def corners(res: Result):
    out = []
    for li, loop in enumerate(res.loops):
        n = len(loop)
        for k in range(n):
            a, p, b = loop[k - 1], loop[k], loop[(k + 1) % n]
            t1 = (p - a) / np.linalg.norm(p - a)
            t2 = (b - p) / np.linalg.norm(b - p)
            turn = math.atan2(t1[0] * t2[1] - t1[1] * t2[0], t1 @ t2)
            if abs(turn) > CORNER_TURN:
                alpha = math.pi - turn
                kk = max(1, int(round(alpha / (math.pi / 2))))
                out.append((p[0], p[1], math.degrees(alpha), kk, t2[0], t2[1]))
    res.corners = out
    return out


def _aligned(res: Result, cx, cy, radius, lo, hi, samples=720):
    """Angles phi in [lo, hi] where a cross direction points along phi."""
    phis = np.linspace(lo, hi, samples)
    g = []
    for phi in phis:
        z = sample(res, cx + radius * math.cos(phi), cy + radius * math.sin(phi))
        g.append(np.nan if z is None or abs(z) == 0 else _wrap(4 * phi - np.angle(z)))
    g = np.asarray(g)
    found = []
    for k in range(len(phis) - 1):
        g0, g1 = g[k], g[k + 1]
        if np.isnan(g0) or np.isnan(g1):
            continue
        if g0 <= 0 < g1 and g1 - g0 < 1.0:
            t = -g0 / (g1 - g0)
            found.append(phis[k] + t * (phis[k + 1] - phis[k]))
    return found


def trace(res: Result, x, y, heading, stop_points, max_length):
    """Follow the cross field from (x, y) along ``heading`` (radians)."""
    step = 0.5 * res.h
    d = complex(math.cos(heading), math.sin(heading))
    points = [(x, y)]
    length = 0.0
    p = complex(x, y)

    def direction(q, dprev):
        z = sample(res, q.real, q.imag)
        if z is None or abs(z) == 0:
            return None
        th = np.angle(z) / 4.0
        best = None
        for kk in range(4):
            c = complex(math.cos(th + kk * math.pi / 2), math.sin(th + kk * math.pi / 2))
            dot = (c * dprev.conjugate()).real
            if best is None or dot > best[0]:
                best = (dot, c)
        return best[1]

    while length < max_length:
        d1 = direction(p, d)
        if d1 is None:
            return points, "boundary"
        mid = p + 0.5 * step * d1
        d2 = direction(mid, d1)
        if d2 is None:
            return points, "boundary"
        p = p + step * d2
        d = d2
        length += step
        points.append((p.real, p.imag))
        if length > 4 * res.h:
            for (sx, sy) in stop_points:
                if abs(p - complex(sx, sy)) < 1.5 * res.h:
                    points.append((sx, sy))
                    return points, "singularity"
    return points, "limit"


def separatrices(res: Result, max_length=None):
    t0 = time.time()
    ny, nx = res.shape
    if max_length is None:
        max_length = 3.0 * (nx + ny) * res.h
    sing = res.singularities
    stops = [(s[0], s[1]) for s in sing]
    traces = []
    radius = 2.5 * res.h
    for sx, sy, idx in sing:
        dirs = _aligned(res, sx, sy, radius, -np.pi, np.pi)
        for phi in dirs:
            start = (sx + radius * math.cos(phi), sy + radius * math.sin(phi))
            pts, kind = trace(res, start[0], start[1], phi, stops, max_length)
            traces.append({"points": [(sx, sy)] + pts, "from": "singularity", "index": idx, "end": kind})
    for cx, cy, alpha_deg, k, tx, ty in res.corners:
        if k < 2:
            continue
        alpha = math.radians(alpha_deg)
        base = math.atan2(ty, tx)
        r = 3.0 * res.h
        while True:
            found = _aligned(res, cx, cy, r, base + 0.08, base + alpha - 0.08)
            if found or r > 12 * res.h:
                break
            r *= 1.5
        for j in range(1, k):
            ideal = base + alpha * j / k
            phi = min(found, key=lambda f: abs(f - ideal)) if found else ideal
            start = (cx + r * math.cos(phi), cy + r * math.sin(phi))
            pts, kind = trace(res, start[0], start[1], phi, stops, max_length)
            traces.append({"points": [(cx, cy)] + pts, "from": "corner", "alpha": alpha_deg, "end": kind,
                           "aligned": bool(found)})
    res.traces = traces
    res.stats["t_trace"] = round(time.time() - t0, 2)
    return traces


# ---------------------------------------------------------------------------
# partition statistics
# ---------------------------------------------------------------------------


def _rasterize(res: Result, points, out, value=True):
    ny, nx = res.shape
    pts = np.asarray(points)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        steps = max(1, int(4 * math.hypot(x1 - x0, y1 - y0) / res.h))
        for t in np.linspace(0, 1, steps + 1):
            i = int((x0 + t * (x1 - x0) - res.origin[0]) / res.h)
            j = int((y0 + t * (y1 - y0) - res.origin[1]) / res.h)
            if 0 <= i < nx and 0 <= j < ny:
                out[j, i] = value


def partition(res: Result):
    from scipy import ndimage

    t0 = time.time()
    ny, nx = res.shape
    cut = np.zeros((ny, nx), dtype=bool)
    owner = [np.zeros((ny, nx), dtype=bool) for _ in res.traces]
    for k, tr in enumerate(res.traces):
        _rasterize(res, tr["points"], owner[k])
        cut |= owner[k]
    cut = ndimage.binary_dilation(cut, iterations=1)
    free = res.fluid & ~cut
    labels, count = ndimage.label(free)
    res.labels = labels
    # nodes: singularities, corners, trace ends at walls, crossings
    nodes = [(s[0], s[1]) for s in res.singularities] + [(c[0], c[1]) for c in res.corners]
    nodes += [tr["points"][-1] for tr in res.traces if tr["end"] == "boundary"]
    stack = np.sum(np.stack(owner), axis=0) if owner else np.zeros((ny, nx))
    crossing_labels, ncross = ndimage.label(stack >= 2)
    near_pairs = 0
    for c in range(1, ncross + 1):
        jj, ii = np.nonzero(crossing_labels == c)
        if len(jj) > 12:
            near_pairs += 1
            continue
        point = (res.origin[0] + (ii.mean() + 0.5) * res.h, res.origin[1] + (jj.mean() + 0.5) * res.h)
        # two traces leaving the same singularity or corner overlap there:
        # that is the start node, not a crossing
        if any(math.hypot(point[0] - x, point[1] - y) < 4 * res.h for x, y in nodes):
            continue
        nodes.append(point)
    node_px = []
    for x, y in nodes:
        node_px.append((int((y - res.origin[1]) / res.h), int((x - res.origin[0]) / res.h)))
    sides = []
    thin = []
    edt = ndimage.distance_transform_edt(free)
    areas = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, count + 1))
    big = [lab for lab, area in zip(range(1, count + 1), areas) if area >= 6]
    for lab in big:
        mask = labels == lab
        grown = ndimage.binary_dilation(mask, iterations=4)
        k = sum(1 for (j, i) in node_px if 0 <= j < ny and 0 <= i < nx and grown[j, i])
        sides.append(k)
        r = edt[mask].max()
        thin.append(min(1.0, 4 * r * r / mask.sum()))
    res.stats.update({
        "patches": len(big),
        "patch_nodes": {str(k): sides.count(k) for k in sorted(set(sides))},
        "four_node_patches": sides.count(4),
        "thinnest": round(min(thin), 4) if thin else None,
        "near_coincident": near_pairs,
        "t_partition": round(time.time() - t0, 2),
    })
    return res


def summarize(res: Result):
    sing = res.singularities
    res.stats["singularities"] = {
        "+1/4 (valence 3)": sum(1 for s in sing if s[2] > 0),
        "-1/4 (valence 5)": sum(1 for s in sing if s[2] < 0),
        "other": sum(1 for s in sing if abs(abs(s[2]) - 0.25) > 1e-9),
    }
    ends = {}
    for tr in res.traces:
        ends[tr["end"]] = ends.get(tr["end"], 0) + 1
    res.stats["traces"] = len(res.traces)
    res.stats["trace_ends"] = ends
    res.stats["corners"] = len(res.corners)
    res.stats["corner_k"] = {str(k): sum(1 for c in res.corners if c[3] == k) for k in sorted({c[3] for c in res.corners})}
    return res.stats


def run(loops, n=320, name="case", with_partition=True):
    res = solve_field(loops, n, name)
    singularities(res)
    corners(res)
    separatrices(res)
    if with_partition:
        partition(res)
    summarize(res)
    return res
