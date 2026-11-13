"""Self-test of the field solver and the tracer: a square, an annulus, an L.

python experiments/scaffold_spike/selftest.py
"""

import math
import os
import sys
import unittest
from pathlib import Path

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import cases  # noqa: E402
import crossfield  # noqa: E402


class FieldTests(unittest.TestCase):
    def test_square_is_one_quad_without_singularities(self):
        res = crossfield.run(cases.square()["loops"], n=60)
        self.assertEqual(res.singularities, [])
        self.assertEqual(res.stats["patches"], 1)
        self.assertEqual(res.stats["four_node_patches"], 1)
        # the field is axis aligned everywhere: 4 theta = 0 mod 2 pi
        z = res.z[res.fluid]
        self.assertLess(np.abs(np.angle(z)).max(), 1e-6)

    def test_annulus_field_is_polar_without_singularities(self):
        res = crossfield.run(cases.annulus()["loops"], n=100)
        self.assertEqual(res.singularities, [])
        self.assertEqual(res.traces, [])
        # at (0.7, 0) the cross is radial/tangential
        z = crossfield.sample(res, 0.7, 0.0)
        self.assertLess(abs(math.remainder(np.angle(z), 2 * math.pi)), 0.05)

    def test_l_shape_reflex_corner_gives_two_separatrices_and_three_quads(self):
        res = crossfield.run(cases.l_shape()["loops"], n=120)
        self.assertEqual(res.singularities, [])
        reflex = [c for c in res.corners if c[3] == 3]
        self.assertEqual(len(reflex), 1)
        self.assertEqual(len(res.traces), 2)
        self.assertTrue(all(tr["end"] == "boundary" for tr in res.traces))
        self.assertEqual(res.stats["four_node_patches"], 3)
        ends = sorted(tuple(np.round(tr["points"][-1], 1)) for tr in res.traces)
        self.assertEqual(ends, [(0.0, 1.0), (1.0, 0.0)])

    def test_tracer_follows_the_field_to_the_wall(self):
        res = crossfield.solve_field(cases.square()["loops"], n=60)
        points, kind = crossfield.trace(res, 0.5, 0.5, 0.0, [], 10.0)
        self.assertEqual(kind, "boundary")
        self.assertAlmostEqual(points[-1][1], 0.5, places=6)
        self.assertGreater(points[-1][0], 0.95)

    def test_deterministic(self):
        a = crossfield.run(cases.l_shape()["loops"], n=80)
        b = crossfield.run(cases.l_shape()["loops"], n=80)
        self.assertEqual(a.stats["patches"], b.stats["patches"])
        self.assertTrue(np.array_equal(np.nan_to_num(a.z), np.nan_to_num(b.z)))


def _grid(nx, ny, skip=()):
    nodes = {}
    ids = {}
    for j in range(ny + 1):
        for i in range(nx + 1):
            ids[(i, j)] = len(ids) + 1
            nodes[ids[(i, j)]] = (float(i), float(j))
    quads = []
    for j in range(ny):
        for i in range(nx):
            if (i, j) in skip:
                continue
            quads.append([ids[(i, j)], ids[(i + 1, j)], ids[(i + 1, j + 1)], ids[(i, j + 1)]])
    used = {v for q in quads for v in q}
    return {k: v for k, v in nodes.items() if k in used}, quads


class BaseComplexTests(unittest.TestCase):
    def test_a_structured_grid_is_one_block_with_its_counts(self):
        import quadmesh

        nodes, quads = _grid(4, 3)
        bc = quadmesh.base_complex(nodes, quads, [])
        self.assertEqual(len(bc["patches"]), 1)
        sides, corners = quadmesh.patch_layout(nodes, quads, bc, bc["patches"][0])
        self.assertEqual(sorted(len(side) - 1 for side in sides), [3, 3, 4, 4])

    def test_an_l_grid_is_cut_at_its_reflex_corner(self):
        import quadmesh

        skip = {(i, j) for i in range(2, 4) for j in range(2, 4)}
        nodes, quads = _grid(4, 4, skip)
        bc = quadmesh.base_complex(nodes, quads, [])
        self.assertEqual(len(bc["patches"]), 3)
        self.assertEqual([info[1] for info in bc["irregular"].values() if info[2] == 3], [3])

    def test_a_valence_three_vertex_cuts_three_blocks(self):
        import quadmesh

        # three quads round a centre (a triangle split into quads), each
        # subdivided 2 x 2: one interior valence-3 vertex, three blocks
        centre = np.array([0.0, 0.0])
        corners = [np.array([math.cos(a), math.sin(a)]) for a in (math.pi / 2, 7 * math.pi / 6, 11 * math.pi / 6)]
        mids = [(corners[k] + corners[(k + 1) % 3]) / 2 for k in range(3)]
        nodes, quads, index = {}, [], {}

        def node(p):
            key = (round(p[0], 9), round(p[1], 9))
            if key not in index:
                index[key] = len(index) + 1
                nodes[index[key]] = key
            return index[key]

        for k in range(3):
            a, b, c, d = centre, mids[k - 1], corners[k], mids[k]
            for j in range(2):
                for i in range(2):
                    def at(u, v):
                        return (1 - u) * (1 - v) * a + u * (1 - v) * d + u * v * c + (1 - u) * v * b
                    quads.append([node(at(i / 2, j / 2)), node(at((i + 1) / 2, j / 2)),
                                  node(at((i + 1) / 2, (j + 1) / 2)), node(at(i / 2, (j + 1) / 2))])
        bc = quadmesh.base_complex(nodes, quads, [])
        self.assertEqual(sum(1 for info in bc["irregular"].values() if info[0] == "interior"), 1)
        self.assertEqual(len(bc["patches"]), 3)


class GmshTests(unittest.TestCase):
    def test_the_step_through_quadqs_is_a_valid_session(self):
        import tempfile

        import quadmesh

        if quadmesh.gmsh_binary() is None:
            self.skipTest("gmsh not found")
        with tempfile.TemporaryDirectory() as work:
            row, result = quadmesh.analyse("backward_step", "quadqs", 20, work, str(Path(work) / "s.json"))
        self.assertEqual(row.get("session"), "valid", row)
        self.assertEqual(row["irregular_interior"], 2)


if __name__ == "__main__":
    unittest.main()
