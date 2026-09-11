"""Structured and textual descriptions of a topology for scripts and agents.

The JSON form is stable, complete, and diffable; the text form is a compact
listing that reads well in a terminal. Both refer to entities by the same IDs
the command registry accepts, so a reader can go straight from a description
to an edit.
"""

from __future__ import annotations

from typing import Any

from .commands import edge_text, export_settings_data
from .domain import Block, EdgeKey, edge_key
from .model import MeshModel


MAX_LISTED_CONTROL_POINTS = 8
SECTIONS = (
    "summary",
    "settings",
    "blocks",
    "edges",
    "vertices",
    "boundaries",
    "curves",
    "spacing_links",
)


def describe_model(model: MeshModel) -> dict[str, Any]:
    """Return a complete JSON-compatible description of ``model``."""
    model.validate()
    occurrences = model.edge_occurrences()
    edges = model.edges()
    block_edges = _blocks_by_vertex(model)

    vertices = [
        {
            "id": vertex.id,
            "x": vertex.x,
            "y": vertex.y,
            "blocks": block_edges.get(vertex.id, []),
        }
        for vertex in model.vertices.values()
    ]

    blocks = [_block_entry(model, block) for block in model.blocks]
    edge_entries = [
        _edge_entry(model, current, occurrences[current]) for current in edges
    ]
    boundaries = [
        {
            "name": boundary.name,
            "type": boundary.kind,
            "neighbour_patch": boundary.neighbour_patch,
            "color": boundary.color,
            "edges": [
                edge_text(current) for current in model.boundary_edges(boundary.name)
            ],
        }
        for boundary in model.boundaries.values()
    ]
    curves = [
        {
            "id": curve.id,
            "name": curve.name,
            "point_count": len(curve.points),
            "show_points": curve.show_points,
            "bounds": _bounds(curve.points),
            "first_point": list(curve.points[0]),
            "last_point": list(curve.points[-1]),
        }
        for curve in model.geometry_curves.values()
    ]
    spacing_links = [
        {
            "vertex": link.vertex,
            "edges": [edge_text(link.first_edge), edge_text(link.second_edge)],
            "synchronized": model.spacing_link_is_synchronized(link),
            "widths": [
                model.edge_width_at_vertex(link.first_edge, link.vertex),
                model.edge_width_at_vertex(link.second_edge, link.vertex),
            ],
        }
        for link in sorted(model.spacing_links)
    ]

    used_vertices = {
        identifier for block in model.blocks for identifier in block.vertices
    }
    exterior = [current for current in edges if len(occurrences[current]) == 1]
    corner_points = [
        (model.vertices[identifier].x, model.vertices[identifier].y)
        for identifier in used_vertices
    ]
    summary = {
        "blocks": len(model.blocks),
        "vertices": len(model.vertices),
        "standalone_vertices": len(model.vertices) - len(used_vertices),
        "edges": len(edges),
        "exterior_edges": len(exterior),
        "internal_edges": len(edges) - len(exterior),
        "unassigned_exterior_edges": sum(
            1 for current in exterior if current not in model.edge_boundaries
        ),
        "curved_edges": len(model.edge_geometry),
        "graded_edges": len(model.edge_grading),
        "total_cells": sum(
            nx * ny * nz
            for nx, ny, nz in (model.block_cell_counts(block) for block in model.blocks)
        ),
        "bounds": _bounds(corner_points),
        "boundaries": len(model.boundaries),
        "curves": len(model.geometry_curves),
        "spacing_links": len(model.spacing_links),
        "unsynchronized_spacing_links": sum(
            1 for entry in spacing_links if not entry["synchronized"]
        ),
    }
    return {
        "summary": summary,
        "settings": export_settings_data(model),
        "blocks": blocks,
        "edges": edge_entries,
        "vertices": vertices,
        "boundaries": boundaries,
        "curves": curves,
        "spacing_links": spacing_links,
    }


