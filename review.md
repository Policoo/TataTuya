# Review of the Tuya Cloud history implementation — round 3

## Verdict

The four findings from the previous review have been fixed and now have focused
regression coverage:

- report-event exponent amplification is rejected before fixed-point rendering;
- cancellation during authentication prevents the original authenticated
  request and later repository writes;
- pacing applies to every physical report-log attempt, including retries and
  the page following a retry;
- the bounded transport no longer falls back to parameterless `read()`.

The changes are still not ready to merge or activate. One P1 resource-exhaustion
class remains: the new decimal-length protection is local to report-log event
parsing, while other untrusted Tuya decimal and scale paths can still create
arbitrarily large fixed-point strings. Some of those paths are used by current
production workflows even while cloud import remains behind its proof gate.

`HistoricalScaleContract` must remain false until this finding and the separate
Phase 13.1 real-account evidence requirements are resolved.

## Finding

### P1 — Apply decimal-amplification limits to every Tuya rendering and normalization path

The report-log adapter now safely inspects a value's decimal tuple through
`_canonical_decimal_length()` before calling `canonical_decimal()`. That closes
the exact event-value reproduction from the previous review, but it does not
protect the following adjacent paths.

#### Unbounded specification scale expands normalized persisted values

`parse_energy_specification()` in
`src/tatatuya/infrastructure/tuya/parsers.py:131` accepts any non-negative Python
integer as `scale`. `normalize_energy()` in
`src/tatatuya/domain/energy.py:31` applies that scale by changing the Decimal
exponent without bounding the resulting canonical representation.
`ReadingRepository.add()` then fixed-formats the value at
`src/tatatuya/infrastructure/repositories/readings.py:36`.

A short remote specification containing `scale=100000` therefore turns raw
value `1` into a persisted decimal string containing 100,002 characters. A much
larger but still short integer scale can exhaust memory. This affects both cloud
normalization and existing current-status capture.

The report-value cap alone cannot prevent this because the expansion is caused
afterward by the independently supplied specification scale. The statement in
`docs/ARCHITECTURE.md:461` that the raw cap prevents expansion during
normalization is therefore incomplete.

#### Successful-response and error diagnostics still fixed-render arbitrary decimals

The generic successful-response diagnostic serializer in
`src/tatatuya/infrastructure/tuya/parsers.py:219` calls
`format(value, "f")` for every finite `Decimal`. The error-redaction path in
`src/tatatuya/infrastructure/tuya/client.py:416` does the same at line 425.

A bounded Tuya body can contain a compact token such as `1e-100000`. Parsing it
as `Decimal` is compact, but either diagnostic path expands it to a 100,002-
character string. Larger legal exponent tokens can stall or terminate the
application before body limits or workflow deadlines can help. The error path
is reachable for HTTP failures and successful envelopes containing
`success=false`; the successful diagnostic path is used by device,
specification, and status parsing.

Concrete review reproductions:

```text
scale=100000, raw=1 -> normalized persistence length: 100002
Decimal("1e-100000") -> error diagnostic length: 100002
Decimal("1e-100000") -> successful diagnostic length: 100008
```

Required direction:

- Introduce one arithmetic, non-rendering decimal-size validator in an
  appropriate UI/HTTP/SQLite-independent location rather than keeping the only
  implementation private to `report_logs.py`.
- Validate that a specification scale and the resulting normalized energy value
  cannot create an unbounded canonical persisted quantity. Reject an
  implausible/unrepresentable scale as an invalid specification rather than
  guessing or truncating it.
- Apply a fixed expansion bound before every diagnostic `format(value, "f")`.
  Either reject/discard the diagnostic payload safely or retain an explicitly
  bounded exact exponent representation; never materialize the expanded form
  first.
- Preserve exact `Decimal` behavior and ordinary equivalent representations.
  Do not introduce binary floating-point, rounding, or silent truncation.
- Update `PRODUCT_SPEC.md`, `ARCHITECTURE.md`, and Phase 13.2 together so the
  accepted scale, normalized-quantity, and diagnostic-rendering bounds are
  explicit rather than implied only for raw report values.

Required regression coverage:

1. A specification with an extreme scale is rejected before normalization or
   repository insertion.
2. Boundary-valid scales and ordinary scale 0/2/3 behavior remain exact for Wh
   and kWh.
3. Extreme exponent values in batch and individual current-status responses do
   not reach fixed-point persistence or diagnostic expansion.
4. Extreme unrelated decimal fields in device, specification, and status
   success payloads fail closed before `_dump()` expands them.
5. Extreme decimal fields in bounded JSON HTTP errors and `success=false`
   envelopes fail closed before `_redact_payload()` expands them.
6. Tests prove that rejection occurs without first constructing the large
   fixed-point string.

This is a P1 blocker because the relevant values are controlled by remote Tuya
responses and the generic diagnostic/error paths are active in the current
application, independently of cloud-history activation.

## Resolved findings from round 2

1. **Report-event exponent bounds:** values whose canonical raw decimal would
   exceed 128 characters are rejected using tuple arithmetic. Tests cover
   positive/negative extreme exponents, boundary values, ordinary exponent
   equivalence, unrelated event fields, and large pagination integers.
2. **Post-authentication cancellation:** `TuyaClient` now checkpoints
   immediately after authentication. Tests cover device listing,
   specification, status, report logs, and a repository-level Refresh path.
3. **Physical-attempt pacing:** `_RequestPacer` records every actual request
   start. Deterministic tests cover retry-to-next-page spacing, multiple
   retries, slow responses, cancellation during pacing, and deadline expiry.
4. **Strict bounded reads:** a stream that rejects `read(size)` now fails closed
   without invoking parameterless `read()`, with a regression assertion for
   both call forms.

The earlier product/UI, fixed-seven-day, persistence, transaction, and layer-
direction findings remain resolved. No Tuya mutation endpoint was introduced,
and the production cloud action remains unavailable while the historical-scale
contract is unverified.

## Acceptance gate for the next iteration

Before this implementation can be accepted:

1. Every untrusted Tuya Decimal-to-fixed-point path must have a pre-render
   expansion bound.
2. Specification scale and normalized persisted quantities must be bounded and
   tested without weakening exact unit/scale normalization.
3. Successful and error diagnostic serialization must fail closed without
   expanding compact exponent tokens.
4. The new adversarial tests must fail against the current implementation and
   pass after correction.
5. The full automated suite, Ruff, Pyright, and `git diff --check` must remain
   green.
6. `HistoricalScaleContract` must remain false until the independent Phase 13.1
   real-account, cadence, entitlement, and authoritative historical unit/scale
   evidence gate passes.

## Verification performed during this review

- Focused report-log, Tuya-client, and reading-service tests: 68 passed.
- Full automated suite: 232 passed.
- Ruff: passed.
- Pyright: 0 errors, 0 warnings.
- `git diff --check`: passed.
- The specification-scale, error-diagnostic, and successful-diagnostic
  amplification paths were reproduced independently.
- No UI implementation changed in this correction round; the previously
  rendered light, dark, unavailable, and missing-settings cloud-card states
  remain the relevant layout evidence.

The green suite does not override the finding because none of its current tests
exercise the remaining scale-driven or generic diagnostic expansion paths.
