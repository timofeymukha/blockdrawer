"""Structured, agent-facing operations over a pipeline run.

These are deliberately research-only: they do not touch BlockDrawer's command
registry or MCP surface.  Each one is a pure function of a completed run, so an
agent reads a description, asks for candidate moves, applies exactly one, and
compares the before and after - without ever being asked to place a vertex.
"""

from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np

import geometry2d as g2
import grid_quality
import moves as move_module
import patch_graph as pg


def describe(result) -> dict:
    """Everything an agent needs to reason about one run."""
    graph = result.graph
    domain = result.domain
    description = {
        "family": result.family,
        "resolved": result.resolved,
        "scale": result.scale,
        "domain": None,
        "boundaries": [],
        "layer_fronts": (result.analysis.get("layers") or {}).get("fronts", []),
        "singularities": [],
        "separatrix_graph": None,
        "count_components": [],
        "worst_cells": [],
        "stage_errors": list(result.errors),
        "failures": list(result.failures),
        "graph_problems": list(result.problems),
    }
    if domain is not None:
        description["domain"] = {
            "name": domain.name,
            "holes": domain.hole_count,
            "euler_characteristic": domain.euler_characteristic,
            "simply_connected": domain.simply_connected,
            "bounds": list(domain.bounds()),
        }
        description["boundaries"] = [
            {
                "chain": chain.name,
                "role": chain.role,
                "patch_type": chain.patch_type,
                "neighbour": chain.neighbour,
                "length": chain.length,
                "is_wall": chain.is_wall,
            }
            for chain in domain.chains()
        ]
    if graph is not None:
        description["graph"] = graph.summary()
        description["singularities"] = [
            {
                **record,
                "session_vertex": result.identifiers.get(tuple(record["vertex"])),
            }
            for record in graph.singularities()
        ]
        description["count_components"] = [
            {
                "component": record["component"],
                "cells": record["cells"],
                "edges": record["edges"],
                "roles": record["roles"],
                "metric_target": record["weighted_target"],
            }
            for record in (result.counts.components if result.counts else [])
        ]
    if result.family == "external" and result.diagram is not None:
        description["separatrix_graph"] = {
            "kind": "generalized medial axis",
            "junctions": [
                {
                    "index": junction.index,
                    "point": [float(junction.point[0]), float(junction.point[1])],
                    "sites": [result.sites[i].name for i in junction.sites],
                    "degree": junction.degree,
                }
                for junction in result.diagram.junctions
            ],
            "branches": [
                {
                    "index": branch.index,
                    "sites": [result.sites[i].name for i in branch.pair],
                    "length": branch.length,
                    "closed": branch.closed,
                }
                for branch in result.diagram.branches
            ],
        }
    elif result.family == "internal" and result.correspondence is not None:
        description["separatrix_graph"] = {
            "kind": "sweep ribs",
            "columns": result.correspondence.columns,
            "monotone": result.correspondence.monotone,
            "crossing_columns": list(result.correspondence.crossing),
        }
    if result.grid is not None:
        description["worst_cells"] = result.grid.worst_cells
        description["quality"] = result.grid.described()
    return description


def candidate_report(result) -> dict:
    """Ranked candidate topology moves with their screening results."""
    found = move_module.candidates(result)
    return {
        "index_budget": {
            "expected_total": 4 * (
                result.domain.euler_characteristic if result.domain else 1
            ),
            "actual_total": result.graph.total_index() if result.graph else None,
        },
        "candidates": [move.described() for move in found],
        "applicable": [
            move.identifier for move in found if move.implemented
        ],
    }


def find_move(result, identifier: str):
    for move in move_module.candidates(result):
        if move.identifier == identifier:
            return move
    raise KeyError(f"unknown move {identifier!r}")


def compare(first, second) -> dict:
    """Before and after scores of two runs of the same case."""
    return {
        "resolved": {"before": first.resolved, "after": second.resolved},
        "blocks": {
            "before": len(first.graph.faces) if first.graph else None,
            "after": len(second.graph.faces) if second.graph else None,
        },
        "cells": {
            "before": first.counts.total_cells if first.counts else None,
            "after": second.counts.total_cells if second.counts else None,
        },
        "singularities": {
            "before": len(first.graph.singularities()) if first.graph else None,
            "after": len(second.graph.singularities()) if second.graph else None,
        },
        "grid": grid_quality.compare(
            first.grid.described() if first.grid else {},
            second.grid.described() if second.grid else {},
        ),
    }


def focus(result, *, block: str | None = None, point=None, radius: float | None = None):
    """Bounds and contents of a small diagnostic window around a bad region."""
    centre = None
    if point is not None:
        centre = np.asarray(point, dtype=np.float64)
    elif block is not None and result.graph is not None:
        index = int(block[1:]) if block.startswith("b") else int(block)
        centre = np.mean(result.graph.corner_points(result.graph.faces[index]), axis=0)
    elif result.grid is not None and result.grid.maximum_skewness is not None:
        centre = np.asarray(result.grid.maximum_skewness.point)
    elif result.failures:
        for failure in result.failures:
            points = failure.get("gate_points")
            if points:
                centre = np.asarray(points[0], dtype=np.float64)
                break
    if centre is None:
        return None
    span = radius if radius is not None else 0.06 * result.scale
    bounds = (
        float(centre[0] - span),
        float(centre[1] - span),
        float(centre[0] + span),
        float(centre[1] + span),
    )
    contents = []
    if result.graph is not None:
        for index, face in enumerate(result.graph.faces):
            points = result.graph.corner_points(face)
            box = (
                float(np.min(points[:, 0])),
                float(np.min(points[:, 1])),
                float(np.max(points[:, 0])),
                float(np.max(points[:, 1])),
            )
            overlaps = not (
                box[2] < bounds[0]
                or bounds[2] < box[0]
                or box[3] < bounds[1]
                or bounds[3] < box[1]
            )
            if overlaps:
                angles = np.degrees(g2.quad_corner_angles(points))
                contents.append(
                    {
                        "block": f"b{index}",
                        "role": face.role,
                        "provenance": face.provenance,
                        "corner_angles_degrees": [float(value) for value in angles],
                        "cells": _face_cells(result, face),
                    }
                )
    return {
        "centre": [float(centre[0]), float(centre[1])],
        "bounds": list(bounds),
        "radius_over_scale": span / max(result.scale, 1e-30),
        "blocks": contents,
    }


def _face_cells(result, face) -> list | None:
    if result.counts is None or result.graph is None:
        return None
    edges = result.graph.face_edges(face)
    return [
        int(result.counts.counts.get(edges[0], 0)),
        int(result.counts.counts.get(edges[1], 0)),
    ]
