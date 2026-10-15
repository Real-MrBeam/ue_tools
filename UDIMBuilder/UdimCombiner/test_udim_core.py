import binascii
import json
import struct
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

import UdimCombiner
import udim_core
from udim_core import archive_config, build_plan, checkout_file_for_edit, discover_texture_names, ensure_config_exclusive_open, find_config_files, load_config_fields, reorder_texture_names, run_configs, save_config_fields, source_texture_name, unreal_destination_from_content_folder


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc & 0xFFFFFFFF)


def write_png(path: Path, color_type: int = 2, srgb: bool = False, bit_depth: int = 8) -> None:
    channels = {2: 3, 3: 1, 6: 4}[color_type]
    raw_row = b"\x00" + (b"\x00" if color_type == 3 else b"\x7f" * channels * (bit_depth // 8))
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, bit_depth, color_type, 0, 0, 0))
    if srgb:
        payload += _png_chunk(b"sRGB", b"\x00")
    if color_type == 3:
        payload += _png_chunk(b"PLTE", b"\x7f\x7f\x7f")
    payload += _png_chunk(b"IDAT", zlib.compress(raw_row))
    payload += _png_chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_config(root: Path, textures: list[str], extra: str = "", set_name: str = "Test") -> Path:
    config_path = root / f"{set_name}Textures.txt"
    config_path.write_text(
        f"#udim_set_name: {set_name}\n"
        "#original_files_path: source\n"
        "#udim_files_path: output\n"
        "#unreal_destination_path: /Game/Test/UDIM\n"
        f"{extra}"
        "\n#textures_start\n"
        + "\n".join(textures)
        + "\n#textures_end\n",
        encoding="utf-8",
    )
    return config_path


def write_texture(root: Path, texture_name: str, missing: set[str] | None = None, srgb_channel: str | None = None) -> None:
    missing = missing or set()
    for channel in ("C", "N", "ORS"):
        if channel in missing:
            continue
        write_png(root / "source" / f"T_{texture_name}_{channel}.png", srgb=channel == srgb_channel)


def write_deprecated_placeholders(root: Path, bit_depth: int = 8) -> dict[str, Path]:
    suffix = "_16" if bit_depth == 16 else ""
    placeholders = {channel: root / f"T_Deprecated_{channel}{suffix}.png" for channel in ("C", "N", "ORS")}
    for channel, color_type in (("C", 2), ("N", 6), ("ORS", 3)):
        write_png(placeholders[channel], color_type=6 if bit_depth == 16 else color_type, bit_depth=bit_depth)
    return placeholders


