# TataTuya Implementation Plan

## Status legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete and verified

The phases are ordered to establish testable business behavior before relying on
the GUI. A phase is complete only when its acceptance checks pass.

Post-Phase-9 checkpoint: the architecture review reopened acceptance for Phases
1, 4, 5, and 7. The bounded correction work recorded in Phase 9A has passed its
automated and rendered-UI gates. Phase 10 was completed in the same pass because
its safe-error work was part of the remediation boundary.

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
- [x] Keep informational meter-table cells non-selectable.
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
- [x] Filter start/end readings by a separately selected local date.
- [x] Group each date beside its exact reading in start/end rows.
- [x] Limit selector popups to 15 visible entries before scrolling.
- [x] Default to newest ending reading and the last calculation's ending reading
  as the next start, falling back to the earliest reading.
- [x] Show the per-meter prior price as faded fallback text.
- [x] Automatically use that price when the input remains empty.
- [x] Show consumption and a two-decimal total using Romanian formatting.
- [x] Persist the immutable calculation and updated meter preference atomically.
- [x] Add domain, integration, and UI tests for all validation paths.

Acceptance:

- Multiple readings on one day are individually selectable by time.
- Date selection narrows each reading selector without changing persisted IDs.
- Dense selector popups scroll after 15 visible entries.
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
- [x] Keep read-only history-table cells non-selectable.
- [x] Keep the read-only Status diagnostic table non-selectable.

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

- [x] Run the full unit, integration, and UI suite.
- [x] Run a fresh-database end-to-end workflow with representative Tuya fixtures.
- [x] Test Romanian formatting for RON and EUR.
- [x] Inspect final rendered main, Settings, Calculate, History, and Error screens
  under both light and dark system palettes.
- [x] Rehearse upgrade behavior from the preceding schema version.
- [x] Review README installation and troubleshooting instructions.
- [x] Confirm developer-facing documentation, metadata, CI, and scripts use English.
- [ ] Install the draft DMG on a clean Apple Silicon Mac, complete a connection
  test, and only then promote the draft GitHub Release.

The local release-readiness gates are complete. The remaining clean-Mac check
requires the draft artifact, Apple Silicon hardware, and real Tuya access; it
cannot be replaced by the Linux test environment. Follow the release rehearsal
in `README.md` before declaring Phase 12 complete.

Acceptance:

- All automated checks pass.
- The primary workflow can be completed without developer tools.
- The stored calculation can be reconstructed from its persisted inputs.
- Documentation and implemented behavior agree.

## Phase 13: On-demand Tuya Cloud calculation history

This phase adds a sparse daily import inside Calculate without background
polling. Imported readings join the existing persisted-reading selectors, so a
saved local period boundary and a cloud-imported boundary can be calculated
together. It uses only Tuya's documented read-only status-report-log OpenAPI and
does not promise access to all consumer-app history.

### 13.1 Capability and contract probe

Implementation status (2026-07-30): the migration, bounded read-only gateway,
exact parsing/reduction, idempotent persistence, fixed-seven-day calculation UI,
and cooperative worker/shutdown foundations are implemented. Production
composition enables `HistoricalScaleContract` from the official Tuya report-log,
things-data-model, and DP-protocol contracts. Operational availability is tested
by the explicit import request; a missing entitlement, empty history, or sparse
meter cadence does not globally hide the feature or affect local calculations.

- [x] Confirm `GET /v2.1/cloud/thing/{device_id}/report-logs` in Tuya's current
  official IoT Core API reference and retain it as a read-only request.
- [x] Establish the normalization contract from Tuya's official documentation:
  report-log rows are values reported by the requested DP code, while numeric DP
  wire values are integers interpreted with that DP model's unit and scale.
- [x] Document IoT Core subscription/project authorization, app-account linking,
  default seven-day Device Log retention, and the lack of consumer-app parity.
- [x] Build deterministic synthetic fixtures for pagination overlap, repeated
  cursors, rate-limit retries, malformed responses, and bounded payload failures.
- [ ] Verify the endpoint against the configured project and a representative
  supported meter as part of release rehearsal.
- [ ] Confirm that the device reports the exact selected cumulative-forward-
  energy alias with useful cadence near local day boundaries; preserve and
  display actual event times rather than treating reports as midnight readings.
