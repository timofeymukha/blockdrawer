import json
from pathlib import Path
import tempfile
import unittest

from blockdrawer.app import BlockDrawerApp
from blockdrawer.config import (
    AppConfig,
    ConfigError,
    SHORTCUT_ACTIONS,
    default_config,
    default_config_path,
    from_data,
    load_config,
    save_config,
    shortcut_to_tk_sequences,
    to_data,
)


class ConfigTests(unittest.TestCase):
    def test_native_config_paths_are_platform_appropriate(self) -> None:
        home = Path("/users/example")

        self.assertEqual(
            default_config_path(platform="linux", home=home, environment={}),
            home / ".blockdrawer",
        )
        self.assertEqual(
            default_config_path(platform="darwin", home=home, environment={}),
            home / ".blockdrawer",
        )
        self.assertEqual(
            default_config_path(
                platform="win32",
                home=home,
                environment={"APPDATA": "C:/Users/example/AppData/Roaming"},
            ),
            Path("C:/Users/example/AppData/Roaming")
            / "BlockDrawer" / "config.json",
        )
        self.assertEqual(
            default_config_path(platform="win32", home=home, environment={}),
            home / "AppData" / "Roaming" / "BlockDrawer" / "config.json",
        )

    def test_defaults_cover_every_action_and_use_native_primary_modifier(self) -> None:
        linux = default_config("linux")
        macos = default_config("darwin")

        self.assertEqual(set(linux.shortcuts), set(SHORTCUT_ACTIONS))
        self.assertEqual(linux.shortcuts["save_session"], ("Ctrl+S",))
        self.assertEqual(macos.shortcuts["save_session"], ("Cmd+S",))
        self.assertEqual(macos.shortcuts["redo"], ("Cmd+Shift+Z",))
        self.assertEqual(linux.shortcuts["export_block_mesh_dict"], ("E",))
        self.assertEqual(macos.shortcuts["export_block_mesh_dict"], ("E",))
        self.assertEqual(linux.shortcuts["split_edge"], ("S",))
        self.assertEqual(
            linux.shortcuts["execute_split"], ("Enter", "NumpadEnter")
        )
        self.assertEqual(linux.shortcuts["combine_blocks"], ("Shift+S",))
        self.assertEqual(linux.shortcuts["link_spacing"], ("L",))
        self.assertEqual(linux.shortcuts["project"], ("P",))
        self.assertEqual(linux.shortcuts["toggle_geometry"], ("G",))
        self.assertEqual(linux.shortcuts["toggle_mesh_preview"], ("M",))
        self.assertEqual(linux.shortcuts["fit_view"], ())
        self.assertTrue(linux.show_block_mesh)
        self.assertTrue(linux.show_geometry)
        self.assertTrue(linux.show_vertex_ids)
        self.assertTrue(linux.show_edge_cell_counts)
        self.assertTrue(linux.show_edge_nodes)
        self.assertTrue(linux.show_edge_interpolation_points)
        self.assertFalse(linux.show_mesh_preview)
        self.assertEqual(linux.preview_coarsening, 1)

    def test_shortcut_notation_converts_to_tk_sequences(self) -> None:
        self.assertEqual(
            shortcut_to_tk_sequences("Ctrl+Shift+S"),
            ("<Control-Shift-KeyPress-S>",),
        )
        self.assertEqual(
            shortcut_to_tk_sequences("X"),
            ("<KeyPress-x>", "<KeyPress-X>"),
        )
        self.assertEqual(
            shortcut_to_tk_sequences("NumpadDelete"),
            ("<KP_Delete>",),
        )
        self.assertEqual(
            shortcut_to_tk_sequences("NumpadEnter"),
            ("<KP_Enter>",),
        )
        self.assertEqual(
            shortcut_to_tk_sequences("Shift+S"),
            ("<Shift-KeyPress-S>",),
        )
        self.assertEqual(
            shortcut_to_tk_sequences("Cmd+Z"),
            ("<Command-KeyPress-z>",),
        )

    def test_app_binds_custom_sequences_and_uses_first_menu_label(self) -> None:
        shortcuts = {action: () for action in SHORTCUT_ACTIONS}
        shortcuts["fit_view"] = ("Ctrl+F", "F11")
        app = BlockDrawerApp.__new__(BlockDrawerApp)
        app.preferences = AppConfig("auto", shortcuts)
        bindings: list[tuple[str, object]] = []
        app.root = type(
            "FakeRoot",
            (),
            {
                "bind": lambda _self, sequence, handler: bindings.append(
                    (sequence, handler)
                )
            },
        )()

        app._bind_configured_shortcuts()

        self.assertEqual(
            [sequence for sequence, _handler in bindings],
            ["<Control-KeyPress-f>", "<F11>"],
        )
        self.assertEqual(app._shortcut_label("fit_view"), "Ctrl+F")
        self.assertEqual(app._shortcut_label("cancel"), "")

    def test_missing_actions_inherit_defaults_and_empty_list_disables(self) -> None:
        data = to_data(default_config("linux"))
        data["ui"]["scale"] = 1.75
        data["ui"]["showBlockMesh"] = False
        data["ui"]["showGeometry"] = True
        data["ui"]["showVertexIds"] = False
        data["ui"]["showEdgeCellCounts"] = False
        data["ui"]["showEdgeNodes"] = False
        data["ui"]["showEdgeInterpolationPoints"] = False
        data["ui"]["showMeshPreview"] = True
        data["ui"]["previewCoarsening"] = 10
        data["shortcuts"] = {
            "save_session": ["Ctrl+Shift+W"],
            "fit_view": ["F"],
            "delete_edge": [],
        }

        config = from_data(data, platform="linux")

        self.assertEqual(config.ui_scale, "1.75")
        self.assertFalse(config.show_block_mesh)
        self.assertTrue(config.show_geometry)
        self.assertFalse(config.show_vertex_ids)
        self.assertFalse(config.show_edge_cell_counts)
        self.assertFalse(config.show_edge_nodes)
        self.assertFalse(config.show_edge_interpolation_points)
        self.assertTrue(config.show_mesh_preview)
        self.assertEqual(config.preview_coarsening, 10)
        self.assertEqual(config.shortcuts["save_session"], ("Ctrl+Shift+W",))
        self.assertEqual(config.shortcuts["fit_view"], ("F",))
        self.assertEqual(config.shortcuts["delete_edge"], ())
        self.assertEqual(config.shortcuts["open_session"], ("Ctrl+O",))
        self.assertEqual(config.shortcuts["project"], ("P",))
        self.assertEqual(config.shortcuts["toggle_geometry"], ("G",))
        self.assertEqual(config.shortcuts["toggle_mesh_preview"], ("M",))

    def test_missing_annotation_and_marker_fields_use_visibility_defaults(
        self,
    ) -> None:
        data = to_data(default_config("linux"))
        del data["ui"]["showVertexIds"]
        del data["ui"]["showEdgeCellCounts"]
        del data["ui"]["showEdgeNodes"]
        del data["ui"]["showEdgeInterpolationPoints"]
        del data["ui"]["showMeshPreview"]
        del data["ui"]["previewCoarsening"]

        config = from_data(data, platform="linux")

        self.assertTrue(config.show_vertex_ids)
        self.assertTrue(config.show_edge_cell_counts)
        self.assertTrue(config.show_edge_nodes)
        self.assertTrue(config.show_edge_interpolation_points)
        self.assertFalse(config.show_mesh_preview)
        self.assertEqual(config.preview_coarsening, 1)

    def test_file_round_trip_writes_readable_complete_json(self) -> None:
        config = default_config("linux").with_ui_scale(1.5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "preferences.json"

            save_config(config, path)
            parsed = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_config(path, platform="linux")

        self.assertEqual(parsed["format"], "blockDrawerConfig")
        self.assertEqual(parsed["version"], 3)
        self.assertEqual(parsed["ui"]["scale"], 1.5)
        self.assertTrue(parsed["ui"]["showBlockMesh"])
        self.assertTrue(parsed["ui"]["showGeometry"])
        self.assertTrue(parsed["ui"]["showVertexIds"])
        self.assertTrue(parsed["ui"]["showEdgeCellCounts"])
        self.assertTrue(parsed["ui"]["showEdgeNodes"])
        self.assertTrue(parsed["ui"]["showEdgeInterpolationPoints"])
        self.assertFalse(parsed["ui"]["showMeshPreview"])
        self.assertEqual(parsed["ui"]["previewCoarsening"], 1)
        self.assertEqual(set(parsed["shortcuts"]), set(SHORTCUT_ACTIONS))
        self.assertEqual(to_data(loaded), to_data(config))

    def test_version_one_default_export_binding_is_migrated_to_e(self) -> None:
        data = to_data(default_config("linux"))
        data["version"] = 1
        data["shortcuts"]["export_block_mesh_dict"] = ["Ctrl+E"]

        migrated = from_data(data, platform="linux")

        self.assertEqual(migrated.shortcuts["export_block_mesh_dict"], ("E",))

        data["shortcuts"]["export_block_mesh_dict"] = ["F8"]
        custom = from_data(data, platform="linux")
        self.assertEqual(custom.shortcuts["export_block_mesh_dict"], ("F8",))

        data["shortcuts"]["export_block_mesh_dict"] = ["Ctrl+E"]
        data["shortcuts"]["fit_view"] = ["E"]
        conflict_avoided = from_data(data, platform="linux")
        self.assertEqual(
            conflict_avoided.shortcuts["export_block_mesh_dict"], ("Ctrl+E",)
        )

    def test_version_two_config_inherits_mesh_preview_defaults(self) -> None:
        data = to_data(default_config("linux"))
        data["version"] = 2
        del data["ui"]["showMeshPreview"]
        del data["ui"]["previewCoarsening"]
        del data["shortcuts"]["toggle_mesh_preview"]

        migrated = from_data(data, platform="linux")

        self.assertFalse(migrated.show_mesh_preview)
        self.assertEqual(migrated.preview_coarsening, 1)
        self.assertEqual(migrated.shortcuts["toggle_mesh_preview"], ("M",))

    def test_app_loader_creates_defaults_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = BlockDrawerApp.__new__(BlockDrawerApp)
            app.config_path = Path(directory) / "config.json"
            app.config_warning = None

            config = app._load_preferences()

            self.assertTrue(app.config_path.exists())
            self.assertEqual(to_data(config), to_data(default_config()))
            self.assertIsNone(app.config_warning)
            self.assertTrue(app.config_write_enabled)

    def test_app_loader_leaves_invalid_file_untouched_and_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            invalid = "{ definitely not JSON }\n"
            path.write_text(invalid, encoding="utf-8")
            app = BlockDrawerApp.__new__(BlockDrawerApp)
            app.config_path = path
            app.config_warning = None

            config = app._load_preferences()

            self.assertEqual(to_data(config), to_data(default_config()))
            self.assertEqual(path.read_text(encoding="utf-8"), invalid)
            self.assertIn("Using defaults", app.config_warning)
            self.assertFalse(app.config_write_enabled)

    def test_invalid_scale_action_key_shortcut_and_conflict_are_rejected(self) -> None:
        data = to_data(default_config("linux"))
        data["ui"]["scale"] = 10
        with self.assertRaisesRegex(ConfigError, "between"):
            from_data(data, platform="linux")

        data = to_data(default_config("linux"))
        data["ui"]["showGeometry"] = "yes"
        with self.assertRaisesRegex(ConfigError, "true or false"):
            from_data(data, platform="linux")

        data = to_data(default_config("linux"))
        data["ui"]["previewCoarsening"] = 0
        with self.assertRaisesRegex(ConfigError, "positive integer"):
            from_data(data, platform="linux")

        data = to_data(default_config("linux"))
        data["shortcuts"]["not_an_action"] = ["F1"]
        with self.assertRaisesRegex(ConfigError, "Unknown shortcut action"):
            from_data(data, platform="linux")

        data = to_data(default_config("linux"))
        data["shortcuts"]["fit_view"] = ["Ctrl+Banana"]
        with self.assertRaisesRegex(ConfigError, "unsupported key"):
            from_data(data, platform="linux")

        data = to_data(default_config("linux"))
        data["shortcuts"]["fit_view"] = ["Ctrl+S"]
        with self.assertRaisesRegex(ConfigError, "conflicts"):
            from_data(data, platform="linux")


if __name__ == "__main__":
    unittest.main()
