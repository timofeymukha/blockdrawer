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

    def test_unstartable_command_raises(self) -> None:
        with self.assertRaisesRegex(CheckMeshError, "Could not start blockMesh"):
            run_mesh_check(
                MeshModel(), blockmesh_command=["/nonexistent/blockMesh"]
            )


import unittest.mock  # noqa: E402  (used by RunTests)


if __name__ == "__main__":
    unittest.main()
