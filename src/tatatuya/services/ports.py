"""Repository contracts consumed by application services."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tatatuya.domain.models import (
    Calculation,
    Currency,
    Device,
    DevicePricePreference,
    Reading,
    TuyaSettings,
)


class SettingsStore(Protocol):
    def save_tuya(self, settings: TuyaSettings, updated_at_utc: datetime | None = None) -> None: ...
    def load_tuya(self) -> TuyaSettings | None: ...


class DeviceStore(Protocol):
    def upsert(self, device: Device, seen_at_utc: datetime | None = None) -> Device: ...
    def get(self, device_id: str) -> Device | None: ...
    def list_all(self) -> list[Device]: ...


class ReadingStore(Protocol):
    def add(self, reading: Reading) -> Reading: ...
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

