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

import heapq
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
    # How one cell count is chosen for an opposite-edge equality component
    # whose edges ask for different counts: ``mean`` takes the
    # length-weighted mean of the edges' metric lengths, so a wall piece
    # tied to a far-field piece hundreds of times longer is resolved as
    # the far field asks; ``max`` takes the finest demand, so every edge
    # meets the metric and the far field carries the wall's count.
    count_rule: str = "mean"
    # The largest ratio between the cells on the two sides of a block
    # interface, and along an edge from cell to cell, that the grading
    # propagation allows (``propagate_grading``): every edge's end widths
    # are matched to its neighbours' across the interfaces, finest first,
    # and an edge whose count cannot carry the series at this ratio has its
    # component's count raised.  None leaves the grading to the band spokes
    # and wake lines alone.
    interface_growth: float | None = None
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
            "count_rule": self.options.count_rule,
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
        if options.count_rule == "max":
            mean = float(np.max(wanted)) if len(wanted) else 1.0
        elif options.count_rule == "mean":
            mean = float(np.dot(weights, wanted) / total) if total > 0.0 else 1.0
        else:
            raise ValueError(f"unknown count rule {options.count_rule!r}; use 'mean' or 'max'")
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


def _series_ratio(first: float, length: float, cells: int) -> float:
    """Cell-to-cell ratio of the geometric series of ``cells`` cells from ``first`` over ``length`` (1 for a uniform fit)."""
    if cells < 2 or first <= 0.0 or length <= 0.0:
        return 1.0
    uniform = length / cells
    if abs(first - uniform) <= 1e-12 * max(uniform, 1e-300):
        return 1.0
    growing = first < uniform
    low, high = (1.0, 2.0) if growing else (0.5, 1.0)
    total = lambda q: first * (q ** cells - 1.0) / (q - 1.0) if abs(q - 1.0) > 1e-12 else first * cells
    if growing:
        while total(high) < length and high < 1.0e6:
            high *= 2.0
    else:
        while total(low) > length and low > 1.0e-6:
            low *= 0.5
    for _ in range(80):
        middle = 0.5 * (low + high)
        if (total(middle) < length) == growing:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def _cells_for_series(first: float, length: float, ratio: float) -> int:
    """Cells a series from ``first`` growing at ``ratio`` needs to reach ``length``."""
    if first <= 0.0 or length <= first:
        return 1
    if ratio <= 1.0 + 1e-12:
        return int(math.ceil(length / first))
    return int(math.ceil(math.log(1.0 + length * (ratio - 1.0) / first) / math.log(ratio)))


# Two edges meeting at a vertex across a block interface continue each other
# only when they leave the vertex at least this far from each other.
LINK_ANGLE = math.radians(90.0)
# Opposite edges of a block carry the same series only when their lengths are
# within this factor; a block fanning out further keeps its sides independent.
OPPOSITE_LENGTH_RATIO = 2.0


