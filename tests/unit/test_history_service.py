from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Currency, Reading
from tatatuya.services.history_service import HistoryService


NOW = datetime(2026, 12, 3, 18, 42, tzinfo=UTC)


def reading(reading_id: int, minute: int, value: str) -> Reading:
    return Reading(
        "meter-1",
        NOW + timedelta(minutes=minute),
        value.replace(".", ""),
        2,
        "kWh",
        Decimal(value),
        "batch" if reading_id == 1 else "status",
        "{}",
        reading_id,
    )


class ReadingStore:
    def __init__(self, readings: list[Reading]) -> None:
        self.readings = readings

    def list_for_device(self, device_id: str) -> list[Reading]:
        return list(self.readings)


class CalculationStore:
    def __init__(self, calculations: list[Calculation]) -> None:
        self.calculations = calculations

    def list_for_device(self, device_id: str) -> list[Calculation]:
        return list(self.calculations)


def test_history_is_newest_first_and_resolves_immutable_reading_references() -> None:
    start = reading(1, 0, "1234.56")
    end = reading(2, 30, "1247.06")
    calculation = Calculation(
        "meter-1",
        1,
        2,
        Decimal("12.50"),
        Decimal("0.80"),
        Currency.RON,
        Decimal("10.00"),
        NOW + timedelta(hours=1),
        7,
    )

    context = HistoryService(
        ReadingStore([start, end]), CalculationStore([calculation])
    ).prepare("meter-1")

    assert context.readings == (end, start)
    assert len(context.calculations) == 1
    item = context.calculations[0]
    assert item.calculation is calculation
    assert item.start_reading is start
    assert item.end_reading is end


def test_history_rejects_a_calculation_with_missing_reading_references() -> None:
    calculation = Calculation(
        "meter-1",
        1,
        2,
        Decimal("1"),
        Decimal("1"),
        Currency.RON,
        Decimal("1"),
        NOW,
        1,
    )

    with pytest.raises(UserFacingError) as raised:
        HistoryService(
            ReadingStore([reading(1, 0, "10")]),
            CalculationStore([calculation]),
        ).prepare("meter-1")

    assert raised.value.title == "Istoric indisponibil"
