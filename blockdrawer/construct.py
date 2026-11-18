"""Construction operators: whole rows and patches of blocks in one command.

The agents that built the backward step and the grooved tandem by hand each
wrote a private geometry kernel first (vertex IDs deduplicated by
coordinate, polar points, ray-square hits, offset circles, arcs, edge
marking). These operators do that geometry so a layout is stated, not
computed:

``add_grid``
    Blocks from x and y station lists, or from radii and angles about a
    centre (circumferential edges become arcs). Cells whose centre lies in
    a ``skip`` box, inside an ``outside`` reference curve or inside an
    existing block are left out; vertices coincident with existing ones are
    reused, and with ``conform`` the existing vertices lying on a grid line
    become stations, so a grid closes conformally round what is there.
``add_band``
    One or more rows of blocks along a chain (or closed loop) of exterior
    edges: spokes along the mitred normals, a fan of corner blocks at a
    convex corner sharper than ``FAN_ANGLE``, open ends following the
    boundary they meet; offset arcs stay arcs. ``to_box`` instead carries
    every spoke to a rectangle (a box corner taken by the nearest spoke).
``add_ring``
    A band round a closed reference curve: vertices on the curve at its
    sharp corners, at given arc-length fractions, where the outward normal
    has given angles, optionally at rounded convex corners (``tips``), and
    by a count spread between them; wall edges as splines through the
    curve's own points, then the band as ``add_band`` builds it.
``set_boundary_by``
    Assign every exterior edge chosen by a selector - within a tolerance of
    reference curves, inside a box, along the exterior chain between two
    vertices (all given selectors must hold) - to a patch, created if
    missing. A curve is closed implicitly, as ``add_ring`` closes it, where
    the closing segment's edges have their blocks outside it.

Every operator works on a copy and replaces the model only when the result
validates, so each is one atomic edit and one history entry. A block that
would not be strictly convex is refused by name. With ``id=PREFIX`` new
vertices are ``PREFIX_0, PREFIX_1, ...`` and new blocks ``PREFIX_b0, ...``
in creation order; without it the next free ``vN`` and ``bN`` are taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Sequence

from .domain import Block, EdgeGeometry, EdgeKey, TopologyError, Vertex, edge_key
from .model import MeshModel
from .session import from_data, to_data

Point = tuple[float, float]

# A convex corner of a band turning more than this gets a fan of corner
# blocks (each turning at most FAN_SECTOR) instead of one mitred spoke.
FAN_ANGLE = math.radians(60.0)
FAN_SECTOR = math.radians(90.0)
# A curve vertex turning more than this is a sharp corner add_ring keeps
# (the default of its corner option).
CORNER_ANGLE = math.radians(30.0)
# A rounded convex corner (add_ring tips=true): the wall turns at least
# TIP_TURN within TIP_WINDOW of the perimeter either side of a vertex.
TIP_TURN = math.radians(45.0)
TIP_WINDOW = 0.01
# A tip this close (fraction of the perimeter) to a corner, a fraction or a
# normal station is that station.
TIP_MERGE = 0.001
# A mitred spoke is at most this many band heights long.
MITRE_LIMIT = 3.0
# Interior points kept per spline edge.
MAX_SPLINE_POINTS = 40


@dataclass
class ConstructResult:
    operation: str
    vertices: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    reused_vertices: int = 0
    skipped_cells: int = 0
    edges: int = 0
    note: str = ""

    def to_data(self) -> dict[str, Any]:
        data = {
            "operation": self.operation,
            "created_vertices": self.vertices,
            "created_blocks": self.blocks,
            "reused_vertices": self.reused_vertices,
        }
        if self.operation == "add_grid":
            data["skipped_cells"] = self.skipped_cells
        if self.operation == "set_boundary_by":
            data["edges"] = self.edges
        if self.note:
            data["note"] = self.note
        return data


# ---------------------------------------------------------------------------
# Building on a copy
# ---------------------------------------------------------------------------


class _Build:
    def __init__(self, model: MeshModel, prefix: str | None) -> None:
        self.model = model
        self.prefix = prefix
        self.created_vertices: list[str] = []
        self.created_blocks: list[str] = []
        self.reused: set[str] = set()
        self.curved: dict[EdgeKey, tuple[str, str, str, tuple[Point, ...]]] = {}
        self.existing_edges = set(model.edge_cells)
        coordinates = [abs(v.x) + abs(v.y) for v in model.vertices.values()] or [1.0]
        self.tol = 1.0e-9 * max(1.0, max(coordinates))
        if prefix is not None and (not prefix or any(c.isspace() for c in prefix)):
            raise TopologyError("id must be a non-empty string without whitespace")

    def scale(self, extent: float) -> None:
        self.tol = max(self.tol, 1.0e-9 * max(1.0, extent))

    def find(self, x: float, y: float) -> str | None:
        for vertex in self.model.vertices.values():
            if abs(vertex.x - x) <= self.tol and abs(vertex.y - y) <= self.tol:
                return vertex.id
        return None

    def vertex(self, point: Point) -> str:
        x, y = point
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("A constructed vertex is not finite")
        found = self.find(x, y)
        if found is not None:
            if found not in self.created_vertices:
                self.reused.add(found)
            return found
        if self.prefix is not None:
            identifier = f"{self.prefix}_{len(self.created_vertices)}"
            if identifier in self.model.vertices:
                raise TopologyError(f"Vertex ID {identifier} is already used")
        else:
            identifier = self.model._next_id("v", self.model.vertices)
        self.model.vertices[identifier] = Vertex(identifier, float(x), float(y))
        self.created_vertices.append(identifier)
        return identifier

    def block(self, identifiers: Sequence[str], expect: float | None = None) -> str:
        """A block from four vertices; with ``expect`` (+1 counter-clockwise,
        -1 clockwise) the four must already make a strictly convex quad in the
        given order - sorting them round their centre would turn a folded or
        crossed band block into a valid-looking block with other edges."""
        if len(set(identifiers)) != 4:
            raise TopologyError(
                f"A constructed block collapses to {len(set(identifiers))} distinct vertices "
                f"({', '.join(identifiers)})"
            )
        if expect is not None:
            corners = [(self.model.vertices[v].x, self.model.vertices[v].y) for v in identifiers]
            if not _ordered_convex(corners, expect):
                listed = ", ".join(f"{v} ({x:.6g}, {y:.6g})" for v, (x, y) in zip(identifiers, corners))
                raise TopologyError(
                    f"A constructed block [{listed}] folds or crosses itself in its construction order"
                )
        ordered = self.model._counter_clockwise_vertices(identifiers)
        for block in self.model.blocks:
            if set(block.vertices) == set(ordered):
                return block.id
        if self.prefix is not None:
            block_id = f"{self.prefix}_b{len(self.created_blocks)}"
            if any(block.id == block_id for block in self.model.blocks):
                raise TopologyError(f"Block ID {block_id} is already used")
        else:
            block_id = self.model._next_id("b", (block.id for block in self.model.blocks))
        block = Block(block_id, ordered)
        try:
            self.model._validate_convex_ccw(block)
        except TopologyError as exc:
            corners = ", ".join(
                f"{v} ({self.model.vertices[v].x:.6g}, {self.model.vertices[v].y:.6g})" for v in ordered
            )
            raise TopologyError(f"Block {block_id} [{corners}] would not be strictly convex: {exc}") from exc
        self.model.blocks.append(block)
        self.created_blocks.append(block_id)
        return block_id

    def curve(self, first: str, second: str, kind: str, points: Sequence[Point]) -> None:
        """Geometry for a new edge, interpolation points in first -> second order."""
        current = edge_key(first, second)
        if current in self.existing_edges or not points:
            return
        ordered = tuple(points) if current[0] == first else tuple(reversed(points))
        self.curved[current] = (current[0], current[1], kind, ordered)

    def finish(self) -> None:
        model = self.model
        for block_id in self.created_blocks:
            block = next(item for item in model.blocks if item.id == block_id)
            edges = [edge_key(*block.directed_edge(k)) for k in range(4)]
            before = {current: model.edge_cells.get(current) for current in edges}
            for current in edges:
                model.edge_cells.setdefault(current, model.DEFAULT_EDGE_CELLS)
            for first, second in ((edges[0], edges[2]), (edges[1], edges[3])):
                cells = before[first] or before[second] or model.edge_cells[first]
                model.set_edge_cells(first, cells)
        for current, (_a, _b, kind, points) in self.curved.items():
            if current in model.edge_cells and current not in model.edge_geometry:
                model.edge_geometry[current] = EdgeGeometry(kind, points)
        model._prune_boundary_assignments()
        model.validate()

    def result(self, operation: str) -> ConstructResult:
        return ConstructResult(
            operation,
            list(self.created_vertices),
            list(self.created_blocks),
            len(self.reused),
        )


def _atomic(model: MeshModel, work) -> ConstructResult:
    working = from_data(to_data(model))
    result = work(working)
    for key, value in working.__dict__.items():
        setattr(model, key, value)
    return result


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _curve_polygon(model: MeshModel, curve_id: str) -> list[Point]:
    curve = model.geometry_curves.get(curve_id)
    if curve is None:
        raise TopologyError(f"Unknown curve {curve_id!r}")
    points = list(curve.points)
    if len(points) > 2 and math.dist(points[0], points[-1]) <= 1.0e-12 * (1.0 + _extent(points)):
        points.pop()
    return points


def _extent(points: Sequence[Point]) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if points else 0.0


def _inside(point: Point, polygon: Sequence[Point]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    for k in range(n):
        (x1, y1), (x2, y2) = polygon[k], polygon[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if cross > x:
                inside = not inside
    return inside


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length = dx * dx + dy * dy
    t = 0.0 if length <= 0.0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _distance_to(point: Point, polyline: Sequence[Point], closed: bool) -> float:
    pairs = list(zip(polyline, polyline[1:]))
    if closed:
        pairs.append((polyline[-1], polyline[0]))
    return min(_segment_distance(point, a, b) for a, b in pairs)


def _block_polygon(model: MeshModel, block: Block) -> list[Point]:
    polygon: list[Point] = []
    for k in range(4):
        first, second = block.directed_edge(k)
        current = edge_key(first, second)
        if current in model.edge_geometry:
            fractions = [index / 8.0 for index in range(8)]
            points = model.edge_points(current, [f if first == current[0] else 1.0 - f for f in fractions])
            polygon.extend(points)
        else:
            vertex = model.vertices[first]
            polygon.append((vertex.x, vertex.y))
    return polygon


def _circumcentre(a: Point, b: Point, c: Point) -> Point | None:
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) <= 1.0e-300:
        return None
    sa, sb, sc = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    return ((sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
            (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d)


def _unit(x: float, y: float) -> Point:
    length = math.hypot(x, y)
    if length <= 0.0:
        raise TopologyError("A band direction is degenerate")
    return x / length, y / length


# ---------------------------------------------------------------------------
# add_grid
# ---------------------------------------------------------------------------


def add_grid(
    model: MeshModel,
    *,
    xs: Sequence[float] = (),
    ys: Sequence[float] = (),
    center: Sequence[float] = (),
    rs: Sequence[float] = (),
    thetas: Sequence[float] = (),
    period: float = 0.0,
    skip: Sequence[float] = (),
    outside: Sequence[str] = (),
    conform: bool = True,
    prefix: str | None = None,
) -> ConstructResult:
    """Blocks from Cartesian stations, or polar ones about ``center``."""
    polar = bool(center)
    if polar:
        if len(center) != 2:
            raise TopologyError("center takes two numbers")
        if xs or ys:
            raise TopologyError("give xs/ys or center/rs/thetas, not both")
        if len(rs) < 2 or len(thetas) < 1:
            raise TopologyError("a polar grid needs at least two radii and one angle")
        if min(rs) <= 0.0:
            raise TopologyError("radii must be positive")
    elif len(xs) < 2 or len(ys) < 2:
        raise TopologyError("a grid needs at least two x and two y stations")
    if len(skip) % 4:
        raise TopologyError("skip takes boxes of four numbers: xmin, ymin, xmax, ymax")
    boxes = [tuple(skip[k:k + 4]) for k in range(0, len(skip), 4)]

    def work(working: MeshModel) -> ConstructResult:
        build = _Build(working, prefix)
        solids = [_curve_polygon(working, curve) for curve in outside]
        occurrences = working.edge_occurrences()
        rim = sorted({v for e, items in occurrences.items() if len(items) == 1 for v in e})
        occupied = [_block_polygon(working, block) for block in working.blocks]
        if polar:
            cx, cy = center
            build.scale(abs(cx) + abs(cy) + max(rs))
            full = period > 0.0 or (len(thetas) > 1 and abs(max(thetas) - min(thetas) - 360.0) < 1.0e-9)
            first_axis = sorted(set(rs))
            second_axis = sorted({t % 360.0 for t in _expand(thetas, period)}) if full else sorted(set(thetas))

            def position(r: float, t: float) -> Point:
                return cx + r * math.cos(math.radians(t)), cy + r * math.sin(math.radians(t))

            def make_cells(radii, angles):
                return _polar_cells(radii, angles, full)

            def describe(cell):
                (r0, t0), (r1, t1) = cell
                centre = position(0.5 * (r0 + r1), 0.5 * (t0 + t1))
                return centre, [position(r0, t0), position(r0, t1), position(r1, t1), position(r1, t0)]
        else:
            first_axis = sorted(set(xs))
            second_axis = sorted(set(ys))
            build.scale(max(abs(first_axis[0]), abs(first_axis[-1])) + max(abs(second_axis[0]), abs(second_axis[-1])))

            def make_cells(px, py):
                return [((px[i], py[j]), (px[i + 1], py[j + 1]))
                        for i in range(len(px) - 1) for j in range(len(py) - 1)]

            def describe(cell):
                (x0, y0), (x1, y1) = cell
                return (0.5 * (x0 + x1), 0.5 * (y0 + y1)), [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

        def kept(cells):
            out, skipped = [], 0
            for cell in cells:
                centre, corners = describe(cell)
                if any(b[0] <= centre[0] <= b[2] and b[1] <= centre[1] <= b[3] for b in boxes) \
                        or any(_inside(centre, solid) for solid in solids) \
                        or any(_inside(centre, polygon) for polygon in occupied):
                    skipped += 1
                    continue
                out.append(cell)
            return out, skipped

        cells, skipped = kept(make_cells(first_axis, second_axis))
        if conform:
            # Existing exterior vertices on a kept cell's side become stations
            # (only there: a vertex inside a skipped region adds nothing).
            for _ in range(16):
                extra_first, extra_second = set(), set()
                for cell in cells:
                    (a0, b0), (a1, b1) = cell
                    for vertex_id in rim:
                        vertex = working.vertices[vertex_id]
                        if polar:
                            r = math.hypot(vertex.x - cx, vertex.y - cy)
                            if r <= build.tol:
                                continue
                            t = math.degrees(math.atan2(vertex.y - cy, vertex.x - cx))
                            t = a0_t = t % 360.0
                            if t < b0 - 1.0e-9:
                                t += 360.0
                            angle_tol = math.degrees(build.tol / r)
                            on_r = any(abs(r - radius) <= build.tol for radius in (a0, a1))
                            on_t = any(abs(t - angle) <= angle_tol for angle in (b0, b1))
                            if on_r and b0 + angle_tol < t < b1 - angle_tol:
                                extra_second.add(a0_t)
                            elif on_t and a0 + build.tol < r < a1 - build.tol:
                                extra_first.add(r)
                        else:
                            on_x = any(abs(vertex.x - x) <= build.tol for x in (a0, a1))
                            on_y = any(abs(vertex.y - y) <= build.tol for y in (b0, b1))
                            if on_x and b0 + build.tol < vertex.y < b1 - build.tol:
                                extra_second.add(vertex.y)
                            elif on_y and a0 + build.tol < vertex.x < a1 - build.tol:
                                extra_first.add(vertex.x)
                if not extra_first and not extra_second:
                    break
                first_axis = _merge(first_axis, extra_first, build.tol)
                second_axis = _merge(second_axis, extra_second, build.tol if not polar else 1.0e-9)
                cells, skipped = kept(make_cells(first_axis, second_axis))
        before_rim = [
            e for e, items in occurrences.items()
            if len(items) == 1 and e not in working.edge_geometry
        ]
        for cell in cells:
            centre, corners = describe(cell)
            for corner in corners:
                for current in before_rim:
                    a, b = working.vertices[current[0]], working.vertices[current[1]]
                    if build.find(*corner) in current:
                        continue
                    if _segment_distance(corner, (a.x, a.y), (b.x, b.y)) <= build.tol \
                            and min(math.dist(corner, (a.x, a.y)), math.dist(corner, (b.x, b.y))) > build.tol:
                        raise TopologyError(
                            f"a grid vertex at ({corner[0]:.6g}, {corner[1]:.6g}) would fall inside "
                            f"the existing edge {current[0]}-{current[1]} and leave it non-conformal: "
                            "a station from elsewhere crosses it; split that edge first or "
                            "build the grid in parts"
                        )
            ids = [build.vertex(point) for point in corners]
            build.block(ids)
            if polar:
                (r0, t0), (r1, t1) = cell
                tm = 0.5 * (t0 + t1)
                for r, (a, b) in ((r0, (ids[0], ids[1])), (r1, (ids[3], ids[2]))):
                    build.curve(a, b, "arc", (position(r, tm),))
        build.finish()
        result = build.result("add_grid")
        result.skipped_cells = skipped
        if not result.blocks:
            raise TopologyError("add_grid created no block: every cell was skipped or exists")
        return result

    return _atomic(model, work)


def _merge(stations, extra, tol) -> list[float]:
    """Stations with the extra ones added, values within ``tol`` of one
    already there dropped."""
    merged = sorted(stations)
    for value in sorted(extra):
        if all(abs(value - kept) > tol for kept in merged):
            merged = sorted(merged + [value])
    return merged


def _expand(thetas: Sequence[float], period: float) -> list[float]:
    if period <= 0.0:
        return list(thetas)
    copies = 360.0 / period
    if abs(copies - round(copies)) > 1.0e-9 or round(copies) < 1:
        raise TopologyError("period must divide 360 degrees")
    return [t + k * period for k in range(int(round(copies))) for t in thetas]


def _polar_cells(radii, angles, full):
    spans = list(zip(angles, angles[1:]))
    if full:
        spans.append((angles[-1], angles[0] + 360.0))
    for t0, t1 in spans:
        if t1 - t0 >= 180.0:
            raise TopologyError("an angular step of 180 degrees or more cannot be one block")
    return [((radii[i], t0), (radii[i + 1], t1)) for i in range(len(radii) - 1) for t0, t1 in spans]


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


@dataclass
class _Chain:
    """An ordered chain of vertices (closed or open) with the interior points
    of every edge in traversal order and the band side: +1 left of the
    traversal, -1 right."""

    vertices: list[str]
    interiors: list[tuple[Point, ...]]
    kinds: list[str]
    closed: bool
    side: float
    ends: tuple[Point | None, Point | None] = (None, None)


def _left(t: Point) -> Point:
    return -t[1], t[0]


def _band(build: _Build, chain: _Chain, height: float, rows: int, growth: float,
          box: Sequence[float] | None) -> None:
    model = build.model
    position = {v: (model.vertices[v].x, model.vertices[v].y) for v in chain.vertices}
    count = len(chain.vertices)
    edge_count = count if chain.closed else count - 1

    def edge_path(k: int) -> list[Point]:
        a = position[chain.vertices[k]]
        b = position[chain.vertices[(k + 1) % count]]
        return [a, *chain.interiors[k], b]

    def arc_tangent(k: int, at_start: bool) -> Point | None:
        if chain.kinds[k] != "arc":
            return None
        a, m, b = edge_path(k)
        centre = _circumcentre(a, m, b)
        if centre is None:
            return None
        p = a if at_start else b
        radial = (p[0] - centre[0], p[1] - centre[1])
        t = _unit(-radial[1], radial[0])
        towards = (m[0] - p[0], m[1] - p[1]) if at_start else (p[0] - m[0], p[1] - m[1])
        if t[0] * towards[0] + t[1] * towards[1] < 0.0:
            t = (-t[0], -t[1])
        return t

    def tangent_out(k: int) -> Point:   # leaving vertex k along edge k
        exact = arc_tangent(k, True)
        if exact is not None:
            return exact
        path = edge_path(k)
        return _unit(path[1][0] - path[0][0], path[1][1] - path[0][1])

    def tangent_in(k: int) -> Point:    # arriving at vertex k+1 along edge k
        exact = arc_tangent(k, False)
        if exact is not None:
            return exact
        path = edge_path(k)
        return _unit(path[-1][0] - path[-2][0], path[-1][1] - path[-2][1])

    def normal(t: Point) -> Point:
        n = _left(t)
        return n[0] * chain.side, n[1] * chain.side

    # spokes per vertex: list of (direction, length factor); fans add mid points
    spokes: list[list[tuple[Point, float]]] = []
    for k in range(count):
        incoming = (k - 1) % count if (chain.closed or k > 0) else None
        outgoing = k if (chain.closed or k < count - 1) else None
        if incoming is not None and outgoing is not None:
            n_in = normal(tangent_in(incoming))
            n_out = normal(tangent_out(outgoing))
            t_out = tangent_out(outgoing)
            turn = math.acos(max(-1.0, min(1.0, n_in[0] * n_out[0] + n_in[1] * n_out[1])))
            convex = n_in[0] * t_out[0] + n_in[1] * t_out[1] < -1.0e-12
            if box is None and convex and turn > FAN_ANGLE:
                sectors = max(1, math.ceil(turn / FAN_SECTOR - 1.0e-9))
                start = math.atan2(n_in[1], n_in[0])
                sweep = math.atan2(n_in[0] * n_out[1] - n_in[1] * n_out[0],
                                   n_in[0] * n_out[0] + n_in[1] * n_out[1])
                fan = []
                for s in range(sectors + 1):
                    angle = start + sweep * s / sectors
                    fan.append(((math.cos(angle), math.sin(angle)), 1.0))
                    if s < sectors:
                        middle = start + sweep * (s + 0.5) / sectors
                        fan.append(((math.cos(middle), math.sin(middle)),
                                    1.0 / math.cos(abs(sweep) / sectors / 2.0)))
                spokes.append(fan)
                continue
            direction = _unit(n_in[0] + n_out[0], n_in[1] + n_out[1])
            cosine = direction[0] * n_out[0] + direction[1] * n_out[1]
            spokes.append([(direction, min(MITRE_LIMIT, 1.0 / max(cosine, 1.0e-9)))])
        else:
            edge = outgoing if outgoing is not None else incoming
            t = tangent_out(edge) if outgoing is not None else tangent_in(edge)
            n = normal(t)
            follow = chain.ends[0] if outgoing is not None else chain.ends[1]
            if follow is not None:
                d = _unit(follow[0], follow[1])
                if d[0] * n[0] + d[1] * n[1] > math.cos(math.radians(60.0)):
                    cosine = d[0] * n[0] + d[1] * n[1]
                    spokes.append([(d, 1.0 / cosine)])
                    continue
            spokes.append([(n, 1.0)])

    if box is not None:
        _band_to_box(build, chain, position, spokes, box, edge_path)
        return
    weights = [growth ** r for r in range(rows)]
    total = sum(weights)
    fractions = [0.0]
    for w in weights:
        fractions.append(fractions[-1] + w / total)

    def point_at(k: int, spoke: int, f: float, h: float) -> Point:
        (dx, dy), factor = spokes[k][spoke]
        base = position[chain.vertices[k]]
        return base[0] + dx * factor * h * f, base[1] + dy * factor * h * f

    def point(k: int, spoke: int, f: float) -> Point:
        return point_at(k, spoke, f, height)

    def quads(h: float):
        """Every band block's corners in construction order, with the
        orientation that order must have (``chain.side``: wall a, wall b,
        then outwards; a fan's first-row kite runs outwards first)."""
        for r in range(1, rows + 1):
            f0, f1 = fractions[r - 1], fractions[r]
            for e in range(edge_count):
                a, b = e, (e + 1) % count
                sa = len(spokes[a]) - 1
                yield (("edge", e), [point_at(a, sa, f0, h), point_at(b, 0, f0, h),
                                     point_at(b, 0, f1, h), point_at(a, sa, f1, h)], chain.side)
            for k in range(count):
                fan = spokes[k]
                for s in range(0, len(fan) - 1, 2):
                    if r == 1:
                        yield (("fan", k), [position[chain.vertices[k]], point_at(k, s, f1, h),
                                            point_at(k, s + 1, f1, h), point_at(k, s + 2, f1, h)],
                               -chain.side)
                    else:
                        for first, second in ((s, s + 1), (s + 1, s + 2)):
                            yield (("fan", k), [point_at(k, first, f0, h), point_at(k, second, f0, h),
                                                point_at(k, second, f1, h), point_at(k, first, f1, h)],
                                   chain.side)

    def first_fold(h: float):
        for label, corners, sign in quads(h):
            if not _ordered_convex(corners, sign):
                return label
        return None

    folded = first_fold(height)
    if folded is not None:
        raise TopologyError(_fold_message(chain, position, spokes, edge_count, count, height,
                                          folded, first_fold))

    level_ids: dict[tuple[int, int, int], str] = {}

    def vid(k: int, spoke: int, r: int) -> str:
        key = (k, spoke, r)
        if key not in level_ids:
            level_ids[key] = chain.vertices[k] if r == 0 else build.vertex(point(k, spoke, fractions[r]))
        return level_ids[key]

    for r in range(1, rows + 1):
        # along the edges
        for e in range(edge_count):
            a, b = e, (e + 1) % count
            sa, sb = len(spokes[a]) - 1, 0
            ids = [vid(a, sa, r - 1), vid(b, sb, r - 1), vid(b, sb, r), vid(a, sa, r)]
            build.block(ids, expect=chain.side)
            interior = chain.interiors[e]
            if interior:
                outer = _offset_interior(edge_path(e), chain, height * fractions[r],
                                         spokes[a][sa], spokes[b][sb])
                build.curve(ids[3], ids[2], chain.kinds[e], outer)
        # fans
        for k in range(count):
            fan = spokes[k]
            for s in range(0, len(fan) - 1, 2):
                if r == 1:
                    ids = [chain.vertices[k], vid(k, s, 1), vid(k, s + 1, 1), vid(k, s + 2, 1)]
                    build.block(ids, expect=-chain.side)
                else:
                    for first, second in ((s, s + 1), (s + 1, s + 2)):
                        build.block([vid(k, first, r - 1), vid(k, second, r - 1),
                                     vid(k, second, r), vid(k, first, r)], expect=chain.side)


def _ordered_convex(corners: Sequence[Point], sign: float) -> bool:
    """Whether four corners, in the given order, make a strictly convex quad
    turning the way ``sign`` says (+1 counter-clockwise); the sine of every
    corner is compared, as the model's own convexity check does."""
    for index in range(4):
        p, q, r = corners[index], corners[(index + 1) % 4], corners[(index + 2) % 4]
        ux, uy = q[0] - p[0], q[1] - p[1]
        vx, vy = r[0] - q[0], r[1] - q[1]
        lengths = math.hypot(ux, uy) * math.hypot(vx, vy)
        if lengths <= 0.0 or sign * (ux * vy - uy * vx) <= MeshModel.COORDINATE_TOLERANCE * lengths:
            return False
    return True


def _round_down(value: float) -> str:
    """``value`` to three significant digits, never rounded up."""
    if value <= 0.0:
        return "0"
    digits = 2 - math.floor(math.log10(value))
    factor = 10.0 ** digits
    return f"{math.floor(value * factor * (1.0 - 1.0e-12)) / factor:.{max(digits, 0)}f}"


def _fold_message(chain: _Chain, position, spokes, edge_count: int, count: int, height: float,
                  folded, first_fold) -> str:
    """Why the band block ``folded`` cannot be built at ``height``, and the
    largest height at which every band block is strictly convex."""
    low, high = 0.0, height
    if first_fold(height * 1.0e-6) is not None:
        largest = None
    else:
        low = height * 1.0e-6
        for _ in range(60):
            middle = 0.5 * (low + high)
            if first_fold(middle) is None:
                low = middle
            else:
                high = middle
        largest = low
    advice = (
        f"the largest height that builds is {_round_down(largest)}" if largest
        else ("no height builds it: the spokes at its ends already cross at the wall, as when "
              "an edge path turns back at its end (look at its interpolation points)")
    )
    kind, index = folded
    if kind == "fan":
        vertex = chain.vertices[index]
        return f"the fan at {vertex} would fold at height {height:g}; {advice}"
    a, b = index, (index + 1) % count
    first, second = chain.vertices[a], chain.vertices[b]
    pa, pb = position[first], position[second]
    length = math.dist(pa, pb)
    t = ((pb[0] - pa[0]) / length, (pb[1] - pa[1]) / length)

    def slide(k: int, spoke: int, towards: float) -> float:
        (dx, dy), factor = spokes[k][spoke]
        return towards * (dx * t[0] + dy * t[1]) * factor * height

    slides = (slide(a, len(spokes[a]) - 1, 1.0), slide(b, 0, -1.0))
    movers = []
    for k, along in ((a, slides[0]), (b, slides[1])):
        if along <= 0.0:
            continue
        name = chain.vertices[k]
        neighbours = []
        if chain.closed or k > 0:
            neighbours.append(chain.vertices[(k - 1) % count])
        if chain.closed or k < count - 1:
            neighbours.append(chain.vertices[(k + 1) % count])
        if len(neighbours) == 2 and len(spokes[k]) == 1:
            here = position[name]
            before, after = position[neighbours[0]], position[neighbours[1]]
            t_in = _unit(here[0] - before[0], here[1] - before[1])
            t_out = _unit(after[0] - here[0], after[1] - here[1])
            turn = math.degrees(math.acos(max(-1.0, min(1.0, t_in[0] * t_out[0] + t_in[1] * t_out[1]))))
            what = (f"the mitred spoke at the concave corner {name} (turning {turn:.0f} degrees "
                    f"between edges {neighbours[0]}-{name} and {name}-{neighbours[1]})")
        else:
            what = f"the spoke at {name}"
        movers.append(f"{what} moves the offset end {along:.4g} along it")
    if sum(slides) >= length and movers:
        cause = (
            f"its offset reverses: {' and '.join(movers)}, more than its length {length:.4g} "
            "(a mitred spoke at a concave corner turning T degrees moves the offset point "
            "height * tan(T/2) along each edge beside it)"
        )
        advice += ", or merge the short edge into its neighbour"
    else:
        cause = "its offset corners would not make a strictly convex block"
    return f"the band block on edge {first}-{second} would fold at height {height:g}: {cause}; {advice}"


def _offset_interior(path: list[Point], chain: _Chain, distance: float,
                     spoke_a, spoke_b) -> tuple[Point, ...]:
    """Interior points of an edge carried along their local normals; an arc
    stays an arc (its one point moves along the radius).

    The offset's ends are where the end spokes put them, not on the local
    normals: a mitre at a concave corner moves an end forward along the
    edge, and a wall curving tighter than the offset folds the offset back
    on itself, so points near an end can land behind it and the offset edge
    would hook back.  Only the points running monotonically along the
    offset chord, clear of both ends, are kept (`_monotone_points`)."""
    out = []
    n = len(path)
    for index in range(1, n - 1):
        before, after = path[index - 1], path[index + 1]
        t = _unit(after[0] - before[0], after[1] - before[1])
        normal = _left(t)
        normal = normal[0] * chain.side, normal[1] * chain.side
        out.append((path[index][0] + normal[0] * distance, path[index][1] + normal[1] * distance))
    (da, fa), (db, fb) = spoke_a, spoke_b
    start = (path[0][0] + da[0] * fa * distance, path[0][1] + da[1] * fa * distance)
    end = (path[-1][0] + db[0] * fb * distance, path[-1][1] + db[1] * fb * distance)
    return _monotone_points(start, out, end)


def _monotone_points(start: Point, points: Sequence[Point], end: Point) -> tuple[Point, ...]:
    """The longest run of ``points`` (kept in order) whose projections on the
    chord ``start`` -> ``end`` strictly increase and stay a thousandth of
    the chord clear of both ends - the interior points of an edge path
    that never runs backwards along its chord."""
    ux, uy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(ux, uy)
    if length <= 0.0 or not points:
        return tuple(points)
    gap = 1.0e-3 * length
    along = [((p[0] - start[0]) * ux + (p[1] - start[1]) * uy) / length for p in points]
    candidates = [k for k, value in enumerate(along) if gap < value < length - gap]
    # Longest strictly increasing subsequence (first such on ties): a hook
    # at either end, or a swallowtail in the middle, is what it leaves out.
    best: dict[int, tuple[int, int | None]] = {}
    for k in candidates:
        length_here, previous = 1, None
        for j in candidates:
            if j >= k:
                break
            if along[j] < along[k] - gap * 1.0e-3 and best[j][0] + 1 > length_here:
                length_here, previous = best[j][0] + 1, j
        best[k] = (length_here, previous)
    if not best:
        return ()
    tail = max(best, key=lambda k: (best[k][0], -k))
    kept = []
    while tail is not None:
        kept.append(points[tail])
        tail = best[tail][1]
    return tuple(reversed(kept))


def _band_to_box(build, chain, position, spokes, box, edge_path):
    if len(box) != 4:
        raise TopologyError("to_box takes xmin, ymin, xmax, ymax")
    x0, y0, x1, y1 = box
    count = len(chain.vertices)
    edge_count = count if chain.closed else count - 1
    hits = []
    sides = []
    for k in range(count):
        (dx, dy), _factor = spokes[k][0]
        px, py = position[chain.vertices[k]]
        if not (x0 < px < x1 and y0 < py < y1):
            raise TopologyError(f"vertex {chain.vertices[k]} is not inside the box")
        candidates = []
        if dx > 0:
            candidates.append(((x1 - px) / dx, 1))
        if dx < 0:
            candidates.append(((x0 - px) / dx, 3))
        if dy > 0:
            candidates.append(((y1 - py) / dy, 2))
        if dy < 0:
            candidates.append(((y0 - py) / dy, 0))
        t, side = min(candidates)
        hits.append([px + t * dx, py + t * dy])
        sides.append(side)
    corners = {(0, 1): (x1, y0), (1, 2): (x1, y1), (2, 3): (x0, y1), (3, 0): (x0, y0)}
    snapped = set()
    for e in range(edge_count):
        a, b = e, (e + 1) % count
        if sides[a] == sides[b] or a in snapped and b in snapped:
            continue
        pair = (sides[a], sides[b]) if (sides[a], sides[b]) in corners else (sides[b], sides[a])
        if pair not in corners:
            raise TopologyError(
                f"the spokes of {chain.vertices[a]} and {chain.vertices[b]} reach opposite sides "
                "of the box: add stations so a spoke runs to each box corner"
            )
        corner = corners[pair]
        if any(math.dist(hits[k], corner) <= 1.0e-12 * (1.0 + abs(corner[0]) + abs(corner[1])) for k in (a, b)):
            continue
        target = a if math.dist(hits[a], corner) <= math.dist(hits[b], corner) else b
        if target in snapped:
            target = b if target == a else a
        if target in snapped:
            raise TopologyError(f"no spoke left for the box corner {corner}")
        hits[target] = list(corner)
        snapped.add(target)
    for e in range(edge_count):
        a, b = e, (e + 1) % count
        quad = [position[chain.vertices[a]], position[chain.vertices[b]], tuple(hits[b]), tuple(hits[a])]
        if not _ordered_convex(quad, chain.side):
            def where(k: int) -> str:
                tag = " (a box corner)" if k in snapped else ""
                return f"{chain.vertices[k]}'s spoke runs to ({hits[k][0]:.6g}, {hits[k][1]:.6g}){tag}"
            raise TopologyError(
                f"the block from edge {chain.vertices[a]}-{chain.vertices[b]} to the box would fold: "
                f"{where(a)} and {where(b)}; put a station whose outward normal points at the box "
                "corner (add_ring normals=...), or move the box"
            )
    outer = [build.vertex((h[0], h[1])) for h in hits]
    for e in range(edge_count):
        a, b = e, (e + 1) % count
        build.block([chain.vertices[a], chain.vertices[b], outer[b], outer[a]], expect=chain.side)


def _model_chain(model: MeshModel, edges: Sequence[EdgeKey]) -> _Chain:
    occurrences = model.edge_occurrences()
    exterior = [current for current in edges if len(occurrences.get(current, ())) == 1]
    if len(exterior) != len(edges):
        inner = [current for current in edges if current not in exterior]
        raise TopologyError(f"a band grows from exterior edges only; {'-'.join(inner[0])} is not one")
    adjacency: dict[str, list[EdgeKey]] = {}
    for current in edges:
        for vertex in current:
            adjacency.setdefault(vertex, []).append(current)
    if any(len(items) > 2 for items in adjacency.values()):
        raise TopologyError("the selected edges branch: a band needs one chain or one loop")
    ends = sorted(v for v, items in adjacency.items() if len(items) == 1)
    start = ends[0] if ends else min(adjacency)
    order = [start]
    used: list[EdgeKey] = []
    current_vertex = start
    while True:
        options = [e for e in adjacency[current_vertex] if e not in used]
        if not options:
            break
        nxt = sorted(options)[0]
        used.append(nxt)
        current_vertex = nxt[1] if nxt[0] == current_vertex else nxt[0]
        if current_vertex == start:
            break
        order.append(current_vertex)
    if len(used) != len(edges):
        raise TopologyError("the selected edges are not one connected chain or loop")
    closed = not ends
    interiors, kinds = [], []
    for k, current in enumerate(used):
        first = order[k]
        kind = model.edge_type(current)
        kinds.append("line" if kind == "line" else kind)
        if kind == "line":
            interiors.append(())
        else:
            points = model.edge_control_points(current)
            interiors.append(tuple(points) if first == current[0] else tuple(reversed(points)))
    # band side: away from the owning blocks
    occurrence = occurrences[used[0]][0]
    block = occurrence[0]
    cx = sum(model.vertices[v].x for v in block.vertices) / 4.0
    cy = sum(model.vertices[v].y for v in block.vertices) / 4.0
    a, b = model.vertices[order[0]], model.vertices[order[1] if len(order) > 1 else order[0]]
    mid = model.edge_point(used[0], 0.5)
    tx, ty = b.x - a.x, b.y - a.y
    left = (-ty, tx)
    side = -1.0 if left[0] * (cx - mid[0]) + left[1] * (cy - mid[1]) > 0.0 else 1.0
    for k, current in enumerate(used):
        block = occurrences[current][0][0]
        bx = sum(model.vertices[v].x for v in block.vertices) / 4.0
        by = sum(model.vertices[v].y for v in block.vertices) / 4.0
        p, q = model.vertices[order[k]], model.vertices[order[(k + 1) % len(order)]]
        lx, ly = -(q.y - p.y), q.x - p.x
        m = model.edge_point(current, 0.5)
        if (lx * (bx - m[0]) + ly * (by - m[1])) * side > 0.0:
            raise TopologyError("the selected chain has blocks on both sides: select one side's edges")
    ends_follow: list[Point | None] = [None, None]
    if not closed:
        all_exterior = [e for e, items in occurrences.items() if len(items) == 1 and e not in used]
        for slot, vertex in ((0, order[0]), (1, order[-1])):
            nexts = [e for e in all_exterior if vertex in e]
            if len(nexts) == 1:
                other = nexts[0][1] if nexts[0][0] == vertex else nexts[0][0]
                v, o = model.vertices[vertex], model.vertices[other]
                ends_follow[slot] = (o.x - v.x, o.y - v.y)
    return _Chain(order, interiors, kinds, closed, side, (ends_follow[0], ends_follow[1]))


def _select_exterior(model: MeshModel, bbox: Sequence[float], unassigned: bool) -> list[EdgeKey]:
    occurrences = model.edge_occurrences()
    x0, y0, x1, y1 = bbox
    chosen = []
    for current, items in sorted(occurrences.items()):
        if len(items) != 1:
            continue
        if unassigned and current in model.edge_boundaries:
            continue
        points = model.edge_points(current, [0.0, 0.25, 0.5, 0.75, 1.0])
        if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in points):
            chosen.append(current)
    return chosen


