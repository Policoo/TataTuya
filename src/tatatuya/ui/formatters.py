"""Romanian presentation formatting."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from tatatuya.domain.models import Currency
from tatatuya.ui import text


def format_decimal(value: Decimal, *, places: int | None = None) -> str:
    """Format an exact decimal with Romanian separators."""
    if places is None:
        rendered = format(value, "f")
    else:
        rendered = format(value, f",.{places}f").replace(",", "\0")
    if places is None:
        whole, separator, fraction = rendered.partition(".")
        sign = ""
        if whole.startswith("-"):
            sign, whole = "-", whole[1:]
        digits = whole
        groups = []
        while digits:
            groups.append(digits[-3:])
            digits = digits[:-3]
        grouped = ".".join(reversed(groups)) or "0"
        return sign + grouped + (("," + fraction) if separator else "")
    return rendered.replace(".", ",").replace("\0", ".")


def format_energy(value: Decimal) -> str:
    return f"{format_decimal(value)} kWh"


def format_money(value: Decimal, currency: Currency) -> str:
    return f"{format_decimal(value, places=2)} {currency.value}"


def format_unit_price(value: Decimal, currency: Currency) -> str:
    return f"{format_decimal(value)} {currency.value}/kWh"


def format_local_datetime(value: datetime) -> str:
    local = value.astimezone()
    return local.strftime("%d.%m.%Y, %H:%M")


def format_local_date(value: datetime) -> str:
    return value.astimezone().strftime("%d.%m.%Y")


def format_reading_option(value: Decimal, recorded_at_utc: datetime) -> str:
    return f"{format_local_datetime(recorded_at_utc)} — {format_energy(value)}"


def online_label(value: bool | None) -> str:
    if value is True:
        return text.ONLINE
    if value is False:
        return text.OFFLINE
    return text.UNKNOWN
