# TataTuya Target Architecture

## 1. Architecture goals

The current repository is a prototype. The target structure separates:

- Exact, UI-independent energy and billing rules
- Application workflows
- Tuya and SQLite implementation details
- PySide6 presentation

This separation is required for reliable testing, maintainable Qt code, and a
future PDF statement feature.

## 2. Target repository layout

```text
TataTuya/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── docs/
│   ├── PRODUCT_SPEC.md
│   ├── ARCHITECTURE.md
│   └── IMPLEMENTATION_PLAN.md
├── src/
│   └── tatatuya/
│       ├── __init__.py
│       ├── __main__.py
│       ├── application.py
│       ├── paths.py
│       ├── domain/
│       │   ├── models.py
│       │   ├── billing.py
│       │   ├── energy.py
│       │   └── errors.py
│       ├── services/
│       │   ├── device_service.py
│       │   ├── reading_service.py
│       │   ├── cloud_history_service.py
│       │   ├── billing_service.py
│       │   └── settings_service.py
│       ├── infrastructure/
│       │   ├── database.py
│       │   ├── migrations.py
│       │   ├── repositories/
│       │   │   ├── devices.py
│       │   │   ├── readings.py
│       │   │   ├── calculations.py
│       │   │   └── settings.py
│       │   └── tuya/
│       │       ├── client.py
│       │       ├── signing.py
│       │       ├── parsers.py
│       │       ├── report_logs.py
│       │       └── energy_specification.py
│       ├── ui/
│       │   ├── main_window.py
│       │   ├── workers.py
│       │   ├── formatting.py
│       │   ├── text.py
│       │   ├── widgets/
│       │   │   └── device_table.py
│       │   └── dialogs/
│       │       ├── calculate.py
│       │       ├── history.py
│       │       ├── settings.py
│       │       ├── device_info.py
│       │       ├── device_status.py
│       │       └── error.py
│       └── resources/
│           ├── styles.qss
│           └── icons/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── ui/
│   └── fixtures/tuya_responses/
├── scripts/
│   ├── build_macos.sh
│   └── create_dmg.sh
└── .github/workflows/
    ├── tests.yml
    └── release-macos.yml
```

## 3. Dependency direction

```text
UI -> services -> domain
                  ^
infrastructure ---|
```

- `domain` imports neither Qt, SQLite, nor Tuya code.
- `services` coordinate domain operations through repository/client interfaces.
- `infrastructure` implements persistence and remote API behavior.
- `ui` presents service results and maps user actions to service calls.
- PDF output can later consume immutable calculation models without importing
  calculation-dialog code.

## 4. Domain types

The domain layer should use typed dataclasses or equivalent explicit models for:

- `Device`
- `EnergySpecification`
- `Reading`
- `Calculation`
- `Currency`
- `TuyaSettings`

Energy values and monetary quantities use `Decimal`. Domain validation owns the
rules for scale normalization, unit conversion, reading order, meter resets,
price validation, and total calculation.

Tuya report pages remain infrastructure transport models. The report-log port
delivers a small HTTP/SQLite/Qt-independent `CloudReportEvent` value declared at
the service boundary; the infrastructure adapter parses and maps each concrete
response row into it. A normalized cloud import candidate may be a private typed
service value while a load is validated and reduced, but it is not part of
billing. Billing continues to receive only persisted `Reading` models with
database IDs.

## 5. SQLCipher location, keys, and connection behavior

On macOS the database lives at:

```text
~/Library/Application Support/TataTuya/tatatuya.sqlite3
```

Production macOS uses the pinned SQLCipher driver and verifies a non-empty
`PRAGMA cipher_version` before schema access. A random 32-byte database key is
stored as Keychain generic-password account `database-key-v1` under service
`ro.tatatuya.app`. The Tuya Client Secret uses the separate account
`tuya-client-secret-v1`. Missing, denied, malformed, or wrong keys fail closed;
an existing unreadable database is never replaced or opened through plaintext
SQLite.

The parent directory is owned by the current user, is not a symlink, and uses
`0700`. The database, journals/WAL, migration temporaries, and logs are regular
owned files using `0600`. Existing plaintext databases are classified by header
and converted off the UI thread with SQLCipher `sqlcipher_export()` to a random
same-directory temporary. The Client Secret is copied to and verified in
Keychain before conversion, removed from the encrypted logical settings, and
the original is atomically replaced only after integrity, foreign-key, cipher,
correct-key, wrong-key, and empty-key checks pass. Conversion compares every
schema object plus per-table row counts and content hashes. A `0600` migration
state marker names only random same-directory temporary/rollback files and is
durably written before export begins; startup uses it to clean temporary
database sidecars or restore the original across a crash at any conversion
stage, then fsyncs and removes it. A persistent owned `0600` interprocess lock
serializes classification, database-key create-if-absent, recovery, conversion,
fresh creation, initial migrations, and integrity checks across simultaneous
launches. Lock waits are bounded and cancellation-aware.

Linux and other POSIX development systems outside macOS default to ordinary
SQLite and a separate, explicitly named `tuya-client-secret.plaintext` artifact
beside the database. Atomic symlink-safe writes and best-effort `0700`/`0600`
permissions reduce accidental exposure but do not provide encryption;
developers must use test credentials. Windows is deliberately unsupported and
fails with an explicit platform diagnostic before POSIX locking or ownership
APIs are used. Production macOS has no environment switch to the plaintext
backend and always requires SQLCipher plus Keychain. Tests may inject an
in-memory secret store. Connections
are not shared unsafely across Qt worker threads. Cancellation contexts install
SQLite progress handlers and are checked at cross-store boundaries so cancelled
work starts no later transaction. Transactions must make a refresh result
internally consistent: device updates and associated readings either complete
as designed or expose an explicit partial-result state.

