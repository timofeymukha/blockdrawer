import math
import unittest

from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import EdgeGeometry, TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import to_data


def square_curve(model_commands, half=0.5, centre=(0.0, 0.0), name="body"):
    cx, cy = centre
    points = ";".join(
        f"{cx + x},{cy + y}" for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))
    )
    return model_commands + [f"add_curve {points} name={name}"]


def _segment_gap(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length <= 0.0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


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

    def test_the_band_note_gives_the_front_in_chain_order(self) -> None:
        # A chain of three edges along y = 0; the new front vertices are named
        # in creation order (B_0 is not at the chain's start), the note in
        # the chain's order.
        model = apply_commands(MeshModel(box=(0, 0, 3, 1)), [
            "split_edge v0-v1 0.3333333333333333 id=s", "split_edge s-v1 0.5 id=t",
        ]).model
        result = apply_commands(model, ["add_band edges=v0-s,s-t,t-v1 height=0.2 id=B"]).results[0].result
        built = apply_commands(model, ["add_band edges=v0-s,s-t,t-v1 height=0.2 id=B"]).model
        note = result["note"]
        self.assertIn("front along the wall chain v0..v1, in its order: ", note)
        front = note.split("in its order: ")[1].split(";")[0].split(", ")
        xs = [built.vertices[v].x for v in front]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(front), 4)
        self.assertIn(f"its ends {front[0]} (beside v0) and {front[-1]} (beside v1)", note)
        self.assertNotEqual(front[0], "B_0")

    def test_the_help_states_the_fan_rule_the_band_uses(self) -> None:
        from blockdrawer.commands import COMMANDS
        from blockdrawer.construct import FAN_ANGLE, FAN_SECTOR, MITRE_LIMIT

        for name in ("add_band", "add_ring"):
            text = COMMANDS[name].help
            self.assertIn(f"more than {math.degrees(FAN_ANGLE):g} degrees", text)
            self.assertIn(f"one per {math.degrees(FAN_SECTOR):g} degrees of turning", text)
        self.assertIn(f"at most {MITRE_LIMIT:g} heights", COMMANDS["add_band"].help)
        # A square corner gets one corner block, a 135-degree turn two.
        square = apply_commands(MeshModel(box=(0, 0, 1, 1)), ["add_band edges=v0-v1,v1-v2 height=0.25"]).model
        self.assertEqual(len(square.blocks), 4)

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

    def test_a_band_that_would_fold_at_a_concave_corner_is_refused_with_the_height_that_builds(self) -> None:
        # An L-shaped region whose reflex corner (1, 1) has a 0.1-long edge
        # beside it: a band grown into the notch mitres there and moves the
        # offset of that edge 0.15 along it at height 0.15, so its block
        # would fold. Sorting its corners round their centre used to turn
        # it into a valid-looking block with other edges, silently.
        model = apply_commands(MeshModel(box=(0, 0, 0.9, 1)), [
            "add_grid xs=0,0.9,1,2 ys=0,1,1.1,2 skip=1,1,2,2",
        ]).model
        corner = next(v.id for v in model.vertices.values() if (v.x, v.y) == (1.0, 1.0))
        short = next(v.id for v in model.vertices.values() if (v.x, v.y) == (1.0, 1.1))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_band bbox=0.85,0.95,2.05,2.05 height=0.15"])
        message = str(caught.exception)
        self.assertIn(f"concave corner {corner}", message)
        self.assertIn(f"edge {corner}-{short}", message)
        self.assertIn("largest height that builds is 0.0999", message)
        built = apply_commands(model, ["add_band bbox=0.85,0.95,2.05,2.05 height=0.0999 id=B"]).model
        # Below that height the band block keeps the wall edge as its own.
        wall_block = next(b for b in built.blocks if {corner, short} <= set(b.vertices) and b.id.startswith("B_"))
        self.assertIn(edge_key(corner, short), [edge_key(*wall_block.directed_edge(k)) for k in range(4)])

    def test_an_offset_spline_never_runs_back_behind_its_ends(self) -> None:
        # The wall edge leaving the reflex corner (1, 1) is a spline with
        # points 0.02 and 0.05 from the corner; the mitre there moves the
        # offset's start 0.1 along it, so those points offset along their
        # normals used to land behind the start and the offset spline hooked
        # back - the next band then failed ("overlap or are misoriented").
        model = apply_commands(MeshModel(box=(0, 0, 0.9, 1)), [
            "add_grid xs=0,0.9,1,2 ys=0,1,1.5,2 skip=1,1,2,2",
        ]).model
        ids = {(v.x, v.y): v.id for v in model.vertices.values()}
        wall = edge_key(ids[(1.0, 1.0)], ids[(2.0, 1.0)])
        xs = [1.02, 1.05, 1.1, 1.2, 1.4, 1.6, 1.8, 1.9]
        points = [(x, 1.0 - 0.02 * math.sin(math.pi * (x - 1.0))) for x in xs]
        if wall[0] != ids[(1.0, 1.0)]:
            points.reverse()
        model.edge_geometry[wall] = EdgeGeometry("spline", tuple(points))
        model.validate()
        banded = apply_commands(model, ["add_band bbox=0.85,0.95,2.05,2.05 height=0.1 id=B"]).model
        offsets = [k for k, g in banded.edge_geometry.items() if g.kind == "spline" and k != wall]
        self.assertEqual(len(offsets), 1)
        first, second = (banded.vertices[v] for v in offsets[0])
        chord = (second.x - first.x, second.y - first.y)
        path = [(first.x, first.y), *banded.edge_geometry[offsets[0]].points, (second.x, second.y)]
        along = [(p[0] - first.x) * chord[0] + (p[1] - first.y) * chord[1] for p in path]
        self.assertEqual(along, sorted(set(along)))
        self.assertGreaterEqual(len(path), 5)   # the hook goes, the curve stays
        apply_commands(banded, ["add_band bbox=0.8,0.9,2.2,2.2 height=0.05"]).model.validate()

    def test_monotone_points_drop_a_hook_and_a_swallowtail(self) -> None:
        from blockdrawer.construct import _monotone_points

        kept = _monotone_points(
            (0.0, 0.0),
            [(-0.1, 0.1), (0.2, 0.1), (0.4, 0.1), (0.35, 0.2), (0.6, 0.1), (1.05, 0.1)],
            (1.0, 0.0),
        )
        self.assertEqual(kept, ((0.2, 0.1), (0.4, 0.1), (0.6, 0.1)))

    def test_no_operator_spline_runs_backwards_and_walls_follow_the_curve(self) -> None:
        # The NACA example's ring: stations by arc length fall just past a
        # curve point, whose segment (down to a thousandth of the chord)
        # used to loop in 12 wall and front splines.
        import os

        from blockdrawer.model import spline_hooks

        folder = os.path.join(os.path.dirname(__file__), "..", ".claude", "skills", "mesh-blocking", "examples")
        here = os.getcwd()
        os.chdir(folder)
        try:
            model = apply_commands(MeshModel(box=(20, 20, 21, 21)), [
                "import_curve naca0012_a5.dat name=naca",
                "add_ring naca 0.05 count=40",
                "set_boundary_by airfoil type=wall curves=naca",
                "add_band bbox=-0.5,-0.5,1.5,0.5 height=0.5",
            ]).model
        finally:
            os.chdir(here)
        curve = next(iter(model.geometry_curves.values())).points
        splines = 0
        for key, geometry in model.edge_geometry.items():
            if geometry.kind != "spline":
                continue
            splines += 1
            first, second = model.vertices[key[0]], model.vertices[key[1]]
            self.assertEqual(spline_hooks([(first.x, first.y), *geometry.points, (second.x, second.y)]), [], key)
        self.assertEqual(splines, 120)
        # The wall splines still lie on the airfoil: within 1e-4 chords of
        # its polyline (6.02e-5 after the rule, 6.03e-5 before).
        worst = 0.0
        for key in model.edges():
            geometry = model.edge_geometry.get(key)
            if geometry is None or not all(abs(model.vertices[v].x) < 1.5 and abs(model.vertices[v].y) < 0.2
                                           for v in key):
                continue
            first = model.vertices[key[0]]
            if min(math.hypot(first.x - x, first.y - y) for x, y in curve) > 1.0e-9:
                continue
            for x, y in model.edge_points(key, [k / 50 for k in range(51)]):
                worst = max(worst, min(
                    _segment_gap((x, y), curve[j], curve[(j + 1) % len(curve)]) for j in range(len(curve))))
        self.assertLess(worst, 1.0e-4)

    def test_unhooked_points_drop_the_point_beside_the_long_neighbour_either_way_round(self) -> None:
        from blockdrawer.model import spline_hooks, unhooked_points

        wall = [(0.0, 0.0), (0.011883, -0.001113), (0.028296, -0.002506), (0.029717, -0.002608)]
        self.assertEqual(spline_hooks(wall), [2])
        kept = unhooked_points(wall[0], wall[1:-1], wall[-1])
        self.assertEqual(kept, (wall[1],))
        self.assertEqual(spline_hooks([wall[0], *kept, wall[-1]]), [])
        backwards = unhooked_points(wall[-1], list(reversed(wall[1:-1])), wall[0])
        self.assertEqual(backwards, tuple(reversed(kept)))
        # Nothing hooks: nothing goes.
        smooth = [(0.1, 0.01), (0.2, 0.015), (0.3, 0.01)]
        self.assertEqual(unhooked_points((0.0, 0.0), smooth, (0.4, 0.0)), tuple(smooth))

    def test_a_ring_round_a_square_curve(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), square_curve([]) + [
            "add_ring body 0.1 count=8",
            "set_boundary_by wall type=wall curves=body",
        ]).model
        walls = [e for e, name in model.edge_boundaries.items() if name == "wall"]
        self.assertEqual(len(walls), 8)
        # Eight edge blocks and one fan block at each of the four corners.
        self.assertEqual(len(model.blocks), 1 + 8 + 4)


