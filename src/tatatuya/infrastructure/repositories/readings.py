"""Cumulative-reading persistence."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from tatatuya.domain.energy import canonical_decimal
from tatatuya.domain.models import Reading
from tatatuya.infrastructure.repositories._mapping import from_utc_text, to_utc_text


class ReadingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, reading: Reading) -> Reading:
        cursor = self.connection.execute(
            """
            INSERT INTO readings(
                device_id, recorded_at_utc, raw_value, scale, source_unit,
                value_kwh, source, raw_status_json, raw_specification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading.device_id,
                to_utc_text(reading.recorded_at_utc),
                reading.raw_value,
                reading.scale,
                reading.source_unit,
                canonical_decimal(reading.value_kwh),
                reading.source,
                reading.raw_status_json,
                reading.raw_specification_json,
            ),
        )
        reading_id = cursor.lastrowid
        if reading_id is None:
            raise sqlite3.DatabaseError("SQLite did not return a reading ID")
        return Reading(
            device_id=reading.device_id,
            recorded_at_utc=reading.recorded_at_utc,
            raw_value=reading.raw_value,
            scale=reading.scale,
            source_unit=reading.source_unit,
            value_kwh=reading.value_kwh,
            source=reading.source,
            raw_status_json=reading.raw_status_json,
            id=int(reading_id),
            raw_specification_json=reading.raw_specification_json,
        )

    def get(self, reading_id: int) -> Reading | None:
        row = self.connection.execute(
            "SELECT * FROM readings WHERE id = ?", (reading_id,)
        ).fetchone()
        return None if row is None else _map_reading(row)

    def list_for_device(self, device_id: str) -> list[Reading]:
        rows = self.connection.execute(
            """
            SELECT * FROM readings
            WHERE device_id = ?
            ORDER BY recorded_at_utc, id
            """,
            (device_id,),
        ).fetchall()
        return [_map_reading(row) for row in rows]

    def latest_for_device(self, device_id: str) -> Reading | None:
        row = self.connection.execute(
            """
            SELECT * FROM readings
            WHERE device_id = ?
            ORDER BY recorded_at_utc DESC, id DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        return None if row is None else _map_reading(row)

    def latest_by_device(self) -> dict[str, Reading]:
        """Return every device's newest reading with one database query."""
        rows = self.connection.execute(
            """
            SELECT current.*
            FROM readings AS current
            WHERE current.id = (
                SELECT candidate.id
                FROM readings AS candidate
                WHERE candidate.device_id = current.device_id
                ORDER BY candidate.recorded_at_utc DESC, candidate.id DESC
                LIMIT 1
            )
            """
        ).fetchall()
        return {str(row["device_id"]): _map_reading(row) for row in rows}


def _map_reading(row: sqlite3.Row) -> Reading:
    return Reading(
        id=row["id"],
        device_id=row["device_id"],
        recorded_at_utc=from_utc_text(row["recorded_at_utc"]),
        raw_value=row["raw_value"],
        scale=row["scale"],
        source_unit=row["source_unit"],
        value_kwh=Decimal(row["value_kwh"]),
        source=row["source"],
        raw_status_json=row["raw_status_json"],
        raw_specification_json=row["raw_specification_json"],
    )
