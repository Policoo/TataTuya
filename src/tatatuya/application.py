"""Application entry point kept compatible with the prototype UI."""

from __future__ import annotations

import sys
import os
import multiprocessing


def _disposable_smoke_keychain_service() -> str | None:
    service = os.environ.get("TATATUYA_SMOKE_KEYCHAIN_SERVICE")
    if sys.platform != "darwin":
        return service
    allowed_prefixes = (
        "ro.tatatuya.app.test.",
        "ro.tatatuya.app.ci-smoke.",
    )
    if service is None or not any(
        service.startswith(prefix) and len(service) > len(prefix)
        for prefix in allowed_prefixes
    ):
        raise RuntimeError(
            "The macOS smoke test requires a disposable Keychain service"
        )
    return service


def run() -> None:
    multiprocessing.freeze_support()
    if sys.platform != "darwin" and os.name != "posix":
        raise SystemExit(
            "TataTuya development is supported only on POSIX systems; the distributed application targets macOS."
        )
    # Used by packaging tests to exercise the installed entry point without
    # opening a window or making a Tuya request.
    if "--smoke-test" in sys.argv:
        from PySide6.QtWidgets import QApplication

        from tatatuya.infrastructure.database import Database
        from tatatuya.infrastructure.secrets import MacOSKeychainSecretStore
        from tatatuya.ui.app import load_stylesheet

        stylesheet = load_stylesheet()
        if not stylesheet.strip():
            raise RuntimeError("The packaged stylesheet is empty")
        existing = QApplication.instance()
        app = existing if isinstance(existing, QApplication) else QApplication([])
        app.setStyleSheet(stylesheet)
        app.processEvents()
        smoke_service = _disposable_smoke_keychain_service()
        secret_store = (
            MacOSKeychainSecretStore(smoke_service) if smoke_service else None
        )
        sentinel = os.environ.get("TATATUYA_SMOKE_SENTINEL")
        if not sentinel:
            raise RuntimeError("The packaged smoke-test sentinel is missing")
        database = Database(secret_store=secret_store)
        database.initialize()
        with database.connect() as connection:
            cipher_version = connection.execute("PRAGMA cipher_version").fetchone()
            if database.require_cipher and (
                cipher_version is None or not cipher_version[0]
            ):
                raise RuntimeError("The packaged SQLCipher runtime is unavailable")
            existing_probe = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("smoke.encryption_probe",),
            ).fetchone()
            if existing_probe is None:
                connection.execute(
                    "INSERT INTO settings(key, value, updated_at_utc) VALUES (?, ?, ?)",
                    (
                        "smoke.encryption_probe",
                        sentinel,
                        "2000-01-01T00:00:00+00:00",
                    ),
                )
            elif str(existing_probe[0]) != sentinel:
                raise RuntimeError("The packaged database restart probe changed")
        app.quit()
        return

    if "--smoke-test-clean-keychain" in sys.argv:
        from tatatuya.infrastructure.secrets import (
            DATABASE_KEY_ACCOUNT,
            TUYA_CLIENT_SECRET_ACCOUNT,
            MacOSKeychainSecretStore,
        )

        smoke_service = _disposable_smoke_keychain_service()
        if not smoke_service:
            raise RuntimeError("The disposable smoke-test Keychain service is missing")
        store = MacOSKeychainSecretStore(smoke_service)
        store.delete(DATABASE_KEY_ACCOUNT)
        store.delete(TUYA_CLIENT_SECRET_ACCOUNT)
        return

    if "--smoke-test-assert-clean-keychain" in sys.argv:
        from tatatuya.infrastructure.secrets import (
            DATABASE_KEY_ACCOUNT,
            TUYA_CLIENT_SECRET_ACCOUNT,
            MacOSKeychainSecretStore,
        )

        smoke_service = _disposable_smoke_keychain_service()
        if not smoke_service:
            raise RuntimeError("The disposable smoke-test Keychain service is missing")
        store = MacOSKeychainSecretStore(smoke_service)
        if (
            store.get(DATABASE_KEY_ACCOUNT) is not None
            or store.get(TUYA_CLIENT_SECRET_ACCOUNT) is not None
        ):
            raise RuntimeError("Disposable smoke-test Keychain cleanup failed")
        return

    from tatatuya.infrastructure.logging_setup import configure_logging

    configure_logging()

    # Keeping this import lazy lets domain and persistence tools run without Qt.
    from tatatuya.ui.app import run as run_legacy_ui

    run_legacy_ui()
