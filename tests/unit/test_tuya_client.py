import json
from decimal import Decimal
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.tuya.client import (
    BodyLimits,
    BoundedPayload,
    PreparedRequest,
    QtNetworkTransport,
    TuyaAPIError,
    TuyaClient,
    UrllibTransport,
    _RejectRedirects,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "tuya_responses"
SETTINGS = TuyaSettings("client-id", "super-secret", "central_europe", Currency.RON)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[PreparedRequest] = []

    def send(self, request: PreparedRequest):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client_with(transport: FakeTransport) -> TuyaClient:
    client = TuyaClient(SETTINGS, transport=transport, clock_ms=lambda: "1721124000000")
    client.access_token = "token-value"
    return client


def test_default_client_uses_abortable_qt_transport() -> None:
    client = TuyaClient(SETTINGS)

    assert isinstance(client.transport, QtNetworkTransport)


def test_qt_transport_rejects_non_read_only_request_before_network() -> None:
    request = PreparedRequest(
        "POST",
        "https://openapi.tuyaeu.com/v1.0/devices/meter-1/commands",
        {},
        b"{}",
        {},
        allowed_origin="https://openapi.tuyaeu.com",
    )

    with pytest.raises(TuyaAPIError, match="non-read-only"):
        QtNetworkTransport().send(request)


@pytest.mark.parametrize("cancel", [False, True])
def test_qt_transport_aborts_stalled_tls_inside_absolute_bound(cancel) -> None:
    from PySide6.QtCore import QByteArray, QObject, Signal
    from PySide6.QtNetwork import QNetworkReply

    class StalledReply(QObject):
        readyRead = Signal()
        finished = Signal()

        def abort(self):
            self.finished.emit()

        def readAll(self):
            return QByteArray()

        def attribute(self, name):
            return None

        def error(self):
            return QNetworkReply.NetworkError.NoError

        def rawHeader(self, name):
            return QByteArray()

    reply = StalledReply()

    class Manager:
        def get(self, request):
            return reply

    context = CancellationContext(5)
    cancellation_timer = None
    if cancel:
        cancellation_timer = threading.Timer(0.1, context.cancel)
        cancellation_timer.start()
    started = time.monotonic()
    origin = "https://openapi.tuyaeu.com"
    request = PreparedRequest(
        "GET",
        f"{origin}/v1.0/token",
        {},
        b"",
        {},
        timeout_seconds=1,
        allowed_origin=origin,
        cancellation=context,
        deadline_monotonic=time.monotonic() + (1 if cancel else 0.15),
    )
    try:
        expected_error = UserFacingError if cancel else TuyaAPIError
        with pytest.raises(expected_error):
            QtNetworkTransport(
                timeout_seconds=1,
                manager_factory=Manager,
            ).send(request)
        assert time.monotonic() - started < 1
    finally:
        if cancellation_timer is not None:
            cancellation_timer.cancel()


def test_endpoints_use_settings_and_return_typed_values() -> None:
    transport = FakeTransport(
        [
            fixture("devices.json"),
            fixture("specification.json"),
            fixture("individual_status.json"),
        ]
    )
    client = client_with(transport)

    assert client.list_devices()[0].device_id == "meter-1"
    assert client.get_device_specification("meter-1").scale == 2
    assert client.get_device_status("meter-1").device_id == "meter-1"

    query = parse_qs(urlsplit(transport.requests[0].url).query)
    assert query == {}
    assert transport.requests[0].url == (
        "https://openapi.tuyaeu.com/v1.0/iot-01/associated-users/devices"
    )
    assert transport.requests[1].url.endswith("/devices/meter-1/specification")
    assert transport.requests[2].url.endswith("/devices/meter-1/status")


def test_authentication_uses_unsigned_token_endpoint() -> None:
    transport = FakeTransport(
        [{"success": True, "result": {"access_token": "new-token"}}]
    )
    client = TuyaClient(SETTINGS, transport=transport, clock_ms=lambda: "1721124000000")
    assert client.authenticate() == "new-token"
    request = transport.requests[0]
    assert "access_token" not in request.headers
    assert request.url.endswith("/v1.0/token?grant_type=1")


def test_device_list_follows_cursor_pages_and_deduplicates_ids() -> None:
    first_page = {
        "success": True,
        "result": {
            "devices": [{"id": "meter-1", "name": "Casa"}],
            "has_more": True,
            "last_row_key": "cursor-1",
        },
    }
    second_page = {
        "success": True,
        "result": {
            "devices": [
                {"id": "meter-1", "name": "Duplicat"},
                {"id": "meter-2", "name": "Garaj"},
            ],
            "has_more": False,
            "last_row_key": "cursor-2",
        },
    }
    transport = FakeTransport([first_page, second_page])
    devices = client_with(transport).list_devices(size=20)

    assert [(device.device_id, device.name) for device in devices] == [
        ("meter-1", "Casa"),
        ("meter-2", "Garaj"),
    ]
    first_query = parse_qs(urlsplit(transport.requests[0].url).query)
    second_query = parse_qs(urlsplit(transport.requests[1].url).query)
    assert first_query == {"size": ["20"]}
    assert second_query == {"size": ["20"], "last_row_key": ["cursor-1"]}


def test_device_listing_stops_at_explicit_page_limit() -> None:
    transport = FakeTransport(
        [
            {
                "success": True,
                "result": {
                    "devices": [],
                    "has_more": True,
                    "last_row_key": f"cursor-{index}",
                },
            }
            for index in range(50)
        ]
    )

    with pytest.raises(TuyaAPIError, match="50 pages"):
        client_with(transport).list_devices()

    assert len(transport.requests) == 50


def test_device_listing_cancellation_starts_no_later_page() -> None:
    cancellation = CancellationContext(30)

    class CancelAfterFirstPage(FakeTransport):
        def send(self, request):
            response = super().send(request)
            cancellation.cancel()
            return response

    transport = CancelAfterFirstPage(
        [
            {
                "success": True,
                "result": {
                    "devices": [],
                    "has_more": True,
                    "last_row_key": "next",
                },
            }
        ]
    )
    client = TuyaClient(
        SETTINGS,
        transport=transport,
        cancellation=cancellation,
        clock_ms=lambda: "1721124000000",
    )
    client.access_token = "token-value"

    with pytest.raises(UserFacingError, match="anulată"):
        client.list_devices()

    assert len(transport.requests) == 1


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_transport_rejects_redirect_without_opening_target(code) -> None:
    class RedirectingOpener:
        calls = 0

        def open(self, request, timeout):
            self.calls += 1
            raise HTTPError(
                request.full_url,
                code,
                "redirect",
                HTTPMessage(),
                BytesIO(b"redirect body"),
            )

    opener = RedirectingOpener()
    request = PreparedRequest(
        "GET",
        "https://openapi.tuyaeu.com/v1.0/token",
        {"client_id": "sentinel-client", "sign": "sentinel-sign"},
        b"",
        {"method": "GET", "region": "central_europe"},
        allowed_origin="https://openapi.tuyaeu.com",
    )

    with pytest.raises(TuyaAPIError, match="redirect") as caught:
        UrllibTransport(opener=opener).send(request)

    assert opener.calls == 1
    rendered = repr((str(caught.value), caught.value.response_payload))
    assert "sentinel-client" not in rendered
    assert "sentinel-sign" not in rendered


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_handler_never_constructs_follow_up_request(code) -> None:
    handler = _RejectRedirects()
    request = Request("https://openapi.tuyaeu.com/v1.0/token")
    assert (
        handler.redirect_request(
            request,
            BytesIO(),
            code,
            "",
            HTTPMessage(),
            "https://evil.test",
        )
        is None
    )


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_qt_transport_rejects_redirect_without_follow_up(code) -> None:
    from PySide6.QtCore import QByteArray, QObject, QTimer, Signal
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

    class RedirectReply(QObject):
        readyRead = Signal()
        finished = Signal()

        def abort(self):
            self.finished.emit()

        def readAll(self):
            return QByteArray()

        def attribute(self, name):
            if name == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
                return code
            return None

        def error(self):
            return QNetworkReply.NetworkError.NoError

        def rawHeader(self, name):
            return QByteArray()

    reply = RedirectReply()

    class Manager:
        def __init__(self):
            self.calls = 0

        def get(self, request):
            self.calls += 1
            QTimer.singleShot(0, reply.finished.emit)
            return reply

    manager = Manager()
    request = PreparedRequest(
        "GET",
        "https://openapi.tuyaeu.com/v1.0/token",
        {"client_id": "sentinel-client", "sign": "sentinel-sign"},
        b"",
        {},
        allowed_origin="https://openapi.tuyaeu.com",
    )

    with pytest.raises(TuyaAPIError, match="redirect") as caught:
        QtNetworkTransport(manager_factory=lambda: manager).send(request)

    assert manager.calls == 1
    assert "sentinel" not in str(caught.value)


def _streaming_qt_reply(status, chunks, headers=None):
    from PySide6.QtCore import QByteArray, QObject, QTimer, Signal
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

    class StreamingReply(QObject):
        metaDataChanged = Signal()
        readyRead = Signal()
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.pending = QByteArray()
            self.chunks = list(chunks)
            self.headers = dict(headers or {})
            self.aborted = False
            self.bytes_read = 0

        def start(self):
            self.metaDataChanged.emit()
            if self.aborted:
                return
            self._next()

        def _next(self):
            if self.aborted:
                return
            if not self.chunks:
                self.finished.emit()
                return
            self.pending = QByteArray(self.chunks.pop(0))
            self.readyRead.emit()
            QTimer.singleShot(0, self._next)

        def abort(self):
            if not self.aborted:
                self.aborted = True
                self.finished.emit()

        def readAll(self):
            value = self.pending
            self.pending = QByteArray()
            self.bytes_read += value.size()
            return value

        def attribute(self, name):
            if name == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
                return status
            return None

        def error(self):
            return QNetworkReply.NetworkError.NoError

        def rawHeader(self, name):
            return QByteArray(self.headers.get(str(name), b""))

    reply = StreamingReply()

    class Manager:
        def get(self, request):
            del request
            QTimer.singleShot(0, reply.start)
            return reply

    return reply, Manager


def _qt_request() -> PreparedRequest:
    origin = "https://openapi.tuyaeu.com"
    return PreparedRequest(
        "GET",
        f"{origin}/v1.0/token",
        {},
        b"",
        {},
        allowed_origin=origin,
    )


@pytest.mark.parametrize("status, message", [(500, "too large"), (302, "redirect")])
def test_qt_transport_caps_streamed_error_and_redirect_bodies_immediately(
    status, message
) -> None:
    chunks = [b"x" * 8192 for _ in range(100)]
    reply, manager = _streaming_qt_reply(status, chunks)

    with pytest.raises(TuyaAPIError, match=message):
        QtNetworkTransport(manager_factory=manager).send_bounded(
            _qt_request(), BodyLimits(1_048_576, 1_048_576)
        )

    assert reply.aborted
    assert reply.bytes_read <= 65_536 + 8192
    assert reply.chunks


def test_qt_transport_rejects_declared_oversize_error_before_body_read() -> None:
    reply, manager = _streaming_qt_reply(
        500,
        [b"must-not-be-read"],
        {"Content-Length": b"65537"},
    )

    with pytest.raises(TuyaAPIError, match="too large"):
        QtNetworkTransport(manager_factory=manager).send_bounded(
            _qt_request(), BodyLimits(1_048_576, 1_048_576)
        )

    assert reply.aborted
    assert reply.bytes_read == 0


def test_qt_transport_accepts_success_body_above_error_cap_at_exact_limit() -> None:
    body = b'{"value":"' + (b"x" * 69_988) + b'"}'
    assert len(body) == 70_000
    reply, manager = _streaming_qt_reply(
        200,
        [body[:35_000], body[35_000:]],
        {"Content-Length": b"70000", "Content-Encoding": b"identity"},
    )

    payload = QtNetworkTransport(manager_factory=manager).send_bounded(
        _qt_request(), BodyLimits(70_000, 70_000)
    )

    assert payload.raw_bytes == 70_000
    assert payload.decoded_characters == 70_000
    assert len(payload.payload["value"]) == 69_988
    assert not reply.aborted


@pytest.mark.parametrize(
    "url",
    [
        "http://openapi.tuyaeu.com/v1.0/token",
        "https://user@openapi.tuyaeu.com/v1.0/token",
        "https://openapi.tuyaeu.com/v1.0/token#fragment",
        "https://evil.test/v1.0/token",
        "https://openapi.tuyaeu.com:444/v1.0/token",
    ],
)
def test_transport_rejects_noncanonical_tuya_origin_before_open(url) -> None:
    class NeverOpen:
        def open(self, request, timeout):
            raise AssertionError("unsafe URL reached the network opener")

    request = PreparedRequest(
        "GET",
        url,
        {},
        b"",
        {},
        allowed_origin="https://openapi.tuyaeu.com",
    )
    with pytest.raises(TuyaAPIError, match="security validation"):
        UrllibTransport(opener=NeverOpen()).send(request)


def test_http_transport_parses_fractional_numbers_as_decimal(monkeypatch) -> None:
    raw = (FIXTURES / "individual_status_decimal.json").read_bytes()

    class Response:
        def __init__(self):
            self.body = BytesIO(raw)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return self.body.read(size)

    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client._open_no_redirect",
        lambda request, timeout: Response(),
    )
    payload = UrllibTransport().send(
        PreparedRequest("GET", "https://example.test", {}, b"", {})
    )
    value = payload["result"][0]["value"]
    assert value == Decimal("0.12345678901234567890123456789")
    assert isinstance(value, Decimal)


