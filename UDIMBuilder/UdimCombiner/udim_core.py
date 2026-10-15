from __future__ import annotations

import json
import os
import re
import shutil
import stat
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = SCRIPT_DIR / "Configs"
CHANNELS = ("C", "N", "ORS")
DEPRECATED_PNG_PATHS = {channel: SCRIPT_DIR / "Resources" / f"T_Deprecated_{channel}.png" for channel in CHANNELS}
CONFIG_FIELD_KEYS = (
    "udim_set_name",
    "original_files_path",
    "udim_files_path",
    "unreal_destination_path",
    "unreal_asset_C",
    "unreal_asset_N",
    "unreal_asset_ORS",
)
SCHEMA_VERSION = 1
TOOL_VERSION = "2.0.0"
DEFAULT_MANIFEST_NAME = "UDIMBuildManifest.json"
DEFAULT_MANIFEST_PATH = SCRIPT_DIR / "Saved" / DEFAULT_MANIFEST_NAME
ASSET_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
SOURCE_TEXTURE_PATTERN = re.compile(r"^T_([A-Za-z0-9_]+)_(?:C|N|ORS)\.png$", re.IGNORECASE)


@dataclass(frozen=True)
class PngInfo:
    width: int
    height: int
    bit_depth: int
    color_type: int
    has_srgb: bool
    gamma: int | None

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "bit_depth": self.bit_depth,
            "color_type": self.color_type,
            "has_srgb": self.has_srgb,
            "gamma": self.gamma,
        }


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    texture_name: str | None = None
    channel: str | None = None
    path: Path | None = None

    def to_dict(self) -> dict:
        value = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.texture_name:
            value["texture_name"] = self.texture_name
        if self.channel:
            value["channel"] = self.channel
        if self.path:
            value["path"] = str(self.path)
        return value


@dataclass
class TilePlan:
    udim: int
    texture_name: str
    sources: dict[str, Path | None]
    targets: dict[str, Path]
    png_info: dict[str, PngInfo] = field(default_factory=dict)
    deprecated: bool = False
    original_sources: dict[str, Path | None] = field(default_factory=dict)
    original_png_info: dict[str, PngInfo] = field(default_factory=dict)

    def original_source(self, channel: str) -> Path | None:
        return self.original_sources.get(channel) if self.deprecated else self.sources.get(channel)

    def preview_source(self, channel: str) -> Path | None:
        return self.original_source(channel) or self.sources.get(channel)

    def original_info(self, channel: str) -> PngInfo | None:
        return self.original_png_info.get(channel) if self.deprecated else self.png_info.get(channel)

    def to_manifest(self) -> dict:
        channel_data = {}
        for channel in CHANNELS:
            source = self.sources.get(channel)
            info = self.png_info.get(channel)
            channel_data[channel] = {
                "source_path": str(source) if source else None,
                "output_path": str(self.targets[channel]),
                "png": info.to_dict() if info else None,
            }
        return {
            "udim": self.udim,
            "texture_name": self.texture_name,
            "deprecated": self.deprecated,
            "channels": channel_data,
        }


@dataclass
class SetPlan:
    config_path: Path
    set_name: str
    original_files_path: Path | None
    udim_files_path: Path | None
    unreal_destination_path: str | None
    unreal_assets: dict[str, str]
    tiles: list[TilePlan] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    existing_output_count: int = 0
    unmanaged_outputs: list[Path] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.diagnostics)

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0 and bool(self.tiles)

    def to_manifest(self, status: str, diagnostics: Iterable[Diagnostic] | None = None) -> dict:
        all_diagnostics = list(diagnostics if diagnostics is not None else self.diagnostics)
        return {
            "config_path": str(self.config_path),
            "set_name": self.set_name,
            "status": status,
            "success": status == "built",
            "original_files_path": str(self.original_files_path) if self.original_files_path else None,
            "udim_files_path": str(self.udim_files_path) if self.udim_files_path else None,
            "unreal_destination_path": self.unreal_destination_path,
            "unreal_assets": self.unreal_assets,
            "existing_output_count": self.existing_output_count,
            "unmanaged_outputs": [str(path) for path in self.unmanaged_outputs],
            "diagnostics": [item.to_dict() for item in all_diagnostics],
            "tiles": [tile.to_manifest() for tile in self.tiles],
        }


