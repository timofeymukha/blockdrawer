"""Geometry and briefs for the held-out agent-workflow benchmark (Nov 18 2026).

Run:  bin/lp python3 make_cases.py
Writes CASE/geometry/*.dat, CASE/geometry_preview.png and CASE/README.md for
every case below. Point lists are `x y` per line; outer chains are open,
anticlockwise round the fluid and joined end to end; bodies are closed loops
(closing point not repeated), anticlockwise.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
COLORS = {"wall": "k", "inlet": "tab:blue", "outlet": "tab:red", "farfield": "tab:green",
          "cyclic": "tab:purple"}


def polyline(corners, spacing):
    """Densify a polyline through `corners` to about `spacing`, corners kept."""
    out = [np.asarray(corners[0], float)]
    for a, b in zip(corners[:-1], corners[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(1, int(round(np.linalg.norm(b - a) / spacing)))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
    return np.array(out)


def arc(center, radius, a0, a1, n):
    t = np.radians(np.linspace(a0, a1, n))
    return np.column_stack([center[0] + radius * np.cos(t), center[1] + radius * np.sin(t)])


def circle(center, radius, n):
    return arc(center, radius, 0.0, 360.0, n + 1)[:-1]


def naca4_thickness(x, t):
    return 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)


def write(case, name, points, note):
    folder = ROOT / case / "geometry"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{name}.dat", "w") as handle:
        handle.write(f"# {case}: {note}\n")
        for x, y in points:
            handle.write(f"{x:.12g} {y:.12g}\n")


def emit(case, chains, bodies, readme, view=None):
    """chains: [(name, role, points)], bodies: [(name, points)]."""
    (ROOT / case).mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2 if view else 1, figsize=(14 if view else 9, 6))
    axes = np.atleast_1d(axes)
    for ax in axes:
        for name, role, pts in chains:
            ax.plot(pts[:, 0], pts[:, 1], color=COLORS[role], lw=1.5, label=f"{name} ({role})")
        for name, pts in bodies:
            closed = np.vstack([pts, pts[:1]])
            ax.fill(closed[:, 0], closed[:, 1], color="0.75")
            ax.plot(closed[:, 0], closed[:, 1], "k", lw=1.0, label=f"{name} (wall)")
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7, loc="best")
    if view:
        axes[1].set_xlim(view[0], view[2])
        axes[1].set_ylim(view[1], view[3])
    fig.tight_layout()
    fig.savefig(ROOT / case / "geometry_preview.png", dpi=110)
    plt.close(fig)
    for name, role, pts in chains:
        write(case, name, pts, f"{role} chain, anticlockwise round the fluid")
    for name, pts in bodies:
        write(case, name, pts, "wall, closed loop (closing point not repeated), anticlockwise")
    rows = "\n".join(
        f"| `{name}.dat` | {role} | ({pts[0][0]:.4g}, {pts[0][1]:.4g}) | ({pts[-1][0]:.4g}, {pts[-1][1]:.4g}) | {len(pts)} |"
        for name, role, pts in chains)
    rows += "".join(f"\n| `{name}.dat` | wall (closed body) | - | - | {len(pts)} |" for name, pts in bodies)
    table = "| file | role | from | to | points |\n| --- | --- | --- | --- | --- |\n" + rows
    (ROOT / case / "README.md").write_text(readme.strip().replace("@TABLE@", table) + "\n")


COMMON = """
`geometry_preview.png` shows the domain. Lengths are in the units of the
table. How the trial is run, the quality targets and what to deliver are in
`../TRIAL.md`.
"""


def cylinder_channel():
    h, length = 0.41, 2.2
    chains = [
        ("bottom", "wall", polyline([(0, 0), (length, 0)], 0.01)),
        ("outlet", "outlet", polyline([(length, 0), (length, h)], 0.01)),
        ("top", "wall", polyline([(length, h), (0, h)], 0.01)),
        ("inlet", "inlet", polyline([(0, h), (0, 0)], 0.01)),
    ]
    bodies = [("cylinder", circle((0.2, 0.2), 0.05, 240))]
    emit("cylinder_channel", chains, bodies, f"""
# Cylinder in a channel (internal flow with a body)

The laminar benchmark channel: height 0.41, length 2.2, a cylinder of
diameter $D = 0.1$ centred at (0.2, 0.2) - 0.20 from the bottom wall and 0.21
from the top wall, so the case is not symmetric.

@TABLE@

Sizing inputs: first cell width $5\\times10^{{-4}}$ on every wall (cylinder,
top, bottom), core size 0.02, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the cylinder; block lines
following the cylinder's wake to the outlet.
{COMMON}""", view=(0.0, 0.0, 0.6, 0.41))


def u_bend():
    half, width, leg = 0.25, 1.0, 5.0
    outer = half + width
    chains = [
        ("outer_wall", "wall", polyline([(-leg, -outer), (outer, -outer), (outer, outer), (-leg, outer)], 0.05)),
        ("outlet", "outlet", polyline([(-leg, outer), (-leg, half)], 0.05)),
        ("divider", "wall", np.vstack([
            polyline([(-leg, half), (0, half)], 0.05)[:-1],
            arc((0, 0), half, 90, -90, 61),
            polyline([(0, -half), (-leg, -half)], 0.05)[1:]])),
        ("inlet", "inlet", polyline([(-leg, -half), (-leg, -outer)], 0.05)),
    ]
    emit("u_bend", chains, [], f"""
# Two-pass duct with a 180-degree turn (internal flow)