def test_transport_connect_timeout_uses_absolute_time_remaining(monkeypatch) -> None:
    observed: list[float] = []

    class Response:
        headers = {}

        def __init__(self):
            self.body = BytesIO(b'{"result": {}}')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return self.body.read(size)

    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client.time.monotonic", lambda: 95.0
    )

    class Opener:
        def open(self, request, timeout):
            observed.append(timeout)
            return Response()

    request = PreparedRequest(
        "GET",
        "https://openapi.tuyaeu.com/v1.0/token",
        {},
        b"",
        {},
        timeout_seconds=30,
        allowed_origin="https://openapi.tuyaeu.com",
        deadline_monotonic=100.0,
    )
    UrllibTransport(timeout_seconds=30, opener=Opener()).send(request)

    assert observed == [5.0]


def test_cancellation_during_body_read_stops_before_another_read(monkeypatch) -> None:
    cancellation = CancellationContext(30)

    class Response:
        headers = {}

        def __init__(self):
            self.read_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            self.read_count += 1
            cancellation.cancel()
            return b"{"

    response = Response()
    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client._open_no_redirect",
        lambda request, timeout: response,
    )
    request = PreparedRequest(
        "GET",
        "https://openapi.tuyaeu.com/v1.0/token",
        {},
        b"",
        {},
        allowed_origin="https://openapi.tuyaeu.com",
        cancellation=cancellation,
    )

    with pytest.raises(UserFacingError, match="anulată"):
        UrllibTransport().send(request)

    assert response.read_count == 1


