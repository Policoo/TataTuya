"""Shared conversion helpers for SQLite repositories."""

from __future__ import annotations

from datetime import UTC, datetime


def to_utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Persisted timestamps must include a timezone")
    return value.astimezone(UTC).isoformat()


def from_utc_text(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

