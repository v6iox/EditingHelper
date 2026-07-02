"""Background threads so the UI never freezes during probing or syncing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from PySide6.QtCore import QThread, Signal

from .. import audio as audio_mod
from ..autoedit import BeatGrid, detect_beats, detect_silences, score_highlights
from ..builder import BuildOptions, BuildResult, build
from ..media import MediaFile, ProbeError, Role, classify, collect_video_files, probe
from ..report import write_json_report
from ..timeline import Timeline


class ProbeWorker(QThread):
    """Probe + classify dropped files off the UI thread."""

    file_ready = Signal(object)  # MediaFile
    file_failed = Signal(str, str)  # path, error
    done = Signal()

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self) -> None:
        try:
            files = collect_video_files(self.paths)
        except ProbeError as exc:
            self.file_failed.emit(str(self.paths[0]), str(exc))
            self.done.emit()
            return
        media: list[MediaFile] = []
        for f in files:
            try:
                media.append(probe(f))
            except ProbeError as exc:
                self.file_failed.emit(str(f), str(exc))
        classify(media)
        for m in media:
            self.file_ready.emit(m)
        self.done.emit()


@dataclass
class SyncJob:
    media: list[MediaFile]
    options: BuildOptions
    formats: list[str]  # timeline formats; "video" renders a finished MP4
    output_dir: Path
    music: MediaFile | None = None
    write_report: bool = True


@dataclass
class SyncOutcome:
    result: BuildResult
    written: list[Path] = field(default_factory=list)
    video: Path | None = None  # the finished MP4, when rendered


def write_outputs(
    timeline: Timeline,
    formats: list[str],
    output_dir: Path,
    options: BuildOptions,
    progress: Callable[[str], None],
    warnings: list[str] | None = None,
) -> tuple[list[Path], Path | None]:
    """Write every requested format for a finished timeline — shared by
    the training-mode sync flow and the recreational editor's export."""
    from .. import exporters

    written: list[Path] = []
    base = output_dir / options.project_name.replace(" ", "_")
    progress("Writing timeline files...")
    video: Path | None = None
    for fmt in (f for f in formats if f != "video"):
        out = base.with_suffix(exporters.default_extension(fmt))
        exporters.export(fmt, timeline, out)
        written.append(out)
    if "video" in formats:
        from ..renderer import render as render_video
        from ..titlecard import render_card_png

        card_png = None
        if timeline.title_card is not None:
            card_png = base.resolve().with_suffix(".title_card.png")
            render_card_png(
                timeline.title_card, timeline.width, timeline.height, card_png
            )
        video = base.with_suffix(".mp4")
        progress("Rendering your video... 0%")
        render_video(
            timeline,
            video,
            overlay_style=options.overlay_style,
            blur_amount=options.blur_amount,
            card_png=card_png,
            progress=lambda pct: progress(f"Rendering your video... {pct}%"),
        )
        written.append(video)
    if (
        warnings is not None
        and timeline.title_card is not None
        and "premiere" in formats
    ):
        warnings.append(
            "The opening title card only exports to Final Cut Pro — "
            "Premiere's format can't carry it, so add it there by hand."
        )
    return written, video


class SyncWorker(QThread):
    """Run the full sync + export pipeline, reporting progress."""

    progress = Signal(str)
    finished_ok = Signal(object)  # SyncOutcome
    failed = Signal(str)

    def __init__(self, job: SyncJob, parent=None):
        super().__init__(parent)
        self.job = job

    def run(self) -> None:
        try:
            primaries = [m for m in self.job.media if m.role == Role.PRIMARY]
            overlays = [
                m
                for m in self.job.media
                if m.role == Role.OVERLAY and m.has_audio
            ]
            result = build(
                primaries,
                overlays,
                self.job.options,
                progress=self.progress.emit,
                music=self.job.music,
            )

            written, video = write_outputs(
                result.timeline,
                self.job.formats,
                self.job.output_dir,
                self.job.options,
                self.progress.emit,
                warnings=result.warnings,
            )
            if self.job.write_report:
                base = self.job.output_dir / self.job.options.project_name.replace(
                    " ", "_"
                )
                report_path = base.with_suffix(".sync-report.json")
                write_json_report(result, report_path)
                written.append(report_path)

            self.finished_ok.emit(
                SyncOutcome(result=result, written=written, video=video)
            )
        except Exception as exc:  # surfaced in the UI, never a crash
            self.failed.emit(str(exc))


@dataclass
class StudioAnalysis:
    """Everything the recreational editor needs to open a shoot."""

    result: BuildResult
    waveforms: dict[str, np.ndarray]  # per-file display envelopes
    rms_envs: dict[str, np.ndarray]  # per-file raw RMS envelopes (100 fps)
    onset_envs: dict[str, np.ndarray]  # per-file onset envelopes (100 fps)
    silences: list[tuple[float, float]]  # timeline-domain dead air
    highlights: list  # autoedit.Highlight, timeline-domain
    beats: BeatGrid | None  # from the music file, if any


