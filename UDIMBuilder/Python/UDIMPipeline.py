import configparser
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import unreal


SETTINGS_SECTION = "Settings"
SCRIPT_PATH_KEY = "UDIMScriptPath"
PYTHON_PATH_KEY = "UDIMPythonExecutable"
LAST_MANIFEST_KEY = "LastManifestPath"
MANIFEST_FILENAME = "UDIMBuildManifest.json"
LOCAL_MANIFEST_DIR = "Saved"
MANIFEST_SCHEMA_VERSION = 1
ASSET_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def _settings_path():
    saved_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    return Path(saved_dir) / "Config" / "Python" / "UDIMSettings.ini"


def _load_settings():
    config = configparser.ConfigParser()
    path = _settings_path()
    if path.exists():
        config.read(path.as_posix(), encoding="utf-8")
    return config


def _save_setting(key, value):
    config = _load_settings()
    if SETTINGS_SECTION not in config:
        config.add_section(SETTINGS_SECTION)
    config.set(SETTINGS_SECTION, key, str(value))
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as config_file:
        config.write(config_file)


def _get_setting(key, default=""):
    config = _load_settings()
    if config.has_option(SETTINGS_SECTION, key):
        return config.get(SETTINGS_SECTION, key).strip()
    return default


def _show_message(title, message):
    unreal.EditorDialog.show_message(title, message, unreal.AppMsgType.OK)


def _show_error(message):
    unreal.log_error(message)
    _show_message("UDIM Tool", message)


def _configured_tool_path():
    value = _get_setting(SCRIPT_PATH_KEY)
    path = Path(value).expanduser() if value else None
    if path is not None and path.is_file():
        return path.resolve()

    message = (
        f"The configured UDIM Builder was not found:\n{path}\n\nLocate UdimCombiner.bat now?"
        if path else "No UDIM Builder path is configured.\n\nLocate UdimCombiner.bat now?"
    )
    answer = unreal.EditorDialog.show_message("UDIM Builder setup", message, unreal.AppMsgType.YES_NO)
    if answer != unreal.AppReturnType.YES:
        return None

    start_dir = path.parent if path and path.parent.is_dir() else Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    )
    root = None
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            parent=root,
            title="Locate UdimCombiner.bat",
            initialdir=str(start_dir),
            filetypes=[("Batch files", "*.bat")],
        )
    except Exception as exc:
        _show_error(f"Could not open the UDIM Builder file browser:\n{exc}")
        return None
    finally:
        if root is not None:
            root.destroy()

    if not selected:
        return None
    chosen = Path(selected).resolve()
    if chosen.name.casefold() != "udimcombiner.bat" or not chosen.is_file() or not chosen.with_name("UdimCombiner.py").is_file():
        _show_error("Select UdimCombiner.bat beside UdimCombiner.py.")
        return None
    try:
        _save_setting(SCRIPT_PATH_KEY, chosen)
    except OSError as exc:
        _show_error(f"Could not save the UDIM Builder path:\n{exc}")
        return None
    return chosen


def _python_command():
    configured = _get_setting(PYTHON_PATH_KEY)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return [str(path.resolve())]
        unreal.log_warning(f"Configured UDIM Python executable was not found: {path}")

    environment_path = os.environ.get("UDIM_PYTHON", "").strip()
    if environment_path and Path(environment_path).is_file():
        return [str(Path(environment_path).resolve())]

    for executable in ("python.exe", "python", "python3.exe", "python3"):
        resolved = shutil.which(executable)
        if resolved:
            return [resolved]

    return None


def _tool_command(tool_path, ui=False):
    suffix = tool_path.suffix.casefold()
    if suffix == ".exe":
        command = [str(tool_path)]
    elif suffix in (".bat", ".cmd"):
        script_path = tool_path.with_name("UdimCombiner.py")
        python_command = _python_command()
        if not script_path.is_file() or not python_command:
            _show_error(
                "UDIM Builder requires UdimCombiner.py beside the configured batch file and a valid external Python executable."
            )
            return None
        command = python_command + [str(script_path)]
    else:
        python_command = _python_command()
        if not python_command:
            _show_error(
                "No external Python executable was found for the UDIM Builder. "
                "Add UDIMPythonExecutable to Saved/Config/Python/UDIMSettings.ini or set UDIM_PYTHON."
            )
            return None
        command = python_command + [str(tool_path)]

    if ui:
        command.append("--ui")
    return command


def open_builder():
    tool_path = _configured_tool_path()
    if not tool_path:
        return False
    command = _tool_command(tool_path, ui=True)
    if not command:
        return False
    try:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        environment = os.environ.copy()
        environment["UDIM_UNREAL_CONTENT_DIR"] = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
        subprocess.Popen(command, cwd=str(tool_path.parent), env=environment, creationflags=creation_flags)
        unreal.log(f"Opened UDIM Builder: {tool_path}")
        return True
    except OSError as exc:
        _show_error(f"Could not open the UDIM Builder:\n{exc}")
        return False


