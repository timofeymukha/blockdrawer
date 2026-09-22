"""The outer boundary is explicit: named chains with roles, corners as gates."""

from dataclasses import replace
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from test_agentic_topology import CAVITY_FAST, FAST, run_case
import geometry2d as g2
import pipeline
import planar_domain as pdm
import session_emit
import sites as site_module
import synthetic_cases as cases
from blockdrawer.session import load_session
from pointlist import parse_box, parse_outer_argument, parse_sides

RECTANGLE = replace(CAVITY_FAST, farfield_shape="rectangle")
_RUNS: dict = {}


def run_outer(name: str, outer, options=CAVITY_FAST):
    key = (name, repr(options), id(outer))
    if key not in _RUNS:
        names, loops = cases.CASES[name]()
        _RUNS[key] = pipeline.run_external(names, loops, options, outer=outer)
    return _RUNS[key]


def boundary_edges(model):
    """Session edges by patch name, with their sampled geometry."""
    found: dict[str, list] = {}
    for edge in model.edges():
        if not model.is_boundary_edge(edge):
            continue
        name = model.edge_boundaries[edge]
        points = np.asarray([model.edge_point(edge, t) for t in np.linspace(0.0, 1.0, 17)])
        found.setdefault(name, []).append(points)
    return found


class FabricatedFarfieldTests(unittest.TestCase):
    def test_default_circle_is_one_farfield_chain_and_unchanged(self):
        result = run_case("two_circles")
        self.assertEqual(
            [(chain.name, chain.role) for chain in result.domain.outer.chains],
            [("farfield", "farfield")],
        )
        self.assertEqual(result.graph.summary()["faces"], 32)
        self.assertTrue(result.resolved)
        arcs = [edge for edge in result.model.edge_geometry.values() if edge.kind == "arc"]
        self.assertTrue(arcs, "the circular far field still exports exact arcs")

    def test_rectangle_sides_become_patches_with_their_roles(self):
        result = run_case("single_ellipse", RECTANGLE)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertTrue(result.resolved, result.described_failures())
        model = result.model
        self.assertEqual(
            {name: patch.kind for name, patch in model.boundaries.items()},
            {"body": "wall", "bottom": "patch", "outlet": "patch", "top": "patch", "inlet": "patch"},
        )
        domain = result.domain
        chains = {chain.name: chain for chain in domain.chains()}
        for name, edges in boundary_edges(model).items():
            for points in edges:
                distance = g2.distance_to_polyline(chains[name].points, points)
                self.assertLess(float(np.max(distance)), 1e-6 * domain.scale, name)

    def test_rectangle_corners_are_gates_split_by_a_mitre_spoke(self):
        """The classical O-grid in a box: each corner is a gate whose spoke
        runs along the corner bisector to the ring, so two blocks share it at
        45 degrees each and the annulus keeps total index zero."""
        result = run_case("single_ellipse", RECTANGLE)
        xmin, ymin, xmax, ymax = result.domain.bounds()
        corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        faces = result.graph.face_counts()
        for corner in corners:
            key = min(
                result.graph.vertices,
                key=lambda item: float(np.linalg.norm(result.graph.vertices[item].point - corner)),
            )
            point = result.graph.vertices[key].point
            self.assertLess(float(np.linalg.norm(point - corner)), 1e-9 * result.domain.scale)
            self.assertEqual(faces[key], 2)
            angles = []
            for face in result.graph.faces:
                if key not in face.corners:
                    continue
                points = result.graph.corner_points(face)
                position = face.corners.index(key)
                angles.append(math.degrees(float(g2.quad_corner_angles(points)[position])))
            self.assertEqual(len(angles), 2)
            self.assertAlmostEqual(sum(angles), 90.0, delta=1e-6)
            for angle in angles:
                self.assertGreater(angle, 30.0)
            spokes = [
                edge for edge in result.graph.edges.values()
                if key in edge.key and edge.boundary is None
            ]
            self.assertEqual(len(spokes), 1)
            self.assertEqual(spokes[0].role, "core_spoke")
        self.assertEqual(result.graph.total_index(), 0)

    def test_named_sides_and_absolute_box(self):
        names, loops = cases.two_circles()
        options = replace(
            CAVITY_FAST,
            farfield_shape="rectangle",
            farfield_sides=(("floor", "symmetry"), ("out", "outlet"), ("roof", "symmetry"), ("in", "inlet")),
            farfield_box=(-4.0, -3.0, 6.0, 3.0),
        )
        result = pipeline.run_external(names, loops, options)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(result.domain.bounds(), (-4.0, -3.0, 6.0, 3.0))
        self.assertEqual(result.model.boundaries["floor"].kind, "symmetry")
        self.assertEqual(result.model.boundaries["roof"].kind, "symmetry")
        self.assertEqual(result.model.boundaries["in"].kind, "patch")
        self.assertEqual(result.graph.boundary_roles["floor"], "symmetry")

    def test_rectangle_is_permutation_invariant(self):
        names, loops = cases.two_circles()
        base = pipeline.run_external(names, loops, RECTANGLE)
        other = pipeline.run_external(*cases.permute(names, loops, (1, 0)), RECTANGLE)
        self.assertEqual(base.graph.summary(), other.graph.summary())
        self.assertEqual(
            session_emit.topology_signature(base.model),
            session_emit.topology_signature(other.model),
        )

    def test_spec_rejects_bad_input(self):
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec("triangle")
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec("rectangle", sides=(("a", "wall"), ("b", "outlet"), ("c", "farfield")))
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec("rectangle", sides=(("a", "wall"), ("a", "outlet"), ("c", "farfield"), ("d", "inlet")))
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec("rectangle", sides=(("a", "hot"), ("b", "outlet"), ("c", "farfield"), ("d", "inlet")))
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec("rectangle", box=(0.0, 0.0, -1.0, 1.0))
        with self.assertRaises(pdm.DomainError):
            pdm.FarfieldSpec(scale=0.5)


