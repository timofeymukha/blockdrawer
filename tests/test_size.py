import math
import unittest

from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data
from blockdrawer.size import SizeOptions, series_ratio, size_model

WALL = ["add_boundary wall", "set_boundary_type wall wall"]


def channel() -> MeshModel:
    """One block 4 long and 1 high, walls top and bottom: every transverse
    edge wants a first cell at both ends."""
    return apply_commands(MeshModel(box=(0, 0, 4, 1)), WALL + [
        "set_edge_boundary v0-v1 wall", "set_edge_boundary v2-v3 wall",
    ]).model


def o_grid() -> MeshModel:
    """Four blocks round a square body 1 wide in a square 5 wide."""
    from blockdrawer.session import from_data

    data = to_data(MeshModel())
    points = {
        "v0": (-2.5, -2.5), "v1": (2.5, -2.5), "v2": (2.5, 2.5), "v3": (-2.5, 2.5),
        "i0": (-0.5, -0.5), "i1": (0.5, -0.5), "i2": (0.5, 0.5), "i3": (-0.5, 0.5),
    }
    data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
    quads = [("v0", "v1", "i1", "i0"), ("v1", "v2", "i2", "i1"),
             ("v2", "v3", "i3", "i2"), ("v3", "v0", "i0", "i3")]
    data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
    edges = set()
    for quad in quads:
        for k in range(4):
            edges.add(tuple(sorted((quad[k], quad[(k + 1) % 4]))))
    data["edgeCells"] = [{"vertices": list(edge), "cells": 10} for edge in sorted(edges)]
    model = from_data(data)
    return apply_commands(model, WALL + [
        f"set_edge_boundary {edge} wall" for edge in ("i0-i1", "i1-i2", "i2-i3", "i0-i3")
    ]).model


