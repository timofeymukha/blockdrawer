"""A wake separatrix continues the band past a trailing edge and cuts the core."""

from dataclasses import replace
import math
import unittest
from unittest import mock

import numpy as np

from test_agentic_topology import CAVITY_FAST, run_case
import geometry2d as g2
import patch_graph as pg
import pipeline
import session_emit
import synthetic_cases as cases
import wake

WAKE = replace(CAVITY_FAST, wake=wake.WakeOptions(enabled=True))
_RUNS: dict = {}


def teardrop_loop(angle: float = 0.0):
    return cases.teardrop((0.0, 0.0), 0.32, tip_ratio=2.2, angle=angle)


def run_teardrop(options=WAKE, *, outer=None, angle: float = 0.0, key=None):
    key = key or ("teardrop", repr(options), outer is not None, angle)
    if key not in _RUNS:
        _RUNS[key] = pipeline.run_external(["tear"], [teardrop_loop(angle)], options, outer=outer)
    return _RUNS[key]


def c_outer():
    return cases.c_shaped_outer(center=(0.0, 0.0), radius=2.0, downstream=4.0)


class WakeGeometryTests(unittest.TestCase):
    def test_ray_crossings_report_distance_and_arclength(self):
        square = np.asarray([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)])
        hits = wake._ray_crossings((1.0, 1.0), (1.0, 0.0), square)
        self.assertEqual(len(hits), 1)
        t, arclength, segment = hits[0]
        self.assertAlmostEqual(t, 1.0)
        self.assertAlmostEqual(arclength, 3.0)
        self.assertEqual(segment, 1)
        # A ray leaving a boundary vertex does not count that vertex.
        self.assertEqual(len(wake._ray_crossings((0.0, 0.0), (1.0, 1.0), square)), 1)

    def test_feature_directions_bisect_the_fluid_sector(self):
        loop = g2.orient_anticlockwise(g2.close_loop(teardrop_loop(), 0.0))
        found = wake.feature_directions(loop, 1.0, math.radians(250.0))
        self.assertEqual(len(found), 1)
        index, direction = found[0]
        self.assertTrue(np.allclose(loop[index], [0.32 * 2.2, 0.0]))
        self.assertTrue(np.allclose(direction, [1.0, 0.0], atol=1e-9))

    def test_options_reject_bad_parameters(self):
        for parameters in ({"fluid_angle": 90.0}, {"fluid_angle": float("nan")},
                           {"direction": (0.0, 0.0)}, {"clearance_ratio": -1.0}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                wake.WakeOptions(**parameters)


class WakeConstructionTests(unittest.TestCase):
    def test_teardrop_in_a_circle_gets_a_resolved_c_topology(self):
        result = run_teardrop()
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.resolved, result.described_failures())
        self.assertEqual(len(result.wakes.applied), 1)
        record = result.wakes.applied[0]
        self.assertEqual(record["exit_chain"], "farfield")
        graph = result.graph
        self.assertEqual(graph.total_index(), 0)
        self.assertEqual(graph.euler(), 0)
        roles = {edge.role for edge in graph.edges.values()}
        self.assertIn("wake", roles)
        self.assertIn("wake_front", roles)
        self.assertEqual(sum(face.role == "wake" for face in graph.faces), 4)
        # The trailing edge carries four blocks meeting at about right angles.
        key = tuple(("gate", record["cell"], *record["anchor_key"]))
        faces = [face for face in graph.faces if key in face.corners]
        self.assertEqual(len(faces), 4)
        for face in faces:
            points = graph.corner_points(face)
            angle = math.degrees(float(g2.quad_corner_angles(points)[face.corners.index(key)]))
            self.assertGreater(angle, 60.0)
            self.assertLess(angle, 120.0)
        singular = graph.singularities()
        self.assertEqual(sorted(item["index"] for item in singular), [-2, 1, 1])
        self.assertTrue(all(item["on_boundary"] for item in singular if item["index"] == -2))

    def test_wake_band_shares_the_wall_bands_normal_count(self):
        result = run_teardrop()
        graph = result.graph
        components = pg.constraint_components(graph)
        record = result.wakes.applied[0]
        gate = tuple(("gate", record["cell"], *record["anchor_key"]))
        spoke = next(
            key for key, edge in graph.edges.items()
            if edge.role == "layer_spoke" and gate in key
        )
        component = next(keys for keys in components if spoke in keys)
        roles = {graph.edges[key].role for key in component}
        self.assertIn("ring", roles, "the ring pieces across the wake band carry the band count")
        self.assertIn("wall", roles, "the outer boundary pieces across the wake band carry it too")
        self.assertNotIn("wake_front", roles)
        structure = pg.sizing_structure(graph)
        self.assertEqual(structure["wall_tangential_normal_couplings"], 0)
        self.assertEqual(structure["band_core_depth_couplings"], 0)

    def test_c_shaped_outer_slides_the_corner_anchors_clear_of_the_wake(self):
        result = run_teardrop(outer=c_outer())
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.resolved, result.described_failures())
        self.assertEqual(len(result.wakes.applied), 1)
        self.assertEqual(result.wakes.applied[0]["exit_chain"], "outlet")
        self.assertGreaterEqual(sum("slid" in note for note in result.wake_notes), 1)
        # The wake exit and both wake-front exits split the outlet into four pieces.
        outlet = [key for key, name in result.model.edge_boundaries.items() if name == "outlet"]
        self.assertEqual(len(outlet), 4)
        baseline = run_teardrop(CAVITY_FAST, outer=c_outer())
        self.assertGreater(result.graph.summary()["faces"], baseline.graph.summary()["faces"])

    def test_wake_into_another_body_is_refused_with_a_reason(self):
        result = run_case("sharp_bodies", WAKE)
        self.assertTrue(result.admissible)
        self.assertEqual(result.wakes.applied, [])
        record = next(item for item in result.wakes.records if item["site"] == "tip")
        self.assertIn("another body", record["reason"])
        baseline = run_case("sharp_bodies", CAVITY_FAST)
        self.assertEqual(result.graph.summary(), baseline.graph.summary())

    def test_blunt_base_is_refused_at_both_corners(self):
        """Two convex corners a hundredth of the perimeter apart are one base."""
        tear = cases.teardrop((0.0, 0.0), 0.32, tip_ratio=4.0)
        tip, arc = tear[-1], tear[:-1]
        half, depth = math.asin(0.25), 0.02
        blunt = np.vstack([arc, [[tip[0] - depth, -depth * math.tan(half)], [tip[0] - depth, depth * math.tan(half)]]])
        result = pipeline.run_external(["blunt"], [blunt], WAKE)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(len(result.wakes.records), 2)
        for record in result.wakes.records:
            self.assertFalse(record["planned"])
            self.assertIn("blunt trailing edge", record["reason"])
        sharp = pipeline.run_external(["sharp"], [tear], WAKE)
        self.assertEqual(len(sharp.wakes.applied), 1)
        self.assertTrue(sharp.resolved, sharp.described_failures())

    def test_direction_outside_the_sector_is_refused(self):
        result = run_teardrop(
            replace(WAKE, wake=wake.WakeOptions(enabled=True, direction=(-1.0, 0.0))),
            key="teardrop-backwards",
        )
        record = result.wakes.records[0]
        self.assertFalse(record["planned"])
        self.assertIn("fluid sector", record["reason"])
        self.assertEqual(result.graph.summary(), run_teardrop(CAVITY_FAST, key="teardrop-plain").graph.summary())

    def test_rejected_wake_leaves_the_annular_result_untouched(self):
        baseline = run_teardrop(CAVITY_FAST, key="teardrop-plain")
        signature = session_emit.topology_signature(baseline.model)
        broken = pg.GraphError("deliberately broken")

        def failing(*args, **kwargs):
            raise broken

        with mock.patch.object(wake, "_write_wake", side_effect=failing):
            result = pipeline.run_external(["tear"], [teardrop_loop()], WAKE)
        self.assertTrue(result.admissible)
        self.assertEqual(result.wakes.applied, [])
        self.assertIn("deliberately broken", result.wakes.records[0]["reason"])
        self.assertEqual(session_emit.topology_signature(result.model), signature)
        self.assertEqual(result.graph.summary(), baseline.graph.summary())

    def test_wake_follows_a_rotated_and_scaled_body(self):
        """The wake direction and the trailing-edge structure are covariant.

        The annular layout of a lone teardrop is itself raster dependent
        under a rotation (the fixtures with invariance tests are the CASES
        corpus), so the block count is not compared - the wake is.
        """
        angle = 0.9
        rotation = np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        moved = 40.0 * teardrop_loop() @ rotation.T + [3.0, -7.0]
        other = pipeline.run_external(["tear"], [moved], WAKE)
        self.assertTrue(other.resolved, other.described_failures())
        self.assertEqual(len(other.wakes.applied), 1)
        record = other.wakes.applied[0]
        self.assertTrue(np.allclose(record["direction"], rotation @ [1.0, 0.0], atol=1e-9))
        key = tuple(("gate", record["cell"], *record["anchor_key"]))
        self.assertEqual(sum(key in face.corners for face in other.graph.faces), 4)
        self.assertEqual(sum(face.role == "wake" for face in other.graph.faces), 4)
        self.assertEqual(sorted(item["index"] for item in other.graph.singularities()), [-2, 1, 1])
        self.assertAlmostEqual(record["band_height_at_edge"] / 40.0, run_teardrop().wakes.applied[0]["band_height_at_edge"], delta=2e-3)

    def test_session_round_trips_with_the_wake(self):
        import tempfile
        from pathlib import Path
        from blockdrawer.session import load_session

        result = run_teardrop(outer=c_outer())
        with tempfile.TemporaryDirectory() as folder:
            path = session_emit.write_session(result.model, Path(folder) / "wake.json")
            reloaded = load_session(path)
            reloaded.validate()
            self.assertEqual(
                session_emit.topology_signature(reloaded),
                session_emit.topology_signature(result.model),
            )


if __name__ == "__main__":
    unittest.main()
