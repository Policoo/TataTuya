from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sqlite3
import sys
import multiprocessing

import pytest

from tatatuya.domain.billing import calculate_period
from tatatuya.domain.cancellation import CancellationContext
from tatatuya.domain.errors import UserFacingError
from tatatuya.domain.models import (
    Currency,
    Device,
    EnergyEligibility,
    Reading,
    TuyaSettings,
)
from tatatuya.infrastructure.database import Database
from tatatuya.infrastructure import migrations
from tatatuya.infrastructure.secrets import (
    MemorySecretStore,
    PlaintextFileSecretStore,
    SecretStoreError,
)
from tatatuya.infrastructure.repositories.calculations import (
    CalculationRepository,
    DevicePreferenceRepository,
)
from tatatuya.infrastructure.repositories.devices import DeviceRepository
from tatatuya.infrastructure.repositories.readings import ReadingRepository
from tatatuya.infrastructure.repositories.settings import (
    DatabaseSettingsStore,
    SettingsRepository,
)


NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)


def _initialize_in_process(path: str, start, results) -> None:
    try:
        start.wait(5)
        Database(path).initialize()
        results.put(None)
    except BaseException as exc:  # pragma: no cover - asserted in parent
        results.put(repr(exc))


def initialized_database(tmp_path) -> Database:
    database = Database(tmp_path / "tatatuya.sqlite3")
    database.initialize()
    return database


def test_empty_database_migrates_idempotently(tmp_path) -> None:
    database = initialized_database(tmp_path)
    database.initialize()
    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert [row[0] for row in versions] == [1, 2, 3, 4]
    assert {"settings", "devices", "readings", "calculations"} <= tables


def test_concurrent_process_startup_serializes_initial_migrations(tmp_path) -> None:
    if sys.platform == "darwin":
        pytest.skip("native Keychain/SQLCipher race belongs to the clean-Mac group")
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    path = str(tmp_path / "concurrent.sqlite3")
    processes = [
        context.Process(target=_initialize_in_process, args=(path, start, results))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)

    assert outcomes == [None] * len(processes)
    assert all(process.exitcode == 0 for process in processes)
    with Database(path).connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [row[0] for row in versions] == [1, 2, 3, 4]


def test_cancelled_startup_opens_no_database(tmp_path) -> None:
    path = tmp_path / "cancelled-startup.sqlite3"
    cancellation = CancellationContext(10)
    cancellation.cancel()

    with pytest.raises(UserFacingError, match="anulată"):
        Database(path).initialize(cancellation)

    assert not path.exists()


def test_early_plaintext_conversion_recovery_removes_temp_and_sidecars(
    tmp_path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    connection.commit()
    connection.close()
    temporary = tmp_path / ".tatatuya-encrypted-abc.tmp"
    temporary.write_bytes(b"")
    sidecars = [
        tmp_path / f"{temporary.name}-journal",
        tmp_path / f"{temporary.name}-wal",
        tmp_path / f"{temporary.name}-shm",
    ]
    for sidecar in sidecars:
        sidecar.write_bytes(b"temporary")
    rollback = tmp_path / ".legacy.sqlite3.rollback-abc"
    database = Database(
        path,
        driver=sqlite3,
        secret_store=MemorySecretStore(),
        require_cipher=True,
    )
    database._write_migration_marker(temporary, rollback)

    database._recover_interrupted_migration()

    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    assert not temporary.exists()
    assert not any(sidecar.exists() for sidecar in sidecars)
    assert not database._migration_marker_path().exists()


def test_database_snapshot_checks_cancellation_inside_row_scan(tmp_path) -> None:
    path = tmp_path / "snapshot.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE payload(value TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO payload(value) VALUES (?)",
        (("x" * 1024,) for _ in range(100)),
    )

    class RowScanCancellation(CancellationContext):
        def __init__(self) -> None:
            super().__init__(10)
            self.checks = 0

        def checkpoint(self) -> None:
            self.checks += 1
            if self.checks == 8:
                self.cancel()
            super().checkpoint()

    cancellation = RowScanCancellation()
    try:
        with pytest.raises(UserFacingError, match="anulată"):
            Database._database_snapshot(
                connection, "main", cancellation=cancellation
            )
    finally:
        connection.close()
    assert cancellation.checks == 8


