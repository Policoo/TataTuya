from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVELOPER_FACING_TEXT_FILES = (
    "README.md",
    "pyproject.toml",
    ".github/workflows/release-macos.yml",
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
    assert "actions/upload-artifact@v7" in workflow
    assert "python -m venv .venv" in workflow
    assert "python -m pyright --pythonversion 3.12" in workflow


def test_release_workflow_only_updates_draft_releases() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-macos.yml"
    ).read_text(encoding="utf-8")

    assert "gh release create" in workflow
    assert "--draft" in workflow
    assert "--json isDraft" in workflow
    assert '"${RELEASE_IS_DRAFT}" != "true"' in workflow


def test_packaging_dependencies_are_declared() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
        metadata = tomllib.load(project_file)

    optional = metadata["project"]["optional-dependencies"]
    assert any(item.startswith("pyinstaller") for item in optional["package"])
    assert any(item.startswith("pyright") for item in optional["dev"])


def test_developer_facing_release_text_has_no_romanian_diacritics() -> None:
    for relative_path in DEVELOPER_FACING_TEXT_FILES:
        content = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        unexpected = sorted(ROMANIAN_SPECIFIC_CHARACTERS.intersection(content))
        assert not unexpected, f"{relative_path} contains Romanian text: {unexpected}"
