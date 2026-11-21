"""Blocks lying over one another: a check the topology rules do not make.

``MeshModel.validate`` checks every block strictly convex and every shared
edge oriented both ways, so two blocks on the same side of a shared edge are
refused - but two blocks that share no edge may still cover the same area (a
grid built over a band, a fill across a hole's front) and the session
validates and exports. blockMesh meshes both; the result is two cells over
one place.

``overlapping_blocks`` finds such pairs: a point of one block - a corner, a
sample of an edge (curved edges at ``SAMPLES`` fractions, straight ones at
their midpoint) or one of five interior points of its transfinite map - lying
inside the other block's boundary (curved edges sampled) by more than a
tolerance. Points on an edge or at a vertex the two blocks share are not
tested, so neighbours never count. What it does not see: two blocks whose
edges cross with no such point of either inside the other (two thin blocks
crossing near their ends); ``check`` (checkMesh) remains the authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from .domain import Block, EdgeKey, edge_key

Point = tuple[float, float]

# Fractions at which a curved edge is sampled, for both the boundary
# polygon and the points tested.
SAMPLES = 16
# A point counts as inside another block when it lies inside its sampled
# boundary by more than this fraction of the smaller block's size (and
# more than 1e-9 of the model's extent).
INSIDE_FRACTION = 1.0e-6
# Interior points of a block's transfinite map tested against the others.
INTERIOR = ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))


@dataclass(frozen=True)
class Overlap:
    """Two blocks over one another and a point of ``first`` inside ``second``."""

    first: str
    second: str
    point: Point

    def describe(self) -> str:
        return (f"{self.first} and {self.second} overlap "
                f"(a point of {self.first} at ({self.point[0]:.6g}, {self.point[1]:.6g}) "
                f"lies inside {self.second})")


class _Shape:
    __slots__ = ("id", "boundary", "bbox", "size", "vertices", "edges", "tests")

    def __init__(self, identifier, boundary, vertices, edges, tests):
        self.id = identifier
        self.boundary = boundary
        xs = [p[0] for p in boundary]
        ys = [p[1] for p in boundary]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.size = math.hypot(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])
        self.vertices = vertices
        # tests: (point, the vertex it is, or None; the edge it lies on, or None)
        self.edges = edges
        self.tests = tests


def _edge_samples(model, current: EdgeKey, cache: dict) -> tuple[Point, ...]:
    """Points of an edge at ``SAMPLES`` fractions (both ends included) in
    canonical order; a straight edge's at 0, 1/2 and 1."""
    found = cache.get(current)
    if found is None:
        if current not in model.edge_cells:
            # A block an operator has just made, its edges not yet set
            # (`construct._Build.finish` sets them): straight for now.
            a, b = model.vertices[current[0]], model.vertices[current[1]]
            found = ((a.x, a.y), (0.5 * (a.x + b.x), 0.5 * (a.y + b.y)), (b.x, b.y))
        elif current in model.edge_geometry:
            found = model.edge_points(current, [k / SAMPLES for k in range(SAMPLES + 1)])
        else:
            found = model.edge_points(current, [0.0, 0.5, 1.0])
        cache[current] = found
    return found


def _shape(model, block: Block, cache: dict) -> _Shape:
    boundary: list[Point] = []
    sides: list[list[Point]] = []
    edges = []
    tests: list[tuple[Point, str | None, EdgeKey | None]] = []
    for k in range(4):
        first, second = block.directed_edge(k)
        current = edge_key(first, second)
        edges.append(current)
        points = list(_edge_samples(model, current, cache))
        if current[0] != first:
            points.reverse()
        sides.append(points)
        boundary.extend(points[:-1])
        tests.append((points[0], first, None))
        tests.extend((p, None, current) for p in points[1:-1])
    for u, v in INTERIOR:
        tests.append((_coons(sides, u, v), None, None))
    return _Shape(block.id, boundary, frozenset(block.vertices), frozenset(edges), tests)


def _along(points: Sequence[Point], f: float) -> Point:
    """The point at fraction ``f`` of a sampled side (by sample index)."""
    t = f * (len(points) - 1)
    k = min(int(t), len(points) - 2)
    w = t - k
    a, b = points[k], points[k + 1]
    return a[0] + w * (b[0] - a[0]), a[1] + w * (b[1] - a[1])


def _coons(sides: Sequence[Sequence[Point]], u: float, v: float) -> Point:
    """The bilinear Coons patch of the four sides at (u, v): sides 0 and 2
    run along u (side 2 backwards), sides 1 and 3 along v (side 3 backwards)."""
    s0, s1, s2, s3 = sides
    bottom = _along(s0, u)
    top = _along(s2, 1.0 - u)
    right = _along(s1, v)
    left = _along(s3, 1.0 - v)
    c00, c10, c11, c01 = s0[0], s1[0], s2[0], s3[0]
    x = ((1 - v) * bottom[0] + v * top[0] + (1 - u) * left[0] + u * right[0]
         - ((1 - u) * (1 - v) * c00[0] + u * (1 - v) * c10[0] + u * v * c11[0] + (1 - u) * v * c01[0]))
    y = ((1 - v) * bottom[1] + v * top[1] + (1 - u) * left[1] + u * right[1]
         - ((1 - u) * (1 - v) * c00[1] + u * (1 - v) * c10[1] + u * v * c11[1] + (1 - u) * v * c01[1]))
    return x, y


