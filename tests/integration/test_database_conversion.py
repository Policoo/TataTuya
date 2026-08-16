from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from typing import Any
import uuid

import pytest

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.database import Database, _PLAINTEXT_HEADER
from tatatuya.infrastructure.secrets import (
    DATABASE_KEY_ACCOUNT,
    TUYA_CLIENT_SECRET_ACCOUNT,
    MacOSKeychainSecretStore,
    MemorySecretStore,
)


_LEGACY_SECRET = b"synthetic-migration-secret"
_PROBE_ROWS = ((1, "alpha"), (2, "beta"), (3, "gamma"))
_ENCRYPTED_HEADER_SIZE = len(_PLAINTEXT_HEADER)
_FAKE_ENCRYPTED_HEADER = bytes(value ^ 0xFF for value in _PLAINTEXT_HEADER)


class InjectedMigrationFailure(RuntimeError):
    pass


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return None if not self._rows else self._rows[0]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeCipherDriver:
    """Small SQLCipher semantic fake that still moves real SQLite files."""

    Row = sqlite3.Row
    Error = sqlite3.Error

    def __init__(self) -> None:
        self._required_keys: dict[tuple[int, int], bytes] = {}
        self.blocked_verification_statement: str | None = None
        self.progress_interruptions = 0

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        metadata = path.stat()
        return (metadata.st_dev, metadata.st_ino)

    @staticmethod
    def _toggle_header(path: Path) -> None:
        with path.open("r+b") as stream:
            header = stream.read(_ENCRYPTED_HEADER_SIZE)
            stream.seek(0)
            stream.write(bytes(value ^ 0xFF for value in header))
            stream.flush()
            os.fsync(stream.fileno())

    def _register_encrypted(self, path: Path, key: bytes) -> None:
        identity = self._identity(path)
        self._required_keys[identity] = key

    @staticmethod
    def _header(path: Path) -> bytes:
        with path.open("rb") as stream:
            return stream.read(_ENCRYPTED_HEADER_SIZE)

    def _encode(self, path: Path) -> None:
        if self._header(path) == _PLAINTEXT_HEADER:
            self._toggle_header(path)

    def is_encrypted(self, path: Path) -> bool:
        if not path.exists():
            return False
        return (
            self._header(path) == _FAKE_ENCRYPTED_HEADER
            and self._identity(path) in self._required_keys
        )

    def connect(self, path: str | Path) -> FakeCipherConnection:
        if str(path) == ":memory:":
            connection = sqlite3.connect(":memory:")
            return FakeCipherConnection(self, None, connection, None, False)

        resolved = Path(path)
        required_key: bytes | None = None
        reencode = False
        if resolved.exists() and resolved.stat().st_size:
            identity = self._identity(resolved)
            if self._header(resolved) == _FAKE_ENCRYPTED_HEADER:
                required_key = self._required_keys.get(identity)
                self._toggle_header(resolved)
                reencode = True
        try:
            connection = sqlite3.connect(resolved)
        except BaseException:
            if reencode:
                self._encode(resolved)
            raise
        return FakeCipherConnection(
            self,
            resolved,
            connection,
            required_key,
            reencode,
        )


