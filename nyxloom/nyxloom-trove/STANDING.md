# Standing package contracts — nyxloom implementation waves

Inherited by EVERY handoff in this directory. Read once, follow exactly.
Use TODAY'S ACTUAL DATE — the one your carve packet states, not a date copied
from this file. (Wrong dates are review-rejected. Until 2026-08-02 this line
hardcoded "2026-07-15" and instructed every handoff to use it, so it had been
mandating a wrong date for 18 days; a date pinned in an inherited contract is
wrong by construction.)

## Environment

- Work dir: `/workspaces/vbpub/nyxloom`. Never leave it.
- Python (DIAGNOSTIC only): the interpreter on `PATH` (currently 3.14; PyYAML,
  jsonschema, hypothesis, pytest installed — install NOTHING). Corrected
  2026-08-02: this line named `/workspaces/vbpub/.venv/bin/python` at "3.13",
  a path that does not exist.
  <!-- product-truth:interpreter=3.14 -->
- **Gate — the only accepted evidence** is the project's real declared gate,
  `[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml`: pytest under
  `-n auto` with coverage inside the `tester-unified` container, followed by
  the changed-line coverage floor. Run it; paste the tail of its real output
  into your REPORT.
  <!-- product-truth:authoritative_gate=tester-unified -->
  (CR-01/DR-04, 2026-08-03: both markers above are asserted against the
  running interpreter and `nyxloom.toml`'s declared `[gates.*]` by
  `tests/test_product_truth.py` on every gate run, so this paragraph cannot
  go stale again the way the corrections above record it once did.)
  Corrected 2026-08-02: this line used to name a cockpit venv `pytest` command
  as "the only accepted evidence", contradicting both `nyxloom.toml` and the
  project's own rule that cockpit runs are diagnostic and are never release
  evidence. A cockpit run is still useful for your inner loop — it is just not
  proof, and a REPORT that offers only a cockpit tail is incomplete.
  NOTE: the coverage phase diffs committed `HEAD`, so it reports NO MEASUREMENT
  (and fails) while your work is uncommitted. That is the gate failing closed,
  not a coverage failure; say so explicitly rather than presenting it as green.

## Frozen files — read, NEVER modify

`pyproject.toml`, `tests/conftest.py`, `schemas/`, `docs/`, and
`src/nyxloom/{__init__,types,paths,storage,config,leases}.py`, plus every
file owned by another package. Your module's stub DOCSTRING is the normative
interface: implement beneath it, keep the docstring and all public
signatures EXACTLY as written. If a frozen file or the contract seems wrong,
insufficient, or impossible: STOP — do not improvise, do not work around —
write `BLOCKED: <reason>` in your REPORT and final message, and exit.

### Core-redesign wave exception (CR-00 through CR-16)

The operator-approved core-redesign program in
`reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md` supersedes
the frozen-file list only for a package whose explicit contract names one of
those files. This is a package-scoped ownership grant, not a general unfreeze:

- CR-01 may change the declared document/lint surfaces it audits.
- CR-03 and CR-07 may change `types.py` and their explicitly named schemas.
- CR-04 may change `storage.py`, `storage_sqlite.py`, and their explicitly
  named schemas and command surfaces.
- Other CR packages may change a normally frozen file only when their written
  contract names the exact file and explains why the package acceptance cannot
  be met without it.

Files owned by another active package remain frozen. An agent that discovers a
new frozen-file need must request a bounded contract amendment; it must not
infer ownership from this exception. Existing live state and nonterminal tasks
must be preserved through backup plus versioned upcasting. No CR package is
authorized to delete or silently reset live state.

## Cross-package dependencies

Other packages are being implemented in parallel; their modules may still
raise NotImplementedError. Code against their frozen interfaces; in YOUR
tests, monkeypatch those functions with canned returns where your handoff
says so. Never import-and-hope; never reimplement another package's logic.

## Code and test rules

- stdlib + PyYAML + jsonschema (+ hypothesis in tests) only. Type hints on
  public functions. No dead code, no scaffolding, ASCII only.
- Use conftest fixtures (`tmp_state`, `sample_project`, `make_handoff`).
  Local fixtures go in YOUR test file, never conftest.
- No hollow tests: assert observable artifacts (files written, events
  appended, exit codes, rendered content), not call bookkeeping. Every
  bound/negative case in your handoff's oracles gets a test that VIOLATES
  it and asserts the outcome.
- **Determinism — hardware speed must NEVER decide a test's verdict** (L20; the
  full anti-pattern list is `reference/AUTHORING.md` §3b). This supersedes the
  old "no sleeps>2s" wording, which licensed exactly the defect it meant to
  prevent — a 2s budget is still a budget, and a slower machine still fails it.
  - FORBIDDEN: `time.sleep(N)` then assert; `deadline = monotonic() + N` then
    assert; asserting on elapsed time or on iterations completed. If shrinking
    the number could flip the result, it is an oracle and it is wrong.
  - REQUIRED: wait on a real synchronization point (`join()`, an `Event` the
    code sets, draining a queue) — or better, delete the wait by extracting the
    pure per-iteration step and calling it directly from the main thread.
  - A timeout is legal ONLY as a failsafe against hanging the suite forever, and
    must be generous (60s, not 3s) so it can never be the deciding factor.
  - A test that fails on a slow/loaded machine is a TRUE red — a real race the
    slow host revealed. Fix the test; never widen a timeout or add CPU.
- Determinism, other axes: no network (except handoff-specified loopback
  servers); no `datetime.now()`/`time.time()` where an assertion depends on the
  value; never leave process-global state (logging config, env vars, module
  attributes) mutated at teardown — under xdist the damage lands in a sibling
  test, not yours.

## Deliverables (all four, or the package is incomplete)

1. Implementation in your owned files only.
2. Tests green under the gate command.
3. `handoff/reports/P<NN>-REPORT.md`: result (done|BLOCKED), per-oracle
   pass/fail table, files touched, gate output tail (verbatim), deviations
   or assumptions, suggestions for the reviewer (do NOT act on them).
4. Final message = short receipt: `result / oracles: n pass m fail /
   files: ... / notes`.

## Never

Commit or run any git write command (worktree creation inside tests via the
fixtures is fine); touch files you don't own; start long-lived daemons that
outlive your tests; call external networks or AI services; edit this file
or any handoff.
