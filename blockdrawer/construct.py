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
    ``fan=false`` (or ``open_at`` points) leaves a convex corner open: the
    band ends on each side at the spoke normal to that side.
``add_rows``
    Rows of blocks carrying a chain of exterior edges along a direction to
    a line ``x = X`` or ``y = Y``, or by a distance, at given stations or
    rows with growth: a wake band from a trailing edge left open, a duct
    extended to its outlet.
``add_ring``
    A band round a closed reference curve, or along an open one on a named
    side with no host block: vertices on the curve at its sharp corners, at
    given arc-length fractions, where the outward normal has given angles,
    optionally at rounded convex corners (``tips``), where an open curve
    crosses given x or y, and by a count spread between them; wall edges as
    splines through the curve's own points (none whose segment would run
    backwards), then the band as ``add_band`` builds it, an open curve's
    end spokes along given directions.
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
    new stations facing the opposite side's.  Three or five sides become
    that many four-sided fills round a centre vertex.
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
from .model import MeshModel, unhooked_points
from .preview import _block_mesh_point
from .sampling import FOLD_CELLS
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
# An add_fill refusal lists at most this many rejected readings of its sides.
MAX_FILL_REASONS = 4
# A new vertex closer than this fraction of the model's extent (the diagonal
# of all its vertices' bounding box) to another vertex, but outside the
# reuse tolerance, is named in the operator's note: a station meant to be
# that vertex and missed by a rounded fraction or a side read as open. On the
# 19 round-3 benchmark builds and the skill's four examples the closest pair
# an operator made was 3.6e-5 of the extent (high-lift ring and fill
# stations); the defects reproduced were 3.7e-7 (a six-digit fraction,
# U-bend) and 8.2e-6 (a fill side read as open beside a wall vertex,
# high-lift). The fraction is about their geometric mean, 1.7e-5, rounded down.
NEAR_FRACTION = 1.5e-5
# Notes list at most this many such vertices.
NEAR_LISTED = 4


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
    # New vertices beside another vertex but not reused (`_Build.near_note`).
    near: str = ""

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
        note = "; ".join(part for part in (self.note, self.near) if part)
        if note:
            data["note"] = note
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
        # Chain vertices whose corner a band left open, and its front (`_band`).
        self.opened: list[str] = []
        self.front: list[str] = []
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
        result = ConstructResult(
            operation,
            list(self.created_vertices),
            list(self.created_blocks),
            len(self.reused),
        )
        result.near = self.near_note()
        return result

    def near_pairs(self) -> tuple[float, list[tuple[str, str, float]]]:
        """New vertices closer to another vertex than ``NEAR_FRACTION`` of the
        model's extent but outside the reuse tolerance, each with its
        nearest such vertex: the extent and (new, other, distance), closest
        first."""
        vertices = self.model.vertices
        new = [v for v in self.created_vertices if v in vertices]
        if not new:
            return 0.0, []
        xs = [v.x for v in vertices.values()]
        ys = [v.y for v in vertices.values()]
        extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        limit = NEAR_FRACTION * extent
        if limit <= self.tol:
            return extent, []
        grid: dict[tuple[int, int], list[str]] = {}
        for vertex in vertices.values():
            grid.setdefault((math.floor(vertex.x / limit), math.floor(vertex.y / limit)), []).append(vertex.id)
        found: dict[frozenset, tuple[str, str, float]] = {}
        for identifier in new:
            p = vertices[identifier]
            i, j = math.floor(p.x / limit), math.floor(p.y / limit)
            best = None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for other in grid.get((i + di, j + dj), ()):
                        if other == identifier:
                            continue
                        d = math.hypot(vertices[other].x - p.x, vertices[other].y - p.y)
                        if self.tol < d < limit and (best is None or d < best[2]):
                            best = (identifier, other, d)
            if best is not None:
                found.setdefault(frozenset(best[:2]), best)
        return extent, sorted(found.values(), key=lambda item: item[2])

    def near_note(self) -> str:
        extent, pairs = self.near_pairs()
        if not pairs:
            return ""
        listed = ", ".join(f"{a} {d:.2g} from {b}" for a, b, d in pairs[:NEAR_LISTED])
        more = f" (and {len(pairs) - NEAR_LISTED} more)" if len(pairs) > NEAR_LISTED else ""
        return (
            f"NOTE: new vertex beside another, not reused (closer than {NEAR_FRACTION:g} of the "
            f"extent {extent:.4g}): {listed}{more} - where one was meant to be the other, give "
            "that vertex itself (a station by points=ID, a fill side by sideK=..., not a rounded "
            "coordinate or fraction)"
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
    # Spoke directions given for the open ends (start, end): taken as
    # given (turned to the band's side), where ``ends`` is only followed
    # within 60 degrees of the normal.
    end_spokes: tuple[Point | None, Point | None] = (None, None)


def _left(t: Point) -> Point:
    return -t[1], t[0]


def _band(build: _Build, chain: _Chain, height: float, rows: int, growth: float,
          box: Sequence[float] | None, open_corners: Sequence[int] | str = ()) -> list[str]:
    """Build the band; return its front's vertices (the outermost row, or
    the box ends of the spokes) in the chain's order.

    ``open_corners`` (chain indices, or ``"all"`` for every corner that
    would get a fan) are convex corners left open: the band ends on each
    side at the spoke normal to that side and the wedge between the two
    spokes is not built."""
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
    opened: set[int] = set()
    asked = None if open_corners == "all" else set(open_corners)
    for k in range(count):
        incoming = (k - 1) % count if (chain.closed or k > 0) else None
        outgoing = k if (chain.closed or k < count - 1) else None
        if asked is not None and k in asked and (incoming is None or outgoing is None):
            raise TopologyError(f"{chain.vertices[k]} is an end of the chain, not a corner to leave open")
        if incoming is not None and outgoing is not None:
            n_in = normal(tangent_in(incoming))
            n_out = normal(tangent_out(outgoing))
            t_out = tangent_out(outgoing)
            turn = math.acos(max(-1.0, min(1.0, n_in[0] * n_out[0] + n_in[1] * n_out[1])))
            convex = n_in[0] * t_out[0] + n_in[1] * t_out[1] < -1.0e-12
            if box is None and (k in asked if asked is not None else convex and turn > FAN_ANGLE):
                if not convex:
                    raise TopologyError(
                        f"the corner at {chain.vertices[k]} is not convex on the band's side "
                        f"(it turns {math.degrees(turn):.1f} degrees the other way): only a convex "
                        "corner can be left open"
                    )
                spokes.append([(n_in, 1.0), (n_out, 1.0)])
                opened.add(k)
                continue
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
            given = chain.end_spokes[0] if outgoing is not None else chain.end_spokes[1]
            if given is not None:
                d = _unit(given[0], given[1])
                cosine = d[0] * n[0] + d[1] * n[1]
                if cosine < 0.0:
                    d, cosine = (-d[0], -d[1]), -cosine
                if cosine <= 1.0e-9:
                    raise TopologyError(
                        f"the {'start' if outgoing is not None else 'end'} spoke direction "
                        f"({given[0]:g}, {given[1]:g}) runs along the wall at {chain.vertices[k]}: "
                        "give a direction leaving the wall"
                    )
                # Its component normal to the wall is the band height.
                spokes.append([(d, 1.0 / cosine)])
                continue
            follow = chain.ends[0] if outgoing is not None else chain.ends[1]
            if follow is not None:
                d = _unit(follow[0], follow[1])
                if d[0] * n[0] + d[1] * n[1] > math.cos(math.radians(60.0)):
                    cosine = d[0] * n[0] + d[1] * n[1]
                    spokes.append([(d, 1.0 / cosine)])
                    continue
            spokes.append([(n, 1.0)])

    if box is not None:
        return _band_to_box(build, chain, position, spokes, box, edge_path)
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
                fan = spokes[k] if k not in opened else spokes[k][:1]
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
        # fans (none at a corner left open)
        for k in range(count):
            fan = spokes[k] if k not in opened else spokes[k][:1]
            for s in range(0, len(fan) - 1, 2):
                if r == 1:
                    ids = [chain.vertices[k], vid(k, s, 1), vid(k, s + 1, 1), vid(k, s + 2, 1)]
                    build.block(ids, expect=-chain.side)
                else:
                    for first, second in ((s, s + 1), (s + 1, s + 2)):
                        build.block([vid(k, first, r - 1), vid(k, second, r - 1),
                                     vid(k, second, r), vid(k, first, r)], expect=chain.side)
    front: list[str] = []
    for k in range(count):
        for spoke in range(len(spokes[k])):
            identifier = vid(k, spoke, rows)
            if not front or front[-1] != identifier:
                front.append(identifier)
    build.opened = [chain.vertices[k] for k in sorted(opened)]
    build.front = list(front)
    return front


def _front_note(chain: _Chain, front: Sequence[str], to_box: bool) -> str:
    """The band's front in the wall chain's order, for the note: IDs are
    given in creation order, which is not the chain's."""
    what = "box ends of the spokes" if to_box else "front"
    wall = f"wall chain {chain.vertices[0]}..{chain.vertices[-1]}"
    if chain.closed:
        return f"{what} round the closed {wall}: {_id_list(list(front), 12)}"
    return (
        f"{what} along the {wall}, in its order: {_id_list(list(front), 12)}; "
        f"its ends {front[0]} (beside {chain.vertices[0]}) and {front[-1]} (beside {chain.vertices[-1]})"
    )


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
    offset chord, clear of both ends, are kept (`_monotone_points`), and
    of those none whose spline segment runs backwards
    (`model.unhooked_points`)."""
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
    return unhooked_points(start, _monotone_points(start, out, end), end)


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
        # An open chain's end may lie on the box's edge (a band front ending
        # on a line the box shares); its spoke runs to another side.
        end = not chain.closed and k in (0, count - 1)
        slack = build.tol if end else 0.0
        if not (x0 - slack < px < x1 + slack and y0 - slack < py < y1 + slack):
            raise TopologyError(
                f"vertex {chain.vertices[k]} is not inside the box"
                + ("" if end else " (only an open chain's end vertex may lie on its edge)")
            )
        on = [side for side, gap in enumerate((py - y0, x1 - px, y1 - py, px - x0)) if abs(gap) <= build.tol]
        if end and on:
            # On the box's edge: the spoke runs along that edge, as a band's
            # open end follows the boundary it meets, to the box corner on
            # the band's side.
            if len(on) > 1:
                raise TopologyError(f"vertex {chain.vertices[k]} is at a corner of the box: no spoke runs from it")
            side = on[0]
            ends = {0: ((x0, y0), (x1, y0)), 1: ((x1, y0), (x1, y1)),
                    2: ((x1, y1), (x0, y1)), 3: ((x0, y1), (x0, y0))}[side]
            along = [(dx * (c[0] - px) + dy * (c[1] - py), c) for c in ends]
            reach, target = max(along)
            if reach <= 0.0:
                raise TopologyError(
                    f"the spoke of {chain.vertices[k]}, on the box's edge, runs out of the box"
                )
            hits.append(list(target))
            # It arrives at the corner on the side across the one it runs along.
            sides.append(next(other for other, on_it in enumerate(
                (target[1] == y0, target[0] == x1, target[1] == y1, target[0] == x0)) if on_it and other != side))
            continue
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
    return outer


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


def _corner_choice(fan: bool, open_at: Sequence[Point], box: bool) -> None:
    if not fan and open_at:
        raise TopologyError("give fan=false (every corner that would fan left open) or open_at, not both")
    if box and (open_at or not fan):
        raise TopologyError("to_box builds no fan: fan and open_at do not apply")


def _open_indices(chain: _Chain, position, fan: bool, open_at: Sequence[Point]):
    """The chain corners to leave open: ``"all"`` with ``fan=false``, else
    the chain vertex nearest each ``open_at`` point."""
    if not fan:
        return "all"
    chosen = []
    for point in open_at:
        nearest = min(range(len(chain.vertices)), key=lambda k: math.dist(position(chain.vertices[k]), point))
        chosen.append(nearest)
    return chosen


def _end_directions(start: Sequence[float], end: Sequence[float]) -> tuple[Point | None, Point | None]:
    out = []
    for name, value in (("start", start), ("end", end)):
        if value and len(value) != 2:
            raise TopologyError(f"{name} takes a direction dx,dy")
        if value and math.hypot(value[0], value[1]) <= 0.0:
            raise TopologyError(f"{name} is a zero direction")
        out.append((float(value[0]), float(value[1])) if value else None)
    return out[0], out[1]


def _opened_note(build: _Build) -> str:
    if not build.opened:
        return ""
    return f"corner(s) left open, no fan: {', '.join(build.opened)}"


def _select_chain(model: MeshModel, edges, bbox, between) -> list[EdgeKey]:
    """A band's or rows' chain: the edges given, the unassigned exterior
    edges inside ``bbox``, or the shortest chain of unassigned exterior
    edges ``between`` two vertices."""
    if not (edges or bbox or between):
        raise TopologyError("select the chain by edges, bbox (its unassigned exterior edges) "
                            "or between=A,B (the unassigned exterior chain joining two vertices)")
    if edges:
        return list(edges)
    if bbox:
        if len(bbox) != 4:
            raise TopologyError("bbox takes xmin, ymin, xmax, ymax")
        return _select_exterior(model, bbox, unassigned=True)
    if len(between) != 2:
        raise TopologyError("between takes two vertices")
    occurrences = model.edge_occurrences()
    free = sorted(e for e, items in occurrences.items() if len(items) == 1 and e not in model.edge_boundaries)
    return _exterior_path(model, free, between[0], between[1])


def add_band(
    model: MeshModel,
    *,
    edges: Sequence[EdgeKey] = (),
    bbox: Sequence[float] = (),
    height: float = 0.0,
    rows: int = 1,
    growth: float = 1.0,
    to_box: Sequence[float] = (),
    fan: bool = True,
    open_at: Sequence[Point] = (),
    start: Sequence[float] = (),
    end: Sequence[float] = (),
    between: Sequence[str] = (),
    prefix: str | None = None,
) -> ConstructResult:
    """One or more rows of blocks along a chain of exterior edges.

    ``fan=false`` leaves every convex corner that would get a fan open, and
    ``open_at`` the corner nearest each point: the band ends on each side at
    the spoke normal to that side. ``start``/``end`` give the direction of
    an open chain's first and last spoke (its normal component is the band
    height)."""
    if bool(to_box) == (height > 0.0):
        raise TopologyError("give a positive height or to_box, not both")
    if rows < 1 or (to_box and rows != 1):
        raise TopologyError("rows must be at least 1 (1 with to_box)")
    if growth <= 0.0:
        raise TopologyError("growth must be positive")
    _corner_choice(fan, open_at, bool(to_box))
    end_spokes = _end_directions(start, end)

    def work(working: MeshModel) -> ConstructResult:
        chosen = _select_chain(working, edges, bbox, between)
        if not chosen:
            raise TopologyError("no exterior edge selected")
        chain = _model_chain(working, chosen)
        if any(end_spokes):
            if chain.closed:
                raise TopologyError("start and end apply to an open chain; this one is closed")
            chain.end_spokes = end_spokes
        build = _Build(working, prefix)
        where = {v: (working.vertices[v].x, working.vertices[v].y) for v in chain.vertices}
        opened = _open_indices(chain, where.__getitem__, fan, open_at)
        front = _band(build, chain, height, rows, growth, tuple(to_box) or None, opened)
        build.finish()
        result = build.result("add_band")
        result.note = "; ".join(part for part in (_front_note(chain, front, bool(to_box)), _opened_note(build)) if part)
        return result

    return _atomic(model, work)


# ---------------------------------------------------------------------------
# add_rows
# ---------------------------------------------------------------------------


def add_rows(
    model: MeshModel,
    *,
    edges: Sequence[EdgeKey] = (),
    bbox: Sequence[float] = (),
    direction: Sequence[float] = (),
    to_x: float | None = None,
    to_y: float | None = None,
    distance: float = 0.0,
    rows: int = 1,
    growth: float = 1.0,
    stations: Sequence[float] = (),
    conform: bool = True,
    between: Sequence[str] = (),
    spread: float = 1.0,
    prefix: str | None = None,
) -> ConstructResult:
    """Rows of blocks carrying a chain of exterior edges along ``direction``.

    Every point of the chain (its vertices and the interior points of its
    curved edges) moves along the direction to the line ``x = to_x`` or
    ``y = to_y`` - each point its own distance - or by ``distance``; the
    far row is the chain's image there.  The rows between are at
    ``stations`` (x values with ``to_x``, y values with ``to_y``,
    distances with ``distance``: every row on one such line) or, without
    them, ``rows`` rows whose heights along each point's path grow by
    ``growth``.  Vertices are reused by coordinate; with ``conform`` an
    existing exterior vertex lying on a point's path becomes a station, so
    the rows meet what is already there.  A wake band from a trailing edge
    left open (`add_ring fan=false`) to the outlet, or a duct extended to
    its outlet, is one command.

    ``spread`` widens the rows: across the carry (along the target line;
    normal to the direction with ``distance``) every point's offset from
    the middle of the chain's extent grows linearly with its travel, to
    ``spread`` times it on the far row - a wake band that is thin at a
    trailing edge and wide at the outlet, so the wall's clustering is not
    carried to the outlet in one strip."""
    if len(direction) != 2 or math.hypot(direction[0], direction[1]) <= 0.0:
        raise TopologyError("direction takes dx,dy, not zero")
    targets = [to_x is not None, to_y is not None, distance > 0.0]
    if sum(targets) != 1:
        raise TopologyError("give one of to_x, to_y or a positive distance")
    if distance < 0.0:
        raise TopologyError("distance must be positive")
    d = _unit(direction[0], direction[1])
    if to_x is not None and abs(d[0]) <= 1.0e-12:
        raise TopologyError("the direction never reaches a line x = constant")
    if to_y is not None and abs(d[1]) <= 1.0e-12:
        raise TopologyError("the direction never reaches a line y = constant")
    if rows < 1 or growth <= 0.0:
        raise TopologyError("rows must be at least 1 and growth positive")
    if stations and rows != 1:
        raise TopologyError("give rows (with growth) or stations, not both")
    if spread <= 0.0:
        raise TopologyError("spread must be positive (1: no widening)")
    lateral = (0.0, 1.0) if to_x is not None else (1.0, 0.0) if to_y is not None else (-d[1], d[0])

    def reach(p: Point) -> float:
        """How far along the direction the point travels to the target."""
        if to_x is not None:
            return (to_x - p[0]) / d[0]
        if to_y is not None:
            return (to_y - p[1]) / d[1]
        return distance

    def travel(p: Point, value: float, absolute: bool) -> float:
        """The distance along the direction to a row: a fraction of the
        point's reach, or an absolute station (x, y or a distance)."""
        if not absolute:
            return value * reach(p)
        if to_x is not None:
            return (value - p[0]) / d[0]
        if to_y is not None:
            return (value - p[1]) / d[1]
        return value

    def work(working: MeshModel) -> ConstructResult:
        chosen = _select_chain(working, edges, bbox, between)
        if not chosen:
            raise TopologyError("no exterior edge selected")
        chain = _model_chain(working, chosen)
        build = _Build(working, prefix)
        position = {v: (working.vertices[v].x, working.vertices[v].y) for v in chain.vertices}
        count = len(chain.vertices)
        edge_count = count if chain.closed else count - 1
        build.scale(max(abs(c) for p in position.values() for c in p)
                    + max(abs(reach(p)) for p in position.values()))
        target = (f"x = {to_x:g}" if to_x is not None else f"y = {to_y:g}" if to_y is not None
                  else f"a distance {distance:g}")
        for vertex, p in position.items():
            if reach(p) <= build.tol:
                raise TopologyError(
                    f"{vertex} ({p[0]:.6g}, {p[1]:.6g}) does not reach {target} along the direction "
                    f"({d[0]:.4g}, {d[1]:.4g}): the line must lie ahead of every chain vertex"
                )
        for e in range(edge_count):
            for p in chain.interiors[e]:
                if reach(p) <= build.tol:
                    raise TopologyError(f"the curved edge from {chain.vertices[e]} crosses {target}")
        translation = distance > 0.0 and spread == 1.0
        if not translation:
            # An arc carried by different distances is no longer an arc:
            # its rows follow samples of it as splines.
            for e in range(edge_count):
                if chain.kinds[e] == "arc":
                    a, b = chain.vertices[e], chain.vertices[(e + 1) % count]
                    key = edge_key(a, b)
                    fractions = [k / 8.0 for k in range(1, 8)]
                    chain.interiors[e] = tuple(working.edge_points(
                        key, fractions if key[0] == a else [1.0 - f for f in fractions]))
        absolute = bool(stations)
        ignored = []
        if absolute:
            # A station on the target line is the far row itself.
            goal = to_x if to_x is not None else to_y if to_y is not None else distance
            values = []
            for value in sorted(set(stations)):
                if abs(value - goal) <= 1.0e-9 * (1.0 + abs(goal)):
                    ignored.append(value)
                else:
                    values.append(value)
        else:
            weights = [growth ** r for r in range(rows)]
            values = [sum(weights[:r]) / sum(weights) for r in range(1, rows)]
        # Existing exterior vertices on a point's path become stations.
        taken = []
        if conform:
            occurrences = working.edge_occurrences()
            rim = sorted({v for e, items in occurrences.items() if len(items) == 1 for v in e} - set(chain.vertices))
            for vertex_id in rim:
                q = (working.vertices[vertex_id].x, working.vertices[vertex_id].y)
                for p in position.values():
                    along = (q[0] - p[0]) * d[0] + (q[1] - p[1]) * d[1]
                    across = abs((q[0] - p[0]) * d[1] - (q[1] - p[1]) * d[0])
                    if across <= build.tol and build.tol < along < reach(p) - build.tol:
                        if absolute:
                            value = q[0] if to_x is not None else q[1] if to_y is not None else along
                        else:
                            value = along / reach(p)
                        if all(abs(value - kept) > build.tol for kept in values):
                            values.append(value)
                            taken.append(vertex_id)
                        break
            values = sorted(values)
        for value in values:
            for vertex, p in position.items():
                t = travel(p, value, absolute)
                if not build.tol < t < reach(p) - build.tol:
                    raise TopologyError(
                        f"the station {value:g} is not between {vertex} and {target} along the direction"
                    )
        # The rows in order along the direction (by the first vertex's
        # travel), the target last.
        first = position[chain.vertices[0]]
        rows_at = sorted(((value, absolute) for value in values), key=lambda row: travel(first, *row))
        rows_at.append((1.0, False))

        offsets = [p[0] * lateral[0] + p[1] * lateral[1] for p in position.values()]
        middle = 0.5 * (min(offsets) + max(offsets))

        def carried(p: Point, row: int) -> Point:
            if row == 0:
                return p
            t = travel(p, *rows_at[row - 1])
            widen = (spread - 1.0) * (t / reach(p)) * (p[0] * lateral[0] + p[1] * lateral[1] - middle)
            return p[0] + t * d[0] + widen * lateral[0], p[1] + t * d[1] + widen * lateral[1]

        quads = []
        for row in range(1, len(rows_at) + 1):
            for e in range(edge_count):
                a, b = chain.vertices[e], chain.vertices[(e + 1) % count]
                corners = [carried(position[a], row - 1), carried(position[b], row - 1),
                           carried(position[b], row), carried(position[a], row)]
                if not _ordered_convex(corners, chain.side):
                    raise TopologyError(
                        f"the row {row} block from edge {a}-{b} would not be strictly convex: the "
                        f"direction ({d[0]:.4g}, {d[1]:.4g}) must leave the chain on the side away "
                        "from its blocks, and the rows must not cross"
                    )
                quads.append(((e, row - 1), corners))
        _refuse_overlap(working, quads, {}, lambda e, r: f"the row {r + 1} block from edge "
                        f"{chain.vertices[e]}-{chain.vertices[(e + 1) % count]}")
        before_rim = [
            current for current, items in working.edge_occurrences().items()
            if len(items) == 1 and current not in working.edge_geometry
        ]
        ids: dict[tuple[int, int], str] = {}
        for row in range(len(rows_at) + 1):
            for k, vertex in enumerate(chain.vertices):
                if row == 0:
                    ids[k, row] = vertex
                    continue
                point = carried(position[vertex], row)
                if build.find(*point) is None:
                    for current in before_rim:
                        pa, pb = working.vertices[current[0]], working.vertices[current[1]]
                        if _segment_distance(point, (pa.x, pa.y), (pb.x, pb.y)) <= build.tol and \
                                min(math.dist(point, (pa.x, pa.y)), math.dist(point, (pb.x, pb.y))) > build.tol:
                            raise TopologyError(
                                f"a row vertex at ({point[0]:.6g}, {point[1]:.6g}) would fall inside the "
                                f"existing edge {current[0]}-{current[1]} and leave it non-conformal: "
                                "split that edge there first, or give a station there"
                            )
                ids[k, row] = build.vertex(point)
        for row in range(1, len(rows_at) + 1):
            for e in range(edge_count):
                a, b = e, (e + 1) % count
                build.block([ids[a, row - 1], ids[b, row - 1], ids[b, row], ids[a, row]], expect=chain.side)
                interior = chain.interiors[e]
                if not interior:
                    continue
                pa, pb = carried(position[chain.vertices[a]], row), carried(position[chain.vertices[b]], row)
                moved = [carried(p, row) for p in interior]
                if all(_segment_distance(p, pa, pb) <= build.tol * 10 for p in moved):
                    continue
                if chain.kinds[e] == "arc" and translation:
                    build.curve(ids[a, row], ids[b, row], "arc", moved)
                else:
                    build.curve(ids[a, row], ids[b, row], "spline" if chain.kinds[e] != "polyLine" else "polyLine",
                                unhooked_points(pa, moved, pb) if chain.kinds[e] != "polyLine" else moved)
        build.finish()
        result = build.result("add_rows")
        far = [ids[k, len(rows_at)] for k in range(count)]
        parts = [f"{len(rows_at)} row(s) of {edge_count} block(s) to {target}"
                 + (f", widened {spread:g} times" if spread != 1.0 else ""),
                 f"far row along the chain {chain.vertices[0]}..{chain.vertices[-1]}, in its order: "
                 f"{_id_list(far, 12)}" + ("" if chain.closed else
                                          f"; its ends {far[0]} (beside {chain.vertices[0]}) and "
                                          f"{far[-1]} (beside {chain.vertices[-1]})")]
        if taken:
            parts.append(f"stations from existing vertices on the paths: {_id_list(taken)}")
        if ignored:
            parts.append(f"station(s) on the target ignored: {', '.join(f'{v:g}' for v in ignored)}")
        parts.append(_thinnest_column(working, chain, position, edge_count, reach))
        result.note = "; ".join(parts)
        return result

    return _atomic(model, work)


