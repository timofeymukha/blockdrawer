from types import SimpleNamespace
import unittest
from unittest.mock import patch

from blockdrawer.app import BlockDrawerApp
from blockdrawer.ui_helpers import (
    MAX_ZOOM_PIXELS_PER_UNIT,
    is_text_input_class as _is_text_input_class,
    nearest_edge_fraction as _nearest_edge_fraction,
    scaled_named_font_size as _scaled_named_font_size,
    split_fraction_from_text as _split_fraction_from_text,
    system_display_scale as _system_display_scale,
    visible_control_point_indices as _visible_control_point_indices,
)
from blockdrawer.config import default_config
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.preview import MeshPreviewCache
from blockdrawer.render_cache import RenderPathCache


class FakeStringVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeEntry:
    def __init__(self) -> None:
        self.bindings = {}

    def bind(self, sequence, callback) -> None:
        self.bindings[sequence] = callback


class FakeRoot:
    def __init__(self) -> None:
        self.class_bindings = {}

    def bind_class(self, widget_class, sequence, callback) -> None:
        self.class_bindings[(widget_class, sequence)] = callback


class FakeSelectableEntry:
    def __init__(self) -> None:
        self.selection = None
        self.cursor = None

    def winfo_class(self) -> str:
        return "TEntry"

    def selection_range(self, first, last) -> None:
        self.selection = (first, last)

    def icursor(self, index) -> None:
        self.cursor = index


class CountingSizeCanvas:
    def __init__(self, width: int = 800, height: int = 600) -> None:
        self.width = width
        self.height = height
        self.width_calls = 0
        self.height_calls = 0

    def winfo_width(self) -> int:
        self.width_calls += 1
        return self.width

    def winfo_height(self) -> int:
        self.height_calls += 1
        return self.height


class RecordingCanvas(CountingSizeCanvas):
    def __init__(self, width: int = 800, height: int = 600) -> None:
        super().__init__(width, height)
        self.created: list[str] = []
        self.texts: list[str] = []

    def delete(self, _tag: str) -> None:
        self.created.clear()
        self.texts.clear()

    def _create(self, kind: str) -> int:
        self.created.append(kind)
        return len(self.created)

    def create_line(self, *_args, **_options) -> int:
        return self._create("line")

    def create_text(self, *_args, **options) -> int:
        self.texts.append(str(options.get("text", "")))
        return self._create("text")

    def create_oval(self, *_args, **_options) -> int:
        return self._create("oval")

    def create_rectangle(self, *_args, **_options) -> int:
        return self._create("rectangle")

    def create_polygon(self, *_args, **_options) -> int:
        return self._create("polygon")


class FakeAfterRoot:
    def __init__(self) -> None:
        self.scheduled: dict[str, tuple[int, object]] = {}
        self.after_calls = 0
        self.cancelled: list[str] = []

    def after(self, delay: int, callback) -> str:
        identifier = f"after#{self.after_calls}"
        self.after_calls += 1
        self.scheduled[identifier] = (delay, callback)
        return identifier

    def after_cancel(self, identifier: str) -> None:
        self.cancelled.append(identifier)
        self.scheduled.pop(identifier, None)

    def run(self, identifier: str) -> None:
        _delay, callback = self.scheduled.pop(identifier)
        callback()


