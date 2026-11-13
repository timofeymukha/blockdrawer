"""Read an ASCII OpenFOAM polyMesh and map checkMesh's sets to blocks.

``checkMesh`` writes the faces, cells and points it objects to as sets under
``constant/polyMesh/sets``. ``blockMesh`` numbers cells block by block in
``blockMeshDict`` order - the order :mod:`blockdrawer.foam` writes the
model's blocks - and makes the lower-numbered cell of every internal face
its owner, so an offending face belongs to its owner's block and an
offending cell to its own. This module reads the few polyMesh lists that
mapping needs with the standard library alone and reports, per set, how many
items each block holds and where they are in the 2D plane, so an agent can
render that region and repair that block.

Only the ASCII format is read; :mod:`blockdrawer.checkmesh` writes a case
whose ``controlDict`` asks for it. Large lists are kept as their text lines
and parsed on demand, so a set of a few hundred faces in a million-face mesh
costs one pass over ``owner`` rather than a parse of the whole mesh.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
import gzip
import math
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from .model import MeshModel


Vector = tuple[float, float, float]

# Sets that name faces, cells or points, as checkMesh (v2606) writes them.
FACE_SETS = frozenset({
    "nonOrthoFaces", "wrongOrientedFaces", "skewFaces", "zeroAreaFaces",
    "concaveFaces", "warpedFaces", "lowWeightFaces", "faceVolumeRatio",
    "lowQualityTetFaces", "coupledFaces",
})
CELL_SETS = frozenset({
    "highAspectRatioCells", "nonClosedCells", "zeroVolumeCells",
    "concaveCells", "cellDeterminant", "underdeterminedCells",
})
POINT_SETS = frozenset({"shortEdges", "nonAlignedEdges", "nearPoints"})


class PolyMeshError(ValueError):
    """A polyMesh file is missing, binary, or not in the expected shape."""


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    compressed = path.with_name(path.name + ".gz")
    if compressed.is_file():
        with gzip.open(compressed, "rt", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    raise PolyMeshError(f"{path} does not exist")


def _header(text: str, path: Path) -> tuple[dict[str, str], int]:
    """The FoamFile header entries and the offset just after its closing brace."""
    start = text.find("FoamFile")
    if start < 0:
        raise PolyMeshError(f"{path} has no FoamFile header")
    open_brace = text.find("{", start)
    close_brace = text.find("}", open_brace)
    if open_brace < 0 or close_brace < 0:
        raise PolyMeshError(f"{path} has an unterminated FoamFile header")
    entries: dict[str, str] = {}
    for line in text[open_brace + 1:close_brace].split(";"):
        parts = line.split(None, 1)
        if len(parts) == 2:
            entries[parts[0]] = parts[1].strip().strip('"')
    if entries.get("format", "ascii") != "ascii":
        raise PolyMeshError(
            f"{path} is written in {entries.get('format')} format; BlockDrawer reads "
            "ASCII meshes only (set writeFormat ascii in system/controlDict)"
        )
    return entries, close_brace + 1


_SKIP = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.S)
_LIST_START = re.compile(r"(\d+)\s*([({])")
_NESTED_ITEM = re.compile(r"(\d*)\s*\(([^()]*)\)")


@dataclass(frozen=True)
class _ListText:
    """The entries of one OpenFOAM list as text, one entry per item."""

    entries: Sequence[str]
    end: int  # offset in the source text just after the list


def _list_entries(text: str, start: int, path: Path, *, nested: bool) -> _ListText:
    """Split the list that starts at or after ``start`` into item strings.

    The fast path is OpenFOAM's usual ASCII layout - the count, ``(`` on its
    own line, one item per line, ``)`` - which is split by lines. Short lists
    written on one line and uniform ``N{value}`` lists take a slower general
    path. ``nested`` lists hold bracketed items (points, faces).
    """
    position = _SKIP.match(text, start).end()
    match = _LIST_START.match(text, position)
    if match is None:
        raise PolyMeshError(f"{path} contains no list where one was expected")
    count = int(match.group(1))
    opener = match.end() - 1
    if text[opener] == "{":
        close = text.index("}", opener)
        return _ListText([text[opener + 1:close].strip()] * count, close + 1)
    if text.startswith("\n", opener + 1) or text.startswith("\r\n", opener + 1):
        lines = text[opener + 1:].split("\n", count + 2)
        if len(lines) >= count + 2 and lines[count + 1].strip().startswith(")"):
            items = [line.strip() for line in lines[1:count + 1]]
            if all(items) and (
                all(item.endswith(")") for item in items) if nested
                else all(" " not in item and "(" not in item for item in items)
            ):
                consumed = sum(len(line) + 1 for line in lines[:count + 1])
                close = text.index(")", opener + 1 + consumed)
                return _ListText(items, close + 1)
    if nested:
        found: list[str] = []
        end = opener + 1
        for item in _NESTED_ITEM.finditer(text, opener + 1):
            if len(found) == count:
                break
            found.append(item.group(0).strip())
            end = item.end()
        close = text.index(")", end)
    else:
        close = text.index(")", opener + 1)
        found = text[opener + 1:close].split()
    if len(found) != count:
        raise PolyMeshError(f"{path}: expected {count} entries, found {len(found)}")
    return _ListText(found, close + 1)


def read_label_list(path: str | Path) -> list[int]:
    """A ``labelList`` file (``owner``, ``neighbour``) or a face/cell/point set."""
    source = Path(path)
    text = _read_text(source)
    _entries, offset = _header(text, source)
    listed = _list_entries(text, offset, source, nested=False)
    try:
        return [int(item) for item in listed.entries]
    except ValueError as exc:
        raise PolyMeshError(f"{source}: not a list of labels ({exc})") from exc


def read_set(path: str | Path) -> tuple[str, list[int]]:
    """The class (``faceSet``, ``cellSet``, ``pointSet``) and labels of a set file."""
    source = Path(path)
    text = _read_text(source)
    entries, offset = _header(text, source)
    listed = _list_entries(text, offset, source, nested=False)
    try:
        labels = [int(item) for item in listed.entries]
    except ValueError as exc:
        raise PolyMeshError(f"{source}: not a list of labels ({exc})") from exc
    return entries.get("class", ""), labels


def _vector(item: str) -> Vector:
    values = item.strip().strip("()").split()
    if len(values) != 3:
        raise PolyMeshError(f"not a 3D point: {item!r}")
    return float(values[0]), float(values[1]), float(values[2])


def _face_labels(item: str) -> list[int]:
    open_index = item.index("(")
    return [int(value) for value in item[open_index + 1:item.rindex(")")].split()]


class PolyMesh:
    """Lazy access to the points, faces, owner and neighbour of a polyMesh."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._points: Sequence[str] | None = None
        self._faces: Sequence[str] | None = None
        self._compact: tuple[list[int], list[int]] | None = None
        self._owner: list[int] | None = None
        self._neighbour: list[int] | None = None
        self._point_cache: dict[int, Vector] = {}

    # -- raw lists ---------------------------------------------------------

    @property
    def owner(self) -> list[int]:
        if self._owner is None:
            self._owner = read_label_list(self.directory / "owner")
        return self._owner

    @property
    def neighbour(self) -> list[int]:
        if self._neighbour is None:
            self._neighbour = read_label_list(self.directory / "neighbour")
        return self._neighbour

    @property
    def cell_count(self) -> int:
        owner = self.owner
        neighbour = self.neighbour
        return max(max(owner, default=-1), max(neighbour, default=-1)) + 1

    def _load_points(self) -> Sequence[str]:
        if self._points is None:
            path = self.directory / "points"
            text = _read_text(path)
            _entries, offset = _header(text, path)
            self._points = _list_entries(text, offset, path, nested=True).entries
        return self._points

    def _load_faces(self) -> None:
        if self._faces is not None or self._compact is not None:
            return
        path = self.directory / "faces"
        text = _read_text(path)
        entries, offset = _header(text, path)
        if entries.get("class") == "faceCompactList":
            offsets = _list_entries(text, offset, path, nested=False)
            labels = _list_entries(text, offsets.end, path, nested=False)
            self._compact = (
                [int(item) for item in offsets.entries],
                [int(item) for item in labels.entries],
            )
        else:
            self._faces = _list_entries(text, offset, path, nested=True).entries

    # -- geometry ----------------------------------------------------------

    def point(self, index: int) -> Vector:
        cached = self._point_cache.get(index)
        if cached is None:
            cached = _vector(self._load_points()[index])
            self._point_cache[index] = cached
        return cached

    def face(self, index: int) -> list[int]:
        self._load_faces()
        if self._compact is not None:
            offsets, labels = self._compact
            return labels[offsets[index]:offsets[index + 1]]
        assert self._faces is not None
        return _face_labels(self._faces[index])

    @property
    def face_count(self) -> int:
        self._load_faces()
        if self._compact is not None:
            return len(self._compact[0]) - 1
        assert self._faces is not None
        return len(self._faces)

    def face_points(self, index: int) -> list[Vector]:
        return [self.point(label) for label in self.face(index)]

    def face_centre_and_area(self, index: int) -> tuple[Vector, Vector]:
        """OpenFOAM's face centre and area vector (primitiveMeshTools)."""
        return face_centre_and_area(self.face_points(index))

    def cell_faces(self, cells: Iterable[int]) -> dict[int, list[tuple[int, bool]]]:
        """Each wanted cell's faces as ``(face, owned)`` pairs, in one pass."""
        wanted = set(cells)
        found: dict[int, list[tuple[int, bool]]] = {cell: [] for cell in wanted}
        for face, cell in enumerate(self.owner):
            if cell in wanted:
                found[cell].append((face, True))
        for face, cell in enumerate(self.neighbour):
            if cell in wanted:
                found[cell].append((face, False))
        return found

    def cell_centre(self, faces: list[tuple[int, bool]]) -> Vector:
        """OpenFOAM's volume-weighted cell centre from the cell's faces."""
        geometry = [self.face_centre_and_area(face) for face, _owned in faces]
        if not geometry:
            raise PolyMeshError("a cell without faces")
        estimate = _scale(_sum(centre for centre, _area in geometry), 1.0 / len(geometry))
        total = 0.0
        weighted = (0.0, 0.0, 0.0)
        for (centre, area), (_face, owned) in zip(geometry, faces):
            offset = _sub(centre, estimate) if owned else _sub(estimate, centre)
            volume = _dot(area, offset)
            pyramid = _add(_scale(centre, 0.75), _scale(estimate, 0.25))
            weighted = _add(weighted, _scale(pyramid, volume))
            total += volume
        if abs(total) > 1.0e-300:
            return _scale(weighted, 1.0 / total)
        return estimate