- [ ] Record sanitized real-account fixtures for the actual endpoint and target
  DP, one page, multiple pages when naturally available, an empty interval, the
  observed retention boundary, and the configured project's permission/service
  behavior.

Gate:

- Code activation requires the official report-log and DP-model contracts, exact
  code matching, specification bracketing, retained specification provenance,
  and no scale inference from event magnitude. Those conditions are satisfied.
- A release rehearsal still records whether a representative configured account
  has the required IoT Core entitlement and whether its target meter reports at
  a useful cadence. These are deployment/device outcomes surfaced at runtime,
  not reasons to disable the feature for every installation.
- Sanitized live fixtures supplement deterministic adversarial fixtures without
  deliberately forcing rate limits or manipulating device data.

### 13.2 Domain, gateway, and exact parsing

- [ ] Keep report pages as typed infrastructure models, map response rows to the
  transport-neutral event value at the service port boundary, and keep normalized
  candidates private to the cloud-history service; do not pass unpersisted
  candidates into billing.
- [ ] Add a narrow report-log gateway port with `size=99`, at most 50 pages and
  4,950 rows, separate 1 MiB raw-body and decoded-text limits per successful
  page, separate 10 MiB raw/decoded totals, a 64 KiB raw/decoded error-body cap,
  a five-second request timeout, and a 30-second monotonic workflow deadline.
- [ ] Implement the limits in the HTTP transport: request identity encoding,
  reject unsupported encodings, preflight a declared oversized length, stream
  only bounded chunks up to the smaller per-page/remaining-total allowance plus
  one byte, stop before decode or parse on overflow, decode strict UTF-8, enforce
  the remaining decoded budget before parsing, and return raw/decoded counts for
  cross-page accounting. Reject a stream that cannot perform `read(size)` and
  never call an unbounded response or HTTP-error `read()`.
- [ ] Derive exactly the most recent seven local calendar dates in the service,
  cap today's upper bound at now, and convert DST-aware local bounds exactly to
  UTC milliseconds. Expose no caller-selected interval in this release.
- [ ] Pace request starts at least 250 ms apart and retry a documented rate-limit
  response at most twice after 500 ms and 1,000 ms within the same deadline.
  Apply the cadence to every physical attempt, including retries and the page
  immediately following a successful retry.
- [ ] Validate explicit device ID/code/timestamp/range, snake_case and observed
  camelCase pagination aliases, non-empty continuation pages, non-negative
  integer pagination data, stable `total` when supplied, and non-missing/non-
  repeated cursors. Accept the observed minimal terminal
  `{"hasMore": false}` result as empty history while rejecting conflicting
  aliases and any explicit wrong-device ID.
- [ ] Parse JSON integer and decimal tokens through exact hooks before any
  binary `float` exists; accept raw event values only as JSON numbers or strict
  decimal strings within the documented byte/significant-digit/magnitude limits
  and reject booleans, nulls, composites, non-finite or negative values. Inspect
  the decimal tuple before fixed rendering and cap its canonical plain-text form
  at 128 characters so exponent notation cannot amplify a bounded response.
- [ ] Centralize the arithmetic, non-rendering canonical-decimal validator in
  the domain layer and apply its 128-character bound to raw and normalized Tuya
  energy values for both current status and cloud history. Accept specification
  scales only from 0 through 123, reject larger scales as invalid metadata, and
  retain exact scale 0/2/3 plus boundary-valid Wh/kWh behavior without rounding.
- [ ] Apply the same pre-render bound to every successful device,
  specification, batch-status, individual-status, and report-log diagnostic
  Decimal. Reject an oversized successful payload, except that batch status
  drops only the affected meter entry so other usable readings survive; in
  bounded HTTP-error and `success=false` diagnostics, discard only the unsafe
  scalar behind the fixed `[DECIMAL_DISCARDED]` marker so error handling cannot
  trigger expansion.
- [ ] Deduplicate exact rows, preserve unchanged values at different times, and
  reject different values for the same device/code/timestamp as ambiguous.
- [ ] Retrieve and timestamp the authoritative specification before page one,
  verify the same code/unit/scale after the final page, and normalize through the
  existing Wh/kWh domain rules.
