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

    def test_a_grazing_wake_is_aimed_at_the_downstream_band(self):
        """The rear foil dropped a fifth of a chord under the front foil's
        wake: along the wake direction the wake would meet the rear's band
        at eight degrees on its upper shoulder, a grazing arrival whose
        strips beyond the ring are slivers at the landing, so the plan aims
        the far leg at the band - the first direction, a degree at a time,
        whose estimated incidence passes - and the writer measures the real
        one."""
        names, loops = cases.tandem_foils(drop=0.2)
        result = pipeline.run_external(names, loops, WAKE)
        self.assertTrue(result.admissible, result.problems or result.failures)
        by_site = {item["site"]: item for item in result.wakes.records}
        front = by_site["front"]
        self.assertTrue(front["applied"], front.get("reason"))
        self.assertEqual(front["target"]["kind"], "body")
        rear = result.diagram.sites[front["target"]["site_index"]]
        straight = wake._landing_estimate(front["ring_point"], front["direction"], rear, result.metric.layer_height)
        self.assertLess(straight[0], wake.LANDING_INCIDENCE, "along the wake direction the landing grazes")
        aim = front["landing"]
        self.assertLess(aim["turn_degrees"], 0.0, "the far leg turns towards the rear foil")
        self.assertGreaterEqual(aim["estimated_incidence_degrees"], math.degrees(wake.LANDING_INCIDENCE + wake.LANDING_AIM_MARGIN))
        self.assertGreaterEqual(front["landing_incidence_degrees"], math.degrees(wake.LANDING_INCIDENCE))
        self.assertLess(front["target"]["point"][1], 0.0, "the gate sits below the axis, on the shoulder")
        points, leg = wake.far_path(front, front["ring_point"])
        self.assertEqual(len(points), 1)
        self.assertTrue(np.allclose(leg, aim["leg"]))
        # The rear foil's own wake and the tandem's eight wake faces are as before.
        self.assertTrue(by_site["rear"]["applied"], by_site["rear"].get("reason"))
        self.assertEqual(sum(1 for face in result.graph.faces if face.role == "wake"), 8)

    def test_pinning_a_wake_keeps_the_relaxed_gates(self):
        """Rebuilding the cuts from the anchors puts every gate back at its
        hint or projection; the wake stage remembers the relaxed stations and
        restores them, so the gates the wake does not touch stay where the
        layout's relaxation put them."""
        import copy
        import block_layout
        import patch_solver

        result = run_teardrop()
        layout = block_layout.build_layout(result.diagram, result.options.layout)
        patch_solver.solve(layout, result.options.solver)
        projected = {(cell, cut.anchor.key): cut.projected_station for cell, cuts in enumerate(layout.cuts) for cut in cuts}
        relaxed = {(cell, cut.anchor.key): cut.wall_station for cell, cuts in enumerate(layout.cuts) for cut in cuts}
        moved = [key for key in relaxed if abs(relaxed[key] - projected[key]) > 1e-9]
        self.assertTrue(moved, "the relaxation moves at least one gate on the teardrop")
        trial = copy.deepcopy(layout)
        records = wake.plan(trial, WAKE.wake, height=result.metric.layer_height)
        wake.prepare(trial, records)
        after = {(cell, cut.anchor.key): cut.wall_station for cell, cuts in enumerate(trial.cuts) for cut in cuts}
        kept = [key for key in moved if key in after]
        self.assertTrue(kept)
        for key in kept:
            self.assertAlmostEqual(after[key], relaxed[key], places=12, msg=str(key))

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