def wall_stations(model, curve_points):
    """The ring's vertices lying on the curve."""
    from blockdrawer.construct import _distance_to

    return sorted(
        (round(v.x, 6), round(v.y, 6)) for v in model.vertices.values()
        if _distance_to((v.x, v.y), curve_points, True) < 1e-9
    )


class RingStationTests(unittest.TestCase):
    def ellipse(self, a, b, n):
        return [(a * math.cos(2 * math.pi * k / n), b * math.sin(2 * math.pi * k / n)) for k in range(n)]

    def build(self, points, ring):
        text = ";".join(f"{x},{y}" for x, y in points)
        batch = apply_commands(MeshModel(box=(20, 20, 21, 21)), [f"add_curve {text} name=c", ring])
        return batch.model, batch.results[1].result

    def test_tips_put_a_station_at_each_rounded_nose_and_none_on_a_circle(self) -> None:
        # A 10:1 ellipse with 21 stations by arc length from (1, 0): the
        # far nose (-1, 0) falls in the middle of an edge.
        points = self.ellipse(1.0, 0.1, 400)
        model, _ = self.build(points, "add_ring c 0.03 count=21")
        self.assertNotIn((-1.0, 0.0), wall_stations(model, points))
        model, result = self.build(points, "add_ring c 0.03 count=21 tips=true")
        stations = wall_stations(model, points)
        self.assertIn((-1.0, 0.0), [(round(x, 6), round(abs(y), 6)) for x, y in stations])
        self.assertIn("2 tip(s)", result["note"])
        circle = self.ellipse(1.0, 1.0, 64)
        model, result = self.build(circle, "add_ring c 0.1 count=8 tips=true")
        self.assertEqual(len(wall_stations(model, circle)), 8)
        self.assertIn("0 tip(s)", result["note"])

    def test_the_corner_threshold_is_an_option(self) -> None:
        # A regular octagon turns 45 degrees at each vertex.
        octagon = self.ellipse(1.0, 1.0, 8)
        model, _ = self.build(octagon, "add_ring c 0.1 count=4")
        self.assertEqual(len(wall_stations(model, octagon)), 8)
        model, _ = self.build(octagon, "add_ring c 0.1 count=4 corner=50")
        self.assertEqual(len(wall_stations(model, octagon)), 4)

    def test_stations_by_outward_normal(self) -> None:
        points = self.ellipse(1.0, 0.25, 300)
        model, result = self.build(points, "add_ring c 0.05 normals=90,180,270,0 count=4")
        stations = wall_stations(model, points)
        for expected in ((0.0, 0.25), (-1.0, 0.0), (0.0, -0.25), (1.0, 0.0)):
            self.assertTrue(any(math.dist(expected, s) < 1e-6 for s in stations), expected)
        self.assertIn("4 station(s) by normal", result["note"])
        # A normal held along a flat side, or met more than once, is refused.
        square = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        with self.assertRaises(Exception) as caught:
            self.build(square, "add_ring c 0.1 normals=90")
        self.assertIn("straight stretch", str(caught.exception))
        dented = [(math.cos(t) * (1.0 - 0.4 * math.exp(-((t - math.pi / 2) / 0.3) ** 2)),
                   math.sin(t) * (1.0 - 0.4 * math.exp(-((t - math.pi / 2) / 0.3) ** 2)))
                  for t in (2 * math.pi * k / 200 for k in range(200))]
        with self.assertRaises(Exception) as caught:
            self.build(dented, "add_ring c 0.02 normals=60")
        self.assertIn("3 places", str(caught.exception))

    def test_normal_stations_carry_a_band_to_the_box_corners(self) -> None:
        # A plate with a round nose: by arc length alone one band block
        # takes both left box corners and would fold; the refusal names
        # them. Stations whose normals point at the corners build.
        points = [(1.0 - k * 0.01, 0.02) for k in range(101)]
        points += [(0.02 * math.cos(math.pi / 2 + math.pi * k / 20), 0.02 * math.sin(math.pi / 2 + math.pi * k / 20))
                   for k in range(1, 20)]
        points += [(k * 0.01, -0.02) for k in range(101)]
        text = ";".join(f"{x},{y}" for x, y in points)
        start = [f"add_curve {text} name=plate"]
        rest = ["set_boundary_by wall type=wall curves=plate",
                "add_band bbox=-0.5,-0.5,1.5,0.5 to_box=-0.4,-0.3,1.4,0.3"]
        with self.assertRaises(Exception) as caught:
            apply_commands(MeshModel(box=(-3, -3, -2, -2)), start + ["add_ring plate 0.01 count=20"] + rest)
        self.assertIn("(a box corner)", str(caught.exception))
        model = apply_commands(MeshModel(box=(-3, -3, -2, -2)),
                               start + ["add_ring plate 0.01 count=20 normals=143,180,217"] + rest).model
        corners = {(round(v.x, 9), round(v.y, 9)) for v in model.vertices.values()}
        self.assertTrue({(-0.4, -0.3), (-0.4, 0.3), (1.4, -0.3), (1.4, 0.3)} <= corners)


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


    def test_a_curve_closed_implicitly_takes_its_closing_segment_only_round_a_body(self) -> None:
        # A plate with a round nose whose blunt base (x = 1) is the implied
        # closing segment: four times the point spacing, so the gap rule
        # alone left it open and the base edge unassigned after add_ring.
        points = [(1.0 - k * 0.01, 0.02) for k in range(101)]
        points += [(0.02 * math.cos(math.pi / 2 + math.pi * k / 20), 0.02 * math.sin(math.pi / 2 + math.pi * k / 20))
                   for k in range(1, 20)]
        points += [(k * 0.01, -0.02) for k in range(101)]
        text = ";".join(f"{x},{y}" for x, y in points)
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {text} name=plate", "add_ring plate 0.01 count=12",
            "set_boundary_by wall type=wall curves=plate",
        ]).model
        base = [e for e in model.edge_boundaries
                if all(abs(model.vertices[v].x - 1.0) < 1e-12 for v in e)]
        self.assertEqual(len(base), 1)
        self.assertEqual(len(model.edge_boundaries), 13)
        # An open U whose chord is the domain's own lid: the lid's block is
        # inside the closed curve, so the lid is not the U's.
        cavity = apply_commands(MeshModel(box=(0, 0, 1, 1)), [
            "add_curve 0,1;0,0;1,0;1,1 name=walls",
            "set_boundary_by walls type=wall curves=walls",
        ]).model
        self.assertEqual(len(cavity.edge_boundaries), 3)
        lid = edge_key(*(v.id for v in cavity.vertices.values() if v.y == 1.0))
        self.assertNotIn(lid, cavity.edge_boundaries)


