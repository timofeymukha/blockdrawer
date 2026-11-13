import math
import unittest

from blockdrawer.domain import EdgeGeometry, edge_key
from blockdrawer.model import MeshModel
from blockdrawer.sampling import (
    SEVERE_NON_ORTHOGONALITY,
    block_grid,
    cell_angles,
    sample_model,
)


def sheared_pair() -> MeshModel:
    """A unit square b0 and, right of it, a parallelogram b1 whose grid lines
    rise at 45 degrees: b1's own faces are 45 degrees non-orthogonal and the
    faces on the shared edge atan(1/2), because b1's cell centres sit half a
    cell higher than b0's."""
    model = MeshModel()
    model.add_vertex(2.0, 1.0, vertex_id="v4")
    model.add_vertex(2.0, 2.0, vertex_id="v5")
    model.add_block_from_vertices(("v1", "v4", "v5", "v2"))
    model.validate()
    return model


class GridTests(unittest.TestCase):
    def test_real_counts_unless_capped(self) -> None:
        model = MeshModel()
        block = model.blocks[0]
        grid = block_grid(model, block)
        self.assertEqual(grid.sampled_cells, (10, 10))
        self.assertFalse(grid.coarsened)
        capped = block_grid(model, block, max_cells=4)
        self.assertTrue(capped.coarsened)
        # Every n-th node from both ends: fewer than the real ten, at most twice the cap.
        self.assertLess(max(capped.sampled_cells), 10)
        self.assertLessEqual(max(capped.sampled_cells), 8)
        # Corners are kept exactly.
        self.assertEqual(capped.nodes[0][0], (0.0, 0.0))
        self.assertEqual(capped.nodes[-1][-1], (1.0, 1.0))
        self.assertEqual(block_grid(model, block, max_cells=None).sampled_cells, (10, 10))

    def test_cell_angles_of_a_square_and_a_fold(self) -> None:
        model = MeshModel()
        square = cell_angles(block_grid(model, model.blocks[0]).nodes)
        self.assertEqual((square.cells, square.folded, square.severe), (100, 0, 0))
        self.assertAlmostEqual(square.min_angle, 90.0)
        self.assertAlmostEqual(square.worst, 90.0)
        # An arc bulging past the opposite edge folds cells between corners
        # the first-cell corner screen reads as sound.
        model.edge_geometry[edge_key("v0", "v1")] = EdgeGeometry("arc", ((0.5, 1.4),))
        model.validate()
        folded = cell_angles(block_grid(model, model.blocks[0]).nodes)
        self.assertGreater(folded.folded, 0)
        self.assertEqual(folded.worst, -math.inf)
        self.assertIsNotNone(folded.folded_at)
        first = cell_angles(block_grid(model, model.blocks[0]).nodes, stop_at_fold=True)
        self.assertEqual(first.folded, 1)


class ModelSampleTests(unittest.TestCase):
    def test_square_is_orthogonal(self) -> None:
        sample = sample_model(MeshModel())
        (block,) = sample.blocks
        self.assertAlmostEqual(block.max_non_orthogonality, 0.0, places=9)
        self.assertEqual((block.folded_cells, block.severe_faces), (0, 0))
        summary = sample.summary()
        self.assertEqual(summary["sampled_cells"], 100)
        self.assertEqual(summary["coarsened_blocks"], 0)
        self.assertEqual(summary["severe_threshold"], SEVERE_NON_ORTHOGONALITY)

    def test_face_measure_inside_blocks_and_across_a_shared_edge(self) -> None:
        sample = sample_model(sheared_pair())
        by_id = {block.block_id: block for block in sample.blocks}
        self.assertAlmostEqual(by_id["b1"].max_non_orthogonality, 45.0, places=9)
        # The shared-edge faces count for b0, the first block (blockMesh's owner).
        self.assertAlmostEqual(
            by_id["b0"].max_non_orthogonality, math.degrees(math.atan(0.5)), places=9
        )
        self.assertAlmostEqual(by_id["b0"].worst_face[0], 1.0)
        self.assertEqual(sample.summary()["worst_block"], "b1")

    def test_severe_faces_are_counted_once(self) -> None:
        model = MeshModel()
        model.move_vertex("v2", 4.0, 1.0)  # a strongly sheared parallelogram
        model.move_vertex("v3", 3.0, 1.0)
        sample = sample_model(model)
        (block,) = sample.blocks
        # Grid lines meet at atan(1/3) = 18.4 degrees: every face of the
        # family crossing the shear is 71.6 degrees off.
        self.assertAlmostEqual(
            block.max_non_orthogonality, 90.0 - math.degrees(math.atan(1.0 / 3.0)), places=6
        )
        self.assertGreater(block.severe_faces, 0)
        self.assertEqual(block.severe_faces, sample.summary()["severe_faces"])
        self.assertEqual(block.severe_cells, 100)


if __name__ == "__main__":
    unittest.main()
