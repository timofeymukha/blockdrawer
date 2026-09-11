"""Mirror one half of a BlockDrawer session onto the other half."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Sequence

from .domain import (
    Block,
    EdgeGeometry,
    EdgeKey,
    SpacingLink,
    TopologyError,
    Vertex,
)
from .model import MeshModel, edge_key
from .session import SessionError, load_session, save_session


AXES = ("x", "y")
SOURCE_SIDES = ("positive", "negative")


class SymmetryError(ValueError):
    """Raised when a session cannot be split and mirrored safely."""


@dataclass(frozen=True)
class SymmetryResult:
    """A symmetrized model and a concise operation summary."""

    model: MeshModel
    vertex_map: dict[str, str]
    source_block_count: int
    mirrored_block_count: int
    skipped_spacing_link_count: int


def symmetrize_model(
    model: MeshModel,
    *,
    axis: str,
    source_side: str = "positive",
    tolerance: float = MeshModel.COORDINATE_TOLERANCE,
) -> SymmetryResult:
    """Replace one side of ``axis`` with a reflection of ``source_side``.

    ``axis="x"`` reflects ``y`` coordinates and ``axis="y"`` reflects ``x``
    coordinates. Blocks must lie wholly on one side, although their boundary
    vertices may lie on the axis. Independent reference curves are retained
    unchanged.
    """
    if axis not in AXES:
        raise SymmetryError(f"Axis must be one of {AXES}, got {axis!r}")
    if source_side not in SOURCE_SIDES:
        raise SymmetryError(
            f"Source side must be one of {SOURCE_SIDES}, got {source_side!r}"
        )
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise SymmetryError("Tolerance must be positive and finite")

    try:
        model.validate()
    except TopologyError as exc:
        raise SymmetryError(f"Input session is invalid: {exc}") from exc

    source_sign = 1 if source_side == "positive" else -1
    vertex_sides = {
        identifier: _coordinate_side(vertex, axis, tolerance)
        for identifier, vertex in model.vertices.items()
    }
    source_blocks: list[Block] = []
    crossing_blocks: list[str] = []
    for block in model.blocks:
        off_axis_sides = {
            vertex_sides[identifier]
            for identifier in block.vertices
            if vertex_sides[identifier] != 0
        }
        if len(off_axis_sides) > 1:
            crossing_blocks.append(block.id)
        elif off_axis_sides == {source_sign}:
            source_blocks.append(block)

    if crossing_blocks:
        joined = ", ".join(repr(value) for value in crossing_blocks)
        raise SymmetryError(
            f"Block(s) {joined} straddle the {axis}-axis. Split the topology "
            "along the symmetry axis before using this utility."
        )
    if not source_blocks:
        coordinate = "y" if axis == "x" else "x"
        operator = ">" if source_sign > 0 else "<"
        raise SymmetryError(
            f"No blocks were found on the source half ({coordinate} {operator} 0)."
        )

    result = MeshModel(initialize=False)
    _copy_global_state(model, result)

    all_block_vertices = {
        identifier
        for block in model.blocks
        for identifier in block.vertices
    }
    source_block_vertices = {
        identifier
        for block in source_blocks
        for identifier in block.vertices
    }
    standalone_vertices = set(model.vertices) - all_block_vertices
    retained_vertices = {
        identifier
        for identifier, side in vertex_sides.items()
        if side == source_sign
    }
    retained_vertices.update(source_block_vertices)
    retained_vertices.update(
        identifier
        for identifier in standalone_vertices
        if vertex_sides[identifier] == 0
    )

    for identifier, vertex in model.vertices.items():
        if identifier not in retained_vertices:
            continue
        x, y = _snapped_point((vertex.x, vertex.y), axis, tolerance)
        result.vertices[identifier] = Vertex(identifier, x, y)

    vertex_map: dict[str, str] = {}
    used_vertex_ids = set(result.vertices)
    for identifier, vertex in tuple(result.vertices.items()):
        if _coordinate_side(vertex, axis, tolerance) == 0:
            vertex_map[identifier] = identifier
            continue
        mirrored_id = _unique_id(f"mirror_{identifier}", used_vertex_ids)
        used_vertex_ids.add(mirrored_id)
        mirrored_x, mirrored_y = _reflect_point((vertex.x, vertex.y), axis)
        result.vertices[mirrored_id] = Vertex(
            mirrored_id, mirrored_x, mirrored_y
        )
        vertex_map[identifier] = mirrored_id

    result.blocks = [
        Block(block.id, block.vertices)
        for block in source_blocks
    ]
    used_block_ids = {block.id for block in result.blocks}
    for block in source_blocks:
        mirrored_id = _unique_id(f"mirror_{block.id}", used_block_ids)
        used_block_ids.add(mirrored_id)
        mapped = tuple(vertex_map[value] for value in block.vertices)
        mirrored_vertices = (mapped[0], mapped[3], mapped[2], mapped[1])
        result.blocks.append(Block(mirrored_id, mirrored_vertices))

    source_edges = _ordered_edges(source_blocks)
    source_edge_set = set(source_edges)
    mirrored_edges: dict[EdgeKey, EdgeKey] = {}
    for current in source_edges:
        mapped_direction = (
            vertex_map[current[0]],
            vertex_map[current[1]],
        )
        mirrored = edge_key(*mapped_direction)
        mirrored_edges[current] = mirrored
        cells = model.edge_cells[current]
        _set_matching_value(result.edge_cells, current, cells, "cell count")
        _set_matching_value(result.edge_cells, mirrored, cells, "cell count")

        geometry = model.edge_geometry.get(current)
        if geometry is not None:
            mirrored_geometry = _mirrored_geometry(
                geometry,
                axis,
                mapped_direction,
                mirrored,
            )
            if mirrored == current:
                if geometry.kind == "arc" or not _geometry_matches(
                    geometry, mirrored_geometry, tolerance
                ):
                    raise SymmetryError(
                        f"Edge {current!r} lies on the {axis}-axis but its "
                        "interpolation points are not symmetric about that axis."
                    )
                axis_geometry = EdgeGeometry(
                    geometry.kind,
                    tuple(
                        _snapped_point(point, axis, tolerance)
                        for point in geometry.points
                    ),
                )
                _set_matching_value(
                    result.edge_geometry,
                    current,
                    axis_geometry,
                    "edge geometry",
                )
            else:
                _set_matching_value(
                    result.edge_geometry,
                    current,
                    geometry,
                    "edge geometry",
                )
                _set_matching_value(
                    result.edge_geometry,
                    mirrored,
                    mirrored_geometry,
                    "edge geometry",
                )

        total_ratio = model.edge_grading.get(current)
        if total_ratio is not None:
            _set_matching_value(
                result.edge_grading, current, total_ratio, "edge grading"
            )
            mirrored_ratio = (
                total_ratio
                if mapped_direction == mirrored
                else 1.0 / total_ratio
            )
            _set_matching_value(
                result.edge_grading,
                mirrored,
                mirrored_ratio,
                "edge grading",
            )

    occurrences = result.edge_occurrences()
    for current in source_edges:
        boundary_name = model.edge_boundaries.get(current)
        if boundary_name is None:
            continue
        for candidate in (current, mirrored_edges[current]):
            if len(occurrences.get(candidate, ())) == 1:
                result.edge_boundaries[candidate] = boundary_name

    skipped_links = _copy_spacing_links(
        model,
        result,
        source_edge_set,
        mirrored_edges,
        vertex_map,
    )

    try:
        result.validate()
    except TopologyError as exc:
        raise SymmetryError(
            f"The mirrored session would be invalid: {exc}"
        ) from exc
    return SymmetryResult(
        result,
        vertex_map,
        len(source_blocks),
        len(source_blocks),
        skipped_links,
    )


def default_output_path(source: str | Path) -> Path:
    """Return a non-destructive default output name beside ``source``."""
    path = Path(source)
    suffix = path.suffix or ".json"
    return path.with_name(f"{path.stem}-symmetric{suffix}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the session symmetrizer command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Copy one half of a BlockDrawer JSON session onto the other half. "
            "This operates on editor topology, not an OpenFOAM mesh."
        )
    )
    parser.add_argument(
        "session", type=Path, help="input BlockDrawer session JSON"
    )
    parser.add_argument(
        "--axis",
        choices=AXES,
        required=True,
        help=(
            "symmetry axis: x reflects y coordinates; "
            "y reflects x coordinates"
        ),
    )
    parser.add_argument(
        "--source-side",
        choices=SOURCE_SIDES,
        default="positive",
        help="half to preserve and copy (default: positive)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output JSON path (default: INPUT-symmetric.json)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=MeshModel.COORDINATE_TOLERANCE,
        help=(
            "distance treated as lying on the axis "
            f"(default: {MeshModel.COORDINATE_TOLERANCE:g})"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an existing output file",
    )
    args = parser.parse_args(argv)

    destination = (
        args.output
        if args.output is not None
        else default_output_path(args.session)
    )
    if destination.exists() and not args.overwrite:
        parser.error(
            f"output already exists: {destination}; use --overwrite to replace it"
        )

    try:
        model = load_session(args.session)
        result = symmetrize_model(
            model,
            axis=args.axis,
            source_side=args.source_side,
            tolerance=args.tolerance,
        )
        save_session(result.model, destination)
    except (OSError, SessionError, SymmetryError) as exc:
        parser.exit(1, f"error: {exc}\n")

    message = (
        f"Wrote {destination} with {result.source_block_count} source and "
        f"{result.mirrored_block_count} mirrored block"
        f"{'s' if result.mirrored_block_count != 1 else ''}."
    )
    if result.skipped_spacing_link_count:
        message += (
            f" Skipped {result.skipped_spacing_link_count} mirrored spacing "
            "link(s) that would reuse an occupied endpoint."
        )
    print(message)
    return 0


def _copy_global_state(source: MeshModel, target: MeshModel) -> None:
    target.boundaries = dict(source.boundaries)
    target.geometry_curves = dict(source.geometry_curves)
    target.z_cells = source.z_cells
    target.z_min = source.z_min
    target.z_max = source.z_max
    target.scale = source.scale
    target.z_min_patch_name = source.z_min_patch_name
    target.z_min_patch_type = source.z_min_patch_type
    target.z_max_patch_name = source.z_max_patch_name
    target.z_max_patch_type = source.z_max_patch_type


def _coordinate_side(
    vertex: Vertex, axis: str, tolerance: float
) -> int:
    coordinate = vertex.y if axis == "x" else vertex.x
    if coordinate > tolerance:
        return 1
    if coordinate < -tolerance:
        return -1
    return 0


def _reflect_point(
    point: tuple[float, float], axis: str
) -> tuple[float, float]:
    x, y = point
    return (x, -y) if axis == "x" else (-x, y)


def _snapped_point(
    point: tuple[float, float], axis: str, tolerance: float
) -> tuple[float, float]:
    x, y = point
    coordinate = y if axis == "x" else x
    if abs(coordinate) > tolerance:
        return point
    return (x, 0.0) if axis == "x" else (0.0, y)


def _unique_id(preferred: str, used: set[str]) -> str:
    if preferred not in used:
        return preferred
    index = 2
    while f"{preferred}_{index}" in used:
        index += 1
    return f"{preferred}_{index}"


def _ordered_edges(blocks: Iterable[Block]) -> list[EdgeKey]:
    result: list[EdgeKey] = []
    seen: set[EdgeKey] = set()
    for block in blocks:
        for index in range(4):
            current = edge_key(*block.directed_edge(index))
            if current not in seen:
                seen.add(current)
                result.append(current)
    return result


def _set_matching_value(
    values: dict,
    key,
    value,
    description: str,
) -> None:
    existing = values.get(key)
    if existing is not None and existing != value:
        raise SymmetryError(
            f"Mirroring produced conflicting {description} for {key!r}"
        )
    values[key] = value


def _mirrored_geometry(
    geometry: EdgeGeometry,
    axis: str,
    mapped_direction: tuple[str, str],
    mirrored_edge: EdgeKey,
) -> EdgeGeometry:
    points = tuple(_reflect_point(point, axis) for point in geometry.points)
    if mapped_direction != mirrored_edge:
        points = tuple(reversed(points))
    return EdgeGeometry(geometry.kind, points)


def _geometry_matches(
    first: EdgeGeometry,
    second: EdgeGeometry,
    tolerance: float,
) -> bool:
    return (
        first.kind == second.kind
        and len(first.points) == len(second.points)
        and all(
            math.isclose(
                first_point[0],
                second_point[0],
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            and math.isclose(
                first_point[1],
                second_point[1],
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for first_point, second_point in zip(first.points, second.points)
        )
    )


def _copy_spacing_links(
    source: MeshModel,
    target: MeshModel,
    source_edges: set[EdgeKey],
    mirrored_edges: dict[EdgeKey, EdgeKey],
    vertex_map: dict[str, str],
) -> int:
    occupied: set[tuple[EdgeKey, str]] = set()
    source_links = [
        link
        for link in sorted(source.spacing_links)
        if link.first_edge in source_edges
        and link.second_edge in source_edges
        and link.vertex in vertex_map
    ]
    for link in source_links:
        target.spacing_links.add(link)
        occupied.add((link.first_edge, link.vertex))
        occupied.add((link.second_edge, link.vertex))

    skipped = 0
    actual_edges = set(target.edge_cells)
    for link in source_links:
        vertex = vertex_map[link.vertex]
        first = mirrored_edges[link.first_edge]
        second = mirrored_edges[link.second_edge]
        if first == second:
            continue
        first, second = sorted((first, second))
        mirrored_link = SpacingLink(vertex, first, second)
        if mirrored_link in target.spacing_links:
            continue
        endpoints = {(first, vertex), (second, vertex)}
        if (
            first not in actual_edges
            or second not in actual_edges
            or set(first) & set(second) != {vertex}
            or endpoints & occupied
        ):
            skipped += 1
            continue
        target.spacing_links.add(mirrored_link)
        occupied.update(endpoints)
    return skipped


if __name__ == "__main__":
    raise SystemExit(main())
