#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPEC_FILE="${PROJECT_ROOT}/packaging/tatatuya.spec"
WORK_DIRECTORY="${PROJECT_ROOT}/build/pyinstaller"
DIST_DIRECTORY="${PROJECT_ROOT}/dist"
APP_EXECUTABLE="${DIST_DIRECTORY}/TataTuya.app/Contents/MacOS/TataTuya"

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
  rm -rf -- "${SMOKE_DATA_DIRECTORY}"
}
trap cleanup EXIT

TATATUYA_DATA_DIR="${SMOKE_DATA_DIRECTORY}" \
  QT_QPA_PLATFORM="offscreen" \
  "${APP_EXECUTABLE}" --smoke-test

if [[ ! -f "${SMOKE_DATA_DIRECTORY}/tatatuya.sqlite3" ]]; then
  echo "Error: the packaged application did not initialize its database." >&2
  exit 1
fi

echo "Application created: ${DIST_DIRECTORY}/TataTuya.app"
