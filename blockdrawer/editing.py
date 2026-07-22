"""Editing-mode controllers and mesh/geometry commands for the Tk interface."""

from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from .geometry import GeometryImportError, load_point_pairs
from .model import EdgeKey, TopologyError, edge_key
from .projection import DEFAULT_FIT_MAX_POINTS, FIT_RELATIVE_TOLERANCE
from .ui_helpers import (
    MIN_SPLIT_FRACTION,
    PROJECTION_DIRECTION_LABELS,
    display_split_percentage as _display_split_percentage,
    nearest_edge_fraction as _nearest_edge_fraction,
    positive_integer as _positive_integer,
    split_fraction_from_text as _split_fraction_from_text,
)


class EditingControllerMixin:
    """Coordinate boundaries, projection, topology, and geometry edits."""

    def toggle_spacing_link_mode(self) -> None:
        """Enter or leave focused endpoint-spacing link selection."""
        activating = not self.spacing_link_mode_active
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.spacing_link_mode_active = activating
        self.spacing_link_first_edge = (
            self.selected_edge
            if activating and self.selected_edge in self.model.edge_cells
            else None
        )
        self.vertex_placement_active = False
        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.spacing_link_button.configure(
            text="Done linking" if activating else "Link spacing"
        )
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        if not activating:
            self.status.set("Finished linking edge spacing.")
        elif self.spacing_link_first_edge is None:
            self.status.set(
                "Spacing links: select the first edge of an incident pair."
            )
        else:
            self.status.set(
                "Spacing links: selected the driver edge; now select an "
                "incident edge."
            )

    def _clear_spacing_link_mode(self) -> None:
        self.spacing_link_mode_active = False
        self.spacing_link_first_edge = None
        if hasattr(self, "spacing_link_button"):
            self.spacing_link_button.configure(text="Link spacing")

    def select_spacing_link_edge(self, edge: EdgeKey) -> None:
        """Stage a driver edge or create one link from the staged pair."""
        current = edge_key(*edge)
        self.selected_vertex = None
        self.selected_edge = current
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        first = self.spacing_link_first_edge
        if first is None:
            self.spacing_link_first_edge = current
            self._update_property_panel()
            self.redraw()
            self.status.set(
                f"Spacing links: {current[0]} — {current[1]} is the driver; "
                "select an incident edge."
            )
            return
        if current == first:
            self.spacing_link_first_edge = None
            self._update_property_panel()
            self.redraw()
            self.status.set(
                "Cleared the staged pair. Select a first edge when ready."
            )
            return
        try:
            link = self.model.add_spacing_link(first, current)
        except TopologyError as exc:
            self._show_error("Cannot link edge spacing", exc)
            self._update_property_panel()
            self.redraw()
            return
        self.spacing_link_first_edge = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Linked cell widths at {link.vertex}; {first[0]} — {first[1]} "
            f"drove {current[0]} — {current[1]}."
        )

    def synchronize_selected_spacing_links(self) -> None:
        if self.selected_edge is None:
            return
        if not self.model.spacing_links_for_edge(self.selected_edge):
            self.status.set("The selected edge has no spacing links to synchronize.")
            return
        try:
            affected = self.model.synchronize_spacing_links(self.selected_edge)
        except TopologyError as exc:
            self._show_error("Cannot synchronize spacing links", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Synchronized {len(affected)} spacing-linked edge"
            f"{'s' if len(affected) != 1 else ''} from the selected edge."
        )

    def remove_selected_spacing_link(
        self, first_edge: EdgeKey, second_edge: EdgeKey
    ) -> None:
        try:
            link = self.model.remove_spacing_link(first_edge, second_edge)
        except TopologyError as exc:
            self._show_error("Cannot remove spacing link", exc)
            return
        self.spacing_link_first_edge = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(f"Removed the spacing link at vertex {link.vertex}.")

    def toggle_boundary_mode(self) -> None:
        self.boundary_mode_active = not self.boundary_mode_active
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.vertex_placement_active = False
        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        if self.boundary_mode_active \
                and self.active_boundary_name not in self.model.boundaries:
            self.active_boundary_name = next(iter(self.model.boundaries), None)
        self.boundary_button.configure(
            text="Done boundaries" if self.boundary_mode_active else "Set boundaries"
        )
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        if self.boundary_mode_active:
            self.status.set(
                "Boundary mode: select or add a patch, then click exterior edges."
            )
        else:
            self.status.set("Finished setting boundaries.")

    def toggle_export_mode(self) -> None:
        activating = not self.export_mode_active
        self._clear_split_state()
        self.export_mode_active = activating
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.vertex_placement_active = False
        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.export_button.configure(
            text="Close export" if activating else "Export"
        )
        if activating:
            self._sync_global_values()
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "Configure extrusion and z-face patches, then export."
            if activating else "Closed export settings."
        )

    def _clear_export_mode(self) -> None:
        self.export_mode_active = False
        if hasattr(self, "export_button"):
            self.export_button.configure(text="Export")

    def add_boundary(self) -> None:
        name = self.boundary_name_var.get().strip()
        try:
            boundary = self.model.add_boundary(name)
        except TopologyError as exc:
            self._show_error("Cannot add boundary", exc)
            return
        self.active_boundary_name = boundary.name
        self.boundary_name_var.set("")
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Added boundary {boundary.name!r}; click exterior edges to assign it."
        )

    def remove_active_boundary(self) -> None:
        name = self.active_boundary_name
        if name is None:
            return
        assigned = len(self.model.boundary_edges(name))
        try:
            self.model.remove_boundary(name)
        except TopologyError as exc:
            self._show_error("Cannot remove boundary", exc)
            return
        self.active_boundary_name = next(iter(self.model.boundaries), None)
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Removed boundary {name!r} and unassigned {assigned} edge(s). "
            "Use Undo to restore it."
        )

    def apply_boundary_definition(self) -> None:
        name = self.active_boundary_name
        if name is None or self.boundary_type_var is None:
            return
        kind = self.boundary_type_var.get()
        neighbour = None
        if kind == "cyclic" and self.boundary_neighbour_var is not None:
            neighbour = self.boundary_neighbour_var.get() or None
        try:
            affected = self.model.set_boundary_type(
                name, kind, neighbour_patch=neighbour
            )
        except TopologyError as exc:
            self._show_error("Cannot set boundary type", exc)
            self._update_property_panel()
            return
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        if kind == "cyclic":
            self.status.set(
                f"Paired cyclic boundaries {name!r} and {neighbour!r}."
            )
        else:
            changed = ", ".join(sorted(affected))
            self.status.set(f"Set {name!r} to {kind}; updated {changed}.")

    def _boundary_list_selected(self, _event: tk.Event) -> None:
        if self.boundary_listbox is None:
            return
        selection = self.boundary_listbox.curselection()
        if not selection:
            return
        self.active_boundary_name = str(
            self.boundary_listbox.get(selection[0])
        )
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Active boundary: {self.active_boundary_name!r}."
        )

    def _boundary_type_selected(self, _event: tk.Event) -> None:
        self.apply_boundary_definition()

    def _boundary_neighbour_selected(self, _event: tk.Event) -> None:
        self.apply_boundary_definition()

    def apply_vertex(self) -> None:
        if self.selected_vertex is None or self.vertex_x_var is None \
                or self.vertex_y_var is None:
            return
        try:
            self.model.move_vertex(
                self.selected_vertex,
                float(self.vertex_x_var.get()),
                float(self.vertex_y_var.get()),
            )
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid vertex coordinates", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self.redraw()
        self._sync_property_values()
        self.status.set(f"Moved vertex {self.selected_vertex}.")

    def apply_edge_cells(self) -> None:
        if self.selected_edge is None or self.edge_cells_var is None:
            return
        try:
            cells = _positive_integer(self.edge_cells_var.get(), "Cells")
            affected = self.model.set_edge_cells(self.selected_edge, cells)
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid cell count", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self.redraw()
        self._update_property_panel()
        spacing_affected = set().union(*(
            self.model.spacing_linked_component(current)
            for current in affected
        ))
        spacing_text = (
            f" Synchronized {len(spacing_affected)} spacing-linked edges."
            if len(spacing_affected) > len(affected) else ""
        )
        self.status.set(
            f"Set {cells} cells on {len(affected)} topology-linked edge"
            f"{'s' if len(affected) != 1 else ''}." + spacing_text
        )

    def apply_edge_grading(self, parameter: str) -> None:
        if self.selected_edge is None:
            return
        variables = {
            "cell_ratio": self.edge_cell_ratio_var,
            "total_ratio": self.edge_total_ratio_var,
            "start_width": self.edge_start_width_var,
            "end_width": self.edge_end_width_var,
        }
        variable = variables.get(parameter)
        if variable is None:
            return
        try:
            value = float(variable.get())
            propagate = bool(self.edge_grading_propagate_var.get())
            self.model.set_edge_grading(
                self.selected_edge,
                parameter,
                value,
                propagate=propagate,
            )
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid edge grading", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        if getattr(self, "spacing_link_mode_active", False):
            self._update_property_panel()
        else:
            self._sync_property_values()
        self.redraw()
        labels = {
            "cell_ratio": "cell-to-cell ratio",
            "total_ratio": "total expansion ratio",
            "start_width": "start-cell width",
            "end_width": "end-cell width",
        }
        first, second = self.selected_edge
        affected_count = (
            len(self.model.edge_constraint_component(self.selected_edge))
            if propagate else 1
        )
        anchors = (
            self.model.edge_constraint_component(self.selected_edge)
            if propagate else {self.selected_edge}
        )
        spacing_affected = set().union(*(
            self.model.spacing_linked_component(current)
            for current in anchors
        ))
        spacing_text = (
            f" Synchronized {len(spacing_affected)} spacing-linked edges."
            if len(spacing_affected) > len(anchors) else ""
        )
        self.status.set(
            f"Set {labels[parameter]} for edge {first} → {second}; "
            f"updated {affected_count} linked edge"
            f"{'s' if affected_count != 1 else ''}." + spacing_text
        )

    def _edge_type_selected(self, _event: tk.Event) -> None:
        self.apply_edge_type()

    def apply_edge_type(self) -> None:
        if self.selected_edge is None or self.edge_type_var is None:
            return
        selected = self.selected_edge
        kind = self.edge_type_var.get()
        try:
            self.model.set_edge_type(selected, kind)
        except TopologyError as exc:
            self._show_error("Invalid edge type", exc)
            self._update_property_panel()
            return
        self.selected_control_point_index = None if kind == "line" else 0
        self._commit_edit()
        self.redraw()
        self._update_property_panel()
        self.status.set(f"Set edge {selected[0]} — {selected[1]} to {kind}.")

    def _control_point_selected(self, _event: tk.Event) -> None:
        if self.point_list_index_var is None:
            return
        self.selected_control_point_index = int(
            self.point_list_index_var.get()
        ) - 1
        self._update_property_panel()
        self.redraw()

    def apply_control_point(self) -> None:
        if self.selected_edge is None or self.selected_control_point_index is None \
                or self.point_x_var is None or self.point_y_var is None:
            return
        selected = self.selected_edge
        index = self.selected_control_point_index
        try:
            self.model.set_edge_control_point(
                selected,
                index,
                float(self.point_x_var.get()),
                float(self.point_y_var.get()),
            )
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid interpolation point", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self.redraw()
        self._sync_property_values()
        self.status.set(
            f"Moved interpolation point {index + 1} on edge "
            f"{selected[0]} — {selected[1]}."
        )

    def add_edge_control_point(self) -> None:
        if self.selected_edge is None or self.selected_control_point_index is None:
            return
        try:
            new_index = self.model.add_edge_control_point(
                self.selected_edge, self.selected_control_point_index
            )
        except TopologyError as exc:
            self._show_error("Cannot add interpolation point", exc)
            return
        self.selected_control_point_index = new_index
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        edge_type = self.model.edge_type(self.selected_edge)
        self.status.set(
            f"Added {edge_type} interpolation point {new_index + 1}."
        )

    def remove_edge_control_point(self) -> None:
        if self.selected_edge is None or self.selected_control_point_index is None:
            return
        removed_index = self.selected_control_point_index
        try:
            self.model.remove_edge_control_point(
                self.selected_edge, removed_index
            )
        except TopologyError as exc:
            self._show_error("Cannot remove interpolation point", exc)
            return
        point_count = len(self.model.edge_control_points(self.selected_edge))
        self.selected_control_point_index = min(removed_index, point_count - 1)
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        edge_type = self.model.edge_type(self.selected_edge)
        self.status.set(
            f"Removed {edge_type} interpolation point {removed_index + 1}."
        )

    def reset_edge_control_points(self) -> None:
        if self.selected_edge is None:
            return
        try:
            self.model.reset_edge_control_points(self.selected_edge)
        except TopologyError as exc:
            self._show_error("Cannot reset interpolation points", exc)
            return
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        edge_type = self.model.edge_type(self.selected_edge)
        self.status.set(
            f"Reset {edge_type} points to equidistant chord positions."
        )

    def apply_edge_control_point_count(self) -> None:
        if self.selected_edge is None or self.edge_point_count_var is None:
            return
        selected = self.selected_edge
        try:
            count = _positive_integer(
                self.edge_point_count_var.get(),
                "Interpolation point count",
            )
            self.model.set_edge_control_point_count(selected, count)
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid interpolation point count", exc)
            self._sync_property_values()
            return
        previous_index = self.selected_control_point_index or 0
        self.selected_control_point_index = min(previous_index, count - 1)
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        edge_type = self.model.edge_type(selected)
        self.status.set(
            f"Set {edge_type} edge {selected[0]} — {selected[1]} to "
            f"{count} equidistant interpolation point"
            f"{'s' if count != 1 else ''}."
        )

    def add_geometry_curve(self) -> None:
        visible_width = max(
            self.canvas.winfo_width() / self.pixels_per_unit, 0.5
        )
        half_span = max(0.25, min(1.0, visible_width * 0.2))
        curve = self.model.add_geometry_curve((
            (self.view_x - half_span, self.view_y),
            (self.view_x + half_span, self.view_y),
        ))
        self._select_geometry_curve(curve.id, 0)
        self._ensure_geometry_visible()
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Added reference curve {curve.name!r}; edit or drag its points."
        )

    def import_geometry_curve(self) -> None:
        filename = filedialog.askopenfilename(
            title="Import reference-curve points",
            filetypes=[
                ("Point lists", "*.txt *.dat *.csv"),
                ("All files", "*"),
            ],
        )
        if not filename:
            return
        try:
            points = load_point_pairs(filename)
            curve = self.model.add_geometry_curve(
                points,
                name=self._unique_geometry_curve_name(Path(filename).stem),
                show_points=False,
            )
        except (GeometryImportError, TopologyError) as exc:
            self._show_error("Could not import geometry curve", exc)
            return
        self._select_geometry_curve(curve.id, 0)
        self._ensure_geometry_visible()
        self._commit_edit()
        self.fit_view()
        self._update_property_panel()
        self.status.set(
            f"Imported {len(points)} points as geometry curve {curve.name!r}."
        )

    def replace_geometry_curve_points_from_file(self) -> None:
        curve_id = self.selected_geometry_curve
        if curve_id is None:
            return
        filename = filedialog.askopenfilename(
            title="Replace reference-curve points",
            filetypes=[
                ("Point lists", "*.txt *.dat *.csv"),
                ("All files", "*"),
            ],
        )
        if not filename:
            return
        try:
            points = load_point_pairs(filename)
            self.model.replace_geometry_curve_points(curve_id, points)
            self.model.set_geometry_curve_point_visibility(curve_id, False)
        except (GeometryImportError, TopologyError) as exc:
            self._show_error("Could not replace geometry points", exc)
            return
        self.selected_geometry_point_index = 0
        self._commit_edit()
        self.fit_view()
        self._update_property_panel()
        curve = self.model.geometry_curves[curve_id]
        self.status.set(
            f"Replaced {curve.name!r} with {len(points)} imported points."
        )

    def apply_geometry_curve_name(self) -> None:
        curve_id = self.selected_geometry_curve
        if curve_id is None or self.geometry_name_var is None:
            return
        try:
            self.model.set_geometry_curve_name(
                curve_id, self.geometry_name_var.get()
            )
        except TopologyError as exc:
            self._show_error("Invalid geometry curve name", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Renamed geometry curve to "
            f"{self.model.geometry_curves[curve_id].name!r}."
        )

    def apply_geometry_point_visibility(self) -> None:
        curve_id = self.selected_geometry_curve
        if curve_id is None or self.geometry_show_points_var is None:
            return
        self.model.set_geometry_curve_point_visibility(
            curve_id, bool(self.geometry_show_points_var.get())
        )
        self._commit_edit()
        self.redraw()
        state = "shown" if self.model.geometry_curves[curve_id].show_points \
            else "hidden"
        self.status.set(
            f"Geometry points for "
            f"{self.model.geometry_curves[curve_id].name!r} are {state}."
        )

    def _geometry_point_selected(self, _event: tk.Event) -> None:
        if self.geometry_point_index_var is None:
            return
        self.selected_geometry_point_index = int(
            self.geometry_point_index_var.get()
        ) - 1
        self._update_property_panel()
        self.redraw()

    def apply_geometry_curve_point(self) -> None:
        curve_id = self.selected_geometry_curve
        point_index = self.selected_geometry_point_index
        if curve_id is None or point_index is None \
                or self.geometry_point_x_var is None \
                or self.geometry_point_y_var is None:
            return
        try:
            self.model.set_geometry_curve_point(
                curve_id,
                point_index,
                float(self.geometry_point_x_var.get()),
                float(self.geometry_point_y_var.get()),
            )
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid geometry point", exc)
            self._sync_property_values()
            return
        self._commit_edit()
        self._sync_property_values()
        self.redraw()
        self.status.set(
            f"Moved geometry point {point_index + 1}."
        )

    def add_geometry_curve_point(self) -> None:
        curve_id = self.selected_geometry_curve
        point_index = self.selected_geometry_point_index
        if curve_id is None or point_index is None:
            return
        try:
            new_index = self.model.add_geometry_curve_point(
                curve_id, point_index
            )
        except TopologyError as exc:
            self._show_error("Cannot add geometry point", exc)
            return
        self.selected_geometry_point_index = new_index
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(f"Added geometry point {new_index + 1}.")

    def remove_geometry_curve_point(self) -> None:
        curve_id = self.selected_geometry_curve
        point_index = self.selected_geometry_point_index
        if curve_id is None or point_index is None:
            return
        try:
            self.model.remove_geometry_curve_point(curve_id, point_index)
        except TopologyError as exc:
            self._show_error("Cannot remove geometry point", exc)
            return
        count = len(self.model.geometry_curves[curve_id].points)
        self.selected_geometry_point_index = min(point_index, count - 1)
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(f"Removed geometry point {point_index + 1}.")

    def delete_geometry_curve(self) -> None:
        curve_id = self.selected_geometry_curve
        if curve_id is None:
            return
        curve_name = self.model.geometry_curves[curve_id].name
        self.model.remove_geometry_curve(curve_id)
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_geometry_point = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(f"Deleted geometry curve {curve_name!r}.")

    def _select_geometry_curve(self, curve_id: str, point_index: int) -> None:
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = curve_id
        self.selected_geometry_point_index = point_index

    def _unique_geometry_curve_name(self, requested: str) -> str:
        base = requested.strip() or "curve"
        used = {curve.name for curve in self.model.geometry_curves.values()}
        if base not in used:
            return base
        index = 2
        while f"{base}_{index}" in used:
            index += 1
        return f"{base}_{index}"

    def _ensure_geometry_visible(self) -> None:
        if self.show_geometry_var.get():
            return
        self.show_geometry_var.set(True)
        self.apply_visibility()

    def _clear_projection_state(self) -> None:
        self.projection_stage = None
        self.projection_entity_kind = None
        self.projection_vertex_ids = []
        self.projection_edges = []
        self.projection_curve_ids = []

    def start_projection(self) -> None:
        if not self.model.geometry_curves:
            self.status.set(
                "Add or import at least one reference curve before projecting."
            )
            return
        if not self.show_block_mesh_var.get() \
                or not self.show_geometry_var.get():
            self.show_block_mesh_var.set(True)
            self.show_geometry_var.set(True)
            self.apply_visibility()
        self._clear_split_state()
        self._clear_export_mode()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.vertex_placement_active = False
        self.block_vertex_selection = None
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.projection_fit_var.set(False)
        self.projection_stage = "entities"
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "Projection: select either mesh vertices or mesh edges, then continue."
        )

    def continue_projection(self) -> None:
        if self.projection_stage != "entities":
            return
        if not self.projection_vertex_ids and not self.projection_edges:
            self.status.set("Select at least one mesh vertex or edge first.")
            return
        self.projection_stage = "curves"
        self.projection_curve_ids = []
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "Projection: select one or more reference curves, choose a direction, "
            "then apply."
        )

    def back_projection(self) -> None:
        if self.projection_stage != "curves":
            return
        self.projection_stage = "entities"
        self.projection_curve_ids = []
        self._update_property_panel()
        self.redraw()
        self.status.set("Projection: adjust the selected mesh entities.")

    def cancel_projection(self) -> None:
        if self.projection_stage is None:
            return
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self._update_property_panel()
        self.redraw()
        self.status.set("Cancelled projection selection.")

    def _toggle_projection_target(self, target: tuple[str, object] | None) -> None:
        if self.projection_stage == "entities":
            kind: str | None = None
            identifier: str | EdgeKey | None = None
            if target is not None and target[0] == "vertex":
                kind = "vertex"
                identifier = str(target[1])
            elif target is not None and target[0] == "edge":
                kind = "edge"
                identifier = target[1]  # type: ignore[assignment]
            elif target is not None and target[0] == "control_point":
                kind = "edge"
                identifier = target[1][0]  # type: ignore[index]
            if kind is None or identifier is None:
                self.status.set(
                    "Projection: click a mesh vertex or edge, or press Esc."
                )
                return
            if self.projection_entity_kind not in (None, kind):
                self.status.set(
                    "Vertices and edges cannot be mixed. Deselect all current "
                    "entities before switching type."
                )
                return
            self.projection_entity_kind = kind
            if kind == "vertex":
                self.projection_fit_var.set(False)
            selected: list[str] | list[EdgeKey]
            selected = (
                self.projection_vertex_ids
                if kind == "vertex"
                else self.projection_edges
            )
            if identifier in selected:
                selected.remove(identifier)  # type: ignore[arg-type]
            else:
                selected.append(identifier)  # type: ignore[arg-type]
            if not selected:
                self.projection_entity_kind = None
            self._update_property_panel()
            self.redraw()
            count = len(self.projection_vertex_ids) + len(self.projection_edges)
            self.status.set(f"Projection: selected {count} mesh entities.")
            return

        if self.projection_stage != "curves":
            return
        curve_id: str | None = None
        if target is not None and target[0] == "geometry_curve":
            curve_id = str(target[1])
        elif target is not None and target[0] == "geometry_point":
            curve_id = str(target[1][0])  # type: ignore[index]
        if curve_id is None:
            self.status.set(
                "Projection: click a reference curve, or press Esc to cancel."
            )
            return
        if curve_id in self.projection_curve_ids:
            self.projection_curve_ids.remove(curve_id)
        else:
            self.projection_curve_ids.append(curve_id)
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Projection: selected {len(self.projection_curve_ids)} target curves."
        )

    def apply_projection(self) -> None:
        if self.projection_stage != "curves":
            return
        direction = PROJECTION_DIRECTION_LABELS.get(
            self.projection_direction_var.get()
        )
        if direction is None:
            self.status.set("Choose a valid projection direction.")
            return
        fit = bool(self.projection_fit_var.get())
        fit_tolerance = FIT_RELATIVE_TOLERANCE
        fit_max_points = DEFAULT_FIT_MAX_POINTS
        if fit:
            try:
                tolerance_var = getattr(
                    self, "projection_fit_tolerance_var", None
                )
                maximum_var = getattr(
                    self, "projection_fit_max_points_var", None
                )
                fit_tolerance = float(
                    tolerance_var.get()
                    if tolerance_var is not None
                    else FIT_RELATIVE_TOLERANCE
                )
                fit_max_points = int(
                    maximum_var.get()
                    if maximum_var is not None
                    else DEFAULT_FIT_MAX_POINTS
                )
                if not math.isfinite(fit_tolerance) or fit_tolerance <= 0.0:
                    raise ValueError(
                        "Relative fit tolerance must be a positive number"
                    )
                if fit_max_points < 1:
                    raise ValueError(
                        "Maximum spline points must be a positive integer"
                    )
            except (TypeError, ValueError) as exc:
                self._show_error("Invalid spline fit settings", exc)
                return
        try:
            result = self.model.project_to_geometry(
                self.projection_curve_ids,
                direction,
                vertex_ids=self.projection_vertex_ids,
                edges=self.projection_edges,
                fit=fit,
                fit_relative_tolerance=fit_tolerance,
                fit_max_points=fit_max_points,
            )
        except TopologyError as exc:
            self._show_error("Cannot project selected entities", exc)
            return
        self._clear_projection_state()
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        converted = (
            f" Converted {len(result.converted_arcs)} arc"
            f"{'s' if len(result.converted_arcs) != 1 else ''} to spline."
            if result.converted_arcs else ""
        )
        fitted = (
            f" Fitted {len(result.fitted_edges)} edge"
            f"{'s' if len(result.fitted_edges) != 1 else ''} with "
            f"{result.fit_interpolation_point_count} spline point"
            f"{'s' if result.fit_interpolation_point_count != 1 else ''} "
            f"(maximum measured distance {result.max_fit_error:.6g}; "
            f"target {result.fit_tolerance:.6g}"
            f"{' met' if result.fit_tolerance_met else '; target not met'})."
            if result.fitted_edges and result.max_fit_error is not None else ""
        )
        self.status.set(
            f"Projected {result.projected_point_count} mesh point"
            f"{'s' if result.projected_point_count != 1 else ''} "
            f"{direction}." + converted + fitted
        )

    def start_edge_split(self) -> None:
        if self.selected_edge is None \
                or self.selected_edge not in self.model.edge_cells:
            self.status.set("Select a mesh edge before starting a split.")
            return
        self._clear_export_mode()
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.vertex_placement_active = False
        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.drag_split_marker = False
        self.split_edge_active = self.selected_edge
        cells = self.model.edge_cells[self.split_edge_active]
        self.split_fraction = (
            0.5
            if cells == 1
            else self.model.edge_node_fraction(
                self.split_edge_active, max(1, cells // 2)
            )
        )
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "Split mode: position the marker, then press Enter or use Execute split."
        )

    def cancel_edge_split(self) -> None:
        if self.split_edge_active is None:
            return
        self._clear_split_state()
        self._update_property_panel()
        self.redraw()
        self.status.set("Cancelled edge split.")

    def _clear_split_state(self) -> None:
        self.split_edge_active = None
        self.drag_split_marker = False
        self.split_fraction_var = None
        self.split_cells_var = None

    def _update_split_marker_from_pointer(self, event: tk.Event) -> None:
        if self.split_edge_active is None:
            return
        x, y = self.screen_to_world(event.x, event.y)
        fraction = _nearest_edge_fraction(
            self.model, self.split_edge_active, x, y
        )
        self.split_fraction = min(
            1.0 - MIN_SPLIT_FRACTION,
            max(MIN_SPLIT_FRACTION, fraction),
        )
        self._sync_split_panel_value()
        self.redraw()

    def _finish_edge_split(self) -> None:
        if self.split_edge_active is None:
            return
        selected = self.split_edge_active
        try:
            result = self.model.split_edge(selected, self.split_fraction)
        except TopologyError as exc:
            self._show_error("Cannot split edge", exc)
            self.redraw()
            return
        self._clear_split_state()
        self.selected_vertex = None
        self.selected_edge = result.cut_edges[0] if result.cut_edges else None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Split {len(result.new_block_ids)} block"
            f"{'s' if len(result.new_block_ids) != 1 else ''} at "
            f"{_display_split_percentage(result.fraction)}% into "
            f"{result.first_cells} + {result.second_cells} cells."
        )

    def execute_edge_split(self) -> None:
        """Apply the split percentage currently shown in the properties panel."""
        if self.split_edge_active is None:
            return
        if self.split_fraction_var is not None:
            try:
                self.split_fraction = _split_fraction_from_text(
                    self.split_fraction_var.get()
                )
            except ValueError as exc:
                self._show_error("Cannot split edge", exc)
                return
        self._finish_edge_split()

    def combine_selected_blocks(self) -> None:
        """Combine the conformal block pairs across the selected internal cut."""
        if self.split_edge_active is not None \
                or self.export_mode_active \
                or self.boundary_mode_active \
                or getattr(self, "spacing_link_mode_active", False) \
                or self.projection_stage is not None:
            self.status.set("Finish the active editing mode before combining blocks.")
            return
        if self.selected_edge is None:
            self.status.set("Select an internal edge before combining blocks.")
            return
        selected = self.selected_edge
        try:
            result = self.model.combine_blocks(selected)
        except TopologyError as exc:
            self._show_error("Cannot combine blocks", exc)
            return
        self.selected_vertex = None
        self.selected_edge = (
            result.merged_edges[0] if result.merged_edges else None
        )
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Combined {len(result.removed_block_ids)} block pair"
            f"{'s' if len(result.removed_block_ids) != 1 else ''}; removed "
            f"{len(result.removed_edges)} internal edge"
            f"{'s' if len(result.removed_edges) != 1 else ''}."
        )

    def add_selected_block(self) -> None:
        if self.selected_edge is None:
            self.status.set("Select an exterior edge before adding a block.")
            return
        try:
            block = self.model.add_block(self.selected_edge)
        except TopologyError as exc:
            self._show_error("Cannot add block", exc)
            return
        self.selected_vertex = None
        self.selected_edge = edge_key(*block.directed_edge(2))
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self.redraw()
        self._update_property_panel()
        self.status.set(
            f"Added {block.id}. Its outer edge is selected for quick extension."
        )

    def start_block_from_vertices(self) -> None:
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.vertex_placement_active = False
        self.block_vertex_selection = []
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "New block mode: select four existing vertices; press Esc to cancel."
        )

    def start_vertex_placement(self) -> None:
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self._clear_spacing_link_mode()
        self.boundary_mode_active = False
        self.boundary_button.configure(text="Set boundaries")
        self.vertex_placement_active = True
        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "Vertex placement: click an empty canvas location; press Esc to cancel."
        )

    def cancel_vertex_placement(self) -> None:
        if not self.vertex_placement_active:
            return
        self.vertex_placement_active = False
        self._update_property_panel()
        self.redraw()
        self.status.set("Cancelled standalone vertex placement.")

    def cancel_block_from_vertices(self) -> None:
        if self.block_vertex_selection is None:
            return
        self.block_vertex_selection = None
        self._update_property_panel()
        self.redraw()
        self.status.set("Cancelled new block vertex selection.")

    def _toggle_block_vertex(self, vertex_id: str) -> None:
        if self.block_vertex_selection is None:
            return
        if vertex_id in self.block_vertex_selection:
            self.block_vertex_selection.remove(vertex_id)
        elif len(self.block_vertex_selection) < 4:
            self.block_vertex_selection.append(vertex_id)
        else:
            self.status.set(
                "Four vertices are staged; deselect one or press Esc to restart."
            )
            return

        self._update_property_panel()
        self.redraw()
        if len(self.block_vertex_selection) < 4:
            self.status.set(
                f"Selected {len(self.block_vertex_selection)} of 4 vertices."
            )
            return

        try:
            block = self.model.add_block_from_vertices(
                self.block_vertex_selection
            )
        except TopologyError as exc:
            self.status.set(
                f"Cannot create block: {exc}. Deselect a vertex or press Esc."
            )
            return

        self.block_vertex_selection = None
        self.selected_vertex = None
        self.selected_edge = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(f"Added {block.id} from four existing vertices.")

    def delete_selected_edge(self) -> None:
        if self.selected_edge is None:
            self.status.set("Select an edge before deleting it.")
            return
        selected = self.selected_edge
        if not self.model.can_remove_edge(selected):
            self.status.set("Cannot delete this edge: at least one block must remain.")
            return
        try:
            removed = self.model.remove_edge(selected)
        except TopologyError as exc:
            self._show_error("Cannot delete edge", exc)
            return
        self.selected_edge = None
        self.selected_vertex = None
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Deleted edge {selected[0]} — {selected[1]} and "
            f"{len(removed)} incident block{'s' if len(removed) != 1 else ''}."
        )

    def delete_selected_entity(self) -> None:
        if self.selected_geometry_curve is not None:
            self.delete_geometry_curve()
            return
        self.delete_selected_edge()
