import math
import unittest

from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import edge_key
from blockdrawer.quality import wall_first_widths
from blockdrawer.session import to_data
from blockdrawer.size import SizeOptions, size_model

from tests.test_size import band, column


class SetFirstWidthTests(unittest.TestCase):
    def test_every_spoke_of_a_band_gets_the_height_asked(self) -> None:
        model = band()
        size_model(model, SizeOptions(0.001, 0.1, growth=1.2, grading="strip"))
        (before,) = wall_first_widths(model)
        low, high = before["first_width"]
        # One shared ratio: the first width in proportion to the spoke's length.
        self.assertGreater(high / low, 1.3)
        counts = dict(model.edge_cells)
        batch = apply_commands(model, ["set_first_width 0.001"])
        model = batch.model
        result = batch.results[0].result
        self.assertEqual(result["spokes"], 3)
        self.assertEqual(result["opposite_edges"], 0)
        self.assertEqual(dict(model.edge_cells), counts)
        for spoke in (edge_key("w0", "t0"), edge_key("w1", "t1"), edge_key("w2", "t2")):
            self.assertAlmostEqual(model.edge_width_at_vertex(spoke, spoke[1]), 0.001, delta=1.0e-9)
        low, high = result["first_width_after"]["wall"]
        self.assertAlmostEqual(low, 0.001, delta=1.0e-9)
        self.assertAlmostEqual(high, 0.001, delta=1.0e-9)
        self.assertEqual(result["first_width_before"]["wall"], before["first_width"])
        self.assertIn("worst", result["jumps_before"])
        (note,) = batch_digest(batch.model, batch)["notes"]
        self.assertIn("-> wall 0.001..0.001 (+0%..+0%)", note["first_width"])
        for value in note.values():
            self.assertTrue(isinstance(value, (int, float, str, bool)) or (
                isinstance(value, list) and all(not isinstance(item, (list, dict)) for item in value)), value)

    def test_an_oblique_spoke_is_graded_by_its_height_not_its_width(self) -> None:
        from tests.test_size import _blocks

        # The right spoke leans 30 degrees from the wall's normal.
        lean = math.tan(math.radians(30.0))
        model = _blocks({"w0": (0, 0), "w1": (1, 0), "t0": (0, 0.5), "t1": (1 + 0.5 * lean, 0.5)},
                        [("w0", "w1", "t1", "t0")], ["w0-w1"])
        result = apply_commands(model, ["set_first_width 0.002"]).results[0].result
        low, high = result["first_width_after"]["wall"]
        self.assertAlmostEqual(low, 0.002, delta=1.0e-9)
        self.assertAlmostEqual(high, 0.002, delta=1.0e-9)

    def test_the_side_opposite_a_spoke_is_kept_or_follows(self) -> None:
        # column(): the wall is the left end of the bottom row; a1-b1 is a
        # spoke whose opposite side in the second row, a2-b2, is not.
        base = column()
        size_model(base, SizeOptions(0.001, 1.0, growth=1.2, split=False, grading="strip"))
        spoke, other = edge_key("a1", "b1"), edge_key("a2", "b2")
        before = base.edge_grading.get(other, 1.0)
        kept = apply_commands(base, ["set_first_width 0.0005"])
        data = kept.results[0].result
        self.assertEqual(data["opposite"], "keep")
        self.assertEqual(data["opposite_edges"], 1)
        self.assertEqual(kept.model.edge_grading.get(other, 1.0), before)
        self.assertGreater(data["max_tilt"], 0.0)
        self.assertTrue(data["worst_tilt"][0].startswith("b1 a2-b2"))
        followed = apply_commands(base, ["set_first_width 0.0005 opposite=follow"])
        model = followed.model
        # The spoke's series runs on along its strip: a2-b2, a3-b3, a4-b4.
        for a, b in (("a2", "b2"), ("a3", "b3"), ("a4", "b4")):
            self.assertAlmostEqual(model.edge_total_expansion(edge_key(a, b)),
                                   model.edge_total_expansion(spoke), places=12)
        self.assertEqual(followed.results[0].result["followed_edges"], 3)
        self.assertAlmostEqual(followed.results[0].result["max_tilt"], 0.0, places=9)

    def test_refusals_and_one_history_entry(self) -> None:
        model = band()
        with self.assertRaises(Exception):
            apply_commands(model, ["set_first_width 0.001 patch=nothing"])
        with self.assertRaises(Exception):
            apply_commands(model, ["set_first_width 0"])
        with self.assertRaises(Exception):
            apply_commands(apply_commands(model, ["add_boundary inlet"]).model,
                           ["set_first_width 0.001 patch=inlet"])
        history = ModelHistory(model)
        before = to_data(model)
        batch = apply_commands(model, ["set_first_width 0.001"])
        history.record(batch.model)
        self.assertNotEqual(to_data(batch.model), before)
        self.assertEqual(to_data(history.undo()), before)


if __name__ == "__main__":
    unittest.main()
