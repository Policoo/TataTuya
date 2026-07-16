from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_installed_command_runs_outside_checkout(tmp_path) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
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
