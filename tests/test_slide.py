import math
import unittest

from blockdrawer.commands import apply_commands, batch_digest
from blockdrawer.domain import TopologyError
from blockdrawer.history import ModelHistory
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import from_data, to_data
from blockdrawer.slide import SLIDE_TOLERANCE, slide_vertex
from blockdrawer.smooth import SmoothOptions, smooth_model


def channel() -> MeshModel:
    """A 3 x 1 channel in two columns, the bottom wall cut at x = 1."""
    model = apply_commands(MeshModel(box=(0, 0, 3, 1)), [
        "split_edge v0-v1 0.3333333333",
        "add_boundary floor", "set_boundary_type floor wall",
        "set_edge_boundary v0-v4 floor", "set_edge_boundary v1-v4 floor",
    ]).model
    return model


def banded(offset: float) -> MeshModel:
    """A wall band of two blocks on y = 0 with a core above; the wall vertex
    of the middle spoke sits ``offset`` to the right of its front vertex, so
    the band blocks are sheared."""
    data = to_data(MeshModel())
    points = {"a": (0, 0), "w": (1.5 + offset, 0), "b": (3, 0),
              "fa": (0, 0.2), "f": (1.5, 0.2), "fb": (3, 0.2),
              "ta": (0, 1), "t": (1.5, 1), "tb": (3, 1)}
    data["vertices"] = [{"id": k, "x": x, "y": y} for k, (x, y) in points.items()]
    quads = [("a", "w", "f", "fa"), ("w", "b", "fb", "f"), ("fa", "f", "t", "ta"), ("f", "fb", "tb", "t")]
    data["blocks"] = [{"id": f"b{i}", "vertices": list(q)} for i, q in enumerate(quads)]
    edges = set()
    for q in quads:
        for k in range(4):
            edges.add(tuple(sorted((q[k], q[(k + 1) % 4]))))
    data["edgeCells"] = [{"vertices": list(e), "cells": 8} for e in sorted(edges)]
    return apply_commands(from_data(data), [
        "add_boundary floor", "set_boundary_type floor wall",
        "set_edge_boundary a-w floor", "set_edge_boundary b-w floor",
    ]).model


class SlideVertexTests(unittest.TestCase):
    def test_a_line_stays_a_line(self) -> None:
        model = channel()
        result = slide_vertex(model, "v4", fraction=0.5, towards="v1")
        self.assertAlmostEqual(model.vertices["v4"].x, 1.5)
        self.assertEqual(model.vertices["v4"].y, 0.0)
        self.assertNotIn(edge_key("v0", "v4"), model.edge_geometry)
        self.assertLess(result["deviation"], 1e-12)

    def test_an_arc_stays_on_its_circle(self) -> None:
        model = apply_commands(MeshModel(box=(-3, -3, -2, -2)), [
            "add_grid center=0,0 rs=1,2 thetas=0,45,90",
        ]).model
        inner = next(v for v in model.vertices.values() if abs(math.hypot(v.x, v.y) - 1) < 1e-9
                     and abs(v.x - v.y) < 1e-9)
        slide_vertex(model, inner.id, fraction=0.3)
        self.assertAlmostEqual(math.hypot(model.vertices[inner.id].x, model.vertices[inner.id].y), 1.0)
        for current, geometry in model.edge_geometry.items():
            if inner.id in current and geometry.kind == "arc":
                (x, y), = geometry.points
                self.assertAlmostEqual(math.hypot(x, y), 1.0)

    def test_a_spline_chain_is_recut_within_the_bound(self) -> None:
        points = ";".join(f"{math.cos(2 * math.pi * k / 48)},{0.6 * math.sin(2 * math.pi * k / 48)}"
                          for k in range(48))
        model = apply_commands(MeshModel(box=(5, 5, 6, 6)), [
            f"add_curve {points} name=body", "add_ring body 0.1 count=12",
        ]).model
        wall = [v for v in model.vertices.values() if abs((v.x) ** 2 + (v.y / 0.6) ** 2 - 1) < 1e-6]
        target = sorted(wall, key=lambda v: v.id)[3]
        result = slide_vertex(model, target.id, distance=0.05)
        self.assertLessEqual(result["deviation"], SLIDE_TOLERANCE * 2 * math.pi)

    def test_corners_and_patch_junctions_do_not_slide(self) -> None:
        model = apply_commands(channel(), [
            "set_edge_boundary v1-v2 floor", "set_edge_boundary v2-v5 floor",
            "set_edge_boundary v3-v5 floor", "set_edge_boundary v0-v3 floor",
        ]).model
        with self.assertRaises(TopologyError) as corner:
            slide_vertex(model, "v1", fraction=0.5)
        self.assertIn("corner", str(corner.exception))
        junction = apply_commands(channel(), ["add_boundary other", "set_edge_boundary v1-v4 other"]).model
        with self.assertRaises(TopologyError) as meet:
            slide_vertex(junction, "v4", fraction=0.5)
        self.assertIn("patches meet", str(meet.exception))

    def test_one_history_entry_and_a_note(self) -> None:
        model = channel()
        history = ModelHistory(model)
        before = to_data(model)
        batch = apply_commands(model, ["slide_vertex v4 fraction=0.6 towards=v1"])
        history.record(batch.model)
        self.assertEqual(to_data(history.undo()), before)
        (note,) = batch_digest(model, batch)["notes"]
        self.assertEqual(note["op"], "slide_vertex")


