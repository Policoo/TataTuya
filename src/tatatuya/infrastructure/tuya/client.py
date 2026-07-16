"""Settings-driven, read-only Tuya OpenAPI client."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tatatuya.domain.models import Device, DeviceStatus, EnergySpecification, TuyaSettings
from tatatuya.infrastructure.tuya.parsers import (
    parse_batch_status,
    parse_device_page,
    parse_energy_specification,
    parse_individual_status,
    redact_sensitive_fields,
)
from tatatuya.infrastructure.tuya.signing import RequestSigner, canonical_path, json_bytes


REGION_BASE_URLS = {
    "central_europe": "https://openapi.tuyaeu.com",
    "western_europe": "https://openapi-weaz.tuyaeu.com",
    "western_america": "https://openapi.tuyaus.com",
    "eastern_america": "https://openapi-ueaz.tuyaus.com",
    "china": "https://openapi.tuyacn.com",
    "india": "https://openapi.tuyain.com",
}
MAX_BATCH_SIZE = 20


class TuyaConfigError(RuntimeError):
    pass


class TuyaAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_info: Mapping[str, Any] | None = None,
        response_payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.request_info = dict(request_info or {})
        self.response_payload = response_payload


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes
    diagnostic: Mapping[str, Any]


class Transport(Protocol):
    def send(self, request: PreparedRequest) -> Mapping[str, Any]: ...


class UrllibTransport:
    def __init__(self, timeout_seconds: float = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def send(self, request: PreparedRequest) -> Mapping[str, Any]:
        raw_request = Request(
            request.url,
            data=request.body or None,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with urlopen(raw_request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            try:
                diagnostic_body = _parse_json(details)
            except json.JSONDecodeError:
                # Opaque bodies can contain credentials with no discoverable key name.
                diagnostic_body = {
                    "body_format": "non-json",
                    "body_length": len(details),
                }
            raise TuyaAPIError(
                f"Tuya HTTP error {exc.code}",
                request_info=request.diagnostic,
                response_payload=diagnostic_body,
            ) from exc
        except URLError as exc:
            raise TuyaAPIError(
                "Tuya could not be reached",
                request_info=request.diagnostic,
            ) from exc
        try:
            payload = _parse_json(raw)
        except json.JSONDecodeError as exc:
            raise TuyaAPIError(
                "Tuya returned an invalid response",
                request_info=request.diagnostic,
            ) from exc
        if not isinstance(payload, Mapping):
            raise TuyaAPIError(
                "Tuya returned an invalid response",
                request_info=request.diagnostic,
            )
        return payload


class TuyaClient:
    """A Tuya client whose credentials are supplied by persisted settings."""

    def __init__(
        self,
        settings: TuyaSettings,
        *,
        transport: Transport | None = None,
        clock_ms: Callable[[], str] | None = None,
    ) -> None:
        if not settings.is_complete:
            raise TuyaConfigError("Tuya settings are incomplete")
        try:
            self.base_url = REGION_BASE_URLS[settings.region].rstrip("/")
        except KeyError as exc:
            raise TuyaConfigError(f"Unknown Tuya region: {settings.region}") from exc
        self.settings = settings
        self.transport = transport or UrllibTransport()
        self.clock_ms = clock_ms or (lambda: str(time.time_ns() // 1_000_000))
        self.signer = RequestSigner(settings.client_id, settings.client_secret)
        self.access_token: str | None = None

    def authenticate(self) -> str:
        result = self._request("GET", "/v1.0/token", {"grant_type": 1}, use_token=False)
        token = result.get("access_token") if isinstance(result, Mapping) else None
        if not isinstance(token, str) or not token:
            raise TuyaAPIError("Tuya did not return an access token")
        self.access_token = token
        return token

    def list_devices(self, **params: Any) -> list[Device]:
        query = dict(params)
        devices: dict[str, Device] = {}
        seen_cursors: set[str] = set()
        while True:
            page = parse_device_page(
                self._request(
                    "GET", "/v1.0/iot-01/associated-users/devices", query
                )
            )
            for device in page.devices:
                devices.setdefault(device.device_id, device)
            if not page.has_more:
                return list(devices.values())
            cursor = page.last_row_key
            if cursor is None or cursor in seen_cursors:
                raise TuyaAPIError(
                    "Tuya returned invalid device pagination metadata"
                )
            seen_cursors.add(cursor)
            query["last_row_key"] = cursor

    def get_device_specification(self, device_id: str) -> EnergySpecification:
        encoded_id = quote(device_id, safe="")
        return parse_energy_specification(
            self._request("GET", f"/v1.0/iot-03/devices/{encoded_id}/specification")
        )

    def get_device_status(self, device_id: str) -> DeviceStatus:
        encoded_id = quote(device_id, safe="")
        result = self._request("GET", f"/v1.0/iot-03/devices/{encoded_id}/status")
        return parse_individual_status(device_id, result)

    def get_devices_status(self, device_ids: Sequence[str]) -> dict[str, DeviceStatus]:
        unique_ids = list(dict.fromkeys(str(item) for item in device_ids if str(item)))
        statuses: dict[str, DeviceStatus] = {}
        for start in range(0, len(unique_ids), MAX_BATCH_SIZE):
            chunk = unique_ids[start : start + MAX_BATCH_SIZE]
            result = self._request(
                "GET",
                "/v1.0/iot-03/devices/status",
                {"device_ids": ",".join(chunk)},
            )
            statuses.update(parse_batch_status(result))
        return statuses

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Any | None = None,
        *,
        use_token: bool = True,
    ) -> Any:
        if use_token and not self.access_token:
            self.authenticate()
        timestamp = self.clock_ms()
        request_path = canonical_path(path, params)
        body_data = json_bytes(body)
        token = self.access_token if use_token else None
        headers = {
            "client_id": self.settings.client_id,
            "sign": self.signer.sign(method, request_path, timestamp, body_data, token),
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "lang": "en",
        }
        if token:
            headers["access_token"] = token
        if body_data:
            headers["Content-Type"] = "application/json"
        diagnostic = {
            "method": method.upper(),
            "url": self.base_url + request_path,
            "region": self.settings.region,
            "uses_access_token": bool(token),
        }
        prepared = PreparedRequest(
            method.upper(), self.base_url + request_path, headers, body_data, diagnostic
        )
        try:
            payload = self.transport.send(prepared)
        except TuyaAPIError as exc:
            secrets = self._secrets()
            raise TuyaAPIError(
                _redact(str(exc), secrets),
                request_info=_redact_payload(exc.request_info, secrets),
                response_payload=_redact_payload(exc.response_payload, secrets),
            ) from exc
        if payload.get("success") is False:
            code = payload.get("code", "unknown")
            message = _redact(str(payload.get("msg", "Tuya request failed")), self._secrets())
            raise TuyaAPIError(
                f"Tuya error {code}: {message}",
                request_info=diagnostic,
                response_payload=_redact_payload(payload, self._secrets()),
            )
        if "result" not in payload:
            raise TuyaAPIError(
                "Tuya response has no result",
                request_info=diagnostic,
                response_payload=_redact_payload(payload, self._secrets()),
            )
        return payload["result"]

    def _secrets(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.settings.client_secret,
                self.access_token,
            )
            if value
        )


def _redact(value: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def _redact_payload(value: Any, secrets: Sequence[str]) -> Any:
    value = redact_sensitive_fields(value)
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, Mapping):
        return {key: _redact_payload(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, secrets) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _parse_json(raw: str) -> Any:
    return json.loads(raw, parse_float=Decimal)
