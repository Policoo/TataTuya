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
from tatatuya.services.settings_service import SettingsService
from tatatuya.ui.components.device_table import DeviceTableRow
from tatatuya.ui.components.modal import AppModal
from tatatuya.ui.dialogs.settings import REGION_LABELS, SavedSettings, SettingsDialog
from tatatuya.ui.main_window import InitialState, MainWindow


def load_stylesheet() -> str:
    ui_directory = Path(__file__).parent
    stylesheet = (ui_directory / "styles.qss").read_text(encoding="utf-8")
    arrow_path = (ui_directory / "icons" / "down-arrow.svg").as_posix()
    return stylesheet.replace("__TATATUYA_DOWN_ARROW__", arrow_path)


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

    def show_error(error, parent=None) -> None:
        modal = AppModal(error.title, error.message, parent or window)
        if error.technical_details:
            modal.add_field_grid([("Detalii tehnice", error.technical_details)])
        modal.exec()

    def show_settings() -> None:
        database.initialize()
        saved_result: SavedSettings | None = None
        with database.connect() as connection:
            service = SettingsService(
                SettingsRepository(connection),
                TuyaClient,
                REGION_LABELS,
            )
            dialog = SettingsDialog(service, REGION_LABELS, window)
            dialog.error_raised.connect(
                lambda error: show_error(error, dialog)
            )

            def remember_saved_settings(result: object) -> None:
                nonlocal saved_result
                if not isinstance(result, SavedSettings):
                    return
                saved_result = result

            dialog.settings_saved.connect(remember_saved_settings)
            dialog.exec()
        if saved_result is not None:
            window.apply_settings(
                lambda: _refresh_workflow(database, saved_result.settings),
                connection_verified=saved_result.connection_verified,
                refresh_when_verified=True,
            )

    window.settings_requested.connect(show_settings)
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
