"""EditSync main window: a three-screen flow.

1. Setup — drop footage, pick options
2. Working — live progress while the audio sync runs
3. Done — what was placed where, warnings, one-click reveal in Finder
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSettings,
    Qt,
    QUrl,
)
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..builder import BuildOptions
from ..media import MediaFile, ProbeError, Role, require_tool
from .recreational import RecreationalPage
from .testmode import SecretTrigger, TestModeDialog
from .title_picker import TitleStylePicker
from .update import UpdateFooter, UpdatePill, start_update_check
from .widgets import AnimatedButton, DropZone, FileRow, Segmented, section_label
from .worker import ProbeWorker, SyncJob, SyncOutcome, SyncWorker

VIDEO_FILTER = (
    "Videos and music (*.mp4 *.mov *.m4v *.mts *.m2ts *.avi *.mkv "
    "*.mp3 *.wav *.m4a *.aac *.flac *.aiff *.aif *.ogg)"
)
LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"
VERTICAL_LOGO_PATH = Path(__file__).parent / "assets" / "logo_vertical.png"
ICON_PATH = Path(__file__).parent / "assets" / "icon.png"


def brand_logo(height: int, vertical: bool = False) -> QLabel | None:
    """The 86 Auto Lab mark scaled to `height` px: the wide wordmark by
    default, the stacked vertical logo where a tall format fits better.
    Returns None if the asset is missing."""
    path = VERTICAL_LOGO_PATH if vertical else LOGO_PATH
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    # render at 2x and mark it high-DPI so it stays crisp on retina displays
    scaled = pixmap.scaledToHeight(height * 2, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(2.0)
    label = QLabel()
    label.setPixmap(scaled)
    return label


class ModeSwitch(QWidget):
    """The corner switcher between the app's two sides: Training (the
    one-button sync flow) and Recreational (the studio timeline)."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.seg = Segmented(
            [("training", "Training"), ("recreational", "Recreational")],
            default="training",
        )
        self.seg.changed.connect(window.set_mode)
        layout.addWidget(self.seg)
        self.adjustSize()

    def reposition(self) -> None:
        self.adjustSize()
        self.move(self._window.width() - self.width() - 14, 10)
        self.raise_()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("EditSync — 86 Auto Lab")
        self.setMinimumSize(760, 640)
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setAcceptDrops(True)  # dropping anywhere on the window works

        self.media: list[MediaFile] = []
        self._probe_worker: ProbeWorker | None = None
        self._sync_worker: SyncWorker | None = None
        self._output_dir: Path | None = None
        self._page_fade: QPropertyAnimation | None = None
        self.update_pill = None  # set by start_update_check when out of date

        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)

        self.setup_page = self._build_setup_page()
        self.working_page = self._build_working_page()
        self.done_page = self._build_done_page()
        self.rec_page = RecreationalPage(self)
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.working_page)
        self.stack.addWidget(self.done_page)
        self.stack.addWidget(self.rec_page)

        self.mode = "training"
        self.mode_switch = ModeSwitch(self)

        self._load_settings()
        self._update_worker = start_update_check(self)
        self.mode_switch.reposition()

    def show_update_pill(self, info) -> None:
        """Show (or refresh) the bottom-left update prompt."""
        if self.update_pill is not None:
            self.update_pill.deleteLater()
        self.update_pill = UpdatePill(info, self)

    def set_mode(self, mode: str) -> None:
        """Flip between the training flow and the recreational studio."""
        if mode not in ("training", "recreational"):
            mode = "training"
        self.mode = mode
        self.mode_switch.seg.blockSignals(True)
        self.mode_switch.seg.set_value(mode)
        self.mode_switch.seg.blockSignals(False)
        if mode == "recreational":
            self._pulse.stop()
            self.stack.setCurrentWidget(self.rec_page)
        else:
            self.stack.setCurrentWidget(self.setup_page)
        self.mode_switch.reposition()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.update_pill is not None:
            self.update_pill.reposition()
        self.mode_switch.reposition()

    # ------------------------------------------------------------- setup
    def _build_setup_page(self) -> QWidget:
        # scrollable so smaller windows never crush the option controls
        page = QScrollArea()
        page.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("Root")
        page.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)

        logo = brand_logo(34)
        if logo is not None:
            layout.addWidget(logo)
            # hidden test mode: triple-click the logo (no cursor hint)
            self._secret = SecretTrigger(logo, self)
            self._secret.triggered.connect(self._open_test_mode)
        header = QHBoxLayout()
        title = QLabel("EDITSYNC")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        subtitle = QLabel(
            "Drop everything from the shoot. Your glasses clips are matched "
            "to the main camera by sound and layered onto one timeline."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_paths)
        self.drop_zone.browse_requested.connect(self._browse)
        layout.addWidget(self.drop_zone)

        # file list (hidden until something is added)
        self.files_card = QFrame()
        self.files_card.setObjectName("Card")
        files_layout = QVBoxLayout(self.files_card)
        files_layout.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        self.files_label = section_label("Footage")
        clear = AnimatedButton("Clear all", kind="ghost")
        clear.clicked.connect(self._clear_files)
        header.addWidget(self.files_label)
        header.addStretch(1)
        header.addWidget(clear)
        files_layout.addLayout(header)

        self.file_list_box = QVBoxLayout()
        self.file_list_box.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_inner = QWidget()
        scroll_inner.setLayout(self.file_list_box)
        self.file_list_box.addStretch(1)
        scroll.setWidget(scroll_inner)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(200)
        files_layout.addWidget(scroll)
        self.files_card.hide()
        layout.addWidget(self.files_card)

        # options
        options_card = QFrame()
        options_card.setObjectName("Card")
        opt = QVBoxLayout(options_card)
        opt.setContentsMargins(16, 14, 16, 14)
        opt.setSpacing(12)

        name_row = QHBoxLayout()
        name_row.addWidget(section_label("Project name"))
        self.name_edit = QLineEdit("My Video")
        self.name_edit.setMaximumWidth(320)
        name_row.addWidget(self.name_edit)
        name_row.addStretch(1)
        opt.addLayout(name_row)

        opt.addWidget(section_label("Opening title — leave empty for none"))
        title_fields = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title — e.g. Front Bumper Removal")
        self.title_desc_edit = QLineEdit()
        self.title_desc_edit.setPlaceholderText("Description — e.g. 2024 Toyota GR86")
        title_fields.addWidget(self.title_edit)
        title_fields.addWidget(self.title_desc_edit)
        opt.addLayout(title_fields)

        self.title_style_picker = TitleStylePicker()
        opt.addWidget(self.title_style_picker)
        self.title_edit.textChanged.connect(self._refresh_title_previews)
        self.title_desc_edit.textChanged.connect(self._refresh_title_previews)

        title_timing = QHBoxLayout()
        hold_label = QLabel("Stays on screen")
        hold_label.setObjectName("Hint")
        self.title_hold_slider = QSlider(Qt.Horizontal)
        self.title_hold_slider.setRange(2, 16)  # half-second steps: 1-8 s
        self.title_hold_slider.setValue(6)
        self.title_hold_slider.setMaximumWidth(160)
        self.title_hold_value = QLabel("3.0 s")
        self.title_hold_value.setObjectName("Hint")
        self.title_hold_slider.valueChanged.connect(
            lambda v: self.title_hold_value.setText(f"{v / 2:.1f} s")
        )
        fade_label = QLabel("Fades out over")
        fade_label.setObjectName("Hint")
        self.title_fade_slider = QSlider(Qt.Horizontal)
        self.title_fade_slider.setRange(1, 12)  # quarter-second steps: 0.25-3 s
        self.title_fade_slider.setValue(4)
        self.title_fade_slider.setMaximumWidth(160)
        self.title_fade_value = QLabel("1.00 s")
        self.title_fade_value.setObjectName("Hint")
        self.title_fade_slider.valueChanged.connect(
            lambda v: self.title_fade_value.setText(f"{v / 4:.2f} s")
        )
        for w in (
            hold_label, self.title_hold_slider, self.title_hold_value,
            fade_label, self.title_fade_slider, self.title_fade_value,
        ):
            title_timing.addWidget(w)
        title_timing.addStretch(1)
        opt.addLayout(title_timing)

        opt.addWidget(section_label("Vertical clips look like"))
        self.style_seg = Segmented(
            [
                ("blur-bg", "Blurred background"),
                ("center", "Centered"),
                ("fill", "Fill the frame"),
                ("pip-left", "Small · left"),
                ("pip-right", "Small · right"),
            ],
            default="blur-bg",
        )
        self.style_seg.changed.connect(self._on_style_changed)
        opt.addWidget(self.style_seg)

        blur_row = QHBoxLayout()
        self.blur_label = QLabel("Background blur")
        self.blur_label.setObjectName("Hint")
        self.blur_slider = QSlider(Qt.Horizontal)
        self.blur_slider.setRange(5, 100)
        self.blur_slider.setValue(50)
        self.blur_slider.setMaximumWidth(240)
        self.blur_value = QLabel("50")
        self.blur_value.setObjectName("Hint")
        self.blur_slider.valueChanged.connect(
            lambda v: self.blur_value.setText(str(v))
        )
        blur_row.addWidget(self.blur_label)
        blur_row.addWidget(self.blur_slider)
        blur_row.addWidget(self.blur_value)
        blur_row.addStretch(1)
        opt.addLayout(blur_row)

        opt.addWidget(section_label("While a glasses clip plays"))
        self.duck_seg = Segmented(
            [
                ("-60", "Mute the main camera"),
                ("-18", "Turn it down"),
                ("off", "Leave it alone"),
            ],
            default="-60",
        )
        opt.addWidget(self.duck_seg)

        opt.addWidget(section_label("Background music"))
        self.music_enable = QCheckBox(
            "Loop my music file quietly under the whole video"
        )
        self.music_enable.setToolTip(
            "Drop a song (mp3, wav, m4a...) in with your footage, then turn "
            "this on. It plays at background level so your voice stays clear."
        )
        self.music_enable.toggled.connect(self._on_music_toggled)
        opt.addWidget(self.music_enable)

        music_vol_row = QHBoxLayout()
        self.music_vol_label = QLabel("Music volume")
        self.music_vol_label.setObjectName("Hint")
        self.music_vol_slider = QSlider(Qt.Horizontal)
        self.music_vol_slider.setRange(-40, -8)
        self.music_vol_slider.setValue(-22)
        self.music_vol_slider.setMaximumWidth(240)
        self.music_vol_value = QLabel("-22 dB")
        self.music_vol_value.setObjectName("Hint")
        self.music_vol_slider.valueChanged.connect(
            lambda v: self.music_vol_value.setText(f"{v} dB")
        )
        music_vol_row.addWidget(self.music_vol_label)
        music_vol_row.addWidget(self.music_vol_slider)
        music_vol_row.addWidget(self.music_vol_value)
        music_vol_row.addStretch(1)
        opt.addLayout(music_vol_row)

        self.music_duck = QCheckBox("Silence the music while a glasses clip plays")
        opt.addWidget(self.music_duck)
        self.music_hint = QLabel("Add a music file above to enable this.")
        self.music_hint.setObjectName("Hint")
        opt.addWidget(self.music_hint)
        self._on_music_toggled(False)

        opt.addWidget(section_label("Save for"))
        fmt_row = QHBoxLayout()
        self.fmt_fcp = QCheckBox("Final Cut Pro")
        self.fmt_fcp.setChecked(True)
        self.fmt_premiere = QCheckBox("Premiere Pro")
        self.fmt_otio = QCheckBox("DaVinci Resolve / OTIO")
        self.fmt_capcut = QCheckBox("CapCut")
        self.fmt_capcut.setToolTip(
            "Writes a CapCut desktop draft folder (experimental - CapCut has "
            "no official import format). Instructions are included."
        )
        self.fmt_video = QCheckBox("Finished video (MP4)")
        self.fmt_video.setToolTip(
            "Renders the whole synced edit to one video file - no editing "
            "software needed. Takes a few minutes."
        )
        fmt_row.addWidget(self.fmt_fcp)
        fmt_row.addWidget(self.fmt_premiere)
        fmt_row.addWidget(self.fmt_otio)
        fmt_row.addWidget(self.fmt_capcut)
        fmt_row.addWidget(self.fmt_video)
        fmt_row.addStretch(1)
        opt.addLayout(fmt_row)

        self.lane_per_clip = QCheckBox("Every clip on its own layer")
        self.lane_per_clip.setToolTip(
            "Off: clips share layers when they don't overlap. "
            "On: each glasses clip gets its own layer."
        )
        opt.addWidget(self.lane_per_clip)

        layout.addWidget(options_card)
        layout.addStretch(1)

        bottom = QHBoxLayout()
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("Hint")
        self.sync_btn = AnimatedButton("Sync my footage", kind="primary")
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(self._start_sync)
        bottom.addWidget(self.status_hint, stretch=1)
        bottom.addWidget(self.sync_btn)
        layout.addLayout(bottom)

        layout.addWidget(UpdateFooter())
        return page

    # ----------------------------------------------------------- working
    def _build_working_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(60, 0, 60, 0)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(18)

        logo = brand_logo(120, vertical=True)
        if logo is not None:
            logo.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo, alignment=Qt.AlignHCenter)

        self.working_label = QLabel("Listening to your footage…")
        self.working_label.setObjectName("BigStatus")
        self.working_label.setAlignment(Qt.AlignCenter)
        # slow breathing pulse while the analysis runs
        pulse_effect = QGraphicsOpacityEffect(self.working_label)
        self.working_label.setGraphicsEffect(pulse_effect)
        self._pulse = QPropertyAnimation(pulse_effect, b"opacity", self)
        self._pulse.setStartValue(1.0)
        self._pulse.setKeyValueAt(0.5, 0.55)
        self._pulse.setEndValue(1.0)
        self._pulse.setDuration(1600)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.InOutSine)

        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setFixedHeight(6)

        self.working_detail = QLabel("")
        self.working_detail.setObjectName("Subtitle")
        self.working_detail.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.working_label)
        layout.addWidget(bar)
        layout.addWidget(self.working_detail)
        return page

    # -------------------------------------------------------------- done
    def _build_done_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        logo = brand_logo(24)
        if logo is not None:
            layout.addWidget(logo)
        self.done_title = QLabel("Done")
        self.done_title.setObjectName("Title")
        self.done_summary = QLabel("")
        self.done_summary.setObjectName("Subtitle")
        self.done_summary.setWordWrap(True)
        layout.addWidget(self.done_title)
        layout.addWidget(self.done_summary)

        self.done_details = QPlainTextEdit()
        self.done_details.setReadOnly(True)
        layout.addWidget(self.done_details, stretch=1)

        buttons = QHBoxLayout()
        self.watch_btn = AnimatedButton("Watch the video", kind="primary")
        self.watch_btn.clicked.connect(self._watch_video)
        self.watch_btn.hide()
        self.reveal_btn = AnimatedButton("Show the files", kind="primary")
        self.reveal_btn.clicked.connect(self._reveal_output)
        again = AnimatedButton("Start over")
        again.clicked.connect(self._reset)
        buttons.addStretch(1)
        buttons.addWidget(again)
        buttons.addWidget(self.reveal_btn)
        buttons.addWidget(self.watch_btn)
        layout.addLayout(buttons)
        return page

    # -------------------------------------------------------- transitions
    def _go(self, page: QWidget) -> None:
        """Switch stacked pages with a quick fade-in."""
        if page is self.working_page:
            self._pulse.start()
        else:
            self._pulse.stop()
        # no mode-hopping while the sync analysis is running
        self.mode_switch.setEnabled(page is not self.working_page)
        self.stack.setCurrentWidget(page)
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        self._page_fade = QPropertyAnimation(effect, b"opacity", self)
        self._page_fade.setStartValue(0.0)
        self._page_fade.setEndValue(1.0)
        self._page_fade.setDuration(220)
        self._page_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._page_fade.finished.connect(lambda: page.setGraphicsEffect(None))
        self._page_fade.start()

    # ------------------------------------------------- window-level drops
    def dragEnterEvent(self, event) -> None:
        current = self.stack.currentWidget()
        droppable = current is self.setup_page or (
            current is self.rec_page
            and self.rec_page.screens.currentWidget()
            is self.rec_page.intake_screen
        )
        if event.mimeData().hasUrls() and droppable:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            if self.stack.currentWidget() is self.rec_page:
                self.rec_page.add_paths(paths)
            else:
                self._add_paths(paths)
        event.acceptProposedAction()

    def _on_style_changed(self, value: str) -> None:
        is_blur = value == "blur-bg"
        self.blur_label.setEnabled(is_blur)
        self.blur_slider.setEnabled(is_blur)
        self.blur_value.setEnabled(is_blur)

    def _open_test_mode(self) -> None:
        dialog = TestModeDialog(self)
        if dialog.exec() and dialog.paths:
            if dialog.fill_sample_title and not self.title_edit.text().strip():
                self.title_edit.setText("Front Bumper Removal")
                self.title_desc_edit.setText("2024 Toyota GR86")
            self._add_paths(dialog.paths)

    def _refresh_title_previews(self) -> None:
        self.title_style_picker.update_sample(
            self.title_edit.text(), self.title_desc_edit.text()
        )

    def _music_file(self) -> MediaFile | None:
        return next((m for m in self.media if m.role == Role.MUSIC), None)

    def _on_music_toggled(self, on: bool) -> None:
        has_music = self._music_file() is not None
        self.music_enable.setEnabled(has_music)
        for w in (
            self.music_vol_label,
            self.music_vol_slider,
            self.music_vol_value,
            self.music_duck,
        ):
            w.setEnabled(has_music and on)
        self.music_hint.setVisible(not has_music)

    # ------------------------------------------------------- file intake
    def _browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose footage", "", VIDEO_FILTER
        )
        if files:
            self._add_paths([Path(f) for f in files])

    def _add_paths(self, paths: list[Path]) -> None:
        self.status_hint.setText("Reading files…")
        self._probe_worker = ProbeWorker(paths, self)
        self._probe_worker.file_ready.connect(self._on_file_ready)
        self._probe_worker.file_failed.connect(self._on_file_failed)
        self._probe_worker.done.connect(self._refresh_summary)
        self._probe_worker.start()

    def _on_file_ready(self, media: MediaFile) -> None:
        if any(m.path == media.path for m in self.media):
            return
        self.media.append(media)
        row = FileRow(media)
        row.removed.connect(self._remove_file)
        self.file_list_box.insertWidget(self.file_list_box.count() - 1, row)
        self.files_card.show()

    def _on_file_failed(self, path: str, error: str) -> None:
        QMessageBox.warning(
            self, "Couldn't read a file", f"{Path(path).name}\n\n{error}"
        )

    def _remove_file(self, media: MediaFile) -> None:
        self.media = [m for m in self.media if m.path != media.path]
        for i in range(self.file_list_box.count()):
            item = self.file_list_box.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, FileRow) and w.media.path == media.path:
                w.setParent(None)
                w.deleteLater()
                break
        self._refresh_summary()

    def _clear_files(self) -> None:
        self.media = []
        while self.file_list_box.count() > 1:
            item = self.file_list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.files_card.hide()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        # once footage is in, the drop zone shrinks to give the list room
        self.drop_zone.setMinimumHeight(110 if self.media else 180)
        primaries = [m for m in self.media if m.role == Role.PRIMARY]
        overlays = [m for m in self.media if m.role == Role.OVERLAY]
        music = [m for m in self.media if m.role == Role.MUSIC]
        label = f"FOOTAGE — {len(primaries)} MAIN CAM, {len(overlays)} OVERLAY"
        if music:
            label += f", {len(music)} MUSIC"
        self.files_label.setText(label)
        self._on_music_toggled(self.music_enable.isChecked())
        ready = bool(primaries) and bool(overlays)
        self.sync_btn.setEnabled(ready)
        if not self.media:
            self.status_hint.setText("")
        elif not primaries:
            self.status_hint.setText(
                "Add your main camera footage (the horizontal recording)."
            )
        elif not overlays:
            self.status_hint.setText(
                "Add your glasses clips (the vertical ones)."
            )
        else:
            self.status_hint.setText("Ready when you are.")

    # -------------------------------------------------------------- sync
    def _formats(self) -> list[str]:
        formats = []
        if self.fmt_fcp.isChecked():
            formats.append("fcpxml")
        if self.fmt_premiere.isChecked():
            formats.append("premiere")
        if self.fmt_otio.isChecked():
            formats.append("otio")
        if self.fmt_capcut.isChecked():
            formats.append("capcut")
        if self.fmt_video.isChecked():
            formats.append("video")
        return formats

    def _start_sync(self) -> None:
        formats = self._formats()
        if not formats:
            QMessageBox.information(
                self, "Pick a format", "Choose at least one app to save for."
            )
            return

        default_dir = str(self.media[0].path.parent) if self.media else ""
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should the project file go?", default_dir
        )
        if not chosen:
            return
        self._output_dir = Path(chosen)

        duck = self.duck_seg.value()
        options = BuildOptions(
            project_name=self.name_edit.text().strip() or "My Video",
            duck_db=None if duck == "off" else float(duck),
            overlay_style=self.style_seg.value(),
            blur_amount=float(self.blur_slider.value()),
            lane_per_clip=self.lane_per_clip.isChecked(),
            music_db=float(self.music_vol_slider.value()),
            music_duck=self.music_duck.isChecked(),
            title_text=self.title_edit.text(),
            title_description=self.title_desc_edit.text(),
            title_style=self.title_style_picker.value(),
            title_hold=self.title_hold_slider.value() / 2,
            title_fade=self.title_fade_slider.value() / 4,
        )
        job = SyncJob(
            media=list(self.media),
            options=options,
            formats=formats,
            output_dir=self._output_dir,
            music=self._music_file() if self.music_enable.isChecked() else None,
        )
        self._save_settings()
        self._go(self.working_page)
        self._sync_worker = SyncWorker(job, self)
        self._sync_worker.progress.connect(self.working_detail.setText)
        self._sync_worker.finished_ok.connect(self._on_sync_done)
        self._sync_worker.failed.connect(self._on_sync_failed)
        self._sync_worker.start()

    def _on_sync_done(self, outcome: SyncOutcome) -> None:
        result = outcome.result
        self._video_path = outcome.video
        self.watch_btn.setVisible(outcome.video is not None)
        if outcome.video is not None:
            self.reveal_btn.setText("Show the files")
        placed = [m for m in result.matches if m.placed]
        skipped = [m for m in result.matches if not m.placed]

        self.done_title.setText("Done")
        summary = (
            f"{len(placed)} of {len(result.matches)} glasses clip"
            f"{'s' if len(result.matches) != 1 else ''} matched and layered "
            f"onto your timeline."
        )
        if skipped:
            summary += (
                f" {len(skipped)} couldn't be matched confidently and "
                f"{'was' if len(skipped) == 1 else 'were'} left out — "
                f"details below."
            )
        self.done_summary.setText(summary)

        lines = []
        for m in placed:
            mins, secs = divmod(int(float(m.timeline_start)), 60)
            conf = int(min((m.result.confidence if m.result else 0) / 0.9, 1.0) * 100)
            lines.append(
                f"✓  {m.media.path.name}  →  placed at {mins}:{secs:02d}"
                f"  ({conf}% match)"
            )
        for m in skipped:
            lines.append(f"✕  {m.media.path.name}  —  {m.reason}")
        if result.warnings:
            lines.append("")
            lines.append("Things worth checking:")
            lines += [f"  •  {w}" for w in result.warnings]
        lines.append("")
        lines.append("Files created:")
        lines += [f"  {p.name}" for p in outcome.written]
        if outcome.video is not None:
            lines.append("")
            lines.append(
                "Your finished video is ready - press 'Watch the video', or "
                "share the .mp4 anywhere. No editing software needed."
            )
        if any(p.suffix == ".capcut" for p in outcome.written):
            lines.append("")
            lines.append(
                "CapCut: copy the .capcut folder into CapCut's drafts "
                "directory (INSTRUCTIONS.txt inside has the exact path), "
                "then reopen CapCut."
            )
        if any(p.suffix == ".fcpxml" for p in outcome.written):
            lines.append("")
            lines.append(
                "Next: drag the .fcpxml file into Final Cut Pro and your "
                "synced timeline will appear as a new project."
            )
        self.done_details.setPlainText("\n".join(lines))
        self._go(self.done_page)

    def _on_sync_failed(self, message: str) -> None:
        self._go(self.setup_page)
        QMessageBox.critical(self, "Something went wrong", message)

    def _watch_video(self) -> None:
        if getattr(self, "_video_path", None):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._video_path)))

    def _reveal_output(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def _reset(self) -> None:
        self._go(self.setup_page)

    # ---------------------------------------------------------- settings
    @staticmethod
    def _settings() -> QSettings:
        return QSettings("86 Auto Lab", "EditSync")

    def _load_settings(self) -> None:
        s = self._settings()
        self.name_edit.setText(s.value("project_name", self.name_edit.text()))
        self.style_seg.set_value(s.value("overlay_style", self.style_seg.value()))
        self.duck_seg.set_value(s.value("duck", self.duck_seg.value()))
        self.blur_slider.setValue(s.value("blur_amount", self.blur_slider.value(), type=int))
        self.fmt_fcp.setChecked(s.value("fmt_fcp", True, type=bool))
        self.fmt_premiere.setChecked(s.value("fmt_premiere", False, type=bool))
        self.fmt_otio.setChecked(s.value("fmt_otio", False, type=bool))
        self.fmt_capcut.setChecked(s.value("fmt_capcut", False, type=bool))
        self.fmt_video.setChecked(s.value("fmt_video", False, type=bool))
        self.lane_per_clip.setChecked(s.value("lane_per_clip", False, type=bool))
        self.music_enable.setChecked(s.value("music_enable", False, type=bool))
        self.music_vol_slider.setValue(s.value("music_db", -22, type=int))
        self.music_duck.setChecked(s.value("music_duck", False, type=bool))
        self.title_edit.setText(s.value("title_text", ""))
        self.title_desc_edit.setText(s.value("title_description", ""))
        self.title_style_picker.set_value(
            s.value("title_style", self.title_style_picker.value())
        )
        self.title_hold_slider.setValue(s.value("title_hold_halves", 6, type=int))
        self.title_fade_slider.setValue(s.value("title_fade_quarters", 4, type=int))
        self.rec_page.load_settings(s)
        self.set_mode(s.value("mode", "training"))
        geometry = s.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        s = self._settings()
        s.setValue("project_name", self.name_edit.text())
        s.setValue("overlay_style", self.style_seg.value())
        s.setValue("duck", self.duck_seg.value())
        s.setValue("blur_amount", self.blur_slider.value())
        s.setValue("fmt_fcp", self.fmt_fcp.isChecked())
        s.setValue("fmt_premiere", self.fmt_premiere.isChecked())
        s.setValue("fmt_otio", self.fmt_otio.isChecked())
        s.setValue("fmt_capcut", self.fmt_capcut.isChecked())
        s.setValue("fmt_video", self.fmt_video.isChecked())
        s.setValue("lane_per_clip", self.lane_per_clip.isChecked())
        s.setValue("music_enable", self.music_enable.isChecked())
        s.setValue("music_db", self.music_vol_slider.value())
        s.setValue("music_duck", self.music_duck.isChecked())
        s.setValue("title_text", self.title_edit.text())
        s.setValue("title_description", self.title_desc_edit.text())
        s.setValue("title_style", self.title_style_picker.value())
        s.setValue("title_hold_halves", self.title_hold_slider.value())
        s.setValue("title_fade_quarters", self.title_fade_slider.value())
        self.rec_page.save_settings(s)
        s.setValue("mode", self.mode)
        s.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event) -> None:
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------ checks
    def check_dependencies(self) -> bool:
        try:
            require_tool("ffmpeg")
            require_tool("ffprobe")
            return True
        except ProbeError:
            QMessageBox.critical(
                self,
                "Missing component",
                "EditSync needs ffmpeg to read your footage, and it wasn't "
                "found on this computer.\n\n"
                "If you downloaded EditSync as an app, please re-download it "
                "— ffmpeg should come bundled. Otherwise install it from "
                "ffmpeg.org and reopen EditSync.",
            )
            return False
