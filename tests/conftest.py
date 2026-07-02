from __future__ import annotations

import datetime as dt
from fractions import Fraction
from pathlib import Path

import pytest

from editsync.media import MediaFile, Role


@pytest.fixture
def make_media():
    """Factory for MediaFile objects without touching ffprobe."""

    def _make(
        name: str = "clip.mp4",
        width: int = 3840,
        height: int = 2160,
        duration: float = 60.0,
        fps: Fraction = Fraction(30),
        role: Role = Role.UNKNOWN,
        creation_time: dt.datetime | None = None,
        rotation: int = 0,
        has_audio: bool = True,
        color_space: str = "",
        color_primaries: str = "",
        color_transfer: str = "",
    ) -> MediaFile:
        return MediaFile(
            path=Path(f"/media/{name}").resolve(),
            width=width,
            height=height,
            rotation=rotation,
            duration=Fraction(duration).limit_denominator(1000),
            frame_rate=fps,
            audio_rate=48000,
            audio_channels=2,
            has_audio=has_audio,
            creation_time=creation_time,
            role=role,
            color_space=color_space,
            color_primaries=color_primaries,
            color_transfer=color_transfer,
        )

    return _make