def test_http_json_error_body_is_structured_and_redacted(monkeypatch) -> None:
    body = json.dumps(
        {
            "msg": "request failed",
            "local_key": "device-credential",
            "measurement": 0.12345678901234567890123456789,
        }
    ).encode()

    def fail(request, timeout):
        raise HTTPError(
            request.full_url, 400, "Bad Request", HTTPMessage(), BytesIO(body)
        )

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client._open_no_redirect", fail)
    client = TuyaClient(SETTINGS, transport=UrllibTransport())
    client.access_token = "token-value"
    with pytest.raises(TuyaAPIError) as caught:
        client.list_devices()

    rendered = repr(caught.value.response_payload)
    assert "device-credential" not in rendered
    assert "[REDACTED]" in rendered


def test_http_json_error_discards_extreme_decimal_without_fixed_rendering(
    monkeypatch,
) -> None:
    body = b'{"msg":"failed","measurement":1e-100000}'

    def fail(request, timeout):
        raise HTTPError(
            request.full_url, 400, "Bad Request", HTTPMessage(), BytesIO(body)
        )

    def unexpected_format(*args, **kwargs):
        raise AssertionError("fixed rendering must not run")

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client._open_no_redirect", fail)
    monkeypatch.setattr(
        "tatatuya.domain.energy.format", unexpected_format, raising=False
    )
    client = TuyaClient(SETTINGS, transport=UrllibTransport())
    client.access_token = "token-value"

    with pytest.raises(TuyaAPIError) as caught:
        client.list_devices()

    assert isinstance(caught.value.response_payload, dict)
    assert caught.value.response_payload["measurement"] == "[DECIMAL_DISCARDED]"


