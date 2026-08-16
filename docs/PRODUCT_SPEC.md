# TataTuya Product Specification

## 1. Purpose

TataTuya is a simple Romanian-language macOS application for a non-technical
user who manages smart electricity meters in multiple houses through Tuya.

Each meter reports a cumulative forward-energy value and does not reset every
month. TataTuya records those cumulative readings and calculates the cost of a
period by comparing two saved readings for one meter.

```text
period consumption = ending reading - starting reading
total price = period consumption * price per kWh
```

The first successful status retrieval for a meter establishes its first stored
reading. A later reading can come from another current-status capture or from a
sparse Tuya Cloud history import. This lets a user combine, for example, a saved
June boundary reading with a July boundary reading imported a few days late.

## 2. Scope

### Included in the first version

- Configure Tuya credentials and region in Settings.
- Discover the user's Tuya devices.
- Retrieve statuses in batches and individually.
- Discover the forward-energy data point, unit, and scale from each device's
  specification.
- Store every usable current-status energy reading in an encrypted SQLCipher
  database.
- Calculate a cost for one meter between two stored readings.
- Remember the most recently used price separately for each meter.
- Select a global currency of RON or EUR.
- View a meter's readings and saved calculations.
- View translated device information and raw Tuya status diagnostics.
- Display consistent Romanian error dialogs.
- Distribute an Apple Silicon application in an unsigned `.dmg`.

### Added by the cloud-history extension

- Load cumulative forward-energy status-report events from Tuya Cloud for one
  meter for the most recent seven local calendar days, including today.
- Import at most one representative cloud reading per returned local calendar
  day without requiring TataTuya to have been open at that time.
- Combine imported cloud readings and existing saved readings in the same
  calculation selectors.
- Retain imported daily readings locally even when no calculation is saved.

Cloud history means data exposed to the configured developer project by Tuya's
documented read-only OpenAPI. It does not imply that every item visible in the
Tuya consumer application is available to TataTuya. Availability and retention
depend on the Tuya project, permissions, service edition, device, and data point.

### Deliberately excluded for now

- Sending commands or changes to Tuya devices.
- Renaming meters in TataTuya.
- Automatic scheduled/background polling while the app is closed.
- Scraping, reverse-engineering, or using private endpoints from the Tuya
  consumer application.
- Editing or deleting readings and calculations.
- Tiered tariffs, VAT, fixed fees, or other billing adjustments.
- Converting old amounts when the selected currency changes.
- PDF statements and CSV export.
- A first-run setup wizard.
- Apple signing and notarization.

PDF generation is a planned extension. The calculation model must therefore be
independent of the current on-screen result presentation.

## 3. Product principles

- The common workflow must be obvious without technical Tuya knowledge.
- Billing actions are primary; Info and raw Status are secondary diagnostics.
- The application must never guess an energy scale or silently bill with an
  unsupported unit.
- Historical records must remain reproducible even when later settings change.
- Failures should explain what the user can do next and must not crash the app.
- The application is read-only with respect to Tuya but maintains its own local
  history in an encrypted local database.

## 4. Main window

### Top bar

The top bar contains:

- Application name
- Connection/loading state
- `Actualizează` button
- `Setări` button

There is no setup wizard. When credentials are absent or incomplete and no
cached meter has usable local history, the main area directs the user to
Settings.

When cached meters have readings or calculations, the device table remains
visible regardless of Tuya credential completeness. A Romanian warning banner,
`Conexiunea Tuya nu este configurată. Datele salvate rămân disponibile.`, links
to Settings without replacing local history. `Calculează`, `Istoric`, and `Info`
remain available. Remote-only `Actualizează`, `Status`, and Tuya Cloud import are
disabled until complete credentials are saved. A transition to incomplete Tuya
settings—including credential clearing if that operation is supported—never
hides cached rows or makes the application currency unreadable.

### Device table

| Meaning | Romanian label | Value |
|---|---|---|
| Device | `Contor` | Name returned by Tuya |
| Connectivity | `Stare` | Online, offline, or unknown |
| Current cumulative reading | `Index curent` | Latest stored kWh value |
| Reading time | `Ultima citire` | Local date and time |
| Row actions | `Acțiuni` | Billing, history, info, and status |

Row actions:

