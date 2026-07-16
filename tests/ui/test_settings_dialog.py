import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.services.settings_service import SettingsService
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.settings import REGION_LABELS, SavedSettings, SettingsDialog


class MemorySettings:
    def __init__(self, value=None) -> None:
        self.value = value

    def load_tuya(self):
        return self.value

    def save_tuya(self, settings, updated_at_utc=None) -> None:
        self.value = settings


class Gateway:
    def authenticate(self):
        time.sleep(0.03)
        return "token"

    def list_devices(self, **params):
        return [object()]


def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def configured_settings() -> TuyaSettings:
    return TuyaSettings(
        "client-id", "client-secret", "central_europe", Currency.EUR
    )


def test_settings_dialog_loads_fields_and_has_usable_geometry(tmp_path) -> None:
    qt_app = app()
    store = MemorySettings(configured_settings())
    service = SettingsService(store, lambda value: Gateway(), REGION_LABELS)
    dialog = SettingsDialog(service, REGION_LABELS)
    dialog.show()
    qt_app.processEvents()

    assert dialog.client_id.text() == "client-id"
    assert dialog.client_secret.text() == "client-secret"
    assert dialog.client_secret.echoMode() == dialog.client_secret.EchoMode.Password
    assert dialog.region.currentData() == "central_europe"
    assert dialog.currency.currentData() == Currency.EUR
    for control in (
        dialog.client_id,
        dialog.client_secret,
        dialog.region,
        dialog.currency,
        dialog.test_button,
        dialog.save_button,
    ):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()

    screenshot_path = tmp_path / "settings-dialog.png"
    assert dialog.grab().save(str(screenshot_path))
    assert screenshot_path.stat().st_size > 8_000
    dialog.close()


def test_connection_test_runs_async_and_restores_actions() -> None:
    qt_app = app()
    store = MemorySettings(configured_settings())
    service = SettingsService(store, lambda value: Gateway(), REGION_LABELS)
    dialog = SettingsDialog(service, REGION_LABELS)
    results = []
    dialog.settings_saved.connect(results.append)
    dialog.show()

    dialog.test_connection()
    assert dialog.active_thread is not None
    assert not dialog.test_button.isEnabled()
    assert not dialog.client_id.isEnabled()
    assert dialog.feedback.text() == text.TESTING_CONNECTION

    deadline = time.monotonic() + 2
    while dialog.active_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()

    assert dialog.active_thread is None
    assert dialog.test_button.isEnabled()
    assert dialog.feedback.text() == text.CONNECTION_SUCCEEDED_ONE
    dialog.save()
    assert results == [SavedSettings(configured_settings(), True)]


def test_edit_after_success_invalidates_the_verified_snapshot() -> None:
    qt_app = app()
    store = MemorySettings(configured_settings())
    service = SettingsService(store, lambda value: Gateway(), REGION_LABELS)
    dialog = SettingsDialog(service, REGION_LABELS)
    results = []
    dialog.settings_saved.connect(results.append)
    dialog.show()
    dialog.test_connection()

    deadline = time.monotonic() + 2
    while dialog.active_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    dialog.client_id.setText("changed-client")
    qt_app.processEvents()

    assert dialog.feedback.text() == text.SETTINGS_CHANGED_AFTER_TEST
    dialog.save()
    assert results and not results[0].connection_verified


def test_currency_change_keeps_the_tuya_connection_verified() -> None:
    qt_app = app()
    store = MemorySettings(configured_settings())
    service = SettingsService(store, lambda value: Gateway(), REGION_LABELS)
    dialog = SettingsDialog(service, REGION_LABELS)
    results = []
    dialog.settings_saved.connect(results.append)
    dialog.show()
    dialog.test_connection()

    deadline = time.monotonic() + 2
    while dialog.active_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    dialog.currency.setCurrentIndex(dialog.currency.findData(Currency.RON))
    qt_app.processEvents()

    assert dialog.feedback.text() == text.CONNECTION_SUCCEEDED_ONE
    dialog.save()
    assert results and results[0].connection_verified
    assert results[0].settings.currency is Currency.RON


def test_escape_during_connection_test_does_not_wait_on_gui_thread() -> None:
    qt_app = app()
    release = threading.Event()

    class BlockingGateway(Gateway):
        def authenticate(self):
            release.wait(timeout=2)
            return "token"

    service = SettingsService(
        MemorySettings(configured_settings()),
        lambda value: BlockingGateway(),
        REGION_LABELS,
    )
    dialog = SettingsDialog(service, REGION_LABELS)
    dialog.show()
    dialog.test_connection()

    started = time.monotonic()
    dialog.reject()
    elapsed = time.monotonic() - started
    qt_app.processEvents()

    assert elapsed < 0.05
    assert dialog.isVisible()
    assert dialog.active_thread is not None

    release.set()
    deadline = time.monotonic() + 2
    while dialog.active_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert not dialog.isVisible()


def test_dark_palette_form_labels_and_open_combo_are_readable(tmp_path) -> None:
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
    try:
        service = SettingsService(
            MemorySettings(configured_settings()), lambda value: Gateway(), REGION_LABELS
        )
        dialog = SettingsDialog(service, REGION_LABELS)
        dialog.show()
        qt_app.processEvents()

        labels = dialog.findChildren(QLabel, "SettingsFieldLabel")
        assert len(labels) == 4
        assert all(
            label.palette().color(QPalette.ColorRole.WindowText) == QColor("#344054")
            for label in labels
        )

        assert dialog.grab().save(str(tmp_path / "settings-dark.png"))
        for name, combo in (("region", dialog.region), ("currency", dialog.currency)):
            combo.showPopup()
            qt_app.processEvents()
            popup = combo.view()
            popup_window = popup.window()
            assert popup_window.objectName() == "ComboPopup"
            assert popup_window.palette().color(QPalette.ColorRole.Window) == QColor("#ffffff")
            assert popup_window.palette().color(QPalette.ColorRole.Base) == QColor("#ffffff")
            assert popup_window.palette().color(QPalette.ColorRole.Text) == QColor("#101828")
            assert popup.palette().color(QPalette.ColorRole.Base) == QColor("#ffffff")
            assert popup.palette().color(QPalette.ColorRole.Text) == QColor("#101828")
            assert popup.palette().color(QPalette.ColorRole.Highlight) == QColor("#dbeafe")
            assert popup.palette().color(QPalette.ColorRole.HighlightedText) == QColor("#101828")
            assert popup_window.grab().save(
                str(tmp_path / f"settings-popup-{name}-dark.png")
            )
            combo.hidePopup()
        dialog.close()
    finally:
        qt_app.setPalette(original)


def test_save_persists_and_emits_normalized_settings() -> None:
    qt_app = app()
    store = MemorySettings()
    service = SettingsService(store, lambda value: Gateway(), REGION_LABELS)
    dialog = SettingsDialog(service, REGION_LABELS)
    results = []
    dialog.settings_saved.connect(results.append)
    dialog.client_id.setText(" client ")
    dialog.client_secret.setText(" secret ")
    dialog.currency.setCurrentIndex(dialog.currency.findData(Currency.EUR))
    dialog.show()

    dialog.save_button.click()
    qt_app.processEvents()

    expected = TuyaSettings(
        "client", "secret", "central_europe", Currency.EUR
    )
    assert results == [SavedSettings(expected, False)]
    assert store.value == expected
    assert dialog.result() == dialog.DialogCode.Accepted