class UdimCoreTests(unittest.TestCase):
    def _fake_p4(self, client: Mock, exception_type: type[Exception]):
        module = types.ModuleType("P4")
        module.P4 = lambda: client
        module.P4Exception = exception_type
        return patch.dict("sys.modules", {"P4": module})

    def test_new_config_is_added_as_exclusive_text(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.connected.return_value = True
            client.run_fstat.return_value = []
            client.run_add.return_value = [{"action": "add"}]
            with self._fake_p4(client, FakeP4Exception):
                self.assertTrue(ensure_config_exclusive_open(config))
            client.run_add.assert_called_once_with("-t", "text+l", str(config.resolve()))

    def test_existing_config_with_wrong_type_is_reopened_as_exclusive_text(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/TestTextures.txt", "headType": "text", "action": "edit", "type": "text"}]
            client.run_reopen.return_value = [{"action": "edit"}]
            with self._fake_p4(client, FakeP4Exception):
                self.assertTrue(ensure_config_exclusive_open(config))
            client.run_reopen.assert_called_once_with("-t", "text+l", str(config.resolve()))

    def test_exclusive_config_type_is_left_alone(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/TestTextures.txt", "headType": "text+l", "action": "edit", "type": "text+l"}]
            with self._fake_p4(client, FakeP4Exception):
                self.assertTrue(ensure_config_exclusive_open(config))
            client.run_add.assert_not_called()
            client.run_reopen.assert_not_called()

    def test_new_config_stays_local_when_perforce_is_unavailable(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.connect.side_effect = FakeP4Exception("offline")
            client.connected.return_value = False
            with self._fake_p4(client, FakeP4Exception):
                self.assertFalse(ensure_config_exclusive_open(config))
            client.run_add.assert_not_called()

    def test_checkout_for_edit_blocks_other_workspace_and_names_owner(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.user = "artist"
            client.client = "my_workspace"
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/TestTextures.txt"}]
            client.run_opened.return_value = [{"user": "another_artist", "client": "their_workspace"}]
            with self._fake_p4(client, FakeP4Exception):
                with self.assertRaisesRegex(RuntimeError, "another_artist@their_workspace"):
                    checkout_file_for_edit(config)
            client.run_edit.assert_not_called()

    def test_checkout_for_edit_opens_only_unowned_tracked_file(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            config = write_config(Path(temp_dir), ["Leaf"])
            client = Mock()
            client.user = "artist"
            client.client = "my_workspace"
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/TestTextures.txt"}]
            client.run_opened.side_effect = [
                [{"user": "artist", "client": "my_workspace"}],
                [],
            ]
            with self._fake_p4(client, FakeP4Exception):
                checkout_file_for_edit(config)
                checkout_file_for_edit(config)
            client.run_edit.assert_called_once_with(str(config.resolve()))

    def test_source_checkout_requires_confirmation_only_when_needed(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "T_Leaf_C.png"
            write_png(source)
            client = Mock()
            client.user = "artist"
            client.client = "my_workspace"
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf_C.png"}]
            client.run_opened.side_effect = [[], [], [{"user": "artist", "client": "my_workspace"}]]
            confirm = Mock(side_effect=[False, True])
            with self._fake_p4(client, FakeP4Exception):
                self.assertFalse(checkout_file_for_edit(source, confirm_checkout=confirm))
                self.assertTrue(checkout_file_for_edit(source, confirm_checkout=confirm))
                self.assertTrue(checkout_file_for_edit(source, confirm_checkout=confirm))
            self.assertEqual(confirm.call_count, 2)
            client.run_edit.assert_called_once_with(str(source.resolve()))

    def test_untracked_source_edit_can_be_declined(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "T_Leaf_C.png"
            write_png(source)
            client = Mock()
            client.connected.return_value = True
            client.run_fstat.return_value = []
            confirm = Mock(return_value=False)
            with self._fake_p4(client, FakeP4Exception):
                self.assertFalse(checkout_file_for_edit(source, confirm_untracked=confirm))
            confirm.assert_called_once_with(source.resolve())
            client.run_edit.assert_not_called()

    def test_local_manifest_build_does_not_checkout_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            manifest = root / "Saved" / "UDIMBuildManifest.json"
            with patch.object(udim_core, "DEFAULT_MANIFEST_PATH", manifest), \
                    patch.object(udim_core, "checkout_file_for_edit", side_effect=AssertionError("unexpected checkout")) as checkout:
                results, written_manifest = run_configs([config], sync=False)
            self.assertTrue(results[0].success)
            self.assertEqual(written_manifest, manifest.resolve())
            self.assertTrue(manifest.is_file())
            self.assertFalse((root / "output" / "T_Test.udim-manifest.json").exists())
            checkout.assert_not_called()

    def test_independent_sets_keep_their_saved_tile_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            red = write_config(root, ["Bark"], "#deprecated_tiles: Bark\n", set_name="RedForest")
            pink = write_config(root, ["Sand"], set_name="PinkDesert")
            write_texture(root, "Bark")
            write_texture(root, "Sand")
            placeholders = write_deprecated_placeholders(root)

            self.assertEqual(find_config_files(root), [pink, red])
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                results, manifest_path = run_configs(
                    [red, pink], sync=False, manifest_path=root / "Saved" / "UDIMBuildManifest.json"
                )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(all(result.success for result in results))
            self.assertEqual([item["set_name"] for item in manifest["sets"]], ["RedForest", "PinkDesert"])
            self.assertTrue(manifest["sets"][0]["tiles"][0]["deprecated"])
            self.assertFalse(manifest["sets"][1]["tiles"][0]["deprecated"])
            self.assertTrue((root / "output" / "T_RedForest_C.1001.png").exists())
            self.assertTrue((root / "output" / "T_PinkDesert_C.1001.png").exists())

    def test_perforce_up_to_date_warnings_do_not_block_build(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_diff.return_value = []

            def warning_when_not_opened(*_args):
                client.warnings = ["File(s) not opened."]
                if client.exception_level == 2:
                    raise FakeP4Exception("File(s) not opened.")
                return []

            def warning_when_up_to_date(*_args):
                client.warnings = ["File(s) up-to-date."]
                if client.exception_level == 2:
                    raise FakeP4Exception("File(s) up-to-date.")
                return []

            client.run_opened.side_effect = warning_when_not_opened
            client.run_sync.side_effect = warning_when_up_to_date
            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertTrue(results[0].success)
            self.assertEqual(client.exception_level, 1)
            self.assertEqual(client.run_sync.call_count, 3)

    def test_perforce_sync_restores_tracked_source_missing_locally(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf", missing={"N"})
            missing = root / "source" / "T_Leaf_N.png"
            client = Mock()
            client.connected.return_value = True
            client.run_opened.return_value = []
            client.run_diff.return_value = []

            def fstat(*paths):
                if "..." in str(paths[0]):
                    return [{"depotFile": "//art/T_Leaf_N.png", "clientFile": str(missing), "headAction": "edit"}]
                return [{"depotFile": f"//art/{Path(paths[0]).name}"}]

            def sync(source):
                if Path(source) == missing:
                    write_png(missing)
                return []

            client.run_fstat.side_effect = fstat
            client.run_sync.side_effect = sync
            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertTrue(results[0].success)
            self.assertTrue(missing.is_file())
            self.assertIn("T_Leaf_N.png", [Path(call.args[0]).name for call in client.run_sync.call_args_list])

    def test_perforce_opened_source_blocks_all_sync_and_output(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_diff.return_value = []
            client.run_opened.side_effect = [
                [{"user": "another_artist", "client": "their_workspace"}], [], [],
            ]

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertEqual([item.code for item in results[0].diagnostics], ["p4_opened"])
            self.assertIn("another_artist@their_workspace", results[0].diagnostics[0].message)
            client.run_sync.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_perforce_own_checkout_builds_from_local_file_without_syncing_it(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.user = "artist"
            client.client = "art_workspace"
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_diff.return_value = []
            client.run_opened.side_effect = lambda _all, source: (
                [{"user": "artist", "client": "art_workspace"}]
                if Path(source).name == "T_Leaf_C.png" else []
            )

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertTrue(results[0].success)
            synced = [Path(call.args[0]).name for call in client.run_sync.call_args_list]
            self.assertEqual(synced, ["T_Leaf_N.png", "T_Leaf_ORS.png"])
            self.assertTrue((root / "output").exists())

    def test_perforce_same_user_other_workspace_still_blocks_build(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.user = "artist"
            client.client = "art_workspace"
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_diff.return_value = []
            client.run_opened.side_effect = lambda _all, source: (
                [{"user": "artist", "client": "other_workspace"}]
                if Path(source).name == "T_Leaf_C.png" else []
            )

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertIn("artist@other_workspace", results[0].diagnostics[0].message)
            client.run_sync.assert_not_called()

    def test_perforce_own_and_other_checkout_still_blocks_build(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.user = "artist"
            client.client = "art_workspace"
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_diff.return_value = []
            client.run_opened.side_effect = lambda _all, source: (
                [
                    {"user": "artist", "client": "art_workspace"},
                    {"user": "other_artist", "client": "their_workspace"},
                ] if Path(source).name == "T_Leaf_C.png" else []
            )

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertIn("other_artist@their_workspace", results[0].diagnostics[0].message)
            client.run_sync.assert_not_called()

    def test_perforce_error_reports_cause_once_and_stops_before_output(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.side_effect = FakeP4Exception("Permission denied")

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertEqual(len(results[0].diagnostics), 1)
            self.assertIn("Permission denied", results[0].diagnostics[0].message)
            client.run_sync.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_perforce_unopened_local_edits_are_not_overwritten(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_opened.return_value = []
            client.run_diff.return_value = [str(root / "source" / "T_Leaf_C.png")]

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertEqual([item.code for item in results[0].diagnostics], ["p4_local_modified"])
            self.assertIn("not checked out", results[0].diagnostics[0].message)
            self.assertIn("this workspace", results[0].diagnostics[0].message)
            client.run_sync.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_perforce_untracked_source_requires_explicit_local_build(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = []

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertEqual([item.code for item in results[0].diagnostics], ["p4_untracked"])
            client.run_sync.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_perforce_real_sync_error_is_reported_once(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            client = Mock()
            client.exception_level = 2
            client.errors = []
            client.warnings = []
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_opened.return_value = []
            client.run_diff.return_value = []
            client.run_sync.side_effect = FakeP4Exception("Can't clobber writable file")

            with self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertEqual([item.code for item in results[0].diagnostics], ["p4_sync_failed"])
            self.assertIn("Can't clobber writable file", results[0].diagnostics[0].message)
            self.assertEqual(client.run_sync.call_count, 1)
            self.assertFalse((root / "output").exists())

    def test_source_scan_groups_channel_files_into_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            write_png(source / "T_Bark_C.png")
            write_png(source / "nested" / "T_Bark_N.png")
            write_png(source / "T_Stone_ORS.PNG")
            write_png(source / "T_Ignore_X.png")
            write_png(source / "T_Bark_C.1001.png")

            self.assertEqual(discover_texture_names(source), ["Bark", "Stone"])
            self.assertEqual(source_texture_name(source / "T_Bark_N.png"), "Bark")
            self.assertEqual(source_texture_name(source / "T_Stone_ORS.PNG"), "Stone")
            self.assertIsNone(source_texture_name(source / "T_Ignore_X.png"))
            self.assertIsNone(source_texture_name(source / "T_Bark_C.1001.png"))
            with self.assertRaises(ValueError):
                discover_texture_names(source / "missing")

            config = write_config(Path(temp_dir), ["Stone"])
            plan = build_plan(config)
            self.assertEqual(plan.tiles[0].sources["ORS"], (source / "T_Stone_ORS.PNG").resolve())

    def test_unreal_destination_from_content_folder_stays_within_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            content = Path(temp_dir) / "MyProject" / "Content"
            destination = content / "Environment" / "Foliage" / "_UDIM"
            destination.mkdir(parents=True)

            self.assertEqual(unreal_destination_from_content_folder(content, destination), "/Game/Environment/Foliage/_UDIM")
            self.assertEqual(unreal_destination_from_content_folder(content, content), "/Game/")
            with self.assertRaises(ValueError):
                unreal_destination_from_content_folder(content, content.parent / "Other")

    def test_config_discovery_prefers_subfolder_but_accepts_legacy_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "Configs"
            config_dir.mkdir()
            legacy = root / "WaterTextures.txt"
            moved = config_dir / legacy.name
            other = root / "BuildingTextures.txt"
            legacy.write_text("legacy", encoding="utf-8")
            moved.write_text("new", encoding="utf-8")
            other.write_text("legacy only", encoding="utf-8")

            with patch.object(udim_core, "SCRIPT_DIR", root), patch.object(udim_core, "CONFIG_DIR", config_dir):
                self.assertEqual(find_config_files(), [other, moved])
                self.assertEqual(find_config_files(root), [other, legacy])
            with patch.object(UdimCombiner, "SCRIPT_DIR", root), patch.object(UdimCombiner, "CONFIG_DIR", config_dir):
                self.assertEqual(UdimCombiner._resolve_configs(["WaterTextures.txt"]), [moved])
                self.assertEqual(UdimCombiner._resolve_configs(["BuildingTextures.txt"]), [other])

    def test_config_editor_preserves_unknown_directives_and_detects_concurrent_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(
                root,
                ["Leaf"],
                "#unreal_asset_C: T_Test_UDIM_C\n"
                "#unreal_asset_N: T_Test_UDIM_N\n"
                "#unreal_asset_ORS: T_Test_UDIM_ORS\n"
                "#artist_note: keep this\n",
            )
            config.write_bytes(b"\xef\xbb\xbf" + config.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            fields, textures, original = load_config_fields(config)
            save_config_fields(config, fields, textures, expected_bytes=original)
            self.assertEqual(config.read_bytes(), original)
            fields["original_files_path"] = "../RevisedSource"
            fields["unreal_asset_ORS"] = "T_Test_UDIM_ORM"

            save_config_fields(config, fields, [*textures, "Bark"], expected_bytes=original)

            saved_fields, saved_textures, _ = load_config_fields(config)
            self.assertEqual(saved_fields["original_files_path"], "../RevisedSource")
            self.assertEqual(saved_fields["unreal_asset_ORS"], "T_Test_UDIM_ORM")
            self.assertEqual(saved_textures, ["Leaf", "Bark"])
            self.assertIn("#artist_note: keep this", config.read_text(encoding="utf-8"))
            self.assertTrue(config.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", config.read_bytes())
            with self.assertRaises(RuntimeError):
                save_config_fields(config, fields, saved_textures, expected_bytes=original)

            new_config = root / "CopyTextures.txt"
            save_config_fields(new_config, fields, saved_textures, expected_bytes=b"")
            self.assertTrue(new_config.is_file())

    def test_archived_set_is_removed_from_discovery_without_touching_textures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")

            archived = archive_config(config)

            self.assertEqual(archived, root / "Unused" / "TestTextures.txt")
            self.assertTrue(archived.is_file())
            self.assertEqual(find_config_files(root), [])
            self.assertTrue((root / "source" / "T_Leaf_C.png").is_file())
            write_config(root, ["Replacement"])
            with self.assertRaises(FileExistsError):
                archive_config(config)
            self.assertTrue(config.is_file())

    def test_reordered_texture_names_define_udim_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["First", "Second", "Third"])
            for name in ("First", "Second", "Third"):
                write_texture(root, name)
            fields, _, original = load_config_fields(config)

            save_config_fields(config, fields, ["Third", "First", "Second"], expected_bytes=original)

            _, saved_names, _ = load_config_fields(config)
            self.assertEqual(saved_names, ["Third", "First", "Second"])
            self.assertEqual(
                [(tile.udim, tile.texture_name) for tile in build_plan(config).tiles],
                [(1001, "Third"), (1002, "First"), (1003, "Second")],
            )

    def test_preview_move_updates_udims_without_saving_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["First", "Second", "Third"])
            for name in ("First", "Second", "Third"):
                write_texture(root, name)
            original = config.read_bytes()

            draft_names = reorder_texture_names(["First", "Second", "Third"], 0, 2)
            draft = build_plan(config, texture_names_override=draft_names)

            self.assertEqual(draft_names, ["Second", "Third", "First"])
            self.assertEqual([(tile.udim, tile.texture_name) for tile in draft.tiles], [
                (1001, "Second"), (1002, "Third"), (1003, "First")
            ])
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(reorder_texture_names(draft_names, 2, 0), ["First", "Second", "Third"])
            across_rows = reorder_texture_names([f"Tile{i}" for i in range(12)], 0, 11)
            self.assertEqual((across_rows[0], across_rows[11]), ("Tile1", "Tile0"))
            with self.assertRaises(IndexError):
                reorder_texture_names(draft_names, 0, 3)

    def test_missing_tile_does_not_shift_following_udim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["First", "Second"])
            write_texture(root, "First", missing={"C"})
            write_texture(root, "Second")

            plan = build_plan(config)

            self.assertFalse(plan.is_valid)
            self.assertEqual([tile.udim for tile in plan.tiles], [1001, 1002])
            self.assertEqual(plan.tiles[1].targets["C"].name, "T_Test_C.1002.png")

    def test_deprecated_tile_uses_shared_placeholder_without_source_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Leaf"], "#deprecated_tiles: Old\n")
            write_texture(root, "Leaf")
            placeholders = write_deprecated_placeholders(root)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
                results, manifest_path = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            self.assertTrue(plan.is_valid)
            self.assertTrue(plan.tiles[0].deprecated)
            self.assertFalse(plan.tiles[1].deprecated)
            self.assertEqual(plan.tiles[0].sources, placeholders)
            self.assertTrue(results[0].success)
            for channel in ("C", "N", "ORS"):
                self.assertEqual((root / "output" / f"T_Test_{channel}.1001.png").read_bytes(), placeholders[channel].read_bytes())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["sets"][0]["tiles"][0]["deprecated"])

    def test_deprecated_tile_previews_original_but_builds_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old"], "#deprecated_tiles: Old\n")
            write_texture(root, "Old")
            placeholders = write_deprecated_placeholders(root)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
                results, manifest_path = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            self.assertTrue(plan.is_valid)
            self.assertTrue(results[0].success)
            for channel in ("C", "N", "ORS"):
                original = root / "source" / f"T_Old_{channel}.png"
                self.assertEqual(plan.tiles[0].original_source(channel), original)
                self.assertEqual(plan.tiles[0].preview_source(channel), original)
                self.assertEqual(plan.tiles[0].sources[channel], placeholders[channel])
                self.assertEqual((root / "output" / f"T_Test_{channel}.1001.png").read_bytes(), placeholders[channel].read_bytes())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for channel in ("C", "N", "ORS"):
                self.assertEqual(manifest["sets"][0]["tiles"][0]["channels"][channel]["source_path"], str(placeholders[channel]))

    def test_deprecated_placeholder_is_not_synced_from_perforce(self) -> None:
        class FakeP4Exception(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Leaf"], "#deprecated_tiles: Old\n")
            write_texture(root, "Leaf")
            placeholders = write_deprecated_placeholders(root)
            client = Mock()
            client.user = "artist"
            client.client = "my_workspace"
            client.connected.return_value = True
            client.run_fstat.return_value = [{"depotFile": "//art/T_Leaf.png"}]
            client.run_opened.return_value = []
            client.run_diff.return_value = []
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders), self._fake_p4(client, FakeP4Exception):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")
            self.assertTrue(results[0].success)
            self.assertEqual(client.run_sync.call_count, 3)
            self.assertTrue(all("T_Leaf_" in call.args[0] for call in client.run_sync.call_args_list))

    def test_all_deprecated_set_needs_no_perforce_source_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old"], "#deprecated_tiles: Old\n")
            (root / "source").mkdir()
            placeholders = write_deprecated_placeholders(root)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders), patch.dict("sys.modules", {"P4": None}):
                results, _ = run_configs([config], sync=True, manifest_path=root / "manifest.json")
            self.assertTrue(results[0].success)

    def test_deprecated_names_round_trip_without_changing_tile_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Leaf"], "#deprecated_tiles: Old\n#artist_note: keep this\n")
            fields, names, original = load_config_fields(config)
            self.assertEqual(fields["deprecated_tiles"], "Old")
            save_config_fields(config, fields, ["Leaf", "Old"], expected_bytes=original)
            saved_fields, saved_names, _ = load_config_fields(config)
            self.assertEqual(saved_names, ["Leaf", "Old"])
            self.assertEqual(saved_fields["deprecated_tiles"], "Old")
            self.assertIn("#artist_note: keep this", config.read_text(encoding="utf-8"))
            saved_fields["deprecated_tiles"] = ""
            save_config_fields(config, saved_fields, saved_names)
            self.assertNotIn("#deprecated_tiles:", config.read_text(encoding="utf-8"))

    def test_missing_deprecated_placeholder_reports_one_blocking_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Older"], "#deprecated_tiles: Old, Older\n")
            (root / "source").mkdir()
            placeholders = write_deprecated_placeholders(root)
            placeholders["N"].unlink()
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
            self.assertFalse(plan.is_valid)
            self.assertEqual([item.code for item in plan.diagnostics], ["deprecated_placeholder_missing"])
            self.assertEqual(plan.diagnostics[0].channel, "N")

    def test_metadata_and_supported_png_types_do_not_block_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf", srgb_channel="C")
            write_png(root / "source" / "T_Leaf_N.png", color_type=6, srgb=True)
            write_png(root / "source" / "T_Leaf_ORS.png", color_type=3, srgb=True)

            plan = build_plan(config)

            self.assertTrue(plan.is_valid)
            self.assertEqual(plan.diagnostics, [])
            results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")
            self.assertTrue(results[0].success)
            for channel in ("C", "N", "ORS"):
                self.assertEqual(
                    (root / "source" / f"T_Leaf_{channel}.png").read_bytes(),
                    (root / "output" / f"T_Test_{channel}.1001.png").read_bytes(),
                )

    def test_first_tile_sets_bit_depth_for_each_channel(self) -> None:
        for first_depth, second_depth in ((8, 16), (16, 8)):
            with self.subTest(first_depth=first_depth), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = write_config(root, ["Leaf", "Bark"])
                write_texture(root, "Leaf")
                write_texture(root, "Bark")
                write_png(root / "source" / "T_Leaf_C.png", bit_depth=first_depth)
                write_png(root / "source" / "T_Bark_C.png", bit_depth=second_depth)

                plan = build_plan(config)

                mismatches = [item for item in plan.diagnostics if item.code == "bit_depth_mismatch"]
                self.assertEqual(len(mismatches), 1)
                self.assertEqual((mismatches[0].texture_name, mismatches[0].channel), ("Bark", "C"))
                self.assertIn(f"{first_depth}-bit", mismatches[0].message)
                self.assertIn(f"{second_depth}-bit", mismatches[0].message)
                results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")
                self.assertFalse(results[0].success)
                self.assertFalse((root / "output").exists())

    def test_different_channels_can_have_different_uniform_bit_depths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf", "Bark"])
            write_texture(root, "Leaf")
            write_texture(root, "Bark")
            for name in ("Leaf", "Bark"):
                write_png(root / "source" / f"T_{name}_N.png", bit_depth=16)

            plan = build_plan(config)

            self.assertTrue(plan.is_valid)
            self.assertEqual([item.code for item in plan.diagnostics], [])

    def test_first_live_tile_selects_placeholder_depth_per_channel(self) -> None:
        for depths in ((8, 8, 8), (16, 16, 16), (16, 8, 16)):
            with self.subTest(depths=depths), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = write_config(root, ["Old", "Leaf", "Older"], "#deprecated_tiles: Old, Older\n")
                write_texture(root, "Old")
                write_texture(root, "Leaf")
                placeholders = write_deprecated_placeholders(root)
                placeholders_16 = write_deprecated_placeholders(root, bit_depth=16)
                for channel, depth in zip(udim_core.CHANNELS, depths):
                    write_png(root / "source" / f"T_Leaf_{channel}.png", bit_depth=depth)
                    write_png(root / "source" / f"T_Old_{channel}.png", bit_depth=24 - depth)

                with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                    plan = build_plan(config)
                    results, manifest_path = run_configs([config], sync=False, manifest_path=root / "manifest.json")

                self.assertTrue(plan.is_valid, plan.diagnostics)
                self.assertTrue(results[0].success)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for channel, depth in zip(udim_core.CHANNELS, depths):
                    expected = (placeholders_16 if depth == 16 else placeholders)[channel]
                    for index in (0, 2):
                        self.assertEqual(plan.tiles[index].sources[channel], expected)
                        self.assertEqual(plan.tiles[index].png_info[channel].bit_depth, depth)
                        output = root / "output" / f"T_Test_{channel}.{1001 + index}.png"
                        self.assertEqual(output.read_bytes(), expected.read_bytes())
                        data = manifest["sets"][0]["tiles"][index]["channels"][channel]
                        self.assertEqual(data["source_path"], str(expected))
                        self.assertEqual(data["png"]["bit_depth"], depth)
                    self.assertEqual(plan.tiles[0].preview_source(channel), root / "source" / f"T_Old_{channel}.png")

    def test_placeholder_depth_skips_missing_or_invalid_live_maps(self) -> None:
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = write_config(root, ["Old", "Missing", "Leaf"], "#deprecated_tiles: Old\n")
                write_texture(root, "Missing", missing={"C"})
                if invalid:
                    (root / "source" / "T_Missing_C.png").write_bytes(b"not a PNG")
                write_texture(root, "Leaf")
                write_png(root / "source" / "T_Leaf_C.png", bit_depth=16)
                placeholders = write_deprecated_placeholders(root)
                placeholders_16 = write_deprecated_placeholders(root, bit_depth=16)
                with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                    plan = build_plan(config)

                self.assertFalse(plan.is_valid)
                self.assertEqual(plan.tiles[0].sources["C"], placeholders_16["C"])
                self.assertEqual(plan.tiles[0].png_info["C"].bit_depth, 16)
                self.assertFalse(any(item.code == "bit_depth_mismatch" for item in plan.diagnostics))

    def test_missing_selected_16_bit_placeholder_blocks_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Leaf"], "#deprecated_tiles: Old\n")
            write_texture(root, "Leaf")
            write_png(root / "source" / "T_Leaf_C.png", bit_depth=16)
            placeholders = write_deprecated_placeholders(root)

            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)

                results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            self.assertFalse(plan.is_valid)
            errors = [item for item in plan.diagnostics if item.severity == "error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].code, "deprecated_placeholder_missing")
            self.assertIn("T_Deprecated_C_16.png", errors[0].message)
            self.assertFalse(results[0].success)
            self.assertFalse((root / "output").exists())

    def test_16_bit_set_does_not_require_unused_8_bit_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf", "Old"], "#deprecated_tiles: Old\n")
            write_texture(root, "Leaf")
            placeholders = {channel: root / f"T_Deprecated_{channel}.png" for channel in udim_core.CHANNELS}
            placeholders_16 = write_deprecated_placeholders(root, bit_depth=16)
            for channel in udim_core.CHANNELS:
                write_png(root / "source" / f"T_Leaf_{channel}.png", bit_depth=16)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
            self.assertTrue(plan.is_valid, plan.diagnostics)
            self.assertEqual(plan.tiles[1].sources, placeholders_16)

    def test_all_deprecated_defaults_to_8_bit_despite_old_16_bit_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old"], "#deprecated_tiles: Old\n")
            for channel in udim_core.CHANNELS:
                write_png(root / "source" / f"T_Old_{channel}.png", bit_depth=16)
            placeholders = write_deprecated_placeholders(root)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
            self.assertTrue(plan.is_valid, plan.diagnostics)
            self.assertEqual(plan.tiles[0].sources, placeholders)
            self.assertTrue(all(info.bit_depth == 8 for info in plan.tiles[0].png_info.values()))

    def test_mixed_live_depths_still_block_with_leading_deprecated_tile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Old", "Leaf", "Bark"], "#deprecated_tiles: Old\n")
            write_texture(root, "Leaf")
            write_texture(root, "Bark")
            write_png(root / "source" / "T_Leaf_C.png", bit_depth=16)
            placeholders = write_deprecated_placeholders(root)
            write_deprecated_placeholders(root, bit_depth=16)
            with patch.object(udim_core, "DEPRECATED_PNG_PATHS", placeholders):
                plan = build_plan(config)
            mismatches = [item for item in plan.diagnostics if item.code == "bit_depth_mismatch"]
            self.assertEqual(len(mismatches), 1)
            self.assertEqual(mismatches[0].texture_name, "Bark")
            self.assertIn("Leaf", mismatches[0].message)

    def test_many_bit_depth_mismatches_are_summarized_per_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            names = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
            config = write_config(root, names)
            for name in names:
                write_texture(root, name)
            for name in names[1:]:
                write_png(root / "source" / f"T_{name}_C.png", bit_depth=16)

            plan = build_plan(config)

            mismatches = [item for item in plan.diagnostics if item.code == "bit_depth_mismatch"]
            self.assertEqual(len(mismatches), 1)
            self.assertEqual(mismatches[0].channel, "C")
            self.assertIn("5 C tile(s)", mismatches[0].message)
            self.assertIn("and 1 more", mismatches[0].message)

    def test_successful_build_writes_manifest_with_explicit_unreal_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"], "#unreal_asset_ORS: T_Test_UDIM_ORM\n")
            write_texture(root, "Leaf")
            manifest_path = root / "manifest.json"

            results, written_manifest = run_configs([config], sync=False, manifest_path=manifest_path)
            manifest = json.loads(written_manifest.read_text(encoding="utf-8"))

            self.assertTrue(results[0].success)
            self.assertTrue(manifest["success"])
            self.assertEqual(manifest["sets"][0]["unreal_assets"]["ORS"], "T_Test_UDIM_ORM")
            self.assertTrue((root / "output" / "T_Test_C.1001.png").exists())
            self.assertFalse((root / "output" / "T_Test.udim-manifest.json").exists())

    def test_stage_cleanup_failure_is_reported_without_marking_generated_tiles_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            with patch.object(udim_core.shutil, "rmtree", side_effect=PermissionError("access denied")):
                results, manifest_path = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            result = results[0]
            self.assertTrue(result.success)
            self.assertTrue((root / "output" / "T_Test_C.1001.png").exists())
            cleanup = [item for item in result.diagnostics if item.code == "stage_cleanup_failed"]
            self.assertEqual(len(cleanup), 1)
            self.assertEqual(cleanup[0].severity, "warning")
            self.assertIn("access denied", cleanup[0].message)
            self.assertTrue(cleanup[0].path.is_dir())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["sets"][0]["diagnostics"][-1]["code"], "stage_cleanup_failed")

    def test_stage_cleanup_failure_is_reported_after_build_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            with patch.object(udim_core.shutil, "copy2", side_effect=OSError("copy failed")), \
                    patch.object(udim_core.shutil, "rmtree", side_effect=PermissionError("access denied")):
                results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            result = results[0]
            self.assertFalse(result.success)
            self.assertEqual([item.code for item in result.diagnostics], ["build_failed", "stage_cleanup_failed"])
            self.assertFalse((root / "output").exists())

    def test_failed_rollback_preserves_staged_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            output = root / "output"
            old_color = output / "T_Test_C.1001.png"
            write_png(old_color)
            write_png(output / "T_Test_N.1001.png")
            real_replace = udim_core.os.replace

            def replace(source, target):
                source, target = Path(source), Path(target)
                if source.parent.name == "backups" and target == old_color:
                    raise OSError("restore denied")
                if source.name == "T_Test_N.1001.png" and source.parent.name.startswith(".Test-udim-"):
                    raise OSError("install denied")
                return real_replace(source, target)

            with patch.object(udim_core.os, "replace", side_effect=replace):
                results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            result = results[0]
            self.assertFalse(result.success)
            self.assertIn("rollback_failed", [item.code for item in result.diagnostics])
            preserved = next(item for item in result.diagnostics if item.code == "stage_preserved")
            self.assertTrue((preserved.path / "backups" / old_color.name).is_file())

    def test_only_extra_tiles_for_this_set_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf")
            output = root / "output"
            write_png(output / "T_Test_SIL.1002.png")
            write_png(output / "T_OldSet_C.1002.png")
            write_png(output / "T_Test_C.1002.png")

            plan = build_plan(config)

            self.assertEqual([path.name for path in plan.unmanaged_outputs], ["T_Test_C.1002.png"])
            self.assertEqual([item.code for item in plan.diagnostics], ["unmanaged_outputs"])

    def test_validation_failure_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = write_config(root, ["Leaf"])
            write_texture(root, "Leaf", missing={"N"})

            results, _ = run_configs([config], sync=False, manifest_path=root / "manifest.json")

            self.assertFalse(results[0].success)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
