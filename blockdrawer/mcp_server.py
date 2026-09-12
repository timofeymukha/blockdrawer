"""Model Context Protocol server exposing BlockDrawer sessions as agent tools.

The tools are thin wrappers over the same modules the command-line interface
uses: the command registry, description, quality heuristics, renderer, export,
and the OpenFOAM check runner. Every tool takes a session path, so the server
itself is stateless and several agents or a person in the GUI can work on the
same file.

The ``mcp`` package is an optional dependency (``pip install blockdrawer[mcp]``).
The tool functions in this module are plain Python and import without it;
only :func:`build_server` and :func:`main` need it.
"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path
import sys
from typing import Any, Callable, Sequence

from .checkmesh import format_mesh_check, run_mesh_check
from .cli import HANDLED_ERRORS, CliError
from .command_line import split_command_line
from .commands import (
    COMMANDS,
    apply_commands as _apply_commands,
    command_specs,
    format_result,
)
from .describe import SECTIONS, describe_model, filter_description, format_description
from .foam import block_mesh_dict, write_block_mesh_dict
from .model import MeshModel
from .quality import QualityThresholds, assess_quality, format_quality
from .render import (
    RenderOptions,
    entity_bounds,
    options_with_highlights,
    render_png_bytes,
    render_to_file,
)
from .session import load_session, save_session


SERVER_NAME = "blockdrawer"

INSTRUCTIONS = """\
BlockDrawer edits 2D block topologies for OpenFOAM blockMesh. A session is a
JSON file; every tool takes its path. Typical loop:

1. describe_session to learn block, vertex, and edge IDs. Edges are written
   first-second (either order), e.g. v0-v1.
2. edit_session with a list of commands such as "set_edge_cells v0-v1 40",
   "add_block v1-v2", "set_edge_grading v0-v1 start_width 0.002",
   "set_edge_type v2-v3 arc", "split_edge v1-v2 0.5", "add_boundary inlet",
   "set_edge_boundary v0-v3 inlet". list_commands documents all of them.
   Batches are atomic: nothing is saved when any command fails.
3. quality_report for corner angles, aspect ratios, growth, and size jumps.
4. render_session to look at the result; use zoom and highlight for detail.
5. check_mesh to run blockMesh and checkMesh when OpenFOAM is available, then
   export_block_mesh_dict into the case.

For pseudo-2D cases set both z patches to type empty with
set_export_settings z_min_patch_type=empty z_max_patch_type=empty; otherwise
checkMesh treats the mesh as 3D.
"""


class ToolFailure(Exception):
    """A clean, user-facing tool failure (translated to MCP ToolError)."""


# ---------------------------------------------------------------------------
# Tool implementations (plain functions, usable without the mcp package)
# ---------------------------------------------------------------------------


def new_session(path: str, force: bool = False) -> dict[str, Any]:
    """Create a fresh single-block session file at ``path``."""
    destination = Path(path)
    if destination.exists() and not force:
        raise ToolFailure(f"{destination} already exists; pass force=true to replace it")
    model = MeshModel()
    save_session(model, destination)
    return {"ok": True, "session": str(destination), "summary": describe_model(model)["summary"]}


def describe_session(
    path: str,
    sections: list[str] | None = None,
    only: list[str] | None = None,
    as_text: bool = False,
) -> dict[str, Any]:
    """Describe blocks, edges, vertices, patches, curves, and spacing links.

    ``sections`` limits the output to some of: summary, settings, blocks,
    edges, vertices, boundaries, curves, spacing_links. ``only`` restricts the
    lists to the given block, edge, vertex, patch, or curve IDs. ``as_text``
    returns the compact terminal listing instead of structured data.
    """
    model = load_session(path)
    data = describe_model(model)
    if only:
        data = filter_description(data, set(only))
    wanted = tuple(sections) if sections else None
    if wanted:
        unknown = [name for name in wanted if name not in SECTIONS]
        if unknown:
            raise ToolFailure(
                f"Unknown section(s) {', '.join(unknown)}; choose from {', '.join(SECTIONS)}"
            )
    if as_text:
        return {"text": format_description(data, sections=wanted)}
    if wanted:
        data = {key: data[key] for key in wanted if key in data}
    return data


def list_commands(name: str | None = None) -> dict[str, Any]:
    """List the editing commands accepted by edit_session, or describe one."""
    if name:
        spec = COMMANDS.get(name)
        if spec is None:
            raise ToolFailure(
                f"Unknown command {name!r}; known commands: {', '.join(COMMANDS)}"
            )
        return {"commands": [spec.describe()]}
    return {
        "commands": [spec.describe() for spec in command_specs()],
        "edge_notation": "first-second in either order, e.g. v0-v1",
    }


def edit_session(
    path: str,
    commands: list[str | dict[str, Any]],
    output: str | None = None,
    in_place: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply editing commands atomically and save the result.

    ``commands`` are text lines ("set_edge_cells v0-v1 20") or objects
    ({"op": "add_block", "edge": ["v1", "v2"]}). Pass ``in_place=true`` to
    overwrite ``path`` or ``output`` to write elsewhere; with neither, or with
    ``dry_run``, the result is reported but not saved. If any command fails
    nothing is written and the error names the failing step.
    """
    if output and in_place:
        raise ToolFailure("output and in_place are mutually exclusive")
    if not commands:
        raise ToolFailure("No commands given")
    model = load_session(path)
    batch = _apply_commands(model, commands)
    destination: Path | None = None
    if in_place:
        destination = Path(path)
    elif output:
        destination = Path(output)
    saved = destination is not None and not dry_run
    if saved:
        assert destination is not None
        save_session(batch.model, destination)
    return {
        "ok": True,
        "applied": len(batch.results),
        "saved": saved,
        "output": str(destination) if saved else None,
        "results": [result.to_data() for result in batch.results],
        "log": [format_result(result) for result in batch.results],
        "summary": describe_model(batch.model)["summary"],
    }


