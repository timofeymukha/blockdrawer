import unittest

from blockdrawer.commands import CommandError, apply_commands, command_specs
from blockdrawer.model import MeshModel, TopologyError, edge_key
from blockdrawer.quality import block_quality
from blockdrawer.repair import RepairScore, repair_block


def two_by_two(centre=(1.0, 1.0)) -> MeshModel:
    """A 2x2 block grid on [0, 2]^2 whose centre vertex is at ``centre``."""
    model = MeshModel()
    # The default model is one unit block b0 = v0..v3; stretch it to [0, 2]^2
    # and split it twice to get four blocks round a centre vertex.
    model.move_vertex("v1", 2.0, 0.0)
    model.move_vertex("v2", 2.0, 2.0)
    model.move_vertex("v3", 0.0, 2.0)
    model.split_edge(edge_key("v0", "v1"), 0.5)
    model.split_edge(edge_key("v0", "v3"), 0.5)
    middle = next(
        vertex.id for vertex in model.vertices.values()
        if abs(vertex.x - 1.0) < 1e-9 and abs(vertex.y - 1.0) < 1e-9
    )
    model.move_vertex(middle, *centre)
    model.validate()
    return model, middle


def worst_of(model: MeshModel, block_id: str) -> float:
    quality = block_quality(model, block_id)
    return min(quality.min_angle, 180.0 - quality.max_angle)


def worst_block(model: MeshModel) -> str:
    return min(model.blocks, key=lambda block: worst_of(model, block.id)).id


