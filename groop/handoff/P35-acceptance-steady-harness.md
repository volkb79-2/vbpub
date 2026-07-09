# P35 - Acceptance Steady Harness

## Goal

Extend the P33 rootless acceptance module with a repeatable steady-state
collector run that records CPU/RSS evidence over multiple samples.

This improves release evidence for `TUI-SPEC.md` §9 items 1 and 2 without
claiming to replace the final live 5-minute Textual acceptance run. It should be
safe to run rootless on a real host and fast under fixtures.

## Workflow

Follow `groop/README.md` "Workflow protocol" exactly.

- Branch: `feat/groop-p35-acceptance-steady`
- Worktree: `.worktrees/-groop-p35-acceptance-steady`
- Branch from local `main`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P35-LOG.md` updated while working
- Finish with `groop/handoff/reports/P35-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `groop/README.md`
- `groop/CONTRACTS.md`
- `groop/TUI-SPEC.md` §9
- `groop/MEASUREMENTS.md`
- `groop/docs/OPERATIONS.md`
- `groop/docs/STATUS.md`
- `groop/src/groop/acceptance.py`
- `groop/tests/test_acceptance.py`

## Functional Requirements

Add a `steady` subcommand:

```bash
python -m groop.acceptance steady \
  [--cgroup-root PATH] \
  [--samples N] \
  [--interval-s SECONDS] \
  [--max-cpu-pct FLOAT] \
  [--max-rss-kb INT] \
  [--json] [--pretty-json]
```

Defaults:

- `--samples 60`
- `--interval-s 5.0`
- no default CPU/RSS pass threshold unless the user provides one

Behavior:

- Rootless/read-only collector loop using `Collector(cgroup_root=...)`.
- No Textual import requirement.
- No subprocess execution.
- No host mutation.
- Sleep between samples using `time.sleep()`, but make tests able to inject or
  monkeypatch sleep/time so they do not wait.
- Measure:
  - wall time;
  - user CPU;
  - sys CPU;
  - max RSS;
  - sample count attempted/completed;
  - entity-count min/max/last;
  - average collector wall time per sample;
  - approximate CPU percent of one core over wall time.
- Threshold checks:
  - if `--max-cpu-pct` is provided, mark failure when measured CPU percent is
    above it;
  - if `--max-rss-kb` is provided, mark failure when max RSS is above it.
- Exit codes:
  - `0` when collection completes and all provided thresholds pass;
  - `1` for collection/threshold failures;
  - `2` for usage/validation errors.

Output:

- JSON deterministic and similar in spirit to P33 smoke.
- Text paste-friendly for `MEASUREMENTS.md`.
- Explicitly label this as collector steady-state evidence, not full TUI
  steady-state acceptance.

Implementation guidance:

- Keep the module import-light.
- Prefer small helper dataclasses rather than giant dict assembly.
- Preserve the existing `smoke` command behavior.
- Use `resource.getrusage(resource.RUSAGE_SELF)` as P33 does.

## Tests

Add focused tests covering:

- steady JSON with fixture cgroup root and small `--samples 2 --interval-s 0`;
- steady text output;
- `--pretty-json` parseable and sorted;
- CPU threshold failure returns exit 1 with a controlled result;
- RSS threshold failure returns exit 1 with a controlled result;
- invalid samples/interval values return exit 2;
- no Textual import in a clean subprocess;
- existing smoke tests still pass.

Keep tests fast. Do not sleep for real in tests.

## Documentation

Update:

- `groop/MEASUREMENTS.md` with the steady command as the preferred rootless
  collector evidence path before the final live TUI run.
- `groop/docs/OPERATIONS.md` with a short command example.
- `groop/docs/STATUS.md` acceptance notes if appropriate.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- Full interactive Textual TUI CPU/RSS benchmark.
- DAMON/BPF privileged measurement gates.
- Packaging build/install verification.
- Any daemon status checks.