Two parallel passes of width 1 separated by a divider of thickness 0.5 whose
end is a semicircle of radius 0.25 centred at the origin. The duct end is
square: the outer wall has two 90-degree concave corners at (1.25, -1.25) and
(1.25, 1.25), and the clearance between the divider tip and the end wall is
1.0. Flow enters the lower pass at x = -5 and leaves the upper pass at x = -5.

@TABLE@

Sizing inputs: first cell width $2\\times10^{{-3}}$ on every wall, core size
0.08, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band along the divider that follows its
rounded tip.
{COMMON}""", view=(-1.0, -1.4, 1.4, 1.4))


def y_junction():
    width, angle, length = 0.7, 130.0, 4.0
    d = np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))])
    n = np.array([d[1], -d[0]])                      # to the right of the branch axis
    foot = width / math.sin(math.radians(180.0 - angle))
    a0, b0 = np.array([0.0, 1.0]), np.array([foot, 1.0])
    a1 = a0 + length * d
    b1 = a1 + width * n
    chains = [
        ("bottom", "wall", polyline([(-4, 0), (6, 0)], 0.05)),
        ("outlet", "outlet", polyline([(6, 0), (6, 1)], 0.05)),
        ("top_downstream", "wall", polyline([(6, 1), b0], 0.05)),
        ("branch_right", "wall", polyline([b0, b1], 0.05)),
        ("branch_inlet", "inlet", polyline([b1, a1], 0.05)),
        ("branch_left", "wall", polyline([a1, a0], 0.05)),
        ("top_upstream", "wall", polyline([a0, (-4, 1)], 0.05)),
        ("inlet", "inlet", polyline([(-4, 1), (-4, 0)], 0.05)),
    ]
    emit("y_junction", chains, [], f"""
# Y-junction: a side branch joining a channel at 50 degrees (internal flow)

A main channel of height 1 (x from -4 to 6) and a side branch of width 0.7
whose axis makes 50 degrees with the main flow direction (it runs up and
upstream from the junction, towards ({a1[0]:.3f}, {a1[1]:.3f})). The branch
meets the top wall between x = 0 and x = {foot:.4f}. Both junction corners are
sharp: at (0, 1) the solid is a 50-degree wedge (a 310-degree fluid corner),
at ({foot:.4f}, 1) a 130-degree wedge (a 230-degree fluid corner). The branch
inlet is perpendicular to the branch walls.

@TABLE@

Sizing inputs: first cell width $2\\times10^{{-3}}$ on every wall, core size
0.08, growth 1.15. Two inlets (`inlet`, `branch_inlet`), one outlet. Pseudo-2D
(`empty` z patches).

Required of the topology: wall-normal resolution on every wall including the
branch walls up to both junction corners.
{COMMON}""", view=(-1.5, 0.0, 2.5, 3.0))


def cascade():
    beta1, beta2, pitch, thick = math.radians(30.0), math.radians(-45.0), 0.8, 0.14
    delta = beta2 - beta1
    arc_length = delta / (math.sin(beta2) - math.sin(beta1))        # axial chord 1

    def camber(s):
        th = beta1 + delta * s
        return (arc_length * (np.sin(th) - math.sin(beta1)) / delta,
                -arc_length * (np.cos(th) - math.cos(beta1)) / delta, th)

    s = 0.5 * (1 - np.cos(np.linspace(0, math.pi, 201)))
    xc, yc, th = camber(s)
    yt = arc_length * naca4_thickness(s, thick)
    upper = np.column_stack([xc - yt * np.sin(th), yc + yt * np.cos(th)])
    lower = np.column_stack([xc + yt * np.sin(th), yc - yt * np.cos(th)])
    blade = np.vstack([upper[::-1], lower[1:-1]])       # TE -> upper -> LE -> lower
    x_in, x_out = -1.0, 2.0
    sm = np.linspace(0, 1, 121)
    xm, ym, _ = camber(sm)
    mid = np.column_stack([xm, ym])
    te = mid[-1]
    up = polyline([(x_in, x_in * math.tan(beta1)), (0.0, 0.0)], 0.01)
    down = polyline([te, (x_out, te[1] + (x_out - te[0]) * math.tan(beta2))], 0.01)
    line = np.vstack([up[:-1], mid, down[1:]])
    low = line + np.array([0.0, -pitch / 2])
    high = (line + np.array([0.0, pitch / 2]))[::-1]
    chains = [
        ("periodic_lower", "cyclic", low),
        ("outlet", "outlet", polyline([low[-1], high[0]], 0.01)),
        ("periodic_upper", "cyclic", high),
        ("inlet", "inlet", polyline([high[-1], low[0]], 0.01)),
    ]
    emit("cascade", chains, [("blade", blade)], f"""
# Linear turbine cascade passage with periodic boundaries (internal flow)

One blade of a linear cascade, axial chord 1, pitch {pitch} in y. The camber
line is a circular arc from a 30-degree inlet angle to a -45-degree outlet
angle (75 degrees of turning) with a 14 percent thickness distribution and a
sharp trailing edge at ({te[0]:.4f}, {te[1]:.4f}); the leading edge is at the
origin. The periodic boundaries are the camber line moved by plus and minus
half a pitch, continued straight upstream at 30 degrees to the inlet plane
x = {x_in:g} and straight downstream at -45 degrees to the outlet plane
x = {x_out:g}. `periodic_upper.dat` is `periodic_lower.dat` translated by
(0, {pitch}) with the point order reversed (so the boundary stays
anticlockwise): the two are a `cyclic` pair.

@TABLE@

