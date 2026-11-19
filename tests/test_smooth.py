import math
from dataclasses import replace
import unittest
from unittest import mock

from blockdrawer import smooth as smooth_module
from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import EdgeGeometry, TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.sampling import sample_model
from blockdrawer.smooth import SmoothOptions, band_spokes, select_free, smooth_model


def two_by_two(centre=(0.66, 0.6)) -> MeshModel:
    """A unit square cut into four blocks whose shared centre vertex v6 is
    pushed off the middle, so the four blocks shear."""
    model = MeshModel()
    model.split_edge(edge_key("v0", "v1"), 0.5)
    model.split_edge(edge_key("v1", "v2"), 0.5)
    model.move_vertex("v6", *centre)
    return model


def positions(model: MeshModel) -> dict:
    return {vertex.id: (vertex.x, vertex.y) for vertex in model.vertices.values()}


class MoveVerticesTests(unittest.TestCase):
    def test_both_endpoints_carry_a_curved_edge_by_one_similarity(self) -> None:
        model = MeshModel()
        model.set_edge_type(edge_key("v0", "v1"), "arc")
        (point,) = model.edge_geometry[edge_key("v0", "v1")].points
        model.move_vertices({"v0": (0.1, 0.1), "v1": (1.1, 0.1)})
        (moved,) = model.edge_geometry[edge_key("v0", "v1")].points
        self.assertAlmostEqual(moved[0], point[0] + 0.1)
        self.assertAlmostEqual(moved[1], point[1] + 0.1)

    def test_an_invalid_result_restores_everything(self) -> None:
        model = MeshModel()
        before = positions(model)
        with self.assertRaises(TopologyError):
            model.move_vertices({"v2": (0.0, 0.0), "v1": (5.0, 5.0)})
        self.assertEqual(positions(model), before)


