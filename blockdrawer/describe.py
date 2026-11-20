"""Structured and textual descriptions of a topology for scripts and agents.

The JSON form is stable, complete, and diffable; the text form is a compact
listing that reads well in a terminal. Both refer to entities by the same IDs
the command registry accepts, so a reader can go straight from a description
to an edit.

A large topology is read in pieces: :func:`query_description` keeps only the
entities inside a box, the opposite-edge count component of an edge, or a
list of IDs, and reports the exterior edges that still have no patch as
chains, so an agent asks for the region it is working on instead of the
whole session.
"""

from __future__ import annotations

from typing import Any, Sequence

from .commands import CommandError, edge_text, export_settings_data, parse_edge
from .domain import Block, EdgeKey, TopologyError, edge_key
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
# Sections a query adds; they are listed only when asked for.
QUERY_SECTIONS = ("component", "unassigned_chains")
# Other names accepted for a section: the commands call boundaries patches,
# and the unassigned chains are asked for by --unassigned.
SECTION_ALIASES = {"patches": "boundaries", "unassigned": "unassigned_chains"}
# The query that produces each query section, for the refusal without it.
_QUERY_FLAGS = {"component": "component=EDGE (--component EDGE)",
                "unassigned_chains": "unassigned=true (--unassigned)"}


def resolve_sections(names, *, component: Any = None, unassigned: bool = False) -> tuple[str, ...] | None:
    """The sections ``names`` asks for, aliases resolved and repeats
    dropped; ``None`` for none. A query section (``component``,
    ``unassigned_chains``) is accepted with the query that produces it
    (``component``, ``unassigned``), and alone shows only that section. An
    unknown name, or a query section without its query, raises
    ``ValueError``."""
    if not names:
        return None
    resolved: list[str] = []
    unknown = []
    for name in names:
        section = SECTION_ALIASES.get(name, name)
        if section not in SECTIONS and section not in QUERY_SECTIONS:
            unknown.append(name)
        elif section not in resolved:
            resolved.append(section)
    if unknown:
        raise ValueError(
            f"Unknown section(s) {', '.join(unknown)}; choose from {', '.join((*SECTIONS, *QUERY_SECTIONS))} "
            f"({', '.join(f'{alias} = {name}' for alias, name in SECTION_ALIASES.items())})"
        )
    given = {"component": component is not None, "unassigned_chains": bool(unassigned)}
    missing = [section for section in resolved if section in QUERY_SECTIONS and not given[section]]
    if missing:
        raise ValueError(
            "; ".join(f"the {section} section comes from the query {_QUERY_FLAGS[section]}" for section in missing)
        )
    return tuple(resolved)


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
            if data.get("filtered") and edge.get("control_points"):
                # A narrowed listing shows the points it carries.
                lines.append("    points: " + " ".join(
                    f"({_num(x)}, {_num(y)})" for x, y in edge["control_points"]
                ))
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
                total = boundary.get("total_edges")
                if total is not None and total != len(boundary["edges"]):
                    edges += f" ({len(boundary['edges'])} of its {total} edges shown)"
                lines.append(
                    f"  {boundary['name']} {boundary['type']}{neighbour}: {edges}"
                )
        elif data.get("filtered"):
            lines.append("Boundaries: none on the listed edges")
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
    # Query sections are printed whenever a query asked for them.
    if "component" in data:
        component = data["component"]
        lines.append("")
        lines.append(
            f"Count component of {component['edge']}: {component['cells']} cells on "
            f"{len(component['edges'])} edge(s) across {len(component['blocks'])} block(s)"
        )
        lines.append(f"  edges: {', '.join(component['edges'])}")
        lines.append(f"  blocks: {', '.join(component['blocks'])}")
    if "unassigned_chains" in data:
        chains = data["unassigned_chains"]
        lines.append("")
        if chains:
            lines.append(
                f"Exterior edges without a patch: {sum(len(c['edges']) for c in chains)} "
                f"in {len(chains)} chain(s) (vertices in order | edges | length):"
            )
            for chain in chains:
                path = " ".join(chain["vertices"])
                closed = " (closed)" if chain["closed"] else ""
                lines.append(
                    f"  {path}{closed} | {len(chain['edges'])} | {_num(chain['length'])}"
                )
        else:
            lines.append("Exterior edges without a patch: none")
    return "\n".join(lines).strip("\n") + "\n"


