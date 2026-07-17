from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tatatuya.domain.billing import calculate_period, parse_decimal_input, resolve_unit_price
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Currency, Reading


NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)


def reading(reading_id: int, value: str, when: datetime = NOW, device_id: str = "meter-1") -> Reading:
    return Reading(device_id, when, value, 2, "kWh", Decimal(value), "batch", "{}", reading_id)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0,80", Decimal("0.80")), ("1.234,56", Decimal("1234.56")), ("0.80", Decimal("0.80"))],
)
def test_parse_decimal_input(text, expected) -> None:
    assert parse_decimal_input(text) == expected


def test_exact_calculation() -> None:
    result = calculate_period(
        reading(1, "100.10"),
        reading(2, "112.45", NOW + timedelta(days=30)),
        Decimal("0.83"),
        Currency.RON,
        NOW + timedelta(days=30),
    )
    assert result.consumption_kwh == Decimal("12.35")
    assert result.total == Decimal("10.2505")


def test_rejects_meter_reset() -> None:
    with pytest.raises(UserFacingError, match="resetat"):
        calculate_period(
            reading(1, "100"),
            reading(2, "90", NOW + timedelta(days=1)),
            Decimal("1"), Currency.RON, NOW,
        )


def test_rejects_same_reading() -> None:
    same = reading(1, "100")
    with pytest.raises(UserFacingError, match="diferite"):
        calculate_period(same, same, Decimal("1"), Currency.RON, NOW)


def test_rejects_reversed_timestamps() -> None:
    with pytest.raises(UserFacingError, match="ulterioară"):
        calculate_period(
            reading(1, "100", NOW),
            reading(2, "101", NOW - timedelta(seconds=1)),
            Decimal("1"), Currency.RON, NOW,
        )


def test_equal_timestamps_use_reading_id_as_order() -> None:
    result = calculate_period(
        reading(1, "100", NOW),
        reading(2, "101", NOW),
        Decimal("1"), Currency.RON, NOW,
    )
    assert result.consumption_kwh == Decimal("1")


def test_equal_timestamps_reject_reversed_reading_ids() -> None:
    with pytest.raises(UserFacingError, match="ulterioară"):
        calculate_period(
            reading(2, "100", NOW),
            reading(1, "101", NOW),
            Decimal("1"), Currency.RON, NOW,
        )


def test_rejects_readings_from_different_devices() -> None:
    with pytest.raises(UserFacingError, match="aceluiași"):
        calculate_period(
            reading(1, "100", device_id="one"),
            reading(2, "101", NOW + timedelta(seconds=1), device_id="two"),
            Decimal("1"), Currency.RON, NOW,
        )


@pytest.mark.parametrize("text", ["", "0", "-2", "abc"])
def test_invalid_price_without_fallback(text) -> None:
    with pytest.raises(UserFacingError):
        resolve_unit_price(text, None)


def test_remembered_price_fallback() -> None:
    assert resolve_unit_price("", Decimal("0.75")) == Decimal("0.75")


@pytest.mark.parametrize("remembered", [Decimal("NaN"), Decimal("Infinity")])
def test_rejects_non_finite_remembered_price(remembered) -> None:
    with pytest.raises(UserFacingError, match="pozitiv"):
        resolve_unit_price("", remembered)


def test_rejects_unsupported_reading_unit() -> None:
    start = reading(1, "100")
    unsupported = Reading(
        "meter-1", NOW + timedelta(days=1), "101", 0, "MWh",
        Decimal("101"), "batch", "{}", 2,
    )
    with pytest.raises(UserFacingError, match="unitate neacceptată"):
        calculate_period(start, unsupported, Decimal("1"), Currency.RON, NOW)


def test_accepts_tuya_middle_dot_kwh_readings() -> None:
    start = Reading(
        "meter-1", NOW, "10000", 2, "kW·h", Decimal("100"), "batch", "{}", 1
    )
    end = Reading(
        "meter-1",
        NOW + timedelta(days=1),
        "11250",
        2,
        "kW·h",
        Decimal("112.5"),
        "batch",
        "{}",
        2,
    )
    result = calculate_period(start, end, Decimal("0.8"), Currency.RON, NOW)
    assert result.consumption_kwh == Decimal("12.5")
