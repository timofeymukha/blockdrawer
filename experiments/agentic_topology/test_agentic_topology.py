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
import voronoi_graph
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


def _no_crash(result):
    """A trial that raised something other than a refusal is a defect."""
    if result.crashes:
        raise AssertionError(f"a trial crashed: {result.crashes[0]['error']}")
    return result


def run_case(name: str, options=None, *, fresh: bool = False):
    settings = options or FAST
    key = ("external", name, repr(settings))
    if fresh or key not in _RUNS:
        names, loops = cases.CASES[name]()
        result = _no_crash(pipeline.run_external(names, loops, settings))
        if fresh:
            return result
        _RUNS[key] = result
    return _RUNS[key]


def run_internal(name: str, options=None, *, fresh: bool = False):
    settings = options or FAST
    key = ("internal", name, repr(settings))
    if fresh or key not in _RUNS:
        domain = pdm.from_internal_case(cases.INTERNAL_CASES[name]())
        result = _no_crash(pipeline.run_internal(domain, settings))
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
    def test_a_reflex_join_on_a_guide_is_named(self):
        # The lower guide of a backward-facing step, in the loop's
        # anticlockwise direction: upstream wall, step face, downstream wall.
        # Labels are per segment, as ``detect`` builds its sides.
        points = np.asarray(
            [[-1.0, 0.0], [-0.5, 0.0], [0.0, 0.0], [0.0, -0.5], [0.0, -1.0], [0.5, -1.0], [1.0, -1.0]]
        )
        labels = ("up", "up", "step", "step", "down", "down")
        side = sweep_module.Side(points, labels)

        found = sweep_module.reflex_joins(side)

        self.assertEqual(
            [(item["between"], round(item["fluid_angle_degrees"])) for item in found],
            [(["up", "step"], 270)],
        )
        self.assertEqual(found[0]["point"], [0.0, 0.0])
        # The step's foot is a convex join and is not reported; a duplicated
        # join point does not hide the corner.
        doubled = np.asarray([[-1.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, -1.0], [1.0, -1.0]])
        self.assertEqual(
            [item["between"] for item in sweep_module.reflex_joins(
                sweep_module.Side(doubled, ("up", "step", "step", "down"))
            )],
            [["up", "step"]],
        )
        failure = sweep_module._with_reflex_cause(
            {"stage": "sweep_correspondence", "reason": "ribs cross"}, found
        )
        self.assertIn("up/step (270 degrees of fluid)", failure["reason"])
        self.assertIn("build it by hand", failure["reason"])
        self.assertEqual(failure["reflex_joins"], found)

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
        self.assertEqual(result.graph.boundary_roles, {
            chain.name: chain.role for chain in result.domain.chains()
        })
        self.assertEqual(pg.sizing_structure(result.graph)["wall_tangential_normal_couplings"], 0)
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
                self.assertEqual(result.resolved, reference.resolved)
                self.assertEqual(
                    result.within_shape_targets, reference.within_shape_targets
                )
                self.assertEqual(result.sizing_feasible, reference.sizing_feasible)
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

    def test_a_seam_point_follows_the_front_arriving_at_the_feature(self):
        """A sharp vertex closed by a tiny base, its band thinning along one
        flank: the seam point on that side is offset along the normal of the
        chord past the base, not the base's own, and by the thinner height
        the front arrives with - never by more than the feature's own."""
        # A wedge pointing along +x with a 0.002 base at the tip, closed.
        tip = np.array([1.0, 0.0])
        upper = np.array([[1.0 - 0.1 * k, 0.05 * k] for k in range(1, 9)])
        lower = np.array([[1.0 - 0.1 * k, -0.05 * k] for k in range(8, 0, -1)])
        wall = np.vstack([[tip + [0.0, 0.001]], upper, [[0.0, 0.0]], lower, [tip - [0.0, 0.001]], [tip + [0.0, 0.001]]])
        count = len(wall) - 1
        heights = np.full(count + 1, 0.2)
        heights[count - 2] = 0.12   # the lower flank's last vertex: thinner
        seams = layer_module._seam_points(wall, {0: 0}, heights, 1.0)
        incoming, outgoing = seams[0]
        base_normal = np.array([1.0, 0.0])
        lower_normal = np.array([0.05, -0.1]) / math.hypot(0.05, 0.1)
        # The incoming side (the base, then the lower flank): its offset is
        # along the lower flank's normal, 0.12 long, not along the base's.
        offset = incoming - wall[0]
        self.assertAlmostEqual(float(np.linalg.norm(offset)), 0.12, places=9)
        self.assertGreater(float(np.dot(offset / 0.12, lower_normal)), 0.95, offset)
        self.assertLess(float(np.dot(offset / 0.12, base_normal)), 0.6, offset)
        # The outgoing side (the upper flank) keeps the feature's height.
        self.assertAlmostEqual(float(np.linalg.norm(outgoing - wall[0])), 0.2, places=9)

    def test_the_band_inside_a_reflex_corner_shadow_holds_the_corner_height(self):
        """Caps rising along both walls from a 90-degree pocket corner are held
        at the corner's own cap within its mitre shadow - one corner height
        along each wall - and untouched beyond it."""
        # A closed square pocket wall traversed so the corner at the origin
        # sees a 90-degree fluid sector; only the angles and arc lengths matter.
        wall = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]])
        wall = np.vstack([wall[0], *(np.linspace(wall[k], wall[k + 1], 21)[1:] for k in range(4))])
        arclength = g2.cumulative_length(wall)
        angles = np.full(len(wall), math.pi)
        angles[0] = angles[-1] = 0.5 * math.pi
        caps = 0.1 + 0.5 * np.minimum(arclength, arclength[-1] - arclength)
        held = layer_module.hold_corner_shadows(caps, arclength, angles, closed=True)
        gaps = np.minimum(arclength, arclength[-1] - arclength)
        shadow = gaps <= 0.1
        self.assertTrue(np.any(shadow[1:-1]))
        self.assertTrue(np.allclose(held[shadow], 0.1), held[shadow])
        self.assertTrue(np.allclose(held[~shadow], caps[~shadow]))
        self.assertEqual(held[0], held[-1])

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
    def test_the_finest_demand_count_rule_resolves_the_wall_not_the_far_field(self):
        """With one count per equality component, the length-weighted mean
        lets a far-field piece hundreds of times longer than the wall piece
        it is tied to decide the count; ``count_rule="max"`` takes the
        finest demand, so every edge meets the metric."""
        names, loops = cases.two_circles()
        mean = run_case("two_circles", FAST)
        finest = pipeline.run_external(
            names, loops, dataclasses.replace(FAST, sizing=dataclasses.replace(FAST.sizing, count_rule="max"))
        )
        self.assertTrue(finest.admissible, finest.problems or finest.failures)
        self.assertEqual(finest.metric.described()["count_rule"], "max")
        for record in finest.counts.components:
            wanted = min(finest.metric.options.maximum_cells, max(record["metric_lengths"]) * finest.counts.budget_factor)
            self.assertGreaterEqual(record["cells"] + 0.5, wanted, record["roles"])
        self.assertGreaterEqual(finest.counts.total_cells, mean.counts.total_cells)
        bad = pipeline.run_external(names, loops, dataclasses.replace(FAST, sizing=dataclasses.replace(FAST.sizing, count_rule="median")))
        self.assertIsNone(bad.counts)
        self.assertTrue(any("count rule" in str(error) for error in bad.errors), bad.errors)

    def test_the_interface_growth_grades_the_spokes_from_their_neighbours(self):
        """With ``interface_growth`` the sizing stage matches the cell widths
        of the spokes across every block interface, finest first, raising a
        count where a series at the cap needs more cells: the two-circle case
        keeps every block untangled, the core spokes next to the bands start
        at the bands' last cells, and the report says what was raised."""
        names, loops = cases.two_circles()
        plain = run_case("two_circles", FAST)
        graded = pipeline.run_external(
            names, loops, dataclasses.replace(FAST, sizing=dataclasses.replace(FAST.sizing, interface_growth=1.25))
        )
        self.assertTrue(graded.admissible, graded.problems or graded.failures)
        self.assertEqual(graded.grid.inverted_cells, 0)
        report = graded.interface_grading
        self.assertIsNotNone(report)
        self.assertGreaterEqual(report["graded"], 1)
        self.assertGreaterEqual(report["total_cells"], plain.counts.total_cells)
        before = (plain.grid.interface or {}).get("maximum_size_ratio") or 1.0
        after = (graded.grid.interface or {}).get("maximum_size_ratio") or 1.0
        self.assertLessEqual(after, max(1.0, before) + 1e-9)
        # A core spoke's grading request points at its band end.
        requests = {tuple(q.edge): q for q in sizing.propagate_counts_and_grading(graded.graph, dataclasses.replace(graded.counts), sizing.layer_grading_requests(graded.graph, graded.metric), graded.options.sizing)[0]}
        core = [key for key, edge in graded.graph.edges.items() if edge.role == "core_spoke" and key in requests]
        self.assertTrue(core)
        for key in core:
            band_end = [vertex for vertex in key if vertex[0] in ("ring", "front")]
            self.assertTrue(band_end, key)
            self.assertIn(tuple(requests[key].wall_vertex), [tuple(vertex) for vertex in band_end], key)

    def test_the_wall_aspect_floors_the_size_along_tangential_edges(self):
        """``wall_aspect`` sets the wall-parallel spacing at the wall as a
        multiple of the first cell width: the size metric along every
        tangential edge is floored at that spacing, so the wall edges get
        fewer cells, while the band spokes - wall-normal, graded to the
        first width - keep their counts."""
        names, loops = cases.two_circles()
        plain = run_case("two_circles", FAST)
        floored = pipeline.run_external(
            names, loops, dataclasses.replace(FAST, sizing=dataclasses.replace(FAST.sizing, wall_aspect=10.0))
        )
        self.assertTrue(floored.admissible, floored.problems or floored.failures)
        self.assertEqual(plain.metric.tangential_floor, 0.0)
        self.assertAlmostEqual(floored.metric.tangential_floor, 10.0 * floored.metric.first)
        self.assertEqual(floored.metric.described()["wall_aspect"], 10.0)
        wall_plain = sum(plain.counts.counts[key] for key, edge in plain.graph.edges.items() if edge.role == "wall")
        wall_floored = sum(floored.counts.counts[key] for key, edge in floored.graph.edges.items() if edge.role == "wall")
        self.assertLess(wall_floored, wall_plain)
        spokes_plain = sorted(plain.counts.counts[key] for key, edge in plain.graph.edges.items() if edge.role == "layer_spoke")
        spokes_floored = sorted(floored.counts.counts[key] for key, edge in floored.graph.edges.items() if edge.role == "layer_spoke")
        self.assertEqual(spokes_plain, spokes_floored)
        self.assertLess(floored.counts.total_cells, plain.counts.total_cells)

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
        """Brute force over every admissible integer against the recorded targets."""
        result = run_case("two_circles")
        components = pg.constraint_components(result.graph)
        options = result.options.sizing
        candidates = np.arange(options.minimum_cells, options.maximum_cells + 1)
        for record in result.counts.components:
            keys = components[record["component"]]
            weights = np.asarray([g2.total_length(result.graph.edges[key].path) for key in keys])
            targets = np.asarray(record["metric_lengths"])
            errors = [float(np.sum(weights * (count - targets) ** 2)) for count in candidates]
            best = int(candidates[int(np.argmin(errors))])
            self.assertEqual(record["cells"], best, record["component"])

    def test_metric_length_integrates_a_band_spoke_exactly(self):
        """A two-point spoke leaving a straight wall: the integral of ds/size."""
        wall = np.column_stack([np.linspace(-5.0, 5.0, 201), np.zeros(201)])
        for ratio in (4.0e-3, 1.0e-3, 1.0e-5):
            metric = sizing.SizeMetric(sizing.SizingOptions(first_width_ratio=ratio), 1.0, [wall])
            g, first, core = metric.growth, metric.first, metric.core
            for height in (0.02, 0.1, 0.6):
                spoke = np.asarray([[0.0, 0.0], [0.0, height]])
                capped = (core - first) / (g - 1.0)
                if height <= capped:
                    exact = math.log(1.0 + (g - 1.0) * height / first) / (g - 1.0)
                else:
                    exact = math.log(core / first) / (g - 1.0) + (height - capped) / core
                with self.subTest(first=first, height=height):
                    self.assertAlmostEqual(metric.metric_length(spoke) / exact, 1.0, places=2)

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
            cells = model.edge_cells[current]
            if cells < 2 or request.first_width * cells >= model.edge_length(current):
                # Refused: the uniform edge is already finer than the request.
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
    def test_a_first_cell_request_too_steep_for_its_count_is_refused(self):
        """A first-cell width on an edge of two cells would grade at a ratio
        of twelve, a sliver and a slab that shear the block against its
        uniform opposite side; the emitter refuses above three and leaves
        the edge uniform."""
        self.assertAlmostEqual(session_emit._cell_ratio_for(0.007, 0.097, 2), 12.857, places=2)
        self.assertLess(session_emit._cell_ratio_for(0.001, 0.03, 10), 1.3)
        self.assertEqual(session_emit._cell_ratio_for(0.001, 0.0039, 6), 1.0)
        self.assertGreater(session_emit._cell_ratio_for(0.007, 0.097, 2), session_emit.MAX_CELL_RATIO)

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
        reloaded = self._round_trip(result)
        # A pseudo-2-D mesh: both z patches empty, so checkMesh judges the
        # plane mesh, not a unit-depth slab whose z faces fail aspect ratio.
        self.assertEqual((reloaded.z_min_patch_type, reloaded.z_max_patch_type), ("empty", "empty"))

    def test_an_unreachable_cell_budget_is_reported(self):
        result = run_case(
            "two_circles", dataclasses.replace(FAST, sizing=dataclasses.replace(FAST.sizing, budget=10))
        )
        self.assertFalse(result.counts.budget_met)
        self.assertGreater(result.counts.total_cells, 10)
        self.assertFalse(result.analysis["sizing"]["budget_met"])
        self.assertTrue(run_case("two_circles").counts.budget_met)

    def test_the_origin_is_a_far_field_centre(self):
        import research_cli

        parsed = research_cli.build_parser().parse_args(
            ["run", "--case", "single_ellipse", "--farfield-center", "0,0"]
        )
        self.assertEqual(research_cli.build_options(parsed).farfield_center, (0.0, 0.0))

    def test_sizing_can_be_given_in_the_geometry_units(self):
        import research_cli

        parsed = research_cli.build_parser().parse_args(
            ["run", "--case", "single_ellipse", "--first-width", "0.001", "--core-size", "0.5"]
        )
        options = research_cli.build_options(parsed)
        self.assertEqual(options.sizing.widths(39.6), (0.001, 0.5))
        parsed = research_cli.build_parser().parse_args(["run", "--case", "single_ellipse"])
        first, _ = research_cli.build_options(parsed).sizing.widths(39.6)
        self.assertAlmostEqual(first, 2.0e-3 * 39.6)

    def test_outer_chains_alone_are_an_internal_domain(self):
        import research_cli

        case = cases.straight_channel()
        with tempfile.TemporaryDirectory() as folder:
            argv = ["run"]
            for boundary in case.boundaries:
                path = Path(folder) / f"{boundary.name}.dat"
                path.write_text(
                    "\n".join(f"{x} {y}" for x, y in boundary.points) + "\n"
                )
                argv += ["--outer", f"{boundary.name}:{boundary.kind}={path}"]
            parsed = research_cli.build_parser().parse_args(argv)
            result = research_cli.run_case(parsed, FAST)
            self.assertEqual(result.family, "internal")
            self.assertTrue(result.admissible, result.failures or result.problems)
            patches = set(result.model.boundaries)
            self.assertEqual(
                patches, {"bottom_wall", "outlet", "top_wall", "inlet"}
            )
            # A cyclic pair needs its translation, which --outer does not carry.
            argv[-1] = argv[-1].replace("inlet:inlet", "inlet:cyclic")
            parsed = research_cli.build_parser().parse_args(argv)
            with self.assertRaises(SystemExit):
                research_cli.run_case(parsed, FAST)
            # And nothing at all is still refused with a message naming the forms.
            with self.assertRaises(SystemExit):
                research_cli.run_case(
                    research_cli.build_parser().parse_args(["run"]), FAST
                )

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
                # The verdict and the list of what keeps it from being
                # resolved come from separate code paths; they must agree,
                # and the report must state the same verdict.
                self.assertEqual(result.resolved, not result.quality_failures)
                self.assertEqual(result.analysis["acceptance"]["resolved"], result.resolved)
                self.assertEqual(result.coverage["uncovered_samples"], 0)
                self.assertEqual(result.coverage["overlapping_samples"], 0)
                self.assertEqual(result.coverage["outside_samples"], 0)
                self.assertEqual(result.grid.inverted_cells, 0)
                # Counted from the faces, edges and vertices; the total index
                # is 4 * chi for any manifold quad graph and proves nothing.
                self.assertEqual(result.graph.euler(), result.domain.euler_characteristic)

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