def vertex_at(model, x, y):
    return min(model.vertices.values(), key=lambda v: math.hypot(v.x - x, v.y - y)).id


def bump(x):
    return 1.0 + 0.2 * math.sin(math.pi * x)


class FillTests(unittest.TestCase):
    def arcs(self):
        model = MeshModel(box=(5, 5, 6, 6))
        return apply_commands(model, [
            "add_grid center=0,0 rs=1,1.2 thetas=0,30,60,90 id=a",
            "add_grid center=0,0 rs=2,2.4 thetas=0,30,60,90 id=c",
        ]).model

    def test_a_fill_between_two_arcs_and_two_straight_sides(self) -> None:
        model = self.arcs()
        # A standalone station on the first straight side; the opposite one
        # gets a new station facing it.
        model = apply_commands(model, ["add_vertex 1.5 0 id=s"]).model
        corners = [vertex_at(model, *p) for p in ((1.2, 0), (2, 0), (0, 2), (0, 1.2))]
        batch = apply_commands(model, [f"add_fill {','.join(corners)} id=f"])
        data = batch.results[0].result
        filled = batch.model
        self.assertEqual(len(data["created_blocks"]), 6)
        self.assertIn("2 x 3 blocks", data["note"])
        self.assertIn("the exterior chain", data["note"])
        self.assertIn("through standalone stations s", data["note"])
        self.assertIn("new stations facing", data["note"])
        # The matched station mirrors s: fraction 0.375 from the inner arc.
        self.assertEqual(vertex_at(filled, 0, 1.5), "f_0")
        self.assertAlmostEqual(filled.vertices["f_0"].y, 1.5, places=12)
        # Two interior vertices, between the arcs, in the middle row.
        interior = data["created_vertices"][1:]
        self.assertEqual(len(interior), 2)
        for vertex in interior:
            radius = math.hypot(filled.vertices[vertex].x, filled.vertices[vertex].y)
            self.assertTrue(1.4 < radius < 1.6, radius)
        # Opposite edges share counts across the old and new blocks alike.
        filled.validate()
        self.assertEqual(filled.edge_cells[edge_key(corners[0], "s")],
                         filled.edge_cells[edge_key(corners[3], "f_0")])
        # Either corner order builds the same blocks.
        reverse = apply_commands(model, [f"add_fill {','.join(reversed(corners))} id=f"]).model
        def shapes(result):
            return sorted(sorted((round(result.vertices[v].x, 9), round(result.vertices[v].y, 9))
                                 for v in block.vertices) for block in result.blocks)
        self.assertEqual(shapes(reverse), shapes(filled))
        # Deterministic, and the quiet digest carries the note.
        again = apply_commands(model, [f"add_fill {','.join(corners)} id=f"])
        self.assertEqual(to_data(again.model), to_data(filled))
        note = batch_digest(model, batch)["notes"][0]
        self.assertEqual((note["op"], note["blocks"]), ("add_fill", 6))
        self.assertIn("2 x 3 blocks", note["note"])

    def front_and_curve(self, xs=(0.25, 0.5, 0.75)):
        model = MeshModel(box=(0, 0, 0.25, 0.2))
        curve = ";".join(f"{x / 20.0},{bump(x / 20.0)}" for x in range(-4, 25))
        commands = [
            "add_grid xs=0,0.25,0.5,0.75,1 ys=0,0.2",
            f"add_curve {curve} name=top",
        ]
        commands += [f"add_vertex {x} {bump(x)} id=t{k}" for k, x in enumerate((0.0, *xs, 1.0))]
        return apply_commands(model, commands).model

    def test_a_fill_from_a_front_to_standalone_stations_on_a_curve(self) -> None:
        model = self.front_and_curve()
        corners = f"{vertex_at(model, 0, 0.2)},{vertex_at(model, 1, 0.2)},t4,t0"
        batch = apply_commands(model, [f"add_fill {corners} curve3=top id=f"])
        filled = batch.model
        self.assertIn("4 x 1 blocks", batch.results[0].result["note"])
        self.assertIn("open, through standalone stations t3, t2, t1 along top", batch.results[0].result["note"])
        self.assertEqual(len(filled.blocks), 8)
        # The new top edges follow the curve: splines through its points.
        for k in range(4):
            geometry = filled.edge_geometry.get(edge_key(f"t{k}", f"t{k + 1}"))
            self.assertIsNotNone(geometry)
            self.assertEqual(geometry.kind, "spline")
            for x, y in geometry.points:
                self.assertAlmostEqual(y, bump(x), places=12)
        # The straight sides stay lines.
        self.assertNotIn(edge_key(vertex_at(model, 1, 0.2), "t4"), filled.edge_geometry)

    def test_opposite_sides_with_different_station_counts_are_refused_with_the_fix(self) -> None:
        model = self.front_and_curve(xs=(0.25, 0.75))
        corners = f"{vertex_at(model, 0, 0.2)},{vertex_at(model, 1, 0.2)},t3,t0"
        with self.assertRaises(Exception) as caught:
            apply_commands(model, [f"add_fill {corners} curve3=top"])
        text = str(caught.exception)
        self.assertIn("side1", text)
        self.assertIn("4 edge(s)", text)
        self.assertIn("side3", text)
        self.assertIn("3 edge(s)", text)
        # The missing station faces x = 0.5, half way from t2 to t1.
        self.assertIn("a station at 0.5 of the way from t2 to t1", text)
        # A chain short of stations is pointed at split_edge.
        model = apply_commands(self.front_and_curve(), [f"add_vertex 0.125 {bump(0.125)} id=extra"]).model
        sides = "t4,t3,t2,t1,extra,t0"
        with self.assertRaises(Exception) as caught:
            apply_commands(model, [f"add_fill {corners.replace('t3', 't4')} side3={sides}"])
        self.assertIn(f"split_edge {vertex_at(model, 0, 0.2)}-{vertex_at(model, 0.25, 0.2)} 0.",
                      str(caught.exception))

    def test_a_fill_refusal_names_what_rejected_each_reading(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            "add_vertex 0 0 id=a", "add_vertex 1 0 id=b", "add_vertex 1 1 id=c", "add_vertex 0 1 id=d",
            "add_vertex 0.5 0.5 id=e",
        ]).model
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_fill a,b,c,d"])
        text = str(caught.exception)
        self.assertIn("bound no region", text)
        self.assertIn("with side1 open, side2 open, side3 open, side4 open: existing vertex e (0.5, 0.5) "
                      "inside the region", text)

    def test_a_fill_note_says_when_a_block_folds_on_the_count_free_map(self) -> None:
        # The top side follows a curve dipping below the bottom: the block's
        # corners are convex, its interior folds.
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            "add_vertex 0 0 id=a", "add_vertex 1 0 id=b", "add_vertex 1 1 id=c", "add_vertex 0 1 id=d",
            "add_curve 1,1;0.7,0.2;0.5,-0.3;0.3,0.2;0,1 name=dip",
        ]).model
        note = apply_commands(model, ["add_fill a,b,c,d curve3=dip id=f"]).results[0].result["note"]
        self.assertRegex(note, r"NOTE: block\(s\) fold on the count-free map \(32 even fractions a side\): f_b0 \(\d+\)")
        plain = apply_commands(model, ["add_fill a,b,c,d id=f"]).results[0].result["note"]
        self.assertNotIn("fold", plain)

    def test_a_fill_whose_block_would_not_be_convex_is_refused_by_name(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            "add_vertex 0 0 id=a", "add_vertex 2 0 id=b", "add_vertex 1 0.5 id=c", "add_vertex 1 2 id=d",
        ]).model
        before = to_data(model)
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_fill a,b,c,d id=f"])
        text = str(caught.exception)
        self.assertIn("fill block f_b0", text)
        self.assertIn("would not be strictly convex at its corner c (1, 0.5)", text)
        self.assertEqual(to_data(model), before)

    def test_an_annulus_in_two_fills_takes_each_half_of_the_ring_front(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {circle_points(0.5, 64)} name=body",
            "add_ring body 0.1 count=8 id=r",
            f"add_curve {circle_points(2.0, 64)} name=outer",
        ]).model
        front = [v for v in model.vertices.values() if abs(math.hypot(v.x, v.y) - 0.6) < 0.01]
        self.assertEqual(len(front), 8)
        outer = []
        for k, vertex in enumerate(sorted(front, key=lambda v: math.atan2(v.y, v.x))):
            angle = math.atan2(vertex.y, vertex.x)
            model = apply_commands(model, [
                f"add_vertex {2 * math.cos(angle)} {2 * math.sin(angle)} id=o{k}"]).model
            outer.append((vertex.id, f"o{k}"))
        (f0, o0), (f4, o4) = outer[0], outer[4]
        # Both halves of the front join f0 and f4; the orientation decides.
        first = apply_commands(model, [f"add_fill {f0},{f4},{o4},{o0} curve3=outer"])
        self.assertIn("4 x 1 blocks", first.results[0].result["note"])
        second = apply_commands(first.model, [f"add_fill {f4},{f0},{o0},{o4} curve3=outer"])
        self.assertIn("side2", second.results[0].result["note"])
        self.assertEqual(len(second.model.blocks), len(model.blocks) + 8)
        second.model.validate()


