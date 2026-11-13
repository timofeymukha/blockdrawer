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
    batch_digest,
    command_specs,
    format_result,
    read_command_lines,
)
from .describe import (
    QUERY_SECTIONS,
    SECTIONS,
    describe_model,
    format_description,
    query_description,
)
from .foam import block_mesh_dict, write_block_mesh_dict
from .model import MeshModel
from .quality import (
    DEFAULT_INTERIOR_CELLS,
    DEFAULT_MAX_WARNINGS,
    QualityThresholds,
    assess_quality,
    format_quality,
)
from .render import (
    RenderOptions,
    entity_bounds,
    options_with_highlights,
    render_png_bytes,
    render_to_file,
)
from .session import load_session, save_session


SERVER_NAME = "blockdrawer"

# An unfiltered text description longer than this is cut to the summary and
# the patches, with a note on how to ask for the rest.
COMPACT_DESCRIPTION_CHARACTERS = 12000
# Text quality reports list this many worst blocks and internal edges unless
# asked otherwise.
DEFAULT_WORST = 10

INSTRUCTIONS = """\
BlockDrawer edits 2D block topologies for OpenFOAM blockMesh. A session is a
JSON file; every tool takes its path, so several agents or a person in the GUI
can share one file. Mechanics:

Read tools answer in compact text by default (as_text=false gives the full
JSON); ask for the part you need rather than the whole session.

1. describe_session for block, vertex, edge and patch IDs. Edges are written
   first-second in either order, e.g. v0-v1; a grading value or a split
   fraction is read along the edge in the order written (start_width at the
   first vertex). Narrow it with only=[IDs] (a named edge lists all its
   control points), bbox=[xmin, ymin, xmax, ymax], component=EDGE (the
   edges sharing its cell count) or unassigned=true (exterior edges with no
   patch, as chains).
2. edit_session with a list of commands ("set_edge_cells v0-v1 40",
   "add_block v1-v2", "set_edge_grading v0-v1 start_width 0.002",
   "set_edge_type v2-v3 arc", "split_edge v1-v2 0.5", "smooth",
   "repair_block b12",
   "add_boundary inlet",
   "set_edge_boundary v0-v3 inlet") or with commands_file pointing at a
   generated batch (one command per line, # comments, JSON objects allowed).
   list_commands documents every command. Batches are atomic: nothing is
   saved when any command fails. The answer counts the commands and lists
   the IDs created and removed and the results worth reading (repairs,
   smoothing, splits, fits); detail=true returns every command's result.
   To improve a finished topology run "smooth" first (moves every interior
   vertex, keeps wall bands, cell sizes and the worst face; applied only
   when checkMesh's measure gets no worse), then "repair_block" on the
   blocks that remain bad.
3. quality_report: a verdict (valid, untangled, worst non-orthogonality and
   faces above 70 degrees, measured as checkMesh does on the block
   interiors sampled at the real counts), the worst blocks and edges, and
   the worst warnings - corner angles, aspect ratios, growth, size jumps.
4. render_session to look, with zoom and highlight for trailing edges,
   corners and gaps.
5. check_mesh runs blockMesh and checkMesh when OpenFOAM is available; it is
   the authority. It reports the severely non-orthogonal face count and maps
   checkMesh's bad-face and bad-cell sets to blocks, worst first, with a
   location to render. export_block_mesh_dict writes the dictionary into a
   case.

How to choose and build a topology - O, C or H grids, wall bands, wakes,
far-field placement, sizing from a target y+, the validation ladder and the
known failure signatures - is the repository skill
.claude/skills/mesh-blocking/SKILL.md. Read it before building or judging a
mesh. Pseudo-2D cases need both z patches of type empty:
set_export_settings z_min_patch_type=empty z_max_patch_type=empty.
"""


class ToolFailure(Exception):
    """A clean, user-facing tool failure (translated to MCP ToolError)."""


# ---------------------------------------------------------------------------
# Tool implementations (plain functions, usable without the mcp package)
# ---------------------------------------------------------------------------


def new_session(
    path: str, force: bool = False, box: list[float] | None = None
) -> dict[str, Any]:
    """Create a fresh single-block session file at ``path``.

    ``box`` places the first block as [xmin, ymin, xmax, ymax] (default:
    the unit square), so a hand layout starts from the block it wants.
    """
    destination = Path(path)
    if destination.exists() and not force:
        raise ToolFailure(f"{destination} already exists; pass force=true to replace it")
    if box is not None and len(box) != 4:
        raise ToolFailure("box needs four numbers: xmin, ymin, xmax, ymax")
    model = MeshModel(box=tuple(box) if box else None)  # type: ignore[arg-type]
    save_session(model, destination)
    return {"ok": True, "session": str(destination), "summary": describe_model(model)["summary"]}


