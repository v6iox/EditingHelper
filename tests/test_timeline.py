from __future__ import annotations

from fractions import Fraction

from editsync.media import Role
from editsync.timeline import (
    TimelineClip,
    assign_lanes,
    merge_intervals,
    quantize,
)


def clip(make_media, start: float, dur: float, lane: int = 1) -> TimelineClip:
    return TimelineClip(
        media=make_media(name=f"c{start}.mp4"),
        timeline_start=Fraction(start),
        duration=Fraction(dur),
        source_start=Fraction(0),
        lane=lane,
    )


class TestQuantize:
    def test_snaps_to_frame(self):
        fd = Fraction(1, 30)
        assert quantize(Fraction(101, 1000), fd) == Fraction(3, 30)
        assert quantize(Fraction(10), fd) == Fraction(10)

    def test_ntsc_rate(self):
        fd = Fraction(1001, 30000)
        t = quantize(Fraction(5, 1), fd)
        assert (t / fd).denominator == 1  # exact frame count


class TestAssignLanes:
    def test_non_overlapping_share_lane(self, make_media):
        clips = [clip(make_media, 0, 5), clip(make_media, 10, 5)]
        assign_lanes(clips)
        assert [c.lane for c in clips] == [1, 1]

    def test_overlapping_get_distinct_lanes(self, make_media):
        clips = [clip(make_media, 0, 10), clip(make_media, 5, 10), clip(make_media, 8, 4)]
        assign_lanes(clips)
        lanes = [c.lane for c in clips]
        assert lanes[0] != lanes[1]
        assert lanes[1] != lanes[2]
        assert lanes[0] != lanes[2]

    def test_lane_reuse_after_clip_ends(self, make_media):
        clips = [clip(make_media, 0, 5), clip(make_media, 2, 10), clip(make_media, 6, 3)]
        assign_lanes(clips)
        # third clip starts after the first ends, reuses lane 1
        assert clips[0].lane == 1
        assert clips[1].lane == 2
        assert clips[2].lane == 1

    def test_lane_per_clip(self, make_media):
        clips = [clip(make_media, 0, 5), clip(make_media, 20, 5)]
        assign_lanes(clips, lane_per_clip=True)
        assert [c.lane for c in clips] == [1, 2]

    def test_primary_clips_untouched(self, make_media):
        primary = clip(make_media, 0, 100, lane=0)
        overlay = clip(make_media, 5, 5)
        assign_lanes([primary, overlay])
        assert primary.lane == 0
        assert overlay.lane == 1


class TestMergeIntervals:
    def test_merges_near_adjacent(self):
        result = merge_intervals(
            [(Fraction(0), Fraction(5)), (Fraction(5), Fraction(8))]
        )
        assert result == [(Fraction(0), Fraction(8))]

    def test_keeps_distant_separate(self):
        result = merge_intervals(
            [(Fraction(0), Fraction(2)), (Fraction(10), Fraction(12))]
        )
        assert len(result) == 2

    def test_unsorted_input(self):
        result = merge_intervals(
            [(Fraction(10), Fraction(12)), (Fraction(0), Fraction(2))]
        )
        assert result[0][0] == Fraction(0)