@dataclass
class BuildResult:
    plan: SetPlan
    success: bool
    status: str
    diagnostics: list[Diagnostic]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_config_files(directory: Path | None = None) -> list[Path]:
    if directory is not None:
        return sorted(directory.glob("*Textures*.txt"), key=lambda path: path.name.casefold())
    # New configs take precedence if an older checkout temporarily restores a root-level copy.
    configs = {path.name.casefold(): path for path in SCRIPT_DIR.glob("*Textures*.txt")}
    configs.update({path.name.casefold(): path for path in CONFIG_DIR.glob("*Textures*.txt")})
    return sorted(configs.values(), key=lambda path: path.name.casefold())


def _resolve_config_path(config_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def unreal_destination_from_content_folder(content_dir: Path, folder: Path) -> str:
    relative = folder.resolve().relative_to(content_dir.resolve())
    return f"/Game/{relative.as_posix()}" if relative.parts else "/Game/"


def default_unreal_assets(set_name: str) -> dict[str, str]:
    unreal_stem = set_name if set_name.casefold().endswith("_udim") else f"{set_name}_UDIM"
    return {channel: f"T_{unreal_stem}_{channel}" for channel in CHANNELS}


def reorder_texture_names(names: list[str], source_index: int, target_index: int) -> list[str]:
    if not 0 <= source_index < len(names) or not 0 <= target_index < len(names):
        raise IndexError("Tile position is outside the texture set.")
    reordered = names.copy()
    reordered.insert(target_index, reordered.pop(source_index))
    return reordered


def _read_config(config_path: Path) -> tuple[dict[str, str], list[str], list[Diagnostic]]:
    directives: dict[str, str] = {}
    textures: list[str] = []
    diagnostics: list[Diagnostic] = []
    inside_textures = False

    try:
        lines = config_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        diagnostics.append(Diagnostic("error", "config_read_failed", f"Could not read config: {exc}", path=config_path))
        return directives, textures, diagnostics

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line == "#textures_start":
            inside_textures = True
            continue
        if line == "#textures_end":
            inside_textures = False
            continue
        if line.startswith("#"):
            if ":" in line:
                key, value = line[1:].split(":", 1)
                directives[key.strip().casefold()] = value.strip()
            continue
        if inside_textures:
            textures.append(line)
        else:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "config_text_outside_section",
                    f"Ignoring text outside #textures_start/#textures_end at line {line_number}: {line}",
                    path=config_path,
                )
            )

    if inside_textures:
        diagnostics.append(Diagnostic("error", "config_section_unclosed", "Missing #textures_end.", path=config_path))

    return directives, textures, diagnostics


def load_config_fields(config_path: Path) -> tuple[dict[str, str], list[str], bytes]:
    config_path = Path(config_path)
    original = config_path.read_bytes()
    directives, textures, _ = _read_config(config_path)
    fields = {key: directives.get(key.casefold(), "") for key in CONFIG_FIELD_KEYS}
    fields["deprecated_tiles"] = directives.get("deprecated_tiles", "")
    defaults = default_unreal_assets(fields["udim_set_name"] or "Unnamed")
    for channel in CHANNELS:
        key = f"unreal_asset_{channel}"
        fields[key] = fields[key] or defaults[channel]
    return fields, textures, original


