"""Research self-tests for the agentic topology prototype.

These are intentionally separate from BlockDrawer's standard-library-only
``tests/`` suite: they need NumPy, and the geometry cases take seconds rather
than milliseconds.  Run them with::

    python -m unittest discover -s experiments/agentic_topology -v
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # the research package imports BlockDrawer, never the other way round
    import blockdrawer  # noqa: F401
except ImportError:  # pragma: no cover - running from inside the folder
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import numpy as np
except ImportError:  # pragma: no cover - research dependency
    raise unittest.SkipTest("NumPy is a research-only dependency")

import agent_ops
import block_layout
import external_topology
import fan_cavity
import geometry2d as g2
import grid_quality
import layers as layer_module
import linalg_lite
import moves as move_module
import patch_graph as pg
import patch_solver
import pipeline
import planar_domain as pdm
import session_emit
import sites as site_module
import sizing
import sweep as sweep_module
import synthetic_cases as cases

FAST = pipeline.PipelineOptions(
    grid_width=420,
    coverage_samples=240,
    sizing=sizing.SizingOptions(first_width_ratio=4.0e-3, core_size_ratio=6.0e-2),
)
QUICK_CASES = ("single_ellipse", "two_circles", "concave_and_convex", "four_bodies")
# The cavity fixtures keep the default sizing: a deliberately narrow gap cannot
# hold the coarse first-cell width the other quick cases use.
CAVITY_FAST = pipeline.PipelineOptions(grid_width=420, coverage_samples=240)


# Several tests read the same run from different angles, and a run costs
# seconds.  The cache is keyed by the case and the complete options, so a test
# that changes an option still gets its own run; ``fresh`` opts out for the one
# test that mutates the result it is given.
_RUNS: dict = {}


def run_case(name: str, options=None, *, fresh: bool = False):
    settings = options or FAST
    key = ("external", name, repr(settings))
    if fresh or key not in _RUNS:
        names, loops = cases.CASES[name]()
        result = pipeline.run_external(names, loops, settings)
        if fresh:
            return result
        _RUNS[key] = result
    return _RUNS[key]


def run_internal(name: str, options=None, *, fresh: bool = False):
    settings = options or FAST
    key = ("internal", name, repr(settings))
    if fresh or key not in _RUNS:
        domain = pdm.from_internal_case(cases.INTERNAL_CASES[name]())
        result = pipeline.run_internal(domain, settings)
        if fresh:
            return result
        _RUNS[key] = result
    return _RUNS[key]


# ---------------------------------------------------------------------------
# Geometry and numerics
# ---------------------------------------------------------------------------


class GeometryHelperTests(unittest.TestCase):
    def test_loop_section_runs_both_ways(self):
        loop = g2.close_loop([[0, 0], [2, 0], [2, 2], [0, 2]], 0.0)
        forward = g2.loop_section(loop, 1.0, 5.0, forward=True)
        backward = g2.loop_section(loop, 1.0, 5.0, forward=False)
        self.assertAlmostEqual(g2.total_length(forward), 4.0)
        self.assertAlmostEqual(g2.total_length(backward), 4.0)
        self.assertTrue(np.allclose(forward[0], backward[0]))

    def test_quality_is_orientation_agnostic(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertAlmostEqual(patch_solver.block_quality(square), 1.0)
        self.assertAlmostEqual(patch_solver.block_quality(square[::-1]), 1.0)

    def test_quality_rejects_a_folded_quadrilateral(self):
        bowtie = [(0, 0), (1, 0), (0, 1), (1, 1)]
        self.assertLess(patch_solver.block_quality(bowtie), 0.0)

    def test_offset_keeps_a_constant_corner_distance(self):
        path = np.asarray([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
        offset = g2.offset_polyline(path, 0.1, closed=False)
        self.assertTrue(np.allclose(offset[1], [0.9, 0.1]))

    def test_self_intersection_is_detected(self):
        bowtie = np.asarray([[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]])
        self.assertFalse(g2.is_simple(bowtie, closed=False))

    def test_dense_solvers_match_direct_algebra(self):
        matrix = np.asarray([[4.0, 1.0], [1.0, 3.0]])
        vector = np.asarray([1.0, 2.0])
        solution = linalg_lite.solve_dense(matrix, vector)
        self.assertTrue(np.allclose(matrix @ solution, vector))


# ---------------------------------------------------------------------------
# 1. Planar domain validation and normalisation
# ---------------------------------------------------------------------------


class PlanarDomainTests(unittest.TestCase):
    def test_internal_case_is_a_valid_simply_connected_domain(self):
        domain = pdm.from_internal_case(cases.periodic_hill())
        self.assertEqual(domain.problems(), [])
        self.assertTrue(domain.simply_connected)
        self.assertEqual(domain.euler_characteristic, 1)
        self.assertGreater(g2.signed_area(domain.outer.points()), 0.0)

    def test_bodies_become_clockwise_holes_in_a_canonical_order(self):
        names, loops = cases.three_rotated_ellipses()
        domain = pdm.from_bodies(names, loops)
        self.assertEqual(domain.problems(), [])
        self.assertEqual(domain.hole_count, 3)
        self.assertEqual(domain.euler_characteristic, -2)
        for hole in domain.holes:
            self.assertLess(g2.signed_area(hole.points()), 0.0)
        permuted_names, permuted_loops = cases.permute(names, loops, (2, 0, 1))
        other = pdm.from_bodies(permuted_names, permuted_loops)
        self.assertEqual(domain.signature(), other.signature())

    def test_reversed_representation_normalises_back(self):
        domain = pdm.from_internal_case(cases.periodic_hill())
        reversed_domain = pdm.from_internal_case(
            cases.reverse_case(cases.periodic_hill())
        )
        self.assertEqual(reversed_domain.problems(), [])
        self.assertEqual(domain.signature(), reversed_domain.signature())
        self.assertTrue(
            np.allclose(
                domain.translation("periodic_left"),
                reversed_domain.translation("periodic_left"),
            )
        )

    def test_validation_names_every_kind_of_defect(self):
        chain = pdm.Chain("only", "wall", [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
        gap = pdm.PlanarDomain("broken", (pdm.Loop((chain,), False),))
        kinds = {problem["kind"] for problem in gap.problems()}
        self.assertIn("disconnected_chains", kinds)
        duplicate = pdm.PlanarDomain(
            "twice",
            (
                pdm.Loop(
                    (
                        pdm.Chain("a", "wall", [[0, 0], [1, 0]]),
                        pdm.Chain("a", "wall", [[1, 0], [0, 0]]),
                    ),
                    False,
                ),
            ),
        )
        self.assertIn(
            "duplicate_chain_name",
            {problem["kind"] for problem in duplicate.problems()},
        )

    def test_a_cyclic_chain_without_a_partner_is_rejected(self):
        case = cases.periodic_hill()
        boundaries = tuple(
            cases.BoundaryChain(chain.name, chain.kind, chain.points, None)
            if chain.name == "periodic_left"
            else chain
            for chain in case.boundaries
        )
        broken = cases.InternalFlowCase(
            case.name, boundaries, case.periodic_translation
        )
        domain = pdm.from_internal_case(broken)
        kinds = {problem["kind"] for problem in domain.problems()}
        self.assertTrue(
            {"cyclic_without_neighbour", "cyclic_not_reciprocal"} & kinds, kinds
        )


# ---------------------------------------------------------------------------
# 2. Periodic pairs under rigid transforms and uniform scaling
# ---------------------------------------------------------------------------


class PeriodicPairTests(unittest.TestCase):
    def test_translation_follows_a_rigid_transform_and_scaling(self):
        base = pdm.from_internal_case(cases.periodic_hill())
        angle = math.radians(37.0)
        scale = 4.0
        moved = pdm.from_internal_case(
            cases.transform_case(
                cases.periodic_hill(),
                translate=(12.0, -7.5),
                rotate=angle,
                scale=scale,
            )
        )
        self.assertEqual(moved.problems(), [])
        rotation = np.asarray(
            [
                [math.cos(angle), -math.sin(angle)],
                [math.sin(angle), math.cos(angle)],
            ]
        )
        expected = scale * (rotation @ base.translation("periodic_left"))
        self.assertTrue(
            np.allclose(moved.translation("periodic_left"), expected, atol=1e-9)
        )
        self.assertEqual(base.signature(), moved.signature())

    def test_a_mismatched_periodic_pair_is_reported(self):
        case = cases.periodic_hill()
        left = case.boundary("periodic_left")
        shifted = cases.BoundaryChain(
            left.name, left.kind, left.points + np.asarray([0.0, 0.03]), left.neighbour
        )
        boundaries = tuple(
            shifted if chain.name == left.name else chain for chain in case.boundaries
        )
        domain = pdm.PlanarDomain(
            "broken",
            (pdm.Loop(
                tuple(
                    pdm.Chain(c.name, c.kind, c.points, c.neighbour)
                    for c in boundaries
                ),
                False,
            ),),
            {("periodic_left", "periodic_right"): np.asarray([9.0, 0.0])},
        )
        kinds = {problem["kind"] for problem in domain.problems()}
        self.assertIn("cyclic_geometry_mismatch", kinds)


# ---------------------------------------------------------------------------
# 3, 4, 5. Sweep / submapping
# ---------------------------------------------------------------------------


class SweepTests(unittest.TestCase):
    def test_straight_channel_is_detected_and_built(self):
        result = run_internal("straight_channel")
        self.assertTrue(result.resolved, result.failures or result.problems)
        four = result.four_sided
        self.assertEqual(
            sorted(name for side in four.guides for name in four.sides[side].names),
            ["bottom_wall", "top_wall"],
        )
        self.assertEqual(
            sorted(name for side in four.ends for name in four.sides[side].names),
            ["inlet", "outlet"],
        )
        self.assertFalse(four.periodic)
        self.assertTrue(result.correspondence.monotone)
        self.assertEqual(result.correspondence.crossing, [])
        roles = result.graph.summary()["face_roles"]
        self.assertGreater(roles.get("layer", 0), 0)
        self.assertGreater(roles.get("core", 0), 0)

    def test_a_rotated_and_reversed_channel_is_equivalent(self):
        reference = run_internal("straight_channel")
        moved = pdm.from_internal_case(
            cases.transform_case(
                cases.reverse_case(cases.straight_channel()),
                translate=(5.0, -3.0),
                rotate=math.radians(41.0),
                scale=2.5,
            )
        )
        other = pipeline.run_internal(moved, FAST)
        self.assertTrue(other.resolved, other.failures or other.problems)
        self.assertEqual(
            session_emit.topology_signature(reference.model),
            session_emit.topology_signature(other.model),
        )

    def test_periodic_hill_builds_bands_a_core_and_a_cyclic_pair(self):
        result = run_internal("periodic_hill")
        self.assertTrue(result.admissible, result.failures or result.problems)
        self.assertTrue(result.four_sided.periodic)
        self.assertTrue(
            np.allclose(np.abs(result.four_sided.translation), [9.0, 0.0])
        )
        roles = result.graph.summary()["face_roles"]
        self.assertGreater(roles.get("layer", 0), 0)
        self.assertGreater(roles.get("core", 0), 0)
        self.assertGreater(result.graph.summary()["periodic_vertex_pairs"], 0)
        model = result.model
        self.assertEqual(model.boundaries["bottom_wall"].kind, "wall")
        self.assertEqual(model.boundaries["top_wall"].kind, "wall")
        self.assertEqual(model.boundaries["periodic_left"].kind, "cyclic")
        self.assertEqual(
            model.boundaries["periodic_left"].neighbour_patch, "periodic_right"
        )
        self.assertEqual(
            model.boundaries["periodic_right"].neighbour_patch, "periodic_left"
        )
        self.assertEqual(result.grid.inverted_cells, 0)

    def test_periodic_sides_match_after_translation(self):
        result = run_internal("periodic_hill")
        graph = result.graph
        translation = result.domain.translation("periodic_left")
        self.assertGreater(len(graph.periodic_vertices), 0)
        for key, partner in graph.periodic_vertices.items():
            first = graph.vertices[key].point
            second = graph.vertices[partner].point
            gap = min(
                float(np.linalg.norm(second - first - translation)),
                float(np.linalg.norm(first - second - translation)),
            )
            self.assertLess(gap, 1e-9 * result.scale)

    def test_periodic_edges_carry_equal_counts_and_grading(self):
        result = run_internal("periodic_hill")
        model = result.model
        identifiers = result.identifiers
        graph = result.graph
        pairs = 0
        seen = set()
        for key, partner in graph.periodic_vertices.items():
            for other, other_partner in graph.periodic_vertices.items():
                if key == other:
                    continue
                near = graph.edge_key(key, other)
                far = graph.edge_key(partner, other_partner)
                if near not in graph.edges or far not in graph.edges:
                    continue
                if near in seen:
                    continue
                seen.add(near)
                first = pg.PatchGraph.edge_key(
                    identifiers[near[0]], identifiers[near[1]]
                )
                second = pg.PatchGraph.edge_key(
                    identifiers[far[0]], identifiers[far[1]]
                )
                first = tuple(sorted(first))
                second = tuple(sorted(second))
                self.assertEqual(
                    model.edge_cells[first], model.edge_cells[second]
                )
                self.assertAlmostEqual(
                    model.edge_total_expansion(first),
                    model.edge_total_expansion(second),
                    places=9,
                )
                pairs += 1
        self.assertGreater(pairs, 0)

    def test_periodic_hill_survives_every_equivalence(self):
        reference = run_internal("periodic_hill")
        signature = session_emit.topology_signature(reference.model)
        variants = {
            "reversed": cases.reverse_case(cases.periodic_hill()),
            "rigid_and_scaled": cases.transform_case(
                cases.periodic_hill(),
                translate=(12.0, -7.5),
                rotate=math.radians(37.0),
                scale=4.0,
            ),
            "reversed_then_moved": cases.transform_case(
                cases.reverse_case(cases.periodic_hill()),
                translate=(-3.0, 2.0),
                rotate=math.radians(-64.0),
                scale=0.25,
            ),
        }
        for label, case in variants.items():
            with self.subTest(variant=label):
                result = pipeline.run_internal(pdm.from_internal_case(case), FAST)
                self.assertTrue(
                    result.admissible, result.failures or result.problems
                )
                self.assertEqual(
                    result.within_quality_targets,
                    reference.within_quality_targets,
                )
                self.assertEqual(
                    session_emit.topology_signature(result.model), signature
                )
                self.assertEqual(result.grid.inverted_cells, 0)
                self.assertAlmostEqual(
                    result.grid.minimum_scaled_jacobian.value,
                    reference.grid.minimum_scaled_jacobian.value,
                    places=6,
                )

    def test_a_domain_with_a_hole_is_not_sweepable(self):
        names, loops = cases.two_circles()
        domain = pdm.from_bodies(names, loops)
        four, reasons = sweep_module.detect(domain)
        self.assertIsNone(four)
        self.assertEqual(reasons[0]["kind"], "not_simply_connected")


# ---------------------------------------------------------------------------
# 6, 7, 8. Boundary-layer fronts
# ---------------------------------------------------------------------------


class LayerTests(unittest.TestCase):
    def test_offset_front_around_a_smooth_body_keeps_its_height(self):
        loop = g2.close_loop(cases.circle((0.0, 0.0), 1.0, 240), 0.0)
        loop = g2.orient_anticlockwise(loop)
        front = layer_module.build_front(
            loop,
            [0.0, 1.5, 3.0, 4.5],
            fluid_sign=1.0,
            wall_loops=[loop],
            requested=0.1,
            options=layer_module.LayerOptions(),
            name="disk",
        )
        radii = np.linalg.norm(front.front[:-1], axis=1)
        self.assertTrue(np.allclose(radii, 1.1, atol=2e-3), radii.min())
        self.assertTrue(g2.is_simple(front.front, closed=True))

    def test_two_close_walls_clearance_limit_their_fronts(self):
        first = g2.orient_anticlockwise(
            g2.close_loop(cases.circle((-0.6, 0.0), 0.5, 200), 0.0)
        )
        second = g2.orient_anticlockwise(
            g2.close_loop(cases.circle((0.6, 0.0), 0.5, 200), 0.0)
        )
        options = layer_module.LayerOptions(clearance_fraction=0.35)
        front = layer_module.build_front(
            first,
            [0.0, 0.8, 1.6, 2.4],
            fluid_sign=1.0,
            wall_loops=[first, second],
            requested=1.0,
            options=options,
            name="left",
        )
        gap = 0.2  # the two circles are 0.2 apart
        towards = np.argmax(front.wall[:-1, 0])
        self.assertLess(float(front.heights[towards]), 0.35 * gap + 1e-6)
        away = np.argmin(front.wall[:-1, 0])
        self.assertGreater(float(front.heights[away]), float(front.heights[towards]))
        self.assertTrue(g2.is_simple(front.front, closed=True))

    def test_a_sharp_tip_gets_a_seam_with_three_incident_blocks(self):
        names, loops = cases.sharp_bodies()
        result = pipeline.run_external(names, loops, FAST)
        self.assertTrue(result.admissible, result.failures or result.problems)
        seams = [
            record
            for record in result.assembly.seams
            if record.get("wedge_is_convex", True)
        ]
        self.assertTrue(seams, result.assembly.seams)
        graph = result.graph
        faces = graph.face_counts()
        valence = graph.valence()
        for record in seams:
            key = next(
                item
                for item in graph.vertices
                if item[0] == "gate"
                and np.allclose(graph.vertices[item].point, record["point"])
            )
            self.assertEqual(faces[key], 3, "the sharp feature must carry 3 blocks")
            self.assertEqual(valence[key], 4, "two wall edges and two spokes")
        self.assertEqual(graph.total_index(), 4 * result.domain.euler_characteristic)

    def test_the_seam_geometry_is_index_balanced(self):
        names, loops = cases.sharp_bodies()
        result = pipeline.run_external(names, loops, FAST)
        graph = result.graph
        self.assertEqual(graph.euler(), graph.euler_characteristic)
        self.assertEqual(graph.total_index(), 4 * graph.euler_characteristic)


# ---------------------------------------------------------------------------
# 9. Euler and index bookkeeping for discrete moves
# ---------------------------------------------------------------------------


class DiscreteMoveTests(unittest.TestCase):
    def test_splitting_a_valence_six_vertex_preserves_the_index(self):
        pieces = move_module.split_valences(6)
        self.assertEqual(pieces, [5, 5])
        self.assertEqual(sum(4 - value for value in pieces), 4 - 6)
        self.assertEqual(move_module.split_valences(4), [4])
        self.assertEqual(move_module.split_valences(2), [3, 3])

    def test_a_valence_six_medial_junction_offers_a_balanced_split(self):
        result = run_case("two_circles")
        valence = result.graph.valence()
        boundary = result.graph.boundary_vertices()
        interior = [
            key
            for key in result.graph.vertices
            if key not in boundary and valence[key] >= 6
        ]
        self.assertTrue(interior, "the medial junctions should be valence six")
        candidates = [
            move
            for move in move_module.candidates(result)
            if move.kind == "split_singularity"
        ]
        self.assertTrue(candidates)
        for move in candidates:
            self.assertTrue(move.index_balanced, move.described())
            self.assertEqual(
                sum(4 - value for value in move.target["replacement_valences"]),
                move.target["index"],
            )
            self.assertIsNotNone(move.rejection)

    def test_candidates_are_ranked_and_applicable_ones_can_be_applied(self):
        result = run_case("two_circles")
        report = agent_ops.candidate_report(result)
        self.assertEqual(
            report["index_budget"]["expected_total"],
            report["index_budget"]["actual_total"],
        )
        self.assertTrue(report["candidates"])
        applicable = report["applicable"]
        if applicable:
            move = agent_ops.find_move(result, applicable[0])
            options = move_module.apply(result.options, move)
            self.assertNotEqual(options, result.options)

    def test_an_unimplemented_move_refuses_to_apply(self):
        result = run_case("two_circles")
        move = next(
            item
            for item in move_module.candidates(result)
            if not item.implemented
        )
        with self.assertRaises(ValueError):
            move_module.apply(result.options, move)


# ---------------------------------------------------------------------------
# 10. Metric cell-count quantisation
# ---------------------------------------------------------------------------


class SizingTests(unittest.TestCase):
    def test_layer_series_reaches_the_core_size(self):
        options = sizing.SizingOptions(
            first_width=0.01, core_size=0.2, growth=1.2
        )
        metric = sizing.SizeMetric(options, 1.0, [])
        self.assertGreaterEqual(
            metric.first * metric.growth ** (metric.layer_cells - 1),
            metric.core - 1e-12,
        )
        self.assertAlmostEqual(
            metric.layer_height,
            sizing.layer_height(metric.first, metric.growth, metric.layer_cells),
        )

    def test_every_equality_component_gets_one_count(self):
        result = run_case("two_circles")
        components = pg.constraint_components(result.graph)
        counts = result.counts.counts
        self.assertEqual(len(components), len(result.counts.components))
        for group in components:
            values = {counts[key] for key in group}
            self.assertEqual(len(values), 1, group)
        for face in result.graph.faces:
            edges = result.graph.face_edges(face)
            self.assertEqual(counts[edges[0]], counts[edges[2]])
            self.assertEqual(counts[edges[1]], counts[edges[3]])

    def test_the_count_minimises_the_weighted_metric_error(self):
        result = run_case("two_circles")
        for record in result.counts.components:
            target = record["weighted_target"]
            chosen = record["cells"]
            self.assertLessEqual(abs(chosen - target), 0.5 + 1e-9)

    def test_a_budget_scales_every_component_down(self):
        names, loops = cases.two_circles()
        options = pipeline.PipelineOptions(
            grid_width=FAST.grid_width,
            coverage_samples=FAST.coverage_samples,
            sizing=sizing.SizingOptions(
                first_width_ratio=4.0e-3, core_size_ratio=6.0e-2, budget=700
            ),
        )
        result = pipeline.run_external(names, loops, options)
        self.assertTrue(result.admissible, result.failures or result.problems)
        self.assertLessEqual(result.counts.total_cells, 700)
        self.assertLess(result.counts.budget_factor, 1.0)

    def test_wall_normal_edges_carry_the_requested_first_cell(self):
        result = run_case("two_circles")
        model = result.model
        requests = sizing.layer_grading_requests(result.graph, result.metric)
        self.assertTrue(requests)
        checked = 0
        for request in requests[:6]:
            current = tuple(
                sorted(
                    (
                        result.identifiers[request.edge[0]],
                        result.identifiers[request.edge[1]],
                    )
                )
            )
            if model.edge_cells[current] < 2:
                continue
            values = model.edge_grading_values(current)
            wall = result.identifiers[request.wall_vertex]
            width = values.start_width if current[0] == wall else values.end_width
            self.assertAlmostEqual(width, result.metric.first, places=9)
            checked += 1
        self.assertGreater(checked, 0)


# ---------------------------------------------------------------------------
# 11. Sampled transfinite-grid quality
# ---------------------------------------------------------------------------


class GridQualityTests(unittest.TestCase):
    def test_sampled_grid_sees_a_defect_the_four_corners_miss(self):
        from blockdrawer import quality as bd_quality
        from blockdrawer.domain import EdgeGeometry, edge_key
        from blockdrawer.model import MeshModel

        model = MeshModel()
        model.edge_geometry[edge_key("v0", "v1")] = EdgeGeometry(
            "arc", ((0.5, 1.4),)
        )
        model.validate()
        corner = bd_quality.assess_quality(model)
        self.assertEqual(corner.summary()["warning_count"], 0)
        self.assertGreater(corner.summary()["min_angle"], 20.0)
        report = grid_quality.evaluate(model)
        self.assertGreater(report.inverted_cells, 0)
        self.assertFalse(report.admissible)
        self.assertLess(report.minimum_scaled_jacobian.value, 0.0)
        self.assertEqual(report.minimum_scaled_jacobian.block, "b0")

    def test_a_clean_block_is_admissible_everywhere(self):
        from blockdrawer.model import MeshModel

        report = grid_quality.evaluate(MeshModel())
        self.assertTrue(report.admissible)
        self.assertAlmostEqual(report.minimum_scaled_jacobian.value, 1.0, places=9)
        self.assertAlmostEqual(report.maximum_non_orthogonality.value, 0.0, places=6)

    def test_layer_blocks_report_their_anisotropy_separately(self):
        result = run_case("two_circles")
        described = result.grid.described()
        self.assertIsNotNone(described["maximum_aspect_ratio_boundary_layer"])
        self.assertIsNotNone(described["maximum_aspect_ratio_unintended"])
        self.assertEqual(described["inverted_cells"], 0)
        self.assertIsNotNone(described["maximum_wall_misalignment_degrees"])


# ---------------------------------------------------------------------------
# 12. Sessions, plus the older invariance and diagnostics coverage
# ---------------------------------------------------------------------------


class SessionTests(unittest.TestCase):
    def _round_trip(self, result):
        from blockdrawer.foam import block_mesh_dict
        from blockdrawer.render import RenderOptions, render_svg

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
        return reloaded

    def test_external_session_round_trip_render_and_export(self):
        result = run_case("two_circles")
        self.assertTrue(result.admissible)
        self._round_trip(result)

    def test_internal_session_round_trip_render_and_export(self):
        from blockdrawer.foam import block_mesh_dict

        result = run_internal("periodic_hill")
        self.assertTrue(result.admissible, result.failures or result.problems)
        reloaded = self._round_trip(result)
        text = block_mesh_dict(reloaded)
        self.assertIn("cyclic", text)
        self.assertIn("neighbourPatch", text)

    def test_named_patches_cover_every_exterior_edge(self):
        result = run_case("two_circles")
        model = result.model
        exterior = [edge for edge in model.edges() if model.is_boundary_edge(edge)]
        self.assertTrue(exterior)
        for edge in exterior:
            self.assertIn(edge, model.edge_boundaries)
        for name in ("small", "large", "farfield"):
            self.assertIn(name, model.boundaries)
        self.assertEqual(model.boundaries["small"].kind, "wall")

    def test_wall_edges_keep_the_supplied_points(self):
        names, loops = cases.two_circles()
        result = pipeline.run_external(names, loops, FAST)
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
        result = run_case("two_circles")
        attached = {curve.name for curve in result.model.geometry_curves.values()}
        self.assertEqual(attached, {"small_points", "large_points"})


class SyntheticCaseTests(unittest.TestCase):
    def test_every_quick_case_produces_a_valid_topology(self):
        # ``admissible`` - valid topology and no folded cell - is the claim a
        # construction test can make.  Whether a case also meets every declared
        # quality target is a separate, stricter statement (``resolved``) that
        # these coarse research settings deliberately do not promise.
        for name in QUICK_CASES:
            with self.subTest(case=name):
                result = run_case(name)
                self.assertTrue(
                    result.admissible,
                    (result.errors, result.failures, result.problems[:2]),
                )
                self.assertTrue(result.topology_valid)
                self.assertTrue(result.untangled)
                self.assertEqual(
                    result.resolved,
                    result.within_quality_targets,
                )
                self.assertEqual(result.coverage["uncovered_samples"], 0)
                self.assertEqual(result.coverage["overlapping_samples"], 0)
                self.assertEqual(result.coverage["outside_samples"], 0)
                self.assertEqual(result.grid.inverted_cells, 0)
                self.assertEqual(
                    result.graph.total_index(),
                    4 * result.domain.euler_characteristic,
                )

    def test_single_body_case_has_no_junction(self):
        result = run_case("single_ellipse")
        self.assertEqual(len(result.diagram.junctions), 0)
        self.assertEqual(len(result.diagram.branches), 1)
        self.assertTrue(result.diagram.branches[0].closed)

    def test_body_counts_are_not_assumed(self):
        for name in ("single_ellipse", "two_circles", "four_bodies"):
            result = run_case(name)
            self.assertTrue(result.admissible)
            self.assertEqual(
                len(result.analysis["domain"]["chains"]),
                result.domain.hole_count + 1,
            )


class InvarianceTests(unittest.TestCase):
    def assertComparable(self, first: float, second: float, tolerance: float = 1e-3):
        scale = max(abs(first), abs(second), 1e-12)
        self.assertLessEqual(abs(first - second) / scale, tolerance)

    def _signature(self, names, loops):
        result = pipeline.run_external(names, loops, FAST)
        self.assertTrue(result.admissible, result.failures or result.problems)
        return session_emit.topology_signature(result.model), result

    def test_component_permutation(self):
        names, loops = cases.two_circles()
        reference, _first = self._signature(names, loops)
        permuted_names, permuted_loops = cases.permute(names, loops, (1, 0))
        other, _second = self._signature(permuted_names, permuted_loops)
        self.assertEqual(reference, other)

    def test_point_order_reversal(self):
        names, loops = cases.two_circles()
        reference, _first = self._signature(names, loops)
        other, _second = self._signature(names, cases.reverse(loops))
        self.assertEqual(reference, other)

    def test_translation_rotation_and_uniform_scaling(self):
        names, loops = cases.two_circles()
        reference, first = self._signature(names, loops)
        moved = cases.transform(
            loops, translate=(12.0, -7.5), rotate=math.radians(37.0), scale=4.0
        )
        other, second = self._signature(names, moved)
        self.assertEqual(reference, other)
        self.assertComparable(
            first.grid.minimum_scaled_jacobian.value,
            second.grid.minimum_scaled_jacobian.value,
            0.05,
        )

    def test_quality_is_dimensionless_under_scaling(self):
        names, loops = cases.concave_and_convex()
        reference, first = self._signature(names, loops)
        other, second = self._signature(names, cases.transform(loops, scale=1000.0))
        self.assertEqual(reference, other)
        self.assertComparable(
            first.grid.maximum_non_orthogonality.value,
            second.grid.maximum_non_orthogonality.value,
            0.02,
        )


class DiagnosticTests(unittest.TestCase):
    def test_coverage_sees_a_removed_block(self):
        # This one mutates what it is given, so it must not share a cached run.
        result = run_case("two_circles", fresh=True)
        result.graph.faces.pop()
        coverage = pg.coverage(result.graph, result.domain, samples=240)
        self.assertGreater(coverage["uncovered_samples"], 0)

    def test_a_failing_stage_still_produces_a_report(self):
        from unittest import mock

        names, loops = cases.two_circles()
        with mock.patch.object(
            pipeline.voronoi_graph,
            "build_diagram",
            side_effect=RuntimeError("forced trace failure"),
        ):
            result = pipeline.run_external(names, loops, FAST)
        self.assertFalse(result.resolved)
        self.assertIsNone(result.model)
        self.assertEqual(result.analysis["stage_errors"][0]["stage"], "voronoi_graph")
        self.assertIn(
            "forced trace failure", result.analysis["stage_errors"][0]["error"]
        )
        self.assertIsNotNone(result.analysis["domain"])
        self.assertIsNone(result.analysis["graph"])
        self.assertIsNone(result.analysis["session"])

    def test_an_impossible_acceptance_reports_a_structured_failure(self):
        names, loops = cases.two_circles()
        options = pipeline.PipelineOptions(
            grid_width=FAST.grid_width,
            coverage_samples=FAST.coverage_samples,
            sizing=FAST.sizing,
            solver=patch_solver.SolverOptions(
                accept_quality=0.999, split_quality=0.999, max_splits=1
            ),
        )
        result = pipeline.run_external(names, loops, options)
        self.assertFalse(result.resolved)
        self.assertIsNone(result.model)
        self.assertTrue(result.failures)
        self.assertIsNone(result.analysis["session"])

    def test_the_agent_description_names_every_decision_input(self):
        result = run_case("two_circles")
        description = agent_ops.describe(result)
        for key in (
            "domain",
            "boundaries",
            "layer_fronts",
            "singularities",
            "separatrix_graph",
            "count_components",
            "worst_cells",
        ):
            self.assertIn(key, description)
        self.assertTrue(description["boundaries"])
        self.assertEqual(
            description["separatrix_graph"]["kind"], "generalized medial axis"
        )

    def test_focus_returns_a_window_with_blocks(self):
        result = run_case("two_circles")
        window = agent_ops.focus(result, block="b0")
        self.assertIsNotNone(window)
        self.assertTrue(window["blocks"])
        self.assertEqual(len(window["bounds"]), 4)

    def test_analysis_reports_the_graph_and_its_singularities(self):
        result = run_case("two_circles")
        analysis = result.analysis
        self.assertEqual(
            analysis["graph"]["faces"], len(result.graph.faces)
        )
        self.assertEqual(
            analysis["medial"]["junction_count"], len(result.diagram.junctions)
        )
        for record in analysis["singularities"]:
            self.assertNotEqual(record["index"], 0)
            self.assertIsNotNone(record["session_vertex"])
        self.assertTrue(analysis["limitations"])


class PeriodicHillGeometryTests(unittest.TestCase):
    """Lock down the internal-flow fixture for the solver stage."""

    def test_classical_dimensions_profile_and_symmetry(self):
        case = cases.periodic_hill()
        bottom = case.boundary("bottom_wall").points
        self.assertAlmostEqual(float(bottom[0, 0]), 0.0)
        self.assertAlmostEqual(float(bottom[-1, 0]), 9.0)
        self.assertAlmostEqual(float(bottom[0, 1]), 1.0)
        self.assertAlmostEqual(float(bottom[-1, 1]), 1.0)
        self.assertAlmostEqual(float(case.boundary("top_wall").points[0, 1]), 3.036)
        stations = np.asarray([0.0, 0.321, 0.5, 0.714, 1.071, 1.429, 1.929, 4.5])
        heights = cases.periodic_hill_height(stations)
        mirrored = cases.periodic_hill_height(9.0 - stations)
        self.assertTrue(np.allclose(heights, mirrored, atol=1e-12))
        self.assertAlmostEqual(float(heights[0]), 1.0)
        self.assertAlmostEqual(float(heights[-1]), 0.0)
        self.assertTrue(np.all((bottom[:, 1] >= 0.0) & (bottom[:, 1] <= 1.0)))
        probes = np.asarray([0.2, 0.4, 0.6, 0.9, 1.2, 1.7, 2.0])
        expected = np.asarray(
            [0.994272, 0.925648, 0.776040, 0.526352, 0.294016, 0.030763, 0.0]
        )
        self.assertTrue(
            np.allclose(cases.periodic_hill_height(probes), expected, atol=1e-12)
        )

    def test_boundary_chains_form_one_anticlockwise_domain(self):
        case = cases.periodic_hill()
        loop = case.loop()
        self.assertTrue(np.allclose(loop[0], loop[-1]))
        self.assertTrue(np.all(np.linalg.norm(np.diff(loop, axis=0), axis=1) > 0.0))
        self.assertGreater(g2.signed_area(loop), 0.0)
        pairs = zip(case.boundaries, case.boundaries[1:] + case.boundaries[:1])
        for first, second in pairs:
            self.assertTrue(np.allclose(first.points[-1], second.points[0]))

    def test_periodic_sides_are_reciprocal_translated_copies(self):
        case = cases.periodic_hill()
        left = case.boundary("periodic_left")
        right = case.boundary("periodic_right")
        self.assertEqual(left.neighbour, right.name)
        self.assertEqual(right.neighbour, left.name)
        translation = np.asarray(case.periodic_translation)
        self.assertTrue(np.allclose(left.points[::-1] + translation, right.points))
        self.assertEqual(case.boundary("bottom_wall").kind, "wall")
        self.assertEqual(case.boundary("top_wall").kind, "wall")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ---------------------------------------------------------------------------
# Sharp-feature cavities
# ---------------------------------------------------------------------------
#
# The fixture is a hand-built four-row strip of quadrilaterals whose bottom
# wall carries a spike.  It is deliberately not a pipeline case: a cavity
# operation has to be testable on an arbitrary patch graph, and a fixture whose
# every coordinate is written down here cannot accidentally encode anything
# about a particular aerofoil.

CUSP_COLUMNS = (-2.0, -1.0, 0.0, 1.0, 2.0)
CUSP_ROWS = (1.4, 2.6, 3.6)


def cusp_rows(*, spike: float = 1.2, front: float = 1.28, ring_dip: float = 0.0):
    """Wall, front, ring and top rows of the cusp strip."""
    wall = [(x, spike if index == 2 else 0.0) for index, x in enumerate(CUSP_COLUMNS)]
    layer = [
        (x, front if index == 2 else CUSP_ROWS[0])
        for index, x in enumerate(CUSP_COLUMNS)
    ]
    ring = [
        (x, CUSP_ROWS[1] - (ring_dip if index == 2 else 0.0))
        for index, x in enumerate(CUSP_COLUMNS)
    ]
    top = [(x, CUSP_ROWS[2]) for x in CUSP_COLUMNS]
    return [wall, layer, ring, top]


def cusp_strip(
    *,
    spike: float = 1.2,
    front: float = 1.28,
    ring_dip: float = 0.0,
    translate=(0.0, 0.0),
    rotate: float = 0.0,
    scale: float = 1.0,
    reverse: bool = False,
):
    """A patch graph and matching domain with one sharp wall feature.

    ``reverse`` mirrors the column order, which is the graph-level equivalent
    of handing the same geometry in the opposite direction.
    """
    rows = cusp_rows(spike=spike, front=front, ring_dip=ring_dip)
    if reverse:
        rows = [list(reversed(row)) for row in rows]

    def place(point):
        x, y = float(point[0]) * scale, float(point[1]) * scale
        cosine, sine = math.cos(rotate), math.sin(rotate)
        return (
            cosine * x - sine * y + translate[0],
            sine * x + cosine * y + translate[1],
        )

    columns = len(rows[0])
    graph = pg.PatchGraph(euler_characteristic=1)
    keys = []
    for row_index, row in enumerate(rows):
        line = []
        for column, point in enumerate(row):
            key = ("node", row_index, column)
            graph.add_vertex(key, place(point))
            line.append(key)
        keys.append(line)
    for row_index in range(len(rows)):
        name = "wall" if row_index == 0 else ("top" if row_index == 3 else None)
        role = "wall" if row_index == 0 else ("ring" if row_index >= 2 else "front")
        for column in range(columns - 1):
            first, second = keys[row_index][column], keys[row_index][column + 1]
            graph.add_edge(
                first,
                second,
                path=np.asarray(
                    [graph.vertices[first].point, graph.vertices[second].point]
                ),
                boundary=name,
                role=role,
                provenance="cusp strip",
            )
    for row_index in range(len(rows) - 1):
        for column in range(columns):
            name = (
                "inlet"
                if column == 0
                else ("outlet" if column == columns - 1 else None)
            )
            first, second = keys[row_index][column], keys[row_index + 1][column]
            graph.add_edge(
                first,
                second,
                path=np.asarray(
                    [graph.vertices[first].point, graph.vertices[second].point]
                ),
                boundary=name,
                role="layer_spoke" if row_index == 0 else "core_spoke",
                provenance="cusp strip",
            )
    for row_index in range(len(rows) - 1):
        for column in range(columns - 1):
            graph.add_face(
                (
                    keys[row_index][column],
                    keys[row_index][column + 1],
                    keys[row_index + 1][column + 1],
                    keys[row_index + 1][column],
                ),
                role="layer" if row_index == 0 else "core",
                provenance="cusp strip",
            )
    points = [[place(item) for item in row] for row in rows]
    domain = pdm.from_chains(
        "cusp strip",
        [
            pdm.Chain("wall", "wall", points[0]),
            pdm.Chain("outlet", "outlet", [row[-1] for row in points]),
            pdm.Chain("top", "symmetry", list(reversed(points[-1]))),
            pdm.Chain("inlet", "inlet", [row[0] for row in reversed(points)]),
        ],
    )
    return graph, domain


def cusp_cavity(graph, options=None):
    """The cavity at the strip's spike, with its validation context."""
    settings = options or fan_cavity.CavityOptions()
    feature = fan_cavity.feature_corners(graph, settings)[0]["vertex"]
    cavity, reason = fan_cavity.build_cavity(
        graph, feature, depth=settings.growth_depth
    )
    if cavity is None:  # pragma: no cover - the fixture is built to have one
        raise AssertionError(reason)
    return cavity, fan_cavity.build_context(graph, cavity, None, settings)


