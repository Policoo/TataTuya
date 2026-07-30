from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Currency, Device, Reading
from tatatuya.services.billing_service import CalculationContext
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.calculate import CalculationDialog, CloudImportPayload
from tatatuya.ui.formatters import format_local_datetime
from tatatuya.ui.workers import WorkerOwner


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
    assert dialog.start_date.count() == 1
    assert dialog.end_date.count() == 1
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
    assert dialog.date_column_label.text() == text.DATE
    assert dialog.reading_column_label.text() == text.EXACT_READING
    assert dialog.start_date.geometry().top() == dialog.start_reading.geometry().top()
    assert dialog.end_date.geometry().top() == dialog.end_reading.geometry().top()
    assert dialog.start_date.geometry().right() < dialog.start_reading.geometry().left()
    assert dialog.end_date.geometry().right() < dialog.end_reading.geometry().left()
    assert dialog.start_date.geometry().bottom() < dialog.end_date.geometry().top()

    assert dialog.grab().save(str(tmp_path / "calculation-dialog.png"))
    assert dialog.save_button.width() >= dialog.save_button.sizeHint().width()
    assert dialog.save_button.height() >= dialog.save_button.sizeHint().height()
    dialog.close()


def test_dates_filter_exact_readings_and_large_popup_is_capped(tmp_path) -> None:
    qt_app = app()
    first_day = datetime(2026, 12, 3, 8, 0, tzinfo=UTC)
    dense_readings = {
        reading_id: Reading(
            "meter-1",
            first_day + timedelta(minutes=reading_id - 1),
            str(100_000 + reading_id),
            2,
            "kWh",
            Decimal(1000) + Decimal(reading_id) / 100,
            "batch",
            "{}",
            reading_id,
        )
        for reading_id in range(1, 22)
    }
    dense_readings[22] = Reading(
        "meter-1",
        first_day + timedelta(days=1),
        "100022",
        2,
        "kWh",
        Decimal("1000.22"),
        "batch",
        "{}",
        22,
    )
    dense_context = CalculationContext(
        "meter-1",
        tuple(dense_readings.values()),
        1,
        22,
        Decimal("0.80"),
        Currency.RON,
    )
    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        dense_context,
        Service(dense_readings),
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.start_date.count() == 2
    assert dialog.end_date.count() == 2
    assert dialog.start_reading.count() == 21
    assert dialog.end_reading.count() == 1
    assert all(
        combo.maxVisibleItems() == 15
        for combo in (
            dialog.start_date,
            dialog.start_reading,
            dialog.end_date,
            dialog.end_reading,
        )
    )
    assert all(
        combo.isVisible() and not combo.geometry().isEmpty()
        for combo in (
            dialog.start_date,
            dialog.start_reading,
            dialog.end_date,
            dialog.end_reading,
        )
    )
    assert dialog.grab().save(str(tmp_path / "calculation-dialog-dense.png"))

    dialog.start_reading.showPopup()
    qt_app.processEvents()
    view = dialog.start_reading.view()
    fourteen_rows_height = sum(view.sizeHintForRow(row) for row in range(14))
    fifteen_rows_height = sum(view.sizeHintForRow(row) for row in range(15))
    assert fourteen_rows_height < view.viewport().height() <= fifteen_rows_height
    assert view.verticalScrollBar().maximum() > 0
    assert view.window().grab().save(
        str(tmp_path / "calculation-reading-popup-dense.png")
    )
    dialog.start_reading.hidePopup()

    dialog.start_date.setCurrentIndex(1)
    qt_app.processEvents()
    assert dialog.start_reading.count() == 1
    assert dialog.start_reading.currentData() == 22
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


