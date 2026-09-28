# Standing package contracts — nyxloom implementation waves

Inherited by EVERY handoff in this directory. Read once, follow exactly.
Use TODAY'S ACTUAL DATE — the one your carve packet states, not a date copied
from this file. (Wrong dates are review-rejected. Until 2026-08-02 this line
hardcoded "2026-07-15" and instructed every handoff to use it, so it had been
mandating a wrong date for 18 days; a date pinned in an inherited contract is
wrong by construction.)

## Environment

- Work dir: the assigned task worktree under `/workspaces/vbpub/.worktrees/<branch>`.
  Do not make task edits in the shared `/workspaces/vbpub` main checkout.
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

## Protected files — require explicit package-scoped ownership

`tests/conftest.py`, `schemas/`, and
`src/nyxloom/{__init__,types,paths,storage,config,leases}.py`, plus every
file owned by another active package, are protected. `docs/` and
`pyproject.toml` are not globally protected; change them when they are needed
to complete the accepted task.

`scope.touch` is the planned file inventory, not an exclusive edit allowlist.
An agent may change an additional file when it is directly needed to complete
the accepted task, and must record the path and reason in the task log or
report. This does not transfer ownership of files owned by another active
package, authorize unrelated work, or override the protected-file rule or an
explicit `scope.forbid`. A protected-file change or an explicit-forbid
override still requires user authorization and a bounded contract amendment
that names the exact path and explains why acceptance needs it. If that
authorization is absent, stop only the dependent work and record the specific
blocker. Your module's stub DOCSTRING is the normative interface: implement
beneath it, keep the docstring and all public signatures EXACTLY as written.

### Core-redesign wave exception (CR-00 through CR-16)

The operator-approved core-redesign program in
`reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md` grants
package-scoped exceptions to the protected-file rule only when that package's
explicit contract names the exact file. This is not general ownership:

- CR-01 may change the declared document/lint surfaces it audits.
- CR-03 and CR-07 may change `types.py` and their explicitly named schemas.
- CR-04 may change `storage.py`, `storage_sqlite.py`, and their explicitly
  named schemas and command surfaces.
- Other CR packages may change a normally frozen file only when their written
  contract names the exact file and explains why the package acceptance cannot
  be met without it.

Files owned by another active package remain protected. An agent that
discovers a new protected-file need must request a bounded contract amendment;
it must not infer ownership from this exception. Existing live state and
nonterminal tasks must be preserved through backup plus versioned upcasting.
No CR package is authorized to delete or silently reset live state.

## Cross-package dependencies

Other packages are being implemented in parallel; their modules may still
raise NotImplementedError. Code against their frozen interfaces; in YOUR
tests, monkeypatch those functions with canned returns where your handoff
says so. Never import-and-hope; never reimplement another package's logic.

## Code and test rules

- Runtime and test dependencies must be declared in `pyproject.toml`; optional
  UI libraries belong in an explicit extra. Do not install undeclared
  dependencies manually. Type hints on public functions. No dead code, no
  scaffolding, ASCII only.
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

1. Implementation in files owned by this task. `scope.touch` is the planned
   inventory rather than an exclusive edit allowlist; necessary additions are
   allowed when the accepted task requires them and must be recorded. The
   protected-file and other-package ownership rules above still apply.
2. Tests green under the gate command.
3. `handoff/reports/P<NN>-REPORT.md`: result (done|BLOCKED), per-oracle
   pass/fail table, files touched, gate output tail (verbatim), deviations
   or assumptions, suggestions for the reviewer (do NOT act on them).
4. Final message = short receipt: `result / oracles: n pass m fail /
   files: ... / notes`.

## Never

Use Git writes only from the assigned task worktree and its feature branch.
Commits there are allowed and expected when the declared gate requires a clean,
committed tree for changed-line coverage. Do not stage, commit, reset, rebase,
or amend from the shared main checkout. Keep the gate's `--worktree` target on
the assigned task worktree. Also never edit protected files or another active
package's files without explicit bounded authorization; start long-lived
daemons that outlive your tests; call external networks or AI services; or edit
this file or any handoff as an implementation agent.
