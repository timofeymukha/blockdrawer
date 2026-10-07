"""The single-body C-grid producer: level sets of the body and its wake."""

from dataclasses import replace
import math
import unittest

import numpy as np

from test_agentic_topology import CAVITY_FAST
import cgrid
import geometry2d as g2
import patch_graph as pg
import pipeline
import planar_domain as pdm
import synthetic_cases as cases
import wake

_RUNS: dict = {}


def teardrop(tip_ratio: float = 2.2):
    return cases.teardrop((0.0, 0.0), 0.32, tip_ratio=tip_ratio)


def c_options(**overrides):
    settings = dict(
        farfield_shape="cshape", farfield_radius=3.0, farfield_center=(0.704, 0.0),
        layer_height=0.06, grid_width=420, coverage_samples=240,
        wake=wake.WakeOptions(enabled=True),
    )
    settings.update(overrides)
    return replace(CAVITY_FAST, **settings)


def run(key, loops, options, names=("tear",)):
    if key not in _RUNS:
        _RUNS[key] = pipeline.run_external(list(names), loops, options)
    return _RUNS[key]


class MitreTests(unittest.TestCase):
    def test_mitre_points_lie_at_the_height_from_both_lines(self):
        loop = g2.orient_anticlockwise(g2.close_loop(teardrop(), 0.0))
        index = int(np.argmax(loop[:-1, 0]))
        direction = np.asarray([math.cos(0.2), math.sin(0.2)])
        u_in, u_out = cgrid.mitre_directions(loop, index, direction, 1.0)
        tip = loop[index]
        segments = g2.segment_directions(loop)
        count = len(loop) - 1
        for u, side in ((u_in, -segments[(index - 1) % count]), (u_out, segments[index % count])):
            point = tip + 0.1 * u
            to_wake = abs(float((point - tip)[0] * direction[1] - (point - tip)[1] * direction[0]))
            to_side = abs(float((point - tip)[0] * side[1] - (point - tip)[1] * side[0]))
            self.assertAlmostEqual(to_wake, 0.1, places=9)
            self.assertAlmostEqual(to_side, 0.1, places=9)
        # The two mitres lie on opposite sides of the wake.
        cross = lambda u: direction[0] * u[1] - direction[1] * u[0]
        self.assertLess(cross(u_in) * cross(u_out), 0.0)

    def test_options_reject_bad_parameters(self):
        for parameters in ({"growth": 1.0}, {"reach": 1.5}, {"max_levels": -1}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                cgrid.CGridOptions(**parameters)


class CGridConstructionTests(unittest.TestCase):
    def test_teardrop_in_a_c_shaped_far_field_becomes_a_resolved_c_grid(self):
        result = run("tear-c", [teardrop()], c_options())
        self.assertTrue(result.cgrid["applied"], result.cgrid)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.resolved, result.described_failures())
        graph = result.graph
        self.assertEqual(graph.total_index(), 0)
        self.assertEqual(graph.euler(), 0)
        self.assertGreaterEqual(result.cgrid["levels"], 2)
        roles = {face.role for face in graph.faces}
        self.assertEqual(roles, {"layer", "core", "wake"})
        # Every level is a closed ring of blocks around the body plus the wake band.
        levels = result.cgrid["levels"]
        gates = result.cgrid["gates"]
        wake_blocks = sum(face.role == "wake" for face in graph.faces)
        self.assertEqual(wake_blocks, 2 * levels)
        self.assertEqual(sum(face.role == "layer" for face in graph.faces), gates)
        # The outlet corners are single-block corners; the edge carries four blocks.
        faces = graph.face_counts()
        for label in ("c_top", "c_bot"):
            self.assertEqual(faces[("outer", "corner", label)], 1)
        te = next(key for key in graph.vertices if key[0] == "gate" and key[-1] == 0 and key[2] == "station")
        self.assertEqual(faces[te], 4)
        self.assertEqual(sorted(item["index"] for item in graph.singularities()), [-2, 1, 1])

    def test_the_wake_band_widens_towards_the_outlet(self):
        """The wake lines leave their mitre points at the spread slope, so the
        band that is one band height wide at the trailing edge is wider at the
        outlet, and the strips beside it do not carry first-cell-thin cells for
        the length of the far field."""
        result = run("tear-c", [teardrop()], c_options())
        graph = result.graph
        cell = next(key[1] for key in graph.vertices if key[0] == "wake" and key[2] == "outlet")
        at_body = np.linalg.norm(graph.vertices[("front", cell, "level", 1, 0, "out")].point - graph.vertices[("front", cell, "level", 1, 0, "in")].point)
        at_outlet = np.linalg.norm(graph.vertices[("wake", cell, "outlet", 1, "out")].point - graph.vertices[("wake", cell, "outlet", 1, "in")].point)
        length = graph.vertices[("wake", cell, "outlet", 1, "out")].point[0] - graph.vertices[("front", cell, "level", 1, 0, "out")].point[0]
        self.assertGreater(at_outlet, at_body)
        self.assertAlmostEqual((at_outlet - at_body) / (2.0 * length), c_options().cgrid.wake_spread, places=2)

    def test_a_circle_with_an_outlet_arc_takes_the_c_grid(self):
        """A circular far field cut into cap, legs and an outlet arc is the
        four-chain boundary the producer wants, so a body in a circle gets the
        C-grid and its full band instead of the annular construction's
        inscribed-disk cap (trial B's agent had to supply the circle as four
        --outer chains by hand)."""
        result = run(
            "tear-circle-outlet", [teardrop()],
            c_options(farfield_shape="circle", farfield_radius=3.0, farfield_outlet_degrees=25.0),
        )
        self.assertTrue(result.cgrid["applied"], result.cgrid.get("reason"))
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(sorted(result.model.boundaries), ["bottom", "cap", "outlet", "tear", "top"])
        # Every outer vertex lies on the circle.
        import numpy as np
        for key, vertex in result.graph.vertices.items():
            if key[0] in ("outer", "wake") and (key[1] == "corner" or key[1] == "cap" or (len(key) > 2 and key[2] == "outlet")):
                self.assertAlmostEqual(float(np.hypot(vertex.point[0] - 0.704, vertex.point[1])), 3.0, places=4)

    def test_band_height_is_realised_and_levels_grow_geometrically(self):
        result = run("tear-c", [teardrop()], c_options())
        heights = result.cgrid["heights"]
        self.assertAlmostEqual(heights[0], 0.06)
        for lower, upper in zip(heights, heights[1:]):
            self.assertAlmostEqual(upper / lower, 3.0)
        front = result.assembly.fronts[0]
        self.assertGreater(float(np.min(front.heights[front.gate_indices])), 0.059)
        self.assertEqual(sorted(result.model.boundaries), ["bottom", "cap", "outlet", "tear", "top"])

    def test_far_field_fan_out_is_reported_not_failed(self):
        result = run("tear-c", [teardrop()], c_options())
        structure = result.analysis["graph"]["sizing_structure"]
        self.assertIsNotNone(structure["farfield_fanout_ratio"])
        # The verdict is judged on the whole component list, not the short
        # table: the worst component that is not far-field fan-out.
        judged = structure["worst_judged"]
        self.assertFalse(judged["fan_out"])
        self.assertLessEqual(judged["length_ratio"], 20.0)
        self.assertEqual(result.structure_failures, [])
        # The report states what the exemption waives: the same limit on the
        # worst component overall, fan-out included.
        strict = result.analysis["sizing"]["feasibility"]["failures_without_exemptions"]
        overall = structure["worst_overall"]["length_ratio"]
        self.assertGreaterEqual(overall, judged["length_ratio"])
        self.assertEqual(
            any(item["metric"] == "maximum_component_length_ratio" for item in strict),
            overall > 20.0,
        )

    def test_chain_names_do_not_decide_the_c_grid(self):
        """The outer loop starts at the chain whose name sorts first; with the
        upper leg first the outlet ends at station 0, which the outlet check
        must not mistake for a wake line leaving beyond the corners."""
        reference = run("tear-c", [teardrop()], c_options())
        roles = [chain.role for chain in reference.domain.outer.chains]
        chains = pdm.cshape_chains((0.704, 0.0), 3.0)
        names = ("upstream", "lower", "outlet", "farfield")
        renamed = [(name, chain.role, chain.points) for name, chain in zip(names, chains)]
        result = pipeline.run_external(["tear"], [teardrop()], c_options(), outer=renamed)
        self.assertEqual(result.domain.outer.chains[0].name, "farfield")
        self.assertTrue(result.cgrid["applied"], result.cgrid.get("reason"))
        self.assertEqual(result.graph.summary(), reference.graph.summary())
        self.assertEqual(result.resolved, reference.resolved)
        self.assertEqual(sorted(roles), sorted(chain.role for chain in result.domain.outer.chains))

    def test_c_grid_needs_the_c_shaped_boundary(self):
        result = run("tear-circle", [teardrop()], c_options(farfield_shape="circle", farfield_radius=None, farfield_center=None))
        self.assertFalse(result.cgrid["applied"])
        self.assertIn("outlet", result.cgrid["reason"])
        # The wake rewrite on the closed ring still applies there.
        self.assertTrue(result.admissible)
        self.assertEqual(len(result.wakes.applied), 1)

    def test_two_bodies_keep_the_annular_construction(self):
        names, loops = cases.sharp_bodies()
        result = run("sharp-c", loops, c_options(farfield_center=(0.0, 0.0), farfield_radius=4.0, layer_height=None), names=names)
        self.assertIsNotNone(result.cgrid)
        self.assertFalse(result.cgrid["applied"])
        self.assertIn("exactly one", result.cgrid["reason"])

    def test_body_rotated_against_the_cap_is_refused_and_falls_back(self):
        """The far-field map runs from the cap's top join to its bottom join by
        arc-length fraction, so a body turned twenty degrees against the
        axis-aligned cap twists the far sectors until one is non-convex.  That
        is refused with the reason, and the annular construction stays."""
        angle = 0.35
        rotation = np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        moved = teardrop() @ rotation.T
        centre = rotation @ np.asarray([0.704, 0.0])
        result = pipeline.run_external(
            ["tear"], [moved],
            c_options(farfield_center=(float(centre[0]), float(centre[1])),
                      wake=wake.WakeOptions(enabled=True, direction=(float(math.cos(angle)), float(math.sin(angle))))),
        )
        self.assertTrue(result.cgrid["attempted"])
        self.assertFalse(result.cgrid["applied"])
        self.assertIn("reverted", result.cgrid["reason"])
        self.assertTrue(any(item["kind"] == "non_convex_face" for item in result.cgrid["problems"]))
        self.assertTrue(result.admissible, result.problems or result.failures)


if __name__ == "__main__":
    unittest.main()
