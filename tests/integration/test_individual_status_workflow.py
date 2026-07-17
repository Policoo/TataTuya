from decimal import Decimal

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import (
    Currency,
    Device,
    DeviceStatus,
    EnergySpecification,
    StatusValue,
    TuyaSettings,
)
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import SettingsRepository
from tatatuya.ui.app import _capture_status


class Gateway:
    def __init__(self, settings: TuyaSettings) -> None:
        self.settings = settings

    def get_device_status(self, device_id: str) -> DeviceStatus:
        return DeviceStatus(
            device_id,
            (StatusValue("forward_energy_total", 123456),),
            '{"status":[{"code":"forward_energy_total","value":123456}]}',
        )

    def get_device_specification(self, device_id: str) -> EnergySpecification:
        return EnergySpecification("forward_energy_total", "kWh", 2)


def configured_database(tmp_path) -> Database:
    database = Database(tmp_path / "status.sqlite3")
    database.initialize()
    with database.connect() as connection:
        SettingsRepository(connection).save_tuya(
            TuyaSettings("client", "secret", "central_europe", Currency.RON)
        )
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"))
    return database


def test_application_status_workflow_records_each_usable_individual_call(
    tmp_path, monkeypatch
) -> None:
    database = configured_database(tmp_path)
    monkeypatch.setattr("tatatuya.ui.app.TuyaClient", Gateway)

    first = _capture_status(database, "meter-1")
    second = _capture_status(database, "meter-1")

    assert first.status.statuses[0].code == "forward_energy_total"
    assert first.reading is not None and second.reading is not None
    assert first.reading.value_kwh == Decimal("1234.56")
    with database.connect() as connection:
        stored = ReadingRepository(connection).list_for_device("meter-1")
    assert [reading.source for reading in stored] == ["status", "status"]
    assert stored[0].id != stored[1].id


def test_application_status_workflow_requires_saved_settings(tmp_path) -> None:
    database = Database(tmp_path / "unconfigured.sqlite3")
    database.initialize()

    with pytest.raises(UserFacingError) as raised:
        _capture_status(database, "meter-1")

    assert raised.value.title == "Setări incomplete"


def test_application_status_workflow_converts_request_failure_to_user_error(
    tmp_path, monkeypatch
) -> None:
    database = configured_database(tmp_path)

    class FailingGateway(Gateway):
        def get_device_status(self, device_id: str) -> DeviceStatus:
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr("tatatuya.ui.app.TuyaClient", FailingGateway)

    with pytest.raises(UserFacingError) as raised:
        _capture_status(database, "meter-1")

    assert raised.value.title == "Status indisponibil"
    assert "Casa" in raised.value.message
    with database.connect() as connection:
        assert ReadingRepository(connection).list_for_device("meter-1") == []
