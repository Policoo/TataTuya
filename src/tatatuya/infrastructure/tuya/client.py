"""Settings-driven, read-only Tuya OpenAPI client."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.energy import DecimalExpansionError, canonical_decimal
from tatatuya.domain.models import (
    Device,
    DeviceStatus,
    EnergySpecification,
    TuyaSettings,
)
from tatatuya.infrastructure.tuya.parsers import (
    parse_batch_status,
    parse_device_page,
    parse_energy_specification,
    parse_individual_status,
    redact_sensitive_fields,
)
from tatatuya.infrastructure.tuya.signing import (
    RequestSigner,
    canonical_path,
    json_bytes,
)


REGION_BASE_URLS = {
    "central_europe": "https://openapi.tuyaeu.com",
    "western_europe": "https://openapi-weaz.tuyaeu.com",
    "western_america": "https://openapi.tuyaus.com",
    "eastern_america": "https://openapi-ueaz.tuyaus.com",
    "china": "https://openapi.tuyacn.com",
    "india": "https://openapi.tuyain.com",
}
MAX_BATCH_SIZE = 20
MAX_DEVICE_PAGES = 50
MAX_DISCOVERED_DEVICES = 5_000
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


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
    timeout_seconds: float = 5.0
    allowed_origin: str | None = None
    cancellation: CancellationContext | None = None
    deadline_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class BodyLimits:
    raw_bytes: int
    decoded_characters: int
    error_raw_bytes: int = 65_536
    error_decoded_characters: int = 65_536
    decimal_integers: bool = False


@dataclass(frozen=True, slots=True)
class BoundedPayload:
    payload: Mapping[str, Any]
    raw_bytes: int
    decoded_characters: int


class Transport(Protocol):
    def send(self, request: PreparedRequest) -> Mapping[str, Any]: ...


class UrllibTransport:
    def __init__(self, timeout_seconds: float = 30, *, opener: Any | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    def send(self, request: PreparedRequest) -> Mapping[str, Any]:
        return self.send_bounded(
            request,
            BodyLimits(1_048_576, 1_048_576),
        ).payload

    def send_bounded(
        self, request: PreparedRequest, limits: BodyLimits
    ) -> BoundedPayload:
        _validate_request_url(request.url, request.allowed_origin)
        deadline = request.deadline_monotonic or (
            time.monotonic() + min(self.timeout_seconds, request.timeout_seconds)
        )
        _request_checkpoint(request, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TuyaAPIError(
                "Tuya request deadline is exhausted",
                request_info=request.diagnostic,
            )
        headers = dict(request.headers)
        headers["Accept-Encoding"] = "identity"
        raw_request = Request(
            request.url,
            data=request.body or None,
            headers=headers,
            method=request.method,
        )
        try:
            open_request = (
                self._opener.open if self._opener is not None else _open_no_redirect
            )
            with open_request(
                raw_request,
                timeout=max(
                    0.001,
                    min(self.timeout_seconds, request.timeout_seconds, remaining),
                ),
            ) as response:
                _validate_identity_encoding(response)
                _validate_declared_length(response, limits.raw_bytes)
                raw_body = _read_bounded(
                    response,
                    limits.raw_bytes,
                    cancellation=request.cancellation,
                    deadline_monotonic=deadline,
                )
        except HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                raise TuyaAPIError(
                    "Tuya redirect responses are not allowed",
                    request_info=request.diagnostic,
                ) from exc
            try:
                _validate_identity_encoding(exc)
                _validate_declared_length(exc, limits.error_raw_bytes)
                error_body = _read_bounded(
                    exc,
                    limits.error_raw_bytes,
                    cancellation=request.cancellation,
                    deadline_monotonic=deadline,
                )
                details = error_body.decode("utf-8", errors="strict")
                if len(details) > limits.error_decoded_characters:
                    raise ResponseLimitError("Tuya error response is too large")
            except ResponseDeadlineError as deadline_error:
                raise TuyaAPIError(
                    "Tuya request deadline is exhausted",
                    request_info=request.diagnostic,
                ) from deadline_error
            except (ResponseLimitError, UnicodeDecodeError):
                diagnostic_body = {"body_format": "discarded"}
            else:
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
        except ResponseLimitError as exc:
            raise TuyaAPIError(
                "Tuya response is too large",
                request_info=request.diagnostic,
            ) from exc
        except ResponseDeadlineError as exc:
            raise TuyaAPIError(
                "Tuya request deadline is exhausted",
                request_info=request.diagnostic,
            ) from exc
        except URLError as exc:
            raise TuyaAPIError(
                "Tuya could not be reached",
                request_info=request.diagnostic,
            ) from exc
        try:
            decoded = raw_body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise TuyaAPIError(
                "Tuya returned invalid UTF-8",
                request_info=request.diagnostic,
            ) from exc
        if len(decoded) > limits.decoded_characters:
            raise TuyaAPIError(
                "Tuya response is too large",
                request_info=request.diagnostic,
            )
        try:
            payload = _parse_json(decoded, decimal_integers=limits.decimal_integers)
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
        return BoundedPayload(payload, len(raw_body), len(decoded))


class QtNetworkTransport:
    """Abortable Qt transport whose deadline includes DNS, TLS, and body reads."""

    def __init__(
        self,
        timeout_seconds: float = 30,
        *,
        manager_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.manager_factory = manager_factory

    def send(self, request: PreparedRequest) -> Mapping[str, Any]:
        return self.send_bounded(
            request,
            BodyLimits(1_048_576, 1_048_576),
        ).payload

    def send_bounded(
        self, request: PreparedRequest, limits: BodyLimits
    ) -> BoundedPayload:
        from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

        _validate_request_url(request.url, request.allowed_origin)
        if request.method.upper() != "GET" or request.body:
            raise TuyaAPIError("Tuya transport rejected a non-read-only request")
        deadline = request.deadline_monotonic or (
            time.monotonic() + min(self.timeout_seconds, request.timeout_seconds)
        )
        _request_checkpoint(request, deadline)
        remaining = min(
            self.timeout_seconds,
            request.timeout_seconds,
            deadline - time.monotonic(),
        )
        if remaining <= 0:
            raise TuyaAPIError(
                "Tuya request deadline is exhausted",
                request_info=request.diagnostic,
            )

        _ensure_qt_core_application(QCoreApplication)
        manager = (
            self.manager_factory()
            if self.manager_factory is not None
            else QNetworkAccessManager()
        )
        network_request = QNetworkRequest(QUrl(request.url))
        network_request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        network_request.setTransferTimeout(max(1, int(remaining * 1000)))
        headers = dict(request.headers)
        headers["Accept-Encoding"] = "identity"
        for name, value in headers.items():
            network_request.setRawHeader(name.encode("ascii"), value.encode("utf-8"))

        reply = manager.get(network_request)
        loop = QEventLoop()
        deadline_timer = QTimer()
        deadline_timer.setSingleShot(True)
        cancellation_timer = QTimer()
        cancellation_timer.setInterval(25)
        raw_body = bytearray()
        state: dict[str, Any] = {
            "deadline": False,
            "cancelled": False,
            "oversize": False,
            "metadata": False,
            "status": None,
            # Until HTTP metadata proves this is a successful response, apply
            # the smaller error/redirect budget.
            "raw_limit": limits.error_raw_bytes,
            "encoding_error": False,
            "length_error": False,
        }

        def classify_metadata() -> None:
            raw_status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
            if raw_status is None:
                return
            status = int(raw_status)
            state["status"] = status
            state["metadata"] = True
            state["raw_limit"] = (
                limits.raw_bytes
                if status < 400 and status not in _REDIRECT_CODES
                else limits.error_raw_bytes
            )
            encoding = _qt_bytes(reply.rawHeader("Content-Encoding").data()).decode(
                "ascii", errors="ignore"
            )
            if encoding and encoding.strip().casefold() != "identity":
                state["encoding_error"] = True
                reply.abort()
                return
            declared_length = _qt_bytes(reply.rawHeader("Content-Length").data())
            if declared_length:
                try:
                    parsed_length = int(declared_length)
                except ValueError:
                    state["length_error"] = True
                    reply.abort()
                    return
                if parsed_length < 0 or parsed_length > int(state["raw_limit"]):
                    state["oversize"] = True
                    reply.abort()

        def drain() -> None:
            classify_metadata()
            if not state["metadata"]:
                return
            chunk = _qt_bytes(reply.readAll().data())
            if not chunk:
                return
            maximum_body = int(state["raw_limit"])
            remaining_body = maximum_body + 1 - len(raw_body)
            raw_body.extend(chunk[: max(0, remaining_body)])
            if len(chunk) > remaining_body or len(raw_body) > maximum_body:
                state["oversize"] = True
                reply.abort()

        def expire() -> None:
            state["deadline"] = True
            reply.abort()

        def check_cancellation() -> None:
            if request.cancellation is not None and request.cancellation.cancelled:
                state["cancelled"] = True
                reply.abort()

        metadata_signal = getattr(reply, "metaDataChanged", None)
        if metadata_signal is not None:
            metadata_signal.connect(classify_metadata)
        reply.readyRead.connect(drain)
        reply.finished.connect(loop.quit)
        deadline_timer.timeout.connect(expire)
        cancellation_timer.timeout.connect(check_cancellation)
        deadline_timer.start(max(1, int(remaining * 1000)))
        if request.cancellation is not None:
            cancellation_timer.start()
        loop.exec()
        classify_metadata()
        drain()
        deadline_timer.stop()
        cancellation_timer.stop()

        if state["cancelled"] and request.cancellation is not None:
            request.cancellation.checkpoint()
        if state["deadline"] or time.monotonic() >= deadline:
            raise TuyaAPIError(
                "Tuya request deadline is exhausted",
                request_info=request.diagnostic,
            )

        status = state["status"]
        if status in _REDIRECT_CODES:
            raise TuyaAPIError(
                "Tuya redirect responses are not allowed",
                request_info=request.diagnostic,
            )
        if reply.error() != QNetworkReply.NetworkError.NoError and status is None:
            raise TuyaAPIError(
                "Tuya could not be reached",
                request_info=request.diagnostic,
            )

        raw_limit = int(state["raw_limit"])
        if state["oversize"] or len(raw_body) > raw_limit:
            raise TuyaAPIError(
                "Tuya response is too large",
                request_info=request.diagnostic,
            )
        if state["encoding_error"]:
            raise TuyaAPIError("Tuya returned an unsupported content encoding")
        if state["length_error"]:
            raise TuyaAPIError("Tuya returned an invalid content length")
        try:
            decoded = bytes(raw_body).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise TuyaAPIError(
                "Tuya returned invalid UTF-8",
                request_info=request.diagnostic,
            ) from exc

        character_limit = (
            limits.error_decoded_characters
            if status is not None and status >= 400
            else limits.decoded_characters
        )
        if len(decoded) > character_limit:
            raise TuyaAPIError(
                "Tuya response is too large",
                request_info=request.diagnostic,
            )
        if status is not None and status >= 400:
            try:
                diagnostic_body = _parse_json(decoded)
            except json.JSONDecodeError:
                diagnostic_body = {
                    "body_format": "non-json",
                    "body_length": len(decoded),
                }
            raise TuyaAPIError(
                f"Tuya HTTP error {status}",
                request_info=request.diagnostic,
                response_payload=diagnostic_body,
            )
        try:
            payload = _parse_json(decoded, decimal_integers=limits.decimal_integers)
        except json.JSONDecodeError as exc:
            raise TuyaAPIError(
                "Tuya returned invalid JSON",
                request_info=request.diagnostic,
            ) from exc
        if not isinstance(payload, Mapping):
            raise TuyaAPIError(
                "Tuya returned a non-object response",
                request_info=request.diagnostic,
            )
        return BoundedPayload(payload, len(raw_body), len(decoded))


class ResponseLimitError(RuntimeError):
    pass


class ResponseDeadlineError(RuntimeError):
    pass


class TuyaClient:
    """A Tuya client whose credentials are supplied by persisted settings."""

    def __init__(
        self,
        settings: TuyaSettings,
        *,
        transport: Transport | None = None,
        clock_ms: Callable[[], str] | None = None,
        cancellation: CancellationContext | None = None,
    ) -> None:
        if not settings.is_complete:
            raise TuyaConfigError("Tuya settings are incomplete")
        try:
            self.base_url = REGION_BASE_URLS[settings.region].rstrip("/")
        except KeyError as exc:
            raise TuyaConfigError(f"Unknown Tuya region: {settings.region}") from exc
        self.settings = settings
        self.transport = transport or QtNetworkTransport(5)
        self.clock_ms = clock_ms or (lambda: str(time.time_ns() // 1_000_000))
        self.cancellation = cancellation
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
        for _page_number in range(1, MAX_DEVICE_PAGES + 1):
            if self.cancellation is not None:
                self.cancellation.checkpoint()
            page = parse_device_page(
                self._request("GET", "/v1.0/iot-01/associated-users/devices", query),
                self._secrets(),
            )
            for device in page.devices:
                devices.setdefault(device.device_id, device)
                if len(devices) > MAX_DISCOVERED_DEVICES:
                    raise TuyaAPIError(
                        "Tuya device listing exceeded the safety limit"
                    )
            if not page.has_more:
                return list(devices.values())
            cursor = page.last_row_key
            if cursor is None or cursor in seen_cursors:
                raise TuyaAPIError("Tuya returned invalid device pagination metadata")
            seen_cursors.add(cursor)
            query["last_row_key"] = cursor
        raise TuyaAPIError(
            f"Tuya device listing exceeded {MAX_DEVICE_PAGES} pages"
        )

    def get_device_specification(self, device_id: str) -> EnergySpecification:
        encoded_id = quote(device_id, safe="")
        return parse_energy_specification(
            self._request("GET", f"/v1.0/iot-03/devices/{encoded_id}/specification"),
            self._secrets(),
        )

    def get_device_status(self, device_id: str) -> DeviceStatus:
        encoded_id = quote(device_id, safe="")
        result = self._request("GET", f"/v1.0/iot-03/devices/{encoded_id}/status")
        return parse_individual_status(device_id, result, self._secrets())

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
            statuses.update(parse_batch_status(result, self._secrets()))
        return statuses

    def get_report_log_page(
        self,
        device_id: str,
        code: str,
        start_time_ms: int,
        end_time_ms: int,
        *,
        last_row_key: str | None,
        size: int,
        raw_allowance: int,
        decoded_allowance: int,
    ) -> BoundedPayload:
        """Fetch one exact-decimal report-log page under caller budgets."""

        encoded_id = quote(device_id, safe="")
        params: dict[str, Any] = {
            "codes": code,
            "start_time": start_time_ms,
            "end_time": end_time_ms,
            "size": size,
        }
        if last_row_key:
            params["last_row_key"] = last_row_key
        prepared = self._prepare_request(
            "GET", f"/v2.1/cloud/thing/{encoded_id}/report-logs", params
        )
        send_bounded = getattr(self.transport, "send_bounded", None)
        if send_bounded is None:
            raise TuyaAPIError("Tuya transport does not support bounded responses")
        try:
            envelope = send_bounded(
                prepared,
                BodyLimits(
                    min(1_048_576, raw_allowance),
                    min(1_048_576, decoded_allowance),
                    decimal_integers=True,
                ),
            )
        except TuyaAPIError as exc:
            raise self._redacted_error(exc) from exc
        result = self._extract_result(envelope.payload, prepared.diagnostic)
        if not isinstance(result, Mapping):
            raise TuyaAPIError("Tuya returned an invalid report-log page")
        sanitized = _redact_payload(result, self._secrets())
        if not isinstance(sanitized, Mapping):
            raise TuyaAPIError("Tuya returned an invalid report-log page")
        return BoundedPayload(
            sanitized,
            envelope.raw_bytes,
            envelope.decoded_characters,
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Any | None = None,
        *,
        use_token: bool = True,
    ) -> Any:
        prepared = self._prepare_request(
            method, path, params, body, use_token=use_token
        )
        try:
            payload = self.transport.send(prepared)
        except TuyaAPIError as exc:
            raise self._redacted_error(exc) from exc
        return self._extract_result(payload, prepared.diagnostic)

    def _prepare_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Any | None = None,
        *,
        use_token: bool = True,
    ) -> PreparedRequest:
        if self.cancellation is not None:
            self.cancellation.checkpoint()
        if use_token and not self.access_token:
            self.authenticate()
            if self.cancellation is not None:
                self.cancellation.checkpoint()
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
        timeout_seconds = 5.0
        if self.cancellation is not None:
            timeout_seconds = self.cancellation.remote_timeout_seconds()
            if timeout_seconds <= 0:
                self.cancellation.checkpoint()
                raise TuyaAPIError("Tuya request deadline is exhausted")
        return PreparedRequest(
            method.upper(),
            self.base_url + request_path,
            headers,
            body_data,
            diagnostic,
            timeout_seconds,
            self.base_url,
            self.cancellation,
            time.monotonic() + timeout_seconds,
        )

    def _extract_result(
        self, payload: Mapping[str, Any], diagnostic: Mapping[str, Any]
    ) -> Any:
        if payload.get("success") is False:
            code = payload.get("code", "unknown")
            message = _redact(
                str(payload.get("msg", "Tuya request failed")), self._secrets()
            )
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

    def _redacted_error(self, exc: TuyaAPIError) -> TuyaAPIError:
        secrets = self._secrets()
        return TuyaAPIError(
            _redact(str(exc), secrets),
            request_info=_redact_payload(exc.request_info, secrets),
            response_payload=_redact_payload(exc.response_payload, secrets),
        )

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
        try:
            return canonical_decimal(value)
        except (DecimalExpansionError, ValueError):
            return "[DECIMAL_DISCARDED]"
    return value


def _ensure_qt_core_application(application_type: Any) -> Any:
    existing = application_type.instance()
    if existing is None:
        raise RuntimeError(
            "A Qt application must be created by the application owner before networking"
        )
    return existing


def _qt_bytes(value: Any) -> bytes:
    return bytes(value)


class _RejectRedirects(HTTPRedirectHandler):
    """Reject redirects before urllib can copy Tuya authentication headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalized_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.hostname
    ):
        raise TuyaAPIError("Tuya request URL failed security validation")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise TuyaAPIError("Tuya request URL failed security validation") from exc
    return ("https", parsed.hostname.casefold().rstrip("."), port)


