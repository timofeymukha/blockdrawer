import unittest

from blockdrawer.commands import apply_commands, batch_digest, format_digest
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
        # A first cell above the uniform width: a shrinking series.
        shrinking = series_ratio(0.1, 1.0, 20)
        self.assertLess(shrinking, 1.0)
        self.assertAlmostEqual(0.1 * (1.0 - shrinking ** 20) / (1.0 - shrinking), 1.0)

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

    def test_the_quiet_note_carries_what_step_5_reads(self) -> None:
        model = channel()
        batch = apply_commands(model, ["size first_width=0.005 core_size=0.1 wall_aspect=10"])
        result = batch.results[0].result
        digest = batch_digest(model, batch)
        (note,) = digest["notes"]
        self.assertIn(note["budget_binding"], (True, False))
        self.assertEqual(note["budget_binding"], result["budget_binding"])
        self.assertRegex(note["verification"], r"^field \d+ faces >70, max [0-9.]+, skew [0-9.]+ \| result ")
        (band,) = result["bands"]
        self.assertEqual(band["asked"], 0.005)
        self.assertRegex(note["first_width"], r"^wall [0-9.e-]+\.\.[0-9.e-]+ for 0\.005 \([+-]\d+%\.\.[+-]\d+%\)$")
        worst = result["worst_interfaces"][0]
        self.assertTrue(note["worst_jump"].startswith(f"{worst['jump']:.3g} at {worst['edge']}"))
        text = "\n".join(format_digest(1, digest))
        self.assertNotIn("…", text)
        self.assertNotIn("=None", text)

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
        self.assertIn("first_width", note)
        self.assertIn("faces >70", note["verification"])
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
        # width) narrows to a 0.1 side that keeps those 100 cells: a strip
        # of forced-fine cells. The field alone does not see it; the budget
        # is spent grading the blocks beside it from it.
        options = dict(first_width=0.01, core_size=0.2, growth=1.2, split=False)
        model = fan()
        result = size_model(model, SizeOptions(**options))
        self.assertLess(result.jump_cost, 0.6 * result.field_jump_cost)
        self.assertLessEqual(result.total_cells, result.budget)
        self.assertEqual(result.budget, round(1.5 * result.field_cells))
        self.assertTrue(result.moves)
        rich = fan()
        richer = size_model(rich, SizeOptions(**options, budget=4 * result.field_cells))
        self.assertTrue(any(move["kind"] == "raise" for move in richer.moves))
        forced = 0.1 / rich.edge_cells[edge_key("b", "c")]
        at_b = rich.edge_width_at_vertex(edge_key("b", "e"), "b")
        self.assertLess(at_b, model.edge_width_at_vertex(edge_key("b", "e"), "b"))
        self.assertLess(at_b, 6.0 * forced)

    def test_more_budget_never_raises_the_jump_cost(self) -> None:
        # From the field's own state a smaller budget's moves are a prefix
        # of a larger one's: the greedy stops at the first move it cannot
        # afford.
        field_cells = size_model(fan(), SizeOptions(
            first_width=0.01, core_size=0.2, split=False)).field_cells
        costs = []
        # Below 1.5 times the field's own cells the field itself is
        # coarsened, a different start, so the claim is made from there up.
        for multiple in (1.5, 1.8, 2.5, 4.0):
            result = size_model(fan(), SizeOptions(
                first_width=0.01, core_size=0.2, split=False,
                budget=int(multiple * field_cells),
            ))
            self.assertLessEqual(result.total_cells, int(multiple * field_cells))
            costs.append(result.jump_cost)
        for smaller, larger in zip(costs, costs[1:]):
            self.assertLessEqual(larger, smaller + 1.0e-12)
        self.assertLess(costs[-1], costs[0])

    def test_a_move_the_sampled_check_refuses_is_not_taken(self) -> None:
        from unittest import mock

        import blockdrawer.quality  # noqa: F401 - bound to the real sampler first
        import blockdrawer.sampling as sampling

        real = sampling.sample_model
        calls = []

        def folding(model, *args, **kwargs):
            # Every guard sampling reports one more fold than the one
            # before, so every move looks like it folds a cell.
            sample = real(model, *args, **kwargs)
            if kwargs.get("blocks") is None or (args and args[0] is None):
                return sample
            calls.append(1)
            summary = {**sample.summary(), "folded_cells": len(calls)}
            return mock.Mock(summary=lambda: summary)

        options = SizeOptions(first_width=0.01, core_size=0.2, split=False)
        with mock.patch.object(sampling, "sample_model", folding):
            refused = size_model(fan(), options)
        self.assertEqual(refused.moves, [])
        self.assertIn("refused by the sampled check", refused.guard)
        self.assertEqual(refused.total_cells, refused.field_cells)
        self.assertEqual(refused.jump_cost, refused.field_jump_cost)

    def test_the_verification_undoes_what_the_coarse_guard_passed(self) -> None:
        # The guard samples 64 cells a side; the verification at 256 (and
        # real counts where faces are steep) is what the result promises.
        # Here every raised state is made to show one more face above 70
        # degrees in b1 than the field's - as at 64 a side, where the guard
        # was blind to blocks of hundreds of cells: the guard passes the
        # raises, the verification puts them back, deterministically.
        from unittest import mock

        import blockdrawer.size as size

        real = size._verify

        def run():
            seen = []

            def steep(model, neighbours):
                blocks, summary = real(model, neighbours)
                cells = sum(c[0] * c[1] for c in (model.block_cell_counts(x) for x in model.blocks))
                seen.append(cells)
                if cells > seen[0]:
                    blocks = {**blocks, "b1": {**blocks["b1"], "severe_faces": blocks["b1"]["severe_faces"] + 1}}
                    summary = {**summary, "severe_faces": summary["severe_faces"] + 1}
                return blocks, summary

            model = fan()
            with mock.patch.object(size, "_verify", steep):
                result = size_model(model, SizeOptions(
                    first_width=0.01, core_size=0.2, split=False, budget=32000))
            return model, result, seen[0]

        model, result, repaired = run()
        self.assertGreater(result.verification["undone_moves"], 0)
        self.assertIn("undone by the verification", result.guard)
        self.assertEqual(result.total_cells, repaired)
        self.assertFalse(any(move["kind"] == "raise" for move in result.moves))
        again, second, _ = run()
        self.assertEqual(to_data(model), to_data(again))
        self.assertEqual(result.verification, second.verification)

    def test_the_result_reports_its_verified_faces(self) -> None:
        result = size_model(fan(), SizeOptions(first_width=0.01, core_size=0.2, split=False))
        verification = result.to_data()["verification"]
        self.assertEqual(verification["cells_per_side"], 256)
        for state in ("field", "result"):
            self.assertIn("severe_faces", verification[state])
            self.assertIn("max_non_orthogonality", verification[state])
        self.assertLessEqual(verification["result"]["severe_faces"], verification["field"]["severe_faces"])
        self.assertLessEqual(
            verification["result"]["max_non_orthogonality"],
            verification["field"]["max_non_orthogonality"] + 0.5,
        )

    def test_a_ring_terminates(self) -> None:
        # A closed chain of interfaces (the O-grid's four blocks round the
        # body) spends its budget and stops, deterministically.
        first, second = o_grid(), o_grid()
        options = SizeOptions(first_width=0.01, core_size=0.4)
        one = size_model(first, options)
        size_model(second, options)
        self.assertEqual(to_data(first), to_data(second))
        self.assertLessEqual(one.total_cells, one.budget)

    def test_a_split_does_not_leave_a_sliver(self) -> None:
        # A channel narrowing to a 0.004 throat between walls: its inlet
        # side wants fine cells at both walls, but cutting the strip would
        # cut the throat into pieces shorter than one first cell.
        from blockdrawer.session import from_data

        data = to_data(MeshModel())
        points = {"a": (0.0, 0.0), "b": (4.0, 0.498), "c": (4.0, 0.502), "d": (0.0, 1.0)}
        data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
        data["blocks"] = [{"id": "b0", "vertices": ["a", "b", "c", "d"]}]
        data["edgeCells"] = [
            {"vertices": list(edge), "cells": 10}
            for edge in (("a", "b"), ("b", "c"), ("c", "d"), ("a", "d"))
        ]
        model = apply_commands(from_data(data), WALL + [
            "set_edge_boundary a-b wall", "set_edge_boundary c-d wall",
        ]).model
        result = size_model(model, SizeOptions(first_width=0.005, core_size=0.2))
        self.assertEqual(result.splits, [])
        self.assertEqual(len(model.blocks), 1)
        self.assertTrue(any("would be" in text and "are wanted" in text for text in result.refused),
                        result.refused)

    def test_an_edge_reached_fine_from_both_ends_is_split(self) -> None:
        # A channel 3 long whose walls are 1 apart: every transverse edge
        # wants a first cell at both walls, so its strip is cut where the
        # size peaks and each half is graded from its own wall.
        model = apply_commands(MeshModel(box=(0, 0, 3, 1)), WALL + [
            "set_edge_boundary v0-v1 wall", "set_edge_boundary v2-v3 wall",
        ]).model
        result = size_model(model, SizeOptions(first_width=0.002, core_size=0.2, growth=1.2))
        self.assertGreaterEqual(len(result.splits), 1)
        self.assertEqual(len(model.blocks), 2)


