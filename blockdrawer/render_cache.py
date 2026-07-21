"""Cached world-space paths and bounds for canvas rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable

from .domain import EdgeKey, edge_key
from .model import MeshModel


Point = tuple[float, float]
Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class RenderPath:
    """One immutable sampled path and its world-space bounding box."""

    points: tuple[Point, ...]
    bounds: Bounds


class RenderPathCache:
    """Retain one sampled path per current mesh edge and reference curve."""

    def __init__(self) -> None:
        self._edge_paths: dict[EdgeKey, tuple[Hashable, RenderPath]] = {}
        self._geometry_paths: dict[str, tuple[Hashable, RenderPath]] = {}

    def edge_path(
        self,
        model: MeshModel,
        edge: EdgeKey,
        *,
        arc_segments: int,
        spline_samples_per_span: int,
    ) -> RenderPath:
        current = edge_key(*edge)
        first = model.vertices[current[0]]
        second = model.vertices[current[1]]
        geometry = model.edge_geometry.get(current)
        signature = (
            first.x,
            first.y,
            second.x,
            second.y,
            None if geometry is None else (geometry.kind, geometry.points),
            arc_segments,
            spline_samples_per_span,
        )
        existing = self._edge_paths.get(current)
        if existing is not None and existing[0] == signature:
            return existing[1]
        path = _render_path(model.edge_render_points(
            current,
            arc_segments=arc_segments,
            spline_samples_per_span=spline_samples_per_span,
        ))
        self._edge_paths[current] = (signature, path)
        return path

    def geometry_path(
        self,
        model: MeshModel,
        curve_id: str,
        *,
        samples_per_span: int,
    ) -> RenderPath:
        curve = model.geometry_curves[curve_id]
        signature = (curve.points, samples_per_span)
        existing = self._geometry_paths.get(curve_id)
        if existing is not None and existing[0] == signature:
            return existing[1]
        path = _render_path(model.geometry_curve_render_points(
            curve_id, samples_per_span=samples_per_span
        ))
        self._geometry_paths[curve_id] = (signature, path)
        return path

    def prune(
        self,
        edges: Iterable[EdgeKey],
        curve_ids: Iterable[str],
    ) -> None:
        retained_edges = set(edges)
        retained_curve_ids = set(curve_ids)
        self._edge_paths = {
            current: entry
            for current, entry in self._edge_paths.items()
            if current in retained_edges
        }
        self._geometry_paths = {
            curve_id: entry
            for curve_id, entry in self._geometry_paths.items()
            if curve_id in retained_curve_ids
        }


def points_bounds(points: Iterable[Point]) -> Bounds:
    iterator = iter(points)
    first = next(iterator)
    min_x = max_x = first[0]
    min_y = max_y = first[1]
    for x, y in iterator:
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    return min_x, min_y, max_x, max_y


def bounds_intersect(first: Bounds, second: Bounds) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def point_in_bounds(point: Point, bounds: Bounds) -> bool:
    return (
        bounds[0] <= point[0] <= bounds[2]
        and bounds[1] <= point[1] <= bounds[3]
    )


def _render_path(points: Iterable[Point]) -> RenderPath:
    sampled = tuple(points)
    return RenderPath(sampled, points_bounds(sampled))
