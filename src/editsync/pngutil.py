"""Minimal PNG writer (stdlib only).

The title-card exporter needs a solid background image next to the
project file; generating it here avoids any imaging dependency in the
CLI and keeps the output deterministic.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_solid_png(
    path: Path, width: int, height: int, rgb: tuple[int, int, int] = (255, 255, 255)
) -> None:
    """Write a solid-color, 8-bit RGB PNG."""
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width  # filter byte 0 + pixels
    body = zlib.compress(row * height, level=9)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", body)
        + _chunk(b"IEND", b"")
    )
