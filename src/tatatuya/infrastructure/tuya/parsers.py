"""Parsers for Tuya payloads at the infrastructure/domain boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from tatatuya.domain.energy import canonical_energy_unit
from tatatuya.domain.errors import (
    EnergySpecificationError,
    UnsupportedEnergyDeviceError,
)
from tatatuya.domain.models import (
    Device,
    DeviceStatus,
    EnergySpecification,
    StatusValue,
)


class TuyaPayloadError(EnergySpecificationError, ValueError):
    """Raised when a successful Tuya envelope has an unusable shape."""


SUPPORTED_FORWARD_ENERGY_CODES = frozenset(
    {"forward_energy_total", "total_forward_energy"}
)


@dataclass(frozen=True, slots=True)
class DevicePage:
    devices: tuple[Device, ...]
    has_more: bool
    last_row_key: str | None


def parse_devices(result: Any) -> list[Device]:
    return list(parse_device_page(result).devices)


def parse_device_page(result: Any) -> DevicePage:
    rows = _require_list(result, ("list", "devices", "data"), "Device collection")
    devices: list[Device] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TuyaPayloadError(
                "Device collection contains a non-object row",
                _dump(redact_sensitive_fields(result)),
            )
        device_id = row.get("id") or row.get("device_id")
        if not device_id:
            raise TuyaPayloadError(
                "Device collection contains a row without an ID",
                _dump(redact_sensitive_fields(result)),
            )
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
                present_in_tuya=True,
            )
        )
    raw_has_more = (
        result.get("has_more", False) if isinstance(result, Mapping) else False
    )
    if not isinstance(raw_has_more, bool):
        raise TuyaPayloadError(
            "Device pagination has_more is not boolean",
            _dump(redact_sensitive_fields(result)),
        )
    has_more = raw_has_more
    raw_cursor = result.get("last_row_key") if isinstance(result, Mapping) else None
    if raw_cursor not in (None, "") and not isinstance(raw_cursor, (str, int)):
        raise TuyaPayloadError(
            "Device pagination cursor is invalid",
            _dump(redact_sensitive_fields(result)),
        )
    cursor = str(raw_cursor) if raw_cursor not in (None, "") else None
    if has_more and cursor is None:
        raise TuyaPayloadError(
            "Device pagination cursor is missing",
            _dump(redact_sensitive_fields(result)),
        )
    return DevicePage(tuple(devices), has_more, cursor)


def parse_energy_specification(result: Any) -> EnergySpecification:
    raw_json = _dump(redact_sensitive_fields(result))
    if not isinstance(result, Mapping):
        raise TuyaPayloadError("Specification result is not an object", raw_json)
    rows = _require_list(
        result.get("status"), ("list",), "Specification status collection", raw_json
    )
    if any(
        not isinstance(row, Mapping) or not isinstance(row.get("code"), str)
        for row in rows
    ):
        raise TuyaPayloadError(
            "Specification status collection contains an invalid row", raw_json
        )
    candidates = [row for row in rows if row.get("code") in SUPPORTED_FORWARD_ENERGY_CODES]
    if not candidates:
        raise UnsupportedEnergyDeviceError(
            "Device has no supported cumulative forward-energy specification",
            raw_json,
        )
    if len(candidates) != 1:
        raise TuyaPayloadError(
            "Expected exactly one supported cumulative forward-energy specification, "
            f"found {len(candidates)}",
            raw_json,
        )
    values = candidates[0].get("values")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except json.JSONDecodeError as exc:
            raise TuyaPayloadError(
                "Energy specification values are invalid JSON", raw_json
            ) from exc
    if not isinstance(values, Mapping):
        raise TuyaPayloadError("Energy specification values are missing", raw_json)
    scale = values.get("scale")
    unit = values.get("unit")
    if (
        isinstance(scale, bool)
        or not isinstance(scale, int)
        or scale < 0
        or not isinstance(unit, str)
        or not unit.strip()
    ):
        raise TuyaPayloadError(
            "Energy specification scale or unit is invalid", raw_json
        )
    if canonical_energy_unit(unit) is None:
        raise UnsupportedEnergyDeviceError(
            "Energy specification unit is unsupported", raw_json
        )
    return EnergySpecification(str(candidates[0]["code"]), unit, scale, raw_json)


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


def _require_list(
    value: Any,
    nested_keys: Sequence[str],
    description: str,
    raw_json: str | None = None,
) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        for key in nested_keys:
            if key in value:
                nested = value[key]
                if isinstance(nested, list):
                    return nested
                break
    diagnostic = raw_json or _dump(redact_sensitive_fields(value))
    raise TuyaPayloadError(f"{description} is missing or invalid", diagnostic)


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
