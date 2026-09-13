"""Pillow rendering of the topology analysis.

The picture keeps the component-coloured generalized Voronoi raster and draws
the finished layout on top: block outlines, traced branches, junctions and
block vertices.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from PIL import Image, ImageDraw, ImageFont


REGION_COLOURS = (
    (226, 240, 252),
    (237, 231, 250),
    (252, 238, 218),
    (226, 244, 229),
    (250, 226, 235),
    (238, 238, 238),
)
BRANCH_COLOUR = (0, 114, 178)
BLOCK_FILLS = (
    (214, 234, 248),
    (250, 229, 211),
    (215, 242, 224),
    (240, 219, 238),
    (247, 243, 208),
    (225, 225, 245),
)


class Canvas:
    """A world-to-pixel drawing surface with an optional legend column."""

    def __init__(self, bounds, width: int, legend: int = 340):
        xmin, ymin, xmax, ymax = bounds
        self.bounds = (float(xmin), float(ymin), float(xmax), float(ymax))
        span_x = xmax - xmin
        span_y = ymax - ymin
        self.width = int(width)
        self.height = max(16, int(round(width * span_y / span_x)))
        self.legend = int(legend)
        self.image = Image.new(
            "RGB", (self.width + self.legend, self.height), (255, 255, 255)
        )
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default()

    def paste_field(self, pixels: np.ndarray) -> None:
        field = Image.fromarray(pixels, mode="RGB")
        if field.size != (self.width, self.height):
            field = field.resize((self.width, self.height), Image.NEAREST)
        self.image.paste(field, (0, 0))

    def to_pixel(self, point) -> tuple[float, float]:
        xmin, ymin, xmax, ymax = self.bounds
        x = (float(point[0]) - xmin) / (xmax - xmin) * (self.width - 1)
        y = (ymax - float(point[1])) / (ymax - ymin) * (self.height - 1)
        return x, y

    def polyline(self, points, colour, width: int = 1) -> None:
        pixels = [self.to_pixel(point) for point in points]
        if len(pixels) >= 2:
            self.draw.line(pixels, fill=colour, width=width, joint="curve")

    def polygon(self, points, fill, outline=None, width: int = 1) -> None:
        pixels = [self.to_pixel(point) for point in points]
        if len(pixels) >= 3:
            self.draw.polygon(pixels, fill=fill, outline=outline, width=width)

    def marker(self, point, colour, radius: int = 4, outline=(20, 20, 20)) -> None:
        x, y = self.to_pixel(point)
        self.draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=colour,
            outline=outline,
        )

    def cross(self, point, colour, radius: int = 7, width: int = 3) -> None:
        x, y = self.to_pixel(point)
        self.draw.line((x - radius, y - radius, x + radius, y + radius), colour, width)
        self.draw.line((x - radius, y + radius, x + radius, y - radius), colour, width)

    def text(self, point, message, colour=(20, 20, 20)) -> None:
        x, y = self.to_pixel(point)
        self.draw.text((x + 4, y - 6), message, fill=colour, font=self.font)

    def save(self, path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(destination)
        return destination


def region_field(diagram) -> np.ndarray:
    raster = diagram.raster
    pixels = np.full((raster.height, raster.width, 3), 255, dtype=np.uint8)
    for index in range(len(diagram.sites)):
        colour = REGION_COLOURS[index % len(REGION_COLOURS)]
        pixels[(raster.labels == index) & raster.free] = colour
    pixels[~raster.free] = (60, 64, 68)
    return pixels


def draw_legend(canvas: Canvas, title: str, rows) -> None:
    x = canvas.width + 18
    y = 18
    canvas.draw.text((x, y), title, fill=(0, 0, 0), font=canvas.font)
    y += 24
    for swatch, label in rows:
        if swatch is not None:
            canvas.draw.rectangle(
                (x, y, x + 14, y + 14), fill=swatch, outline=(80, 80, 80)
            )
            canvas.draw.text(
                (x + 22, y + 2), label, fill=(30, 30, 30), font=canvas.font
            )
        else:
            canvas.draw.text((x, y + 2), label, fill=(30, 30, 30), font=canvas.font)
        y += 18
        if y > canvas.height - 20:
            break
