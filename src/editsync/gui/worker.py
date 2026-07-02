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
    formats: list[str]
    output_dir: Path
    write_report: bool = True


@dataclass
class SyncOutcome:
    result: BuildResult
    written: list[Path] = field(default_factory=list)


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
            )

            from .. import exporters

            written: list[Path] = []
            base = self.job.output_dir / self.job.options.project_name.replace(
                " ", "_"
            )
            self.progress.emit("Writing timeline files...")
            for fmt in self.job.formats:
                out = base.with_suffix(exporters.default_extension(fmt))
                exporters.export(fmt, result.timeline, out)
                written.append(out)
            if self.job.write_report:
                report_path = base.with_suffix(".sync-report.json")
                write_json_report(result, report_path)
                written.append(report_path)

            self.finished_ok.emit(SyncOutcome(result=result, written=written))
        except Exception as exc:  # surfaced in the UI, never a crash
            self.failed.emit(str(exc))
