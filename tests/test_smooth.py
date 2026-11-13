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
            smooth_module, "_optimise", return_value=({"v6": (0.99, 0.99)}, 1, 0)
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