class SizeTests(unittest.TestCase):
    def test_series_ratio(self) -> None:
        ratio = series_ratio(0.01, 1.0, 20)
        self.assertAlmostEqual(0.01 * (ratio ** 20 - 1) / (ratio - 1), 1.0, places=9)
        self.assertEqual(series_ratio(0.05, 1.0, 20), 1.0)

    def test_a_channel_is_split_and_graded_from_both_walls(self) -> None:
        model = channel()
        result = size_model(model, SizeOptions(first_width=0.005, core_size=0.1, growth=1.2))
        self.assertEqual(len(result.splits), 1)
        self.assertAlmostEqual(result.splits[0]["fraction"], 0.5, places=6)
        self.assertEqual(len(model.blocks), 2)
        # Every transverse half starts at the wall with the first width.
        for vertex in ("v0", "v1", "v2", "v3"):
            spokes = [
                current for current in model.edges()
                if vertex in current and abs(model.vertices[current[0]].x - model.vertices[current[1]].x) < 1e-12
            ]
            (spoke,) = spokes
            # At most the first width; a whole count makes it a little finer.
            self.assertLessEqual(model.edge_width_at_vertex(spoke, vertex), 0.005 * (1 + 1e-9))
            self.assertGreater(model.edge_width_at_vertex(spoke, vertex), 0.004)
        self.assertLess(result.worst_interface, 1.0 + 1e-6 + 0.25)
        self.assertEqual(result.bands[0]["patch"], "wall")
        self.assertEqual(result.total_cells, sum(
            model.block_cell_counts(block)[0] * model.block_cell_counts(block)[1]
            for block in model.blocks
        ))
        unsplit = channel()
        kept = size_model(unsplit, SizeOptions(first_width=0.005, core_size=0.1, split=False))
        self.assertEqual(kept.splits, [])
        self.assertEqual(len(unsplit.blocks), 1)

    def test_an_o_grid_is_graded_from_the_body(self) -> None:
        model = o_grid()
        result = size_model(model, SizeOptions(first_width=0.01, core_size=0.4, growth=1.2))
        self.assertEqual(result.splits, [])
        spoke = edge_key("v0", "i0")
        self.assertLessEqual(model.edge_width_at_vertex(spoke, "i0"), 0.01 * (1 + 1e-9))
        self.assertGreater(model.edge_width_at_vertex(spoke, "i0"), 0.009)
        far = model.edge_width_at_vertex(spoke, "v0")
        self.assertGreater(far, 0.1)
        # The wall is resolved at the first width along it without wall_aspect,
        # and at 10 first widths with it.
        coarse = o_grid()
        stretched = size_model(coarse, SizeOptions(first_width=0.01, core_size=0.4, wall_aspect=10.0))
        self.assertLess(stretched.total_cells, result.total_cells / 3)
        override = o_grid()
        size_model(override, SizeOptions(
            first_width=0.01, core_size=0.4, patch_first_widths=(("wall", 0.002),)
        ))
        self.assertLessEqual(override.edge_width_at_vertex(spoke, "i0"), 0.002 * (1 + 1e-9))
        self.assertGreater(override.edge_width_at_vertex(spoke, "i0"), 0.0018)

    def test_without_a_wall_every_cell_is_the_core_size(self) -> None:
        model = MeshModel(box=(0, 0, 2, 1))
        result = size_model(model, SizeOptions(first_width=0.01, core_size=0.1))
        self.assertIn("no patch of type wall", result.note)
        self.assertEqual(model.edge_cells[edge_key("v0", "v1")], 20)
        self.assertEqual(model.edge_cells[edge_key("v1", "v2")], 10)
        self.assertEqual(model.edge_grading, {})

    def test_deterministic_budget_and_errors(self) -> None:
        first, second = channel(), channel()
        options = SizeOptions(first_width=0.005, core_size=0.1, interface_growth=1.25)
        size_model(first, options)
        size_model(second, options)
        self.assertEqual(to_data(first), to_data(second))
        capped = channel()
        result = size_model(capped, SizeOptions(first_width=0.005, core_size=0.1, budget=2000))
        self.assertLessEqual(result.total_cells, 2000)
        self.assertLess(result.budget_factor, 1.0)
        with self.assertRaises(TopologyError):
            size_model(channel(), SizeOptions(first_width=0.2, core_size=0.1))
        with self.assertRaises(TopologyError):
            size_model(channel(), SizeOptions(
                first_width=0.01, core_size=0.1, patch_first_widths=(("nope", 0.01),)
            ))

    def test_the_command_is_one_undoable_action(self) -> None:
        model = channel()
        history = ModelHistory(model)
        before = to_data(model)
        batch = apply_commands(model, ["size first_width=0.005 core_size=0.1 wall_aspect=10"])
        history.record(batch.model)
        self.assertNotEqual(to_data(batch.model), before)
        restored = history.undo()
        self.assertEqual(to_data(restored), before)
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["op"], "size")
        self.assertEqual(len(note["splits"]), 1)
        scoped = apply_commands(o_grid(), [
            "size first_width=0.01 core_size=0.4 blocks=b1 first_widths=wall=0.005",
        ]).results[0].result
        self.assertGreater(scoped["total_cells"], 0)


def step() -> MeshModel:
    """A backward-facing step as an L of three blocks: the upstream channel
    b0 (x -2..0, y 0..1), the block above the step b1 (x 0..2, y 0..1) and
    the block below it b2 (x 0..2, y -1..0). Walls: the upstream floor, the
    step face, the downstream floor and the ceiling."""
    from blockdrawer.session import from_data

    data = to_data(MeshModel())
    points = {
        "a": (-2.0, 0.0), "lip": (0.0, 0.0), "b": (2.0, 0.0), "c": (2.0, 1.0),
        "d": (0.0, 1.0), "e": (-2.0, 1.0), "foot": (0.0, -1.0), "f": (2.0, -1.0),
    }
    data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
    quads = [("a", "lip", "d", "e"), ("lip", "b", "c", "d"), ("foot", "f", "b", "lip")]
    data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
    edges = set()
    for quad in quads:
        for k in range(4):
            edges.add(tuple(sorted((quad[k], quad[(k + 1) % 4]))))
    data["edgeCells"] = [{"vertices": list(edge), "cells": 10} for edge in sorted(edges)]
    model = from_data(data)
    return apply_commands(model, WALL + [
        f"set_edge_boundary {edge} wall" for edge in ("a-lip", "foot-lip", "f-foot", "d-e", "c-d")
    ]).model


