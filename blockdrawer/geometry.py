"""Reference-geometry point-list import helpers."""

from __future__ import annotations

import math
from pathlib import Path


class GeometryImportError(ValueError):
    """Raised when a reference-curve point file is malformed."""


def parse_point_pairs(text: str) -> tuple[tuple[float, float], ...]:
    """Parse one whitespace- or comma-separated x/y pair per line."""
    points: list[tuple[float, float]] = []
    for line_number, original in enumerate(text.splitlines(), start=1):
        content = original.split("#", 1)[0].strip()
        if not content:
            continue
        fields = content.replace(",", " ").split()
        if len(fields) != 2:
            raise GeometryImportError(
                f"Line {line_number} must contain exactly one x/y pair"
            )
        try:
            x, y = (float(value) for value in fields)
        except ValueError as exc:
            raise GeometryImportError(
                f"Line {line_number} contains a non-numeric coordinate"
            ) from exc
        if not (math.isfinite(x) and math.isfinite(y)):
            raise GeometryImportError(
                f"Line {line_number} coordinates must be finite"
            )
        points.append((x, y))
    if len(points) < 2:
        raise GeometryImportError(
            "A geometry curve point file must contain at least two points"
        )
    return tuple(points)


def load_point_pairs(path: str | Path) -> tuple[tuple[float, float], ...]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GeometryImportError(f"Could not read {source}: {exc}") from exc
    return parse_point_pairs(text)
