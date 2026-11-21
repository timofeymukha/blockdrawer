"""Construction after round 4: operators that misled, selections, positions."""

import math
import unittest

from blockdrawer.commands import CommandError, apply_commands, batch_digest
from blockdrawer.model import MeshModel
from blockdrawer.overlap import overlapping_blocks
from blockdrawer.quality import assess_quality


def arc_points(radius, start, stop, count, centre=(0.0, 0.0)):
    return [(centre[0] + radius * math.cos(math.radians(start + (stop - start) * k / (count - 1))),
             centre[1] + radius * math.sin(math.radians(start + (stop - start) * k / (count - 1))))
            for k in range(count)]


def vane_points():
    """A thin circular-arc vane about the origin (r 1.38 to 1.42, -80 to -10
    degrees), closed with half circles: the elbow's vane of round 4."""
    outer = arc_points(1.42, -80, -10, 60)
    tip = arc_points(0.02, -10, 170, 9, (1.4 * math.cos(math.radians(-10)), 1.4 * math.sin(math.radians(-10))))
    inner = arc_points(1.38, -10, -80, 60)
    tail = arc_points(0.02, 100, 280, 9, (1.4 * math.cos(math.radians(-80)), 1.4 * math.sin(math.radians(-80))))
    points = outer + tip[1:-1] + inner + tail[1:-1]
    return ";".join(f"{x:.12g},{y:.12g}" for x, y in points)


def cap_stations():
    """Stations on the vane's two rounded ends (three each), for points=."""
    out = []
    for theta, angles in ((-10, (35, 80, 125)), (-80, (145, 190, 235))):
        cx, cy = 1.4 * math.cos(math.radians(theta)), 1.4 * math.sin(math.radians(theta))
        out += [(cx + 0.02 * math.cos(math.radians(a)), cy + 0.02 * math.sin(math.radians(a))) for a in angles]
    return ";".join(f"{x:.12g},{y:.12g}" for x, y in out)


