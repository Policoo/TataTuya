"""Energy normalization rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Never

from tatatuya.domain.errors import UserFacingError


MAX_CANONICAL_DECIMAL_CHARACTERS = 128
# Wh normalization adds three decimal places. This is the greatest scale for
# which the smallest positive integer raw value still fits the canonical bound.
MAX_ENERGY_SCALE = MAX_CANONICAL_DECIMAL_CHARACTERS - 5


class DecimalExpansionError(ValueError):
    """A finite Decimal cannot be represented safely in canonical fixed form."""


def canonical_energy_unit(unit: str) -> str | None:
    """Map explicitly supported Tuya spellings to the billing unit meaning."""
    normalized = unit.strip().lower().replace(" ", "").replace("·", "")
    if normalized == "kwh":
        return "kWh"
    if normalized == "wh":
        return "Wh"
    return None


def canonical_decimal(value: Decimal) -> str:
    """Serialize a finite Decimal without exponent notation or redundant zeros."""
    validate_canonical_decimal(value)
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def canonical_decimal_length(value: Decimal) -> int:
    """Return fixed canonical length using tuple arithmetic, without rendering."""

    if not value.is_finite():
        raise ValueError("Decimal values must be finite")
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("Decimal values must be finite")
    if not any(digits):
        return 1
    trailing_zeroes = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zeroes += 1
    significant_count = len(digits) - trailing_zeroes
    canonical_exponent = exponent + trailing_zeroes
    if canonical_exponent >= 0:
        return sign + significant_count + canonical_exponent
    if significant_count + canonical_exponent > 0:
        return sign + significant_count + 1
    return sign + 2 - canonical_exponent


def validate_canonical_decimal(
    value: Decimal,
    *,
    max_characters: int = MAX_CANONICAL_DECIMAL_CHARACTERS,
) -> None:
    """Reject a Decimal whose canonical fixed form would exceed a safe bound."""

    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    if canonical_decimal_length(value) > max_characters:
        raise DecimalExpansionError(
            f"Canonical Decimal exceeds {max_characters} characters"
        )


def normalize_energy(raw_value: Any, scale: int, unit: str) -> Decimal:
    """Normalize a scaled Tuya energy value to canonical kWh."""
    if (
        isinstance(scale, bool)
        or not isinstance(scale, int)
        or scale < 0
        or scale > MAX_ENERGY_SCALE
    ):
        raise UserFacingError(
            "Citire incompatibilă",
            "Scara valorii de energie primită de la Tuya nu este validă.",
            f"scale={scale!r}",
        )
    if isinstance(raw_value, bool):
        _raise_invalid_value(raw_value)
    try:
        if isinstance(raw_value, Decimal):
            raw_decimal = raw_value
        elif isinstance(raw_value, int):
            raw_decimal = Decimal(raw_value)
        else:
            raw_decimal = Decimal(str(raw_value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        _raise_invalid_value(raw_value)
    if not raw_decimal.is_finite():
        _raise_invalid_value(raw_value)

    try:
        validate_canonical_decimal(raw_decimal)
    except DecimalExpansionError:
        _raise_oversized_value()

    normalized_unit = canonical_energy_unit(unit)
    if normalized_unit == "kWh":
        normalized = _shift_decimal(raw_decimal, scale)
    elif normalized_unit == "Wh":
        normalized = _shift_decimal(raw_decimal, scale + 3)
    else:
        raise UserFacingError(
            "Unitate necunoscută",
            "Contorul folosește o unitate de energie care nu este acceptată.",
            f"unit={unit!r}",
        )
    try:
        validate_canonical_decimal(normalized)
    except DecimalExpansionError:
        _raise_oversized_value()
    return normalized


def _raise_invalid_value(raw_value: Any) -> Never:
    raise UserFacingError(
        "Citire invalidă",
        "Valoarea de energie primită de la Tuya nu este numerică.",
        f"raw_value={raw_value!r}",
    )


def _raise_oversized_value() -> Never:
    raise UserFacingError(
        "Citire invalidă",
        "Valoarea de energie primită de la Tuya depășește limitele acceptate.",
        "canonical-decimal-limit-exceeded",
    )


def _shift_decimal(value: Decimal, decimal_places: int) -> Decimal:
    """Divide by a power of ten by changing the exponent without rounding."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("Only finite Decimal values can be shifted")
    return Decimal((sign, digits, exponent - decimal_places))
