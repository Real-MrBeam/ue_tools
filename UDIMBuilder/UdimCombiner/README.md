# UDIM Builder

UDIM Builder validates source textures, previews their final tile layout, generates the Unreal-facing PNG files, and writes a manifest consumed by the Unreal editor importer.

## Setup
 
To run `UdimCombiner.bat` directly, install Python and make sure `python` is available on `PATH`. The batch file checks for PySide6 and P4Python and runs `InstallRequirements.bat` automatically if either is missing or outside the supported versions. You can also install the dependencies manually:

```text
python -m pip install -r requirements.txt
```

You can also launch it from Unreal with `Tools > UDIM Tools > Open UDIM Builder` (under the OL Tools heading).
Unreal launches `UdimCombiner.py` directly with its configured external Python, so that entry point also installs missing requirements before opening the UI or running a build. `--no-sync` skips source PNG sync; build manifests stay local and never need a Perforce checkout.

The first Unreal action that needs the builder will ask you to locate `UdimCombiner.bat` if its saved path is missing or invalid. The chosen path is saved in the project's `Saved/Config/Python/UDIMSettings.ini`; the legacy settings menu is no longer needed. If `python` is not available on `PATH`, add `UDIMPythonExecutable` to the same file:

```ini
[Settings]
UDIMScriptPath=D:\Art\UdimCombiner\UdimCombiner.bat
UDIMPythonExecutable=C:\Path\To\python.exe
```

## Workflow

1. Select a texture set in the left panel.
2. Review Color, Normal and ORS thumbnails in their final UDIM positions.
3. Resolve blocking validation errors.
4. Generate the selected set.
5. In Unreal, use `Tools > UDIM Tools > Import Latest UDIM Build` to import your latest local build.

For each Color, Normal and ORS channel, the first tile (UDIM 1001) sets the required 8- or 16-bit PNG depth. A different depth later in that channel is a blocking validation error. Deprecated tiles use their generated placeholder PNG for this check.

The left list shows green for ready sets, amber for warnings and red for blocking errors. Use `New` or `Edit` to manage set paths and Unreal importer-only settings without editing a text file by hand. For a new set, choosing the source folder scans it recursively and adds one tile per `T_<name>_C.png`, `_N.png` or `_ORS.png` group, initially in alphabetical order. In the main preview, drag a thumbnail onto another tile to move it to that UDIM position; intervening tiles shift, and Color, Normal and ORS stay together. Double-click a thumbnail to edit that channel's original PNG in your chosen image editor; the first use asks you to select its executable and remembers it per artist. Right-click for `Open in default app (no checkout)` or to change the editor. `Resolution audit` outlines each preview thumbnail with the Unreal source-resolution audit color and shows its PNG width and height below the image; the color is based on the larger dimension and does not test Unreal VT build-scale compatibility. `Add tile` opens a PNG picker in the source folder; choosing any C, N or ORS map adds its whole tile. `Add tile by name` allows a tile without source PNGs yet; it will fail validation until its maps exist or it is marked deprecated. Select a tile and use `Edit tile` to choose a different source PNG and replace that tile, `Remove tile` to remove it, or `Mark deprecated` to use the matching `Resources/T_Deprecated_C.png`, `_N.png` or `_ORS.png` for each generated channel. Deprecated previews show the original channel PNG with pink stripes matching the deprecated frame when it exists, falling back to the placeholder when it does not. Generated files always use the matching channel placeholder. The preview and validation update while you edit, but the config is not changed until `Save tile changes`. `Revert` discards the draft, not an automatic Perforce checkout. Clicking Generate with unsaved tile changes warns instead of building. Generated PNGs are not updated until you run Generate. Removing a tile from the set does not delete its source or old generated PNGs; review any unmanaged-output warning before importing in Unreal. `Remove` in the left panel moves a built-in set config to `Configs/Unused` so it is excluded from the list and headless builds; it leaves source and generated textures in place. An external config opened with `Open another config...` can be removed from this window without moving its file.

The Builder preloads scaled thumbnails in the background for every set and channel, then reuses them when you switch sets or preview channels. Refresh clears the cache so externally updated PNGs are shown. On the first actual tile edit and again before saving, it checks out an existing tracked config in the current Perforce workspace; a checkout by another user or workspace blocks the edit and names the owner. Remove also checks out a tracked config before moving it to `Configs/Unused`; reconcile that move in Perforce. Different sets have separate configs, so artists can work on them independently. New, untracked configs still need to be added or reconciled in Perforce. Build manifests are local files under `Saved/` and are not checked out or submitted. Double-click editing checks Perforce ownership first: it asks before checking out a tracked PNG, opens your own checkout without another prompt, and warns before opening an untracked PNG. If another workspace has it checked out or Perforce is unavailable, editing is blocked; `Open in default app (no checkout)` is still available for viewing, but it does not protect edits. Automatic checkouts use the default changelist; the tool does not submit, revert or move them.