- [ ] Partition fully validated events with the captured operating-system
  timezone and select the earliest real event at or after local midnight for
  each returned date; empty dates produce no candidate.
- [ ] Implement transport-level cancellation plus checks before authentication,
  immediately after authentication before the original request, between pages,
  during pacing/backoff, before the second specification request, and before
  persistence. Dialog close/shutdown must not synchronously wait on the UI thread
  for a page sequence.
- [ ] Add unit and fixture tests for every date, numeric, pagination, payload,
  pacing, deadline, cancellation, daily-reduction, specification, and redaction
  boundary above.
- [ ] Add transport tests for declared and streamed oversized success bodies,
  decoded-limit overflow, invalid UTF-8, oversized JSON and non-JSON HTTP error
  bodies, exact numeric-token decoding, and raw/decoded total accounting across
  pages. Assert rejection occurs before JSON parsing.

Gate:

- A multi-page query returns exact, chronologically ordered events for only the
  selected cumulative DP and reduces them deterministically to no more than one
  candidate per returned local day with its real timestamp preserved.
- No malformed, ambiguous, changed-specification, out-of-range, unsupported,
  canceled, expired-deadline, or oversized result reaches persistence.
- Request count, payload, time, rate retry, and cancellation behavior are fixed
  contracts covered by tests rather than open-ended implementation choices.
- No report-log success or error path performs an unbounded body read, decode,
  or JSON parse, and multi-page accounting cannot exceed either total limit.
- No compact Tuya exponent or specification scale can create an over-128-character
  fixed-point raw, normalized, persisted, UI, or diagnostic value. Regression
  tests prove rejection occurs before fixed rendering for current and cloud
  success payloads and that bounded error diagnostics remain safe.
- Request diagnostics and fixtures contain no credentials, tokens, local keys,
  or personal device data.

### 13.3 Sparse daily persistence and unified billing

- [ ] Add an ordered migration for nullable external event key, import time,
  specification-observation time, exact source DP code, local bucket date,
  timezone identifier, and UTC offset on readings.
- [ ] Add a partial unique external-event index and a separate partial unique
  `(device_id, cloud_day_local_date)` index applying only to `cloud_daily` rows.
- [ ] Define the `tuya-report-v1` SHA-256 input as length-framed UTF-8 device ID,
  DP code, base-10 event milliseconds, and canonical raw decimal. Add fixed-vector
  tests and explicitly treat JSON `1`, `1.0`, and string `"1"` as equivalent.
- [ ] Add a transactional daily-import port that compares only device ID, source
  DP code, exact event timestamp, canonical raw decimal, scale, source unit, and
  normalized kWh when reusing an external event key.
- [ ] Preserve the first import's diagnostic event payload, full specification
  snapshot, observation time, bucket, timezone, and offset. Diagnostic-only
  reorder/add/change differences may produce a safe marker but never reject,
  update, or duplicate the reading.
- [ ] Validate and reduce the complete remote result before starting the import
  transaction; get or create the entire daily set atomically and roll it all back
  on cancellation, conflict, consistency failure, or repository error.
- [ ] Reuse an existing immutable daily row on repeated/overlapping loads. Never
  replace it or add a second daily row when a later query reveals an earlier
  event for the same meter/date.
- [ ] Persist a completed daily import independently of calculation saving, then
  reload ordinary `Reading` models with IDs into the existing selectors.
- [ ] Keep `BillingService` responsible only for persisted reading IDs,
  calculation validation, immutable calculation insertion, and price preference;
  do not give cloud-history services calculation responsibilities.
- [ ] Add migration/integration tests for a clean database and migration-3
  upgrade, 100 repeated loads, overlapping ranges, different events on an
  existing day, timezone changes, rollback at every write boundary,
  restart/offline use, and later settings/specification changes.
- [ ] Reimport the same event with reordered/added/changed diagnostic-only event
  and specification fields; assert the same reading ID and first snapshots remain
  with no extra row.

Gate:

- A successful load creates at most one `cloud_daily` reading for each returned
  local day and keeps it even when Calculate closes without saving a calculation.
- Repeating or overlapping the load is idempotent by both event and meter/day;
  current `batch` and `status` captures on the same day still create every
  required reading.
