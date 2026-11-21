"""The first cell height at a wall, set on every band spoke of its patches.

``set_first_width`` regrades the spokes :func:`blockdrawer.quality.band_spokes`
finds - the edges leaving a wall vertex within ``SPOKE_CONE`` of the wall's
inward normal, the ones ``quality``'s ``first_widths`` reads - so that the
first mesh node of each lies the height asked above the wall's tangent line,
keeping every count. It is the one-line form of a ``set_edge_grading ...
start_width`` per spoke (the benchmark's trials wrote up to 13 of them after
``size``), and says what it costs: the first heights and the interface jumps
before and after.

An edge opposite a spoke in one of its blocks that is not itself a spoke of
those patches (a far-field edge, a front of another band, a wake line) is
``opposite=keep``: left as it is, so the lines across that block tilt by the
difference of the two gradings (the largest tilt is reported), or
``opposite=follow``: given the spoke's node fractions, the same total ratio
run the same physical way across the block, and so on through the further
opposite sides that are no spokes - the strip ``set_edge_grading ...
propagate=true`` sweeps, stopped at spokes (from several spokes, the mean
log ratio) - which carries the wall's clustering along that strip. Counts
never change; one-cell spokes and spokes with both ends on a wall are left
and listed.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .domain import EdgeKey, TopologyError, edge_key
from .model import MeshModel

# Height iterations: the start width along a spoke is corrected by the ratio
# of the height asked to the height found (exact for a straight spoke after
# one step).
ITERATIONS = 6
# A spoke is reported as off when its height leaves the one asked by more
# than this fraction after grading (a curved spoke, or a start width that no
# series of its count can give).
TOLERANCE = 1.0e-6
# The tilts and edges named in the result, at most.
NAMED = 5


def _directed_fractions(model: MeshModel, a: str, b: str) -> list[float]:
    current = edge_key(a, b)
    cells = model.edge_cells[current]
    fractions = model.edge_node_fractions(current, range(cells + 1))
    return list(fractions) if current == (a, b) else [1.0 - value for value in reversed(fractions)]


def _tilt(model: MeshModel, block, k: int) -> float:
    """Degrees the lines across ``block`` tilt between its sides k and k+2."""
    c = block.vertices
    first = _directed_fractions(model, c[k], c[(k + 1) % 4])
    second = _directed_fractions(model, c[(k + 3) % 4], c[(k + 2) % 4])
    length = 0.5 * (model.edge_length(edge_key(c[k], c[(k + 1) % 4]))
                    + model.edge_length(edge_key(c[(k + 3) % 4], c[(k + 2) % 4])))
    height = 0.5 * (model.edge_length(edge_key(c[(k + 1) % 4], c[(k + 2) % 4]))
                    + model.edge_length(edge_key(c[(k + 3) % 4], c[k])))
    shift = max(abs(x - y) for x, y in zip(first, second))
    return math.degrees(math.atan(shift * length / height)) if height > 0.0 else 90.0


def _jumps(model: MeshModel) -> dict[str, Any]:
    from .quality import QualityThresholds, _interface_quality

    limits = QualityThresholds()
    values = [
        _interface_quality(model, current, occurrences, limits).max_size_ratio
        for current, occurrences in model.edge_occurrences().items()
        if len(occurrences) == 2
    ]
    return {
        "worst": max(values, default=1.0),
        "above_2_5": sum(1 for value in values if value > 2.5),
        "above_5": sum(1 for value in values if value > 5.0),
    }


def _total_from_wall(model: MeshModel, current: EdgeKey, vertex: str, start: float) -> float:
    """The canonical total ratio of ``current`` whose first cell at
    ``vertex`` is ``start`` wide along the edge."""
    from .size import series_ratio

    cells = model.edge_cells[current]
    ratio = series_ratio(start, model.edge_length(current), cells)
    total = ratio ** (cells - 1)
    return total if vertex == current[0] else 1.0 / total


def _follow(model: MeshModel, graded: dict[EdgeKey, str], spokes: set[EdgeKey]) -> dict[EdgeKey, list[float]]:
    """``opposite=follow``: from each graded spoke, its node fractions carried
    across its blocks to the opposite sides and on through every further
    opposite side that is no spoke (the strip ``set_edge_grading ...
    propagate=true`` sweeps, stopped at spokes): per edge reached, the
    canonical log total ratio from each spoke that reaches it."""
    occurrences = model.edge_occurrences()
    found: dict[EdgeKey, list[float]] = {}
    for spoke in sorted(graded):
        log = math.log(model.edge_total_expansion(spoke))
        queue = [(spoke, spoke, log)]  # edge, direction the log runs along, log
        seen = {spoke}
        while queue:
            current, direction, value = queue.pop()
            for block, k, directed in occurrences.get(current, ()):
                c = block.vertices
                # The log along this block's side k (corner k -> k+1).
                along = value if directed == direction else -value
                across = (c[(k + 3) % 4], c[(k + 2) % 4])
                other = edge_key(*across)
                if other in seen or other in spokes:
                    continue
                seen.add(other)
                canonical = along if across == other else -along
                found.setdefault(other, []).append(canonical)
                queue.append((other, other, canonical))
    return found


def set_first_width(
    model: MeshModel,
    width: float,
    patches: Sequence[str] = (),
    opposite: str = "keep",
) -> dict[str, Any]:
    """Grade every band spoke of ``patches`` (default: every wall patch) so
    its first cell height normal to the wall is ``width``; see the module
    docstring. The model is changed in place and validated; on an error it
    is left as it was."""
    from .quality import band_spokes, spoke_height, wall_first_widths

    if not math.isfinite(width) or width <= 0.0:
        raise TopologyError("set_first_width: the width must be positive and finite")
    if opposite not in ("keep", "follow"):
        raise TopologyError("set_first_width: opposite is keep or follow")
    walls = sorted(name for name, boundary in model.boundaries.items() if boundary.kind == "wall")
    chosen = list(dict.fromkeys(patches)) if patches else walls
    for name in chosen:
        if name not in model.boundaries:
            raise TopologyError(f"set_first_width: no patch {name!r}")
        if name not in walls:
            raise TopologyError(f"set_first_width: patch {name!r} is of type "
                                f"{model.boundaries[name].kind}, not wall (its edges have no band spokes)")
    if not chosen:
        raise TopologyError("set_first_width: the session has no patch of type wall")
    model.validate()
    found, _others = band_spokes(model)
    ends: dict[EdgeKey, list[tuple[str, str, tuple[float, float]]]] = {}
    for current, vertex, name, normal in found:
        ends.setdefault(current, []).append((vertex, name, normal))
    selected = {current: items for current, items in ends.items() if any(item[1] in chosen for item in items)}
    asked = {name: width for name in chosen}
    before_widths = [item for item in wall_first_widths(model, asked) if item["patch"] in chosen]
    before_jumps = _jumps(model)
    saved = dict(model.edge_grading)
    followed_edges = 0
    one_cell, two_ended, off = [], [], []
    graded: dict[EdgeKey, str] = {}
    try:
        for current in sorted(selected):
            items = selected[current]
            if len({item[0] for item in items}) > 1:
                two_ended.append("-".join(current))
                continue
            if model.edge_cells[current] < 2:
                one_cell.append("-".join(current))
                continue
            vertex, _name, normal = items[0]
            start = width
            height = 0.0
            for _ in range(ITERATIONS):
                model.edge_grading[current] = _total_from_wall(model, current, vertex, start)
                if abs(math.log(model.edge_grading[current])) <= 1.0e-12:
                    model.edge_grading.pop(current, None)
                height = spoke_height(model, current, vertex, normal)
                if height <= 0.0 or abs(height / width - 1.0) <= TOLERANCE:
                    break
                start *= width / height
            if height <= 0.0 or abs(height / width - 1.0) > 1.0e-3:
                off.append(f"{'-'.join(current)} {height:.4g}")
            graded[current] = vertex
        # The sides opposite each spoke in its blocks.
        opposite_logs: dict[EdgeKey, list[float]] = {}
        for block in model.blocks:
            keys = [edge_key(*block.directed_edge(k)) for k in range(4)]
            for k in range(4):
                current, other = keys[k], keys[(k + 2) % 4]
                if current not in graded or other in graded or other in selected:
                    continue
                # Side k runs corner k -> k+1; its opposite, read the same way,
                # runs corner k+3 -> k+2.
                c = block.vertices
                start_corner, end_corner = c[k], c[(k + 1) % 4]
                across_start, across_end = c[(k + 3) % 4], c[(k + 2) % 4]
                log = math.log(model.edge_total_expansion(current))
                if current != (start_corner, end_corner):
                    log = -log  # now along start_corner -> end_corner
                if other != (across_start, across_end):
                    log = -log  # canonical for the opposite edge
                opposite_logs.setdefault(other, []).append(log)
        if opposite == "follow":
            followed = _follow(model, graded, set(selected))
            for other, logs in followed.items():
                value = sum(logs) / len(logs)
                if model.edge_cells[other] < 2 or abs(value) <= 1.0e-12:
                    model.edge_grading.pop(other, None)
                else:
                    model.edge_grading[other] = math.exp(value)
            followed_edges = len(followed)
        model.validate()
    except Exception:
        model.edge_grading = saved
        raise
    # The tilt left between each spoke and an opposite side kept as it was.
    tilted = []
    for block in model.blocks:
        keys = [edge_key(*block.directed_edge(k)) for k in range(4)]
        for k in range(2):
            pair = (keys[k], keys[k + 2])
            if any(edge in opposite_logs for edge in pair) and any(edge in graded for edge in pair):
                tilted.append((_tilt(model, block, k), block.id, "-".join(next(e for e in pair if e in opposite_logs))))
    tilted.sort(key=lambda item: (-item[0], item[1]))
    after_widths = [item for item in wall_first_widths(model, asked) if item["patch"] in chosen]
    after_jumps = _jumps(model)

    def widths(items):
        return {item["patch"]: item["first_width"] for item in items if item.get("spokes")}

    return {
        "width": width,
        "patches": chosen,
        "spokes": len(graded),
        "opposite": opposite,
        "opposite_edges": len(opposite_logs),
        "followed_edges": followed_edges,
        "worst_tilt": [f"{block} {edge} {angle:.3g}" for angle, block, edge in tilted[:NAMED]],
        "max_tilt": tilted[0][0] if tilted else 0.0,
        "one_cell": one_cell,
        "two_ended": two_ended,
        "not_reached": off,
        "first_width_before": widths(before_widths),
        "first_width_after": widths(after_widths),
        "jumps_before": before_jumps,
        "jumps_after": after_jumps,
    }
