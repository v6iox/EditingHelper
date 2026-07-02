"""EditSync main window: a three-screen flow.

1. Setup — drop footage, pick options
2. Working — live progress while the audio sync runs
3. Done — what was placed where, warnings, one-click reveal in Finder
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..builder import BuildOptions
from ..media import MediaFile, ProbeError, Role, require_tool
from .widgets import DropZone, FileRow, Segmented, section_label
from .worker import ProbeWorker, SyncJob, SyncOutcome, SyncWorker

VIDEO_FILTER = "Videos (*.mp4 *.mov *.m4v *.mts *.m2ts *.avi *.mkv)"


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("EditSync")
        self.setMinimumSize(760, 640)

        self.media: list[MediaFile] = []
        self._probe_worker: ProbeWorker | None = None
        self._sync_worker: SyncWorker | None = None
        self._output_dir: Path | None = None

        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)

        self.setup_page = self._build_setup_page()
        self.working_page = self._build_working_page()
        self.done_page = self._build_done_page()
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.working_page)
        self.stack.addWidget(self.done_page)

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

        title = QLabel("EDITSYNC")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Drop everything from the shoot. Your glasses clips are matched "
            "to the main camera by sound and layered onto one timeline."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
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
        clear = QPushButton("Clear all")
        clear.setObjectName("Ghost")
        clear.setCursor(Qt.PointingHandCursor)
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

        opt.addWidget(section_label("Vertical clips look like"))
        self.style_seg = Segmented(
            [
                ("center", "Centered"),
                ("fill", "Fill the frame"),
                ("pip-left", "Small · left"),
                ("pip-right", "Small · right"),
            ],
            default="center",
        )
        opt.addWidget(self.style_seg)

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

        opt.addWidget(section_label("Save for"))
        fmt_row = QHBoxLayout()
        self.fmt_fcp = QCheckBox("Final Cut Pro")
        self.fmt_fcp.setChecked(True)
        self.fmt_premiere = QCheckBox("Premiere Pro")
        self.fmt_otio = QCheckBox("DaVinci Resolve / OTIO")
        fmt_row.addWidget(self.fmt_fcp)
        fmt_row.addWidget(self.fmt_premiere)
        fmt_row.addWidget(self.fmt_otio)
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
        self.sync_btn = QPushButton("Sync my footage")
        self.sync_btn.setObjectName("Primary")
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(self._start_sync)
        bottom.addWidget(self.status_hint, stretch=1)
        bottom.addWidget(self.sync_btn)
        layout.addLayout(bottom)
        return page

    # ----------------------------------------------------------- working
    def _build_working_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(60, 0, 60, 0)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(18)

        self.working_label = QLabel("Listening to your footage…")
        self.working_label.setObjectName("BigStatus")
        self.working_label.setAlignment(Qt.AlignCenter)

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
        self.reveal_btn = QPushButton("Show the files")
        self.reveal_btn.setCursor(Qt.PointingHandCursor)
        self.reveal_btn.clicked.connect(self._reveal_output)
        again = QPushButton("Start over")
        again.setCursor(Qt.PointingHandCursor)
        again.clicked.connect(self._reset)
        buttons.addStretch(1)
        buttons.addWidget(again)
        buttons.addWidget(self.reveal_btn)
        layout.addLayout(buttons)
        return page

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
        self.files_label.setText(
            f"FOOTAGE — {len(primaries)} MAIN CAM, {len(overlays)} OVERLAY"
        )
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
            lane_per_clip=self.lane_per_clip.isChecked(),
        )
        job = SyncJob(
            media=list(self.media),
            options=options,
            formats=formats,
            output_dir=self._output_dir,
        )
        self.stack.setCurrentWidget(self.working_page)
        self._sync_worker = SyncWorker(job, self)
        self._sync_worker.progress.connect(self.working_detail.setText)
        self._sync_worker.finished_ok.connect(self._on_sync_done)
        self._sync_worker.failed.connect(self._on_sync_failed)
        self._sync_worker.start()

    def _on_sync_done(self, outcome: SyncOutcome) -> None:
        result = outcome.result
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
        if any(p.suffix == ".fcpxml" for p in outcome.written):
            lines.append("")
            lines.append(
                "Next: drag the .fcpxml file into Final Cut Pro and your "
                "synced timeline will appear as a new project."
            )
        self.done_details.setPlainText("\n".join(lines))
        self.stack.setCurrentWidget(self.done_page)

    def _on_sync_failed(self, message: str) -> None:
        self.stack.setCurrentWidget(self.setup_page)
        QMessageBox.critical(self, "Something went wrong", message)

    def _reveal_output(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def _reset(self) -> None:
        self.stack.setCurrentWidget(self.setup_page)

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