class FakeCipherConnection:
    def __init__(
        self,
        driver: FakeCipherDriver,
        path: Path | None,
        connection: sqlite3.Connection,
        required_key: bytes | None,
        reencode: bool,
    ) -> None:
        self.driver = driver
        self.path = path
        self.connection = connection
        self.required_key = required_key
        self.presented_key: bytes | None = None
        self.reencode = reencode
        self.attached: sqlite3.Connection | None = None
        self.attached_path: Path | None = None
        self.attached_key: bytes | None = None
        self.progress_handler: Any | None = None
        self.progress_steps = 0

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self.connection.row_factory = value
        if self.attached is not None:
            self.attached.row_factory = value

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    def _check_key(self) -> None:
        if self.required_key is not None and self.presented_key != self.required_key:
            raise sqlite3.DatabaseError("file is encrypted")

    def execute(self, sql: str, parameters=()):
        statement = " ".join(sql.strip().split())
        key_match = re.fullmatch(
            r"PRAGMA key = \"x'([0-9a-f]+)'\"", statement, re.IGNORECASE
        )
        if key_match:
            self.presented_key = bytes.fromhex(key_match.group(1))
            return _Rows([])
        if statement == "PRAGMA key = ''":
            raise sqlite3.OperationalError("empty SQLCipher keys are unsupported")
        if statement.casefold() == "pragma cipher_version":
            return _Rows([("fake-sqlcipher",)])

        self._check_key()
        if statement == self.driver.blocked_verification_statement:
            if self.progress_handler is None:
                raise AssertionError("blocked verification has no progress handler")
            while not self.progress_handler():
                time.sleep(0.001)
            self.driver.blocked_verification_statement = None
            self.driver.progress_interruptions += 1
            raise sqlite3.OperationalError("interrupted")
        attach_match = re.fullmatch(
            r"ATTACH DATABASE '(.+)' AS encrypted KEY \"x'([0-9a-f]+)'\"",
            statement,
            re.IGNORECASE,
        )
        if attach_match:
            self.attached_path = Path(attach_match.group(1).replace("''", "'"))
            self.attached_key = bytes.fromhex(attach_match.group(2))
            self.attached = sqlite3.connect(self.attached_path)
            self.attached.row_factory = self.connection.row_factory
            if self.progress_handler is not None:
                self.attached.set_progress_handler(
                    self.progress_handler, self.progress_steps
                )
            self.driver._register_encrypted(self.attached_path, self.attached_key)
            return _Rows([])
        if statement.casefold() == "pragma encrypted.journal_mode = off":
            return _Rows([("off",)])
        if statement.casefold() == "select sqlcipher_export('encrypted')":
            if self.attached is None:
                raise sqlite3.OperationalError("encrypted database is not attached")
            self.connection.backup(self.attached)
            return _Rows([(1,)])
        if statement.casefold() == "detach database encrypted":
            self._close_attached()
            return _Rows([])
        if statement.casefold() == "pragma cipher_integrity_check":
            return _Rows([])
        if "encrypted." in statement:
            if self.attached is None:
                raise sqlite3.OperationalError("encrypted database is not attached")
            routed = statement.replace("encrypted.", "")
            return self.attached.execute(routed, parameters)
        return self.connection.execute(sql, parameters)

    def executescript(self, script: str):
        self._check_key()
        return self.connection.executescript(script)

    def set_progress_handler(self, handler, steps: int) -> None:
        self.progress_handler = handler
        self.progress_steps = steps
        self.connection.set_progress_handler(handler, steps)
        if self.attached is not None:
            self.attached.set_progress_handler(handler, steps)

    def commit(self) -> None:
        self.connection.commit()
        if self.attached is not None:
            self.attached.commit()

    def rollback(self) -> None:
        self.connection.rollback()
        if self.attached is not None:
            self.attached.rollback()

    def _close_attached(self) -> None:
        if self.attached is None or self.attached_path is None:
            return
        self.attached.close()
        self.attached = None
        self.driver._encode(self.attached_path)

    def close(self) -> None:
        self._close_attached()
        self.connection.close()
        if self.reencode and self.path is not None and self.path.exists():
            self.driver._encode(self.path)


class FaultInjectingDatabase(Database):
    def __init__(self, *args, fault_stage: str, fault_kind: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fault_stage = fault_stage
        self.fault_kind = fault_kind
        self.reached_stages: list[str] = []

    def _migration_checkpoint(
        self, stage: str, cancellation: CancellationContext | None
    ) -> None:
        self.reached_stages.append(stage)
        if stage == self.fault_stage:
            if self.fault_kind == "cancel":
                if cancellation is None:
                    raise AssertionError("migration cancellation context is missing")
                cancellation.cancel()
            else:
                raise InjectedMigrationFailure(stage)
        super()._migration_checkpoint(stage, cancellation)


def test_fake_cipher_ignores_stale_identity_for_plaintext_file(
    tmp_path, monkeypatch
) -> None:
    driver = FakeCipherDriver()
    reused_identity = (1, 1)
    monkeypatch.setattr(driver, "_identity", lambda path: reused_identity)
    stale = tmp_path / "removed-temporary.sqlite3"
    stale.touch()
    driver._register_encrypted(stale, b"k" * 32)
    driver._encode(stale)
    stale.unlink()

    replacement = tmp_path / "replacement.sqlite3"
    with closing(sqlite3.connect(replacement)) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER NOT NULL)")
        connection.commit()

    connection = driver.connect(replacement)
    try:
        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone() == (0,)
    finally:
        connection.close()
    assert replacement.read_bytes().startswith(_PLAINTEXT_HEADER)