class EquivalentInputTests(unittest.TestCase):
    """The block graph is a function of the geometry, not of how it is written.

    Measured on all thirteen external fixtures with twelve equivalent inputs
    each - rotations by 37, 90 and 180 degrees, a mirror, scaling by 1000, a
    translation by 1e4, the start a third of the way round, both reversals,
    every segment densified and a repeated closing point - no graph changes,
    at raster widths 420 and 700 alike.  Before the layout fixes nine of the
    thirteen did; the fixtures and inputs here are the ones that changed, and
    a half-wall clamp in the gate unwrapping later broke five of them under
    the half turn and the mirror.
    """

    VARIANTS = {
        "half turn": lambda loops: cases.transform(loops, rotate=math.pi),
        "mirror": cases.mirror,
        "start a third round": lambda loops: cases.roll(loops, 1.0 / 3.0),
        "plain reversal": cases.reverse_plainly,
        "densified": cases.densify,
    }

    def test_the_block_graph_follows_the_geometry_only(self):
        options = dataclasses.replace(CAVITY_FAST, evaluate_grid=False)
        for name in ("single_ellipse", "two_circles", "concave_and_convex", "sharp_bodies", "blunt_foil"):
            names, loops = cases.CASES[name]()
            reference = pipeline.run_external(names, loops, options)
            self.assertTrue(reference.topology_valid, (name, reference.problems))
            for label, variant in self.VARIANTS.items():
                result = pipeline.run_external(names, variant(loops), options)
                with self.subTest(case=name, variant=label):
                    self.assertIsNotNone(result.graph, result.errors)
                    self.assertEqual(result.graph.summary(), reference.graph.summary())


