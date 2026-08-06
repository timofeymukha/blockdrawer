import struct
import unittest

from blockdrawer.node_raster import rasterize_node_markers


class NodeMarkerRasterTests(unittest.TestCase):
    def test_every_visible_exact_node_contributes_to_the_raster(self) -> None:
        raster = rasterize_node_markers(
            ((10.0, 10.0), (15.0, 10.0), (20.0, 10.0)),
            canvas_width=40,
            canvas_height=30,
            color="#334e68",
            radius=1,
        )

        self.assertIsNotNone(raster)
        assert raster is not None
        for screen_x in (10, 15, 20):
            x = screen_x - raster.left
            y = 10 - raster.top
            self.assertGreater(raster.levels[y * raster.width + x], 0)

    def test_coincident_nodes_accumulate_opacity(self) -> None:
        single = rasterize_node_markers(
            ((10.0, 10.0),),
            canvas_width=30,
            canvas_height=30,
            color="#334e68",
            radius=2,
        )
        repeated = rasterize_node_markers(
            ((10.0, 10.0), (10.0, 10.0)),
            canvas_width=30,
            canvas_height=30,
            color="#334e68",
            radius=2,
        )

        assert single is not None and repeated is not None
        single_offset = (10 - single.top) * single.width + 10 - single.left
        repeated_offset = (
            (10 - repeated.top) * repeated.width + 10 - repeated.left
        )
        self.assertGreater(
            repeated.levels[repeated_offset],
            single.levels[single_offset],
        )

    def test_png_encoding_preserves_the_cropped_dimensions(self) -> None:
        raster = rasterize_node_markers(
            ((10.0, 10.0), (20.0, 12.0)),
            canvas_width=40,
            canvas_height=30,
            color="#334e68",
            radius=2,
        )

        assert raster is not None
        png = raster.png_bytes()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            struct.unpack(">II", png[16:24]),
            (raster.width, raster.height),
        )
        self.assertEqual(png[25], 3)

    def test_completely_offscreen_nodes_produce_no_image(self) -> None:
        self.assertIsNone(rasterize_node_markers(
            ((-100.0, -100.0),),
            canvas_width=40,
            canvas_height=30,
            color="#334e68",
            radius=2,
        ))


if __name__ == "__main__":
    unittest.main()
