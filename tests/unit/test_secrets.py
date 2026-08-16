from __future__ import annotations

import multiprocessing
import sys
import threading
import time
from pathlib import Path

import pytest

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.secrets import (
    MacOSKeychainSecretStore,
    PlaintextFileSecretStore,
    SecretStoreError,
)


class FakeSecurity:
    kSecClass = "class"
    kSecClassGenericPassword = "generic-password"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecAttrSynchronizable = "synchronizable"
    kSecReturnData = "return-data"
    kSecMatchLimit = "match-limit"
    kSecMatchLimitOne = "one"
    kSecAttrLabel = "label"
    kSecValueData = "value"
    kSecUseAuthenticationUI = "authentication-ui"
    kSecUseAuthenticationUIFail = "fail"
    errSecSuccess = 0
    errSecItemNotFound = -25300
    errSecDuplicateItem = -25299

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], bytes] = {}
        self.last_query: dict[object, object] | None = None
        self.read_status: int | None = None

    def _key(self, query: dict[object, object]) -> tuple[str, str]:
        return (str(query[self.kSecAttrService]), str(query[self.kSecAttrAccount]))

    def SecItemCopyMatching(self, query, result):
        del result
        self.last_query = query
        if self.read_status is not None:
            return self.read_status, None
        value = self.items.get(self._key(query))
        if value is None:
            return self.errSecItemNotFound, None
        return self.errSecSuccess, value

    def SecItemAdd(self, attributes, result):
        del result
        key = self._key(attributes)
        if key in self.items:
            return self.errSecDuplicateItem, None
        self.items[key] = bytes(attributes[self.kSecValueData])
        return self.errSecSuccess, None

    def SecItemUpdate(self, query, attributes):
        key = self._key(query)
        if key not in self.items:
            return self.errSecItemNotFound
        self.items[key] = bytes(attributes[self.kSecValueData])
        return self.errSecSuccess

    def SecItemDelete(self, query):
        if self.items.pop(self._key(query), None) is None:
            return self.errSecItemNotFound
        return self.errSecSuccess


def _blocking_keychain_helper(
    connection, service, operation, account, value, label
) -> None:
    del connection, service, operation, value, label
    time.sleep(30)
    Path(account).write_text("late-write", encoding="utf-8")


def _silent_keychain_helper(
    connection, service, operation, account, value, label
) -> None:
    del service, operation, account, value, label
    connection.close()


def keychain_store(monkeypatch) -> tuple[MacOSKeychainSecretStore, FakeSecurity]:
    security = FakeSecurity()
    monkeypatch.setitem(sys.modules, "Security", security)
    return (
        MacOSKeychainSecretStore(
            "ro.tatatuya.test", _use_helper_process=False
        ),
        security,
    )


def test_keychain_add_read_update_and_delete(monkeypatch) -> None:
    store, security = keychain_store(monkeypatch)

    assert store.get("client-secret") is None
    store.set("client-secret", b"first", label="Test secret")
    assert store.get("client-secret") == b"first"
    store.set("client-secret", b"second", label="Test secret")
    assert store.get("client-secret") == b"second"
    store.delete("client-secret")
    store.delete("client-secret")
    assert store.get("client-secret") is None
    assert security.last_query is not None
    assert security.last_query[security.kSecAttrSynchronizable] is False
    assert (
        security.last_query[security.kSecUseAuthenticationUI]
        == security.kSecUseAuthenticationUIFail
    )


def test_keychain_set_if_absent_keeps_concurrent_winner(monkeypatch) -> None:
    store, security = keychain_store(monkeypatch)
    security.items[("ro.tatatuya.test", "database-key")] = b"winner"

    assert (
        store.set_if_absent("database-key", b"loser", label="Database key")
        == b"winner"
    )
    assert security.items[("ro.tatatuya.test", "database-key")] == b"winner"


