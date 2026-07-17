"""Read-only history for a meter's readings and calculations."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tatatuya.domain.models import Device, Reading
from tatatuya.services.history_service import HistoryContext
from tatatuya.ui import text
from tatatuya.ui.formatters import (
    format_energy,
    format_local_datetime,
    format_money,
    format_unit_price,
)


class HistoryDialog(QDialog):
    def __init__(
        self,
        device: Device,
        context: HistoryContext,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.context = context
        self.setWindowTitle(text.HISTORY_TITLE)
        self.setModal(True)
        self.resize(980, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel(text.HISTORY_TITLE)
        title.setObjectName("ModalTitle")
        layout.addWidget(title)
        meter = QLabel(device.name)
        meter.setObjectName("HistoryMeter")
        meter.setWordWrap(True)
        layout.addWidget(meter)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_readings_tab(), text.READINGS)
        self.tabs.addTab(self._build_calculations_tab(), text.CALCULATIONS)
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText(text.CLOSE)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_readings()
        self._populate_calculations()

    def _build_readings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)

        self.readings_stack = QStackedWidget()
        self.readings_table = self._table(
            [
                text.DATE_AND_TIME,
                text.CUMULATIVE_READING,
                text.RAW_VALUE,
                text.SCALE_AND_UNIT,
                text.SOURCE,
            ]
        )
        self.readings_empty = self._empty_label(text.NO_READING_HISTORY)
        self.readings_stack.addWidget(self.readings_table)
        self.readings_stack.addWidget(self.readings_empty)
        layout.addWidget(self.readings_stack)
        return tab

    def _build_calculations_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        self.calculations_stack = QStackedWidget()
        self.calculations_table = self._table(
            [
                text.CALCULATION_DATE,
                text.PERIOD,
                text.CONSUMPTION,
                text.UNIT_PRICE,
                text.TOTAL,
            ]
        )
        self.calculations_table.currentCellChanged.connect(
            self._show_calculation_detail
        )
        self.calculations_empty = self._empty_label(text.NO_CALCULATION_HISTORY)
        self.calculations_stack.addWidget(self.calculations_table)
        self.calculations_stack.addWidget(self.calculations_empty)
        layout.addWidget(self.calculations_stack, 1)

        self.calculation_detail = QFrame()
        self.calculation_detail.setObjectName("FieldPanel")
        self.detail_grid = QGridLayout(self.calculation_detail)
        self.detail_grid.setContentsMargins(14, 12, 14, 12)
        self.detail_grid.setHorizontalSpacing(18)
        self.detail_grid.setVerticalSpacing(8)
        self.detail_grid.setColumnStretch(1, 1)
        layout.addWidget(self.calculation_detail)
        return tab

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        for column in range(len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _empty_label(message: str) -> QLabel:
        label = QLabel(message)
        label.setObjectName("HistoryEmpty")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label

    def _populate_readings(self) -> None:
        self.readings_table.setRowCount(len(self.context.readings))
        for row, reading in enumerate(self.context.readings):
            values = (
                format_local_datetime(reading.recorded_at_utc),
                format_energy(reading.value_kwh),
                reading.raw_value,
                f"{reading.scale} · {reading.source_unit}",
                source_label(reading.source),
            )
            for column, value in enumerate(values):
                self.readings_table.setItem(row, column, QTableWidgetItem(value))
        self.readings_stack.setCurrentWidget(
            self.readings_table if self.context.readings else self.readings_empty
        )

    def _populate_calculations(self) -> None:
        self.calculations_table.setRowCount(len(self.context.calculations))
        for row, item in enumerate(self.context.calculations):
            calculation = item.calculation
            values = (
                format_local_datetime(calculation.created_at_utc),
                (
                    f"{format_local_datetime(item.start_reading.recorded_at_utc)} – "
                    f"{format_local_datetime(item.end_reading.recorded_at_utc)}"
                ),
                format_energy(calculation.consumption_kwh),
                format_unit_price(calculation.unit_price, calculation.currency),
                format_money(calculation.total, calculation.currency),
            )
            for column, value in enumerate(values):
                self.calculations_table.setItem(row, column, QTableWidgetItem(value))

        has_calculations = bool(self.context.calculations)
        self.calculations_stack.setCurrentWidget(
            self.calculations_table if has_calculations else self.calculations_empty
        )
        self.calculation_detail.setVisible(has_calculations)
        if has_calculations:
            self.calculations_table.setCurrentCell(0, 0)
            self._show_calculation_detail(0)

    def _show_calculation_detail(self, row: int, *_args: object) -> None:
        if row < 0 or row >= len(self.context.calculations):
            return
        self._clear_detail()
        item = self.context.calculations[row]
        calculation = item.calculation
        rows = (
            (text.CALCULATION_DATE, format_local_datetime(calculation.created_at_utc)),
            (text.START_READING, reading_detail(item.start_reading)),
            (text.END_READING, reading_detail(item.end_reading)),
            (text.CONSUMPTION, format_energy(calculation.consumption_kwh)),
            (
                text.UNIT_PRICE,
                format_unit_price(calculation.unit_price, calculation.currency),
            ),
            (text.CURRENCY, calculation.currency.value),
            (
                text.TOTAL,
                format_money(calculation.total, calculation.currency),
            ),
        )
        for index, (label, value) in enumerate(rows):
            label_widget = QLabel(label)
            label_widget.setObjectName("FieldLabel")
            value_widget = QLabel(value)
            value_widget.setObjectName("FieldValue")
            value_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_widget.setWordWrap(True)
            self.detail_grid.addWidget(
                label_widget, index, 0, Qt.AlignmentFlag.AlignTop
            )
            self.detail_grid.addWidget(value_widget, index, 1)

    def _clear_detail(self) -> None:
        while self.detail_grid.count():
            item = self.detail_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()


def reading_detail(reading: Reading) -> str:
    return (
        f"{format_local_datetime(reading.recorded_at_utc)} — "
        f"{format_energy(reading.value_kwh)}"
    )


def source_label(source: str) -> str:
    labels = {
        "batch": text.SOURCE_BATCH,
        "status": text.SOURCE_INDIVIDUAL,
    }
    return labels.get(source, source)
