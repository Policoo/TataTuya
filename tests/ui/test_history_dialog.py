from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tatatuya.domain.models import Calculation, Currency, Device, Reading
from tatatuya.services.history_service import (
    CalculationHistoryItem,
    HistoryContext,
)
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.history import HistoryDialog


NOW = datetime(2026, 12, 3, 18, 42, tzinfo=UTC)


def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def reading(reading_id: int, minute: int, value: str, source: str) -> Reading:
    return Reading(
        "meter-1",
        NOW + timedelta(minutes=minute),
        value.replace(".", ""),
        2,
        "kWh",
        Decimal(value),
        source,
        "{}",
        reading_id,
    )


def populated_context() -> HistoryContext:
    start = reading(1, 0, "1234.56", "batch")
    end = reading(2, 30, "1247.06", "status")
    calculation = Calculation(
        "meter-1",
        1,
        2,
        Decimal("12.50"),
        Decimal("0.80"),
        Currency.RON,
        Decimal("10.00"),
        NOW + timedelta(hours=1),
        7,
    )
    return HistoryContext(
        "meter-1",
        (end, start),
        (CalculationHistoryItem(calculation, start, end),),
    )


def test_history_dialog_renders_read_only_tabs_and_full_calculation_details(
    tmp_path,
) -> None:
    qt_app = app()
    dialog = HistoryDialog(
        Device("meter-1", "Contor principal — locuința familiei"),
        populated_context(),
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.tabs.tabText(0) == text.READINGS
    assert dialog.tabs.tabText(1) == text.CALCULATIONS
    assert dialog.readings_table.rowCount() == 2
    assert dialog.readings_table.item(0, 1).text() == "1.247,06 kWh"
    assert dialog.readings_table.item(0, 4).text() == text.SOURCE_INDIVIDUAL
    assert dialog.calculations_table.rowCount() == 1
    assert dialog.calculations_table.item(0, 4).text() == "10,00 RON"

    detail_text = " ".join(
        label.text() for label in dialog.calculation_detail.findChildren(QLabel)
    )
    for expected in (
        text.START_READING,
        text.END_READING,
        "1.234,56 kWh",
        "1.247,06 kWh",
        "12,50 kWh",
        "0,80 RON/kWh",
        "10,00 RON",
    ):
        assert expected in detail_text

    assert not dialog.readings_table.editTriggers()
    assert not dialog.calculations_table.editTriggers()
    button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
    assert text.CLOSE in button_texts
    assert "Șterge" not in button_texts
    assert "Editează" not in button_texts

    screenshot_path = tmp_path / "history-dialog.png"
    screenshot = dialog.grab()
    assert screenshot.save(str(screenshot_path))
    assert screenshot_path.stat().st_size > 10_000
    assert dialog.minimumSizeHint().height() <= dialog.height()

    details_dialog = HistoryDialog(
        Device("meter-1", "Contor principal — locuința familiei"),
        populated_context(),
    )
    details_dialog.tabs.setCurrentIndex(1)
    details_dialog.show()
    qt_app.processEvents()
    assert details_dialog.isVisible()
    assert details_dialog.tabs.currentIndex() == 1
    assert details_dialog.calculations_table.isVisible()
    assert details_dialog.calculation_detail.isVisible()
    calculation_item = details_dialog.calculations_table.item(0, 0)
    assert calculation_item is not None
    assert not details_dialog.calculations_table.visualItemRect(
        calculation_item
    ).isEmpty()
    visible_details = [
        label
        for label in details_dialog.calculation_detail.findChildren(QLabel)
        if label.isVisible()
    ]
    assert visible_details
    assert all(not label.geometry().isEmpty() for label in visible_details)
    details_path = tmp_path / "history-calculations.png"
    assert details_dialog.grab().save(str(details_path))
    assert details_path.stat().st_size > 10_000
    details_dialog.close()
    dialog.close()


def test_history_dialog_has_clear_empty_states() -> None:
    qt_app = app()
    dialog = HistoryDialog(
        Device("meter-1", "Casa"),
        HistoryContext("meter-1", (), ()),
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.readings_empty.text() == text.NO_READING_HISTORY
    assert dialog.calculations_empty.text() == text.NO_CALCULATION_HISTORY
    assert dialog.readings_empty.isVisible()
    dialog.tabs.setCurrentIndex(1)
    qt_app.processEvents()
    assert dialog.calculations_empty.isVisible()
    assert not dialog.calculation_detail.isVisible()
    dialog.close()
