"""PySide application setup and production dependency composition."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Currency, TuyaSettings
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.calculations import (
    CalculationRepository,
    DevicePreferenceRepository,
)
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import (
    DatabaseSettingsStore,
    SettingsRepository,
)
from tatatuya.infrastructure.tuya.client import TuyaClient
from tatatuya.services.billing_service import BillingService, CalculationContext
from tatatuya.services.device_service import DeviceService
from tatatuya.services.history_service import HistoryContext, HistoryService
from tatatuya.services.reading_service import ReadingService, StatusCaptureResult
from tatatuya.services.settings_service import SettingsService
from tatatuya.ui import text
from tatatuya.ui.components.device_table import DeviceTableRow, should_show_device
from tatatuya.ui.dialogs.calculate import CalculationDialog
from tatatuya.ui.dialogs.device_info import DeviceInfoDialog
from tatatuya.ui.dialogs.device_status import DeviceStatusDialog
from tatatuya.ui.dialogs.error import ErrorDialog
from tatatuya.ui.dialogs.history import HistoryDialog
from tatatuya.ui.dialogs.settings import REGION_LABELS, SavedSettings, SettingsDialog
from tatatuya.ui.main_window import InitialState, MainWindow
from tatatuya.ui.workers import log_unexpected_exception


@dataclass(frozen=True, slots=True)
class SettingsDialogContext:
    service: SettingsService
    settings: TuyaSettings | None


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
    rows = [
        DeviceTableRow(device, latest.get(device.device_id))
        for device in devices
        if should_show_device(device, latest.get(device.device_id))
    ]
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


def _prepare_calculation(database: Database, device_id: str) -> CalculationContext:
    database.initialize()
    with database.connect() as connection:
        settings = SettingsRepository(connection).load_tuya()
        if settings is None:
            raise UserFacingError(
                "Setări incomplete",
                "Configurați moneda aplicației înainte de calcul.",
            )
        return BillingService(
            ReadingRepository(connection),
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
        ).prepare(device_id, settings.currency)


def _save_calculation(
    database: Database,
    device_id: str,
    start_reading_id: int,
    end_reading_id: int,
    entered_price: str,
    currency: Currency,
) -> Calculation:
    with database.connect() as connection:
        return BillingService(
            ReadingRepository(connection),
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
        ).save_calculation(
            device_id,
            start_reading_id,
            end_reading_id,
            entered_price,
            currency,
        )


def _prepare_history(database: Database, device_id: str) -> HistoryContext:
    database.initialize()
    with database.connect() as connection:
        return HistoryService(
            ReadingRepository(connection),
            CalculationRepository(connection),
        ).prepare(device_id)


def _capture_status(database: Database, device_id: str) -> StatusCaptureResult:
    database.initialize()
    with database.connect() as connection:
        settings = SettingsRepository(connection).load_tuya()
        if settings is None or not settings.is_complete:
            raise UserFacingError(
                "Setări incomplete",
                "Configurați conexiunea Tuya înainte de a încărca statusul.",
            )
        gateway = TuyaClient(settings)
        devices = DeviceRepository(connection)
        return ReadingService(
            gateway,
            DeviceService(gateway, devices),
            ReadingRepository(connection),
        ).capture_individual_status(device_id)


def _prepare_settings(database: Database) -> SettingsDialogContext:
    database.initialize()
    service = SettingsService(
        DatabaseSettingsStore(database),
        TuyaClient,
        REGION_LABELS,
    )
    return SettingsDialogContext(service, service.load())


def create_main_window(database: Database | None = None) -> MainWindow:
    database = database or Database()
    window = MainWindow(bootstrap_workflow=lambda: _load_initial_state(database))

    def show_error(error, parent=None) -> None:
        ErrorDialog(error, parent or window).exec()

    def show_settings() -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, SettingsDialogContext):
                return
            saved_result: SavedSettings | None = None
            dialog = SettingsDialog(
                payload.service,
                REGION_LABELS,
                window,
                initial_settings=payload.settings,
            )
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
            saved = saved_result
            if saved is not None:
                window.apply_settings(
                    lambda: _refresh_workflow(database, saved.settings),
                    connection_verified=saved.connection_verified,
                    refresh_when_verified=True,
                )

        window.run_background_operation(
            lambda: _prepare_settings(database),
            open_dialog,
            text.LOADING_SETTINGS,
        )

    def show_calculation(device) -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, CalculationContext):
                return
            dialog = CalculationDialog(
                device,
                payload,
                lambda start_id, end_id, entered: _save_calculation(
                    database,
                    device.device_id,
                    start_id,
                    end_id,
                    entered,
                    payload.currency,
                ),
                window,
            )
            dialog.error_raised.connect(lambda error: show_error(error, dialog))
            dialog.exec()

        window.run_background_operation(
            lambda: _prepare_calculation(database, device.device_id),
            open_dialog,
            text.PREPARING_CALCULATION,
        )

    def show_history(device) -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, HistoryContext):
                return
            HistoryDialog(device, payload, window).exec()

        window.run_background_operation(
            lambda: _prepare_history(database, device.device_id),
            open_dialog,
            text.PREPARING_HISTORY,
        )

    def show_info(device) -> None:
        DeviceInfoDialog(device, window).exec()

    def show_status(device) -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, StatusCaptureResult):
                return
            if payload.reading is not None:
                window.apply_individual_reading(device.device_id, payload.reading)
            DeviceStatusDialog(device, payload, window).exec()

        window.run_background_operation(
            lambda: _capture_status(database, device.device_id),
            open_dialog,
            text.LOADING_STATUS,
        )

    window.settings_requested.connect(show_settings)
    window.calculation_requested.connect(show_calculation)
    window.history_requested.connect(show_history)
    window.info_requested.connect(show_info)
    window.status_requested.connect(show_status)
    window.error_raised.connect(show_error)
    QTimer.singleShot(0, window.load_initial_state)
    return window


def install_exception_hook(window: MainWindow) -> None:
    """Convert uncaught Qt callback failures into the shared safe dialog."""

    def handle_exception(exception_type, error, traceback) -> None:
        if isinstance(error, UserFacingError):
            displayed = error
        else:
            error.__traceback__ = traceback
            log_unexpected_exception(error)
            displayed = UserFacingError(
                "Eroare neașteptată",
                "Operațiunea nu a putut fi finalizată. Încercați din nou.",
            )
        QTimer.singleShot(0, lambda: ErrorDialog(displayed, window).exec())

    sys.excepthook = handle_exception


def run() -> None:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("TataTuya")
    app.setStyleSheet(load_stylesheet())
    window = create_main_window()
    install_exception_hook(window)
    window.show()
    sys.exit(app.exec())
