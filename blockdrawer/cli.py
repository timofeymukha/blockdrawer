"""Headless command-line interface for BlockDrawer sessions.

Every subcommand reads a session JSON file, does one job, and prints either
text or, with ``--json``, one JSON document. Failures print ``error: ...`` to
stderr (or a JSON object with an ``error`` key) and exit with status 1. Use
``python -m blockdrawer.cli --help`` or the installed ``blockdrawer-cli``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .checkmesh import (
    BLOCKMESH_ENV,
    CHECKMESH_ENV,
    CheckMeshError,
    DEFAULT_TIMEOUT_SECONDS,
    format_mesh_check,
    run_mesh_check,
)
from .commands import (
    COMMANDS,
    CommandError,
    apply_commands,
    command_specs,
    format_result,
    read_command_lines,
    spec_help_text,
)
from .describe import (
    SECTIONS,
    describe_model,
    filter_description,
    format_description,
)
from .domain import TopologyError
from .foam import block_mesh_dict, write_block_mesh_dict
from .geometry import GeometryImportError
from .model import MeshModel
from .quality import QualityThresholds, assess_quality, format_quality
from .render import (
    RenderError,
    RenderOptions,
    entity_bounds,
    options_with_highlights,
    render_to_file,
)
from .session import SessionError, load_session, save_session


HANDLED_ERRORS = (
    CheckMeshError,
    CommandError,
    GeometryImportError,
    OSError,
    RenderError,
    SessionError,
    TopologyError,
    ValueError,
)


class CliError(Exception):
    """A user-facing failure with a clean message."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    as_json = bool(getattr(args, "json", False))
    try:
        return int(handler(args) or 0)
    except HANDLED_ERRORS as exc:
        return _fail(str(exc), as_json)
    except CliError as exc:
        return _fail(str(exc), as_json)


def _fail(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 1


def _emit(data: Any, text: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=_json_default))
    else:
        sys.stdout.write(text)


