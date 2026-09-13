"""Generalized Voronoi graph over an arbitrary set of boundary sites.

The raster diagram only decides *topology*: which sites meet, where the
junctions are and which site pairs own a bisector branch.  Every coordinate
that reaches the block layout is then recomputed analytically, so the graph is
resolution independent within the range where the raster resolves the same
connectivity.

Junctions are refined by Gauss-Newton on the equidistance residuals, and every
branch is traced along its exact bisector with a predictor/corrector walk.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import linalg_lite
from sites import Site


@dataclass
class Junction:
    """A point equidistant from three or more sites."""

    index: int
    point: np.ndarray
    sites: tuple[int, ...]  # cyclic, anticlockwise around the junction
    directions: np.ndarray  # (k, 2) outgoing branch tangents, anticlockwise
    clearance: float
    residual: float
    seed_radius: float
    pixel_count: int

    @property
    def degree(self) -> int:
        return len(self.sites)

    def slot_pair(self, slot: int) -> tuple[int, int]:
        """Site pair of the branch leaving through boundary ``slot``."""
        order = self.sites
        return (order[slot - 1], order[slot])


@dataclass
class Branch:
    """One bisector arc of the generalized Voronoi diagram."""

    index: int
    pair: tuple[int, int]
    path: np.ndarray
    ends: tuple[tuple[int, int] | None, tuple[int, int] | None]
    closed: bool
    steps: int

    @property
    def length(self) -> float:
        return g2.total_length(self.path)


@dataclass
class Raster:
    bounds: tuple[float, float, float, float]
    width: int
    height: int
    labels: np.ndarray
    free: np.ndarray
    distances: np.ndarray
    pixel: float


@dataclass
class Diagram:
    sites: list[Site]
    scale: float
    junctions: list[Junction]
    branches: list[Branch]
    raster: Raster
    notes: list[str] = field(default_factory=list)

    def branches_of(self, site_index: int) -> list[Branch]:
        return [branch for branch in self.branches if site_index in branch.pair]


# ---------------------------------------------------------------------------
# Raster stage
# ---------------------------------------------------------------------------


def build_raster(sites: list[Site], width: int) -> Raster:
    farfield = sites[-1].curve
    loop = farfield.loop()
    xmin, ymin = np.min(loop, axis=0)
    xmax, ymax = np.max(loop, axis=0)
    span_x = float(xmax - xmin)
    span_y = float(ymax - ymin)
    height = max(16, int(round(width * span_y / span_x)))
    xs = np.linspace(xmin, xmax, width)
    ys = np.linspace(ymax, ymin, height)
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    distances = np.empty((len(sites), len(points)))
    for index, site in enumerate(sites):
        distances[index] = site.curve.distance(points)
    labels = np.argmin(distances, axis=0).astype(np.int16)
    free = farfield.contains(points)
    for site in sites[:-1]:
        free &= ~site.curve.contains(points)
    pixel = math.hypot(span_x / max(1, width - 1), span_y / max(1, height - 1))
    return Raster(
        (float(xmin), float(ymin), float(xmax), float(ymax)),
        width,
        height,
        labels.reshape(height, width),
        free.reshape(height, width),
        distances.reshape(len(sites), height, width),
        pixel,
    )


def _components(mask: np.ndarray, limit: int) -> list[np.ndarray]:
    visited = np.zeros_like(mask)
    result: list[np.ndarray] = []
    height, width = mask.shape
    for row, column in np.argwhere(mask):
        row = int(row)
        column = int(column)
        if visited[row, column]:
            continue
        visited[row, column] = True
        queue = deque([(row, column)])
        pixels: list[tuple[int, int]] = []
        while queue:
            current_row, current_column = queue.popleft()
            pixels.append((current_row, current_column))
            if len(pixels) > limit:
                raise ValueError("junction candidate cluster is implausibly large")
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    next_row = current_row + dy
                    next_column = current_column + dx
                    if (
                        (dx or dy)
                        and 0 <= next_row < height
                        and 0 <= next_column < width
                        and mask[next_row, next_column]
                        and not visited[next_row, next_column]
                    ):
                        visited[next_row, next_column] = True
                        queue.append((next_row, next_column))
        result.append(np.asarray(pixels, dtype=np.int64))
    return result


def _pixel_to_world(raster: Raster, rows, columns) -> np.ndarray:
    xmin, ymin, xmax, ymax = raster.bounds
    x = xmin + np.asarray(columns, dtype=np.float64) / max(1, raster.width - 1) * (
        xmax - xmin
    )
    y = ymax - np.asarray(rows, dtype=np.float64) / max(1, raster.height - 1) * (
        ymax - ymin
    )
    return np.column_stack((x, y))


# ---------------------------------------------------------------------------
# Exact refinement helpers
# ---------------------------------------------------------------------------


def _site_measures(sites: list[Site], indices, point: np.ndarray):
    query = point[None, :]
    distance = np.empty(len(indices))
    gradient = np.empty((len(indices), 2))
    for row, site_index in enumerate(indices):
        result = sites[site_index].curve.closest(query)
        distance[row] = result.distance[0]
        delta = point - result.point[0]
        norm = float(np.linalg.norm(delta))
        gradient[row] = delta / norm if norm > 0.0 else np.array([0.0, 0.0])
    return distance, gradient


def refine_junction(
    sites: list[Site], indices, start: np.ndarray, *, scale: float, steps: int = 60
) -> tuple[np.ndarray, float]:
    """Gauss-Newton walk towards equal distance to every listed site."""
    point = np.asarray(start, dtype=np.float64).copy()
    residual = math.inf
    for _ in range(steps):
        distance, gradient = _site_measures(sites, indices, point)
        errors = distance[1:] - distance[0]
        residual = float(np.max(np.abs(errors))) if len(errors) else 0.0
        if residual <= 1e-13 * scale:
            break
        jacobian = gradient[1:] - gradient[0][None, :]
        try:
            step = linalg_lite.solve_least_squares(jacobian, -errors)
        except ZeroDivisionError:  # pragma: no cover - defensive
            break
        limit = 0.5 * float(np.min(distance))
        length = float(np.linalg.norm(step))
        if length > limit > 0.0:
            step *= limit / length
        candidate = point + step
        new_distance, _ = _site_measures(sites, indices, candidate)
        new_errors = new_distance[1:] - new_distance[0]
        new_residual = float(np.max(np.abs(new_errors))) if len(new_errors) else 0.0
        factor = 1.0
        while new_residual > residual and factor > 1.0 / 64.0:
            factor *= 0.5
            candidate = point + factor * step
            new_distance, _ = _site_measures(sites, indices, candidate)
            new_errors = new_distance[1:] - new_distance[0]
            new_residual = (
                float(np.max(np.abs(new_errors))) if len(new_errors) else 0.0
            )
        if new_residual >= residual:
            break
        point = candidate
        residual = new_residual
    return point, residual


def project_to_bisector(
    sites: list[Site], pair: tuple[int, int], point: np.ndarray, *, scale: float
) -> np.ndarray:
    """Newton projection of a point onto the exact bisector of two sites."""
    current = np.asarray(point, dtype=np.float64).copy()
    for _ in range(40):
        distance, gradient = _site_measures(sites, pair, current)
        value = distance[0] - distance[1]
        if abs(value) <= 1e-14 * scale:
            break
        normal = gradient[0] - gradient[1]
        squared = float(normal @ normal)
        if squared <= 1e-18:
            break
        current = current - (value / squared) * normal
    return current


def _ring_sectors(
    sites: list[Site], point: np.ndarray, radius: float, samples: int = 1440
):
    angles = np.linspace(0.0, 2.0 * math.pi, samples, endpoint=False)
    ring = point[None, :] + radius * np.column_stack((np.cos(angles), np.sin(angles)))
    distances = np.empty((len(sites), samples))
    for index, site in enumerate(sites):
        distances[index] = site.curve.distance(ring)
    labels = np.argmin(distances, axis=0)
    changes = np.nonzero(labels != np.roll(labels, 1))[0]
    if len(changes) == 0:
        return [], np.zeros((0, 2)), []
    order = []
    boundary_angles = []
    for position in changes:
        order.append(int(labels[position]))
        before = angles[position - 1]
        after = angles[position]
        if after < before:
            after += 2.0 * math.pi
        boundary_angles.append(0.5 * (before + after))
    directions = np.column_stack(
        (np.cos(boundary_angles), np.sin(boundary_angles))
    )
    return order, directions, boundary_angles


def detect_junctions(
    sites: list[Site], raster: Raster, *, scale: float, tolerance_pixels: float = 1.6
) -> list[Junction]:
    if len(sites) < 3:
        return []
    sorted_distance = np.sort(raster.distances, axis=0)
    spread = sorted_distance[2] - sorted_distance[0]
    candidate = raster.free & (spread <= tolerance_pixels * raster.pixel)
    count = int(np.count_nonzero(candidate))
    if count == 0:
        return []
    if count > max(4000, int(0.02 * np.count_nonzero(raster.free))):
        raise ValueError(
            "junction detection produced an implausible number of candidate "
            "pixels; the raster is probably too coarse for this geometry"
        )
    clusters = _components(candidate, limit=20000)
    junctions: list[Junction] = []
    for pixels in clusters:
        centre = _pixel_to_world(raster, pixels[:, 0], pixels[:, 1]).mean(axis=0)
        distance, _ = _site_measures(sites, list(range(len(sites))), centre)
        order = np.argsort(distance)
        window = distance[order[0]] + 3.0 * raster.pixel
        members = [int(value) for value in order if distance[value] <= window]
        if len(members) < 3:
            members = [int(value) for value in order[:3]]
        point, residual = refine_junction(sites, members, centre, scale=scale)
        clearance = float(np.min(_site_measures(sites, members, point)[0]))
        radius = max(3.0 * residual, 1e-3 * clearance)
        sector_sites: list[int] = []
        directions = np.zeros((0, 2))
        while radius <= 0.25 * clearance:
            sector_sites, directions, _ = _ring_sectors(sites, point, radius)
            if len(sector_sites) >= 3:
                break
            radius *= 2.0
        if len(sector_sites) < 3:
            continue
        junctions.append(
            Junction(
                len(junctions),
                point,
                tuple(sector_sites),
                directions,
                clearance,
                residual,
                radius,
                len(pixels),
            )
        )
    return _merge_close_junctions(junctions, scale)


def _merge_close_junctions(junctions: list[Junction], scale: float) -> list[Junction]:
    """Drop duplicate clusters that refined onto the same junction point."""
    kept: list[Junction] = []
    for junction in junctions:
        duplicate = False
        for existing in kept:
            if (
                float(np.linalg.norm(existing.point - junction.point))
                <= 1e-7 * scale
                and set(existing.sites) == set(junction.sites)
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(
                Junction(
                    len(kept),
                    junction.point,
                    junction.sites,
                    junction.directions,
                    junction.clearance,
                    junction.residual,
                    junction.seed_radius,
                    junction.pixel_count,
                )
            )
    return kept


# ---------------------------------------------------------------------------
# Branch tracing
# ---------------------------------------------------------------------------


class TraceError(RuntimeError):
    """Raised when a bisector walk cannot be completed."""


def _tangent(gradient: np.ndarray, previous: np.ndarray | None) -> np.ndarray:
    normal = gradient[0] - gradient[1]
    norm = float(np.linalg.norm(normal))
    if norm <= 1e-12:
        raise TraceError("the bisector normal degenerated during tracing")
    normal = normal / norm
    tangent = np.array([-normal[1], normal[0]])
    if previous is not None and float(tangent @ previous) < 0.0:
        tangent = -tangent
    return tangent


def trace_bisector(
    sites: list[Site],
    pair: tuple[int, int],
    start: np.ndarray,
    direction: np.ndarray,
    *,
    scale: float,
    junctions: list[Junction],
    start_junction: int | None,
    start_offset: float,
    step_fraction: float = 0.12,
    min_step: float,
    max_steps: int = 40000,
):
    """Walk the exact bisector of ``pair`` until it reaches a junction."""
    others = [index for index in range(len(sites)) if index not in pair]
    point = project_to_bisector(
        sites, pair, start + start_offset * direction, scale=scale
    )
    path = [np.asarray(start, dtype=np.float64).copy(), point.copy()]
    previous = np.asarray(direction, dtype=np.float64)
    travelled = start_offset
    for step_index in range(max_steps):
        distance, gradient = _site_measures(sites, pair, point)
        tangent = _tangent(gradient, previous)
        clearance = float(np.min(distance))
        step = min(max(step_fraction * clearance, min_step), 0.05 * scale)
        candidate = project_to_bisector(
            sites, pair, point + step * tangent, scale=scale
        )
        moved = float(np.linalg.norm(candidate - point))
        if moved <= 0.1 * min_step:
            raise TraceError("the bisector walk stalled")
        travelled += moved
        if others and _outside_cell(sites, pair, others, candidate):
            crossing = _bisect_cell_exit(sites, pair, others, point, candidate, scale)
            snap = _nearest_junction(
                junctions,
                crossing,
                exclude=None,
                radius=max(4.0 * step, 8.0 * start_offset),
            )
            if snap is None:
                raise TraceError(
                    "the bisector left its Voronoi cell without reaching a "
                    "detected junction"
                )
            path.append(junctions[snap].point.copy())
            return np.asarray(path), snap, step_index + 1
        previous = tangent
        point = candidate
        path.append(point.copy())
    raise TraceError("the bisector walk exceeded its step budget")


def _outside_cell(sites, pair, others, point: np.ndarray) -> bool:
    other_distance, _ = _site_measures(sites, others, point)
    pair_distance, _ = _site_measures(sites, pair, point)
    return float(np.min(other_distance)) < float(np.min(pair_distance))


def _bisect_cell_exit(sites, pair, others, inside, outside, scale) -> np.ndarray:
    low = np.asarray(inside, dtype=np.float64)
    high = np.asarray(outside, dtype=np.float64)
    for _ in range(40):
        middle = project_to_bisector(sites, pair, 0.5 * (low + high), scale=scale)
        if _outside_cell(sites, pair, others, middle):
            high = middle
        else:
            low = middle
    return 0.5 * (low + high)


def _nearest_junction(
    junctions: list[Junction], point: np.ndarray, *, exclude: int | None, radius: float
) -> int | None:
    best = None
    best_distance = radius
    for junction in junctions:
        if exclude is not None and junction.index == exclude:
            continue
        distance = float(np.linalg.norm(junction.point - point))
        if distance <= best_distance:
            best_distance = distance
            best = junction.index
    return best


def _match_slot(junction: Junction, pair: tuple[int, int], arrival: np.ndarray) -> int:
    """Return the boundary slot of ``junction`` that a branch arrived through."""
    candidates = [
        slot
        for slot in range(junction.degree)
        if tuple(sorted(junction.slot_pair(slot))) == tuple(sorted(pair))
    ]
    if not candidates:
        raise TraceError("a branch arrived at a junction that does not carry its pair")
    if len(candidates) == 1:
        return candidates[0]
    scores = [float(junction.directions[slot] @ arrival) for slot in candidates]
    return candidates[int(np.argmax(scores))]


def trace_branches(
    sites: list[Site],
    raster: Raster,
    junctions: list[Junction],
    *,
    scale: float,
    notes: list[str],
) -> list[Branch]:
    min_step = 0.25 * raster.pixel
    branches: list[Branch] = []
    consumed: set[tuple[int, int]] = set()
    for junction in junctions:
        for slot in range(junction.degree):
            if (junction.index, slot) in consumed:
                continue
            pair = tuple(sorted(junction.slot_pair(slot)))
            path, target, steps = trace_bisector(
                sites,
                pair,
                junction.point,
                junction.directions[slot],
                scale=scale,
                junctions=junctions,
                start_junction=junction.index,
                start_offset=max(min_step, 2.0 * junction.seed_radius),
                min_step=min_step,
            )
            arrival = path[-2] - path[-1]
            arrival = arrival / max(float(np.linalg.norm(arrival)), 1e-300)
            target_slot = _match_slot(junctions[target], pair, arrival)
            consumed.add((junction.index, slot))
            consumed.add((target, target_slot))
            branches.append(
                Branch(
                    len(branches),
                    pair,
                    _clean_path(path, scale),
                    ((junction.index, slot), (target, target_slot)),
                    False,
                    steps,
                )
            )
    branches.extend(
        _trace_loop_branches(
            sites, raster, junctions, branches, scale=scale, notes=notes
        )
    )
    return branches


def _clean_path(path: np.ndarray, scale: float) -> np.ndarray:
    cleaned = g2.drop_repeated(path, 1e-12 * scale)
    if len(cleaned) < 2:
        raise TraceError("a traced branch collapsed to a single point")
    return cleaned


def _adjacent_pairs(raster: Raster) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    labels = raster.labels
    free = raster.free
    for dy, dx in ((0, 1), (1, 0)):
        first = labels[
            max(0, -dy) : labels.shape[0] - max(0, dy),
            max(0, -dx) : labels.shape[1] - max(0, dx),
        ]
        second = labels[
            max(0, dy) : labels.shape[0] - max(0, -dy),
            max(0, dx) : labels.shape[1] - max(0, -dx),
        ]
        shared = (
            free[
                max(0, -dy) : free.shape[0] - max(0, dy),
                max(0, -dx) : free.shape[1] - max(0, dx),
            ]
            & free[
                max(0, dy) : free.shape[0] - max(0, -dy),
                max(0, dx) : free.shape[1] - max(0, -dx),
            ]
        )
        changing = shared & (first != second)
        for low, high in zip(first[changing], second[changing]):
            pairs.add((int(min(low, high)), int(max(low, high))))
    return pairs


def _trace_loop_branches(
    sites: list[Site],
    raster: Raster,
    junctions: list[Junction],
    existing: list[Branch],
    *,
    scale: float,
    notes: list[str],
) -> list[Branch]:
    covered = {branch.pair for branch in existing}
    missing = sorted(_adjacent_pairs(raster) - covered)
    result: list[Branch] = []
    min_step = 0.25 * raster.pixel
    for pair in missing:
        seed = _seed_point(sites, raster, pair)
        if seed is None:
            notes.append(f"no bisector seed found for site pair {pair}")
            continue
        start = project_to_bisector(sites, pair, seed, scale=scale)
        _, gradient = _site_measures(sites, pair, start)
        try:
            tangent = _tangent(gradient, None)
        except TraceError as error:
            notes.append(f"pair {pair}: {error}")
            continue
        path = _trace_closed_loop(
            sites, pair, start, tangent, scale=scale, min_step=min_step
        )
        if path is None:
            notes.append(
                f"site pair {pair} has a bisector that is neither a closed loop "
                "nor attached to a junction"
            )
            continue
        result.append(
            Branch(
                len(existing) + len(result),
                pair,
                _clean_path(path, scale),
                (None, None),
                True,
                len(path),
            )
        )
    return result


def _seed_point(sites, raster: Raster, pair: tuple[int, int]) -> np.ndarray | None:
    first, second = pair
    gap = np.abs(raster.distances[first] - raster.distances[second])
    others = [index for index in range(len(sites)) if index not in pair]
    usable = raster.free.copy()
    if others:
        best_other = np.min(raster.distances[others], axis=0)
        usable &= best_other > raster.distances[first]
    if not np.any(usable):
        return None
    masked = np.where(usable, gap, np.inf)
    row, column = np.unravel_index(int(np.argmin(masked)), masked.shape)
    return _pixel_to_world(raster, [row], [column])[0]


def _trace_closed_loop(
    sites, pair, start, tangent, *, scale, min_step, max_steps: int = 40000
):
    others = [index for index in range(len(sites)) if index not in pair]
    point = start.copy()
    previous = tangent
    path = [point.copy()]
    travelled = 0.0
    for _ in range(max_steps):
        distance, gradient = _site_measures(sites, pair, point)
        direction = _tangent(gradient, previous)
        clearance = float(np.min(distance))
        step = min(max(0.12 * clearance, min_step), 0.05 * scale)
        candidate = project_to_bisector(
            sites, pair, point + step * direction, scale=scale
        )
        moved = float(np.linalg.norm(candidate - point))
        if moved <= 0.1 * min_step:
            return None
        travelled += moved
        if others:
            other_distance, _ = _site_measures(sites, others, candidate)
            pair_distance, _ = _site_measures(sites, pair, candidate)
            if float(np.min(other_distance)) < float(np.min(pair_distance)):
                return None
        if (
            travelled > 8.0 * step
            and float(np.linalg.norm(candidate - start)) <= 1.5 * step
        ):
            path.append(start.copy())
            return np.asarray(path)
        previous = direction
        point = candidate
        path.append(point.copy())
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_diagram(
    sites: list[Site], *, scale: float, grid_width: int = 1200
) -> Diagram:
    notes: list[str] = []
    raster = build_raster(sites, grid_width)
    junctions = detect_junctions(sites, raster, scale=scale)
    branches = trace_branches(sites, raster, junctions, scale=scale, notes=notes)
    return Diagram(sites, scale, junctions, branches, raster, notes)