def face_centre_and_area(points: Sequence[Vector]) -> tuple[Vector, Vector]:
    """Centre and area vector of a polygon, exactly as OpenFOAM computes them."""
    count = len(points)
    if count == 3:
        a, b, c = points
        centre = _scale(_add(_add(a, b), c), 1.0 / 3.0)
        return centre, _scale(_cross(_sub(b, a), _sub(c, a)), 0.5)
    estimate = _scale(_sum(points), 1.0 / count)
    sum_n = (0.0, 0.0, 0.0)
    sum_a = 0.0
    sum_ac = (0.0, 0.0, 0.0)
    for index in range(count):
        here = points[index]
        following = points[(index + 1) % count]
        c = _add(_add(here, following), estimate)
        n = _cross(_sub(following, here), _sub(estimate, here))
        a = math.sqrt(_dot(n, n))
        sum_n = _add(sum_n, n)
        sum_a += a
        sum_ac = _add(sum_ac, _scale(c, a))
    if sum_a < 1.0e-300:
        return estimate, (0.0, 0.0, 0.0)
    return _scale(sum_ac, 1.0 / (3.0 * sum_a)), _scale(sum_n, 0.5)


def _add(a: Vector, b: Vector) -> Vector:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _sub(a: Vector, b: Vector) -> Vector:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(a: Vector, factor: float) -> Vector:
    return a[0] * factor, a[1] * factor, a[2] * factor


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sum(vectors: Iterable[Vector]) -> Vector:
    total = (0.0, 0.0, 0.0)
    for vector in vectors:
        total = _add(total, vector)
    return total