def _thinnest_column(model: MeshModel, chain: _Chain, position, edge_count: int, reach) -> str:
    """The narrowest column the rows carry: its chain edge, that edge's
    length and how far it is carried - a fan front's short edge carried to
    the far field shows here before ``size`` fills it with thin cells."""
    count = len(chain.vertices)
    best = None
    for e in range(edge_count):
        a, b = chain.vertices[e], chain.vertices[(e + 1) % count]
        length = model.edge_length(edge_key(a, b))
        travel = 0.5 * (reach(position[a]) + reach(position[b]))
        if best is None or length < best[2]:
            best = (a, b, length, travel)
    a, b, length, travel = best
    return (f"thinnest column: edge {a}-{b}, {length:.3g} long, carried {travel:.3g} "
            f"({travel / length:.3g} times its length)")


# ---------------------------------------------------------------------------
# add_ring
# ---------------------------------------------------------------------------


def add_ring(
    model: MeshModel,
    curve: str | Sequence[str],
    height: float,
    *,
    count: int = 0,
    fractions: Sequence[float] = (),
    normals: Sequence[float] = (),
    points: Sequence[Point] = (),
    corner: float = math.degrees(CORNER_ANGLE),
    tips: bool = False,
    rows: int = 1,
    growth: float = 1.0,
    side: str = "",
    between: Sequence[float] = (),
    xs: Sequence[float] = (),
    ys: Sequence[float] = (),
    start: Sequence[float] = (),
    end: Sequence[float] = (),
    fan: bool = True,
    open_at: Sequence[Point] = (),
    prefix: str | None = None,
) -> ConstructResult:
    """A band along a reference curve, its wall edges on the curve
    (splines through the curve's points between stations, a point left out
    where OpenFOAM's spline would run backwards: `model.unhooked_points`).

    Round a closed curve (the default; ``side=in`` puts the band inside
    it): stations at every curve vertex turning more than ``corner``
    degrees, the arc-length ``fractions``, the point where the outward
    normal has each of the ``normals`` angles (refused unless there is
    exactly one), with ``tips`` the peak of every rounded convex corner
    (`_tip_stations`), and ``count`` edges spread by arc length between
    them; ``points`` puts a station at the curve point nearest each point
    (refused where that is not unique).

    Along an open curve (``side=left`` or ``right`` of its point order, no
    host block needed; `_open_ring`) - or several, given in order, each
    starting where the previous one ends, the joints kept as stations: the
    section ``between`` (default the whole curve) with stations at its ends,
    corners, ``fractions``, where it crosses each of ``xs`` and ``ys``, at
    the point nearest each of ``points``, and ``count`` edges between them
    (none: the ends and corners alone); ``start``/``end`` set the first and
    last spoke's direction (an inlet line), else they are normal to the wall.

    ``fan=false`` leaves every convex corner that would fan open, ``open_at``
    the station nearest each point (`_band`)."""
    if height <= 0.0:
        raise TopologyError("height must be positive")
    if side not in ("", "out", "in", "left", "right"):
        raise TopologyError("side is out or in (closed curve), left or right (open curve)")
    open_curve = side in ("left", "right")
    curves = [curve] if isinstance(curve, str) else list(curve)
    if not curves:
        raise TopologyError("give the curve")
    if len(curves) > 1 and not open_curve:
        raise TopologyError("several curves are joined into one open wall: give side=left or side=right "
                            "(a closed ring takes one curve)")
    if count < 0:
        raise TopologyError("count is the number of edges, at least 0")
    if not open_curve and not (count or fractions or normals or points):
        raise TopologyError("give count (edges round the curve), fractions, normals or points")
    if not 0.0 < corner < 180.0:
        raise TopologyError("corner is an angle in degrees between 0 and 180")
    if not open_curve:
        for name, value in (("between", between), ("xs", xs), ("ys", ys), ("start", start), ("end", end)):
            if value:
                raise TopologyError(
                    f"{name} applies along an open curve: give side=left or side=right "
                    "(the band's side of the curve's point order)"
                )
    elif normals or tips:
        raise TopologyError("normals and tips need a closed curve; on an open curve give fractions, xs or ys")
    _corner_choice(fan, open_at, False)
    end_spokes = _end_directions(start, end)
    corner_angle = math.radians(corner)

    def work(working: MeshModel) -> ConstructResult:
        if open_curve:
            build, chain, notes, stations = _open_ring(
                working, curves, side, between, corner_angle, corner, fractions, xs, ys, points,
                count, prefix)
            chain.end_spokes = end_spokes
            where = {v: (working.vertices[v].x, working.vertices[v].y) for v in chain.vertices}
            _band(build, chain, height, rows, growth, None,
                  _open_indices(chain, where.__getitem__, fan, open_at))
            build.finish()
            result = build.result("add_ring")
            result.note = "; ".join(part for part in (
                f"{stations} stations along the open curve, band on its {side}: "
                + ("; ".join(notes) or ("by arc length" if count else "its ends alone")),
                _front_note(chain, build.front, False), _opened_note(build)) if part)
            return result
        curve_id = curves[0]
        loop = _curve_polygon(working, curve_id)
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
        if points:
            nodes = [(lengths[k] / total, loop[k]) for k in range(n)]
            extent = _extent(loop)
            explicit.update(u * total for u in _station_points(nodes, True, points, extent, 1.0e-3 * extent))
            notes.append(f"{len(points)} station(s) at points")
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
            for piece_start, piece_end in pieces:
                share = max(1, round(count * (piece_end - piece_start) / total))
                for step in range(1, share):
                    stations.add((piece_start + (piece_end - piece_start) * step / share) % total)
            stations.add(anchors[0] % total)
        ordered = sorted({round(value, 12) for value in stations})
        if len(ordered) < 3:
            raise TopologyError("a ring needs at least three stations")

        def at(arc: float) -> Point:
            arc %= total
            k = max(0, min(n - 1, _bisect(lengths, arc) - 1))
            span = lengths[k + 1] - lengths[k]
            f = 0.0 if span <= 0.0 else (arc - lengths[k]) / span
            a, b = loop[k], loop[(k + 1) % n]
            return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])

        build = _Build(working, prefix)
        build.scale(_extent(loop) + max(abs(c) for p in loop for c in p))
        ids = [build.vertex(at(value)) for value in ordered]
        interiors = []
        for k, first in enumerate(ordered):
            last = ordered[(k + 1) % len(ordered)] + (total if k + 1 == len(ordered) else 0.0)
            inside = [loop[j % n] for j in range(n * 2)
                      if first + 1.0e-12 < (lengths[j % n] + (total if j >= n else 0.0)) < last - 1.0e-12]
            a_point, b_point = at(first), at(last)
            gap = 1.0e-3 * max(math.dist(a_point, b_point), build.tol)
            inside = [p for p in inside if math.dist(p, a_point) > gap and math.dist(p, b_point) > gap]
            if len(inside) > MAX_SPLINE_POINTS:
                step = len(inside) / MAX_SPLINE_POINTS
                inside = [inside[int(i * step)] for i in range(MAX_SPLINE_POINTS)]
            if not inside:
                inside = [at(0.5 * (first + last))]
            interiors.append(unhooked_points(a_point, inside, b_point))
        # outward: right of an anticlockwise loop; inward with side=in
        band_side = (-1.0 if area > 0.0 else 1.0) * (-1.0 if side == "in" else 1.0)
        kinds = _wall_edges(build, ids, interiors, [at(value) for value in ordered], True)
        chain = _Chain(ids, interiors, kinds, True, band_side)
        where = {v: (working.vertices[v].x, working.vertices[v].y) for v in chain.vertices}
        _band(build, chain, height, rows, growth, None, _open_indices(chain, where.__getitem__, fan, open_at))
        build.finish()
        result = build.result("add_ring")
        result.note = "; ".join(part for part in (
            f"{len(ordered)} stations: " + ("; ".join(notes) or "by arc length"), _opened_note(build)) if part)
        return result

    return _atomic(model, work)


