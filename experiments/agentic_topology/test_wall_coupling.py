"""Count coupling is measured at physical walls/fronts, not core axes."""

import unittest

import fan_cavity
import patch_graph as pg
from test_agentic_topology import cusp_cavity, cusp_strip


def corner_band():
    """One band block with a non-wall boundary along its lower normal side."""
    graph = pg.PatchGraph(boundary_roles={
        "outer": "farfield", "solid": "wall", "exit": "outlet",
    })
    for index, point in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        graph.add_vertex((index,), point)
    for first, second, role, boundary in (
        (0, 1, "wall", "outer"),  # The external producer uses this label too.
        (1, 2, "wall", "solid"),
        (2, 3, "layer_spoke", "exit"),
        (3, 0, "front", None),
    ):
        graph.add_edge((first,), (second,),
                       path=[graph.vertices[(first,)].point, graph.vertices[(second,)].point],
                       role=role, boundary=boundary)
    graph.add_face(((0,), (1,), (2,), (3,)), role="layer")
    return graph


class WallCouplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        graph, domain = cusp_strip()
        graph.boundary_roles = {chain.name: chain.role for chain in domain.chains()}
        options = fan_cavity.CavityOptions(choices=(("node_0_2", "fan3"),))
        cavity, context = cusp_cavity(graph, options)
        candidate = fan_cavity.enumerate_candidates(graph, cavity, context, options)[0]
        assert candidate.valid, candidate.rejections
        cls.fan = fan_cavity.apply_candidate(graph, cavity, candidate)

    def test_farfield_label_is_not_wall_tangential_even_beside_a_band(self):
        graph = corner_band()
        report = pg.sizing_structure(graph)
        self.assertEqual(report["tangential_normal_couplings"], 1)
        self.assertEqual(report["wall_tangential_normal_couplings"], 0)
        mixed = report["coupled"][0]
        self.assertEqual(mixed["wall_tangential_chains"], [])
        self.assertEqual(mixed["wall_normal_chains"], ["solid"])
        self.assertEqual(pg.structure_failures(report, pg.StructureLimits()), [])
        self.assertEqual(fan_cavity._count_coupling(graph), [])

    def test_coupling_different_physical_walls_is_still_forbidden(self):
        graph = corner_band()
        graph.boundary_roles["outer"] = "wall"
        # The physical role, not a construction label or boundary name, matters.
        graph.edges[((0,), (1,))].role = "interior"
        report = pg.sizing_structure(graph)
        self.assertEqual(report["wall_tangential_normal_couplings"], 1)
        mixed = report["wall_coupled"][0]
        self.assertEqual(mixed["wall_tangential_chains"], ["outer"])
        self.assertEqual(mixed["wall_normal_chains"], ["solid"])
        self.assertEqual(len(fan_cavity._count_coupling(graph)), 1)
        failure = pg.structure_failures(report, pg.StructureLimits())[0]
        self.assertEqual(failure["metric"], "wall_tangential_normal_couplings")
        self.assertEqual(failure["edges"], [[[0], [1]], [[2], [3]]])

    def test_fan_fronts_keep_wall_provenance_after_the_wall_count_disconnects(self):
        report = pg.sizing_structure(self.fan, reported=1)
        self.assertEqual(report["wall_tangential_normal_couplings"], 2)
        self.assertEqual(len(report["wall_coupled"]), 2)
        components = pg.constraint_components(self.fan)
        for record in report["wall_coupled"]:
            self.assertEqual(record["wall_tangential_chains"], ["wall"])
            self.assertEqual(record["wall_normal_chains"], ["wall"])
            keys = components[record["component"]]
            self.assertFalse(any(self.fan.edges[key].boundary == "wall" for key in keys))
            tangent = tuple(tuple(vertex) for vertex in record["wall_tangential_edge"])
            normal = tuple(tuple(vertex) for vertex in record["wall_normal_edge"])
            self.assertIn(tangent, keys)
            self.assertIn(normal, keys)
            self.assertEqual(self.fan.edges[tangent].role, "front")
            self.assertEqual(self.fan.edges[normal].role, "layer_spoke")
        failure = pg.structure_failures(report, pg.StructureLimits(max_length_ratio=None))[0]
        self.assertEqual(failure["observed"], 2)
        self.assertIsNotNone(failure["component"])
        self.assertIsNotNone(failure["edges"])
        self.assertEqual(len(fan_cavity._count_coupling(self.fan)), 2)
        self.assertEqual(pg.structure_failures(report, pg.StructureLimits(
            max_length_ratio=None, allow_tangential_normal_coupling=True,
        )), [])

    def test_copy_preserves_roles_without_aliasing(self):
        clone = self.fan.copy()
        self.assertEqual(clone.boundary_roles, self.fan.boundary_roles)
        clone.boundary_roles["wall"] = "farfield"
        self.assertEqual(self.fan.boundary_roles["wall"], "wall")
        self.assertEqual(pg.sizing_structure(clone)["wall_tangential_normal_couplings"], 0)

    def test_legacy_graph_infers_only_explicit_wall_front_bands(self):
        clone = self.fan.copy()
        clone.boundary_roles.clear()
        self.assertEqual(pg.wall_direction_components(clone), pg.wall_direction_components(self.fan))


if __name__ == "__main__":
    unittest.main()
