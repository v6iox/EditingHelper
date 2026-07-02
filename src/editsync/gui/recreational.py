"""Recreational mode — 'the studio'.

The other side of the app: instead of one straight training-video run,
this is a small auto-editing suite. Footage is still synced by sound
first; then it opens on a real multi-track timeline where the heavy
lifting is one-click automations:

- Tighten dead air     — find and ripple-cut the quiet stretches
- Keep the best moments — keep only the most exciting seconds
- Cut to the beat       — nudge every cut onto the music's beat grid
- portion picking       — double-click any clip, drag over its waveform
- draft preview         — fast rough MP4 to watch right now

plus everything training mode has: framing styles, main-camera muting,
looping background music, and every export format.
"""

from __future__ import annotations

import math
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..autoedit import (
    apply_cuts,
    cut_intervals,
    detect_silences_env,
    score_highlights_env,
)
from ..builder import BuildOptions, add_music, refresh_regions
from ..media import MediaFile, Role
from ..timeline import Timeline
from . import style
from .editor import RangeDialog, TimelineView
from .widgets import AnimatedButton, DropZone, FileRow, Segmented, section_label
from .worker import ExportWorker, ProbeWorker, StudioAnalysis, StudioWorker, SyncJob

MAX_UNDO = 30


def snapshot(timeline: Timeline) -> Timeline:
    """A deep copy for the undo stack (media objects can be shared)."""
    import copy

    return copy.deepcopy(timeline)