def _wall_edges(build: _Build, ids: Sequence[str], interiors: list, points: Sequence[Point],
                closed: bool) -> list[str]:
    """Record the wall edges between consecutive stations - splines through
    their interior points, lines where those lie on the chord - and return
    each edge's kind; a straight edge's interior is emptied in place."""
    kinds = []
    for k in range(len(ids) if closed else len(ids) - 1):
        following = (k + 1) % len(ids)
        path = [points[k], *interiors[k], points[following]]
        if all(_segment_distance(p, path[0], path[-1]) <= build.tol * 10 for p in path[1:-1]):
            interiors[k] = ()
            kinds.append("line")
        else:
            build.curve(ids[k], ids[following], "spline", interiors[k])
            kinds.append("spline")
    return kinds


def _joined_curve(model: MeshModel, curves: Sequence[str]) -> tuple[str, list[Point], list[int]]:
    """The points of one curve, or of several open curves in order joined
    where each starts at the previous one's last point (the shared point
    kept once), with the indices of the joints in the joined list."""
    names, joined, joints = [], [], []
    for curve_id in curves:
        curve = model.geometry_curves.get(curve_id)
        if curve is None:
            raise TopologyError(f"Unknown curve {curve_id!r}")
        names.append(curve.name or curve_id)
        points = [(float(p[0]), float(p[1])) for p in curve.points]
        if not joined:
            joined = points
            continue
        size = _extent(joined + points)
        here = joined[-1]
        if math.dist(here, points[0]) > 1.0e-6 * size:
            reverse = math.dist(here, points[-1]) <= 1.0e-6 * size
            raise TopologyError(
                f"curve {names[-1]} does not start where {names[-2]} ends ({here[0]:.6g}, {here[1]:.6g}); "
                + ("it ends there: its points run the other way" if reverse else
                   f"it starts at ({points[0][0]:.6g}, {points[0][1]:.6g})")
                + " - several curves are joined end to start, in order"
            )
        joints.append(len(joined) - 1)
        joined.extend(points[1:])
    return " + ".join(names), joined, joints


