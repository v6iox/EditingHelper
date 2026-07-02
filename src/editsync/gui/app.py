"""EditSync desktop app entry point (`editsync-app`)."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .style import STYLESHEET
from .window import ICON_PATH, MainWindow


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("EditSync")
    app.setApplicationDisplayName("EditSync")
    app.setOrganizationName("86 Auto Lab")
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.resize(860, 920)
    window.show()
    window.check_dependencies()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
