"""Tkinter graphical interface for BlockDrawer."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Callable

from .foam import write_block_mesh_dict
from .history import ModelHistory
from .model import EdgeKey, MeshModel, TopologyError, edge_key
from .session import SessionError, load_session, save_session


APP_NAME = "BlockDrawer"
MAX_VISIBLE_EDGE_MARKERS = 500


class BlockDrawerApp:
    """Top-level application controller and Tk view."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.model = MeshModel()
        self.history = ModelHistory(self.model)
        self.session_path: Path | None = None
        self.dirty = False
        self.selected_vertex: str | None = None
        self.selected_edge: EdgeKey | None = None
        self.block_vertex_selection: list[str] | None = None
        self.item_targets: dict[int, tuple[str, object]] = {}
        self.drag_vertex: str | None = None
        self.drag_changed = False
        self.pan_anchor: tuple[float, float, float, float] | None = None

        self.system_tk_scaling = float(self.root.tk.call("tk", "scaling"))
        self.system_display_scale = _system_display_scale(
            self.system_tk_scaling, sys.platform
        )
        self.ui_scale_multiplier = 1.0
        self.display_scale = self.system_display_scale
        self.ui_scale_var = tk.StringVar(value="auto")
        self.default_font_family = tkfont.nametofont(
            "TkDefaultFont", root=self.root
        ).cget("family")
        self.base_named_font_sizes: dict[str, int] = {}
        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                self.base_named_font_sizes[font_name] = int(
                    tkfont.nametofont(font_name, root=self.root).cget("size")
                )
            except tk.TclError:
                continue
        self._scale_named_fonts(1.0)

        self.view_x = 0.5
        self.view_y = 0.5
        self.pixels_per_unit = 450.0 * self.display_scale

        self.status = tk.StringVar(
            value="Select a vertex to move it, or select an exterior edge to add a block."
        )
        self.z_cells_var = tk.StringVar()
        self.z_min_var = tk.StringVar()
        self.z_max_var = tk.StringVar()
        self.scale_var = tk.StringVar()
        self.vertex_x_var: tk.StringVar | None = None
        self.vertex_y_var: tk.StringVar | None = None
        self.edge_cells_var: tk.StringVar | None = None

        self._build_window()
        self._sync_global_values()
        self._update_property_panel()
        self._update_history_controls()
        self._update_title()
        self.root.after_idle(self.fit_view)

    def _build_window(self) -> None:
        self.root.title(APP_NAME)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        initial_width = min(self._px(1120), int(screen_width * 0.92))
        initial_height = min(self._px(720), int(screen_height * 0.88))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self._set_minimum_window_size()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_menu()

        self.toolbar = ttk.Frame(self.root, padding=(self._px(8), self._px(6)))
        self.toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(self.toolbar, text="New", command=self.new_session).pack(side="left")
        ttk.Button(self.toolbar, text="Open", command=self.open_session).pack(
            side="left", padx=(self._px(5), 0)
        )
        ttk.Button(self.toolbar, text="Save", command=self.save).pack(
            side="left", padx=(self._px(5), 0)
        )
        ttk.Separator(self.toolbar, orient="vertical").pack(
            side="left", fill="y", padx=self._px(8)
        )
        self.undo_button = ttk.Button(
            self.toolbar, text="Undo", command=self.undo
        )
        self.undo_button.pack(side="left")
        self.redo_button = ttk.Button(
            self.toolbar, text="Redo", command=self.redo
        )
        self.redo_button.pack(side="left", padx=(self._px(5), 0))
        ttk.Separator(self.toolbar, orient="vertical").pack(
            side="left", fill="y", padx=self._px(8)
        )
        self.add_button = ttk.Button(
            self.toolbar, text="Add block", command=self.add_selected_block
        )
        self.add_button.pack(side="left")
        ttk.Button(self.toolbar, text="Fit view", command=self.fit_view).pack(
            side="left", padx=(self._px(5), 0)
        )
        ttk.Button(self.toolbar, text="Export blockMeshDict", command=self.export).pack(
            side="right"
        )

        main = ttk.Frame(self.root)
        main.grid(row=1, column=0, columnspan=2, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            main,
            background="#f8fafc",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.sidebar = ttk.Frame(
            main, width=self._px(300), padding=self._px(12)
        )
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self._build_sidebar()

        self.status_bar = ttk.Label(
            self.root,
            textvariable=self.status,
            anchor="w",
            padding=(self._px(8), self._px(5)),
            relief="sunken",
        )
        self.status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_drag)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

        self.root.bind("<Control-n>", lambda _event: self.new_session())
        self.root.bind("<Control-o>", lambda _event: self.open_session())
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-Shift-S>", lambda _event: self.save_as())
        self.root.bind("<Control-e>", lambda _event: self.export())
        self.root.bind("<Control-z>", lambda _event: self._shortcut(self.undo))
        self.root.bind("<Control-y>", lambda _event: self._shortcut(self.redo))
        self.root.bind("<Control-Shift-Z>", lambda _event: self._shortcut(self.redo))
        self.root.bind("<Command-z>", lambda _event: self._shortcut(self.undo))
        self.root.bind("<Command-Shift-Z>", lambda _event: self._shortcut(self.redo))
        self.root.bind("<Delete>", self._delete_shortcut)
        self.root.bind("<BackSpace>", self._delete_shortcut)
        self.root.bind("<KP_Delete>", self._delete_shortcut)
        self.root.bind("<KeyPress-x>", self._delete_shortcut)
        self.root.bind("<KeyPress-X>", self._delete_shortcut)
        self.root.bind("<KeyPress-n>", self._new_block_shortcut)
        self.root.bind("<KeyPress-N>", self._new_block_shortcut)
        self.root.bind("<Escape>", self._escape_shortcut)

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="New", accelerator="Ctrl+N", command=self.new_session)
        file_menu.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_session)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(
            label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export blockMeshDict…", accelerator="Ctrl+E", command=self.export
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)

        self.edit_menu = tk.Menu(menu, tearoff=False)
        self.edit_menu.add_command(
            label="Undo", accelerator="Ctrl+Z", command=self.undo
        )
        self.undo_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_command(
            label="Redo", accelerator="Ctrl+Y", command=self.redo
        )
        self.redo_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(
            label="New block from 4 vertices",
            accelerator="N",
            command=self.start_block_from_vertices,
        )
        self.edit_menu.add_command(
            label="Delete selected edge",
            accelerator="Delete / X",
            command=self.delete_selected_edge,
        )
        self.delete_edge_menu_index = self.edit_menu.index("end")
        menu.add_cascade(label="Edit", menu=self.edit_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(label="Fit topology", command=self.fit_view)
        scale_menu = tk.Menu(view_menu, tearoff=False)
        for label, value in (
            ("System (automatic)", "auto"),
            ("125% of system", "1.25"),
            ("150% of system", "1.5"),
            ("200% of system", "2.0"),
        ):
            scale_menu.add_radiobutton(
                label=label,
                variable=self.ui_scale_var,
                value=value,
                command=self.apply_ui_scale,
            )
        view_menu.add_cascade(label="UI scale", menu=scale_menu)
        menu.add_cascade(label="View", menu=view_menu)
        self.root.configure(menu=menu)

    def _build_sidebar(self) -> None:
        title = ttk.Label(
            self.sidebar, text="Properties", font=self._font(15, "bold")
        )
        title.grid(row=0, column=0, sticky="w", pady=(0, 10))

        global_frame = ttk.LabelFrame(
            self.sidebar, text="Extrusion", padding=self._px(10)
        )
        global_frame.grid(row=1, column=0, sticky="ew")
        global_frame.columnconfigure(1, weight=1)

        self._field(global_frame, 0, "Z cells", self.z_cells_var)
        self._field(global_frame, 1, "zMin", self.z_min_var)
        self._field(global_frame, 2, "zMax", self.z_max_var)
        self._field(global_frame, 3, "Scale", self.scale_var)
        ttk.Label(
            global_frame,
            text="Z cells defaults to 1 for a pseudo-2D mesh.",
            foreground="#52606d",
            wraplength=self._px(245),
        ).grid(
            row=4, column=0, columnspan=2, sticky="w",
            pady=(self._px(6), self._px(8)),
        )
        ttk.Button(global_frame, text="Apply extrusion", command=self.apply_global).grid(
            row=5, column=0, columnspan=2, sticky="ew"
        )

        self.selection_frame = ttk.LabelFrame(
            self.sidebar, text="Selection", padding=self._px(10)
        )
        self.selection_frame.grid(
            row=2, column=0, sticky="new", pady=(self._px(12), 0)
        )
        self.selection_frame.columnconfigure(1, weight=1)

        ttk.Label(
            self.sidebar,
            text=(
                "Mouse wheel: zoom\nMiddle/right drag: pan\n"
                "Double-click an exterior edge: add block\n"
                "N: connect 4 vertices · Esc: cancel"
            ),
            foreground="#52606d",
            justify="left",
        ).grid(row=3, column=0, sticky="sw", pady=(self._px(18), 0))
        self.sidebar.rowconfigure(3, weight=1)

    def _field(self, parent: ttk.Frame, row: int, label: str,
               variable: tk.StringVar) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w",
            padx=(0, self._px(8)), pady=self._px(3),
        )
        entry = ttk.Entry(parent, textvariable=variable, width=16)
        entry.grid(row=row, column=1, sticky="ew", pady=self._px(3))
        return entry

    def _update_property_panel(self) -> None:
        for child in self.selection_frame.winfo_children():
            child.destroy()
        self.vertex_x_var = None
        self.vertex_y_var = None
        self.edge_cells_var = None

        if self.block_vertex_selection is not None:
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
            self._field(self.selection_frame, 1, "X", self.vertex_x_var)
            self._field(self.selection_frame, 2, "Y", self.vertex_y_var)
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
            self._field(self.selection_frame, 1, "Cells", self.edge_cells_var)
            ttk.Label(
                self.selection_frame,
                text=(
                    f"Changing this value updates {len(affected)} linked edge"
                    f"{'s' if len(affected) != 1 else ''}. The canvas shows "
                    "uniform mesh-node positions."
                ),
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(
                row=2, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), self._px(8)),
            )
            ttk.Button(
                self.selection_frame,
                text="Apply cell count",
                command=self.apply_edge_cells,
            ).grid(row=3, column=0, columnspan=2, sticky="ew")
            if boundary:
                ttk.Button(
                    self.selection_frame,
                    text="Add block on this side",
                    command=self.add_selected_block,
                ).grid(
                    row=4, column=0, columnspan=2, sticky="ew",
                    pady=(self._px(7), 0),
                )
            else:
                ttk.Label(
                    self.selection_frame,
                    text="Internal edge — already shared by two blocks",
                    foreground="#52606d",
                    wraplength=self._px(245),
                ).grid(
                    row=4, column=0, columnspan=2, sticky="w",
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
                row=5, column=0, columnspan=2, sticky="ew",
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
                text="Click a vertex or an edge in the drawing.",
                foreground="#52606d",
                wraplength=self._px(245),
            ).grid(row=1, column=0, sticky="w", pady=(self._px(5), 0))

        can_add = self.selected_edge is not None \
            and self.model.is_boundary_edge(self.selected_edge)
        self.add_button.configure(state="normal" if can_add else "disabled")
        delete_state = (
            "normal"
            if self.selected_edge is not None
            and self.model.can_remove_edge(self.selected_edge)
            else "disabled"
        )
        self.edit_menu.entryconfigure(
            self.delete_edge_menu_index, state=delete_state
        )

    def apply_global(self) -> None:
        previous = (
            self.model.z_cells,
            self.model.z_min,
            self.model.z_max,
            self.model.scale,
        )
        try:
            self.model.set_z_cells(_positive_integer(self.z_cells_var.get(), "Z cells"))
            self.model.set_z_extents(
                float(self.z_min_var.get()), float(self.z_max_var.get())
            )
            self.model.scale = float(self.scale_var.get())
            self.model.validate()
        except (ValueError, TopologyError) as exc:
            (
                self.model.z_cells,
                self.model.z_min,
                self.model.z_max,
                self.model.scale,
            ) = previous
            self._show_error("Invalid extrusion settings", exc)
            return
        self._commit_edit()
        self._sync_global_values()
        self.status.set("Extrusion settings updated.")

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
        self.status.set(
            f"Set {cells} cells on {len(affected)} topology-linked edge"
            f"{'s' if len(affected) != 1 else ''}."
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
        self._commit_edit()
        self.fit_view()
        self._update_property_panel()
        self.status.set(
            f"Added {block.id}. Its outer edge is selected for quick extension."
        )

    def start_block_from_vertices(self) -> None:
        self.block_vertex_selection = []
        self.selected_vertex = None
        self.selected_edge = None
        self.drag_vertex = None
        self.drag_changed = False
        self.canvas.focus_set()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            "New block mode: select four existing vertices; press Esc to cancel."
        )

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
        self._commit_edit()
        self._update_property_panel()
        self.redraw()
        self.status.set(
            f"Deleted edge {selected[0]} — {selected[1]} and "
            f"{len(removed)} incident block{'s' if len(removed) != 1 else ''}."
        )

    def redraw(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        self.item_targets.clear()
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self._draw_grid(width, height)

        for block in self.model.blocks:
            points: list[float] = []
            for identifier in block.vertices:
                vertex = self.model.vertices[identifier]
                points.extend(self.world_to_screen(vertex.x, vertex.y))
            self.canvas.create_polygon(
                points,
                fill="#ffffff",
                outline="",
            )

        for current in self.model.edges():
            first = self.model.vertices[current[0]]
            second = self.model.vertices[current[1]]
            x1, y1 = self.world_to_screen(first.x, first.y)
            x2, y2 = self.world_to_screen(second.x, second.y)
            selected = current == self.selected_edge
            color = "#e8590c" if selected else "#334e68"
            line = self.canvas.create_line(
                x1, y1, x2, y2,
                fill=color,
                width=self._px(4 if selected else 2),
            )
            self.item_targets[line] = ("edge", current)
            self._draw_edge_nodes(current, x1, y1, x2, y2, color)

            midpoint_x = (x1 + x2) / 2.0
            midpoint_y = (y1 + y2) / 2.0
            label = self.canvas.create_text(
                midpoint_x,
                midpoint_y - self._px(11),
                text=f"{self.model.edge_cells[current]}",
                fill="#9c3d10" if selected else "#52606d",
                font=self._font(9, "bold" if selected else "normal"),
            )
            self.item_targets[label] = ("edge", current)

        for identifier, vertex in self.model.vertices.items():
            x, y = self.world_to_screen(vertex.x, vertex.y)
            selected = identifier == self.selected_vertex
            staged_index = None
            if self.block_vertex_selection is not None \
                    and identifier in self.block_vertex_selection:
                staged_index = self.block_vertex_selection.index(identifier)
            # Deliberately larger than edge markers so vertices remain easy to
            # acquire with the mouse, including on high-density displays.
            emphasized = selected or staged_index is not None
            radius = self._px(9 if emphasized else 7)
            item = self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=(
                    "#7048a8" if staged_index is not None
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
            label = self.canvas.create_text(
                x + self._px(11),
                y + self._px(11),
                text=identifier,
                anchor="nw",
                fill="#243b53",
                font=self._font(9),
            )
            self.item_targets[label] = ("vertex", identifier)

    def _draw_edge_nodes(self, current: EdgeKey, x1: float, y1: float,
                         x2: float, y2: float, color: str) -> None:
        cells = self.model.edge_cells[current]
        if cells <= 1:
            return
        stride = max(1, math.ceil((cells - 1) / MAX_VISIBLE_EDGE_MARKERS))
        for index in range(stride, cells, stride):
            ratio = index / cells
            x = x1 + ratio * (x2 - x1)
            y = y1 + ratio * (y2 - y1)
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
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        return (
            width / 2.0 + (x - self.view_x) * self.pixels_per_unit,
            height / 2.0 - (y - self.view_y) * self.pixels_per_unit,
        )

    def screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        return (
            self.view_x + (x - width / 2.0) / self.pixels_per_unit,
            self.view_y - (y - height / 2.0) / self.pixels_per_unit,
        )

    def fit_view(self) -> None:
        if not self.model.vertices:
            return
        self.root.update_idletasks()
        xs = [vertex.x for vertex in self.model.vertices.values()]
        ys = [vertex.y for vertex in self.model.vertices.values()]
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
        self.sidebar.configure(width=self._px(300), padding=self._px(12))
        for child in self.sidebar.winfo_children():
            child.destroy()
        self._build_sidebar()
        self._update_property_panel()
        self.pixels_per_unit *= ratio
        self.redraw()
        label = "system automatic" if choice == "auto" else f"{choice}× system"
        self.status.set(f"UI scale set to {label}.")

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
        self.drag_vertex = None
        self.drag_changed = False
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
        elif target[0] == "vertex":
            self.selected_vertex = str(target[1])
            self.selected_edge = None
            self.drag_vertex = self.selected_vertex
        elif target[0] == "edge":
            self.selected_vertex = None
            self.selected_edge = target[1]  # type: ignore[assignment]
        self._update_property_panel()
        self.redraw()

    def _on_left_drag(self, event: tk.Event) -> None:
        if self.drag_vertex is None:
            return
        x, y = self.screen_to_world(event.x, event.y)
        try:
            self.model.move_vertex(self.drag_vertex, x, y)
        except TopologyError as exc:
            self.status.set(str(exc))
            return
        self.drag_changed = True
        self._refresh_dirty()
        self._sync_property_values()
        self.redraw()
        self.status.set(
            f"{self.drag_vertex}: ({_display_number(x)}, {_display_number(y)})"
        )

    def _on_left_release(self, _event: tk.Event) -> None:
        if self.drag_vertex is not None and self.drag_changed:
            self._commit_edit()
        self.drag_vertex = None
        self.drag_changed = False

    def _on_double_click(self, _event: tk.Event) -> None:
        target = self._target_at_cursor()
        if target is not None and target[0] == "edge":
            self.selected_vertex = None
            self.selected_edge = target[1]  # type: ignore[assignment]
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

    def _on_pan_start(self, event: tk.Event) -> None:
        self.pan_anchor = (event.x, event.y, self.view_x, self.view_y)

    def _on_pan_drag(self, event: tk.Event) -> None:
        if self.pan_anchor is None:
            return
        start_x, start_y, center_x, center_y = self.pan_anchor
        self.view_x = center_x - (event.x - start_x) / self.pixels_per_unit
        self.view_y = center_y + (event.y - start_y) / self.pixels_per_unit
        self.redraw()

    def _on_mousewheel(self, event: tk.Event) -> None:
        before_x, before_y = self.screen_to_world(event.x, event.y)
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            factor = 1.15
        else:
            factor = 1.0 / 1.15
        self.pixels_per_unit = max(
            10.0 * self.display_scale,
            min(5000.0 * self.display_scale, self.pixels_per_unit * factor),
        )
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self.view_x = before_x - (event.x - width / 2.0) / self.pixels_per_unit
        self.view_y = before_y + (event.y - height / 2.0) / self.pixels_per_unit
        self.redraw()

    def new_session(self) -> None:
        if not self._confirm_discard():
            return
        self.model = MeshModel()
        self.history.reset(self.model)
        self.session_path = None
        self.selected_vertex = None
        self.selected_edge = None
        self.block_vertex_selection = None
        self._refresh_dirty()
        self._update_history_controls()
        self._sync_global_values()
        self._update_property_panel()
        self.fit_view()
        self.status.set("Created a new single-block topology.")
        self._update_title()

    def open_session(self) -> None:
        if not self._confirm_discard():
            return
        filename = filedialog.askopenfilename(
            title="Open BlockDrawer session",
            filetypes=[("BlockDrawer JSON", "*.json"), ("All files", "*")],
        )
        if not filename:
            return
        try:
            model = load_session(filename)
        except SessionError as exc:
            self._show_error("Could not open session", exc)
            return
        self.model = model
        self.history.reset(self.model)
        self.session_path = Path(filename)
        self.selected_vertex = None
        self.selected_edge = None
        self.block_vertex_selection = None
        self._refresh_dirty()
        self._update_history_controls()
        self._sync_global_values()
        self._update_property_panel()
        self.fit_view()
        self.status.set(f"Loaded {self.session_path.name}.")
        self._update_title()

    def save(self) -> bool:
        if self.session_path is None:
            return self.save_as()
        try:
            save_session(self.model, self.session_path)
        except (OSError, SessionError, TopologyError) as exc:
            self._show_error("Could not save session", exc)
            return False
        self.history.mark_saved(self.model)
        self._refresh_dirty()
        self.status.set(f"Saved {self.session_path.name}.")
        return True

    def save_as(self) -> bool:
        filename = filedialog.asksaveasfilename(
            title="Save BlockDrawer session",
            defaultextension=".json",
            initialfile=self.session_path.name if self.session_path else "mesh-blocks.json",
            filetypes=[("BlockDrawer JSON", "*.json"), ("All files", "*")],
        )
        if not filename:
            return False
        self.session_path = Path(filename)
        return self.save()

    def export(self) -> None:
        initial_directory = str(self.session_path.parent) if self.session_path else None
        filename = filedialog.asksaveasfilename(
            title="Export OpenFOAM dictionary",
            initialdir=initial_directory,
            initialfile="blockMeshDict",
            filetypes=[("OpenFOAM dictionary", "blockMeshDict"), ("All files", "*")],
        )
        if not filename:
            return
        try:
            write_block_mesh_dict(self.model, filename)
        except (OSError, TopologyError) as exc:
            self._show_error("Could not export blockMeshDict", exc)
            return
        self.status.set(f"Exported {filename}. Run OpenFOAM blockMesh to generate the mesh.")

    def close(self) -> None:
        if self._confirm_discard():
            self.root.destroy()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            "Save this BlockDrawer session before continuing?",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def undo(self) -> None:
        restored = self.history.undo()
        if restored is None:
            self.status.set("Nothing to undo.")
            return
        self._restore_model(restored)
        self.status.set("Undid the last edit.")

    def redo(self) -> None:
        restored = self.history.redo()
        if restored is None:
            self.status.set("Nothing to redo.")
            return
        self._restore_model(restored)
        self.status.set("Redid the last edit.")

    def _restore_model(self, restored: MeshModel) -> None:
        self.model = restored
        self.block_vertex_selection = None
        if self.selected_vertex not in self.model.vertices:
            self.selected_vertex = None
        if self.selected_edge not in self.model.edge_cells:
            self.selected_edge = None
        self.drag_vertex = None
        self.drag_changed = False
        self._sync_global_values()
        self._update_property_panel()
        self.redraw()
        self._refresh_dirty()
        self._update_history_controls()

    def _commit_edit(self) -> None:
        self.history.record(self.model)
        self._refresh_dirty()
        self._update_history_controls()

    def _refresh_dirty(self) -> None:
        dirty = self.history.is_dirty(self.model)
        if dirty != self.dirty:
            self.dirty = dirty
            self._update_title()

    def _update_history_controls(self) -> None:
        undo_state = "normal" if self.history.can_undo else "disabled"
        redo_state = "normal" if self.history.can_redo else "disabled"
        self.undo_button.configure(state=undo_state)
        self.redo_button.configure(state=redo_state)
        self.edit_menu.entryconfigure(self.undo_menu_index, state=undo_state)
        self.edit_menu.entryconfigure(self.redo_menu_index, state=redo_state)

    @staticmethod
    def _shortcut(action: Callable[[], None]) -> str:
        action()
        return "break"

    def _delete_shortcut(self, event: tk.Event) -> str | None:
        # Preserve normal editing inside coordinate/cell input widgets.
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.delete_selected_edge()
        return "break"

    def _new_block_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        # Shift/CapsLock are fine for uppercase N; avoid intercepting Ctrl+N,
        # Command+N, Alt+N, and other modified application shortcuts.
        if event.state & ~0x0003:
            return None
        self.start_block_from_vertices()
        return "break"

    def _escape_shortcut(self, _event: tk.Event) -> str | None:
        if self.block_vertex_selection is None:
            return None
        self.cancel_block_from_vertices()
        return "break"

    def _update_title(self) -> None:
        name = self.session_path.name if self.session_path else "Untitled"
        marker = "*" if self.dirty else ""
        self.root.title(f"{marker}{name} — {APP_NAME}")

    def _sync_global_values(self) -> None:
        self.z_cells_var.set(str(self.model.z_cells))
        self.z_min_var.set(_display_number(self.model.z_min))
        self.z_max_var.set(_display_number(self.model.z_max))
        self.scale_var.set(_display_number(self.model.scale))

    def _sync_property_values(self) -> None:
        if self.selected_vertex is not None and self.vertex_x_var is not None \
                and self.vertex_y_var is not None:
            vertex = self.model.vertices[self.selected_vertex]
            self.vertex_x_var.set(_display_number(vertex.x))
            self.vertex_y_var.set(_display_number(vertex.y))
        if self.selected_edge is not None and self.edge_cells_var is not None:
            self.edge_cells_var.set(str(self.model.edge_cells[self.selected_edge]))

    def _show_error(self, title: str, error: Exception) -> None:
        self.status.set(str(error))
        messagebox.showerror(title, str(error), parent=self.root)


def _positive_integer(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if result < 1 or str(result) != value.strip():
        raise ValueError(f"{label} must be a positive integer")
    return result


def _display_number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(value, ".8g")


def _nice_grid_step(raw_step: float) -> float:
    exponent = math.floor(math.log10(max(raw_step, 1.0e-12)))
    fraction = raw_step / (10.0 ** exponent)
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10.0 ** exponent)


def _system_display_scale(tk_scaling: float, platform: str) -> float:
    # Aqua uses logical 72-DPI points and handles Retina backing pixels itself.
    # Windows and X11 conventionally report pixels per 72-point inch, with 96
    # DPI as the unscaled baseline. Never shrink UI on unusually low DPI data.
    if platform == "darwin":
        return 1.0
    baseline = 96.0 / 72.0
    return max(1.0, min(4.0, tk_scaling / baseline))


def _scaled_named_font_size(base_size: int, system_scale: float,
                            manual_multiplier: float) -> int:
    if base_size < 0:
        # Negative Tk sizes are raw pixels and otherwise bypass tk scaling.
        return -max(
            1,
            int(round(abs(base_size) * system_scale * manual_multiplier)),
        )
    # Positive sizes are points, so Tk already applies the system DPI.
    return max(1, int(round(base_size * manual_multiplier)))


def _is_text_input_class(widget_class: str) -> bool:
    return widget_class in {
        "Entry", "TEntry", "Text", "Spinbox", "TSpinbox",
    }


def _enable_high_dpi_awareness() -> None:
    """Opt into Windows per-monitor DPI before Tk creates any windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        # Older Windows/Tk combinations still use Tk's global scaling value.
        return


def main() -> None:
    _enable_high_dpi_awareness()
    root = tk.Tk()
    BlockDrawerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