class SmoothSlideTests(unittest.TestCase):
    def test_a_sheared_band_slides_its_wall_vertex_and_carries_the_spoke(self) -> None:
        model = banded(0.4)
        corners = {v: (model.vertices[v].x, model.vertices[v].y) for v in ("a", "b", "ta", "tb")}
        length = math.hypot(0.4, 0.2)
        result = smooth_model(model, options=SmoothOptions(slide=True))
        self.assertTrue(result.applied, result.reason)
        self.assertGreaterEqual(result.slid, 1)
        self.assertEqual(model.vertices["w"].y, 0.0)
        self.assertLess(model.vertices["w"].x, 1.9)
        for v, point in corners.items():
            self.assertEqual((model.vertices[v].x, model.vertices[v].y), point)
        spoke = math.hypot(model.vertices["f"].x - model.vertices["w"].x,
                           model.vertices["f"].y - model.vertices["w"].y)
        self.assertLessEqual(abs(spoke / length - 1.0), 0.05 + 1e-9)
        self.assertLess(result.boundary_deviation, 1e-12)
        without = banded(0.4)
        smooth_model(without)
        self.assertEqual(without.vertices["w"].x, 1.9)

    def test_deterministic(self) -> None:
        first, second = banded(0.4), banded(0.4)
        smooth_model(first, options=SmoothOptions(slide=True))
        smooth_model(second, options=SmoothOptions(slide=True))
        self.assertEqual(to_data(first), to_data(second))

    def test_held_vertices_do_not_slide(self) -> None:
        model = banded(0.4)
        smooth_model(model, hold=("w",), options=SmoothOptions(slide=True))
        self.assertEqual(model.vertices["w"].x, 1.9)

    def test_a_hold_box_stops_a_boundary_vertex_sliding(self) -> None:
        model = banded(0.4)
        result = smooth_model(model, hold_box=(1.8, -0.1, 2.0, 0.1), options=SmoothOptions(slide=True))
        self.assertEqual(model.vertices["w"].x, 1.9)
        # w is no free vertex: the box is said to hold none.
        self.assertIn("holds no free vertex", result.reason)

    def test_a_sliding_wall_vertex_does_not_carry_a_line_held_front_off_its_line(self) -> None:
        model = banded(0.4)
        result = smooth_model(model, hold_line=((0.0, 0.2), (3.0, 0.2)), hold_along="line",
                              options=SmoothOptions(slide=True))
        self.assertEqual(result.line_vertices, 1)
        self.assertEqual(model.vertices["f"].y, 0.2)
        spoke = math.hypot(model.vertices["f"].x - model.vertices["w"].x,
                           model.vertices["f"].y - model.vertices["w"].y)
        self.assertLessEqual(abs(spoke / math.hypot(0.4, 0.2) - 1.0), 0.05 + 1e-9)


if __name__ == "__main__":
    unittest.main()
