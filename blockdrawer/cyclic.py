"""Face-by-face correspondence of reciprocal cyclic patch pairs.

OpenFOAM matches the faces of a ``cyclic`` pair by position in the two face
lists: face ``i`` of one patch is coupled to face ``i`` of its neighbour, and
blockMesh writes the faces of each listed block face in the block's own
local direction. A pair therefore only meshes when its edges correspond one
to one under a single rigid motion of the plane - a translation, or a
rotation for a sector - and the export lists and orients them accordingly.

``match_cyclic_pair`` finds that motion from the geometry alone (vertex
names play no part): it maps the first patch's edges onto the second's,
preferring a translation, and requires each edge's image to run against its
partner's block direction, as a coupled face must. ``check_cyclic_nodes``
then compares every mesh node of each edge with its partner's, so counts,
grading and curved geometry are checked in one measure at OpenFOAM's
matching tolerance. Both raise :class:`TopologyError` naming the edges.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel

# Relative tolerance on block vertex positions when pairing edges, as a
# fraction of the pair's extent. Node agreement is judged separately.
VERTEX_TOLERANCE = 1.0e-6
# OpenFOAM's default cyclic matchTolerance: relative to a face's size.
MATCH_TOLERANCE = 1.0e-4


@dataclass(frozen=True)
class CyclicMatch:
    """One edge of the first patch and its image on the second."""

    first: EdgeKey
    second: EdgeKey
    # True when the motion maps first[0] onto second[0], so the two
    # canonical directions are the same physical direction across the pair.
    aligned: bool


@dataclass(frozen=True)
class CyclicPair:
    first: str
    second: str
    rotation: float  # radians, counter-clockwise
    translation: tuple[float, float]
    matches: tuple[CyclicMatch, ...]

    def apply(self, x: float, y: float) -> tuple[float, float]:
        """The image of a point on the first patch on the second."""
        cosine, sine = math.cos(self.rotation), math.sin(self.rotation)
        return (
            cosine * x - sine * y + self.translation[0],
            sine * x + cosine * y + self.translation[1],
        )

    def partner(self, edge: EdgeKey) -> tuple[EdgeKey, bool] | None:
        """The partner of an edge on either patch and whether aligned."""
        current = edge_key(*edge)
        for match in self.matches:
            if match.first == current:
                return match.second, match.aligned
            if match.second == current:
                return match.first, match.aligned
        return None


def cyclic_pairs(model: MeshModel, *, check_nodes: bool = True) -> list[CyclicPair]:
    """Every reciprocal cyclic pair with its edge correspondence.

    Raises :class:`TopologyError` naming the edges of a pair that has no
    edge on one side, does not correspond under one rigid motion, or (with
    ``check_nodes``) whose corresponding edges place their nodes apart.
    """
    result: list[CyclicPair] = []
    seen: set[str] = set()
    for boundary in model.boundaries.values():
        if boundary.kind != "cyclic" or boundary.name in seen:
            continue
        partner = boundary.neighbour_patch
        if partner is None or partner not in model.boundaries:
            continue
        seen.update((boundary.name, partner))
        pair = match_cyclic_pair(model, boundary.name, partner)
        if check_nodes:
            check_cyclic_nodes(model, pair)
        result.append(pair)
    return result


def _directed_occurrences(model: MeshModel) -> dict[EdgeKey, tuple[str, str]]:
    """Each exterior edge as its block traverses it (counter-clockwise)."""
    return {
        current: occurrences[0][2]
        for current, occurrences in model.edge_occurrences().items()
        if len(occurrences) == 1
    }


def _format_edges(edges: list[EdgeKey]) -> str:
    shown = ", ".join(f"{first}-{second}" for first, second in edges[:8])
    if len(edges) > 8:
        shown += f" and {len(edges) - 8} more"
    return shown


def match_cyclic_pair(model: MeshModel, first: str, second: str) -> CyclicPair:
    """The rigid motion and edge correspondence from ``first`` to ``second``."""
    first_edges = model.boundary_edges(first)
    second_edges = model.boundary_edges(second)
    label = f"Cyclic pair {first!r}/{second!r}"
    if not first_edges or not second_edges:
        raise TopologyError(
            f"{label} needs at least one assigned edge on each patch"
        )
    directed = _directed_occurrences(model)
    vertices = model.vertices

    def point(identifier: str) -> tuple[float, float]:
        vertex = vertices[identifier]
        return vertex.x, vertex.y

    xs = [point(v)[0] for current in first_edges + second_edges for v in current]
    ys = [point(v)[1] for current in first_edges + second_edges for v in current]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0e-300)
    tolerance = VERTEX_TOLERANCE * extent
    second_ends = {
        directed[current]: current for current in second_edges
    }

    def close(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return math.hypot(a[0] - b[0], a[1] - b[1]) <= tolerance

    def trial(rotation: float, translation: tuple[float, float]):
        cosine, sine = math.cos(rotation), math.sin(rotation)

        def image(identifier: str) -> tuple[float, float]:
            x, y = point(identifier)
            return (cosine * x - sine * y + translation[0],
                    sine * x + cosine * y + translation[1])

        matches: list[CyclicMatch] = []
        unmatched: list[EdgeKey] = []
        used: set[EdgeKey] = set()
        miss = 0.0  # how far unmatched images land from the partner's vertices
        for current in first_edges:
            start, end = directed[current]
            image_start, image_end = image(start), image(end)
            # A coupled face runs against its partner's block direction.
            found = None
            for (other_start, other_end), other in second_ends.items():
                if other in used:
                    continue
                if close(image_start, point(other_end)) \
                        and close(image_end, point(other_start)):
                    found = other
                    break
            if found is None:
                unmatched.append(current)
                miss += sum(
                    min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in targets)
                    for p in (image_start, image_end)
                )
                continue
            used.add(found)
            matches.append(CyclicMatch(
                current, found, close(image(current[0]), point(found[0]))
            ))
        return matches, unmatched, [e for e in second_edges if e not in used], miss

    anchor = first_edges[0]
    anchor_start, anchor_end = directed[anchor]
    a0, a1 = point(anchor_start), point(anchor_end)
    candidates: list[tuple[float, float, tuple[float, float]]] = []
    for (other_start, other_end) in second_ends:
        # The anchor's start maps onto the candidate's end and vice versa.
        b0, b1 = point(other_end), point(other_start)
        rotation = math.atan2(b1[1] - b0[1], b1[0] - b0[0]) - math.atan2(
            a1[1] - a0[1], a1[0] - a0[0]
        )
        rotation = math.atan2(math.sin(rotation), math.cos(rotation))
        if abs(rotation) <= 1.0e-9:
            rotation = 0.0
        cosine, sine = math.cos(rotation), math.sin(rotation)
        translation = (
            b0[0] - (cosine * a0[0] - sine * a0[1]),
            b0[1] - (sine * a0[0] + cosine * a0[1]),
        )
        candidates.append((abs(rotation), rotation, translation))
    candidates.sort(key=lambda item: item[0])

    targets = [point(v) for current in second_edges for v in current]
    best = None
    for _, rotation, translation in candidates:
        matches, unmatched, left, miss = trial(rotation, translation)
        if not unmatched and not left:
            return CyclicPair(first, second, rotation, translation, tuple(matches))
        score = (-len(matches), miss)
        if best is None or score < best[0]:
            best = (score, rotation, translation, unmatched, left)

    if len(first_edges) != len(second_edges):
        detail = (
            f"{len(first_edges)} edges on {first!r} against "
            f"{len(second_edges)} on {second!r}"
        )
    else:
        detail = f"{len(first_edges)} edges on each"
    message = (
        f"{label} does not correspond face by face ({detail}): no "
        "translation or rotation maps the edges of one onto the other"
    )
    if best is not None:
        _, rotation, translation, unmatched, left = best
        motion = (
            f"translation by ({translation[0]:.6g}, {translation[1]:.6g})"
            if rotation == 0.0 else
            f"rotation by {math.degrees(rotation):.6g} degrees"
        )
        parts = []
        if unmatched:
            parts.append(f"{_format_edges(unmatched)} of {first!r}")
        if left:
            parts.append(f"{_format_edges(left)} of {second!r}")
        message += (
            f"; the closest, a {motion}, leaves "
            + " and ".join(parts) + " without a partner"
        )
    raise TopologyError(message)


def _node_points(model: MeshModel, edge: EdgeKey) -> tuple[tuple[float, float], ...]:
    cells = model.edge_cells[edge]
    return model.edge_points(
        edge, model.edge_node_fractions(edge, range(cells + 1))
    )


def check_cyclic_nodes(model: MeshModel, pair: CyclicPair) -> None:
    """Refuse a pair whose corresponding edges place their nodes apart."""
    depth = abs(model.z_max - model.z_min) / max(model.z_cells, 1)
    for match in pair.matches:
        first_cells = model.edge_cells[match.first]
        second_cells = model.edge_cells[match.second]
        names = (
            f"edge {match.first[0]}-{match.first[1]} of {pair.first!r} and "
            f"its partner {match.second[0]}-{match.second[1]} of {pair.second!r}"
        )
        if first_cells != second_cells:
            raise TopologyError(
                f"Cyclic pair {pair.first!r}/{pair.second!r}: {names} need the "
                f"same cell count ({first_cells} against {second_cells})"
            )
        source = [pair.apply(*p) for p in _node_points(model, match.first)]
        target = list(_node_points(model, match.second))
        if not match.aligned:
            target.reverse()
        worst = (0.0, 0, 0.0)
        for index, (a, b) in enumerate(zip(source, target)):
            width = min(
                math.hypot(a[0] - source[other][0], a[1] - source[other][1])
                for other in (index - 1, index + 1) if 0 <= other < len(source)
            )
            allowed = MATCH_TOLERANCE * 0.5 * math.hypot(width, depth)
            gap = math.hypot(a[0] - b[0], a[1] - b[1])
            if gap > allowed and gap / allowed > worst[0]:
                worst = (gap / allowed, index, gap)
        if worst[0] > 0.0:
            first_ratio = model.edge_total_expansion(match.first)
            second_ratio = model.edge_total_expansion(match.second)
            if not match.aligned:
                second_ratio = 1.0 / second_ratio
            raise TopologyError(
                f"Cyclic pair {pair.first!r}/{pair.second!r}: {names} place "
                f"node {worst[1]} {worst[2]:.3g} apart (more than OpenFOAM's "
                f"matching tolerance); total grading {first_ratio:.6g} against "
                f"{second_ratio:.6g} in the same direction, lengths "
                f"{model.edge_length(match.first):.6g} and "
                f"{model.edge_length(match.second):.6g}"
            )
