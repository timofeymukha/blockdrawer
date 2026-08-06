"""Pure screen-space rasterization for dense exact edge-node markers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import struct
import zlib
from typing import Iterable


ScreenPoint = tuple[float, float]
_DENSITY_ALPHAS = (0, 180, 233, 249, 254, 255)


@dataclass(frozen=True, slots=True)
class NodeMarkerRaster:
    """One cropped transparent indexed image positioned on the canvas."""

    left: int
    top: int
    width: int
    height: int
    color: tuple[int, int, int]
    levels: bytes

    def png_bytes(self) -> bytes:
        """Encode the raster as a minimal transparent indexed PNG for Tk."""
        stride = self.width
        scanlines = bytearray()
        for row in range(self.height):
            scanlines.append(0)
            start = row * stride
            scanlines.extend(self.levels[start:start + stride])
        header = struct.pack(
            ">IIBBBBB",
            self.width,
            self.height,
            8,
            3,
            0,
            0,
            0,
        )
        return b"".join((
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"PLTE", bytes(self.color) * len(_DENSITY_ALPHAS)),
            _png_chunk(b"tRNS", bytes(_DENSITY_ALPHAS)),
            _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=1)),
            _png_chunk(b"IEND", b""),
        ))


def rasterize_node_markers(
    points: Iterable[ScreenPoint],
    *,
    canvas_width: int,
    canvas_height: int,
    color: str,
    radius: int,
) -> NodeMarkerRaster | None:
    """Rasterize every visible node, darkening coincident pixel bins."""
    if canvas_width < 1 or canvas_height < 1:
        return None
    radius = max(1, int(radius))
    rgb = _hex_color(color)
    counts: Counter[tuple[int, int]] = Counter()
    for x, y in points:
        pixel_x = round(x)
        pixel_y = round(y)
        if not -radius <= pixel_x < canvas_width + radius \
                or not -radius <= pixel_y < canvas_height + radius:
            continue
        counts[(pixel_x, pixel_y)] += 1
    if not counts:
        return None

    left = max(0, min(x for x, _y in counts) - radius)
    top = max(0, min(y for _x, y in counts) - radius)
    right = min(canvas_width - 1, max(x for x, _y in counts) + radius)
    bottom = min(canvas_height - 1, max(y for _x, y in counts) + radius)
    width = right - left + 1
    height = bottom - top + 1
    level_map = bytearray(width * height)
    offsets = _disk_offsets(radius)

    for (center_x, center_y), count in counts.items():
        level = min(count, len(_DENSITY_ALPHAS) - 1)
        local_center_x = center_x - left
        local_center_y = center_y - top
        for delta_x, delta_y in offsets:
            x = local_center_x + delta_x
            y = local_center_y + delta_y
            if not 0 <= x < width or not 0 <= y < height:
                continue
            offset = y * width + x
            if level > level_map[offset]:
                level_map[offset] = level

    return NodeMarkerRaster(
        left, top, width, height, rgb, bytes(level_map)
    )


@lru_cache(maxsize=16)
def _disk_offsets(radius: int) -> tuple[tuple[int, int], ...]:
    radius_squared = radius * radius
    return tuple(
        (delta_x, delta_y)
        for delta_y in range(-radius, radius + 1)
        for delta_x in range(-radius, radius + 1)
        if delta_x * delta_x + delta_y * delta_y <= radius_squared
    )


def _hex_color(color: str) -> tuple[int, int, int]:
    if len(color) != 7 or not color.startswith("#"):
        raise ValueError("Node raster colors must use #rrggbb notation")
    try:
        return (
            int(color[1:3], 16),
            int(color[3:5], 16),
            int(color[5:7], 16),
        )
    except ValueError as exc:
        raise ValueError("Node raster colors must use #rrggbb notation") from exc


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xffffffff)
    )