- `Calculează`
- `Istoric`
- `Info`
- `Status`

Buttons and rows must size from their styled text and layout. The UI must not
depend on fixed control heights.

The meter table is informational and does not allow cell or row selection. Its
buttons remain the only interactive controls inside the table.

`Online` is displayed in green and `Offline` in red. Unknown or unavailable
states use a neutral or warning treatment rather than implying connectivity.

Only devices with a supported cumulative-energy specification are presented as
new meters. Other Tuya products are not shown in the billing table. A meter with
saved readings remains visible if it later disappears from Tuya or becomes
unsupported, so its immutable history and calculations remain accessible. Its
state shows `Indisponibil în Tuya` when discovery confirms it is absent, and its
remote `Status` action is disabled while absent.

## 5. Refresh and reading capture

`Actualizează` performs this workflow:

1. Retrieve the Tuya device list.
2. Update the local cache of device names and metadata.
3. Load or retrieve the energy specification for each applicable device.
4. Split IDs into groups of no more than 20.
5. Retrieve current statuses through the Tuya batch-status endpoint.
6. Extract and normalize each usable cumulative forward-energy value.
7. Store one new reading per successful device result.
8. Update the device table.

A successful discovery distinguishes supported meters, explicitly unsupported
Tuya devices, and devices whose specification could not be classified safely.
Unsupported devices without reading history are omitted from the meter table.
An unsuccessful discovery does not mark cached meters as absent.

When the application starts with complete saved settings, it performs this
refresh once after showing cached local data. Saving settings immediately after
a successful connection test also performs one refresh. TataTuya does not poll
periodically or refresh in the background while the application is closed.

Every successful status call creates a reading, including calls that return the
same value as the preceding reading. A failed or offline device retains its most
recent saved reading but receives no new entry.

For each completed batch-status response, all usable meter results are stored in
one atomic capture transaction. Opening `Status` first revalidates that meter's
energy specification and only then performs the individual status request; a
usable response is stored in one atomic capture transaction. A database failure
rolls back that response's whole capture and uses the shared Romanian error
experience rather than leaving a partial batch. Only one Refresh or individual
Status capture runs at once; `Actualizează` and every `Status` action remain
disabled until that capture boundary finishes.

## 6. Energy extraction

The preferred Tuya status code is `forward_energy_total`. Tuya also defines
`total_forward_energy` for compatible circuit-breaker products. The application
accepts these two explicit cumulative-forward-energy aliases and must pair the
status value with the exact code selected from that device's specification.
Exactly one supported alias may be present; ambiguity is rejected.

The normalized value is:

```text
normalized value = raw value / 10^scale
```

Rules:

- Scaled kWh values become the canonical reading directly. Supported Tuya
  spellings are `kWh` and `kW·h`.
- Scaled Wh values are converted to kWh. Supported Tuya spellings are `Wh` and
  `W·h`.
- A Tuya energy scale is accepted only as an integer from 0 through 123. The
  upper bound is derived from the 128-character canonical quantity limit after
  the additional three-place Wh-to-kWh conversion; a larger scale is an invalid
  specification, not a value to guess, round, or truncate.
- Before fixed-point rendering, both the raw energy value and its normalized kWh
  result must fit an exact canonical decimal string of at most 128 characters.
  The bound is checked from the `Decimal` tuple without first materializing the
  fixed-point string. This rule applies to current batch/status captures and
  cloud history alike.
- Raw value, scale, source unit, normalized kWh, redacted raw status/report
  event, and the redacted raw specification used for normalization are retained.
- Missing, ambiguous, non-numeric, or unsupported energy data raises a clear
  error rather than being guessed.
- A changed or invalid specification must not reinterpret old readings; each
  reading stores the scale, unit, and specification snapshot used to normalize
  it. Current-status readings use a specification revalidated at capture time;
  imported cloud readings follow the additional safety rules in Section 7.

## 7. Calculation dialog

The calculation dialog displays:

- Meter name
- Starting-date dropdown
- Starting-reading dropdown
- Ending-date dropdown
- Ending-reading dropdown
- Starting cumulative value
- Ending cumulative value
- Consumption for the selected period
- Price per kWh
- Currency
- Final total
- Optional compact Tuya Cloud card with
  `Importă citirile din ultimele 7 zile`

