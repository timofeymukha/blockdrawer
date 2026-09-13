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


def layer_height(first: float, growth: float, cells: int) -> float:
    """Total thickness of ``cells`` geometric cells starting at ``first``."""
    return first * (growth**cells - 1.0) / (growth - 1.0)


class SizeMetric:
    """Requested cell size as a function of distance from the nearest wall."""

    def __init__(self, options: SizingOptions, scale: float, wall_loops):
        self.options = options
        self.scale = float(scale)
        self.first, self.core = options.widths(scale)
        self.growth = float(options.growth)
        self.layer_cells = layer_cells(self.first, self.growth, self.core)
        self.layer_height = layer_height(self.first, self.growth, self.layer_cells)
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

    def metric_length(self, path: np.ndarray) -> float:
        return g2.metric_length(path, self.size(path))

    def described(self) -> dict:
        return {
            "first_cell_width": self.first,
            "growth_ratio": self.growth,
            "core_cell_size": self.core,
            "first_width_over_scale": self.first / self.scale,
            "core_size_over_scale": self.core / self.scale,
            "layer_cells": self.layer_cells,
            "layer_height": self.layer_height,
            "layer_height_over_scale": self.layer_height / self.scale,
            "law": "size(d) = min(core, first + (growth - 1) * d)",
        }


@dataclass
class CountAssignment:
    counts: dict[tuple, int]
    components: list[dict]
    budget_factor: float
    total_cells: int
    unattainable: list[dict]


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
            targets.append(metric.metric_length(path))
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
    return CountAssignment(counts, records, factor, total_cells, unattainable)


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
    on_wall: set[pg.VertexKey] = set()
    for key, edge in graph.edges.items():
        if edge.role == "wall":
            on_wall.update(key)
    requests = []
    for key, edge in graph.edges.items():
        if edge.role != "layer_spoke":
            continue
        ends = [vertex for vertex in key if vertex in on_wall]
        if len(ends) != 1:
            continue
        requests.append(GradingRequest(key, ends[0], metric.first))
    return requests