class SmoothTests(unittest.TestCase):
    def test_rectangles_have_nothing_to_gain(self) -> None:
        model = apply_commands(MeshModel(), ["add_block v1-v2", "split_edge v0-v1 0.5"]).model
        model.split_edge(edge_key("v1", "v2"), 0.5)
        before = positions(model)
        result = smooth_model(model)
        self.assertFalse(result.applied)
        self.assertIn("nothing to gain", result.reason)
        self.assertEqual(positions(model), before)
        self.assertEqual(result.after, result.before)

    def test_a_sheared_centre_is_smoothed_deterministically(self) -> None:
        model = two_by_two()
        before = sample_model(model).summary()["max_non_orthogonality"]
        result = smooth_model(model)
        self.assertTrue(result.applied, result.reason)
        self.assertEqual(result.moved, 1)
        self.assertLess(result.after["max_non_orthogonality"], before - 5.0)
        self.assertEqual(result.after["folded_cells"], 0)
        self.assertGreater(result.max_displacement, 0.0)
        again = two_by_two()
        smooth_model(again)
        self.assertEqual(positions(again), positions(model))
        data = result.to_data()
        self.assertEqual(data["moved"], 1)
        self.assertIn("smoothed 1 of 1 free vertices", data["reason"])

    def test_scope_and_hold(self) -> None:
        self.assertEqual(select_free(two_by_two()), ["v6"])
        held = two_by_two()
        result = smooth_model(held, hold=["v6"])
        self.assertFalse(result.applied)
        self.assertIn("no free vertex", result.reason)
        outside = smooth_model(two_by_two(), bbox=[0.0, 0.0, 0.3, 0.3])
        self.assertFalse(outside.applied)
        self.assertEqual(outside.free_vertices, 0)
        inside = two_by_two()
        self.assertTrue(smooth_model(inside, bbox=[0.6, 0.6, 0.9, 0.9]).applied)
        self.assertTrue(smooth_model(two_by_two(), blocks=["b0"]).applied)
        with self.assertRaises(TopologyError):
            smooth_model(two_by_two(), blocks=["b99"])

    def test_a_wall_spoke_keeps_its_length(self) -> None:
        model = apply_commands(two_by_two(), [
            "add_boundary wall", "set_boundary_type wall wall",
            "set_edge_boundary v0-v4 wall", "set_edge_boundary v1-v4 wall",
        ]).model
        spokes = band_spokes(model, ["v6"])
        self.assertEqual([spoke.wall for spoke in spokes["v6"]], ["v4"])
        length = model.edge_length(edge_key("v4", "v6"))
        result = smooth_model(model)
        self.assertTrue(result.applied, result.reason)
        self.assertEqual(result.band_spokes, 1)
        self.assertLessEqual(result.max_band_change, 0.05 + 1e-9)
        change = abs(model.edge_length(edge_key("v4", "v6")) / length - 1.0)
        self.assertLessEqual(change, 0.05 + 1e-9)
        # Without the band the centre goes further from that length.
        free = two_by_two()
        smooth_model(free)
        self.assertGreater(abs(free.edge_length(edge_key("v4", "v6")) / length - 1.0), change)
        # A zero tolerance holds the spoke exactly.
        exact = apply_commands(two_by_two(), [
            "add_boundary wall", "set_boundary_type wall wall",
            "set_edge_boundary v0-v4 wall", "set_edge_boundary v1-v4 wall",
        ]).model
        smooth_model(exact, options=SmoothOptions(band_tolerance=0.0))
        self.assertAlmostEqual(exact.edge_length(edge_key("v4", "v6")), length, places=9)

    def test_lengths_stay_within_their_tolerance(self) -> None:
        model = two_by_two((0.7, 0.65))
        lengths = {current: model.edge_length(current) for current in model.edges()}
        result = smooth_model(model, options=SmoothOptions(length_tolerance=0.1))
        self.assertTrue(result.applied, result.reason)
        for current, length in lengths.items():
            ratio = model.edge_length(current) / length
            self.assertLessEqual(ratio, 1.1 + 1e-9)
            self.assertGreaterEqual(ratio * 1.1, 1.0 - 1e-9)

    def test_a_jump_below_the_ceiling_does_not_cross_it(self) -> None:
        def centre_jumps(model):
            values = []
            for _shared, at, first, second in model.interface_pairs():
                if at == "v6":
                    a = model.edge_width_at_vertex(first, at)
                    b = model.edge_width_at_vertex(second, at)
                    values.append(max(a / b, b / a))
            return values

        def start() -> MeshModel:
            model = stretched_two_by_two()
            model.move_vertex("v6", 0.4, 0.3)
            return model

        # The polish moves the centre so that its jumps grow (2.24 -> 2.47);
        # with every pair above size_cap and a wide slack, only the ceiling
        # holds them.
        loose = SmoothOptions(size_cap=1.0, size_slack=10.0, size_ceiling=0.0, length_tolerance=0.0)
        free = start()
        self.assertLess(max(centre_jumps(free)), 2.3)
        self.assertTrue(smooth_model(free, options=loose).applied)
        self.assertGreater(max(centre_jumps(free)), 2.3)
        held = start()
        result = smooth_model(held, options=replace(loose, size_ceiling=2.3))
        self.assertTrue(result.applied, result.reason)
        self.assertLessEqual(max(centre_jumps(held)), 2.3 + 1e-9)
        self.assertEqual(SmoothOptions().size_ceiling, smooth_module.SIZE_CEILING)

    def test_a_proposal_the_full_check_refuses_is_never_applied(self) -> None:
        model = two_by_two()
        before = positions(model)
        # An optimiser that proposes a fold: the full check must refuse it at
        # every scale and leave the model as it was.
        with mock.patch.object(
            smooth_module, "_optimise", return_value=({"v6": (0.99, 0.99)}, 1, 0, {})
        ):
            result = smooth_model(model)
        self.assertFalse(result.applied)
        self.assertEqual(positions(model), before)

    def test_a_folded_model_is_not_smoothed(self) -> None:
        model = two_by_two((0.5, 0.5))
        # An arc on the wall that bulges past the opposite side folds b0's cells.
        model.edge_geometry[edge_key("v0", "v4")] = EdgeGeometry("arc", ((0.25, 0.9),))
        result = smooth_model(model)
        self.assertFalse(result.applied)
        self.assertIn("fold", result.reason)
        # The refusal names the folding blocks with their counts, on the
        # real counts or on the count-free map.
        self.assertRegex(result.reason, r"at the stored counts .* in b0 \(\d+\)")
        shaped = smooth_model(model, options=SmoothOptions(mode="shape"))
        self.assertFalse(shaped.applied)
        self.assertRegex(shaped.reason, r"on the count-free block map .* in b0 \(\d+\)")
        self.assertIn("the shape step can be left out", shaped.reason)

    def test_a_count_free_fold_from_a_hooked_spline_names_the_edge(self) -> None:
        model = two_by_two((0.5, 0.5))
        # The wall's last segment (0.02) beside one of 0.48: OpenFOAM's
        # spline runs back past the end there, and the uniform map folds.
        model.edge_geometry[edge_key("v0", "v4")] = EdgeGeometry("spline", ((0.48, -0.005),))
        model.validate()
        shaped = smooth_model(model, options=SmoothOptions(mode="shape"))
        self.assertFalse(shaped.applied)
        self.assertRegex(shaped.reason, r"on the count-free block map .* in b0 \(\d+\)")
        self.assertIn("v0-v4 (segment 2 of 2", shaped.reason)
        self.assertIn("control point 0", shaped.reason)
        self.assertIn("remove_control_point", shaped.reason)
        self.assertNotIn("whatever its counts", shaped.reason)

    def test_the_polish_does_not_cross_the_aspect_ratio_limit(self) -> None:
        from blockdrawer import smooth as module

        self.assertIn("max_aspect_ratio", module._verdict(sample_model(two_by_two(), 64)))
        calls = []

        def crossed_at_full_scale(before, before_high, after, after_high):
            calls.append(after)
            return len(calls) == 1

        with mock.patch.object(module, "aspect_worse", side_effect=crossed_at_full_scale):
            model = two_by_two()
            result = smooth_model(model)
        self.assertTrue(result.applied)
        self.assertLess(result.scale, 1.0)
        self.assertIn("aspect ratio limit 1000 bound: at 1 of the optimised displacement", result.reason)
        with mock.patch.object(module, "aspect_worse", return_value=True):
            model = two_by_two()
            before = positions(model)
            refused = smooth_model(model)
        self.assertFalse(refused.applied)
        self.assertIn("not applied: checkMesh's aspect ratio limit 1000 bound", refused.reason)
        self.assertEqual(positions(model), before)
        # Shape mode judges stale counts: the limit is not applied there.
        with mock.patch.object(module, "aspect_worse", return_value=True):
            shaped = smooth_model(two_by_two((0.7, 0.65)), options=SmoothOptions(mode="shape"))
        self.assertTrue(shaped.applied, shaped.reason)

    def test_the_blocks_taken_past_the_limit_are_held_in_a_second_run(self) -> None:
        from blockdrawer import smooth as module

        calls = []

        def crossed(before, before_high, after, after_high):
            # The full displacement crosses, in all four blocks; nothing after.
            calls.append(after)
            return len(calls) <= 5

        with mock.patch.object(module, "aspect_worse", side_effect=crossed):
            result = smooth_model(two_by_two())
        self.assertEqual(sorted(result.aspect_blocks), ["b0", "b1", "b2", "b3"])
        self.assertEqual(sorted(result.to_data()["aspect_blocks"]), ["b0", "b1", "b2", "b3"])
        # Every free vertex held in the second run: nothing to gain there, so
        # the scaled-back result stands.
        self.assertTrue(result.applied)
        self.assertLess(result.scale, 1.0)

    def test_a_second_run_holds_the_vertices_of_the_blocks_past_the_limit(self) -> None:
        from blockdrawer import smooth as module

        first_run = 5 + len(module.FALLBACK_SCALES)
        calls = []

        def crossed(before, before_high, after, after_high):
            # The first run crosses at every scale (and in all four blocks);
            # the second runs with every free vertex held (nothing left to move).
            calls.append(after)
            return len(calls) <= first_run

        with mock.patch.object(module, "aspect_worse", side_effect=crossed), \
                mock.patch.object(module, "select_free", wraps=module.select_free) as free:
            model = two_by_two()
            result = smooth_model(model)
        self.assertEqual(free.call_args_list[1].kwargs["hold"], ("v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"))
        self.assertFalse(result.applied)
        self.assertIn("not applied", result.reason)

    def test_the_aspect_rule(self) -> None:
        from blockdrawer.sampling import aspect_worse

        self.assertFalse(aspect_worse(500.0, 0, 999.0, 0))
        self.assertTrue(aspect_worse(986.0, 0, 1356.0, 40))
        self.assertFalse(aspect_worse(1800.0, 40, 2400.0, 40))
        self.assertTrue(aspect_worse(1800.0, 40, 1700.0, 41))

    def test_the_command_is_one_undoable_action(self) -> None:
        model = two_by_two()
        history = ModelHistory(model)
        original = positions(model)
        batch = apply_commands(model, ["smooth"])
        smoothed = batch.model
        history.record(smoothed)
        self.assertNotEqual(positions(smoothed), original)
        restored = history.undo()
        self.assertIsNotNone(restored)
        self.assertEqual(positions(restored), original)
        redone = history.redo()
        self.assertEqual(positions(redone), positions(smoothed))
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["op"], "smooth")
        self.assertTrue(note["applied"])
        held = apply_commands(two_by_two(), ["smooth hold=v6"]).results[0].result
        self.assertFalse(held["applied"])
        boxed = apply_commands(two_by_two(), ["smooth bbox=0.6,0.6,0.9,0.9 blocks=b0,b1"])
        self.assertTrue(boxed.results[0].result["applied"])


