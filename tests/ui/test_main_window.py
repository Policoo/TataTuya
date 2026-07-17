from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import (
    Currency,
    Device,
    EnergyEligibility,
    Reading,
    TuyaSettings,
)
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import SettingsRepository
from tatatuya.services.reading_service import DeviceRefreshResult
from tatatuya.ui import text
from tatatuya.ui.app import (
    _load_initial_state,
    _prepare_settings,
    create_main_window,
    load_stylesheet,
)
from tatatuya.ui.components.device_table import DeviceTableRow
from tatatuya.ui.dialogs.settings import SavedSettings
from tatatuya.ui.main_window import InitialState, MainWindow


def app() -> QApplication:
    existing = QApplication.instance()
    instance = existing if isinstance(existing, QApplication) else QApplication([])
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
    assert name_item is not None
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


def test_calculate_row_action_is_forwarded_with_its_device() -> None:
    qt_app = app()
    row = representative_row()
    window = MainWindow(cached_rows=[row], settings_configured=True)
    requested = []
    window.calculation_requested.connect(requested.append)
    window.show()
    qt_app.processEvents()

    action_widget = window.table.cellWidget(0, 4)
    calculate = next(
        button
        for button in action_widget.findChildren(QPushButton)
        if button.text() == text.CALCULATE
    )
    calculate.click()
    qt_app.processEvents()

    assert requested == [row.device]
    window.close()


def test_history_row_action_is_forwarded_with_its_device() -> None:
    qt_app = app()
    row = representative_row()
    window = MainWindow(cached_rows=[row], settings_configured=True)
    requested = []
    window.history_requested.connect(requested.append)
    window.show()
    qt_app.processEvents()

    action_widget = window.table.cellWidget(0, 4)
    history = next(
        button
        for button in action_widget.findChildren(QPushButton)
        if button.text() == text.HISTORY
    )
    history.click()
    qt_app.processEvents()

    assert requested == [row.device]
    window.close()


def test_info_and_status_row_actions_are_forwarded_with_their_device() -> None:
    qt_app = app()
    row = representative_row()
    window = MainWindow(cached_rows=[row], settings_configured=True)
    info_requested = []
    status_requested = []
    window.info_requested.connect(info_requested.append)
    window.status_requested.connect(status_requested.append)
    window.show()
    qt_app.processEvents()

    action_widget = window.table.cellWidget(0, 4)
    buttons = {
        button.text(): button for button in action_widget.findChildren(QPushButton)
    }
    buttons[text.INFO].click()
    buttons[text.STATUS].click()
    qt_app.processEvents()

    assert info_requested == [row.device]
    assert status_requested == [row.device]
    window.close()


def test_individual_status_reading_updates_the_cached_main_table_row() -> None:
    qt_app = app()
    row = representative_row()
    updated = Reading(
        row.device.device_id,
        datetime(2026, 12, 3, 19, 15, tzinfo=UTC),
        "124706",
        2,
        "kWh",
        Decimal("1247.06"),
        "status",
        "{}",
        2,
    )
    window = MainWindow(cached_rows=[row], settings_configured=True)
    window.show()

    window.apply_individual_reading(row.device.device_id, updated)
    qt_app.processEvents()

    assert window.table.rows[0].latest_reading is updated
    current_reading = window.table.item(0, 2)
    assert current_reading is not None and current_reading.text() == "1.247,06 kWh"
    window.close()


