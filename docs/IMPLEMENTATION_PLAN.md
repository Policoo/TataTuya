# TataTuya Implementation Plan

## Status legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete and verified

The phases are ordered to establish testable business behavior before relying on
the GUI. A phase is complete only when its acceptance checks pass.

## Phase 1: Project foundation

- [x] Add `pyproject.toml` with runtime, development, test, and packaging tools.
- [x] Introduce the `src/tatatuya` package.
- [x] Add test directories and recorded-response fixture structure.
- [x] Add application-data path resolution with test overrides.
- [x] Preserve a working launcher during migration from the prototype layout.

Acceptance:

- Package imports and launches from the documented development command.
- Tests run from a clean environment.
- Existing files are migrated intentionally rather than duplicated indefinitely.

## Phase 2: Domain model and exact billing

- [x] Define typed device, specification, reading, calculation, currency, and
  settings models.
- [x] Implement scale normalization and Wh-to-kWh conversion.
- [x] Implement Romanian decimal input parsing.
- [x] Implement exact period consumption and total calculation with `Decimal`.
- [x] Implement reset/reversal, chronology, unit, and price validation.
- [x] Add the shared user-facing exception model.
- [x] Add focused domain tests.

Acceptance:

- No persisted energy or money calculation uses `float`.
- Scale 0, 2, and 3 tests pass.
- Reset meters and invalid prices produce explicit user-facing failures.

## Phase 3: SQLite persistence

- [x] Implement application database creation and connection handling.
- [x] Add versioned migrations for settings, devices, preferences, readings, and
  calculations.
- [x] Implement repository interfaces and SQLite implementations.
- [x] Store canonical decimal strings and UTC timestamps.
- [x] Add indexes for meter history queries.
- [x] Add migration and repository integration tests.

Acceptance:

- A new database initializes automatically.
- Restart tests preserve credentials/settings, devices, readings, and calculations.
- Equal consecutive readings are stored as separate records.
- Calculations remain unchanged after currency/settings changes.

## Phase 4: Tuya client refactor

- [x] Separate request signing from the client's endpoint methods.
- [x] Load credentials through Settings rather than module constants.
- [x] Keep diagnostic request information without exposing secrets in logs.
- [x] Implement device list, specification, individual status, and batch status.
- [x] Split batch requests into groups of at most 20 devices.
- [x] Parse responses into typed transport/domain values.
- [x] Add fixture-based client and parser tests.

Acceptance:

- Signing and endpoint construction have deterministic tests.
- Partial batch responses map reliably by device ID.
- Logs and user-visible details do not contain Client Secret or access tokens.

## Phase 5: Reading and refresh services

- [x] Implement device discovery and metadata upsert.
- [x] Resolve and cache `forward_energy_total` specifications.
- [x] Refresh stale or incompatible specifications.
- [x] Normalize and store every successful batch reading.
- [x] Store readings from individual Status calls.
- [x] Preserve successful devices when another device fails.
- [x] Return per-device loading/error results suitable for the UI.
- [x] Add workflow integration tests for batch size, repeated readings, offline
  devices, missing energy fields, and partial failures.

Acceptance:

- Every usable status call creates exactly one expected reading per device.
- A repeated cumulative value is still recorded.
- Unsupported energy values are never silently billed.
- More than 20 device IDs result in multiple compliant batch calls.

## Phase 6: Romanian application shell and main table

- [x] Build the main window, top bar, empty/settings-required state, and table.
- [x] Add `Actualizează`, `Setări`, and row actions.
- [x] Connect refresh through non-blocking workers.
- [x] Display the latest saved reading while offline or during recoverable errors.
- [x] Centralize Romanian strings and display formatting.
- [x] Establish styles without hard-coded control heights.
- [x] Add UI geometry tests and render a representative screenshot.

Acceptance:

- `Calculează`, `Istoric`, `Info`, and `Status` text is visibly rendered.
- Table rows fit their content under the application stylesheet.
- Representative long Romanian text is not clipped.
- Refresh cannot freeze the main window and always restores controls.
- A rendered screenshot is inspected before proceeding.

