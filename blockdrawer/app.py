"""Tkinter graphical interface for BlockDrawer."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Callable

from .config import (
    AppConfig,
    ConfigError,
    default_config,
    default_config_path,
    load_config,
    save_config,
    shortcut_to_tk_sequences,
)
from .foam import write_block_mesh_dict
from .geometry import GeometryImportError, load_point_pairs
from .history import ModelHistory
from .model import EdgeKey, MeshModel, TopologyError, edge_key
from .projection import DEFAULT_FIT_MAX_POINTS, FIT_RELATIVE_TOLERANCE
from .session import SessionError, load_session, save_session


APP_NAME = "BlockDrawer"
MAX_VISIBLE_EDGE_MARKERS = 500
MAX_VISIBLE_CONTROL_POINTS = 250
CURVE_RENDER_SEGMENTS = 64
SPLINE_SAMPLES_PER_SPAN = 4
GEOMETRY_SAMPLES_PER_SPAN = 4
MAX_ZOOM_PIXELS_PER_UNIT = 10_000_000.0
MIN_SPLIT_FRACTION = 1.0e-4
MAX_SPLIT_PICK_SAMPLES = 4096
PROJECTION_DIRECTION_LABELS = {
    "Orthogonal (shortest path)": "orthogonal",
    "Along x": "x",
    "Along y": "y",
}


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
        self.selected_control_point_index: int | None = None
        self.selected_geometry_curve: str | None = None
        self.selected_geometry_point_index: int | None = None
        self.block_vertex_selection: list[str] | None = None
        self.vertex_placement_active = False
        self.boundary_mode_active = False
        self.export_mode_active = False
        self.split_edge_active: EdgeKey | None = None
        self.split_fraction = 0.5
        self.drag_split_marker = False
        self.projection_stage: str | None = None
        self.projection_entity_kind: str | None = None
        self.projection_vertex_ids: list[str] = []
        self.projection_edges: list[EdgeKey] = []
        self.projection_curve_ids: list[str] = []
        self.active_boundary_name: str | None = None
        self.item_targets: dict[int, tuple[str, object]] = {}
        self.drag_vertex: str | None = None
        self.drag_control_point: tuple[EdgeKey, int] | None = None
        self.drag_geometry_point: tuple[str, int] | None = None
        self.drag_changed = False
        self.last_pressed_target: tuple[str, object] | None = None
        self.pan_anchor: tuple[float, float, float, float] | None = None

        self.config_path = default_config_path()
        self.config_warning: str | None = None
        self.preferences = self._load_preferences()

        self.system_tk_scaling = float(self.root.tk.call("tk", "scaling"))
        self.system_display_scale = _system_display_scale(
            self.system_tk_scaling, sys.platform
        )
        self.ui_scale_multiplier = (
            1.0
            if self.preferences.ui_scale == "auto"
            else float(self.preferences.ui_scale)
        )
        self.display_scale = (
            self.system_display_scale * self.ui_scale_multiplier
        )
        self.ui_scale_var = tk.StringVar(value=self.preferences.ui_scale)
        self.show_block_mesh_var = tk.BooleanVar(
            value=self.preferences.show_block_mesh
        )
        self.show_geometry_var = tk.BooleanVar(
            value=self.preferences.show_geometry
        )
        self.show_edge_nodes_var = tk.BooleanVar(
            value=self.preferences.show_edge_nodes
        )
        self.show_edge_interpolation_points_var = tk.BooleanVar(
            value=self.preferences.show_edge_interpolation_points
        )
        self.edge_grading_propagate_var = tk.BooleanVar(value=False)
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
        self._scale_named_fonts(self.ui_scale_multiplier)

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
        self.z_min_patch_name_var = tk.StringVar()
        self.z_min_patch_type_var = tk.StringVar()
        self.z_max_patch_name_var = tk.StringVar()
        self.z_max_patch_type_var = tk.StringVar()
        self.vertex_x_var: tk.StringVar | None = None
        self.vertex_y_var: tk.StringVar | None = None
        self.edge_cells_var: tk.StringVar | None = None
        self.edge_type_var: tk.StringVar | None = None
        self.edge_length_var: tk.StringVar | None = None
        self.edge_cell_ratio_var: tk.StringVar | None = None
        self.edge_total_ratio_var: tk.StringVar | None = None
        self.edge_start_width_var: tk.StringVar | None = None
        self.edge_end_width_var: tk.StringVar | None = None
        self.point_x_var: tk.StringVar | None = None
        self.point_y_var: tk.StringVar | None = None
        self.point_list_index_var: tk.StringVar | None = None
        self.edge_point_count_var: tk.StringVar | None = None
        self.split_fraction_var: tk.StringVar | None = None
        self.split_cells_var: tk.StringVar | None = None
        self.geometry_name_var: tk.StringVar | None = None
        self.geometry_point_x_var: tk.StringVar | None = None
        self.geometry_point_y_var: tk.StringVar | None = None
        self.geometry_point_index_var: tk.StringVar | None = None
        self.geometry_show_points_var: tk.BooleanVar | None = None
        self.projection_direction_var = tk.StringVar(
            value="Orthogonal (shortest path)"
        )
        self.projection_fit_var = tk.BooleanVar(value=False)
        self.projection_fit_tolerance_var = tk.StringVar(
            value=_display_number(FIT_RELATIVE_TOLERANCE)
        )
        self.projection_fit_max_points_var = tk.StringVar(
            value=str(DEFAULT_FIT_MAX_POINTS)
        )
        self.boundary_name_var = tk.StringVar()
        self.boundary_type_var: tk.StringVar | None = None
        self.boundary_neighbour_var: tk.StringVar | None = None
        self.boundary_listbox: tk.Listbox | None = None
        self.boundary_neighbour_selector: ttk.Combobox | None = None

        self._build_window()
        self._sync_global_values()
        self._update_property_panel()
        self._update_history_controls()
        self._update_title()
        if self.config_warning is not None:
            self.status.set(self.config_warning)
        self.root.after_idle(self.fit_view)

    def _load_preferences(self) -> AppConfig:
        defaults = default_config()
        self.config_write_enabled = True
        try:
            if self.config_path.exists():
                return load_config(self.config_path)
            save_config(defaults, self.config_path)
            return defaults
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            self.config_warning = (
                f"Could not use preferences at {self.config_path}: {exc}. "
                "Using defaults for this run."
            )
            return defaults

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
        ttk.Button(
            self.toolbar,
            text="Add vertex",
            command=self.start_vertex_placement,
        ).pack(side="left", padx=(self._px(5), 0))
        self.boundary_button = ttk.Button(
            self.toolbar,
            text="Set boundaries",
            command=self.toggle_boundary_mode,
        )
        self.boundary_button.pack(side="left", padx=(self._px(5), 0))
        ttk.Button(
            self.toolbar,
            text="Add curve",
            command=self.add_geometry_curve,
        ).pack(side="left", padx=(self._px(5), 0))
        ttk.Button(
            self.toolbar,
            text="Import curve…",
            command=self.import_geometry_curve,
        ).pack(side="left", padx=(self._px(5), 0))
        ttk.Button(self.toolbar, text="Fit view", command=self.fit_view).pack(
            side="left", padx=(self._px(5), 0)
        )
        self.export_button = ttk.Button(
            self.toolbar, text="Export", command=self.toggle_export_mode
        )
        self.export_button.pack(side="right")

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

        self.sidebar_host = ttk.Frame(main, width=self._px(300))
        self.sidebar_host.grid(row=0, column=1, sticky="ns")
        self.sidebar_host.grid_propagate(False)
        self.sidebar_host.columnconfigure(0, weight=1)
        self.sidebar_host.rowconfigure(0, weight=1)
        self.sidebar_canvas = tk.Canvas(
            self.sidebar_host,
            highlightthickness=0,
            borderwidth=0,
            background=self.root.cget("background"),
        )
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        self.sidebar_scrollbar = ttk.Scrollbar(
            self.sidebar_host,
            orient="vertical",
            command=self.sidebar_canvas.yview,
        )
        self.sidebar_scrollbar.grid(row=0, column=1, sticky="ns")
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)
        self.sidebar = ttk.Frame(self.sidebar_canvas, padding=self._px(12))
        self.sidebar_window = self.sidebar_canvas.create_window(
            (0, 0), window=self.sidebar, anchor="nw"
        )
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.bind("<Configure>", self._on_sidebar_content_configure)
        self.sidebar_canvas.bind("<Configure>", self._on_sidebar_canvas_configure)
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
        self.root.bind("<MouseWheel>", self._on_sidebar_mousewheel, add="+")
        self.root.bind("<Button-4>", self._on_sidebar_mousewheel, add="+")
        self.root.bind("<Button-5>", self._on_sidebar_mousewheel, add="+")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

        self._bind_text_editing_shortcuts()
        self._bind_configured_shortcuts()

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="New",
            accelerator=self._shortcut_label("new_session"),
            command=self.new_session,
        )
        file_menu.add_command(
            label="Open…",
            accelerator=self._shortcut_label("open_session"),
            command=self.open_session,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Save",
            accelerator=self._shortcut_label("save_session"),
            command=self.save,
        )
        file_menu.add_command(
            label="Save As…",
            accelerator=self._shortcut_label("save_session_as"),
            command=self.save_as,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export blockMeshDict…",
            accelerator=self._shortcut_label("export_block_mesh_dict"),
            command=self.toggle_export_mode,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)

        self.edit_menu = tk.Menu(menu, tearoff=False)
        self.edit_menu.add_command(
            label="Undo",
            accelerator=self._shortcut_label("undo"),
            command=self.undo,
        )
        self.undo_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_command(
            label="Redo",
            accelerator=self._shortcut_label("redo"),
            command=self.redo,
        )
        self.redo_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(
            label="Add standalone vertex",
            accelerator=self._shortcut_label("add_vertex"),
            command=self.start_vertex_placement,
        )
        self.edit_menu.add_command(
            label="New block from 4 vertices",
            accelerator=self._shortcut_label("new_block"),
            command=self.start_block_from_vertices,
        )
        self.edit_menu.add_command(
            label="Set boundaries",
            accelerator=self._shortcut_label("set_boundaries"),
            command=self.toggle_boundary_mode,
        )
        self.edit_menu.add_command(
            label="Project onto geometry",
            accelerator=self._shortcut_label("project"),
            command=self.start_projection,
        )
        self.edit_menu.add_command(
            label="Split selected edge",
            accelerator=self._shortcut_label("split_edge"),
            command=self.start_edge_split,
        )
        self.split_edge_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_command(
            label="Combine blocks across edge",
            accelerator=self._shortcut_label("combine_blocks"),
            command=self.combine_selected_blocks,
        )
        self.combine_blocks_menu_index = self.edit_menu.index("end")
        self.edit_menu.add_command(
            label="Delete selected entity",
            accelerator=self._shortcut_label("delete_edge"),
            command=self.delete_selected_entity,
        )
        self.delete_edge_menu_index = self.edit_menu.index("end")
        menu.add_cascade(label="Edit", menu=self.edit_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(
            label="Fit topology",
            accelerator=self._shortcut_label("fit_view"),
            command=self.fit_view,
        )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Block mesh",
            variable=self.show_block_mesh_var,
            command=self.apply_visibility,
        )
        view_menu.add_checkbutton(
            label="Geometry",
            accelerator=self._shortcut_label("toggle_geometry"),
            variable=self.show_geometry_var,
            command=self.apply_visibility,
        )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Mesh subdivision nodes",
            variable=self.show_edge_nodes_var,
            command=self.apply_visibility,
        )
        view_menu.add_checkbutton(
            label="Edge interpolation points",
            variable=self.show_edge_interpolation_points_var,
            command=self.apply_visibility,
        )
        view_menu.add_separator()
        scale_menu = tk.Menu(view_menu, tearoff=False)
        scale_choices = [
            ("System (automatic)", "auto"),
            ("125% of system", "1.25"),
            ("150% of system", "1.5"),
            ("200% of system", "2.0"),
        ]
        configured_scale = self.ui_scale_var.get()
        if configured_scale not in {value for _label, value in scale_choices}:
            scale_choices.append((
                f"Configured ({float(configured_scale) * 100:g}% of system)",
                configured_scale,
            ))
        for label, value in scale_choices:
            scale_menu.add_radiobutton(
                label=label,
                variable=self.ui_scale_var,
                value=value,
                command=self.apply_ui_scale,
            )
        view_menu.add_cascade(label="UI scale", menu=scale_menu)
        menu.add_cascade(label="View", menu=view_menu)
        self.root.configure(menu=menu)

    def _shortcut_label(self, action: str) -> str:
        shortcuts = self.preferences.shortcuts[action]
        return shortcuts[0] if shortcuts else ""

    def _bind_configured_shortcuts(self) -> None:
        handlers: dict[str, Callable[[tk.Event], str | None]] = {
            "new_session": lambda _event: self._shortcut(self.new_session),
            "open_session": lambda _event: self._shortcut(self.open_session),
            "save_session": lambda _event: self._shortcut(self.save),
            "save_session_as": lambda _event: self._shortcut(self.save_as),
            "export_block_mesh_dict": self._export_shortcut,
            "undo": lambda _event: self._shortcut(self.undo),
            "redo": lambda _event: self._shortcut(self.redo),
            "delete_edge": self._delete_shortcut,
            "split_edge": self._split_shortcut,
            "execute_split": self._execute_split_shortcut,
            "combine_blocks": self._combine_shortcut,
            "new_block": self._new_block_shortcut,
            "add_vertex": self._new_vertex_shortcut,
            "set_boundaries": self._boundary_shortcut,
            "project": self._projection_shortcut,
            "toggle_geometry": self._geometry_visibility_shortcut,
            "cancel": self._escape_shortcut,
            "fit_view": lambda _event: self._shortcut(self.fit_view),
        }
        for action, shortcuts in self.preferences.shortcuts.items():
            handler = handlers[action]
            for shortcut in shortcuts:
                for sequence in shortcut_to_tk_sequences(shortcut):
                    self.root.bind(sequence, handler)

    def _bind_text_editing_shortcuts(self) -> None:
        sequences = ["<Control-KeyPress-a>"]
        if sys.platform == "darwin":
            sequences.append("<Command-KeyPress-a>")
        for widget_class in (
            "Entry", "TEntry", "Text", "Spinbox", "TSpinbox",
        ):
            for sequence in sequences:
                self.root.bind_class(
                    widget_class, sequence, self._select_all_text
                )

    @staticmethod
    def _select_all_text(event: tk.Event) -> str:
        widget = event.widget
        if widget.winfo_class() == "Text":
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
            widget.see("insert")
        else:
            try:
                widget.selection_range(0, "end")
                widget.icursor("end")
            except AttributeError:
                # Classic Tk spinboxes expose selection only as Tcl commands.
                widget.tk.call(widget._w, "selection", "range", 0, "end")
                widget.tk.call(widget._w, "icursor", "end")
        return "break"

    def _on_sidebar_content_configure(self, _event: tk.Event) -> None:
        bounds = self.sidebar_canvas.bbox("all")
        if bounds is not None:
            self.sidebar_canvas.configure(scrollregion=bounds)

    def _on_sidebar_canvas_configure(self, event: tk.Event) -> None:
        self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width)

    def _on_sidebar_mousewheel(self, event: tk.Event) -> str | None:
        widget: tk.Misc | None = event.widget
        while widget is not None and widget is not self.sidebar_host:
            widget = getattr(widget, "master", None)
        if widget is None:
            return None
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            direction = -1
        else:
            direction = 1
        self.sidebar_canvas.yview_scroll(direction, "units")
        return "break"

    def _build_sidebar(self) -> None:
        self.sidebar_title = ttk.Label(
            self.sidebar, text="Properties", font=self._font(15, "bold")
        )
        self.sidebar_title.grid(row=0, column=0, sticky="w", pady=(0, 10))

        self.selection_frame = ttk.LabelFrame(
            self.sidebar, text="Selection", padding=self._px(10)
        )
        self.selection_frame.grid(
            row=1, column=0, sticky="new"
        )
        self.selection_frame.columnconfigure(1, weight=1)

        self.sidebar_help = ttk.Label(
            self.sidebar,
            text=(
                "Mouse wheel: zoom\nMiddle/right drag: pan\n"
                "Double-click an exterior edge: add block\n"
                "V: place vertex · N: connect 4 vertices\n"
                "S: split edge · Shift+S: combine\n"
                "B: boundaries · P: project\n"
                "E: export · Esc: cancel"
            ),
            foreground="#52606d",
            justify="left",
        )
        self.sidebar_help.grid(
            row=2, column=0, sticky="sw", pady=(self._px(18), 0)
        )
        self.sidebar.rowconfigure(2, weight=1)

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

        self.sidebar_title.configure(text="Properties")
        self.selection_frame.configure(text="Selection")
        self.selection_frame.grid_configure(row=1, pady=0)
        self.sidebar_help.configure(
            text=(
                "Mouse wheel: zoom\nMiddle/right drag: pan\n"
                "Double-click an exterior edge: add block\n"
                "V: place vertex · N: connect 4 vertices\n"
                "S: split edge · Shift+S: combine\n"
                "B: boundaries · P: project\n"
                "E: export · Esc: cancel"
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

            next_row = 5
            grading = self.model.edge_grading_values(self.selected_edge)
            ttk.Label(
                self.selection_frame,
                text=f"Grading {first} → {second}",
                font=self._font(10, "bold"),
            ).grid(
                row=next_row, column=0, columnspan=2, sticky="w",
                pady=(self._px(10), self._px(3)),
            )
            next_row += 1
            ttk.Checkbutton(
                self.selection_frame,
                text=(
                    f"Propagate to {len(affected)} cell-count-linked edge"
                    f"{'s' if len(affected) != 1 else ''}"
                ),
                variable=self.edge_grading_propagate_var,
            ).grid(
                row=next_row, column=0, columnspan=2, sticky="w",
                pady=(0, self._px(4)),
            )
            next_row += 1
            ttk.Label(self.selection_frame, text="Edge length").grid(
                row=next_row, column=0, sticky="w",
                padx=(0, self._px(8)), pady=self._px(3),
            )
            self.edge_length_var = tk.StringVar(
                value=_display_grading_number(grading.length)
            )
            ttk.Label(
                self.selection_frame,
                textvariable=self.edge_length_var,
                anchor="e",
            ).grid(row=next_row, column=1, sticky="ew", pady=self._px(3))
            next_row += 1
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
                self._grading_field(next_row, label, variable, parameter)
                next_row += 1
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
                row=next_row, column=0, columnspan=2, sticky="w",
                pady=(self._px(5), self._px(2)),
            )
            next_row += 1

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
            text="Apply boundary type",
            command=self.apply_boundary_definition,
        ).grid(row=detail_row, column=0, columnspan=2, sticky="ew")
        detail_row += 1
        ttk.Button(
            self.selection_frame,
            text="Remove boundary",
            command=self.remove_active_boundary,
        ).grid(
            row=detail_row, column=0, columnspan=2, sticky="ew",
            pady=(self._px(6), 0),
        )

    def toggle_boundary_mode(self) -> None:
        self.boundary_mode_active = not self.boundary_mode_active
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
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
        if self.boundary_neighbour_selector is None \
                or self.boundary_type_var is None:
            return
        state = (
            "readonly" if self.boundary_type_var.get() == "cyclic"
            else "disabled"
        )
        self.boundary_neighbour_selector.configure(state=state)

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
        self.status.set(
            f"Set {labels[parameter]} for edge {first} → {second}; "
            f"updated {affected_count} linked edge"
            f"{'s' if affected_count != 1 else ''}."
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
        self.fit_view()
        self._update_property_panel()
        self.status.set(
            f"Added {block.id}. Its outer edge is selected for quick extension."
        )

    def start_block_from_vertices(self) -> None:
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
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

    def redraw(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        self.item_targets.clear()
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self._draw_grid(width, height)
        if not self.show_block_mesh_var.get():
            if self.show_geometry_var.get():
                self._draw_geometry_curves()
            return

        for current in self.model.edges():
            selected = current == self.selected_edge
            projection_selected = current in self.projection_edges
            boundary_name = self.model.edge_boundaries.get(current)
            boundary_color = (
                self.model.boundaries[boundary_name].color
                if boundary_name is not None else None
            )
            if projection_selected:
                color = "#9c36b5"
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
            render_points = self.model.edge_render_points(
                current,
                arc_segments=CURVE_RENDER_SEGMENTS,
                spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
            )
            for world_x, world_y in render_points:
                screen_points.extend(self.world_to_screen(world_x, world_y))
            line = self.canvas.create_line(
                *screen_points,
                fill=color,
                width=self._px(
                    5 if projection_selected or active_boundary_edge
                    else 4 if selected
                    else 3 if boundary_color
                    else 2
                ),
            )
            self.item_targets[line] = ("edge", current)
            if self.show_edge_nodes_var.get():
                self._draw_edge_nodes(current, color)

            midpoint_world = self.model.edge_point(current, 0.5)
            midpoint_x, midpoint_y = self.world_to_screen(*midpoint_world)
            label = self.canvas.create_text(
                midpoint_x,
                midpoint_y - self._px(11),
                text=f"{self.model.edge_cells[current]}",
                fill=boundary_color or ("#9c3d10" if selected else "#52606d"),
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

        for identifier, vertex in self.model.vertices.items():
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

    def _draw_split_marker(self) -> None:
        if self.split_edge_active is None \
                or self.split_edge_active not in self.model.edge_cells:
            return
        world_x, world_y = self.model.edge_point(
            self.split_edge_active, self.split_fraction
        )
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
        for curve_id, curve in self.model.geometry_curves.items():
            selected = curve_id == self.selected_geometry_curve
            projection_selected = curve_id in self.projection_curve_ids
            screen_points: list[float] = []
            for world_point in self.model.geometry_curve_render_points(
                curve_id, samples_per_span=GEOMETRY_SAMPLES_PER_SPAN
            ):
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

            label_x, label_y = self.world_to_screen(
                *self.model.geometry_curve_point(curve_id, 0.5)
            )
            label = self.canvas.create_text(
                label_x,
                label_y - self._px(13),
                text=curve.name,
                fill="#087f5b" if projection_selected else "#006d77",
                font=self._font(
                    9, "bold" if selected or projection_selected else "normal"
                ),
            )
            self.item_targets[label] = ("geometry_curve", curve_id)

            if not curve.show_points:
                continue
            for point_index, (point_x, point_y) in enumerate(curve.points):
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

    def _draw_edge_nodes(self, current: EdgeKey, color: str) -> None:
        cells = self.model.edge_cells[current]
        if cells <= 1:
            return
        stride = max(1, math.ceil((cells - 1) / MAX_VISIBLE_EDGE_MARKERS))
        for index in range(stride, cells, stride):
            ratio = self.model.edge_node_fraction(current, index)
            world_x, world_y = self.model.edge_point(current, ratio)
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
                for x, y in self.model.edge_render_points(
                    current,
                    arc_segments=CURVE_RENDER_SEGMENTS,
                    spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
                ):
                    xs.append(x)
                    ys.append(y)
        if self.show_geometry_var.get():
            for curve_id, curve in self.model.geometry_curves.items():
                for x, y in curve.points:
                    xs.append(x)
                    ys.append(y)
                for x, y in self.model.geometry_curve_render_points(
                    curve_id,
                    samples_per_span=GEOMETRY_SAMPLES_PER_SPAN,
                ):
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
        show_edge_nodes = bool(self.show_edge_nodes_var.get())
        show_edge_interpolation_points = bool(
            self.show_edge_interpolation_points_var.get()
        )
        self.preferences = self.preferences.with_visibility(
            show_block_mesh=show_block_mesh,
            show_geometry=show_geometry,
            show_edge_nodes=show_edge_nodes,
            show_edge_interpolation_points=show_edge_interpolation_points,
        )
        if not show_block_mesh:
            self._clear_split_state()
            self._clear_projection_state()
            self.selected_vertex = None
            self.selected_edge = None
            self.selected_control_point_index = None
            self.block_vertex_selection = None
            self.vertex_placement_active = False
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
            min(
                MAX_ZOOM_PIXELS_PER_UNIT * self.display_scale,
                self.pixels_per_unit * factor,
            ),
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
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.block_vertex_selection = None
        self.vertex_placement_active = False
        self.boundary_mode_active = False
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self.active_boundary_name = None
        self.boundary_button.configure(text="Set boundaries")
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.last_pressed_target = None
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
        self.selected_control_point_index = None
        self.selected_geometry_curve = None
        self.selected_geometry_point_index = None
        self.block_vertex_selection = None
        self.vertex_placement_active = False
        self.boundary_mode_active = False
        self._clear_split_state()
        self._clear_export_mode()
        self._clear_projection_state()
        self.active_boundary_name = next(iter(self.model.boundaries), None)
        self.boundary_button.configure(text="Set boundaries")
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
        self.drag_changed = False
        self.last_pressed_target = None
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
        previous = (
            self.model.z_cells,
            self.model.z_min,
            self.model.z_max,
            self.model.scale,
            self.model.z_min_patch_name,
            self.model.z_min_patch_type,
            self.model.z_max_patch_name,
            self.model.z_max_patch_type,
        )
        try:
            self.model.set_export_settings(
                _positive_integer(self.z_cells_var.get(), "Z cells"),
                float(self.z_min_var.get()),
                float(self.z_max_var.get()),
                float(self.scale_var.get()),
                self.z_min_patch_name_var.get().strip(),
                self.z_min_patch_type_var.get(),
                self.z_max_patch_name_var.get().strip(),
                self.z_max_patch_type_var.get(),
            )
        except (ValueError, TopologyError) as exc:
            self._show_error("Invalid export settings", exc)
            return

        initial_directory = str(self.session_path.parent) if self.session_path else None
        filename = filedialog.asksaveasfilename(
            title="Export OpenFOAM dictionary",
            initialdir=initial_directory,
            initialfile="blockMeshDict",
            filetypes=[("OpenFOAM dictionary", "blockMeshDict"), ("All files", "*")],
        )
        if not filename:
            self.model.set_export_settings(*previous)
            return
        try:
            write_block_mesh_dict(self.model, filename)
        except (OSError, TopologyError) as exc:
            self.model.set_export_settings(*previous)
            self._show_error("Could not export blockMeshDict", exc)
            return
        self._sync_global_values()
        self._commit_edit()
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
        self._clear_split_state()
        self._clear_projection_state()
        self.block_vertex_selection = None
        self.vertex_placement_active = False
        if self.active_boundary_name not in self.model.boundaries:
            self.active_boundary_name = next(iter(self.model.boundaries), None)
        if self.selected_vertex not in self.model.vertices:
            self.selected_vertex = None
        if self.selected_edge not in self.model.edge_cells:
            self.selected_edge = None
            self.selected_control_point_index = None
        if self.selected_geometry_curve not in self.model.geometry_curves:
            self.selected_geometry_curve = None
            self.selected_geometry_point_index = None
        elif self.selected_geometry_curve is not None:
            point_count = len(
                self.model.geometry_curves[self.selected_geometry_curve].points
            )
            if self.selected_geometry_point_index is None:
                self.selected_geometry_point_index = 0
            else:
                self.selected_geometry_point_index = min(
                    self.selected_geometry_point_index, point_count - 1
                )
        self.drag_vertex = None
        self.drag_control_point = None
        self.drag_geometry_point = None
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
        if getattr(self, "export_mode_active", False) \
                or getattr(self, "split_edge_active", None) is not None \
                or getattr(self, "projection_stage", None) is not None:
            return "break"
        self.delete_selected_entity()
        return "break"

    def _new_block_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.start_block_from_vertices()
        return "break"

    def _split_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.start_edge_split()
        return "break"

    def _execute_split_shortcut(self, _event: tk.Event) -> str | None:
        if getattr(self, "split_edge_active", None) is None:
            return None
        self.execute_edge_split()
        return "break"

    def _combine_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.combine_selected_blocks()
        return "break"

    def _new_vertex_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.start_vertex_placement()
        return "break"

    def _boundary_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.toggle_boundary_mode()
        return "break"

    def _projection_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.start_projection()
        return "break"

    def _export_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.toggle_export_mode()
        return "break"

    def _geometry_visibility_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.show_geometry_var.set(not self.show_geometry_var.get())
        self.apply_visibility()
        return "break"

    def _escape_shortcut(self, _event: tk.Event) -> str | None:
        if getattr(self, "split_edge_active", None) is not None:
            self.cancel_edge_split()
            return "break"
        if getattr(self, "export_mode_active", False):
            self.toggle_export_mode()
            return "break"
        if getattr(self, "projection_stage", None) is not None:
            self.cancel_projection()
            return "break"
        if self.boundary_mode_active:
            self.toggle_boundary_mode()
            return "break"
        if self.vertex_placement_active:
            self.cancel_vertex_placement()
            return "break"
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
        self.z_min_patch_name_var.set(self.model.z_min_patch_name)
        self.z_min_patch_type_var.set(self.model.z_min_patch_type)
        self.z_max_patch_name_var.set(self.model.z_max_patch_name)
        self.z_max_patch_type_var.set(self.model.z_max_patch_type)

    def _sync_property_values(self) -> None:
        if self.selected_vertex is not None and self.vertex_x_var is not None \
                and self.vertex_y_var is not None:
            vertex = self.model.vertices[self.selected_vertex]
            self.vertex_x_var.set(_display_number(vertex.x))
            self.vertex_y_var.set(_display_number(vertex.y))
        if self.selected_edge is not None and self.edge_cells_var is not None:
            self.edge_cells_var.set(str(self.model.edge_cells[self.selected_edge]))
        edge_point_count_var = getattr(self, "edge_point_count_var", None)
        if self.selected_edge is not None and edge_point_count_var is not None:
            edge_point_count_var.set(str(
                len(self.model.edge_control_points(self.selected_edge))
            ))
        if self.selected_edge is not None \
                and self.edge_length_var is not None \
                and self.edge_cell_ratio_var is not None \
                and self.edge_total_ratio_var is not None \
                and self.edge_start_width_var is not None \
                and self.edge_end_width_var is not None:
            grading = self.model.edge_grading_values(self.selected_edge)
            self.edge_length_var.set(_display_grading_number(grading.length))
            self.edge_cell_ratio_var.set(
                _display_grading_number(grading.cell_ratio)
            )
            self.edge_total_ratio_var.set(
                _display_grading_number(grading.total_ratio)
            )
            self.edge_start_width_var.set(
                _display_grading_number(grading.start_width)
            )
            self.edge_end_width_var.set(
                _display_grading_number(grading.end_width)
            )
        if self.selected_edge is not None \
                and self.selected_control_point_index is not None \
                and self.point_x_var is not None and self.point_y_var is not None:
            points = self.model.edge_control_points(self.selected_edge)
            point_x, point_y = points[self.selected_control_point_index]
            self.point_x_var.set(_display_number(point_x))
            self.point_y_var.set(_display_number(point_y))
        selected_geometry_curve = getattr(
            self, "selected_geometry_curve", None
        )
        selected_geometry_point_index = getattr(
            self, "selected_geometry_point_index", None
        )
        if selected_geometry_curve is not None \
                and selected_geometry_point_index is not None:
            curve = self.model.geometry_curves[selected_geometry_curve]
            if getattr(self, "geometry_name_var", None) is not None:
                self.geometry_name_var.set(curve.name)
            if getattr(self, "geometry_point_x_var", None) is not None \
                    and getattr(self, "geometry_point_y_var", None) is not None:
                point_x, point_y = curve.points[
                    selected_geometry_point_index
                ]
                self.geometry_point_x_var.set(_display_number(point_x))
                self.geometry_point_y_var.set(_display_number(point_y))

    def _show_error(self, title: str, error: Exception) -> None:
        self.status.set(str(error))
        messagebox.showerror(title, str(error), parent=self.root)


def _nearest_edge_fraction(
    model: MeshModel, edge: EdgeKey, x: float, y: float
) -> float:
    """Return the edge parameter closest to a 2D pointer location."""
    current = edge_key(*edge)
    if model.edge_type(current) == "line":
        first = model.vertices[current[0]]
        second = model.vertices[current[1]]
        dx = second.x - first.x
        dy = second.y - first.y
        length_squared = dx * dx + dy * dy
        if length_squared == 0.0:
            return 0.5
        return min(1.0, max(
            0.0,
            ((x - first.x) * dx + (y - first.y) * dy) / length_squared,
        ))

    point_count = len(model.edge_control_points(current))
    samples = min(
        MAX_SPLIT_PICK_SAMPLES,
        max(64, (point_count + 1) * 16),
    )

    def distance_squared(fraction: float) -> float:
        point_x, point_y = model.edge_point(current, fraction)
        return (point_x - x) ** 2 + (point_y - y) ** 2

    best_index = min(
        range(samples + 1),
        key=lambda index: distance_squared(index / samples),
    )
    low = max(0.0, (best_index - 1) / samples)
    high = min(1.0, (best_index + 1) / samples)
    for _ in range(36):
        first_third = low + (high - low) / 3.0
        second_third = high - (high - low) / 3.0
        if distance_squared(first_third) <= distance_squared(second_third):
            high = second_third
        else:
            low = first_third
    return (low + high) / 2.0


def _split_fraction_from_text(value: str) -> float:
    """Parse a user-facing percentage into an edge fraction."""
    text = value.strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        percentage = float(text)
    except ValueError as exc:
        raise ValueError("Current split must be a percentage between 0 and 100") \
            from exc
    if not math.isfinite(percentage) or not 0.0 < percentage < 100.0:
        raise ValueError("Current split must be strictly between 0 and 100 percent")
    return percentage / 100.0


def _display_split_percentage(fraction: float) -> str:
    return format(fraction * 100.0, ".12g")


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


def _visible_control_point_indices(
    count: int,
    selected_index: int | None,
) -> tuple[int, ...]:
    """Bound dense canvas markers while retaining the selected point."""
    if count <= 0:
        return ()
    stride = max(1, math.ceil(count / MAX_VISIBLE_CONTROL_POINTS))
    indices = set(range(0, count, stride))
    indices.add(count - 1)
    if selected_index is not None and 0 <= selected_index < count:
        indices.add(selected_index)
    return tuple(sorted(indices))


def _display_grading_number(value: float) -> str:
    if value == 0.0:
        return "0"
    return format(value, ".12g")


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
