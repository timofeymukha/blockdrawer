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
        model.set_z_cells(3)
        model.set_z_extents(-0.25, 0.25)
        model.scale = 0.001

        loaded = from_data(to_data(model))

        self.assertEqual(to_data(loaded), to_data(model))

    def test_file_round_trip_is_readable_json(self) -> None:
        model = MeshModel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.json"
            save_session(model, path)

            parsed = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_session(path)

        self.assertEqual(parsed["format"], "blockDrawer")
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(to_data(loaded), to_data(model))

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