def _nearest_parameter(nodes: Sequence[tuple[float, Point]], closed: bool, point: Point,
                       tie: float, apart: float, what: str) -> float:
    """The parameter of the point of a polyline (``nodes``: parameter and
    point, in order; ``closed`` joins the last to the first at parameter 1)
    nearest to ``point``, refused when another point of the polyline, more
    than ``apart`` from it, is as near within ``tie``."""
    segments = list(zip(nodes, nodes[1:]))
    if closed:
        segments.append((nodes[-1], (1.0 + nodes[0][0], nodes[0][1])))
    candidates = []
    for (ua, a), (ub, b) in segments:
        dx, dy = b[0] - a[0], b[1] - a[1]
        squared = dx * dx + dy * dy
        t = 0.0 if squared <= 0.0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
        q = (a[0] + t * dx, a[1] + t * dy)
        u = ua + t * (ub - ua)
        candidates.append((math.dist(point, q), u % 1.0 if closed else u, q))
    best = min(item[0] for item in candidates)
    close = sorted((item for item in candidates if item[0] <= best + tie), key=lambda item: item[0])
    distinct: list[tuple[float, float, Point]] = []
    for item in close:
        if all(math.dist(item[2], other[2]) > apart for other in distinct):
            distinct.append(item)
    if len(distinct) > 1:
        listed = ", ".join(f"({q[0]:.6g}, {q[1]:.6g})" for _d, _u, q in distinct[:4])
        raise TopologyError(
            f"{what} has no unique nearest curve point: {listed} are all {best:.4g} from it; "
            "give a point nearer the one meant"
        )
    return close[0][1]


