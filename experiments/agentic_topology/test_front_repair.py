"""The optional final height search has a bounded, explicitly checked result."""

from dataclasses import replace
import unittest

from test_agentic_topology import cases, g2, layer_module, np


class FinalFrontSearchTests(unittest.TestCase):
    def test_exhausted_local_search_requests_a_cut_before_global_scaling(self):
        loop = g2.orient_anticlockwise(g2.close_loop(cases.circle((0, 0), 1, 120), 0))
        options = replace(layer_module.LayerOptions(), local_attempts=0, bisection_steps=4)
        inputs = dict(fluid_sign=1, wall_loops=[loop], requested=.1, options=options, name="disk")
        gates = [0, 1.5, 3, 4.5]
        with self.assertRaisesRegex(layer_module.LayerError, "wall section needs a cut"):
            layer_module.build_front(loop, gates, **inputs)
        front = layer_module.build_front(loop, gates, allow_scale=True, **inputs)
        self.assertTrue(g2.is_simple(front.front, closed=True))
        self.assertAlmostEqual(front.shrink, 1 - 2**-4)
        radii = np.linalg.norm(front.front[:-1], axis=1)
        self.assertTrue(np.allclose(radii, 1 + .1 * front.shrink, atol=2e-3))
        self.assertTrue(any("local repairs did not converge" in note for note in front.notes))


if __name__ == "__main__":
    unittest.main()
