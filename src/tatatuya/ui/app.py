"""PySide application setup and production dependency composition."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import SettingsRepository
from tatatuya.infrastructure.tuya.client import TuyaClient
from tatatuya.services.device_service import DeviceService
from tatatuya.services.reading_service import ReadingService
from tatatuya.ui.components.device_table import DeviceTableRow
from tatatuya.ui.components.modal import AppModal
from tatatuya.ui.main_window import InitialState, MainWindow


def load_stylesheet() -> str:
    return Path(__file__).with_name("styles.qss").read_text(encoding="utf-8")


def _load_initial_state(database: Database) -> InitialState:
    database.initialize()
    with database.connect() as connection:
        settings = SettingsRepository(connection).load_tuya()
        devices = DeviceRepository(connection).list_all()
        latest = ReadingRepository(connection).latest_by_device()
    configured = settings is not None and settings.is_complete
    rows = [DeviceTableRow(device, latest.get(device.device_id)) for device in devices]
    refresh = (
        (lambda: _refresh_workflow(database, settings)) if configured else None
    )
    return InitialState(rows, configured, refresh)


def _refresh_workflow(database: Database, settings):
    with database.connect() as connection:
        gateway = TuyaClient(settings)
        devices = DeviceRepository(connection)
        reading_store = ReadingRepository(connection)
        device_service = DeviceService(gateway, devices)
        return ReadingService(gateway, device_service, reading_store).refresh()


def create_main_window(database: Database | None = None) -> MainWindow:
    database = database or Database()
    window = MainWindow(bootstrap_workflow=lambda: _load_initial_state(database))

    def show_settings_placeholder() -> None:
        modal = AppModal(
            "Setări",
            "Configurarea acreditărilor Tuya va fi implementată în etapa următoare.",
            window,
        )
        modal.add_message(
            "Interfața principală este pregătită să folosească setările salvate local."
        )
        modal.exec()

    def show_error(error) -> None:
        modal = AppModal(error.title, error.message, window)
        if error.technical_details:
            modal.add_field_grid([("Detalii tehnice", error.technical_details)])
        modal.exec()

    window.settings_requested.connect(show_settings_placeholder)
    window.error_raised.connect(show_error)
    QTimer.singleShot(0, window.load_initial_state)
    return window


def run() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("TataTuya")
    app.setStyleSheet(load_stylesheet())
    window = create_main_window()
    window.show()
    sys.exit(app.exec())
