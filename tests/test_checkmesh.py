import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from blockdrawer.checkmesh import (
    CheckMeshError,
    derive_checkmesh_command,
    fatal_errors,
    format_mesh_check,
    parse_block_mesh_log,
    parse_check_mesh_log,
    prepare_case,
    run_mesh_check,
)
from blockdrawer.model import MeshModel
from tests.polymesh_fixture import extruded_mesh, write_polymesh, write_set
from tests.test_polymesh import CELLS, POINTS, two_block_model


BLOCK_MESH_LOG = """
----------------
Mesh Information
----------------
  boundingBox: (0 0 0) (1.6 1 1)
  nPoints: 242
  nCells: 100
  nFaces: 420
  nInternalFaces: 180
----------------
Patches
----------------
  patch 0 (start: 180 size: 10) name: inlet
  patch 1 (start: 190 size: 100) name: zMin
  patch 2 (start: 290 size: 100) name: zMax
  patch 3 (start: 390 size: 30) name: defaultFaces

End
"""

CHECK_MESH_LOG = """
Mesh stats
    points:           242
    internal points:  0
    faces:            420
    internal faces:   180
    cells:            100
    faces per cell:   6
    boundary patches: 4

Checking topology...
    Boundary definition OK.
 ***Total number of faces on empty patches is not divisible by the number of cells in the mesh. Hence this mesh is not 1D or 2D.
    Number of regions: 1 (OK).

Checking geometry...
    Overall domain bounding box (0 0 0) (1.6 1 1)
    Mesh has 1 solution (non-empty) directions (0 0 1)
 ***Number of edges not aligned with or perpendicular to non-empty directions: 200
    Max cell openness = 2.20005e-16 OK.
    Max aspect ratio = 1 OK.
    Minimum face area = 0.00173954. Maximum face area = 0.322065.  Face area magnitudes OK.
    Min volume = 0.00173954. Max volume = 0.0315961.  Total volume = 1.3.  Cell volumes OK.
    Mesh non-orthogonality Max: 41.3995 average: 30.5054
    Non-orthogonality check OK.
    Max skewness = 1.40056 OK.

Failed 1 mesh checks.

End
"""

# The geometry part of a v2606 log that passed with 596 faces above 70
# degrees: before the parser read single-star lines this was ok with no
# problems and no count anywhere.
SEVERE_LOG = """
Checking geometry...
    Overall domain bounding box (-14.1996 -15 0) (15.8 15 1)
    Mesh has 2 solution (non-empty) directions (1 1 0)
    Max cell openness = 7.20898e-15 OK.
    Max aspect ratio = 156.7 OK.
    Minimum face area = 1.21863e-09. Maximum face area = 1.58317.  Face area magnitudes OK.
    Min volume = 1.21863e-09. Max volume = 0.648532.  Total volume = 803.363.  Cell volumes OK.
    Mesh non-orthogonality Max: 80.2199 average: 21.2153
   *Number of severely non-orthogonal (> 70 degrees) faces: 596.
    Non-orthogonality check OK.
  <<Writing 596 non-orthogonal faces to set nonOrthoFaces
    Face pyramids OK.
   *There are 3 faces with concave angles between consecutive edges. Min angle = 1.2 degrees
    Max skewness = 2.49898 OK.
Mesh OK.
End
"""

FATAL_LOG = """
--> FOAM FATAL ERROR: (openfoam-2606)
cannot find file "/case/system/fvSchemes"

    From virtual Foam::autoPtr<Foam::ISstream> Foam::fileOperations::uncollatedFileOperation::readStream(...)
    in file global/fileOperations/uncollatedFileOperation/uncollatedFileOperation.C at line 658.

FOAM exiting
"""


