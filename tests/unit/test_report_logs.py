from decimal import Decimal
from itertools import count

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.tuya.client import BoundedPayload
from tatatuya.infrastructure.tuya.client import TuyaAPIError
from tatatuya.infrastructure.tuya.report_logs import (
    MAX_CANONICAL_RAW_DECIMAL_CHARACTERS,
    TuyaReportLogGateway,
    parse_report_decimal,
)
from tatatuya.domain.energy import canonical_decimal
from tatatuya.services.cancellation import CancellationContext


class PageClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get_report_log_page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return BoundedPayload(self.pages.pop(0), 100, 100)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeCancellation(CancellationContext):
    def __init__(
        self,
        clock: FakeClock,
        *,
        timeout_seconds: float = 30,
        cancel_during_wait: bool = False,
    ) -> None:
        super().__init__(timeout_seconds, monotonic=clock)
        self.clock = clock
        self.cancel_during_wait = cancel_during_wait

    def wait(self, seconds: float) -> None:
        self.checkpoint()
        self.clock.advance(seconds)
        if self.cancel_during_wait:
            self.cancel()
        self.checkpoint()


class TimedPageClient:
    def __init__(self, responses, clock: FakeClock, durations=None) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.durations = list(durations or [0.0] * len(self.responses))
        self.starts: list[float] = []

    def get_report_log_page(self, *args, **kwargs):
        self.starts.append(self.clock())
        response = self.responses.pop(0)
        self.clock.advance(self.durations.pop(0))
        if isinstance(response, Exception):
            raise response
        return BoundedPayload(response, 100, 100)


def test_report_pages_are_validated_deduplicated_and_sorted() -> None:
    client = PageClient(
        [
            {
                "device_id": "meter-1",
                "total": Decimal("2"),
                "has_more": False,
                "logs": [
                    {
                        "code": "forward_energy_total",
                        "value": Decimal("101.0"),
                        "event_time": Decimal("2000"),
                    },
                    {
                        "event_time": Decimal("1000"),
                        "value": "100.00",
                        "code": "forward_energy_total",
                    },
                    {
                        "code": "forward_energy_total",
                        "value": "100.0",
                        "event_time": Decimal("1000"),
                    },
                ],
            }
        ]
    )
    events = TuyaReportLogGateway(client).list_events(
        "meter-1",
        "forward_energy_total",
        0,
        3000,
        CancellationContext(5),
    )

    assert [event.event_time_ms for event in events] == [1000, 2000]
    assert [event.raw_value for event in events] == [
        Decimal("100.00"),
        Decimal("101.0"),
    ]
    assert client.calls[0][1]["size"] == 99


def test_observed_v21_minimal_empty_result_returns_no_events() -> None:
    client = PageClient([{"hasMore": False}])

    events = TuyaReportLogGateway(client).list_events(
        "meter-1",
        "forward_energy_total",
        0,
        3000,
        CancellationContext(5),
    )

    assert events == ()


def test_camel_case_pagination_and_missing_optional_metadata_are_accepted() -> None:
    client = PageClient(
        [
            {
                "hasMore": True,
                "lastRowKey": "next-page",
                "logs": [
                    {
                        "code": "energy",
                        "value": "1",
                        "event_time": Decimal("1000"),
                    }
                ],
            },
            {
                "hasMore": False,
                "logs": [
                    {
                        "code": "energy",
                        "value": "2",
                        "event_time": Decimal("2000"),
                    }
                ],
            },
        ]
    )
    ticks = count()

    events = TuyaReportLogGateway(
        client, monotonic=lambda: float(next(ticks))
    ).list_events("meter-1", "energy", 0, 3000, CancellationContext(5))

    assert [event.raw_value for event in events] == [Decimal("1"), Decimal("2")]
    assert client.calls[1][1]["last_row_key"] == "next-page"


def test_explicitly_mismatched_response_device_is_still_rejected() -> None:
    client = PageClient([{"deviceId": "meter-2", "hasMore": False}])

    with pytest.raises(UserFacingError, match="altui contor"):
        TuyaReportLogGateway(client).list_events(
            "meter-1", "energy", 0, 3000, CancellationContext(5)
        )