def quality_report(
    path: str,
    min_angle: float | None = None,
    max_angle: float | None = None,
    non_orthogonality: float | None = None,
    cell_aspect_ratio: float | None = None,
    cell_growth_ratio: float | None = None,
    interface_size_ratio: float | None = None,
    as_text: bool = False,
) -> dict[str, Any]:
    """Report per-block and per-interface mesh quality heuristics.

    Corner angles are measured from the first mesh cell, so curved edges and
    grading count. Thresholds default to angle 30-150 degrees, non-orthogonality
    65 degrees, cell aspect 100, cell growth 1.3, and interface size jump 2.5.
    These are screening heuristics; check_mesh is the authority.
    """
    model = load_session(path)
    thresholds = QualityThresholds.from_overrides(
        min_angle=min_angle,
        max_angle=max_angle,
        non_orthogonality=non_orthogonality,
        cell_aspect_ratio=cell_aspect_ratio,
        cell_growth_ratio=cell_growth_ratio,
        interface_size_ratio=interface_size_ratio,
    )
    report = assess_quality(model, thresholds)
    if as_text:
        return {"text": format_quality(report)}
    return report.to_data()


def render_session(
    path: str,
    output: str | None = None,
    width: int = 1000,
    height: int = 750,
    zoom: list[str] | None = None,
    margin: float = 0.3,
    highlight: list[str] | None = None,
    bounds: list[float] | None = None,
    preview: bool = False,
    coarsening: int = 1,
    title: str | None = None,
    show_labels: bool = True,
    show_nodes: bool = True,
    show_curves: bool = True,
    show_legend: bool = True,
) -> list:
    """Draw the topology and return the picture (PNG) plus its view bounds.

    Vertex IDs, block IDs, edge cell counts, patch colors, reference curves,
    and spacing links are drawn. ``zoom`` frames the named blocks, edges
    (a-b), or vertices with a relative ``margin``; ``bounds`` gives an explicit
    [xmin, ymin, xmax, ymax] region; ``highlight`` emphasizes entities;
    ``preview`` adds interior mesh lines. When ``output`` ends in .svg or .png
    the file is written as well.
    """
    if width < 50 or height < 50:
        raise ToolFailure("width and height must be at least 50 pixels")
    if coarsening < 1:
        raise ToolFailure("coarsening must be a positive integer")
    if zoom and bounds:
        raise ToolFailure("zoom and bounds are mutually exclusive")
    if bounds is not None and len(bounds) != 4:
        raise ToolFailure("bounds needs four numbers: xmin, ymin, xmax, ymax")
    model = load_session(path)
    options = RenderOptions(
        width=width,
        height=height,
        bounds=tuple(bounds) if bounds else None,  # type: ignore[arg-type]
        show_vertex_ids=show_labels,
        show_block_ids=show_labels,
        show_edge_cells=show_labels,
        show_edge_nodes=show_nodes,
        show_curves=show_curves,
        show_legend=show_legend,
        show_preview=preview,
        preview_coarsening=coarsening,
        title=title,
    )
    if zoom:
        options = RenderOptions(**{
            **options.__dict__,
            "bounds": entity_bounds(model, zoom, margin=margin),
        })
    if highlight:
        options = options_with_highlights(model, options, highlight)

    info: dict[str, Any] = {
        "width": options.width,
        "height": options.height,
        "bounds": list(options.bounds) if options.bounds else None,
    }
    written: Path | None = None
    if output:
        written = render_to_file(model, output, options)
        info["output"] = str(written)
    if written is not None and written.suffix.lower() == ".png":
        png = written.read_bytes()
    else:
        png = render_png_bytes(model, options)
    return [_image(png), info]


