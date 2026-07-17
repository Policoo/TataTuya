"""Shared Romanian error dialog with optional copyable diagnostics."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tatatuya.domain.errors import UserFacingError
from tatatuya.ui import text


class ErrorDialog(QDialog):
    def __init__(
        self,
        error: UserFacingError,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorDialog")
        self.setWindowTitle(error.title)
        self.setModal(True)
        self.setMinimumWidth(520)

        self.details_toggle: QPushButton | None = None
        self.details_panel: QFrame | None = None
        self.details: QPlainTextEdit | None = None
        self.copy_button: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)

        summary = QFrame()
        summary.setObjectName("ErrorSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(14)

        symbol = QLabel("!")
        symbol.setObjectName("ErrorSymbol")
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        summary_layout.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)

        message_layout = QVBoxLayout()
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(6)
        title = QLabel(error.title)
        title.setObjectName("ErrorTitle")
        title.setWordWrap(True)
        message = QLabel(error.message)
        message.setObjectName("ErrorMessage")
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message_layout.addWidget(title)
        message_layout.addWidget(message)
        summary_layout.addLayout(message_layout, 1)
        layout.addWidget(summary)

        if error.technical_details:
            self.details_toggle = QPushButton(text.SHOW_TECHNICAL_DETAILS)
            self.details_toggle.setObjectName("ErrorDetailsToggle")
            self.details_toggle.setCheckable(True)
            self.details_toggle.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )
            self.details_toggle.toggled.connect(self._toggle_details)
            layout.addWidget(self.details_toggle, 0, Qt.AlignmentFlag.AlignLeft)

            self.details_panel = QFrame()
            self.details_panel.setObjectName("ErrorDetailsPanel")
            details_layout = QVBoxLayout(self.details_panel)
            details_layout.setContentsMargins(14, 12, 14, 14)
            details_layout.setSpacing(10)

            details_header = QHBoxLayout()
            details_title = QLabel(text.TECHNICAL_DETAILS)
            details_title.setObjectName("ErrorDetailsTitle")
            details_header.addWidget(details_title)
            details_header.addStretch()
            self.copy_button = QPushButton(text.COPY_TECHNICAL_DETAILS)
            self.copy_button.setObjectName("SecondaryButton")
            self.copy_button.clicked.connect(self._copy_details)
            details_header.addWidget(self.copy_button)
            details_layout.addLayout(details_header)

            self.details = QPlainTextEdit(error.technical_details)
            self.details.setObjectName("ErrorTechnicalDetails")
            self.details.setReadOnly(True)
            self.details.setMinimumHeight(
                self.details.fontMetrics().lineSpacing() * 7
            )
            details_layout.addWidget(self.details, 1)
            self.details_panel.setVisible(False)
            layout.addWidget(self.details_panel, 1)

        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText(text.CLOSE)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(620, self.sizeHint().height())

    def _toggle_details(self, visible: bool) -> None:
        if self.details_panel is not None:
            self.details_panel.setVisible(visible)
        if self.details_toggle is not None:
            self.details_toggle.setText(
                text.HIDE_TECHNICAL_DETAILS if visible else text.SHOW_TECHNICAL_DETAILS
            )
        if visible:
            expanded = self.sizeHint()
            self.resize(max(self.width(), 680, expanded.width()), expanded.height())
        else:
            compact = self.sizeHint()
            self.resize(max(self.minimumWidth(), compact.width()), compact.height())

    def _copy_details(self) -> None:
        if self.details is not None:
            QApplication.clipboard().setText(self.details.toPlainText())
