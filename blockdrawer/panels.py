"""Properties-sidebar construction for the BlockDrawer Tk interface."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .model import MeshModel, edge_key
from .ui_helpers import (
    PROJECTION_DIRECTION_LABELS,
    display_grading_number as _display_grading_number,
    display_number as _display_number,
    display_split_percentage as _display_split_percentage,
)


class PropertiesPanelMixin:
    """Build and refresh contextual controls in the right-hand sidebar."""

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        on_confirm: Callable[[], None] | None = None,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        entry = ttk.Entry(parent, textvariable=variable, width=16)
        entry.grid(row=row, column=1, sticky="ew", pady=self._px(3))
        if on_confirm is not None:
            self._bind_entry_confirmation(entry, on_confirm)
        return entry

    def _bind_entry_confirmation(
        self,
        entry: ttk.Entry,
        command: Callable[[], None],
    ) -> None:
        def confirm(_event: tk.Event) -> str:
            command()
            return "break"

        entry.bind("<Return>", confirm)
        entry.bind("<KP_Enter>", confirm)

    def _grading_field(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        parameter: str,
    ) -> None:
        ttk.Label(self.selection_frame, text=label).grid(
            row=row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        controls = ttk.Frame(self.selection_frame)
        controls.grid(row=row, column=1, sticky="ew", pady=self._px(3))
        controls.columnconfigure(0, weight=1)
        entry = ttk.Entry(controls, textvariable=variable, width=11)
        entry.grid(row=0, column=0, sticky="ew")
        self._bind_entry_confirmation(
            entry,
            lambda source=parameter: self.apply_edge_grading(source),
        )
        ttk.Button(
            controls,
            text="Set",
            width=4,
            command=lambda source=parameter: self.apply_edge_grading(source),
        ).grid(row=0, column=1, padx=(self._px(4), 0))

    def _build_edge_grading_controls(
        self,
        current: tuple[str, str],
        row: int,
        *,
        heading_padding: int,
        include_help: bool,
    ) -> int:
        """Build the shared directional-grading editor and return its next row."""
        first, second = current
        affected = self.model.edge_constraint_component(current)
        grading = self.model.edge_grading_values(current)
        ttk.Label(
            self.selection_frame,
            text=f"Grading {first} → {second}",
            font=self._font(10, "bold"),
        ).grid(
            row=row, column=0, columnspan=2, sticky="w",
            pady=(self._px(heading_padding), self._px(3)),
        )
        row += 1
        ttk.Checkbutton(
            self.selection_frame,
            text=(
                f"Propagate to {len(affected)} cell-count-linked edge"
                f"{'s' if len(affected) != 1 else ''}"
            ),
            variable=self.edge_grading_propagate_var,
        ).grid(
            row=row, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(4)),
        )
        row += 1
        ttk.Label(self.selection_frame, text="Edge length").grid(
            row=row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        self.edge_length_var = tk.StringVar(
            value=_display_grading_number(grading.length)
        )
        ttk.Label(
            self.selection_frame,
            textvariable=self.edge_length_var,
            anchor="e",
        ).grid(row=row, column=1, sticky="ew", pady=self._px(3))
        row += 1
        self.edge_cell_ratio_var = tk.StringVar(
            value=_display_grading_number(grading.cell_ratio)
        )
        self.edge_total_ratio_var = tk.StringVar(
            value=_display_grading_number(grading.total_ratio)
        )
        self.edge_start_width_var = tk.StringVar(
            value=_display_grading_number(grading.start_width)
        )
        self.edge_end_width_var = tk.StringVar(
            value=_display_grading_number(grading.end_width)
        )
        for label, variable, parameter in (
            ("Cell/cell ratio", self.edge_cell_ratio_var, "cell_ratio"),
            ("Total ratio", self.edge_total_ratio_var, "total_ratio"),
            ("Start width", self.edge_start_width_var, "start_width"),
            ("End width", self.edge_end_width_var, "end_width"),
        ):
            self._grading_field(row, label, variable, parameter)
            row += 1
        if include_help:
            ttk.Label(
                self.selection_frame,
                text=(
                    "Set any one value; the other three are recomputed. "
                    "Total ratio is end width / start width in the arrow "
                    "direction. Propagation preserves physical direction "
                    "when linked edge arrows are reversed."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=row, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), self._px(2)),
            )
            row += 1
        return row

    def _update_property_panel(self) -> None:
        for child in self.selection_frame.winfo_children():
            child.destroy()
        self.vertex_x_var = None
        self.vertex_y_var = None
        self.edge_cells_var = None
        self.edge_type_var = None
        self.edge_length_var = None
        self.edge_cell_ratio_var = None
        self.edge_total_ratio_var = None
        self.edge_start_width_var = None
        self.edge_end_width_var = None
        self.point_x_var = None
        self.point_y_var = None
        self.point_list_index_var = None
        self.edge_point_count_var = None
        self.split_fraction_var = None
        self.split_cells_var = None
        self.geometry_name_var = None
        self.geometry_point_x_var = None
        self.geometry_point_y_var = None
        self.geometry_point_index_var = None
        self.geometry_show_points_var = None

        if getattr(self, "export_mode_active", False):
            self.sidebar_title.configure(text="Export")
            self.selection_frame.configure(text="blockMeshDict export")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "Configure extrusion and the automatic z-face patches.\n"
                    "Settings take effect when the dictionary is exported.\n"
                    "E or Esc: close export"
                )
            )
            self._build_export_panel()
            self.add_button.configure(state="disabled")
            self.edit_menu.entryconfigure(
                self.delete_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.split_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.combine_blocks_menu_index, state="disabled"
            )
            return

        if getattr(self, "projection_stage", None) is not None:
            self.sidebar_title.configure(text="Projection")
            self.selection_frame.configure(text="Project onto geometry")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "P: restart projection selection\n"
                    "Click a selected item again to deselect it.\n"
                    "Esc: cancel projection"
                )
            )
            self._build_projection_panel()
            self.add_button.configure(state="disabled")
            self.edit_menu.entryconfigure(
                self.delete_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.split_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.combine_blocks_menu_index, state="disabled"
            )
            return

        if self.boundary_mode_active:
            self.sidebar_title.configure(text="Boundaries")
            self.selection_frame.configure(text="Boundary patches")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "Click an exterior edge to assign it to the selected patch.\n"
                    "Click it again to unassign it.\nB or Esc: finish boundaries"
                )
            )
            self._build_boundary_panel()
            self.add_button.configure(state="disabled")
            self.edit_menu.entryconfigure(
                self.delete_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.split_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.combine_blocks_menu_index, state="disabled"
            )
            return

        if getattr(self, "spacing_link_mode_active", False):
            self.sidebar_title.configure(text="Spacing links")
            self.selection_frame.configure(text="Cell spacing")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "Select two incident edges to link their cell widths.\n"
                    "Cell-count and grading edits synchronize link chains.\n"
                    "L or Esc: finish spacing links"
                )
            )
            self._build_spacing_link_panel()
            self.add_button.configure(state="disabled")
            self.edit_menu.entryconfigure(
                self.delete_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.split_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.combine_blocks_menu_index, state="disabled"
            )
            return

        if self.split_edge_active is not None:
            self.sidebar_title.configure(text="Split block")
            self.selection_frame.configure(text="Conformal edge split")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "Click or drag on the selected edge to place the split.\n"
                    "Reposition it as often as needed, then press Enter or "
                    "use Execute split.\n"
                    "Esc: cancel split"
                )
            )
            self._build_split_panel()
            self.add_button.configure(state="disabled")
            self.edit_menu.entryconfigure(
                self.delete_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.split_edge_menu_index, state="disabled"
            )
            self.edit_menu.entryconfigure(
                self.combine_blocks_menu_index, state="disabled"
            )
            return

        if self.show_mesh_preview_var.get():
            self.sidebar_title.configure(text="Mesh preview")
            self.selection_frame.configure(text="Visualization mesh")
            self.selection_frame.grid_configure(row=1, pady=0)
            self.sidebar_help.configure(
                text=(
                    "The preview is visual only and is never exported.\n"
                    "Pan and zoom normally while it is visible.\n"
                    "M: hide mesh preview"
                )
            )
            self._build_mesh_preview_panel()
            self._configure_selection_edit_controls()
            return

        self.sidebar_title.configure(text="Properties")
        self.selection_frame.configure(text="Selection")
        self.selection_frame.grid_configure(row=1, pady=0)
        self.sidebar_help.configure(
            text=(
                "Mouse wheel: zoom\nMiddle/right drag: pan\n"
                "Double-click an exterior edge: add block\n"
                "V: place vertex · N: connect 4 vertices\n"
                "S: split edge · Shift+S: combine\n"
                "B: boundaries · L: link spacing · P: project\n"
                "M: preview · E: export · Esc: cancel"
            )
        )

        if self.vertex_placement_active:
            ttk.Label(
                self.selection_frame,
                text="Add standalone vertex",
                font=self._font(11, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                self.selection_frame,
                text=(
                    "Click an empty canvas location. The new vertex can be "
                    "moved normally and selected with N for a new block."
                ),
                foreground="#7048a8",
                wraplength=self._px(245),
            ).grid(
                row=1, column=0, columnspan=2, sticky="w",
                pady=(self._px(6), self._px(8)),
            )
            ttk.Button(
                self.selection_frame,
                text="Cancel vertex placement (Esc)",
                command=self.cancel_vertex_placement,
            ).grid(row=2, column=0, columnspan=2, sticky="ew")
        elif self.block_vertex_selection is not None:
            count = len(self.block_vertex_selection)
            ttk.Label(
                self.selection_frame,
                text="New block from vertices",
                font=self._font(11, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                self.selection_frame,
                text=(
                    "Click four existing vertices in any order. Click a staged "
                    "vertex again to deselect it."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=1, column=0, columnspan=2, sticky="w",
                pady=(self._px(6), self._px(8)),
            )
            ttk.Label(
                self.selection_frame,
                text=f"Selected: {count} / 4",
                font=self._font(10, "bold"),
            ).grid(row=2, column=0, columnspan=2, sticky="w")
            selected_text = (
                "\n".join(
                    f"{index}. {identifier}"
                    for index, identifier in enumerate(
                        self.block_vertex_selection, start=1
                    )
                )
                or "No vertices selected yet."
            )
            ttk.Label(
                self.selection_frame,
                text=selected_text,
                foreground="#7048a8",
                justify="left",
            ).grid(
                row=3, column=0, columnspan=2, sticky="w",
                pady=(self._px(6), self._px(8)),
            )
            ttk.Button(
                self.selection_frame,
                text="Cancel vertex selection (Esc)",
                command=self.cancel_block_from_vertices,
            ).grid(row=4, column=0, columnspan=2, sticky="ew")
        elif self.selected_geometry_curve is not None:
            self._build_geometry_curve_panel()
        elif self.selected_vertex is not None:
            vertex = self.model.vertices[self.selected_vertex]
            ttk.Label(
                self.selection_frame,
                text=f"Vertex {vertex.id}",
                font=self._font(11, "bold"),
            ).grid(
                row=0, column=0, columnspan=2, sticky="w",
                pady=(0, self._px(7)),
            )
            self.vertex_x_var = tk.StringVar(value=_display_number(vertex.x))
            self.vertex_y_var = tk.StringVar(value=_display_number(vertex.y))
            self._field(
                self.selection_frame, 1, "X", self.vertex_x_var,
                on_confirm=self.apply_vertex,
            )
            self._field(
                self.selection_frame, 2, "Y", self.vertex_y_var,
                on_confirm=self.apply_vertex,
            )
            ttk.Button(
                self.selection_frame,
                text="Apply coordinates",
                command=self.apply_vertex,
            ).grid(
                row=3, column=0, columnspan=2, sticky="ew",
                pady=(self._px(8), 0),
            )
        elif self.selected_edge is not None:
            first, second = self.selected_edge
            cells = self.model.edge_cells[self.selected_edge]
            edge_type = self.model.edge_type(self.selected_edge)
            control_points = self.model.edge_control_points(self.selected_edge)
            if control_points:
                if self.selected_control_point_index is None \
                        or self.selected_control_point_index >= len(control_points):
                    self.selected_control_point_index = 0
            else:
                self.selected_control_point_index = None
            affected = self.model.edge_constraint_component(self.selected_edge)
            boundary = self.model.is_boundary_edge(self.selected_edge)
            ttk.Label(
                self.selection_frame,
                text=f"Edge {first} — {second}",
                font=self._font(11, "bold"),
            ).grid(
                row=0, column=0, columnspan=2, sticky="w",
                pady=(0, self._px(7)),
            )
            self.edge_cells_var = tk.StringVar(value=str(cells))
            self._field(
                self.selection_frame, 1, "Cells", self.edge_cells_var,
                on_confirm=self.apply_edge_cells,
            )
            ttk.Label(self.selection_frame, text="Type").grid(
                row=2, column=0, sticky="w",
                padx=(0, self._px(8)), pady=self._px(3),
            )
            self.edge_type_var = tk.StringVar(value=edge_type)
            type_selector = ttk.Combobox(
                self.selection_frame,
                textvariable=self.edge_type_var,
                values=MeshModel.SUPPORTED_EDGE_TYPES,
                state="readonly",
                width=14,
            )
            type_selector.grid(row=2, column=1, sticky="ew", pady=self._px(3))
            type_selector.bind("<<ComboboxSelected>>", self._edge_type_selected)
            ttk.Label(
                self.selection_frame,
                text=(
                    f"Changing this value updates {len(affected)} linked edge"
                    f"{'s' if len(affected) != 1 else ''}. The canvas shows "
                    "the current graded mesh-node positions."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=3, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), self._px(8)),
            )
            ttk.Button(
                self.selection_frame,
                text="Apply cell count",
                command=self.apply_edge_cells,
            ).grid(row=4, column=0, columnspan=2, sticky="ew")

            next_row = self._build_edge_grading_controls(
                self.selected_edge,
                5,
                heading_padding=10,
                include_help=True,
            )

            if control_points and self.selected_control_point_index is not None:
                point_index = self.selected_control_point_index
                point_x, point_y = control_points[point_index]
                ttk.Label(
                    self.selection_frame,
                    text=(
                        "Arc interpolation point"
                        if edge_type == "arc"
                        else f"{edge_type} interpolation points"
                    ),
                    font=self._font(10, "bold"),
                ).grid(
                    row=next_row, column=0, columnspan=2, sticky="w",
                    pady=(self._px(10), self._px(3)),
                )
                next_row += 1
                if edge_type in MeshModel.MULTI_POINT_EDGE_TYPES:
                    ttk.Label(
                        self.selection_frame, text="Point count"
                    ).grid(
                        row=next_row, column=0, sticky="w",
                        padx=(0, self._px(8)), pady=self._px(3),
                    )
                    point_count_controls = ttk.Frame(self.selection_frame)
                    point_count_controls.grid(
                        row=next_row, column=1, sticky="ew", pady=self._px(3)
                    )
                    point_count_controls.columnconfigure(0, weight=1)
                    self.edge_point_count_var = tk.StringVar(
                        value=str(len(control_points))
                    )
                    point_count_entry = ttk.Entry(
                        point_count_controls,
                        textvariable=self.edge_point_count_var,
                        width=11,
                    )
                    point_count_entry.grid(row=0, column=0, sticky="ew")
                    self._bind_entry_confirmation(
                        point_count_entry,
                        self.apply_edge_control_point_count,
                    )
                    ttk.Button(
                        point_count_controls,
                        text="Set",
                        width=4,
                        command=self.apply_edge_control_point_count,
                    ).grid(row=0, column=1, padx=(self._px(4), 0))
                    next_row += 1
                    ttk.Label(self.selection_frame, text="Selected point").grid(
                        row=next_row, column=0, sticky="w",
                        padx=(0, self._px(8)), pady=self._px(3),
                    )
                    self.point_list_index_var = tk.StringVar(
                        value=str(point_index + 1)
                    )
                    point_selector = ttk.Combobox(
                        self.selection_frame,
                        textvariable=self.point_list_index_var,
                        values=tuple(
                            str(index + 1) for index in range(len(control_points))
                        ),
                        state="readonly",
                        width=14,
                    )
                    point_selector.grid(
                        row=next_row, column=1, sticky="ew", pady=self._px(3)
                    )
                    point_selector.bind(
                        "<<ComboboxSelected>>", self._control_point_selected
                    )
                    next_row += 1

                self.point_x_var = tk.StringVar(value=_display_number(point_x))
                self.point_y_var = tk.StringVar(value=_display_number(point_y))
                self._field(
                    self.selection_frame, next_row, "Point X", self.point_x_var,
                    on_confirm=self.apply_control_point,
                )
                self._field(
                    self.selection_frame, next_row + 1, "Point Y", self.point_y_var,
                    on_confirm=self.apply_control_point,
                )
                ttk.Button(
                    self.selection_frame,
                    text="Apply point coordinates",
                    command=self.apply_control_point,
                ).grid(
                    row=next_row + 2, column=0, columnspan=2, sticky="ew",
                    pady=(self._px(6), 0),
                )
                next_row += 3

                if edge_type in MeshModel.MULTI_POINT_EDGE_TYPES:
                    point_actions = ttk.Frame(self.selection_frame)
                    point_actions.grid(
                        row=next_row, column=0, columnspan=2, sticky="ew",
                        pady=(self._px(6), 0),
                    )
                    for column in range(3):
                        point_actions.columnconfigure(column, weight=1)
                    ttk.Button(
                        point_actions,
                        text="Add",
                        command=self.add_edge_control_point,
                    ).grid(
                        row=0, column=0, sticky="ew",
                        padx=(0, self._px(2)),
                    )
                    ttk.Button(
                        point_actions,
                        text="Remove",
                        command=self.remove_edge_control_point,
                        state="normal" if len(control_points) > 1 else "disabled",
                    ).grid(
                        row=0, column=1, sticky="ew",
                        padx=self._px(2),
                    )
                    ttk.Button(
                        point_actions,
                        text="Reset",
                        command=self.reset_edge_control_points,
                    ).grid(
                        row=0, column=2, sticky="ew",
                        padx=(self._px(2), 0),
                    )
                    next_row += 1

                ttk.Label(
                    self.selection_frame,
                    text=(
                        "Purple points are numbered in path order and can be "
                        "dragged on the canvas."
                        if edge_type in MeshModel.MULTI_POINT_EDGE_TYPES
                        else "The purple point can also be dragged on the canvas."
                    ),
                    foreground="#7048a8",
                    wraplength=self._px(245),
                ).grid(
                    row=next_row, column=0, columnspan=2, sticky="w",
                    pady=(self._px(5), 0),
                )
                next_row += 1

            if boundary:
                ttk.Button(
                    self.selection_frame,
                    text="Add block on this side",
                    command=self.add_selected_block,
                ).grid(
                    row=next_row, column=0, columnspan=2, sticky="ew",
                    pady=(self._px(7), 0),
                )
            else:
                ttk.Label(
                    self.selection_frame,
                    text="Internal edge — already shared by two blocks",
                    foreground="#52606d",
                    wraplength=self._px(245),
                ).grid(
                    row=next_row, column=0, columnspan=2, sticky="w",
                    pady=(self._px(7), 0),
                )
            incident_count = len(
                self.model.edge_occurrences()[self.selected_edge]
            )
            can_delete = self.model.can_remove_edge(self.selected_edge)
            ttk.Button(
                self.selection_frame,
                text=(
                    f"Delete edge and {incident_count} block"
                    f"{'s' if incident_count != 1 else ''}"
                    if can_delete
                    else "Cannot delete the final block"
                ),
                command=self.delete_selected_edge,
                state="normal" if can_delete else "disabled",
            ).grid(
                row=next_row + 1, column=0, columnspan=2, sticky="ew",
                pady=(self._px(9), 0),
            )
        else:
            ttk.Label(
                self.selection_frame,
                text="Nothing selected",
                font=self._font(11, "bold"),
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                self.selection_frame,
                text="Click a mesh vertex, edge, or geometry curve.",
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(row=1, column=0, sticky="w", pady=(self._px(5), 0))

        self._configure_selection_edit_controls()

    def _build_spacing_link_panel(self) -> None:
        """Build the grading-only edge panel used while linking spacing."""
        staged = self.spacing_link_first_edge
        if self.selected_edge is None:
            ttk.Label(
                self.selection_frame,
                text="Select a driver edge",
                font=self._font(11, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                self.selection_frame,
                text=(
                    "Then select a second edge sharing one vertex. The second "
                    "edge is regraded to match the driver's cell width."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=1, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), 0),
            )
            return

        current = self.selected_edge
        first, second = current
        cells = self.model.edge_cells[current]
        affected = self.model.edge_constraint_component(current)
        ttk.Label(
            self.selection_frame,
            text=f"Edge {first} — {second}",
            font=self._font(11, "bold"),
        ).grid(
            row=0, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(4)),
        )
        stage_text = (
            "Driver selected—click the second incident edge."
            if staged == current
            else (
                f"Driver: {staged[0]} — {staged[1]}"
                if staged is not None
                else "Click an edge to begin another pair."
            )
        )
        ttk.Label(
            self.selection_frame,
            text=stage_text,
            foreground="#7048a8" if staged is not None else "#52606d",
            wraplength=self._px(245),
        ).grid(
            row=1, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(7)),
        )

        self.edge_cells_var = tk.StringVar(value=str(cells))
        self._field(
            self.selection_frame,
            2,
            "Cells",
            self.edge_cells_var,
            on_confirm=self.apply_edge_cells,
        )
        ttk.Button(
            self.selection_frame,
            text="Apply cell count",
            command=self.apply_edge_cells,
        ).grid(
            row=3, column=0, columnspan=2, sticky="ew",
            pady=(self._px(5), 0),
        )
        ttk.Label(
            self.selection_frame,
            text=(
                f"Cell count also updates {len(affected)} opposite-edge "
                "topology constraint"
                f"{'s' if len(affected) != 1 else ''}."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=4, column=0, columnspan=2, sticky="w",
            pady=(self._px(5), self._px(7)),
        )

        next_row = self._build_edge_grading_controls(
            current,
            5,
            heading_padding=4,
            include_help=False,
        )

        links = self.model.spacing_links_for_edge(current)
        ttk.Separator(self.selection_frame).grid(
            row=next_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(9), self._px(7)),
        )
        next_row += 1
        ttk.Label(
            self.selection_frame,
            text=f"Endpoint links ({len(links)})",
            font=self._font(10, "bold"),
        ).grid(row=next_row, column=0, columnspan=2, sticky="w")
        next_row += 1
        if not links:
            ttk.Label(
                self.selection_frame,
                text="This edge is not linked at either endpoint.",
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=next_row, column=0, columnspan=2, sticky="w",
                pady=(self._px(4), 0),
            )
            next_row += 1
        for link in links:
            other = (
                link.second_edge
                if current == link.first_edge else link.first_edge
            )
            width = self.model.edge_width_at_vertex(current, link.vertex)
            other_width = self.model.edge_width_at_vertex(other, link.vertex)
            synchronized = self.model.spacing_link_is_synchronized(link)
            ttk.Label(
                self.selection_frame,
                text=(
                    f"At {link.vertex}: {other[0]} — {other[1]}\n"
                    f"Widths {_display_grading_number(width)} / "
                    f"{_display_grading_number(other_width)} "
                    f"({'matched' if synchronized else 'out of sync'})"
                ),
                foreground="#0b726c" if synchronized else "#b45309",
                wraplength=self._px(245),
            ).grid(
                row=next_row, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), self._px(3)),
            )
            next_row += 1
            ttk.Button(
                self.selection_frame,
                text=f"Remove link at {link.vertex}",
                command=lambda first_edge=link.first_edge,
                second_edge=link.second_edge: self.remove_selected_spacing_link(
                    first_edge, second_edge
                ),
            ).grid(
                row=next_row, column=0, columnspan=2, sticky="ew"
            )
            next_row += 1

        ttk.Button(
            self.selection_frame,
            text="Synchronize links from this edge",
            command=self.synchronize_selected_spacing_links,
            state="normal" if links else "disabled",
        ).grid(
            row=next_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(9), 0),
        )

    def _configure_selection_edit_controls(self) -> None:
        can_add = self.selected_edge is not None \
            and self.model.is_boundary_edge(self.selected_edge)
        self.add_button.configure(state="normal" if can_add else "disabled")
        delete_state = (
            "normal"
            if self.selected_geometry_curve is not None
            or (
                self.selected_edge is not None
                and self.model.can_remove_edge(self.selected_edge)
            )
            else "disabled"
        )
        self.edit_menu.entryconfigure(
            self.delete_edge_menu_index, state=delete_state
        )
        self.edit_menu.entryconfigure(
            self.split_edge_menu_index,
            state="normal" if self.selected_edge is not None else "disabled",
        )
        self.edit_menu.entryconfigure(
            self.combine_blocks_menu_index,
            state=(
                "normal"
                if self.selected_edge is not None
                and self.model.can_combine_edge(self.selected_edge)
                else "disabled"
            ),
        )

    def _build_mesh_preview_panel(self) -> None:
        ttk.Label(
            self.selection_frame,
            text="Structured mesh visualization",
            font=self._font(11, "bold"),
        ).grid(
            row=0, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(7)),
        )
        ttk.Label(
            self.selection_frame,
            textvariable=self.mesh_preview_info_var,
            foreground="#42637a",
            wraplength=self._px(245),
        ).grid(
            row=1, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(8)),
        )
        self._field(
            self.selection_frame,
            2,
            "Coarsening factor",
            self.mesh_preview_coarsening_var,
            on_confirm=self.apply_mesh_preview_coarsening,
        )
        ttk.Button(
            self.selection_frame,
            text="Apply coarsening",
            command=self.apply_mesh_preview_coarsening,
        ).grid(
            row=3, column=0, columnspan=2, sticky="ew",
            pady=(self._px(7), 0),
        )
        ttk.Label(
            self.selection_frame,
            text=(
                "A factor of 1 uses every edge subdivision. A factor of 10 "
                "uses every tenth subdivision and always retains block "
                "corners. Curved and graded boundary locations are respected."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=4, column=0, columnspan=2, sticky="w",
            pady=(self._px(8), 0),
        )

    def _build_split_panel(self) -> None:
        assert self.split_edge_active is not None
        first, second = self.split_edge_active
        affected = self.model.edge_constraint_component(
            self.split_edge_active
        )
        affected_blocks = sum(
            any(
                edge_key(*block.directed_edge(index)) in affected
                for index in range(4)
            )
            for block in self.model.blocks
        )
        ttk.Label(
            self.selection_frame,
            text=f"Edge {first} — {second}",
            font=self._font(11, "bold"),
            foreground="#7b2cbf",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self.selection_frame,
            text=(
                f"The cut propagates across {affected_blocks} block"
                f"{'s' if affected_blocks != 1 else ''} and splits "
                f"{len(affected)} aligned edge"
                f"{'s' if len(affected) != 1 else ''}."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=1, column=0, columnspan=2, sticky="w",
            pady=(self._px(6), self._px(10)),
        )
        self.split_fraction_var = tk.StringVar()
        self._field(
            self.selection_frame,
            2,
            "Current split (%)",
            self.split_fraction_var,
            on_confirm=self.execute_edge_split,
        )
        self.split_cells_var = tk.StringVar()
        ttk.Label(self.selection_frame, text="Cell allocation").grid(
            row=3, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        ttk.Label(
            self.selection_frame,
            textvariable=self.split_cells_var,
            font=self._font(10, "bold"),
            foreground="#7b2cbf",
        ).grid(row=3, column=1, sticky="w", pady=self._px(3))
        self._sync_split_panel_value()
        ttk.Label(
            self.selection_frame,
            text=(
                "The cell counts are chosen at the nearest existing mesh node. "
                "Arcs and polyLines retain their paths; splines are resampled "
                "from the original curve."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=4, column=0, columnspan=2, sticky="w",
            pady=(self._px(9), 0),
        )
        ttk.Button(
            self.selection_frame,
            text="Execute split",
            command=self.execute_edge_split,
        ).grid(
            row=5, column=0, columnspan=2, sticky="ew",
            pady=(self._px(10), 0),
        )

    def _sync_split_panel_value(self) -> None:
        if self.split_edge_active is None or self.split_fraction_var is None:
            return
        first_cells, second_cells = self.model.edge_split_cell_counts(
            self.split_edge_active, self.split_fraction
        )
        self.split_fraction_var.set(
            _display_split_percentage(self.split_fraction)
        )
        if self.split_cells_var is not None:
            self.split_cells_var.set(
                f"{first_cells} + {second_cells} cells"
            )

    def _build_export_panel(self) -> None:
        ttk.Label(
            self.selection_frame,
            text="Extrusion",
            font=self._font(11, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self._field(
            self.selection_frame, 1, "Z cells", self.z_cells_var,
            on_confirm=self.export,
        )
        self._field(
            self.selection_frame, 2, "zMin", self.z_min_var,
            on_confirm=self.export,
        )
        self._field(
            self.selection_frame, 3, "zMax", self.z_max_var,
            on_confirm=self.export,
        )
        self._field(
            self.selection_frame, 4, "Scale", self.scale_var,
            on_confirm=self.export,
        )
        ttk.Label(
            self.selection_frame,
            text="Z cells defaults to 1 for a pseudo-2D mesh.",
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=5, column=0, columnspan=2, sticky="w",
            pady=(self._px(5), self._px(10)),
        )

        ttk.Separator(self.selection_frame).grid(
            row=6, column=0, columnspan=2, sticky="ew",
            pady=(0, self._px(9)),
        )
        ttk.Label(
            self.selection_frame,
            text="Automatic z-face patches",
            font=self._font(11, "bold"),
        ).grid(row=7, column=0, columnspan=2, sticky="w")
        self._field(
            self.selection_frame, 8, "zMin name", self.z_min_patch_name_var,
            on_confirm=self.export,
        )
        ttk.Label(self.selection_frame, text="zMin type").grid(
            row=9, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        z_min_type = ttk.Combobox(
            self.selection_frame,
            textvariable=self.z_min_patch_type_var,
            values=MeshModel.SUPPORTED_BOUNDARY_TYPES,
            state="readonly",
            width=14,
        )
        z_min_type.grid(row=9, column=1, sticky="ew", pady=self._px(3))
        z_min_type.bind("<<ComboboxSelected>>", self._z_patch_type_selected)

        self._field(
            self.selection_frame, 10, "zMax name", self.z_max_patch_name_var,
            on_confirm=self.export,
        )
        ttk.Label(self.selection_frame, text="zMax type").grid(
            row=11, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        z_max_type = ttk.Combobox(
            self.selection_frame,
            textvariable=self.z_max_patch_type_var,
            values=MeshModel.SUPPORTED_BOUNDARY_TYPES,
            state="readonly",
            width=14,
        )
        z_max_type.grid(row=11, column=1, sticky="ew", pady=self._px(3))
        z_max_type.bind("<<ComboboxSelected>>", self._z_patch_type_selected)

        ttk.Label(
            self.selection_frame,
            text=(
                "The two patches are always written. Selecting cyclic for "
                "either face pairs both faces and writes reciprocal "
                "neighbourPatch entries automatically."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=12, column=0, columnspan=2, sticky="w",
            pady=(self._px(6), self._px(9)),
        )
        ttk.Button(
            self.selection_frame,
            text="Export blockMeshDict…",
            command=self.export,
        ).grid(row=13, column=0, columnspan=2, sticky="ew")

    def _z_patch_type_selected(self, event: tk.Event) -> None:
        selected = str(event.widget.get())
        if selected == "cyclic":
            self.z_min_patch_type_var.set("cyclic")
            self.z_max_patch_type_var.set("cyclic")
        elif "cyclic" in (
            self.z_min_patch_type_var.get(), self.z_max_patch_type_var.get()
        ):
            # A paired cyclic selection must also be easy to leave. The first
            # non-cyclic choice resets the pair; either face can then be changed
            # independently to another non-cyclic type.
            self.z_min_patch_type_var.set(selected)
            self.z_max_patch_type_var.set(selected)

    def _build_projection_panel(self) -> None:
        if self.projection_stage == "entities":
            ttk.Label(
                self.selection_frame,
                text="1. Select mesh entities",
                font=self._font(11, "bold"),
                foreground="#9c36b5",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                self.selection_frame,
                text=(
                    "Click one or more mesh vertices or edges. The first "
                    "selection fixes the entity type; vertices and edges "
                    "cannot be mixed."
                ),
                wraplength=self._px(245),
                foreground="#52606d",
            ).grid(
                row=1, column=0, columnspan=2, sticky="w",
                pady=(self._px(6), self._px(8)),
            )
            if self.projection_entity_kind == "vertex":
                selected = ", ".join(self.projection_vertex_ids)
            elif self.projection_entity_kind == "edge":
                selected = ", ".join(
                    f"{current[0]}—{current[1]}"
                    for current in self.projection_edges
                )
            else:
                selected = "None"
            count = len(self.projection_vertex_ids) + len(self.projection_edges)
            ttk.Label(
                self.selection_frame,
                text=f"Selected: {count}\n{selected}",
                justify="left",
                foreground="#9c36b5",
                wraplength=self._px(245),
            ).grid(
                row=2, column=0, columnspan=2, sticky="w",
                pady=(0, self._px(8)),
            )
            ttk.Button(
                self.selection_frame,
                text="Next: select target curves",
                command=self.continue_projection,
                state="normal" if count else "disabled",
            ).grid(row=3, column=0, columnspan=2, sticky="ew")
            ttk.Button(
                self.selection_frame,
                text="Cancel projection (Esc)",
                command=self.cancel_projection,
            ).grid(
                row=4, column=0, columnspan=2, sticky="ew",
                pady=(self._px(6), 0),
            )
            return

        selected_names = [
            self.model.geometry_curves[curve_id].name
            for curve_id in self.projection_curve_ids
            if curve_id in self.model.geometry_curves
        ]
        ttk.Label(
            self.selection_frame,
            text="2. Select target curves",
            font=self._font(11, "bold"),
            foreground="#087f5b",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self.selection_frame,
            text=(
                "Click one or more teal reference curves. Each source point "
                "uses the nearest valid projection among them."
            ),
            wraplength=self._px(245),
            foreground="#52606d",
        ).grid(
            row=1, column=0, columnspan=2, sticky="w",
            pady=(self._px(6), self._px(8)),
        )
        ttk.Label(
            self.selection_frame,
            text=(
                "Targets: " + (", ".join(selected_names) or "None")
            ),
            foreground="#087f5b",
            wraplength=self._px(245),
        ).grid(
            row=2, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(8)),
        )
        ttk.Label(self.selection_frame, text="Direction").grid(
            row=3, column=0, sticky="w", padx=(0, self._px(8))
        )
        ttk.Combobox(
            self.selection_frame,
            textvariable=self.projection_direction_var,
            values=tuple(PROJECTION_DIRECTION_LABELS),
            state="readonly",
            width=22,
        ).grid(row=3, column=1, sticky="ew")
        ttk.Label(
            self.selection_frame,
            text=(
                "Along x/y moves parallel to that axis. Orthogonal uses the "
                "shortest path to the selected curves."
            ),
            wraplength=self._px(245),
            foreground="#52606d",
        ).grid(
            row=4, column=0, columnspan=2, sticky="w",
            pady=(self._px(6), self._px(8)),
        )
        ttk.Checkbutton(
            self.selection_frame,
            text="Fit edge as spline",
            variable=self.projection_fit_var,
            state="normal" if self.projection_entity_kind == "edge" else "disabled",
            command=self._update_property_panel,
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self.selection_frame,
            text=(
                "Fit greedily adds points where they reduce the maximum "
                "geometric distance most. It stops at the requested tolerance "
                "or point limit."
            ),
            wraplength=self._px(245),
            foreground="#52606d",
        ).grid(
            row=6, column=0, columnspan=2, sticky="w",
            pady=(self._px(4), self._px(8)),
        )
        next_row = 7
        fit_controls_active = (
            self.projection_entity_kind == "edge"
            and self.projection_fit_var.get()
        )
        if fit_controls_active:
            ttk.Label(
                self.selection_frame, text="Relative tolerance"
            ).grid(
                row=next_row,
                column=0,
                sticky="w",
                padx=(0, self._px(8)),
            )
            tolerance_entry = ttk.Entry(
                self.selection_frame,
                textvariable=self.projection_fit_tolerance_var,
                width=12,
            )
            tolerance_entry.grid(row=next_row, column=1, sticky="ew")
            self._bind_entry_confirmation(
                tolerance_entry, self.apply_projection
            )
            next_row += 1
            ttk.Label(
                self.selection_frame, text="Maximum points per edge"
            ).grid(
                row=next_row,
                column=0,
                sticky="w",
                padx=(0, self._px(8)),
                pady=(self._px(5), 0),
            )
            maximum_entry = ttk.Entry(
                self.selection_frame,
                textvariable=self.projection_fit_max_points_var,
                width=12,
            )
            maximum_entry.grid(
                row=next_row,
                column=1,
                sticky="ew",
                pady=(self._px(5), 0),
            )
            self._bind_entry_confirmation(
                maximum_entry, self.apply_projection
            )
            next_row += 1
            ttk.Label(
                self.selection_frame,
                text=(
                    "The absolute target is the relative tolerance multiplied "
                    "by the fitted curve-section size (with a 1e-12 floor)."
                ),
                wraplength=self._px(245),
                foreground="#52606d",
            ).grid(
                row=next_row,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(self._px(5), self._px(8)),
            )
            next_row += 1
        ttk.Button(
            self.selection_frame,
            text="Project selected entities",
            command=self.apply_projection,
            state="normal" if selected_names else "disabled",
        ).grid(row=next_row, column=0, columnspan=2, sticky="ew")
        next_row += 1
        ttk.Button(
            self.selection_frame,
            text="Back to mesh entities",
            command=self.back_projection,
        ).grid(
            row=next_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )
        next_row += 1
        ttk.Button(
            self.selection_frame,
            text="Cancel projection (Esc)",
            command=self.cancel_projection,
        ).grid(
            row=next_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )

    def _build_geometry_curve_panel(self) -> None:
        curve_id = self.selected_geometry_curve
        if curve_id is None or curve_id not in self.model.geometry_curves:
            return
        curve = self.model.geometry_curves[curve_id]
        point_index = self.selected_geometry_point_index
        if point_index is None or not 0 <= point_index < len(curve.points):
            point_index = 0
            self.selected_geometry_point_index = point_index

        ttk.Label(
            self.selection_frame,
            text="Reference geometry curve",
            font=self._font(11, "bold"),
            foreground="#007c83",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(7)),
        )
        self.geometry_name_var = tk.StringVar(value=curve.name)
        self._field(
            self.selection_frame,
            1,
            "Name",
            self.geometry_name_var,
            on_confirm=self.apply_geometry_curve_name,
        )
        ttk.Button(
            self.selection_frame,
            text="Apply name",
            command=self.apply_geometry_curve_name,
        ).grid(
            row=2, column=0, columnspan=2, sticky="ew",
            pady=(self._px(5), self._px(8)),
        )

        self.geometry_show_points_var = tk.BooleanVar(
            value=curve.show_points
        )
        ttk.Checkbutton(
            self.selection_frame,
            text="Show curve points",
            variable=self.geometry_show_points_var,
            command=self.apply_geometry_point_visibility,
        ).grid(
            row=3, column=0, columnspan=2, sticky="w",
            pady=(0, self._px(6)),
        )

        ttk.Label(self.selection_frame, text="Selected point").grid(
            row=4, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        self.geometry_point_index_var = tk.StringVar(
            value=str(point_index + 1)
        )
        selector = ttk.Combobox(
            self.selection_frame,
            textvariable=self.geometry_point_index_var,
            values=tuple(str(index + 1) for index in range(len(curve.points))),
            state="readonly",
            width=14,
        )
        selector.grid(row=4, column=1, sticky="ew", pady=self._px(3))
        selector.bind(
            "<<ComboboxSelected>>", self._geometry_point_selected
        )

        point_x, point_y = curve.points[point_index]
        self.geometry_point_x_var = tk.StringVar(
            value=_display_number(point_x)
        )
        self.geometry_point_y_var = tk.StringVar(
            value=_display_number(point_y)
        )
        self._field(
            self.selection_frame,
            5,
            "Point X",
            self.geometry_point_x_var,
            on_confirm=self.apply_geometry_curve_point,
        )
        self._field(
            self.selection_frame,
            6,
            "Point Y",
            self.geometry_point_y_var,
            on_confirm=self.apply_geometry_curve_point,
        )
        ttk.Button(
            self.selection_frame,
            text="Apply point coordinates",
            command=self.apply_geometry_curve_point,
        ).grid(
            row=7, column=0, columnspan=2, sticky="ew",
            pady=(self._px(5), 0),
        )

        point_actions = ttk.Frame(self.selection_frame)
        point_actions.grid(
            row=8, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )
        point_actions.columnconfigure(0, weight=1)
        point_actions.columnconfigure(1, weight=1)
        ttk.Button(
            point_actions,
            text="Add point",
            command=self.add_geometry_curve_point,
        ).grid(row=0, column=0, sticky="ew", padx=(0, self._px(2)))
        ttk.Button(
            point_actions,
            text="Remove point",
            command=self.remove_geometry_curve_point,
            state="normal" if len(curve.points) > 2 else "disabled",
        ).grid(row=0, column=1, sticky="ew", padx=(self._px(2), 0))

        ttk.Button(
            self.selection_frame,
            text="Replace points from file…",
            command=self.replace_geometry_curve_points_from_file,
        ).grid(
            row=9, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )
        ttk.Button(
            self.selection_frame,
            text="Delete geometry curve",
            command=self.delete_geometry_curve,
        ).grid(
            row=10, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )
        ttk.Label(
            self.selection_frame,
            text=(
                "Teal dashed curves and square points are reference geometry. "
                "They are saved with the session but are not exported to "
                "blockMeshDict. Drag a point to move it."
            ),
            foreground="#007c83",
            wraplength=self._px(245),
        ).grid(
            row=11, column=0, columnspan=2, sticky="w",
            pady=(self._px(7), 0),
        )

    def _build_boundary_panel(self) -> None:
        names = list(self.model.boundaries)
        if self.active_boundary_name not in self.model.boundaries:
            self.active_boundary_name = names[0] if names else None

        ttk.Label(
            self.selection_frame,
            text="Named patches",
            font=self._font(11, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.boundary_listbox = tk.Listbox(
            self.selection_frame,
            height=max(4, min(8, len(names) or 4)),
            exportselection=False,
            activestyle="dotbox",
        )
        self.boundary_listbox.grid(
            row=1, column=0, columnspan=2, sticky="ew",
            pady=(self._px(5), self._px(7)),
        )
        for name in names:
            boundary = self.model.boundaries[name]
            self.boundary_listbox.insert("end", name)
            self.boundary_listbox.itemconfigure(
                "end", foreground=boundary.color
            )
        if self.active_boundary_name is not None:
            selected_index = names.index(self.active_boundary_name)
            self.boundary_listbox.selection_set(selected_index)
            self.boundary_listbox.activate(selected_index)
            self.boundary_listbox.see(selected_index)
        self.boundary_listbox.bind(
            "<<ListboxSelect>>", self._boundary_list_selected
        )

        add_row = ttk.Frame(self.selection_frame)
        add_row.grid(row=2, column=0, columnspan=2, sticky="ew")
        add_row.columnconfigure(0, weight=1)
        name_entry = ttk.Entry(
            add_row,
            textvariable=self.boundary_name_var,
        )
        name_entry.grid(row=0, column=0, sticky="ew")
        self._bind_entry_confirmation(name_entry, self.add_boundary)
        ttk.Button(
            add_row, text="Add", width=6, command=self.add_boundary
        ).grid(row=0, column=1, padx=(self._px(5), 0))

        if self.active_boundary_name is None:
            ttk.Label(
                self.selection_frame,
                text=(
                    "The list is empty. Enter a name such as inlet, walls, "
                    "or frontAndBack, then press Add."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=3, column=0, columnspan=2, sticky="w",
                pady=(self._px(8), 0),
            )
            return

        boundary = self.model.boundaries[self.active_boundary_name]
        detail_row = 3
        swatch = tk.Label(
            self.selection_frame,
            text="   ",
            background=boundary.color,
            relief="solid",
            borderwidth=1,
        )
        swatch.grid(
            row=detail_row, column=0, sticky="w",
            pady=(self._px(10), self._px(4)),
        )
        ttk.Label(
            self.selection_frame,
            text=(
                f"{len(self.model.boundary_edges(boundary.name))} assigned "
                "edge(s)"
            ),
        ).grid(
            row=detail_row, column=1, sticky="w",
            pady=(self._px(10), self._px(4)),
        )
        detail_row += 1

        ttk.Label(self.selection_frame, text="Type").grid(
            row=detail_row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        self.boundary_type_var = tk.StringVar(value=boundary.kind)
        type_selector = ttk.Combobox(
            self.selection_frame,
            textvariable=self.boundary_type_var,
            values=MeshModel.SUPPORTED_BOUNDARY_TYPES,
            state="readonly",
            width=14,
        )
        type_selector.grid(
            row=detail_row, column=1, sticky="ew", pady=self._px(3)
        )
        type_selector.bind(
            "<<ComboboxSelected>>", self._boundary_type_selected
        )
        detail_row += 1

        ttk.Label(self.selection_frame, text="Neighbour").grid(
            row=detail_row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        neighbours = tuple(name for name in names if name != boundary.name)
        self.boundary_neighbour_var = tk.StringVar(
            value=boundary.neighbour_patch or (neighbours[0] if neighbours else "")
        )
        self.boundary_neighbour_selector = ttk.Combobox(
            self.selection_frame,
            textvariable=self.boundary_neighbour_var,
            values=neighbours,
            state="readonly" if boundary.kind == "cyclic" else "disabled",
            width=14,
        )
        self.boundary_neighbour_selector.grid(
            row=detail_row, column=1, sticky="ew", pady=self._px(3)
        )
        self.boundary_neighbour_selector.bind(
            "<<ComboboxSelected>>", self._boundary_neighbour_selected
        )
        detail_row += 1

        ttk.Label(
            self.selection_frame,
            text=(
                "Cyclic patches are paired reciprocally. OpenFOAM infers the "
                "ordinary cyclic transform from the two matching patches."
            ),
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=detail_row, column=0, columnspan=2, sticky="w",
            pady=(self._px(4), self._px(7)),
        )
        detail_row += 1

        ttk.Button(
            self.selection_frame,
            text="Remove boundary",
            command=self.remove_active_boundary,
        ).grid(
            row=detail_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )
