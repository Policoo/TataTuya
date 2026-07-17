import json
from decimal import Decimal
from pathlib import Path

import pytest

from tatatuya.domain.errors import UnsupportedEnergyDeviceError
from tatatuya.infrastructure.tuya.parsers import (
    TuyaPayloadError,
    parse_batch_status,
    parse_devices,
    parse_energy_specification,
    parse_individual_status,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "tuya_responses"


def fixture_result(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["result"]


def test_device_fixture_maps_to_domain_devices() -> None:
    devices = parse_devices(fixture_result("devices.json"))
    assert [(item.device_id, item.name, item.online) for item in devices] == [
        ("meter-1", "Casa Părinților", True),
        ("meter-2", "Garaj", False),
    ]
    assert json.loads(devices[0].raw_device_json)["product_id"] == "product-1"
    assert json.loads(devices[0].raw_device_json)["local_key"] == "[REDACTED]"
    assert "must-not-be-persisted" not in devices[0].raw_device_json


@pytest.mark.parametrize(
    "payload",
    [{}, {"devices": "not-a-list"}, {"devices": ["not-an-object"]}],
)
def test_malformed_device_collection_is_rejected(payload) -> None:
    with pytest.raises(TuyaPayloadError, match="Device collection"):
        parse_devices(payload)


def test_structurally_valid_empty_device_collection_is_accepted() -> None:
    assert parse_devices({"devices": [], "has_more": False}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"devices": [], "has_more": "false"},
        {"devices": [], "has_more": True},
        {"devices": [], "has_more": True, "last_row_key": {}},
    ],
)
def test_malformed_device_pagination_is_rejected(payload) -> None:
    with pytest.raises(TuyaPayloadError, match="pagination"):
        parse_devices(payload)


def test_specification_fixture_finds_scale_and_unit() -> None:
    specification = parse_energy_specification(fixture_result("specification.json"))
    assert (specification.code, specification.unit, specification.scale) == (
        "forward_energy_total", "kWh", 2
    )


def test_circuit_breaker_fixture_preserves_supported_alias_and_unit() -> None:
    specification = parse_energy_specification(
        fixture_result("specification_total_forward.json")
    )
    assert (specification.code, specification.unit, specification.scale) == (
        "total_forward_energy",
        "kW·h",
        2,
    )


def test_ambiguous_energy_specification_is_rejected() -> None:
    supported_row = {
        "code": "forward_energy_total",
        "values": {"unit": "kWh", "scale": 2},
    }
    unsupported_row = {
        "code": "forward_energy_total",
        "values": {"unit": "MWh", "scale": 2},
    }
    with pytest.raises(TuyaPayloadError, match="exactly one"):
        parse_energy_specification({"status": [supported_row, unsupported_row]})


def test_unsupported_unit_is_classified_as_non_billable() -> None:
    with pytest.raises(UnsupportedEnergyDeviceError):
        parse_energy_specification(
            {
                "status": [
                    {
                        "code": "forward_energy_total",
                        "values": {"unit": "MWh", "scale": 2},
                    }
                ]
            }
        )


def test_invalid_scale_remains_a_payload_failure() -> None:
    with pytest.raises(TuyaPayloadError, match="scale or unit"):
        parse_energy_specification(
            {
                "status": [
                    {
                        "code": "forward_energy_total",
                        "values": {"unit": "kWh", "scale": -1},
                    }
                ]
            }
        )


@pytest.mark.parametrize("payload", [{}, {"status": {}}, {"status": "invalid"}])
def test_missing_or_malformed_specification_collection_is_recoverable(payload) -> None:
    with pytest.raises(TuyaPayloadError, match="status collection"):
        parse_energy_specification(payload)


def test_valid_specification_without_energy_is_unsupported() -> None:
    with pytest.raises(UnsupportedEnergyDeviceError):
        parse_energy_specification(
            {"status": [{"code": "switch", "values": {"type": "bool"}}]}
        )


def test_status_fixtures_preserve_raw_values_and_map_batch_by_id() -> None:
    individual = parse_individual_status(
        "meter-1", fixture_result("individual_status.json")
    )
    batch = parse_batch_status(fixture_result("batch_status_partial.json"))
    assert individual.value_for("forward_energy_total") == 123456
    assert list(batch) == ["meter-2", "meter-1"]
    assert batch["meter-1"].value_for("switch") is True
    assert "forward_energy_total" in batch["meter-2"].raw_json


def test_decimal_status_diagnostic_remains_an_exact_json_number() -> None:
    payload = json.loads(
        (FIXTURES / "individual_status_decimal.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
    )
    status = parse_individual_status("meter-1", payload["result"])
    raw_value = json.loads(status.raw_json, parse_float=Decimal)[0]["value"]
    assert raw_value == Decimal("0.12345678901234567890123456789")
    assert isinstance(raw_value, Decimal)


def test_status_diagnostics_redact_unexpected_sensitive_fields() -> None:
    individual = parse_individual_status(
        "meter-1",
        {
            "status": [{"code": "forward_energy_total", "value": 123456}],
            "debug": {
                "access_token": "must-not-be-visible",
                "local_key": "device-secret",
            },
        },
    )
    batch = parse_batch_status(
        [
            {
                "id": "meter-1",
                "status": [{"code": "forward_energy_total", "value": 123456}],
                "client_secret": "must-not-be-visible",
            }
        ]
    )

    assert individual.value_for("forward_energy_total") == 123456
    assert batch["meter-1"].value_for("forward_energy_total") == 123456
    assert "must-not-be-visible" not in individual.raw_json
    assert "device-secret" not in individual.raw_json
    assert "must-not-be-visible" not in batch["meter-1"].raw_json
    assert individual.raw_json.count("[REDACTED]") == 2
    assert "[REDACTED]" in batch["meter-1"].raw_json
