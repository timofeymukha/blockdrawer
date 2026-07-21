import unittest

from blockdrawer.model import MeshModel, edge_key
from blockdrawer.render_cache import (
    RenderPathCache,
    bounds_intersect,
    point_in_bounds,
    points_bounds,
)


class RenderPathCacheTests(unittest.TestCase):
    def test_edge_path_is_reused_until_defining_geometry_changes(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        cache = RenderPathCache()
        original = model.edge_render_points
        calls: list[tuple[str, str]] = []

        def counted(edge, **options):
            calls.append(edge)
            return original(edge, **options)

        model.edge_render_points = counted  # type: ignore[method-assign]
        first = cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )
        second = cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )
        model.add_boundary("wall")
        third = cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )

        self.assertEqual(len(calls), 1)
        self.assertIs(first, second)
        self.assertIs(first, third)

        model.move_vertex("v1", 1.2, 0.0)
        moved = cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )

        self.assertEqual(len(calls), 2)
        self.assertIsNot(first, moved)
        self.assertEqual(moved.bounds, (0.0, 0.0, 1.2, 0.0))

    def test_curved_edge_sampling_options_are_part_of_cache_key(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        model.set_edge_type(selected, "arc")
        cache = RenderPathCache()

        coarse = cache.edge_path(
            model, selected, arc_segments=8, spline_samples_per_span=4
        )
        fine = cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )

        self.assertEqual(len(coarse.points), 9)
        self.assertEqual(len(fine.points), 65)
        self.assertIsNot(coarse, fine)

    def test_geometry_path_ignores_name_and_visibility_but_tracks_points(
        self,
    ) -> None:
        model = MeshModel()
        curve = model.add_geometry_curve(
            ((0.0, 0.0), (0.5, 0.25), (1.0, 0.0)), name="guide"
        )
        cache = RenderPathCache()
        original = model.geometry_curve_render_points
        calls: list[str] = []

        def counted(curve_id, **options):
            calls.append(curve_id)
            return original(curve_id, **options)

        model.geometry_curve_render_points = counted  # type: ignore[method-assign]
        first = cache.geometry_path(model, curve.id, samples_per_span=4)
        model.set_geometry_curve_name(curve.id, "renamed")
        model.set_geometry_curve_point_visibility(curve.id, False)
        unchanged = cache.geometry_path(model, curve.id, samples_per_span=4)

        self.assertEqual(calls, [curve.id])
        self.assertIs(first, unchanged)

        model.set_geometry_curve_point(curve.id, 1, 0.5, 0.5)
        changed = cache.geometry_path(model, curve.id, samples_per_span=4)
        self.assertEqual(calls, [curve.id, curve.id])
        self.assertIsNot(first, changed)

    def test_prune_discards_removed_entity_entries(self) -> None:
        model = MeshModel()
        selected = edge_key("v0", "v1")
        curve = model.add_geometry_curve(((0.0, 0.0), (1.0, 0.0)))
        cache = RenderPathCache()
        cache.edge_path(
            model, selected, arc_segments=64, spline_samples_per_span=4
        )
        cache.geometry_path(model, curve.id, samples_per_span=4)

        cache.prune((), ())

        self.assertEqual(cache._edge_paths, {})
        self.assertEqual(cache._geometry_paths, {})

    def test_bounds_helpers_are_inclusive_and_reject_separated_boxes(self) -> None:
        bounds = points_bounds(((2.0, -1.0), (-3.0, 4.0), (0.0, 2.0)))

        self.assertEqual(bounds, (-3.0, -1.0, 2.0, 4.0))
        self.assertTrue(bounds_intersect(bounds, (2.0, 4.0, 3.0, 5.0)))
        self.assertFalse(bounds_intersect(bounds, (2.1, 4.1, 3.0, 5.0)))
        self.assertTrue(point_in_bounds((-3.0, 4.0), bounds))
        self.assertFalse(point_in_bounds((2.1, 0.0), bounds))


if __name__ == "__main__":
    unittest.main()