# ---------------------------------------------------------------------------
# Mapping sets to blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockHits:
    """The items of one set that fall in one block, and where they are."""

    block: str
    count: int
    centroid: tuple[float, float]
    bounds: tuple[float, float, float, float]
    max_non_orthogonality: float | None = None

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "block": self.block,
            "count": self.count,
            "centroid": list(self.centroid),
            "bounds": list(self.bounds),
        }
        if self.max_non_orthogonality is not None:
            data["max_non_orthogonality"] = self.max_non_orthogonality
        return data


@dataclass(frozen=True)
class SetReport:
    """One checkMesh set mapped to BlockDrawer blocks, worst block first."""

    name: str
    kind: str
    count: int
    blocks: tuple[BlockHits, ...]
    unmapped: int = 0
    max_non_orthogonality: float | None = None

    def to_data(self, worst: int | None = None) -> dict[str, Any]:
        shown = self.blocks if worst is None else self.blocks[: max(0, worst)]
        data: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "count": self.count,
            "block_count": len(self.blocks),
            "blocks": [hits.to_data() for hits in shown],
        }
        if len(shown) < len(self.blocks):
            data["blocks_omitted"] = len(self.blocks) - len(shown)
        if self.unmapped:
            data["unmapped"] = self.unmapped
        if self.max_non_orthogonality is not None:
            data["max_non_orthogonality"] = self.max_non_orthogonality
        return data


