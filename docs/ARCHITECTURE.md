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

## 5. SQLite location and connection behavior

On macOS the database lives at:

```text
~/Library/Application Support/TataTuya/tatatuya.sqlite3
```

Development and tests can override this path. Tests use temporary databases.
SQLite connections are not shared unsafely across Qt worker threads. Transactions
must make a refresh result internally consistent: device updates and associated
readings either complete as designed or expose an explicit partial-result state.

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

Stores Tuya Client ID, Client Secret, region, and selected currency. Migration 2
removes the obsolete `tuya.account_uid` setting from existing databases.

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
first_seen_at_utc TEXT NOT NULL
last_seen_at_utc TEXT NOT NULL
```

The row caches remote metadata. `name` is refreshed from Tuya and is not a local
override.

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
```

Indexes:

```text
CREATE INDEX readings_device_time
    ON readings(device_id, recorded_at_utc);
CREATE INDEX readings_device_id
    ON readings(device_id, id);
```

No uniqueness constraint exists on device, timestamp, or value because every
successful status request must remain observable.

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

The Tuya client receives credentials from `settings_service`; production code
does not depend on module-level credential constants. Settings remain in SQLite
for this version.

The explicit Settings connection test authenticates and verifies access to the
read-only associated-device listing used by the normal refresh workflow. It does
not require unrelated Tuya API products or permissions. Client ID, Client Secret,
and region form the connection-settings identity; changing only the local billing
currency does not invalidate a successful connection test.

### Device discovery

Discovery updates cached device metadata without mutating Tuya. A disappeared
device is not allowed to orphan or delete historical data.

The associated-user device endpoint is cursor-paginated. Follow `has_more` and
`last_row_key` until all pages are loaded, and deduplicate devices by Tuya ID.
Sensitive device credentials such as `local_key` are redacted before raw
metadata is retained for diagnostics.

### Specifications

For each applicable device, retrieve or load its specification and locate the
status definition for `forward_energy_total`. Cache code, unit, and scale on the
device, while retaining enough raw data for diagnostics.

Code, unit, and scale remain cached for local metadata and diagnostics, but the
first release revalidates the specification once at the start of every batch or
individual reading-capture workflow. This deliberately favors billing
correctness over one fewer API request: a scale or unit can change while the
`forward_energy_total` code remains unchanged. Each saved reading retains the
exact specification used for that capture.

### Batch status

The endpoint accepts at most 20 comma-separated device IDs. The reading service
chunks larger lists, maps each result back by device ID, and records every usable
result. One device failure must not discard valid readings from other devices.

### Normalization

1. Find `forward_energy_total` in returned statuses.
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

### Individual status workflow

```text
request status
  -> retain raw status for display
  -> revalidate energy specification
  -> normalize forward energy when present
  -> store reading
  -> return status and capture result
```

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

HTTP error bodies are diagnostic input, not trusted display text. JSON bodies
are parsed with exact decimal handling and recursively redact sensitive fields.
Opaque non-JSON bodies are not retained; diagnostics record only their format
and length so an unknown credential cannot leak through raw upstream text.

Worker code returns failures to the main thread. The UI owns dialog creation and
always restores loading controls in success and failure paths. Unexpected errors
are logged locally and wrapped in a generic Romanian message.

## 10. UI architecture

- Dialogs receive services or presentation-ready models, not global clients.
- Table widgets do not call Tuya or SQLite directly.
- User-facing strings are centralized in `ui/text.py` or an equivalent resource
  so terminology stays consistent and future translation remains possible.
- Formatting helpers own Romanian decimal, currency, date, and time display.
- QSS defines appearance; Qt layouts and content size hints define geometry.
- Long operations run through reusable Qt worker infrastructure.

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

### Integration tests

- Empty-database migrations
- Device upsert without history loss
- Equal repeated readings retained
- Every successful status call recorded
- Immutable calculations survive restart and setting changes
- More than 20 devices are chunked correctly
- Partial Tuya failures retain successful results
- Recorded Tuya fixtures cover list, specification, batch, and individual status

### UI tests

- Action buttons have visible text and usable rendered geometry
- Rows and dialogs fit representative Romanian labels
- Price fallback is understandable
- Custom errors open the shared modal
- Controls recover after asynchronous errors
- Main screen is rendered and screenshot-inspected for layout changes

## 12. Packaging and release

- Build an Apple Silicon `.app` with PyInstaller.
- Bundle styles, icons, and database migrations explicitly.
- Create an unsigned `.dmg` containing the `.app` and Applications shortcut.
- GitHub Actions runs tests, builds on an ARM64 macOS runner, and attaches the
  `.dmg` to a GitHub Release.
- Document the first-launch Control-click/right-click `Open` workaround required
  by Gatekeeper for an unnotarized application.
- Perform a clean-machine/fresh-database rehearsal before calling a release ready.

No release may contain credentials embedded in source or build artifacts.
