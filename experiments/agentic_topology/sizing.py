"""Metric-based cell-count quantisation and wall-normal grading.

The size metric is one scalar field: the requested cell size at a point.  Near
a wall it is the geometric boundary-layer series ``w0, w0*g, w0*g^2, ...``,
which in closed form is ``size(d) = w0 + (g - 1) * d`` capped at the isotropic
core size.  Every count and every grading value in the emitted session comes
from that field, so a change of resolution is a change of one number rather
than a different topology.

BlockDrawer forces opposite edges of a quadrilateral to share a cell count, so
the edges of a conformal patch graph fall into equality components.  For each
component this module picks the single positive integer that minimises the
length-weighted squared error against the metric lengths of its members, which
for an equality-only constraint is exactly the weighted mean, rounded.  No
mixed-integer dependency is needed or used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import geometry2d as g2
import patch_graph as pg


@dataclass(frozen=True)
class SizingOptions:
    """Everything the metric needs, expressed relative to the domain scale."""

    first_width_ratio: float = 2.0e-3
    growth: float = 1.2
    core_size_ratio: float = 4.0e-2
    first_width: float | None = None
    core_size: float | None = None
    # Wall-parallel spacing at the wall as a multiple of the first cell
    # width: the usual boundary-layer stretching (25 is a typical RANS
    # mesh).  It floors the size metric along every tangential edge - wall,
    # front, ring, level rows, wake lines - and leaves the wall-normal
    # edges, whose first cells the grading sets, and the rungs across a
    # wake.  None keeps the isotropic law, first-cell-sized cells along the
    # wall.
    wall_aspect: float | None = None
    minimum_cells: int = 1
    maximum_cells: int = 400
    budget: int | None = None
    graded_layers: bool = True

    def widths(self, scale: float) -> tuple[float, float]:
        first = (
            float(self.first_width)
            if self.first_width is not None
            else self.first_width_ratio * scale
        )
        core = (
            float(self.core_size)
            if self.core_size is not None
            else self.core_size_ratio * scale
        )
        if not (0.0 < first <= core):
            raise ValueError(
                "the first cell width must be positive and at most the core size"
            )
        if self.growth <= 1.0:
            raise ValueError("the growth ratio must be greater than one")
        return first, core


def layer_cells(first: float, growth: float, core: float) -> int:
    """Cells needed for the geometric series to reach the isotropic core size."""
    return max(1, int(math.ceil(math.log(core / first) / math.log(growth))) + 1)


def layer_height_of(first: float, growth: float, cells: int) -> float:
    """Total thickness of ``cells`` geometric cells starting at ``first``."""
    return first * (growth**cells - 1.0) / (growth - 1.0)


# Backwards-compatible name.
layer_height = layer_height_of

# The band height the default sizing's wall-normal series reaches the core
# size at, as a fraction of the domain scale.  Used as the geometric default
# for the requested band height so the topology does not follow the sizing;
# about 0.256.
DEFAULT_LAYER_HEIGHT_RATIO = layer_height_of(
    SizingOptions.first_width_ratio,
    SizingOptions.growth,
    layer_cells(
        SizingOptions.first_width_ratio,
        SizingOptions.growth,
        SizingOptions.core_size_ratio,
    ),
)


class SizeMetric:
    """Requested cell size as a function of distance from the nearest wall."""

    def __init__(
        self,
        options: SizingOptions,
        scale: float,
        wall_loops,
        *,
        layer_height: float | None = None,
    ):
        self.options = options
        self.scale = float(scale)
        self.first, self.core = options.widths(scale)
        self.growth = float(options.growth)
        self.layer_cells = layer_cells(self.first, self.growth, self.core)
        # The band height is a geometric parameter of the topology.  By
        # default it is the thickness at which the wall-normal series reaches
        # the core size, so a change of resolution keeps the same band; an
        # explicit height decouples the two.
        self.series_height = layer_height_of(self.first, self.growth, self.layer_cells)
        self.layer_height = (
            float(layer_height) if layer_height is not None else self.series_height
        )
        self.layer_height_explicit = layer_height is not None
        self._walls = [np.asarray(loop, dtype=np.float64) for loop in wall_loops]

    def wall_distance(self, points) -> np.ndarray:
        query = np.atleast_2d(np.asarray(points, dtype=np.float64))
        if not self._walls:
            return np.full(len(query), math.inf)
        best = np.full(len(query), math.inf)
        for wall in self._walls:
            best = np.minimum(best, g2.distance_to_polyline(wall, query))
        return best

    def size_at_distance(self, distance) -> np.ndarray:
        values = np.asarray(distance, dtype=np.float64)
        return np.minimum(self.core, self.first + (self.growth - 1.0) * values)

    def size(self, points) -> np.ndarray:
        return self.size_at_distance(self.wall_distance(points))

    # A tangential cell may turn at most this much along a curved edge: the
    # floor is capped at the edge's mean radius of curvature times this
    # angle, so a cell's chord stays close to the wall it follows (one cell
    # across a 45-degree cove wall folded against a band thinner than its
    # sagitta on 30P30N).
    MAX_CELL_TURNING = math.radians(15.0)

    @property
    def tangential_floor(self) -> float:
        """The smallest size along a tangential edge: ``wall_aspect`` first widths, or none."""
        aspect = self.options.wall_aspect
        return 0.0 if aspect is None else min(self.core, float(aspect) * self.first)

    def metric_length(self, path: np.ndarray, *, tangential: bool = False) -> float:
        """The path's length in cells of the size metric, the integral of ds/size.

        Long segments are subdivided at a quarter of the core size first, so
        the integral follows the core cap and a wall distance that is not
        linear along a segment; each piece is then integrated exactly for a
        linear size (``g2.metric_length``).  Band spokes, core spokes and
        sweep ribs are two-point lines, which the old per-vertex trapezoid
        overcounted 1.3 to 1.7 times at the default sizing and by a factor of
        about 67 at a first-cell width of 1e-5 of the scale.  A tangential
        edge's size is floored at ``tangential_floor``.
        """
        points = g2.subdivide(path, 0.25 * self.core)
        size = self.size(points)
        if tangential and self.tangential_floor > 0.0:
            floor = self.tangential_floor
            turning = float(g2.total_turning(np.asarray(path, dtype=np.float64)))
            length = float(g2.total_length(path))
            if turning > 0.0 and length > 0.0:
                floor = min(floor, (length / turning) * self.MAX_CELL_TURNING)
            size = np.maximum(size, floor)
        return g2.metric_length(points, size)

    def described(self) -> dict:
        return {
            "first_cell_width": self.first,
            "growth_ratio": self.growth,
            "core_cell_size": self.core,
            "wall_aspect": self.options.wall_aspect,
            "tangential_floor": self.tangential_floor,
            "first_width_over_scale": self.first / self.scale,
            "core_size_over_scale": self.core / self.scale,
            "layer_cells": self.layer_cells,
            "layer_height": self.layer_height,
            "layer_height_over_scale": self.layer_height / self.scale,
            "layer_height_source": (
                "geometric ratio" if self.layer_height_explicit else "wall-normal series"
            ),
            "series_height": self.series_height,
            "law": "size(d) = min(core, first + (growth - 1) * d)",
        }


@dataclass
class CountAssignment:
    counts: dict[tuple, int]
    components: list[dict]
    budget_factor: float
    total_cells: int
    unattainable: list[dict]
    # False when even the smallest counts the factor search reaches exceed
    # the budget: the bisection stops at its floor and says nothing else.
    budget_met: bool = True


# Edges whose first cells the grading sets - the spokes across the boundary
# layer and the core, the rungs across a wake band, and the wake lines and
# channel lines graded from the wall end - follow the isotropic law; every
# other edge runs along the wall, a front, a ring or a level and takes the
# tangential floor.  (A floored wake line of two cells carried a first-cell
# grading of ratio twelve against a uniform front piece and folded the
# flap's band under the main's wake on 30P30N.)
NORMAL_ROLES = frozenset({"layer_spoke", "core_spoke", "wake_rung", "wake", "wake_front"})


def _takes_floor(edge) -> bool:
    return edge.role not in NORMAL_ROLES and edge.provenance != "channel line"


def assign_counts(
    graph: pg.PatchGraph, metric: SizeMetric, options: SizingOptions
) -> CountAssignment:
    """Choose one cell count per opposite-edge equality component."""
    components = pg.constraint_components(graph)
    records = []
    for index, keys in enumerate(components):
        lengths = []
        targets = []
        for key in keys:
            path = graph.edges[key].path
            geometric = g2.total_length(path)
            lengths.append(geometric)
            targets.append(metric.metric_length(path, tangential=_takes_floor(graph.edges[key])))
        weights = np.asarray(lengths)
        wanted = np.asarray(targets)
        total = float(np.sum(weights))
        mean = float(np.dot(weights, wanted) / total) if total > 0.0 else 1.0
        records.append(
            {
                "component": index,
                "edges": len(keys),
                "keys": keys,
                "metric_lengths": [float(value) for value in wanted],
                "weighted_target": mean,
                "roles": sorted({graph.edges[key].role for key in keys}),
            }
        )
    raw = [record["weighted_target"] for record in records]
    factor = 1.0
    if options.budget is not None and options.budget > 0:
        factor = _budget_factor(graph, components, raw, options)
    counts: dict[tuple, int] = {}
    for record in records:
        value = int(
            min(
                options.maximum_cells,
                max(options.minimum_cells, round(record["weighted_target"] * factor)),
            )
        )
        record["cells"] = value
        record["relative_error"] = [
            float((value - target) / target) if target > 0.0 else math.inf
            for target in record["metric_lengths"]
        ]
        for key in record.pop("keys"):
            counts[key] = value
    total_cells = 0
    for face in graph.faces:
        edges = graph.face_edges(face)
        total_cells += counts[edges[0]] * counts[edges[1]]
    unattainable = [
        {
            "component": record["component"],
            "requested": record["weighted_target"],
            "assigned": record["cells"],
            "roles": record["roles"],
        }
        for record in records
        if abs(record["cells"] - record["weighted_target"] * factor) > 0.5 + 1e-9
    ]
    budget_met = options.budget is None or options.budget <= 0 or total_cells <= options.budget
    return CountAssignment(counts, records, factor, total_cells, unattainable, budget_met)


def _budget_factor(graph, components, targets, options: SizingOptions) -> float:
    """Uniform scale factor that keeps the total cell count under a budget."""
    index_of = {}
    for index, keys in enumerate(components):
        for key in keys:
            index_of[key] = index

    def total(factor: float) -> int:
        counts = [
            min(
                options.maximum_cells,
                max(options.minimum_cells, round(value * factor)),
            )
            for value in targets
        ]
        result = 0
        for face in graph.faces:
            edges = graph.face_edges(face)
            result += counts[index_of[edges[0]]] * counts[index_of[edges[1]]]
        return result

    if total(1.0) <= options.budget:
        return 1.0
    low, high = 1.0e-3, 1.0
    for _step in range(40):
        middle = 0.5 * (low + high)
        if total(middle) > options.budget:
            high = middle
        else:
            low = middle
    return low


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


@dataclass
class GradingRequest:
    """One wall-normal edge that should carry the boundary-layer series."""

    edge: tuple
    wall_vertex: pg.VertexKey
    first_width: float


def layer_grading_requests(graph: pg.PatchGraph, metric: SizeMetric) -> list:
    """Every band spoke, directed away from its wall vertex."""
    # Vertices on a physical wall.  Construction role ``wall`` is every
    # boundary edge - symmetry, inlet, far field - so a band spoke ending on a
    # symmetry plane was graded towards it; the domain role decides.  Graphs
    # without recorded roles (hand-built tests) fall back to the label.
    roles = graph.boundary_roles
    on_wall: set[pg.VertexKey] = set()
    for key, edge in graph.edges.items():
        if edge.role != "wall":
            continue
        physical = roles.get(edge.boundary) if (roles and edge.boundary is not None) else "wall"
        if physical == "wall":
            on_wall.update(key)
    # A blunt base's boundary layer runs along its strip: the two wake lines
    # leaving the base corners are the base's wall-normal edges.
    base_edges: set = set()
    for face in graph.faces:
        if face.provenance != "base strip":
            continue
        for key in graph.face_edges(face):
            if graph.edges[key].role == "wake":
                base_edges.add(key)
    requests = []
    for key, edge in graph.edges.items():
        if edge.role != "layer_spoke" and key not in base_edges:
            continue
        ends = [vertex for vertex in key if vertex in on_wall]
        if len(ends) != 1:
            continue
        requests.append(GradingRequest(key, ends[0], metric.first))
    # A wake strip's streamwise edges run from the body to the outlet beside
    # the band's first cells: uniform, fourteen cells of a chord each stood
    # next to 0.0002-wide cells on 30P30N (aspect 1e4).  Grade them from the
    # end nearer the wall.  All of them take one first width - the metric's
    # size just outside the band - because the two long sides of one wake
    # block are the wake axis, which starts on the wall, and a band edge line
    # that starts a band height off it: graded each from its own end's size
    # they sheared the cells between them to 87 degrees in checkMesh.
    first = float(np.asarray(metric.size_at_distance(np.asarray([metric.layer_height]))).reshape(-1)[0])
    for key, edge in graph.edges.items():
        # A channel line between two merged wake bands runs beside the
        # bands' lines from the hull to the outlet and must carry their
        # grading, or the strip between them shears (89 degrees on 30P30N).
        channel = edge.provenance == "channel line"
        if (edge.role not in ("wake", "wake_front") and not channel) or key in base_edges:
            continue
        points = np.asarray([graph.vertices[vertex].point for vertex in key], dtype=np.float64)
        distances = metric.wall_distance(points)
        near, far = (0, 1) if distances[0] <= distances[1] else (1, 0)
        if distances[far] <= 2.0 * max(distances[near], metric.first):
            continue
        requests.append(GradingRequest(key, tuple(key)[near], first))
    return requests
