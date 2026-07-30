"""Reusable thread for blocking application workflows."""

from __future__ import annotations

import logging
import inspect
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.cancellation import CancellationContext


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

    def __init__(
        self,
        call: Callable[..., Any],
        parent=None,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(parent)
        self.call = call
        self.cancellation = CancellationContext(timeout_seconds)

    def requestInterruption(self) -> None:  # noqa: N802 - Qt API compatibility
        self.cancellation.cancel()
        super().requestInterruption()

    def run(self) -> None:
        try:
            parameters = inspect.signature(self.call).parameters
            result = (
                self.call(self.cancellation)
                if parameters
                else self.call()
            )
            self.cancellation.checkpoint()
            self.succeeded.emit(result)
        except UserFacingError as exc:
            if not self.cancellation.cancelled:
                self.failed.emit(exc)
        except Exception as exc:
            log_unexpected_exception(exc)
            self.failed.emit(
                UserFacingError(
                    "Eroare neașteptată",
                    "Operațiunea nu a putut fi finalizată. Încercați din nou.",
                )
            )


class WorkerOwner(QObject):
    """Application-scope lifetime owner for every workflow thread."""

    all_finished = Signal()
    active_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._threads: set[WorkflowThread] = set()

    @property
    def active(self) -> bool:
        return bool(self._threads)

    def track(self, thread: WorkflowThread) -> WorkflowThread:
        if thread in self._threads:
            return thread
        was_active = self.active
        self._threads.add(thread)
        thread.finished.connect(lambda: self._finished(thread))
        if not was_active:
            self.active_changed.emit(True)
        return thread

    def cancel_all(self) -> None:
        for thread in tuple(self._threads):
            thread.requestInterruption()

    def _finished(self, thread: WorkflowThread) -> None:
        self._threads.discard(thread)
        if not self._threads:
            self.active_changed.emit(False)
            self.all_finished.emit()
