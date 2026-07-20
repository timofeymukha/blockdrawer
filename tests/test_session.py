import json
from pathlib import Path
import tempfile
import unittest

from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import (
    SessionError,
    from_data,
    load_session,
    save_session,
    to_data,
)


class SessionTests(unittest.TestCase):
    def test_session_round_trip_preserves_topology_and_settings(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        model.move_vertex("v5", 2.25, 1.2)
        model.set_edge_cells(edge_key("v1", "v2"), 18)
        model.set_edge_grading(edge_key("v1", "v2"), "total_ratio", 6.0)
        model.set_edge_type(edge_key("v0", "v1"), "arc")
        model.set_arc_point(edge_key("v0", "v1"), 0.45, -0.3)
        model.add_boundary("inlet")
        model.add_boundary("outlet")
        model.set_boundary_type("inlet", "cyclic", neighbour_patch="outlet")
        model.set_edge_boundary(edge_key("v0", "v3"), "inlet")
        model.set_edge_boundary(edge_key("v4", "v5"), "outlet")
        model.set_z_cells(3)
        model.set_z_extents(-0.25, 0.25)
        model.scale = 0.001

        loaded = from_data(to_data(model))

        self.assertEqual(to_data(loaded), to_data(model))

    def test_session_round_trip_preserves_standalone_vertex(self) -> None:
        model = MeshModel()
        standalone = model.add_vertex(2.0, 3.0)

        data = to_data(model)
        loaded = from_data(data)

        self.assertEqual(
            (loaded.vertices[standalone.id].x, loaded.vertices[standalone.id].y),
            (2.0, 3.0),
        )
        self.assertTrue(all(
            standalone.id not in block.vertices for block in loaded.blocks
        ))

    def test_file_round_trip_is_readable_json(self) -> None:
        model = MeshModel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.json"
            save_session(model, path)

            parsed = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_session(path)

        self.assertEqual(parsed["format"], "blockDrawer")
        self.assertEqual(parsed["version"], 4)
        self.assertEqual(to_data(loaded), to_data(model))

    def test_version_one_straight_edge_session_is_migrated(self) -> None:
        data = to_data(MeshModel())
        data["version"] = 1
        del data["edgeGeometry"]
        del data["edgeGrading"]

        loaded = from_data(data)

        self.assertTrue(all(
            loaded.edge_type(current) == "line" for current in loaded.edges()
        ))
        self.assertEqual(to_data(loaded)["version"], 4)

    def test_version_two_session_is_migrated_with_uniform_grading(self) -> None:
        data = to_data(MeshModel())
        data["version"] = 2
        del data["edgeGrading"]

        loaded = from_data(data)

        self.assertTrue(all(
            loaded.edge_total_expansion(current) == 1.0
            for current in loaded.edges()
        ))
        self.assertEqual(to_data(loaded)["version"], 4)

    def test_version_three_session_is_migrated_without_boundaries(self) -> None:
        data = to_data(MeshModel())
        data["version"] = 3
        del data["boundaries"]
        del data["edgeBoundaries"]

        loaded = from_data(data)

        self.assertEqual(loaded.boundaries, {})
        self.assertEqual(loaded.edge_boundaries, {})
        self.assertEqual(to_data(loaded)["version"], 4)

    def test_boundary_definitions_and_assignments_use_version_four_schema(self) -> None:
        model = MeshModel()
        first = model.add_boundary("periodicA")
        second = model.add_boundary("periodicB")
        model.set_boundary_type(
            first.name, "cyclic", neighbour_patch=second.name
        )
        model.set_edge_boundary(edge_key("v0", "v3"), first.name)
        model.set_edge_boundary(edge_key("v1", "v2"), second.name)

        data = to_data(model)
        restored = from_data(data)

        self.assertEqual(data["boundaries"], [
            {
                "name": "periodicA",
                "type": "cyclic",
                "color": first.color,
                "neighbourPatch": "periodicB",
            },
            {
                "name": "periodicB",
                "type": "cyclic",
                "color": second.color,
                "neighbourPatch": "periodicA",
            },
        ])
        self.assertEqual(to_data(restored), data)

    def test_edge_grading_round_trips_in_canonical_direction(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_grading(selected, "total_ratio", 8.0)

        data = to_data(model)
        restored = from_data(data)

        self.assertEqual(data["edgeGrading"], [{
            "vertices": ["v0", "v1"],
            "expansionRatio": 8.0,
        }])
        self.assertEqual(restored.edge_total_expansion(selected), 8.0)

    def test_arc_geometry_uses_extensible_interpolation_point_schema(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)

        data = to_data(model)

        self.assertEqual(data["edgeGeometry"], [{
            "vertices": ["v0", "v1"],
            "type": "arc",
            "points": [{"x": 0.5, "y": -0.25}],
        }])
        restored = from_data(data)
        self.assertEqual(restored.arc_point(selected), (0.5, -0.25))

    def test_polyline_point_list_round_trips_in_version_two(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.2)

        data = to_data(model)
        restored = from_data(data)

        self.assertEqual(data["edgeGeometry"][0]["type"], "polyLine")
        self.assertEqual(
            restored.edge_control_points(selected),
            ((0.25, -0.4), (0.75, -0.2)),
        )

    def test_spline_point_list_round_trips_in_version_two(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.2)

        data = to_data(model)
        restored = from_data(data)

        self.assertEqual(data["edgeGeometry"][0]["type"], "spline")
        self.assertEqual(restored.edge_type(selected), "spline")
        self.assertEqual(
            restored.edge_control_points(selected),
            ((0.25, -0.4), (0.75, -0.2)),
        )

    def test_rejects_unknown_version(self) -> None:
        data = to_data(MeshModel())
        data["version"] = 999

        with self.assertRaisesRegex(SessionError, "Unsupported"):
            from_data(data)

    def test_rejects_inconsistent_opposite_edge_counts(self) -> None:
        data = to_data(MeshModel())
        data["edgeCells"][0]["cells"] = 3

        with self.assertRaisesRegex(SessionError, "unequal"):
            from_data(data)

    def test_rejects_empty_topology_session(self) -> None:
        data = to_data(MeshModel())
        data["vertices"] = []
        data["blocks"] = []
        data["edgeCells"] = []

        with self.assertRaisesRegex(SessionError, "at least one block"):
            from_data(data)


if __name__ == "__main__":
    unittest.main()
