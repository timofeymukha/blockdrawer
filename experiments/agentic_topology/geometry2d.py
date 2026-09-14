"""Vectorised planar-geometry helpers for the agentic-topology prototype.

Nothing here hard-codes a tolerance in model units: callers pass a domain
scale and derive their own tolerances from it.  Polylines are ``(k, 2)`` float
arrays.  A *loop* is a polyline whose last point repeats its first point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Basic polyline structure
# ---------------------------------------------------------------------------


def as_polyline(points) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2 or len(array) < 2:
        raise ValueError("a polyline needs at least two 2D points")
    if not np.all(np.isfinite(array)):
        raise ValueError("polyline coordinates must be finite")
    return array


def drop_repeated(points, tolerance: float) -> np.ndarray:
    """Return the polyline without consecutive duplicates."""
    array = np.asarray(points, dtype=np.float64)
    keep = [0]
    for index in range(1, len(array)):
        if math.dist(array[index], array[keep[-1]]) > tolerance:
            keep.append(index)
    return array[keep]


def close_loop(points, tolerance: float) -> np.ndarray:
    """Return a loop array whose last point repeats the first exactly."""
    array = drop_repeated(points, tolerance)
    if len(array) >= 2 and math.dist(array[0], array[-1]) <= tolerance:
        array = array[:-1]
    if len(array) < 3:
        raise ValueError("a closed body needs at least three distinct points")
    return np.vstack([array, array[:1]])


def segment_lengths(poly: np.ndarray) -> np.ndarray:
    return np.linalg.norm(poly[1:] - poly[:-1], axis=1)


def cumulative_length(poly: np.ndarray) -> np.ndarray:
    return np.concatenate(([0.0], np.cumsum(segment_lengths(poly))))


def total_length(poly: np.ndarray) -> float:
    return float(np.sum(segment_lengths(poly)))


def signed_area(loop: np.ndarray) -> float:
    """Signed area of a closed loop; positive when anticlockwise."""
    x = loop[:-1, 0]
    y = loop[:-1, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def orient_anticlockwise(loop: np.ndarray) -> np.ndarray:
    return loop if signed_area(loop) > 0.0 else loop[::-1].copy()


def centroid_by_arclength(poly: np.ndarray) -> np.ndarray:
    """Arc-length weighted centroid; covariant under rigid motions."""
    lengths = segment_lengths(poly)
    midpoints = 0.5 * (poly[1:] + poly[:-1])
    total = float(np.sum(lengths))
    if total <= 0.0:
        return poly[0].copy()
    return np.sum(midpoints * lengths[:, None], axis=0) / total


# ---------------------------------------------------------------------------
# Sampling and sectioning
# ---------------------------------------------------------------------------


def sample_at_arclength(poly: np.ndarray, distances) -> np.ndarray:
    """Evaluate a polyline at absolute arc-length positions."""
    lengths = segment_lengths(poly)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    total = float(cumulative[-1])
    values = np.clip(np.asarray(distances, dtype=np.float64), 0.0, total)
    index = np.clip(
        np.searchsorted(cumulative, values, side="right") - 1, 0, len(lengths) - 1
    )
    safe = np.where(lengths[index] > 0.0, lengths[index], 1.0)
    local = (values - cumulative[index]) / safe
    return poly[index] + local[:, None] * (poly[index + 1] - poly[index])


def resample(poly: np.ndarray, count: int) -> np.ndarray:
    """Return ``count`` points spread evenly by arc length, endpoints kept."""
    if count < 2:
        raise ValueError("resampling needs at least two points")
    total = total_length(poly)
    result = sample_at_arclength(poly, np.linspace(0.0, total, count))
    result[0] = poly[0]
    result[-1] = poly[-1]
    return result


def densify(poly: np.ndarray, step: float) -> np.ndarray:
    """Insert points so that no segment is longer than ``step``."""
    pieces = [poly[:1]]
    for start, end in zip(poly[:-1], poly[1:]):
        distance = float(np.linalg.norm(end - start))
        parts = max(1, int(math.ceil(distance / step))) if step > 0.0 else 1
        fractions = np.linspace(0.0, 1.0, parts + 1)[1:]
        pieces.append(start[None, :] + fractions[:, None] * (end - start)[None, :])
    return np.vstack(pieces)


def polyline_section(poly: np.ndarray, start: float, end: float) -> np.ndarray:
    """Return the sub-path of an open polyline between two arc lengths."""
    cumulative = cumulative_length(poly)
    total = float(cumulative[-1])
    first = min(max(float(start), 0.0), total)
    last = min(max(float(end), 0.0), total)
    if last < first:
        return polyline_section(poly, last, first)[::-1].copy()
    tolerance = 1e-12 * max(total, 1.0)
    interior = poly[(cumulative > first + tolerance) & (cumulative < last - tolerance)]
    ends = sample_at_arclength(poly, [first, last])
    return np.vstack([ends[:1], interior, ends[1:]])


def loop_section(
    loop: np.ndarray, start: float, end: float, *, forward: bool
) -> np.ndarray:
    """Return the sub-path of a closed loop between two arc lengths.

    ``forward`` follows increasing arc length, which is the loop's stored
    winding direction.  Equal endpoints request the complete loop.
    """
    cumulative = cumulative_length(loop)
    total = float(cumulative[-1])
    tolerance = 1e-12 * total
    origin = float(start) % total
    target = float(end) % total
    nodes = cumulative[:-1]
    if forward:
        span = (target - origin) % total
        if span <= tolerance:
            span = total
        candidates = np.concatenate([nodes, nodes + total])
        window = (candidates > origin + tolerance) & (
            candidates < origin + span - tolerance
        )
        stations = np.concatenate(
            ([origin], np.sort(candidates[window]), [origin + span])
        )
    else:
        span = (origin - target) % total
        if span <= tolerance:
            span = total
        candidates = np.concatenate([nodes - total, nodes])
        window = (candidates < origin - tolerance) & (
            candidates > origin - span + tolerance
        )
        stations = np.concatenate(
            ([origin], np.sort(candidates[window])[::-1], [origin - span])
        )
    points = sample_at_arclength(loop, np.mod(stations, total))
    points[0] = sample_at_arclength(loop, [origin])[0]
    points[-1] = sample_at_arclength(loop, [np.mod(stations[-1], total)])[0]
    return points


# ---------------------------------------------------------------------------
# Closest-point queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosestResult:
    distance: np.ndarray
    point: np.ndarray
    arclength: np.ndarray
    segment: np.ndarray


def closest_on_polyline(
    poly: np.ndarray, queries, *, chunk: int = 256
) -> ClosestResult:
    """Closest point on a polyline for every query point."""
    starts = poly[:-1]
    vectors = poly[1:] - poly[:-1]
    lengths_squared = np.einsum("ij,ij->i", vectors, vectors)
    safe = np.where(lengths_squared > 0.0, lengths_squared, 1.0)
    cumulative = np.concatenate(([0.0], np.cumsum(np.sqrt(lengths_squared))))
    points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
    distance = np.empty(len(points))
    closest = np.empty((len(points), 2))
    arclength = np.empty(len(points))
    segment = np.empty(len(points), dtype=np.int64)
    for first in range(0, len(points), chunk):
        sample = points[first : first + chunk]
        offset = sample[:, None, :] - starts[None, :, :]
        fraction = np.einsum("qsi,si->qs", offset, vectors) / safe[None, :]
        np.clip(fraction, 0.0, 1.0, out=fraction)
        targets = starts[None, :, :] + fraction[:, :, None] * vectors[None, :, :]
        delta = sample[:, None, :] - targets
        squared = np.einsum("qsi,qsi->qs", delta, delta)
        index = np.argmin(squared, axis=1)
        rows = np.arange(len(sample))
        stop = first + len(sample)
        distance[first:stop] = np.sqrt(squared[rows, index])
        closest[first:stop] = targets[rows, index]
        arclength[first:stop] = cumulative[index] + fraction[rows, index] * np.sqrt(
            lengths_squared[index]
        )
        segment[first:stop] = index
    return ClosestResult(distance, closest, arclength, segment)


def distance_to_polyline(poly: np.ndarray, queries, *, chunk: int = 256) -> np.ndarray:
    """Distances only; avoids materialising the closest-point arrays.

    Chunks stay small on purpose: the per-segment temporaries then fit in cache,
    which matters when the raster asks for a million distances at once.
    """
    starts = poly[:-1]
    vectors = poly[1:] - poly[:-1]
    lengths_squared = np.einsum("ij,ij->i", vectors, vectors)
    safe = np.where(lengths_squared > 0.0, lengths_squared, 1.0)
    points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
    answer = np.empty(len(points))
    for first in range(0, len(points), chunk):
        sample = points[first : first + chunk]
        offset_x = sample[:, None, 0] - starts[None, :, 0]
        offset_y = sample[:, None, 1] - starts[None, :, 1]
        fraction = offset_x * vectors[None, :, 0]
        fraction += offset_y * vectors[None, :, 1]
        fraction /= safe[None, :]
        np.clip(fraction, 0.0, 1.0, out=fraction)
        offset_x -= fraction * vectors[None, :, 0]
        offset_y -= fraction * vectors[None, :, 1]
        offset_x *= offset_x
        offset_y *= offset_y
        offset_x += offset_y
        answer[first : first + len(sample)] = np.sqrt(np.min(offset_x, axis=1))
    return answer


def minimum_separation(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.min(closest_on_polyline(second, first[:-1]).distance))


# ---------------------------------------------------------------------------
# Containment, simplification, curvature
# ---------------------------------------------------------------------------


def points_in_loop(loop: np.ndarray, queries, *, chunk: int = 256) -> np.ndarray:
    """Vectorised even-odd containment test for a closed loop."""
    points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
    x1, y1 = loop[:-1, 0], loop[:-1, 1]
    x2, y2 = loop[1:, 0], loop[1:, 1]
    sloped = y1 != y2
    x1, y1, x2, y2 = x1[sloped], y1[sloped], x2[sloped], y2[sloped]
    if not len(x1):
        return np.zeros(len(points), dtype=bool)
    inverse = 1.0 / (y2 - y1)
    inside = np.empty(len(points), dtype=bool)
    for first in range(0, len(points), chunk):
        sample = points[first : first + chunk]
        py = sample[:, 1][:, None]
        crossing = (y1[None, :] > py) != (y2[None, :] > py)
        x_at = x1[None, :] + (py - y1[None, :]) * ((x2 - x1) * inverse)[None, :]
        hits = crossing & (sample[:, 0][:, None] < x_at)
        inside[first : first + len(sample)] = np.count_nonzero(hits, axis=1) % 2 == 1
    return inside


def simplify(poly: np.ndarray, tolerance: float) -> np.ndarray:
    """Ramer-Douglas-Peucker simplification in model units."""
    if len(poly) <= 2:
        return poly
    keep = np.zeros(len(poly), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(poly) - 1)]
    limit = tolerance * tolerance
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        span = poly[last] - poly[first]
        span_squared = float(span @ span)
        candidates = poly[first + 1 : last]
        if span_squared == 0.0:
            delta = candidates - poly[first]
        else:
            fraction = np.clip(
                (candidates - poly[first]) @ span / span_squared, 0.0, 1.0
            )
            delta = candidates - (poly[first] + fraction[:, None] * span)
        squared = np.einsum("ij,ij->i", delta, delta)
        relative = int(np.argmax(squared))
        if float(squared[relative]) > limit:
            index = first + 1 + relative
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return poly[keep]


def turning_angles(poly: np.ndarray, *, closed: bool) -> np.ndarray:
    """Signed exterior turning angle at every interior (or every) vertex."""
    if closed:
        nodes = poly[:-1]
        previous = np.roll(nodes, 1, axis=0)
        current = nodes
        following = np.roll(nodes, -1, axis=0)
    else:
        previous = poly[:-2]
        current = poly[1:-1]
        following = poly[2:]
    incoming = current - previous
    outgoing = following - current
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = np.einsum("ij,ij->i", incoming, outgoing)
    return np.arctan2(cross, dot)


def discrete_curvature(loop: np.ndarray) -> np.ndarray:
    """Signed turning per unit length at every distinct loop node."""
    nodes = loop[:-1]
    angles = turning_angles(loop, closed=True)
    previous = np.roll(nodes, 1, axis=0)
    following = np.roll(nodes, -1, axis=0)
    span = 0.5 * (
        np.linalg.norm(nodes - previous, axis=1)
        + np.linalg.norm(following - nodes, axis=1)
    )
    return angles / np.where(span > 0.0, span, 1.0)


def total_turning(poly: np.ndarray) -> float:
    """Accumulated absolute turning along an open polyline, in radians."""
    if len(poly) < 3:
        return 0.0
    return float(np.sum(np.abs(turning_angles(poly, closed=False))))


# ---------------------------------------------------------------------------
# Quadrilateral measures
# ---------------------------------------------------------------------------


def quad_corner_crosses(corners) -> np.ndarray:
    points = np.asarray(corners, dtype=np.float64)
    following = np.roll(points, -1, axis=0)
    after = np.roll(points, -2, axis=0)
    incoming = following - points
    outgoing = after - following
    return incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]


def strictly_convex(corners, tolerance: float = 0.0) -> bool:
    values = quad_corner_crosses(corners)
    return bool(np.all(values > tolerance) or np.all(values < -tolerance))


def quad_corner_angles(corners) -> np.ndarray:
    """Interior angles, in radians, of the straight-sided quadrilateral."""
    points = np.asarray(corners, dtype=np.float64)
    previous = np.roll(points, 1, axis=0)
    following = np.roll(points, -1, axis=0)
    incoming = previous - points
    outgoing = following - points
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = np.einsum("ij,ij->i", incoming, outgoing)
    return np.abs(np.arctan2(cross, dot))


def polygon_area(points) -> float:
    array = np.asarray(points, dtype=np.float64)
    x = array[:, 0]
    y = array[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def quad_aspect_ratio(corners) -> float:
    """Longest over shortest side of the straight-sided quadrilateral."""
    points = np.asarray(corners, dtype=np.float64)
    sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    shortest = float(np.min(sides))
    if shortest <= 0.0:
        return math.inf
    return float(np.max(sides)) / shortest


# ---------------------------------------------------------------------------
# Normals, offsets and intersection tests
# ---------------------------------------------------------------------------


def segment_directions(poly: np.ndarray) -> np.ndarray:
    """Unit direction of every segment; a degenerate segment reuses the last."""
    delta = poly[1:] - poly[:-1]
    lengths = np.linalg.norm(delta, axis=1)
    safe = np.where(lengths > 0.0, lengths, 1.0)
    return delta / safe[:, None]


def vertex_normals(poly: np.ndarray, *, closed: bool) -> np.ndarray:
    """Left-hand unit normals at every vertex of a polyline.

    The normal is the normalised average of the two incident segment normals,
    so it bisects a corner instead of jumping across it.  ``closed`` treats the
    repeated last point as the first one.
    """
    directions = segment_directions(poly)
    normals = np.column_stack((-directions[:, 1], directions[:, 0]))
    if closed:
        after = normals
        before = np.roll(normals, 1, axis=0)
        averaged = before + after
        result = np.vstack([averaged, averaged[:1]])
    else:
        inner = normals[:-1] + normals[1:]
        result = np.vstack([normals[:1], inner, normals[-1:]])
    lengths = np.linalg.norm(result, axis=1)
    safe = np.where(lengths > 1.0e-12, lengths, 1.0)
    return result / safe[:, None]


def miter_scale(poly: np.ndarray, *, closed: bool, limit: float = 4.0) -> np.ndarray:
    """Offset length multiplier that keeps a corner offset at constant distance."""
    directions = segment_directions(poly)
    normals = np.column_stack((-directions[:, 1], directions[:, 0]))
    averaged = vertex_normals(poly, closed=closed)
    if closed:
        reference = np.vstack([np.roll(normals, 1, axis=0), normals[:1]])
    else:
        reference = np.vstack([normals[:1], normals[:-1], normals[-1:]])
    cosine = np.einsum("ij,ij->i", averaged, reference)
    cosine = np.where(np.abs(cosine) < 1.0 / limit, 1.0 / limit, cosine)
    return 1.0 / cosine


def offset_polyline(
    poly: np.ndarray, heights, *, closed: bool, limit: float = 4.0
) -> np.ndarray:
    """Offset a polyline to its left by a per-vertex height."""
    values = np.asarray(heights, dtype=np.float64)
    if values.ndim == 0:
        values = np.full(len(poly), float(values))
    normals = vertex_normals(poly, closed=closed)
    scale = miter_scale(poly, closed=closed, limit=limit)
    return poly + (values * scale)[:, None] * normals


def _segment_crossings(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Boolean matrix of proper crossings between two segment sets."""
    p = first[:-1]
    r = first[1:] - first[:-1]
    q = second[:-1]
    s = second[1:] - second[:-1]
    denominator = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    delta_x = q[None, :, 0] - p[:, None, 0]
    delta_y = q[None, :, 1] - p[:, None, 1]
    safe = np.where(np.abs(denominator) > 0.0, denominator, 1.0)
    t = (delta_x * s[None, :, 1] - delta_y * s[None, :, 0]) / safe
    u = (delta_x * r[:, None, 1] - delta_y * r[:, None, 0]) / safe
    proper = np.abs(denominator) > 0.0
    return proper & (t > 0.0) & (t < 1.0) & (u > 0.0) & (u < 1.0)


