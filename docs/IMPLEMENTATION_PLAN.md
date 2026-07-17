# TataTuya Implementation Plan

## Status legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete and verified

The phases are ordered to establish testable business behavior before relying on
the GUI. A phase is complete only when its acceptance checks pass.

Post-Phase-9 checkpoint: the architecture review reopened acceptance for Phases
1, 4, 5, and 7. The bounded correction work specified in
`docs/REMEDIATION_PLAN.md` has passed the Phase 9A automated and rendered-UI
gates. Phase 10 was completed in the same pass because its safe-error work was
part of the remediation boundary.

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

- [x] Build Tuya credential and region fields.
- [x] Build the RON/EUR currency selector.
- [x] Implement explicit connection testing.
- [x] Persist settings only through the settings service/repository.
- [x] Show clear missing-settings, success, and failure states.
- [x] Ensure there is no setup wizard.
- [x] Refresh once on configured startup and after a verified settings save.

Acceptance:

- An unconfigured app directs the user to Settings.
- Valid settings survive restart.
- Connection failure is shown in the shared Romanian error experience.

## Phase 8: Calculation workflow

- [x] Build timestamped start/end reading selectors.
- [x] Default to newest ending reading and the last calculation's ending reading
  as the next start, falling back to the earliest reading.
- [x] Show the per-meter prior price as faded fallback text.
- [x] Automatically use that price when the input remains empty.
- [x] Show consumption and a two-decimal total using Romanian formatting.
- [x] Persist the immutable calculation and updated meter preference atomically.
- [x] Add domain, integration, and UI tests for all validation paths.

Acceptance:

- Multiple readings on one day are individually selectable by time.
- Comma decimal input works.
- Old prices are meter-specific and currency-aware.
- A lower ending meter value opens the custom error modal.
- Saved calculation values match the preview exactly.

## Phase 9: History, Info, and Status

- [x] Build read-only `Citiri` and `Calcule` history tabs.
- [x] Add full calculation detail display.
- [x] Add translated device Info with no mutation controls.
- [x] Add raw Status diagnostics with unchanged Tuya codes.
- [x] Record energy when Status performs an individual API call.
- [x] Add empty-history and error-state UI tests.

Acceptance:

- No edit/delete action exists for readings or calculations.
- Info cannot rename or control a Tuya device.
- Status retains raw technical data and records any usable energy reading.

## Phase 9A: Post-implementation architecture remediation

- [x] Retire the obsolete `.env`-driven Tuya client and unsafe diagnostic tools.
- [x] Classify supported meters separately from unrelated Tuya devices.
- [x] Support the documented `forward_energy_total` and
  `total_forward_energy` aliases and Tuya middle-dot Wh/kWh spellings.
- [x] Preserve visible history access for meters missing from later discovery.
- [x] Persist redacted raw specifications on devices and new readings through an
  ordered migration.
- [x] Move Settings initialization, loading, saving, and commit off the Qt UI
  thread.
- [x] Log unexpected exceptions and show only safe generic Romanian errors.
- [x] Add the expandable/copyable shared Romanian error dialog.
- [x] Add migration, mixed-account, disappeared-meter, Settings-thread, error,
  and rendered-geometry tests.
- [x] Reconcile product and architecture documentation with the implemented
  lifecycle behavior.
- [x] Pass the configured Ruff and Pyright checks without baseline exclusions.

Acceptance:

- Unsupported non-meter devices are not presented as billable meters.
- Supported Tuya meter/circuit-breaker aliases are matched exactly to their
  status value and normalized from the device-provided scale and unit.
- Previously historical meters remain reachable when absent or newly
  unsupported, without guessing new readings.
- Every new reading retains the redacted specification used to normalize it.
- Settings database work cannot block the Qt UI thread.
- Unexpected exceptions are logged and cannot expose raw exception details in
  the UI.
- The active source tree contains one production Tuya client and no tracked real
  credential or device-specific diagnostic values.
- Focused and full suites pass, and changed layouts are screenshot-inspected.

## Phase 10: Central errors and logging

- [x] Implement the shared Romanian error modal.
- [x] Support optional expandable and copyable technical details.
- [x] Catch expected user-facing exceptions at the UI boundary.
- [x] Log unexpected errors locally and show a safe generic message.
- [x] Verify loading state cleanup across all worker failure paths.

Acceptance:

- Services can invoke consistent UI errors by raising the shared exception.
- Unexpected failures do not crash the app or expose credentials.
- All asynchronous failure tests leave controls usable.

## Phase 11: macOS packaging

- [x] Add PyInstaller configuration for Apple Silicon.
- [x] Bundle QSS, icons, migrations, and Qt plugins.
- [x] Add `.app` build and `.dmg` creation scripts.
- [x] Add a GitHub Actions ARM64 macOS release workflow.
- [x] Attach versioned `.dmg` artifacts to draft GitHub Releases without
  automatically publishing them.
- [x] Document installation and Gatekeeper's Control-click `Open` workaround.

The packaging contract and Linux-verifiable checks are complete. The clean-Mac
installation acceptance gate remains part of Phase 12 release rehearsal and
must pass before manually promoting the draft to a production release.

Acceptance:

- A clean Apple Silicon Mac can install from the generated `.dmg`.
- The packaged application creates its database under Application Support.
- A fresh packaged app opens Settings and can complete a connection test.
- No credential is embedded in the release artifact.

## Phase 12: Release readiness

- [ ] Run the full unit, integration, and UI suite.
- [ ] Run a fresh-database end-to-end workflow with representative Tuya fixtures.
- [ ] Test Romanian formatting for RON and EUR.
- [ ] Inspect final rendered main, Settings, Calculate, History, and Error screens
  under both light and dark system palettes.
- [ ] Rehearse upgrade behavior from the preceding schema version.
- [ ] Review README installation and troubleshooting instructions.
- [ ] Confirm developer-facing documentation, metadata, CI, and scripts use English.
- [ ] Install the draft DMG on a clean Apple Silicon Mac, complete a connection
  test, and only then promote the draft GitHub Release.

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
