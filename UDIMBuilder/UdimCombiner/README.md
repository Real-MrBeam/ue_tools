# UDIM Builder

UDIM Builder validates source textures, previews their final tile layout, generates the Unreal-facing PNG files, and writes a manifest the Unreal editor importer reads.

## Setup

Install Python and make sure `python` is on `PATH`, then run `UdimCombiner.bat`. It installs PySide6 and P4Python if they are missing. To install them yourself:

```text
python -m pip install -r requirements.txt
```

You can also open it from Unreal with `Tools > UDIM Tools > Open UDIM Builder`.

The first Unreal action that needs the builder asks you to locate `UdimCombiner.bat`, and saves the path in the project's `Saved/Config/Python/UDIMSettings.ini`. If `python` is not on `PATH`, add `UDIMPythonExecutable` to the same file:

```ini
[Settings]
UDIMScriptPath=D:\Art\UdimCombiner\UdimCombiner.bat
UDIMPythonExecutable=C:\Path\To\python.exe
```

## Workflow

1. Select a texture set in the left panel. Green means ready, amber is a warning, red blocks the build.
2. Review the Color, Normal and ORS thumbnails in their final UDIM positions.
3. Resolve any blocking validation errors.
4. Generate the selected set.
5. In Unreal, use `Tools > UDIM Tools > Import Latest UDIM Build`.

`Build and Import UDIMs` does steps 4 and 5 for every set. Failed sets are skipped, and the rest still import.

## Working with tiles

- **New / Edit** manage a set's paths and its Unreal importer settings, so you never have to edit the config by hand. For a new set, choosing the source folder scans it recursively and adds one tile per `T_<name>_C.png`, `_N.png` and `_ORS.png` group, in alphabetical order.
- **Drag a thumbnail** onto another tile to move it to that UDIM position. Tiles in between shift, and Color, Normal and ORS stay together.
- **Add tile** picks a PNG from the source folder and adds its whole tile. **Add tile by name** creates a tile before its maps exist; it fails validation until they do, or until it is marked deprecated.
- **Edit tile** swaps in a different source PNG, **Remove tile** takes it out. Removing a tile does not delete any PNGs.
- **Mark deprecated** uses the placeholder PNGs in `Resources` for that tile instead of its source maps.
- **Double-click** a thumbnail to open that PNG in your image editor. The first use asks which editor to use and remembers it. Right-click for `Open in default app (no checkout)`.
- **Resolution audit** outlines each thumbnail with Unreal's source-resolution audit colour, based on the larger dimension, and shows its pixel size.

Edits are a draft until you press `Save tile changes`; `Revert` discards it. Generating with unsaved changes warns instead of building, and generated PNGs only change when you run Generate.

`Remove` in the left panel moves a set's config to `Configs/Unused`, which takes it out of the list and out of headless builds. Source and generated textures are left alone.

## Validation

For each channel, the first tile (UDIM 1001) sets the required 8- or 16-bit PNG depth, and a different depth later in that channel blocks the build.

Source PNGs are copied without touching their pixels or metadata, so sRGB tags never block a build. On import, the Color asset is saved with sRGB enabled, and Normal and ORS with sRGB disabled.

The generator leaves legacy files alone, and warns when a stray tile could join one of the active UDIM assets during import.

## Perforce

The builder checks out the files it needs and syncs sources before a build. It never submits, reverts or moves changelists.

- **Sources** are synced before generating. Your own checkouts are used as they are. The build stops if a source is checked out by someone else, has local edits without a checkout, or is not tracked.
- **Configs** are checked out when you edit or remove a set. A checkout by another user blocks the edit and names the owner.
- **Build manifests** are local files under `Saved/` and are never checked out.

Turn sync off only when you mean to build from the files you have locally; that does not check them against the depot. Perforce cannot see another artist's unsaved work unless they have the file checked out.

## Config contract

Active texture-set files live in `Configs/`, archived ones in `Configs/Unused/`. Source and output folders are relative to `Configs/`. The latest manifest is `Saved/UDIMBuildManifest.json`, which is where the Unreal importer looks.

The ordered texture names live in the `#textures_start` / `#textures_end` section. Deprecated tiles are listed in an optional `#deprecated_tiles: TileName, AnotherTile` directive; configs without it work unchanged.

The Unreal importer reads these directives, which do not affect generated PNG names or their output folder:

```text
#unreal_destination_path: /Game/Environment/Foliage/Water/_UDIM
#unreal_asset_C: T_Water_UDIM_C
#unreal_asset_N: T_Water_UDIM_N
#unreal_asset_ORS: T_Water_UDIM_ORS
```

Asset names are given explicitly because naming is not uniform across sets: one may omit `_UDIM`, another may map the ORS source channel to an `_ORM` asset.

In the texture-set editor, `Browse...` beside Unreal destination picks a folder inside the project's `Content` directory and writes its `/Game/...` package path. Opened from Unreal, the builder already knows where `Content` is; run standalone, it asks the first time.

Deprecated placeholders match the bit depth of the first valid tile in each channel: `Resources/T_Deprecated_C.png`, `_N.png` and `_ORS.png` for 8-bit, the `_16` versions for 16-bit. Keep both in `Resources`.

## Command line

```text
python UdimCombiner.py                                 build every config
python UdimCombiner.py --ui                            open the UI
python UdimCombiner.py --config WaterTextures.txt      one set
python UdimCombiner.py --config WaterTextures.txt --no-sync
```

Bare config names resolve to `Configs/`. A build emits a `[MANIFEST]` line for the Unreal importer, alongside the older `[REIMPORT]` lines.