def paths_cross(first: np.ndarray, second: np.ndarray) -> bool:
    """True when two open polylines properly cross each other."""
    if len(first) < 2 or len(second) < 2:
        return False
    return bool(np.any(_segment_crossings(first, second)))


def self_intersections(poly: np.ndarray, *, closed: bool) -> list[tuple[int, int]]:
    """Indices of properly crossing non-adjacent segment pairs."""
    count = len(poly) - 1
    if count < 3:
        return []
    crossings = _segment_crossings(poly, poly)
    rows, columns = np.nonzero(np.triu(crossings, 1))
    pairs = []
    for row, column in zip(rows.tolist(), columns.tolist()):
        if column == row + 1:
            continue
        if closed and row == 0 and column == count - 1:
            continue
        pairs.append((row, column))
    return pairs


def is_simple(poly: np.ndarray, *, closed: bool) -> bool:
    return not self_intersections(poly, closed=closed)


def resample_by_metric(poly: np.ndarray, sizes, *, minimum: int = 1) -> np.ndarray:
    """Return arc-length stations whose spacing follows a per-vertex size."""
    cumulative = cumulative_length(poly)
    values = np.asarray(sizes, dtype=np.float64)
    density = 1.0 / np.where(values > 0.0, values, 1.0)
    average = 0.5 * (density[1:] + density[:-1])
    counted = np.concatenate(([0.0], np.cumsum(average * segment_lengths(poly))))
    total = float(counted[-1])
    parts = max(int(minimum), int(round(total)))
    targets = np.linspace(0.0, total, parts + 1)
    return np.interp(targets, counted, cumulative)


