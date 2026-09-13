"""A general quadrilateral patch graph: the topology IR of the prototype.

Nothing in this module knows about annuli, wall/ring/spoke sides, sweeps or
boundary layers.  A patch graph is simply a set of topological vertices with
geometric constraints, shared oriented edges, and quadrilateral faces, plus the
bookkeeping needed to reason about it: valence, discrete index, Euler
characteristic, boundary cycles, periodic correspondence and provenance.

Every producer (the annular medial layout, the wall-layer bands, the
sweep/submapping core, and the discrete topology moves) writes into this one
representation, and only this representation is converted to a BlockDrawer
``MeshModel``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import geometry2d as g2

VertexKey = tuple


class GraphError(RuntimeError):
    """Raised when a patch graph is asked for something structurally impossible."""


@dataclass(frozen=True)
class Constraint:
    """Where a topological vertex is allowed to move.

    ``chain``   - the vertex slides along a named domain boundary chain;
    ``guide``   - the vertex slides along a named guide curve (a layer front,
                  a medial branch, a sweep rib);
    ``fixed``   - the vertex is pinned to its point (a domain corner, a sharp
                  wall feature, a medial junction);
    ``free``    - an interior vertex with two degrees of freedom.
    """

    kind: str = "free"
    target: str | None = None
    parameter: float | None = None

    def described(self) -> dict:
        return {
            "kind": self.kind,
            "target": self.target,
            "parameter": None if self.parameter is None else float(self.parameter),
        }


FIXED = Constraint("fixed")
FREE = Constraint("free")


@dataclass
class PGVertex:
    key: VertexKey
    point: np.ndarray
    constraint: Constraint = FREE
    provenance: str = ""


@dataclass
class PGEdge:
    """A shared edge stored once, in canonical vertex-key order."""

    key: tuple[VertexKey, VertexKey]
    kind: str
    points: tuple[tuple[float, float], ...]
    path: np.ndarray
    boundary: str | None
    role: str
    provenance: str

    @property
    def length(self) -> float:
        return g2.total_length(self.path)

    def directed_path(self, first: VertexKey) -> np.ndarray:
        return self.path if self.key[0] == first else self.path[::-1]


@dataclass
class PGFace:
    key: tuple
    corners: tuple[VertexKey, VertexKey, VertexKey, VertexKey]
    role: str
    provenance: str


@dataclass
class PatchGraph:
    """Vertices, shared edges and quadrilateral faces of one topology."""

    euler_characteristic: int = 1
    vertices: dict[VertexKey, PGVertex] = field(default_factory=dict)
    edges: dict[tuple, PGEdge] = field(default_factory=dict)
    faces: list[PGFace] = field(default_factory=list)
    periodic_vertices: dict[VertexKey, VertexKey] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- construction ------------------------------------------------------

    def add_vertex(
        self,
        key: VertexKey,
        point,
        *,
        constraint: Constraint = FREE,
        provenance: str = "",
    ) -> VertexKey:
        existing = self.vertices.get(key)
        if existing is None:
            self.vertices[key] = PGVertex(
                key, np.asarray(point, dtype=np.float64), constraint, provenance
            )
        return key

    @staticmethod
    def edge_key(first: VertexKey, second: VertexKey) -> tuple:
        if first == second:
            raise GraphError(f"an edge needs two distinct vertices ({first!r})")
        return (first, second) if first <= second else (second, first)

    def add_edge(
        self,
        first: VertexKey,
        second: VertexKey,
        *,
        path,
        kind: str = "line",
        points=(),
        boundary: str | None = None,
        role: str = "interior",
        provenance: str = "",
    ) -> tuple:
        key = self.edge_key(first, second)
        if key in self.edges:
            return key
        array = np.asarray(path, dtype=np.float64)
        stored_path = array if key[0] == first else array[::-1].copy()
        stored_points = (
            tuple(points) if key[0] == first else tuple(reversed(tuple(points)))
        )
        self.edges[key] = PGEdge(
            key, kind, stored_points, stored_path, boundary, role, provenance
        )
        return key

    def add_face(
        self, corners, *, role: str = "block", provenance: str = "", key=None
    ) -> PGFace:
        ordered = tuple(corners)
        if len(set(ordered)) != 4:
            raise GraphError(
                f"a quadrilateral face needs four distinct corners: {ordered!r}"
            )
        points = np.asarray([self.vertices[item].point for item in ordered])
        if g2.polygon_area(points) < 0.0:
            ordered = tuple(reversed(ordered))
        face = PGFace(
            key if key is not None else ("face", len(self.faces)),
            ordered,
            role,
            provenance,
        )
        self.faces.append(face)
        return face

    # -- queries -----------------------------------------------------------

    def face_edges(self, face: PGFace) -> list[tuple]:
        return [
            self.edge_key(face.corners[index], face.corners[(index + 1) % 4])
            for index in range(4)
        ]

    def edge_faces(self) -> dict[tuple, list[int]]:
        incidence: dict[tuple, list[int]] = {}
        for index, face in enumerate(self.faces):
            for key in self.face_edges(face):
                incidence.setdefault(key, []).append(index)
        return incidence

    def valence(self) -> dict[VertexKey, int]:
        counts: dict[VertexKey, int] = {key: 0 for key in self.vertices}
        for key in self.edges:
            counts[key[0]] = counts.get(key[0], 0) + 1
            counts[key[1]] = counts.get(key[1], 0) + 1
        return counts

    def face_counts(self) -> dict[VertexKey, int]:
        counts: dict[VertexKey, int] = {key: 0 for key in self.vertices}
        for face in self.faces:
            for corner in face.corners:
                counts[corner] = counts.get(corner, 0) + 1
        return counts

    def boundary_vertices(self) -> set[VertexKey]:
        incidence = self.edge_faces()
        result: set[VertexKey] = set()
        for key, faces in incidence.items():
            if len(faces) == 1:
                result.update(key)
        return result

    def indices(self) -> dict[VertexKey, int]:
        """Discrete charge of every vertex; zero for a regular vertex."""
        valence = self.valence()
        faces = self.face_counts()
        boundary = self.boundary_vertices()
        result = {}
        for key in self.vertices:
            if key in boundary:
                result[key] = 2 - faces.get(key, 0)
            else:
                result[key] = 4 - valence.get(key, 0)
        return result

    def total_index(self) -> int:
        return int(sum(self.indices().values()))

    def euler(self) -> int:
        return len(self.vertices) - len(self.edges) + len(self.faces)

    def singularities(self) -> list[dict]:
        valence = self.valence()
        faces = self.face_counts()
        boundary = self.boundary_vertices()
        charges = self.indices()
        result = []
        for key, vertex in self.vertices.items():
            if charges[key] == 0:
                continue
            result.append(
                {
                    "vertex": list(key),
                    "point": [float(vertex.point[0]), float(vertex.point[1])],
                    "on_boundary": key in boundary,
                    "valence": valence.get(key, 0),
                    "incident_faces": faces.get(key, 0),
                    "index": charges[key],
                    "constraint": vertex.constraint.described(),
                    "provenance": vertex.provenance,
                }
            )
        result.sort(key=lambda record: (record["index"], record["vertex"]))
        return result

    def corner_points(self, face: PGFace) -> np.ndarray:
        return np.asarray([self.vertices[key].point for key in face.corners])

    def face_outline(self, face: PGFace) -> np.ndarray:
        pieces = []
        for index in range(4):
            first = face.corners[index]
            second = face.corners[(index + 1) % 4]
            edge = self.edges[self.edge_key(first, second)]
            pieces.append(edge.directed_path(first)[:-1])
        return np.vstack([*pieces, pieces[0][:1]])

    def boundary_chains(self) -> dict[str, list[tuple]]:
        chains: dict[str, list[tuple]] = {}
        incidence = self.edge_faces()
        for key, faces in incidence.items():
            if len(faces) != 1:
                continue
            name = self.edges[key].boundary
            chains.setdefault(name if name else "", []).append(key)
        return chains

    # -- validation --------------------------------------------------------

    def problems(self, *, check_geometry: bool = True) -> list[dict]:
        found: list[dict] = []
        incidence: dict[tuple, list[int]] = {}
        for index, face in enumerate(self.faces):
            if len(set(face.corners)) != 4:
                found.append(
                    {"kind": "degenerate_face", "face": list(face.key)}
                )
                continue
            points = self.corner_points(face)
            if g2.polygon_area(points) <= 0.0:
                found.append(
                    {"kind": "face_orientation", "face": list(face.key)}
                )
            if not g2.strictly_convex(points):
                found.append(
                    {
                        "kind": "non_convex_face",
                        "face": list(face.key),
                        "block": f"b{index}",
                        "role": face.role,
                        "provenance": face.provenance,
                        "corners": [
                            [float(point[0]), float(point[1])] for point in points
                        ],
                        "corner_angles_degrees": [
                            math.degrees(value)
                            for value in g2.quad_corner_angles(points)
                        ],
                    }
                )
            for key in self.face_edges(face):
                if key not in self.edges:
                    found.append(
                        {
                            "kind": "missing_edge",
                            "face": list(face.key),
                            "edge": [list(key[0]), list(key[1])],
                        }
                    )
                incidence.setdefault(key, []).append(index)
        for key, faces in incidence.items():
            if len(faces) > 2:
                found.append(
                    {
                        "kind": "non_manifold_edge",
                        "edge": [list(key[0]), list(key[1])],
                        "faces": len(faces),
                    }
                )
        for key, edge in self.edges.items():
            faces = incidence.get(key, [])
            if not faces:
                found.append(
                    {"kind": "orphan_edge", "edge": [list(key[0]), list(key[1])]}
                )
            elif len(faces) == 1 and edge.boundary is None:
                found.append(
                    {
                        "kind": "unnamed_boundary_edge",
                        "edge": [list(key[0]), list(key[1])],
                        "role": edge.role,
                    }
                )
            elif len(faces) == 2 and edge.boundary is not None:
                found.append(
                    {
                        "kind": "interior_edge_with_boundary",
                        "edge": [list(key[0]), list(key[1])],
                        "boundary": edge.boundary,
                    }
                )
        used = {corner for face in self.faces for corner in face.corners}
        for key in self.vertices:
            if key not in used:
                found.append({"kind": "hanging_vertex", "vertex": list(key)})
        expected = self.euler_characteristic
        actual = self.euler()
        if actual != expected:
            found.append(
                {
                    "kind": "euler_characteristic",
                    "expected": expected,
                    "actual": actual,
                    "vertices": len(self.vertices),
                    "edges": len(self.edges),
                    "faces": len(self.faces),
                }
            )
        charge = self.total_index()
        if charge != 4 * expected:
            found.append(
                {"kind": "index_sum", "expected": 4 * expected, "actual": charge}
            )
        found.extend(self._boundary_cycle_problems(incidence))
        found.extend(self._periodic_problems())
        if check_geometry:
            found.extend(self._crossing_problems())
        return found

    def _boundary_cycle_problems(self, incidence) -> list[dict]:
        """Every boundary vertex must see exactly two boundary edges."""
        counts: dict[VertexKey, int] = {}
        for key, faces in incidence.items():
            if len(faces) != 1:
                continue
            counts[key[0]] = counts.get(key[0], 0) + 1
            counts[key[1]] = counts.get(key[1], 0) + 1
        found = []
        for key, count in sorted(counts.items(), key=lambda item: str(item[0])):
            if count != 2:
                found.append(
                    {
                        "kind": "broken_boundary_cycle",
                        "vertex": list(key),
                        "boundary_edges": count,
                    }
                )
        return found

    def _periodic_problems(self) -> list[dict]:
        found = []
        for key, partner in sorted(
            self.periodic_vertices.items(), key=lambda item: str(item[0])
        ):
            if partner not in self.vertices:
                found.append(
                    {"kind": "unknown_periodic_partner", "vertex": list(key)}
                )
                continue
            if self.periodic_vertices.get(partner) != key:
                found.append(
                    {
                        "kind": "periodic_not_reciprocal",
                        "vertices": [list(key), list(partner)],
                    }
                )
        return found

    def _crossing_problems(self) -> list[dict]:
        keys = list(self.edges)
        boxes = []
        for key in keys:
            path = self.edges[key].path
            boxes.append(
                (
                    float(np.min(path[:, 0])),
                    float(np.min(path[:, 1])),
                    float(np.max(path[:, 0])),
                    float(np.max(path[:, 1])),
                )
            )
        found = []
        for first in range(len(keys)):
            for second in range(first + 1, len(keys)):
                if set(keys[first]) & set(keys[second]):
                    continue
                a = boxes[first]
                b = boxes[second]
                if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
                    continue
                if g2.paths_cross(
                    self.edges[keys[first]].path, self.edges[keys[second]].path
                ):
                    found.append(
                        {
                            "kind": "crossing_edges",
                            "edges": [
                                [list(keys[first][0]), list(keys[first][1])],
                                [list(keys[second][0]), list(keys[second][1])],
                            ],
                        }
                    )
                    if len(found) >= 12:
                        return found
        return found

    def require_valid(self) -> "PatchGraph":
        found = self.problems()
        if found:
            raise GraphError(f"invalid patch graph: {found[:6]}")
        return self

    # -- reporting ---------------------------------------------------------

    def summary(self) -> dict:
        roles: dict[str, int] = {}
        for face in self.faces:
            roles[face.role] = roles.get(face.role, 0) + 1
        edge_roles: dict[str, int] = {}
        for edge in self.edges.values():
            edge_roles[edge.role] = edge_roles.get(edge.role, 0) + 1
        return {
            "vertices": len(self.vertices),
            "edges": len(self.edges),
            "faces": len(self.faces),
            "euler_characteristic": self.euler(),
            "expected_euler_characteristic": self.euler_characteristic,
            "total_index": self.total_index(),
            "singularity_count": len(self.singularities()),
            "face_roles": dict(sorted(roles.items())),
            "edge_roles": dict(sorted(edge_roles.items())),
            "periodic_vertex_pairs": len(self.periodic_vertices) // 2,
        }


# ---------------------------------------------------------------------------
# Independent geometric coverage test
# ---------------------------------------------------------------------------


def coverage(graph: PatchGraph, domain, *, samples: int = 400) -> dict:
    """Confirm the faces tile the fluid region exactly.

    ``MeshModel.validate()`` is not a planar coverage test, so this samples the
    domain on a regular grid and counts how many face outlines contain each
    fluid sample.  Anything other than one is a defect.
    """
    xmin, ymin, xmax, ymax = domain.bounds()
    # Offset the sample lattice by an irrational fraction of a cell so a sample
    # never lands exactly on a shared edge, where an even-odd test would give
    # the same point to both of its blocks.
    step_x = (xmax - xmin) / samples
    step_y = (ymax - ymin) / samples
    xs = xmin + (np.arange(samples) + 0.3183098861837907) * step_x
    ys = ymax - (np.arange(samples) + 0.2718281828459045) * step_y
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    inside = domain.contains(points)
    counts = np.zeros(len(points), dtype=np.int32)
    for face in graph.faces:
        counts += g2.points_in_loop(graph.face_outline(face), points).astype(np.int32)
    uncovered = inside & (counts == 0)
    overlapped = counts > 1
    outside = ~inside & (counts > 0)
    # A sample within one lattice step of the domain boundary is indeterminate:
    # the face outlines and the domain loops are different polygonisations of
    # the same curve, so their sagittas disagree by less than a step.  Interior
    # overlaps and uncovered cores are unaffected by this exclusion.
    flagged = uncovered | overlapped | outside
    ambiguous = 0
    if bool(np.any(flagged)):
        step = math.hypot(step_x, step_y)
        suspect = points[flagged]
        near = domain.clearance(suspect) < step
        if graph.vertices:
            corners = np.asarray(
                [vertex.point for vertex in graph.vertices.values()]
            )
            # A sample that lands on a shared vertex belongs to every face that
            # meets there as far as an even-odd test can tell.
            gaps = np.min(
                np.linalg.norm(suspect[:, None, :] - corners[None, :, :], axis=2),
                axis=1,
            )
            near |= gaps < step
        indices = np.nonzero(flagged)[0][near]
        ambiguous = int(len(indices))
        uncovered[indices] = False
        overlapped[indices] = False
        outside[indices] = False
    fluid = int(np.count_nonzero(inside))
    return {
        "samples": int(samples),
        "fluid_samples": fluid,
        "boundary_ambiguous_samples": ambiguous,
        "uncovered_samples": int(np.count_nonzero(uncovered)),
        "overlapping_samples": int(np.count_nonzero(overlapped)),
        "outside_samples": int(np.count_nonzero(outside)),
        "uncovered_area_fraction": float(np.count_nonzero(uncovered) / max(1, fluid)),
        "overlap_area_fraction": float(np.count_nonzero(overlapped) / max(1, fluid)),
        "worst_points": [
            [float(value) for value in point]
            for point in points[uncovered | overlapped][:12]
        ],
    }


def constraint_components(graph: PatchGraph) -> list[list[tuple]]:
    """Group edges into BlockDrawer's opposite-edge equality components."""
    parent: dict[tuple, tuple] = {key: key for key in graph.edges}

    def find(item: tuple) -> tuple:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(first: tuple, second: tuple) -> None:
        one, other = find(first), find(second)
        if one != other:
            parent[other] = one

    for face in graph.faces:
        edges = graph.face_edges(face)
        union(edges[0], edges[2])
        union(edges[1], edges[3])
    groups: dict[tuple, list[tuple]] = {}
    for key in graph.edges:
        groups.setdefault(find(key), []).append(key)
    return [sorted(value, key=str) for value in groups.values()]