Sizing inputs: first cell width $2\\times10^{{-4}}$ on the blade, core size
0.03, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the blade; block lines following
the wake from the trailing edge to the outlet; matching vertices and cell
counts on the two periodic boundaries so the `cyclic` pair is valid in
OpenFOAM (checkMesh must accept it).
{COMMON}""", view=(-0.3, -0.8, 1.4, 0.6))


def naca4412():
    m, p, t = 0.04, 0.4, 0.12
    x = 0.5 * (1 - np.cos(np.linspace(0, math.pi, 201)))
    yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x**2))
    slope = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    th = np.arctan(slope)
    yt = naca4_thickness(x, t)
    upper = np.column_stack([x - yt * np.sin(th), yc + yt * np.cos(th)])
    lower = np.column_stack([x + yt * np.sin(th), yc - yt * np.cos(th)])
    foil = np.vstack([upper[::-1], lower[1:-1]])
    radius, x_out = 15.0, 20.0
    chains = [
        ("bottom", "farfield", polyline([(0.5, -radius), (x_out, -radius)], 0.25)),
        ("outlet", "outlet", polyline([(x_out, -radius), (x_out, radius)], 0.25)),
        ("top", "farfield", polyline([(x_out, radius), (0.5, radius)], 0.25)),
        ("inlet", "inlet", arc((0.5, 0.0), radius, 90, 270, 181)),
    ]
    emit("naca4412", chains, [("airfoil", foil)], f"""
# NACA 4412 in a C-shaped far field (external flow)

A NACA 4412 of chord 1 (leading edge at the origin, sharp trailing edge at
(1, 0), closed-trailing-edge thickness formula), chord aligned with x. The far
field is C-shaped: a semicircular inlet of radius 15 centred at (0.5, 0),
straight top and bottom at y = +-15 and an outlet plane at x = 20.

@TABLE@

Sizing inputs: $Re = 10^6$; first cell width $1\\times10^{{-4}}$ on the
airfoil, core size 0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the airfoil that continues
downstream from the trailing edge as a wake band to the outlet (a C-grid); a
fan of blocks at the trailing edge is not acceptable.
{COMMON}""", view=(-0.3, -0.5, 1.5, 0.5))


def blunt_plate():
    a, b = 0.2, 0.04
    nose = np.column_stack([a - a * np.cos(np.radians(np.linspace(90, -90, 121))),
                            b * np.sin(np.radians(np.linspace(90, -90, 121)))])
    # nose runs (a, b) -> (0, 0) -> (a, -b)
    plate = np.vstack([
        polyline([(1.0, b), (a, b)], 0.005)[:-1],
        nose,
        polyline([(a, -b), (1.0, -b)], 0.005)[1:],
        polyline([(1.0, -b), (1.0, b)], 0.005)[1:-1],
    ])
    box = (-6.0, -6.0, 12.0, 6.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.25)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.25)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.25)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.25)),
    ]
    emit("blunt_plate", chains, [("plate", plate)], f"""
# Flat plate with an elliptic nose and a blunt base (external flow)

A plate of chord 1 and thickness 0.08: a semi-elliptic nose (semi-axes 0.2
along x and 0.04 along y, leading edge at the origin), flat sides at
y = +-0.04 and a blunt base at x = 1 with two sharp 90-degree corners at
(1, 0.04) and (1, -0.04). Rectangular far field x in [-6, 12], y in [-6, 6].

@TABLE@

Sizing inputs: $Re = 10^5$; first cell width $2\\times10^{{-4}}$ on the
plate (base included), core size 0.3, growth 1.15. Pseudo-2D (`empty` z
patches).

Required of the topology: a wall band round the plate; the two base corners
are block corners; block lines leave both base corners downstream to the
outlet so the base's wake is a strip of its own between the two shear layers.
{COMMON}""", view=(-0.2, -0.4, 1.6, 0.4))


def three_cylinders():
    specs = [("cylinder_a", (0.0, 0.0), 0.5), ("cylinder_b", (1.1, 0.75), 0.25), ("cylinder_c", (1.9, -0.5), 0.35)]
    bodies = [(name, circle(c, r, int(round(480 * r / 0.5)))) for name, c, r in specs]
    box = (-8.0, -8.0, 20.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.25)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.25)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.25)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.25)),
    ]
    gaps = []
    for i in range(3):
        for j in range(i + 1, 3):
            (na, ca, ra), (nb, cb, rb) = specs[i], specs[j]
            gaps.append(f"{na[-1]}-{nb[-1]} {math.dist(ca, cb) - ra - rb:.3f}")
    emit("three_cylinders", chains, bodies, f"""
# Three unequal cylinders, staggered (external flow)

Three circular cylinders of diameters 1.0, 0.5 and 0.7 centred at (0, 0),
(1.1, 0.75) and (1.9, -0.5); the surface-to-surface gaps are {", ".join(gaps)}.
No two centres share an x or a y coordinate. Rectangular far field
x in [-8, 20], y in [-8, 8].

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on every cylinder, core
size 0.4, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round every cylinder; the gaps resolved
by blocks of their own, not by one band squeezed against another.
{COMMON}""", view=(-1.0, -1.5, 3.0, 1.5))


def high_lift():
    source = ROOT.parent / "30p30n" / "geometry"
    bodies = []
    for name in ("slat", "main", "flap"):
        pts = np.loadtxt(source / f"{name}.dat")
        if np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        x, y = pts[:, 0], pts[:, 1]
        if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:      # make it anticlockwise
            pts = pts[::-1]
        bodies.append((name, pts))
    box = (-15.0, -15.0, 25.0, 15.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.5)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.5)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.5)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.5)),
    ]
    emit("high_lift", chains, bodies, f"""
