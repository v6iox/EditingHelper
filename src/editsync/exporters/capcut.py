"""CapCut desktop exporter (experimental).

CapCut has no official project-interchange format — it cannot import
FCPXML, xmeml, or OTIO. What it does have is its local *draft* format:
each project is a folder holding `draft_content.json` (tracks, segments,
materials; all times in microseconds) inside CapCut's drafts directory.
The format is undocumented but well mapped by the community; this
exporter writes a draft covering the timeline's core:

- the primary storyline on a video track (files butt-joined)
- each overlay on a video track above, at its synced position, with a
  volume of 1 and framing transform for pip styles
- volume keyframes ducking the primary under overlays
- looping background-music segments at their set level (with mute
  keyframes when music-duck is on)
- the title card as a white image + text segment fading out

Because ByteDance can change the draft schema at any release, treat this
as experimental: if a CapCut update stops reading it, the finished-video
render (`--render`) always works. Tested structure targets CapCut
desktop 4.x-5.x era drafts.

Install: copy the exported `<name>.capcut` folder into CapCut's drafts
directory (the INSTRUCTIONS.txt inside lists the per-OS paths), then
restart CapCut — the project appears in the home screen list.
"""

from __future__ import annotations

import json
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Optional

from ..pngutil import write_solid_png
from ..timeline import DuckRegion, Timeline, TimelineClip
from ..titles import get_style

US = 1_000_000
FADE_US = 250_000  # matches the engine's 0.25 s duck/fade ramps


def _us(t) -> int:
    return round(float(t) * US)


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _timerange(start, duration) -> dict:
    return {"start": _us(start), "duration": _us(duration)}


def _gain(db: float) -> float:
    return round(10 ** (db / 20), 6)


def _volume_keyframes(
    clip: TimelineClip, regions: list[DuckRegion], base: float
) -> list[dict]:
    """CapCut volume keyframes (time offsets relative to segment start)."""
    points: list[tuple[int, float]] = []
    seg_start = float(clip.timeline_start)
    seg_end = float(clip.timeline_end)
    for r in regions:
        s, e = float(r.start), float(r.end)
        if e <= seg_start or s >= seg_end:
            continue
        level = max(0.0, _gain(r.level_db)) * base
        for t, v in [
            (s - 0.25, base), (s, level), (e, level), (e + 0.25, base),
        ]:
            local = max(0.0, min(t, seg_end) - seg_start)
            points.append((round(local * US), round(v, 6)))
    points.sort()
    deduped: list[tuple[int, float]] = []
    for t, v in points:
        if deduped and t <= deduped[-1][0]:
            deduped[-1] = (deduped[-1][0], min(deduped[-1][1], v))
        else:
            deduped.append((t, v))
    if not deduped:
        return []
    return [
        {
            "id": _uid(),
            "material_id": "",
            "property_type": "KFTypeVolume",
            "keyframe_list": [
                {
                    "curveType": "Line",
                    "graphID": "",
                    "left_control": {"x": 0.0, "y": 0.0},
                    "right_control": {"x": 0.0, "y": 0.0},
                    "id": _uid(),
                    "time_offset": t,
                    "values": [v],
                }
                for t, v in deduped
            ],
        }
    ]


def _alpha_fade_keyframes(hold, fade) -> list[dict]:
    return [
        {
            "id": _uid(),
            "material_id": "",
            "property_type": "KFTypeAlpha",
            "keyframe_list": [
                {
                    "curveType": "Line",
                    "graphID": "",
                    "left_control": {"x": 0.0, "y": 0.0},
                    "right_control": {"x": 0.0, "y": 0.0},
                    "id": _uid(),
                    "time_offset": t,
                    "values": [v],
                }
                for t, v in [(_us(hold), 1.0), (_us(hold) + _us(fade), 0.0)]
            ],
        }
    ]


def _clip_block(scale: float = 1.0, tx: float = 0.0, ty: float = 0.0) -> dict:
    return {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": scale, "y": scale},
        "transform": {"x": tx, "y": ty},
    }


def _overlay_clip_from(clip: TimelineClip, timeline: Timeline) -> dict:
    """Map the engine's framing transform onto CapCut's normalized clip
    block ((0,0) center, ±1 spans half the canvas)."""
    if clip.transform_scale is None and clip.transform_position is None:
        return _clip_block()  # center / blur-bg: default fit
    scale = clip.transform_scale[0] if clip.transform_scale else 1.0
    tx = ty = 0.0
    if clip.transform_position:
        tx = clip.transform_position[0] / (timeline.width / 2)
        # the model's +y is up (FCP convention); CapCut's canvas y is down
        ty = -clip.transform_position[1] / (timeline.height / 2)
    return _clip_block(scale=round(scale, 4), tx=round(tx, 4), ty=round(ty, 4))


