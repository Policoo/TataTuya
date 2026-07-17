import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.logging_setup import LOGGER_NAME
from tatatuya.ui import text
from tatatuya.ui.app import load_stylesheet
from tatatuya.ui.dialogs.error import ErrorDialog
from tatatuya.ui.workers import WorkflowThread


def app() -> QApplication:
    existing = QApplication.instance()
    instance = existing if isinstance(existing, QApplication) else QApplication([])
    instance.setStyleSheet(load_stylesheet())
    return instance


def test_unexpected_worker_error_redacts_ui_and_logs(caplog) -> None:
    qt_app = app()
    secret = "synthetic-client-secret"
    errors = []
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)

    def fail():
        raise RuntimeError(f"upstream failed with {secret}")

    thread = WorkflowThread(fail)
    thread.failed.connect(errors.append)
    thread.start()
    deadline = time.monotonic() + 2
    while thread.isRunning() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    thread.wait()
    qt_app.processEvents()

    assert len(errors) == 1
    assert errors[0].title == "Eroare neașteptată"
    assert errors[0].technical_details is None
    assert secret not in caplog.text
    assert "upstream failed" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_error_details_expand_copy_and_render(tmp_path) -> None:
    qt_app = app()
    error = UserFacingError(
        "Citire indisponibilă",
        "Verificați conexiunea și încercați din nou.",
        "request-id=safe-diagnostic",
    )
    dialog = ErrorDialog(error)
    dialog.show()
    qt_app.processEvents()

    summary = dialog.findChild(QFrame, "ErrorSummary")
    title = dialog.findChild(QLabel, "ErrorTitle")
    message = dialog.findChild(QLabel, "ErrorMessage")
    assert summary is not None and summary.isVisible()
    assert title is not None and title.text() == error.title
    assert message is not None and message.text() == error.message
    assert title.palette().color(QPalette.ColorRole.WindowText) == QColor("#7f1d1d")
    assert dialog.details is not None and not dialog.details.isVisible()
    assert dialog.details_panel is not None and not dialog.details_panel.isVisible()
    assert dialog.details_toggle is not None
    assert dialog.details_toggle.width() <= dialog.details_toggle.sizeHint().width()
    dialog.details_toggle.click()
    qt_app.processEvents()
    assert dialog.details.isVisible()
    assert dialog.details_panel.isVisible()
    assert dialog.copy_button is not None and dialog.copy_button.isVisible()
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == "request-id=safe-diagnostic"
    assert dialog.details_toggle.text() == text.HIDE_TECHNICAL_DETAILS
    screenshot = QPixmap(dialog.size())
    screenshot.fill(QColor("#f8fafc"))
    dialog.render(screenshot)
    assert screenshot.save(str(tmp_path / "error-dialog.png"))
    assert dialog.minimumSizeHint().height() <= dialog.height()
    dialog.close()


def test_error_without_diagnostics_is_compact_and_clear(tmp_path) -> None:
    qt_app = app()
    error = UserFacingError(
        "Eroare neașteptată",
        "Operațiunea nu a putut fi finalizată. Încercați din nou.",
    )
    dialog = ErrorDialog(error)
    dialog.show()
    qt_app.processEvents()

    assert dialog.details_toggle is None
    assert dialog.details_panel is None
    assert dialog.height() < 360
    screenshot = QPixmap(dialog.size())
    screenshot.fill(QColor("#f8fafc"))
    dialog.render(screenshot)
    assert screenshot.save(str(tmp_path / "error-dialog-compact.png"))
    dialog.close()


def test_error_dialog_remains_readable_under_dark_palette(tmp_path) -> None:
    qt_app = app()
    original = qt_app.palette()
    dark = QPalette(original)
    dark.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#f8fafc"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#101114"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#f8fafc"))
    qt_app.setPalette(dark)
    qt_app.setStyleSheet(load_stylesheet())
    dialog = None
    try:
        warmup = ErrorDialog(
            UserFacingError("Eroare", "Mesaj de verificare pentru paletă.")
        )
        warmup.show()
        qt_app.processEvents()
        warmup.close()
        dialog = ErrorDialog(
            UserFacingError(
                "Conexiune Tuya nereușită",
                "Verificați conexiunea și setările Tuya, apoi încercați din nou.",
                "request-id=safe-diagnostic",
            )
        )
        dialog.show()
        qt_app.processEvents()
        title = dialog.findChild(QLabel, "ErrorTitle")
        message = dialog.findChild(QLabel, "ErrorMessage")
        assert title is not None
        assert message is not None
        assert title.palette().color(QPalette.ColorRole.WindowText) == QColor(
            "#7f1d1d"
        )
        assert message.palette().color(QPalette.ColorRole.WindowText) == QColor(
            "#475467"
        )
        assert dialog.details_toggle is not None
        dialog.details_toggle.click()
        qt_app.processEvents()
        assert dialog.details is not None
        assert dialog.details.palette().color(QPalette.ColorRole.Text) == QColor(
            "#182230"
        )
        screenshot = QPixmap(dialog.size())
        screenshot.fill(QColor("#f8fafc"))
        dialog.render(screenshot)
        assert screenshot.save(str(tmp_path / "error-dialog-dark.png"))
    finally:
        if dialog is not None:
            dialog.close()
        qt_app.setPalette(original)
        qt_app.setStyleSheet(load_stylesheet())