def format_description(
    data: dict[str, Any], *, sections: tuple[str, ...] | None = None
) -> str:
    """Render a description dictionary as compact terminal text."""
    wanted = SECTIONS if sections is None else sections
    lines: list[str] = []
    if "summary" in wanted:
        lines.extend(_format_summary(data["summary"], data["settings"]))
    if "settings" in wanted and "summary" not in wanted:
        lines.extend(_format_settings(data["settings"]))
    if "blocks" in wanted:
        lines.append("")
        lines.append("Blocks (id: vertices ccw | cells nx ny nz | x edges; y edges):")
        for block in data["blocks"]:
            nx, ny, nz = block["cells"]
            lines.append(
                f"  {block['id']}: {' '.join(block['vertices'])} | "
                f"{nx} {ny} {nz} | x: {', '.join(block['x_edges'])}; "
                f"y: {', '.join(block['y_edges'])}"
            )
    if "edges" in wanted:
        lines.append("")
        lines.append(
            "Edges (id | type | cells | length | grading | kind | blocks | patch):"
        )
        for edge in data["edges"]:
            lines.append("  " + _format_edge_line(edge))
    if "vertices" in wanted:
        lines.append("")
        lines.append("Vertices (id (x, y) blocks):")
        for vertex in data["vertices"]:
            blocks = ", ".join(vertex["blocks"]) if vertex["blocks"] else "standalone"
            lines.append(
                f"  {vertex['id']} ({_num(vertex['x'])}, {_num(vertex['y'])}) {blocks}"
            )
    if "boundaries" in wanted:
        lines.append("")
        if data["boundaries"]:
            lines.append("Boundaries (name type [-> neighbour]: edges):")
            for boundary in data["boundaries"]:
                neighbour = (
                    f" -> {boundary['neighbour_patch']}"
                    if boundary["neighbour_patch"] else ""
                )
                edges = ", ".join(boundary["edges"]) or "no edges"
                lines.append(
                    f"  {boundary['name']} {boundary['type']}{neighbour}: {edges}"
                )
        else:
            lines.append("Boundaries: none defined")
        settings = data["settings"]
        lines.append(
            f"  extrusion patches: {settings['z_min_patch']['name']} "
            f"({settings['z_min_patch']['type']}), "
            f"{settings['z_max_patch']['name']} ({settings['z_max_patch']['type']})"
        )
    if "curves" in wanted:
        lines.append("")
        if data["curves"]:
            lines.append("Reference curves (id name: points, bounds):")
            for curve in data["curves"]:
                lines.append(
                    f"  {curve['id']} {curve['name']!r}: {curve['point_count']} points, "
                    f"{_format_bounds(curve['bounds'])}"
                )
        else:
            lines.append("Reference curves: none")
    if "spacing_links" in wanted:
        lines.append("")
        if data["spacing_links"]:
            lines.append("Spacing links (vertex: edge <-> edge status):")
            for link in data["spacing_links"]:
                status = "synchronized" if link["synchronized"] else (
                    "OUT OF SYNC "
                    f"({_num(link['widths'][0])} vs {_num(link['widths'][1])})"
                )
                lines.append(
                    f"  {link['vertex']}: {link['edges'][0]} <-> {link['edges'][1]} "
                    f"{status}"
                )
        else:
            lines.append("Spacing links: none")
    return "\n".join(lines).strip("\n") + "\n"


def _blocks_by_vertex(model: MeshModel) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for block in model.blocks:
        for identifier in block.vertices:
            result.setdefault(identifier, []).append(block.id)
    return result


def _block_entry(model: MeshModel, block: Block) -> dict[str, Any]:
    nx, ny, nz = model.block_cell_counts(block)
    edges = [edge_key(*block.directed_edge(index)) for index in range(4)]
    corners = [
        (model.vertices[identifier].x, model.vertices[identifier].y)
        for identifier in block.vertices
    ]
    return {
        "id": block.id,
        "vertices": list(block.vertices),
        "cells": [nx, ny, nz],
        "edges": [edge_text(current) for current in edges],
        "x_edges": [edge_text(edges[0]), edge_text(edges[2])],
        "y_edges": [edge_text(edges[1]), edge_text(edges[3])],
        "centroid": [
            sum(point[0] for point in corners) / 4.0,
            sum(point[1] for point in corners) / 4.0,
        ],
    }


