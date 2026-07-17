"""Calculation preparation, preview, and immutable persistence workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tatatuya.domain.billing import (
    calculate_consumption,
    calculate_period,
    resolve_unit_price,
)
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Currency, Reading
from tatatuya.services.ports import (
    CalculationStore,
    DevicePreferenceStore,
    ReadingStore,
)


@dataclass(frozen=True, slots=True)
class CalculationContext:
    device_id: str
    readings: tuple[Reading, ...]
    default_start_reading_id: int
    default_end_reading_id: int
    remembered_unit_price: Decimal | None
    currency: Currency


class BillingService:
    def __init__(
        self,
        readings: ReadingStore,
        calculations: CalculationStore,
        preferences: DevicePreferenceStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.readings = readings
        self.calculations = calculations
        self.preferences = preferences
        self.now = now or (lambda: datetime.now(UTC))

    def prepare(self, device_id: str, currency: Currency) -> CalculationContext:
        readings = tuple(
            sorted(
                self.readings.list_for_device(device_id),
                key=lambda reading: (
                    reading.recorded_at_utc,
                    reading.id if reading.id is not None else -1,
                ),
            )
        )
        if len(readings) < 2:
            raise UserFacingError(
                "Citiri insuficiente",
                "Sunt necesare cel puțin două citiri pentru a calcula consumul.",
            )

        newest = readings[-1]
        earliest = readings[0]
        latest_calculation = self.calculations.latest_for_device(device_id)
        available_ids = {reading.id for reading in readings}
        previous_end_id = (
            latest_calculation.end_reading_id
            if latest_calculation is not None
            and latest_calculation.end_reading_id in available_ids
            else earliest.id
        )
        if any(reading.id is None for reading in readings) or previous_end_id is None:
            raise UserFacingError(
                "Citiri indisponibile",
                "Citirile salvate nu au putut fi pregătite pentru calcul.",
            )

        preference = self.preferences.get(device_id)
        remembered = (
            preference.last_unit_price
            if preference is not None and preference.price_currency is currency
            else None
        )
        return CalculationContext(
            device_id=device_id,
            readings=readings,
            default_start_reading_id=previous_end_id,
            default_end_reading_id=newest.id,
            remembered_unit_price=remembered,
            currency=currency,
        )

    @staticmethod
    def preview(
        start: Reading,
        end: Reading,
        entered_price: str,
        currency: Currency,
        remembered_price: Decimal | None,
    ) -> Calculation:
        price = resolve_unit_price(entered_price, remembered_price)
        return calculate_period(start, end, price, currency, datetime.now(UTC))

    @staticmethod
    def consumption(start: Reading, end: Reading) -> Decimal:
        return calculate_consumption(start, end)

    def save_calculation(
        self,
        device_id: str,
        start_reading_id: int,
        end_reading_id: int,
        entered_price: str,
        currency: Currency,
    ) -> Calculation:
        start = self._reading_for_device(start_reading_id, device_id)
        end = self._reading_for_device(end_reading_id, device_id)
        preference = self.preferences.get(device_id)
        remembered = (
            preference.last_unit_price
            if preference is not None and preference.price_currency is currency
            else None
        )
        price = resolve_unit_price(entered_price, remembered)
        created_at = self.now()
        calculation = calculate_period(start, end, price, currency, created_at)
        saved = self.calculations.add(calculation)
        self.preferences.save_price(device_id, price, currency, created_at)
        return saved

    def _reading_for_device(self, reading_id: int, device_id: str) -> Reading:
        reading = self.readings.get(reading_id)
        if reading is None or reading.device_id != device_id:
            raise UserFacingError(
                "Citire indisponibilă",
                "Una dintre citirile selectate nu mai este disponibilă.",
            )
        return reading