def export_block_mesh_dict(
    path: str,
    output: str | None = None,
    case: str | None = None,
) -> dict[str, Any]:
    """Write blockMeshDict to ``output`` or ``case``/system, or return its text."""
    if output and case:
        raise ToolFailure("output and case are mutually exclusive")
    model = load_session(path)
    if case:
        destination = Path(case) / "system" / "blockMeshDict"
        destination.parent.mkdir(parents=True, exist_ok=True)
    elif output:
        destination = Path(output)
    else:
        return {"ok": True, "text": block_mesh_dict(model)}
    write_block_mesh_dict(model, destination)
    return {"ok": True, "output": str(destination), "blocks": len(model.blocks)}


def validate_session(path: str) -> dict[str, Any]:
    """Check that a session loads, is topologically valid, and exports."""
    model = load_session(path)
    model.validate()
    block_mesh_dict(model)
    return {"ok": True, "session": str(path), "summary": describe_model(model)["summary"]}


def check_mesh(
    path: str,
    case_dir: str | None = None,
    keep: bool = False,
    run_checkmesh: bool = True,
    timeout: float = 300.0,
    blockmesh_command: str | None = None,
    checkmesh_command: str | None = None,
    include_logs: bool = False,
) -> dict[str, Any]:
    """Run OpenFOAM blockMesh and checkMesh on the exported dictionary.

    The blockMesh command prefix comes from ``blockmesh_command`` or the
    BLOCKMESH_COMMAND environment variable; the checkMesh prefix is derived
    from it unless given. ``case_dir`` keeps the case there; otherwise a
    temporary case is used and deleted unless ``keep``. The result is ok only
    when blockMesh succeeds and checkMesh prints "Mesh OK".
    """
    model = load_session(path)
    result = run_mesh_check(
        model,
        blockmesh_command=(
            split_command_line(blockmesh_command) if blockmesh_command else None
        ),
        checkmesh_command=(
            split_command_line(checkmesh_command) if checkmesh_command else None
        ),
        case_dir=case_dir,
        keep=keep,
        run_checkmesh=run_checkmesh,
        timeout=timeout,
    )
    data = result.to_data()
    data["text"] = format_mesh_check(result)
    if include_logs:
        data["block_mesh"]["log"] = result.block_mesh.log
        if result.check_mesh is not None:
            data["check_mesh"]["log"] = result.check_mesh.log
    return data


TOOLS: tuple[tuple[Callable[..., Any], bool], ...] = (
    # (function, read_only)
    (describe_session, True),
    (list_commands, True),
    (quality_report, True),
    (render_session, False),
    (validate_session, True),
    (edit_session, False),
    (new_session, False),
    (export_block_mesh_dict, False),
    (check_mesh, False),
)


def _image(png: bytes) -> Any:
    from mcp.server.mcpserver import Image

    return Image(data=png, format="png")


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------


def build_server() -> Any:
    """Create the MCP server with every tool registered."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "The MCP server needs the mcp package: python -m pip install 'mcp>=2,<3' "
            "(or pip install blockdrawer[mcp])"
        ) from exc

    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, log_level="WARNING")
    for function, read_only in TOOLS:
        server.add_tool(
            _guarded(function, ToolError),
            annotations=ToolAnnotations(
                read_only_hint=read_only,
                destructive_hint=not read_only,
                open_world_hint=False,
            ),
            # The picture is returned as image content, not JSON.
            structured_output=False if function is render_session else None,
        )
    return server


def _guarded(function: Callable[..., Any], tool_error: type[Exception]) -> Callable[..., Any]:
    """Translate BlockDrawer failures into clean MCP tool errors."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except (ToolFailure, CliError) as exc:
            raise tool_error(str(exc)) from exc
        except HANDLED_ERRORS as exc:
            raise tool_error(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="blockdrawer-mcp",
        description="Serve BlockDrawer session tools over the Model Context Protocol.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="MCP transport (default: stdio, which Claude Code and most clients use)",
    )
    args = parser.parse_args(argv)
    try:
        server = build_server()
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
