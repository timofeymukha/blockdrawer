"""Quality of the actual transfinite grid, not of the four block corners.

The block-corner measures used while relaxing a layout are cheap early filters.
They cannot see a curved wall cell folding over, a first cell that is nowhere
near wall-normal, or a size jump halfway along an interface.  This module
therefore builds every sampled node of every block with **BlockDrawer's own**
edge-weighted transfinite interpolation - the private helpers of
``blockdrawer.preview`` are imported deliberately, so the evaluation cannot
drift away from what the editor previews and ``blockMesh`` writes - and then
measures every sampled cell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from blockdrawer import preview as bd_preview
from blockdrawer import quality as bd_quality
from blockdrawer.domain import edge_key


# Every declared limit is an upper bound on a measured quantity.  ``None``
# disables one deliberately: it is then reported as disabled, contributes no
# failure, and is never silently replaced by a huge magic number.
LIMIT_FIELDS = (
    ("max_non_orthogonality", "maximum_non_orthogonality_degrees"),
    ("max_skewness", "maximum_equiangle_skewness"),
    ("max_aspect_ratio", "maximum_aspect_ratio_unintended"),
    ("max_interface_ratio", "maximum_interface_size_ratio"),
    ("max_wall_misalignment", "maximum_wall_misalignment_degrees"),
    ("max_first_width_error", "maximum_first_cell_width_error"),
)


@dataclass(frozen=True)
class GridOptions:
    """Acceptance limits; all are dimensionless or in degrees.

    ``None`` disables a limit.  A disabled limit is recorded as such in the
    report so a reader can tell "this was not checked" from "this passed".
    """

    max_non_orthogonality: float | None = 70.0
    max_skewness: float | None = 0.85
    max_aspect_ratio: float | None = 100.0
    max_interface_ratio: float | None = 2.5
    max_wall_misalignment: float | None = 25.0
    max_first_width_error: float | None = 0.25
    node_budget: int = 4_000_000

    def limits(self) -> "GridLimits":
        return GridLimits(
            **{name: getattr(self, name) for name, _key in LIMIT_FIELDS}
        )


@dataclass(frozen=True)
class GridLimits:
    """An immutable copy of the limits a report was actually judged against.

    A report carries this so it can never be compared against different
    defaults later; ``GridReport.within_quality_targets`` means "within *these*
    limits" and nothing else.
    """

    max_non_orthogonality: float | None = None
    max_skewness: float | None = None
    max_aspect_ratio: float | None = None
    max_interface_ratio: float | None = None
    max_wall_misalignment: float | None = None
    max_first_width_error: float | None = None

    def described(self) -> dict:
        return {
            name: ("disabled" if getattr(self, name) is None else getattr(self, name))
            for name, _key in LIMIT_FIELDS
        }


@dataclass(frozen=True)
class QualityFailure:
    """One declared limit that the measured grid does not meet.

    ``severity`` is the fractional overshoot ``observed / limit - 1`` so
    failures of different metrics can be ordered against each other; a limit of
    zero reports the observed value itself.
    """

    metric: str
    observed: float
    limit: float
    comparison: str = "at_most"
    block: str | None = None
    logical_index: list | None = None
    point: list | None = None

    @property
    def severity(self) -> float:
        if self.limit > 0.0:
            return float(self.observed / self.limit - 1.0)
        return float(self.observed)

    def described(self) -> dict:
        return {
            "metric": self.metric,
            "observed": float(self.observed),
            "limit": float(self.limit),
            "comparison": self.comparison,
            "severity": self.severity,
            "block": self.block,
            "logical_index": self.logical_index,
            "point": self.point,
        }


@dataclass
class CellExtreme:
    value: float
    block: str
    index: tuple[int, int]
    point: tuple[float, float]

    def described(self) -> dict:
        return {
            "value": float(self.value),
            "block": self.block,
            "logical_index": [int(self.index[0]), int(self.index[1])],
            "point": [float(self.point[0]), float(self.point[1])],
        }


@dataclass
class GridReport:
    sampled_cells: int
    inverted_cells: int
    minimum_scaled_jacobian: CellExtreme | None
    minimum_angle: CellExtreme | None
    maximum_angle: CellExtreme | None
    maximum_non_orthogonality: CellExtreme | None
    maximum_skewness: CellExtreme | None
    maximum_aspect_ratio: CellExtreme | None
    maximum_layer_aspect_ratio: CellExtreme | None
    maximum_wall_misalignment: CellExtreme | None
    maximum_first_width_error: CellExtreme | None
    worst_cells: list[dict] = field(default_factory=list)
    interface: dict = field(default_factory=dict)
    corner_quality: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    limits: GridLimits = field(default_factory=GridLimits)
    quality_failures: list[QualityFailure] = field(default_factory=list)
    topology_valid: bool = True

    @property
    def untangled(self) -> bool:
        """No sampled cell is inverted and the worst one still has area."""
        if self.inverted_cells:
            return False
        extreme = self.minimum_scaled_jacobian
        return extreme is None or extreme.value > 0.0

    @property
    def within_quality_targets(self) -> bool:
        """Every *enabled* declared limit in ``self.limits`` is met."""
        return not self.quality_failures

    @property
    def admissible(self) -> bool:
        """The grid is a real mesh: valid topology and no folded cell.

        Admissible deliberately does **not** mean good.  It is the condition
        under which a session may be written for inspection; meeting the
        declared quality targets is the separate, stricter
        ``within_quality_targets``.
        """
        return self.topology_valid and self.untangled

    def described(self) -> dict:
        def value(extreme):
            return None if extreme is None else extreme.described()

        return {
            "sampled_cells": self.sampled_cells,
            "inverted_cells": self.inverted_cells,
            "topology_valid": self.topology_valid,
            "untangled": self.untangled,
            "within_quality_targets": self.within_quality_targets,
            "admissible": self.admissible,
            "limits": self.limits.described(),
            "quality_failures": [
                failure.described()
                for failure in sorted(
                    self.quality_failures, key=lambda item: -item.severity
                )
            ],
            "minimum_scaled_jacobian": value(self.minimum_scaled_jacobian),
            "minimum_angle_degrees": value(self.minimum_angle),
            "maximum_angle_degrees": value(self.maximum_angle),
            "maximum_non_orthogonality_degrees": value(
                self.maximum_non_orthogonality
            ),
            "maximum_equiangle_skewness": value(self.maximum_skewness),
            "maximum_aspect_ratio_unintended": value(self.maximum_aspect_ratio),
            "maximum_aspect_ratio_boundary_layer": value(
                self.maximum_layer_aspect_ratio
            ),
            "maximum_wall_misalignment_degrees": value(
                self.maximum_wall_misalignment
            ),
            "maximum_first_cell_width_error": value(self.maximum_first_width_error),
            "worst_cells": self.worst_cells,
            "interface": self.interface,
            "block_corner_quality": self.corner_quality,
            "warnings": self.warnings,
        }


def block_nodes(model, block) -> np.ndarray:
    """Every sampled node of one block as an ``(ny + 1, nx + 1, 2)`` array."""
    directed = tuple(block.directed_edge(index) for index in range(4))
    edges = tuple(edge_key(*item) for item in directed)
    x_cells = model.edge_cells[edges[0]]
    y_cells = model.edge_cells[edges[1]]
    x_indices = tuple(range(x_cells + 1))
    y_indices = tuple(range(y_cells + 1))
    bottom = bd_preview._directed_edge_samples(model, directed[0], x_indices)
    right = bd_preview._directed_edge_samples(model, directed[1], y_indices)
    top = bd_preview._directed_edge_samples(
        model, (directed[2][1], directed[2][0]), x_indices
    )
    left = bd_preview._directed_edge_samples(
        model, (directed[3][1], directed[3][0]), y_indices
    )
    corners = tuple(
        (model.vertices[identifier].x, model.vertices[identifier].y)
        for identifier in block.vertices
    )
    grid = np.empty((y_cells + 1, x_cells + 1, 2), dtype=np.float64)
    for row in range(y_cells + 1):
        right_sample = right[row]
        left_sample = left[row]
        for column in range(x_cells + 1):
            grid[row, column] = bd_preview._block_mesh_point(
                bottom[column], right_sample, top[column], left_sample, corners
            )
    return grid


def _cross(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return first[..., 0] * second[..., 1] - first[..., 1] * second[..., 0]


def cell_metrics(grid: np.ndarray) -> dict[str, np.ndarray]:
    """Per-cell measures for one block's sampled grid."""
    a = grid[:-1, :-1]
    b = grid[:-1, 1:]
    c = grid[1:, 1:]
    d = grid[1:, :-1]
    sides = np.stack([b - a, c - b, d - c, a - d], axis=0)
    lengths = np.linalg.norm(sides, axis=-1)
    incoming = np.stack([sides[3], sides[0], sides[1], sides[2]], axis=0)
    crosses = _cross(incoming, sides)
    pairs = np.linalg.norm(incoming, axis=-1) * lengths
    safe = np.where(pairs > 0.0, pairs, 1.0)
    scaled = np.where(pairs > 0.0, crosses / safe, -1.0)
    dots = np.einsum("kijc,kijc->kij", -incoming, sides)
    raw = np.arctan2(np.abs(crosses), dots)
    angles = np.degrees(np.where(crosses >= 0.0, raw, 2.0 * math.pi - raw))
    area = 0.5 * (_cross(c - a, d - b))
    x_width = 0.5 * (lengths[0] + lengths[2])
    y_width = 0.5 * (lengths[1] + lengths[3])
    low = np.minimum(x_width, y_width)
    aspect = np.where(low > 0.0, np.maximum(x_width, y_width) / np.where(
        low > 0.0, low, 1.0
    ), math.inf)
    minimum_angle = np.min(angles, axis=0)
    maximum_angle = np.max(angles, axis=0)
    return {
        "scaled_jacobian": np.min(scaled, axis=0),
        "minimum_angle": minimum_angle,
        "maximum_angle": maximum_angle,
        "non_orthogonality": np.max(np.abs(90.0 - angles), axis=0),
        "skewness": np.maximum(
            np.maximum((maximum_angle - 90.0) / 90.0, (90.0 - minimum_angle) / 90.0),
            0.0,
        ),
        "aspect_ratio": aspect,
        "area": area,
        "centre": 0.25 * (a + b + c + d),
    }