# Three-element high-lift airfoil: slat, main element, flap (external flow)

The 30P30N configuration, stowed chord 1. The slat has a sharp trailing
edge at (0.0856, 0.0042) and a sharp cusp at (0.0482, -0.0893) with its
concave cove between them. The main element has a sharp trailing edge at
(0.7888, 0.0254) and a flap cove at its rear: a step with a concave
90-degree corner at (0.6457, 0.0263) and a convex corner at
(0.6457, -0.0142), under a thin lip. The flap has a blunt trailing edge
0.005 thick at (1.0, -0.1155)-(0.9979, -0.1199). The slots are narrow: 0.024
between the slat's trailing edge and the main element, 0.010 between the main
element's trailing edge and the flap. Rectangular far field
x in [-15, 25], y in [-15, 15].

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-4}}$ on all three elements,
core size 0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round each element, through both slots
and both coves; block lines following each element's wake downstream.

Trial condition: build this one **by hand with the construction operators**
(`add_grid`, `add_ring`, `add_band`, `set_boundary_by`, splits and single
blocks) - not with the topology engine. The engine's result on this geometry
is already known; this trial measures the hand route. Do not open the earlier
sessions or layouts for this geometry (`/tmp/timofey/data/blockdrawer/30p30n/`,
the repository's `cases/30p30n/`, `30p30n-engine*.session.json`).
{COMMON}""", view=(-0.1, -0.25, 1.15, 0.2))


# Round 2 additions: domain classes the skill's route table has no row for.


def nozzle():
    def half(x):
        x = np.asarray(x, float)
        contraction = 0.2 + 0.3 * (1 - np.cos(math.pi * np.clip(-x, 0, 1))) / 2
        expansion = 0.2 + 0.2 * (1 - np.cos(math.pi * np.clip(x, 0, 2) / 2)) / 2
        return np.where(x < 0, contraction, expansion)

    x = np.linspace(-1.0, 3.0, 401)
    lower = np.column_stack([x, -half(x)])
    upper = np.column_stack([x[::-1], half(x[::-1])])
    chains = [
        ("lower_wall", "wall", lower),
        ("outlet", "outlet", polyline([(3.0, -0.4), (3.0, 0.4)], 0.02)),
        ("upper_wall", "wall", upper),
        ("inlet", "inlet", polyline([(-1.0, 0.5), (-1.0, -0.5)], 0.02)),
    ]
    emit("nozzle", chains, [], f"""
# Converging-diverging nozzle (internal flow, curved walls)

A planar nozzle, symmetric about y = 0, x in [-1, 3]. The half height is 0.5
at the inlet, contracts along a cosine to 0.2 at the throat (x = 0), expands
along a cosine to 0.4 at x = 2 and stays 0.4 to the outlet. The walls are
smooth (no corners). Mesh the full duct, not a half.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls, core size
0.04, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band along each wall; the block lines across
the duct meet both walls close to their normals through the contraction and
the throat.
{COMMON}""", view=(-1.0, -0.55, 1.0, 0.55))


def airfoil_tunnel():
    x = 0.5 * (1 - np.cos(np.linspace(0, math.pi, 201)))
    yt = naca4_thickness(x, 0.12)
    foil = np.vstack([np.column_stack([x, yt])[::-1], np.column_stack([x, -yt])[1:-1]])
    alpha = math.radians(-8.0)
    rot = np.array([[math.cos(alpha), -math.sin(alpha)], [math.sin(alpha), math.cos(alpha)]])
    foil = (foil - (0.25, 0.0)) @ rot.T + (0.25, 0.0)
    te = (np.array([1.0, 0.0]) - (0.25, 0.0)) @ rot.T + (0.25, 0.0)
    box = (-3.0, -1.5, 6.0, 1.5)
    chains = [
        ("floor", "wall", polyline([(box[0], box[1]), (box[2], box[1])], 0.05)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.05)),
        ("ceiling", "wall", polyline([(box[2], box[3]), (box[0], box[3])], 0.05)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.05)),
    ]
    emit("airfoil_tunnel", chains, [("airfoil", foil)], f"""
# Airfoil at incidence in a wind tunnel (internal flow with a sharp body)

A NACA 0012 of chord 1 at 8 degrees of incidence (nose up; rotated about its
quarter-chord point (0.25, 0)), with a sharp trailing edge at
({te[0]:.4f}, {te[1]:.4f}), between the floor and ceiling of a tunnel: x in
[-3, 6], y in [-1.5, 1.5]. Floor and ceiling are walls.

@TABLE@

Sizing inputs: first cell width $2\\times10^{{-4}}$ on the airfoil and
$1\\times10^{{-3}}$ on the floor and ceiling, core size 0.1, growth 1.15.
Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the airfoil that continues
downstream from the trailing edge as a wake band to the outlet (a fan of
blocks at the trailing edge is not acceptable); a wall band along the floor
and along the ceiling.
{COMMON}""", view=(-0.3, -0.6, 1.6, 0.6))


def bluff_ground():
    length, height, gap, r = 1.0, 0.3, 0.05, 0.1
    top, bot = gap + height, gap
    body = np.vstack([
        polyline([(length, top), (r, top)], 0.005)[:-1],
        arc((r, top - r), r, 90, 180, 31),
        polyline([(0.0, top - r), (0.0, bot + r)], 0.005)[1:-1],
        arc((r, bot + r), r, 180, 270, 31),
        polyline([(r, bot), (length, bot)], 0.005)[1:],
        polyline([(length, bot), (length, top)], 0.005)[1:-1],
    ])
    box = (-4.0, 0.0, 10.0, 5.0)
    chains = [
        ("ground", "wall", polyline([(box[0], box[1]), (box[2], box[1])], 0.05)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.05)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.05)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.05)),
    ]
    emit("bluff_ground", chains, [("body", body)], f"""
# Bluff body close to the ground (external flow, body-wall gap)

A body of length 1 and height 0.3 whose underside is 0.05 above the ground
(y = 0): front corners rounded with radius 0.1, a flat roof at y = 0.35, a
flat underside at y = 0.05, and a blunt base at x = 1 with two sharp
90-degree corners at (1, 0.35) and (1, 0.05). Domain: x in [-4, 10], y in
[0, 5]; the ground is a wall along the whole domain.

@TABLE@

Sizing inputs: first cell width $5\\times10^{{-4}}$ on the body and on the
ground, core size 0.25, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the body and a wall band along
the ground, both carried through the gap under the body; the two base corners
are block corners, with block lines leaving them downstream to the outlet.
{COMMON}""", view=(-0.4, 0.0, 1.8, 0.7))


def bump_channel():
    """Round 3 (Nov 19 2026): staggered circular-arc bumps on both walls."""
    length, height, half_chord, rise = 8.0, 1.0, 0.5, 0.3
    radius = (half_chord**2 + rise**2) / (2 * rise)
    foot = math.degrees(math.asin(half_chord / radius))

    def bump(centre, wall, sign):
        # points of the arc from its upstream foot to its downstream foot
        cy = wall - sign * (radius - rise)
        angles = np.linspace(90 + foot, 90 - foot, 81)
        return np.column_stack([centre + radius * np.cos(np.radians(angles)),
                                cy + sign * radius * np.sin(np.radians(angles))])

    low, high = 3.0, 3.75
    bottom = np.vstack([
        polyline([(0, 0), (low - half_chord, 0)], 0.02)[:-1],
        bump(low, 0.0, 1.0),
        polyline([(low + half_chord, 0), (length, 0)], 0.02)[1:]])
    top = np.vstack([
        polyline([(length, height), (high + half_chord, height)], 0.02)[:-1],
        bump(high, height, -1.0)[::-1],
        polyline([(high - half_chord, height), (0, height)], 0.02)[1:]])
    chains = [
        ("bottom", "wall", bottom),
        ("outlet", "outlet", polyline([(length, 0), (length, height)], 0.02)),
        ("top", "wall", top),
        ("inlet", "inlet", polyline([(0, height), (0, 0)], 0.02)),
    ]
    emit("bump_channel", chains, [], f"""
# Channel with staggered bumps (internal flow, wall corners on curved walls)

A channel of height 1, x in [0, 8]. The bottom wall carries a circular-arc
bump of chord 1 and height 0.3 centred at x = {low:g} (radius {radius:.4f}); the
top wall carries the same bump, hanging down, centred at x = {high:g}, so the
two overlap for x in [{high - half_chord:g}, {low + half_chord:g}]. Each bump
meets its flat wall at a sharp concave corner (the wall turns {foot:.1f}
degrees there): bottom feet at ({low - half_chord:g}, 0) and
({low + half_chord:g}, 0), top feet at ({high - half_chord:g}, 1) and
({high + half_chord:g}, 1).

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls, core size
0.05, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band along each wall, carried over its bump;
the four bump feet are block corners; the block lines across the channel meet
both walls close to their normals over the bumps.
{COMMON}""", view=(2.2, -0.05, 4.6, 1.05))


def triangle():
    """Round 3 (Nov 19 2026): equilateral triangular cylinder, apex upstream."""
    depth = math.sqrt(3) / 2
    corners = [(0.0, 0.0), (depth, -0.5), (depth, 0.5)]
    body = polyline(corners + corners[:1], 0.005)[:-1]
    box = (-8.0, -8.0, 16.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.1)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.1)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.1)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.1)),
    ]
    emit("triangle", chains, [("prism", body)], f"""
# Triangular cylinder (external flow, three sharp corners, a blunt base)

An equilateral triangle of side 1 with its apex pointing upstream at (0, 0)
and its base at x = {depth:.6f}: base corners at ({depth:.6f}, -0.5) and
({depth:.6f}, 0.5). All three corners are sharp (60 degrees inside the body).
Domain: x in [-8, 16], y in [-8, 8]; flow from the left.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on the prism, core size
0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the prism; the three corners are
block corners; block lines leave the two base corners downstream so that the
wake behind the base is covered to the outlet, and for x up to 6 and y in
[-0.5, 0.5] no cell is wider than 0.1 across the wake (in y).
{COMMON}""", view=(-0.8, -1.2, 2.4, 1.2))


def cavity_channel():
    """Round 4 (Nov 20 2026): rectangular cavity in a channel's bottom wall."""
    length, height, x0, x1, depth = 8.0, 1.0, 3.0, 4.5, 0.5
    bottom = polyline([(0, 0), (x0, 0), (x0, -depth), (x1, -depth), (x1, 0), (length, 0)], 0.02)
    chains = [
        ("bottom", "wall", bottom),
        ("outlet", "outlet", polyline([(length, 0), (length, height)], 0.02)),
        ("top", "wall", polyline([(length, height), (0, height)], 0.02)),
        ("inlet", "inlet", polyline([(0, height), (0, 0)], 0.02)),
    ]
    emit("cavity_channel", chains, [], f"""
# Channel with a rectangular cavity (internal flow, sharp convex and concave wall corners)

A channel of height 1, x in [0, 8]. The bottom wall drops into a rectangular
cavity {x1 - x0:g} long and {depth:g} deep: lips at ({x0:g}, 0) and ({x1:g}, 0),
where the wall turns away from the fluid (270 degrees of fluid), and floor
corners at ({x0:g}, {-depth:g}) and ({x1:g}, {-depth:g}) (90 degrees of fluid).
The bottom wall is one chain through all four corners.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls, the cavity's
three sides included, core size 0.05, growth 1.15. Pseudo-2D (`empty` z
patches).

Required of the topology: the four cavity corners are block corners; the
first cell on every wall, the cavity's side walls and floor included, has the
width asked normal to that wall; the shear layer over the cavity mouth
(y in [-0.05, 0.05] for x between the lips) has no cell taller than 0.01.
{COMMON}""", view=(2.5, -0.6, 5.0, 1.05))


def splitter_cylinder():
    """Round 4 (Nov 20 2026): cylinder with a splitter plate at its rear."""
    radius, half, end = 0.5, 0.03, 1.5
    theta = math.degrees(math.asin(half / radius))
    root = radius * math.cos(math.radians(theta))
    body = np.vstack([
        arc((0.0, 0.0), radius, theta, 360.0 - theta, 601),
        polyline([(root, -half), (end, -half), (end, half), (root, half)], 0.005)[1:-1]])
    box = (-8.0, -8.0, 20.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.1)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.1)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.1)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.1)),
    ]
    emit("splitter_cylinder", chains, [("body", body)], f"""
# Cylinder with a splitter plate (external flow, concave and convex body corners)

A circular cylinder of diameter 1 centred at (0, 0) with a splitter plate
attached at its rear: thickness {2 * half:g}, from the cylinder to x = {end:g},
with a blunt end. The body is one closed curve. It has two concave corners
where the plate meets the cylinder, at ({root:.6f}, {half:g}) and
({root:.6f}, {-half:g}) (about {90 + theta:.1f} degrees of fluid), and two
convex corners at the plate's end, ({end:g}, {half:g}) and ({end:g}, {-half:g}).
Domain: x in [-8, 20], y in [-8, 8]; flow from the left.

@TABLE@

Sizing inputs: first cell width $5\\times10^{{-4}}$ on the body, core size 0.5,
growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the whole body, the plate
included; the two junction corners and the two plate-end corners are block
corners; block lines leave the plate's end downstream so that the wake is
covered to the outlet, and for x up to 6 and y in [-0.5, 0.5] no cell is
wider than 0.1 across the wake (in y).
{COMMON}""", view=(-0.9, -0.9, 2.3, 0.9))


