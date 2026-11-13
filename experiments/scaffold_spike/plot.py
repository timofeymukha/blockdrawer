"""Pillow pictures of a cross-field result: patches, boundaries, separatrices,
singularities (red +1/4, blue -1/4), corners (green), crosses."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw


def _palette(count):
    rng = np.random.default_rng(7)
    return [tuple(int(v) for v in 140 + 110 * rng.random(3)) for _ in range(count + 1)]


def render(res, path, window=None, width=1600, crosses=True):
    ny, nx = res.shape
    x0, y0 = res.origin
    x1, y1 = x0 + nx * res.h, y0 + ny * res.h
    if window is not None:
        x0, y0, x1, y1 = window
    scale = width / (x1 - x0)
    height = max(50, int((y1 - y0) * scale))
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def px(x, y):
        return ((x - x0) * scale, height - (y - y0) * scale)

    # patches / fluid as one array, cropped and scaled
    rgb = np.full((ny, nx, 3), 255, dtype=np.uint8)
    rgb[~res.fluid] = (90, 90, 90)
    if res.labels is not None:
        colors = np.asarray(_palette(int(res.labels.max())), dtype=np.uint8)
        lab = res.labels
        rgb[(lab > 0)] = colors[lab[lab > 0]]
    j0 = max(0, int((y0 - res.origin[1]) / res.h))
    j1 = min(ny, int((y1 - res.origin[1]) / res.h) + 1)
    i0 = max(0, int((x0 - res.origin[0]) / res.h))
    i1 = min(nx, int((x1 - res.origin[0]) / res.h) + 1)
    crop = rgb[j0:j1, i0:i1][::-1]
    base = Image.fromarray(crop)
    ax0, ay1 = px(res.origin[0] + i0 * res.h, res.origin[1] + j1 * res.h)
    ax1, ay0 = px(res.origin[0] + i1 * res.h, res.origin[1] + j0 * res.h)
    size = (max(1, int(round(ax1 - ax0))), max(1, int(round(ay0 - ay1))))
    img.paste(base.resize(size, Image.NEAREST), (int(round(ax0)), int(round(ay1))))
    if crosses:
        stride = max(1, int(round(12 / (res.h * scale)))) if res.h * scale < 12 else 1
        stride = max(stride, 1)
        arm = 0.4 * stride * res.h
        for j in range(j0, j1, stride):
            for i in range(i0, i1, stride):
                if not res.fluid[j, i] or np.isnan(res.z[j, i]):
                    continue
                th = np.angle(res.z[j, i]) / 4
                cx, cy = res.origin[0] + (i + 0.5) * res.h, res.origin[1] + (j + 0.5) * res.h
                for k in range(2):
                    a = th + k * math.pi / 2
                    draw.line([px(cx - arm * math.cos(a), cy - arm * math.sin(a)),
                               px(cx + arm * math.cos(a), cy + arm * math.sin(a))], fill=(60, 60, 60), width=1)
    for loop in res.loops:
        pts = [px(x, y) for x, y in np.vstack([loop, loop[:1]])]
        draw.line(pts, fill=(0, 0, 0), width=2)
    for tr in res.traces:
        color = (30, 30, 200) if tr["end"] != "limit" else (220, 120, 0)
        draw.line([px(x, y) for x, y in tr["points"]], fill=color, width=2)
    r = 5
    for x, y, idx in res.singularities:
        c = px(x, y)
        draw.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(220, 0, 0) if idx > 0 else (0, 80, 255))
    for c in res.corners:
        q = px(c[0], c[1])
        draw.rectangle([q[0] - 3, q[1] - 3, q[0] + 3, q[1] + 3], fill=(0, 160, 0))
    img.save(path)
    return path
