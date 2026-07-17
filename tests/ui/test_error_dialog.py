import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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

    assert dialog.details is not None and not dialog.details.isVisible()
    assert dialog.details_toggle is not None
    dialog.details_toggle.click()
    qt_app.processEvents()
    assert dialog.details.isVisible()
    assert dialog.copy_button is not None and dialog.copy_button.isVisible()
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == "request-id=safe-diagnostic"
    assert dialog.details_toggle.text() == text.HIDE_TECHNICAL_DETAILS
    assert dialog.grab().save(str(tmp_path / "error-dialog.png"))
    assert dialog.minimumSizeHint().height() <= dialog.height()
    dialog.close()
