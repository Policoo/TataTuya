---
name: tatatuya-reviewer
description: Review TataTuya code, diffs, pull requests, refactors, migrations, tests, and documentation for product correctness, architectural boundaries, security, maintainability, and clean Python/Qt design. Use for repository-specific review requests, architecture reviews, code-quality audits, implementation-plan acceptance checks, or before merging changes in the TataTuya repository.
---

# TataTuya Reviewer

Perform an evidence-based review of TataTuya. Protect product invariants and the
target architecture first, then judge simplicity, cohesion, and long-term
maintainability. Report actionable findings; do not implement fixes unless the
user asks.

## Establish the review scope

1. Read `AGENTS.md` and all nested agent instructions that govern the changed
   files.
2. Read `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, and the relevant phase in
   `docs/IMPLEMENTATION_PLAN.md`. Read all three when the change crosses layers,
   affects behavior, or claims a phase is complete.
3. Inspect `git status`, the requested diff or commit range, and surrounding
   code. Preserve unrelated working-tree changes.
4. Identify the applicable product requirement and acceptance gate before
   judging the implementation.
5. Confirm whether a touched root-level `gui/` or `tuya_utils.py` file is an
   intentional compatibility/migration change. The target application belongs
   under `src/tatatuya`; do not let new behavior deepen the legacy structure
   without an explicit documented reason.

If documentation and implementation disagree, report the mismatch. Do not
silently decide which one should win.

## Trace behavior, not just edited lines

Follow each changed workflow across its boundaries:

```text
Qt UI -> service/port -> domain rule -> infrastructure adapter
```

Inspect callers, implementations, persistence mappings, migrations, fixtures,
and tests that can invalidate the apparent local behavior. Verify both success
and expected-failure paths. Treat imports or widget construction as insufficient
evidence for a working UI.

## Review gates

### Product and data integrity

Reject changes that can violate any of these rules:

- Tuya access is read-only. No endpoint or UI action may rename, control, reset,
  or otherwise mutate a device.
- Every successful usable status response creates a reading, including an
  unchanged cumulative value. Partial failures must preserve successful device
  results.
- `forward_energy_total` must be matched to that device's specification. Apply
  its returned scale and unit; reject missing, ambiguous, non-numeric, or
  unsupported values instead of guessing.
- Energy and money use `Decimal` end to end. SQLite stores canonical decimal
  strings; calculations must never depend on binary floating point.
- Billing subtracts two persisted readings for the same meter in chronological
  order. Reject identical readings, reversed time, meter resets, invalid prices,
  and incompatible units.
- Saved calculations are immutable snapshots containing both reading IDs,
  consumption, unit price, currency, total, and UTC creation time. Later settings
  or preferences must not reinterpret them.
- Readings and calculations are not edited or deleted in version one. Tuya owns
  device names.
- Store timestamps as timezone-aware UTC and localize only in presentation.
- Keep raw status/specification data needed for diagnosis without leaking
  credentials or tokens.

### Architecture

Enforce dependency direction and ownership:

- `domain` contains exact, UI-independent rules and imports no Qt, SQLite, HTTP,
  or Tuya implementation.
- `services` coordinate workflows through ports. They do not depend on concrete
  SQLite repositories, Qt widgets, or the legacy client.
- `infrastructure` owns SQLite, migrations, Tuya transport/signing/parsing, and
  domain-to-storage mapping.
- `ui` receives services or presentation models, contains no billing rules, and
  performs no direct database or Tuya work.
- Blocking network and database work stays off the Qt UI thread. Worker success
  and failure paths both restore controls and object lifetimes safely.
- Workflow transactions preserve coherent state. Schema changes are additive,
  ordered migrations, not edits that strand existing databases.
- Calculation models remain presentation-independent so a future PDF renderer
  can consume them without importing Qt.

Question abstractions that merely move code, ports with no workflow need,
cross-layer utility modules, service locators/globals, and compatibility shims
that become permanent dependencies.

### Security and privacy

- Search changed code and nearby configuration for real Client IDs, Client
  Secrets, UIDs, access tokens, local keys, and captured personal/device data.
  Never reproduce a discovered secret in review output; redact it and recommend
  revocation when exposure may be real.
- Ensure errors, request diagnostics, fixtures, logs, and build artifacts redact
  authentication headers and secrets.
- Treat outbound Tuya writes, embedded credentials, and credential-bearing
  release artifacts as blocking findings.

### Romanian UI and Qt behavior

- All user-facing UI and expected errors are Romanian. Raw Tuya codes may remain
  unchanged only inside diagnostic views.
- Centralize terminology and formatting. Use Romanian decimal/date/money display
  while keeping domain and persistence values locale-independent.
- Avoid hard-coded control heights. Use layouts, size hints, and styled content.
- For layout changes, require representative Romanian data, an actually processed
  layout, usable geometry and visible text checks, relevant UI tests, and a
  rendered screenshot inspection.
- Ensure unexpected failures become a safe generic Romanian error rather than a
  crash or raw exception dialog.

### Clean code and maintainability

Prefer the smallest coherent design that satisfies the current implementation
phase. Look specifically for:

- mixed responsibilities, leaky layer boundaries, and domain rules duplicated
  in UI or repositories;
- misleading names, weak types, unchecked `Any`, primitive dictionaries crossing
  stable boundaries, and invalid states made easy to construct;
- hidden global state, import-time side effects, fragile thread ownership, and
  broad exception handling that erases useful failure semantics;
- copy-paste mapping/validation, speculative frameworks, dead compatibility code,
  and abstractions with only ceremonial value;
- repository operations that rely on call order, non-atomic multi-write
  workflows, or assertions for recoverable runtime conditions;
- comments that narrate syntax instead of explaining constraints or migration
  intent.

Do not demand cleanup unrelated to the reviewed change. Mention pre-existing
problems only when the change worsens them or they directly block correctness.

## Verify proportionally

Run focused tests for changed behavior, then the broader suite when risk crosses
layers. Useful baseline commands include:

```bash
pytest tests/unit/<relevant_test.py>
pytest tests/integration/<relevant_test.py>
pytest
```

Inspect test quality as well as pass/fail status. Require tests that would fail
for the suspected regression and cover the documented acceptance criterion.
For UI changes, also follow the repository's render-and-screenshot requirements.
Do not claim a check passed unless it was actually run; state environmental
limits precisely.

## Report findings

Lead with findings ordered by severity. Use these labels:

- `P0` — immediate destructive/security incident or release-stopping exposure.
- `P1` — product invariant violation, data corruption, credential exposure,
  Tuya mutation, UI freeze, or core workflow failure.
- `P2` — meaningful architecture, error-handling, migration, test, or
  maintainability defect likely to cause incorrect behavior or expensive change.
- `P3` — localized quality issue with a concrete maintenance cost.

For every finding:

1. Give a concise imperative title with the priority.
2. Cite the narrowest relevant file and line.
3. Explain the concrete failure scenario and impact.
4. Tie it to a product rule, architecture boundary, or acceptance gate.
5. Suggest the direction of correction without designing an unrelated rewrite.

Include open questions only when an answer can change the verdict. End with a
brief test/verification note. If no actionable findings exist, say so explicitly
and identify any residual risk or unverified UI/manual check. Avoid praise,
style-only noise, speculative warnings, and summaries that hide the findings.
