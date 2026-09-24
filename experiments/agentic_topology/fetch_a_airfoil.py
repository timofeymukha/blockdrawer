"""Download the Aerospatiale A-airfoil coordinates into the git-ignored cache.

The point list belongs to the Tohoku University turbulence database
(https://www.klab.mech.tohoku.ac.jp/database/) and is not copied into this
repository.  This helper fetches it into ``experiments/agentic_topology/
geometry/`` and writes a normalised copy - chord 1, leading edge at the
origin, chord along +x - because the supplied chord line is inclined by about
0.68 degrees.  Every supplied point is kept; nothing is resampled.
"""

from __future__ import annotations

import argparse
import math
import urllib.request
from pathlib import Path

URL = "https://www.klab.mech.tohoku.ac.jp/database/2022TamakiAIAAJ/airfoil.txt"
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "geometry"
RAW_NAME = "A-Airfoil.dat"
NORMALISED_NAME = "A-Airfoil-Normalized.dat"


def parse_points(text: str) -> list[tuple[float, float]]:
    points = []
    for line in text.splitlines():
        fields = line.replace(",", " ").split()
        if len(fields) != 2:
            continue
        try:
            points.append((float(fields[0]), float(fields[1])))
        except ValueError:
            continue
    if len(points) < 3:
        raise ValueError("the airfoil file holds fewer than three coordinate pairs")
    return points


def normalise(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Chord 1, leading edge at the origin, trailing edge at (1, 0)."""
    trailing = points[0]
    leading = max(points, key=lambda point: math.dist(point, trailing))
    chord = math.dist(leading, trailing)
    angle = math.atan2(trailing[1] - leading[1], trailing[0] - leading[0])
    cos, sin = math.cos(-angle), math.sin(-angle)
    result = []
    for x, y in points:
        dx, dy = x - leading[0], y - leading[1]
        result.append(((dx * cos - dy * sin) / chord, (dx * sin + dy * cos) / chord))
    return result


def fetch(directory: Path = DEFAULT_DIRECTORY, *, force: bool = False) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / RAW_NAME
    if force or not raw.exists():
        with urllib.request.urlopen(URL, timeout=60) as response:
            raw.write_bytes(response.read())
    normalised = directory / NORMALISED_NAME
    if force or not normalised.exists():
        points = normalise(parse_points(raw.read_text(encoding="utf-8", errors="replace")))
        normalised.write_text(
            "".join(f"{x:.12f} {y:.12f}\n" for x, y in points), encoding="utf-8"
        )
    return {"raw": raw, "normalised": normalised}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--force", action="store_true", help="re-download and re-normalise")
    arguments = parser.parse_args(argv)
    for name, path in fetch(arguments.directory, force=arguments.force).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