## 6. Database schema

### `schema_migrations`

```text
version INTEGER PRIMARY KEY
applied_at_utc TEXT NOT NULL
```

### `settings`

```text
key TEXT PRIMARY KEY
value TEXT NOT NULL
updated_at_utc TEXT NOT NULL
```

Stores Tuya Client ID, region, and selected currency. The Client Secret is a
Keychain value and is forbidden in this table. Migration 2
removes the obsolete `tuya.account_uid` setting from existing databases.
Migration 3 adds device eligibility/presence/specification snapshots and the
per-reading specification snapshot without rewriting existing readings.
Migration 4 adds nullable cloud-event and local-day provenance columns plus
source-specific partial unique indexes without changing existing current-status
rows.

### `devices`

```text
device_id TEXT PRIMARY KEY
name TEXT NOT NULL
product_id TEXT
product_name TEXT
category TEXT
online INTEGER
energy_code TEXT
energy_unit TEXT
energy_scale INTEGER
raw_device_json TEXT
energy_eligibility TEXT NOT NULL
present_in_tuya INTEGER
raw_specification_json TEXT
first_seen_at_utc TEXT NOT NULL
last_seen_at_utc TEXT NOT NULL
```

The row caches remote metadata. `name` is refreshed from Tuya and is not a local
override.

`energy_eligibility` is `supported`, `unsupported`, or `unknown`.
`present_in_tuya` is nullable because upgraded databases cannot infer presence
until the next successful complete discovery. Explicitly unsupported devices
without history are omitted from the primary meter table; historical devices
remain accessible regardless of later eligibility or presence.

### `device_preferences`

```text
device_id TEXT PRIMARY KEY REFERENCES devices(device_id)
last_unit_price TEXT
price_currency TEXT
updated_at_utc TEXT
```

The currency stored with the preference prevents a price from being silently
reused under a different currency after the global setting changes.

### `readings`

```text
id INTEGER PRIMARY KEY
device_id TEXT NOT NULL REFERENCES devices(device_id)
recorded_at_utc TEXT NOT NULL
raw_value TEXT NOT NULL
scale INTEGER NOT NULL
source_unit TEXT NOT NULL
value_kwh TEXT NOT NULL
source TEXT NOT NULL
raw_status_json TEXT NOT NULL
raw_specification_json TEXT NOT NULL
external_event_key TEXT
imported_at_utc TEXT
specification_observed_at_utc TEXT
source_code TEXT
cloud_day_local_date TEXT
cloud_day_timezone TEXT
cloud_day_utc_offset TEXT
```

Indexes:

```text
CREATE INDEX readings_device_time
    ON readings(device_id, recorded_at_utc);
CREATE INDEX readings_device_id
    ON readings(device_id, id);
CREATE UNIQUE INDEX readings_external_event_key
    ON readings(external_event_key)
    WHERE external_event_key IS NOT NULL;
CREATE UNIQUE INDEX readings_cloud_daily_device_date
    ON readings(device_id, cloud_day_local_date)
    WHERE source = 'cloud_daily'
      AND cloud_day_local_date IS NOT NULL;
```

No uniqueness constraint exists on device, timestamp, or value because every
successful current-status request must remain observable. `source` is `batch`,
`status`, or `cloud_daily`. Current-status readings leave all cloud provenance
columns null, so neither cloud-specific uniqueness rule affects their required
duplicate semantics.

A daily cloud reading uses the representative event's Tuya `event_time` as
`recorded_at_utc`, records the local import time separately, stores the exact DP
in `source_code`, and retains the redacted report-log row in the existing
`raw_status_json` diagnostic field. `cloud_day_local_date` is the local bucket
date selected at import. `cloud_day_timezone` stores the system timezone
identifier used to derive the bucket, or `system-local` when no stable identifier
is available; `cloud_day_utc_offset` stores that zone's signed `±HH:MM` offset at
the representative event. These fields make the daily choice auditable after a
timezone change without pretending the event occurred at midnight.

Tuya does not supply an event ID. `external_event_key` is therefore a versioned
SHA-256 persistence contract. Version 1 hashes these exact bytes:

```text
b"tuya-report-v1\0" + frame(device_id) + frame(source_code)
    + frame(event_time_ms) + frame(canonical_raw_decimal)

frame(value) = ASCII(byte_length(UTF8(value))) + b":" + UTF8(value)
```

The raw input may be a JSON number or a decimal string matching
`(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?`; it is parsed directly to a
finite, non-negative `Decimal`, with booleans and signed zero rejected.
`canonical_raw_decimal` uses the repository's non-exponent canonical decimal
format, strips redundant fractional zeroes, and renders every accepted zero as
`0`. JSON `1`, `1.0`, and string `"1"` are therefore the same event value.
`event_time_ms` is the base-10 integer with no leading sign or zero padding. The
hex digest is stored. Fixed-vector tests freeze this serialization across
releases.

The retained redacted event row is serialized separately as canonical diagnostic
JSON with sorted keys, fixed separators, UTF-8 text, and exact decimal tokens.
Fingerprinting never depends on upstream JSON key order or diagnostic formatting.

When an external key already exists, get-or-create verifies device ID,
`source_code`, event timestamp, canonical raw value, scale, source unit,
normalized kWh. Any mismatch in those stable, billing-relevant fields is a
consistency failure, not a row to reuse. The first import's diagnostic event
payload, full specification snapshot, specification-observation time, bucket
date, timezone, and offset remain immutable provenance but are not event-identity
comparands. Reordering fields or adding/changing an unrelated diagnostic field
must reuse the existing reading ID without replacing its stored snapshots. Such
a difference may emit a safe technical marker, but logs must not contain either
diagnostic blob, raw values, credentials, tokens, or personal device data.

