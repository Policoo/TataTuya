"""Immutable calculation and per-device price persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from tatatuya.domain.energy import canonical_decimal
from tatatuya.domain.models import Calculation, Currency, DevicePricePreference
from tatatuya.infrastructure.repositories._mapping import from_utc_text, to_utc_text


class CalculationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, calculation: Calculation) -> Calculation:
        cursor = self.connection.execute(
            """
            INSERT INTO calculations(
                device_id, start_reading_id, end_reading_id, consumption_kwh,
                unit_price, currency, total, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calculation.device_id,
                calculation.start_reading_id,
                calculation.end_reading_id,
                canonical_decimal(calculation.consumption_kwh),
                canonical_decimal(calculation.unit_price),
                calculation.currency.value,
                canonical_decimal(calculation.total),
                to_utc_text(calculation.created_at_utc),
            ),
        )
        calculation_id = cursor.lastrowid
        if calculation_id is None:
            raise sqlite3.DatabaseError("SQLite did not return a calculation ID")
        return Calculation(
            device_id=calculation.device_id,
            start_reading_id=calculation.start_reading_id,
            end_reading_id=calculation.end_reading_id,
            consumption_kwh=calculation.consumption_kwh,
            unit_price=calculation.unit_price,
            currency=calculation.currency,
            total=calculation.total,
            created_at_utc=calculation.created_at_utc,
            id=int(calculation_id),
        )

    def list_for_device(self, device_id: str) -> list[Calculation]:
        rows = self.connection.execute(
            """
            SELECT * FROM calculations
            WHERE device_id = ?
            ORDER BY created_at_utc, id
            """,
            (device_id,),
        ).fetchall()
        return [_map_calculation(row) for row in rows]

    def latest_for_device(self, device_id: str) -> Calculation | None:
        row = self.connection.execute(
            """
            SELECT * FROM calculations
            WHERE device_id = ?
            ORDER BY created_at_utc DESC, id DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        return None if row is None else _map_calculation(row)


class DevicePreferenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, device_id: str) -> DevicePricePreference | None:
        row = self.connection.execute(
            "SELECT * FROM device_preferences WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row is None:
            return None
        return DevicePricePreference(
            device_id=row["device_id"],
            last_unit_price=(
                None if row["last_unit_price"] is None else Decimal(row["last_unit_price"])
            ),
            price_currency=(
                None if row["price_currency"] is None else Currency(row["price_currency"])
            ),
            updated_at_utc=(
                None if row["updated_at_utc"] is None else from_utc_text(row["updated_at_utc"])
            ),
        )

    def save_price(
        self,
        device_id: str,
        unit_price: Decimal,
        currency: Currency,
        updated_at_utc: datetime,
    ) -> DevicePricePreference:
        self.connection.execute(
            """
            INSERT INTO device_preferences(
                device_id, last_unit_price, price_currency, updated_at_utc
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_unit_price = excluded.last_unit_price,
                price_currency = excluded.price_currency,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                device_id,
                canonical_decimal(unit_price),
                currency.value,
                to_utc_text(updated_at_utc),
            ),
        )
        preference = self.get(device_id)
        assert preference is not None
        return preference


def _map_calculation(row: sqlite3.Row) -> Calculation:
    return Calculation(
        id=row["id"],
        device_id=row["device_id"],
        start_reading_id=row["start_reading_id"],
        end_reading_id=row["end_reading_id"],
        consumption_kwh=Decimal(row["consumption_kwh"]),
        unit_price=Decimal(row["unit_price"]),
        currency=Currency(row["currency"]),
        total=Decimal(row["total"]),
        created_at_utc=from_utc_text(row["created_at_utc"]),
    )