def test_cancelled_recovery_keeps_marker_and_valid_plaintext_source(tmp_path) -> None:
    path = tmp_path / "legacy-cancelled.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE retained(value TEXT NOT NULL)")
    connection.execute("INSERT INTO retained(value) VALUES ('kept')")
    connection.commit()
    connection.close()
    temporary = tmp_path / ".tatatuya-encrypted-cancel.tmp"
    temporary.write_bytes(b"")
    rollback = tmp_path / ".legacy-cancelled.sqlite3.rollback-cancel"
    database = Database(
        path,
        driver=sqlite3,
        secret_store=MemorySecretStore(),
        require_cipher=True,
    )
    database._write_migration_marker(temporary, rollback)
    cancellation = CancellationContext(10)
    cancellation.cancel()

    with pytest.raises(UserFacingError, match="anulată"):
        database._recover_interrupted_migration(cancellation)

    with sqlite3.connect(path) as retained:
        assert retained.execute("SELECT value FROM retained").fetchone() == ("kept",)
    assert temporary.exists()
    assert database._migration_marker_path().exists()


def test_failed_migration_is_atomic_and_can_be_retried(tmp_path, monkeypatch) -> None:
    path = tmp_path / "failed-migration.sqlite3"
    database = Database(path)
    broken = "CREATE TABLE partial_change(id INTEGER); CREATE TABLE invalid("
    monkeypatch.setattr(migrations, "MIGRATIONS", ((1, broken),))

    with pytest.raises(sqlite3.OperationalError):
        database.initialize()

    with database.connect() as connection:
        partial_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial_change'"
        ).fetchone()
        recorded = connection.execute(
            "SELECT version FROM schema_migrations WHERE version = 1"
        ).fetchone()
    assert partial_table is None
    assert recorded is None

    valid = "CREATE TABLE recovered(id INTEGER);"
    monkeypatch.setattr(migrations, "MIGRATIONS", ((1, valid),))
    database.initialize()
    with database.connect() as connection:
        recovered = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'recovered'"
        ).fetchone()
        versions = connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    assert recovered[0] == "recovered"
    assert [row[0] for row in versions] == [1]


def test_settings_survive_new_connection(tmp_path) -> None:
    database = initialized_database(tmp_path)
    settings = TuyaSettings("client", "secret", "central_europe", Currency.EUR)
    with database.connect() as connection:
        SettingsRepository(
            connection, database.client_secret_store()
        ).save_tuya(settings, NOW)
    with database.connect() as connection:
        assert SettingsRepository(
            connection, database.client_secret_store()
        ).load_tuya() == settings
        stored_keys = {
            row[0]
            for row in connection.execute("SELECT key FROM settings").fetchall()
        }
    assert "tuya.client_secret" not in stored_keys
    assert b"secret" == database.client_secret_store().get(
        "tuya-client-secret-v1"
    )


def test_non_macos_restart_uses_plain_sqlite_and_persistent_plaintext_secret(
    tmp_path,
) -> None:
    if sys.platform == "darwin":
        pytest.skip("POSIX development policy outside macOS")
    path = tmp_path / "restart.sqlite3"
    first = Database(path)
    first.initialize()
    settings = TuyaSettings("client", "secret", "central_europe", Currency.EUR)
    DatabaseSettingsStore(first).save_tuya(settings)

    restarted = Database(path)
    restarted.initialize()

    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    assert isinstance(restarted.client_secret_store(), PlaintextFileSecretStore)
    assert DatabaseSettingsStore(restarted).load_tuya() == settings
    artifact = tmp_path / "tuya-client-secret.plaintext"
    assert artifact.read_bytes() == b"secret"
    assert artifact.stat().st_mode & 0o777 == 0o600


