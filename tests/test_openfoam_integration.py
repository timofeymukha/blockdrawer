import os
from pathlib import Path
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

    def test_block_mesh_accepts_block_created_from_existing_vertices(self) -> None:
        model = build_ring_model()
        model.add_block_from_vertices(center_vertex_ids(model))

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_normal_extension_from_skewed_block(self) -> None:
        model = MeshModel()
        model.move_vertex("v1", 2.0, 0.0)
        model.move_vertex("v2", 1.2, 1.0)
        model.move_vertex("v3", 0.8, 1.0)
        model.add_block(edge_key("v2", "v3"))

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_arc_on_both_extruded_planes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        model.set_edge_cells(selected, 8)

        self._assert_block_mesh_accepts(model)

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
        second = model.add_polyline_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.3)
        model.set_edge_cells(selected, 8)

        self._assert_block_mesh_accepts(model)

    def test_block_mesh_accepts_reset_equidistant_polyline(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        second = model.add_polyline_point(selected, 0)
        model.add_polyline_point(selected, second)

        model.reset_polyline_points(selected)

        self.assertEqual(
            model.edge_control_points(selected),
            ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
        )
        self._assert_block_mesh_accepts(model)

    def _assert_block_mesh_accepts(self, model: MeshModel) -> None:
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
            self.assertTrue((case / "constant" / "polyMesh" / "points").exists())


if __name__ == "__main__":
    unittest.main()
