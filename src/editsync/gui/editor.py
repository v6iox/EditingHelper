"""Recreational-mode timeline editor.

A custom-painted multi-track timeline over the neutral `Timeline` model:
overlay lanes on top, the primary storyline under them, music passes at
the bottom. Clips show their audio waveform, drag to move (overlays),
drag edges to trim, Delete removes, double-click opens the portion
picker. Every primary-storyline edit is expressed as a ripple cut via
`autoedit.apply_cuts`, which is what keeps glasses clips in sync while
the video underneath is being tightened.

All edit operations exist as plain methods (`trim`, `move_overlay`,
`delete_clip`, ...) so the automation panel and the offscreen tests
drive exactly the code the mouse does.
"""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..audio import extract_audio
from ..autoedit import apply_cuts, rms_envelope
from ..media import MediaFile, require_tool
from ..timeline import Timeline, TimelineClip, assign_lanes
from . import style
from .widgets import AnimatedButton, mix

WAVE_FPS = 25  # waveform resolution painted inside clips

RULER_H = 26
LANE_H = 46
MUSIC_H = 28
LANE_GAP = 5
EDGE_PX = 7  # trim-handle hit width
SNAP_PX = 9  # snapping distance


def clip_waveform(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Normalized 0-1 loudness envelope at WAVE_FPS for clip painting."""
    env = rms_envelope(samples, sample_rate, WAVE_FPS)
    peak = float(env.max())
    return env / peak if peak > 0 else env


class TimelineView(QWidget):
    """The multi-track timeline canvas."""

    selection_changed = Signal(object)  # TimelineClip | None
    about_to_edit = Signal()  # fired before a mutation (undo snapshot point)
    edited = Signal(str)  # a short human description of the edit
    playhead_moved = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.timeline: Timeline | None = None
        self.waveforms: dict[str, np.ndarray] = {}
        self.beats: list[float] = []
        self.regions: dict[str, list[tuple[float, float]]] = {}
        self.selected: TimelineClip | None = None
        self.playhead = 0.0
        self._pps = 12.0  # pixels per second (zoom)
        self._scroll = 0.0  # seconds at the left edge
        self._hover: TimelineClip | None = None
        self._hover_edge: str | None = None
        # drag state: (mode, clip, grab time offset, original values)
        self._drag: tuple | None = None
        self._ghost_time: float | None = None
        self._move_ghost: float | None = None  # dragged overlay's preview start

    # ------------------------------------------------------------- model
    def set_timeline(self, timeline: Timeline | None) -> None:
        self.timeline = timeline
        self.selected = None
        self.selection_changed.emit(None)
        self._drag = None
        self._ghost_time = None
        self.updateGeometry()
        self.update()

    def set_analysis(
        self,
        waveforms: dict[str, np.ndarray] | None = None,
        beats: list[float] | None = None,
        regions: dict[str, list[tuple[float, float]]] | None = None,
    ) -> None:
        if waveforms is not None:
            self.waveforms = waveforms
        if beats is not None:
            self.beats = beats
        if regions is not None:
            self.regions = regions
        self.update()

    # -------------------------------------------------------- coordinates
    def x_at(self, t: float) -> float:
        return (t - self._scroll) * self._pps

    def t_at(self, x: float) -> float:
        return max(0.0, x / self._pps + self._scroll)

    def _lane_rows(self) -> list[tuple[int, float, float]]:
        """(lane, y, height) rows top-to-bottom for the current timeline."""
        if self.timeline is None:
            return []
        rows: list[tuple[int, float, float]] = []
        y = float(RULER_H + LANE_GAP)
        for lane in range(self.timeline.lane_count, 0, -1):
            rows.append((lane, y, LANE_H))
            y += LANE_H + LANE_GAP
        rows.append((0, y, LANE_H))
        y += LANE_H + LANE_GAP
        if self.timeline.music_clips:
            rows.append((-1, y, MUSIC_H))
            y += MUSIC_H + LANE_GAP
        return rows

    def _lane_geometry(self, lane: int) -> tuple[float, float]:
        for row_lane, y, h in self._lane_rows():
            if row_lane == (-1 if lane < 0 else lane):
                return y, h
        return float(RULER_H + LANE_GAP), LANE_H

    def content_height(self) -> int:
        rows = self._lane_rows()
        if not rows:
            return RULER_H + 3 * (LANE_H + LANE_GAP)
        _, y, h = rows[-1]
        return int(y + h + LANE_GAP)

    def minimumSizeHint(self) -> QSize:
        return QSize(400, self.content_height())

    def clip_rect(self, clip: TimelineClip) -> QRectF:
        y, h = self._lane_geometry(clip.lane)
        x = self.x_at(float(clip.timeline_start))
        w = float(clip.duration) * self._pps
        return QRectF(x, y, max(w, 2.0), h)

    # --------------------------------------------------------------- zoom
    def zoom(self) -> float:
        return self._pps

    def set_zoom(self, pps: float, anchor_x: float | None = None) -> None:
        pps = max(1.5, min(240.0, pps))
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        t_anchor = self.t_at(anchor_x)
        self._pps = pps
        self._scroll = max(0.0, t_anchor - anchor_x / pps)
        self.update()

    def fit(self) -> None:
        if self.timeline is None or self.timeline.duration <= 0:
            return
        self._scroll = 0.0
        self._pps = max(1.5, (self.width() - 24) / float(self.timeline.duration))
        self.update()

    # ------------------------------------------------------------ editing
    def _select(self, clip: TimelineClip | None) -> None:
        if clip is not self.selected:
            self.selected = clip
            self.selection_changed.emit(clip)
            self.update()

    def snap_time(self, t: float, ignore: TimelineClip | None = None) -> float:
        """Snap to beats, clip edges, and whole seconds within SNAP_PX."""
        if self.timeline is None:
            return t
        threshold = SNAP_PX / self._pps
        candidates: list[float] = [round(t)]
        candidates += [b for b in self.beats if abs(b - t) < threshold * 2]
        for c in self.timeline.clips:
            if c is ignore:
                continue
            candidates += [float(c.timeline_start), float(c.timeline_end)]
        best = min(candidates, key=lambda c: abs(c - t), default=t)
        return best if abs(best - t) <= threshold else t

    def move_overlay(self, clip: TimelineClip, new_start: float) -> None:
        """Reposition a glasses clip (the user's call — this changes where
        it sits relative to the main camera)."""
        if self.timeline is None or clip.lane <= 0:
            return
        fd = self.timeline.frame_duration
        from ..timeline import quantize

        self.about_to_edit.emit()
        clip.timeline_start = quantize(
            Fraction(max(0.0, new_start)).limit_denominator(48000), fd
        )
        assign_lanes(self.timeline.clips)
        self.edited.emit(f"Moved {clip.media.path.name}")
        self.update()

    def trim(self, clip: TimelineClip, edge: str, new_time: float) -> None:
        """Drag an edge to `new_time` (timeline seconds).

        Overlays trim in place and stay in sync (head-trims advance the
        source with the clip). Primary pieces trim as ripple cuts, so
        everything after slides left and stays in sync."""
        if self.timeline is None:
            return
        fd = self.timeline.frame_duration
        min_len = float(fd) * 2
        start, end = float(clip.timeline_start), float(clip.timeline_end)

        if clip.lane == 0:
            if edge == "in":
                cut = (clip.timeline_start,
                       Fraction(min(new_time, end - min_len)).limit_denominator(48000))
            else:
                cut = (Fraction(max(new_time, start + min_len)).limit_denominator(48000),
                       clip.timeline_end)
            if float(cut[1]) - float(cut[0]) < float(fd):
                return
            self.apply_timeline(
                apply_cuts(self.timeline, [cut]),
                f"Trimmed {clip.media.path.name}",
            )
            return

        from ..timeline import quantize

        self.about_to_edit.emit()
        if edge == "in":
            new_time = min(max(new_time, start - float(clip.source_start)), end - min_len)
            delta = Fraction(new_time - start).limit_denominator(48000)
            clip.timeline_start = quantize(clip.timeline_start + delta, fd)
            clip.source_start = max(Fraction(0), clip.source_start + delta)
            clip.duration = quantize(clip.duration - delta, fd)
        else:
            max_end = start + float(clip.media.duration - clip.source_start)
            new_time = max(min(new_time, max_end), start + min_len)
            clip.duration = quantize(
                Fraction(new_time - start).limit_denominator(48000), fd
            )
        assign_lanes(self.timeline.clips)
        self.edited.emit(f"Trimmed {clip.media.path.name}")
        self.update()

    def delete_clip(self, clip: TimelineClip) -> None:
        if self.timeline is None:
            return
        if clip.lane == 0:
            if len(self.timeline.primary_clips) <= 1:
                return  # never delete the last storyline piece
            self.apply_timeline(
                apply_cuts(
                    self.timeline, [(clip.timeline_start, clip.timeline_end)]
                ),
                f"Removed {clip.media.path.name}",
            )
            return
        self.about_to_edit.emit()
        self.timeline.clips.remove(clip)
        assign_lanes(self.timeline.clips)
        if clip is self.selected:
            self._select(None)
        self.edited.emit(f"Removed {clip.media.path.name}")
        self.update()

    def set_clip_range(
        self, clip: TimelineClip, source_start: float, duration: float
    ) -> None:
        """Apply the portion picker's choice to a clip."""
        if self.timeline is None:
            return
        old_ss, old_dur = float(clip.source_start), float(clip.duration)
        head = source_start - old_ss
        tail = (old_ss + old_dur) - (source_start + duration)
        if clip.lane == 0:
            start, end = float(clip.timeline_start), float(clip.timeline_end)
            cuts = []
            if head > 0.01:
                cuts.append((clip.timeline_start,
                             Fraction(start + head).limit_denominator(48000)))
            if tail > 0.01:
                cuts.append((Fraction(end - tail).limit_denominator(48000),
                             clip.timeline_end))
            if cuts:
                self.apply_timeline(
                    apply_cuts(self.timeline, cuts),
                    f"Picked a portion of {clip.media.path.name}",
                )
            return
        from ..timeline import quantize

        fd = self.timeline.frame_duration
        self.about_to_edit.emit()
        clip.source_start = Fraction(max(0.0, source_start)).limit_denominator(48000)
        clip.duration = quantize(
            Fraction(max(duration, float(fd))).limit_denominator(48000), fd
        )
        assign_lanes(self.timeline.clips)
        self.edited.emit(f"Picked a portion of {clip.media.path.name}")
        self.update()

    def append_clip(
        self, media: MediaFile, source_start: float, duration: float
    ) -> None:
        """Butt-join more material onto the end of the storyline."""
        if self.timeline is None:
            return
        from ..timeline import quantize

        fd = self.timeline.frame_duration
        end = max(
            (c.timeline_end for c in self.timeline.primary_clips),
            default=Fraction(0),
        )
        self.about_to_edit.emit()
        self.timeline.clips.append(
            TimelineClip(
                media=media,
                timeline_start=end,
                duration=quantize(
                    Fraction(duration).limit_denominator(48000), fd
                ),
                source_start=Fraction(source_start).limit_denominator(48000),
                lane=0,
                role="DJI",
            )
        )
        self.edited.emit(f"Added {media.path.name} to the end")
        self.update()

    def apply_timeline(self, timeline: Timeline, description: str) -> None:
        """Swap in a transformed timeline (ripple cuts, automations)."""
        self.about_to_edit.emit()
        self.timeline = timeline
        self._select(None)
        self.edited.emit(description)
        self.updateGeometry()
        self.update()

    # -------------------------------------------------------------- mouse
    def _hit(self, pos) -> tuple[TimelineClip | None, str | None]:
        """(clip, edge) under the cursor; edge is 'in' / 'out' / None."""
        if self.timeline is None:
            return None, None
        for clip in reversed(list(self.timeline.clips)):
            r = self.clip_rect(clip)
            if not r.adjusted(-EDGE_PX, 0, EDGE_PX, 0).contains(pos):
                continue
            if abs(pos.x() - r.left()) <= EDGE_PX:
                return clip, "in"
            if abs(pos.x() - r.right()) <= EDGE_PX:
                return clip, "out"
            if r.contains(pos):
                return clip, None
        return None, None

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() != Qt.LeftButton:
            return
        if pos.y() <= RULER_H:
            self.playhead = self.t_at(pos.x())
            self.playhead_moved.emit(self.playhead)
            self.update()
            return
        clip, edge = self._hit(pos)
        self._select(clip)
        if clip is None:
            return
        if edge is not None:
            self._drag = ("trim", clip, edge)
        elif clip.lane > 0:
            grab = self.t_at(pos.x()) - float(clip.timeline_start)
            self._drag = ("move", clip, grab)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is None:
            clip, edge = self._hit(pos)
            self._hover, self._hover_edge = clip, edge
            if edge is not None:
                self.setCursor(Qt.SizeHorCursor)
            elif clip is not None and clip.lane > 0:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            self.update()
            return
        mode = self._drag[0]
        if mode == "move":
            _, clip, grab = self._drag
            t = self.snap_time(self.t_at(pos.x()) - grab, ignore=clip)
            clip.timeline_start = Fraction(max(0.0, t)).limit_denominator(48000)
            self.update()
        elif mode == "trim":
            _, clip, edge = self._drag
            self._ghost_time = self.snap_time(self.t_at(pos.x()), ignore=clip)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        mode = self._drag[0]
        if mode == "move":
            _, clip, _ = self._drag
            self._drag = None
            self.move_overlay(clip, float(clip.timeline_start))
        elif mode == "trim":
            _, clip, edge = self._drag
            t = self._ghost_time
            self._drag = None
            self._ghost_time = None
            if t is not None:
                self.trim(clip, edge, t)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        clip, _ = self._hit(event.position())
        if clip is not None and clip.lane >= 0:
            self._select(clip)
            self._open_range_picker(clip)

    def _open_range_picker(self, clip: TimelineClip) -> None:
        dialog = RangeDialog(clip.media, clip, self)
        if dialog.exec():
            start, dur = dialog.selection
            self.set_clip_range(clip, start, dur)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected:
            self.delete_clip(self.selected)
        elif event.key() == Qt.Key_0:
            self.fit()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        dy = event.angleDelta().y()
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.0015 ** dy
            self.set_zoom(self._pps * factor, event.position().x())
        else:
            dx = event.angleDelta().x() or dy
            self._scroll = max(0.0, self._scroll - dx / self._pps * 0.5)
            self.update()

    # ------------------------------------------------------------ painting
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(style.BLACK))
        if self.timeline is None:
            painter.setPen(QColor(style.GRAY_DIM))
            painter.drawText(self.rect(), Qt.AlignCenter, "No timeline yet")
            painter.end()
            return

        self._paint_ruler(painter)
        self._paint_lanes(painter)
        self._paint_regions(painter)
        for clip in self.timeline.music_clips:
            self._paint_clip(painter, clip)
        for clip in self.timeline.primary_clips:
            self._paint_clip(painter, clip)
        for clip in self.timeline.overlay_clips:
            self._paint_clip(painter, clip)
        self._paint_playhead(painter)
        if self._ghost_time is not None:
            x = self.x_at(self._ghost_time)
            pen = QPen(QColor(style.WHITE), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(x), RULER_H, int(x), self.height())
        painter.end()

    def _nice_step(self) -> float:
        for step in (0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if step * self._pps >= 64:
                return step
        return 600

    def _paint_ruler(self, painter: QPainter) -> None:
        painter.setPen(QColor(style.LINE_SOFT))
        painter.drawLine(0, RULER_H, self.width(), RULER_H)
        step = self._nice_step()
        t = int(self._scroll / step) * step
        font = painter.font()
        font.setPointSizeF(8.0)
        painter.setFont(font)
        while True:
            x = self.x_at(t)
            if x > self.width():
                break
            if x >= 0:
                painter.setPen(QColor(style.LINE))
                painter.drawLine(int(x), RULER_H - 7, int(x), RULER_H)
                painter.setPen(QColor(style.GRAY_DIM))
                mins, secs = divmod(int(t), 60)
                label = f"{mins}:{secs:02d}" if step >= 1 else f"{t:.2f}"
                painter.drawText(int(x) + 4, RULER_H - 9, label)
            t += step
        # beat ticks
        painter.setPen(QColor(style.GRAY_DIM))
        for b in self.beats:
            x = self.x_at(b)
            if 0 <= x <= self.width():
                painter.drawLine(int(x), RULER_H - 3, int(x), RULER_H)

    def _paint_lanes(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        for _lane, y, h in self._lane_rows():
            painter.setBrush(QColor(style.SURFACE))
            painter.drawRoundedRect(
                QRectF(0, y, self.width(), h), 5, 5
            )

    def _paint_regions(self, painter: QPainter) -> None:
        y, h = self._lane_geometry(0)
        for kind, intervals in self.regions.items():
            for s, e in intervals:
                x0, x1 = self.x_at(s), self.x_at(e)
                if x1 < 0 or x0 > self.width():
                    continue
                r = QRectF(x0, y, x1 - x0, h)
                if kind == "silence":
                    color = QColor(style.BLACK)
                    color.setAlphaF(0.55)
                    painter.fillRect(r, color)
                    painter.setPen(QColor(style.GRAY_DIM))
                    if r.width() > 44:
                        painter.drawText(r, Qt.AlignCenter, "quiet")
                elif kind == "highlight":
                    color = QColor(style.WHITE)
                    color.setAlphaF(0.10)
                    painter.fillRect(r, color)

    def _paint_clip(self, painter: QPainter, clip: TimelineClip) -> None:
        r = self.clip_rect(clip)
        if r.right() < 0 or r.left() > self.width():
            return
        selected = clip is self.selected
        hovered = clip is self._hover
        if clip.lane < 0:
            fill = QColor(style.SURFACE_2)
            border = QColor(style.LINE_SOFT)
        elif clip.lane == 0:
            fill = mix(style.SURFACE_2, style.WHITE, 0.03 if hovered else 0.0)
            border = QColor(style.LINE)
        else:
            fill = mix(style.SURFACE_2, style.WHITE, 0.10 if hovered else 0.06)
            border = QColor(style.GRAY)
        if selected:
            border = QColor(style.WHITE)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.6 if selected else 1.0))
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)

        self._paint_wave(painter, clip, r, selected)

        if r.width() > 56:
            painter.setPen(
                QColor(style.WHITE) if selected else QColor(style.GRAY)
            )
            font = painter.font()
            font.setPointSizeF(8.0)
            painter.setFont(font)
            name = clip.media.path.name if clip.lane >= 0 else "♪ music"
            text = painter.fontMetrics().elidedText(
                name, Qt.ElideMiddle, int(r.width()) - 12
            )
            painter.drawText(
                QRectF(r.left() + 6, r.top() + 2, r.width() - 12, 14),
                Qt.AlignLeft | Qt.AlignVCenter,
                text,
            )

    def _paint_wave(
        self, painter: QPainter, clip: TimelineClip, r: QRectF, selected: bool
    ) -> None:
        env = self.waveforms.get(str(clip.media.path))
        if env is None or len(env) == 0 or r.width() < 8:
            return
        lo = int(float(clip.source_start) * WAVE_FPS)
        hi = int(float(clip.source_start + clip.duration) * WAVE_FPS)
        window = env[max(0, lo):max(0, hi)]
        if len(window) == 0:
            return
        color = QColor(style.WHITE if selected else style.GRAY_DIM)
        color.setAlphaF(0.55 if selected else 0.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        base = r.top() + 16
        avail = r.height() - 20
        step_px = 2.0
        n_bars = int(r.width() // step_px)
        if n_bars < 1:
            return
        for i in range(n_bars):
            j = int(i * len(window) / n_bars)
            amp = float(window[j]) * avail
            x = r.left() + i * step_px
            if x + 1 < r.left() + 2 or x > r.right() - 2:
                continue
            painter.drawRect(
                QRectF(x, base + (avail - amp) / 2, 1.4, max(amp, 1.0))
            )

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self.x_at(self.playhead)
        if x < 0 or x > self.width():
            return
        painter.setPen(QPen(QColor(style.WHITE), 1))
        painter.drawLine(int(x), 6, int(x), self.height())
        painter.setBrush(QColor(style.WHITE))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(
            [QPointF(x - 4, 6), QPointF(x + 4, 6), QPointF(x, 14)]
        )


class WaveformStrip(QWidget):
    """A source clip's full waveform with draggable in/out handles —
    'use this part' selection for the portion picker."""

    range_changed = Signal(float, float)  # start, end (seconds)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(72)
        self.setMouseTracking(True)
        self._env: np.ndarray | None = None
        self._duration = 0.0
        self._sel = (0.0, 0.0)
        self._drag: str | None = None  # "in" | "out" | "body"
        self._grab = 0.0

    def set_data(self, env: np.ndarray, duration: float) -> None:
        peak = float(env.max()) if len(env) else 0.0
        self._env = env / peak if peak > 0 else env
        self._duration = max(duration, 0.001)
        self.update()

    def set_selection(self, start: float, end: float) -> None:
        start = max(0.0, min(start, self._duration))
        end = max(start, min(end, self._duration))
        self._sel = (start, end)
        self.range_changed.emit(start, end)
        self.update()

    def selection(self) -> tuple[float, float]:
        return self._sel

    def _x(self, t: float) -> float:
        return t / self._duration * self.width()

    def _t(self, x: float) -> float:
        return max(0.0, min(self._duration, x / max(self.width(), 1) * self._duration))

    def mousePressEvent(self, event) -> None:
        x = event.position().x()
        x_in, x_out = self._x(self._sel[0]), self._x(self._sel[1])
        if abs(x - x_in) <= 8:
            self._drag = "in"
        elif abs(x - x_out) <= 8:
            self._drag = "out"
        elif x_in < x < x_out:
            self._drag = "body"
            self._grab = self._t(x) - self._sel[0]
        else:  # start a fresh selection from here
            t = self._t(x)
            self._drag = "out"
            self.set_selection(t, t)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._drag is None:
            x_in, x_out = self._x(self._sel[0]), self._x(self._sel[1])
            near = abs(x - x_in) <= 8 or abs(x - x_out) <= 8
            self.setCursor(Qt.SizeHorCursor if near else Qt.ArrowCursor)
            return
        t = self._t(x)
        s, e = self._sel
        if self._drag == "in":
            self.set_selection(min(t, e), e)
        elif self._drag == "out":
            self.set_selection(s, max(t, s))
        else:
            length = e - s
            new_s = max(0.0, min(t - self._grab, self._duration - length))
            self.set_selection(new_s, new_s + length)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(style.SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)

        if self._env is not None and len(self._env) and self._duration > 0:
            n = len(self._env)
            x_in, x_out = self._x(self._sel[0]), self._x(self._sel[1])
            for x in range(2, self.width() - 2, 2):
                j = int(x / self.width() * n)
                amp = float(self._env[j]) * (self.height() - 16)
                inside = x_in <= x <= x_out
                color = QColor(style.WHITE if inside else style.GRAY_DIM)
                color.setAlphaF(0.75 if inside else 0.45)
                painter.setBrush(color)
                painter.drawRect(
                    QRectF(x, (self.height() - amp) / 2, 1.4, max(amp, 1.0))
                )
            # selection borders
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(style.WHITE), 2))
            for x in (x_in, x_out):
                painter.drawLine(int(x), 4, int(x), self.height() - 4)
        painter.end()


