import importlib.util
from decimal import Decimal
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "inspect_tuya.py"
SPEC = importlib.util.spec_from_file_location("tatatuya_test_inspector", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
INSPECTOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = INSPECTOR
SPEC.loader.exec_module(INSPECTOR)

Capture = INSPECTOR.Capture
_capture_text = INSPECTOR._capture_text
_configure_locale = INSPECTOR._configure_locale
_render_json = INSPECTOR._render_json
_sanitize_payload = INSPECTOR._sanitize_payload
_validate_read_only_path = INSPECTOR._validate_read_only_path


@pytest.mark.parametrize(
    "path",
    (
        "/v1.0/iot-01/associated-users/devices",
        "/v1.0/iot-03/devices/status",
        "/v1.0/iot-03/devices/meter-1/specification",
        "/v1.0/iot-03/devices/meter-1/status",
        "/v2.0/cloud/thing/meter-1/report-logs",
        "/v2.1/cloud/thing/meter-1/report-logs",
    ),
)
def test_inspector_allowlists_only_known_read_endpoints(path: str) -> None:
    _validate_read_only_path(path)


@pytest.mark.parametrize(
    "path",
    (
        "/v1.0/devices/meter-1/commands",
        "/v2.0/cloud/thing/meter-1/reset",
        "/v2.0/cloud/thing/meter-1/report-logs/extra",
        "https://example.test/anything",
    ),
)
def test_inspector_rejects_non_allowlisted_endpoints(path: str) -> None:
    with pytest.raises(ValueError, match="allowlist"):
        _validate_read_only_path(path)


def test_inspector_redacts_sensitive_fields_and_dynamic_secrets() -> None:
    payload = {
        "result": {
            "local_key": "device-secret",
            "nested": ["token-value embedded", {"password": "password-value"}],
        }
    }

    sanitized = _sanitize_payload(payload, ("token-value",))

    assert sanitized == {
        "result": {
            "local_key": "[REDACTED]",
            "nested": ["[REDACTED] embedded", {"password": "[REDACTED]"}],
        }
    }


def test_inspector_renders_decimal_as_a_number_not_a_binary_float_or_string() -> None:
    rendered = _render_json({"value": Decimal("123.450"), "count": Decimal("2")})

    assert '"value": 123.450' in rendered
    assert '"count": 2' in rendered
    assert '"123.450"' not in rendered


def test_capture_output_highlights_requested_and_returned_device_ids() -> None:
    output = _capture_text(
        (
            Capture(
                "Report logs v2.1, page 1",
                "https://example.test/report-logs",
                {"success": True, "result": {"device_id": "returned-meter"}},
                100,
                100,
                note=(
                    "Requested device_id: requested-meter\n"
                    "Returned result.device_id: 'returned-meter'"
                ),
            ),
        )
    )

    assert "Requested device_id: requested-meter" in output
    assert "Returned result.device_id: 'returned-meter'" in output
    assert '"device_id": "returned-meter"' in output


def test_locale_configuration_falls_back_when_environment_locale_is_invalid(
    monkeypatch,
) -> None:
    calls = []

    def setlocale(category, candidate):
        calls.append((category, candidate))
        if candidate == "":
            raise INSPECTOR.locale.Error("unsupported locale setting")
        return candidate

    monkeypatch.setattr(INSPECTOR.locale, "setlocale", setlocale)

    assert _configure_locale() == "C.UTF-8"
    assert [candidate for _, candidate in calls] == ["", "C.UTF-8"]


def test_locale_configuration_does_not_block_tui_when_all_candidates_fail(
    monkeypatch,
) -> None:
    def reject_locale(category, candidate):
        raise INSPECTOR.locale.Error("unsupported locale setting")

    monkeypatch.setattr(INSPECTOR.locale, "setlocale", reject_locale)

    assert _configure_locale() is None