class DistanceMethodTests(unittest.TestCase):
    def test_item_3_sizing_is_reachable_and_deterministic(self) -> None:
        first, second, default = fan(), fan(), fan()
        options = dict(first_width=0.01, core_size=0.2, interface_growth=None, split=False)
        one = size_model(first, SizeOptions(**options, method="distance"))
        size_model(second, SizeOptions(**options, method="distance"))
        self.assertEqual(to_data(first), to_data(second))
        size_model(default, SizeOptions(first_width=0.01, core_size=0.2, split=False))
        # The tapered fan is where the two differ: the wall-distance field
        # leaves the narrow side's neighbours to the field alone.
        self.assertNotEqual(to_data(first)["edgeCells"], to_data(default)["edgeCells"])
        self.assertEqual(one.verification["method"], "distance")
        self.assertEqual(one.verification["field"], one.verification["result"])
        self.assertEqual(one.moves, [])
        with self.assertRaises(TopologyError):
            size_model(fan(), SizeOptions(**options, method="distance", keep_counts=True))

    def test_the_command_takes_the_method(self) -> None:
        batch = apply_commands(fan(), ["size 0.01 0.2 split=false method=distance"])
        data = batch.results[0].result
        self.assertEqual(data["verification"]["method"], "distance")
        with self.assertRaises(Exception):
            apply_commands(fan(), ["size 0.01 0.2 method=nope"])


