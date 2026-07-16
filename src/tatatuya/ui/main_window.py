"""Main application window."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from tatatuya.infrastructure import tuya_legacy
from tatatuya.ui.components.device_table import DeviceTable
from tatatuya.ui.components.modal import AppModal
from tatatuya.ui.formatters import (
    device_id,
    device_name,
    extract_devices,
    flatten_status,
    important_device_fields,
)
from tatatuya.ui.workers import ApiWorker
from tatatuya.infrastructure.tuya_legacy import TuyaClient


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.client = TuyaClient()
        self.active_threads: list[QThread] = []
        self.active_workers: list[ApiWorker] = []

        self.setWindowTitle("Tata Tuya")
        self.resize(1080, 680)
        self._build_ui()
        self.refresh_devices()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(26, 24, 26, 26)
        root_layout.setSpacing(18)

        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title = QLabel("Smart meters")
        title.setObjectName("Title")
        subtitle = QLabel(f"{tuya_legacy.REGION} | {self.client.base_url}")
        subtitle.setObjectName("Subtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_devices)

        self.settings_button = QPushButton("Settings")
        settings_icon = self.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogDetailedView
        )
        self.settings_button.setIcon(settings_icon)
        self.settings_button.clicked.connect(self.open_settings)

        header.addLayout(title_stack)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(self.settings_button)
        root_layout.addLayout(header)

        self.summary = QFrame()
        self.summary.setObjectName("SummaryBar")
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        self.status_label = QLabel("Starting")
        self.status_label.setObjectName("SummaryPrimary")
        self.count_label = QLabel("0 devices")
        self.count_label.setObjectName("SummarySecondary")
        summary_layout.addWidget(self.status_label)
        summary_layout.addStretch()
        summary_layout.addWidget(self.count_label)
        root_layout.addWidget(self.summary)

        table_panel = QFrame()
        table_panel.setObjectName("Panel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = DeviceTable()
        self.table.details_requested.connect(self.open_details)
        self.table.status_requested.connect(self.open_status)
        table_layout.addWidget(self.table)
        root_layout.addWidget(table_panel, 1)

        self.setCentralWidget(root)

    def refresh_devices(self) -> None:
        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading devices")
        self.count_label.setText("Refreshing")
        self.table.set_devices([])

        def call() -> dict[str, Any]:
            self.client.get_access_token()
            return self.client.list_devices()

        self._run_worker(call, self._devices_loaded, self._devices_failed)

    def _devices_loaded(self, payload: object) -> None:
        devices = extract_devices(payload if isinstance(payload, dict) else {})
        self.table.set_devices(devices)
        self.status_label.setText("Devices loaded")
        self.count_label.setText(f"{len(devices)} device{'s' if len(devices) != 1 else ''}")
        self.refresh_button.setEnabled(True)

    def _devices_failed(
        self,
        message: str,
        request_info: dict[str, Any],
        response_payload: object,
    ) -> None:
        self.status_label.setText("Could not load devices")
        self.count_label.setText("Error")
        self.refresh_button.setEnabled(True)
        modal = AppModal("Tuya request failed", "Device loading failed.", self)
        modal.add_error_details(message, request_info, response_payload)
        modal.exec()

    def open_details(self, device: dict[str, Any]) -> None:
        modal = AppModal(device_name(device), "Device information from Tuya.", self)
        modal.add_field_grid(important_device_fields(device))

        hidden_fields = sorted(
            (key, value)
            for key, value in device.items()
            if key
            not in {
                "name",
                "custom_name",
                "product_name",
                "id",
                "device_id",
                "category",
                "online",
                "time_zone",
                "owner_id",
                "uid",
                "local_key",
            }
        )
        if hidden_fields:
            modal.add_field_grid([(format_key(key), value) for key, value in hidden_fields])
        modal.exec()

    def open_status(self, device: dict[str, Any]) -> None:
        did = device_id(device)
        if not did:
            modal = AppModal("Status unavailable", "This device has no usable device ID.", self)
            modal.add_field_grid(important_device_fields(device))
            modal.exec()
            return

        modal = AppModal(f"{device_name(device)} status", "Loading live status from Tuya.", self)
        modal.add_message("Fetching current status...")

        def call() -> dict[str, Any]:
            return self.client.get_device_status(did)

        def loaded(payload: object) -> None:
            modal.clear_content()
            rows = flatten_status(payload if isinstance(payload, dict) else {"result": payload})
            modal.add_field_grid(rows or [("Status", "No status fields returned")])

        def failed(message: str, request_info: dict[str, Any], response_payload: object) -> None:
            modal.clear_content()
            modal.add_error_details(message, request_info, response_payload)

        self._run_worker(call, loaded, failed)
        modal.exec()

    def open_settings(self) -> None:
        modal = AppModal("Settings", "Application settings will live here.", self)
        modal.add_message("No settings yet.")
        modal.exec()

    def _run_worker(self, call, success_handler, failure_handler) -> None:
        thread = QThread()
        worker = ApiWorker(call)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.succeeded.connect(success_handler)
        worker.failed.connect(failure_handler)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._forget_worker(worker))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))

        self.active_threads.append(thread)
        self.active_workers.append(worker)
        thread.start()

    def _forget_thread(self, thread: QThread) -> None:
        if thread in self.active_threads:
            self.active_threads.remove(thread)

    def _forget_worker(self, worker: ApiWorker) -> None:
        if worker in self.active_workers:
            self.active_workers.remove(worker)


def format_key(key: str) -> str:
    return key.replace("_", " ").strip().title()