def metric_length(poly: np.ndarray, sizes) -> float:
    """Length of a polyline measured in a per-vertex size metric."""
    values = np.asarray(sizes, dtype=np.float64)
    density = 1.0 / np.where(values > 0.0, values, 1.0)
    average = 0.5 * (density[1:] + density[:-1])
    return float(np.sum(average * segment_lengths(poly)))


# ---------------------------------------------------------------------------
# Scale-aware segment and path relations
# ---------------------------------------------------------------------------
#
# ``paths_cross`` answers one question - is there a transversal intersection -
# and answers it with strict inequalities, so it is blind to the three ways two
# block edges can be illegally related without properly crossing: a collinear
# partial overlap, an endpoint sitting inside another edge, and a segment that
# has collapsed to a point.  A planar topology operation has to reject all of
# them, so the classifier below reports which relation holds instead of a
# boolean, and every threshold it uses is a length compared against a caller
# supplied tolerance derived from the domain or cavity scale.

DISJOINT = "disjoint"
TOUCH = "touch"
PROPER_CROSSING = "proper_crossing"
T_JUNCTION = "t_junction"
COLLINEAR_OVERLAP = "collinear_overlap"
DEGENERATE_SEGMENT = "degenerate_segment"
UNEXPECTED_TOUCH = "unexpected_touch"

