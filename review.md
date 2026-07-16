# Phase 6 Re-review

The latest changes satisfactorily resolve the application-level shutdown and
neutral startup-state findings. Phase 6 should remain incomplete until the one
remaining bootstrap-failure finding below is resolved.

## Remaining finding

### P2 — Exit the loading page when bootstrap fails

**Location:** `src/tatatuya/ui/main_window.py:156`

Bootstrap uses the same generic `_operation_failed()` handler as a device
refresh. That handler updates the summary and emits an error, but it does not
change the stacked page or clear the bootstrap state.

When database initialization or initial-state loading raises an exception, the
error is emitted but the main area remains indefinitely on
`Se încarcă datele salvate`. The summary simultaneously says
`Actualizarea nu a reușit`, and the refresh button becomes enabled. Pressing
refresh then incorrectly switches to the credentials-required page because
configuration was never loaded.

This is an error-path regression from introducing the neutral loading page. It
conflicts with the product requirement for failures to explain a usable next
step and with the architecture requirement that worker failure paths restore a
coherent UI state.

Use a bootstrap-specific failure handler that exits the loading page, presents
an appropriate local-data failure or retry state, and does not infer that
credentials are missing. Add a UI test that makes bootstrap fail and verifies
the resulting page, summary, and available recovery action.

## Resolved findings

### Application-level quit during active work — resolved

`MainWindow` now connects `QApplication.aboutToQuit` to a synchronous worker
shutdown routine. Both direct window closure and application-level quit wait for
owned work to finish before Qt destroys the thread objects.

The independent `QApplication.quit()` reproduction completed with exit code 0,
without the previous QObject or running-thread diagnostics. A subprocess UI test
now covers this exact application-exit path.

### Neutral state during local bootstrap — resolved

A distinct Romanian loading page is displayed while settings and cached data
are still unknown. It does not claim that credentials are missing, and a delayed
successful bootstrap switches to the correct configured state afterward.

The loading page rendered cleanly at 700 x 450 with visible, non-overlapping
text. A delayed-bootstrap UI test verifies that the settings-required wording is
absent while loading.

### Settings-required layout — resolved

The heading and explanatory text receive usable geometry without overlap at
both 1180 x 680 and 700 x 450. Tests check height-for-width behavior and label
intersection.

### Representative table content — resolved

The representative long Romanian meter name remains fully visible at the
default window size. Recoverable error details do not expand the state column
and remain available through its tooltip.

### Startup database work and cached-reading query — resolved

Database initialization and initial reads run outside the GUI thread. Cached
latest readings are retrieved in one repository query with tested timestamp-
then-ID selection behavior.

## Verification

- Focused UI, formatter, and database tests: `19 passed`.
- Complete test suite: `71 passed`.
- Independent application-level quit during refresh: exit code 0.
- Direct window closure during refresh completed safely.
- Loading and representative table screens were rendered with the application
  stylesheet and inspected.
- A forced bootstrap exception reproduced the remaining incoherent loading
  state.
- `git diff --check` passed.
- No product code was modified as part of the re-review.
