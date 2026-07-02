from __future__ import annotations

import datetime as dt

from editsync.media import Role, classify, sort_primaries


class TestClassify:
    def test_landscape_is_primary(self, make_media):
        m = make_media(name="clip001.mp4", width=3840, height=2160)
        classify([m])
        assert m.role == Role.PRIMARY

    def test_portrait_is_overlay(self, make_media):
        m = make_media(name="clip002.mp4", width=1440, height=1920)
        classify([m])
        assert m.role == Role.OVERLAY

    def test_rotation_makes_portrait(self, make_media):
        m = make_media(name="clip003.mp4", width=1920, height=1080, rotation=90)
        classify([m])
        assert m.is_portrait
        assert m.role == Role.OVERLAY

    def test_dji_filename_hint(self, make_media):
        # portrait DJI file: filename hint outranks orientation
        m = make_media(name="DJI_20260101_0001.mp4", width=1080, height=1920)
        classify([m])
        assert m.role == Role.PRIMARY

    def test_metadata_outranks_filename(self, make_media):
        m = make_media(name="DJI_lookalike.mp4")
        m.make = "Meta"
        m.model = "Ray-Ban Meta Glasses"
        classify([m])
        assert m.role == Role.OVERLAY

    def test_cli_patterns_outrank_everything(self, make_media):
        m = make_media(name="DJI_0001.mp4", width=3840, height=2160)
        classify([m], overlay_patterns=["DJI_*"])
        assert m.role == Role.OVERLAY


class TestSortPrimaries:
    def test_sorted_by_creation_time(self, make_media):
        t0 = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.timezone.utc)
        b = make_media(name="b.mp4", creation_time=t0 + dt.timedelta(minutes=5))
        a = make_media(name="a.mp4", creation_time=t0)
        assert [m.name for m in sort_primaries([b, a])] == ["a", "b"]

    def test_missing_times_fall_back_to_name(self, make_media):
        b = make_media(name="b.mp4")
        a = make_media(name="a.mp4")
        assert [m.name for m in sort_primaries([b, a])] == ["a", "b"]


class TestHDRDetection:
    def test_sdr_bt709_is_not_hdr(self, make_media):
        m = make_media(color_space="bt709", color_primaries="bt709",
                       color_transfer="bt709")
        assert not m.is_hdr

    def test_untagged_is_not_hdr(self, make_media):
        assert not make_media().is_hdr

    def test_hlg_is_hdr(self, make_media):
        # Meta glasses default: HLG transfer in BT.2020
        m = make_media(color_space="bt2020nc", color_primaries="bt2020",
                       color_transfer="arib-std-b67")
        assert m.is_hdr

    def test_pq_is_hdr(self, make_media):
        m = make_media(color_transfer="smpte2084")
        assert m.is_hdr

    def test_bt2020_primaries_alone_is_hdr(self, make_media):
        assert make_media(color_primaries="bt2020").is_hdr
