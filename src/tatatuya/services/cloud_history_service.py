"""Validated sparse daily import of Tuya status-report history."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import Callable, Protocol

from tatatuya.domain.energy import canonical_decimal, normalize_energy
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, EnergySpecification, Reading
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.services.ports import CloudReportEvent


RECENT_LOCAL_DAYS = 7
MAX_NORMALIZED_KWH = Decimal("1E+30")


@dataclass(frozen=True, slots=True)
class HistoricalScaleContract:
    verified: bool
    evidence_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CloudImportResult:
    readings: tuple[Reading, ...]
    new_count: int
    reused_count: int


class SpecificationGateway(Protocol):
    def get_device_specification(self, device_id: str) -> EnergySpecification: ...


class ReportLogGateway(Protocol):
    def list_events(
        self,
        device_id: str,
        code: str,
        start_time_ms: int,
        end_time_ms: int,
        cancellation: CancellationContext,
    ) -> tuple[CloudReportEvent, ...]: ...


class DailyReadingStore(Protocol):
    def import_cloud_daily(
        self, readings: tuple[Reading, ...]
    ) -> "DailyImportStoreResult": ...


class DailyImportStoreResult(Protocol):
    @property
    def readings(self) -> tuple[Reading, ...]: ...

    @property
    def new_count(self) -> int: ...

    @property
    def reused_count(self) -> int: ...


class CloudHistoryService:
    def __init__(
        self,
        specification_gateway: SpecificationGateway,
        report_logs: ReportLogGateway,
        readings: DailyReadingStore,
        historical_scale_contract: HistoricalScaleContract,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.specification_gateway = specification_gateway
        self.report_logs = report_logs
        self.readings = readings
        self.historical_scale_contract = historical_scale_contract
        self.clock = clock or (lambda: datetime.now(UTC))

    def import_recent(
        self,
        device: Device,
        timezone: tzinfo,
        cancellation: CancellationContext,
    ) -> CloudImportResult:
        cancellation.checkpoint()
        if not self.historical_scale_contract.verified:
            raise UserFacingError(
                "Import cloud neactivat",
                "Tuya nu documentează încă scara istorică a valorilor. Importul rămâne blocat pentru a evita un calcul greșit.",
                "historical-scale-contract-not-verified",
            )
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        start_ms, end_ms = recent_utc_milliseconds(timezone, now)

        cancellation.checkpoint()
        observed_at = self.clock().astimezone(UTC)
        first_specification = self.specification_gateway.get_device_specification(
            device.device_id
        )
        cancellation.checkpoint()
        events = self.report_logs.list_events(
            device.device_id,
            first_specification.code,
            start_ms,
            end_ms,
            cancellation,
        )
        cancellation.checkpoint()
        final_specification = self.specification_gateway.get_device_specification(
            device.device_id
        )
        if _specification_identity(first_specification) != _specification_identity(
            final_specification
        ):
            raise UserFacingError(
                "Specificație schimbată",
                "Specificația contorului s-a schimbat în timpul importului. Nu s-a salvat nicio citire.",
            )

        imported_at = self.clock().astimezone(UTC)
        candidates = reduce_daily_events(
            events,
            first_specification,
            timezone,
            observed_at,
            imported_at,
        )
        cancellation.checkpoint()
        stored = self.readings.import_cloud_daily(candidates)
        return CloudImportResult(
            tuple(stored.readings),
            int(stored.new_count),
            int(stored.reused_count),
        )


def recent_utc_milliseconds(timezone: tzinfo, now: datetime) -> tuple[int, int]:
    """Return exact UTC bounds for the latest seven local dates."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    local_now = now.astimezone(timezone)
    end_date = local_now.date()
    start_date = end_date - timedelta(days=RECENT_LOCAL_DAYS - 1)
    lower = datetime.combine(start_date, time.min, timezone).astimezone(UTC)
    upper = now.astimezone(UTC)
    return _epoch_milliseconds(lower), _epoch_milliseconds(upper)


def reduce_daily_events(
    events: tuple[CloudReportEvent, ...],
    specification: EnergySpecification,
    timezone: tzinfo,
    specification_observed_at_utc: datetime,
    imported_at_utc: datetime,
) -> tuple[Reading, ...]:
    earliest: dict[date, CloudReportEvent] = {}
    for event in sorted(events, key=lambda item: item.event_time_ms):
        timestamp = _datetime_from_epoch_milliseconds(event.event_time_ms)
        local_date = timestamp.astimezone(timezone).date()
        earliest.setdefault(local_date, event)

    timezone_name = _timezone_name(timezone)
    candidates: list[Reading] = []
    for local_date, event in sorted(earliest.items()):
        recorded_at = _datetime_from_epoch_milliseconds(event.event_time_ms)
        normalized = normalize_energy(
            event.raw_value, specification.scale, specification.unit
        )
        if normalized < 0 or normalized > MAX_NORMALIZED_KWH:
            raise UserFacingError(
                "Valoare istorică invalidă",
                "O valoare istorică de energie depășește limitele acceptate.",
            )
        raw = canonical_decimal(event.raw_value)
        candidates.append(
            Reading(
                device_id=event.device_id,
                recorded_at_utc=recorded_at,
                raw_value=raw,
                scale=specification.scale,
                source_unit=specification.unit,
                value_kwh=normalized,
                source="cloud_daily",
                raw_status_json=event.raw_json,
                raw_specification_json=specification.raw_json,
                external_event_key=external_event_key(
                    event.device_id,
                    event.code,
                    event.event_time_ms,
                    raw,
                ),
                imported_at_utc=imported_at_utc,
                specification_observed_at_utc=specification_observed_at_utc,
                source_code=event.code,
                cloud_day_local_date=local_date,
                cloud_day_timezone=timezone_name,
                cloud_day_utc_offset=_utc_offset(recorded_at.astimezone(timezone)),
            )
        )
    return tuple(candidates)


def external_event_key(
    device_id: str,
    source_code: str,
    event_time_ms: int,
    canonical_raw_value: str,
) -> str:
    payload = b"tuya-report-v1\0" + b"".join(
        _frame(value)
        for value in (
            device_id,
            source_code,
            str(event_time_ms),
            canonical_raw_value,
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return str(len(encoded)).encode("ascii") + b":" + encoded


def _epoch_milliseconds(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _datetime_from_epoch_milliseconds(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def _timezone_name(timezone: tzinfo) -> str:
    key = getattr(timezone, "key", None)
    return key if isinstance(key, str) and key else "system-local"


def _utc_offset(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("timezone has no UTC offset")
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    hours, remainder = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{remainder:02d}"


def _specification_identity(value: EnergySpecification) -> tuple[str, str, int]:
    return value.code, value.unit, value.scale