class PolygonFillTests(unittest.TestCase):
    def polygon(self, corners, stations):
        """Standalone corners and evenly spaced standalone stations, the
        given number of edges per side."""
        commands = [f"add_vertex {x} {y} id=c{k}" for k, (x, y) in enumerate(corners)]
        for k, count in enumerate(stations):
            (x0, y0), (x1, y1) = corners[k], corners[(k + 1) % len(corners)]
            commands += [f"add_vertex {x0 + (x1 - x0) * j / count} {y0 + (y1 - y0) * j / count} id=s{k}_{j}"
                         for j in range(1, count)]
        return apply_commands(MeshModel(box=(5, 5, 6, 6)), commands).model

    def test_a_triangle_is_three_fills_round_a_centre(self) -> None:
        corners = [(0, 0), (2, 0), (1, 1.6)]
        model = self.polygon(corners, (4, 4, 4))
        batch = apply_commands(model, ["add_fill c0,c1,c2 id=t"])
        data = batch.results[0].result
        self.assertIn("3 fills round a centre (2, 2, 2 edges on its spokes)", data["note"])
        self.assertEqual(len(data["created_blocks"]), 12)
        centre = batch.model.vertices["t_0"]
        self.assertAlmostEqual(centre.x, 1.0, places=12)
        # The mean of the three side midpoints, where the spokes end.
        self.assertAlmostEqual(centre.y, 1.6 / 3.0, places=12)
        batch.model.validate()
        # Uneven sides, read from c2: 3, 4, 3 edges; spoke k has
        # (s_(k-1) + s_(k+1) - s_k) / 2 edges: 2, 1, 2.
        uneven = apply_commands(self.polygon(corners, (4, 3, 3)), ["add_fill c2,c0,c1"])
        self.assertIn("(2, 1, 2 edges on its spokes)", uneven.results[0].result["note"])
        self.assertEqual(len(uneven.model.blocks), len(model.blocks) + 2 * 1 + 1 * 2 + 2 * 2)
        uneven.model.validate()
        # Clockwise corners build the same blocks.
        reverse = apply_commands(model, ["add_fill c2,c1,c0 id=t"]).model
        self.assertEqual(len(reverse.blocks), len(batch.model.blocks))

    def test_a_triangle_with_an_odd_edge_total_is_refused_with_the_condition(self) -> None:
        model = self.polygon([(0, 0), (2, 0), (1, 1.6)], (3, 2, 2))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_fill c0,c1,c2"])
        text = str(caught.exception)
        self.assertIn("side edge counts (3, 2, 2)", text)
        self.assertIn("sum to an even number and each side shorter than the other two", text)
        model = self.polygon([(0, 0), (2, 0), (1, 1.6)], (6, 2, 2))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_fill c0,c1,c2"])
        self.assertIn("σ = (-1, 3, 3)", str(caught.exception))

    def test_a_pentagon_is_five_fills_round_a_centre(self) -> None:
        corners = [(math.cos(2 * math.pi * k / 5 + 0.3), math.sin(2 * math.pi * k / 5 + 0.3)) for k in range(5)]
        model = self.polygon(corners, (2, 2, 2, 2, 2))
        batch = apply_commands(model, ["add_fill c0,c1,c2,c3,c4"])
        self.assertEqual(len(batch.results[0].result["created_blocks"]), 5)
        self.assertIn("1, 1, 1, 1, 1 edges on its spokes", batch.results[0].result["note"])
        batch.model.validate()
        # An odd edge total has no whole spoke counts.
        with self.assertRaises(Exception) as caught:
            apply_commands(self.polygon(corners, (3, 2, 2, 2, 2)), ["add_fill c0,c1,c2,c3,c4"])
        self.assertIn("a pentagon is filled as 5 blocks round a centre", str(caught.exception))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_fill c0,c1,c2,c3 side5=c4,c0"])
        self.assertIn("side5", str(caught.exception))


