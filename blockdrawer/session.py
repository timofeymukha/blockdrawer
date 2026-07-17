"""Versioned JSON session persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Block, MeshModel, TopologyError, Vertex, edge_key


FORMAT_NAME = "blockDrawer"
FORMAT_VERSION = 1


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
    }


def from_data(data: Any) -> MeshModel:
    try:
        if not isinstance(data, dict):
            raise SessionError("The session root must be a JSON object")
        if data.get("format") != FORMAT_NAME:
            raise SessionError("This is not a BlockDrawer session")
        if data.get("version") != FORMAT_VERSION:
            raise SessionError(
                f"Unsupported BlockDrawer session version {data.get('version')!r}"
            )

        settings = _mapping(data, "settings")
        vertices_data = _list(data, "vertices")
        blocks_data = _list(data, "blocks")
        cells_data = _list(data, "edgeCells")

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