def test_macos_default_cannot_be_switched_to_plaintext_by_environment(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("tatatuya.infrastructure.database.sys.platform", "darwin")
    monkeypatch.setenv("TATATUYA_ALLOW_PLAINTEXT_TEST_DATABASE", "1")

    database = Database(tmp_path / "production.sqlite3")

    assert database.require_cipher is True
    assert not isinstance(database.client_secret_store(), PlaintextFileSecretStore)


def test_windows_reports_deliberate_unsupported_platform(tmp_path, monkeypatch) -> None:
    path = tmp_path / "unsupported.sqlite3"
    monkeypatch.setattr("tatatuya.infrastructure.database.sys.platform", "win32")
    monkeypatch.setattr("tatatuya.infrastructure.database.os.name", "nt")

    with pytest.raises(UserFacingError) as caught:
        Database(path)

    assert caught.value.title == "Platformă neacceptată"
    assert not path.exists()


def test_keychain_denial_is_a_romanian_recovery_error(tmp_path) -> None:
    class DeniedStore:
        def get(self, account, cancellation=None):
            raise SecretStoreError("read", -25308)

        def set(self, account, value, *, label, cancellation=None):
            raise SecretStoreError("add", -25308)

        def set_if_absent(self, account, value, *, label, cancellation=None):
            raise SecretStoreError("add", -25308)

        def delete(self, account, cancellation=None):
            raise SecretStoreError("delete", -25308)

    database = initialized_database(tmp_path)
    with database.connect() as connection:
        repository = SettingsRepository(connection, DeniedStore())
        with pytest.raises(UserFacingError) as load_error:
            repository.load_tuya()
        with pytest.raises(UserFacingError) as save_error:
            repository.save_tuya(
                TuyaSettings("client", "secret", "central_europe", Currency.RON)
            )

    assert load_error.value.title == "Client Secret nu este disponibil"
    assert save_error.value.title == "Client Secret nu este disponibil"


def test_cancelled_database_transaction_rolls_back_before_commit(tmp_path) -> None:
    database = initialized_database(tmp_path)
    cancellation = CancellationContext(10)

    with pytest.raises(UserFacingError, match="anulată"):
        with database.connect(cancellation) as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at_utc) VALUES (?, ?, ?)",
                ("cancelled", "must-not-commit", NOW.isoformat()),
            )
            cancellation.cancel()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM settings WHERE key = 'cancelled'"
        ).fetchone() is None


def test_settings_cancellation_after_secret_write_starts_no_database_update(
    tmp_path,
) -> None:
    cancellation = CancellationContext(10)
    cancellation_to_trigger = cancellation

    class CancellingStore(MemorySecretStore):
        def set(self, account, value, *, label, cancellation=None):
            super().set(
                account, value, label=label, cancellation=cancellation
            )
            cancellation_to_trigger.cancel()

    secret_store = CancellingStore()
    database = Database(tmp_path / "cancel-settings.sqlite3", secret_store=secret_store)
    database.initialize()

    with pytest.raises(UserFacingError, match="anulată"):
        DatabaseSettingsStore(database).save_tuya(
            TuyaSettings("client", "new-secret", "central_europe", Currency.RON),
            cancellation=cancellation,
        )

    assert secret_store.get("tuya-client-secret-v1") == b"new-secret"
    with database.connect() as connection:
        assert SettingsRepository(connection, secret_store).get("tuya.client_id") is None


def test_database_and_parent_permissions_are_restrictive(tmp_path) -> None:
    parent = tmp_path / "application-data"
    database = Database(parent / "tatatuya.sqlite3")
    database.initialize()

    assert parent.stat().st_mode & 0o777 == 0o700
    assert database.path.stat().st_mode & 0o777 == 0o600


def test_cipher_requirement_never_falls_back_to_plaintext(tmp_path) -> None:
    path = tmp_path / "must-be-encrypted.sqlite3"
    database = Database(
        path,
        driver=sqlite3,
        secret_store=MemorySecretStore(),
        require_cipher=True,
    )

    with pytest.raises(UserFacingError) as caught:
        database.initialize()

    assert "Baza de date" in caught.value.title
    assert not path.exists()


def test_database_rejects_symlink_without_changing_target(tmp_path) -> None:
    target = tmp_path / "unrelated.txt"
    target.write_bytes(b"keep-me")
    path = tmp_path / "tatatuya.sqlite3"
    path.symlink_to(target)

    with pytest.raises(UserFacingError) as caught:
        Database(path).initialize()

    assert "Baza de date" in caught.value.title
    assert target.read_bytes() == b"keep-me"
    assert path.is_symlink()


def test_upgrade_removes_obsolete_account_uid_setting(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "upgrade.sqlite3")
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:1])
    database.initialize()
    with database.connect() as connection:
        SettingsRepository(
            connection, database.client_secret_store()
        ).set("tuya.account_uid", "obsolete-uid", NOW)

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    database.initialize()
    with database.connect() as connection:
        assert SettingsRepository(
            connection, database.client_secret_store()
        ).get("tuya.account_uid") is None
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [row[0] for row in versions] == [1, 2, 3, 4]


