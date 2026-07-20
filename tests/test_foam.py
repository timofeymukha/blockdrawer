import re
import unittest

from blockdrawer.foam import block_mesh_dict
from blockdrawer.model import MeshModel, TopologyError, edge_key


class FoamExportTests(unittest.TestCase):
    def test_reference_geometry_is_not_exported(self) -> None:
        model = MeshModel()
        baseline = block_mesh_dict(model)
        model.add_geometry_curve(
            ((-1.0, 0.0), (0.5, 2.0), (2.0, 0.0)),
            name="guide",
        )

        self.assertEqual(block_mesh_dict(model), baseline)

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
            "hex (0 1 2 3 4 5 6 7) (12 7 1) "
            "edgeGrading (1 1 1 1 1 1 1 1 1 1 1 1)",
            result,
        )
        self.assertIn("edges\n(\n)\n;", result)
        self.assertIn("    zMin\n    {\n        type patch;", result)
        self.assertIn("            (0 3 2 1)", result)
        self.assertIn("    zMax\n    {\n        type patch;", result)
        self.assertIn("            (4 5 6 7)", result)

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

    def test_standalone_vertex_is_exported_without_changing_block(self) -> None:
        model = MeshModel()
        standalone = model.add_vertex(2.0, 3.0)

        result = block_mesh_dict(model)

        self.assertIn(f"(2 3 0) // 4: {standalone.id} lower", result)
        self.assertIn(f"(2 3 1) // 9: {standalone.id} upper", result)
        self.assertIn(
            "hex (0 1 2 3 5 6 7 8) (10 10 1) "
            "edgeGrading (1 1 1 1 1 1 1 1 1 1 1 1)",
            result,
        )

    def test_each_2d_edge_grading_is_mapped_to_both_z_planes(self) -> None:
        model = MeshModel()
        model.set_edge_grading(edge_key("v0", "v1"), "total_ratio", 8.0)
        model.set_edge_grading(edge_key("v2", "v3"), "total_ratio", 4.0)
        model.set_edge_grading(edge_key("v0", "v3"), "total_ratio", 2.0)
        model.set_edge_grading(edge_key("v1", "v2"), "total_ratio", 3.0)

        result = block_mesh_dict(model)
        hex_line = next(
            line for line in result.splitlines()
            if line.strip().startswith("hex")
        )

        self.assertIn(
            "edgeGrading (8 0.25 0.25 8 2 3 3 2 1 1 1 1)",
            hex_line,
        )

    def test_propagated_grading_has_one_physical_block_direction(self) -> None:
        model = MeshModel()
        model.set_edge_grading(
            edge_key("v0", "v1"),
            "total_ratio",
            8.0,
            propagate=True,
        )

        hex_line = next(
            line for line in block_mesh_dict(model).splitlines()
            if line.strip().startswith("hex")
        )

        self.assertIn(
            "edgeGrading (8 8 8 8 1 1 1 1 1 1 1 1)",
            hex_line,
        )

    def test_shared_edge_grading_is_reversed_for_neighboring_block(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.add_block(selected)
        model.set_edge_grading(selected, "total_ratio", 8.0)

        hex_lines = [
            line for line in block_mesh_dict(model).splitlines()
            if line.strip().startswith("hex")
        ]

        self.assertIn(
            "edgeGrading (1 1 1 1 1 8 8 1 1 1 1 1)",
            hex_lines[0],
        )
        self.assertIn(
            "edgeGrading (0.125 1 1 0.125 1 1 1 1 1 1 1 1)",
            hex_lines[1],
        )

    def test_export_contains_no_curved_edges_and_only_automatic_z_patches(self) -> None:
        result = block_mesh_dict(MeshModel())

        edges_section = re.search(r"edges\s*\((.*?)\)\s*;", result, re.DOTALL)
        self.assertIsNotNone(edges_section)
        self.assertEqual(edges_section.group(1).strip(), "")
        self.assertEqual(result.count("        type patch;"), 2)
        self.assertIn("    zMin\n", result)
        self.assertIn("    zMax\n", result)

    def test_custom_z_patch_names_and_types_are_exported(self) -> None:
        model = MeshModel()
        model.set_export_settings(
            3, -0.5, 0.5, 0.001,
            "frontPlane", "symmetry", "backPlane", "wall",
        )

        result = block_mesh_dict(model)

        self.assertIn("    frontPlane\n    {\n        type symmetry;", result)
        self.assertIn("    backPlane\n    {\n        type wall;", result)
        self.assertNotIn("neighbourPatch", result)

    def test_cyclic_z_patch_selection_exports_reciprocal_pair(self) -> None:
        model = MeshModel()
        model.set_export_settings(
            2, 0.0, 1.0, 1.0,
            "periodicLow", "patch", "periodicHigh", "cyclic",
        )

        result = block_mesh_dict(model)

        self.assertEqual(result.count("type cyclic;"), 2)
        self.assertIn("neighbourPatch periodicHigh;", result)
        self.assertIn("neighbourPatch periodicLow;", result)

    def test_automatic_z_patches_contain_one_face_per_block(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))

        result = block_mesh_dict(model)

        self.assertIn("            (0 3 2 1)", result)
        self.assertIn("            (2 5 4 1)", result)
        self.assertIn("            (6 7 8 9)", result)
        self.assertIn("            (8 7 10 11)", result)

    def test_named_boundaries_export_extruded_side_faces_and_types(self) -> None:
        model = MeshModel()
        model.add_boundary("inlet")
        model.add_boundary("walls")
        model.add_boundary("sideSymmetry")
        model.add_boundary("twoD")
        model.set_boundary_type("walls", "wall")
        model.set_boundary_type("sideSymmetry", "symmetry")
        model.set_boundary_type("twoD", "empty")
        model.set_edge_boundary(edge_key("v0", "v3"), "inlet")
        model.set_edge_boundary(edge_key("v0", "v1"), "walls")
        model.set_edge_boundary(edge_key("v2", "v3"), "sideSymmetry")
        model.set_edge_boundary(edge_key("v1", "v2"), "twoD")

        result = block_mesh_dict(model)

        self.assertIn("type patch;", result)
        self.assertIn("type wall;", result)
        self.assertIn("type symmetry;", result)
        self.assertIn("type empty;", result)
        self.assertIn("(3 0 4 7)", result)
        self.assertIn("(0 1 5 4)", result)
        self.assertIn("(2 3 7 6)", result)
        self.assertIn("(1 2 6 5)", result)

    def test_cyclic_boundaries_export_reciprocal_neighbour_patch(self) -> None:
        model = MeshModel()
        model.add_boundary("periodicA")
        model.add_boundary("periodicB")
        model.set_boundary_type(
            "periodicA", "cyclic", neighbour_patch="periodicB"
        )
        model.set_edge_boundary(edge_key("v0", "v3"), "periodicA")
        model.set_edge_boundary(edge_key("v1", "v2"), "periodicB")

        result = block_mesh_dict(model)

        self.assertEqual(result.count("type cyclic;"), 2)
        self.assertIn("neighbourPatch periodicB;", result)
        self.assertIn("neighbourPatch periodicA;", result)

    def test_incomplete_cyclic_pair_is_rejected_on_export(self) -> None:
        model = MeshModel()
        model.add_boundary("periodicA")
        model.add_boundary("periodicB")
        model.set_boundary_type(
            "periodicA", "cyclic", neighbour_patch="periodicB"
        )
        model.set_edge_boundary(edge_key("v0", "v3"), "periodicA")

        with self.assertRaisesRegex(TopologyError, "at least one assigned"):
            block_mesh_dict(model)

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
