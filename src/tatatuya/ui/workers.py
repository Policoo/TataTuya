"""Background workers for Tuya API calls."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from tatatuya.infrastructure.tuya_legacy import TuyaAPIError, TuyaConfigError


class ApiWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str, dict, object)
    finished = Signal()

    def __init__(self, call: Callable[[], Any]) -> None:
        super().__init__()
        self.call = call

    def run(self) -> None:
        try:
            self.succeeded.emit(self.call())
        except (TuyaAPIError, TuyaConfigError) as exc:
            request_info = exc.request_info if isinstance(exc, TuyaAPIError) else {}
            response_payload = exc.response_payload if isinstance(exc, TuyaAPIError) else None
            self.failed.emit(str(exc), request_info, response_payload)
        except Exception as exc:
            self.failed.emit(str(exc), {}, None)
        finally:
            self.finished.emit()