def test_version_two_upgrade_preserves_history_with_unknown_presence(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "version-two.sqlite3")
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:2])
    database.initialize()
    with database.connect() as connection:
        timestamp = NOW.isoformat()
        connection.execute(
            """
            INSERT INTO devices(
                device_id, name, raw_device_json,
                first_seen_at_utc, last_seen_at_utc
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("meter-1", "Casa", "{}", timestamp, timestamp),
        )
        cursor = connection.execute(
            """
            INSERT INTO readings(
                device_id, recorded_at_utc, raw_value, scale, source_unit,
                value_kwh, source, raw_status_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meter-1",
                timestamp,
                "12345",
                2,
                "kWh",
                "123.45",
                "batch",
                '{"status":"legacy"}',
            ),
        )
        assert cursor.lastrowid is not None
        legacy_reading_id = int(cursor.lastrowid)

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    database.initialize()
    with database.connect() as connection:
        upgraded_device = DeviceRepository(connection).get("meter-1")
        upgraded_reading = ReadingRepository(connection).get(legacy_reading_id)

    assert upgraded_device is not None
    assert upgraded_device.energy_eligibility is EnergyEligibility.UNKNOWN
    assert upgraded_device.present_in_tuya is None
    assert upgraded_reading is not None
    assert upgraded_reading.value_kwh == Decimal("123.45")
    assert upgraded_reading.raw_specification_json == "{}"


