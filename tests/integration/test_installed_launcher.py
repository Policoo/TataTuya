from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_installed_command_runs_outside_checkout(tmp_path) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    data_directory = tmp_path / "application-data"
    environment["TATATUYA_DATA_DIR"] = str(data_directory)
    command = Path(sys.executable).with_name("tatatuya")
    assert command.is_file(), "Install the project with `pip install -e .` before testing"
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
    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [(1,), (2,), (3,)]