def test_plaintext_store_is_persistent_restrictive_and_symlink_safe(tmp_path) -> None:
    parent = tmp_path / "data"
    path = parent / "tuya-client-secret.plaintext"
    store = PlaintextFileSecretStore(path)
    store.set("tuya-client-secret-v1", b"development-secret", label="ignored")

    restarted = PlaintextFileSecretStore(path)
    assert restarted.get("tuya-client-secret-v1") == b"development-secret"
    assert parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600

    path.unlink()
    target = tmp_path / "unrelated"
    target.write_bytes(b"keep")
    path.symlink_to(target)
    with pytest.raises(SecretStoreError):
        restarted.get("tuya-client-secret-v1")
    assert target.read_bytes() == b"keep"


def test_keychain_error_does_not_expose_secret(monkeypatch) -> None:
    store, security = keychain_store(monkeypatch)
    security.read_status = -34018

    with pytest.raises(SecretStoreError) as caught:
        store.set("client-secret", b"never-render-this", label="Test secret")

    rendered = repr(caught.value)
    assert "never-render-this" not in rendered
    assert caught.value.operation == "read"
    assert caught.value.status == -34018


@pytest.mark.parametrize("status", [-128, -25293, -25308])
def test_keychain_preserves_distinct_security_statuses(monkeypatch, status) -> None:
    store, security = keychain_store(monkeypatch)
    security.read_status = status

    with pytest.raises(SecretStoreError) as caught:
        store.get("client-secret")

    assert caught.value.operation == "read"
    assert caught.value.status == status


def test_keychain_duplicate_race_is_a_checked_error(monkeypatch) -> None:
    store, security = keychain_store(monkeypatch)
    security.items[("ro.tatatuya.test", "client-secret")] = b"existing"
    monkeypatch.setattr(
        security,
        "SecItemCopyMatching",
        lambda query, result: (security.errSecItemNotFound, None),
    )

    with pytest.raises(SecretStoreError) as caught:
        store.set("client-secret", b"replacement", label="Test secret")

    assert caught.value.operation == "add"
    assert caught.value.status == security.errSecDuplicateItem


def test_keychain_rejects_corrupt_non_bytes_result(monkeypatch) -> None:
    store, security = keychain_store(monkeypatch)
    monkeypatch.setattr(
        security,
        "SecItemCopyMatching",
        lambda query, result: (security.errSecSuccess, object()),
    )

    with pytest.raises(SecretStoreError) as caught:
        store.get("client-secret")

    assert caught.value.operation == "decode"


def test_isolated_keychain_helper_exit_is_a_safe_error() -> None:
    store = MacOSKeychainSecretStore(
        "ro.tatatuya.app.test.silent",
        _use_helper_process=True,
        _helper_target=_silent_keychain_helper,
    )

    with pytest.raises(SecretStoreError) as caught:
        store.get("client-secret")

    assert caught.value.operation == "helper-exit"


def test_pytest_guard_rejects_production_keychain_service() -> None:
    with pytest.raises(AssertionError, match="production macOS Keychain"):
        MacOSKeychainSecretStore()


@pytest.mark.parametrize("operation", ["get", "set", "set_if_absent", "delete"])
def test_isolated_keychain_operations_are_killed_on_cancellation(
    tmp_path, operation
) -> None:
    marker = tmp_path / f"{operation}.late"
    cancellation = CancellationContext(10)
    store = MacOSKeychainSecretStore(
        "ro.tatatuya.app.test.blocking",
        _use_helper_process=True,
        _helper_target=_blocking_keychain_helper,
    )
    timer = threading.Timer(0.1, cancellation.cancel)
    children_before = {child.pid for child in multiprocessing.active_children()}
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(UserFacingError, match="anulată"):
            if operation == "get":
                store.get(str(marker), cancellation)
            elif operation == "set":
                store.set(
                    str(marker),
                    b"secret",
                    label="Test",
                    cancellation=cancellation,
                )
            elif operation == "set_if_absent":
                store.set_if_absent(
                    str(marker),
                    b"secret",
                    label="Test",
                    cancellation=cancellation,
                )
            else:
                store.delete(str(marker), cancellation)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 2
    assert not marker.exists()
    assert {
        child.pid for child in multiprocessing.active_children()
    } <= children_before
