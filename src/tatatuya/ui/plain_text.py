"""Safe QLabel helpers for remote and user-controlled presentation text."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


def plain_text_label(value: object = "") -> QLabel:
    """Create a label that never auto-detects remote input as rich text."""

    label = QLabel()
    set_plain_text(label, value)
    return label


def set_plain_text(label: QLabel, value: object) -> None:
    """Assign literal text while keeping link activation disabled."""

    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setText(str(value))
