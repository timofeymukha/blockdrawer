import math
import unittest

from blockdrawer.domain import EdgeGeometry, edge_key
from blockdrawer.model import MeshModel
from tests.helpers import build_sheared_periodic_strip
from blockdrawer.sampling import (
    ASPECT_LIMIT,
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
    def test_cyclic_faces_are_measured_as_coupled_faces(self) -> None:
        # checkMesh measures a cyclic face against the partner cell's centre
        # carried across the pair (OpenFOAM v2606 on this strip: 53 faces
        # above 70 degrees, max 71.5651, skewness 0.45); as plain boundary
        # faces they read skewness 2.0 and are not counted.
        coupled = sample_model(build_sheared_periodic_strip()).summary()
        self.assertEqual(coupled["severe_faces"], 53)
        self.assertAlmostEqual(coupled["max_skewness"], 0.45, places=9)
        self.assertAlmostEqual(
            coupled["max_non_orthogonality"], math.degrees(math.atan(3.0)), places=9
        )
        plain = sample_model(build_sheared_periodic_strip(cyclic=False)).summary()
        self.assertEqual(plain["severe_faces"], 37)
        self.assertAlmostEqual(plain["max_skewness"], 2.0, places=9)

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
        # checkMesh's average is the angle of the mean cosine; every face of
        # an affine parallelogram grid is equally non-orthogonal.
        self.assertAlmostEqual(
            sample.summary()["average_non_orthogonality"], block.max_non_orthogonality, places=6
        )
        self.assertAlmostEqual(sample_model(MeshModel()).summary()["average_non_orthogonality"], 0.0)


if __name__ == "__main__":
    unittest.main()


class AspectRatioTests(unittest.TestCase):
    """checkMesh's cell aspect ratio (primitiveMeshTools::cellClosedness)."""

    def _strip(self, x_cells: int, y_cells: int, height: float = 1.0) -> MeshModel:
        model = MeshModel(box=(0.0, 0.0, 1.0, height))
        model.set_edge_cells(edge_key("v0", "v1"), x_cells)
        model.set_edge_cells(edge_key("v1", "v2"), y_cells)
        model.z_min_patch_type = model.z_max_patch_type = "empty"
        return model

    def test_a_2d_cell_reads_its_side_ratio(self) -> None:
        model = self._strip(1, 1, height=0.0005)
        summary = sample_model(model, None).summary()
        self.assertAlmostEqual(summary["max_aspect_ratio"], 2000.0, places=6)
        self.assertEqual(summary["high_aspect_cells"], 1)
        self.assertEqual(summary["worst_aspect_block"], "b0")
        self.assertEqual(summary["aspect_limit"], ASPECT_LIMIT)

    def test_the_measure_is_cartesian(self) -> None:
        # A long thin cell at 45 degrees has equal x and y area sums: 1.
        model = MeshModel()
        for vertex, (x, y) in {"v0": (0, 0), "v1": (1, 1), "v2": (0.99, 1.01), "v3": (-0.01, 0.01)}.items():
            model.vertices[vertex] = type(model.vertices[vertex])(vertex, x, y)
        model.z_min_patch_type = model.z_max_patch_type = "empty"
        self.assertAlmostEqual(sample_model(model, None).summary()["max_aspect_ratio"], 1.0, places=9)

    def test_without_empty_z_patches_the_depth_counts(self) -> None:
        model = self._strip(1, 1)
        model.z_min_patch_type = model.z_max_patch_type = "patch"
        model.z_max = 0.01
        # Sums over the faces: x and y 2 * 1 * 0.01, z 2 * 1; ratio 100.
        self.assertAlmostEqual(sample_model(model, None).summary()["max_aspect_ratio"], 100.0, places=6)
        # In the plane (size's measure) only x and y count: a unit square.
        self.assertAlmostEqual(sample_model(model, None, planar=True).summary()["max_aspect_ratio"], 1.0,
                               places=6)

    def test_a_coarsened_cell_is_measured_per_real_cell(self) -> None:
        model = self._strip(512, 1)
        real = sample_model(model, None).summary()
        coarse = sample_model(model, 256).summary()
        self.assertEqual(coarse["coarsened_blocks"], 1)
        self.assertAlmostEqual(real["max_aspect_ratio"], 512.0, places=6)
        self.assertAlmostEqual(coarse["max_aspect_ratio"], 512.0, places=6)
