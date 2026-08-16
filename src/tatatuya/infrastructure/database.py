"""Fail-closed SQLCipher connection, migration, and permission management."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, Iterator

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - exercised by the unsupported-platform test
    fcntl = None  # type: ignore[assignment]

from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.infrastructure.migrations import migrate
from tatatuya.infrastructure.secrets import (
    DATABASE_KEY_ACCOUNT,
    TUYA_CLIENT_SECRET_ACCOUNT,
    MacOSKeychainSecretStore,
    PlaintextFileSecretStore,
    SecretStore,
    SecretStoreError,
)
from tatatuya.paths import database_path


_PLAINTEXT_HEADER = b"SQLite format 3\x00"
_MIGRATION_RECOVERY_SECONDS = 5.0
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class SecureDatabaseError(RuntimeError):
    """Safe infrastructure error for unavailable or unreadable encrypted data."""


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.absolute()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


class Database:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        driver: Any | None = None,
        secret_store: SecretStore | None = None,
        require_cipher: bool | None = None,
    ) -> None:
        if sys.platform != "darwin" and os.name != "posix":
            raise UserFacingError(
                "Platformă neacceptată",
                "Dezvoltarea TataTuya este acceptată numai pe sisteme POSIX, iar aplicația distribuită este pentru macOS.",
            )
        self.path = Path(path) if path is not None else database_path()
        self.require_cipher = sys.platform == "darwin" if require_cipher is None else require_cipher
        self.driver = driver
        if secret_store is None:
            secret_store = (
                MacOSKeychainSecretStore()
                if self.require_cipher
                else PlaintextFileSecretStore(
                    self.path.parent / "tuya-client-secret.plaintext"
                )
            )
        self.secret_store = secret_store
        self._database_key: bytes | None = None
        self._prepared = False
        self._lock = _path_lock(self.path)

    @staticmethod
    def _load_driver(require_cipher: bool) -> Any:
        if require_cipher:
            try:
                dbapi2 = import_module("sqlcipher3.dbapi2")
            except ImportError as exc:
                raise SecureDatabaseError(
                    "SQLCipher is required for the TataTuya database"
                ) from exc
            return dbapi2
        import sqlite3

        return sqlite3

    def _dbapi(self) -> Any:
        if self.driver is None:
            raise SecureDatabaseError("The database driver is unavailable")
        return self.driver

    def initialize(self, cancellation: CancellationContext | None = None) -> None:
        with self._lock:
            self._secure_parent_with_safe_errors()
            with self._interprocess_lock(cancellation):
                self._prepare_storage_with_safe_errors(cancellation)
                self._checkpoint(cancellation)
                connection = self._open(self.path, self._database_key)
                self._install_cancellation_handler(connection, cancellation)
                try:
                    self._checkpoint(cancellation)
                    migrate(connection)
                    self._checkpoint(cancellation)
                    if self.require_cipher:
                        self._verify_cipher_integrity(connection, cancellation)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    self._checkpoint(cancellation)
                    raise
                finally:
                    self._remove_cancellation_handler(connection, cancellation)
                    connection.close()
            self._secure_sensitive_files()

    @contextmanager
    def connect(
        self, cancellation: CancellationContext | None = None
    ) -> Iterator[Any]:
        with self._lock:
            if not self._prepared:
                self._secure_parent_with_safe_errors()
                with self._interprocess_lock(cancellation):
                    self._prepare_storage_with_safe_errors(cancellation)
            self._checkpoint(cancellation)
            connection = self._open(self.path, self._database_key)
            self._install_cancellation_handler(connection, cancellation)
        try:
            self._checkpoint(cancellation)
            yield connection
            self._checkpoint(cancellation)
            connection.commit()
        except BaseException:
            connection.rollback()
            self._checkpoint(cancellation)
            raise
        finally:
            self._remove_cancellation_handler(connection, cancellation)
            connection.close()
            self._secure_sensitive_files()

    def _prepare_storage(self, cancellation: CancellationContext | None = None) -> None:
        if self._prepared:
            return
        self._checkpoint(cancellation)
        if self.driver is None:
            self.driver = self._load_driver(self.require_cipher)
        if self.require_cipher:
            self._verify_cipher_driver(cancellation)
        self._secure_parent()
        if self.require_cipher:
            self._recover_interrupted_migration(cancellation)
        for candidate in self._sidecar_paths(include_database=True):
            if os.path.lexists(candidate):
                self._validate_regular_owned_file(candidate)
        if not self.require_cipher:
            self._prepared = True
            return

        self._checkpoint(cancellation)
        header = self._read_header() if os.path.lexists(self.path) else b""
        if header == _PLAINTEXT_HEADER:
            self._migrate_plaintext_database(cancellation)
        elif os.path.lexists(self.path):
            self._checkpoint(cancellation)
            key = self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation)
            self._checkpoint(cancellation)
            if key is None:
                raise self._recovery_error("Cheia bazei de date lipsește din Keychain.")
            self._validate_database_key(key)
            self._probe_encrypted_database(key, cancellation)
            self._database_key = key
        else:
            self._checkpoint(cancellation)
            key = self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation)
            self._checkpoint(cancellation)
            if key is None:
                key = self.secret_store.set_if_absent(
                    DATABASE_KEY_ACCOUNT,
                    secrets.token_bytes(32),
                    label="TataTuya database key",
                    cancellation=cancellation,
                )
                self._checkpoint(cancellation)
                if self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation) != key:
                    raise SecureDatabaseError("Database key round-trip failed")
                self._checkpoint(cancellation)
            self._validate_database_key(key)
            self._database_key = key
            connection = self._open(self.path, key)
            connection.close()
            self._secure_sensitive_files()
        self._prepared = True

    def _verify_cipher_driver(
        self, cancellation: CancellationContext | None = None
    ) -> None:
        connection = self._dbapi().connect(":memory:")
        self._install_cancellation_handler(connection, cancellation)
        try:
            self._checkpoint(cancellation)
            version = connection.execute("PRAGMA cipher_version").fetchone()
            self._checkpoint(cancellation)
            if version is None or not version[0]:
                raise SecureDatabaseError("The database driver is not SQLCipher")
        finally:
            self._remove_cancellation_handler(connection, cancellation)
            connection.close()

    def _prepare_storage_with_safe_errors(
        self, cancellation: CancellationContext | None = None
    ) -> None:
        try:
            self._prepare_storage(cancellation)
        except SecretStoreError as exc:
            raise UserFacingError(
                "Keychain nu este disponibil",
                "TataTuya nu poate accesa datele protejate. Deblocați Keychain și încercați din nou.",
                f"Keychain operation failed: {exc.operation}",
            ) from exc
        except (OSError, SecureDatabaseError) as exc:
            raise self._recovery_error(str(exc)) from exc

    def _open(self, path: Path, key: bytes | None) -> Any:
        driver = self._dbapi()
        connection = driver.connect(path)
        try:
            if self.require_cipher:
                if key is None:
                    raise SecureDatabaseError("The database key is unavailable")
                self._apply_key(connection, key)
                version = connection.execute("PRAGMA cipher_version").fetchone()
                if version is None or not version[0]:
                    raise SecureDatabaseError("The database driver is not SQLCipher")
                connection.execute("PRAGMA temp_store = MEMORY")
            connection.row_factory = driver.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _apply_key(connection: Any, key: bytes) -> None:
        Database._validate_database_key(key)
        connection.execute(f'PRAGMA key = "x\'{key.hex()}\'"')

    @staticmethod
    def _validate_database_key(key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise SecureDatabaseError("The database key has an invalid format")

    def _probe_encrypted_database(
        self, key: bytes, cancellation: CancellationContext | None = None
    ) -> None:
        try:
            self._probe_database_path(self.path, key, cancellation)
        except Exception as exc:
            self._checkpoint(cancellation)
            raise self._recovery_error(
                "Baza de date nu poate fi deschisă cu cheia din Keychain."
            ) from exc

    def _probe_database_path(
        self,
        path: Path,
        key: bytes,
        cancellation: CancellationContext | None = None,
    ) -> None:
        connection = self._open(path, key)
        self._install_cancellation_handler(connection, cancellation)
        try:
            self._checkpoint(cancellation)
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            self._checkpoint(cancellation)
        finally:
            self._remove_cancellation_handler(connection, cancellation)
            connection.close()

    def _migrate_plaintext_database(
        self, cancellation: CancellationContext | None = None
    ) -> None:
        self._migration_checkpoint("legacy-secret-read", cancellation)
        legacy_secret = self._read_legacy_client_secret(cancellation)
        self._migration_checkpoint("legacy-secret-read-complete", cancellation)
        if legacy_secret:
            encoded_secret = legacy_secret.encode("utf-8")
            self._migration_checkpoint("legacy-secret-write", cancellation)
            self.secret_store.set(
                TUYA_CLIENT_SECRET_ACCOUNT,
                encoded_secret,
                label="TataTuya Tuya Client Secret",
                cancellation=cancellation,
            )
            self._migration_checkpoint("legacy-secret-write-complete", cancellation)
            self._migration_checkpoint("legacy-secret-verify", cancellation)
            if (
                self.secret_store.get(TUYA_CLIENT_SECRET_ACCOUNT, cancellation)
                != encoded_secret
            ):
                raise SecureDatabaseError("Client Secret round-trip failed")
            self._migration_checkpoint("legacy-secret-verify-complete", cancellation)

        self._migration_checkpoint("database-key-read", cancellation)
        key = self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation)
        self._migration_checkpoint("database-key-read-complete", cancellation)
        if key is None:
            self._migration_checkpoint("database-key-create", cancellation)
            key = self.secret_store.set_if_absent(
                DATABASE_KEY_ACCOUNT,
                secrets.token_bytes(32),
                label="TataTuya database key",
                cancellation=cancellation,
            )
            self._migration_checkpoint("database-key-create-complete", cancellation)
        self._migration_checkpoint("database-key-verify", cancellation)
        if self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation) != key:
            raise SecureDatabaseError("Database key round-trip failed")
        self._migration_checkpoint("database-key-verify-complete", cancellation)
        self._validate_database_key(key)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".tatatuya-encrypted-", suffix=".tmp", dir=self.path.parent
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        temporary = Path(temporary_name)
        rollback = self.path.parent / f".{self.path.name}.rollback-{secrets.token_hex(8)}"
        try:
            self._write_migration_marker(temporary, rollback)
            self._migration_checkpoint("marker-written", cancellation)
            source = self._dbapi().connect(self.path)
            self._install_cancellation_handler(source, cancellation)
            try:
                self._migration_checkpoint("source-opened", cancellation)
                expected_snapshot = self._database_snapshot(
                    source,
                    "main",
                    exclude_legacy_secret=True,
                    cancellation=cancellation,
                )
                self._migration_checkpoint("source-snapshot", cancellation)
                escaped_path = str(temporary).replace("'", "''")
                source.execute(
                    f"ATTACH DATABASE '{escaped_path}' AS encrypted "
                    f"KEY \"x'{key.hex()}'\""
                )
                source.execute("PRAGMA encrypted.journal_mode = OFF")
                self._migration_checkpoint("export", cancellation)
                try:
                    source.execute("SELECT sqlcipher_export('encrypted')")
                except Exception:
                    self._checkpoint(cancellation)
                    raise
                self._migration_checkpoint("export-complete", cancellation)
                source.execute(
                    "DELETE FROM encrypted.settings WHERE key = ?",
                    ("tuya.client_secret",),
                )
                migrated_snapshot = self._database_snapshot(
                    source, "encrypted", cancellation=cancellation
                )
                self._migration_checkpoint("destination-snapshot", cancellation)
                if migrated_snapshot != expected_snapshot:
                    raise SecureDatabaseError(
                        "Migrated database schema or row counts do not match"
                    )
                source.commit()
                source.execute("DETACH DATABASE encrypted")
            except UserFacingError:
                raise
            except Exception:
                self._checkpoint(cancellation)
                raise
            finally:
                self._remove_cancellation_handler(source, cancellation)
                source.close()
            os.chmod(temporary, 0o600, follow_symlinks=False)
            self._verify_migrated_database(
                temporary,
                key,
                cancellation,
                stage_prefix="temporary-verification",
            )
            self._fsync_file(temporary)
            self._migration_checkpoint("before-source-replace", cancellation)
            os.replace(self.path, rollback)
            self._migration_checkpoint("after-source-replace", cancellation)
            self._fsync_directory()
            self._migration_checkpoint("before-destination-replace", cancellation)
            os.replace(temporary, self.path)
            self._migration_checkpoint("after-destination-replace", cancellation)
            self._fsync_directory()
            self._verify_migrated_database(
                self.path,
                key,
                cancellation,
                stage_prefix="active-verification",
            )
            self._migration_checkpoint("before-rollback-cleanup", cancellation)
            self._remove_migration_artifact(rollback)
            self._migration_checkpoint("after-rollback-cleanup", cancellation)
            self._migration_checkpoint("before-marker-cleanup", cancellation)
            self._migration_marker_path().unlink()
            self._migration_checkpoint("after-marker-cleanup", cancellation)
            self._fsync_directory()
            self._remove_legacy_sidecars()
            self._database_key = key
        except BaseException:
            if os.path.lexists(self._migration_marker_path()):
                recovery = CancellationContext(_MIGRATION_RECOVERY_SECONDS)
                self._recover_interrupted_migration(recovery)
            elif os.path.lexists(temporary):
                self._remove_migration_artifact(temporary)
            raise

    @staticmethod
    def _database_snapshot(
        connection: Any,
        schema: str,
        *,
        exclude_legacy_secret: bool = False,
        cancellation: CancellationContext | None = None,
    ) -> dict[str, Any]:
        if schema not in {"main", "encrypted"}:
            raise ValueError("Unsupported database schema")
        try:
            objects = connection.execute(
                f"SELECT type, name, tbl_name, sql FROM {schema}.sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        except UserFacingError:
            raise
        except Exception:
            Database._checkpoint(cancellation)
            raise
        Database._checkpoint(cancellation)
        normalized_objects = [tuple(row) for row in objects]
        tables = [
            str(row[1])
            for row in objects
            if str(row[0]) == "table" and row[3] is not None
        ]
        row_counts: dict[str, int] = {}
        row_hashes: dict[str, str] = {}
        for table in tables:
            Database._checkpoint(cancellation)
            quoted_table = table.replace('"', '""')
            where = ""
            parameters: tuple[object, ...] = ()
            if exclude_legacy_secret and table == "settings":
                where = " WHERE key <> ?"
                parameters = ("tuya.client_secret",)
            try:
                cursor = connection.execute(
                    f'SELECT * FROM {schema}."{quoted_table}"{where} ORDER BY rowid',
                    parameters,
                )
                digest = hashlib.sha256()
                count = 0
                for row in cursor:
                    Database._checkpoint(cancellation)
                    count += 1
                    for value in tuple(row):
                        encoded = Database._snapshot_value(value)
                        digest.update(len(encoded).to_bytes(8, "big"))
                        digest.update(encoded)
            except UserFacingError:
                raise
            except Exception:
                Database._checkpoint(cancellation)
                raise
            row_counts[table] = count
            row_hashes[table] = digest.hexdigest()
        return {
            "objects": normalized_objects,
            "rows": row_counts,
            "hashes": row_hashes,
        }

    @staticmethod
    def _snapshot_value(value: object) -> bytes:
        if value is None:
            return b"n"
        if isinstance(value, bytes):
            return b"b" + value
        if isinstance(value, str):
            return b"s" + value.encode("utf-8")
        if isinstance(value, int):
            return b"i" + str(value).encode("ascii")
        if isinstance(value, float):
            return b"f" + value.hex().encode("ascii")
        raise SecureDatabaseError("Database contains an unsupported value type")

    def _migration_marker_path(self) -> Path:
        return self.path.parent / f".{self.path.name}.migration-state"

    def _write_migration_marker(self, temporary: Path, rollback: Path) -> None:
        marker = self._migration_marker_path()
        payload = json.dumps(
            {
                "version": 1,
                "temporary": temporary.name,
                "rollback": rollback.name,
            },
            sort_keys=True,
        ).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(marker, flags, 0o600)
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("Could not write database migration marker")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory()

    def _recover_interrupted_migration(
        self, cancellation: CancellationContext | None = None
    ) -> None:
        self._migration_checkpoint("recovery-start", cancellation)
        marker = self._migration_marker_path()
        if not os.path.lexists(marker):
            return
        self._validate_regular_owned_file(marker)
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureDatabaseError("Database migration state is invalid") from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise SecureDatabaseError("Database migration state is invalid")
        temporary = self._migration_member(
            state.get("temporary"), ".tatatuya-encrypted-", ".tmp"
        )
        rollback = self._migration_member(
            state.get("rollback"), f".{self.path.name}.rollback-", ""
        )
        for candidate in (temporary, rollback, self.path):
            if os.path.lexists(candidate):
                self._validate_regular_owned_file(candidate)

        main_exists = os.path.lexists(self.path)
        rollback_exists = os.path.lexists(rollback)
        if not main_exists and rollback_exists:
            os.replace(rollback, self.path)
            self._remove_migration_artifact(temporary)
        elif main_exists and self._read_header() == _PLAINTEXT_HEADER:
            if rollback_exists:
                raise SecureDatabaseError("Ambiguous plaintext migration recovery")
            self._remove_migration_artifact(temporary)
        elif main_exists:
            self._checkpoint(cancellation)
            key = self.secret_store.get(DATABASE_KEY_ACCOUNT, cancellation)
            self._checkpoint(cancellation)
            if key is None:
                raise SecureDatabaseError("Database migration key is unavailable")
            self._validate_database_key(key)
            try:
                self._verify_migrated_database(
                    self.path,
                    key,
                    cancellation,
                    stage_prefix="recovery-verification",
                )
            except Exception:
                if not rollback_exists:
                    raise
                os.replace(rollback, self.path)
            else:
                self._remove_migration_artifact(rollback)
            self._remove_migration_artifact(temporary)
        else:
            raise SecureDatabaseError("Database migration cannot be recovered")
        marker.unlink()
        self._fsync_directory()

    def _migration_member(
        self, value: object, required_prefix: str, required_suffix: str
    ) -> Path:
        if not isinstance(value, str) or Path(value).name != value:
            raise SecureDatabaseError("Database migration state is invalid")
        if not value.startswith(required_prefix) or not value.endswith(required_suffix):
            raise SecureDatabaseError("Database migration state is invalid")
        variable = value[len(required_prefix) :]
        if required_suffix:
            variable = variable[: -len(required_suffix)]
        if not variable or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in variable
        ):
            raise SecureDatabaseError("Database migration state is invalid")
        return self.path.parent / value

    def _remove_migration_artifact(self, path: Path) -> None:
        for candidate in (
            path,
            Path(f"{path}-journal"),
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
        ):
            if not os.path.lexists(candidate):
                continue
            self._validate_regular_owned_file(candidate)
            candidate.unlink()

    def _read_legacy_client_secret(
        self, cancellation: CancellationContext | None = None
    ) -> str | None:
        connection = self._dbapi().connect(self.path)
        self._install_cancellation_handler(connection, cancellation)
        try:
            self._checkpoint(cancellation)
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
            ).fetchone()
            if table is None:
                self._checkpoint(cancellation)
                return None
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("tuya.client_secret",),
            ).fetchone()
            self._checkpoint(cancellation)
            return None if row is None else str(row[0])
        except UserFacingError:
            raise
        except Exception:
            self._checkpoint(cancellation)
            raise
        finally:
            self._remove_cancellation_handler(connection, cancellation)
            connection.close()

    def _verify_migrated_database(
        self,
        path: Path,
        key: bytes,
        cancellation: CancellationContext | None = None,
        *,
        stage_prefix: str = "verification",
    ) -> None:
        connection = self._open(path, key)
        self._install_cancellation_handler(connection, cancellation)
        try:
            self._migration_checkpoint(
                f"{stage_prefix}:foreign-key", cancellation
            )
            try:
                foreign_key_error = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchone()
            except Exception:
                self._checkpoint(cancellation)
                raise
            if foreign_key_error is not None:
                raise SecureDatabaseError("Migrated database has invalid foreign keys")
            self._migration_checkpoint(f"{stage_prefix}:integrity", cancellation)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            except Exception:
                self._checkpoint(cancellation)
                raise
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise SecureDatabaseError("Migrated database failed integrity_check")
            self._migration_checkpoint(
                f"{stage_prefix}:cipher-integrity", cancellation
            )
            self._verify_cipher_integrity(connection, cancellation)
            secret = connection.execute(
                "SELECT 1 FROM settings WHERE key = ?", ("tuya.client_secret",)
            ).fetchone()
            if secret is not None:
                raise SecureDatabaseError("Client Secret remained in migrated database")
        except UserFacingError:
            raise
        except Exception:
            self._checkpoint(cancellation)
            raise
        finally:
            self._remove_cancellation_handler(connection, cancellation)
            connection.close()
        self._migration_checkpoint(f"{stage_prefix}:wrong-key", cancellation)
        wrong_key = bytes([key[0] ^ 0xFF]) + key[1:]
        try:
            self._probe_database_path(path, wrong_key, cancellation)
        except Exception:
            self._checkpoint(cancellation)
            pass
        else:
            raise SecureDatabaseError("Migrated database accepted a wrong key")
        self._migration_checkpoint(f"{stage_prefix}:empty-key", cancellation)
        empty = self._dbapi().connect(path)
        self._install_cancellation_handler(empty, cancellation)
        try:
            self._checkpoint(cancellation)
            empty.execute("PRAGMA key = ''")
            empty.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            self._checkpoint(cancellation)
        except Exception:
            self._checkpoint(cancellation)
            pass
        else:
            raise SecureDatabaseError("Migrated database accepted an empty key")
        finally:
            self._remove_cancellation_handler(empty, cancellation)
            empty.close()

    @staticmethod
    def _verify_cipher_integrity(
        connection: Any, cancellation: CancellationContext | None = None
    ) -> None:
        Database._checkpoint(cancellation)
        try:
            rows = connection.execute("PRAGMA cipher_integrity_check").fetchall()
        except Exception:
            Database._checkpoint(cancellation)
            raise
        Database._checkpoint(cancellation)
        if rows:
            raise SecureDatabaseError("SQLCipher integrity check failed")

    def _migration_checkpoint(
        self, stage: str, cancellation: CancellationContext | None
    ) -> None:
        """Expose named safe boundaries for deterministic recovery testing."""

        del stage
        self._checkpoint(cancellation)

    @contextmanager
    def _interprocess_lock(
        self, cancellation: CancellationContext | None
    ) -> Iterator[None]:
        """Serialize storage classification, recovery, and initial migrations."""

        lock_path = self.path.parent / f".{self.path.name}.lock"
        if fcntl is None:  # pragma: no cover - rejected during construction
            raise UserFacingError(
                "Platformă neacceptată",
                "Blocarea sigură a bazei de date necesită un sistem POSIX.",
            )
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                os.close(descriptor)
                raise self._recovery_error("Database lock path is unsafe")
            os.fchmod(descriptor, 0o600)
        except OSError as exc:
            raise self._recovery_error("Database startup lock is unavailable") from exc

        deadline = time.monotonic() + 5.0
        acquired = False
        try:
            while not acquired:
                self._checkpoint(cancellation)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise UserFacingError(
                            "Baza de date este ocupată",
                            "O altă instanță TataTuya pregătește datele locale. Închideți cealaltă instanță și încercați din nou.",
                        )
                    if cancellation is None:
                        time.sleep(0.05)
                    else:
                        cancellation.wait(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _checkpoint(cancellation: CancellationContext | None) -> None:
        if cancellation is not None:
            cancellation.checkpoint()

    @staticmethod
    def _install_cancellation_handler(
        connection: Any, cancellation: CancellationContext | None
    ) -> None:
        if cancellation is not None:
            connection.set_progress_handler(
                lambda: int(
                    cancellation.cancelled
                    or cancellation.remaining_seconds() <= 0
                ),
                1000,
            )

    @staticmethod
    def _remove_cancellation_handler(
        connection: Any, cancellation: CancellationContext | None
    ) -> None:
        if cancellation is not None:
            connection.set_progress_handler(None, 0)

    def _secure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._validate_directory(self.path.parent)
        os.chmod(self.path.parent, 0o700, follow_symlinks=False)

    def _secure_parent_with_safe_errors(self) -> None:
        try:
            self._secure_parent()
        except (OSError, SecureDatabaseError) as exc:
            raise self._recovery_error(str(exc)) from exc

    @staticmethod
    def _validate_directory(path: Path) -> None:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SecureDatabaseError("Application data directory is unsafe")

    @staticmethod
    def _validate_regular_owned_file(path: Path) -> None:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise SecureDatabaseError("Database path is unsafe")

    def _secure_sensitive_files(self) -> None:
        for candidate in self._sidecar_paths(include_database=True):
            if not os.path.lexists(candidate):
                continue
            self._validate_regular_owned_file(candidate)
            os.chmod(candidate, 0o600, follow_symlinks=False)

    def _remove_legacy_sidecars(self) -> None:
        for candidate in self._sidecar_paths(include_database=False):
            if os.path.lexists(candidate):
                self._validate_regular_owned_file(candidate)
                candidate.unlink()

    def _sidecar_paths(self, *, include_database: bool) -> tuple[Path, ...]:
        values = (
            self.path,
            Path(f"{self.path}-journal"),
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        )
        return values if include_database else values[1:]

    def _read_header(self) -> bytes:
        with self.path.open("rb") as stream:
            return stream.read(len(_PLAINTEXT_HEADER))

    def _fsync_directory(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _recovery_error(details: str) -> UserFacingError:
        return UserFacingError(
            "Baza de date nu poate fi deschisă",
            "Datele locale sunt protejate și nu au fost înlocuite. Verificați accesul la Keychain și încercați din nou.",
            details,
        )

    def client_secret_store(self) -> SecretStore:
        return self.secret_store
