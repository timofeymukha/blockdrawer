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


def staggered_options(**overrides):
    settings = dict(
        farfield_shape="cshape", farfield_radius=15.0, farfield_center=(1.0, 0.0),
        layer_height=0.01, wake=wake.WakeOptions(enabled=True),
        hull=hull.HullOptions(enabled=True, height_ratio=20.0),
    )
    settings.update(overrides)
    return replace(CAVITY_FAST, **settings)


def staggered_near(key="staggered-near"):
    """The near field inside the hull as ``_try_hull`` builds it, run on its own:
    the annular construction with the hull as the single outer chain."""
    import math
    import planar_domain as pdm
    import sites as site_module
    if key in _RUNS:
        return _RUNS[key]
    names, loops = cases.staggered_foils()
    settings = staggered_options()
    outer, circle = pdm.fabricate_outer(loops, pipeline.farfield_spec(settings))
    domain = pdm.from_bodies(names, loops, outer=outer)
    sites, _scale = site_module.sites_from_domain(domain, circle=circle, frame=site_module.domain_frame(loops))
    metric = pipeline._metric(domain, settings)
    points, _height = pipeline.hull_outline(loops, settings, metric)
    points = hull.align_start(points, sites[-1].curve.point_at(0.0))
    near_settings = pipeline.hull_near_settings(settings, metric)
    _RUNS[key] = pipeline.run_external(names, loops, near_settings, outer=[("hull", "farfield", points)])
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
        self.assertEqual(record["hull_chains"], ["hull"])
        near = record["near"]
        self.assertTrue(near["admissible"])
        self.assertEqual({item["site"]: item["target"] for item in near["wakes"]}, {"front": "body", "rear": "outer"})
        boundaries = sorted(result.model.boundaries)
        self.assertEqual(boundaries, ["bottom", "cap", "front", "outlet", "rear", "top"])
        levels = {key[2] for key in result.graph.vertices if key[:2] == ("hull", "level")}
        self.assertEqual(levels, set(range(1, record["levels"] + 1)))
        # The exit wake's band widens towards the outlet at the spread slope.
        vertices = result.graph.vertices
        count = max(key[3] for key in vertices if key[:3] == ("hull", "level", 1))
        at_hull = np.linalg.norm(vertices[("hull", "level", 1, 0)].point - vertices[("hull", "level", 1, count)].point)
        at_outlet = np.linalg.norm(vertices[("hull", "exit", 1, "out")].point - vertices[("hull", "exit", 1, "in")].point)
        self.assertGreater(at_outlet, at_hull)

    def test_a_blunt_base_leaves_the_cluster_as_three_strips(self):
        """The rear foil's blunt base exits the hull as the base strip between
        the two wake lines with a wake band on each side, all running on to
        the outlet through every level."""
        names, loops = cases.tandem_blunt()
        result = run("tandem-blunt-hull", names, loops, tandem_options())
        record = result.hull
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(record["exit_kind"], "base")
        beyond = [face for face in result.graph.faces if face.provenance == "wake band beyond the hull"]
        self.assertEqual(len(beyond), 3)
        exits = sorted(key[3] for key in result.graph.vertices if key[:3] == ("hull", "exit", 0))
        self.assertEqual(exits, ["centre0", "centre1", "in", "out"])

    def test_a_wake_passes_over_a_staggered_rear_foil(self):
        """The multi-element wake: the front wake crosses the rear foil's cell
        as a chord with a strip on each side, the lens between the lens-side
        strip and the medial arc is three quads around a centre, the rear's
        band gets three spokes under the wake, and the rear's own wake leaves
        through the hull."""
        names, loops = cases.staggered_foils()
        result = run("staggered-hull", names, loops, staggered_options())
        record = result.hull
        near = record["near"]
        self.assertTrue(near["admissible"], near.get("problems") or near.get("failures") or near.get("errors"))
        self.assertEqual({item["site"]: (item["applied"], item["target"]) for item in near["wakes"]},
                         {"front": (True, "outer"), "rear": (True, "outer")})
        # Both wakes leave the cluster, and the hull far field cuts its
        # levels along one of them: each is tried as the cut with the other
        # wake's band ending at the hull, the front's first since it passes
        # over the rear.  Behind the rear the slit hull's first level gives a
        # non-convex sector, so that attempt yields, the cut along the rear's
        # wake builds with the front's band ending at the hull, both are
        # reported, and the near field keeps both wakes.
        attempts = record["exit_attempts"]
        self.assertEqual([item["kept"] for item in attempts], ["front", "rear"])
        self.assertIn("non-convex", attempts[0]["reason"])
        self.assertIsNone(attempts[1]["reason"])
        self.assertEqual(attempts[1]["ending"], ["front"])
        self.assertEqual(record["ending_wakes"], ["front"])
        self.assertNotIn("dropped_wakes", record)
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertEqual(record["exit_wake"], "rear")
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertIs(result.hull, record)
        # The front's exits are hull vertices carried outward as level spokes.
        exits = [key for key in result.graph.vertices if key[:2] == ("hull", "exit")]
        self.assertTrue(exits)
        self.assertEqual(sum(1 for face in result.graph.faces if face.provenance == "wake band beyond the hull"), 2)

    def test_the_wake_over_the_rear_foil_is_written_through_its_cell(self):
        """The near field inside the hull, built directly: the front record
        carries the passage and the lens, the graph the four strip blocks,
        the three lens blocks and the three spokes onto the rear's band."""
        result = staggered_near()
        self.assertTrue(result.admissible, result.problems or result.failures)
        wakes = {item["site"]: item for item in result.wakes.records}
        front = wakes["front"]
        self.assertTrue(front["applied"], front.get("reason"))
        self.assertEqual(front["through"][0]["site"], "rear")
        self.assertEqual(front["target"]["kind"], "outer")
        self.assertEqual(front["lens"]["blocks"], 3)
        self.assertEqual(front["exit_key"], front["anchor_keys"][1])
        self.assertTrue(wakes["rear"]["applied"], wakes["rear"].get("reason"))
        provenances = [face.provenance for face in result.graph.faces]
        self.assertEqual(provenances.count("lens beside the wake"), 3)
        self.assertEqual(provenances.count("wake band over the body"), 4)
        rear = int(front["through"][0]["cell"])
        feet = sorted(key[-1] for key in result.graph.vertices if key[:2] == ("wake", rear) and key[-2] == "gate")
        self.assertEqual(feet, ["f", "g", "x"])
        centre = ("wake", rear, *front["anchor_key"], "lens", "centre")
        self.assertIn(centre, result.graph.vertices)
        self.assertEqual(sum(centre in key for key in result.graph.edges), 3)

    def test_without_a_wake_the_hull_far_field_is_closed(self):
        """Without a wake the rear tail stays a seam and no wake leaves the
        cluster, so the far field is the closed one, mapped chain onto chain.
        (This test used to assert only keys that are always set.)"""
        names, loops = cases.tandem_foils()
        result = run("tandem-hull-nowake", names, loops, tandem_options(wake=wake.WakeOptions(enabled=False)))
        self.assertTrue(result.hull["applied"], result.hull.get("reason"))
        self.assertTrue(result.hull["closed"])
        self.assertTrue(result.admissible, result.problems or result.failures)

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