If a later query in another system timezone selects the same external event for
another bucket, the existing reading is reused without relabelling it or
creating a duplicate. The separate daily unique index guarantees at most one
automatic cloud reading per meter and stored bucket date even when overlapping
queries select different remote events. Existing daily rows are never replaced
by a later query.

`specification_observed_at_utc` records when the specification used to normalize
a cloud event was retrieved. `raw_specification_json`, scale, and source unit
remain immutable on the imported reading; later specification changes never
reinterpret it. An ordered migration adds these nullable columns and the partial
unique index without rewriting existing history.

### `calculations`

```text
id INTEGER PRIMARY KEY
device_id TEXT NOT NULL REFERENCES devices(device_id)
start_reading_id INTEGER NOT NULL REFERENCES readings(id)
end_reading_id INTEGER NOT NULL REFERENCES readings(id)
consumption_kwh TEXT NOT NULL
unit_price TEXT NOT NULL
currency TEXT NOT NULL
total TEXT NOT NULL
created_at_utc TEXT NOT NULL
```

Calculations are immutable snapshots. Persisting derived values is intentional:
it preserves exactly what the user saw and provides a future statement record.

### Decimal and timestamp storage

- Persist exact decimals as canonical strings.
- Parse them into `Decimal` at repository boundaries.
- Store UTC ISO-8601 timestamps.
- Format values and timestamps only in the presentation layer.

## 7. Tuya integration

### Authentication and settings

The Tuya client receives transient credentials from `settings_service`;
production code does not depend on module-level credential constants. The
Client Secret is loaded from the platform credential backend only inside
background workflows. Automatic capability checks use non-interactive access;
denied or corrupt Client Secret disables only remote capability, while cached
rows, History, currency, and billing remain available. Settings and explicit
remote actions surface the Romanian recovery error. The Settings UI never
receives the secret in a field or Qt signal; it receives only
a stored-state flag and sanitized connection-test settings. Saving writes and verifies
credential secret before committing non-secret settings to the database. A
cancellation after the secret write stops before the database update, yielding
a safe retry state.

Production Security-framework operations execute in a narrowly scoped helper
process. The owning workflow polls its cancellation context and enforces a
five-second maximum, then terminates and joins the helper before returning. A
cancelled or timed-out credential write may already have completed atomically in
Keychain, but no late helper result can start a database transaction; retrying
Settings safely reconciles the non-secret metadata. Automated tests replace the
default production Keychain backend with memory and reject the production
`ro.tatatuya.app` service name. Native macOS tests and smoke runs use unique
`ro.tatatuya.app.test.*` or `ro.tatatuya.app.ci-smoke.*` services and delete
only their exact accounts.

Application settings and Tuya connection settings have separate read contracts.
Calculation preparation loads currency without requiring Client ID, Client
Secret, or region. Remote workflows request complete Tuya settings explicitly.
The existing key-value storage may remain shared, but no service may treat a
missing Tuya credential as a missing application currency. This lets persisted
calculations work after credentials expire or are removed.

The explicit Settings connection test authenticates and verifies access to the
read-only associated-device listing used by the normal refresh workflow. Its
success message reports the number of associated Tuya devices, before meter
classification; it does not claim that every associated device is billable. It
does not require unrelated Tuya API products or permissions. Client ID, Client
Secret, and region form the connection-settings identity; changing only the
local billing currency does not invalidate a successful connection test.

The normal connection test remains limited to device discovery and does not
promise Device Log access. Cloud-history capability is checked only when the
user explicitly imports it in Calculate. Permission/service failures and
rate-limit failures have Romanian recovery messages. An empty successful result
uses a combined empty-or-no-longer-available message because the endpoint does
not expose a documented retention signal. Local calculations remain usable for
projects that can list devices but cannot query report logs.

### Device discovery

Discovery updates cached device metadata without mutating Tuya. A disappeared
device is not allowed to orphan or delete historical data. After a successful
complete listing, returned devices are marked present and omitted cached devices
are marked absent. A failed listing does not change presence state.

The associated-user device endpoint is cursor-paginated. Follow `has_more` and
`last_row_key` until all pages are loaded, and deduplicate devices by Tuya ID.
Sensitive device credentials such as `local_key` are redacted before raw
metadata is retained for diagnostics.

### Specifications

For each applicable device, retrieve or load its specification and locate
exactly one of the explicit cumulative-forward-energy aliases:
`forward_energy_total` or `total_forward_energy`. Cache the selected original
code, unit, and scale on the device, while retaining the redacted raw
specification for diagnostics.

A specification is supported only when it contains exactly one supported alias,
an integer scale from 0 through 123, and a supported Wh or kWh spelling (`Wh`,
`W·h`, `kWh`, or `kW·h`). The upper bound is the largest common scale for which
raw integer `1` remains within the 128-character canonical quantity limit after
the extra three-place Wh-to-kWh conversion. A valid specification without either
alias or with a
conclusively unsupported unit classifies the device as unsupported. Missing,
malformed, ambiguous, or invalid scale data remains an explicit recoverable
error rather than an unsupported classification.

`domain.energy` owns the HTTP-, SQLite-, and UI-independent decimal bound. It
computes canonical fixed-point length arithmetically from the finite `Decimal`
tuple. Both the raw energy quantity and the result after scale/unit normalization
must fit at most 128 characters before any fixed rendering or repository call.
This closes scale-driven expansion independently of the raw token spelling and
applies equally to current batch/status capture and cloud import.

Code, unit, and scale remain cached for local metadata and diagnostics, but the
first release revalidates the specification once at the start of every batch or
individual reading-capture workflow. This deliberately favors billing
correctness over one fewer API request: a scale or unit can change while the
`forward_energy_total` code remains unchanged. Each saved reading retains the
exact specification used for that capture.

