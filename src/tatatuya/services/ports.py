"""Repository contracts consumed by application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.models import (
    Calculation,
    Currency,
    Device,
    DeviceStatus,
    DevicePricePreference,
    EnergySpecification,
    Reading,
    TuyaSettings,
)


@dataclass(frozen=True, slots=True)
class CloudReportEvent:
    """Transport-neutral event delivered to the cloud-history workflow."""

    device_id: str
    code: str
    event_time_ms: int
    raw_value: Decimal
    raw_json: str


class TuyaGateway(Protocol):
    def list_devices(self, **params: object) -> list[Device]: ...
    def get_device_specification(self, device_id: str) -> EnergySpecification: ...
    def get_device_status(self, device_id: str) -> DeviceStatus: ...
    def get_devices_status(self, device_ids: list[str]) -> dict[str, DeviceStatus]: ...


class SettingsStore(Protocol):
    def save_tuya(
        self,
        settings: TuyaSettings,
        updated_at_utc: datetime | None = None,
        cancellation: CancellationContext | None = None,
    ) -> None: ...
    def load_tuya(
        self, cancellation: CancellationContext | None = None
    ) -> TuyaSettings | None: ...


class DeviceStore(Protocol):
    def upsert(self, device: Device, seen_at_utc: datetime | None = None) -> Device: ...
    def mark_all_missing(self) -> None: ...
    def get(self, device_id: str) -> Device | None: ...
    def list_all(self) -> list[Device]: ...


class ReadingStore(Protocol):
    def add(self, reading: Reading) -> Reading: ...
    def prepare_capture_phase(self) -> None: ...
    def add_all(
        self,
        readings: tuple[Reading, ...],
        *,
        busy_timeout_seconds: float,
    ) -> tuple[Reading, ...]: ...
    def get(self, reading_id: int) -> Reading | None: ...
    def list_for_device(self, device_id: str) -> list[Reading]: ...
    def latest_for_device(self, device_id: str) -> Reading | None: ...


class CalculationStore(Protocol):
    def add(self, calculation: Calculation) -> Calculation: ...
    def list_for_device(self, device_id: str) -> list[Calculation]: ...
    def latest_for_device(self, device_id: str) -> Calculation | None: ...


class DevicePreferenceStore(Protocol):
    def get(self, device_id: str) -> DevicePricePreference | None: ...
    def save_price(
        self,
        device_id: str,
        unit_price: Decimal,
        currency: Currency,
        updated_at_utc: datetime,
    ) -> DevicePricePreference: ...