def filter_description(
    data: dict[str, Any], wanted: set[str], model: MeshModel | None = None
) -> dict[str, Any]:
    """Keep only entities named in ``wanted`` plus what touches those blocks.

    A patch is kept when it is named or owns one of the kept edges; its edge
    list is then restricted to the kept edges and ``total_edges`` gives the
    patch's full count. With ``model`` an edge may be named in either vertex
    order, and every named edge lists all its control points, however many.
    """
    wanted = set(wanted)
    named_edges: set[str] = set()
    if model is not None:
        for item in list(wanted):
            if "-" not in item or item in model.vertices:
                continue
            try:
                current = parse_edge(model, item)
            except (CommandError, TopologyError):
                continue
            named_edges.add(edge_text(current))
        wanted |= named_edges
    filtered = dict(data)
    filtered["filtered"] = True
    filtered["blocks"] = [item for item in data["blocks"] if item["id"] in wanted]
    filtered["edges"] = [
        item for item in data["edges"]
        if item["id"] in wanted or any(block in wanted for block in item["blocks"])
    ]
    if model is not None and named_edges:
        expanded = []
        for item in filtered["edges"]:
            if item["id"] in named_edges and item["control_point_count"]:
                item = dict(item)
                current = parse_edge(model, item["id"])
                item["control_points"] = [
                    list(point) for point in model.edge_control_points(current)
                ]
            expanded.append(item)
        filtered["edges"] = expanded
    filtered["vertices"] = [
        item for item in data["vertices"]
        if item["id"] in wanted or any(block in wanted for block in item["blocks"])
    ]
    kept_edges = {item["id"] for item in filtered["edges"]}
    filtered["boundaries"] = []
    for item in data["boundaries"]:
        if item["name"] in wanted:
            filtered["boundaries"].append(item)
            continue
        touching = [edge for edge in item["edges"] if edge in kept_edges]
        if touching:
            filtered["boundaries"].append(
                {**item, "edges": touching, "total_edges": len(item["edges"])}
            )
    filtered["curves"] = [
        item for item in data["curves"]
        if item["id"] in wanted or item["name"] in wanted
    ]
    filtered["spacing_links"] = [
        item for item in data["spacing_links"]
        if item["vertex"] in wanted or any(edge in wanted for edge in item["edges"])
    ]
    return filtered


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


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def blocks_in_box(
    model: MeshModel, box: Sequence[float]
) -> tuple[list[str], list[str]]:
    """Blocks whose extent overlaps ``box`` and vertices inside it.

    A block's extent is the box round its corners and its edges' sampled
    paths, so a block whose curved edge bulges into the box is found.
    ``box`` is ``[xmin, ymin, xmax, ymax]``.
    """
    if len(box) != 4:
        raise ValueError("a box needs four numbers: xmin, ymin, xmax, ymax")
    xmin, ymin, xmax, ymax = (float(value) for value in box)
    if xmin > xmax or ymin > ymax:
        raise ValueError("a box needs xmin <= xmax and ymin <= ymax")
    vertices = [
        vertex.id for vertex in model.vertices.values()
        if xmin <= vertex.x <= xmax and ymin <= vertex.y <= ymax
    ]
    extents: dict[EdgeKey, tuple[float, float, float, float]] = {}
    blocks = []
    for block in model.blocks:
        low_x = low_y = float("inf")
        high_x = high_y = float("-inf")
        for index in range(4):
            current = edge_key(*block.directed_edge(index))
            extent = extents.get(current)
            if extent is None:
                points = [
                    (model.vertices[current[0]].x, model.vertices[current[0]].y),
                    (model.vertices[current[1]].x, model.vertices[current[1]].y),
                ]
                if current in model.edge_geometry:
                    points.extend(model.edge_points(
                        current, tuple(step / 16.0 for step in range(1, 16))
                    ))
                    points.extend(model.edge_control_points(current))
                extent = (
                    min(point[0] for point in points), min(point[1] for point in points),
                    max(point[0] for point in points), max(point[1] for point in points),
                )
                extents[current] = extent
            low_x = min(low_x, extent[0])
            low_y = min(low_y, extent[1])
            high_x = max(high_x, extent[2])
            high_y = max(high_y, extent[3])
        if low_x <= xmax and high_x >= xmin and low_y <= ymax and high_y >= ymin:
            blocks.append(block.id)
    return blocks, vertices


def count_component(model: MeshModel, edge: Any) -> dict[str, Any]:
    """The edges whose cell count must equal ``edge``'s, and the blocks they span."""
    current = parse_edge(model, edge)
    component = model.edge_constraint_component(current)
    occurrences = model.edge_occurrences()
    blocks: list[str] = []
    for member in component:
        for block, _index, _directed in occurrences.get(member, ()):
            blocks.append(block.id)
    order = {block.id: index for index, block in enumerate(model.blocks)}
    return {
        "edge": edge_text(current),
        "cells": model.edge_cells[current],
        "edges": sorted(edge_text(member) for member in component),
        "blocks": sorted(set(blocks), key=lambda identifier: order[identifier]),
    }


