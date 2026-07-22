"""Canvas rendering, coordinate transforms, and pointer interaction."""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter
import tkinter as tk
from tkinter import font as tkfont

from .config import ConfigError, save_config
from .model import EdgeKey, MeshModel, TopologyError
from .render_cache import bounds_intersect, point_in_bounds
from .ui_helpers import (
    CURVE_RENDER_SEGMENTS,
    GEOMETRY_SAMPLES_PER_SPAN,
    MAX_VISIBLE_CONTROL_POINTS,
    MAX_VISIBLE_EDGE_MARKERS,
    MAX_ZOOM_PIXELS_PER_UNIT,
    SPLINE_SAMPLES_PER_SPAN,
    display_number as _display_number,
    nice_grid_step as _nice_grid_step,
    positive_integer as _positive_integer,
    scaled_named_font_size as _scaled_named_font_size,
    visible_control_point_indices as _visible_control_point_indices,
)


@dataclass(frozen=True, slots=True)
class _CanvasTransform:
    """One viewport's cached affine world/screen coordinate transform."""

    width: int
    height: int
    view_x: float
    view_y: float
    pixels_per_unit: float

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.width / 2.0 + (x - self.view_x) * self.pixels_per_unit,
            self.height / 2.0 - (y - self.view_y) * self.pixels_per_unit,
        )

    def screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.view_x + (x - self.width / 2.0) / self.pixels_per_unit,
            self.view_y - (y - self.height / 2.0) / self.pixels_per_unit,
        )


