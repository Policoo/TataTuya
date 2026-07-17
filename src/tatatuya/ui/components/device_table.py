"""Main-window meter table."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from tatatuya.domain.models import Device, EnergyEligibility, Reading
from tatatuya.ui import text
from tatatuya.ui.formatters import format_energy, format_local_datetime, online_label


@dataclass(frozen=True, slots=True)
class DeviceTableRow:
    device: Device
    latest_reading: Reading | None = None
    error_message: str | None = None


def should_show_device(device: Device, latest_reading: Reading | None) -> bool:
    if latest_reading is not None:
        return True
    if device.energy_eligibility is EnergyEligibility.SUPPORTED:
        return True
    if device.energy_eligibility is EnergyEligibility.UNSUPPORTED:
        return False
    return device.present_in_tuya is True


class DeviceTable(QTableWidget):
    calculate_requested = Signal(object)
    history_requested = Signal(object)
    info_requested = Signal(object)
    status_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 5, parent)
        self.rows: list[DeviceTableRow] = []
        self.setObjectName("DeviceTable")
        self.setHorizontalHeaderLabels(
            [text.METER, text.STATE, text.CURRENT_READING, text.LAST_READING, text.ACTIONS]
        )
        self.verticalHeader().setVisible(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)

        header = self.horizontalHeader()
        # The state column stays compact; recoverable details live in its
        # tooltip. This leaves the flexible width for the Tuya-owned meter name.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)

    def set_rows(self, rows: list[DeviceTableRow]) -> None:
        self.rows = rows
        self.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            name_item = QTableWidgetItem(row.device.name)
            name_item.setToolTip(row.device.name)
            self.setItem(row_index, 0, name_item)
            state = (
                text.NOT_IN_TUYA
                if row.device.present_in_tuya is False
                else online_label(row.device.online)
            )
            state_item = QTableWidgetItem(state)
            if row.device.present_in_tuya is False:
                state_color = QColor("#8a5700")
            elif row.device.online is True:
                state_color = QColor("#157347")
            elif row.device.online is False:
                state_color = QColor("#b42318")
            else:
                state_color = QColor("#667085")
            state_item.setForeground(QBrush(state_color))
            if row.device.online is not None:
                state_font = state_item.font()
                state_font.setWeight(QFont.Weight.DemiBold)
                state_item.setFont(state_font)
            if row.error_message:
                state_item.setToolTip(row.error_message)
            self.setItem(row_index, 1, state_item)
            self.setItem(
                row_index,
                2,
                QTableWidgetItem(
                    format_energy(row.latest_reading.value_kwh)
                    if row.latest_reading
                    else text.NO_READING
                ),
            )
            self.setItem(
                row_index,
                3,
                QTableWidgetItem(
                    format_local_datetime(row.latest_reading.recorded_at_utc)
                    if row.latest_reading
                    else "—"
                ),
            )
            actions = RowActions(row.device, self)
            self.setCellWidget(row_index, 4, actions)
            actions.ensurePolished()
            separator = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
            self.setRowHeight(row_index, actions.sizeHint().height() + separator)

        if rows:
            # The stylesheet gives table cells 12 px of padding on each side.
            # Derive the action column from its styled contents plus that inset.
            actions_width = max(
                self.cellWidget(index, 4).sizeHint().width()
                for index in range(len(rows))
            )
            self.setColumnWidth(4, actions_width + 24)


class RowActions(QWidget):
    def __init__(self, device: Device, table: DeviceTable) -> None:
        super().__init__(table)
        layout = QHBoxLayout(self)
        # QTableWidget applies its own horizontal cell inset. Keeping the inner
        # layout flush prevents the last action from being compressed or clipped.
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(2)
        actions = (
            (text.CALCULATE, table.calculate_requested),
            (text.HISTORY, table.history_requested),
            (text.INFO, table.info_requested),
            (text.STATUS, table.status_requested),
        )
        for label, signal in actions:
            button = QPushButton(label)
            button.setObjectName("RowActionButton")
            button.clicked.connect(lambda checked=False, s=signal: s.emit(device))
            if label == text.STATUS and device.present_in_tuya is False:
                button.setEnabled(False)
                button.setToolTip(text.STATUS_UNAVAILABLE)
            layout.addWidget(button)
