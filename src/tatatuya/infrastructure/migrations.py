"""Ordered, transactional SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


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
)


def migrate(connection: sqlite3.Connection) -> None:
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
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise
