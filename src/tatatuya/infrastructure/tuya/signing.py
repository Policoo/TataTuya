"""Deterministic Tuya request canonicalization and signing."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit


def json_bytes(body: Any | None) -> bytes:
    if body is None:
        return b""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_path(
    path: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    """Return the path and a stable, URL-encoded query string."""
    parsed = urlsplit(path)
    pairs: list[tuple[str, str]] = list(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            rendered = ",".join(str(item) for item in value)
        else:
            rendered = str(value)
        pairs.append((str(key), rendered))
    pairs.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(pairs)
    return parsed.path + (f"?{query}" if query else "")


@dataclass(frozen=True, slots=True)
class RequestSigner:
    client_id: str
    client_secret: str

    def sign(
        self,
        method: str,
        path: str,
        timestamp_ms: str,
        body: bytes = b"",
        access_token: str | None = None,
    ) -> str:
        content_hash = hashlib.sha256(body).hexdigest()
        string_to_sign = f"{method.upper()}\n{content_hash}\n\n{path}"
        token = access_token or ""
        payload = f"{self.client_id}{token}{timestamp_ms}{string_to_sign}"
        return hmac.new(
            self.client_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
