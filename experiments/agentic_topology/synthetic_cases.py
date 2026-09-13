"""Synthetic multi-body geometries for the research self-tests.

The generators are deliberately parametric: every case can be permuted,
reversed, translated, rotated and uniformly scaled so that the invariance of
the topology construction can be measured rather than assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BoundaryChain:
    """One directed part of an internal-flow domain boundary."""

    name: str
    kind: str
    points: np.ndarray
    neighbour: str | None = None


@dataclass(frozen=True)
class InternalFlowCase:
    """A simply connected fluid domain with named boundary chains."""

    name: str
    boundaries: tuple[BoundaryChain, ...]
    periodic_translation: tuple[float, float] | None = None

    def boundary(self, name: str) -> BoundaryChain:
        return next(boundary for boundary in self.boundaries if boundary.name == name)

    def loop(self) -> np.ndarray:
        """Return the connected, anticlockwise closed domain boundary."""
        pieces = [boundary.points[:-1] for boundary in self.boundaries]
        return np.vstack([*pieces, self.boundaries[0].points[:1]])


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


def teardrop(
    center,
    radius: float,
    *,
    tip_ratio: float = 2.0,
    angle: float = 0.0,
    count: int = 160,
) -> np.ndarray:
    """A smooth body closed by a sharp convex tip.

    The two straight flanks are tangent to the circle, so the included angle at
    the tip is ``2 * asin(1 / tip_ratio)``: the fluid sector there is wider than
    300 degrees for ``tip_ratio >= 2``.
    """
    if tip_ratio <= 1.0:
        raise ValueError("the tip must lie outside the circle")
    contact = math.acos(1.0 / tip_ratio)
    theta = np.linspace(contact, 2.0 * math.pi - contact, count)
    arc = radius * np.column_stack((np.cos(theta), np.sin(theta)))
    tip = np.asarray([[tip_ratio * radius, 0.0]])
    local = np.vstack([arc, tip])
    return _place(local, center, angle)


def sharp_bodies():
    return (
        ["tip", "disk"],
        [
            teardrop((-0.75, 0.0), 0.32, tip_ratio=2.2, angle=math.radians(8.0)),
            circle((0.95, 0.05), 0.34, 150),
        ],
    )


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


# ---------------------------------------------------------------------------
# Internal-flow acceptance geometry
# ---------------------------------------------------------------------------


def periodic_hill_height(x):
    """Classical periodic-hill lower-wall height for ``H = 1``.

    This is the six-piece Almeida baseline polynomial reproduced in Appendix A
    of Gehrke & Rung, IJNMF 94 (2022), DOI 10.1002/fld.5085.  The hill is
    mirrored about the middle of its streamwise period, ``Lx/H = 9``.
    """
    original = np.asarray(x, dtype=np.float64)
    periodic = np.mod(original, 9.0)
    # Rounding keeps mirrored points on the same side of the published decimal
    # breakpoints (for example 0.321 versus 9 - 0.321).
    distance = np.round(np.minimum(periodic, 9.0 - periodic), 12)
    height = np.zeros_like(distance)

    pieces = (
        (
            distance <= 0.321,
            lambda value: np.minimum(
                1.0, 1.000 + 0.190 * value**2 - 1.666 * value**3
            ),
        ),
        (
            (distance > 0.321) & (distance <= 0.500),
            lambda value: 0.896
            + 0.975 * value
            - 2.845 * value**2
            + 1.482 * value**3,
        ),
        (
            (distance > 0.500) & (distance <= 0.714),
            lambda value: 0.921
            + 0.821 * value
            - 2.536 * value**2
            + 1.275 * value**3,
        ),
        (
            (distance > 0.714) & (distance <= 1.071),
            lambda value: 1.445
            - 1.380 * value
            + 0.545 * value**2
            - 0.162 * value**3,
        ),
        (
            (distance > 1.071) & (distance <= 1.429),
            lambda value: 0.640
            + 0.874 * value
            - 1.559 * value**2
            + 0.492 * value**3,
        ),
        (
            (distance > 1.429) & (distance <= 1.929),
            lambda value: np.maximum(
                0.0,
                2.014
                - 2.011 * value
                + 0.461 * value**2
                + 0.021 * value**3,
            ),
        ),
    )
    for mask, polynomial in pieces:
        height[mask] = polynomial(distance[mask])
    if original.ndim == 0:
        return float(height)
    return height


def periodic_hill(samples: int = 361) -> InternalFlowCase:
    """Classical simply connected periodic-hill channel.

    Coordinates are normalized by hill height ``H``.  The streamwise period is
    ``9 H`` and the flat upper wall is at ``3.036 H``.  Boundary directions
    form one anticlockwise loop; the two cyclic chains therefore have opposite
    path directions and match after reversing one and translating by ``9 H``.
    """
    if samples < 17:
        raise ValueError("periodic hill needs at least 17 streamwise samples")
    half_features = np.asarray([0.0, 0.321, 0.500, 0.714, 1.071, 1.429, 1.929])
    features = np.concatenate((half_features, 9.0 - half_features[::-1]))
    x = np.unique(np.concatenate((np.linspace(0.0, 9.0, samples), features)))
    bottom = np.column_stack((x, periodic_hill_height(x)))

    side_samples = max(9, samples // 12)
    right_y = np.linspace(float(bottom[-1, 1]), 3.036, side_samples)
    right = np.column_stack((np.full_like(right_y, 9.0), right_y))
    top = np.column_stack((x[::-1], np.full_like(x, 3.036)))
    left_y = np.linspace(3.036, float(bottom[0, 1]), side_samples)
    left = np.column_stack((np.zeros_like(left_y), left_y))

    return InternalFlowCase(
        "periodic_hill",
        (
            BoundaryChain("bottom_wall", "wall", bottom),
            BoundaryChain("periodic_right", "cyclic", right, "periodic_left"),
            BoundaryChain("top_wall", "wall", top),
            BoundaryChain("periodic_left", "cyclic", left, "periodic_right"),
        ),
        periodic_translation=(9.0, 0.0),
    )


def straight_channel(
    length: float = 6.0, height: float = 1.0, samples: int = 61
) -> InternalFlowCase:
    """A rectangular channel with an inlet and an outlet.

    The simplest four-sided internal domain: two wall guides and two non-wall
    ends that are compatible but not periodic.
    """
    x = np.linspace(0.0, length, samples)
    y = np.linspace(0.0, height, max(9, samples // 6))
    bottom = np.column_stack((x, np.zeros_like(x)))
    outlet = np.column_stack((np.full_like(y, length), y))
    top = np.column_stack((x[::-1], np.full_like(x, height)))
    inlet = np.column_stack((np.zeros_like(y), y[::-1]))
    return InternalFlowCase(
        "straight_channel",
        (
            BoundaryChain("bottom_wall", "wall", bottom),
            BoundaryChain("outlet", "outlet", outlet),
            BoundaryChain("top_wall", "wall", top),
            BoundaryChain("inlet", "inlet", inlet),
        ),
    )


def transform_case(
    case: InternalFlowCase,
    *,
    translate=(0.0, 0.0),
    rotate: float = 0.0,
    scale: float = 1.0,
) -> InternalFlowCase:
    """Rigidly move and uniformly scale a complete internal-flow case."""
    rotation = np.asarray(
        [
            [math.cos(rotate), -math.sin(rotate)],
            [math.sin(rotate), math.cos(rotate)],
        ]
    )
    offset = np.asarray(translate, dtype=np.float64)
    boundaries = tuple(
        BoundaryChain(
            chain.name,
            chain.kind,
            scale * np.einsum("ij,kj->ki", rotation, chain.points) + offset[None, :],
            chain.neighbour,
        )
        for chain in case.boundaries
    )
    translation = case.periodic_translation
    if translation is not None:
        moved = scale * (rotation @ np.asarray(translation, dtype=np.float64))
        translation = (float(moved[0]), float(moved[1]))
    return InternalFlowCase(case.name, boundaries, translation)


def reverse_case(case: InternalFlowCase) -> InternalFlowCase:
    """Reverse the complete boundary representation of an internal-flow case."""
    boundaries = tuple(
        BoundaryChain(
            chain.name, chain.kind, chain.points[::-1].copy(), chain.neighbour
        )
        for chain in reversed(case.boundaries)
    )
    translation = case.periodic_translation
    if translation is not None:
        translation = (-translation[0], -translation[1])
    return InternalFlowCase(case.name, boundaries, translation)


INTERNAL_CASES = {
    "periodic_hill": periodic_hill,
    "straight_channel": straight_channel,
}


CASES = {
    "single_ellipse": single_ellipse,
    "two_circles": two_circles,
    "three_rotated_ellipses": three_rotated_ellipses,
    "concave_and_convex": concave_and_convex,
    "four_bodies": four_bodies,
    "sharp_bodies": sharp_bodies,
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
