import re
import unittest

from blockdrawer.foam import block_mesh_dict
from blockdrawer.model import MeshModel, TopologyError, edge_key


class FoamExportTests(unittest.TestCase):
    def test_single_block_export_has_extruded_vertices_and_hex(self) -> None:
        model = MeshModel()
        model.set_edge_cells(edge_key("v0", "v1"), 12)
        model.set_edge_cells(edge_key("v1", "v2"), 7)
        model.set_z_cells(1)

        result = block_mesh_dict(model)

        self.assertIn("object      blockMeshDict;", result)
        self.assertIn("scale 1;", result)
        self.assertIn("(0 0 0) // 0: v0 lower", result)
        self.assertIn("(0 0 1) // 4: v0 upper", result)
        self.assertIn(
            "hex (0 1 2 3 4 5 6 7) (12 7 1) simpleGrading (1 1 1)",
            result,
        )
        self.assertIn("edges\n(\n)\n;", result)
        self.assertIn("boundary\n(\n)\n;", result)

    def test_multiple_blocks_share_exported_vertex_indices(self) -> None:
        model = MeshModel()
        added = model.add_block(edge_key("v1", "v2"))
        model.set_edge_cells(edge_key("v1", "v2"), 9)
        model.set_edge_cells(edge_key("v1", "v4"), 4)

        result = block_mesh_dict(model)

        hex_lines = [line for line in result.splitlines() if line.strip().startswith("hex")]
        self.assertEqual(len(hex_lines), 2)
        self.assertRegex(hex_lines[0], r"\(10 9 1\)")
        self.assertIn("hex (2 1 4 5 8 7 10 11) (9 4 1)", hex_lines[1])
        self.assertTrue(hex_lines[1].endswith(f"// {added.id}"))

    def test_export_contains_no_curved_edges_or_named_boundaries(self) -> None:
        result = block_mesh_dict(MeshModel())

        edges_section = re.search(r"edges\s*\((.*?)\)\s*;", result, re.DOTALL)
        boundary_section = re.search(r"boundary\s*\((.*?)\)\s*;", result, re.DOTALL)
        self.assertIsNotNone(edges_section)
        self.assertIsNotNone(boundary_section)
        self.assertEqual(edges_section.group(1).strip(), "")
        self.assertEqual(boundary_section.group(1).strip(), "")

    def test_arc_is_exported_on_lower_and_upper_extruded_edges(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        model.set_z_extents(-1.0, 2.0)

        result = block_mesh_dict(model)

        edges_section = re.search(r"edges\s*\((.*?)\)\s*;", result, re.DOTALL)
        self.assertIsNotNone(edges_section)
        arc_lines = [
            line.strip()
            for line in edges_section.group(1).splitlines()
            if line.strip()
        ]
        self.assertEqual(arc_lines, [
            "arc 0 1 (0.5 -0.25 -1)",
            "arc 4 5 (0.5 -0.25 2)",
        ])

    def test_arc_shared_by_two_blocks_is_not_duplicated(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 1.2, 0.5)
        model.add_block(selected)

        result = block_mesh_dict(model)

        self.assertEqual(result.count("    arc "), 2)

    def test_polyline_point_lists_are_exported_on_both_z_planes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.2)
        model.set_z_extents(-1.0, 2.0)

        result = block_mesh_dict(model)

        self.assertIn(
            """    polyLine 0 1
    (
        (0.25 -0.4 -1)
        (0.75 -0.2 -1)
    )""",
            result,
        )
        self.assertIn(
            """    polyLine 4 5
    (
        (0.25 -0.4 2)
        (0.75 -0.2 2)
    )""",
            result,
        )

    def test_spline_is_exported_on_both_planes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.2)
        model.set_z_extents(-1.0, 2.0)

        result = block_mesh_dict(model)

        self.assertIn(
            """    spline 0 1
    (
        (0.25 -0.4 -1)
        (0.75 -0.2 -1)
    )""",
            result,
        )
        self.assertIn(
            """    spline 4 5
    (
        (0.25 -0.4 2)
        (0.75 -0.2 2)
    )""",
            result,
        )
    def test_empty_topology_cannot_be_exported(self) -> None:
        model = MeshModel(initialize=False)

        with self.assertRaisesRegex(TopologyError, "at least one block"):
            block_mesh_dict(model)


if __name__ == "__main__":
    unittest.main()
