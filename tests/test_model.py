import unittest

from blockdrawer.model import MeshModel, TopologyError, edge_key
from tests.helpers import build_ring_model, center_vertex_ids, edge_between


class MeshModelTests(unittest.TestCase):
    def test_new_model_is_one_valid_block(self) -> None:
        model = MeshModel()

        model.validate()
        self.assertEqual(len(model.blocks), 1)
        self.assertEqual(len(model.vertices), 4)
        self.assertEqual(len(model.edges()), 4)
        self.assertEqual(model.block_cell_counts(model.blocks[0]), (10, 10, 1))

    def test_block_can_be_added_on_every_initial_side(self) -> None:
        for selected in (
            edge_key("v0", "v1"),
            edge_key("v1", "v2"),
            edge_key("v2", "v3"),
            edge_key("v0", "v3"),
        ):
            with self.subTest(selected=selected):
                model = MeshModel()
                added = model.add_block(selected)

                model.validate()
                self.assertEqual(len(model.blocks), 2)
                self.assertEqual(len(model.vertices), 6)
                self.assertEqual(len(model.edge_occurrences()[selected]), 2)
                self.assertFalse(model.is_boundary_edge(selected))
                self.assertIn(added, model.blocks)

    def test_cannot_add_to_internal_edge(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.add_block(selected)

        with self.assertRaisesRegex(TopologyError, "boundary"):
            model.add_block(selected)

    def test_edge_count_propagates_across_shared_topology(self) -> None:
        model = MeshModel()
        shared = edge_key("v1", "v2")
        model.add_block(shared)

        affected = model.set_edge_cells(shared, 23)

        # Shared edge plus the opposite outer edge in each neighboring block.
        self.assertEqual(len(affected), 3)
        self.assertTrue(all(model.edge_cells[current] == 23 for current in affected))
        self.assertEqual(model.block_cell_counts(model.blocks[0]), (10, 23, 1))
        self.assertEqual(model.block_cell_counts(model.blocks[1]), (23, 10, 1))
        model.validate()

    def test_arc_type_creates_a_curved_edge_with_one_control_point(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")

        model.set_edge_type(selected, "arc")

        self.assertEqual(model.edge_type(selected), "arc")
        point_x, point_y = model.arc_point(selected)
        self.assertAlmostEqual(point_x, 0.5)
        self.assertLess(point_y, 0.0)
        self.assertEqual(model.edge_point(selected, 0.0), (0.0, 0.0))
        self.assertAlmostEqual(model.edge_point(selected, 1.0)[0], 1.0)
        self.assertLess(model.edge_point(selected, 0.5)[1], 0.0)
        model.validate()

    def test_arc_point_can_be_moved_and_collinear_move_is_rolled_back(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")

        model.set_arc_point(selected, 0.5, -0.5)
        self.assertEqual(model.arc_point(selected), (0.5, -0.5))

        with self.assertRaisesRegex(TopologyError, "collinear"):
            model.set_arc_point(selected, 0.5, 0.0)

        self.assertEqual(model.arc_point(selected), (0.5, -0.5))
        model.validate()

    def test_changing_arc_back_to_line_removes_optional_geometry(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")

        model.set_edge_type(selected, "line")

        self.assertEqual(model.edge_type(selected), "line")
        self.assertNotIn(selected, model.edge_geometry)
        self.assertEqual(model.edge_point(selected, 0.5), (0.5, 0.0))

    def test_polyline_points_define_length_based_edge_positions(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.0, 1.0)
        second_index = model.add_polyline_point(selected, 0)
        model.set_edge_control_point(selected, second_index, 1.0, 1.0)

        self.assertEqual(model.edge_type(selected), "polyLine")
        self.assertEqual(
            model.edge_control_points(selected),
            ((0.0, 1.0), (1.0, 1.0)),
        )
        self.assertEqual(model.edge_point(selected, 0.25), (0.0, 0.75))
        self.assertEqual(model.edge_point(selected, 0.5), (0.5, 1.0))
        self.assertEqual(model.edge_point(selected, 0.75), (1.0, 0.75))
        model.validate()

    def test_polyline_points_can_be_added_removed_and_reset(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.25, -0.25)
        second = model.add_polyline_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.5, -0.5)
        third = model.add_polyline_point(selected, second)
        model.set_edge_control_point(selected, third, 0.8, -0.3)

        model.reset_polyline_points(selected)
        self.assertEqual(
            model.edge_control_points(selected),
            ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
        )

        model.remove_polyline_point(selected, 1)
        model.remove_polyline_point(selected, 1)
        self.assertEqual(model.edge_control_points(selected), ((0.25, 0.0),))
        with self.assertRaisesRegex(TopologyError, "at least one"):
            model.remove_polyline_point(selected, 0)

    def test_polyline_rejects_coincident_adjacent_point_and_rolls_back(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        before = model.edge_control_points(selected)

        with self.assertRaisesRegex(TopologyError, "coincide"):
            model.set_edge_control_point(selected, 0, 0.0, 0.0)

        self.assertEqual(model.edge_control_points(selected), before)
        model.validate()

    def test_vertex_move_that_invalidates_an_arc_is_rolled_back(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.5)

        with self.assertRaisesRegex(TopologyError, "collinear"):
            model.move_vertex("v1", 1.0, -1.0)

        self.assertEqual(
            (model.vertices["v1"].x, model.vertices["v1"].y),
            (1.0, 0.0),
        )
        model.validate()

    def test_new_block_inherits_source_counts(self) -> None:
        model = MeshModel()
        model.set_edge_cells(edge_key("v0", "v1"), 7)
        model.set_edge_cells(edge_key("v1", "v2"), 13)

        added = model.add_block(edge_key("v1", "v2"))

        self.assertEqual(model.block_cell_counts(added), (13, 7, 1))

    def test_skewed_block_extension_uses_one_outward_normal_displacement(self) -> None:
        model = MeshModel()
        model.move_vertex("v1", 2.0, 0.0)
        model.move_vertex("v2", 1.2, 1.0)
        model.move_vertex("v3", 0.8, 1.0)
        selected = edge_key("v2", "v3")

        added = model.add_block(selected)

        # The selected directed edge is v2 -> v3, so its outward normal points
        # upward. Both new points must use the same perpendicular displacement.
        new_for_v2 = model.vertices[added.vertices[2]]
        new_for_v3 = model.vertices[added.vertices[3]]
        displacement_v2 = (
            new_for_v2.x - model.vertices["v2"].x,
            new_for_v2.y - model.vertices["v2"].y,
        )
        displacement_v3 = (
            new_for_v3.x - model.vertices["v3"].x,
            new_for_v3.y - model.vertices["v3"].y,
        )
        tangent = (
            model.vertices["v3"].x - model.vertices["v2"].x,
            model.vertices["v3"].y - model.vertices["v2"].y,
        )
        self.assertAlmostEqual(displacement_v2[0], displacement_v3[0])
        self.assertAlmostEqual(displacement_v2[1], displacement_v3[1])
        self.assertAlmostEqual(
            displacement_v2[0] * tangent[0]
            + displacement_v2[1] * tangent[1],
            0.0,
        )
        self.assertGreater(displacement_v2[1], 0.0)
        model.validate()

    def test_moving_shared_vertex_updates_both_blocks(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))

        model.move_vertex("v2", 1.2, 1.1)

        self.assertEqual((model.vertices["v2"].x, model.vertices["v2"].y), (1.2, 1.1))
        self.assertIn("v2", model.blocks[0].vertices)
        self.assertIn("v2", model.blocks[1].vertices)
        model.validate()

    def test_invalid_vertex_move_is_rolled_back(self) -> None:
        model = MeshModel()
        before = (model.vertices["v2"].x, model.vertices["v2"].y)

        with self.assertRaisesRegex(TopologyError, "convex"):
            model.move_vertex("v2", -1.0, -1.0)

        self.assertEqual((model.vertices["v2"].x, model.vertices["v2"].y), before)
        model.validate()

    def test_filling_rectangular_grid_reuses_coincident_vertices(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        model.add_block(edge_key("v2", "v3"))
        top_right = next(
            current
            for current in model.edges()
            if sorted(
                (model.vertices[current[0]].x, model.vertices[current[1]].x)
            ) == [1.0, 2.0]
            and model.vertices[current[0]].y == 1.0
            and model.vertices[current[1]].y == 1.0
        )

        model.add_block(top_right)

        self.assertEqual(len(model.blocks), 4)
        self.assertEqual(len(model.vertices), 9)
        model.validate()

    def test_removing_boundary_edge_removes_its_block_and_orphans(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        removed_edge = edge_key("v4", "v5")
        model.set_edge_type(removed_edge, "arc")

        removed = model.remove_edge(removed_edge)

        self.assertEqual([block.id for block in removed], ["b1"])
        self.assertEqual([block.id for block in model.blocks], ["b0"])
        self.assertEqual(set(model.vertices), {"v0", "v1", "v2", "v3"})
        self.assertNotIn(removed_edge, model.edge_cells)
        self.assertNotIn(removed_edge, model.edge_geometry)
        model.validate()

    def test_edge_removal_cannot_delete_every_remaining_block(self) -> None:
        model = MeshModel()
        internal = edge_key("v1", "v2")
        model.add_block(internal)
        before_blocks = list(model.blocks)
        before_vertices = dict(model.vertices)

        with self.assertRaisesRegex(TopologyError, "At least one block"):
            model.remove_edge(internal)

        self.assertEqual(model.blocks, before_blocks)
        self.assertEqual(model.vertices, before_vertices)
        model.validate()

    def test_single_remaining_block_cannot_be_deleted(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")

        self.assertFalse(model.can_remove_edge(selected))
        with self.assertRaisesRegex(TopologyError, "At least one block"):
            model.remove_edge(selected)

        self.assertEqual(len(model.blocks), 1)
        model.validate()

    def test_removing_edge_preserves_blocks_not_incident_to_it(self) -> None:
        model = MeshModel()
        internal = edge_key("v1", "v2")
        first_added = model.add_block(internal)
        next_outer = edge_key(*first_added.directed_edge(2))
        last_block = model.add_block(next_outer)

        removed = model.remove_edge(internal)

        self.assertEqual([block.id for block in removed], ["b0", "b1"])
        self.assertEqual(model.blocks, [last_block])
        self.assertEqual(set(model.vertices), set(last_block.vertices))
        self.assertTrue(model.is_boundary_edge(next_outer))
        self.assertFalse(model.can_remove_edge(next_outer))
        model.validate()

    def test_removing_unknown_edge_is_rejected_without_changes(self) -> None:
        model = MeshModel()
        before_blocks = list(model.blocks)

        with self.assertRaisesRegex(TopologyError, "Unknown edge"):
            model.remove_edge(edge_key("not-a-vertex", "also-missing"))

        self.assertEqual(model.blocks, before_blocks)
        model.validate()

    def test_block_can_be_created_from_four_unordered_existing_vertices(self) -> None:
        model = build_ring_model()
        center = center_vertex_ids(model)
        horizontal_edges = (
            edge_between(model, (1.0, 1.0), (2.0, 1.0)),
            edge_between(model, (1.0, 2.0), (2.0, 2.0)),
        )
        vertical_edges = (
            edge_between(model, (1.0, 1.0), (1.0, 2.0)),
            edge_between(model, (2.0, 1.0), (2.0, 2.0)),
        )
        for current in horizontal_edges:
            model.set_edge_cells(current, 7)
        for current in vertical_edges:
            model.set_edge_cells(current, 9)

        added = model.add_block_from_vertices(
            [center[2], center[0], center[3], center[1]]
        )

        self.assertEqual(len(model.blocks), 9)
        self.assertEqual(set(added.vertices), set(center))
        self.assertEqual(set(model.block_cell_counts(added)[:2]), {7, 9})
        for current in horizontal_edges + vertical_edges:
            self.assertEqual(len(model.edge_occurrences()[current]), 2)
        model.validate()

    def test_existing_block_cannot_be_created_again_from_its_vertices(self) -> None:
        model = MeshModel()

        with self.assertRaisesRegex(TopologyError, "already exists"):
            model.add_block_from_vertices(["v2", "v0", "v3", "v1"])

        self.assertEqual(len(model.blocks), 1)
        model.validate()


if __name__ == "__main__":
    unittest.main()