The reading selectors always contain persisted TataTuya readings. They may mix
current `batch`/`status` captures and daily readings imported from Tuya Cloud.
Billing therefore keeps one workflow: it receives two persisted reading IDs and
saves the same immutable calculation regardless of reading provenance.

The cloud card loads only after the explicit import action. The service derives
the fixed window from local today minus six days through the current instant;
the UI does not expose a date-range picker. This seven-day contract applies to
every caller, not only the calculation dialog. A future advanced range requires
a verified paid-retention entitlement and a separate product decision.

With complete Tuya credentials, the card exposes the single seven-day import
action. Missing credentials show the separate
`Configurează conexiunea în Setări` state. Device Log entitlement, permission,
empty-history, and meter-cadence limitations are checked by the attempted import
and reported without affecting calculations from local readings.

Loading is asynchronous. The complete remote result is validated before any
write. A successful load atomically imports at most one representative reading
per returned local calendar day, keeps those readings even if the dialog closes
without a calculation, refreshes the unified selectors, and reports how many
new and existing daily readings were found. The UI states clearly that importing
saves daily readings locally while remaining read-only toward Tuya.

Closing Calculate during an import hides the dialog, cancels remote work, and
prevents late results from updating it. Quitting the application during any
background workflow—bootstrap, Refresh, Settings loading/testing/saving, Status,
calculation preparation/saving, or cloud import—shows a non-blocking Romanian
closing state, keeps processing Qt events, and requests cooperative
cancellation. A canceled workflow starts no later remote call or database
transaction except for one required current-status capture: once an individual
or batch status request has started after specification revalidation, that
request and the atomic persistence of every usable result it returns form one
cancellation-safe boundary. Cancellation prevents the next remote request but
does not discard this completed response. Other atomic writes already inside
their documented commit boundary may finish or roll back. Quit completes after
all workers finish; an in-flight status capture has an eleven-second maximum
shutdown-drain bound covering its remote request, required capture transaction,
and Qt completion delivery. The UI must not freeze and a running worker must
never be destroyed during shutdown.

`Calculează` opens the dialog even when fewer than two readings are saved
locally. It explains that more readings are needed while leaving cloud import
available when Tuya settings are complete. Missing, invalid, expired, or removed
Tuya credentials disable only cloud import; they never prevent opening the
dialog or calculating from persisted readings. Currency remains independently
available as an application setting.

The user chooses a local date before choosing the exact reading for that date.
Changing a date filters its reading dropdown to measurements captured on that
date. The date and its reading are displayed side by side in clearly labelled
`Început` and `Sfârșit` rows. Reading options contain an exact local timestamp
and reading, for example:

```text
03.12.2026, 18:42 — 1.234,56 kWh
```

Multiple readings on the same day remain separate selectable entries.
Date and reading dropdowns show at most 15 entries at once and scroll when more
entries are available.

### Defaults

- The ending reading defaults to the newest persisted reading.
- The starting reading defaults to the ending reading used by the meter's most
  recent saved calculation.
- With no previous calculation, the start defaults to the earliest persisted
  reading.
- After a successful cloud import, the selectors are rebuilt from all persisted
  readings and these same defaults are reapplied.

### Price behavior

- Prices belong to individual meters.
- The previous price is shown as faded helper/placeholder text.
- Leaving the price input empty automatically uses that previous price.
- Entering a value overrides it and becomes the meter's remembered price after
  a successful calculation.
- Romanian comma input is primary; dot input may be accepted and normalized.
- Currency is the global Settings choice: RON or EUR.

### Calculation behavior

- Use exact decimal arithmetic.
- Taxes and fees are assumed already included in the price per kWh.
- Display monetary values with two decimal places and a comma decimal separator.
- Saving a successful calculation creates an immutable record containing both
  reading references, consumption, unit price, currency, total, and timestamp.
- Changing the global currency later does not alter old records.

### Validation

A calculation is rejected with a Romanian error when:

- Fewer than two persisted readings exist.
- Start and end refer to the same reading.
- The ending time precedes the starting time.
- The ending value is lower than the starting value, such as after meter reset
  or replacement.
- No current or remembered price exists.
- The price is malformed, zero, or negative.
- Either reading uses unsupported or incompatible units.
- A cloud import is requested with unavailable Tuya access or a response outside
  the safety limits.

