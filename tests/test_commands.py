import json
import unittest

from blockdrawer.commands import (
    COMMANDS,
    CommandError,
    apply_command,
    apply_commands,
    command_specs,
    edge_text,
    format_result,
    parse_command,
    parse_edge,
    read_command_lines,
    resolve_curve,
    spec_help_text,
    to_json_value,
)
from blockdrawer.domain import EdgeSplitResult
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data


class EdgeParsingTests(unittest.TestCase):
    def test_edge_text_uses_canonical_order(self) -> None:
        self.assertEqual(edge_text(("v1", "v0")), "v0-v1")

    def test_parse_edge_accepts_text_in_either_order(self) -> None:
        model = MeshModel()
        self.assertEqual(parse_edge(model, "v1-v0"), edge_key("v0", "v1"))
        self.assertEqual(parse_edge(model, ["v1", "v0"]), edge_key("v0", "v1"))

    def test_parse_edge_handles_hyphenated_vertex_ids(self) -> None:
        model = MeshModel()
        model.vertices["a-b"] = model.vertices.pop("v0")
        model.vertices["a-b"].id = "a-b"
        model.blocks[0] = type(model.blocks[0])(
            "b0", ("a-b", "v1", "v2", "v3")
        )
        model.edge_cells = {edge: 10 for edge in model.edges()}
        model.validate()
        self.assertEqual(parse_edge(model, "a-b-v1"), edge_key("a-b", "v1"))

    def test_parse_edge_rejects_unknown_and_non_topological_pairs(self) -> None:
        model = MeshModel()
        with self.assertRaisesRegex(CommandError, "Unknown edge"):
            parse_edge(model, "v0-v9")
        with self.assertRaisesRegex(CommandError, "not an edge of any block"):
            parse_edge(model, "v0-v2")
        with self.assertRaisesRegex(CommandError, "two vertex IDs"):
            parse_edge(model, ["v0"])

    def test_resolve_curve_by_id_or_unique_name(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 1.0)), name="wall")
        self.assertEqual(resolve_curve(model, curve.id), curve.id)
        self.assertEqual(resolve_curve(model, "wall"), curve.id)
        with self.assertRaisesRegex(CommandError, "Unknown reference curve"):
            resolve_curve(model, "missing")


class ParseCommandTests(unittest.TestCase):
    def test_text_positional_and_keyword_arguments(self) -> None:
        name, raw = parse_command(
            "set_edge_grading v0-v1 total_ratio 4 propagate=true"
        )
        self.assertEqual(name, "set_edge_grading")
        self.assertEqual(
            raw,
            {
                "edge": "v0-v1",
                "parameter": "total_ratio",
                "value": "4",
                "propagate": "true",
            },
        )

    def test_json_text_and_dict_forms(self) -> None:
        payload = {"op": "set_edge_cells", "edge": ["v0", "v1"], "cells": 5}
        self.assertEqual(parse_command(payload), ("set_edge_cells", {
            "edge": ["v0", "v1"], "cells": 5,
        }))
        self.assertEqual(parse_command(json.dumps(payload))[0], "set_edge_cells")

    def test_rejects_unknown_commands_and_arguments(self) -> None:
        with self.assertRaisesRegex(CommandError, "Unknown command"):
            parse_command("frobnicate v0-v1")
        with self.assertRaisesRegex(CommandError, "Unknown argument 'foo'"):
            parse_command("add_block v0-v1 foo=1")
        with self.assertRaisesRegex(CommandError, "Too many arguments"):
            parse_command("add_block v0-v1 v1-v2")
        with self.assertRaisesRegex(CommandError, '"op" field'):
            parse_command({"edge": "v0-v1"})
        with self.assertRaisesRegex(CommandError, "Empty command"):
            parse_command("   ")

    def test_quoted_point_lists_survive_shell_splitting(self) -> None:
        name, raw = parse_command("add_curve '0,0;0.5,0.2;1,0' name=top")
        self.assertEqual(name, "add_curve")
        self.assertEqual(raw["points"], "0,0;0.5,0.2;1,0")
        self.assertEqual(raw["name"], "top")

    def test_read_command_lines_skips_blanks_and_comments(self) -> None:
        text = "# header\n\nset_edge_cells v0-v1 4\n   # indented comment\nadd_block v1-v2\n"
        self.assertEqual(
            read_command_lines(text),
            ["set_edge_cells v0-v1 4", "add_block v1-v2"],
        )


