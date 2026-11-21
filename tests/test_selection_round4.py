"""Selections and positions after round 4: render's box, bbox= chains,
normals near a point, fill corners by position, a vertex between two."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from blockdrawer.commands import CommandError, apply_commands
from blockdrawer.model import MeshModel
from blockdrawer.session import save_session
from tests.test_cli import run_cli
from tests.test_construct_round4 import arc_points, cap_stations, vane_points


class SelectionTests(unittest.TestCase):
    def test_a_box_on_a_curved_front_s_exact_extent_selects_it(self) -> None:
        circle = ";".join(f"{x:.12g},{y:.12g}" for x, y in arc_points(0.25, 0, 360, 73)[:-1])
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {circle} name=c", "add_ring c 0.05 count=8 id=r",
        ]).model
        batch = apply_commands(model, ["add_band bbox=-0.3,-0.3,0.3,0.3 height=0.05 id=b"])
        self.assertEqual(len(batch.results[0].result["created_blocks"]), 8)

    def test_a_box_names_the_edge_it_left_out_at_a_chain_end(self) -> None:
        model = apply_commands(MeshModel(box=(0, 0, 1, 1)), ["add_grid xs=1,2 ys=0,1"]).model
        note = apply_commands(model, ["add_band bbox=-0.1,-0.1,1.1,0.1 height=0.1"]).results[0].result["note"]
        self.assertIn("the box left out", note)
        self.assertIn("leaves the box by 0.9", note)

    def test_an_empty_box_names_the_edges_partly_in_it(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        with self.assertRaises(CommandError) as caught:
            apply_commands(model, ["add_band bbox=0,0,0.5,0.5 height=0.1"])
        self.assertIn("partly in it", str(caught.exception))

    def test_normals_near_picks_one_of_several_places(self) -> None:
        # On the thin vane the normal at 120 degrees occurs on the inner arc
        # (at -60) and on both rounded ends (round 4: cascade, splitter, vane).
        model = MeshModel(box=(5, 5, 6, 6))
        with self.assertRaises(CommandError) as caught:
            apply_commands(model, [f"add_curve {vane_points()} name=v", f"add_ring v 0.01 count=40 points={cap_stations()} normals=120"])
        self.assertIn("at 3 places", str(caught.exception))
        self.assertIn("near=", str(caught.exception))
        self.assertIn("points=", str(caught.exception))
        batch = apply_commands(model, [f"add_curve {vane_points()} name=v",
                                       f"add_ring v 0.01 count=40 points={cap_stations()} normals=120 near=0.69,-1.2"])
        target = (1.38 * math.cos(math.radians(-60)), 1.38 * math.sin(math.radians(-60)))
        self.assertLess(min(math.dist((v.x, v.y), target) for v in batch.model.vertices.values()), 1e-3)

    def test_fill_corners_by_points(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        model = apply_commands(model, ["add_grid xs=2,3 ys=0,1"]).model
        batch = apply_commands(model, ["add_fill points=1,0;2,0;2,1;v2"])
        self.assertEqual(len(batch.model.blocks), 3)

    def test_add_vertex_between(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        batch = apply_commands(model, ["add_vertex between=v0,v2 id=m",
                                       "add_vertex between=v0,v1 fraction=0.25 dy=-1 id=q"])
        m, q = batch.model.vertices["m"], batch.model.vertices["q"]
        self.assertAlmostEqual(m.x, 0.5)
        self.assertAlmostEqual(m.y, 0.5)
        self.assertAlmostEqual(q.x, 0.25)
        self.assertAlmostEqual(q.y, -1.0)
        with self.assertRaises(CommandError):
            apply_commands(model, ["add_vertex between=v0,v1 at=v2"])


class RenderBoxTests(unittest.TestCase):
    def test_render_takes_bbox_and_the_comma_form(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from blockdrawer.session import save_session
        from tests.test_cli import run_cli

        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "s.json"
            save_session(MeshModel(), session)
            output = Path(directory) / "x.svg"
            for options in (["--bbox", "-0.5,-0.5,1.5,1.5"], ["--bbox", "-0.5", "-0.5", "1.5", "1.5"],
                            ["--bounds", "-0.5,-0.5,1.5,1.5"], ["--bbox=-0.5,-0.5,1.5,1.5"]):
                code, out, err = run_cli("render", str(session), "-o", str(output), *options, "--json")
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["bounds"], [-0.5, -0.5, 1.5, 1.5])


if __name__ == "__main__":
    unittest.main()
