"""UI-independent command registry for scripted and agent-driven editing.

Every public ``MeshModel`` mutation is exposed as a named command with a typed
parameter list. Commands can be written either as JSON objects (``{"op":
"set_edge_cells", "edge": ["v0", "v1"], "cells": 20}``) or as short shell-like
text lines (``set_edge_cells v0-v1 20``). Both forms resolve through the same
registry, so the command-line interface, batch files, and any future MCP server
share one vocabulary.

Batches are atomic: :func:`apply_commands` works on a copy of the model and
returns the new model only when every command succeeds. The caller's model is
never mutated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
import math
from typing import Any, Callable, Iterable, Sequence

from .command_line import split_command_line
from .domain import EdgeKey, SpacingLink, TopologyError, edge_key
from .geometry import GeometryImportError, load_point_pairs
from .model import MeshModel
from .projection import (
    DEFAULT_FIT_MAX_POINTS,
    FIT_RELATIVE_TOLERANCE,
    PROJECTION_DIRECTIONS,
)
from .session import from_data, to_data


EDGE_SEPARATOR = "-"


class CommandError(ValueError):
    """Raised when a command is malformed or its model operation fails."""


@dataclass(frozen=True)
class Parameter:
    """One typed command argument.

    ``kind`` is one of ``edge``, ``edges``, ``vertex``, ``vertices``,
    ``curve``, ``curves``, ``int``, ``float``, ``str``, ``bool``, ``point``,
    ``points``, ``choice``, or ``optional_str``.
    """

    name: str
    kind: str
    help: str
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "type": self.kind,
            "required": self.required,
            "help": self.help,
        }
        if not self.required:
            data["default"] = self.default
        if self.choices:
            data["choices"] = list(self.choices)
        return data


@dataclass(frozen=True)
class CommandSpec:
    """A named model operation and its parameter schema."""

    name: str
    help: str
    parameters: tuple[Parameter, ...]
    run: Callable[[MeshModel, dict[str, Any]], dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "help": self.help,
            "parameters": [parameter.describe() for parameter in self.parameters],
            "usage": self.usage(),
        }

    def usage(self) -> str:
        parts = [self.name]
        for parameter in self.parameters:
            if parameter.required:
                parts.append(f"<{parameter.name}>")
            else:
                parts.append(f"[{parameter.name}=...]")
        return " ".join(parts)


@dataclass(frozen=True)
class CommandResult:
    """The outcome of one successfully applied command."""

    index: int
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    def to_data(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "op": self.name,
            "arguments": self.arguments,
            "result": self.result,
        }


@dataclass(frozen=True)
class BatchResult:
    """A new model plus the per-command results of one atomic batch."""

    model: MeshModel
    results: tuple[CommandResult, ...]


# ---------------------------------------------------------------------------
# Public text helpers
# ---------------------------------------------------------------------------


def edge_text(edge: EdgeKey) -> str:
    """Render a canonical edge key as ``first-second``."""
    current = edge_key(*edge)
    return f"{current[0]}{EDGE_SEPARATOR}{current[1]}"


def parse_edge(model: MeshModel, value: Any) -> EdgeKey:
    """Resolve ``a-b`` text or a two-item sequence into a known edge."""
    if isinstance(value, str):
        candidates: list[EdgeKey] = []
        for index in range(1, len(value)):
            if value[index] != EDGE_SEPARATOR:
                continue
            first = value[:index]
            second = value[index + 1:]
            if first in model.vertices and second in model.vertices \
                    and first != second:
                candidates.append(edge_key(first, second))
        if not candidates:
            raise CommandError(
                f"Unknown edge {value!r}; expected two known vertex IDs "
                f"joined by {EDGE_SEPARATOR!r}, for example v0-v1"
            )
        if len(set(candidates)) > 1:
            raise CommandError(
                f"Edge {value!r} is ambiguous; use the JSON form "
                '["first", "second"] instead'
            )
        current = candidates[0]
    else:
        try:
            first, second = value
        except (TypeError, ValueError) as exc:
            raise CommandError(
                f"An edge needs two vertex IDs, got {value!r}"
            ) from exc
        if not isinstance(first, str) or not isinstance(second, str):
            raise CommandError(f"An edge needs two vertex IDs, got {value!r}")
        for vertex_id in (first, second):
            if vertex_id not in model.vertices:
                raise CommandError(f"Unknown vertex {vertex_id!r}")
        try:
            current = edge_key(first, second)
        except TopologyError as exc:
            raise CommandError(str(exc)) from exc
    if current not in model.edge_cells:
        raise CommandError(
            f"{edge_text(current)} is not an edge of any block"
        )
    return current


def resolve_curve(model: MeshModel, value: Any) -> str:
    """Resolve a reference-curve ID or unique name to its ID."""
    if not isinstance(value, str) or not value:
        raise CommandError(f"A reference curve needs an ID or name, got {value!r}")
    if value in model.geometry_curves:
        return value
    matches = [
        curve.id for curve in model.geometry_curves.values()
        if curve.name == value
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CommandError(f"Unknown reference curve {value!r}")
    raise CommandError(
        f"Reference curve name {value!r} is ambiguous; use its ID"
    )


# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    raise CommandError(f"{name} must be true or false, got {value!r}")


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CommandError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise CommandError(f"{name} must be an integer, got {value!r}")


def _coerce_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise CommandError(f"{name} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError as exc:
            raise CommandError(
                f"{name} must be a number, got {value!r}"
            ) from exc
    else:
        raise CommandError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(result):
        raise CommandError(f"{name} must be finite, got {value!r}")
    return result


def _coerce_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise CommandError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _coerce_optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in (
        "", "none", "null",
    ):
        return None
    return _coerce_str(value, name)


def _coerce_list(value: Any, name: str) -> list[Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CommandError(f"{name} is not valid JSON: {exc}") from exc
            if not isinstance(loaded, list):
                raise CommandError(f"{name} must be a list")
            return loaded
        return [item for item in text.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise CommandError(f"{name} must be a list, got {value!r}")


def _coerce_point(value: Any, name: str) -> tuple[float, float]:
    if isinstance(value, str):
        fields = [
            item for item in value.replace(",", " ").split() if item
        ]
    elif isinstance(value, (list, tuple)):
        fields = list(value)
    else:
        raise CommandError(f"{name} must be an x,y point, got {value!r}")
    if len(fields) != 2:
        raise CommandError(f"{name} must be an x,y point, got {value!r}")
    return (
        _coerce_float(fields[0], f"{name}.x"),
        _coerce_float(fields[1], f"{name}.y"),
    )


def _coerce_points(value: Any, name: str) -> tuple[tuple[float, float], ...]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            items = _coerce_list(text, name)
        else:
            items = [item for item in text.split(";") if item.strip()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise CommandError(f"{name} must be a list of x,y points")
    return tuple(
        _coerce_point(item, f"{name}[{index}]") for index, item in enumerate(items)
    )


def _coerce_vertex(model: MeshModel, value: Any, name: str) -> str:
    vertex_id = _coerce_str(value, name)
    if vertex_id not in model.vertices:
        raise CommandError(f"Unknown vertex {vertex_id!r}")
    return vertex_id


def coerce_argument(
    model: MeshModel, parameter: Parameter, value: Any
) -> Any:
    """Convert one raw argument into the value a model method expects."""
    name = parameter.name
    kind = parameter.kind
    if kind == "edge":
        return parse_edge(model, value)
    if kind == "edges":
        return tuple(
            parse_edge(model, item) for item in _split_edges(model, value, name)
        )
    if kind == "vertex":
        return _coerce_vertex(model, value, name)
    if kind == "vertices":
        return tuple(
            _coerce_vertex(model, item, name) for item in _coerce_list(value, name)
        )
    if kind == "curve":
        return resolve_curve(model, value)
    if kind == "curves":
        return tuple(
            resolve_curve(model, item) for item in _coerce_list(value, name)
        )
    if kind == "int":
        return _coerce_int(value, name)
    if kind == "float":
        return _coerce_float(value, name)
    if kind == "str":
        return _coerce_str(value, name)
    if kind == "optional_str":
        return _coerce_optional_str(value, name)
    if kind == "bool":
        return _coerce_bool(value, name)
    if kind == "point":
        return _coerce_point(value, name)
    if kind == "points":
        return _coerce_points(value, name)
    if kind == "choice":
        text = _coerce_str(value, name)
        if text not in parameter.choices:
            raise CommandError(
                f"{name} must be one of {', '.join(parameter.choices)}, "
                f"got {text!r}"
            )
        return text
    raise CommandError(f"Unsupported parameter kind {kind!r}")


def _split_edges(model: MeshModel, value: Any, name: str) -> list[Any]:
    if isinstance(value, str):
        return [item for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(item, str) for item in value) \
                and all(item in model.vertices for item in value):
            # A bare ["a", "b"] pair is one edge, not two.
            return [tuple(value)]
        return list(value)
    raise CommandError(f"{name} must be a list of edges")


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------


_EDGE_FIELDS = frozenset({
    "edge",
    "edges",
    "driver_edge",
    "follower_edge",
    "first_edge",
    "second_edge",
    "source_edge",
    "affected_edges",
    "converted_arcs",
    "fitted_edges",
    "selected_segments",
    "cut_edges",
    "removed_edges",
    "merged_edges",
})


def to_json_value(value: Any, *, as_edge: bool = False) -> Any:
    """Convert model return values into plain JSON-compatible data.

    Edge keys are plain two-string tuples, which a pair of vertex IDs would
    also match, so they are rendered as ``first-second`` text only under
    dictionary fields known to hold edges (see ``_EDGE_FIELDS``).
    """
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_value(asdict(value))
    if isinstance(value, dict):
        return {
            (edge_text(key) if _is_edge_key(key) else str(key)): to_json_value(
                item, as_edge=key in _EDGE_FIELDS
            )
            for key, item in value.items()
        }
    if as_edge and _is_edge_key(value):
        return edge_text(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if isinstance(value, (set, frozenset)):
            items = sorted(items, key=repr)
        return [to_json_value(item, as_edge=as_edge) for item in items]
    return value


def _is_edge_key(value: Any) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) for item in value)
    )


def _grading_data(model: MeshModel, edge: EdgeKey) -> dict[str, Any]:
    values = model.edge_grading_values(edge)
    return {
        "edge": edge_text(edge),
        "direction": f"{edge[0]} -> {edge[1]}",
        "cells": model.edge_cells[edge],
        "length": values.length,
        "total_ratio": values.total_ratio,
        "cell_ratio": values.cell_ratio,
        "start_width": values.start_width,
        "end_width": values.end_width,
    }


def _link_data(model: MeshModel, link: SpacingLink) -> dict[str, Any]:
    return {
        "vertex": link.vertex,
        "edges": [edge_text(link.first_edge), edge_text(link.second_edge)],
        "synchronized": (
            model.spacing_link_is_synchronized(link)
            if link in model.spacing_links else None
        ),
    }


def _block_data(model: MeshModel, block_id: str) -> dict[str, Any]:
    block = next(item for item in model.blocks if item.id == block_id)
    nx, ny, nz = model.block_cell_counts(block)
    return {
        "block": block.id,
        "vertices": list(block.vertices),
        "cells": [nx, ny, nz],
        "edges": [
            edge_text(edge_key(*block.directed_edge(index))) for index in range(4)
        ],
    }


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _run_set_edge_cells(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    affected = model.set_edge_cells(args["edge"], args["cells"])
    return {
        "cells": args["cells"],
        "affected_edges": sorted(edge_text(item) for item in affected),
    }


def _run_set_edge_grading(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    edge = args["edge"]
    model.set_edge_grading(
        edge, args["parameter"], args["value"], propagate=args["propagate"]
    )
    data = _grading_data(model, edge)
    links = model.spacing_links_for_edge(edge)
    if links:
        data["spacing_links"] = [_link_data(model, link) for link in links]
    return data


def _run_set_edge_type(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    edge = args["edge"]
    model.set_edge_type(edge, args["type"])
    return {
        "edge": edge_text(edge),
        "type": model.edge_type(edge),
        "control_points": to_json_value(model.edge_control_points(edge)),
    }


def _control_point_result(model: MeshModel, edge: EdgeKey) -> dict[str, Any]:
    return {
        "edge": edge_text(edge),
        "type": model.edge_type(edge),
        "control_points": to_json_value(model.edge_control_points(edge)),
    }


def _run_set_control_point(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    edge = args["edge"]
    model.set_edge_control_point(edge, args["index"], args["x"], args["y"])
    return _control_point_result(model, edge)


def _run_add_control_point(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    edge = args["edge"]
    index = model.add_edge_control_point(edge, args["after_index"])
    data = _control_point_result(model, edge)
    data["index"] = index
    return data


def _run_remove_control_point(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    edge = args["edge"]
    model.remove_edge_control_point(edge, args["index"])
    return _control_point_result(model, edge)


def _run_reset_control_points(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    edge = args["edge"]
    model.reset_edge_control_points(edge)
    return _control_point_result(model, edge)


def _run_set_control_point_count(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    edge = args["edge"]
    model.set_edge_control_point_count(edge, args["count"])
    return _control_point_result(model, edge)


def _run_move_vertex(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.move_vertex(args["vertex"], args["x"], args["y"])
    vertex = model.vertices[args["vertex"]]
    return {"vertex": vertex.id, "x": vertex.x, "y": vertex.y}


def _run_add_vertex(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    vertex = model.add_vertex(args["x"], args["y"])
    return {"vertex": vertex.id, "x": vertex.x, "y": vertex.y}


def _run_add_block(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    block = model.add_block(args["edge"])
    data = _block_data(model, block.id)
    data["source_edge"] = edge_text(args["edge"])
    data["opposite_edge"] = edge_text(
        edge_key(block.vertices[2], block.vertices[3])
    )
    return data


def _run_add_block_from_vertices(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    block = model.add_block_from_vertices(args["vertices"])
    return _block_data(model, block.id)


def _run_remove_edge(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    removed = model.remove_edge(args["edge"])
    return {
        "edge": edge_text(args["edge"]),
        "removed_blocks": [block.id for block in removed],
        "remaining_blocks": [block.id for block in model.blocks],
    }


def _run_split_edge(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    result = model.split_edge(args["edge"], args["fraction"])
    return to_json_value(result)


def _run_combine_blocks(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    result = model.combine_blocks(args["edge"])
    return to_json_value(result)


def _run_add_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    boundary = model.add_boundary(args["name"])
    return to_json_value(boundary)


def _run_remove_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.remove_boundary(args["name"])
    return {"removed": args["name"], "boundaries": sorted(model.boundaries)}


def _run_set_boundary_type(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    affected = model.set_boundary_type(
        args["name"], args["type"], neighbour_patch=args["neighbour_patch"]
    )
    return {
        "affected": sorted(affected),
        "boundaries": [
            to_json_value(model.boundaries[name]) for name in sorted(affected)
        ],
    }


def _run_set_edge_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    edge = args["edge"]
    model.set_edge_boundary(edge, args["name"])
    return {
        "edge": edge_text(edge),
        "boundary": model.edge_boundaries.get(edge),
    }


def _run_add_spacing_link(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    link = model.add_spacing_link(args["driver_edge"], args["follower_edge"])
    data = _link_data(model, link)
    data["follower_grading"] = _grading_data(model, args["follower_edge"])
    return data


def _run_remove_spacing_link(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    link = model.remove_spacing_link(args["first_edge"], args["second_edge"])
    return {
        "vertex": link.vertex,
        "edges": [edge_text(link.first_edge), edge_text(link.second_edge)],
    }


def _run_synchronize_spacing_links(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    affected = model.synchronize_spacing_links(args["edge"])
    return {
        "driver_edge": edge_text(args["edge"]),
        "affected_edges": sorted(edge_text(item) for item in affected),
    }


def _run_add_curve(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    curve = model.add_geometry_curve(args["points"], name=args["name"])
    return {"curve": curve.id, "name": curve.name, "point_count": len(curve.points)}


def _run_import_curve(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    try:
        points = load_point_pairs(args["path"])
    except GeometryImportError as exc:
        raise CommandError(str(exc)) from exc
    curve = model.add_geometry_curve(
        points, name=args["name"], show_points=False
    )
    return {
        "curve": curve.id,
        "name": curve.name,
        "point_count": len(curve.points),
        "path": args["path"],
    }


def _run_remove_curve(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.remove_geometry_curve(args["curve"])
    return {"removed": args["curve"], "curves": sorted(model.geometry_curves)}


def _curve_result(model: MeshModel, curve_id: str) -> dict[str, Any]:
    curve = model.geometry_curves[curve_id]
    return {
        "curve": curve.id,
        "name": curve.name,
        "point_count": len(curve.points),
        "show_points": curve.show_points,
    }


def _run_rename_curve(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.set_geometry_curve_name(args["curve"], args["name"])
    return _curve_result(model, args["curve"])


def _run_set_curve_show_points(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    model.set_geometry_curve_point_visibility(args["curve"], args["visible"])
    return _curve_result(model, args["curve"])


def _run_set_curve_point(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.set_geometry_curve_point(args["curve"], args["index"], args["x"], args["y"])
    data = _curve_result(model, args["curve"])
    data["index"] = args["index"]
    return data


def _run_add_curve_point(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    curve = model.geometry_curves.get(args["curve"])
    after_index = args["after_index"]
    if after_index is None and curve is not None:
        after_index = len(curve.points) - 1
    index = model.add_geometry_curve_point(args["curve"], after_index)
    data = _curve_result(model, args["curve"])
    data["index"] = index
    return data


def _run_remove_curve_point(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    model.remove_geometry_curve_point(args["curve"], args["index"])
    return _curve_result(model, args["curve"])


def _run_replace_curve_points(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    model.replace_geometry_curve_points(args["curve"], args["points"])
    return _curve_result(model, args["curve"])


def _run_replace_curve_points_from_file(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    try:
        points = load_point_pairs(args["path"])
    except GeometryImportError as exc:
        raise CommandError(str(exc)) from exc
    model.replace_geometry_curve_points(args["curve"], points)
    model.set_geometry_curve_point_visibility(args["curve"], False)
    data = _curve_result(model, args["curve"])
    data["path"] = args["path"]
    return data


def _run_project(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    result = model.project_to_geometry(
        args["curves"],
        args["direction"],
        vertex_ids=args["vertices"],
        edges=args["edges"],
        fit=args["fit"],
        fit_relative_tolerance=args["fit_tolerance"],
        fit_max_points=args["fit_max_points"],
    )
    return to_json_value(result)


def _run_set_export_settings(
    model: MeshModel, args: dict[str, Any]
) -> dict[str, Any]:
    def pick(name: str, current: Any) -> Any:
        value = args.get(name)
        return current if value is None else value

    model.set_export_settings(
        pick("z_cells", model.z_cells),
        pick("z_min", model.z_min),
        pick("z_max", model.z_max),
        pick("scale", model.scale),
        pick("z_min_patch_name", model.z_min_patch_name),
        pick("z_min_patch_type", model.z_min_patch_type),
        pick("z_max_patch_name", model.z_max_patch_name),
        pick("z_max_patch_type", model.z_max_patch_type),
    )
    return export_settings_data(model)


def export_settings_data(model: MeshModel) -> dict[str, Any]:
    return {
        "z_cells": model.z_cells,
        "z_min": model.z_min,
        "z_max": model.z_max,
        "scale": model.scale,
        "z_min_patch": {"name": model.z_min_patch_name, "type": model.z_min_patch_type},
        "z_max_patch": {"name": model.z_max_patch_name, "type": model.z_max_patch_type},
    }


_EDGE = Parameter("edge", "edge", "topological edge as first-second, e.g. v0-v1")
_BOUNDARY_TYPES = MeshModel.SUPPORTED_BOUNDARY_TYPES

COMMANDS: dict[str, CommandSpec] = {}


def _register(spec: CommandSpec) -> None:
    COMMANDS[spec.name] = spec


_register(CommandSpec(
    "set_edge_cells",
    "Set an edge's cell count; opposite edges in every constrained block follow.",
    (
        _EDGE,
        Parameter("cells", "int", "positive number of cells along the edge"),
    ),
    _run_set_edge_cells,
))
_register(CommandSpec(
    "set_edge_grading",
    "Set directional grading from one representation; the others are derived.",
    (
        _EDGE,
        Parameter(
            "parameter", "choice",
            "which representation the value uses",
            choices=MeshModel.GRADING_PARAMETERS,
        ),
        Parameter("value", "float", "positive grading value in first->second direction"),
        Parameter(
            "propagate", "bool",
            "also grade the whole opposite-edge constraint component",
            required=False, default=False,
        ),
    ),
    _run_set_edge_grading,
))
_register(CommandSpec(
    "set_edge_type",
    "Change an edge between line, arc, polyLine, and spline geometry.",
    (
        _EDGE,
        Parameter(
            "type", "choice", "OpenFOAM edge type",
            choices=MeshModel.SUPPORTED_EDGE_TYPES,
        ),
    ),
    _run_set_edge_type,
))
_register(CommandSpec(
    "set_control_point",
    "Move one interpolation point of a curved edge (index 0 is the arc point).",
    (
        _EDGE,
        Parameter("index", "int", "zero-based interpolation point index"),
        Parameter("x", "float", "new x coordinate"),
        Parameter("y", "float", "new y coordinate"),
    ),
    _run_set_control_point,
))
_register(CommandSpec(
    "add_control_point",
    "Insert an interpolation point after an index on a polyLine or spline.",
    (
        _EDGE,
        Parameter(
            "after_index", "int",
            "insert after this zero-based index (default: append at the end)",
            required=False, default=None,
        ),
    ),
    _run_add_control_point,
))
_register(CommandSpec(
    "remove_control_point",
    "Remove one interpolation point from a polyLine or spline.",
    (_EDGE, Parameter("index", "int", "zero-based interpolation point index")),
    _run_remove_control_point,
))
_register(CommandSpec(
    "reset_control_points",
    "Redistribute a polyLine's or spline's points evenly along the chord.",
    (_EDGE,),
    _run_reset_control_points,
))
_register(CommandSpec(
    "set_control_point_count",
    "Replace a polyLine's or spline's points with N evenly spaced chord points.",
    (_EDGE, Parameter("count", "int", "positive number of interpolation points")),
    _run_set_control_point_count,
))
_register(CommandSpec(
    "move_vertex",
    "Move a vertex; every incident block must stay strictly convex.",
    (
        Parameter("vertex", "vertex", "vertex ID"),
        Parameter("x", "float", "new x coordinate"),
        Parameter("y", "float", "new y coordinate"),
    ),
    _run_move_vertex,
))
_register(CommandSpec(
    "add_vertex",
    "Create a standalone vertex for later use by add_block_from_vertices.",
    (
        Parameter("x", "float", "x coordinate"),
        Parameter("y", "float", "y coordinate"),
    ),
    _run_add_vertex,
))
_register(CommandSpec(
    "add_block",
    "Extrude a new block outward from an exterior edge.",
    (_EDGE,),
    _run_add_block,
))
_register(CommandSpec(
    "add_block_from_vertices",
    "Create a block from four existing vertices given in any order.",
    (Parameter("vertices", "vertices", "four vertex IDs, e.g. v1,v2,v5,v6"),),
    _run_add_block_from_vertices,
))
_register(CommandSpec(
    "remove_edge",
    "Remove an edge and every block incident to it.",
    (_EDGE,),
    _run_remove_edge,
))
_register(CommandSpec(
    "split_edge",
    "Split the complete conformal block strip through an edge at a fraction.",
    (
        _EDGE,
        Parameter(
            "fraction", "float",
            "0-1 position along the edge in first->second direction; "
            "snaps to the nearest existing mesh node",
        ),
    ),
    _run_split_edge,
))
_register(CommandSpec(
    "combine_blocks",
    "Merge the block pairs across an internal edge (inverse of split_edge).",
    (_EDGE,),
    _run_combine_blocks,
))
_register(CommandSpec(
    "add_boundary",
    "Create a named patch of type patch.",
    (Parameter("name", "str", "OpenFOAM patch name"),),
    _run_add_boundary,
))
_register(CommandSpec(
    "remove_boundary",
    "Delete a patch, its edge assignments, and any cyclic pairing.",
    (Parameter("name", "str", "patch name"),),
    _run_remove_boundary,
))
_register(CommandSpec(
    "set_boundary_type",
    "Set a patch type; cyclic pairs need neighbour_patch and pair reciprocally.",
    (
        Parameter("name", "str", "patch name"),
        Parameter("type", "choice", "patch type", choices=_BOUNDARY_TYPES),
        Parameter(
            "neighbour_patch", "optional_str",
            "partner patch for type cyclic",
            required=False, default=None,
        ),
    ),
    _run_set_boundary_type,
))
_register(CommandSpec(
    "set_edge_boundary",
    "Assign an exterior edge to a patch, or clear it with name none.",
    (
        _EDGE,
        Parameter("name", "optional_str", "patch name, or none to unassign"),
    ),
    _run_set_edge_boundary,
))
_register(CommandSpec(
    "add_spacing_link",
    "Link two edges at their shared vertex; the follower is regraded to match.",
    (
        Parameter("driver_edge", "edge", "edge whose endpoint width is kept"),
        Parameter("follower_edge", "edge", "incident edge that is regraded"),
    ),
    _run_add_spacing_link,
))
_register(CommandSpec(
    "remove_spacing_link",
    "Remove the link between two edges while keeping their grading.",
    (
        Parameter("first_edge", "edge", "one linked edge"),
        Parameter("second_edge", "edge", "the other linked edge"),
    ),
    _run_remove_spacing_link,
))
_register(CommandSpec(
    "synchronize_spacing_links",
    "Re-establish every linked width reachable from a driver edge.",
    (_EDGE,),
    _run_synchronize_spacing_links,
))
_register(CommandSpec(
    "add_curve",
    "Add a reference curve through ordered points, e.g. 0,0;0.5,0.2;1,0.",
    (
        Parameter("points", "points", "at least two x,y points separated by ;"),
        Parameter(
            "name", "optional_str", "curve name (default: curveN)",
            required=False, default=None,
        ),
    ),
    _run_add_curve,
))
_register(CommandSpec(
    "import_curve",
    "Add a reference curve from a text file with one x y pair per line.",
    (
        Parameter("path", "str", "point-file path"),
        Parameter(
            "name", "optional_str", "curve name (default: curveN)",
            required=False, default=None,
        ),
    ),
    _run_import_curve,
))
_register(CommandSpec(
    "remove_curve",
    "Delete a reference curve by ID or unique name.",
    (Parameter("curve", "curve", "curve ID or name"),),
    _run_remove_curve,
))
_register(CommandSpec(
    "rename_curve",
    "Rename a reference curve.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("name", "str", "new unique name"),
    ),
    _run_rename_curve,
))
_register(CommandSpec(
    "set_curve_show_points",
    "Show or hide a reference curve's point markers in the GUI.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("visible", "bool", "true to show point markers"),
    ),
    _run_set_curve_show_points,
))
_register(CommandSpec(
    "set_curve_point",
    "Move one point of a reference curve.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("index", "int", "zero-based point index"),
        Parameter("x", "float", "new x coordinate"),
        Parameter("y", "float", "new y coordinate"),
    ),
    _run_set_curve_point,
))
_register(CommandSpec(
    "add_curve_point",
    "Insert a reference-curve point after an index (default: append).",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter(
            "after_index", "int", "insert after this zero-based index",
            required=False, default=None,
        ),
    ),
    _run_add_curve_point,
))
_register(CommandSpec(
    "remove_curve_point",
    "Remove one reference-curve point; two points must remain.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("index", "int", "zero-based point index"),
    ),
    _run_remove_curve_point,
))
_register(CommandSpec(
    "replace_curve_points",
    "Replace every point of a reference curve, e.g. 0,0;0.5,0.2;1,0.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("points", "points", "at least two x,y points separated by ;"),
    ),
    _run_replace_curve_points,
))
_register(CommandSpec(
    "replace_curve_points_from_file",
    "Replace a reference curve's points from a text file and hide its markers.",
    (
        Parameter("curve", "curve", "curve ID or name"),
        Parameter("path", "str", "point-file path"),
    ),
    _run_replace_curve_points_from_file,
))
_register(CommandSpec(
    "project",
    "Project vertices or whole edges onto reference curves as one atomic edit.",
    (
        Parameter("curves", "curves", "target curve IDs or names, comma separated"),
        Parameter(
            "direction", "choice", "projection direction",
            choices=PROJECTION_DIRECTIONS,
        ),
        Parameter(
            "vertices", "vertices", "vertex IDs to project (exclusive with edges)",
            required=False, default=(),
        ),
        Parameter(
            "edges", "edges", "edges to project, e.g. v0-v1,v1-v2",
            required=False, default=(),
        ),
        Parameter(
            "fit", "bool",
            "replace each edge with a spline fitted to the curve section",
            required=False, default=False,
        ),
        Parameter(
            "fit_tolerance", "float", "relative fit tolerance",
            required=False, default=FIT_RELATIVE_TOLERANCE,
        ),
        Parameter(
            "fit_max_points", "int", "interpolation-point cap per fitted edge",
            required=False, default=DEFAULT_FIT_MAX_POINTS,
        ),
    ),
    _run_project,
))
_register(CommandSpec(
    "set_export_settings",
    "Change any subset of the z extrusion, scale, and zMin/zMax patch settings.",
    (
        Parameter("z_cells", "int", "cells in z", required=False, default=None),
        Parameter("z_min", "float", "lower z", required=False, default=None),
        Parameter("z_max", "float", "upper z", required=False, default=None),
        Parameter("scale", "float", "blockMesh scale", required=False, default=None),
        Parameter(
            "z_min_patch_name", "str", "zMin patch name",
            required=False, default=None,
        ),
        Parameter(
            "z_min_patch_type", "choice", "zMin patch type",
            required=False, default=None, choices=_BOUNDARY_TYPES,
        ),
        Parameter(
            "z_max_patch_name", "str", "zMax patch name",
            required=False, default=None,
        ),
        Parameter(
            "z_max_patch_type", "choice", "zMax patch type",
            required=False, default=None, choices=_BOUNDARY_TYPES,
        ),
    ),
    _run_set_export_settings,
))


# ---------------------------------------------------------------------------
# Parsing and execution
# ---------------------------------------------------------------------------


def command_specs() -> list[CommandSpec]:
    """Return every registered command in a stable order."""
    return [COMMANDS[name] for name in COMMANDS]


def parse_command(command: Any) -> tuple[str, dict[str, Any]]:
    """Normalize a text line or JSON object into ``(name, raw_arguments)``.

    Text lines are split like a shell command. Tokens of the form
    ``key=value`` are keyword arguments; the remaining tokens fill the
    command's parameters in order.
    """
    if isinstance(command, str):
        text = command.strip()
        if not text:
            raise CommandError("Empty command")
        if text.startswith("{"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CommandError(f"Invalid JSON command: {exc}") from exc
            return parse_command(loaded)
        try:
            tokens = split_command_line(text)
        except ValueError as exc:
            raise CommandError(f"Could not parse command {text!r}: {exc}") from exc
        name = tokens[0]
        spec = _spec(name)
        positional: list[str] = []
        keywords: dict[str, Any] = {}
        parameter_names = {parameter.name for parameter in spec.parameters}
        for token in tokens[1:]:
            key, separator, value = token.partition("=")
            if separator and key in parameter_names:
                if key in keywords:
                    raise CommandError(f"Duplicate argument {key!r}")
                keywords[key] = value
            elif separator and key.isidentifier():
                raise CommandError(
                    f"Unknown argument {key!r} for {name}; usage: {spec.usage()}"
                )
            else:
                positional.append(token)
        raw: dict[str, Any] = {}
        unfilled = [
            parameter for parameter in spec.parameters
            if parameter.name not in keywords
        ]
        if len(positional) > len(unfilled):
            raise CommandError(
                f"Too many arguments for {name}; usage: {spec.usage()}"
            )
        for parameter, value in zip(unfilled, positional):
            raw[parameter.name] = value
        raw.update(keywords)
        return name, raw
    if isinstance(command, dict):
        data = dict(command)
        name = data.pop("op", None)
        if name is None:
            name = data.pop("command", None)
        if not isinstance(name, str):
            raise CommandError('A JSON command needs an "op" field')
        _spec(name)
        return name, data
    raise CommandError(
        f"A command must be a text line or a JSON object, got {type(command).__name__}"
    )


def _spec(name: str) -> CommandSpec:
    spec = COMMANDS.get(name)
    if spec is None:
        known = ", ".join(COMMANDS)
        raise CommandError(f"Unknown command {name!r}; known commands: {known}")
    return spec


def bind_arguments(
    model: MeshModel, spec: CommandSpec, raw: dict[str, Any]
) -> dict[str, Any]:
    """Validate raw arguments against a spec and coerce them for the model."""
    unknown = set(raw) - {parameter.name for parameter in spec.parameters}
    if unknown:
        raise CommandError(
            f"Unknown argument(s) for {spec.name}: {', '.join(sorted(unknown))}; "
            f"usage: {spec.usage()}"
        )
    bound: dict[str, Any] = {}
    for parameter in spec.parameters:
        if parameter.name in raw and raw[parameter.name] is not None:
            bound[parameter.name] = coerce_argument(
                model, parameter, raw[parameter.name]
            )
        elif parameter.name in raw and parameter.kind == "optional_str":
            # An explicit null is a valid value for optional-string fields
            # such as set_edge_boundary's patch name.
            bound[parameter.name] = None
        elif parameter.required:
            raise CommandError(
                f"Missing argument {parameter.name!r} for {spec.name}; "
                f"usage: {spec.usage()}"
            )
        else:
            bound[parameter.name] = parameter.default
    return bound


def apply_command(
    model: MeshModel, command: Any, *, index: int = 0
) -> CommandResult:
    """Apply one command to ``model`` in place and return its result.

    Model operations roll themselves back on failure, so a failed command
    leaves ``model`` unchanged. Batches should prefer :func:`apply_commands`.
    """
    name, raw = parse_command(command)
    spec = COMMANDS[name]
    bound = bind_arguments(model, spec, raw)
    try:
        result = spec.run(model, bound)
    except (TopologyError, ValueError) as exc:
        raise CommandError(f"{name}: {exc}") from exc
    return CommandResult(index, name, to_json_value(bound), result)


def apply_commands(
    model: MeshModel, commands: Iterable[Any]
) -> BatchResult:
    """Apply commands to a copy of ``model`` atomically.

    The returned model reflects every command. If any command fails, a
    :class:`CommandError` identifying the failing step is raised and the
    caller's model remains untouched.
    """
    working = from_data(to_data(model))
    results: list[CommandResult] = []
    for index, command in enumerate(commands):
        try:
            results.append(apply_command(working, command, index=index))
        except CommandError as exc:
            shown = command if isinstance(command, str) else json.dumps(command)
            raise CommandError(
                f"Command {index + 1} ({shown}) failed: {exc}"
            ) from exc
    try:
        working.validate()
    except TopologyError as exc:  # pragma: no cover - defensive
        raise CommandError(f"Resulting topology is invalid: {exc}") from exc
    return BatchResult(working, tuple(results))


def read_command_lines(text: str) -> list[str]:
    """Split a batch file into commands, ignoring blanks and ``#`` comments."""
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(stripped)
    return commands