class ParsingTests(unittest.TestCase):
    def test_parse_block_mesh_log(self) -> None:
        stats, patches = parse_block_mesh_log(BLOCK_MESH_LOG)
        self.assertEqual(
            stats,
            {"points": 242, "cells": 100, "faces": 420, "internal_faces": 180},
        )
        self.assertEqual(len(patches), 4)
        self.assertEqual(patches[0], {"index": 0, "start": 180, "faces": 10, "name": "inlet"})
        self.assertEqual(patches[3]["name"], "defaultFaces")

    def test_parse_check_mesh_log(self) -> None:
        parsed = parse_check_mesh_log(CHECK_MESH_LOG)
        self.assertEqual(parsed["max_aspect_ratio"], 1.0)
        self.assertAlmostEqual(parsed["max_non_orthogonality"], 41.3995)
        self.assertAlmostEqual(parsed["average_non_orthogonality"], 30.5054)
        self.assertAlmostEqual(parsed["max_skewness"], 1.40056)
        self.assertAlmostEqual(parsed["min_volume"], 0.00173954)
        self.assertAlmostEqual(parsed["total_volume"], 1.3)
        self.assertEqual(parsed["bounding_box"], [0.0, 0.0, 0.0, 1.6, 1.0, 1.0])
        self.assertEqual(parsed["regions"], 1)
        self.assertEqual(parsed["solution_directions"], 1)
        self.assertEqual(parsed["mesh_stats"]["cells"], 100)
        self.assertEqual(parsed["mesh_stats"]["boundary_patches"], 4)
        self.assertEqual(parsed["failed_checks"], 1)
        self.assertIs(parsed["mesh_ok"], False)
        self.assertEqual(len(parsed["problems"]), 2)
        self.assertTrue(parsed["problems"][1].startswith("Number of edges not aligned"))

    def test_single_star_lines_and_the_severe_count(self) -> None:
        parsed = parse_check_mesh_log(SEVERE_LOG)
        self.assertIs(parsed["mesh_ok"], True)
        self.assertEqual(parsed["problems"], [])
        self.assertEqual(parsed["severe_non_orthogonal_faces"], 596)
        self.assertEqual(parsed["severe_non_orthogonality_threshold"], 70.0)
        self.assertEqual(len(parsed["warnings"]), 2)
        self.assertTrue(parsed["warnings"][0].startswith("Number of severely non-orthogonal"))
        self.assertTrue(parsed["warnings"][1].startswith("There are 3 faces with concave"))
        # No severe line after a non-orthogonality line means none.
        self.assertEqual(
            parse_check_mesh_log(CHECK_MESH_LOG)["severe_non_orthogonal_faces"], 0
        )
        self.assertNotIn("severe_non_orthogonal_faces", parse_check_mesh_log("nothing"))

    def test_mesh_ok_verdict(self) -> None:
        parsed = parse_check_mesh_log("Checking geometry...\n    Max skewness = 0.5 OK.\n\nMesh OK.\n\nEnd\n")
        self.assertIs(parsed["mesh_ok"], True)
        self.assertIsNone(parsed["failed_checks"])
        self.assertEqual(parsed["problems"], [])
        self.assertIsNone(parse_check_mesh_log("nothing useful")["mesh_ok"])

    def test_fatal_errors(self) -> None:
        errors = fatal_errors(FATAL_LOG)
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot find file", errors[0])
        self.assertEqual(fatal_errors("all good"), ())

    def test_derive_checkmesh_command(self) -> None:
        wrapped = [
            "apptainer", "exec", "image.sif", "bash", "-lc",
            'source bashrc && exec blockMesh "$@"', "blockMesh",
        ]
        derived = derive_checkmesh_command(wrapped)
        self.assertEqual(derived[-1], "checkMesh")
        self.assertIn("exec checkMesh", derived[-2])
        self.assertEqual(derive_checkmesh_command(["blockMesh"]), ["checkMesh"])
        self.assertIsNone(derive_checkmesh_command(["foamRun"]))
        self.assertIsNone(derive_checkmesh_command(["myblockMesher"]))