def save_config_fields(config_path: Path, fields: dict[str, str], textures: list[str], expected_bytes: bytes | None = None) -> None:
    config_path = Path(config_path)
    values = {key.casefold(): fields.get(key, "").strip() for key in CONFIG_FIELD_KEYS}
    for key, value in values.items():
        if not value or "\n" in value or "\r" in value:
            raise ValueError(f"{key} needs a single non-empty value.")
    if not ASSET_TOKEN_PATTERN.fullmatch(values["udim_set_name"]):
        raise ValueError("Set name may contain only letters, numbers and underscores.")
    if not values["unreal_destination_path"].startswith("/Game/") or ".." in values["unreal_destination_path"]:
        raise ValueError("Unreal destination must be a package path below /Game/.")
    for channel in CHANNELS:
        if not ASSET_TOKEN_PATTERN.fullmatch(values[f"unreal_asset_{channel}".casefold()]):
            raise ValueError(f"Unreal asset name for {channel} may contain only letters, numbers and underscores.")

    names = [name.strip() for name in textures if name.strip()]
    if not names:
        raise ValueError("Add at least one texture name.")
    if any(not ASSET_TOKEN_PATTERN.fullmatch(name) for name in names):
        raise ValueError("Texture names may contain only letters, numbers and underscores.")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Texture names must be unique.")
    deprecated_names = [name.strip() for name in fields.get("deprecated_tiles", "").split(",") if name.strip()]
    if len({name.casefold() for name in deprecated_names}) != len(deprecated_names):
        raise ValueError("Deprecated tile names must be unique.")
    names_by_fold = {name.casefold(): name for name in names}
    if any(name.casefold() not in names_by_fold for name in deprecated_names):
        raise ValueError("Every deprecated tile must still be in this texture set.")
    deprecated_value = ", ".join(names_by_fold[name.casefold()] for name in deprecated_names)

    original = config_path.read_bytes() if config_path.exists() else b""
    if expected_bytes is not None and original != expected_bytes:
        raise RuntimeError("The config changed since it was opened. Refresh it before saving.")
    if config_path.exists() and not config_path.stat().st_mode & stat.S_IWRITE:
        raise PermissionError("The config is read-only. Check it out in Perforce before saving.")

    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    source_text = original.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in source_text else "\n"
    lines = source_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "#textures_start"), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "#textures_end"), len(lines))
    prefix = lines[:start]
    suffix = lines[end + 1:] if end < len(lines) else []
    seen: set[str] = set()

    def update_lines(source: list[str]) -> list[str]:
        updated = []
        for line in source:
            stripped = line.strip()
            key = stripped[1:].split(":", 1)[0].strip().casefold() if stripped.startswith("#") and ":" in stripped else ""
            if key in values:
                if key not in seen:
                    original_key = stripped[1:].split(":", 1)[0].strip()
                    updated.append(f"#{original_key}: {values[key]}")
                    seen.add(key)
            elif key == "deprecated_tiles":
                if deprecated_value and key not in seen:
                    updated.append(f"#deprecated_tiles: {deprecated_value}")
                    seen.add(key)
            else:
                updated.append(line)
        return updated

    prefix = update_lines(prefix)
    suffix = update_lines(suffix)
    for key in CONFIG_FIELD_KEYS:
        if key.casefold() not in seen:
            prefix.append(f"#{key}: {values[key.casefold()]}")
    if deprecated_value and "deprecated_tiles" not in seen:
        prefix.append(f"#deprecated_tiles: {deprecated_value}")
    if prefix and prefix[-1].strip():
        prefix.append("")
    content = newline.join(prefix + ["#textures_start", *names, "#textures_end", *suffix]) + newline
    encoded = bom + content.encode("utf-8")
    if encoded == original:
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, config_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def archive_config(config_path: Path) -> Path:
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    if not config_path.stat().st_mode & stat.S_IWRITE:
        raise PermissionError("The config is read-only. Check it out in Perforce before removing it.")
    archive_dir = config_path.parent / "Unused"
    target = archive_dir / config_path.name
    if target.exists():
        raise FileExistsError(f"An archived config already exists: {target}")
    archive_dir.mkdir(exist_ok=True)
    config_path.rename(target)
    return target


def read_png_info(path: Path) -> PngInfo:
    width = height = bit_depth = color_type = None
    has_srgb = False
    gamma = None

    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a PNG file")

        while True:
            raw_length = stream.read(4)
            if not raw_length:
                break
            if len(raw_length) != 4:
                raise ValueError("Truncated PNG chunk length")
            (length,) = struct.unpack(">I", raw_length)
            chunk_type = stream.read(4)
            data = stream.read(length)
            crc = stream.read(4)
            if len(chunk_type) != 4 or len(data) != length or len(crc) != 4:
                raise ValueError("Truncated PNG chunk")

            if chunk_type == b"IHDR":
                if length != 13:
                    raise ValueError("Invalid PNG IHDR")
                width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", data)
            elif chunk_type == b"sRGB":
                has_srgb = True
            elif chunk_type == b"gAMA" and length == 4:
                (gamma,) = struct.unpack(">I", data)
            elif chunk_type == b"IEND":
                break

    if None in (width, height, bit_depth, color_type):
        raise ValueError("PNG has no valid IHDR")

    return PngInfo(width, height, bit_depth, color_type, has_srgb, gamma)


def source_texture_name(path: Path) -> str | None:
    match = SOURCE_TEXTURE_PATTERN.fullmatch(path.name)
    return match.group(1) if match else None


def _source_png_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() == ".png"),
        key=lambda path: str(path).casefold(),
    )


def discover_texture_names(root: Path) -> list[str]:
    if not root.is_dir():
        raise ValueError(f"Source folder does not exist: {root}")
    names: dict[str, str] = {}
    for path in _source_png_files(root):
        name = source_texture_name(path)
        if name:
            names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=str.casefold)


def _index_png_files(root: Path) -> tuple[dict[str, list[Path]], list[Diagnostic]]:
    index: dict[str, list[Path]] = {}
    diagnostics: list[Diagnostic] = []
    try:
        for path in _source_png_files(root):
            index.setdefault(path.name.casefold(), []).append(path.resolve())
    except OSError as exc:
        diagnostics.append(Diagnostic("error", "source_scan_failed", f"Could not scan source folder: {exc}", path=root))
    return index, diagnostics