def format_result(result: CommandResult) -> str:
    """Render one command result as a compact human-readable line."""
    parts = [f"{result.index + 1}. {result.name}"]
    summary = _summarize(result.result)
    if summary:
        parts.append(summary)
    return ": ".join(parts)


def _summarize(data: Any, *, depth: int = 0) -> str:
    if isinstance(data, dict):
        items = []
        for key, value in data.items():
            if isinstance(value, (dict, list)) and depth >= 1:
                items.append(f"{key}=…")
            else:
                items.append(f"{key}={_summarize(value, depth=depth + 1)}")
        return ", ".join(items) if depth else "; ".join(items)
    if isinstance(data, list):
        if len(data) > 8:
            return f"[{len(data)} items]"
        return "[" + ", ".join(_summarize(item, depth=depth + 1) for item in data) + "]"
    if isinstance(data, float):
        return format(data, ".6g")
    return str(data)


def spec_help_text(specs: Sequence[CommandSpec] | None = None) -> str:
    """Return a human-readable listing of all commands and parameters."""
    lines: list[str] = []
    for spec in specs if specs is not None else command_specs():
        lines.append(spec.usage())
        lines.append(f"    {spec.help}")
        for parameter in spec.parameters:
            extra = ""
            if parameter.choices:
                extra = f" ({'|'.join(parameter.choices)})"
            if not parameter.required:
                extra += f" [default: {parameter.default!r}]"
            lines.append(f"    {parameter.name}: {parameter.help}{extra}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
