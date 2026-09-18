"""Research command line for the agentic topology prototype.

This is deliberately outside BlockDrawer's command registry and MCP surface:
the operations here are experimental and must earn their way in.  Every
subcommand is stateless - it rebuilds the topology from the supplied geometry
and options - so an agent's decision is expressed as an option delta rather
than as hidden state.

    python experiments/agentic_topology/research_cli.py run --case periodic_hill \
        --output out/hill.png --json out/hill.json --session out/hill-session.json

    python experiments/agentic_topology/research_cli.py candidates --case two_circles
    python experiments/agentic_topology/research_cli.py apply --case two_circles \
        --move split_patch:b7
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image  # noqa: F401  (dependency check)
except ImportError as exc:  # pragma: no cover - research-tool dependency check
    raise SystemExit(
        "This research prototype needs NumPy and Pillow; neither is a "
        "BlockDrawer runtime dependency."
    ) from exc

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # pragma: no cover - import shim for running the script directly
    import blockdrawer  # noqa: F401
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import agent_ops  # noqa: E402
import analysis_plot  # noqa: E402
import block_layout  # noqa: E402
import fan_cavity  # noqa: E402
import patch_graph as pg  # noqa: E402
import layers as layer_module  # noqa: E402
import pipeline  # noqa: E402
import session_emit  # noqa: E402
import sizing  # noqa: E402
import spanned  # noqa: E402
import synthetic_cases as cases  # noqa: E402
import planar_domain as pdm  # noqa: E402
from pointlist import parse_curve_argument, read_point_list  # noqa: E402


def add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--curve",
        action="append",
        default=[],
        type=parse_curve_argument,
        metavar="NAME=PATH",
        help="closed point-list body; repeat for every component (external flow)",
    )
    parser.add_argument(
        "--case",
        help="a built-in synthetic case: "
        + ", ".join(sorted({*cases.CASES, *cases.INTERNAL_CASES})),
    )
    parser.add_argument("--width", type=int, default=700, help="medial raster width")
    parser.add_argument("--farfield-scale", type=float, default=3.0)
    parser.add_argument(
        "--farfield-shape", choices=("circle", "rectangle"), default="circle"
    )
    parser.add_argument("--farfield-name", default="farfield")
    parser.add_argument(
        "--span-gaps", action="store_true",
        help="try one core strip across narrow body-body gaps (experimental; rejected trials roll back)",
    )
    parser.add_argument(
        "--wall-edge-type", choices=("polyLine", "spline"), default="polyLine"
    )
    parser.add_argument("--coverage-samples", type=int, default=400)
    parser.add_argument(
        "--first-width-ratio",
        type=float,
        default=sizing.SizingOptions.first_width_ratio,
        help="first cell width as a fraction of the domain scale",
    )
    parser.add_argument(
        "--core-size-ratio",
        type=float,
        default=sizing.SizingOptions.core_size_ratio,
        help="isotropic core cell size as a fraction of the domain scale",
    )
    parser.add_argument("--growth", type=float, default=sizing.SizingOptions.growth)
    parser.add_argument(
        "--cell-budget", type=int, default=None, help="cap on the total cell count"
    )
    parser.add_argument(
        "--clearance-fraction",
        type=float,
        default=layer_module.LayerOptions.clearance_fraction,
    )
    parser.add_argument(
        "--layer-height-ratio",
        type=lambda text: None if text == "series" else float(text),
        default=pipeline.PipelineOptions.layer_height_ratio,
        metavar="FRACTION",
        help=(
            "boundary-layer band height as a fraction of the domain scale "
            "(default %(default)s); 'series' uses the height at which the "
            "metric's wall-normal series reaches the core size instead"
        ),
    )
    parser.add_argument(
        "--max-length-ratio",
        type=float,
        default=pg.StructureLimits.max_length_ratio,
        metavar="RATIO",
        help=(
            "largest geometric length ratio one opposite-edge equality "
            "component may force; the structural sizing feasibility limit"
        ),
    )
    parser.add_argument(
        "--max-wall-turning",
        type=float,
        default=math.degrees(block_layout.LayoutOptions.max_wall_turning),
        metavar="DEGREES",
        help=(
            "largest wall turning one annular patch may span before it is cut; "
            "smaller values give more, straighter band blocks"
        ),
    )
    parser.add_argument(
        "--no-layers", action="store_true", help="build the core without wall bands"
    )
    parser.add_argument(
        "--no-cavity-repair",
        "--no-fans",
        dest="no_cavity_repair",
        action="store_true",
        help="leave every sharp-feature cavity exactly as the producer built it",
    )
    parser.add_argument(
        "--cavity-choice",
        action="append",
        default=[],
        metavar="FEATURE=TEMPLATE",
        help="force one cavity template, for example gate_0_anchor_24=seam",
    )
    parser.add_argument(
        "--allow-count-coupling",
        action="store_true",
        help=(
            "permit a cavity replacement that merges a tangential and a normal "
            "cell-count component inside a boundary layer"
        ),
    )
    parser.add_argument(
        "--no-grid-quality",
        action="store_true",
        help="skip the sampled transfinite-grid evaluation",
    )
    parser.add_argument(
        "--no-reference-curves",
        action="store_true",
        help="do not attach the supplied point lists as reference geometry",
    )
    parser.add_argument(
        "--split",
        action="append",
        default=[],
        metavar="CELL:CUT",
        help="force an extra anchor inside one annular patch; repeatable",
    )


def build_options(arguments) -> pipeline.PipelineOptions:
    forced = []
    for item in arguments.split:
        cell, _, cut = item.partition(":")
        forced.append((int(cell), int(cut)))
    choices = []
    for item in getattr(arguments, "cavity_choice", []):
        feature, _, template = item.partition("=")
        if not template:
            raise SystemExit("--cavity-choice takes FEATURE=TEMPLATE")
        choices.append((feature, template))
    return pipeline.PipelineOptions(
        span=spanned.SpanOptions(enabled=arguments.span_gaps),
        grid_width=arguments.width,
        farfield_scale=arguments.farfield_scale,
        farfield_shape=arguments.farfield_shape,
        farfield_name=arguments.farfield_name,
        wall_edge_style=arguments.wall_edge_type,
        coverage_samples=arguments.coverage_samples,
        reference_curves=not arguments.no_reference_curves,
        evaluate_grid=not arguments.no_grid_quality,
        forced_splits=tuple(forced),
        layer_height_ratio=arguments.layer_height_ratio,
        structure=pg.StructureLimits(max_length_ratio=arguments.max_length_ratio),
        layout=block_layout.LayoutOptions(
            max_wall_turning=math.radians(arguments.max_wall_turning)
        ),
        layer=layer_module.LayerOptions(
            enabled=not arguments.no_layers,
            clearance_fraction=arguments.clearance_fraction,
        ),
        cavity=fan_cavity.CavityOptions(
            enabled=not arguments.no_cavity_repair,
            allow_count_coupling=arguments.allow_count_coupling,
            choices=tuple(choices),
        ),
        sizing=sizing.SizingOptions(
            first_width_ratio=arguments.first_width_ratio,
            core_size_ratio=arguments.core_size_ratio,
            growth=arguments.growth,
            budget=arguments.cell_budget,
        ),
    )


def run_case(arguments) -> pipeline.PipelineResult:
    options = build_options(arguments)
    if arguments.case and arguments.case in cases.INTERNAL_CASES:
        domain = pdm.from_internal_case(cases.INTERNAL_CASES[arguments.case]())
        return pipeline.run_internal(domain, options)
    if arguments.case:
        if arguments.case not in cases.CASES:
            raise SystemExit(f"unknown case {arguments.case!r}")
        names, loops = cases.CASES[arguments.case]()
        return pipeline.run_external(names, loops, options)
    if not arguments.curve:
        raise SystemExit("supply --case NAME or at least one --curve NAME=PATH")
    names = [name for name, _path in arguments.curve]
    if len(set(names)) != len(names):
        raise SystemExit("curve names must be unique")
    if arguments.farfield_name in names:
        raise SystemExit("the farfield name must differ from every curve name")
    loops = [read_point_list(path) for _name, path in arguments.curve]
    return pipeline.run_external(names, loops, options)


def write_artifacts(result, arguments) -> list[Path]:
    written: list[Path] = []
    analysis = result.analysis
    if getattr(arguments, "output", None):
        canvas = analysis_plot.render_result(result, arguments.plot_width)
        written.append(canvas.save(arguments.output))
    if result.admissible and getattr(arguments, "session", None):
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
        if getattr(arguments, "block_mesh_dict", None):
            arguments.block_mesh_dict.parent.mkdir(parents=True, exist_ok=True)
            arguments.block_mesh_dict.write_text(text, encoding="utf-8")
            written.append(arguments.block_mesh_dict)
        analysis["session"]["render_bytes"] = len(
            render_svg(reloaded, RenderOptions(show_preview=False))
        )
        if getattr(arguments, "session_render", None):
            arguments.session_render.parent.mkdir(parents=True, exist_ok=True)
            written.append(
                render_to_file(
                    reloaded,
                    arguments.session_render,
                    RenderOptions(width=1600, height=1200),
                )
            )
    elif getattr(arguments, "session", None):
        analysis["session_not_written"] = (
            "the topology is not a mesh - it is crossed, uncovered, or has an "
            "inverted sampled cell - so no partial session is written"
        )
    if result.admissible and not result.resolved:
        analysis.setdefault("session", {})["below_quality_targets"] = (
            result.described_failures()
        )
    if result.admissible and not result.within_sizing_targets:
        analysis.setdefault("session", {})["sizing_misses"] = [
            failure.described() for failure in result.sizing_failures
        ]
    if getattr(arguments, "json", None):
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(analysis, indent=2, default=_encode) + "\n", encoding="utf-8"
        )
        written.append(arguments.json)
    return written


def _encode(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


def command_run(arguments) -> int:
    result = run_case(arguments)
    for path in write_artifacts(result, arguments):
        print(f"wrote {path}")
    summary = result.graph.summary() if result.graph else {}
    print(
        "family=%s blocks=%s cells=%s singularities=%s inverted=%s"
        % (
            result.family,
            summary.get("faces", "-"),
            result.counts.total_cells if result.counts else "-",
            summary.get("singularity_count", "-"),
            result.grid.inverted_cells if result.grid else "-",
        )
    )
    print(
        "topology_valid=%s untangled=%s within_shape_targets=%s "
        "sizing_feasible=%s admissible=%s resolved=%s"
        % (
            result.topology_valid,
            result.untangled,
            result.within_shape_targets,
            result.sizing_feasible,
            result.admissible,
            result.resolved,
        )
    )
    if not result.topology_valid or not result.untangled:
        for error in result.errors:
            print(f"{error['stage']} stage failed: {error['error']}", file=sys.stderr)
        for failure in result.failures:
            print(f"unresolved: {failure.get('reason', failure)}", file=sys.stderr)
        for problem in result.problems[:5]:
            print(f"graph problem: {problem}", file=sys.stderr)
        for record in _unresolved_cavities(result):
            print(
                "unresolved cavity at %s: %s (needs a different %s)"
                % (
                    record["feature"],
                    str(record.get("rejection"))[:160],
                    record.get("needs") or "placement",
                ),
                file=sys.stderr,
            )
        return 1
    for described in result.described_failures():
        print(
            "below target: %s = %.4g, limit %.4g (%s) at %s"
            % (
                described["metric"],
                described["observed"],
                described["limit"],
                described["comparison"],
                described.get("block") or described.get("roles") or "-",
            ),
            file=sys.stderr,
        )
    for failure in result.sizing_failures:
        described = failure.described()
        print(
            "sizing (informational): %s = %.4g, limit %.4g at %s"
            % (
                described["metric"],
                described["observed"],
                described["limit"],
                described["block"] or "-",
            ),
            file=sys.stderr,
        )
    return 0 if result.resolved else 1


def _unresolved_cavities(result) -> list:
    repair = getattr(result, "cavity", None)
    if repair is None:
        return []
    return [
        record
        for record in repair.cavities
        if record.get("applied") is None and not record.get("kept_existing")
    ]


def command_describe(arguments) -> int:
    result = run_case(arguments)
    print(json.dumps(agent_ops.describe(result), indent=2, default=_encode))
    return 0 if result.resolved else 1


def command_cavities(arguments) -> int:
    """Every sharp-feature cavity with its validated alternatives."""
    result = run_case(arguments)
    report = {
        "acceptance": (result.analysis.get("acceptance") or {}),
        "cavities": (
            {} if result.cavity is None else fan_cavity.described(result.cavity)
        ),
    }
    print(json.dumps(report, indent=2, default=_encode))
    return 0 if not _unresolved_cavities(result) else 1


def command_candidates(arguments) -> int:
    result = run_case(arguments)
    print(json.dumps(agent_ops.candidate_report(result), indent=2, default=_encode))
    return 0


def command_apply(arguments) -> int:
    import moves as move_module

    before = run_case(arguments)
    move = agent_ops.find_move(before, arguments.move)
    options = move_module.apply(before.options, move)
    after = _rerun(arguments, options)
    report = {
        "move": move.described(),
        "comparison": agent_ops.compare(before, after),
    }
    print(json.dumps(report, indent=2, default=_encode))
    if getattr(arguments, "output", None) or getattr(arguments, "json", None):
        for path in write_artifacts(after, arguments):
            print(f"wrote {path}", file=sys.stderr)
    return 0 if after.resolved else 1


def command_focus(arguments) -> int:
    result = run_case(arguments)
    window = agent_ops.focus(
        result, block=arguments.block, radius=arguments.radius
    )
    if window is None:
        print(json.dumps({"focus": None, "reason": "nothing to focus on"}))
        return 0
    if getattr(arguments, "output", None):
        canvas = analysis_plot.render_result(
            result,
            arguments.plot_width,
            bounds=tuple(window["bounds"]),
            title="Focused diagnostic",
        )
        path = canvas.save(arguments.output)
        window["picture"] = str(path)
    print(json.dumps(window, indent=2, default=_encode))
    return 0


def _rerun(arguments, options):
    if arguments.case and arguments.case in cases.INTERNAL_CASES:
        domain = pdm.from_internal_case(cases.INTERNAL_CASES[arguments.case]())
        return pipeline.run_internal(domain, options)
    if arguments.case:
        names, loops = cases.CASES[arguments.case]()
        return pipeline.run_external(names, loops, options)
    names = [name for name, _path in arguments.curve]
    loops = [read_point_list(path) for _name, path in arguments.curve]
    return pipeline.run_external(names, loops, options)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="build a topology and write its artifacts")
    add_input_arguments(run)
    run.add_argument("--output", type=Path, help="analysis PNG path")
    run.add_argument("--plot-width", type=int, default=1100)
    run.add_argument("--json", type=Path, help="analysis JSON path")
    run.add_argument("--session", type=Path, help="BlockDrawer session path")
    run.add_argument("--session-render", type=Path)
    run.add_argument("--block-mesh-dict", type=Path)
    run.set_defaults(handler=command_run)

    describe = sub.add_parser("describe", help="agent-facing description of a run")
    add_input_arguments(describe)
    describe.set_defaults(handler=command_describe)

    cavities = sub.add_parser(
        "cavities", help="sharp-feature cavities and their validated alternatives"
    )
    add_input_arguments(cavities)
    cavities.set_defaults(handler=command_cavities)

    candidates = sub.add_parser("candidates", help="ranked candidate topology moves")
    add_input_arguments(candidates)
    candidates.set_defaults(handler=command_candidates)

    apply_move = sub.add_parser("apply", help="apply one candidate move and compare")
    add_input_arguments(apply_move)
    apply_move.add_argument("--move", required=True, help="candidate identifier")
    apply_move.add_argument("--output", type=Path)
    apply_move.add_argument("--plot-width", type=int, default=1100)
    apply_move.add_argument("--json", type=Path)
    apply_move.add_argument("--session", type=Path)
    apply_move.add_argument("--session-render", type=Path)
    apply_move.add_argument("--block-mesh-dict", type=Path)
    apply_move.set_defaults(handler=command_apply)

    focus = sub.add_parser("focus", help="render and describe one bad region")
    add_input_arguments(focus)
    focus.add_argument("--block", help="block id to centre on, for example b12")
    focus.add_argument("--radius", type=float, help="window radius in model units")
    focus.add_argument("--output", type=Path)
    focus.add_argument("--plot-width", type=int, default=900)
    focus.set_defaults(handler=command_focus)
    return parser


def main(argv=None) -> int:
    arguments = build_parser().parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
