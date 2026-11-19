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
from .smooth import SHAPE_LENGTH_RATIO


EDGE_SEPARATOR = "-"


class CommandError(ValueError):
    """Raised when a command is malformed or its model operation fails."""


@dataclass(frozen=True)
class Parameter:
    """One typed command argument.

    ``kind`` is one of ``edge``, ``directed_edge`` (the same edge, kept in
    the order written, for values given along it), ``edges``, ``vertex``,
    ``vertices``, ``curve``, ``curves``, ``int``, ``float``, ``str``,
    ``bool``, ``point``, ``points``, ``choice``, ``optional_str``,
    ``blocks`` (block IDs) or ``numbers`` (a list of numbers).
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
    return edge_key(*parse_directed_edge(model, value))


def parse_directed_edge(model: MeshModel, value: Any) -> tuple[str, str]:
    """Resolve ``a-b`` text or a two-item sequence into a known edge, in the order written.

    The topological edge is unordered, but a grading value or a split
    fraction is given along the edge, and the natural reading is the order
    the agent wrote: ``set_edge_grading v9-v2 start_width 0.01`` puts the
    small cell at ``v9``. A hand-built mesh whose every edge sorted the other
    way got all its gradings crossed before this read the written order.
    """
    if isinstance(value, str):
        candidates: list[tuple[str, str]] = []
        for index in range(1, len(value)):
            if value[index] != EDGE_SEPARATOR:
                continue
            first = value[:index]
            second = value[index + 1:]
            if first in model.vertices and second in model.vertices \
                    and first != second:
                candidates.append((first, second))
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
        first, second = candidates[0]
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
    return (first, second)


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
    if kind == "directed_edge":
        return parse_directed_edge(model, value)
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
    if kind == "blocks":
        known = {block.id for block in model.blocks}
        identifiers = [str(item).strip() for item in _coerce_list(value, name)]
        unknown = [item for item in identifiers if item not in known]
        if unknown:
            raise CommandError(f"Unknown block(s) {', '.join(unknown)}")
        return tuple(identifiers)
    if kind == "numbers":
        if isinstance(value, str) and not value.strip().startswith("["):
            items: list[Any] = [item for item in value.replace(",", " ").split() if item]
        else:
            items = _coerce_list(value, name)
        return tuple(_coerce_float(item, name) for item in items)
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


_REVERSED_GRADING = {
    "start_width": "end_width",
    "end_width": "start_width",
    "total_ratio": "total_ratio",
    "cell_ratio": "cell_ratio",
}


def _run_set_edge_grading(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    written = args["edge"]
    edge = edge_key(*written)
    parameter, value = args["parameter"], args["value"]
    if tuple(written) != edge:
        # The value was given along the edge as written; the model stores
        # gradings in canonical order, so read it the other way round.
        parameter = _REVERSED_GRADING[parameter]
        if parameter in ("total_ratio", "cell_ratio") and value > 0.0:
            value = 1.0 / value
    model.set_edge_grading(edge, parameter, value, propagate=args["propagate"])
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
    model.move_vertex(
        args["vertex"], args["x"], args["y"], carry_curves=bool(args.get("carry_curves", False))
    )
    vertex = model.vertices[args["vertex"]]
    return {"vertex": vertex.id, "x": vertex.x, "y": vertex.y}


def _run_add_vertex(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    vertex = model.add_vertex(args["x"], args["y"], vertex_id=args.get("id"))
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
    identifiers = list(args["vertices"])
    if args["points"]:
        from .construct import resolve_points

        identifiers += resolve_points(model, args["points"])
    if len(identifiers) != 4:
        raise TopologyError(
            f"a block takes four vertices (IDs and points together), got {len(identifiers)}"
        )
    block = model.add_block_from_vertices(identifiers)
    return _block_data(model, block.id)


def _run_remove_edge(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    removed = model.remove_edge(args["edge"])
    return {
        "edge": edge_text(args["edge"]),
        "removed_blocks": [block.id for block in removed],
        "remaining_blocks": [block.id for block in model.blocks],
    }


def _run_split_edge(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    written = args["edge"]
    edge = edge_key(*written)
    fraction = args["fraction"]
    if tuple(written) != edge:
        # The fraction counts from the first vertex as written.
        fraction = 1.0 - fraction
    result = model.split_edge(edge, fraction, vertex_id=args.get("id"))
    return to_json_value(result)


def _run_combine_blocks(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    result = model.combine_blocks(args["edge"])
    return to_json_value(result)


def _run_repair_block(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .repair import MOVES, repair_block

    chosen = args.get("moves")
    moves = tuple(item.strip() for item in chosen.split(",") if item.strip()) if chosen else MOVES
    result = repair_block(model, args["block"], moves=moves)
    return to_json_value(result)


def _run_smooth(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .smooth import SmoothOptions, smooth_model

    bbox = args.get("bbox") or None
    if bbox is not None and len(bbox) != 4:
        raise CommandError("bbox needs four numbers: xmin, ymin, xmax, ymax")
    options = SmoothOptions(
        band_tolerance=args["band_tolerance"],
        size_cap=args["size_cap"],
        length_tolerance=args["length_tolerance"],
        mode=args["mode"],
        allow_stale_fold=args["allow_stale_fold"],
        slide=args["slide"],
        shape_length_ratio=args["shape_length_ratio"],
        keep_lines=args["keep_lines"],
    )
    result = smooth_model(
        model,
        blocks=args.get("blocks") or None,
        bbox=bbox,
        hold=args.get("hold") or (),
        options=options,
    )
    return result.to_data()


def _run_size(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .size import SizeOptions, size_model

    overrides = []
    for item in (args.get("first_widths") or "").replace(";", ",").split(","):
        if not item.strip():
            continue
        name, separator, value = item.partition("=")
        if not separator:
            raise CommandError("first_widths takes PATCH=WIDTH pairs, comma separated")
        overrides.append((name.strip(), _coerce_float(value, "first_widths")))
    bbox = args.get("bbox") or None
    if bbox is not None and len(bbox) != 4:
        raise CommandError("bbox needs four numbers: xmin, ymin, xmax, ymax")
    options = SizeOptions(
        first_width=args["first_width"],
        core_size=args["core_size"],
        growth=args["growth"],
        wall_aspect=args["wall_aspect"] or None,
        interface_growth=(
            (1.25 if args["method"] == "directional" else None)
            if args["interface_growth"] < 0 else (args["interface_growth"] or None)
        ),
        count_rule=args["count_rule"],
        budget=args["budget"] or None,
        patch_first_widths=tuple(overrides),
        split=args["split"],
        keep_counts=args["keep_counts"],
        method=args["method"],
        grading=args["grading"],
    )
    result = size_model(model, options, blocks=args.get("blocks") or None, bbox=bbox)
    return result.to_data()


def _run_add_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    boundary = model.add_boundary(args["name"])
    return to_json_value(boundary)


def _run_remove_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    model.remove_boundary(args["name"])
    return {"removed": args["name"], "boundaries": sorted(model.boundaries)}


def _run_rename_boundary(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    boundary = model.rename_boundary(args["name"], args["new_name"])
    return to_json_value(boundary)


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
_DIRECTED_EDGE = Parameter(
    "edge", "directed_edge",
    "edge as first-second; the value is read along the edge in the order written",
)
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
        _DIRECTED_EDGE,
        Parameter(
            "parameter", "choice",
            "which representation the value uses",
            choices=MeshModel.GRADING_PARAMETERS,
        ),
        Parameter("value", "float", "positive grading value along the edge as written (start_width at the first vertex)"),
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
    (
        "Move a vertex; every incident block must stay strictly convex. By "
        "default the interpolation points of incident curved edges stay put; "
        "carry_curves=true carries each curve with the vertex (scaled and "
        "rotated about the edge's other end), so an arc stays an arc."
    ),
    (
        Parameter("vertex", "vertex", "vertex ID"),
        Parameter("x", "float", "new x coordinate"),
        Parameter("y", "float", "new y coordinate"),
        Parameter(
            "carry_curves", "bool",
            "carry incident curved edges with the vertex instead of leaving their points",
            required=False, default=False,
        ),
    ),
    _run_move_vertex,
))
_register(CommandSpec(
    "add_vertex",
    "Create a standalone vertex for later use by add_block_from_vertices.",
    (
        Parameter("x", "float", "x coordinate"),
        Parameter("y", "float", "y coordinate"),
        Parameter(
            "id", "str",
            "ID for the new vertex (default: the next free vN); naming the "
            "vertices lets a batch refer to them before it is applied",
            required=False, default=None,
        ),
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
    (
        "Create a block from four existing vertices given in any order: by "
        "ID, or by points x,y;x,y each naming the vertex there (within the "
        "construction operators' tolerance, a billionth of the drawing's "
        "size), or both, four in all."
    ),
    (
        Parameter("vertices", "vertices", "vertex IDs, e.g. v1,v2,v5,v6",
                  required=False, default=()),
        Parameter("points", "points", "coordinates of existing vertices, x,y;x,y",
                  required=False, default=()),
    ),
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
        _DIRECTED_EDGE,
        Parameter(
            "fraction", "float",
            "0-1 position along the edge from the first vertex as written; "
            "the new vertex sits there, and the two halves take the cell "
            "counts of the nearest existing mesh node, so a fraction on a "
            "node keeps the node locations",
        ),
        Parameter(
            "id", "str",
            "ID for the new vertex on this edge; the vertices on the other "
            "edges of the strip become ID_1, ID_2, ... in affected_edges "
            "order (default: the next free vN), so later commands in the "
            "same batch can name the halves",
            required=False, default=None,
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
    "repair_block",
    (
        "Improve a block by the best local move - moving one of its interior "
        "vertices (curved edges carried along), combining it across an internal "
        "edge, splitting one of its edges, or regrading an edge to the series "
        "of its opposite edge - judged on the block and its neighbours by the "
        "worst cell and the count of severely sheared sampled cells; boundary "
        "vertices never move, and nothing changes when no move helps."
    ),
    (
        Parameter("block", "str", "block ID"),
        Parameter(
            "moves", "optional_str",
            "comma-separated subset of move,combine,split,grade (default all)",
            required=False, default=None,
        ),
    ),
    _run_repair_block,
))
_register(CommandSpec(
    "smooth",
    (
        "Move the free interior vertices (curved edges carried along) to lower "
        "checkMesh's face non-orthogonality over the sampled block interiors at "
        "the real counts and grading, without raising any block's worst face; "
        "boundary and held vertices stay, wall band spokes keep their length "
        "(band_tolerance), no edge changes length by more than "
        "length_tolerance, and no interface size jump crosses size_cap. The "
        "result is applied only when the full sampled check stays untangled "
        "with no more severe faces and no higher maximum. mode=shape optimises "
        "the count-free block shape instead, before size sets counts and grading, "
        "every edge keeping shape_length_ratio of its length. keep_lines=true "
        "moves a vertex on a straight block line only along it. "
        "slide=true also slides boundary vertices along their boundary. The "
        "reason gives the shortest and longest edge over its input length."
    ),
    (
        Parameter(
            "blocks", "blocks", "only the corners of these blocks move (default: all)",
            required=False, default=(),
        ),
        Parameter(
            "bbox", "numbers", "only vertices inside xmin,ymin,xmax,ymax move",
            required=False, default=(),
        ),
        Parameter(
            "hold", "vertices", "vertices that must not move", required=False, default=(),
        ),
        Parameter(
            "band_tolerance", "float",
            "largest relative length change of an edge from a wall vertex",
            required=False, default=0.05,
        ),
        Parameter(
            "length_tolerance", "float",
            "largest relative length change of any edge (0: unbounded)",
            required=False, default=0.5,
        ),
        Parameter(
            "size_cap", "float",
            "a first-cell size jump across an internal edge below this may not "
            "cross it; one above may grow by a tenth",
            required=False, default=2.5,
        ),
        Parameter(
            "mode", "choice",
            "real judges the mesh the stored counts and grading define; shape the "
            "count-free block map, without size limits, leaving the counts "
            "and grading stale for size to set",
            required=False, default="real", choices=("real", "shape"),
        ),
        Parameter(
            "shape_length_ratio", "float",
            "in shape mode, every edge keeps at least this fraction of its input "
            "length (0: unbounded)",
            required=False, default=SHAPE_LENGTH_RATIO,
        ),
        Parameter(
            "keep_lines", "bool",
            "an interior vertex on a straight block line (two opposite line edges, "
            "collinear) moves only along it; on two such lines it stays",
            required=False, default=False,
        ),
        Parameter(
            "allow_stale_fold", "bool",
            "in shape mode, apply even when the stored counts and grading would fold "
            "a real cell (size follows)",
            required=False, default=False,
        ),
        Parameter(
            "slide", "bool",
            "boundary vertices on a smooth path of one patch slide along it (edges "
            "re-cut from the input boundary; a wall vertex carries its band spoke); "
            "corners, patch junctions and held vertices stay",
            required=False, default=False,
        ),
    ),
    _run_smooth,
))


def _run_slide_vertex(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .slide import slide_vertex

    return slide_vertex(
        model, args["vertex"], fraction=args["fraction"], distance=args["distance"],
        towards=args["towards"],
    )


_register(CommandSpec(
    "slide_vertex",
    (
        "Slide a boundary vertex along its boundary: its two exterior edges "
        "(one patch, no corner at the vertex) are re-cut from their current "
        "path - lines stay lines, arcs on one circle stay arcs, splines are cut "
        "from the path. fraction places it on the path between its two "
        "boundary neighbours (0 at the first, 1 at towards); distance moves it "
        "that arc length towards towards (default: the neighbour with the "
        "larger ID). Internal curved edges are carried; band spokes are not."
    ),
    (
        Parameter("vertex", "vertex", "the boundary vertex"),
        Parameter("fraction", "float", "position between the neighbours, 0..1",
                  required=False, default=None),
        Parameter("distance", "float", "arc length towards towards (signed)",
                  required=False, default=None),
        Parameter("towards", "optional_str", "the neighbour fraction 1 and positive distance point to",
                  required=False, default=None),
    ),
    _run_slide_vertex,
))
_register(CommandSpec(
    "size",
    (
        "Set every cell count and grading from a directional size field: "
        "first_width across patches of type wall, growing by growth away from "
        "them up to core_size, and wall_aspect first widths along them. Every "
        "edge end's width is bounded by the field and by interface_growth "
        "across block interfaces; each strip of opposite edges shares one "
        "geometric series, each opposite-edge component takes the max (or "
        "length-weighted mean) of its edges' demands, and a strip that wants "
        "fine cells at both ends is cut where its size peaks (split). Then "
        "cells are spent on continuity: count raises and regrades are taken "
        "greedily by the drop in interface jumps per cell until budget total "
        "cells (default 1.5 times the field's own count) are used or nothing "
        "gains. Before spending, a block the field leaves above skewness 4 or "
        "with faces above 70 degrees is repaired by the same moves. Each move "
        "is checked on the sampled grid (256 cells a side) and refused if it "
        "folds a cell, adds a face above 70 degrees, raises the worst face "
        "past the field's or takes skewness past 4; the result is verified at "
        "256 a side (real counts for blocks with faces above 65 degrees) "
        "against the field's repaired state - no more faces above 70 degrees, "
        "worst face at most 0.5 degrees higher - and spending is undone where "
        "it breaks that; both verdicts are reported. The faces above 70 the "
        "field state itself has are not removed. A result above skewness 4 "
        "from an input below is refused. Strips the spent budget leaves coarse between finer "
        "neighbours at both ends are split and the budget spent again when "
        "that lowers the jumps. Counts and gradings are written directly: "
        "spacing links are kept and reported when out of sync."
    ),
    (
        Parameter("first_width", "float", "first cell width at a wall"),
        Parameter("core_size", "float", "largest cell, away from walls"),
        Parameter("growth", "float", "cell-to-cell growth away from walls",
                  required=False, default=1.2),
        Parameter("wall_aspect", "float",
                  "wall-parallel spacing at a wall in first widths (0: first width)",
                  required=False, default=0.0),
        Parameter("interface_growth", "float",
                  "largest cell-size ratio across a block interface (0: unmatched; "
                  "default 1.25 with method=directional, unmatched with distance)",
                  required=False, default=-1.0),
        Parameter("count_rule", "choice", "count of a component from its edges' demands",
                  required=False, default="max", choices=("max", "mean")),
        Parameter("budget", "int",
                  "total cells to spend on continuity (0: 1.5 times the field's own; "
                  "below the field's own the field is coarsened)",
                  required=False, default=0),
        Parameter("first_widths", "optional_str",
                  "per-patch first widths, PATCH=WIDTH comma separated",
                  required=False, default=None),
        Parameter("split", "bool", "split two-ended strips where their size peaks",
                  required=False, default=True),
        Parameter("method", "choice",
                  "directional (default) or distance: item 3's wall-distance sizing, "
                  "no budget spending; compare the two by check when the directional "
                  "field's verification shows faces above 70 degrees",
                  required=False, default="directional", choices=("directional", "distance")),
        Parameter("keep_counts", "bool",
                  "keep every count; the gradings are recomputed and regraded for "
                  "continuity, no count is raised (after a smoothing or a hand count)",
                  required=False, default=False),
        Parameter("grading", "choice",
                  "strip: opposite sides through a strip share one total ratio; field: "
                  "a last pass grades each edge by the field where it lies at its count (a "
                  "wall spoke starts at the first width, far edges relax) where the shear, "
                  "the interface jumps and the sampled grid allow; counts unchanged",
                  required=False, default="strip", choices=("strip", "field")),
        Parameter("blocks", "blocks", "only these blocks' edges are graded (default: all)",
                  required=False, default=()),
        Parameter("bbox", "numbers", "only blocks with a corner inside xmin,ymin,xmax,ymax",
                  required=False, default=()),
    ),
    _run_size,
))
def _run_add_grid(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import add_grid

    return add_grid(
        model, xs=args["xs"], ys=args["ys"], center=args["center"], rs=args["rs"],
        thetas=args["thetas"], period=args["period"], skip=args["skip"],
        outside=args["outside"], conform=args["conform"], prefix=args["id"],
    ).to_data()


def _run_add_band(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import add_band

    return add_band(
        model, edges=args["edges"], bbox=args["bbox"], height=args["height"],
        rows=args["rows"], growth=args["growth"], to_box=args["to_box"], prefix=args["id"],
    ).to_data()


def _run_add_ring(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import add_ring

    return add_ring(
        model, args["curve"], args["height"], count=args["count"],
        fractions=args["fractions"], normals=args["normals"], corner=args["corner"],
        tips=args["tips"], rows=args["rows"], growth=args["growth"], prefix=args["id"],
    ).to_data()


def _run_add_stations(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import add_stations

    return add_stations(
        model, args["curve"], count=args["count"], between=args["between"],
        fractions=args["fractions"], xs=args["xs"], ys=args["ys"],
        offset=args["offset"], prefix=args["id"],
    ).to_data()


def _run_add_fill(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import add_fill

    return add_fill(
        model, args["corners"],
        sides=tuple(args[f"side{k}"] for k in range(1, 6)),
        curves=tuple(args[f"curve{k}"] for k in range(1, 6)),
        prefix=args["id"],
    ).to_data()


def _run_set_boundary_by(model: MeshModel, args: dict[str, Any]) -> dict[str, Any]:
    from .construct import set_boundary_by

    return set_boundary_by(
        model, args["name"], kind=args["type"] or None, curves=args["curves"],
        tolerance=args["tolerance"], bbox=args["bbox"], between=args["between"],
        unassigned=args["unassigned"],
    ).to_data()


_ID = Parameter("id", "optional_str",
                "name new vertices ID_0, ID_1, ... and blocks ID_b0, ... (default: next vN, bN)",
                required=False, default=None)
_register(CommandSpec(
    "add_grid",
    (
        "Add blocks from station lists: xs and ys, or radii rs and angles "
        "thetas (degrees) about center, repeated every period degrees round "
        "the circle (circumferential edges are arcs). A cell is left out when "
        "its centre lies in a skip box (xmin,ymin,xmax,ymax, several allowed), "
        "inside an outside curve, or inside an existing block. Vertices "
        "coincident with existing ones are reused; with conform (default) "
        "existing vertices lying on a grid line become stations, so the grid "
        "closes conformally round what is there."
    ),
    (
        Parameter("xs", "numbers", "x stations", required=False, default=()),
        Parameter("ys", "numbers", "y stations", required=False, default=()),
        Parameter("center", "numbers", "centre x,y of a polar grid", required=False, default=()),
        Parameter("rs", "numbers", "radii of a polar grid", required=False, default=()),
        Parameter("thetas", "numbers", "angles of a polar grid, degrees", required=False, default=()),
        Parameter("period", "float", "repeat thetas every period degrees (0: no repeat)",
                  required=False, default=0.0),
        Parameter("skip", "numbers", "boxes whose cells are left out, 4 numbers each",
                  required=False, default=()),
        Parameter("outside", "curves", "leave out cells inside these closed curves",
                  required=False, default=()),
        Parameter("conform", "bool", "take existing vertices on grid lines as stations",
                  required=False, default=True),
        _ID,
    ),
    _run_add_grid,
))
_register(CommandSpec(
    "add_band",
    (
        "Add a band of blocks along a chain or loop of exterior edges (edges, "
        "or the unassigned exterior edges inside bbox): spokes along the "
        "mitred normals, height high in rows rows (growth: each row's height "
        "over the previous). Where the chain turns convexly by more than "
        "60 degrees it gets a fan of corner blocks, one per 90 degrees of "
        "turning or part of it (one block at a square corner, two above a "
        "90-degree turn); at a smaller turn or a concave corner one mitred "
        "spoke (at most 3 heights long). Open ends follow the boundary they "
        "meet; offset arcs stay arcs. With to_box every spoke runs to that "
        "rectangle instead, a box corner taken by the nearest spoke, and no "
        "corner gets a fan."
    ),
    (
        Parameter("edges", "edges", "the chain's edges", required=False, default=()),
        Parameter("bbox", "numbers", "select the unassigned exterior edges inside this box",
                  required=False, default=()),
        Parameter("height", "float", "band height (0 with to_box)", required=False, default=0.0),
        Parameter("rows", "int", "rows of blocks", required=False, default=1),
        Parameter("growth", "float", "row height ratio", required=False, default=1.0),
        Parameter("to_box", "numbers", "xmin,ymin,xmax,ymax the spokes run to",
                  required=False, default=()),
        _ID,
    ),
    _run_add_band,
))
_register(CommandSpec(
    "add_ring",
    (
        "Add a band of blocks round a closed reference curve: vertices on the "
        "curve at its corners (vertices turning more than corner degrees), "
        "at the given arc-length fractions from its first point, where the "
        "outward normal points at each of the normals angles (degrees "
        "counter-clockwise from +x; exactly one such point is required), "
        "with tips=true at the peak of each rounded convex corner (a nose "
        "turning 45 degrees within a hundredth of the perimeter either "
        "side), and count edges spread by arc length between them; wall "
        "edges as splines through the curve's points, then the band as "
        "add_band builds it, height high: a station where the curve turns "
        "convexly by more than 60 degrees gets a fan of corner blocks, one "
        "per 90 degrees of turning or part of it (a square corner one block, "
        "a sharp trailing edge two or more), every other station one spoke."
    ),
    (
        Parameter("curve", "curve", "closed reference curve (ID or name)"),
        Parameter("height", "float", "band height"),
        Parameter("count", "int", "edges round the curve (0: stations only)",
                  required=False, default=0),
        Parameter("fractions", "numbers", "arc-length fractions of extra stations",
                  required=False, default=()),
        Parameter("normals", "numbers", "outward-normal angles (degrees) of extra stations",
                  required=False, default=()),
        Parameter("corner", "float", "a vertex turning more than this many degrees is a station",
                  required=False, default=30.0),
        Parameter("tips", "bool", "a station at the peak of each rounded convex corner",
                  required=False, default=False),
        Parameter("rows", "int", "rows of blocks", required=False, default=1),
        Parameter("growth", "float", "row height ratio", required=False, default=1.0),
        _ID,
    ),
    _run_add_ring,
))
_register(CommandSpec(
    "add_stations",
    (
        "Add standalone vertices on a reference curve, ordered along it (on "
        "the polyline through its points, where add_ring puts its stations): "
        "count of them spread by arc length over the curve or over between "
        "(two fractions, or two points taken to the curve; forward round a "
        "closed curve), the arc-length fractions of the whole curve, and "
        "where it crosses each xs and ys (exactly one crossing, else "
        "refused). offset=DX,DY translates them all: the stations of a "
        "periodic partner chain, node for node. id=NAME names station k "
        "NAME_k; a station on an existing vertex reuses it."
    ),
    (
        Parameter("curve", "curve", "reference curve (ID or name)"),
        Parameter("count", "int", "stations spread by arc length, ends included (0: none)",
                  required=False, default=0),
        Parameter("between", "numbers", "section: two fractions, or x0,y0,x1,y1",
                  required=False, default=()),
        Parameter("fractions", "numbers", "arc-length fractions of the curve", required=False, default=()),
        Parameter("xs", "numbers", "stations where the curve crosses these x", required=False, default=()),
        Parameter("ys", "numbers", "stations where the curve crosses these y", required=False, default=()),
        Parameter("offset", "numbers", "dx,dy added to every station", required=False, default=()),
        Parameter("id", "optional_str", "name station k ID_k (default: next vN)",
                  required=False, default=None),
    ),
    _run_add_stations,
))
_register(CommandSpec(
    "add_fill",
    (
        "Fill a four-sided region with blocks: corners A,B,C,D in order round "
        "it (either way; counter-clockwise where both ways bound a region, as "
        "in a half annulus), side k from corner k to corner k+1. A side is the "
        "vertex list given for it (sideK=A,...,B); else the chain of exterior "
        "edges joining its corners with their blocks outside the region (a "
        "band front, a grid's side; the most such sides are taken); else "
        "open: straight, or along curveK, through the standalone vertices "
        "lying on it, or with none through new stations at the opposite "
        "side's arc-length fractions (one edge when both are open). Opposite "
        "sides need as many edges (a refusal says where to split or add a "
        "station). Interior vertices by blockMesh's transfinite interpolation "
        "of the sides; new side edges between two points on curveK follow it "
        "as splines. A block that would not be strictly convex is refused by "
        "name, nothing built. Three or five corners: that many blocks round a "
        "centre vertex, side k split at a station so that side k's edge count "
        "is the sum of spokes k-1 and k+1 (the counts summing to an even "
        "number; refused with the spoke counts the sides give otherwise)."
    ),
    (
        Parameter("corners", "vertices", "three, four or five corner vertices in order round the region"),
        *(Parameter(f"side{k}", "vertices",
                    f"side {k}'s stations from corner {k} to corner {k + 1} (or 1; either order)",
                    required=False, default=()) for k in range(1, 6)),
        *(Parameter(f"curve{k}", "curve", f"reference curve side {k} follows where it is open or new",
                    required=False, default=None) for k in range(1, 6)),
        _ID,
    ),
    _run_add_fill,
))
_register(CommandSpec(
    "set_boundary_by",
    (
        "Assign to patch name (created if missing, type if given) every "
        "exterior edge chosen by all the given selectors: within tolerance "
        "(default a thousandth of the curve's size) of the curves, inside "
        "bbox, on the shortest exterior chain between two vertices; "
        "unassigned=true leaves assigned edges alone. A curve is closed from "
        "its last point to its first where the edge's block lies outside it "
        "(a body's implied base), not where the chord bounds the domain."
    ),
    (
        Parameter("name", "str", "patch name"),
        Parameter("type", "optional_str", "patch type (patch, wall, symmetry, empty)",
                  required=False, default=None),
        Parameter("curves", "curves", "reference curves the edges lie on", required=False, default=()),
        Parameter("tolerance", "float", "distance to the curves (0: a thousandth of their size)",
                  required=False, default=0.0),
        Parameter("bbox", "numbers", "xmin,ymin,xmax,ymax", required=False, default=()),
        Parameter("between", "vertices", "two exterior vertices", required=False, default=()),
        Parameter("unassigned", "bool", "only edges with no patch yet", required=False, default=False),
    ),
    _run_set_boundary_by,
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
    "rename_boundary",
    "Rename a patch, keeping its type, edges and cyclic partner.",
    (
        Parameter("name", "str", "current patch name"),
        Parameter("new_name", "str", "new unique patch name"),
    ),
    _run_rename_boundary,
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


def format_results(results: Iterable[CommandResult]) -> list[str]:
    """Text lines for a batch's full results: one :func:`format_result` line
    per command and, under a command that has a digest note, that note on
    an indented ``note:`` line.

    The full line elides values nested two levels deep, so the note is what
    guarantees that the long form shows at least what the quiet digest
    shows (a ``size``'s verification verdicts, first widths and worst jump).
    """
    lines: list[str] = []
    for result in results:
        lines.append(format_result(result))
        note = _note(result)
        if note:
            detail = {key: value for key, value in note.items() if key not in ("index", "op")}
            lines.append(f"   note: {_summarize(detail)}")
    return lines


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


# ---------------------------------------------------------------------------
# Compact batch reports
# ---------------------------------------------------------------------------

# A quiet report lists at most this many per-command notes.
MAX_NOTES = 40


def compress_ids(identifiers: Sequence[str]) -> list[str]:
    """Collapse runs of consecutive IDs sharing a prefix into ``first..last``.

    ``["v3", "v4", "v5", "b1", "x"]`` becomes ``["v3..v5", "b1", "x"]``; the
    order of the input is kept.
    """
    result: list[str] = []
    run: list[tuple[str, int, str]] = []

    def flush() -> None:
        if not run:
            return
        if len(run) >= 3:
            result.append(f"{run[0][2]}..{run[-1][2]}")
        else:
            result.extend(item[2] for item in run)
        run.clear()

    for identifier in identifiers:
        digits = len(identifier) - len(identifier.rstrip("0123456789"))
        prefix = identifier[: len(identifier) - digits]
        text = identifier[len(prefix):]
        # Zero-padded numbers are left alone: "v007..v009" would read oddly.
        number = int(text) if text and (text == "0" or not text.startswith("0")) else None
        if run and number is not None and prefix == run[-1][0] and number == run[-1][1] + 1:
            run.append((prefix, number, identifier))
            continue
        flush()
        if number is None:
            result.append(identifier)
        else:
            run.append((prefix, number, identifier))
    flush()
    return result


def _size_note(data: dict[str, Any]) -> dict[str, Any]:
    """The ``size`` result in the fields Step 5 of the skill reads, each a
    scalar or a short string so a quiet digest prints it whole; empty ones
    are left out."""

    def verdict(item: dict[str, Any] | None) -> str:
        item = item or {}
        text = (
            f"{item.get('severe_faces', 0)} faces >70, max {item.get('max_non_orthogonality', 0.0):.2f}, "
            f"skew {item.get('max_skewness', 0.0):.2f}"
        )
        if "max_aspect_ratio" in item:
            text += f", aspect {item['max_aspect_ratio']:.0f}"
            if item.get("high_aspect_cells"):
                text += (
                    f" ({item['high_aspect_cells']} cells >1000 in {item.get('worst_aspect_block')}: "
                    "checkMesh fails)"
                )
        return text + (f", {item['folded_cells']} folded" if item.get("folded_cells") else "")

    def percent(value: float, asked: float) -> str:
        return f"{int(round(100.0 * (value / asked - 1.0))):+d}%"

    verification = data.get("verification") or {}
    bands = []
    for band in data.get("bands") or ():
        low, high = band["first_width"]
        text = f"{band['patch']} {low:.3g}..{high:.3g}"
        asked = band.get("asked")
        if asked:
            text += f" for {asked:.3g} ({percent(low, asked)}..{percent(high, asked)})"
        if band.get("first_width_edges"):
            text += " at {}..{}".format(*band["first_width_edges"])
        bands.append(text)
    worst = (data.get("worst_interfaces") or [None])[0]
    jumps = data.get("interface_jumps") or {}
    note = {
        "total_cells": data.get("total_cells"),
        "field_cells": data.get("field_cells"),
        "budget": data.get("budget"),
        "budget_binding": data.get("budget_binding"),
        "verification": (
            f"field {verdict(verification.get('field'))} | result {verdict(verification.get('result'))}"
            if verification.get("field") else None
        ),
        # Achieved first cell heights normal to each wall patch at its band
        # spokes, with the spokes of the smallest and largest.
        "first_width": " | ".join(bands) or None,
        # quality's interface measure on the sized session, before any
        # polish (a later smooth moves vertices and changes it).
        "worst_jump": (
            f"{worst['jump']:.3g} at {worst['edge']} ({'|'.join(worst['blocks'])}), above "
            f"2.5/5/20: {jumps.get('above_2_5', 0)}/{jumps.get('above_5', 0)}/{jumps.get('above_20', 0)}"
            if worst else None
        ),
        "jump_cost": [data.get("field_jump_cost"), data.get("jump_cost")],
        "splits": [item.get("edge") for item in data.get("splits", [])],
        "moves": data.get("moves"),
        "guard": data.get("guard"),
        "refused": data.get("refused"),
        # grading=field: strips graded by the field all the way, part of
        # the way, or kept on their shared ratio.
        "field_grading": (
            "{own} own, {part} part, {shared} shared of {strips} strips".format(**data["field_grading"])
            + (f" ({data['field_grading']['dropped']})" if data["field_grading"].get("dropped") else "")
            if data.get("field_grading") else None
        ),
        "note": data.get("note"),
    }
    return {key: value for key, value in note.items() if value not in (None, "", [])}


def _note(result: CommandResult) -> dict[str, Any] | None:
    """What one command reported that an agent should read, if anything."""
    data = result.result
    base = {"index": result.index + 1, "op": result.name}
    if result.name == "repair_block":
        return {
            **base,
            "block": data.get("block_id"),
            "applied": data.get("operation") is not None,
            "operation": data.get("operation"),
            "arguments": data.get("arguments"),
            "reason": data.get("reason"),
        }
    if result.name == "smooth":
        return {
            **base,
            "applied": data.get("applied"),
            "moved": data.get("moved"),
            "slid": data.get("slid"),
            "boundary_deviation": data.get("boundary_deviation"),
            "max_band_change": data.get("max_band_change"),
            "min_edge_ratio": data.get("min_edge_ratio"),
            "min_edge": data.get("min_edge"),
            "reason": data.get("reason"),
        }
    if result.name == "slide_vertex":
        return {
            **base,
            "vertex": data.get("vertex"),
            "moved": data.get("moved"),
            "deviation": data.get("deviation"),
        }
    if result.name == "size":
        return {**base, **_size_note(data)}
    if result.name in ("add_grid", "add_band", "add_ring", "add_fill"):
        note = {
            **base,
            "blocks": len(data.get("created_blocks", [])),
            "reused_vertices": data.get("reused_vertices"),
            "skipped_cells": data.get("skipped_cells"),
        }
        if data.get("note"):
            note["note"] = data["note"]
        return note
    if result.name == "set_boundary_by":
        return {**base, "edges": data.get("edges")}
    if result.name == "add_stations":
        return {**base, "stations": compress_ids(data.get("stations") or []), "note": data.get("note")}
    if result.name == "split_edge":
        return {
            **base,
            "edge": data.get("source_edge"),
            "cells": [data.get("first_cells"), data.get("second_cells")],
            "vertices": data.get("split_vertex_ids"),
            "cut_edges": data.get("cut_edges"),
        }
    if result.name == "combine_blocks":
        return {
            **base,
            "merged_blocks": data.get("merged_block_ids"),
            "removed_blocks": data.get("removed_block_ids"),
        }
    if result.name == "project" and data.get("fitted_edges"):
        return {
            **base,
            "fitted_edges": len(data.get("fitted_edges") or ()),
            "max_fit_error": data.get("max_fit_error"),
            "fit_tolerance": data.get("fit_tolerance"),
            "fit_tolerance_met": data.get("fit_tolerance_met"),
        }
    if result.name == "project" and data.get("converted_arcs"):
        return {**base, "converted_arcs": data.get("converted_arcs")}
    links = data.get("spacing_links") if isinstance(data, dict) else None
    if links and any(link.get("synchronized") is False for link in links):
        return {
            **base,
            "edge": data.get("edge"),
            "unsynchronized_links": [
                link for link in links if link.get("synchronized") is False
            ],
        }
    return None


def batch_digest(
    before: MeshModel, batch: BatchResult, *, max_notes: int = MAX_NOTES
) -> dict[str, Any]:
    """A compact account of a batch: what it did, created and removed.

    ``operations`` counts the commands by name; ``created`` and ``removed``
    list the vertex, block, curve and patch IDs that appeared or vanished
    (runs of consecutive IDs collapsed to ``first..last``); ``notes`` keeps
    the results worth reading - repairs, splits and merges, fits, spacing
    links left out of sync - at most ``max_notes`` of them, the rest counted
    in ``notes_omitted``.
    """
    after = batch.model
    operations: dict[str, int] = {}
    for result in batch.results:
        operations[result.name] = operations.get(result.name, 0) + 1

    def appeared(old: Iterable[str], new: Iterable[str]) -> list[str]:
        known = set(old)
        return [identifier for identifier in new if identifier not in known]

    created = {
        "vertices": appeared(before.vertices, after.vertices),
        "blocks": appeared((b.id for b in before.blocks), (b.id for b in after.blocks)),
        "curves": appeared(before.geometry_curves, after.geometry_curves),
        "boundaries": appeared(before.boundaries, after.boundaries),
    }
    removed = {
        "vertices": appeared(after.vertices, before.vertices),
        "blocks": appeared((b.id for b in after.blocks), (b.id for b in before.blocks)),
        "curves": appeared(after.geometry_curves, before.geometry_curves),
        "boundaries": appeared(after.boundaries, before.boundaries),
    }
    notes = [note for note in (_note(result) for result in batch.results) if note]
    shown = notes[: max(0, max_notes)]
    return {
        "operations": operations,
        "created": {key: compress_ids(value) for key, value in created.items() if value},
        "created_counts": {key: len(value) for key, value in created.items() if value},
        "removed": {key: compress_ids(value) for key, value in removed.items() if value},
        "removed_counts": {key: len(value) for key, value in removed.items() if value},
        "notes": shown,
        "notes_omitted": len(notes) - len(shown),
    }


def format_digest(applied: int, digest: dict[str, Any]) -> list[str]:
    """Text lines for :func:`batch_digest`."""
    operations = ", ".join(
        f"{name} {count}" for name, count in sorted(
            digest["operations"].items(), key=lambda item: (-item[1], item[0])
        )
    )
    lines = [f"Applied {applied} command(s)" + (f": {operations}" if operations else "")]
    for label, key in (("Created", "created"), ("Removed", "removed")):
        parts = [
            f"{digest[key + '_counts'][kind]} {kind} ({', '.join(identifiers)})"
            for kind, identifiers in digest[key].items()
        ]
        if parts:
            lines.append(f"{label}: " + "; ".join(parts))
    for note in digest["notes"]:
        detail = {key: value for key, value in note.items() if key not in ("index", "op")}
        lines.append(f"  {note['index']}. {note['op']}: {_summarize(detail)}")
    if digest["notes_omitted"]:
        lines.append(f"  ... {digest['notes_omitted']} more note(s)")
    return lines


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
