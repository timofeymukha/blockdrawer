from blockdrawer.model import EdgeKey, MeshModel


def edge_between(model: MeshModel, first: tuple[float, float],
                 second: tuple[float, float]) -> EdgeKey:
    wanted = {first, second}
    for current in model.edges():
        coordinates = {
            (model.vertices[identifier].x, model.vertices[identifier].y)
            for identifier in current
        }
        if coordinates == wanted:
            return current
    raise AssertionError(f"No edge between {first!r} and {second!r}")


def vertex_at(model: MeshModel, coordinates: tuple[float, float]) -> str:
    for identifier, vertex in model.vertices.items():
        if (vertex.x, vertex.y) == coordinates:
            return identifier
    raise AssertionError(f"No vertex at {coordinates!r}")


def build_ring_model() -> MeshModel:
    """Build a 3x3 block ring with the center block missing."""
    model = MeshModel()
    for first, second in (
        ((1.0, 0.0), (1.0, 1.0)),
        ((2.0, 0.0), (2.0, 1.0)),
        ((0.0, 1.0), (1.0, 1.0)),
        ((0.0, 2.0), (1.0, 2.0)),
        ((2.0, 1.0), (3.0, 1.0)),
        ((2.0, 2.0), (3.0, 2.0)),
        ((1.0, 2.0), (1.0, 3.0)),
    ):
        model.add_block(edge_between(model, first, second))
    model.validate()
    return model


def center_vertex_ids(model: MeshModel) -> list[str]:
    return [
        vertex_at(model, coordinates)
        for coordinates in (
            (1.0, 1.0),
            (2.0, 1.0),
            (2.0, 2.0),
            (1.0, 2.0),
        )
    ]


# Vertex names that sort against the x order, so blocks built from them by
# ``add_block_from_vertices`` would start at an arbitrary corner.
ADVERSARIAL_NAMES = ("k", "c", "m", "a", "z", "e", "q", "b", "d", "y", "f", "w")


def build_periodic_rows(
    names: tuple[str, ...] = ADVERSARIAL_NAMES,
    *,
    block_order: tuple[int, ...] = (0, 1, 2, 5, 4, 3),
    starts: tuple[int, ...] = (1, 0, 3, 3, 1, 2),
    z_cells: int = 2,
) -> MeshModel:
    """Two rows of three blocks over [0, 3] x [0, 1], periodic in y.

    The lower periodic edges belong to the first row and the upper ones to
    the second, the second row is listed in reverse and every block starts
    at the corner ``starts`` names, so neither the model's edge order nor
    the blocks' local directions line the two patches up. Cell counts 3, 4
    and 5 and a total grading of 3 in +x on every x edge make the faces
    differ, as they must for blockMesh to notice a mismatch.
    """
    from blockdrawer.domain import Block, Vertex
    from blockdrawer.model import edge_key

    grid = {(i, j): names[4 * j + i] for i in range(4) for j in range(3)}
    model = MeshModel(initialize=False)
    for (i, j), name in grid.items():
        model.vertices[name] = Vertex(name, float(i), 0.5 * j)
    quads = []
    for j in range(2):
        for i in range(3):
            corners = [grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]]
            shift = starts[3 * j + i]
            quads.append(tuple(corners[shift:] + corners[:shift]))
    for position, index in enumerate(block_order):
        model.blocks.append(Block(f"b{position}", quads[index]))
    for current in model.edges():
        model.edge_cells[current] = 4
    model.validate()
    for i in range(3):
        model.set_edge_cells(edge_key(grid[i, 0], grid[i + 1, 0]), 3 + i)
    for j in range(3):
        for i in range(3):
            first, second = grid[i, j], grid[i + 1, j]
            current = edge_key(first, second)
            model.set_edge_grading(
                current, "total_ratio", 3.0 if current == (first, second) else 1 / 3.0
            )
    model.add_boundary("lower")
    model.add_boundary("upper")
    model.set_boundary_type("lower", "cyclic", neighbour_patch="upper")
    for i in range(3):
        model.set_edge_boundary(edge_key(grid[i, 0], grid[i + 1, 0]), "lower")
        model.set_edge_boundary(edge_key(grid[i, 2], grid[i + 1, 2]), "upper")
    model.set_export_settings(z_cells, 0.0, 0.1, 1.0, "front", "patch", "back", "patch")
    return model
