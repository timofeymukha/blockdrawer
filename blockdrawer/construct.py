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
``add_stations``
    Standalone vertices on a reference curve: a count spread over it or a
    section of it, arc-length fractions, or where it crosses given x or y;
    an ``offset`` copy gives a translated periodic partner matching
    stations.
``add_fill``
    Blocks filling a four-sided region between existing vertices - band
    fronts, grid sides, standalone stations on a curve - its interior
    vertices by blockMesh's transfinite interpolation of the four sides; a
    side not given is the exterior chain joining its corners, or open
    (straight or along a curve) through the standalone vertices on it or
    new stations facing the opposite side's.
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
from .preview import _block_mesh_point
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
    stations: list[str] = field(default_factory=list)

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
        if self.operation == "add_stations":
            data["stations"] = self.stations
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

    def vertex(self, point: Point, name: str | None = None) -> str:
        """The vertex at ``point``: an existing one within the tolerance, or a
        new one named ``name``, ``PREFIX_k`` (k in creation order) or the
        next free ``vN``."""
        x, y = point
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("A constructed vertex is not finite")
        found = self.find(x, y)
        if found is not None:
            if found not in self.created_vertices:
                self.reused.add(found)
            return found
        if name is not None:
            identifier = name
            if identifier in self.model.vertices:
                raise TopologyError(f"Vertex ID {identifier} is already used")
        elif self.prefix is not None:
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


# ---------------------------------------------------------------------------
# Reference curves as paths (add_stations, add_fill)
# ---------------------------------------------------------------------------


