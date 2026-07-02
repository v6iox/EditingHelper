"""Finished-video renderer.

Turns a Timeline into one final MP4 with ffmpeg — no editing software
involved. Everything the timeline describes is composited:

- primary storyline (multiple files butt-joined, gaps become black)
- overlay clips at their synced positions, framed per the chosen style
  (centered, fill, picture-in-picture) with the wide shot Gaussian-blurred
  underneath for the blur-bg style
- the opening title card (full-frame PNG with text) fading out on top
- audio: primary ducked under overlays, overlay audio at full level,
  looping background music at its set level (optionally muted under
  overlays) — all with the same fade ramps the FCPXML export encodes

Progress is reported as 0-100 via ffmpeg's -progress output.
"""

from __future__ import annotations

import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional

from .media import ProbeError, require_tool
from .timeline import DuckRegion, Timeline, TimelineClip

FADE = 0.25  # seconds; matches the exporters' duck/blur fade


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def region_gain_expr(region: DuckRegion, fade: float = FADE) -> str:
    """A piecewise-linear ffmpeg volume expression for one duck region:
    unity outside, `level` inside, linear ramps over `fade` seconds."""
    level = max(db_to_gain(region.level_db), 0.0)
    s, e = float(region.start), float(region.end)
    s0, e1 = max(0.0, s - fade), e + fade
    f_in = max(s - s0, 1e-6)
    return (
        f"if(lt(t,{s0:.4f}),1,"
        f"if(lt(t,{s:.4f}),1+({level:.6f}-1)*(t-{s0:.4f})/{f_in:.4f},"
        f"if(lt(t,{e:.4f}),{level:.6f},"
        f"if(lt(t,{e1:.4f}),{level:.6f}+(1-{level:.6f})*(t-{e:.4f})/{fade:.4f},1))))"
    )


def _duck_chain(regions: list[DuckRegion]) -> str:
    """Chained volume filters (one per merged region)."""
    return "".join(
        f",volume='{region_gain_expr(r)}':eval=frame" for r in regions
    )


def _enable_expr(intervals: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{s:.4f},{e:.4f})" for s, e in intervals)


def _overlay_scale(style: str, w: int, h: int) -> str:
    """Scale filter for an overlay clip per framing style."""
    if style == "fill":
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}"
        )
    if style in ("pip-left", "pip-right"):
        return (
            f"scale={round(w * 0.4)}:{round(h * 0.62)}:"
            f"force_original_aspect_ratio=decrease"
        )
    # center / blur-bg: fit inside the frame
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease"


def _overlay_position(style: str, w: int) -> tuple[str, str]:
    if style == "pip-left":
        return (f"{round(w * 0.04)}", "(H-h)/2")
    if style == "pip-right":
        return (f"W-w-{round(w * 0.04)}", "(H-h)/2")
    return ("(W-w)/2", "(H-h)/2")


