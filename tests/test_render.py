from pathlib import Path
import tempfile
import unittest
import xml.dom.minidom

from blockdrawer.commands import apply_commands
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.render import (
    RenderError,
    RenderOptions,
    content_bounds,
    entity_bounds,
    options_with_highlights,
    render_svg,
    render_to_file,
    resolve_entity,
)

try:  # pragma: no cover - depends on the environment
    import PIL  # noqa: F401
    HAVE_PILLOW = True
except ImportError:  # pragma: no cover
    HAVE_PILLOW = False


def _sample_model() -> MeshModel:
    return apply_commands(MeshModel(), [
        "add_block v1-v2",
        "set_edge_type v0-v1 arc",
        "add_boundary inlet",
        "set_edge_boundary v0-v3 inlet",
        "add_curve '0,-0.5;0.5,-0.6;1,-0.5' name=bottom",
        "add_spacing_link v0-v1 v1-v2",
        "add_vertex 3 3",
    ]).model


class BoundsTests(unittest.TestCase):
    def test_content_bounds_include_control_points_and_curves(self) -> None:
        model = _sample_model()
        x_min, y_min, x_max, y_max = content_bounds(model)
        self.assertEqual((x_min, x_max), (0.0, 3.0))
        self.assertLess(y_min, -0.5)
        self.assertEqual(y_max, 3.0)
        _, y_min_no_curves, _, _ = content_bounds(model, include_curves=False)
        self.assertGreater(y_min_no_curves, -0.5)

    def test_degenerate_bounds_are_expanded(self) -> None:
        model = MeshModel()
        bounds = entity_bounds(model, ["v0"], margin=0.0)
        self.assertLess(bounds[0], bounds[2])
        self.assertLess(bounds[1], bounds[3])

    def test_entity_bounds_and_resolution(self) -> None:
        model = _sample_model()
        self.assertEqual(resolve_entity(model, "b1"), ("block", "b1"))
        self.assertEqual(resolve_entity(model, "v2"), ("vertex", "v2"))
        self.assertEqual(resolve_entity(model, "v2-v1"), ("edge", edge_key("v1", "v2")))
        with self.assertRaisesRegex(RenderError, "Unknown entity"):
            resolve_entity(model, "nothing")
        bounds = entity_bounds(model, ["b1"], margin=0.0)
        self.assertEqual(bounds, (1.0, 0.0, 2.0, 1.0))
        padded = entity_bounds(model, ["b1"], margin=0.5)
        self.assertEqual(padded, (0.5, -0.5, 2.5, 1.5))

    def test_options_with_highlights_classifies_entities(self) -> None:
        model = _sample_model()
        options = options_with_highlights(
            model, RenderOptions(), ["b0", "v3", "v1-v0"]
        )
        self.assertEqual(options.highlight_blocks, ("b0",))
        self.assertEqual(options.highlight_vertices, ("v3",))
        self.assertEqual(options.highlight_edges, (edge_key("v0", "v1"),))


class SvgRenderTests(unittest.TestCase):
    def test_svg_is_well_formed_and_labelled(self) -> None:
        model = _sample_model()
        svg = render_svg(model, RenderOptions(title="Sample", show_preview=True))
        xml.dom.minidom.parseString(svg)
        self.assertIn('width="1200"', svg)
        for label in ("b0", "b1", "v0", "v6", "inlet (patch)", "g0 bottom", "Sample"):
            self.assertIn(f">{label}<", svg, label)
        self.assertIn("#d9485f", svg)  # inlet color on its edge and legend
        self.assertIn("stroke-dasharray", svg)  # reference curve

    def test_flags_hide_layers(self) -> None:
        model = _sample_model()
        svg = render_svg(model, RenderOptions(
            show_grid=False,
            show_vertex_ids=False,
            show_block_ids=False,
            show_edge_cells=False,
            show_edge_nodes=False,
            show_control_points=False,
            show_curves=False,
            show_legend=False,
            show_spacing_links=False,
        ))
        xml.dom.minidom.parseString(svg)
        self.assertNotIn(">b0<", svg)
        self.assertNotIn(">v0<", svg)
        self.assertNotIn("stroke-dasharray", svg)
        self.assertNotIn("<text", svg)

    def test_bounds_cull_far_entities(self) -> None:
        model = _sample_model()
        svg = render_svg(model, RenderOptions(bounds=(0.9, -0.1, 1.1, 0.1)))
        self.assertIn(">v1<", svg)
        self.assertNotIn(">v6<", svg)
        with self.assertRaisesRegex(RenderError, "positive width"):
            render_svg(model, RenderOptions(bounds=(1.0, 0.0, 1.0, 1.0)))

    def test_highlights_use_highlight_color(self) -> None:
        model = _sample_model()
        options = options_with_highlights(model, RenderOptions(), ["b1", "v0"])
        svg = render_svg(model, options)
        self.assertIn('<polygon points=', svg)
        self.assertIn("#e8590c", svg)

    def test_many_control_points_render_without_labels(self) -> None:
        model = apply_commands(MeshModel(), [
            "set_edge_type v0-v1 spline",
            "set_control_point_count v0-v1 300",
        ]).model
        svg = render_svg(model)
        xml.dom.minidom.parseString(svg)
        self.assertNotIn(">150<", svg)


class FileRenderTests(unittest.TestCase):
    def test_render_to_file_dispatches_on_suffix(self) -> None:
        model = _sample_model()
        with tempfile.TemporaryDirectory() as directory:
            svg_path = render_to_file(model, Path(directory) / "out.svg")
            self.assertTrue(svg_path.read_text(encoding="utf-8").startswith("<?xml"))
            with self.assertRaisesRegex(RenderError, "Unsupported output format"):
                render_to_file(model, Path(directory) / "out.pdf")

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is not installed")
    def test_png_output(self) -> None:
        from PIL import Image

        model = _sample_model()
        options = options_with_highlights(
            model,
            RenderOptions(width=400, height=300, show_preview=True, title="PNG"),
            ["b1", "v0-v3"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = render_to_file(model, Path(directory) / "out.png", options)
            with Image.open(path) as image:
                self.assertEqual(image.size, (400, 300))
                colors = image.convert("RGB").getcolors(maxcolors=1_000_000)
        self.assertGreater(len(colors), 20)


if __name__ == "__main__":
    unittest.main()
