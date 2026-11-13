"""Boundary vertices that slide along their boundary.

A boundary vertex may slide when its two exterior edges form one smooth path
of one patch: the same patch (or both unassigned) on both, no corner there -
the path turns by at most ``SLIDE_CORNER`` degrees, the corner threshold
``add_ring`` and the smoother's band rule use - exactly two exterior edges,
and not a ``cyclic`` patch (its partner would no longer match). Corners,
patch junctions and trailing edges never move.

The geometry must not drift: each boundary chain (the exterior edges between
two pinned vertices, or a closed loop pinned at its smallest vertex ID) is
taken once, as a path parameterised by arc length, and every edge is re-cut
from that original path whatever positions its ends take. A piece that lies
on lines and polyLines stays a line (or the polyLine through the original
breakpoints it covers); a piece on arcs of one circle stays an arc of it;
anything else becomes a spline through the original's interpolation points
inside it. ``Chain.deviation`` measures the largest distance from re-cut
edges to the original path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Sequence

from .domain import EdgeGeometry, EdgeKey, TopologyError, edge_key
from .model import MeshModel

# A boundary vertex whose path turns more than this (degrees) is a corner.
SLIDE_CORNER = 30.0
# Samples per curved original edge for its arc length and distances.
DENSE = 400
# Re-cut edges may leave the original path by at most this fraction of the
# chain's length (measured, reported, and a smoothing refused past it).
SLIDE_TOLERANCE = 1.0e-5


def _circle(a: complex, m: complex, b: complex) -> tuple[complex, float] | None:
    d = 2.0 * (a.real * (m.imag - b.imag) + m.real * (b.imag - a.imag) + b.real * (a.imag - m.imag))
    if abs(d) <= 1.0e-300:
        return None
    sa, sm, sb = abs(a) ** 2, abs(m) ** 2, abs(b) ** 2
    centre = complex(
        (sa * (m.imag - b.imag) + sm * (b.imag - a.imag) + sb * (a.imag - m.imag)) / d,
        (sa * (b.real - m.real) + sm * (a.real - b.real) + sb * (m.real - a.real)) / d,
    )
    return centre, abs(a - centre)


@dataclass
class _Piece:
    """One original edge in chain order."""

    key: EdgeKey
    kind: str
    start: float                 # arc length at its first vertex
    end: float
    a: complex
    b: complex
    breaks: list[tuple[float, complex]] = field(default_factory=list)   # polyLine/spline points
    dense: list[tuple[float, complex]] = field(default_factory=list)    # curved: (s, point)
    centre: complex = 0j
    radius: float = 0.0
    angle: float = 0.0
    sweep: float = 0.0
    patch: str | None = None


class Chain:
    """A boundary chain, taken once from a session."""

    def __init__(self, model: MeshModel, vertices: list[str], closed: bool) -> None:
        self.vertices = list(vertices)
        self.closed = closed
        self.pieces: list[_Piece] = []
        position = {v: complex(model.vertices[v].x, model.vertices[v].y) for v in vertices}
        s = 0.0
        count = len(vertices) if closed else len(vertices) - 1
        for k in range(count):
            first, second = vertices[k], vertices[(k + 1) % len(vertices)]
            key = edge_key(first, second)
            kind = model.edge_type(key)
            a, b = position[first], position[second]
            piece = _Piece(key, kind, s, s, a, b, patch=model.edge_boundaries.get(key))
            if kind == "line":
                length = abs(b - a)
            elif kind == "arc":
                (mx, my), = model.edge_control_points(key)
                fit = _circle(a, complex(mx, my), b)
                if fit is None:
                    raise TopologyError(f"arc {'-'.join(key)} is degenerate")
                piece.centre, piece.radius = fit
                start = math.atan2((a - piece.centre).imag, (a - piece.centre).real)
                middle = math.atan2((complex(mx, my) - piece.centre).imag, (complex(mx, my) - piece.centre).real)
                end = math.atan2((b - piece.centre).imag, (b - piece.centre).real)
                ccw = (end - start) % (2 * math.pi)
                if (middle - start) % (2 * math.pi) <= ccw:
                    piece.sweep = ccw
                else:
                    piece.sweep = ccw - 2 * math.pi
                piece.angle = start
                length = piece.radius * abs(piece.sweep)
            elif kind == "polyLine":
                control = [complex(x, y) for x, y in model.edge_control_points(key)]
                if first != key[0]:
                    control.reverse()
                points = [a, *control, b]
                length = 0.0
                dense = [(s, points[0])]
                for p, q in zip(points, points[1:]):
                    length += abs(q - p)
                    dense.append((s + length, q))
                piece.dense = dense
                piece.breaks = dense[1:-1]
            else:
                fractions = [index / DENSE for index in range(DENSE + 1)]
                if first != key[0]:
                    fractions = [1.0 - f for f in fractions]
                points = [complex(x, y) for x, y in model.edge_points(key, fractions)]
                points[0], points[-1] = a, b
                length = 0.0
                dense = [(s, points[0])]
                for p, q in zip(points, points[1:]):
                    length += abs(q - p)
                    dense.append((s + length, q))
                piece.dense = dense
                control = list(model.edge_control_points(key))
                if first != key[0]:
                    control.reverse()
                for x, y in control:
                    point = complex(x, y)
                    nearest = min(dense, key=lambda item: abs(item[1] - point))
                    piece.breaks.append((nearest[0], point))
            piece.end = s + length
            s += length
            self.pieces.append(piece)
        self.length = s
        self.stations = [piece.start for piece in self.pieces] + ([] if closed else [s])
        self.index = {v: k for k, v in enumerate(vertices)}

    # -- evaluation -------------------------------------------------------

    def _piece(self, s: float) -> _Piece:
        if self.closed:
            s %= self.length
        for piece in self.pieces:
            if s <= piece.end:
                return piece
        return self.pieces[-1]

    def point(self, s: float) -> complex:
        if self.closed:
            s %= self.length
        piece = self._piece(s)
        span = piece.end - piece.start
        f = 0.0 if span <= 0.0 else min(1.0, max(0.0, (s - piece.start) / span))
        if piece.kind == "line":
            return piece.a + f * (piece.b - piece.a)
        if piece.kind == "arc":
            angle = piece.angle + f * piece.sweep
            return piece.centre + piece.radius * complex(math.cos(angle), math.sin(angle))
        dense = piece.dense
        low, high = 0, len(dense) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if dense[middle][0] <= s:
                low = middle
            else:
                high = middle
        (s0, p0), (s1, p1) = dense[low], dense[high]
        t = 0.0 if s1 <= s0 else (s - s0) / (s1 - s0)
        return p0 + t * (p1 - p0)

    def tangent(self, s: float) -> complex:
        h = 1.0e-6 * max(self.length, 1.0e-300)
        before = self.point(s - h) if (self.closed or s - h >= 0.0) else self.point(s)
        after = self.point(s + h) if (self.closed or s + h <= self.length) else self.point(s)
        delta = after - before
        return delta / abs(delta) if abs(delta) > 0.0 else 1.0 + 0j

    # -- re-cutting -------------------------------------------------------

    def _unwrap(self, s0: float, s1: float) -> tuple[float, float]:
        if self.closed and s1 <= s0:
            s1 += self.length
        return s0, s1

    def recut(self, s0: float, s1: float) -> EdgeGeometry | None:
        """The geometry of the piece of the original path from ``s0`` to
        ``s1`` (points in that order); ``None`` for a straight line."""
        s0, s1 = self._unwrap(s0, s1)
        gap = s1 - s0
        eps = 1.0e-9 * max(self.length, 1.0e-300)
        covered = []
        for shift in ((0.0, self.length) if self.closed else (0.0,)):
            for piece in self.pieces:
                if piece.start + shift < s1 - eps and piece.end + shift > s0 + eps:
                    covered.append((piece, shift))
        kinds = {piece.kind for piece, _shift in covered}
        a, b = self.point(s0), self.point(s1)
        margin = 1.0e-3 * gap

        def inside(s: float) -> bool:
            return s0 + margin < s < s1 - margin

        if kinds <= {"line", "polyLine"}:
            points = []
            for piece, shift in covered:
                for s, point in [(piece.start + shift, piece.a)] + [(t + shift, p) for t, p in piece.breaks]:
                    if inside(s):
                        points.append((s, point))
            points.sort(key=lambda item: item[0])
            chord = b - a
            if all(abs(((p - a) * chord.conjugate()).imag) / max(abs(chord), 1.0e-300) <= 1.0e-12 * max(self.length, 1.0)
                   for _s, p in points):
                return None
            return EdgeGeometry("polyLine", tuple((p.real, p.imag) for _s, p in points))
        if kinds == {"arc"}:
            first = covered[0][0]
            if all(abs(piece.centre - first.centre) <= 1.0e-7 * max(first.radius, 1.0)
                   and abs(piece.radius - first.radius) <= 1.0e-7 * max(first.radius, 1.0)
                   for piece, _shift in covered):
                middle = self.point(0.5 * (s0 + s1))
                return EdgeGeometry("arc", ((middle.real, middle.imag),))
        points = []
        for piece, shift in covered:
            candidates = [(piece.start + shift, piece.a)]
            if piece.kind == "spline":
                # The original curve sampled evenly at a quarter of its
                # point spacing: a Catmull-Rom through them follows the
                # original closely right up to the new ends.
                spacing = (piece.end - piece.start) / (8.0 * (len(piece.breaks) + 1))
                steps = max(1, min(400, round((piece.end - piece.start) / max(spacing, 1.0e-300))))
                for k in range(1, steps):
                    t = piece.start + (piece.end - piece.start) * k / steps
                    candidates.append((t + shift, self.point(t)))
                # Where two original edges meet the path may kink a little;
                # samples closing in on the joint keep the new spline's
                # rounding of it small.
                for joint in (piece.start, piece.end):
                    for k in range(1, 6):
                        for sign in (-1.0, 1.0):
                            t = joint + sign * spacing * 0.5 ** k
                            if piece.start < t < piece.end:
                                candidates.append((t + shift, self.point(t)))
            elif piece.kind == "polyLine":
                candidates += [(t + shift, p) for t, p in piece.breaks]
            elif piece.kind == "arc":
                steps = max(2, math.ceil(abs(piece.sweep) / math.radians(2.0)))
                for k in range(1, steps):
                    t = piece.start + (piece.end - piece.start) * k / steps
                    candidates.append((t + shift, self.point(t)))
                spacing = (piece.end - piece.start) / steps
                for joint in (piece.start, piece.end):
                    for k in range(1, 6):
                        for sign in (-1.0, 1.0):
                            t = joint + sign * spacing * 0.5 ** k
                            if piece.start < t < piece.end:
                                candidates.append((t + shift, self.point(t)))
            for s, point in candidates:
                if inside(s):
                    points.append((s, point))
        points.sort(key=lambda item: item[0])
        if not points:
            middle = self.point(0.5 * (s0 + s1))
            points = [(0.5 * (s0 + s1), middle)]
        return EdgeGeometry("spline", tuple((p.real, p.imag) for _s, p in points))

    def distance(self, point: complex) -> float:
        best = math.inf
        for piece in self.pieces:
            if piece.kind == "line":
                d = piece.b - piece.a
                t = 0.0 if abs(d) == 0.0 else max(0.0, min(1.0, ((point - piece.a) * d.conjugate()).real / abs(d) ** 2))
                best = min(best, abs(point - piece.a - t * d))
            elif piece.kind == "arc":
                angle = math.atan2((point - piece.centre).imag, (point - piece.centre).real)
                offset = (angle - piece.angle) % (2 * math.pi) if piece.sweep > 0 else (piece.angle - angle) % (2 * math.pi)
                if offset <= abs(piece.sweep):
                    best = min(best, abs(abs(point - piece.centre) - piece.radius))
                else:
                    best = min(best, abs(point - piece.a), abs(point - piece.b))
            else:
                for (_s0, p), (_s1, q) in zip(piece.dense, piece.dense[1:]):
                    d = q - p
                    t = 0.0 if abs(d) == 0.0 else max(0.0, min(1.0, ((point - p) * d.conjugate()).real / abs(d) ** 2))
                    best = min(best, abs(point - p - t * d))
        return best


# ---------------------------------------------------------------------------
# Which vertices slide, and their chains
# ---------------------------------------------------------------------------


def _exterior(model: MeshModel) -> dict[str, list[EdgeKey]]:
    by_vertex: dict[str, list[EdgeKey]] = {}
    for current, items in model.edge_occurrences().items():
        if len(items) == 1:
            for vertex in current:
                by_vertex.setdefault(vertex, []).append(current)
    return by_vertex


def _turn(model: MeshModel, vertex: str, edges: Sequence[EdgeKey]) -> float:
    here = complex(model.vertices[vertex].x, model.vertices[vertex].y)
    tangents = []
    for current in edges:
        fraction = 1.0e-4 if current[0] == vertex else 1.0 - 1.0e-4
        ((x, y),) = model.edge_points(current, (fraction,))
        t = complex(x, y) - here
        tangents.append(t / abs(t) if abs(t) > 0.0 else 0j)
    straight = -tangents[0] / tangents[1] if abs(tangents[1]) > 0.0 else 0j
    return abs(math.degrees(math.atan2(straight.imag, straight.real)))


def slidable(model: MeshModel, vertex: str, exterior: dict[str, list[EdgeKey]] | None = None) -> str | None:
    """``None`` when ``vertex`` may slide, else why not."""
    exterior = exterior if exterior is not None else _exterior(model)
    edges = exterior.get(vertex, [])
    if len(edges) != 2:
        return f"{vertex} is not on exactly two exterior edges"
    patches = {model.edge_boundaries.get(current) for current in edges}
    if len(patches) != 1:
        return f"{vertex} is where two patches meet"
    (patch,) = patches
    if patch is not None and model.boundaries[patch].kind == "cyclic":
        return f"{vertex} is on the cyclic patch {patch}, whose partner must stay matched"
    turn = _turn(model, vertex, edges)
    if turn > SLIDE_CORNER:
        return f"{vertex} is a corner (the boundary turns {turn:.1f} degrees there)"
    return None


def chains(model: MeshModel, sliders: set[str]) -> dict[str, Chain]:
    """The chain of every slider (vertices not in ``sliders`` pin the ends)."""
    exterior = _exterior(model)
    found: dict[str, Chain] = {}
    for vertex in sorted(sliders):
        if vertex in found:
            continue
        # walk both ways to pinned vertices
        sequence = [vertex]
        closed = False
        for direction in (0, 1):
            previous, current = vertex, None
            edge = sorted(exterior[vertex])[direction]
            current = edge[1] if edge[0] == vertex else edge[0]
            path = []
            while True:
                if current == vertex:
                    closed = True
                    break
                path.append(current)
                if current not in sliders:
                    break
                options = [e for e in exterior[current] if previous not in e]
                if len(options) != 1:
                    break
                previous, current = current, (options[0][1] if options[0][0] == current else options[0][0])
            if closed:
                sequence = [vertex] + path
                break
            sequence = (sequence + path) if direction == 1 else (list(reversed(path)) + sequence)
        if closed:
            # Pin the loop at its smallest vertex ID: a loop needs an origin.
            start = sequence.index(min(sequence))
            sequence = sequence[start:] + sequence[:start]
            chain = Chain(model, sequence, True)
        else:
            chain = Chain(model, sequence, False)
        for member in chain.vertices[1:] if closed else chain.vertices:
            if member in sliders:
                found[member] = chain
    return found


def neighbours(chain: Chain, vertex: str) -> tuple[str, str]:
    k = chain.index[vertex]
    n = len(chain.vertices)
    return chain.vertices[k - 1], chain.vertices[(k + 1) % n]


def oriented(geometry: EdgeGeometry | None, first: str, second: str) -> EdgeGeometry | None:
    """Geometry given first -> second, stored in canonical order."""
    if geometry is not None and edge_key(first, second)[0] != first:
        return EdgeGeometry(geometry.kind, tuple(reversed(geometry.points)))
    return geometry


def recut_edges(chain: Chain, positions: dict[str, float]) -> dict[EdgeKey, EdgeGeometry | None]:
    """Every chain edge, re-cut for the arc-length positions of its vertices."""
    s = [positions.get(v, chain.stations[k]) for k, v in enumerate(chain.vertices)]
    out: dict[EdgeKey, EdgeGeometry | None] = {}
    count = len(chain.vertices) if chain.closed else len(chain.vertices) - 1
    for k in range(count):
        first, second = chain.vertices[k], chain.vertices[(k + 1) % len(chain.vertices)]
        geometry = chain.recut(s[k], s[(k + 1) % len(s)] if not (chain.closed and k + 1 == len(s)) else s[0])
        key = edge_key(first, second)
        if geometry is not None and key[0] != first:
            geometry = EdgeGeometry(geometry.kind, tuple(reversed(geometry.points)))
        out[key] = geometry
    return out


def deviation(model: MeshModel, chain: Chain, edges: Sequence[EdgeKey]) -> float:
    """Largest distance of the given edges (sampled) from the original chain."""
    worst = 0.0
    for current in edges:
        for x, y in model.edge_points(current, [k / 32.0 for k in range(33)]):
            worst = max(worst, chain.distance(complex(x, y)))
    return worst


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def slide_vertex(
    model: MeshModel,
    vertex: str,
    *,
    fraction: float | None = None,
    distance: float | None = None,
    towards: str | None = None,
) -> dict[str, Any]:
    """Slide one boundary vertex along its two exterior edges: to
    ``fraction`` of the path between its two neighbours (0 at the first,
    1 at ``towards``), or by ``distance`` of arc length towards ``towards``
    (default: the neighbour with the larger ID)."""
    if vertex not in model.vertices:
        raise TopologyError(f"Unknown vertex {vertex!r}")
    if (fraction is None) == (distance is None):
        raise TopologyError("give fraction or distance")
    exterior = _exterior(model)
    why = slidable(model, vertex, exterior)
    if why is not None:
        raise TopologyError(f"{vertex} cannot slide: {why}")
    first_edge, second_edge = sorted(exterior[vertex])
    neighbours = sorted((e[1] if e[0] == vertex else e[0]) for e in (first_edge, second_edge))
    if towards is None:
        towards = neighbours[1]
    if towards not in neighbours:
        raise TopologyError(f"towards must be one of {vertex}'s neighbours on the boundary: {', '.join(neighbours)}")
    start = neighbours[0] if towards == neighbours[1] else neighbours[1]
    chain = Chain(model, [start, vertex, towards], False)
    here = chain.stations[1]
    if fraction is not None:
        target = fraction * chain.length
    else:
        target = here + distance
    margin = 1.0e-6 * chain.length
    if not margin < target < chain.length - margin:
        raise TopologyError(f"{vertex} must stay strictly between {start} and {towards} (path length {chain.length:.6g})")
    point = chain.point(target)
    recut = recut_edges(chain, {vertex: target})
    model.move_vertices({vertex: (point.real, point.imag)}, carry_curves=True, edge_geometry=recut)
    return {
        "vertex": vertex,
        "position": [point.real, point.imag],
        "fraction": target / chain.length,
        "moved": abs(target - here),
        "deviation": deviation(model, chain, list(recut)),
    }