def build_plan(
    config_path: Path,
    texture_names_override: list[str] | None = None,
    deprecated_names_override: set[str] | None = None,
) -> SetPlan:
    config_path = Path(config_path).resolve()
    directives, texture_names, diagnostics = _read_config(config_path)
    if texture_names_override is not None:
        texture_names = list(texture_names_override)
    deprecated_names = (
        {name.casefold() for name in deprecated_names_override}
        if deprecated_names_override is not None
        else {name.strip().casefold() for name in directives.get("deprecated_tiles", "").split(",") if name.strip()}
    )
    set_name = directives.get("udim_set_name", "").strip()
    original_path = _resolve_config_path(config_path, directives.get("original_files_path"))
    output_path = _resolve_config_path(config_path, directives.get("udim_files_path"))
    destination = directives.get("unreal_destination_path") or None
    unreal_assets = default_unreal_assets(set_name or "Unnamed")

    for channel in CHANNELS:
        override = directives.get(f"unreal_asset_{channel}".casefold())
        if override:
            unreal_assets[channel] = override

    plan = SetPlan(
        config_path=config_path,
        set_name=set_name,
        original_files_path=original_path,
        udim_files_path=output_path,
        unreal_destination_path=destination,
        unreal_assets=unreal_assets,
        diagnostics=diagnostics,
    )

    if not set_name:
        plan.diagnostics.append(Diagnostic("error", "set_name_missing", "Missing #udim_set_name.", path=config_path))
    elif not ASSET_TOKEN_PATTERN.fullmatch(set_name):
        plan.diagnostics.append(
            Diagnostic("error", "set_name_invalid", "UDIM set names may contain only letters, numbers and underscores.", path=config_path)
        )
    if original_path is None:
        plan.diagnostics.append(Diagnostic("error", "source_path_missing", "Missing #original_files_path.", path=config_path))
    elif not original_path.is_dir():
        plan.diagnostics.append(Diagnostic("error", "source_path_not_found", "Source folder does not exist.", path=original_path))
    if output_path is None:
        plan.diagnostics.append(Diagnostic("error", "output_path_missing", "Missing #udim_files_path.", path=config_path))
    if not destination:
        plan.diagnostics.append(
            Diagnostic("warning", "unreal_destination_missing", "No #unreal_destination_path is configured; Unreal import will skip this set.", path=config_path)
        )
    elif not destination.startswith("/Game/") or ".." in destination:
        plan.diagnostics.append(
            Diagnostic("error", "unreal_destination_invalid", "Unreal destination must be a package path below /Game/.", path=config_path)
        )
    if not texture_names:
        plan.diagnostics.append(Diagnostic("error", "textures_empty", "The texture list is empty.", path=config_path))
    unknown_deprecated = deprecated_names - {name.casefold() for name in texture_names}
    if unknown_deprecated:
        plan.diagnostics.append(Diagnostic(
            "warning", "deprecated_unknown",
            f"Deprecated names not in this set: {', '.join(sorted(unknown_deprecated))}", path=config_path,
        ))

    seen_names: dict[str, str] = {}
    for texture_name in texture_names:
        folded = texture_name.casefold()
        if not ASSET_TOKEN_PATTERN.fullmatch(texture_name):
            plan.diagnostics.append(
                Diagnostic(
                    "error",
                    "texture_name_invalid",
                    f"Texture names may contain only letters, numbers and underscores: {texture_name}",
                    texture_name=texture_name,
                    path=config_path,
                )
            )
        if folded in seen_names:
            plan.diagnostics.append(
                Diagnostic("error", "texture_duplicate", f"Texture is listed more than once: {texture_name}", texture_name=texture_name, path=config_path)
            )
        else:
            seen_names[folded] = texture_name

    for channel, asset_name in unreal_assets.items():
        if not ASSET_TOKEN_PATTERN.fullmatch(asset_name):
            plan.diagnostics.append(
                Diagnostic(
                    "error",
                    "unreal_asset_name_invalid",
                    f"Unreal asset name for {channel} may contain only letters, numbers and underscores: {asset_name}",
                    channel=channel,
                    path=config_path,
                )
            )

    source_index: dict[str, list[Path]] = {}
    if original_path and original_path.is_dir():
        source_index, scan_diagnostics = _index_png_files(original_path)
        plan.diagnostics.extend(scan_diagnostics)

    for index, texture_name in enumerate(texture_names):
        udim = 1001 + index
        deprecated = texture_name.casefold() in deprecated_names
        sources: dict[str, Path | None] = {}
        targets: dict[str, Path] = {}
        png_info: dict[str, PngInfo] = {}
        original_sources: dict[str, Path | None] = {}
        original_png_info: dict[str, PngInfo] = {}

        for channel in CHANNELS:
            expected_name = f"T_{texture_name}_{channel}.png"
            matches = source_index.get(expected_name.casefold(), [])
            original_source = matches[0] if len(matches) == 1 else None
            source = None if deprecated else original_source
            sources[channel] = source
            if deprecated:
                original_sources[channel] = original_source
            target_name = f"T_{set_name}_{channel}.{udim}.png"
            targets[channel] = (output_path / target_name) if output_path else Path(target_name)

            if deprecated:
                if original_source:
                    try:
                        original_png_info[channel] = read_png_info(original_source)
                    except (OSError, ValueError, struct.error):
                        pass  # Old source maps are optional; they never block a deprecated build.
                continue

            if not matches:
                plan.diagnostics.append(
                    Diagnostic("error", "source_missing", f"Missing source map: {expected_name}", texture_name, channel, original_path)
                )
                continue
            if len(matches) > 1:
                joined = ", ".join(str(path) for path in matches)
                plan.diagnostics.append(
                    Diagnostic("error", "source_ambiguous", f"Multiple source maps match {expected_name}: {joined}", texture_name, channel, original_path)
                )
                continue

            try:
                info = read_png_info(source)
                png_info[channel] = info
            except (OSError, ValueError, struct.error) as exc:
                plan.diagnostics.append(
                    Diagnostic("error", "png_invalid", f"Could not inspect PNG: {exc}", texture_name, channel, source)
                )
                continue

        plan.tiles.append(TilePlan(udim, texture_name, sources, targets, png_info, deprecated, original_sources, original_png_info))

    deprecated_tiles = [tile for tile in plan.tiles if tile.deprecated]
    for channel in CHANNELS:
        # Placeholder tiles must not decide the bit depth of the live sources.
        first_tile = next((tile for tile in plan.tiles if not tile.deprecated and channel in tile.png_info), None)
        first_info = first_tile.png_info[channel] if first_tile else None
        if deprecated_tiles:
            bit_depth = first_info.bit_depth if first_info else 8
            placeholder = DEPRECATED_PNG_PATHS[channel]
            if bit_depth == 16:
                placeholder = placeholder.with_name(f"{placeholder.stem}_16{placeholder.suffix}")
            for tile in deprecated_tiles:
                tile.sources[channel] = placeholder
            if not placeholder.is_file():
                plan.diagnostics.append(Diagnostic(
                    "error", "deprecated_placeholder_missing", f"Resources/{placeholder.name} is missing.",
                    channel=channel, path=placeholder,
                ))
            else:
                try:
                    info = read_png_info(placeholder)
                    if info.bit_depth != bit_depth:
                        raise ValueError(f"Expected {bit_depth}-bit PNG, found {info.bit_depth}-bit.")
                except (OSError, ValueError, struct.error) as exc:
                    plan.diagnostics.append(Diagnostic(
                        "error", "deprecated_placeholder_invalid", f"Could not use {placeholder.name}: {exc}",
                        channel=channel, path=placeholder,
                    ))
                else:
                    for tile in deprecated_tiles:
                        tile.png_info[channel] = info

        if first_tile is not None and first_info is not None:
            mismatches = [
                (tile, tile.png_info[channel]) for tile in plan.tiles
                if tile is not first_tile and channel in tile.png_info and tile.png_info[channel].bit_depth != first_info.bit_depth
            ]
            if not mismatches:
                continue
            examples = ", ".join(
                f"{tile.texture_name} ({tile.udim}, {info.bit_depth}-bit)" for tile, info in mismatches[:4]
            )
            if len(mismatches) > 4:
                examples += f", and {len(mismatches) - 4} more"
            plan.diagnostics.append(Diagnostic(
                "error", "bit_depth_mismatch",
                f"{len(mismatches)} {channel} tile(s) differ from the first valid, non-deprecated tile "
                f"(UDIM {first_tile.udim}, {first_tile.texture_name}, {first_info.bit_depth}-bit): {examples}.",
                mismatches[0][0].texture_name if len(mismatches) == 1 else None,
                channel, mismatches[0][0].sources[channel],
            ))

    if output_path and output_path.is_dir():
        expected_names = {target.name.casefold() for tile in plan.tiles for target in tile.targets.values()}
        active_channels = "|".join(re.escape(channel) for channel in CHANNELS)
        generated_pattern = re.compile(rf"^T_{re.escape(set_name)}_(?:{active_channels})\.\d{{4}}\.png$", re.IGNORECASE)
        for path in output_path.glob("*.png"):
            if path.name.casefold() in expected_names:
                plan.existing_output_count += 1
            elif generated_pattern.fullmatch(path.name):
                plan.unmanaged_outputs.append(path.resolve())

        if plan.unmanaged_outputs:
            plan.diagnostics.append(
                Diagnostic(
                    "warning",
                    "unmanaged_outputs",
                    f"Found {len(plan.unmanaged_outputs)} generated-looking files not owned by this build. They will not be deleted.",
                    path=output_path,
                )
            )

    return plan


