# R0 structured-report tiebreak — the design, and what it deliberately does not do

Author: Claude (operator-directed session), 2026-09-08.
Anchor: main `33c9fc59`. Supersedes nothing; A-073, `Outcome` and
`EXIT_CODES` are untouched by this document (SR-0 explains why that is a
finding, not a scoping choice made to be conservative).

Evidence base: run-gate-project `KNOWN_ISSUES_TODO_BACKLOG.md` RG-45
(filed 2026-09-08, 5 live reproductions against dstdns P176's
`frontend-unit`/`ui_unit` lanes — every one showing all real tests green
with a non-zero exit from vitest's own internal RPC heartbeat, independent
of contention level by the 5th repro); this session's own reading of
`src/assay/errors.py` and `src/assay/runner.py:execute_command` (2026-09-08).
Nothing here rests on a capability nobody has specified.

---

## SR-0 — What RG-45 actually is, and what it is not

RG-45's own filing offered three candidate directions (a retry heuristic, a
cross-container CPU-quota coordination primitive, or accepting the
flakiness as operator policy) and explicitly declined to prescribe one. The
operator's own first framing of a fix — "assay's default return value
becomes non-zero, forcing verdict reads, reverse A-073" — turned out, on
reading the actual code, to target a layer that is **already correct**:

`src/assay/errors.py`'s `Outcome`/`EXIT_CODES` is already a closed, frozen,
six-value vocabulary (`PASS=0, FAIL=1, ERROR=2, NO_MEASUREMENT=3,
BUDGET_EXCEEDED=4, INCONCLUSIVE=5`), with the module's own docstring
stating the design intent outright: *"the exit code IS the verdict: a
consumer that checks only the exit code must never be wrong."* `EXIT_CODES`
is frozen by A-021 precisely because every consumer's gate depends on the
specific integers. Flipping `PASS` to a non-zero value would not close
RG-45 — the verdict and assay's own exit code already agree by
construction; there is no drift at that layer to fix, and breaking the
frozen mapping would re-point every existing consumer's gate for a
mechanism that isn't the defect.