class _EnvelopeWorker(QThread):
    """Decode a clip's audio and hand back its display envelope."""

    ready = Signal(object, float)  # env array, duration seconds

    def __init__(self, media: MediaFile, parent=None):
        super().__init__(parent)
        self.media = media

    def run(self) -> None:
        try:
            samples = extract_audio(self.media.path)
            env = rms_envelope(samples, 8000, WAVE_FPS)
            self.ready.emit(env, float(self.media.duration))
        except Exception:
            self.ready.emit(np.zeros(1), float(self.media.duration))


class _ThumbnailWorker(QThread):
    """Pull a handful of preview frames along the clip with ffmpeg."""

    thumb = Signal(int, object)  # index, QImage

    def __init__(self, media: MediaFile, count: int, parent=None):
        super().__init__(parent)
        self.media = media
        self.count = count

    def run(self) -> None:
        try:
            ffmpeg = require_tool("ffmpeg")
        except Exception:
            return
        duration = max(float(self.media.duration), 0.1)
        for i in range(self.count):
            t = duration * (i + 0.5) / self.count
            try:
                proc = subprocess.run(
                    [
                        ffmpeg, "-v", "error", "-ss", f"{t:.3f}",
                        "-i", str(self.media.path),
                        "-frames:v", "1", "-vf", "scale=160:-2",
                        "-f", "image2pipe", "-vcodec", "png", "-",
                    ],
                    capture_output=True, timeout=20,
                )
                image = QImage.fromData(proc.stdout, "PNG")
                if not image.isNull():
                    self.thumb.emit(i, image)
            except Exception:
                continue


