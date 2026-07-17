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
