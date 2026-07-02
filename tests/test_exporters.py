from __future__ import annotations

import json
from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from editsync.exporters import fcpxml, otio, premiere
from editsync.exporters.fcpxml import fmt_time
from editsync.timeline import BlurRegion, DuckRegion, Marker, Timeline, TimelineClip


@pytest.fixture
def timeline(make_media) -> Timeline:
    primary1 = make_media(name="DJI_0001.mp4", duration=60.0)
    primary2 = make_media(name="DJI_0002.mp4", duration=40.0)
    overlay = make_media(name="meta_001.mp4", width=1440, height=1920, duration=8.0)

    tl = Timeline(
        name="Test Project", frame_rate=Fraction(30), width=3840, height=2160
    )
    tl.clips = [
        TimelineClip(primary1, Fraction(0), Fraction(60), Fraction(0), 0, role="DJI"),
        TimelineClip(primary2, Fraction(60), Fraction(40), Fraction(0), 0, role="DJI"),
        TimelineClip(
            overlay,
            Fraction(70),
            Fraction(8),
            Fraction(0),
            1,
            role="Meta",
            sync_confidence=0.8,
            markers=[Marker(Fraction(0), "EditSync: matched DJI_0002 @ 10.00s")],
        ),
    ]
    tl.duck_regions = [DuckRegion(Fraction(70), Fraction(78), -60.0)]
    return tl


class TestFcpxml:
    def _root(self, timeline: Timeline, tmp_path) -> ET.Element:
        out = tmp_path / "out.fcpxml"
        fcpxml.export(timeline, out)
        text = out.read_text()
        assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<!DOCTYPE fcpxml>" in text
        return ET.fromstring(text.split("<!DOCTYPE fcpxml>")[1])

    def test_fmt_time(self):
        assert fmt_time(Fraction(10)) == "10s"
        assert fmt_time(Fraction(1, 30)) == "1/30s"
        assert fmt_time(Fraction(2, 60)) == "1/30s"

    def test_resources_deduplicated(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        assets = root.findall("./resources/asset")
        assert len(assets) == 3
        formats = root.findall("./resources/format")
        # sequence + primary share 3840x2160@30; overlay adds one more
        assert len(formats) == 2

    def test_spine_structure(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        spine = root.find("./library/event/project/sequence/spine")
        primaries = spine.findall("asset-clip")
        assert len(primaries) == 2
        assert primaries[0].get("offset") == "0s"
        assert primaries[1].get("offset") == "60s"

    def test_overlay_is_connected_clip_with_lane(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        spine = root.find("./library/event/project/sequence/spine")
        second = spine.findall("asset-clip")[1]
        nested = second.findall("asset-clip")
        assert len(nested) == 1
        assert nested[0].get("lane") == "1"
        # timeline 70s inside a parent that starts at 60s -> local offset 10s
        assert nested[0].get("offset") == "10s"
        assert nested[0].get("name") == "meta_001"

    def test_duck_keyframes_on_matched_primary(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        spine = root.find("./library/event/project/sequence/spine")
        first, second = spine.findall("asset-clip")
        assert first.find("adjust-volume") is None
        keyframes = second.findall("./adjust-volume/param/keyframe")
        assert len(keyframes) == 4
        values = [k.get("value") for k in keyframes]
        assert values == ["0dB", "-60dB", "-60dB", "0dB"]
        # keyframe times are in the parent's source time: 70s-60s = 10s
        assert keyframes[1].get("time") == "10s"
        assert keyframes[2].get("time") == "18s"

    def test_markers_survive(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        markers = root.findall(".//asset-clip/marker")
        assert any("EditSync" in m.get("value") for m in markers)

    def test_media_rep_urls(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        for rep in root.findall(".//media-rep"):
            assert rep.get("src").startswith("file://")

    def test_blur_background_keyframes(self, timeline, tmp_path):
        timeline.blur_regions = [BlurRegion(Fraction(70), Fraction(78), 60.0)]
        root = self._root(timeline, tmp_path)
        effects = root.findall("./resources/effect")
        assert len(effects) == 1
        assert effects[0].get("uid") == "FFGaussianBlur"
        spine = root.find("./library/event/project/sequence/spine")
        first, second = spine.findall("asset-clip")
        # only the primary under the overlay gets the blur filter
        assert first.find("filter-video") is None
        filt = second.find("filter-video")
        assert filt is not None
        assert filt.get("ref") == effects[0].get("id")
        keyframes = filt.findall("./param/keyframe")
        values = [k.get("value") for k in keyframes]
        assert values == ["0", "60", "60", "0"]
        # blur ramps up right where the overlay starts: 70s - 60s = 10s local
        assert keyframes[1].get("time") == "10s"

    def test_no_blur_effect_without_regions(self, timeline, tmp_path):
        root = self._root(timeline, tmp_path)
        assert not root.findall("./resources/effect")
        assert not root.findall(".//filter-video")

    def test_gap_inserted_for_spaced_primaries(self, timeline, tmp_path):
        timeline.clips[1].timeline_start = Fraction(65)  # 5s hole after clip 1
        root = self._root(timeline, tmp_path)
        spine = root.find("./library/event/project/sequence/spine")
        gap = spine.find("gap")
        assert gap is not None
        assert gap.get("offset") == "60s"
        assert gap.get("duration") == "5s"


class TestPremiere:
    def test_valid_structure(self, timeline, tmp_path):
        out = tmp_path / "out.xml"
        premiere.export(timeline, out)
        root = ET.fromstring(out.read_text().split("<!DOCTYPE xmeml>")[1])
        assert root.tag == "xmeml"
        video_tracks = root.findall("./sequence/media/video/track")
        assert len(video_tracks) == 2  # V1 primaries + V2 overlay lane
        v1_clips = video_tracks[0].findall("clipitem")
        assert len(v1_clips) == 2
        overlay = video_tracks[1].find("clipitem")
        assert overlay.find("start").text == str(70 * 30)
        assert overlay.find("end").text == str(78 * 30)

    def test_file_defined_once(self, timeline, tmp_path):
        out = tmp_path / "out.xml"
        premiere.export(timeline, out)
        root = ET.fromstring(out.read_text().split("<!DOCTYPE xmeml>")[1])
        defs = [f for f in root.iter("file") if f.find("pathurl") is not None]
        refs = [f for f in root.iter("file") if f.find("pathurl") is None]
        assert len(defs) == 3
        assert refs  # audio clipitems reference by id


class TestOtio:
    def test_valid_document(self, timeline, tmp_path):
        out = tmp_path / "out.otio"
        otio.export(timeline, out)
        doc = json.loads(out.read_text())
        assert doc["OTIO_SCHEMA"] == "Timeline.1"
        tracks = doc["tracks"]["children"]
        assert len(tracks) == 2
        overlay_track = tracks[1]
        # gap of 70s then the 8s clip
        gap, clip = overlay_track["children"]
        assert gap["OTIO_SCHEMA"] == "Gap.1"
        assert gap["source_range"]["duration"]["value"] == 70 * 30
        assert clip["source_range"]["duration"]["value"] == 8 * 30
        assert clip["metadata"]["editsync"]["sync_confidence"] == 0.8

    def test_duck_regions_in_metadata(self, timeline, tmp_path):
        out = tmp_path / "out.otio"
        otio.export(timeline, out)
        doc = json.loads(out.read_text())
        regions = doc["metadata"]["editsync"]["duck_regions"]
        assert regions == [{"start": 70.0, "end": 78.0, "level_db": -60.0}]