def test_unsuccessful_envelope_discards_extreme_decimal_without_fixed_rendering(
    monkeypatch,
) -> None:
    transport = FakeTransport(
        [
            {
                "success": False,
                "code": 1234,
                "msg": "failed",
                "measurement": Decimal("1e-100000"),
            }
        ]
    )

    def unexpected_format(*args, **kwargs):
        raise AssertionError("fixed rendering must not run")

    monkeypatch.setattr(
        "tatatuya.domain.energy.format", unexpected_format, raising=False
    )

    with pytest.raises(TuyaAPIError) as caught:
        client_with(transport).list_devices()

    assert isinstance(caught.value.response_payload, dict)
    assert caught.value.response_payload["measurement"] == "[DECIMAL_DISCARDED]"


def test_http_non_json_error_body_does_not_retain_opaque_content(monkeypatch) -> None:
    body = b"upstream debug output with unknown-api-key-value"

    def fail(request, timeout):
        raise HTTPError(
            request.full_url, 502, "Bad Gateway", HTTPMessage(), BytesIO(body)
        )

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client._open_no_redirect", fail)
    client = TuyaClient(SETTINGS, transport=UrllibTransport())
    client.access_token = "token-value"
    with pytest.raises(TuyaAPIError) as caught:
        client.list_devices()

    assert caught.value.response_payload == {
        "body_format": "non-json",
        "body_length": len(body),
    }
    assert "unknown-api-key-value" not in repr(caught.value.response_payload)


