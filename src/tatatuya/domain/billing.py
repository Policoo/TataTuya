"""Exact parsing and billing calculations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Calculation, Currency, Reading


def parse_decimal_input(text: str) -> Decimal:
    """Parse Romanian comma decimals, accepting a defensive plain dot decimal."""
    compact = text.strip().replace(" ", "").replace("\u00a0", "")
    if not compact:
        raise _invalid_price()

    if "," in compact:
        # Romanian grouping: 1.234,56 -> 1234.56
        normalized = compact.replace(".", "").replace(",", ".")
    else:
        normalized = compact
    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise _invalid_price() from exc
    if not value.is_finite():
        raise _invalid_price()
    return value


def calculate_period(
    start: Reading,
    end: Reading,
    unit_price: Decimal,
    currency: Currency,
    created_at_utc: datetime,
) -> Calculation:
    if start.id is None or end.id is None:
        raise ValueError("Billing requires persisted readings")
    if start.device_id != end.device_id:
        raise UserFacingError(
            "Citiri incompatibile",
            "Citirile selectate nu aparțin aceluiași contor.",
        )
    if start.id == end.id:
        raise UserFacingError(
            "Perioadă invalidă",
            "Selectați două citiri diferite.",
        )
    if end.recorded_at_utc <= start.recorded_at_utc:
        raise UserFacingError(
            "Perioadă invalidă",
            "Citirea finală trebuie să fie ulterioară citirii inițiale.",
        )
    if start.source_unit.strip().lower() not in {"kwh", "wh"} or end.source_unit.strip().lower() not in {"kwh", "wh"}:
        raise UserFacingError(
            "Unități incompatibile",
            "Una dintre citirile selectate folosește o unitate neacceptată.",
        )
    if not unit_price.is_finite() or unit_price <= 0:
        raise _invalid_price()

    consumption = end.value_kwh - start.value_kwh
    if consumption < 0:
        raise UserFacingError(
            "Index mai mic",
            "Indexul final este mai mic decât cel inițial. Contorul poate fi resetat sau înlocuit.",
        )

    return Calculation(
        device_id=start.device_id,
        start_reading_id=start.id,
        end_reading_id=end.id,
        consumption_kwh=consumption,
        unit_price=unit_price,
        currency=currency,
        total=consumption * unit_price,
        created_at_utc=created_at_utc,
    )


def resolve_unit_price(entered_text: str, remembered: Decimal | None) -> Decimal:
    if entered_text.strip():
        price = parse_decimal_input(entered_text)
    elif remembered is not None:
        price = remembered
    else:
        raise UserFacingError(
            "Preț lipsă",
            "Introduceți prețul pentru un kWh.",
        )
    if price <= 0:
        raise _invalid_price()
    return price


def _invalid_price() -> UserFacingError:
    return UserFacingError(
        "Preț invalid",
        "Introduceți un preț pozitiv, de exemplu 0,80.",
    )

