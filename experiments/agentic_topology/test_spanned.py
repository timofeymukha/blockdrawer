"""Gap strips must survive export, sampled grids and complete rollback."""

import copy
from dataclasses import replace
import unittest
from unittest import mock

from test_agentic_topology import CAVITY_FAST, run_case
import geometry2d as g2
import numpy as np
import pipeline
import session_emit
import spanned
import synthetic_cases as cases
from blockdrawer.session import to_data


SPAN = replace(CAVITY_FAST, span=spanned.SpanOptions(enabled=True))


def layout_state(layout):
    return [
        [(cut.anchor.key, cut.anchor.position, cut.wall_station) for cut in cuts]
        for cuts in layout.cuts
    ]


class SpanTests(unittest.TestCase):
    def test_two_circles_dissolve_both_junctions_and_preserve_index(self):
        result = run_case("two_circles", SPAN)
        self.assertTrue(result.admissible, result.analysis)
        self.assertEqual(len(result.spanning.spanned), 1)
        applied = next(item for item in result.spanning.branches if item["spanned"])
        self.assertEqual(len(applied["junctions_dissolved"]), 2)
        self.assertEqual(applied["strip_blocks"], 4)
        self.assertEqual(result.graph.euler(), -1)
        self.assertEqual(result.graph.total_index(), -4)
        singularities = result.graph.singularities()
        self.assertEqual(len(singularities), 4)
        self.assertTrue(all(item["valence"] == 5 for item in singularities))
        self.assertTrue(all(item["vertex"][0] == "front" for item in singularities))
        self.assertFalse(any(key[0] == "ring" and key[1] == "junction" for key in result.graph.vertices))
        self.assertEqual(result.graph.problems(), [])
        self.assertEqual(result.shape.inverted_cells, 0)
        self.assertEqual(result.grid.inverted_cells, 0)
        self.assertTrue(result.covered)

    def test_exported_farfield_arcs_follow_the_merged_boundary_section(self):
        """The wrong arc direction leaves the graph clean but folds the grid."""
        result = run_case("two_circles", SPAN)
        arcs = 0
        for edge in result.graph.edges.values():
            if edge.kind != "arc":
                continue
            arcs += 1
            key = tuple(sorted(result.identifiers[vertex] for vertex in edge.key))
            points = np.asarray([result.model.edge_point(key, t) for t in np.linspace(0, 1, 31)])
            self.assertLess(float(np.max(g2.distance_to_polyline(edge.path, points))), result.graph.scale() * 1e-3)
        self.assertGreater(arcs, 0)

    def test_spanning_does_not_hide_the_mouth_count_coupling(self):
        result = run_case("two_circles", SPAN)
        structure = result.analysis["graph"]["sizing_structure"]
        self.assertEqual(structure["tangential_normal_couplings"], 1)
        self.assertEqual(len(structure["coupled"]), 1)
        self.assertEqual(structure["coupled"][0]["roles"], ["core_rung", "ring", "wall"])
        self.assertIsNotNone(result.structure_failures[0]["component"])
        self.assertFalse(result.sizing_feasible)
        self.assertFalse(result.resolved)
        self.assertFalse(spanned.SpanOptions().enabled)

    def test_rejection_restores_gates_anchors_graph_and_session(self):
        baseline = copy.deepcopy(run_case("two_circles", CAVITY_FAST))
        layout_before = layout_state(baseline.layout)
        signature = session_emit.topology_signature(baseline.model)
        graph_before = baseline.graph
        construct = spanned._span_branch

        def incomplete(*args):
            candidate = construct(*args)
            candidate.remove_faces([candidate.faces[-1].key])
            return candidate

        with mock.patch.object(spanned, "_span_branch", side_effect=incomplete):
            result = pipeline._try_spanning(baseline, SPAN)
        self.assertTrue(result.admissible)
        self.assertEqual(result.spanning.spanned, [])
        self.assertIs(result.graph, graph_before)
        self.assertEqual(layout_state(result.layout), layout_before)
        self.assertEqual(session_emit.topology_signature(result.model), signature)
        rejected = [item for item in result.spanning.branches if "reverted" in item.get("reason", "")]
        self.assertEqual(len(rejected), 1)
        self.assertTrue(rejected[0]["failures"][0]["problems"])

    def test_a_clean_graph_with_an_inverted_exported_grid_is_rolled_back(self):
        baseline = copy.deepcopy(run_case("two_circles", CAVITY_FAST))
        session_before = to_data(baseline.model)
        layout_before = layout_state(baseline.layout)
        construct = spanned._span_branch
        farfield = next(site.curve for site in baseline.sites if site.curve.kind != "wall")

        def wrong_arc(*args):
            candidate = construct(*args)
            edge = next(edge for edge in candidate.edges.values() if edge.kind == "arc")
            # Keep its graph path valid, but export the arc around the other
            # side of the circle: the old merged-arc bug did exactly this.
            edge.points = tuple(tuple(2 * farfield.center - point) for point in np.asarray(edge.points))
            return candidate

        with mock.patch.object(spanned, "_span_branch", side_effect=wrong_arc):
            result = pipeline._try_spanning(baseline, replace(SPAN, evaluate_grid=False))
        self.assertEqual(result.spanning.spanned, [])
        self.assertEqual(to_data(result.model), session_before)
        self.assertEqual(layout_state(result.layout), layout_before)
        rejection = next(item for item in result.spanning.branches if "grid" in item)
        self.assertEqual(rejection["problems"], [])
        self.assertGreater(rejection["grid"]["shape_inverted"], 0)

    def test_narrow_gap_without_room_for_a_mouth_keeps_the_original(self):
        baseline = run_case("narrow_gap_tip", CAVITY_FAST)
        result = run_case("narrow_gap_tip", SPAN)
        self.assertTrue(result.admissible, result.analysis)
        self.assertEqual(result.spanning.spanned, [])
        self.assertEqual(layout_state(result.layout), layout_state(baseline.layout))
        self.assertEqual(session_emit.topology_signature(result.model), session_emit.topology_signature(baseline.model))
        self.assertTrue(any("mouth" in item.get("reason", "") for item in result.spanning.branches))

    def test_junction_with_several_narrow_branches_has_an_explicit_reason(self):
        result = run_case("four_bodies", CAVITY_FAST)
        records = spanned.plan(result.layout, result.metric, SPAN.span)
        self.assertFalse(any(item["spanned"] for item in records))
        self.assertTrue(any("more than one" in item.get("reason", "") for item in records))

    def test_spanning_is_invariant_under_reversal_permutation_and_similarity(self):
        names, loops = cases.two_circles()
        angle = .731
        rotation = np.asarray([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        transformed = [1000 * np.asarray(loop)[::-1] @ rotation.T + [17, -9] for loop in loops]
        result = pipeline.run_external(names[::-1], transformed[::-1], SPAN)
        baseline = run_case("two_circles", SPAN)
        self.assertTrue(result.admissible, result.analysis)
        self.assertEqual(result.graph.summary(), baseline.graph.summary())
        self.assertEqual(result.spanning.spanned, baseline.spanning.spanned)
        self.assertEqual(result.graph.total_index(), baseline.graph.total_index())

    def test_options_reject_nonfinite_and_degenerate_parameters(self):
        for parameters in ({"mouth_angle": 180}, {"mouth_angle": float("nan")},
                           {"mouth_reach": 0}, {"clearance_ratio": float("inf")}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                spanned.SpanOptions(**parameters)


if __name__ == "__main__":
    unittest.main()
