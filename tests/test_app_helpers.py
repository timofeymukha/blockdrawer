from types import SimpleNamespace
import unittest

from blockdrawer.app import (
    BlockDrawerApp,
    _is_text_input_class,
    _scaled_named_font_size,
    _system_display_scale,
)
from blockdrawer.model import MeshModel, edge_key


class FakeStringVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DpiScalingTests(unittest.TestCase):
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
        self.assertIsNone(app._new_vertex_shortcut(
            SimpleNamespace(widget=canvas, state=0x0004)
        ))
        self.assertEqual(calls, [True])

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
        self.assertIsNone(app._boundary_shortcut(
            SimpleNamespace(widget=canvas, state=0x0004)
        ))
        self.assertEqual(calls, [True])

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


if __name__ == "__main__":
    unittest.main()
