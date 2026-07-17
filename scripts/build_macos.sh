#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPEC_FILE="${PROJECT_ROOT}/packaging/tatatuya.spec"
WORK_DIRECTORY="${PROJECT_ROOT}/build/pyinstaller"
DIST_DIRECTORY="${PROJECT_ROOT}/dist"
APP_EXECUTABLE="${DIST_DIRECTORY}/TataTuya.app/Contents/MacOS/TataTuya"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Eroare: aplicația macOS poate fi construită numai pe macOS." >&2
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Eroare: build-ul de distribuție necesită un Mac Apple Silicon (arm64)." >&2
  exit 1
fi

python -m PyInstaller \
  --clean \
  --noconfirm \
  --workpath "${WORK_DIRECTORY}" \
  --distpath "${DIST_DIRECTORY}" \
  "${SPEC_FILE}"

if [[ ! -x "${APP_EXECUTABLE}" ]]; then
  echo "Eroare: PyInstaller nu a creat executabilul așteptat." >&2
  exit 1
fi

if [[ "$(lipo -archs "${APP_EXECUTABLE}")" != "arm64" ]]; then
  echo "Eroare: executabilul rezultat nu este exclusiv arm64." >&2
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
  echo "Eroare: aplicația împachetată nu a inițializat baza de date." >&2
  exit 1
fi

echo "Aplicație creată: ${DIST_DIRECTORY}/TataTuya.app"
