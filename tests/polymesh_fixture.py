"""Write small extruded ASCII polyMesh cases by hand, as blockMesh would.

The tests of :mod:`blockdrawer.polymesh` and :mod:`blockdrawer.checkmesh`
must not need OpenFOAM, so this writes the four polyMesh lists and checkMesh
sets for a handful of quadrilateral cells extruded one cell deep: internal
faces first with the lower cell as owner, outward-facing boundary faces, and
the zMin/zMax faces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


HEADER = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      {format};
    arch        "LSB;label=32;scalar=64";
    class       {cls};
    location    "{location}";
    object      {name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //


"""

FOOTER = "\n\n// ************************************************************************* //\n"


def foam_list(items: Sequence[str], *, cls: str, name: str, location: str, format: str = "ascii") -> str:
    body = f"{len(items)}\n(\n" + "\n".join(items) + "\n)\n"
    return HEADER.format(format=format, cls=cls, location=location, name=name) + body + FOOTER


def extruded_mesh(
    points2d: Sequence[tuple[float, float]], cells: Sequence[tuple[int, int, int, int]]
) -> dict[str, list]:
    """Points, faces, owner and neighbour of quads extruded from z=0 to z=1.

    Each cell lists four point indices counter-clockwise.
    """
    count = len(points2d)
    points = [(x, y, 0.0) for x, y in points2d] + [(x, y, 1.0) for x, y in points2d]
    edges: dict[tuple[int, int], list[tuple[int, tuple[int, int]]]] = {}
    for cell, quad in enumerate(cells):
        for index in range(4):
            directed = (quad[index], quad[(index + 1) % 4])
            edges.setdefault(tuple(sorted(directed)), []).append((cell, directed))
    internal = []
    boundary = []
    for occurrences in edges.values():
        occurrences.sort()
        owner, (p, q) = occurrences[0]
        # The right-hand normal of the owner's counter-clockwise side points out of it.
        face = [p, q, q + count, p + count]
        if len(occurrences) == 2:
            internal.append((owner, occurrences[1][0], face))
        else:
            boundary.append((owner, face))
    internal.sort(key=lambda item: (item[0], item[1]))
    faces = [face for _o, _n, face in internal] + [face for _o, face in boundary]
    owner = [o for o, _n, _f in internal] + [o for o, _f in boundary]
    neighbour = [n for _o, n, _f in internal]
    for cell, (a, b, c, d) in enumerate(cells):
        faces.append([a, d, c, b])
        owner.append(cell)
    for cell, (a, b, c, d) in enumerate(cells):
        faces.append([a + count, b + count, c + count, d + count])
        owner.append(cell)
    return {"points": points, "faces": faces, "owner": owner, "neighbour": neighbour}


def write_polymesh(case: Path, mesh: dict[str, list], *, format: str = "ascii") -> Path:
    directory = case / "constant" / "polyMesh"
    directory.mkdir(parents=True, exist_ok=True)
    location = "constant/polyMesh"
    (directory / "points").write_text(foam_list(
        [f"({x:.10g} {y:.10g} {z:.10g})" for x, y, z in mesh["points"]],
        cls="vectorField", name="points", location=location, format=format,
    ))
    (directory / "faces").write_text(foam_list(
        [f"{len(face)}({' '.join(str(label) for label in face)})" for face in mesh["faces"]],
        cls="faceList", name="faces", location=location, format=format,
    ))
    for name in ("owner", "neighbour"):
        (directory / name).write_text(foam_list(
            [str(label) for label in mesh[name]],
            cls="labelList", name=name, location=location, format=format,
        ))
    return directory


def write_set(case: Path, name: str, cls: str, labels: Sequence[int]) -> Path:
    directory = case / "constant" / "polyMesh" / "sets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(foam_list(
        [str(label) for label in labels],
        cls=cls, name=name, location="constant/polyMesh/sets",
    ))
    return path