def _station_points(nodes, closed: bool, places: Sequence[Point], extent: float, tolerance: float) -> list[float]:
    """Parameters of the stations at ``places``: each the nearest point of
    the polyline (`_nearest_parameter`; a tie within a millionth of the
    curve's size between points more than ``tolerance`` apart is refused)."""
    return [
        _nearest_parameter(nodes, closed, place, 1.0e-6 * extent, tolerance,
                           f"the point ({place[0]:.6g}, {place[1]:.6g})")
        for place in places
    ]


def _open_ring(working: MeshModel, curves: Sequence[str], side: str, between: Sequence[float],
               corner_angle: float, corner: float, fractions: Sequence[float], xs: Sequence[float],
               ys: Sequence[float], places: Sequence[Point], count: int, prefix: str | None):
    """The wall chain of a band along an open curve (the polyline through
    its points, no closing segment) - or several joined end to start, each
    joint a station (`_joined_curve`) - or the section ``between`` two
    arc-length fractions or two points (taken to the curve): stations at
    the section's ends and joints, at the curve's vertices turning more
    than the corner angle, at the ``fractions`` of the whole curve, where
    the section crosses each of ``xs`` and ``ys`` (exactly once, else
    refused), at the section's point nearest each of ``places``
    (`_nearest_parameter`), then ``count`` edges spread by arc length
    between them; wall edges are splines through the curve's points
    (`_CurvePath.edge_interior`, no segment running backwards)."""
    name, joined, joint_indices = _joined_curve(working, curves)
    path = _CurvePath(None, name, force_open=True, points=joined)
    points = list(path.points)
    lengths = _polyline_lengths(points)
    total = lengths[-1]
    if total <= 0.0:
        raise TopologyError(f"curve {path.name} has no length")
    if between:
        if len(between) == 2:
            for f in between:
                if not -1.0e-12 <= f <= 1.0 + 1.0e-12:
                    raise TopologyError(f"fraction {f:g} is not on the curve {path.name} (0 to 1)")
            s0, s1 = sorted((between[0] * total, between[1] * total))
        elif len(between) == 4:
            s0, s1 = sorted((_polyline_locate(points, lengths, (between[0], between[1]))[0],
                             _polyline_locate(points, lengths, (between[2], between[3]))[0]))
        else:
            raise TopologyError("between takes two fractions or two points (x0,y0,x1,y1)")
        if s1 - s0 <= 1.0e-12 * total:
            raise TopologyError("between names one point of the curve")
    else:
        s0, s1 = 0.0, total
    notes = []
    anchors = {s0, s1}
    joints = [lengths[k] for k in joint_indices if s0 < lengths[k] < s1]
    anchors.update(joints)
    if joints:
        notes.append(f"{len(joints)} joint(s) between curves")
    corners = []
    for k in range(1, len(points) - 1):
        if not s0 < lengths[k] < s1 or k in joint_indices:
            continue
        t1 = _unit(points[k][0] - points[k - 1][0], points[k][1] - points[k - 1][1])
        t2 = _unit(points[k + 1][0] - points[k][0], points[k + 1][1] - points[k][1])
        if abs(math.atan2(t1[0] * t2[1] - t1[1] * t2[0], t1[0] * t2[0] + t1[1] * t2[1])) > corner_angle:
            corners.append(lengths[k])
    anchors.update(corners)
    if corners:
        notes.append(f"{len(corners)} corner(s) above {corner:g} degrees")
    for f in fractions:
        value = f * total
        if not s0 - 1.0e-12 * total <= value <= s1 + 1.0e-12 * total:
            raise TopologyError(f"fraction {f:g} is outside the section given by between")
        anchors.add(min(s1, max(s0, value)))
    if fractions:
        notes.append(f"{len(fractions)} station(s) by fraction")
    inside = path.inside_with_parameters(s0, s1 - s0)
    samples = [0.0, *[u for u, _p in inside], 1.0]
    for axis, values in ((0, xs), (1, ys)):
        for value in values:
            hits = _crossings(path, s0, s1 - s0, samples, axis, value)
            if len(hits) != 1:
                axis_name = "x" if axis == 0 else "y"
                raise TopologyError(
                    f"the section of {path.name} crosses {axis_name} = {value:g} "
                    + (f"{len(hits)} times" if hits else "nowhere")
                    + ": a station needs exactly one crossing; narrow it with between=, "
                    "or give the station's point (points=)"
                )
            anchors.add(s0 + hits[0] * (s1 - s0))
    if xs or ys:
        notes.append(f"{len(xs) + len(ys)} station(s) by crossing")
    if places:
        nodes = [(0.0, path.at(s0)), *inside, (1.0, path.at(s1))]
        for u in _station_points(nodes, False, places, path.extent, path.tolerance):
            anchors.add(s0 + u * (s1 - s0))
        notes.append(f"{len(places)} station(s) at points")
    ordered = sorted(anchors)
    if count:
        extra = set()
        for first, last in zip(ordered, ordered[1:]):
            share = max(1, round(count * (last - first) / (s1 - s0)))
            extra.update(first + (last - first) * step / share for step in range(1, share))
        ordered = sorted(set(ordered) | extra)
    merged: list[float] = []
    for value in ordered:
        if not merged or value - merged[-1] > 1.0e-9 * total:
            merged.append(value)
    if len(merged) < 2:
        raise TopologyError("a band along an open curve needs at least two stations")
    build = _Build(working, prefix)
    build.scale(path.extent + max(abs(c) for p in points for c in p))
    station_points = [_polyline_at(points, lengths, value) for value in merged]
    ids = [build.vertex(point) for point in station_points]
    if len(set(ids)) != len(ids):
        raise TopologyError("two stations of the band fall on one existing vertex")
    tolerance = path.tolerance * 1.0e-3
    interiors = []
    for first, last in zip(merged, merged[1:]):
        interiors.append(path.edge_interior(first, last - first, tolerance))
    kinds = _wall_edges(build, ids, interiors, station_points, False)
    chain = _Chain(ids, interiors, kinds, False, 1.0 if side == "left" else -1.0)
    return build, chain, notes, len(merged)


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

    def __init__(self, model: MeshModel | None, curve_id: str, force_open: bool = False,
                 points: Sequence[Point] | None = None) -> None:
        if points is None:
            curve = model.geometry_curves.get(curve_id) if model is not None else None
            if curve is None:
                raise TopologyError(f"Unknown curve {curve_id!r}")
            name = curve.name or curve_id
            points = curve.points
        else:
            name = curve_id
        points = [(float(p[0]), float(p[1])) for p in points]
        extent = _extent(points)
        repeated = len(points) > 2 and math.dist(points[0], points[-1]) <= 1.0e-12 * (1.0 + extent)
        if repeated and not force_open:
            points.pop()
        segments = [math.dist(a, b) for a, b in zip(points, points[1:])]
        self.name = name
        self.points = points
        self.closed = not force_open and (repeated or (
            len(points) >= 3 and math.dist(points[-1], points[0]) <= max(segments) * (1.0 + 1.0e-6)))
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
        end, at most ``MAX_SPLINE_POINTS``, none leaving a segment that runs
        backwards: `model.unhooked_points`); empty when the section is
        straight."""
        a, b = self.at(start), self.at(start + length)
        gap = 1.0e-3 * max(math.dist(a, b), tol)
        inside = [p for p in self.inside(start, length) if math.dist(p, a) > gap and math.dist(p, b) > gap]
        if len(inside) > MAX_SPLINE_POINTS:
            inside = [inside[k] for k in _symmetric_picks(len(inside), MAX_SPLINE_POINTS)]
        if all(_segment_distance(p, a, b) <= tol * 10 for p in inside):
            return ()
        return unhooked_points(a, inside, b)


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
    points: Sequence[Point] = (),
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
    crosses exactly once), and the section's point nearest each of
    ``points`` (refused where that is not unique).  ``offset`` translates every station after it is
    placed, so the translated partner of a periodic chain gets stations
    matching this chain's node for node.  With ``id=NAME`` station k along
    the section is ``NAME_k``; a station on an existing vertex reuses it."""
    if count < 0 or count == 1:
        raise TopologyError("count is the number of stations, at least 2 (0: none)")
    if not (count or fractions or xs or ys or points):
        raise TopologyError("give count, fractions, xs, ys or points")
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
                        + ": a station needs exactly one crossing; narrow it with between=, "
                        "or give the station's point (points=)"
                    )
                found.append(hits[0])
        if points:
            if whole_loop:
                nodes = [(path.lengths[k] / total, p) for k, p in enumerate(path.points)]
            else:
                nodes = [(0.0, path.at(s0)), *path.inside_with_parameters(s0, length),
                         (1.0, path.at(s0 + length))]
            found += _station_points(nodes, whole_loop, points, path.extent, path.tolerance)
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
    sides: Sequence[Sequence[str]] = (),
    curves: Sequence[str | None] = (),
    prefix: str | None = None,
) -> ConstructResult:
    """Blocks filling a region of three, four or five sides whose sides run
    between existing vertices, interior vertices by transfinite
    interpolation.

    ``corners`` go round the region (either way); side k runs from corner k
    to corner k+1.  A side is, in this order: the vertex list given for it;
    else the chain of exterior edges joining its corners, its blocks
    outside the region (the band front, the grid side that is there); else
    open - straight, or along its reference curve - taking the standalone
    vertices lying on it as stations, or, with none, new stations at the
    opposite side's arc-length fractions (four sides).  Four sides need as
    many segments on opposite sides.  Three or five sides are filled as
    that many blocks round a centre vertex (`_build_polygon`), each side
    split at a station where two of them meet.  Edges between stations both
    on a side's curve follow it as splines through its points."""
    count = len(corners)
    if count not in (3, 4, 5) or len(set(corners)) != count:
        raise TopologyError("corners takes three, four or five distinct vertices, in order round the region")
    sides = list(sides) + [()] * max(0, count - len(sides))
    curves = list(curves) + [None] * max(0, count - len(curves))
    if any(sides[count:]) or any(curves[count:]):
        raise TopologyError(f"a fill with {count} corners has {count} sides: side{count + 1} and above are not used")
    sides, curves = sides[:count], curves[:count]

    def work(working: MeshModel) -> ConstructResult:
        build = _Build(working, prefix)
        position = {v.id: (v.x, v.y) for v in working.vertices.values()}
        build.scale(_extent([position[c] for c in corners]) + max(abs(c) for v in corners for c in position[v]))
        occurrences = working.edge_occurrences()
        paths = [_CurvePath(working, c) if c else None for c in curves]
        candidates: list[list] = []
        for k in range(count):
            a, b = corners[k], corners[(k + 1) % count]
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
            _resolve_side(working, chosen[k], corners[k], corners[(k + 1) % count], paths[k], position,
                          "given" if sides[k] else "chain", listed)
            for k in range(count)
        ]
        if count == 4:
            for first, second in ((0, 2), (1, 3)):
                _match_counts(resolved, first, second, corners)
            n, m = _build_quad(working, build, list(corners), resolved, sign)
            shape = f"{n} x {m} blocks"
        else:
            shape = _build_polygon(working, build, list(corners), resolved, sign)
        build.finish()
        result = build.result("add_fill")
        described = []
        for k, side in enumerate(resolved):
            a, b = corners[k], corners[(k + 1) % count]
            text = {
                "given": "as given",
                "chain": "the exterior chain",
                "stations": "open, through standalone stations",
                "matched": "open, new stations facing the opposite side's",
                "open": "open",
            }[side.kind]
            if side.kind == "stations":
                text += " " + _id_list([v for v in side.vertices[1:-1] if v is not None])
            curve_text = f" along {paths[k].name}" if paths[k] is not None else ""
            described.append(f"side{k + 1} {a}..{b}: {len(side.points) - 1} edge(s), {text}{curve_text}")
        result.note = f"{shape}; " + "; ".join(described)
        folds = _uniform_folds(working, result.blocks)
        if folds:
            result.note += (
                f"; NOTE: block(s) fold on the count-free map ({FOLD_CELLS} even fractions a "
                f"side): {folds} - a side bends further than the transfinite map follows; "
                "move a station or fill in parts"
            )
        return result

    return _atomic(model, work)