def test_conflicting_pagination_aliases_are_rejected() -> None:
    client = PageClient([{"has_more": False, "hasMore": True}])

    with pytest.raises(UserFacingError, match="contradictorii"):
        TuyaReportLogGateway(client).list_events(
            "meter-1", "energy", 0, 3000, CancellationContext(5)
        )


@pytest.mark.parametrize(
    "value",
    [True, None, {}, [], "-1", "01", "NaN", Decimal("-0")],
)
def test_report_decimal_rejects_ambiguous_or_unsupported_values(value) -> None:
    with pytest.raises(UserFacingError):
        parse_report_decimal(value)


@pytest.mark.parametrize(
    "value",
    [
        "1e-100000",
        "1e100000",
        Decimal("1e-100000"),
        Decimal("1e100000"),
        "1e-" + "9" * 124,
        "1e+" + "9" * 124,
    ],
)
def test_report_decimal_rejects_extreme_exponents_without_fixed_rendering(
    value,
) -> None:
    with pytest.raises(UserFacingError):
        parse_report_decimal(value)


@pytest.mark.parametrize("value", ["1e127", "1e-126"])
def test_report_decimal_accepts_canonical_length_boundary(value) -> None:
    parsed = parse_report_decimal(value)
    assert len(canonical_decimal(parsed)) == MAX_CANONICAL_RAW_DECIMAL_CHARACTERS


def test_equivalent_ordinary_exponents_keep_one_canonical_identity() -> None:
    assert {
        canonical_decimal(parse_report_decimal(value)) for value in (1, "1.0", "1e0")
    } == {"1"}


def test_extreme_exponent_rejects_before_diagnostic_canonicalization(
    monkeypatch,
) -> None:
    client = PageClient(
        [
            {
                "device_id": "meter-1",
                "total": Decimal("1"),
                "has_more": False,
                "logs": [
                    {
                        "code": "energy",
                        "value": Decimal("1e-100000"),
                        "event_time": Decimal("1000"),
                    }
                ],
            }
        ]
    )
    canonical_calls = []
    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.report_logs.canonical_decimal",
        lambda value: canonical_calls.append(value) or "unexpected",
    )

    with pytest.raises(UserFacingError):
        TuyaReportLogGateway(client).list_events(
            "meter-1", "energy", 0, 2000, CancellationContext(5)
        )

    assert canonical_calls == []


def test_extreme_unrelated_decimal_rejects_before_diagnostic_expansion(
    monkeypatch,
) -> None:
    extreme = Decimal("1e-100000")
    client = PageClient(
        [
            {
                "device_id": "meter-1",
                "total": Decimal("1"),
                "has_more": False,
                "logs": [
                    {
                        "code": "energy",
                        "value": "1",
                        "event_time": Decimal("1000"),
                        "extra": extreme,
                    }
                ],
            }
        ]
    )
    rendered = []
    original = canonical_decimal

    def record_canonical(value):
        rendered.append(value)
        return original(value)

    monkeypatch.setattr(
        "tatatuya.infrastructure.tuya.report_logs.canonical_decimal",
        record_canonical,
    )

    with pytest.raises(UserFacingError, match="câmp numeric"):
        TuyaReportLogGateway(client).list_events(
            "meter-1", "energy", 0, 2000, CancellationContext(5)
        )

    assert extreme not in rendered


@pytest.mark.parametrize("field", ["total", "event_time"])
def test_extreme_integer_exponent_rejects_before_integer_conversion(field) -> None:
    row = {
        "code": "energy",
        "value": "1",
        "event_time": Decimal("1000"),
    }
    page = {
        "device_id": "meter-1",
        "total": Decimal("1"),
        "has_more": False,
        "logs": [row],
    }
    if field == "total":
        page["total"] = Decimal("1e100000")
    else:
        row["event_time"] = Decimal("1e100000")

    with pytest.raises(UserFacingError, match="număr întreg"):
        TuyaReportLogGateway(PageClient([page])).list_events(
            "meter-1", "energy", 0, 2000, CancellationContext(5)
        )


def test_same_timestamp_with_different_values_rejects_complete_load() -> None:
    client = PageClient(
        [
            {
                "device_id": "meter-1",
                "total": Decimal("2"),
                "has_more": False,
                "logs": [
                    {"code": "energy", "value": "1", "event_time": Decimal("1000")},
                    {"code": "energy", "value": "2", "event_time": Decimal("1000")},
                ],
            }
        ]
    )
    with pytest.raises(UserFacingError, match="valori diferite"):
        TuyaReportLogGateway(client).list_events(
            "meter-1", "energy", 0, 2000, CancellationContext(5)
        )


