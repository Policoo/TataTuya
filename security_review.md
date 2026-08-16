# TataTuya Security Review and Remediation Plan

**Review date:** 2026-08-16

**Reviewed revision:** `f2c5413` (`main`) plus the current working tree

**Status:** fifth remediation follow-up reviewed; earlier findings are retained
below as historical implementation baselines

**Release verdict:** **not ready for a public release**

This document records the security review, the evidence behind each finding,
and the intended fixes. It is deliberately prescriptive so that a later agent
can implement the work without having to reconstruct the threat model or make
security-sensitive product decisions from scratch.

## Fifth follow-up review after the latest security changes

The latest changes address the fourth follow-up's `P2`. Blocked foreign-key and
integrity checks now run after the encrypted database has become active, through
the fresh recovery context created by the production conversion exception path.
The tests prove progress-handler interruption, preserve caller-context
independence, retain exact data through restart, and separately prove that a
failed recovery restore leaves both the active encrypted copy and plaintext
rollback plus the durable marker.

The raw SQLite connections identified in the new conversion harness and
installed-launcher test are also closed explicitly now. A warning-strict focused
run is clean. A stricter whole-suite run, however, exposed one remaining raw
SQLite context-manager leak in an adjacent recovery test and an independent
`HTTPError` response leak in `UrllibTransport`. Both are localized `P3` cleanup
defects; no new `P0`, `P1`, or `P2` finding was identified.

Public release remains **not ready** because the clean Apple Silicon rehearsal
and private historical-identifier decision are still unchecked manual gates.
The two `P3` defects should be fixed before merging the security-remediation
work, but they do not replace or broaden those release blockers.

| Priority | Current finding or gate | Release effect |
|---|---|---|
| P3 | `UrllibTransport` does not close caught `HTTPError` response bodies | Fix the nondefault adapter's error cleanup before merge |
| P3 | One adjacent recovery test still relies on `sqlite3.Connection.__exit__` to close a connection | Fix the final warning-strict test leak before merge |
| Operational check | The pre-merge workflow has not been exercised on a pull request or configured as required branch checks from this local review | Validate in GitHub repository settings |
| Manual blocker | Clean Apple Silicon encrypted creation, populated upgrade, Keychain ACL/recovery, native linkage, and replacement-boundary cancellation remain unchecked | Block public release |
| Manual blocker | The historical device identifier has not been privately classified/remediated | Block public release |

### Disposition of the fourth follow-up findings

| Earlier finding | Latest disposition |
|---|---|
| Safety recovery was cancelled only at entry | **Addressed.** `tests/integration/test_database_conversion.py:531-572` blocks both `PRAGMA foreign_key_check` and `PRAGMA integrity_check` after the `before-rollback-cleanup` fault enters the production-created recovery context. It proves a distinct fresh context, progress interruption, original-context independence, one valid database, and exact restart convergence. Lines 575-629 additionally make recovery verification and its rollback restore fail, then prove the active encrypted copy, plaintext rollback, and marker all survive for a successful restart. |
| New conversion tests leaked raw SQLite connections | **Addressed in those files.** `contextlib.closing` now covers raw connections in `test_database_conversion.py` and `test_installed_launcher.py`, and their focused warning-strict run is clean. The adjacent pre-existing security-recovery test at `test_database.py:200-201` uses the same incorrect context-manager assumption and is the narrower remaining `P3` below. |

### P3 — Close caught HTTP error responses in `UrllibTransport`

**Evidence**

- `UrllibTransport.send_bounded()` catches `HTTPError` at
  `src/tatatuya/infrastructure/tuya/client.py:152`, but neither the immediate
  redirect rejection at lines 153-157 nor the bounded ordinary-error path at
  lines 158-190 closes `exc`. An exception returned by urllib is the response
  object; it owns a file/socket-like body and is outside the successful
  response's `with` block.
- The redirect tests construct five `HTTPError` objects backed by `BytesIO`
  (`tests/unit/test_tuya_client.py:260-286`). The JSON, extreme-decimal, and
  opaque-error tests create three more at lines 633-725.
- Running the Tuya-client unit file and forcing garbage collection produced
  five captured redirect `ResourceWarning`s plus finalization warnings for the
  two 400 responses and the 502 response. This confirms that the adapter, not
  merely a test assertion, retains unclosed error bodies.
- `TuyaClient` defaults to `QtNetworkTransport` in the desktop composition
  (`src/tatatuya/infrastructure/tuya/client.py:486`), so this is not a current
  core desktop workflow failure. `UrllibTransport` remains shipped
  infrastructure with a concrete resource-exhaustion failure under repeated
  errors, which still warrants correction.

**Failure scenario and impact**

A caller that injects `UrllibTransport` and receives repeated redirects, HTTP
errors, or hostile oversized/error bodies can retain response file descriptors
and connections until garbage collection. A long diagnostic or future non-Qt
workflow could eventually exhaust descriptors or connection-pool resources;
warning-strict tests also remain noisy and timing-dependent.

**Required correction and acceptance**

Close `HTTPError` in a `finally` block covering every redirect, deadline,
limit/decoding, parsed-error, and unexpected-exception branch, or wrap the
caught response with `contextlib.closing`. Preserve exception chaining only
after the body is closed. Update the tests to keep each body object and assert it
is closed after both successful diagnostic parsing and every rejected branch.
The Tuya-client file must pass with `ResourceWarning` treated as an error and an
explicit post-test garbage collection.

### P3 — Close the final raw SQLite connection in the recovery tests

**Evidence**

- `tests/integration/test_database.py:200-201` uses
  `with sqlite3.connect(path) as retained`. The SQLite connection context manager
  commits or rolls back; it does not close the connection.
- Running `test_database.py` followed by the conversion matrix and forcing
  garbage collection produced one `ResourceWarning: unclosed database`. The
  warning may be attributed to whichever later parametrized conversion case
  triggers collection, which is why the focused new-file run appeared clean.
- The corresponding raw connections in
  `tests/integration/test_database_conversion.py:378,412,624,822` and
  `tests/integration/test_installed_launcher.py:58,62` now correctly use
  `contextlib.closing`.

**Failure scenario and impact**

This is test-only, but it keeps a recovery fixture's database descriptor alive
past the assertion and makes warning output depend on garbage-collection timing.
That can obscure future real conversion leaks and prevents a trustworthy
warning-strict gate.

**Required correction and acceptance**

Use `contextlib.closing(sqlite3.connect(path))` or explicit `try/finally` at
lines 200-201. Run `test_database.py` plus the conversion matrix in one process,
force garbage collection, and require zero unclosed-database warnings.

### Verification performed for the fifth follow-up

- Focused conversion, installed-launcher, and packaging tests with
  `ResourceWarning` promoted to an error and database coverage enabled:
  **100 passed, 1 native-only test skipped** in 0.66 seconds; `database.py`
  reached 80% diagnostic statement coverage.
- Complete suite in normal mode: **433 passed, 1 native-only test skipped** in
  4.01 seconds.
- Complete suite with `ResourceWarning` promoted to an error: **433 passed, 1
  native-only test skipped**, but pytest reported six unraisable/finalization
  warnings and additional late HTTP-error cleanup warnings. Destructor warnings
  are converted to `PytestUnraisableExceptionWarning`, so the zero exit status
  is not evidence of clean resources.
- `test_database.py` plus the conversion matrix with explicit final garbage
  collection: **108 passed, 1 native-only test skipped**, with one unclosed
  SQLite connection warning.
- Tuya-client unit tests with explicit final garbage collection: **51 passed**,
  with five captured redirect warnings and three late HTTP-error body warnings.
- Qt transport then main-window UI: **78 passed**.
- Main-window UI then Qt transport: **78 passed**.
- Ruff: passed.
- Pyright targeting Python 3.12: zero errors or warnings.
- `pip check`: no broken requirements.
- `git diff --check`: passed.
- A narrow current-tree credential scan found only synthetic test sentinels; no
  real credential was identified. This does not discharge the historical
  identifier decision or replace the configured full-history Gitleaks job.
- Local `pip-audit` remains unavailable (`No module named pip_audit`), so the
  dependency audit workflow was reviewed but not independently executed.
- Native Keychain/SQLCipher execution, the GitHub workflow, required-check
  configuration, and the clean-Mac rehearsal remain unverified from this Linux
  workspace and are explicitly unclaimed.

### Current release decision

Do not publish the draft. Close the two remaining resources and run the new
checks on a real pull request, but keep release readiness tied to the two manual
Phase 14 items: the complete clean Apple Silicon rehearsal and the private
historical-identifier decision. The automated conversion and recovery gate is
now sufficient locally; it does not replace native hardware, Keychain ACL, or
real-account evidence.

This review changed only `security_review.md`; application, test, and workflow
code remain untouched.

## Fourth follow-up review after the preceding security changes (historical)

This section records the state before the fifth follow-up. The latest
dispositions and release decision are in the section above.

The latest changes substantially address both findings from the third
follow-up. A new read-only pull-request/`main` workflow runs the complete
Linux-verifiable gate, both Qt lifecycle orders, and a native Apple Silicon
Keychain/SQLCipher job. A deterministic conversion harness now drives the real
migration state machine through 36 named boundaries with both cancellation and
ordinary exceptions, verifies restart convergence, exercises both filesystem
replacement failures, and retains a separate native populated-upgrade test.

No new `P0` or `P1` defect was found. The pre-merge CI code finding is
**addressed**. The conversion finding is **mostly addressed**, but one `P2`
acceptance gap remains: the fresh five-second safety-recovery path is only
cancelled at its entry, not while a verification is blocked inside it. A `P3`
resource leak in the new migration harness should also be cleaned up so the
fault matrix remains deterministic under repetition and coverage.

Public release remains **not ready**. The clean Apple Silicon rehearsal and
private historical-identifier decision are still explicitly unchecked release
gates, and the safety-recovery proof below should be completed before Phase 14's
fault-injection item is treated as fully verified.

| Priority | Current finding or gate | Release effect |
|---|---|---|
| P2 | The safety-recovery budget is not tested during an in-progress blocked verification | Complete the recovery-specific fault test before relying on the conversion gate |
| P3 | Raw SQLite connections in the new fault harness are transaction-scoped but not closed | Fix before the matrix grows or is made warning-strict |
| Operational check | The new workflow has not been exercised on a pull request or configured as required branch checks from this local review | Validate in GitHub repository settings |
| Manual blocker | Clean Apple Silicon encrypted creation, populated upgrade, Keychain ACL/recovery, native linkage, and replacement-boundary cancellation remain unchecked | Block public release |
| Manual blocker | The historical device identifier has not been privately classified/remediated | Block public release |

### Disposition of the third follow-up findings

| Earlier finding | Latest disposition |
|---|---|
| Missing stage-by-stage SQLCipher conversion coverage | **Mostly addressed.** `tests/integration/test_database_conversion.py:274-311` defines 36 production checkpoints; the cancellation/exception matrix, replacement-failure tests, exact snapshot comparison, restart convergence, progress-handler test, and native populated upgrade all pass in the locally available environment. The remaining safety-recovery-specific gap is below. |
| Correctness checks existed only on release tags | **Addressed in code.** `.github/workflows/tests.yml:3-107` runs on pull requests and `main`, grants only `contents: read`, disables persisted checkout credentials, runs Ruff/Pyright/full pytest/`pip check`, runs both Qt orders, and installs the hash-locked macOS graph before the disposable native tests. Making the checks required and proving their behavior on a real PR remain repository-operations work rather than a workflow-file change. |

### P2 — Interrupt a blocked operation inside safety recovery, not only at recovery entry

**Evidence**

- Production conversion catches any failure with a durable marker and creates a
  fresh five-second `CancellationContext` before calling recovery
  (`src/tatatuya/infrastructure/database.py:422-425`). Recovery may then verify
  the active encrypted database, restore the rollback, and clean artifacts
  (`src/tatatuya/infrastructure/database.py:537-594`).
- `RecoveryCancellingDatabase` cancels only when the `recovery-start` checkpoint
  is entered (`tests/integration/test_database_conversion.py:263-271`). Its one
  recovery test therefore exits before marker parsing, key access, SQL
  verification, replacement, or cleanup (`tests/integration/test_database_conversion.py:444-472`).
- The blocked-verification test does prove that a progress handler interrupts a
  `PRAGMA foreign_key_check`, but it calls `_verify_migrated_database()` directly
  with the ordinary caller-owned context (`tests/integration/test_database_conversion.py:597-624`). It does not enter
  `_migrate_plaintext_database()`'s exception handler, use the fresh safety
  budget, or assert last-copy preservation after an interruption inside
  `_recover_interrupted_migration()`.