if __name__ == "__main__":
    unittest.main()


class ShapeModeTests(unittest.TestCase):
    def test_shape_mode_reports_the_stale_mesh_and_ignores_size_limits(self) -> None:
        model = two_by_two((0.7, 0.65))
        lengths = {current: model.edge_length(current) for current in model.edges()}
        result = smooth_model(
            model, options=SmoothOptions(mode="shape", length_tolerance=0.01)
        )
        self.assertTrue(result.applied, result.reason)
        self.assertEqual(result.mode, "shape")
        self.assertIn("run size next", result.reason)
        data = result.to_data()
        self.assertIn("real_before", data)
        self.assertLess(data["real_after"]["max_non_orthogonality"],
                        data["real_before"]["max_non_orthogonality"])
        # No length limit in shape mode: an edge changed by more than 1 percent.
        self.assertTrue(any(
            abs(model.edge_length(current) / length - 1.0) > 0.01
            for current, length in lengths.items()
        ))
        self.assertNotIn("real_before", smooth_model(two_by_two()).to_data())

    def test_a_shape_that_folds_the_stale_mesh_is_refused_unless_allowed(self) -> None:
        real = smooth_module.sample_model
        calls = []

        class Folded:
            def __init__(self, sample):
                from dataclasses import replace

                self.sample = sample
                self.blocks = (replace(sample.blocks[0], folded_cells=3), *sample.blocks[1:])

            def summary(self):
                return {**self.sample.summary(), "folded_cells": 3}

        def folding(model, max_cells=None, *, uniform=None):
            sample = real(model, max_cells, uniform=uniform)
            if uniform is None:
                calls.append(1)
                if len(calls) > 1:  # every real check after the first
                    return Folded(sample)
            return sample

        with mock.patch.object(smooth_module, "sample_model", folding):
            model = two_by_two()
            before = positions(model)
            refused = smooth_model(model, options=SmoothOptions(mode="shape"))
            self.assertFalse(refused.applied)
            self.assertIn("allow_stale_fold", refused.reason)
            self.assertIn("in b0 (3)", refused.reason)
            self.assertEqual(positions(model), before)
            calls.clear()
            allowed = smooth_model(
                model, options=SmoothOptions(mode="shape", allow_stale_fold=True)
            )
            self.assertTrue(allowed.applied)
            self.assertEqual(allowed.real_after["folded_cells"], 3)

    def test_the_command_takes_the_mode(self) -> None:
        result = apply_commands(two_by_two(), ["smooth mode=shape"]).results[0].result
        self.assertEqual(result["mode"], "shape")
        self.assertTrue(result["applied"])


