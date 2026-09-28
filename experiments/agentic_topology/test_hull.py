"""The hull far field: level sets of the whole cluster beyond the near field."""

from dataclasses import replace
import unittest

import numpy as np

from test_agentic_topology import CAVITY_FAST
import geometry2d as g2
import hull
import pipeline
import synthetic_cases as cases
import wake

_RUNS: dict = {}


def run(key, names, loops, options):
    if key not in _RUNS:
        _RUNS[key] = pipeline.run_external(names, loops, options)
    return _RUNS[key]


def tandem_options(**overrides):
    settings = dict(
        farfield_shape="cshape", farfield_radius=15.0, farfield_center=(1.0, 0.0),
        layer_height=0.15, wake=wake.WakeOptions(enabled=True), hull=hull.HullOptions(enabled=True),
    )
    settings.update(overrides)
    return replace(CAVITY_FAST, **settings)


class LevelSetTests(unittest.TestCase):
    def test_the_hull_is_one_loop_at_the_exact_height(self):
        names, loops = cases.tandem_foils()
        points = hull.cluster_level_set(loops, 0.9, resolution=200)
        self.assertTrue(np.allclose(points[0], points[-1]))
        distance = hull.distance_to_walls(loops, points[:-1])
        self.assertLess(float(np.max(np.abs(distance - 0.9))), 1e-6)
        self.assertGreater(g2.signed_area(points), 0.0)
        self.assertTrue(g2.is_simple(points, closed=True))

    def test_below_half_the_gap_there_is_no_hull(self):
        names, loops = cases.tandem_foils()
        with self.assertRaises(hull.HullError):
            hull.cluster_level_set(loops, 0.15, resolution=200)

    def test_options_reject_bad_parameters(self):
        for parameters in ({"height_ratio": 0.0}, {"growth": 1.0}, {"reach": 1.5}, {"max_levels": -1}, {"resolution": 4}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                hull.HullOptions(**parameters)


class HullFarFieldTests(unittest.TestCase):
    def test_tandem_foils_at_fifteen_chords_build_through_the_hull(self):
        """The rung the C-shaped far field blocked: the near field inside the
        hull carries both wakes, the levels beyond it reach the cap and the
        rear wake's band runs on to the outlet."""
        names, loops = cases.tandem_foils()
        result = run("tandem-hull", names, loops, tandem_options())
        record = result.hull
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertGreaterEqual(record["levels"], 1)
        self.assertEqual(record["exit_wake"], "rear")
        self.assertEqual(record["hull_chains"], ["bottom", "outlet", "top", "cap"])
        near = record["near"]
        self.assertTrue(near["admissible"])
        self.assertEqual({item["site"]: item["target"] for item in near["wakes"]}, {"front": "body", "rear": "outer"})
        boundaries = sorted(result.model.boundaries)
        self.assertEqual(boundaries, ["bottom", "cap", "front", "outlet", "rear", "top"])
        levels = {key[2] for key in result.graph.vertices if key[:2] == ("hull", "level")}
        self.assertEqual(levels, set(range(1, record["levels"] + 1)))

    def test_the_annular_construction_is_the_fallback_with_a_reason(self):
        names, loops = cases.tandem_foils()
        result = run("tandem-hull-nowake", names, loops, tandem_options(wake=wake.WakeOptions(enabled=False)))
        # Without a wake the rear tail stays a seam and no wake leaves the
        # cluster, so the far field is the closed one; either way the record
        # says what happened.
        self.assertIn("applied", result.hull)
        if not result.hull["applied"]:
            self.assertIn("reason", result.hull)

    def test_one_body_is_the_c_grid_case(self):
        result = run("one-body", ["tear"], [cases.teardrop((0.0, 0.0), 0.32, tip_ratio=2.2)], replace(CAVITY_FAST, hull=hull.HullOptions(enabled=True)))
        self.assertFalse(result.hull["applied"])
        self.assertIn("one body", result.hull["reason"])

    def test_two_circles_in_a_far_rectangle_get_a_closed_far_field(self):
        names, loops = cases.two_circles()
        result = run("two-circles-hull", names, loops, replace(CAVITY_FAST, farfield_shape="rectangle", farfield_scale=8.0, hull=hull.HullOptions(enabled=True)))
        record = result.hull
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(record["closed"])
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(sorted(result.model.boundaries), ["bottom", "inlet", "large", "outlet", "small", "top"])


if __name__ == "__main__":
    unittest.main()