class TurningDensityTests(unittest.TestCase):
    """The wall turning the layout cuts and judges by is a density on the
    turning vertices: continuous in the gates and blind to collinear points."""

    def setUp(self):
        import block_layout as bl
        import geometry2d as g2

        self.bl = bl
        points = cases.ellipse((0.0, 0.0), 2.0, 1.0, 0.0, 40)
        self.loop = g2.orient_anticlockwise(g2.close_loop(points, 0.0))
        self.dense = g2.orient_anticlockwise(g2.close_loop(cases.densify([points])[0], 0.0))
        stations = g2.cumulative_length(self.loop)
        self.perimeter = float(stations[-1])
        self.tip = 0.0
        self.flank = float(stations[10])
        self.far = float(stations[20])

    def test_a_gate_on_a_vertex_sees_half_of_its_turning_on_each_side(self):
        half = self.bl.turning_between(self.loop, self.tip, self.far - self.tip)
        other = self.bl.turning_between(self.loop, self.far, self.perimeter - self.far)
        self.assertAlmostEqual(half, math.pi, places=12)
        self.assertAlmostEqual(other, math.pi, places=12)
        quarter = self.bl.turning_between(self.loop, self.tip, self.flank - self.tip)
        self.assertAlmostEqual(quarter, 0.5 * math.pi, places=12)

    def test_a_sharp_corner_belongs_to_neither_block_beside_its_gate(self):
        import geometry2d as g2

        tear = g2.orient_anticlockwise(g2.close_loop(cases.teardrop((0.0, 0.0), 0.32, tip_ratio=4.0), 0.0))
        stations = g2.cumulative_length(tear)
        total = float(stations[-1])
        turning = np.abs(g2.turning_angles(tear, closed=True))
        tip = int(np.argmax(turning))
        self.assertGreater(turning[tip], math.radians(100.0))
        at_tip = float(stations[tip])
        # A gate on the tip: the two halves share the smooth turning only.
        halves = self.bl.turning_between(tear, at_tip, 0.5 * total) + self.bl.turning_between(tear, at_tip + 0.5 * total, 0.5 * total)
        self.assertAlmostEqual(halves, 2.0 * math.pi - float(turning[tip]), places=9)
        # A span with the tip strictly inside counts all of it, and nothing
        # else: the flanks are straight.
        inside = self.bl.turning_between(tear, at_tip - 0.1, 0.2)
        self.assertAlmostEqual(inside, float(turning[tip]), places=12)
        self.assertAlmostEqual(self.bl._turning_midpoint(tear, at_tip - 0.1, 0.2), 0.1, places=9)

    def test_the_turning_is_continuous_as_a_gate_crosses_a_vertex(self):
        step = 1.0e-7
        before = self.bl.turning_between(self.loop, self.tip - step, self.far - self.tip)
        after = self.bl.turning_between(self.loop, self.tip + step, self.far - self.tip)
        # The tip vertex turns 17.9 degrees; a prefix sum of vertex turnings
        # jumped by that much here.
        self.assertLess(abs(before - after), math.radians(0.01))

    def test_collinear_vertices_change_neither_turning_nor_midpoint(self):
        for start, length in ((self.tip, self.far - self.tip), (0.37, 2.9), (5.1, 1.3)):
            with self.subTest(start=start, length=length):
                self.assertAlmostEqual(
                    self.bl.turning_between(self.loop, start, length),
                    self.bl.turning_between(self.dense, start, length),
                    places=12,
                )
                self.assertAlmostEqual(
                    self.bl._turning_midpoint(self.loop, start, length),
                    self.bl._turning_midpoint(self.dense, start, length),
                    places=9,
                )
        # ``densify`` leaves the closing segment alone, so the tip has a whole
        # segment on one side and a half on the other; counting the inserted
        # point as a vertex moved the tip's turning two-thirds to one side.
        midpoint = self.bl._turning_midpoint(self.dense, self.tip, self.far - self.tip)
        self.assertAlmostEqual(midpoint, self.flank - self.tip, places=9)


class TipGateTests(unittest.TestCase):
    """A rounded convex corner is a pinned block corner, as a sharp one is."""

    def test_a_thin_ellipse_keeps_its_tips_as_gates(self):
        options = dataclasses.replace(CAVITY_FAST, evaluate_grid=False)
        names, loops = cases.HELD_OUT["ellipse_10to1"]()
        result = pipeline.run_external(names, loops, options)
        tips = [cut for cut in result.layout.cuts[0] if cut.anchor.kind == "tip"]
        self.assertEqual(len(tips), 2)
        for cut in tips:
            self.assertTrue(cut.pinned)
            # The relaxation left the gate on the tip; it used to slide a
            # sixth of the nose radius off it, and a band block wrapped the
            # nose.
            self.assertAlmostEqual(abs(float(cut.wall_point[0])), 1.0, places=9)
            self.assertAlmostEqual(float(cut.wall_point[1]), 0.0, places=9)
        self.assertTrue(result.topology_valid, result.problems)

    def test_a_round_tip_stays_a_free_curvature_gate(self):
        options = dataclasses.replace(CAVITY_FAST, evaluate_grid=False)
        names, loops = cases.HELD_OUT["ellipse_2to1_40"]()
        result = pipeline.run_external(names, loops, options)
        kinds = {cut.anchor.kind for cut in result.layout.cuts[0]}
        self.assertIn("curvature", kinds)
        self.assertNotIn("tip", kinds)


