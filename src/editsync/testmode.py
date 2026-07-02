"""Demo-shoot generator for test mode.

Creates a self-contained fake shoot that exercises every feature end to
end: a continuous "main camera" recording (optionally split into two
files, the way action cameras chunk long recordings), vertical "glasses"
clips whose audio genuinely overlaps the main recording (recorded by a
"different microphone": gain change + independent noise), and a short
music file. Running the normal pipeline on it demonstrates sync,
layering, ducking, title cards, music, and rendering — with known-good
expected positions.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .media import require_tool

SR = 48000
# where each glasses clip starts in the master scene, and how long it runs
OVERLAY_PLAN = [("meta_demo_001", 7.0, 6.0), ("meta_demo_002", 21.5, 6.0),
                ("meta_demo_003", 33.0, 5.0)]
SPLIT_AT = 24.0  # the boundary meta_demo_002 spans when splitting
TOTAL = 42.0


@dataclass
class DemoShoot:
    files: list[Path] = field(default_factory=list)
    music: Optional[Path] = None
    expected_overlays: dict[str, float] = field(default_factory=dict)


def _scene_audio(seconds: float, seed: int = 7):
    """Speech-like synthetic audio: irregular noise bursts (numpy)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n, dtype=np.float32)
    t = 0
    while t < n:
        burst = int(rng.uniform(0.05, 0.4) * SR)
        gap = int(rng.uniform(0.05, 0.5) * SR)
        end = min(t + burst, n)
        x[t:end] = rng.normal(0, rng.uniform(0.1, 0.5), end - t).astype(np.float32)
        t = end + gap
    return np.clip(x, -0.99, 0.99), rng


def _write_wav(path: Path, audio) -> None:
    pcm = (audio * 32767).astype("int16")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _label_filter(text: str) -> str:
    from .titlecard import _find_font

    font = _find_font()
    if not font:
        return "null"
    safe = text.replace(":", "\\:").replace("'", "")
    font_arg = font.replace("\\", "/").replace(":", "\\:")
    return (
        f"drawtext=fontfile='{font_arg}':text='{safe}':fontsize=h/12"
        f":fontcolor=white:x=(w-text_w)/2:y=h-h/6:box=1:boxcolor=black@0.5"
    )


def _mux(
    ffmpeg: str, source: str, wav: Path, out: Path,
    label: str, creation: str,
) -> None:
    subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", source,
            "-i", str(wav),
            "-vf", _label_filter(label),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-shortest",
            "-metadata", f"creation_time={creation}",
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def generate_demo_shoot(
    dest: Path,
    split_recording: bool = True,
    include_music: bool = True,
    progress: Callable[[str], None] = lambda msg: None,
) -> DemoShoot:
    """Generate the demo footage into `dest` and return what to expect."""
    ffmpeg = require_tool("ffmpeg")
    dest.mkdir(parents=True, exist_ok=True)
    shoot = DemoShoot()

    progress("Creating the scene audio…")
    master, rng = _scene_audio(TOTAL)

    def base_time(offset: float) -> str:
        m, s = divmod(int(offset), 60)
        return f"2026-06-20T10:{m:02d}:{s:02d}Z"

    # --- main camera (one file, or split like an action cam) ------------
    segments = (
        [("DJI_DEMO_0001.MP4", 0.0, SPLIT_AT),
         ("DJI_DEMO_0002.MP4", SPLIT_AT, TOTAL)]
        if split_recording
        else [("DJI_DEMO_0001.MP4", 0.0, TOTAL)]
    )
    for name, start, end in segments:
        progress(f"Filming the main camera ({name})…")
        wav = dest / f"{name}.wav"
        _write_wav(wav, master[int(start * SR):int(end * SR)])
        out = dest / name
        _mux(
            ffmpeg,
            f"testsrc2=size=1280x720:rate=30:duration={end - start:.3f}",
            wav, out, "MAIN CAMERA (demo)", base_time(start),
        )
        wav.unlink()
        shoot.files.append(out)

    # --- glasses clips: same scene, different "microphone" --------------
    import numpy as np

    for i, (name, start, length) in enumerate(OVERLAY_PLAN):
        progress(f"Recording glasses clip {i + 1} of {len(OVERLAY_PLAN)}…")
        seg = master[int(start * SR):int((start + length) * SR)]
        seg = 0.6 * seg + rng.normal(0, 0.02, len(seg)).astype(np.float32)
        wav = dest / f"{name}.wav"
        _write_wav(wav, np.clip(seg, -0.99, 0.99))
        out = dest / f"{name}.mp4"
        shade = ["0x1d3557", "0x431d55", "0x1d5540"][i % 3]
        _mux(
            ffmpeg,
            f"color=c={shade}:size=720x1280:rate=30:duration={length:.3f}",
            wav, out, f"GLASSES CLIP {i + 1} (demo)", base_time(start),
        )
        wav.unlink()
        shoot.files.append(out)
        shoot.expected_overlays[out.name] = start

    # --- a short song to loop underneath ---------------------------------
    if include_music:
        progress("Writing the demo music…")
        music = dest / "demo_music.m4a"
        melody = (
            "aevalsrc='0.12*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.5*t))"
            "+0.08*sin(2*PI*277.18*t)+0.06*sin(2*PI*329.63*t)':d=12"
        )
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", melody,
             "-c:a", "aac", str(music)],
            check=True,
            capture_output=True,
        )
        shoot.files.append(music)
        shoot.music = music

    progress("Demo shoot ready.")
    return shoot