class ContinuityTests(unittest.TestCase):
    def test_the_upstream_wall_is_graded_towards_the_lip(self) -> None:
        model = step()
        size_model(model, SizeOptions(
            first_width=0.005, core_size=0.25, growth=1.1, wall_aspect=30.0, split=False,
        ))
        floor = edge_key("a", "lip")
        at_lip = model.edge_width_at_vertex(floor, "lip")
        upstream = model.edge_width_at_vertex(floor, "a")
        # The step face's normal runs along the upstream floor: the cells
        # along it shrink to about the first width at the lip.
        self.assertLess(at_lip, 0.01)
        self.assertGreater(upstream, 5 * at_lip)
        # The opposite side of b0 shares the series: no shear in a rectangle.
        from blockdrawer.sampling import sample_model

        self.assertLess(sample_model(model).summary()["max_non_orthogonality"], 1.0e-6)

    def test_a_strip_beside_a_fine_strip_is_graded_from_it(self) -> None:
        # A fan block b0 from a wall (1 long, 100 cells along it at the first
        # width) narrows to a 0.02 side that keeps those 100 cells: a strip
        # of forced-fine cells. The blocks beside it see only the field
        # until a continuity round feeds those widths back.
        from blockdrawer.session import from_data

        data = to_data(MeshModel())
        points = {"a": (0, 0), "b": (1, 0.49), "c": (1, 0.51), "d": (0, 1),
                  "e": (3, 0.49), "f": (3, 0.51), "g": (3, 0.0), "h": (3, 1.0)}
        data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
        quads = [("a", "b", "c", "d"), ("b", "e", "f", "c"), ("a", "g", "e", "b"), ("c", "f", "h", "d")]
        data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
        edges = set()
        for quad in quads:
            for k in range(4):
                edges.add(tuple(sorted((quad[k], quad[(k + 1) % 4]))))
        data["edgeCells"] = [{"vertices": list(edge), "cells": 10} for edge in sorted(edges)]
        base = apply_commands(from_data(data), WALL + ["set_edge_boundary a-d wall"]).model
        options = dict(first_width=0.01, core_size=0.2, growth=1.2, split=False)
        plain = from_data(to_data(base))
        before = size_model(plain, SizeOptions(**options))
        fed = from_data(to_data(base))
        after = size_model(fed, SizeOptions(**options, continuity_rounds=2))
        forced = 0.02 / fed.edge_cells[edge_key("b", "c")]
        self.assertLess(fed.edge_width_at_vertex(edge_key("b", "e"), "b"), 2.0 * forced)
        self.assertGreater(plain.edge_width_at_vertex(edge_key("b", "e"), "b"), 10.0 * forced)
        self.assertLess(after.worst_interface, before.worst_interface / 10.0)
        self.assertGreater(after.total_cells, before.total_cells)

    def test_a_ring_terminates(self) -> None:
        # A closed chain of interfaces (the O-grid's four blocks round the
        # body) with continuity rounds must stop, and deterministically.
        first, second = o_grid(), o_grid()
        options = SizeOptions(first_width=0.01, core_size=0.4, continuity_rounds=6)
        one = size_model(first, options)
        size_model(second, options)
        self.assertEqual(to_data(first), to_data(second))
        self.assertLessEqual(one.rounds, 6)
        self.assertLess(one.total_cells, 10 ** 6)

    def test_an_edge_reached_fine_from_both_ends_is_split(self) -> None:
        # The middle row of a three-row channel touches no wall, but the two
        # fine wall rows reach it from both sides: its strip is two-ended
        # once the widths are fed back, and is split.
        from blockdrawer.session import from_data

        data = to_data(MeshModel())
        ys = [0.0, 0.05, 0.95, 1.0]
        points = {}
        for row, y in enumerate(ys):
            points[f"l{row}"] = (0.0, y)
            points[f"r{row}"] = (3.0, y)
        data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
        quads = [(f"l{k}", f"r{k}", f"r{k + 1}", f"l{k + 1}") for k in range(3)]
        data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
        edges = set()
        for quad in quads:
            for k in range(4):
                edges.add(tuple(sorted((quad[k], quad[(k + 1) % 4]))))
        data["edgeCells"] = [{"vertices": list(edge), "cells": 10} for edge in sorted(edges)]
        model = apply_commands(from_data(data), WALL + [
            "set_edge_boundary l0-r0 wall", "set_edge_boundary l3-r3 wall",
        ]).model
        result = size_model(model, SizeOptions(
            first_width=0.002, core_size=0.2, growth=1.2, continuity_rounds=1,
        ))
        self.assertGreaterEqual(len(result.splits), 1)
        # The middle row's strip is cut in two: one more block.
        self.assertEqual(len(model.blocks), 4)


if __name__ == "__main__":
    unittest.main()