def _p4_error_text(p4, fallback: str, exc: Exception | None = None) -> str:
    errors = getattr(p4, "errors", None) or []
    warnings = getattr(p4, "warnings", None) or []
    return (
        "; ".join(str(error) for error in errors)
        or (str(exc).strip() if exc else "")
        or "; ".join(str(warning) for warning in warnings)
        or fallback
    )


def checkout_file_for_edit(
    path: Path,
    confirm_checkout: Callable[[Path], bool] | None = None,
    confirm_untracked: Callable[[Path], bool] | None = None,
) -> bool:
    """Open a tracked file only when no other workspace has it open; return False if declined."""
    path = Path(path).resolve()
    if not path.is_file():
        return True  # New files are added through the normal artist reconcile workflow.
    try:
        from P4 import P4, P4Exception
    except ImportError as exc:
        raise RuntimeError("P4Python is needed to check out this file before editing.") from exc

    p4 = P4()
    p4.cwd = str(path.parent)
    p4.exception_level = 1
    if not os.environ.get("P4CONFIG"):
        os.environ["P4CONFIG"] = ".p4config"
    try:
        p4.connect()
        fstat = p4.run_fstat(str(path))
        if not fstat or "depotFile" not in fstat[0]:
            return confirm_untracked(path) if confirm_untracked else True
        opened = p4.run_opened("-a", fstat[0]["depotFile"])
        other_opened = [
            item for item in opened
            if not (p4.user and p4.client and item.get("user") == p4.user and item.get("client") == p4.client)
        ]
        if other_opened:
            owners = ", ".join(sorted({f"{item.get('user', '?')}@{item.get('client', '?')}" for item in other_opened}))
            raise RuntimeError(f"{path.name} is checked out by {owners}. Ask them to submit or release it before editing.")
        if not opened:
            if confirm_checkout and not confirm_checkout(path):
                return False
            if not p4.run_edit(str(path)):
                raise RuntimeError(_p4_error_text(p4, f"Perforce did not check out {path.name}."))
        return True
    except P4Exception as exc:
        raise RuntimeError(_p4_error_text(p4, f"Could not check out {path.name}.", exc)) from exc
    finally:
        if p4.connected():
            p4.disconnect()