def test_bounded_transport_rejects_declared_oversize_before_read(monkeypatch) -> None:
    class Response:
        headers = {"Content-Length": "101"}
        read_called = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            self.read_called = True
            return b"{}"

    response = Response()
    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client._open_no_redirect",
        lambda request, timeout: response,
    )
    with pytest.raises(TuyaAPIError, match="too large"):
        UrllibTransport().send_bounded(
            PreparedRequest("GET", "https://example.test", {}, b"", {}),
            BodyLimits(100, 100),
        )
    assert not response.read_called


def test_bounded_transport_rejects_streamed_oversize_and_invalid_utf8(
    monkeypatch,
) -> None:
    class Response:
        headers = {}

        def __init__(self, body):
            self.body = BytesIO(body)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return self.body.read(size)

    responses = iter((Response(b"{" + b"x" * 100), Response(b"\xff")))
    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client._open_no_redirect",
        lambda request, timeout: next(responses),
    )
    request = PreparedRequest("GET", "https://example.test", {}, b"", {})
    with pytest.raises(TuyaAPIError, match="too large"):
        UrllibTransport().send_bounded(request, BodyLimits(20, 100))
    with pytest.raises(TuyaAPIError, match="UTF-8"):
        UrllibTransport().send_bounded(request, BodyLimits(20, 100))


def test_bounded_transport_never_falls_back_to_parameterless_read(monkeypatch) -> None:
    class Response:
        headers = {}

        def __init__(self):
            self.sized_reads = 0
            self.parameterless_reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, *args):
            if args:
                self.sized_reads += 1
                raise TypeError("sized reads unsupported")
            self.parameterless_reads += 1
            return b"{}"

    response = Response()
    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.client._open_no_redirect",
        lambda request, timeout: response,
    )

    with pytest.raises(TuyaAPIError):
        UrllibTransport().send_bounded(
            PreparedRequest("GET", "https://example.test", {}, b"", {}),
            BodyLimits(100, 100),
        )

    assert response.sized_reads == 1
    assert response.parameterless_reads == 0


