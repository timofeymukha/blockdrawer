import gzip
import math
from pathlib import Path
import tempfile
import unittest

from blockdrawer.model import MeshModel
from blockdrawer.polymesh import (
    PolyMesh,
    PolyMeshError,
    block_cell_offsets,
    face_centre_and_area,
    locate_sets,
    read_label_list,
    read_set,
    written_set_names,
)
from tests.polymesh_fixture import (
    HEADER,
    extruded_mesh,
    foam_list,
    write_polymesh,
    write_set,
)


# Two rows of points; four cells whose shared sides lean, so the faces
# between them are not orthogonal.
POINTS = [
    (0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0), (2.0, 0.0),
    (0.0, 1.0), (0.8, 1.0), (1.4, 1.0), (1.7, 1.0), (2.0, 1.0),
]
CELLS = [(0, 1, 6, 5), (1, 2, 7, 6), (2, 3, 8, 7), (3, 4, 9, 8)]


def two_block_model() -> MeshModel:
    """Two blocks of two cells each: blockMesh numbers b0's cells 0-1, b1's 2-3."""
    model = MeshModel()
    model.set_edge_cells(("v0", "v1"), 2)
    model.set_edge_cells(("v1", "v2"), 1)
    model.add_block(("v1", "v2"))  # b1 inherits two cells away from the shared edge
    assert [math.prod(model.block_cell_counts(item)) for item in model.blocks] == [2, 2]
    return model


def centroid(polygon):
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return cx / (3 * area), cy / (3 * area)


def expected_non_orthogonality(owner_cell, neighbour_cell, p, q):
    own = centroid([POINTS[i] for i in CELLS[owner_cell]])
    nei = centroid([POINTS[i] for i in CELLS[neighbour_cell]])
    dx, dy = nei[0] - own[0], nei[1] - own[1]
    ex, ey = POINTS[q][0] - POINTS[p][0], POINTS[q][1] - POINTS[p][1]
    nx, ny = ey, -ex
    cosine = (dx * nx + dy * ny) / (math.hypot(dx, dy) * math.hypot(nx, ny))
    return math.degrees(math.acos(cosine))


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_label_lists_in_every_ascii_layout(self) -> None:
        long = self._write("owner", foam_list(
            [str(value) for value in range(12)], cls="labelList", name="owner", location="x",
        ))
        self.assertEqual(read_label_list(long), list(range(12)))
        short = self._write("short", HEADER.format(
            format="ascii", cls="labelList", location="x", name="short"
        ) + "3(4 5 6)\n")
        self.assertEqual(read_label_list(short), [4, 5, 6])
        uniform = self._write("uniform", HEADER.format(
            format="ascii", cls="labelList", location="x", name="uniform"
        ) + "4{7}\n")
        self.assertEqual(read_label_list(uniform), [7, 7, 7, 7])
        empty = self._write("empty", HEADER.format(
            format="ascii", cls="faceSet", location="x", name="empty"
        ) + "0()\n")
        self.assertEqual(read_set(empty), ("faceSet", []))

    def test_compressed_and_binary_files(self) -> None:
        text = foam_list(["1", "2"], cls="labelList", name="neighbour", location="x")
        with gzip.open(self.root / "neighbour.gz", "wt", encoding="utf-8") as stream:
            stream.write(text)
        self.assertEqual(read_label_list(self.root / "neighbour"), [1, 2])
        binary = self._write("bin", foam_list(
            ["1"], cls="labelList", name="bin", location="x", format="binary",
        ))
        with self.assertRaisesRegex(PolyMeshError, "ASCII"):
            read_label_list(binary)
        with self.assertRaisesRegex(PolyMeshError, "does not exist"):
            read_label_list(self.root / "missing")

    def test_points_and_faces_parse_on_demand(self) -> None:
        directory = write_polymesh(self.root, extruded_mesh(POINTS, CELLS))
        mesh = PolyMesh(directory)
        self.assertEqual(mesh.point(6), (0.8, 1.0, 0.0))
        self.assertEqual(mesh.point(16), (0.8, 1.0, 1.0))
        self.assertEqual(mesh.face_count, 3 + 10 + 8)
        self.assertEqual(mesh.cell_count, 4)
        self.assertEqual(mesh.neighbour, [1, 2, 3])
        # The first internal face separates cells 0 and 1 along points 1 and 6.
        self.assertEqual(sorted(mesh.face(0)), sorted([1, 6, 16, 11]))

    def test_face_centre_and_area_of_a_unit_square(self) -> None:
        centre, area = face_centre_and_area(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        )
        for got, want in zip(centre, (0.5, 0.5, 0.0)):
            self.assertAlmostEqual(got, want)
        for got, want in zip(area, (0.0, 0.0, 1.0)):
            self.assertAlmostEqual(got, want)

    def test_compact_face_list(self) -> None:
        directory = self.root / "constant" / "polyMesh"
        directory.mkdir(parents=True)
        (directory / "faces").write_text(
            HEADER.format(format="ascii", cls="faceCompactList", location="x", name="faces")
            + "3\n(\n0\n4\n7\n)\n\n7\n(\n0\n1\n2\n3\n4\n5\n6\n)\n"
        )
        mesh = PolyMesh(directory)
        self.assertEqual(mesh.face_count, 2)
        self.assertEqual(mesh.face(0), [0, 1, 2, 3])
        self.assertEqual(mesh.face(1), [4, 5, 6])


class LocateSetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.case = Path(self._directory.name)
        write_polymesh(self.case, extruded_mesh(POINTS, CELLS))
        self.model = two_block_model()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_block_offsets_follow_block_order(self) -> None:
        starts, names, total = block_cell_offsets(self.model)
        self.assertEqual((starts, names, total), ([0, 2], ["b0", "b1"], 4))

    def test_written_set_names(self) -> None:
        log = (
            "  <<Writing 596 non-orthogonal faces to set nonOrthoFaces\n"
            "  <<Writing 3 cells with high aspect ratio to set highAspectRatioCells\n"
            "  <<Writing 596 non-orthogonal faces to set nonOrthoFaces\n"
        )
        self.assertEqual(written_set_names(log), ["nonOrthoFaces", "highAspectRatioCells"])

    def test_faces_map_to_their_owner_block_with_checkmesh_angles(self) -> None:
        write_set(self.case, "nonOrthoFaces", "faceSet", [2, 1])
        (report,) = locate_sets(self.case, self.model, ["nonOrthoFaces"])
        self.assertEqual((report.kind, report.count, report.unmapped), ("face", 2, 0))
        by_block = {hits.block: hits for hits in report.blocks}
        self.assertEqual(set(by_block), {"b0", "b1"})
        # Face 1 lies between cells 1 (b0, the owner) and 2 (b1).
        expected = expected_non_orthogonality(1, 2, 2, 7)
        self.assertAlmostEqual(by_block["b0"].max_non_orthogonality, expected, places=9)
        self.assertAlmostEqual(
            by_block["b1"].max_non_orthogonality, expected_non_orthogonality(2, 3, 3, 8), places=9
        )
        self.assertAlmostEqual(report.max_non_orthogonality, max(
            expected, expected_non_orthogonality(2, 3, 3, 8)
        ))
        x0, y0, x1, y1 = by_block["b0"].bounds
        self.assertAlmostEqual(x0, 1.0)
        self.assertAlmostEqual(x1, 1.4)
        self.assertEqual((y0, y1), (0.0, 1.0))
        self.assertAlmostEqual(by_block["b0"].centroid[0], 1.2)
        # Worst first: equal counts, so the larger angle leads.
        self.assertGreaterEqual(
            report.blocks[0].max_non_orthogonality, report.blocks[1].max_non_orthogonality
        )
        data = report.to_data(worst=1)
        self.assertEqual(data["block_count"], 2)
        self.assertEqual(len(data["blocks"]), 1)
        self.assertEqual(data["blocks_omitted"], 1)

    def test_cells_and_points_map_to_blocks(self) -> None:
        write_set(self.case, "highAspectRatioCells", "cellSet", [3, 0])
        write_set(self.case, "nonAlignedEdges", "pointSet", [9])
        cells, points = locate_sets(
            self.case, self.model, ["highAspectRatioCells", "nonAlignedEdges"]
        )
        self.assertEqual(cells.kind, "cell")
        self.assertEqual({hits.block: hits.count for hits in cells.blocks}, {"b0": 1, "b1": 1})
        b1 = next(hits for hits in cells.blocks if hits.block == "b1")
        self.assertAlmostEqual(b1.centroid[0], (1.5 + 2.0 + 2.0 + 1.7) / 4)
        self.assertEqual(points.kind, "point")
        self.assertEqual([(hits.block, hits.count) for hits in points.blocks], [("b1", 1)])
        self.assertEqual(points.blocks[0].bounds, (2.0, 1.0, 2.0, 1.0))

    def test_a_case_from_another_session_is_refused(self) -> None:
        write_set(self.case, "nonOrthoFaces", "faceSet", [1])
        with self.assertRaisesRegex(PolyMeshError, "does not belong"):
            locate_sets(self.case, MeshModel(), ["nonOrthoFaces"])


if __name__ == "__main__":
    unittest.main()
