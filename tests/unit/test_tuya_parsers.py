import json
from decimal import Decimal
from pathlib import Path

import pytest

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


def test_specification_fixture_finds_scale_and_unit() -> None:
    specification = parse_energy_specification(fixture_result("specification.json"))
    assert (specification.code, specification.unit, specification.scale) == (
        "forward_energy_total", "kWh", 2
    )


def test_ambiguous_energy_specification_is_rejected() -> None:
    row = {
        "code": "forward_energy_total",
        "values": {"unit": "kWh", "scale": 2},
    }
    with pytest.raises(TuyaPayloadError, match="exactly one"):
        parse_energy_specification({"status": [row, row]})


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
