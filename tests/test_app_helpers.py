from types import SimpleNamespace
import unittest
from unittest.mock import patch

from blockdrawer.app import (
    BlockDrawerApp,
    MAX_ZOOM_PIXELS_PER_UNIT,
    _is_text_input_class,
    _scaled_named_font_size,
    _system_display_scale,
    _visible_control_point_indices,
)
from blockdrawer.config import default_config
from blockdrawer.model import MeshModel, edge_key


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


class DpiScalingTests(unittest.TestCase):
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
        app.redraw = lambda: None

        app._on_mousewheel(SimpleNamespace(x=400, y=300, delta=120, num=None))

        self.assertEqual(app.pixels_per_unit, MAX_ZOOM_PIXELS_PER_UNIT)

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
        app.show_edge_nodes_var = FakeStringVar(False)
        app.show_edge_interpolation_points_var = FakeStringVar(False)
        app.config_write_enabled = False
        app.status = FakeStringVar()
        app._update_property_panel = lambda: None
        app.redraw = lambda: None

        app.apply_visibility()

        self.assertFalse(app.preferences.show_edge_nodes)
        self.assertFalse(app.preferences.show_edge_interpolation_points)

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
            "blockdrawer.app.filedialog.askopenfilename",
            return_value="/tmp/dense-airfoil.txt",
        ), patch(
            "blockdrawer.app.load_point_pairs",
            return_value=((0.0, 0.0), (0.5, 0.1), (1.0, 0.0)),
        ):
            app.import_geometry_curve()

        curve = next(iter(app.model.geometry_curves.values()))
        self.assertEqual(curve.name, "dense-airfoil")
        self.assertFalse(curve.show_points)


if __name__ == "__main__":
    unittest.main()
