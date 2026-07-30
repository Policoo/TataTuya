from decimal import Decimal

import pytest

from tatatuya.domain.energy import (
    MAX_CANONICAL_DECIMAL_CHARACTERS,
    MAX_ENERGY_SCALE,
    DecimalExpansionError,
    canonical_decimal,
    canonical_decimal_length,
    normalize_energy,
)
from tatatuya.domain.errors import UserFacingError


@pytest.mark.parametrize(
    ("raw", "scale", "unit", "expected"),
    [
        ("12", 0, "kWh", Decimal("12")),
        (123456, 2, "kWh", Decimal("1234.56")),
        (1234567, 3, "kWh", Decimal("1234.567")),
        (123456, 2, "Wh", Decimal("1.23456")),
        (123456, 2, "kW·h", Decimal("1234.56")),
        (123456, 2, "W·h", Decimal("1.23456")),
    ],
)
def test_normalize_energy(raw, scale, unit, expected) -> None:
    assert normalize_energy(raw, scale, unit) == expected


def test_normalization_does_not_round_beyond_decimal_context_precision() -> None:
    raw = Decimal("0.12345678901234567890123456789")
    assert normalize_energy(raw, 0, "kWh") == raw
    assert normalize_energy(raw, 0, "Wh") == Decimal(
        "0.00012345678901234567890123456789"
    )


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
    assert canonical_decimal(Decimal("-0")) == "0"


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("kWh", Decimal("1e-123")),
        ("Wh", Decimal("1e-126")),
    ],
)
def test_maximum_energy_scale_remains_exact_within_canonical_bound(
    unit, expected
) -> None:
    normalized = normalize_energy(1, MAX_ENERGY_SCALE, unit)

    assert MAX_ENERGY_SCALE == 123
    assert normalized == expected
    assert canonical_decimal_length(normalized) <= MAX_CANONICAL_DECIMAL_CHARACTERS


def test_normalization_rejects_scale_above_fixed_representation_limit() -> None:
    with pytest.raises(UserFacingError, match="Scara"):
        normalize_energy(1, MAX_ENERGY_SCALE + 1, "kWh")


@pytest.mark.parametrize(
    ("raw", "scale"),
    [
        (Decimal("1e-127"), 0),
        (Decimal("1e-126"), 1),
        (Decimal("1e128"), 0),
    ],
)
def test_normalization_rejects_oversized_raw_or_normalized_value(raw, scale) -> None:
    with pytest.raises(UserFacingError, match="depășește"):
        normalize_energy(raw, scale, "kWh")


def test_canonical_limit_rejects_before_fixed_rendering(monkeypatch) -> None:
    def unexpected_format(*args, **kwargs):
        raise AssertionError("fixed rendering must not run")

    monkeypatch.setattr(
        "tatatuya.domain.energy.format", unexpected_format, raising=False
    )

    with pytest.raises(DecimalExpansionError):
        canonical_decimal(Decimal("1e-100000"))
