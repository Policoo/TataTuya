"""Parsers for Tuya payloads at the infrastructure/domain boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from tatatuya.domain.models import (
    Device,
    DeviceStatus,
    EnergySpecification,
    StatusValue,
)


class TuyaPayloadError(ValueError):
    """Raised when a successful Tuya envelope has an unusable shape."""


@dataclass(frozen=True, slots=True)
class DevicePage:
    devices: tuple[Device, ...]
    has_more: bool
    last_row_key: str | None


def parse_devices(result: Any) -> list[Device]:
    return list(parse_device_page(result).devices)


def parse_device_page(result: Any) -> DevicePage:
    rows = _find_list(result, ("list", "devices", "data"))
    devices: list[Device] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        device_id = row.get("id") or row.get("device_id")
        if not device_id:
            continue
        name = row.get("name") or row.get("custom_name") or row.get("product_name")
        devices.append(
            Device(
                device_id=str(device_id),
                name=str(name or device_id),
                product_id=_optional_string(row.get("product_id")),
                product_name=_optional_string(row.get("product_name")),
                category=_optional_string(row.get("category")),
                online=row.get("online") if isinstance(row.get("online"), bool) else None,
                raw_device_json=_dump(redact_sensitive_fields(row)),
            )
        )
    has_more = result.get("has_more") is True if isinstance(result, Mapping) else False
    raw_cursor = result.get("last_row_key") if isinstance(result, Mapping) else None
    cursor = str(raw_cursor) if raw_cursor not in (None, "") else None
    return DevicePage(tuple(devices), has_more, cursor)


def parse_energy_specification(result: Any) -> EnergySpecification:
    if not isinstance(result, Mapping):
        raise TuyaPayloadError("Specification result is not an object")
    candidates: list[EnergySpecification] = []
    for row in _find_list(result.get("status"), ("list",)):
        if not isinstance(row, Mapping) or row.get("code") != "forward_energy_total":
            continue
        values = row.get("values")
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError as exc:
                raise TuyaPayloadError("Energy specification values are invalid JSON") from exc
        if not isinstance(values, Mapping):
            raise TuyaPayloadError("Energy specification values are missing")
        scale = values.get("scale")
        unit = values.get("unit")
        if isinstance(scale, bool) or not isinstance(scale, int) or not isinstance(unit, str):
            raise TuyaPayloadError("Energy specification scale or unit is invalid")
        candidates.append(EnergySpecification("forward_energy_total", unit, scale))
    if len(candidates) != 1:
        raise TuyaPayloadError(
            "Expected exactly one forward_energy_total status specification, "
            f"found {len(candidates)}"
        )
    return candidates[0]


def parse_individual_status(device_id: str, result: Any) -> DeviceStatus:
    rows = _find_list(result, ("status", "list"))
    return DeviceStatus(
        str(device_id),
        _parse_status_values(rows),
        _dump(redact_sensitive_fields(result)),
    )


def parse_batch_status(result: Any) -> dict[str, DeviceStatus]:
    rows = _find_list(result, ("list", "devices", "data"))
    parsed: dict[str, DeviceStatus] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        device_id = row.get("id") or row.get("device_id")
        if not device_id:
            continue
        status_rows = _find_list(row.get("status"), ("list",))
        parsed[str(device_id)] = DeviceStatus(
            str(device_id),
            _parse_status_values(status_rows),
            _dump(redact_sensitive_fields(row)),
        )
    return parsed


def _parse_status_values(rows: Sequence[Any]) -> tuple[StatusValue, ...]:
    values: list[StatusValue] = []
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("code"), str):
            values.append(StatusValue(row["code"], row.get("value")))
    return tuple(values)


def _find_list(value: Any, nested_keys: Sequence[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        for key in nested_keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _dump(value: Any) -> str:
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(
            f"{json.dumps(str(key), ensure_ascii=False)}:{_dump(item)}"
            for key, item in items
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_dump(item) for item in value) + "]"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if value.is_finite():
            return format(value, "f")
        raise ValueError("Diagnostic JSON cannot contain a non-finite Decimal")
    if isinstance(value, float):
        return repr(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


_SENSITIVE_DEVICE_FIELDS = {
    "access_token",
    "client_secret",
    "local_key",
    "password",
    "secret_key",
}


def redact_sensitive_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if str(key).lower() in _SENSITIVE_DEVICE_FIELDS
                else redact_sensitive_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    return value