class SolverVariableTests(unittest.TestCase):
    def test_a_gate_past_the_wall_origin_is_read_inside_its_window(self):
        # Bounds run on from the previous gate and may pass the wall's
        # length; the stored station has wrapped to a small number.  Read as
        # stored, the gate seemed to lie far below its window and the window
        # collapsed around it, so the one gate past the origin was never
        # refined - and which gate that was followed the point list's start.
        variable = patch_solver._Variable(
            "gate", None, lambda: 0.3, lambda: (9.5, 10.5), (), (), period=10.0
        )
        self.assertAlmostEqual(variable.current(9.5), 10.3)
        self.assertAlmostEqual(variable.current(0.1), 0.3)
        open_branch = patch_solver._Variable("anchor", None, lambda: 0.3, None, (), ())
        self.assertAlmostEqual(open_branch.current(9.5), 0.3)


class HeldOutEquivalentInputTests(unittest.TestCase):
    """The same property on simple bodies that no layout rule was tuned on.

    The coarse lists space their vertices wider than the curvature smoothing
    used to be, so every vertex was its own curvature peak and the anchors
    were picked among near-ties by list position - or by round-off: the same
    24-gon generated another way, 9e-16 apart, gave 21 blocks instead of 18.
    Six fixes made the circle and the 2:1 ellipse pass: a kernel at least as
    wide as the vertex spacing, peaks located on the curvature itself, the
    split midpoint from a turning density that ignores collinear vertices,
    the solver reading a gate past the wall's origin inside its window, a
    closed ring traced from its least clearance, and the far-field cell no
    longer seeding a ring the body had seeded.  A tight convex bend is now a
    pinned rounded corner (``tip``), which the solver bug had done by
    accident for lists starting at a tip.

    Two still fail, and carry ``expectedFailure`` until fixed.  Their layouts
    are identical across the inputs, their rings agree to 6e-8 and their
    solver states to 1e-12; the coordinate-descent relaxation then takes a
    different path on a near-tie, and the band repair after it ends in a
    different graph:

    * ``ellipse_10to1``: topology-valid under every input, in 24, 21 or 12
      blocks;
    * ``naca0012_coarse``: 22 blocks in seven inputs, 14 and topology-invalid
      reversed.
    """

    VARIANTS = {
        **EquivalentInputTests.VARIANTS,
        "start one vertex on": lambda loops: [np.roll(np.asarray(loop), -1, axis=0) for loop in loops],
        "start five vertices on": lambda loops: [np.roll(np.asarray(loop), -5, axis=0) for loop in loops],
    }

    def assert_follows_the_geometry_only(self, name):
        options = dataclasses.replace(CAVITY_FAST, evaluate_grid=False)
        names, loops = cases.HELD_OUT[name]()
        reference = pipeline.run_external(names, loops, options)
        self.assertTrue(reference.topology_valid, (name, reference.problems))
        for label, variant in self.VARIANTS.items():
            result = pipeline.run_external(names, variant(loops), options)
            with self.subTest(case=name, variant=label):
                self.assertIsNotNone(result.graph, result.errors)
                self.assertTrue(result.topology_valid, result.problems)
                self.assertEqual(result.graph.summary(), reference.graph.summary())

    def test_a_coarse_circle(self):
        self.assert_follows_the_geometry_only("circle_24")

    def test_a_coarse_ellipse(self):
        self.assert_follows_the_geometry_only("ellipse_2to1_40")

    @unittest.expectedFailure
    def test_a_thin_ellipse(self):
        self.assert_follows_the_geometry_only("ellipse_10to1")

    @unittest.expectedFailure
    def test_a_coarse_naca_section(self):
        self.assert_follows_the_geometry_only("naca0012_coarse")


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

    def test_a_shallow_crossing_is_found_in_either_argument_order(self):
        """A short segment crossing a long one at a tiny angle: measuring the
        long one's far end against the short one's extrapolated line used to
        call the pair disjoint when the short segment was passed first."""
        tolerance = 1.0e-6
        angle = 0.5e-3
        short = (np.zeros(2), np.asarray([1.0e-3, 0.0]))
        centre = np.asarray([0.5e-3, 0.0])
        direction = np.asarray([math.cos(angle), math.sin(angle)])
        long = (centre - 0.5 * direction, centre + 0.5 * direction)
        first = g2.segment_relation(*short, *long, tolerance=tolerance)["kind"]
        second = g2.segment_relation(*long, *short, tolerance=tolerance)["kind"]
        self.assertEqual(first, second)
        self.assertNotEqual(first, g2.DISJOINT)

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

    def test_the_periodic_hill_is_resolved_and_states_its_sizing_misses(self):
        """Valid, untangled, within the shape targets, structurally sizable.

        The interface size jump the default metric produces is real and is
        reported, as a sizing miss that a later stage owns rather than as a
        verdict on the topology.
        """
        result = run_internal("periodic_hill")
        self.assertTrue(result.topology_valid)
        self.assertTrue(result.untangled)
        self.assertTrue(result.admissible)
        self.assertEqual(result.shape.inverted_cells, 0)
        self.assertEqual(result.grid.inverted_cells, 0)
        self.assertTrue(result.within_shape_targets, result.described_failures())
        self.assertTrue(result.sizing_feasible, result.structure_failures)
        self.assertTrue(result.resolved)
        self.assertEqual(result.quality_failures, [])
        self.assertFalse(result.within_sizing_targets)
        missed = {failure.described()["metric"] for failure in result.sizing_failures}
        self.assertIn("maximum_interface_size_ratio", missed)
        for failure in result.sizing_failures:
            described = failure.described()
            self.assertGreater(described["observed"], described["limit"])
        acceptance = result.analysis["acceptance"]
        self.assertTrue(acceptance["resolved"])
        self.assertEqual(
            acceptance["untangled_by_grid"], {"shape": True, "fold_check": True, "counts": True}
        )
        self.assertTrue(acceptance["within_shape_targets"])
        self.assertTrue(acceptance["sizing_feasible"])
        self.assertFalse(acceptance["within_sizing_targets"])
        self.assertEqual(result.analysis["quality"]["sampling"], "uniform")
        self.assertEqual(result.analysis["sizing"]["report"]["sampling"], "counts")

    def test_a_below_target_candidate_is_still_written_out(self):
        """The documented session and exit policy, both ways round."""
        import research_cli

        # The peanut is admissible but misses the shape targets at its waist.
        result = run_case("peanut_body", CAVITY_FAST)
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
        # A resolved result with a sizing miss records the miss separately.
        hill = run_internal("periodic_hill")
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
            research_cli.write_artifacts(hill, arguments)
            self.assertNotIn("below_quality_targets", hill.analysis["session"])
            self.assertTrue(hill.analysis["session"]["sizing_misses"])

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

    def test_the_tangent_disk_size_vanishes_at_the_reflex_corners(self):
        """The tangent-disk feature size is zero at the waist; the front is not.

        While the band was built on that law it could not exist at all, and
        the failure named the two waist gates; the level-set front keeps a
        positive height there.
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
        self.assertTrue(result.admissible, (result.failures, result.problems[:2]))
        front = next(iter(result.assembly.fronts.values()))
        self.assertGreater(front.minimum_height, 0.0)

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

    def test_the_peanut_waist_band_is_held_at_the_corner_height_through_its_shadow(self):
        """A reflex corner's shadow carries the corner's band height - raised to
        it where the probe read the adjacent wall as a collapse, held to it
        where the band would thicken past the mitre - so the front leaves the
        mitre along the walls, not along the bisector over the core spoke."""
        result = run_case("peanut_body", CAVITY_FAST)
        self.assertTrue(result.admissible, (result.failures, result.problems[:2]))
        front = next(iter(result.assembly.fronts.values()))
        stations = g2.cumulative_length(front.wall)
        angles = layer_module.fluid_angles(front.wall, 1.0 if g2.signed_area(front.wall) > 0.0 else -1.0)
        total = float(stations[-1])
        for index in np.where(np.degrees(angles) < 170.0)[0]:
            height = float(front.heights[index])
            self.assertGreater(height, 0.5 * float(np.median(front.heights)), height)
            gaps = np.abs(stations[: len(angles)] - stations[index])
            gaps = np.minimum(gaps, total - gaps)
            shadow = gaps <= height / math.tan(0.5 * float(angles[index]))
            self.assertTrue(np.all(front.heights[: len(angles)][shadow] <= height + 1e-9))
            self.assertTrue(np.all(front.heights[: len(angles)][shadow] >= 0.9 * height - 1e-9), front.heights[: len(angles)][shadow])

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

    def test_the_skimming_tail_is_admissible(self):
        """Both bands fit into the slot since gates are graded out of it.

        The clearance-jump rule cuts a patch whose two spokes differ by more
        than a factor of four, so the hull's band beside the tail is no
        longer one block from a sliver of a spoke to a slab; this fixture was
        the one expected failure of the suite until then."""
        result = run_case("skimming_tail", CAVITY_FAST)
        self.assertTrue(result.admissible, (result.failures, result.problems[:2]))
        # Its shape miss is the tip's own seam, above the slot, not the slot.
        _names, loops = cases.skimming_tail()
        tip = np.asarray(loops[0])[np.argmax(_fluid_angles(np.asarray(loops[0])))]
        misses = [item for item in result.described_failures() if item["metric"] == "maximum_wall_misalignment_degrees"]
        for item in misses:
            self.assertLess(float(np.linalg.norm(np.asarray(item["point"]) - tip)), 1.0e-6 * result.scale)


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
        self.assertEqual(structure["wall_tangential_normal_couplings"], 0)
        self.assertEqual(result.graph.boundary_roles, {
            chain.name: chain.role for chain in result.domain.chains()
        })
        self.assertEqual(structure["band_core_depth_couplings"], 0)
        self.assertIn("sizing_structure", result.analysis["graph"])
        names, loops = cases.two_circles()
        scaled = pipeline.run_external(
            names, cases.transform(loops, scale=1000.0), FAST
        )
        other = pg.sizing_structure(scaled.graph)
        self.assertEqual(
            structure["wall_tangential_normal_couplings"],
            other["wall_tangential_normal_couplings"],
        )
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

    Every synthetic fixture gave the same graph at widths 400, 700 and 1000
    when measured; this test checks two of them at 300 and 600.  The 30P30N acceptance geometry does not: at width 1400 it has 106 faces and
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


class AcceptanceRestructureTests(unittest.TestCase):
    """Shape is judged count-free; sizing is structural at this stage."""

    def test_the_shape_grid_does_not_depend_on_the_sizing(self):
        names, loops = cases.two_circles()
        coarse = pipeline.run_external(names, loops, FAST)
        fine = pipeline.run_external(
            names,
            loops,
            dataclasses.replace(
                FAST,
                sizing=sizing.SizingOptions(
                    first_width_ratio=1.0e-3, core_size_ratio=2.0e-2
                ),
            ),
        )
        self.assertNotEqual(coarse.grid.sampled_cells, fine.grid.sampled_cells)
        self.assertEqual(coarse.shape.sampled_cells, fine.shape.sampled_cells)
        for name in (
            "minimum_scaled_jacobian",
            "maximum_non_orthogonality",
            "maximum_skewness",
            "maximum_wall_misalignment",
        ):
            first = getattr(coarse.shape, name).value
            second = getattr(fine.shape, name).value
            self.assertAlmostEqual(first, second, places=9, msg=name)
        self.assertEqual(coarse.shape.sampling, "uniform")
        self.assertEqual(coarse.shape.interface, {})
        self.assertIsNone(coarse.shape.maximum_first_width_error)

    def test_shape_only_disables_exactly_the_sizing_limits(self):
        options = grid_quality.GridOptions().shape_only()
        for name in grid_quality.SIZING_LIMIT_NAMES:
            self.assertIsNone(getattr(options, name))
        for name in grid_quality.SHAPE_LIMIT_NAMES:
            self.assertIsNotNone(getattr(options, name))

    def test_structural_infeasibility_is_a_topology_verdict(self):
        """The seam wedge's coupling keeps the narrow gap tip from resolving."""
        result = run_case("narrow_gap_tip", CAVITY_FAST)
        self.assertTrue(result.admissible)
        self.assertFalse(result.sizing_feasible)
        self.assertFalse(result.resolved)
        failure = next(
            item
            for item in result.structure_failures
            if item["metric"] == "maximum_component_length_ratio"
        )
        self.assertEqual(sorted(failure["roles"]), ["core_spoke", "layer_spoke"])
        self.assertGreater(failure["observed"], failure["limit"])
        # Loosening the structural limit is an explicit option, and then the
        # verdict is about shape alone.
        loose = pipeline.run_external(
            *cases.narrow_gap_tip(),
            dataclasses.replace(
                CAVITY_FAST, structure=pg.StructureLimits(max_length_ratio=None)
            ),
        )
        self.assertTrue(loose.sizing_feasible)
        self.assertEqual(loose.resolved, loose.within_shape_targets)

    def test_structure_failures_read_like_quality_failures(self):
        structure = {
            "worst": [
                {
                    "component": 3,
                    "length_ratio": 42.0,
                    "roles": ["core_spoke", "layer_spoke"],
                    "shortest_edge": [["a"], ["b"]],
                    "longest_edge": [["c"], ["d"]],
                    "mixes_tangential_and_normal": False,
                }
            ],
            "tangential_normal_couplings": 1,
        }
        found = pg.structure_failures(
            structure, pg.StructureLimits(max_length_ratio=20.0)
        )
        self.assertEqual(
            [item["metric"] for item in found],
            ["maximum_component_length_ratio", "tangential_normal_couplings"],
        )
        for item in found:
            self.assertGreater(item["observed"], item["limit"])
            self.assertEqual(item["comparison"], "at_most")
        allowed = pg.structure_failures(
            structure,
            pg.StructureLimits(
                max_length_ratio=None, allow_tangential_normal_coupling=True
            ),
        )
        self.assertEqual(allowed, [])

    def test_the_band_height_is_a_geometric_input(self):
        names, loops = cases.two_circles()
        default = run_case("two_circles")
        # The metric's scale is the domain's, far field included.
        self.assertAlmostEqual(
            default.metric.layer_height,
            sizing.DEFAULT_LAYER_HEIGHT_RATIO * default.domain.scale,
        )
        self.assertAlmostEqual(sizing.DEFAULT_LAYER_HEIGHT_RATIO, 0.2562, places=4)
        self.assertEqual(
            default.analysis["sizing"]["metric"]["layer_height_source"],
            "geometric ratio",
        )
        series = pipeline.run_external(
            names, loops, dataclasses.replace(FAST, layer_height_ratio=None)
        )
        self.assertEqual(
            series.analysis["sizing"]["metric"]["layer_height_source"],
            "wall-normal series",
        )
        self.assertAlmostEqual(
            series.metric.layer_height, series.metric.series_height
        )

    def test_the_sweep_block_count_does_not_follow_the_sizing(self):
        domain = pdm.from_internal_case(cases.periodic_hill())
        coarse = pipeline.run_internal(domain, FAST)
        fine = pipeline.run_internal(
            domain,
            dataclasses.replace(
                FAST,
                sizing=sizing.SizingOptions(
                    first_width_ratio=1.0e-3, core_size_ratio=2.0e-2
                ),
            ),
        )
        self.assertEqual(len(coarse.graph.faces), len(fine.graph.faces))
        self.assertEqual(
            coarse.correspondence.columns, fine.correspondence.columns
        )
        self.assertEqual(coarse.shape.described(), fine.shape.described())


