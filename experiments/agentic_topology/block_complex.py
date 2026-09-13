"""A conformal quadrilateral block complex built from the patch layout.

Vertices carry an identity that is shared by every patch that touches them, so
the complex is conformal by construction: ring vertices belong to a branch and
are therefore seen by both incident cells, gate vertices belong to a cell's
wall, and every edge is looked up by its canonical vertex pair.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
from block_layout import Cut, Layout
from sites import EdgeCurve


@dataclass
class Vertex:
    key: tuple
    point: np.ndarray
    kind: str


@dataclass
class Edge:
    key: tuple[tuple, tuple]
    kind: str
    curve: EdgeCurve
    boundary: str | None
    path: np.ndarray


@dataclass
class Block:
    key: tuple
    corners: tuple[tuple, tuple, tuple, tuple]
    patch: int
    site: str


@dataclass
class Complex:
    vertices: dict[tuple, Vertex] = field(default_factory=dict)
    edges: dict[tuple, Edge] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_vertex(self, key: tuple, point, kind: str) -> tuple:
        existing = self.vertices.get(key)
        if existing is None:
            self.vertices[key] = Vertex(key, np.asarray(point, dtype=np.float64), kind)
        return key

    def edge_key(self, first: tuple, second: tuple) -> tuple:
        return (first, second) if first <= second else (second, first)

    def add_edge(self, first, second, kind, curve, boundary, path) -> None:
        key = self.edge_key(first, second)
        if key in self.edges:
            return
        stored = path if key[0] == first else path[::-1]
        geometry = curve
        if key[0] != first:
            geometry = EdgeCurve(curve.kind, tuple(reversed(curve.points)))
        self.edges[key] = Edge(key, kind, geometry, boundary, stored)

    def block_outline(self, block: Block) -> np.ndarray:
        pieces = []
        for index in range(4):
            first = block.corners[index]
            second = block.corners[(index + 1) % 4]
            edge = self.edges[self.edge_key(first, second)]
            path = edge.path if edge.key[0] == first else edge.path[::-1]
            pieces.append(path[:-1])
        return np.vstack([*pieces, pieces[0][:1]])

    def corner_points(self, block: Block) -> np.ndarray:
        return np.asarray([self.vertices[key].point for key in block.corners])


def _anchor_vertex_key(cut: Cut) -> tuple:
    return ("ring", *cut.anchor.key)


def _gate_vertex_key(cut: Cut) -> tuple:
    return ("gate", cut.cell, *cut.anchor.key)


def build_complex(layout: Layout, *, wall_edge_style: str = "polyLine") -> Complex:
    diagram = layout.diagram
    result = Complex()
    for cell_cuts in layout.cuts:
        for cut in cell_cuts:
            result.add_vertex(_anchor_vertex_key(cut), cut.ring_point, "ring")
            result.add_vertex(_gate_vertex_key(cut), cut.wall_point, "gate")
    for patch in layout.patches:
        cell = layout.cells[patch.cell]
        site = diagram.sites[cell.site]
        cell_cuts = layout.cuts[patch.cell]
        first = cell_cuts[patch.first_cut]
        second = cell_cuts[patch.second_cut]
        gate_first = _gate_vertex_key(first)
        gate_second = _gate_vertex_key(second)
        ring_first = _anchor_vertex_key(first)
        ring_second = _anchor_vertex_key(second)
        result.add_edge(
            gate_first,
            gate_second,
            "wall",
            site.curve.edge_curve(
                first.wall_station,
                second.wall_station,
                forward=True,
                style=wall_edge_style,
            ),
            site.name,
            patch.wall,
        )
        result.add_edge(
            ring_first,
            ring_second,
            "ring",
            _path_curve(patch.ring),
            None,
            patch.ring,
        )
        for gate, ring, point_gate, point_ring in (
            (gate_first, ring_first, first.wall_point, first.ring_point),
            (gate_second, ring_second, second.wall_point, second.ring_point),
        ):
            result.add_edge(
                gate,
                ring,
                "spoke",
                EdgeCurve("line", ()),
                None,
                np.asarray([point_gate, point_ring]),
            )
        corners = (gate_first, gate_second, ring_second, ring_first)
        points = np.asarray([result.vertices[key].point for key in corners])
        if g2.polygon_area(points) < 0.0:
            corners = tuple(reversed(corners))
        result.blocks.append(
            Block(("patch", patch.index), corners, patch.index, site.name)
        )
    return result


def _path_curve(path: np.ndarray) -> EdgeCurve:
    interior = path[1:-1]
    if len(interior) == 0:
        return EdgeCurve("line", ())
    return EdgeCurve("polyLine", tuple((float(x), float(y)) for x, y in interior))


# ---------------------------------------------------------------------------
# Validity and quality
# ---------------------------------------------------------------------------


@dataclass
class BlockReport:
    patch: int
    site: str
    convex: bool
    minimum_angle: float
    maximum_angle: float
    aspect_ratio: float
    area: float
    scaled_jacobian: float


def block_reports(complex_: Complex) -> list[BlockReport]:
    reports: list[BlockReport] = []
    for block in complex_.blocks:
        points = complex_.corner_points(block)
        angles = g2.quad_corner_angles(points)
        crosses = g2.quad_corner_crosses(points)
        sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        pairs = sides * np.roll(sides, -1)
        sign = 1.0 if g2.polygon_area(points) >= 0.0 else -1.0
        scaled = float(
            np.min(sign * crosses / np.where(pairs > 0.0, pairs, 1.0))
        )
        reports.append(
            BlockReport(
                block.patch,
                block.site,
                bool(np.all(sign * crosses > 0.0)),
                float(np.min(angles)),
                float(np.max(angles)),
                g2.quad_aspect_ratio(points),
                g2.polygon_area(points),
                scaled,
            )
        )
    return reports


def check_manifold(complex_: Complex) -> list[str]:
    """Structural checks that do not depend on BlockDrawer."""
    problems: list[str] = []
    incidence: dict[tuple, int] = {}
    for block in complex_.blocks:
        if len(set(block.corners)) != 4:
            problems.append(f"block {block.key} has repeated corners")
        for index in range(4):
            key = complex_.edge_key(
                block.corners[index], block.corners[(index + 1) % 4]
            )
            if key not in complex_.edges:
                problems.append(f"block {block.key} uses undefined edge {key}")
            incidence[key] = incidence.get(key, 0) + 1
    for key, count in incidence.items():
        if count > 2:
            problems.append(f"edge {key} is shared by {count} blocks")
    for key in complex_.edges:
        if key not in incidence:
            problems.append(f"edge {key} belongs to no block")
    return problems


def minimum_vertex_separation(complex_: Complex) -> float:
    points = np.asarray([vertex.point for vertex in complex_.vertices.values()])
    if len(points) < 2:
        return math.inf
    best = math.inf
    for index in range(len(points)):
        delta = points[index + 1 :] - points[index]
        if len(delta):
            best = min(best, float(np.min(np.linalg.norm(delta, axis=1))))
    return best


def coverage_check(
    layout: Layout, complex_: Complex, *, samples: int = 600
) -> dict[str, object]:
    """Independent raster test that the blocks tile the fluid region exactly.

    Every free raster pixel must lie in exactly one block outline.  This catches
    both uncovered core regions and overlapping blocks, neither of which
    ``MeshModel.validate()`` can see.
    """
    diagram = layout.diagram
    raster = diagram.raster
    xmin, ymin, xmax, ymax = raster.bounds
    xs = np.linspace(xmin, xmax, samples)
    ys = np.linspace(ymax, ymin, samples)
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    farfield = diagram.sites[-1].curve
    inside = farfield.contains(points)
    for site in diagram.sites[:-1]:
        inside &= ~site.curve.contains(points)
    counts = np.zeros(len(points), dtype=np.int32)
    for block in complex_.blocks:
        outline = complex_.block_outline(block)
        counts += g2.points_in_loop(outline, points).astype(np.int32)
    pixel = math.hypot(
        (xmax - xmin) / (samples - 1), (ymax - ymin) / (samples - 1)
    )
    # Neighbouring blocks share the identical boundary path, so the even-odd
    # test assigns a sample on a shared edge to exactly one of them.
    uncovered = inside & (counts == 0)
    overlapped = counts > 1
    outside_covered = ~inside & (counts > 0)
    area = pixel * pixel
    return {
        "samples": int(samples),
        "sample_area": area,
        "fluid_samples": int(np.count_nonzero(inside)),
        "uncovered_samples": int(np.count_nonzero(uncovered)),
        "overlapping_samples": int(np.count_nonzero(overlapped)),
        "outside_samples": int(np.count_nonzero(outside_covered)),
        "uncovered_area_fraction": float(
            np.count_nonzero(uncovered) / max(1, np.count_nonzero(inside))
        ),
        "overlap_area_fraction": float(
            np.count_nonzero(overlapped) / max(1, np.count_nonzero(inside))
        ),
        "worst_points": [
            [float(value) for value in point]
            for point in points[uncovered | overlapped][:12]
        ],
    }
