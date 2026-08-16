"""Shared Qt lifecycle and macOS credential-isolation fixtures."""

from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """Create the one widget-capable Qt singleton before any transport test."""

    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    application = (
        existing if isinstance(existing, QApplication) else QApplication([])
    )
    yield application
    application.processEvents()


@pytest.fixture(autouse=True)
def isolated_test_keychain(monkeypatch):
    """Prevent ordinary pytest code from touching production Keychain items."""

    from tatatuya.infrastructure import database as database_module
    from tatatuya.infrastructure.secrets import (
        KEYCHAIN_SERVICE,
        MacOSKeychainSecretStore,
        MemorySecretStore,
    )

    original_init = MacOSKeychainSecretStore.__init__
    memory_store = MemorySecretStore()

    def guarded_init(self, service=KEYCHAIN_SERVICE, **kwargs):
        if service == KEYCHAIN_SERVICE:
            raise AssertionError(
                "Tests must not use the production macOS Keychain service"
            )
        original_init(self, service, **kwargs)

    monkeypatch.setattr(MacOSKeychainSecretStore, "__init__", guarded_init)
    monkeypatch.setattr(
        database_module,
        "MacOSKeychainSecretStore",
        lambda: memory_store,
    )