class _CurvePath:
    """A reference curve as the polyline through its points - the path
    ``add_ring`` puts its stations on - parametrised by arc length from its
    first point.

    It is closed when its first point is repeated at its end, or when the
    chord from its last point back to its first is no longer than its
    longest segment (a body's loop with the closing point left implied); an
    open chain is never closed across its ends.  ``tolerance`` is
    ``set_boundary_by``'s default, a thousandth of the curve's size: a
    vertex that close is on the curve."""

    def __init__(self, model: MeshModel, curve_id: str) -> None:
        curve = model.geometry_curves.get(curve_id)
        if curve is None:
            raise TopologyError(f"Unknown curve {curve_id!r}")
        points = [(float(p[0]), float(p[1])) for p in curve.points]
        extent = _extent(points)
        repeated = len(points) > 2 and math.dist(points[0], points[-1]) <= 1.0e-12 * (1.0 + extent)
        if repeated:
            points.pop()
        segments = [math.dist(a, b) for a, b in zip(points, points[1:])]
        self.name = curve.name or curve_id
        self.points = points
        self.closed = repeated or (
            len(points) >= 3 and math.dist(points[-1], points[0]) <= max(segments) * (1.0 + 1.0e-6))
        self.lengths = [0.0]
        for length in segments:
            self.lengths.append(self.lengths[-1] + length)
        if self.closed:
            self.lengths.append(self.lengths[-1] + math.dist(points[-1], points[0]))
        self.total = self.lengths[-1]
        if self.total <= 0.0:
            raise TopologyError(f"curve {self.name} has no length")
        self.extent = extent
        self.tolerance = 1.0e-3 * extent

    def wrap(self, s: float) -> float:
        return s % self.total if self.closed else min(max(s, 0.0), self.total)

    def at(self, s: float) -> Point:
        s = self.wrap(s)
        n = len(self.points)
        k = max(0, min(len(self.lengths) - 2, _bisect(self.lengths, s) - 1))
        span = self.lengths[k + 1] - self.lengths[k]
        f = 0.0 if span <= 0.0 else (s - self.lengths[k]) / span
        a, b = self.points[k], self.points[(k + 1) % n]
        return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])

    def locate(self, point: Point) -> tuple[float, float]:
        """The arc length of the nearest point of the path, and the distance."""
        best_distance, best_s = math.inf, 0.0
        n = len(self.points)
        for k in range(len(self.lengths) - 1):
            a, b = self.points[k], self.points[(k + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            squared = dx * dx + dy * dy
            t = 0.0 if squared <= 0.0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
            distance = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
            if distance < best_distance:
                best_distance, best_s = distance, self.lengths[k] + t * (self.lengths[k + 1] - self.lengths[k])
        return best_s, best_distance

    def span(self, start: float, end: float) -> float:
        """Signed arc length from ``start`` to ``end``: direct on an open
        curve, the shorter way round a closed one."""
        if not self.closed:
            return end - start
        forward = (end - start) % self.total
        return forward if forward <= self.total - forward else forward - self.total

    def inside(self, start: float, length: float) -> list[Point]:
        """The curve's own points strictly inside the section from ``start``
        over the signed ``length``, in its direction."""
        return [point for _u, point in self.inside_with_parameters(start, length)]

    def inside_with_parameters(self, start: float, length: float) -> list[tuple[float, Point]]:
        """As ``inside``, each point with its fraction of the section."""
        if length == 0.0:
            return []
        found = []
        for j, point in enumerate(self.points):
            values = [self.lengths[j]]
            if self.closed:
                values += [self.lengths[j] + self.total, self.lengths[j] - self.total]
            for value in values:
                u = (value - start) / length
                if 1.0e-12 < u < 1.0 - 1.0e-12:
                    found.append((u, point))
                    break
        found.sort()
        return found

    def edge_interior(self, start: float, length: float, tol: float) -> tuple[Point, ...]:
        """Interior points of a spline edge along the section, as add_ring's
        wall edges take them (none within a thousandth of the chord of an
        end, at most ``MAX_SPLINE_POINTS``); empty when the section is
        straight."""
        a, b = self.at(start), self.at(start + length)
        gap = 1.0e-3 * max(math.dist(a, b), tol)
        inside = [p for p in self.inside(start, length) if math.dist(p, a) > gap and math.dist(p, b) > gap]
        if len(inside) > MAX_SPLINE_POINTS:
            inside = [inside[k] for k in _symmetric_picks(len(inside), MAX_SPLINE_POINTS)]
        if all(_segment_distance(p, a, b) <= tol * 10 for p in inside):
            return ()
        return tuple(inside)


def _symmetric_picks(available: int, wanted: int) -> list[int]:
    """``wanted`` indices spread evenly over ``available``, the same set
    read from either end: an edge and its reversed partner (the two chains
    of a periodic pair, cut in opposite directions) keep the same points.
    An odd count over an even number has no middle index and drops one."""
    if wanted % 2 and not available % 2:
        wanted -= 1
    half = [int(k * (available - 1) / (wanted - 1) + 0.5) for k in range((wanted + 1) // 2)]
    return sorted(set(half) | {available - 1 - k for k in half[: wanted // 2]})


def _polyline_lengths(points: Sequence[Point]) -> list[float]:
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    return lengths


def _polyline_at(points: Sequence[Point], lengths: Sequence[float], s: float) -> Point:
    k = max(0, min(len(points) - 2, _bisect(lengths, s) - 1))
    span = lengths[k + 1] - lengths[k]
    f = 0.0 if span <= 0.0 else (s - lengths[k]) / span
    a, b = points[k], points[k + 1]
    return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])


def _polyline_locate(points: Sequence[Point], lengths: Sequence[float], point: Point) -> tuple[float, float]:
    best_distance, best_s = math.inf, 0.0
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        squared = dx * dx + dy * dy
        t = 0.0 if squared <= 0.0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
        distance = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
        if distance < best_distance:
            best_distance, best_s = distance, lengths[k] + t * (lengths[k + 1] - lengths[k])
    return best_s, best_distance


# ---------------------------------------------------------------------------
# add_stations
# ---------------------------------------------------------------------------


def add_stations(
    model: MeshModel,
    curve: str,
    *,
    count: int = 0,
    between: Sequence[float] = (),
    fractions: Sequence[float] = (),
    xs: Sequence[float] = (),
    ys: Sequence[float] = (),
    offset: Sequence[float] = (),
    prefix: str | None = None,
) -> ConstructResult:
    """Standalone vertices on a reference curve, ordered along it.

    The section is the whole curve, or with ``between`` the part from its
    first to its second end - two arc-length fractions, or two points taken
    to their nearest curve points - running forward round a closed curve.
    Stations: ``count`` of them spread by arc length over the section (both
    ends included; round a closed curve without ``between`` they start at
    its first point), the arc-length ``fractions`` of the whole curve, and
    where the section crosses each of ``xs`` and ``ys`` (refused unless it
    crosses exactly once).  ``offset`` translates every station after it is
    placed, so the translated partner of a periodic chain gets stations
    matching this chain's node for node.  With ``id=NAME`` station k along
    the section is ``NAME_k``; a station on an existing vertex reuses it."""
    if count < 0 or count == 1:
        raise TopologyError("count is the number of stations, at least 2 (0: none)")
    if not (count or fractions or xs or ys):
        raise TopologyError("give count, fractions, xs or ys")
    if between and len(between) not in (2, 4):
        raise TopologyError("between takes two fractions or two points (x0,y0,x1,y1)")
    if offset and len(offset) != 2:
        raise TopologyError("offset takes dx,dy")

    def work(working: MeshModel) -> ConstructResult:
        path = _CurvePath(working, curve)
        build = _Build(working, prefix)
        build.scale(path.extent + max(abs(c) for p in path.points for c in p))
        total = path.total
        if between:
            if len(between) == 2:
                for f in between:
                    if not path.closed and not -1.0e-12 <= f <= 1.0 + 1.0e-12:
                        raise TopologyError(f"fraction {f:g} is not on the open curve {path.name} (0 to 1)")
                s0, s1 = between[0] * total, between[1] * total
            else:
                s0 = path.locate((between[0], between[1]))[0]
                s1 = path.locate((between[2], between[3]))[0]
            length = ((s1 - s0) % total if path.closed else s1 - s0)
            if abs(length) <= 1.0e-12 * total:
                raise TopologyError("between names one point of the curve")
        else:
            s0, length = 0.0, total
        whole_loop = path.closed and not between

        def section_u(s: float, what: str) -> float:
            u = (((s - s0) % total) if path.closed else (s - s0)) / length
            if whole_loop:
                return u % 1.0
            if not -1.0e-9 <= u <= 1.0 + 1.0e-9:
                raise TopologyError(f"{what} is outside the section given by between")
            return min(1.0, max(0.0, u))

        found: list[float] = []
        if count:
            found += [k / count if whole_loop else k / (count - 1) for k in range(count)]
        for f in fractions:
            if not path.closed and not -1.0e-12 <= f <= 1.0 + 1.0e-12:
                raise TopologyError(f"fraction {f:g} is not on the open curve {path.name} (0 to 1)")
            found.append(section_u(path.wrap(f * total) if path.closed else f * total, f"fraction {f:g}"))
        samples = [0.0, *[u for u, _p in path.inside_with_parameters(s0, length)], 1.0]
        for axis, values in ((0, xs), (1, ys)):
            for value in values:
                hits = _crossings(path, s0, length, samples, axis, value)
                if whole_loop:
                    hits = sorted({round(u % 1.0, 12) for u in hits})
                if len(hits) != 1:
                    name = "x" if axis == 0 else "y"
                    where = ", ".join(f"{(path.wrap(s0 + u * length)) / total:.4g}" for u in hits[:6])
                    raise TopologyError(
                        f"the section of {path.name} crosses {name} = {value:g} "
                        + (f"{len(hits)} times (fractions {where})" if hits else "nowhere")
                        + ": a station needs exactly one crossing; narrow it with between="
                    )
                found.append(hits[0])
        ordered: list[float] = []
        for u in sorted(found):
            if not ordered or abs(u - ordered[-1]) * abs(length) > build.tol:
                ordered.append(u)
        dx, dy = (offset[0], offset[1]) if offset else (0.0, 0.0)
        ids = []
        for k, u in enumerate(ordered):
            x, y = path.at(s0 + u * length)
            name = f"{prefix}_{k}" if prefix is not None else None
            ids.append(build.vertex((x + dx, y + dy), name=name))
        working.validate()
        result = build.result("add_stations")
        result.stations = ids
        span = (f" from fraction {path.wrap(s0) / total:.6g} to {path.wrap(s0 + length) / total:.6g}"
                if between else "")
        result.note = f"{len(ids)} stations on {path.name}{span}" + (
            f", offset by ({dx:g}, {dy:g})" if offset else "")
        return result

    return _atomic(model, work)


def _crossings(path: _CurvePath, s0: float, length: float, samples: Sequence[float],
               axis: int, value: float) -> list[float]:
    """Section parameters where the section's polyline crosses the line
    ``x = value`` (axis 0) or ``y = value`` (axis 1)."""
    points = [path.at(s0 + u * length)[axis] - value for u in samples]
    hits: list[float] = []
    for k in range(len(samples) - 1):
        a, b = points[k], points[k + 1]
        if a == 0.0:
            hit = samples[k]
        elif a * b < 0.0 or (b == 0.0 and k + 1 == len(samples) - 1):
            hit = samples[k] + (samples[k + 1] - samples[k]) * a / (a - b)
        else:
            continue
        if not hits or abs(hit - hits[-1]) > 1.0e-12:
            hits.append(hit)
    return hits


# ---------------------------------------------------------------------------
# add_fill
# ---------------------------------------------------------------------------


@dataclass
class _FillSide:
    """One side of a fill, from its first corner to its second: stations
    (``None`` for one still to be created), their points, and per segment
    an existing edge, a section of the side's curve, or a straight line."""

    kind: str
    vertices: list[str | None]
    points: list[Point]
    segments: list[tuple]
    lengths: list[float]
    route: list[Point] = field(default_factory=list)
    path: Any = None

    def fractions(self) -> list[float]:
        total = sum(self.lengths)
        out = [0.0]
        for length in self.lengths:
            out.append(out[-1] + length / total if total > 0.0 else 0.0)
        out[-1] = 1.0
        return out


def add_fill(
    model: MeshModel,
    corners: Sequence[str],
    *,
    sides: Sequence[Sequence[str]] = ((), (), (), ()),
    curves: Sequence[str | None] = (None, None, None, None),
    prefix: str | None = None,
) -> ConstructResult:
    """Blocks filling a four-sided region whose sides run between existing
    vertices, interior vertices by transfinite interpolation.

    ``corners`` go round the region (either way); side k runs from corner k
    to corner k+1.  A side is, in this order: the vertex list given for it;
    else the chain of exterior edges joining its corners, its blocks
    outside the region (the band front, the grid side that is there); else
    open - straight, or along its reference curve - taking the standalone
    vertices lying on it as stations, or, with none, new stations at the
    opposite side's arc-length fractions.  Opposite sides need as many
    segments; edges between stations both on a side's curve follow it as
    splines through its points."""
    if len(corners) != 4 or len(set(corners)) != 4:
        raise TopologyError("corners takes four distinct vertices, in order round the region")
    if len(sides) != 4 or len(curves) != 4:
        raise TopologyError("a fill has four sides")

    def work(working: MeshModel) -> ConstructResult:
        build = _Build(working, prefix)
        position = {v.id: (v.x, v.y) for v in working.vertices.values()}
        build.scale(_extent([position[c] for c in corners]) + max(abs(c) for v in corners for c in position[v]))
        occurrences = working.edge_occurrences()
        paths = [_CurvePath(working, c) if c else None for c in curves]
        candidates: list[list[list[str] | None]] = []
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            if sides[k]:
                given = list(sides[k])
                if given[0] == b and given[-1] == a:
                    given.reverse()
                if given[0] != a or given[-1] != b or len(given) < 2:
                    raise TopologyError(f"side{k + 1} must run from corner {a} to corner {b}")
                if len(set(given)) != len(given):
                    raise TopologyError(f"side{k + 1} lists a vertex twice")
                for p, q in zip(given, given[1:]):
                    key = edge_key(p, q)
                    if len(occurrences.get(key, ())) > 1:
                        raise TopologyError(f"edge {key[0]}-{key[1]} of side{k + 1} already has blocks on both sides")
                candidates.append([given])
            else:
                forbidden = set(corners) - {a, b}
                ways = (1, -1) if paths[k] is not None and paths[k].closed else (0,)
                candidates.append([*_exterior_walks(occurrences, a, b, forbidden), *ways])
        chosen, sign = _choose_sides(working, occurrences, corners, candidates, paths, position)
        listed = {v for side in sides for v in side}
        resolved = [
            _resolve_side(working, chosen[k], corners[k], corners[(k + 1) % 4], paths[k], position,
                          "given" if sides[k] else "chain", listed)
            for k in range(4)
        ]
        for first, second in ((0, 2), (1, 3)):
            _match_counts(resolved, first, second, corners)
        n = len(resolved[0].points) - 1
        m = len(resolved[1].points) - 1
        fractions = [side.fractions() for side in resolved]

        def boundary(i: int, j: int) -> tuple[str | None, Point, tuple[int, int] | None]:
            if j == 0:
                return resolved[0].vertices[i], resolved[0].points[i], (0, i)
            if i == n:
                return resolved[1].vertices[j], resolved[1].points[j], (1, j)
            if j == m:
                return resolved[2].vertices[n - i], resolved[2].points[n - i], (2, n - i)
            return resolved[3].vertices[m - j], resolved[3].points[m - j], (3, m - j)

        c00, c10, c11, c01 = (position[c] for c in corners)
        grid: dict[tuple[int, int], Point] = {}
        for j in range(m + 1):
            for i in range(n + 1):
                if i in (0, n) or j in (0, m):
                    grid[i, j] = boundary(i, j)[1]
                    continue
                grid[i, j] = _block_mesh_point(
                    (fractions[0][i], resolved[0].points[i]),
                    (fractions[1][j], resolved[1].points[j]),
                    (1.0 - fractions[2][n - i], resolved[2].points[n - i]),
                    (1.0 - fractions[3][m - j], resolved[3].points[m - j]),
                    (c00, c10, c11, c01),
                )
        quads = [((i, j), [grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]])
                 for j in range(m) for i in range(n)]
        names = {}
        for i in range(n + 1):
            for j in range(m + 1):
                if i in (0, n) or j in (0, m):
                    names[i, j] = boundary(i, j)[0]
        for index, ((i, j), quad) in enumerate(quads):
            if not _ordered_convex(quad, sign):
                raise TopologyError(_fill_fold_message(working, build, index, i, j, n, m, quad, sign, names))
        _refuse_overlap(working, quads, names, position)
        # Vertices: new side stations, side by side, then the interior row by row.
        for k, side in enumerate(resolved):
            for s, vertex in enumerate(side.vertices):
                if vertex is None:
                    side.vertices[s] = build.vertex(side.points[s])
        ids: dict[tuple[int, int], str] = {}
        for j in range(m + 1):
            for i in range(n + 1):
                if i in (0, n) or j in (0, m):
                    ids[i, j] = boundary(i, j)[0]  # type: ignore[assignment]
        for j in range(1, m):
            for i in range(1, n):
                ids[i, j] = build.vertex(grid[i, j])
        for (i, j), _quad in quads:
            build.block([ids[i, j], ids[i + 1, j], ids[i + 1, j + 1], ids[i, j + 1]], expect=sign)
        for side in resolved:
            for s, segment in enumerate(side.segments):
                if segment[0] == "curve" and segment[1]:
                    build.curve(side.vertices[s], side.vertices[s + 1], "spline", segment[1])
        build.finish()
        result = build.result("add_fill")
        described = []
        for k, side in enumerate(resolved):
            a, b = corners[k], corners[(k + 1) % 4]
            text = {
                "given": "as given",
                "chain": "the exterior chain",
                "stations": "open, through standalone stations",
                "matched": "open, new stations facing the opposite side's",
                "open": "open",
            }[side.kind]
            curve_text = f" along {paths[k].name}" if paths[k] is not None else ""
            described.append(f"side{k + 1} {a}..{b}: {len(side.points) - 1} edge(s), {text}{curve_text}")
        result.note = f"{n} x {m} blocks; " + "; ".join(described)
        return result

    return _atomic(model, work)


def _exterior_walks(occurrences, a: str, b: str, forbidden: set[str]) -> list[list[str]]:
    """Chains of exterior edges from ``a`` to ``b``: from each exterior edge
    at ``a``, on through vertices with exactly two exterior edges, never
    through another corner."""
    adjacency: dict[str, list[EdgeKey]] = {}
    for current, items in occurrences.items():
        if len(items) == 1:
            for vertex in current:
                adjacency.setdefault(vertex, []).append(current)
    walks = []
    for first in sorted(adjacency.get(a, ())):
        path, edge, current = [a], first, a
        while True:
            following = edge[1] if edge[0] == current else edge[0]
            if following == b:
                walks.append(path + [b])
                break
            if following == a or following in forbidden or following in path:
                break
            options = [e for e in adjacency[following] if e != edge]
            if len(options) != 1:
                break
            path.append(following)
            current, edge = following, options[0]
    return walks


def _open_path(position, a: str, b: str, path: _CurvePath | None, way: int) -> list[Point]:
    """An open side's path: straight, or the curve's section between the
    points nearest its corners - round a closed curve forwards (``way`` 1)
    or backwards (-1)."""
    pa, pb = position[a], position[b]
    if path is None:
        return [pa, pb]
    start, end = path.locate(pa)[0], path.locate(pb)[0]
    if path.closed:
        forward = (end - start) % path.total
        length = forward if way > 0 else forward - path.total
    else:
        length = end - start
    return [pa, *path.inside(start, length), pb]


def _side_samples(model: MeshModel, vertices: Sequence[str], position) -> list[Point]:
    points: list[Point] = []
    for p, q in zip(vertices, vertices[1:]):
        key = edge_key(p, q)
        points.append(position[p])
        if key in model.edge_geometry:
            fractions = [k / 8.0 for k in range(1, 8)]
            samples = model.edge_points(key, fractions if key[0] == p else [1.0 - f for f in fractions])
            points.extend(samples)
    points.append(position[vertices[-1]])
    return points


def _choose_sides(model, occurrences, corners, candidates, paths, position):
    """The choice of one candidate per side that bounds a region with the
    blocks of every existing side edge outside it and no existing vertex
    inside - the most sides on existing edges first, then the region the
    corners go round counter-clockwise; refused when that leaves two."""
    import itertools

    valid = []
    for combination in itertools.product(*candidates):
        loop: list[Point] = []
        for k, choice in enumerate(combination):
            a, b = corners[k], corners[(k + 1) % 4]
            if isinstance(choice, int):
                side = _open_path(position, a, b, paths[k], choice)
            else:
                side = _side_samples(model, choice, position)
            loop.extend(side[:-1])
        area = sum(loop[k][0] * loop[(k + 1) % len(loop)][1] - loop[(k + 1) % len(loop)][0] * loop[k][1]
                   for k in range(len(loop)))
        if abs(area) <= 1.0e-12 * _extent(loop) ** 2:
            continue
        sign = 1.0 if area > 0.0 else -1.0
        ok = True
        for choice in combination:
            if isinstance(choice, int):
                continue
            score = 0
            for p, q in zip(choice, choice[1:]):
                key = edge_key(p, q)
                items = occurrences.get(key)
                if not items:
                    continue
                block = items[0][0]
                cx, cy = _block_centre(model, block)
                mx, my = model.edge_point(key, 0.5)
                tx, ty = position[q][0] - position[p][0], position[q][1] - position[p][1]
                score += 1 if (tx * (cy - my) - ty * (cx - mx)) * sign < 0.0 else -1
            if score < 0 or (score == 0 and any(occurrences.get(edge_key(p, q)) for p, q in zip(choice, choice[1:]))):
                ok = False
                break
        if ok and not _vertex_inside(model, loop, corners, combination):
            valid.append((sum(not isinstance(choice, int) for choice in combination), combination, sign))
    if not valid:
        raise TopologyError(
            "the sides between corners " + ", ".join(corners) + " bound no region with the existing "
            "blocks outside it: give the corners in order round the region, or the sides explicitly "
            "(side1=...)"
        )
    best = max(score for score, _c, _s in valid)
    top = [(combination, sign) for score, combination, sign in valid if score == best]
    if len(top) > 1 and sum(sign > 0 for _c, sign in top) == 1:
        # Corners that go round either of two regions (a half annulus
        # between two loops): the one they go round counter-clockwise.
        top = [item for item in top if item[1] > 0]
    if len(top) > 1:
        differing = [k + 1 for k in range(4) if len({str(c[k]) for c, _s in top}) > 1]
        raise TopologyError(
            "the region is ambiguous: side" + ", side".join(map(str, differing))
            + " can run more than one way between its corners; give it explicitly (sideK=A,...,B)"
        )
    return top[0]


def _vertex_inside(model: MeshModel, loop: Sequence[Point], corners, combination) -> bool:
    """Whether an existing vertex lies inside the region bounded by
    ``loop``, clear of its boundary: a fill region holds none."""
    on_sides = set(corners)
    for choice in combination:
        if not isinstance(choice, int):
            on_sides.update(choice)
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    clearance = 1.0e-6 * math.hypot(x1 - x0, y1 - y0)
    for vertex in model.vertices.values():
        if vertex.id in on_sides or not (x0 < vertex.x < x1 and y0 < vertex.y < y1):
            continue
        point = (vertex.x, vertex.y)
        if _inside(point, loop) and _distance_to(point, loop, True) > clearance:
            return True
    return False


def _resolve_side(model, choice, a, b, path, position, kind, listed) -> _FillSide:
    tol = path.tolerance if path is not None else 0.0
    if not isinstance(choice, int):
        points = [position[v] for v in choice]
        segments, lengths = [], []
        for p, q in zip(choice, choice[1:]):
            key = edge_key(p, q)
            if key in model.edge_cells:
                segments.append(("edge", key))
                lengths.append(model.edge_length(key))
            else:
                segments.append(_new_segment(position[p], position[q], path))
                lengths.append(segments[-1][2])
        return _FillSide(kind, list(choice), points, segments, lengths)
    route = _open_path(position, a, b, path, choice)
    route_lengths = _polyline_lengths(route)
    total = route_lengths[-1]
    tol = max(tol, 1.0e-3 * total)
    used = {v for block in model.blocks for v in block.vertices}
    found = []
    for vertex in model.vertices.values():
        if vertex.id in used or vertex.id in listed or vertex.id in (a, b):
            continue
        s, distance = _polyline_locate(route, route_lengths, (vertex.x, vertex.y))
        if distance <= tol and tol < s < total - tol:
            found.append((s, vertex.id))
    found.sort()
    stations = [a, *(v for _s, v in found), b]
    points = [position[v] for v in stations]
    segments = [_new_segment(p, q, path) for p, q in zip(points, points[1:])]
    return _FillSide("stations" if found else "open", list(stations), points, segments,
                     [segment[2] for segment in segments], route, path)


def _new_segment(p: Point, q: Point, path: _CurvePath | None) -> tuple:
    """A new side edge from ``p`` to ``q``: along the curve (interior points
    for a spline, empty when straight) when both ends are on it, else a
    line; with its length."""
    if path is not None:
        sp, dp = path.locate(p)
        sq, dq = path.locate(q)
        if dp <= path.tolerance and dq <= path.tolerance:
            length = path.span(sp, sq)
            interior = path.edge_interior(sp, length, path.tolerance * 1.0e-3)
            return ("curve", interior, math.dist(p, q) if not interior else
                    _polyline_lengths([p, *interior, q])[-1])
    return ("line", (), math.dist(p, q))


def _match_counts(resolved: list[_FillSide], first: int, second: int, corners) -> None:
    one, other = resolved[first], resolved[second]
    open_kinds = ("open",)
    if one.kind in open_kinds and other.kind in open_kinds:
        return
    if one.kind in open_kinds or other.kind in open_kinds:
        pending, source = (one, other) if one.kind in open_kinds else (other, one)
        targets = [1.0 - f for f in reversed(source.fractions())]
        if len(targets) <= 2:
            return
        route, path = pending.route, pending.path
        lengths = _polyline_lengths(route)
        points = [_polyline_at(route, lengths, f * lengths[-1]) for f in targets]
        points[0], points[-1] = route[0], route[-1]
        pending.kind = "matched"
        pending.vertices = [pending.vertices[0], *([None] * (len(points) - 2)), pending.vertices[-1]]
        pending.points = points
        pending.segments = [_new_segment(p, q, path) for p, q in zip(points, points[1:])]
        pending.lengths = [segment[2] for segment in pending.segments]
        return
    if len(one.points) == len(other.points):
        return
    raise TopologyError(_mismatch_message(resolved, first, second, corners))


def _mismatch_message(resolved, first, second, corners) -> str:
    sides = {first: resolved[first], second: resolved[second]}
    count = {k: len(side.points) - 1 for k, side in sides.items()}
    long_k, short_k = (first, second) if count[first] > count[second] else (second, first)
    long_side, short_side = sides[long_k], sides[short_k]
    # Fractions of the long side's stations read along the short side.
    targets = [1.0 - f for f in reversed(long_side.fractions())]
    short_f = short_side.fractions()
    interior = sorted(targets[1:-1], key=lambda f: -min(abs(f - g) for g in short_f))
    extra = sorted(interior[:count[long_k] - count[short_k]])
    hints = []
    for f in extra:
        s = max(0, min(len(short_f) - 2, _bisect(short_f, f) - 1))
        local = (f - short_f[s]) / (short_f[s + 1] - short_f[s]) if short_f[s + 1] > short_f[s] else 0.5
        p, q = short_side.vertices[s], short_side.vertices[s + 1]
        if short_side.segments[s][0] == "edge":
            hints.append(f"split_edge {p}-{q} {local:.3g}")
        else:
            hints.append(f"a station at {local:.3g} of the way from {p} to {q}")
    def name(k: int) -> str:
        return f"side{k + 1} ({corners[k]}..{corners[(k + 1) % 4]}, {count[k]} edge(s))"
    return (
        f"opposite sides need as many edges: {name(first)} against {name(second)}; "
        f"side{short_k + 1} lacks stations facing side{long_k + 1}'s near " + "; ".join(hints)
        + f" (or remove {count[long_k] - count[short_k]} station(s) from side{long_k + 1})"
    )


def _fill_fold_message(model, build, index, i, j, n, m, quad, sign, names) -> str:
    if build.prefix is not None:
        block_id = f"{build.prefix}_b{index}"
    else:
        used = {block.id for block in model.blocks}
        block_id = None
        for _ in range(index + 1):
            block_id = model._next_id("b", used)
            used.add(block_id)
    labels = []
    for (di, dj), point in zip(((0, 0), (1, 0), (1, 1), (0, 1)), quad):
        vertex = names.get((i + di, j + dj))
        labels.append(f"{vertex or 'new'} ({point[0]:.6g}, {point[1]:.6g})")
    bad = 0
    for corner in range(4):
        p, q, r = quad[corner - 1], quad[corner], quad[(corner + 1) % 4]
        cross = (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])
        if cross * sign <= MeshModel.COORDINATE_TOLERANCE * math.dist(p, q) * math.dist(q, r):
            bad = corner
            break
    return (
        f"fill block {block_id} (column {i + 1} of {n}, row {j + 1} of {m}) [{', '.join(labels)}] "
        f"would not be strictly convex at its corner {labels[bad]}: the stations of opposite sides "
        "do not face each other there, or a side bends more than the transfinite map follows; "
        "move or add stations, or fill the region in parts"
    )


def _refuse_overlap(model: MeshModel, quads, names, position) -> None:
    """Refuse a fill block lying over an existing block: an existing vertex
    strictly inside it, or its centre inside an existing block."""
    existing = []
    for block in model.blocks:
        polygon = _block_polygon(model, block)
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        existing.append((block, polygon, (min(xs), min(ys), max(xs), max(ys))))
    every_vertex = [(v.id, (v.x, v.y)) for v in model.vertices.values()]
    for (i, j), quad in quads:
        corners_here = {names.get((i + di, j + dj)) for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1))}
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        box = (min(xs), min(ys), max(xs), max(ys))
        centre = (sum(xs) / 4.0, sum(ys) / 4.0)
        for block, polygon, (x0, y0, x1, y1) in existing:
            if x0 <= centre[0] <= x1 and y0 <= centre[1] <= y1 and _inside(centre, polygon):
                raise TopologyError(
                    f"the fill's block at column {i + 1}, row {j + 1} would lie over the existing block "
                    f"{block.id}: check the corners and sides"
                )
        for vertex_id, point in every_vertex:
            if vertex_id in corners_here:
                continue
            if box[0] < point[0] < box[2] and box[1] < point[1] < box[3] and _strictly_inside(point, quad):
                raise TopologyError(
                    f"the existing vertex {vertex_id} lies inside the fill's block at column {i + 1}, "
                    f"row {j + 1}: a side passes the wrong side of it, or it should be a station"
                )


def _strictly_inside(point: Point, quad: Sequence[Point]) -> bool:
    """Inside a convex quad, clear of its sides by a relative tolerance."""
    signs = []
    for k in range(4):
        a, b = quad[k], quad[(k + 1) % 4]
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        signs.append(cross / max(math.dist(a, b) ** 2, 1.0e-300))
    return all(s > 1.0e-9 for s in signs) or all(s < -1.0e-9 for s in signs)



def resolve_points(model: MeshModel, points: Sequence[Point]) -> list[str]:
    """The existing vertex at each point, within the construction operators'
    coordinate tolerance; refused naming the nearest vertex otherwise."""
    build = _Build(model, None)
    out = []
    for x, y in points:
        found = build.find(x, y)
        if found is None:
            nearest = min(model.vertices.values(), key=lambda v: math.hypot(v.x - x, v.y - y), default=None)
            near = (f"; the nearest is {nearest.id} at ({nearest.x:.12g}, {nearest.y:.12g}), "
                    f"{math.hypot(nearest.x - x, nearest.y - y):.3g} away" if nearest is not None else "")
            raise TopologyError(f"no vertex at ({x:.12g}, {y:.12g}){near}: give its ID, or add_vertex first")
        out.append(found)
    return out