@dataclass
class _Accumulator:
    count: int = 0
    sum_x: float = 0.0
    sum_y: float = 0.0
    bounds: list[float] = field(
        default_factory=lambda: [math.inf, math.inf, -math.inf, -math.inf]
    )
    max_non_orthogonality: float | None = None

    def add(self, centre: tuple[float, float], points: Iterable[Vector]) -> None:
        self.count += 1
        self.sum_x += centre[0]
        self.sum_y += centre[1]
        for x, y, _z in points:
            self.bounds[0] = min(self.bounds[0], x)
            self.bounds[1] = min(self.bounds[1], y)
            self.bounds[2] = max(self.bounds[2], x)
            self.bounds[3] = max(self.bounds[3], y)


def block_cell_offsets(model: MeshModel) -> tuple[list[int], list[str], int]:
    """First cell index of every block in blockMesh's numbering, and the total."""
    starts: list[int] = []
    names: list[str] = []
    total = 0
    for block in model.blocks:
        nx, ny, nz = model.block_cell_counts(block)
        starts.append(total)
        names.append(block.id)
        total += nx * ny * nz
    return starts, names, total


def written_set_names(check_mesh_log: str) -> list[str]:
    """The sets a checkMesh run says it wrote (``<<Writing N ... to set NAME``)."""
    names = re.findall(r"<<Writing\s+\d+\s+.*?\bto set\s+(\S+)", check_mesh_log)
    return list(dict.fromkeys(name.rstrip(".") for name in names))


