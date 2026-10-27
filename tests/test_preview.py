import unittest

from blockdrawer.model import MeshModel, TopologyError, edge_key
from blockdrawer.preview import MeshPreviewCache, build_mesh_preview
from blockdrawer.render_cache import points_bounds


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

        # Every third node from either end of the four-cell edge: 0, 3 and
        # 4, 1 - so the kept set does not depend on the edge's direction.
        self.assertEqual(preview.sampled_node_count, 8)
        self.assertEqual(preview.line_count, 2)
        self.assertEqual(
            preview.polylines[0],
            ((0.25, 0.0), (0.25, 1.0)),
        )
        self.assertEqual(
            preview.polylines[1],
            ((0.75, 0.0), (0.75, 1.0)),
        )

    def test_coarsened_lines_of_neighbouring_blocks_meet_on_shared_nodes(self) -> None:
        # Two blocks share v1-v2; the first runs it upward, the second
        # downward. With five cells and a factor of two, counting from one
        # end only kept nodes 0, 2, 4, 5 on one side and 5, 3, 1, 0 on the
        # other, so the lines did not meet across the edge.
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        model.set_edge_cells(edge_key("v1", "v2"), 5)
        model.set_edge_cells(edge_key("v0", "v1"), 5)
        shared = edge_key("v1", "v2")
        x = model.vertices["v1"].x

        preview = build_mesh_preview(model, coarsening=2)

        on_shared = sorted(
            {
                round(point[1], 9)
                for line in preview.polylines
                for point in (line[0], line[-1])
                if abs(point[0] - x) < 1e-12
            }
        )
        expected = sorted(
            {round(model.edge_point(shared, model.edge_node_fraction(shared, index))[1], 9)
             for index in (1, 2, 3, 4)}
        )
        self.assertEqual(on_shared, expected)
        kept_per_block = len(on_shared)
        # Both blocks end a line on each of those nodes.
        hits = [
            point
            for line in preview.polylines
            for point in (line[0], line[-1])
            if abs(point[0] - x) < 1e-12
        ]
        self.assertEqual(len(hits), 2 * kept_per_block)

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

    def test_preview_accumulates_exact_bounds_for_every_polyline(self) -> None:
        model = self._rectangular_model()
        bottom = edge_key("v0", "v1")
        model.set_edge_type(bottom, "arc")
        model.set_arc_point(bottom, 0.5, -0.4)

        preview = build_mesh_preview(model)

        self.assertEqual(
            preview.polyline_bounds,
            tuple(points_bounds(polyline) for polyline in preview.polylines),
        )

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