def test_version_three_upgrade_adds_cloud_provenance_without_rewriting_history(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "version-three.sqlite3")
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:3])
    database.initialize()
    with database.connect() as connection:
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
        cursor = connection.execute(
            """
            INSERT INTO readings(
                device_id, recorded_at_utc, raw_value, scale, source_unit,
                value_kwh, source, raw_status_json, raw_specification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meter-1",
                NOW.isoformat(),
                "12345",
                2,
                "kWh",
                "123.45",
                "batch",
                "{}",
                "{}",
            ),
        )
        assert cursor.lastrowid is not None
        legacy_id = int(cursor.lastrowid)

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    database.initialize()
    with database.connect() as connection:
        upgraded = ReadingRepository(connection).get(legacy_id)
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert upgraded is not None
    assert upgraded.external_event_key is None
    assert upgraded.cloud_day_local_date is None
    assert {
        "readings_external_event_key",
        "readings_cloud_daily_device_date",
    } <= indexes


def test_device_upsert_preserves_history_and_equal_readings(tmp_path) -> None:
    database = initialized_database(tmp_path)
    first_device = Device("meter-1", "Casa Veche", online=True, raw_device_json='{"v":1}')
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        saved_device = devices.upsert(first_device, NOW)
        readings = ReadingRepository(connection)
        first = readings.add(
            Reading("meter-1", NOW, "12345", 2, "kWh", Decimal("123.45"), "batch", "{}")
        )
        second = readings.add(
            Reading("meter-1", NOW, "12345", 2, "kWh", Decimal("123.45"), "batch", "{}")
        )
        updated = devices.upsert(
            Device("meter-1", "Casa Nouă", online=False, raw_device_json='{"v":2}'),
            NOW + timedelta(days=1),
        )
        stored = readings.list_for_device("meter-1")

    assert first.id != second.id
    assert [item.value_kwh for item in stored] == [Decimal("123.45"), Decimal("123.45")]
    assert updated.name == "Casa Nouă"
    assert updated.first_seen_at_utc == saved_device.first_seen_at_utc


def test_cloud_daily_import_is_idempotent_by_event_and_local_day(tmp_path) -> None:
    database = initialized_database(tmp_path)
    imported = NOW + timedelta(hours=1)
    candidate = Reading(
        "meter-1",
        NOW,
        "10000",
        2,
        "kWh",
        Decimal("100"),
        "cloud_daily",
        '{"code":"forward_energy_total"}',
        raw_specification_json='{"scale":2}',
        external_event_key="event-key-1",
        imported_at_utc=imported,
        specification_observed_at_utc=NOW,
        source_code="forward_energy_total",
        cloud_day_local_date=NOW.date(),
        cloud_day_timezone="Europe/Amsterdam",
        cloud_day_utc_offset="+02:00",
    )
    with database.connect() as connection:
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
        repository = ReadingRepository(connection)
        first = repository.import_cloud_daily((candidate,))
        changed_diagnostics = replace(
            candidate,
            raw_status_json='{"unrelated":"changed"}',
            raw_specification_json='{"extra":true,"scale":2}',
        )
        repeated_results = [
            repository.import_cloud_daily((changed_diagnostics,))
            for _ in range(100)
        ]
        repeated = repeated_results[-1]
        later_same_day = replace(
            candidate,
            recorded_at_utc=NOW + timedelta(hours=2),
            raw_value="10100",
            value_kwh=Decimal("101"),
            external_event_key="event-key-2",
        )
        overlapping = repository.import_cloud_daily((later_same_day,))
        stored = repository.list_for_device("meter-1")

    assert (first.new_count, first.reused_count) == (1, 0)
    assert (repeated.new_count, repeated.reused_count) == (0, 1)
    assert all(result.readings[0].id == first.readings[0].id for result in repeated_results)
    assert (overlapping.new_count, overlapping.reused_count) == (0, 1)
    assert first.readings[0].id == repeated.readings[0].id == overlapping.readings[0].id
    assert len(stored) == 1
    assert stored[0].raw_status_json == candidate.raw_status_json


def test_latest_readings_for_all_devices_uses_timestamp_then_id(tmp_path) -> None:
    database = initialized_database(tmp_path)
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        readings = ReadingRepository(connection)
        for device_id in ("meter-1", "meter-2", "meter-empty"):
            devices.upsert(Device(device_id, device_id), NOW)
        readings.add(
            Reading("meter-1", NOW, "100", 0, "kWh", Decimal("100"), "batch", "{}")
        )
        readings.add(
            Reading("meter-1", NOW, "101", 0, "kWh", Decimal("101"), "batch", "{}")
        )
        readings.add(
            Reading(
                "meter-2", NOW + timedelta(hours=1), "200", 0, "kWh",
                Decimal("200"), "batch", "{}",
            )
        )
        latest = readings.latest_by_device()

    assert set(latest) == {"meter-1", "meter-2"}
    assert latest["meter-1"].value_kwh == Decimal("101")
    assert latest["meter-2"].value_kwh == Decimal("200")


def test_product_change_invalidates_cached_energy_specification(tmp_path) -> None:
    database = initialized_database(tmp_path)
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        devices.upsert(
            Device(
                "meter-1", "Casa", product_id="old-product",
                energy_code="forward_energy_total", energy_unit="kWh", energy_scale=2,
            ),
            NOW,
        )
        changed = devices.upsert(
            Device("meter-1", "Casa", product_id="new-product"),
            NOW + timedelta(seconds=1),
        )

    assert changed.product_id == "new-product"
    assert changed.energy_code is None
    assert changed.energy_unit is None
    assert changed.energy_scale is None


def test_unchanged_product_preserves_cached_energy_specification(tmp_path) -> None:
    database = initialized_database(tmp_path)
    with database.connect() as connection:
        devices = DeviceRepository(connection)
        devices.upsert(
            Device(
                "meter-1", "Casa", product_id="same-product",
                energy_code="forward_energy_total", energy_unit="kWh", energy_scale=2,
            ),
            NOW,
        )
        updated = devices.upsert(
            Device("meter-1", "Casa actualizată", product_id="same-product"),
            NOW + timedelta(seconds=1),
        )

    assert updated.energy_code == "forward_energy_total"
    assert updated.energy_unit == "kWh"
    assert updated.energy_scale == 2


def test_calculation_is_immutable_after_settings_and_preference_changes(tmp_path) -> None:
    database = initialized_database(tmp_path)
    with database.connect() as connection:
        DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
        readings = ReadingRepository(connection)
        start = readings.add(
            Reading("meter-1", NOW, "10000", 2, "kWh", Decimal("100"), "batch", "{}")
        )
        end = readings.add(
            Reading(
                "meter-1", NOW + timedelta(days=30), "11250", 2, "kWh",
                Decimal("112.5"), "batch", "{}",
            )
        )
        calculation = calculate_period(
            start, end, Decimal("0.8"), Currency.RON, NOW + timedelta(days=30)
        )
        saved = CalculationRepository(connection).add(calculation)
        DevicePreferenceRepository(connection).save_price(
            "meter-1", Decimal("1.25"), Currency.EUR, NOW + timedelta(days=31)
        )
        SettingsRepository(
            connection, database.client_secret_store()
        ).set("application.currency", "EUR", NOW)

    with database.connect() as connection:
        reloaded = CalculationRepository(connection).latest_for_device("meter-1")
    assert reloaded == saved
    assert reloaded is not None
    assert reloaded.currency is Currency.RON
    assert reloaded.unit_price == Decimal("0.8")
    assert reloaded.total == Decimal("10")


def test_failed_transaction_rolls_back(tmp_path) -> None:
    database = initialized_database(tmp_path)
    try:
        with database.connect() as connection:
            DeviceRepository(connection).upsert(Device("meter-1", "Casa"), NOW)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with database.connect() as connection:
        assert DeviceRepository(connection).get("meter-1") is None
