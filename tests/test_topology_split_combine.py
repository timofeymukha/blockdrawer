import math
import unittest

from blockdrawer.model import MeshModel, TopologyError, edge_key
from tests.helpers import edge_between


class SplitCombineTests(unittest.TestCase):
    def test_combining_split_block_restores_counts_grading_and_arc(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 10)
        model.set_edge_grading(selected, "total_ratio", 8.0)
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.25)
        original_points = tuple(
            model.edge_point(selected, index / 20.0)
            for index in range(21)
        )

        split = model.split_edge(selected, model.edge_node_fraction(selected, 4))
        result = model.combine_blocks(split.cut_edges[0])

        self.assertEqual(len(model.blocks), 1)
        self.assertEqual(set(model.vertices), {"v0", "v1", "v2", "v3"})
        self.assertEqual(result.removed_edges, split.cut_edges)
        self.assertEqual(model.edge_cells[selected], 10)
        self.assertEqual(model.edge_type(selected), "arc")
        self.assertAlmostEqual(model.edge_total_expansion(selected), 8.0)
        for index, expected in enumerate(original_points):
            self.assertLess(
                math.dist(expected, model.edge_point(selected, index / 20.0)),
                1.0e-11,
            )
        model.validate()

    def test_split_and_combine_transfer_endpoint_spacing_link(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        incident = edge_key("v0", "v3")
        model.add_spacing_link(selected, incident)

        split = model.split_edge(selected, 0.4)

        endpoint_segment = next(
            current for current in split.selected_segments if "v0" in current
        )
        split_link = next(iter(model.spacing_links))
        self.assertIn(endpoint_segment, (
            split_link.first_edge, split_link.second_edge
        ))
        self.assertIn(incident, (split_link.first_edge, split_link.second_edge))

        model.combine_blocks(split.cut_edges[0])

        combined_link = next(iter(model.spacing_links))
        self.assertIn(selected, (
            combined_link.first_edge, combined_link.second_edge
        ))
        self.assertIn(incident, (
            combined_link.first_edge, combined_link.second_edge
        ))
        model.validate()

    def test_combine_propagates_across_a_connected_multiblock_cut(self) -> None:
        model = MeshModel()
        shared = edge_key("v1", "v2")
        model.add_block(shared)
        original_blocks = tuple(model.blocks)
        split = model.split_edge(shared, 0.37)

        result = model.combine_blocks(split.cut_edges[0])

        self.assertEqual(len(result.removed_edges), 2)
        self.assertEqual(len(result.removed_block_ids), 2)
        self.assertEqual(
            [(block.id, set(block.vertices)) for block in model.blocks],
            [(block.id, set(block.vertices)) for block in original_blocks],
        )
        self.assertFalse(any(
            current in model.edge_cells for current in split.cut_edges
        ))
        model.validate()

    def test_existing_two_block_interface_can_be_combined_without_split_metadata(
        self,
    ) -> None:
        model = MeshModel()
        internal = edge_key("v1", "v2")
        model.add_block(internal)
        first_bottom = edge_between(model, (0.0, 0.0), (1.0, 0.0))
        second_bottom = edge_between(model, (1.0, 0.0), (2.0, 0.0))
        model.add_boundary("wall")
        model.set_boundary_type("wall", "wall")
        model.set_edge_boundary(first_bottom, "wall")
        model.set_edge_boundary(second_bottom, "wall")

        result = model.combine_blocks(internal)

        bottom = edge_between(model, (0.0, 0.0), (2.0, 0.0))
        top = edge_between(model, (0.0, 1.0), (2.0, 1.0))
        self.assertEqual(len(model.blocks), 1)
        self.assertEqual(model.edge_cells[bottom], 20)
        self.assertEqual(model.edge_cells[top], 20)
        self.assertEqual(model.edge_boundaries[bottom], "wall")
        self.assertEqual(len(result.removed_vertex_ids), 2)
        model.validate()

    def test_combine_retains_point_list_edge_types(self) -> None:
        for kind in ("polyLine", "spline"):
            with self.subTest(kind=kind):
                model = MeshModel()
                selected = edge_key("v0", "v1")
                model.set_edge_type(selected, kind)
                model.set_edge_control_point(selected, 0, 0.3, -0.2)
                point_index = model.add_edge_control_point(selected, 0)
                model.set_edge_control_point(
                    selected, point_index, 0.72, -0.16
                )
                split = model.split_edge(selected, 0.4)

                model.combine_blocks(split.cut_edges[0])

                self.assertEqual(model.edge_type(selected), kind)
                self.assertGreaterEqual(
                    len(model.edge_control_points(selected)), 1
                )
                model.validate()

    def test_combine_rejects_different_boundary_assignments_atomically(self) -> None:
        model = MeshModel()
        internal = edge_key("v1", "v2")
        model.add_block(internal)
        first = edge_between(model, (0.0, 0.0), (1.0, 0.0))
        second = edge_between(model, (1.0, 0.0), (2.0, 0.0))
        model.add_boundary("leftPart")
        model.add_boundary("rightPart")
        model.set_edge_boundary(first, "leftPart")
        model.set_edge_boundary(second, "rightPart")
        blocks_before = tuple(model.blocks)
        cells_before = dict(model.edge_cells)

        with self.assertRaisesRegex(TopologyError, "same boundary"):
            model.combine_blocks(internal)

        self.assertEqual(tuple(model.blocks), blocks_before)
        self.assertEqual(model.edge_cells, cells_before)
        model.validate()

    def test_only_internal_edges_can_combine_blocks(self) -> None:
        model = MeshModel()
        boundary = edge_key("v0", "v1")

        self.assertFalse(model.can_combine_edge(boundary))
        with self.assertRaisesRegex(TopologyError, "internal edge"):
            model.combine_blocks(boundary)

    def test_edge_split_divides_block_counts_and_opposite_edges(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")

        result = model.split_edge(selected, 0.31)

        self.assertEqual((result.first_cells, result.second_cells), (3, 7))
        self.assertEqual(len(model.blocks), 2)
        self.assertEqual(len(result.affected_edges), 2)
        self.assertEqual(len(result.split_vertex_ids), 2)
        self.assertEqual(len(result.cut_edges), 1)
        self.assertNotIn(selected, model.edge_cells)
        self.assertEqual(
            [model.edge_cells[current] for current in result.selected_segments],
            [3, 7],
        )
        self.assertEqual(model.edge_cells[result.cut_edges[0]], 10)
        self.assertEqual(
            len(model.edge_occurrences()[result.cut_edges[0]]), 2
        )
        split_x = {
            round(model.vertices[identifier].x, 12)
            for identifier in result.split_vertex_ids
        }
        self.assertEqual(split_x, {0.31})
        model.validate()

    def test_edge_split_propagates_through_shared_multiblock_strip(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.add_block(selected)
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 1.15, 0.5)

        result = model.split_edge(selected, 0.4)

        self.assertEqual(len(result.affected_edges), 3)
        self.assertEqual(len(result.cut_edges), 2)
        self.assertEqual(len(model.blocks), 4)
        self.assertEqual(len(result.new_block_ids), 2)
        self.assertTrue(all(
            current not in model.edge_cells for current in result.affected_edges
        ))
        self.assertTrue(all(
            len(model.edge_occurrences()[current]) == 2
            for current in result.cut_edges
        ))
        self.assertTrue(all(
            model.edge_type(current) == "arc"
            and len(model.edge_occurrences()[current]) == 2
            for current in result.selected_segments
        ))
        model.validate()

    def test_one_cell_edge_split_creates_two_one_cell_segments(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 1)

        result = model.split_edge(selected, 0.8)

        self.assertEqual((result.first_cells, result.second_cells), (1, 1))
        self.assertEqual(
            [model.edge_cells[current] for current in result.selected_segments],
            [1, 1],
        )
        model.validate()

    def test_split_at_graded_node_preserves_original_selected_edge_nodes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 10)
        model.set_edge_grading(selected, "total_ratio", 8.0)
        split_fraction = model.edge_node_fraction(selected, 4)
        original_nodes = [
            model.edge_point(selected, model.edge_node_fraction(selected, index))
            for index in range(11)
        ]

        result = model.split_edge(selected, split_fraction)
        split_nodes = [
            model.edge_point(
                current, model.edge_node_fraction(current, index)
            )
            for current in result.selected_segments
            for index in range(model.edge_cells[current] + 1)
        ]

        self.assertEqual((result.first_cells, result.second_cells), (4, 6))
        for expected in original_nodes:
            self.assertTrue(any(
                math.dist(expected, actual) < 1.0e-12
                for actual in split_nodes
            ))
        model.validate()

    def test_split_retains_arc_polyline_and_spline_edge_types(self) -> None:
        for kind in ("arc", "polyLine", "spline"):
            with self.subTest(kind=kind):
                model = MeshModel()
                selected = edge_key("v0", "v1")
                model.set_edge_type(selected, kind)
                model.set_edge_control_point(selected, 0, 0.3, -0.25)
                if kind in MeshModel.MULTI_POINT_EDGE_TYPES:
                    second = model.add_edge_control_point(selected, 0)
                    model.set_edge_control_point(selected, second, 0.72, -0.18)
                original_samples = [
                    model.edge_point(selected, index / 40.0)
                    for index in range(41)
                ]

                result = model.split_edge(selected, 0.37)

                self.assertTrue(all(
                    model.edge_type(current) == kind
                    for current in result.selected_segments
                ))
                self.assertTrue(all(
                    model.edge_control_points(current)
                    for current in result.selected_segments
                ))
                if kind in ("arc", "polyLine"):
                    split_samples = [
                        model.edge_point(current, index / 400.0)
                        for current in result.selected_segments
                        for index in range(401)
                    ]
                    self.assertLess(max(
                        min(math.dist(point, candidate)
                            for candidate in split_samples)
                        for point in original_samples
                    ), 0.003)
                model.validate()

    def test_split_preserves_boundary_on_both_resulting_segments(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.add_boundary("wall")
        model.set_boundary_type("wall", "wall")
        model.set_edge_boundary(selected, "wall")

        result = model.split_edge(selected, 0.5)

        self.assertEqual(
            [model.edge_boundaries[current] for current in result.selected_segments],
            ["wall", "wall"],
        )
        model.validate()



if __name__ == "__main__":
    unittest.main()