def test_bootstrap_hides_new_unsupported_devices_but_keeps_historical_rows(
    tmp_path,
) -> None:
    qt_app = app()
    database = Database(tmp_path / "lifecycle.sqlite3")
    database.initialize()
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        devices.upsert(
            Device(
                "lamp-1",
                "Lampă",
                energy_eligibility=EnergyEligibility.UNSUPPORTED,
                present_in_tuya=True,
            ),
            datetime(2026, 7, 17, tzinfo=UTC),
        )
        devices.upsert(
            Device(
                "unknown-missing",
                "Dispozitiv neclasificat",
                energy_eligibility=EnergyEligibility.UNKNOWN,
                present_in_tuya=False,
            ),
            datetime(2026, 7, 17, tzinfo=UTC),
        )
        devices.upsert(
            Device(
                "meter-old",
                "Contor vechi",
                energy_eligibility=EnergyEligibility.UNSUPPORTED,
                present_in_tuya=False,
            ),
            datetime(2026, 7, 17, tzinfo=UTC),
        )
        ReadingRepository(connection).add(
            Reading(
                "meter-old",
                datetime(2026, 7, 16, tzinfo=UTC),
                "10000",
                2,
                "kWh",
                Decimal("100"),
                "batch",
                "{}",
            )
        )

    state = _load_initial_state(database)
    assert [row.device.device_id for row in state.rows] == ["meter-old"]

    window = MainWindow(cached_rows=state.rows, settings_configured=True)
    window.show()
    qt_app.processEvents()
    state_item = window.table.item(0, 1)
    assert state_item is not None and state_item.text() == text.NOT_IN_TUYA
    action_widget = window.table.cellWidget(0, 4)
    assert action_widget is not None
    buttons = {
        button.text(): button
        for button in action_widget.findChildren(QPushButton)
    }
    assert buttons[text.CALCULATE].isEnabled()
    assert buttons[text.HISTORY].isEnabled()
    assert buttons[text.INFO].isEnabled()
    assert not buttons[text.STATUS].isEnabled()
    assert window.grab().save(str(tmp_path / "missing-historical-meter.png"))
    window.close()


def test_calculation_preparation_runs_off_gui_thread_and_restores_controls() -> None:
    qt_app = app()
    gui_thread = threading.get_ident()
    worker_threads = []
    payloads = []

    def prepare():
        worker_threads.append(threading.get_ident())
        return "pregătit"

    window = MainWindow(
        cached_rows=[representative_row()], settings_configured=True
    )
    window.show()
    window.run_background_operation(
        prepare,
        payloads.append,
        text.PREPARING_CALCULATION,
    )
    assert not window.refresh_button.isEnabled()
    assert window.status_label.text() == text.PREPARING_CALCULATION

    deadline = time.monotonic() + 2
    while (
        window.active_threads or not payloads
    ) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert worker_threads and worker_threads[0] != gui_thread
    assert payloads == ["pregătit"]
    assert window.refresh_button.isEnabled()
    assert window.status_label.text() == text.READY
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


def test_historical_unsupported_classification_reports_complete_refresh() -> None:
    qt_app = app()
    row = representative_row()
    unsupported = replace(
        row.device,
        energy_eligibility=EnergyEligibility.UNSUPPORTED,
        present_in_tuya=True,
    )

    window = MainWindow(
        lambda: [
            DeviceRefreshResult(
                unsupported,
                None,
                row.latest_reading,
            )
        ],
        cached_rows=[row],
        settings_configured=True,
    )
    window.show()
    window.refresh_devices()
    deadline = time.monotonic() + 2
    while window.active_threads and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert window.status_label.text() == text.REFRESH_COMPLETE
    assert window.table.rowCount() == 1
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


def test_saved_settings_do_not_look_verified_without_a_matching_test() -> None:
    qt_app = app()
    window = MainWindow(settings_configured=False)
    window.show()

    window.apply_settings(lambda: [], connection_verified=False)
    qt_app.processEvents()
    assert window.settings_configured
    assert window.status_label.text() == text.SETTINGS_SAVED_UNVERIFIED

    window.apply_settings(lambda: [], connection_verified=True)
    assert window.status_label.text() == text.SETTINGS_SAVED_VERIFIED
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
    tmp_path,
) -> None:
    qt_app = app()
    calls = []
    gui_thread = threading.get_ident()

    def load_state(database):
        calls.append(threading.get_ident())
        return InitialState([], False, None)

    monkeypatch.setattr("tatatuya.ui.app._load_initial_state", load_state)
    window = create_main_window(Database(tmp_path / "bootstrap.sqlite3"))
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