class RangeDialog(QDialog):
    """'Use this part' — pick a portion of a source clip intuitively:
    thumbnails to see, waveform to hear, handles to choose."""

    THUMBS = 8

    def __init__(
        self,
        media: MediaFile,
        clip: TimelineClip | None = None,
        parent=None,
        analyze: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Use which part of {media.path.name}?")
        self.setObjectName("Root")
        self.setMinimumWidth(560)
        self.media = media
        self.selection: tuple[float, float] = (0.0, float(media.duration))
        self._workers: list[QThread] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        hint = QLabel(
            "Drag across the waveform to choose, drag the edges to adjust, "
            "or drag the middle to slide the window."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.thumb_row = QHBoxLayout()
        self.thumb_row.setSpacing(2)
        self._thumb_labels: list[QLabel] = []
        for _ in range(self.THUMBS):
            thumb = QLabel()
            thumb.setFixedSize(64, 40)
            thumb.setStyleSheet(
                f"background: {style.SURFACE_2}; border-radius: 3px;"
            )
            thumb.setScaledContents(True)
            self._thumb_labels.append(thumb)
            self.thumb_row.addWidget(thumb)
        self.thumb_row.addStretch(1)
        layout.addLayout(self.thumb_row)

        self.strip = WaveformStrip()
        self.strip.range_changed.connect(self._on_range)
        layout.addWidget(self.strip)

        row = QHBoxLayout()
        row.addWidget(QLabel("From"))
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(2)
        self.start_spin.setMaximum(float(media.duration))
        self.start_spin.setSuffix(" s")
        row.addWidget(self.start_spin)
        row.addWidget(QLabel("for"))
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setDecimals(2)
        self.length_spin.setRange(0.1, float(media.duration))
        self.length_spin.setSuffix(" s")
        row.addWidget(self.length_spin)
        row.addStretch(1)
        self.use_btn = AnimatedButton("Use this part", kind="primary")
        self.use_btn.clicked.connect(self.accept)
        cancel = AnimatedButton("Cancel", kind="ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(self.use_btn)
        layout.addLayout(row)

        self.start_spin.valueChanged.connect(self._on_spin)
        self.length_spin.valueChanged.connect(self._on_spin)

        initial_start = float(clip.source_start) if clip else 0.0
        initial_len = float(clip.duration) if clip else float(media.duration)
        self.strip.set_data(np.zeros(1), float(media.duration))
        self.strip.set_selection(initial_start, initial_start + initial_len)

        if analyze:
            env_worker = _EnvelopeWorker(media, self)
            env_worker.ready.connect(
                lambda env, dur: self.strip.set_data(env, dur)
            )
            env_worker.start()
            thumb_worker = _ThumbnailWorker(media, self.THUMBS, self)
            thumb_worker.thumb.connect(self._on_thumb)
            thumb_worker.start()
            self._workers = [env_worker, thumb_worker]

    def _on_thumb(self, index: int, image: QImage) -> None:
        from PySide6.QtGui import QPixmap

        if 0 <= index < len(self._thumb_labels):
            self._thumb_labels[index].setPixmap(QPixmap.fromImage(image))

    def _on_range(self, start: float, end: float) -> None:
        self.selection = (start, max(end - start, 0.1))
        for spin, value in (
            (self.start_spin, start),
            (self.length_spin, max(end - start, 0.1)),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_spin(self) -> None:
        start = self.start_spin.value()
        self.strip.set_selection(start, start + self.length_spin.value())

    def done(self, result: int) -> None:
        for worker in self._workers:
            worker.wait(5000)
        super().done(result)
