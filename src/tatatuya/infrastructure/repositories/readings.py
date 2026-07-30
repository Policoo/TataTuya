"""Cumulative-reading persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
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
                value_kwh, source, raw_status_json, raw_specification_json,
                external_event_key, imported_at_utc,
                specification_observed_at_utc, source_code,
                cloud_day_local_date, cloud_day_timezone, cloud_day_utc_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                reading.external_event_key,
                _optional_utc_text(reading.imported_at_utc),
                _optional_utc_text(reading.specification_observed_at_utc),
                reading.source_code,
                (
                    reading.cloud_day_local_date.isoformat()
                    if reading.cloud_day_local_date is not None
                    else None
                ),
                reading.cloud_day_timezone,
                reading.cloud_day_utc_offset,
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
            external_event_key=reading.external_event_key,
            imported_at_utc=reading.imported_at_utc,
            specification_observed_at_utc=reading.specification_observed_at_utc,
            source_code=reading.source_code,
            cloud_day_local_date=reading.cloud_day_local_date,
            cloud_day_timezone=reading.cloud_day_timezone,
            cloud_day_utc_offset=reading.cloud_day_utc_offset,
        )

    def prepare_capture_phase(self) -> None:
        """Commit the completed metadata stage before status requests begin."""

        self.connection.commit()

    def add_all(
        self,
        readings: tuple[Reading, ...],
        *,
        busy_timeout_seconds: float,
    ) -> tuple[Reading, ...]:
        """Commit exactly one completed current-status response atomically."""

        if not readings:
            return ()
        if self.connection.in_transaction:
            raise sqlite3.ProgrammingError(
                "prepare_capture_phase() must finish prior work before capture"
            )
        timeout_ms = max(1, min(5_000, int(busy_timeout_seconds * 1_000)))
        previous_timeout_row = self.connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()
        previous_timeout = int(previous_timeout_row[0])
        self.connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            saved = tuple(self.add(reading) for reading in readings)
            self.connection.commit()
            return saved
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute(f"PRAGMA busy_timeout = {previous_timeout}")

    def import_cloud_daily(
        self, readings: tuple[Reading, ...]
    ) -> "DailyImportResult":
        """Get or create a fully validated daily set without mutating provenance."""

        saved: list[Reading] = []
        new_count = 0
        reused_count = 0
        for candidate in readings:
            if (
                candidate.source != "cloud_daily"
                or candidate.external_event_key is None
                or candidate.cloud_day_local_date is None
            ):
                raise ValueError("cloud daily provenance is incomplete")
            existing = self._by_external_key(candidate.external_event_key)
            if existing is not None:
                _verify_cloud_identity(existing, candidate)
                saved.append(existing)
                reused_count += 1
                continue
            existing = self._by_cloud_day(
                candidate.device_id, candidate.cloud_day_local_date
            )
            if existing is not None:
                saved.append(existing)
                reused_count += 1
                continue
            saved.append(self.add(candidate))
            new_count += 1
        return DailyImportResult(tuple(saved), new_count, reused_count)

    def _by_external_key(self, key: str) -> Reading | None:
        row = self.connection.execute(
            "SELECT * FROM readings WHERE external_event_key = ?", (key,)
        ).fetchone()
        return None if row is None else _map_reading(row)

    def _by_cloud_day(self, device_id: str, local_date: date) -> Reading | None:
        row = self.connection.execute(
            """
            SELECT * FROM readings
            WHERE device_id = ? AND source = 'cloud_daily'
              AND cloud_day_local_date = ?
            """,
            (device_id, local_date.isoformat()),
        ).fetchone()
        return None if row is None else _map_reading(row)

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
        external_event_key=row["external_event_key"],
        imported_at_utc=_optional_utc(row["imported_at_utc"]),
        specification_observed_at_utc=_optional_utc(
            row["specification_observed_at_utc"]
        ),
        source_code=row["source_code"],
        cloud_day_local_date=(
            date.fromisoformat(row["cloud_day_local_date"])
            if row["cloud_day_local_date"] is not None
            else None
        ),
        cloud_day_timezone=row["cloud_day_timezone"],
        cloud_day_utc_offset=row["cloud_day_utc_offset"],
    )


@dataclass(frozen=True, slots=True)
class DailyImportResult:
    readings: tuple[Reading, ...]
    new_count: int
    reused_count: int


def _optional_utc_text(value):
    return None if value is None else to_utc_text(value)


def _optional_utc(value):
    return None if value is None else from_utc_text(value)


def _verify_cloud_identity(existing: Reading, candidate: Reading) -> None:
    comparable = (
        "device_id",
        "source_code",
        "recorded_at_utc",
        "raw_value",
        "scale",
        "source_unit",
        "value_kwh",
    )
    if any(getattr(existing, field) != getattr(candidate, field) for field in comparable):
        raise sqlite3.IntegrityError("cloud event identity conflict")
