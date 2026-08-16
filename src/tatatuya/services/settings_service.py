"""Validation, connection testing, and persistence for application settings."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import TuyaSettings
from tatatuya.services.ports import SettingsStore


class SettingsGateway(Protocol):
    def authenticate(self) -> str: ...
    def list_devices(self, **params: Any) -> Sequence[object]: ...


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    settings: TuyaSettings
    device_count: int


class SettingsService:
    def __init__(
        self,
        store: SettingsStore,
        gateway_factory: Callable[
            [TuyaSettings, CancellationContext | None], SettingsGateway
        ],
        supported_regions: Collection[str],
    ) -> None:
        self.store = store
        self.gateway_factory = gateway_factory
        self.supported_regions = frozenset(supported_regions)

    def load(
        self, cancellation: CancellationContext | None = None
    ) -> TuyaSettings | None:
        if cancellation is None:
            return self.store.load_tuya()
        cancellation.checkpoint()
        return self.store.load_tuya(cancellation=cancellation)

    def save(
        self,
        settings: TuyaSettings,
        cancellation: CancellationContext | None = None,
        *,
        preserve_stored_secret: bool = False,
    ) -> TuyaSettings:
        if cancellation is not None:
            cancellation.checkpoint()
        normalized = self._resolve(settings, preserve_stored_secret, cancellation)
        if cancellation is None:
            self.store.save_tuya(normalized)
        else:
            self.store.save_tuya(normalized, cancellation=cancellation)
        return replace(normalized, client_secret="")

    def test_connection(
        self,
        settings: TuyaSettings,
        cancellation: CancellationContext | None = None,
        *,
        preserve_stored_secret: bool = False,
    ) -> ConnectionTestResult:
        if cancellation is not None:
            cancellation.checkpoint()
        normalized = self._resolve(settings, preserve_stored_secret, cancellation)
        try:
            gateway = self.gateway_factory(normalized, cancellation)
            gateway.authenticate()
        except Exception as exc:
            raise self._connection_error(
                normalized,
                "Autentificarea Tuya nu a reușit. Verificați Client ID, Client Secret și regiunea.",
                exc,
            ) from exc
        if cancellation is not None:
            cancellation.checkpoint()
        try:
            devices = gateway.list_devices()
        except Exception as exc:
            raise self._connection_error(
                normalized,
                "Autentificarea a reușit, dar Tuya nu a permis citirea listei de dispozitive. Verificați permisiunile proiectului cloud.",
                exc,
            ) from exc
        if cancellation is not None:
            cancellation.checkpoint()
        return ConnectionTestResult(
            replace(normalized, client_secret=""),
            len(devices),
        )

    def _resolve(
        self,
        settings: TuyaSettings,
        preserve_stored_secret: bool,
        cancellation: CancellationContext | None = None,
    ) -> TuyaSettings:
        candidate = settings
        if preserve_stored_secret and not settings.client_secret.strip():
            stored = (
                self.store.load_tuya()
                if cancellation is None
                else self.store.load_tuya(cancellation=cancellation)
            )
            if stored is not None and stored.client_secret:
                candidate = replace(settings, client_secret=stored.client_secret)
        return self.validate(candidate)

    @staticmethod
    def _connection_error(
        settings: TuyaSettings,
        message: str,
        error: Exception,
    ) -> UserFacingError:
        details = str(error).replace(settings.client_secret, "[REDACTED]")
        return UserFacingError("Conexiunea Tuya nu a reușit", message, details)

    def validate(
        self, settings: TuyaSettings, *, allow_stored_secret: bool = False
    ) -> TuyaSettings:
        normalized = replace(
            settings,
            client_id=settings.client_id.strip(),
            client_secret=settings.client_secret.strip(),
            region=settings.region.strip(),
        )
        connection_fields_complete = bool(
            normalized.client_id
            and normalized.region
            and (normalized.client_secret or allow_stored_secret)
        )
        if not connection_fields_complete:
            raise UserFacingError(
                "Setări incomplete",
                "Completați Client ID, Client Secret și regiunea Tuya.",
            )
        if normalized.region not in self.supported_regions:
            raise UserFacingError(
                "Regiune Tuya neacceptată",
                "Selectați una dintre regiunile Tuya disponibile.",
            )
        return normalized