class SegmentRelationTests(unittest.TestCase):
    """Every forbidden relation is classified, at any place and size."""

    RELATIONS = {
        "proper_crossing": (((0, 0), (1, 0)), ((0.5, -0.5), (0.5, 0.5))),
        "near_endpoint_crossing": (((0, 0), (1, 0)), ((0.002, -0.4), (0.002, 0.4))),
        "t_junction": (((0, 0), (1, 0)), ((0.4, 0.0), (0.4, 0.7))),
        "collinear_overlap": (((0, 0), (1, 0)), ((0.5, 0), (1.5, 0))),
        "touch": (((0, 0), (1, 0)), ((1, 0), (1.6, 0.8))),
        "disjoint": (((0, 0), (1, 0)), ((2, 1), (3, 2))),
    }
    EXPECTED = {
        "proper_crossing": g2.PROPER_CROSSING,
        "near_endpoint_crossing": g2.PROPER_CROSSING,
        "t_junction": g2.T_JUNCTION,
        "collinear_overlap": g2.COLLINEAR_OVERLAP,
        "touch": g2.TOUCH,
        "disjoint": g2.DISJOINT,
    }

    @staticmethod
    def place(points, translate, rotate, scale):
        rotation = np.asarray(
            [
                [math.cos(rotate), -math.sin(rotate)],
                [math.sin(rotate), math.cos(rotate)],
            ]
        )
        array = np.asarray(points, dtype=np.float64) * scale
        return array @ rotation.T + np.asarray(translate, dtype=np.float64)

    def test_relations_survive_translation_rotation_and_scaling(self):
        for label, (first, second) in self.RELATIONS.items():
            for translate in ((0.0, 0.0), (-317.25, 88.5)):
                for rotate in (0.0, 0.7, math.pi / 2, 2.9):
                    for scale in (1.0, 1.0e-3, 1.0e4):
                        with self.subTest(
                            relation=label,
                            translate=translate,
                            rotate=rotate,
                            scale=scale,
                        ):
                            a = self.place(first, translate, rotate, scale)
                            b = self.place(second, translate, rotate, scale)
                            found = g2.segment_relation(
                                a[0], a[1], b[0], b[1], tolerance=1.0e-9 * scale
                            )
                            self.assertEqual(found["kind"], self.EXPECTED[label])

    def test_a_shared_vertex_is_the_only_legitimate_contact(self):
        square = np.asarray(
            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], dtype=np.float64
        )
        spoke = np.asarray([[1.0, 0.0], [2.0, 0.0]], dtype=np.float64)
        self.assertEqual(
            g2.path_conflicts(
                square, spoke, tolerance=1e-9, shared=[(1.0, 0.0)]
            ),
            [],
        )
        undeclared = g2.path_conflicts(square, spoke, tolerance=1e-9)
        self.assertTrue(undeclared)
        self.assertEqual(undeclared[0]["kind"], g2.UNEXPECTED_TOUCH)

    def test_edges_sharing_a_vertex_are_still_checked_against_each_other(self):
        graph = pg.PatchGraph(euler_characteristic=1)
        graph.add_vertex(("a",), (0.0, 0.0))
        graph.add_vertex(("b",), (1.0, 0.0))
        graph.add_vertex(("c",), (2.0, 0.0))
        graph.add_edge(("a",), ("b",), path=[[0.0, 0.0], [1.0, 0.0]])
        graph.add_edge(("a",), ("c",), path=[[0.0, 0.0], [2.0, 0.0]])
        kinds = {item["kind"] for item in graph._crossing_problems()}
        self.assertIn("overlapping_edges", kinds)


