from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tatatuya.domain.billing import calculate_period
from tatatuya.domain.models import Currency, Device, Reading, TuyaSettings
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.calculations import (
    CalculationRepository,
    DevicePreferenceRepository,
)
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import SettingsRepository


NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)


def initialized_database(tmp_path) -> Database:
    database = Database(tmp_path / "tatatuya.sqlite3")
    database.initialize()
    return database


def test_empty_database_migrates_idempotently(tmp_path) -> None:
    database = initialized_database(tmp_path)
    database.initialize()
    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert [row[0] for row in versions] == [1]
    assert {"settings", "devices", "readings", "calculations"} <= tables


def test_settings_survive_new_connection(tmp_path) -> None:
    database = initialized_database(tmp_path)
    settings = TuyaSettings("client", "secret", "uid", "central_europe", Currency.EUR)
    with database.connect() as connection:
        SettingsRepository(connection).save_tuya(settings, NOW)
    with database.connect() as connection:
        assert SettingsRepository(connection).load_tuya() == settings


def test_device_upsert_preserves_history_and_equal_readings(tmp_path) -> None:
    database = initialized_database(tmp_path)
    first_device = Device("meter-1", "Casa Veche", online=True, raw_device_json='{"v":1}')
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        saved_device = devices.upsert(first_device, NOW)
        readings = ReadingRepository(connection)
        first = readings.add(
            Reading("meter-1", NOW, "12345", 2, "kWh", Decimal("123.45"), "batch", "{}")
        )
        second = readings.add(
            Reading("meter-1", NOW, "12345", 2, "kWh", Decimal("123.45"), "batch", "{}")
        )
        updated = devices.upsert(
            Device("meter-1", "Casa Nouă", online=False, raw_device_json='{"v":2}'),
            NOW + timedelta(days=1),
        )
        stored = readings.list_for_device("meter-1")

    assert first.id != second.id
    assert [item.value_kwh for item in stored] == [Decimal("123.45"), Decimal("123.45")]
    assert updated.name == "Casa Nouă"
    assert updated.first_seen_at_utc == saved_device.first_seen_at_utc


def test_calculation_is_immutable_after_settings_and_preference_changes(tmp_path) -> None:
    database = initialized_database(tmp_path)
    with database.connect() as connection:
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
        readings = ReadingRepository(connection)
        start = readings.add(
            Reading("meter-1", NOW, "10000", 2, "kWh", Decimal("100"), "batch", "{}")
        )
        end = readings.add(
            Reading(
                "meter-1", NOW + timedelta(days=30), "11250", 2, "kWh",
                Decimal("112.5"), "batch", "{}",
            )
        )
        calculation = calculate_period(
            start, end, Decimal("0.8"), Currency.RON, NOW + timedelta(days=30)
        )
        saved = CalculationRepository(connection).add(calculation)
        DevicePreferenceRepository(connection).save_price(
            "meter-1", Decimal("1.25"), Currency.EUR, NOW + timedelta(days=31)
        )
        SettingsRepository(connection).set("application.currency", "EUR", NOW)

    with database.connect() as connection:
        reloaded = CalculationRepository(connection).latest_for_device("meter-1")
    assert reloaded == saved
    assert reloaded is not None
    assert reloaded.currency is Currency.RON
    assert reloaded.unit_price == Decimal("0.8")
    assert reloaded.total == Decimal("10")


def test_failed_transaction_rolls_back(tmp_path) -> None:
    database = initialized_database(tmp_path)
    try:
        with database.connect() as connection:
            DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with database.connect() as connection:
        assert DeviceRepository(connection).get("meter-1") is None