CONFLICT_KINDS = (
    PROPER_CROSSING,
    T_JUNCTION,
    COLLINEAR_OVERLAP,
    UNEXPECTED_TOUCH,
)


def segment_relation(p0, p1, q0, q1, *, tolerance: float) -> dict:
    """Classify how two segments are related, to within ``tolerance``.

    The result is one of ``disjoint``, ``touch`` (they meet at an endpoint of
    both), ``proper_crossing``, ``t_junction`` (an endpoint of one lies inside
    the other) or ``collinear_overlap``.  ``tolerance`` is a length: two
    segments count as parallel when their supporting lines separate by less
    than it over the shorter of the two, which makes the classification
    invariant under translation and rotation and equivariant under a uniform
    scale that also scales ``tolerance``.
    """
    first = np.asarray(p0, dtype=np.float64)
    second = np.asarray(p1, dtype=np.float64)
    third = np.asarray(q0, dtype=np.float64)
    fourth = np.asarray(q1, dtype=np.float64)
    r = second - first
    s = fourth - third
    length_r = float(math.hypot(float(r[0]), float(r[1])))
    length_s = float(math.hypot(float(s[0]), float(s[1])))
    if length_r <= tolerance or length_s <= tolerance:
        return {"kind": DEGENERATE_SEGMENT, "point": None, "parameters": None}
    w = third - first
    cross = float(r[0] * s[1] - r[1] * s[0])
    # |cross| = |r| |s| sin(angle), so the supporting lines deviate by
    # |sin(angle)| * min(|r|, |s|) over the shorter segment.
    if abs(cross) * min(length_r, length_s) > tolerance * length_r * length_s:
        t = float(w[0] * s[1] - w[1] * s[0]) / cross
        u = float(w[0] * r[1] - w[1] * r[0]) / cross
        margin_t = tolerance / length_r
        margin_u = tolerance / length_s
        if (
            t < -margin_t
            or t > 1.0 + margin_t
            or u < -margin_u
            or u > 1.0 + margin_u
        ):
            return {"kind": DISJOINT, "point": None, "parameters": (t, u)}
        end_first = t <= margin_t or t >= 1.0 - margin_t
        end_second = u <= margin_u or u >= 1.0 - margin_u
        if end_first and end_second:
            kind = TOUCH
        elif end_first or end_second:
            kind = T_JUNCTION
        else:
            kind = PROPER_CROSSING
        point = first + t * r
        return {
            "kind": kind,
            "point": (float(point[0]), float(point[1])),
            "parameters": (t, u),
        }
    offset = abs(float(w[0] * r[1] - w[1] * r[0])) / length_r
    if offset > tolerance:
        return {"kind": DISJOINT, "point": None, "parameters": None}
    scale = length_r * length_r
    start = float(np.dot(w, r)) / scale
    finish = float(np.dot(fourth - first, r)) / scale
    low = max(0.0, min(start, finish))
    high = min(1.0, max(start, finish))
    middle = first + 0.5 * (low + high) * r
    point = (float(middle[0]), float(middle[1]))
    if (high - low) * length_r > tolerance:
        return {
            "kind": COLLINEAR_OVERLAP,
            "point": point,
            "parameters": (low, high),
        }
    if high >= low - tolerance / length_r:
        return {"kind": TOUCH, "point": point, "parameters": (low, high)}
    return {"kind": DISJOINT, "point": None, "parameters": None}


