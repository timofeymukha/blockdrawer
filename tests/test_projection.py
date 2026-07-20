import math
import unittest

from blockdrawer.projection import (
    DEFAULT_FIT_MAX_POINTS,
    ProjectionError,
    ReferenceProjector,
)
from blockdrawer.model import MeshModel, TopologyError, edge_key


class ReferenceProjectorTests(unittest.TestCase):
    def test_x_projection_moves_horizontally_to_nearest_intersection(self) -> None:
        projector = ReferenceProjector((
            ((-2.0, -1.0), (-2.0, 1.0)),
            ((3.0, -1.0), (3.0, 1.0)),
        ))

        projected = projector.project((0.25, 0.4), "x")

        self.assertAlmostEqual(projected[0], -2.0)
        self.assertEqual(projected[1], 0.4)

    def test_y_projection_moves_vertically_and_preserves_x(self) -> None:
        projector = ReferenceProjector((((-1.0, 2.0), (1.0, 2.0)),))

        projected = projector.project((0.25, -3.0), "y")

        self.assertEqual(projected[0], 0.25)
        self.assertAlmostEqual(projected[1], 2.0)

    def test_axis_projection_reports_when_curve_does_not_intersect(self) -> None:
        projector = ReferenceProjector((((0.0, 0.0), (0.0, 1.0)),))

        with self.assertRaisesRegex(ProjectionError, "horizontal"):
            projector.project((1.0, 2.0), "x")

    def test_orthogonal_projection_finds_true_nearest_point(self) -> None:
        projector = ReferenceProjector((((0.0, 0.0), (2.0, 2.0)),))

        projected = projector.project((2.0, 0.0), "orthogonal")

        self.assertAlmostEqual(projected[0], 1.0)
        self.assertAlmostEqual(projected[1], 1.0)
        self.assertAlmostEqual(math.dist(projected, (2.0, 0.0)), math.sqrt(2.0))

    def test_axis_projection_detects_a_tangent_cubic_intersection(self) -> None:
        projector = ReferenceProjector((
            ((-1.0, 1.0), (0.0, 0.0), (1.0, 1.0)),
        ))

        projected = projector.project((2.0, 0.0), "x")

        self.assertAlmostEqual(projected[0], 0.0)
        self.assertEqual(projected[1], 0.0)

    def test_z_projection_is_not_supported_in_the_2d_editor(self) -> None:
        projector = ReferenceProjector((((0.0, 0.0), (1.0, 0.0)),))

        with self.assertRaisesRegex(ProjectionError, "Unsupported"):
            projector.project((0.25, 1.0), "z")

    def test_invalid_direction_is_rejected(self) -> None:
        projector = ReferenceProjector((((0.0, 0.0), (1.0, 0.0)),))

        with self.assertRaisesRegex(ProjectionError, "Unsupported"):
            projector.project((0.0, 0.0), "radial")

    def test_projection_location_identifies_the_target_curve_and_span(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (1.0, 0.0)),
            ((0.0, 2.0), (1.0, 2.0)),
        ))

        location = projector.project_location((0.25, 1.8), "orthogonal")

        self.assertEqual(location.curve_index, 1)
        self.assertAlmostEqual(location.span_parameter, 0.25)
        self.assertEqual(location.point, (0.25, 2.0))

    def test_full_reference_section_is_reproduced_to_fit_tolerance(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (0.5, 0.4), (1.0, 0.0)),
        ))
        start = projector.project_location((0.0, -1.0), "y")
        end = projector.project_location((1.0, -1.0), "y")

        fitted = projector.fit_spline(start, end, ((0.0, -1.0), (1.0, -1.0)))

        self.assertEqual(fitted.points, ((0.5, 0.4),))
        self.assertLessEqual(fitted.max_error, fitted.tolerance)
        self.assertEqual(fitted.branch, "open")

    def test_straight_reference_fit_keeps_one_interpolation_point(self) -> None:
        projector = ReferenceProjector((((0.0, 0.0), (1.0, 0.0)),))
        start = projector.project_location((0.0, -1.0), "y")
        end = projector.project_location((1.0, -1.0), "y")

        fitted = projector.fit_spline(
            start, end, ((0.0, -1.0), (1.0, -1.0))
        )

        self.assertEqual(fitted.points, ((0.5, 0.0),))
        self.assertLessEqual(fitted.max_error, fitted.tolerance)

    def test_partial_reference_section_refines_until_tolerance_is_met(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (0.5, 0.4), (1.0, 0.0)),
        ))
        start = projector.project_location((0.15, -1.0), "y")
        end = projector.project_location((0.85, -1.0), "y")

        fitted = projector.fit_spline(
            start,
            end,
            tuple((0.15 + 0.7 * index / 16.0, -1.0) for index in range(17)),
            relative_tolerance=1.0e-5,
        )

        self.assertGreater(len(fitted.points), 1)
        self.assertLessEqual(fitted.max_error, fitted.tolerance)

    def test_fit_returns_best_result_at_default_point_cap(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (0.5, 0.4), (1.0, 0.0)),
        ))
        start = projector.project_location((0.15, -1.0), "y")
        end = projector.project_location((0.85, -1.0), "y")

        fitted = projector.fit_spline(
            start,
            end,
            ((0.15, -1.0), (0.85, -1.0)),
        )

        self.assertEqual(len(fitted.points), DEFAULT_FIT_MAX_POINTS)
        self.assertGreater(fitted.max_error, fitted.tolerance)

    def test_dense_airfoil_fit_remains_bounded_by_practical_default(self) -> None:
        points = []
        for index in range(601):
            angle = math.pi * index / 600.0
            x = 0.5 * (1.0 - math.cos(angle))
            thickness = 0.6 * (
                0.2969 * math.sqrt(x)
                - 0.1260 * x
                - 0.3516 * x**2
                + 0.2843 * x**3
                - 0.1015 * x**4
            )
            points.append((x, thickness))
        projector = ReferenceProjector((points,))
        start = projector.project_location(points[0], "orthogonal")
        end = projector.project_location(points[-1], "orthogonal")

        fitted = projector.fit_spline(
            start, end, (points[0], points[-1])
        )

        self.assertLessEqual(len(fitted.points), DEFAULT_FIT_MAX_POINTS)
        self.assertTrue(math.isfinite(fitted.max_error))
        path = (start.point, *fitted.points, end.point)
        self.assertGreater(
            min(math.dist(first, second) for first, second in zip(
                path, path[1:]
            )),
            MeshModel.COORDINATE_TOLERANCE,
        )

    def test_fit_honours_custom_tolerance_and_point_cap(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (0.5, 0.4), (1.0, 0.0)),
        ))
        start = projector.project_location((0.15, -1.0), "y")
        end = projector.project_location((0.85, -1.0), "y")

        fitted = projector.fit_spline(
            start,
            end,
            ((0.15, -1.0), (0.85, -1.0)),
            relative_tolerance=1.0e-12,
            max_interpolation_points=17,
        )

        self.assertEqual(len(fitted.points), 17)
        self.assertGreater(fitted.max_error, fitted.tolerance)

    def test_fit_requires_both_endpoints_on_the_same_curve(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (0.5, 0.0)),
            ((1.5, 0.0), (2.0, 0.0)),
        ))
        start = projector.project_location((0.0, 0.1), "orthogonal")
        end = projector.project_location((2.0, 0.1), "orthogonal")

        with self.assertRaisesRegex(ProjectionError, "same reference curve"):
            projector.fit_spline(start, end, ((0.0, 0.1), (2.0, 0.1)))

    def test_closed_curve_uses_the_branch_nearest_the_original_edge(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0),
             (1.0, -1.0), (0.0, 0.0)),
        ))
        start = projector.project_location((1.0, 1.0), "orthogonal")
        end = projector.project_location((1.0, -1.0), "orthogonal")

        # A relaxed tolerance keeps this test focused on branch selection; the
        # production tolerance is exercised by the open-section tests above.
        fitted = projector.fit_spline(
            start,
            end,
            ((1.0, 1.0), (2.0, 0.0), (1.0, -1.0)),
            relative_tolerance=1.0e-4,
        )

        self.assertEqual(fitted.branch, "forward")
        self.assertGreater(max(point[0] for point in fitted.points), 1.9)

    def test_equally_close_closed_curve_branches_are_rejected(self) -> None:
        projector = ReferenceProjector((
            ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0),
             (1.0, -1.0), (0.0, 0.0)),
        ))
        start = projector.project_location((1.0, 1.0), "orthogonal")
        end = projector.project_location((1.0, -1.0), "orthogonal")

        with self.assertRaisesRegex(ProjectionError, "equally close"):
            projector.fit_spline(start, end, (start.point, end.point))


