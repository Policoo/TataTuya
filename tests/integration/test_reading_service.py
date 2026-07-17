import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tatatuya.domain.errors import (
    EnergySpecificationError,
    UnsupportedEnergyDeviceError,
    UserFacingError,
)
from tatatuya.domain.models import (
    Currency,
    Device,
    DeviceStatus,
    EnergyEligibility,
    EnergySpecification,
    StatusValue,
    TuyaSettings,
)
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.tuya.client import PreparedRequest, TuyaClient
from tatatuya.infrastructure.tuya.parsers import (
    parse_devices,
    parse_energy_specification,
)
from tatatuya.services.device_service import DeviceService
from tatatuya.services.ports import TuyaGateway
from tatatuya.services.reading_service import ReadingService


NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "tuya_responses"


class FakeGateway:
    def __init__(self, devices: list[Device]) -> None:
        self.devices = devices
        self.specification = EnergySpecification("forward_energy_total", "kWh", 2)
        self.status_code = "forward_energy_total"
        self.values: dict[str, int | Decimal | None] = {
            device.device_id: 12345 for device in devices
        }
        self.omitted: set[str] = set()
        self.failed_batches: set[int] = set()
        self.batch_calls: list[list[str]] = []
        self.specification_calls: list[str] = []
        self.unsupported: set[str] = set()
        self.invalid_specifications: set[str] = set()

    def list_devices(self, **params):
        return self.devices

    def get_device_specification(self, device_id: str):
        self.specification_calls.append(device_id)
        if device_id in self.unsupported:
            raise UnsupportedEnergyDeviceError(
                "no cumulative energy", '{"status":[]}'
            )
        if device_id in self.invalid_specifications:
            raise EnergySpecificationError(
                "invalid specification", '{"status":"invalid"}'
            )
        return self.specification

    def get_devices_status(self, device_ids: list[str]):
        call_index = len(self.batch_calls)
        self.batch_calls.append(device_ids)
        if call_index in self.failed_batches:
            raise RuntimeError("batch offline")
        return {
            device_id: self._status(device_id)
            for device_id in device_ids
            if device_id not in self.omitted
        }

    def get_device_status(self, device_id: str):
        return self._status(device_id)

    def _status(self, device_id: str):
        value = self.values[device_id]
        statuses = () if value is None else (StatusValue(self.status_code, value),)
        return DeviceStatus(
            device_id,
            statuses,
            f'{{"device":"{device_id}","value":"{value}"}}',
        )


def services(tmp_path, gateway: TuyaGateway):
    database = Database(tmp_path / "readings.sqlite3")
    database.initialize()
    connection_context = database.connect()
    connection = connection_context.__enter__()
    devices = DeviceRepository(connection)
    readings = ReadingRepository(connection)
    device_service = DeviceService(gateway, devices, clock=lambda: NOW)
    reading_service = ReadingService(
        gateway, device_service, readings, clock=lambda: NOW
    )
    return connection_context, devices, readings, reading_service


@pytest.mark.parametrize("payload", [{}, {"devices": "not-a-list"}])
def test_malformed_discovery_does_not_mark_cached_meter_absent(
    tmp_path, payload
) -> None:
    class MalformedDiscoveryGateway(FakeGateway):
        def list_devices(self, **params):
            return parse_devices(payload)

    gateway = MalformedDiscoveryGateway([])
    context, devices, _, service = services(tmp_path, gateway)
    try:
        devices.upsert(
            Device(
                "meter-1",
                "Casa",
                energy_eligibility=EnergyEligibility.SUPPORTED,
                present_in_tuya=True,
            ),
            NOW,
        )

        with pytest.raises(UserFacingError, match="Lista dispozitivelor"):
            service.refresh()

        cached = devices.get("meter-1")
        assert cached is not None and cached.present_in_tuya is True
    finally:
        context.__exit__(None, None, None)


def test_refresh_chunks_21_devices_and_preserves_successful_batch(tmp_path) -> None:
    gateway = FakeGateway([Device(f"meter-{index}", f"Contor {index}") for index in range(21)])
    gateway.failed_batches.add(1)
    context, _, readings, service = services(tmp_path, gateway)
    try:
        results = service.refresh()
        assert [len(call) for call in gateway.batch_calls] == [20, 1]
        assert sum(result.succeeded for result in results) == 20
        assert results[-1].error is not None
        assert len(readings.list_for_device("meter-0")) == 1
        assert readings.list_for_device("meter-20") == []
    finally:
        context.__exit__(None, None, None)


def test_equal_refresh_values_create_distinct_readings(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    context, _, readings, service = services(tmp_path, gateway)
    try:
        first = service.refresh()[0]
        second = service.refresh()[0]
        stored = readings.list_for_device("meter-1")
        assert first.reading is not None and second.reading is not None
        assert first.reading.id != second.reading.id
        assert [item.raw_value for item in stored] == ["12345", "12345"]
        assert [item.source for item in stored] == ["batch", "batch"]
    finally:
        context.__exit__(None, None, None)


def test_partial_response_keeps_latest_saved_reading_for_offline_device(tmp_path) -> None:
    gateway = FakeGateway([Device("online", "Online"), Device("offline", "Offline")])
    context, _, readings, service = services(tmp_path, gateway)
    try:
        service.refresh()
        gateway.omitted.add("offline")
        results = {item.device.device_id: item for item in service.refresh()}
        assert results["online"].succeeded
        assert not results["offline"].succeeded
        assert results["offline"].latest_reading == readings.latest_for_device("offline")
        assert len(readings.list_for_device("offline")) == 1
    finally:
        context.__exit__(None, None, None)


def test_missing_energy_is_reported_and_refreshes_cached_specification(tmp_path) -> None:
    remote = Device("meter-1", "Casa", product_id="product")
    gateway = FakeGateway([remote])
    gateway.values["meter-1"] = None
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        devices.upsert(
            Device(
                "meter-1", "Casa", product_id="product",
                energy_code="forward_energy_total", energy_unit="kWh", energy_scale=2,
            ),
            NOW,
        )
        result = service.refresh()[0]
        assert result.error is not None
        assert gateway.specification_calls == ["meter-1"]
        assert readings.list_for_device("meter-1") == []
    finally:
        context.__exit__(None, None, None)


def test_individual_status_stores_every_usable_energy_result(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        devices.upsert(Device("meter-1", "Casa"), NOW)
        first = service.capture_individual_status("meter-1")
        second = service.capture_individual_status("meter-1")
        assert first.reading is not None and second.reading is not None
        assert [item.source for item in readings.list_for_device("meter-1")] == [
            "status", "status"
        ]
    finally:
        context.__exit__(None, None, None)


def test_individual_status_preserves_diagnostics_when_energy_is_missing(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    gateway.values["meter-1"] = None
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        devices.upsert(Device("meter-1", "Casa"), NOW)
        result = service.capture_individual_status("meter-1")
        assert result.status.device_id == "meter-1"
        assert result.reading is None
        assert result.capture_error is not None
        assert readings.list_for_device("meter-1") == []
    finally:
        context.__exit__(None, None, None)


def test_refresh_revalidates_changed_scale_before_each_reading(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    context, _, readings, service = services(tmp_path, gateway)
    try:
        first = service.refresh()[0]
        gateway.specification = EnergySpecification(
            "forward_energy_total", "kWh", 3
        )
        second = service.refresh()[0]

        assert first.reading is not None and second.reading is not None
        assert (first.reading.scale, first.reading.value_kwh) == (2, Decimal("123.45"))
        assert (second.reading.scale, second.reading.value_kwh) == (3, Decimal("12.345"))
        assert [item.scale for item in readings.list_for_device("meter-1")] == [2, 3]
        assert gateway.specification_calls == ["meter-1", "meter-1"]
    finally:
        context.__exit__(None, None, None)


def test_precision_sensitive_decimal_is_persisted_exactly(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    gateway.values["meter-1"] = Decimal("0.12345678901234567890123456789")
    gateway.specification = EnergySpecification(
        "forward_energy_total", "kWh", 0
    )
    context, _, readings, service = services(tmp_path, gateway)
    try:
        result = service.refresh()[0]
        assert result.reading is not None
        assert result.reading.value_kwh == Decimal(
            "0.12345678901234567890123456789"
        )
        stored = readings.list_for_device("meter-1")[0]
        assert stored.raw_value == "0.12345678901234567890123456789"
        assert stored.value_kwh == Decimal("0.12345678901234567890123456789")
        assert "0.12345678901234567890123456789" in stored.raw_status_json
    finally:
        context.__exit__(None, None, None)


def test_circuit_breaker_alias_is_classified_captured_and_persisted(tmp_path) -> None:
    payload = json.loads(
        (FIXTURES / "specification_total_forward.json").read_text(encoding="utf-8")
    )
    gateway = FakeGateway([Device("meter-1", "Casa", category="dlq")])
    gateway.specification = parse_energy_specification(payload["result"])
    gateway.status_code = gateway.specification.code
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        result = service.refresh()[0]
        stored_device = devices.get("meter-1")

        assert result.error is None
        assert result.reading is not None
        assert result.reading.value_kwh == Decimal("123.45")
        assert result.reading.source_unit == "kW·h"
        assert result.reading.raw_specification_json == gateway.specification.raw_json
        assert stored_device is not None
        assert stored_device.energy_eligibility is EnergyEligibility.SUPPORTED
        assert stored_device.energy_code == "total_forward_energy"
        assert readings.latest_for_device("meter-1") == result.reading
    finally:
        context.__exit__(None, None, None)


def test_mixed_account_classifies_and_omits_non_meter_from_capture(tmp_path) -> None:
    gateway = FakeGateway(
        [Device("meter-1", "Casa"), Device("lamp-1", "Lampă")]
    )
    gateway.unsupported.add("lamp-1")
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        results = {item.device.device_id: item for item in service.refresh()}

        assert results["meter-1"].reading is not None
        assert results["lamp-1"].reading is None
        assert results["lamp-1"].error is None
        lamp = devices.get("lamp-1")
        assert lamp is not None
        assert lamp.energy_eligibility is EnergyEligibility.UNSUPPORTED
        assert readings.list_for_device("lamp-1") == []
        assert gateway.batch_calls == [["meter-1"]]
    finally:
        context.__exit__(None, None, None)


def test_invalid_specification_remains_unknown_instead_of_unsupported(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    gateway.invalid_specifications.add("meter-1")
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        result = service.refresh()[0]
        stored = devices.get("meter-1")
        assert result.error is not None
        assert result.reading is None
        assert stored is not None
        assert stored.energy_eligibility is EnergyEligibility.UNKNOWN
        assert stored.raw_specification_json == '{"status":"invalid"}'
        assert readings.list_for_device("meter-1") == []
    finally:
        context.__exit__(None, None, None)


def test_disappeared_meter_remains_in_refresh_results_with_history(tmp_path) -> None:
    meter = Device("meter-1", "Casa")
    garage = Device("meter-2", "Garaj")
    gateway = FakeGateway([meter, garage])
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        service.refresh()
        garage_latest = readings.latest_for_device("meter-2")
        gateway.devices = [meter]

        results = {item.device.device_id: item for item in service.refresh()}

        assert set(results) == {"meter-1", "meter-2"}
        assert results["meter-2"].device.present_in_tuya is False
        assert results["meter-2"].latest_reading == garage_latest
        assert results["meter-2"].reading is None
        assert len(readings.list_for_device("meter-2")) == 1
        stored_garage = devices.get("meter-2")
        assert stored_garage is not None and stored_garage.present_in_tuya is False
    finally:
        context.__exit__(None, None, None)


def test_historical_meter_becoming_unsupported_is_not_a_refresh_failure(
    tmp_path,
) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    context, _, readings, service = services(tmp_path, gateway)
    try:
        first = service.refresh()[0]
        gateway.unsupported.add("meter-1")

        second = service.refresh()[0]

        assert first.reading is not None
        assert second.error is None
        assert second.reading is None
        assert second.latest_reading == first.reading
        assert readings.list_for_device("meter-1") == [first.reading]
    finally:
        context.__exit__(None, None, None)


def test_reading_retains_exact_redacted_specification_snapshot(tmp_path) -> None:
    gateway = FakeGateway([Device("meter-1", "Casa")])
    raw_specification = (
        '{"status":[{"code":"forward_energy_total",'
        '"values":{"scale":2,"unit":"kWh"}}]}'
    )
    gateway.specification = EnergySpecification(
        "forward_energy_total", "kWh", 2, raw_specification
    )
    context, devices, readings, service = services(tmp_path, gateway)
    try:
        result = service.refresh()[0]
        assert result.reading is not None
        assert result.reading.raw_specification_json == raw_specification
        assert readings.list_for_device("meter-1")[0].raw_specification_json == raw_specification
        stored_device = devices.get("meter-1")
        assert stored_device is not None
        assert stored_device.raw_specification_json == raw_specification
    finally:
        context.__exit__(None, None, None)


def test_paginated_client_devices_are_all_refreshed(tmp_path) -> None:
    specification = {
        "success": True,
        "result": {
            "status": [
                {
                    "code": "forward_energy_total",
                    "values": {"unit": "kWh", "scale": 2},
                }
            ]
        },
    }
    responses = [
        {
            "success": True,
            "result": {
                "devices": [{"id": "meter-1", "name": "Casa"}],
                "has_more": True,
                "last_row_key": "next",
            },
        },
        {
            "success": True,
            "result": {
                "devices": [{"id": "meter-2", "name": "Garaj"}],
                "has_more": False,
            },
        },
        specification,
        specification,
        {
            "success": True,
            "result": [
                {
                    "id": "meter-1",
                    "status": [{"code": "forward_energy_total", "value": 10000}],
                },
                {
                    "id": "meter-2",
                    "status": [{"code": "forward_energy_total", "value": 20000}],
                },
            ],
        },
    ]

    class Transport:
        def __init__(self):
            self.requests: list[PreparedRequest] = []

        def send(self, request: PreparedRequest):
            self.requests.append(request)
            return responses.pop(0)

    transport = Transport()
    client = TuyaClient(
        TuyaSettings(
            "client", "secret", "central_europe", Currency.RON
        ),
        transport=transport,
        clock_ms=lambda: "1721124000000",
    )
    client.access_token = "token"
    context, _, readings, service = services(tmp_path, client)
    try:
        results = service.refresh()
        assert [result.device.device_id for result in results] == [
            "meter-1", "meter-2"
        ]
        assert all(result.succeeded for result in results)
        assert len(readings.list_for_device("meter-1")) == 1
        assert len(readings.list_for_device("meter-2")) == 1
        assert "last_row_key=next" in transport.requests[1].url
    finally:
        context.__exit__(None, None, None)