class StationTests(unittest.TestCase):
    def wavy(self, offset=0.0, reverse=False, steps=20):
        points = [(2.0 * k / steps, offset - 0.5 + 0.1 * math.sin(2.0 * math.pi * k / steps))
                  for k in range(steps + 1)]
        if reverse:
            points.reverse()
        return ";".join(f"{x},{y}" for x, y in points)

    def test_stations_by_count_fraction_and_crossing_are_ordered_and_named(self) -> None:
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {self.wavy()} name=lower",
            "add_stations lower count=5 id=c",
            "add_stations lower fractions=0.5,0.1 xs=1.55 id=f",
            f"add_stations lower count=3 between=0.2,{-0.5 + 0.1 * math.sin(0.2 * math.pi)},1.8,{-0.5 + 0.1 * math.sin(1.8 * math.pi)} id=b",
        ])
        c = model.results[1].result
        self.assertEqual(c["stations"], ["c_0", "c_1", "c_2", "c_3", "c_4"])
        self.assertEqual(c["created_vertices"], c["stations"])
        vertices = model.model.vertices
        self.assertAlmostEqual(vertices["c_0"].x, 0.0, places=12)
        self.assertAlmostEqual(vertices["c_4"].x, 2.0, places=12)
        # Ordered along the curve, whatever order they were asked in; the
        # fraction 0.5 is the middle station already there, reused.
        f = model.results[2].result
        self.assertEqual(f["stations"][1], "c_2")
        self.assertEqual(f["created_vertices"], ["f_0", "f_2"])
        self.assertAlmostEqual(vertices["f_2"].x, 1.55, places=12)
        # Every station is on the polyline through the curve's points.
        for name in ("f_0", "f_2", "b_0", "b_2"):
            x, y = vertices[name].x, vertices[name].y
            k = min(int(x * 10.0), 19)
            a = (k / 10.0, -0.5 + 0.1 * math.sin(math.pi * k / 10.0))
            b = ((k + 1) / 10.0, -0.5 + 0.1 * math.sin(math.pi * (k + 1) / 10.0))
            self.assertAlmostEqual(y, a[1] + (x - a[0]) / (b[0] - a[0]) * (b[1] - a[1]), places=12)
        # between by two points, each taken to its nearest curve point; the
        # middle station is the curve's middle, c_2 again.
        self.assertEqual(model.results[3].result["stations"], ["b_0", "c_2", "b_2"])
        self.assertLess(vertices["b_0"].x, vertices["b_2"].x)
        self.assertAlmostEqual(vertices["b_0"].x, 0.2, places=9)
        self.assertAlmostEqual(vertices["b_2"].x, 1.8, places=9)

    def test_a_crossing_must_be_unique(self) -> None:
        model = MeshModel(box=(5, 5, 6, 6))
        with self.assertRaises(Exception) as caught:
            apply_commands(model, [f"add_curve {circle_points(1.0, 32)} name=disc",
                                   "add_stations disc ys=0.5"])
        self.assertIn("crosses y = 0.5 2 times", str(caught.exception))
        # A section with one crossing is enough: forward from 270 degrees to 90.
        batch = apply_commands(model, [f"add_curve {circle_points(1.0, 32)} name=disc",
                                       "add_stations disc ys=0.5 between=0.75,0.25 id=s"])
        self.assertGreater(batch.model.vertices["s_0"].x, 0.0)

    def test_matched_stations_on_a_periodic_pair_fill_and_export_as_cyclic(self) -> None:
        from blockdrawer.foam import block_mesh_dict

        # Dense curves: each edge keeps a spread of MAX_SPLINE_POINTS of their
        # points, the same ones whichever way the fill cuts the edge.
        commands = [
            f"add_curve {self.wavy(steps=400)} name=lower",
            f"add_curve {self.wavy(1.0, reverse=True, steps=400)} name=upper",
            "add_stations lower xs=0,0.3,0.7,1.2,2 id=l",
            "add_stations lower xs=0,0.3,0.7,1.2,2 offset=0,1 id=u",
            "add_fill l_0,l_4,u_4,u_0 curve1=lower curve3=upper",
            "remove_edge v0-v1",
            "set_boundary_by lower curves=lower",
            "set_boundary_by upper curves=upper",
            "set_boundary_type lower cyclic neighbour_patch=upper",
            "set_boundary_by inlet bbox=-0.01,-1,0.01,1",
            "set_boundary_by outlet bbox=1.99,-1,2.01,1",
            "set_edge_cells l_0-l_1 40",
            "set_edge_grading l_0-l_1 total_ratio 4",
            "set_edge_grading u_0-u_1 total_ratio 4",
        ]
        first = apply_commands(MeshModel(box=(5, 5, 6, 6)), commands)
        model = first.model
        self.assertIn("4 x 1 blocks", first.results[4].result["note"])
        for k in range(5):
            lower, upper = model.vertices[f"l_{k}"], model.vertices[f"u_{k}"]
            self.assertAlmostEqual(upper.x, lower.x, places=12)
            self.assertAlmostEqual(upper.y, lower.y + 1.0, places=12)
        for k in range(4):
            lower = model.edge_geometry[edge_key(f"l_{k}", f"l_{k + 1}")]
            upper = model.edge_geometry[edge_key(f"u_{k}", f"u_{k + 1}")]
            self.assertEqual(lower.kind, "spline")
            self.assertEqual(len(lower.points), len(upper.points))
            for a, b in zip(lower.points, upper.points):
                self.assertAlmostEqual(b[0], a[0], places=9)
                self.assertAlmostEqual(b[1], a[1] + 1.0, places=9)
        self.assertEqual(len(model.edge_boundaries), 10)
        text = block_mesh_dict(model)
        self.assertIn("neighbourPatch upper;", text)
        second = apply_commands(MeshModel(box=(5, 5, 6, 6)), commands).model
        self.assertEqual(to_data(second), to_data(model))

    def test_stations_refuse_a_used_name_and_report_a_reused_vertex(self) -> None:
        model = apply_commands(MeshModel(box=(0, -0.6, 0.5, -0.5)), [f"add_curve {self.wavy()} name=lower"]).model
        # The curve starts on the block's corner v3: station 0 is v3, so
        # s_0 is not made and station 1 is still s_1.
        batch = apply_commands(model, ["add_stations lower count=3 id=s"])
        self.assertEqual(batch.results[0].result["stations"], ["v3", "s_1", "s_2"])
        self.assertEqual(batch.results[0].result["reused_vertices"], 1)
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_vertex 9 9 id=s_1", "add_stations lower count=3 id=s"])
        self.assertIn("Vertex ID s_1 is already used", str(caught.exception))