class CavityTests(unittest.TestCase):
    def test_the_fixture_is_a_valid_graph_with_one_sharp_feature(self):
        graph, _domain = cusp_strip()
        self.assertEqual(graph.problems(), [])
        self.assertEqual(graph.euler(), 1)
        self.assertEqual(graph.total_index(), 4)
        features = fan_cavity.feature_corners(graph, fan_cavity.CavityOptions())
        self.assertEqual(len(features), 1)
        self.assertAlmostEqual(features[0]["fluid_angle"], 280.4, places=1)

    def test_the_cavity_is_the_octagon_incidence_says_it_is(self):
        graph, _domain = cusp_strip()
        cavity, _context = cusp_cavity(graph)
        self.assertEqual(cavity.size, 8)
        self.assertEqual(len(cavity.faces), 4)
        self.assertEqual(cavity.boundary[0], ("node", 0, 2))
        self.assertEqual(cavity.interior, (("node", 1, 2),))
        self.assertTrue(cavity.has_layer)

    def test_a_candidate_that_leaves_the_cavity_is_rejected_before_mutation(self):
        """Rejection names both entities and changes nothing."""
        graph, _domain = cusp_strip()
        before = graph.summary()
        options = fan_cavity.CavityOptions()
        cavity, context = cusp_cavity(graph, options)
        frame = fan_cavity.build_frame(graph, cavity)
        template = next(
            item
            for item in fan_cavity.templates(options, cavity.size)
            if item.name == "band"
        )
        # A front vertex tucked against one wall side: the edge from it to the
        # front vertex on the other side has to pass through the spike.
        faces, vertices, roles = fan_cavity.resolve(
            template, cavity, {"mid": frame.ray(0.06, 0.25)}
        )
        candidate = fan_cavity.Candidate(
            "probe", "band", 2, cavity.depth, {}, new_vertices=vertices,
            faces=faces, edge_roles=roles, face_count=len(faces),
        )
        rejections = fan_cavity.validate(candidate, context, options)
        crossings = [
            item for item in rejections if item["reason"].startswith("proper_crossing")
        ]
        self.assertTrue(crossings, [item["reason"] for item in rejections])
        self.assertEqual(len(crossings[0]["entities"]), 2)
        self.assertIsNotNone(crossings[0]["point"])
        self.assertFalse(candidate.valid)
        self.assertEqual(graph.summary(), before)

    def test_a_detour_that_crosses_an_unaffected_edge_is_rejected(self):
        """The check reaches past the cavity to the rest of the graph."""
        graph, _domain = cusp_strip()
        options = fan_cavity.CavityOptions()
        cavity, context = cusp_cavity(graph, options)
        frame = fan_cavity.build_frame(graph, cavity)
        template = next(
            item
            for item in fan_cavity.templates(options, cavity.size)
            if item.name == "band"
        )
        faces, vertices, roles = fan_cavity.resolve(
            template, cavity, {"mid": frame.ray(0.5, 0.5)}
        )
        key = pg.PatchGraph.edge_key(("node", 0, 2), ("cavity", "mid", "node", 0, 2))
        spoke = np.asarray(
            [
                graph.vertices[("node", 0, 2)].point,
                # a deliberate detour out through the left neighbour's band
                graph.vertices[("node", 0, 0)].point
                + np.asarray([0.0, 0.7]),
                list(vertices.values())[0],
            ]
        )
        candidate = fan_cavity.Candidate(
            "detour", "band", 2, cavity.depth, {}, new_vertices=vertices,
            faces=faces, edge_roles=roles,
            edge_paths={key: spoke if key[0] == ("node", 0, 2) else spoke[::-1]},
            face_count=len(faces),
        )
        rejections = fan_cavity.validate(candidate, context, options)
        reasons = {item["reason"] for item in rejections}
        self.assertTrue(
            any(reason.endswith("_with_graph_edge") for reason in reasons), reasons
        )

    def test_local_quality_alone_would_choose_a_crossing_candidate(self):
        """The applied candidate is not the locally best-looking one."""
        graph, _domain = cusp_strip()
        options = fan_cavity.CavityOptions()
        cavity, context = cusp_cavity(graph, options)
        frame = fan_cavity.build_frame(graph, cavity)
        template = next(
            item
            for item in fan_cavity.templates(options, cavity.size)
            if item.name == "band"
        )

        def build(points, paths=None):
            faces, vertices, roles = fan_cavity.resolve(template, cavity, points)
            candidate = fan_cavity.Candidate(
                "probe", "band", 2, cavity.depth, {}, new_vertices=vertices,
                faces=faces, edge_roles=roles, edge_paths=paths or {},
                face_count=len(faces),
            )
            placed = {
                **{key: graph.vertices[key].point for key in cavity.boundary},
                **vertices,
            }
            local = min(
                fan_cavity.face_quality(
                    np.asarray([placed[key] for key in corners])[::-1]
                    if g2.polygon_area(
                        np.asarray([placed[key] for key in corners])
                    ) < 0.0
                    else np.asarray([placed[key] for key in corners])
                )
                for corners in faces
            )
            fan_cavity.validate(candidate, context, options)
            return candidate, local

        # The same placement, once honestly and once with one spoke routed out
        # through the neighbouring band.  Both have the same four faces, so a
        # four-corner measure cannot tell them apart at all.
        points = {"mid": frame.ray(0.5, 0.5)}
        honest, honest_quality = build(points)
        key = pg.PatchGraph.edge_key(("node", 0, 2), ("cavity", "mid", "node", 0, 2))
        detour = np.asarray(
            [
                graph.vertices[("node", 0, 2)].point,
                graph.vertices[("node", 0, 0)].point + np.asarray([0.0, 0.7]),
                points["mid"],
            ]
        )
        crossing, crossing_quality = build(points, {key: detour})
        self.assertAlmostEqual(honest_quality, crossing_quality, places=12)
        self.assertTrue(honest.valid)
        self.assertFalse(crossing.valid)
        # And the stage really does apply a globally valid one.
        result = fan_cavity.repair(graph, None, options=options)
        self.assertTrue(result.applied)
        self.assertEqual(result.graph.problems(), [])

    def test_an_accepted_cavity_preserves_euler_and_index(self):
        """The replacement is a valid planar topology and a valid session."""
        graph, domain = cusp_strip()
        options = fan_cavity.CavityOptions()
        result = fan_cavity.repair(graph, domain, options=options)
        self.assertEqual(len(result.applied), 1)
        after = result.graph
        self.assertEqual(after.problems(), [])
        self.assertEqual(after.euler(), graph.euler())
        self.assertEqual(after.total_index(), graph.total_index())
        self.assertGreater(len(after.faces), len(graph.faces))
        record = result.applied[0]
        self.assertEqual(record["index_sum_before"], record["index_sum_after"])
        metric = sizing.SizeMetric(
            sizing.SizingOptions(), domain.scale,
            [chain.points for chain in domain.wall_chains()],
        )
        counts = sizing.assign_counts(after, metric, sizing.SizingOptions())
        model, _identifiers, _refused = session_emit.build_model_from_graph(
            after, domain, counts=counts.counts, reference_curves=False
        )
        model.validate()
        self.assertEqual(len(model.blocks), len(after.faces))

    def test_rejection_is_atomic_and_the_stage_is_deterministic(self):
        """Two runs agree, and a refused cavity leaves the graph alone."""
        first, _domain = cusp_strip()
        second, _other = cusp_strip()
        options = fan_cavity.CavityOptions()
        one = fan_cavity.repair(first, None, options=options)
        two = fan_cavity.repair(second, None, options=options)
        self.assertEqual(one.graph.summary(), two.graph.summary())
        self.assertEqual(
            [record["applied"] for record in one.cavities],
            [record["applied"] for record in two.cavities],
        )
        # Only the copy is ever written to.
        self.assertEqual(len(first.faces), 12)
        # A cavity with no admissible alternative changes nothing at all.
        blocked = fan_cavity.CavityOptions(
            choices=(("node_0_2", "through_fan3"),), minimum_quality=0.9
        )
        graph, _ = cusp_strip()
        before = graph.summary()
        refused = fan_cavity.repair(graph, None, options=blocked)
        self.assertEqual(refused.applied, [])
        self.assertEqual(refused.graph.summary(), before)
        self.assertTrue(refused.cavities[0]["candidates"])

    def test_two_nearby_features_cannot_consume_the_same_faces(self):
        """The second cavity is refused, and the result is still valid."""
        result = run_case("narrow_gap_tip", CAVITY_FAST)
        records = result.cavity.cavities
        self.assertGreaterEqual(len(records), 2)
        applied = [item for item in records if item.get("applied")]
        self.assertEqual(len(applied), 1)
        # Every other feature in reach of the one that was rewritten is
        # refused, and says which of the two reasons refused it.
        refused = [item for item in records if not item.get("applied")]
        self.assertTrue(refused)
        for item in refused:
            self.assertTrue(item.get("rejection"))
            self.assertTrue(
                "already rewritten" in item["rejection"]
                or "removed the reason" in item["rejection"]
                or "improves on the construction" in item["rejection"],
                item["rejection"],
            )
        self.assertEqual(result.problems, [])
        self.assertTrue(result.topology_valid)

    def test_the_cavity_stage_repairs_what_it_is_there_for(self):
        """Without it the narrow-gap fixture is not a valid topology."""
        names, loops = cases.narrow_gap_tip()
        without = pipeline.run_external(
            names,
            loops,
            dataclasses.replace(
                CAVITY_FAST, cavity=fan_cavity.CavityOptions(enabled=False)
            ),
        )
        self.assertFalse(without.topology_valid)
        self.assertTrue(
            any(item["kind"] == "non_convex_face" for item in without.problems)
        )
        with_stage = run_case("narrow_gap_tip", CAVITY_FAST)
        self.assertTrue(with_stage.topology_valid)
        self.assertTrue(with_stage.untangled)

    def test_a_candidate_is_equivalent_under_rigid_motion_and_scaling(self):
        """Same template, same parameters, same score."""
        reference, _domain = cusp_strip()
        options = fan_cavity.CavityOptions()
        base = fan_cavity.repair(reference, None, options=options)
        variants = {
            "translated": {"translate": (13.0, -4.0)},
            "rotated": {"rotate": math.radians(23.0)},
            "scaled": {"scale": 250.0},
            "reversed": {"reverse": True},
            "all_of_them": {
                "translate": (-9.0, 6.5),
                "rotate": math.radians(-71.0),
                "scale": 0.004,
                "reverse": True,
            },
        }
        expected = base.cavities[0]
        for label, kwargs in variants.items():
            with self.subTest(variant=label):
                graph, _other = cusp_strip(**kwargs)
                result = fan_cavity.repair(graph, None, options=options)
                record = result.cavities[0]
                self.assertEqual(
                    record["applied_template"], expected["applied_template"]
                )
                self.assertEqual(
                    len(result.graph.faces), len(base.graph.faces)
                )
                chosen = next(
                    item
                    for item in record["candidates"]
                    if item["template"] == record["applied_template"]
                )
                reference_choice = next(
                    item
                    for item in expected["candidates"]
                    if item["template"] == expected["applied_template"]
                )
                self.assertAlmostEqual(
                    chosen["score"], reference_choice["score"], places=6
                )
                for name, value in reference_choice["parameters"].items():
                    self.assertAlmostEqual(
                        chosen["parameters"][name], value, places=6
                    )

    def test_a_fan_that_merges_the_count_components_is_refused_in_a_band(self):
        """The structural reason a contained fan is not a boundary layer."""
        graph, _domain = cusp_strip()
        options = fan_cavity.CavityOptions(choices=(("node_0_2", "fan3"),))
        result = fan_cavity.repair(graph, None, options=options)
        record = result.cavities[0]
        self.assertIsNone(record["applied"])
        reasons = {
            item.get("reason")
            for candidate in record["candidates"]
            for item in candidate["rejections"]
        }
        self.assertIn("count_component_coupling", reasons)
        allowed = fan_cavity.CavityOptions(
            choices=(("node_0_2", "fan3"),), allow_count_coupling=True
        )
        other, _ = cusp_strip()
        permitted = fan_cavity.repair(other, None, options=allowed)
        fan = permitted.cavities[0]["candidates"][0]
        self.assertEqual(fan["template"], "fan3")
        self.assertTrue(fan["accepted"])
        self.assertEqual(fan["rejections"], [])

    def test_a_tangled_cavity_is_reported_rather_than_filled(self):
        graph, _domain = cusp_strip(ring_dip=2.0)
        cavity, reason = fan_cavity.build_cavity(
            graph, ("node", 0, 2), depth=2
        )
        self.assertIsNone(cavity)
        self.assertIn("not simple", reason)


