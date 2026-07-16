# TataTuya Agent Guide

This repository contains a Romanian-language desktop application for reading
cumulative energy values from Tuya smart meters and calculating per-meter
energy costs.

## Required context

Before changing application behavior, read the documents relevant to the task:

- `docs/PRODUCT_SPEC.md` defines the approved user experience and business rules.
- `docs/ARCHITECTURE.md` defines the target boundaries, data model, and release design.
- `docs/IMPLEMENTATION_PLAN.md` defines the implementation sequence and acceptance gates.

If implementation and documentation disagree, do not silently choose one. Confirm
the intended behavior and update the relevant document with the code.

## Non-negotiable product rules

- The user interface is Romanian. Raw Tuya codes may remain untranslated in
  diagnostic views.
- The application is read-only with respect to Tuya. It must never rename,
  control, reset, or otherwise mutate a device.
- Every successful status request that contains a usable cumulative forward
  energy value creates a reading, even when the value is unchanged.
- Meter values must be normalized using the unit and scale returned by that
  device's Tuya specification. Do not assume a fixed scale.
- Billing uses two stored readings: consumption is the ending cumulative value
  minus the starting cumulative value.
- Use `Decimal` for energy-price calculations. Do not use binary floating-point
  for persisted quantities or money.
- A completed calculation is an immutable historical record. Store its reading
  references, consumption, unit price, currency, total, and creation time.
- Device names come from Tuya and cannot be edited locally.
- Readings and calculations cannot be edited or deleted in the first version.
- Credentials are configured through Settings; there is no first-run wizard.

## Engineering rules

- Keep Qt widgets thin. Business calculations belong in `domain`, workflows in
  `services`, and persistence/API details in `infrastructure`.
- Network and database work that can block must not freeze the Qt UI thread.
- Convert expected domain and service failures into the shared user-facing
  exception and Romanian error dialog.
- Store timestamps in UTC and display them in the operating system's local time.
- Store exact decimal quantities in SQLite as canonical decimal strings.
- Preserve raw Tuya status/specification data needed for diagnostics.
- Use migrations for every database schema change.
- Keep future PDF generation possible by making calculations independent from
  their UI presentation.

## UI verification

Do not treat successful imports or widget construction as proof that a UI change
works. For changed screens:

1. Exercise the screen with representative Romanian data.
2. Process/show the actual Qt layout.
3. Verify that controls have usable geometry and visible text.
4. Run the relevant UI tests.
5. Capture or inspect a rendered screenshot when layout is involved.

Avoid hard-coded control heights. Prefer layouts, size hints, and minimum sizes
derived from styled content.

## Change workflow

1. Identify the applicable requirement and acceptance criterion.
2. Add or update tests for business rules before or with the implementation.
3. Implement within the architecture boundaries.
4. Run focused tests, then the broader suite appropriate to the risk.
5. Update documentation when a decision, workflow, schema, or release process
   changes.

Do not publish a release while source files contain real credentials. Release
artifacts target Apple Silicon macOS and are distributed as an unsigned `.dmg`
until Apple signing/notarization is introduced.
