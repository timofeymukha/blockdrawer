"""Construction after round 6: a closed curve's closing segment in a patch
selection."""

import math
import unittest

from blockdrawer.commands import CommandError, apply_commands
from blockdrawer.model import MeshModel


def run(model, commands):
    """The batch's model and each command's result."""
    batch = apply_commands(model, commands)
    return batch.model, [item.result for item in batch.results]


def circle(radius, count, centre=(0.0, 0.0), start=0.0):
    return ";".join(
        f"{centre[0] + radius * math.cos(start + 2 * math.pi * k / count):.12g},"
        f"{centre[1] + radius * math.sin(start + 2 * math.pi * k / count):.12g}"
        for k in range(count))


class ClosingSegmentTests(unittest.TestCase):
    def test_a_wide_band_block_across_the_seam_is_taken(self) -> None:
        # Round 6, three cylinders: the curve starts at 0 degrees and the
        # ring's stations at 344 and 45 degrees make one wall edge across
        # the implied closing segment. Its band block's corners average to
        # a point inside the circle; the block lies outside it.
        model, results = run(MeshModel(box=(-8, -8, -7, -7)), [
            f"add_curve {circle(0.25, 240, (1.1, 0.75))} name=body",
            "add_ring body 0.04 normals=45,135,225,315,195.945396,344.054604 id=r",
            "set_boundary_by wall type=wall curves=body",
        ])
        self.assertEqual(results[-1]["edges"], 6)
        walls = [edge for edge, name in model.edge_boundaries.items() if name == "wall"]
        self.assertEqual(len(walls), 6)

    def test_a_domain_bounded_by_the_chord_is_still_left_alone(self) -> None:
        # An open wall whose chord closes the domain (a cavity lid): the
        # block along the chord lies inside the closed curve and is not
        # taken by the curve's selection.
        model, results = run(MeshModel(box=(0, -1, 2, 0)), [
            "add_curve 0,0;0,-1;2,-1;2,0 name=cavity",
            "set_boundary_by walls type=wall curves=cavity",
        ])
        self.assertEqual(results[-1]["edges"], 3)
        lid = [edge for edge in model.edges() if edge not in model.edge_boundaries]
        self.assertEqual(len(lid), 1)
        self.assertTrue(all(model.vertices[v].y == 0.0 for v in lid[0]))

if __name__ == "__main__":
    unittest.main()
