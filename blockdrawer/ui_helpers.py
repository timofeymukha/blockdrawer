"""Shared constants and pure helpers for the Tk user interface."""

from __future__ import annotations

import math

from .model import EdgeKey, MeshModel, edge_key


MAX_VISIBLE_EDGE_MARKERS = 500
MAX_VISIBLE_CONTROL_POINTS = 250
CURVE_RENDER_SEGMENTS = 64
SPLINE_SAMPLES_PER_SPAN = 4
GEOMETRY_SAMPLES_PER_SPAN = 4
MAX_ZOOM_PIXELS_PER_UNIT = 10_000_000.0
MIN_SPLIT_FRACTION = 1.0e-4
MAX_SPLIT_PICK_SAMPLES = 4096
PROJECTION_DIRECTION_LABELS = {
    "Orthogonal (shortest path)": "orthogonal",
    "Along x": "x",
    "Along y": "y",
}


def nearest_edge_fraction(
    model: MeshModel, edge: EdgeKey, x: float, y: float
) -> float:
    """Return the edge parameter closest to a 2D pointer location."""
    current = edge_key(*edge)
    if model.edge_type(current) == "line":
        first = model.vertices[current[0]]
        second = model.vertices[current[1]]
        dx = second.x - first.x
        dy = second.y - first.y
        length_squared = dx * dx + dy * dy
        if length_squared == 0.0:
            return 0.5
        return min(1.0, max(
            0.0,
            ((x - first.x) * dx + (y - first.y) * dy) / length_squared,
        ))

    point_count = len(model.edge_control_points(current))
    samples = min(
        MAX_SPLIT_PICK_SAMPLES,
        max(64, (point_count + 1) * 16),
    )

    def distance_squared(fraction: float) -> float:
        point_x, point_y = model.edge_point(current, fraction)
        return (point_x - x) ** 2 + (point_y - y) ** 2

    best_index = min(
        range(samples + 1),
        key=lambda index: distance_squared(index / samples),
    )
    low = max(0.0, (best_index - 1) / samples)
    high = min(1.0, (best_index + 1) / samples)
    for _ in range(36):
        first_third = low + (high - low) / 3.0
        second_third = high - (high - low) / 3.0
        if distance_squared(first_third) <= distance_squared(second_third):
            high = second_third
        else:
            low = first_third
    return (low + high) / 2.0


def split_fraction_from_text(value: str) -> float:
    """Parse a user-facing percentage into an edge fraction."""
    text = value.strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        percentage = float(text)
    except ValueError as exc:
        raise ValueError("Current split must be a percentage between 0 and 100") \
            from exc
    if not math.isfinite(percentage) or not 0.0 < percentage < 100.0:
        raise ValueError("Current split must be strictly between 0 and 100 percent")
    return percentage / 100.0


def display_split_percentage(fraction: float) -> str:
    return format(fraction * 100.0, ".12g")


def positive_integer(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if result < 1 or str(result) != value.strip():
        raise ValueError(f"{label} must be a positive integer")
    return result


def display_number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(value, ".8g")


def visible_control_point_indices(
    count: int,
    selected_index: int | None,
) -> tuple[int, ...]:
    """Bound dense canvas markers while retaining the selected point."""
    if count <= 0:
        return ()
    stride = max(1, math.ceil(count / MAX_VISIBLE_CONTROL_POINTS))
    indices = set(range(0, count, stride))
    indices.add(count - 1)
    if selected_index is not None and 0 <= selected_index < count:
        indices.add(selected_index)
    return tuple(sorted(indices))


def display_grading_number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(value, ".12g")


def nice_grid_step(raw_step: float) -> float:
    exponent = math.floor(math.log10(max(raw_step, 1.0e-12)))
    fraction = raw_step / (10.0 ** exponent)
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10.0 ** exponent)


def system_display_scale(tk_scaling: float, platform: str) -> float:
    """Return the logical UI scale implied by Tk and the platform."""
    if platform == "darwin":
        return 1.0
    baseline = 96.0 / 72.0
    return max(1.0, min(4.0, tk_scaling / baseline))


def scaled_named_font_size(
    base_size: int, system_scale: float, manual_multiplier: float
) -> int:
    if base_size < 0:
        return -max(
            1,
            int(round(abs(base_size) * system_scale * manual_multiplier)),
        )
    return max(1, int(round(base_size * manual_multiplier)))


def is_text_input_class(widget_class: str) -> bool:
    return widget_class in {
        "Entry", "TEntry", "Text", "Spinbox", "TSpinbox",
    }