def elbow_vane():
    """Round 4 (Nov 20 2026): 90-degree elbow with a guide vane."""
    inner, outer, leg = 1.0, 2.0, 2.0
    mid, half, a0, a1 = 1.4, 0.02, -80.0, -10.0
    outer_wall = np.vstack([
        polyline([(-leg, -outer), (0, -outer)], 0.02)[:-1],
        arc((0, 0), outer, -90.0, 0.0, 158),
        polyline([(outer, 0), (outer, leg)], 0.02)[1:]])
    inner_wall = np.vstack([
        polyline([(inner, leg), (inner, 0)], 0.02)[:-1],
        arc((0, 0), inner, 0.0, -90.0, 80),
        polyline([(0, -inner), (-leg, -inner)], 0.02)[1:]])

    def tip(angle, start):
        c = (mid * math.cos(math.radians(angle)), mid * math.sin(math.radians(angle)))
        return arc(c, half, start, start + 180.0, 21)

    vane = np.vstack([
        arc((0, 0), mid + half, a0, a1, 351)[:-1],
        tip(a1, a1)[:-1],
        arc((0, 0), mid - half, a1, a0, 341)[:-1],
        tip(a0, a0 + 180.0)[:-1]])
    chains = [
        ("outer_wall", "wall", outer_wall),
        ("outlet", "outlet", polyline([(outer, leg), (inner, leg)], 0.02)),
        ("inner_wall", "wall", inner_wall),
        ("inlet", "inlet", polyline([(-leg, -inner), (-leg, -outer)], 0.02)),
    ]
    emit("elbow_vane", chains, [("vane", vane)], f"""
# Elbow with a guide vane (internal flow, curved walls, a thin body in the bend)

A 90-degree elbow of width 1: a straight leg along x (x in [-2, 0], y in
[-2, -1]), a bend about (0, 0) between the radii {inner:g} and {outer:g}, and a
straight leg along y (x in [1, 2], y in [0, 2]). Flow enters at x = -2 and
leaves at y = 2. In the bend sits a guide vane: a circular-arc plate about
(0, 0) with mean radius {mid:g}, thickness {2 * half:g}, from {a0:g} to {a1:g}
degrees, with semicircular ends of radius {half:g}. It leaves a passage
{mid - half - inner:g} wide on the inner side and {outer - mid - half:g} wide on
the outer side. Each wall is one chain; the walls are tangent where the
straight legs meet the bend (no wall corner).

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls and on the
vane, core size 0.05, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band along each wall and round the vane,
its two rounded ends included; in both passages beside the vane the block
lines across the passage meet the wall and the vane close to their normals;
block lines leave the vane's downstream end towards the outlet.
{COMMON}""", view=(-0.3, -2.1, 2.1, 0.3))


