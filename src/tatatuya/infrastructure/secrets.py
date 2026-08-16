"""Secret storage adapters for macOS Keychain and local development."""

from __future__ import annotations

import multiprocessing
import os
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tatatuya.domain.cancellation import CancellationContext


KEYCHAIN_SERVICE = "ro.tatatuya.app"
TUYA_CLIENT_SECRET_ACCOUNT = "tuya-client-secret-v1"
DATABASE_KEY_ACCOUNT = "database-key-v1"


class SecretStoreError(RuntimeError):
    """A safe credential-storage failure that never embeds secret material."""

    def __init__(self, operation: str, status: int | None = None) -> None:
        marker = "unavailable" if status is None else str(status)
        super().__init__(f"Keychain {operation} failed (OSStatus {marker})")
        self.operation = operation
        self.status = status


class SecretStore(Protocol):
    def get(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> bytes | None: ...
    def set(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> None: ...
    def set_if_absent(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> bytes: ...
    def delete(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> None: ...


@dataclass(slots=True)
class MemorySecretStore:
    """Explicitly injected fake used by isolated automated tests."""

    values: dict[str, bytes] = field(default_factory=dict)

    def get(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> bytes | None:
        _checkpoint(cancellation)
        value = self.values.get(account)
        return None if value is None else bytes(value)

    def set(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> None:
        del label
        _checkpoint(cancellation)
        self.values[account] = bytes(value)

    def set_if_absent(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> bytes:
        del label
        _checkpoint(cancellation)
        winner = self.values.setdefault(account, bytes(value))
        return bytes(winner)

    def delete(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> None:
        _checkpoint(cancellation)
        self.values.pop(account, None)


class PlaintextFileSecretStore:
    """Persistent plaintext secret storage for POSIX development outside macOS."""

    def __init__(self, path: Path, *, account: str = TUYA_CLIENT_SECRET_ACCOUNT) -> None:
        self.path = path
        self.account = account

    def get(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> bytes | None:
        _checkpoint(cancellation)
        self._check_account(account)
        self._secure_parent()
        if not os.path.lexists(self.path):
            return None
        descriptor = self._open_existing(os.O_RDONLY)
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)

    def set(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> None:
        del label
        _checkpoint(cancellation)
        self._check_account(account)
        self._secure_parent()
        if os.path.lexists(self.path):
            descriptor = self._open_existing(os.O_RDONLY)
            os.close(descriptor)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(bytes(value))
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("Could not write plaintext secret")
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            if os.path.lexists(self.path):
                existing = self._open_existing(os.O_RDONLY)
                os.close(existing)
            os.replace(temporary, self.path)
            self._fsync_parent()
        except OSError as exc:
            raise SecretStoreError("plaintext-write") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if os.path.lexists(temporary):
                temporary.unlink()

    def set_if_absent(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> bytes:
        _checkpoint(cancellation)
        self._check_account(account)
        existing = self.get(account, cancellation)
        if existing is not None:
            return existing
        self.set(account, value, label=label, cancellation=cancellation)
        winner = self.get(account, cancellation)
        if winner is None:
            raise SecretStoreError("plaintext-round-trip")
        return winner

    def delete(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> None:
        _checkpoint(cancellation)
        self._check_account(account)
        self._secure_parent()
        if not os.path.lexists(self.path):
            return
        descriptor = self._open_existing(os.O_RDONLY)
        os.close(descriptor)
        self.path.unlink()
        self._fsync_parent()

    def _check_account(self, account: str) -> None:
        if account != self.account:
            raise SecretStoreError("plaintext-account")

    def _secure_parent(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = self.path.parent.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise OSError("Unsafe plaintext secret directory")
            os.chmod(self.path.parent, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise SecretStoreError("plaintext-directory") from exc

    def _open_existing(self, flags: int) -> int:
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise OSError("Unsafe plaintext secret file")
            os.fchmod(descriptor, 0o600)
            return descriptor
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise SecretStoreError("plaintext-read") from exc

    def _fsync_parent(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class MacOSKeychainSecretStore:
    """Generic-password storage through Apple's Security framework."""

    def __init__(
        self,
        service: str = KEYCHAIN_SERVICE,
        *,
        _use_helper_process: bool | None = None,
        _helper_target: Any | None = None,
    ) -> None:
        self._security: Any | None = None
        self._service = service
        self._use_helper_process = (
            sys.platform == "darwin"
            if _use_helper_process is None
            else _use_helper_process
        )
        self._helper_target = _helper_target or _keychain_helper

    def _api(self) -> Any:
        if self._security is not None:
            return self._security
        try:
            import Security  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SecretStoreError("framework-load") from exc
        self._security = Security
        return Security

    def _query(self, account: str) -> dict[object, object]:
        security = self._api()
        query = {
            security.kSecClass: security.kSecClassGenericPassword,
            security.kSecAttrService: self._service,
            security.kSecAttrAccount: account,
            security.kSecAttrSynchronizable: False,
        }
        authentication_ui = getattr(security, "kSecUseAuthenticationUI", None)
        authentication_ui_fail = getattr(
            security, "kSecUseAuthenticationUIFail", None
        )
        if authentication_ui is not None and authentication_ui_fail is not None:
            query[authentication_ui] = authentication_ui_fail
        return query

    def get(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> bytes | None:
        if self._use_helper_process:
            result = self._run_isolated("get", account, cancellation=cancellation)
            return None if result is None else bytes(result)
        return self._get_direct(account)

    def _get_direct(self, account: str) -> bytes | None:
        security = self._api()
        query = self._query(account)
        query[security.kSecReturnData] = True
        query[security.kSecMatchLimit] = security.kSecMatchLimitOne
        status, result = security.SecItemCopyMatching(query, None)
        if status == security.errSecItemNotFound:
            return None
        self._check("read", status)
        try:
            return bytes(result)
        except (TypeError, ValueError) as exc:
            raise SecretStoreError("decode") from exc

    def set(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> None:
        if self._use_helper_process:
            self._run_isolated(
                "set",
                account,
                value=value,
                label=label,
                cancellation=cancellation,
            )
            return
        self._set_direct(account, value, label=label)

    def _set_direct(self, account: str, value: bytes, *, label: str) -> None:
        security = self._api()
        query = self._query(account)
        existing = self._get_direct(account)
        if existing is None:
            attributes = dict(query)
            attributes[security.kSecAttrLabel] = label
            attributes[security.kSecValueData] = bytes(value)
            status, _result = security.SecItemAdd(attributes, None)
            self._check("add", status)
        else:
            status = security.SecItemUpdate(
                query,
                {
                    security.kSecAttrLabel: label,
                    security.kSecValueData: bytes(value),
                },
            )
            self._check("update", status)

    def set_if_absent(
        self,
        account: str,
        value: bytes,
        *,
        label: str,
        cancellation: CancellationContext | None = None,
    ) -> bytes:
        if self._use_helper_process:
            result = self._run_isolated(
                "set_if_absent",
                account,
                value=value,
                label=label,
                cancellation=cancellation,
            )
            if result is None:
                raise SecretStoreError("helper-result")
            return bytes(result)
        return self._set_if_absent_direct(account, value, label=label)

    def _set_if_absent_direct(
        self, account: str, value: bytes, *, label: str
    ) -> bytes:
        security = self._api()
        attributes = self._query(account)
        attributes[security.kSecAttrLabel] = label
        attributes[security.kSecValueData] = bytes(value)
        status, _result = security.SecItemAdd(attributes, None)
        duplicate_status = getattr(security, "errSecDuplicateItem", -25299)
        if status == duplicate_status:
            winner = self._get_direct(account)
            if winner is None:
                raise SecretStoreError("read-after-duplicate", int(status))
            return winner
        self._check("add", status)
        return bytes(value)

    def delete(
        self, account: str, cancellation: CancellationContext | None = None
    ) -> None:
        if self._use_helper_process:
            self._run_isolated("delete", account, cancellation=cancellation)
            return
        self._delete_direct(account)

    def _delete_direct(self, account: str) -> None:
        security = self._api()
        status = security.SecItemDelete(self._query(account))
        if status == security.errSecItemNotFound:
            return
        self._check("delete", status)

    def _check(self, operation: str, status: int) -> None:
        if status != self._api().errSecSuccess:
            raise SecretStoreError(operation, int(status))

    def _run_isolated(
        self,
        operation: str,
        account: str,
        *,
        value: bytes | None = None,
        label: str | None = None,
        cancellation: CancellationContext | None = None,
    ) -> bytes | None:
        """Run one Security-framework call in a killable helper process."""

        _checkpoint(cancellation)
        receive, send = multiprocessing.get_context("spawn").Pipe(duplex=False)
        process = multiprocessing.get_context("spawn").Process(
            target=self._helper_target,
            args=(send, self._service, operation, account, value, label),
            daemon=True,
        )
        deadline = time.monotonic() + min(
            5.0,
            cancellation.remaining_seconds() if cancellation is not None else 5.0,
        )
        try:
            process.start()
            send.close()
            while True:
                _checkpoint(cancellation)
                if receive.poll(0.025):
                    try:
                        kind, payload = receive.recv()
                    except EOFError as exc:
                        raise SecretStoreError(
                            "helper-exit", process.exitcode
                        ) from exc
                    process.join(timeout=0.25)
                    if kind == "ok":
                        return payload
                    error_operation, status = payload
                    raise SecretStoreError(error_operation, status)
                if not process.is_alive():
                    raise SecretStoreError("helper-exit", process.exitcode)
                if time.monotonic() >= deadline:
                    _checkpoint(cancellation)
                    raise SecretStoreError("timeout")
        finally:
            send.close()
            receive.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)


def _checkpoint(cancellation: CancellationContext | None) -> None:
    if cancellation is not None:
        cancellation.checkpoint()


def _keychain_helper(
    connection: Any,
    service: str,
    operation: str,
    account: str,
    value: bytes | None,
    label: str | None,
) -> None:
    """Execute one direct Keychain operation and return only bounded data."""

    try:
        store = MacOSKeychainSecretStore(service, _use_helper_process=False)
        if operation == "get":
            result = store._get_direct(account)
        elif operation == "set":
            if value is None or label is None:
                raise SecretStoreError("helper-request")
            store._set_direct(account, value, label=label)
            result = None
        elif operation == "set_if_absent":
            if value is None or label is None:
                raise SecretStoreError("helper-request")
            result = store._set_if_absent_direct(account, value, label=label)
        elif operation == "delete":
            store._delete_direct(account)
            result = None
        else:
            raise SecretStoreError("helper-request")
        connection.send(("ok", result))
    except SecretStoreError as exc:
        connection.send(("error", (exc.operation, exc.status)))
    except BaseException:
        connection.send(("error", ("helper-failure", None)))
    finally:
        connection.close()
