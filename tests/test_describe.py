import json
import unittest

from blockdrawer.commands import apply_commands
from blockdrawer.describe import SECTIONS, describe_model, format_description
from blockdrawer.model import MeshModel


def _sample_model() -> MeshModel:
    return apply_commands(MeshModel(), [
        "set_edge_cells v0-v1 20",
        "add_block v1-v2",
        "set_edge_grading v0-v1 total_ratio 4",
        "set_edge_type v0-v1 arc",
        "add_boundary inlet",
        "set_edge_boundary v0-v3 inlet",
        "add_curve '0,-0.5;0.5,-0.6;1,-0.5' name=bottom",
        "add_spacing_link v0-v1 v1-v2",
        "add_vertex 5 5",
    ]).model


class DescribeModelTests(unittest.TestCase):
    def test_description_is_json_serializable_and_complete(self) -> None:
        data = describe_model(_sample_model())
        json.dumps(data)
        self.assertEqual(tuple(data), SECTIONS)
        summary = data["summary"]
        self.assertEqual(summary["blocks"], 2)
        self.assertEqual(summary["vertices"], 7)
        self.assertEqual(summary["standalone_vertices"], 1)
        self.assertEqual(summary["edges"], 7)
        self.assertEqual(summary["exterior_edges"], 6)
        self.assertEqual(summary["internal_edges"], 1)
        self.assertEqual(summary["unassigned_exterior_edges"], 5)
        self.assertEqual(summary["curved_edges"], 1)
        self.assertEqual(summary["total_cells"], 400)
        self.assertEqual(summary["bounds"]["x_max"], 2.0)
        self.assertEqual(summary["curves"], 1)
        self.assertEqual(summary["spacing_links"], 1)
        self.assertEqual(summary["unsynchronized_spacing_links"], 0)

    def test_block_and_edge_entries_reference_command_ids(self) -> None:
        data = describe_model(_sample_model())
        block = data["blocks"][0]
        self.assertEqual(block["id"], "b0")
        self.assertEqual(block["cells"], [20, 10, 1])
        self.assertEqual(block["x_edges"], ["v0-v1", "v2-v3"])
        self.assertEqual(block["y_edges"], ["v1-v2", "v0-v3"])
        self.assertEqual(block["centroid"], [0.5, 0.5])

        edges = {edge["id"]: edge for edge in data["edges"]}
        arc = edges["v0-v1"]
        self.assertEqual(arc["type"], "arc")
        self.assertEqual(arc["kind"], "exterior")
        self.assertEqual(arc["blocks"], ["b0"])
        self.assertEqual(arc["grading"]["total_ratio"], 4.0)
        self.assertEqual(arc["control_point_count"], 1)
        self.assertEqual(len(arc["control_points"]), 1)
        self.assertEqual(arc["spacing_links"][0]["other_edge"], "v1-v2")
        self.assertTrue(arc["spacing_links"][0]["synchronized"])
        shared = edges["v1-v2"]
        self.assertEqual(shared["kind"], "internal")
        self.assertEqual(shared["blocks"], ["b0", "b1"])
        self.assertEqual(edges["v0-v3"]["boundary"], "inlet")
        self.assertIsNone(edges["v2-v3"]["boundary"])

        vertices = {vertex["id"]: vertex for vertex in data["vertices"]}
        self.assertEqual(vertices["v1"]["blocks"], ["b0", "b1"])
        self.assertEqual(vertices["v6"]["blocks"], [])

        boundary = data["boundaries"][0]
        self.assertEqual(boundary["name"], "inlet")
        self.assertEqual(boundary["edges"], ["v0-v3"])
        curve = data["curves"][0]
        self.assertEqual(curve["name"], "bottom")
        self.assertEqual(curve["point_count"], 3)
        self.assertEqual(curve["bounds"]["y_min"], -0.6)
        self.assertEqual(data["spacing_links"][0]["edges"], ["v0-v1", "v1-v2"])

    def test_many_control_points_are_counted_not_listed(self) -> None:
        model = apply_commands(MeshModel(), [
            "set_edge_type v0-v1 spline",
            "set_control_point_count v0-v1 12",
        ]).model
        edge = next(
            item for item in describe_model(model)["edges"] if item["id"] == "v0-v1"
        )
        self.assertEqual(edge["control_point_count"], 12)
        self.assertNotIn("control_points", edge)


class FormatDescriptionTests(unittest.TestCase):
    def test_text_contains_every_section(self) -> None:
        text = format_description(describe_model(_sample_model()))
        self.assertIn("Topology: 2 block(s), 7 vertices (1 standalone)", text)
        self.assertIn("Notes: 5 exterior edge(s) without a patch", text)
        self.assertIn("b0: v0 v1 v2 v3 | 20 10 1", text)
        self.assertIn("v0-v1 | arc[1] | 20 |", text)
        self.assertIn("| exterior | b0 | - | links: v1->v1-v2", text)
        self.assertIn("v6 (5, 5) standalone", text)
        self.assertIn("inlet patch: v0-v3", text)
        self.assertIn("g0 'bottom': 3 points", text)
        self.assertIn("v1: v0-v1 <-> v1-v2 synchronized", text)
        self.assertTrue(text.endswith("\n"))

    def test_sections_can_be_selected(self) -> None:
        data = describe_model(_sample_model())
        text = format_description(data, sections=("blocks",))
        self.assertIn("Blocks (", text)
        self.assertNotIn("Edges (", text)
        self.assertNotIn("Topology:", text)

    def test_out_of_sync_links_are_flagged(self) -> None:
        model = _sample_model()
        model.move_vertex("v2", 1.0, 1.5)  # lengthens follower v1-v2
        text = format_description(describe_model(model))
        self.assertIn("OUT OF SYNC", text)
        self.assertIn("spacing link(s) out of sync", text)

    def test_empty_optional_sections_have_placeholders(self) -> None:
        text = format_description(describe_model(MeshModel()))
        self.assertIn("Boundaries: none defined", text)
        self.assertIn("Reference curves: none", text)
        self.assertIn("Spacing links: none", text)


if __name__ == "__main__":
    unittest.main()