def describe_session(
    path: str,
    sections: list[str] | None = None,
    only: list[str] | None = None,
    as_text: bool = True,
    bbox: list[float] | None = None,
    component: str | None = None,
    unassigned: bool = False,
) -> dict[str, Any]:
    """Describe blocks, edges, vertices, patches, curves, and spacing links.

    ``sections`` limits the output to some of: summary, settings, blocks,
    edges, vertices, boundaries, curves, spacing_links. ``only`` restricts the
    lists to the given block, edge, vertex, patch, or curve IDs (a named
    edge lists all its control points). ``bbox`` [xmin, ymin, xmax, ymax]
    keeps the blocks overlapping the box and the vertices in it;
    ``component`` (an edge) keeps the edges whose cell count must equal it
    and their blocks; ``unassigned`` lists the exterior edges without a
    patch as chains. The answer is compact text by default - for a large
    unfiltered session only the summary and patches, with a note on how to
    narrow it; ``as_text=false`` returns the structured data in full.
    """
    model = load_session(path)
    if bbox is not None and len(bbox) != 4:
        raise ToolFailure("bbox needs four numbers: xmin, ymin, xmax, ymax")
    data = query_description(
        model, only=only, bbox=bbox, component=component, unassigned=unassigned
    )
    wanted = tuple(sections) if sections else None
    if wanted:
        unknown = [name for name in wanted if name not in SECTIONS]
        if unknown:
            raise ToolFailure(
                f"Unknown section(s) {', '.join(unknown)}; choose from {', '.join(SECTIONS)}"
            )
    if as_text:
        text = format_description(data, sections=wanted)
        narrowed = wanted or only or bbox is not None or component or unassigned
        if not narrowed and len(text) > COMPACT_DESCRIPTION_CHARACTERS:
            summary = data["summary"]
            text = format_description(data, sections=("summary", "boundaries")) + (
                f"\n(The full listing of {summary['blocks']} blocks, {summary['edges']} "
                f"edges and {summary['vertices']} vertices is {len(text)} characters; "
                "narrow it with only=[IDs], bbox=[xmin, ymin, xmax, ymax], "
                "component=EDGE, unassigned=true or sections=[...], or pass "
                "as_text=false for all of it as JSON.)\n"
            )
        return {"text": text}
    if wanted:
        data = {key: data[key] for key in (*wanted, *QUERY_SECTIONS) if key in data}
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
        "edge_notation": (
            "first-second in either order, e.g. v0-v1; a grading value or split "
            "fraction is read along the edge in the order written"
        ),
    }


def edit_session(
    path: str,
    commands: list[str | dict[str, Any]] | None = None,
    output: str | None = None,
    in_place: bool = False,
    dry_run: bool = False,
    commands_file: str | None = None,
    detail: bool = False,
) -> dict[str, Any]:
    """Apply editing commands atomically and save the result.

    ``commands`` are text lines ("set_edge_cells v0-v1 20") or objects
    ({"op": "add_block", "edge": ["v1", "v2"]}). ``commands_file`` names a
    batch file - one command per line, ``#`` comments, JSON objects allowed,
    the same format ``blockdrawer-cli apply -f`` reads - which is applied
    before any inline ``commands``; a generated topology of thousands of
    commands belongs in a file, not in the call. Pass ``in_place=true`` to
    overwrite ``path`` or ``output`` to write elsewhere; with neither, or with
    ``dry_run``, the result is reported but not saved. If any command fails
    nothing is written and the error names the failing step and repeats the
    command. The answer counts the commands by name, lists the vertex,
    block, curve and patch IDs created and removed (runs written
    ``first..last``) and the results worth reading - repairs, splits,
    merges, fits, spacing links left out of sync; ``detail=true`` adds every
    command's full result and log line.
    """
    if output and in_place:
        raise ToolFailure("output and in_place are mutually exclusive")
    batch_commands: list[Any] = []
    if commands_file:
        source = Path(commands_file)
        if not source.is_file():
            raise ToolFailure(f"commands_file {source} does not exist")
        batch_commands.extend(read_command_lines(source.read_text(encoding="utf-8")))
    batch_commands.extend(commands or [])
    if not batch_commands:
        raise ToolFailure("No commands given")
    model = load_session(path)
    batch = _apply_commands(model, batch_commands)
    destination: Path | None = None
    if in_place:
        destination = Path(path)
    elif output:
        destination = Path(output)
    saved = destination is not None and not dry_run
    if saved:
        assert destination is not None
        save_session(batch.model, destination)
    data: dict[str, Any] = {
        "ok": True,
        "applied": len(batch.results),
        "saved": saved,
        "output": str(destination) if saved else None,
        "summary": describe_model(batch.model)["summary"],
    }
    data.update(batch_digest(model, batch))
    if detail:
        data["results"] = [result.to_data() for result in batch.results]
        data["log"] = [format_result(result) for result in batch.results]
    return data