class MeshProjectionTests(unittest.TestCase):
    def test_selected_vertex_is_projected_without_moving_other_vertices(
        self,
    ) -> None:
        model = MeshModel()
        target = model.add_geometry_curve(((-1.0, -0.25), (2.0, -0.25)))

        result = model.project_to_geometry(
            (target.id,), "y", vertex_ids=("v0",)
        )

        self.assertEqual((model.vertices["v0"].x, model.vertices["v0"].y), (0.0, -0.25))
        self.assertEqual((model.vertices["v1"].x, model.vertices["v1"].y), (1.0, 0.0))
        self.assertEqual(result.vertex_ids, ("v0",))
        self.assertEqual(result.projected_point_count, 1)
        model.validate()

    def test_line_edge_projection_moves_both_endpoints(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        target = model.add_geometry_curve(((-1.0, -0.5), (2.0, -0.5)))

        result = model.project_to_geometry(
            (target.id,), "y", edges=(selected,)
        )

        self.assertEqual(model.edge_type(selected), "line")
        self.assertEqual(model.vertices["v0"].y, -0.5)
        self.assertEqual(model.vertices["v1"].y, -0.5)
        self.assertEqual(result.edges, (selected,))
        self.assertEqual(result.projected_point_count, 2)
        model.validate()

    def test_point_list_edge_projection_moves_every_interpolation_point(
        self,
    ) -> None:
        for kind in ("polyLine", "spline"):
            with self.subTest(kind=kind):
                model = MeshModel()
                selected = edge_key("v0", "v1")
                model.set_edge_type(selected, kind)
                target = model.add_geometry_curve(
                    ((-1.0, -0.5), (2.0, -0.5))
                )

                result = model.project_to_geometry(
                    (target.id,), "y", edges=(selected,)
                )

                self.assertEqual(model.edge_type(selected), kind)
                self.assertEqual(model.edge_control_points(selected)[0][1], -0.5)
                self.assertEqual(result.projected_point_count, 3)
                model.validate()

    def test_arc_becomes_spline_when_projected_points_are_collinear(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        target = model.add_geometry_curve(((-1.0, -0.5), (2.0, -0.5)))

        result = model.project_to_geometry(
            (target.id,), "y", edges=(selected,)
        )

        self.assertEqual(model.edge_type(selected), "spline")
        self.assertEqual(model.edge_control_points(selected)[0][1], -0.5)
        self.assertEqual(result.converted_arcs, (selected,))
        model.validate()

    def test_invalid_projected_topology_is_rolled_back_atomically(self) -> None:
        model = MeshModel()
        target = model.add_geometry_curve(((1.0, -1.0), (1.0, 2.0)))
        before = {
            identifier: (vertex.x, vertex.y)
            for identifier, vertex in model.vertices.items()
        }

        with self.assertRaisesRegex(TopologyError, "coincident"):
            model.project_to_geometry(
                (target.id,), "x", vertex_ids=("v0",)
            )

        self.assertEqual(
            {
                identifier: (vertex.x, vertex.y)
                for identifier, vertex in model.vertices.items()
            },
            before,
        )
        model.validate()

    def test_vertices_and_edges_cannot_be_mixed_in_one_projection(self) -> None:
        model = MeshModel()
        target = model.add_geometry_curve(((0.0, -1.0), (1.0, -1.0)))

        with self.assertRaisesRegex(TopologyError, "not both"):
            model.project_to_geometry(
                (target.id,),
                "orthogonal",
                vertex_ids=("v0",),
                edges=(edge_key("v0", "v1"),),
            )

    def test_fit_replaces_selected_edge_with_spline(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        target = model.add_geometry_curve(
            ((0.0, -0.2), (0.5, -0.4), (1.0, -0.2))
        )

        result = model.project_to_geometry(
            (target.id,), "y", edges=(selected,), fit=True
        )

        self.assertEqual(model.edge_type(selected), "spline")
        self.assertEqual(model.edge_control_points(selected), ((0.5, -0.4),))
        self.assertEqual(result.fitted_edges, (selected,))
        self.assertEqual(result.fit_interpolation_point_count, 1)
        self.assertIsNotNone(result.max_fit_error)
        self.assertLessEqual(result.max_fit_error, result.fit_tolerance)
        model.validate()

    def test_fit_reports_when_point_cap_is_reached_before_tolerance(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        target = model.add_geometry_curve(
            ((-0.2, -0.2), (0.5, -0.4), (1.2, -0.2))
        )

        result = model.project_to_geometry(
            (target.id,),
            "y",
            edges=(selected,),
            fit=True,
            fit_relative_tolerance=1.0e-12,
            fit_max_points=5,
        )

        self.assertEqual(result.fit_interpolation_point_count, 5)
        self.assertFalse(result.fit_tolerance_met)
        self.assertGreater(result.max_fit_error, result.fit_tolerance)
        model.validate()

    def test_fit_is_not_available_for_vertex_projection(self) -> None:
        model = MeshModel()
        target = model.add_geometry_curve(((0.0, -0.2), (1.0, -0.2)))

        with self.assertRaisesRegex(TopologyError, "only.*edges"):
            model.project_to_geometry(
                (target.id,), "y", vertex_ids=("v0",), fit=True
            )

    def test_fit_to_different_endpoint_curves_is_atomic(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        first = model.add_geometry_curve(((-0.2, -0.2), (0.2, -0.2)))
        second = model.add_geometry_curve(((0.8, -0.2), (1.2, -0.2)))
        before = {
            identifier: (vertex.x, vertex.y)
            for identifier, vertex in model.vertices.items()
        }

        with self.assertRaisesRegex(TopologyError, "same reference curve"):
            model.project_to_geometry(
                (first.id, second.id),
                "orthogonal",
                edges=(selected,),
                fit=True,
            )

        self.assertEqual(model.edge_type(selected), "line")
        self.assertEqual(
            {
                identifier: (vertex.x, vertex.y)
                for identifier, vertex in model.vertices.items()
            },
            before,
        )
        model.validate()

    def test_fit_retains_common_curve_at_a_target_curve_junction(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        side_curve = model.add_geometry_curve(((-1.0, -0.2), (0.0, -0.2)))
        fitting_curve = model.add_geometry_curve(
            ((0.0, -0.2), (0.5, -0.4), (1.0, -0.2))
        )

        # The first endpoint is equally near both curves. Selecting the side
        # curve first must not discard the common fitting-curve location.
        result = model.project_to_geometry(
            (side_curve.id, fitting_curve.id),
            "y",
            edges=(selected,),
            fit=True,
        )

        self.assertEqual(result.fitted_edges, (selected,))
        self.assertEqual(model.edge_control_points(selected), ((0.5, -0.4),))
        model.validate()


if __name__ == "__main__":
    unittest.main()
