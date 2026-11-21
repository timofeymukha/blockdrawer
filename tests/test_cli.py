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
        code, _, _ = run_cli("new", str(target), "--force", "--box", "-5", "0", "0", "1")
        self.assertEqual(code, 0)
        placed = load_session(target)
        self.assertEqual((placed.vertices["v0"].x, placed.vertices["v2"].y), (-5.0, 1.0))
        code, _, err = run_cli("new", str(target), "--force", "--box", "0", "0", "0", "1")
        self.assertEqual(code, 1)
        self.assertIn("xmax > xmin", err)

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
        code, both, _ = run_cli(
            "describe", str(self.session), "--section", "blocks", "--only", "b0,b1",
        )
        self.assertEqual(code, 0)
        self.assertIn("b0", both)
        self.assertIn("b1", both)
        # patches is another name for the boundaries section.
        code, out, _ = run_cli("describe", str(self.session), "--json", "--section", "patches")
        self.assertEqual(code, 0)
        self.assertEqual(list(json.loads(out)), ["boundaries"])
        code, _, _ = run_cli("describe", str(self.session), "--section", "patches")
        self.assertEqual(code, 0)
        code, _, err = run_cli("describe", str(self.session), "--only", "b0,b18")
        self.assertEqual(code, 1)
        self.assertIn("'b18'", err)

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

    def test_quality_reports_the_largest_cells_in_a_box(self) -> None:
        run_cli("apply", str(self.session), "set_edge_cells v0-v1 10", "set_edge_cells v1-v2 4",
                "--in-place")
        code, out, _ = run_cli("quality", str(self.session), "--bbox", "0,0,0.5,1", "--json")
        self.assertEqual(code, 0)
        found = json.loads(out)["cell_extents"][0]
        self.assertEqual(found["cells"], 20)
        self.assertAlmostEqual(found["max_x_extent"], 0.1)
        self.assertAlmostEqual(found["max_y_extent"], 0.25)
        self.assertEqual(found["y_block"], "b0")
        code, out, _ = run_cli("quality", str(self.session), "--bbox", "0", "0", "1", "1",
                               "--bbox", "5,5,6,6", "--summary-only")
        self.assertEqual(code, 0)
        self.assertIn("cells in box 0,0,1,1: 40; largest x extent 0.1 (b0 at", out)
        self.assertIn("cells in box 5,5,6,6: none has its centre there", out)

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

    def test_check_lists_the_worst_blocks_of_each_set(self) -> None:
        from tests.polymesh_fixture import extruded_mesh, write_polymesh, write_set
        from tests.test_checkmesh import SEVERE_LOG
        from tests.test_polymesh import CELLS, POINTS, two_block_model

        save_session(two_block_model(), self.session)
        fixture = self.root / "fixture"
        write_polymesh(fixture, extruded_mesh(POINTS, CELLS))
        write_set(fixture, "nonOrthoFaces", "faceSet", [1, 2])
        stub = self.root / "meshstub.py"
        stub.write_text(
            "import shutil, sys\n"
            "case = sys.argv[sys.argv.index('-case') + 1]\n"
            "if 'blockMesh' in sys.argv:\n"
            f"    shutil.copytree({str(fixture / 'constant')!r}, case + '/constant', dirs_exist_ok=True)\n"
            "    print('  nCells: 4')\n"
            "else:\n"
            f"    print({SEVERE_LOG.replace('596', '2')!r})\n",
            encoding="utf-8",
        )
        command = f"{sys.executable} {stub} blockMesh"
        code, out, _ = run_cli("check", str(self.session), "--blockmesh-command", command, "--worst", "1")
        self.assertEqual(code, 0)
        self.assertIn("severely non-orthogonal faces (> 70 degrees): 2", out)
        self.assertIn("nonOrthoFaces: 2 faces in 2 block(s)", out)
        self.assertIn("... 1 more block(s)", out)
        code, out, _ = run_cli("check", str(self.session), "--blockmesh-command", command, "--json")
        data = json.loads(out)
        self.assertEqual(len(data["sets"][0]["blocks"]), 2)
        self.assertEqual(data["severe_non_orthogonal_faces"], 2)
        code, out, _ = run_cli(
            "check", str(self.session), "--blockmesh-command", command, "--json", "--no-sets",
        )
        self.assertEqual(json.loads(out)["sets"], [])

    def test_describe_queries(self) -> None:
        run_cli("apply", str(self.session), "add_block v1-v2", "add_block v4-v5", "--in-place")
        code, out, _ = run_cli("describe", str(self.session), "--bbox", "1.2", "0.2", "1.4", "0.4")
        self.assertEqual(code, 0)
        self.assertIn("b1:", out)
        self.assertNotIn("b0:", out)
        code, out, _ = run_cli("describe", str(self.session), "--component", "v1-v2", "--json")
        self.assertEqual(json.loads(out)["component"]["blocks"], ["b0", "b1", "b2"])
        code, out, _ = run_cli(
            "describe", str(self.session), "--unassigned", "--section", "summary",
        )
        self.assertIn("Exterior edges without a patch: 8 in 1 chain(s)", out)

    def test_describe_box_in_one_argument_and_query_sections(self) -> None:
        run_cli("apply", str(self.session), "add_block v1-v2", "add_block v4-v5", "--in-place")
        for box in (["1.2,0.2,1.4,0.4"], ["--bbox=1.2,0.2,1.4,0.4"]):
            argv = ["describe", str(self.session)] + (box if box[0].startswith("--") else ["--bbox", *box])
            code, out, _ = run_cli(*argv)
            self.assertEqual(code, 0)
            self.assertIn("b1:", out)
            self.assertNotIn("b0:", out)
        code, out, _ = run_cli("describe", str(self.session), "--bbox", "-1,-1,0.5,0.5", "--json")
        self.assertEqual([block["id"] for block in json.loads(out)["blocks"]], ["b0"])
        code, out, _ = run_cli("describe", str(self.session), "--component", "v1-v2", "--section", "component")
        self.assertEqual(code, 0)
        self.assertTrue(out.lstrip().startswith("Count component of v1-v2"))
        self.assertNotIn("Blocks (", out)
        code, _, err = run_cli("describe", str(self.session), "--section", "component")
        self.assertEqual(code, 1)
        self.assertIn("--component EDGE", err)
        code, out, _ = run_cli("describe", str(self.session), "--unassigned", "--section", "unassigned")
        self.assertIn("Exterior edges without a patch", out)
        self.assertNotIn("Topology:", out)

    def test_render_without_labels_and_control_points_outside_the_patch_palette(self) -> None:
        from blockdrawer.model import MeshModel
        from blockdrawer.render import CONTROL_POINT_COLOR

        self.assertNotIn(CONTROL_POINT_COLOR, MeshModel.BOUNDARY_COLORS)
        run_cli("apply", str(self.session), "add_block v1-v2", "--in-place")
        output = self.root / "plain.svg"
        code, _, _ = run_cli("render", str(self.session), "-o", str(output), "--no-labels")
        self.assertEqual(code, 0)
        svg = output.read_text(encoding="utf-8")
        for label in (">b1<", ">v4<", ">10<"):
            self.assertNotIn(label, svg)

    def test_quality_interior_and_warning_cap(self) -> None:
        run_cli(
            "apply", str(self.session), "add_block v1-v2",
            "set_edge_grading v0-v1 total_ratio 20", "set_edge_grading v1-v4 total_ratio 50",
            "--in-place",
        )
        code, out, _ = run_cli("quality", str(self.session), "--max-warnings", "1")
        self.assertEqual(code, 0)
        self.assertIn("Verdict: valid and exports; untangled (real counts)", out)
        self.assertRegex(out, r"max skewness [0-9.]+ in b[01];")
        self.assertIn("corner screen (first cells at block corners, not checkMesh's measures)", out)
        self.assertIn("max corner-cell aspect ratio", out)
        self.assertIn("Warnings, the 1 worst of 3:", out)
        code, out, _ = run_cli("quality", str(self.session), "--all-warnings")
        self.assertIn("Warnings:", out)
        code, out, _ = run_cli("quality", str(self.session), "--json", "--no-interior")
        data = json.loads(out)
        self.assertIsNone(data["verdict"]["worst_skew_block"])
        self.assertFalse(data["verdict"]["sampled"])
        self.assertNotIn("interior", data["blocks"][0])
        code, out, _ = run_cli("quality", str(self.session), "--json", "--max-warnings", "2")
        data = json.loads(out)
        self.assertEqual((len(data["warnings"]), data["warnings_omitted"]), (2, 1))
        code, _, err = run_cli("quality", str(self.session), "--interior-cells", "-1")
        self.assertEqual(code, 1)

    def test_apply_quiet(self) -> None:
        code, out, _ = run_cli(
            "apply", str(self.session), "add_block v1-v2", "split_edge v0-v1 0.5",
            "--quiet", "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertIn("Applied 2 command(s)", out)
        self.assertIn("Created:", out)
        self.assertIn("2. split_edge:", out)
        code, out, _ = run_cli(
            "apply", str(self.session), "add_block v1-v2", "--quiet", "--json",
        )
        data = json.loads(out)
        self.assertNotIn("results", data)
        self.assertEqual(data["created"]["blocks"], ["b1"])


if __name__ == "__main__":
    unittest.main()
