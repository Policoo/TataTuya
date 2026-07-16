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
reading. It cannot produce a bill until a later reading exists.

## 2. Scope

### Included in the first version

- Configure Tuya credentials and region in Settings.
- Discover the user's Tuya devices.
- Retrieve statuses in batches and individually.
- Discover the forward-energy data point, unit, and scale from each device's
  specification.
- Store every successful energy reading in SQLite.
- Calculate a cost for one meter between two stored readings.
- Remember the most recently used price separately for each meter.
- Select a global currency of RON or EUR.
- View a meter's readings and saved calculations.
- View translated device information and raw Tuya status diagnostics.
- Display consistent Romanian error dialogs.
- Distribute an Apple Silicon application in an unsigned `.dmg`.

### Deliberately excluded for now

- Sending commands or changes to Tuya devices.
- Renaming meters in TataTuya.
- Automatic scheduled/background polling while the app is closed.
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
  history in SQLite.

## 4. Main window

### Top bar

The top bar contains:

- Application name
- Connection/loading state
- `Actualizează` button
- `Setări` button

There is no setup wizard. When credentials are absent or incomplete, the main
area directs the user to Settings.

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

When the application starts with complete saved settings, it performs this
refresh once after showing cached local data. Saving settings immediately after
a successful connection test also performs one refresh. TataTuya does not poll
periodically or refresh in the background while the application is closed.

Every successful status call creates a reading, including calls that return the
same value as the preceding reading. A failed or offline device retains its most
recent saved reading but receives no new entry.

Opening `Status` performs an individual status request. It also records a new
reading when the response contains a usable forward-energy value.

## 6. Energy extraction

The preferred Tuya status code is `forward_energy_total`. The application must
pair a status value with the matching entry from the device specification.

The normalized value is:

```text
normalized value = raw value / 10^scale
```

Rules:

- Scaled kWh values become the canonical reading directly.
- Scaled Wh values are converted to kWh.
- Raw value, scale, source unit, normalized kWh, and diagnostic response are
  retained.
- Missing, ambiguous, non-numeric, or unsupported energy data raises a clear
  error rather than being guessed.
- A changed or invalid specification must not reinterpret old readings; each
  reading stores the scale and unit used when it was captured.

## 7. Calculation dialog

The calculation dialog displays:

- Meter name
- Starting-reading dropdown
- Ending-reading dropdown
- Starting cumulative value
- Ending cumulative value
- Consumption for the selected period
- Price per kWh
- Currency
- Final total

Reading options contain an exact local timestamp and reading, for example:

```text
03.12.2026, 18:42 — 1.234,56 kWh
```

Multiple readings on the same day remain separate selectable entries.

### Defaults

- The ending reading defaults to the newest available reading.
- The starting reading defaults to the ending reading used by the meter's most
  recent saved calculation.
- With no previous calculation, the starting reading defaults to the earliest
  available reading.

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

- Fewer than two readings exist.
- Start and end refer to the same reading.
- The ending time precedes the starting time.
- The ending value is lower than the starting value, such as after meter reset
  or replacement.
- No current or remembered price exists.
- The price is malformed, zero, or negative.
- Either reading uses unsupported or incompatible units.

## 8. History

`Istoric` is a read-only dialog with two tabs.

### `Citiri`

- Date and time
- Cumulative kWh value
- Raw value
- Scale and original unit
- Source: batch refresh or individual status request

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

Credentials are stored in the local SQLite database. A failed connection test
must not be displayed as a successful setup.

### Application configuration

- Currency dropdown containing RON and EUR

## 10. Info and diagnostics

`Info` displays translated labels for device metadata obtained from Tuya. It
does not modify the device.

`Status` displays translated surrounding UI while preserving raw Tuya status
codes and values. This makes support possible without hiding technical data.

## 11. Error experience

Expected failures use a shared custom user-facing exception containing:

- Romanian title
- Plain-language Romanian message
- Optional technical details

The shared error dialog provides a close action and, when details exist, an
expand/copy mechanism. Unexpected errors are logged and converted to a generic
Romanian error instead of terminating the application.

## 12. Romanian formatting and terminology

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
