"""Browser-based BlockDrawer editor: a local HTTP server plus a canvas client.

The server owns the model, history, and preferences exactly like the Tk
application; the browser owns the viewport, selection, and editing modes.
Everything is standard library: ``http.server`` for the API and static
files, server-sent events for live updates, and a vanilla JavaScript client.
"""

from .session import WebSession
from .server import WebServer, serve

__all__ = ["WebSession", "WebServer", "serve"]