- Persisted daily readings retain exact decimal values, actual event/import
  times, source DP/payload, specification snapshot/observation time, local bucket
  date, timezone, and offset.
- A saved local reading and an imported daily reading can produce one ordinary
  immutable calculation; canceling that calculation does not remove the import.

### 13.4 Romanian calculation UI

- [ ] Refactor calculation preparation so the dialog opens with zero or one
  persisted reading, preserving remembered price/currency and showing an
  in-dialog insufficient-readings state.
- [ ] Split application-currency reads from optional Tuya connection settings.
  Missing/invalid Tuya credentials disable only cloud import and never block
  persisted-reading calculation.
- [ ] Refactor bootstrap/main-window content selection so cached meters with
  usable history remain visible without complete Tuya settings. Use the Settings
  empty screen only when no cached history exists.
- [ ] Add the Romanian settings warning above cached rows; keep `Calculează`,
  `Istoric`, and cached `Info` enabled while disabling `Actualizează`, `Status`,
  and cloud import without remote capability.
- [ ] Ensure incomplete Tuya settings—and credential clearing if that operation
  is supported—preserve cached rows and application currency while recomputing
  only remote-action availability.
- [ ] Keep one unified pair of persisted-reading selectors and preserve existing
  defaults, local-date filtering, 15-row popup limit, validation, and price
  behavior for all provenances.
- [ ] Add one compact `Importă citirile din ultimele 7 zile` action and clear text
  that a successful load saves at most one daily reading per returned date
  locally. Missing credentials instead offer a Settings action.
- [ ] Import through a non-blocking, cancelable worker with progress,
  permission/service, rate-limit, result-limit, combined empty-or-no-longer-
  available, and safe generic Romanian states. Do not invent a distinct
  retention error without an observed documented signal.
- [ ] Detach results on dialog close and restore controls with truthful terminal
  status on success, empty results, permission/service failure, rate/response
  limits, generic failure, and cancellation.
- [ ] Add an application-level `WorkerOwner` that owns every asynchronous UI
  workflow worker from before start through `finished`; windows/dialogs hold only
  operation subscriptions, including for cloud import.
- [ ] Replace parameterless workflow callables with a UI-independent shared
  `CancellationContext` created before thread start. Pass it explicitly through
  bootstrap, Refresh, Settings load/test/save, Status, calculation prepare/save,
  and cloud import services and infrastructure; use interruptible waits and
  checkpoints before every remote call/retry, collection step, new transaction,
  and result publication.
- [ ] Apply the architecture's per-workflow deadlines, the lesser-of-five-
  seconds end-to-end request timeout, and a SQLite busy timeout no greater than
  five seconds. Ensure request timeout/abort covers resolution, connection, and
  body reads. After cancellation, prohibit additional remote calls, backoffs,
  and database transactions except the one atomic capture transaction reserved
  by an already-started individual or batch current-status request.
- [ ] Reorder individual Status so it revalidates the energy specification and
  checks cancellation before requesting status; never make a specification call
  after a completed status response.
- [ ] Add one transactional current-status capture-store operation. Normalize
  every usable result from a completed batch of at most 20 devices and insert
  them together in exactly one transaction; individual Status uses the same
  operation with one result. Bound normalization and persistence together to
  five seconds, pass only the stage's remaining time to SQLite's busy timeout,
  and roll back the entire response without a shutdown retry on failure/expiry.
- [ ] Make the status request plus its required post-response transaction one
  explicit cancellation-safe boundary. Before starting status, reserve five
  seconds of the remaining workflow deadline for post-response normalization and
  capture persistence, and bound the request by the lesser of five seconds or
  the unreserved remainder; do not start it without positive request budget.
  Permit only one such workflow at a time, disable Refresh/Status triggers while
  it is held, and after cancellation allow its single transaction but no next
  remote request or transaction.
- [ ] Implement the documented safe write boundaries: atomic complete-discovery
  cache updates; atomic Settings/calculation saves once their transaction starts
  with no chained refresh; and cloud-import rollback when cancellation wins
  before commit.
- [ ] Disable Qt's automatic last-window quit and route main-window,
  application-menu/Dock, application quit-event, and last-window requests
  through a shutdown coordinator before `QApplication.quit()`. Prohibit direct
  application quit calls outside that coordinator. While workers are active,
  defer quit, show a non-blocking Romanian closing state, request cancellation,
  keep processing Qt events, and quit exactly once after all workers finish.