class RoundedReflexCornerTests(unittest.TestCase):
    """A tight concave bend is one reflex corner at its turning midpoint."""

    def test_the_cove_apex_is_a_rounded_reflex_corner(self):
        names, loops = cases.concave_and_convex()
        loop = g2.close_loop(np.asarray(loops[0]), 0.0)
        sign = 1.0 if g2.signed_area(loop) > 0.0 else -1.0
        runs = layer_module.tight_concave_runs(loop, sign)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertGreater(math.degrees(run["turning"]), 60.0)
        # The middle sits at the apex, on the axis of the dimple.
        self.assertAlmostEqual(float(loop[run["middle"]][1]), 0.0, delta=0.02)
        effective = layer_module.effective_fluid_angles(loop, sign)
        self.assertLess(effective[run["middle"]], layer_module.REFLEX_FLUID_ANGLE)
        plain = layer_module.fluid_angles(loop, sign)
        self.assertGreaterEqual(plain[run["middle"]], layer_module.REFLEX_FLUID_ANGLE)

    def test_smooth_and_sharp_bodies_have_no_rounded_corners(self):
        for name in ("single_ellipse", "two_circles", "peanut_body", "sharp_bodies"):
            with self.subTest(name=name):
                names, loops = cases.CASES[name]()
                for points in loops:
                    loop = g2.close_loop(np.asarray(points), 0.0)
                    sign = 1.0 if g2.signed_area(loop) > 0.0 else -1.0
                    self.assertEqual(layer_module.tight_concave_runs(loop, sign), [])

    def test_the_apex_carries_a_gate_and_keeps_its_band(self):
        result = run_case("concave_and_convex")
        self.assertTrue(result.admissible, result.problems or result.failures)
        cuts = result.layout.cuts[0]
        self.assertIn("reflex", [cut.anchor.kind for cut in cuts])
        front = result.assembly.fronts[0]
        apex = int(np.argmin(np.linalg.norm(front.wall - np.asarray([-0.25, 0.0]), axis=1)))
        self.assertGreater(float(front.heights[apex]), 0.05)


