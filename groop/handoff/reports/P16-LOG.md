# P16 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: `feat/groop-p16-daemon-spike`
- Worktree: `/tmp/vbpub-groop-p16-daemon-spike`
- Base commit: `3e7c895`
- Package: `P16`
- Current objective: Add a narrow read-only Unix-socket daemon spike, tests, and
  architecture docs.

## Timeline

```text
2026-07-09 07:47 CEST
- Action: Created P16 worktree and inspected daemon handoff, CLI, live stream, and architecture docs.
- Commands: git worktree add -b feat/groop-p16-daemon-spike /tmp/vbpub-groop-p16-daemon-spike main; sed over P16 handoff, cli.py, record/live.py, docs/ARCHITECTURE.md.
- Files changed: groop/handoff/reports/P16-LOG.md
- Result: Chosen spike shape is a stdlib Unix-socket JSONL read broker with current/stream requests and no mutation verbs.
- Follow-up: Implement broker, CLI serve command, docs, and socket tests.

2026-07-09 08:02 CEST
- Action: Implemented daemon broker spike, CLI serve command, socket tests, and architecture/threat-model docs.
- Commands: apply_patch; /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests/test_daemon_broker.py -q; find groop/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-groop-p13-venv/bin/python -m py_compile.
- Files changed: groop/src/groop/daemon/*; groop/src/groop/cli.py; groop/docs/ARCHITECTURE.md; groop/docs/DAEMON.md; groop/tests/test_daemon_broker.py; groop/handoff/reports/P16-LOG.md
- Result: Focused daemon tests passed (3 passed); py_compile clean.
- Follow-up: Run full suite and smoke validation.

2026-07-09 08:07 CEST
- Action: Completed full validation.
- Commands: /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q; PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke; PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch.
- Files changed: groop/handoff/reports/P16-LOG.md
- Result: Full suite passed (96 tests), replay smoke passed, fixture JSON smoke produced schema_version=1 entities=8 host_metrics=36.
- Follow-up: Write report and commit branch.
```

## Decisions

- Decision: Implement a minimal JSON-lines protocol instead of a full RPC layer.
  Reason: The spike needs a reviewable daemon boundary without committing to a
  long-term transport framework.
  Impact: P20 attach mode can consume the same frame JSON shape or replace the
  wire layer later without changing the frame model.

## Blockers

- None currently.

## Validation

```bash
# /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 96 passed in 15.44s

# find groop/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-groop-p13-venv/bin/python -m py_compile
# no output

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=36
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [ ] Feature branch committed.