`Build and Import UDIMs` performs steps 4 and 5 for every configured set. Failed sets are skipped; successful sets can still import.

The generator stages PNGs and rollback backups in a temporary `.<set>-udim-*` folder beside the configured output folder. It normally removes that folder after the build. If removal fails, the Builder and command line report a cleanup warning with the exact path; generated tiles remain usable. If a failed build cannot restore an old output, it preserves the folder and its backup instead. Restore that backup before deleting the folder, and never add it to Perforce.

With Perforce sync enabled, the Builder checks all source PNGs before syncing any of them. Your own checkouts in the current Perforce workspace are left untouched and used for the build; other sources are synced, including tracked PNGs missing locally. It stops the build if a source is checked out by another user or workspace, has local edits without a checkout, or is not tracked in the workspace. Normal "up-to-date" responses are not errors. Turn sync off only when you intentionally want to build from current local files; that does not verify they are the latest depot revision. Perforce cannot see another artist's unsaved changes unless that artist checks out the file.

PNG sRGB tags do not block the build; source PNGs are copied without changing their pixels or metadata. During Unreal import, the Color asset is saved with sRGB enabled, while Normal and ORS assets are saved with sRGB disabled. Existing assets are checked on reimport as well.

The generator leaves legacy files untouched. It warns when an extra tile could join one of the active UDIM assets during Unreal import.

## Config contract

Active texture-set files live in `Configs/`; archived sets live in `Configs/Unused/`. Source and output folders in active configs are relative to `Configs/`, so the moved configs use `../../` instead of the old `../`. Archived configs retain these same paths so they work again when moved back into `Configs/`. New sets are created in `Configs/`. When Perforce is connected, the Builder opens new set configs for add as `text+l` and corrects that type when editing a tracked config. If Perforce is unavailable, a new config remains local and the Builder warns that it still needs to be added as `text+l`. The latest local manifest is `Saved/UDIMBuildManifest.json`; the Unreal importer reads it from there. Older manifests beside this README or in output folders are no longer used.

After everyone has the updated Builder and Unreal importer, mark the older manifests for delete in Perforce instead of deleting them locally first. The art depot's `.p4ignore` should ignore `Saved/`; do not add the new local manifest to Perforce.

The ordered texture names remain in the existing `#textures_start` / `#textures_end` section. The Unreal importer-only fields are written to the build manifest and do not change generated PNG names or their output folder. The Unreal integration uses these additional directives:

Deprecated tiles are recorded separately as an optional comma-separated `#deprecated_tiles: TileName, AnotherTile` directive. Older configs without it work unchanged. Removing the directive or using `Restore tile` returns a tile to its ordinary source PNGs.

Deprecated placeholders automatically match the first valid, non-deprecated tile in each channel: 8-bit uses `Resources/T_Deprecated_C.png`, `_N.png` and `_ORS.png`; 16-bit uses `T_Deprecated_C_16.png`, `T_Deprecated_N_16.png` and `T_Deprecated_ORS_16.png`. Keep both versions in `Resources`. If every tile is deprecated, the Builder uses the 8-bit placeholders. Regular tiles with mismatched bit depths still block generation.

In the texture-set editor, `Browse...` beside Unreal destination selects a folder inside the project's `Content` directory and writes its `/Game/...` package path. When opened from Unreal, the builder receives the project Content location automatically. When run standalone, select the project's `Content` folder the first time you browse in that session.

```text
#unreal_destination_path: /Game/Environment/Foliage/Water/_UDIM
#unreal_asset_C: T_Water_UDIM_C
#unreal_asset_N: T_Water_UDIM_N
#unreal_asset_ORS: T_Water_UDIM_ORS
```

Explicit names are required because asset naming is not uniform across all existing sets. For example, one set may omit `_UDIM`, while another maps the ORS source channel to an `_ORM` Unreal asset.

## Command line compatibility

Running `python UdimCombiner.py` still builds every config and emits legacy `[REIMPORT]` lines. It also emits a `[MANIFEST]` line for the new Unreal importer. Bare names such as `--config WaterTextures.txt` resolve to `Configs/`; a root-level legacy config still works if no moved copy exists. To roll back the folder move, move active configs back beside the script, move archived configs back to `Unused/`, and change their source/output paths from `../../` to `../`.

Useful options:

```text
python UdimCombiner.py --ui
python UdimCombiner.py --config WaterTextures.txt
python UdimCombiner.py --config Configs/WaterTextures.txt
python UdimCombiner.py --config WaterTextures.txt --no-sync
```