- The named fault matrix has temporary and active verification stages, but no
  `recovery-verification:*` stage (`tests/integration/test_database_conversion.py:274-311`). This is narrower than the architecture requirement that cancellation
  while the separate recovery budget is active retain the last valid copy and
  interrupt a blocked verification through its SQLite progress handler
  (`docs/ARCHITECTURE.md:947-950`). Phase 14 nevertheless marks fault injection
  across safety-recovery boundaries complete
  (`docs/IMPLEMENTATION_PLAN.md:652-655`).

**Failure scenario and impact**

The ordinary conversion matrix can remain green if a later edit accidentally
installs the original already-cancelled context during recovery, omits the
recovery verification progress handler, swallows its timeout incorrectly, or
deletes/restores the wrong copy after an in-query interruption. That regression
would occur only after conversion has already failed near a replacement
boundary, precisely when preserving the user's sole history matters most. The
current entry-cancellation test proves a safe early stop, but not the documented
bounded recovery mechanism.

**Required correction**

- Arrange a conversion failure after the encrypted destination has become
  active while a valid plaintext rollback and marker still exist.
- Block `recovery-verification:foreign-key` (and preferably one later integrity
  stage) until the fresh safety context expires or is explicitly cancelled.
  Enter this through `_migrate_plaintext_database()` so the production-created
  recovery context is the one under test.
- Assert that the SQLite progress handler causes the interruption, the complete
  failure/recovery attempt respects the five-second safety bound, and at least
  one exact valid database remains without an invented key or empty replacement.
- Restart with a new context and prove exact schema, row counts, row hashes,
  Client Secret placement, marker cleanup, and convergence. Also inject an
  ordinary failure into recovery's restore/cleanup boundary so a failed recovery
  operation cannot remove the last valid copy.
- Keep `docs/IMPLEMENTATION_PLAN.md:652-655` unchecked or explicitly qualified
  until this test exists. The clean-Mac checklist remains separate native proof.

**Acceptance tests**

- A blocked query reached through the production safety-recovery path is
  interrupted by its progress handler within the fresh budget.
- Immediately afterward, the active path, rollback, and temporary/marker state
  contain at least one reopenable exact copy; no cleanup deletes the last valid
  copy merely to satisfy the timer.
- A subsequent startup converges to one encrypted database with the original
  data and secret-placement invariants.

### P3 — Close every raw SQLite connection in the conversion harness

**Evidence**

- The harness uses `with sqlite3.connect(...)` in the populated-source helper
  and plaintext-validity probe
  (`tests/integration/test_database_conversion.py:322-340,355-360`), and the
  native no-key probe does the same at lines 665-667. Python's SQLite connection
  context manager commits or rolls back a transaction; it does not close the
  connection.
- The installed-launcher test has the same pattern at
  `tests/integration/test_installed_launcher.py:57-64`.
- A focused conversion coverage run passed but emitted **100
  `ResourceWarning: unclosed database` warnings**. The normal suite does not
  collect the objects at the same time, so its clean warning summary does not
  demonstrate that the handles were closed.

**Failure scenario and impact**

The 72-case fault matrix can accumulate file descriptors until garbage
collection happens. That makes repetition, warning-strict CI, and future larger
fixtures flaky, and weakens confidence that artifact cleanup assertions are
observing a fully released database rather than an unlinked-but-open inode.

**Required correction and acceptance**

Wrap each raw `sqlite3.connect()` in `contextlib.closing(...)` or use explicit
`try/finally: connection.close()`. Re-run the conversion matrix with
`ResourceWarning` promoted to an error and with coverage enabled; it must pass
without an unclosed-database warning.

### Verification performed for the fourth follow-up

- Focused conversion and packaging tests: **96 passed, 1 native-only test
  skipped** in 0.40 seconds.
- Complete Linux suite: **430 passed, 1 native-only test skipped** in 3.89
  seconds.
- Qt transport then main-window UI: **78 passed**.
- Main-window UI then Qt transport: **78 passed**.
- Focused conversion coverage: **78 passed, 1 native-only test skipped**;
  `database.py` reached 80% diagnostic statement coverage. This run also exposed
  the 100 unclosed-database warnings described above; coverage is diagnostic,
  not a release percentage target.
- Ruff: passed.
- Pyright targeting Python 3.12: zero errors or warnings.
- `pip check`: no broken requirements.
- `git diff --check`: passed.
- A narrow current-tree credential scan found only synthetic test sentinels; no
  real credential was identified. This does not discharge the historical
  identifier decision or replace the configured full-history Gitleaks job.
- Local `pip-audit` is still unavailable (`No module named pip_audit`), so the
  dependency audit workflow was reviewed but not independently executed.
- Native Keychain/SQLCipher execution, the new GitHub workflow, required-check
  configuration, and the clean-Mac rehearsal cannot be verified from this Linux
  workspace and remain explicitly unclaimed.

### Current release decision

Do not publish the draft. Complete the recovery-in-progress fault test, close
the leaked test connections, exercise the new checks on a real pull request and
make them required once stable, then execute the two manual Phase 14 release
gates. The new conversion matrix and pre-merge workflow are meaningful progress,
but they do not replace clean Apple Silicon recovery and Keychain evidence.

This review changed only `security_review.md`; application and workflow code
remain untouched.

## Third follow-up review after the preceding security changes (historical)

This section records the state before the fourth follow-up. The latest
dispositions and release decision are in the section above.

The new working-tree changes address all five findings from the second
follow-up. Ordinary pytest runs are isolated from the production Keychain
service, Security-framework work runs in a bounded helper process, SQLCipher
conversion now propagates cancellation through snapshots and verification,
the release job installs and tests the package's encrypted launcher, Windows is
explicitly unsupported rather than accidentally included, and one session-owned
`QApplication` makes the transport/UI test order stable.

No new `P0` or `P1` code defect was found in this pass. Public release is still
**not ready**, because the clean Apple Silicon rehearsal and private historical
identifier decision remain explicitly unchecked release gates. Two `P2` test/
verification defects should also be corrected before relying on Phase 14 as a
repeatable security gate.

| Priority | Current finding or gate | Release effect |
|---|---|---|
| P2 | Cancellation-safe plaintext-to-SQLCipher conversion is marked complete without stage-by-stage regression coverage | Add the missing destructive-boundary tests before release |
| P2 | Full correctness checks run only for a release tag, not for pull requests or pushes to `main` | Add a read-only pre-merge CI gate |
| Manual blocker | Clean Apple Silicon encrypted creation, legacy upgrade, Keychain ACL/recovery, packaged launch, and real Tuya rehearsal remain unchecked | Block public release |
| Manual blocker | The historical device identifier has not been privately classified/remediated | Block public release |

### Disposition of the second follow-up findings

| Earlier finding | Latest disposition |
|---|---|
| Tests used the production macOS Keychain namespace | **Addressed.** `tests/conftest.py:27-53` replaces the default database store with `MemorySecretStore` and rejects construction of `ro.tatatuya.app` during pytest. The installed launcher uses a UUID-suffixed disposable service and cleans both exact accounts. |
| Security-framework and conversion work could not enforce cancellation | **Addressed in code.** `MacOSKeychainSecretStore` runs each operation in a spawn helper with cancellation, a five-second deadline, terminate/kill, and join. Database snapshots, integrity checks, key probes, and recovery receive cancellation contexts and progress handlers. The remaining issue is proof coverage, not the previously missing mechanism. |
| Fresh macOS release tests did not install or exercise TataTuya correctly | **Addressed.** Both macOS jobs install the checked-out package with dependency resolution/build isolation disabled. The launcher test opens the database with SQLCipher, proves stdlib SQLite cannot enumerate it, and uses a disposable Keychain service. The packaged smoke runs twice and checks encrypted restart, linkage, and cleanup. |
| “Non-macOS” incorrectly implied Windows support | **Addressed.** Product, architecture, README, entry point, database, and logging behavior now say Linux/other POSIX development; Windows receives an explicit unsupported-platform result. |
| Qt transport and widget tests depended on collection order | **Addressed.** `tests/conftest.py:13-24` creates the one widget-capable application before tests. Both transport-first and UI-first runs pass locally and are represented as separate macOS matrix entries. |

### P2 — Prove cancellation and recovery at every SQLCipher conversion boundary

**Evidence**

- `src/tatatuya/infrastructure/database.py:292-397` contains the destructive
  legacy conversion workflow: Keychain migration, source snapshot, attach/
  export, destination snapshot, two verifications, two `os.replace` calls, and
  rollback recovery.
- `src/tatatuya/infrastructure/database.py:600-649` separately implements
  foreign-key, database integrity, cipher integrity, wrong-key, and empty-key
  probes under cancellation.
- The new focused tests exercise a pre-cancelled startup, cancellation inside
  `_database_snapshot()`, and a pre-cancelled early recovery
  (`tests/integration/test_database.py:101-203`). They do not drive cancellation
  or failure through the actual conversion, either replacement boundary, any
  integrity/key probe, or the fresh five-second recovery context.
- A focused coverage run over `test_secrets.py` and `test_database.py` reported
  only 55% statement coverage for `database.py`; the conversion body at lines
  295-397 and migrated-database verification at lines 606-649 were almost
  entirely unexecuted on Linux.
- `tests/integration/test_installed_launcher.py` and `scripts/build_macos.sh`
  validate fresh encrypted databases. Neither constructs and upgrades a
  populated legacy plaintext database or injects cancellation at conversion
  stages.
- `docs/IMPLEMENTATION_PLAN.md:626-630` marks conversion cancellation and the
  separate recovery budget complete, while the documented clean-Mac rehearsal
  does not enumerate cancellation at these stages. The checked status therefore
  lacks its own regression evidence.

**Failure scenario and impact**

A future edit can omit one progress handler, mishandle the exception between
the two replacements, delete the only valid rollback, or overrun the shutdown
budget without failing any current automated check. This path upgrades the
user's only local history and moves its Client Secret and database key across
two storage systems. A hidden regression can therefore cause data loss,
unrecoverable encrypted data, or an application that cannot finish shutdown.
The manual “upgrade once on a clean Mac” gate is necessary native evidence, but
it is not a repeatable fault-injection suite.

**Required correction**

- Add a deterministic SQLCipher-capable conversion harness. On Linux this can
  be a narrowly instrumented driver/connection fake that preserves the real
  state machine; retain a native macOS group for actual SQLCipher semantics.
- Inject cancellation/failure during legacy-secret read/write/verification,
  database-key get/create/verification, source snapshot, export, destination
  snapshot, foreign-key check, integrity check, cipher-integrity check,
  wrong-key probe, empty-key probe, before/after each replacement, rollback
  cleanup, and marker cleanup.
- After every injected point, assert that exactly one valid source or encrypted
  database remains recoverable, the marker names only owned in-directory
  artifacts, the Client Secret is absent from the accepted encrypted logical
  database, and restart converges without inventing a key or empty database.
- Test cancellation after the first failure while the five-second recovery
  context itself is active. Bound the complete worker drain, not only an
  internal checkpoint count.
- Keep the clean-Mac populated-upgrade rehearsal as native confirmation; do not
  replace it with fakes or mark its checklist item complete automatically.

**Acceptance tests**

- A table-driven test reaches every stage above and proves both cancellation
  and an ordinary injected exception preserve a reopenable database with exact
  schema, row counts, row hashes, and secret-placement invariants.
- A cancellation immediately before and after each `os.replace` finishes within
  the documented ordinary shutdown bound or the explicit safety-recovery budget,
  then restarts successfully.
- A deliberately blocked verification is interrupted by the SQLite progress
  handler; a deliberately blocked recovery never deletes the last valid copy to
  meet the timer.
- The native Apple Silicon test upgrades a populated synthetic plaintext
  database, verifies correct/wrong/empty key behavior, and confirms both
  migration and cleanup artifacts use the required modes and disappear only
  after successful verification.

### P2 — Run correctness and security-regression checks before merge

**Evidence**

- `.github/workflows/release-macos.yml:3-6` is triggered only by version tags.
  It is the only workflow that runs Ruff, Pyright, pytest, the installed
  launcher, or the two Qt orderings.
- `.github/workflows/security.yml:3-8,13-43` runs on pull requests and pushes to
  `main`, but its jobs only scan history and audit the dependency lock.
- No `.github/workflows/tests.yml` or equivalent pre-merge correctness workflow
  exists, despite `docs/ARCHITECTURE.md` defining the unit, integration, UI,
  cancellation, migration, and packaging gates.

**Failure scenario and impact**

