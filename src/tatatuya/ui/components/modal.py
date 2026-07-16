"""Reusable modal dialog."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AppModal(QDialog):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(680, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)

        title_label = QLabel(title)
        title_label.setObjectName("ModalTitle")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("ModalSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_field_grid(self, rows: list[tuple[str, Any]]) -> None:
        grid_host = QFrame()
        grid_host.setObjectName("FieldPanel")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        for row, (label, value) in enumerate(rows):
            label_widget = QLabel(label)
            label_widget.setObjectName("FieldLabel")
            value_widget = QLabel(format_value(value))
            value_widget.setObjectName("FieldValue")
            value_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_widget.setWordWrap(True)
            grid.addWidget(label_widget, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_widget, row, 1)

        grid.setColumnStretch(1, 1)
        self.content_layout.addWidget(grid_host)

    def clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def add_message(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("ModalMessage")
        label.setWordWrap(True)
        self.content_layout.addWidget(label)

    def add_error_details(
        self,
        message: str,
        request_info: dict[str, Any],
        response_payload: object,
    ) -> None:
        self.add_message(message)
        if request_info:
            self.add_field_grid([("Method", request_info.get("method")), ("URL", request_info.get("url"))])
        if response_payload is not None:
            self.add_field_grid([("Tuya response", response_payload)])


def format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return "-"
    return str(value)
