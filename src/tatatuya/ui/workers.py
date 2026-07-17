"""Reusable thread for blocking application workflows."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from tatatuya.domain.errors import UserFacingError


LOGGER_NAME = "tatatuya"


def log_unexpected_exception(error: BaseException) -> None:
    """Log only static exception metadata, never its dynamic message."""
    frames = traceback.extract_tb(error.__traceback__)
    locations = " -> ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in frames
    )
    logging.getLogger(LOGGER_NAME).error(
        "Unexpected exception type=%s stack=%s",
        type(error).__name__,
        locations or "unavailable",
    )


class WorkflowThread(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, call: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self.call = call

    def run(self) -> None:
        try:
            self.succeeded.emit(self.call())
        except UserFacingError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            log_unexpected_exception(exc)
            self.failed.emit(
                UserFacingError(
                    "Eroare neașteptată",
                    "Operațiunea nu a putut fi finalizată. Încercați din nou.",
                )
            )
