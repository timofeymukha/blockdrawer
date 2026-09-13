"""Boundary sites: closed bodies and the trial farfield.

A *site* owns one closed boundary curve.  The generalized Voronoi diagram is
built over the set of sites, so every site must answer distance and
closest-point queries and hand out ordered sections of its own boundary for
block-edge geometry.  Sections keep the source point list; raster samples never
replace supplied geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import geometry2d as g2


@dataclass(frozen=True)
class EdgeCurve:
    """Geometry for one emitted block edge between two boundary stations."""

    kind: str  # "line", "arc", "polyLine" or "spline"
    points: tuple[tuple[float, float], ...]


class BoundaryCurve:
    """Common interface of a closed, arc-length parameterised boundary."""

    name: str
    kind: str

    def loop(self) -> np.ndarray:
        raise NotImplementedError

    def length(self) -> float:
        raise NotImplementedError

    def closest(self, queries) -> g2.ClosestResult:
        raise NotImplementedError

    def distance(self, queries) -> np.ndarray:
        return self.closest(queries).distance

    def point_at(self, station: float) -> np.ndarray:
        raise NotImplementedError

    def section(self, start: float, end: float, *, forward: bool) -> np.ndarray:
        raise NotImplementedError

    def contains(self, queries) -> np.ndarray:
        raise NotImplementedError

    def tangent(self, station: float) -> np.ndarray:
        raise NotImplementedError

    @property
    def fluid_sign(self) -> float:
        """+1 when the fluid lies right of the anticlockwise boundary."""
        return 1.0 if self.kind == "wall" else -1.0

    def fluid_normal(self, station: float) -> np.ndarray:
        tangent = self.tangent(station)
        return self.fluid_sign * np.array([tangent[1], -tangent[0]])

    def edge_curve(
        self, start: float, end: float, *, forward: bool, style: str
    ) -> EdgeCurve:
        raise NotImplementedError


class PolygonCurve(BoundaryCurve):
    """A closed point list supplied by the user, kept verbatim."""

    def __init__(
        self, name: str, points, *, kind: str = "wall", tolerance: float = 0.0
    ):
        loop = g2.close_loop(points, tolerance)
        self._loop = g2.orient_anticlockwise(loop)
        self._cumulative = g2.cumulative_length(self._loop)
        self.name = name
        self.kind = kind

    def loop(self) -> np.ndarray:
        return self._loop

    def length(self) -> float:
        return float(self._cumulative[-1])

    def closest(self, queries) -> g2.ClosestResult:
        return g2.closest_on_polyline(self._loop, queries)

    def distance(self, queries) -> np.ndarray:
        return g2.distance_to_polyline(self._loop, queries)

    def point_at(self, station: float) -> np.ndarray:
        value = float(station) % self.length()
        index = int(np.searchsorted(self._cumulative, value, side="right")) - 1
        index = min(max(index, 0), len(self._loop) - 2)
        span = self._cumulative[index + 1] - self._cumulative[index]
        local = (value - self._cumulative[index]) / span if span > 0.0 else 0.0
        return self._loop[index] + local * (self._loop[index + 1] - self._loop[index])

    def section(self, start: float, end: float, *, forward: bool) -> np.ndarray:
        return g2.loop_section(self._loop, start, end, forward=forward)

    def contains(self, queries) -> np.ndarray:
        return g2.points_in_loop(self._loop, queries)

    def tangent(self, station: float) -> np.ndarray:
        value = float(station) % self.length()
        index = int(np.searchsorted(self._cumulative, value, side="right")) - 1
        index = min(max(index, 0), len(self._loop) - 2)
        delta = self._loop[index + 1] - self._loop[index]
        norm = float(np.linalg.norm(delta))
        return delta / norm if norm > 0.0 else np.array([1.0, 0.0])

    def edge_curve(
        self, start: float, end: float, *, forward: bool, style: str
    ) -> EdgeCurve:
        path = self.section(start, end, forward=forward)
        interior = path[1:-1]
        if len(interior) == 0:
            return EdgeCurve("line", ())
        kind = "spline" if style == "spline" else "polyLine"
        return EdgeCurve(kind, tuple((float(x), float(y)) for x, y in interior))


class CircleCurve(BoundaryCurve):
    """An analytic circular farfield; exported as exact circular arcs."""

    RENDER_SEGMENTS = 720

    def __init__(self, name: str, center, radius: float, *, kind: str = "farfield"):
        self._center = np.asarray(center, dtype=np.float64)
        self._radius = float(radius)
        if not math.isfinite(self._radius) or self._radius <= 0.0:
            raise ValueError("the farfield radius must be positive")
        self.name = name
        self.kind = kind
        angles = np.linspace(0.0, 2.0 * math.pi, self.RENDER_SEGMENTS + 1)
        self._loop = self._center[None, :] + self._radius * np.column_stack(
            (np.cos(angles), np.sin(angles))
        )
        self._loop[-1] = self._loop[0]

    @property
    def center(self) -> np.ndarray:
        return self._center

    @property
    def radius(self) -> float:
        return self._radius

    def loop(self) -> np.ndarray:
        return self._loop

    def length(self) -> float:
        return 2.0 * math.pi * self._radius

    def distance(self, queries) -> np.ndarray:
        points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
        radius = np.linalg.norm(points - self._center[None, :], axis=1)
        return np.abs(self._radius - radius)

    def closest(self, queries) -> g2.ClosestResult:
        points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
        offset = points - self._center[None, :]
        radius = np.linalg.norm(offset, axis=1)
        safe = np.where(radius > 0.0, radius, 1.0)
        direction = offset / safe[:, None]
        direction[radius == 0.0] = np.array([1.0, 0.0])
        target = self._center[None, :] + self._radius * direction
        angle = np.mod(np.arctan2(direction[:, 1], direction[:, 0]), 2.0 * math.pi)
        return g2.ClosestResult(
            np.abs(self._radius - radius),
            target,
            angle * self._radius,
            np.zeros(len(points), dtype=np.int64),
        )

    def _angle(self, station: float) -> float:
        return float(station) / self._radius

    def point_at(self, station: float) -> np.ndarray:
        angle = self._angle(station)
        return self._center + self._radius * np.array(
            [math.cos(angle), math.sin(angle)]
        )

    def section(self, start: float, end: float, *, forward: bool) -> np.ndarray:
        total = self.length()
        span = (end - start) % total if forward else -((start - end) % total)
        if abs(span) < 1e-12 * total:
            span = total if forward else -total
        count = max(3, int(math.ceil(abs(span) / total * self.RENDER_SEGMENTS)) + 1)
        stations = start + np.linspace(0.0, span, count)
        angles = stations / self._radius
        return self._center[None, :] + self._radius * np.column_stack(
            (np.cos(angles), np.sin(angles))
        )

    def contains(self, queries) -> np.ndarray:
        points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
        return np.linalg.norm(points - self._center[None, :], axis=1) < self._radius

    def tangent(self, station: float) -> np.ndarray:
        angle = self._angle(station)
        return np.array([-math.sin(angle), math.cos(angle)])

    def edge_curve(
        self, start: float, end: float, *, forward: bool, style: str
    ) -> EdgeCurve:
        total = self.length()
        span = (end - start) % total if forward else -((start - end) % total)
        if abs(span) < 1e-12 * total:
            span = total if forward else -total
        if abs(span) / total < 0.47:
            middle = self.point_at(start + 0.5 * span)
            return EdgeCurve("arc", ((float(middle[0]), float(middle[1])),))
        path = self.section(start, end, forward=forward)
        interior = path[1:-1]
        kind = "polyLine" if style == "polyLine" else "spline"
        return EdgeCurve(kind, tuple((float(x), float(y)) for x, y in interior))


@dataclass(frozen=True)
class Site:
    """One Voronoi site: a name, a boundary curve and its role."""

    index: int
    name: str
    curve: BoundaryCurve

    @property
    def kind(self) -> str:
        return self.curve.kind

    @property
    def is_wall(self) -> bool:
        return self.curve.kind == "wall"


def domain_frame(loops) -> tuple[np.ndarray, float]:
    """Return a translation/rotation covariant centre and radius.

    The centre is the arc-length weighted centroid of every supplied boundary
    and the radius is the largest distance from it to any boundary point.  Both
    are invariant to component order and to point-list reversal.
    """
    weights = []
    centres = []
    for loop in loops:
        length = g2.total_length(loop)
        weights.append(length)
        centres.append(g2.centroid_by_arclength(loop))
    total = float(sum(weights))
    if total <= 0.0:
        raise ValueError("supplied geometry has zero total boundary length")
    center = np.sum(
        np.asarray(centres) * np.asarray(weights)[:, None], axis=0
    ) / total
    radius = 0.0
    for loop in loops:
        radius = max(radius, float(np.max(np.linalg.norm(loop - center, axis=1))))
    if radius <= 0.0:
        raise ValueError("supplied geometry is degenerate")
    return center, radius


def canonical_order(loops, center, radius) -> list[int]:
    """Order bodies by geometry rather than by the order they were supplied.

    The key is built from dimensionless, rotation invariant measures, so a
    permuted input produces the same site order and therefore the same block
    and vertex identifiers.  Geometrically indistinguishable bodies keep their
    supplied order, which is the only information left to separate them.
    """
    keys = []
    for index, loop in enumerate(loops):
        closed = g2.close_loop(loop, 0.0)
        perimeter = g2.total_length(closed) / radius
        area = abs(g2.signed_area(closed)) / (radius * radius)
        centre = g2.centroid_by_arclength(closed)
        offset = float(np.linalg.norm(centre - center)) / radius
        spread = float(
            np.mean(np.linalg.norm(closed[:-1] - centre[None, :], axis=1))
        ) / radius
        keys.append(
            (
                -round(perimeter, 9),
                -round(area, 9),
                round(offset, 9),
                round(spread, 9),
                index,
            )
        )
    return [index for *_key, index in sorted(keys)]


def build_sites(
    names,
    loops,
    *,
    farfield_shape: str = "circle",
    farfield_scale: float = 3.0,
    farfield_name: str = "farfield",
) -> tuple[list[Site], float]:
    """Return the canonically ordered site list and the domain scale."""
    center, radius = domain_frame(loops)
    scale = 2.0 * radius
    order = canonical_order(loops, center, radius)
    sites: list[Site] = []
    for index, source in enumerate(order):
        name = names[source]
        sites.append(Site(index, name, PolygonCurve(name, loops[source], kind="wall")))
    outer_radius = radius * float(farfield_scale)
    if farfield_shape == "circle":
        curve: BoundaryCurve = CircleCurve(farfield_name, center, outer_radius)
    elif farfield_shape == "rectangle":
        half = outer_radius
        corners = [
            (center[0] - half, center[1] - half),
            (center[0] + half, center[1] - half),
            (center[0] + half, center[1] + half),
            (center[0] - half, center[1] + half),
        ]
        curve = PolygonCurve(farfield_name, corners, kind="farfield")
    else:
        raise ValueError(f"unknown farfield shape {farfield_shape!r}")
    sites.append(Site(len(sites), farfield_name, curve))
    return sites, scale


def closest_all(sites, queries) -> tuple[np.ndarray, np.ndarray]:
    """Return per-site distances and closest points for every query point."""
    points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
    distances = np.empty((len(sites), len(points)))
    targets = np.empty((len(sites), len(points), 2))
    for index, site in enumerate(sites):
        result = site.curve.closest(points)
        distances[index] = result.distance
        targets[index] = result.point
    return distances, targets


def unit_gradients(points: np.ndarray, targets: np.ndarray, distances: np.ndarray):
    """Gradient of the distance field, ``(p - closest) / d`` per site."""
    delta = points[None, :, :] - targets
    safe = np.where(distances > 0.0, distances, 1.0)
    return delta / safe[:, :, None]
