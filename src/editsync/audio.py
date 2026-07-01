"""Audio extraction and fingerprinting.

Audio is decoded to mono float32 with ffmpeg, then reduced to an onset
envelope (half-wave-rectified difference of log energy). Envelopes are
robust to the level/EQ differences between a DJI action cam mic and the
Meta glasses mic array, which is what makes cross-camera correlation work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .media import ProbeError, require_tool

SAMPLE_RATE = 8000  # Hz used for sync analysis (plenty for envelope alignment)
ENVELOPE_FPS = 100  # envelope frames per second -> 10 ms coarse resolution


def extract_audio(
    path: Path,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    duration: float | None = None,
) -> np.ndarray:
    """Decode a file's audio to mono float32 at `sample_rate` via ffmpeg."""
    ffmpeg = require_tool("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]

    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise ProbeError(
            f"ffmpeg audio extraction failed for {path}: "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if samples.size == 0:
        raise ProbeError(f"No audio decoded from {path}")
    return samples.astype(np.float64)


def onset_envelope(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    fps: int = ENVELOPE_FPS,
) -> np.ndarray:
    """Compute a per-frame onset envelope from mono audio.

    Steps: frame RMS energy -> log compression -> first difference ->
    half-wave rectification -> light smoothing. The result emphasizes
    *changes* in loudness (speech syllables, transients), which correlate
    strongly across different microphones recording the same scene.
    """
    hop = sample_rate // fps
    n_frames = len(samples) // hop
    if n_frames < 2:
        return np.zeros(1)
    trimmed = samples[: n_frames * hop].reshape(n_frames, hop)
    energy = np.sqrt(np.mean(trimmed * trimmed, axis=1))
    log_energy = np.log1p(1000.0 * energy)
    onset = np.diff(log_energy, prepend=log_energy[0])
    onset = np.maximum(onset, 0.0)
    # 3-frame moving average tolerates small clock differences between cameras
    kernel = np.ones(3) / 3.0
    return np.convolve(onset, kernel, mode="same")


def zscore(x: np.ndarray) -> np.ndarray:
    std = x.std()
    if std < 1e-12:
        return np.zeros_like(x)
    return (x - x.mean()) / std
