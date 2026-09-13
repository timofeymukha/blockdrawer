"""Reading BlockDrawer-style ``x y`` point lists for the research prototype."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


def read_point_list(path: Path) -> np.ndarray:
    points: list[tuple[float, float]] = []
    text = Path(path).read_text(encoding="utf-8")
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        stripped = raw_line.split("#", 1)[0].strip().replace(",", " ")
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) != 2:
            raise ValueError(f"{path}:{line_number}: expected exactly two coordinates")
        try:
            point = (float(fields[0]), float(fields[1]))
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
        if not all(math.isfinite(value) for value in point):
            raise ValueError(f"{path}:{line_number}: coordinates must be finite")
        points.append(point)
    if len(points) < 3:
        raise ValueError(f"{path}: a closed body needs at least three points")
    return np.asarray(points, dtype=np.float64)


def parse_curve_argument(value: str) -> tuple[str, Path]:
    try:
        name, raw_path = value.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("curves must be written NAME=PATH") from error
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("curve name cannot be empty")
    return name, Path(raw_path).expanduser()