# ---------------------------------------------------------------------------
# Truthful acceptance
# ---------------------------------------------------------------------------


class AcceptanceTests(unittest.TestCase):
    """What the report claims is what was measured."""

    def sample_report(self, **limits):
        result = run_case("two_circles")
        return grid_quality.evaluate(
            result.model,
            options=grid_quality.GridOptions(**limits),
            layer_blocks=result.layer_blocks,
            first_width=result.metric.first,
        ), result

    def test_each_declared_limit_fails_on_its_own(self):
        result = run_case("two_circles")
        loose = dict(
            max_non_orthogonality=None,
            max_skewness=None,
            max_aspect_ratio=None,
            max_interface_ratio=None,
            max_wall_misalignment=None,
            max_first_width_error=None,
        )

        def report(**overrides):
            return grid_quality.evaluate(
                result.model,
                options=grid_quality.GridOptions(**{**loose, **overrides}),
                layer_blocks=result.layer_blocks,
                first_width=result.metric.first,
            )

        everything_off = report()
        self.assertTrue(everything_off.within_quality_targets)
        self.assertEqual(everything_off.quality_failures, [])
        self.assertEqual(
            everything_off.limits.described()["max_skewness"], "disabled"
        )
        observed = {
            "max_non_orthogonality": (
                "maximum_non_orthogonality_degrees",
                everything_off.maximum_non_orthogonality.value,
            ),
            "max_skewness": (
                "maximum_equiangle_skewness",
                everything_off.maximum_skewness.value,
            ),
            "max_aspect_ratio": (
                "maximum_aspect_ratio_unintended",
                everything_off.maximum_aspect_ratio.value,
            ),
            "max_wall_misalignment": (
                "maximum_wall_misalignment_degrees",
                everything_off.maximum_wall_misalignment.value,
            ),
            "max_first_width_error": (
                "maximum_first_cell_width_error",
                everything_off.maximum_first_width_error.value,
            ),
            "max_interface_ratio": (
                "maximum_interface_size_ratio",
                float(everything_off.interface["maximum_size_ratio"]),
            ),
        }
        for field_name, (metric, value) in observed.items():
            with self.subTest(limit=field_name):
                limit = 0.5 * value if value > 0.0 else -1.0
                only = report(**{field_name: limit})
                self.assertFalse(only.within_quality_targets)
                self.assertEqual(len(only.quality_failures), 1)
                failure = only.quality_failures[0].described()
                self.assertEqual(failure["metric"], metric)
                self.assertAlmostEqual(failure["observed"], value, places=9)
                self.assertAlmostEqual(failure["limit"], limit, places=9)
                self.assertEqual(failure["comparison"], "at_most")
                self.assertGreater(failure["severity"], 0.0)
                # Passing it is equally decisive.
                generous = report(**{field_name: 4.0 * max(value, 1.0)})
                self.assertTrue(generous.within_quality_targets)

    def test_admissible_is_topology_and_tangling_only(self):
        report, result = self.sample_report(max_skewness=-1.0)
        self.assertFalse(report.within_quality_targets)
        self.assertTrue(report.untangled)
        self.assertTrue(report.admissible)
        described = report.described()
        self.assertTrue(described["admissible"])
        self.assertFalse(described["within_quality_targets"])
        self.assertEqual(described["limits"]["max_skewness"], -1.0)
        invalid = grid_quality.evaluate(
            result.model,
            layer_blocks=result.layer_blocks,
            topology_valid=False,
        )
        self.assertFalse(invalid.admissible)
        self.assertTrue(invalid.untangled)

    def test_the_periodic_hill_states_the_targets_it_misses(self):
        """Valid and untangled, and explicit about the rest."""
        result = run_internal("periodic_hill")
        self.assertTrue(result.topology_valid)
        self.assertTrue(result.untangled)
        self.assertTrue(result.admissible)
        self.assertEqual(result.grid.inverted_cells, 0)
        self.assertFalse(result.within_quality_targets)
        self.assertFalse(result.resolved)
        missed = {
            failure.described()["metric"] for failure in result.quality_failures
        }
        # The two the README records; the coarse research sizing used here can
        # add more, and every one of them has to be a real overshoot.
        self.assertLessEqual(
            {"maximum_interface_size_ratio", "maximum_first_cell_width_error"},
            missed,
        )
        self.assertNotIn("maximum_equiangle_skewness", missed)
        self.assertNotIn("maximum_non_orthogonality_degrees", missed)
        for failure in result.quality_failures:
            described = failure.described()
            self.assertGreater(described["observed"], described["limit"])
        self.assertEqual(
            result.analysis["acceptance"]["within_quality_targets"], False
        )
        self.assertEqual(result.analysis["acceptance"]["admissible"], True)

    def test_a_below_target_candidate_is_still_written_out(self):
        """The documented session and exit policy, both ways round."""
        import research_cli

        result = run_internal("periodic_hill")
        self.assertTrue(result.admissible)
        self.assertFalse(result.resolved)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            arguments = argparse.Namespace(
                session=root / "nested" / "session.json",
                json=root / "nested" / "analysis.json",
                output=None,
                session_render=None,
                block_mesh_dict=root / "nested" / "blockMeshDict",
                plot_width=400,
            )
            written = research_cli.write_artifacts(result, arguments)
            self.assertIn(arguments.session, written)
            self.assertIn(arguments.block_mesh_dict, written)
            self.assertTrue(arguments.session.exists())
            self.assertNotIn("session_not_written", result.analysis)
            below = result.analysis["session"]["below_quality_targets"]
            self.assertTrue(below)
            self.assertTrue(all(item["observed"] > item["limit"] for item in below))

        broken = run_internal("periodic_hill", fresh=True)
        broken.problems.append({"kind": "crossing_edges", "edges": []})
        self.assertFalse(broken.topology_valid)
        self.assertFalse(broken.admissible)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            arguments = argparse.Namespace(
                session=root / "session.json",
                json=None,
                output=None,
                session_render=None,
                block_mesh_dict=None,
                plot_width=400,
            )
            written = research_cli.write_artifacts(broken, arguments)
            self.assertEqual(written, [])
            self.assertFalse((root / "session.json").exists())
            self.assertIn("session_not_written", broken.analysis)

    def test_the_cavity_stage_is_visible_to_an_agent(self):
        """Candidates and their rejections reach the move vocabulary."""
        result = run_case("narrow_gap_tip", CAVITY_FAST)
        report = agent_ops.candidate_report(result)
        cavity_moves = [
            item
            for item in report["candidates"]
            if item["kind"] == "wall_feature_cavity"
        ]
        self.assertTrue(cavity_moves)
        self.assertTrue(any(item["implemented"] for item in cavity_moves))
        refused = [item for item in cavity_moves if not item["implemented"]]
        self.assertTrue(all(item["rejection"] for item in refused))
        chosen = next(item for item in cavity_moves if item["implemented"])
        self.assertIn("cavity.choices", chosen["option_delta"])
        move = agent_ops.find_move(result, chosen["id"])
        options = move_module.apply(result.options, move)
        self.assertTrue(options.cavity.choices)
        described = result.analysis["sharp_feature_cavities"]
        self.assertTrue(described["cavities"])
        for record in described["cavities"]:
            self.assertIn("opened_because", record)
            self.assertIn("fluid_angle_degrees", record)


