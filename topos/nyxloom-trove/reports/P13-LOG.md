# P13 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: `feat/topos-p13-ui-navigation`
- Worktree: `/tmp/vbpub-topos-p13-ui-navigation`
- Base commit: `40e851568e388f8318af3cabf531bbbcd664b25b`
- Package: `P13`
- Current objective: Implement UI navigation polish, replay controls/status, and explicit disabled v2 action UX without changing v1/v1.5 scope.

## Timeline

Append newest entries at the bottom.

```text
2026-07-09 05:05 CEST
- Action: Created the required worktree and read workflow, contracts, TUI spec, P13 handoff, and log template.
- Commands: git worktree add -b feat/topos-p13-ui-navigation /tmp/vbpub-topos-p13-ui-navigation main; sed -n on README/CONTRACTS/TUI-SPEC/P13 handoff/log template; rg over topos/src/topos/ui, topos/src/topos/record, topos/tests, topos/docs/OPERATIONS.md.
- Files changed: topos/handoff/reports/P13-LOG.md
- Result: Confirmed scope is limited to UI tree navigation, replay controls/status, reserved action messaging, profile polish, tests, and operations docs.
- Follow-up: Patch app/tree/table/cli paths, then add focused UI and table tests.

2026-07-09 05:31 CEST
- Action: Moved the in-progress P13 diff from the main checkout into the required /tmp worktree and restored the main checkout.
- Commands: git diff > /tmp/vbpub-topos-p13.patch; git apply /tmp/vbpub-topos-p13.patch; git restore on main checkout targets.
- Files changed: topos/docs/OPERATIONS.md; topos/src/topos/cli.py; topos/src/topos/record/replay.py; topos/src/topos/ui/app.py; topos/src/topos/ui/keys.py; topos/src/topos/ui/table.py; topos/src/topos/ui/tree.py; topos/tests/test_ui_app.py; topos/tests/test_ui_table.py; topos/handoff/reports/P13-LOG.md
- Result: Worktree now carries the intended edits and the main checkout is clean again.
- Follow-up: Run focused UI/table tests, fix any API mismatches, then run the full required validation set.

2026-07-09 05:33 CEST
- Action: Prepared isolated test environment because system pytest is unavailable.
- Commands: python3 -m venv /tmp/vbpub-topos-p13-venv; /tmp/vbpub-topos-p13-venv/bin/pip install -e topos pytest
- Files changed: topos/handoff/reports/P13-LOG.md
- Result: Using /tmp/vbpub-topos-p13-venv for required pytest and smoke validation.
- Follow-up: Run focused UI tests inside the venv, then full suite and smoke commands.

2026-07-09 05:40 CEST
- Action: Completed focused UI/table validation after adjusting replay key names to Textual's key tokens.
- Commands: /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_ui_table.py topos/tests/test_ui_app.py -q
- Files changed: topos/src/topos/ui/keys.py; topos/tests/test_ui_app.py; topos/handoff/reports/P13-LOG.md
- Result: Focused UI/table coverage passed (11 tests).
- Follow-up: Run full pytest, py_compile, fixture --once --json smoke, and replay UI smoke.

2026-07-09 05:43 CEST
- Action: Completed required validation for the worktree build.
- Commands: /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q; find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile; /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch; /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
- Files changed: topos/handoff/reports/P13-LOG.md
- Result: Full suite passed (84 tests), py_compile was clean, fixture once-json produced schema_version=1 entities=8 host_metrics=20, replay UI smoke passed.
- Follow-up: Write final report, commit on feat/topos-p13-ui-navigation, and hand off branch/hash/evidence.
```

## Decisions

- Decision: Reuse the existing `ToposApp` and `ReplayDriver` instead of introducing a parallel replay UI path.
  Reason: The contracts and spec require replay to route through the same model/UI surfaces as live mode.
  Impact: Replay status and controls stay localized to `ui/app.py` and small `record/replay.py` helpers.

- Decision: Represent unsupported custom-profile columns as ignored metadata rather than changing config parsing.
  Reason: The handoff asked for graceful handling, but the contracts freeze the config interface.
  Impact: Config remains backward compatible while the UI surfaces ignored column names explicitly.

## Blockers

- Blocker: System `pytest` is unavailable in this environment.
  Tried: `python3 -m pytest topos/tests/test_ui_table.py topos/tests/test_ui_app.py -q`
  Needed: Isolated venv under `/tmp` with `pytest` and editable package install.

## Validation

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 84 passed in 11.98s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# (no output)

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=20

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.

2026-07-09 05:47 CEST
- Action: Wrote the final handoff report and committed the P13 branch state.
- Commands: git add ...; git commit -m "topos: polish tree navigation and replay UI"
- Files changed: topos/handoff/reports/P13-LOG.md; topos/handoff/reports/P13-REPORT.md
- Result: Branch is ready for controller review with validation evidence and scope notes recorded.
- Follow-up: Hand off branch name, commit hash, changed files, validation evidence, and any deviations.
