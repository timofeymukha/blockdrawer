import unittest

from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data
from tests.helpers import build_ring_model, center_vertex_ids


class ModelHistoryTests(unittest.TestCase):
    def test_block_combination_is_one_undoable_action(self) -> None:
        model = MeshModel()
        split = model.split_edge(edge_key("v0", "v1"), 0.4)
        history = ModelHistory(model)

        model.combine_blocks(split.cut_edges[0])
        history.record(model)

        restored = history.undo()
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(len(restored.blocks), 2)
        self.assertIn(split.cut_edges[0], restored.edge_cells)

        redone = history.redo()
        self.assertIsNotNone(redone)
        assert redone is not None
        self.assertEqual(len(redone.blocks), 1)
        self.assertNotIn(split.cut_edges[0], redone.edge_cells)

    def test_conformal_edge_split_is_one_undoable_action(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        history = ModelHistory(model)

        result = model.split_edge(selected, 0.4)
        history.record(model)

        restored = history.undo()
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(len(restored.blocks), 1)
        self.assertIn(selected, restored.edge_cells)
        self.assertEqual(restored.edge_type(selected), "arc")

        redone = history.redo()
        self.assertIsNotNone(redone)
        assert redone is not None
        self.assertEqual(len(redone.blocks), 2)
        self.assertTrue(all(
            redone.edge_type(current) == "arc"
            for current in result.selected_segments
        ))

    def test_export_settings_are_undoable_session_state(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)
        model.set_export_settings(
            5, -1.0, 1.0, 0.01,
            "front", "wall", "back", "symmetry",
        )
        history.record(model)

        restored = history.undo()
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.z_cells, 1)
        self.assertEqual(restored.z_min_patch_name, "zMin")

        redone = history.redo()
        self.assertIsNotNone(redone)
        assert redone is not None
        self.assertEqual(redone.z_cells, 5)
        self.assertEqual(redone.z_max_patch_type, "symmetry")

    def test_compound_topology_edits_undo_and_redo_as_snapshots(self) -> None:
        model = MeshModel()
        initial = to_data(model)
        history = ModelHistory(model)

        model.set_edge_cells(edge_key("v1", "v2"), 17)
        history.record(model)
        after_cells = to_data(model)
        model.add_block(edge_key("v1", "v2"))
        history.record(model)
        after_block = to_data(model)

        self.assertTrue(history.can_undo)
        self.assertEqual(to_data(history.undo()), after_cells)
        self.assertEqual(to_data(history.undo()), initial)
        self.assertFalse(history.can_undo)
        self.assertEqual(to_data(history.redo()), after_cells)
        self.assertEqual(to_data(history.redo()), after_block)
        self.assertFalse(history.can_redo)

    def test_new_edit_after_undo_discards_redo_branch(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)
        model.move_vertex("v2", 1.2, 1.1)
        history.record(model)
        model = history.undo()

        model.move_vertex("v2", 0.8, 1.1)
        history.record(model)

        self.assertFalse(history.can_redo)
        self.assertEqual(model.vertices["v2"].x, 0.8)

    def test_dirty_state_tracks_saved_snapshot_across_undo(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)
        self.assertFalse(history.is_dirty(model))

        model.set_z_cells(2)
        history.record(model)
        self.assertTrue(history.is_dirty(model))
        history.mark_saved(model)
        self.assertFalse(history.is_dirty(model))

        restored = history.undo()
        self.assertTrue(history.is_dirty(restored))
        restored = history.redo()
        self.assertFalse(history.is_dirty(restored))

    def test_identical_state_is_not_recorded(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)

        self.assertFalse(history.record(model))
        self.assertFalse(history.can_undo)

    def test_arc_creation_and_point_move_are_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        history = ModelHistory(model)

        model.set_edge_type(selected, "arc")
        history.record(model)
        default_point = model.arc_point(selected)
        model.set_arc_point(selected, 0.5, -0.5)
        history.record(model)

        restored = history.undo()
        self.assertEqual(restored.edge_type(selected), "arc")
        self.assertEqual(restored.arc_point(selected), default_point)
        restored = history.undo()
        self.assertEqual(restored.edge_type(selected), "line")
        restored = history.redo()
        self.assertEqual(restored.edge_type(selected), "arc")
        restored = history.redo()
        self.assertEqual(restored.arc_point(selected), (0.5, -0.5))

    def test_polyline_point_reset_is_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.8, -0.2)
        history = ModelHistory(model)
        before = model.edge_control_points(selected)

        model.reset_edge_control_points(selected)
        history.record(model)

        self.assertEqual(
            model.edge_control_points(selected),
            ((1.0 / 3.0, 0.0), (2.0 / 3.0, 0.0)),
        )
        restored = history.undo()
        self.assertEqual(restored.edge_control_points(selected), before)
        restored = history.redo()
        self.assertEqual(
            restored.edge_control_points(selected),
            ((1.0 / 3.0, 0.0), (2.0 / 3.0, 0.0)),
        )

    def test_spline_point_list_edit_is_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        history = ModelHistory(model)

        new_index = model.add_edge_control_point(selected, 0)
        history.record(model)

        self.assertEqual(len(model.edge_control_points(selected)), 2)
        restored = history.undo()
        self.assertEqual(restored.edge_type(selected), "spline")
        self.assertEqual(len(restored.edge_control_points(selected)), 1)
        restored = history.redo()
        self.assertEqual(len(restored.edge_control_points(selected)), 2)
        self.assertEqual(new_index, 1)

    def test_interpolation_point_count_change_is_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "polyLine")
        history = ModelHistory(model)

        model.set_edge_control_point_count(selected, 4)
        history.record(model)

        self.assertEqual(len(model.edge_control_points(selected)), 4)
        restored = history.undo()
        self.assertEqual(len(restored.edge_control_points(selected)), 1)
        restored = history.redo()
        self.assertEqual(
            restored.edge_control_points(selected),
            ((0.2, 0.0), (0.4, 0.0), (0.6, 0.0), (0.8, 0.0)),
        )

    def test_fitted_projection_is_undoable_and_redoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        curve = model.add_geometry_curve(
            ((0.0, -0.2), (0.5, -0.4), (1.0, -0.2))
        )
        history = ModelHistory(model)

        model.project_to_geometry(
            (curve.id,), "y", edges=(selected,), fit=True
        )
        history.record(model)

        self.assertEqual(model.edge_type(selected), "spline")
        restored = history.undo()
        self.assertEqual(restored.edge_type(selected), "line")
        self.assertEqual(restored.vertices["v0"].y, 0.0)
        restored = history.redo()
        self.assertEqual(restored.edge_type(selected), "spline")
        self.assertEqual(restored.edge_control_points(selected), ((0.5, -0.4),))

    def test_edge_deletion_and_all_incident_blocks_undo_atomically(self) -> None:
        model = MeshModel()
        internal = edge_key("v1", "v2")
        first_added = model.add_block(internal)
        model.add_block(edge_key(*first_added.directed_edge(2)))
        before_deletion = to_data(model)
        history = ModelHistory(model)

        model.remove_edge(internal)
        history.record(model)
        self.assertEqual([block.id for block in model.blocks], ["b2"])

        restored = history.undo()
        self.assertEqual(to_data(restored), before_deletion)
        self.assertEqual(len(restored.blocks), 3)
        restored = history.redo()
        self.assertEqual([block.id for block in restored.blocks], ["b2"])

    def test_block_from_existing_vertices_is_one_undoable_action(self) -> None:
        model = build_ring_model()
        history = ModelHistory(model)
        before = to_data(model)

        model.add_block_from_vertices(reversed(center_vertex_ids(model)))
        history.record(model)
        self.assertEqual(len(model.blocks), 9)

        restored = history.undo()
        self.assertEqual(to_data(restored), before)
        self.assertEqual(len(restored.blocks), 8)
        restored = history.redo()
        self.assertEqual(len(restored.blocks), 9)

    def test_standalone_vertex_addition_is_undoable(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)

        added = model.add_vertex(2.0, 3.0)
        history.record(model)

        restored = history.undo()
        self.assertNotIn(added.id, restored.vertices)
        restored = history.redo()
        self.assertEqual(
            (restored.vertices[added.id].x, restored.vertices[added.id].y),
            (2.0, 3.0),
        )

    def test_edge_grading_is_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        history = ModelHistory(model)

        model.set_edge_grading(
            selected, "start_width", 0.025, propagate=True
        )
        history.record(model)
        graded_ratio = model.edge_total_expansion(selected)
        opposite = edge_key("v2", "v3")
        self.assertNotEqual(model.edge_total_expansion(opposite), 1.0)

        restored = history.undo()
        self.assertEqual(restored.edge_total_expansion(selected), 1.0)
        self.assertEqual(restored.edge_total_expansion(opposite), 1.0)
        restored = history.redo()
        self.assertAlmostEqual(
            restored.edge_total_expansion(selected), graded_ratio
        )
        self.assertAlmostEqual(
            restored.edge_total_expansion(opposite), 1.0 / graded_ratio
        )

    def test_spacing_link_creation_and_propagation_are_undoable(self) -> None:
        model = MeshModel()
        history = ModelHistory(model)
        first = edge_key("v0", "v1")
        second = edge_key("v1", "v2")
        model.set_edge_grading(first, "total_ratio", 8.0)
        history.record(model)

        model.add_spacing_link(first, second)
        history.record(model)
        linked_ratio = model.edge_total_expansion(second)

        restored = history.undo()
        self.assertEqual(restored.spacing_links, set())
        self.assertEqual(restored.edge_total_expansion(second), 1.0)
        restored = history.redo()
        self.assertEqual(len(restored.spacing_links), 1)
        self.assertAlmostEqual(
            restored.edge_total_expansion(second), linked_ratio
        )

    def test_boundary_creation_assignment_and_removal_are_undoable(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v3")
        history = ModelHistory(model)

        model.add_boundary("inlet")
        model.set_edge_boundary(selected, "inlet")
        history.record(model)
        assigned = to_data(model)
        model.remove_boundary("inlet")
        history.record(model)

        restored = history.undo()
        self.assertEqual(to_data(restored), assigned)
        self.assertEqual(restored.edge_boundaries[selected], "inlet")
        restored = history.redo()
        self.assertEqual(restored.boundaries, {})
        self.assertEqual(restored.edge_boundaries, {})

    def test_geometry_curve_point_edit_is_undoable(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        history = ModelHistory(model)

        model.set_geometry_curve_point(curve.id, 1, 1.0, 2.0)
        history.record(model)

        restored = history.undo()
        self.assertEqual(
            restored.geometry_curves[curve.id].points,
            ((0.0, 0.0), (1.0, 0.0)),
        )
        restored = history.redo()
        self.assertEqual(
            restored.geometry_curves[curve.id].points,
            ((0.0, 0.0), (1.0, 2.0)),
        )

    def test_geometry_point_visibility_is_undoable(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        history = ModelHistory(model)

        model.set_geometry_curve_point_visibility(curve.id, False)
        history.record(model)

        self.assertFalse(model.geometry_curves[curve.id].show_points)
        restored = history.undo()
        self.assertTrue(restored.geometry_curves[curve.id].show_points)
        restored = history.redo()
        self.assertFalse(restored.geometry_curves[curve.id].show_points)

    def test_geometry_curve_deletion_is_undoable(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        history = ModelHistory(model)

        model.remove_geometry_curve(curve.id)
        history.record(model)

        self.assertNotIn(curve.id, model.geometry_curves)
        restored = history.undo()
        self.assertEqual(
            restored.geometry_curves[curve.id].points,
            ((0.0, 0.0), (1.0, 0.0)),
        )
        restored = history.redo()
        self.assertNotIn(curve.id, restored.geometry_curves)

    def test_mesh_projection_is_one_undoable_action(self) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(((-1.0, -0.5), (2.0, -0.5)))
        history = ModelHistory(model)

        model.project_to_geometry(
            (curve.id,), "y", edges=(edge_key("v0", "v1"),)
        )
        history.record(model)

        self.assertEqual(model.vertices["v0"].y, -0.5)
        restored = history.undo()
        self.assertEqual(restored.vertices["v0"].y, 0.0)
        restored = history.redo()
        self.assertEqual(restored.vertices["v0"].y, -0.5)


if __name__ == "__main__":
    unittest.main()
