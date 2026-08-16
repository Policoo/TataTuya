"""Local logging that never records dynamic exception messages."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from tatatuya.paths import application_data_dir


LOGGER_NAME = "tatatuya"


def configure_logging(log_path: Path | None = None) -> Path:
    if os.name != "posix":
        raise RuntimeError(
            "TataTuya development logging is supported only on POSIX systems"
        )
    path = log_path or (application_data_dir() / "tatatuya.log")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_metadata = path.parent.lstat()
    if not stat.S_ISDIR(parent_metadata.st_mode) or parent_metadata.st_uid != os.getuid():
        raise RuntimeError("Log directory is unsafe")
    os.chmod(path.parent, 0o700, follow_symlinks=False)
    if os.path.lexists(path):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("Log path is unsafe")
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    resolved = path.resolve()
    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename).resolve() == resolved
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    os.chmod(path, 0o600, follow_symlinks=False)
    return path
