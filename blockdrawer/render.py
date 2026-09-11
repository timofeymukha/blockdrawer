"""Headless rendering of a topology to SVG or PNG.

The picture is meant for inspection by a person or an agent who will then
refer back to entities by ID, so vertex IDs, block IDs, edge cell counts, and
boundary colors are drawn by default. SVG output needs only the standard
library. PNG output uses Pillow when it is installed; it is an optional
dependency because the editor itself does not need it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import html
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from .commands import CommandError, edge_text, parse_edge
from .domain import EdgeKey, edge_key
from .model import MeshModel
from .preview import build_mesh_preview
from .ui_helpers import (
    CURVE_RENDER_SEGMENTS,
    GEOMETRY_SAMPLES_PER_SPAN,
    MAX_VISIBLE_CONTROL_POINTS,
    MAX_INDIVIDUAL_EDGE_NODE_MARKERS,
    SPLINE_SAMPLES_PER_SPAN,
    nice_grid_step,
)


Point = tuple[float, float]
Bounds = tuple[float, float, float, float]

BACKGROUND = "#ffffff"
GRID_COLOR = "#e4e9ee"
GRID_LABEL_COLOR = "#8a97a4"
EXTERIOR_EDGE_COLOR = "#334e68"
INTERNAL_EDGE_COLOR = "#8a9bab"
HIGHLIGHT_COLOR = "#e8590c"
VERTEX_COLOR = "#1971c2"
VERTEX_LABEL_COLOR = "#243b53"
BLOCK_LABEL_COLOR = "#102a43"
CONTROL_POINT_COLOR = "#7048a8"
CURVE_COLOR = "#2b8a3e"
PREVIEW_COLOR = "#b8c4ce"
SPACING_LINK_COLOR = "#0b7285"
CELL_LABEL_COLOR = "#52606d"


class RenderError(RuntimeError):
    """Raised when a picture cannot be produced."""


@dataclass(frozen=True)
class RenderOptions:
    """Everything that controls one rendered picture."""

    width: int = 1200
    height: int = 900
    bounds: Bounds | None = None
    padding: float = 48.0
    show_grid: bool = True
    show_vertex_ids: bool = True
    show_block_ids: bool = True
    show_edge_cells: bool = True
    show_edge_nodes: bool = True
    show_control_points: bool = True
    show_curves: bool = True
    show_legend: bool = True
    show_spacing_links: bool = True
    show_preview: bool = False
    preview_coarsening: int = 1
    highlight_edges: tuple[EdgeKey, ...] = ()
    highlight_vertices: tuple[str, ...] = ()
    highlight_blocks: tuple[str, ...] = ()
    title: str | None = None
    font_size: float = 12.0
    supersample: int = 2


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def render_to_file(
    model: MeshModel, path: str | Path, options: RenderOptions | None = None
) -> Path:
    """Write SVG or PNG depending on the file suffix and return the path."""
    destination = Path(path)
    suffix = destination.suffix.lower()
    if suffix == ".svg":
        destination.write_text(render_svg(model, options), encoding="utf-8")
    elif suffix == ".png":
        render_png(model, destination, options)
    else:
        raise RenderError(
            f"Unsupported output format {suffix!r}; use .svg or .png"
        )
    return destination


def render_svg(model: MeshModel, options: RenderOptions | None = None) -> str:
    """Return the picture as an SVG document."""
    settings = options or RenderOptions()
    painter = _SvgPainter(settings.width, settings.height)
    _draw(model, settings, painter, scale=1.0)
    return painter.finish()


def render_png(
    model: MeshModel, path: str | Path, options: RenderOptions | None = None
) -> None:
    """Write the picture as a PNG using Pillow."""
    Path(path).write_bytes(render_png_bytes(model, options))


def render_png_bytes(
    model: MeshModel, options: RenderOptions | None = None
) -> bytes:
    """Return the picture as PNG bytes using Pillow."""
    import io

    settings = options or RenderOptions()
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RenderError(
            "PNG rendering needs Pillow (python -m pip install Pillow); "
            "render to .svg instead or install it"
        ) from exc
    scale = max(1, int(settings.supersample))
    painter = _PillowPainter(settings.width, settings.height, scale)
    _draw(model, settings, painter, scale=float(scale))
    buffer = io.BytesIO()
    painter.finish().save(buffer, format="PNG")
    return buffer.getvalue()


def content_bounds(
    model: MeshModel, *, include_curves: bool = True
) -> Bounds:
    """Return a world-space box around blocks, control points, and curves."""
    points: list[Point] = [
        (vertex.x, vertex.y) for vertex in model.vertices.values()
    ]
    for geometry in model.edge_geometry.values():
        points.extend(geometry.points)
    if include_curves:
        for curve in model.geometry_curves.values():
            points.extend(curve.points)
    return _expand_degenerate(_bounds_of(points))


def entity_bounds(
    model: MeshModel, entities: Iterable[str], *, margin: float = 0.3
) -> Bounds:
    """Return a box around named blocks, edges, or vertices plus a margin."""
    points: list[Point] = []
    for entity in entities:
        kind, key = resolve_entity(model, entity)
        if kind == "block":
            block = next(item for item in model.blocks if item.id == key)
            for index in range(4):
                points.extend(_edge_points(model, edge_key(*block.directed_edge(index))))
        elif kind == "edge":
            points.extend(_edge_points(model, key))
        else:
            vertex = model.vertices[key]
            points.append((vertex.x, vertex.y))
    box = _expand_degenerate(_bounds_of(points))
    x_min, y_min, x_max, y_max = box
    pad = margin * max(x_max - x_min, y_max - y_min)
    return x_min - pad, y_min - pad, x_max + pad, y_max + pad


def resolve_entity(model: MeshModel, text: str) -> tuple[str, Any]:
    """Classify ``text`` as a block ID, vertex ID, or edge ``a-b``."""
    if any(block.id == text for block in model.blocks):
        return "block", text
    if text in model.vertices:
        return "vertex", text
    try:
        return "edge", parse_edge(model, text)
    except CommandError as exc:
        raise RenderError(
            f"Unknown entity {text!r}; expected a block ID, vertex ID, or edge a-b"
        ) from exc


def options_with_highlights(
    model: MeshModel, options: RenderOptions, entities: Sequence[str]
) -> RenderOptions:
    """Return options that highlight the given block, edge, and vertex names."""
    edges: list[EdgeKey] = []
    vertices: list[str] = []
    blocks: list[str] = []
    for entity in entities:
        kind, key = resolve_entity(model, entity)
        if kind == "edge":
            edges.append(key)
        elif kind == "vertex":
            vertices.append(key)
        else:
            blocks.append(key)
    return replace(
        options,
        highlight_edges=tuple(edges),
        highlight_vertices=tuple(vertices),
        highlight_blocks=tuple(blocks),
    )


# ---------------------------------------------------------------------------
# Viewport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Viewport:
    width: float
    height: float
    center_x: float
    center_y: float
    pixels_per_unit: float
    offset_x: float
    offset_y: float

    def to_screen(self, x: float, y: float) -> Point:
        return (
            self.offset_x + (x - self.center_x) * self.pixels_per_unit,
            self.offset_y - (y - self.center_y) * self.pixels_per_unit,
        )

    def world_bounds(self) -> Bounds:
        return (
            self.center_x - self.offset_x / self.pixels_per_unit,
            self.center_y - (self.height - self.offset_y) / self.pixels_per_unit,
            self.center_x + (self.width - self.offset_x) / self.pixels_per_unit,
            self.center_y + self.offset_y / self.pixels_per_unit,
        )


def _viewport(
    bounds: Bounds,
    width: float,
    height: float,
    padding: float,
    top_extra: float = 0.0,
) -> _Viewport:
    """Fit ``bounds`` below a reserved band of ``top_extra`` pixels."""
    x_min, y_min, x_max, y_max = bounds
    usable_width = max(1.0, width - 2.0 * padding)
    usable_height = max(1.0, height - 2.0 * padding - top_extra)
    pixels_per_unit = min(
        usable_width / max(x_max - x_min, 1.0e-12),
        usable_height / max(y_max - y_min, 1.0e-12),
    )
    return _Viewport(
        width,
        height,
        (x_min + x_max) / 2.0,
        (y_min + y_max) / 2.0,
        pixels_per_unit,
        width / 2.0,
        padding + top_extra + usable_height / 2.0,
    )


def _bounds_of(points: Sequence[Point]) -> Bounds:
    if not points:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _expand_degenerate(bounds: Bounds) -> Bounds:
    x_min, y_min, x_max, y_max = bounds
    size = max(x_max - x_min, y_max - y_min)
    if size <= 1.0e-12:
        size = 1.0
    if x_max - x_min <= 1.0e-12:
        x_min -= size / 2.0
        x_max += size / 2.0
    if y_max - y_min <= 1.0e-12:
        y_min -= size / 2.0
        y_max += size / 2.0
    return x_min, y_min, x_max, y_max


def _in_view(point: Point, bounds: Bounds, margin: float) -> bool:
    return (
        bounds[0] - margin <= point[0] <= bounds[2] + margin
        and bounds[1] - margin <= point[1] <= bounds[3] + margin
    )


def _edge_points(model: MeshModel, edge: EdgeKey) -> tuple[Point, ...]:
    return model.edge_render_points(
        edge,
        arc_segments=CURVE_RENDER_SEGMENTS,
        spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
    )


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _draw(
    model: MeshModel, options: RenderOptions, painter: _Painter, *, scale: float
) -> None:
    model.validate()
    bounds = options.bounds or content_bounds(
        model, include_curves=options.show_curves
    )
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise RenderError("Render bounds must have positive width and height")
    font = options.font_size * scale
    px = scale
    legend_rows = _legend_rows(model, options, bounds) if options.show_legend else []
    legend_height = _legend_height(legend_rows, font, px)
    view = _viewport(
        bounds,
        options.width * scale,
        options.height * scale,
        options.padding * scale,
        top_extra=legend_height,
    )
    visible = view.world_bounds()
    cull_margin = 40.0 / view.pixels_per_unit

    painter.rect(0, 0, view.width, view.height, BACKGROUND)
    if options.show_grid:
        _draw_grid(painter, view, font, px)

    occurrences = model.edge_occurrences()
    edges = model.edges()
    edge_paths = {current: _edge_points(model, current) for current in edges}

    for block_id in options.highlight_blocks:
        block = next((item for item in model.blocks if item.id == block_id), None)
        if block is None:
            continue
        loop: list[Point] = []
        for index in range(4):
            directed = block.directed_edge(index)
            current = edge_key(*directed)
            points = edge_paths[current]
            if directed != current:
                points = tuple(reversed(points))
            loop.extend(points[:-1])
        painter.polygon(
            [view.to_screen(*point) for point in loop],
            HIGHLIGHT_COLOR,
            0.18,
        )

    if options.show_preview:
        preview = build_mesh_preview(model, options.preview_coarsening)
        for polyline in preview.polylines:
            if not any(_in_view(point, visible, cull_margin) for point in polyline):
                continue
            painter.line(
                [view.to_screen(*point) for point in polyline],
                PREVIEW_COLOR,
                1.0 * px,
            )

    if options.show_curves:
        _draw_curves(model, painter, view, visible, cull_margin, font, px)

    highlighted_edges = set(options.highlight_edges)
    for current in edges:
        points = edge_paths[current]
        if not any(_in_view(point, visible, cull_margin) for point in points):
            continue
        boundary_name = model.edge_boundaries.get(current)
        exterior = len(occurrences[current]) == 1
        if current in highlighted_edges:
            color = HIGHLIGHT_COLOR
            width = 4.0
        elif boundary_name is not None:
            color = model.boundaries[boundary_name].color
            width = 3.0
        elif exterior:
            color = EXTERIOR_EDGE_COLOR
            width = 2.0
        else:
            color = INTERNAL_EDGE_COLOR
            width = 1.5
        painter.line([view.to_screen(*point) for point in points], color, width * px)

        cells = model.edge_cells[current]
        if options.show_edge_nodes and 1 < cells <= MAX_INDIVIDUAL_EDGE_NODE_MARKERS:
            for index in range(1, cells):
                node = model.edge_point(current, model.edge_node_fraction(current, index))
                if not _in_view(node, visible, 0.0):
                    continue
                x, y = view.to_screen(*node)
                painter.circle(x, y, 2.0 * px, color, None, 0.0)

        if options.show_control_points:
            control_points = model.edge_control_points(current)
            dense = len(control_points) > MAX_VISIBLE_CONTROL_POINTS
            for index, point in enumerate(control_points):
                if not _in_view(point, visible, 0.0):
                    continue
                x, y = view.to_screen(*point)
                radius = (2.5 if dense else 5.0) * px
                painter.circle(x, y, radius, CONTROL_POINT_COLOR, "#ffffff", 1.0 * px)
                if not dense and len(control_points) > 1:
                    painter.text(
                        x, y, str(index + 1), font * 0.6, "#ffffff",
                        anchor="middle", bold=True, halo=False,
                    )

        if options.show_edge_cells:
            midpoint = model.edge_point(current, 0.5)
            if _in_view(midpoint, visible, 0.0):
                x, y = _label_position(
                    model, current, occurrences[current], view, 10.0 * px
                )
                painter.text(
                    x, y, str(cells), font * 0.85,
                    color if boundary_name or current in highlighted_edges
                    else CELL_LABEL_COLOR,
                    anchor="middle",
                    bold=current in highlighted_edges,
                )

    if options.show_spacing_links:
        for link in sorted(model.spacing_links):
            vertex = model.vertices[link.vertex]
            if not _in_view((vertex.x, vertex.y), visible, cull_margin):
                continue
            for current in (link.first_edge, link.second_edge):
                fraction = 0.08 if current[0] == link.vertex else 0.92
                tip = model.edge_point(current, fraction)
                painter.line(
                    [view.to_screen(vertex.x, vertex.y), view.to_screen(*tip)],
                    SPACING_LINK_COLOR,
                    5.0 * px,
                )

    used_vertices = {
        identifier for block in model.blocks for identifier in block.vertices
    }
    highlighted_vertices = set(options.highlight_vertices)
    for identifier, vertex in model.vertices.items():
        if not _in_view((vertex.x, vertex.y), visible, cull_margin):
            continue
        x, y = view.to_screen(vertex.x, vertex.y)
        highlighted = identifier in highlighted_vertices
        fill = HIGHLIGHT_COLOR if highlighted else VERTEX_COLOR
        radius = (7.0 if highlighted else 5.0) * px
        if identifier in used_vertices or highlighted:
            painter.circle(x, y, radius, fill, "#ffffff", 1.5 * px)
        else:
            painter.circle(x, y, radius, BACKGROUND, VERTEX_COLOR, 2.0 * px)
        if options.show_vertex_ids:
            painter.text(
                x + 7.0 * px, y + 7.0 * px, identifier, font * 0.9,
                HIGHLIGHT_COLOR if highlighted else VERTEX_LABEL_COLOR,
                anchor="start", baseline="hanging", bold=highlighted,
            )

    if options.show_block_ids:
        for block in model.blocks:
            centroid = _block_centroid(model, block, edge_paths)
            if not _in_view(centroid, visible, 0.0):
                continue
            x, y = view.to_screen(*centroid)
            highlighted = block.id in options.highlight_blocks
            painter.label(
                x, y, block.id, font,
                HIGHLIGHT_COLOR if highlighted else BLOCK_LABEL_COLOR,
                "#ffffff",
                HIGHLIGHT_COLOR if highlighted else "#9fb3c8",
                px,
            )

    if legend_rows:
        _draw_legend(legend_rows, painter, font, px)


def _label_position(
    model: MeshModel,
    current: EdgeKey,
    occurrences: list,
    view: _Viewport,
    offset_pixels: float,
) -> Point:
    """Place an edge label just outside the first incident block."""
    before = model.edge_point(current, 0.45)
    after = model.edge_point(current, 0.55)
    midpoint = model.edge_point(current, 0.5)
    tangent_x = after[0] - before[0]
    tangent_y = after[1] - before[1]
    length = math.hypot(tangent_x, tangent_y)
    if length <= 0.0:
        return view.to_screen(midpoint[0], midpoint[1] + offset_pixels / view.pixels_per_unit)
    # Blocks are counter-clockwise, so the right-hand normal of a block's
    # directed edge points out of that block.
    _, _, directed = occurrences[0]
    sign = 1.0 if directed == current else -1.0
    normal_x = sign * tangent_y / length
    normal_y = -sign * tangent_x / length
    offset = offset_pixels / view.pixels_per_unit
    return view.to_screen(
        midpoint[0] + normal_x * offset, midpoint[1] + normal_y * offset
    )


def _draw_grid(painter: _Painter, view: _Viewport, font: float, px: float) -> None:
    x_min, y_min, x_max, y_max = view.world_bounds()
    step = nice_grid_step((x_max - x_min) / 10.0)
    if step <= 0.0 or not math.isfinite(step):
        return
    start = math.floor(x_min / step) * step
    value = start
    while value <= x_max:
        x, _ = view.to_screen(value, 0.0)
        painter.line([(x, 0.0), (x, view.height)], GRID_COLOR, 1.0 * px)
        painter.text(
            x + 2.0 * px, view.height - 4.0 * px, _grid_label(value, step),
            font * 0.75, GRID_LABEL_COLOR, anchor="start", baseline="alphabetic",
            halo=False,
        )
        value += step
    value = math.floor(y_min / step) * step
    while value <= y_max:
        _, y = view.to_screen(0.0, value)
        painter.line([(0.0, y), (view.width, y)], GRID_COLOR, 1.0 * px)
        painter.text(
            4.0 * px, y - 2.0 * px, _grid_label(value, step), font * 0.75,
            GRID_LABEL_COLOR, anchor="start", baseline="alphabetic", halo=False,
        )
        value += step


def _grid_label(value: float, step: float) -> str:
    if abs(value) < step * 1.0e-6:
        value = 0.0
    return format(value, ".6g")


def _draw_curves(
    model: MeshModel,
    painter: _Painter,
    view: _Viewport,
    visible: Bounds,
    cull_margin: float,
    font: float,
    px: float,
) -> None:
    for curve in model.geometry_curves.values():
        points = model.geometry_curve_render_points(
            curve.id, samples_per_span=GEOMETRY_SAMPLES_PER_SPAN
        )
        if not any(_in_view(point, visible, cull_margin) for point in points):
            continue
        screen = [view.to_screen(*point) for point in points]
        painter.line(screen, CURVE_COLOR, 1.5 * px, dash=(6.0 * px, 4.0 * px))
        if curve.show_points and len(curve.points) <= MAX_VISIBLE_CONTROL_POINTS:
            for point in curve.points:
                if _in_view(point, visible, 0.0):
                    x, y = view.to_screen(*point)
                    painter.circle(x, y, 2.5 * px, CURVE_COLOR, None, 0.0)
        label_point = model.geometry_curve_point(curve.id, 0.5)
        if _in_view(label_point, visible, 0.0):
            x, y = view.to_screen(*label_point)
            painter.text(
                x, y - 8.0 * px, f"{curve.id} {curve.name}", font * 0.85,
                CURVE_COLOR, anchor="middle",
            )


def _block_centroid(
    model: MeshModel, block, edge_paths: dict[EdgeKey, tuple[Point, ...]]
) -> Point:
    # Average edge midpoints so the label stays inside curved blocks.
    xs = []
    ys = []
    for index in range(4):
        current = edge_key(*block.directed_edge(index))
        midpoint = model.edge_point(current, 0.5)
        xs.append(midpoint[0])
        ys.append(midpoint[1])
    return sum(xs) / 4.0, sum(ys) / 4.0


def _legend_rows(
    model: MeshModel, options: RenderOptions, bounds: Bounds
) -> list[tuple[str | None, str]]:
    rows: list[tuple[str | None, str]] = []
    if options.title:
        rows.append((None, options.title))
    total_cells = sum(
        nx * ny * nz
        for nx, ny, nz in (model.block_cell_counts(block) for block in model.blocks)
    )
    rows.append((
        None,
        f"{len(model.blocks)} block(s), {len(model.edges())} edges, "
        f"{total_cells} cells",
    ))
    for boundary in model.boundaries.values():
        suffix = f" ({boundary.kind}" + (
            f" -> {boundary.neighbour_patch})" if boundary.neighbour_patch else ")"
        )
        rows.append((boundary.color, boundary.name + suffix))
    unassigned = sum(
        1 for current in model.edges()
        if model.is_boundary_edge(current) and current not in model.edge_boundaries
    )
    if unassigned:
        rows.append((EXTERIOR_EDGE_COLOR, f"{unassigned} unassigned exterior edge(s)"))
    if any(len(items) == 2 for items in model.edge_occurrences().values()):
        rows.append((INTERNAL_EDGE_COLOR, "internal edge"))
    if options.show_curves and model.geometry_curves:
        rows.append((CURVE_COLOR, "reference curve"))
    if options.show_spacing_links and model.spacing_links:
        rows.append((SPACING_LINK_COLOR, "spacing link"))
    if options.highlight_edges or options.highlight_vertices or options.highlight_blocks:
        rows.append((HIGHLIGHT_COLOR, "highlighted"))
    rows.append((
        None,
        f"view x {bounds[0]:.6g}..{bounds[2]:.6g}, y {bounds[1]:.6g}..{bounds[3]:.6g}",
    ))
    return rows


def _legend_height(rows: list, font: float, px: float) -> float:
    if not rows:
        return 0.0
    return font * 1.5 * len(rows) + 10.0 * px + 16.0 * px


def _draw_legend(
    rows: list[tuple[str | None, str]],
    painter: _Painter,
    font: float,
    px: float,
) -> None:
    line_height = font * 1.5
    box_width = (max(len(text) for _, text in rows) * font * 0.58) + 34.0 * px
    box_height = line_height * len(rows) + 10.0 * px
    x0 = 10.0 * px
    y0 = 10.0 * px
    painter.rect(x0, y0, box_width, box_height, "#ffffff", "#c9d3dd", 1.0 * px, 0.92)
    for index, (color, text) in enumerate(rows):
        y = y0 + 5.0 * px + line_height * index + line_height / 2.0
        text_x = x0 + 8.0 * px
        if color is not None:
            painter.rect(text_x, y - 4.0 * px, 14.0 * px, 8.0 * px, color)
            text_x += 20.0 * px
        painter.text(
            text_x, y, text, font * 0.85, BLOCK_LABEL_COLOR,
            anchor="start", bold=(index == 0 and color is None and len(rows) > 1
                                  and not text.startswith("view ")
                                  and not text[0].isdigit()),
            halo=False,
        )


# ---------------------------------------------------------------------------
# Painters
# ---------------------------------------------------------------------------


class _Painter:
    def rect(
        self, x: float, y: float, width: float, height: float, fill: str,
        outline: str | None = None, outline_width: float = 0.0,
        opacity: float = 1.0,
    ) -> None:
        raise NotImplementedError

    def line(
        self, points: Sequence[Point], color: str, width: float,
        dash: tuple[float, float] | None = None,
    ) -> None:
        raise NotImplementedError

    def polygon(self, points: Sequence[Point], fill: str, opacity: float) -> None:
        raise NotImplementedError

    def circle(
        self, x: float, y: float, radius: float, fill: str | None,
        outline: str | None, outline_width: float,
    ) -> None:
        raise NotImplementedError

    def text(
        self, x: float, y: float, text: str, size: float, color: str, *,
        anchor: str = "middle", baseline: str = "middle", bold: bool = False,
        halo: bool = True,
    ) -> None:
        raise NotImplementedError

    def label(
        self, x: float, y: float, text: str, size: float, color: str,
        fill: str, outline: str, px: float,
    ) -> None:
        width = len(text) * size * 0.62 + 10.0 * px
        height = size * 1.5
        self.rect(x - width / 2.0, y - height / 2.0, width, height, fill, outline,
                  1.0 * px, 0.95)
        self.text(x, y, text, size, color, anchor="middle", bold=True, halo=False)


class _SvgPainter(_Painter):
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.elements: list[str] = []

    def rect(self, x, y, width, height, fill, outline=None, outline_width=0.0,
             opacity=1.0) -> None:
        stroke = (
            f' stroke="{outline}" stroke-width="{outline_width:.2f}"'
            if outline else ""
        )
        alpha = f' fill-opacity="{opacity:.3f}"' if opacity < 1.0 else ""
        self.elements.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" '
            f'height="{height:.2f}" fill="{fill}"{alpha}{stroke} rx="3"/>'
        )

    def line(self, points, color, width, dash=None) -> None:
        if len(points) < 2:
            return
        coordinates = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        dashes = (
            f' stroke-dasharray="{dash[0]:.1f} {dash[1]:.1f}"' if dash else ""
        )
        self.elements.append(
            f'<polyline points="{coordinates}" fill="none" stroke="{color}" '
            f'stroke-width="{width:.2f}" stroke-linejoin="round" '
            f'stroke-linecap="round"{dashes}/>'
        )

    def polygon(self, points, fill, opacity) -> None:
        coordinates = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        self.elements.append(
            f'<polygon points="{coordinates}" fill="{fill}" '
            f'fill-opacity="{opacity:.3f}" stroke="none"/>'
        )

    def circle(self, x, y, radius, fill, outline, outline_width) -> None:
        stroke = (
            f' stroke="{outline}" stroke-width="{outline_width:.2f}"'
            if outline else ""
        )
        self.elements.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" '
            f'fill="{fill or "none"}"{stroke}/>'
        )

    def text(self, x, y, text, size, color, *, anchor="middle",
             baseline="middle", bold=False, halo=True) -> None:
        svg_anchor = {"start": "start", "end": "end"}.get(anchor, "middle")
        svg_baseline = {
            "hanging": "hanging", "alphabetic": "alphabetic",
        }.get(baseline, "central")
        weight = ' font-weight="bold"' if bold else ""
        stroke = (
            f' stroke="{BACKGROUND}" stroke-width="{size * 0.25:.2f}" '
            'paint-order="stroke" stroke-linejoin="round"'
            if halo else ""
        )
        self.elements.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" '
            f'font-family="Helvetica, Arial, sans-serif" fill="{color}" '
            f'text-anchor="{svg_anchor}" dominant-baseline="{svg_baseline}"'
            f'{weight}{stroke}>{html.escape(text)}</text>'
        )

    def finish(self) -> str:
        body = "\n".join(f"  {element}" for element in self.elements)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
            f'height="{self.height}" viewBox="0 0 {self.width} {self.height}">\n'
            f"{body}\n</svg>\n"
        )


class _PillowPainter(_Painter):
    def __init__(self, width: int, height: int, scale: int) -> None:
        from PIL import Image, ImageDraw

        self.scale = scale
        self.target_size = (width, height)
        self.image = Image.new(
            "RGBA", (width * scale, height * scale), BACKGROUND
        )
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self._fonts: dict[tuple[int, bool], Any] = {}

    def _font(self, size: float, bold: bool):
        from PIL import ImageFont

        key = (max(1, int(round(size))), bold)
        font = self._fonts.get(key)
        if font is None:
            try:
                font = ImageFont.load_default(size=key[0])
            except TypeError:  # pragma: no cover - very old Pillow
                font = ImageFont.load_default()
            self._fonts[key] = font
        return font

    @staticmethod
    def _rgba(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
        value = color.lstrip("#")
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
        return red, green, blue, int(round(255 * opacity))

    def rect(self, x, y, width, height, fill, outline=None, outline_width=0.0,
             opacity=1.0) -> None:
        if opacity < 1.0:
            from PIL import Image, ImageDraw

            overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rounded_rectangle(
                (x, y, x + width, y + height),
                radius=3 * self.scale,
                fill=self._rgba(fill, opacity),
                outline=self._rgba(outline) if outline else None,
                width=max(1, int(round(outline_width))),
            )
            self.image.alpha_composite(overlay)
            self.draw = ImageDraw.Draw(self.image, "RGBA")
            return
        self.draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=3 * self.scale,
            fill=self._rgba(fill),
            outline=self._rgba(outline) if outline else None,
            width=max(1, int(round(outline_width))) if outline else 0,
        )

    def line(self, points, color, width, dash=None) -> None:
        if len(points) < 2:
            return
        stroke = max(1, int(round(width)))
        if dash is None:
            self.draw.line(list(points), fill=self._rgba(color), width=stroke,
                           joint="curve")
            return
        on, off = dash
        pattern_on = True
        remaining = on
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            segment = math.hypot(x2 - x1, y2 - y1)
            position = 0.0
            while position < segment:
                step = min(remaining, segment - position)
                if pattern_on:
                    start = (
                        x1 + (x2 - x1) * position / segment,
                        y1 + (y2 - y1) * position / segment,
                    )
                    end = (
                        x1 + (x2 - x1) * (position + step) / segment,
                        y1 + (y2 - y1) * (position + step) / segment,
                    )
                    self.draw.line([start, end], fill=self._rgba(color), width=stroke)
                position += step
                remaining -= step
                if remaining <= 1.0e-9:
                    pattern_on = not pattern_on
                    remaining = on if pattern_on else off

    def polygon(self, points, fill, opacity) -> None:
        from PIL import Image, ImageDraw

        if len(points) < 3:
            return
        overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).polygon(list(points), fill=self._rgba(fill, opacity))
        self.image.alpha_composite(overlay)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def circle(self, x, y, radius, fill, outline, outline_width) -> None:
        self.draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=self._rgba(fill) if fill else None,
            outline=self._rgba(outline) if outline else None,
            width=max(1, int(round(outline_width))) if outline else 0,
        )

    def text(self, x, y, text, size, color, *, anchor="middle",
             baseline="middle", bold=False, halo=True) -> None:
        horizontal = {"start": "l", "end": "r"}.get(anchor, "m")
        vertical = {"hanging": "a", "alphabetic": "s"}.get(baseline, "m")
        font = self._font(size, bold)
        stroke_width = max(1, int(round(size * 0.12))) if halo else 0
        self.draw.text(
            (x, y), text, fill=self._rgba(color), font=font,
            anchor=horizontal + vertical,
            stroke_width=stroke_width,
            stroke_fill=self._rgba(BACKGROUND) if halo else None,
        )

    def finish(self):
        from PIL import Image

        if self.scale == 1:
            return self.image.convert("RGB")
        return self.image.resize(self.target_size, Image.LANCZOS).convert("RGB")
