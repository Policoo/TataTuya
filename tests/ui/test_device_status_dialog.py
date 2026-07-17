from datetime import UTC, datetime
from decimal import Decimal
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, DeviceStatus, Reading, StatusValue
from tatatuya.services.reading_service import StatusCaptureResult
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.device_status import DeviceStatusDialog


NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def app() -> QApplication:
    existing = QApplication.instance()
    instance = existing if isinstance(existing, QApplication) else QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def result_with_reading() -> StatusCaptureResult:
    status = DeviceStatus(
        "meter-1",
        (
            StatusValue("forward_energy_total", 123456),
            StatusValue("switch_1", False),
        ),
        '{"status":[{"code":"forward_energy_total","value":123456},'
        '{"code":"switch_1","value":false}]}',
    )
    reading = Reading(
        "meter-1",
        NOW,
        "123456",
        2,
        "kWh",
        Decimal("1234.56"),
        "status",
        status.raw_json,
        9,
    )
    return StatusCaptureResult(status, reading)


def test_status_preserves_raw_codes_and_reports_captured_reading(tmp_path) -> None:
    qt_app = app()
    dialog = DeviceStatusDialog(
        Device("meter-1", "Contor principal"), result_with_reading()
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.status_table.rowCount() == 2
    energy_code = dialog.status_table.item(0, 0)
    switch_code = dialog.status_table.item(1, 0)
    switch_value = dialog.status_table.item(1, 1)
    assert energy_code is not None and energy_code.text() == "forward_energy_total"
    assert switch_code is not None and switch_code.text() == "switch_1"
    assert switch_value is not None and switch_value.text() == "false"
    assert not dialog.status_table.editTriggers()
    assert dialog.capture_feedback.text() == text.STATUS_READING_SAVED.format(
        reading="1.234,56 kWh"
    )
    assert "forward_energy_total" in dialog.raw_response.toPlainText()
    assert dialog.raw_response.isReadOnly()
    assert [button.text() for button in dialog.findChildren(QPushButton)] == [
        text.CLOSE
    ]

    screenshot_path = tmp_path / "device-status.png"
    assert dialog.grab().save(str(screenshot_path))
    assert screenshot_path.stat().st_size > 8_000
    assert dialog.minimumSizeHint().height() <= dialog.height()
    dialog.close()


def test_status_keeps_diagnostics_visible_when_energy_cannot_be_saved() -> None:
    qt_app = app()
    status = DeviceStatus(
        "meter-1",
        (StatusValue("switch_1", True),),
        '{"status":[{"code":"switch_1","value":true}]}',
    )
    capture_error = UserFacingError(
        "Citire de energie indisponibilă",
        "Contorul nu a returnat indexul cumulativ.",
    )
    dialog = DeviceStatusDialog(
        Device("meter-1", "Casa"),
        StatusCaptureResult(status, None, capture_error),
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.status_table.rowCount() == 1
    switch_code = dialog.status_table.item(0, 0)
    assert switch_code is not None and switch_code.text() == "switch_1"
    assert "Citirea nu a fost salvată" in dialog.capture_feedback.text()
    assert capture_error.message in dialog.capture_feedback.text()
    assert dialog.raw_response.isVisible()
    dialog.close()


def test_status_has_a_clear_empty_diagnostic_state() -> None:
    qt_app = app()
    status = DeviceStatus("meter-1", (), "{}")
    dialog = DeviceStatusDialog(
        Device("meter-1", "Casa"), StatusCaptureResult(status, None)
    )
    dialog.show()
    qt_app.processEvents()

    assert dialog.status_empty.text() == text.NO_STATUS_VALUES
    assert dialog.status_empty.isVisible()
    assert dialog.capture_feedback.text() == text.STATUS_WITHOUT_READING
    dialog.close()
