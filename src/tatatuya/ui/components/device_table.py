"""Device table component."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from tatatuya.ui.formatters import device_id, device_name, online_label, value_from


class DeviceTable(QTableWidget):
    details_requested = Signal(dict)
    status_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 6, parent)
        self.devices: list[dict[str, Any]] = []

        self.setHorizontalHeaderLabels(["Name", "Device ID", "Category", "Product", "Status", "Actions"])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

    def set_devices(self, devices: list[dict[str, Any]]) -> None:
        self.devices = devices
        self.setRowCount(len(devices))

        for row, device in enumerate(devices):
            self._set_text(row, 0, device_name(device))
            self._set_text(row, 1, device_id(device) or "-")
            self._set_text(row, 2, value_from(device, "category"))
            self._set_text(row, 3, value_from(device, "product_name", "product_id"))
            self._set_text(row, 4, online_label(device.get("online")))
            actions = RowActions(device, self)
            self.setCellWidget(row, 5, actions)

            # QTableWidget does not reliably include cell widgets when sizing
            # rows. Use the action layout's real styled size instead of a
            # hard-coded height.
            actions.ensurePolished()
            self.setRowHeight(row, actions.sizeHint().height())

        if devices:
            self.selectRow(0)

    def _set_text(self, row: int, column: int, value: str) -> None:
        item = QTableWidgetItem(value)
        self.setItem(row, column, item)


class RowActions(QWidget):
    def __init__(self, device: dict[str, Any], table: DeviceTable) -> None:
        super().__init__(table)
        self.device = device
        self.table = table

        details_button = QPushButton("Details")
        details_button.setObjectName("GhostButton")
        details_button.clicked.connect(lambda: self.table.details_requested.emit(self.device))

        status_button = QPushButton("Status")
        status_button.setObjectName("GhostButton")
        status_button.clicked.connect(lambda: self.table.status_requested.emit(self.device))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        layout.addWidget(details_button)
        layout.addWidget(status_button)
