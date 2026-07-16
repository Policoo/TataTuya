from datetime import UTC, datetime
from decimal import Decimal

from tatatuya.ui.formatters import format_decimal, format_energy, format_local_datetime


def test_romanian_decimal_and_energy_formatting() -> None:
    assert format_decimal(Decimal("1234.560")) == "1.234,560"
    assert format_decimal(Decimal("1234.5"), places=2) == "1.234,50"
    assert format_energy(Decimal("0")) == "0 kWh"


def test_timestamp_format_contains_local_date_and_24_hour_time() -> None:
    rendered = format_local_datetime(datetime(2026, 12, 3, 18, 42, tzinfo=UTC))
    assert rendered.count(".") == 2
    assert ", " in rendered
    assert ":" in rendered

