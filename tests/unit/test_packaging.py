from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import tomllib

import pytest

from tatatuya.application import _disposable_smoke_keychain_service


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVELOPER_FACING_TEXT_FILES = (
    "README.md",
    "pyproject.toml",
    ".github/workflows/release-macos.yml",
    ".github/workflows/tests.yml",
    "scripts/build_macos.sh",
    "scripts/create_dmg.sh",
)
ROMANIAN_SPECIFIC_CHARACTERS = frozenset("ăâîșțĂÂÎȘȚ")


def test_pyinstaller_spec_declares_arm64_resources_and_migrations() -> None:
    spec_path = PROJECT_ROOT / "packaging" / "tatatuya.spec"
    source = spec_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert 'target_arch="arm64"' in source
    assert '"styles.qss"' in source
    assert '"icons"' in source
    assert '"tatatuya.infrastructure.migrations"' in source
    assert '"PySide6.QtNetwork"' in source
    assert '"sqlcipher3.dbapi2"' in source
    assert '"Security"' in source
    assert 'bundle_identifier="ro.tatatuya.app"' in source
    assert '"NSPrincipalClass": "NSApplication"' in source


def test_macos_scripts_have_valid_shell_syntax_and_are_executable() -> None:
    for relative_path in ("scripts/build_macos.sh", "scripts/create_dmg.sh"):
        script = PROJECT_ROOT / relative_path
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert script.stat().st_mode & 0o111


def test_build_script_rejects_non_arm64_output() -> None:
    script = (PROJECT_ROOT / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )

    assert '"$(uname -m)" != "arm64"' in script
    assert 'lipo -archs "${APP_EXECUTABLE}"' in script


def test_release_workflow_builds_on_arm64_macos() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-macos.yml"
    ).read_text(encoding="utf-8")

    runner = re.search(r"runs-on: (macos-[^\s]+)", workflow)
    assert runner is not None
    assert not runner.group(1).endswith("-intel")
    assert "./scripts/build_macos.sh" in workflow
    assert "./scripts/create_dmg.sh" in workflow
    assert "gh release upload" in workflow
    assert "contents: write" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "python -m venv .venv" in workflow
    assert "--require-hashes --only-binary=:all:" in workflow
    assert (
        ".venv/bin/python -m pip install --no-deps --no-build-isolation ."
        in workflow
    )
    assert "python -m pyright --pythonversion 3.12" in workflow
    assert (
        "python -m pip_audit --strict --requirement requirements-macos.lock "
        "--disable-pip"
    ) in workflow
    assert "python -m pip_audit --strict\n" not in workflow
    assert "qt-lifecycle:" in workflow
    assert "tests/unit/test_tuya_client.py tests/ui/test_main_window.py" in workflow
    assert "tests/ui/test_main_window.py tests/unit/test_tuya_client.py" in workflow
    assert "needs: qt-lifecycle" in workflow
    assert "fetch-depth: 0" in workflow
    assert "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e" in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "GITLEAKS_ENABLE_COMMENTS: false" in workflow
    sbom = workflow.split("      - name: Create checksum and dependency SBOM", maxsplit=1)[1]
    assert "--requirement requirements-macos.lock" in sbom
    assert "--disable-pip" in sbom
    assert "--format cyclonedx-json" in sbom


def test_release_workflow_only_updates_draft_releases() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-macos.yml"
    ).read_text(encoding="utf-8")

    assert "gh release create" in workflow
    assert "--draft" in workflow
    assert "--json isDraft" in workflow
    assert '"${RELEASE_IS_DRAFT}" != "true"' in workflow


def test_premerge_workflow_runs_read_only_correctness_and_native_gates() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "tests.yml"
    ).read_text(encoding="utf-8")
    macos_security = workflow.split("  macos-security:", maxsplit=1)[1]

    assert "pull_request:" in workflow
    assert "branches: [main]" in workflow
    assert workflow.count("contents: read") == 4
    assert "contents: write" not in workflow
    assert workflow.count("persist-credentials: false") == 3
    assert "python -m ruff check ." in workflow
    assert "python -m pyright --pythonversion 3.12" in workflow
    assert "python -m pytest\n" in workflow
    assert "python -m pip check" in workflow
    assert "tests/unit/test_tuya_client.py tests/ui/test_main_window.py" in workflow
    assert "tests/ui/test_main_window.py tests/unit/test_tuya_client.py" in workflow
    assert "runs-on: macos-15" in workflow
    assert "--require-hashes --only-binary=:all:" in workflow
    assert "python -m pyright --pythonversion 3.12" in macos_security
    assert "Run complete macOS SQLCipher-backed gate" in macos_security
    assert "run: python -m pytest\n" in macos_security
    assert "python -m pytest -m macos_keychain" not in macos_security
    assert (
        ".venv/bin/python -m pip install --no-deps --no-build-isolation ."
        in workflow
    )


