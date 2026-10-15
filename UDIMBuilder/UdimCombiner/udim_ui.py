from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QProcess, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QDrag, QIcon, QImageReader, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from udim_core import (
    ASSET_TOKEN_PATTERN,
    CHANNELS,
    CONFIG_DIR,
    SCRIPT_DIR,
    BuildResult,
    SetPlan,
    TilePlan,
    archive_config,
    build_plan,
    checkout_file_for_edit,
    default_unreal_assets,
    discover_texture_names,
    ensure_config_exclusive_open,
    find_config_files,
    load_config_fields,
    reorder_texture_names,
    run_configs,
    save_config_fields,
    source_texture_name,
    unreal_destination_from_content_folder,
)


class BuildWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)

    def __init__(self, config_path: Path, sync: bool):
        super().__init__()
        self.config_path = config_path
        self.sync = sync

    @Slot()
    def run(self) -> None:
        try:
            results, manifest_path = run_configs([self.config_path], sync=self.sync)
            self.finished.emit(results[0], manifest_path)
        except Exception as exc:
            self.failed.emit(str(exc))


class ThumbnailWorker(QObject):
    loaded = Signal(int, str, object)
    finished = Signal()

    def __init__(self, epoch: int, paths: list[str]):
        super().__init__()
        self.epoch = epoch
        self.paths = paths
        self.cancelled = False

    @Slot()
    def run(self) -> None:
        try:
            for path in self.paths:
                if self.cancelled:
                    break
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    size.scale(QSize(92, 92), Qt.KeepAspectRatio)
                    reader.setScaledSize(size)
                self.loaded.emit(self.epoch, path, reader.read())
        finally:
            self.finished.emit()


