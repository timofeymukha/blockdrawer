"""UI-independent topology model for BlockDrawer."""

from __future__ import annotations

from collections import deque
import colorsys
from dataclasses import dataclass
import math
import re
import sys
from typing import Iterable, TypeAlias


class TopologyError(ValueError):
    """Raised when an operation would create an invalid block topology."""


@dataclass
class Vertex:
    id: str
    x: float
    y: float


@dataclass(frozen=True)
class Block:
    id: str
    vertices: tuple[str, str, str, str]

    def directed_edge(self, index: int) -> tuple[str, str]:
        return self.vertices[index], self.vertices[(index + 1) % 4]


@dataclass(frozen=True)
class EdgeGeometry:
    """Optional geometry attached to a topological edge.

    Straight edges are implicit and therefore absent from ``edge_geometry``.
    Keeping interpolation points as an ordered tuple leaves room for OpenFOAM
    spline and polyline edge types without changing the topology representation.
    """

    kind: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class EdgeGradingValues:
    """Equivalent grading representations for one directed edge."""

    length: float
    cell_ratio: float
    total_ratio: float
    start_width: float
    end_width: float


@dataclass(frozen=True)
class Boundary:
    """One named OpenFOAM boundary patch and its display metadata."""

    name: str
    kind: str
    color: str
    neighbour_patch: str | None = None


EdgeKey: TypeAlias = tuple[str, str]
EdgeOccurrence: TypeAlias = tuple[Block, int, tuple[str, str]]


def edge_key(first: str, second: str) -> EdgeKey:
    """Return the canonical identity of an undirected topological edge."""
    if first == second:
        raise TopologyError("An edge must connect two distinct vertices")
    return (first, second) if first < second else (second, first)


def _finite_expansion_ratio(logarithm: float) -> float:
    minimum = math.log(sys.float_info.min)
    maximum = math.log(sys.float_info.max)
    if not math.isfinite(logarithm) or not minimum <= logarithm <= maximum:
        raise TopologyError(
            "That grading is too extreme to represent as an OpenFOAM ratio"
        )
    ratio = math.exp(logarithm)
    if not math.isfinite(ratio) or ratio <= 0.0 \
            or not math.isfinite(1.0 / ratio):
        raise TopologyError(
            "That grading is too extreme to represent as an OpenFOAM ratio"
        )
    return ratio


def _log_geometric_sum(log_ratio: float, cells: int) -> float:
    """Return log(sum(exp(i*log_ratio), i=0..cells-1)) stably."""
    if log_ratio == 0.0:
        return math.log(cells)
    if log_ratio < 0.0:
        return math.log(-math.expm1(cells * log_ratio)) \
            - math.log(-math.expm1(log_ratio))

    def log_expm1(value: float) -> float:
        if value > 50.0:
            return value + math.log1p(-math.exp(-value))
        return math.log(math.expm1(value))

    return log_expm1(cells * log_ratio) - log_expm1(log_ratio)


def _cell_ratio_log_from_start_width(
    length: float, cells: int, start_width: float
) -> float:
    if not math.isfinite(start_width) or not 0.0 < start_width < length:
        raise TopologyError(
            "A start or end width must be positive and smaller than the "
            "edge length when there is more than one cell"
        )
    uniform_width = length / cells
    if math.isclose(
        start_width, uniform_width, rel_tol=1.0e-12, abs_tol=1.0e-15
    ):
        return 0.0

    target = math.log(length / start_width)
    low = -1.0
    while _log_geometric_sum(low, cells) > target:
        low *= 2.0
    high = 1.0
    while _log_geometric_sum(high, cells) < target:
        high *= 2.0
    for _ in range(120):
        middle = (low + high) / 2.0
        if _log_geometric_sum(middle, cells) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _grading_from_total_ratio(
    length: float, cells: int, total_ratio: float
) -> tuple[float, float, float]:
    """Return cell ratio, start width and end width."""
    if not math.isfinite(length) or length <= 0.0:
        raise TopologyError("An edge must have positive finite length")
    if not math.isfinite(total_ratio) or total_ratio <= 0.0 \
            or not math.isfinite(1.0 / total_ratio):
        raise TopologyError("The total expansion ratio must be positive and finite")
    if cells == 1:
        if total_ratio != 1.0:
            raise TopologyError("A one-cell edge can only use uniform grading")
        return 1.0, length, length

    log_cell_ratio = math.log(total_ratio) / (cells - 1)
    if abs(log_cell_ratio) <= 1.0e-14:
        width = length / cells
        return 1.0, width, width

    cell_ratio = math.exp(log_cell_ratio)
    if log_cell_ratio > 0.0:
        end_width = length * (
            -math.expm1(-log_cell_ratio)
        ) / (-math.expm1(-cells * log_cell_ratio))
        start_width = end_width / total_ratio
    else:
        start_width = length * math.expm1(log_cell_ratio) / math.expm1(
            cells * log_cell_ratio
        )
        end_width = start_width * total_ratio
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (cell_ratio, start_width, end_width)
    ):
        raise TopologyError(
            "That grading is too extreme to produce finite cell widths"
        )
    return cell_ratio, start_width, end_width