def _edge_entry(
    model: MeshModel, current: EdgeKey, occurrences: list
) -> dict[str, Any]:
    values = model.edge_grading_values(current)
    control_points = model.edge_control_points(current)
    entry: dict[str, Any] = {
        "id": edge_text(current),
        "vertices": list(current),
        "type": model.edge_type(current),
        "cells": model.edge_cells[current],
        "length": values.length,
        "kind": "exterior" if len(occurrences) == 1 else "internal",
        "blocks": [block.id for block, _, _ in occurrences],
        "boundary": model.edge_boundaries.get(current),
        "grading": {
            "direction": f"{current[0]} -> {current[1]}",
            "total_ratio": values.total_ratio,
            "cell_ratio": values.cell_ratio,
            "start_width": values.start_width,
            "end_width": values.end_width,
        },
        "control_point_count": len(control_points),
    }
    if control_points and len(control_points) <= MAX_LISTED_CONTROL_POINTS:
        entry["control_points"] = [list(point) for point in control_points]
    links = model.spacing_links_for_edge(current)
    if links:
        entry["spacing_links"] = [
            {
                "vertex": link.vertex,
                "other_edge": edge_text(
                    link.second_edge if link.first_edge == current else link.first_edge
                ),
                "synchronized": model.spacing_link_is_synchronized(link),
            }
            for link in links
        ]
    return entry


def _bounds(points) -> dict[str, float] | None:
    points = list(points)
    if not points:
        return None
    return {
        "x_min": min(point[0] for point in points),
        "x_max": max(point[0] for point in points),
        "y_min": min(point[1] for point in points),
        "y_max": max(point[1] for point in points),
    }


def _format_bounds(bounds: dict[str, float] | None) -> str:
    if bounds is None:
        return "no extent"
    return (
        f"x {_num(bounds['x_min'])} .. {_num(bounds['x_max'])}, "
        f"y {_num(bounds['y_min'])} .. {_num(bounds['y_max'])}"
    )


def _format_summary(summary: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    lines = [
        f"Topology: {summary['blocks']} block(s), {summary['vertices']} vertices "
        f"({summary['standalone_vertices']} standalone), {summary['edges']} edges "
        f"({summary['exterior_edges']} exterior, {summary['internal_edges']} internal, "
        f"{summary['curved_edges']} curved, {summary['graded_edges']} graded), "
        f"{summary['total_cells']} cells",
        f"Bounds: {_format_bounds(summary['bounds'])}",
    ]
    lines.extend(_format_settings(settings))
    extras = []
    if summary["unassigned_exterior_edges"]:
        extras.append(
            f"{summary['unassigned_exterior_edges']} exterior edge(s) without a "
            "patch (blockMesh puts them in defaultFaces)"
        )
    if summary["unsynchronized_spacing_links"]:
        extras.append(
            f"{summary['unsynchronized_spacing_links']} spacing link(s) out of sync"
        )
    if extras:
        lines.append("Notes: " + "; ".join(extras))
    return lines


def _format_settings(settings: dict[str, Any]) -> list[str]:
    return [
        f"Extrusion: z {_num(settings['z_min'])} .. {_num(settings['z_max'])} with "
        f"{settings['z_cells']} cell(s); scale {_num(settings['scale'])}; patches "
        f"{settings['z_min_patch']['name']} ({settings['z_min_patch']['type']}), "
        f"{settings['z_max_patch']['name']} ({settings['z_max_patch']['type']})"
    ]


def _format_edge_line(edge: dict[str, Any]) -> str:
    grading = edge["grading"]
    if grading["total_ratio"] == 1.0:
        grading_text = f"uniform ({_num(grading['start_width'])})"
    else:
        grading_text = (
            f"ratio {_num(grading['total_ratio'])} "
            f"({_num(grading['start_width'])} -> {_num(grading['end_width'])})"
        )
    type_text = edge["type"]
    if edge["control_point_count"]:
        type_text += f"[{edge['control_point_count']}]"
    parts = [
        edge["id"],
        type_text,
        str(edge["cells"]),
        _num(edge["length"]),
        grading_text,
        edge["kind"],
        ",".join(edge["blocks"]),
        edge["boundary"] or "-",
    ]
    line = " | ".join(parts)
    links = edge.get("spacing_links")
    if links:
        link_text = ", ".join(
            f"{link['vertex']}->{link['other_edge']}"
            f"{'' if link['synchronized'] else ' (out of sync)'}"
            for link in links
        )
        line += f" | links: {link_text}"
    return line


def _num(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return str(value)
    text = format(value, ".6g")
    return text