# ---------------------------------------------------------------------------
# Regression fixtures for the 30P30N failure modes
# ---------------------------------------------------------------------------
#
# Each fixture reproduces one of the two defects the acceptance geometry still
# shows, in a body small enough to run in seconds.  Each carries a stable
# statement about the geometry and about where the pipeline locates its
# failure, plus an ``expectedFailure`` on admissibility: the moment the
# construction handles the fixture that test becomes an unexpected success,
# which unittest reports as a failure of the run, so the flip cannot go
# unnoticed.


def _fluid_angles(loop) -> np.ndarray:
    closed = np.vstack([loop, loop[:1]])
    sign = 1.0 if g2.signed_area(closed) > 0.0 else -1.0
    return np.degrees(layer_module.fluid_angles(closed, sign))


class RegressionFixtureTests(unittest.TestCase):
    def test_the_peanut_has_only_reflex_sharp_features(self):
        """Two 90 degree waist corners and nothing else sharper than 20 degrees."""
        _names, loops = cases.peanut_body()
        angles = _fluid_angles(np.asarray(loops[0]))
        self.assertEqual(int(np.sum(angles < 170.0)), 2)
        self.assertEqual(int(np.sum(angles > 200.0)), 0)
        self.assertGreater(float(np.min(angles)), 85.0)
        self.assertLess(float(np.min(angles)), 95.0)

    def test_the_peanut_failure_is_located_at_the_reflex_corners(self):
        """The tangent-disk feature size is zero at the waist; the far clearance is not.

        While the band could not be built, the failure named the two waist
        gates; now that it can, the front keeps a positive height there.
        """
        _names, loops = cases.peanut_body()
        loop = np.asarray(loops[0])
        closed = np.vstack([loop, loop[:1]])
        angles = _fluid_angles(loop)
        reflex = np.where(angles < 170.0)[0]
        sign = 1.0 if g2.signed_area(closed) > 0.0 else -1.0
        normals = -sign * g2.vertex_normals(closed, closed=True)
        size = layer_module.local_feature_size(
            [closed], closed[reflex], normals[reflex], upper=1.0
        )
        self.assertTrue(np.all(size == 0.0), size)
        result = run_case("peanut_body", CAVITY_FAST)
        if result.admissible:
            front = next(iter(result.assembly.fronts.values()))
            self.assertGreater(front.minimum_height, 0.0)
            return
        failures = [
            item
            for item in result.failures
            if item.get("stage") == "boundary_layer_front"
        ]
        self.assertTrue(failures, result.failures)
        reported = np.asarray(
            [point for item in failures for point in item["gate_points"]]
        )
        for corner in closed[reflex]:
            gap = float(np.min(np.linalg.norm(reported - corner, axis=1)))
            self.assertLess(gap, 1.0e-3 * result.scale)

    def test_the_peanut_band_is_admissible(self):
        """A front survives a concave wall corner: it mitres instead of folding."""
        result = run_case("peanut_body", CAVITY_FAST)
        self.assertTrue(result.admissible, (result.failures, result.problems[:2]))
        self.assertEqual(result.grid.inverted_cells, 0)
        front = next(iter(result.assembly.fronts.values()))
        self.assertGreater(front.minimum_height, 0.0)
        # Both reflex corners are pinned gates whose spoke runs along the
        # bisector to the level set's mitre.
        _names, loops = cases.peanut_body()
        loop = np.asarray(loops[0])
        corners = loop[np.where(_fluid_angles(loop) < 170.0)[0]]
        reflex_gates = [
            cut
            for cuts in result.layout.cuts
            for cut in cuts
            if cut.anchor.kind == "reflex" and cut.pinned
        ]
        self.assertEqual(len(reflex_gates), 2)
        for cut in reflex_gates:
            gap = float(np.min(np.linalg.norm(corners - cut.wall_point, axis=1)))
            self.assertLess(gap, 1.0e-6 * result.scale)

    def test_the_skimming_tail_is_a_narrow_slot(self):
        """One sharp tip, one smooth hull, a gap near one percent of the scale."""
        _names, loops = cases.skimming_tail()
        tail, hull = (np.asarray(loop) for loop in loops)
        self.assertEqual(int(np.sum(_fluid_angles(tail) > 200.0)), 1)
        self.assertEqual(int(np.sum(_fluid_angles(hull) > 200.0)), 0)
        result = run_case("skimming_tail", CAVITY_FAST)
        self.assertEqual(result.domain.problems(), [])
        closed_tail = np.vstack([tail, tail[:1]])
        closed_hull = np.vstack([hull, hull[:1]])
        gap = min(
            float(np.min(g2.closest_on_polyline(closed_hull, closed_tail).distance)),
            float(np.min(g2.closest_on_polyline(closed_tail, closed_hull).distance)),
        ) / result.scale
        self.assertGreater(gap, 0.005)
        self.assertLess(gap, 0.03)
        # The medial branch between the two bodies runs through the slot.
        self.assertEqual(len(result.diagram.junctions), 2)
        if not result.admissible:
            located = any(
                item.get("stage") == "boundary_layer_front"
                for item in result.failures
            ) or bool(result.problems)
            self.assertTrue(located, (result.failures, result.problems))

    @unittest.expectedFailure
    def test_the_skimming_tail_is_not_yet_admissible(self):
        """Flip this test when both bands fit into the slot."""
        result = run_case("skimming_tail", CAVITY_FAST)
        self.assertTrue(result.admissible, (result.failures, result.problems[:2]))


