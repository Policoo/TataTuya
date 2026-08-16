#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPEC_FILE="${PROJECT_ROOT}/packaging/tatatuya.spec"
WORK_DIRECTORY="${PROJECT_ROOT}/build/pyinstaller"
DIST_DIRECTORY="${PROJECT_ROOT}/dist"
APP_EXECUTABLE="${DIST_DIRECTORY}/TataTuya.app/Contents/MacOS/TataTuya"
SMOKE_KEYCHAIN_SERVICE="ro.tatatuya.app.ci-smoke.${GITHUB_RUN_ID:-$$}"
SMOKE_SENTINEL="tatatuya-ci-encrypted-restart-probe-20260807"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: the macOS application can only be built on macOS." >&2
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Error: the distribution build requires an Apple Silicon Mac (arm64)." >&2
  exit 1
fi

python -m PyInstaller \
  --clean \
  --noconfirm \
  --workpath "${WORK_DIRECTORY}" \
  --distpath "${DIST_DIRECTORY}" \
  "${SPEC_FILE}"

if [[ ! -x "${APP_EXECUTABLE}" ]]; then
  echo "Error: PyInstaller did not create the expected executable." >&2
  exit 1
fi

if [[ "$(lipo -archs "${APP_EXECUTABLE}")" != "arm64" ]]; then
  echo "Error: the resulting executable is not arm64-only." >&2
  exit 1
fi

SMOKE_DATA_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/tatatuya-smoke.XXXXXX")"
cleanup() {
  local cleanup_status=0
  TATATUYA_SMOKE_KEYCHAIN_SERVICE="${SMOKE_KEYCHAIN_SERVICE}" \
    "${APP_EXECUTABLE}" --smoke-test-clean-keychain || cleanup_status=$?
  TATATUYA_SMOKE_KEYCHAIN_SERVICE="${SMOKE_KEYCHAIN_SERVICE}" \
    "${APP_EXECUTABLE}" --smoke-test-assert-clean-keychain || cleanup_status=$?
  rm -rf -- "${SMOKE_DATA_DIRECTORY}" || cleanup_status=$?
  return "${cleanup_status}"
}
trap cleanup EXIT

TATATUYA_DATA_DIR="${SMOKE_DATA_DIRECTORY}" \
  TATATUYA_SMOKE_KEYCHAIN_SERVICE="${SMOKE_KEYCHAIN_SERVICE}" \
  TATATUYA_SMOKE_SENTINEL="${SMOKE_SENTINEL}" \
  QT_QPA_PLATFORM="offscreen" \
  "${APP_EXECUTABLE}" --smoke-test

TATATUYA_DATA_DIR="${SMOKE_DATA_DIRECTORY}" \
  TATATUYA_SMOKE_KEYCHAIN_SERVICE="${SMOKE_KEYCHAIN_SERVICE}" \
  TATATUYA_SMOKE_SENTINEL="${SMOKE_SENTINEL}" \
  QT_QPA_PLATFORM="offscreen" \
  "${APP_EXECUTABLE}" --smoke-test

if [[ ! -f "${SMOKE_DATA_DIRECTORY}/tatatuya.sqlite3" ]]; then
  echo "Error: the packaged application did not initialize its database." >&2
  exit 1
fi

if [[ "$(head -c 15 "${SMOKE_DATA_DIRECTORY}/tatatuya.sqlite3")" == "SQLite format 3" ]]; then
  echo "Error: the packaged application created a plaintext SQLite database." >&2
  exit 1
fi

if grep -R -a -F -q -- "${SMOKE_SENTINEL}" "${SMOKE_DATA_DIRECTORY}"; then
  echo "Error: encrypted smoke-test data is visible in a database file." >&2
  exit 1
fi

SQLCIPHER_EXTENSION="$(find "${DIST_DIRECTORY}/TataTuya.app" -type f -path '*sqlcipher3*' -name '*.so' -print -quit)"
if [[ -z "${SQLCIPHER_EXTENSION}" ]]; then
  echo "Error: the packaged application does not contain the SQLCipher extension." >&2
  exit 1
fi
if [[ "$(lipo -archs "${SQLCIPHER_EXTENSION}")" != "arm64" ]]; then
  echo "Error: the packaged SQLCipher extension is not arm64-only." >&2
  exit 1
fi
if otool -L "${SQLCIPHER_EXTENSION}" | grep -q '/usr/lib/libsqlite3'; then
  echo "Error: the packaged database extension links to system plaintext SQLite." >&2
  exit 1
fi

echo "Application created: ${DIST_DIRECTORY}/TataTuya.app"