class TileButton(QToolButton):
    tile_moved = Signal(int, int)
    edit_source_requested = Signal(object)
    open_default_requested = Signal(object)
    choose_editor_requested = Signal()
    DRAG_MIME = "application/x-udim-builder-tile"

    def __init__(
        self, tile: TilePlan, channel: str, thumbnail: QPixmap | None = None,
        editable: bool = True, show_resolution: bool = False, parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.tile = tile
        self.channel = channel
        self.editable = editable
        self._drag_start = None
        self.setAcceptDrops(editable)
        if editable:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(92, 92))
        self.setFixedSize(126 if show_resolution else 112, 174 if show_resolution else 154 if tile.deprecated else 136)
        self.setToolTip(self._tooltip())

        preview_source = tile.preview_source(channel)
        preview_exists = bool(preview_source and preview_source.is_file())
        original_source = tile.original_source(channel)
        original_exists = bool(original_source and original_source.is_file())
        info = tile.original_info(channel)
        if show_resolution:
            if original_exists and info:
                resolution_text = f"{info.width} × {info.height}"
            else:
                resolution_text = "No size" if original_exists else "Missing"
            self.setText(f"{tile.udim}\n{tile.texture_name}\n{resolution_text}")
            if original_exists:
                self.setProperty("resolutionAudit", "true")
                self.setProperty("auditResolution", str(max(info.width, info.height)) if info else "other")
        else:
            self.setText(f"{tile.udim}\n{tile.texture_name}" + ("\nDEPRECATED" if tile.deprecated else ""))

        if preview_exists:
            self.setProperty("tileState", "loading")
            if thumbnail is not None:
                self.set_thumbnail(thumbnail)
        else:
            self.setProperty("tileState", "missing")

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self.setProperty("tileState", "missing")
            return
        if self.tile.deprecated:
            pixmap = pixmap.copy()
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor("#ff79b8"), 4))
            for x in range(-pixmap.height(), pixmap.width() + pixmap.height(), 18):
                painter.drawLine(x, 0, x + pixmap.height(), pixmap.height())
            painter.end()
        self.setIcon(QIcon(pixmap))
        self.setProperty("tileState", "deprecated" if self.tile.deprecated else "ready")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _tooltip(self) -> str:
        source = self.tile.original_source(self.channel)
        target = self.tile.targets[self.channel]
        info = self.tile.original_info(self.channel)
        lines = [f"UDIM {self.tile.udim}", self.tile.texture_name, f"Original source: {source or 'Missing'}", f"Output: {target}"]
        if info:
            lines.append(f"{info.width} x {info.height}, {info.bit_depth}-bit, color type {info.color_type}")
        if self.tile.deprecated:
            lines.append(f"Deprecated: generated channel uses {self.tile.sources[self.channel]}.")
        if source and source.is_file():
            lines.append("Double-click to edit this source PNG; right-click for more options.")
        if self.editable:
            lines.append("Drag onto another tile to move it to that UDIM position.")
        return "\n".join(lines)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self._drag_start = None
        source = self.tile.original_source(self.channel)
        if not source or not source.is_file():
            QMessageBox.information(self, "Source texture unavailable", "This tile has no source PNG for the selected channel.")
        else:
            self.edit_source_requested.emit(source)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        source = self.tile.original_source(self.channel)
        if not source or not source.is_file():
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        edit_action = menu.addAction("Edit in image editor")
        default_action = menu.addAction("Open in default app (no checkout)")
        menu.addSeparator()
        choose_action = menu.addAction("Choose image editor...")
        chosen = menu.exec(event.globalPos())
        if chosen == edit_action:
            self.edit_source_requested.emit(source)
        elif chosen == default_action:
            self.open_default_requested.emit(source)
        elif chosen == choose_action:
            self.choose_editor_requested.emit()

    def mousePressEvent(self, event) -> None:
        self._drag_start = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self.editable
            and self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._drag_start = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.DRAG_MIME, str(self.tile.udim).encode("ascii"))
            drag.setMimeData(mime)
            if not self.icon().isNull():
                drag.setPixmap(self.icon().pixmap(self.iconSize()))
            self.setDown(False)
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _accept_tile_drag(self, event) -> bool:
        source = event.source()
        return (
            self.editable
            and event.mimeData().hasFormat(self.DRAG_MIME)
            and isinstance(source, TileButton)
            and source is not self
            and source.editable
            and source.parentWidget() is self.parentWidget()
        )

    def _show_drop_target(self, visible: bool) -> None:
        self.setProperty("dropTarget", visible)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event) -> None:
        if self._accept_tile_drag(event):
            self._show_drop_target(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._accept_tile_drag(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._show_drop_target(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._show_drop_target(False)
        if not self._accept_tile_drag(event):
            event.ignore()
            return
        source = event.source()
        source_udim, target_udim = source.tile.udim, self.tile.udim
        event.acceptProposedAction()
        self.tile_moved.emit(source_udim, target_udim)

class ConfigEditorDialog(QDialog):
    _standalone_content_root: Path | None = None

    def __init__(self, config_path: Path | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.config_path = config_path
        self.saved_path: Path | None = None
        self.original_bytes: bytes | None = None
        fields = {}
        textures = []
        if config_path:
            fields, textures, self.original_bytes = load_config_fields(config_path)
        self.textures = textures
        self.deprecated_tiles = fields.get("deprecated_tiles", "")

        self.setWindowTitle("Edit texture set" if config_path else "New texture set")
        self.resize(740, 500)
        layout = QVBoxLayout(self)
        location = QLabel(f"Config: {config_path}" if config_path else f"A config file will be created in {CONFIG_DIR}.")
        location.setObjectName("muted")
        location.setWordWrap(True)
        layout.addWidget(location)

        self.inputs: dict[str, QLineEdit] = {}
        builder_group = QGroupBox("UDIM Builder - source and output")
        builder_form = QFormLayout(builder_group)
        importer_group = QGroupBox("Unreal importer only")
        importer_group.setObjectName("unrealImporter")
        importer_layout = QVBoxLayout(importer_group)
        importer_note = QLabel(
            "These values are passed to Unreal in the build manifest. They do not change the generated PNG names or output folder."
        )
        importer_note.setWordWrap(True)
        importer_layout.addWidget(importer_note)
        importer_form = QFormLayout()
        importer_layout.addLayout(importer_form)
        labels = (
            ("udim_set_name", "Set name"),
            ("original_files_path", "Source folder"),
            ("udim_files_path", "Output folder"),
            ("unreal_destination_path", "Unreal destination"),
            ("unreal_asset_C", "Color asset"),
            ("unreal_asset_N", "Normal asset"),
            ("unreal_asset_ORS", "ORS asset"),
        )
        for key, label in labels:
            field = QLineEdit(fields.get(key, ""))
            self.inputs[key] = field
            form = builder_form if key in ("udim_set_name", "original_files_path", "udim_files_path") else importer_form
            if key in ("original_files_path", "udim_files_path"):
                field.setToolTip(f"Relative paths are resolved from {config_path.parent if config_path else CONFIG_DIR}.")
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                browse = QPushButton("Browse...")
                browse.clicked.connect(lambda checked=False, selected=key: self.browse_folder(selected))
                row_layout.addWidget(browse)
                form.addRow(label, row)
            elif key == "unreal_destination_path":
                field.setToolTip("Unreal package folder below /Game/. Browse selects a folder inside the project's Content directory.")
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                browse = QPushButton("Browse...")
                browse.clicked.connect(self.browse_unreal_destination)
                row_layout.addWidget(browse)
                form.addRow(label, row)
            else:
                form.addRow(label, field)
        self.source_scan_label: QLabel | None = None
        if config_path is None:
            self.source_scan_label = QLabel("Choose a source folder to find tiles automatically.")
            self.source_scan_label.setWordWrap(True)
            builder_form.addRow("Tiles found", self.source_scan_label)
            self.inputs["original_files_path"].editingFinished.connect(self.scan_new_source)
        layout.addWidget(builder_group)
        layout.addWidget(importer_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if config_path is None:
            self.inputs["udim_set_name"].textChanged.connect(self.update_default_asset_names)

    def update_default_asset_names(self, name: str) -> None:
        previous = getattr(self, "_default_names", {})
        current = default_unreal_assets(name or "Unnamed")
        for channel in CHANNELS:
            field = self.inputs[f"unreal_asset_{channel}"]
            if not field.text() or field.text() == previous.get(channel):
                field.setText(current[channel])
        self._default_names = current

    def browse_folder(self, key: str) -> None:
        base = self.config_path.parent if self.config_path else CONFIG_DIR
        current = self.inputs[key].text().strip()
        start = (base / current).resolve() if current else base
        folder = QFileDialog.getExistingDirectory(self, "Select folder", str(start if start.is_dir() else base))
        if folder:
            try:
                self.inputs[key].setText(Path(os.path.relpath(folder, base)).as_posix())
            except ValueError:
                self.inputs[key].setText(Path(folder).as_posix())
            if key == "original_files_path" and self.config_path is None:
                self.scan_new_source()

    def scan_new_source(self) -> None:
        if self.source_scan_label is None:
            return
        value = self.inputs["original_files_path"].text().strip()
        if not value:
            self.textures = []
            self.source_scan_label.setText("Choose a source folder to find tiles automatically.")
            return
        folder = (CONFIG_DIR / value).resolve()
        try:
            self.textures = discover_texture_names(folder)
        except (OSError, ValueError) as exc:
            self.textures = []
            self.source_scan_label.setText(str(exc))
            return
        if self.textures:
            preview = ", ".join(self.textures[:4])
            if len(self.textures) > 4:
                preview += ", ..."
            self.source_scan_label.setText(f"{len(self.textures)} tiles: {preview}. Order can be changed in the preview.")
        else:
            self.source_scan_label.setText("No matching source PNGs found. Expected names like T_Bark_C.png, T_Bark_N.png or T_Bark_ORS.png.")

    def _unreal_content_root(self) -> Path | None:
        environment_root = os.environ.get("UDIM_UNREAL_CONTENT_DIR", "").strip()
        root = Path(environment_root).expanduser().resolve() if environment_root else self._standalone_content_root
        if root is not None and root.is_dir() and root.name.casefold() == "content":
            return root

        folder = QFileDialog.getExistingDirectory(self, "Select the Unreal project's Content folder", str(SCRIPT_DIR))
        if not folder:
            return None
        root = Path(folder).resolve()
        if root.name.casefold() != "content":
            QMessageBox.warning(self, "Invalid Content folder", "Select the project's Content folder, not a folder inside it.")
            return None
        ConfigEditorDialog._standalone_content_root = root
        return root

    def browse_unreal_destination(self) -> None:
        content_root = self._unreal_content_root()
        if content_root is None:
            return

        current = self.inputs["unreal_destination_path"].text().strip()
        start = content_root
        if current.startswith("/Game/"):
            candidate = (content_root / current[len("/Game/"):]).resolve()
            try:
                candidate.relative_to(content_root)
            except ValueError:
                pass
            else:
                while not candidate.is_dir() and candidate != content_root:
                    candidate = candidate.parent
                start = candidate

        folder = QFileDialog.getExistingDirectory(self, "Select Unreal destination folder", str(start))
        if not folder:
            return
        try:
            destination = unreal_destination_from_content_folder(content_root, Path(folder))
        except ValueError:
            QMessageBox.warning(self, "Invalid Unreal destination", "Select a folder inside the project's Content directory.")
            return
        self.inputs["unreal_destination_path"].setText(destination)

    def save(self) -> None:
        fields = {key: widget.text().strip() for key, widget in self.inputs.items()}
        fields["deprecated_tiles"] = self.deprecated_tiles
        if self.config_path is None:
            self.scan_new_source()
            if not self.textures:
                QMessageBox.warning(self, "No source tiles", self.source_scan_label.text())
                return
        textures = self.textures
        path = self.config_path or CONFIG_DIR / f"{fields['udim_set_name']}Textures.txt"
        if self.config_path is None and path.exists():
            QMessageBox.warning(self, "Cannot create set", f"A config already exists:\n{path}")
            return
        try:
            checkout_file_for_edit(path)
            save_config_fields(path, fields, textures, expected_bytes=self.original_bytes if self.config_path else b"")
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save texture set", str(exc))
            return
        try:
            if not ensure_config_exclusive_open(path):
                QMessageBox.warning(
                    self, "Texture set saved locally",
                    f"{path.name} was saved, but Perforce is unavailable. Add it as text+l when connected.",
                )
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(
                self, "Perforce file type not set",
                f"{path.name} was saved, but Perforce could not set text+l:\n{exc}",
            )
        self.saved_path = path.resolve()
        self.accept()


class UdimBuilderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UDIM Builder")
        self.resize(1320, 820)
        self.plans: list[SetPlan] = []
        self.extra_configs: list[Path] = []
        self.current_plan: SetPlan | None = None
        self.draft_plan: SetPlan | None = None
        self.draft_textures: list[str] | None = None
        self.draft_deprecated: set[str] | None = None
        self.draft_original_bytes: bytes | None = None
        self.current_channel = "C"
        self.current_tile: TilePlan | None = None
        self.worker_thread: QThread | None = None
        self.worker: BuildWorker | None = None
        self.thumbnail_thread: QThread | None = None
        self.thumbnail_worker: ThumbnailWorker | None = None
        self.thumbnail_epoch = 0
        self.thumbnail_cache: dict[str, QPixmap] = {}
        self.thumbnail_pending: set[str] = set()
        self.visible_thumbnail_buttons: dict[str, list[TileButton]] = {}
        self.closing = False
        self.editor_settings = QSettings("Off Leash Interactive", "UDIM Builder")

        self._build_ui()
        self._apply_style()
        self.refresh_plans()

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(top_bar)
        title_block = QVBoxLayout()
        title = QLabel("UDIM Builder")
        title.setObjectName("appTitle")
        subtitle = QLabel("Validate, preview and generate Unreal virtual-texture tiles")
        subtitle.setObjectName("muted")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        top_layout.addLayout(title_block)
        top_layout.addStretch()

        self.sync_checkbox = QCheckBox("Update source PNGs from Perforce when generating")
        self.sync_checkbox.setChecked(True)
        self.sync_checkbox.setToolTip(
            "Before Generate selected set, check source PNGs and sync them from Perforce. "
            "Your own checkouts in this workspace stay untouched; sources checked out elsewhere, "
            "locally modified without checkout, or untracked stop generation. "
            "Uncheck to build from your current local copies. "
            "This does not change the Unreal importer settings."
        )
        top_layout.addWidget(self.sync_checkbox)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_plans)
        top_layout.addWidget(self.refresh_button)
        self.open_output_button = QPushButton("Open output")
        self.open_output_button.clicked.connect(self.open_output)
        top_layout.addWidget(self.open_output_button)
        self.generate_button = QPushButton("Generate selected set")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self.generate_selected)
        top_layout.addWidget(self.generate_button)
        outer.addWidget(top_bar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        side_panel = QFrame()
        side_panel.setObjectName("sidePanel")
        side_layout = QVBoxLayout(side_panel)
        side_label = QLabel("TEXTURE SETS")
        side_label.setObjectName("sectionLabel")
        side_layout.addWidget(side_label)
        self.set_list = QListWidget()
        self.set_list.setObjectName("setList")
        self.set_list.setIconSize(QSize(14, 14))
        self.set_list.currentRowChanged.connect(self.select_plan)
        side_layout.addWidget(self.set_list, 1)
        set_actions = QHBoxLayout()
        self.new_set_button = QPushButton("New")
        self.new_set_button.clicked.connect(self.new_set)
        set_actions.addWidget(self.new_set_button)
        self.edit_set_button = QPushButton("Edit")
        self.edit_set_button.clicked.connect(self.edit_set)
        set_actions.addWidget(self.edit_set_button)
        self.remove_set_button = QPushButton("Remove")
        self.remove_set_button.clicked.connect(self.remove_set)
        set_actions.addWidget(self.remove_set_button)
        side_layout.addLayout(set_actions)
        self.open_config_button = QPushButton("Open another config...")
        self.open_config_button.clicked.connect(self.open_config)
        side_layout.addWidget(self.open_config_button)
        splitter.addWidget(side_panel)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(18, 16, 18, 14)
        workspace_layout.setSpacing(10)

        heading_row = QHBoxLayout()
        heading_copy = QVBoxLayout()
        self.set_title = QLabel("No set selected")
        self.set_title.setObjectName("setTitle")
        self.path_label = QLabel("")
        self.path_label.setObjectName("muted")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        heading_copy.addWidget(self.set_title)
        heading_copy.addWidget(self.path_label)
        heading_row.addLayout(heading_copy, 1)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summary")
        heading_row.addWidget(self.summary_label)
        workspace_layout.addLayout(heading_row)

        channel_row = QHBoxLayout()
        channel_label = QLabel("Preview channel")
        channel_label.setObjectName("muted")
        channel_row.addWidget(channel_label)
        self.channel_group = QButtonGroup(self)
        self.channel_group.setExclusive(True)
        for channel in CHANNELS:
            button = QPushButton("Color" if channel == "C" else channel)
            button.setCheckable(True)
            button.setProperty("channelButton", True)
            button.clicked.connect(lambda checked=False, value=channel: self.set_channel(value))
            self.channel_group.addButton(button)
            channel_row.addWidget(button)
            if channel == "C":
                button.setChecked(True)
        self.resolution_audit_button = QPushButton("Resolution audit")
        self.resolution_audit_button.setObjectName("resolutionAuditButton")
        self.resolution_audit_button.setCheckable(True)
        self.resolution_audit_button.setToolTip(
            "Outline thumbnails by their source PNG's largest dimension and show width × height below.\n"
            "256 cyan · 512 blue · 1K green · 2K yellow · 4K orange · 8K red · 16K purple."
        )
        self.resolution_audit_button.toggled.connect(self.set_resolution_audit)
        channel_row.addWidget(self.resolution_audit_button)
        channel_row.addStretch()
        grid_hint = QLabel("U runs left to right. V runs bottom to top.")
        grid_hint.setObjectName("muted")
        channel_row.addWidget(grid_hint)
        workspace_layout.addLayout(channel_row)

        tile_actions = QHBoxLayout()
        self.tile_edit_hint = QLabel("Drag to reorder; double-click to open the source PNG.")
        self.tile_edit_hint.setObjectName("muted")
        tile_actions.addWidget(self.tile_edit_hint, 1)
        self.add_tile_button = QPushButton("Add tile")
        self.add_tile_button.setToolTip("Choose a C, N or ORS PNG from the source folder to append its tile.")
        self.add_tile_button.clicked.connect(self.add_tile)
        tile_actions.addWidget(self.add_tile_button)
        self.add_tile_name_button = QPushButton("Add tile by name")
        self.add_tile_name_button.setToolTip("Add a tile name even if its source PNGs are not present yet.")
        self.add_tile_name_button.clicked.connect(self.add_tile_by_name)
        tile_actions.addWidget(self.add_tile_name_button)
        self.edit_tile_button = QPushButton("Edit tile")
        self.edit_tile_button.setToolTip("Choose another C, N or ORS PNG to replace the selected tile's source name.")
        self.edit_tile_button.clicked.connect(self.edit_tile)
        tile_actions.addWidget(self.edit_tile_button)
        self.remove_tile_button = QPushButton("Remove tile")
        self.remove_tile_button.setToolTip("Remove the tile from this set. Source and generated PNGs are not deleted.")
        self.remove_tile_button.clicked.connect(self.remove_tile)
        tile_actions.addWidget(self.remove_tile_button)
        self.deprecated_button = QPushButton("Mark deprecated")
        self.deprecated_button.setToolTip("Use the 8-bit or 16-bit deprecated placeholder matching the first valid, non-deprecated tile in each channel.")
        self.deprecated_button.clicked.connect(self.toggle_deprecated_tile)
        tile_actions.addWidget(self.deprecated_button)
        self.save_tiles_button = QPushButton("Save tile changes")
        self.save_tiles_button.setToolTip("Write tile names and order to the .txt config. Generate afterward to update PNGs.")
        self.save_tiles_button.setObjectName("primaryButton")
        self.save_tiles_button.clicked.connect(self.save_tile_changes)
        tile_actions.addWidget(self.save_tiles_button)
        self.revert_tiles_button = QPushButton("Revert")
        self.revert_tiles_button.clicked.connect(self.revert_tile_changes)
        tile_actions.addWidget(self.revert_tiles_button)
        workspace_layout.addLayout(tile_actions)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setObjectName("preview")
        self.preview_content = QWidget()
        self.preview_grid = QGridLayout(self.preview_content)
        self.preview_grid.setContentsMargins(12, 12, 12, 12)
        self.preview_grid.setHorizontalSpacing(7)
        self.preview_grid.setVerticalSpacing(7)
        self.preview_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview_scroll.setWidget(self.preview_content)
        workspace_layout.addWidget(self.preview_scroll, 1)

        self.detail_label = QLabel("Select a tile to inspect its source and destination.")
        self.detail_label.setObjectName("detailPanel")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        workspace_layout.addWidget(self.detail_label)

        diagnostics_label = QLabel("VALIDATION")
        diagnostics_label.setObjectName("sectionLabel")
        workspace_layout.addWidget(diagnostics_label)
        self.diagnostics_tree = QTreeWidget()
        self.diagnostics_tree.setHeaderLabels(["Level", "Tile", "Message"])
        self.diagnostics_tree.setRootIsDecorated(False)
        self.diagnostics_tree.setMaximumHeight(158)
        self.diagnostics_tree.setColumnWidth(0, 78)
        self.diagnostics_tree.setColumnWidth(1, 150)
        workspace_layout.addWidget(self.diagnostics_tree)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        workspace_layout.addWidget(self.progress)

        splitter.addWidget(workspace)
        splitter.setSizes([235, 1085])
        self.setCentralWidget(central)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #171b21; color: #e9eef4; font-family: 'Segoe UI'; font-size: 13px; }
            QFrame#topBar { background: #222831; border-bottom: 1px solid #343d48; }
            QFrame#sidePanel { background: #1d2229; border-right: 1px solid #343d48; }
            QLabel#appTitle { font-size: 20px; font-weight: 600; }
            QLabel#setTitle { font-size: 22px; font-weight: 600; }
            QLabel#muted { color: #9da9b7; }
            QLabel#sectionLabel { color: #8f9ba8; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
            QLabel#summary { color: #b8c5d2; padding: 7px 10px; background: #232a33; border: 1px solid #38424e; border-radius: 5px; }
            QLabel#detailPanel { background: #222831; border: 1px solid #38424e; border-radius: 6px; padding: 9px; }
            QGroupBox { border: 1px solid #45515e; border-radius: 6px; margin-top: 10px; padding: 10px 8px 6px; background: #20262e; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #a8dfff; }
            QGroupBox#unrealImporter { border-color: #78664d; background: #292720; }
            QGroupBox#unrealImporter::title { color: #f1c46f; }
            QPushButton { background: #2b333e; border: 1px solid #424d5a; border-radius: 5px; padding: 7px 11px; }
            QPushButton:hover { background: #34404c; }
            QPushButton:disabled { color: #707985; background: #242a31; }
            QPushButton#primaryButton { background: #5bc9ff; color: #10212b; border: 0; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #79d4ff; }
            QPushButton[channelButton='true'] { padding: 6px 13px; }
            QPushButton[channelButton='true']:checked { background: #e8edf2; color: #17202a; border-color: #e8edf2; }
            QPushButton#resolutionAuditButton:checked { background: #344d60; border-color: #5bc9ff; }
            QListWidget#setList { background: transparent; border: 0; outline: 0; }
            QListWidget#setList::item { padding: 10px 9px; border-radius: 5px; margin: 1px 0; }
            QListWidget#setList::item:selected { background: #263846; color: #e9eef4; border-left: 3px solid #5bc9ff; }
            QScrollArea#preview { background: #11151a; border: 1px solid #303944; border-radius: 6px; }
            QScrollArea#preview > QWidget > QWidget { background: #11151a; }
            QToolButton { background: #26303a; border: 1px solid #3b4652; border-radius: 5px; padding: 5px; color: #dfe7ef; }
            QToolButton:hover { border-color: #6f8294; background: #2e3944; }
            QToolButton:checked { border: 2px solid #5bc9ff; background: #2b3d49; }
            QToolButton[tileState='missing'] { border: 1px dashed #f1b85b; color: #f1b85b; }
            QToolButton[tileState='loading'] { border: 1px solid #607080; }
            QToolButton[resolutionAudit='true'] { border: 4px solid #475569; }
            QToolButton[auditResolution='256'] { border-color: #06b6d4; }
            QToolButton[auditResolution='512'] { border-color: #3b82f6; }
            QToolButton[auditResolution='1024'] { border-color: #22c55e; }
            QToolButton[auditResolution='2048'] { border-color: #eab308; }
            QToolButton[auditResolution='4096'] { border-color: #f97316; }
            QToolButton[auditResolution='8192'] { border-color: #ef4444; }
            QToolButton[auditResolution='16384'] { border-color: #a855f7; }
            QToolButton[resolutionAudit='true']:checked { background: #344d60; }
            QToolButton[tileState='deprecated'] { border: 3px solid #ff79b8; color: #ffb3d2; }
            QToolButton[dropTarget='true'] { border: 3px solid #5bc9ff; background: #344d60; }
            QTreeWidget { background: #1d2229; border: 1px solid #343d48; alternate-background-color: #20262e; }
            QHeaderView::section { background: #272e37; color: #aeb8c4; border: 0; border-right: 1px solid #39434f; padding: 5px; }
            QProgressBar { border: 1px solid #38424e; border-radius: 4px; text-align: center; background: #20262d; }
            QProgressBar::chunk { background: #5bc9ff; }
            QSplitter::handle { background: #343d48; }
            """
        )

    @Slot()
    def refresh_plans(self) -> None:
        selected_path = self.current_plan.config_path if self.current_plan else None
        self.draft_plan = None
        self.draft_textures = None
        self.draft_deprecated = None
        self.draft_original_bytes = None
        built_in = find_config_files()
        self.extra_configs = [path for path in self.extra_configs if path.is_file() and path not in built_in]
        self.plans = [build_plan(path) for path in [*built_in, *self.extra_configs]]
        self.thumbnail_epoch += 1
        if self.thumbnail_worker:
            self.thumbnail_worker.cancelled = True
        self.thumbnail_cache.clear()
        self.thumbnail_pending = {
            str(source) for plan in self.plans for tile in plan.tiles for channel in CHANNELS
            if (source := tile.preview_source(channel)) and source.is_file()
        }
        self.set_list.clear()
        selected_row = 0
        for row, plan in enumerate(self.plans):
            state = "ERROR" if plan.error_count else "WARN" if plan.warning_count else "READY"
            item = QListWidgetItem(f"{plan.set_name or plan.config_path.stem}\n{state}  |  {len(plan.tiles)} tiles")
            item.setToolTip(str(plan.config_path))
            color = QColor("#ff8585") if plan.error_count else QColor("#f1c46f") if plan.warning_count else QColor("#70d5a7")
            tint = QColor("#40272b") if plan.error_count else QColor("#3d3425") if plan.warning_count else QColor("#20382f")
            item.setForeground(QBrush(color))
            item.setBackground(QBrush(tint))
            pixmap = QPixmap(14, 14)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(2, 2, 10, 10)
            painter.end()
            item.setIcon(QIcon(pixmap))
            self.set_list.addItem(item)
            if selected_path and plan.config_path == selected_path:
                selected_row = row
        if self.plans:
            self.set_list.setCurrentRow(selected_row)
        else:
            self.current_plan = None
            self.render_plan()
        QTimer.singleShot(0, self.start_thumbnail_preload)

    @Slot()
    def start_thumbnail_preload(self) -> None:
        if self.closing or self.thumbnail_thread is not None or not self.thumbnail_pending:
            return
        shown = self.shown_plan()
        visible_first = [
            str(source) for tile in shown.tiles
            if (source := tile.preview_source(self.current_channel)) and str(source) in self.thumbnail_pending
        ] if shown else []
        paths = list(dict.fromkeys([*visible_first, *sorted(self.thumbnail_pending, key=str.casefold)]))
        self.thumbnail_pending.clear()
        thread = QThread(self)
        worker = ThumbnailWorker(self.thumbnail_epoch, paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.loaded.connect(self.thumbnail_loaded)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self.thumbnail_preload_finished)
        self.thumbnail_thread = thread
        self.thumbnail_worker = worker
        thread.start()

    @Slot(int, str, object)
    def thumbnail_loaded(self, epoch: int, path: str, image) -> None:
        if epoch != self.thumbnail_epoch:
            return
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self.thumbnail_cache[path] = pixmap
        for button in self.visible_thumbnail_buttons.get(path, []):
            button.set_thumbnail(pixmap)

    @Slot()
    def thumbnail_preload_finished(self) -> None:
        if self.thumbnail_thread:
            self.thumbnail_thread.deleteLater()
        self.thumbnail_worker = None
        self.thumbnail_thread = None
        if self.thumbnail_pending and not self.closing:
            QTimer.singleShot(0, self.start_thumbnail_preload)

    def queue_plan_thumbnails(self, plan: SetPlan) -> None:
        for tile in plan.tiles:
            for channel in CHANNELS:
                source = tile.preview_source(channel)
                if source and source.is_file() and str(source) not in self.thumbnail_cache:
                    self.thumbnail_pending.add(str(source))
        QTimer.singleShot(0, self.start_thumbnail_preload)

    def select_config_path(self, path: Path) -> None:
        for row, plan in enumerate(self.plans):
            if plan.config_path == path:
                self.set_list.setCurrentRow(row)
                return

    @Slot(int)
    def select_plan(self, row: int) -> None:
        self.current_plan = self.plans[row] if 0 <= row < len(self.plans) else None
        self.draft_plan = None
        self.draft_textures = None
        self.draft_deprecated = None
        self.draft_original_bytes = None
        self.current_tile = None
        self.render_plan()

    @Slot()
    def open_config(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open UDIM config", str(Path.cwd()), "Texture configs (*Textures*.txt);;Text files (*.txt)")
        if not filename:
            return
        path = Path(filename).resolve()
        if path not in self.extra_configs:
            self.extra_configs.append(path)
        self.refresh_plans()
        self.select_config_path(path)

    @Slot()
    def new_set(self) -> None:
        dialog = ConfigEditorDialog(None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_path:
            self.refresh_plans()
            self.select_config_path(dialog.saved_path)

    @Slot()
    def edit_set(self) -> None:
        if not self.current_plan:
            return
        try:
            dialog = ConfigEditorDialog(self.current_plan.config_path, self)
        except OSError as exc:
            QMessageBox.warning(self, "Could not open texture set", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_plans()

    @Slot()
    def remove_set(self) -> None:
        if not self.current_plan:
            return
        path = self.current_plan.config_path
        built_in = path.parent in (CONFIG_DIR, SCRIPT_DIR)
        message = (
            f"Move {path.name} to Unused?\n\nSource and generated textures will stay where they are. Reconcile the config move in Perforce."
            if built_in else f"Remove {path.name} from this window?\n\nThe config file will stay where it is."
        )
        yes = QMessageBox.StandardButton.Yes
        no = QMessageBox.StandardButton.No
        if QMessageBox.question(self, "Remove texture set", message, yes | no, no) != yes:
            return
        if built_in:
            try:
                checkout_file_for_edit(path)
                archive_config(path)
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "Could not remove texture set", str(exc))
                return
        else:
            self.extra_configs = [item for item in self.extra_configs if item != path]
        self.refresh_plans()

    def set_channel(self, channel: str) -> None:
        self.current_channel = channel
        self.render_tiles()
        self.update_detail()

    @Slot(bool)
    def set_resolution_audit(self, enabled: bool) -> None:
        self.render_tiles()

    def shown_plan(self) -> SetPlan | None:
        return self.draft_plan or self.current_plan

    def tile_names(self) -> list[str]:
        if self.draft_textures is not None:
            return self.draft_textures.copy()
        return [tile.texture_name for tile in self.current_plan.tiles] if self.current_plan else []

    def update_tile_controls(self) -> None:
        busy = self.worker_thread is not None
        dirty = self.draft_textures is not None
        has_set = self.current_plan is not None
        selected = self.current_tile is not None
        self.add_tile_button.setEnabled(has_set and not busy)
        self.add_tile_name_button.setEnabled(has_set and not busy)
        self.resolution_audit_button.setEnabled(has_set)
        self.edit_tile_button.setEnabled(selected and not busy)
        self.remove_tile_button.setEnabled(selected and len(self.tile_names()) > 1 and not busy)
        self.deprecated_button.setEnabled(selected and not busy)
        self.deprecated_button.setText("Restore tile" if selected and self.current_tile.deprecated else "Mark deprecated")
        self.save_tiles_button.setEnabled(dirty and not busy)
        self.revert_tiles_button.setEnabled(dirty and not busy)
        self.set_list.setEnabled(not dirty and not busy)
        self.refresh_button.setEnabled(not dirty and not busy)
        self.open_config_button.setEnabled(not dirty and not busy)
        self.new_set_button.setEnabled(not dirty and not busy)
        self.edit_set_button.setEnabled(has_set and not dirty and not busy)
        self.remove_set_button.setEnabled(has_set and not dirty and not busy)
        self.generate_button.setEnabled(bool(has_set and not busy and (dirty or self.current_plan.is_valid)))
        self.tile_edit_hint.setText(
            "Unsaved tile changes - Save or Revert before generating or switching sets."
            if dirty else "Drag to reorder; double-click to open the source PNG."
        )
        self.tile_edit_hint.setStyleSheet("color: #f1c46f;" if dirty else "")

    def tile_edit_state(self) -> tuple[list[str], set[str], bytes] | None:
        if not self.current_plan or self.worker_thread:
            return None
        if self.draft_textures is not None:
            assert self.draft_original_bytes is not None
            assert self.draft_deprecated is not None
            return self.draft_textures.copy(), self.draft_deprecated.copy(), self.draft_original_bytes
        try:
            fields, names, original = load_config_fields(self.current_plan.config_path)
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Could not edit tiles", str(exc))
            return None
        deprecated = {name.strip().casefold() for name in fields["deprecated_tiles"].split(",") if name.strip()}
        current_deprecated = {tile.texture_name.casefold() for tile in self.current_plan.tiles if tile.deprecated}
        if names != [tile.texture_name for tile in self.current_plan.tiles] or deprecated != current_deprecated:
            QMessageBox.warning(self, "Texture set changed", "The config changed on disk. Refresh the set before editing its tiles.")
            self.refresh_plans()
            return None
        return names, deprecated, original

    def set_tile_draft(self, names: list[str], deprecated: set[str], original_bytes: bytes, selected_index: int | None) -> None:
        saved_names = [tile.texture_name for tile in self.current_plan.tiles]
        saved_deprecated = {tile.texture_name.casefold() for tile in self.current_plan.tiles if tile.deprecated}
        changed = names != saved_names or deprecated != saved_deprecated
        if changed and self.draft_textures is None:
            try:
                checkout_file_for_edit(self.current_plan.config_path)
            except (OSError, RuntimeError) as exc:
                QMessageBox.warning(self, "Texture set checked out elsewhere", str(exc))
                return
        if not changed:
            self.draft_textures = None
            self.draft_deprecated = None
            self.draft_original_bytes = None
            self.draft_plan = None
        else:
            self.draft_textures = names
            self.draft_deprecated = deprecated
            self.draft_original_bytes = original_bytes
            self.draft_plan = build_plan(
                self.current_plan.config_path,
                texture_names_override=names,
                deprecated_names_override=deprecated,
            )
            self.queue_plan_thumbnails(self.draft_plan)
        plan = self.shown_plan()
        self.current_tile = plan.tiles[selected_index] if selected_index is not None and 0 <= selected_index < len(plan.tiles) else None
        self.render_plan()

    def valid_tile_name(self, name: str, names: list[str], replace_index: int | None = None) -> bool:
        if not ASSET_TOKEN_PATTERN.fullmatch(name):
            QMessageBox.warning(self, "Invalid tile name", "Use only letters, numbers and underscores.")
            return False
        if any(existing.casefold() == name.casefold() for index, existing in enumerate(names) if index != replace_index):
            QMessageBox.warning(self, "Duplicate tile", f"{name} is already in this texture set.")
            return False
        return True

    def pick_source_tile_name(self, title: str) -> str | None:
        source_folder = self.current_plan.original_files_path if self.current_plan else None
        if source_folder is None or not source_folder.is_dir():
            QMessageBox.warning(self, "Source folder missing", "Set an existing source folder in Edit texture set first.")
            return None
        filename, _ = QFileDialog.getOpenFileName(self, title, str(source_folder), "PNG textures (*.png *.PNG);;All files (*)")
        if not filename:
            return None
        path = Path(filename).resolve()
        try:
            path.relative_to(source_folder.resolve())
        except ValueError:
            QMessageBox.warning(self, "Outside source folder", "Choose a PNG inside this texture set's source folder.")
            return None
        name = source_texture_name(path) if path.is_file() else None
        if not name:
            QMessageBox.warning(self, "Invalid source texture", "Choose a PNG named like T_Bark_C.png, T_Bark_N.png or T_Bark_ORS.png.")
            return None
        return name

    @Slot()
    def add_tile(self) -> None:
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        name = self.pick_source_tile_name("Add tile from source PNG")
        if name is None or not self.valid_tile_name(name, names):
            return
        names.append(name)
        self.set_tile_draft(names, deprecated, original, len(names) - 1)

    @Slot()
    def add_tile_by_name(self) -> None:
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        value, accepted = QInputDialog.getText(self, "Add tile by name", "Texture name (without T_ or channel suffix):")
        if not accepted:
            return
        name = value.strip()
        if not self.valid_tile_name(name, names):
            return
        names.append(name)
        self.set_tile_draft(names, deprecated, original, len(names) - 1)

    @Slot()
    def edit_tile(self) -> None:
        if not self.current_tile:
            return
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        index = self.current_tile.udim - 1001
        name = self.pick_source_tile_name("Replace tile from source PNG")
        if name is None or not self.valid_tile_name(name, names, replace_index=index):
            return
        old_name = names[index]
        names[index] = name
        if old_name.casefold() in deprecated:
            deprecated.remove(old_name.casefold())
            deprecated.add(name.casefold())
        self.set_tile_draft(names, deprecated, original, index)

    @Slot()
    def remove_tile(self) -> None:
        if not self.current_tile:
            return
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        if len(names) <= 1:
            return
        index = self.current_tile.udim - 1001
        deprecated.discard(names.pop(index).casefold())
        self.set_tile_draft(names, deprecated, original, min(index, len(names) - 1))

    @Slot()
    def toggle_deprecated_tile(self) -> None:
        if not self.current_tile:
            return
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        index = self.current_tile.udim - 1001
        name = names[index].casefold()
        if name in deprecated:
            deprecated.remove(name)
        else:
            deprecated.add(name)
        self.set_tile_draft(names, deprecated, original, index)

    @Slot(int, int)
    def move_tile(self, source_udim: int, target_udim: int) -> None:
        if source_udim == target_udim:
            return
        state = self.tile_edit_state()
        if state is None:
            return
        names, deprecated, original = state
        source_index = source_udim - 1001
        target_index = target_udim - 1001
        if not 0 <= source_index < len(names) or not 0 <= target_index < len(names):
            return
        self.set_tile_draft(reorder_texture_names(names, source_index, target_index), deprecated, original, target_index)

    @Slot()
    def save_tile_changes(self) -> None:
        if self.draft_textures is None or not self.current_plan or self.draft_original_bytes is None:
            return
        path = self.current_plan.config_path
        try:
            fields, _, _ = load_config_fields(path)
            assert self.draft_deprecated is not None
            fields["deprecated_tiles"] = ", ".join(
                name for name in self.draft_textures if name.casefold() in self.draft_deprecated
            )
            checkout_file_for_edit(path)
            save_config_fields(path, fields, self.draft_textures, expected_bytes=self.draft_original_bytes)
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save tile changes", str(exc))
            return
        self.draft_plan = None
        self.draft_textures = None
        self.draft_deprecated = None
        self.draft_original_bytes = None
        self.refresh_plans()
        self.select_config_path(path)
        self.statusBar().showMessage("Tile changes saved. Generate the set to update its PNGs before importing in Unreal.", 15000)

    @Slot()
    def revert_tile_changes(self) -> None:
        if self.draft_textures is None:
            return
        self.draft_plan = None
        self.draft_textures = None
        self.draft_deprecated = None
        self.draft_original_bytes = None
        self.render_plan()

    def closeEvent(self, event) -> None:
        if self.worker_thread is not None:
            QMessageBox.information(self, "Build in progress", "Wait for the current UDIM build to finish before closing.")
            event.ignore()
            return
        if self.draft_textures is not None:
            answer = QMessageBox.question(
                self,
                "Unsaved tile changes",
                "Close UDIM Builder and discard unsaved tile changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.closing = True
        if self.thumbnail_worker and self.thumbnail_thread:
            self.thumbnail_worker.cancelled = True
            self.thumbnail_thread.quit()
            if not self.thumbnail_thread.wait(5000):
                self.closing = False
                event.ignore()
                self.statusBar().showMessage("Waiting for thumbnail loading to finish before closing.", 5000)
                return
        super().closeEvent(event)

    def render_plan(self) -> None:
        plan = self.shown_plan()
        if not plan:
            self.set_title.setText("No texture sets found")
            self.path_label.clear()
            self.summary_label.clear()
            self.open_output_button.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.edit_set_button.setEnabled(False)
            self.remove_set_button.setEnabled(False)
            self._clear_grid()
            self.diagnostics_tree.clear()
            self.current_tile = None
            self.update_detail()
            self.update_tile_controls()
            return

        self.set_title.setText((plan.set_name or plan.config_path.stem) + (" *" if self.draft_textures is not None else ""))
        self.path_label.setText(f"{plan.original_files_path}  ->  {plan.udim_files_path}")
        expected = len(plan.tiles) * len(CHANNELS)
        self.summary_label.setText(
            f"{len(plan.tiles)} tiles  |  {expected} maps  |  {plan.existing_output_count} existing  |  "
            f"{plan.error_count} errors  |  {plan.warning_count} warnings"
        )
        self.open_output_button.setEnabled(bool(plan.udim_files_path))
        self.generate_button.setEnabled(plan.is_valid)
        self.edit_set_button.setEnabled(self.worker_thread is None)
        self.remove_set_button.setEnabled(self.worker_thread is None)
        self.render_tiles()
        self.render_diagnostics()
        self.update_detail()
        self.update_tile_controls()

    def _clear_grid(self) -> None:
        self.visible_thumbnail_buttons.clear()
        while self.preview_grid.count():
            item = self.preview_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def render_tiles(self) -> None:
        self._clear_grid()
        plan = self.shown_plan()
        if not plan or not plan.tiles:
            self.current_tile = None
            self.update_tile_controls()
            return

        selected_udim = self.current_tile.udim if self.current_tile else None
        self.current_tile = next((tile for tile in plan.tiles if tile.udim == selected_udim), None)

        max_v = max((tile.udim - 1001) // 10 for tile in plan.tiles)
        self.preview_grid.addWidget(QLabel("V / U"), 0, 0, alignment=Qt.AlignCenter)
        for u in range(10):
            label = QLabel(f"U{u}")
            label.setObjectName("muted")
            self.preview_grid.addWidget(label, 0, u + 1, alignment=Qt.AlignCenter)

        for v in range(max_v + 1):
            row = max_v - v + 1
            label = QLabel(f"V{v}")
            label.setObjectName("muted")
            self.preview_grid.addWidget(label, row, 0, alignment=Qt.AlignCenter)

        for tile in plan.tiles:
            offset = tile.udim - 1001
            u = offset % 10
            v = offset // 10
            row = max_v - v + 1
            preview_source = tile.preview_source(self.current_channel)
            button = TileButton(
                tile,
                self.current_channel,
                thumbnail=self.thumbnail_cache.get(str(preview_source)),
                editable=self.worker_thread is None,
                show_resolution=self.resolution_audit_button.isChecked(),
            )
            button.clicked.connect(lambda checked=False, selected=tile: self.select_tile(selected))
            button.tile_moved.connect(self.move_tile)
            button.edit_source_requested.connect(self.open_source_in_editor)
            button.open_default_requested.connect(self.open_source_default)
            button.choose_editor_requested.connect(self.choose_image_editor)
            self.preview_grid.addWidget(button, row, u + 1)
            if preview_source:
                self.visible_thumbnail_buttons.setdefault(str(preview_source), []).append(button)
            if self.current_tile is tile:
                button.setChecked(True)

        self.preview_grid.setColumnStretch(11, 1)
        self.preview_grid.setRowStretch(max_v + 2, 1)

    def select_tile(self, tile: TilePlan) -> None:
        self.current_tile = tile
        self.update_detail()
        self.update_tile_controls()

    def choose_image_editor(self) -> Path | None:
        current = str(self.editor_settings.value("image_editor_executable", "") or "")
        start = str(Path(current).parent) if current else os.environ.get("ProgramFiles", "C:/Program Files")
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose image editor executable", start, "Applications (*.exe);;All files (*)"
        )
        if not filename:
            return None
        editor = Path(filename).resolve()
        if not editor.is_file():
            QMessageBox.warning(self, "Image editor unavailable", f"The selected program does not exist:\n{editor}")
            return None
        self.editor_settings.setValue("image_editor_executable", str(editor))
        self.editor_settings.sync()
        return editor

    def _image_editor(self) -> Path | None:
        configured = str(self.editor_settings.value("image_editor_executable", "") or "")
        if configured and Path(configured).is_file():
            return Path(configured)
        return self.choose_image_editor()

    def _confirm_source_checkout(self, source: Path) -> bool:
        return QMessageBox.question(
            self,
            "Check out source texture?",
            f"{source.name} is not checked out. Check it out in your current Perforce workspace and open it in the image editor?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _confirm_untracked_source(self, source: Path) -> bool:
        return QMessageBox.question(
            self,
            "Source texture not in Perforce",
            f"{source.name} is not tracked in this Perforce workspace. Editing it will not be protected by a checkout. Open it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    @Slot(object)
    def open_source_in_editor(self, source: Path) -> None:
        source = Path(source).resolve()
        if not source.is_file():
            QMessageBox.warning(self, "Source texture unavailable", f"The source PNG no longer exists:\n{source}")
            return
        editor = self._image_editor()
        if editor is None:
            return
        try:
            allowed = checkout_file_for_edit(
                source,
                confirm_checkout=self._confirm_source_checkout,
                confirm_untracked=self._confirm_untracked_source,
            )
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cannot edit source texture", str(exc))
            return
        if not allowed:
            return
        launched, _ = QProcess.startDetached(str(editor), [str(source)])
        if not launched:
            QMessageBox.warning(
                self, "Could not start image editor",
                f"Could not launch {editor}. The source may now be checked out in Perforce; check P4V before trying again.",
            )

    @Slot(object)
    def open_source_default(self, source: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(source))):
            QMessageBox.warning(self, "Could not open source texture", f"Windows could not open:\n{source}")

    def update_detail(self) -> None:
        tile = self.current_tile
        if not tile:
            self.detail_label.setText("Select a tile to inspect its source and destination.")
            return
        source = tile.original_source(self.current_channel)
        target = tile.targets[self.current_channel]
        info = tile.original_info(self.current_channel)
        metadata = ""
        if info:
            metadata = f"  |  {info.width} x {info.height}  |  {info.bit_depth}-bit  |  PNG type {info.color_type}"
        self.detail_label.setText(
            f"{tile.texture_name}  |  UDIM {tile.udim}  |  {self.current_channel}{metadata}\n"
            f"{source or 'Missing original source'}\n"
            + (f"Build source: {tile.sources[self.current_channel]}\n" if tile.deprecated else "")
            + f"-> {target}"
        )

    def render_diagnostics(self) -> None:
        self.diagnostics_tree.clear()
        plan = self.shown_plan()
        if not plan:
            return
        if not plan.diagnostics:
            item = QTreeWidgetItem(["READY", "", "All source maps passed validation."])
            item.setForeground(0, QColor("#70d5a7"))
            self.diagnostics_tree.addTopLevelItem(item)
            return
        for diagnostic in plan.diagnostics:
            tile = diagnostic.texture_name or ""
            if diagnostic.channel:
                tile = f"{tile} [{diagnostic.channel}]"
            item = QTreeWidgetItem([diagnostic.severity.upper(), tile, diagnostic.message])
            color = QColor("#ff8585") if diagnostic.severity == "error" else QColor("#f1c46f")
            item.setForeground(0, color)
            if diagnostic.path:
                item.setToolTip(2, str(diagnostic.path))
            self.diagnostics_tree.addTopLevelItem(item)

    @Slot()
    def open_output(self) -> None:
        if not self.current_plan or not self.current_plan.udim_files_path:
            return
        self.current_plan.udim_files_path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_plan.udim_files_path)))

    @Slot()
    def generate_selected(self) -> None:
        if self.draft_textures is not None:
            QMessageBox.warning(
                self, "Unsaved tile changes",
                "Save or revert your tile changes before generating this texture set.",
            )
            return
        if not self.current_plan or not self.current_plan.is_valid or self.worker_thread:
            return
        self.generate_button.setEnabled(False)
        self.set_list.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.open_config_button.setEnabled(False)
        self.new_set_button.setEnabled(False)
        self.edit_set_button.setEnabled(False)
        self.remove_set_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Building and validating %p%")
        self.progress.setVisible(True)

        thread = QThread(self)
        worker = BuildWorker(self.current_plan.config_path, self.sync_checkbox.isChecked())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.build_finished)
        worker.failed.connect(self.build_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self.clear_worker)
        self.worker_thread = thread
        self.worker = worker
        thread.start()
        self.render_tiles()
        self.update_tile_controls()

    @Slot(object, object)
    def build_finished(self, result: BuildResult, manifest_path: Path) -> None:
        cleanup_warnings = [
            f"{item.message}\nFolder: {item.path}" for item in result.diagnostics
            if item.code in ("stage_cleanup_failed", "stage_preserved")
        ]
        if result.success:
            message = f"Generated {len(result.plan.tiles) * len(CHANNELS)} maps for {result.plan.set_name}.\n\nManifest:\n{manifest_path}"
            if cleanup_warnings:
                QMessageBox.warning(self, "UDIM build complete; cleanup needed", message + "\n\n" + "\n".join(cleanup_warnings))
            else:
                QMessageBox.information(self, "UDIM build complete", message)
        else:
            errors = "\n".join(item.message for item in result.diagnostics if item.severity == "error")
            details = "\n".join(filter(None, [errors, *cleanup_warnings]))
            QMessageBox.critical(self, "UDIM build failed", details or "The build did not complete.")
        self.refresh_plans()

    @Slot(str)
    def build_failed(self, message: str) -> None:
        QMessageBox.critical(self, "UDIM build failed", message)

    @Slot()
    def clear_worker(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self.progress.setVisible(False)
        self.render_plan()


def _set_dark_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#171b21"))
    palette.setColor(QPalette.WindowText, QColor("#e9eef4"))
    palette.setColor(QPalette.Base, QColor("#1d2229"))
    palette.setColor(QPalette.AlternateBase, QColor("#222831"))
    palette.setColor(QPalette.Text, QColor("#e9eef4"))
    palette.setColor(QPalette.Button, QColor("#2b333e"))
    palette.setColor(QPalette.ButtonText, QColor("#e9eef4"))
    palette.setColor(QPalette.Highlight, QColor("#5bc9ff"))
    palette.setColor(QPalette.HighlightedText, QColor("#10212b"))
    app.setPalette(palette)


def run_ui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("UDIM Builder")
    app.setStyle("Fusion")
    _set_dark_palette(app)
    window = UdimBuilderWindow()
    window.show()
    return app.exec()