def quality_report(
    path: str,
    min_angle: float | None = None,
    max_angle: float | None = None,
    non_orthogonality: float | None = None,
    cell_aspect_ratio: float | None = None,
    cell_growth_ratio: float | None = None,
    interface_size_ratio: float | None = None,
    as_text: bool = True,
    summary_only: bool = False,
    worst: int | None = None,
    interior_cells: int = DEFAULT_INTERIOR_CELLS,
    max_warnings: int | None = None,
) -> dict[str, Any]:
    """Report the verdict, per-block and per-interface mesh quality.

    Every block interior is sampled at its real cell counts (up to
    ``interior_cells`` a side; 0 skips it) with blockMesh's interpolation,
    and each face's non-orthogonality is measured as checkMesh does: the
    ``verdict`` says whether the session is valid and exports, whether any
    sampled cell folds, the worst non-orthogonality and where, and how many
    faces exceed 70 degrees. The corner screen adds first-cell corner
    angles, aspect ratios, growth and size jumps across internal edges;
    check_mesh remains the authority. Thresholds default to angle 30-150
    degrees, non-orthogonality 65 degrees, cell aspect 100, cell growth 1.3,
    and interface size jump 2.5. The answer is compact text by default: the
    ``worst`` (default 10) blocks - by folded cells, then sampled
    non-orthogonality - and internal edges, and the ``max_warnings``
    (default 20) worst warnings. ``as_text=false`` returns structured data,
    complete unless ``worst``/``max_warnings`` are given; ``summary_only``
    drops the tables.
    """
    if worst is not None and worst < 0:
        raise ToolFailure("worst must be a non-negative count")
    if max_warnings is not None and max_warnings < 0:
        raise ToolFailure("max_warnings must be a non-negative count")
    if interior_cells < 0:
        raise ToolFailure("interior_cells must be a non-negative count")
    model = load_session(path)
    thresholds = QualityThresholds.from_overrides(
        min_angle=min_angle,
        max_angle=max_angle,
        non_orthogonality=non_orthogonality,
        cell_aspect_ratio=cell_aspect_ratio,
        cell_growth_ratio=cell_growth_ratio,
        interface_size_ratio=interface_size_ratio,
    )
    report = assess_quality(model, thresholds, interior_cells=interior_cells)
    if as_text:
        return {"text": format_quality(
            report,
            summary_only=summary_only,
            worst=DEFAULT_WORST if worst is None else worst,
            max_warnings=DEFAULT_MAX_WARNINGS if max_warnings is None else max_warnings,
        )}
    return report.to_data(summary_only=summary_only, worst=worst, max_warnings=max_warnings)


def render_session(
    path: str,
    output: str | None = None,
    width: int = 1000,
    height: int | None = None,
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
    return_image: bool = True,
) -> list:
    """Draw the topology and return the picture (PNG) plus its view bounds.

    Vertex IDs, block IDs, edge cell counts, patch colors, reference curves,
    and spacing links are drawn. ``zoom`` frames the named blocks, edges
    (a-b), or vertices with a relative ``margin``; ``bounds`` gives an explicit
    [xmin, ymin, xmax, ymax] region; ``highlight`` emphasizes entities;
    ``preview`` adds interior mesh lines. Without ``height`` the canvas
    follows the shown region's aspect ratio (between a quarter and twice
    the width), so a long channel is a wide strip. When ``output`` ends in
    .svg or .png the file is written as well. Pass ``return_image=false``
    with ``output`` to write a deliverable picture without receiving it
    inline.
    """
    if width < 50 or (height is not None and height < 50):
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
    if not return_image:
        if written is None:
            raise ToolFailure("return_image=false needs an output file to write")
        return [info]
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
    worst: int = 10,
    locate: bool = True,
) -> dict[str, Any]:
    """Run OpenFOAM blockMesh and checkMesh on the exported dictionary.

    The blockMesh command prefix comes from ``blockmesh_command`` or the
    BLOCKMESH_COMMAND environment variable; the checkMesh prefix is derived
    from it unless given. ``case_dir`` keeps the case there; otherwise a
    temporary case is used and deleted unless ``keep``. The result is ok only
    when blockMesh succeeds and checkMesh prints "Mesh OK" - a mesh can be OK
    with hundreds of faces above 70 degrees, so read
    ``severe_non_orthogonal_faces`` and ``warnings`` (checkMesh's
    single-star lines) too. ``sets`` maps each set checkMesh wrote
    (nonOrthoFaces, skewFaces, wrongOrientedFaces, ...) to the blocks owning
    its faces or cells, worst block first, with each block's count,
    centroid, bounds (for render_session's ``bounds``) and, for
    nonOrthoFaces, its worst face's non-orthogonality; ``worst`` caps the
    blocks listed per set and ``locate=false`` skips the mapping.
    """
    if worst < 0:
        raise ToolFailure("worst must be a non-negative count")
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
        locate=locate,
    )
    data = result.to_data(worst=worst)
    data["text"] = format_mesh_check(result, worst=worst)
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
