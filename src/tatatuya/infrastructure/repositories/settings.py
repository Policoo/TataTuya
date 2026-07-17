"""Application-setting persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories._mapping import to_utc_text


SETTING_CLIENT_ID = "tuya.client_id"
SETTING_CLIENT_SECRET = "tuya.client_secret"
SETTING_REGION = "tuya.region"
SETTING_CURRENCY = "application.currency"


class SettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set(self, key: str, value: str, updated_at_utc: datetime | None = None) -> None:
        timestamp = to_utc_text(updated_at_utc or datetime.now(UTC))
        self.connection.execute(
            """
            INSERT INTO settings(key, value, updated_at_utc) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at_utc = excluded.updated_at_utc
            """,
            (key, value, timestamp),
        )

    def save_tuya(self, settings: TuyaSettings, updated_at_utc: datetime | None = None) -> None:
        values = {
            SETTING_CLIENT_ID: settings.client_id,
            SETTING_CLIENT_SECRET: settings.client_secret,
            SETTING_REGION: settings.region,
            SETTING_CURRENCY: settings.currency.value,
        }
        timestamp = updated_at_utc or datetime.now(UTC)
        for key, value in values.items():
            self.set(key, value, timestamp)

    def load_tuya(self) -> TuyaSettings | None:
        values = {
            key: self.get(key)
            for key in (
                SETTING_CLIENT_ID,
                SETTING_CLIENT_SECRET,
                SETTING_REGION,
                SETTING_CURRENCY,
            )
        }
        required = (
            values[SETTING_CLIENT_ID],
            values[SETTING_CLIENT_SECRET],
            values[SETTING_REGION],
        )
        if not any(required):
            return None
        return TuyaSettings(
            client_id=values[SETTING_CLIENT_ID] or "",
            client_secret=values[SETTING_CLIENT_SECRET] or "",
            region=values[SETTING_REGION] or "",
            currency=Currency(values[SETTING_CURRENCY] or Currency.RON.value),
        )


class DatabaseSettingsStore:
    """Thread-safe settings adapter that owns one connection per operation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_tuya(
        self,
        settings: TuyaSettings,
        updated_at_utc: datetime | None = None,
    ) -> None:
        with self.database.connect() as connection:
            SettingsRepository(connection).save_tuya(settings, updated_at_utc)

    def load_tuya(self) -> TuyaSettings | None:
        with self.database.connect() as connection:
            return SettingsRepository(connection).load_tuya()
