"""Resolve the operating-system IANA timezone for auditable cloud buckets."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tatatuya.domain.errors import UserFacingError


def load_system_timezone() -> ZoneInfo:
    candidates: list[str] = []
    configured = os.environ.get("TZ", "").removeprefix(":").strip()
    if configured:
        candidates.append(configured)
    try:
        resolved = str(Path("/etc/localtime").resolve(strict=True))
    except OSError:
        resolved = ""
    marker = "/zoneinfo/"
    if marker in resolved:
        candidates.append(resolved.split(marker, 1)[1])
    for candidate in candidates:
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    raise UserFacingError(
        "Fus orar indisponibil",
        "Fusul orar al sistemului nu a putut fi identificat. Configurați un fus orar regional în macOS și încercați din nou.",
    )
