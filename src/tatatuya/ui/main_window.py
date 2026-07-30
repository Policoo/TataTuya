"""Romanian application shell and main meter table."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Reading
from tatatuya.services.reading_service import DeviceRefreshResult
from tatatuya.ui import text
from tatatuya.ui.components.device_table import (
    DeviceTable,
    DeviceTableRow,
    should_show_device,
)
from tatatuya.ui.workers import WorkerOwner, WorkflowThread


@dataclass(frozen=True, slots=True)
class InitialState:
    rows: list[DeviceTableRow]
    settings_configured: bool
    refresh_workflow: Callable[..., list[DeviceRefreshResult]] | None


class MainWindow(QMainWindow):
    settings_requested = Signal()
    calculation_requested = Signal(object)
    history_requested = Signal(object)
    info_requested = Signal(object)
    status_requested = Signal(object)
    error_raised = Signal(object)

    def __init__(
        self,
        refresh_workflow: Callable[..., list[DeviceRefreshResult]] | None = None,
        *,
        bootstrap_workflow: Callable[..., InitialState] | None = None,
        cached_rows: list[DeviceTableRow] | None = None,
        settings_configured: bool = False,
        worker_owner: WorkerOwner | None = None,
    ) -> None:
        super().__init__()
        self.refresh_workflow = refresh_workflow
        self.bootstrap_workflow = bootstrap_workflow
        self.settings_configured = settings_configured
        self.worker_owner = worker_owner or WorkerOwner(self)
        self.shutdown_requested: Callable[[], None] | None = None
        self._allow_close = False
        self._shutdown_coordinator: object | None = None
        self.active_threads: list[WorkflowThread] = []
        self._close_when_idle = False
        self._refresh_when_idle = False
        self.setWindowTitle(text.APP_NAME)
        self.resize(1180, 680)
        self._build_ui()
        self.set_rows(cached_rows or [])
        if bootstrap_workflow is not None:
            self.content.setCurrentWidget(self.loading_state)
            self.refresh_button.setEnabled(False)
            self.settings_button.setEnabled(False)
            self.status_label.setText(text.LOADING_LOCAL_DATA)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 24, 26, 26)
        layout.setSpacing(18)

        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title = QLabel(text.APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel("Citiri și costuri pentru contoarele Tuya")
        subtitle.setObjectName("Subtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.refresh_button = QPushButton(text.REFRESH)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.settings_button = QPushButton(text.SETTINGS)
        self.settings_button.setObjectName("SecondaryButton")
        self.settings_button.clicked.connect(self.settings_requested)
        header.addWidget(self.refresh_button)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        summary = QFrame()
        summary.setObjectName("SummaryBar")
        summary_layout = QHBoxLayout(summary)
        self.status_label = QLabel(text.READY)
        self.status_label.setObjectName("SummaryPrimary")
        self.count_label = QLabel(text.METERS_COUNT.format(count=0))
        self.count_label.setObjectName("SummarySecondary")
        summary_layout.addWidget(self.status_label)
        summary_layout.addStretch()
        summary_layout.addWidget(self.count_label)
        layout.addWidget(summary)

        self.settings_warning = QFrame()
        self.settings_warning.setObjectName("SettingsWarning")
        warning_layout = QHBoxLayout(self.settings_warning)
        self.settings_warning_label = QLabel(text.TUYA_NOT_CONFIGURED_WARNING)
        self.settings_warning_label.setWordWrap(True)
        warning_layout.addWidget(self.settings_warning_label, 1)
        warning_button = QPushButton(text.OPEN_SETTINGS)
        warning_button.setObjectName("SecondaryButton")
        warning_button.clicked.connect(self.settings_requested)
        warning_layout.addWidget(warning_button)
        layout.addWidget(self.settings_warning)

        self.content = QStackedWidget()
        self.table = DeviceTable()
        self.table.calculate_requested.connect(self.calculation_requested)
        self.table.history_requested.connect(self.history_requested)
        self.table.info_requested.connect(self.info_requested)
        self.table.status_requested.connect(self.status_requested)
        self.content.addWidget(self.table)
        self.loading_state = self._state_panel(
            text.LOADING_LOCAL_TITLE,
            text.LOADING_LOCAL_HELP,
        )
        self.content.addWidget(self.loading_state)
        self.local_data_error_state = self._state_panel(
            text.LOCAL_DATA_ERROR_TITLE,
            text.LOCAL_DATA_ERROR_HELP,
            action=(text.RETRY, self.load_initial_state),
        )
        self.content.addWidget(self.local_data_error_state)
        self.empty_state = self._state_panel(text.NO_METERS, text.NO_METERS_HELP)
        self.content.addWidget(self.empty_state)
        self.settings_state = self._state_panel(
            text.SETTINGS_REQUIRED,
            text.SETTINGS_REQUIRED_HELP,
            action=(text.OPEN_SETTINGS, self.settings_requested.emit),
        )
        self.content.addWidget(self.settings_state)
        layout.addWidget(self.content, 1)
        self.setCentralWidget(root)

    def _state_panel(
        self,
        title: str,
        message: str,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> QWidget:
        panel = QFrame()
        panel.setObjectName("EmptyState")
        box = QVBoxLayout(panel)
        box.setContentsMargins(32, 32, 32, 32)
        box.addStretch()
        heading = QLabel(title)
        heading.setObjectName("EmptyTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setWordWrap(True)
        heading.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail = QLabel(message)
        detail.setObjectName("EmptyMessage")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box.addWidget(heading)
        box.addWidget(detail)
        if action:
            button = QPushButton(action[0])
            button.clicked.connect(action[1])
            box.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addStretch()
        return panel

    def load_initial_state(self) -> None:
        if self.bootstrap_workflow is None or self.active_threads:
            return
        self.content.setCurrentWidget(self.loading_state)
        self.status_label.setText(text.LOADING_LOCAL_DATA)
        self.refresh_button.setEnabled(False)
        self._run_worker(
            self.bootstrap_workflow,
            self._bootstrap_succeeded,
            self._bootstrap_failed,
            timeout_seconds=120,
        )

    def _bootstrap_succeeded(self, payload: object) -> None:
        if not isinstance(payload, InitialState):
            self._bootstrap_failed(
                UserFacingError(
                    "Date locale indisponibile",
                    "Datele salvate nu au putut fi încărcate.",
                )
            )
            return
        self.settings_configured = payload.settings_configured
        self.refresh_workflow = payload.refresh_workflow
        self.bootstrap_workflow = None
        self.set_rows(payload.rows)
        self.status_label.setText(text.READY)
        if payload.settings_configured and payload.refresh_workflow is not None:
            self._schedule_refresh()

    def _bootstrap_failed(self, error: UserFacingError) -> None:
        self.content.setCurrentWidget(self.local_data_error_state)
        self.status_label.setText(text.LOCAL_DATA_FAILED)
        self.refresh_button.setEnabled(False)
        self.error_raised.emit(error)

    def set_rows(self, rows: list[DeviceTableRow]) -> None:
        self.table.set_rows(rows)
        remote_enabled = self.settings_configured and not self.active_threads
        self.table.set_remote_enabled(remote_enabled)
        self.refresh_button.setEnabled(remote_enabled)
        count = len(rows)
        self.count_label.setText(
            text.ONE_METER if count == 1 else text.METERS_COUNT.format(count=count)
        )
        self.settings_warning.setVisible(bool(rows) and not self.settings_configured)
        if rows:
            self.content.setCurrentWidget(self.table)
        elif not self.settings_configured:
            self.content.setCurrentWidget(self.settings_state)
        else:
            self.content.setCurrentWidget(self.empty_state)

    def refresh_devices(self) -> None:
        if not self.settings_configured or self.refresh_workflow is None:
            self.content.setCurrentWidget(self.settings_state)
            return
        if self.active_threads:
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText(text.REFRESHING)
        self._run_worker(
            self.refresh_workflow,
            self._refresh_succeeded,
            self._operation_failed,
            timeout_seconds=120,
        )

    def apply_settings(
        self,
        refresh_workflow: Callable[..., list[DeviceRefreshResult]],
        *,
        connection_verified: bool = False,
        refresh_when_verified: bool = False,
    ) -> None:
        """Enable configured behavior immediately after settings are saved."""
        self.settings_configured = True
        self.refresh_workflow = refresh_workflow
        self.set_rows(list(self.table.rows))
        self.status_label.setText(
            text.SETTINGS_SAVED_VERIFIED
            if connection_verified
            else text.SETTINGS_SAVED_UNVERIFIED
        )
        if connection_verified and refresh_when_verified:
            self._schedule_refresh()

    def apply_individual_reading(self, device_id: str, reading: Reading) -> None:
        """Display a reading captured by the individual Status workflow."""
        rows = [
            DeviceTableRow(row.device, reading, None)
            if row.device.device_id == device_id
            else row
            for row in self.table.rows
        ]
        self.set_rows(rows)

    def run_background_operation(
        self,
        call: Callable[..., object],
        success_handler: Callable[[object], None],
        working_status: str,
        *,
        timeout_seconds: float = 15,
    ) -> None:
        """Run a non-refresh workflow without blocking the Qt event loop."""
        if self.active_threads:
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText(working_status)

        def succeeded(payload: object) -> None:
            self.status_label.setText(text.READY)
            QTimer.singleShot(0, lambda: success_handler(payload))

        def failed(error: UserFacingError) -> None:
            self.status_label.setText(text.READY)
            self.error_raised.emit(error)

        self._run_worker(
            call, succeeded, failed, timeout_seconds=timeout_seconds
        )

    def _schedule_refresh(self) -> None:
        if self.active_threads:
            self._refresh_when_idle = True
            return
        QTimer.singleShot(0, self.refresh_devices)

    def _refresh_succeeded(self, payload: object) -> None:
        results = list(payload) if isinstance(payload, list) else []
        rows = [
            DeviceTableRow(
                result.device,
                result.latest_reading,
                result.error.message if result.error else None,
            )
            for result in results
            if isinstance(result, DeviceRefreshResult)
            and should_show_device(result.device, result.latest_reading)
        ]
        self.set_rows(rows)
        failures = sum(row.error_message is not None for row in rows)
        self.status_label.setText(
            text.REFRESH_PARTIAL if failures else text.REFRESH_COMPLETE
        )

    def _operation_failed(self, error: UserFacingError) -> None:
        self.status_label.setText(text.REFRESH_FAILED)
        self.error_raised.emit(error)

    def _run_worker(
        self,
        call: Callable[..., object],
        success_handler: Callable[[object], None],
        failure_handler: Callable[[UserFacingError], None],
        *,
        timeout_seconds: float = 15,
    ) -> None:
        self.settings_button.setEnabled(False)
        self.table.set_remote_enabled(False)
        # The worker lifetime is tracked explicitly in active_threads. Keeping
        # it parentless avoids a deferred-delete race if the window closes just
        # after a finished worker has scheduled deleteLater().
        thread = WorkflowThread(call, timeout_seconds=timeout_seconds)
        self.worker_owner.track(thread)
        thread.succeeded.connect(success_handler)
        thread.failed.connect(failure_handler)
        thread.finished.connect(self._operation_finished)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._worker_finished(thread))
        self.active_threads.append(thread)
        thread.start()

    def _operation_finished(self) -> None:
        self.refresh_button.setEnabled(
            self.settings_configured
            and self.bootstrap_workflow is None
            and not self._close_when_idle
        )
        self.settings_button.setEnabled(not self._close_when_idle)
        self.table.set_remote_enabled(
            self.settings_configured and not self._close_when_idle
        )

    def _worker_finished(self, thread: WorkflowThread) -> None:
        if thread in self.active_threads:
            self.active_threads.remove(thread)
        if self._close_when_idle and not self.active_threads:
            QTimer.singleShot(0, self.close)
        elif self._refresh_when_idle and not self.active_threads:
            self._refresh_when_idle = False
            QTimer.singleShot(0, self.refresh_devices)

    def shutdown_workers(self) -> None:
        """Request cooperative cancellation without blocking the Qt thread."""

        self.worker_owner.cancel_all()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._allow_close:
            event.accept()
            return
        if self.shutdown_requested is not None:
            event.ignore()
            self.shutdown_requested()
            return
        if self.active_threads:
            self._close_when_idle = True
            self.refresh_button.setEnabled(False)
            self.settings_button.setEnabled(False)
            self.status_label.setText(text.CLOSING_AFTER_WORK)
            self.shutdown_workers()
            event.ignore()
            return
        event.accept()
