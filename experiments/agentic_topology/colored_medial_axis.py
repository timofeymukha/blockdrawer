"""Research prototype: automatic quadrilateral block topology around 2D bodies.

This is deliberately outside the BlockDrawer runtime package.  It uses NumPy
and Pillow to build a component-coloured generalized Voronoi diagram from
closed point-list curves, cuts every Voronoi cell into four-sided patches, and
relaxes those patches into a conformal all-quadrilateral block topology that
can be written as an ordinary BlockDrawer session.

The raster diagram only decides connectivity.  Junction points, bisector
branches, cut anchors and gate vertices are all recomputed analytically, so the
result does not depend on the raster resolution beyond the resolution needed to
see the same graph.

    python experiments/agentic_topology/colored_medial_axis.py \
      --curve slat=slat.dat --curve main=main.dat --curve flap=flap.dat \
      --output coupling.png --json coupling.json --session coupling-session.json

Point lists use BlockDrawer's ordinary ``x y`` / ``x, y`` format and describe
closed bodies; an open final segment is closed automatically.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image  # noqa: F401  (imported for the dependency check)
except ImportError as exc:  # pragma: no cover - research-tool dependency check
    raise SystemExit(
        "This research prototype needs NumPy and Pillow; neither is a "
        "BlockDrawer runtime dependency."
    ) from exc

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis_plot  # noqa: E402
import block_layout  # noqa: E402
import patch_solver  # noqa: E402
import pipeline  # noqa: E402
import session_emit  # noqa: E402
from pointlist import parse_curve_argument, read_point_list  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--curve",
        action="append",
        required=True,
        type=parse_curve_argument,
        metavar="NAME=PATH",
        help="closed point-list body; repeat for every component",
    )
    parser.add_argument("--output", type=Path, required=True, help="output PNG path")
    parser.add_argument("--json", type=Path, help="analysis JSON path")
    parser.add_argument(
        "--session",
        type=Path,
        help="write the completed topology as a BlockDrawer session",
    )
    parser.add_argument(
        "--session-render",
        type=Path,
        help="render the emitted session headlessly to this .png or .svg",
    )
    parser.add_argument(
        "--block-mesh-dict",
        type=Path,
        help="write the exported blockMeshDict of the emitted session",
    )
    parser.add_argument(
        "--width", type=int, default=900, help="analysis raster width in pixels"
    )
    parser.add_argument(
        "--plot-width", type=int, default=1100, help="analysis picture width in pixels"
    )
    parser.add_argument(
        "--farfield-scale",
        type=float,
        default=3.0,
        help="farfield size as a multiple of the geometry radius",
    )
    parser.add_argument(
        "--farfield-shape",
        choices=("circle", "rectangle"),
        default="circle",
        help="trial farfield boundary; the circle is rotation invariant",
    )
    parser.add_argument(
        "--farfield-name", default="farfield", help="name of the farfield patch"
    )
    parser.add_argument(
        "--cells", type=int, default=10, help="uniform cell count on every edge"
    )
    parser.add_argument(
        "--wall-edge-type",
        choices=("polyLine", "spline"),
        default="polyLine",
        help="how supplied wall point lists become block-edge geometry",
    )
    parser.add_argument(
        "--max-wall-turning",
        type=float,
        default=100.0,
        help="degrees of wall turning above which a patch is split",
    )
    parser.add_argument(
        "--coverage-samples",
        type=int,
        default=400,
        help="raster resolution of the independent coverage check",
    )
    parser.add_argument(
        "--no-reference-curves",
        action="store_true",
        help="do not attach the supplied point lists as reference geometry",
    )
    return parser


def render_analysis(
    result: pipeline.PipelineResult, width: int
) -> analysis_plot.Canvas:
    """Draw whatever the run produced, including a partial failed run."""
    sites = result.sites
    diagram = result.diagram
    if diagram is not None:
        canvas = analysis_plot.Canvas(diagram.raster.bounds, width)
        canvas.paste_field(analysis_plot.region_field(diagram))
    else:
        loop = sites[-1].curve.loop()
        bounds = (
            float(np.min(loop[:, 0])),
            float(np.min(loop[:, 1])),
            float(np.max(loop[:, 0])),
            float(np.max(loop[:, 1])),
        )
        canvas = analysis_plot.Canvas(bounds, width)
    if result.complex is not None:
        for index, block in enumerate(result.complex.blocks):
            canvas.polygon(
                result.complex.block_outline(block),
                analysis_plot.BLOCK_FILLS[index % len(analysis_plot.BLOCK_FILLS)],
                (70, 70, 70),
                1,
            )
    if diagram is not None:
        for branch in diagram.branches:
            canvas.polyline(branch.path, analysis_plot.BRANCH_COLOUR, 2)
    for site in sites:
        canvas.polyline(site.curve.loop(), (15, 17, 20), 2)
    if diagram is not None:
        for junction in diagram.junctions:
            canvas.marker(junction.point, (255, 255, 255), 5)
    if result.complex is not None:
        for vertex in result.complex.vertices.values():
            canvas.marker(vertex.point, (250, 210, 60), 2, outline=None)
        for report in result.reports:
            if not report.convex:
                block = result.complex.blocks[report.patch]
                canvas.cross(
                    np.mean(result.complex.corner_points(block), axis=0), (190, 30, 45)
                )
    for failure in result.solve.failures:
        chain = failure.get("wall_chain")
        if chain:
            canvas.cross(np.mean(np.asarray(chain), axis=0), (190, 30, 45))
    rows = [(None, "Nearest boundary component")]
    for index, site in enumerate(sites):
        rows.append(
            (
                analysis_plot.REGION_COLOURS[index % len(analysis_plot.REGION_COLOURS)],
                site.name,
            )
        )
    rows.extend(
        [
            (None, ""),
            (None, "junctions: %s" % _count(diagram, "junctions")),
            (None, "branches: %s" % _count(diagram, "branches")),
            (
                None,
                "blocks:   %s"
                % (len(result.complex.blocks) if result.complex else "-"),
            ),
            (None, "worst scaled Jacobian: %.4f" % result.solve.worst_quality),
            (None, "anchor splits: %d" % result.solve.splits),
            (None, ""),
            (None, "White circle: Voronoi junction"),
            (None, "Yellow dot:  block vertex"),
            (None, "Red X:       unresolved region"),
            (None, ""),
            (None, "resolved" if result.resolved else "UNRESOLVED - see JSON"),
        ]
    )
    for error in result.errors:
        rows.append((None, "%s: %s" % (error["stage"], error["error"][:40])))
    analysis_plot.draw_legend(canvas, "Agentic block topology", rows)
    return canvas


def _count(diagram, attribute: str) -> str:
    return "-" if diagram is None else str(len(getattr(diagram, attribute)))


def main(argv=None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.width < 200:
        raise SystemExit("--width must be at least 200")
    if arguments.farfield_scale <= 1.0:
        raise SystemExit("--farfield-scale must be greater than 1")
    if arguments.cells < 1:
        raise SystemExit("--cells must be positive")
    names = [name for name, _path in arguments.curve]
    if len(set(names)) != len(names):
        raise SystemExit("curve names must be unique")
    if arguments.farfield_name in names:
        raise SystemExit("the farfield name must differ from every curve name")
    loops = [read_point_list(path) for _name, path in arguments.curve]

    options = pipeline.PipelineOptions(
        grid_width=arguments.width,
        farfield_scale=arguments.farfield_scale,
        farfield_shape=arguments.farfield_shape,
        farfield_name=arguments.farfield_name,
        cells=arguments.cells,
        wall_edge_style=arguments.wall_edge_type,
        coverage_samples=arguments.coverage_samples,
        reference_curves=not arguments.no_reference_curves,
        layout=block_layout.LayoutOptions(
            max_wall_turning=math.radians(arguments.max_wall_turning)
        ),
        solver=patch_solver.SolverOptions(),
    )
    result = pipeline.run(names, loops, options)
    analysis = result.analysis

    written: list[Path] = []
    canvas = render_analysis(result, arguments.plot_width)
    written.append(canvas.save(arguments.output))

    if result.resolved and arguments.session is not None:
        destination = session_emit.write_session(result.model, arguments.session)
        reloaded = session_emit.round_trip(destination)
        analysis["session"]["path"] = str(destination)
        analysis["session"]["reloaded_signature"] = session_emit.topology_signature(
            reloaded
        )
        written.append(destination)
        from blockdrawer.foam import block_mesh_dict
        from blockdrawer.render import RenderOptions, render_svg, render_to_file

        text = block_mesh_dict(reloaded)
        analysis["session"]["block_mesh_dict_bytes"] = len(text)
        if arguments.block_mesh_dict is not None:
            arguments.block_mesh_dict.parent.mkdir(parents=True, exist_ok=True)
            arguments.block_mesh_dict.write_text(text, encoding="utf-8")
            written.append(arguments.block_mesh_dict)
        picture = render_svg(reloaded, RenderOptions(show_preview=False))
        analysis["session"]["render_bytes"] = len(picture)
        if arguments.session_render is not None:
            arguments.session_render.parent.mkdir(parents=True, exist_ok=True)
            written.append(
                render_to_file(
                    reloaded,
                    arguments.session_render,
                    RenderOptions(width=1600, height=1200),
                )
            )
    elif arguments.session is not None:
        analysis["session_not_written"] = (
            "unresolved regions remain; no partial session is written"
        )

    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(analysis, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        written.append(arguments.json)

    for path in written:
        print(f"wrote {path}")
    print(
        "blocks=%s junctions=%s branches=%s worst scaled Jacobian=%.4f"
        % (
            len(result.complex.blocks) if result.complex else "-",
            _count(result.diagram, "junctions"),
            _count(result.diagram, "branches"),
            result.solve.worst_quality,
        )
    )
    if not result.resolved:
        for error in result.errors:
            print(
                "%s stage failed: %s" % (error["stage"], error["error"]),
                file=sys.stderr,
            )
        print("unresolved regions remain; see the JSON report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
