"""Background threads so the UI never freezes during probing or syncing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..builder import BuildOptions, BuildResult, build
from ..media import MediaFile, ProbeError, Role, classify, collect_video_files, probe
from ..report import write_json_report


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

            from .. import exporters

            written: list[Path] = []
            base = self.job.output_dir / self.job.options.project_name.replace(
                " ", "_"
            )
            self.progress.emit("Writing timeline files...")
            video: Path | None = None
            timeline_formats = [f for f in self.job.formats if f != "video"]
            for fmt in timeline_formats:
                out = base.with_suffix(exporters.default_extension(fmt))
                exporters.export(fmt, result.timeline, out)
                written.append(out)
            if "video" in self.job.formats:
                from ..renderer import render as render_video
                from ..titlecard import render_card_png

                card_png = None
                if result.timeline.title_card is not None:
                    card_png = base.resolve().with_suffix(".title_card.png")
                    render_card_png(
                        result.timeline.title_card,
                        result.timeline.width,
                        result.timeline.height,
                        card_png,
                    )
                video = base.with_suffix(".mp4")
                self.progress.emit("Rendering your video... 0%")
                render_video(
                    result.timeline,
                    video,
                    overlay_style=self.job.options.overlay_style,
                    blur_amount=self.job.options.blur_amount,
                    card_png=card_png,
                    progress=lambda pct: self.progress.emit(
                        f"Rendering your video... {pct}%"
                    ),
                )
                written.append(video)
            if (
                result.timeline.title_card is not None
                and "premiere" in self.job.formats
            ):
                result.warnings.append(
                    "The opening title card only exports to Final Cut Pro — "
                    "Premiere's format can't carry it, so add it there by hand."
                )
            if self.job.write_report:
                report_path = base.with_suffix(".sync-report.json")
                write_json_report(result, report_path)
                written.append(report_path)

            self.finished_ok.emit(
                SyncOutcome(result=result, written=written, video=video)
            )
        except Exception as exc:  # surfaced in the UI, never a crash
            self.failed.emit(str(exc))