def vertex_at(model: MeshModel, x: float, y: float) -> str:
    (found,) = [
        vertex.id for vertex in model.vertices.values()
        if abs(vertex.x - x) < 1e-9 and abs(vertex.y - y) < 1e-9
    ]
    return found


class ShapeLengthFloorTests(unittest.TestCase):
    def test_no_edge_ends_shorter_than_the_floor(self) -> None:
        base = two_by_two((0.7, 0.65))
        lengths = {current: base.edge_length(current) for current in base.edges()}
        # Unbounded, the count-free optimum shortens an edge past 0.9 of its length.
        free = two_by_two((0.7, 0.65))
        unbounded = smooth_model(free, options=SmoothOptions(mode="shape", shape_length_ratio=0.0))
        self.assertTrue(unbounded.applied, unbounded.reason)
        self.assertLess(unbounded.min_edge_ratio, 0.9)
        shortest = min(free.edge_length(current) / length for current, length in lengths.items())
        self.assertAlmostEqual(unbounded.min_edge_ratio, shortest)
        # With the floor at 0.9 every edge keeps 0.9 of its length, and the
        # result and its reason say how close it came.
        limited = two_by_two((0.7, 0.65))
        result = smooth_model(limited, options=SmoothOptions(mode="shape", shape_length_ratio=0.9))
        self.assertTrue(result.applied, result.reason)
        for current, length in lengths.items():
            self.assertGreaterEqual(limited.edge_length(current) / length, 0.9 - 1e-9)
        self.assertGreaterEqual(result.min_edge_ratio, 0.9 - 1e-9)
        self.assertGreater(result.max_edge_ratio, 1.0)
        data = result.to_data()
        self.assertEqual(data["min_edge_ratio"], result.min_edge_ratio)
        self.assertIn(data["min_edge"], {f"{a}-{b}" for a, b in lengths})
        self.assertIn(f"edge lengths {result.min_edge_ratio:.3g} ({result.min_edge})", result.reason)
        self.assertIn("(floor 0.9)", result.reason)

    def test_the_command_applies_the_default_floor_and_the_digest_shows_it(self) -> None:
        model = two_by_two((0.7, 0.65))
        batch = apply_commands(model, ["smooth mode=shape"])
        result = batch.results[0].result
        self.assertTrue(result["applied"])
        self.assertGreaterEqual(result["min_edge_ratio"], smooth_module.SHAPE_LENGTH_RATIO - 1e-9)
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["min_edge_ratio"], result["min_edge_ratio"])
        self.assertIn("edge lengths", note["reason"])
        with self.assertRaises(Exception):
            apply_commands(two_by_two(), ["smooth mode=shape shape_length_ratio=1.5"])