class CanvasControllerMixin:
    """Draw and interact with topology and reference geometry on the canvas."""

    VIEWPORT_REDRAW_INTERVAL_MS = 16
    VIEWPORT_CULL_PADDING_PIXELS = 40

    def redraw(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self._cancel_viewport_redraw()
        self.canvas.delete("all")
        self.item_targets.clear()
        transform = self._refresh_canvas_transform()
        width = transform.width
        height = transform.height
        self._redraw_world_bounds = self._viewport_world_bounds(transform)
        edges = self.model.edges()
        show_vertex_ids_var = getattr(self, "show_vertex_ids_var", None)
        show_vertex_ids = (
            show_vertex_ids_var is None or bool(show_vertex_ids_var.get())
        )
        show_edge_cell_counts_var = getattr(
            self, "show_edge_cell_counts_var", None
        )
        show_edge_cell_counts = (
            show_edge_cell_counts_var is None
            or bool(show_edge_cell_counts_var.get())
        )
        self.render_path_cache.prune(edges, self.model.geometry_curves)
        self._draw_grid(width, height)
        if self.show_mesh_preview_var.get():
            self._draw_mesh_preview()
        if not self.show_block_mesh_var.get():
            if self.show_geometry_var.get():
                self._draw_geometry_curves()
            return

        for current in edges:
            render_path = self.render_path_cache.edge_path(
                self.model,
                current,
                arc_segments=CURVE_RENDER_SEGMENTS,
                spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
            )
            if not bounds_intersect(
                render_path.bounds, self._redraw_world_bounds
            ):
                continue
            selected = current == self.selected_edge
            spacing_staged = (
                getattr(self, "spacing_link_mode_active", False)
                and current == getattr(self, "spacing_link_first_edge", None)
            )
            projection_selected = current in self.projection_edges
            boundary_name = self.model.edge_boundaries.get(current)
            boundary_color = (
                self.model.boundaries[boundary_name].color
                if boundary_name is not None else None
            )
            if projection_selected:
                color = "#9c36b5"
            elif spacing_staged:
                color = "#7048a8"
            elif boundary_color is not None:
                color = boundary_color
            elif self.boundary_mode_active and not self.model.is_boundary_edge(current):
                color = "#9aa5b1"
            else:
                color = "#e8590c" if selected else "#334e68"
            active_boundary_edge = (
                self.boundary_mode_active
                and boundary_name == self.active_boundary_name
            )
            edge_type = self.model.edge_type(current)
            screen_points: list[float] = []
            for world_x, world_y in render_path.points:
                screen_points.extend(self.world_to_screen(world_x, world_y))
            line = self.canvas.create_line(
                *screen_points,
                fill=color,
                width=self._px(
                    5 if projection_selected or active_boundary_edge
                    or spacing_staged
                    else 4 if selected
                    else 3 if boundary_color
                    else 2
                ),
            )
            self.item_targets[line] = ("edge", current)
            if self.show_edge_nodes_var.get():
                self._draw_edge_nodes(current, color)

            midpoint_world = self.model.edge_point(current, 0.5)
            if show_edge_cell_counts and point_in_bounds(
                midpoint_world, self._redraw_world_bounds
            ):
                midpoint_x, midpoint_y = self.world_to_screen(*midpoint_world)
                label = self.canvas.create_text(
                    midpoint_x,
                    midpoint_y - self._px(11),
                    text=f"{self.model.edge_cells[current]}",
                    fill=(
                        boundary_color
                        or ("#9c3d10" if selected else "#52606d")
                    ),
                    font=self._font(9, "bold" if selected else "normal"),
                )
                self.item_targets[label] = ("edge", current)

            control_points = (
                self.model.edge_control_points(current)
                if self.show_edge_interpolation_points_var.get()
                else ()
            )
            dense_control_points = (
                len(control_points) > MAX_VISIBLE_CONTROL_POINTS
            )
            selected_point_index = (
                self.selected_control_point_index if selected else None
            )
            for point_index in _visible_control_point_indices(
                len(control_points), selected_point_index
            ):
                point_x, point_y = control_points[point_index]
                if not point_in_bounds(
                    (point_x, point_y), self._redraw_world_bounds
                ):
                    continue
                control_x, control_y = self.world_to_screen(point_x, point_y)
                point_selected = selected \
                    and point_index == self.selected_control_point_index
                control_radius = self._px(
                    8 if point_selected else 3 if dense_control_points else 6
                )
                control = self.canvas.create_oval(
                    control_x - control_radius,
                    control_y - control_radius,
                    control_x + control_radius,
                    control_y + control_radius,
                    fill="#7048a8",
                    outline="#e8590c" if point_selected else "#ffffff",
                    width=self._px(2),
                )
                point_target = (current, point_index)
                self.item_targets[control] = ("control_point", point_target)
                if edge_type in MeshModel.MULTI_POINT_EDGE_TYPES \
                        and (not dense_control_points or point_selected):
                    order_label = self.canvas.create_text(
                        control_x,
                        control_y,
                        text=str(point_index + 1),
                        fill="#ffffff",
                        font=self._font(7, "bold"),
                    )
                    self.item_targets[order_label] = (
                        "control_point", point_target
                    )

        self._draw_spacing_link_markers()

        for identifier, vertex in self.model.vertices.items():
            if not point_in_bounds(
                (vertex.x, vertex.y), self._redraw_world_bounds
            ):
                continue
            x, y = self.world_to_screen(vertex.x, vertex.y)
            selected = identifier == self.selected_vertex
            projection_selected = identifier in self.projection_vertex_ids
            staged_index = None
            if self.block_vertex_selection is not None \
                    and identifier in self.block_vertex_selection:
                staged_index = self.block_vertex_selection.index(identifier)
            # Deliberately larger than edge markers so vertices remain easy to
            # acquire with the mouse, including on high-density displays.
            emphasized = selected or projection_selected or staged_index is not None
            radius = self._px(9 if emphasized else 7)
            item = self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=(
                    "#7048a8" if staged_index is not None
                    else "#9c36b5" if projection_selected
                    else "#e8590c" if selected
                    else "#1971c2"
                ),
                outline="#ffffff",
                width=self._px(2),
            )
            self.item_targets[item] = ("vertex", identifier)
            if staged_index is not None:
                order_label = self.canvas.create_text(
                    x,
                    y,
                    text=str(staged_index + 1),
                    fill="#ffffff",
                    font=self._font(7, "bold"),
                )
                self.item_targets[order_label] = ("vertex", identifier)
            if show_vertex_ids:
                label = self.canvas.create_text(
                    x + self._px(11),
                    y + self._px(11),
                    text=identifier,
                    anchor="nw",
                    fill="#243b53",
                    font=self._font(9),
                )
                self.item_targets[label] = ("vertex", identifier)

        if self.show_geometry_var.get():
            self._draw_geometry_curves()
        if self.split_edge_active is not None:
            self._draw_split_marker()

    def _draw_mesh_preview(self) -> None:
        started = perf_counter()
        preview, cache_hit = self.mesh_preview_cache.get(
            self.model, self.preferences.preview_coarsening
        )
        data_ms = (perf_counter() - started) * 1000.0
        visible_bounds = getattr(self, "_redraw_world_bounds", None)
        visible_line_count = 0
        for polyline, polyline_bounds in zip(
            preview.polylines, preview.polyline_bounds
        ):
            if visible_bounds is not None \
                    and not bounds_intersect(polyline_bounds, visible_bounds):
                continue
            visible_line_count += 1
            screen_points: list[float] = []
            for world_x, world_y in polyline:
                screen_points.extend(self.world_to_screen(world_x, world_y))
            self.canvas.create_line(
                *screen_points,
                fill="#7fa9c2",
                width=self._px(1),
            )
        total_ms = (perf_counter() - started) * 1000.0
        cache_label = (
            f"cached; drawn in {total_ms:.1f} ms"
            if cache_hit
            else f"built in {data_ms:.1f} ms; drawn in {total_ms:.1f} ms"
        )
        self.mesh_preview_info_var.set(
            f"{preview.block_count} block"
            f"{'s' if preview.block_count != 1 else ''} · "
            f"{visible_line_count}/{preview.line_count} visible interior lines · "
            f"{preview.sampled_node_count} sampled nodes · {cache_label}."
        )

    def _draw_split_marker(self) -> None:
        if self.split_edge_active is None \
                or self.split_edge_active not in self.model.edge_cells:
            return
        world_x, world_y = self.model.edge_point(
            self.split_edge_active, self.split_fraction
        )
        visible_bounds = getattr(self, "_redraw_world_bounds", None)
        if visible_bounds is not None and not point_in_bounds(
            (world_x, world_y), visible_bounds
        ):
            return
        x, y = self.world_to_screen(world_x, world_y)
        radius = self._px(10)
        marker = self.canvas.create_polygon(
            x, y - radius,
            x + radius, y,
            x, y + radius,
            x - radius, y,
            fill="#9c36b5",
            outline="#ffffff",
            width=self._px(2),
        )
        self.item_targets[marker] = (
            "split_marker", self.split_edge_active
        )
        label = self.canvas.create_text(
            x,
            y - radius - self._px(8),
            text=f"{self.split_fraction * 100:.1f}%",
            fill="#7b2cbf",
            font=self._font(9, "bold"),
        )
        self.item_targets[label] = (
            "split_marker", self.split_edge_active
        )

    def _draw_geometry_curves(self) -> None:
        visible_bounds = getattr(self, "_redraw_world_bounds", None)
        for curve_id, curve in self.model.geometry_curves.items():
            render_path = self.render_path_cache.geometry_path(
                self.model,
                curve_id,
                samples_per_span=GEOMETRY_SAMPLES_PER_SPAN,
            )
            if visible_bounds is not None and not bounds_intersect(
                render_path.bounds, visible_bounds
            ):
                continue
            selected = curve_id == self.selected_geometry_curve
            projection_selected = curve_id in self.projection_curve_ids
            screen_points: list[float] = []
            for world_point in render_path.points:
                screen_points.extend(self.world_to_screen(*world_point))
            line = self.canvas.create_line(
                *screen_points,
                fill=(
                    "#087f5b" if projection_selected
                    else "#006d77" if selected
                    else "#0096a6"
                ),
                width=self._px(5 if projection_selected else 4 if selected else 3),
                dash=(self._px(7), self._px(4)),
            )
            self.item_targets[line] = ("geometry_curve", curve_id)

            label_point = self.model.geometry_curve_point(curve_id, 0.5)
            if visible_bounds is None or point_in_bounds(
                label_point, visible_bounds
            ):
                label_x, label_y = self.world_to_screen(*label_point)
                label = self.canvas.create_text(
                    label_x,
                    label_y - self._px(13),
                    text=curve.name,
                    fill="#087f5b" if projection_selected else "#006d77",
                    font=self._font(
                        9,
                        "bold" if selected or projection_selected else "normal",
                    ),
                )
                self.item_targets[label] = ("geometry_curve", curve_id)

            if not curve.show_points:
                continue
            for point_index, (point_x, point_y) in enumerate(curve.points):
                if visible_bounds is not None and not point_in_bounds(
                    (point_x, point_y), visible_bounds
                ):
                    continue
                x, y = self.world_to_screen(point_x, point_y)
                point_selected = selected \
                    and point_index == self.selected_geometry_point_index
                radius = self._px(8 if point_selected else 6)
                point = self.canvas.create_rectangle(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill="#e67700" if point_selected else "#12b8b0",
                    outline="#ffffff",
                    width=self._px(2),
                )
                target = (curve_id, point_index)
                self.item_targets[point] = ("geometry_point", target)
                order_label = self.canvas.create_text(
                    x,
                    y,
                    text=str(point_index + 1),
                    fill="#ffffff",
                    font=self._font(7, "bold"),
                )
                self.item_targets[order_label] = (
                    "geometry_point", target
                )

    def _draw_spacing_link_markers(self) -> None:
        """Draw short teal legs showing which edge endpoints are paired."""
        marker_length = self._px(18)
        for link in sorted(self.model.spacing_links):
            vertex = self.model.vertices[link.vertex]
            if not point_in_bounds(
                (vertex.x, vertex.y), self._redraw_world_bounds
            ):
                continue
            center_x, center_y = self.world_to_screen(vertex.x, vertex.y)
            for current in (link.first_edge, link.second_edge):
                fraction = 0.08 if link.vertex == current[0] else 0.92
                world_x, world_y = self.model.edge_point(current, fraction)
                target_x, target_y = self.world_to_screen(world_x, world_y)
                delta_x = target_x - center_x
                delta_y = target_y - center_y
                distance = math.hypot(delta_x, delta_y)
                if distance <= 0.0:
                    continue
                scale = min(1.0, marker_length / distance)
                end_x = center_x + delta_x * scale
                end_y = center_y + delta_y * scale
                leg = self.canvas.create_line(
                    center_x,
                    center_y,
                    end_x,
                    end_y,
                    fill="#0b8f87",
                    width=self._px(4),
                    capstyle=tk.ROUND,
                )
                self.item_targets[leg] = ("edge", current)

    def _draw_edge_nodes(self, current: EdgeKey, color: str) -> None:
        cells = self.model.edge_cells[current]
        if cells <= 1:
            return
        stride = max(1, math.ceil((cells - 1) / MAX_VISIBLE_EDGE_MARKERS))
        visible_bounds = getattr(self, "_redraw_world_bounds", None)
        for index in range(stride, cells, stride):
            ratio = self.model.edge_node_fraction(current, index)
            world_x, world_y = self.model.edge_point(current, ratio)
            if visible_bounds is not None and not point_in_bounds(
                (world_x, world_y), visible_bounds
            ):
                continue
            x, y = self.world_to_screen(world_x, world_y)
            item = self.canvas.create_oval(
                x - self._px(2.4),
                y - self._px(2.4),
                x + self._px(2.4),
                y + self._px(2.4),
                fill="#ffffff",
                outline=color,
                width=self._px(1),
            )
            self.item_targets[item] = ("edge", current)

    def _draw_grid(self, width: int, height: int) -> None:
        step = _nice_grid_step(
            (75.0 * self.display_scale) / self.pixels_per_unit
        )
        left, top = self.screen_to_world(0, 0)
        right, bottom = self.screen_to_world(width, height)
        min_x, max_x = sorted((left, right))
        min_y, max_y = sorted((bottom, top))

        x = math.ceil(min_x / step) * step
        while x <= max_x + step * 0.01:
            screen_x, _ = self.world_to_screen(x, 0.0)
            axis = math.isclose(x, 0.0, abs_tol=step * 1.0e-6)
            self.canvas.create_line(
                screen_x, 0, screen_x, height,
                fill="#9fb3c8" if axis else "#e4e7eb",
                width=self._px(2 if axis else 1),
            )
            if not axis:
                self.canvas.create_text(
                    screen_x + self._px(3), height - self._px(4),
                    text=_display_number(x),
                    anchor="sw",
                    fill="#829ab1",
                    font=self._font(8),
                )
            x += step

        y = math.ceil(min_y / step) * step
        while y <= max_y + step * 0.01:
            _, screen_y = self.world_to_screen(0.0, y)
            axis = math.isclose(y, 0.0, abs_tol=step * 1.0e-6)
            self.canvas.create_line(
                0, screen_y, width, screen_y,
                fill="#9fb3c8" if axis else "#e4e7eb",
                width=self._px(2 if axis else 1),
            )
            if not axis:
                self.canvas.create_text(
                    self._px(4), screen_y - self._px(3),
                    text=_display_number(y),
                    anchor="sw",
                    fill="#829ab1",
                    font=self._font(8),
                )
            y += step

        self.canvas.create_text(
            width - self._px(8), height - self._px(8),
            text="x", anchor="se", fill="#52606d", font=self._font(9),
        )
        self.canvas.create_text(
            self._px(8), self._px(8), text="y", anchor="nw",
            fill="#52606d", font=self._font(9),
        )

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return self._current_canvas_transform().world_to_screen(x, y)

    def screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        return self._current_canvas_transform().screen_to_world(x, y)

    def _current_canvas_transform(self) -> _CanvasTransform:
        transform = getattr(self, "_canvas_transform", None)
        if transform is None \
                or transform.view_x != self.view_x \
                or transform.view_y != self.view_y \
                or transform.pixels_per_unit != self.pixels_per_unit:
            return self._refresh_canvas_transform()
        return transform

    def _refresh_canvas_transform(self) -> _CanvasTransform:
        transform = _CanvasTransform(
            max(self.canvas.winfo_width(), 1),
            max(self.canvas.winfo_height(), 1),
            self.view_x,
            self.view_y,
            self.pixels_per_unit,
        )
        self._canvas_transform = transform
        return transform

    def _viewport_world_bounds(
        self, transform: _CanvasTransform
    ) -> tuple[float, float, float, float]:
        padding = self._px(self.VIEWPORT_CULL_PADDING_PIXELS)
        first = transform.screen_to_world(-padding, -padding)
        second = transform.screen_to_world(
            transform.width + padding,
            transform.height + padding,
        )
        return (
            min(first[0], second[0]),
            min(first[1], second[1]),
            max(first[0], second[0]),
            max(first[1], second[1]),
        )

    def _request_viewport_redraw(self) -> None:
        """Coalesce viewport events into at most one redraw per frame."""
        if getattr(self, "_viewport_redraw_after_id", None) is not None:
            return
        self._viewport_redraw_after_id = self.root.after(
            self.VIEWPORT_REDRAW_INTERVAL_MS,
            self._flush_viewport_redraw,
        )

    def _flush_viewport_redraw(self) -> None:
        self._viewport_redraw_after_id = None
        self.redraw()

    def _cancel_viewport_redraw(self) -> None:
        after_id = getattr(self, "_viewport_redraw_after_id", None)
        if after_id is None:
            return
        self._viewport_redraw_after_id = None
        try:
            self.root.after_cancel(after_id)
        except tk.TclError:
            # The timer may already be dispatching during shutdown.
            pass

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        self._request_viewport_redraw()

    def fit_view(self) -> None:
        self.root.update_idletasks()
        xs: list[float] = []
        ys: list[float] = []
        if self.show_block_mesh_var.get():
            xs.extend(vertex.x for vertex in self.model.vertices.values())
            ys.extend(vertex.y for vertex in self.model.vertices.values())
            for current in self.model.edge_geometry:
                for x, y in self.model.edge_control_points(current):
                    xs.append(x)
                    ys.append(y)
                render_path = self.render_path_cache.edge_path(
                    self.model,
                    current,
                    arc_segments=CURVE_RENDER_SEGMENTS,
                    spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
                )
                for x, y in render_path.points:
                    xs.append(x)
                    ys.append(y)
        if self.show_geometry_var.get():
            for curve_id, curve in self.model.geometry_curves.items():
                for x, y in curve.points:
                    xs.append(x)
                    ys.append(y)
                render_path = self.render_path_cache.geometry_path(
                    self.model,
                    curve_id,
                    samples_per_span=GEOMETRY_SAMPLES_PER_SPAN,
                )
                for x, y in render_path.points:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 0.25)
        span_y = max(max_y - min_y, 0.25)
        width = max(
            self.canvas.winfo_width() - self._px(100), self._px(100)
        )
        height = max(
            self.canvas.winfo_height() - self._px(100), self._px(100)
        )
        self.view_x = (min_x + max_x) / 2.0
        self.view_y = (min_y + max_y) / 2.0
        self.pixels_per_unit = min(width / span_x, height / span_y)
        self.pixels_per_unit = max(
            10.0 * self.display_scale,
            min(self.pixels_per_unit, 2000.0 * self.display_scale),
        )
        self.redraw()

    def apply_ui_scale(self) -> None:
        """Apply an accessibility scale on top of Tk's detected system DPI."""
        choice = self.ui_scale_var.get()
        multiplier = 1.0 if choice == "auto" else float(choice)
        old_scale = self.display_scale
        self.ui_scale_multiplier = multiplier
        self.display_scale = self.system_display_scale * multiplier
        ratio = self.display_scale / old_scale

        # The original Tk scaling already represents the OS DPI. The manual
        # multiplier changes named fonts and our pixel geometry on top of it.
        self._scale_named_fonts(multiplier)
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        target_width = min(
            int(self.root.winfo_width() * ratio), int(screen_width * 0.95)
        )
        target_height = min(
            int(self.root.winfo_height() * ratio), int(screen_height * 0.92)
        )
        self.root.geometry(f"{target_width}x{target_height}")
        self._set_minimum_window_size()
        self.toolbar.configure(padding=(self._px(8), self._px(6)))
        self.status_bar.configure(padding=(self._px(8), self._px(5)))
        self.sidebar_host.configure(width=self._px(300))
        self.sidebar.configure(padding=self._px(12))
        for child in self.sidebar.winfo_children():
            child.destroy()
        self._build_sidebar()
        self._update_property_panel()
        self.pixels_per_unit *= ratio
        self.redraw()
        label = "system automatic" if choice == "auto" else f"{choice}× system"
        self.preferences = self.preferences.with_ui_scale(choice)
        if not self.config_write_enabled:
            self.status.set(
                f"UI scale set to {label}, but preferences were not saved "
                "because the config could not be loaded at startup."
            )
            return
        try:
            save_config(self.preferences, self.config_path)
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            self.status.set(
                f"UI scale set to {label}, but could not save "
                f"{self.config_path}: {exc}"
            )
            return
        self.status.set(
            f"UI scale set to {label} and saved in {self.config_path}."
        )

    def apply_visibility(self) -> None:
        show_block_mesh = bool(self.show_block_mesh_var.get())
        show_geometry = bool(self.show_geometry_var.get())
        show_vertex_ids = bool(self.show_vertex_ids_var.get())
        show_edge_cell_counts = bool(self.show_edge_cell_counts_var.get())
        show_edge_nodes = bool(self.show_edge_nodes_var.get())
        show_edge_interpolation_points = bool(
            self.show_edge_interpolation_points_var.get()
        )
        show_mesh_preview = bool(self.show_mesh_preview_var.get())
        self.preferences = self.preferences.with_visibility(
            show_block_mesh=show_block_mesh,
            show_geometry=show_geometry,
            show_vertex_ids=show_vertex_ids,
            show_edge_cell_counts=show_edge_cell_counts,
            show_edge_nodes=show_edge_nodes,
            show_edge_interpolation_points=show_edge_interpolation_points,
            show_mesh_preview=show_mesh_preview,
        )
        if not show_block_mesh:
            self._clear_split_state()
            self._clear_projection_state()
            self.selected_vertex = None
            self.selected_edge = None
            self.selected_control_point_index = None
            self.block_vertex_selection = None
            self.vertex_placement_active = False
            self._clear_spacing_link_mode()
            if self.boundary_mode_active:
                self.boundary_mode_active = False
                self.boundary_button.configure(text="Set boundaries")
        if not show_geometry:
            self._clear_projection_state()
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
            self.drag_geometry_point = None
        self._update_property_panel()
        self.redraw()
        visible = []
        if show_block_mesh:
            visible.append("block mesh")
        if show_geometry:
            visible.append("geometry")
        if show_mesh_preview:
            visible.append("mesh preview")
        label = " and ".join(visible) if visible else "grid only"
        if not self.config_write_enabled:
            self.status.set(
                f"Showing {label}; visibility was not saved because the "
                "config could not be loaded at startup."
            )
            return
        try:
            save_config(self.preferences, self.config_path)
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            self.status.set(
                f"Showing {label}, but could not save {self.config_path}: {exc}"
            )
            return
        self.status.set(f"Showing {label}.")

    def apply_mesh_preview_coarsening(self) -> None:
        try:
            coarsening = _positive_integer(
                self.mesh_preview_coarsening_var.get(),
                "Preview coarsening",
            )
        except ValueError as exc:
            self._show_error("Invalid preview coarsening", exc)
            return
        self.preferences = self.preferences.with_preview_coarsening(coarsening)
        self.mesh_preview_coarsening_var.set(str(coarsening))
        self.redraw()
        if not self.config_write_enabled:
            self.status.set(
                f"Preview coarsening set to {coarsening}; it was not saved "
                "because the config could not be loaded at startup."
            )
            return
        try:
            save_config(self.preferences, self.config_path)
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            self.status.set(
                f"Preview coarsening set to {coarsening}, but could not save "
                f"{self.config_path}: {exc}"
            )
            return
        self.status.set(f"Preview coarsening set to {coarsening}.")

    def _px(self, value: float) -> int:
        return max(1, int(round(value * self.display_scale)))

    def _set_minimum_window_size(self) -> None:
        self.root.minsize(
            min(self._px(760), int(self.root.winfo_screenwidth() * 0.80)),
            min(self._px(500), int(self.root.winfo_screenheight() * 0.80)),
        )

    def _font(self, points: int, weight: str = "normal") -> tuple[str, int, str]:
        size = max(1, int(round(points * self.ui_scale_multiplier)))
        return self.default_font_family, size, weight

    def _scale_named_fonts(self, multiplier: float) -> None:
        for font_name, base_size in self.base_named_font_sizes.items():
            size = _scaled_named_font_size(
                base_size, self.system_display_scale, multiplier
            )
            tkfont.nametofont(font_name, root=self.root).configure(size=size)

    def _on_left_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        target = self._target_at_cursor()
        if getattr(self, "projection_stage", None) is not None:
            target = self._projection_target_at(event.x, event.y)
        self.last_pressed_target = target
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        if self.split_edge_active is not None:
            self.drag_split_marker = True
            self._update_split_marker_from_pointer(event)
            self.status.set(
                "Positioning split marker; release it anywhere, then press "
                "Enter or use Execute split."
            )
            return
        if self.export_mode_active:
            self.status.set(
                "Export settings are open; press E or Esc to return to editing."
            )
            return
        if getattr(self, "projection_stage", None) is not None:
            self._toggle_projection_target(target)
            return
        if getattr(self, "spacing_link_mode_active", False):
            current: EdgeKey | None = None
            if target is not None and target[0] == "edge":
                current = target[1]  # type: ignore[assignment]
            elif target is not None and target[0] == "control_point":
                current = target[1][0]  # type: ignore[index]
            if current is None:
                self.status.set(
                    "Spacing links: click a mesh edge, or press Esc to finish."
                )
                return
            self.select_spacing_link_edge(current)
            return
        if self.boundary_mode_active:
            if target is None or target[0] != "edge":
                self.status.set(
                    "Boundary mode: click an exterior edge, or press Esc to finish."
                )
                return
            current = target[1]
            if not isinstance(current, tuple) or len(current) != 2:
                return
            if self.active_boundary_name is None:
                self.status.set("Add and select a boundary before assigning edges.")
                return
            if not self.model.is_boundary_edge(current):
                self.status.set("Internal edges cannot belong to a boundary patch.")
                return
            existing = self.model.edge_boundaries.get(current)
            replacement = (
                None if existing == self.active_boundary_name
                else self.active_boundary_name
            )
            try:
                self.model.set_edge_boundary(current, replacement)
            except TopologyError as exc:
                self.status.set(str(exc))
                return
            self._commit_edit()
            self._update_property_panel()
            self.redraw()
            if replacement is None:
                self.status.set(
                    f"Unassigned edge {current[0]} — {current[1]}."
                )
            elif existing is None:
                self.status.set(
                    f"Assigned edge {current[0]} — {current[1]} to "
                    f"{replacement!r}."
                )
            else:
                self.status.set(
                    f"Reassigned edge {current[0]} — {current[1]} from "
                    f"{existing!r} to {replacement!r}."
                )
            return
        if self.vertex_placement_active:
            if target is not None:
                self.status.set(
                    "Vertex placement: click an empty canvas location."
                )
                return
            x, y = self.screen_to_world(event.x, event.y)
            try:
                vertex = self.model.add_vertex(x, y)
            except TopologyError as exc:
                self.status.set(str(exc))
                return
            self.vertex_placement_active = False
            self.last_pressed_target = None
            self.selected_vertex = vertex.id
            self.selected_edge = None
            self.selected_control_point_index = None
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
            self._commit_edit()
            self._update_property_panel()
            self.redraw()
            self.status.set(
                f"Added standalone vertex {vertex.id} at "
                f"({_display_number(x)}, {_display_number(y)})."
            )
            return
        if self.block_vertex_selection is not None:
            if target is not None and target[0] == "vertex":
                self._toggle_block_vertex(str(target[1]))
            else:
                self.status.set(
                    "New block mode: click an existing vertex or press Esc."
                )
            return
        if target is None:
            self.selected_vertex = None
            self.selected_edge = None
            self.selected_control_point_index = None
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
        elif target[0] == "vertex":
            self.selected_vertex = str(target[1])
            self.selected_edge = None
            self.selected_control_point_index = None
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
            self.drag_vertex = self.selected_vertex
        elif target[0] == "edge":
            self.selected_vertex = None
            self.selected_edge = target[1]  # type: ignore[assignment]
            self.selected_control_point_index = (
                0 if self.model.edge_control_points(self.selected_edge) else None
            )
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
        elif target[0] == "control_point":
            point_target = target[1]
            edge, point_index = point_target  # type: ignore[misc]
            self.selected_vertex = None
            self.selected_edge = edge
            self.selected_control_point_index = point_index
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
            self.drag_control_point = (edge, point_index)
        elif target[0] == "geometry_curve":
            curve_id = str(target[1])
            point_index = (
                self.selected_geometry_point_index
                if curve_id == self.selected_geometry_curve
                and self.selected_geometry_point_index is not None
                else 0
            )
            self._select_geometry_curve(curve_id, point_index)
        elif target[0] == "geometry_point":
            curve_id, point_index = target[1]  # type: ignore[misc]
            self._select_geometry_curve(curve_id, point_index)
            self.drag_geometry_point = (curve_id, point_index)
        self._update_property_panel()
        self.redraw()

    def _on_left_drag(self, event: tk.Event) -> None:
        if self.drag_split_marker:
            self._update_split_marker_from_pointer(event)
            return
        if self.drag_vertex is None and self.drag_control_point is None \
                and self.drag_geometry_point is None:
            return
        x, y = self.screen_to_world(event.x, event.y)
        try:
            if self.drag_vertex is not None:
                self.model.move_vertex(self.drag_vertex, x, y)
            elif self.drag_control_point is not None:
                edge, point_index = self.drag_control_point
                self.model.set_edge_control_point(edge, point_index, x, y)
            elif self.drag_geometry_point is not None:
                curve_id, point_index = self.drag_geometry_point
                self.model.set_geometry_curve_point(
                    curve_id, point_index, x, y
                )
        except TopologyError as exc:
            self.status.set(str(exc))
            return
        self.drag_changed = True
        self._refresh_dirty()
        self._sync_property_values()
        self.redraw()
        if self.drag_vertex is not None:
            target_name = self.drag_vertex
        elif self.drag_control_point is not None:
            target_name = f"Point {self.drag_control_point[1] + 1}"
        else:
            assert self.drag_geometry_point is not None
            target_name = f"Geometry point {self.drag_geometry_point[1] + 1}"
        self.status.set(
            f"{target_name}: ({_display_number(x)}, {_display_number(y)})"
        )

    def _on_left_release(self, _event: tk.Event) -> None:
        if self.drag_split_marker:
            self.drag_split_marker = False
            self.status.set(
                "Split location set. Reposition it if needed, then press "
                "Enter or use Execute split."
            )
            return
        if (self.drag_vertex is not None or self.drag_control_point is not None
                or self.drag_geometry_point is not None) \
                and self.drag_changed:
            self._commit_edit()
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False

    def _on_double_click(self, _event: tk.Event) -> None:
        if self.split_edge_active is not None or self.export_mode_active \
                or self.boundary_mode_active \
                or getattr(self, "spacing_link_mode_active", False) \
                or self.projection_stage is not None:
            return
        target = self._target_at_cursor()
        authoritative = (
            target
            if target is not None
            else self.last_pressed_target
        )
        if authoritative is None or authoritative[0] != "edge":
            return
        self.selected_vertex = None
        self.selected_edge = authoritative[1]  # type: ignore[assignment]
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        # The preceding ButtonPress binding redraws the canvas, which can clear
        # Tk's transient "current" item before this double-click binding runs.
        # In that case the edge selected by the press is still authoritative.
        if self.selected_edge is not None \
                and self.model.is_boundary_edge(self.selected_edge):
            self.add_selected_block()

    def _target_at_cursor(self) -> tuple[str, object] | None:
        current = self.canvas.find_withtag("current")
        if not current:
            return None
        return self.item_targets.get(current[-1])

    def _projection_target_at(
        self, x: float, y: float
    ) -> tuple[str, object] | None:
        """Pick a stage-relevant item even when both canvas layers overlap."""
        radius = self._px(4)
        items = reversed(self.canvas.find_overlapping(
            x - radius, y - radius, x + radius, y + radius
        ))
        targets = [
            self.item_targets[item]
            for item in items
            if item in self.item_targets
        ]
        if self.projection_stage == "curves":
            kinds = ("geometry_point", "geometry_curve")
        elif self.projection_entity_kind == "edge":
            kinds = ("control_point", "edge")
        elif self.projection_entity_kind == "vertex":
            kinds = ("vertex",)
        else:
            kinds = ("vertex", "control_point", "edge")
        for kind in kinds:
            for target in targets:
                if target[0] == kind:
                    return target
        return None

    def _on_pan_start(self, event: tk.Event) -> None:
        self.pan_anchor = (event.x, event.y, self.view_x, self.view_y)

    def _on_pan_drag(self, event: tk.Event) -> None:
        if self.pan_anchor is None:
            return
        transform = self._current_canvas_transform()
        start_x, start_y, center_x, center_y = self.pan_anchor
        self.view_x = center_x - (event.x - start_x) / self.pixels_per_unit
        self.view_y = center_y + (event.y - start_y) / self.pixels_per_unit
        self._canvas_transform = _CanvasTransform(
            transform.width,
            transform.height,
            self.view_x,
            self.view_y,
            self.pixels_per_unit,
        )
        self._request_viewport_redraw()

    def _on_mousewheel(self, event: tk.Event) -> None:
        transform = self._current_canvas_transform()
        before_x, before_y = transform.screen_to_world(event.x, event.y)
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            factor = 1.15
        else:
            factor = 1.0 / 1.15
        self.pixels_per_unit = max(
            10.0 * self.display_scale,
            min(
                MAX_ZOOM_PIXELS_PER_UNIT * self.display_scale,
                self.pixels_per_unit * factor,
            ),
        )
        width = transform.width
        height = transform.height
        self.view_x = before_x - (event.x - width / 2.0) / self.pixels_per_unit
        self.view_y = before_y + (event.y - height / 2.0) / self.pixels_per_unit
        self._canvas_transform = _CanvasTransform(
            width,
            height,
            self.view_x,
            self.view_y,
            self.pixels_per_unit,
        )
        self._request_viewport_redraw()