def fan() -> MeshModel:
    """A fan b0 from the wall a-d narrowing to the 0.1 side b-c, which a
    thin strip b1 continues to x = 3, between b2 and b3."""
    from blockdrawer.session import from_data

    data = to_data(MeshModel())
    points = {"a": (0, 0), "b": (1, 0.45), "c": (1, 0.55), "d": (0, 1),
              "e": (3, 0.45), "f": (3, 0.55), "g": (3, 0.0), "h": (3, 1.0)}
    data["vertices"] = [{"id": key, "x": x, "y": y} for key, (x, y) in points.items()]
    quads = [("a", "b", "c", "d"), ("b", "e", "f", "c"), ("a", "g", "e", "b"), ("c", "f", "h", "d")]
    data["blocks"] = [{"id": f"b{index}", "vertices": list(quad)} for index, quad in enumerate(quads)]
    edges = set()
    for quad in quads:
        for k in range(4):
            edges.add(tuple(sorted((quad[k], quad[(k + 1) % 4]))))
    data["edgeCells"] = [{"vertices": list(edge), "cells": 10} for edge in sorted(edges)]
    return apply_commands(from_data(data), WALL + ["set_edge_boundary a-d wall"]).model


class SkewnessTests(unittest.TestCase):
    def test_the_face_skewness_is_checkmeshs(self) -> None:
        from blockdrawer.sampling import _skewness

        # The line between the cell centres crosses the face 0.5 from its
        # centre; the face's half-length 1 is the larger reference.
        self.assertAlmostEqual(_skewness((0.0, 0.0), (2.0, 1.0), (1.0, -1.0), (1.0, 1.0)), 0.5)
        # Centres in line with the face centre: no skewness.
        self.assertAlmostEqual(_skewness((0.0, 0.0), (2.0, 0.0), (1.0, -1.0), (1.0, 1.0)), 0.0)
        # A boundary face: the owner's centre projected along the face
        # normal lands 0.5 from the face centre.
        self.assertAlmostEqual(_skewness((0.0, 0.5), None, (1.0, -1.0), (1.0, 1.0)), 0.5)
        # A short face: 0.2 of the centre distance is the reference.
        self.assertAlmostEqual(
            _skewness((0.0, 0.0), (10.0, 1.0), (5.0, -0.1), (5.0, 0.1)), 0.5 / (0.2 * 101 ** 0.5)
        )

    def test_a_rectangle_has_none_and_the_verdict_reports_it(self) -> None:
        from blockdrawer.quality import assess_quality
        from blockdrawer.sampling import sample_model

        model = MeshModel(box=(0, 0, 2, 1))
        self.assertLess(sample_model(model).summary()["max_skewness"], 1.0e-9)
        apply = apply_commands(model, ["move_vertex v2 3.5 1.2"]).model
        skew = sample_model(apply).summary()["max_skewness"]
        self.assertGreater(skew, 0.0)
        verdict = assess_quality(apply, interior_cells=64).verdict()
        self.assertAlmostEqual(verdict["max_skewness"], skew)

    def test_smooth_refuses_a_result_above_checkmeshs_skewness_limit(self) -> None:
        from blockdrawer.smooth import _no_worse

        before = {"folded_cells": 0, "severe_faces": 0, "max_non_orthogonality": 60.0,
                  "max_skewness": 3.5}
        worse = {**before, "max_non_orthogonality": 50.0, "max_skewness": 4.2}
        self.assertFalse(_no_worse(worse, before))
        self.assertTrue(_no_worse(worse, {**before, "max_skewness": 4.5}))



