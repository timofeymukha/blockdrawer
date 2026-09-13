"""An explicit planar fluid domain: oriented loops of named boundary chains.

This is the shared input of every topology producer in the prototype.  It
deliberately knows nothing about bodies, farfields, airfoils or channels: a
domain is one oriented outer loop, zero or more oriented hole loops, and a
partition of those loops into named, directed boundary chains that carry a role
and, for a translational cyclic pair, reciprocal partner metadata.

Orientation convention: **the fluid is on the left of every chain**.  The outer
loop is therefore anticlockwise and every hole loop is clockwise.  A boundary
representation that arrives reversed is normalised, not rejected.

Supplied point lists are kept verbatim.  Nothing here resamples, smooths or
rasterises the input geometry; raster fields elsewhere in the prototype decide
connectivity only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

import geometry2d as g2


class DomainError(ValueError):
    """Raised when a planar domain cannot be interpreted or is inconsistent."""


ROLES = ("wall", "inlet", "outlet", "symmetry", "cyclic", "farfield", "patch")

#: Role to OpenFOAM patch type.  Roles that carry no physics become ``patch``.
PATCH_TYPES = {
    "wall": "wall",
    "symmetry": "symmetry",
    "cyclic": "cyclic",
    "inlet": "patch",
    "outlet": "patch",
    "farfield": "patch",
    "patch": "patch",
}


@dataclass(frozen=True)
class Chain:
    """One directed, named part of a domain loop.

    ``points`` is the supplied polyline, kept exactly as given.  ``neighbour``
    names the reciprocal partner of a translational cyclic pair.
    """

    name: str
    role: str
    points: np.ndarray
    neighbour: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", g2.as_polyline(self.points))
        if self.role not in ROLES:
            raise DomainError(f"chain {self.name!r} has unknown role {self.role!r}")

    @property
    def start(self) -> np.ndarray:
        return self.points[0]

    @property
    def end(self) -> np.ndarray:
        return self.points[-1]

    @property
    def length(self) -> float:
        return g2.total_length(self.points)

    @property
    def is_wall(self) -> bool:
        return self.role == "wall"

    @property
    def patch_type(self) -> str:
        return PATCH_TYPES[self.role]

    def reversed(self) -> "Chain":
        return replace(self, points=self.points[::-1].copy())

    def transformed(self, matrix: np.ndarray, offset: np.ndarray) -> "Chain":
        moved = np.einsum("ij,kj->ki", matrix, self.points) + offset[None, :]
        return replace(self, points=moved)

    def point_at(self, station: float) -> np.ndarray:
        return g2.sample_at_arclength(self.points, [float(station)])[0]

    def section(self, start: float, end: float) -> np.ndarray:
        return g2.polyline_section(self.points, start, end)

    def station_of(self, point) -> float:
        return float(g2.closest_on_polyline(self.points, [point]).arclength[0])


@dataclass(frozen=True)
class Loop:
    """A closed cycle of chains bounding the fluid on its left."""

    chains: tuple[Chain, ...]
    hole: bool = False

    def points(self) -> np.ndarray:
        pieces = [chain.points[:-1] for chain in self.chains]
        return np.vstack([*pieces, self.chains[0].points[:1]])

    def chain_offsets(self) -> tuple[float, ...]:
        offsets = []
        total = 0.0
        for chain in self.chains:
            offsets.append(total)
            total += chain.length
        return tuple(offsets)

    @property
    def length(self) -> float:
        return float(sum(chain.length for chain in self.chains))

    def reversed(self) -> "Loop":
        return Loop(
            tuple(chain.reversed() for chain in reversed(self.chains)), self.hole
        )

    def transformed(self, matrix, offset) -> "Loop":
        return Loop(
            tuple(chain.transformed(matrix, offset) for chain in self.chains),
            self.hole,
        )


@dataclass
class PlanarDomain:
    """One outer loop, zero or more hole loops and their cyclic pairings."""

    name: str
    loops: tuple[Loop, ...]
    periodic: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    guides: dict[str, np.ndarray] = field(default_factory=dict)

    # -- structure ---------------------------------------------------------

    @property
    def outer(self) -> Loop:
        return self.loops[0]

    @property
    def holes(self) -> tuple[Loop, ...]:
        return self.loops[1:]

    @property
    def hole_count(self) -> int:
        return len(self.loops) - 1

    @property
    def euler_characteristic(self) -> int:
        """``1 - holes``: a disk is 1, an annulus 0, a three-hole domain -2."""
        return 1 - self.hole_count

    @property
    def simply_connected(self) -> bool:
        return self.hole_count == 0

    def chains(self) -> tuple[Chain, ...]:
        return tuple(chain for loop in self.loops for chain in loop.chains)

    def chain(self, name: str) -> Chain:
        for candidate in self.chains():
            if candidate.name == name:
                return candidate
        raise DomainError(f"unknown boundary chain {name!r}")

    def loop_of(self, name: str) -> Loop:
        for loop in self.loops:
            if any(chain.name == name for chain in loop.chains):
                return loop
        raise DomainError(f"unknown boundary chain {name!r}")

    def wall_chains(self) -> tuple[Chain, ...]:
        return tuple(chain for chain in self.chains() if chain.is_wall)

    def periodic_partners(self) -> tuple[tuple[Chain, Chain, np.ndarray], ...]:
        pairs = []
        seen: set[str] = set()
        for chain in self.chains():
            if chain.role != "cyclic" or chain.name in seen or not chain.neighbour:
                continue
            partner = self.chain(chain.neighbour)
            seen.update({chain.name, partner.name})
            pairs.append((chain, partner, self.translation(chain.name)))
        return tuple(pairs)

    def translation(self, name: str) -> np.ndarray:
        """Vector that maps ``name`` onto its reciprocal cyclic partner.

        Translations are stored once per pair, keyed by the two chain names in
        sorted order and directed from the first to the second, so the stored
        value cannot disagree with itself.
        """
        chain = self.chain(name)
        if chain.neighbour is None:
            raise DomainError(f"chain {name!r} has no cyclic partner")
        key = periodic_key(chain.name, chain.neighbour)
        vector = self.periodic.get(key)
        if vector is None:
            forward = g2.centroid_by_arclength(
                self.chain(key[1]).points
            ) - g2.centroid_by_arclength(self.chain(key[0]).points)
            vector = forward
        vector = np.asarray(vector, dtype=np.float64)
        return vector if name == key[0] else -vector

    # -- frame and containment --------------------------------------------

    def frame(self) -> tuple[np.ndarray, float]:
        """Arc-length weighted centre and the largest distance to it."""
        weights = []
        centres = []
        for loop in self.loops:
            points = loop.points()
            weights.append(g2.total_length(points))
            centres.append(g2.centroid_by_arclength(points))
        total = float(sum(weights))
        if total <= 0.0:
            raise DomainError("the domain boundary has zero length")
        center = np.sum(
            np.asarray(centres) * np.asarray(weights)[:, None], axis=0
        ) / total
        radius = max(
            float(np.max(np.linalg.norm(loop.points() - center, axis=1)))
            for loop in self.loops
        )
        if radius <= 0.0:
            raise DomainError("the domain is geometrically degenerate")
        return center, radius

    @property
    def scale(self) -> float:
        return 2.0 * self.frame()[1]

    def bounds(self) -> tuple[float, float, float, float]:
        points = np.vstack([loop.points() for loop in self.loops])
        return (
            float(np.min(points[:, 0])),
            float(np.min(points[:, 1])),
            float(np.max(points[:, 0])),
            float(np.max(points[:, 1])),
        )

    def contains(self, queries) -> np.ndarray:
        """True for query points strictly inside the fluid region."""
        points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
        inside = g2.points_in_loop(self.outer.points(), points)
        for loop in self.holes:
            inside &= ~g2.points_in_loop(loop.points(), points)
        return inside

    def clearance(self, queries) -> np.ndarray:
        """Distance from every query point to the nearest boundary."""
        points = np.atleast_2d(np.asarray(queries, dtype=np.float64))
        best = np.full(len(points), math.inf)
        for loop in self.loops:
            best = np.minimum(best, g2.distance_to_polyline(loop.points(), points))
        return best

    # -- transforms --------------------------------------------------------

    def transformed(self, *, rotate=0.0, translate=(0.0, 0.0), scale=1.0):
        matrix = scale * np.asarray(
            [
                [math.cos(rotate), -math.sin(rotate)],
                [math.sin(rotate), math.cos(rotate)],
            ]
        )
        offset = np.asarray(translate, dtype=np.float64)
        periodic = {
            key: matrix @ np.asarray(value, dtype=np.float64)
            for key, value in self.periodic.items()
        }
        guides = {
            key: np.einsum("ij,kj->ki", matrix, np.asarray(value)) + offset[None, :]
            for key, value in self.guides.items()
        }
        return PlanarDomain(
            self.name,
            tuple(loop.transformed(matrix, offset) for loop in self.loops),
            periodic,
            guides,
        )

    def reversed(self) -> "PlanarDomain":
        """Reverse the complete boundary representation."""
        periodic = {key: -value for key, value in self.periodic.items()}
        return PlanarDomain(
            self.name,
            tuple(loop.reversed() for loop in self.loops),
            periodic,
            dict(self.guides),
        )

    # -- normalisation and identity ---------------------------------------

    def normalised(self) -> "PlanarDomain":
        """Deterministic representation, independent of how it was supplied.

        Loop winding is forced to fluid-on-left, hole loops are ordered by a
        dimensionless rotation-invariant geometric key, and every loop starts at
        the chain that key selects.  Translation, rotation and uniform scaling
        of the input therefore give the same structure, and so does reversing
        the whole boundary representation.
        """
        center, radius = self.frame()
        loops = []
        for index, loop in enumerate(self.loops):
            area = g2.signed_area(loop.points())
            wanted = -1.0 if loop.hole else 1.0
            fixed = loop.reversed() if area * wanted < 0.0 else loop
            loops.append(_rotate_chains(fixed))
        outer = [loop for loop in loops if not loop.hole]
        holes = [loop for loop in loops if loop.hole]
        if len(outer) != 1:
            raise DomainError("a planar domain needs exactly one outer loop")
        holes.sort(key=lambda loop: _loop_key(loop, center, radius))
        periodic = {}
        for key, value in self.periodic.items():
            canonical = periodic_key(*key)
            vector = np.asarray(value, dtype=np.float64)
            periodic[canonical] = vector if canonical == tuple(key) else -vector
        result = PlanarDomain(
            self.name, tuple([outer[0], *holes]), periodic, dict(self.guides)
        )
        # Reversing a representation keeps the chain names, so the stored
        # direction is re-derived from the geometry instead of trusted.
        for first, second, _vector in result.periodic_partners():
            key = periodic_key(first.name, second.name)
            stored = result.periodic.get(key)
            if stored is None:
                continue
            derived = g2.centroid_by_arclength(
                result.chain(key[1]).points
            ) - g2.centroid_by_arclength(result.chain(key[0]).points)
            if float(np.dot(stored, derived)) < 0.0:
                result.periodic[key] = -stored
        return result

    def signature(self) -> dict:
        """A structural fingerprint that survives rigid motions and scaling."""
        _center, radius = self.frame()
        loops = []
        for loop in self.loops:
            loops.append(
                {
                    "hole": loop.hole,
                    "chains": [
                        {
                            "name": chain.name,
                            "role": chain.role,
                            "neighbour": chain.neighbour,
                            "relative_length": round(chain.length / radius, 9),
                            "points": len(chain.points),
                        }
                        for chain in loop.chains
                    ],
                }
            )
        return {
            "loops": loops,
            "holes": self.hole_count,
            "euler_characteristic": self.euler_characteristic,
            "periodic_pairs": sorted(
                sorted((first.name, second.name))
                for first, second, _vector in self.periodic_partners()
            ),
        }

    # -- validation --------------------------------------------------------

    def problems(self) -> list[dict]:
        """Structured validation findings; an empty list means the domain is usable."""
        found: list[dict] = []
        names: dict[str, int] = {}
        for chain in self.chains():
            names[chain.name] = names.get(chain.name, 0) + 1
        for name, count in sorted(names.items()):
            if count > 1:
                found.append(
                    {"kind": "duplicate_chain_name", "chain": name, "count": count}
                )
        try:
            _center, radius = self.frame()
        except DomainError as error:
            return [*found, {"kind": "degenerate", "detail": str(error)}]
        scale = 2.0 * radius
        join = 1.0e-7 * scale
        coincide = 1.0e-12 * scale

        if sum(0 if loop.hole else 1 for loop in self.loops) != 1:
            found.append({"kind": "outer_loop_count", "loops": len(self.loops)})

        for loop in self.loops:
            label = [chain.name for chain in loop.chains]
            for chain in loop.chains:
                gaps = g2.segment_lengths(chain.points)
                if len(gaps) and float(np.min(gaps)) <= coincide:
                    found.append(
                        {
                            "kind": "coincident_points",
                            "chain": chain.name,
                            "minimum_segment": float(np.min(gaps)),
                        }
                    )
                if chain.length <= coincide:
                    found.append({"kind": "empty_chain", "chain": chain.name})
            for first, second in zip(loop.chains, loop.chains[1:] + loop.chains[:1]):
                gap = float(np.linalg.norm(first.end - second.start))
                if gap > join:
                    found.append(
                        {
                            "kind": "disconnected_chains",
                            "chains": [first.name, second.name],
                            "gap": gap,
                        }
                    )
            points = loop.points()
            area = g2.signed_area(points)
            wanted = -1.0 if loop.hole else 1.0
            if area * wanted <= 0.0:
                found.append(
                    {"kind": "loop_orientation", "chains": label, "signed_area": area}
                )
            crossings = g2.self_intersections(points, closed=True)
            if crossings:
                found.append(
                    {
                        "kind": "self_intersecting_loop",
                        "chains": label,
                        "segment_pairs": crossings[:8],
                    }
                )

        outer_points = self.outer.points()
        for loop in self.holes:
            points = loop.points()
            if not bool(np.all(g2.points_in_loop(outer_points, points[:-1]))):
                found.append(
                    {
                        "kind": "hole_not_contained",
                        "chains": [chain.name for chain in loop.chains],
                    }
                )
            if g2.paths_cross(points, outer_points):
                found.append(
                    {
                        "kind": "hole_crosses_outer",
                        "chains": [chain.name for chain in loop.chains],
                    }
                )
        for first in range(len(self.holes)):
            for second in range(first + 1, len(self.holes)):
                one = self.holes[first].points()
                other = self.holes[second].points()
                if g2.paths_cross(one, other) or bool(
                    np.any(g2.points_in_loop(one, other[:-1]))
                ):
                    found.append(
                        {
                            "kind": "holes_overlap",
                            "chains": [
                                self.holes[first].chains[0].name,
                                self.holes[second].chains[0].name,
                            ],
                        }
                    )

        found.extend(self._periodic_problems(scale))
        return found

    def _periodic_problems(self, scale: float) -> list[dict]:
        found: list[dict] = []
        for chain in self.chains():
            if chain.role != "cyclic":
                if chain.neighbour is not None:
                    found.append(
                        {"kind": "neighbour_without_cyclic_role", "chain": chain.name}
                    )
                continue
            if chain.neighbour is None:
                found.append({"kind": "cyclic_without_neighbour", "chain": chain.name})
                continue
            try:
                partner = self.chain(chain.neighbour)
            except DomainError:
                found.append(
                    {
                        "kind": "unknown_cyclic_neighbour",
                        "chain": chain.name,
                        "neighbour": chain.neighbour,
                    }
                )
                continue
            if partner.neighbour != chain.name:
                found.append(
                    {
                        "kind": "cyclic_not_reciprocal",
                        "chains": [chain.name, partner.name],
                    }
                )
                continue
            if chain.name > partner.name:
                continue
            vector = self.translation(chain.name)
            moved = chain.points[::-1] + vector[None, :]
            deviation = float(
                np.max(g2.closest_on_polyline(partner.points, moved).distance)
            )
            back = float(
                np.max(
                    g2.closest_on_polyline(
                        moved, partner.points
                    ).distance
                )
            )
            error = max(deviation, back) / scale
            if error > 1.0e-7:
                found.append(
                    {
                        "kind": "cyclic_geometry_mismatch",
                        "chains": [chain.name, partner.name],
                        "relative_deviation": error,
                        "translation": [float(vector[0]), float(vector[1])],
                    }
                )
            ratio = partner.length / chain.length if chain.length > 0.0 else math.inf
            if abs(ratio - 1.0) > 1.0e-7:
                found.append(
                    {
                        "kind": "cyclic_length_mismatch",
                        "chains": [chain.name, partner.name],
                        "length_ratio": ratio,
                    }
                )
        return found

    def require_valid(self) -> "PlanarDomain":
        found = self.problems()
        if found:
            raise DomainError(f"invalid planar domain: {found}")
        return self


def periodic_key(first: str, second: str) -> tuple[str, str]:
    """Canonical key of a cyclic pair: the two chain names in sorted order."""
    return (first, second) if first <= second else (second, first)


def _rotate_chains(loop: Loop) -> Loop:
    """Start a loop at a deterministic chain without looking at coordinates."""
    keys = [(chain.name, index) for index, chain in enumerate(loop.chains)]
    _name, start = min(keys)
    order = loop.chains[start:] + loop.chains[:start]
    return Loop(order, loop.hole)


def _loop_key(loop: Loop, center: np.ndarray, radius: float) -> tuple:
    points = loop.points()
    perimeter = g2.total_length(points) / radius
    area = abs(g2.signed_area(points)) / (radius * radius)
    centre = g2.centroid_by_arclength(points)
    offset = float(np.linalg.norm(centre - center)) / radius
    spread = float(
        np.mean(np.linalg.norm(points[:-1] - centre[None, :], axis=1))
    ) / radius
    return (
        -round(perimeter, 9),
        -round(area, 9),
        round(offset, 9),
        round(spread, 9),
        tuple(sorted(chain.name for chain in loop.chains)),
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def from_chains(
    name: str,
    outer,
    holes=(),
    *,
    periodic=None,
    guides=None,
) -> PlanarDomain:
    """Build a domain from chain sequences and normalise its representation."""
    loops = [Loop(tuple(outer), False)]
    for hole in holes:
        loops.append(Loop(tuple(hole), True))
    domain = PlanarDomain(
        name,
        tuple(loops),
        {} if periodic is None else dict(periodic),
        {} if guides is None else dict(guides),
    )
    return domain.normalised()


def from_internal_case(case, *, name: str | None = None) -> PlanarDomain:
    """Adapt a ``synthetic_cases.InternalFlowCase`` to a planar domain."""
    chains = []
    for boundary in case.boundaries:
        role = boundary.kind
        chains.append(
            Chain(boundary.name, role, boundary.points, boundary.neighbour)
        )
    periodic = {}
    if case.periodic_translation is not None:
        vector = np.asarray(case.periodic_translation, dtype=np.float64)
        for chain in chains:
            if chain.role != "cyclic" or chain.neighbour is None:
                continue
            key = periodic_key(chain.name, chain.neighbour)
            if key in periodic:
                continue
            lookup = {item.name: item for item in chains}
            direction = g2.centroid_by_arclength(
                lookup[key[1]].points
            ) - g2.centroid_by_arclength(lookup[key[0]].points)
            periodic[key] = (
                vector if float(np.dot(direction, vector)) >= 0.0 else -vector
            )
    return from_chains(name or case.name, chains, periodic=periodic)


def from_bodies(
    names,
    loops,
    *,
    farfield_shape: str = "circle",
    farfield_scale: float = 3.0,
    farfield_name: str = "farfield",
    name: str = "external",
) -> PlanarDomain:
    """Adapt the external-flow ``--curve NAME=PATH`` input to a planar domain.

    Bodies become hole loops in a canonical geometric order, so a permuted
    input yields the same domain.  The trial outer boundary is generated here
    and is the only fabricated geometry; an internal domain never gets one.
    """
    import sites as site_module

    center, radius = site_module.domain_frame(loops)
    order = site_module.canonical_order(loops, center, radius)
    holes = []
    for index in order:
        closed = g2.close_loop(loops[index], 0.0)
        # Fluid on the left of a hole means clockwise winding.
        if g2.signed_area(closed) > 0.0:
            closed = closed[::-1].copy()
        holes.append([Chain(names[index], "wall", closed)])
    outer_radius = radius * float(farfield_scale)
    if farfield_shape == "circle":
        angles = np.linspace(0.0, 2.0 * math.pi, 721)
        points = center[None, :] + outer_radius * np.column_stack(
            (np.cos(angles), np.sin(angles))
        )
        points[-1] = points[0]
    elif farfield_shape == "rectangle":
        half = outer_radius
        points = np.asarray(
            [
                [center[0] - half, center[1] - half],
                [center[0] + half, center[1] - half],
                [center[0] + half, center[1] + half],
                [center[0] - half, center[1] + half],
                [center[0] - half, center[1] - half],
            ]
        )
    else:
        raise DomainError(f"unknown farfield shape {farfield_shape!r}")
    outer = [Chain(farfield_name, "farfield", points)]
    return from_chains(name, outer, holes)