def orifice_channel():
    """Round 5 (Nov 21 2026): channel with an orifice of two thin plates."""
    length, height, x0, x1, reach = 8.0, 1.0, 3.0, 3.1, 0.3
    bottom = polyline([(0, 0), (x0, 0), (x0, reach), (x1, reach), (x1, 0), (length, 0)], 0.02)
    top = polyline([(length, height), (x1, height), (x1, height - reach), (x0, height - reach),
                    (x0, height), (0, height)], 0.02)
    chains = [
        ("bottom", "wall", bottom),
        ("outlet", "outlet", polyline([(length, 0), (length, height)], 0.02)),
        ("top", "wall", top),
        ("inlet", "inlet", polyline([(0, height), (0, 0)], 0.02)),
    ]
    emit("orifice_channel", chains, [], f"""
# Channel with an orifice (internal flow, thin plates standing on both walls)

A channel of height 1, x in [0, 8]. At x in [{x0:g}, {x1:g}] a plate {x1 - x0:g}
thick stands {reach:g} high on each wall, leaving an opening of
{height - 2 * reach:g} between the plate tips (y in [{reach:g}, {height - reach:g}]).
Each wall is one chain over its plate: two foot corners (90 degrees of fluid)
and two tip corners (270 degrees of fluid) per plate.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls, the plates'
faces and tips included, core size 0.05, growth 1.15. Pseudo-2D (`empty` z
patches).

Required of the topology: the eight plate corners are block corners; the
first cell on every wall, the plates' upstream and downstream faces and their
tips included, has the width asked normal to that wall; the jet behind the
opening (x in [{x1:g}, 5], y in [0.25, 0.75]) has no cell taller than 0.02.
{COMMON}""", view=(2.5, -0.05, 4.0, 1.05))


