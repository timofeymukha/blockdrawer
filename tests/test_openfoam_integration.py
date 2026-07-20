import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from blockdrawer.foam import write_block_mesh_dict
from blockdrawer.model import MeshModel, edge_key
from tests.helpers import build_ring_model, center_vertex_ids


@unittest.skipUnless(
    os.environ.get("BLOCKMESH_COMMAND"),
    "set BLOCKMESH_COMMAND to run the OpenFOAM integration test",
)
class OpenFoamIntegrationTests(unittest.TestCase):
    def test_block_mesh_accepts_exported_multiblock_dictionary(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        model.set_edge_cells(edge_key("v0", "v1"), 3)
        model.set_edge_cells(edge_key("v1", "v2"), 4)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_named_patch_and_wall_boundaries(self) -> None:
        model = MeshModel()
        model.add_boundary("inlet")
        model.add_boundary("outlet")
        model.add_boundary("walls")
        model.add_boundary("unusedPatch")
        model.set_boundary_type("walls", "wall")
        model.set_edge_boundary(edge_key("v0", "v3"), "inlet")
        model.set_edge_boundary(edge_key("v1", "v2"), "outlet")
        model.set_edge_boundary(edge_key("v0", "v1"), "walls")
        model.set_edge_boundary(edge_key("v2", "v3"), "walls")

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_inferred_translational_cyclic_pair(self) -> None:
        model = MeshModel()
        model.add_boundary("periodicA")
        model.add_boundary("periodicB")
        model.set_boundary_type(
            "periodicA", "cyclic", neighbour_patch="periodicB"
        )
        model.set_edge_boundary(edge_key("v0", "v3"), "periodicA")
        model.set_edge_boundary(edge_key("v1", "v2"), "periodicB")

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_automatic_cyclic_z_patch_pair(self) -> None:
        model = MeshModel()
        model.set_export_settings(
            2, -0.5, 0.5, 1.0,
            "periodicFront", "cyclic", "periodicBack", "patch",
        )

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_separate_empty_z_patches(self) -> None:
        model = MeshModel()
        model.set_export_settings(
            1, 0.0, 0.1, 1.0,
            "front", "empty", "back", "empty",
        )

        self._assert_block_mesh_accepts(model)

    def test_shared_graded_edge_matches_in_both_neighboring_blocks(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.set_edge_cells(selected, 4)
        model.add_block(selected)
        model.set_edge_grading(selected, "total_ratio", 8.0)
        expected_nodes = [
            (
                *model.edge_point(
                    selected, model.edge_node_fraction(selected, index)
                ),
                model.z_min,
            )
            for index in range(1, 4)
        ]

        self._assert_block_mesh_accepts(
            model, expected_points=expected_nodes
        )

    def test_propagated_grading_matches_on_opposite_edges(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        opposite = edge_key("v2", "v3")
        model.set_edge_cells(selected, 4)
        model.set_edge_grading(
            selected, "total_ratio", 8.0, propagate=True
        )
        expected_nodes = [
            (
                *model.edge_point(
                    current, model.edge_node_fraction(current, index)
                ),
                model.z_min,
            )
            for current in (selected, opposite)
            for index in range(1, 4)
        ]

        self._assert_block_mesh_accepts(
            model, expected_points=expected_nodes
        )

    def test_block_mesh_accepts_block_created_from_existing_vertices(self) -> None:
        model = build_ring_model()
        model.add_block_from_vertices(center_vertex_ids(model))

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_unused_standalone_vertex(self) -> None:
        model = MeshModel()
        model.add_vertex(2.0, 3.0)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_normal_extension_from_skewed_block(self) -> None:
        model = MeshModel()
        model.move_vertex("v1", 2.0, 0.0)
        model.move_vertex("v2", 1.2, 1.0)
        model.move_vertex("v3", 0.8, 1.0)
        model.add_block(edge_key("v2", "v3"))

        self._assert_block_mesh_accepts(model)

    def test_extruded_opposite_edge_keeps_source_grading(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 4)
        model.set_edge_grading(selected, "total_ratio", 8.0)

        added = model.add_block(selected)
        opposite = edge_key(added.vertices[2], added.vertices[3])
        expected_nodes = [
            (
                *model.edge_point(
                    opposite, model.edge_node_fraction(opposite, index)
                ),
                model.z_min,
            )
            for index in range(1, 4)
        ]

        self._assert_block_mesh_accepts(
            model, expected_points=expected_nodes
        )

    def test_block_mesh_accepts_arc_on_both_extruded_planes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        model.set_edge_cells(selected, 8)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_graded_arc_and_matches_preview(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        model.set_edge_cells(selected, 4)
        model.set_edge_grading(selected, "cell_ratio", 2.0)
        expected_nodes = [
            (
                *model.edge_point(
                    selected, model.edge_node_fraction(selected, index)
                ),
                model.z_min,
            )
            for index in range(1, 4)
        ]

        self._assert_block_mesh_accepts(
            model, expected_points=expected_nodes
        )

    def test_block_mesh_accepts_arc_shared_by_two_blocks(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 1.2, 0.5)
        model.add_block(selected)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_polyline_with_multiple_points(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.25, -0.2)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.3)
        model.set_edge_cells(selected, 8)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_reset_equidistant_polyline(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        second = model.add_edge_control_point(selected, 0)
        model.add_edge_control_point(selected, second)

        model.reset_edge_control_points(selected)

        self.assertEqual(
            model.edge_control_points(selected),
            ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
        )
        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_spline_with_multiple_points(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        model.set_edge_control_point(selected, 0, 0.3, -0.25)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.7, -0.35)
        model.set_edge_cells(selected, 12)

        expected_nodes = [
            (*model.edge_point(selected, index / 12), model.z_min)
            for index in range(1, 12)
        ]
        self._assert_block_mesh_accepts(model, expected_points=expected_nodes)

    def test_block_mesh_accepts_arc_converted_by_projection(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        target = model.add_geometry_curve(((-1.0, -0.25), (2.0, -0.25)))

        result = model.project_to_geometry(
            (target.id,), "y", edges=(selected,)
        )

        self.assertEqual(result.converted_arcs, (selected,))
        self.assertEqual(model.edge_type(selected), "spline")
        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_spline_created_by_fitted_projection(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        target = model.add_geometry_curve(
            ((0.0, -0.2), (0.5, -0.4), (1.0, -0.2))
        )

        result = model.project_to_geometry(
            (target.id,), "y", edges=(selected,), fit=True
        )

        self.assertEqual(result.fitted_edges, (selected,))
        self.assertEqual(model.edge_type(selected), "spline")
        self._assert_block_mesh_accepts(model)

    def _assert_block_mesh_accepts(
        self,
        model: MeshModel,
        *,
        expected_points: list[tuple[float, float, float]] | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            (case / "system").mkdir()
            (case / "constant").mkdir()
            (case / "system" / "controlDict").write_text(
                """FoamFile
{
    format ascii;
    class dictionary;
    object controlDict;
}
application blockMesh;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 1;
deltaT 1;
writeControl timeStep;
writeInterval 1;
""",
                encoding="utf-8",
            )
            write_block_mesh_dict(model, case / "system" / "blockMeshDict")
            command = [
                *shlex.split(os.environ["BLOCKMESH_COMMAND"]),
                "-case",
                str(case),
            ]
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout)
            points_path = case / "constant" / "polyMesh" / "points"
            self.assertTrue(points_path.exists())
            if expected_points:
                number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
                tuples = re.findall(
                    rf"\(\s*({number})\s+({number})\s+({number})\s*\)",
                    points_path.read_text(encoding="utf-8"),
                )
                generated = [tuple(map(float, values)) for values in tuples]
                for expected in expected_points:
                    self.assertTrue(
                        any(math.dist(expected, actual) < 1.0e-7
                            for actual in generated),
                        f"Preview point {expected!r} is absent from blockMesh output",
                    )


if __name__ == "__main__":
    unittest.main()
