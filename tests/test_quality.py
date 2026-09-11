import json
import math
import unittest

from blockdrawer.commands import apply_commands
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.quality import (
    QualityThresholds,
    assess_quality,
    format_quality,
)


class BlockQualityTests(unittest.TestCase):
    def test_unit_square_is_perfect(self) -> None:
        report = assess_quality(MeshModel())
        self.assertEqual(len(report.blocks), 1)
        block = report.blocks[0]
        self.assertEqual(block.block_id, "b0")
        self.assertEqual(block.cells, (10, 10, 1))
        self.assertAlmostEqual(block.area, 1.0)
        for corner in block.corners:
            self.assertAlmostEqual(corner.angle, 90.0)
            self.assertAlmostEqual(corner.aspect_ratio, 1.0)
        self.assertAlmostEqual(block.non_orthogonality, 0.0)
        self.assertAlmostEqual(block.equiangle_skewness, 0.0)
        self.assertAlmostEqual(block.max_cell_growth_ratio, 1.0)
        self.assertAlmostEqual(block.min_cell_size, 0.1)
        self.assertEqual(report.warnings, ())
        self.assertEqual(report.interfaces, ())
        self.assertEqual(report.total_cells, 100)
        summary = report.summary()
        self.assertEqual(summary["warning_count"], 0)
        self.assertEqual(summary["max_interface_size_ratio"], 1.0)

    def test_parallelogram_reports_corner_angles(self) -> None:
        model = MeshModel()
        model.move_vertex("v2", 2.0, 1.0)
        model.move_vertex("v3", 1.0, 1.0)
        block = assess_quality(model).blocks[0]
        self.assertAlmostEqual(block.min_angle, 45.0, places=6)
        self.assertAlmostEqual(block.max_angle, 135.0, places=6)
        self.assertAlmostEqual(block.non_orthogonality, 45.0, places=6)
        self.assertAlmostEqual(block.equiangle_skewness, 0.5, places=6)
        corners = {corner.vertex: corner for corner in block.corners}
        self.assertAlmostEqual(corners["v0"].angle, 45.0, places=6)
        self.assertAlmostEqual(corners["v1"].angle, 135.0, places=6)

    def test_first_cell_angle_follows_curved_edge(self) -> None:
        model = MeshModel()
        bottom = edge_key("v0", "v1")
        model.set_edge_type(bottom, "arc")
        model.set_arc_point(bottom, 0.5, -0.5)
        model.set_edge_cells(bottom, 40)
        corners = {
            corner.vertex: corner for corner in assess_quality(model).blocks[0].corners
        }
        # A semicircle leaves v0 pointing straight down, so the corner with the
        # vertical left edge opens to about 180 degrees.
        self.assertGreater(corners["v0"].angle, 170.0)
        self.assertAlmostEqual(corners["v2"].angle, 90.0)

    def test_grading_drives_aspect_growth_and_warnings(self) -> None:
        model = MeshModel()
        bottom = edge_key("v0", "v1")
        model.set_edge_grading(bottom, "total_ratio", 100.0)
        report = assess_quality(model)
        block = report.blocks[0]
        self.assertGreater(block.max_cell_growth_ratio, 1.6)
        corners = {corner.vertex: corner for corner in block.corners}
        self.assertLess(corners["v0"].x_width, corners["v0"].y_width)
        self.assertGreater(corners["v1"].x_width, corners["v1"].y_width)
        self.assertTrue(any("growth" in warning for warning in report.warnings))

        strict = assess_quality(model, QualityThresholds(cell_aspect_ratio=2.0))
        self.assertTrue(any("aspect ratio" in warning for warning in strict.warnings))
        lenient = assess_quality(
            model, QualityThresholds(cell_growth_ratio=10.0, cell_aspect_ratio=1000.0)
        )
        self.assertEqual(lenient.warnings, ())

    def test_angle_warnings(self) -> None:
        model = MeshModel()
        model.move_vertex("v2", 3.0, 1.0)
        model.move_vertex("v3", 2.0, 1.0)
        report = assess_quality(model)
        self.assertTrue(any("corner angle" in warning for warning in report.warnings))
        self.assertFalse(any("non-orthogonality" in warning for warning in report.warnings))
        moderate = assess_quality(
            model, QualityThresholds(min_angle=5.0, max_angle=175.0, non_orthogonality=10.0)
        )
        self.assertTrue(any("non-orthogonality" in w for w in moderate.warnings))


class InterfaceQualityTests(unittest.TestCase):
    def test_size_jump_across_shared_edge(self) -> None:
        model = apply_commands(MeshModel(), [
            "add_block v1-v2",
            "set_edge_grading v1-v4 total_ratio 9",
        ]).model
        report = assess_quality(model)
        self.assertEqual(len(report.interfaces), 1)
        interface = report.interfaces[0]
        self.assertEqual(interface.edge, edge_key("v1", "v2"))
        self.assertEqual(interface.blocks, ("b0", "b1"))
        # Block b0's bottom cells are 0.1 wide while b1's first x cell at v1
        # is small because of the grading; at v2 both transverse edges are
        # uniform with equal cells, so there is no jump there.
        ratio_v1, ratio_v2 = interface.size_ratios
        self.assertGreater(ratio_v1, 1.0)
        self.assertAlmostEqual(ratio_v2, 1.0)
        self.assertEqual(interface.max_size_ratio, max(ratio_v1, ratio_v2))
        self.assertTrue(any("cell size jumps" in warning for warning in report.warnings))

    def test_matched_blocks_have_no_jump(self) -> None:
        model = apply_commands(MeshModel(), ["add_block v1-v2"]).model
        interface = assess_quality(model).interfaces[0]
        self.assertAlmostEqual(interface.max_size_ratio, 1.0)


class QualityReportTests(unittest.TestCase):
    def test_to_data_and_text(self) -> None:
        model = apply_commands(MeshModel(), [
            "add_block v1-v2",
            "set_edge_grading v4-v5 total_ratio 50",
        ]).model
        report = assess_quality(model)
        data = report.to_data()
        json.dumps(data)
        self.assertEqual(data["summary"]["blocks"], 2)
        self.assertEqual(data["thresholds"]["non_orthogonality"], 65.0)
        self.assertEqual(data["interfaces"][0]["edge"], "v1-v2")
        self.assertEqual(len(data["blocks"][0]["corners"]), 4)
        self.assertIn("aspect_ratio", data["blocks"][0]["corners"][0])
        text = format_quality(report)
        self.assertIn("Quality: 2 block(s), 200 cells", text)
        self.assertIn("Internal edges (edge | blocks | size jump at each end):", text)
        self.assertIn("Warnings:", text)
        self.assertIn("Thresholds:", text)
        self.assertTrue(any(math.isfinite(corner["angle"]) for corner in data["blocks"][0]["corners"]))

    def test_clean_report_says_so(self) -> None:
        text = format_quality(assess_quality(MeshModel()))
        self.assertIn("No warnings at the current thresholds.", text)


if __name__ == "__main__":
    unittest.main()
