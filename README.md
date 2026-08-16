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
calculations are not deleted when the application closes or is updated. The
complete database is encrypted with SQLCipher. TataTuya stores the random
database key and the Tuya Client Secret as separate generic passwords in the
login Keychain; the Client Secret is not stored inside the database.

On later Settings visits the Client Secret field remains empty and indicates
that a secret is stored. Leave it empty to preserve the current secret, or enter
a replacement. If macOS denies Keychain access, unlock the login Keychain and
retry the explicit Settings or Tuya action.

Version one uses a local-key recovery policy. Losing the TataTuya database-key
item from the login Keychain makes the encrypted history unreadable. Copying
only `tatatuya.sqlite3` to another Mac is not a complete backup. Use a tested
Time Machine/Mac migration process that includes the login Keychain; TataTuya
does not provide a portable database export in this version.

Saved readings and calculations remain available when Tuya credentials are
missing, denied, or corrupt. In that state, refresh, individual status, and Tuya
Cloud import are disabled, while Calculate, History, currency, and cached device
information stay usable.

Linux and other POSIX development systems outside macOS deliberately use
ordinary SQLite and store Client Secret in the plainly named
`tuya-client-secret.plaintext` file beside the development database. The
directory and files are restricted to the current user where the platform
permits, but this is not encryption. Use test-only Tuya credentials on
development machines. Windows development is not supported. macOS production
builds cannot select this backend and continue to require SQLCipher and
Keychain.

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

The first script creates `dist/TataTuya.app`, verifies the executable and
SQLCipher extension architecture/linkage, creates and reopens a synthetic
encrypted database through a disposable Keychain namespace, and cleans up that
exact namespace. The second creates `dist/TataTuya-0.1.0-arm64.dmg` with the
application and an Applications shortcut.

Release preparation is triggered by a Git tag that exactly matches the version
in `pyproject.toml`, for example `v0.1.0`. The ARM64 workflow runs the checks,
builds the DMG with read-only repository permission, then a separate publication
job attaches the DMG, SHA-256 checksum, and SBOM to a draft GitHub Release. The release remains
unpublished until the Phase 12 rehearsal on a clean Mac confirms installation,
database initialization, opening the settings screen, and connection testing.
Never place real credentials in source files, configuration files, or release
artifacts.

Pull requests and pushes to `main` also run read-only Linux correctness, both Qt
test orders, macOS dependency-aware type checks, and the complete Apple Silicon
SQLCipher-backed suite—including disposable Keychain checks—before a release tag
exists. Configure those jobs as required branch checks in the repository
settings once their signal is stable.

### Clean-Mac release rehearsal

Before publishing the draft GitHub Release, use a clean Apple Silicon Mac with
no TataTuya development checkout or existing TataTuya data:

1. Download the DMG from the draft release, install the app, and launch it with
   the documented Gatekeeper workaround.
2. Confirm that the unconfigured app directs the user to Settings, creates an
   encrypted `~/Library/Application Support/TataTuya/tatatuya.sqlite3`, and uses
   `0700`/`0600` permissions for the data directory and sensitive files.
3. Enter real Tuya credentials in Settings, test the connection, save, and
   confirm that the meter table refreshes without developer tools.
4. Quit and reopen the app, confirm the Keychain-backed configuration and local
   history survive, then refresh a supported meter twice and calculate a cost between its two saved
   readings, and confirm that the calculation appears in History after an app
   restart.
5. Rehearse a populated synthetic plaintext database upgrade; confirm the old
   Client Secret is absent from logical settings and correct/wrong/missing key
   behavior is fail-closed.
6. Inspect the local log and release artifact for credentials, verify the DMG
   checksum/SBOM and SQLCipher native linkage, then publish the
   draft only if every preceding check passes.

## Troubleshooting

- If macOS reports that the developer cannot be verified, use the
  Control-click → `Open` steps above; do not disable Gatekeeper globally.
- If configuration is missing, open the application's settings screen. There
  is no first-run wizard.
- If TataTuya reports that Keychain or the protected database is unavailable,
  do not delete or rename the database. Unlock the login Keychain, restore its
  TataTuya key items through the supported Mac backup/migration process, and
  retry. The application intentionally does not create an empty replacement.
- If authentication succeeds but device listing fails, verify that the Tuya
  cloud project grants access to the associated-device listing used by the app.
- If cloud import reports an unavailable service or permission, confirm that
  IoT Core is subscribed and authorized for the cloud project and that the app
  account/device is linked. Local readings continue to work.
- The local log is stored at
  `~/Library/Application Support/TataTuya/tatatuya.log` and does not include
  Tuya secrets.
