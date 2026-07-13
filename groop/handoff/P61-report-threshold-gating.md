# P61 - Steady-State Report Threshold Gating

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P54 (merged)
> **Base:** main after P54 merge
> **Session-hint:** fresh (report area; P54 implementer session long past cache TTL)
> **Serialize-with:** none
> **Escalate-if:** a named contract cannot be met as specified; threshold evaluation requires changing `compute_profile`'s output shape rather than consuming it

## Goal

Add threshold gating to `groop report`: let an operator assert bounds on the
already-computed steady-state profile figures and get a non-zero exit when a
bound is breached, so `groop report` can run as a pass/fail gate in the
gstammtisch stack measurement program and in CI-style acceptance scripts. This
is the "alerting/threshold gating on the computed profile" consumer that P54
explicitly deferred (P54 Out Of Scope, bullet 4).

## Workflow

- Branch: `feat/groop-p61-report-threshold-gating`
- Worktree: `.worktrees/groop-p61-report-threshold-gating`
- Touch only `groop/**`; write P61-LOG.md/P61-REPORT.md; commit, do not merge.

## Requirements

- Add repeatable `--assert GROUP:METRIC:STAT<=VALUE` (and `>=`) options to the
  existing `groop report` subcommand (`parse_report_args`/`_main_report` in
  `cli.py`). `GROUP` matches a profile `key` exactly (entity key or slice key
  per the active `--group-by`); `METRIC` is a gauge or rate name present in the
  report; `STAT` is one of `p50|p95|max`. Multiple `--assert` flags are ANDed.
- Evaluate assertions against the **already-computed** `GroupProfile` list from
  `compute_profile` — do NOT recompute or re-read frames, and do NOT change the
  profile computation. Threshold logic lives in a new pure helper in
  `report.py` (e.g. `evaluate_assertions(profiles, assertions) -> list[AssertionResult]`)
  that the CLI consumes; keep it independently unit-testable without argparse.
- Exit codes: `0` when all assertions pass (or none given); `1` when at least
  one assertion is breached (a genuine gate failure, distinct from usage
  errors); `2` for malformed `--assert` specs, unknown STAT, or the existing
  usage errors (missing `--json`, bad `--window`/`--group-by`, unreadable file).
  Note this introduces a new meaningful exit code `1` — document it and keep
  `2` reserved strictly for usage errors.
- A referenced GROUP/METRIC that is absent from the report (e.g. filtered
  recording, degenerate window) is a **breach** reported with a clear
  "not present in report" reason and exit 1 — NOT a silent pass and NOT a
  usage error. A metric present but with a `null` STAT (single-frame rate) is
  likewise a breach with a distinct reason.
- Assertion outcomes must appear in the `--json` output under a deterministic
  top-level key (e.g. `"assertions": [{group, metric, stat, op, threshold,
  actual, passed, reason}]`, sorted), preserving the existing byte-determinism
  contract (sorted keys, 6-dp float rounding). Emitting the report JSON is
  unconditional; assertions only change the exit code and add the block.
- Tests (fixture-recording based, asserting observable outcomes):
  a passing bound (exit 0), a breached `<=` bound (exit 1 + the breaching
  actual value in JSON), a breached `>=` bound, an absent-group breach, an
  absent-metric breach, a null-STAT breach, a malformed `--assert` (exit 2),
  an unknown STAT (exit 2), multiple asserts where one fails (exit 1), and
  byte-determinism of the assertions block across two runs. At least one test
  must assert the exact exit code via a real subprocess, not just the helper
  return value.
- Update `README.md` (CLI docs / the existing `groop report` paragraph) and
  `docs/OPERATIONS.md` with a threshold-gating example; note in `report.py`'s
  module docstring that assertions consume, never recompute, the profile.

## Out Of Scope

- Steady-state window auto-detection (that is P62).
- Per-sample or time-series alerting, hysteresis, or multi-run trend
  comparison — this is a single-report pass/fail gate only.
- Changing `compute_profile`, the gauge set, the percentile method, or the
  recording/reader path.
- Human-readable/non-JSON rendering of assertion results beyond the exit code
  and the JSON block.

## Gates

- Run focused tests (`groop/tests/test_report.py`) and the full suite with
  `-W error`; wrap the full-suite command in `timeout`; state in the REPORT
  which environment each result came from (the report area needs no `zstandard`;
  note the 2 known zstandard skips if present).
