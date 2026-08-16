"""The DB-API module used by repositories and migrations."""

from __future__ import annotations

from importlib import import_module
import sys
from typing import Any

dbapi: Any
try:
    dbapi = import_module("sqlcipher3.dbapi2")
except ImportError:
    if sys.platform == "darwin":  # pragma: no cover - production fails closed
        raise
    dbapi = import_module("sqlite3")


Connection = dbapi.Connection
Row = dbapi.Row
