import unittest

from blockdrawer.command_line import split_command_line


class CommandLineTests(unittest.TestCase):
    def test_windows_paths_keep_backslashes(self) -> None:
        tokens = split_command_line(
            r"import_curve C:\Users\agent\mesh\points.txt name=foil",
            windows=True,
        )
        self.assertEqual(
            tokens,
            [
                "import_curve",
                r"C:\Users\agent\mesh\points.txt",
                "name=foil",
            ],
        )

    def test_windows_paths_with_spaces_can_be_quoted(self) -> None:
        tokens = split_command_line(
            r'import_curve "C:\Users\agent\Mesh Cases\points.txt" name=foil',
            windows=True,
        )
        self.assertEqual(tokens[1], r"C:\Users\agent\Mesh Cases\points.txt")

    def test_windows_parsing_keeps_posix_wrapper_grouping(self) -> None:
        tokens = split_command_line(
            "wsl.exe bash -lc "
            "'source /opt/openfoam/etc/bashrc && exec blockMesh \"$@\"' blockMesh",
            windows=True,
        )
        self.assertEqual(
            tokens,
            [
                "wsl.exe",
                "bash",
                "-lc",
                'source /opt/openfoam/etc/bashrc && exec blockMesh "$@"',
                "blockMesh",
            ],
        )

    def test_posix_parsing_retains_normal_backslash_escaping(self) -> None:
        tokens = split_command_line(
            r"import_curve /tmp/mesh\ cases/points.txt name=foil",
            windows=False,
        )
        self.assertEqual(tokens[1], "/tmp/mesh cases/points.txt")


if __name__ == "__main__":
    unittest.main()