class OverlapTests(unittest.TestCase):
    def _vane(self):
        return apply_commands(MeshModel(box=(-2, -2, -1.9, -1.9)), [
            f"add_curve {vane_points()} name=vane",
            f"add_ring vane 0.04 count=40 points={cap_stations()} id=vn",
        ]).model

    def test_a_grid_cell_over_a_band_is_left_out_and_named(self) -> None:
        # Without outside=, the cells over the vane's band have their centres
        # outside every band block (round 4: blocks=30, skipped_cells=0, and
        # the session validated).
        batch = apply_commands(self._vane(), [
            "add_grid center=0,0 rs=1,1.25,1.55,2 thetas=-90,-75,-60,-45,-30,-15,0 id=p",
        ])
        result = batch.results[0].result
        self.assertGreater(result["skipped_cells"], 0)
        self.assertIn("cell(s) over existing blocks left out", result["note"])
        self.assertEqual(overlapping_blocks(batch.model), [])

    def test_validate_and_quality_name_blocks_over_one_another(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        model.add_vertex(0.25, 0.25, vertex_id="a")
        model.add_vertex(2.0, 0.25, vertex_id="b")
        model.add_vertex(2.0, 0.75, vertex_id="c")
        model.add_vertex(0.25, 0.75, vertex_id="d")
        model.add_block_from_vertices(["a", "b", "c", "d"])
        model.validate()  # block by block it is valid
        found = overlapping_blocks(model)
        self.assertEqual(len(found), 1)
        self.assertEqual({found[0].first, found[0].second}, {"b0", "b1"})
        report = assess_quality(model, interior_cells=16)
        self.assertFalse(report.verdict()["valid"])
        self.assertEqual(report.summary()["overlapping_blocks"], 1)
        self.assertIn("two cells over one place", report.ranked_warnings(1)[0][0])

    def test_neighbours_sharing_curved_edges_do_not_overlap(self) -> None:
        self.assertEqual(overlapping_blocks(self._vane()), [])


class SideFromTopologyTests(unittest.TestCase):
    def test_rows_from_a_bulging_curved_edge_go_away_from_its_block(self) -> None:
        # A thin block whose top edge bulges far above its corners: the
        # corners' mean lies below the edge's midpoint on the other side of
        # the curve. Round 4: "blocks on both sides", alone "not strictly
        # convex".
        model = MeshModel(box=(0, 0, 1, 0.02))
        model = apply_commands(model, ["set_edge_type v2-v3 arc", "set_control_point v2-v3 0 0.5 0.3"]).model
        batch = apply_commands(model, ["add_rows edges=v2-v3 direction=0,1 to_y=2 rows=2 id=r"])
        self.assertEqual(len(batch.model.blocks), 3)
        for block in batch.model.blocks[1:]:
            self.assertGreater(min(batch.model.vertices[v].y for v in block.vertices), 0.019)

    def test_a_chain_with_blocks_on_both_sides_names_the_edges(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        model = apply_commands(model, ["add_grid xs=1,2 ys=1,2"]).model
        with self.assertRaises(CommandError) as caught:
            apply_commands(model, ["add_band edges=v0-v1,v1-v2,v2-v6 height=0.1"])
        self.assertIn("lie on one side", str(caught.exception))


class FillSideTests(unittest.TestCase):
    def test_a_thin_block_beside_a_curved_front_is_no_false_overlap(self) -> None:
        # A band 0.04 thick on an arc of radius 1.4 spanning 20 degrees per
        # block, a polar grid 0.04 beyond its front: the fill block's corner
        # mean falls in the band (the chord sags 0.018). Before: "would lie
        # over the existing block w_b0".
        points = ";".join(f"{x:.12g},{y:.12g}" for x, y in arc_points(1.4, 0, 60, 61))
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {points} name=wall",
            "add_ring wall 0.04 side=right count=3 id=w",
            "add_grid center=0,0 rs=1.48,1.6 thetas=0,20,40,60 id=g",
        ]).model

        def at(r, t):
            target = (r * math.cos(math.radians(t)), r * math.sin(math.radians(t)))
            return min(model.vertices.values(), key=lambda v: math.dist((v.x, v.y), target)).id

        corners = ",".join([at(1.44, 0), at(1.44, 60), at(1.48, 60), at(1.48, 0)])
        batch = apply_commands(model, [f"add_fill {corners} id=f"])
        self.assertEqual(len(batch.results[0].result["created_blocks"]), 3)
        self.assertEqual(overlapping_blocks(batch.model), [])
        # The curved front and the straight chord between its ends are two
        # paths: the note names the other.
        self.assertIn("NOT the only reading: it also bounds a region open (straight)",
                      batch.results[0].result["note"])

    def test_one_path_is_one_reading(self) -> None:
        # A side that is one existing edge, or collinear edges, and the
        # straight side between the same corners are the same path.
        model = apply_commands(MeshModel(box=(0, 0, 1, 1)), ["add_grid xs=2,3 ys=0,1"]).model
        note = apply_commands(model, ["add_fill points=1,0;2,0;2,1;1,1"]).results[0].result["note"]
        self.assertNotIn("NOT the only reading", note)
        model = apply_commands(MeshModel(box=(0, 0, 1, 1)), ["add_grid xs=1,2,3 ys=3,4",
                                                              "add_grid xs=3,4 ys=0,1,2,3"]).model
        note = apply_commands(model, ["add_fill v2,v13,v8,v4"]).results[0].result["note"]
        self.assertNotIn("NOT the only reading", note)

    def test_the_note_names_another_reading(self) -> None:
        # Corners on two separate blocks: side 2 is either the long chain
        # round the second block or the straight way.
        model = MeshModel(box=(0, 0, 1, 1))
        model = apply_commands(model, ["add_grid xs=0,1 ys=2,3"]).model
        result = apply_commands(model, ["add_fill v3,v2,v5,v4"]).results[0].result
        self.assertIn("side", result["note"])


class HookTests(unittest.TestCase):
    def test_a_mitre_front_does_not_hook_back(self) -> None:
        # A circle with a plate: the front of the cylinder's band at the
        # concave junction ends where the plate's front line passes; its
        # spline leaves the end within 45 degrees of the wall.
        from blockdrawer.construct import HOOK_TURN, _turn

        circle = [(0.5 * math.cos(math.radians(a)), 0.5 * math.sin(math.radians(a)))
                  for a in range(4, 357, 2)]
        y = 0.5 * math.sin(math.radians(4))
        plate = [(x / 10.0, -y) for x in range(5, 16)] + [(x / 10.0, y) for x in range(15, 4, -1)]
        points = ";".join(f"{x:.12g},{v:.12g}" for x, v in circle + plate)
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {points} name=body", "add_ring body 0.1 count=30 id=r",
        ]).model
        for key, geometry in model.edge_geometry.items():
            if geometry.kind != "spline" or not geometry.points:
                continue
            a, b = model.vertices[key[0]], model.vertices[key[1]]
            first, final = geometry.points[0], geometry.points[-1]
            chord = (b.x - a.x, b.y - a.y)
            self.assertLess(_turn(chord, (first[0] - a.x, first[1] - a.y)), math.radians(100))
            self.assertLess(_turn((-chord[0], -chord[1]), (final[0] - b.x, final[1] - b.y)), math.radians(100))
        self.assertAlmostEqual(math.degrees(HOOK_TURN), 45.0)


