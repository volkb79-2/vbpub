# P62 - Steady-State Window Auto-Detection

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** terra-med
> **Depends-on:** P54 (merged)
> **Base:** main after P54 merge
> **Session-hint:** fresh (report area)
> **Serialize-with:** P61   <!-- both edit cli.py parse_report_args and report.py; must not run concurrently -->
> **Escalate-if:** a named contract cannot be met as specified; a deterministic, testable stability criterion cannot be defined without per-sample data the P2 frame does not carry

## Goal

Add optional automatic steady-state window detection to `topos report`: instead
of the operator manually choosing `--window last:Ns`, `--window auto` finds the
longest trailing sub-window over which the key gauges are stable (within a
bounded relative spread) and profiles that window. This is the "steady-state
window auto-detection" consumer P54 explicitly deferred as future work (P54 Out
Of Scope, bullet 1). It is `terra-med` because the stability criterion is a
genuine design decision that must be pinned to a deterministic, testable oracle
(the P51-benchmark lesson: contract-tighten before escalating; a fuzzy
"looks stabilized" heuristic is the failure mode).

## Workflow

- Branch: `feat/topos-p62-report-steady-state-autodetect`
- Worktree: `.worktrees/topos-p62-report-steady-state-autodetect`
- Touch only `topos/**`; write P62-LOG.md/P62-REPORT.md; commit, do not merge.

## Requirements

- Extend `--window` to accept `auto` (in addition to the existing `all` and
  `last:Ns`). `auto` selects a trailing window of frames by a pinned stability
  criterion, then feeds exactly those frames to the existing `compute_profile`
  (do NOT change percentile/rate math). Detection is a new pure helper in
  `report.py` (e.g. `detect_steady_window(frames, *, stability_gauge, ...) ->
  WindowRange | None`), independently unit-testable.
- **Pin the stability criterion deterministically** (this is the core
  contract, not an implementation detail): over a candidate trailing window,
  compute the coefficient of variation (population stddev / mean) of a named
  primary stability gauge (default `ram`; overridable via `--stability-gauge
  METRIC`) across the window's per-frame values for the busiest entity, and
  accept the window if CoV <= a fixed threshold (default `0.05`, overridable
  via `--stability-cov FLOAT`). Choose the **longest** trailing window (scan
  from the last frame backward, growing the window, and take the largest window
  that still satisfies the bound, requiring a minimum of `--min-frames`
  frames, default 3). Ties/edge rules must be fully specified in the LOG.
- Add at least one oracle test whose frame set makes auto and `all` select
  **different** windows and asserts the exact frames chosen (a test that
  detects the wrong window boundary, not just a plausible sample count) — the
  P54-style "oracle that detects the wrong mechanism" requirement.
- Degenerate handling (no raise, consistent with P54): if no trailing window
  of `--min-frames` satisfies the bound, `auto` falls back to `all` and the
  JSON records `"window_mode":"auto","window_detected":false`; when a window
  is found, record `"window_mode":"auto","window_detected":true` plus the
  detected bounds. A single-frame or empty recording yields the existing
  empty/degenerate behavior.
- Preserve the byte-determinism contract (sorted keys, 6-dp rounding) and the
  existing exit-code contract (2 for malformed `--stability-cov`/`--stability-gauge`
  values or a bad `--window`; 0 otherwise). `auto` combined with P61's
  `--assert` (if merged) must evaluate assertions against the detected window.
- Tests (fixture-recording based, observable): the oracle above; a stable
  recording where auto == a known trailing window; a noisy recording where
  auto falls back to `all` (`window_detected:false`); `--stability-gauge` and
  `--stability-cov` overrides changing the selected window; malformed override
  values exit 2; determinism across two runs.
- Update `README.md` and `docs/OPERATIONS.md` with an `--window auto` example
  and a one-line statement of the pinned CoV criterion and its defaults.

## Out Of Scope

- Per-entity independent window detection (one detected window applies to the
  whole report in v1).
- Multi-gauge composite stability scoring, change-point detection, or ML
  methods — the single-gauge CoV criterion is deliberately simple and pinned.
- Threshold gating / assertions (that is P61).
- Any change to `compute_profile`, the reader, or the recording format.

## Gates

- Run focused (`topos/tests/test_report.py`) and full suite with `-W error`,
  full suite wrapped in `timeout`; state the environment for each in the
  REPORT.