def _validate_request_url(url: str, allowed_origin: str | None) -> None:
    actual = _normalized_origin(url)
    if allowed_origin is not None and actual != _normalized_origin(allowed_origin):
        raise TuyaAPIError("Tuya request URL failed security validation")


_NO_REDIRECT_OPENER = build_opener(_RejectRedirects())


def _open_no_redirect(request: Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _parse_json(raw: str, *, decimal_integers: bool = False) -> Any:
    return json.loads(
        raw,
        parse_float=Decimal,
        parse_int=Decimal if decimal_integers else int,
    )


def _validate_identity_encoding(response: Any) -> None:
    headers = getattr(response, "headers", None)
    encoding = headers.get("Content-Encoding") if headers is not None else None
    if encoding and str(encoding).strip().lower() != "identity":
        raise TuyaAPIError("Tuya returned an unsupported content encoding")


def _validate_declared_length(response: Any, allowance: int) -> None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    if value is None:
        return
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise TuyaAPIError("Tuya returned an invalid content length") from exc
    if length < 0 or length > allowance:
        raise ResponseLimitError("Tuya response is too large")


def _read_bounded(
    stream: Any,
    allowance: int,
    *,
    cancellation: CancellationContext | None = None,
    deadline_monotonic: float | None = None,
) -> bytes:
    if allowance < 0:
        raise ResponseLimitError("Tuya response budget is exhausted")
    chunks: list[bytes] = []
    remaining = allowance + 1
    while remaining > 0:
        if cancellation is not None:
            cancellation.checkpoint()
        if deadline_monotonic is not None:
            deadline_remaining = deadline_monotonic - time.monotonic()
            if deadline_remaining <= 0:
                raise ResponseDeadlineError("Tuya response deadline is exhausted")
            _set_stream_timeout(stream, deadline_remaining)
        size = min(65_536, remaining)
        try:
            chunk = stream.read(size)
        except TypeError as exc:
            raise ResponseLimitError(
                "Tuya response stream does not support bounded reads"
            ) from exc
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise ResponseDeadlineError("Tuya response deadline is exhausted")
    body = b"".join(chunks)
    if len(body) > allowance:
        raise ResponseLimitError("Tuya response is too large")
    return body


def _request_checkpoint(request: PreparedRequest, deadline_monotonic: float) -> None:
    if request.cancellation is not None:
        request.cancellation.checkpoint()
    if time.monotonic() >= deadline_monotonic:
        raise TuyaAPIError(
            "Tuya request deadline is exhausted",
            request_info=request.diagnostic,
        )


def _set_stream_timeout(stream: Any, seconds: float) -> None:
    """Tighten the real urllib response socket to the absolute time remaining."""

    candidate = stream
    for attribute in ("fp", "raw", "_sock"):
        setter = getattr(candidate, "settimeout", None)
        if callable(setter):
            setter(max(0.001, seconds))
            return
        candidate = getattr(candidate, attribute, None)
        if candidate is None:
            return
    setter = getattr(candidate, "settimeout", None)
    if callable(setter):
        setter(max(0.001, seconds))
