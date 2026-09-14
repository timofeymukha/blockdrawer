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


ROLE_FILLS = {
    "layer": (214, 234, 248),
    "core": (250, 240, 220),
    "block": (235, 235, 245),
}
ROLE_OUTLINE = (80, 80, 90)


def render_result(result, width: int = 1100, *, bounds=None, title: str = None):
    """Draw whatever a pipeline run produced, including a partial failed run."""
    domain = result.domain
    box = bounds or (domain.bounds() if domain is not None else (0.0, 0.0, 1.0, 1.0))
    canvas = Canvas(box, width)
    diagram = result.diagram
    if diagram is not None and bounds is None:
        canvas.paste_field(region_field(diagram))
    graph = result.graph
    if graph is not None:
        for face in graph.faces:
            canvas.polygon(
                graph.face_outline(face),
                ROLE_FILLS.get(face.role, ROLE_FILLS["block"]),
                ROLE_OUTLINE,
                1,
            )
        for edge in graph.edges.values():
            if edge.role in ("front",):
                canvas.polyline(edge.path, (20, 120, 80), 2)
    if diagram is not None:
        for branch in diagram.branches:
            canvas.polyline(branch.path, BRANCH_COLOUR, 2)
    if domain is not None:
        for loop in domain.loops:
            canvas.polyline(loop.points(), (15, 17, 20), 2)
    if graph is not None:
        for vertex in graph.vertices.values():
            canvas.marker(vertex.point, (250, 210, 60), 2, outline=None)
        for record in graph.singularities():
            canvas.marker(record["point"], (150, 60, 200), 4)
    if result.grid is not None:
        for record in result.grid.worst_cells[:12]:
            canvas.cross(record["point"], (190, 30, 45), 6, 2)
    for failure in result.failures:
        for point in failure.get("gate_points", []):
            canvas.cross(point, (220, 120, 0), 8, 3)
    repair = getattr(result, "cavity", None)
    if repair is not None:
        for record in repair.cavities:
            cavity = record.get("cavity") or {}
            point = cavity.get("point")
            if point is None:
                continue
            if record.get("applied"):
                canvas.marker(point, (40, 160, 90), 6)
            elif record.get("kept_existing"):
                canvas.marker(point, (120, 140, 170), 5)
            else:
                canvas.cross(point, (200, 40, 120), 9, 3)
    rows = [(None, "Domain")]
    if domain is not None:
        for chain in domain.chains():
            rows.append((None, f"  {chain.name}: {chain.role}"))
    rows.extend(
        [
            (None, ""),
            (ROLE_FILLS["layer"], "boundary-layer band"),
            (ROLE_FILLS["core"], "core block"),
            (None, "purple dot:  singularity"),
            (None, "red X:       worst sampled cell"),
            (None, "green dot:   cavity replaced"),
            (None, "grey dot:    cavity kept as it was"),
            (None, "magenta X:   cavity unresolved"),
            (None, ""),
        ]
    )
    if graph is not None:
        summary = graph.summary()
        rows.extend(
            [
                (None, "blocks:   %d" % summary["faces"]),
                (None, "vertices: %d" % summary["vertices"]),
                (None, "singularities: %d" % summary["singularity_count"]),
            ]
        )
    if result.counts is not None:
        rows.append((None, "cells:    %d" % result.counts.total_cells))
    if result.grid is not None:
        report = result.grid
        rows.extend(
            [
                (None, "inverted cells: %d" % report.inverted_cells),
                (
                    None,
                    "min scaled Jacobian: %s"
                    % _value(report.minimum_scaled_jacobian),
                ),
                (
                    None,
                    "max non-orthogonality: %s"
                    % _value(report.maximum_non_orthogonality),
                ),
                (None, "max skewness: %s" % _value(report.maximum_skewness)),
            ]
        )
    rows.append((None, ""))
    rows.extend(
        [
            (None, "topology valid: %s" % result.topology_valid),
            (None, "untangled:      %s" % result.untangled),
            (None, "shape targets: %s" % result.within_shape_targets),
            (None, "sizing feasible: %s" % result.sizing_feasible),
        ]
    )
    for described in result.described_failures()[:4]:
        rows.append(
            (
                None,
                "  %s %.3g > %.3g"
                % (described["metric"][:26], described["observed"], described["limit"]),
            )
        )
    for error in result.errors:
        rows.append((None, "%s: %s" % (error["stage"], error["error"][:38])))
    for failure in result.failures[:4]:
        rows.append((None, str(failure.get("reason", ""))[:44]))
    draw_legend(canvas, title or f"Agentic topology ({result.family})", rows)
    return canvas


def _value(extreme) -> str:
    return "-" if extreme is None else f"{extreme.value:.4g} in {extreme.block}"
