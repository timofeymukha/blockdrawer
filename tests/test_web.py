import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from blockdrawer.model import MeshModel
from blockdrawer.session import load_session, save_session
from blockdrawer.web.server import WebServer
from blockdrawer.web.session import WebSession, WebSessionError


class WebSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.config = self.root / "config.json"
        self.path = self.root / "session.json"
        save_session(MeshModel(), self.path)
        self.session = WebSession(self.path, config_path=self.config)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_snapshot_shape(self) -> None:
        data = self.session.snapshot()
        json.dumps(data)
        self.assertEqual(data["session"]["name"], "session.json")
        self.assertFalse(data["session"]["dirty"])
        self.assertIn("shortcuts", data["preferences"])
        self.assertEqual(data["preferences"]["recent_files"], [str(self.path.resolve())])
        self.assertEqual(len(data["edges"]), 4)
        edge = data["edges"][0]
        for key in ("id", "path", "nodes", "grading", "constraint_count", "can_delete", "can_combine"):
            self.assertIn(key, edge)
        self.assertEqual(len(edge["nodes"]), 9)
        self.assertIsNone(edge["node_fractions"])
        self.assertEqual(data["settings"]["z_cells"], 1)

    def test_command_records_history_and_dirty(self) -> None:
        version = self.session.version
        result = self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 7})
        self.assertEqual(result["cells"], 7)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.history.can_undo)
        self.assertGreater(self.session.version, version)
        with self.assertRaisesRegex(WebSessionError, "positive integer"):
            self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 0})
        self.assertTrue(self.session.undo())
        self.assertFalse(self.session.dirty)
        self.assertTrue(self.session.redo())
        self.assertEqual(self.session.model.edge_cells[("v0", "v1")], 7)
        self.assertFalse(self.session.redo())

    def test_drag_records_once_on_release(self) -> None:
        self.session.drag("vertex", "v1", 1.2, 0.0)
        self.session.drag("vertex", "v1", 1.4, 0.0)
        self.assertFalse(self.session.history.can_undo)
        self.assertTrue(self.session.drag_end())
        self.assertTrue(self.session.history.can_undo)
        self.assertFalse(self.session.drag_end())
        with self.assertRaisesRegex(WebSessionError, "strictly convex"):
            self.session.drag("vertex", "v0", 5.0, 5.0)
        self.session.command("set_edge_type", {"edge": "v0-v1", "type": "arc"})
        self.session.drag("control_point", ["v0-v1", 0], 0.5, -0.3)
        self.assertEqual(self.session.model.arc_point(("v0", "v1")), (0.5, -0.3))
        curve = self.session.command("add_curve", {"points": [[0, -1], [1, -1]]})
        self.session.drag("geometry_point", [curve["curve"], 1], 2.0, -1.0)
        self.assertEqual(self.session.model.geometry_curves[curve["curve"]].points[1], (2.0, -1.0))
        with self.assertRaisesRegex(WebSessionError, "Unknown drag kind"):
            self.session.drag("block", "b0", 0, 0)

    def test_save_open_new_and_recent(self) -> None:
        self.session.command("add_block", {"edge": "v1-v2"})
        other = self.root / "other.json"
        self.session.save_session(other)
        self.assertEqual(self.session.session_path, other.resolve())
        self.assertFalse(self.session.dirty)
        self.assertEqual(len(load_session(other).blocks), 2)
        self.assertEqual(self.session.preferences.recent_files[0], str(other.resolve()))
        self.session.new_session()
        self.assertIsNone(self.session.session_path)
        self.assertEqual(len(self.session.model.blocks), 1)
        with self.assertRaisesRegex(WebSessionError, "Choose a file name"):
            self.session.save_session()
        self.session.open_session(other)
        self.assertEqual(len(self.session.model.blocks), 2)
        with self.assertRaisesRegex(WebSessionError, "Could not read"):
            self.session.open_session(self.root / "missing.json")
        self.session.remove_recent(other)
        self.assertNotIn(str(other.resolve()), self.session.preferences.recent_files)
        self.session.clear_recent()
        self.assertEqual(self.session.preferences.recent_files, ())

    def test_external_change_detection_and_reload(self) -> None:
        self.assertFalse(self.session.external_change_detected())
        model = MeshModel()
        model.add_block(("v1", "v2"))
        save_session(model, self.path)
        import os
        os.utime(self.path, (self.path.stat().st_atime, self.path.stat().st_mtime + 5))
        self.assertTrue(self.session.external_change_detected())
        self.session.reload_session()
        self.assertEqual(len(self.session.model.blocks), 2)
        self.assertFalse(self.session.external_change_detected())

    def test_export_applies_settings_only_on_success(self) -> None:
        target = self.root / "blockMeshDict"
        self.session.export_block_mesh_dict(target, {"z_cells": 3, "scale": 0.5})
        self.assertTrue(target.is_file())
        self.assertEqual(self.session.model.z_cells, 3)
        self.assertTrue(self.session.history.can_undo)
        with self.assertRaisesRegex(WebSessionError, "distinct names"):
            self.session.export_block_mesh_dict(target, {"z_max_patch_name": "zMin"})
        self.assertEqual(self.session.model.z_cells, 3)
        with self.assertRaises(WebSessionError):
            self.session.export_block_mesh_dict(self.root / "missing" / "dir" / "blockMeshDict", {"z_cells": 9})
        self.assertEqual(self.session.model.z_cells, 3)

    def test_preferences_persist(self) -> None:
        warning = self.session.set_visibility(show_mesh_preview=True, show_vertex_ids=False)
        self.assertIsNone(warning)
        stored = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertTrue(stored["ui"]["showMeshPreview"])
        self.assertFalse(stored["ui"]["showVertexIds"])
        self.assertTrue(self.session.snapshot()["preferences"]["show_mesh_preview"])
        self.session.set_preview_coarsening(5)
        self.assertEqual(self.session.snapshot()["preferences"]["preview_coarsening"], 5)
        with self.assertRaises(WebSessionError):
            self.session.set_preview_coarsening(0)
        with self.assertRaises(WebSessionError):
            self.session.set_visibility(show_everything=True)
        self.session.set_ui_scale("1.5")
        self.assertEqual(self.session.snapshot()["preferences"]["ui_scale"], "1.5")

    def test_queries(self) -> None:
        info = self.session.edge_fraction("v0-v1", 0.3, -0.2)
        self.assertAlmostEqual(info["fraction"], 0.3, places=6)
        self.assertEqual(info["first_cells"] + info["second_cells"], 10)
        clamped = self.session.edge_fraction("v0-v1", -5.0, 0.0)
        self.assertGreater(clamped["fraction"], 0.0)
        cells = self.session.split_cells("v0-v1", 0.5)
        self.assertEqual((cells["first_cells"], cells["second_cells"]), (5, 5))
        self.assertEqual(cells["point"], [0.5, 0.0])
        listing = self.session.list_directory(self.root)
        names = [entry["name"] for entry in listing["entries"]]
        self.assertIn("session.json", names)
        with self.assertRaisesRegex(WebSessionError, "not a directory"):
            self.session.list_directory(self.root / "nope")

    def test_edges_send_every_node_and_graded_fractions(self) -> None:
        self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 1200})
        self.session.command("set_edge_grading", {"edge": "v0-v1", "parameter": "total_ratio", "value": 4.0})
        edge = next(item for item in self.session.snapshot()["edges"] if item["id"] == "v0-v1")
        self.assertTrue(edge["dense_nodes"])
        self.assertEqual(len(edge["nodes"]), 1199)
        self.assertEqual(len(edge["node_fractions"]), 1199)
        self.assertLess(edge["node_fractions"][0], 1 / 1200)
        self.assertAlmostEqual(edge["node_fractions"][-1], 1.0 - edge["node_fractions"][-1] * 0 - (1.0 - edge["node_fractions"][-1]), places=6)

    def test_wait_for_change_returns_on_bump(self) -> None:
        version = self.session.version
        timer = threading.Timer(0.05, lambda: self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 4}))
        timer.start()
        seen = self.session.wait_for_change(version, timeout=2.0)
        self.assertGreater(seen, version)
        self.assertEqual(self.session.wait_for_change(seen, timeout=0.05), seen)


class WebServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.path = self.root / "session.json"
        save_session(MeshModel(), self.path)
        self.session = WebSession(self.path, config_path=self.root / "config.json")
        self.server = WebServer(("127.0.0.1", 0), self.session)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._directory.cleanup()

    def request(self, method: str, path: str, body=None, token: str | None = "default"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token == "default":
            headers["X-BlockDrawer-Token"] = self.server.token
        elif token is not None:
            headers["X-BlockDrawer-Token"] = token
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def request_json(self, method: str, path: str, body=None, token="default"):
        status, raw = self.request(method, path, body, token)
        return status, json.loads(raw.decode("utf-8"))

    def test_index_embeds_token_and_static_files_are_served(self) -> None:
        status, raw = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertIn(self.server.token, html)
        self.assertIn("/static/app.js", html)
        status, raw = self.request("GET", "/static/app.js", token=None)
        self.assertEqual(status, 200)
        self.assertIn("BlockDrawer browser client", raw.decode("utf-8"))
        status, _ = self.request("GET", "/static/../server.py", token=None)
        self.assertEqual(status, 404)
        status, _ = self.request("GET", "/static/missing.css", token=None)
        self.assertEqual(status, 404)

    def test_api_requires_token(self) -> None:
        status, data = self.request_json("GET", "/api/state", token=None)
        self.assertEqual(status, 401)
        self.assertFalse(data["ok"])
        status, data = self.request_json("GET", "/api/state", token="wrong")
        self.assertEqual(status, 401)
        status, data = self.request_json("GET", "/api/state?token=" + self.server.token, token=None)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["state"]["edges"]), 4)

    def test_command_undo_and_errors(self) -> None:
        status, data = self.request_json("POST", "/api/command", {
            "op": "add_block", "args": {"edge": "v1-v2"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["block"], "b1")
        self.assertEqual(len(data["state"]["blocks"]), 2)
        self.assertTrue(data["state"]["session"]["dirty"])
        status, data = self.request_json("POST", "/api/undo", {})
        self.assertTrue(data["changed"])
        self.assertEqual(len(data["state"]["blocks"]), 1)
        status, data = self.request_json("POST", "/api/command", {
            "op": "set_edge_cells", "args": {"edge": "v0-v9", "cells": 3},
        })
        self.assertEqual(status, 400)
        self.assertIn("Unknown edge", data["error"])
        status, data = self.request_json("POST", "/api/nothing", {})
        self.assertEqual(status, 404)
        status, data = self.request("POST", "/api/command", None)
        self.assertEqual(status, 400)

    def test_drag_save_and_files(self) -> None:
        status, data = self.request_json("POST", "/api/drag", {
            "kind": "vertex", "target": "v1", "x": 1.3, "y": 0.0,
        })
        self.assertEqual(status, 200)
        self.assertFalse(data["state"]["session"]["can_undo"])
        status, data = self.request_json("POST", "/api/drag_end", {})
        self.assertTrue(data["recorded"])
        self.assertTrue(data["state"]["session"]["can_undo"])
        target = self.root / "saved.json"
        status, data = self.request_json("POST", "/api/save", {"path": str(target)})
        self.assertEqual(status, 200)
        self.assertFalse(data["state"]["session"]["dirty"])
        self.assertEqual(load_session(target).vertices["v1"].x, 1.3)
        status, data = self.request_json("GET", "/api/files?dir=" + str(self.root))
        self.assertIn("saved.json", [entry["name"] for entry in data["result"]["entries"]])
        status, data = self.request_json("POST", "/api/export", {
            "path": str(self.root / "blockMeshDict"), "settings": {"z_cells": 2},
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["state"]["settings"]["z_cells"], 2)

    def test_dirty_flag_tracks_history_position(self) -> None:
        self.assertFalse(self.session.snapshot()["session"]["dirty"])
        self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 3})
        self.assertTrue(self.session.dirty)
        self.session.undo()
        self.assertFalse(self.session.dirty)
        self.session.redo()
        self.session.save_session()
        self.assertFalse(self.session.dirty)
        self.session.undo()
        self.assertTrue(self.session.dirty)
        self.session.redo()
        self.assertFalse(self.session.dirty)
        self.session.drag("vertex", "v1", 1.2, 0.0)
        self.assertTrue(self.session.dirty)

    def test_event_stream_reports_changes(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(
            "GET",
            "/api/events?token=" + self.server.token + "&version=" + str(self.session.version),
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type", "").startswith("text/event-stream"))
        timer = threading.Timer(
            0.1, lambda: self.session.command("set_edge_cells", {"edge": "v0-v1", "cells": 3})
        )
        timer.start()
        line = response.fp.readline().decode("utf-8")
        self.assertEqual(line.strip(), "event: state")
        data_line = response.fp.readline().decode("utf-8")
        self.assertIn('"version"', data_line)
        connection.close()


if __name__ == "__main__":
    unittest.main()
