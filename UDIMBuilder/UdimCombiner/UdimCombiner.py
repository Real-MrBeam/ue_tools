from __future__ import annotations

import argparse
import ctypes
import importlib
from importlib import metadata
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from udim_core import CONFIG_DIR, DEFAULT_MANIFEST_PATH, SCRIPT_DIR, BuildResult, find_config_files, run_configs


def _package_ready(distribution: str, module: str, minimum: tuple[int, int], maximum: tuple[int, int] | None = None) -> bool:
    try:
        installed = tuple(int(part) for part in metadata.version(distribution).split(".")[:2])
        if installed < minimum or (maximum is not None and installed >= maximum):
            return False
        importlib.import_module(module)
    except (metadata.PackageNotFoundError, ImportError, OSError, ValueError):
        return False
    return True


def _requirements_error(message: str, ui: bool) -> None:
    print(f"[ERROR] {message}", flush=True)
    if ui and sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{message}\n\nRun InstallRequirements.bat manually for installation details.",
                "UDIM Builder setup",
                0x10,
            )
        except (AttributeError, OSError):
            pass


def _ensure_requirements(ui: bool, sync: bool) -> bool:
    required = []
    if ui or sync:
        required.append(("P4Python", "P4", (2023, 1), None))
    if ui:
        required.append(("PySide6", "PySide6.QtWidgets", (6, 7), (7, 0)))
    if not required or all(_package_ready(*package) for package in required):
        return True

    print("[INFO] Installing missing or outdated UDIM Builder requirements...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(SCRIPT_DIR / "requirements.txt")],
            cwd=str(SCRIPT_DIR),
            check=False,
        )
    except OSError as exc:
        _requirements_error(f"Could not start requirements installation: {exc}", ui)
        return False
    if result.returncode:
        _requirements_error(f"Requirements installation failed (exit code {result.returncode}).", ui)
        return False
    if not all(_package_ready(*package) for package in required):
        _requirements_error("Requirements installed, but this Python cannot import them.", ui)
        return False
    return True


def _print_result(result: BuildResult) -> None:
    plan = result.plan
    print(f"\n[{result.status.upper()}] {plan.set_name or plan.config_path.name}")
    for diagnostic in result.diagnostics:
        location = f" ({diagnostic.path})" if diagnostic.path else ""
        print(f"[{diagnostic.severity.upper()}] {diagnostic.message}{location}")

    if result.success:
        color_asset = plan.unreal_assets.get("C", f"T_{plan.set_name}_UDIM_C")
        legacy_source_name = f"T_{plan.set_name}_C.1001"
        print(f"[REIMPORT] {legacy_source_name}")
        print(f"[REIMPORT_ASSET] {color_asset}")


def _resolve_configs(values: list[str] | None) -> list[Path]:
    if not values:
        return find_config_files()
    configs = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            preferred = CONFIG_DIR / path
            path = preferred if len(path.parts) == 1 and preferred.is_file() else SCRIPT_DIR / path
        configs.append(path.resolve())
    return configs


def _run_ui() -> int:
    try:
        from udim_ui import run_ui
    except ImportError as exc:
        print(f"[ERROR] Could not start the UDIM Builder UI: {exc}")
        print("[ERROR] Install dependencies with: python -m pip install -r requirements.txt")
        return 2
    return run_ui()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and generate Unreal UDIM texture sets.")
    parser.add_argument("--ui", action="store_true", help="Open the artist-facing desktop UI.")
    parser.add_argument("--config", action="append", help="Build one config file (bare names use Configs/). May be supplied more than once.")
    parser.add_argument("--no-sync", action="store_true", help="Use local source files without Perforce sync.")
    parser.add_argument("--manifest", help=f"Combined manifest path. Defaults to {DEFAULT_MANIFEST_PATH}.")
    args = parser.parse_args(argv)

    if args.ui:
        if not _ensure_requirements(ui=True, sync=True):
            return 2
        return _run_ui()

    configs = _resolve_configs(args.config)
    if not configs:
        print("[ERROR] No *Textures*.txt config files were found.")
        return 1
    if not _ensure_requirements(ui=False, sync=not args.no_sync):
        # The Unreal importer otherwise falls back to an older on-disk manifest.
        # A unique nonexistent path makes this failed build unambiguously rejectable.
        print(f"[MANIFEST] {SCRIPT_DIR / f'.udim-requirements-failed-{uuid4().hex}.json'}")
        return 2

    manifest_path = Path(args.manifest).resolve() if args.manifest else DEFAULT_MANIFEST_PATH
    try:
        results, written_manifest = run_configs(configs, sync=not args.no_sync, manifest_path=manifest_path)
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] UDIM build stopped: {exc}")
        print(f"[MANIFEST] {SCRIPT_DIR / f'.udim-build-failed-{uuid4().hex}.json'}")
        return 2
    for result in results:
        _print_result(result)
    print(f"[MANIFEST] {written_manifest}")

    return 0 if all(result.success for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
