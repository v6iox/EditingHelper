"""Background-music feature: tiling, classification, and export shapes."""

from __future__ import annotations

from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from editsync.builder import BuildOptions, add_music
from editsync.exporters import fcpxml, otio
from editsync.media import Role, classify
from editsync.timeline import DuckRegion, Timeline, TimelineClip


@pytest.fixture
def music_media(make_media):
    m = make_media(name="song.mp3", width=0, height=0, duration=30.0)
    m.frame_rate = Fraction(0)
    m.role = Role.MUSIC
    return m


@pytest.fixture
def timeline_with_primary(make_media) -> Timeline:
    tl = Timeline(name="T", frame_rate=Fraction(30), width=3840, height=2160)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", duration=100.0),
            Fraction(0), Fraction(100), Fraction(0), 0, role="DJI",
        )
    )
    return tl


class TestClassification:
    def test_audio_only_is_music(self, music_media):
        music_media.role = Role.UNKNOWN
        classify([music_media])
        assert music_media.role == Role.MUSIC
        assert "audio" in music_media.role_reason


class TestAddMusic:
    def test_loops_to_cover_timeline(self, timeline_with_primary, music_media):
        add_music(timeline_with_primary, music_media, BuildOptions(music_db=-25.0))
        music = timeline_with_primary.music_clips
        # 100s of video with a 30s song -> 30 + 30 + 30 + 10
        assert len(music) == 4
        assert [float(c.timeline_start) for c in music] == [0.0, 30.0, 60.0, 90.0]
        assert float(music[-1].duration) == pytest.approx(10.0)
        assert all(c.lane == -1 for c in music)
        assert all(c.volume_db == -25.0 for c in music)
        # the loop never extends past the video
        assert music[-1].timeline_end <= timeline_with_primary.duration

    def test_music_longer_than_video_is_trimmed(self, timeline_with_primary, make_media):
        long_song = make_media(name="long.mp3", width=0, height=0, duration=500.0)
        long_song.frame_rate = Fraction(0)
        add_music(timeline_with_primary, long_song, BuildOptions())
        music = timeline_with_primary.music_clips
        assert len(music) == 1
        assert float(music[0].duration) == pytest.approx(100.0)

    def test_zero_duration_music_ignored(self, timeline_with_primary, make_media):
        broken = make_media(name="broken.mp3", width=0, height=0, duration=0.0)
        add_music(timeline_with_primary, broken, BuildOptions())
        assert timeline_with_primary.music_clips == []

    def test_lane_assignment_leaves_music_alone(self, timeline_with_primary, music_media):
        from editsync.timeline import assign_lanes

        add_music(timeline_with_primary, music_media, BuildOptions())
        overlay = TimelineClip(
            music_media, Fraction(5), Fraction(5), Fraction(0), 1, role="Meta"
        )
        timeline_with_primary.clips.append(overlay)
        assign_lanes(timeline_with_primary.clips)
        assert all(c.lane == -1 for c in timeline_with_primary.music_clips)
        assert overlay.lane == 1


class TestFcpxmlMusic:
    def _export(self, tl, tmp_path):
        out = tmp_path / "m.fcpxml"
        fcpxml.export(tl, out)
        return ET.fromstring(out.read_text().split("<!DOCTYPE fcpxml>")[1])

    def test_music_as_negative_lane_with_static_volume(
        self, timeline_with_primary, music_media, tmp_path
    ):
        add_music(timeline_with_primary, music_media, BuildOptions(music_db=-22.0))
        root = self._export(timeline_with_primary, tmp_path)
        primary = root.find("./library/event/project/sequence/spine/asset-clip")
        music_els = [
            c for c in primary.findall("asset-clip") if int(c.get("lane")) < 0
        ]
        assert len(music_els) == 4
        assert all(c.get("audioRole") == "music" for c in music_els)
        assert all(c.get("format") is None for c in music_els)
        vol = music_els[0].find("adjust-volume")
        assert vol is not None and vol.get("amount") == "-22dB"
        # audio-only asset has no video/format attributes
        asset = next(
            a for a in root.findall("./resources/asset")
            if a.get("name") == "song"
        )
        assert asset.get("hasVideo") is None
        assert asset.get("format") is None
        assert asset.get("hasAudio") == "1"

    def test_music_muted_under_overlays(
        self, timeline_with_primary, music_media, make_media, tmp_path
    ):
        add_music(timeline_with_primary, music_media, BuildOptions(music_db=-20.0))
        timeline_with_primary.music_duck_regions = [
            DuckRegion(Fraction(10), Fraction(15), -96.0)
        ]
        root = self._export(timeline_with_primary, tmp_path)
        primary = root.find("./library/event/project/sequence/spine/asset-clip")
        first_music = next(
            c for c in primary.findall("asset-clip") if int(c.get("lane")) < 0
        )
        keyframes = first_music.findall("./adjust-volume/param/keyframe")
        values = [k.get("value") for k in keyframes]
        assert values == ["-20dB", "-96dB", "-96dB", "-20dB"]
        # later passes outside the overlay keep the static level
        last_music = [
            c for c in primary.findall("asset-clip") if int(c.get("lane")) < 0
        ][-1]
        assert last_music.find("adjust-volume").get("amount") == "-20dB"


class TestOtioMusic:
    def test_audio_track_emitted(self, timeline_with_primary, music_media, tmp_path):
        import json as jsonlib

        add_music(timeline_with_primary, music_media, BuildOptions(music_db=-22.0))
        out = tmp_path / "m.otio"
        otio.export(timeline_with_primary, out)
        doc = jsonlib.loads(out.read_text())
        tracks = doc["tracks"]["children"]
        music_track = tracks[-1]
        assert music_track["kind"] == "Audio"
        assert music_track["name"] == "Background Music"
        clips = [c for c in music_track["children"] if c["OTIO_SCHEMA"] == "Clip.1"]
        assert len(clips) == 4
        assert clips[0]["metadata"]["editsync"]["volume_db"] == -22.0