class RepairBlockTests(unittest.TestCase):
    def test_a_sliver_from_a_displaced_centre_vertex_is_opened_by_moving_it(self) -> None:
        # The centre vertex almost on the upper-right block's diagonal: that
        # block has a 177-degree corner there, the sliver signature.
        model, middle = two_by_two(centre=(1.55, 1.42))
        block = worst_block(model)
        before = worst_of(model, block)
        self.assertLess(before, 10.0)

        result = repair_block(model, block)

        self.assertTrue(result.applied)
        # Merging the sliver into its neighbour or moving the centre vertex
        # both open it; the score decides, and here the merge wins.
        self.assertIn(result.operation, ("move_vertex", "combine_blocks"))
        self.assertGreater(result.after.worst, before + 10.0)
        self.assertAlmostEqual(
            min(worst_of(model, item.id) for item in model.blocks), result.after.worst
        )
        model.validate()

    def test_boundary_vertices_never_move_and_a_good_block_is_left_alone(self) -> None:
        model, _middle = two_by_two()
        block = model.blocks[0].id
        before = {vertex.id: (vertex.x, vertex.y) for vertex in model.vertices.values()}

        result = repair_block(model, block, moves=("move",))

        self.assertFalse(result.applied)
        self.assertIn("no local move", result.reason)
        self.assertEqual({vertex.id: (vertex.x, vertex.y) for vertex in model.vertices.values()}, before)

    def test_the_repair_is_deterministic(self) -> None:
        first, _ = two_by_two(centre=(1.55, 1.42))
        second, _ = two_by_two(centre=(1.55, 1.42))
        block = worst_block(first)
        one = repair_block(first, block)
        two = repair_block(second, block)
        self.assertEqual(one.operation, two.operation)
        self.assertEqual(one.arguments, two.arguments)
        self.assertEqual(one.after, two.after)

    def test_unknown_block_and_unknown_move_are_errors(self) -> None:
        model, _ = two_by_two()
        with self.assertRaises(TopologyError):
            repair_block(model, "b99")
        with self.assertRaises(TopologyError):
            repair_block(model, "b0", moves=("teleport",))

    def test_the_command_is_registered_and_batches(self) -> None:
        self.assertIn("repair_block", {spec.name for spec in command_specs()})
        model, _middle = two_by_two(centre=(1.55, 1.42))
        block = worst_block(model)
        batch = apply_commands(model, [f"repair_block {block}"])
        self.assertTrue(batch.results[0].result["operation"])
        # A second repair on the result's worst block is a no-op or an
        # improvement, never an error.
        again = apply_commands(batch.model, [f"repair_block {worst_block(batch.model)} moves=move"])
        self.assertIn("reason", again.results[0].result)
        self.assertGreater(
            min(worst_of(batch.model, item.id) for item in batch.model.blocks),
            min(worst_of(model, item.id) for item in model.blocks),
        )
        with self.assertRaisesRegex(CommandError, "repair_block"):
            apply_commands(model, ["repair_block b99"])

    def test_crossed_opposite_gradings_are_uncrossed_by_the_grade_move(self) -> None:
        """A seam-wedge kite: a 13-degree apex, its long spoke graded fine at
        the apex while the opposite short spoke is fine at the wall. Every
        cell between them is sheared whatever the corners say; its vertices
        are all on boundary edges, so no move applies, and the grade move
        gives one spoke the other's series."""
        model = MeshModel()
        model.move_vertex("v2", 0.6, 6.0)
        model.move_vertex("v3", -0.2, 1.0)
        block = model.blocks[0]
        long_spoke = edge_key(*block.directed_edge(1))   # v1 -> v2, towards the apex
        short_spoke = edge_key(*block.directed_edge(3))  # v3 -> v0, towards the wall
        model.set_edge_cells(long_spoke, 40)
        # Fine at the apex v2 on the long spoke...
        model._set_edge_expansion_in_direction("v1", "v2", 1.0 / 30.0)
        # ... fine at the wall v0 on the short one: crossed.
        model._set_edge_expansion_in_direction("v0", "v3", 30.0)
        model.validate()
        untouched = repair_block(model, block.id, moves=("move",))
        self.assertFalse(untouched.applied, untouched.reason)
        self.assertGreater(untouched.before.severe, 0)

        result = repair_block(model, block.id, moves=("grade",))

        self.assertTrue(result.applied, result.reason)
        self.assertEqual(result.operation, "set_edge_grading")
        self.assertLess(result.after.severe, result.before.severe)
        self.assertAlmostEqual(
            model.edge_expansion_in_direction("v1", "v2"),
            model.edge_expansion_in_direction("v0", "v3"),
        )
        model.validate()

    def test_a_score_improves_by_the_worst_cell_or_by_the_severe_count(self) -> None:
        before = RepairScore(12.0, 150.0, 12.0, severe=100)
        self.assertTrue(RepairScore(12.6, 150.0, 12.6, severe=100).improves(before))
        self.assertFalse(RepairScore(12.3, 150.0, 12.3, severe=100).improves(before))
        self.assertTrue(RepairScore(12.0, 150.0, 12.0, severe=90).improves(before))
        self.assertFalse(RepairScore(12.0, 150.0, 12.0, severe=91).improves(before))
        self.assertFalse(RepairScore(11.5, 150.0, 11.5, severe=10).improves(before))

    def test_a_vertex_move_carries_the_curved_edges_with_it(self) -> None:
        """An arc on an interior edge of the sliver: moving its vertex would
        bend the arc flat and be refused, so the move carries the arc."""
        model, middle = two_by_two(centre=(1.55, 1.42))
        block = worst_block(model)
        target = next(item for item in model.blocks if item.id == block)
        interior = next(
            edge_key(*target.directed_edge(index))
            for index in range(4)
            if middle in target.directed_edge(index)
        )
        model.set_edge_type(interior, "arc")
        model.validate()
        before = model.arc_point(interior)

        result = repair_block(model, block, moves=("move",))

        self.assertTrue(result.applied, result.reason)
        self.assertEqual(result.operation, "move_vertex")
        self.assertTrue(result.arguments["carry_curves"])
        self.assertEqual(model.edge_type(interior), "arc")
        self.assertNotEqual(model.arc_point(interior), before)
        model.validate()


if __name__ == "__main__":
    unittest.main()
