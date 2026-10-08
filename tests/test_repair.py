import unittest

from blockdrawer.commands import CommandError, apply_commands, command_specs
from blockdrawer.model import MeshModel, TopologyError, edge_key
from blockdrawer.quality import block_quality
from blockdrawer.repair import repair_block


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


if __name__ == "__main__":
    unittest.main()