class DpiScalingTests(unittest.TestCase):
    def test_adding_block_preserves_viewport_without_fitting(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.selected_edge = edge_key("v1", "v2")
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.view_x = 7.5
        app.view_y = -3.25
        app.pixels_per_unit = 1234.0
        app.status = FakeStringVar()
        redraws: list[bool] = []
        app._commit_edit = lambda: None
        app._update_property_panel = lambda: None
        app.redraw = lambda: redraws.append(True)
        app.fit_view = lambda: self.fail("adding a block must not fit the view")
        app._show_error = lambda _title, error: self.fail(str(error))

        app.add_selected_block()

        self.assertEqual(len(app.model.blocks), 2)
        self.assertEqual(redraws, [True])
        self.assertEqual(app.view_x, 7.5)
        self.assertEqual(app.view_y, -3.25)
        self.assertEqual(app.pixels_per_unit, 1234.0)

    def test_redraw_culls_topology_completely_outside_viewport(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeAfterRoot()
        app._viewport_redraw_after_id = None
        app.canvas = RecordingCanvas()
        app.item_targets = {}
        app.model = MeshModel()
        app.render_path_cache = RenderPathCache()
        app.view_x = 100.0
        app.view_y = 100.0
        app.pixels_per_unit = 100.0
        app.display_scale = 1.0
        app.ui_scale_multiplier = 1.0
        app.default_font_family = "TkDefaultFont"
        app.show_mesh_preview_var = FakeStringVar(False)
        app.show_block_mesh_var = FakeStringVar(True)
        app.show_geometry_var = FakeStringVar(False)
        app.show_edge_nodes_var = FakeStringVar(True)
        app.show_edge_interpolation_points_var = FakeStringVar(True)
        app.selected_edge = None
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.projection_edges = []
        app.projection_vertex_ids = []
        app.block_vertex_selection = None
        app.boundary_mode_active = False
        app.active_boundary_name = None
        app.split_edge_active = None
        app._draw_grid = lambda _width, _height: None

        app.redraw()

        self.assertEqual(app.canvas.created, [])

        app.view_x = 0.5
        app.view_y = 0.5
        app.redraw()
        self.assertGreater(len(app.canvas.created), 0)

    def test_viewport_redraw_requests_are_coalesced_per_frame(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeAfterRoot()
        app._viewport_redraw_after_id = None
        redraws: list[bool] = []
        app.redraw = lambda: redraws.append(True)

        for _index in range(20):
            app._request_viewport_redraw()

        self.assertEqual(app.root.after_calls, 1)
        self.assertEqual(redraws, [])
        after_id = app._viewport_redraw_after_id
        self.assertIsNotNone(after_id)
        assert after_id is not None
        self.assertEqual(
            app.root.scheduled[after_id][0],
            app.VIEWPORT_REDRAW_INTERVAL_MS,
        )

        app.root.run(after_id)

        self.assertEqual(redraws, [True])
        self.assertIsNone(app._viewport_redraw_after_id)

    def test_pending_viewport_redraw_can_be_cancelled(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeAfterRoot()
        app._viewport_redraw_after_id = None
        app._request_viewport_redraw()
        after_id = app._viewport_redraw_after_id
        assert after_id is not None

        app._cancel_viewport_redraw()

        self.assertEqual(app.root.cancelled, [after_id])
        self.assertIsNone(app._viewport_redraw_after_id)
        self.assertNotIn(after_id, app.root.scheduled)

    def test_wheel_events_accumulate_before_one_scheduled_redraw(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeAfterRoot()
        app.canvas = CountingSizeCanvas()
        app._viewport_redraw_after_id = None
        app.display_scale = 1.0
        app.pixels_per_unit = 100.0
        app.view_x = 0.0
        app.view_y = 0.0
        redraws: list[bool] = []
        app.redraw = lambda: redraws.append(True)
        event = SimpleNamespace(x=400, y=300, delta=120, num=None)

        for _index in range(5):
            app._on_mousewheel(event)

        self.assertAlmostEqual(app.pixels_per_unit, 100.0 * 1.15 ** 5)
        self.assertEqual(app.root.after_calls, 1)
        self.assertEqual(app.canvas.width_calls, 1)
        self.assertEqual(app.canvas.height_calls, 1)
        self.assertEqual(redraws, [])

        after_id = app._viewport_redraw_after_id
        assert after_id is not None
        app.root.run(after_id)
        self.assertEqual(redraws, [True])

    def test_canvas_transform_reuses_dimensions_for_repeated_points(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.canvas = CountingSizeCanvas()
        app.view_x = 0.5
        app.view_y = -0.25
        app.pixels_per_unit = 200.0

        screen_points = [
            app.world_to_screen(index / 10.0, index / 20.0)
            for index in range(100)
        ]
        world_points = [
            app.screen_to_world(*point) for point in screen_points
        ]

        self.assertEqual(app.canvas.width_calls, 1)
        self.assertEqual(app.canvas.height_calls, 1)
        for index, point in enumerate(world_points):
            self.assertAlmostEqual(point[0], index / 10.0)
            self.assertAlmostEqual(point[1], index / 20.0)

        app.view_x = 1.0
        app.world_to_screen(0.0, 0.0)
        self.assertEqual(app.canvas.width_calls, 2)
        self.assertEqual(app.canvas.height_calls, 2)

    def test_dense_control_point_markers_are_decimated_but_keep_selection(
        self,
    ) -> None:
        indices = _visible_control_point_indices(10_000, 9_998)

        self.assertLessEqual(len(indices), 252)
        self.assertIn(0, indices)
        self.assertIn(9_999, indices)
        self.assertIn(9_998, indices)

    def test_zoom_ceiling_supports_dense_geometry_inspection(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.display_scale = 1.0
        app.pixels_per_unit = MAX_ZOOM_PIXELS_PER_UNIT * 0.99
        app.view_x = 0.0
        app.view_y = 0.0
        app.canvas = SimpleNamespace(
            winfo_width=lambda: 800,
            winfo_height=lambda: 600,
        )
        redraw_requests: list[bool] = []
        app._request_viewport_redraw = lambda: redraw_requests.append(True)

        app._on_mousewheel(SimpleNamespace(x=400, y=300, delta=120, num=None))

        self.assertEqual(app.pixels_per_unit, MAX_ZOOM_PIXELS_PER_UNIT)
        self.assertEqual(redraw_requests, [True])

    def test_ctrl_a_selects_all_text_in_entry_and_stops_propagation(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeRoot()
        entry = FakeSelectableEntry()

        app._bind_text_editing_shortcuts()
        callback = app.root.class_bindings[(
            "TEntry", "<Control-KeyPress-a>"
        )]
        result = callback(SimpleNamespace(widget=entry))

        self.assertEqual(entry.selection, (0, "end"))
        self.assertEqual(entry.cursor, "end")
        self.assertEqual(result, "break")

    def test_entry_confirmation_accepts_main_and_keypad_enter(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        entry = FakeEntry()
        calls: list[bool] = []

        app._bind_entry_confirmation(entry, lambda: calls.append(True))

        self.assertEqual(set(entry.bindings), {"<Return>", "<KP_Enter>"})
        self.assertEqual(entry.bindings["<Return>"](SimpleNamespace()), "break")
        self.assertEqual(
            entry.bindings["<KP_Enter>"](SimpleNamespace()), "break"
        )
        self.assertEqual(calls, [True, True])

    def test_windows_and_x11_use_96_dpi_as_unscaled_baseline(self) -> None:
        self.assertAlmostEqual(_system_display_scale(96.0 / 72.0, "win32"), 1.0)
        self.assertAlmostEqual(_system_display_scale(192.0 / 72.0, "win32"), 2.0)
        self.assertAlmostEqual(_system_display_scale(144.0 / 72.0, "linux"), 1.5)

    def test_macos_uses_logical_point_baseline(self) -> None:
        self.assertEqual(_system_display_scale(1.0, "darwin"), 1.0)
        self.assertEqual(_system_display_scale(2.0, "darwin"), 1.0)

    def test_bad_low_dpi_never_shrinks_interface(self) -> None:
        self.assertEqual(_system_display_scale(0.5, "linux"), 1.0)

    def test_pixel_fonts_receive_system_and_manual_scaling(self) -> None:
        self.assertEqual(_scaled_named_font_size(-12, 2.0, 1.0), -24)
        self.assertEqual(_scaled_named_font_size(-12, 2.0, 1.5), -36)

    def test_point_fonts_only_need_manual_scaling(self) -> None:
        self.assertEqual(_scaled_named_font_size(10, 2.0, 1.0), 10)
        self.assertEqual(_scaled_named_font_size(10, 2.0, 1.5), 15)

    def test_delete_shortcut_focus_guard_recognizes_input_widgets(self) -> None:
        for widget_class in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox"):
            self.assertTrue(_is_text_input_class(widget_class))
        self.assertFalse(_is_text_input_class("Canvas"))
        self.assertFalse(_is_text_input_class("TButton"))

    def test_delete_shortcut_removes_selected_geometry_curve(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.selected_geometry_curve = "g0"
        app.selected_geometry_point_index = 1
        calls: list[str] = []
        app.delete_geometry_curve = lambda: calls.append("curve")
        app.delete_selected_edge = lambda: calls.append("edge")
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")

        result = app._delete_shortcut(SimpleNamespace(widget=canvas))

        self.assertEqual(result, "break")
        self.assertEqual(calls, ["curve"])

    def test_g_shortcut_toggles_geometry_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.show_geometry_var = FakeStringVar(True)
        calls: list[bool] = []
        app.apply_visibility = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        result = app._geometry_visibility_shortcut(
            SimpleNamespace(widget=canvas)
        )

        self.assertEqual(result, "break")
        self.assertFalse(app.show_geometry_var.get())
        self.assertEqual(calls, [True])
        self.assertIsNone(app._geometry_visibility_shortcut(
            SimpleNamespace(widget=entry)
        ))
        self.assertEqual(calls, [True])

    def test_m_shortcut_toggles_mesh_preview_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.show_mesh_preview_var = FakeStringVar(False)
        calls: list[bool] = []
        app.apply_visibility = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        result = app._mesh_preview_shortcut(SimpleNamespace(widget=canvas))

        self.assertEqual(result, "break")
        self.assertTrue(app.show_mesh_preview_var.get())
        self.assertEqual(calls, [True])
        self.assertIsNone(app._mesh_preview_shortcut(
            SimpleNamespace(widget=entry)
        ))
        self.assertEqual(calls, [True])

    def test_v_shortcut_starts_vertex_placement_only_from_canvas(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.start_vertex_placement = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        result = app._new_vertex_shortcut(
            SimpleNamespace(widget=canvas, state=0)
        )

        self.assertEqual(result, "break")
        self.assertEqual(calls, [True])
        self.assertIsNone(app._new_vertex_shortcut(
            SimpleNamespace(widget=entry, state=0)
        ))
        self.assertEqual(app._new_vertex_shortcut(
            SimpleNamespace(widget=canvas, state=0x0004)
        ), "break")
        self.assertEqual(calls, [True, True])

    def test_b_shortcut_toggles_boundaries_only_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.toggle_boundary_mode = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        result = app._boundary_shortcut(
            SimpleNamespace(widget=canvas, state=0)
        )

        self.assertEqual(result, "break")
        self.assertEqual(calls, [True])
        self.assertIsNone(app._boundary_shortcut(
            SimpleNamespace(widget=entry, state=0)
        ))
        self.assertEqual(app._boundary_shortcut(
            SimpleNamespace(widget=canvas, state=0x0004)
        ), "break")
        self.assertEqual(calls, [True, True])

    def test_e_shortcut_toggles_export_only_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.toggle_export_mode = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        self.assertEqual(
            app._export_shortcut(SimpleNamespace(widget=canvas)), "break"
        )
        self.assertEqual(calls, [True])
        self.assertIsNone(
            app._export_shortcut(SimpleNamespace(widget=entry))
        )
        self.assertEqual(calls, [True])

    def test_s_shortcut_starts_split_only_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.start_edge_split = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        self.assertEqual(
            app._split_shortcut(SimpleNamespace(widget=canvas)), "break"
        )
        self.assertEqual(calls, [True])
        self.assertIsNone(
            app._split_shortcut(SimpleNamespace(widget=entry))
        )
        self.assertEqual(calls, [True])

    def test_split_pointer_finds_nearest_line_and_arc_fraction(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")

        self.assertAlmostEqual(
            _nearest_edge_fraction(model, selected, 0.73, -0.4), 0.73
        )

        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        point = model.edge_point(selected, 0.31)
        self.assertAlmostEqual(
            _nearest_edge_fraction(model, selected, *point),
            0.31,
            places=6,
        )

    def test_split_percentage_accepts_exact_values_and_optional_suffix(self) -> None:
        self.assertEqual(_split_fraction_from_text("37.125"), 0.37125)
        self.assertEqual(_split_fraction_from_text(" 12.5% "), 0.125)
        for value in ("", "half", "0", "100", "nan", "inf"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "between|strictly"
            ):
                _split_fraction_from_text(value)

    def test_execute_split_shortcut_only_applies_in_split_mode(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.execute_edge_split = lambda: calls.append(True)
        app.split_edge_active = None

        self.assertIsNone(
            app._execute_split_shortcut(SimpleNamespace())
        )
        app.split_edge_active = edge_key("v0", "v1")
        self.assertEqual(
            app._execute_split_shortcut(SimpleNamespace()), "break"
        )
        self.assertEqual(calls, [True])

    def test_shift_s_combines_only_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.combine_selected_blocks = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        self.assertEqual(
            app._combine_shortcut(SimpleNamespace(widget=canvas)), "break"
        )
        self.assertEqual(calls, [True])
        self.assertIsNone(
            app._combine_shortcut(SimpleNamespace(widget=entry))
        )
        self.assertEqual(calls, [True])

    def test_combining_selected_blocks_commits_once_and_selects_joined_edge(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        split = app.model.split_edge(edge_key("v0", "v1"), 0.4)
        app.selected_edge = split.cut_edges[0]
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.split_edge_active = None
        app.export_mode_active = False
        app.boundary_mode_active = False
        app.projection_stage = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app.combine_selected_blocks()

        self.assertEqual(commits, [True])
        self.assertEqual(len(app.model.blocks), 1)
        self.assertIsNotNone(app.selected_edge)
        self.assertIn(app.selected_edge, app.model.edge_cells)

    def test_execute_split_uses_exact_panel_percentage(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.split_edge_active = edge_key("v0", "v1")
        app.split_fraction = 0.5
        app.split_fraction_var = FakeStringVar("31.875")
        applied: list[float] = []
        app._finish_edge_split = lambda: applied.append(app.split_fraction)

        app.execute_edge_split()

        self.assertEqual(applied, [0.31875])

    def test_releasing_split_marker_only_finishes_placement(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.drag_split_marker = True
        app.status = FakeStringVar()
        executions: list[bool] = []
        app._finish_edge_split = lambda: executions.append(True)

        app._on_left_release(SimpleNamespace())

        self.assertFalse(app.drag_split_marker)
        self.assertEqual(executions, [])
        self.assertIn("Split location set", app.status.get())

    def test_finishing_split_commits_one_edit_and_selects_cut_edge(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.split_edge_active = edge_key("v0", "v1")
        app.split_fraction = 0.4
        app.split_fraction_var = None
        app.split_cells_var = None
        app.drag_split_marker = False
        app.selected_vertex = None
        app.selected_edge = app.split_edge_active
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app._finish_edge_split()

        self.assertEqual(commits, [True])
        self.assertEqual(len(app.model.blocks), 2)
        self.assertIsNone(app.split_edge_active)
        self.assertIsNotNone(app.selected_edge)
        self.assertEqual(
            len(app.model.edge_occurrences()[app.selected_edge]), 2
        )

    def test_export_applies_panel_settings_only_after_destination_is_chosen(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.session_path = None
        app.z_cells_var = FakeStringVar("3")
        app.z_min_var = FakeStringVar("-0.2")
        app.z_max_var = FakeStringVar("0.4")
        app.scale_var = FakeStringVar("0.001")
        app.z_min_patch_name_var = FakeStringVar("front")
        app.z_min_patch_type_var = FakeStringVar("cyclic")
        app.z_max_patch_name_var = FakeStringVar("back")
        app.z_max_patch_type_var = FakeStringVar("wall")
        app.status = FakeStringVar()
        app._sync_global_values = lambda: None
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)

        with patch(
            "blockdrawer.app.filedialog.asksaveasfilename",
            return_value="/tmp/blockMeshDict",
        ), patch("blockdrawer.app.write_block_mesh_dict") as writer:
            app.export()

        writer.assert_called_once_with(app.model, "/tmp/blockMeshDict")
        self.assertEqual(app.model.z_cells, 3)
        self.assertEqual(app.model.z_min_patch_type, "cyclic")
        self.assertEqual(app.model.z_max_patch_type, "cyclic")
        self.assertEqual(commits, [True])

        app.z_cells_var.set("9")
        with patch(
            "blockdrawer.app.filedialog.asksaveasfilename", return_value=""
        ), patch("blockdrawer.app.write_block_mesh_dict") as writer:
            app.export()

        writer.assert_not_called()
        self.assertEqual(app.model.z_cells, 3)
        self.assertEqual(commits, [True])

    def test_z_patch_type_selector_enters_and_leaves_cyclic_as_a_pair(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.z_min_patch_type_var = FakeStringVar("wall")
        app.z_max_patch_type_var = FakeStringVar("patch")

        app.z_min_patch_type_var.set("cyclic")
        app._z_patch_type_selected(
            SimpleNamespace(widget=SimpleNamespace(get=lambda: "cyclic"))
        )
        self.assertEqual(app.z_min_patch_type_var.get(), "cyclic")
        self.assertEqual(app.z_max_patch_type_var.get(), "cyclic")

        app.z_max_patch_type_var.set("empty")
        app._z_patch_type_selected(
            SimpleNamespace(widget=SimpleNamespace(get=lambda: "empty"))
        )
        self.assertEqual(app.z_min_patch_type_var.get(), "empty")
        self.assertEqual(app.z_max_patch_type_var.get(), "empty")

        app.z_min_patch_type_var.set("wall")
        app._z_patch_type_selected(
            SimpleNamespace(widget=SimpleNamespace(get=lambda: "wall"))
        )
        self.assertEqual(app.z_min_patch_type_var.get(), "wall")
        self.assertEqual(app.z_max_patch_type_var.get(), "empty")

    def test_boundary_type_and_neighbour_selections_apply_immediately(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        for name in ("inlet", "outlet", "alternate"):
            app.model.add_boundary(name)
        app.active_boundary_name = "inlet"
        app.boundary_type_var = FakeStringVar("wall")
        app.boundary_neighbour_var = FakeStringVar("")
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app._boundary_type_selected(SimpleNamespace())

        self.assertEqual(app.model.boundaries["inlet"].kind, "wall")
        self.assertEqual(commits, [True])

        app.boundary_type_var.set("cyclic")
        app.boundary_neighbour_var.set("outlet")
        app._boundary_type_selected(SimpleNamespace())

        self.assertEqual(
            app.model.boundaries["inlet"].neighbour_patch, "outlet"
        )
        self.assertEqual(
            app.model.boundaries["outlet"].neighbour_patch, "inlet"
        )

        app.boundary_neighbour_var.set("alternate")
        app._boundary_neighbour_selected(SimpleNamespace())

        self.assertEqual(
            app.model.boundaries["inlet"].neighbour_patch, "alternate"
        )
        self.assertEqual(app.model.boundaries["outlet"].kind, "patch")
        self.assertEqual(commits, [True, True, True])

    def test_p_shortcut_starts_projection_only_outside_text_input(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        calls: list[bool] = []
        app.start_projection = lambda: calls.append(True)
        canvas = SimpleNamespace(winfo_class=lambda: "Canvas")
        entry = SimpleNamespace(winfo_class=lambda: "TEntry")

        self.assertEqual(
            app._projection_shortcut(SimpleNamespace(widget=canvas)),
            "break",
        )
        self.assertEqual(calls, [True])
        self.assertIsNone(
            app._projection_shortcut(SimpleNamespace(widget=entry))
        )
        self.assertEqual(calls, [True])

    def test_escape_cancels_projection_selection(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.projection_stage = "curves"
        calls: list[bool] = []
        app.cancel_projection = lambda: calls.append(True)

        result = app._escape_shortcut(SimpleNamespace())

        self.assertEqual(result, "break")
        self.assertEqual(calls, [True])

    def test_projection_entity_selection_does_not_mix_vertices_and_edges(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.projection_stage = "entities"
        app.projection_entity_kind = None
        app.projection_vertex_ids = []
        app.projection_edges = []
        app.projection_curve_ids = []
        app.projection_fit_var = FakeStringVar(False)
        app.status = FakeStringVar()
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app._toggle_projection_target(("vertex", "v0"))
        app._toggle_projection_target(("edge", edge_key("v0", "v1")))

        self.assertEqual(app.projection_vertex_ids, ["v0"])
        self.assertEqual(app.projection_edges, [])
        self.assertIn("cannot be mixed", app.status.get())

        app._toggle_projection_target(("vertex", "v0"))
        app._toggle_projection_target(("edge", edge_key("v0", "v1")))
        self.assertEqual(app.projection_entity_kind, "edge")
        self.assertEqual(app.projection_edges, [edge_key("v0", "v1")])

    def test_projection_curve_selection_accepts_curve_point_targets(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.projection_stage = "curves"
        app.projection_curve_ids = []
        app.status = FakeStringVar()
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app._toggle_projection_target(("geometry_point", ("g0", 17)))

        self.assertEqual(app.projection_curve_ids, ["g0"])

    def test_projection_hit_testing_reaches_mesh_below_geometry_layer(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.display_scale = 1.0
        app.projection_stage = "entities"
        app.projection_entity_kind = None
        app.item_targets = {
            1: ("vertex", "v0"),
            2: ("geometry_curve", "g0"),
        }
        app.canvas = SimpleNamespace(
            find_overlapping=lambda *_bounds: (1, 2)
        )

        target = app._projection_target_at(100, 100)

        self.assertEqual(target, ("vertex", "v0"))

    def test_applying_projection_commits_once_and_leaves_mode(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        curve = app.model.add_geometry_curve(((-1.0, -0.5), (2.0, -0.5)))
        app.projection_stage = "curves"
        app.projection_entity_kind = "vertex"
        app.projection_vertex_ids = ["v0"]
        app.projection_edges = []
        app.projection_curve_ids = [curve.id]
        app.projection_direction_var = FakeStringVar("Along y")
        app.projection_fit_var = FakeStringVar(False)
        app.selected_vertex = None
        app.selected_edge = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_projection()

        self.assertEqual(app.model.vertices["v0"].y, -0.5)
        self.assertEqual(commits, [True])
        self.assertIsNone(app.projection_stage)

    def test_applying_projection_with_fit_creates_one_undoable_spline_edit(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        selected = edge_key("v0", "v1")
        curve = app.model.add_geometry_curve(
            ((0.0, -0.2), (0.5, -0.4), (1.0, -0.2))
        )
        app.projection_stage = "curves"
        app.projection_entity_kind = "edge"
        app.projection_vertex_ids = []
        app.projection_edges = [selected]
        app.projection_curve_ids = [curve.id]
        app.projection_direction_var = FakeStringVar("Along y")
        app.projection_fit_var = FakeStringVar(True)
        app.projection_fit_tolerance_var = FakeStringVar("1e-5")
        app.projection_fit_max_points_var = FakeStringVar("237")
        app.selected_vertex = None
        app.selected_edge = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))
        original_project = app.model.project_to_geometry
        projection_arguments = {}

        def project(*args, **kwargs):
            projection_arguments.update(kwargs)
            return original_project(*args, **kwargs)

        app.model.project_to_geometry = project

        app.apply_projection()

        self.assertEqual(app.model.edge_type(selected), "spline")
        self.assertEqual(app.model.edge_control_points(selected), ((0.5, -0.4),))
        self.assertEqual(commits, [True])
        self.assertIn("Fitted 1 edge", app.status.get())
        self.assertIn("maximum measured distance", app.status.get())
        self.assertEqual(projection_arguments["fit_relative_tolerance"], 1e-5)
        self.assertEqual(projection_arguments["fit_max_points"], 237)
        self.assertIsNone(app.projection_stage)

    def test_point_count_input_redistributes_and_commits_once(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.selected_edge = edge_key("v0", "v1")
        app.model.set_edge_type(app.selected_edge, "spline")
        app.selected_control_point_index = 0
        app.edge_point_count_var = FakeStringVar("3")
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_edge_control_point_count()

        self.assertEqual(
            app.model.edge_control_points(app.selected_edge),
            ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
        )
        self.assertEqual(commits, [True])
        self.assertIn("3 equidistant", app.status.get())

    def test_edge_marker_visibility_is_stored_in_preferences(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.preferences = default_config("linux")
        app.show_block_mesh_var = FakeStringVar(True)
        app.show_geometry_var = FakeStringVar(True)
        app.show_vertex_ids_var = FakeStringVar(False)
        app.show_edge_cell_counts_var = FakeStringVar(False)
        app.show_edge_nodes_var = FakeStringVar(False)
        app.show_edge_interpolation_points_var = FakeStringVar(False)
        app.show_mesh_preview_var = FakeStringVar(True)
        app.config_write_enabled = False
        app.status = FakeStringVar()
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app.apply_visibility()

        self.assertFalse(app.preferences.show_vertex_ids)
        self.assertFalse(app.preferences.show_edge_cell_counts)
        self.assertFalse(app.preferences.show_edge_nodes)
        self.assertFalse(app.preferences.show_edge_interpolation_points)
        self.assertTrue(app.preferences.show_mesh_preview)

    def test_canvas_vertex_and_edge_count_labels_can_be_hidden(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.root = FakeAfterRoot()
        app._viewport_redraw_after_id = None
        app.canvas = RecordingCanvas()
        app.item_targets = {}
        app.model = MeshModel()
        app.render_path_cache = RenderPathCache()
        app.view_x = 0.5
        app.view_y = 0.5
        app.pixels_per_unit = 100.0
        app.display_scale = 1.0
        app.ui_scale_multiplier = 1.0
        app.default_font_family = "TkDefaultFont"
        app.show_mesh_preview_var = FakeStringVar(False)
        app.show_block_mesh_var = FakeStringVar(True)
        app.show_geometry_var = FakeStringVar(False)
        app.show_vertex_ids_var = FakeStringVar(False)
        app.show_edge_cell_counts_var = FakeStringVar(False)
        app.show_edge_nodes_var = FakeStringVar(False)
        app.show_edge_interpolation_points_var = FakeStringVar(False)
        app.selected_edge = None
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.projection_edges = []
        app.projection_vertex_ids = []
        app.block_vertex_selection = None
        app.boundary_mode_active = False
        app.active_boundary_name = None
        app.split_edge_active = None
        app._draw_grid = lambda _width, _height: None

        app.redraw()

        self.assertEqual(app.canvas.texts, [])

        app.show_vertex_ids_var.set(True)
        app.show_edge_cell_counts_var.set(True)
        app.redraw()

        self.assertEqual(
            {text for text in app.canvas.texts if text.startswith("v")},
            {"v0", "v1", "v2", "v3"},
        )
        self.assertEqual(app.canvas.texts.count("10"), 4)

    def test_preview_coarsening_is_validated_and_stored_in_preferences(
        self,
    ) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.preferences = default_config("linux")
        app.mesh_preview_coarsening_var = FakeStringVar("10")
        app.config_write_enabled = False
        app.status = FakeStringVar()
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_mesh_preview_coarsening()

        self.assertEqual(app.preferences.preview_coarsening, 10)
        self.assertEqual(app.mesh_preview_coarsening_var.get(), "10")
        self.assertIn("not saved", app.status.get())

    def test_canvas_preview_draws_cached_interior_polylines(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.preferences = default_config("linux")
        app.mesh_preview_cache = MeshPreviewCache(capacity=1)
        app.mesh_preview_info_var = FakeStringVar()
        drawn: list[tuple[float, ...]] = []
        app.canvas = SimpleNamespace(
            create_line=lambda *points, **_options: drawn.append(points)
        )
        app.world_to_screen = lambda x, y: (x, y)
        app._px = lambda value: round(value)

        app._draw_mesh_preview()
        first_info = app.mesh_preview_info_var.get()
        app._draw_mesh_preview()

        self.assertEqual(len(drawn), 36)
        self.assertIn("18/18 visible interior lines", first_info)
        self.assertIn("cached", app.mesh_preview_info_var.get())

    def test_canvas_preview_culls_polylines_outside_viewport(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.preferences = default_config("linux")
        app.mesh_preview_cache = MeshPreviewCache(capacity=1)
        app.mesh_preview_info_var = FakeStringVar()
        drawn: list[tuple[float, ...]] = []
        app.canvas = SimpleNamespace(
            create_line=lambda *points, **_options: drawn.append(points)
        )
        app.world_to_screen = lambda x, y: (x, y)
        app._px = lambda value: round(value)
        app._redraw_world_bounds = (0.4, -0.1, 0.6, 1.1)

        app._draw_mesh_preview()

        self.assertEqual(len(drawn), 12)
        self.assertIn(
            "12/18 visible interior lines",
            app.mesh_preview_info_var.get(),
        )

        drawn.clear()
        app._redraw_world_bounds = (2.0, 2.0, 3.0, 3.0)
        app._draw_mesh_preview()
        self.assertEqual(drawn, [])
        self.assertIn(
            "0/18 visible interior lines",
            app.mesh_preview_info_var.get(),
        )

    def test_grading_input_recomputes_other_property_fields(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.selected_edge = edge_key("v0", "v1")
        app.model.set_edge_cells(app.selected_edge, 4)
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.edge_cells_var = FakeStringVar("4")
        app.edge_length_var = FakeStringVar()
        app.edge_cell_ratio_var = FakeStringVar()
        app.edge_total_ratio_var = FakeStringVar()
        app.edge_start_width_var = FakeStringVar(str(1.0 / 15.0))
        app.edge_end_width_var = FakeStringVar()
        app.edge_grading_propagate_var = SimpleNamespace(get=lambda: True)
        app.point_x_var = None
        app.point_y_var = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_edge_grading("start_width")

        self.assertAlmostEqual(float(app.edge_cell_ratio_var.get()), 2.0)
        self.assertAlmostEqual(float(app.edge_total_ratio_var.get()), 8.0)
        self.assertAlmostEqual(float(app.edge_end_width_var.get()), 8.0 / 15.0)
        self.assertAlmostEqual(
            app.model.edge_total_expansion(edge_key("v2", "v3")),
            1.0 / 8.0,
        )
        self.assertEqual(commits, [True])

    def test_spacing_link_mode_selects_a_pair_and_commits_once(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        first = edge_key("v0", "v1")
        second = edge_key("v1", "v2")
        app.spacing_link_first_edge = None
        app.selected_vertex = None
        app.selected_edge = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app._update_property_panel = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.select_spacing_link_edge(first)
        self.assertEqual(app.spacing_link_first_edge, first)
        self.assertEqual(commits, [])
        app.select_spacing_link_edge(second)

        self.assertIsNone(app.spacing_link_first_edge)
        self.assertEqual(app.selected_edge, second)
        self.assertEqual(len(app.model.spacing_links), 1)
        self.assertEqual(commits, [True])

    def test_grading_ui_synchronizes_spacing_link_follower(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.selected_edge = edge_key("v0", "v1")
        follower = edge_key("v1", "v2")
        app.model.add_spacing_link(app.selected_edge, follower)
        app.selected_vertex = None
        app.selected_control_point_index = None
        app.edge_cells_var = FakeStringVar("10")
        app.edge_length_var = FakeStringVar()
        app.edge_cell_ratio_var = FakeStringVar()
        app.edge_total_ratio_var = FakeStringVar("8")
        app.edge_start_width_var = FakeStringVar()
        app.edge_end_width_var = FakeStringVar()
        app.edge_grading_propagate_var = SimpleNamespace(get=lambda: False)
        app.point_x_var = None
        app.point_y_var = None
        app.status = FakeStringVar()
        app._commit_edit = lambda: None
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_edge_grading("total_ratio")

        self.assertAlmostEqual(
            app.model.edge_width_at_vertex(app.selected_edge, "v1"),
            app.model.edge_width_at_vertex(follower, "v1"),
        )

    def test_geometry_coordinate_input_updates_selected_point(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        curve = app.model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        app.selected_geometry_curve = curve.id
        app.selected_geometry_point_index = 1
        app.geometry_point_x_var = FakeStringVar("2.5")
        app.geometry_point_y_var = FakeStringVar("-1.25")
        app.geometry_name_var = FakeStringVar(curve.name)
        app.selected_vertex = None
        app.selected_edge = None
        app.selected_control_point_index = None
        app.status = FakeStringVar()
        commits: list[bool] = []
        app._commit_edit = lambda: commits.append(True)
        app.redraw = lambda: None
        app._show_error = lambda _title, error: self.fail(str(error))

        app.apply_geometry_curve_point()

        self.assertEqual(
            app.model.geometry_curves[curve.id].points[1],
            (2.5, -1.25),
        )
        self.assertEqual(commits, [True])

    def test_imported_geometry_curve_starts_with_points_hidden(self) -> None:
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.model = MeshModel()
        app.selected_vertex = None
        app.selected_edge = None
        app.selected_control_point_index = None
        app.selected_geometry_curve = None
        app.selected_geometry_point_index = None
        app.status = FakeStringVar()
        app._ensure_geometry_visible = lambda: None
        app._commit_edit = lambda: None
        app.fit_view = lambda: None
        app._update_property_panel = lambda: None

        with patch(
            "blockdrawer.editing.filedialog.askopenfilename",
            return_value="/tmp/dense-airfoil.txt",
        ), patch(
            "blockdrawer.editing.load_point_pairs",
            return_value=((0.0, 0.0), (0.5, 0.1), (1.0, 0.0)),
        ):
            app.import_geometry_curve()

        curve = next(iter(app.model.geometry_curves.values()))
        self.assertEqual(curve.name, "dense-airfoil")
        self.assertFalse(curve.show_points)


if __name__ == "__main__":
    unittest.main()
