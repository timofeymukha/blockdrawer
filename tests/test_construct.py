import math
import unittest

from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data


def square_curve(model_commands, half=0.5, centre=(0.0, 0.0), name="body"):
    cx, cy = centre
    points = ";".join(
        f"{cx + x},{cy + y}" for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))
    )
    return model_commands + [f"add_curve {points} name={name}"]


def circle_points(radius, count, centre=(0.0, 0.0)):
    return ";".join(
        f"{centre[0] + radius * math.cos(2 * math.pi * k / count)},"
        f"{centre[1] + radius * math.sin(2 * math.pi * k / count)}"
        for k in range(count)
    )


class GridTests(unittest.TestCase):
    def test_a_step_from_stations_reuses_the_first_block(self) -> None:
        model = MeshModel(box=(-5, 0, 0, 0.15))
        batch = apply_commands(model, [
            "add_grid xs=-5,0,0.15,1.5,7,20 ys=-1,-0.85,-0.15,0,0.15,0.85,1 skip=-5,-1,0,0",
        ])
        result = batch.results[0].result
        self.assertEqual(len(batch.model.blocks), 27)
        # Three skipped in the box, one covered by the first block, whose
        # vertices the cells round it reuse (its lower-left corner is used
        # by no new cell).
        self.assertEqual(result["skipped_cells"], 4)
        self.assertEqual(result["reused_vertices"], 3)
        # A rectangle of the grid: every corner a right angle, so validation passed.
        batch.model.validate()

    def test_a_polar_grid_leaves_out_the_body_and_uses_arcs(self) -> None:
        model = MeshModel(box=(-3, -3, -2, -2))
        batch = apply_commands(model, [
            f"add_curve {circle_points(0.5, 64)} name=disc",
            "add_grid center=0,0 rs=0.25,0.5,0.75 thetas=0,90 period=90 outside=disc id=ring",
        ])
        data = batch.results[1].result
        # Eight cells: four inside the disc are left out.
        self.assertEqual(len(data["created_blocks"]), 4)
        self.assertEqual(data["skipped_cells"], 4)
        self.assertEqual(data["created_vertices"][0], "ring_0")
        self.assertTrue(all(block.startswith("ring_b") for block in data["created_blocks"]))
        arcs = [g for g in batch.model.edge_geometry.values() if g.kind == "arc"]
        self.assertEqual(len(arcs), 8)

    def test_a_grid_conforms_to_what_is_there_and_refuses_a_t_junction(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        model = apply_commands(model, ["split_edge v0-v1 0.5"]).model
        # The split vertex at (0.5, 0) and (0.5, 1) becomes an x station.
        batch = apply_commands(model, ["add_grid xs=0,1 ys=-1,0"])
        self.assertEqual(len(batch.model.blocks), 4)
        occurrences = batch.model.edge_occurrences()
        bottom = [e for e, items in occurrences.items() if len(items) == 2
                  and all(abs(batch.model.vertices[v].y) < 1e-12 for v in e)]
        self.assertEqual(len(bottom), 2)
        # Without conform the grid would cut the split edge: refused by name.
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_grid xs=0,0.25,1 ys=-1,0 conform=false"])
        self.assertIn("non-conformal", str(caught.exception))

    def test_a_concave_cell_is_refused_by_name(self) -> None:
        model = MeshModel(box=(-3, -3, -2, -2))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_grid center=0,0 rs=1,2 thetas=0,200"])
        self.assertIn("180 degrees", str(caught.exception))


class BandTests(unittest.TestCase):
    def test_a_band_on_a_wall_with_ends_following_the_sides(self) -> None:
        model = apply_commands(MeshModel(box=(0, 0, 4, 1)), [
            "add_band edges=v0-v1 height=0.2 rows=2 id=band",
        ]).model
        self.assertEqual(len(model.blocks), 3)
        ys = sorted({round(v.y, 9) for v in model.vertices.values()})
        self.assertEqual(ys, [-0.2, -0.1, 0.0, 1.0])
        xs = sorted({round(v.x, 9) for v in model.vertices.values()})
        self.assertEqual(xs, [0.0, 4.0])

    def test_a_band_round_a_convex_corner_gets_a_fan(self) -> None:
        model = apply_commands(MeshModel(box=(0, 0, 1, 1)), [
            "add_band edges=v0-v1,v1-v2 height=0.25",
        ]).model
        # Two edge blocks and one corner block at v1.
        self.assertEqual(len(model.blocks), 4)
        corner = model.vertices["v1"]
        around = [b for b in model.blocks if "v1" in b.vertices]
        self.assertEqual(len(around), 4)   # the first block, two band blocks, the fan
        self.assertEqual((corner.x, corner.y), (1.0, 0.0))

    def test_a_ring_of_arcs_runs_to_a_box_with_its_corners(self) -> None:
        model = MeshModel(box=(-3, -3, -2, -2))
        model = apply_commands(model, [
            "add_grid center=0,0 rs=0.5,0.6 thetas=-45,0 period=90",
            "set_boundary_by body type=wall bbox=-0.55,-0.55,0.55,0.55",
        ]).model
        batch = apply_commands(model, ["add_band bbox=-0.7,-0.7,0.7,0.7 to_box=-1,-1,1,1"])
        corners = {(round(v.x, 9), round(v.y, 9)) for v in batch.model.vertices.values()}
        for corner in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            self.assertIn((float(corner[0]), float(corner[1])), corners)
        self.assertEqual(len(batch.model.blocks), 1 + 8 + 8)

    def test_a_ring_round_a_square_curve(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), square_curve([]) + [
            "add_ring body 0.1 count=8",
            "set_boundary_by wall type=wall curves=body",
        ]).model
        walls = [e for e, name in model.edge_boundaries.items() if name == "wall"]
        self.assertEqual(len(walls), 8)
        # Eight edge blocks and one fan block at each of the four corners.
        self.assertEqual(len(model.blocks), 1 + 8 + 4)