def _segment_boxes(poly: np.ndarray) -> np.ndarray:
    lower = np.minimum(poly[:-1], poly[1:])
    upper = np.maximum(poly[:-1], poly[1:])
    return np.column_stack((lower, upper))


def path_relations(
    first, second, *, tolerance: float, limit: int = 64
) -> list[dict]:
    """Every non-disjoint segment relation between two polylines."""
    one = np.asarray(first, dtype=np.float64)
    other = np.asarray(second, dtype=np.float64)
    if len(one) < 2 or len(other) < 2:
        return []
    boxes_one = _segment_boxes(one)
    boxes_other = _segment_boxes(other)
    overlap = (
        (boxes_one[:, None, 0] <= boxes_other[None, :, 2] + tolerance)
        & (boxes_other[None, :, 0] <= boxes_one[:, None, 2] + tolerance)
        & (boxes_one[:, None, 1] <= boxes_other[None, :, 3] + tolerance)
        & (boxes_other[None, :, 1] <= boxes_one[:, None, 3] + tolerance)
    )
    rows, columns = np.nonzero(overlap)
    found: list[dict] = []
    for row, column in zip(rows.tolist(), columns.tolist()):
        relation = segment_relation(
            one[row],
            one[row + 1],
            other[column],
            other[column + 1],
            tolerance=tolerance,
        )
        if relation["kind"] in (DISJOINT, DEGENERATE_SEGMENT):
            # A repeated point inside a polyline carries no geometry and cannot
            # cross anything; a whole edge that has collapsed is caught by the
            # edge-length check instead.
            continue
        found.append({**relation, "first_segment": row, "second_segment": column})
        if len(found) >= limit:
            break
    return found


