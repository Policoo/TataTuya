from zoneinfo import ZoneInfo

import pytest

from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.system_timezone import load_system_timezone


def test_system_timezone_prefers_explicit_iana_environment(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    assert load_system_timezone() == ZoneInfo("Europe/Amsterdam")


def test_system_timezone_rejects_missing_stable_identity(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "not/a-zone")
    monkeypatch.setattr(
        "tatatuya.infrastructure.system_timezone.Path.resolve",
        lambda self, strict: self,
    )
    with pytest.raises(UserFacingError, match="Fusul orar"):
        load_system_timezone()