def unassigned_chains(model: MeshModel) -> list[dict[str, Any]]:
    """Exterior edges without a patch, joined into vertex chains.

    Each chain is a maximal path through vertices where exactly two such
    edges meet; ``closed`` chains are loops. Chains are ordered by their
    first edge in the model's edge order.
    """
    occurrences = model.edge_occurrences()
    loose = [
        current for current in model.edges()
        if len(occurrences[current]) == 1 and current not in model.edge_boundaries
    ]
    neighbours: dict[str, list[EdgeKey]] = {}
    for current in loose:
        for vertex_id in current:
            neighbours.setdefault(vertex_id, []).append(current)
    used: set[EdgeKey] = set()
    chains: list[dict[str, Any]] = []

    def walk(start: str, first: EdgeKey) -> None:
        path = [start]
        edges: list[EdgeKey] = []
        here: str = start
        current: EdgeKey | None = first
        while current is not None:
            used.add(current)
            edges.append(current)
            here = current[1] if current[0] == here else current[0]
            path.append(here)
            touching = neighbours[here]
            current = None
            if len(touching) == 2:
                current = next((item for item in touching if item not in used), None)
        chains.append({
            "vertices": path,
            "edges": [edge_text(item) for item in edges],
            "closed": len(edges) > 1 and path[0] == path[-1],
            "length": sum(model.edge_length(item) for item in edges),
        })

    # Open chains run between vertices where other than two such edges meet;
    # whatever is left over forms closed loops.
    for current in loose:
        for vertex_id in current:
            if len(neighbours[vertex_id]) == 2:
                continue
            for item in neighbours[vertex_id]:
                if item not in used:
                    walk(vertex_id, item)
    for current in loose:
        if current not in used:
            walk(current[0], current)
    return chains


def resolve_only(model: MeshModel, only: Sequence[str]) -> list[str]:
    """The IDs ``only`` names, comma-separated lists split, each checked.

    An item that names an entity as it stands is kept whole (an ID may
    contain a comma); otherwise it is split at commas. An ID that names no
    block, vertex, edge (either vertex order), patch or curve (ID or name)
    is an error naming it, rather than an empty section.
    """
    blocks = {block.id for block in model.blocks}
    names = (
        blocks | set(model.vertices) | set(model.boundaries)
        | {model.z_min_patch_name, model.z_max_patch_name}
        | set(model.geometry_curves)
        | {curve.name for curve in model.geometry_curves.values()}
    )

    def known(item: str) -> bool:
        if item in names:
            return True
        if "-" not in item:
            return False
        try:
            parse_edge(model, item)
        except (CommandError, TopologyError):
            return False
        return True

    resolved: list[str] = []
    unknown: list[str] = []
    for item in only:
        parts = [item] if known(item) or "," not in item else [
            part.strip() for part in item.split(",") if part.strip()
        ]
        for part in parts:
            if known(part):
                resolved.append(part)
            else:
                unknown.append(part)
    if unknown:
        raise TopologyError(
            "only: no block, vertex, edge, patch or curve named "
            + ", ".join(repr(item) for item in unknown)
        )
    return resolved


def query_description(
    model: MeshModel,
    data: dict[str, Any] | None = None,
    *,
    only: Sequence[str] | None = None,
    bbox: Sequence[float] | None = None,
    component: Any = None,
    unassigned: bool = False,
) -> dict[str, Any]:
    """Describe ``model`` restricted to the union of the given selections.

    ``only`` names blocks, edges, vertices, patches or curves; ``bbox``
    selects the blocks overlapping ``[xmin, ymin, xmax, ymax]`` and the
    vertices inside it; ``component`` selects the opposite-edge count
    component of one edge (and adds a ``component`` section); ``unassigned``
    adds the ``unassigned_chains`` section and, when it is the only query,
    lists the edges of those chains. Without any selection the whole
    description is returned.
    """
    if data is None:
        data = describe_model(model)
    only = resolve_only(model, only) if only else only
    wanted: set[str] = set(only or ())
    selected = bool(only) or bbox is not None or component is not None
    if bbox is not None:
        blocks, vertices = blocks_in_box(model, bbox)
        wanted.update(blocks)
        wanted.update(vertices)
    extra: dict[str, Any] = {}
    if component is not None:
        extra["component"] = count_component(model, component)
        wanted.update(extra["component"]["edges"])
        wanted.update(extra["component"]["blocks"])
    if unassigned:
        extra["unassigned_chains"] = unassigned_chains(model)
        if not selected:
            wanted.update(
                edge for chain in extra["unassigned_chains"] for edge in chain["edges"]
            )
            selected = True
    result = filter_description(data, wanted, model) if selected else dict(data)
    if selected and component is not None:
        # The component is about edges: list its edges, not every edge of
        # every block it crosses.
        members = set(extra["component"]["edges"])
        named = set(only or ())
        result["edges"] = [
            item for item in result["edges"]
            if item["id"] in members or item["id"] in named
            or bbox is not None and any(block in wanted for block in item["blocks"])
        ]
    result.update(extra)
    return result