def tandem_airfoils():
    """Round 5 (Nov 21 2026): two airfoils, the second below the first's wake."""
    x = 0.5 * (1 - np.cos(np.linspace(0, math.pi, 201)))
    yt = naca4_thickness(x, 0.12)
    unit = np.vstack([np.column_stack([x, yt])[::-1], np.column_stack([x, -yt])[1:-1]])

    def placed(chord, alpha_deg, nose):
        a = math.radians(-alpha_deg)
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        pts = (unit * chord) @ rot.T + nose
        te = np.array([chord, 0.0]) @ rot.T + nose
        return pts, te

    front, te_front = placed(1.0, 4.0, (0.0, 0.0))
    rear, te_rear = placed(0.6, 6.0, (1.8, -0.3))
    box = (-8.0, -8.0, 20.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.25)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.25)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.25)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.25)),
    ]
    emit("tandem_airfoils", chains, [("front", front), ("rear", rear)], f"""
# Two airfoils in tandem (external flow, a wake passing over a second body)

Two NACA 0012 airfoils with sharp trailing edges. `front`: chord 1, nose at
(0, 0), 4 degrees of incidence (nose up), trailing edge at
({te_front[0]:.4f}, {te_front[1]:.4f}). `rear`: chord 0.6, nose at (1.8, -0.3),
6 degrees of incidence, trailing edge at ({te_rear[0]:.4f}, {te_rear[1]:.4f}).
The free stream runs along +x, so the front airfoil's wake passes about 0.2
above the rear airfoil. Rectangular far field x in [-8, 20], y in [-8, 8].

@TABLE@

Sizing inputs: first cell width $2\\times10^{{-4}}$ on both airfoils, core
size 0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round each airfoil with no fan at
either trailing edge; block lines leave each trailing edge downstream and
follow that airfoil's wake to the outlet; the front airfoil's wake lines pass
over the rear airfoil in blocks of their own, not merged into the rear
airfoil's wall band.
{COMMON}""", view=(-0.3, -0.8, 3.0, 0.5))


def eccentric_annulus():
    """Round 5 (Nov 21 2026): a closed domain between two eccentric circles."""
    radius, inner_r, centre = 1.0, 0.35, (0.3, -0.2)
    outer = circle((0.0, 0.0), radius, 720)
    inner = circle(centre, inner_r, 360)
    offset = math.hypot(*centre)
    emit("eccentric_annulus", [("outer", "wall", outer)], [("inner", inner)], f"""
# Eccentric annulus (a closed domain: no inlet, no outlet)

The fluid lies between an outer circular wall of radius {radius:g} centred at
(0, 0) and a cylinder of radius {inner_r:g} centred at ({centre[0]:g}, {centre[1]:g}).
The gap is {radius - inner_r - offset:.3f} at its narrowest and
{radius - inner_r + offset:.3f} at its widest. `outer.dat` is a closed loop
like a body (the closing point is not repeated), anticlockwise, with the
fluid inside it; the "from" and "to" of its row below are its first and last
points, not ends.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both walls, core size
0.04, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band along the whole of each wall; two
patches, `outer` and `inner`, both of type `wall`, and no other side patch.
{COMMON}""")


def ellipse_incidence():
    """Round 6 (Nov 22 2026): a 4:1 ellipse at incidence, one smooth body."""
    a, b, alpha = 0.5, 0.125, math.radians(-20.0)
    t = np.radians(np.linspace(0.0, 360.0, 481)[:-1])
    pts = np.column_stack([a * np.cos(t), b * np.sin(t)])
    rot = np.array([[math.cos(alpha), -math.sin(alpha)], [math.sin(alpha), math.cos(alpha)]])
    body = pts @ rot.T
    box = (-8.0, -8.0, 20.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.25)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.25)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.25)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.25)),
    ]
    emit("ellipse_incidence", chains, [("ellipse", body)], f"""
# Ellipse at incidence (external flow, one smooth body with two tight ends)

An ellipse with semi-axes {a:g} and {b:g} (4:1), centred at the origin, its
long axis turned 20 degrees nose up against the free stream along +x. No
sharp corner anywhere; the two ends have a radius of curvature of
{b * b / a:.4g}. Rectangular far field x in [-8, 20], y in [-8, 8].

@TABLE@

Sizing inputs: first cell width $5\\times10^{{-4}}$ on the ellipse, core size
0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round the whole ellipse with spokes
close to the wall normal round both ends; block lines leave the rear end
downstream and cover the wake to the outlet; no cell wider than 0.1 across
the wake (in y) for x up to 6 and |y| <= 0.5.
{COMMON}""", view=(-1.0, -0.8, 2.0, 0.8))


