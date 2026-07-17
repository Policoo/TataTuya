"""Raw, read-only Tuya status diagnostics."""

from __future__ import annotations

from decimal import Decimal
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tatatuya.domain.models import Device
from tatatuya.services.reading_service import StatusCaptureResult
from tatatuya.ui import text
from tatatuya.ui.formatters import format_energy


class DeviceStatusDialog(QDialog):
    def __init__(
        self,
        device: Device,
        result: StatusCaptureResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.capture_result = result
        self.setWindowTitle(text.DEVICE_STATUS_TITLE)
        self.setModal(True)
        self.resize(860, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel(text.DEVICE_STATUS_TITLE)
        title.setObjectName("ModalTitle")
        layout.addWidget(title)
        meter = QLabel(device.name)
        meter.setObjectName("HistoryMeter")
        meter.setWordWrap(True)
        layout.addWidget(meter)

        self.capture_feedback = QLabel()
        self.capture_feedback.setObjectName("StatusCaptureFeedback")
        self.capture_feedback.setWordWrap(True)
        layout.addWidget(self.capture_feedback)

        values_label = QLabel(text.STATUS_VALUES)
        values_label.setObjectName("SectionTitle")
        layout.addWidget(values_label)

        self.status_stack = QStackedWidget()
        self.status_table = QTableWidget(0, 2)
        self.status_table.setHorizontalHeaderLabels(
            [text.TUYA_CODE, text.TUYA_VALUE]
        )
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.status_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.status_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setShowGrid(False)
        header = self.status_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.status_empty = QLabel(text.NO_STATUS_VALUES)
        self.status_empty.setObjectName("HistoryEmpty")
        self.status_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_empty.setWordWrap(True)
        self.status_stack.addWidget(self.status_table)
        self.status_stack.addWidget(self.status_empty)
        layout.addWidget(self.status_stack, 1)

        raw_label = QLabel(text.RAW_TUYA_RESPONSE)
        raw_label.setObjectName("SectionTitle")
        layout.addWidget(raw_label)
        self.raw_response = QPlainTextEdit()
        self.raw_response.setObjectName("RawStatus")
        self.raw_response.setReadOnly(True)
        self.raw_response.setPlainText(pretty_json(result.status.raw_json))
        layout.addWidget(self.raw_response, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(text.CLOSE)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_status()
        self._show_capture_result()

    def _populate_status(self) -> None:
        statuses = self.capture_result.status.statuses
        self.status_table.setRowCount(len(statuses))
        for row, status in enumerate(statuses):
            self.status_table.setItem(row, 0, QTableWidgetItem(status.code))
            self.status_table.setItem(
                row, 1, QTableWidgetItem(format_status_value(status.value))
            )
        self.status_stack.setCurrentWidget(
            self.status_table if statuses else self.status_empty
        )

    def _show_capture_result(self) -> None:
        if self.capture_result.reading is not None:
            self.capture_feedback.setProperty("state", "success")
            self.capture_feedback.setText(
                text.STATUS_READING_SAVED.format(
                    reading=format_energy(self.capture_result.reading.value_kwh)
                )
            )
            return
        if self.capture_result.capture_error is not None:
            self.capture_feedback.setProperty("state", "warning")
            self.capture_feedback.setText(
                text.STATUS_READING_NOT_SAVED.format(
                    message=self.capture_result.capture_error.message
                )
            )
            return
        self.capture_feedback.setProperty("state", "neutral")
        self.capture_feedback.setText(text.STATUS_WITHOUT_READING)


def format_status_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def pretty_json(raw_json: str) -> str:
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