def test_packaging_dependencies_are_declared() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
        metadata = tomllib.load(project_file)

    optional = metadata["project"]["optional-dependencies"]
    assert any(item.startswith("pyinstaller") for item in optional["package"])
    assert any(item.startswith("pyright") for item in optional["dev"])
    assert any(item.startswith("sqlcipher3==") for item in metadata["project"]["dependencies"])


def test_pyright_checks_sources_without_generated_build_copies() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
        metadata = tomllib.load(project_file)

    pyright = metadata["tool"]["pyright"]
    assert pyright["include"] == ["src", "tests", "scripts"]
    assert pyright["exclude"] == ["build", "dist"]


def test_release_publication_job_does_not_execute_repository_code() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-macos.yml"
    ).read_text(encoding="utf-8")
    publication = workflow.split("  publish-draft:", maxsplit=1)[1]

    assert "actions/checkout" not in publication
    assert "./scripts/" not in publication
    assert "python " not in publication
    assert "actions: read" in publication
    assert "contents: write" in publication


def test_build_smoke_requires_encrypted_database_and_sqlcipher() -> None:
    script = (PROJECT_ROOT / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )

    assert script.splitlines().count('  "${APP_EXECUTABLE}" --smoke-test') == 2
    assert "SQLite format 3" in script
    assert "TATATUYA_SMOKE_SENTINEL" in script
    assert "--smoke-test-clean-keychain" in script
    assert "--smoke-test-assert-clean-keychain" in script
    assert "grep -R -a -F -q" in script
    assert "sqlcipher3" in script
    assert "/usr/lib/libsqlite3" in script


def test_secret_scanning_covers_historical_tuya_device_identifiers() -> None:
    config = (PROJECT_ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "security.yml"
    ).read_text(encoding="utf-8")

    assert "tuya-device-id-assignment" in config
    assert "[A-Za-z0-9]{22}" in config
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "GITLEAKS_ENABLE_COMMENTS: false" in workflow


def test_security_audit_is_scoped_to_the_hash_locked_dependency_graph() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "security.yml"
    ).read_text(encoding="utf-8")

    assert (
        "python -m pip_audit --strict --requirement requirements-macos.lock "
        "--disable-pip"
    ) in workflow
    assert "python -m pip_audit --strict\n" not in workflow


def test_developer_facing_release_text_has_no_romanian_diacritics() -> None:
    for relative_path in DEVELOPER_FACING_TEXT_FILES:
        content = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        unexpected = sorted(ROMANIAN_SPECIFIC_CHARACTERS.intersection(content))
        assert not unexpected, f"{relative_path} contains Romanian text: {unexpected}"


def test_documentation_limits_plaintext_development_to_posix() -> None:
    documents = [
        (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "README.md",
            "docs/PRODUCT_SPEC.md",
            "docs/ARCHITECTURE.md",
        )
    ]

    assert all("Non-macOS development" not in document for document in documents)
    assert all("POSIX" in document for document in documents)
    assert all("Windows" in document for document in documents)


@pytest.mark.parametrize(
    "service",
    [
        None,
        "ro.tatatuya.app",
        "ro.tatatuya.app.test.",
        "ro.tatatuya.app.ci-smoke.",
        "unrelated.test",
    ],
)
def test_macos_smoke_rejects_missing_or_nondisposable_keychain_service(
    monkeypatch, service
) -> None:
    monkeypatch.setattr("tatatuya.application.sys.platform", "darwin")
    if service is None:
        monkeypatch.delenv("TATATUYA_SMOKE_KEYCHAIN_SERVICE", raising=False)
    else:
        monkeypatch.setenv("TATATUYA_SMOKE_KEYCHAIN_SERVICE", service)

    with pytest.raises(RuntimeError, match="disposable Keychain"):
        _disposable_smoke_keychain_service()


def test_macos_smoke_accepts_unique_test_keychain_service(monkeypatch) -> None:
    service = "ro.tatatuya.app.test.synthetic-run-id"
    monkeypatch.setattr("tatatuya.application.sys.platform", "darwin")
    monkeypatch.setenv("TATATUYA_SMOKE_KEYCHAIN_SERVICE", service)

    assert _disposable_smoke_keychain_service() == service
