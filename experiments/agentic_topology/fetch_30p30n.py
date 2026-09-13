"""Download the 30P30N acceptance geometry into a git-ignored cache.

The point lists belong to the third-party validation case at
https://github.com/linuxguy123/30P-30N-Validation-Case and are deliberately not
copied into this repository.  This helper fetches them into
``experiments/agentic_topology/geometry/`` so the acceptance run is
reproducible from a clean checkout.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://raw.githubusercontent.com/linuxguy123/30P-30N-Validation-Case/master/"
)
FILES = {
    "slat": "30P-30N-Slat-Normalized.dat",
    "main": "30P-30N-Main-Normalized.dat",
    "flap": "30P-30N-Flap-Normalized.dat",
}
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "geometry"


def fetch(directory: Path = DEFAULT_DIRECTORY, *, force: bool = False) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, filename in FILES.items():
        destination = directory / filename
        if force or not destination.exists():
            with urllib.request.urlopen(BASE_URL + filename, timeout=60) as response:
                destination.write_bytes(response.read())
        result[name] = destination
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=DEFAULT_DIRECTORY, help="download target"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-download existing files"
    )
    arguments = parser.parse_args(argv)
    for name, path in fetch(arguments.directory, force=arguments.force).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
