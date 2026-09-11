"""Standard-library HTTP server for the browser editor.

Routes:

- ``GET /``                     the client page with the launch token embedded
- ``GET /static/<file>``        client assets
- ``GET /api/state``            complete snapshot
- ``GET /api/events``           server-sent events: ``state`` and ``external_change``
- ``GET /api/files?dir=...``    directory listing for file dialogs
- ``POST /api/<action>``        JSON body; mutating actions return the new snapshot

Every ``/api`` request must carry the per-launch token (header
``X-BlockDrawer-Token`` or ``?token=`` query), so other pages open in the
same browser cannot drive the editor.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import sys
import threading
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .session import WebSession, WebSessionError


STATIC_DIR = Path(__file__).parent / "static"
EVENT_POLL_SECONDS = 1.0
EVENT_KEEPALIVE_SECONDS = 15.0


class WebServer(ThreadingHTTPServer):
    """HTTP server bound to one :class:`WebSession`."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        session: WebSession,
        *,
        token: str | None = None,
        static_dir: Path | None = None,
    ) -> None:
        super().__init__(address, BlockDrawerHandler)
        self.session = session
        self.token = token or secrets.token_urlsafe(24)
        self.static_dir = static_dir or STATIC_DIR

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
        return f"http://{shown}:{port}/"

    def shutdown_later(self) -> None:
        threading.Thread(target=self.shutdown, daemon=True).start()


class BlockDrawerHandler(BaseHTTPRequestHandler):
    server: WebServer
    protocol_version = "HTTP/1.1"

    # Quiet the default per-request logging.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        if path in ("/", "/index.html"):
            self._send_index()
        elif path.startswith("/static/"):
            self._send_static(path[len("/static/"):])
        elif path == "/api/state":
            if self._authorized(query):
                self._send_json({"ok": True, "state": self.server.session.snapshot()})
        elif path == "/api/events":
            if self._authorized(query):
                self._stream_events(query)
        elif path == "/api/files":
            if self._authorized(query):
                directory = query.get("dir", [None])[0]
                self._run(lambda: self.server.session.list_directory(directory))
        else:
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if not parts.path.startswith("/api/"):
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._authorized(parse_qs(parts.query)):
            return
        action = parts.path[len("/api/"):]
        handler = ACTIONS.get(action)
        if handler is None:
            self._send_json(
                {"ok": False, "error": f"Unknown action {action!r}"}, HTTPStatus.NOT_FOUND
            )
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._run(lambda: handler(self.server, body))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        supplied = self.headers.get("X-BlockDrawer-Token") or query.get("token", [None])[0]
        if supplied == self.server.token:
            return True
        self._send_json(
            {"ok": False, "error": "Missing or invalid token"}, HTTPStatus.UNAUTHORIZED
        )
        return False

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("The request body must be a JSON object")
        return data

    def _run(self, action: Callable[[], Any]) -> None:
        try:
            payload = action()
        except WebSessionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(
                {"ok": False, "error": f"Bad request: {exc}"}, HTTPStatus.BAD_REQUEST
            )
            return
        if isinstance(payload, dict) and "ok" in payload:
            self._send_json(payload)
        else:
            self._send_json({"ok": True, "result": payload})

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        source = self.server.static_dir / "index.html"
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            self._send_json({"ok": False, "error": "Client files missing"}, HTTPStatus.NOT_FOUND)
            return
        body = text.replace("__TOKEN__", self.server.token).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str) -> None:
        if "/" in name or name.startswith(".") or not name:
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        source = self.server.static_dir / name
        if not source.is_file():
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        body = source.read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript", "application/json",
        ):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self, query: dict[str, list[str]]) -> None:
        session = self.server.session
        try:
            known = int(query.get("version", ["-1"])[0])
        except ValueError:
            known = -1
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        idle = 0.0
        external_reported = False
        try:
            while True:
                current = session.wait_for_change(known, EVENT_POLL_SECONDS)
                if current != known:
                    known = current
                    idle = 0.0
                    self._write_event("state", {"version": current})
                    continue
                idle += EVENT_POLL_SECONDS
                changed = session.external_change_detected()
                if changed and not external_reported:
                    external_reported = True
                    self._write_event("external_change", {"path": str(session.session_path)})
                elif not changed:
                    external_reported = False
                if idle >= EVENT_KEEPALIVE_SECONDS:
                    idle = 0.0
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _write_event(self, name: str, data: dict[str, Any]) -> None:
        payload = f"event: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()


def _json_default(value: Any) -> Any:
    if isinstance(value, float):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


