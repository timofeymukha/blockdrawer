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

from block_complex import Complex

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


def build_model(
    complex_: Complex,
    diagram,
    *,
    cells: int = 10,
    reference_curves: bool = True,
) -> MeshModel:
    model = MeshModel(initialize=False)
    identifiers: dict[tuple, str] = {}
    for index, key in enumerate(complex_.vertices):
        vertex = complex_.vertices[key]
        identifier = f"v{index}"
        identifiers[key] = identifier
        model.vertices[identifier] = Vertex(
            identifier, float(vertex.point[0]), float(vertex.point[1])
        )
    for index, block in enumerate(complex_.blocks):
        model.blocks.append(
            DomainBlock(
                f"b{index}", tuple(identifiers[key] for key in block.corners)
            )
        )
    model.edge_cells = {current: cells for current in model.edges()}
    for key, edge in complex_.edges.items():
        first = identifiers[key[0]]
        second = identifiers[key[1]]
        current = edge_key(first, second)
        if current not in model.edge_cells:
            continue
        if edge.curve.kind == "line" or not edge.curve.points:
            continue
        start = (model.vertices[current[0]].x, model.vertices[current[0]].y)
        end = (model.vertices[current[1]].x, model.vertices[current[1]].y)
        points = edge.curve.points
        if current != (first, second):
            points = tuple(reversed(points))
        if edge.curve.kind == "arc":
            model.edge_geometry[current] = EdgeGeometry("arc", points)
            continue
        thinned = _thin(points, start, end)
        if not thinned:
            continue
        model.edge_geometry[current] = EdgeGeometry(edge.curve.kind, thinned)

    used: set[str] = {model.z_min_patch_name, model.z_max_patch_name}
    patches: dict[str, str] = {}
    for site in diagram.sites:
        patches[site.name] = _patch_name(site.name, used)
    for site in diagram.sites:
        model.add_boundary(patches[site.name])
        if site.curve.kind == "wall":
            model.set_boundary_type(patches[site.name], "wall")
    for key, edge in complex_.edges.items():
        if edge.boundary is None:
            continue
        current = edge_key(identifiers[key[0]], identifiers[key[1]])
        if model.is_boundary_edge(current):
            model.set_edge_boundary(current, patches[edge.boundary])
    if reference_curves:
        for site in diagram.sites:
            if site.curve.kind != "wall":
                continue
            points = site.curve.loop()[:-1]
            model.add_geometry_curve(
                [(float(x), float(y)) for x, y in points],
                name=f"{site.name}_points",
                show_points=False,
            )
    model.validate()
    return model


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