class BoundaryTests(unittest.TestCase):
    def test_selectors_combine(self) -> None:
        model = apply_commands(MeshModel(box=(0, 0, 2, 1)), ["split_edge v0-v1 0.5"]).model
        batch = apply_commands(model, [
            "set_boundary_by floor type=wall bbox=0,0,2,0",
            "set_boundary_by left bbox=-1,-1,0,2 unassigned=true",
        ])
        floor = [e for e, n in batch.model.edge_boundaries.items() if n == "floor"]
        self.assertEqual(len(floor), 2)
        self.assertEqual(batch.results[1].result["edges"], 1)
        with self.assertRaises(Exception):
            apply_commands(model, ["set_boundary_by x bbox=10,10,11,11"])
        with self.assertRaises(Exception):
            apply_commands(batch.model, ["set_boundary_by again bbox=0,0,2,0 unassigned=true"])
        chain = apply_commands(model, ["set_boundary_by side between=v0,v2"]).model
        self.assertTrue(all(name == "side" for name in chain.edge_boundaries.values()))


class OperatorContractTests(unittest.TestCase):
    def test_one_history_entry_and_determinism(self) -> None:
        commands = [
            "add_grid xs=-5,0,0.15,1.5 ys=-1,0,1 skip=-5,-1,0,0",
            "set_boundary_by walls type=wall bbox=-5,-1,1.5,-1",
            "add_band edges=v0-v3 height=0.1",
        ]
        first = apply_commands(MeshModel(box=(-5, 0, 0, 1)), commands).model
        second = apply_commands(MeshModel(box=(-5, 0, 0, 1)), commands).model
        self.assertEqual(to_data(first), to_data(second))
        model = MeshModel(box=(-5, 0, 0, 1))
        history = ModelHistory(model)
        before = to_data(model)
        batch = apply_commands(model, commands[:1])
        history.record(batch.model)
        self.assertEqual(to_data(history.undo()), before)
        digest = batch_digest(model, batch)
        self.assertEqual(digest["notes"][0]["op"], "add_grid")

    def test_a_refusal_leaves_the_model_unchanged(self) -> None:
        model = MeshModel(box=(0, 0, 1, 1))
        before = to_data(model)
        with self.assertRaises(TopologyError):
            from blockdrawer.construct import add_band

            add_band(model, edges=(edge_key("v0", "v1"),), height=0.1, to_box=(0, 0, 1, 1))
        self.assertEqual(to_data(model), before)


if __name__ == "__main__":
    unittest.main()
