"""Compatibility entry point for the external-flow topology run.

The prototype grew a second domain family (simply connected internal flow) and
a small set of agent-facing operations, so the command line moved to
``research_cli.py``.  This module keeps the original invocation working:

    python experiments/agentic_topology/colored_medial_axis.py \
      --curve slat=slat.dat --curve main=main.dat --curve flap=flap.dat \
      --output coupling.png --json coupling.json --session coupling-session.json

is exactly ``research_cli.py run`` with the same arguments.  New work should
call the new command directly; it also offers ``describe``, ``candidates``,
``apply`` and ``focus``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_cli  # noqa: E402


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"run", "describe", "candidates"}:
        arguments = ["run", *arguments]
    return research_cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
