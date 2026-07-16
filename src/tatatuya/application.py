"""Application entry point kept compatible with the prototype UI."""

from __future__ import annotations


def run() -> None:
    # The Qt presentation is migrated in a later implementation phase. Keeping
    # this import lazy lets domain and persistence tools run without importing Qt.
    from gui.app import run as run_legacy_ui

    run_legacy_ui()