class RoleAndStructureTests(unittest.TestCase):
    def test_replacement_faces_take_their_role_from_their_edges(self):
        """A seam wedge is not a band block; a rebuilt core patch is not either."""
        graph, domain = cusp_strip()
        result = fan_cavity.repair(
            graph, domain, options=fan_cavity.CavityOptions()
        )
        self.assertEqual(len(result.applied), 1)
        after = result.graph
        for face in after.faces:
            roles = {after.edges[key].role for key in after.face_edges(face)}
            expected = "layer" if {"wall", "front"} <= roles else "core"
            self.assertEqual(face.role, expected, (face.corners, sorted(roles)))
        fresh = after.faces[-result.applied[0]["new_faces"] :]
        self.assertEqual({face.role for face in fresh}, {"layer", "core"})

    def test_every_pipeline_face_role_agrees_with_its_edges(self):
        """The producers and the cavity stage use one definition of a band block."""
        for name in ("narrow_gap_tip", "sharp_bodies"):
            result = run_case(name, CAVITY_FAST)
            graph = result.graph
            for face in graph.faces:
                self.assertEqual(
                    face.role,
                    fan_cavity.face_role(graph, face.corners),
                    (name, face.corners, face.provenance),
                )

    def test_sizing_structure_is_count_independent_and_dimensionless(self):
        """Length ratios and role couplings come from the topology alone."""
        result = run_case("two_circles")
        structure = pg.sizing_structure(result.graph)
        self.assertEqual(
            structure["components"], len(pg.constraint_components(result.graph))
        )
        self.assertEqual(structure["tangential_normal_couplings"], 0)
        self.assertEqual(structure["band_core_depth_couplings"], 0)
        self.assertIn("sizing_structure", result.analysis["graph"])
        names, loops = cases.two_circles()
        scaled = pipeline.run_external(
            names, cases.transform(loops, scale=1000.0), FAST
        )
        other = pg.sizing_structure(scaled.graph)
        # The topology signature is identical under scaling; the relaxed
        # coordinates are only comparable, as the invariance tests state.
        self.assertEqual(
            structure["band_core_depth_couplings"],
            other["band_core_depth_couplings"],
        )
        self.assertLessEqual(
            abs(structure["maximum_length_ratio"] - other["maximum_length_ratio"])
            / structure["maximum_length_ratio"],
            0.05,
        )

    def test_a_seam_wedge_ties_the_band_count_to_the_core_depth(self):
        """The structural report names the coupling before any count is chosen."""
        result = run_case("narrow_gap_tip", CAVITY_FAST)
        self.assertTrue(
            any(item.get("applied_template") == "seam" for item in result.cavity.applied)
        )
        structure = pg.sizing_structure(result.graph)
        self.assertGreaterEqual(structure["band_core_depth_couplings"], 1)
        worst = structure["worst"][0]
        self.assertTrue(worst["ties_band_to_core_depth"])
        self.assertEqual(sorted(worst["roles"]), ["core_spoke", "layer_spoke"])
        self.assertGreater(worst["length_ratio"], 10.0)