class StudioWorker(QThread):
    """Sync the shoot, then run the auto-editing analysis over it:
    waveforms for the clips, dead-air + highlight detection over the
    storyline, beat detection over the music."""

    progress = Signal(str)
    finished_ok = Signal(object)  # StudioAnalysis
    failed = Signal(str)

    def __init__(self, job: SyncJob, parent=None):
        super().__init__(parent)
        self.job = job

    def run(self) -> None:
        try:
            from .editor import WAVE_FPS, clip_waveform

            primaries = [m for m in self.job.media if m.role == Role.PRIMARY]
            overlays = [
                m
                for m in self.job.media
                if m.role == Role.OVERLAY and m.has_audio
            ]
            result = build(
                primaries,
                overlays,
                self.job.options,
                progress=self.progress.emit,
                music=self.job.music,
            )
            timeline = result.timeline

            self.progress.emit("Reading the audio for the editor...")
            sr = audio_mod.SAMPLE_RATE
            cache: dict[str, np.ndarray] = {}
            for m in self.job.media:
                if m.has_audio:
                    try:
                        cache[str(m.path)] = audio_mod.extract_audio(m.path)
                    except ProbeError:
                        cache[str(m.path)] = np.zeros(sr)
            waveforms = {
                key: clip_waveform(samples, sr) for key, samples in cache.items()
            }
            from ..autoedit import rms_envelope
            from ..audio import onset_envelope

            rms_envs = {
                key: rms_envelope(samples, sr) for key, samples in cache.items()
            }
            onset_envs = {
                key: onset_envelope(samples, sr) for key, samples in cache.items()
            }

            # storyline audio in timeline order, for dead air + highlights
            self.progress.emit("Looking for dead air and highlights...")
            total = float(timeline.duration)
            story = np.zeros(max(int(total * sr) + 1, sr))
            for clip in timeline.primary_clips:
                samples = cache.get(str(clip.media.path))
                if samples is None:
                    continue
                lo = int(float(clip.source_start) * sr)
                hi = lo + int(float(clip.duration) * sr)
                seg = samples[lo:hi]
                at = int(float(clip.timeline_start) * sr)
                story[at : at + len(seg)] = seg[: max(0, len(story) - at)]
            silences = detect_silences(story, sr)
            highlights = score_highlights(story, sr)

            beats: BeatGrid | None = None
            if self.job.music is not None:
                self.progress.emit("Finding the music's beat...")
                music_audio = cache.get(str(self.job.music.path))
                if music_audio is not None:
                    beats = detect_beats(music_audio, sr)

            self.finished_ok.emit(
                StudioAnalysis(
                    result=result,
                    waveforms=waveforms,
                    rms_envs=rms_envs,
                    onset_envs=onset_envs,
                    silences=silences,
                    highlights=highlights,
                    beats=beats,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    """Export an edited timeline (recreational mode) — same outputs as
    the sync flow, but from the editor's current state. `draft` renders
    a fast rough preview MP4 instead."""

    progress = Signal(str)
    finished_ok = Signal(object)  # (written: list[Path], video: Path | None)
    failed = Signal(str)

    def __init__(
        self,
        timeline: Timeline,
        formats: list[str],
        output_dir: Path,
        options: BuildOptions,
        draft: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.timeline = timeline
        self.formats = formats
        self.output_dir = output_dir
        self.options = options
        self.draft = draft

    def run(self) -> None:
        try:
            if self.draft:
                from ..renderer import render as render_video
                from ..titlecard import render_card_png

                out = self.output_dir / "editsync_draft_preview.mp4"
                card_png = None
                if self.timeline.title_card is not None:
                    card_png = (
                        self.output_dir / "editsync_draft_card.png"
                    ).resolve()
                    render_card_png(
                        self.timeline.title_card,
                        self.timeline.width,
                        self.timeline.height,
                        card_png,
                    )
                self.progress.emit("Rendering a draft preview... 0%")
                render_video(
                    self.timeline,
                    out,
                    overlay_style=self.options.overlay_style,
                    blur_amount=self.options.blur_amount,
                    card_png=card_png,
                    progress=lambda pct: self.progress.emit(
                        f"Rendering a draft preview... {pct}%"
                    ),
                    draft=True,
                )
                self.finished_ok.emit(([out], out))
                return

            written, video = write_outputs(
                self.timeline,
                self.formats,
                self.output_dir,
                self.options,
                self.progress.emit,
            )
            self.finished_ok.emit((written, video))
        except Exception as exc:
            self.failed.emit(str(exc))
