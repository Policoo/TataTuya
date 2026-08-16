"""PySide application setup and production dependency composition."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
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
    ClientSecretUnavailableError,
    DatabaseSettingsStore,
    SettingsRepository,
)
from tatatuya.infrastructure.system_timezone import load_system_timezone
from tatatuya.infrastructure.tuya.client import TuyaClient
from tatatuya.infrastructure.tuya.report_logs import TuyaReportLogGateway
from tatatuya.services.billing_service import BillingService, CalculationContext
from tatatuya.services.cloud_history_service import (
    CloudHistoryService,
    HistoricalScaleContract,
)
from tatatuya.services.device_service import DeviceService
from tatatuya.services.history_service import HistoryContext, HistoryService
from tatatuya.services.reading_service import (
    DeviceRefreshResult,
    ReadingService,
    StatusCaptureResult,
)
from tatatuya.services.settings_service import SettingsService
from tatatuya.domain.cancellation import CancellationContext, uncancelled_context
from tatatuya.ui import text
from tatatuya.ui.components.device_table import DeviceTableRow, should_show_device
from tatatuya.ui.dialogs.calculate import CalculationDialog, CloudImportPayload
from tatatuya.ui.dialogs.device_info import DeviceInfoDialog
from tatatuya.ui.dialogs.device_status import DeviceStatusDialog
from tatatuya.ui.dialogs.error import ErrorDialog
from tatatuya.ui.dialogs.history import HistoryDialog
from tatatuya.ui.dialogs.settings import REGION_LABELS, SavedSettings, SettingsDialog
from tatatuya.ui.main_window import InitialState, MainWindow
from tatatuya.ui.workers import WorkerOwner, log_unexpected_exception


@dataclass(frozen=True, slots=True)
class SettingsDialogContext:
    service: SettingsService
    settings: TuyaSettings | None
    stored_secret_available: bool


# Tuya documents report-log values as reports of the selected DP and defines a
# value DP's wire value through that DP model's unit and decimal scale. Runtime
# specification bracketing still rejects a model change during an import.
CLOUD_HISTORY_CONTRACT = HistoricalScaleContract(
    True,
    "tuya-report-log-and-dp-model-docs-2026-07-30",
)


class ShutdownCoordinator(QObject):
    """Defer the one real application quit until owned workers have drained."""

    def __init__(
        self,
        application: QApplication,
        window: MainWindow,
        workers: WorkerOwner,
    ) -> None:
        super().__init__(application)
        self.application = application
        self.window = window
        self.workers = workers
        self.pending = False
        self._issuing_quit = False
        workers.all_finished.connect(self._workers_finished)

    def request_quit(self) -> None:
        if self.pending:
            return
        self.pending = True
        self.window._close_when_idle = True
        self.window.refresh_button.setEnabled(False)
        self.window.settings_button.setEnabled(False)
        self.window.table.set_remote_enabled(False)
        self.window.status_label.setText(text.CLOSING_AFTER_WORK)
        self.workers.cancel_all()
        if not self.workers.active:
            QTimer.singleShot(0, self._finish)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.application
            and event.type() is QEvent.Type.Quit
            and not self._issuing_quit
        ):
            self.request_quit()
            return True
        return super().eventFilter(watched, event)

    def _workers_finished(self) -> None:
        if self.pending:
            QTimer.singleShot(0, self._finish)

    def _finish(self) -> None:
        if not self.pending or self.workers.active:
            return
        self.window._allow_close = True
        self.window.close()
        self._issuing_quit = True
        QTimer.singleShot(0, self.application.quit)


def load_stylesheet() -> str:
    ui_directory = Path(__file__).parent
    stylesheet = (ui_directory / "styles.qss").read_text(encoding="utf-8")
    arrow_path = (ui_directory / "icons" / "down-arrow.svg").as_posix()
    return stylesheet.replace("__TATATUYA_DOWN_ARROW__", arrow_path)


def _load_initial_state(
    database: Database, cancellation: CancellationContext | None = None
) -> InitialState:
    cancellation = cancellation or uncancelled_context()
    cancellation.checkpoint()
    database.initialize(cancellation)
    with database.connect(cancellation) as connection:
        devices = DeviceRepository(connection).list_all()
        latest = ReadingRepository(connection).latest_by_device()
    settings = _load_optional_remote_settings(database, cancellation)
    configured = settings is not None and settings.is_complete
    rows = [
        DeviceTableRow(device, latest.get(device.device_id))
        for device in devices
        if should_show_device(device, latest.get(device.device_id))
    ]
    refresh: Callable[[CancellationContext], list[DeviceRefreshResult]] | None = None
    if settings is not None and settings.is_complete:

        def refresh_workflow(context: CancellationContext):
            return _refresh_with_saved_settings(database, context)

        refresh = refresh_workflow
    return InitialState(rows, configured, refresh)


def _refresh_workflow(
    database: Database,
    settings: TuyaSettings,
    cancellation: CancellationContext | None = None,
):
    cancellation = cancellation or uncancelled_context()
    cancellation.checkpoint()
    with database.connect(cancellation) as connection:
        gateway = TuyaClient(settings, cancellation=cancellation)
        devices = DeviceRepository(connection)
        reading_store = ReadingRepository(connection)
        device_service = DeviceService(gateway, devices)
        return ReadingService(gateway, device_service, reading_store).refresh(
            cancellation
        )


def _refresh_with_saved_settings(
    database: Database,
    cancellation: CancellationContext,
):
    cancellation.checkpoint()
    with database.connect(cancellation) as connection:
        settings = SettingsRepository(
            connection, database.client_secret_store()
        ).load_tuya(cancellation)
    if settings is None or not settings.is_complete:
        raise UserFacingError(
            "Setări incomplete",
            "Configurați conexiunea Tuya înainte de actualizare.",
        )
    return _refresh_workflow(database, settings, cancellation)


def _prepare_calculation(
    database: Database,
    device_id: str,
    cancellation: CancellationContext | None = None,
) -> CalculationContext:
    cancellation = cancellation or uncancelled_context(15)
    cancellation.checkpoint()
    database.initialize(cancellation)
    with database.connect(cancellation) as connection:
        settings_repository = SettingsRepository(
            connection, database.client_secret_store()
        )
        currency = settings_repository.load_currency()
        context = BillingService(
            ReadingRepository(connection),
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
        ).prepare(device_id, currency)
    settings = _load_optional_remote_settings(database, cancellation)
    return replace(
        context,
        tuya_configured=settings is not None and settings.is_complete,
    )


def _save_calculation(
    database: Database,
    device_id: str,
    start_reading_id: int,
    end_reading_id: int,
    entered_price: str,
    currency: Currency,
    cancellation: CancellationContext | None = None,
) -> Calculation:
    cancellation = cancellation or uncancelled_context(15)
    cancellation.checkpoint()
    with database.connect(cancellation) as connection:
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


def _import_cloud_history(
    database: Database,
    device_id: str,
    cancellation: CancellationContext,
) -> CloudImportPayload:
    cancellation.checkpoint()
    with database.connect(cancellation) as connection:
        settings = SettingsRepository(
            connection, database.client_secret_store()
        ).load_tuya(cancellation)
        if settings is None or not settings.is_complete:
            raise UserFacingError(
                "Setări incomplete",
                "Configurați conexiunea Tuya înainte de importul cloud.",
            )
        device = DeviceRepository(connection).get(device_id)
        if device is None:
            raise UserFacingError(
                "Contor necunoscut",
                "Contorul selectat nu mai există în baza de date locală.",
            )
        client = TuyaClient(settings, cancellation=cancellation)
        result = CloudHistoryService(
            client,
            TuyaReportLogGateway(client),
            ReadingRepository(connection),
            CLOUD_HISTORY_CONTRACT,
        ).import_recent(
            device,
            load_system_timezone(),
            cancellation,
        )
        cancellation.checkpoint()
        context = BillingService(
            ReadingRepository(connection),
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
        ).prepare(device_id, settings.currency)
        return CloudImportPayload(context, result.new_count, result.reused_count)


def _prepare_history(
    database: Database,
    device_id: str,
    cancellation: CancellationContext | None = None,
) -> HistoryContext:
    cancellation = cancellation or uncancelled_context(15)
    cancellation.checkpoint()
    database.initialize(cancellation)
    with database.connect(cancellation) as connection:
        return HistoryService(
            ReadingRepository(connection),
            CalculationRepository(connection),
        ).prepare(device_id)


def _capture_status(
    database: Database,
    device_id: str,
    cancellation: CancellationContext | None = None,
) -> StatusCaptureResult:
    cancellation = cancellation or uncancelled_context(15)
    cancellation.checkpoint()
    database.initialize(cancellation)
    with database.connect(cancellation) as connection:
        settings = SettingsRepository(
            connection, database.client_secret_store()
        ).load_tuya(cancellation)
        if settings is None or not settings.is_complete:
            raise UserFacingError(
                "Setări incomplete",
                "Configurați conexiunea Tuya înainte de a încărca statusul.",
            )
        gateway = TuyaClient(settings, cancellation=cancellation)
        devices = DeviceRepository(connection)
        return ReadingService(
            gateway,
            DeviceService(gateway, devices),
            ReadingRepository(connection),
        ).capture_individual_status(device_id, cancellation)


def _prepare_settings(
    database: Database, cancellation: CancellationContext | None = None
) -> SettingsDialogContext:
    cancellation = cancellation or uncancelled_context(15)
    cancellation.checkpoint()
    database.initialize(cancellation)
    service = SettingsService(
        DatabaseSettingsStore(database),
        lambda settings, cancellation: TuyaClient(
            settings, cancellation=cancellation
        ),
        REGION_LABELS,
    )
    settings = service.load(cancellation)
    return SettingsDialogContext(
        service,
        None if settings is None else replace(settings, client_secret=""),
        settings is not None and bool(settings.client_secret),
    )


def _load_optional_remote_settings(
    database: Database, cancellation: CancellationContext
) -> TuyaSettings | None:
    """Load optional remote capability without blocking local-only workflows."""

    try:
        return DatabaseSettingsStore(database).load_tuya(cancellation)
    except ClientSecretUnavailableError:
        cancellation.checkpoint()
        return None


def create_main_window(database: Database | None = None) -> MainWindow:
    database = database or Database()
    application = QApplication.instance()
    owner = WorkerOwner(application if isinstance(application, QApplication) else None)
    window = MainWindow(
        bootstrap_workflow=lambda context: _load_initial_state(database, context),
        worker_owner=owner,
    )

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
                stored_secret_available=payload.stored_secret_available,
                worker_owner=owner,
            )
            dialog.error_raised.connect(lambda error: show_error(error, dialog))

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
                    lambda context: _refresh_with_saved_settings(database, context),
                    connection_verified=saved.connection_verified,
                    refresh_when_verified=True,
                )

        window.run_background_operation(
            lambda context: _prepare_settings(database, context),
            open_dialog,
            text.LOADING_SETTINGS,
        )

    def show_calculation(device) -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, CalculationContext):
                return
            remote_available = (
                payload.tuya_configured and CLOUD_HISTORY_CONTRACT.verified
            )
            cloud_workflow: Callable[..., CloudImportPayload] | None = None
            if remote_available:

                def run_cloud_workflow(context):
                    return _import_cloud_history(
                        database,
                        device.device_id,
                        context,
                    )

                cloud_workflow = run_cloud_workflow
            dialog = CalculationDialog(
                device,
                payload,
                lambda start_id, end_id, entered, context: _save_calculation(
                    database,
                    device.device_id,
                    start_id,
                    end_id,
                    entered,
                    payload.currency,
                    context,
                ),
                window,
                cloud_import_workflow=cloud_workflow,
                cloud_unavailable_text=(
                    None
                    if remote_available
                    else text.CLOUD_IMPORT_NOT_AVAILABLE
                    if payload.tuya_configured
                    else None
                ),
                cloud_settings_available=not payload.tuya_configured,
                worker_owner=owner,
            )
            dialog.error_raised.connect(lambda error: show_error(error, dialog))

            def open_cloud_settings() -> None:
                dialog.reject()
                QTimer.singleShot(0, show_settings)

            dialog.settings_requested.connect(open_cloud_settings)
            dialog.exec()

        window.run_background_operation(
            lambda context: _prepare_calculation(database, device.device_id, context),
            open_dialog,
            text.PREPARING_CALCULATION,
        )

    def show_history(device) -> None:
        def open_dialog(payload: object) -> None:
            if not isinstance(payload, HistoryContext):
                return
            HistoryDialog(device, payload, window).exec()

        window.run_background_operation(
            lambda context: _prepare_history(database, device.device_id, context),
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
            lambda context: _capture_status(database, device.device_id, context),
            open_dialog,
            text.LOADING_STATUS,
            timeout_seconds=15,
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
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(load_stylesheet())
    window = create_main_window()
    coordinator = ShutdownCoordinator(app, window, window.worker_owner)
    window.shutdown_requested = coordinator.request_quit
    app.installEventFilter(coordinator)
    window._shutdown_coordinator = coordinator
    app.aboutToQuit.connect(lambda: _assert_no_active_workers(window.worker_owner))
    install_exception_hook(window)
    window.show()
    sys.exit(app.exec())


def _assert_no_active_workers(workers: WorkerOwner) -> None:
    if workers.active:
        raise RuntimeError("QApplication quit while workflow threads were active")
