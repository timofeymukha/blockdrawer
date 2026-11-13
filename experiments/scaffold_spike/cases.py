"""Spike cases: closed loops (outer boundary anticlockwise first, bodies
after) built from geometry in the repository, so a clean checkout runs them."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> np.ndarray:
    rows = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            x, y = line.replace(",", " ").split()[:2]
            rows.append((float(x), float(y)))
    points = np.asarray(rows, dtype=float)
    if np.linalg.norm(points[0] - points[-1]) < 1e-12:
        points = points[:-1]
    return points


def _box(x0, y0, x1, y1):
    return np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=float)


def _resample(loop: np.ndarray, spacing: float) -> np.ndarray:
    """Insert points so no segment is longer than ``spacing`` (corners kept)."""
    out = []
    n = len(loop)
    for k in range(n):
        a, b = loop[k], loop[(k + 1) % n]
        pieces = max(1, int(math.ceil(np.linalg.norm(b - a) / spacing)))
        for j in range(pieces):
            out.append(a + (b - a) * j / pieces)
    return np.asarray(out)


def backward_step():
    outer = np.array([(-5, 0), (0, 0), (0, -1), (20, -1), (20, 1), (-5, 1)], dtype=float)
    return {"name": "backward_step", "loops": [outer], "walls": [0]}


def grooved_cylinder():
    body = _load(ROOT / ".claude/skills/mesh-blocking/examples/cylinder1.dat")
    return {"name": "grooved_cylinder", "loops": [_box(-3, -3, 3, 3), body]}


def naca0012():
    body = _load(ROOT / ".claude/skills/mesh-blocking/examples/naca0012_a5.dat")
    return {"name": "naca0012", "loops": [_box(-1.5, -1.5, 2.5, 1.5), body]}


def three_rotated_ellipses():
    import sys

    sys.path.insert(0, str(ROOT / "experiments" / "agentic_topology"))
    import synthetic_cases

    _names, bodies = synthetic_cases.three_rotated_ellipses()
    return {"name": "three_rotated_ellipses", "loops": [_box(-3, -2, 3, 2)] + [np.asarray(b) for b in bodies]}


def p30p30n():
    folder = ROOT / "experiments" / "agentic_topology" / "geometry"
    bodies = [_load(folder / f"30P-30N-{part}-Normalized.dat") for part in ("Slat", "Main", "Flap")]
    return {"name": "30p30n", "loops": [_box(-1.0, -1.2, 2.2, 1.0)] + bodies}


def square():
    return {"name": "square", "loops": [_box(0, 0, 1, 1)]}


def annulus():
    t = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    outer = np.column_stack((np.cos(t), np.sin(t)))
    inner = 0.4 * outer
    return {"name": "annulus", "loops": [outer, inner]}


def l_shape():
    return {"name": "l_shape", "loops": [np.array([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)], dtype=float)]}


CASES = {
    "backward_step": backward_step,
    "grooved_cylinder": grooved_cylinder,
    "naca0012": naca0012,
    "three_rotated_ellipses": three_rotated_ellipses,
    "30p30n": p30p30n,
    "square": square,
    "annulus": annulus,
    "l_shape": l_shape,
}


def oriented(loops):
    """Outer loop anticlockwise, bodies clockwise (fluid on the left)."""
    out = []
    for k, loop in enumerate(loops):
        area = 0.5 * np.sum(loop[:, 0] * np.roll(loop[:, 1], -1) - np.roll(loop[:, 0], -1) * loop[:, 1])
        want_positive = k == 0
        if (area > 0) != want_positive:
            loop = loop[::-1]
        out.append(np.asarray(loop, dtype=float))
    return out


def transformed(case, rotate_degrees=0.0):
    if not rotate_degrees:
        return case
    a = math.radians(rotate_degrees)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    allpts = np.vstack(case["loops"])
    centre = 0.5 * (allpts.min(axis=0) + allpts.max(axis=0))
    loops = [(loop - centre) @ rot.T + centre for loop in case["loops"]]
    return {**case, "loops": loops, "rotation": (rotate_degrees, centre)}
