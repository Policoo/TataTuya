import json
from decimal import Decimal
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from tatatuya.domain.models import Currency, TuyaSettings
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.tuya.client import (
    BodyLimits,
    BoundedPayload,
    PreparedRequest,
    TuyaAPIError,
    TuyaClient,
    UrllibTransport,
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
        "tatatuya.infrastructure.tuya.client.urlopen",
        lambda request, timeout: Response(),
    )
    payload = UrllibTransport().send(
        PreparedRequest("GET", "https://example.test", {}, b"", {})
    )
    value = payload["result"][0]["value"]
    assert value == Decimal("0.12345678901234567890123456789")
    assert isinstance(value, Decimal)


def test_http_json_error_body_is_structured_and_redacted(monkeypatch) -> None:
    body = json.dumps(
        {
            "msg": "request failed",
            "local_key": "device-credential",
            "measurement": 0.12345678901234567890123456789,
        }
    ).encode()

    def fail(request, timeout):
        raise HTTPError(request.full_url, 400, "Bad Request", Message(), BytesIO(body))

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client.urlopen", fail)
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
        raise HTTPError(request.full_url, 400, "Bad Request", Message(), BytesIO(body))

    def unexpected_format(*args, **kwargs):
        raise AssertionError("fixed rendering must not run")

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client.urlopen", fail)
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
        raise HTTPError(request.full_url, 502, "Bad Gateway", Message(), BytesIO(body))

    monkeypatch.setattr("tatatuya.infrastructure.tuya.client.urlopen", fail)
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
        "tatatuya.infrastructure.tuya.client.urlopen",
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
        "tatatuya.infrastructure.tuya.client.urlopen",
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
        "tatatuya.infrastructure.tuya.client.urlopen",
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