- [ ] Remove blocking UI-thread `thread.wait()` shutdown paths. Retain worker
  references until `finished`, and make `aboutToQuit` an assertion/final cleanup
  hook rather than the owner of live-thread cancellation.
- [ ] After commit, reload unified persisted selectors, reapply ordinary
  defaults, and allow local and `cloud_daily` readings to be combined.
- [ ] Report new/reused daily counts and keep successfully imported readings
  when the user closes or cancels calculation saving.
- [ ] Update `Citiri` history to identify daily Tuya Cloud imports and show the
  real event time, import time, local bucket date, and timezone/offset without
  adding edit/delete actions.
- [ ] Exercise representative Romanian data, process the real layouts, verify
  geometry/text, run UI tests, and inspect light/dark rendered screenshots.
- [ ] Add a full bootstrap UI test with cached readings, application currency,
  and no Tuya credentials that opens Calculate from the real main-window row and
  verifies every local/remote action state and warning geometry.
- [ ] Add in-flight tests for dialog close and every quit path. Exercise quit
  separately during bootstrap, manual Refresh, Settings load/test/save, Status,
  calculation prepare/save, and cloud import. Prove responsive event processing,
  no late UI update, no destroyed running `QThread`, no post-cancel call/write
  outside the safe commit boundary, ordinary completion within six seconds, and
  completion within eleven seconds for an in-flight current-status capture.
- [ ] Add cancellation tests immediately after individual and multi-result batch
  status responses but before persistence. Assert specification-before-status
  call order, pre-request persistence-budget reservation, exactly one capture
  transaction, every usable result committed or the whole response rolled back
  on a busy-database failure, no later remote call, and the eleven-second
  shutdown bound.
- [ ] Add a rendered UI test proving `Actualizează` and all row `Status` actions
  remain disabled while either Refresh or individual Status owns the exclusive
  capture boundary and are restored on every completion path.

Gate:

- A user can import the fixed recent-seven-day cloud history, combine an imported
  boundary with any persisted boundary for that meter, preview the exact saved
  result, and find both inputs and the immutable calculation after restart.
- Persisted calculation works offline and without Tuya credentials; only the
  explicit import action depends on remote settings.
- Cached meters and their Calculate, History, and Info actions remain reachable
  from the main window without Tuya credentials; remote-only actions are visibly
  unavailable and the settings warning does not replace local history.
- Repeated/overlapping imports do not add duplicate selector options and an
  imported row always displays its real timestamp rather than midnight.
- Cloud network/database work never freezes Qt, stale or canceled results never
  update closed UI, and deferred shutdown preserves worker lifetime until all
  bounded cancellations finish.
- Every owned workflow is cooperatively cancelable, obeys its complete-workflow
  deadline, and drains ordinary application shutdown within six seconds without
  starting later work or violating an atomic persistence boundary. The sole
  post-cancel transaction exception is a completed usable current-status
  response, whose one atomic capture drains within eleven seconds.
- The rendered dialog keeps all Romanian labels and controls visible at the
  supported window size in light and dark palettes.

### 13.5 Documentation and release regression

- [ ] Update README setup/troubleshooting with the verified Tuya permission,
  service/retention limitation, and cloud-history error recovery.
- [ ] Run focused unit, integration, and UI tests, then Ruff, Pyright, and the
  full test suite.
- [ ] Re-run the clean-database and preceding-schema upgrade workflows.
- [ ] Add a clean-Mac rehearsal covering a successful daily import and one
  calculation combining saved-local and cloud-origin readings before publishing
  a release containing Phase 13.

Gate:

- Product, architecture, setup documentation, migrations, fixtures, and
  implemented behavior agree.
- The original refresh/status capture and local calculation workflows pass
  unchanged alongside daily cloud import and unified calculation.
- A release artifact contains no credentials or captured personal/device data.

## Future extensions

These remain outside the first implementation but the architecture must not
prevent them:

- PDF billing statements generated from immutable calculations
- CSV exports
- Signed and notarized macOS releases
- Optional scheduled reading capture
- Additional currencies or localization
- Explicit administrative deletion with audit safeguards