def locate_sets(
    case_dir: str | Path,
    model: MeshModel,
    names: Sequence[str],
) -> tuple[SetReport, ...]:
    """Map each named set in ``case_dir`` to the model's blocks.

    Faces go to their owner cell's block, cells to their own, and points to
    the block of a cell using them. Every block's hits carry the centroid of
    the offending items' centres and the bounding box of their points in the
    x-y plane. For ``nonOrthoFaces`` each face's non-orthogonality is
    recomputed the way checkMesh does - the angle between the owner-to-
    neighbour centre vector and the face area vector - and the block's
    maximum is reported with it.
    """
    mesh = PolyMesh(Path(case_dir) / "constant" / "polyMesh")
    sets_directory = mesh.directory / "sets"
    starts, block_ids, total = block_cell_offsets(model)
    owner = mesh.owner
    cells_in_mesh = mesh.cell_count
    if cells_in_mesh != total:
        raise PolyMeshError(
            f"the mesh has {cells_in_mesh} cells but the session's blocks give {total}; "
            "the case does not belong to this session"
        )

    def block_of(cell: int) -> str | None:
        if cell < 0 or cell >= total:
            return None
        return block_ids[bisect.bisect_right(starts, cell) - 1]

    reports: list[SetReport] = []
    for name in names:
        kind_class, labels = read_set(sets_directory / name)
        kind = (
            "face" if kind_class == "faceSet" or name in FACE_SETS else
            "cell" if kind_class == "cellSet" or name in CELL_SETS else
            "point" if kind_class == "pointSet" or name in POINT_SETS else
            ""
        )
        if not kind:
            raise PolyMeshError(f"set {name} has unknown class {kind_class!r}")
        labels = sorted(set(labels))
        accumulators: dict[str, _Accumulator] = {}
        unmapped = 0
        set_max: float | None = None
        if kind == "face":
            internal = len(mesh.neighbour)
            orthogonality: dict[int, float] = {}
            if name == "nonOrthoFaces":
                orthogonality = _face_non_orthogonality(mesh, labels, internal)
                if orthogonality:
                    set_max = max(orthogonality.values())
            for face in labels:
                if face >= len(owner):
                    unmapped += 1
                    continue
                block = block_of(owner[face])
                if block is None:
                    unmapped += 1
                    continue
                points = mesh.face_points(face)
                centre = _mean_xy(points)
                entry = accumulators.setdefault(block, _Accumulator())
                entry.add(centre, points)
                value = orthogonality.get(face)
                if value is not None and (
                    entry.max_non_orthogonality is None or value > entry.max_non_orthogonality
                ):
                    entry.max_non_orthogonality = value
        elif kind == "cell":
            faces_of = mesh.cell_faces(labels)
            for cell in labels:
                block = block_of(cell)
                faces = faces_of.get(cell) or []
                if block is None or not faces:
                    unmapped += 1
                    continue
                points = list({
                    label: mesh.point(label)
                    for face, _owned in faces for label in mesh.face(face)
                }.values())
                accumulators.setdefault(block, _Accumulator()).add(_mean_xy(points), points)
        else:
            wanted = set(labels)
            cell_of_point: dict[int, int] = {}
            for face in range(mesh.face_count):
                if len(cell_of_point) == len(wanted):
                    break
                for label in mesh.face(face):
                    if label in wanted and label not in cell_of_point:
                        cell_of_point[label] = owner[face]
            for point in labels:
                cell = cell_of_point.get(point)
                block = None if cell is None else block_of(cell)
                if block is None:
                    unmapped += 1
                    continue
                location = mesh.point(point)
                accumulators.setdefault(block, _Accumulator()).add(
                    (location[0], location[1]), [location]
                )
        hits = [
            BlockHits(
                block,
                entry.count,
                (entry.sum_x / entry.count, entry.sum_y / entry.count),
                tuple(entry.bounds),  # type: ignore[arg-type]
                entry.max_non_orthogonality,
            )
            for block, entry in accumulators.items()
        ]
        order = {identifier: index for index, identifier in enumerate(block_ids)}
        hits.sort(key=lambda item: (
            -item.count,
            -(item.max_non_orthogonality or 0.0),
            order.get(item.block, 0),
        ))
        reports.append(SetReport(name, kind, len(labels), tuple(hits), unmapped, set_max))
    return tuple(reports)


def _mean_xy(points: Sequence[Vector]) -> tuple[float, float]:
    count = len(points)
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
    )


def _face_non_orthogonality(
    mesh: PolyMesh, faces: Sequence[int], internal: int
) -> dict[int, float]:
    """checkMesh's non-orthogonality in degrees for each internal face given."""
    owner = mesh.owner
    neighbour = mesh.neighbour
    internal_faces = [face for face in faces if face < internal]
    cells = {owner[face] for face in internal_faces} | {neighbour[face] for face in internal_faces}
    faces_of = mesh.cell_faces(cells)
    centres = {cell: mesh.cell_centre(faces_of[cell]) for cell in cells if faces_of[cell]}
    result: dict[int, float] = {}
    for face in internal_faces:
        own = centres.get(owner[face])
        nei = centres.get(neighbour[face])
        if own is None or nei is None:
            continue
        _centre, area = mesh.face_centre_and_area(face)
        delta = _sub(nei, own)
        norms = math.sqrt(_dot(delta, delta) * _dot(area, area))
        if norms <= 0.0:
            continue
        cosine = max(-1.0, min(1.0, _dot(delta, area) / norms))
        result[face] = math.degrees(math.acos(cosine))
    return result