class ApplyCommandTests(unittest.TestCase):
    def test_every_registered_command_has_usage_and_help(self) -> None:
        for spec in command_specs():
            self.assertTrue(spec.help)
            self.assertTrue(spec.usage().startswith(spec.name))
            described = spec.describe()
            self.assertEqual(described["name"], spec.name)
        self.assertIn("set_edge_cells <edge> <cells>", spec_help_text())

    def test_set_edge_cells_propagates_and_reports(self) -> None:
        model = MeshModel()
        result = apply_command(model, "set_edge_cells v0-v1 7")
        self.assertEqual(result.name, "set_edge_cells")
        self.assertEqual(result.result["cells"], 7)
        self.assertEqual(result.result["affected_edges"], ["v0-v1", "v2-v3"])
        self.assertEqual(model.edge_cells[edge_key("v2", "v3")], 7)
        self.assertEqual(result.arguments, {"edge": "v0-v1", "cells": 7})

    def test_batch_is_atomic_and_leaves_source_model_untouched(self) -> None:
        model = MeshModel()
        original = to_data(model)
        with self.assertRaisesRegex(CommandError, r"Command 2 \(set_edge_cells v0-v1 0\)"):
            apply_commands(model, ["set_edge_cells v0-v1 5", "set_edge_cells v0-v1 0"])
        self.assertEqual(to_data(model), original)

        batch = apply_commands(model, ["set_edge_cells v0-v1 5", "add_block v1-v2"])
        self.assertEqual(to_data(model), original)
        self.assertEqual(len(batch.model.blocks), 2)
        self.assertEqual(len(batch.results), 2)
        self.assertEqual(batch.results[1].result["block"], "b1")
        self.assertEqual(batch.results[1].result["opposite_edge"], "v4-v5")

    def test_edge_workflow_commands(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "set_edge_type v0-v1 arc",
            "set_control_point v0-v1 0 0.5 -0.3",
            "set_edge_type v2-v3 spline",
            "add_control_point v2-v3",
            "set_control_point_count v2-v3 3",
            "remove_control_point v2-v3 1",
            "reset_control_points v2-v3",
            {"op": "set_edge_grading", "edge": "v0-v1", "parameter": "cell_ratio",
             "value": 1.1, "propagate": True},
        ])
        edited = batch.model
        self.assertEqual(edited.edge_type(edge_key("v0", "v1")), "arc")
        self.assertEqual(edited.arc_point(edge_key("v0", "v1")), (0.5, -0.3))
        self.assertEqual(len(edited.edge_control_points(edge_key("v2", "v3"))), 2)
        self.assertEqual(batch.results[1].result["control_points"], [[0.5, -0.3]])
        self.assertIn("total_ratio", batch.results[-1].result)
        self.assertNotEqual(edited.edge_total_expansion(edge_key("v2", "v3")), 1.0)

    def test_topology_commands(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "add_block v1-v2",
            "split_edge v0-v1 0.5",
            "add_vertex 5 5",
            "move_vertex v3 -0.1 1.0",
        ])
        edited = batch.model
        self.assertEqual(len(edited.blocks), 3)
        split = batch.results[1].result
        self.assertEqual(split["source_edge"], "v0-v1")
        self.assertEqual(len(split["cut_edges"]), 1)
        self.assertEqual(batch.results[2].result["vertex"], "v8")
        self.assertEqual(edited.vertices["v3"].x, -0.1)

        combined = apply_commands(edited, [
            f"combine_blocks {split['cut_edges'][0]}",
        ]).model
        self.assertEqual(len(combined.blocks), 2)
        with self.assertRaisesRegex(CommandError, "At least one block must remain"):
            apply_commands(combined, ["remove_edge v1-v2"])
        removed = apply_commands(combined, ["remove_edge v4-v5"])
        self.assertEqual(len(removed.results[0].result["removed_blocks"]), 1)
        self.assertEqual(len(removed.model.blocks), 1)

    def test_add_block_from_vertices_and_remove_edge_keep_one_block(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "add_vertex 2 0",
            "add_vertex 2 1",
            "add_block_from_vertices v1,v4,v5,v2",
        ])
        self.assertEqual(len(batch.model.blocks), 2)
        with self.assertRaisesRegex(CommandError, "remove_edge"):
            apply_commands(model, ["remove_edge v0-v1"])

    def test_boundary_commands(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "add_boundary inlet",
            "add_boundary outlet",
            "set_edge_boundary v0-v3 inlet",
            "set_edge_boundary v1-v2 outlet",
            "set_boundary_type inlet cyclic neighbour_patch=outlet",
            "set_edge_boundary v1-v2 none",
            "set_boundary_type outlet wall",
            "remove_boundary inlet",
        ])
        edited = batch.model
        self.assertEqual(sorted(edited.boundaries), ["outlet"])
        self.assertEqual(edited.boundaries["outlet"].kind, "wall")
        self.assertEqual(batch.results[4].result["affected"], ["inlet", "outlet"])
        self.assertIsNone(batch.results[5].result["boundary"])
        self.assertNotIn(edge_key("v1", "v2"), edited.edge_boundaries)

    def test_spacing_link_commands(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "set_edge_grading v0-v1 total_ratio 8",
            "add_spacing_link v0-v1 v1-v2",
            "synchronize_spacing_links v0-v1",
            "remove_spacing_link v1-v2 v0-v1",
        ])
        link = batch.results[1].result
        self.assertEqual(link["vertex"], "v1")
        self.assertTrue(link["synchronized"])
        self.assertEqual(batch.results[2].result["affected_edges"], ["v0-v1", "v1-v2"])
        self.assertFalse(batch.model.spacing_links)

    def test_curve_and_projection_commands(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "add_curve '0,-0.2;0.5,-0.4;1,-0.2' name=lower",
            "project lower y vertices=v0",
            "project lower orthogonal edges=v0-v1 fit=true fit_max_points=20",
            "remove_curve lower",
        ])
        edited = batch.model
        self.assertEqual(batch.results[0].result["curve"], "g0")
        self.assertEqual(edited.vertices["v0"].y, -0.2)
        projection = batch.results[2].result
        self.assertEqual(projection["fitted_edges"], ["v0-v1"])
        self.assertEqual(projection["vertex_ids"], ["v0", "v1"])
        self.assertEqual(edited.edge_type(edge_key("v0", "v1")), "spline")
        self.assertFalse(edited.geometry_curves)

    def test_project_requires_exactly_one_selection_kind(self) -> None:
        model = MeshModel()
        model.add_geometry_curve(((0.0, -0.5), (1.0, -0.5)), name="lower")
        with self.assertRaisesRegex(CommandError, "not both"):
            apply_command(model, "project lower y vertices=v0 edges=v0-v1")

    def test_import_curve_reads_point_file(self) -> None:
        import tempfile
        from pathlib import Path

        model = MeshModel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.txt"
            path.write_text("# comment\n0 0\n0.5, 0.1\n1 0\n", encoding="utf-8")
            batch = apply_commands(model, [f"import_curve {path} name=imported"])
        curve = next(iter(batch.model.geometry_curves.values()))
        self.assertEqual(curve.name, "imported")
        self.assertEqual(len(curve.points), 3)
        self.assertFalse(curve.show_points)
        with self.assertRaisesRegex(CommandError, "Could not read"):
            apply_commands(model, ["import_curve /nonexistent/points.txt"])

    def test_set_export_settings_accepts_partial_updates(self) -> None:
        model = MeshModel()
        batch = apply_commands(model, [
            "set_export_settings z_cells=3 scale=0.001",
            {"op": "set_export_settings", "z_min_patch_type": "empty",
             "z_max_patch_type": "empty"},
        ])
        edited = batch.model
        self.assertEqual(edited.z_cells, 3)
        self.assertEqual(edited.scale, 0.001)
        self.assertEqual(edited.z_min_patch_type, "empty")
        self.assertEqual(edited.z_min_patch_name, "zMin")
        with self.assertRaisesRegex(CommandError, "distinct names"):
            apply_commands(model, ["set_export_settings z_max_patch_name=zMin"])

    def test_argument_coercion_errors_are_command_errors(self) -> None:
        model = MeshModel()
        cases = {
            "set_edge_cells v0-v1 many": "must be an integer",
            "move_vertex v0 nan 0": "must be finite",
            "move_vertex v9 0 0": "Unknown vertex",
            "set_edge_type v0-v1 bezier": "must be one of",
            "set_edge_grading v0-v1 total_ratio 2 propagate=maybe": "true or false",
            "add_curve '0,0;1'": "x,y point",
        }
        for command, message in cases.items():
            with self.subTest(command=command):
                with self.assertRaisesRegex(CommandError, message):
                    apply_command(model, command)

    def test_model_failures_roll_back_and_are_reported(self) -> None:
        model = MeshModel()
        before = to_data(model)
        with self.assertRaisesRegex(CommandError, "strictly convex"):
            apply_command(model, "move_vertex v0 2 2")
        self.assertEqual(to_data(model), before)


