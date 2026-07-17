#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
VERSION="${1:-}"
APP_PATH="${2:-${PROJECT_ROOT}/dist/TataTuya.app}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Eroare: imaginea DMG poate fi creată numai pe macOS." >&2
  exit 1
fi

if [[ -z "${VERSION}" ]]; then
  echo "Utilizare: $0 <versiune> [cale-către-TataTuya.app]" >&2
  exit 1
fi

VERSION="${VERSION#v}"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Eroare: versiunea trebuie să fie o versiune semantică, de exemplu 0.1.0." >&2
  exit 1
fi

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Eroare: aplicația nu există la ${APP_PATH}." >&2
  exit 1
fi

DIST_DIRECTORY="${PROJECT_ROOT}/dist"
DMG_PATH="${DIST_DIRECTORY}/TataTuya-${VERSION}-arm64.dmg"
if [[ -e "${DMG_PATH}" ]]; then
  echo "Eroare: fișierul există deja: ${DMG_PATH}" >&2
  exit 1
fi

STAGING_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/tatatuya-dmg.XXXXXX")"
cleanup() {
  rm -rf -- "${STAGING_DIRECTORY}"
}
trap cleanup EXIT

ditto "${APP_PATH}" "${STAGING_DIRECTORY}/TataTuya.app"
ln -s /Applications "${STAGING_DIRECTORY}/Applications"
mkdir -p "${DIST_DIRECTORY}"

hdiutil create \
  -volname "TataTuya ${VERSION}" \
  -srcfolder "${STAGING_DIRECTORY}" \
  -format UDZO \
  "${DMG_PATH}"

echo "Imagine creată: ${DMG_PATH}"
