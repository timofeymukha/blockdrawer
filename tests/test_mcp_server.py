import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from blockdrawer import mcp_server
from blockdrawer.mcp_server import (
    INSTRUCTIONS,
    TOOLS,
    ToolFailure,
    check_mesh,
    describe_session,
    edit_session,
    export_block_mesh_dict,
    list_commands,
    new_session,
    quality_report,
    render_session,
    validate_session,
)
from blockdrawer.model import MeshModel
from blockdrawer.session import SessionError, load_session, save_session

try:  # pragma: no cover - depends on the environment
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:  # pragma: no cover
    HAVE_MCP = False

try:  # pragma: no cover - depends on the environment
    import PIL  # noqa: F401
    HAVE_PILLOW = True
except ImportError:  # pragma: no cover
    HAVE_PILLOW = False


class ToolFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.session = self.root / "session.json"
        save_session(MeshModel(), self.session)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_new_session(self) -> None:
        target = self.root / "fresh.json"
        data = new_session(str(target))
        self.assertTrue(data["ok"])
        self.assertEqual(data["summary"]["blocks"], 1)
        with self.assertRaisesRegex(ToolFailure, "already exists"):
            new_session(str(target))
        self.assertTrue(new_session(str(target), force=True)["ok"])

    def test_describe_sections_filter_and_text(self) -> None:
        edit_session(str(self.session), ["add_block v1-v2"], in_place=True)
        data = describe_session(str(self.session))
        self.assertEqual(data["summary"]["blocks"], 2)
        self.assertIn("edges", data)
        partial = describe_session(str(self.session), sections=["summary"])
        self.assertEqual(list(partial), ["summary"])
        only = describe_session(str(self.session), sections=["edges"], only=["b1"])
        self.assertTrue(all("b1" in edge["blocks"] for edge in only["edges"]))
        text = describe_session(str(self.session), as_text=True)["text"]
        self.assertIn("Topology: 2 block(s)", text)
        with self.assertRaisesRegex(ToolFailure, "Unknown section"):
            describe_session(str(self.session), sections=["nope"])
        json.dumps(data)

    def test_describe_missing_file_raises_session_error(self) -> None:
        with self.assertRaises(SessionError):
            describe_session(str(self.root / "missing.json"))

    def test_list_commands(self) -> None:
        data = list_commands()
        names = [spec["name"] for spec in data["commands"]]
        self.assertIn("set_edge_cells", names)
        self.assertIn("edge_notation", data)
        single = list_commands("split_edge")
        self.assertEqual(single["commands"][0]["name"], "split_edge")
        with self.assertRaisesRegex(ToolFailure, "Unknown command"):
            list_commands("nope")

    def test_edit_session_saves_atomically(self) -> None:
        output = self.root / "edited.json"
        data = edit_session(
            str(self.session),
            ["set_edge_cells v0-v1 6", {"op": "add_block", "edge": ["v1", "v2"]}],
            output=str(output),
        )
        self.assertTrue(data["saved"])
        self.assertEqual(data["applied"], 2)
        self.assertEqual(data["results"][1]["result"]["block"], "b1")
        self.assertEqual(len(data["log"]), 2)
        self.assertEqual(len(load_session(output).blocks), 2)
        self.assertEqual(len(load_session(self.session).blocks), 1)

        from blockdrawer.commands import CommandError

        with self.assertRaisesRegex(CommandError, "Command 2"):
            edit_session(
                str(self.session), ["add_block v1-v2", "set_edge_cells v0-v1 0"],
                in_place=True,
            )
        self.assertEqual(len(load_session(self.session).blocks), 1)

        dry = edit_session(str(self.session), ["set_edge_cells v0-v1 9"], dry_run=True)
        self.assertFalse(dry["saved"])
        unsaved = edit_session(str(self.session), ["set_edge_cells v0-v1 9"])
        self.assertFalse(unsaved["saved"])
        self.assertEqual(load_session(self.session).edge_cells[("v0", "v1")], 10)
        with self.assertRaisesRegex(ToolFailure, "mutually exclusive"):
            edit_session(str(self.session), ["add_block v1-v2"], output="x.json", in_place=True)
        with self.assertRaisesRegex(ToolFailure, "No commands"):
            edit_session(str(self.session), [])

    def test_edit_session_reads_a_commands_file(self) -> None:
        batch = self.root / "batch.jsonl"
        batch.write_text(
            "# a generated batch\n"
            "set_edge_cells v0-v1 6\n"
            "\n"
            '{"op": "add_block", "edge": ["v1", "v2"]}\n',
            encoding="utf-8",
        )
        output = self.root / "edited.json"
        data = edit_session(
            str(self.session), commands_file=str(batch), output=str(output),
        )
        self.assertEqual(data["applied"], 2)
        self.assertEqual(len(load_session(output).blocks), 2)
        # Inline commands follow the file, in one atomic batch.
        both = edit_session(
            str(self.session),
            ["set_edge_cells v0-v1 7"],
            commands_file=str(batch),
            dry_run=True,
        )
        self.assertEqual(both["applied"], 3)
        self.assertIn("set_edge_cells", both["log"][-1])
        self.assertFalse(both["saved"])
        with self.assertRaisesRegex(ToolFailure, "does not exist"):
            edit_session(str(self.session), commands_file=str(self.root / "missing.jsonl"))
        empty = self.root / "empty.jsonl"
        empty.write_text("# nothing\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolFailure, "No commands"):
            edit_session(str(self.session), commands_file=str(empty))

    def test_quality_report(self) -> None:
        edit_session(
            str(self.session), ["set_edge_grading v0-v1 total_ratio 20"], in_place=True,
        )
        data = quality_report(str(self.session))
        self.assertEqual(data["summary"]["blocks"], 1)
        self.assertGreater(data["summary"]["warning_count"], 0)
        lenient = quality_report(str(self.session), cell_growth_ratio=5.0)
        self.assertEqual(lenient["thresholds"]["cell_growth_ratio"], 5.0)
        self.assertEqual(lenient["summary"]["warning_count"], 0)
        text = quality_report(str(self.session), as_text=True)["text"]
        self.assertIn("Quality: 1 block(s)", text)

    def test_export_validate(self) -> None:
        text = export_block_mesh_dict(str(self.session))["text"]
        self.assertIn("hex (0 1 2 3 4 5 6 7)", text)
        case = self.root / "case"
        data = export_block_mesh_dict(str(self.session), case=str(case))
        self.assertTrue((case / "system" / "blockMeshDict").is_file())
        self.assertEqual(data["blocks"], 1)
        with self.assertRaisesRegex(ToolFailure, "mutually exclusive"):
            export_block_mesh_dict(str(self.session), output="a", case="b")
        self.assertTrue(validate_session(str(self.session))["ok"])

    @unittest.skipUnless(HAVE_PILLOW and HAVE_MCP, "Pillow and mcp are required")
    def test_render_returns_image_and_info(self) -> None:
        edit_session(str(self.session), ["add_block v1-v2"], in_place=True)
        image, info = render_session(
            str(self.session), width=300, height=200, zoom=["b1"], highlight=["v4"],
            preview=True, title="T",
        )
        self.assertEqual(info["width"], 300)
        self.assertEqual(len(info["bounds"]), 4)
        self.assertTrue(image.data.startswith(b"\x89PNG"))
        output = self.root / "pic.svg"
        _, info = render_session(str(self.session), output=str(output), width=200, height=200)
        self.assertEqual(info["output"], str(output))
        self.assertTrue(output.is_file())
        with self.assertRaisesRegex(ToolFailure, "mutually exclusive"):
            render_session(str(self.session), zoom=["b0"], bounds=[0, 0, 1, 1])
        with self.assertRaisesRegex(ToolFailure, "four numbers"):
            render_session(str(self.session), bounds=[0, 0, 1])

    def test_check_mesh_with_stub(self) -> None:
        stub = self.root / "stub.py"
        stub.write_text(
            "import sys\n"
            "print('  nCells: 100' if 'blockMesh' in sys.argv else 'Mesh OK.')\n",
            encoding="utf-8",
        )
        data = check_mesh(
            str(self.session),
            blockmesh_command=f"{sys.executable} {stub} blockMesh",
            case_dir=str(self.root / "case"),
            include_logs=True,
        )
        self.assertTrue(data["ok"])
        self.assertEqual(data["mesh_stats"]["cells"], 100)
        self.assertIn("checkMesh: Mesh OK", data["text"])
        self.assertIn("Mesh OK.", data["check_mesh"]["log"])

    def test_tool_table_is_complete(self) -> None:
        names = {function.__name__ for function, _ in TOOLS}
        self.assertEqual(names, {
            "describe_session", "list_commands", "quality_report", "render_session",
            "validate_session", "edit_session", "new_session",
            "export_block_mesh_dict", "check_mesh",
        })
        for function, _ in TOOLS:
            self.assertTrue(function.__doc__, function.__name__)
        self.assertIn("edit_session", INSTRUCTIONS)
        self.assertIn("commands_file", INSTRUCTIONS)
        # Methodology lives in the repository skill, and the pointer must exist.
        self.assertIn(".claude/skills/mesh-blocking/SKILL.md", INSTRUCTIONS)
        skill = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "mesh-blocking" / "SKILL.md"
        self.assertTrue(skill.is_file(), skill)
        text = skill.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: mesh-blocking"))
        for tool in ("edit_session", "quality_report", "render_session", "check_mesh"):
            self.assertIn(tool, text, tool)


@unittest.skipUnless(HAVE_MCP, "mcp is not installed")
class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.session = self.root / "session.json"
        save_session(MeshModel(), self.session)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    def test_tools_are_registered_with_schemas_and_annotations(self) -> None:
        from mcp.client import Client

        server = mcp_server.build_server()

        async def scenario():
            async with Client(server) as client:
                listing = await client.list_tools()
                return {tool.name: tool for tool in listing.tools}

        tools = self._run(scenario())
        self.assertEqual(
            set(tools), {function.__name__ for function, _ in TOOLS}
        )
        edit = tools["edit_session"]
        self.assertIn("commands", edit.input_schema["properties"])
        self.assertIn("path", edit.input_schema["required"])
        self.assertFalse(edit.annotations.read_only_hint)
        self.assertTrue(tools["describe_session"].annotations.read_only_hint)
        self.assertIn("atomic", edit.description)

    def test_call_tools_through_client(self) -> None:
        from mcp.client import Client

        server = mcp_server.build_server()

        async def scenario():
            async with Client(server) as client:
                edited = await client.call_tool("edit_session", {
                    "path": str(self.session),
                    "commands": ["add_block v1-v2", {"op": "add_boundary", "name": "inlet"}],
                    "in_place": True,
                })
                described = await client.call_tool("describe_session", {
                    "path": str(self.session), "sections": ["summary"],
                })
                failed = await client.call_tool("edit_session", {
                    "path": str(self.session), "commands": ["set_edge_cells v0-v1 0"],
                })
                missing = await client.call_tool("validate_session", {
                    "path": str(self.root / "missing.json"),
                })
                return edited, described, failed, missing

        edited, described, failed, missing = self._run(scenario())
        self.assertFalse(edited.is_error)
        payload = json.loads(edited.content[0].text)
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["summary"]["blocks"], 2)
        self.assertEqual(json.loads(described.content[0].text)["summary"]["boundaries"], 1)
        self.assertTrue(failed.is_error)
        self.assertIn("Command 1", failed.content[0].text)
        self.assertIn("positive integer", failed.content[0].text)
        self.assertTrue(missing.is_error)
        self.assertIn("Could not read", missing.content[0].text)

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is not installed")
    def test_render_tool_returns_image_content(self) -> None:
        from mcp.client import Client

        server = mcp_server.build_server()

        async def scenario():
            async with Client(server) as client:
                return await client.call_tool("render_session", {
                    "path": str(self.session), "width": 200, "height": 150,
                })

        result = self._run(scenario())
        self.assertFalse(result.is_error)
        kinds = [type(block).__name__ for block in result.content]
        self.assertEqual(kinds, ["ImageContent", "TextContent"])
        self.assertEqual(result.content[0].mime_type, "image/png")
        self.assertEqual(json.loads(result.content[1].text)["width"], 200)

    def test_main_reports_missing_dependency_cleanly(self) -> None:
        import contextlib
        import io
        import unittest.mock

        err = io.StringIO()
        with unittest.mock.patch.object(
            mcp_server, "build_server", side_effect=ImportError("needs mcp")
        ), contextlib.redirect_stderr(err):
            code = mcp_server.main([])
        self.assertEqual(code, 1)
        self.assertIn("needs mcp", err.getvalue())


if __name__ == "__main__":
    unittest.main()