def pair_changes(before: MeshModel, after: MeshModel) -> list[float]:
    """Per interface pair, the factor its length ratio moved by."""
    changes = []
    for _shared, _at, first, second in before.interface_pairs():
        old = before.edge_length(first) / before.edge_length(second)
        new = after.edge_length(first) / after.edge_length(second)
        changes.append(max(new / old, old / new))
    return changes


class InterfacePairTests(unittest.TestCase):
    def test_pairs_are_the_transverse_edges_across_each_internal_edge(self) -> None:
        model = two_by_two()
        pairs = model.interface_pairs()
        # Four internal edges, a pair at each end.
        self.assertEqual(len(pairs), 8)
        occurrences = model.edge_occurrences()
        for shared, at, first, second in pairs:
            self.assertEqual(len(occurrences[shared]), 2)
            self.assertIn(at, shared)
            self.assertIn(at, first)
            self.assertIn(at, second)
            self.assertNotEqual(first, second)
            self.assertNotIn(shared, (first, second))
        # The one definition size relaxes over.
        from blockdrawer.size import _links

        links = _links(model)
        self.assertEqual(sum(len(items) for items in links.values()), 2 * len(pairs))
        for _shared, at, first, second in pairs:
            self.assertIn((second, at), links[(first, at)])


class ShapePairRatioTests(unittest.TestCase):
    def test_no_interface_pair_leaves_the_limit(self) -> None:
        base = two_by_two((0.7, 0.65))
        free = two_by_two((0.7, 0.65))
        unbounded = smooth_model(free, options=SmoothOptions(mode="shape"))
        self.assertTrue(unbounded.applied, unbounded.reason)
        self.assertGreater(max(pair_changes(base, free)), 1.2)
        self.assertAlmostEqual(unbounded.max_pair_change, max(pair_changes(base, free)))
        self.assertIn("interface pair length ratios within", unbounded.reason)
        limited = two_by_two((0.7, 0.65))
        result = smooth_model(limited, options=SmoothOptions(mode="shape", shape_pair_ratio=1.2))
        self.assertTrue(result.applied, result.reason)
        self.assertLessEqual(max(pair_changes(base, limited)), 1.2 * (1.0 + 1e-6))
        self.assertLessEqual(result.max_pair_change, 1.2 * (1.0 + 1e-6))
        self.assertGreater(result.max_pair_change, 1.0)
        self.assertIn("/", result.max_pair)
        self.assertIn("(limit 1.2)", result.reason)
        # It still smooths: the count-free maximum falls.
        self.assertLess(result.after["max_non_orthogonality"], result.before["max_non_orthogonality"])

    def test_the_command_takes_the_limit_and_the_digest_shows_the_change(self) -> None:
        model = two_by_two((0.7, 0.65))
        batch = apply_commands(model, ["smooth mode=shape shape_pair_ratio=1.2"])
        result = batch.results[0].result
        self.assertTrue(result["applied"])
        self.assertLessEqual(result["max_pair_change"], 1.2 * (1.0 + 1e-6))
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["max_pair_change"], result["max_pair_change"])
        self.assertEqual(smooth_module.SHAPE_PAIR_RATIO, SmoothOptions().shape_pair_ratio)
        with self.assertRaises(Exception):
            apply_commands(two_by_two(), ["smooth mode=shape shape_pair_ratio=0.5"])