def _json_default(value: Any) -> Any:
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blockdrawer-cli",
        description=(
            "Inspect, edit, render, export, and check BlockDrawer sessions "
            "without the GUI."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="COMMAND")

    new = subparsers.add_parser(
        "new", help="write a fresh single-block session",
    )
    new.add_argument("output", type=Path, help="session JSON path to create")
    new.add_argument("--force", action="store_true", help="replace an existing file")
    new.add_argument("--json", action="store_true")
    new.set_defaults(handler=_cmd_new)

    describe = subparsers.add_parser(
        "describe", help="list blocks, edges, vertices, patches, curves, and links",
    )
    describe.add_argument("session", type=Path)
    describe.add_argument("--json", action="store_true", help="print JSON")
    describe.add_argument(
        "--section", action="append", choices=SECTIONS, metavar="SECTION",
        help="limit output to a section (repeatable): " + ", ".join(SECTIONS),
    )
    describe.add_argument(
        "--only", nargs="+", metavar="ID",
        help="restrict blocks/edges/vertices/patches/curves to these IDs",
    )
    describe.set_defaults(handler=_cmd_describe)

    quality = subparsers.add_parser(
        "quality", help="report block and interface quality heuristics",
    )
    quality.add_argument("session", type=Path)
    quality.add_argument("--json", action="store_true")
    quality.add_argument("--min-angle", type=float, help="warn below this corner angle")
    quality.add_argument("--max-angle", type=float, help="warn above this corner angle")
    quality.add_argument(
        "--non-orthogonality", type=float, help="warn above this deviation from 90°",
    )
    quality.add_argument("--aspect-ratio", type=float, help="warn above this cell aspect")
    quality.add_argument(
        "--growth-ratio", type=float, help="warn above this cell-to-cell ratio",
    )
    quality.add_argument(
        "--interface-ratio", type=float,
        help="warn above this cell size jump across an internal edge",
    )
    quality.set_defaults(handler=_cmd_quality)

    render = subparsers.add_parser(
        "render", help="draw the topology to an .svg or .png file",
    )
    render.add_argument("session", type=Path)
    render.add_argument("-o", "--output", type=Path, required=True, help=".svg or .png")
    render.add_argument("--width", type=int, default=RenderOptions.width)
    render.add_argument("--height", type=int, default=RenderOptions.height)
    render.add_argument(
        "--bounds", type=float, nargs=4, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        help="world region to show (default: everything)",
    )
    render.add_argument(
        "--zoom", nargs="+", metavar="ID",
        help="frame these blocks, edges (a-b), or vertices instead of everything",
    )
    render.add_argument(
        "--margin", type=float, default=0.3,
        help="relative margin around --zoom entities (default 0.3)",
    )
    render.add_argument(
        "--highlight", nargs="+", metavar="ID",
        help="emphasize these blocks, edges (a-b), or vertices",
    )
    render.add_argument("--preview", action="store_true", help="draw interior mesh lines")
    render.add_argument(
        "--coarsening", type=int, default=1, help="keep every nth preview line",
    )
    render.add_argument("--title", help="legend title")
    render.add_argument("--no-grid", action="store_true")
    render.add_argument("--no-vertex-ids", action="store_true")
    render.add_argument("--no-block-ids", action="store_true")
    render.add_argument("--no-edge-cells", action="store_true")
    render.add_argument("--no-nodes", action="store_true", help="hide mesh node ticks")
    render.add_argument("--no-control-points", action="store_true")
    render.add_argument("--no-curves", action="store_true", help="hide reference curves")
    render.add_argument("--no-legend", action="store_true")
    render.add_argument("--no-links", action="store_true", help="hide spacing links")
    render.add_argument("--font-size", type=float, default=RenderOptions.font_size)
    render.add_argument("--json", action="store_true")
    render.set_defaults(handler=_cmd_render)

    export = subparsers.add_parser(
        "export", help="write blockMeshDict (to stdout without -o/--case)",
    )
    export.add_argument("session", type=Path)
    export.add_argument("-o", "--output", type=Path, help="dictionary path")
    export.add_argument(
        "--case", type=Path, help="OpenFOAM case; writes CASE/system/blockMeshDict",
    )
    export.add_argument("--json", action="store_true")
    export.set_defaults(handler=_cmd_export)

    validate = subparsers.add_parser("validate", help="check that a session loads and is valid")
    validate.add_argument("session", type=Path)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_cmd_validate)

    check = subparsers.add_parser(
        "check", help="run OpenFOAM blockMesh and checkMesh on the exported dictionary",
    )
    check.add_argument("session", type=Path)
    check.add_argument(
        "--case", type=Path, help="write and keep the case here instead of a temp dir",
    )
    check.add_argument("--keep", action="store_true", help="keep the temporary case")
    check.add_argument("--no-checkmesh", action="store_true", help="run blockMesh only")
    check.add_argument(
        "--blockmesh-command",
        help=f"command prefix accepting -case PATH (default: ${BLOCKMESH_ENV})",
    )
    check.add_argument(
        "--checkmesh-command",
        help=(
            f"command prefix (default: ${CHECKMESH_ENV}, else the blockMesh "
            "prefix with blockMesh replaced by checkMesh)"
        ),
    )
    check.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds per application",
    )
    check.add_argument("--log", action="store_true", help="print the complete logs")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=_cmd_check)

    apply = subparsers.add_parser(
        "apply",
        help="apply editing commands atomically and save the result",
        description=(
            "Apply commands in order to a copy of the session. If any command "
            "fails nothing is written. Commands are text lines such as "
            "'set_edge_cells v0-v1 20' or JSON objects such as "
            '\'{"op": "add_block", "edge": ["v1", "v2"]}\'. '
            "Run 'commands' to list them."
        ),
    )
    apply.add_argument("session", type=Path)
    apply.add_argument(
        "commands", nargs="*", metavar="COMMAND", help="commands to apply in order",
    )
    apply.add_argument(
        "-f", "--file", type=Path, action="append", default=[],
        help="read commands from a file (one per line, # comments); repeatable",
    )
    apply.add_argument("--stdin", action="store_true", help="read commands from stdin")
    apply.add_argument("-o", "--output", type=Path, help="write the edited session here")
    apply.add_argument("--in-place", action="store_true", help="overwrite the input")
    apply.add_argument(
        "--dry-run", action="store_true", help="apply and report without saving",
    )
    apply.add_argument("--json", action="store_true")
    apply.set_defaults(handler=_cmd_apply)

    commands = subparsers.add_parser(
        "commands", help="list the editing commands accepted by apply",
    )
    commands.add_argument("name", nargs="?", help="show one command")
    commands.add_argument("--json", action="store_true")
    commands.set_defaults(handler=_cmd_commands)

    return parser


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _load(path: Path) -> MeshModel:
    return load_session(path)


def _cmd_new(args: argparse.Namespace) -> int:
    if args.output.exists() and not args.force:
        raise CliError(f"{args.output} already exists; pass --force to replace it")
    model = MeshModel()
    save_session(model, args.output)
    _emit(
        {"ok": True, "session": str(args.output), "blocks": len(model.blocks)},
        f"Wrote {args.output} with {len(model.blocks)} block.\n",
        args.json,
    )
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    model = _load(args.session)
    data = describe_model(model)
    if args.only:
        data = filter_description(data, set(args.only))
    sections = tuple(args.section) if args.section else None
    text = format_description(data, sections=sections)
    if sections:
        data = {key: data[key] for key in sections if key in data}
    _emit(data, text, args.json)
    return 0