class MissedEndTests(unittest.TestCase):
    def test_a_grid_line_missing_a_front_vertex_names_it(self) -> None:
        # The U-bend's divider: a straight stretch along y = 0.25 ending at
        # x = 0, then a half circle; the ring's front vertex at the joint lies
        # 0.0013 off x = 0 (the mitre of the two polyline segments).
        line = [(-2 + 0.05 * k, 0.25) for k in range(40)]
        arc = arc_points(0.25, 90, -90, 61)
        back = [(-0.05 * k, -0.25) for k in range(1, 41)]
        points = ";".join(f"{x:.12g},{y:.12g}" for x, y in line + arc + back)
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {points} name=divider",
            "add_ring divider 0.1 side=left points=0,0.25;0.25,0;0,-0.25 id=d",
        ]).model
        with self.assertRaises(CommandError) as caught:
            apply_commands(model, ["add_grid xs=-2,0 ys=0.35,0.6"])
        self.assertIn("is only 0.00131 from it", str(caught.exception))
        self.assertIn("x=0 carry_curves=true", str(caught.exception))


class DigestTests(unittest.TestCase):
    def test_a_block_made_under_a_reused_id_is_counted(self) -> None:
        # The placeholder b0 removed, the next block made takes its ID
        # (round 4's U-bend digest: "19 blocks" for 20).
        model = MeshModel(box=(5, 5, 6, 6))
        batch = apply_commands(model, ["add_grid xs=0,1 ys=0,1", "remove_edge v0-v1",
                                       "add_grid xs=1,2 ys=0,1"])
        self.assertIn("b0", [block.id for block in batch.model.blocks])
        digest = batch_digest(model, batch)
        self.assertEqual(digest["created_counts"]["blocks"], len(batch.model.blocks))
        self.assertEqual(digest["removed_counts"]["blocks"], 1)

    def test_moved_corners_keep_a_block(self) -> None:
        # smooth moves every free vertex: no block is made or removed.
        model = apply_commands(MeshModel(box=(0, 0, 1, 1)), ["add_grid xs=0,1,2 ys=0,1,2",
                                                              "move_vertex v2 dx=0.3 dy=0.2"]).model
        batch = apply_commands(model, ["move_vertex v2 dx=-0.1 dy=0.05"])
        digest = batch_digest(model, batch)
        self.assertNotIn("blocks", digest["created_counts"])
        self.assertNotIn("blocks", digest["removed_counts"])
        batch = apply_commands(model, ["smooth"])
        self.assertNotEqual((batch.model.vertices["v2"].x, batch.model.vertices["v2"].y), (1.3, 1.2))
        digest = batch_digest(model, batch)
        self.assertNotIn("blocks", digest["created_counts"])
        self.assertNotIn("blocks", digest["removed_counts"])


class CommandLineTests(unittest.TestCase):
    def test_validate_names_blocks_over_one_another(self) -> None:
        import tempfile
        from pathlib import Path

        from blockdrawer.session import save_session
        from tests.test_cli import run_cli

        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "s.json"
            model = MeshModel(box=(0, 0, 1, 1))
            for name, x, y in (("a", 0.25, 0.25), ("b", 2, 0.25), ("c", 2, 0.75), ("d", 0.25, 0.75)):
                model.add_vertex(x, y, vertex_id=name)
            model.add_block_from_vertices(["a", "b", "c", "d"])
            save_session(model, session)
            code, _, err = run_cli("validate", str(session))
            self.assertEqual(code, 1)
            self.assertIn("lie over one another", err)

    def test_band_note_says_which_end_start_and_end_set(self) -> None:
        model = MeshModel(box=(0, 0, 4, 1))
        batch = apply_commands(model, ["add_band between=v1,v0 height=0.2 end=0.3,-1"])
        note = batch.results[0].result["note"]
        self.assertIn("end= (0.3, -1) set the spoke at v1", note)
        self.assertIn("not between='s order", note)


if __name__ == "__main__":
    unittest.main()
