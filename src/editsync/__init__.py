"""editsync — audio-sync multi-camera footage into NLE timelines.

The core pipeline is editor-agnostic:

    media files -> probe/classify -> audio sync -> Timeline model -> exporter

Exporters live in :mod:`editsync.exporters`; adding support for a new
editing application means writing one new exporter against the Timeline
model, without touching the sync engine.
"""

__version__ = "1.2.0"
