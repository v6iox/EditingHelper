"""The studio's live viewer — a program monitor for the timeline.

Shows the composed frame at the playhead: the main camera (blurred
where the blur-background style is active), every glasses clip framed
exactly as the renderer will frame it, drawn live. Two direct
manipulations:

- the **scrubber puck** under the picture (and the playhead in the
  timeline ruler) drags through time, updating the picture as it goes;
- a **glasses clip can be grabbed and dragged inside the picture** to
  move what's visible — the new position is stored on the clip
  (project-pixel offset from center, +y up, Final Cut's convention) and
  honored by the finished-video renderer and every exporter.

Frames are pulled by a persistent ffmpeg fetcher thread (latest-wins,
small LRU cache), so scrubbing never blocks the UI. Playback steps the
playhead against the wall clock and draws frames as fast as they
arrive — a silent preview; the draft render has sound.
"""

from __future__ import annotations

import subprocess
import time
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import (
    QMutex,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QWaitCondition,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from ..media import require_tool
from ..timeline import Timeline, TimelineClip
from . import style
from .widgets import AnimatedButton

FETCH_WIDTH = 640  # decoded frame width; scaled up in the viewer
CACHE_FRAMES = 96
TIME_GRAIN = 8  # frames cached at 1/8-second granularity


def overlay_rect_px(
    timeline: Timeline, clip: TimelineClip, overlay_style: str
) -> tuple[float, float, float, float]:
    """(x, y, w, h) of an overlay inside the project frame, in project
    pixels — the same math the finished-video renderer uses, plus the
    clip's own dragged position offset when one is set."""
    W, H = timeline.width, timeline.height
    dw = max(clip.media.display_width, 1)
    dh = max(clip.media.display_height, 1)
    if overlay_style == "fill":
        return 0.0, 0.0, float(W), float(H)
    if overlay_style in ("pip-left", "pip-right"):
        box_w, box_h = round(W * 0.4), round(H * 0.62)
        scale = min(box_w / dw, box_h / dh)
        w, h = dw * scale, dh * scale
        x = W * 0.04 if overlay_style == "pip-left" else W - w - W * 0.04
        y = (H - h) / 2
    else:  # center / blur-bg: fit inside the frame
        scale = min(W / dw, H / dh)
        w, h = dw * scale, dh * scale
        x, y = (W - w) / 2, (H - h) / 2
    if clip.transform_position is not None:
        px, py = clip.transform_position
        x = (W - w) / 2 + px
        y = (H - h) / 2 - py  # +y is up in the model (FCP convention)
    return x, y, w, h


def clips_at(timeline: Timeline, t: float) -> list[TimelineClip]:
    """Visible clips at time t, bottom to top (primary, then overlays)."""
    out = [
        c
        for c in timeline.primary_clips
        if float(c.timeline_start) <= t < float(c.timeline_end)
    ]
    out += [
        c
        for c in timeline.overlay_clips
        if float(c.timeline_start) <= t < float(c.timeline_end)
    ]
    return out


def blur_active(timeline: Timeline, t: float) -> bool:
    return any(
        float(r.start) <= t < float(r.end) for r in timeline.blur_regions
    )


def cheap_blur(image: QImage, amount: float) -> QImage:
    """Approximate a Gaussian blur by scaling way down and back up —
    plenty for a preview monitor."""
    if image.isNull():
        return image
    factor = max(4, int(amount / 6))
    small = image.scaled(
        max(2, image.width() // factor),
        max(2, image.height() // factor),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )
    return small.scaled(
        image.width(), image.height(), Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )


class FrameFetcher(QThread):
    """Latest-wins frame fetcher: the viewer asks for a time, the thread
    decodes one frame per visible clip with ffmpeg and hands them back.
    Requests made while busy replace the pending one (scrubbing never
    queues up a backlog)."""

    frames_ready = Signal(float, object)  # t, {str(path): QImage}

    _live: list["FrameFetcher"] = []  # every running fetcher, for stop_all

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._pending: tuple[float, list[tuple[str, float]]] | None = None
        self._quit = False
        self._cache: OrderedDict[tuple[str, int], QImage] = OrderedDict()

    def request(self, t: float, wants: list[tuple[str, float]]) -> None:
        """wants: [(media path, source time)] for every visible clip.
        The thread starts lazily on the first request."""
        self._mutex.lock()
        self._pending = (t, wants)
        self._wake.wakeAll()
        self._mutex.unlock()
        if not self.isRunning() and not self._quit:
            FrameFetcher._live.append(self)
            self.start()

    def stop(self) -> None:
        self._mutex.lock()
        self._quit = True
        self._wake.wakeAll()
        self._mutex.unlock()
        self.wait(5000)
        if self in FrameFetcher._live:
            FrameFetcher._live.remove(self)

    @classmethod
    def stop_all(cls) -> None:
        """Stop every running fetcher — app shutdown and test teardown."""
        for fetcher in list(cls._live):
            fetcher.stop()

    def _cached(self, path: str, src_t: float) -> QImage | None:
        key = (path, int(src_t * TIME_GRAIN))
        image = self._cache.get(key)
        if image is not None:
            self._cache.move_to_end(key)
        return image

    def _store(self, path: str, src_t: float, image: QImage) -> None:
        key = (path, int(src_t * TIME_GRAIN))
        self._cache[key] = image
        while len(self._cache) > CACHE_FRAMES:
            self._cache.popitem(last=False)

    def _decode(self, path: str, src_t: float) -> QImage:
        try:
            ffmpeg = require_tool("ffmpeg")
            proc = subprocess.run(
                [
                    ffmpeg, "-v", "error",
                    "-ss", f"{max(0.0, src_t):.3f}", "-i", path,
                    "-frames:v", "1", "-vf", f"scale={FETCH_WIDTH}:-2",
                    "-f", "image2pipe", "-vcodec", "bmp", "-",
                ],
                capture_output=True, timeout=15,
            )
            return QImage.fromData(proc.stdout, "BMP")
        except Exception:
            return QImage()

    def run(self) -> None:
        while True:
            self._mutex.lock()
            while self._pending is None and not self._quit:
                self._wake.wait(self._mutex)
            if self._quit:
                self._mutex.unlock()
                return
            t, wants = self._pending
            self._pending = None
            self._mutex.unlock()

            frames: dict[str, QImage] = {}
            for path, src_t in wants:
                image = self._cached(path, src_t)
                if image is None:
                    image = self._decode(path, src_t)
                    if not image.isNull():
                        self._store(path, src_t, image)
                if not image.isNull():
                    frames[path] = image
            self.frames_ready.emit(t, frames)


class LiveViewer(QWidget):
    """The picture. Paints the composed frame; glasses clips can be
    grabbed and dragged to move what's visible."""

    drag_started = Signal(object)  # TimelineClip
    drag_finished = Signal(object)  # TimelineClip

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.timeline: Timeline | None = None
        self.overlay_style = "blur-bg"
        self.blur_amount = 50.0
        self.time = 0.0
        self.frames: dict[str, QImage] = {}
        self._blur_cache: tuple[int, float, QImage] | None = None
        self._drag: tuple[TimelineClip, float, float, tuple[float, float]] | None = None
        self._hover_clip: TimelineClip | None = None

    # ------------------------------------------------------------ frames
    def show_frames(self, t: float, frames: dict[str, QImage]) -> None:
        self.time = t
        self.frames = frames
        self._blur_cache = None
        self.update()

    # ---------------------------------------------------------- geometry
    def frame_rect(self) -> QRectF:
        """Where the project frame sits inside the widget (letterboxed)."""
        if self.timeline is None:
            return QRectF(self.rect())
        W, H = self.timeline.width, self.timeline.height
        if W <= 0 or H <= 0:
            return QRectF(self.rect())
        s = min(self.width() / W, self.height() / H)
        w, h = W * s, H * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _scale(self) -> float:
        if self.timeline is None or self.timeline.width <= 0:
            return 1.0
        return self.frame_rect().width() / self.timeline.width

    def overlay_widget_rect(self, clip: TimelineClip) -> QRectF:
        x, y, w, h = overlay_rect_px(self.timeline, clip, self.overlay_style)
        s = self._scale()
        frame = self.frame_rect()
        return QRectF(frame.x() + x * s, frame.y() + y * s, w * s, h * s)

    def _visible_overlays(self) -> list[TimelineClip]:
        if self.timeline is None:
            return []
        return [c for c in clips_at(self.timeline, self.time) if c.lane > 0]

    # ------------------------------------------------------------- mouse
    def _overlay_at(self, pos) -> TimelineClip | None:
        for clip in reversed(self._visible_overlays()):  # topmost first
            if self.overlay_widget_rect(clip).contains(pos):
                return clip
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self.timeline is None:
            return
        clip = self._overlay_at(event.position())
        if clip is None or self.overlay_style == "fill":
            return
        x, y, w, h = overlay_rect_px(self.timeline, clip, self.overlay_style)
        W, H = self.timeline.width, self.timeline.height
        # current offset from center in project px (+y up)
        offset = (
            clip.transform_position
            if clip.transform_position is not None
            else (x - (W - w) / 2, (H - h) / 2 - y)
        )
        self._drag = (clip, event.position().x(), event.position().y(), offset)
        self.setCursor(Qt.ClosedHandCursor)
        self.drag_started.emit(clip)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            hover = self._overlay_at(event.position())
            self._hover_clip = hover
            self.setCursor(
                Qt.OpenHandCursor
                if hover is not None and self.overlay_style != "fill"
                else Qt.ArrowCursor
            )
            self.update()
            return
        clip, x0, y0, (ox, oy) = self._drag
        s = self._scale() or 1.0
        dx = (event.position().x() - x0) / s
        dy = (event.position().y() - y0) / s
        clip.transform_position = (round(ox + dx, 1), round(oy - dy, 1))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        clip = self._drag[0]
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        self.drag_finished.emit(clip)

    # ------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(style.BLACK))
        frame = self.frame_rect()
        painter.setPen(QPen(QColor(style.LINE_SOFT), 1))
        painter.setBrush(QColor("#000000"))
        painter.drawRect(frame)

        if self.timeline is None:
            painter.setPen(QColor(style.GRAY_DIM))
            painter.drawText(self.rect(), Qt.AlignCenter, "No preview yet")
            painter.end()
            return

        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        visible = clips_at(self.timeline, self.time)
        primary = next((c for c in visible if c.lane == 0), None)
        if primary is not None:
            image = self.frames.get(str(primary.media.path))
            if image is not None and not image.isNull():
                if self.overlay_style == "blur-bg" and blur_active(
                    self.timeline, self.time
                ):
                    key = (image.cacheKey(), self.blur_amount)
                    if self._blur_cache is None or self._blur_cache[:2] != key:
                        self._blur_cache = (
                            *key, cheap_blur(image, self.blur_amount)
                        )
                    image = self._blur_cache[2]
                # the renderer fits the primary inside the frame (pad)
                fitted = QRectF(frame)
                iw, ih = image.width(), image.height()
                s = min(frame.width() / iw, frame.height() / ih)
                w, h = iw * s, ih * s
                fitted = QRectF(
                    frame.x() + (frame.width() - w) / 2,
                    frame.y() + (frame.height() - h) / 2,
                    w, h,
                )
                painter.drawImage(fitted, image)

        for clip in (c for c in visible if c.lane > 0):
            image = self.frames.get(str(clip.media.path))
            rect = self.overlay_widget_rect(clip)
            if image is not None and not image.isNull():
                if self.overlay_style == "fill":
                    # scale to cover, center-crop into the frame
                    iw, ih = image.width(), image.height()
                    s = max(rect.width() / iw, rect.height() / ih)
                    src_w, src_h = rect.width() / s, rect.height() / s
                    src = QRectF(
                        (iw - src_w) / 2, (ih - src_h) / 2, src_w, src_h
                    )
                    painter.drawImage(rect, image, src)
                else:
                    painter.drawImage(rect, image)
            if clip is self._hover_clip or self._drag is not None and clip is self._drag[0]:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(style.WHITE), 1.4))
                painter.drawRect(rect)
        painter.end()


class LiveViewerPanel(QWidget):
    """Viewer + transport: play/pause, the time readout, and the
    scrubber puck that moves through what's visible."""

    time_changed = Signal(float)  # user moved the playhead here
    clip_repositioned = Signal(object, str)  # clip, description

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.fetcher = FrameFetcher(self)
        self.fetcher.frames_ready.connect(self._on_frames)
        self._playing = False
        self._play_clock = 0.0
        self._play_started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(66)
        self._timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.viewer = LiveViewer()
        self.viewer.drag_started.connect(self._on_drag_started)
        self.viewer.drag_finished.connect(self._on_drag_finished)
        layout.addWidget(self.viewer, stretch=1)

        transport = QHBoxLayout()
        transport.setSpacing(10)
        self.play_btn = AnimatedButton("▶", kind="ghost")
        self.play_btn.setFixedWidth(34)
        self.play_btn.setToolTip("Play / pause (silent preview)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 0)
        self.scrubber.sliderMoved.connect(self._on_scrub)
        self.scrubber.sliderPressed.connect(self.pause)
        self.time_label = QLabel("0:00.0")
        self.time_label.setObjectName("Hint")
        transport.addWidget(self.play_btn)
        transport.addWidget(self.scrubber, stretch=1)
        transport.addWidget(self.time_label)
        layout.addLayout(transport)

    # ------------------------------------------------------------- wiring
    def set_timeline(
        self, timeline: Timeline | None, overlay_style: str, blur_amount: float
    ) -> None:
        self.timeline = timeline
        self.viewer.timeline = timeline
        self.viewer.overlay_style = overlay_style
        self.viewer.blur_amount = blur_amount
        total = float(timeline.duration) if timeline is not None else 0.0
        self.scrubber.setRange(0, int(total * 1000))
        self.seek(min(self.viewer.time, max(0.0, total - 0.001)), emit=False)

    def seek(self, t: float, emit: bool = True) -> None:
        """Move the playhead to t and fetch that frame."""
        if self.timeline is None:
            return
        total = float(self.timeline.duration)
        t = max(0.0, min(t, max(0.0, total - 0.001)))
        self.viewer.time = t
        self.scrubber.blockSignals(True)
        self.scrubber.setValue(int(t * 1000))
        self.scrubber.blockSignals(False)
        mins, secs = divmod(t, 60.0)
        self.time_label.setText(f"{int(mins)}:{secs:04.1f}")
        wants = [
            (
                str(c.media.path),
                float(c.source_start) + t - float(c.timeline_start),
            )
            for c in clips_at(self.timeline, t)
        ]
        self.fetcher.request(t, wants)
        if emit:
            self.time_changed.emit(t)
        self.viewer.update()

    def refresh(self) -> None:
        """Timeline structure changed — re-resolve the current frame."""
        if self.timeline is not None:
            self.set_timeline(
                self.timeline, self.viewer.overlay_style, self.viewer.blur_amount
            )

    def _on_frames(self, t: float, frames: dict) -> None:
        self.viewer.show_frames(t, frames)

    def _on_scrub(self, value: int) -> None:
        self.seek(value / 1000.0)

    # ----------------------------------------------------------- playback
    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if self.timeline is None or self._playing:
            return
        total = float(self.timeline.duration)
        if total <= 0:
            return
        if self.viewer.time >= total - 0.05:  # replay from the top
            self.seek(0.0)
        self._playing = True
        self._play_clock = self.viewer.time
        self._play_started = time.monotonic()
        self.play_btn.setText("⏸")
        self._timer.start()

    def pause(self) -> None:
        self._playing = False
        self.play_btn.setText("▶")
        self._timer.stop()

    def _tick(self) -> None:
        if not self._playing or self.timeline is None:
            return
        t = self._play_clock + (time.monotonic() - self._play_started)
        if t >= float(self.timeline.duration):
            self.pause()
            t = float(self.timeline.duration) - 0.001
        self.seek(t)

    # ------------------------------------------------- reposition plumbing
    def _on_drag_started(self, clip) -> None:
        self.clip_repositioned.emit(clip, "__begin__")

    def _on_drag_finished(self, clip) -> None:
        self.clip_repositioned.emit(
            clip, f"Moved {clip.media.path.name} in the frame"
        )

    def shutdown(self) -> None:
        self.pause()
        self.fetcher.stop()
