"""Bounded, exact parsing for Tuya status-report history."""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol

from tatatuya.domain.energy import (
    MAX_CANONICAL_DECIMAL_CHARACTERS,
    canonical_decimal,
    canonical_decimal_length,
)
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.tuya.client import BoundedPayload, TuyaAPIError
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.services.ports import CloudReportEvent


PAGE_SIZE = 99
MAX_PAGES = 50
MAX_ROWS = 4_950
MAX_TOTAL_RAW = 10_485_760
MAX_TOTAL_DECODED = 10_485_760
MIN_REQUEST_INTERVAL_SECONDS = 0.250
MAX_CANONICAL_RAW_DECIMAL_CHARACTERS = MAX_CANONICAL_DECIMAL_CHARACTERS
MAX_REPORT_INTEGER = 9_223_372_036_854_775_807
DECIMAL_TEXT = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_MISSING = object()


class ReportLogPageClient(Protocol):
    def get_report_log_page(
        self,
        device_id: str,
        code: str,
        start_time_ms: int,
        end_time_ms: int,
        *,
        last_row_key: str | None,
        size: int,
        raw_allowance: int,
        decoded_allowance: int,
    ) -> BoundedPayload: ...


class TuyaReportLogGateway:
    def __init__(
        self,
        client: ReportLogPageClient,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.monotonic = monotonic

    def list_events(
        self,
        device_id: str,
        code: str,
        start_time_ms: int,
        end_time_ms: int,
        cancellation: CancellationContext,
    ) -> tuple[CloudReportEvent, ...]:
        if (
            not device_id
            or not code
            or start_time_ms < 0
            or end_time_ms < start_time_ms
        ):
            raise ValueError("invalid report-log query")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_rows: dict[tuple[str, str, int], Decimal] = {}
        events: list[CloudReportEvent] = []
        expected_total: int | None = None
        raw_remaining = MAX_TOTAL_RAW
        decoded_remaining = MAX_TOTAL_DECODED
        request_pacer = _RequestPacer(self.monotonic)

        for page_number in range(1, MAX_PAGES + 1):
            cancellation.checkpoint()
            bounded = self._fetch_with_rate_retry(
                device_id,
                code,
                start_time_ms,
                end_time_ms,
                cursor,
                raw_remaining,
                decoded_remaining,
                cancellation,
                request_pacer,
            )
            raw_remaining -= bounded.raw_bytes
            decoded_remaining -= bounded.decoded_characters
            page = bounded.payload
            returned_device = _aliased_page_field(
                page,
                ("device_id", "deviceId"),
                "identificatorul contorului",
            )
            if returned_device is not _MISSING:
                if not isinstance(returned_device, str) or not returned_device:
                    raise self._invalid(
                        "Identificatorul contorului din răspunsul Tuya nu este valid."
                    )
                if returned_device != device_id:
                    raise self._invalid("Tuya a returnat istoricul altui contor.")
            raw_has_more = _aliased_page_field(
                page,
                ("has_more", "hasMore"),
                "marcajul de paginare",
            )
            has_more = raw_has_more
            if not isinstance(has_more, bool):
                raise self._invalid("Marcajul de paginare Tuya nu este valid.")
            raw_rows = _aliased_page_field(page, ("logs",), "lista evenimentelor")
            if raw_rows is _MISSING and not has_more:
                rows: list[object] = []
            elif not isinstance(raw_rows, list):
                raise self._invalid("Lista evenimentelor Tuya nu este validă.")
            else:
                rows = raw_rows
            if has_more and not rows:
                raise self._invalid("Tuya a returnat o pagină goală înainte de final.")
            raw_total = _aliased_page_field(
                page,
                ("total",),
                "numărul total de evenimente",
            )
            if raw_total is not _MISSING:
                total = _non_negative_integer(raw_total, "total")
                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise self._invalid(
                        "Numărul total de evenimente s-a schimbat în timpul încărcării."
                    )
            if len(events) + len(rows) > MAX_ROWS:
                raise self._limit()
            for row in rows:
                event = _parse_event(row, device_id, code, start_time_ms, end_time_ms)
                identity = (event.device_id, event.code, event.event_time_ms)
                previous = seen_rows.get(identity)
                if previous is not None:
                    if previous != event.raw_value:
                        raise self._invalid(
                            "Tuya a returnat valori diferite pentru același eveniment."
                        )
                    continue
                seen_rows[identity] = event.raw_value
                events.append(event)
            if not has_more:
                return tuple(sorted(events, key=lambda item: item.event_time_ms))
            next_cursor = _aliased_page_field(
                page,
                ("last_row_key", "lastRowKey"),
                "cursorul de paginare",
            )
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise self._invalid("Cursorul de paginare Tuya nu este valid.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise self._limit()

    def _fetch_with_rate_retry(
        self,
        device_id: str,
        code: str,
        start_time_ms: int,
        end_time_ms: int,
        cursor: str | None,
        raw_allowance: int,
        decoded_allowance: int,
        cancellation: CancellationContext,
        request_pacer: "_RequestPacer",
    ) -> BoundedPayload:
        for attempt, delay in enumerate((0.0, 0.5, 1.0)):
            if delay:
                cancellation.wait(delay)
            request_pacer.wait_before_request(cancellation)
            try:
                return self.client.get_report_log_page(
                    device_id,
                    code,
                    start_time_ms,
                    end_time_ms,
                    last_row_key=cursor,
                    size=PAGE_SIZE,
                    raw_allowance=raw_allowance,
                    decoded_allowance=decoded_allowance,
                )
            except TuyaAPIError as exc:
                if attempt >= 2 or not _is_rate_limit(exc):
                    raise _translate_api_error(exc) from exc
        raise AssertionError("rate retry loop did not return")

    @staticmethod
    def _invalid(message: str) -> UserFacingError:
        return UserFacingError("Istoric Tuya invalid", message)

    @staticmethod
    def _limit() -> UserFacingError:
        return UserFacingError(
            "Prea multe date Tuya",
            "Tuya a returnat prea multe date pentru ultimele 7 zile. Încercați din nou mai târziu.",
        )


def _parse_event(
    row: object,
    device_id: str,
    expected_code: str,
    start_time_ms: int,
    end_time_ms: int,
) -> CloudReportEvent:
    if not isinstance(row, Mapping):
        raise TuyaReportLogGateway._invalid("Un eveniment Tuya nu este valid.")
    code = row.get("code")
    if code != expected_code:
        raise TuyaReportLogGateway._invalid(
            "Tuya a returnat un cod de energie neașteptat."
        )
    timestamp = _non_negative_integer(row.get("event_time"), "event_time")
    if timestamp < start_time_ms or timestamp > end_time_ms:
        raise TuyaReportLogGateway._invalid(
            "Tuya a returnat un eveniment în afara intervalului."
        )
    raw_value = parse_report_decimal(row.get("value"))
    return CloudReportEvent(
        device_id,
        str(code),
        timestamp,
        raw_value,
        _diagnostic_json(row),
    )


def parse_report_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise TuyaReportLogGateway._invalid(
            "O valoare istorică de energie nu este numerică."
        )
    if isinstance(value, Decimal):
        decimal = value
        if decimal.is_zero() and decimal.is_signed():
            raise TuyaReportLogGateway._invalid(
                "O valoare istorică de energie nu este validă."
            )
        text = str(value)
    elif isinstance(value, int):
        decimal = Decimal(value)
        text = str(value)
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 128 or DECIMAL_TEXT.fullmatch(value) is None:
            raise TuyaReportLogGateway._invalid(
                "O valoare istorică de energie nu este validă."
            )
        text = value
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise TuyaReportLogGateway._invalid(
                "O valoare istorică de energie nu este validă."
            ) from exc
    else:
        raise TuyaReportLogGateway._invalid(
            "O valoare istorică de energie nu este numerică."
        )
    if (
        len(text.encode("utf-8")) > 128
        or not decimal.is_finite()
        or decimal < 0
        or len(decimal.as_tuple().digits) > 64
        or canonical_decimal_length(decimal) > MAX_CANONICAL_RAW_DECIMAL_CHARACTERS
    ):
        raise TuyaReportLogGateway._invalid(
            "O valoare istorică de energie depășește limitele acceptate."
        )
    return decimal


class _RequestPacer:
    """Apply one cadence to every physical report-log transport attempt."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self.monotonic = monotonic
        self.last_start: float | None = None

    def wait_before_request(self, cancellation: CancellationContext) -> None:
        cancellation.checkpoint()
        now = self.monotonic()
        if self.last_start is not None:
            delay = MIN_REQUEST_INTERVAL_SECONDS - (now - self.last_start)
            if delay > 0:
                cancellation.wait(delay)
                now = self.monotonic()
        cancellation.checkpoint()
        self.last_start = now


def _non_negative_integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise TuyaReportLogGateway._invalid(
            f"Câmpul {field} nu este un număr întreg valid."
        )
    if isinstance(value, Decimal):
        if (
            not value.is_finite()
            or value.is_signed()
            or value != value.to_integral_value()
            or value > MAX_REPORT_INTEGER
        ):
            raise TuyaReportLogGateway._invalid(
                f"Câmpul {field} nu este un număr întreg valid."
            )
        return int(value)
    if isinstance(value, int) and 0 <= value <= MAX_REPORT_INTEGER:
        return value
    raise TuyaReportLogGateway._invalid(
        f"Câmpul {field} nu este un număr întreg valid."
    )


def _aliased_page_field(
    page: Mapping[str, Any],
    names: tuple[str, ...],
    description: str,
) -> object:
    present = [(name, page[name]) for name in names if name in page]
    if not present:
        return _MISSING
    first_value = present[0][1]
    if any(value != first_value for _, value in present[1:]):
        raise TuyaReportLogGateway._invalid(
            f"Tuya a returnat valori contradictorii pentru {description}."
        )
    return first_value


def _diagnostic_json(value: Mapping[str, Any]) -> str:
    def normalize(item: object) -> object:
        if isinstance(item, Decimal):
            if (
                not item.is_finite()
                or canonical_decimal_length(item) > MAX_CANONICAL_RAW_DECIMAL_CHARACTERS
            ):
                raise TuyaReportLogGateway._invalid(
                    "Un câmp numeric din evenimentul Tuya depășește limitele acceptate."
                )
            return canonical_decimal(item)
        if isinstance(item, Mapping):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        return item

    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_rate_limit(error: TuyaAPIError) -> bool:
    payload = error.response_payload
    code = payload.get("code") if isinstance(payload, Mapping) else None
    return code in {429, "429", 1010, "1010", "TOO_MANY_REQUESTS"}


def _translate_api_error(error: TuyaAPIError) -> UserFacingError:
    if _is_rate_limit(error):
        return UserFacingError(
            "Tuya este ocupat",
            "Limita de cereri Tuya a fost atinsă. Încercați din nou mai târziu.",
        )
    return UserFacingError(
        "Istoric Tuya indisponibil",
        "Istoricul contorului nu a putut fi încărcat. Verificați serviciul și permisiunile proiectului Tuya.",
        str(error),
    )