def _manifest_from_output(stdout):
    for line in stdout.splitlines():
        if line.startswith("[MANIFEST]"):
            value = line[len("[MANIFEST]"):].strip()
            if value:
                return Path(value)
    return None


def _log_process_output(stdout, stderr):
    for line in stdout.splitlines():
        if line.startswith("[ERROR]"):
            unreal.log_error(line)
        elif line.startswith("[WARNING]"):
            unreal.log_warning(line)
        else:
            unreal.log(line)
    for line in stderr.splitlines():
        unreal.log_error(line)


def _run_combiner(tool_path=None):
    tool_path = tool_path or _configured_tool_path()
    if not tool_path:
        return None
    command = _tool_command(tool_path, ui=False)
    if not command:
        return None

    try:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command,
            cwd=str(tool_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
            check=False,
        )
    except OSError as exc:
        _show_error(f"Could not run the UDIM Builder:\n{exc}")
        return None

    _log_process_output(result.stdout, result.stderr)
    manifest_path = _manifest_from_output(result.stdout)
    if manifest_path is None or not manifest_path.is_file():
        _show_error(
            f"The UDIM Builder returned code {result.returncode} and did not produce a manifest. "
            "Review the Output Log for details."
        )
        return None

    if result.returncode:
        unreal.log_warning(
            f"UDIM Builder returned code {result.returncode}. Successfully built sets may still be imported; failed sets will be skipped."
        )

    manifest_path = manifest_path.resolve()
    _save_setting(LAST_MANIFEST_KEY, manifest_path)
    return manifest_path


def _latest_manifest_path():
    tool_path = _configured_tool_path()
    if not tool_path:
        return None
    candidate = tool_path.parent / LOCAL_MANIFEST_DIR / MANIFEST_FILENAME
    if candidate.is_file():
        manifest_path = candidate.resolve()
        _save_setting(LAST_MANIFEST_KEY, manifest_path)
        return manifest_path

    _show_error("No local UDIM build manifest was found. Generate the UDIMs before importing them.")
    return None


def _load_manifest(path):
    try:
        with path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError) as exc:
        _show_error(f"Could not read the UDIM build manifest:\n{path}\n\n{exc}")
        return None

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _show_error(
            f"Unsupported UDIM manifest schema {manifest.get('schema_version')}. "
            f"Expected schema {MANIFEST_SCHEMA_VERSION}."
        )
        return None
    if not isinstance(manifest.get("sets"), list):
        _show_error("The UDIM build manifest has no valid sets array.")
        return None
    return manifest


def _selected_asset_paths():
    paths = set()
    for asset in unreal.EditorUtilityLibrary.get_selected_assets():
        path = asset.get_path_name().split(".", 1)[0]
        paths.add(path.casefold())
    return paths


def _set_import_property(parameters, property_name, value):
    try:
        parameters.set_editor_property(property_name, value)
    except Exception as exc:
        unreal.log_warning(f"Could not set import property {property_name}: {exc}")


def _result_succeeded(result):
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _import_asset(manager, asset_path, asset_name, destination_path, source_path):
    parameters = unreal.ImportAssetParameters()
    _set_import_property(parameters, "is_automated", True)
    _set_import_property(parameters, "follow_redirectors", True)
    _set_import_property(parameters, "replace_existing", True)
    _set_import_property(parameters, "force_show_dialog", False)

    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if not asset:
            return False, "Asset exists but could not be loaded."
        _set_import_property(parameters, "reimport_asset", asset)
        try:
            import_data = asset.get_editor_property("asset_import_data")
            old_sources = import_data.extract_filenames() if import_data else []
        except Exception as exc:
            return False, f"Could not read the asset's import source: {exc}"
        if not import_data:
            return False, "Asset has no import source data."
        if not old_sources:
            return False, "Asset has no recorded source file."
        previous_source = old_sources[0]
        source_changed = Path(previous_source).resolve() != source_path.resolve()
        try:
            if source_changed:
                import_data.scripted_add_filename(str(source_path), 0, "")
            result = manager.reimport_asset(asset, parameters)
            if _result_succeeded(result):
                return True, "reimported"
            error = "Reimport returned failure."
        except Exception as exc:
            error = f"Reimport raised an exception: {exc}"
        if source_changed:
            try:
                import_data.scripted_add_filename(previous_source, 0, "")
            except Exception as exc:
                error += f" Could not restore the previous source path: {exc}"
        return False, error

    try:
        source_data = unreal.InterchangeManager.create_source_data(str(source_path))
        _set_import_property(parameters, "destination_name", asset_name)
        result = manager.import_asset(destination_path, source_data, parameters)
        return _result_succeeded(result), "created"
    except Exception as exc:
        return False, f"Import raised an exception: {exc}"