## Phase 7: Settings

- [ ] Build Tuya credential and region fields.
- [ ] Build the RON/EUR currency selector.
- [ ] Implement explicit connection testing.
- [ ] Persist settings only through the settings service/repository.
- [ ] Show clear missing-settings, success, and failure states.
- [ ] Ensure there is no setup wizard.

Acceptance:

- An unconfigured app directs the user to Settings.
- Valid settings survive restart.
- Connection failure is shown in the shared Romanian error experience.

## Phase 8: Calculation workflow

- [ ] Build timestamped start/end reading selectors.
- [ ] Default to newest ending reading and the last calculation's ending reading
  as the next start, falling back to the earliest reading.
- [ ] Show the per-meter prior price as faded fallback text.
- [ ] Automatically use that price when the input remains empty.
- [ ] Show consumption and a two-decimal total using Romanian formatting.
- [ ] Persist the immutable calculation and updated meter preference atomically.
- [ ] Add domain, integration, and UI tests for all validation paths.

Acceptance:

- Multiple readings on one day are individually selectable by time.
- Comma decimal input works.
- Old prices are meter-specific and currency-aware.
- A lower ending meter value opens the custom error modal.
- Saved calculation values match the preview exactly.

## Phase 9: History, Info, and Status

- [ ] Build read-only `Citiri` and `Calcule` history tabs.
- [ ] Add full calculation detail display.
- [ ] Add translated device Info with no mutation controls.
- [ ] Add raw Status diagnostics with unchanged Tuya codes.
- [ ] Record energy when Status performs an individual API call.
- [ ] Add empty-history and error-state UI tests.

Acceptance:

- No edit/delete action exists for readings or calculations.
- Info cannot rename or control a Tuya device.
- Status retains raw technical data and records any usable energy reading.

## Phase 10: Central errors and logging

- [ ] Implement the shared Romanian error modal.
- [ ] Support optional expandable and copyable technical details.
- [ ] Catch expected user-facing exceptions at the UI boundary.
- [ ] Log unexpected errors locally and show a safe generic message.
- [ ] Verify loading state cleanup across all worker failure paths.

Acceptance:

- Services can invoke consistent UI errors by raising the shared exception.
- Unexpected failures do not crash the app or expose credentials.
- All asynchronous failure tests leave controls usable.

## Phase 11: macOS packaging

- [ ] Add PyInstaller configuration for Apple Silicon.
- [ ] Bundle QSS, icons, migrations, and Qt plugins.
- [ ] Add `.app` build and `.dmg` creation scripts.
- [ ] Add a GitHub Actions ARM64 macOS release workflow.
- [ ] Attach versioned `.dmg` artifacts to GitHub Releases.
- [ ] Document installation and Gatekeeper's Control-click `Open` workaround.

Acceptance:

- A clean Apple Silicon Mac can install from the generated `.dmg`.
- The packaged application creates its database under Application Support.
- A fresh packaged app opens Settings and can complete a connection test.
- No credential is embedded in the release artifact.

## Phase 12: Release readiness

- [ ] Run the full unit, integration, and UI suite.
- [ ] Run a fresh-database end-to-end workflow with representative Tuya fixtures.
- [ ] Test Romanian formatting for RON and EUR.
- [ ] Inspect final rendered main, Settings, Calculate, History, and Error screens.
- [ ] Rehearse upgrade behavior from the preceding schema version.
- [ ] Review README installation and troubleshooting instructions.

Acceptance:

- All automated checks pass.
- The primary workflow can be completed without developer tools.
- The stored calculation can be reconstructed from its persisted inputs.
- Documentation and implemented behavior agree.

## Future extensions

These remain outside the first implementation but the architecture must not
prevent them:

- PDF billing statements generated from immutable calculations
- CSV exports
- Signed and notarized macOS releases
- Optional scheduled reading capture
- Additional currencies or localization
- Explicit administrative deletion with audit safeguards
