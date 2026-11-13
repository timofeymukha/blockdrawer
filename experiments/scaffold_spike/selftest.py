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


if __name__ == "__main__":
    unittest.main()
