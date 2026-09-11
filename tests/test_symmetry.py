from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from blockdrawer.domain import EdgeGeometry, SpacingLink
from blockdrawer.model import MeshModel, edge_key
from blockdrawer.session import load_session, save_session
from blockdrawer.symmetry import (
    SymmetryError,
    default_output_path,
    main,
    symmetrize_model,
)


class SessionSymmetryTests(unittest.TestCase):
    def test_positive_half_replaces_target_with_complete_mirrored_data(
        self,
    ) -> None:
        model = MeshModel()
        model.add_block(edge_key("v0", "v1"))
        old_target_vertices = {
            identifier
            for identifier, vertex in model.vertices.items()
            if vertex.y < 0.0
        }
        model.move_vertex("v2", 1.2, 1.1)
        right = edge_key("v1", "v2")
        top = edge_key("v2", "v3")
        model.set_edge_type(right, "arc")
        model.set_edge_type(top, "spline")
        model.set_edge_control_point_count(top, 2)
        model.set_edge_cells(right, 7)
        model.set_edge_cells(top, 9)
        model.set_edge_grading(top, "total_ratio", 4.0)
        source_link = model.add_spacing_link(top, right)
        airfoil = model.add_boundary("airfoil")
        model.set_edge_boundary(top, airfoil.name)
        curve = model.add_geometry_curve(
            ((0.0, 0.0), (0.5, 0.2), (1.0, 0.0)),
            name="reference",
        )
        positive_standalone = model.add_vertex(2.0, 0.5)
        negative_standalone = model.add_vertex(3.0, -0.5)

        operation = symmetrize_model(model, axis="x")
        mirrored = operation.model

        self.assertEqual(operation.source_block_count, 1)
        self.assertEqual(operation.mirrored_block_count, 1)
        self.assertEqual(len(mirrored.blocks), 2)
        self.assertTrue(old_target_vertices.isdisjoint(mirrored.vertices))
        self.assertNotIn(negative_standalone.id, mirrored.vertices)
        self.assertIn(positive_standalone.id, mirrored.vertices)
        mirrored_standalone = operation.vertex_map[positive_standalone.id]
        self.assertEqual(
            (
                mirrored.vertices[mirrored_standalone].x,
                mirrored.vertices[mirrored_standalone].y,
            ),
            (2.0, -0.5),
        )
        self.assertEqual(mirrored.geometry_curves[curve.id], curve)

        mirrored_top = _mapped_edge(top, operation.vertex_map)
        mirrored_right = _mapped_edge(right, operation.vertex_map)
        self.assertEqual(
            mirrored.edge_cells[mirrored_top],
            model.edge_cells[top],
        )
        self.assertEqual(
            mirrored.edge_cells[mirrored_right],
            model.edge_cells[right],
        )
        self.assertEqual(
            mirrored.edge_boundaries[mirrored_top],
            airfoil.name,
        )
        self.assertEqual(
            _directed_expansion(
                mirrored,
                (
                    operation.vertex_map[top[0]],
                    operation.vertex_map[top[1]],
                ),
            ),
            _directed_expansion(model, top),
        )
        self.assertEqual(
            _directed_geometry_points(
                mirrored,
                (
                    operation.vertex_map[top[0]],
                    operation.vertex_map[top[1]],
                ),
            ),
            tuple(
                (x, -y)
                for x, y in _directed_geometry_points(model, top)
            ),
        )
        self.assertEqual(
            _directed_geometry_points(
                mirrored,
                (
                    operation.vertex_map[right[0]],
                    operation.vertex_map[right[1]],
                ),
            ),
            tuple(
                (x, -y)
                for x, y in _directed_geometry_points(model, right)
            ),
        )

        mirrored_vertex = operation.vertex_map[source_link.vertex]
        mirrored_link = SpacingLink(
            mirrored_vertex,
            *sorted((mirrored_right, mirrored_top)),
        )
        self.assertIn(source_link, mirrored.spacing_links)
        self.assertIn(mirrored_link, mirrored.spacing_links)
        mirrored.validate()

    def test_source_topology_is_rebuilt_on_target(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v0", "v1"))
        model.add_block(edge_key("v1", "v2"))

        operation = symmetrize_model(model, axis="x")

        self.assertEqual(operation.source_block_count, 2)
        self.assertEqual(operation.mirrored_block_count, 2)
        self.assertEqual(len(operation.model.blocks), 4)
        mirrored_signatures = {
            frozenset(block.vertices)
            for block in operation.model.blocks[2:]
        }
        for block in operation.model.blocks[:2]:
            expected = frozenset(
                operation.vertex_map[value] for value in block.vertices
            )
            self.assertIn(expected, mirrored_signatures)

    def test_y_axis_reflects_x_coordinates(self) -> None:
        model = MeshModel()
        model.add_block(edge_key("v0", "v3"))
        model.move_vertex("v1", 1.25, 0.0)
        model.move_vertex("v2", 1.1, 1.0)

        operation = symmetrize_model(model, axis="y")

        mirrored_v1 = operation.model.vertices[
            operation.vertex_map["v1"]
        ]
        mirrored_v2 = operation.model.vertices[
            operation.vertex_map["v2"]
        ]
        self.assertEqual((mirrored_v1.x, mirrored_v1.y), (-1.25, 0.0))
        self.assertEqual((mirrored_v2.x, mirrored_v2.y), (-1.1, 1.0))
        operation.model.validate()

    def test_negative_half_can_drive_positive_half(self) -> None:
        model = MeshModel()
        lower = model.add_block(edge_key("v0", "v1"))
        lower_vertex = next(
            identifier
            for identifier in lower.vertices
            if model.vertices[identifier].y < 0.0
            and model.vertices[identifier].x > 0.0
        )
        vertex = model.vertices[lower_vertex]
        model.move_vertex(lower_vertex, vertex.x + 0.2, vertex.y - 0.3)

        operation = symmetrize_model(
            model, axis="x", source_side="negative"
        )

        self.assertIn(lower.id, {block.id for block in operation.model.blocks})
        mirrored_vertex = operation.model.vertices[
            operation.vertex_map[lower_vertex]
        ]
        self.assertEqual(
            (mirrored_vertex.x, mirrored_vertex.y),
            (
                model.vertices[lower_vertex].x,
                -model.vertices[lower_vertex].y,
            ),
        )

    def test_blocks_that_straddle_axis_are_rejected(self) -> None:
        model = MeshModel()
        model.move_vertex("v0", 0.0, -1.0)
        model.move_vertex("v1", 1.0, -1.0)

        with self.assertRaisesRegex(SymmetryError, "straddle"):
            symmetrize_model(model, axis="x")

    def test_curved_shared_axis_edge_is_rejected(self) -> None:
        model = MeshModel()
        axis_edge = edge_key("v0", "v1")
        model.edge_geometry[axis_edge] = EdgeGeometry(
            "spline", ((0.5, -0.2),)
        )
        model.validate()

        with self.assertRaisesRegex(
            SymmetryError, "lies on the x-axis"
        ):
            symmetrize_model(model, axis="x")

    def test_axis_boundary_is_pruned_and_conflicting_mirrored_link_skipped(
        self,
    ) -> None:
        model = MeshModel()
        axis_edge = edge_key("v0", "v1")
        source_edge = edge_key("v1", "v2")
        symmetry = model.add_boundary("centreline")
        model.set_edge_boundary(axis_edge, symmetry.name)
        source_link = model.add_spacing_link(axis_edge, source_edge)

        operation = symmetrize_model(model, axis="x")

        self.assertNotIn(axis_edge, operation.model.edge_boundaries)
        self.assertEqual(operation.skipped_spacing_link_count, 1)
        self.assertEqual(operation.model.spacing_links, {source_link})
        operation.model.validate()

    def test_cli_writes_a_new_session_without_changing_input(self) -> None:
        model = MeshModel()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "airfoil.json"
            save_session(model, source)
            original = source.read_text(encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                return_code = main([str(source), "--axis", "x"])

            destination = default_output_path(source)
            loaded = load_session(destination)
            input_after = source.read_text(encoding="utf-8")

        self.assertEqual(return_code, 0)
        self.assertIn("airfoil-symmetric.json", output.getvalue())
        self.assertEqual(input_after, original)
        self.assertEqual(len(loaded.blocks), 2)
        self.assertTrue(any(vertex.y < 0.0 for vertex in loaded.vertices.values()))


def _mapped_edge(
    edge: tuple[str, str], vertex_map: dict[str, str]
) -> tuple[str, str]:
    return edge_key(vertex_map[edge[0]], vertex_map[edge[1]])


def _directed_expansion(
    model: MeshModel, direction: tuple[str, str]
) -> float:
    return model.edge_expansion_in_direction(*direction)


def _directed_geometry_points(
    model: MeshModel, direction: tuple[str, str]
) -> tuple[tuple[float, float], ...]:
    current = edge_key(*direction)
    geometry = model.edge_geometry[current]
    return (
        geometry.points
        if current == direction
        else tuple(reversed(geometry.points))
    )


if __name__ == "__main__":
    unittest.main()
