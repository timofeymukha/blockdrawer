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


def run_case(name: str, options=None) -> pipeline.PipelineResult:
    names, loops = cases.CASES[name]()
    return pipeline.run_external(names, loops, options or FAST)


def run_internal(name: str, options=None) -> pipeline.PipelineResult:
    domain = pdm.from_internal_case(cases.INTERNAL_CASES[name]())
    return pipeline.run_internal(domain, options or FAST)


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
        self.assertTrue(result.resolved, result.failures or result.problems)
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
                self.assertTrue(result.resolved, result.failures or result.problems)
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
        self.assertTrue(result.resolved, result.failures or result.problems)
        seams = [
            record
            for record in result.assembly.seams
            if record.get("applied", True)
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
        self.assertTrue(result.resolved, result.failures or result.problems)
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
        self.assertTrue(result.resolved)
        self._round_trip(result)

    def test_internal_session_round_trip_render_and_export(self):
        from blockdrawer.foam import block_mesh_dict

        result = run_internal("periodic_hill")
        self.assertTrue(result.resolved, result.failures or result.problems)
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
        for name in QUICK_CASES:
            with self.subTest(case=name):
                result = run_case(name)
                self.assertTrue(
                    result.resolved,
                    (result.errors, result.failures, result.problems[:2]),
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
            self.assertTrue(result.resolved)
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
        self.assertTrue(result.resolved, result.failures or result.problems)
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
        result = run_case("two_circles")
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
