"""Energy normalization rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from tatatuya.domain.errors import UserFacingError


def canonical_decimal(value: Decimal) -> str:
    """Serialize a finite Decimal without exponent notation or redundant zeros."""
    if not value.is_finite():
        raise ValueError("Decimal values must be finite")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def normalize_energy(raw_value: Any, scale: int, unit: str) -> Decimal:
    """Normalize a scaled Tuya energy value to canonical kWh."""
    if isinstance(scale, bool) or not isinstance(scale, int) or scale < 0:
        raise UserFacingError(
            "Citire incompatibilă",
            "Scara valorii de energie primită de la Tuya nu este validă.",
            f"scale={scale!r}",
        )
    if isinstance(raw_value, bool):
        _raise_invalid_value(raw_value)
    try:
        raw_decimal = Decimal(str(raw_value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        _raise_invalid_value(raw_value)
    if not raw_decimal.is_finite():
        _raise_invalid_value(raw_value)

    scaled = raw_decimal / (Decimal(10) ** scale)
    normalized_unit = unit.strip().lower().replace(" ", "")
    if normalized_unit == "kwh":
        return scaled
    if normalized_unit == "wh":
        return scaled / Decimal(1000)
    raise UserFacingError(
        "Unitate necunoscută",
        "Contorul folosește o unitate de energie care nu este acceptată.",
        f"unit={unit!r}",
    )


def _raise_invalid_value(raw_value: Any) -> None:
    raise UserFacingError(
        "Citire invalidă",
        "Valoarea de energie primită de la Tuya nu este numerică.",
        f"raw_value={raw_value!r}",
    )

