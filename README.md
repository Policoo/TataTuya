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

Saved readings and calculations remain available when Tuya credentials are
missing. In that state, refresh, individual status, and Tuya Cloud import are
disabled, while Calculate, History, and cached device information stay usable.

### Tuya Cloud history capability

TataTuya contains the bounded, read-only implementation for
`GET /v2.1/cloud/thing/{device_id}/report-logs`, including sparse daily import
of the most recent seven local calendar days and idempotent local persistence.
With complete Tuya settings, Calculate exposes the import action. Tuya documents
the endpoint as status values reported by a requested DP, and defines a numeric
DP's wire value through that DP model's unit and scale. TataTuya reads the exact
cumulative-energy specification before and after the log query and rejects a
change instead of guessing.

The project must subscribe to and authorize **IoT Core**, link the Tuya/Smart
Life app account, and grant access to the device. Device Log availability still
depends on the project, service edition, device, and retention period. The free
Device Log view documents seven days; imported data is not guaranteed to match
everything visible in the consumer app.

## Development

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,package]"
python -m pytest
python -m tatatuya
```

### Read-only Tuya response inspector

To inspect the full response envelopes returned to your configured account,
run the terminal UI from the repository checkout:

```bash
.venv/bin/python scripts/inspect_tuya.py
```

It reads the saved TataTuya database by default. Use `--database PATH` when the
database is elsewhere. The inspector provides allowlisted `GET` actions for the
associated-device list, device specification, individual and batch status, and
the v2.0/v2.1 status-report-log endpoints. Select a meter with `d`, edit the DP
code with `c`, and use `x` to exit while printing the current sanitized response
to ordinary terminal output for copying. `q` exits without printing.

The inspector never persists readings or sends commands. It displays device IDs
locally because they are needed to diagnose response mismatches, but redacts
access tokens, client secrets, local keys, passwords, and other sensitive fields.
Do not attach its output to an issue without reviewing the device and account
metadata first.

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

### Clean-Mac release rehearsal

Before publishing the draft GitHub Release, use a clean Apple Silicon Mac with
no TataTuya development checkout or existing TataTuya data:

1. Download the DMG from the draft release, install the app, and launch it with
   the documented Gatekeeper workaround.
2. Confirm that the unconfigured app directs the user to Settings and creates
   `~/Library/Application Support/TataTuya/tatatuya.sqlite3`.
3. Enter real Tuya credentials in Settings, test the connection, save, and
   confirm that the meter table refreshes without developer tools.
4. Refresh a supported meter twice, calculate a cost between its two saved
   readings, and confirm that the calculation appears in History after an app
   restart.
5. Inspect the local log and release artifact for credentials, then publish the
   draft only if every preceding check passes.

## Troubleshooting

- If macOS reports that the developer cannot be verified, use the
  Control-click → `Open` steps above; do not disable Gatekeeper globally.
- If configuration is missing, open the application's settings screen. There
  is no first-run wizard.
- If authentication succeeds but device listing fails, verify that the Tuya
  cloud project grants access to the associated-device listing used by the app.
- If cloud import reports an unavailable service or permission, confirm that
  IoT Core is subscribed and authorized for the cloud project and that the app
  account/device is linked. Local readings continue to work.
- The local log is stored at
  `~/Library/Application Support/TataTuya/tatatuya.log` and does not include
  Tuya secrets.
