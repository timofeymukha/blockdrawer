"""Run OpenFOAM's ``blockMesh`` and ``checkMesh`` on a model and parse the logs.

BlockDrawer never meshes; this module closes the loop by handing the exported
dictionary to a real OpenFOAM installation and reading back what it says. The
installation is reached through a command prefix, the same ``BLOCKMESH_COMMAND``
convention the integration tests use, so a container wrapper works as well as
a sourced local installation.

checkMesh writes the faces and cells it objects to as sets; while the case
still exists they are read back (:mod:`blockdrawer.polymesh`) and mapped to
the blocks that own them, worst block first, with where they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Sequence

from .command_line import split_command_line
from .foam import write_block_mesh_dict
from .model import MeshModel
from .overlap import overlapping_blocks
from .polymesh import PolyMeshError, SetReport, locate_sets, written_set_names


BLOCKMESH_ENV = "BLOCKMESH_COMMAND"
CHECKMESH_ENV = "CHECKMESH_COMMAND"
DEFAULT_TIMEOUT_SECONDS = 300.0

CONTROL_DICT = """FoamFile
{
    format ascii;
    class dictionary;
    object controlDict;
}
application blockMesh;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 1;
deltaT 1;
writeControl timeStep;
writeInterval 1;
writeFormat ascii;
writeCompression off;
"""

FV_SCHEMES = """FoamFile
{
    format ascii;
    class dictionary;
    object fvSchemes;
}
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
"""

FV_SOLUTION = """FoamFile
{
    format ascii;
    class dictionary;
    object fvSolution;
}
solvers {}
"""


class CheckMeshError(RuntimeError):
    """Raised when OpenFOAM cannot be run at all."""


@dataclass(frozen=True)
class FoamRun:
    """One executed OpenFOAM application and its complete log."""

    application: str
    command: tuple[str, ...]
    returncode: int
    log: str
    log_path: str | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and "FOAM FATAL" not in self.log


@dataclass(frozen=True)
class MeshCheckResult:
    """Parsed outcome of running blockMesh and optionally checkMesh."""

    case_dir: str
    kept: bool
    block_mesh: FoamRun
    check_mesh: FoamRun | None
    mesh_stats: dict[str, int] = field(default_factory=dict)
    patches: tuple[dict[str, Any], ...] = ()
    geometry: dict[str, Any] = field(default_factory=dict)
    failed_checks: int | None = None
    mesh_ok: bool | None = None
    problems: tuple[str, ...] = ()
    fatal_errors: tuple[str, ...] = ()
    # Single-star checkMesh lines ("*Number of severely non-orthogonal ...
    # faces: 596."): reported, but not failures, so ``ok`` ignores them.
    warnings: tuple[str, ...] = ()
    # checkMesh's sets mapped to blocks, and why that failed when it did.
    sets: tuple[SetReport, ...] = ()
    set_error: str | None = None
    # Blocks of the session lying over one another
    # (``overlap.overlapping_blocks``): checkMesh does not fail two cells
    # over one place, so they are reported beside its verdict.
    overlaps: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        if not self.block_mesh.ok:
            return False
        if self.check_mesh is None:
            return True
        return self.check_mesh.ok and bool(self.mesh_ok)

    def to_data(self, *, worst: int | None = None) -> dict[str, Any]:
        """Structured result; ``worst`` lists at most N blocks per set."""
        return {
            "ok": self.ok,
            "case_dir": self.case_dir,
            "kept": self.kept,
            "block_mesh": _run_data(self.block_mesh),
            "check_mesh": (
                _run_data(self.check_mesh) if self.check_mesh is not None else None
            ),
            "mesh_stats": dict(self.mesh_stats),
            "patches": [dict(patch) for patch in self.patches],
            "geometry": dict(self.geometry),
            "failed_checks": self.failed_checks,
            "mesh_ok": self.mesh_ok,
            "problems": list(self.problems),
            "fatal_errors": list(self.fatal_errors),
            "warnings": list(self.warnings),
            "severe_non_orthogonal_faces": self.geometry.get("severe_non_orthogonal_faces"),
            "sets": [report.to_data(worst) for report in self.sets],
            "set_error": self.set_error,
            "overlapping_blocks": list(self.overlaps),
        }


def _run_data(run: FoamRun) -> dict[str, Any]:
    return {
        "application": run.application,
        "command": list(run.command),
        "returncode": run.returncode,
        "ok": run.ok,
        "log_path": run.log_path,
        "log_lines": len(run.log.splitlines()),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def blockmesh_command_from_env() -> list[str] | None:
    value = os.environ.get(BLOCKMESH_ENV)
    return split_command_line(value) if value else None


def checkmesh_command_from_env() -> list[str] | None:
    value = os.environ.get(CHECKMESH_ENV)
    return split_command_line(value) if value else None


def derive_checkmesh_command(blockmesh_command: Sequence[str]) -> list[str] | None:
    """Turn a blockMesh command prefix into the matching checkMesh prefix.

    Every ``blockMesh`` word in the prefix is replaced, including words nested
    inside a quoted shell wrapper such as ``bash -lc '... exec blockMesh
    "$@"' blockMesh``. ``None`` means the prefix did not mention blockMesh
    and no guess is possible.
    """
    pattern = re.compile(r"\bblockMesh\b")
    derived = [pattern.sub("checkMesh", token) for token in blockmesh_command]
    if derived == list(blockmesh_command):
        return None
    return derived


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def prepare_case(model: MeshModel, case_dir: str | Path) -> Path:
    """Write a minimal OpenFOAM case containing the model's blockMeshDict."""
    case = Path(case_dir)
    system = case / "system"
    system.mkdir(parents=True, exist_ok=True)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    (system / "controlDict").write_text(CONTROL_DICT, encoding="utf-8")
    (system / "fvSchemes").write_text(FV_SCHEMES, encoding="utf-8")
    (system / "fvSolution").write_text(FV_SOLUTION, encoding="utf-8")
    write_block_mesh_dict(model, system / "blockMeshDict")
    return case


