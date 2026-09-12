"""Cross-platform parsing for BlockDrawer's shell-like command strings.

OpenFOAM applications are normally Linux programs, even when BlockDrawer is
running on Windows and reaches them through WSL, a container, or another
wrapper. Windows parsing therefore still accepts POSIX single/double-quoted
groups, while treating backslashes literally so native paths survive intact.
"""

from __future__ import annotations

import os
import shlex


def split_command_line(text: str, *, windows: bool | None = None) -> list[str]:
    """Split a shell-like command without corrupting native Windows paths.

    POSIX hosts use :func:`shlex.split` unchanged. On Windows, quotes retain
    their POSIX grouping behavior (needed for ``bash -lc '...'`` wrappers), but
    backslash escaping is disabled. Windows callers should quote arguments
    containing whitespace instead of escaping the whitespace with a backslash.

    ``windows`` exists so both behaviors can be tested on every host; normal
    callers should leave it unset.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return shlex.split(text)

    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)
