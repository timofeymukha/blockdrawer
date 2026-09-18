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

# Two points closer than this fraction of the graph diagonal are the same
# point.  Everything geometric here is compared against a length derived
# from it, never against a bare epsilon.
TOUCH_RATIO = 1.0e-9
# An edge shorter than this fraction of the graph diagonal has collapsed.
DEGENERATE_RATIO = 1.0e-7

# The edge roles the producers assign, grouped by the direction they resolve.
# A tangential edge runs along a wall, or along the front or medial ring that
# follows one; a normal edge crosses a band or a core towards a wall.  The
# sweep producer's ribs cross the channel, so they are normal edges too.
TANGENTIAL_ROLES = frozenset({"wall", "front", "ring"})
NORMAL_ROLES = frozenset({"layer_spoke", "core_spoke", "core_rib", "core_rung"})


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
    # Face keys must stay unique for the lifetime of a graph, including
    # across removals, so a counter issues them instead of the face count.
    face_counter: int = 0

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
        if key is None:
            key = ("face", self.face_counter)
            self.face_counter += 1
        face = PGFace(key, ordered, role, provenance)
        self.faces.append(face)
        return face

    # -- scale, copying and surgery ---------------------------------------

    def scale(self) -> float:
        """Diagonal of the vertex bounding box; the natural length unit here."""
        if not self.vertices:
            return 1.0
        points = np.asarray([vertex.point for vertex in self.vertices.values()])
        span = np.max(points, axis=0) - np.min(points, axis=0)
        value = float(math.hypot(float(span[0]), float(span[1])))
        return value if value > 0.0 else 1.0

    def tolerance(self, ratio: float = TOUCH_RATIO) -> float:
        """Length below which two points count as the same point."""
        return float(ratio) * self.scale()

    def copy(self) -> "PatchGraph":
        """An independent graph; mutating the copy cannot touch the original."""
        clone = PatchGraph(euler_characteristic=self.euler_characteristic)
        for key, vertex in self.vertices.items():
            clone.vertices[key] = PGVertex(
                key, vertex.point.copy(), vertex.constraint, vertex.provenance
            )
        for key, edge in self.edges.items():
            clone.edges[key] = PGEdge(
                key,
                edge.kind,
                tuple(edge.points),
                edge.path.copy(),
                edge.boundary,
                edge.role,
                edge.provenance,
            )
        clone.faces = [
            PGFace(face.key, face.corners, face.role, face.provenance)
            for face in self.faces
        ]
        clone.periodic_vertices = dict(self.periodic_vertices)
        clone.notes = list(self.notes)
        clone.face_counter = self.face_counter
        return clone

    def remove_faces(self, keys) -> list[PGFace]:
        """Drop faces by key and return them, leaving edges and vertices alone."""
        wanted = {tuple(key) for key in keys}
        removed = [face for face in self.faces if tuple(face.key) in wanted]
        self.faces = [face for face in self.faces if tuple(face.key) not in wanted]
        return removed

    def unused_entities(self, *, keep_edges=(), keep_vertices=()):
        """Edges and vertices no remaining face refers to."""
        protected_edges = {tuple(key) for key in keep_edges}
        protected_vertices = set(keep_vertices)
        live_edges: set[tuple] = set()
        live_vertices: set = set()
        for face in self.faces:
            live_vertices.update(face.corners)
            live_edges.update(self.face_edges(face))
        edges = [
            key
            for key in self.edges
            if key not in live_edges and key not in protected_edges
        ]
        vertices = [
            key
            for key in self.vertices
            if key not in live_vertices and key not in protected_vertices
        ]
        return edges, vertices

    def discard(self, edges=(), vertices=()) -> None:
        for key in edges:
            self.edges.pop(tuple(key), None)
        for key in vertices:
            self.vertices.pop(key, None)
            partner = self.periodic_vertices.pop(key, None)
            if partner is not None:
                self.periodic_vertices.pop(partner, None)

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

    def problems(
        self,
        *,
        check_geometry: bool = True,
        tolerance_ratio: float = TOUCH_RATIO,
    ) -> list[dict]:
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
        found.extend(self._degenerate_edge_problems(tolerance_ratio))
        if check_geometry:
            found.extend(self._crossing_problems(tolerance_ratio))
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

    CONFLICT_PROBLEM_KINDS = {
        g2.PROPER_CROSSING: "crossing_edges",
        g2.T_JUNCTION: "t_junction_edges",
        g2.COLLINEAR_OVERLAP: "overlapping_edges",
        g2.UNEXPECTED_TOUCH: "touching_edges",
        g2.DEGENERATE_SEGMENT: "degenerate_edge_segment",
    }

    def _degenerate_edge_problems(self, ratio: float) -> list[dict]:
        limit = DEGENERATE_RATIO * self.scale()
        found = []
        for key, edge in sorted(self.edges.items(), key=str):
            if edge.length > limit:
                continue
            found.append(
                {
                    "kind": "degenerate_edge",
                    "edge": [list(key[0]), list(key[1])],
                    "length": float(edge.length),
                    "limit": float(limit),
                    "role": edge.role,
                }
            )
        return found

    def _crossing_problems(self, ratio: float = TOUCH_RATIO) -> list[dict]:
        """Every forbidden geometric relation between two distinct edges.

        Edges that share a vertex are *not* skipped: two edges leaving the same
        vertex can still overlap, and an edge can still pass through a vertex
        another edge merely ends at.  Their shared vertices are declared as the
        only points where touching is legitimate instead.
        """
        tolerance = self.tolerance(ratio)
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
                a = boxes[first]
                b = boxes[second]
                if (
                    a[2] + tolerance < b[0]
                    or b[2] + tolerance < a[0]
                    or a[3] + tolerance < b[1]
                    or b[3] + tolerance < a[1]
                ):
                    continue
                shared = [
                    self.vertices[key].point
                    for key in set(keys[first]) & set(keys[second])
                    if key in self.vertices
                ]
                conflicts = g2.path_conflicts(
                    self.edges[keys[first]].path,
                    self.edges[keys[second]].path,
                    tolerance=tolerance,
                    shared=shared,
                    limit=1,
                )
                for conflict in conflicts:
                    found.append(
                        {
                            "kind": self.CONFLICT_PROBLEM_KINDS.get(
                                conflict["kind"], "edge_conflict"
                            ),
                            "relation": conflict["kind"],
                            "edges": [
                                [list(keys[first][0]), list(keys[first][1])],
                                [list(keys[second][0]), list(keys[second][1])],
                            ],
                            "roles": [
                                self.edges[keys[first]].role,
                                self.edges[keys[second]].role,
                            ],
                            "point": (
                                None
                                if conflict.get("point") is None
                                else [
                                    float(conflict["point"][0]),
                                    float(conflict["point"][1]),
                                ]
                            ),
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


@dataclass(frozen=True)
class StructureLimits:
    """What a topology may impose on any later sizing.

    ``max_length_ratio`` bounds the geometric length ratio inside one
    opposite-edge equality component, which under uniform grading is the
    cell-size jump that component forces whatever count it gets.  A
    tangential/normal coupling ties a streamwise resolution to a wall-normal
    one and is refused unless allowed.  ``None`` disables the ratio limit.
    """

    max_length_ratio: float | None = 20.0
    allow_tangential_normal_coupling: bool = False

    def described(self) -> dict:
        return {
            "max_length_ratio": (
                "disabled" if self.max_length_ratio is None else self.max_length_ratio
            ),
            "allow_tangential_normal_coupling": self.allow_tangential_normal_coupling,
        }


def structure_failures(structure: dict, limits: StructureLimits) -> list[dict]:
    """The structural sizing statements a topology fails, as failure records.

    Each record has the shape of a grid quality failure - ``metric``,
    ``observed``, ``limit``, ``comparison`` - so an agent reads both kinds the
    same way; ``edges`` names the offending component's extreme edges.
    """
    found: list[dict] = []
    worst = structure.get("worst") or []
    if limits.max_length_ratio is not None and worst:
        record = worst[0]
        if record["length_ratio"] > limits.max_length_ratio:
            found.append(
                {
                    "metric": "maximum_component_length_ratio",
                    "observed": float(record["length_ratio"]),
                    "limit": float(limits.max_length_ratio),
                    "comparison": "at_most",
                    "block": None,
                    "component": record["component"],
                    "roles": record["roles"],
                    "edges": [record["shortest_edge"], record["longest_edge"]],
                    "detail": (
                        "two edges of one opposite-edge equality component carry "
                        "the same cell count whatever it is, so their length ratio "
                        "is the cell-size jump the topology forces under uniform "
                        "grading"
                    ),
                }
            )
    couplings = int(structure.get("tangential_normal_couplings", 0))
    if couplings and not limits.allow_tangential_normal_coupling:
        example = next(
            (item for item in structure.get("coupled", worst) if item.get("mixes_tangential_and_normal")),
            None,
        )
        found.append(
            {
                "metric": "tangential_normal_couplings",
                "observed": float(couplings),
                "limit": 0.0,
                "comparison": "at_most",
                "block": None,
                "component": None if example is None else example["component"],
                "roles": None if example is None else example["roles"],
                "edges": None,
                "detail": (
                    "a component holds both a tangential and a wall-normal edge, "
                    "so a streamwise resolution is tied to a boundary-layer one"
                ),
            }
        )
    return found


def sizing_structure(graph: PatchGraph, *, reported: int = 6) -> dict:
    """Count-independent consequences of the opposite-edge equality components.

    Whatever cell counts are chosen later, two edges in one component carry
    the same number of cells, so the ratio of their geometric lengths is a
    lower bound on the cell-size jump between them under uniform grading, and a
    component that holds both a tangential and a normal edge ties a streamwise
    resolution to a wall-normal one.  A component that holds both band spokes
    and core spokes ties the boundary layer's thickness resolution to the depth
    of the core behind it.  All three are properties of the topology and the
    vertex positions alone, so they are measured here before any size metric
    is consulted, and they are what the topology stage can be held to.
    """
    records = []
    for index, keys in enumerate(constraint_components(graph)):
        lengths = np.asarray([graph.edges[key].length for key in keys])
        roles = sorted({graph.edges[key].role for key in keys})
        shortest = int(np.argmin(lengths))
        longest = int(np.argmax(lengths))
        ratio = (
            float(lengths[longest] / lengths[shortest])
            if lengths[shortest] > 0.0
            else math.inf
        )
        records.append(
            {
                "component": index,
                "edges": len(keys),
                "roles": roles,
                "minimum_length": float(lengths[shortest]),
                "maximum_length": float(lengths[longest]),
                "length_ratio": ratio,
                "shortest_edge": [list(keys[shortest][0]), list(keys[shortest][1])],
                "longest_edge": [list(keys[longest][0]), list(keys[longest][1])],
                "mixes_tangential_and_normal": bool(
                    set(roles) & TANGENTIAL_ROLES and set(roles) & NORMAL_ROLES
                ),
                "ties_band_to_core_depth": (
                    {"layer_spoke", "core_spoke"} <= set(roles)
                ),
            }
        )
    records.sort(key=lambda record: -record["length_ratio"])
    return {
        "components": len(records),
        "maximum_length_ratio": records[0]["length_ratio"] if records else 1.0,
        "tangential_normal_couplings": sum(
            record["mixes_tangential_and_normal"] for record in records
        ),
        "band_core_depth_couplings": sum(
            record["ties_band_to_core_depth"] for record in records
        ),
        "worst": records[:reported],
        # Couplings need not have an extreme length ratio. Keep their full
        # evidence even when they fall outside the short worst-ratio table.
        "coupled": [record for record in records if record["mixes_tangential_and_normal"]],
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
