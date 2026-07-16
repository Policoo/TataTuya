"""PySide application setup."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from tatatuya.ui.main_window import MainWindow


def load_stylesheet() -> str:
    stylesheet_path = Path(__file__).with_name("styles.qss")
    return stylesheet_path.read_text(encoding="utf-8")


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Tata Tuya")
    app.setStyleSheet(load_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
