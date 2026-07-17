"""Romanian settings dialog for Tuya credentials and application currency."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QComboBox,
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
from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.services.settings_service import ConnectionTestResult, SettingsService
from tatatuya.ui import text
from tatatuya.ui.workers import WorkflowThread


REGION_LABELS = {
    "central_europe": "Europa Centrală",
    "western_europe": "Europa de Vest",
    "western_america": "America de Vest",
    "eastern_america": "America de Est",
    "china": "China",
    "india": "India",
}


@dataclass(frozen=True, slots=True)
class SavedSettings:
    settings: TuyaSettings
    connection_verified: bool


class SettingsComboBox(QComboBox):
    """Combo whose native popup remains readable under a dark system palette."""

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        super().showPopup()
        popup = self.view().window()
        popup.setObjectName("ComboPopup")
        view_palette = self.view().palette()
        palette = popup.palette()
        palette.setColor(
            QPalette.ColorRole.Window,
            view_palette.color(QPalette.ColorRole.Base),
        )
        palette.setColor(
            QPalette.ColorRole.Base,
            view_palette.color(QPalette.ColorRole.Base),
        )
        palette.setColor(
            QPalette.ColorRole.WindowText,
            view_palette.color(QPalette.ColorRole.Text),
        )
        palette.setColor(
            QPalette.ColorRole.Text,
            view_palette.color(QPalette.ColorRole.Text),
        )
        popup.setPalette(palette)
        popup.setAutoFillBackground(True)


class SettingsDialog(QDialog):
    settings_saved = Signal(object)
    error_raised = Signal(object)

    def __init__(
        self,
        service: SettingsService,
        regions: Mapping[str, str],
        parent: QWidget | None = None,
        *,
        initial_settings: TuyaSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.regions = dict(regions)
        self.active_thread: WorkflowThread | None = None
        self._active_operation: str | None = None
        self._pending_saved: SavedSettings | None = None
        self._save_was_verified = False
        self._close_when_idle = False
        self._verified_settings: TuyaSettings | None = None
        self._has_test_result = False
        self.setWindowTitle(text.SETTINGS)
        self.setModal(True)
        self.resize(620, 540)
        self._build_ui()
        self._populate(initial_settings)
        self._connect_verification_invalidation()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)

        title = QLabel(text.SETTINGS)
        title.setObjectName("ModalTitle")
        layout.addWidget(title)
        subtitle = QLabel(text.SETTINGS_SUBTITLE)
        subtitle.setObjectName("ModalSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("FieldPanel")
        form = QFormLayout(panel)
        form.setContentsMargins(18, 16, 18, 16)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.client_id = QLineEdit()
        self.client_id.setObjectName("ClientIdField")
        self.client_secret = QLineEdit()
        self.client_secret.setObjectName("ClientSecretField")
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.region = SettingsComboBox()
        self.region.setObjectName("RegionField")
        for code, label in self.regions.items():
            self.region.addItem(label, code)
        self.currency = SettingsComboBox()
        self.currency.setObjectName("CurrencyField")
        self.currency.addItem("Leu românesc (RON)", Currency.RON)
        self.currency.addItem("Euro (EUR)", Currency.EUR)

        form.addRow(self._field_label("Client ID"), self.client_id)
        form.addRow(self._field_label("Client Secret"), self.client_secret)
        form.addRow(self._field_label("Regiune Tuya"), self.region)
        form.addRow(self._field_label("Monedă"), self.currency)
        layout.addWidget(panel)

        self.feedback = QLabel(text.SETTINGS_NOT_TESTED)
        self.feedback.setObjectName("SettingsFeedback")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        layout.addStretch()

        actions = QHBoxLayout()
        self.test_button = QPushButton(text.TEST_CONNECTION)
        self.test_button.setObjectName("SecondaryButton")
        self.test_button.clicked.connect(self.test_connection)
        self.close_button = QPushButton(text.CLOSE)
        self.close_button.setObjectName("SecondaryButton")
        self.close_button.clicked.connect(self.reject)
        self.save_button = QPushButton(text.SAVE)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.test_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

    @staticmethod
    def _field_label(label: str) -> QLabel:
        widget = QLabel(label)
        widget.setObjectName("SettingsFieldLabel")
        return widget

    def _connect_verification_invalidation(self) -> None:
        for field in (self.client_id, self.client_secret):
            field.textChanged.connect(self._invalidate_verification)
        self.region.currentIndexChanged.connect(self._invalidate_verification)

    def _populate(self, settings: TuyaSettings | None) -> None:
        if settings is None:
            self.region.setCurrentIndex(0)
            self.currency.setCurrentIndex(0)
            return
        self.client_id.setText(settings.client_id)
        self.client_secret.setText(settings.client_secret)
        region_index = self.region.findData(settings.region)
        if region_index >= 0:
            self.region.setCurrentIndex(region_index)
        currency_index = self.currency.findData(settings.currency)
        if currency_index >= 0:
            self.currency.setCurrentIndex(currency_index)

    def current_settings(self) -> TuyaSettings:
        currency = Currency(str(self.currency.currentData() or Currency.RON.value))
        return TuyaSettings(
            self.client_id.text(),
            self.client_secret.text(),
            str(self.region.currentData() or ""),
            currency,
        )

    def test_connection(self) -> None:
        if self.active_thread is not None:
            return
        settings = self.current_settings()
        self._has_test_result = False
        self.feedback.setText(text.TESTING_CONNECTION)
        self.feedback.setProperty("state", "")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self._set_actions_enabled(False)
        self._active_operation = "test"
        thread = WorkflowThread(lambda: self.service.test_connection(settings), self)
        thread.succeeded.connect(self._operation_succeeded)
        thread.failed.connect(self._operation_failed)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.active_thread = thread
        thread.start()

    def _operation_succeeded(self, payload: object) -> None:
        if self._active_operation == "test":
            self._test_succeeded(payload)
            return
        if self._active_operation == "save":
            if isinstance(payload, TuyaSettings):
                self._pending_saved = SavedSettings(
                    payload, self._save_was_verified
                )
            else:
                self._operation_failed(
                    UserFacingError(
                        "Salvare nereușită",
                        "Setările nu au putut fi confirmate după salvare.",
                    )
                )

    def _operation_failed(self, error: UserFacingError) -> None:
        if self._active_operation == "test":
            self._test_failed(error)
            return
        self.feedback.setText(text.SETTINGS_SAVE_FAILED)
        self.feedback.setProperty("state", "error")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.error_raised.emit(error)

    def _test_succeeded(self, payload: object) -> None:
        if not isinstance(payload, ConnectionTestResult):
            self._test_failed(
                UserFacingError(
                    "Conexiunea Tuya nu a reușit",
                    "Răspunsul testului de conexiune nu a putut fi verificat.",
                )
            )
            return
        try:
            current = self.service.validate(self.current_settings())
        except UserFacingError:
            current = None
        if current is None or _connection_key(current) != _connection_key(payload.settings):
            self._verified_settings = None
            self._has_test_result = True
            self.feedback.setText(text.SETTINGS_CHANGED_AFTER_TEST)
            self.feedback.setProperty("state", "")
            self.feedback.style().unpolish(self.feedback)
            self.feedback.style().polish(self.feedback)
            return
        self._verified_settings = payload.settings
        self._has_test_result = True
        count = payload.device_count
        self.feedback.setText(
            text.CONNECTION_SUCCEEDED_ONE
            if count == 1
            else text.CONNECTION_SUCCEEDED.format(count=count)
        )
        self.feedback.setProperty("state", "success")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)

    def _test_failed(self, error: UserFacingError) -> None:
        self._verified_settings = None
        self._has_test_result = True
        self.feedback.setText(text.CONNECTION_FAILED)
        self.feedback.setProperty("state", "error")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.error_raised.emit(error)

    def _thread_finished(self) -> None:
        self.active_thread = None
        self._active_operation = None
        if self._pending_saved is not None:
            saved = self._pending_saved
            self._pending_saved = None
            self.settings_saved.emit(saved)
            super().accept()
            return
        if self._close_when_idle:
            super().reject()
        else:
            self._set_actions_enabled(True)

    def _invalidate_verification(self) -> None:
        if not self._has_test_result:
            return
        self._verified_settings = None
        self._has_test_result = False
        self.feedback.setText(text.SETTINGS_CHANGED_AFTER_TEST)
        self.feedback.setProperty("state", "")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)

    def save(self) -> None:
        if self.active_thread is not None:
            return
        try:
            settings = self.service.validate(self.current_settings())
        except UserFacingError as error:
            self.error_raised.emit(error)
            return
        self._save_was_verified = (
            self._verified_settings is not None
            and _connection_key(settings) == _connection_key(self._verified_settings)
        )
        self.feedback.setText(text.SAVING_SETTINGS)
        self._set_actions_enabled(False)
        self._active_operation = "save"
        thread = WorkflowThread(lambda: self.service.save(settings), self)
        thread.succeeded.connect(self._operation_succeeded)
        thread.failed.connect(self._operation_failed)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.active_thread = thread
        thread.start()

    def _set_actions_enabled(self, enabled: bool) -> None:
        for field in (
            self.client_id,
            self.client_secret,
            self.region,
            self.currency,
        ):
            field.setEnabled(enabled)
        self.test_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self.active_thread is not None:
            self._close_when_idle = True
            self.active_thread.requestInterruption()
            event.ignore()
            return
        event.accept()

    def reject(self) -> None:
        if self.active_thread is None:
            super().reject()
            return
        self._close_when_idle = True
        self.active_thread.requestInterruption()

    def accept(self) -> None:
        if self.active_thread is None:
            super().accept()


def _connection_key(settings: TuyaSettings) -> tuple[str, str, str]:
    return (
        settings.client_id,
        settings.client_secret,
        settings.region,
    )