class SerializationTests(unittest.TestCase):
    def test_to_json_value_distinguishes_edges_from_vertex_pairs(self) -> None:
        result = EdgeSplitResult(
            ("v0", "v1"), 0.5, 1, 1, (("v0", "v1"),), ("v4", "v5"),
            (("v0", "v4"), ("v1", "v4")), (("v4", "v5"),), ("b1",),
        )
        data = to_json_value(result)
        self.assertEqual(data["source_edge"], "v0-v1")
        self.assertEqual(data["split_vertex_ids"], ["v4", "v5"])
        self.assertEqual(data["selected_segments"], ["v0-v4", "v1-v4"])
        self.assertEqual(to_json_value({"vertices": ("v0", "v1")}), {"vertices": ["v0", "v1"]})
        self.assertEqual(to_json_value({"edges": {("v0", "v1"): 3}}), {"edges": {"v0-v1": 3}})

    def test_format_result_is_compact(self) -> None:
        model = MeshModel()
        result = apply_command(model, "set_edge_cells v0-v1 4")
        text = format_result(result)
        self.assertTrue(text.startswith("1. set_edge_cells: cells=4"))
        self.assertIn("affected_edges=[v0-v1, v2-v3]", text)
        self.assertIn("set_edge_cells", COMMANDS)
        json.dumps(result.to_data())


if __name__ == "__main__":
    unittest.main()
