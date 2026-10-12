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


class TrimBacktrackTests(unittest.TestCase):
    """A band front section ending at a seam loses the hook the offset leaves there."""

    def test_the_hook_before_a_seam_point_is_removed(self) -> None:
        # The flap's lower band front at its base corner: the offset of the
        # flank runs past the seam point and the last segment doubles back.
        section = np.array([
            [0.97707, -0.14172], [0.97735, -0.14196], [0.97924, -0.1435],
            [0.98188, -0.14523], [0.98188, -0.14523], [0.97864, -0.14288],
        ])
        trimmed = layer_module.trim_backtrack(section)
        np.testing.assert_allclose(trimmed[0], section[0])
        np.testing.assert_allclose(trimmed[-1], section[-1])
        self.assertEqual(len(trimmed), 3)
        for first, second, third in zip(trimmed, trimmed[1:], trimmed[2:]):
            self.assertGreater(float(np.dot(second - first, third - second)), 0.0)

    def test_a_clean_section_is_unchanged(self) -> None:
        section = np.array([[0.0, 0.0], [1.0, 0.1], [2.0, 0.0], [3.0, 0.2]])
        np.testing.assert_allclose(layer_module.trim_backtrack(section), section)
        two = np.array([[0.0, 0.0], [1.0, 1.0]])
        np.testing.assert_allclose(layer_module.trim_backtrack(two), two)
