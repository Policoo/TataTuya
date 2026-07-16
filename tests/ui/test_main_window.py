from datetime import UTC, datetime
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, Reading
from tatatuya.services.reading_service import DeviceRefreshResult
from tatatuya.ui import text
from tatatuya.ui.app import create_main_window, load_stylesheet
from tatatuya.ui.components.device_table import DeviceTableRow
from tatatuya.ui.main_window import InitialState, MainWindow


def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def representative_row() -> DeviceTableRow:
    return DeviceTableRow(
        Device(
            "meter-1",
            "Contor principal — locuința familiei din Strada Independenței",
            online=True,
        ),
        Reading(
            "meter-1",
            datetime(2026, 12, 3, 18, 42, tzinfo=UTC),
            "123456",
            2,
            "kWh",
            Decimal("1234.56"),
            "batch",
            "{}",
            1,
        ),
    )


def test_missing_settings_state_directs_user_to_settings() -> None:
    qt_app = app()
    window = MainWindow(cached_rows=[representative_row()], settings_configured=False)
    window.show()
    qt_app.processEvents()
    assert window.content.currentWidget() is window.settings_state
    labels = {
        label.objectName(): label for label in window.settings_state.findChildren(QLabel)
    }
    heading = labels["EmptyTitle"]
    detail = labels["EmptyMessage"]
    assert heading.text() == text.SETTINGS_REQUIRED
    assert detail.text() == text.SETTINGS_REQUIRED_HELP
    assert heading.height() >= heading.heightForWidth(heading.width())
    assert detail.height() >= detail.heightForWidth(detail.width())
    assert not heading.geometry().intersects(detail.geometry())
    window.resize(700, 450)
    qt_app.processEvents()
    assert heading.height() >= heading.heightForWidth(heading.width())
    assert detail.height() >= detail.heightForWidth(detail.width())
    assert not heading.geometry().intersects(detail.geometry())
    window.close()


def test_action_text_is_visible_and_rows_fit_styled_buttons(tmp_path) -> None:
    qt_app = app()
    window = MainWindow(cached_rows=[representative_row()], settings_configured=True)
    window.show()
    qt_app.processEvents()

    action_widget = window.table.cellWidget(0, 4)
    buttons = action_widget.findChildren(QPushButton)
    assert [button.text() for button in buttons] == [
        text.CALCULATE,
        text.HISTORY,
        text.INFO,
        text.STATUS,
    ]
    assert all(button.width() >= button.sizeHint().width() for button in buttons)
    assert all(button.height() >= button.sizeHint().height() for button in buttons)
    assert window.table.rowHeight(0) >= action_widget.sizeHint().height()
    name_item = window.table.item(0, 0)
    name_rect = window.table.visualItemRect(name_item)
    text_width = window.table.fontMetrics().horizontalAdvance(name_item.text())
    assert name_rect.width() - 24 >= text_width

    screenshot = window.grab()
    screenshot_path = tmp_path / "main-window.png"
    assert screenshot.save(str(screenshot_path))
    assert screenshot.width() == window.width()
    assert screenshot.height() == window.height()
    assert screenshot_path.stat().st_size > 10_000
    window.close()


def test_refresh_runs_async_preserves_rows_and_restores_button() -> None:
    qt_app = app()

    def refresh():
        time.sleep(0.03)
        row = representative_row()
        return [DeviceRefreshResult(row.device, row.latest_reading, row.latest_reading)]

    window = MainWindow(
        refresh,
        cached_rows=[representative_row()],
        settings_configured=True,
    )
    window.show()
    window.refresh_devices()
    assert not window.refresh_button.isEnabled()
    assert window.table.rowCount() == 1
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert not window.active_threads
    assert window.refresh_button.isEnabled()
    assert window.status_label.text() == text.REFRESH_COMPLETE
    window.close()


def test_refresh_failure_restores_controls_and_emits_safe_error() -> None:
    qt_app = app()

    def refresh():
        raise UserFacingError("Conexiune eșuată", "Verificați setările.")

    window = MainWindow(refresh, settings_configured=True)
    errors = []
    window.error_raised.connect(errors.append)
    window.show()
    window.refresh_devices()
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert window.refresh_button.isEnabled()
    assert errors and errors[0].title == "Conexiune eșuată"
    window.close()