def _wall_sides(model, block) -> list[int]:
    """Local side indices of a block whose edge is a named wall patch."""
    result = []
    for index in range(4):
        current = edge_key(*block.directed_edge(index))
        name = model.edge_boundaries.get(current)
        if name is None:
            continue
        boundary = model.boundaries.get(name)
        if boundary is not None and boundary.kind == "wall":
            result.append(index)
    return result


def _wall_rows(grid: np.ndarray, side: int):
    """Return (wall nodes, first interior nodes) for one local block side."""
    if side == 0:
        return grid[0, :], grid[1, :]
    if side == 1:
        return grid[:, -1], grid[:, -2]
    if side == 2:
        return grid[-1, :], grid[-2, :]
    return grid[:, 0], grid[:, 1]


def evaluate(
    model,
    *,
    options: GridOptions | None = None,
    layer_blocks: set[str] | None = None,
    first_width: float | None = None,
    topology_valid: bool = True,
) -> GridReport:
    """Measure every sampled cell of every block.

    ``topology_valid`` is supplied by the caller because this module only sees
    a ``MeshModel``: whether the patch graph it came from was planar, covered
    the domain and had compatible periodic patches is known one level up.
    """
    limits = options or GridOptions()
    layers = layer_blocks or set()
    total_nodes = 0
    for block in model.blocks:
        nx, ny, _nz = model.block_cell_counts(block)
        total_nodes += (nx + 1) * (ny + 1)
    warnings: list[str] = []
    if total_nodes > limits.node_budget:
        warnings.append(
            f"sampled node count {total_nodes} exceeds the budget "
            f"{limits.node_budget}; reduce the metric resolution"
        )
    best: dict[str, CellExtreme | None] = {
        key: None
        for key in (
            "scaled_jacobian",
            "minimum_angle",
            "maximum_angle",
            "non_orthogonality",
            "skewness",
            "aspect_ratio",
            "layer_aspect_ratio",
            "wall_misalignment",
            "first_width_error",
        )
    }
    worst_records: list[tuple[float, dict]] = []
    sampled = 0
    inverted = 0

    def consider(
        name: str, values: np.ndarray, block_id: str, centre, *, largest: bool
    ):
        if values.size == 0:
            return
        flat = np.argmax(values) if largest else np.argmin(values)
        index = np.unravel_index(flat, values.shape)
        value = float(values[index])
        current = best[name]
        better = current is None or (
            value > current.value if largest else value < current.value
        )
        if better:
            best[name] = CellExtreme(
                value,
                block_id,
                (int(index[1]), int(index[0])),
                tuple(float(item) for item in centre[index]),
            )

    for block in model.blocks:
        grid = block_nodes(model, block)
        metrics = cell_metrics(grid)
        centre = metrics["centre"]
        sampled += metrics["area"].size
        bad = (metrics["area"] <= 0.0) | (metrics["scaled_jacobian"] <= 0.0)
        inverted += int(np.count_nonzero(bad))
        for name, largest in (
            ("scaled_jacobian", False),
            ("minimum_angle", False),
            ("maximum_angle", True),
            ("non_orthogonality", True),
            ("skewness", True),
        ):
            consider(name, metrics[name], block.id, centre, largest=largest)
        key = "layer_aspect_ratio" if block.id in layers else "aspect_ratio"
        consider(key, metrics["aspect_ratio"], block.id, centre, largest=True)
        for side in _wall_sides(model, block):
            wall, inner = _wall_rows(grid, side)
            step = inner - wall
            width = np.linalg.norm(step, axis=1)
            tangent = np.gradient(wall, axis=0)
            tangent_length = np.linalg.norm(tangent, axis=1)
            normal = np.column_stack((-tangent[:, 1], tangent[:, 0]))
            usable = (width > 0.0) & (tangent_length > 0.0)
            if not np.any(usable):
                continue
            cosine = np.abs(
                np.einsum("ij,ij->i", step[usable], normal[usable])
                / (width[usable] * tangent_length[usable])
            )
            misalignment = np.degrees(np.arccos(np.clip(cosine, 0.0, 1.0)))
            consider(
                "wall_misalignment",
                misalignment[None, :],
                block.id,
                wall[usable][None, :],
                largest=True,
            )
            if first_width is not None and first_width > 0.0:
                error = np.abs(width[usable] - first_width) / first_width
                consider(
                    "first_width_error",
                    error[None, :],
                    block.id,
                    wall[usable][None, :],
                    largest=True,
                )
        rows, columns = np.nonzero(_worst_mask(metrics, limits) | bad)
        for row, column in list(zip(rows.tolist(), columns.tolist()))[:4]:
            worst_records.append(
                (
                    float(metrics["skewness"][row, column]),
                    {
                        "block": block.id,
                        "logical_index": [int(column), int(row)],
                        "point": [
                            float(centre[row, column, 0]),
                            float(centre[row, column, 1]),
                        ],
                        "scaled_jacobian": float(
                            metrics["scaled_jacobian"][row, column]
                        ),
                        "non_orthogonality_degrees": float(
                            metrics["non_orthogonality"][row, column]
                        ),
                        "equiangle_skewness": float(metrics["skewness"][row, column]),
                        "aspect_ratio": float(metrics["aspect_ratio"][row, column]),
                        "inverted": bool(bad[row, column]),
                    },
                )
            )
    worst_records.sort(key=lambda item: -item[0])
    corner_report = bd_quality.assess_quality(model)
    summary = corner_report.summary()
    interfaces = [
        {
            "edge": list(item.edge),
            "blocks": list(item.blocks),
            "max_size_ratio": item.max_size_ratio,
        }
        for item in sorted(
            corner_report.interfaces, key=lambda item: -item.max_size_ratio
        )[:8]
    ]
    report = GridReport(
        sampled,
        inverted,
        best["scaled_jacobian"],
        best["minimum_angle"],
        best["maximum_angle"],
        best["non_orthogonality"],
        best["skewness"],
        best["aspect_ratio"],
        best["layer_aspect_ratio"],
        best["wall_misalignment"],
        best["first_width_error"],
        [record for _score, record in worst_records[:12]],
        {
            "maximum_size_ratio": summary.get("max_interface_size_ratio"),
            "threshold": limits.max_interface_ratio,
            "worst": interfaces,
        },
        summary,
        warnings,
        limits.limits(),
        [],
        bool(topology_valid),
    )
    report.quality_failures = _quality_failures(report, limits)
    if inverted:
        report.warnings.append(f"{inverted} sampled cells are inverted")
    return report


