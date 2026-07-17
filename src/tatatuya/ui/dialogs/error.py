"""Shared Romanian error dialog with optional copyable diagnostics."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from tatatuya.domain.errors import UserFacingError
from tatatuya.ui import text
from tatatuya.ui.components.modal import AppModal


class ErrorDialog(AppModal):
    def __init__(
        self,
        error: UserFacingError,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(error.title, error.message, parent)
        self.resize(680, 440)
        self.details_toggle: QPushButton | None = None
        self.details: QPlainTextEdit | None = None
        self.copy_button: QPushButton | None = None

        if error.technical_details:
            self.details_toggle = QPushButton(text.SHOW_TECHNICAL_DETAILS)
            self.details_toggle.setObjectName("SecondaryButton")
            self.details_toggle.setCheckable(True)
            self.details_toggle.toggled.connect(self._toggle_details)
            self.content_layout.addWidget(self.details_toggle)

            self.details = QPlainTextEdit(error.technical_details)
            self.details.setObjectName("ErrorTechnicalDetails")
            self.details.setReadOnly(True)
            self.details.setVisible(False)
            self.content_layout.addWidget(self.details)

            actions = QHBoxLayout()
            actions.addStretch()
            self.copy_button = QPushButton(text.COPY_TECHNICAL_DETAILS)
            self.copy_button.setObjectName("SecondaryButton")
            self.copy_button.clicked.connect(self._copy_details)
            self.copy_button.setVisible(False)
            actions.addWidget(self.copy_button)
            self.content_layout.addLayout(actions)

        buttons = self.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.button(QDialogButtonBox.StandardButton.Close).setText(text.CLOSE)

    def _toggle_details(self, visible: bool) -> None:
        if self.details is not None:
            self.details.setVisible(visible)
        if self.copy_button is not None:
            self.copy_button.setVisible(visible)
        if self.details_toggle is not None:
            self.details_toggle.setText(
                text.HIDE_TECHNICAL_DETAILS if visible else text.SHOW_TECHNICAL_DETAILS
            )

    def _copy_details(self) -> None:
        if self.details is not None:
            QApplication.clipboard().setText(self.details.toPlainText())
