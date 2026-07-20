import math
import unittest

from blockdrawer.model import EdgeGeometry, MeshModel, TopologyError, edge_key
from tests.helpers import (
    build_ring_model,
    center_vertex_ids,
    edge_between,
    vertex_at,
)


class MeshModelTests(unittest.TestCase):
    def test_named_boundaries_assign_only_exterior_edges_with_unique_colors(self) -> None:
        model = MeshModel()
        inlet = model.add_boundary("inlet")
        walls = model.add_boundary("walls")
        selected = edge_key("v0", "v3")

        model.set_edge_boundary(selected, inlet.name)

        self.assertNotEqual(inlet.color, walls.color)
        self.assertEqual(model.edge_boundaries[selected], "inlet")
        shared = edge_key("v1", "v2")
        model.add_block(shared)
        with self.assertRaisesRegex(TopologyError, "exterior"):
            model.set_edge_boundary(shared, "walls")
        model.validate()

    def test_cyclic_boundaries_are_paired_and_detached_atomically(self) -> None:
        model = MeshModel()
        model.add_boundary("periodicA")
        model.add_boundary("periodicB")

        affected = model.set_boundary_type(
            "periodicA", "cyclic", neighbour_patch="periodicB"
        )

        self.assertEqual(affected, {"periodicA", "periodicB"})
        self.assertEqual(
            model.boundaries["periodicA"].neighbour_patch, "periodicB"
        )
        self.assertEqual(
            model.boundaries["periodicB"].neighbour_patch, "periodicA"
        )
        model.set_boundary_type("periodicA", "wall")
        self.assertEqual(model.boundaries["periodicA"].kind, "wall")
        self.assertEqual(model.boundaries["periodicB"].kind, "patch")
        self.assertIsNone(model.boundaries["periodicB"].neighbour_patch)
        model.validate()

    def test_boundary_removal_unassigns_edges_and_detaches_cyclic_partner(self) -> None:
        model = MeshModel()
        model.add_boundary("first")
        model.add_boundary("second")
        model.set_boundary_type("first", "cyclic", neighbour_patch="second")
        selected = edge_key("v0", "v3")
        model.set_edge_boundary(selected, "first")

        model.remove_boundary("first")

        self.assertNotIn("first", model.boundaries)
        self.assertNotIn(selected, model.edge_boundaries)
        self.assertEqual(model.boundaries["second"].kind, "patch")
        model.validate()

    def test_extrusion_moves_boundary_assignment_to_new_outer_edge(self) -> None:
        model = MeshModel()
        model.add_boundary("inlet")
        selected = edge_key("v0", "v1")
        model.set_edge_boundary(selected, "inlet")

        added = model.add_block(selected)
        opposite = edge_key(*added.directed_edge(2))

        self.assertNotIn(selected, model.edge_boundaries)
        self.assertEqual(model.edge_boundaries[opposite], "inlet")
        self.assertTrue(model.is_boundary_edge(opposite))
        model.validate()

    def test_block_from_vertices_prunes_assignment_that_becomes_internal(self) -> None:
        model = build_ring_model()
        center = center_vertex_ids(model)
        selected = edge_key(center[0], center[1])
        model.add_boundary("hole")
        model.set_edge_boundary(selected, "hole")

        model.add_block_from_vertices(center)

        self.assertNotIn(selected, model.edge_boundaries)
        self.assertFalse(model.is_boundary_edge(selected))
        model.validate()

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

    def test_all_four_grading_inputs_recompute_equivalent_values(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 4)

        for parameter, value in (
            ("cell_ratio", 2.0),
            ("total_ratio", 8.0),
            ("start_width", 1.0 / 15.0),
            ("end_width", 8.0 / 15.0),
        ):
            with self.subTest(parameter=parameter):
                grading = model.set_edge_grading(selected, parameter, value)
                self.assertAlmostEqual(grading.length, 1.0)
                self.assertAlmostEqual(grading.cell_ratio, 2.0)
                self.assertAlmostEqual(grading.total_ratio, 8.0)
                self.assertAlmostEqual(grading.start_width, 1.0 / 15.0)
                self.assertAlmostEqual(grading.end_width, 8.0 / 15.0)
        model.validate()

    def test_graded_node_fractions_follow_geometric_cell_widths(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_cells(selected, 4)
        model.set_edge_grading(selected, "total_ratio", 8.0)

        fractions = [
            model.edge_node_fraction(selected, index) for index in range(5)
        ]

        expected = (0.0, 1.0 / 15.0, 3.0 / 15.0, 7.0 / 15.0, 1.0)
        for actual, wanted in zip(fractions, expected):
            self.assertAlmostEqual(actual, wanted)
        self.assertEqual(
            model.edge_expansion_in_direction("v0", "v1"), 8.0
        )
        self.assertEqual(
            model.edge_expansion_in_direction("v1", "v0"), 1.0 / 8.0
        )

    def test_grading_propagates_over_cell_count_edges_with_direction(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        opposite = edge_key("v2", "v3")

        model.set_edge_grading(
            selected, "total_ratio", 8.0, propagate=True
        )

        self.assertEqual(
            set(model.edge_grading),
            model.edge_constraint_component(selected),
        )
        self.assertEqual(model.edge_total_expansion(selected), 8.0)
        self.assertEqual(model.edge_total_expansion(opposite), 1.0 / 8.0)
        self.assertEqual(
            model.edge_expansion_in_direction("v3", "v2"), 8.0
        )
        self.assertEqual(
            model.edge_total_expansion(edge_key("v0", "v3")), 1.0
        )
        model.validate()

    def test_grading_propagation_is_transitive_across_blocks(self) -> None:
        model = MeshModel()
        selected = edge_key("v1", "v2")
        model.add_block(selected)
        affected = model.edge_constraint_component(selected)

        model.set_edge_grading(
            selected, "total_ratio", 8.0, propagate=True
        )

        self.assertEqual(len(affected), 3)
        self.assertEqual(set(model.edge_grading), affected)
        for current in affected:
            self.assertEqual(model.edge_total_expansion(current), 8.0)
        model.validate()

    def test_one_cell_edge_resets_and_rejects_nonuniform_grading(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_grading(selected, "total_ratio", 4.0)

        model.set_edge_cells(selected, 1)

        self.assertEqual(model.edge_total_expansion(selected), 1.0)
        grading = model.edge_grading_values(selected)
        self.assertEqual(grading.start_width, grading.length)
        self.assertEqual(grading.end_width, grading.length)
        with self.assertRaisesRegex(TopologyError, "one-cell"):
            model.set_edge_grading(selected, "cell_ratio", 2.0)

    def test_arc_length_is_used_for_grading_widths(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        model.set_arc_point(selected, 0.5, -0.5)

        grading = model.edge_grading_values(selected)

        self.assertAlmostEqual(grading.length, math.pi / 2.0)
        self.assertAlmostEqual(grading.start_width, math.pi / 20.0)
        self.assertAlmostEqual(grading.end_width, math.pi / 20.0)

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
        second_index = model.add_edge_control_point(selected, 0)
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
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.5, -0.5)
        third = model.add_edge_control_point(selected, second)
        model.set_edge_control_point(selected, third, 0.8, -0.3)

        model.reset_edge_control_points(selected)
        self.assertEqual(
            model.edge_control_points(selected),
            ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
        )

        model.remove_edge_control_point(selected, 1)
        model.remove_edge_control_point(selected, 1)
        self.assertEqual(model.edge_control_points(selected), ((0.25, 0.0),))
        with self.assertRaisesRegex(TopologyError, "at least one"):
            model.remove_edge_control_point(selected, 0)

    def test_spline_intersects_all_ordered_points(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        model.set_edge_control_point(selected, 0, 0.25, -0.4)
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.75, -0.2)

        first_fraction = math.hypot(0.25, -0.4)
        second_length = math.hypot(0.5, 0.2)
        third_length = math.hypot(0.25, 0.2)
        total = first_fraction + second_length + third_length

        self.assertEqual(model.edge_type(selected), "spline")
        first_point = model.edge_point(selected, first_fraction / total)
        second_point = model.edge_point(
            selected, (first_fraction + second_length) / total
        )
        self.assertAlmostEqual(first_point[0], 0.25)
        self.assertAlmostEqual(first_point[1], -0.4)
        self.assertAlmostEqual(second_point[0], 0.75)
        self.assertAlmostEqual(second_point[1], -0.2)
        self.assertNotEqual(model.edge_point(selected, 0.5)[1], 0.0)
        model.validate()

    def test_spline_rendering_samples_every_span_and_retains_all_points(
        self,
    ) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        control_points = tuple(
            (
                index / 601.0,
                -0.2 * math.sin(math.pi * index / 601.0),
            )
            for index in range(1, 601)
        )
        model.edge_geometry[selected] = EdgeGeometry(
            "spline", control_points
        )
        model.validate()

        rendered = model.edge_render_points(
            selected, spline_samples_per_span=4
        )
        path = ((0.0, 0.0), *control_points, (1.0, 0.0))

        self.assertEqual(len(rendered), (len(path) - 1) * 4 + 1)
        for index, point in enumerate(path):
            self.assertEqual(rendered[index * 4], point)

    def test_spline_supports_add_remove_and_reset(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        second = model.add_edge_control_point(selected, 0)
        model.set_edge_control_point(selected, second, 0.8, -0.3)

        model.reset_edge_control_points(selected)

        self.assertEqual(
            model.edge_control_points(selected),
            ((1.0 / 3.0, 0.0), (2.0 / 3.0, 0.0)),
        )
        model.remove_edge_control_point(selected, 1)
        with self.assertRaisesRegex(TopologyError, "at least one"):
            model.remove_edge_control_point(selected, 0)

    def test_point_list_count_is_resized_on_equidistant_chord_positions(
        self,
    ) -> None:
        for kind in ("polyLine", "spline"):
            with self.subTest(kind=kind):
                model = MeshModel()
                selected = edge_key("v0", "v1")
                model.set_edge_type(selected, kind)
                model.set_edge_control_point(selected, 0, 0.4, -0.5)

                model.set_edge_control_point_count(selected, 3)

                self.assertEqual(
                    model.edge_control_points(selected),
                    ((0.25, 0.0), (0.5, 0.0), (0.75, 0.0)),
                )
                model.set_edge_control_point_count(selected, 1)
                self.assertEqual(
                    model.edge_control_points(selected), ((0.5, 0.0),)
                )
                model.validate()

    def test_point_list_count_rejects_invalid_values_and_edge_types(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "spline")
        before = model.edge_control_points(selected)

        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TopologyError, "positive integer"):
                    model.set_edge_control_point_count(selected, value)
                self.assertEqual(model.edge_control_points(selected), before)

        model.set_edge_type(selected, "arc")
        with self.assertRaisesRegex(TopologyError, "point-list"):
            model.set_edge_control_point_count(selected, 2)

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

    def test_new_block_opposite_edge_inherits_source_grading(self) -> None:
        for selected in (
            edge_key("v0", "v1"),
            edge_key("v1", "v2"),
            edge_key("v2", "v3"),
            edge_key("v0", "v3"),
        ):
            with self.subTest(selected=selected):
                model = MeshModel()
                source_direction = model.edge_occurrences()[selected][0][2]
                model.set_edge_grading(selected, "total_ratio", 7.0)
                source_ratio = model.edge_expansion_in_direction(
                    *source_direction
                )

                added = model.add_block(selected)
                opposite_direction = (
                    added.vertices[2], added.vertices[3]
                )

                self.assertAlmostEqual(
                    model.edge_expansion_in_direction(*opposite_direction),
                    source_ratio,
                )
                model.validate()

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

    def test_standalone_vertex_can_be_added_and_moved(self) -> None:
        model = MeshModel()

        added = model.add_vertex(2.0, 3.0)
        model.move_vertex(added.id, 2.5, 3.5)

        self.assertEqual((added.x, added.y), (2.5, 3.5))
        self.assertTrue(all(
            added.id not in block.vertices for block in model.blocks
        ))
        with self.assertRaisesRegex(TopologyError, "already exists"):
            model.add_vertex(2.5, 3.5)
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

    def test_edge_removal_preserves_unrelated_standalone_vertex(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v1", "v2"))
        standalone = model.add_vertex(4.0, 4.0)

        model.remove_edge(edge_key("v4", "v5"))

        self.assertIn(standalone.id, model.vertices)
        self.assertEqual(
            set(model.vertices),
            {"v0", "v1", "v2", "v3", standalone.id},
        )
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

    def test_standalone_vertex_completes_missing_block_in_l_shape(self) -> None:
        model = MeshModel()
        model.add_block(edge_between(model, (1.0, 0.0), (1.0, 1.0)))
        model.add_block(edge_between(model, (1.0, 1.0), (2.0, 1.0)))
        missing_corner = model.add_vertex(0.0, 2.0)

        added = model.add_block_from_vertices([
            vertex_at(model, (0.0, 1.0)),
            vertex_at(model, (1.0, 1.0)),
            vertex_at(model, (1.0, 2.0)),
            missing_corner.id,
        ])

        self.assertEqual(len(model.blocks), 4)
        self.assertEqual(len(model.vertices), 9)
        self.assertIn(missing_corner.id, added.vertices)
        for current in (
            edge_between(model, (0.0, 1.0), (1.0, 1.0)),
            edge_between(model, (1.0, 1.0), (1.0, 2.0)),
        ):
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
