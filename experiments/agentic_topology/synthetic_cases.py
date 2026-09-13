"""Synthetic multi-body geometries for the research self-tests.

The generators are deliberately parametric: every case can be permuted,
reversed, translated, rotated and uniformly scaled so that the invariance of
the topology construction can be measured rather than assumed.
"""

from __future__ import annotations

import math

import numpy as np


def ellipse(
    center, semi_major: float, semi_minor: float, angle: float = 0.0, count: int = 160
) -> np.ndarray:
    """Closed anticlockwise ellipse sampled uniformly in parameter."""
    theta = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    local = np.column_stack(
        (semi_major * np.cos(theta), semi_minor * np.sin(theta))
    )
    return _place(local, center, angle)


def circle(center, radius: float, count: int = 160) -> np.ndarray:
    return ellipse(center, radius, radius, 0.0, count)


def dimpled_disk(
    center,
    radius: float,
    *,
    depth: float = 0.6,
    width: float = 0.45,
    angle: float = 0.0,
    count: int = 220,
) -> np.ndarray:
    """A smooth but genuinely concave body: a disk with one deep cove."""
    theta = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    wrapped = np.mod(theta + math.pi, 2.0 * math.pi) - math.pi
    profile = radius * (1.0 - depth * np.exp(-((wrapped / width) ** 2)))
    local = np.column_stack((profile * np.cos(theta), profile * np.sin(theta)))
    return _place(local, center, angle)


def _place(local: np.ndarray, center, angle: float) -> np.ndarray:
    rotation = np.asarray(
        [
            [math.cos(angle), -math.sin(angle)],
            [math.sin(angle), math.cos(angle)],
        ]
    )
    rotated = np.einsum("ij,kj->ki", rotation, local)
    return rotated + np.asarray(center, dtype=np.float64)[None, :]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def single_ellipse():
    return ["body"], [ellipse((0.0, 0.0), 1.0, 0.35, math.radians(12.0), 180)]


def two_circles():
    return (
        ["small", "large"],
        [circle((-0.9, 0.15), 0.28, 140), circle((0.8, -0.1), 0.45, 160)],
    )


def three_rotated_ellipses():
    return (
        ["front", "middle", "rear"],
        [
            ellipse((-1.35, 0.20), 0.42, 0.16, math.radians(25.0), 150),
            ellipse((0.0, 0.0), 0.70, 0.20, math.radians(-8.0), 170),
            ellipse((1.15, -0.28), 0.36, 0.13, math.radians(-32.0), 150),
        ],
    )


def concave_and_convex():
    return (
        ["cove", "disk"],
        [
            dimpled_disk((-0.55, 0.0), 0.75, angle=0.0, count=240),
            circle((0.85, 0.05), 0.30, 150),
        ],
    )


def four_bodies():
    return (
        ["a", "b", "c", "d"],
        [
            circle((-1.0, 0.8), 0.30, 140),
            circle((1.0, 0.7), 0.22, 130),
            ellipse((0.9, -0.8), 0.40, 0.18, math.radians(40.0), 150),
            ellipse((-0.9, -0.75), 0.33, 0.22, math.radians(-15.0), 150),
        ],
    )


CASES = {
    "single_ellipse": single_ellipse,
    "two_circles": two_circles,
    "three_rotated_ellipses": three_rotated_ellipses,
    "concave_and_convex": concave_and_convex,
    "four_bodies": four_bodies,
}


# ---------------------------------------------------------------------------
# Equivalence transforms
# ---------------------------------------------------------------------------


def transform(loops, *, translate=(0.0, 0.0), rotate: float = 0.0, scale: float = 1.0):
    rotation = np.asarray(
        [
            [math.cos(rotate), -math.sin(rotate)],
            [math.sin(rotate), math.cos(rotate)],
        ]
    )
    offset = np.asarray(translate, dtype=np.float64)
    return [
        scale * np.einsum("ij,kj->ki", rotation, np.asarray(loop)) + offset[None, :]
        for loop in loops
    ]


def reverse(loops):
    """Reverse every point list while keeping its first point."""
    result = []
    for loop in loops:
        array = np.asarray(loop)
        result.append(np.vstack([array[:1], array[1:][::-1]]))
    return result


def permute(names, loops, order):
    return [names[index] for index in order], [loops[index] for index in order]
