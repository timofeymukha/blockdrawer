"""Server-side session state for the browser editor.

``WebSession`` mirrors the responsibilities of the Tk ``BlockDrawerApp`` that
do not belong to a window: the model, undo/redo history, the session file,
preferences, recent files, and export. The browser client keeps the viewport,
selection, and editing modes. Every mutation runs under one lock and bumps a
version counter that the event stream uses to notify clients.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any

from ..commands import (
    CommandError,
    apply_command,
    edge_text,
    export_settings_data,
    parse_edge,
)
from ..config import (
    AppConfig,
    ConfigError,
    MAX_RECENT_FILES,
    default_config,
    default_config_path,
    load_config,
    save_config,
)
from ..domain import EdgeKey, TopologyError, edge_key
from ..foam import write_block_mesh_dict
from ..history import ModelHistory
from ..model import MeshModel
from ..session import SessionError, load_session, save_session
from ..ui_helpers import (
    CURVE_RENDER_SEGMENTS,
    GEOMETRY_SAMPLES_PER_SPAN,
    MAX_INDIVIDUAL_EDGE_NODE_MARKERS,
    MIN_SPLIT_FRACTION,
    SPLINE_SAMPLES_PER_SPAN,
    nearest_edge_fraction,
)


DRAG_KINDS = ("vertex", "control_point", "geometry_point")


class WebSessionError(ValueError):
    """A user-facing failure of a session operation."""


class WebSession:
    """The editable document behind one browser editor instance."""

    def __init__(
        self,
        session_path: str | Path | None = None,
        *,
        config_path: str | Path | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self._changed = threading.Condition(self.lock)
        self.version = 0
        self.model = MeshModel()
        self.history = ModelHistory(self.model)
        self.session_path: Path | None = None
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.config_warning: str | None = None
        self.config_write_enabled = True
        self.preferences = self._load_preferences()
        self._disk_mtime: float | None = None
        self._drag_changed = False
        if session_path is not None:
            self.open_session(session_path)

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def _load_preferences(self) -> AppConfig:
        defaults = default_config()
        try:
            if self.config_path.exists():
                return load_config(self.config_path)
            save_config(defaults, self.config_path)
            return defaults
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            self.config_warning = (
                f"Could not use preferences at {self.config_path}: {exc}. "
                "Using defaults for this run."
            )
            return defaults

    def _save_preferences(self) -> str | None:
        if not self.config_write_enabled:
            return (
                "Preferences were not saved because the config could not be "
                "loaded at startup."
            )
        try:
            save_config(self.preferences, self.config_path)
        except (OSError, ConfigError) as exc:
            self.config_write_enabled = False
            return f"Could not save {self.config_path}: {exc}"
        return None

    def set_visibility(self, **flags: bool) -> str | None:
        with self.lock:
            current = {
                "show_block_mesh": self.preferences.show_block_mesh,
                "show_geometry": self.preferences.show_geometry,
                "show_vertex_ids": self.preferences.show_vertex_ids,
                "show_edge_cell_counts": self.preferences.show_edge_cell_counts,
                "show_edge_nodes": self.preferences.show_edge_nodes,
                "show_edge_interpolation_points":
                    self.preferences.show_edge_interpolation_points,
                "show_mesh_preview": self.preferences.show_mesh_preview,
            }
            unknown = set(flags) - set(current)
            if unknown:
                raise WebSessionError(
                    f"Unknown visibility flag(s): {', '.join(sorted(unknown))}"
                )
            current.update({key: bool(value) for key, value in flags.items()})
            try:
                self.preferences = self.preferences.with_visibility(**current)
            except ConfigError as exc:
                raise WebSessionError(str(exc)) from exc
            warning = self._save_preferences()
            self._bump()
            return warning

    def set_preview_coarsening(self, value: int) -> str | None:
        with self.lock:
            try:
                self.preferences = self.preferences.with_preview_coarsening(value)
            except ConfigError as exc:
                raise WebSessionError(str(exc)) from exc
            warning = self._save_preferences()
            self._bump()
            return warning

    def set_ui_scale(self, value: str | float) -> str | None:
        with self.lock:
            try:
                self.preferences = self.preferences.with_ui_scale(value)
            except ConfigError as exc:
                raise WebSessionError(str(exc)) from exc
            warning = self._save_preferences()
            self._bump()
            return warning

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_path(path: str | Path) -> Path:
        source = Path(path).expanduser()
        try:
            return source.resolve()
        except OSError:
            return source.absolute()

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(path))

    def _record_recent(self, path: Path) -> str | None:
        source = self._canonical_path(path)
        ordered = [str(source)]
        seen = {self._path_key(source)}
        for value in self.preferences.recent_files:
            existing = self._canonical_path(value)
            key = self._path_key(existing)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(str(existing))
            if len(ordered) >= MAX_RECENT_FILES:
                break
        return self._set_recent(tuple(ordered))

    def remove_recent(self, path: str | Path) -> str | None:
        with self.lock:
            key = self._path_key(self._canonical_path(path))
            remaining = tuple(
                value for value in self.preferences.recent_files
                if self._path_key(self._canonical_path(value)) != key
            )
            return self._set_recent(remaining)

    def clear_recent(self) -> str | None:
        with self.lock:
            return self._set_recent(())

    def _set_recent(self, paths: tuple[str, ...]) -> str | None:
        if paths == self.preferences.recent_files:
            return None
        self.preferences = self.preferences.with_recent_files(paths)
        warning = self._save_preferences()
        self._bump()
        return warning

    # ------------------------------------------------------------------
    # Change notification
    # ------------------------------------------------------------------

    def _bump(self) -> None:
        self.version += 1
        self._changed.notify_all()

    def wait_for_change(self, known_version: int, timeout: float) -> int:
        """Block until the version differs from ``known_version`` or timeout."""
        with self._changed:
            if self.version == known_version:
                self._changed.wait(timeout)
            return self.version

    def external_change_detected(self) -> bool:
        """Return whether the session file changed on disk since we touched it."""
        with self.lock:
            if self.session_path is None or self._disk_mtime is None:
                return False
            try:
                current = self.session_path.stat().st_mtime
            except OSError:
                return False
            return current != self._disk_mtime

    def _remember_disk_state(self) -> None:
        if self.session_path is None:
            self._disk_mtime = None
            return
        try:
            self._disk_mtime = self.session_path.stat().st_mtime
        except OSError:
            self._disk_mtime = None

    # ------------------------------------------------------------------
    # Session commands
    # ------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self._drag_changed or not self.history.is_at_saved_index()

    def new_session(self) -> None:
        with self.lock:
            self.model = MeshModel()
            self.history.reset(self.model)
            self.session_path = None
            self._disk_mtime = None
            self._drag_changed = False
            self._bump()

    def open_session(self, path: str | Path) -> str | None:
        with self.lock:
            source = self._canonical_path(path)
            try:
                model = load_session(source)
            except SessionError as exc:
                raise WebSessionError(str(exc)) from exc
            self.model = model
            self.history.reset(self.model)
            self.session_path = source
            self._drag_changed = False
            self._remember_disk_state()
            warning = self._record_recent(source)
            self._bump()
            return warning

    def reload_session(self) -> str | None:
        with self.lock:
            if self.session_path is None:
                raise WebSessionError("The session has not been saved to a file yet")
            return self.open_session(self.session_path)

    def save_session(self, path: str | Path | None = None) -> str | None:
        with self.lock:
            destination = (
                self._canonical_path(path) if path is not None else self.session_path
            )
            if destination is None:
                raise WebSessionError("Choose a file name to save the session")
            try:
                save_session(self.model, destination)
            except (OSError, SessionError, TopologyError) as exc:
                raise WebSessionError(str(exc)) from exc
            self.session_path = destination
            self.history.mark_saved(self.model)
            self._remember_disk_state()
            warning = self._record_recent(destination)
            self._bump()
            return warning

    def export_block_mesh_dict(
        self, path: str | Path, settings: dict[str, Any] | None = None
    ) -> None:
        """Apply export settings, write the dictionary, and record the edit.

        The settings only become part of the model when the export succeeds,
        matching the Tk export panel.
        """
        with self.lock:
            previous = (
                self.model.z_cells, self.model.z_min, self.model.z_max,
                self.model.scale, self.model.z_min_patch_name,
                self.model.z_min_patch_type, self.model.z_max_patch_name,
                self.model.z_max_patch_type,
            )
            if settings:
                try:
                    apply_command(
                        self.model, {"op": "set_export_settings", **settings}
                    )
                except CommandError as exc:
                    raise WebSessionError(str(exc)) from exc
            try:
                write_block_mesh_dict(self.model, self._canonical_path(path))
            except (OSError, TopologyError) as exc:
                self.model.set_export_settings(*previous)
                raise WebSessionError(str(exc)) from exc
            self.history.record(self.model)
            self._bump()

    def undo(self) -> bool:
        with self.lock:
            restored = self.history.undo()
            if restored is None:
                return False
            self.model = restored
            self._drag_changed = False
            self._bump()
            return True

    def redo(self) -> bool:
        with self.lock:
            restored = self.history.redo()
            if restored is None:
                return False
            self.model = restored
            self._drag_changed = False
            self._bump()
            return True

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def command(self, op: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply one registry command in place and record it as one edit."""
        with self.lock:
            try:
                result = apply_command(self.model, {"op": op, **(arguments or {})})
            except CommandError as exc:
                raise WebSessionError(str(exc)) from exc
            self.history.record(self.model)
            self._drag_changed = False
            self._bump()
            return result.result

    def drag(self, kind: str, target: Any, x: float, y: float) -> None:
        """Move an entity without recording history (a drag in progress)."""
        with self.lock:
            if kind not in DRAG_KINDS:
                raise WebSessionError(f"Unknown drag kind {kind!r}")
            try:
                if kind == "vertex":
                    self.model.move_vertex(str(target), float(x), float(y))
                elif kind == "control_point":
                    edge_value, index = target
                    self.model.set_edge_control_point(
                        parse_edge(self.model, edge_value), int(index), float(x), float(y)
                    )
                else:
                    curve_id, index = target
                    self.model.set_geometry_curve_point(
                        str(curve_id), int(index), float(x), float(y)
                    )
            except (CommandError, TopologyError, TypeError, ValueError) as exc:
                raise WebSessionError(str(exc)) from exc
            self._drag_changed = True
            self._bump()

    def drag_end(self) -> bool:
        """Record a finished drag as one history entry."""
        with self.lock:
            if not self._drag_changed:
                return False
            self._drag_changed = False
            recorded = self.history.record(self.model)
            self._bump()
            return recorded

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def edge_fraction(self, edge: Any, x: float, y: float) -> dict[str, Any]:
        with self.lock:
            try:
                current = parse_edge(self.model, edge)
            except CommandError as exc:
                raise WebSessionError(str(exc)) from exc
            fraction = nearest_edge_fraction(self.model, current, float(x), float(y))
            fraction = min(1.0 - MIN_SPLIT_FRACTION, max(MIN_SPLIT_FRACTION, fraction))
            return self.split_cells(current, fraction)

    def split_cells(self, edge: Any, fraction: float) -> dict[str, Any]:
        with self.lock:
            try:
                current = parse_edge(self.model, edge)
                first, second = self.model.edge_split_cell_counts(current, float(fraction))
            except (CommandError, TopologyError) as exc:
                raise WebSessionError(str(exc)) from exc
            point = self.model.edge_point(current, float(fraction))
            return {
                "edge": edge_text(current),
                "fraction": float(fraction),
                "first_cells": first,
                "second_cells": second,
                "point": [point[0], point[1]],
            }

    def list_directory(self, path: str | Path | None) -> dict[str, Any]:
        """List a directory for the browser file dialog."""
        if path in (None, ""):
            base = self.session_path.parent if self.session_path else Path.cwd()
        else:
            base = Path(str(path)).expanduser()
        base = self._canonical_path(base)
        if base.is_file():
            base = base.parent
        if not base.is_dir():
            raise WebSessionError(f"{base} is not a directory")
        entries: list[dict[str, Any]] = []
        try:
            for child in sorted(base.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                if child.name.startswith("."):
                    continue
                try:
                    is_dir = child.is_dir()
                    size = 0 if is_dir else child.stat().st_size
                except OSError:
                    continue
                entries.append({"name": child.name, "is_dir": is_dir, "size": size})
        except OSError as exc:
            raise WebSessionError(f"Could not read {base}: {exc}") from exc
        return {
            "dir": str(base),
            "parent": str(base.parent) if base.parent != base else None,
            "home": str(Path.home()),
            "entries": entries,
        }

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return everything the browser needs to draw and edit.

        Every per-edge quantity is computed once here: occurrences,
        constraint components (union-find over opposite block edges), and
        grading values, from which link widths are read directly. Spline
        lengths are therefore sampled once per edge instead of once per link
        endpoint, which keeps drag updates cheap on large topologies. Every
        edge carries all of its graded node positions and fractions so the
        browser can build the mesh preview itself with OpenFOAM's transfinite
        interpolation (see ``preview.py`` for the reference implementation).
        """
        with self.lock:
            model = self.model
            occurrences = model.edge_occurrences()
            edges = model.edges()
            used = {identifier for block in model.blocks for identifier in block.vertices}
            component_sizes = _constraint_component_sizes(model, edges)
            grading = {current: model.edge_grading_values(current) for current in edges}

            def width_at(edge: EdgeKey, vertex: str) -> float:
                values = grading[edge]
                return values.start_width if vertex == edge[0] else values.end_width

            links_by_edge: dict[EdgeKey, list[dict[str, Any]]] = {}
            link_entries: list[dict[str, Any]] = []
            for link in sorted(model.spacing_links):
                first_width = width_at(link.first_edge, link.vertex)
                second_width = width_at(link.second_edge, link.vertex)
                synchronized = model._spacing_widths_match(first_width, second_width)
                for current, other, width, other_width in (
                    (link.first_edge, link.second_edge, first_width, second_width),
                    (link.second_edge, link.first_edge, second_width, first_width),
                ):
                    links_by_edge.setdefault(current, []).append({
                        "vertex": link.vertex,
                        "other_edge": edge_text(other),
                        "synchronized": synchronized,
                        "width": width,
                        "other_width": other_width,
                    })
                link_entries.append({
                    "vertex": link.vertex,
                    "edges": [edge_text(link.first_edge), edge_text(link.second_edge)],
                    "synchronized": synchronized,
                    "legs": [
                        _round_point(model.edge_point(
                            current, 0.08 if link.vertex == current[0] else 0.92
                        ))
                        for current in (link.first_edge, link.second_edge)
                    ],
                })

            block_count = len(model.blocks)
            data: dict[str, Any] = {
                "version": self.version,
                "session": {
                    "path": str(self.session_path) if self.session_path else None,
                    "name": self.session_path.name if self.session_path else "Untitled",
                    "dirty": self.dirty,
                    "can_undo": self.history.can_undo,
                    "can_redo": self.history.can_redo,
                    "config_warning": self.config_warning,
                    "config_path": str(self.config_path),
                },
                "preferences": {
                    "ui_scale": self.preferences.ui_scale,
                    "show_block_mesh": self.preferences.show_block_mesh,
                    "show_geometry": self.preferences.show_geometry,
                    "show_vertex_ids": self.preferences.show_vertex_ids,
                    "show_edge_cell_counts": self.preferences.show_edge_cell_counts,
                    "show_edge_nodes": self.preferences.show_edge_nodes,
                    "show_edge_interpolation_points":
                        self.preferences.show_edge_interpolation_points,
                    "show_mesh_preview": self.preferences.show_mesh_preview,
                    "preview_coarsening": self.preferences.preview_coarsening,
                    "recent_files": list(self.preferences.recent_files),
                    "shortcuts": {
                        action: list(combos)
                        for action, combos in self.preferences.shortcuts.items()
                    },
                },
                "settings": export_settings_data(model),
                "vertices": [
                    {"id": vertex.id, "x": vertex.x, "y": vertex.y, "used": vertex.id in used}
                    for vertex in model.vertices.values()
                ],
                "blocks": [
                    {
                        "id": block.id,
                        "vertices": list(block.vertices),
                        "cells": list(model.block_cell_counts(block)),
                    }
                    for block in model.blocks
                ],
                "edges": [
                    self._edge_entry(
                        current,
                        occurrences[current],
                        grading[current],
                        component_sizes[current],
                        links_by_edge.get(current, []),
                        block_count,
                    )
                    for current in edges
                ],
                "boundaries": [
                    {
                        "name": boundary.name,
                        "type": boundary.kind,
                        "neighbour_patch": boundary.neighbour_patch,
                        "color": boundary.color,
                        "edge_count": sum(
                            1 for name in model.edge_boundaries.values()
                            if name == boundary.name
                        ),
                    }
                    for boundary in model.boundaries.values()
                ],
                "curves": [
                    {
                        "id": curve.id,
                        "name": curve.name,
                        "show_points": curve.show_points,
                        "points": [list(point) for point in curve.points],
                        "path": [
                            _round_point(point)
                            for point in model.geometry_curve_render_points(
                                curve.id, samples_per_span=GEOMETRY_SAMPLES_PER_SPAN
                            )
                        ],
                        "label_point": _round_point(model.geometry_curve_point(curve.id, 0.5)),
                    }
                    for curve in model.geometry_curves.values()
                ],
                "spacing_links": link_entries,
            }
            return data

    def _edge_entry(
        self,
        current: EdgeKey,
        occurrences: list,
        grading: Any,
        constraint_count: int,
        links: list[dict[str, Any]],
        block_count: int,
    ) -> dict[str, Any]:
        model = self.model
        cells = model.edge_cells[current]
        path = model.edge_render_points(
            current,
            arc_segments=CURVE_RENDER_SEGMENTS,
            spline_samples_per_span=SPLINE_SAMPLES_PER_SPAN,
        )
        node_count = cells - 1
        if node_count > 0:
            fractions = model.edge_node_fractions(current, range(1, cells))
            nodes = model.edge_points(current, fractions)
        else:
            fractions = ()
            nodes = ()
        incident_blocks = {block.id for block, _, _ in occurrences}
        return {
            "id": edge_text(current),
            "vertices": list(current),
            "type": model.edge_type(current),
            "cells": cells,
            "exterior": len(occurrences) == 1,
            "blocks": [block.id for block, _, _ in occurrences],
            "boundary": model.edge_boundaries.get(current),
            "path": [_round_point(point) for point in path],
            "midpoint": _round_point(model.edge_point(current, 0.5)),
            "nodes": [_round_point(point) for point in nodes],
            # Uniform grading is implicit (fraction i / cells); graded edges
            # carry their canonical-direction node fractions for the preview.
            "node_fractions": (
                [float(f"{value:.12g}") for value in fractions]
                if grading.total_ratio != 1.0 else None
            ),
            "dense_nodes": node_count > MAX_INDIVIDUAL_EDGE_NODE_MARKERS,
            "control_points": [list(point) for point in model.edge_control_points(current)],
            "grading": {
                "length": grading.length,
                "cell_ratio": grading.cell_ratio,
                "total_ratio": grading.total_ratio,
                "start_width": grading.start_width,
                "end_width": grading.end_width,
            },
            "constraint_count": constraint_count,
            "can_delete": bool(occurrences) and len(incident_blocks) < block_count,
            "can_combine": len(occurrences) == 2,
            "spacing_links": links,
        }

def _round_point(point: tuple[float, float]) -> list[float]:
    """Trim display coordinates to ten significant digits to shrink JSON."""
    return [float(f"{point[0]:.10g}"), float(f"{point[1]:.10g}")]


def _constraint_component_sizes(model: MeshModel, edges: list[EdgeKey]) -> dict[EdgeKey, int]:
    """Size of every opposite-edge constraint component via union-find."""
    parent: dict[EdgeKey, EdgeKey] = {current: current for current in edges}

    def find(item: EdgeKey) -> EdgeKey:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    for block in model.blocks:
        sides = [edge_key(*block.directed_edge(index)) for index in range(4)]
        for first, second in ((sides[0], sides[2]), (sides[1], sides[3])):
            root_a, root_b = find(first), find(second)
            if root_a != root_b:
                parent[root_a] = root_b
    counts: dict[EdgeKey, int] = {}
    for current in edges:
        counts[find(current)] = counts.get(find(current), 0) + 1
    return {current: counts[find(current)] for current in edges}


def edge_from_text(model: MeshModel, value: Any) -> EdgeKey:
    """Public helper for callers holding ``a-b`` edge text."""
    return parse_edge(model, value)


__all__ = ["WebSession", "WebSessionError", "edge_from_text", "edge_key"]