def propagate_grading(graph: pg.PatchGraph, counts: dict, requests: list, *, max_ratio: float, maximum_cells: int):
    """Match the cell widths across every block interface, finest first.

    Two edges that meet at a vertex on opposite sides of a block interface -
    the relation BlockDrawer's spacing links express - must agree on the
    cell width at that vertex.  The known widths are the band spokes' and
    wake lines' first cells (``requests``) and every edge's own uniform
    width; they are propagated in ascending order, so the finest demand at
    each vertex wins: an edge reached at one end takes that width there,
    its one geometric ratio follows from its count and length, and the
    width at its other end goes on to the edges linked there.  An edge
    whose ratio would exceed ``max_ratio`` has too few cells: the count
    its component needs for the series at the cap is returned in
    ``bumps`` (component index to cells), and the caller re-runs the
    propagation with the counts raised.  Returns ``(requests, bumps,
    report)``: one grading request per graded edge, the bumps, and the
    interface mismatches left (an edge reached at both ends, where one
    ratio cannot serve both).
    """
    edges = graph.edges
    lengths = {key: float(g2.total_length(edge.path)) for key, edge in edges.items()}
    # Links: for each face, each pair of adjacent sides meets at a corner;
    # across the shared side ``s`` at vertex ``v`` the side of this face and
    # the side of the neighbouring face continue each other.
    at_vertex: dict[tuple, dict[tuple, list[tuple]]] = {}
    # at_vertex[v][s] = list of (face index, edge e) where e is the side of
    # that face adjacent to s at v.
    for index, face in enumerate(graph.faces):
        keys = graph.face_edges(face)
        corners = list(face.corners)
        for k in range(4):
            v = corners[k]
            before = keys[(k - 1) % 4]
            after = keys[k]
            at_vertex.setdefault(v, {}).setdefault(before, []).append((index, after))
            at_vertex.setdefault(v, {}).setdefault(after, []).append((index, before))
    # Only an edge that continues the other across the interface is linked:
    # the two must leave the vertex in nearly opposite directions
    # (``LINK_ANGLE``).  At a seam wedge's gate the wall edge and the
    # wedge's spoke are adjacent to the same side in two faces, and linking
    # them would grade the wall at the spoke's first cell (which inverted
    # the cove's band block on 30P30N); at a wake band's hull exit the
    # hull piece across the band continues the far-field spoke, not the
    # wake line, whatever its role says.
    def leaving(key, v) -> np.ndarray:
        path = edges[key].directed_path(v)
        step = np.asarray(path[1], dtype=np.float64) - np.asarray(path[0], dtype=np.float64)
        norm = float(np.linalg.norm(step))
        return step / norm if norm > 0.0 else step

    # ... and of one family: a spoke continues a spoke, a tangential edge a
    # tangential edge - at a sharp trailing edge the wall arriving on one
    # side is nearly opposite the seam spoke leaving on the other, and the
    # angle alone would grade the whole wall at the spoke's first cell.
    # A ring or hull piece inside a wake band (both its ends the wake's
    # vertices) is a rung across the band and counts as a spoke.
    wake_anchors = {key[2:4] for key in graph.vertices if key[0] == "wake" and len(key) >= 4}

    def of_wake(vertex) -> bool:
        if vertex[0] == "wake":
            return True
        return any(vertex[i:i + 2] in wake_anchors for i in range(len(vertex) - 1))

    def family(key) -> str:
        role = edges[key].role
        if role in ("layer_spoke", "core_spoke", "wake_rung"):
            return "normal"
        if role in ("ring", "front") and all(of_wake(vertex) for vertex in key):
            return "normal"
        return "tangential"

    # The propagation works on the normal family alone - the spokes across
    # the bands and the core, the rungs across the wake bands and the
    # far-field lines continuing them: that is where the cells grow away
    # from a wall or a wake and where a thin strip's neighbour must start
    # at the strip's width.  Tangential edges keep their own rules (the
    # wall-parallel floor, the wake lines graded from the wall end): graded
    # through the chains too, a far-field level piece starting at a wake
    # band's edge handed its fractions down to a wall piece five times
    # shorter and graded the wall at two millionths on 30P30N.
    normal = {key for key in edges if family(key) == "normal"}
    # Each normal edge continues into the partner across the shared side
    # that leaves the vertex most nearly opposite to it, provided it leaves
    # the far side at all (``LINK_ANGLE``): at a wake band's hull exit the
    # far-field level spoke continues the hull piece across the band at
    # 115 degrees, since the hull runs oblique to the spoke there.
    links: dict[tuple, set[tuple]] = {}
    for v, by_side in at_vertex.items():
        for s, items in by_side.items():
            for i in range(len(items)):
                if items[i][1] not in normal:
                    continue
                best, best_dot = None, math.cos(LINK_ANGLE)
                for j in range(len(items)):
                    if i == j or items[i][0] == items[j][0] or items[j][1] not in normal:
                        continue
                    dot = float(leaving(items[i][1], v) @ leaving(items[j][1], v))
                    if dot < best_dot:
                        best, best_dot = items[j][1], dot
                if best is not None:
                    links.setdefault((items[i][1], v), set()).add((best, v))
                    links.setdefault((best, v), set()).add((items[i][1], v))
    # Within a block the opposite edge carries the same series: a wall edge
    # graded from one gate against a uniform front shears the band block
    # between them (431 inverted cells in the flap's band under the main's
    # wake on 30P30N).  ``opposite[(edge, vertex)]`` lists the opposite
    # edge's end on the same side of the block.
    opposite: dict[tuple, list[tuple]] = {}
    for face in graph.faces:
        keys = graph.face_edges(face)
        corners = list(face.corners)
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            d, c = corners[(k - 1) % 4], corners[(k + 2) % 4]
            opposite.setdefault((keys[k], a), []).append((keys[(k + 2) % 4], d))
            opposite.setdefault((keys[k], b), []).append((keys[(k + 2) % 4], c))
    components = pg.constraint_components(graph)
    component_of = {key: index for index, keys in enumerate(components) for key in keys}
    width: dict[tuple, float] = {}        # (edge, vertex) -> the width first assigned there
    actual: dict[tuple, float] = {}       # (edge, vertex) -> the width the edge's own series has there
    fixed: dict[tuple, tuple] = {}        # edge -> (vertex, width) the grading is set from
    queue: list[tuple] = []
    order = 0
    # The band spokes' first cells at the wall are the hard constraints; a
    # wake line's request (its first cell at the end nearer the wall, which
    # is a ring or hull vertex) is a seed the near-field strips override
    # when they arrive finer: hard, it clashed with the block before the
    # hull by a factor of twenty on 30P30N.
    passed_through = []
    for request in requests:
        if tuple(request.edge) not in normal:
            # A tangential request (a wake line graded from the wall end)
            # is emitted as it is.
            passed_through.append(request)
            continue
        heapq.heappush(queue, (float(request.first_width), 0, order, tuple(request.edge), request.wall_vertex, "request")); order += 1
    for key in normal:
        cells = int(counts.get(key, 1))
        uniform = lengths[key] / max(cells, 1)
        for v in key:
            heapq.heappush(queue, (uniform, 1, order, key, v, "uniform")); order += 1
    bumps: dict[int, int] = {}
    mismatches: list[dict] = []
    while queue:
        w, soft, _order, key, v, source = heapq.heappop(queue)
        if (key, v) in width:
            continue
        other = key[0] if key[1] == v else key[1]
        width[(key, v)] = w
        if key not in fixed:
            cells = int(counts.get(key, 1))
            length = lengths[key]
            clamped = False
            if soft == 1:
                ratio = 1.0
            else:
                ratio = _series_ratio(w, length, cells)
                if ratio > max_ratio + 1e-9:
                    needed = _cells_for_series(w, length, max_ratio)
                    if needed > cells and cells < maximum_cells:
                        component = component_of.get(key)
                        if component is not None:
                            bumps[component] = max(bumps.get(component, 0), min(needed, maximum_cells))
                    ratio = max_ratio
                    clamped = True
                elif ratio < 1.0 / max_ratio - 1e-9:
                    ratio = 1.0 / max_ratio
                    clamped = True
            fixed[key] = (v, w, soft, source, ratio)
            far_width = w * ratio ** max(cells - 1, 0) if cells > 1 else w
            actual[(key, v)] = w
            actual[(key, other)] = far_width
            # A clamped series is wrong until its count is raised in the
            # next round: it hands nothing on, or its capped ratio would
            # put microscopic first cells on the opposite edges.
            if soft != 1 and cells > 1 and not clamped:
                for opp_key, opp_v in opposite.get((key, v), ()):
                    if (opp_key, opp_v) in width or opp_key in fixed or opp_key not in normal:
                        continue
                    opp_cells = int(counts.get(opp_key, cells))
                    opp_length = lengths[opp_key]
                    # A block whose opposite edges differ in length by more
                    # than ``OPPOSITE_LENGTH_RATIO`` fans out, and the same
                    # fractions on both would put the short side's first
                    # cell at a fraction of the long side's (a core patch
                    # from a ring piece to a wall piece five times shorter
                    # graded the wall at two millionths on 30P30N); such a
                    # block keeps its sides independent.
                    if max(opp_length, length) > OPPOSITE_LENGTH_RATIO * min(opp_length, length):
                        continue
                    if abs(ratio - 1.0) < 1e-12:
                        first = opp_length / max(opp_cells, 1)
                    else:
                        first = opp_length * (ratio - 1.0) / (ratio ** opp_cells - 1.0)
                    heapq.heappush(queue, (float(first), 2, order, opp_key, opp_v, ("opposite", key, v))); order += 1
            if (key, other) not in width and not clamped:
                width[(key, other)] = far_width
                for linked in links.get((key, other), ()):
                    if linked not in width:
                        heapq.heappush(queue, (far_width, 2, order, linked[0], linked[1], ("far link", key, other))); order += 1
        for linked in links.get((key, v), ()):
            if linked not in width:
                heapq.heappush(queue, (w, 2, order, linked[0], linked[1], ("link", key, v))); order += 1
    # Mismatches: linked ends whose edges' own series disagree by more than the cap.
    for (key, v), w in actual.items():
        for linked in links.get((key, v), ()):
            w2 = actual.get(linked)
            if w2 is not None and max(w, w2) > max_ratio * min(w, w2) * (1.0 + 1e-6) and key < linked[0]:
                mismatches.append({"edges": [list(key), list(linked[0])], "vertex": list(v), "widths": [w, w2], "ratio": max(w, w2) / min(w, w2)})
    graded = list(passed_through)
    for key, (v, w, soft, _source, _ratio) in fixed.items():
        if soft == 1:
            continue
        graded.append(GradingRequest(key, v, float(w)))
    return graded, bumps, {"mismatches": sorted(mismatches, key=lambda item: -item["ratio"])[:16], "mismatch_count": len(mismatches), "graded": len(graded)}


