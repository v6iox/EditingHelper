"""Finished-video renderer: filter-graph assembly and a real render."""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from editsync.renderer import build_command, db_to_gain, region_gain_expr
from editsync.timeline import (
    BlurRegion,
    DuckRegion,
    Timeline,
    TimelineClip,
    TitleCard,
)


@pytest.fixture
def timeline(make_media) -> Timeline:
    tl = Timeline(name="T", frame_rate=Fraction(30), width=640, height=360)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", width=640, height=360, duration=30.0),
            Fraction(0), Fraction(30), Fraction(0), 0, role="DJI",
        )
    )
    tl.clips.append(
        TimelineClip(
            make_media(name="meta_001.mp4", width=360, height=640, duration=5.0),
            Fraction(10), Fraction(5), Fraction(0), 1, role="Meta",
        )
    )
    tl.duck_regions = [DuckRegion(Fraction(10), Fraction(15), -60.0)]
    return tl


class TestExpressions:
    def test_db_to_gain(self):
        assert db_to_gain(0) == pytest.approx(1.0)
        assert db_to_gain(-20) == pytest.approx(0.1)

    def test_region_gain_expr_shape(self):
        expr = region_gain_expr(DuckRegion(Fraction(10), Fraction(15), -20.0))
        # unity before, level inside, ramps at the edges
        assert "lt(t,9.75" in expr
        assert "0.100000" in expr
        assert expr.count("if(") == 4


class TestBuildCommand:
    def test_graph_structure(self, timeline, make_media):
        cmd = build_command(timeline, Path("/tmp/out.mp4"))
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "overlay=(W-w)/2:(H-h)/2" in graph
        assert "between(t,10.0000,15.0000)" in graph
        assert "volume=" in graph  # primary ducked under the overlay
        assert "amix=inputs=2" in graph  # primary + overlay audio
        assert cmd[cmd.index("-t") + 1] == "30.0000"
        assert "-movflags" in cmd

    def test_blur_bg_adds_gblur(self, timeline):
        timeline.blur_regions = [BlurRegion(Fraction(10), Fraction(15), 60.0)]
        cmd = build_command(
            timeline, Path("/tmp/out.mp4"),
            overlay_style="blur-bg", blur_amount=60.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "gblur=sigma=15.00" in graph
        assert "enable='between(t,10.0000,15.0000)'" in graph

    def test_music_and_card_inputs(self, timeline, make_media, tmp_path):
        song = make_media(name="song.mp3", width=0, height=0, duration=12.0)
        song.frame_rate = Fraction(0)
        timeline.clips.append(
            TimelineClip(song, Fraction(0), Fraction(12), Fraction(0), -1,
                         role="Music", volume_db=-22.0)
        )
        timeline.title_card = TitleCard(title="X", hold=Fraction(2), fade=Fraction(1))
        card = tmp_path / "card.png"
        card.write_bytes(b"png")
        cmd = build_command(timeline, Path("/tmp/out.mp4"), card_png=card)
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "-stream_loop" in cmd  # music loops to cover the timeline
        assert cmd[cmd.index("-stream_loop") + 1] == "2"  # 30s / 12s -> 2 extra
        assert "fade=t=out:st=2.0000:d=1.0000:alpha=1" in graph
        assert "amix=inputs=3" in graph

    def test_input_deduplication(self, timeline):
        # a media file used twice must only be an ffmpeg input once
        clip = timeline.primary_clips[0]
        timeline.clips.append(
            TimelineClip(clip.media, Fraction(30), Fraction(10), Fraction(5),
                         0, role="DJI")
        )
        cmd = build_command(timeline, Path("/tmp/out.mp4"))
        assert cmd.count(str(clip.media.path)) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestRealRender:
    def test_render_end_to_end(self, tmp_path):
        from tests.test_integration import SR, scene_audio, write_video
        import numpy as np
        from editsync.cli import main

        master = scene_audio(20)
        dji = tmp_path / "DJI_0001.mp4"
        write_video(dji, master, 640, 360, "2026-06-20T10:00:00Z")
        seg = (0.6 * master[8 * SR : 13 * SR]).astype(np.float32)
        meta = tmp_path / "meta_001.mp4"
        write_video(meta, seg, 360, 640, "2026-06-20T10:00:08Z")

        out = tmp_path / "final"
        rc = main(
            [
                "sync", str(dji), str(meta),
                "-o", str(out),
                "--render",
                "--title", "Front Bumper Removal",
                "--title-description", "2024 Toyota GR86",
                "--title-hold", "2",
                "--title-fade", "1",
                "--overlay-style", "blur-bg",
            ]
        )
        assert rc == 0
        mp4 = tmp_path / "final.mp4"
        assert mp4.exists()

        probe = json.loads(
            subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(mp4)],
                capture_output=True, text=True, check=True,
            ).stdout
        )
        kinds = {s["codec_type"] for s in probe["streams"]}
        assert kinds == {"video", "audio"}
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (640, 360)
        assert abs(float(probe["format"]["duration"]) - 20.0) < 0.5

        def mean_luma(t: float) -> float:
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(mp4),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, check=True,
            ).stdout
            return sum(raw) / len(raw)

        # t=1: title card fully visible -> almost white frame
        assert mean_luma(1.0) > 180
        # t=6: card long gone -> the gray test footage, much darker
        assert mean_luma(6.0) < 170