def test_dialog_opens_with_no_readings_and_cloud_import_rebuilds_selectors(
    tmp_path,
) -> None:
    qt_app = app()
    empty = CalculationContext(
        "meter-1", (), None, None, Decimal("0.80"), Currency.RON, True
    )
    imported_context = context()

    def import_cloud(cancellation):
        cancellation.checkpoint()
        return CloudImportPayload(imported_context, 2, 1)

    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        empty,
        Service(),
        cloud_import_workflow=import_cloud,
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.feedback.text() == text.INSUFFICIENT_READINGS
    assert not dialog.save_button.isEnabled()
    assert dialog.cloud_import_button.isEnabled()
    dialog.cloud_import_button.click()
    wait_for_save(qt_app, dialog)

    assert dialog.start_reading.count() == 3
    assert dialog.end_reading.count() == 3
    assert dialog.cloud_feedback.text() == text.CLOUD_IMPORT_RESULT.format(
        new=2, existing=1
    )
    assert dialog.cloud_feedback.property("state") == "success"
    assert dialog.save_button.isEnabled()
    assert all(
        widget.isVisible() and not widget.geometry().isEmpty()
        for widget in (
            dialog.cloud_panel,
            dialog.cloud_title,
            dialog.cloud_help,
            dialog.cloud_import_button,
            dialog.cloud_feedback,
        )
    )
    assert dialog.start_date.width() >= dialog.start_date.sizeHint().width()
    assert dialog.end_date.width() >= dialog.end_date.sizeHint().width()
    assert (
        dialog.cloud_import_button.width()
        >= dialog.cloud_import_button.sizeHint().width()
    )
    assert (
        dialog.cloud_title.palette().color(QPalette.ColorRole.WindowText)
        == QColor("#1d2939")
    )
    assert (
        dialog.cloud_help.palette().color(QPalette.ColorRole.WindowText)
        == QColor("#475467")
    )
    assert (
        dialog.cloud_feedback.palette().color(QPalette.ColorRole.WindowText)
        == QColor("#067647")
    )
    assert dialog.grab().save(str(tmp_path / "calculation-cloud-card-light.png"))
    dialog.close()


def test_unverified_feature_has_honest_state_and_no_import_control(tmp_path) -> None:
    qt_app = app()
    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        context(),
        Service(),
        cloud_unavailable_text=text.CLOUD_IMPORT_NOT_AVAILABLE,
    )
    dialog.show()
    qt_app.processEvents()

    assert not dialog.cloud_import_button.isVisible()
    assert not dialog.cloud_settings_button.isVisible()
    assert dialog.cloud_feedback.text() == text.CLOUD_IMPORT_NOT_AVAILABLE
    assert dialog.cloud_feedback.property("state") == "unavailable"
    assert all(
        widget.isVisible() and not widget.geometry().isEmpty()
        for widget in (
            dialog.cloud_panel,
            dialog.cloud_title,
            dialog.cloud_help,
            dialog.cloud_feedback,
        )
    )
    assert dialog.grab().save(str(tmp_path / "calculation-cloud-unavailable.png"))
    dialog.close()


def test_missing_credentials_offer_settings_action() -> None:
    qt_app = app()
    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        context(),
        Service(),
        cloud_settings_available=True,
    )
    requested = []
    dialog.settings_requested.connect(lambda: requested.append(True))
    dialog.show()
    qt_app.processEvents()

    assert not dialog.cloud_import_button.isVisible()
    assert dialog.cloud_settings_button.isVisible()
    assert dialog.cloud_settings_button.isEnabled()
    assert not dialog.cloud_settings_button.geometry().isEmpty()
    assert (
        dialog.cloud_settings_button.width()
        >= dialog.cloud_settings_button.sizeHint().width()
    )
    assert dialog.cloud_feedback.text() == text.CLOUD_IMPORT_NEEDS_SETTINGS
    dialog.cloud_settings_button.click()
    assert requested == [True]
    dialog.close()


def test_empty_cloud_result_ends_with_truthful_non_loading_state() -> None:
    qt_app = app()

    def import_cloud(cancellation):
        cancellation.checkpoint()
        return CloudImportPayload(context(), 0, 0)

    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        context(),
        Service(),
        cloud_import_workflow=import_cloud,
    )
    dialog.show()
    dialog.cloud_import_button.click()
    wait_for_save(qt_app, dialog)

    assert dialog.cloud_feedback.text() == text.CLOUD_IMPORT_EMPTY
    assert dialog.cloud_feedback.property("state") == "empty"
    assert dialog.cloud_import_button.isEnabled()
    dialog.close()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Istoric Tuya indisponibil", text.CLOUD_IMPORT_PERMISSION_UNAVAILABLE),
        ("Tuya este ocupat", text.CLOUD_IMPORT_RATE_LIMITED),
        ("Prea multe date Tuya", text.CLOUD_IMPORT_RESPONSE_LIMIT),
        ("Eroare import", text.CLOUD_IMPORT_FAILED),
    ],
)
def test_cloud_failure_restores_action_and_terminal_status(title, expected) -> None:
    qt_app = app()

    def import_cloud(cancellation):
        cancellation.checkpoint()
        raise UserFacingError(title, "Mesaj de test")

    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        context(),
        Service(),
        cloud_import_workflow=import_cloud,
    )
    errors = []
    dialog.error_raised.connect(errors.append)
    dialog.show()
    dialog.cloud_import_button.click()
    wait_for_save(qt_app, dialog)

    assert errors and errors[0].title == title
    assert dialog.cloud_feedback.text() == expected
    assert dialog.cloud_feedback.text() != text.CLOUD_IMPORTING
    assert dialog.cloud_feedback.property("state") == "error"
    assert dialog.cloud_import_button.isEnabled()
    dialog.close()