class WakeTurnTests(unittest.TestCase):
    """A wake whose edge bisector is off the wake direction turns in stations."""

    @staticmethod
    def blunt_loop():
        tear = cases.teardrop((0.0, 0.0), 0.32, tip_ratio=4.0)
        tip, arc = tear[-1], tear[:-1]
        half, depth = math.asin(0.25), 0.02
        return np.vstack([arc, [[tip[0] - depth, -depth * math.tan(half)], [tip[0] - depth, depth * math.tan(half)]]])

    @staticmethod
    def options(degrees: float, turn: float = wake.WakeOptions.turn):
        direction = (math.cos(math.radians(degrees)), math.sin(math.radians(degrees)))
        return replace(WAKE, wake=wake.WakeOptions(enabled=True, direction=direction, turn=turn))

    def test_line_path_mitres_the_offset_lines(self):
        """A line beside the axis keeps its lateral distance through a turn:
        the station point of a line offset by d lies d / cos(turn / 2) along
        the mitre, and a line with its own first leg meets the first rung
        where it does."""
        record = {"point": [0.0, 0.0], "direction": [0.0, 1.0], "legs": [[1.0, 0.0], [0.0, 1.0]], "stations": [[1.0, 0.0]], "turned": True}
        path, leg = wake.line_path(record, [0.0, -0.5])
        self.assertTrue(np.allclose(path[1], [1.5, -0.5]))
        self.assertTrue(np.allclose(leg, [0.0, 1.0]))
        path, leg = wake.line_path(record, [0.0, 0.5], leaving=[1.0, 0.5])
        self.assertAlmostEqual(path[1][0] - 1.0, -path[1][1], places=12, msg="the point lies on the rung through the station")
        self.assertTrue(np.allclose(path[1], [1.0 / 3.0, 2.0 / 3.0]))
        straight = {"point": [0.0, 0.0], "direction": [1.0, 0.0], "legs": [[1.0, 0.0]], "stations": [], "turned": False}
        path, leg = wake.line_path(straight, [0.0, 0.5])
        self.assertEqual(len(path), 1)
        with self.assertRaises(pg.GraphError):
            wake.line_path(record, [0.0, 0.5], leaving=[-1.0, 0.0])

    def test_a_wake_off_its_bisector_turns_in_a_station(self):
        """The teardrop's bisector is (1, 0); a wake asked for 30 degrees
        above it leaves along the bisector, turns at one station and runs
        on along the requested direction: the band's strips are split at
        the station and the station is carried round the cell as a level,
        so every patch stays a convex quad and the result resolves."""
        result = pipeline.run_external(["tear"], [teardrop_loop()], self.options(30.0))
        self.assertTrue(result.admissible, result.problems or result.failures)
        record = result.wakes.records[0]
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(record["turned"])
        self.assertAlmostEqual(record["turn_degrees"], 30.0, places=6)
        self.assertEqual(len(record["stations"]), 1)
        self.assertTrue(np.allclose(record["legs"][0], [1.0, 0.0]))
        self.assertTrue(np.allclose(record["legs"][-1], record["direction"]))
        self.assertEqual(record["turn_levels"]["stations"], 1)
        self.assertTrue(result.resolved, result.described_failures())
        roles = {}
        for edge in result.graph.edges.values():
            roles[edge.role] = roles.get(edge.role, 0) + 1
        self.assertEqual(roles["wake_rung"], 2, "one rung across each strip at the station")
        self.assertEqual(roles["core_rung"], record["turn_levels"]["patches"])
        self.assertEqual(sum(1 for face in result.graph.faces if face.role == "wake"), 6)
        # Two stations when the turn per station is halved; they are spaced
        # by the band height, so a band as thick as the clearance allows
        # fits one station only and the thinner band is asked for.
        thick = pipeline.run_external(["tear"], [teardrop_loop()], self.options(30.0, turn=16.0))
        self.assertIn("do not fit", thick.wakes.records[0]["reason"])
        halved = pipeline.run_external(["tear"], [teardrop_loop()], replace(self.options(30.0, turn=16.0), layer_height=0.05))
        record = halved.wakes.records[0]
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertEqual(len(record["stations"]), 2)
        self.assertTrue(halved.resolved, halved.described_failures())

    def test_turning_off_keeps_the_wake_straight(self):
        """With the turn disabled the same request builds the straight wake
        of before (or refuses it), and a direction within the margin of the
        bisector never turns."""
        result = pipeline.run_external(["tear"], [teardrop_loop()], self.options(30.0, turn=0.0))
        record = result.wakes.records[0]
        self.assertFalse(record["turned"])
        self.assertEqual(record["stations"], [])
        self.assertTrue(np.allclose(record["legs"][0], record["direction"]))
        near = pipeline.run_external(["tear"], [teardrop_loop()], self.options(5.0))
        record = near.wakes.records[0]
        self.assertFalse(record["turned"])
        self.assertTrue(record["applied"], record.get("reason"))

    def test_a_chord_beside_a_fan_is_measured_against_the_leg(self):
        """On a ring that is a fan round a corner, the chord from the wake's
        crossing to the neighbouring cut is nearly along a leg crossing the
        fan obliquely and square to one crossing it radially."""
        angles = np.linspace(0.0, 2.0 * math.pi, 361)
        loop = np.column_stack([np.cos(angles), np.sin(angles)])
        cuts = [(0.0, [1.0, 0.0], "turning", ("anchor", 1)), (0.75 * math.pi, [math.cos(0.75 * math.pi), math.sin(0.75 * math.pi)], "turning", ("anchor", 2)),
                (1.25 * math.pi, [math.cos(1.25 * math.pi), math.sin(1.25 * math.pi)], "corner", ("anchor", 3))]
        crossing = [math.cos(-0.25 * math.pi), math.sin(-0.25 * math.pi)]
        oblique, what = wake._chord_angle(loop, crossing, [1.0, 0.0], cuts, frozenset())
        self.assertLess(oblique, wake.RING_CHORD_ANGLE)
        self.assertIn("corner cut before", what)
        radial, _what = wake._chord_angle(loop, crossing, crossing, cuts, frozenset())
        self.assertAlmostEqual(math.degrees(radial), 45.0, places=6)
        # The wake's own cut is skipped.
        skipped, what = wake._chord_angle(loop, crossing, [1.0, 0.0], cuts, frozenset({("anchor", 3)}))
        self.assertIn("turning cut", what)
        self.assertGreater(skipped, oblique)

    def test_the_placement_ladder_on_the_teardrop_layout(self):
        """The planner's ladder on the teardrop's layout: the stations sit
        inside the ring when they fit; when they do not (two of them spaced
        by a band as thick as the clearance allows) and nothing lies beyond
        the ring the wake is refused with both reasons, and a wake ending on
        another body runs straight along its edge direction to it."""
        # The layout before any wake: the trailing edge still carries its
        # corner anchor, which a wake of its own would replace.
        result = pipeline.run_external(["tear"], [teardrop_loop()], replace(CAVITY_FAST, wake=wake.WakeOptions(enabled=False)))
        layout = result.layout
        record = wake.plan(layout, replace(self.options(30.0).wake, enabled=True))[0]
        cell_index = record["cell"]
        owned = wake.owned_anchor_keys(layout, cell_index, record["wall_station"], record["wall_station"])
        self.assertEqual(len(owned), 1)
        self.assertEqual(next(cut.anchor.kind for cut in layout.cuts[cell_index] if cut.anchor.key in owned), "corner")
        outer = layout.diagram.sites[-1].curve.loop()
        settings = wake.WakeOptions(enabled=True, turn=50.0)
        inside, why = wake.plan_turns(layout, cell_index, record["point"], [1.0, 0.0], record["direction"], settings, 0.05, owned_keys=owned, outer_loop=outer)
        self.assertEqual(inside["turn_placement"], "inside", why)
        self.assertEqual(len(inside["stations"]), 1)
        small = wake.WakeOptions(enabled=True, turn=16.0)
        refused, why = wake.plan_turns(layout, cell_index, record["point"], [1.0, 0.0], record["direction"], small, 10.0, owned_keys=owned, outer_loop=None)
        self.assertIsNone(refused)
        self.assertIn("do not fit", why)
        self.assertIn("cannot go beyond the ring", why)
        to_body, why = wake.plan_turns(layout, cell_index, record["point"], [1.0, 0.0], record["direction"], small, 10.0, owned_keys=owned, outer_loop=outer, body_target=True)
        self.assertEqual(to_body["turn_placement"], "straight", why)
        self.assertTrue(to_body["turned"])
        self.assertEqual(to_body["stations"], [])
        self.assertTrue(np.allclose(to_body["legs"], [[1.0, 0.0]]))
        self.assertIn("to the body it ends on", to_body["turn_note"])
        straight = {"point": record["point"], "axis_point": record["point"], "direction": record["direction"], **to_body}
        path, leg = wake.line_path(straight, record["point"])
        self.assertEqual(len(path), 1)
        self.assertTrue(np.allclose(leg, [1.0, 0.0]))
        far, leg = wake.far_path(straight, [2.0, 0.0])
        self.assertEqual(len(far), 1)
        self.assertTrue(np.allclose(leg, [1.0, 0.0]))

    def test_a_wake_turns_beyond_the_ring_when_the_ring_bends_at_its_crossing(self):
        """With the ring judged to bend beside the crossing along the wake
        direction (the chord measure patched to say so for the body's ring),
        the teardrop's wake crosses its ring straight along the bisector and
        turns beyond it: the strips beyond the ring are split at the station,
        the station is carried round the outer layer as a level, and the
        result resolves."""
        real = wake._chord_angle
        calls = []

        def bent(loop, crossing, far_leg, cuts, owned_keys):
            # The planner measures the ring beside the inside placement's
            # crossing first; only that measure is patched.
            calls.append(far_leg)
            if len(calls) == 1:
                return 0.0, "fan (patched)"
            return real(loop, crossing, far_leg, cuts, owned_keys)

        with mock.patch.object(wake, "_chord_angle", bent):
            result = pipeline.run_external(["tear"], [teardrop_loop()], self.options(30.0))
        record = result.wakes.records[0]
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertEqual(record["turn_placement"], "beyond")
        self.assertIn("fan (patched)", record["turn_note"])
        self.assertEqual(len(record["stations"]), 1)
        self.assertTrue(np.allclose(record["legs"][0], [1.0, 0.0]))
        ring = np.asarray(record["ring_point"])
        self.assertGreater(record["stations"][0][0], ring[0], "the station lies beyond the ring")
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.resolved, result.described_failures())
        beyond = [face for face in result.graph.faces if face.provenance == "wake band beyond the ring"]
        self.assertEqual(len(beyond), 4, "each of the two strips beyond the ring is split at the station")
        inside = [face for face in result.graph.faces if face.provenance == "wake band"]
        self.assertEqual(len(inside), 2)
        self.assertEqual(record["turn_levels"]["stations"], 1)
        levels = [key for key in result.graph.vertices if key[0] == "wake" and "level" in key]
        self.assertEqual(len(levels), record["turn_levels"]["patches"] - 1)

    @staticmethod
    def lens_loop(height: float = 0.2, count: int = 80) -> np.ndarray:
        """A lens: two parabolic arcs meeting at a sharp tip at each end, starting at the trailing tip."""
        x = np.cos(np.linspace(0.0, math.pi, count))[::-1]
        upper = np.column_stack([x, height * (1.0 - x ** 2)])
        lower = np.column_stack([x[::-1], -height * (1.0 - x[::-1] ** 2)])
        loop = np.vstack([upper, lower[1:-1]])
        return np.roll(loop, -int(np.argmax(loop[:, 0])), axis=0)

    def test_a_level_row_crosses_another_sharp_corners_seam_wedge(self):
        """The lens's trailing tip turns its wake thirty degrees; the station's
        level row goes round the body and through the leading tip's seam
        wedge, which no wake replaces: the wedge becomes a column of quads on
        each side of its bisector spoke under one quad at the ring vertex."""
        result = pipeline.run_external(["lens"], [self.lens_loop()], self.options(30.0))
        self.assertTrue(result.admissible, result.problems or result.failures)
        records = {item["vertex"]: item for item in result.wakes.records}
        tail = records[0]
        self.assertTrue(tail["applied"], tail.get("reason"))
        self.assertEqual(tail["turn_placement"], "inside")
        self.assertEqual(len(tail["stations"]), 1)
        nose = next(item for vertex, item in records.items() if vertex != 0)
        self.assertFalse(nose["applied"])
        wedge = [key for key in result.graph.vertices if key[0] == "wake" and "wedge" in key]
        self.assertEqual(len(wedge), 1, "one row, one point on the bisector")
        bisector = [edge for edge in result.graph.edges.values() if edge.provenance == "seam wedge bisector"]
        self.assertEqual(len(bisector), 1)
        gate = next(key for key in bisector[0].key if key[0] == "gate")
        faces_at_gate = [face for face in result.graph.faces if gate in face.corners]
        self.assertEqual(len(faces_at_gate), 4, "two band blocks and the wedge's two lower quads")
        self.assertEqual(sum(1 for face in result.graph.faces if wedge[0] in face.corners), 3)
        self.assertFalse(result.problems)

    def test_seams_are_repaired_before_the_wakes_with_the_seam_template_alone(self):
        """On the lens laid out without the gate relaxation both tips carry a
        poor plain seam; the wake stage's pre-repair re-places their seam
        points with the seam template alone, so the wedges the level rows
        cross are still there, and leaves the wakes' own features alone."""
        import block_layout
        import external_topology
        import planar_domain as pdm
        import sites as site_module
        import voronoi_graph

        options = self.options(30.0)
        loop = self.lens_loop()
        outer, circle = pdm.fabricate_outer([loop], pipeline.farfield_spec(options))
        domain = pdm.from_bodies(["lens"], [loop], outer=outer)
        sites, scale = site_module.sites_from_domain(domain, circle=circle, frame=site_module.domain_frame([loop]))
        diagram = voronoi_graph.build_diagram(sites, scale=scale, grid_width=options.grid_width)
        metric = pipeline._metric(domain, options)
        layout = block_layout.build_layout(diagram, options.layout)
        assembly = external_topology.build_graph(layout, domain, metric, options=options.layer, wall_edge_style=options.wall_edge_style, allow_scale=True)
        self.assertFalse(assembly.failures)
        graph, notes = pipeline._repair_seams_before_wakes(assembly.graph, domain, [], options)
        self.assertEqual(len(notes), 2, notes)
        self.assertTrue(all(":seam)" in note for note in notes), notes)
        tail_key = next(cut.anchor.key for cut in layout.cuts[0] if abs(cut.wall_station) < 1e-9)
        # With the trailing tip named as a wake's own feature it is skipped.
        graph, notes = pipeline._repair_seams_before_wakes(assembly.graph, domain, [{"anchor_key": list(tail_key)}], options)
        self.assertEqual(len(notes), 1, notes)
        self.assertNotIn(str(list(tail_key)), notes[0])
        wedges = [face for face in graph.faces if face.provenance == "sharp-feature band seam"]
        self.assertTrue(wedges, "the seam wedges are re-placed, not removed")

    def test_the_cavity_stage_can_be_held_to_one_template(self):
        """The wake stage's pre-repair enumerates the seam template only."""
        import fan_cavity
        names = [item.name for item in fan_cavity.templates(fan_cavity.CavityOptions(), 8)]
        self.assertIn("band", names)
        self.assertIn("seam", names)
        only = [item.name for item in fan_cavity.templates(fan_cavity.CavityOptions(templates=("seam",)), 8)]
        self.assertEqual(only, ["seam"])

    def test_a_base_wake_off_its_flank_bisector_turns(self):
        """A blunt base's wake leaves along the bisector of its flanks and
        turns towards the requested direction; its three strips and both
        bands turn together at the station."""
        result = pipeline.run_external(["blunt"], [self.blunt_loop()], self.options(30.0))
        self.assertTrue(result.admissible, result.problems or result.failures)
        record = result.wakes.records[0]
        self.assertEqual(record["kind"], "base")
        self.assertTrue(record["applied"], record.get("reason"))
        self.assertTrue(record["turned"])
        self.assertEqual(len(record["stations"]), 1)
        self.assertTrue(np.allclose(record["edge_direction"], [1.0, 0.0], atol=1e-6))
        self.assertTrue(result.resolved, result.described_failures())
        strips = sorted(face.provenance for face in result.graph.faces if face.role == "wake")
        self.assertEqual(strips.count("base strip"), 2)
        self.assertEqual(strips.count("wake band"), 4)
        roles = {}
        for edge in result.graph.edges.values():
            roles[edge.role] = roles.get(edge.role, 0) + 1
        self.assertEqual(roles["wake_rung"], 3, "one rung across each of the three strips")


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