def run_mesh_check(
    model: MeshModel,
    *,
    blockmesh_command: Sequence[str] | None = None,
    checkmesh_command: Sequence[str] | None = None,
    case_dir: str | Path | None = None,
    keep: bool = False,
    run_checkmesh: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    locate: bool = True,
) -> MeshCheckResult:
    """Export ``model``, run blockMesh, then checkMesh, and parse both logs.

    ``case_dir`` is kept when given. Otherwise a temporary case is created
    and deleted afterwards unless ``keep`` is true. Logs are stored in the case
    as ``log.blockMesh`` and ``log.checkMesh`` and are also returned in full.
    With ``locate`` the sets checkMesh wrote are mapped to blocks before a
    temporary case is deleted; a failure there is reported in ``set_error``
    and never changes the verdict.
    """
    block_command = list(blockmesh_command or blockmesh_command_from_env() or ())
    if not block_command:
        raise CheckMeshError(
            f"No blockMesh command; set {BLOCKMESH_ENV} to a command prefix that "
            "accepts -case <path> (for example 'blockMesh' after sourcing "
            "OpenFOAM, or an apptainer/docker wrapper) or pass --blockmesh-command"
        )
    check_command: list[str] | None = None
    if run_checkmesh:
        check_command = list(
            checkmesh_command or checkmesh_command_from_env()
            or derive_checkmesh_command(block_command) or ()
        ) or None

    temporary = case_dir is None
    if temporary:
        case = Path(tempfile.mkdtemp(prefix="blockdrawer-check-"))
    else:
        case = Path(case_dir)
    kept = keep or not temporary
    try:
        prepare_case(model, case)
        block_run = _run_application("blockMesh", block_command, case, timeout)
        check_run: FoamRun | None = None
        if check_command is not None and block_run.ok:
            check_run = _run_application("checkMesh", check_command, case, timeout)
        result = _parse(case, kept, block_run, check_run)
        if locate and check_run is not None:
            result = _with_sets(result, model, case, check_run.log)
        overlaps = overlapping_blocks(model)
        if overlaps:
            result = replace(result, overlaps=tuple(item.describe() for item in overlaps))
    finally:
        if temporary and not kept:
            shutil.rmtree(case, ignore_errors=True)
    return result


