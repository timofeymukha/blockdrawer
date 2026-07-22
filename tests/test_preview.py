import unittest

from blockdrawer.model import MeshModel, TopologyError, edge_key
from blockdrawer.preview import MeshPreviewCache, build_mesh_preview


class MeshPreviewTests(unittest.TestCase):
    def _rectangular_model(self) -> MeshModel:
        model = MeshModel()
        model.set_edge_cells(edge_key("v0", "v1"), 4)
        model.set_edge_cells(edge_key("v0", "v3"), 2)
        return model

    def test_rectangular_preview_contains_expected_interior_grid(self) -> None:
        preview = build_mesh_preview(self._rectangular_model())

        self.assertEqual(preview.block_count, 1)
        self.assertEqual(preview.sampled_node_count, 15)
        self.assertEqual(preview.line_count, 4)
        self.assertEqual(
            preview.polylines[0],
            (
                (0.0, 0.5),
                (0.25, 0.5),
                (0.5, 0.5),
                (0.75, 0.5),
                (1.0, 0.5),
            ),
        )
        self.assertEqual(
            preview.polylines[1],
            ((0.25, 0.0), (0.25, 0.5), (0.25, 1.0)),
        )

    def test_coarsening_uses_every_nth_index_and_retains_corners(self) -> None:
        model = self._rectangular_model()

        preview = build_mesh_preview(model, coarsening=3)

        self.assertEqual(preview.sampled_node_count, 6)
        self.assertEqual(preview.line_count, 1)
        self.assertEqual(
            preview.polylines[0],
            ((0.75, 0.0), (0.75, 1.0)),
        )

    def test_preview_boundary_nodes_honor_curves_and_grading(self) -> None:
        model = self._rectangular_model()
        bottom = edge_key("v0", "v1")
        model.set_edge_type(bottom, "arc")
        model.set_arc_point(bottom, 0.5, -0.25)
        model.set_edge_grading(bottom, "total_ratio", 8.0)

        preview = build_mesh_preview(model)
        first_vertical = preview.polylines[1]
        first_fraction = model.edge_node_fraction(bottom, 1)
        expected = model.edge_point(bottom, first_fraction)

        self.assertAlmostEqual(first_vertical[0][0], expected[0])
        self.assertAlmostEqual(first_vertical[0][1], expected[1])
        self.assertNotAlmostEqual(first_vertical[0][1], 0.0)
        self.assertEqual(first_vertical[-1], (0.25, 1.0))

    def test_preview_uses_block_mesh_weights_for_four_distinct_gradings(
        self,
    ) -> None:
        model = MeshModel()
        bottom = edge_key("v0", "v1")
        right = edge_key("v1", "v2")
        top = edge_key("v2", "v3")
        left = edge_key("v0", "v3")
        model.set_edge_cells(bottom, 2)
        model.set_edge_cells(left, 2)
        model.set_edge_grading(bottom, "total_ratio", 9.0)
        model.set_edge_grading(top, "total_ratio", 2.0 / 3.0)
        model.set_edge_grading(left, "total_ratio", 4.0)
        model.set_edge_grading(right, "total_ratio", 0.25)

        preview = build_mesh_preview(model)
        centre = preview.polylines[0][1]

        self.assertAlmostEqual(centre[0], 0.2627118644067797)
        self.assertAlmostEqual(centre[1], 0.3728813559322034)
        self.assertNotAlmostEqual(centre[1], 0.5)

    def test_cache_reuses_unchanged_mesh_and_ignores_reference_geometry(self) -> None:
        model = self._rectangular_model()
        cache = MeshPreviewCache()

        first, first_hit = cache.get(model, 1)
        second, second_hit = cache.get(model, 1)
        model.add_geometry_curve(((0.0, -1.0), (1.0, -1.0)))
        third, third_hit = cache.get(model, 1)
        loose = model.add_vertex(2.0, 2.0)
        model.move_vertex(loose.id, 3.0, 3.0)
        fourth, fourth_hit = cache.get(model, 1)

        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertTrue(third_hit)
        self.assertTrue(fourth_hit)
        self.assertIs(first, second)
        self.assertIs(first, third)
        self.assertIs(first, fourth)

    def test_cache_rebuilds_after_mesh_or_resolution_change(self) -> None:
        model = self._rectangular_model()
        cache = MeshPreviewCache()
        original, _hit = cache.get(model, 1)

        coarsened, coarsened_hit = cache.get(model, 2)
        model.move_vertex("v2", 1.2, 1.0)
        moved, moved_hit = cache.get(model, 1)

        self.assertFalse(coarsened_hit)
        self.assertFalse(moved_hit)
        self.assertIsNot(original, coarsened)
        self.assertIsNot(original, moved)

    def test_invalid_coarsening_is_rejected(self) -> None:
        model = MeshModel()
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    TopologyError, "positive integer"
                ):
                    build_mesh_preview(model, value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
