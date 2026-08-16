#!/usr/bin/env python3
"""Interactive, read-only inspector for the Tuya endpoints used by TataTuya."""

from __future__ import annotations

import argparse
import curses
import json
import locale
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from tatatuya.infrastructure.database import Database  # noqa: E402
from tatatuya.infrastructure.repositories.settings import (  # noqa: E402
    SettingsRepository,
)
from tatatuya.infrastructure.tuya.client import (  # noqa: E402
    BodyLimits,
    BoundedPayload,
    TuyaAPIError,
    TuyaClient,
)
from tatatuya.infrastructure.tuya.parsers import (  # noqa: E402
    SUPPORTED_FORWARD_ENERGY_CODES,
    redact_sensitive_fields,
)
from tatatuya.paths import database_path  # noqa: E402


MAX_INSPECTOR_BODY = 4 * 1024 * 1024
MAX_PAGES = 50
MIN_REPORT_REQUEST_INTERVAL_SECONDS = 0.250
DEFAULT_ENERGY_CODE = "forward_energy_total"

_DEVICE_ENDPOINT = re.compile(r"/v1\.0/iot-03/devices/[^/]+/(?:specification|status)\Z")
_REPORT_ENDPOINT = re.compile(r"/v2\.[01]/cloud/thing/[^/]+/report-logs\Z")
_EXACT_ENDPOINTS = {
    "/v1.0/iot-01/associated-users/devices",
    "/v1.0/iot-03/devices/status",
}


@dataclass(frozen=True, slots=True)
class DeviceChoice:
    device_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Capture:
    label: str
    request_url: str
    payload: Mapping[str, Any] | None = None
    raw_bytes: int | None = None
    decoded_characters: int | None = None
    error: str | None = None
    error_payload: object | None = None
    note: str | None = None


def _validate_read_only_path(path: str) -> None:
    if (
        path not in _EXACT_ENDPOINTS
        and _DEVICE_ENDPOINT.fullmatch(path) is None
        and _REPORT_ENDPOINT.fullmatch(path) is None
    ):
        raise ValueError(
            f"Endpoint is not on the read-only inspector allowlist: {path}"
        )


def _sanitize_payload(value: object, secrets: Sequence[str]) -> object:
    value = redact_sensitive_fields(value)
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_payload(item, secrets) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_payload(item, secrets) for item in value]
    return value


def _render_json(value: object, level: int = 0) -> str:
    """Render parsed JSON while keeping Decimal values as JSON number tokens."""

    indent = "  " * level
    child_indent = "  " * (level + 1)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        rows = []
        for key, item in value.items():
            rendered = _render_json(item, level + 1)
            rows.append(
                f"{child_indent}{json.dumps(str(key), ensure_ascii=False)}: {rendered}"
            )
        return "{\n" + ",\n".join(rows) + f"\n{indent}" + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        rows = [f"{child_indent}{_render_json(item, level + 1)}" for item in value]
        return "[\n" + ",\n".join(rows) + f"\n{indent}]"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def _result_from_capture(capture: Capture) -> Mapping[str, Any]:
    payload = capture.payload
    if payload is None or payload.get("success") is False:
        raise RuntimeError(f"{capture.label} did not return a successful envelope")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError(f"{capture.label} result is not an object")
    return result


