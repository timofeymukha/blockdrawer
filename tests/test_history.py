import unittest

from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data
from tests.helpers import build_ring_model, center_vertex_ids


class ModelHistoryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
