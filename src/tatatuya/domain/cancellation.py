"""UI-independent cooperative cancellation and workflow deadlines."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from tatatuya.domain.errors import UserFacingError


class CancellationContext:
    """A thread-safe cancellation signal with one monotonic deadline."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._monotonic = monotonic
        self._deadline = monotonic() + timeout_seconds
        self._cancelled = threading.Event()
        self._remote_reserve_seconds = 0.0

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - self._monotonic())

    def remote_timeout_seconds(self, maximum: float = 5.0) -> float:
        return max(
            0.0,
            min(maximum, self.remaining_seconds() - self._remote_reserve_seconds),
        )

    @contextmanager
    def reserve_after_remote(self, seconds: float) -> Iterator[None]:
        """Reserve deadline time for a mandatory post-response stage."""

        self.checkpoint()
        previous = self._remote_reserve_seconds
        self._remote_reserve_seconds = max(previous, seconds)
        if self.remote_timeout_seconds() <= 0:
            self._remote_reserve_seconds = previous
            raise UserFacingError(
                "Operațiune expirată",
                "Nu a rămas suficient timp pentru salvarea sigură a citirii.",
            )
        try:
            yield
        finally:
            self._remote_reserve_seconds = previous

    def checkpoint(self) -> None:
        if self.cancelled:
            raise UserFacingError(
                "Operațiune anulată",
                "Operațiunea a fost anulată.",
            )
        if self.remaining_seconds() <= 0:
            raise UserFacingError(
                "Operațiune expirată",
                "Operațiunea a durat prea mult. Încercați din nou.",
            )

    def wait(self, seconds: float) -> None:
        self.checkpoint()
        duration = min(max(0.0, seconds), self.remaining_seconds())
        if self._cancelled.wait(duration):
            self.checkpoint()
        self.checkpoint()


def uncancelled_context(timeout_seconds: float = 120.0) -> CancellationContext:
    """Create a context for non-UI callers that still need finite work."""

    return CancellationContext(timeout_seconds)
