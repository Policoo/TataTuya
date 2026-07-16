from pathlib import Path

from tatatuya.paths import application_data_dir, database_path


def test_explicit_data_path_override() -> None:
    assert application_data_dir("/tmp/tatatuya-test") == Path("/tmp/tatatuya-test")
    assert database_path("/tmp/tatatuya-test") == Path("/tmp/tatatuya-test/tatatuya.sqlite3")

