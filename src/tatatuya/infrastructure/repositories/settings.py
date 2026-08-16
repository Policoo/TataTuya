"""Application-setting persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.secrets import (
    TUYA_CLIENT_SECRET_ACCOUNT,
    SecretStore,
    SecretStoreError,
)
from tatatuya.infrastructure.repositories._mapping import to_utc_text


SETTING_CLIENT_ID = "tuya.client_id"
SETTING_REGION = "tuya.region"
SETTING_CURRENCY = "application.currency"


class ClientSecretUnavailableError(UserFacingError):
    """Expected credential-backend failure for optional remote capability."""


class SettingsRepository:
    def __init__(self, connection: Any, secret_store: SecretStore) -> None:
        self.connection = connection
        self.secret_store = secret_store

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

    def save_tuya(
        self,
        settings: TuyaSettings,
        updated_at_utc: datetime | None = None,
        cancellation: CancellationContext | None = None,
    ) -> None:
        encoded_secret = settings.client_secret.encode("utf-8")
        _checkpoint(cancellation)
        try:
            self.secret_store.set(
                TUYA_CLIENT_SECRET_ACCOUNT,
                encoded_secret,
                label="TataTuya Tuya Client Secret",
                cancellation=cancellation,
            )
            _checkpoint(cancellation)
            if (
                self.secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT, cancellation)
                != encoded_secret
            ):
                raise SecretStoreError("round-trip")
        except SecretStoreError as exc:
            raise _secret_store_error(exc) from exc
        _checkpoint(cancellation)
        self.save_non_secret_tuya(settings, updated_at_utc, cancellation)

    def save_non_secret_tuya(
        self,
        settings: TuyaSettings,
        updated_at_utc: datetime | None = None,
        cancellation: CancellationContext | None = None,
    ) -> None:
        values = {
            SETTING_CLIENT_ID: settings.client_id,
            SETTING_REGION: settings.region,
            SETTING_CURRENCY: settings.currency.value,
        }
        timestamp = updated_at_utc or datetime.now(UTC)
        for key, value in values.items():
            _checkpoint(cancellation)
            self.set(key, value, timestamp)

    def load_tuya(
        self, cancellation: CancellationContext | None = None
    ) -> TuyaSettings | None:
        values = self.load_non_secret_tuya()
        _checkpoint(cancellation)
        try:
            stored_secret = self.secret_store.get(
                TUYA_CLIENT_SECRET_ACCOUNT, cancellation
            )
        except SecretStoreError as exc:
            raise _secret_store_error(exc) from exc
        _checkpoint(cancellation)
        try:
            client_secret = "" if stored_secret is None else stored_secret.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _secret_store_error(SecretStoreError("decode")) from exc
        if values is None and not client_secret:
            return None
        metadata = values or TuyaSettings("", "", "", Currency.RON)
        return TuyaSettings(
            client_id=metadata.client_id,
            client_secret=client_secret,
            region=metadata.region,
            currency=metadata.currency,
        )

    def load_non_secret_tuya(self) -> TuyaSettings | None:
        values: dict[str, str | None] = {
            key: self.get(key)
            for key in (
                SETTING_CLIENT_ID,
                SETTING_REGION,
                SETTING_CURRENCY,
            )
        }
        required = (
            values[SETTING_CLIENT_ID],
            values[SETTING_REGION],
        )
        if not any(required):
            return None
        return TuyaSettings(
            client_id=values[SETTING_CLIENT_ID] or "",
            client_secret="",
            region=values[SETTING_REGION] or "",
            currency=Currency(values[SETTING_CURRENCY] or Currency.RON.value),
        )

    def load_currency(self) -> Currency:
        """Load the local billing setting independently from Tuya credentials."""

        return Currency(self.get(SETTING_CURRENCY) or Currency.RON.value)


class DatabaseSettingsStore:
    """Thread-safe settings adapter that owns one connection per operation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_tuya(
        self,
        settings: TuyaSettings,
        updated_at_utc: datetime | None = None,
        cancellation: CancellationContext | None = None,
    ) -> None:
        _checkpoint(cancellation)
        encoded_secret = settings.client_secret.encode("utf-8")
        try:
            secret_store = self.database.client_secret_store()
            secret_store.set(
                TUYA_CLIENT_SECRET_ACCOUNT,
                encoded_secret,
                label="TataTuya Tuya Client Secret",
                cancellation=cancellation,
            )
            _checkpoint(cancellation)
            if (
                secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT, cancellation)
                != encoded_secret
            ):
                raise SecretStoreError("round-trip")
        except SecretStoreError as exc:
            raise self._secret_store_error(exc) from exc
        # This is the cross-store cancellation boundary. A cancelled save may
        # leave only the new secret, and a safe retry completes the metadata.
        _checkpoint(cancellation)
        with self.database.connect(cancellation) as connection:
            _checkpoint(cancellation)
            SettingsRepository(
                connection, self.database.client_secret_store()
            ).save_non_secret_tuya(settings, updated_at_utc, cancellation)

    def load_tuya(
        self, cancellation: CancellationContext | None = None
    ) -> TuyaSettings | None:
        try:
            with self.database.connect(cancellation) as connection:
                return SettingsRepository(
                    connection, self.database.client_secret_store()
                ).load_tuya(cancellation)
        except SecretStoreError as exc:
            raise self._secret_store_error(exc) from exc

    @staticmethod
    def _secret_store_error(exc: SecretStoreError) -> UserFacingError:
        return _secret_store_error(exc)


def _checkpoint(cancellation: CancellationContext | None) -> None:
    if cancellation is not None:
        cancellation.checkpoint()


def _secret_store_error(exc: SecretStoreError) -> ClientSecretUnavailableError:
    return ClientSecretUnavailableError(
        "Client Secret nu este disponibil",
        "TataTuya nu poate accesa Client Secret salvat. Verificați spațiul de stocare al acreditărilor și încercați din nou.",
        f"Secret storage operation failed: {exc.operation}",
    )
