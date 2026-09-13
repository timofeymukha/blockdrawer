"""Turn the block complex into an ordinary, editable BlockDrawer session.

Nothing here weakens BlockDrawer: the model is built from the public domain
types, validated by ``MeshModel.validate()`` and written with
``blockdrawer.session.save_session()``.  Wall edges keep the supplied point
list, farfield edges keep exact circular arcs, and every body point list is
also attached as a reference curve so the result can be edited by hand.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np

try:  # pragma: no cover - import shim for running the script directly
    from blockdrawer.domain import Block as DomainBlock
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from blockdrawer.domain import Block as DomainBlock

from blockdrawer.domain import EdgeGeometry, Vertex, edge_key
from blockdrawer.model import MeshModel
from blockdrawer.session import save_session

COORDINATE_TOLERANCE = 2.0e-9


def _patch_name(name: str, used: set[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not cleaned or not re.match(r"[A-Za-z_]", cleaned[0]):
        cleaned = f"p_{cleaned}" if cleaned else "patch"
    candidate = cleaned
    index = 2
    while candidate in used:
        candidate = f"{cleaned}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _thin(points, first, second):
    """Drop interpolation points that BlockDrawer would reject as coincident."""
    kept: list[tuple[float, float]] = []
    previous = first
    for point in points:
        if math.dist(point, previous) <= COORDINATE_TOLERANCE:
            continue
        kept.append((float(point[0]), float(point[1])))
        previous = point
    while kept and math.dist(kept[-1], second) <= COORDINATE_TOLERANCE:
        kept.pop()
    return tuple(kept)


def write_session(model: MeshModel, path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_session(model, destination)
    return destination


def round_trip(path) -> MeshModel:
    """Reload a written session and validate it again."""
    from blockdrawer.session import load_session

    model = load_session(path)
    model.validate()
    return model


def topology_signature(model: MeshModel) -> dict[str, object]:
    """A permutation-stable fingerprint of the emitted topology.

    Vertex identifiers depend on construction order, so the signature counts
    structure instead: block count, edge count, the sorted vertex valences and
    the sorted number of edges carried by each named patch.
    """
    valence: dict[str, int] = {}
    for current in model.edges():
        for identifier in current:
            valence[identifier] = valence.get(identifier, 0) + 1
    boundary_counts: dict[str, int] = {}
    for current, name in model.edge_boundaries.items():
        boundary_counts[name] = boundary_counts.get(name, 0) + 1
    interior = sum(
        1 for current in model.edges() if not model.is_boundary_edge(current)
    )
    return {
        "blocks": len(model.blocks),
        "vertices": len(model.vertices),
        "edges": len(model.edges()),
        "internal_edges": interior,
        "valences": sorted(valence.values()),
        "boundary_edge_counts": sorted(boundary_counts.values()),
    }


def quality_summary(model: MeshModel) -> dict[str, object]:
    """Dimensionless quality of the emitted blocks."""
    minimum_angle = math.inf
    maximum_angle = 0.0
    worst_scaled = math.inf
    worst_aspect = 0.0
    areas = []
    for block in model.blocks:
        points = np.asarray(
            [
                (model.vertices[identifier].x, model.vertices[identifier].y)
                for identifier in block.vertices
            ]
        )
        following = np.roll(points, -1, axis=0)
        after = np.roll(points, -2, axis=0)
        incoming = following - points
        outgoing = after - following
        crosses = (
            incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
        )
        sides = np.linalg.norm(incoming, axis=1)
        pairs = sides * np.roll(sides, -1)
        worst_scaled = min(worst_scaled, float(np.min(crosses / pairs)))
        angles = np.abs(np.arctan2(crosses, np.einsum("ij,ij->i", incoming, outgoing)))
        interior = math.pi - angles
        minimum_angle = min(minimum_angle, float(np.min(interior)))
        maximum_angle = max(maximum_angle, float(np.max(interior)))
        worst_aspect = max(worst_aspect, float(np.max(sides) / np.min(sides)))
        x = points[:, 0]
        y = points[:, 1]
        areas.append(
            0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        )
    return {
        "block_count": len(model.blocks),
        "minimum_corner_angle_degrees": math.degrees(minimum_angle),
        "maximum_corner_angle_degrees": math.degrees(maximum_angle),
        "minimum_scaled_jacobian": worst_scaled,
        "maximum_aspect_ratio": worst_aspect,
        "minimum_block_area": min(areas) if areas else 0.0,
        "maximum_block_area": max(areas) if areas else 0.0,
        "area_ratio": (max(areas) / min(areas)) if areas and min(areas) > 0 else None,
    }


# ---------------------------------------------------------------------------
# Emission from the general patch graph
# ---------------------------------------------------------------------------


def build_model_from_graph(
    graph,
    domain,
    *,
    counts,
    grading=(),
    reference_curves: bool = True,
    default_cells: int = 10,
) -> tuple[MeshModel, dict, list[dict]]:
    """Turn a validated patch graph into an ordinary BlockDrawer session.

    Returns the model, the vertex-key to session-id map, and a list of sizing
    requests that BlockDrawer refused, so an unattainable first-cell width is
    reported rather than silently dropped.
    """
    model = MeshModel(initialize=False)
    identifiers: dict[tuple, str] = {}
    for index, key in enumerate(graph.vertices):
        vertex = graph.vertices[key]
        identifier = f"v{index}"
        identifiers[key] = identifier
        model.vertices[identifier] = Vertex(
            identifier, float(vertex.point[0]), float(vertex.point[1])
        )
    for index, face in enumerate(graph.faces):
        model.blocks.append(
            DomainBlock(f"b{index}", tuple(identifiers[key] for key in face.corners))
        )
    actual = set(model.edges())
    model.edge_cells = {}
    for key in actual:
        model.edge_cells[key] = int(default_cells)
    for key, edge in graph.edges.items():
        current = edge_key(identifiers[key[0]], identifiers[key[1]])
        if current not in actual:
            continue
        cells = counts.get(key)
        if cells is not None:
            model.edge_cells[current] = int(cells)
        if edge.kind == "line" or not edge.points:
            continue
        start = (model.vertices[current[0]].x, model.vertices[current[0]].y)
        end = (model.vertices[current[1]].x, model.vertices[current[1]].y)
        points = edge.points
        if current != (identifiers[key[0]], identifiers[key[1]]):
            points = tuple(reversed(points))
        if edge.kind == "arc":
            model.edge_geometry[current] = EdgeGeometry("arc", points)
            continue
        thinned = _thin(points, start, end)
        if not thinned:
            continue
        model.edge_geometry[current] = EdgeGeometry(edge.kind, thinned)

    used: set[str] = {model.z_min_patch_name, model.z_max_patch_name}
    patches: dict[str, str] = {}
    chains = {chain.name: chain for chain in domain.chains()}
    for name in sorted(chains):
        patches[name] = _patch_name(name, used)
    assigned: set[str] = set()
    for key, edge in graph.edges.items():
        if edge.boundary is None:
            continue
        current = edge_key(identifiers[key[0]], identifiers[key[1]])
        if model.is_boundary_edge(current):
            assigned.add(edge.boundary)
    for name in sorted(assigned):
        model.add_boundary(patches[name])
    for name in sorted(assigned):
        chain = chains[name]
        if chain.role == "cyclic":
            continue
        if chain.patch_type != "patch":
            model.set_boundary_type(patches[name], chain.patch_type)
    for name in sorted(assigned):
        chain = chains[name]
        if chain.role != "cyclic" or chain.neighbour not in assigned:
            continue
        if model.boundaries[patches[name]].kind == "cyclic":
            continue
        model.set_boundary_type(
            patches[name], "cyclic", neighbour_patch=patches[chain.neighbour]
        )
    for key, edge in graph.edges.items():
        if edge.boundary is None:
            continue
        current = edge_key(identifiers[key[0]], identifiers[key[1]])
        if model.is_boundary_edge(current):
            model.set_edge_boundary(current, patches[edge.boundary])

    refused: list[dict] = []
    for request in grading:
        current = edge_key(
            identifiers[request.edge[0]], identifiers[request.edge[1]]
        )
        if current not in model.edge_cells:
            continue
        cells = model.edge_cells[current]
        length = model.edge_length(current)
        parameter = (
            "start_width"
            if current[0] == identifiers[request.wall_vertex]
            else "end_width"
        )
        if cells < 2 or request.first_width >= length:
            refused.append(
                {
                    "edge": list(current),
                    "requested_first_width": float(request.first_width),
                    "edge_length": float(length),
                    "cells": int(cells),
                    "reason": "the requested first cell does not fit in this edge",
                }
            )
            continue
        try:
            model.set_edge_grading(current, parameter, float(request.first_width))
        except Exception as error:  # pragma: no cover - reported, not raised
            refused.append(
                {
                    "edge": list(current),
                    "requested_first_width": float(request.first_width),
                    "edge_length": float(length),
                    "cells": int(cells),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )

    if reference_curves:
        for chain in domain.wall_chains():
            points = chain.points
            if len(points) > 2 and math.dist(points[0], points[-1]) <= 0.0:
                points = points[:-1]
            model.add_geometry_curve(
                [(float(x), float(y)) for x, y in points],
                name=f"{chain.name}_points",
                show_points=False,
            )
    model.validate()
    return model, identifiers, refused
