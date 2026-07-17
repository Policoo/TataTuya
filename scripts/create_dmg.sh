#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
VERSION="${1:-}"
APP_PATH="${2:-${PROJECT_ROOT}/dist/TataTuya.app}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: the DMG image can only be created on macOS." >&2
  exit 1
fi

if [[ -z "${VERSION}" ]]; then
  echo "Usage: $0 <version> [path-to-TataTuya.app]" >&2
  exit 1
fi

VERSION="${VERSION#v}"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Error: the version must use semantic versioning, for example 0.1.0." >&2
  exit 1
fi

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Error: the application does not exist at ${APP_PATH}." >&2
  exit 1
fi

DIST_DIRECTORY="${PROJECT_ROOT}/dist"
DMG_PATH="${DIST_DIRECTORY}/TataTuya-${VERSION}-arm64.dmg"
if [[ -e "${DMG_PATH}" ]]; then
  echo "Error: the file already exists: ${DMG_PATH}" >&2
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

echo "Image created: ${DMG_PATH}"
