"""Small Tuya OpenAPI helper functions.

Local prototype credentials are loaded from the repository's ignored ``.env``
file. The region must match the data center selected for the Tuya IoT Cloud
project.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv


load_dotenv()

CLIENT_ID = os.getenv("TUYA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("TUYA_CLIENT_SECRET", "")
APP_ACCOUNT_UID = os.getenv("TUYA_APP_ACCOUNT_UID", "")
REGION = os.getenv("TUYA_REGION", "central_europe")

REGION_BASE_URLS = {
    "central_europe": "https://openapi.tuyaeu.com",
    "western_europe": "https://openapi-weaz.tuyaeu.com",
    "western_america": "https://openapi.tuyaus.com",
    "eastern_america": "https://openapi-ueaz.tuyaus.com",
    "china": "https://openapi.tuyacn.com",
    "india": "https://openapi.tuyain.com",
}

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class TuyaConfigError(RuntimeError):
    """Raised when local Tuya credentials or region are missing/invalid."""


class TuyaAPIError(RuntimeError):
    """Raised when Tuya returns an HTTP error or an unsuccessful API payload."""

    def __init__(
        self,
        message: str,
        request_info: dict[str, Any] | None = None,
        response_payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.request_info = request_info or {}
        self.response_payload = response_payload


def _now_ms() -> str:
    return str(time.time_ns() // 1_000_000)


def _json_bytes(body: Any | None) -> bytes:
    if body is None:
        return b""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass
class TuyaClient:
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    app_account_uid: str = APP_ACCOUNT_UID
    region: str = REGION
    access_token: str | None = None

    @property
    def base_url(self) -> str:
        try:
            return REGION_BASE_URLS[self.region].rstrip("/")
        except KeyError as exc:
            valid_regions = ", ".join(sorted(REGION_BASE_URLS))
            raise TuyaConfigError(
                f"Unknown Tuya region '{self.region}'. Valid regions: {valid_regions}"
            ) from exc

    def _check_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise TuyaConfigError(
                "Set TUYA_CLIENT_ID and TUYA_CLIENT_SECRET in .env before calling Tuya."
            )

    def _canonical_path(self, path: str, params: dict[str, Any] | None = None) -> str:
        parsed = urlparse(path)
        all_params = parse_qsl(parsed.query, keep_blank_values=True)

        if params:
            for key in sorted(params):
                value = params[key]
                if value is None:
                    continue
                if isinstance(value, (list, tuple, set)):
                    value = ",".join(str(item) for item in value)
                all_params.append((key, str(value)))

        query = urlencode(all_params)
        return parsed.path + (f"?{query}" if query else "")

    def _sign(
        self,
        method: str,
        canonical_path: str,
        timestamp: str,
        body_bytes: bytes,
        access_token: str | None = None,
    ) -> str:
        content_hash = hashlib.sha256(body_bytes).hexdigest()
        string_to_sign = f"{method.upper()}\n{content_hash}\n\n{canonical_path}"
        payload = self.client_id
        if access_token:
            payload += access_token
        payload += timestamp + string_to_sign
        digest = hmac.new(
            self.client_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest.upper()

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
        use_token: bool = True,
    ) -> dict[str, Any]:
        self._check_credentials()

        method = method.upper()
        body_bytes = _json_bytes(body)
        timestamp = _now_ms()
        canonical_path = self._canonical_path(path, params)
        token = self.access_token if use_token else None

        if use_token and not token:
            token = self.get_access_token()

        headers = {
            "client_id": self.client_id,
            "sign": self._sign(method, canonical_path, timestamp, body_bytes, token),
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "lang": "en",
        }
        if token:
            headers["access_token"] = token
        if body_bytes:
            headers["Content-Type"] = "application/json"

        request_info = {
            "method": method,
            "base_url": self.base_url,
            "path": canonical_path,
            "url": self.base_url + canonical_path,
            "use_token": use_token,
            "has_access_token": bool(token),
            "region": self.region,
            "body": body if body is not None else None,
        }
        request = Request(
            self.base_url + canonical_path,
            data=body_bytes if body_bytes else None,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise TuyaAPIError(
                f"HTTP {exc.code} from Tuya: {details}",
                request_info=request_info,
                response_payload=details,
            ) from exc
        except URLError as exc:
            raise TuyaAPIError(
                f"Could not reach Tuya: {exc.reason}",
                request_info=request_info,
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TuyaAPIError(
                f"Tuya returned non-JSON response: {raw}",
                request_info=request_info,
                response_payload=raw,
            ) from exc

        if payload.get("success") is False:
            code = payload.get("code", "unknown")
            message = payload.get("msg", "Tuya request failed")
            raise TuyaAPIError(
                f"Tuya error {code}: {message}",
                request_info=request_info,
                response_payload=payload,
            )

        return payload

    def get_access_token(self) -> str:
        payload = self.request("GET", "/v1.0/token", {"grant_type": 1}, use_token=False)
        token = payload.get("result", {}).get("access_token")
        if not token:
            raise TuyaAPIError(f"No access token in response: {payload}")
        self.access_token = token
        return token

    def list_devices(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/v1.0/iot-01/associated-users/devices", params)

    def get_device_specification(self, device_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1.0/iot-03/devices/{device_id}/specification")

    def get_device_status(self, device_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1.0/iot-03/devices/{device_id}/status")

    def get_devices_status(self, device_ids: list[str] | tuple[str, ...] | str) -> dict[str, Any]:
        if isinstance(device_ids, str):
            device_ids_value = device_ids
        else:
            device_ids_value = ",".join(device_ids)
        return self.request(
            "GET",
            "/v1.0/iot-03/devices/status",
            {"device_ids": device_ids_value},
        )

    def get_electricity_statistics_sum(self, **params: Any) -> dict[str, Any]:
        return self.request(
            "GET",
            "/v1.0/iot-03/energy/electricity/device/nodes/statistics-sum",
            params,
        )

    def get_electricity_statistics_trend(self, **params: Any) -> dict[str, Any]:
        return self.request(
            "GET",
            "/v1.0/iot-03/energy/electricity/devices/nodes/statistics-trend",
            params,
        )


_default_client: TuyaClient | None = None


def get_default_client() -> TuyaClient:
    global _default_client
    if _default_client is None:
        _default_client = TuyaClient()
    return _default_client


def get_access_token() -> str:
    return get_default_client().get_access_token()


def list_devices(**params: Any) -> dict[str, Any]:
    return get_default_client().list_devices(**params)


def get_device_specification(device_id: str) -> dict[str, Any]:
    return get_default_client().get_device_specification(device_id)


def get_device_status(device_id: str) -> dict[str, Any]:
    return get_default_client().get_device_status(device_id)


def get_devices_status(device_ids: list[str] | tuple[str, ...] | str) -> dict[str, Any]:
    return get_default_client().get_devices_status(device_ids)


def get_electricity_statistics_sum(**params: Any) -> dict[str, Any]:
    return get_default_client().get_electricity_statistics_sum(**params)


def get_electricity_statistics_trend(**params: Any) -> dict[str, Any]:
    return get_default_client().get_electricity_statistics_trend(**params)