class RecoveryCancellingDatabase(FaultInjectingDatabase):
    def _migration_checkpoint(
        self, stage: str, cancellation: CancellationContext | None
    ) -> None:
        if stage == "recovery-start":
            if cancellation is None:
                raise AssertionError("recovery cancellation context is missing")
            cancellation.cancel()
        super()._migration_checkpoint(stage, cancellation)


class BlockingRecoveryDatabase(FaultInjectingDatabase):
    def __init__(self, *args, blocked_statement: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.blocked_statement = blocked_statement
        self.recovery_context: CancellationContext | None = None

    def _verify_migrated_database(
        self,
        path: Path,
        key: bytes,
        cancellation: CancellationContext | None = None,
        *,
        stage_prefix: str = "verification",
    ) -> None:
        timer: threading.Timer | None = None
        if stage_prefix == "recovery-verification":
            if cancellation is None:
                raise AssertionError("recovery cancellation context is missing")
            if not isinstance(self.driver, FakeCipherDriver):
                raise AssertionError("blocking recovery requires the cipher fake")
            self.recovery_context = cancellation
            self.driver.blocked_verification_statement = self.blocked_statement
            timer = threading.Timer(0.05, cancellation.cancel)
            timer.start()
        try:
            super()._verify_migrated_database(
                path,
                key,
                cancellation,
                stage_prefix=stage_prefix,
            )
        finally:
            if timer is not None:
                timer.cancel()


class FailingRecoveryVerificationDatabase(FaultInjectingDatabase):
    def _migration_checkpoint(
        self, stage: str, cancellation: CancellationContext | None
    ) -> None:
        if stage == "recovery-verification:foreign-key":
            raise InjectedMigrationFailure(stage)
        super()._migration_checkpoint(stage, cancellation)


MIGRATION_STAGES = (
    "legacy-secret-read",
    "legacy-secret-read-complete",
    "legacy-secret-write",
    "legacy-secret-write-complete",
    "legacy-secret-verify",
    "legacy-secret-verify-complete",
    "database-key-read",
    "database-key-read-complete",
    "database-key-create",
    "database-key-create-complete",
    "database-key-verify",
    "database-key-verify-complete",
    "marker-written",
    "source-opened",
    "source-snapshot",
    "export",
    "export-complete",
    "destination-snapshot",
    "temporary-verification:foreign-key",
    "temporary-verification:integrity",
    "temporary-verification:cipher-integrity",
    "temporary-verification:wrong-key",
    "temporary-verification:empty-key",
    "before-source-replace",
    "after-source-replace",
    "before-destination-replace",
    "after-destination-replace",
    "active-verification:foreign-key",
    "active-verification:integrity",
    "active-verification:cipher-integrity",
    "active-verification:wrong-key",
    "active-verification:empty-key",
    "before-rollback-cleanup",
    "after-rollback-cleanup",
    "before-marker-cleanup",
    "after-marker-cleanup",
)


def _create_populated_legacy_database(path: Path) -> dict[str, Any]:
    database = Database(
        path,
        driver=sqlite3,
        secret_store=MemorySecretStore(),
        require_cipher=False,
    )
    database.initialize()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO settings(key, value, updated_at_utc) VALUES (?, ?, ?)",
            ("tuya.client_secret", _LEGACY_SECRET.decode(), "2026-08-16T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO settings(key, value, updated_at_utc) VALUES (?, ?, ?)",
            ("currency", "RON", "2026-08-16T00:00:00+00:00"),
        )
        connection.execute(
            "CREATE TABLE migration_probe(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO migration_probe(id, value) VALUES (?, ?)", _PROBE_ROWS
        )
        connection.commit()
        return Database._database_snapshot(
            connection, "main", exclude_legacy_secret=True
        )


def _assert_one_valid_database(
    path: Path, driver: FakeCipherDriver, key: bytes | None
) -> None:
    candidates = [
        candidate
        for candidate in path.parent.iterdir()
        if candidate == path
        or candidate.name.startswith(".tatatuya-encrypted-")
        or candidate.name.startswith(f".{path.name}.rollback-")
    ]
    valid = 0
    for candidate in candidates:
        if candidate.read_bytes().startswith(_PLAINTEXT_HEADER):
            with closing(sqlite3.connect(candidate)) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM migration_probe"
                ).fetchone() == (len(_PROBE_ROWS),)
            valid += 1
        elif driver.is_encrypted(candidate) and key is not None:
            connection = driver.connect(candidate)
            try:
                Database._apply_key(connection, key)
                assert connection.execute(
                    "SELECT COUNT(*) FROM migration_probe"
                ).fetchone() == (len(_PROBE_ROWS),)
            finally:
                connection.close()
            valid += 1
    assert valid == 1