def add_band(
    model: MeshModel,
    *,
    edges: Sequence[EdgeKey] = (),
    bbox: Sequence[float] = (),
    height: float = 0.0,
    rows: int = 1,
    growth: float = 1.0,
    to_box: Sequence[float] = (),
    prefix: str | None = None,
) -> ConstructResult:
    """One or more rows of blocks along a chain of exterior edges."""
    if bool(to_box) == (height > 0.0):
        raise TopologyError("give a positive height or to_box, not both")
    if rows < 1 or (to_box and rows != 1):
        raise TopologyError("rows must be at least 1 (1 with to_box)")
    if growth <= 0.0:
        raise TopologyError("growth must be positive")

    def work(working: MeshModel) -> ConstructResult:
        if edges:
            chosen = list(edges)
        elif bbox:
            if len(bbox) != 4:
                raise TopologyError("bbox takes xmin, ymin, xmax, ymax")
            chosen = _select_exterior(working, bbox, unassigned=True)
        else:
            raise TopologyError("select the chain by edges or by bbox (its unassigned exterior edges)")
        if not chosen:
            raise TopologyError("no exterior edge selected")
        chain = _model_chain(working, chosen)
        build = _Build(working, prefix)
        _band(build, chain, height, rows, growth, tuple(to_box) or None)
        build.finish()
        return build.result("add_band")

    return _atomic(model, work)