def test_configured_startup_refreshes_once_and_displays_fresh_reading() -> None:
    qt_app = app()
    calls = 0
    fresh_row = representative_row()

    def refresh():
        nonlocal calls
        calls += 1
        return [
            DeviceRefreshResult(
                fresh_row.device,
                fresh_row.latest_reading,
                fresh_row.latest_reading,
            )
        ]

    window = MainWindow(
        bootstrap_workflow=lambda: InitialState([], True, refresh)
    )
    window.show()
    window.load_initial_state()
    deadline = time.monotonic() + 2
    while (calls == 0 or window.active_threads) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert calls == 1
    assert window.table.rowCount() == 1
    assert window.table.rows[0].latest_reading == fresh_row.latest_reading
    assert window.status_label.text() == text.REFRESH_COMPLETE

    end = time.monotonic() + 0.05
    while time.monotonic() < end:
        qt_app.processEvents()
    assert calls == 1
    window.close()


def test_verified_settings_save_triggers_one_refresh_but_untested_save_does_not() -> None:
    qt_app = app()
    calls = 0
    row = representative_row()

    def refresh():
        nonlocal calls
        calls += 1
        return [DeviceRefreshResult(row.device, row.latest_reading, row.latest_reading)]

    window = MainWindow(settings_configured=False)
    window.show()
    window.apply_settings(
        refresh,
        connection_verified=False,
        refresh_when_verified=True,
    )
    qt_app.processEvents()
    assert calls == 0

    window.apply_settings(
        refresh,
        connection_verified=True,
        refresh_when_verified=True,
    )
    deadline = time.monotonic() + 2
    while (calls == 0 or window.active_threads) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert calls == 1
    assert window.table.rowCount() == 1
    assert window.status_label.text() == text.REFRESH_COMPLETE
    window.close()


def test_verified_dialog_save_refreshes_only_after_settings_commit(
    tmp_path, monkeypatch
) -> None:
    qt_app = app()
    database = Database(tmp_path / "tatatuya.sqlite3")
    settings = TuyaSettings(
        "client", "secret", "central_europe", Currency.RON
    )
    refresh_observations = []
    settings_load_threads = []
    gui_thread = threading.get_ident()
    row = representative_row()

    class FakeSettingsDialog(QObject):
        error_raised = Signal(object)
        settings_saved = Signal(object)

        def __init__(
            self, service, regions, parent=None, *, initial_settings=None
        ):
            super().__init__(parent)
            self.service = service

        def exec(self):
            saved = self.service.save(settings)
            self.settings_saved.emit(SavedSettings(saved, True))

    def initial_state(db):
        db.initialize()
        return InitialState([], False, None)

    def refresh(db, active_settings):
        with db.connect() as connection:
            persisted = SettingsRepository(connection).load_tuya()
        refresh_observations.append((active_settings, persisted))
        return [DeviceRefreshResult(row.device, row.latest_reading, row.latest_reading)]

    def prepare_settings(db):
        settings_load_threads.append(threading.get_ident())
        return _prepare_settings(db)

    monkeypatch.setattr("tatatuya.ui.app.SettingsDialog", FakeSettingsDialog)
    monkeypatch.setattr("tatatuya.ui.app._load_initial_state", initial_state)
    monkeypatch.setattr("tatatuya.ui.app._refresh_workflow", refresh)
    monkeypatch.setattr("tatatuya.ui.app._prepare_settings", prepare_settings)

    window = create_main_window(database)
    window.show()
    deadline = time.monotonic() + 2
    while (
        window.bootstrap_workflow is not None or window.active_threads
    ) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    window.settings_requested.emit()
    deadline = time.monotonic() + 2
    while (
        not refresh_observations or window.active_threads
    ) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert refresh_observations == [(settings, settings)]
    assert settings_load_threads and settings_load_threads[0] != gui_thread
    assert window.table.rowCount() == 1
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
    while (
        window.status_label.text() != text.REFRESH_COMPLETE
        or window.active_threads
    ) and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    assert attempts == 2
    assert window.content.currentWidget() is window.empty_state
    assert window.status_label.text() == text.REFRESH_COMPLETE
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
