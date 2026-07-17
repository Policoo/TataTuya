from datetime import UTC, datetime
from decimal import Decimal

from tatatuya.domain.models import Currency
from tatatuya.ui.formatters import (
    format_decimal,
    format_energy,
    format_local_datetime,
    format_money,
    format_unit_price,
)


def test_romanian_decimal_and_energy_formatting() -> None:
    assert format_decimal(Decimal("1234.560")) == "1.234,560"
    assert format_decimal(Decimal("1234.5"), places=2) == "1.234,50"
    assert format_energy(Decimal("0")) == "0 kWh"


def test_timestamp_format_contains_local_date_and_24_hour_time() -> None:
    rendered = format_local_datetime(datetime(2026, 12, 3, 18, 42, tzinfo=UTC))
    assert rendered.count(".") == 2
    assert ", " in rendered
    assert ":" in rendered


def test_romanian_money_formatting_for_supported_currencies() -> None:
    assert format_money(Decimal("1234.5"), Currency.RON) == "1.234,50 RON"
    assert format_money(Decimal("1234.5"), Currency.EUR) == "1.234,50 EUR"
    assert format_unit_price(Decimal("0.800"), Currency.RON) == "0,800 RON/kWh"
    assert format_unit_price(Decimal("1.25"), Currency.EUR) == "1,25 EUR/kWh"