A change can merge while breaking Keychain isolation, SQLCipher fail-closed
behavior, redirect rejection, diagnostic redaction, shutdown, or ordinary
product invariants. The first automated discovery would be after someone pushes
a release tag. The tag job prevents a bad artifact from being drafted, but it
does not protect `main` or give the next implementation agent timely evidence
that a security boundary regressed.

**Required correction**

- Add a read-only pull-request/`main` workflow that installs reviewed
  dependencies and runs Ruff, Pyright, and the complete Linux-verifiable suite.
- Add the two Qt orderings or a randomized-order equivalent to that workflow,
  not only the release-tag workflow.
- Run the native disposable-Keychain/SQLCipher launcher test on macOS before
  merge when security, database, packaging, dependency, or workflow paths
  change. A path-filtered job is acceptable if its filter includes every file
  capable of altering production composition.
- Keep `persist-credentials: false`, top-level `contents: read`, unique
  disposable Keychain service names, cleanup-on-failure, hash-locked macOS
  dependencies, and the pytest production-service guard.
- Make the pre-merge jobs required branch checks once their signal is stable.

**Acceptance tests**

- Open a test pull request that intentionally fails Ruff, Pyright, one ordinary
  test, one Qt ordering, and the production-Keychain guard in turn; each defect
  must block the appropriate required check before merge.
- Verify the workflow token cannot write repository contents and checkout does
  not persist credentials.
- On macOS, preseed the production TataTuya Keychain accounts, run the native
  job including an intentional failure, and prove the sentinels are unchanged
  and the disposable accounts are removed.

### Verification performed for the third follow-up

- Complete suite in normal order: **351 passed in 3.58 seconds**.
- Qt transport then main-window UI: **78 passed**.
- Main-window UI then Qt transport: **78 passed**.
- Focused secret-store/database tests with coverage: **42 passed**; diagnostic
  coverage was 71% for `secrets.py` and 55% for `database.py`. Coverage was used
  only to identify unexercised security stages, not as a release percentage
  target.
- Ruff: passed.
- Pyright with Python 3.14: zero errors or warnings.
- `pip check`: no broken requirements.
- A manual blocked-helper timeout probe returned `timeout` in **5.024 seconds**
  with no surviving multiprocessing child. Existing tests also cover
  cancellation of get/set/create-if-absent/delete helpers before their synthetic
  late write.