@dataclass
class GradingRequest:
    """One wall-normal edge that should carry the boundary-layer series."""

    edge: tuple
    wall_vertex: pg.VertexKey
    first_width: float


def propagate_counts_and_grading(graph: pg.PatchGraph, assignment: CountAssignment, requests: list, options: SizingOptions, *, rounds: int = 8):
    """Run ``propagate_grading`` until no count needs raising, raising them in ``assignment``.

    Returns the grading requests to emit and a report (rounds run, the
    components raised with their old and new counts, the mismatches left).
    """
    components = pg.constraint_components(graph)
    raised: dict[int, tuple] = {}
    report: dict = {}
    graded = list(requests)
    for _round in range(rounds):
        graded, bumps, report = propagate_grading(
            graph, assignment.counts, requests, max_ratio=float(options.interface_growth), maximum_cells=options.maximum_cells
        )
        report["round"] = _round + 1
        if not bumps:
            break
        for index, cells in bumps.items():
            keys = components[index]
            before = int(assignment.counts.get(keys[0], 1))
            if cells <= before:
                continue
            for key in keys:
                assignment.counts[key] = int(cells)
            raised[index] = (raised.get(index, (before, cells))[0], int(cells))
            if index < len(assignment.components):
                assignment.components[index]["cells"] = int(cells)
                assignment.components[index]["raised_for_interface_growth"] = True
    total = 0
    for face in graph.faces:
        keys = graph.face_edges(face)
        total += int(assignment.counts[keys[0]]) * int(assignment.counts[keys[1]])
    assignment.total_cells = total
    report["raised"] = [{"component": index, "from": before, "to": after} for index, (before, after) in sorted(raised.items())]
    report["total_cells"] = total
    return graded, report


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