class RecreationalPage(QWidget):
    """Two screens: pick footage, then edit on the timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.media: list[MediaFile] = []
        self.analysis: StudioAnalysis | None = None
        self._undo: list[Timeline] = []
        self._original: Timeline | None = None
        self._probe_worker: ProbeWorker | None = None
        self._studio_worker: StudioWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._output_dir: Path | None = None
        self._pending_snapshot: Timeline | None = None

        self.screens = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.screens)
        self.intake_screen = self._build_intake()
        self.editor_screen = self._build_editor()
        self.screens.addWidget(self.intake_screen)
        self.screens.addWidget(self.editor_screen)

    # ------------------------------------------------------------- intake
    def _build_intake(self) -> QWidget:
        page = QScrollArea()
        page.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("Root")
        page.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)

        title = QLabel("THE STUDIO")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Your footage, synced by sound, on a real timeline — then let "
            "the automations do the boring parts: cut the dead air, keep "
            "the good moments, land every cut on the beat."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.add_paths)
        self.drop_zone.browse_requested.connect(self._browse)
        layout.addWidget(self.drop_zone)

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
        inner = QWidget()
        inner.setLayout(self.file_list_box)
        self.file_list_box.addStretch(1)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(110)
        scroll.setMaximumHeight(180)
        files_layout.addWidget(scroll)
        self.files_card.hide()
        layout.addWidget(self.files_card)

        options = QFrame()
        options.setObjectName("Card")
        opt = QVBoxLayout(options)
        opt.setContentsMargins(16, 14, 16, 14)
        opt.setSpacing(12)

        name_row = QHBoxLayout()
        name_row.addWidget(section_label("Project name"))
        self.name_edit = QLineEdit("Studio Cut")
        self.name_edit.setMaximumWidth(320)
        name_row.addWidget(self.name_edit)
        name_row.addStretch(1)
        opt.addLayout(name_row)

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

        opt.addWidget(section_label("Background music"))
        self.music_enable = QCheckBox(
            "Loop my music file quietly under the whole video"
        )
        opt.addWidget(self.music_enable)
        music_row = QHBoxLayout()
        self.music_vol_label = QLabel("Music volume")
        self.music_vol_label.setObjectName("Hint")
        self.music_vol_slider = QSlider(Qt.Horizontal)
        self.music_vol_slider.setRange(-40, -8)
        self.music_vol_slider.setValue(-22)
        self.music_vol_slider.setMaximumWidth(200)
        self.music_vol_value = QLabel("-22 dB")
        self.music_vol_value.setObjectName("Hint")
        self.music_vol_slider.valueChanged.connect(
            lambda v: self.music_vol_value.setText(f"{v} dB")
        )
        music_row.addWidget(self.music_vol_label)
        music_row.addWidget(self.music_vol_slider)
        music_row.addWidget(self.music_vol_value)
        music_row.addStretch(1)
        opt.addLayout(music_row)
        self.music_duck = QCheckBox("Silence the music while a glasses clip plays")
        opt.addWidget(self.music_duck)

        layout.addWidget(options)
        layout.addStretch(1)

        bottom = QHBoxLayout()
        self.intake_status = QLabel("")
        self.intake_status.setObjectName("Hint")
        self.open_btn = AnimatedButton("Open the studio", kind="primary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_studio)
        bottom.addWidget(self.intake_status, stretch=1)
        bottom.addWidget(self.open_btn)
        layout.addLayout(bottom)

        from .update import UpdateFooter

        layout.addWidget(UpdateFooter())
        return page

    # ------------------------------------------------------------- editor
    def _build_editor(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        back = AnimatedButton("← New shoot", kind="ghost")
        back.clicked.connect(self._back_to_intake)
        self.editor_title = QLabel("STUDIO")
        self.editor_title.setObjectName("Title")
        self.duration_label = QLabel("")
        self.duration_label.setObjectName("Hint")
        zoom_out = AnimatedButton("−", kind="ghost")
        zoom_out.setFixedWidth(28)
        zoom_out.clicked.connect(lambda: self.view.set_zoom(self.view.zoom() / 1.4))
        zoom_in = AnimatedButton("＋", kind="ghost")
        zoom_in.setFixedWidth(28)
        zoom_in.clicked.connect(lambda: self.view.set_zoom(self.view.zoom() * 1.4))
        fit = AnimatedButton("Fit", kind="ghost")
        fit.clicked.connect(self.view_fit)
        top.addWidget(back)
        top.addSpacing(10)
        top.addWidget(self.editor_title)
        top.addStretch(1)
        top.addWidget(self.duration_label)
        top.addSpacing(10)
        top.addWidget(zoom_out)
        top.addWidget(zoom_in)
        top.addWidget(fit)
        layout.addLayout(top)

        middle = QHBoxLayout()
        middle.setSpacing(12)

        view_wrap = QVBoxLayout()
        self.view = TimelineView()
        self.view.about_to_edit.connect(self._on_about_to_edit)
        self.view.edited.connect(self._on_edited)
        self.view.selection_changed.connect(self._on_selection)
        view_wrap.addWidget(self.view, stretch=1)
        self.edit_status = QLabel("")
        self.edit_status.setObjectName("Hint")
        view_wrap.addWidget(self.edit_status)
        middle.addLayout(view_wrap, stretch=1)

        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(240)
        side = QVBoxLayout(panel)
        side.setContentsMargins(14, 12, 14, 12)
        side.setSpacing(9)

        side.addWidget(section_label("Auto-edit"))
        self.tighten_btn = AnimatedButton("Tighten dead air")
        self.tighten_btn.setToolTip(
            "Finds the quiet stretches and ripple-cuts them out. Glasses "
            "clips stay perfectly in sync."
        )
        self.tighten_btn.clicked.connect(self.tighten_dead_air)
        side.addWidget(self.tighten_btn)

        self.best_btn = AnimatedButton("Keep the best moments")
        self.best_btn.setToolTip(
            "Scores every second by loudness and activity, keeps the top "
            "moments (plus every glasses clip), cuts the rest."
        )
        self.best_btn.clicked.connect(self.keep_best_moments)
        side.addWidget(self.best_btn)
        keep_row = QHBoxLayout()
        keep_label = QLabel("Keep about")
        keep_label.setObjectName("Hint")
        self.keep_slider = QSlider(Qt.Horizontal)
        self.keep_slider.setRange(1, 12)  # x 15 seconds
        self.keep_slider.setValue(4)
        self.keep_value = QLabel("60 s")
        self.keep_value.setObjectName("Hint")
        self.keep_slider.valueChanged.connect(
            lambda v: self.keep_value.setText(f"{v * 15} s")
        )
        keep_row.addWidget(keep_label)
        keep_row.addWidget(self.keep_slider)
        keep_row.addWidget(self.keep_value)
        side.addLayout(keep_row)

        self.beat_btn = AnimatedButton("Cut to the beat")
        self.beat_btn.setToolTip(
            "Nudges every cut back onto the music's beat grid. Needs a "
            "music file with a steady pulse."
        )
        self.beat_btn.clicked.connect(self.cut_to_beat)
        side.addWidget(self.beat_btn)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {style.LINE_SOFT};")
        side.addWidget(line)

        side.addWidget(section_label("Selected clip"))
        self.pick_btn = AnimatedButton("Pick the part to use")
        self.pick_btn.setEnabled(False)
        self.pick_btn.clicked.connect(self._pick_portion)
        side.addWidget(self.pick_btn)
        self.remove_btn = AnimatedButton("Remove it")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self._remove_selected)
        side.addWidget(self.remove_btn)

        side.addWidget(section_label("Timeline"))
        self.add_btn = AnimatedButton("Add a clip to the end")
        self.add_btn.clicked.connect(self._add_clip_to_end)
        side.addWidget(self.add_btn)
        self.undo_btn = AnimatedButton("Undo")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        side.addWidget(self.undo_btn)
        self.reset_btn = AnimatedButton("Back to the synced cut")
        self.reset_btn.clicked.connect(self.reset_to_sync)
        side.addWidget(self.reset_btn)
        side.addStretch(1)

        middle.addWidget(panel)
        layout.addLayout(middle, stretch=1)

        bottom = QHBoxLayout()
        self.fmt_fcp = QCheckBox("Final Cut")
        self.fmt_premiere = QCheckBox("Premiere")
        self.fmt_otio = QCheckBox("Resolve/OTIO")
        self.fmt_capcut = QCheckBox("CapCut")
        self.fmt_video = QCheckBox("Finished video")
        self.fmt_video.setChecked(True)
        for w in (
            self.fmt_fcp,
            self.fmt_premiere,
            self.fmt_otio,
            self.fmt_capcut,
            self.fmt_video,
        ):
            bottom.addWidget(w)
        bottom.addStretch(1)
        self.preview_btn = AnimatedButton("Draft preview")
        self.preview_btn.setToolTip(
            "A fast, rough MP4 of the current timeline, opened in your "
            "video player. Not export quality."
        )
        self.preview_btn.clicked.connect(self.draft_preview)
        self.export_btn = AnimatedButton("Export", kind="primary")
        self.export_btn.clicked.connect(self.export)
        bottom.addWidget(self.preview_btn)
        bottom.addWidget(self.export_btn)
        layout.addLayout(bottom)
        return page

    # -------------------------------------------------------- file intake
    def _browse(self) -> None:
        from .window import VIDEO_FILTER

        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose footage", "", VIDEO_FILTER
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def add_paths(self, paths: list[Path]) -> None:
        self.intake_status.setText("Reading files…")
        self._probe_worker = ProbeWorker(paths, self)
        self._probe_worker.file_ready.connect(self._on_file_ready)
        self._probe_worker.file_failed.connect(self._on_file_failed)
        self._probe_worker.done.connect(self._refresh_intake)
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
        self._refresh_intake()

    def _clear_files(self) -> None:
        self.media = []
        while self.file_list_box.count() > 1:
            item = self.file_list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.files_card.hide()
        self._refresh_intake()

    def _music_file(self) -> MediaFile | None:
        return next((m for m in self.media if m.role == Role.MUSIC), None)

    def _refresh_intake(self) -> None:
        self.drop_zone.setMinimumHeight(110 if self.media else 180)
        primaries = [m for m in self.media if m.role == Role.PRIMARY]
        overlays = [m for m in self.media if m.role == Role.OVERLAY]
        music = [m for m in self.media if m.role == Role.MUSIC]
        label = f"FOOTAGE — {len(primaries)} MAIN CAM, {len(overlays)} OVERLAY"
        if music:
            label += f", {len(music)} MUSIC"
        self.files_label.setText(label)
        self.open_btn.setEnabled(bool(primaries))
        if not self.media:
            self.intake_status.setText("")
        elif not primaries:
            self.intake_status.setText(
                "Add at least your main camera footage (the horizontal files)."
            )
        else:
            self.intake_status.setText("Ready when you are.")

    # ------------------------------------------------------------ analyze
    def build_options(self) -> BuildOptions:
        duck = self.duck_seg.value()
        return BuildOptions(
            project_name=self.name_edit.text().strip() or "Studio Cut",
            duck_db=None if duck == "off" else float(duck),
            overlay_style=self.style_seg.value(),
            music_db=float(self.music_vol_slider.value()),
            music_duck=self.music_duck.isChecked(),
        )

    def _open_studio(self) -> None:
        self.open_btn.setEnabled(False)
        self.intake_status.setText("Listening to your footage…")
        job = SyncJob(
            media=list(self.media),
            options=self.build_options(),
            formats=[],
            output_dir=Path(tempfile.gettempdir()),
            music=self._music_file() if self.music_enable.isChecked() else None,
        )
        self._studio_worker = StudioWorker(job, self)
        self._studio_worker.progress.connect(self.intake_status.setText)
        self._studio_worker.finished_ok.connect(self._on_analysis)
        self._studio_worker.failed.connect(self._on_analysis_failed)
        self._studio_worker.start()

    def _on_analysis(self, analysis: StudioAnalysis) -> None:
        self.analysis = analysis
        self._undo = []
        self._original = snapshot(analysis.result.timeline)
        self.view.set_timeline(analysis.result.timeline)
        self.view.set_analysis(
            waveforms=analysis.waveforms,
            beats=self._timeline_beats(),
            regions={
                "silence": analysis.silences,
                "highlight": [(h.start, h.end) for h in analysis.highlights],
            },
        )
        self.editor_title.setText(
            (self.name_edit.text().strip() or "STUDIO").upper()
        )
        skipped = [m for m in analysis.result.matches if not m.placed]
        note = f"{len(skipped)} clip(s) couldn't be matched — left out. " if skipped else ""
        self.edit_status.setText(
            note + "Double-click a clip to pick the part you want. "
            "Drag edges to trim, Delete to remove."
        )
        self._refresh_after_edit()
        self.screens.setCurrentWidget(self.editor_screen)
        self.view_fit()
        self.intake_status.setText("")
        self.open_btn.setEnabled(True)

    def _on_analysis_failed(self, message: str) -> None:
        self.open_btn.setEnabled(True)
        self.intake_status.setText("")
        QMessageBox.critical(self, "Something went wrong", message)

    def view_fit(self) -> None:
        self.view.fit()

    def _back_to_intake(self) -> None:
        self.screens.setCurrentWidget(self.intake_screen)

    # -------------------------------------------------------- edit plumbing
    def _on_about_to_edit(self) -> None:
        if self.view.timeline is not None:
            self._pending_snapshot = snapshot(self.view.timeline)

    def _on_edited(self, description: str) -> None:
        if self._pending_snapshot is not None:
            self._undo.append(self._pending_snapshot)
            del self._undo[:-MAX_UNDO]
            self._pending_snapshot = None
        self.edit_status.setText(description)
        self._refresh_after_edit()

    def _on_selection(self, clip) -> None:
        has = clip is not None
        self.pick_btn.setEnabled(has)
        self.remove_btn.setEnabled(has)

    def _timeline_beats(self) -> list[float]:
        """The music's beat grid tiled across the timeline (music loops)."""
        if (
            self.analysis is None
            or self.analysis.beats is None
            or self.analysis.beats.confidence < 0.1
            or not self.analysis.beats.beats
            or self.view.timeline is None
        ):
            return []
        grid = self.analysis.beats
        period = 60.0 / grid.bpm if grid.bpm > 0 else 0.5
        total = float(self.view.timeline.duration)
        # half a period past the end so a cut exactly on the last beat
        # still counts as on-grid
        return list(np.arange(grid.beats[0] % period, total + period / 2, period))

    def _story_envelopes(self) -> tuple[np.ndarray, np.ndarray]:
        """Timeline-domain RMS + onset envelopes assembled from the
        per-file envelopes (cheap — no audio re-decode after edits)."""
        assert self.analysis is not None and self.view.timeline is not None
        fps = 100
        total = float(self.view.timeline.duration)
        n = max(int(total * fps) + 1, 1)
        rms = np.zeros(n)
        onset = np.zeros(n)
        for clip in self.view.timeline.primary_clips:
            key = str(clip.media.path)
            src_rms = self.analysis.rms_envs.get(key)
            src_onset = self.analysis.onset_envs.get(key)
            if src_rms is None:
                continue
            lo = int(float(clip.source_start) * fps)
            hi = lo + int(float(clip.duration) * fps)
            at = int(float(clip.timeline_start) * fps)
            seg = src_rms[lo:hi][: max(0, n - at)]
            rms[at : at + len(seg)] = seg
            if src_onset is not None:
                seg_o = src_onset[lo:hi][: max(0, n - at)]
                onset[at : at + len(seg_o)] = seg_o
        return rms, onset

    def _refresh_after_edit(self) -> None:
        """Re-lay music, recompute duck/blur regions and the analysis
        overlays after any structural change."""
        timeline = self.view.timeline
        if timeline is None or self.analysis is None:
            return
        opts = self.build_options()
        # music passes must cover the (new) duration
        timeline.clips = [c for c in timeline.clips if c.lane >= 0]
        music = self._music_file()
        if music is not None and self.music_enable.isChecked():
            add_music(timeline, music, opts)
        refresh_regions(timeline, opts)

        rms, onset = self._story_envelopes()
        silences = detect_silences_env(rms)
        highlights = score_highlights_env(rms, onset)
        self.view.set_analysis(
            beats=self._timeline_beats(),
            regions={
                "silence": silences,
                "highlight": [(h.start, h.end) for h in highlights],
            },
        )
        total = float(timeline.duration)
        mins, secs = divmod(int(total), 60)
        self.duration_label.setText(f"{mins}:{secs:02d}")
        self.undo_btn.setEnabled(bool(self._undo))
        self.undo_btn.setText(f"Undo ({len(self._undo)})" if self._undo else "Undo")
        self.beat_btn.setEnabled(bool(self._timeline_beats()))

    # --------------------------------------------------------- automations
    def tighten_dead_air(self) -> None:
        timeline = self.view.timeline
        if timeline is None:
            return
        rms, _ = self._story_envelopes()
        cuts = cut_intervals(timeline, detect_silences_env(rms))
        if not cuts:
            self.edit_status.setText("No dead air worth cutting — already tight.")
            return
        removed = sum(float(e - s) for s, e in cuts)
        self.view.apply_timeline(
            apply_cuts(timeline, cuts),
            f"Cut {len(cuts)} quiet stretch{'es' if len(cuts) != 1 else ''} "
            f"(−{removed:.0f}s)",
        )

    def keep_best_moments(self) -> None:
        timeline = self.view.timeline
        if timeline is None:
            return
        target = self.keep_slider.value() * 15
        rms, onset = self._story_envelopes()
        length = 6.0
        top = max(1, math.ceil(target / length))
        highlights = score_highlights_env(rms, onset, length=length, top=top)
        if not highlights:
            self.edit_status.setText("Couldn't find stand-out moments to keep.")
            return
        total = float(timeline.duration)
        keeps = sorted((h.start, h.end) for h in highlights)
        complement: list[tuple[float, float]] = []
        cursor = 0.0
        for s, e in keeps:
            if s > cursor:
                complement.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < total:
            complement.append((cursor, total))
        cuts = cut_intervals(timeline, complement, min_cut=0.4)
        if not cuts:
            self.edit_status.setText("Everything already counts as a best moment.")
            return
        self.view.apply_timeline(
            apply_cuts(timeline, cuts),
            f"Kept the best ~{target}s (plus every glasses clip)",
        )

    def cut_to_beat(self) -> None:
        """Nudge each cut between storyline pieces onto the beat grid.

        Boundaries are fixed one at a time on a working copy — every cut
        shifts what follows relative to the (fixed) music grid, so later
        boundaries are re-evaluated after each fix. Total duration
        strictly decreases each round, so this terminates."""
        timeline = self.view.timeline
        beats = self._timeline_beats()
        if timeline is None or not beats:
            self.edit_status.setText(
                "Add a music file with a steady beat to use this."
            )
            return
        period = beats[1] - beats[0] if len(beats) > 1 else 0.5
        phase = beats[0] % period
        working = timeline  # apply_cuts is pure; view keeps the original
        moved = 0
        for _ in range(200):  # hard stop, boundaries are finite
            total = float(working.duration)
            grid = list(np.arange(phase, total + period, period))
            chosen = None
            for b in (float(c.timeline_end) for c in working.primary_clips):
                prev_beats = [t for t in grid if t < b - 0.05]
                if not prev_beats:
                    continue
                beat = max(prev_beats)
                gap = b - beat
                if gap < 0.05 or gap > period * 0.9:
                    continue  # on the beat already / nearer the next beat
                cuts = cut_intervals(
                    working, [(beat, b)], min_cut=0.04, protect_pad=0.0
                )
                if not cuts:
                    continue  # a glasses clip sits on this cut — leave it
                chosen = cuts
                break
            if chosen is None:
                break
            working = apply_cuts(working, chosen)
            moved += 1
        if moved:
            self.view.apply_timeline(
                working,
                f"Moved {moved} cut{'s' if moved != 1 else ''} onto the beat",
            )
        else:
            self.edit_status.setText("Your cuts already land on the beat.")

    def undo(self) -> None:
        if not self._undo:
            return
        timeline = self._undo.pop()
        self.view.timeline = timeline
        self.view.selected = None
        self.view.selection_changed.emit(None)
        self.view.update()
        self.edit_status.setText("Undone.")
        self._refresh_after_edit()

    def reset_to_sync(self) -> None:
        if self._original is None:
            return
        self._undo.append(snapshot(self.view.timeline))
        del self._undo[:-MAX_UNDO]
        self.view.timeline = snapshot(self._original)
        self.view.selected = None
        self.view.selection_changed.emit(None)
        self.view.update()
        self.edit_status.setText("Back to the freshly synced timeline.")
        self._refresh_after_edit()

    # ------------------------------------------------------ clip commands
    def _pick_portion(self) -> None:
        clip = self.view.selected
        if clip is None:
            return
        dialog = RangeDialog(clip.media, clip, self)
        if dialog.exec():
            start, dur = dialog.selection
            self.view.set_clip_range(clip, start, dur)

    def _remove_selected(self) -> None:
        if self.view.selected is not None:
            self.view.delete_clip(self.view.selected)

    def _add_clip_to_end(self) -> None:
        from .window import VIDEO_FILTER

        file, _ = QFileDialog.getOpenFileName(
            self, "Add a clip", "", VIDEO_FILTER
        )
        if not file:
            return
        from ..media import probe

        try:
            media = probe(Path(file))
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't read that file", str(exc))
            return
        dialog = RangeDialog(media, None, self)
        if dialog.exec():
            start, dur = dialog.selection
            self.view.append_clip(media, start, dur)

    # ------------------------------------------------------------- output
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

    def _busy(self, on: bool) -> None:
        for w in (self.preview_btn, self.export_btn, self.tighten_btn,
                  self.best_btn, self.beat_btn, self.undo_btn, self.reset_btn):
            w.setEnabled(not on)
        if not on:
            self._refresh_after_edit()
            self._on_selection(self.view.selected)

    def draft_preview(self) -> None:
        timeline = self.view.timeline
        if timeline is None:
            return
        self._busy(True)
        out_dir = Path(tempfile.mkdtemp(prefix="editsync_preview_"))
        self._export_worker = ExportWorker(
            snapshot(timeline), [], out_dir, self.build_options(),
            draft=True, parent=self,
        )
        self._export_worker.progress.connect(self.edit_status.setText)
        self._export_worker.finished_ok.connect(self._on_preview_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_preview_done(self, payload) -> None:
        _, video = payload
        self._busy(False)
        self.edit_status.setText("Draft preview ready — opening it.")
        if video is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(video)))

    def export(self) -> None:
        timeline = self.view.timeline
        if timeline is None:
            return
        formats = self._formats()
        if not formats:
            QMessageBox.information(
                self, "Pick a format", "Choose at least one thing to save."
            )
            return
        default_dir = str(self.media[0].path.parent) if self.media else ""
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should it go?", default_dir
        )
        if not chosen:
            return
        self._output_dir = Path(chosen)
        self._busy(True)
        self._export_worker = ExportWorker(
            snapshot(timeline), formats, self._output_dir,
            self.build_options(), parent=self,
        )
        self._export_worker.progress.connect(self.edit_status.setText)
        self._export_worker.finished_ok.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_done(self, payload) -> None:
        written, video = payload
        self._busy(False)
        names = ", ".join(p.name for p in written) or "nothing"
        self.edit_status.setText(f"Saved: {names}")
        if video is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(video)))
        elif self._output_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def _on_export_failed(self, message: str) -> None:
        self._busy(False)
        QMessageBox.critical(self, "Something went wrong", message)

    # ----------------------------------------------------------- settings
    def load_settings(self, s) -> None:
        self.name_edit.setText(s.value("rec_project_name", self.name_edit.text()))
        self.style_seg.set_value(s.value("rec_overlay_style", self.style_seg.value()))
        self.duck_seg.set_value(s.value("rec_duck", self.duck_seg.value()))
        self.music_enable.setChecked(s.value("rec_music_enable", False, type=bool))
        self.music_vol_slider.setValue(s.value("rec_music_db", -22, type=int))
        self.music_duck.setChecked(s.value("rec_music_duck", False, type=bool))
        self.keep_slider.setValue(s.value("rec_keep_quarters", 4, type=int))
        self.fmt_fcp.setChecked(s.value("rec_fmt_fcp", False, type=bool))
        self.fmt_premiere.setChecked(s.value("rec_fmt_premiere", False, type=bool))
        self.fmt_otio.setChecked(s.value("rec_fmt_otio", False, type=bool))
        self.fmt_capcut.setChecked(s.value("rec_fmt_capcut", False, type=bool))
        self.fmt_video.setChecked(s.value("rec_fmt_video", True, type=bool))

    def save_settings(self, s) -> None:
        s.setValue("rec_project_name", self.name_edit.text())
        s.setValue("rec_overlay_style", self.style_seg.value())
        s.setValue("rec_duck", self.duck_seg.value())
        s.setValue("rec_music_enable", self.music_enable.isChecked())
        s.setValue("rec_music_db", self.music_vol_slider.value())
        s.setValue("rec_music_duck", self.music_duck.isChecked())
        s.setValue("rec_keep_quarters", self.keep_slider.value())
        s.setValue("rec_fmt_fcp", self.fmt_fcp.isChecked())
        s.setValue("rec_fmt_premiere", self.fmt_premiere.isChecked())
        s.setValue("rec_fmt_otio", self.fmt_otio.isChecked())
        s.setValue("rec_fmt_capcut", self.fmt_capcut.isChecked())
        s.setValue("rec_fmt_video", self.fmt_video.isChecked())
