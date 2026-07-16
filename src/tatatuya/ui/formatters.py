"""Formatting helpers for Tuya device data."""

from __future__ import annotations

from typing import Any


def extract_devices(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ("list", "devices", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def value_from(data: dict[str, Any], *keys: str, default: str = "-") -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return default


def device_id(device: dict[str, Any]) -> str:
    return value_from(device, "id", "device_id", default="")


def device_name(device: dict[str, Any]) -> str:
    return value_from(device, "name", "custom_name", "product_name", default="Unnamed device")


def online_label(value: Any) -> str:
    if value is True:
        return "Online"
    if value is False:
        return "Offline"
    return "Unknown"


def flatten_status(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    result = payload.get("result", payload)
    if isinstance(result, list):
        rows: list[tuple[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                code = item.get("code") or item.get("name") or "status"
                rows.append((str(code), item.get("value", item)))
        return rows
    if isinstance(result, dict):
        return [(str(key), value) for key, value in result.items()]
    return [("value", result)]


def important_device_fields(device: dict[str, Any]) -> list[tuple[str, Any]]:
    fields = [
        ("Name", device_name(device)),
        ("Device ID", device_id(device) or "-"),
        ("Product", value_from(device, "product_name", "product_id")),
        ("Category", value_from(device, "category")),
        ("Status", online_label(device.get("online"))),
        ("Time Zone", value_from(device, "time_zone")),
        ("Owner ID", value_from(device, "owner_id", "uid")),
    ]
    return [(label, value) for label, value in fields if value != "-"]
