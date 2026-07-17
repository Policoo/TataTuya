# TataTuya

TataTuya is a Romanian-language desktop application that stores cumulative
readings from Tuya energy meters and calculates the exact cost between two
readings. It reads data from Tuya but never sends commands or modifies devices.

## Installation on Apple Silicon

1. Download `TataTuya-<version>-arm64.dmg` from GitHub Releases.
2. Open the DMG and drag `TataTuya.app` onto the `Applications` shortcut.
3. Eject the DMG, then open TataTuya from the Applications folder.
4. Open the application's settings, enter the Tuya Client ID, Client Secret,
   and region, test the connection, and save.

The initial release is distributed without Apple signing or notarization. On
first launch, macOS may block the usual open action. In Finder, open
`Applications`, Control-click or right-click TataTuya, choose `Open`, and then
confirm `Open` again. This exception is required only once.

Local data is stored in
`~/Library/Application Support/TataTuya/tatatuya.sqlite3`. Readings and
calculations are not deleted when the application closes or is updated.

## Development

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,package]"
python -m pytest
python -m tatatuya
```

Building the distribution requires an Apple Silicon Mac:

```bash
./scripts/build_macos.sh
./scripts/create_dmg.sh 0.1.0
```

The first script creates `dist/TataTuya.app`, verifies the executable
architecture, and runs a smoke test for bundled resources and migrations. The
second creates `dist/TataTuya-0.1.0-arm64.dmg` with the application and an
Applications shortcut.

Release preparation is triggered by a Git tag that exactly matches the version
in `pyproject.toml`, for example `v0.1.0`. The ARM64 workflow runs the checks,
builds the DMG, and attaches it to a draft GitHub Release. The release remains
unpublished until the Phase 12 rehearsal on a clean Mac confirms installation,
database initialization, opening the settings screen, and connection testing.
Never place real credentials in source files, configuration files, or release
artifacts.

## Troubleshooting

- If macOS reports that the developer cannot be verified, use the
  Control-click → `Open` steps above; do not disable Gatekeeper globally.
- If configuration is missing, open the application's settings screen. There
  is no first-run wizard.
- The local log is stored at
  `~/Library/Application Support/TataTuya/tatatuya.log` and does not include
  Tuya secrets.