def build_command(
    timeline: Timeline,
    output: Path,
    overlay_style: str = "center",
    blur_amount: float = 50.0,
    card_png: Optional[Path] = None,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Assemble the full ffmpeg command for a timeline render."""
    w, h = timeline.width, timeline.height
    rate = timeline.frame_rate
    fps = f"{rate.numerator}/{rate.denominator}"
    total = float(timeline.duration)
    primaries = timeline.primary_clips
    overlays = timeline.overlay_clips
    music = timeline.music_clips
    card = timeline.title_card

    if not primaries:
        raise ProbeError("Nothing to render: no primary clips on the timeline.")

    cmd: list[str] = [ffmpeg, "-y", "-v", "error", "-nostdin"]
    filters: list[str] = []
    input_index: dict[str, int] = {}
    n_inputs = 0

    def add_input(args: list[str], key: Optional[str] = None) -> int:
        nonlocal n_inputs
        if key is not None and key in input_index:
            return input_index[key]
        cmd.extend(args)
        idx = n_inputs
        n_inputs += 1
        if key is not None:
            input_index[key] = idx
        return idx

    norm = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1"
    )

    # --- primary storyline video + audio (gaps become black/silence) ----
    segments: list[tuple[str, str]] = []  # (video label, audio label)
    cursor = 0.0
    for i, clip in enumerate(primaries):
        start = float(clip.timeline_start)
        if start > cursor + 0.001:  # gap
            gap = start - cursor
            filters.append(
                f"color=black:s={w}x{h}:r={fps}:d={gap:.4f},setsar=1[pgv{i}]"
            )
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{gap:.4f}[pga{i}]"
            )
            segments.append((f"[pgv{i}]", f"[pga{i}]"))
        idx = add_input(["-i", str(clip.media.path)], key=str(clip.media.path))
        ss, dur = float(clip.source_start), float(clip.duration)
        filters.append(
            f"[{idx}:v]trim={ss:.4f}:{ss + dur:.4f},setpts=PTS-STARTPTS,"
            f"{norm}[pv{i}]"
        )
        if clip.media.has_audio:
            filters.append(
                f"[{idx}:a]atrim={ss:.4f}:{ss + dur:.4f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo[pa{i}]"
            )
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.4f}[pa{i}]"
            )
        segments.append((f"[pv{i}]", f"[pa{i}]"))
        cursor = float(clip.timeline_end)

    if len(segments) == 1:
        filters.append(f"{segments[0][0]}null[basev]")
        filters.append(f"{segments[0][1]}anull[pastory]")
    else:
        joined = "".join(v + a for v, a in segments)
        filters.append(
            f"{joined}concat=n={len(segments)}:v=1:a=1[basev][pastory]"
        )

    # --- blur the wide shot under overlays (blur-bg style) --------------
    vlabel = "[basev]"
    if overlay_style == "blur-bg" and timeline.blur_regions:
        sigma = max(blur_amount / 4, 1)
        enable = _enable_expr(
            [(float(r.start), float(r.end)) for r in timeline.blur_regions]
        )
        filters.append(
            f"{vlabel}gblur=sigma={sigma:.2f}:enable='{enable}'[blurred]"
        )
        vlabel = "[blurred]"

    # --- overlays --------------------------------------------------------
    audio_mix: list[str] = []
    for k, clip in enumerate(overlays):
        idx = add_input(["-i", str(clip.media.path)], key=str(clip.media.path))
        ss, dur = float(clip.source_start), float(clip.duration)
        tl = float(clip.timeline_start)
        filters.append(
            f"[{idx}:v]trim={ss:.4f}:{ss + dur:.4f},setpts=PTS-STARTPTS,"
            f"{_overlay_scale(overlay_style, w, h)},fps={fps},setsar=1,"
            f"setpts=PTS+{tl:.4f}/TB[ov{k}]"
        )
        x, y = _overlay_position(overlay_style, w)
        filters.append(
            f"{vlabel}[ov{k}]overlay={x}:{y}:eof_action=pass"
            f":enable='between(t,{tl:.4f},{tl + dur:.4f})'[ovout{k}]"
        )
        vlabel = f"[ovout{k}]"
        if clip.media.has_audio:
            delay_ms = round(tl * 1000)
            filters.append(
                f"[{idx}:a]atrim={ss:.4f}:{ss + dur:.4f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,"
                f"adelay={delay_ms}:all=1[oa{k}]"
            )
            audio_mix.append(f"[oa{k}]")

    # --- title card -------------------------------------------------------
    if card is not None and card_png is not None:
        card_dur = float(card.duration)
        idx = add_input(
            ["-loop", "1", "-t", f"{card_dur + 0.1:.4f}", "-i", str(card_png)]
        )
        filters.append(
            f"[{idx}:v]scale={w}:{h},format=rgba,"
            f"fade=t=out:st={float(card.hold):.4f}:d={float(card.fade):.4f}:alpha=1,"
            f"fps={fps},setsar=1[card]"
        )
        filters.append(
            f"{vlabel}[card]overlay=0:0:eof_action=pass"
            f":enable='between(t,0,{card_dur:.4f})'[cardout]"
        )
        vlabel = "[cardout]"

    filters.append(f"{vlabel}format=yuv420p[vout]")

    # --- audio mix --------------------------------------------------------
    filters.append(
        f"[pastory]anull{_duck_chain(timeline.duck_regions)}[primarya]"
    )
    audio_mix.insert(0, "[primarya]")

    if music:
        m = music[0].media
        loops = max(0, math.ceil(total / max(float(m.duration), 0.1)) - 1)
        idx = add_input(
            ["-stream_loop", str(loops), "-i", str(m.path)],
            key=f"music:{m.path}",
        )
        gain = db_to_gain(music[0].volume_db or -22.0)
        filters.append(
            f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=0:{total:.4f},volume={gain:.6f}"
            f"{_duck_chain(timeline.music_duck_regions)}[musica]"
        )
        audio_mix.append("[musica]")

    if len(audio_mix) == 1:
        filters.append(f"{audio_mix[0]}anull[aout]")
    else:
        filters.append(
            f"{''.join(audio_mix)}amix=inputs={len(audio_mix)}"
            f":duration=longest:normalize=0[aout]"
        )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.4f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output),
    ]
    return cmd


def render(
    timeline: Timeline,
    output: Path,
    overlay_style: str = "center",
    blur_amount: float = 50.0,
    card_png: Optional[Path] = None,
    progress: Optional[Callable[[int], None]] = None,
) -> None:
    """Render the timeline to `output`, reporting percent via `progress`."""
    ffmpeg = require_tool("ffmpeg")
    cmd = build_command(
        timeline, output,
        overlay_style=overlay_style,
        blur_amount=blur_amount,
        card_png=card_png,
        ffmpeg=ffmpeg,
    )
    total_us = float(timeline.duration) * 1_000_000
    proc = subprocess.Popen(
        cmd + ["-progress", "pipe:1", "-nostats"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and progress and total_us > 0:
            try:
                pct = int(min(99, int(line.split("=")[1]) * 100 / total_us))
                progress(pct)
            except ValueError:
                pass
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise ProbeError(f"Video render failed: {err.strip()[-2000:]}")
    if progress:
        progress(100)
