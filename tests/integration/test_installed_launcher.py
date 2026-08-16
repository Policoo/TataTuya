from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest


@pytest.mark.macos_keychain
def test_installed_command_runs_outside_checkout(tmp_path) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    data_directory = tmp_path / "application-data"
    environment["TATATUYA_DATA_DIR"] = str(data_directory)
    environment["TATATUYA_SMOKE_SENTINEL"] = "synthetic-installed-probe"
    smoke_service = f"ro.tatatuya.app.test.{uuid.uuid4().hex}"
    if sys.platform == "darwin":
        environment["TATATUYA_SMOKE_KEYCHAIN_SERVICE"] = smoke_service
    command = Path(sys.executable).with_name("tatatuya")
    assert command.is_file(), "Install the project with `pip install --no-deps .` before testing"
    try:
        result = subprocess.run(
            [str(command), "--smoke-test"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        database_path = data_directory / "tatatuya.sqlite3"
        assert database_path.is_file()
        if sys.platform == "darwin":
            from tatatuya.infrastructure.dbapi import dbapi as sqlcipher
            from tatatuya.infrastructure.secrets import (
                DATABASE_KEY_ACCOUNT,
                MacOSKeychainSecretStore,
            )

            key = MacOSKeychainSecretStore(smoke_service).get(DATABASE_KEY_ACCOUNT)
            assert key is not None
            connection = sqlcipher.connect(database_path)
            try:
                connection.execute(f'PRAGMA key = "x\'{key.hex()}\'"')
                versions = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            finally:
                connection.close()
            with closing(sqlite3.connect(database_path)) as plaintext:
                with pytest.raises(sqlite3.DatabaseError):
                    plaintext.execute("SELECT name FROM sqlite_master").fetchall()
        else:
            with closing(sqlite3.connect(database_path)) as connection:
                versions = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
        assert versions == [(1,), (2,), (3,), (4,)]
    finally:
        if sys.platform == "darwin":
            cleanup = subprocess.run(
                [str(command), "--smoke-test-clean-keychain"],
                cwd=tmp_path,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert cleanup.returncode == 0, cleanup.stderr
            from tatatuya.infrastructure.secrets import (
                DATABASE_KEY_ACCOUNT,
                TUYA_CLIENT_SECRET_ACCOUNT,
                MacOSKeychainSecretStore,
            )

            cleaned = MacOSKeychainSecretStore(smoke_service)
            assert cleaned.get(DATABASE_KEY_ACCOUNT) is None
            assert cleaned.get(TUYA_CLIENT_SECRET_ACCOUNT) is None