def ensure_config_exclusive_open(path: Path) -> bool:
    """Open a saved config for add as text+l, or fix an already opened config's type.

    Return False when Perforce is unavailable so a new set can remain local.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        from P4 import P4, P4Exception
    except ImportError:
        return False

    p4 = P4()
    p4.cwd = str(path.parent)
    p4.exception_level = 1
    if not os.environ.get("P4CONFIG"):
        os.environ["P4CONFIG"] = ".p4config"
    try:
        try:
            p4.connect()
        except P4Exception:
            return False

        fstat = p4.run_fstat(str(path))
        if not fstat or "depotFile" not in fstat[0]:
            if not p4.run_add("-t", "text+l", str(path)):
                raise RuntimeError(_p4_error_text(p4, f"Perforce did not add {path.name} as text+l."))
        else:
            current_type = fstat[0].get("type") or fstat[0].get("headType")
            if current_type != "text+l":
                if not p4.run_reopen("-t", "text+l", str(path)):
                    raise RuntimeError(_p4_error_text(p4, f"Perforce did not change {path.name} to text+l."))
        return True
    except P4Exception as exc:
        raise RuntimeError(_p4_error_text(p4, f"Could not set {path.name} to text+l.", exc)) from exc
    finally:
        if p4.connected():
            p4.disconnect()


def sync_plan_sources(plan: SetPlan) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    local_sources = {str(source) for tile in plan.tiles if not tile.deprecated for source in tile.sources.values() if source}
    missing_names = {
        f"T_{item.texture_name}_{item.channel}.png" for item in plan.diagnostics
        if item.code == "source_missing" and item.texture_name and item.channel
        and ASSET_TOKEN_PATTERN.fullmatch(item.texture_name)
    }
    if not local_sources and not missing_names:
        return diagnostics
    try:
        from P4 import P4, P4Exception
    except ImportError:
        return [
            Diagnostic(
                "error",
                "p4python_missing",
                "P4Python is not installed. Install it or run with --no-sync to use local source files.",
            )
        ]

    p4 = P4()
    p4.cwd = str(SCRIPT_DIR)
    # Normal responses such as "file(s) up-to-date" are P4 warnings, not sync failures.
    p4.exception_level = 1
    if not os.environ.get("P4CONFIG"):
        os.environ["P4CONFIG"] = ".p4config"

    try:
        p4.connect()
        if missing_names and plan.original_files_path:
            source_root = plan.original_files_path.resolve()
            patterns = [str(source_root / "..." / name) for name in sorted(missing_names, key=str.casefold)]
            try:
                remote_files = p4.run_fstat(*patterns)
            except P4Exception as exc:
                return [Diagnostic("error", "p4_check_failed", _p4_error_text(p4, "Perforce source check failed.", exc))]
            matches: dict[str, set[Path]] = {}
            expected = {name.casefold() for name in missing_names}
            for item in remote_files:
                if not isinstance(item, dict) or item.get("headAction") == "delete" or not item.get("clientFile"):
                    continue
                source = Path(item["clientFile"]).resolve()
                if source.name.casefold() in expected and source.is_relative_to(source_root):
                    matches.setdefault(source.name.casefold(), set()).add(source)
            for name, paths in matches.items():
                if len(paths) > 1:
                    diagnostics.append(Diagnostic(
                        "error", "p4_source_ambiguous", f"Multiple Perforce source maps match {name}.", path=source_root,
                    ))
                else:
                    local_sources.add(str(next(iter(paths))))
            if diagnostics:
                return diagnostics

        unique_sources = sorted(local_sources, key=str.casefold)
        opened_sources: list[tuple[str, str]] = []
        own_opened_sources: set[str] = set()
        locally_modified_sources: list[str] = []
        untracked_sources: list[str] = []
        for source in unique_sources:
            try:
                fstat = p4.run_fstat(source)
                if not fstat or "depotFile" not in fstat[0]:
                    untracked_sources.append(source)
                    continue
                opened = p4.run_opened("-a", source)
                if opened:
                    other_opened = [
                        item for item in opened
                        if not (
                            p4.user and p4.client
                            and item.get("user") == p4.user
                            and item.get("client") == p4.client
                        )
                    ]
                    if other_opened:
                        owners = ", ".join(
                            sorted({f"{item.get('user', '?')}@{item.get('client', '?')}" for item in other_opened})
                        )
                        opened_sources.append((source, owners))
                    else:
                        own_opened_sources.add(source)
                elif Path(source).is_file() and p4.run_diff("-se", source):
                    locally_modified_sources.append(source)
            except P4Exception as exc:
                diagnostics.append(Diagnostic("error", "p4_check_failed", _p4_error_text(p4, "Perforce source check failed.", exc), path=Path(source)))
                return diagnostics

        if opened_sources:
            details = "; ".join(f"{Path(path).name} by {owners}" for path, owners in opened_sources[:5])
            if len(opened_sources) > 5:
                details += f"; and {len(opened_sources) - 5} more"
            diagnostics.append(Diagnostic(
                "error", "p4_opened",
                f"Sync stopped: {len(opened_sources)} source PNG(s) are checked out ({details}). No UDIM files were generated.",
            ))
        if locally_modified_sources:
            names = ", ".join(Path(path).name for path in locally_modified_sources[:5])
            if len(locally_modified_sources) > 5:
                names += f", and {len(locally_modified_sources) - 5} more"
            diagnostics.append(Diagnostic(
                "error", "p4_local_modified",
                f"Sync stopped: {len(locally_modified_sources)} source PNG(s) have local edits in this workspace "
                f"but are not checked out ({names}). There is no checkout owner to report. "
                "Check them out or turn off sync to build from local files.",
            ))
        if untracked_sources:
            names = ", ".join(Path(path).name for path in untracked_sources[:5])
            if len(untracked_sources) > 5:
                names += f", and {len(untracked_sources) - 5} more"
            diagnostics.append(Diagnostic(
                "error", "p4_untracked",
                f"Sync stopped: {len(untracked_sources)} source PNG(s) are not tracked in this Perforce workspace ({names}). "
                "Add them to Perforce or turn off sync to build from local files.",
            ))
        if diagnostics:
            return diagnostics

        for source in unique_sources:
            if source in own_opened_sources:
                continue
            try:
                p4.run_sync(source)
            except P4Exception as exc:
                diagnostics.append(Diagnostic("error", "p4_sync_failed", _p4_error_text(p4, "Perforce sync failed.", exc), path=Path(source)))
                return diagnostics
    except P4Exception as exc:
        diagnostics.append(Diagnostic("error", "p4_connect_failed", _p4_error_text(p4, "Could not connect to Perforce.", exc)))
    finally:
        if p4.connected():
            p4.disconnect()

    return diagnostics


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def execute_plan(plan: SetPlan) -> BuildResult:
    diagnostics = list(plan.diagnostics)
    if not plan.is_valid or not plan.udim_files_path:
        return BuildResult(plan, False, "validation_failed", diagnostics)

    output_path = plan.udim_files_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path = Path(tempfile.mkdtemp(prefix=f".{plan.set_name}-udim-", dir=output_path.parent))
    staged_files: list[tuple[Path, Path]] = []
    moved_backups: dict[Path, Path] = {}
    installed: list[Path] = []
    rollback_incomplete = False
    try:
        for tile in plan.tiles:
            for channel in CHANNELS:
                source = tile.sources[channel]
                if source is None:
                    raise RuntimeError(f"Missing validated source for {tile.texture_name} {channel}")
                staged_target = stage_path / tile.targets[channel].name
                shutil.copy2(source, staged_target)
                staged_files.append((staged_target, tile.targets[channel]))

        output_path.mkdir(parents=True, exist_ok=True)
        backup_path = stage_path / "backups"
        backup_path.mkdir()

        for staged_file, final_file in staged_files:
            final_file.parent.mkdir(parents=True, exist_ok=True)
            if final_file.exists():
                backup_file = backup_path / final_file.name
                os.replace(final_file, backup_file)
                moved_backups[final_file] = backup_file
            os.replace(staged_file, final_file)
            installed.append(final_file)

        return BuildResult(plan, True, "built", diagnostics)
    except Exception as exc:
        diagnostics.append(Diagnostic("error", "build_failed", f"Build failed; output rollback was attempted: {exc}", path=output_path))
        for installed_file in reversed(installed):
            try:
                if installed_file.exists():
                    installed_file.unlink()
            except OSError as rollback_exc:
                rollback_incomplete = True
                diagnostics.append(Diagnostic(
                    "error", "rollback_failed", f"Could not remove {installed_file}: {rollback_exc}", path=installed_file,
                ))
        for final_file, backup_file in moved_backups.items():
            try:
                if not backup_file.exists():
                    raise FileNotFoundError(backup_file)
                os.replace(backup_file, final_file)
            except OSError as rollback_exc:
                rollback_incomplete = True
                diagnostics.append(
                    Diagnostic("error", "rollback_failed", f"Could not restore {final_file}: {rollback_exc}", path=final_file)
                )
        return BuildResult(plan, False, "build_failed", diagnostics)
    finally:
        if rollback_incomplete:
            diagnostics.append(Diagnostic(
                "warning", "stage_preserved",
                "Rollback could not restore every output. Backups were preserved here; restore them before deleting this folder.",
                path=stage_path,
            ))
        else:
            try:
                shutil.rmtree(stage_path)
            except OSError as exc:
                diagnostics.append(Diagnostic(
                    "warning", "stage_cleanup_failed",
                    f"Could not remove temporary UDIM staging folder: {exc}. Inspect it before deleting; do not submit it.",
                    path=stage_path,
                ))


def write_combined_manifest(results: list[BuildResult], manifest_path: Path) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at_utc": utc_timestamp(),
        "success": bool(results) and all(result.success for result in results),
        "sets": [result.plan.to_manifest(result.status, result.diagnostics) for result in results],
    }
    _atomic_write_json(Path(manifest_path), payload)
    return Path(manifest_path).resolve()


def run_configs(
    config_paths: Iterable[Path],
    sync: bool = True,
    manifest_path: Path | None = None,
) -> tuple[list[BuildResult], Path]:
    initial_plans = [build_plan(Path(config_path)) for config_path in config_paths]
    destination = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST_PATH

    results: list[BuildResult] = []
    for initial_plan in initial_plans:
        config_path = initial_plan.config_path
        sync_diagnostics: list[Diagnostic] = []
        if sync and initial_plan.original_files_path and initial_plan.tiles:
            sync_diagnostics = sync_plan_sources(initial_plan)
            plan = build_plan(Path(config_path))
            plan.diagnostics = sync_diagnostics + plan.diagnostics
        else:
            plan = initial_plan
        results.append(execute_plan(plan))

    return results, write_combined_manifest(results, destination)