def _video_material(media, mtype: str = "video") -> dict:
    return {
        "id": _uid(),
        "type": mtype,
        "path": str(media if isinstance(media, Path) else media.path),
        "duration": _us(media.duration) if not isinstance(media, Path) else 10 * US,
        "width": 0 if isinstance(media, Path) else media.width,
        "height": 0 if isinstance(media, Path) else media.height,
        "material_name": (media if isinstance(media, Path) else media.path).name,
        "has_audio": False if isinstance(media, Path) else media.has_audio,
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
    }


def _audio_material(media) -> dict:
    return {
        "id": _uid(),
        "type": "extract_music",
        "path": str(media.path),
        "duration": _us(media.duration),
        "name": media.path.name,
        "music_id": "",
        "wave_points": [],
    }


def _text_material(text: str, size: float, color: str, bold: bool) -> dict:
    content = {
        "styles": [
            {
                "fill": {"content": {"solid": {"color": color}}},
                "font": {"id": "", "path": ""},
                "range": [0, len(text)],
                "size": size,
                "bold": bold,
            }
        ],
        "text": text,
    }
    return {
        "id": _uid(),
        "type": "text",
        "content": json.dumps(content, ensure_ascii=False),
        "alignment": 1,
        "line_spacing": 0.1,
        "letter_spacing": 0.0,
        "typesetting": 0,
    }


def _segment(
    material_id: str,
    source_start,
    duration,
    target_start,
    volume: float = 1.0,
    clip: Optional[dict] = None,
    keyframes: Optional[list] = None,
    render_index: int = 0,
) -> dict:
    return {
        "id": _uid(),
        "material_id": material_id,
        "source_timerange": _timerange(source_start, duration),
        "target_timerange": _timerange(target_start, duration),
        "speed": 1.0,
        "volume": round(volume, 6),
        "visible": True,
        "clip": clip or _clip_block(),
        "common_keyframes": keyframes or [],
        "extra_material_refs": [],
        "enable_adjust": True,
        "enable_color_curves": True,
        "enable_color_wheels": True,
        "last_nonzero_volume": 1.0,
        "render_index": render_index,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
    }


def _track(track_type: str, segments: list[dict]) -> dict:
    return {
        "id": _uid(),
        "type": track_type,
        "segments": segments,
        "attribute": 0,
        "flag": 0,
    }


