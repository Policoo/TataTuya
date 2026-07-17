"""Application entry point kept compatible with the prototype UI."""

from __future__ import annotations

import sys


def run() -> None:
    # Used by packaging tests to exercise the installed entry point without
    # opening a window or making a Tuya request.
    if "--smoke-test" in sys.argv:
        from PySide6.QtWidgets import QApplication

        from tatatuya.infrastructure.database import Database
        from tatatuya.ui.app import load_stylesheet

        stylesheet = load_stylesheet()
        if not stylesheet.strip():
            raise RuntimeError("The packaged stylesheet is empty")
        existing = QApplication.instance()
        app = existing if isinstance(existing, QApplication) else QApplication([])
        app.setStyleSheet(stylesheet)
        app.processEvents()
        Database().initialize()
        app.quit()
        return

    from tatatuya.infrastructure.logging_setup import configure_logging

    configure_logging()

    # Keeping this import lazy lets domain and persistence tools run without Qt.
    from tatatuya.ui.app import run as run_legacy_ui

    run_legacy_ui()