def test_close_during_refresh_waits_for_worker_without_destroying_thread() -> None:
    qt_app = app()
    started = threading.Event()
    release = threading.Event()

    def refresh():
        started.set()
        release.wait(timeout=2)
        return []

    window = MainWindow(refresh, settings_configured=True)
    window.show()
    window.refresh_devices()
    deadline = time.monotonic() + 1
    while not started.is_set() and time.monotonic() < deadline:
        qt_app.processEvents()
    assert started.is_set()

    window.close()
    qt_app.processEvents()
    assert window.isVisible()
    assert window.active_threads
    assert window.status_label.text() == text.CLOSING_AFTER_WORK

    release.set()
    deadline = time.monotonic() + 2
    while (window.active_threads or window.isVisible()) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    assert not window.active_threads
    assert not window.isVisible()


def test_database_bootstrap_starts_after_window_creation_and_off_gui_thread(
    monkeypatch,
) -> None:
    qt_app = app()
    calls = []
    gui_thread = threading.get_ident()

    def load_state(database):
        calls.append(threading.get_ident())
        return InitialState([], False, None)

    monkeypatch.setattr("tatatuya.ui.app._load_initial_state", load_state)
    window = create_main_window(object())
    assert calls == []
    window.show()
    deadline = time.monotonic() + 2
    while (not calls or window.active_threads) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert calls and calls[0] != gui_thread
    assert not window.active_threads
    window.close()


def test_delayed_bootstrap_shows_neutral_loading_state() -> None:
    qt_app = app()
    started = threading.Event()
    release = threading.Event()

    def bootstrap():
        started.set()
        release.wait(timeout=2)
        return InitialState([], True, lambda: [])

    window = MainWindow(bootstrap_workflow=bootstrap)
    window.show()
    window.load_initial_state()
    deadline = time.monotonic() + 1
    while not started.is_set() and time.monotonic() < deadline:
        qt_app.processEvents()
    assert started.is_set()
    assert window.content.currentWidget() is window.loading_state
    loading_text = " ".join(
        label.text() for label in window.loading_state.findChildren(QLabel)
    )
    assert text.LOADING_LOCAL_TITLE in loading_text
    assert text.SETTINGS_REQUIRED not in loading_text

    release.set()
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert window.content.currentWidget() is window.empty_state
    window.close()


def test_failed_bootstrap_shows_local_error_and_retry_recovers() -> None:
    qt_app = app()
    attempts = 0

    def bootstrap():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise UserFacingError(
                "Baza de date nu poate fi deschisă",
                "Verificați accesul la spațiul de stocare.",
            )
        return InitialState([], True, lambda: [])

    window = MainWindow(bootstrap_workflow=bootstrap)
    errors = []
    window.error_raised.connect(errors.append)
    window.show()
    window.load_initial_state()
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert window.content.currentWidget() is window.local_data_error_state
    assert window.status_label.text() == text.LOCAL_DATA_FAILED
    assert not window.refresh_button.isEnabled()
    assert errors and errors[0].title == "Baza de date nu poate fi deschisă"
    retry = next(
        button
        for button in window.local_data_error_state.findChildren(QPushButton)
        if button.text() == text.RETRY
    )
    assert retry.isEnabled()

    retry.click()
    assert window.content.currentWidget() is window.loading_state
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert attempts == 2
    assert window.content.currentWidget() is window.empty_state
    assert window.status_label.text() == text.READY
    assert window.refresh_button.isEnabled()
    window.close()


def test_application_quit_waits_for_active_refresh_worker() -> None:
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONPATH"] = str(project_root / "src")
    script = """
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from tatatuya.ui.main_window import MainWindow

app = QApplication([])

def refresh():
    time.sleep(0.2)
    return []

window = MainWindow(refresh, settings_configured=True)
window.show()
window.refresh_devices()
QTimer.singleShot(20, app.quit)
raise SystemExit(app.exec())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "deleted directly" not in completed.stderr
    assert "thread is still running" not in completed.stderr
