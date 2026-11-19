import json
import math
import unittest

from blockdrawer.commands import apply_commands
from blockdrawer.domain import EdgeGeometry
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


class InteriorSamplingTests(unittest.TestCase):
    def test_off_by_default_in_the_library(self) -> None:
        report = assess_quality(MeshModel())
        self.assertIsNone(report.interior)
        self.assertNotIn("interior", report.summary())
        verdict = report.to_data()["verdict"]
        self.assertIsNone(verdict["valid"])  # export is tried with the sampling
        self.assertFalse(verdict["sampled"])
        self.assertIsNone(verdict["untangled"])
        self.assertIn("corner screen only", format_quality(report))

    def test_sampled_fields_and_verdict(self) -> None:
        model = MeshModel()
        model.move_vertex("v2", 4.0, 1.0)
        model.move_vertex("v3", 3.0, 1.0)
        report = assess_quality(model, interior_cells=256)
        data = report.to_data()
        json.dumps(data)
        interior = data["blocks"][0]["interior"]
        self.assertEqual(interior["sampled_cells"], [10, 10])
        self.assertAlmostEqual(interior["max_non_orthogonality"], 90.0 - math.degrees(math.atan(1 / 3)))
        self.assertGreater(interior["severe_faces"], 0)
        verdict = data["verdict"]
        self.assertTrue(verdict["valid"])
        self.assertTrue(verdict["untangled"])
        self.assertTrue(verdict["real_counts"])
        self.assertEqual(verdict["worst_block"], "b0")
        self.assertEqual(verdict["severe_faces"], interior["severe_faces"])
        self.assertEqual(data["summary"]["interior"]["severe_faces"], interior["severe_faces"])
        self.assertTrue(any("sampled interior non-orthogonality" in w for w in report.warnings))
        text = format_quality(report)
        self.assertIn("Verdict: valid and exports; untangled (real counts)", text)
        self.assertIn("interior non-orth, severe faces, folded", text)

    def test_a_fold_between_sound_corners_is_the_worst_warning(self) -> None:
        model = apply_commands(MeshModel(), [
            "add_block v1-v2", "set_edge_grading v1-v4 total_ratio 30",
        ]).model
        model.edge_geometry[edge_key("v0", "v1")] = EdgeGeometry("arc", ((0.5, 1.4),))
        model.validate()
        corners = assess_quality(model)
        self.assertFalse(any("fold" in warning for warning in corners.warnings))
        report = assess_quality(model, interior_cells=64)
        self.assertFalse(report.verdict()["untangled"])
        self.assertGreater(report.verdict()["folded_cells"], 0)
        ranked, omitted = report.ranked_warnings(1)
        self.assertIn("fold between the corners", ranked[0])
        self.assertEqual(omitted, len(report.warnings) - 1)
        self.assertEqual(report.worst_blocks(1)[0].block_id, "b0")
        data = report.to_data(max_warnings=1)
        self.assertEqual(len(data["warnings"]), 1)
        self.assertEqual(data["warnings_omitted"], len(report.warnings) - 1)
        self.assertIn("TANGLED", format_quality(report))

    def test_text_caps_warnings_worst_first(self) -> None:
        model = apply_commands(MeshModel(), [
            "add_block v1-v2",
            "set_edge_grading v0-v1 total_ratio 20",
            "set_edge_grading v1-v4 total_ratio 50",
        ]).model
        report = assess_quality(model)
        self.assertEqual(len(report.warnings), 3)
        self.assertIn("Block b0", report.warnings[0])  # model order, unranked
        # Size defects rank by how far past their limit: the 41x jump across
        # v1-v2, then b1's 1.54 growth, then b0's 1.40.
        ranked, omitted = report.ranked_warnings()
        self.assertEqual(omitted, 0)
        self.assertTrue(ranked[0].startswith("Edge v1-v2"))
        self.assertIn("v1-v4", ranked[1])
        self.assertIn("v0-v1", ranked[2])
        text = format_quality(report, max_warnings=1)
        self.assertIn("Warnings, the 1 worst of 3:", text)
        self.assertIn("2 more", text)
        self.assertIn("Warnings:", format_quality(report, max_warnings=None))


class VerdictExportTests(unittest.TestCase):
    def test_an_export_failure_makes_the_verdict_invalid(self) -> None:
        from unittest import mock

        with mock.patch("blockdrawer.foam.block_mesh_dict", side_effect=ValueError("no pair")):
            report = assess_quality(MeshModel(), interior_cells=16)
        verdict = report.verdict()
        self.assertFalse(verdict["valid"])
        self.assertEqual(verdict["export_error"], "no pair")
        self.assertIn("does NOT export: no pair", format_quality(report))


