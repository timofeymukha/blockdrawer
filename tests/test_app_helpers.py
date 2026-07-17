import unittest

from blockdrawer.app import (
    _is_text_input_class,
    _scaled_named_font_size,
    _system_display_scale,
)


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


if __name__ == "__main__":
    unittest.main()