def _run_application(
    application: str, prefix: Sequence[str], case: Path, timeout: float
) -> FoamRun:
    command = [*prefix, "-case", str(case)]
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CheckMeshError(
            f"Could not start {application}: {exc}; command was "
            f"{shlex.join(command)}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckMeshError(
            f"{application} exceeded {timeout:g} s: {shlex.join(command)}"
        ) from exc
    log_path = case / f"log.{application}"
    try:
        log_path.write_text(completed.stdout, encoding="utf-8")
        stored = str(log_path)
    except OSError:  # pragma: no cover - unusual filesystem failure
        stored = None
    return FoamRun(
        application, tuple(command), completed.returncode, completed.stdout, stored
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def parse_block_mesh_log(log: str) -> tuple[dict[str, int], tuple[dict[str, Any], ...]]:
    """Extract mesh counts and patch sizes from a blockMesh log."""
    stats: dict[str, int] = {}
    for key, name in (
        ("nPoints", "points"),
        ("nCells", "cells"),
        ("nFaces", "faces"),
        ("nInternalFaces", "internal_faces"),
    ):
        match = re.search(rf"^\s*{key}:\s*(\d+)", log, re.MULTILINE)
        if match:
            stats[name] = int(match.group(1))
    patches = tuple(
        {
            "index": int(match.group(1)),
            "start": int(match.group(2)),
            "faces": int(match.group(3)),
            "name": match.group(4),
        }
        for match in re.finditer(
            r"^\s*patch\s+(\d+)\s+\(start:\s*(\d+)\s+size:\s*(\d+)\)\s+name:\s*(\S+)",
            log,
            re.MULTILINE,
        )
    )
    return stats, patches


def parse_check_mesh_log(log: str) -> dict[str, Any]:
    """Extract geometry statistics and check verdicts from a checkMesh log."""
    geometry: dict[str, Any] = {}

    def number(pattern: str, key: str, group: int = 1) -> None:
        match = re.search(pattern, log)
        if match:
            geometry[key] = float(match.group(group))

    number(rf"Max aspect ratio = ({_NUMBER})", "max_aspect_ratio")
    match = re.search(
        rf"Mesh non-orthogonality Max: ({_NUMBER}) average: ({_NUMBER})", log
    )
    if match:
        geometry["max_non_orthogonality"] = float(match.group(1))
        geometry["average_non_orthogonality"] = float(match.group(2))
    number(rf"Max skewness = ({_NUMBER})", "max_skewness")
    number(rf"Max cell openness = ({_NUMBER})", "max_cell_openness")
    match = re.search(
        rf"Min volume = ({_NUMBER})\. Max volume = ({_NUMBER})\.\s+"
        rf"Total volume = ({_NUMBER})",
        log,
    )
    if match:
        geometry["min_volume"] = float(match.group(1))
        geometry["max_volume"] = float(match.group(2))
        geometry["total_volume"] = float(match.group(3))
    match = re.search(
        rf"Minimum face area = ({_NUMBER})\. Maximum face area = ({_NUMBER})", log
    )
    if match:
        geometry["min_face_area"] = float(match.group(1))
        geometry["max_face_area"] = float(match.group(2))
    match = re.search(
        rf"Overall domain bounding box \(({_NUMBER}) ({_NUMBER}) ({_NUMBER})\) "
        rf"\(({_NUMBER}) ({_NUMBER}) ({_NUMBER})\)",
        log,
    )
    if match:
        geometry["bounding_box"] = [float(match.group(index)) for index in range(1, 7)]
    match = re.search(r"Number of regions: (\d+)", log)
    if match:
        geometry["regions"] = int(match.group(1))
    match = re.search(r"Mesh has (\d+) solution \(non-empty\) directions", log)
    if match:
        geometry["solution_directions"] = int(match.group(1))

    stats: dict[str, int] = {}
    for label, key in (
        ("points", "points"),
        ("internal points", "internal_points"),
        ("faces", "faces"),
        ("internal faces", "internal_faces"),
        ("cells", "cells"),
        ("boundary patches", "boundary_patches"),
    ):
        match = re.search(rf"^\s*{re.escape(label)}:\s*(\d+)", log, re.MULTILINE)
        if match:
            stats[key] = int(match.group(1))
    geometry["mesh_stats"] = stats

    failed = re.search(r"Failed (\d+) mesh checks", log)
    geometry["failed_checks"] = int(failed.group(1)) if failed else None
    if re.search(r"^\s*Mesh OK\.", log, re.MULTILINE):
        geometry["mesh_ok"] = True
    elif failed:
        geometry["mesh_ok"] = False
    else:
        geometry["mesh_ok"] = None
    match = re.search(
        rf"\*Number of severely non-orthogonal \(> ({_NUMBER}) degrees\) faces: (\d+)",
        log,
    )
    if match:
        geometry["severe_non_orthogonality_threshold"] = float(match.group(1))
        geometry["severe_non_orthogonal_faces"] = int(match.group(2))
    elif "max_non_orthogonality" in geometry:
        # checkMesh prints the count only when it is not zero.
        geometry["severe_non_orthogonal_faces"] = 0
    geometry["problems"] = [
        line.strip().lstrip("*").strip()
        for line in log.splitlines()
        if line.lstrip().startswith("***")
    ]
    geometry["warnings"] = [
        line.strip().lstrip("*").strip()
        for line in log.splitlines()
        if line.lstrip().startswith("*") and not line.lstrip().startswith("***")
    ]
    return geometry


def fatal_errors(log: str) -> tuple[str, ...]:
    """Return every FOAM FATAL ERROR message body in ``log``."""
    errors: list[str] = []
    lines = log.splitlines()
    index = 0
    while index < len(lines):
        if "FOAM FATAL" in lines[index]:
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() \
                    and not lines[index].lstrip().startswith("From "):
                body.append(lines[index].strip())
                index += 1
            errors.append(" ".join(body) if body else lines[index - 1].strip())
        index += 1
    return tuple(errors)


def _parse(
    case: Path, kept: bool, block_run: FoamRun, check_run: FoamRun | None
) -> MeshCheckResult:
    stats, patches = parse_block_mesh_log(block_run.log)
    errors = list(fatal_errors(block_run.log))
    geometry: dict[str, Any] = {}
    failed_checks: int | None = None
    mesh_ok: bool | None = None
    problems: list[str] = []
    warnings: list[str] = []
    if check_run is not None:
        parsed = parse_check_mesh_log(check_run.log)
        problems = parsed.pop("problems")
        warnings = parsed.pop("warnings")
        failed_checks = parsed.pop("failed_checks")
        mesh_ok = parsed.pop("mesh_ok")
        check_stats = parsed.pop("mesh_stats")
        for key, value in check_stats.items():
            stats.setdefault(key, value)
        geometry = parsed
        errors.extend(fatal_errors(check_run.log))
        if not check_run.ok and mesh_ok is None:
            mesh_ok = False
    return MeshCheckResult(
        str(case),
        kept,
        block_run,
        check_run,
        stats,
        patches,
        geometry,
        failed_checks,
        mesh_ok,
        tuple(problems),
        tuple(dict.fromkeys(errors)),
        tuple(warnings),
    )


def _with_sets(
    result: MeshCheckResult, model: MeshModel, case: Path, log: str
) -> MeshCheckResult:
    names = written_set_names(log)
    if not names:
        return result
    try:
        sets = locate_sets(case, model, names)
    except (PolyMeshError, OSError, ValueError, IndexError) as exc:
        return replace(result, set_error=f"could not map checkMesh sets to blocks: {exc}")
    return replace(result, sets=sets)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_mesh_check(result: MeshCheckResult, *, worst: int = 5) -> str:
    """Terminal text; ``worst`` blocks are listed per checkMesh set."""
    lines: list[str] = []
    block = result.block_mesh
    if result.overlaps:
        lines.append(
            f"BLOCKS OVERLAP ({len(result.overlaps)} pair(s); blockMesh meshes both and checkMesh "
            "does not fail it): " + "; ".join(result.overlaps[:4])
            + (f" (and {len(result.overlaps) - 4} more)" if len(result.overlaps) > 4 else "")
        )
    if block.ok:
        counts = ", ".join(
            f"{result.mesh_stats[key]} {key.replace('_', ' ')}"
            for key in ("cells", "points", "faces", "internal_faces")
            if key in result.mesh_stats
        )
        lines.append(f"blockMesh: OK ({counts})")
        if result.patches:
            lines.append("  patches: " + ", ".join(
                f"{patch['name']} ({patch['faces']} faces)" for patch in result.patches
            ))
    else:
        lines.append(f"blockMesh: FAILED (exit code {block.returncode})")
    check = result.check_mesh
    if check is None:
        if block.ok:
            lines.append("checkMesh: not run")
    else:
        verdict = (
            "Mesh OK" if result.mesh_ok else
            f"failed {result.failed_checks} check(s)" if result.failed_checks
            else "FAILED"
        )
        lines.append(f"checkMesh: {verdict} (exit code {check.returncode})")
        geometry = result.geometry
        severe = geometry.get("severe_non_orthogonal_faces")
        if severe is not None:
            threshold = geometry.get("severe_non_orthogonality_threshold", 70.0)
            lines.append(
                f"  severely non-orthogonal faces (> {threshold:g} degrees): {severe}"
                + (f" of {result.mesh_stats['internal_faces']} internal"
                   if severe and "internal_faces" in result.mesh_stats else "")
            )
        details = []
        if "max_non_orthogonality" in geometry:
            details.append(
                f"non-orthogonality max {geometry['max_non_orthogonality']:.4g}, "
                f"average {geometry['average_non_orthogonality']:.4g}"
            )
        if "max_skewness" in geometry:
            details.append(f"max skewness {geometry['max_skewness']:.4g}")
        if "max_aspect_ratio" in geometry:
            details.append(f"max aspect ratio {geometry['max_aspect_ratio']:.4g}")
        if "min_volume" in geometry:
            details.append(
                f"cell volume {geometry['min_volume']:.4g} .. "
                f"{geometry['max_volume']:.4g} (total {geometry['total_volume']:.4g})"
            )
        if "solution_directions" in geometry:
            details.append(f"{geometry['solution_directions']} solution direction(s)")
        if details:
            lines.append("  " + "; ".join(details))
        for problem in result.problems:
            lines.append(f"  *** {problem}")
        for warning in result.warnings:
            if severe is not None and warning.startswith("Number of severely non-orthogonal"):
                continue  # already the headline line above
            lines.append(f"  * {warning}")
        for report in result.sets:
            lines.extend(_format_set(report, worst))
        if result.set_error:
            lines.append(f"  sets: {result.set_error}")
    for error in result.fatal_errors:
        lines.append(f"  FATAL: {error}")
    location = "kept at" if result.kept else "deleted from"
    lines.append(f"case {location} {result.case_dir}")
    if result.kept:
        for run in (block, check):
            if run is not None and run.log_path:
                lines.append(f"  log: {run.log_path}")
    return "\n".join(lines) + "\n"


def _format_set(report: SetReport, worst: int) -> list[str]:
    noun = {"face": "faces", "cell": "cells", "point": "points"}.get(report.kind, "items")
    head = (
        f"  {report.name}: {report.count} {noun} in {len(report.blocks)} block(s)"
    )
    if report.max_non_orthogonality is not None:
        head += f", max {report.max_non_orthogonality:.4g} degrees"
    if report.unmapped:
        head += f", {report.unmapped} not mapped to a block"
    shown = report.blocks[: max(0, worst)]
    lines = [head + (", worst first:" if shown else "")]
    for hits in shown:
        x0, y0, x1, y1 = hits.bounds
        detail = f"    {hits.block}: {hits.count}"
        if hits.max_non_orthogonality is not None:
            detail += f" (max {hits.max_non_orthogonality:.4g} deg)"
        detail += (
            f" at ({hits.centroid[0]:.6g}, {hits.centroid[1]:.6g}),"
            f" bounds {x0:.6g} {y0:.6g} {x1:.6g} {y1:.6g}"
        )
        lines.append(detail)
    if len(report.blocks) > len(shown):
        lines.append(f"    ... {len(report.blocks) - len(shown)} more block(s)")
    return lines
