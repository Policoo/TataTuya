"""Filesystem locations used by the application."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_NAME = "TataTuya"
DATABASE_FILENAME = "tatatuya.sqlite3"


def application_data_dir(override: str | Path | None = None) -> Path:
    """Return the writable app-data directory, without creating it."""
    if override is not None:
        return Path(override).expanduser()

    environment_override = os.environ.get("TATATUYA_DATA_DIR")
    if environment_override:
        return Path(environment_override).expanduser()

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIRECTORY_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / APP_DIRECTORY_NAME
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_DIRECTORY_NAME


def database_path(override: str | Path | None = None) -> Path:
    return application_data_dir(override) / DATABASE_FILENAME

