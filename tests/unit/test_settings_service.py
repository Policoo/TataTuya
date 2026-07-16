from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.services.settings_service import SettingsService


REGIONS = {"central_europe", "western_europe"}


class MemorySettings:
    def __init__(self, settings=None) -> None:
        self.settings = settings

    def load_tuya(self):
        return self.settings

    def save_tuya(self, settings, updated_at_utc=None) -> None:
        self.settings = settings


class Gateway:
    def __init__(self, devices=None, error=None) -> None:
        self.devices = devices or []
        self.error = error
        self.authenticated = False

    def authenticate(self):
        if self.error:
            raise self.error
        self.authenticated = True
        return "token"

    def list_devices(self, **params):
        assert self.authenticated
        return self.devices


def settings(**changes) -> TuyaSettings:
    values = {
        "client_id": "client",
        "client_secret": "secret",
        "region": "central_europe",
        "currency": Currency.RON,
    }
    values.update(changes)
    return TuyaSettings(**values)


def test_save_validates_normalizes_and_persists() -> None:
    store = MemorySettings()
    service = SettingsService(store, lambda value: Gateway(), REGIONS)

    saved = service.save(
        settings(
            client_id="  client  ",
            client_secret="  secret  ",
            region=" central_europe ",
            currency=Currency.EUR,
        )
    )

    assert saved == settings(currency=Currency.EUR)
    assert store.settings == saved


def test_incomplete_or_unknown_region_is_not_persisted() -> None:
    store = MemorySettings()
    service = SettingsService(store, lambda value: Gateway(), REGIONS)

    for candidate, title in (
        (settings(client_secret=""), "Setări incomplete"),
        (settings(region="moon"), "Regiune Tuya neacceptată"),
    ):
        try:
            service.save(candidate)
        except UserFacingError as error:
            assert error.title == title
        else:
            raise AssertionError("Expected a user-facing validation error")
    assert store.settings is None


def test_connection_test_authenticates_and_checks_device_access() -> None:
    gateway = Gateway([object(), object()])
    service = SettingsService(MemorySettings(), lambda value: gateway, REGIONS)

    result = service.test_connection(settings())
    assert result.settings == settings()
    assert result.device_count == 2
    assert gateway.authenticated


def test_device_list_permission_failure_identifies_the_failed_step() -> None:
    class ListFailureGateway(Gateway):
        def list_devices(self, **params):
            raise RuntimeError("permission deny")

    service = SettingsService(MemorySettings(), lambda value: ListFailureGateway(), REGIONS)

    try:
        service.test_connection(settings())
    except UserFacingError as error:
        assert "Autentificarea a reușit" in error.message
        assert "listei de dispozitive" in error.message
        assert error.technical_details == "permission deny"
    else:
        raise AssertionError("Expected device-list permission failure")


def test_connection_failure_is_safe_and_does_not_persist() -> None:
    store = MemorySettings()
    gateway = Gateway(error=RuntimeError("secret rejected"))
    service = SettingsService(store, lambda value: gateway, REGIONS)

    try:
        service.test_connection(settings())
    except UserFacingError as error:
        assert error.title == "Conexiunea Tuya nu a reușit"
        assert "secret" not in (error.technical_details or "")
        assert "[REDACTAT]" in (error.technical_details or "")
    else:
        raise AssertionError("Expected a user-facing connection error")
    assert store.settings is None