# ---------------------------------------------------------------------------
# add_ring
# ---------------------------------------------------------------------------


def add_ring(
    model: MeshModel,
    curve: str,
    height: float,
    *,
    count: int = 0,
    fractions: Sequence[float] = (),
    normals: Sequence[float] = (),
    corner: float = math.degrees(CORNER_ANGLE),
    tips: bool = False,
    rows: int = 1,
    growth: float = 1.0,
    prefix: str | None = None,
) -> ConstructResult:
    """A band round a closed reference curve, its wall edges on the curve.

    Stations: every curve vertex turning more than ``corner`` degrees, the
    arc-length ``fractions``, the point where the outward normal has each of
    the ``normals`` angles (refused unless there is exactly one), with
    ``tips`` the peak of every rounded convex corner (`_tip_stations`), and
    ``count`` edges spread by arc length between them."""
    if height <= 0.0:
        raise TopologyError("height must be positive")
    if count < 0 or (not count and not fractions and not normals):
        raise TopologyError("give count (edges round the curve), fractions or normals")
    if not 0.0 < corner < 180.0:
        raise TopologyError("corner is an angle in degrees between 0 and 180")
    corner_angle = math.radians(corner)

    def work(working: MeshModel) -> ConstructResult:
        loop = _curve_polygon(working, curve)
        if len(loop) < 3:
            raise TopologyError("add_ring needs a closed curve of at least three points")
        n = len(loop)
        lengths = [0.0]
        for k in range(n):
            lengths.append(lengths[-1] + math.dist(loop[k], loop[(k + 1) % n]))
        total = lengths[-1]
        area = sum(loop[k][0] * loop[(k + 1) % n][1] - loop[(k + 1) % n][0] * loop[k][1] for k in range(n))
        orientation = 1.0 if area > 0.0 else -1.0
        turning = _turning(loop)
        corners = [lengths[k] for k in range(n) if abs(turning[k]) > corner_angle]
        stations = set(corners)
        explicit = set(corners)
        notes = []
        if corners:
            notes.append(f"{len(corners)} corner(s) above {corner:g} degrees")
        if fractions:
            explicit.update((f % 1.0) * total for f in fractions)
        for angle in normals:
            explicit.add(_normal_station(loop, lengths, turning, orientation, corner_angle, angle))
        if normals:
            notes.append(f"{len(normals)} station(s) by normal")
        stations.update(explicit)
        if tips:
            found = [
                tip for tip in _tip_stations(loop, lengths, turning, orientation, corner_angle)
                if all(_cyclic_gap(tip, other, total) > TIP_MERGE * total for other in explicit)
            ]
            stations.update(found)
            notes.append(f"{len(found)} tip(s)" + (
                " at fraction " + ", ".join(f"{round(t / total, 6) % 1.0:.4g}" for t in sorted(found))
                if found else ""
            ))
        if count:
            anchors = sorted(stations) or [0.0]
            pieces = [(anchors[k], anchors[(k + 1) % len(anchors)] + (total if k + 1 == len(anchors) else 0.0))
                      for k in range(len(anchors))]
            for start, end in pieces:
                share = max(1, round(count * (end - start) / total))
                for s in range(1, share):
                    stations.add((start + (end - start) * s / share) % total)
            stations.add(anchors[0] % total)
        ordered = sorted({round(s, 12) for s in stations})
        if len(ordered) < 3:
            raise TopologyError("a ring needs at least three stations")

        def at(s: float) -> Point:
            s %= total
            k = max(0, min(n - 1, _bisect(lengths, s) - 1))
            span = lengths[k + 1] - lengths[k]
            f = 0.0 if span <= 0.0 else (s - lengths[k]) / span
            a, b = loop[k], loop[(k + 1) % n]
            return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])

        build = _Build(working, prefix)
        build.scale(_extent(loop) + max(abs(c) for p in loop for c in p))
        ids = [build.vertex(at(s)) for s in ordered]
        interiors = []
        for k, start in enumerate(ordered):
            end = ordered[(k + 1) % len(ordered)] + (total if k + 1 == len(ordered) else 0.0)
            inside = [loop[j % n] for j in range(n * 2)
                      if start + 1.0e-12 < (lengths[j % n] + (total if j >= n else 0.0)) < end - 1.0e-12]
            a_point, b_point = at(start), at(end)
            gap = 1.0e-3 * max(math.dist(a_point, b_point), build.tol)
            inside = [p for p in inside if math.dist(p, a_point) > gap and math.dist(p, b_point) > gap]
            if len(inside) > MAX_SPLINE_POINTS:
                step = len(inside) / MAX_SPLINE_POINTS
                inside = [inside[int(i * step)] for i in range(MAX_SPLINE_POINTS)]
            if not inside:
                inside = [at(0.5 * (start + end))]
            interiors.append(tuple(inside))
        area = sum(loop[k][0] * loop[(k + 1) % n][1] - loop[(k + 1) % n][0] * loop[k][1] for k in range(n))
        side = -1.0 if area > 0.0 else 1.0   # outward: right of an anticlockwise loop
        for k in range(len(ids)):
            path = [at(ordered[k]), *interiors[k], at(ordered[(k + 1) % len(ordered)])]
            straight = all(_segment_distance(p, path[0], path[-1]) <= build.tol * 10 for p in path[1:-1])
            if not straight:
                build.curve(ids[k], ids[(k + 1) % len(ids)], "spline", interiors[k])
        kinds = ["spline"] * len(ids)
        for k in range(len(ids)):
            path = [at(ordered[k]), *interiors[k], at(ordered[(k + 1) % len(ordered)])]
            if all(_segment_distance(p, path[0], path[-1]) <= build.tol * 10 for p in path[1:-1]):
                interiors[k] = ()
                kinds[k] = "line"
        chain = _Chain(ids, interiors, kinds, True, side)
        _band(build, chain, height, rows, growth, None)
        build.finish()
        result = build.result("add_ring")
        result.note = f"{len(ordered)} stations: " + ("; ".join(notes) or "by arc length")
        return result

    return _atomic(model, work)


