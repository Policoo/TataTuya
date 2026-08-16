"""Ordered, transactional SQLite schema migrations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tatatuya.infrastructure.dbapi import dbapi


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE devices (
            device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            product_id TEXT,
            product_name TEXT,
            category TEXT,
            online INTEGER,
            energy_code TEXT,
            energy_unit TEXT,
            energy_scale INTEGER,
            raw_device_json TEXT NOT NULL,
            first_seen_at_utc TEXT NOT NULL,
            last_seen_at_utc TEXT NOT NULL
        );

        CREATE TABLE device_preferences (
            device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
            last_unit_price TEXT,
            price_currency TEXT,
            updated_at_utc TEXT
        );

        CREATE TABLE readings (
            id INTEGER PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(device_id),
            recorded_at_utc TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            scale INTEGER NOT NULL,
            source_unit TEXT NOT NULL,
            value_kwh TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_status_json TEXT NOT NULL
        );

        CREATE INDEX readings_device_time
            ON readings(device_id, recorded_at_utc);
        CREATE INDEX readings_device_id
            ON readings(device_id, id);

        CREATE TABLE calculations (
            id INTEGER PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(device_id),
            start_reading_id INTEGER NOT NULL REFERENCES readings(id),
            end_reading_id INTEGER NOT NULL REFERENCES readings(id),
            consumption_kwh TEXT NOT NULL,
            unit_price TEXT NOT NULL,
            currency TEXT NOT NULL,
            total TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );

        CREATE INDEX calculations_device_time
            ON calculations(device_id, created_at_utc);
        """,
    ),
    (
        2,
        """
        DELETE FROM settings WHERE key = 'tuya.account_uid';
        """,
    ),
    (
        3,
        """
        ALTER TABLE devices
            ADD COLUMN energy_eligibility TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE devices
            ADD COLUMN present_in_tuya INTEGER;
        ALTER TABLE devices
            ADD COLUMN raw_specification_json TEXT;
        ALTER TABLE readings
            ADD COLUMN raw_specification_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        4,
        """
        ALTER TABLE readings ADD COLUMN external_event_key TEXT;
        ALTER TABLE readings ADD COLUMN imported_at_utc TEXT;
        ALTER TABLE readings ADD COLUMN specification_observed_at_utc TEXT;
        ALTER TABLE readings ADD COLUMN source_code TEXT;
        ALTER TABLE readings ADD COLUMN cloud_day_local_date TEXT;
        ALTER TABLE readings ADD COLUMN cloud_day_timezone TEXT;
        ALTER TABLE readings ADD COLUMN cloud_day_utc_offset TEXT;

        CREATE UNIQUE INDEX readings_external_event_key
            ON readings(external_event_key)
            WHERE external_event_key IS NOT NULL;
        CREATE UNIQUE INDEX readings_cloud_daily_device_date
            ON readings(device_id, cloud_day_local_date)
            WHERE source = 'cloud_daily'
              AND cloud_day_local_date IS NOT NULL;
        """,
    ),
)


def migrate(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        )
        """
    )
    # executescript() commits pending work before running its script. Commit the
    # migration ledger deliberately, then place every migration and its marker
    # inside an explicit transaction embedded in the same script.
    connection.commit()
    applied = {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        applied_at = datetime.now(UTC).isoformat().replace("'", "''")
        script = f"""
            BEGIN IMMEDIATE;
            {sql}
            INSERT INTO schema_migrations(version, applied_at_utc)
                VALUES ({version}, '{applied_at}');
            COMMIT;
        """
        try:
            connection.executescript(script)
        except dbapi.Error:
            if connection.in_transaction:
                connection.rollback()
            raise