### Cloud-history safety rules

- TataTuya queries only the exact cumulative-forward-energy code selected from
  the meter specification. It does not request or infer unrelated data points.
- Tuya's [status-report-log API](https://developer.tuya.com/en/docs/cloud/269c6a6b6b?id=Kduvi4xnjhav2)
  returns the values reported by the requested DP code. Tuya's
  [things-data-model contract](https://developer.tuya.com/en/docs/cloud/bd68171262?id=Kcp4utbhzzfgo)
  and [DP protocol](https://developer.tuya.com/en/docs/iot-device-dev/TuyaOS-iot_abi_dp_ctrl?id=Kcoglhn5r7ajr)
  define a value DP as an integer wire value interpreted with that DP model's
  unit and decimal scale. TataTuya therefore uses the exact selected status DP's
  specification to normalize its report-log values; it does not infer a scale
  from the magnitude or from unrelated events.
- TataTuya revalidates and snapshots the specification immediately before the
  first page and verifies the same code, unit, and scale after the last page. A
  malformed, ambiguous, changed-during-load, or unsupported specification
  rejects the entire import. Each stored reading retains that specification, so
  later metadata changes do not reinterpret it.
- Report cadence is device-controlled. Dates without a cumulative-DP report
  simply produce no imported reading; TataTuya never manufactures a midnight
  value.
- A successful v2.1 terminal page may contain only `hasMore: false`, omitting
  device ID, total, and logs. TataTuya treats that observed shape as an empty
  result. If Tuya does return a device ID, it must match the requested meter;
  an explicit mismatch still rejects the load.
- Tuya event timestamps are epoch milliseconds. TataTuya converts the most
  recent seven local dates to exact UTC bounds, caps today's upper bound at the
  current instant, rejects out-of-range returned events, and displays every
  imported event's real local timestamp. It never labels a representative as an
  exact midnight reading.
- Cloud values are parsed and normalized with exact decimal handling. Binary
  floating-point is not used. The shared energy limits above apply before
  normalization, fingerprinting, or persistence; exponent forms whose raw or
  normalized canonical representation would exceed 128 characters are rejected.
- Exact decimals in successful Tuya device, specification, status, and report
  payload diagnostics use the same 128-character pre-render bound. An oversized
  decimal rejects that successful device, specification, individual-status, or
  report payload before fixed rendering. In a batch-status response, only the
  affected meter entry is discarded so other usable meters still create their
  required readings. In bounded HTTP error or `success=false` diagnostics, such
  a scalar is discarded and replaced with a fixed technical marker; it is never
  expanded, rounded, or exposed as upstream text.
- Successful and error response bodies are read incrementally under transport-
  enforced raw and decoded size limits before UTF-8 decoding or JSON parsing.
  Oversized or invalid-UTF-8 responses reject the load safely without retaining
  or displaying their body.
- After validating all pages, events are partitioned using the operating
  system's local timezone captured for that query. For each returned local date,
  TataTuya selects the earliest valid event at or after local midnight and
  preserves the event's actual UTC timestamp.
- The local bucket date, timezone identifier, and UTC offset used for the bucket
  are retained. At most one automatic cloud reading exists for a meter and local
  bucket date.
- Repeated and overlapping imports reuse the immutable daily reading. If a later
  query reveals an earlier event for an already imported day, TataTuya keeps the
  existing reading rather than editing it or adding another automatic daily
  reading.
- Exact repeated remote rows are deduplicated. Different canonical values at
  the same device/code/timestamp make the result ambiguous and reject the whole
  import. Unchanged values at different timestamps remain distinct events before
  the daily reduction.
- No daily reading is written until every page, row, bucket, and specification
  check succeeds. A database failure rolls back the entire import.
- A successful cloud-history query is not a current status request. Merely
  importing daily representatives does not weaken the rule that every usable
  `batch` or `status` response creates a separate reading.
- Tuya does not document enough metadata to distinguish an empty recent
  seven-day window from one whose reports have expired. The Romanian UI uses one
  honest combined empty-or-no-longer-available message rather than claiming a
  retention error.

## 8. History

`Istoric` is a read-only dialog with two tabs.
Its tables do not allow cell or row selection.

### `Citiri`

- Date and time
- Cumulative kWh value
- Raw value
- Scale and original unit
- Source: batch refresh, individual status request, or daily Tuya Cloud import
- For a cloud event, its Tuya event time and local import time
- For a cloud event, the retained local bucket date and timezone/offset used to
  select it

### `Calcule`

- Calculation date
- Period start and end
- Consumption
- Unit price
- Currency
- Total

Selecting a calculation shows its complete immutable details. There is no edit
or delete operation in the first version.

## 9. Settings

There is no first-run wizard. Settings contains:

### Tuya configuration

- Client ID
- Client Secret
- Region dropdown
- `Testează conexiunea`
- `Salvează`

On production macOS, the Client Secret is stored automatically as a Keychain
generic password; it is never stored in the logical database. Client ID, region,
and currency are stored in the database. On later visits the secret field stays
empty and uses backend-neutral text to show that a secret is stored. Leaving it
untouched preserves the stored value; entering a new value replaces it after
validation. A failed connection test must not be displayed as a successful
setup.

### Application configuration

- Currency dropdown containing RON and EUR

Currency is an application setting and remains readable independently from Tuya
credentials. Missing, denied, or corrupt Tuya credentials may disable remote
workflows but must not disable cached devices, history, currency, or calculations
from persisted readings. Settings and explicit remote actions show an actionable
Romanian credential-storage error.

### Local-data protection and recovery

The complete database is encrypted with SQLCipher using a random 256-bit key
stored separately in macOS Keychain. The application data directory uses mode
`0700`; the database, sidecars, migration temporaries, and log use `0600`.
TataTuya fails closed when Keychain or SQLCipher is unavailable and never
replaces an unreadable database with an empty or plaintext one.

This fail-closed rule is the production macOS policy. Linux and other POSIX
development systems outside macOS use ordinary SQLite and a separate
`tuya-client-secret.plaintext` artifact by default so restart behavior is
realistic without hidden environment switches. Those permissions are best
effort, not encryption; only test credentials may be used. Windows development
is unsupported and reports that limitation explicitly. Production macOS cannot
select the plaintext backend.

Version one uses the local-key policy: losing the login Keychain item can make
the local history permanently unreadable. Copying only `tatatuya.sqlite3` is not
a complete portable backup, and version one does not offer database export.
Users who depend on the history must include the login Keychain in their tested
Mac migration/Time Machine recovery process. This protection does not defend
against malware controlling the logged-in session or TataTuya while it is open.

## 10. Info and diagnostics

`Info` displays translated labels for device metadata obtained from Tuya. It
does not modify the device.

`Status` displays translated surrounding UI while preserving raw Tuya status
codes and values. This makes support possible without hiding technical data.
Its diagnostic table does not allow cell or row selection.

## 11. Error experience

Expected failures use a shared custom user-facing exception containing:

- Romanian title
- Plain-language Romanian message
- Optional technical details

The shared error dialog provides a close action and, when details exist, an
expand/copy mechanism. Its user-facing summary is visually primary; technical
details remain collapsed and visually secondary. Unexpected errors are logged
and converted to a generic Romanian error instead of terminating the application.

## 12. Romanian formatting and terminology

The Romanian-language requirement applies to application-visible text and
expected user-facing errors. Repository documentation, source identifiers,
developer tooling, CI output, scripts, logs, and package metadata use English.

- Decimal separator: comma
- Thousands separator: period or locale-appropriate grouping
- Dates: day, month, year
- Times: 24-hour
- Currency totals: two decimal places
- Timestamps: stored in UTC, displayed in the Mac's local timezone

Initial terminology:

| English concept | Romanian UI text |
|---|---|
| Refresh | `Actualizează` |
| Settings | `Setări` |
| Meter | `Contor` |
| Status/state | `Stare` |
| Current reading | `Index curent` |
| Last reading | `Ultima citire` |
| Actions | `Acțiuni` |
| Calculate | `Calculează` |
| History | `Istoric` |
| Readings | `Citiri` |
| Calculations | `Calcule` |
| Price per kWh | `Preț per kWh` |
| Consumption | `Consum` |
| Total | `Total` |
| Test connection | `Testează conexiunea` |
| Save | `Salvează` |

Romanian wording should be reviewed as complete screens, not translated one
isolated label at a time.
