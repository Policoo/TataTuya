"""Romanian dialog for previewing and saving a meter calculation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
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
from tatatuya.ui import text
from tatatuya.ui.components.combo_box import PaletteSafeComboBox
from tatatuya.ui.formatters import (
    format_decimal,
    format_energy,
    format_reading_option,
)
from tatatuya.ui.workers import WorkflowThread


class CalculationDialog(QDialog):
    calculation_saved = Signal(object)
    error_raised = Signal(object)

    def __init__(
        self,
        device: Device,
        context: CalculationContext,
        save_workflow: Callable[[int, int, str], Calculation],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.context = context
        self.save_workflow = save_workflow
        self.active_thread: WorkflowThread | None = None
        self._saved_result: Calculation | None = None
        self._close_when_idle = False
        self._readings = {
            reading.id: reading for reading in context.readings if reading.id is not None
        }
        self.setWindowTitle(text.CALCULATION_TITLE)
        self.setModal(True)
        self.resize(720, 570)
        self._build_ui()
        self._populate_readings()
        self._update_preview()
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown_worker)

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
        form = QFormLayout(selection_panel)
        form.setContentsMargins(18, 18, 18, 18)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.start_reading = PaletteSafeComboBox()
        self.start_reading.setObjectName("StartReading")
        self.end_reading = PaletteSafeComboBox()
        self.end_reading.setObjectName("EndReading")
        self.price = QLineEdit()
        self.price.setObjectName("UnitPrice")
        self.price.setPlaceholderText(text.PRICE_EXAMPLE)
        form.addRow(self._field_label(text.START_READING), self.start_reading)
        form.addRow(self._field_label(text.END_READING), self.end_reading)
        form.addRow(self._field_label(text.PRICE_PER_KWH), self.price)
        layout.addWidget(selection_panel)

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
        close = QPushButton(text.CLOSE)
        close.setObjectName("SecondaryButton")
        close.clicked.connect(self.reject)
        self.save_button = QPushButton(text.SAVE_CALCULATION)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(close)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.start_reading.currentIndexChanged.connect(self._update_preview)
        self.end_reading.currentIndexChanged.connect(self._update_preview)
        self.price.textChanged.connect(self._update_preview)

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
        for reading in self.context.readings:
            if reading.id is None:
                continue
            option = format_reading_option(reading.value_kwh, reading.recorded_at_utc)
            self.start_reading.addItem(option, reading.id)
            self.end_reading.addItem(option, reading.id)
        self.start_reading.setCurrentIndex(
            self.start_reading.findData(self.context.default_start_reading_id)
        )
        self.end_reading.setCurrentIndex(
            self.end_reading.findData(self.context.default_end_reading_id)
        )
        if self.context.remembered_unit_price is not None:
            remembered = format_decimal(self.context.remembered_unit_price)
            self.price.setPlaceholderText(
                text.PREVIOUS_PRICE.format(
                    price=remembered,
                    currency=self.context.currency.value,
                )
            )

    def _selected(self, combo: PaletteSafeComboBox) -> Reading | None:
        return self._readings.get(combo.currentData())

    def _update_preview(self, *_args: object) -> None:
        start = self._selected(self.start_reading)
        end = self._selected(self.end_reading)
        if start is None or end is None:
            return
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
        self.total_value.setText(
            f"{format_decimal(preview.total, places=2)} {preview.currency.value}"
        )

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
        thread = WorkflowThread(
            lambda: self.save_workflow(start_id, end_id, entered_price)
        )
        self.active_thread = thread
        thread.succeeded.connect(self._save_succeeded)
        thread.failed.connect(self._save_failed)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._save_finished(thread))
        thread.start()

    def _save_succeeded(self, saved: object) -> None:
        if isinstance(saved, Calculation):
            self._saved_result = saved

    def _save_failed(self, error: UserFacingError) -> None:
        self.error_raised.emit(error)

    def _save_finished(self, thread: WorkflowThread) -> None:
        if self.active_thread is thread:
            self.active_thread = None
        self._set_saving(False)
        self._update_preview()
        if self._saved_result is not None:
            saved = self._saved_result
            self._saved_result = None
            self.calculation_saved.emit(saved)
            self.accept()
        elif self._close_when_idle:
            QTimer.singleShot(0, self.reject)

    def _set_saving(self, saving: bool) -> None:
        self.start_reading.setEnabled(not saving)
        self.end_reading.setEnabled(not saving)
        self.price.setEnabled(not saving)
        self.save_button.setEnabled(not saving)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self.active_thread is not None:
            self._close_when_idle = True
            self.active_thread.requestInterruption()
            event.ignore()
            return
        event.accept()

    def reject(self) -> None:
        if self.active_thread is not None:
            self._close_when_idle = True
            self.active_thread.requestInterruption()
            return
        super().reject()

    def shutdown_worker(self) -> None:
        thread = self.active_thread
        if thread is None:
            return
        thread.requestInterruption()
        if thread is not QThread.currentThread():
            thread.wait()
        self.active_thread = None
