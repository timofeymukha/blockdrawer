"""Research self-tests for the agentic topology prototype.

These are intentionally separate from BlockDrawer's standard-library-only
``tests/`` suite: they need NumPy, and the geometry cases take seconds rather
than milliseconds.  Run them with::

    python -m unittest discover -s experiments/agentic_topology -v
"""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import numpy as np
except ImportError:  # pragma: no cover - research dependency
    raise unittest.SkipTest("NumPy is a research-only dependency")

import block_complex
import block_layout
import geometry2d as g2
import linalg_lite
import patch_solver
import pipeline
import session_emit
import sites as site_module
import synthetic_cases as cases

FAST = pipeline.PipelineOptions(grid_width=420, coverage_samples=260)


def signature_of(result: pipeline.PipelineResult) -> dict:
    return session_emit.topology_signature(result.model)


class GeometryHelperTests(unittest.TestCase):
    def test_loop_section_runs_both_ways(self):
        loop = g2.close_loop([[0, 0], [2, 0], [2, 2], [0, 2]], 0.0)
        forward = g2.loop_section(loop, 1.0, 5.0, forward=True)
        backward = g2.loop_section(loop, 1.0, 5.0, forward=False)
        self.assertAlmostEqual(g2.total_length(forward), 4.0)
        self.assertAlmostEqual(g2.total_length(backward), 4.0)
        self.assertTrue(np.allclose(forward[0], backward[0]))
        self.assertTrue(np.allclose(forward[-1], backward[-1]))

    def test_full_loop_when_endpoints_coincide(self):
        loop = g2.close_loop([[0, 0], [1, 0], [1, 1], [0, 1]], 0.0)
        section = g2.loop_section(loop, 0.5, 0.5, forward=True)
        self.assertAlmostEqual(g2.total_length(section), 4.0)

    def test_quality_is_orientation_agnostic(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertAlmostEqual(patch_solver.block_quality(square), 1.0)
        self.assertAlmostEqual(patch_solver.block_quality(square[::-1]), 1.0)

    def test_quality_rejects_a_folded_quadrilateral(self):
        bowtie = [(0, 0), (1, 0), (0, 1), (1, 1)]
        self.assertLess(patch_solver.block_quality(bowtie), 0.0)

    def test_dense_solvers_match_direct_algebra(self):
        matrix = np.asarray([[4.0, 1.0], [1.0, 3.0]])
        vector = np.asarray([1.0, 2.0])
        solution = linalg_lite.solve_dense(matrix, vector)
        self.assertTrue(np.allclose(matrix @ solution, vector))

    def test_laplacian_reproduces_a_linear_field(self):
        rows, columns, weights = [], [], []
        size = 9
        for index in range(size - 1):
            rows.extend([index, index + 1])
            columns.extend([index + 1, index])
            weights.extend([1.0, 1.0])
        values = np.zeros(size)
        values[-1] = 8.0
        fixed = np.zeros(size, dtype=bool)
        fixed[0] = fixed[-1] = True
        result = linalg_lite.solve_laplacian(
            np.asarray(rows), np.asarray(columns), np.asarray(weights), values, fixed
        )
        self.assertTrue(np.allclose(result, np.arange(size, dtype=float)))


class SiteTests(unittest.TestCase):
    def test_domain_frame_is_rigid_and_scale_covariant(self):
        _names, loops = cases.three_rotated_ellipses()
        center, radius = site_module.domain_frame(loops)
        moved = cases.transform(
            loops, translate=(3.0, -2.0), rotate=0.7, scale=2.5
        )
        new_center, new_radius = site_module.domain_frame(moved)
        rotation = np.asarray(
            [[math.cos(0.7), -math.sin(0.7)], [math.sin(0.7), math.cos(0.7)]]
        )
        expected = 2.5 * (rotation @ center) + np.asarray([3.0, -2.0])
        self.assertTrue(np.allclose(new_center, expected, atol=1e-9))
        self.assertAlmostEqual(new_radius, 2.5 * radius, places=9)

    def test_circle_curve_normal_points_into_the_fluid(self):
        curve = site_module.CircleCurve("far", (0.0, 0.0), 2.0)
        normal = curve.fluid_normal(0.0)
        self.assertTrue(np.allclose(normal, [-1.0, 0.0], atol=1e-12))

    def test_polygon_curve_keeps_the_supplied_points(self):
        points = cases.circle((0.0, 0.0), 1.0, 12)
        curve = site_module.PolygonCurve("body", points)
        section = curve.edge_curve(
            0.0, curve.length() / 4.0, forward=True, style="polyLine"
        )
        self.assertEqual(section.kind, "polyLine")
        for point in section.points:
            self.assertTrue(
                min(math.dist(point, original) for original in points) < 1e-12
            )


class SyntheticCaseTests(unittest.TestCase):
    def test_every_case_produces_a_valid_conformal_topology(self):
        for name, builder in cases.CASES.items():
            with self.subTest(case=name):
                names, loops = builder()
                result = pipeline.run(names, loops, FAST)
                self.assertTrue(
                    result.solve.converged,
                    f"{name}: {result.solve.failures}",
                )
                self.assertEqual(result.manifold, [])
                self.assertEqual(result.coverage["uncovered_samples"], 0)
                self.assertEqual(result.coverage["overlapping_samples"], 0)
                self.assertEqual(result.coverage["outside_samples"], 0)
                self.assertIsNotNone(result.model, result.analysis.get("session_error"))
                self.assertTrue(all(report.convex for report in result.reports))
                self.assertGreater(result.solve.worst_quality, 0.0)

    def test_single_body_case_has_no_junction(self):
        names, loops = cases.single_ellipse()
        result = pipeline.run(names, loops, FAST)
        self.assertEqual(len(result.diagram.junctions), 0)
        self.assertEqual(len(result.diagram.branches), 1)
        self.assertTrue(result.diagram.branches[0].closed)
        self.assertTrue(result.resolved)

    def test_rectangular_farfield_puts_a_gate_on_every_corner(self):
        names, loops = cases.two_circles()
        options = pipeline.PipelineOptions(
            grid_width=FAST.grid_width,
            coverage_samples=FAST.coverage_samples,
            farfield_shape="rectangle",
        )
        result = pipeline.run(names, loops, options)
        self.assertTrue(result.resolved, result.solve.failures)
        farfield = result.diagram.sites[-1].curve
        corners = farfield.loop()[:-1]
        cell = next(
            index
            for index, item in enumerate(result.layout.cells)
            if result.diagram.sites[item.site].curve is farfield
        )
        gates = [cut.wall_point for cut in result.layout.cuts[cell]]
        for corner in corners:
            self.assertTrue(
                min(float(np.linalg.norm(corner - gate)) for gate in gates)
                < 1e-9 * result.scale,
                f"no gate on farfield corner {corner}",
            )

    def test_body_counts_are_not_assumed(self):
        for builder in (cases.single_ellipse, cases.two_circles, cases.four_bodies):
            names, loops = builder()
            result = pipeline.run(names, loops, FAST)
            self.assertTrue(result.resolved)
            self.assertEqual(
                len(result.analysis["curves"]), len(names) + 1, "farfield missing"
            )


class InvarianceTests(unittest.TestCase):
    def _signature(self, names, loops):
        result = pipeline.run(names, loops, FAST)
        self.assertTrue(result.resolved, result.solve.failures)
        return signature_of(result), result

    def assertComparable(self, first: float, second: float, tolerance: float = 1e-3):
        """Dimensionless quality must agree to within raster-seed noise.

        The raster stage decides connectivity from a finite grid, so a
        transformed copy can pick a marginally different seed and the
        relaxation can settle in a marginally different local optimum.  The
        topology signature must still be identical.
        """
        scale = max(abs(first), abs(second), 1e-12)
        self.assertLessEqual(abs(first - second) / scale, tolerance)

    def test_component_permutation(self):
        names, loops = cases.three_rotated_ellipses()
        reference, first = self._signature(names, loops)
        permuted_names, permuted_loops = cases.permute(names, loops, (2, 0, 1))
        other, second = self._signature(permuted_names, permuted_loops)
        self.assertEqual(reference, other)
        self.assertComparable(
            first.solve.worst_quality, second.solve.worst_quality, 1e-9
        )

    def test_point_order_reversal(self):
        names, loops = cases.two_circles()
        reference, first = self._signature(names, loops)
        other, second = self._signature(names, cases.reverse(loops))
        self.assertEqual(reference, other)
        self.assertComparable(first.solve.worst_quality, second.solve.worst_quality)

    def test_translation_rotation_and_uniform_scaling(self):
        names, loops = cases.two_circles()
        reference, first = self._signature(names, loops)
        moved = cases.transform(
            loops, translate=(12.0, -7.5), rotate=math.radians(37.0), scale=4.0
        )
        other, second = self._signature(names, moved)
        self.assertEqual(reference, other)
        self.assertComparable(first.solve.worst_quality, second.solve.worst_quality)
        left = first.analysis["quality"]
        right = second.analysis["quality"]
        # A minimum over all blocks is the most seed-sensitive statistic here,
        # because the axis-aligned raster resolves a rotated copy slightly
        # differently; a few percent is the useful bound.
        self.assertComparable(
            left["minimum_corner_angle_degrees"],
            right["minimum_corner_angle_degrees"],
            0.02,
        )
        self.assertComparable(
            left["minimum_vertex_separation_over_scale"],
            right["minimum_vertex_separation_over_scale"],
            0.02,
        )

    def test_quality_is_dimensionless_under_scaling(self):
        names, loops = cases.concave_and_convex()
        reference, first = self._signature(names, loops)
        other, second = self._signature(names, cases.transform(loops, scale=1000.0))
        self.assertEqual(reference, other)
        self.assertComparable(
            first.analysis["quality"]["maximum_aspect_ratio"],
            second.analysis["quality"]["maximum_aspect_ratio"],
        )
        self.assertComparable(
            first.analysis["quality"]["minimum_scaled_jacobian"],
            second.analysis["quality"]["minimum_scaled_jacobian"],
        )


class SessionTests(unittest.TestCase):
    def test_session_round_trip_render_and_export(self):
        from blockdrawer.foam import block_mesh_dict
        from blockdrawer.render import RenderOptions, render_svg

        names, loops = cases.two_circles()
        result = pipeline.run(names, loops, FAST)
        self.assertTrue(result.resolved)
        model = result.model
        model.validate()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "topology.json"
            session_emit.write_session(model, path)
            reloaded = session_emit.round_trip(path)
        self.assertEqual(
            session_emit.topology_signature(model),
            session_emit.topology_signature(reloaded),
        )
        self.assertIn("hex", block_mesh_dict(reloaded))
        picture = render_svg(reloaded, RenderOptions(width=600, height=400))
        self.assertIn("<svg", picture)

    def test_named_patches_cover_every_exterior_edge(self):
        names, loops = cases.two_circles()
        result = pipeline.run(names, loops, FAST)
        model = result.model
        exterior = [edge for edge in model.edges() if model.is_boundary_edge(edge)]
        self.assertTrue(exterior)
        for edge in exterior:
            self.assertIn(edge, model.edge_boundaries)
        for name in (*names, "farfield"):
            self.assertIn(name, model.boundaries)
        for name in names:
            self.assertEqual(model.boundaries[name].kind, "wall")

    def test_wall_edges_keep_the_supplied_points(self):
        names, loops = cases.two_circles()
        result = pipeline.run(names, loops, FAST)
        model = result.model
        supplied = {
            (round(float(x), 12), round(float(y), 12))
            for loop in loops
            for x, y in loop
        }
        checked = 0
        for edge, geometry in model.edge_geometry.items():
            if geometry.kind != "polyLine":
                continue
            boundary = model.edge_boundaries.get(edge)
            if boundary is None or boundary == "farfield":
                continue
            checked += 1
            for point in geometry.points:
                self.assertIn((round(point[0], 12), round(point[1], 12)), supplied)
        self.assertGreater(checked, 0)

    def test_reference_curves_are_attached(self):
        names, loops = cases.two_circles()
        result = pipeline.run(names, loops, FAST)
        attached = {curve.name for curve in result.model.geometry_curves.values()}
        self.assertEqual(attached, {f"{name}_points" for name in names})


class DiagnosticTests(unittest.TestCase):
    def test_impossible_acceptance_reports_a_structured_failure(self):
        names, loops = cases.two_circles()
        options = pipeline.PipelineOptions(
            grid_width=FAST.grid_width,
            coverage_samples=FAST.coverage_samples,
            solver=patch_solver.SolverOptions(
                accept_quality=0.999, target_quality=0.999, max_splits=1
            ),
        )
        result = pipeline.run(names, loops, options)
        self.assertFalse(result.solve.converged)
        self.assertIsNone(result.model)
        self.assertTrue(result.analysis["solver"]["failures"])
        failure = result.analysis["solver"]["failures"][0]
        for key in ("site", "scaled_jacobian", "wall_chain", "ring_chain", "spokes"):
            self.assertIn(key, failure)
        self.assertIsNone(result.analysis["session"])

    def test_a_failing_stage_still_produces_a_report(self):
        from unittest import mock

        names, loops = cases.two_circles()
        with mock.patch.object(
            pipeline.voronoi_graph,
            "build_diagram",
            side_effect=RuntimeError("forced trace failure"),
        ):
            result = pipeline.run(names, loops, FAST)
        self.assertFalse(result.resolved)
        self.assertIsNone(result.model)
        self.assertIsNone(result.diagram)
        self.assertEqual(result.analysis["stage_errors"][0]["stage"], "voronoi_graph")
        self.assertIn(
            "forced trace failure", result.analysis["stage_errors"][0]["error"]
        )
        self.assertEqual(len(result.analysis["curves"]), len(names) + 1)
        self.assertIsNone(result.analysis["graph"])
        self.assertIsNone(result.analysis["session"])

    def test_coverage_check_sees_a_removed_block(self):
        names, loops = cases.two_circles()
        result = pipeline.run(names, loops, FAST)
        complex_ = result.complex
        complex_.blocks.pop()
        coverage = block_complex.coverage_check(
            result.layout, complex_, samples=FAST.coverage_samples
        )
        self.assertGreater(coverage["uncovered_samples"], 0)

    def test_analysis_reports_the_discrete_graph_and_singularities(self):
        names, loops = cases.three_rotated_ellipses()
        result = pipeline.run(names, loops, FAST)
        analysis = result.analysis
        self.assertEqual(
            analysis["graph"]["junction_count"], len(result.diagram.junctions)
        )
        self.assertEqual(
            analysis["graph"]["branch_count"], len(result.diagram.branches)
        )
        self.assertTrue(analysis["objective"]["terms"])
        for record in analysis["singularities"]:
            self.assertNotEqual(
                record["incident_blocks"], record["regular_block_count"]
            )
            self.assertIsNotNone(record["session_vertex"])
        junction_points = {
            tuple(np.round(junction.point, 9)) for junction in result.diagram.junctions
        }
        for record in analysis["singularities"]:
            if record["kind"] == "ring" and not record["on_boundary"]:
                self.assertIn(tuple(np.round(record["point"], 9)), junction_points)


class LayoutTests(unittest.TestCase):
    def test_relax_gates_spreads_a_collapsed_projection(self):
        names, loops = cases.concave_and_convex()
        result = pipeline.run(names, loops, FAST)
        for cell_index, cell_cuts in enumerate(result.layout.cuts):
            site = result.diagram.sites[result.layout.cells[cell_index].site]
            total = site.curve.length()
            stations = sorted(cut.wall_station for cut in cell_cuts)
            gaps = [
                (stations[(index + 1) % len(stations)] - station) % total
                for index, station in enumerate(stations)
            ]
            self.assertGreater(min(gaps), 0.0)

    def test_every_ring_anchor_is_shared_by_both_cells(self):
        names, loops = cases.three_rotated_ellipses()
        result = pipeline.run(names, loops, FAST)
        counts: dict[tuple, int] = {}
        for cell_cuts in result.layout.cuts:
            for cut in cell_cuts:
                counts[cut.anchor.key] = counts.get(cut.anchor.key, 0) + 1
        for key, count in counts.items():
            expected = 3 if key[0] == "junction" else 2
            self.assertEqual(count, expected, key)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