class VerdictSoundnessTests(unittest.TestCase):
    """A verdict is a statement about evidence; missing evidence proves nothing."""

    def test_unevaluated_grids_fail_closed(self):
        names, loops = cases.single_ellipse()
        result = pipeline.run_external(
            names, loops, dataclasses.replace(CAVITY_FAST, evaluate_grid=False)
        )
        self.assertTrue(result.topology_valid, result.problems)
        self.assertFalse(result.grids_evaluated)
        for term in ("untangled", "within_shape_targets", "admissible", "resolved", "within_sizing_targets"):
            with self.subTest(term=term):
                self.assertFalse(getattr(result, term))
        self.assertFalse(result.analysis["acceptance"]["grids_evaluated"])
        self.assertFalse(result.analysis["acceptance"]["untangled"])
        self.assertEqual(
            result.analysis["acceptance"]["untangled_by_grid"],
            {"shape": None, "fold_check": None, "counts": None},
        )

    def test_the_length_ratio_is_judged_on_every_component(self):
        result = run_case("two_circles", CAVITY_FAST)
        structure = pg.sizing_structure(result.graph)
        judged = structure["worst_judged"]
        self.assertIsNotNone(judged)
        self.assertFalse(judged["fan_out"])
        # The short table may hold fan-out components only; the verdict must
        # still see the worst component that is not one.
        below = pg.StructureLimits(max_length_ratio=0.99 * judged["length_ratio"])
        above = pg.StructureLimits(max_length_ratio=1.01 * judged["length_ratio"])
        metrics = lambda limits: [item["metric"] for item in pg.structure_failures(structure, limits)]
        self.assertIn("maximum_component_length_ratio", metrics(below))
        self.assertNotIn("maximum_component_length_ratio", metrics(above))
        # Without the fan-out exemption the worst component overall is judged.
        overall = structure["worst_overall"]["length_ratio"]
        strict = pg.structure_failures(
            structure, pg.StructureLimits(max_length_ratio=0.99 * overall), exemptions=False
        )
        self.assertIn("maximum_component_length_ratio", [item["metric"] for item in strict])

    def test_apply_reruns_the_case_with_its_explicit_outer_boundary(self):
        import tempfile

        import research_cli

        with tempfile.TemporaryDirectory() as folder:
            arguments = ["apply", "--case", "single_ellipse", "--move", "unused", "--width", "420",
                         "--coverage-samples", "240", "--no-grid-quality"]
            for name, role, points in cases.c_shaped_outer():
                path = Path(folder) / f"{name}.dat"
                np.savetxt(path, np.asarray(points))
                arguments += ["--outer", f"{name}:{role}={path}"]
            parsed = research_cli.build_parser().parse_args(arguments)
            before = research_cli.run_case(parsed)
            after = research_cli._rerun(parsed, research_cli.build_options(parsed))
        names = lambda result: sorted(chain.name for chain in result.domain.outer.chains)
        self.assertEqual(names(after), names(before))
        self.assertEqual(names(after), ["bottom", "cap", "outlet", "top"])


class CoveCutTests(unittest.TestCase):
    def test_a_cove_facing_a_body_gets_pinned_cuts(self):
        """A dimpled disk with its cove facing a disk at the mouth: the medial
        branch runs across the mouth, every split of the cove's band patches
        crowds a cut on that short piece, and the band blocks round the cove
        would turn 54 degrees against a 45 degree limit.  The layout pins
        cove cuts on the cove wall at equal fractions of the turning, spread
        along the ring piece, and the result builds."""
        import block_layout

        loops = [
            cases.dimpled_disk((-0.55, 0.0), 0.75, depth=0.6, width=0.45, count=240),
            cases.circle((0.6, 0.0), 0.30, 150),
        ]
        options = dataclasses.replace(
            CAVITY_FAST,
            layout=block_layout.LayoutOptions(band_height=0.05, max_band_turning=math.radians(45.0)),
        )
        result = pipeline.run_external(["cove", "disk"], loops, options)
        self.assertTrue(result.admissible, result.problems or result.failures)
        layout = result.layout
        coves = [cut for cut in layout.cuts[0] if cut.anchor.kind == "cove"]
        self.assertEqual(len(coves), 2, [cut.anchor.kind for cut in layout.cuts[0]])
        for cut in coves:
            self.assertTrue(cut.anchor.fixed)
            self.assertFalse(cut.anchor.movable)
            # Pinned on the cove wall, inside the cove (x below the rim).
            self.assertLess(cut.wall_point[0], 0.15, cut.wall_point)
        self.assertTrue(any("cove cut(s) pinned" in note for note in layout.notes), layout.notes)
        worst = max(math.degrees(patch.wall_turning()) for patch in layout.patches if patch.cell == 0 and layout.cuts[0][patch.first_cut].anchor.kind != "reflex" and layout.cuts[0][patch.second_cut].anchor.kind != "reflex")
        self.assertLess(worst, 50.0, worst)
        # The other side of the branch sees the same cuts on the disk's wall.
        disk = [cut for cut in layout.cuts[1] if cut.anchor.kind == "cove"]
        self.assertEqual(len(disk), 2)

    def test_a_cove_cut_whose_spoke_the_wall_hides_is_left_out(self):
        """A deeper notch with the same disk at its mouth: no station on the
        ring piece is visible from the notch bottom, so the cut is left out
        with a note rather than placed across the wall."""
        import block_layout

        loops = [
            cases.dimpled_disk((-0.55, 0.0), 0.75, depth=0.7, width=0.4, count=240),
            cases.circle((0.6, 0.0), 0.30, 150),
        ]
        options = dataclasses.replace(
            CAVITY_FAST,
            layout=block_layout.LayoutOptions(band_height=0.05, max_band_turning=math.radians(45.0)),
        )
        result = pipeline.run_external(["cove", "disk"], loops, options)
        layout = result.layout
        self.assertEqual([cut for cut in layout.cuts[0] if cut.anchor.kind == "cove"], [])
        self.assertTrue(any("left out" in note for note in layout.notes), layout.notes)
        self.assertFalse([p for p in result.problems if p["kind"] == "crossing_edges"])