def path_conflicts(
    first,
    second,
    *,
    tolerance: float,
    shared=(),
    limit: int = 64,
) -> list[dict]:
    """Relations between two polylines that a planar block topology forbids.

    Touching is legitimate only where the two paths are supposed to meet - the
    graph vertex they have in common.  Everything else, including a contact
    that lands on such a point but is a crossing or a T-junction rather than a
    clean meeting, is a conflict.
    """
    allowed = np.asarray(shared, dtype=np.float64).reshape(-1, 2)
    found = []
    for relation in path_relations(
        first, second, tolerance=tolerance, limit=limit
    ):
        if relation["kind"] == TOUCH:
            point = relation["point"]
            if point is None:
                continue
            if len(allowed) and float(
                np.min(np.linalg.norm(allowed - np.asarray(point), axis=1))
            ) <= tolerance:
                continue
            found.append({**relation, "kind": UNEXPECTED_TOUCH})
            continue
        found.append(relation)
    return found


def self_conflicts(poly, *, closed: bool, tolerance: float) -> list[dict]:
    """Forbidden relations between non-adjacent segments of one polyline."""
    points = np.asarray(poly, dtype=np.float64)
    count = len(points) - 1
    if count < 3:
        return []
    found = []
    for row in range(count):
        for column in range(row + 2, count):
            if closed and row == 0 and column == count - 1:
                continue
            relation = segment_relation(
                points[row],
                points[row + 1],
                points[column],
                points[column + 1],
                tolerance=tolerance,
            )
            if relation["kind"] in (DISJOINT, DEGENERATE_SEGMENT):
                continue
            found.append(
                {**relation, "first_segment": row, "second_segment": column}
            )
    return found


def path_separation(first, second) -> float:
    """Smallest distance between two polylines, measured both ways."""
    return minimum_separation(
        np.asarray(first, dtype=np.float64), np.asarray(second, dtype=np.float64)
    )
