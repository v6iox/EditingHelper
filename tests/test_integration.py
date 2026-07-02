"""End-to-end test: synthesize real video files with ffmpeg, run the full
CLI pipeline, and validate the produced timelines.

The scenario mirrors the intended use: one long horizontal "DJI" recording
capturing continuous scene audio, plus short vertical "Meta glasses" clips
whose audio is a slice of the same scene (recorded on a different mic).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from editsync.cli import main

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)

SR = 48000
RNG = np.random.default_rng(7)


def scene_audio(seconds: float) -> np.ndarray:
    n = int(seconds * SR)
    x = np.zeros(n, dtype=np.float32)
    t = 0
    while t < n:
        burst = int(RNG.uniform(0.05, 0.4) * SR)
        gap = int(RNG.uniform(0.05, 0.5) * SR)
        end = min(t + burst, n)
        x[t:end] = RNG.normal(0, RNG.uniform(0.1, 0.5), end - t).astype(np.float32)
        t = end + gap
    return np.clip(x, -0.99, 0.99)


def write_video(
    path: Path,
    audio: np.ndarray,
    width: int,
    height: int,
    creation_time: str,
) -> None:
    wav = path.with_suffix(".wav")
    _write_wav(wav, audio)
    duration = len(audio) / SR
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", f"color=c=gray:size={width}x{height}:rate=30:duration={duration:.3f}",
            "-i", str(wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
            "-c:a", "aac", "-shortest",
            "-metadata", f"creation_time={creation_time}",
            str(path),
        ],
        check=True,
    )
    wav.unlink()


def _write_wav(path: Path, audio: np.ndarray) -> None:
    import struct
    import wave

    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


@pytest.fixture(scope="module")
def footage(tmp_path_factory) -> dict:
    """A 90s scene recorded as two DJI file chunks (the camera splits long
    recordings) + Meta clips at known scene positions, one of which spans
    the chunk boundary at 50s."""
    root = tmp_path_factory.mktemp("footage")
    master = scene_audio(90)

    dji1 = root / "DJI_0001.mp4"
    dji2 = root / "DJI_0002.mp4"
    write_video(dji1, master[: 50 * SR], 640, 360, "2026-06-20T10:00:00Z")
    write_video(dji2, master[50 * SR :], 640, 360, "2026-06-20T10:00:50Z")

    def meta_clip(name: str, start_s: float, dur_s: float, ct: str) -> Path:
        seg = master[int(start_s * SR) : int((start_s + dur_s) * SR)]
        # different mic: gain change + independent noise
        seg = 0.6 * seg + RNG.normal(0, 0.02, len(seg)).astype(np.float32)
        path = root / name
        write_video(path, np.clip(seg, -0.99, 0.99), 360, 640, ct)
        return path

    meta1 = meta_clip("meta_001.mp4", 12.0, 6.0, "2026-06-20T10:00:12Z")
    meta2 = meta_clip("meta_002.mp4", 35.5, 8.0, "2026-06-20T10:00:35Z")
    # spans the DJI file split: 47s..55s, boundary at 50s
    meta3 = meta_clip("meta_003.mp4", 47.0, 8.0, "2026-06-20T10:00:47Z")
    return {"root": root, "dji": dji1, "meta1": meta1, "meta2": meta2, "meta3": meta3}


def test_full_pipeline(footage, tmp_path):
    out = tmp_path / "project.fcpxml"
    report = tmp_path / "report.json"
    rc = main(
        [
            "sync",
            str(footage["root"]),
            "-o", str(out),
            "--report", str(report),
        ]
    )
    assert rc == 0
    assert out.exists()

    data = json.loads(report.read_text())
    placed = {Path(o["file"]).name: o for o in data["overlays"]}
    # synced to within one frame of the true scene position — including
    # meta_003, whose audio spans the DJI file-split boundary
    for name, true_start in [
        ("meta_001.mp4", 12.0),
        ("meta_002.mp4", 35.5),
        ("meta_003.mp4", 47.0),
    ]:
        assert placed[name]["placed"], name
        assert abs(placed[name]["timeline_start"] - true_start) < 1 / 30 + 0.001
    assert placed["meta_001.mp4"]["confidence"] > 0.5

    root = ET.fromstring(out.read_text().split("<!DOCTYPE fcpxml>")[1])
    spine = root.find("./library/event/project/sequence/spine")
    primaries = spine.findall("asset-clip")
    assert [p.get("name") for p in primaries] == ["DJI_0001", "DJI_0002"]
    nested = {
        c.get("name"): (p.get("name"), c)
        for p in primaries
        for c in p.findall("asset-clip")
    }
    assert set(nested) == {"meta_001", "meta_002", "meta_003"}
    # the boundary-spanning clip connects to the chunk containing its start
    assert nested["meta_003"][0] == "DJI_0001"
    # non-overlapping overlays pack onto the same lane
    assert all(c.get("lane") == "1" for _, c in nested.values())
    # primary audio is ducked under the overlays on each chunk
    assert primaries[0].findall("./adjust-volume/param/keyframe")
    assert primaries[1].findall("./adjust-volume/param/keyframe")


def test_all_formats_export(footage, tmp_path):
    out_base = tmp_path / "multi"
    rc = main(
        ["sync", str(footage["root"]), "-o", str(out_base), "--format", "all"]
    )
    assert rc == 0
    assert (tmp_path / "multi.fcpxml").exists()
    assert (tmp_path / "multi.xml").exists()
    assert (tmp_path / "multi.otio").exists()
    # premiere output parses and has the overlay on V2
    root = ET.fromstring(
        (tmp_path / "multi.xml").read_text().split("<!DOCTYPE xmeml>")[1]
    )
    tracks = root.findall("./sequence/media/video/track")
    assert len(tracks) == 2


def test_blur_background_style(footage, tmp_path):
    out = tmp_path / "blurred.fcpxml"
    rc = main(
        [
            "sync",
            str(footage["dji"]),
            str(footage["meta1"]),
            "-o", str(out),
            "--overlay-style", "blur-bg",
            "--blur-amount", "65",
        ]
    )
    assert rc == 0
    root = ET.fromstring(out.read_text().split("<!DOCTYPE fcpxml>")[1])
    assert root.find("./resources/effect").get("uid") == "FFGaussianBlur"
    primary = root.find("./library/event/project/sequence/spine/asset-clip")
    keyframes = primary.findall("./filter-video/param/keyframe")
    # ramps 0 -> 65 at the overlay, back to 0 after
    assert [k.get("value") for k in keyframes] == ["0", "65", "65", "0"]
    # the overlay itself stays sharp: no filter, no transform
    overlay = primary.find("asset-clip")
    assert overlay.find("filter-video") is None
    assert overlay.find("adjust-transform") is None


def test_background_music(footage, tmp_path):
    # a 20s sine "song" under 90s of video -> loops 5x (20*4 + 10)
    song = tmp_path / "song.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=20",
         "-c:a", "aac", str(song)],
        check=True,
    )
    out = tmp_path / "music.fcpxml"
    rc = main(
        [
            "sync",
            str(footage["root"]),
            str(song),
            "-o", str(out),
            "--music", str(song),
            "--music-volume", "-25",
            "--music-duck",
        ]
    )
    assert rc == 0
    root = ET.fromstring(out.read_text().split("<!DOCTYPE fcpxml>")[1])
    music_clips = [
        c for c in root.iter("asset-clip")
        if c.get("lane") is not None and int(c.get("lane")) < 0
    ]
    assert len(music_clips) == 5
    # every pass carries the background level (static or keyframed baseline)
    for clip in music_clips:
        vol = clip.find("adjust-volume")
        assert vol is not None
        keyframes = vol.findall("./param/keyframe")
        if keyframes:
            values = {k.get("value") for k in keyframes}
            assert "-25dB" in values and "-96dB" in values
        else:
            assert vol.get("amount") == "-25dB"
    # --music-duck: at least one pass is keyframed down under an overlay
    assert any(
        c.find("adjust-volume/param") is not None for c in music_clips
    )


def test_probe_classification(footage, capsys):
    rc = main(["probe", str(footage["root"])])
    assert rc == 0
    out = capsys.readouterr().out
    assert "primary" in out
    assert "overlay" in out


def test_dry_run_writes_nothing(footage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["sync", str(footage["root"]), "--dry-run"])
    assert rc == 0
    assert not list(tmp_path.glob("*.fcpxml"))


def test_unrelated_clip_not_placed(footage, tmp_path):
    unrelated_dir = tmp_path / "extra"
    unrelated_dir.mkdir()
    noise = scene_audio(5)
    unrelated = unrelated_dir / "meta_unrelated.mp4"
    write_video(unrelated, noise, 360, 640, "2026-06-20T12:00:00Z")

    report = tmp_path / "report.json"
    rc = main(
        [
            "sync",
            str(footage["dji"]),
            str(footage["meta1"]),
            str(unrelated),
            "-o", str(tmp_path / "p.fcpxml"),
            "--report", str(report),
        ]
    )
    assert rc == 0
    data = json.loads(report.read_text())
    by_name = {Path(o["file"]).name: o for o in data["overlays"]}
    assert by_name["meta_001.mp4"]["placed"]
    assert not by_name["meta_unrelated.mp4"]["placed"]
