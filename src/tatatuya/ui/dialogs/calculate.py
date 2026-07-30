"""Romanian dialog for previewing and saving a meter calculation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import inspect

from PySide6.QtCore import QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Device, Reading
from tatatuya.services.billing_service import BillingService, CalculationContext
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.ui import text
from tatatuya.ui.components.combo_box import PaletteSafeComboBox
from tatatuya.ui.formatters import (
    format_decimal,
    format_energy,
    format_local_date,
    format_money,
    format_reading_option,
)
from tatatuya.ui.workers import WorkerOwner, WorkflowThread


@dataclass(frozen=True, slots=True)
class CloudImportPayload:
    context: CalculationContext
    new_count: int
    reused_count: int


class CalculationDialog(QDialog):
    calculation_saved = Signal(object)
    error_raised = Signal(object)
    settings_requested = Signal()

    def __init__(
        self,
        device: Device,
        context: CalculationContext,
        save_workflow: Callable[..., Calculation],
        parent: QWidget | None = None,
        *,
        cloud_import_workflow: Callable[..., CloudImportPayload] | None = None,
        cloud_unavailable_text: str | None = None,
        cloud_settings_available: bool = False,
        worker_owner: WorkerOwner | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.context = context
        self.save_workflow = save_workflow
        self.cloud_import_workflow = cloud_import_workflow
        self.cloud_unavailable_text = cloud_unavailable_text
        self.cloud_settings_available = cloud_settings_available
        self.worker_owner = worker_owner
        self.active_thread: WorkflowThread | None = None
        self._saved_result: Calculation | None = None
        self._cloud_result: CloudImportPayload | None = None
        self._active_operation: str | None = None
        self._detached = False
        self._close_when_idle = False
        self._readings = {
            reading.id: reading for reading in context.readings if reading.id is not None
        }
        self._readings_by_date = self._group_readings_by_local_date(
            tuple(self._readings.values())
        )
        self.setWindowTitle(text.CALCULATION_TITLE)
        self.setModal(True)
        self._build_ui()
        self._populate_readings()
        self._connect_signals()
        self._update_preview()
        preferred = self.sizeHint()
        self.resize(max(720, preferred.width()), preferred.height())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)

        title = QLabel(text.CALCULATION_TITLE)
        title.setObjectName("ModalTitle")
        subtitle = QLabel(text.CALCULATION_SUBTITLE)
        subtitle.setObjectName("ModalSubtitle")
        subtitle.setWordWrap(True)
        meter = QLabel(self.device.name)
        meter.setObjectName("CalculationMeter")
        meter.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(meter)

        selection_panel = QFrame()
        selection_panel.setObjectName("FieldPanel")
        selection = QGridLayout(selection_panel)
        selection.setContentsMargins(18, 18, 18, 18)
        selection.setHorizontalSpacing(12)
        selection.setVerticalSpacing(10)
        selection.setColumnStretch(2, 1)
        self.start_date = PaletteSafeComboBox(popup_row_limit=15)
        self.start_date.setObjectName("StartDate")
        self.start_reading = PaletteSafeComboBox(popup_row_limit=15)
        self.start_reading.setObjectName("StartReading")
        self.end_date = PaletteSafeComboBox(popup_row_limit=15)
        self.end_date.setObjectName("EndDate")
        self.end_reading = PaletteSafeComboBox(popup_row_limit=15)
        self.end_reading.setObjectName("EndReading")
        self.price = QLineEdit()
        self.price.setObjectName("UnitPrice")
        self.price.setPlaceholderText(text.PRICE_EXAMPLE)
        self.date_column_label = self._field_label(text.DATE)
        self.reading_column_label = self._field_label(text.EXACT_READING)
        selection.addWidget(self.date_column_label, 0, 1)
        selection.addWidget(self.reading_column_label, 0, 2)
        selection.addWidget(self._field_label(text.PERIOD_START), 1, 0)
        selection.addWidget(self.start_date, 1, 1)
        selection.addWidget(self.start_reading, 1, 2)
        selection.addWidget(self._field_label(text.PERIOD_END), 2, 0)
        selection.addWidget(self.end_date, 2, 1)
        selection.addWidget(self.end_reading, 2, 2)
        selection.addWidget(self._field_label(text.PRICE_PER_KWH), 3, 0)
        selection.addWidget(self.price, 3, 1, 1, 2)
        layout.addWidget(selection_panel)

        self.cloud_panel = QFrame()
        self.cloud_panel.setObjectName("CloudImportCard")
        cloud_layout = QVBoxLayout(self.cloud_panel)
        cloud_layout.setContentsMargins(18, 16, 18, 16)
        cloud_layout.setSpacing(8)
        self.cloud_title = QLabel(text.CLOUD_IMPORT_TITLE)
        self.cloud_title.setObjectName("CloudImportTitle")
        self.cloud_help = QLabel(text.CLOUD_IMPORT_HELP)
        self.cloud_help.setObjectName("CloudImportHelp")
        self.cloud_help.setWordWrap(True)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.cloud_import_button = QPushButton(text.CLOUD_IMPORT_RECENT)
        self.cloud_import_button.setObjectName("SecondaryButton")
        self.cloud_import_button.clicked.connect(self.import_cloud)
        self.cloud_import_button.setVisible(self.cloud_import_workflow is not None)
        self.cloud_settings_button = QPushButton(text.CLOUD_OPEN_SETTINGS)
        self.cloud_settings_button.setObjectName("SecondaryButton")
        self.cloud_settings_button.setVisible(self.cloud_settings_available)
        self.cloud_settings_button.clicked.connect(self.settings_requested.emit)
        actions.addWidget(self.cloud_import_button)
        actions.addWidget(self.cloud_settings_button)
        actions.addStretch()
        self.cloud_feedback = QLabel()
        self.cloud_feedback.setObjectName("CloudImportStatus")
        self.cloud_feedback.setWordWrap(True)
        cloud_layout.addWidget(self.cloud_title)
        cloud_layout.addWidget(self.cloud_help)
        cloud_layout.addLayout(actions)
        cloud_layout.addWidget(self.cloud_feedback)
        layout.addWidget(self.cloud_panel)
        if self.cloud_import_workflow is not None:
            self._set_cloud_status(text.CLOUD_IMPORT_READY, "ready")
        elif self.cloud_settings_available:
            self._set_cloud_status(text.CLOUD_IMPORT_NEEDS_SETTINGS, "unavailable")
        else:
            self._set_cloud_status(
                self.cloud_unavailable_text or text.CLOUD_IMPORT_NOT_AVAILABLE,
                "unavailable",
            )

        result_panel = QFrame()
        result_panel.setObjectName("CalculationResult")
        result = QFormLayout(result_panel)
        result.setContentsMargins(18, 16, 18, 16)
        result.setHorizontalSpacing(18)
        result.setVerticalSpacing(9)
        self.start_value = self._result_label()
        self.end_value = self._result_label()
        self.consumption_value = self._result_label()
        self.currency_value = self._result_label()
        self.total_value = self._result_label("CalculationTotal")
        result.addRow(self._field_label(text.START_VALUE), self.start_value)
        result.addRow(self._field_label(text.END_VALUE), self.end_value)
        result.addRow(self._field_label(text.CONSUMPTION), self.consumption_value)
        result.addRow(self._field_label(text.CURRENCY), self.currency_value)
        result.addRow(self._field_label(text.TOTAL), self.total_value)
        layout.addWidget(result_panel)

        self.feedback = QLabel()
        self.feedback.setObjectName("CalculationFeedback")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_button = QPushButton(text.CLOSE)
        self.close_button.setObjectName("SecondaryButton")
        self.close_button.clicked.connect(self.reject)
        self.save_button = QPushButton(text.SAVE_CALCULATION)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    @staticmethod
    def _result_label(name: str = "CalculationValue") -> QLabel:
        label = QLabel("—")
        label.setObjectName(name)
        return label

    @staticmethod
    def _field_label(label_text: str) -> QLabel:
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        return label

    def _populate_readings(self) -> None:
        self.start_date.clear()
        self.end_date.clear()
        for date_key, readings in self._readings_by_date.items():
            label = format_local_date(readings[0].recorded_at_utc)
            self.start_date.addItem(label, date_key)
            self.end_date.addItem(label, date_key)
        if self.start_date.count():
            longest_date = max(
                self.start_date.itemText(index)
                for index in range(self.start_date.count())
            )
            date_width = max(
                self.start_date.sizeHint().width(),
                self.end_date.sizeHint().width(),
                self.start_date.fontMetrics().horizontalAdvance(longest_date) + 80,
            )
            self.start_date.setMinimumWidth(date_width)
            self.end_date.setMinimumWidth(date_width)

        if len(self._readings) < 2:
            self.feedback.setText(text.INSUFFICIENT_READINGS)
            self.save_button.setEnabled(False)
            return

        start_id = self.context.default_start_reading_id
        end_id = self.context.default_end_reading_id
        start = self._readings.get(start_id) if start_id is not None else None
        end = self._readings.get(end_id) if end_id is not None else None
        if start is None or end is None:
            self.feedback.setText(text.INSUFFICIENT_READINGS)
            self.save_button.setEnabled(False)
            return
        self.start_date.setCurrentIndex(
            self.start_date.findData(self._local_date_key(start.recorded_at_utc))
        )
        self.end_date.setCurrentIndex(
            self.end_date.findData(self._local_date_key(end.recorded_at_utc))
        )
        self._populate_reading_combo(
            self.start_date,
            self.start_reading,
            preferred_reading_id=self.context.default_start_reading_id,
            prefer_latest=False,
        )
        self._populate_reading_combo(
            self.end_date,
            self.end_reading,
            preferred_reading_id=self.context.default_end_reading_id,
            prefer_latest=True,
        )
        if self.context.remembered_unit_price is not None:
            remembered = format_decimal(self.context.remembered_unit_price)
            self.price.setPlaceholderText(
                text.PREVIOUS_PRICE.format(
                    price=remembered,
                    currency=self.context.currency.value,
                )
            )

    def _connect_signals(self) -> None:
        self.start_date.currentIndexChanged.connect(self._start_date_changed)
        self.start_reading.currentIndexChanged.connect(self._update_preview)
        self.end_date.currentIndexChanged.connect(self._end_date_changed)
        self.end_reading.currentIndexChanged.connect(self._update_preview)
        self.price.textChanged.connect(self._update_preview)

    @staticmethod
    def _local_date_key(recorded_at_utc: datetime) -> str:
        return recorded_at_utc.astimezone().date().isoformat()

    @classmethod
    def _group_readings_by_local_date(
        cls, readings: tuple[Reading, ...]
    ) -> dict[str, tuple[Reading, ...]]:
        grouped: dict[str, list[Reading]] = {}
        ordered = sorted(
            readings,
            key=lambda reading: (
                reading.recorded_at_utc,
                reading.id if reading.id is not None else -1,
            ),
        )
        for reading in ordered:
            grouped.setdefault(
                cls._local_date_key(reading.recorded_at_utc), []
            ).append(reading)
        return {key: tuple(values) for key, values in grouped.items()}

    def _populate_reading_combo(
        self,
        date_combo: PaletteSafeComboBox,
        reading_combo: PaletteSafeComboBox,
        *,
        preferred_reading_id: int | None,
        prefer_latest: bool,
    ) -> None:
        blocker = QSignalBlocker(reading_combo)
        reading_combo.clear()
        readings = self._readings_by_date.get(date_combo.currentData(), ())
        for reading in readings:
            if reading.id is not None:
                reading_combo.addItem(
                    format_reading_option(
                        reading.value_kwh, reading.recorded_at_utc
                    ),
                    reading.id,
                )
        preferred_index = reading_combo.findData(preferred_reading_id)
        if preferred_index >= 0:
            reading_combo.setCurrentIndex(preferred_index)
        elif prefer_latest and reading_combo.count():
            reading_combo.setCurrentIndex(reading_combo.count() - 1)
        del blocker

    def _start_date_changed(self, *_args: object) -> None:
        self._populate_reading_combo(
            self.start_date,
            self.start_reading,
            preferred_reading_id=None,
            prefer_latest=False,
        )
        self._update_preview()

    def _end_date_changed(self, *_args: object) -> None:
        self._populate_reading_combo(
            self.end_date,
            self.end_reading,
            preferred_reading_id=None,
            prefer_latest=True,
        )
        self._update_preview()

    def _selected(self, combo: PaletteSafeComboBox) -> Reading | None:
        return self._readings.get(combo.currentData())

    def _update_preview(self, *_args: object) -> None:
        start = self._selected(self.start_reading)
        end = self._selected(self.end_reading)
        if start is None or end is None:
            self.start_value.setText("—")
            self.end_value.setText("—")
            self.consumption_value.setText("—")
            self.total_value.setText("—")
            self.feedback.setText(text.INSUFFICIENT_READINGS)
            self.save_button.setEnabled(False)
            return
        self.save_button.setEnabled(self.active_thread is None)
        self.start_value.setText(format_energy(start.value_kwh))
        self.end_value.setText(format_energy(end.value_kwh))
        self.currency_value.setText(self.context.currency.value)
        self.consumption_value.setText("—")
        self.total_value.setText("—")
        self.feedback.clear()
        try:
            consumption = BillingService.consumption(start, end)
            self.consumption_value.setText(format_energy(consumption))
            preview = BillingService.preview(
                start,
                end,
                self.price.text(),
                self.context.currency,
                self.context.remembered_unit_price,
            )
        except UserFacingError as error:
            if error.title == "Preț lipsă":
                self.feedback.setText(text.PRICE_REQUIRED_FOR_TOTAL)
            else:
                self.feedback.setText(text.CALCULATION_INVALID_PREVIEW)
            return
        self.total_value.setText(format_money(preview.total, preview.currency))

    def save(self) -> None:
        if self.active_thread is not None:
            return
        start_id = self.start_reading.currentData()
        end_id = self.end_reading.currentData()
        if not isinstance(start_id, int) or not isinstance(end_id, int):
            return
        entered_price = self.price.text()
        self._set_saving(True)
        self.feedback.setText(text.SAVING_CALCULATION)
        self._active_operation = "save"
        thread = WorkflowThread(
            lambda context: self._call_save(
                start_id, end_id, entered_price, context
            ),
            timeout_seconds=15,
        )
        self.active_thread = thread
        if self.worker_owner is not None:
            self.worker_owner.track(thread)
        thread.succeeded.connect(self._save_succeeded)
        thread.failed.connect(self._save_failed)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._save_finished(thread))
        thread.start()

    def _save_succeeded(self, saved: object) -> None:
        if self._detached:
            return
        if self._active_operation == "save" and isinstance(saved, Calculation):
            self._saved_result = saved
        elif self._active_operation == "cloud" and isinstance(saved, CloudImportPayload):
            self._cloud_result = saved

    def _save_failed(self, error: UserFacingError) -> None:
        if self._detached:
            return
        if self._active_operation == "cloud":
            self._cloud_result = None
            self._set_cloud_status(_cloud_failure_text(error), "error")
        self.error_raised.emit(error)

    def _save_finished(self, thread: WorkflowThread) -> None:
        if self.active_thread is thread:
            self.active_thread = None
        if self._detached:
            return
        self._set_saving(False)
        operation = self._active_operation
        self._active_operation = None
        if operation == "cloud" and self._cloud_result is not None:
            result = self._cloud_result
            self._cloud_result = None
            self.context = result.context
            self._readings = {
                reading.id: reading
                for reading in self.context.readings
                if reading.id is not None
            }
            self._readings_by_date = self._group_readings_by_local_date(
                tuple(self._readings.values())
            )
            self._populate_readings()
            if result.new_count == 0 and result.reused_count == 0:
                self._set_cloud_status(text.CLOUD_IMPORT_EMPTY, "empty")
            else:
                self._set_cloud_status(
                    text.CLOUD_IMPORT_RESULT.format(
                        new=result.new_count, existing=result.reused_count
                    ),
                    "success",
                )
        self._update_preview()
        if self._saved_result is not None:
            saved = self._saved_result
            self._saved_result = None
            self.calculation_saved.emit(saved)
            self.accept()
        elif self._close_when_idle:
            QTimer.singleShot(0, self.reject)

    def _set_saving(self, saving: bool) -> None:
        self.start_date.setEnabled(not saving)
        self.start_reading.setEnabled(not saving)
        self.end_date.setEnabled(not saving)
        self.end_reading.setEnabled(not saving)
        self.price.setEnabled(not saving)
        self.save_button.setEnabled(not saving)
        self.cloud_import_button.setEnabled(
            not saving and self.cloud_import_workflow is not None
        )
        self.cloud_settings_button.setEnabled(not saving)

    def import_cloud(self) -> None:
        if self.active_thread is not None or self.cloud_import_workflow is None:
            return
        workflow = self.cloud_import_workflow
        self._set_saving(True)
        self._active_operation = "cloud"
        self._cloud_result = None
        self._set_cloud_status(text.CLOUD_IMPORTING, "loading")
        thread = WorkflowThread(
            lambda context: workflow(context),
            timeout_seconds=30,
        )
        self.active_thread = thread
        if self.worker_owner is not None:
            self.worker_owner.track(thread)
        thread.succeeded.connect(self._save_succeeded)
        thread.failed.connect(self._save_failed)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._save_finished(thread))
        thread.start()

    def _set_cloud_status(self, message: str, state: str) -> None:
        self.cloud_feedback.setText(message)
        self.cloud_feedback.setProperty("state", state)
        self.cloud_feedback.style().unpolish(self.cloud_feedback)
        self.cloud_feedback.style().polish(self.cloud_feedback)

    def _call_save(
        self,
        start_id: int,
        end_id: int,
        entered_price: str,
        context: CancellationContext,
    ) -> Calculation:
        parameters = inspect.signature(self.save_workflow).parameters
        if len(parameters) >= 4:
            return self.save_workflow(start_id, end_id, entered_price, context)
        return self.save_workflow(start_id, end_id, entered_price)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self.active_thread is not None:
            if self._active_operation == "cloud" and self.worker_owner is not None:
                self._detached = True
                self.active_thread.requestInterruption()
                event.accept()
                return
            self._close_when_idle = True
            self.active_thread.requestInterruption()
            event.ignore()
            return
        event.accept()

    def reject(self) -> None:
        if self.active_thread is not None:
            if self._active_operation == "cloud" and self.worker_owner is not None:
                self._detached = True
                self.active_thread.requestInterruption()
                super().reject()
                return
            self._close_when_idle = True
            self.active_thread.requestInterruption()
            return
        super().reject()


def _cloud_failure_text(error: UserFacingError) -> str:
    if error.title == "Tuya este ocupat":
        return text.CLOUD_IMPORT_RATE_LIMITED
    if error.title == "Prea multe date Tuya":
        return text.CLOUD_IMPORT_RESPONSE_LIMIT
    if error.title == "Istoric Tuya indisponibil":
        return text.CLOUD_IMPORT_PERMISSION_UNAVAILABLE
    if error.title == "Operațiune anulată":
        return text.CLOUD_IMPORT_CANCELLED
    return text.CLOUD_IMPORT_FAILED
