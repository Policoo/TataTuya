"""Validation, connection testing, and persistence for application settings."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import Protocol

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import TuyaSettings
from tatatuya.services.ports import SettingsStore


class SettingsGateway(Protocol):
    def authenticate(self) -> str: ...
    def list_devices(self, **params: object) -> list[object]: ...


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    settings: TuyaSettings
    device_count: int


class SettingsService:
    def __init__(
        self,
        store: SettingsStore,
        gateway_factory: Callable[[TuyaSettings], SettingsGateway],
        supported_regions: Collection[str],
    ) -> None:
        self.store = store
        self.gateway_factory = gateway_factory
        self.supported_regions = frozenset(supported_regions)

    def load(self) -> TuyaSettings | None:
        return self.store.load_tuya()

    def save(self, settings: TuyaSettings) -> TuyaSettings:
        normalized = self.validate(settings)
        self.store.save_tuya(normalized)
        return normalized

    def test_connection(self, settings: TuyaSettings) -> ConnectionTestResult:
        normalized = self.validate(settings)
        try:
            gateway = self.gateway_factory(normalized)
            gateway.authenticate()
        except Exception as exc:
            raise self._connection_error(
                normalized,
                "Autentificarea Tuya nu a reușit. Verificați Client ID, Client Secret și regiunea.",
                exc,
            ) from exc
        try:
            devices = gateway.list_devices()
        except Exception as exc:
            raise self._connection_error(
                normalized,
                "Autentificarea a reușit, dar Tuya nu a permis citirea listei de dispozitive. Verificați permisiunile proiectului cloud.",
                exc,
            ) from exc
        return ConnectionTestResult(normalized, len(devices))

    @staticmethod
    def _connection_error(
        settings: TuyaSettings,
        message: str,
        error: Exception,
    ) -> UserFacingError:
        details = str(error).replace(settings.client_secret, "[REDACTAT]")
        return UserFacingError("Conexiunea Tuya nu a reușit", message, details)

    def validate(self, settings: TuyaSettings) -> TuyaSettings:
        normalized = replace(
            settings,
            client_id=settings.client_id.strip(),
            client_secret=settings.client_secret.strip(),
            region=settings.region.strip(),
        )
        if not normalized.is_complete:
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