def _turning(loop: Sequence[Point]) -> list[float]:
    """Signed turning (radians, left positive) at every vertex of a closed loop."""
    n = len(loop)
    out = []
    for k in range(n):
        a, p, b = loop[k - 1], loop[k], loop[(k + 1) % n]
        t1 = _unit(p[0] - a[0], p[1] - a[1])
        t2 = _unit(b[0] - p[0], b[1] - p[1])
        out.append(math.atan2(t1[0] * t2[1] - t1[1] * t2[0], t1[0] * t2[0] + t1[1] * t2[1]))
    return out


def _cyclic_gap(a: float, b: float, total: float) -> float:
    gap = abs(a - b) % total
    return min(gap, total - gap)


def _tip_stations(loop, lengths, turning, orientation, corner_angle) -> list[float]:
    """Arc lengths of the rounded convex corners of a closed loop.

    A vertex's turning is spread over the half segments either side of it
    (a turning density); a stretch where the wall turns convexly by at
    least ``TIP_TURN`` within ``TIP_WINDOW`` of the perimeter either side of
    a vertex is a rounded corner - an elliptic nose, an airfoil's leading
    edge - and its station is the turning midpoint of the window about the
    vertex where that turning peaks.  Vertices sharper than the corner
    threshold are stations of their own and carry no turning here; a
    circle of any size turns 7.2 degrees in such a window and has none."""
    n = len(loop)
    total = lengths[-1]
    window = TIP_WINDOW * total
    convex = [0.0 if abs(t) > corner_angle else orientation * t for t in turning]
    segment = [lengths[k + 1] - lengths[k] for k in range(n)]
    # Vertex k's turning spreads uniformly from the middle of the segment
    # before it to the middle of the one after it.
    lo = [lengths[k] - 0.5 * segment[k - 1] for k in range(n)]

    # The cumulative turning is piecewise linear over the knots lo[0..n-1],
    # lo[0] + total, and grows by the loop's whole convex turning per lap.
    knots = lo + [lo[0] + total]
    cumulative = [0.0]
    for k in range(n):
        cumulative.append(cumulative[-1] + convex[k])
    lap = cumulative[-1]

    def integral(x: float) -> float:
        laps = math.floor((x - lo[0]) / total)
        x -= laps * total
        k = max(0, min(n - 1, _bisect(knots, x) - 1))
        width = knots[k + 1] - knots[k]
        f = 0.0 if width <= 0.0 else (x - knots[k]) / width
        return laps * lap + cumulative[k] + convex[k] * min(1.0, max(0.0, f))

    def turned(start: float, stop: float) -> float:
        """Convex turning spread over the arc [start, stop]."""
        return integral(stop) - integral(start)

    if not any(c > 0.0 for c in convex):
        return []
    sums = [turned(lengths[k] - window, lengths[k] + window) for k in range(n)]
    tight = [s >= TIP_TURN for s in sums]
    if all(tight) or not any(tight):
        return []
    # Runs of tight vertices, cyclically; one tip per run at its peak.
    start = next(k for k in range(n) if not tight[k])
    runs, current = [], []
    for step in range(1, n + 1):
        k = (start + step) % n
        if tight[k]:
            current.append(k)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    stations = []
    for run in runs:
        peak = max(sums[k] for k in run)
        plateau = [k for k in run if sums[k] >= peak * (1.0 - 1.0e-9)]
        centre = plateau[len(plateau) // 2]
        a, b = lengths[centre] - window, lengths[centre] + window
        half = 0.5 * turned(a, b)
        low, high = a, b
        for _ in range(60):
            middle = 0.5 * (low + high)
            if turned(a, middle) < half:
                low = middle
            else:
                high = middle
        stations.append((0.5 * (low + high)) % total)
    return stations


def _normal_station(loop, lengths, turning, orientation, corner_angle, angle: float) -> float:
    """The arc length where the loop's outward normal points at ``angle``
    degrees (counter-clockwise from +x); refused unless there is exactly
    one such point.  A smooth vertex's normal bisects its two segments' and
    varies linearly between vertices; at a corner sharper than the corner
    threshold every normal between its two segments' is at the corner."""
    n = len(loop)
    total = lengths[-1]

    def outward(k: int) -> float:   # segment k -> k+1
        a, b = loop[k], loop[(k + 1) % n]
        tx, ty = b[0] - a[0], b[1] - a[1]
        return math.atan2(-orientation * tx, orientation * ty)

    samples: list[tuple[float, float]] = []
    for k in range(n):
        before, after = outward(k - 1), outward(k)
        if abs(turning[k]) > corner_angle:
            samples.append((lengths[k], before))
            samples.append((lengths[k], before + _wrap(after - before)))
        else:
            samples.append((lengths[k], before + 0.5 * _wrap(after - before)))
    target = math.radians(angle)
    hits: list[float] = []
    flat = False
    for index in range(len(samples)):
        s0, a0 = samples[index]
        s1, a1 = samples[(index + 1) % len(samples)]
        if index + 1 == len(samples):
            s1 += total
        sweep = _wrap(a1 - a0)
        offset = _wrap(target - a0)
        if sweep > 0.0 and 0.0 <= offset < sweep or sweep < 0.0 and sweep < offset <= 0.0:
            hits.append((s0 + (s1 - s0) * offset / sweep) % total)
        elif sweep == 0.0 and abs(offset) <= 1.0e-12:
            hits.extend([s0 % total, s1 % total])
            flat = True
    distinct: list[float] = []
    for hit in sorted(hits):
        if all(_cyclic_gap(hit, other, total) > 1.0e-9 * total for other in distinct):
            distinct.append(hit)
    if len(distinct) != 1:
        where = ", ".join(f"{h / total:.4g}" for h in distinct[:6])
        raise TopologyError(
            f"the outward normal points at {angle:g} degrees "
            + ("along a straight stretch" if flat else
               f"at {len(distinct)} places (fractions {where}{'...' if len(distinct) > 6 else ''})"
               if distinct else "nowhere")
            + " on the curve: a normal station needs exactly one; give fractions there"
        )
    return distinct[0]


def _wrap(angle: float) -> float:
    """``angle`` in (-pi, pi]."""
    wrapped = math.fmod(angle + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def _bisect(values: Sequence[float], x: float) -> int:
    low, high = 0, len(values)
    while low < high:
        middle = (low + high) // 2
        if values[middle] <= x:
            low = middle + 1
        else:
            high = middle
    return low


# ---------------------------------------------------------------------------
# set_boundary_by
# ---------------------------------------------------------------------------


def set_boundary_by(
    model: MeshModel,
    name: str,
    *,
    kind: str | None = None,
    curves: Sequence[str] = (),
    tolerance: float = 0.0,
    bbox: Sequence[float] = (),
    between: Sequence[str] = (),
    unassigned: bool = False,
) -> ConstructResult:
    """Assign the exterior edges every given selector chooses to ``name``."""
    if not (curves or bbox or between):
        raise TopologyError("give at least one selector: curves, bbox or between")
    if bbox and len(bbox) != 4:
        raise TopologyError("bbox takes xmin, ymin, xmax, ymax")
    if between and len(between) != 2:
        raise TopologyError("between takes two vertices")

    def work(working: MeshModel) -> ConstructResult:
        occurrences = working.edge_occurrences()
        exterior = sorted(e for e, items in occurrences.items() if len(items) == 1)
        chosen = set(exterior)
        if unassigned:
            chosen = {e for e in chosen if e not in working.edge_boundaries}
        samples = {e: working.edge_points(e, [0.0, 0.25, 0.5, 0.75, 1.0]) for e in chosen}
        if bbox:
            x0, y0, x1, y1 = bbox
            chosen = {e for e in chosen if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in samples[e])}
        if curves:
            on_curves: set[EdgeKey] = set()
            for curve in curves:
                points = _curve_polygon(working, curve)
                limit = tolerance or 1.0e-3 * _extent(points)
                for e in chosen:
                    if all(_distance_to(p, points, False) <= limit for p in samples[e]):
                        on_curves.add(e)
                    elif len(points) >= 3 \
                            and all(_distance_to(p, points, True) <= limit for p in samples[e]) \
                            and not _inside(_block_centre(working, occurrences[e][0][0]), points):
                        # Along the closing segment from the last point to
                        # the first, with its block outside the closed
                        # curve: the curve bounds a body and is closed
                        # implicitly, as add_ring closes it (a blunt base
                        # whose corner points are not repeated). An open
                        # wall whose chord bounds the domain itself (a
                        # cavity lid, a U-bend's inlet) has its blocks
                        # inside and is left alone.
                        on_curves.add(e)
            chosen &= on_curves
        if between:
            chosen &= set(_exterior_path(working, exterior, between[0], between[1]))
        if not chosen:
            raise TopologyError("no exterior edge matches every selector")
        if name not in working.boundaries:
            working.add_boundary(name)
        if kind:
            working.set_boundary_type(name, kind)
        for current in sorted(chosen):
            working.set_edge_boundary(current, name)
        result = ConstructResult("set_boundary_by")
        result.edges = len(chosen)
        return result

    return _atomic(model, work)


def _block_centre(model: MeshModel, block: Block) -> Point:
    return (sum(model.vertices[v].x for v in block.vertices) / 4.0,
            sum(model.vertices[v].y for v in block.vertices) / 4.0)


def _exterior_path(model, exterior, start, end) -> list[EdgeKey]:
    import heapq

    adjacency: dict[str, list[tuple[str, EdgeKey]]] = {}
    for current in exterior:
        a, b = current
        adjacency.setdefault(a, []).append((b, current))
        adjacency.setdefault(b, []).append((a, current))
    if start not in adjacency or end not in adjacency:
        raise TopologyError("between needs two vertices on the exterior")
    best = {start: 0.0}
    back: dict[str, tuple[str, EdgeKey]] = {}
    queue = [(0.0, start)]
    while queue:
        distance, vertex = heapq.heappop(queue)
        if vertex == end:
            break
        if distance > best.get(vertex, math.inf):
            continue
        for other, current in sorted(adjacency[vertex]):
            value = distance + model.edge_length(current)
            if value < best.get(other, math.inf) - 1.0e-15:
                best[other] = value
                back[other] = (vertex, current)
                heapq.heappush(queue, (value, other))
    if end not in back and start != end:
        raise TopologyError("no exterior chain joins the two vertices")
    path = []
    vertex = end
    while vertex != start:
        vertex, current = back[vertex]
        path.append(current)
    return path
