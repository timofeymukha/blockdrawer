"""Cross-platform, human-editable BlockDrawer application preferences."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


FORMAT_NAME = "blockDrawerConfig"
FORMAT_VERSION = 1
MIN_UI_SCALE = 0.5
MAX_UI_SCALE = 4.0

SHORTCUT_ACTIONS = (
    "new_session",
    "open_session",
    "save_session",
    "save_session_as",
    "export_block_mesh_dict",
    "undo",
    "redo",
    "delete_edge",
    "new_block",
    "add_vertex",
    "set_boundaries",
    "project",
    "toggle_geometry",
    "cancel",
    "fit_view",
)

_MODIFIER_ALIASES = {
    "ctrl": "Control",
    "control": "Control",
    "shift": "Shift",
    "alt": "Alt",
    "option": "Option",
    "cmd": "Command",
    "command": "Command",
    "meta": "Meta",
}
_MODIFIER_ORDER = ("Control", "Shift", "Alt", "Option", "Command", "Meta")
_KEY_ALIASES = {
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "numpaddelete": "KP_Delete",
    "kp_delete": "KP_Delete",
    "esc": "Escape",
    "escape": "Escape",
    "enter": "Return",
    "return": "Return",
    "space": "space",
    "tab": "Tab",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
}


class ConfigError(ValueError):
    """Raised when an application preferences file is malformed."""


@dataclass(frozen=True)
class AppConfig:
    """Validated application-wide preferences."""

    ui_scale: str
    shortcuts: dict[str, tuple[str, ...]]
    show_block_mesh: bool = True
    show_geometry: bool = True
    show_edge_nodes: bool = True
    show_edge_interpolation_points: bool = True

    def with_ui_scale(self, value: str | float) -> AppConfig:
        return replace(self, ui_scale=_ui_scale(value))

    def with_visibility(
        self,
        *,
        show_block_mesh: bool,
        show_geometry: bool,
        show_edge_nodes: bool,
        show_edge_interpolation_points: bool,
    ) -> AppConfig:
        return replace(
            self,
            show_block_mesh=_boolean(show_block_mesh, "ui.showBlockMesh"),
            show_geometry=_boolean(show_geometry, "ui.showGeometry"),
            show_edge_nodes=_boolean(show_edge_nodes, "ui.showEdgeNodes"),
            show_edge_interpolation_points=_boolean(
                show_edge_interpolation_points,
                "ui.showEdgeInterpolationPoints",
            ),
        )


def default_shortcuts(platform: str | None = None) -> dict[str, tuple[str, ...]]:
    """Return platform-natural bindings for every configurable action."""
    current_platform = sys.platform if platform is None else platform
    primary = "Cmd" if current_platform == "darwin" else "Ctrl"
    return {
        "new_session": (f"{primary}+N",),
        "open_session": (f"{primary}+O",),
        "save_session": (f"{primary}+S",),
        "save_session_as": (f"{primary}+Shift+S",),
        "export_block_mesh_dict": (f"{primary}+E",),
        "undo": (f"{primary}+Z",),
        "redo": ("Cmd+Shift+Z",) if current_platform == "darwin" else (
            "Ctrl+Y", "Ctrl+Shift+Z"
        ),
        "delete_edge": ("Delete", "Backspace", "NumpadDelete", "X"),
        "new_block": ("N",),
        "add_vertex": ("V",),
        "set_boundaries": ("B",),
        "project": ("P",),
        "toggle_geometry": ("G",),
        "cancel": ("Esc",),
        "fit_view": (),
    }


def default_config(platform: str | None = None) -> AppConfig:
    return AppConfig(
        "auto", default_shortcuts(platform), True, True, True, True
    )


def default_config_path(
    *,
    platform: str | None = None,
    home: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the preferences location without creating it."""
    current_platform = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else Path(home)
    current_environment = os.environ if environment is None else environment
    if current_platform == "win32":
        app_data = current_environment.get("APPDATA")
        base = Path(app_data) if app_data else user_home / "AppData" / "Roaming"
        return base / "BlockDrawer" / "config.json"
    return user_home / ".blockdrawer"


def shortcut_to_tk_sequences(shortcut: str) -> tuple[str, ...]:
    """Convert a readable combination into one or more Tk event sequences."""
    if not isinstance(shortcut, str) or not shortcut.strip():
        raise ConfigError("A shortcut must be a non-empty string")
    parts = [part.strip() for part in shortcut.split("+")]
    if any(not part for part in parts):
        raise ConfigError(f"Invalid shortcut {shortcut!r}")
    if len(parts) > len(_MODIFIER_ORDER) + 1:
        raise ConfigError(f"Invalid shortcut {shortcut!r}")

    modifiers: set[str] = set()
    for value in parts[:-1]:
        modifier = _MODIFIER_ALIASES.get(value.casefold())
        if modifier is None or modifier in modifiers:
            raise ConfigError(
                f"Shortcut {shortcut!r} has an unknown or repeated modifier"
            )
        modifiers.add(modifier)

    key_text = parts[-1]
    if key_text.casefold() in _MODIFIER_ALIASES:
        raise ConfigError(f"Shortcut {shortcut!r} has no key")
    key = _key_symbol(key_text, shortcut)
    ordered = [value for value in _MODIFIER_ORDER if value in modifiers]
    prefix = "-".join(ordered)
    if prefix:
        prefix += "-"

    if len(key) == 1 and key.isalpha():
        if "Shift" in modifiers:
            return (f"<{prefix}KeyPress-{key.upper()}>",)
        if modifiers:
            return (f"<{prefix}KeyPress-{key.lower()}>",)
        return (
            f"<KeyPress-{key.lower()}>",
            f"<KeyPress-{key.upper()}>",
        )
    return (f"<{prefix}{key}>",)


