"""Construction after round 6: a closed curve's closing segment in a patch
selection, coordinates exactly on the lines they were placed on."""

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


class ExactLineTests(unittest.TestCase):
    """Round 6: a vertex an operator places on a line it was given has that
    coordinate exactly, so a selection by the line takes every edge."""

    def test_rows_on_their_stations_and_target_line(self) -> None:
        points = [(0.1 * k + 0.0137 * k * k, 0.01 * math.sin(3.1 * k)) for k in range(12)]
        commands = [f"add_vertex {x!r} {y!r} id=c{k}" for k, (x, y) in enumerate(points)]
        commands += [f"add_vertex {x!r} -1 id=d{k}" for k, (x, _y) in enumerate(points)]
        commands += [f"add_block_from_vertices c{k},c{k + 1},d{k + 1},d{k}" for k in range(len(points) - 1)]
        model, _ = run(MeshModel(box=(10, 10, 11, 11)), commands + ["remove_edge v0-v1"])
        chain = ",".join(f"c{k}-c{k + 1}" for k in range(len(points) - 1))
        for extra in ("", "far_ratio=2", "spread=3", "far_min=0.4", "spread=2 far_ratio=1.5"):
            carried, _ = run(model, [f"add_rows edges={chain} direction=0.2,1 to_y=7.9 stations=0.3,2.71 id=r {extra}"])
            rows = {v.y for k, v in carried.vertices.items() if k.startswith("r_")}
            self.assertEqual(rows, {0.3, 2.71, 7.9}, extra)
            carried, _ = run(carried, ["set_boundary_by top type=patch bbox=-10,7.9,20,7.9"])
            self.assertEqual(sum(1 for name in carried.edge_boundaries.values() if name == "top"), 11, extra)

    def test_a_box_band_ends_exactly_on_its_sides_and_corners(self) -> None:
        model, _ = run(MeshModel(box=(-8, -8, -7, -7)), [
            f"add_curve {circle(0.3, 90, (0.1, 0.0))} name=body",
            "add_ring body 0.05 normals=45,135,225,315 count=24 id=r",
            "set_boundary_by body type=wall curves=body",
            "add_band bbox=-0.5,-0.5,0.7,0.5 to_box=-0.9,-0.7,1.1,0.7 id=x",
        ])
        box = [v for k, v in model.vertices.items() if k.startswith("x_")]
        self.assertTrue(box)
        for v in box:
            self.assertTrue(v.x in (-0.9, 1.1) or v.y in (-0.7, 0.7), (v.id, v.x, v.y))
        for corner in ((-0.9, -0.7), (1.1, -0.7), (1.1, 0.7), (-0.9, 0.7)):
            self.assertIn(corner, {(v.x, v.y) for v in box})

    def test_stations_at_crossings_have_the_line_coordinate(self) -> None:
        model, _ = run(MeshModel(box=(5, 5, 6, 6)), [
            "add_curve 0,0;0.37,0.11;1.13,0.29;2.71,0.07;3.3,0.41 name=wall",
            "add_stations wall xs=0.3,1.7,2.9 ys=0.35 id=s",
            "add_ring wall 0.05 side=left xs=0.7,2.2 id=w",
        ])
        xs = {round(v.x, 9): v.x for k, v in model.vertices.items() if k.startswith("s_")}
        for value in (0.3, 1.7, 2.9):
            self.assertEqual(xs[value], value)
        self.assertIn(0.35, {v.y for k, v in model.vertices.items() if k.startswith("s_")})
        self.assertTrue({0.7, 2.2} <= {v.x for k, v in model.vertices.items() if k.startswith("w_")})


if __name__ == "__main__":
    unittest.main()
