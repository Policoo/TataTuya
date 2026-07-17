from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tatatuya.domain.models import Currency, Device, Reading
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.calculations import (
    CalculationRepository,
    DevicePreferenceRepository,
)
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.services.billing_service import BillingService


NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def seed(database: Database) -> None:
    database.initialize()
    with database.connect() as connection:
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
        readings = ReadingRepository(connection)
        for value, day in (("100", 0), ("112.5", 1), ("120", 2)):
            readings.add(
                Reading(
                    "meter-1",
                    NOW + timedelta(days=day),
                    value,
                    2,
                    "kWh",
                    Decimal(value),
                    "batch",
                    "{}",
                )
            )


def test_saved_preview_and_meter_preference_survive_restart(tmp_path) -> None:
    database = Database(tmp_path / "billing.sqlite3")
    seed(database)
    with database.connect() as connection:
        service = BillingService(
            ReadingRepository(connection),
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
            lambda: NOW + timedelta(days=3),
        )
        context = service.prepare("meter-1", Currency.RON)
        start, end = context.readings[0], context.readings[-1]
        assert start.id is not None and end.id is not None
        preview = service.preview(start, end, "0,80", Currency.RON, None)
        saved = service.save_calculation(
            "meter-1", start.id, end.id, "0,80", Currency.RON
        )

    with database.connect() as connection:
        calculations = CalculationRepository(connection)
        preferences = DevicePreferenceRepository(connection)
        reloaded = calculations.latest_for_device("meter-1")
        preference = preferences.get("meter-1")
        next_context = BillingService(
            ReadingRepository(connection), calculations, preferences
        ).prepare("meter-1", Currency.RON)

    assert reloaded == saved
    assert saved.consumption_kwh == preview.consumption_kwh
    assert saved.unit_price == preview.unit_price
    assert saved.currency == preview.currency
    assert saved.total == preview.total
    assert preference is not None
    assert preference.last_unit_price == Decimal("0.80")
    assert next_context.default_start_reading_id == saved.end_reading_id
    assert next_context.remembered_unit_price == Decimal("0.80")


def test_calculation_and_preference_update_roll_back_together(tmp_path) -> None:
    database = Database(tmp_path / "rollback.sqlite3")
    seed(database)

    class FailingPreferences(DevicePreferenceRepository):
        def save_price(self, *args, **kwargs):
            super().save_price(*args, **kwargs)
            raise RuntimeError("preference write failed")

    with pytest.raises(RuntimeError, match="preference write failed"):
        with database.connect() as connection:
            readings = ReadingRepository(connection)
            values = readings.list_for_device("meter-1")
            assert values[0].id is not None and values[-1].id is not None
            BillingService(
                readings,
                CalculationRepository(connection),
                FailingPreferences(connection),
                lambda: NOW,
            ).save_calculation(
                "meter-1", values[0].id, values[-1].id, "1", Currency.RON
            )

    with database.connect() as connection:
        assert CalculationRepository(connection).list_for_device("meter-1") == []
        assert DevicePreferenceRepository(connection).get("meter-1") is None
