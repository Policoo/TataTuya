from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tatatuya.domain.models import Currency, Device, Reading
from tatatuya.services.billing_service import CalculationContext
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.calculate import CalculationDialog
from tatatuya.ui.formatters import format_local_datetime


NOW = datetime(2026, 12, 3, 18, 42, tzinfo=UTC)


def app() -> QApplication:
    existing = QApplication.instance()
    instance = existing if isinstance(existing, QApplication) else QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def reading(reading_id: int, value: str, minute: int) -> Reading:
    return Reading(
        "meter-1",
        NOW + timedelta(minutes=minute),
        value,
        2,
        "kWh",
        Decimal(value),
        "batch",
        "{}",
        reading_id,
    )


class Service:
    def __init__(
        self,
        readings=None,
        remembered: Decimal | None = Decimal("0.80"),
    ) -> None:
        self.saved = []
        self.save_thread_ids = []
        self.readings = readings or READINGS
        self.remembered = remembered

    def consumption(self, start, end):
        from tatatuya.domain.billing import calculate_consumption

        return calculate_consumption(start, end)

    def preview(self, start, end, entered, currency, remembered):
        from tatatuya.domain.billing import calculate_period, resolve_unit_price

        return calculate_period(
            start, end, resolve_unit_price(entered, remembered), currency, NOW
        )

    def save_calculation(self, device_id, start_id, end_id, entered, currency):
        start = self.readings[start_id]
        end = self.readings[end_id]
        result = self.preview(start, end, entered, currency, self.remembered)
        self.saved.append(result)
        return result

    def __call__(self, start_id, end_id, entered):
        self.save_thread_ids.append(threading.get_ident())
        return self.save_calculation(
            "meter-1", start_id, end_id, entered, Currency.RON
        )


READINGS = {
    1: reading(1, "1234.56", 0),
    2: reading(2, "1247.06", 30),
    3: reading(3, "1250.06", 60),
}


def context(remembered: Decimal | None = Decimal("0.80")) -> CalculationContext:
    return CalculationContext(
        "meter-1",
        tuple(READINGS.values()),
        2,
        3,
        remembered,
        Currency.RON,
    )


def wait_for_save(qt_app: QApplication, dialog: CalculationDialog) -> None:
    deadline = time.monotonic() + 2
    while dialog.active_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert dialog.active_thread is None


def test_defaults_show_individual_timestamps_fallback_and_exact_preview(tmp_path) -> None:
    qt_app = app()
    service = Service()
    dialog = CalculationDialog(
        Device("meter-1", "Contor principal — Strada Independenței"),
        context(),
        service,
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.start_reading.currentData() == 2
    assert dialog.end_reading.currentData() == 3
    assert dialog.start_reading.count() == 3
    assert format_local_datetime(
        READINGS[1].recorded_at_utc
    ) in dialog.start_reading.itemText(0)
    assert format_local_datetime(
        READINGS[2].recorded_at_utc
    ) in dialog.start_reading.itemText(1)
    assert dialog.price.text() == ""
    assert dialog.price.placeholderText() == "Ultimul preț: 0,80 RON"
    assert dialog.consumption_value.text() == "3,00 kWh"
    assert dialog.total_value.text() == "2,40 RON"

    assert dialog.grab().save(str(tmp_path / "calculation-dialog.png"))
    assert dialog.save_button.width() >= dialog.save_button.sizeHint().width()
    assert dialog.save_button.height() >= dialog.save_button.sizeHint().height()
    dialog.close()


def test_comma_price_updates_preview_and_saved_values_match() -> None:
    qt_app = app()
    gui_thread_id = threading.get_ident()
    service = Service()
    dialog = CalculationDialog(Device("meter-1", "Casa"), context(), service)
    saved = []
    dialog.calculation_saved.connect(saved.append)
    dialog.show()

    dialog.price.setText("0,85")
    qt_app.processEvents()
    assert dialog.total_value.text() == "2,55 RON"
    dialog.save_button.click()
    assert not dialog.save_button.isEnabled()
    wait_for_save(qt_app, dialog)

    assert len(saved) == 1
    assert saved[0].consumption_kwh == Decimal("3.00")
    assert saved[0].unit_price == Decimal("0.85")
    assert saved[0].total == Decimal("2.5500")
    assert service.saved == saved
    assert service.save_thread_ids and service.save_thread_ids[0] != gui_thread_id


def test_lower_end_value_emits_shared_user_error_and_stays_open() -> None:
    qt_app = app()
    reset_end = reading(4, "1200", 90)
    reset_readings = {1: READINGS[1], 4: reset_end}
    reset_context = CalculationContext(
        "meter-1",
        tuple(reset_readings.values()),
        1,
        4,
        Decimal("0.80"),
        Currency.RON,
    )
    service = Service(reset_readings)
    dialog = CalculationDialog(
        Device("meter-1", "Casa"), reset_context, service
    )
    errors = []
    dialog.error_raised.connect(errors.append)
    dialog.show()
    qt_app.processEvents()

    assert dialog.consumption_value.text() == "—"
    assert dialog.feedback.text() == text.CALCULATION_INVALID_PREVIEW
    dialog.save_button.click()
    wait_for_save(qt_app, dialog)

    assert errors and errors[0].title == "Index mai mic"
    assert dialog.isVisible()
    dialog.close()


def test_no_remembered_price_requires_input_for_total() -> None:
    qt_app = app()
    dialog = CalculationDialog(
        Device("meter-1", "Casa"), context(None), Service(remembered=None)
    )
    errors = []
    dialog.error_raised.connect(errors.append)
    dialog.show()
    qt_app.processEvents()

    assert dialog.total_value.text() == "—"
    assert dialog.feedback.text() == text.PRICE_REQUIRED_FOR_TOTAL
    dialog.save_button.click()
    wait_for_save(qt_app, dialog)
    assert errors and errors[0].title == "Preț lipsă"
    assert dialog.isVisible()
    dialog.close()
