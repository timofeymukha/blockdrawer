"""Construction after round 6: a closed curve's closing segment in a patch
selection, coordinates exactly on the lines they were placed on, and the
notes and refusals the trials misread."""

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


class NoteTests(unittest.TestCase):
    def test_closed_ring_names_the_vertex_of_each_normal_and_point(self) -> None:
        model, results = run(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {circle(1.0, 90)} name=body",
            "add_ring body 0.1 count=12 normals=45,200 points=0,-1.2 id=r",
        ])
        note = results[-1]["note"]
        self.assertRegex(note, r"2 station\(s\) by normal: 45->r_\d+, 200->r_\d+")
        self.assertRegex(note, r"1 station\(s\) at points: r_\d+")
        named = note.split("by normal: ")[1].split(";")[0]
        for item in named.split(", "):
            angle, vertex = item.split("->")
            v = model.vertices[vertex]
            self.assertAlmostEqual(math.degrees(math.atan2(v.y, v.x)) % 360.0, float(angle), delta=0.5)
        below = note.split("at points: ")[1].split(";")[0]
        self.assertAlmostEqual(model.vertices[below].y, -1.0, delta=1e-3)

    def test_open_ring_names_the_vertex_of_each_point(self) -> None:
        model, results = run(MeshModel(box=(5, 5, 6, 6)), [
            "add_curve 0,0;1,0;2,0;3,0;4,0 name=floor",
            "add_ring floor 0.1 side=left points=2.5,0.3;0.5,-1 count=8 id=f",
        ])
        named = results[-1]["note"].split("at points: ")[1].split(";")[0].split(", ")
        self.assertEqual(len(named), 2)
        for vertex, x in zip(named, (2.5, 0.5)):
            self.assertAlmostEqual(model.vertices[vertex].x, x, delta=1e-9)

    def test_fill_names_a_block_with_a_sharp_corner(self) -> None:
        _model, results = run(MeshModel(box=(0, 0, 1, 1)), [
            "add_vertex 5 0 id=a", "add_vertex 1.2 1 id=b", "add_fill points=1,0;5,0;1.2,1;1,1"])
        note = results[-1]["note"]
        self.assertIn("within 20 degrees of 0 or 180 on the count-free map", note)
        self.assertRegex(note, r"\(15 deg\)")
        _model, results = run(MeshModel(box=(0, 0, 1, 1)), [
            "add_vertex 3 0 id=a", "add_vertex 3 1 id=b", "add_fill points=1,0;3,0;3,1;1,1"])
        self.assertNotIn("count-free map", results[-1]["note"])

    def test_a_band_over_separate_chains_names_them(self) -> None:
        with self.assertRaises(CommandError) as caught:
            run(MeshModel(box=(0, 0, 4, 2)), [
                f"add_curve {circle(0.5, 60, (10.0, 0.0))} name=body",
                "add_ring body 0.1 count=8 id=r",
                "set_boundary_by body type=wall curves=body",
                "add_band bbox=-1,-1,12,3 height=0.1",
            ])
        text = str(caught.exception)
        self.assertIn("2 separate pieces", text)
        self.assertIn("a loop of 4 edge(s) through v0", text)
        self.assertIn("a loop of 8 edge(s) through r_", text)


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


class MissedLineTests(unittest.TestCase):
    def test_a_grid_line_taken_from_a_vertex_off_its_line_names_that_vertex(self) -> None:
        # Block A's right side has a vertex s 0.001 below y = 0.5; block C's
        # left side a vertex at y = 0.5. The grid between them takes s's y
        # as a station, and its corner (2, 0.499) falls inside C's edge next
        # to C's vertex: s is the vertex off the line, not C's.
        model, _ = run(MeshModel(box=(0, 0, 1, 1)), [
            "set_edge_cells v1-v2 1000",
            "split_edge v1-v2 0.499 id=s",
            "add_vertex 2 0 id=c0", "add_vertex 3 0 id=c1", "add_vertex 3 1 id=c2", "add_vertex 2 1 id=c3",
            "add_block_from_vertices c0,c1,c2,c3",
            "set_edge_cells c0-c3 10",
            "split_edge c0-c3 0.5 id=m",
        ])
        with self.assertRaises(CommandError) as caught:
            run(model, ["add_grid xs=1,2 ys=0,0.5,1"])
        text = str(caught.exception)
        self.assertIn("move_vertex s y=0.5 carry_curves=true", text)
        self.assertNotIn("move_vertex m ", text)


if __name__ == "__main__":
    unittest.main()