def test_multi_page_query_carries_raw_and_decoded_total_allowances() -> None:
    client = PageClient(
        [
            {
                "device_id": "meter-1",
                "total": Decimal("2"),
                "has_more": True,
                "last_row_key": "next-page",
                "logs": [
                    {"code": "energy", "value": "1", "event_time": Decimal("1000")}
                ],
            },
            {
                "device_id": "meter-1",
                "total": Decimal("2"),
                "has_more": False,
                "logs": [
                    {"code": "energy", "value": "2", "event_time": Decimal("2000")}
                ],
            },
        ]
    )
    ticks = count()
    events = TuyaReportLogGateway(
        client, monotonic=lambda: float(next(ticks))
    ).list_events("meter-1", "energy", 0, 3000, CancellationContext(5))

    assert len(events) == 2
    first = client.calls[0][1]
    second = client.calls[1][1]
    assert first["raw_allowance"] == 10_485_760
    assert second["raw_allowance"] == 10_485_660
    assert second["decoded_allowance"] == 10_485_660
    assert second["last_row_key"] == "next-page"


def _page(*, has_more: bool, cursor: str | None = None):
    page = {
        "device_id": "meter-1",
        "total": Decimal("0"),
        "has_more": has_more,
        "logs": [],
    }
    if cursor is not None:
        page["last_row_key"] = cursor
    if has_more:
        page["logs"] = [{"code": "energy", "value": "1", "event_time": Decimal("1000")}]
        page["total"] = Decimal("1")
    return page


def _rate_limit() -> TuyaAPIError:
    return TuyaAPIError("rate limited", response_payload={"code": 429})


def test_retry_and_following_page_pace_every_physical_request() -> None:
    clock = FakeClock()
    first_page = _page(has_more=True, cursor="next")
    second_page = _page(has_more=False)
    second_page["total"] = Decimal("1")
    client = TimedPageClient([_rate_limit(), first_page, second_page], clock)

    TuyaReportLogGateway(client, monotonic=clock).list_events(
        "meter-1", "energy", 0, 2000, FakeCancellation(clock)
    )

    assert client.starts == [0.0, 0.5, 0.75]


def test_multiple_retries_keep_backoff_and_physical_request_pacing() -> None:
    clock = FakeClock()
    client = TimedPageClient(
        [_rate_limit(), _rate_limit(), _page(has_more=False)], clock
    )

    TuyaReportLogGateway(client, monotonic=clock).list_events(
        "meter-1", "energy", 0, 2000, FakeCancellation(clock)
    )

    assert client.starts == [0.0, 0.5, 1.5]


def test_slow_response_needs_no_extra_pacing_wait() -> None:
    clock = FakeClock()
    first_page = _page(has_more=True, cursor="next")
    second_page = _page(has_more=False)
    second_page["total"] = Decimal("1")
    client = TimedPageClient([first_page, second_page], clock, [0.4, 0.0])

    TuyaReportLogGateway(client, monotonic=clock).list_events(
        "meter-1", "energy", 0, 2000, FakeCancellation(clock)
    )

    assert client.starts == [0.0, 0.4]


def test_cancellation_during_pacing_wait_starts_no_next_request() -> None:
    clock = FakeClock()
    client = TimedPageClient([_page(has_more=True, cursor="next")], clock)

    with pytest.raises(UserFacingError, match="anulată"):
        TuyaReportLogGateway(client, monotonic=clock).list_events(
            "meter-1",
            "energy",
            0,
            2000,
            FakeCancellation(clock, cancel_during_wait=True),
        )

    assert client.starts == [0.0]


def test_deadline_expiry_after_response_starts_no_next_request() -> None:
    clock = FakeClock()
    client = TimedPageClient(
        [_page(has_more=True, cursor="next")],
        clock,
        [2.0],
    )

    with pytest.raises(UserFacingError, match="durat prea mult"):
        TuyaReportLogGateway(client, monotonic=clock).list_events(
            "meter-1",
            "energy",
            0,
            2000,
            FakeCancellation(clock, timeout_seconds=1),
        )

    assert client.starts == [0.0]
