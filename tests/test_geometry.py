from pathlib import Path
import tempfile
import unittest

from blockdrawer.geometry import (
    GeometryImportError,
    load_point_pairs,
    parse_point_pairs,
)
from blockdrawer.model import MeshModel, TopologyError


class GeometryCurveTests(unittest.TestCase):
    def test_curve_interpolates_every_input_point_by_chord_fraction(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(
            ((0.0, 0.0), (1.0, 0.0), (1.0, 2.0)),
            name="guide",
        )

        self.assertEqual(model.geometry_curve_point(curve.id, 0.0), (0.0, 0.0))
        self.assertEqual(model.geometry_curve_point(curve.id, 1.0 / 3.0), (1.0, 0.0))
        self.assertEqual(model.geometry_curve_point(curve.id, 1.0), (1.0, 2.0))

    def test_curve_points_can_be_moved_added_removed_and_replaced(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))

        model.set_geometry_curve_point(curve.id, 1, 1.0, 1.0)
        added = model.add_geometry_curve_point(curve.id, 1)
        self.assertEqual(added, 2)
        self.assertEqual(
            model.geometry_curves[curve.id].points,
            ((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)),
        )
        model.remove_geometry_curve_point(curve.id, 1)
        model.replace_geometry_curve_points(
            curve.id, ((-1.0, 0.0), (0.0, 1.0), (1.0, 0.0))
        )
        self.assertEqual(
            model.geometry_curves[curve.id].points,
            ((-1.0, 0.0), (0.0, 1.0), (1.0, 0.0)),
        )

    def test_curve_point_visibility_defaults_on_and_can_be_changed(self) -> None:
        model = MeshModel()
        manual = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        imported = model.add_geometry_curve(
            ((0.0, 1.0), (1.0, 1.0)),
            show_points=False,
        )

        self.assertTrue(manual.show_points)
        self.assertFalse(imported.show_points)
        model.set_geometry_curve_point_visibility(manual.id, False)
        self.assertFalse(model.geometry_curves[manual.id].show_points)

    def test_dense_curve_rendering_samples_each_span_and_keeps_input_points(
        self,
    ) -> None:
        model = MeshModel()
        defining_points = tuple(
            (index / 599.0, (index % 7) / 100.0)
            for index in range(600)
        )
        curve = model.add_geometry_curve(defining_points)

        rendered = model.geometry_curve_render_points(
            curve.id, samples_per_span=4
        )

        self.assertEqual(len(rendered), 599 * 4 + 1)
        self.assertEqual(rendered[::4], defining_points)

    def test_curve_render_sampling_rejects_invalid_span_count(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))

        for invalid in (0, -1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(TopologyError, "positive integer"):
                    model.geometry_curve_render_points(
                        curve.id, samples_per_span=invalid  # type: ignore[arg-type]
                    )

    def test_curve_validation_rejects_too_few_or_adjacent_equal_points(self) -> None:
        model = MeshModel()
        with self.assertRaisesRegex(TopologyError, "at least two"):
            model.add_geometry_curve(((0.0, 0.0),))
        with self.assertRaisesRegex(TopologyError, "must not coincide"):
            model.add_geometry_curve(((0.0, 0.0), (0.0, 0.0)))
        with self.assertRaisesRegex(TopologyError, "true or false"):
            model.add_geometry_curve(
                ((0.0, 0.0), (1.0, 0.0)), show_points=1
            )

    def test_point_parser_accepts_spaces_commas_comments_and_blank_lines(self) -> None:
        points = parse_point_pairs(
            "# upper surface\n0, 0\n\n0.5  1.25 # peak\n1,0\n"
        )

        self.assertEqual(points, ((0.0, 0.0), (0.5, 1.25), (1.0, 0.0)))

    def test_point_parser_reports_line_and_minimum_point_errors(self) -> None:
        with self.assertRaisesRegex(GeometryImportError, "Line 2"):
            parse_point_pairs("0 0\n1 2 3\n")
        with self.assertRaisesRegex(GeometryImportError, "at least two"):
            parse_point_pairs("0 0\n")

    def test_point_file_loader_reads_utf8_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.txt"
            path.write_text("0 0\n1, 2\n", encoding="utf-8")

            points = load_point_pairs(path)

        self.assertEqual(points, ((0.0, 0.0), (1.0, 2.0)))


if __name__ == "__main__":
    unittest.main()
