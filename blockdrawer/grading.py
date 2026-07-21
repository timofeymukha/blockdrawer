"""Numerically stable conversions between BlockMesh grading representations."""

from __future__ import annotations

import math
import sys

from .domain import TopologyError


def _finite_expansion_ratio(logarithm: float) -> float:
    minimum = math.log(sys.float_info.min)
    maximum = math.log(sys.float_info.max)
    if not math.isfinite(logarithm) or not minimum <= logarithm <= maximum:
        raise TopologyError(
            "That grading is too extreme to represent as an OpenFOAM ratio"
        )
    ratio = math.exp(logarithm)
    if not math.isfinite(ratio) or ratio <= 0.0 \
            or not math.isfinite(1.0 / ratio):
        raise TopologyError(
            "That grading is too extreme to represent as an OpenFOAM ratio"
        )
    return ratio


def _log_geometric_sum(log_ratio: float, cells: int) -> float:
    """Return log(sum(exp(i*log_ratio), i=0..cells-1)) stably."""
    if log_ratio == 0.0:
        return math.log(cells)
    if log_ratio < 0.0:
        return math.log(-math.expm1(cells * log_ratio)) \
            - math.log(-math.expm1(log_ratio))

    def log_expm1(value: float) -> float:
        if value > 50.0:
            return value + math.log1p(-math.exp(-value))
        return math.log(math.expm1(value))

    return log_expm1(cells * log_ratio) - log_expm1(log_ratio)


def _cell_ratio_log_from_start_width(
    length: float, cells: int, start_width: float
) -> float:
    if not math.isfinite(start_width) or not 0.0 < start_width < length:
        raise TopologyError(
            "A start or end width must be positive and smaller than the "
            "edge length when there is more than one cell"
        )
    uniform_width = length / cells
    if math.isclose(
        start_width, uniform_width, rel_tol=1.0e-12, abs_tol=1.0e-15
    ):
        return 0.0

    target = math.log(length / start_width)
    low = -1.0
    while _log_geometric_sum(low, cells) > target:
        low *= 2.0
    high = 1.0
    while _log_geometric_sum(high, cells) < target:
        high *= 2.0
    for _ in range(120):
        middle = (low + high) / 2.0
        if _log_geometric_sum(middle, cells) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _grading_from_total_ratio(
    length: float, cells: int, total_ratio: float
) -> tuple[float, float, float]:
    """Return cell ratio, start width and end width."""
    if not math.isfinite(length) or length <= 0.0:
        raise TopologyError("An edge must have positive finite length")
    if not math.isfinite(total_ratio) or total_ratio <= 0.0 \
            or not math.isfinite(1.0 / total_ratio):
        raise TopologyError("The total expansion ratio must be positive and finite")
    if cells == 1:
        if total_ratio != 1.0:
            raise TopologyError("A one-cell edge can only use uniform grading")
        return 1.0, length, length

    log_cell_ratio = math.log(total_ratio) / (cells - 1)
    if abs(log_cell_ratio) <= 1.0e-14:
        width = length / cells
        return 1.0, width, width

    cell_ratio = math.exp(log_cell_ratio)
    if log_cell_ratio > 0.0:
        end_width = length * (
            -math.expm1(-log_cell_ratio)
        ) / (-math.expm1(-cells * log_cell_ratio))
        start_width = end_width / total_ratio
    else:
        start_width = length * math.expm1(log_cell_ratio) / math.expm1(
            cells * log_cell_ratio
        )
        end_width = start_width * total_ratio
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (cell_ratio, start_width, end_width)
    ):
        raise TopologyError(
            "That grading is too extreme to produce finite cell widths"
        )
    return cell_ratio, start_width, end_width
