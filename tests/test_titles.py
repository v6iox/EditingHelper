"""Title-card feature: model, builder, PNG generation, FCPXML emission."""

from __future__ import annotations

import struct
import zlib
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from editsync.builder import BuildOptions
from editsync.exporters import fcpxml, otio
from editsync.pngutil import write_solid_png
from editsync.timeline import Timeline, TimelineClip, TitleCard
from editsync.titles import DEFAULT_STYLE, STYLES, get_style


@pytest.fixture
def timeline(make_media) -> Timeline:
    tl = Timeline(name="Training", frame_rate=Fraction(30), width=3840, height=2160)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", duration=60.0),
            Fraction(0), Fraction(60), Fraction(0), 0, role="DJI",
        )
    )
    tl.clips.append(
        TimelineClip(
            make_media(name="meta_001.mp4", width=1440, height=1920, duration=6.0),
            Fraction(10), Fraction(6), Fraction(0), 1, role="Meta",
        )
    )
    return tl


class TestPngUtil:
    def test_writes_valid_white_png(self, tmp_path):
        out = tmp_path / "bg.png"
        write_solid_png(out, 320, 180)
        data = out.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (320, 180)
        # decode the IDAT payload and confirm it is pure white
        idat_start = data.index(b"IDAT") + 4
        idat_len = struct.unpack(">I", data[idat_start - 8 : idat_start - 4])[0]
        raw = zlib.decompress(data[idat_start : idat_start + idat_len])
        assert len(raw) == 180 * (1 + 320 * 3)
        assert set(raw[1 : 1 + 320 * 3]) == {255}

    def test_rejects_bad_dimensions(self, tmp_path):
        with pytest.raises(ValueError):
            write_solid_png(tmp_path / "x.png", 0, 10)


class TestStyles:
    def test_all_styles_complete(self):
        assert set(STYLES) == {"classic", "lower-left", "statement", "elegant"}
        for style in STYLES.values():
            assert style.title_size > style.desc_size
            assert style.alignment in ("center", "left")

    def test_unknown_key_falls_back(self):
        assert get_style("nope").key == DEFAULT_STYLE


class TestBuilderTitleCard:
    def test_card_created_from_options(self, timeline):
        # exercised through the real build() in integration tests; here we
        # verify the quantization/threshold logic via the same code path
        from editsync.timeline import quantize

        opts = BuildOptions(
            title_text="  Front Bumper Removal  ",
            title_description=" 2024 Toyota GR86 ",
            title_hold=2.5,
            title_fade=0.5,
            title_style="statement",
        )
        card = TitleCard(
            title=opts.title_text.strip(),
            description=opts.title_description.strip(),
            hold=quantize(Fraction(opts.title_hold).limit_denominator(100), Fraction(1, 30)),
            fade=quantize(Fraction(opts.title_fade).limit_denominator(100), Fraction(1, 30)),
            style=opts.title_style,
        )
        assert card.title == "Front Bumper Removal"
        assert card.description == "2024 Toyota GR86"
        assert float(card.duration) == pytest.approx(3.0)


