"""EditSync desktop app entry point (`editsync-app`)."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .style import STYLESHEET
from .window import MainWindow


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("EditSync")
    app.setApplicationDisplayName("EditSync")
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.resize(860, 920)
    window.show()
    window.check_dependencies()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