class RasterInvarianceTests(unittest.TestCase):
    """The raster decides connectivity only; the block graph must not follow it.

    Every synthetic fixture gives the same graph at widths 400, 700 and 1000.
    The 30P30N acceptance geometry does not: at width 1400 it has 106 faces and
    18 singularities against 115 and 24 at width 700, with the same four
    junctions.  Until that is reproduced in a small fixture, this test guards
    the property where it holds.
    """

    def test_the_block_graph_does_not_follow_the_raster(self):
        for name in ("two_circles", "sharp_bodies"):
            names, loops = cases.CASES[name]()
            summaries = []
            for width in (300, 600):
                options = dataclasses.replace(
                    CAVITY_FAST, grid_width=width, evaluate_grid=False
                )
                result = pipeline.run_external(names, loops, options)
                self.assertTrue(result.topology_valid, (name, width, result.problems))
                summaries.append(result.graph.summary())
            with self.subTest(case=name):
                self.assertEqual(summaries[0], summaries[1])


class LevelSetFrontTests(unittest.TestCase):
    """The front is the eroded domain's boundary, not a per-vertex offset."""

    @staticmethod
    def _corner_wall(height_of_wall: float = 1.0, samples: int = 41):
        """An L-shaped wall: two unit edges meeting at a 90 degree reflex corner.

        The fluid occupies the quadrant x > 0, y > 0 seen from the corner at the
        origin, so the wall runs down the y axis and out along the x axis.
        """
        down = np.column_stack((np.zeros(samples), np.linspace(height_of_wall, 0.0, samples)))
        out = np.column_stack((np.linspace(0.0, height_of_wall, samples), np.zeros(samples)))
        return np.vstack([down, out[1:]])

    def test_far_clearance_is_finite_at_a_reflex_corner(self):
        _names, loops = cases.peanut_body()
        loop = g2.close_loop(loops[0], 0.0)
        sign = 1.0 if g2.signed_area(loop) > 0.0 else -1.0
        normals = -sign * g2.vertex_normals(loop, closed=True)
        angles = layer_module.fluid_angles(loop, sign)
        at = np.concatenate((angles, angles[:1]))
        reflex = np.where(np.degrees(angles) < 170.0)[0]
        disk = layer_module.local_feature_size([loop], loop, normals, upper=2.0)
        far = layer_module.far_clearance(
            [], loop, normals, fluid_angles_at=at, closed=True, upper=2.0
        )
        self.assertTrue(np.all(disk[reflex] == 0.0))
        self.assertTrue(np.all(far[reflex] > 1.0), far[reflex])

    def test_far_clearance_matches_the_tangent_disk_against_another_body(self):
        first = g2.orient_anticlockwise(
            g2.close_loop(cases.circle((-0.6, 0.0), 0.5, 200), 0.0)
        )
        second = g2.orient_anticlockwise(
            g2.close_loop(cases.circle((0.6, 0.0), 0.5, 200), 0.0)
        )
        normals = -1.0 * g2.vertex_normals(first, closed=True)
        angles = layer_module.fluid_angles(first, 1.0)
        at = np.concatenate((angles, angles[:1]))
        disk = layer_module.local_feature_size([first, second], first, normals, upper=1.0)
        far = layer_module.far_clearance(
            [second], first, normals, fluid_angles_at=at, closed=True, upper=1.0
        )
        towards = int(np.argmax(first[:-1, 0]))
        # The gap is 0.2, so the largest disk between the bodies has radius 0.1.
        self.assertAlmostEqual(float(far[towards]), 0.1, delta=0.01)
        self.assertAlmostEqual(float(far[towards]), float(disk[towards]), delta=1e-6)

    def test_a_reflex_corner_shadow_maps_onto_the_mitre(self):
        wall = self._corner_wall()
        # Fluid on the left of the path (down the y axis, then along x), so a
        # left turn closes the fluid sector: fluid angle = pi - turning.
        normals = g2.vertex_normals(wall, closed=False)
        turning = g2.turning_angles(wall, closed=False)
        at = np.concatenate(([math.pi], math.pi - turning, [math.pi]))
        height = 0.3
        heights = np.full(len(wall), height)
        offset = g2.offset_polyline(wall, heights, closed=False, limit=1.5)
        front = layer_module.trim_to_level_set(
            [wall], wall, offset, heights, normals, at, closed=False
        )
        distance = g2.distance_to_polyline(wall, front)
        self.assertTrue(np.all(distance >= 0.98 * height - 1e-9), distance.min())
        self.assertTrue(g2.is_simple(front, closed=False))
        corner = int(np.argmin(np.degrees(at)))
        self.assertTrue(np.allclose(front[corner], (height, height), atol=1e-6))
        # Every wall vertex within one height of the corner maps to the mitre.
        stations = g2.cumulative_length(wall)
        shadow = np.abs(stations - stations[corner]) < height - 1e-9
        self.assertTrue(np.all(np.linalg.norm(front[shadow] - (height, height), axis=1) < 1e-6))
        outside = np.abs(stations - stations[corner]) > height + 0.05
        self.assertTrue(np.all(np.abs(distance[outside] - height) < 1e-6))

    def test_the_shadow_cap_keeps_a_gate_out_of_the_shadow(self):
        wall = self._corner_wall()
        turning = g2.turning_angles(wall, closed=False)
        at = np.concatenate(([math.pi], math.pi - turning, [math.pi]))
        corner = int(np.argmin(at))
        stations = g2.cumulative_length(wall)
        neighbour = corner + 8  # a gate 0.2 along the wall from the corner
        cap = np.full(len(wall), 1.0)
        capped = layer_module.reflex_shadow_cap(
            cap, stations, at, [0, corner, neighbour, len(wall) - 1], closed=False
        )
        gap = float(stations[neighbour] - stations[corner])
        self.assertAlmostEqual(float(capped[corner]), 0.9 * gap * math.tan(0.5 * float(at[corner])), places=9)
        self.assertEqual(float(capped[corner + 1]), 1.0)


