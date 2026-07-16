from decimal import Decimal

import pytest

from tatatuya.domain.energy import canonical_decimal, normalize_energy
from tatatuya.domain.errors import UserFacingError


@pytest.mark.parametrize(
    ("raw", "scale", "unit", "expected"),
    [
        ("12", 0, "kWh", Decimal("12")),
        (123456, 2, "kWh", Decimal("1234.56")),
        (1234567, 3, "kWh", Decimal("1234.567")),
        (123456, 2, "Wh", Decimal("1.23456")),
    ],
)
def test_normalize_energy(raw, scale, unit, expected) -> None:
    assert normalize_energy(raw, scale, unit) == expected


@pytest.mark.parametrize("raw", [True, "nope", "NaN", "Infinity"])
def test_rejects_non_numeric_values(raw) -> None:
    with pytest.raises(UserFacingError):
        normalize_energy(raw, 2, "kWh")


def test_rejects_unsupported_unit() -> None:
    with pytest.raises(UserFacingError, match="unitate"):
        normalize_energy(100, 2, "J")


def test_decimal_serialization_is_canonical() -> None:
    assert canonical_decimal(Decimal("123.4500")) == "123.45"
    assert canonical_decimal(Decimal("0.000")) == "0"

