"""Reusable thread for blocking application workflows."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from tatatuya.domain.errors import UserFacingError


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
            self.failed.emit(
                UserFacingError(
                    "Eroare neașteptată",
                    "Operațiunea nu a putut fi finalizată. Încercați din nou.",
                    str(exc),
                )
            )
