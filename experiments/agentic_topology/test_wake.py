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

    def test_wake_into_another_body_lands_on_its_band(self):
        """A wake that crosses the ring into another body's cell ends on that
        body's band: the stagnation gate is pinned where the wake ray meets
        the wall, and the two wake fronts become band spokes beside it."""
        result = run_case("sharp_bodies", WAKE)
        self.assertTrue(result.admissible, result.problems or result.failures)
        record = next(item for item in result.wakes.records if item["site"] == "tip")
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertEqual(record["target"]["kind"], "body")
        self.assertEqual(record["target"]["site"], "disk")
        landing = record["landing_stations"]
        total = result.domain.holes[1].length
        self.assertLess(0.0, (landing["gate"] - landing["in"]) % total)
        self.assertLess((landing["gate"] - landing["in"]) % total, (landing["out"] - landing["in"]) % total)
        graph = result.graph
        wake_edges = [edge for edge in graph.edges.values() if edge.role == "wake"]
        self.assertEqual(len(wake_edges), 2)
        landings = [key for key in graph.vertices if key[0] == "wake" and key[1] == record["target"]["cell"]]
        self.assertEqual(len(landings), 4)
        baseline = run_case("sharp_bodies", CAVITY_FAST)
        self.assertNotEqual(result.graph.summary(), baseline.graph.summary())

    def test_a_wake_over_another_body_is_planned_through_its_cell(self):
        """With the far field many chords away the rear foil's far ring lies
        beyond its tail, so the plan sees the passage but the trial refuses
        it at the rear's sharp tail; the record still says what the wake
        would do."""
        names, loops = cases.staggered_foils()
        result = run_case("staggered_foils", CAVITY_FAST)
        records = wake.plan(result.layout, wake.WakeOptions(enabled=True))
        front = next(item for item in records if item["site"] == "front")
        self.assertTrue(front["planned"], front.get("reason"))
        self.assertEqual(len(front["through"]), 1)
        passage = front["through"][0]
        self.assertEqual(passage["site"], "rear")
        self.assertEqual(front["target"]["kind"], "outer")
        self.assertGreater(passage["length"], 0.0)
        self.assertNotEqual(passage["ring"]["branch"], front["ring_branch"])
        self.assertGreater(front["wake_length"], passage["length"])

    def test_tandem_foils_get_both_wakes(self):
        """The rung between the single airfoil and 30P30N: the front foil's
        wake ends on the rear foil's band, the rear foil's wake leaves for the
        far field, and the two are accepted in either order."""
        names, loops = cases.tandem_foils()
        key = ("tandem", repr(WAKE))
        if key not in _RUNS:
            _RUNS[key] = pipeline.run_external(names, loops, WAKE)
        result = _RUNS[key]
        self.assertTrue(result.admissible, result.problems or result.failures)
        by_site = {item["site"]: item for item in result.wakes.records}
        self.assertTrue(by_site["front"]["applied"], by_site["front"].get("reason"))
        self.assertTrue(by_site["rear"]["applied"], by_site["rear"].get("reason"))
        self.assertEqual(by_site["front"]["target"]["kind"], "body")
        self.assertEqual(by_site["front"]["target"]["site"], "rear")
        self.assertEqual(by_site["rear"]["target"]["kind"], "outer")
        # The front wake lands at the rear nose, on the axis.
        point = by_site["front"]["target"]["point"]
        self.assertAlmostEqual(point[1], 0.0, places=6)
        self.assertLess(point[0], 0.45)
        wake_faces = [face for face in result.graph.faces if face.role == "wake"]
        self.assertEqual(len(wake_faces), 8)

    def test_blunt_base_continues_as_the_wake_core_strip(self):
        """Two convex corners three thousandths of the perimeter apart are one base,
        and the base template writes it: one record for the pair, two pinned
        wake anchors, the base going on downstream as the core strip with the
        two flank bands beside it."""
        tear = cases.teardrop((0.0, 0.0), 0.32, tip_ratio=4.0)
        tip, arc = tear[-1], tear[:-1]
        half, depth = math.asin(0.25), 0.02
        blunt = np.vstack([arc, [[tip[0] - depth, -depth * math.tan(half)], [tip[0] - depth, depth * math.tan(half)]]])
        result = pipeline.run_external(["blunt"], [blunt], WAKE)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(len(result.wakes.records), 1)
        record = result.wakes.records[0]
        self.assertEqual(record["kind"], "base")
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertEqual(len(record["anchor_keys"]), 2)
        self.assertAlmostEqual(record["base_width"], 2.0 * depth * math.tan(half), places=6)
        self.assertTrue(result.resolved, result.described_failures())
        strips = sorted(face.provenance for face in result.graph.faces if face.role == "wake")
        self.assertEqual(strips, ["base strip", "base strip beyond the ring", "wake band", "wake band", "wake band beyond the ring", "wake band beyond the ring"])
        kinds = [cut.anchor.kind for cut in result.layout.cuts[0]]
        self.assertEqual(kinds.count("wake"), 2)
        sharp = pipeline.run_external(["sharp"], [tear], WAKE)
        self.assertEqual(len(sharp.wakes.applied), 1)
        self.assertTrue(sharp.resolved, sharp.described_failures())

    def test_a_base_just_wider_than_the_rule_is_refused_not_cut_from_one_corner(self):
        """Beyond the base rule the two corners used to be planned as two
        trailing edges: one wake written, one refused, a lopsided C-cut from
        one corner.  The same base with the rule set just below its width
        (0.29 percent of the perimeter against 0.2) is refused at both
        corners with the remedy."""
        tear = cases.teardrop((0.0, 0.0), 0.32, tip_ratio=4.0)
        tip, arc = tear[-1], tear[:-1]
        half, depth = math.asin(0.25), 0.02
        blunt = np.vstack([arc, [[tip[0] - depth, -depth * math.tan(half)], [tip[0] - depth, depth * math.tan(half)]]])
        options = replace(WAKE, wake=wake.WakeOptions(enabled=True, blunt_ratio=0.002))
        result = pipeline.run_external(["blunt"], [blunt], options)
        self.assertEqual(len(result.wakes.records), 2)
        for record in result.wakes.records:
            self.assertFalse(record["applied"])
            self.assertIn("wider than the base rule", record["reason"])
            self.assertIn("--wake-blunt-ratio", record["reason"])

    def test_the_clearance_option_reaches_the_writers(self):
        result = run_teardrop(replace(WAKE, wake=wake.WakeOptions(enabled=True, clearance_ratio=0.4)))
        record = result.wakes.records[0]
        self.assertEqual(record["clearance_ratio"], 0.4)
        self.assertEqual(wake.record_clearance(record), 0.4)
        self.assertEqual(wake.record_clearance(run_teardrop().wakes.records[0]), wake.WakeOptions.clearance_ratio)

    def test_blunt_foil_o_grid(self):
        """The single-foil O-grid with a blunt trailing edge: the base strip
        carries the base as its wall edge, so the base's boundary layer is
        the strip's own grading, and the wake lines are the base's normal."""
        names, loops = cases.blunt_foil()
        result = pipeline.run_external(names, loops, replace(WAKE, layer_height=0.05))
        self.assertTrue(result.admissible, result.problems or result.failures)
        record = result.wakes.records[0]
        self.assertEqual(record["kind"], "base")
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(np.allclose(record["direction"], [1.0, 0.0], atol=1e-9))
        strip = next(face for face in result.graph.faces if face.provenance == "base strip")
        gates = [key for key in strip.corners if key[0] == "gate"]
        self.assertEqual(len(gates), 2)
        wall = result.graph.edges[result.graph.edge_key(*gates)]
        self.assertEqual(wall.role, "wall")
        self.assertAlmostEqual(wall.length, 0.02, delta=1e-6)

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

    def test_exported_splines_stay_on_the_validated_wall(self):
        """With spline walls the graph validates the polyline through the
        points; an exported spline that overshoots it (the teardrop's flank,
        a long span beside dense arc points) is exported as that polyline."""
        options = replace(WAKE, wall_edge_style="spline")
        result = run_teardrop(options, key="teardrop-spline")
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.export_notes, "the flank's spline should have been replaced")
        inverse = {value: key for key, value in result.identifiers.items()}
        for current, geometry in result.model.edge_geometry.items():
            if geometry.kind != "spline":
                continue
            key = result.graph.edge_key(inverse[current[0]], inverse[current[1]])
            departure, loops = session_emit._spline_departure(result.model, current, result.graph.edges[key].path)
            start = result.model.vertices[current[0]]
            end = result.model.vertices[current[1]]
            chord = math.dist((start.x, start.y), (end.x, end.y))
            with self.subTest(edge=current):
                self.assertFalse(loops)
                self.assertLessEqual(departure, session_emit.SPLINE_TOLERANCE * chord)

    def test_a_programming_error_in_a_trial_is_a_crash_not_a_refusal(self):
        """A defect inside a trial must not read as "the wake does not apply"."""

        def broken(*args, **kwargs):
            raise TypeError("deliberate defect")

        with mock.patch.object(wake, "make_room", side_effect=broken):
            result = pipeline.run_external(["tear"], [teardrop_loop()], WAKE)
        self.assertEqual(result.wakes.applied, [])
        reason = result.wakes.records[0]["reason"]
        self.assertIn("crashed", reason)
        self.assertIn("TypeError", reason)
        self.assertEqual([item["stage"] for item in result.crashes], ["wake"])
        self.assertEqual(result.analysis["trial_crashes"][0]["stage"], "wake")
        # The construction it fell back to is still the valid annular one.
        self.assertTrue(result.admissible)

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