class RunTests(unittest.TestCase):
    """Drive the runner with a stub executable standing in for OpenFOAM."""

    def _write_stub(self, directory: Path, name: str, body: str) -> Path:
        script = directory / name
        script.write_text(
            "#!" + sys.executable + "\n" + body, encoding="utf-8"
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_prepare_case_writes_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = prepare_case(MeshModel(), Path(directory) / "case")
            for name in ("controlDict", "fvSchemes", "fvSolution", "blockMeshDict"):
                self.assertTrue((case / "system" / name).is_file(), name)
            self.assertTrue((case / "constant").is_dir())

    def test_missing_command_is_reported(self) -> None:
        environment = dict(os.environ)
        environment.pop("BLOCKMESH_COMMAND", None)
        with unittest.mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(CheckMeshError, "No blockMesh command"):
                run_mesh_check(MeshModel())

    def test_stub_success_path_parses_both_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = self._write_stub(root, "foamstub.py", f"""
import sys
application = sys.argv[1]
case = sys.argv[sys.argv.index('-case') + 1]
if application == 'blockMesh':
    import pathlib
    (pathlib.Path(case) / 'constant' / 'polyMesh').mkdir(parents=True, exist_ok=True)
    print({BLOCK_MESH_LOG!r})
else:
    print({CHECK_MESH_LOG!r})
""")
            result = run_mesh_check(
                MeshModel(),
                blockmesh_command=[sys.executable, str(stub), "blockMesh"],
                checkmesh_command=[sys.executable, str(stub), "checkMesh"],
                case_dir=root / "case",
            )
            self.assertTrue(result.block_mesh.ok)
            self.assertIsNotNone(result.check_mesh)
            self.assertFalse(result.ok)  # the stub log reports one failed check
            self.assertEqual(result.failed_checks, 1)
            self.assertEqual(result.mesh_stats["cells"], 100)
            self.assertEqual(result.mesh_stats["boundary_patches"], 4)
            self.assertEqual(result.patches[0]["name"], "inlet")
            self.assertEqual(len(result.problems), 2)
            self.assertTrue(result.kept)
            self.assertTrue((root / "case" / "log.blockMesh").is_file())
            self.assertTrue((root / "case" / "log.checkMesh").is_file())
            data = result.to_data()
            self.assertFalse(data["ok"])
            self.assertEqual(data["check_mesh"]["application"], "checkMesh")
            text = format_mesh_check(result)
            self.assertIn("blockMesh: OK (100 cells", text)
            self.assertIn("checkMesh: failed 1 check(s)", text)
            self.assertIn("*** Number of edges not aligned", text)
            self.assertIn("case kept at", text)

    def test_stub_failure_skips_checkmesh_and_reports_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = self._write_stub(root, "failstub.py", f"""
import sys
print({FATAL_LOG!r})
sys.exit(1)
""")
            result = run_mesh_check(
                MeshModel(),
                blockmesh_command=[sys.executable, str(stub)],
                checkmesh_command=[sys.executable, str(stub)],
                keep=False,
            )
            self.assertFalse(result.ok)
            self.assertIsNone(result.check_mesh)
            self.assertEqual(len(result.fatal_errors), 1)
            self.assertFalse(result.kept)
            self.assertFalse(Path(result.case_dir).exists())
            text = format_mesh_check(result)
            self.assertIn("blockMesh: FAILED (exit code 1)", text)
            self.assertIn("FATAL: cannot find file", text)
            self.assertIn("case deleted from", text)

    def test_derived_checkmesh_and_no_checkmesh_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = self._write_stub(root, "stub.py", f"""
import sys
print('blockMesh' if 'blockMesh' in sys.argv else 'checkMesh')
print({BLOCK_MESH_LOG!r} if 'blockMesh' in sys.argv else 'Mesh OK.')
""")
            command = [sys.executable, str(stub), "blockMesh"]
            derived = run_mesh_check(MeshModel(), blockmesh_command=command, keep=True)
            try:
                self.assertIsNotNone(derived.check_mesh)
                self.assertEqual(derived.check_mesh.command[2], "checkMesh")
                self.assertTrue(derived.ok)
                self.assertIs(derived.mesh_ok, True)
            finally:
                import shutil
                shutil.rmtree(derived.case_dir, ignore_errors=True)
            only = run_mesh_check(
                MeshModel(), blockmesh_command=command, run_checkmesh=False
            )
            self.assertIsNone(only.check_mesh)
            self.assertTrue(only.ok)

    def _fixture_stub(self, root: Path, log: str) -> list[str]:
        """A stub whose blockMesh copies a prepared polyMesh into the case."""
        source = root / "fixture"
        write_polymesh(source, extruded_mesh(POINTS, CELLS))
        write_set(source, "nonOrthoFaces", "faceSet", [1, 2])
        stub = self._write_stub(root, "meshstub.py", f"""
import shutil, sys
case = sys.argv[sys.argv.index('-case') + 1]
if sys.argv[1] == 'blockMesh':
    shutil.copytree({str(source / 'constant')!r}, case + '/constant', dirs_exist_ok=True)
    print({BLOCK_MESH_LOG!r})
else:
    print({log!r})
""")
        return [sys.executable, str(stub)]

    def test_sets_are_mapped_to_blocks_before_a_temporary_case_goes(self) -> None:
        log = SEVERE_LOG.replace("596", "2")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = self._fixture_stub(root, log)
            result = run_mesh_check(
                two_block_model(),
                blockmesh_command=[*stub, "blockMesh"],
                checkmesh_command=[*stub, "checkMesh"],
            )
            self.assertFalse(Path(result.case_dir).exists())
        self.assertTrue(result.ok)
        self.assertIsNone(result.set_error)
        (report,) = result.sets
        self.assertEqual(report.name, "nonOrthoFaces")
        self.assertEqual({hits.block for hits in report.blocks}, {"b0", "b1"})
        data = result.to_data(worst=1)
        self.assertEqual(data["severe_non_orthogonal_faces"], 2)
        self.assertEqual(data["sets"][0]["block_count"], 2)
        self.assertEqual(len(data["sets"][0]["blocks"]), 1)
        self.assertEqual(len(data["warnings"]), 2)
        text = format_mesh_check(result)
        self.assertIn("severely non-orthogonal faces (> 70 degrees): 2", text)
        self.assertIn("nonOrthoFaces: 2 faces in 2 block(s)", text)
        self.assertIn("* There are 3 faces with concave", text)
        self.assertEqual(text.count("Number of severely"), 0)

    def test_a_set_that_does_not_map_leaves_the_verdict_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = self._fixture_stub(root, SEVERE_LOG)
            result = run_mesh_check(
                MeshModel(),  # 100 cells, not the fixture's four
                blockmesh_command=[*stub, "blockMesh"],
                checkmesh_command=[*stub, "checkMesh"],
                case_dir=root / "case",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.sets, ())
            self.assertIn("does not belong", result.set_error)
            self.assertIn("sets: could not map", format_mesh_check(result))
            skipped = run_mesh_check(
                MeshModel(),
                blockmesh_command=[*stub, "blockMesh"],
                checkmesh_command=[*stub, "checkMesh"],
                case_dir=root / "case",
                locate=False,
            )
            self.assertIsNone(skipped.set_error)

    def test_the_case_asks_for_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = prepare_case(MeshModel(), Path(directory) / "case")
            control = (case / "system" / "controlDict").read_text()
            self.assertIn("writeFormat ascii;", control)
            self.assertIn("writeCompression off;", control)

    def test_unstartable_command_raises(self) -> None:
        with self.assertRaisesRegex(CheckMeshError, "Could not start blockMesh"):
            run_mesh_check(
                MeshModel(), blockmesh_command=["/nonexistent/blockMesh"]
            )


import unittest.mock  # noqa: E402  (used by RunTests)


if __name__ == "__main__":
    unittest.main()
