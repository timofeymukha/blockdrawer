"""Construction after round 5: stations facing another wall's, counts per
section, the ring's note pairing wall and front, positions by vertex."""

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


def annulus(model=None):
    """An eccentric annulus: inner circle r 0.35 about (0.3, -0.2), outer unit circle."""
    return run(model or MeshModel(box=(2, 2, 3, 3)), [
        f"add_curve {circle(0.35, 120, (0.3, -0.2))} name=inner",
        f"add_curve {circle(1.0, 240)} name=outer",
        "add_ring inner 0.04 count=24 id=i",
    ])[0]


class FacingTests(unittest.TestCase):
    def test_outer_stations_lie_on_the_inner_normals(self) -> None:
        model = annulus()
        inner = ";".join(f"i_{k}" for k in range(24))
        model, results = run(model, [f"add_ring outer 0.04 side=in facing={inner} id=o"])
        self.assertIn("24 station(s) facing", results[-1]["note"])
        centre = (0.3, -0.2)
        inner_angles = sorted(math.atan2(model.vertices[f"i_{k}"].y - centre[1], model.vertices[f"i_{k}"].x - centre[0])
                              for k in range(24))
        outer_angles = sorted(math.atan2(model.vertices[f"o_{k}"].y - centre[1], model.vertices[f"o_{k}"].x - centre[0])
                              for k in range(24))
        # The inner polyline's vertex normals are radial; each outer station
        # is on the ray from the centre through an inner one (the polyline
        # of the outer circle within its sagitta).
        for a, b in zip(inner_angles, outer_angles):
            self.assertAlmostEqual(a, b, delta=2e-3)

    def test_the_far_side_through_the_body_is_not_taken(self) -> None:
        # The line through an inner station on the wide side also meets the
        # outer circle across the narrow gap, nearer: that hit runs through
        # the inner body and is not the station.
        model = annulus()
        x, y = model.vertices["i_12"].x, model.vertices["i_12"].y
        model, _ = run(model, [f"add_stations outer facing=i_12 id=s"])
        s = model.vertices["s_0"]
        centre = (0.3, -0.2)
        self.assertGreater((s.x - centre[0]) * (x - centre[0]) + (s.y - centre[1]) * (y - centre[1]), 0.0)

    def test_a_point_on_no_other_curve_is_refused(self) -> None:
        model = annulus()
        with self.assertRaisesRegex(CommandError, "lies on no other reference curve"):
            run(model, ["add_stations outer facing=0.3,-0.2"])

    def test_a_corner_faces_along_its_bisector(self) -> None:
        # A channel: the bottom wall has a square step up at x = 1; the top
        # is flat.
        model, _ = run(MeshModel(box=(5, 5, 6, 6)), [
            "add_curve 0,0;1,0;1,0.2;2,0.2 name=bottom",
            "add_curve 2,1;0,1 name=top",
            "add_stations top facing=1,0.2;1,0 id=t",
        ])
        # Both corners' bisectors run up-left at 45 degrees: from (1, 0.2)
        # to x = 0.2 on the top, from (1, 0) to x = 0.
        xs = sorted(model.vertices[f"t_{k}"].x for k in range(2))
        self.assertAlmostEqual(xs[0], 0.0, places=9)
        self.assertAlmostEqual(xs[1], 0.2, places=9)


class CountsTests(unittest.TestCase):
    def test_counts_per_section_of_a_closed_ring(self) -> None:
        # An ellipse with stations at its two ends of the major axis: 6 and
        # 10 edges on the two halves.
        points = ";".join(f"{2 * math.cos(2 * math.pi * k / 200):.12g},{0.5 * math.sin(2 * math.pi * k / 200):.12g}"
                          for k in range(200))
        model, results = run(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {points} name=e", "add_ring e 0.1 fractions=0,0.5 counts=6,10 id=r"])
        upper = [k for k in range(16) if model.vertices[f"r_{k}"].y > 1e-9]
        lower = [k for k in range(16) if model.vertices[f"r_{k}"].y < -1e-9]
        self.assertEqual((len(upper), len(lower)), (5, 9))
        self.assertIn("counts 6,10 by section", results[-1]["note"])

    def test_counts_must_match_the_sections(self) -> None:
        points = circle(1.0, 100)
        with self.assertRaisesRegex(CommandError, "counts gives 3 section.*the stations make 2"):
            run(MeshModel(box=(5, 5, 6, 6)), [
                f"add_curve {points} name=c", "add_ring c 0.1 fractions=0,0.5 counts=4,4,4"])
        with self.assertRaisesRegex(CommandError, "count .* or counts"):
            run(MeshModel(box=(5, 5, 6, 6)), [
                f"add_curve {points} name=c", "add_ring c 0.1 fractions=0,0.5 count=8 counts=4,4"])

    def test_counts_along_an_open_wall(self) -> None:
        model, _ = run(MeshModel(box=(5, 5, 6, 6)), [
            "add_curve 0,0;1,0;2,0;3,0 name=w", "add_ring w 0.1 side=left xs=1 counts=2,4 id=r"])
        xs = sorted(round(model.vertices[f"r_{k}"].x, 9) for k in range(7))
        self.assertEqual(xs, [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])


class OverNoteTests(unittest.TestCase):
    def test_closed_ring_names_the_front_over_each_station(self) -> None:
        _, results = run(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {circle(1.0, 60)} name=c", "add_ring c 0.1 count=8 id=r"])
        note = results[-1]["note"]
        # The first two fronts are made in the order of the first edge's
        # blocks: r_1's front first.
        self.assertIn("front over the wall stations: r_0->r_9, r_1->r_8, r_2..r_7->r_10..r_15", note)

    def test_open_corner_names_both_spoke_ends(self) -> None:
        # A teardrop: a sharp corner at (1, 0).
        points = [(1.0, 0.0)] + [(math.cos(t) * 0.3 - 0.2, math.sin(t) * 0.3)
                                 for t in [math.radians(40 + 280 * k / 30) for k in range(31)]]
        text = ";".join(f"{x:.12g},{y:.12g}" for x, y in points)
        _, results = run(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {text} name=d", "add_ring d 0.05 count=12 fan=false id=r"])
        self.assertRegex(results[-1]["note"], r"open corner r_0: r_\d+ on its r_\d+ side, r_\d+ on its r_1 side")


class PositionByVertexTests(unittest.TestCase):
    def test_to_y_and_stations_take_a_vertex(self) -> None:
        model, _ = run(MeshModel(box=(0, 0, 1, 1)), ["add_vertex 3 2.5 id=top",
                                                                "add_vertex 5 1.75 id=mid"])
        by_id, _ = run(model, ["add_rows edges=v2-v3 direction=0,1 to_y=top stations=mid id=r"])
        by_value, _ = run(model, ["add_rows edges=v2-v3 direction=0,1 to_y=2.5 stations=1.75 id=r"])
        self.assertEqual({k: (v.x, v.y) for k, v in by_id.vertices.items()},
                         {k: (v.x, v.y) for k, v in by_value.vertices.items()})

    def test_a_vertex_station_with_distance_is_refused(self) -> None:
        model, _ = run(MeshModel(box=(0, 0, 1, 1)), ["add_vertex 5 1.75 id=mid"])
        with self.assertRaisesRegex(CommandError, "only with to_x or to_y"):
            run(model, ["add_rows edges=v2-v3 direction=0,1 distance=2 stations=mid"])


if __name__ == "__main__":
    unittest.main()