def stretched_two_by_two() -> MeshModel:
    """Two by two with the centre at the middle and the top right corner
    pulled out: the shape step moves the centre towards that corner and
    grows the spread of the count components through it."""
    model = two_by_two((0.5, 0.5))
    model.move_vertex("v2", 1.6, 1.0)
    return model


def spread_growth(before: MeshModel, after: MeshModel) -> float:
    from blockdrawer.size import count_components

    growth = 1.0
    for component in count_components(before):
        old = [before.edge_length(current) for current in component]
        new = [after.edge_length(current) for current in component]
        growth = max(growth, (max(new) / min(new)) / (max(old) / min(old)))
    return growth


class ShapeSpreadRatioTests(unittest.TestCase):
    def test_no_count_component_spread_grows_past_the_limit(self) -> None:
        base = stretched_two_by_two()
        free = stretched_two_by_two()
        unbounded = smooth_model(free, options=SmoothOptions(mode="shape"))
        self.assertTrue(unbounded.applied, unbounded.reason)
        self.assertGreater(spread_growth(base, free), 1.5)
        self.assertAlmostEqual(unbounded.max_spread_growth, spread_growth(base, free))
        self.assertIn("count component spreads grown at most", unbounded.reason)
        limited = stretched_two_by_two()
        result = smooth_model(limited, options=SmoothOptions(mode="shape", shape_spread_ratio=1.3))
        self.assertTrue(result.applied, result.reason)
        self.assertLessEqual(spread_growth(base, limited), 1.3 * (1.0 + 1e-6))
        self.assertLessEqual(result.max_spread_growth, 1.3 * (1.0 + 1e-6))
        self.assertIn("(limit 1.3)", result.reason)
        self.assertLess(result.after["max_non_orthogonality"], result.before["max_non_orthogonality"])

    def test_a_falling_spread_is_not_limited(self) -> None:
        # The off-centre two by two evens its components out: every spread
        # falls, so even a tight limit leaves the result as it was.
        free = two_by_two((0.7, 0.65))
        unbounded = smooth_model(free, options=SmoothOptions(mode="shape"))
        limited = two_by_two((0.7, 0.65))
        result = smooth_model(limited, options=SmoothOptions(mode="shape", shape_spread_ratio=1.01))
        self.assertEqual(positions(free), positions(limited))
        self.assertEqual(result.max_spread_growth, 1.0)
        self.assertTrue(unbounded.applied)

    def test_the_command_takes_the_limit(self) -> None:
        model = stretched_two_by_two()
        batch = apply_commands(model, ["smooth mode=shape shape_spread_ratio=1.3"])
        result = batch.results[0].result
        self.assertLessEqual(result["max_spread_growth"], 1.3 * (1.0 + 1e-6))
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["max_spread_growth"], result["max_spread_growth"])
        self.assertEqual(smooth_module.SHAPE_SPREAD_RATIO, SmoothOptions().shape_spread_ratio)
        with self.assertRaises(Exception):
            apply_commands(stretched_two_by_two(), ["smooth mode=shape shape_spread_ratio=0.9"])