def _restart_and_assert_converged(
    path: Path,
    driver: FakeCipherDriver,
    secret_store: MemorySecretStore,
    expected_snapshot: dict[str, Any],
) -> None:
    restarted = Database(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
    )
    restarted.initialize(CancellationContext(5))

    assert not path.read_bytes().startswith(_PLAINTEXT_HEADER)
    assert secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT) == _LEGACY_SECRET
    key = secret_store.get(DATABASE_KEY_ACCOUNT)
    assert key is not None
    with restarted.connect() as connection:
        rows = connection.execute(
            "SELECT value FROM migration_probe ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("alpha",),
            ("beta",),
            ("gamma",),
        ]
        assert connection.execute(
            "SELECT 1 FROM settings WHERE key = 'tuya.client_secret'"
        ).fetchone() is None
        actual_snapshot = Database._database_snapshot(connection, "main")
    assert actual_snapshot == expected_snapshot
    assert path.stat().st_mode & 0o777 == 0o600
    assert not restarted._migration_marker_path().exists()
    assert not list(path.parent.glob(".tatatuya-encrypted-*.tmp"))
    assert not list(path.parent.glob(f".{path.name}.rollback-*"))


@pytest.mark.parametrize("fault_kind", ["cancel", "exception"])
@pytest.mark.parametrize("fault_stage", MIGRATION_STAGES)
def test_conversion_faults_preserve_one_database_and_restart(
    tmp_path, fault_stage, fault_kind
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = FaultInjectingDatabase(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
        fault_stage=fault_stage,
        fault_kind=fault_kind,
    )
    cancellation = CancellationContext(5)
    started = time.monotonic()

    expected_error = UserFacingError if fault_kind == "cancel" else InjectedMigrationFailure
    with pytest.raises(expected_error):
        database._migrate_plaintext_database(cancellation)

    assert time.monotonic() - started < 5
    assert fault_stage in database.reached_stages
    _assert_one_valid_database(
        path, driver, secret_store.get(DATABASE_KEY_ACCOUNT)
    )
    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


def test_cancellation_inside_safety_recovery_keeps_both_valid_copies(
    tmp_path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = RecoveryCancellingDatabase(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
        fault_stage="after-source-replace",
        fault_kind="exception",
    )
    started = time.monotonic()

    with pytest.raises(UserFacingError, match="anulată"):
        database._migrate_plaintext_database(CancellationContext(5))

    assert time.monotonic() - started < 5
    rollback = next(tmp_path.glob(".legacy.sqlite3.rollback-*"))
    temporary = next(tmp_path.glob(".tatatuya-encrypted-*.tmp"))
    assert not path.exists()
    assert rollback.read_bytes().startswith(_PLAINTEXT_HEADER)
    assert driver.is_encrypted(temporary)
    assert database._migration_marker_path().exists()

    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


@pytest.mark.parametrize(
    "blocked_statement",
    ["PRAGMA foreign_key_check", "PRAGMA integrity_check"],
)
def test_blocked_safety_recovery_verification_uses_fresh_context_and_restarts(
    tmp_path, blocked_statement
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = BlockingRecoveryDatabase(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
        fault_stage="before-rollback-cleanup",
        fault_kind="exception",
        blocked_statement=blocked_statement,
    )
    caller_context = CancellationContext(5)
    started = time.monotonic()

    with pytest.raises(InjectedMigrationFailure, match="before-rollback-cleanup"):
        database._migrate_plaintext_database(caller_context)

    assert time.monotonic() - started < 1
    assert database.recovery_context is not None
    assert database.recovery_context is not caller_context
    assert database.recovery_context.cancelled
    assert driver.progress_interruptions == 1
    assert not caller_context.cancelled
    expected_stage = (
        "recovery-verification:foreign-key"
        if blocked_statement == "PRAGMA foreign_key_check"
        else "recovery-verification:integrity"
    )
    assert expected_stage in database.reached_stages
    _assert_one_valid_database(
        path, driver, secret_store.get(DATABASE_KEY_ACCOUNT)
    )
    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


def test_failed_recovery_restore_preserves_active_and_rollback_copies(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = FailingRecoveryVerificationDatabase(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
        fault_stage="before-rollback-cleanup",
        fault_kind="exception",
    )
    original_replace = os.replace
    restore_failed = False

    def fail_recovery_restore(source, destination) -> None:
        nonlocal restore_failed
        source_path = Path(source)
        if (
            not restore_failed
            and source_path.name.startswith(f".{path.name}.rollback-")
            and Path(destination) == path
        ):
            restore_failed = True
            raise OSError("injected recovery restore failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_recovery_restore)
    with pytest.raises(OSError, match="injected recovery restore failure"):
        database._migrate_plaintext_database(CancellationContext(5))

    assert restore_failed
    rollback = next(tmp_path.glob(f".{path.name}.rollback-*"))
    assert driver.is_encrypted(path)
    assert rollback.read_bytes().startswith(_PLAINTEXT_HEADER)
    assert database._migration_marker_path().exists()
    key = secret_store.get(DATABASE_KEY_ACCOUNT)
    assert key is not None
    active = driver.connect(path)
    try:
        Database._apply_key(active, key)
        assert active.execute(
            "SELECT COUNT(*) FROM migration_probe"
        ).fetchone() == (len(_PROBE_ROWS),)
    finally:
        active.close()
    with closing(sqlite3.connect(rollback)) as plaintext:
        assert plaintext.execute(
            "SELECT COUNT(*) FROM migration_probe"
        ).fetchone() == (len(_PROBE_ROWS),)

    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


@pytest.mark.parametrize("replace_call", [1, 2])
def test_filesystem_replace_failure_recovers_and_restarts(
    tmp_path, monkeypatch, replace_call
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = Database(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
    )
    original_replace = os.replace
    calls = 0

    def fail_selected_replace(source, destination) -> None:
        nonlocal calls
        calls += 1
        if calls == replace_call:
            raise OSError("injected replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_selected_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        database._migrate_plaintext_database(CancellationContext(5))

    assert calls >= replace_call
    _assert_one_valid_database(
        path, driver, secret_store.get(DATABASE_KEY_ACCOUNT)
    )
    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


def test_rollback_cleanup_failure_is_retried_by_recovery(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = Database(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
    )
    original_remove = database._remove_migration_artifact
    failed = False

    def fail_rollback_once(candidate: Path) -> None:
        nonlocal failed
        if not failed and candidate.name.startswith(f".{path.name}.rollback-"):
            failed = True
            raise OSError("injected rollback cleanup failure")
        original_remove(candidate)

    monkeypatch.setattr(database, "_remove_migration_artifact", fail_rollback_once)
    with pytest.raises(OSError, match="injected rollback cleanup failure"):
        database._migrate_plaintext_database(CancellationContext(5))

    assert failed
    _assert_one_valid_database(
        path, driver, secret_store.get(DATABASE_KEY_ACCOUNT)
    )
    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


def test_marker_cleanup_failure_is_retried_by_recovery(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    driver = FakeCipherDriver()
    secret_store = MemorySecretStore()
    database = Database(
        path,
        driver=driver,
        secret_store=secret_store,
        require_cipher=True,
    )
    marker = database._migration_marker_path()
    original_unlink = Path.unlink
    failed = False

    def fail_marker_once(candidate: Path, *args, **kwargs) -> None:
        nonlocal failed
        if not failed and candidate == marker:
            failed = True
            raise OSError("injected marker cleanup failure")
        original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_marker_once)
    with pytest.raises(OSError, match="injected marker cleanup failure"):
        database._migrate_plaintext_database(CancellationContext(5))

    assert failed
    _assert_one_valid_database(
        path, driver, secret_store.get(DATABASE_KEY_ACCOUNT)
    )
    _restart_and_assert_converged(path, driver, secret_store, expected_snapshot)


class BlockingVerificationConnection:
    def __init__(self) -> None:
        self.handler = None

    def set_progress_handler(self, handler, steps: int) -> None:
        del steps
        self.handler = handler

    def execute(self, sql: str, parameters=()):
        del parameters
        if sql == "PRAGMA foreign_key_check":
            while self.handler is not None and not self.handler():
                time.sleep(0.001)
            raise sqlite3.OperationalError("interrupted")
        return _Rows([])

    def close(self) -> None:
        pass


def test_blocked_verification_uses_progress_handler_for_cancellation(
    tmp_path, monkeypatch
) -> None:
    connection = BlockingVerificationConnection()
    database = Database(
        tmp_path / "blocked.sqlite3",
        driver=sqlite3,
        secret_store=MemorySecretStore(),
        require_cipher=True,
    )
    monkeypatch.setattr(database, "_open", lambda path, key: connection)
    cancellation = CancellationContext(5)
    timer = threading.Timer(0.05, cancellation.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(UserFacingError, match="anulată"):
            database._verify_migrated_database(
                database.path,
                b"k" * 32,
                cancellation,
                stage_prefix="blocked-verification",
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 1
    assert connection.handler is None


@pytest.mark.macos_keychain
@pytest.mark.skipif(sys.platform != "darwin", reason="requires native SQLCipher")
def test_native_sqlcipher_upgrades_populated_plaintext_database(tmp_path) -> None:
    from tatatuya.infrastructure.dbapi import dbapi as sqlcipher

    path = tmp_path / "native-legacy.sqlite3"
    expected_snapshot = _create_populated_legacy_database(path)
    service = f"ro.tatatuya.app.test.{uuid.uuid4().hex}"
    secret_store = MacOSKeychainSecretStore(service)
    try:
        database = Database(
            path,
            driver=sqlcipher,
            secret_store=secret_store,
            require_cipher=True,
        )
        database.initialize(CancellationContext(15))

        assert not path.read_bytes().startswith(_PLAINTEXT_HEADER)
        assert path.stat().st_mode & 0o777 == 0o600
        assert secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT) == _LEGACY_SECRET
        key = secret_store.get(DATABASE_KEY_ACCOUNT)
        assert key is not None
        with database.connect() as connection:
            actual_snapshot = Database._database_snapshot(connection, "main")
            assert connection.execute(
                "SELECT 1 FROM settings WHERE key = 'tuya.client_secret'"
            ).fetchone() is None
        assert actual_snapshot == expected_snapshot

        wrong_key = bytes([key[0] ^ 0xFF]) + key[1:]
        wrong = sqlcipher.connect(path)
        try:
            Database._apply_key(wrong, wrong_key)
            with pytest.raises(sqlcipher.DatabaseError):
                wrong.execute("SELECT name FROM sqlite_master").fetchall()
        finally:
            wrong.close()
        with closing(sqlite3.connect(path)) as plaintext:
            with pytest.raises(sqlite3.DatabaseError):
                plaintext.execute("SELECT name FROM sqlite_master").fetchall()

        assert not database._migration_marker_path().exists()
        assert not list(tmp_path.glob(".tatatuya-encrypted-*.tmp"))
        assert not list(tmp_path.glob(".native-legacy.sqlite3.rollback-*"))
    finally:
        secret_store.delete(DATABASE_KEY_ACCOUNT)
        secret_store.delete(TUYA_CLIENT_SECRET_ACCOUNT)
        assert secret_store.get(DATABASE_KEY_ACCOUNT) is None
        assert secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT) is None
