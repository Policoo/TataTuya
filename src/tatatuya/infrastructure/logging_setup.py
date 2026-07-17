"""Local logging that never records dynamic exception messages."""

from __future__ import annotations

import logging
from pathlib import Path

from tatatuya.paths import application_data_dir


LOGGER_NAME = "tatatuya"


def configure_logging(log_path: Path | None = None) -> Path:
    path = log_path or (application_data_dir() / "tatatuya.log")
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return path