def test_close_during_cloud_import_detaches_late_results_and_owner_drains() -> None:
    qt_app = app()
    owner = WorkerOwner(qt_app)
    started = threading.Event()

    def import_cloud(cancellation):
        started.set()
        while not cancellation.cancelled:
            cancellation.wait(0.01)
        cancellation.checkpoint()
        raise AssertionError("cancelled import unexpectedly continued")

    dialog = CalculationDialog(
        Device("meter-1", "Casa"),
        context(),
        Service(),
        cloud_import_workflow=import_cloud,
        worker_owner=owner,
    )
    dialog.show()
    dialog.cloud_import_button.click()
    deadline = time.monotonic() + 1
    while not started.is_set() and time.monotonic() < deadline:
        qt_app.processEvents()
    assert started.is_set() and owner.active

    dialog.close()
    qt_app.processEvents()
    assert not dialog.isVisible()
    deadline = time.monotonic() + 2
    while owner.active and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)

    assert not owner.active
    assert dialog.cloud_feedback.text() == text.CLOUD_IMPORTING


def test_dark_palette_labels_and_reading_popup_remain_readable(tmp_path) -> None:
    qt_app = app()
    original = qt_app.palette()
    dark = QPalette(original)
    dark.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#f8fafc"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#101114"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#f8fafc"))
    dark.setColor(QPalette.ColorRole.Highlight, QColor("#000000"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
    qt_app.setPalette(dark)
    qt_app.setStyleSheet(load_stylesheet())
    try:
        dialog = CalculationDialog(
            Device("meter-1", "Contor principal — Strada Independenței"),
            context(),
            Service(),
            cloud_import_workflow=lambda cancellation: CloudImportPayload(
                context(), 1, 0
            ),
        )
        dialog.show()
        qt_app.processEvents()

        labels = dialog.findChildren(QLabel, "FieldLabel")
        assert len(labels) == 10
        assert all(label.isVisible() and not label.geometry().isEmpty() for label in labels)
        assert all(
            label.palette().color(QPalette.ColorRole.WindowText) == QColor("#667085")
            for label in labels
        )
        cloud_labels = (
            dialog.cloud_title,
            dialog.cloud_help,
            dialog.cloud_feedback,
        )
        assert all(
            label.isVisible()
            and not label.geometry().isEmpty()
            and bool(label.text())
            for label in cloud_labels
        )
        assert dialog.cloud_import_button.isVisible()
        assert not dialog.cloud_import_button.geometry().isEmpty()
        assert dialog.height() >= dialog.sizeHint().height()
        assert dialog.close_button.geometry().bottom() < dialog.rect().bottom()
        assert dialog.save_button.geometry().bottom() < dialog.rect().bottom()
        assert (
            dialog.cloud_title.palette().color(QPalette.ColorRole.WindowText)
            == QColor("#1d2939")
        )
        assert (
            dialog.cloud_help.palette().color(QPalette.ColorRole.WindowText)
            == QColor("#475467")
        )
        assert (
            dialog.cloud_feedback.palette().color(QPalette.ColorRole.WindowText)
            == QColor("#344054")
        )

        dialog.start_reading.showPopup()
        qt_app.processEvents()
        popup = dialog.start_reading.view()
        popup_window = popup.window()
        assert popup_window.objectName() == "ComboPopup"
        assert popup_window.palette().color(QPalette.ColorRole.Window) == QColor(
            "#ffffff"
        )
        assert popup_window.palette().color(QPalette.ColorRole.Text) == QColor(
            "#101828"
        )
        dialog.start_reading.hidePopup()

        screenshot = dialog.grab()
        assert screenshot.save(str(tmp_path / "calculation-dialog-dark.png"))
        dialog.close()
    finally:
        qt_app.setPalette(original)
        qt_app.setStyleSheet(load_stylesheet())