def _id_list(identifiers: Sequence[str], limit: int = 8) -> str:
    """IDs for a note, the middle elided above ``limit``."""
    if len(identifiers) <= limit:
        return ", ".join(identifiers)
    return f"{', '.join(identifiers[:3])}, ... {', '.join(identifiers[-3:])} ({len(identifiers)})"


def _uniform_folds(model: MeshModel, blocks: Sequence[str]) -> str:
    """The blocks among ``blocks`` folding on the count-free map, as
    ``sampling.folded_blocks`` names them; empty when none."""
    from .sampling import folded_blocks, sample_model

    if not blocks:
        return ""
    return folded_blocks(sample_model(model, uniform=FOLD_CELLS, blocks=blocks))


def _build_quad(working: MeshModel, build: _Build, corners: list[str], resolved: list[_FillSide],
                sign: float) -> tuple[int, int]:
    """The blocks of a four-sided fill whose sides are resolved and matched:
    interior vertices by blockMesh's edge-weighted transfinite interpolation
    of the side stations at their arc-length fractions, every block checked
    convex and clear of existing blocks before any vertex is made."""
    n = len(resolved[0].points) - 1
    m = len(resolved[1].points) - 1
    fractions = [side.fractions() for side in resolved]

    def boundary(i: int, j: int) -> tuple[str | None, Point]:
        if j == 0:
            return resolved[0].vertices[i], resolved[0].points[i]
        if i == n:
            return resolved[1].vertices[j], resolved[1].points[j]
        if j == m:
            return resolved[2].vertices[n - i], resolved[2].points[n - i]
        return resolved[3].vertices[m - j], resolved[3].points[m - j]

    corner_points = tuple(side.points[0] for side in resolved)
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
                corner_points,  # type: ignore[arg-type]
            )
    quads = [((i, j), [grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]])
             for j in range(m) for i in range(n)]
    names = {(i, j): boundary(i, j)[0] for i in range(n + 1) for j in range(m + 1)
             if i in (0, n) or j in (0, m)}
    for index, ((i, j), quad) in enumerate(quads):
        if not _ordered_convex(quad, sign):
            raise TopologyError(_fill_fold_message(working, build, len(build.created_blocks) + index,
                                                   i, j, n, m, quad, sign, names))
    _refuse_overlap(working, quads, names)
    # Vertices: new side stations, side by side, then the interior row by row.
    for side in resolved:
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
    return n, m