def export(timeline: Timeline, path: Path) -> None:
    """Write a CapCut draft folder next to the given output path."""
    draft_dir = path if path.suffix == ".capcut" else path.with_suffix(".capcut")
    draft_dir.mkdir(parents=True, exist_ok=True)

    materials_videos: list[dict] = []
    materials_audios: list[dict] = []
    materials_texts: list[dict] = []
    tracks: list[dict] = []
    render_index = 0

    # --- primary storyline ------------------------------------------------
    primary_segments = []
    for clip in timeline.primary_clips:
        material = _video_material(clip.media)
        materials_videos.append(material)
        primary_segments.append(
            _segment(
                material["id"],
                clip.source_start,
                clip.duration,
                clip.timeline_start,
                keyframes=_volume_keyframes(clip, timeline.duck_regions, 1.0),
                render_index=render_index,
            )
        )
        render_index += 1
    tracks.append(_track("video", primary_segments))

    # --- overlays: one track per lane so layers stay separate -------------
    lanes: dict[int, list[TimelineClip]] = {}
    for clip in timeline.overlay_clips:
        lanes.setdefault(clip.lane, []).append(clip)
    for lane in sorted(lanes):
        segments = []
        for clip in lanes[lane]:
            material = _video_material(clip.media)
            materials_videos.append(material)
            segments.append(
                _segment(
                    material["id"],
                    clip.source_start,
                    clip.duration,
                    clip.timeline_start,
                    clip=_overlay_clip_from(clip, timeline),
                    render_index=render_index,
                )
            )
            render_index += 1
        tracks.append(_track("video", segments))

    # --- title card ---------------------------------------------------------
    card = timeline.title_card
    if card is not None:
        bg_path = draft_dir / "title_background.png"
        write_solid_png(bg_path, timeline.width, timeline.height)
        bg_material = _video_material(bg_path, mtype="photo")
        bg_material["duration"] = _us(card.duration)
        bg_material["width"] = timeline.width
        bg_material["height"] = timeline.height
        materials_videos.append(bg_material)
        tracks.append(
            _track(
                "video",
                [
                    _segment(
                        bg_material["id"], 0, card.duration, 0,
                        keyframes=_alpha_fade_keyframes(card.hold, card.fade),
                        render_index=render_index,
                    )
                ],
            )
        )
        render_index += 1

        style = get_style(card.style)
        text = card.title.upper() if style.title_upper else card.title
        if card.description:
            desc = (
                card.description.upper() if style.desc_upper else card.description
            )
            text = f"{text}\n{desc}"
        text_material = _text_material(
            text, size=15.0, color="#000000", bold=style.title_bold
        )
        materials_texts.append(text_material)
        text_segment = _segment(
            text_material["id"], 0, card.duration, 0,
            keyframes=_alpha_fade_keyframes(card.hold, card.fade),
            render_index=render_index,
        )
        render_index += 1
        tracks.append(_track("text", [text_segment]))

    # --- background music ----------------------------------------------------
    if timeline.music_clips:
        segments = []
        material = _audio_material(timeline.music_clips[0].media)
        materials_audios.append(material)
        for clip in timeline.music_clips:
            segments.append(
                _segment(
                    material["id"],
                    clip.source_start,
                    clip.duration,
                    clip.timeline_start,
                    volume=_gain(clip.volume_db or -22.0),
                    keyframes=_volume_keyframes(
                        clip,
                        timeline.music_duck_regions,
                        _gain(clip.volume_db or -22.0),
                    ),
                )
            )
        tracks.append(_track("audio", segments))

    fps = float(timeline.frame_rate)
    draft = {
        "canvas_config": {
            "width": timeline.width,
            "height": timeline.height,
            "ratio": "original",
        },
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "maintrack_adsorb": True,
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "video_mute": False,
            "zoom_info_params": None,
        },
        "duration": _us(timeline.duration),
        "fps": fps,
        "free_render_index_mode_on": False,
        "id": _uid(),
        "keyframe_graph_list": [],
        "materials": {
            "audio_balances": [],
            "audio_effects": [],
            "audio_fades": [],
            "audios": materials_audios,
            "beats": [],
            "canvases": [],
            "chromas": [],
            "color_curves": [],
            "effects": [],
            "handwrites": [],
            "hsl": [],
            "images": [],
            "log_color_wheels": [],
            "masks": [],
            "material_animations": [],
            "placeholders": [],
            "realtime_denoises": [],
            "shapes": [],
            "smart_crops": [],
            "sound_channel_mappings": [],
            "speeds": [],
            "stickers": [],
            "tail_leaders": [],
            "text_templates": [],
            "texts": materials_texts,
            "transitions": [],
            "video_effects": [],
            "video_trackings": [],
            "videos": materials_videos,
        },
        "mutable_config": None,
        "name": timeline.name,
        "new_version": "83.0.0",
        "platform": {"app_id": 359289, "app_source": "cc", "os": "mac"},
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "tracks": tracks,
        "update_time": 0,
        "version": 360000,
    }
    (draft_dir / "draft_content.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=1)
    )
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(
            {
                "draft_id": draft["id"],
                "draft_name": timeline.name,
                "draft_root_path": str(draft_dir),
                "tm_duration": draft["duration"],
                "tm_draft_create": 0,
                "tm_draft_modified": 0,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    (draft_dir / "INSTRUCTIONS.txt").write_text(
        "EditSync -> CapCut (experimental)\n"
        "=================================\n\n"
        "1. Quit CapCut.\n"
        "2. Copy this whole folder into CapCut's drafts directory:\n"
        "   macOS:   ~/Movies/CapCut/User Data/Projects/com.lveditor.draft/\n"
        "   Windows: %LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft\\\n"
        "3. Reopen CapCut - the project appears on the home screen.\n\n"
        "Notes:\n"
        "- Keep your footage where it was when you synced; the draft\n"
        "  references it in place.\n"
        "- CapCut's draft format is undocumented and can change between\n"
        "  CapCut versions. If a project refuses to open after a CapCut\n"
        "  update, use the Finished video (MP4) option instead and edit\n"
        "  that in CapCut directly.\n"
    )