class ExplicitOuterTests(unittest.TestCase):
    def test_c_shaped_outer_puts_gates_at_tangent_chain_breaks(self):
        outer = cases.c_shaped_outer()
        result = run_outer("single_ellipse", outer)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(
            sorted(result.model.boundaries), ["body", "bottom", "cap", "outlet", "top"]
        )
        self.assertEqual(result.model.boundaries["outlet"].kind, "patch")
        # The cap meets the legs without a corner; both joins must still be vertices.
        points = np.asarray([vertex.point for vertex in result.graph.vertices.values()])
        for join in ((0.0, 3.0), (0.0, -3.0), (5.0, 3.0), (5.0, -3.0)):
            gap = float(np.min(np.linalg.norm(points - np.asarray(join), axis=1)))
            self.assertLess(gap, 1e-9 * result.domain.scale, join)
        chains = {chain.name: chain for chain in result.domain.chains()}
        for name, edges in boundary_edges(result.model).items():
            for sampled in edges:
                distance = g2.distance_to_polyline(chains[name].points, sampled)
                self.assertLess(float(np.max(distance)), 1e-6 * result.domain.scale, name)
        self.assertEqual(result.graph.total_index(), 0)

    def test_explicit_outer_round_trips_through_the_session(self):
        result = run_outer("single_ellipse", cases.c_shaped_outer())
        with tempfile.TemporaryDirectory() as folder:
            path = session_emit.write_session(result.model, Path(folder) / "c.json")
            reloaded = load_session(path)
            reloaded.validate()
            self.assertEqual(
                session_emit.topology_signature(reloaded),
                session_emit.topology_signature(result.model),
            )
            self.assertEqual(sorted(reloaded.boundaries), sorted(result.model.boundaries))

    def test_outer_wall_chain_is_reported_as_not_banded(self):
        outer = cases.c_shaped_outer(roles=("farfield", "wall", "outlet", "wall"))
        result = run_outer("single_ellipse", outer)
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(result.model.boundaries["bottom"].kind, "wall")
        self.assertTrue(
            any("outer wall chain" in note for note in result.assembly.notes),
            result.assembly.notes,
        )
        structure = result.analysis["graph"]["sizing_structure"]
        self.assertEqual(structure["wall_tangential_normal_couplings"], 0)

    def test_disconnected_outer_chains_are_a_domain_problem(self):
        outer = cases.c_shaped_outer()
        name, role, points = outer[1]
        outer[1] = (name, role, np.asarray(points) + [0.0, 0.2])
        names, loops = cases.single_ellipse()
        result = pipeline.run_external(names, loops, CAVITY_FAST, outer=outer)
        self.assertTrue(any(item["kind"] == "disconnected_chains" for item in result.problems))
        self.assertFalse(result.admissible)

    def test_sites_follow_the_domain_chains(self):
        names, loops = cases.two_circles()
        chains, circle = pdm.fabricate_outer(loops, pdm.FarfieldSpec("rectangle"))
        self.assertIsNone(circle)
        domain = pdm.from_bodies(names, loops, outer=chains)
        sites, scale = site_module.sites_from_domain(domain)
        self.assertEqual([site.name for site in sites[:-1]], ["large", "small"])
        outer = sites[-1]
        self.assertEqual(len(outer.chains), 4)
        self.assertEqual(len(outer.chain_breaks), 4)
        total = outer.curve.length()
        for chain in outer.chains:
            middle = chain.start + 0.5 * chain.length
            self.assertEqual(outer.chain_at(middle).name, chain.name)
            point = outer.curve.point_at(middle)
            self.assertLess(
                float(g2.distance_to_polyline(domain.chain(chain.name).points, point[None, :])[0]),
                1e-9 * total,
            )
        self.assertEqual(outer.section_chain(total - 1.0, 1.0).name, outer.chain_at(0.0).name)


class OuterArgumentTests(unittest.TestCase):
    def test_argument_parsers(self):
        name, role, path = parse_outer_argument("cap:farfield=~/cap.dat")
        self.assertEqual((name, role), ("cap", "farfield"))
        self.assertTrue(str(path).endswith("cap.dat"))
        self.assertEqual(
            parse_sides("floor:symmetry, out:outlet ,roof:symmetry,in:inlet"),
            (("floor", "symmetry"), ("out", "outlet"), ("roof", "symmetry"), ("in", "inlet")),
        )
        self.assertEqual(parse_box("-4,-3,6,3"), (-4.0, -3.0, 6.0, 3.0))
        import argparse

        for bad in ("cap=~/cap.dat", ":farfield=~/cap.dat"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_outer_argument(bad)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_sides("a:b,c:d")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_box("1,2,3")


if __name__ == "__main__":
    unittest.main()