**The real defect is one layer inside that**, in `execute_command`
(`runner.py:1140-1142`, A-073): R0 decides whether the **wrapped target
command** (pytest, vitest, `go test`, ...) is a PASS or FAIL by reading
*only* that process's raw exit code. When vitest's own internal
worker↔orchestrator RPC heartbeat (hardcoded 60s timeout, no config path in
vitest 3.2.7, traced in RG-45's own filing) isn't acknowledged in time under
host-wide contention, vitest throws this as an unhandled error and sets
`process.exitCode = 1` — **regardless of whether every actual test
passed.** R0 then correctly reports what it observed (a non-zero exit) as
`FAIL`/`COMMAND_FAILED`; the observation itself is the thing that's wrong,
not R0's response to it.

This document is scoped to that one layer: **how R0 decides FAIL for the
wrapped target**, opt-in, per lane. It does not touch `Outcome`,
`EXIT_CODES`, or A-073's default behavior for a lane that does not opt in.

## SR-1 — The tiebreak is opt-in per lane, never assay's default

A-073's exit-code rule stays the default for every lane that does not
declare a result report. This is not a hedge; it is the safety property
the whole design rests on. A-073 exists because "an ordinary non-zero exit
is a judged R0 FAIL, never a universal PASS" closes an entire class of
silently-waved-away failures — reversing that as assay's *default*
behavior would reopen exactly the hazard it was written to prevent. The
fix is additive: a lane that wants a richer, verified signal declares
where to find it; a lane that doesn't is bound by today's rule, unchanged,
forever.

## SR-2 — What a lane declares, and what "verified complete" means

A lane opts in with a new table naming the report's path and its known
format:

```toml
[lanes.<name>.result_report]
format = "vitest-json"          # closed vocabulary, SR-4
path = ".assay/vitest-report.json"   # relative to the lane's cwd
```

(Exact key names are a review question, not a design decision — the shape
above is illustrative. `result_report` under the lane table mirrors the
nesting `[lanes.<name>.pins.assay]` already uses elsewhere in this
estate's TOML conventions.)

R0, after the wrapped command exits, checks for this table. If absent, or
if the declared file does not exist, or exists but fails to parse as the
declared format: **nothing changes — A-073 governs, on the exit code
alone.** A report is only ever evidence *for* PASS; its absence is never
evidence *against* one, because "the file wasn't written" is exactly the
signature of a genuine crash before results were ever finalized (a hard
kill, an OOM-kill, a segfault) — the case A-073 must keep failing loudly.

**"Verified complete"** is the bar a *present* report must clear before it
can participate in the FAIL determination at all:

- it parses as valid, well-formed JSON (or the format's native shape —
  `go test -json` is NDJSON, SR-4c);
- it carries the format's own "this run finished" marker (vitest:
  `success` field present at the top level; `pytest-json-report`:
  `exitcode` and `summary` both present; `go test -json`: a terminal
  per-package `Action: "pass"|"fail"` event for every package that was
  invoked) — a truncated write (process killed mid-flush) fails this
  check and falls back to A-073, never silently passes;
- it reports a total test count `> 0` — an empty report (zero tests
  collected) is `NO_MEASUREMENT` territory, not something this tiebreak
  should ever wave through as a PASS.

Only when a report clears every one of these does its own pass/fail count
(not the process's exit code) become R0's determination:

- report verified-complete, zero reported failures → `PASS`, regardless of
  what the wrapped process's exit code was;
- report verified-complete, one or more reported failures → `FAIL`,
  regardless of what the exit code was (this direction was already true —
  a report naming real failures should never be overridden by a
  coincidentally-zero exit code either; reading the report at all means
  reading it as the actual ground truth, not a one-directional escape
  hatch);
- report absent/unparseable/incomplete → today's A-073 rule, exit code
  alone, unchanged.

## SR-3 — The residual risk, named rather than hidden

A verified-complete report proves the test framework's own reporter hook
ran to completion and wrote a coherent summary. It does not prove nothing
went wrong in the narrow window *after* that write and before the
process's own exit — a corrupted coverage artifact, a crash during
teardown that doesn't touch the already-written report. This design
accepts that residual risk rather than closing it with a language-specific
stderr-signature allowlist (e.g., matching vitest's exact
`[vitest-worker]: Timeout calling` text): a signature allowlist doesn't
generalize to pytest or Go, each of which would need its own, and every
allowlist entry is a new thing to keep in sync with an upstream tool's
wording across versions — exactly the brittleness RG-44 (a case-sensitivity
mismatch in run-gate's own `GONE_SIGNALS` string match) already
demonstrated live, one layer over, this same day. In practice this window
is narrow: a reporter's finish hook is close to the end of a process's
life in every one of these frameworks, and a genuine hard crash (SIGSEGV,
OOM-kill) overwhelmingly prevents the report from being written at all,
which the completeness check already catches.

**Named v2 trigger:** if a real incident is ever traced to something going
wrong strictly inside that post-report window (proven, not suspected),
that is the evidence a stderr-signature allowlist — or a stricter
completeness bar — would need. Not built speculatively.

## SR-4 — Per-language report source, and why each was picked

Doctrine already in this codebase: `assay/adapters/go_stmtpos.py`'s own
comment states it outright — **"A-217: adapt, do not invent."** Assay runs
the real toolchain's own instrumenter for coverage attribution rather than
guessing from source; the same principle applies here — read each
framework's own maintained, structured report, never hand-parse its
stdout/stderr prose.

**a. vitest — `vitest-json`.** Native: `--reporter=json
--outputFile=<path>`, no plugin, no new dependency for the consuming
project. Produces a Jest-compatible shape (`numTotalTests`,
`numPassedTests`, `numFailedTests`, `success`, `testResults`). This is
RG-45's own confirmed live reproduction and ships first (SR-5).

**b. pytest — `pytest-json-report`.** Operator's own call: use the
plugin, do less ourselves, rather than a hand-rolled conftest emitter.
Pytest has no built-in structured-report flag; the plugin is the
consuming project's own added dependency (its `requirements`/`pyproject`,
never assay's), and assay adds a reader for its documented output shape
(`summary.total`, `summary.passed`, `summary.failed`, `exitcode`).

**c. `go test -json` — `go-test-json`.** Native, stdlib, no plugin.
Streamed NDJSON rather than one JSON blob, which changes what
"well-formed" and "complete" mean (SR-2's bullets need a per-package
terminal-event check, not a single top-level field) — flagged here as
needing its own careful spec at the Go checkpoint (SR-5c), not fully
specified in this document.

## SR-5 — Sequencing, for the carve that follows

Three checkpoints, each closing with its own adversarial review given how
close this sits to R0 — the tier every other rigor level is built on —
even though no line of `Outcome`/`EXIT_CODES` moves:

1. **The opt-in framework + vitest.** The `result_report` lane
   declaration, the completeness-verification machinery (format-agnostic
   core + a `vitest-json` reader), the R0 wiring in `execute_command`, and
   fault-injection regression coverage: a report claiming success but
   truncated mid-write must still FAIL; a report the wrong shape must
   still FAIL; a report naming real failures must FAIL regardless of exit
   code; the RG-45 shape itself (report verified-complete + 0 failures +
   non-zero exit) must PASS. Highest priority — this is the one with a
   confirmed live reproduction.
2. **`pytest-json-report` reader.**
3. **`go test -json` reader**, including the NDJSON-specific completeness
   design SR-4c defers.

## SR-6 — What this design deliberately does not do

**No change to `Outcome`/`EXIT_CODES`.** Frozen by A-021; this design
found no defect there to justify reopening it.

**No change to A-073's default.** A lane that does not opt in is governed
exactly as it is today.

**No cross-container CPU-quota coordination primitive** (RG-45's candidate
direction 2). It would manage the SYMPTOM (contention exists) rather than
the actual defect (a test's correctness should never depend on an internal
heartbeat's punctuality), and is a large, ongoing, cross-repo mechanism for
something a report-based tiebreak eliminates outright, deterministically,
with no retry.

**No retry heuristic** (candidate direction 1). Non-deterministic by
construction — a retry can fail again for the identical reason. This
design replaces the unreliable exit-code proxy with the framework's own
report instead of hoping the proxy behaves better on a second attempt.

**No `run-gate.py` change.** This was the operator's own first assumption
("run-gate would need to learn to interpret the verdict correctly") and it
does not hold once the fix is scoped to R0's *input* rather than assay's
*output* contract: assay's own exit code for a report-verified-clean run
becomes correctly `PASS` (0) at the source, so `EXIT_CODES[PASS] == 0`
propagates through unchanged — run-gate keeps trusting assay's exit code
exactly as it does today, and that trust becomes correct rather than
needing new code on run-gate's side. (A separate, independently-motivated
idea — run-gate reading verdict.json content directly for richer reporting
— remains available but is not required to close RG-45, and is out of
scope here.)

**No dstdns-side argv change made here.** Getting dstdns's own
`frontend-unit`/`ui_unit` lanes to actually emit `--reporter=json
--outputFile=...` and declare `result_report` is a small config change on
dstdns's own `run-gate.toml`/`assay.toml` — mirrors the RG-34 precedent
exactly ("run-gate's half lands in vbpub; the consumer's own argv edit is
dstdns-side"). Tracked as dstdns's own follow-up once SR-5's checkpoint 1
ships, not built speculatively against an environment this repo doesn't
control.

**No new `ReasonCode`.** `COMMAND_FAILED` (R0's existing FAIL reason for
"the lane's declared command failed") continues to cover the case where a
lane's wrapped command genuinely fails, opted-in report or not — this
design changes WHEN that code fires (never for a verified-complete,
zero-failure report), not what it means. Whether the verdict should
additionally carry free-text provenance noting "exit code disagreed with a
verified-complete report" in its existing `detail` field (already used
elsewhere for this class of "which mechanism decided" note, e.g. R4's
`RED_FIRST_UNPROVEN`) is a checkpoint-1 review question — A-050 requires
stopping and asking before adding to a closed enum, and this document
deliberately proposes the free-text `detail` route specifically so that
question does not need to be asked at all.

## Migration surface, for the carve that follows

`src/assay/runner.py` (`execute_command`/`resolve_command_plan`, the R0
wiring), a new report-reader module — naming and location should probably
mirror `src/assay/adapters/`'s existing per-format shape rather than
inventing a second convention (a `src/assay/report_adapters/` or a
`format` dispatch inside `runner.py` itself is a checkpoint-1 design
question) — `assay.toml`'s schema/validation for the new
`[lanes.<name>.result_report]` table, `docs/DESIGN-GUIDE.md` §6 (where
`Outcome`/`ReasonCode` are documented — this addition belongs beside that
section even though it changes neither vocabulary), `docs/CONSUMERS.md`
(how a lane opts in — AGENTS.md's own README/DESIGN-GUIDE/CONSUMERS.md
rule applies here: a lane-declarable capability is incomplete without a
worked, paste-able example), and fault-injection test fixtures under
`tests/` for each of the three completeness-check failure shapes named in
SR-2. The three per-language readers are naturally three separate,
serially-carved packages (SR-5); the opt-in framework + completeness core
is the one piece every later checkpoint depends on and should carve first,
alone.