def _worst_mask(metrics: dict, limits: GridOptions) -> np.ndarray:
    """Cells that already breach an enabled limit, for the worst-cell list."""
    mask = np.zeros(metrics["skewness"].shape, dtype=bool)
    if limits.max_non_orthogonality is not None:
        mask |= metrics["non_orthogonality"] > limits.max_non_orthogonality
    if limits.max_skewness is not None:
        mask |= metrics["skewness"] > limits.max_skewness
    return mask


def _quality_failures(report: GridReport, limits: GridOptions):
    """One record per enabled limit the measured grid does not meet."""
    observed: dict[str, CellExtreme | None] = {
        "max_non_orthogonality": report.maximum_non_orthogonality,
        "max_skewness": report.maximum_skewness,
        "max_aspect_ratio": report.maximum_aspect_ratio,
        "max_wall_misalignment": report.maximum_wall_misalignment,
        "max_first_width_error": report.maximum_first_width_error,
    }
    failures: list[QualityFailure] = []
    for name, key in LIMIT_FIELDS:
        limit = getattr(limits, name)
        if limit is None:
            continue
        if name == "max_interface_ratio":
            value = report.interface.get("maximum_size_ratio")
            if value is None or float(value) <= limit:
                continue
            worst = (report.interface.get("worst") or [{}])[0]
            failures.append(
                QualityFailure(
                    key,
                    float(value),
                    float(limit),
                    "at_most",
                    block=", ".join(worst.get("blocks", [])) or None,
                    point=None,
                    logical_index=None,
                )
            )
            continue
        extreme = observed.get(name)
        if extreme is None or extreme.value <= limit:
            continue
        failures.append(
            QualityFailure(
                key,
                float(extreme.value),
                float(limit),
                "at_most",
                block=extreme.block,
                logical_index=[int(extreme.index[0]), int(extreme.index[1])],
                point=[float(extreme.point[0]), float(extreme.point[1])],
            )
        )
    return failures