def periodic_ring() -> MeshModel:
    """The 3x3 ring round a hole, periodic between y = 0 and y = 3, the hole
    lowered to y 0.6 .. 1.5 with a wall only on its bottom: the lower and
    upper periodic edges of the middle column lie in different count
    components and the field asks different things of them."""
    from tests.helpers import build_ring_model, vertex_at

    model = build_ring_model()
    for x in (1.0, 2.0):
        model.move_vertex(vertex_at(model, (x, 2.0)), x, 1.5)
        model.move_vertex(vertex_at(model, (x, 1.0)), x, 0.6)
    for name, kind in (("body", "wall"), ("lower", "patch"), ("upper", "patch"), ("sides", "patch")):
        model.add_boundary(name)
        model.set_boundary_type(name, kind)
    model.set_boundary_type("lower", "cyclic", neighbour_patch="upper")
    for current in model.edges():
        if not model.is_boundary_edge(current):
            continue
        ys = {model.vertices[v].y for v in current}
        xs = {model.vertices[v].x for v in current}
        if ys == {0.0}:
            model.set_edge_boundary(current, "lower")
        elif ys == {3.0}:
            model.set_edge_boundary(current, "upper")
        elif ys == {0.6}:
            model.set_edge_boundary(current, "body")
        else:
            model.set_edge_boundary(current, "sides")
    return model


class CyclicPairTests(unittest.TestCase):
    def test_partner_edges_get_one_count_and_one_physical_grading(self) -> None:
        from blockdrawer.cyclic import cyclic_pairs
        from blockdrawer.foam import block_mesh_dict

        model = periodic_ring()
        result = size_model(model, SizeOptions(0.005, 0.2, growth=1.2, wall_aspect=10))
        (pair,) = cyclic_pairs(model, check_nodes=False)
        self.assertEqual(len(pair.matches), 3)
        for match in pair.matches:
            first = model.edge_total_expansion(match.first)
            second = model.edge_total_expansion(match.second)
            if not match.aligned:
                second = 1.0 / second
            self.assertEqual(model.edge_cells[match.first], model.edge_cells[match.second], match)
            self.assertAlmostEqual(first, second, places=12, msg=str(match))
        # Before pairs were joined the middle column's strips were split
        # separately and came out with 26 and 16 cells; now a strip holding
        # a cyclic edge is not split, and export accepts the pair node by node.
        self.assertTrue(any("cyclic edge" in text for text in result.refused), result.refused)
        block_mesh_dict(model)


if __name__ == "__main__":
    unittest.main()
