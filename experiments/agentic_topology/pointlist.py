"""Reading BlockDrawer-style ``x y`` point lists for the research prototype."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


def read_point_list(path: Path, *, minimum: int = 3) -> np.ndarray:
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
    if len(points) < minimum:
        raise ValueError(f"{path}: a point list here needs at least {minimum} points")
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


def parse_outer_argument(value: str) -> tuple[str, str, Path]:
    """``NAME:ROLE=PATH`` for one chain of an explicit outer boundary."""
    try:
        head, raw_path = value.split("=", 1)
        name, role = head.split(":", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "outer chains must be written NAME:ROLE=PATH"
        ) from error
    name = name.strip()
    role = role.strip()
    if not name or not role:
        raise argparse.ArgumentTypeError("outer chain name and role cannot be empty")
    return name, role, Path(raw_path).expanduser()


def read_open_point_list(path: Path) -> np.ndarray:
    """An open polyline with at least two points (an outer boundary chain)."""
    points = read_point_list(path, minimum=2)
    return points


def parse_sides(text: str):
    """``bottom:farfield,outlet:outlet,top:farfield,inlet:inlet`` in order."""
    sides = []
    for item in text.split(","):
        name, _, role = item.strip().partition(":")
        if not name or not role:
            raise argparse.ArgumentTypeError(
                "farfield sides are four NAME:ROLE items, anticlockwise from "
                "the lower-left corner (bottom, right, top, left)"
            )
        sides.append((name.strip(), role.strip()))
    if len(sides) != 4:
        raise argparse.ArgumentTypeError("farfield sides need exactly four items")
    return tuple(sides)


def parse_box(text: str):
    """``XMIN,YMIN,XMAX,YMAX`` as floats."""
    try:
        values = tuple(float(item) for item in text.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("a farfield box is XMIN,YMIN,XMAX,YMAX") from error
    if len(values) != 4:
        raise argparse.ArgumentTypeError("a farfield box is XMIN,YMIN,XMAX,YMAX")
    return values


def parse_direction(text: str):
    """``DX,DY`` as a non-zero direction."""
    try:
        values = tuple(float(item) for item in text.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("a direction is DX,DY") from error
    if len(values) != 2 or not all(math.isfinite(v) for v in values) or values == (0.0, 0.0):
        raise argparse.ArgumentTypeError("a direction is a finite non-zero DX,DY")
    return values