def _build_polygon(working: MeshModel, build: _Build, corners: list[str], resolved: list[_FillSide],
                   sign: float) -> str:
    """Three or five sides filled as that many four-sided fills round a
    centre vertex: the fill at corner k runs along side k to its split
    station M_k, along a spoke to the centre, back along the spoke to
    M_(k-1) and along side k-1 to the corner.  Opposite sides of each fill
    match when side k has ``s_k = σ_(k-1) + σ_(k+1)`` edges, σ_k the edges
    of spoke k; M_k is ``σ_(k-1)`` edges from side k's start.  The centre is
    the mean of the split stations."""
    n = len(resolved)
    counts = [len(side.points) - 1 for side in resolved]
    spokes = _spoke_counts(counts)
    if spokes is None or any(value.denominator != 1 or value < 1 for value in spokes):
        shown = ", ".join(str(value) for value in spokes) if spokes is not None else "none"
        name = "triangle" if n == 3 else "pentagon"
        extra = " and each side shorter than the other two together" if n == 3 else ""
        raise TopologyError(
            f"a {name} is filled as {n} blocks round a centre, so side k with s_k edges needs "
            f"spokes σ with s_k = σ_(k-1) + σ_(k+1), each a whole number of at least 1: the side "
            f"edge counts ({', '.join(map(str, counts))}) give σ = ({shown}); the counts must sum to "
            f"an even number{extra} - add or remove a station"
        )
    sigma = [int(value) for value in spokes]
    split = [sigma[(k - 1) % n] for k in range(n)]
    centre_points = [resolved[k].points[split[k]] for k in range(n)]
    centre = (sum(p[0] for p in centre_points) / n, sum(p[1] for p in centre_points) / n)
    centre_id = build.vertex(centre)
    made: dict[int, _FillSide] = {}   # spoke k, from M_k to the centre
    for k in range(n):
        previous = (k - 1) % n
        along = _slice_side(resolved[k], 0, split[k])
        back = _slice_side(resolved[previous], split[previous], counts[previous])
        out = made.get(k) or _straight_side(resolved[k].vertices[split[k]], resolved[k].points[split[k]],
                                            centre_id, centre)
        inward = (_reverse_side(made[previous]) if previous in made else
                  _straight_side(centre_id, centre, resolved[previous].vertices[split[previous]],
                                 resolved[previous].points[split[previous]]))
        quad = [along, out, inward, back]
        names = [corners[k], resolved[k].vertices[split[k]], centre_id,
                 resolved[previous].vertices[split[previous]]]
        _match_counts(quad, 0, 2, names)
        _match_counts(quad, 1, 3, names)
        _build_quad(working, build, names, quad, sign)  # type: ignore[arg-type]
        made.setdefault(k, quad[1])
        made.setdefault(previous, _reverse_side(quad[2]))
    return f"{n} fills round a centre ({', '.join(map(str, sigma))} edges on its spokes)"