def _cmd_quality(args: argparse.Namespace) -> int:
    model = _load(args.session)
    thresholds = QualityThresholds.from_overrides(
        min_angle=args.min_angle,
        max_angle=args.max_angle,
        non_orthogonality=args.non_orthogonality,
        cell_aspect_ratio=args.aspect_ratio,
        cell_growth_ratio=args.growth_ratio,
        interface_size_ratio=args.interface_ratio,
    )
    report = assess_quality(model, thresholds)
    _emit(report.to_data(), format_quality(report), args.json)
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    model = _load(args.session)
    if args.width < 50 or args.height < 50:
        raise CliError("--width and --height must be at least 50 pixels")
    if args.coarsening < 1:
        raise CliError("--coarsening must be a positive integer")
    options = RenderOptions(
        width=args.width,
        height=args.height,
        bounds=tuple(args.bounds) if args.bounds else None,
        show_grid=not args.no_grid,
        show_vertex_ids=not args.no_vertex_ids,
        show_block_ids=not args.no_block_ids,
        show_edge_cells=not args.no_edge_cells,
        show_edge_nodes=not args.no_nodes,
        show_control_points=not args.no_control_points,
        show_curves=not args.no_curves,
        show_legend=not args.no_legend,
        show_spacing_links=not args.no_links,
        show_preview=args.preview,
        preview_coarsening=args.coarsening,
        title=args.title,
        font_size=args.font_size,
    )
    if args.zoom:
        if args.bounds:
            raise CliError("--zoom and --bounds are mutually exclusive")
        options = RenderOptions(
            **{**options.__dict__, "bounds": entity_bounds(
                model, args.zoom, margin=args.margin
            )}
        )
    if args.highlight:
        options = options_with_highlights(model, options, args.highlight)
    destination = render_to_file(model, args.output, options)
    _emit(
        {
            "ok": True,
            "output": str(destination),
            "width": options.width,
            "height": options.height,
            "bounds": list(options.bounds) if options.bounds else None,
        },
        f"Wrote {destination}\n",
        args.json,
    )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    model = _load(args.session)
    if args.output and args.case:
        raise CliError("-o and --case are mutually exclusive")
    if args.case:
        destination = args.case / "system" / "blockMeshDict"
        destination.parent.mkdir(parents=True, exist_ok=True)
    else:
        destination = args.output
    if destination is None:
        sys.stdout.write(block_mesh_dict(model))
        return 0
    write_block_mesh_dict(model, destination)
    _emit(
        {"ok": True, "output": str(destination), "blocks": len(model.blocks)},
        f"Wrote {destination}\n",
        args.json,
    )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    model = _load(args.session)
    model.validate()
    block_mesh_dict(model)  # also checks export-time constraints such as cyclic pairs
    summary = describe_model(model)["summary"]
    _emit(
        {"ok": True, "session": str(args.session), "summary": summary},
        f"OK: {args.session} has {summary['blocks']} block(s), "
        f"{summary['edges']} edges, {summary['total_cells']} cells and exports.\n",
        args.json,
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import shlex

    model = _load(args.session)
    result = run_mesh_check(
        model,
        blockmesh_command=(
            shlex.split(args.blockmesh_command) if args.blockmesh_command else None
        ),
        checkmesh_command=(
            shlex.split(args.checkmesh_command) if args.checkmesh_command else None
        ),
        case_dir=args.case,
        keep=args.keep,
        run_checkmesh=not args.no_checkmesh,
        timeout=args.timeout,
    )
    text = format_mesh_check(result)
    if args.log:
        text += "\n--- log.blockMesh ---\n" + result.block_mesh.log
        if result.check_mesh is not None:
            text += "\n--- log.checkMesh ---\n" + result.check_mesh.log
    data = result.to_data()
    if args.log:
        data["block_mesh"]["log"] = result.block_mesh.log
        if result.check_mesh is not None:
            data["check_mesh"]["log"] = result.check_mesh.log
    _emit(data, text, args.json)
    return 0 if result.ok else 1


def _cmd_apply(args: argparse.Namespace) -> int:
    if args.output and args.in_place:
        raise CliError("-o and --in-place are mutually exclusive")
    commands: list[Any] = []
    for path in args.file:
        commands.extend(read_command_lines(Path(path).read_text(encoding="utf-8")))
    if args.stdin:
        commands.extend(read_command_lines(sys.stdin.read()))
    commands.extend(args.commands)
    if not commands:
        raise CliError("No commands given; pass them as arguments, -f FILE, or --stdin")

    model = _load(args.session)
    batch = apply_commands(model, commands)

    destination: Path | None = None
    if args.in_place:
        destination = args.session
    elif args.output:
        destination = args.output
    saved = destination is not None and not args.dry_run
    if saved:
        assert destination is not None
        save_session(batch.model, destination)

    summary = describe_model(batch.model)["summary"]
    data = {
        "ok": True,
        "applied": len(batch.results),
        "saved": saved,
        "output": str(destination) if saved else None,
        "results": [result.to_data() for result in batch.results],
        "summary": summary,
    }
    lines = [format_result(result) for result in batch.results]
    if saved:
        lines.append(f"Saved {destination}")
    else:
        lines.append(
            "Not saved (dry run); pass -o OUTPUT or --in-place to write the result"
            if not args.dry_run else "Not saved (dry run)"
        )
    _emit(data, "\n".join(lines) + "\n", args.json)
    return 0


def _cmd_commands(args: argparse.Namespace) -> int:
    if args.name:
        spec = COMMANDS.get(args.name)
        if spec is None:
            raise CliError(
                f"Unknown command {args.name!r}; known commands: {', '.join(COMMANDS)}"
            )
        specs = [spec]
    else:
        specs = command_specs()
    _emit(
        {"commands": [spec.describe() for spec in specs]},
        spec_help_text(specs),
        args.json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