def _device_rows(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("list", "devices", "data"):
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
    return []


def _capture_text(captures: Sequence[Capture]) -> str:
    sections: list[str] = []
    for capture in captures:
        rows = [
            f"=== {capture.label} ===",
            "METHOD: GET",
            f"URL: {capture.request_url}",
        ]
        if capture.note:
            rows.extend(("", "INSPECTOR NOTE:", capture.note))
        if capture.error:
            rows.extend(("", "ERROR:", capture.error))
            if capture.error_payload is not None:
                rows.extend(
                    (
                        "",
                        "SANITIZED ERROR PAYLOAD:",
                        _render_json(capture.error_payload),
                    )
                )
        elif capture.payload is not None:
            rows.extend(
                (
                    f"BODY: {capture.raw_bytes} raw bytes, "
                    f"{capture.decoded_characters} decoded characters",
                    "",
                    "SANITIZED RESPONSE ENVELOPE:",
                    _render_json(capture.payload),
                )
            )
        sections.append("\n".join(rows))
    return "\n\n".join(sections)


class InspectorSession:
    """Own authenticated, allowlisted GET access without persistence writes."""

    def __init__(self, client: TuyaClient, *, days: int = 7, page_size: int = 99):
        self.client = client
        self.days = days
        self.page_size = page_size
        self.devices: list[DeviceChoice] = []
        self.selected_device_index = 0
        self.energy_code = DEFAULT_ENERGY_CODE

    @property
    def selected_device(self) -> DeviceChoice | None:
        if not self.devices:
            return None
        self.selected_device_index %= len(self.devices)
        return self.devices[self.selected_device_index]

    def authenticate(self) -> str:
        self.client.authenticate()
        return (
            "Authentication succeeded. The access token is intentionally not "
            "displayed or stored by this inspector."
        )

    def inspect_get(
        self,
        label: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        note: str | None = None,
    ) -> Capture:
        _validate_read_only_path(path)
        prepared = self.client._prepare_request("GET", path, params)  # noqa: SLF001
        send_bounded = getattr(self.client.transport, "send_bounded", None)
        if not callable(send_bounded):
            raise RuntimeError("The configured Tuya transport cannot inspect envelopes")
        secrets = tuple(
            value
            for value in (
                self.client.settings.client_secret,
                self.client.access_token,
            )
            if value
        )
        try:
            response = send_bounded(
                prepared,
                BodyLimits(
                    MAX_INSPECTOR_BODY,
                    MAX_INSPECTOR_BODY,
                    decimal_integers=True,
                ),
            )
        except TuyaAPIError as error:
            safe_error = self.client._redacted_error(error)  # noqa: SLF001
            return Capture(
                label,
                str(prepared.diagnostic["url"]),
                error=str(safe_error),
                error_payload=_sanitize_payload(safe_error.response_payload, secrets),
                note=note,
            )
        if not isinstance(response, BoundedPayload):
            raise RuntimeError("The Tuya transport returned an unexpected value")
        sanitized = _sanitize_payload(response.payload, secrets)
        if not isinstance(sanitized, Mapping):
            raise RuntimeError("The sanitized Tuya envelope is not an object")
        return Capture(
            label,
            str(prepared.diagnostic["url"]),
            sanitized,
            response.raw_bytes,
            response.decoded_characters,
            note=note,
        )

    def reload_devices(self) -> str:
        captures: list[Capture] = []
        choices: dict[str, DeviceChoice] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page_number in range(1, MAX_PAGES + 1):
            params = {"last_row_key": cursor} if cursor else None
            capture = self.inspect_get(
                f"Associated devices page {page_number}",
                "/v1.0/iot-01/associated-users/devices",
                params,
            )
            captures.append(capture)
            result = _result_from_capture(capture)
            for row in _device_rows(result):
                device_id = row.get("id") or row.get("device_id")
                if device_id is None:
                    continue
                rendered_id = str(device_id)
                name = row.get("name") or row.get("custom_name") or rendered_id
                choices.setdefault(rendered_id, DeviceChoice(rendered_id, str(name)))
            has_more = result.get("has_more", False)
            if has_more is not True:
                break
            raw_cursor = result.get("last_row_key")
            if raw_cursor in (None, ""):
                raise RuntimeError("Device response says has_more but has no cursor")
            cursor = str(raw_cursor)
            if cursor in seen_cursors:
                raise RuntimeError("Device response repeated a pagination cursor")
            seen_cursors.add(cursor)
        else:
            raise RuntimeError("Device listing exceeded the 50-page inspector limit")

        previous_id = self.selected_device.device_id if self.selected_device else None
        self.devices = list(choices.values())
        if previous_id:
            self.selected_device_index = next(
                (
                    index
                    for index, device in enumerate(self.devices)
                    if device.device_id == previous_id
                ),
                0,
            )
        else:
            self.selected_device_index = 0
        return _capture_text(captures)

    def inspect_specification(self) -> str:
        device = self._require_device()
        capture = self.inspect_get(
            "Device specification",
            f"/v1.0/iot-03/devices/{quote(device.device_id, safe='')}/specification",
        )
        if capture.payload is not None and capture.payload.get("success") is not False:
            result = capture.payload.get("result")
            if isinstance(result, Mapping):
                rows = result.get("status")
                if isinstance(rows, list):
                    candidates = [
                        str(row["code"])
                        for row in rows
                        if isinstance(row, Mapping)
                        and row.get("code") in SUPPORTED_FORWARD_ENERGY_CODES
                    ]
                    if len(candidates) == 1:
                        self.energy_code = candidates[0]
        return _capture_text((capture,))

    def inspect_status(self) -> str:
        device = self._require_device()
        capture = self.inspect_get(
            "Individual device status",
            f"/v1.0/iot-03/devices/{quote(device.device_id, safe='')}/status",
        )
        return _capture_text((capture,))

    def inspect_batch_status(self) -> str:
        if not self.devices:
            raise RuntimeError("Load the associated-device list first")
        captures = []
        ids = [device.device_id for device in self.devices]
        for start in range(0, len(ids), 20):
            chunk = ids[start : start + 20]
            captures.append(
                self.inspect_get(
                    f"Batch status {start // 20 + 1}",
                    "/v1.0/iot-03/devices/status",
                    {"device_ids": ",".join(chunk)},
                    note=f"Requested device IDs: {', '.join(chunk)}",
                )
            )
        return _capture_text(captures)

    def inspect_report_page(self, version: str) -> str:
        start_time_ms, end_time_ms = self._report_window()
        capture = self._report_capture(version, None, 1, start_time_ms, end_time_ms)
        return _capture_text((capture,))

    def compare_report_versions(self) -> str:
        start_time_ms, end_time_ms = self._report_window()
        captures = (
            self._report_capture("v2.1", None, 1, start_time_ms, end_time_ms),
            self._report_capture("v2.0", None, 1, start_time_ms, end_time_ms),
        )
        return _capture_text(captures)

    def inspect_all_report_pages(self) -> str:
        captures: list[Capture] = []
        start_time_ms, end_time_ms = self._report_window()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        last_request_at: float | None = None
        for page_number in range(1, MAX_PAGES + 1):
            if last_request_at is not None:
                delay = MIN_REPORT_REQUEST_INTERVAL_SECONDS - (
                    time.monotonic() - last_request_at
                )
                if delay > 0:
                    time.sleep(delay)
            last_request_at = time.monotonic()
            capture = self._report_capture(
                "v2.1",
                cursor,
                page_number,
                start_time_ms,
                end_time_ms,
            )
            captures.append(capture)
            result = _result_from_capture(capture)
            has_more = result.get("has_more", result.get("hasMore"))
            if has_more is not True:
                break
            raw_cursor = result.get("last_row_key", result.get("lastRowKey"))
            if not isinstance(raw_cursor, str) or not raw_cursor:
                raise RuntimeError("Report page says has_more but has no cursor")
            if raw_cursor in seen_cursors:
                raise RuntimeError("Report endpoint repeated a pagination cursor")
            seen_cursors.add(raw_cursor)
            cursor = raw_cursor
        else:
            raise RuntimeError("Report history exceeded the 50-page inspector limit")
        return _capture_text(captures)

    def select_previous_device(self) -> None:
        if self.devices:
            self.selected_device_index = (self.selected_device_index - 1) % len(
                self.devices
            )

    def select_next_device(self) -> None:
        if self.devices:
            self.selected_device_index = (self.selected_device_index + 1) % len(
                self.devices
            )

    def _report_capture(
        self,
        version: str,
        cursor: str | None,
        page_number: int,
        start_time_ms: int,
        end_time_ms: int,
    ) -> Capture:
        if version not in {"v2.0", "v2.1"}:
            raise ValueError("Unsupported report-log version")
        device = self._require_device()
        params: dict[str, Any] = {
            "codes": self.energy_code,
            "start_time": start_time_ms,
            "end_time": end_time_ms,
            "size": self.page_size,
        }
        if cursor:
            params["last_row_key"] = cursor
        path = f"/{version}/cloud/thing/{quote(device.device_id, safe='')}/report-logs"
        capture = self.inspect_get(
            f"Report logs {version}, page {page_number}",
            path,
            params,
        )
        returned = None
        if capture.payload is not None:
            result = capture.payload.get("result")
            if isinstance(result, Mapping):
                returned = result.get("device_id", result.get("deviceId", "<missing>"))
        note = (
            f"Requested device_id: {device.device_id}\n"
            f"Returned result.device_id: {returned!r}\n"
            f"Requested DP code: {self.energy_code}\n"
            f"Window: {start_time_ms} through {end_time_ms} (last {self.days} days)"
        )
        return Capture(
            capture.label,
            capture.request_url,
            capture.payload,
            capture.raw_bytes,
            capture.decoded_characters,
            capture.error,
            capture.error_payload,
            note,
        )

    def _report_window(self) -> tuple[int, int]:
        end_time_ms = time.time_ns() // 1_000_000
        start_time_ms = end_time_ms - self.days * 24 * 60 * 60 * 1000
        return start_time_ms, end_time_ms

    def _require_device(self) -> DeviceChoice:
        device = self.selected_device
        if device is None:
            raise RuntimeError("Load the associated-device list first")
        return device


class InspectorTui:
    def __init__(self, screen: curses.window, session: InspectorSession):
        self.screen = screen
        self.session = session
        self.action_index = 0
        self.output_scroll = 0
        self.output = "Starting…"
        self.status = ""
        self.print_on_exit = False
        self.actions: list[tuple[str, Callable[[], str]]] = [
            ("Authenticate", session.authenticate),
            ("List associated devices", session.reload_devices),
            ("Device specification", session.inspect_specification),
            ("Individual device status", session.inspect_status),
            ("Batch status (all devices)", session.inspect_batch_status),
            ("Report logs v2.1 (page 1)", lambda: session.inspect_report_page("v2.1")),
            ("Report logs v2.1 (all pages)", session.inspect_all_report_pages),
            ("Report logs v2.0 (page 1)", lambda: session.inspect_report_page("v2.0")),
            ("Compare v2.1 and v2.0", session.compare_report_versions),
        ]

    def run(self) -> bool:
        curses.curs_set(0)
        self.screen.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
        self._bootstrap()
        while True:
            self._draw()
            key = self.screen.getch()
            if key in (ord("q"), 27):
                return False
            if key == ord("x"):
                self.print_on_exit = True
                return True
            if key in (curses.KEY_UP, ord("u")):
                self.action_index = (self.action_index - 1) % len(self.actions)
            elif key in (curses.KEY_DOWN, ord("n")):
                self.action_index = (self.action_index + 1) % len(self.actions)
            elif key in (10, 13, curses.KEY_ENTER):
                self._run_selected_action()
            elif key in (ord("j"),):
                self.output_scroll += 1
            elif key in (ord("k"),):
                self.output_scroll = max(0, self.output_scroll - 1)
            elif key == curses.KEY_NPAGE:
                self.output_scroll += max(1, self.screen.getmaxyx()[0] - 7)
            elif key == curses.KEY_PPAGE:
                self.output_scroll = max(
                    0, self.output_scroll - max(1, self.screen.getmaxyx()[0] - 7)
                )
            elif key == ord("g"):
                self.output_scroll = 0
            elif key == ord("G"):
                self.output_scroll = max(0, len(self.output.splitlines()) - 1)
            elif key == ord("["):
                self.session.select_previous_device()
                self.status = "Selected previous device"
            elif key == ord("]"):
                self.session.select_next_device()
                self.status = "Selected next device"
            elif key == ord("d"):
                self._select_device()
            elif key == ord("c"):
                value = self._prompt("DP code", self.session.energy_code)
                if value:
                    self.session.energy_code = value
                    self.status = f"DP code set to {value}"
            elif key == curses.KEY_RESIZE:
                continue

    def _bootstrap(self) -> None:
        self.status = "Authenticating and loading associated devices…"
        self._draw()
        try:
            auth = self.session.authenticate()
            devices = self.session.reload_devices()
            self.output = auth + "\n\n" + devices
            self.status = f"Loaded {len(self.session.devices)} devices"
        except Exception as error:  # noqa: BLE001 - diagnostic boundary
            self.output = f"Bootstrap failed:\n{type(error).__name__}: {error}"
            self.status = "Use Authenticate or List associated devices to retry"
        self.output_scroll = 0

    def _run_selected_action(self) -> None:
        label, action = self.actions[self.action_index]
        self.status = f"Running {label}…"
        self._draw()
        try:
            self.output = action()
            self.status = f"Completed {label}"
        except Exception as error:  # noqa: BLE001 - diagnostic boundary
            self.output = f"{label} failed:\n{type(error).__name__}: {error}"
            self.status = f"Failed {label}"
        self.output_scroll = 0

    def _draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 16 or width < 72:
            self._safe_add(0, 0, "Terminal too small; resize to at least 72×16.")
            self.screen.refresh()
            return
        menu_width = min(35, max(27, width // 3))
        device = self.session.selected_device
        device_text = (
            f"{device.name} [{device.device_id}]" if device else "<no device loaded>"
        )
        header = "TataTuya read-only Tuya inspector"
        self._safe_add(0, 0, header, curses.A_BOLD | self._color(1))
        self._safe_add(1, 0, f"Device: {device_text}")
        self._safe_add(
            2,
            0,
            f"DP: {self.session.energy_code} | Window: {self.session.days} days | "
            f"Page size: {self.session.page_size}",
        )
        self._safe_add(3, 0, "─" * (width - 1))

        for index, (label, _) in enumerate(self.actions):
            attribute = self._color(2) if index == self.action_index else 0
            self._safe_add(4 + index, 0, f" {label} ", attribute, menu_width - 1)

        divider_x = menu_width
        for y in range(4, height - 2):
            self._safe_add(y, divider_x, "│")

        output_lines = self.output.splitlines() or [""]
        viewport_height = height - 6
        max_scroll = max(0, len(output_lines) - viewport_height)
        self.output_scroll = min(max_scroll, max(0, self.output_scroll))
        for row, line in enumerate(
            output_lines[self.output_scroll : self.output_scroll + viewport_height]
        ):
            self._safe_add(4 + row, divider_x + 2, line, 0, width - divider_x - 3)

        self._safe_add(height - 2, 0, "─" * (width - 1))
        help_text = (
            "↑↓ action  Enter run  d device  [ ] cycle  c DP  j/k/Pg scroll  "
            "x print+exit  q exit"
        )
        self._safe_add(height - 1, 0, help_text, self._color(1), width - 1)
        status_x = min(width - 1, len(help_text) + 2)
        if status_x < width - 1:
            self._safe_add(
                height - 1,
                status_x,
                self.status,
                self._color(3),
                width - status_x - 1,
            )
        self.screen.refresh()

    def _select_device(self) -> None:
        if not self.session.devices:
            self.status = "Load the associated-device list first"
            return
        index = self.session.selected_device_index
        while True:
            self.screen.erase()
            height, width = self.screen.getmaxyx()
            self._safe_add(
                0, 0, "Select device (Enter confirms, Esc cancels)", curses.A_BOLD
            )
            first = max(
                0,
                min(index - (height - 3) // 2, len(self.session.devices) - height + 2),
            )
            visible = self.session.devices[first : first + max(1, height - 2)]
            for row, device in enumerate(visible, start=1):
                absolute = first + row - 1
                attribute = self._color(2) if absolute == index else 0
                self._safe_add(
                    row,
                    0,
                    f" {device.name} [{device.device_id}] ",
                    attribute,
                    width - 1,
                )
            self.screen.refresh()
            key = self.screen.getch()
            if key in (27, ord("q")):
                return
            if key in (10, 13, curses.KEY_ENTER):
                self.session.selected_device_index = index
                self.status = f"Selected {self.session.devices[index].name}"
                return
            if key == curses.KEY_UP:
                index = (index - 1) % len(self.session.devices)
            elif key == curses.KEY_DOWN:
                index = (index + 1) % len(self.session.devices)

    def _prompt(self, label: str, default: str) -> str | None:
        height, width = self.screen.getmaxyx()
        prompt = f"{label} [{default}]: "
        self._safe_add(height - 1, 0, " " * (width - 1))
        self._safe_add(height - 1, 0, prompt, curses.A_BOLD, width - 1)
        curses.echo()
        curses.curs_set(1)
        try:
            raw = self.screen.getstr(
                height - 1,
                min(len(prompt), width - 2),
                max(1, width - len(prompt) - 2),
            )
        finally:
            curses.noecho()
            curses.curs_set(0)
        value = raw.decode("utf-8", errors="replace").strip()
        return value or default

    def _safe_add(
        self,
        y: int,
        x: int,
        value: str,
        attribute: int = 0,
        width: int | None = None,
    ) -> None:
        height, screen_width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= screen_width:
            return
        available = max(0, min(width or screen_width - x - 1, screen_width - x - 1))
        if available <= 0:
            return
        try:
            self.screen.addnstr(y, x, value, available, attribute)
        except curses.error:
            pass

    @staticmethod
    def _color(pair: int) -> int:
        return curses.color_pair(pair) if curses.has_colors() else 0


def _load_client(path: Path) -> TuyaClient:
    if not path.is_file():
        raise RuntimeError(f"TataTuya database does not exist: {path}")
    database = Database(path)
    with database.connect() as connection:
        settings = SettingsRepository(
            connection, database.client_secret_store()
        ).load_tuya()
    if settings is None or not settings.is_complete:
        raise RuntimeError(
            "Saved Tuya settings are missing or incomplete. Configure them in TataTuya first."
        )
    return TuyaClient(settings)


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Open a read-only TUI for the Tuya endpoints used by TataTuya. "
            "Responses are shown locally with credentials and local keys redacted."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=database_path(),
        help="Path to tatatuya.sqlite3 (defaults to the normal app database)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Report-log lookback in days (1-31, default: 7)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=99,
        help="Report-log page size (1-99, default: 99)",
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.days <= 31:
        parser.error("--days must be between 1 and 31")
    if not 1 <= arguments.page_size <= 99:
        parser.error("--page-size must be between 1 and 99")
    return arguments


def _configure_locale() -> str | None:
    """Use the environment locale when valid, with portable safe fallbacks."""

    for candidate in ("", "C.UTF-8", "UTF-8", "C"):
        try:
            return locale.setlocale(locale.LC_ALL, candidate)
        except locale.Error:
            continue
    return None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        client = _load_client(arguments.database.expanduser())
    except Exception as error:  # noqa: BLE001 - command-line boundary
        print(f"inspect_tuya: {error}", file=sys.stderr)
        return 2
    _configure_locale()
    session = InspectorSession(
        client,
        days=arguments.days,
        page_size=arguments.page_size,
    )
    tui_holder: list[InspectorTui] = []

    def run(screen: curses.window) -> bool:
        tui = InspectorTui(screen, session)
        tui_holder.append(tui)
        return tui.run()

    try:
        print_output = curses.wrapper(run)
    except curses.error as error:
        print(f"inspect_tuya: unable to start terminal UI: {error}", file=sys.stderr)
        return 2
    if print_output and tui_holder:
        print(tui_holder[0].output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