def from_data(data: Any, *, platform: str | None = None) -> AppConfig:
    """Validate preferences and merge omitted actions with current defaults."""
    if not isinstance(data, dict):
        raise ConfigError("The config root must be a JSON object")
    if data.get("format") != FORMAT_NAME:
        raise ConfigError("This is not a BlockDrawer config file")
    version = data.get("version")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise ConfigError(
            f"Unsupported BlockDrawer config version {version!r}"
        )

    ui = data.get("ui", {})
    if not isinstance(ui, dict):
        raise ConfigError("'ui' must be an object")
    scale = _ui_scale(ui.get("scale", "auto"))
    show_block_mesh = _boolean(
        ui.get("showBlockMesh", True), "ui.showBlockMesh"
    )
    show_geometry = _boolean(
        ui.get("showGeometry", True), "ui.showGeometry"
    )
    show_edge_nodes = _boolean(
        ui.get("showEdgeNodes", True), "ui.showEdgeNodes"
    )
    show_edge_interpolation_points = _boolean(
        ui.get("showEdgeInterpolationPoints", True),
        "ui.showEdgeInterpolationPoints",
    )

    shortcuts_data = data.get("shortcuts", {})
    if not isinstance(shortcuts_data, dict):
        raise ConfigError("'shortcuts' must be an object")
    unknown = set(shortcuts_data) - set(SHORTCUT_ACTIONS)
    if unknown:
        raise ConfigError(
            f"Unknown shortcut action(s): {', '.join(sorted(unknown))}"
        )
    shortcuts = default_shortcuts(platform)
    for action, values in shortcuts_data.items():
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise ConfigError(
                f"Shortcut action {action!r} must contain an array of strings"
            )
        shortcuts[action] = tuple(values)
    _validate_shortcuts(shortcuts)
    return AppConfig(
        scale,
        shortcuts,
        show_block_mesh,
        show_geometry,
        show_edge_nodes,
        show_edge_interpolation_points,
    )


def to_data(config: AppConfig) -> dict[str, Any]:
    """Serialize every preference and action for easy manual discovery."""
    _validate_config(config)
    scale: str | float = (
        "auto" if config.ui_scale == "auto" else float(config.ui_scale)
    )
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "ui": {
            "scale": scale,
            "showBlockMesh": config.show_block_mesh,
            "showGeometry": config.show_geometry,
            "showEdgeNodes": config.show_edge_nodes,
            "showEdgeInterpolationPoints": (
                config.show_edge_interpolation_points
            ),
        },
        "shortcuts": {
            action: list(config.shortcuts[action])
            for action in SHORTCUT_ACTIONS
        },
    }


def load_config(
    path: str | Path,
    *,
    platform: str | None = None,
) -> AppConfig:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Could not parse {source}: {exc}") from exc
    except UnicodeError as exc:
        raise ConfigError(f"Could not decode {source} as UTF-8: {exc}") from exc
    return from_data(data, platform=platform)


def save_config(config: AppConfig, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(to_data(config), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _key_symbol(value: str, shortcut: str) -> str:
    alias = _KEY_ALIASES.get(value.casefold())
    if alias is not None:
        return alias
    if re.fullmatch(r"(?i)f(?:[1-9]|1[0-9]|2[0-4])", value):
        return value.upper()
    if len(value) == 1 and value.isascii() and value.isalnum():
        return value
    raise ConfigError(f"Shortcut {shortcut!r} has an unsupported key")


def _ui_scale(value: Any) -> str:
    if isinstance(value, str) and value.casefold() == "auto":
        return "auto"
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError("ui.scale must be 'auto' or a number")
    try:
        scale = float(value)
    except ValueError as exc:
        raise ConfigError("ui.scale must be 'auto' or a number") from exc
    if not math.isfinite(scale) or not MIN_UI_SCALE <= scale <= MAX_UI_SCALE:
        raise ConfigError(
            f"ui.scale must be between {MIN_UI_SCALE:g} and {MAX_UI_SCALE:g}"
        )
    return format(scale, ".15g")


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


def _validate_shortcuts(shortcuts: Mapping[str, tuple[str, ...]]) -> None:
    if set(shortcuts) != set(SHORTCUT_ACTIONS):
        raise ConfigError("The shortcut map does not cover every action")
    owners: dict[str, tuple[str, str]] = {}
    for action in SHORTCUT_ACTIONS:
        values = shortcuts[action]
        if not isinstance(values, tuple):
            raise ConfigError(f"Shortcut action {action!r} has invalid data")
        for shortcut in values:
            for sequence in shortcut_to_tk_sequences(shortcut):
                if sequence in owners:
                    owner, original = owners[sequence]
                    raise ConfigError(
                        f"Shortcut {shortcut!r} for {action!r} conflicts with "
                        f"{original!r} for {owner!r}"
                    )
                owners[sequence] = (action, shortcut)


def _validate_config(config: AppConfig) -> None:
    if not isinstance(config, AppConfig):
        raise ConfigError("Invalid application config data")
    _ui_scale(config.ui_scale)
    _boolean(config.show_block_mesh, "ui.showBlockMesh")
    _boolean(config.show_geometry, "ui.showGeometry")
    _boolean(config.show_edge_nodes, "ui.showEdgeNodes")
    _boolean(
        config.show_edge_interpolation_points,
        "ui.showEdgeInterpolationPoints",
    )
    _validate_shortcuts(config.shortcuts)