class MeshModel:
    """A conformal set of quadrilateral blocks with optional curved edges.

    ``edge_cells`` stores the number of intervals along an edge. Canvas markers
    therefore show ``edge_cells - 1`` interior mesh nodes; OpenFOAM receives the
    interval count directly as its block cell count.
    """

    DEFAULT_EDGE_CELLS = 10
    COORDINATE_TOLERANCE = 1.0e-9
    SUPPORTED_EDGE_TYPES = ("line", "arc", "polyLine", "spline")
    MULTI_POINT_EDGE_TYPES = ("polyLine", "spline")
    DEFAULT_CONTROL_POINT_OFFSET_RATIO = 0.2
    GRADING_PARAMETERS = (
        "cell_ratio",
        "total_ratio",
        "start_width",
        "end_width",
    )
    SPLINE_LENGTH_SAMPLES = 512
    SUPPORTED_BOUNDARY_TYPES = ("patch", "symmetry", "wall", "cyclic", "empty")
    BOUNDARY_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    BOUNDARY_COLORS = (
        "#d9485f",
        "#2f9e44",
        "#7b2cbf",
        "#e67700",
        "#0b7285",
        "#c2255c",
        "#5f3dc4",
        "#2b8a3e",
        "#a61e4d",
        "#1864ab",
        "#9c36b5",
        "#087f5b",
    )

    def __init__(self, *, initialize: bool = True) -> None:
        self.vertices: dict[str, Vertex] = {}
        self.blocks: list[Block] = []
        self.edge_cells: dict[EdgeKey, int] = {}
        self.edge_geometry: dict[EdgeKey, EdgeGeometry] = {}
        # Non-uniform total end/start ratios in canonical EdgeKey direction.
        # Uniform grading is implicit, like straight edge geometry.
        self.edge_grading: dict[EdgeKey, float] = {}
        self.boundaries: dict[str, Boundary] = {}
        self.edge_boundaries: dict[EdgeKey, str] = {}
        self.z_cells = 1
        self.z_min = 0.0
        self.z_max = 1.0
        self.scale = 1.0

        if initialize:
            self._create_initial_block()

    def _create_initial_block(self) -> None:
        self.vertices = {
            "v0": Vertex("v0", 0.0, 0.0),
            "v1": Vertex("v1", 1.0, 0.0),
            "v2": Vertex("v2", 1.0, 1.0),
            "v3": Vertex("v3", 0.0, 1.0),
        }
        self.blocks = [Block("b0", ("v0", "v1", "v2", "v3"))]
        self.edge_cells = {
            edge: self.DEFAULT_EDGE_CELLS for edge in self.edges()
        }
        self.edge_geometry = {}
        self.edge_grading = {}
        self.boundaries = {}
        self.edge_boundaries = {}

    def edges(self) -> list[EdgeKey]:
        """Return unique edges in stable block/local-edge order."""
        result: list[EdgeKey] = []
        seen: set[EdgeKey] = set()
        for block in self.blocks:
            for index in range(4):
                current = edge_key(*block.directed_edge(index))
                if current not in seen:
                    seen.add(current)
                    result.append(current)
        return result

    def edge_occurrences(self) -> dict[EdgeKey, list[EdgeOccurrence]]:
        result: dict[EdgeKey, list[EdgeOccurrence]] = {}
        for block in self.blocks:
            for index in range(4):
                directed = block.directed_edge(index)
                result.setdefault(edge_key(*directed), []).append(
                    (block, index, directed)
                )
        return result

    def is_boundary_edge(self, edge: EdgeKey) -> bool:
        occurrences = self.edge_occurrences().get(edge, [])
        return len(occurrences) == 1

    def add_boundary(self, name: str) -> Boundary:
        """Add an unassigned named patch with a unique display color."""
        self._validate_boundary_name(name)
        if name in self.boundaries:
            raise TopologyError(f"Boundary {name!r} already exists")
        boundary = Boundary(name, "patch", self._next_boundary_color())
        self.boundaries[name] = boundary
        self.validate()
        return boundary

    def remove_boundary(self, name: str) -> None:
        """Remove a patch, its assignments, and any cyclic pairing."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        self._detach_cyclic_boundary(name)
        del self.boundaries[name]
        self.edge_boundaries = {
            current: boundary_name
            for current, boundary_name in self.edge_boundaries.items()
            if boundary_name != name
        }
        self.validate()

    def set_boundary_type(
        self,
        name: str,
        kind: str,
        *,
        neighbour_patch: str | None = None,
    ) -> set[str]:
        """Set a patch type, pairing cyclic patches atomically and reciprocally."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        if kind not in self.SUPPORTED_BOUNDARY_TYPES:
            raise TopologyError(f"Unsupported boundary type {kind!r}")
        if kind == "cyclic":
            if neighbour_patch is None:
                raise TopologyError("A cyclic boundary needs a neighbouring patch")
            if neighbour_patch == name:
                raise TopologyError("A cyclic boundary cannot neighbour itself")
            if neighbour_patch not in self.boundaries:
                raise TopologyError(
                    f"Unknown neighbouring patch {neighbour_patch!r}"
                )

        previous = dict(self.boundaries)
        try:
            affected = {name}
            affected.update(self._detach_cyclic_boundary(name))
            if kind == "cyclic":
                assert neighbour_patch is not None
                affected.add(neighbour_patch)
                affected.update(self._detach_cyclic_boundary(neighbour_patch))
                first = self.boundaries[name]
                second = self.boundaries[neighbour_patch]
                self.boundaries[name] = Boundary(
                    name, "cyclic", first.color, neighbour_patch
                )
                self.boundaries[neighbour_patch] = Boundary(
                    neighbour_patch, "cyclic", second.color, name
                )
            else:
                current = self.boundaries[name]
                self.boundaries[name] = Boundary(name, kind, current.color)
            self.validate()
            return affected
        except Exception:
            self.boundaries = previous
            raise

    def set_edge_boundary(self, edge: EdgeKey, name: str | None) -> None:
        """Assign an exterior topological edge to at most one named patch."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if not self.is_boundary_edge(current):
            raise TopologyError("Only exterior edges can be assigned to a boundary")
        if name is None:
            self.edge_boundaries.pop(current, None)
        else:
            if name not in self.boundaries:
                raise TopologyError(f"Unknown boundary {name!r}")
            self.edge_boundaries[current] = name
        self.validate()

    def boundary_edges(self, name: str) -> list[EdgeKey]:
        """Return a patch's assigned edges in stable topology order."""
        if name not in self.boundaries:
            raise TopologyError(f"Unknown boundary {name!r}")
        return [
            current for current in self.edges()
            if self.edge_boundaries.get(current) == name
        ]

    def _detach_cyclic_boundary(self, name: str) -> set[str]:
        """Turn an existing cyclic pair back into ordinary patches."""
        current = self.boundaries[name]
        affected: set[str] = set()
        neighbour = current.neighbour_patch
        if current.kind == "cyclic" and neighbour in self.boundaries:
            other = self.boundaries[neighbour]
            if other.kind == "cyclic" and other.neighbour_patch == name:
                self.boundaries[neighbour] = Boundary(
                    neighbour, "patch", other.color
                )
                affected.add(neighbour)
        self.boundaries[name] = Boundary(name, "patch", current.color)
        return affected

    def _next_boundary_color(self) -> str:
        used = {boundary.color.lower() for boundary in self.boundaries.values()}
        for color in self.BOUNDARY_COLORS:
            if color.lower() not in used:
                return color
        index = len(self.boundaries)
        while True:
            hue = (index * 0.6180339887498949) % 1.0
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.78)
            color = (
                f"#{round(red * 255):02x}{round(green * 255):02x}"
                f"{round(blue * 255):02x}"
            )
            if color not in used:
                return color
            index += 1

    def edge_constraint_component(self, selected: EdgeKey) -> set[EdgeKey]:
        """Find every edge whose count is constrained to equal ``selected``."""
        return set(self._edge_constraint_orientations(selected))

    def _edge_constraint_orientations(
        self, selected: EdgeKey
    ) -> dict[EdgeKey, bool]:
        """Return linked edges and whether canonical grading must be reversed."""
        selected = edge_key(*selected)
        all_edges = self.edges()
        if selected not in set(all_edges):
            raise TopologyError(f"Unknown edge {selected!r}")

        adjacency: dict[EdgeKey, list[tuple[EdgeKey, bool]]] = {
            current: [] for current in all_edges
        }
        for block in self.blocks:
            first, second, third, fourth = block.vertices
            pairs = (
                ((first, second), (fourth, third)),
                ((second, third), (first, fourth)),
            )
            for first_direction, second_direction in pairs:
                first_edge = edge_key(*first_direction)
                second_edge = edge_key(*second_direction)
                first_reversed = first_edge != first_direction
                second_reversed = second_edge != second_direction
                reverse = first_reversed != second_reversed
                adjacency[first_edge].append((second_edge, reverse))
                adjacency[second_edge].append((first_edge, reverse))

        orientations = {selected: False}
        pending = deque([selected])
        while pending:
            current = pending.popleft()
            for neighbor, reverse in adjacency[current]:
                expected = orientations[current] != reverse
                if neighbor in orientations:
                    if orientations[neighbor] != expected:
                        raise TopologyError(
                            "Edge-count constraints have inconsistent directions"
                        )
                    continue
                orientations[neighbor] = expected
                pending.append(neighbor)
        return orientations

    def set_edge_cells(self, edge: EdgeKey, cells: int) -> set[EdgeKey]:
        """Set an edge count and propagate it through all block constraints."""
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            raise TopologyError("The edge cell count must be a positive integer")
        affected = self.edge_constraint_component(edge)
        for current in affected:
            self.edge_cells[current] = cells
            if cells == 1:
                self.edge_grading.pop(current, None)
        return affected

    def edge_total_expansion(self, edge: EdgeKey) -> float:
        """Return the end/start cell-width ratio in canonical edge direction."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        return self.edge_grading.get(current, 1.0)

    def edge_expansion_in_direction(self, first: str, second: str) -> float:
        """Return total expansion when traversing ``first`` to ``second``."""
        current = edge_key(first, second)
        ratio = self.edge_total_expansion(current)
        return ratio if current == (first, second) else 1.0 / ratio

    def _set_edge_expansion_in_direction(
        self, first: str, second: str, ratio: float
    ) -> None:
        """Store ``ratio`` for a directed edge without validating the model."""
        current = edge_key(first, second)
        canonical_ratio = ratio if current == (first, second) else 1.0 / ratio
        if canonical_ratio == 1.0:
            self.edge_grading.pop(current, None)
        else:
            self.edge_grading[current] = canonical_ratio

    def edge_length(self, edge: EdgeKey) -> float:
        """Return the geometric edge length in unscaled drawing units."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return math.hypot(second.x - first.x, second.y - first.y)
        if geometry.kind == "arc":
            _, _, radius, _, sweep = self._arc_circle(current, geometry)
            return radius * abs(sweep)
        if geometry.kind == "polyLine":
            path = [
                (first.x, first.y),
                *geometry.points,
                (second.x, second.y),
            ]
            return sum(
                math.hypot(end[0] - start[0], end[1] - start[1])
                for start, end in zip(path, path[1:])
            )

        previous = self.edge_point(current, 0.0)
        length = 0.0
        for index in range(1, self.SPLINE_LENGTH_SAMPLES + 1):
            point = self.edge_point(
                current, index / self.SPLINE_LENGTH_SAMPLES
            )
            length += math.hypot(
                point[0] - previous[0], point[1] - previous[1]
            )
            previous = point
        return length

    def edge_grading_values(self, edge: EdgeKey) -> EdgeGradingValues:
        """Return all equivalent grading inputs in canonical edge direction."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        length = self.edge_length(current)
        total_ratio = self.edge_total_expansion(current)
        cell_ratio, start_width, end_width = _grading_from_total_ratio(
            length, cells, total_ratio
        )
        return EdgeGradingValues(
            length,
            cell_ratio,
            total_ratio,
            start_width,
            end_width,
        )

    def set_edge_grading(
        self,
        edge: EdgeKey,
        parameter: str,
        value: float,
        *,
        propagate: bool = False,
    ) -> EdgeGradingValues:
        """Set grading from any representation and optionally sweep linked edges."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        if parameter not in self.GRADING_PARAMETERS:
            raise TopologyError(f"Unknown grading parameter {parameter!r}")
        if not math.isfinite(value) or value <= 0.0:
            raise TopologyError("Grading values must be positive and finite")

        length = self.edge_length(current)
        if cells == 1:
            expected = length if parameter in ("start_width", "end_width") else 1.0
            if not math.isclose(value, expected, rel_tol=1.0e-10, abs_tol=1.0e-12):
                raise TopologyError(
                    "A one-cell edge can only use uniform grading"
                )
            affected = (
                self._edge_constraint_orientations(current)
                if propagate else {current: False}
            )
            for affected_edge in affected:
                self.edge_grading.pop(affected_edge, None)
            return self.edge_grading_values(current)

        if parameter == "total_ratio":
            total_ratio = float(value)
        elif parameter == "cell_ratio":
            logarithm = (cells - 1) * math.log(value)
            total_ratio = _finite_expansion_ratio(logarithm)
        elif parameter == "start_width":
            log_cell_ratio = _cell_ratio_log_from_start_width(
                length, cells, value
            )
            total_ratio = _finite_expansion_ratio(
                (cells - 1) * log_cell_ratio
            )
        else:
            reverse_log_cell_ratio = _cell_ratio_log_from_start_width(
                length, cells, value
            )
            total_ratio = _finite_expansion_ratio(
                -(cells - 1) * reverse_log_cell_ratio
            )

        _grading_from_total_ratio(length, cells, total_ratio)
        orientations = (
            self._edge_constraint_orientations(current)
            if propagate else {current: False}
        )
        previous = dict(self.edge_grading)
        try:
            for affected_edge, reverse in orientations.items():
                ratio = 1.0 / total_ratio if reverse else total_ratio
                if ratio == 1.0:
                    self.edge_grading.pop(affected_edge, None)
                else:
                    self.edge_grading[affected_edge] = ratio
            self.validate()
        except Exception:
            self.edge_grading = previous
            raise
        return self.edge_grading_values(current)

    def edge_node_fraction(self, edge: EdgeKey, node_index: int) -> float:
        """Return one interior node's graded fraction in canonical direction."""
        current = edge_key(*edge)
        cells = self.edge_cells.get(current)
        if cells is None:
            raise TopologyError(f"Unknown edge {current!r}")
        if isinstance(node_index, bool) or not isinstance(node_index, int) \
                or not 0 <= node_index <= cells:
            raise TopologyError("Edge node index is out of range")
        if node_index == 0:
            return 0.0
        if node_index == cells:
            return 1.0
        total_ratio = self.edge_total_expansion(current)
        if total_ratio == 1.0:
            return node_index / cells
        log_cell_ratio = math.log(total_ratio) / (cells - 1)
        if abs(log_cell_ratio) <= 1.0e-14:
            return node_index / cells
        if log_cell_ratio < 0.0:
            return math.expm1(node_index * log_cell_ratio) / math.expm1(
                cells * log_cell_ratio
            )
        return (
            math.exp((node_index - cells) * log_cell_ratio)
            * (-math.expm1(-node_index * log_cell_ratio))
            / (-math.expm1(-cells * log_cell_ratio))
        )

    def edge_type(self, edge: EdgeKey) -> str:
        """Return the OpenFOAM geometry type for ``edge``."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        geometry = self.edge_geometry.get(current)
        return geometry.kind if geometry is not None else "line"

    def set_edge_type(self, edge: EdgeKey, kind: str) -> None:
        """Change an edge between the supported OpenFOAM geometry types.

        A new curved edge receives a deterministic interpolation point offset
        outward from the first incident block.
        """
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if kind not in self.SUPPORTED_EDGE_TYPES:
            raise TopologyError(f"Unsupported edge type {kind!r}")
        if self.edge_type(current) == kind:
            return
        if kind == "line":
            self.edge_geometry.pop(current, None)
            return

        previous = self.edge_geometry.get(current)
        geometry = EdgeGeometry(kind, (self._default_edge_point(current),))
        self.edge_geometry[current] = geometry
        try:
            self._validate_edge_geometry(current, geometry)
        except (TopologyError, ValueError):
            if previous is None:
                self.edge_geometry.pop(current, None)
            else:
                self.edge_geometry[current] = previous
            raise

    def edge_control_points(
        self, edge: EdgeKey
    ) -> tuple[tuple[float, float], ...]:
        """Return the ordered interpolation points for a non-line edge."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        geometry = self.edge_geometry.get(current)
        return geometry.points if geometry is not None else ()

    def arc_point(self, edge: EdgeKey) -> tuple[float, float]:
        """Return the single interpolation point for an arc edge."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind != "arc":
            raise TopologyError(f"Edge {current!r} is not an arc")
        return geometry.points[0]

    def set_arc_point(self, edge: EdgeKey, x: float, y: float) -> None:
        """Move an arc interpolation point, rolling back invalid geometry."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind != "arc":
            raise TopologyError(f"Edge {current!r} is not an arc")
        self.set_edge_control_point(current, 0, x, y)

    def set_edge_control_point(
        self, edge: EdgeKey, index: int, x: float, y: float
    ) -> None:
        """Move one interpolation point, rolling back invalid geometry."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            raise TopologyError(f"Edge {current!r} has no interpolation points")
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Interpolation point coordinates must be finite")
        previous = geometry
        points = list(geometry.points)
        points[index] = (float(x), float(y))
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self.edge_geometry[current] = replacement
        try:
            self._validate_edge_geometry(current, replacement)
        except (TopologyError, ValueError):
            self.edge_geometry[current] = previous
            raise

    def add_edge_control_point(
        self, edge: EdgeKey, after_index: int | None = None
    ) -> int:
        """Insert a point after ``after_index`` and return its new index."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        if after_index is None:
            after_index = len(geometry.points) - 1
        if isinstance(after_index, bool) or not isinstance(after_index, int) \
                or not 0 <= after_index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")

        points = list(geometry.points)
        left = points[after_index]
        right = (
            points[after_index + 1]
            if after_index + 1 < len(points)
            else (
                self.vertices[current[1]].x,
                self.vertices[current[1]].y,
            )
        )
        inserted = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
        new_index = after_index + 1
        points.insert(new_index, inserted)
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement
        return new_index

    def remove_edge_control_point(self, edge: EdgeKey, index: int) -> None:
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        if len(geometry.points) <= 1:
            raise TopologyError(
                f"A {geometry.kind} needs at least one interpolation point"
            )
        if isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(geometry.points):
            raise TopologyError("Interpolation point index is out of range")
        points = list(geometry.points)
        del points[index]
        replacement = EdgeGeometry(geometry.kind, tuple(points))
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement

    def reset_edge_control_points(self, edge: EdgeKey) -> None:
        """Distribute all interpolation points evenly along the edge chord."""
        current = edge_key(*edge)
        geometry = self.edge_geometry.get(current)
        if geometry is None or geometry.kind not in self.MULTI_POINT_EDGE_TYPES:
            raise TopologyError(
                f"Edge {current!r} does not use a point-list geometry"
            )
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        denominator = len(geometry.points) + 1
        points = tuple(
            (
                first.x + (index / denominator) * (second.x - first.x),
                first.y + (index / denominator) * (second.y - first.y),
            )
            for index in range(1, denominator)
        )
        replacement = EdgeGeometry(geometry.kind, points)
        self._validate_edge_geometry(current, replacement)
        self.edge_geometry[current] = replacement

    def edge_point(self, edge: EdgeKey, fraction: float) -> tuple[float, float]:
        """Return a point at ``fraction`` along any supported edge type."""
        current = edge_key(*edge)
        if current not in self.edge_cells:
            raise TopologyError(f"Unknown edge {current!r}")
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise TopologyError("Edge fraction must be between 0 and 1")
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        if fraction == 0.0:
            return first.x, first.y
        if fraction == 1.0:
            return second.x, second.y
        geometry = self.edge_geometry.get(current)
        if geometry is None:
            return (
                first.x + fraction * (second.x - first.x),
                first.y + fraction * (second.y - first.y),
            )

        if geometry.kind == "arc":
            center_x, center_y, radius, start_angle, sweep = self._arc_circle(
                current, geometry
            )
            angle = start_angle + fraction * sweep
            return (
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
            )
        if geometry.kind == "spline":
            return self._spline_point(current, geometry, fraction)
        return self._polyline_point(current, geometry, fraction)

    def set_z_cells(self, cells: int) -> None:
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            raise TopologyError("The z cell count must be a positive integer")
        self.z_cells = cells

    def set_z_extents(self, z_min: float, z_max: float) -> None:
        if not (math.isfinite(z_min) and math.isfinite(z_max)):
            raise TopologyError("Z extents must be finite")
        if z_max <= z_min:
            raise TopologyError("zMax must be greater than zMin")
        self.z_min = float(z_min)
        self.z_max = float(z_max)

    def move_vertex(self, vertex_id: str, x: float, y: float) -> None:
        if vertex_id not in self.vertices:
            raise TopologyError(f"Unknown vertex {vertex_id!r}")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Vertex coordinates must be finite")

        vertex = self.vertices[vertex_id]
        previous = (vertex.x, vertex.y)
        vertex.x, vertex.y = float(x), float(y)
        try:
            self.validate()
        except (TopologyError, ValueError):
            vertex.x, vertex.y = previous
            raise

    def add_vertex(self, x: float, y: float) -> Vertex:
        """Create a standalone vertex that can later be used by a block."""
        if not (math.isfinite(x) and math.isfinite(y)):
            raise TopologyError("Vertex coordinates must be finite")
        for vertex in self.vertices.values():
            if self._coordinates_match(vertex.x, vertex.y, x, y):
                raise TopologyError(
                    f"A vertex already exists at ({x:g}, {y:g})"
                )

        vertex = Vertex(
            self._next_id("v", self.vertices),
            float(x),
            float(y),
        )
        self.vertices[vertex.id] = vertex
        try:
            self.validate()
        except (TopologyError, ValueError):
            del self.vertices[vertex.id]
            raise
        return vertex

    def add_block(self, selected: EdgeKey) -> Block:
        """Append a block along a boundary edge's outward normal."""
        selected = edge_key(*selected)
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            raise TopologyError(f"Unknown edge {selected!r}")
        if len(occurrences) != 1:
            raise TopologyError("A block can only be added to a boundary edge")

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        grading_before = dict(self.edge_grading)
        edge_boundaries_before = dict(self.edge_boundaries)

        try:
            source, index, directed = occurrences[0]
            first_id, second_id = directed
            previous_id = source.vertices[(index - 1) % 4]
            next_id = source.vertices[(index + 2) % 4]
            first = self.vertices[first_id]
            second = self.vertices[second_id]
            previous = self.vertices[previous_id]
            following = self.vertices[next_id]

            edge_x = second.x - first.x
            edge_y = second.y - first.y
            edge_length = math.hypot(edge_x, edge_y)
            if edge_length <= self.COORDINATE_TOLERANCE:
                raise TopologyError("Cannot extrude a zero-length edge")

            # Counter-clockwise blocks have their interior to the left of each
            # directed edge. The right normal therefore points outward. Use the
            # average perpendicular thickness of the source block so both new
            # vertices receive exactly the same normal displacement.
            inward_x = -edge_y / edge_length
            inward_y = edge_x / edge_length
            outward_x = -inward_x
            outward_y = -inward_y
            first_thickness = (
                (previous.x - first.x) * inward_x
                + (previous.y - first.y) * inward_y
            )
            second_thickness = (
                (following.x - second.x) * inward_x
                + (following.y - second.y) * inward_y
            )
            extrusion_distance = (first_thickness + second_thickness) / 2.0
            if extrusion_distance <= self.COORDINATE_TOLERANCE:
                raise TopologyError("Source block has no positive normal thickness")

            new_first = self._vertex_at_or_new(
                first.x + outward_x * extrusion_distance,
                first.y + outward_y * extrusion_distance,
            )
            new_second = self._vertex_at_or_new(
                second.x + outward_x * extrusion_distance,
                second.y + outward_y * extrusion_distance,
            )
            new_block = Block(
                self._next_id("b", (block.id for block in self.blocks)),
                (second_id, first_id, new_first.id, new_second.id),
            )
            if any(set(block.vertices) == set(new_block.vertices)
                   for block in self.blocks):
                raise TopologyError("That block already exists")

            shared_cells = cells_before[selected]
            side_edge = edge_key(previous_id, first_id)
            side_cells = cells_before[side_edge]
            self.blocks.append(new_block)
            for current in self.edges():
                self.edge_cells.setdefault(current, self.DEFAULT_EDGE_CELLS)

            new_edges = [
                edge_key(*new_block.directed_edge(i)) for i in range(4)
            ]
            source_boundary = edge_boundaries_before.get(selected)
            self.edge_cells[new_edges[0]] = shared_cells
            self.edge_cells[new_edges[2]] = shared_cells
            self.edge_cells[new_edges[1]] = side_cells
            self.edge_cells[new_edges[3]] = side_cells
            if new_edges[2] not in cells_before:
                source_ratio = self.edge_expansion_in_direction(
                    first_id, second_id
                )
                self._set_edge_expansion_in_direction(
                    new_first.id, new_second.id, source_ratio
                )
            self.set_edge_cells(selected, shared_cells)
            self.set_edge_cells(new_edges[1], side_cells)
            self._prune_boundary_assignments()
            if source_boundary is not None \
                    and self.is_boundary_edge(new_edges[2]) \
                    and new_edges[2] not in self.edge_boundaries:
                self.edge_boundaries[new_edges[2]] = source_boundary
            self.validate()
            return new_block
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_grading = grading_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def add_block_from_vertices(self, vertex_ids: Iterable[str]) -> Block:
        """Create a block from four existing vertices supplied in any order."""
        identifiers = list(vertex_ids)
        if len(identifiers) != 4 or len(set(identifiers)) != 4:
            raise TopologyError("Select four distinct vertices for the new block")
        unknown = [
            identifier for identifier in identifiers
            if identifier not in self.vertices
        ]
        if unknown:
            raise TopologyError(f"Unknown vertices: {', '.join(unknown)}")

        ordered = self._counter_clockwise_vertices(identifiers)
        new_block = Block(
            self._next_id("b", (block.id for block in self.blocks)),
            ordered,
        )
        if any(set(block.vertices) == set(new_block.vertices)
               for block in self.blocks):
            raise TopologyError("That block already exists")
        self._validate_convex_ccw(new_block)

        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        grading_before = dict(self.edge_grading)
        edge_boundaries_before = dict(self.edge_boundaries)
        try:
            self.blocks.append(new_block)
            new_edges = [
                edge_key(*new_block.directed_edge(index))
                for index in range(4)
            ]
            for current in new_edges:
                self.edge_cells.setdefault(current, self.DEFAULT_EDGE_CELLS)

            # Existing edge counts take precedence over defaults. Adding the
            # block merges each opposite-edge constraint component, so use the
            # first existing count in each pair and propagate it atomically.
            for first, second in ((new_edges[0], new_edges[2]),
                                  (new_edges[1], new_edges[3])):
                if first in cells_before:
                    cells = cells_before[first]
                elif second in cells_before:
                    cells = cells_before[second]
                else:
                    cells = self.DEFAULT_EDGE_CELLS
                self.set_edge_cells(first, cells)

            self._prune_boundary_assignments()
            self.validate()
            return new_block
        except Exception:
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_grading = grading_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def remove_edge(self, selected: EdgeKey) -> list[Block]:
        """Remove an edge and every block incident to it.

        Edges are derived topology. Corners belonging to removed blocks are
        pruned when no surviving block uses them, while unrelated standalone
        vertices are preserved. At least one block must remain.
        """
        selected = edge_key(*selected)
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            raise TopologyError(f"Unknown edge {selected!r}")

        vertices_before = dict(self.vertices)
        blocks_before = list(self.blocks)
        cells_before = dict(self.edge_cells)
        geometry_before = dict(self.edge_geometry)
        grading_before = dict(self.edge_grading)
        edge_boundaries_before = dict(self.edge_boundaries)
        removed_ids = {occurrence[0].id for occurrence in occurrences}
        if len(removed_ids) >= len(self.blocks):
            raise TopologyError("At least one block must remain in the topology")
        removed = [
            block for block in self.blocks if block.id in removed_ids
        ]

        try:
            self.blocks = [
                block for block in self.blocks if block.id not in removed_ids
            ]
            used_vertices = {
                vertex_id
                for block in self.blocks
                for vertex_id in block.vertices
            }
            removed_vertices = {
                vertex_id
                for block in removed
                for vertex_id in block.vertices
            }
            # Preserve standalone vertices unrelated to the deleted blocks.
            # Only newly orphaned corners from those blocks are pruned.
            pruned_vertices = removed_vertices - used_vertices
            self.vertices = {
                vertex_id: vertex
                for vertex_id, vertex in self.vertices.items()
                if vertex_id not in pruned_vertices
            }
            surviving_edges = set(self.edges())
            self.edge_cells = {
                current: cells
                for current, cells in self.edge_cells.items()
                if current in surviving_edges
            }
            self.edge_geometry = {
                current: geometry
                for current, geometry in self.edge_geometry.items()
                if current in surviving_edges
            }
            self.edge_grading = {
                current: ratio
                for current, ratio in self.edge_grading.items()
                if current in surviving_edges
            }
            self._prune_boundary_assignments()
            self.validate()
            return removed
        except Exception:
            self.vertices = vertices_before
            self.blocks = blocks_before
            self.edge_cells = cells_before
            self.edge_geometry = geometry_before
            self.edge_grading = grading_before
            self.edge_boundaries = edge_boundaries_before
            raise

    def _prune_boundary_assignments(self) -> None:
        """Discard assignments whose edges disappeared or became internal."""
        occurrences = self.edge_occurrences()
        self.edge_boundaries = {
            current: name
            for current, name in self.edge_boundaries.items()
            if len(occurrences.get(current, ())) == 1
        }

    def can_remove_edge(self, selected: EdgeKey) -> bool:
        """Return whether removing an edge would leave at least one block."""
        try:
            selected = edge_key(*selected)
        except (TypeError, TopologyError):
            return False
        occurrences = self.edge_occurrences().get(selected, [])
        if not occurrences:
            return False
        removed_ids = {occurrence[0].id for occurrence in occurrences}
        return len(removed_ids) < len(self.blocks)

    def block_cell_counts(self, block: Block) -> tuple[int, int, int]:
        first = edge_key(*block.directed_edge(0))
        second = edge_key(*block.directed_edge(1))
        return self.edge_cells[first], self.edge_cells[second], self.z_cells

    @classmethod
    def _validate_boundary_name(cls, name: str) -> None:
        if not isinstance(name, str) \
                or cls.BOUNDARY_NAME_PATTERN.fullmatch(name) is None:
            raise TopologyError(
                "A boundary name must start with a letter or underscore and "
                "contain only letters, numbers, and underscores"
            )

    def validate(self) -> None:
        if isinstance(self.z_cells, bool) or not isinstance(self.z_cells, int) \
                or self.z_cells < 1:
            raise TopologyError("The z cell count must be a positive integer")
        if not (math.isfinite(self.z_min) and math.isfinite(self.z_max)) \
                or self.z_max <= self.z_min:
            raise TopologyError("zMax must be greater than zMin")
        if not math.isfinite(self.scale) or self.scale <= 0.0:
            raise TopologyError("Scale must be a positive finite number")

        if not self.blocks:
            raise TopologyError("A topology must contain at least one block")

        for vertex in self.vertices.values():
            if not isinstance(vertex.id, str) or not vertex.id:
                raise TopologyError("Every vertex needs a non-empty string ID")
            if not (math.isfinite(vertex.x) and math.isfinite(vertex.y)):
                raise TopologyError(f"Vertex {vertex.id} has invalid coordinates")

        vertex_values = list(self.vertices.values())
        for index, first in enumerate(vertex_values):
            for second in vertex_values[index + 1:]:
                if self._coordinates_match(first.x, first.y, second.x, second.y):
                    raise TopologyError(
                        f"Vertices {first.id} and {second.id} are coincident"
                    )

        block_ids: set[str] = set()
        block_vertex_sets: set[frozenset[str]] = set()
        for block in self.blocks:
            if not isinstance(block.id, str) or not block.id or block.id in block_ids:
                raise TopologyError(f"Invalid or duplicate block ID {block.id!r}")
            block_ids.add(block.id)
            if len(block.vertices) != 4 or len(set(block.vertices)) != 4:
                raise TopologyError(f"Block {block.id} needs four distinct vertices")
            if any(vertex_id not in self.vertices for vertex_id in block.vertices):
                raise TopologyError(f"Block {block.id} references an unknown vertex")
            signature = frozenset(block.vertices)
            if signature in block_vertex_sets:
                raise TopologyError(f"Block {block.id} duplicates another block")
            block_vertex_sets.add(signature)
            self._validate_convex_ccw(block)

        actual_edges = set(self.edges())
        if set(self.edge_cells) != actual_edges:
            missing = actual_edges - set(self.edge_cells)
            extra = set(self.edge_cells) - actual_edges
            raise TopologyError(
                f"Edge cell data does not match topology (missing={missing}, extra={extra})"
            )
        for current, cells in self.edge_cells.items():
            if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
                raise TopologyError(f"Edge {current!r} has an invalid cell count")

        geometry_edges = set(self.edge_geometry)
        if not geometry_edges.issubset(actual_edges):
            extra = geometry_edges - actual_edges
            raise TopologyError(
                f"Edge geometry references unknown topology edges {extra}"
            )
        for current, geometry in self.edge_geometry.items():
            self._validate_edge_geometry(current, geometry)

        grading_edges = set(self.edge_grading)
        if not grading_edges.issubset(actual_edges):
            extra = grading_edges - actual_edges
            raise TopologyError(
                f"Edge grading references unknown topology edges {extra}"
            )
        for current, total_ratio in self.edge_grading.items():
            if total_ratio == 1.0:
                raise TopologyError(
                    f"Uniform grading on edge {current!r} must be implicit"
                )
            _grading_from_total_ratio(
                self.edge_length(current),
                self.edge_cells[current],
                total_ratio,
            )

        used_colors: set[str] = set()
        for name, boundary in self.boundaries.items():
            self._validate_boundary_name(name)
            if not isinstance(boundary, Boundary) or boundary.name != name:
                raise TopologyError(f"Boundary {name!r} has invalid definition data")
            if boundary.kind not in self.SUPPORTED_BOUNDARY_TYPES:
                raise TopologyError(
                    f"Boundary {name!r} has unsupported type {boundary.kind!r}"
                )
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", boundary.color) is None:
                raise TopologyError(f"Boundary {name!r} has an invalid display color")
            color = boundary.color.lower()
            if color in used_colors:
                raise TopologyError("Boundary display colors must be unique")
            used_colors.add(color)
            if boundary.kind == "cyclic":
                neighbour = boundary.neighbour_patch
                if neighbour is None or neighbour == name \
                        or neighbour not in self.boundaries:
                    raise TopologyError(
                        f"Cyclic boundary {name!r} needs a valid neighbouring patch"
                    )
                partner = self.boundaries[neighbour]
                if partner.kind != "cyclic" or partner.neighbour_patch != name:
                    raise TopologyError(
                        f"Cyclic boundary {name!r} is not paired reciprocally"
                    )
            elif boundary.neighbour_patch is not None:
                raise TopologyError(
                    f"Boundary {name!r} has neighbourPatch but is not cyclic"
                )

        for current, name in self.edge_boundaries.items():
            if current not in actual_edges:
                raise TopologyError(
                    f"Boundary assignment references unknown edge {current!r}"
                )
            if name not in self.boundaries:
                raise TopologyError(
                    f"Boundary assignment references unknown patch {name!r}"
                )
            if not self.is_boundary_edge(current):
                raise TopologyError(
                    f"Internal edge {current!r} cannot be assigned to a boundary"
                )

        for current, occurrences in self.edge_occurrences().items():
            if len(occurrences) > 2:
                raise TopologyError(f"Edge {current!r} is non-manifold")
            if len(occurrences) == 2:
                first_direction = occurrences[0][2]
                second_direction = occurrences[1][2]
                if first_direction != tuple(reversed(second_direction)):
                    raise TopologyError(
                        f"Blocks sharing edge {current!r} overlap or are misoriented"
                    )

        for block in self.blocks:
            block_edges = [edge_key(*block.directed_edge(i)) for i in range(4)]
            if self.edge_cells[block_edges[0]] != self.edge_cells[block_edges[2]]:
                raise TopologyError(f"Block {block.id} has unequal x-edge counts")
            if self.edge_cells[block_edges[1]] != self.edge_cells[block_edges[3]]:
                raise TopologyError(f"Block {block.id} has unequal y-edge counts")

    def _validate_edge_geometry(
        self, current: EdgeKey, geometry: EdgeGeometry
    ) -> None:
        if not isinstance(geometry, EdgeGeometry):
            raise TopologyError(f"Edge {current!r} has invalid geometry data")
        if geometry.kind not in ("arc", *self.MULTI_POINT_EDGE_TYPES):
            raise TopologyError(
                f"Edge {current!r} has unsupported type {geometry.kind!r}"
            )
        if geometry.kind == "arc" and len(geometry.points) != 1:
            raise TopologyError("An arc edge needs exactly one interpolation point")
        if geometry.kind in self.MULTI_POINT_EDGE_TYPES and not geometry.points:
            raise TopologyError(
                f"A {geometry.kind} needs at least one interpolation point"
            )
        for point in geometry.points:
            if len(point) != 2 or not all(math.isfinite(value) for value in point):
                raise TopologyError("Interpolation point coordinates must be finite")
        if geometry.kind == "arc":
            self._arc_circle(current, geometry)
            return

        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        for start, end in zip(path, path[1:]):
            if math.hypot(end[0] - start[0], end[1] - start[1]) \
                    <= self.COORDINATE_TOLERANCE:
                raise TopologyError(
                    f"{geometry.kind} interpolation points must not coincide with "
                    "adjacent points"
                )

    def _default_edge_point(self, current: EdgeKey) -> tuple[float, float]:
        occurrences = self.edge_occurrences()[current]
        first_id, second_id = occurrences[0][2]
        first = self.vertices[first_id]
        second = self.vertices[second_id]
        dx = second.x - first.x
        dy = second.y - first.y
        length = math.hypot(dx, dy)
        if length <= self.COORDINATE_TOLERANCE:
            raise TopologyError("Cannot curve a zero-length edge")
        # Blocks are counter-clockwise, so their interior is left of each
        # directed edge and the right normal points out of the first block.
        midpoint = ((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
        height = length * self.DEFAULT_CONTROL_POINT_OFFSET_RATIO
        return (
            midpoint[0] + (dy / length) * height,
            midpoint[1] - (dx / length) * height,
        )

    def _polyline_point(
        self, current: EdgeKey, geometry: EdgeGeometry, fraction: float
    ) -> tuple[float, float]:
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in zip(path, path[1:])
        ]
        target = fraction * sum(lengths)
        traversed = 0.0
        for index, length in enumerate(lengths):
            if target <= traversed + length or index == len(lengths) - 1:
                local = min(1.0, max(0.0, (target - traversed) / length))
                start = path[index]
                end = path[index + 1]
                return (
                    start[0] + local * (end[0] - start[0]),
                    start[1] + local * (end[1] - start[1]),
                )
            traversed += length
        return path[-1]

    def _spline_point(
        self, current: EdgeKey, geometry: EdgeGeometry, fraction: float
    ) -> tuple[float, float]:
        """Evaluate OpenFOAM's through-point Catmull-Rom spline."""
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        path = [(first.x, first.y), *geometry.points, (second.x, second.y)]
        lengths = [
            math.hypot(end[0] - start[0], end[1] - start[1])
            for start, end in zip(path, path[1:])
        ]
        target = fraction * sum(lengths)
        traversed = 0.0
        segment = len(lengths) - 1
        local = 1.0
        for index, length in enumerate(lengths):
            if target <= traversed + length or index == len(lengths) - 1:
                segment = index
                local = min(1.0, max(0.0, (target - traversed) / length))
                break
            traversed += length

        p0 = path[segment]
        p1 = path[segment + 1]
        before = (
            path[segment - 1]
            if segment > 0
            else (2.0 * p0[0] - p1[0], 2.0 * p0[1] - p1[1])
        )
        after = (
            path[segment + 2]
            if segment + 2 < len(path)
            else (2.0 * p1[0] - p0[0], 2.0 * p1[1] - p0[1])
        )
        local_squared = local * local
        local_cubed = local_squared * local
        return (
            0.5 * (
                2.0 * p0[0]
                + (-before[0] + p1[0]) * local
                + (2.0 * before[0] - 5.0 * p0[0]
                   + 4.0 * p1[0] - after[0]) * local_squared
                + (-before[0] + 3.0 * p0[0]
                   - 3.0 * p1[0] + after[0]) * local_cubed
            ),
            0.5 * (
                2.0 * p0[1]
                + (-before[1] + p1[1]) * local
                + (2.0 * before[1] - 5.0 * p0[1]
                   + 4.0 * p1[1] - after[1]) * local_squared
                + (-before[1] + 3.0 * p0[1]
                   - 3.0 * p1[1] + after[1]) * local_cubed
            ),
        )

    def _arc_circle(
        self, current: EdgeKey, geometry: EdgeGeometry
    ) -> tuple[float, float, float, float, float]:
        """Return center, radius, start angle and signed sweep for an arc."""
        first = self.vertices[current[0]]
        second = self.vertices[current[1]]
        middle_x, middle_y = geometry.points[0]

        # Translate the circumcircle calculation to the first endpoint for
        # better numerical behavior when world coordinates are large.
        bx = middle_x - first.x
        by = middle_y - first.y
        cx = second.x - first.x
        cy = second.y - first.y
        b_squared = bx * bx + by * by
        c_squared = cx * cx + cy * cy
        chord_squared = (cx - bx) ** 2 + (cy - by) ** 2
        scale_squared = max(b_squared, c_squared, chord_squared)
        determinant = 2.0 * (bx * cy - by * cx)
        if scale_squared <= self.COORDINATE_TOLERANCE ** 2 or math.isclose(
            determinant,
            0.0,
            rel_tol=0.0,
            abs_tol=2.0 * self.COORDINATE_TOLERANCE * scale_squared,
        ):
            raise TopologyError(
                "Arc interpolation point must not be collinear with its endpoints"
            )

        relative_center_x = (cy * b_squared - by * c_squared) / determinant
        relative_center_y = (bx * c_squared - cx * b_squared) / determinant
        center_x = first.x + relative_center_x
        center_y = first.y + relative_center_y
        radius = math.hypot(relative_center_x, relative_center_y)
        if not all(math.isfinite(value) for value in (center_x, center_y, radius)):
            raise TopologyError("Arc interpolation point produces invalid geometry")

        start_angle = math.atan2(first.y - center_y, first.x - center_x)
        middle_angle = math.atan2(middle_y - center_y, middle_x - center_x)
        end_angle = math.atan2(second.y - center_y, second.x - center_x)
        full_turn = 2.0 * math.pi
        ccw_to_end = (end_angle - start_angle) % full_turn
        ccw_to_middle = (middle_angle - start_angle) % full_turn
        sweep = (
            ccw_to_end
            if ccw_to_middle <= ccw_to_end + 1.0e-10
            else ccw_to_end - full_turn
        )
        return center_x, center_y, radius, start_angle, sweep

    def _validate_convex_ccw(self, block: Block) -> None:
        points = [self.vertices[vertex_id] for vertex_id in block.vertices]
        cross_products = []
        for index in range(4):
            first = points[index]
            second = points[(index + 1) % 4]
            third = points[(index + 2) % 4]
            cross_products.append(
                (second.x - first.x) * (third.y - second.y)
                - (second.y - first.y) * (third.x - second.x)
            )
        if any(value <= self.COORDINATE_TOLERANCE for value in cross_products):
            raise TopologyError(
                f"Block {block.id} must remain strictly convex and counter-clockwise"
            )

    def _counter_clockwise_vertices(
        self, identifiers: Iterable[str]
    ) -> tuple[str, str, str, str]:
        vertex_ids = list(identifiers)
        center_x = sum(self.vertices[value].x for value in vertex_ids) / 4.0
        center_y = sum(self.vertices[value].y for value in vertex_ids) / 4.0
        ordered = sorted(
            vertex_ids,
            key=lambda value: (
                math.atan2(
                    self.vertices[value].y - center_y,
                    self.vertices[value].x - center_x,
                ),
                value,
            ),
        )
        # Rotate to a stable starting ID without changing counter-clockwise order.
        start = min(range(4), key=lambda index: ordered[index])
        ordered = ordered[start:] + ordered[:start]
        return tuple(ordered)  # type: ignore[return-value]

    def _vertex_at_or_new(self, x: float, y: float) -> Vertex:
        for vertex in self.vertices.values():
            if self._coordinates_match(vertex.x, vertex.y, x, y):
                return vertex
        vertex_id = self._next_id("v", self.vertices)
        vertex = Vertex(vertex_id, x, y)
        self.vertices[vertex_id] = vertex
        return vertex

    @classmethod
    def _coordinates_match(cls, x1: float, y1: float,
                           x2: float, y2: float) -> bool:
        return math.isclose(
            x1, x2, rel_tol=0.0, abs_tol=cls.COORDINATE_TOLERANCE
        ) and math.isclose(
            y1, y2, rel_tol=0.0, abs_tol=cls.COORDINATE_TOLERANCE
        )

    @staticmethod
    def _next_id(prefix: str, identifiers: Iterable[str]) -> str:
        used = set(identifiers)
        index = 0
        while f"{prefix}{index}" in used:
            index += 1
        return f"{prefix}{index}"