- `git diff --check`: passed.
- The current [official GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
  maps `macos-15` to an Apple Silicon ARM64 runner, so that label is not a
  finding in this pass.
- Local `pip-audit` remained unavailable (`No module named pip_audit`), so the
  dependency audit configuration was reviewed but the advisory scan was not
  independently rerun here.
- No real credential was identified in the current-tree narrow scan. This does
  not discharge the private historical device-identifier decision or replace
  the configured full-history Gitleaks job.

### Current release decision

Do not publish the draft yet. The latest code changes resolve the previous five
findings, but the next agent should add the conversion fault-injection suite and
pre-merge CI, then execute—not merely document—the remaining clean Apple
Silicon and historical-identifier gates. Only those manual owners can mark the
corresponding checklist items complete.

This review changed only `security_review.md`; it did not modify application or
workflow code.

## Second follow-up review after the preceding working-tree changes (historical)

This section records the state before the third follow-up. The latest
dispositions and release decision are in the section above.

The latest changes implement the accepted Linux/POSIX plaintext-development
policy and resolve most findings from the first follow-up. In particular, the
working tree now has an interprocess database lock, atomic database-key
create-if-absent behavior, local workflows that do not depend on the optional
Tuya Client Secret, a safer migration temporary-file lifecycle, bounded Qt
error-body reads, and inert escaped tooltips.

The public-release verdict remains **not ready**. Two `P1` findings can damage a
developer's real TataTuya Keychain state or leave application shutdown without
the documented bound. Three `P2` findings make the advertised release or
cross-platform verification unreliable. The clean-Mac Phase 14 rehearsal and
the historical secret/identifier review also remain manual release gates.

| Priority | Current finding | Release effect |
|---|---|---|
| P1 | Automated tests use the production macOS Keychain namespace | Block release and protect developer credentials before another Mac test run |
| P1 | Keychain calls and parts of SQLCipher conversion cannot enforce cancellation/shutdown bounds | Block release |
| P2 | The fresh macOS release test job neither installs TataTuya nor tests the encrypted platform contract correctly | Fix before release |
| P2 | The advertised “non-macOS” development mode is implemented only for POSIX-like systems | Resolve support scope before claiming cross-platform behavior |
| P2 | Qt transport and UI tests have an order-dependent application-singleton failure | Fix before relying on the suite as a release gate |

### Disposition of the first follow-up findings

| Earlier finding | Latest disposition |
|---|---|
| Database/key preparation was process-local only | **Addressed in code.** `Database` now holds an owned `0600` `flock` through classification/preparation, and the database-key store has create-if-absent semantics. A four-process Linux test passes. The real macOS Keychain/SQLCipher two-process race and migration race still belong in the clean-Mac rehearsal. |
| Client Secret failures aborted bootstrap and billing | **Addressed.** Cached bootstrap and calculation preparation no longer load the optional Client Secret. Focused UI tests cover denied/corrupt secret behavior for local workflows. |
| Keychain and SQLCipher preparation ignored shutdown bounds | **Partially addressed; remains `P1`.** Lock waits and ordinary initialized connections are cancellation-aware, but direct Security-framework calls and several conversion verification scans remain unbounded. |
| Migration discarded the secured temporary inode before export | **Addressed in code.** The `mkstemp` inode is retained, the durable marker is written before export, and recovery handles temporary sidecars. Real SQLCipher crash-stage rehearsal remains a manual gate. |
| Qt transport selected the error-body limit after draining the response | **Addressed.** Status is available before body collection and targeted tests cover success/error limits. |
| Remote text was active rich text in table tooltips | **Addressed.** Tooltip content is escaped, wrapped as controlled rich text, and includes a bidirectional-control boundary; the targeted rendering test passes. |
| Plaintext non-macOS development mode was requested but absent | **Implemented for Linux/POSIX.** Ordinary SQLite and a persistent plainly named Client Secret artifact survive restart. The broader “non-macOS” wording is still inaccurate for Windows; see the `P2` finding below. |

### P1 — Isolate tests from the production macOS Keychain namespace

**Evidence**

- `src/tatatuya/infrastructure/database.py:59-68` defaults a Darwin `Database`
  to `MacOSKeychainSecretStore()` without a test-specific service name.
- `src/tatatuya/infrastructure/secrets.py:13,174-179` gives that store the
  production service `ro.tatatuya.app`; `set()` at lines 221-239 updates an
  existing account rather than limiting itself to disposable data.
- `tests/integration/test_database.py:51-53,177-195` constructs the default
  database and saves the fixture Client Secret `secret`.
- `tests/integration/test_release_readiness_workflow.py:49-78` also constructs
  the default database and saves `fixture-client-secret`.
- The earlier release requirements in this document already say tests must not
  pollute the developer's real Keychain. The current tests violate that
  boundary when executed on macOS.

**Failure scenario and impact**

Running the normal integration suite on a developer Mac targets the same
`ro.tatatuya.app` / `tuya-client-secret-v1` item as the installed application.
A fixture can replace a real Client Secret, create a production-named item with
test-run access-control metadata, or leave state that changes later application
behavior. The database-key account is also in the same namespace for every
default macOS test database, so unrelated temporary test databases can compete
for one production key. This is test-induced credential corruption and can
make encrypted test data or local application data unrecoverable.

**Required correction**

- Inject `MemorySecretStore` into all ordinary unit, integration, and UI tests
  that do not explicitly verify native Keychain behavior. Make this the default
  test fixture rather than relying on every caller to remember it.
- Give native Keychain tests a unique disposable service such as
  `ro.tatatuya.app.test.<run-uuid>`. Delete only that exact service's accounts in
  teardown, including when the test fails.
- Add a pytest guard that fails immediately if a test process attempts to use
  `KEYCHAIN_SERVICE == "ro.tatatuya.app"`. A generic environment-variable
  override is insufficient unless production startup rejects it.
- Keep native SQLCipher/Keychain coverage, but place it in an explicitly marked
  macOS test group with disposable data directories and service names. Never
  weaken the production service name or production fail-closed path to make
  tests pass.

**Acceptance tests**

- On a Mac, create sentinel values in both production TataTuya Keychain
  accounts, run the complete automated suite, and prove the values and their
  access-control metadata are unchanged.
- Run the native test group twice, including an intentionally failing test, and
  prove no `ro.tatatuya.app.test.*` items remain afterward.
- Verify a normal pytest process fails before the first Security-framework write
  if it tries to use `ro.tatatuya.app`.
- Verify the installed app still uses exactly `ro.tatatuya.app`; test isolation
  must not alter the production lookup contract.

### P1 — Make every startup path obey the cancellation and shutdown contract

**Evidence**

- `src/tatatuya/infrastructure/secrets.py:207-261` invokes
  `SecItemCopyMatching`, `SecItemAdd`, `SecItemUpdate`, and `SecItemDelete`
  synchronously. `kSecUseAuthenticationUIFail` prevents an authentication
  prompt but supplies neither a deadline nor a cancellation mechanism.
- `src/tatatuya/infrastructure/database.py:307-309,337-354` installs a SQLite
  progress handler only around `sqlcipher_export`. The source snapshot,
  encrypted snapshot, pre-replacement verification, and post-replacement
  verification run without one.
- `_database_snapshot()` at lines 367-413 can scan and hash every row in every
  table without a cancellation checkpoint.
- `_verify_migrated_database()` at lines 558-596 runs foreign-key, integrity,
  cipher-integrity, wrong-key, and empty-key probes without the caller's
  cancellation context.
- The exception path at lines 360-362 explicitly calls interrupted-migration
  recovery with `cancellation=None`.
- `src/tatatuya/ui/app.py:89-122` cancels workers and waits for their
  `all_finished` signal. There is no enforceable watchdog if a worker remains
  inside one of the calls above.

**Failure scenario and impact**

A Security-framework call can stall, or a large legacy database can spend much
longer than the remaining shutdown budget in row hashing or integrity checks.
The UI displays the shutdown state and issues cancellation, but it cannot quit
until the worker returns. The documented six/eleven-second bounds therefore do
not describe an enforceable property. Force-quitting during an unbounded
conversion also exercises the most sensitive recovery window.

**Required correction**

- Propagate one `CancellationContext` through classification, legacy-secret
  reads, both snapshots, all verification probes, and interrupted-migration
  recovery. Install/remove progress handlers for every potentially large SQL
  operation and checkpoint inside Python row loops.
- Give recovery a separate short, explicitly documented safety budget when it
  must restore a consistent file after cancellation. Do not recursively discard
  cancellation with `None`, and do not replace or delete the last valid copy
  merely to meet a timer.
- Put Security-framework operations behind an isolation boundary whose timeout
  is enforceable by the owner of the work. A `QThread` timeout that leaves the
  same blocked native call running is not sufficient; the design must also
  define safe cleanup and prevent late results from mutating state.
- If macOS Security APIs cannot be safely bounded in-process, use a narrowly
  scoped helper process and a disposable request/result protocol, or revise the
  product's shutdown guarantee explicitly. Do not claim the current bound on
  the basis of `kSecUseAuthenticationUIFail` alone.
- Preserve migration atomicity and fail-closed behavior. Cancellation must
  never turn an unreadable/partially verified file into the active database.

**Acceptance tests**

- Use fakes that block each Security operation (`get`, add, update, delete),
  request application quit, and prove the worker registry reaches finished and
  the application exits inside the documented bound without a late write.
- Build a large synthetic plaintext database and cancel separately during the
  source snapshot, export, destination snapshot, foreign-key check, integrity
  check, cipher-integrity check, wrong-key probe, empty-key probe, and recovery.
  Each case must finish within its budget and preserve one reopenable source or
  encrypted database with matching rows.
- Repeat interruption immediately before and after each `os.replace`; restart
  must recover deterministically without orphaning the only valid database or
  key.
- Run the native Keychain cases only in the disposable namespace required by the
  preceding finding.

### P2 — Make the fresh macOS release test install and exercise the real package

**Evidence**

- `.github/workflows/release-macos.yml:35-39` creates `.venv` and installs only
  `requirements-macos.lock`. It never installs the local TataTuya project.
- `tests/integration/test_installed_launcher.py:17-18` requires a sibling
  `.venv/bin/tatatuya` console script. That script cannot exist on a fresh
  runner when only third-party locked dependencies were installed.
- The same test opens the produced database with stdlib `sqlite3` at lines
  29-34. On macOS the default `Database` contract requires SQLCipher, so a
  successful encrypted smoke database must not be readable this way.
- The test does not set `TATATUYA_SMOKE_KEYCHAIN_SERVICE`. If the launcher did
  exist, it would target the production Keychain namespace instead of the
  disposable smoke namespace already used by `scripts/build_macos.sh`.

**Failure scenario and impact**

The tag workflow fails before it provides useful installed-launcher assurance.
If an ambient editable install hides that problem, the assertion then expects a
plaintext database on the platform where plaintext is forbidden and may write
to the real Keychain service. The release gate therefore cannot demonstrate the
fresh-install, encrypted-startup contract it claims to cover.

**Required correction**

- After the hash-locked dependency install, install the checked-out project
  with dependency resolution disabled (for example, `pip install --no-deps .`)
  so the lock remains authoritative and the console script is created.
- Split the launcher assertion by contract: Linux/POSIX development may inspect
  the SQLite schema with stdlib `sqlite3`; macOS must validate schema and startup
  through the application/SQLCipher driver and must prove stdlib `sqlite3`
  cannot read the encrypted schema.
- Assign every CI smoke invocation a unique
  `TATATUYA_SMOKE_KEYCHAIN_SERVICE`, clean its exact accounts on success and
  failure, and assert the production service was untouched.
- Make the test run from outside the checkout with `PYTHONPATH` removed, as it
  already attempts to do. Do not make it pass by adding the checkout to imports.

**Acceptance tests**

- Reproduce the workflow from a clean macOS runner or VM with no prior TataTuya
  install or data. Confirm `.venv/bin/tatatuya` exists only after the local
  package installation and `--smoke-test` exits successfully outside the repo.
- Confirm the smoke database has all expected migrations when opened through
  SQLCipher with its disposable Keychain key, and that stdlib `sqlite3` cannot
  enumerate its schema.
- Preseed production Keychain sentinels, run the workflow including a forced
  failure, and prove the sentinels are unchanged and disposable accounts are
  removed.

### P2 — Either implement Windows adapters or narrow “non-macOS” to Linux/POSIX

**Evidence**

- `docs/PRODUCT_SPEC.md:475-479`, `docs/ARCHITECTURE.md:158-163`, and
  `README.md:43-47` describe the plaintext development policy as “non-macOS,”
  which ordinarily includes Windows.
- `src/tatatuya/infrastructure/database.py:6,598-640` unconditionally imports
  and uses `fcntl.flock`.
- `src/tatatuya/infrastructure/secrets.py:141-170` relies on `os.getuid`, POSIX
  ownership/mode checks, directory file descriptors, and `fchmod`.
- `src/tatatuya/infrastructure/logging_setup.py:16-40` also relies on
  `os.getuid`, ownership, and POSIX modes.

**Failure scenario and impact**

Windows does not provide `fcntl` and lacks several of these POSIX APIs. The
application fails at import or startup before it can offer the advertised
plaintext development mode. This does not weaken the Apple Silicon production
build, but it makes the documented platform policy and any downstream testing
assumptions false.

**Required correction**

- Make an explicit product decision: support only Linux/POSIX development and
  update every “non-macOS” statement accordingly, or add platform adapters for
  Windows locking, ownership/ACL validation, atomic secret replacement, and log
  protection.
- Do not silently skip locking or file-safety checks on Windows. If Windows is
  unsupported, fail with a clear platform diagnostic before importing a module
  that cannot load.
- Keep macOS selection fail-closed and independent of this decision.

**Acceptance tests**

- On every advertised development platform, import TataTuya, launch the
  offscreen smoke path, save settings, restart, and verify that the ordinary
  SQLite database and plaintext secret persist.
- Exercise concurrent initialization, symlink/reparse-point rejection, atomic
  secret replacement, and same-user-only access using that platform's native
  primitives.
- If the decision is Linux/POSIX-only, add a documentation test for the exact
  wording and a Windows test that reports the deliberate unsupported-platform
  error rather than an `ImportError` traceback.

### P2 — Remove the Qt application-singleton test-order dependency

**Evidence**

- `src/tatatuya/infrastructure/tuya/client.py:48,740-747` retains a global
  `QCoreApplication` created by Qt transport tests.
- `tests/ui/test_main_window.py:48-51` tries to create a `QApplication` when the
  existing singleton is only a `QCoreApplication`. Qt permits only one core
  application singleton per process.
- The normal suite order passes, but running
  `.venv/bin/python -m pytest tests/unit/test_tuya_client.py tests/ui/test_main_window.py -q`
  produced 87 passes followed by 24 UI failures with
  `RuntimeError: libshiboken: Please destroy the QCoreApplication singleton before creating a new QApplication instance`.
  Each file passes separately.

**Failure scenario and impact**

Test success depends on collection order rather than isolated behavior. A CI
shard, randomized order, or a future filename change can make all UI checks
fail—or avoid the order that exposes the broken lifecycle. This reduces trust
in the suite that protects credential dialogs, error rendering, cancellation,
and shutdown behavior.

**Required correction**

- Establish one session-scoped `QApplication` for tests that need Qt, and let
  transport tests reuse it as a `QCoreApplication`, or isolate the transport
  lifecycle in subprocesses. Do not retain a module-owned application whose
  type depends on which test ran first.
- Centralize creation/teardown in `tests/conftest.py`; individual test modules
  should not compete to create incompatible singletons.
- Preserve headless/offscreen operation and ensure worker/network objects are
  torn down between tests.

**Acceptance tests**

- Run the Tuya-client and main-window files in both orders in the same process;
  both runs must pass.
- Run the full suite with at least one randomized order and with the UI and
  transport groups in separate CI shards.
- Assert there is exactly one live `QCoreApplication`/`QApplication` singleton
  and that it is suitable for widget construction before any UI test.

### Verification performed for this follow-up

- Full suite in the repository's normal order: **335 passed**.
- Focused database and secret-store suite: **33 passed**.
- Tuya-client unit file alone: **51 passed**.
- Main-window UI file alone: **27 passed**.
- Tuya-client followed by main-window in one process: **87 passed, 24 failed**
  due to the Qt singleton defect described above.
- `ruff check .`: passed.
- `pyright --pythonversion 3.14`: passed with zero errors.
- `git diff --check`: passed.
- A Linux restart probe confirmed an ordinary `SQLite format 3` database, a
  persistent recoverable plaintext Client Secret, and `0600` artifact modes.
- A narrow current-tree pattern scan found only variable names and fixture
  files, not an obvious real credential. This was not a substitute for the
  required complete-history/manual identifier review.
- `pip-audit` and local `gitleaks` executables were unavailable in this
  environment. The workflow configuration was reviewed, but those scans were
  not independently rerun here.

### Current release decision and handoff order

Do not publish a public artifact yet. The next implementation agent should work
in this order:

1. Isolate every automated test from the production Keychain service before
   running another suite on a developer Mac.
2. Close the cancellation gaps and demonstrate the shutdown bound across every
   Security-framework and conversion stage.
3. Repair the fresh macOS workflow/launcher contract, then fix Qt test lifecycle
   isolation.
4. Decide whether development support means Linux/POSIX or truly all non-macOS
   platforms and align code, tests, and documentation.
5. On a clean Apple Silicon Mac, complete the unchecked Phase 14 fresh-create,
   populated-upgrade, cancellation, Keychain-denial, packaging, and secret
   history/identifier rehearsal gates with disposable credentials and Keychain
   namespaces.

The review itself changed only this document. It did not modify application
code or accept any manual release gate on the implementer's behalf.

## First follow-up review after the initial remediation working tree (historical)

This section records the state before the latest changes. Its findings and
recommended corrections remain useful as implementation history, but their
current disposition is authoritative only in the second follow-up above.

The remediation materially fixes the original redirect, plaintext-storage,
Tuya cancellation, diagnostic-redaction, QLabel, and release-privilege findings.
The production design now uses an abortable Qt transport, macOS Keychain,
SQLCipher, restrictive filesystem modes, hash-locked dependencies, SHA-pinned
actions, and a separate write-capable draft-publication job.

The public-release verdict nevertheless remains **not ready**. The follow-up
found three `P1` defects and three `P2` defects, and the two manual Phase 14
release gates remain unchecked.

| Priority | Follow-up finding | Release effect |
|---|---|---|
| P1 | Database/key preparation is not serialized across application processes | Block release |
| P1 | Client Secret failures abort local bootstrap and billing | Block release |
| P1 | Keychain and SQLCipher preparation do not obey cancellation/shutdown bounds | Block release |
| P2 | Legacy migration drops the secured temporary inode before SQLCipher writes it | Fix before release |
| P2 | The Qt transport chooses the error-body limit only after draining the response | Fix before release |
| P2 | Remote device/error text remains active rich text in table tooltips | Fix before release |

### Accepted product decision — plaintext non-macOS development mode

**Status:** implemented for Linux/POSIX by subsequent changes. The remaining
Windows/support-scope mismatch is tracked in the current `P2` findings above.

TataTuya's distributable production target remains Apple Silicon macOS. On
macOS, the application must continue to require SQLCipher and macOS Keychain,
fail closed if either backend is unavailable, and never automatically fall back
to plaintext storage.

For Linux and other non-macOS development runs, the owner has explicitly
accepted a deliberately insecure storage mode so the complete application can
be tested without Apple frameworks. That mode should use ordinary SQLite for
the whole database and persist the Tuya Client Secret without encryption. This
is an accepted development convenience, not a security control or a supported
way to protect production credentials. Any process running as the same OS user
must be assumed able to read both the database and the stored Client Secret.

**Gap at the time of this decision (historical)**

- `src/tatatuya/infrastructure/dbapi.py` permits ordinary SQLite on non-macOS
  only when `TATATUYA_ALLOW_PLAINTEXT_TEST_DATABASE=1` is set.
- `src/tatatuya/infrastructure/database.py` pairs that mode with
  `MemorySecretStore`, so credentials entered through Settings disappear when
  the process exits. This prevents a realistic restart test.
- `src/tatatuya/ui/text.py` says the stored secret is in Keychain even when a
  non-Keychain backend is active.

**Recommended implementation for the next agent**

- Select the storage policy centrally by platform: default macOS to
  Keychain/SQLCipher and default non-macOS to persistent plaintext storage. Do
  not require a hidden environment variable for the non-macOS development
  path.
- Keep the `SecretStore` boundary. Add a narrowly scoped plaintext Client
  Secret adapter rather than scattering platform checks through repositories
  or services. A raw file in the application-data directory is acceptable and
  makes the risk obvious; an ordinary SQLite value is also acceptable if its
  migration and transaction behavior are documented.
- Even though the content is unencrypted, retain best-effort `0700` directory
  and `0600` file permissions on POSIX, reject symlinks/non-regular files, and
  use an atomic same-directory replacement for secret updates. These measures
  reduce accidental disclosure and corruption but do **not** prevent another
  same-user application from reading the data.
- Name and document the plaintext artifact unambiguously. Never describe the
  non-macOS backend as secure, encrypted, protected by Keychain, or suitable for
  real production credentials.
- Make the Settings placeholder backend-neutral or platform-aware so Linux
  does not falsely claim Keychain storage. Keep all user-facing text Romanian.
- Keep packaged macOS release checks independent of the non-macOS adapter.
  Tests must prove that changing an environment variable cannot silently select
  plaintext SQLite or plaintext credentials on macOS.
- Update `README.md`, `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, and
  `docs/IMPLEMENTATION_PLAN.md` together. Warn developers to use disposable or
  test Tuya credentials on non-macOS systems.

**Acceptance tests**

- On Linux, start with an empty temporary application-data directory, enter and
  save credentials through the normal Settings workflow, quit, construct a new
  `Database`/settings store, and verify that the credentials and local history
  survive restart.
- Confirm the non-macOS database has the standard `SQLite format 3` header and
  that the persisted Client Secret is recoverable directly from its plaintext
  artifact. This test deliberately documents the absence of encryption.
- Confirm the secret never appears in logs, diagnostics, exception messages,
  source files, or release artifacts despite being plaintext at rest.
- On POSIX, verify `0700` on the data directory and `0600` on the database,
  sidecars, log, and plaintext secret artifact; verify symlink targets are not
  followed or modified.
- On macOS, verify a missing Keychain or SQLCipher dependency fails closed and
  creates neither a plaintext database nor a plaintext secret artifact.

### P1 — Serialize database-key preparation across application processes

**Evidence**

- `src/tatatuya/infrastructure/database.py:33-44` provides only a process-local
  `threading.RLock`.
- `src/tatatuya/infrastructure/database.py:135-159` performs a separate
  Keychain read, possible key generation/write, database creation, and only then
  normal schema access.
- `src/tatatuya/infrastructure/secrets.py:88-106` implements `set()` as
  read-then-add/update. It is intentionally suitable for replacing the Tuya
  Client Secret but is not an atomic create-if-absent primitive for the database
  key.
- No `flock`, `QLockFile`, or equivalent interprocess lock exists in the source.

**Failure scenario and impact**

Two TataTuya processes can both observe a missing database/key. Process A adds
key A and starts creating the database. Process B, whose earlier outer read saw
no key, can enter `set()` after A's add, see key A, and update the Keychain item
to key B. The database can then be encrypted with key A while Keychain retains
only key B. Both processes subsequently fail to open the database, and the only
working key is lost. Simultaneous plaintext conversion has the same class of
race and also violates the exclusive-migration requirement. This is a data-loss
and core-startup failure, not merely a duplicate-instance inconvenience.

**Required correction**

- Acquire an owned `0600` interprocess lock in the `0700` data directory before
  classifying the database or reading either database-key state. Hold it through
  recovery, conversion/fresh creation, initial schema migration, and integrity
  verification.
- Give the database key a create-if-absent operation. On duplicate-add, read and
  validate the winning value; never update an existing database key merely
  because this process generated a different candidate.
- A second app instance should wait within a documented bound or exit with a
  Romanian recovery message. It must not repair, replace, or re-key the file.

**Acceptance tests**

- Start two real OS processes at synchronized points before Keychain add and
  before file creation; both must converge on one key and one reopenable database.
- Repeat with a populated plaintext source and injected pauses at export and
  replacement. Exactly one conversion may run, with no orphaned key or source.

### P1 — Keep optional Client Secret failures out of local bootstrap and billing

**Evidence**

- `src/tatatuya/ui/app.py:136-143` loads the Tuya Client Secret before returning
  cached devices/readings from bootstrap.
- `src/tatatuya/ui/app.py:200-215` loads the Client Secret during calculation
  preparation only to decide whether cloud import is available.
- `src/tatatuya/infrastructure/repositories/settings.py:77-84` turns denied or
  corrupt/non-UTF-8 Client Secret data into a `UserFacingError`.
- `docs/ARCHITECTURE.md:365-370` requires calculation preparation to load
  currency without Client ID, Client Secret, or region;
  `docs/PRODUCT_SPEC.md:274-279` says missing, invalid, expired, or removed Tuya
  credentials disable only cloud import.

**Failure scenario and impact**

If the database-key item has already opened the valid SQLCipher database but the
separate Client Secret item is denied, corrupted, or otherwise unreadable,
bootstrap fails before cached rows are returned. The same condition prevents
opening Calculate even though its readings, currency, and billing rules are all
local. The follow-up reproduced this with a synthetic non-UTF-8 secret: the
failure is raised from `load_tuya()` before the local initial state is built.

**Required correction**

- Split non-secret application settings and optional remote capability into
  separate reads. Bootstrap and calculation preparation must load local models
  and currency without querying the Client Secret item.
- Treat a missing/invalid/denied Client Secret as remote capability unavailable
  for local screens. Preserve the actionable Keychain error when the user opens
  Settings or explicitly invokes a remote workflow.
- Continue failing closed when the *database-key* item is unavailable; the two
  Keychain accounts have different product consequences and must not share one
  blanket failure path.

**Acceptance tests**

- Use an account-aware fake that permits `database-key-v1` but denies or returns
  corrupt bytes for `tuya-client-secret-v1`. Bootstrap must show cached rows and
  Calculate/History must remain usable; remote controls must be disabled.
- Verify Settings and explicit remote actions still show the Romanian recovery
  error and never treat denial as an empty replacement secret.

### P1 — Bound and cancel Keychain/database preparation workflows

**Evidence**

- `src/tatatuya/infrastructure/secrets.py:24-27,74-106` has no cancellation or
  deadline contract around blocking `SecItem` calls.
- `src/tatatuya/infrastructure/database.py:94-101,229-308` does not receive a
  `CancellationContext`; SQLCipher export and recovery cannot observe shutdown.
- `src/tatatuya/ui/dialogs/settings.py:376-386` checks cancellation only before
  entering `SettingsService.save()`.
- `src/tatatuya/infrastructure/repositories/settings.py:47-66` can complete the
  Keychain round trip and then start database writes without another checkpoint.
- `docs/ARCHITECTURE.md:559-574,627-669` requires checkpoints before new
  transactions and a six/eleven-second shutdown-drain bound.

**Failure scenario and impact**

A Keychain authorization prompt or long SQLCipher conversion can outlive the
workflow deadline. Closing the dialog or quitting sets a flag, but the
`WorkerOwner` waits indefinitely for the native call/export to return, leaving
the app stuck in its closing state. If a Settings Keychain call eventually
returns after cancellation, the implementation can still start and commit the
non-secret database writes even though no post-Keychain checkpoint ran.

**Required correction**

- Thread `CancellationContext` through database preparation and the composite
  settings adapter. Check it immediately before every new read-only stage and
  database transaction.
- Define the cross-store Settings boundary precisely. Either start one explicit,
  bounded non-cancelable boundary before the Keychain update and document its
  drain time, or stop after the Keychain round trip when cancellation won and
  use the already-documented retry/reconciliation behavior.
- Automatic background Keychain reads should not leave an unbounded system
  prompt. Use the appropriate noninteractive Security-framework option where it
  preserves the intended UX, or isolate prompt-capable/native work behind a
  process/operation that the shutdown coordinator can bound safely.
- Make SQLCipher export interruptible at safe points (for example through the
  driver's interrupt/progress facilities) or explicitly redesign/document the
  first-upgrade shutdown state. Do not kill a thread while it owns SQLite state.

**Acceptance tests**

- Block each Keychain CRUD operation and SQLCipher export, request dialog close
  and application quit, and assert the documented bound plus no post-cancel
  transaction.
- Cancel immediately before and after the Keychain write and before database
  commit; verify the documented cross-store recovery state on restart.

### P2 — Preserve secure migration temporary files from creation onward

**Evidence**

- `src/tatatuya/infrastructure/database.py:253-261` creates a `0600` temporary
  inode and then unlinks it before SQLCipher opens the path.
- SQLCipher therefore recreates the database with SQLite's process umask/default
  mode. `os.chmod(..., 0o600)` does not run until line 287, after export, secret
  deletion, snapshot comparison, commit, and close.
- The recovery marker is not written until line 291. A process kill during
  export leaves no marker naming the recreated target or its possible journal.

**Failure scenario and impact**

During conversion, and permanently after a pre-marker crash, the encrypted
target and possible journal can have a mode other than the promised `0600` and
remain as untracked artifacts. Before the logical deletion step, that target can
also contain the legacy Client Secret inside its encrypted copy. The `0700`
parent limits immediate exposure, but the file-mode and crash-cleanup contracts
are still false and stale sensitive copies accumulate.

**Required correction**

- Keep the `mkstemp()`-created `0600` inode if SQLCipher accepts the empty file,
  rather than unlinking the security property before `ATTACH`.
- Record a staged recovery marker before the long export, or clean only strictly
  validated random-prefix artifacts while holding the new interprocess lock.
- Include attached-database journals/WAL files in mode enforcement and recovery.

**Acceptance tests**

- Pause/kill after target open, midway through export, after export, after
  logical secret deletion, and at both rename boundaries. Inspect modes during
  the pause and prove restart removes only the exact artifacts or restores the
  source.

### P2 — Apply the Qt error-body cap before draining the response

**Evidence**

- `src/tatatuya/infrastructure/tuya/client.py:299-311` drains up to
  `max(limits.raw_bytes, limits.error_raw_bytes)` before knowing response status.
- Status and the applicable success/error limit are selected only after the
  nested event loop finishes at lines 342-356. `Content-Length` is checked later
  still, at lines 366-376.
- `docs/ARCHITECTURE.md:456-460,482-490,808-814` requires a 64 KiB error cap and
  rejection of a declared oversized body before reading/parsing it.

**Failure scenario and impact**

With the normal 1 MiB success allowance, a 4xx/5xx or redirect response can make
the app receive and retain up to 1 MiB before it is discarded as over the 64 KiB
error limit. A declared oversized error is also downloaded until that larger
streaming cap instead of being rejected from metadata. Parsing remains blocked,
but the transport-level resource boundary is not the documented one.

**Required correction**

Inspect status, encoding, and declared length when Qt emits response metadata.
Choose the active success/error limit then, abort redirects and declared
oversize immediately, and let `readyRead` drain only that active limit plus one
byte.

**Acceptance tests**

- Add Qt-transport tests for a declared 65,537-byte error, a streamed oversized
  error, an oversized redirect body, and a large valid success. Assert exact
  bytes read and that no target request or JSON parse occurs.

### P2 — Escape or remove rich-text table tooltips

**Evidence**

- `src/tatatuya/ui/components/device_table.py:73-75` copies the remote Tuya
  device name into a tooltip.
- Lines 95-97 copy a service/API-derived error message into another tooltip.
- Qt tooltips support rich text; unlike the new `plain_text_label()` helper,
  `QTableWidgetItem.setToolTip()` has no text-format flag.

**Failure scenario and impact**

A device name or API-derived message containing supported markup is rendered as
active tooltip formatting rather than literal data. This preserves a UI-spoofing
sink after the QLabel fixes and can alter tooltip layout or embed misleading
links/images.

**Required correction**

Remove the redundant device-name tooltip where the stretched table cell already
shows the complete value, or generate a deliberately rich tooltip whose dynamic
content is HTML-escaped. Apply the same rule to error tooltips and future status
tips/What's This content.

**Acceptance tests**

- Exercise markup, entities, links, image tags, bidi controls, and long remote
  text in both tooltips. Inspect the rendered tooltip and prove literal text,
  no external activation/resource request, and usable geometry.

### Follow-up release gates and verification

The following Phase 14 items remain release blockers even after the code defects
above are corrected:

- Perform the clean Apple Silicon encrypted-create/plaintext-upgrade, Keychain
  ACL, wrong/missing key, packaged runtime, Gatekeeper, and artifact rehearsal.
- Privately determine whether the historical device identifier in revision
  `7471583` is real. The value is intentionally not reproduced here. The new
  full-history rule is expected to keep CI/release preparation failing until the
  investigation and any coordinated cleanup are complete.

Checks performed for this follow-up on 2026-08-07:

- `.venv/bin/python -m pytest -q` — **320 passed**.
- `.venv/bin/python -m ruff check .` — **passed**.
- `.venv/bin/python -m pyright --pythonversion 3.12` — **0 errors**.
- `.venv/bin/python -m pip check` and `git diff --check` — **passed**.
- Pinned-lock audit with `pip-audit 2.9.0` — **no known vulnerabilities found**.
- The CPython 3.12/macOS ARM64 `sqlcipher3==0.6.2` wheel exists and its SHA-256
  matches `requirements-macos.lock`. Its Mach-O extension is ARM64, reports
  SQLCipher 4.12.0 in the binary, and has only `/usr/lib/libSystem.B.dylib` as a
  dynamic dependency; no system plaintext SQLite dependency was present.
- The changed Settings UI tests passed and both generated screenshots were
  inspected; the Keychain placeholder and controls were visible with usable
  geometry.
- A synthetic corrupt Client Secret reproduction confirmed that bootstrap aborts
  in `load_tuya()` before returning local state.

Limits of this follow-up: the local suite ran on Linux/Python 3.14 in the
explicit plaintext test mode. It did not execute macOS Keychain, SQLCipher file
conversion, the PyInstaller app/DMG, or Gatekeeper. `gitleaks` was not installed
locally. Those limitations are why the checked-in clean-Mac and historical-data
gates remain open.

The working tree already contained an unrelated deletion of `review.md` when
this review began. That deletion was preserved and is not part of this review.

## 1. Original pre-remediation executive summary (historical)

No `P0` issue or active hard-coded Tuya Client Secret was found in the current
source tree. Production Tuya endpoint methods are currently GET-only, and the
diagnostic inspector has an explicit read-only endpoint allowlist. Those are
important controls and must remain regression-tested.

At the original review, seven actionable findings remained:

| Priority | Finding | Release effect |
|---|---|---|
| P1 | Authentication headers follow cross-origin HTTP redirects | Block release |
| P1 | The Client Secret and the entire user database are plaintext with loose file modes | Block release |
| P1 | Settings connection-test cancellation and deadlines do not reach the Tuya client | Block release |
| P2 | Diagnostic redaction misses common key-name variants | Fix before release |
| P2 | Untrusted Tuya text can be interpreted as Qt rich text | Fix before release |
| P2 | The release workflow and dependencies are insufficiently pinned and over-privileged | Fix before release |
| P2 | A likely real Tuya device identifier exists in public Git history | Investigate and contain before release |

The approved storage direction is:

1. The user continues to enter Client ID, Client Secret, region, and currency in
   TataTuya Settings. They do not manually use Keychain Access.
2. TataTuya automatically stores the Client Secret as a macOS Keychain generic
   password and retrieves it when a remote workflow needs it.
3. TataTuya encrypts the **entire SQLite database** with SQLCipher.
4. TataTuya generates a cryptographically random 256-bit database secret and
   stores that secret in macOS Keychain, separate from the Tuya Client Secret.
5. The application data directory, database, journals, logs, migration
   temporaries, and backups also receive restrictive filesystem permissions as
   defense in depth.

The current product and architecture documents explicitly say credentials stay
in SQLite. That is now obsolete: implementation must update
`docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, and
`docs/IMPLEMENTATION_PLAN.md` in the same change as the security work. Do not
silently make code and documentation disagree.

## 2. Scope and threat model

### Assets

- Tuya Client Secret and short-lived access tokens.
- The SQLCipher database secret.
- Tuya Client ID, device IDs, device names, product/account metadata, and raw
  diagnostic payloads.
- Meter readings, usage patterns, timestamps, prices, currencies, and immutable
  billing calculations.
- GitHub release credentials, dependency inputs, and distributed `.dmg`
  artifacts.

### Trust boundaries

- Tuya HTTPS responses are remote, untrusted input even when TLS succeeds.
- Redirect targets are a separate origin and receive no trust merely because a
  trusted origin returned a `Location` header.
- SQLite and Keychain are separate persistence systems; changes spanning them
  are not one database transaction and require an explicit failure protocol.
- Qt presentation code must treat device names, API messages, and diagnostic
  values as untrusted plain text.
- GitHub Actions, third-party actions, PyPI packages, and native wheels are
  supply-chain inputs.
- Another local process may be able to read ordinary user files. It should not
  obtain useful database contents merely by copying `tatatuya.sqlite3`.

### Security goals

- A copied database, WAL, journal, temporary migration file, or ordinary backup
  is unreadable without the database secret from Keychain.
- An ordinary unrelated application cannot silently retrieve TataTuya's
  Keychain items. macOS may prompt the user when access control requires it.
- Tuya credentials never cross an origin boundary, enter logs or diagnostics,
  or remain in logical SQLite settings after migration.
- The application fails closed when Keychain or SQLCipher is unavailable. It
  never falls back to plaintext and never replaces an unreadable database with
  an empty one.
- Remote input cannot create active rich-text UI content or bypass diagnostic
  redaction using trivial key spelling changes.
- Cancellation and workflow deadlines bound shutdown and prevent new remote or
  database work after cancellation, subject only to the documented atomic
  status-capture boundary.
- Release jobs use least privilege and reproducible, reviewed dependency inputs.

### Explicit non-goals and residual risk

At-rest encryption does not protect data from malware running with control of
the user's logged-in session, process injection into TataTuya, screen capture,
an administrator/root compromise, or TataTuya itself while the database is open.
Python strings also cannot be reliably wiped from memory. Keychain and SQLCipher
materially improve protection against casual file access, another local user,
copied disks/backups, and unrelated applications, but they are not a sandbox.

An unsigned and unnotarized application also has weaker distribution identity
and a worse Keychain upgrade story than a signed application. The existing
unsigned-DMG product decision may remain temporarily, but signing and
notarization are strongly recommended and must not be described as providing no
security benefit.

## 3. Findings

### P1 — Reject redirects before sending another credential-bearing request

**Evidence**

- `src/tatatuya/infrastructure/tuya/client.py:94-122` constructs a normal
  `urllib.request.Request` and calls the default `urlopen()` opener.
- `src/tatatuya/infrastructure/tuya/client.py:344-353` adds `client_id`, `sign`,
  and sometimes `access_token` headers.
- A loopback reproduction on the reviewed revision returned a `302` to a
  different origin. The target received all three authentication header names:
  `access_token`, `client_id`, and `sign`. Synthetic sentinel values were used;
  no real credential was sent.

**Failure scenario and impact**

A Tuya endpoint, compromised upstream, proxy, or unexpected HTTP response can
redirect the request to another host. Python's default redirect handler creates
and sends a second request, forwarding TataTuya's nonstandard authentication
headers. The target obtains the Client ID, request signature, and possibly an
access token. Even where the Client Secret itself is not transmitted, this is a
credential disclosure and violates the diagnostic/release security gate.

**Required correction**

- Before constructing `urllib.request.Request`, validate that the URL uses
  `https`, has no user-info or fragment, and has the exact normalized host/port
  for the selected Tuya region. Treat a mismatch as an internal security error.
- Build a dedicated opener whose redirect handler rejects every `301`, `302`,
  `303`, `307`, and `308` response. TataTuya signs a canonical regional Tuya URL
  and has no current product need to follow redirects.
- Convert a rejected redirect into a safe `TuyaAPIError`; do not include the
  `Location` value if it could contain credentials or private query data.
- Do not “fix” this by stripping only `Authorization`. TataTuya's sensitive
  headers use Tuya-specific names.
- If redirects ever become a documented requirement, permit only the exact same
  HTTPS origin after URL normalization, rebuild and re-sign the request, and
  reject HTTPS-to-HTTP downgrade. That is a future design change, not the
  recommended current fix.

**Acceptance tests**

- Local servers exercise all five redirect codes to same-origin, cross-origin,
  and HTTPS-to-HTTP targets.
- The transport makes exactly one request and the target server receives no
  request at all.
- The error presented to users is Romanian and contains no header values,
  signature, token, Client Secret, or unsafe `Location` content.

### P1 — Move secrets to Keychain and encrypt the complete database

**Evidence**

- `src/tatatuya/infrastructure/repositories/settings.py:41-50` writes
  `settings.client_secret` directly to the SQLite `settings.value` column.
- `src/tatatuya/infrastructure/database.py:18-28` creates the parent directory
  and database using ambient process defaults and standard-library SQLite.
- A fresh-database reproduction under the current environment created the data
  directory as `0755` and the database as `0644`.
- `src/tatatuya/infrastructure/logging_setup.py:14-29` similarly relies on
  ambient modes for the log directory and file.
- `docs/PRODUCT_SPEC.md:448` and `docs/ARCHITECTURE.md:334-336` explicitly
  document the current plaintext design.

**Failure scenario and impact**

Any process or account that can read the file obtains the Tuya Client Secret,
device/account metadata, raw diagnostics, energy-usage history, prices, and
calculation history. Moving only the Client Secret to Keychain would protect the
credential but would leave the privacy-sensitive database readable. Restrictive
Unix modes help against other accounts but do not protect a copied backup or a
process that gains file access as the same user.

**Required correction**

Implement the Keychain and SQLCipher design in section 4. This is one security
change with two distinct keys:

- Keychain item `tuya-client-secret-v1`: the user-entered Tuya Client Secret.
- Keychain item `database-key-v1`: 32 bytes from `secrets.token_bytes(32)`,
  encoded for the selected SQLCipher driver without reducing entropy.

Set the default application data directory to `0700` and regular sensitive
files to `0600`, including database sidecars, logs, migration temporaries, and
any exported backup. Correct existing overly broad modes on startup after
validating ownership and file type. Never follow a symlink while “repairing”
permissions or replacing the database.

**Acceptance tests**

- A known sentinel Client Secret is absent from the logical `settings` table
  after migration.
- The database header is not `SQLite format 3\0`; standard `sqlite3`, a wrong
  SQLCipher key, and an empty key cannot read it.
- `PRAGMA cipher_version` proves the packaged process is actually using
  SQLCipher, and `PRAGMA cipher_integrity_check` returns no errors.
- `strings` cannot find sentinel device names, readings, prices, raw JSON, or the
  Client Secret in the database, WAL/journal, migration temporary, or packaged
  artifacts.
- Restart with the correct Keychain items preserves every row and immutable
  calculation. Missing/denied/wrong Keychain keys fail safely and do not create
  or overwrite a database.
- Directory and file modes are verified on a clean Mac and on upgrade.

### P1 — Propagate connection-test cancellation and enforce absolute deadlines

**Evidence**

- `src/tatatuya/services/settings_service.py:26-35` types the gateway factory as
  accepting only `TuyaSettings`.
- `src/tatatuya/services/settings_service.py:45-74` accepts a
  `CancellationContext`, but line 54 constructs the gateway without it.
- `src/tatatuya/ui/app.py:299-310` passes the `TuyaClient` class directly, so
  the production connection-test client has `cancellation=None`.
- `src/tatatuya/ui/dialogs/settings.py:173-190` starts the connection-test worker
  with the 15-second default even though `docs/ARCHITECTURE.md:555-563` specifies
  a 30-second Settings connection-test deadline.
- `src/tatatuya/infrastructure/tuya/client.py:220-236` has no explicit page or
  device ceiling for discovery.
- `src/tatatuya/infrastructure/tuya/client.py:466-485` performs repeated body
  reads without consulting the cancellation context or recomputing an absolute
  remaining deadline.

**Failure scenario and impact**

Closing Settings or quitting the application sets the cancellation flag, but an
in-flight authentication/device-list client does not see it. Device pagination
can continue, a blocking operation can outlive the worker's nominal deadline,
and the shutdown coordinator waits for the worker to finish. This violates the
documented six/eleven-second shutdown-drain bounds and can leave the app stuck
in its closing state. A malicious or broken server can amplify the problem with
many unique cursors or slow response reads.

**Required correction**

- Change the settings gateway factory contract so the same
  `CancellationContext` is passed into `TuyaClient`.
- Start the Settings connection-test worker with the documented 30-second
  workflow deadline. Keep Settings load/save at 15 seconds.
- Treat the deadline as an absolute monotonic deadline across DNS resolution,
  connection, TLS, response headers, and all body reads. If the chosen transport
  cannot guarantee that bound, use an abortable transport rather than assuming
  a socket timeout is an end-to-end timeout.
- Check cancellation between discovery pages and before each new request.
- Add explicit, documented maximum discovery pages and devices. Exceeding the
  ceiling must fail as an incomplete listing and must not mark cached devices as
  absent.
- Keep Keychain calls and SQLCipher initialization off the Qt UI thread as well;
  Apple's `SecItemCopyMatching` is a blocking API.

**Acceptance tests**

- Cancel during authentication, headers, body read, and pagination; no later
  request or write starts.
- Simulate a slow-loris body and a resolver/connect stall; the worker terminates
  inside the documented bound.
- Repeated unique pagination cursors hit the explicit ceiling without unbounded
  memory or network work.
- Quit during the Settings test drains within the ordinary six-second shutdown
  bound after cancellation, or the documented bound is revised with evidence.

### P2 — Canonicalize and broaden diagnostic redaction

**Evidence**

- `src/tatatuya/infrastructure/tuya/parsers.py:270-291` redacts only five exact
  lowercase snake-case keys.
- A direct adversarial check left these keys and values unredacted:
  `refresh_token`, `authorization`, `clientSecret`, `client-secret`, and
  `localKey`.
- Raw device, specification, status, event, and error payloads all pass through
  this helper before persistence or display, so its coverage is a central
  security boundary.

**Failure scenario and impact**

Tuya or an intermediary returns a sensitive field using camelCase, kebab-case,
a new token name, or an authorization field. TataTuya persists and may display
the value in raw diagnostics. Full-database encryption reduces at-rest exposure
but does not make disclosure in the running UI, clipboard, or support output
acceptable.

**Required correction**

- Canonicalize keys with `casefold()` and remove separators such as `_`, `-`,
  `.`, and whitespace before comparison.
- Prefer conservative over-redaction for diagnostic-only data. Redact canonical
  names containing or matching `secret`, `token`, `password`, `passwd`,
  `credential`, `authorization`, `localkey`, and known Tuya key aliases.
- Continue replacing known dynamic secret values recursively even when the key
  is unknown.
- Sanitize before serialization, persistence, display, copying, and fixture
  capture. Never use a diagnostic blob as a billing fingerprint.
- Use fixed markers and bounded metadata for opaque non-JSON error bodies.

**Acceptance tests**

- A table-driven nested payload test covers snake_case, camelCase, kebab-case,
  uppercase, dotted names, lists, and unknown-key strings containing known
  dynamic secrets.
- Persisted raw JSON, user-visible technical details, clipboard output, logs,
  and inspector output contain no sentinels.
- Legitimate energy fields still retain their exact decimal representation.

### P2 — Force untrusted Qt strings to plain text

**Evidence**

- Qt documents `QLabel`'s default format as `Qt.AutoText` and warns that
  web-loaded strings must be sanitized or explicitly set to plain text.
- Remote device names and metadata reach labels without `setTextFormat`, for
  example `src/tatatuya/ui/dialogs/calculate.py:104` and
  `src/tatatuya/ui/components/modal.py:65-75`.
- Service-originated error title/message strings reach labels at
  `src/tatatuya/ui/dialogs/error.py:60-68` without an explicit text format.

**Failure scenario and impact**

A device name or API-derived message that resembles supported HTML is rendered
as rich text rather than as literal data. It can visually spoof application
content, hide text, create misleading emphasis or links, and change layout. This
is a UI injection boundary even where script execution is unavailable.

**Required correction**

- Introduce a small presentation helper for untrusted labels that sets
  `Qt.TextFormat.PlainText` before assigning content.
- Use it for all device names, product metadata, diagnostics, API-derived
  errors, and any future user-entered free text. Fixed developer-owned rich text
  may opt in explicitly at its call site.
- Keep external-link opening disabled for untrusted content.
- Continue using `QPlainTextEdit.setPlainText()` for large raw diagnostics.

**Acceptance tests**

- Render representative values such as `<b>Contor fals</b>`, links, entities,
  malformed tags, very long Romanian text, and bidirectional-control characters.
- Assert plain-text format and literal visible content; inspect screenshots in
  light and dark palettes where layout changes.
- Verify the text creates no clickable external link and does not alter adjacent
  widget geometry unexpectedly.

### P2 — Harden dependencies and split release privileges

**Evidence**

- `.github/workflows/release-macos.yml:8-76` gives `contents: write` to the
  entire build/test/install job.
- `actions/checkout@v6`, `actions/setup-python@v6`, and
  `actions/upload-artifact@v7` are mutable tags rather than full commit SHAs.
- Checkout does not set `persist-credentials: false`.
- `pyproject.toml:10-23` and `requirements.txt:1` use open-ended minimum
  dependency versions with no hash-locked transitive set.
- The declared `PySide6>=6.7` range permits versions affected by
  CVE-2026-6210. Qt states that Qt 6.7.0 through 6.8.7 and 6.9.0 through 6.11.0
  are affected; the reviewed environment uses fixed PySide6 6.11.1, but CI is
  free to resolve differently later.
- The `.dmg` is intentionally unsigned and unnotarized.

**Failure scenario and impact**

A compromised action, dependency release, package index account, install-time
hook, test, or build script runs while a repository-write token is available.
Mutable dependency resolution also makes an old tag non-reproducible and can
silently reintroduce a vulnerable Qt build. Users cannot reliably authenticate
an unsigned downloaded app.

**Required correction**

- Pin every third-party action to a reviewed full commit SHA; retain the release
  tag in a comment for maintainability.
- Split build/test from release publication. The build/test job gets
  `contents: read`, checkout uses `persist-credentials: false`, and no write
  token is available while project or dependency code runs. A narrow publication
  job gets `contents: write` only after required checks and artifact transfer.
- Generate a complete dependency lock for Python 3.12/macOS arm64 and CI test
  platforms, including hashes. Install with hash verification. Pin and review
  SQLCipher, PyObjC/Keychain integration, PyInstaller, Qt, and transitive native
  wheels.
- Add automated dependency and secret scanning, plus a scheduled update process
  that reruns the full suite and packaged security checks.
- Publish SHA-256 checksums and build provenance/SBOM. Move to Developer ID
  signing and notarization when feasible; do not let checksums from the same
  compromised release job stand in for code signing.

**SQLCipher dependency warning**

Do not casually adopt stale `pysqlcipher3` or silently compile against system
SQLite. As of this review, the third-party `sqlcipher3`/`sqlcipher3-binary` 0.6.2
project advertises CPython 3.12 Apple Silicon wheels, but it is not Zetetic's
official Python distribution and its PyPI upload provenance must be reviewed.
Run a dependency spike, inspect source/license and wheel linkage, pin exact
hashes, verify the embedded SQLCipher version at runtime, and prove PyInstaller
bundles the intended arm64 native library. Buying Zetetic's official Apple
binary is an alternative but still requires a reviewed Python bridge and
packaging work.

**Acceptance tests**

- CI logs prove build/test has read-only permissions and no persisted checkout
  credential.
- A clean, offline rebuild from the lock resolves the expected hashes and
  versions.
- Runtime checks inside the packaged app report the approved SQLCipher and Qt
  versions without exposing secrets.
- The final executable and every native library are arm64 and the DMG checksum
  matches the published value.
- The dependency scanner blocks known affected versions, including the Qt SVG
  ranges above.

### P2 — Investigate and contain the historical device identifier

**Evidence**

- The configured Git remote is `Policoo/TataTuya`, and GitHub exposed it as a
  public repository on the review date.
- Historical revision `7471583`, file `test-tuya.sh`, line 5 contains a
  22-character strict alphanumeric literal assigned to `DEVICE_ID`. The literal
  is intentionally not reproduced here.
- The same public page exposed the old diagnostic scripts even though later
  local history deletes them.
- No corresponding literal Client Secret assignment was found in that reviewed
  historical script set; derived access-token variables are not hard-coded
  tokens. This does not prove that every unreachable GitHub ref, fork, cached
  view, CI log, or external clone is clean.

**Failure scenario and impact**

If the identifier belongs to a real household device, public history exposes
private infrastructure metadata and a stable handle useful for correlation or
future attack chains. A device ID alone is not treated as a Tuya credential, so
the evidence does not establish account takeover, but it should not be left
uninvestigated.

**Required correction**

1. Privately verify whether the identifier is real and still active. Do not paste
   it into an issue, PR, commit message, or this document.
2. If any Client Secret, token, local key, or other credential is discovered
   during the expanded audit, revoke/rotate it **before** rewriting history.
3. Ask Tuya what reset/unlink/re-pair action, if any, changes the exposed device
   identifier without violating the application's read-only rule. Operational
   account maintenance is outside TataTuya's runtime behavior.
4. Decide whether the privacy value justifies a coordinated `git-filter-repo`
   history rewrite. Clean branches, tags, PR refs, forks, cached views, CI
   artifacts, and collaborator clones; a force-push alone is incomplete.
5. Enable GitHub secret scanning/push protection where available and keep real
   account data out of fixtures and release rehearsals.

**Acceptance evidence**

- A private incident note records whether the value was real, what was rotated
  or re-paired, and which public refs/caches were handled.
- A fresh clone and GitHub code/history search find no real device/account data.
- Current and historical fixtures contain only unmistakably synthetic values.

## 4. Approved Keychain and SQLCipher implementation design

This section is the implementation contract for the storage finding. Do not
replace it with field-by-field home-grown encryption.

### 4.1 User experience

- Settings remains the only credential entry point; there is no first-run
  wizard.
- On first save, the user types the Client Secret in the normal masked field.
  TataTuya writes it to Keychain automatically.
- On later Settings visits, do not repopulate the full secret into a `QLineEdit`.
  Show a Romanian “secret stored” state and leave the replacement field empty.
  An empty untouched field preserves the stored secret; a deliberate replacement
  updates it. If credential clearing is supported, use an explicit action and
  confirmation rather than overloading an empty field.
- Connection testing may transiently retrieve the secret into a
  `TuyaSettings` value. It must not log it, attach it to long-lived UI state, or
  include it in signals/errors.
- A denied/locked/unavailable Keychain produces a bounded Romanian recovery
  error. It must not be reported as “credentials missing” or cause plaintext
  storage.

### 4.2 Architecture boundaries

- Keep `TuyaSettings` as a domain/service transfer value if useful, but treat its
  Client Secret as transient.
- Add a narrow `SecretStore` contract used by the settings workflow. The macOS
  implementation belongs in `infrastructure`, not in Qt widgets or domain code.
- A composite settings adapter may coordinate SQLite non-secret settings and
  Keychain secret storage behind the existing service port, but its failure
  order must follow section 4.5. Do not make the UI call Keychain directly.
- Add an infrastructure-only `DatabaseKeyStore` used by `Database`. The
  SQLCipher key is not an application setting and should not pass through
  ordinary services or UI.
- Replace standard-library `sqlite3` consistently across
  `infrastructure/database.py`, `infrastructure/migrations.py`, repositories,
  exception handling, and type annotations. Merely changing `connect()` while
  retaining `except sqlite3.Error` from the standard library can miss driver
  exceptions.
- Production on macOS must fail if the Keychain or SQLCipher adapter cannot
  load. Linux tests may inject an in-memory key store and temporary key, but
  there must be no automatic production fallback to plaintext SQLite.
- `scripts/inspect_tuya.py` must open the database through the same secure
  `Database` and Keychain adapters. It must remain GET-only and must never accept
  a database key or Client Secret as a command-line argument.

### 4.3 Keychain item design

Use Apple's Security framework `SecItem` APIs through a pinned, reviewed bridge
such as `pyobjc-framework-Security`; do not invoke `/usr/bin/security` with a
secret in command arguments or parse its human-oriented output.

Use generic-password items with stable, versioned identifiers:

| Attribute | Tuya secret | Database secret |
|---|---|---|
| `kSecClass` | `kSecClassGenericPassword` | `kSecClassGenericPassword` |
| `kSecAttrService` | `ro.tatatuya.app` | `ro.tatatuya.app` |
| `kSecAttrAccount` | `tuya-client-secret-v1` | `database-key-v1` |
| `kSecAttrLabel` | Human-readable TataTuya label | Human-readable TataTuya label |
| `kSecAttrSynchronizable` | false unless explicitly designed otherwise | false unless explicitly designed otherwise |

Use `SecItemAdd`, `SecItemCopyMatching`, `SecItemUpdate`, and `SecItemDelete` and
check every `OSStatus`. Treat duplicate, not-found, user-cancelled, interaction-
not-allowed, auth-failed, and missing-entitlement outcomes distinctly without
including secret bytes in diagnostics. Do not specify an access group the app
is not entitled to use.

The bundle identifier is already `ro.tatatuya.app`. Test Keychain access after a
fresh install, rebuild, upgrade, move of the `.app`, and eventual signing change.
Because the current app is unsigned, do not assume stable app-only ACL behavior:
manually verify that a separate test executable cannot silently read either
item and record whether macOS prompts. Signing/notarization should stabilize the
application identity.

Keychain APIs can block and may present system UI. Call them from a worker, not
the Qt UI thread. Never retry auth/user-cancel outcomes in a tight loop.

### 4.4 Fresh database lifecycle

1. Resolve the production database path. Reject unexpected symlink/non-regular
   file states and verify ownership before mode repair or replacement.
2. Create the parent with `0700` and explicitly enforce the mode.
3. Retrieve `database-key-v1`. If no database exists and no key exists, generate
   32 bytes with `secrets.token_bytes(32)` and add the item to Keychain.
4. Open with the selected SQLCipher DB-API driver and provide the key **before
   any schema query or PRAGMA that reads the database**. Prefer a driver key API;
   if SQL text is unavoidable, only interpolate a validated encoding of the
   generated random bytes and never log/profile that statement.
5. Query `PRAGMA cipher_version` and fail if it is empty. Do not continue with a
   standard SQLite library.
6. Apply `foreign_keys`, bounded `busy_timeout`, and the existing ordered schema
   migrations through the encrypted connection.
7. Verify `PRAGMA cipher_integrity_check`, then close and reopen with the same key
   during creation/integration tests.
8. Enforce `0600` on the database and all sidecars. Evaluate journal mode and
   prove its journal/WAL is encrypted by the packaged SQLCipher build. Keep
   SQLite temporary storage in memory where compatible with correctness and
   test volume.

Do not change SQLCipher cipher/KDF/HMAC defaults without a documented reason and
compatibility tests. Pin the SQLCipher major version and record any compatibility
parameters needed to open older encrypted databases before upgrading them.

### 4.5 Existing plaintext database migration

Encryption is a file-format transition performed before normal schema
migrations, not merely another SQL statement in `MIGRATIONS`.

The migration must be idempotent and crash-safe:

1. Run off the UI thread with an exclusive application-level database migration
   lock. No other connection may open during conversion.
2. Classify the file without writing it. `SQLite format 3\0` indicates the
   expected legacy plaintext format. A different header is not automatically
   trusted as encrypted: try the Keychain key and a harmless schema query. If
   neither known format opens, stop with a Romanian recovery error.
3. For a legacy database, read the Client Secret only for migration, save it to
   `tuya-client-secret-v1`, and verify a Keychain round trip. If this fails,
   leave the original database untouched.
4. Retrieve or create `database-key-v1`. Once an encrypted database already
   exists, a missing/wrong key is a hard failure; **never generate a replacement
   key and never create a new empty database at that path**.
5. Create a `0600` temporary file in the same directory. Open the plaintext
   source with the SQLCipher-capable driver using an empty source key, attach a
   new encrypted target with the Keychain key, and use SQLCipher's documented
   `sqlcipher_export()` operation. Do not loop over tables in Python and do not
   invent per-column crypto.
6. In the encrypted target, delete the logical `tuya.client_secret` setting.
   Preserve Client ID, region, currency, every `schema_migrations` row, table,
   index, foreign key, row ID, UTC timestamp, canonical decimal string, raw
   diagnostic JSON, reading reference, and immutable calculation.
7. Verify before replacement:

   - expected schema objects and migration versions;
   - row counts for every table;
   - stable IDs and representative hashes/canonical values;
   - `PRAGMA foreign_key_check` returns no rows;
   - normal SQLite integrity check succeeds;
   - `PRAGMA cipher_integrity_check` returns no errors;
   - reopen with the correct key succeeds;
   - empty and wrong keys fail;
   - the Client Secret key is absent from logical settings.

8. Flush file and containing-directory metadata as supported, then atomically
   replace the original with the verified encrypted file. Preserve a recoverable
   rollback path until the replacement is durable, but never leave a plaintext
   backup under a predictable long-lived filename.
9. On any pre-replacement failure, close handles, remove only the known temporary
   target, keep the original database, and retain the verified Keychain items so
   retry is idempotent. Never delete or truncate the source to “start over.”
10. After success, correct modes and start normal migrations/application work.

Deleting or replacing a plaintext SQLite file does not prove its old bytes are
gone from APFS snapshots, Time Machine, SSD remapping, cloud backups, or external
copies. Secure deletion cannot be promised. For high-risk installations,
recommend rotating the Tuya Client Secret after successful migration and
removing known plaintext backups according to the user's backup policy.

### 4.6 Cross-store Settings update protocol

SQLite and Keychain cannot commit atomically together. Use an order that never
loses the only working secret:

1. Validate the proposed settings and, when required, test the connection using
   the transient proposed secret.
2. Add/update the Keychain secret and immediately read it back for equality.
3. Commit non-secret Client ID, region, and currency to the encrypted database.
4. Do not store the Client Secret in SQLite, even encrypted.
5. If step 3 fails, report failure while leaving the new Keychain secret intact;
   a retry is safe. Do not restore an older secret unless that rollback was
   explicitly retained and tested.

If credential clearing is implemented, clear the logical connection metadata
and Keychain item using an explicit, documented recovery order. Local readings
and calculations remain usable and must never be deleted.

### 4.7 Backup and key-loss policy

If `database-key-v1` is lost, the encrypted database is intentionally
unreadable. Copying only `tatatuya.sqlite3` to another Mac is not a complete
backup. This is the cost of separating the data and key.

Before release, the product owner must choose and document one of these:

- **V1 local-key policy:** accept that loss of the login Keychain item can make
  history unrecoverable; document this clearly and test supported Time Machine
  and Mac-migration behavior.
- **Portable encrypted backup:** add an explicit export that creates a separate
  SQLCipher database protected by a user-supplied recovery passphrase, with a
  restore test. Do not export plaintext and do not invent a custom encryption or
  key-wrapping format.

Do not silently enable iCloud Keychain synchronization for the database key;
that changes the privacy, recovery, and account-compromise model. Do not build a
“forgot key” backdoor or store a second plaintext key beside the database.

### 4.8 Packaging requirements

- Include the reviewed SQLCipher native library and Keychain bridge in the
  PyInstaller bundle. The build must fail if either is missing.
- Extend `scripts/build_macos.sh` smoke testing to query `cipher_version`, create
  encrypted data, restart, and reopen it. Use synthetic data only.
- Avoid polluting a developer's real Keychain during tests. CI may use an
  explicitly namespaced disposable item on an ephemeral runner and delete that
  exact item during cleanup. Unit/Linux tests use injected fakes.
- Inspect the `.app` with `otool`/`lipo` to prove it loads the intended arm64
  SQLCipher library rather than `/usr/lib/libsqlite3` for application database
  access.
- Perform an upgrade rehearsal on a clean Apple Silicon Mac using a synthetic
  schema-4 plaintext database populated with every table and edge-case decimal.
- Run the diagnostic inspector against the migrated encrypted database and
  confirm it cannot bypass Keychain or print secrets.

## 5. Implementation sequence

Keep changes reviewable and do not combine unrelated refactors.

### Phase A — Immediate containment

1. Reject HTTP redirects and add the leakage regression tests.
2. Privately verify the historical device ID and rotate/re-pair anything that is
   actually sensitive before considering history rewriting.
3. Enable secret/dependency scanning and prevent new real fixtures.

### Phase B — Storage foundation

1. Decide and lock the reviewed SQLCipher and Keychain bridge dependencies.
2. Add Keychain adapters with injected fakes and explicit OSStatus mapping.
3. Add the SQLCipher connection factory, fail-closed runtime checks, and secure
   file mode handling.
4. Implement the crash-safe legacy conversion with fault-injection tests at
   every numbered step in section 4.5.
5. Move the Tuya Client Secret out of logical settings and update Settings UI
   behavior.
6. Update product, architecture, implementation plan, README backup/recovery
   guidance, packaging spec, and inspector.

### Phase C — Network lifecycle and diagnostics

1. Pass cancellation/deadline through the Settings gateway factory.
2. Add absolute transport deadlines and discovery ceilings.
3. Canonicalize redaction and cover every persistence/display/clipboard route.
4. Force remote and error label values to plain text and perform the required Qt
   screenshot verification.

### Phase D — Release supply chain

1. Hash-lock dependencies and pin actions to full SHAs.
2. Split read-only build/test from write-capable draft publication.
3. Add checksums, SBOM/provenance, packaged dependency/runtime checks, and the
   clean-Mac encrypted-upgrade rehearsal.
4. Plan Developer ID signing and notarization.

## 6. Required test matrix

The implementation is incomplete unless all applicable rows pass.

| Area | Required cases |
|---|---|
| Redirects | 301/302/303/307/308; same/cross origin; downgrade; zero second requests; no sensitive error content |
| Keychain CRUD | add/read/update/delete; duplicate; missing; denied; locked; user cancel; corrupt bytes; no UI-thread call |
| Fresh SQLCipher DB | random key; encrypted header; cipher version; correct/wrong/empty key; restart; integrity; modes |
| Legacy migration | empty DB; schema 1-4; all tables; no secret; exact decimals; raw JSON; immutable references; row counts |
| Migration faults | failure after Keychain write, export, validation, fsync, and replacement; kill/restart at each boundary |
| Sidecars/temp | rollback journal/WAL/temp encrypted; `0600`; no predictable plaintext backup; cleanup targets exact files |
| Key loss | missing item, wrong item, denied access, copied DB on another Mac; never create empty replacement |
| Settings UX | first entry, stored-secret placeholder, unchanged secret, replacement, failed save/test, optional explicit clear |
| Cancellation | auth, connect, slow body, pagination, Keychain, DB busy; no new work; bounded shutdown |
| Redaction | casing/separator variants, nested lists/maps, dynamic secret values, errors, clipboard, inspector, persisted JSON |
| Qt text | malicious markup/entities/links/bidi/long text rendered literally; geometry and light/dark screenshots |
| Read-only Tuya | production endpoints remain GET-only; inspector rejects command/reset/unknown URLs |
| Package | clean arm64 install, native linkage, encrypted create/restart/migrate, Keychain ACL behavior, artifact scan |
| CI/supply chain | read-only build token, no persisted credential, hash-locked install, pinned actions, dependency scan |

Tests must use synthetic sentinels and must assert their absence without printing
the sentinel on failure. No test may inspect or modify the developer's real
TataTuya database or Keychain items.

## 7. Release-blocking checklist

Do not promote a draft release until every item below is complete:

- [ ] All three P1 findings are fixed with regression tests.
- [ ] The P2 redaction and Qt plain-text findings are fixed.
- [ ] SQLCipher and Keychain dependencies have been reviewed, pinned, and
      hash-locked.
- [ ] A fresh database and a fully populated legacy plaintext database migrate
      successfully on a clean Apple Silicon Mac.
- [ ] Missing/wrong/denied Keychain key tests prove the app never creates an
      empty replacement database or plaintext fallback.
- [ ] Database, sidecar, log, temporary, and backup permissions/content checks
      pass in the packaged app.
- [ ] The historical device identifier investigation is recorded privately and
      any real exposed credential was rotated before cleanup.
- [ ] Release workflow actions are SHA-pinned and write privilege is isolated
      from build/test/dependency execution.
- [ ] The complete unit, integration, and UI suite, Ruff, Pyright, dependency
      scan, secret scan, and `pip check` pass.
- [ ] Light/dark Qt screenshots are inspected for every changed screen.
- [ ] The backup/key-loss behavior is an explicit product decision documented
      for users.
- [ ] The draft DMG is installed and exercised without developer tooling, and
      the artifact contains no credential or captured personal/device data.

## 8. Verification performed for this review

The following checks were run on 2026-08-07 against the reviewed working tree:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest` — **270 passed**.
- `.venv/bin/python -m ruff check .` — **passed**.
- `.venv/bin/python -m pyright --pythonversion 3.12` — **0 errors**.
- `.venv/bin/python -m pip check` — **no broken requirements**.
- Installed versions inspected: PySide6 6.11.1, PyInstaller 6.21.0, Ruff
  0.15.22, Pyright 1.1.411.
- Fresh database permission reproduction — directory `0755`, database `0644`.
- Loopback redirect reproduction — cross-origin target received the synthetic
  `access_token`, `client_id`, and `sign` header names.
- Redaction-variant reproduction — `refresh_token`, `authorization`,
  `clientSecret`, `client-secret`, and `localKey` remained visible.
- Current source and reviewed history were searched for credential/device-data
  indicators without reproducing candidate values in output. No active source
  Client Secret was found; the historical 22-character device ID candidate was
  confirmed.
- `git diff --check` — **passed** for this documentation change.

A production-targeted `ruff check --select S src scripts` reported six items and
was triaged rather than treated as a passing gate:

- Two `S310` reports identify `Request`/`urlopen`; these support the URL-scheme,
  host, and redirect finding above.
- One `S105` report mistakes the setting key name `tuya.client_secret` for a
  hard-coded password; it is not a credential value. The setting itself must
  nevertheless be removed from logical SQLite storage by the approved migration.
- One `S608` report identifies the migration `executescript()` f-string. Its SQL
  and version currently come only from the code-owned `MIGRATIONS` tuple and its
  timestamp is escaped, so no untrusted input reaches the script. Keep migration
  definitions code-owned and retest this boundary during the SQLCipher driver
  conversion.
- Two `S101` reports identify production assertions immediately after repository
  writes. They are not a demonstrated security exploit, but a maintenance change
  should replace them with explicit invariant failures because `assert` can be
  removed by optimized Python execution.

Passing tests do not reduce the severity of the findings because the current
suite does not assert the failing security properties above. Linux verification
also cannot substitute for the required macOS Keychain, native SQLCipher,
Gatekeeper, packaging, and clean-machine checks.

## 9. Primary references

- [Apple Keychain Services](https://developer.apple.com/documentation/Security/keychain-services)
- [Apple: Adding a password to the keychain](https://developer.apple.com/documentation/security/adding-a-password-to-the-keychain)
- [Apple: `SecItemCopyMatching` (including blocking-call guidance)](https://developer.apple.com/documentation/security/secitemcopymatching%28_%3A_%3A%29)
- [Apple: macOS keychain implementation notes (TN3137)](https://developer.apple.com/documentation/Technotes/tn3137-on-mac-keychains)
- [SQLCipher API, `sqlcipher_export`, and integrity checks](https://www.zetetic.net/sqlcipher/sqlcipher-api/)
- [SQLCipher: Encrypting a plaintext database](https://www.zetetic.net/sqlcipher/encrypting-plaintext-databases/)
- [Python `urllib.request` redirect handling](https://docs.python.org/3/library/urllib.request.html)
- [Qt for Python `QLabel` plain/rich-text warning](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QLabel.html)
- [Qt CVE-2026-6210 advisory](https://www.qt.io/blog/security-advisory-type-confusion-and-heap-buffer-overflow-vulnerability-in-qt-svg-marker-handling)
- [GitHub Actions secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub: Removing sensitive data from repository history](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