def _inside_by(point: Point, polygon: Sequence[Point]) -> float:
    """How far ``point`` lies inside ``polygon`` (its distance to the
    boundary), or -1 when it lies outside."""
    x, y = point
    inside = False
    n = len(polygon)
    for k in range(n):
        (x1, y1), (x2, y2) = polygon[k], polygon[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            if x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
                inside = not inside
    if not inside:
        return -1.0
    best = math.inf
    for k in range(n):
        (ax, ay), (bx, by) = polygon[k], polygon[(k + 1) % n]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length <= 0.0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def _point_inside(shape: _Shape, other: _Shape, floor: float,
                  fraction: float = INSIDE_FRACTION) -> Point | None:
    tolerance = max(floor, fraction * min(shape.size, other.size))
    x0, y0, x1, y1 = other.bbox
    for point, vertex, current in shape.tests:
        if vertex is not None and vertex in other.vertices:
            continue
        if current is not None and current in other.edges:
            continue
        if not (x0 + tolerance < point[0] < x1 - tolerance and y0 + tolerance < point[1] < y1 - tolerance):
            continue
        if _inside_by(point, other.boundary) > tolerance:
            return point
    return None


def shapes(model, blocks: Iterable[Block] | None = None) -> list[_Shape]:
    cache: dict = {}
    return [_shape(model, block, cache) for block in (model.blocks if blocks is None else blocks)]


def _floor(model) -> float:
    coordinates = [abs(v.x) + abs(v.y) for v in model.vertices.values()] or [1.0]
    return 1.0e-9 * max(1.0, max(coordinates))


def overlapping_blocks(model, limit: int | None = None) -> list[Overlap]:
    """Every pair of blocks lying over one another (see the module), in model
    order of the first block; at most ``limit`` when given."""
    found: list[Overlap] = []
    every = shapes(model)
    floor = _floor(model)
    order = sorted(range(len(every)), key=lambda k: every[k].bbox[0])
    active: list[int] = []
    pairs: list[tuple[int, int]] = []
    for k in order:
        shape = every[k]
        active = [j for j in active if every[j].bbox[2] >= shape.bbox[0]]
        for j in active:
            other = every[j]
            if other.bbox[1] <= shape.bbox[3] and shape.bbox[1] <= other.bbox[3]:
                pairs.append((min(j, k), max(j, k)))
        active.append(k)
    for i, j in sorted(pairs):
        a, b = every[i], every[j]
        point = _point_inside(a, b, floor)
        if point is not None:
            found.append(Overlap(a.id, b.id, point))
        else:
            point = _point_inside(b, a, floor)
            if point is not None:
                found.append(Overlap(b.id, a.id, point))
        if limit is not None and len(found) >= limit:
            break
    return found


def overlaps_with(model, new: Sequence[_Shape], existing: Sequence[_Shape],
                  fraction: float = INSIDE_FRACTION) -> list[Overlap]:
    """Pairs of a shape in ``new`` and one in ``existing`` lying over one
    another (a new shape's point inside an existing one, or the reverse);
    a pair of two new shapes is tested once."""
    floor = _floor(model)
    found = []
    seen = set()
    for a in new:
        for b in existing:
            if a.id == b.id or (b.id, a.id) in seen:
                continue
            seen.add((a.id, b.id))
            if a.bbox[0] > b.bbox[2] or b.bbox[0] > a.bbox[2] or a.bbox[1] > b.bbox[3] or b.bbox[1] > a.bbox[3]:
                continue
            point = _point_inside(a, b, floor, fraction)
            if point is not None:
                found.append(Overlap(a.id, b.id, point))
                continue
            point = _point_inside(b, a, floor, fraction)
            if point is not None:
                found.append(Overlap(b.id, a.id, point))
    return found


def outline_shape(identifier: str, sides: Sequence[Sequence[Point]]) -> _Shape:
    """A shape not yet in the model (a cell an operator may build): four
    sampled sides, side k from corner k to corner k+1."""
    boundary: list[Point] = []
    tests: list[tuple[Point, str | None, EdgeKey | None]] = []
    for points in sides:
        boundary.extend(points[:-1])
        tests.extend((p, None, None) for p in points[:-1])
    for u, v in INTERIOR:
        tests.append((_coons(sides, u, v), None, None))
    return _Shape(identifier, boundary, frozenset(), frozenset(), tests)


def overlap_text(overlaps: Sequence[Overlap], listed: int = 4) -> str:
    """One line naming the pairs, for refusals, validate and quality."""
    if not overlaps:
        return ""
    shown = "; ".join(item.describe() for item in overlaps[:listed])
    more = f" (and {len(overlaps) - listed} more pair(s))" if len(overlaps) > listed else ""
    return f"blocks lie over one another: {shown}{more}"


def refuse_overlaps(model) -> None:
    """Raise ``TopologyError`` naming the blocks that lie over one another."""
    from .domain import TopologyError

    found = overlapping_blocks(model)
    if found:
        raise TopologyError(
            overlap_text(found)
            + ": the session validates block by block but two cells lie over one place; "
            "remove one block and build that region again"
        )
