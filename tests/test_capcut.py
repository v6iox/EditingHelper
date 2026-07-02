"""CapCut draft exporter: structure of the generated draft folder."""

from __future__ import annotations

import json
from fractions import Fraction

import pytest

from editsync.exporters import EXPORTERS, capcut
from editsync.timeline import (
    DuckRegion,
    Timeline,
    TimelineClip,
    TitleCard,
)


@pytest.fixture
def timeline(make_media) -> Timeline:
    tl = Timeline(name="Shop Video", frame_rate=Fraction(30), width=1920, height=1080)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", width=1920, height=1080, duration=60.0),
            Fraction(0), Fraction(60), Fraction(0), 0, role="DJI",
        )
    )
    tl.clips.append(
        TimelineClip(
            make_media(name="meta_001.mp4", width=1080, height=1920, duration=6.0),
            Fraction(10), Fraction(6), Fraction(0), 1, role="Meta",
        )
    )
    tl.duck_regions = [DuckRegion(Fraction(10), Fraction(16), -60.0)]
    return tl


def _load(tmp_path, timeline):
    out = tmp_path / "Shop_Video.capcut"
    capcut.export(timeline, out)
    draft = json.loads((out / "draft_content.json").read_text())
    return out, draft


class TestCapcutDraft:
    def test_registered(self):
        assert "capcut" in EXPORTERS
        assert EXPORTERS["capcut"][1] == ".capcut"

    def test_folder_contents(self, timeline, tmp_path):
        out, draft = _load(tmp_path, timeline)
        assert (out / "draft_meta_info.json").exists()
        assert (out / "INSTRUCTIONS.txt").exists()
        meta = json.loads((out / "draft_meta_info.json").read_text())
        assert meta["draft_name"] == "Shop Video"
        assert meta["draft_id"] == draft["id"]

    def test_times_are_microseconds(self, timeline, tmp_path):
        _, draft = _load(tmp_path, timeline)
        assert draft["duration"] == 60_000_000
        overlay_track = draft["tracks"][1]
        seg = overlay_track["segments"][0]
        assert seg["target_timerange"] == {"start": 10_000_000, "duration": 6_000_000}
        assert seg["source_timerange"]["start"] == 0

    def test_track_layout(self, timeline, tmp_path):
        _, draft = _load(tmp_path, timeline)
        types = [t["type"] for t in draft["tracks"]]
        assert types == ["video", "video"]  # primary + overlay lane
        assert len(draft["tracks"][0]["segments"]) == 1
        materials = draft["materials"]["videos"]
        assert {m["material_name"] for m in materials} == {
            "DJI_0001.mp4", "meta_001.mp4",
        }

    def test_duck_keyframes_on_primary(self, timeline, tmp_path):
        _, draft = _load(tmp_path, timeline)
        primary_seg = draft["tracks"][0]["segments"][0]
        kf_groups = primary_seg["common_keyframes"]
        assert len(kf_groups) == 1
        assert kf_groups[0]["property_type"] == "KFTypeVolume"
        values = [k["values"][0] for k in kf_groups[0]["keyframe_list"]]
        assert values[0] == 1.0
        assert min(values) == pytest.approx(0.001, abs=0.001)
        offsets = [k["time_offset"] for k in kf_groups[0]["keyframe_list"]]
        assert offsets == sorted(offsets)
        assert offsets[1] == 10_000_000

    def test_music_track_and_volume(self, timeline, tmp_path, make_media):
        song = make_media(name="song.mp3", width=0, height=0, duration=25.0)
        song.frame_rate = Fraction(0)
        for start in (0, 25):
            timeline.clips.append(
                TimelineClip(song, Fraction(start), Fraction(25), Fraction(0),
                             -1, role="Music", volume_db=-20.0)
            )
        timeline.clips[-1].duration = Fraction(10)  # trimmed last pass
        _, draft = _load(tmp_path, timeline)
        audio_track = next(t for t in draft["tracks"] if t["type"] == "audio")
        assert len(audio_track["segments"]) == 2
        assert audio_track["segments"][0]["volume"] == pytest.approx(0.1)
        assert len(draft["materials"]["audios"]) == 1

    def test_title_card(self, timeline, tmp_path):
        timeline.title_card = TitleCard(
            title="Door Panel", description="2023 BRZ",
            hold=Fraction(3), fade=Fraction(1), style="statement",
        )
        out, draft = _load(tmp_path, timeline)
        assert (out / "title_background.png").exists()
        text_track = next(t for t in draft["tracks"] if t["type"] == "text")
        text_seg = text_track["segments"][0]
        assert text_seg["target_timerange"]["duration"] == 4_000_000
        fade = text_seg["common_keyframes"][0]
        assert fade["property_type"] == "KFTypeAlpha"
        assert [k["time_offset"] for k in fade["keyframe_list"]] == [
            3_000_000, 4_000_000,
        ]
        material = draft["materials"]["texts"][0]
        content = json.loads(material["content"])
        assert content["text"] == "DOOR PANEL\n2023 BRZ"

    def test_pip_transform_normalized(self, timeline, tmp_path):
        overlay = timeline.overlay_clips[0]
        overlay.transform_scale = (0.6, 0.6)
        overlay.transform_position = (537.6, 0.0)  # pip-right at 1920 wide
        _, draft = _load(tmp_path, timeline)
        seg = draft["tracks"][1]["segments"][0]
        assert seg["clip"]["scale"]["x"] == pytest.approx(0.6)
        assert seg["clip"]["transform"]["x"] == pytest.approx(0.56)