### Batch status

The endpoint accepts at most 20 comma-separated device IDs. The reading service
chunks larger lists, maps each result back by device ID, and records every usable
result. The matching energy specifications are revalidated before each device is
included in a batch request. After a batch response completes, all usable results
are normalized and inserted through one atomic capture-store operation; it does
not open one transaction per meter. One device parse/normalization failure must
not discard other usable results, while a database failure rolls back the whole
response capture and returns its documented failure.

### Cloud status-report history

Cloud import data comes from Tuya's documented read-only
[`GET /v2.1/cloud/thing/{device_id}/report-logs`](https://developer.tuya.com/en/docs/cloud/269c6a6b6b?id=Kduvi4xnjhav2)
endpoint. It sends the exact selected energy DP code, UTC epoch-millisecond
bounds, `size=99`, and the returned `last_row_key` while `has_more` is true.

One load has these fixed safety limits:

- Exactly the most recent seven local calendar dates, including today. The
  lower bound is local midnight six dates before today and the upper bound is
  the current instant.
- At most 50 pages and 4,950 returned rows before deduplication.
- At most 1 MiB (1,048,576 bytes) of raw response-body data and 1 MiB of decoded
  text per successful page, and 10 MiB (10,485,760 bytes/characters) for each of
  those two counters across the complete load.
- At most 64 KiB (65,536 bytes/characters) of raw and decoded error-body data;
  an oversized error body is discarded without parsing or retention.
- A five-second timeout for each HTTP request and a 30-second monotonic
  wall-clock deadline for the complete workflow.
- At least 250 ms between every physical request start, including retries and
  the page following a retry. A documented rate-limit response may be retried at
  most twice, after 500 ms and 1,000 ms, within the same deadline.

The cloud-history service derives DST-aware bounds for the fixed recent-seven-
date window and the gateway validates every returned timestamp. The lower bound
is local midnight six dates before today and the upper bound is the current
instant. Each boundary is converted through the captured timezone so 23-hour
and 25-hour DST days remain correct. The gateway rejects out-of-range events,
explicitly wrong-device results, unexpected DP codes, empty pages that claim
more data, missing or repeated cursors, negative/non-integer pagination fields,
and conflicting aliases or changing `total` metadata when it is supplied.
The parser accepts the documented snake_case pagination fields and the observed
v2.1 camelCase `hasMore`/`lastRowKey` fields. A terminal `hasMore: false` page may
omit device ID, `total`, and `logs`; this is a valid empty result. An omitted
device ID is not evidence of another meter because the request URL is already
device-scoped, while an explicit returned ID must still match. Hitting any bound
rejects the whole load with no persistence and asks the user to retry later.

These limits are enforced by the HTTP transport, not by a gateway after it has
received a materialized mapping. Report-log requests explicitly use identity
content encoding; any other content encoding is rejected. A declared
`Content-Length` over the applicable limit is rejected before reading, but an
absent or dishonest header never bypasses the limit: the transport reads fixed
chunks only up to the smaller of the per-page cap and the gateway-supplied
remaining total allowance, plus one byte. It closes the response as soon as
either raw allowance would be exceeded and performs no decode or JSON parse for
that body. A response stream that rejects bounded `read(size)` calls fails
closed; production code never retries with parameterless `read()`. It then
decodes strict UTF-8, checks the smaller of the per-page and remaining decoded
allowances, and only then parses JSON. The bounded transport
response carries raw-byte and decoded-character counts; the gateway subtracts
them from both remaining budgets before requesting the next page. A page that
would cross either 10 MiB total is therefore rejected before JSON parsing.

JSON decoding uses exact integer and decimal token hooks; no JSON number is
first materialized as a Python binary `float`. Raw event values accept only a
JSON number or strict decimal string of at most 128 UTF-8 bytes and 64
significant digits. Their finite decimal tuple is inspected before fixed-point
formatting and rejected when its exponent would produce a canonical raw decimal
longer than 128 characters. The shared normalization rule separately checks the
post-scale kWh value against the same bound, so an accepted raw value and an
independently supplied scale cannot amplify during fingerprinting, persistence,
or diagnostics. Boolean, null, array, object, non-finite, negative, or values
normalizing above `1E+30` kWh are rejected. Different canonical values at the same
device/code/timestamp are ambiguous and reject the load; exact repeats are
deduplicated.

Tuya documents seven-day retention for the free
[Device Log service](https://developer.tuya.com/en/docs/iot/device-log-service?id=Kacpq0nn1bioy)
and optional longer paid retention. The gateway distinguishes observable
permission/service and rate-limit errors, but the API does not document
retention metadata for an empty success. Empty history and history no longer
available therefore share one honest UI state. TataTuya must not claim parity
with consumer-app history or fall back to private mobile APIs.

The report-log response supplies `code`, `value`, and `event_time`, but not a
per-event unit, scale, schema version, or event ID. This is not an absent
normalization contract: Tuya defines these rows as values reported by the
requested DP code, and the official
[things-data-model contract](https://developer.tuya.com/en/docs/cloud/bd68171262?id=Kcp4utbhzzfgo)
defines a value DP's parameter as its actual value multiplied by ten to the DP
model's scale. The official
[DP protocol](https://developer.tuya.com/en/docs/iot-device-dev/TuyaOS-iot_abi_dp_ctrl?id=Kcoglhn5r7ajr)
likewise states that value DPs are transmitted as integers and interpreted by
dividing with their configured decimal scale. Unit and scale belong to the DP
model, while report rows carry instances of that DP value.

The service therefore retrieves and timestamps the exact status-DP
specification before page one, verifies the same code/unit/scale after the final
page, and normalizes every matching report with the existing exact energy rule.
It never derives scale from a value's magnitude. A semantic specification change
during the load rejects all results, and the accepted snapshot remains attached
to every persisted reading.

Sanitized real-account fixtures remain release-rehearsal evidence for endpoint
entitlement, actual meter cadence, pagination, empty history, and the observed
retention boundary; they are not a global application feature flag. Permission,
service, rate-limit, and empty results are ordinary bounded runtime outcomes.
Pagination overlap, repeated cursors, rate-limit retries, malformed responses,
and size-limit failures use deterministic synthetic fixtures. TataTuya never
deliberately exceeds its request pace or manipulates live data to force them.

Only after every page and row passes validation does the service partition
events with the operating system timezone captured at query time. It selects the
earliest event at or after local midnight for each returned local date, retains
the real event timestamp, and builds one `cloud_daily` reading candidate per
day. Empty dates create no reading.

Cancellation is part of the gateway contract, not only a Qt flag. It is checked
before authentication, immediately after authentication before the original
authenticated request, between pages, during pacing/rate-limit waits, before the
second specification request, and before persistence. The report-log transport
must be able to abort an in-flight request or finish it within the five-second
request timeout.

### Asynchronous workflow cancellation and shutdown

Every worker receives a UI-independent `CancellationContext` created before its
thread starts. It contains an idempotent cancellation signal, a monotonic
absolute workflow deadline, `checkpoint()`/remaining-time operations, and an
interruptible wait used instead of `sleep`. `WorkflowThread` invokes a callable
that accepts this context; Qt interruption is only an adapter signal and is not
the service cancellation contract. Services and infrastructure accept the
context explicitly. Outside a reserved current-status capture boundary, they
check it before work, before every remote call or retry, between collection
items/pages/batches, before a new database transaction, and before publishing a
result. Inside that boundary, cancellation is latched but deliberately checked
only after the required response capture resolves. Once canceled, a workflow
starts no additional remote call, backoff, or database transaction except the
single status-capture transaction explicitly reserved by an already-started
current-status request.

Every ordinary Tuya request uses the lesser of five seconds or the context's
remaining time as its end-to-end transport timeout, covering resolution,
connection, and body reads; an implementation whose timeout cannot bound all
three must provide an abortable request instead. Production uses an abortable
Qt Network request with manual redirect policy, an absolute deadline timer,
cancellation polling, and bounded `readyRead` draining; abort covers DNS,
connection, TLS, headers, and body transfer. SQLite uses a busy timeout no
greater than five seconds. The complete workflow deadlines are:

| Workflow | Deadline |
|---|---:|
| Bootstrap, including its startup refresh | 120 seconds |
| Manual Refresh | 120 seconds |
| Settings load or save | 15 seconds |
| Settings connection test | 30 seconds |
| Individual Status | 15 seconds |
| Calculation preparation or save | 15 seconds |
| Tuya Cloud import | 30 seconds |

Cancellation has explicit write boundaries. Read-only stages stop at the next
checkpoint. Device-discovery cache changes are one atomic complete-discovery
transaction and never mark devices missing after a canceled or partial listing.

A current-status capture is the deliberate exception to the ordinary pre-
transaction cancellation check. The service revalidates every required energy
specification first, checks cancellation, and only then starts an individual or
at-most-20-device batch status request. Starting that request reserves one
cancellation-safe capture boundary and five seconds of the remaining workflow
deadline for its possible post-response normalization-and-persistence stage. The
status transport receives at most the lesser of five seconds or the remaining
time after that reservation; if no positive request budget remains, the service
fails the deadline before starting the request. If the request returns usable
values, the service normalizes them within that reserved stage and invokes
exactly one bounded atomic transaction that inserts every usable reading from
that completed response. SQLite's busy timeout is the lesser of five seconds or
the reserved stage time still remaining after normalization. This transaction
may start after cancellation and either commits all usable readings or rolls
them all back on a database error; it is never split per meter and is not
retried during shutdown. Cancellation is observed immediately after that
transaction and prevents the next specification, status, batch, or other remote
request. Only one current-status capture workflow may hold this boundary
application-wide;
Refresh and individual Status triggers are mutually disabled until it is
released.

The service depends on a narrow `CurrentStatusCaptureStore` port whose `add_all`
operation receives the complete tuple of normalized readings for one individual
or batch response. Its SQLite adapter owns begin/insert/commit/rollback and
returns persisted readings only after the single transaction commits. Neither
the service nor UI loops over repository `add()` calls that commit independently.

Settings save and calculation save do nothing when canceled before their atomic
transaction; once that transaction starts, it completes as one commit or
rollback, suppresses stale UI delivery, and starts no chained refresh. Cloud
daily import checks immediately before its transaction and before commit, rolls
back if cancellation wins before commit, and retains a commit that already
completed.

An application-level `WorkerOwner` created during UI composition owns every
asynchronous UI workflow worker/thread—including bootstrap, refresh, Settings,
status, calculation save, and cloud import—from before start until its `finished`
signal. Windows and dialogs observe operations but never become their lifetime
owner. Cloud-import dialog close requests cancellation, disconnects or
generation-invalidates delivery to that dialog, hides/rejects immediately, and
transfers no object-destruction responsibility. Other workflows may defer their
observer's close when product semantics require completion, but the observer
still does not own or destroy the live thread. The owner releases worker and
thread references only after `finished`.

Every normal quit path—main-window close, application menu/Dock quit, and last-
window close—is routed through an application shutdown coordinator before
`QApplication.quit()` is called. UI composition disables Qt's automatic
last-window quit. Main-window close delegates to the coordinator, application
quit events are intercepted at the application boundary, and code must call the
coordinator rather than `QApplication.quit()` directly. If workers are active,
the coordinator:

1. Defers the triggering close or quit event.
2. Shows a Romanian non-blocking closing state and disables new actions.
3. Requests transport/workflow cancellation for every owned worker.
4. Keeps the Qt event loop processing signals; it never calls `thread.wait()` on
   the UI thread or enters a polling loop.
5. Calls `QApplication.quit()` exactly once after the owner reports no active
   workers, then permits object destruction.

The maximum shutdown-drain bound is eleven seconds from the coordinator's
cancellation request. The longest case is the one exclusive current-status
capture boundary: up to five seconds remain for its already-started request, up
to five seconds for its required normalization and single atomic capture
transaction including lock acquisition/commit, and up to one second for worker
completion delivery.
Other workflows drain within six seconds because they may finish at most one
already-started five-second I/O or SQLite transaction plus completion delivery.
Security-framework work is owned by a killable helper process, and database
classification, snapshots, export, integrity checks, and key probes all receive
the workflow cancellation context and SQLite progress handlers. If cancellation
interrupts a conversion, recovery receives its own five-second safety budget:
it restores or preserves one valid database before cleanup and never deletes the
last valid copy merely to meet the timer.
Workflow deadlines still apply if they expire sooner. A capture transaction
that cannot commit within its five-second stage bound rolls back and reports the
safe database failure path; it never retries during shutdown. Late success/
failure signals may complete owner cleanup but cannot update closed dialogs or
restart work. `aboutToQuit` is a final assertion/cleanup hook, not the place that
initiates a blocking wait or first takes ownership of a live thread.

The official frequency-control table currently lists the corresponding v2.0
report-log route at ten requests per second, while the selected API reference is
v2.1. TataTuya's four-or-fewer request starts per second remains deliberately
conservative unless
Tuya documents a more restrictive limit:
[Tuya frequency control](https://developer.tuya.com/en/docs/iot/frequency-control?id=Kcojz2r2dg1f6).

### Normalization

1. Find the exact selected cumulative-forward-energy code in returned statuses.
2. Find its matching specification.
3. Validate numeric raw value, scale, and unit.
4. Apply the decimal scale.
5. Convert Wh to kWh when necessary.
6. Create a reading with both normalized and diagnostic source data.

The application rejects ambiguity instead of selecting an arbitrary energy code.

## 8. Service workflows

### Refresh workflow

```text
load settings
  -> authenticate
  -> list devices
  -> update device cache/specifications
  -> batch IDs in groups of 20
  -> retrieve statuses
  -> normalize and store successful readings
  -> return per-device results and failures
```

The UI starts this workflow once after configured application bootstrap and once
after saving a connection that was successfully verified. Cached rows remain
visible while it runs. There is no periodic timer.

Refresh results also include cached historical meters confirmed absent from the
latest discovery. They keep local calculation/history/info actions, while Status
is disabled. Unsupported devices without readings are filtered from the primary
table without turning the refresh into a false partial failure.

Bootstrap models local data and remote capability separately. Cached meter rows
with usable history are returned and displayed even when Tuya settings are
missing or incomplete. The Settings-required empty screen is used only when no
such local rows exist. With cached rows but no remote capability, the main window
shows a Romanian settings warning, keeps `Calculează`, `Istoric`, and cached
`Info` enabled, and disables `Actualizează`, `Status`, and cloud import. Clearing
credentials, if supported, or otherwise loading incomplete Tuya settings
recomputes remote capability without discarding rows or application currency.

### Individual status workflow

```text
load cached device
  -> revalidate energy specification
  -> cancellation checkpoint
  -> request status and enter the capture boundary
  -> retain raw status for display
  -> normalize forward energy when present
  -> store the usable reading in the boundary's one atomic transaction
  -> return status and capture result
```

No specification request occurs after the status request. Cancellation after
revalidation but before the status request starts prevents that status request;
cancellation after it starts permits only its required capture transaction and
suppresses stale presentation delivery.

### Calculation workflow

```text
load selected readings
  -> validate same device and chronological order
  -> validate ending value >= starting value
  -> resolve explicit or remembered meter price
  -> calculate exact consumption and total
  -> persist immutable calculation
  -> update remembered meter price
  -> return calculation
```

### Cloud daily-import workflow

```text
open Calculate with all persisted readings
  -> explicitly request the most recent seven local dates
  -> derive the fixed DST-aware bounds and current-instant cap in the service
  -> retrieve and timestamp the current specification
  -> page through read-only status-report logs within fixed limits
  -> validate the complete result and recheck the specification
  -> reduce to the earliest real event per returned local calendar day
  -> begin one database transaction
  -> get-or-create immutable cloud_daily readings by day and event identity
  -> commit the complete daily set
  -> reload ordinary persisted readings in the calculation selectors
  -> preview/save through the unchanged ID-based calculation workflow
```

The cloud-history service owns query validation, normalized candidate creation,
local-day reduction, and orchestration through narrow gateway and transactional
import ports. It does not import the concrete Tuya client or SQLite repository
and does not create calculations or price preferences.

All remote validation and daily reduction finish before the import transaction
begins. The transaction processes the complete reduced set. For each day, it
reuses an existing immutable `cloud_daily` row when present; otherwise it checks
the external key and inserts the candidate. A key mismatch, daily conflict,
repository failure, or cancellation before commit rolls back every new reading
from that load. Once committed, imported readings remain even if the user closes
Calculate or cancels calculation saving.

The calculation dialog receives only the refreshed persisted `Reading` models.
`BillingService` therefore remains unchanged in concept: both selected inputs
have IDs, may have any supported provenance, and flow through the same domain
validation and immutable calculation transaction.

## 9. Error architecture

Expected errors cross the service/UI boundary as a shared exception similar to:

```python
class UserFacingError(Exception):
    title: str
    message: str
    technical_details: str | None
```

Examples include missing settings, Tuya authentication failures, unsupported
units, unavailable energy fields, invalid prices, insufficient readings, and
meter resets.

HTTP error bodies are diagnostic input, not trusted display text. The transport
applies the 64 KiB raw/decoded caps before strict UTF-8 decoding or JSON parsing.
Within the cap, JSON bodies use exact decimal handling and recursively redact
sensitive fields. Invalid UTF-8, oversized, or opaque non-JSON bodies are not
retained; diagnostics record only a safe format/oversize marker and bounded
length information so an unknown credential cannot leak through raw upstream
text.

Diagnostic decimal rendering uses the same arithmetic 128-character canonical
bound before calling fixed-point formatting. An oversized or non-finite Decimal
in a successful device, specification, individual-status, or report-log payload
rejects that payload before it can reach persistence or the Status UI. A batch
status parser omits only the affected device entry, allowing the reading service
to preserve other usable results from that response. Error handling must remain
available for hostile responses, so an
oversized Decimal inside a bounded HTTP error, a `success=false` envelope, or a
redacted nested error is replaced with the fixed `[DECIMAL_DISCARDED]` marker.
The original scalar is not rendered, logged, retained, rounded, or truncated.

Worker code returns failures to the main thread. The UI owns dialog creation and
always restores loading controls in success and failure paths. Unexpected errors
are logged locally and wrapped in a generic Romanian message.

## 10. UI architecture

- Romanian is confined to application-visible text and expected user-facing
  errors. Source identifiers and all developer-facing repository material use
  English.
- Dialogs receive services or presentation-ready models, not global clients.
- Table widgets do not call Tuya or SQLite directly.
- User-facing strings are centralized in `ui/text.py` or an equivalent resource
  so terminology stays consistent and future translation remains possible.
- Formatting helpers own Romanian decimal, currency, date, and time display.
- QSS defines appearance; Qt layouts and content size hints define geometry.
- Informational tables disable item selection; interaction is provided through
  explicit controls rather than table-cell state.
- Main-window content selection depends first on cached local rows, not Tuya
  credential completeness. Remote capability controls only the warning banner,
  `Actualizează`, `Status`, and cloud-import availability; cached Calculate,
  History, and Info navigation remains reachable.
- Calculation reading selectors are filtered by local calendar date in the UI;
  the service still receives the two persisted reading IDs.
- Calculation preparation returns currency, remembered price, saved readings,
  and optional saved-reading defaults even when zero or one local reading exists.
  Insufficient local data is an in-dialog state, not a reason to block
  opening the dialog and using the cloud import action.
- Calculation preparation reads application currency independently from
  optional Tuya credentials. Missing remote settings disable only the import
  panel; persisted-reading selectors and calculation remain available.
- The calculation dialog owns presentation state for the fixed seven-day import
  disclosure and loading/empty/error states. It does not construct Tuya
  query parameters, normalize values, bucket days, fingerprint events, or write
  imported readings.
- Tuya Cloud performs a request only after the explicit import action. A closed
  dialog detaches its operation subscription; late worker results never update
  the selectors.
- A successful import reloads the unified persisted-reading context and clearly
  reports that one daily reading per returned date remains saved locally. The
  user may combine local and cloud-origin readings freely for the same meter.
- Long operations run through reusable Qt worker infrastructure. Every workflow
  thread is registered with the application-level `WorkerOwner`; windows and
  dialogs hold operation handles/subscriptions rather than sole thread references.
- The shutdown coordinator intercepts quit before the event loop stops, requests
  cancellation, presents the closing state, and completes quit from the owner's
  all-finished signal. No UI callback performs a blocking thread wait.
- Settings initialization, loading, connection testing, saving, and commit run
  through worker-owned operations. A Settings dialog never owns a live SQLite
  connection and receives its initial model after background loading.
- Labels receiving device metadata, diagnostics, or service errors explicitly
  use `Qt.PlainText` and do not open external links. Large diagnostics continue
  through `QPlainTextEdit.setPlainText()`.

## 11. Testing architecture

### Unit tests

- Scale 0, 2, and 3 normalization
- Wh-to-kWh conversion
- Unsupported or ambiguous specifications
- Romanian comma parsing and defensive dot parsing
- Remembered-price fallback
- Exact consumption and money calculations
- Reversed, identical, and reset readings
- Romanian formatting
- Versioned cloud-event fingerprint fixed vectors and reuse-field verification
- Inclusive local-date to UTC millisecond conversion across ordinary and DST
  transition days, including the current-instant cap
- Earliest-event-per-local-day selection with the real event timestamp preserved
- Exact report-value validation for number/string equivalence, booleans, null,
  composite, non-finite, negative, oversized, repeated, and same-time-conflict
  cases
- Shared cancellation-context checkpoints, interruptible waits, deadline
  expiry, and each workflow's pre-transaction/inside-transaction behavior
- Individual Status specification-before-status ordering and the reserved
  cancellation-safe capture boundary

### Integration tests

- Empty-database migrations
- Table-driven plaintext-to-SQLCipher conversion faults at every credential,
  snapshot, export, verification, replacement, rollback-cleanup, and marker-
  cleanup boundary. Both cancellation and ordinary exceptions must preserve a
  valid database, and restart must converge with identical schema, row counts,
  row hashes, and Client Secret placement.
- Cancellation while the separate conversion-recovery budget is active must
  retain the last valid copy. Blocked foreign-key and integrity verification in
  that production-created recovery context must be interrupted through the
  SQLite progress handler rather than merely observing a checkpoint before the
  query. A failed recovery restore keeps both the active and rollback copies for
  the next startup.
- Device upsert without history loss
- Equal repeated readings retained
- Every successful status call recorded
- Immutable calculations survive restart and setting changes
- More than 20 devices are chunked correctly
- Partial Tuya failures retain successful results
- Cancellation immediately after an individual or multi-result batch response
  persists all usable results through exactly one atomic capture transaction,
  starts no later remote call, and handles a busy database by committing within
  the five-second stage bound or rolling the whole response back
- Recorded Tuya fixtures cover list, specification, batch, and individual status
- Report-log pagination follows `has_more` while enforcing page/event/payload/
  elapsed-time limits, exact numeric parsing, cursor and total consistency,
  request pacing, bounded rate retries, and cancellation between pages
- The transport rejects success bodies over the declared or streamed raw limit
  before decode/parse, rejects decoded-limit and invalid-UTF-8 bodies, safely
  caps oversized JSON/non-JSON error bodies, and accounts totals across pages
- Invalid dates, future or out-of-range events, empty pages with `has_more`,
  malformed rows, specification changes, and result-limit failures persist
  nothing
- Clean databases and migration-3 databases receive both cloud partial unique
  indexes without changing current-status rows
- One seven-day load creates no more than one `cloud_daily` reading per returned
  local date; repeating it 100 times or importing again as the rolling window
  advances reuses overlapping daily rows
- Reimporting the same billing-relevant event after diagnostic event fields or
  unrelated specification fields are reordered, added, or changed reuses the
  same reading ID and preserves the first snapshots
- A different or earlier event for an already imported day does not replace the
  immutable daily reading, while `batch` and `status` still store every usable
  response on that day
- Failure or cancellation at each daily-import write boundary rolls back the
  complete load; canceling or closing after a committed import does not delete it
- Imported daily readings and provenance survive restart, settings/specification
  changes, and loss of Tuya connectivity
- An existing local reading and imported daily reading can form one ordinary
  immutable calculation through the ID-based billing workflow
- Sanitized live fixtures cover only safely observed endpoint, target-DP,
  pagination, empty-history, retention, and configured entitlement behavior;
  deterministic synthetic fixtures cover overlap, repeated cursors, rate limits,
  malformed responses, and other adversarial branches

### UI tests

- Action buttons have visible text and usable rendered geometry
- Rows and dialogs fit representative Romanian labels
- Price fallback is understandable
- Calculation selectors filter readings by local date and cap popups at 15
  visible entries before scrolling
- Informational table cells cannot be selected
- Custom errors open the shared modal
- Controls recover after asynchronous errors
- Main screen is rendered and screenshot-inspected for layout changes
- Calculate opens with zero, one, or many persisted readings independently of
  Tuya credentials
- Real application bootstrap with cached history, application currency, and no
  Tuya credentials renders meter rows plus the Romanian warning; Calculate,
  History, and Info open from row actions while Refresh, Status, and cloud import
  are disabled
- Loading incomplete Tuya settings—and credential clearing if supported—keeps
  cached rows and currency available and updates remote-action states without
  replacing the table
- Explicit fixed-seven-day cloud import, local-persistence disclosure, loading
  cleanup, stale result suppression, combined empty/unavailable state,
  permission/rate/limit/generic errors, date filtering, and unified defaults are exercised with
  representative Romanian text
- Successful import refreshes unified selectors; local and cloud-origin readings
  can be combined, and repeated/overlapping imports add no duplicate options
- Dialog close during an in-flight page request hides the dialog, transfers
  lifetime to `WorkerOwner`, stays responsive, and cannot apply a late result
- Main-window close, application-menu/Dock quit, and last-window quit during an
  in-flight request enter the closing state, keep processing events, destroy no
  running `QThread`, and quit only after bounded cancellation emits `finished`
- Refresh and every individual Status trigger are mutually disabled while the
  exclusive current-status capture boundary is active
- Quit is exercised separately during bootstrap, manual Refresh, Settings
  loading/testing/saving, Status, calculation preparation/saving, and cloud
  import; no canceled workflow starts another call or write outside its safe
  commit boundary, ordinary cases drain within six seconds, and an in-flight
  current-status capture drains within eleven seconds
- The expanded calculation dialog is rendered and screenshot-inspected in light
  and dark palettes with usable control geometry

## 12. Packaging and release

- Pull requests and pushes to `main` run a read-only correctness workflow with
  Ruff, Pyright, the complete Linux-verifiable suite, both Qt transport/UI test
  orders, and `pip check`. A native Apple Silicon job installs the hash-locked
  macOS graph, repeats Pyright against those platform dependencies, and runs
  disposable-Keychain/SQLCipher tests, including a populated plaintext
  conversion and installed-launcher encryption check.
- Build an Apple Silicon `.app` with PyInstaller.
- Bundle styles, icons, and database migrations explicitly.
- Create an unsigned `.dmg` containing the `.app` and Applications shortcut.
- GitHub Actions installs the Python 3.12/macOS ARM64 hash lock, audits that
  exact locked dependency graph without treating the locally built application
  as a third-party package, tests, and builds with a read-only token. A separate
  publication job receives `contents: write` only after downloading the verified
  artifact and attaches the DMG, SHA-256 checksum, and CycloneDX SBOM to a draft
  release. It must refuse to replace an already-public release automatically.
  Every third-party action is pinned to a full commit SHA and checkout never
  persists credentials.
- Document the first-launch Control-click/right-click `Open` workaround required
  by Gatekeeper for an unnotarized application.
- Perform a clean-machine/fresh-database rehearsal before promoting the draft
  to a public release or calling the release ready.

Repository settings make the three pre-merge correctness jobs required checks
after their signal is stable; workflow files cannot enforce branch protection
by themselves.

No release may contain credentials embedded in source or build artifacts.
