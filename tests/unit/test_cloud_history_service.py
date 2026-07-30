from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import Device, EnergySpecification
from tatatuya.infrastructure.repositories.readings import DailyImportResult
from tatatuya.services.cancellation import CancellationContext
from tatatuya.services.cloud_history_service import (
    CloudHistoryService,
    HistoricalScaleContract,
    external_event_key,
    recent_utc_milliseconds,
)
from tatatuya.services.ports import CloudReportEvent


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
SPEC = EnergySpecification("forward_energy_total", "kWh", 2, '{"scale":2}')


class Gateway:
    def __init__(self):
        self.specifications = [SPEC, SPEC]
        self.calls = []
        self.bounds = None

    def get_device_specification(self, device_id):
        self.calls.append("spec")
        return self.specifications.pop(0)

    def list_events(
        self, device_id, code, start_time_ms, end_time_ms, cancellation
    ):
        self.calls.append("logs")
        self.bounds = (start_time_ms, end_time_ms)
        return (
            CloudReportEvent(device_id, code, start_time_ms + 3_600_000, Decimal("10000"), "{}"),
            CloudReportEvent(device_id, code, start_time_ms + 7_200_000, Decimal("10001"), "{}"),
            CloudReportEvent(device_id, code, start_time_ms + 86_400_000, Decimal("10100"), "{}"),
        )


class Store:
    def __init__(self):
        self.candidates = ()

    def import_cloud_daily(self, readings):
        self.candidates = readings
        return DailyImportResult(readings, len(readings), 0)


def test_cloud_import_revalidates_spec_and_reduces_to_earliest_event_per_day() -> None:
    gateway = Gateway()
    store = Store()
    service = CloudHistoryService(
        gateway,
        gateway,
        store,
        HistoricalScaleContract(True, "verified-test-contract"),
        clock=lambda: NOW,
    )

    result = service.import_recent(
        Device("meter-1", "Casa"),
        ZoneInfo("Europe/Amsterdam"),
        CancellationContext(5),
    )

    assert gateway.calls == ["spec", "logs", "spec"]
    assert len(result.readings) == 2
    assert [reading.value_kwh for reading in result.readings] == [
        Decimal("100"),
        Decimal("101"),
    ]
    assert all(reading.source == "cloud_daily" for reading in result.readings)
    assert result.readings[0].cloud_day_timezone == "Europe/Amsterdam"
    assert result.readings[0].cloud_day_utc_offset == "+02:00"
    assert gateway.bounds == (
        int(datetime(2026, 7, 23, 22, tzinfo=UTC).timestamp() * 1000),
        int(NOW.timestamp() * 1000),
    )


def test_unverified_historical_scale_contract_blocks_before_remote_work() -> None:
    gateway = Gateway()
    service = CloudHistoryService(
        gateway,
        gateway,
        Store(),
        HistoricalScaleContract(False),
        clock=lambda: NOW,
    )
    with pytest.raises(UserFacingError, match="scara istorică"):
        service.import_recent(
            Device("meter-1", "Casa"),
            UTC,
            CancellationContext(5),
        )
    assert gateway.calls == []


def test_recent_bounds_cover_seven_local_dates_across_dst_and_cap_at_now() -> None:
    now = datetime(2026, 3, 30, 12, tzinfo=UTC)
    start, end = recent_utc_milliseconds(
        ZoneInfo("Europe/Amsterdam"),
        now,
    )
    assert start == int(datetime(2026, 3, 23, 23, tzinfo=UTC).timestamp() * 1000)
    assert end == int(now.timestamp() * 1000)


def test_fixed_fingerprint_vector() -> None:
    assert external_event_key(
        "meter-1", "forward_energy_total", 1721124000123, "1"
    ) == "6686755712f809e46a97bbac8f3458e54b4e9afed735312157cbbbca042364b9"
