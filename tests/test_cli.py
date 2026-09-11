import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from blockdrawer.cli import main
from blockdrawer.model import MeshModel
from blockdrawer.session import load_session, save_session


def run_cli(*argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    original_stdin = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
    finally:
        sys.stdin = original_stdin
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.session = self.root / "session.json"
        save_session(MeshModel(), self.session)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_no_subcommand_prints_help(self) -> None:
        code, out, _ = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)

    def test_new_and_validate(self) -> None:
        target = self.root / "fresh.json"
        code, out, _ = run_cli("new", str(target))
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)
        self.assertEqual(len(load_session(target).blocks), 1)
        code, _, err = run_cli("new", str(target))
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, out, _ = run_cli("new", str(target), "--force", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        code, out, _ = run_cli("validate", str(target))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("OK:"))

    def test_invalid_session_reports_error(self) -> None:
        broken = self.root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        code, _, err = run_cli("validate", str(broken))
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        code, out, _ = run_cli("validate", str(broken), "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_apply_writes_output_and_is_atomic(self) -> None:
        output = self.root / "edited.json"
        code, out, _ = run_cli(
            "apply", str(self.session), "set_edge_cells v0-v1 6", "add_block v1-v2",
            "-o", str(output),
        )
        self.assertEqual(code, 0)
        self.assertIn("1. set_edge_cells", out)
        self.assertIn(f"Saved {output}", out)
        self.assertEqual(len(load_session(output).blocks), 2)
        self.assertEqual(len(load_session(self.session).blocks), 1)

        code, _, err = run_cli(
            "apply", str(self.session), "add_block v1-v2", "set_edge_cells v0-v1 0",
            "--in-place",
        )
        self.assertEqual(code, 1)
        self.assertIn("Command 2", err)
        self.assertEqual(len(load_session(self.session).blocks), 1)

    def test_apply_in_place_dry_run_file_and_stdin(self) -> None:
        commands = self.root / "commands.txt"
        commands.write_text("# batch\nset_edge_cells v0-v1 3\n", encoding="utf-8")
        code, out, _ = run_cli(
            "apply", str(self.session), "-f", str(commands), "--stdin", "--json",
            "--in-place", stdin='{"op": "add_boundary", "name": "inlet"}\n',
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["saved"])
        self.assertEqual(data["applied"], 2)
        self.assertEqual(data["results"][1]["op"], "add_boundary")
        edited = load_session(self.session)
        self.assertIn("inlet", edited.boundaries)

        code, out, _ = run_cli("apply", str(self.session), "set_edge_cells v0-v1 9", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Not saved", out)
        self.assertEqual(load_session(self.session).edge_cells[("v0", "v1")], 3)

        code, out, _ = run_cli("apply", str(self.session), "set_edge_cells v0-v1 9")
        self.assertEqual(code, 0)
        self.assertIn("pass -o OUTPUT or --in-place", out)

        code, _, err = run_cli("apply", str(self.session))
        self.assertEqual(code, 1)
        self.assertIn("No commands given", err)
        code, _, err = run_cli(
            "apply", str(self.session), "add_block v1-v2", "-o", str(commands), "--in-place",
        )
        self.assertEqual(code, 1)
        self.assertIn("mutually exclusive", err)

    def test_describe_text_json_sections_and_filter(self) -> None:
        run_cli("apply", str(self.session), "add_block v1-v2", "--in-place")
        code, out, _ = run_cli("describe", str(self.session))
        self.assertEqual(code, 0)
        self.assertIn("Topology: 2 block(s)", out)
        self.assertIn("Blocks (", out)
        code, out, _ = run_cli("describe", str(self.session), "--json", "--section", "summary")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(list(data), ["summary"])
        self.assertEqual(data["summary"]["blocks"], 2)
        code, out, _ = run_cli(
            "describe", str(self.session), "--section", "edges", "--only", "b1",
        )
        self.assertEqual(code, 0)
        self.assertIn("v4-v5", out)
        self.assertNotIn("v0-v3", out)
        self.assertNotIn("Topology:", out)

    def test_quality_text_json_and_thresholds(self) -> None:
        run_cli(
            "apply", str(self.session), "set_edge_grading v0-v1 total_ratio 20",
            "--in-place",
        )
        code, out, _ = run_cli("quality", str(self.session))
        self.assertEqual(code, 0)
        self.assertIn("Quality: 1 block(s)", out)
        self.assertIn("growth", out)
        code, out, _ = run_cli(
            "quality", str(self.session), "--json", "--growth-ratio", "5",
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["thresholds"]["cell_growth_ratio"], 5.0)
        self.assertEqual(data["summary"]["warning_count"], 0)

    def test_render_svg_with_zoom_and_highlight(self) -> None:
        run_cli("apply", str(self.session), "add_block v1-v2", "--in-place")
        output = self.root / "picture.svg"
        code, out, _ = run_cli(
            "render", str(self.session), "-o", str(output), "--zoom", "b1",
            "--highlight", "v4", "v1-v2", "--title", "Zoomed", "--json",
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["output"], str(output))
        self.assertEqual(len(data["bounds"]), 4)
        svg = output.read_text(encoding="utf-8")
        self.assertIn(">Zoomed<", svg)
        self.assertIn(">b1<", svg)

        code, _, err = run_cli(
            "render", str(self.session), "-o", str(output), "--zoom", "b1",
            "--bounds", "0", "0", "1", "1",
        )
        self.assertEqual(code, 1)
        self.assertIn("mutually exclusive", err)
        code, _, err = run_cli("render", str(self.session), "-o", str(self.root / "x.txt"))
        self.assertEqual(code, 1)
        self.assertIn("Unsupported output format", err)
        code, _, err = run_cli("render", str(self.session), "-o", str(output), "--zoom", "zz")
        self.assertEqual(code, 1)
        self.assertIn("Unknown entity", err)

    def test_export_to_stdout_file_and_case(self) -> None:
        code, out, _ = run_cli("export", str(self.session))
        self.assertEqual(code, 0)
        self.assertIn("hex (0 1 2 3 4 5 6 7)", out)
        target = self.root / "blockMeshDict"
        code, out, _ = run_cli("export", str(self.session), "-o", str(target))
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        case = self.root / "case"
        code, out, _ = run_cli("export", str(self.session), "--case", str(case), "--json")
        self.assertEqual(code, 0)
        self.assertTrue((case / "system" / "blockMeshDict").is_file())
        self.assertTrue(json.loads(out)["ok"])

    def test_commands_listing(self) -> None:
        code, out, _ = run_cli("commands")
        self.assertEqual(code, 0)
        self.assertIn("set_edge_cells <edge> <cells>", out)
        self.assertIn("project <curves> <direction>", out)
        code, out, _ = run_cli("commands", "split_edge", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["commands"][0]["name"], "split_edge")
        self.assertEqual(data["commands"][0]["parameters"][1]["type"], "float")
        code, _, err = run_cli("commands", "nope")
        self.assertEqual(code, 1)
        self.assertIn("Unknown command", err)

    def test_check_without_openfoam_reports_missing_command(self) -> None:
        import os
        import unittest.mock

        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("BLOCKMESH_COMMAND", "CHECKMESH_COMMAND")
        }
        with unittest.mock.patch.dict(os.environ, environment, clear=True):
            code, _, err = run_cli("check", str(self.session))
        self.assertEqual(code, 1)
        self.assertIn("No blockMesh command", err)

    def test_check_with_stub_command(self) -> None:
        stub = self.root / "stub.py"
        stub.write_text(
            "import sys\n"
            "print('  nCells: 100\\n  nPoints: 242' if 'blockMesh' in sys.argv else 'Mesh OK.')\n",
            encoding="utf-8",
        )
        command = f"{sys.executable} {stub} blockMesh"
        code, out, _ = run_cli(
            "check", str(self.session), "--blockmesh-command", command,
            "--case", str(self.root / "case"), "--log",
        )
        self.assertEqual(code, 0)
        self.assertIn("blockMesh: OK (100 cells, 242 points)", out)
        self.assertIn("checkMesh: Mesh OK", out)
        self.assertIn("--- log.checkMesh ---", out)
        code, out, _ = run_cli(
            "check", str(self.session), "--blockmesh-command", command,
            "--no-checkmesh", "--json",
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["check_mesh"])
        self.assertFalse(data["kept"])


if __name__ == "__main__":
    unittest.main()
