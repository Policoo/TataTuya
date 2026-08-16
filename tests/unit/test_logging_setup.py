from __future__ import annotations

import logging

import pytest

from tatatuya.infrastructure.logging_setup import LOGGER_NAME, configure_logging


def _remove_handler_for(path) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in tuple(logger.handlers):
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(path):
            logger.removeHandler(handler)
            handler.close()


def test_log_directory_and_file_use_restrictive_modes(tmp_path) -> None:
    path = tmp_path / "application-data" / "tatatuya.log"
    try:
        assert configure_logging(path) == path
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        _remove_handler_for(path)


def test_logging_rejects_symlink_without_changing_target(tmp_path) -> None:
    target = tmp_path / "unrelated.txt"
    target.write_text("keep-me", encoding="utf-8")
    path = tmp_path / "tatatuya.log"
    path.symlink_to(target)

    with pytest.raises(RuntimeError, match="unsafe"):
        configure_logging(path)

    assert target.read_text(encoding="utf-8") == "keep-me"
    assert path.is_symlink()