def _spoke_counts(counts: Sequence[int]):
    """σ solving ``counts[k] = σ[k-1] + σ[k+1]`` (cyclic, an odd number of
    sides), as fractions; None when singular."""
    from fractions import Fraction

    n = len(counts)
    rows = [[Fraction(0)] * n + [Fraction(c)] for c in counts]
    for k in range(n):
        rows[k][(k - 1) % n] += 1
        rows[k][(k + 1) % n] += 1
    for column in range(n):
        pivot = next((r for r in range(column, n) if rows[r][column] != 0), None)
        if pivot is None:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(n):
            if r != column and rows[r][column] != 0:
                factor = rows[r][column] / rows[column][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[column])]
    return [rows[k][n] / rows[k][k] for k in range(n)]


def _slice_side(side: _FillSide, first: int, last: int) -> _FillSide:
    return _FillSide("given", side.vertices[first:last + 1], side.points[first:last + 1],
                     side.segments[first:last], side.lengths[first:last])


def _reverse_side(side: _FillSide) -> _FillSide:
    segments = [(segment[0], tuple(reversed(segment[1])), segment[2]) if segment[0] == "curve" else segment
                for segment in reversed(side.segments)]
    return _FillSide("given", list(reversed(side.vertices)), list(reversed(side.points)),
                     segments, list(reversed(side.lengths)))


def _straight_side(first: str | None, a: Point, second: str | None, b: Point) -> _FillSide:
    length = math.dist(a, b)
    return _FillSide("open", [first, second], [a, b], [("line", (), length)], [length], [a, b], None)


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
    rejected: list[str] = []
    for combination in itertools.product(*candidates):
        loop: list[Point] = []
        for k, choice in enumerate(combination):
            a, b = corners[k], corners[(k + 1) % len(corners)]
            if isinstance(choice, int):
                side = _open_path(position, a, b, paths[k], choice)
            else:
                side = _side_samples(model, choice, position)
            loop.extend(side[:-1])
        area = sum(loop[k][0] * loop[(k + 1) % len(loop)][1] - loop[(k + 1) % len(loop)][0] * loop[k][1]
                   for k in range(len(loop)))
        reading = _reading(corners, combination)
        if abs(area) <= 1.0e-12 * _extent(loop) ** 2:
            rejected.append(f"{reading}: the sides enclose no area")
            continue
        sign = 1.0 if area > 0.0 else -1.0
        ok = True
        for k, choice in enumerate(combination):
            if isinstance(choice, int):
                continue
            score = 0
            inside_edges = []
            for p, q in zip(choice, choice[1:]):
                key = edge_key(p, q)
                items = occurrences.get(key)
                if not items:
                    continue
                block = items[0][0]
                cx, cy = _block_centre(model, block)
                mx, my = model.edge_point(key, 0.5)
                tx, ty = position[q][0] - position[p][0], position[q][1] - position[p][1]
                outside = (tx * (cy - my) - ty * (cx - mx)) * sign < 0.0
                score += 1 if outside else -1
                if not outside:
                    inside_edges.append(f"{key[0]}-{key[1]} (block {block.id})")
            if score < 0 or (score == 0 and any(occurrences.get(edge_key(p, q)) for p, q in zip(choice, choice[1:]))):
                rejected.append(
                    f"{reading}: side{k + 1}'s edge(s) {', '.join(inside_edges[:3])}"
                    + (f" and {len(inside_edges) - 3} more" if len(inside_edges) > 3 else "")
                    + " have their block inside the region"
                )
                ok = False
                break
        if not ok:
            continue
        inside = _vertices_inside(model, loop, corners, combination)
        if inside:
            shown = ", ".join(
                f"{vertex} ({_fmt(model.vertices[vertex].x)}, {_fmt(model.vertices[vertex].y)})"
                for vertex in inside[:3]
            )
            rejected.append(
                f"{reading}: existing vertex {shown}"
                + (f" and {len(inside) - 3} more" if len(inside) > 3 else "")
                + " inside the region"
            )
            continue
        valid.append((sum(not isinstance(choice, int) for choice in combination), combination, sign))
    if not valid:
        shown = list(dict.fromkeys(rejected))
        raise TopologyError(
            "the sides between corners " + ", ".join(corners) + " bound no region with the existing "
            "blocks outside it and no existing vertex inside: "
            + "; ".join(shown[:MAX_FILL_REASONS])
            + (f"; {len(shown) - MAX_FILL_REASONS} more reading(s) rejected" if len(shown) > MAX_FILL_REASONS else "")
            + " - give the corners in order round the region, the sides explicitly (side1=...), "
            "or move or remove the vertex inside"
        )
    best = max(score for score, _c, _s in valid)
    top = [(combination, sign) for score, combination, sign in valid if score == best]
    if len(top) > 1 and sum(sign > 0 for _c, sign in top) == 1:
        # Corners that go round either of two regions (a half annulus
        # between two loops): the one they go round counter-clockwise.
        top = [item for item in top if item[1] > 0]
    if len(top) > 1:
        differing = [k + 1 for k in range(len(corners)) if len({str(c[k]) for c, _s in top}) > 1]
        raise TopologyError(
            "the region is ambiguous: side" + ", side".join(map(str, differing))
            + " can run more than one way between its corners; give it explicitly (sideK=A,...,B)"
        )
    return top[0]


def _reading(corners, combination) -> str:
    """One reading of a fill's sides, for a refusal: each side open (and
    which way round a closed curve) or the vertices it runs through."""
    parts = []
    for k, choice in enumerate(combination):
        if isinstance(choice, int):
            parts.append(f"side{k + 1} open" + {1: " forwards", -1: " backwards"}.get(choice, ""))
        elif len(choice) > 4:
            parts.append(f"side{k + 1} {choice[0]},{choice[1]},...,{choice[-1]}")
        else:
            parts.append(f"side{k + 1} " + ",".join(choice))
    return "with " + ", ".join(parts)


def _fmt(value: float) -> str:
    return format(value, ".6g")


def _vertices_inside(model: MeshModel, loop: Sequence[Point], corners, combination) -> list[str]:
    """The existing vertices inside the region bounded by ``loop``, clear
    of its boundary: a fill region holds none."""
    on_sides = set(corners)
    for choice in combination:
        if not isinstance(choice, int):
            on_sides.update(choice)
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    clearance = 1.0e-6 * math.hypot(x1 - x0, y1 - y0)
    found: list[str] = []
    for vertex in model.vertices.values():
        if vertex.id in on_sides or not (x0 < vertex.x < x1 and y0 < vertex.y < y1):
            continue
        point = (vertex.x, vertex.y)
        if _inside(point, loop) and _distance_to(point, loop, True) > clearance:
            found.append(vertex.id)
    return found


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


def _refuse_overlap(model: MeshModel, quads, names, label=None) -> None:
    """Refuse a fill block lying over an existing block: an existing vertex
    strictly inside it, or its centre inside an existing block. ``label``
    names a block by its (i, j) (default: the fill's column and row)."""
    if label is None:
        def label(i: int, j: int) -> str:
            return f"the fill's block at column {i + 1}, row {j + 1}"
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
                    f"{label(i, j)} would lie over the existing block {block.id}: check the corners and sides"
                )
        for vertex_id, point in every_vertex:
            if vertex_id in corners_here:
                continue
            if box[0] < point[0] < box[2] and box[1] < point[1] < box[3] and _strictly_inside(point, quad):
                raise TopologyError(
                    f"the existing vertex {vertex_id} lies inside {label(i, j)}: a side passes the "
                    "wrong side of it, or it should be a station"
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
