from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

from tatatuya.domain.billing import calculate_period
from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.calculations import (
    CalculationRepository,
    DevicePreferenceRepository,
)
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import SettingsRepository
from tatatuya.infrastructure.tuya.client import PreparedRequest, TuyaClient
from tatatuya.services.billing_service import BillingService
from tatatuya.services.device_service import DeviceService
from tatatuya.services.history_service import HistoryService
from tatatuya.services.reading_service import ReadingService


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "tuya_responses"
STARTED_AT = datetime(2026, 12, 3, 18, 42, tzinfo=UTC)


def fixture(name: str) -> dict[str, object]:
    return json.loads(
        (FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"),
        parse_float=Decimal,
    )


class FixtureTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[PreparedRequest] = []

    def send(self, request: PreparedRequest) -> dict[str, object]:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"Unexpected request: {request.url}")
        return self.responses.pop(0)


def test_fresh_database_primary_workflow_reconstructs_saved_calculation(
    tmp_path,
) -> None:
    database = Database(tmp_path / "release-readiness.sqlite3")
    database.initialize()
    settings = TuyaSettings(
        "fixture-client-id",
        "fixture-client-secret",
        "central_europe",
        Currency.RON,
    )
    transport = FixtureTransport(
        [
            fixture("token.json"),
            fixture("devices.json"),
            fixture("specification.json"),
            fixture("specification.json"),
            fixture("batch_status_partial.json"),
            fixture("individual_status_later.json"),
            fixture("specification.json"),
        ]
    )
    reading_times = iter(
        (STARTED_AT, STARTED_AT, STARTED_AT + timedelta(minutes=30))
    )

    with database.connect() as connection:
        SettingsRepository(connection).save_tuya(settings, STARTED_AT)
        devices = DeviceRepository(connection)
        readings = ReadingRepository(connection)
        client = TuyaClient(
            settings,
            transport=transport,
            clock_ms=lambda: "1796323320000",
        )
        device_service = DeviceService(
            client,
            devices,
            clock=lambda: STARTED_AT,
        )
        reading_service = ReadingService(
            client,
            device_service,
            readings,
            clock=lambda: next(reading_times),
        )

        refresh = reading_service.refresh()
        meter_result = next(
            item for item in refresh if item.device.device_id == "meter-1"
        )
        assert meter_result.reading is not None
        assert meter_result.reading.value_kwh == Decimal("1234.56")

        later = reading_service.capture_individual_status("meter-1")
        assert later.reading is not None
        assert later.reading.value_kwh == Decimal("1247.06")

        billing = BillingService(
            readings,
            CalculationRepository(connection),
            DevicePreferenceRepository(connection),
            now=lambda: STARTED_AT + timedelta(minutes=31),
        )
        calculation_context = billing.prepare("meter-1", Currency.RON)
        saved = billing.save_calculation(
            "meter-1",
            calculation_context.default_start_reading_id,
            calculation_context.default_end_reading_id,
            "0,80",
            Currency.RON,
        )
        history = HistoryService(
            readings,
            CalculationRepository(connection),
        ).prepare("meter-1")

        assert saved.consumption_kwh == Decimal("12.50")
        assert saved.total == Decimal("10.0000")
        assert len(history.readings) == 2
        assert history.calculations[0].calculation == saved
        stored_device = devices.get("meter-1")
        assert stored_device is not None
        assert "must-not-be-persisted" not in stored_device.raw_device_json
        assert "[REDACTED]" in stored_device.raw_device_json

    with database.connect() as connection:
        reloaded_settings = SettingsRepository(connection).load_tuya()
        reloaded_readings = ReadingRepository(connection).list_for_device("meter-1")
        reloaded = CalculationRepository(connection).latest_for_device("meter-1")
        restarted_history = HistoryService(
            ReadingRepository(connection),
            CalculationRepository(connection),
        ).prepare("meter-1")

    assert reloaded_settings == settings
    assert reloaded is not None
    assert len(reloaded_readings) == 2
    reconstructed = calculate_period(
        reloaded_readings[0],
        reloaded_readings[1],
        reloaded.unit_price,
        reloaded.currency,
        reloaded.created_at_utc,
    )
    assert reconstructed == replace(reloaded, id=None)
    assert restarted_history.calculations[0].calculation == reloaded
    assert all(request.method == "GET" for request in transport.requests)
    assert not transport.responses