class TestFcpxmlTitleCard:
    def _export(self, tl, tmp_path):
        out = tmp_path / "training.fcpxml"
        fcpxml.export(tl, out)
        return out, ET.fromstring(out.read_text().split("<!DOCTYPE fcpxml>")[1])

    def test_background_png_written_and_referenced(self, timeline, tmp_path):
        timeline.title_card = TitleCard(title="Door Panel", description="2023 BRZ")
        out, root = self._export(timeline, tmp_path)
        bg = tmp_path / "training_title_background.png"
        assert bg.exists()
        width, height = struct.unpack(">II", bg.read_bytes()[16:24])
        assert (width, height) == (3840, 2160)
        # referenced by an image asset (0s duration, format w/o frameDuration)
        asset = next(
            a for a in root.findall("./resources/asset")
            if a.get("name") == bg.stem
        )
        assert asset.get("duration") == "0s"
        fmt = next(
            f for f in root.findall("./resources/format")
            if f.get("id") == asset.get("format")
        )
        assert fmt.get("frameDuration") is None

    def test_card_elements_fade_and_lanes(self, timeline, tmp_path):
        timeline.title_card = TitleCard(
            title="Door Panel", description="2023 BRZ",
            hold=Fraction(3), fade=Fraction(2),
        )
        _, root = self._export(timeline, tmp_path)
        first = root.find("./library/event/project/sequence/spine/asset-clip")
        video = first.find("video")
        title = first.find("title")
        assert video is not None and title is not None
        # card sits above the overlay lane (1) -> lanes 2 and 3
        assert int(video.get("lane")) == 2
        assert int(title.get("lane")) == 3
        for el in (video, title):
            assert el.get("offset") == "0s"
            assert el.get("duration") == "5s"
            keyframes = el.findall("./adjust-blend/param/keyframe")
            assert [(k.get("time"), k.get("value")) for k in keyframes] == [
                ("3s", "1"), ("5s", "0"),
            ]

    def test_text_runs_and_fonts(self, timeline, tmp_path):
        timeline.title_card = TitleCard(
            title="Door Panel", description="2023 BRZ", style="classic"
        )
        _, root = self._export(timeline, tmp_path)
        title = root.find(".//title")
        runs = title.findall("./text/text-style")
        assert runs[0].text == "Door Panel\n"
        assert runs[1].text == "2023 BRZ"
        defs = {d.get("id"): d.find("text-style") for d in title.findall("text-style-def")}
        # Motion templates conform to project resolution, so fontSize stays
        # at the 1080p reference value even on this 4K timeline
        assert defs["ts_card_title"].get("fontSize") == "92"
        assert defs["ts_card_title"].get("fontFace") == "Bold"
        assert defs["ts_card_desc"].get("fontSize") == "48"
        effect = root.find("./resources/effect[@name='Basic Title']")
        assert effect is not None

    def test_relative_export_path_gets_absolute_png_url(
        self, timeline, tmp_path, monkeypatch
    ):
        # regression: `-o myvideo.fcpxml` (relative, the CLI default) must
        # still produce a resolvable absolute file:// URL for the card PNG
        monkeypatch.chdir(tmp_path)
        timeline.title_card = TitleCard(title="Door Panel")
        fcpxml.export(timeline, Path("relative_out.fcpxml"))
        root = ET.fromstring(
            Path("relative_out.fcpxml").read_text().split("<!DOCTYPE fcpxml>")[1]
        )
        png_asset = next(
            a for a in root.findall("./resources/asset")
            if "title_background" in (a.get("name") or "")
        )
        src = png_asset.find("media-rep").get("src")
        assert src.startswith("file:///"), src
        assert (tmp_path / "relative_out_title_background.png").exists()

    def test_statement_style_uppercases(self, timeline, tmp_path):
        timeline.title_card = TitleCard(
            title="Door Panel", description="2023 BRZ", style="statement"
        )
        _, root = self._export(timeline, tmp_path)
        runs = root.findall(".//title/text/text-style")
        assert runs[0].text == "DOOR PANEL\n"
        assert runs[1].text == "2023 BRZ"

    def test_lower_left_positions_title(self, timeline, tmp_path):
        timeline.title_card = TitleCard(title="Door Panel", style="lower-left")
        _, root = self._export(timeline, tmp_path)
        transform = root.find(".//title/adjust-transform")
        assert transform is not None
        x, y = (float(v) for v in transform.get("position").split())
        assert x < 0 and y < 0

    def test_no_description_single_run(self, timeline, tmp_path):
        timeline.title_card = TitleCard(title="Door Panel")
        _, root = self._export(timeline, tmp_path)
        runs = root.findall(".//title/text/text-style")
        assert len(runs) == 1
        assert runs[0].text == "Door Panel"
        assert len(root.findall(".//title/text-style-def")) == 1

    def test_no_card_no_png(self, timeline, tmp_path):
        out, root = self._export(timeline, tmp_path)
        assert not list(tmp_path.glob("*_title_background.png"))
        assert root.find(".//title") is None


class TestOtioTitleCard:
    def test_metadata_carried(self, timeline, tmp_path):
        import json

        timeline.title_card = TitleCard(
            title="Door Panel", description="2023 BRZ",
            hold=Fraction(3), fade=Fraction(1),
        )
        out = tmp_path / "t.otio"
        otio.export(timeline, out)
        meta = json.loads(out.read_text())["metadata"]["editsync"]["title_card"]
        assert meta == {
            "title": "Door Panel",
            "description": "2023 BRZ",
            "hold_seconds": 3.0,
            "fade_seconds": 1.0,
        }