# ----------------------------------------------------------------------
# Actions
# ----------------------------------------------------------------------


def _with_state(server: WebServer, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "state": server.session.snapshot()}
    if extra:
        payload.update(extra)
    return payload


def _action_command(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    op = body.get("op")
    if not isinstance(op, str):
        raise WebSessionError('A command needs an "op" field')
    arguments = body.get("args") or {}
    if not isinstance(arguments, dict):
        raise WebSessionError('"args" must be an object')
    result = server.session.command(op, arguments)
    return _with_state(server, {"result": result})


def _action_drag(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    server.session.drag(str(body.get("kind")), body.get("target"), body["x"], body["y"])
    return _with_state(server)


def _action_drag_end(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    recorded = server.session.drag_end()
    return _with_state(server, {"recorded": recorded})


def _action_undo(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    return _with_state(server, {"changed": server.session.undo()})


def _action_redo(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    return _with_state(server, {"changed": server.session.redo()})


def _action_new(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    server.session.new_session()
    return _with_state(server)


def _action_open(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    path = body.get("path")
    if not isinstance(path, str) or not path:
        raise WebSessionError("A session path is required")
    warning = server.session.open_session(path)
    return _with_state(server, {"warning": warning})


def _action_reload(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    warning = server.session.reload_session()
    return _with_state(server, {"warning": warning})


def _action_save(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    path = body.get("path")
    warning = server.session.save_session(path if isinstance(path, str) and path else None)
    return _with_state(server, {"warning": warning})


def _action_export(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    path = body.get("path")
    if not isinstance(path, str) or not path:
        raise WebSessionError("A destination path is required")
    settings = body.get("settings") or {}
    if not isinstance(settings, dict):
        raise WebSessionError('"settings" must be an object')
    server.session.export_block_mesh_dict(path, settings)
    return _with_state(server, {"path": path})


def _action_visibility(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    flags = body.get("flags") or {}
    if not isinstance(flags, dict):
        raise WebSessionError('"flags" must be an object')
    warning = server.session.set_visibility(**flags)
    return _with_state(server, {"warning": warning})


def _action_preview_coarsening(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    warning = server.session.set_preview_coarsening(body.get("value"))
    return _with_state(server, {"warning": warning})


def _action_ui_scale(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    warning = server.session.set_ui_scale(body.get("value"))
    return _with_state(server, {"warning": warning})


def _action_recent_remove(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    warning = server.session.remove_recent(str(body.get("path")))
    return _with_state(server, {"warning": warning})


def _action_recent_clear(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    warning = server.session.clear_recent()
    return _with_state(server, {"warning": warning})


def _action_edge_fraction(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    return server.session.edge_fraction(body.get("edge"), body["x"], body["y"])


def _action_split_cells(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    return server.session.split_cells(body.get("edge"), body["fraction"])


def _action_shutdown(server: WebServer, body: dict[str, Any]) -> dict[str, Any]:
    server.shutdown_later()
    return {"ok": True, "result": "shutting down"}


ACTIONS: dict[str, Callable[[WebServer, dict[str, Any]], dict[str, Any]]] = {
    "command": _action_command,
    "drag": _action_drag,
    "drag_end": _action_drag_end,
    "undo": _action_undo,
    "redo": _action_redo,
    "new": _action_new,
    "open": _action_open,
    "reload": _action_reload,
    "save": _action_save,
    "export": _action_export,
    "visibility": _action_visibility,
    "preview_coarsening": _action_preview_coarsening,
    "ui_scale": _action_ui_scale,
    "recent_remove": _action_recent_remove,
    "recent_clear": _action_recent_clear,
    "edge_fraction": _action_edge_fraction,
    "split_cells": _action_split_cells,
    "shutdown": _action_shutdown,
}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def serve(
    session_path: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    config_path: str | Path | None = None,
) -> int:
    """Run the editor server until it is shut down; return an exit status."""
    try:
        session = WebSession(session_path, config_path=config_path)
    except WebSessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    server = WebServer((host, port), session)
    url = f"{server.url}?token={server.token}"
    print(f"BlockDrawer web editor at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="blockdrawer-web",
        description="Edit a BlockDrawer session in the browser.",
    )
    parser.add_argument("session", nargs="?", type=Path, help="session JSON to open")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default loopback)")
    parser.add_argument("--port", type=int, default=0, help="port (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--config", type=Path, help="preferences file (default: ~/.blockdrawer)")
    args = parser.parse_args(argv)
    return serve(
        args.session,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        config_path=args.config,
    )