@pytest.mark.parametrize(
    "operation",
    ["devices", "specification", "status", "report_logs"],
)
def test_cancellation_during_authentication_starts_no_original_request(
    operation,
) -> None:
    cancellation = CancellationContext(30)

    class CancelDuringAuthentication:
        def __init__(self):
            self.requests = []

        def send(self, request):
            self.requests.append(request)
            if len(self.requests) > 1:
                raise AssertionError("request started after cancellation")
            cancellation.cancel()
            return {"success": True, "result": {"access_token": "token"}}

        def send_bounded(self, request, limits):
            self.requests.append(request)
            raise AssertionError("bounded request started after cancellation")

    transport = CancelDuringAuthentication()
    client = TuyaClient(
        SETTINGS,
        transport=transport,
        clock_ms=lambda: "1721124000000",
        cancellation=cancellation,
    )

    with pytest.raises(UserFacingError, match="anulată"):
        if operation == "devices":
            client.list_devices()
        elif operation == "specification":
            client.get_device_specification("meter-1")
        elif operation == "status":
            client.get_device_status("meter-1")
        else:
            client.get_report_log_page(
                "meter-1",
                "energy",
                0,
                1000,
                last_row_key=None,
                size=99,
                raw_allowance=1000,
                decoded_allowance=1000,
            )

    assert len(transport.requests) == 1
    assert transport.requests[0].url.endswith("/v1.0/token?grant_type=1")


def test_batch_requests_are_chunked_and_partial_results_map_by_device_id() -> None:
    transport = FakeTransport(
        [
            fixture("batch_status_partial.json"),
            {"success": True, "result": []},
        ]
    )
    client = client_with(transport)
    statuses = client.get_devices_status([f"meter-{index}" for index in range(1, 22)])

    assert set(statuses) == {"meter-1", "meter-2"}
    queries = [parse_qs(urlsplit(request.url).query) for request in transport.requests]
    assert len(queries[0]["device_ids"][0].split(",")) == 20
    assert queries[1]["device_ids"] == ["meter-21"]


def test_report_log_request_uses_current_read_only_v21_endpoint_and_exact_query() -> (
    None
):
    class BoundedTransport(FakeTransport):
        def send_bounded(self, request, limits):
            self.requests.append(request)
            return BoundedPayload(
                fixture("report_logs_empty_v21.json"),
                100,
                100,
            )

    transport = BoundedTransport([])
    client = client_with(transport)
    page = client.get_report_log_page(
        "meter/1",
        "forward_energy_total",
        1000,
        2000,
        last_row_key="cursor",
        size=99,
        raw_allowance=1000,
        decoded_allowance=1000,
    )

    request = transport.requests[0]
    split = urlsplit(request.url)
    assert split.path == "/v2.1/cloud/thing/meter%2F1/report-logs"
    assert parse_qs(split.query) == {
        "codes": ["forward_energy_total"],
        "start_time": ["1000"],
        "end_time": ["2000"],
        "last_row_key": ["cursor"],
        "size": ["99"],
    }
    assert page.raw_bytes == 100
    assert page.payload == {"hasMore": False}


def test_report_log_page_removes_sensitive_fields_and_known_values() -> None:
    class BoundedTransport(FakeTransport):
        def send_bounded(self, request, limits):
            self.requests.append(request)
            return BoundedPayload(
                {
                    "success": True,
                    "result": {
                        "hasMore": False,
                        "refreshToken": "remote-token",
                        "diagnostic": "super-secret token-value",
                    },
                },
                100,
                100,
            )

    client = client_with(BoundedTransport([]))
    page = client.get_report_log_page(
        "meter-1",
        "forward_energy_total",
        1000,
        2000,
        last_row_key=None,
        size=99,
        raw_allowance=1000,
        decoded_allowance=1000,
    )

    rendered = repr(page.payload)
    assert "super-secret" not in rendered
    assert "token-value" not in rendered
    assert "remote-token" not in rendered


def test_diagnostics_and_errors_do_not_expose_secret_or_token() -> None:
    failure = TuyaAPIError(
        "failed with super-secret and token-value",
        request_info={"header": "token-value"},
        response_payload={
            "debug": "super-secret token-value",
            "local_key": "device-credential",
        },
    )
    transport = FakeTransport([failure])
    client = client_with(transport)
    with pytest.raises(TuyaAPIError) as caught:
        client.list_devices()

    rendered = repr(
        (str(caught.value), caught.value.request_info, caught.value.response_payload)
    )
    assert "super-secret" not in rendered
    assert "token-value" not in rendered
    assert "device-credential" not in rendered
    assert rendered.count("[REDACTED]") >= 3
    diagnostic = transport.requests[0].diagnostic
    assert "headers" not in diagnostic