class WakeExclusionTests(unittest.TestCase):
    def test_an_excluded_body_gets_no_wake_and_says_why(self):
        result = run_teardrop()
        records = wake.plan(result.layout, replace(WAKE.wake, exclude=("tear",)))
        self.assertTrue(records)
        for record in records:
            self.assertFalse(record["planned"])
            self.assertIn("excluded", record["reason"])
        planned = wake.plan(result.layout, WAKE.wake)
        self.assertTrue(any(record["planned"] for record in planned))


class WakeSpreadTests(unittest.TestCase):
    def test_lines_beside_the_axis_turn_away_from_it(self):
        direction = np.array([1.0, 0.0])
        axis = np.array([2.0, 0.5])
        on_axis = wake.spread_direction(direction, axis, axis, 0.05)
        self.assertTrue(np.allclose(on_axis, direction))
        above = wake.spread_direction(direction, axis + [0.0, 0.01], axis, 0.05)
        below = wake.spread_direction(direction, axis - [0.0, 0.01], axis, 0.05)
        self.assertGreater(above[1], 0.0)
        self.assertLess(below[1], 0.0)
        self.assertAlmostEqual(above[1] / above[0], 0.05)
        self.assertAlmostEqual(float(np.linalg.norm(above)), 1.0)
        parallel = wake.spread_direction(direction, axis + [0.0, 0.01], axis, 0.0)
        self.assertTrue(np.allclose(parallel, direction))


if __name__ == "__main__":
    unittest.main()