def _ensure_channel_srgb(asset_path, channel):
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if not asset:
        return "Imported asset could not be loaded to set sRGB."

    expected = channel == "C"
    try:
        if bool(asset.get_editor_property("srgb")) != expected:
            asset.set_editor_property("srgb", expected)
        if not unreal.EditorAssetLibrary.save_loaded_asset(asset):
            return "Could not save the asset after setting sRGB."
        if bool(asset.get_editor_property("srgb")) != expected:
            return f"sRGB did not remain {'enabled' if expected else 'disabled'}."
    except Exception as exc:
        return f"Could not set sRGB: {exc}"
    return None


def import_manifest(manifest_path, only_selected=False):
    manifest_path = Path(manifest_path)
    manifest = _load_manifest(manifest_path)
    if not manifest:
        return False

    selected_paths = _selected_asset_paths() if only_selected else set()
    if only_selected and not selected_paths:
        _show_error("No assets are selected in the Content Browser.")
        return False

    built_sets = [item for item in manifest["sets"] if item.get("success") and item.get("status") == "built"]
    skipped_sets = [item.get("set_name", "Unnamed") for item in manifest["sets"] if item not in built_sets]
    if not built_sets:
        _show_error("The manifest contains no successfully built UDIM sets to import.")
        return False

    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    imported = []
    created = []
    failures = []
    filtered = 0
    total_assets = sum(len(item.get("unreal_assets", {})) for item in built_sets)

    with unreal.ScopedSlowTask(total_assets, "Importing generated UDIM textures") as task:
        task.make_dialog(True)
        for set_data in built_sets:
            set_name = set_data.get("set_name", "Unnamed")
            destination_path = set_data.get("unreal_destination_path")
            asset_names = set_data.get("unreal_assets", {})
            tiles = sorted(set_data.get("tiles", []), key=lambda tile: tile.get("udim", 0))
            if not destination_path or not destination_path.startswith("/Game/") or ".." in destination_path or not tiles:
                failures.append(f"{set_name}: manifest is missing destination or tile data")
                continue

            for channel, asset_name in asset_names.items():
                task.enter_progress_frame(1, f"{set_name} {channel}")
                if channel not in ("C", "N", "ORS") or not ASSET_TOKEN_PATTERN.fullmatch(asset_name or ""):
                    failures.append(f"{set_name}: manifest contains an invalid {channel} asset name")
                    continue
                asset_path = f"{destination_path.rstrip('/')}/{asset_name}"
                if only_selected and asset_path.casefold() not in selected_paths:
                    filtered += 1
                    continue

                source_values = [tile.get("channels", {}).get(channel, {}).get("output_path") for tile in tiles]
                missing_tiles = [
                    str(tile.get("udim", "?")) for tile, value in zip(tiles, source_values)
                    if not value or not Path(value).is_file()
                ]
                if missing_tiles:
                    failures.append(f"{asset_path}: generated UDIM tile(s) missing: {', '.join(missing_tiles)}")
                    continue
                source_path = Path(source_values[0])

                success, action = _import_asset(manager, asset_path, asset_name, destination_path, source_path)
                if success:
                    srgb_error = _ensure_channel_srgb(asset_path, channel)
                    if srgb_error:
                        failures.append(f"{asset_path}: {action}, but {srgb_error}")
                        continue
                    if action == "created":
                        created.append(asset_path)
                    else:
                        imported.append(asset_path)
                    unreal.log(f"UDIM {action}: {asset_path}")
                else:
                    failures.append(f"{asset_path}: {action}")

    summary = [
        f"Reimported: {len(imported)}",
        f"Created: {len(created)}",
        f"Failed: {len(failures)}",
    ]
    if filtered:
        summary.append(f"Not selected: {filtered}")
    if skipped_sets:
        summary.append(f"Skipped failed sets: {', '.join(skipped_sets)}")
    if failures:
        summary.append("\n" + "\n".join(failures[:12]))
        if len(failures) > 12:
            summary.append(f"...and {len(failures) - 12} more. See the Output Log.")

    message = "\n".join(summary)
    if failures:
        unreal.log_error(message)
    else:
        unreal.log(message)
    _show_message("UDIM import complete", message)
    return not failures


def build_and_import():
    tool_path = _configured_tool_path()
    if not tool_path:
        return False
    with unreal.ScopedSlowTask(2, "Building and importing UDIM textures") as task:
        task.make_dialog(True)
        task.enter_progress_frame(1, "Validating and generating source tiles")
        manifest_path = _run_combiner(tool_path)
        if not manifest_path:
            return False
        task.enter_progress_frame(1, "Importing successful UDIM sets")
        return import_manifest(manifest_path)


def import_latest_manifest():
    manifest_path = _latest_manifest_path()
    if not manifest_path:
        return False
    return import_manifest(manifest_path)


def import_latest_selected():
    manifest_path = _latest_manifest_path()
    if not manifest_path:
        return False
    return import_manifest(manifest_path, only_selected=True)