def _quads(points: dict, quads: list, cells: int = 10) -> MeshModel:
    from blockdrawer.session import from_data, to_data

    data = to_data(MeshModel())
    data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
    data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
    edges = {
        tuple(sorted((quad[k], quad[(k + 1) % 4]))) for quad in quads for k in range(4)
    }
    data["edgeCells"] = [{"vertices": list(edge), "cells": cells} for edge in sorted(edges)]
    return from_data(data)


class WallFirstWidthTests(unittest.TestCase):
    def test_spokes_are_measured_normal_to_the_wall_and_others_left_out(self) -> None:
        from blockdrawer.quality import wall_first_widths

        # A wall from a to b; c-d runs on along the wall's line past b, the
        # spoke b-e leans 26.6 degrees from the wall normal, a-f is normal.
        model = _quads(
            {"a": (0, 0), "b": (1, 0), "c": (2, 0), "e": (1.5, 1), "f": (0, 1), "g": (2, 1)},
            [("a", "b", "e", "f"), ("b", "c", "g", "e")],
        )
        model = apply_commands(model, [
            "add_boundary wall", "set_boundary_type wall wall", "set_edge_boundary a-b wall",
            "set_edge_grading a-f start_width 0.01", "set_edge_grading b-e start_width 0.01",
        ]).model
        (band,) = wall_first_widths(model, {"wall": 0.01})
        self.assertEqual(band["spokes"], 2)
        # b-c runs along the wall's line: not a spoke.
        self.assertEqual(band["other_edges"], 1)
        self.assertEqual(band["asked"], 0.01)
        low, high = band["first_width"]
        self.assertAlmostEqual(high, 0.01, places=12)
        lean = math.atan2(0.5, 1.0)
        self.assertAlmostEqual(low, 0.01 * math.cos(lean), places=9)
        self.assertEqual(band["first_width_edges"], ["b-e", "a-f"])

    def test_a_wake_line_from_a_sharp_edge_is_no_spoke(self) -> None:
        from blockdrawer.quality import wall_first_widths

        # A thin wedge's trailing edge t with upper and lower walls, a wake
        # line t-w on its bisector and two spokes normal to the walls.
        model = _quads(
            {"u": (0, 0.1), "t": (1, 0), "l": (0, -0.1), "w": (2, 0),
             "un": (0.1, 1), "tn": (1.1, 1), "ln": (0.1, -1), "tm": (1.1, -1),
             "wu": (2, 1), "wl": (2, -1)},
            [("u", "t", "tn", "un"), ("t", "w", "wu", "tn"),
             ("l", "ln", "tm", "t"), ("t", "tm", "wl", "w")],
        )
        model = apply_commands(model, [
            "add_boundary body", "set_boundary_type body wall",
            "set_edge_boundary u-t body", "set_edge_boundary l-t body",
        ]).model
        (band,) = wall_first_widths(model)
        self.assertNotIn("t-w", band["first_width_edges"])
        # u-un, t-tn, l-ln, t-tm are spokes; t-w is not.
        self.assertEqual(band["spokes"], 4)
        self.assertEqual(band["other_edges"], 1)

    def test_a_right_angled_corner_bisector_is_a_spoke(self) -> None:
        from blockdrawer.quality import wall_first_widths

        model = _quads(
            {"v0": (-2, -2), "v1": (2, -2), "v2": (2, 2), "v3": (-2, 2),
             "i0": (-1, -1), "i1": (1, -1), "i2": (1, 1), "i3": (-1, 1)},
            [("v0", "v1", "i1", "i0"), ("v1", "v2", "i2", "i1"),
             ("v2", "v3", "i3", "i2"), ("v3", "v0", "i0", "i3")],
        )
        model = apply_commands(model, [
            "add_boundary wall", "set_boundary_type wall wall",
            *(f"set_edge_boundary {edge} wall" for edge in ("i0-i1", "i1-i2", "i2-i3", "i0-i3")),
        ]).model
        (band,) = wall_first_widths(model)
        self.assertEqual(band["spokes"], 4)
        width = model.edge_width_at_vertex(edge_key("v0", "i0"), "i0")
        self.assertAlmostEqual(band["first_width"][0], width * math.cos(math.radians(45)), places=9)

    def test_the_quality_report_carries_the_first_widths(self) -> None:
        model = apply_commands(MeshModel(box=(0, 0, 4, 1)), [
            "add_boundary wall", "set_boundary_type wall wall",
            "set_edge_boundary v0-v1 wall", "set_edge_grading v0-v3 start_width 0.01",
        ]).model
        report = assess_quality(model)
        (band,) = report.to_data(summary_only=True)["first_widths"]
        self.assertEqual(band["patch"], "wall")
        self.assertAlmostEqual(band["first_width"][0], 0.01, places=9)
        text = format_quality(report)
        self.assertIn("first cell height normal to the wall", text)
        self.assertIn("wall: 0.01..0.1 at v0-v3..v1-v2, 2 spoke(s), 0 other edge(s)", text)
