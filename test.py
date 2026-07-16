"""Interactive terminal tester for the temporary Tuya client."""

from __future__ import annotations

import json
from typing import Any

from tatatuya.infrastructure import tuya_legacy
from tatatuya.infrastructure.tuya_legacy import TuyaAPIError, TuyaClient, TuyaConfigError


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def prompt(message: str) -> str:
    try:
        return input(message).strip()
    except EOFError:
        return "q"


def print_error_details(exc: Exception) -> None:
    print(f"Request failed: {exc}")
    if isinstance(exc, TuyaAPIError):
        if exc.request_info:
            print("\nRequest info:")
            print_json(exc.request_info)
        if exc.response_payload is not None:
            print("\nTuya response:")
            print_json(exc.response_payload)


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


def show_devices(devices: list[dict[str, Any]]) -> None:
    if not devices:
        print("No devices found in the last response.")
        return

    print("\nDevices:")
    for index, device in enumerate(devices, start=1):
        device_id = device.get("id") or device.get("device_id") or "<missing id>"
        name = device.get("name") or device.get("custom_name") or device.get("product_name") or "-"
        category = device.get("category") or "-"
        online = device.get("online")
        online_text = "online" if online is True else "offline" if online is False else "unknown"
        print(f"{index:>2}. {name} | {device_id} | {category} | {online_text}")


def choose_device_id(devices: list[dict[str, Any]]) -> str | None:
    show_devices(devices)
    value = prompt("\nDevice number or device id (blank to cancel): ")
    if not value:
        return None
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(devices):
            device = devices[index - 1]
            return device.get("id") or device.get("device_id")
        print("That number is not in the device list.")
        return None
    return value


def device_ids_from_last_list(devices: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for device in devices:
        device_id = device.get("id") or device.get("device_id")
        if device_id:
            ids.append(str(device_id))
    return ids


def prompt_params() -> dict[str, str]:
    print("\nEnter query parameters for Tuya.")
    print("Use key=value, one per line. Blank line when done.")
    print("Common guesses to try: device_id=..., device_ids=id1,id2, start_time=..., end_time=..., stat_type=month")

    params: dict[str, str] = {}
    while True:
        line = prompt("param> ")
        if not line:
            return params
        if "=" not in line:
            print("Use key=value.")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            params[key] = value


def print_menu() -> None:
    print(
        """
Actions:
  1. Refresh access token
  2. List devices
  3. Get device specification
  4. Get single device status
  5. Get status for all listed devices
  6. Get electricity statistics sum
  7. Get electricity statistics trend
  8. Show region/base URL
  q. Quit
"""
    )


def main() -> None:
    client = TuyaClient()
    devices: list[dict[str, Any]] = []

    print("Tuya terminal tester")
    print(f"Region: {tuya_legacy.REGION}")
    print(f"Base URL: {client.base_url}")

    try:
        print("\nStartup step: get access token")
        token = client.get_access_token()
        print(f"Access token acquired: {token[:8]}...")
    except (TuyaAPIError, TuyaConfigError) as exc:
        print("\nStartup token request failed.")
        print_error_details(exc)
        print("You can still use the menu after fixing credentials in .env and rerunning.")
    else:
        try:
            print("\nStartup step: list devices")
            payload = client.list_devices()
            devices = extract_devices(payload)
            print_json(payload)
            show_devices(devices)
        except (TuyaAPIError, TuyaConfigError) as exc:
            print("\nStartup device list request failed.")
            print_error_details(exc)
            print("You can still use the menu to test other endpoints.")

    while True:
        print_menu()
        choice = prompt("Choose: ").lower()

        try:
            if choice == "1":
                token = client.get_access_token()
                print(f"Access token acquired: {token[:8]}...")

            elif choice == "2":
                payload = client.list_devices()
                devices = extract_devices(payload)
                print_json(payload)
                show_devices(devices)

            elif choice == "3":
                device_id = choose_device_id(devices)
                if device_id:
                    print_json(client.get_device_specification(device_id))

            elif choice == "4":
                device_id = choose_device_id(devices)
                if device_id:
                    print_json(client.get_device_status(device_id))

            elif choice == "5":
                ids = device_ids_from_last_list(devices)
                if not ids:
                    raw = prompt("Comma-separated device IDs: ")
                    ids = [item.strip() for item in raw.split(",") if item.strip()]
                if ids:
                    print_json(client.get_devices_status(ids))
                else:
                    print("No device IDs available.")

            elif choice == "6":
                params = prompt_params()
                print_json(client.get_electricity_statistics_sum(**params))

            elif choice == "7":
                params = prompt_params()
                print_json(client.get_electricity_statistics_trend(**params))

            elif choice == "8":
                print(f"Region: {client.region}")
                print(f"Base URL: {client.base_url}")
                print("Known regions:")
                for region, url in sorted(tuya_legacy.REGION_BASE_URLS.items()):
                    print(f"  {region}: {url}")

            elif choice in {"q", "quit", "exit"}:
                break

            else:
                print("Unknown choice.")

        except (TuyaAPIError, TuyaConfigError) as exc:
            print_error_details(exc)


if __name__ == "__main__":
    main()
