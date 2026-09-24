"""The single-airfoil acceptance case: a C-grid at fifteen chords.

The Aerospatiale A-airfoil (Tohoku database, Re_c 2.1e6, 13 degrees) with the
C-shaped far field one radius downstream, the wake deflected 13 degrees and the
band sized from the measured boundary layer (0.15 c).  The single-body C-grid
producer builds it; this is the acceptance case for that producer.
"""

from pathlib import Path
import unittest

import numpy as np

import pipeline
import wake
from pointlist import read_point_list

GEOMETRY = Path(__file__).resolve().parent / "geometry" / "A-Airfoil-Normalized.dat"
WAKE_DIRECTION = (0.9744, 0.2250)
_RUNS: dict = {}


def options(**overrides) -> pipeline.PipelineOptions:
    settings = dict(
        farfield_shape="cshape",
        farfield_radius=15.0,
        farfield_center=(1.0, 0.0),
        layer_height=0.15,
        grid_width=420,
        coverage_samples=240,
        wake=wake.WakeOptions(enabled=True, direction=WAKE_DIRECTION),
    )
    settings.update(overrides)
    return pipeline.PipelineOptions(**settings)


def run(**overrides):
    key = repr(sorted(overrides.items()))
    if key not in _RUNS:
        loops = [read_point_list(GEOMETRY)]
        _RUNS[key] = pipeline.run_external(["airfoil"], loops, options(**overrides))
    return _RUNS[key]


@unittest.skipUnless(GEOMETRY.is_file(), "run fetch_a_airfoil.py to download the geometry")
class AAirfoilTests(unittest.TestCase):
    def test_geometry_is_normalised_and_sharp(self):
        points = read_point_list(GEOMETRY)
        self.assertEqual(len(points), 1593)
        self.assertTrue(np.allclose(points[0], [1.0, 0.0]))
        self.assertAlmostEqual(float(np.min(points[:, 0])), 0.0, places=9)

    def test_domain_is_the_c_grid_far_field(self):
        result = run()
        chains = {chain.name: chain.role for chain in result.domain.outer.chains}
        self.assertEqual(chains, {"cap": "farfield", "bottom": "farfield", "outlet": "outlet", "top": "farfield"})
        xmin, ymin, xmax, ymax = result.domain.bounds()
        self.assertAlmostEqual(xmin, -14.0, places=6)
        self.assertAlmostEqual(xmax, 16.0, places=6)
        self.assertAlmostEqual(ymax, 15.0, places=6)
        self.assertEqual(len(result.wakes.records), 1)
        self.assertAlmostEqual(result.metric.layer_height, 0.15)

    def test_c_grid_at_fifteen_chords_is_resolved_with_the_wake(self):
        result = run()
        self.assertTrue(result.cgrid["applied"], result.cgrid.get("reason"))
        self.assertTrue(result.admissible, result.problems or result.failures)
        self.assertEqual(len(result.wakes.applied), 1, result.wakes.records)
        self.assertTrue(result.resolved, result.described_failures())
        self.assertGreaterEqual(result.cgrid["levels"], 3)
        front = result.assembly.fronts[0]
        self.assertGreater(float(np.min(front.heights[front.gate_indices])), 0.149)
        self.assertEqual(result.graph.total_index(), 0)
        self.assertEqual(sorted(result.model.boundaries), ["airfoil", "bottom", "cap", "outlet", "top"])


if __name__ == "__main__":
    unittest.main()
