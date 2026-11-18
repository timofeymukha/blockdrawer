import math
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
                self.sample = sample

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