def tube_pair_periodic():
    """Round 6 (Nov 22 2026): two staggered tubes between straight periodic sides."""
    x0, x1, half = -2.0, 6.0, 1.0
    specs = [("tube_a", (0.0, -0.45), 0.3), ("tube_b", (1.4, 0.45), 0.3)]
    bodies = [(name, circle(c, r, 240)) for name, c, r in specs]
    low = polyline([(x0, -half), (x1, -half)], 0.02)
    high = low[::-1] + np.array([0.0, 2 * half])
    chains = [
        ("periodic_lower", "cyclic", low),
        ("outlet", "outlet", polyline([(x1, -half), (x1, half)], 0.02)),
        ("periodic_upper", "cyclic", high),
        ("inlet", "inlet", polyline([(x0, half), (x0, -half)], 0.02)),
    ]
    gap = math.dist(specs[0][1], specs[1][1]) - 0.6
    emit("tube_pair_periodic", chains, bodies, f"""
# Two staggered tubes in a periodic row (internal flow, straight `cyclic` sides)

A slab x in [{x0:g}, {x1:g}], y in [-{half:g}, {half:g}], repeating in y with
pitch {2 * half:g}: `periodic_upper.dat` is `periodic_lower.dat` translated by
(0, {2 * half:g}) with the point order reversed, and the two are a `cyclic`
pair. Two tubes of radius 0.3: `tube_a` centred at (0, -0.45) and `tube_b` at
(1.4, 0.45). The gap between the tubes is {gap:.3f}; each tube is 0.25 from
the periodic side nearer to it.

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-3}}$ on both tubes, core size
0.05, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round each tube; the gap between the
tubes and the gaps between each tube and the periodic side resolved by
blocks of their own; matching vertices and cell counts on the two periodic
sides so the `cyclic` pair is valid in OpenFOAM (checkMesh must accept it).
{COMMON}""", view=(-1.0, -1.05, 3.0, 1.05))


def flap_airfoil():
    """Round 6 (Nov 22 2026): a main airfoil and a slotted flap, sharp edges, no cove."""
    x = 0.5 * (1 - np.cos(np.linspace(0, math.pi, 201)))
    yt = naca4_thickness(x, 0.12)
    unit = np.vstack([np.column_stack([x, yt])[::-1], np.column_stack([x, -yt])[1:-1]])

    def placed(chord, alpha_deg, nose):
        a = math.radians(-alpha_deg)
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        return (unit * chord) @ rot.T + nose, np.array([chord, 0.0]) @ rot.T + nose

    main, te_main = placed(1.0, 4.0, (0.0, 0.0))
    flap, te_flap = placed(0.3, 25.0, (0.97, -0.105))
    slot = float(np.min(np.linalg.norm(flap[:, None, :] - main[None, :, :], axis=2)))
    box = (-8.0, -8.0, 20.0, 8.0)
    chains = [
        ("bottom", "farfield", polyline([(box[0], box[1]), (box[2], box[1])], 0.25)),
        ("outlet", "outlet", polyline([(box[2], box[1]), (box[2], box[3])], 0.25)),
        ("top", "farfield", polyline([(box[2], box[3]), (box[0], box[3])], 0.25)),
        ("inlet", "inlet", polyline([(box[0], box[3]), (box[0], box[1])], 0.25)),
    ]
    emit("flap_airfoil", chains, [("main", main), ("flap", flap)], f"""
# Airfoil with a slotted flap (external flow, two elements and a slot)

Two NACA 0012 sections with sharp trailing edges. `main`: chord 1, nose at
(0, 0), 4 degrees of incidence (nose up), trailing edge at
({te_main[0]:.4f}, {te_main[1]:.4f}). `flap`: chord 0.3, nose at (0.97, -0.105),
deflected 25 degrees, trailing edge at ({te_flap[0]:.4f}, {te_flap[1]:.4f}).
The flap's nose sits under the main element's trailing edge: the slot between
them is {slot:.4f} at its narrowest. Rectangular far field x in [-8, 20],
y in [-8, 8].

@TABLE@

Sizing inputs: first cell width $1\\times10^{{-4}}$ on both elements, core
size 0.5, growth 1.15. Pseudo-2D (`empty` z patches).

Required of the topology: a wall band round each element, both bands carried
through the slot with blocks of the slot's own between them; no fan at
either trailing edge; block lines follow each element's wake downstream to
the outlet.
{COMMON}""", view=(0.6, -0.5, 1.5, 0.2))


CASES = {build.__name__: build for build in (
    cylinder_channel, u_bend, y_junction, cascade, naca4412, blunt_plate,
    three_cylinders, high_lift, nozzle, airfoil_tunnel, bluff_ground,
    bump_channel, triangle, cavity_channel, splitter_cylinder, elbow_vane,
    orifice_channel, tandem_airfoils, eccentric_annulus,
    ellipse_incidence, tube_pair_periodic, flap_airfoil)}

if __name__ == "__main__":
    import sys
    for name in sys.argv[1:] or CASES:
        CASES[name]()
        print("wrote", name)
