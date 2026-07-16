"""Typed domain values shared by services and infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Currency(StrEnum):
    RON = "RON"
    EUR = "EUR"


@dataclass(frozen=True, slots=True)
class TuyaSettings:
    client_id: str
    client_secret: str
    region: str
    currency: Currency = Currency.RON

    @property
    def is_complete(self) -> bool:
        return all(
            value.strip()
            for value in (self.client_id, self.client_secret, self.region)
        )


@dataclass(frozen=True, slots=True)
class EnergySpecification:
    code: str
    unit: str
    scale: int


@dataclass(frozen=True, slots=True)
class StatusValue:
    code: str
    value: Any


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    device_id: str
    statuses: tuple[StatusValue, ...]
    raw_json: str

    def value_for(self, code: str) -> Any | None:
        matches = [item.value for item in self.statuses if item.code == code]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    name: str
    product_id: str | None = None
    product_name: str | None = None
    category: str | None = None
    online: bool | None = None
    energy_code: str | None = None
    energy_unit: str | None = None
    energy_scale: int | None = None
    raw_device_json: str = "{}"
    first_seen_at_utc: datetime | None = None
    last_seen_at_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    device_id: str
    recorded_at_utc: datetime
    raw_value: str
    scale: int
    source_unit: str
    value_kwh: Decimal
    source: str
    raw_status_json: str
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Calculation:
    device_id: str
    start_reading_id: int
    end_reading_id: int
    consumption_kwh: Decimal
    unit_price: Decimal
    currency: Currency
    total: Decimal
    created_at_utc: datetime
    id: int | None = None


@dataclass(frozen=True, slots=True)
class DevicePricePreference:
    device_id: str
    last_unit_price: Decimal | None
    price_currency: Currency | None
    updated_at_utc: datetime | None