def kinked_cross() -> MeshModel:
    """Four blocks whose centre lies on a straight horizontal block line
    (left side - centre - right side) while the vertical one is kinked by
    the top side's vertex moved to the right."""
    model = two_by_two((0.5, 0.5))
    model.move_vertex(vertex_at(model, 0.5, 1.0), 0.85, 1.0)
    return model


class StraightLineTests(unittest.TestCase):
    def test_lines_are_found_through_opposite_straight_edges(self) -> None:
        model = kinked_cross()
        centre = vertex_at(model, 0.5, 0.5)
        lines = smooth_module.straight_lines(model, [centre])
        self.assertAlmostEqual(abs(lines[centre].imag), 0.0)
        # On two lines a vertex is fixed; a curved edge is no line.
        self.assertIsNone(smooth_module.straight_lines(two_by_two((0.5, 0.5)), [centre])[centre])
        curved = kinked_cross()
        curved.set_edge_type(edge_key(centre, vertex_at(curved, 1.0, 0.5)), "arc")
        self.assertNotIn(centre, smooth_module.straight_lines(curved, [centre]))

    def test_a_vertex_on_a_straight_line_moves_only_along_it(self) -> None:
        centre = vertex_at(kinked_cross(), 0.5, 0.5)
        for mode in ("shape", "real"):
            with self.subTest(mode=mode):
                free = kinked_cross()
                smooth_model(free, options=SmoothOptions(mode=mode))
                # Free, the optimum leaves the line.
                self.assertGreater(abs(free.vertices[centre].y - 0.5), 1e-3)
                kept = kinked_cross()
                result = smooth_model(kept, options=SmoothOptions(mode=mode, keep_lines=True))
                self.assertTrue(result.applied, result.reason)
                self.assertEqual((result.line_vertices, result.line_fixed), (1, 0))
                self.assertAlmostEqual(kept.vertices[centre].y, 0.5, places=12)
                self.assertGreater(abs(kept.vertices[centre].x - 0.5), 1e-3)
                self.assertIn("1 vertices on a straight block line", result.reason)

    def test_a_vertex_on_two_lines_stays(self) -> None:
        model = two_by_two((0.5, 0.5))
        result = apply_commands(model, ["smooth mode=shape keep_lines=true"]).results[0].result
        self.assertFalse(result["applied"])
        self.assertEqual(result["line_fixed"], 1)
        self.assertIn("on two straight block lines", result["reason"])
