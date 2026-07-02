"""EditSync desktop application (PySide6).

A drag-and-drop front end over the same engine the CLI uses. The GUI layer
only talks to :mod:`editsync.media`, :mod:`editsync.builder`, and
:mod:`editsync.exporters` — no sync logic lives here.
"""
