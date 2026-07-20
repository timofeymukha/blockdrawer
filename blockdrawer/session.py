"""Versioned JSON session persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    Block,
    Boundary,
    EdgeGeometry,
    MeshModel,
    TopologyError,
    Vertex,
    edge_key,
)


FORMAT_NAME = "blockDrawer"
FORMAT_VERSION = 4


class SessionError(ValueError):
    """Raised when a session file is malformed or unsupported."""


def to_data(model: MeshModel) -> dict[str, Any]:
    model.validate()
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "settings": {
            "zCells": model.z_cells,
            "zMin": model.z_min,
            "zMax": model.z_max,
            "scale": model.scale,
        },
        "vertices": [
            {"id": vertex.id, "x": vertex.x, "y": vertex.y}
            for vertex in model.vertices.values()
        ],
        "blocks": [
            {"id": block.id, "vertices": list(block.vertices)}
            for block in model.blocks
        ],
        "edgeCells": [
            {"vertices": list(current), "cells": model.edge_cells[current]}
            for current in model.edges()
        ],
        "edgeGeometry": [
            {
                "vertices": list(current),
                "type": geometry.kind,
                "points": [
                    {"x": point[0], "y": point[1]}
                    for point in geometry.points
                ],
            }
            for current in model.edges()
            if (geometry := model.edge_geometry.get(current)) is not None
        ],
        "edgeGrading": [
            {
                "vertices": list(current),
                "expansionRatio": model.edge_grading[current],
            }
            for current in model.edges()
            if current in model.edge_grading
        ],
        "boundaries": [
            {
                "name": boundary.name,
                "type": boundary.kind,
                "color": boundary.color,
                **(
                    {"neighbourPatch": boundary.neighbour_patch}
                    if boundary.neighbour_patch is not None else {}
                ),
            }
            for boundary in model.boundaries.values()
        ],
        "edgeBoundaries": [
            {
                "vertices": list(current),
                "boundary": model.edge_boundaries[current],
            }
            for current in model.edges()
            if current in model.edge_boundaries
        ],
    }


def from_data(data: Any) -> MeshModel:
    try:
        if not isinstance(data, dict):
            raise SessionError("The session root must be a JSON object")
        if data.get("format") != FORMAT_NAME:
            raise SessionError("This is not a BlockDrawer session")
        version = data.get("version")
        if version not in (1, 2, 3, FORMAT_VERSION):
            raise SessionError(
                f"Unsupported BlockDrawer session version {version!r}"
            )

        settings = _mapping(data, "settings")
        vertices_data = _list(data, "vertices")
        blocks_data = _list(data, "blocks")
        cells_data = _list(data, "edgeCells")
        # Version 1 stored straight edges only. Treat the absent geometry list
        # as an explicit migration to version 2's implicit-line representation.
        geometry_data = [] if version == 1 else _list(data, "edgeGeometry")
        grading_data = [] if version in (1, 2) else _list(data, "edgeGrading")
        boundaries_data = [] if version in (1, 2, 3) else _list(data, "boundaries")
        edge_boundaries_data = (
            [] if version in (1, 2, 3) else _list(data, "edgeBoundaries")
        )

        model = MeshModel(initialize=False)
        for item in vertices_data:
            if not isinstance(item, dict):
                raise SessionError("Each vertex must be an object")
            identifier = _string(item, "id")
            if identifier in model.vertices:
                raise SessionError(f"Duplicate vertex ID {identifier!r}")
            model.vertices[identifier] = Vertex(
                identifier,
                _number(item, "x"),
                _number(item, "y"),
            )

        for item in blocks_data:
            if not isinstance(item, dict):
                raise SessionError("Each block must be an object")
            identifier = _string(item, "id")
            vertex_ids = _list(item, "vertices")
            if len(vertex_ids) != 4 or not all(isinstance(value, str)
                                                for value in vertex_ids):
                raise SessionError(
                    f"Block {identifier!r} must contain four vertex IDs"
                )
            model.blocks.append(Block(identifier, tuple(vertex_ids)))  # type: ignore[arg-type]

        for item in cells_data:
            if not isinstance(item, dict):
                raise SessionError("Each edgeCells entry must be an object")
            vertex_ids = _list(item, "vertices")
            if len(vertex_ids) != 2 or not all(isinstance(value, str)
                                                for value in vertex_ids):
                raise SessionError("An edgeCells entry needs two vertex IDs")
            current = edge_key(vertex_ids[0], vertex_ids[1])
            if current in model.edge_cells:
                raise SessionError(f"Duplicate edge cell data for {current!r}")
            model.edge_cells[current] = _integer(item, "cells")

        for item in geometry_data:
            if not isinstance(item, dict):
                raise SessionError("Each edgeGeometry entry must be an object")
            vertex_ids = _list(item, "vertices")
            if len(vertex_ids) != 2 or not all(isinstance(value, str)
                                                for value in vertex_ids):
                raise SessionError("An edgeGeometry entry needs two vertex IDs")
            current = edge_key(vertex_ids[0], vertex_ids[1])
            if current in model.edge_geometry:
                raise SessionError(f"Duplicate edge geometry for {current!r}")
            points_data = _list(item, "points")
            points: list[tuple[float, float]] = []
            for point in points_data:
                if not isinstance(point, dict):
                    raise SessionError(
                        "Each edge interpolation point must be an object"
                    )
                points.append((_number(point, "x"), _number(point, "y")))
            model.edge_geometry[current] = EdgeGeometry(
                _string(item, "type"), tuple(points)
            )

        for item in grading_data:
            if not isinstance(item, dict):
                raise SessionError("Each edgeGrading entry must be an object")
            vertex_ids = _list(item, "vertices")
            if len(vertex_ids) != 2 or not all(isinstance(value, str)
                                                for value in vertex_ids):
                raise SessionError("An edgeGrading entry needs two vertex IDs")
            current = edge_key(vertex_ids[0], vertex_ids[1])
            if current in model.edge_grading:
                raise SessionError(f"Duplicate edge grading for {current!r}")
            model.edge_grading[current] = _number(item, "expansionRatio")

        for item in boundaries_data:
            if not isinstance(item, dict):
                raise SessionError("Each boundary entry must be an object")
            name = _string(item, "name")
            if name in model.boundaries:
                raise SessionError(f"Duplicate boundary name {name!r}")
            neighbour_patch = item.get("neighbourPatch")
            if neighbour_patch is not None and not isinstance(neighbour_patch, str):
                raise SessionError("neighbourPatch must be a string when present")
            model.boundaries[name] = Boundary(
                name,
                _string(item, "type"),
                _string(item, "color"),
                neighbour_patch,
            )

        for item in edge_boundaries_data:
            if not isinstance(item, dict):
                raise SessionError("Each edgeBoundaries entry must be an object")
            vertex_ids = _list(item, "vertices")
            if len(vertex_ids) != 2 or not all(
                isinstance(value, str) for value in vertex_ids
            ):
                raise SessionError("An edgeBoundaries entry needs two vertex IDs")
            current = edge_key(vertex_ids[0], vertex_ids[1])
            if current in model.edge_boundaries:
                raise SessionError(
                    f"Duplicate boundary assignment for edge {current!r}"
                )
            model.edge_boundaries[current] = _string(item, "boundary")

        model.z_cells = _integer(settings, "zCells")
        model.z_min = _number(settings, "zMin")
        model.z_max = _number(settings, "zMax")
        model.scale = _number(settings, "scale")
        model.validate()
        return model
    except SessionError:
        raise
    except (KeyError, TypeError, ValueError, TopologyError) as exc:
        raise SessionError(str(exc)) from exc


def save_session(model: MeshModel, path: str | Path) -> None:
    destination = Path(path)
    destination.write_text(
        json.dumps(to_data(model), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_session(path: str | Path) -> MeshModel:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"Could not read {source}: {exc}") from exc
    return from_data(data)


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise SessionError(f"{key!r} must be an object")
    return value


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise SessionError(f"{key!r} must be an array")
    return value


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SessionError(f"{key!r} must be a non-empty string")
    return value


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionError(f"{key!r} must be a number")
    return float(value)


def _integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SessionError(f"{key!r} must be an integer")
    return value