class ConcaveCurvatureCapTests(unittest.TestCase):
    """A smooth bend towards the fluid is capped at its radius of curvature."""

    def test_a_bowl_is_capped_and_a_corner_shadow_is_not(self):
        # A wall running along the x axis with a semicircular bowl of radius 0.5
        # dipping towards the fluid (which lies above the wall, on its left).
        radius = 0.5
        theta = np.linspace(math.pi, 0.0, 61)
        bowl = np.column_stack((radius * np.cos(theta), -radius * np.sin(theta)))
        left = np.column_stack((np.linspace(-2.0, -radius, 31)[:-1], np.zeros(30)))
        right = np.column_stack((np.linspace(radius, 2.0, 31)[1:], np.zeros(30)))
        wall = np.vstack([left, bowl, right])
        normals = g2.vertex_normals(wall, closed=False)
        turning = g2.turning_angles(wall, closed=False)
        angles = np.concatenate(([math.pi], math.pi - turning, [math.pi]))
        arclength = g2.cumulative_length(wall)
        far = np.full(len(wall), 10.0)
        cap = np.full(len(wall), 1.0)
        capped = layer_module.concave_curvature_cap(
            cap, wall, normals, arclength, angles, far,
            closed=False, clearance_fraction=0.35, curvature_fraction=0.8, upper=10.0,
        )
        bottom = int(np.argmin(wall[:, 1]))
        self.assertAlmostEqual(float(capped[bottom]), 0.8 * radius, delta=0.02)
        # The flat parts are not bent and keep their cap.
        self.assertEqual(float(capped[5]), 1.0)
        # A reflex corner's shadow is exempt: the L wall from the level-set
        # tests keeps its far-clearance height right up to the corner.
        corner_wall = LevelSetFrontTests._corner_wall()
        normals = g2.vertex_normals(corner_wall, closed=False)
        turning = g2.turning_angles(corner_wall, closed=False)
        angles = np.concatenate(([math.pi], math.pi - turning, [math.pi]))
        arclength = g2.cumulative_length(corner_wall)
        far = np.full(len(corner_wall), 10.0)
        cap = np.full(len(corner_wall), 0.3)
        capped = layer_module.concave_curvature_cap(
            cap, corner_wall, normals, arclength, angles, far,
            closed=False, clearance_fraction=0.35, curvature_fraction=0.8, upper=10.0,
        )
        corner = int(np.argmin(angles))
        near = np.abs(arclength - arclength[corner]) < 0.3
        self.assertTrue(np.all(capped[near] == 0.3), capped[near])