class LayoutRobustnessTests(unittest.TestCase):
    """Representation details of the input must not decide the layout."""

    def test_the_start_point_of_a_loop_does_not_unwrap_a_zero_gate_step(self):
        """Two gates on one wall station are a zero step around the wall.

        Accumulating the unwrapped station turned that zero into a full turn
        for about one start point in ten of ``sharp_bodies``' tip, and the
        layout then refused the wall ("the gates advance 1.79 times").
        These start points failed that way; the body frame is held fixed so
        only the point order changes.
        """
        names, base = cases.sharp_bodies()
        frame = site_module.domain_frame(base)
        centre, radius = frame
        spec = pipeline.farfield_spec(CAVITY_FAST)
        reference = None
        for roll in (0, 46, 53, 72):
            loops = [np.roll(base[0], -roll, axis=0), base[1]]
            outer = [pdm.circle_chain("farfield", centre, radius * spec.scale)]
            domain = pdm.from_bodies(names, loops, outer=outer)
            sites, scale = site_module.sites_from_domain(
                domain, circle=(centre, radius * spec.scale), frame=frame
            )
            diagram = voronoi_graph.build_diagram(sites, scale=scale, grid_width=CAVITY_FAST.grid_width)
            layout = block_layout.build_layout(diagram, CAVITY_FAST.layout)
            cuts = [len(item) for item in layout.cuts]
            with self.subTest(roll=roll):
                if reference is None:
                    reference = cuts
                self.assertEqual(cuts, reference)

    def test_a_merged_junction_cluster_refines_the_raster(self):
        """At width 420 two junctions of ``four_bodies`` 1.8 pixels apart fell
        into one cluster, refined to a fake degree-4 junction with a residual
        of three percent of its clearance, and the short branch between them
        was lost.  The diagram is rebuilt finer until every junction
        converges, so the raster decides connectivity only."""
        names, loops = cases.four_bodies()
        outer, circle = pdm.fabricate_outer(loops, pipeline.farfield_spec(CAVITY_FAST))
        domain = pdm.from_bodies(names, loops, outer=outer)
        sites, scale = site_module.sites_from_domain(domain, circle=circle, frame=site_module.domain_frame(loops))
        coarse = voronoi_graph.build_diagram(sites, scale=scale, grid_width=420)
        fine = voronoi_graph.build_diagram(sites, scale=scale, grid_width=700)
        self.assertEqual(voronoi_graph.unconverged_junctions(coarse.junctions), [])
        self.assertEqual(len(coarse.junctions), len(fine.junctions))
        self.assertEqual(
            sorted(junction.degree for junction in coarse.junctions),
            sorted(junction.degree for junction in fine.junctions),
        )

    def test_splitting_stops_when_it_no_longer_raises_the_worst_block(self):
        """A worst patch that cannot be split (its cut would crowd another)
        used to make the solver split the next-worst ones until the budget
        of forty ran out - half an hour on the A-airfoil's C-grid layout -
        while the worst block stayed where it was."""
        import patch_solver

        class FakeLayout:
            patches = ()

            def refresh(self):
                pass

        calls = {"relax": 0, "split": 0}

        def relax(layout, settings, **_):
            calls["relax"] += 1
            return 0.1, 1

        def split(layout, settings, result):
            calls["split"] += 1
            return True

        saved = patch_solver.relax, patch_solver._split_worst
        patch_solver.relax, patch_solver._split_worst = relax, split
        try:
            result = patch_solver.solve(FakeLayout(), patch_solver.SolverOptions(accept_quality=0.0))
        finally:
            patch_solver.relax, patch_solver._split_worst = saved
        self.assertEqual(calls["split"], patch_solver.SolverOptions.futile_splits)
        self.assertIn("did not raise the worst block", result.failures[-1]["reason"])

    def test_both_corners_of_a_blunt_base_are_gates(self):
        """Snapping gates onto sharp vertices moved the nearest gate onto
        each in turn, so the second base corner took the gate just snapped
        onto the first and the 250.5 degree corner lay inside a band block's
        wall edge."""
        names, loops = cases.blunt_foil()
        result = run_case("blunt_foil", CAVITY_FAST)
        loop = np.asarray(loops[0])
        corners = loop[_fluid_angles(loop) > 200.0]
        self.assertEqual(len(corners), 2)
        gates = np.asarray([vertex.point for key, vertex in result.graph.vertices.items() if key[0] == "gate"])
        for corner in corners:
            self.assertLess(float(np.min(np.linalg.norm(gates - corner, axis=1))), 1.0e-9 * result.scale)

    def test_unwrapping_treats_coincident_gates_as_a_zero_step(self):
        class FakeCut:
            def __init__(self, station, pinned=False):
                self.wall_station = station
                self.pinned = pinned

        # Values for which stepping from the accumulated station rounds the
        # final zero step to ``total - 4e-16``: winding 1.84 instead of 0.84.
        total = 2.561404013454236
        station = 2.0406373527932953
        cuts = [FakeCut(2.4463, True), FakeCut(0.1973), FakeCut(0.5180), FakeCut(station), FakeCut(station)]
        _order, unwrapped, winding = block_layout._unwrap_gates(cuts, total)
        self.assertEqual(unwrapped[-1], unwrapped[-2])
        self.assertLess(winding, 1.0)

    def test_the_clamp_acts_only_on_gates_out_of_ring_order(self):
        """An unpinned gate behind its predecessor takes a zero step; a long
        step that is in order is kept.

        The stations behind the out-of-order gate are the rear foil's gates in
        ``tandem_foils --wake``: the unpinned gate at 0.65216 projects just
        behind the pinned one at 0.65766, and read forward it wound the gates
        twice round the wall.  The first clamp treated every unpinned step
        longer than half the wall as such a step back, which also collapsed
        the second of two gates half a wall apart - the outer cell of an
        ellipse whose two tips are its first anchors - and made the layout
        follow the orientation of the input.  The clamp now acts only when
        the plain reading winds more than once: on a wall with one pin, a gate
        on the pin's own station at the end of the ring order is a full turn
        on (the C-shaped teardrop's tip, where a chain break projects), and
        reading it as a step back collapsed it onto its predecessor."""
        class FakeCut:
            def __init__(self, station, pinned=False):
                self.wall_station = station
                self.pinned = pinned

        _order, unwrapped, winding = block_layout._unwrap_gates(
            [FakeCut(0.0), FakeCut(1.02)], 2.0, clamp_unpinned=True
        )
        self.assertEqual(unwrapped, [0.0, 1.02])
        rear = [(0.20998, False), (0.35003, False), (0.41456, False), (0.47764, True), (0.65766, True),
                (0.65216, False), (0.81711, False), (1.6624, True), (1.99089, False)]
        cuts = [FakeCut(station, pinned) for station, pinned in rear]
        _order, unwrapped, winding = block_layout._unwrap_gates(cuts, 2.2)
        self.assertGreater(winding, 1.0)
        order, unwrapped, winding = block_layout._unwrap_gates(cuts, 2.2, clamp_unpinned=True)
        self.assertLess(winding, 1.0)
        self.assertEqual(unwrapped[order.index(5)], unwrapped[order.index(4)])
        self.assertTrue(all(b >= a for a, b in zip(unwrapped, unwrapped[1:])))
        # Every gate that was in order keeps its station.
        for position, index in enumerate(order):
            if index != 5:
                self.assertAlmostEqual(unwrapped[position] % 2.2, rear[index][0], places=12)
        tip = [(1.9343, True), (1.9343, False), (0.1547, False), (0.6519, False), (1.1535, False), (1.9343, False)]
        cuts = [FakeCut(station, pinned) for station, pinned in tip]
        _order, unwrapped, winding = block_layout._unwrap_gates(cuts, 2.5614, clamp_unpinned=True)
        self.assertAlmostEqual(winding, 1.0, places=12)
        self.assertAlmostEqual(unwrapped[-1], 1.9343 + 2.5614, places=12)


