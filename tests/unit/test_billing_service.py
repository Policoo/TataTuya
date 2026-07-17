from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import (
    Calculation,
    Currency,
    DevicePricePreference,
    Reading,
)
from tatatuya.services.billing_service import BillingService


NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def reading(reading_id: int, value: str, day: int) -> Reading:
    return Reading(
        "meter-1",
        NOW + timedelta(days=day),
        value,
        2,
        "kWh",
        Decimal(value),
        "batch",
        "{}",
        reading_id,
    )


class ReadingStore:
    def __init__(self, values):
        self.values = list(values)

    def list_for_device(self, device_id):
        return [value for value in self.values if value.device_id == device_id]

    def get(self, reading_id):
        return next((value for value in self.values if value.id == reading_id), None)


class CalculationStore:
    def __init__(self, latest=None):
        self.latest = latest
        self.saved = []

    def latest_for_device(self, device_id):
        return self.latest

    def add(self, value):
        saved = Calculation(
            value.device_id,
            value.start_reading_id,
            value.end_reading_id,
            value.consumption_kwh,
            value.unit_price,
            value.currency,
            value.total,
            value.created_at_utc,
            99,
        )
        self.saved.append(saved)
        return saved


class PreferenceStore:
    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def get(self, device_id):
        return self.value

    def save_price(self, device_id, unit_price, currency, updated_at_utc):
        self.saved.append((device_id, unit_price, currency, updated_at_utc))


def service(readings, calculation=None, preference=None):
    calculation_store = CalculationStore(calculation)
    preference_store = PreferenceStore(preference)
    return (
        BillingService(
            ReadingStore(readings), calculation_store, preference_store, lambda: NOW
        ),
        calculation_store,
        preference_store,
    )


def test_prepare_defaults_to_earliest_and_newest_readings() -> None:
    billing, _, _ = service([reading(1, "100", 0), reading(2, "110", 1)])

    context = billing.prepare("meter-1", Currency.RON)

    assert context.default_start_reading_id == 1
    assert context.default_end_reading_id == 2


def test_prepare_uses_last_calculation_end_as_next_start() -> None:
    values = [reading(1, "100", 0), reading(2, "110", 1), reading(3, "120", 2)]
    previous = Calculation(
        "meter-1", 1, 2, Decimal("10"), Decimal("0.8"), Currency.RON,
        Decimal("8"), NOW, 4,
    )
    billing, _, _ = service(values, previous)

    context = billing.prepare("meter-1", Currency.RON)

    assert context.default_start_reading_id == 2
    assert context.default_end_reading_id == 3


def test_prepare_only_exposes_price_for_matching_currency() -> None:
    preference = DevicePricePreference(
        "meter-1", Decimal("0.81"), Currency.RON, NOW
    )
    billing, _, _ = service(
        [reading(1, "100", 0), reading(2, "110", 1)], preference=preference
    )

    assert billing.prepare("meter-1", Currency.RON).remembered_unit_price == Decimal("0.81")
    assert billing.prepare("meter-1", Currency.EUR).remembered_unit_price is None


def test_prepare_rejects_fewer_than_two_readings() -> None:
    billing, _, _ = service([reading(1, "100", 0)])

    with pytest.raises(UserFacingError, match="cel puțin două"):
        billing.prepare("meter-1", Currency.RON)


def test_save_uses_fallback_and_persists_calculation_and_preference() -> None:
    preference = DevicePricePreference(
        "meter-1", Decimal("0.80"), Currency.RON, NOW
    )
    billing, calculations, preferences = service(
        [reading(1, "100", 0), reading(2, "112.5", 1)], preference=preference
    )

    saved = billing.save_calculation("meter-1", 1, 2, "", Currency.RON)

    assert saved.consumption_kwh == Decimal("12.5")
    assert saved.total == Decimal("10.000")
    assert calculations.saved == [saved]
    assert preferences.saved == [("meter-1", Decimal("0.80"), Currency.RON, NOW)]


def test_save_rejects_reading_from_another_device() -> None:
    values = [reading(1, "100", 0), reading(2, "110", 1)]
    values[1] = Reading(
        "meter-2", values[1].recorded_at_utc, "110", 2, "kWh",
        Decimal("110"), "batch", "{}", 2,
    )
    billing, _, _ = service(values)

    with pytest.raises(UserFacingError, match="disponibilă"):
        billing.save_calculation("meter-1", 1, 2, "1", Currency.RON)