def compare(reference: dict, candidate: dict) -> dict:
    """Side-by-side comparison of two ``GridReport.described()`` dictionaries."""
    keys = (
        "minimum_scaled_jacobian",
        "minimum_angle_degrees",
        "maximum_non_orthogonality_degrees",
        "maximum_equiangle_skewness",
        "maximum_aspect_ratio_unintended",
        "maximum_wall_misalignment_degrees",
    )
    result = {}
    for key in keys:
        first = (reference.get(key) or {}).get("value")
        second = (candidate.get(key) or {}).get("value")
        result[key] = {"baseline": first, "candidate": second}
    result["inverted_cells"] = {
        "baseline": reference.get("inverted_cells"),
        "candidate": candidate.get("inverted_cells"),
    }
    result["interface_maximum_size_ratio"] = {
        "baseline": (reference.get("interface") or {}).get("maximum_size_ratio"),
        "candidate": (candidate.get("interface") or {}).get("maximum_size_ratio"),
    }
    result["warning_count"] = {
        "baseline": (reference.get("block_corner_quality") or {}).get("warning_count"),
        "candidate": (candidate.get("block_corner_quality") or {}).get("warning_count"),
    }
    for flag in ("untangled", "within_quality_targets", "admissible"):
        result[flag] = {
            "baseline": reference.get(flag),
            "candidate": candidate.get(flag),
        }
    result["quality_failures"] = {
        "baseline": [
            item.get("metric") for item in reference.get("quality_failures", [])
        ],
        "candidate": [
            item.get("metric") for item in candidate.get("quality_failures", [])
        ],
    }
    return result
