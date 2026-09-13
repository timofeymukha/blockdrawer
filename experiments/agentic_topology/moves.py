"""Discrete candidate topology moves, screened before any geometry is built.

The point of this module is not to let an agent place vertices.  Algorithms
measure the topology and the sampled grid, propose a small set of *named*
operations that would change the discrete structure, screen each one against
the Euler/index budget, rank what survives, and report why the rest were
rejected.  An agent then picks between a handful of alternatives.

Every move is expressed as a delta to the run options plus, where needed, a
forced split, so applying one is a deterministic re-run rather than an in-place
mutation of a half-built model.  A move that this iteration cannot construct is
still generated and screened; it carries ``implemented = False`` and the exact
reason, which is more useful to an agent than silence.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2
import patch_graph as pg


@dataclass(frozen=True)
class Move:
    identifier: str
    kind: str
    target: dict
    rationale: str
    index_change: dict
    index_balanced: bool
    complexity: int
    implemented: bool
    score: float
    rejection: str | None = None
    option_delta: dict = field(default_factory=dict)

    def described(self) -> dict:
        return {
            "id": self.identifier,
            "kind": self.kind,
            "target": self.target,
            "rationale": self.rationale,
            "index_change": self.index_change,
            "index_balanced": self.index_balanced,
            "extra_faces": self.complexity,
            "implemented": self.implemented,
            "score": self.score,
            "rejection": self.rejection,
            "option_delta": self.option_delta,
        }


# ---------------------------------------------------------------------------
# Measurements the generators share
# ---------------------------------------------------------------------------


def corner_angles(graph: pg.PatchGraph) -> dict[pg.VertexKey, list[float]]:
    """Interior angle each face contributes at each of its corners, in degrees."""
    result: dict[pg.VertexKey, list[float]] = {}
    for face in graph.faces:
        points = graph.corner_points(face)
        angles = np.degrees(g2.quad_corner_angles(points))
        for corner, angle in zip(face.corners, angles):
            result.setdefault(corner, []).append(float(angle))
    return result


def fluid_angles(graph: pg.PatchGraph) -> dict[pg.VertexKey, float]:
    """Total fluid-side angle at every vertex, summed over its faces."""
    return {key: float(sum(values)) for key, values in corner_angles(graph).items()}


def index_balance(changes: dict) -> bool:
    return int(sum(changes.values())) == 0


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def feature_fan_moves(result, *, minimum_corner: float = 135.0) -> list[Move]:
    """Boundary vertices whose fluid sector is shared by too few blocks."""
    graph = result.graph
    if graph is None:
        return []
    boundary = graph.boundary_vertices()
    faces = graph.face_counts()
    angles = fluid_angles(graph)
    moves: list[Move] = []
    for key in sorted(boundary, key=str):
        count = faces.get(key, 0)
        total = angles.get(key, 0.0)
        if count < 1:
            continue
        average = total / count
        if average < minimum_corner:
            continue
        wanted = max(count + 1, int(round(total / 110.0)))
        implemented = wanted == 3 and count == 2
        vertex = graph.vertices[key]
        changes = {
            "boundary_vertex": 2 - wanted - (2 - count),
            "new_interior_vertices": wanted,
        }
        changes = {
            "wall_vertex": -(wanted - count),
            "fan_interior_vertices": (wanted - count) + 2,
            "neighbour_front_vertices": -2,
        }
        moves.append(
            Move(
                f"feature_fan:{_label(key)}",
                "wall_feature_fan",
                {
                    "vertex": list(key),
                    "point": [float(vertex.point[0]), float(vertex.point[1])],
                    "fluid_angle_degrees": total,
                    "incident_faces": count,
                    "mean_corner_degrees": average,
                    "wanted_sectors": wanted,
                },
                (
                    f"the fluid sector at this wall feature is {total:.1f} degrees "
                    f"shared by {count} block(s); {wanted} sectors would give "
                    f"{total / wanted:.1f} degree corners"
                ),
                changes,
                index_balance(changes),
                complexity=3,
                implemented=implemented,
                score=(average - 90.0) / 90.0,
                rejection=(
                    None
                    if implemented
                    else "only the three-sector contained fan is constructed in "
                    "this iteration"
                ),
                option_delta={"layer.fan_enabled": True},
            )
        )
    return moves


def patch_split_moves(result, *, limit: int = 6) -> list[Move]:
    """Split the blocks that carry the worst sampled cells."""
    graph = result.graph
    if graph is None or result.grid is None:
        return []
    ranked: dict[str, float] = {}
    for record in result.grid.worst_cells:
        ranked[record["block"]] = max(
            ranked.get(record["block"], 0.0), float(record["equiangle_skewness"])
        )
    extreme = result.grid.maximum_skewness
    if extreme is not None:
        ranked.setdefault(extreme.block, extreme.value)
    moves: list[Move] = []
    for block, skewness in sorted(ranked.items(), key=lambda item: -item[1])[:limit]:
        index = int(block[1:])
        if index >= len(graph.faces):
            continue
        face = graph.faces[index]
        target = _layout_target(result, face)
        implemented = target is not None
        changes = {"splitting a strip preserves every vertex index": 0}
        moves.append(
            Move(
                f"split_patch:{block}",
                "split_patch",
                {
                    "block": block,
                    "face": list(face.key),
                    "role": face.role,
                    "equiangle_skewness": skewness,
                    **({"layout": target} if target else {}),
                },
                (
                    f"block {block} carries a sampled cell with equiangle skewness "
                    f"{skewness:.3f}; cutting its strip adds one column"
                ),
                changes,
                True,
                complexity=len(graph.faces) // 8 + 1,
                implemented=implemented,
                score=skewness,
                rejection=(
                    None
                    if implemented
                    else "this face is not on a splittable annular patch"
                ),
                option_delta=(
                    {"forced_splits": [target]} if target is not None else {}
                ),
            )
        )
    return moves


def singularity_split_moves(result) -> list[Move]:
    """High-charge interior vertices that could be split into regular pairs."""
    graph = result.graph
    if graph is None:
        return []
    valence = graph.valence()
    boundary = graph.boundary_vertices()
    moves: list[Move] = []
    for key, vertex in graph.vertices.items():
        if key in boundary:
            continue
        charge = 4 - valence.get(key, 0)
        if abs(charge) < 2:
            continue
        pieces = split_valences(valence.get(key, 0))
        changes = {
            "original_vertex": -charge,
            "replacement_vertices": sum(4 - value for value in pieces),
        }
        moves.append(
            Move(
                f"split_singularity:{_label(key)}",
                "split_singularity",
                {
                    "vertex": list(key),
                    "point": [float(vertex.point[0]), float(vertex.point[1])],
                    "valence": valence.get(key, 0),
                    "index": charge,
                    "replacement_valences": pieces,
                    "replacement_index_sum": sum(4 - value for value in pieces),
                },
                (
                    f"valence {valence.get(key, 0)} concentrates index {charge}; "
                    f"valences {pieces} carry the same total charge"
                ),
                changes,
                index_balance(changes),
                complexity=2,
                implemented=False,
                score=abs(charge),
                rejection=(
                    "separating a singularity pair rewrites the separatrices "
                    "between them, which this iteration does not construct"
                ),
            )
        )
    return moves


def split_valences(valence: int) -> list[int]:
    """Valences of the lower-charge vertices one singularity can split into."""
    charge = 4 - valence
    if charge == 0:
        return [valence]
    step = 1 if charge > 0 else -1
    return [4 - step for _ in range(abs(charge))]


def strip_collapse_moves(result, *, minimum_metric: float = 1.0) -> list[Move]:
    """Equality components so short that a single cell already over-resolves them."""
    graph = result.graph
    if graph is None or result.counts is None:
        return []
    moves: list[Move] = []
    for record in result.counts.components:
        if record["cells"] > 1 or record["weighted_target"] >= minimum_metric:
            continue
        changes = {"collapsing a regular strip preserves every index": 0}
        moves.append(
            Move(
                f"collapse_strip:{record['component']}",
                "collapse_strip",
                {
                    "component": record["component"],
                    "edges": record["edges"],
                    "metric_target": record["weighted_target"],
                    "roles": record["roles"],
                },
                (
                    f"component {record['component']} wants "
                    f"{record['weighted_target']:.2f} cells; the strip is thinner "
                    f"than one cell of the requested metric"
                ),
                changes,
                True,
                complexity=-record["edges"],
                implemented=False,
                score=1.0 - record["weighted_target"],
                rejection=(
                    "collapsing a strip needs the inverse of the conformal split "
                    "on the patch graph, which this iteration does not implement"
                ),
            )
        )
    return moves


def separatrix_moves(result) -> list[Move]:
    """Cuts that would connect two singularities of opposite charge."""
    graph = result.graph
    if graph is None:
        return []
    records = graph.singularities()
    positive = [item for item in records if item["index"] > 0]
    negative = [item for item in records if item["index"] < 0]
    moves: list[Move] = []
    for first in positive[:4]:
        for second in negative[:4]:
            changes = {
                "first": -first["index"],
                "second": -second["index"],
                "cut": first["index"] + second["index"],
            }
            distance = float(
                np.linalg.norm(
                    np.asarray(first["point"]) - np.asarray(second["point"])
                )
            )
            moves.append(
                Move(
                    f"separatrix:{_label(tuple(first['vertex']))}"
                    f"-{_label(tuple(second['vertex']))}",
                    "insert_separatrix",
                    {
                        "from": first["vertex"],
                        "to": second["vertex"],
                        "indices": [first["index"], second["index"]],
                        "distance": distance,
                        "distance_over_scale": distance / max(result.scale, 1e-30),
                    },
                    (
                        "a cut between an index "
                        f"{first['index']} and an index {second['index']} vertex "
                        "would cancel both charges"
                    ),
                    changes,
                    index_balance(changes),
                    complexity=4,
                    implemented=False,
                    score=1.0 / (1.0 + distance / max(result.scale, 1e-30)),
                    rejection=(
                        "separatrix tracing needs a cross field or guide curve "
                        "producer, which is the next stage"
                    ),
                )
            )
    return moves


GENERATORS = (
    feature_fan_moves,
    patch_split_moves,
    singularity_split_moves,
    strip_collapse_moves,
    separatrix_moves,
)


def candidates(result, *, complexity_weight: float = 0.02) -> list[Move]:
    """Every screened candidate, best first."""
    found: list[Move] = []
    for generator in GENERATORS:
        found.extend(generator(result))
    ranked = sorted(
        found,
        key=lambda move: (
            not move.implemented,
            -(move.score - complexity_weight * max(move.complexity, 0)),
            move.identifier,
        ),
    )
    return ranked


def apply(options, move: Move):
    """Return the run options that realise a move, or raise when it cannot."""
    if not move.implemented:
        raise ValueError(
            f"move {move.identifier!r} is not implemented: {move.rejection}"
        )
    updated = options
    delta = dict(move.option_delta)
    forced = delta.pop("forced_splits", None)
    if forced:
        updated = dataclasses.replace(
            updated,
            forced_splits=tuple(updated.forced_splits)
            + tuple(tuple(item) for item in forced),
        )
    for path, value in delta.items():
        section, _, field_name = path.partition(".")
        if not field_name:
            updated = dataclasses.replace(updated, **{section: value})
            continue
        inner = dataclasses.replace(getattr(updated, section), **{field_name: value})
        updated = dataclasses.replace(updated, **{section: inner})
    return updated


def _label(key) -> str:
    return "_".join(str(part) for part in key)


def _layout_target(result, face) -> list | None:
    """Cell and cut order of the annular patch a face came from, when there is one."""
    if result.family != "external" or result.layout is None:
        return None
    corners = [key for key in face.corners if key[0] in ("gate", "front")]
    if not corners:
        return None
    cell = corners[0][1]
    anchors = {tuple(key[2:]) for key in corners}
    for patch in result.layout.patches:
        if patch.cell != cell:
            continue
        cuts = result.layout.cuts[patch.cell]
        pair = {
            cuts[patch.first_cut].anchor.key,
            cuts[patch.second_cut].anchor.key,
        }
        if pair <= anchors:
            return [int(cell), int(patch.first_cut)]
    return None