class BlockFromPointsTests(unittest.TestCase):
    def test_a_block_from_coordinates_needs_no_vertex_ids(self) -> None:
        model = apply_commands(MeshModel(box=(-4, 0, 0, 1)), [
            "add_grid xs=-4,0,0.75,6 ys=0,1",
            "add_vertex -1.0 2.5 id=bl",
            "add_vertex -0.25 2.75 id=br",
        ]).model
        batch = apply_commands(model, ["add_block_from_vertices bl,br points=0,1;0.75,1"])
        block = batch.results[0].result["block"]
        corners = {vertex for b in batch.model.blocks if b.id == block for vertex in b.vertices}
        self.assertEqual(corners, {"bl", "br", vertex_at(model, 0, 1), vertex_at(model, 0.75, 1)})
        # Points alone, and a typo refused with the nearest vertex.
        apply_commands(model, ["add_block_from_vertices points=0,1;0.75,1;-0.25,2.75;-1,2.5"])
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_block_from_vertices bl,br points=0,1;0.7501,1"])
        text = str(caught.exception)
        self.assertIn("no vertex at (0.7501, 1)", text)
        self.assertIn(f"the nearest is {vertex_at(model, 0.75, 1)} at (0.75, 1)", text)
        with self.assertRaises(Exception) as caught:
            apply_commands(model, ["add_block_from_vertices bl,br points=0,1"])
        self.assertIn("four vertices", str(caught.exception))


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