class BoundaryFidelityTests(unittest.TestCase):
    """Every named chain reaches the session as its own patch, on its own geometry."""

    @staticmethod
    def _slot_hill():
        base = cases.periodic_hill()
        bottom = base.boundary("bottom_wall").points
        first = int(np.argmin(np.abs(bottom[:, 0] - 1.0)))
        last = int(np.argmin(np.abs(bottom[:, 0] - 1.4)))
        rest = [chain for chain in base.boundaries if chain.name != "bottom_wall"]
        return cases.InternalFlowCase(
            "slot_hill",
            (
                cases.BoundaryChain("wall_up", "wall", bottom[: first + 1]),
                cases.BoundaryChain("slot", "inlet", bottom[first : last + 1]),
                cases.BoundaryChain("wall_down", "wall", bottom[last:]),
                *rest,
            ),
            periodic_translation=base.periodic_translation,
        )

    def test_a_chain_join_on_a_guide_is_a_block_vertex(self):
        """A slot on the hill's wall used to be relaxed away and exported as
        the wall beside it, while the run reported ``resolved``."""
        domain = pdm.from_internal_case(self._slot_hill())
        result = _no_crash(pipeline.run_internal(domain, FAST))
        self.assertTrue(result.topology_valid, result.problems or result.failures)
        self.assertEqual(pg.boundary_problems(result.graph, result.domain), [])
        self.assertIn("slot", result.model.boundaries)
        slot = domain.chain("slot")
        stations = {tuple(np.round(slot.points[0], 9)), tuple(np.round(slot.points[-1], 9))}
        vertices = {tuple(np.round(vertex.point, 9)) for vertex in result.graph.vertices.values()}
        self.assertLessEqual(stations, vertices)

    def test_a_mislabelled_boundary_edge_is_a_problem(self):
        result = run_internal("periodic_hill")
        graph = result.graph.copy()
        self.assertEqual(pg.boundary_problems(graph, result.domain), [])
        key, edge = next(
            (key, edge) for key, edge in graph.edges.items() if edge.boundary == "bottom_wall"
        )
        edge.boundary = "top_wall"
        kinds = {problem["kind"] for problem in pg.boundary_problems(graph, result.domain)}
        self.assertEqual(kinds, {"boundary_off_chain", "chain_not_covered"})

    def test_a_self_crossing_edge_path_is_a_problem(self):
        result = run_case("single_ellipse", CAVITY_FAST)
        graph = result.graph.copy()
        key, edge = max(
            ((key, edge) for key, edge in graph.edges.items() if edge.boundary is None),
            key=lambda item: g2.total_length(item[1].path),
        )
        path = np.asarray(edge.path, dtype=np.float64)
        a, b = path[0], path[-1]
        along, across = b - a, np.asarray([a[1] - b[1], b[0] - a[0]])
        bow = np.asarray([a, a + 0.4 * along + 0.2 * across, a + 0.6 * along - 0.2 * across,
                          a + 0.6 * along + 0.2 * across, a + 0.4 * along - 0.2 * across, b])
        edge.path = bow
        kinds = {problem["kind"] for problem in graph.problems()}
        self.assertIn("self_crossing_edge", kinds)


class ChosenMoveTests(unittest.TestCase):
    """A move reported as implemented realises exactly what it names."""

    def test_a_chosen_cavity_template_is_applied_below_the_incumbent(self):
        graph, domain = cusp_strip()
        plain = fan_cavity.repair(graph, domain, options=fan_cavity.CavityOptions())
        record = plain.cavities[0]
        weaker = next(
            item for item in record["candidates"]
            if item["accepted"] and item["score"] < record["existing"]["score"]
        )
        graph, domain = cusp_strip()
        forced = fan_cavity.repair(
            graph, domain,
            options=fan_cavity.CavityOptions(choices=((_label_of(record), weaker["template"]),)),
        )
        self.assertEqual(
            forced.cavities[0]["applied"], f"fan_cavity:{_label_of(record)}:{weaker['template']}"
        )

    def test_a_new_choice_replaces_the_earlier_one_for_that_feature(self):
        options = dataclasses.replace(
            CAVITY_FAST,
            cavity=fan_cavity.CavityOptions(choices=(("node_0_2", "seam"), ("node_1_4", "band"))),
        )
        move = move_module.Move(
            identifier="fan_cavity:node_0_2:band", kind="wall_feature_cavity", target={},
            rationale="", index_change={}, index_balanced=True, complexity=0, implemented=True,
            score=1.0, option_delta={"cavity.choices": [("node_0_2", "band")]},
        )
        updated = move_module.apply(options, move)
        self.assertEqual(
            sorted(updated.cavity.choices), [("node_0_2", "band"), ("node_1_4", "band")]
        )


def _label_of(record) -> str:
    return record["applied"].split(":")[1] if record.get("applied") else fan_cavity._label(tuple(record["feature"]))


class HostileDomainTests(unittest.TestCase):
    """Domains the producers cannot mesh are refused by name at the domain stage."""

    @staticmethod
    def _kinds(names, loops, outer=None):
        return {problem["kind"] for problem in pdm.from_bodies(names, loops, outer=outer).problems()}

    def test_a_body_inside_another_is_refused_whichever_is_longer(self):
        t = np.linspace(0.0, 2.0 * math.pi, 600, endpoint=False)
        radius = 0.6 + 0.25 * np.cos(24 * t)
        gear = np.column_stack((radius * np.cos(t), radius * np.sin(t)))
        disk = cases.circle((0.0, 0.0), 1.0, 200)
        # The gear's perimeter is four times the disk's, so it sorts first.
        self.assertIn("holes_overlap", self._kinds(["disk", "gear"], [disk, gear]))

    def test_a_loop_touching_itself_is_refused(self):
        eight = np.asarray([(0, 0), (1, 0), (1, 1), (2, 1), (2, 2), (1, 2), (1, 1), (0, 1)], float)
        hairpin = np.asarray([(0, 0), (1, 0), (1, 1), (0.5, 1), (0.5, 1.8), (0.5, 1), (0, 1)], float)
        for loop in (eight, hairpin):
            with self.subTest(points=len(loop)):
                self.assertIn("self_touching_loop", self._kinds(["body"], [loop]))

    def test_a_body_flush_with_the_outer_boundary_is_refused(self):
        outer = [("box", "farfield", np.asarray([(-3, -3), (3, -3), (3, 3), (-3, 3), (-3, -3)], float))]
        flush = np.asarray([(0, -3), (1, -3), (1, -2), (0, -2)], float)
        self.assertIn("hole_touches_outer", self._kinds(["flush"], [flush], outer))

    def test_points_blockdrawer_would_merge_are_refused_here(self):
        loop = cases.circle((0.0, 0.0), 0.5, 100)
        near = np.insert(loop, 10, loop[10] + np.asarray([3.0e-12, -1.0e-11]), axis=0)
        self.assertIn("coincident_points", self._kinds(["body"], [near]))

    def test_the_corpus_has_no_domain_problems(self):
        for name, factory in cases.CASES.items():
            names, loops = factory()
            with self.subTest(case=name):
                self.assertEqual(pdm.from_bodies(names, loops).problems(), [])


class SymmetryGuideTests(unittest.TestCase):
    """A sweep between a wall and a symmetry plane: one band, then core."""

    @staticmethod
    def _half_channel(length: float = 6.0, height: float = 1.0):
        def line(a, b, count):
            t = np.linspace(0.0, 1.0, count)[:, None]
            return (1.0 - t) * np.asarray(a, float) + t * np.asarray(b, float)

        return cases.InternalFlowCase(
            "half_channel",
            (
                cases.BoundaryChain("bottom_wall", "wall", line((0, 0), (length, 0), 61)),
                cases.BoundaryChain("outlet", "outlet", line((length, 0), (length, height), 11)),
                cases.BoundaryChain("top_symmetry", "symmetry", line((length, height), (0, height), 61)),
                cases.BoundaryChain("inlet", "inlet", line((0, height), (0, 0), 11)),
            ),
        )

    def test_the_band_stays_below_the_symmetry_plane(self):
        domain = pdm.from_internal_case(self._half_channel())
        result = _no_crash(pipeline.run_internal(domain, FAST))
        self.assertTrue(result.resolved, result.failures or result.problems or result.described_failures())
        roles = {face.role for face in result.graph.faces}
        self.assertEqual(roles, {"layer", "core"})
        # No band block touches the symmetry plane, and no grading request
        # ends on it.
        symmetry = {
            vertex
            for key, edge in result.graph.edges.items() if edge.boundary == "top_symmetry"
            for vertex in key
        }
        for face in result.graph.faces:
            if face.role == "layer":
                self.assertFalse(set(face.corners) & symmetry)
        for request in sizing.layer_grading_requests(result.graph, result.metric):
            self.assertNotIn(request.wall_vertex, symmetry)
        heights = [
            float(vertex.point[1]) for vertex in result.graph.vertices.values()
            if vertex.provenance == "front"
        ]
        self.assertLess(max(heights), 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
