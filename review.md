# Phase 9 Review

## Finding

### P3 — Capture the calculation tab before closing the last dialog

In `tests/ui/test_history_dialog.py:110`, the first window is closed before a
second dialog is opened to render the calculation tab. Under the offscreen Qt
platform, the resulting `history-calculations.png` contained large black
regions, yet the file-size assertion still passed.

This makes the Phase 9 screenshot verification unreliable. Render both tabs
from the same visible dialog, or keep the first dialog open, and verify key
widget geometry and visibility in addition to the PNG file size. An isolated
render confirmed that the calculation tab itself is displayed correctly.

## Verdict

No P0–P2 findings were identified. The Phase 9 workflows otherwise satisfy the
documented acceptance gate:

- History is read-only.
- Calculation details are resolved from immutable records.
- Info has no mutation controls.
- Status preserves raw diagnostics.
- Each usable individual Status request stores a distinct reading.

## Verification

- Focused Phase 9 tests: 35 passed.
- Full suite: 121 passed.
- Rendered `Citiri`, `Calcule`, `Info`, and `Status` screens were manually
  inspected.
- `git diff --check` passed.
- Ruff and pyright were unavailable in the environment.
