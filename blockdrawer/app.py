"""Tkinter graphical interface for BlockDrawer."""

from __future__ import annotations

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
from .canvas import CanvasControllerMixin
from .editing import EditingControllerMixin
from .foam import write_block_mesh_dict
from .history import ModelHistory
from .model import EdgeKey, MeshModel, TopologyError
from .panels import PropertiesPanelMixin
from .preview import MeshPreviewCache
from .projection import DEFAULT_FIT_MAX_POINTS, FIT_RELATIVE_TOLERANCE
from .render_cache import RenderPathCache
from .session import SessionError, load_session, save_session
from .ui_helpers import (
    display_grading_number as _display_grading_number,
    display_number as _display_number,
    is_text_input_class as _is_text_input_class,
    positive_integer as _positive_integer,
    system_display_scale as _system_display_scale,
)


APP_NAME = "BlockDrawer"


class BlockDrawerApp(
    PropertiesPanelMixin,
    EditingControllerMixin,
    CanvasControllerMixin,
):
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
        self._viewport_redraw_after_id: str | None = None

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
        self.show_mesh_preview_var = tk.BooleanVar(
            value=self.preferences.show_mesh_preview
        )
        self.mesh_preview_coarsening_var = tk.StringVar(
            value=str(self.preferences.preview_coarsening)
        )
        self.mesh_preview_info_var = tk.StringVar(
            value="Preview has not been built yet."
        )
        # One entry guarantees instant re-entry without retaining several
        # potentially large sampled grids after resolution or topology changes.
        self.mesh_preview_cache = MeshPreviewCache(capacity=1)
        self.render_path_cache = RenderPathCache()
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
        self.canvas.bind("<Configure>", self._on_canvas_configure)

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
        view_menu.add_checkbutton(
            label="Mesh preview",
            accelerator=self._shortcut_label("toggle_mesh_preview"),
            variable=self.show_mesh_preview_var,
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
            "toggle_mesh_preview": self._mesh_preview_shortcut,
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
                "B: boundaries · P: project · M: preview\n"
                "E: export · Esc: cancel"
            ),
            foreground="#52606d",
            justify="left",
        )
        self.sidebar_help.grid(
            row=2, column=0, sticky="sw", pady=(self._px(18), 0)
        )
        self.sidebar.rowconfigure(2, weight=1)

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

    def _mesh_preview_shortcut(self, event: tk.Event) -> str | None:
        if _is_text_input_class(event.widget.winfo_class()):
            return None
        self.show_mesh_preview_var.set(not self.show_mesh_preview_var.get())
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
