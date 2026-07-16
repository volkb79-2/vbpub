# P15 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: `feat/topos-p15-snapshot-enrichment`
- Worktree: `/tmp/vbpub-topos-p15-snapshot-enrichment`
- Base commit: `4b5dba3`
- Package: `P15`
- Current objective: Enrich incident snapshots with fresh bounded metadata,
  improve inspect output, and document redaction/locations.

## Timeline

```text
2026-07-09 06:49 CEST
- Action: Created P15 worktree and inspected snapshot bundle, UI snapshot hotkey, docker/systemd helper APIs, and existing tests.
- Commands: git worktree add -b feat/topos-p15-snapshot-enrichment /tmp/vbpub-topos-p15-snapshot-enrichment main; sed/rg over snapshot, ui/app, dockerjoin, origin, tests.
- Files changed: topos/handoff/reports/P15-LOG.md
- Result: Existing bundle format already accepts systemctl/docker payloads; P15 can focus on fresh TUI metadata collection and inspect/report polish.
- Follow-up: Add enrichment helper, wire UI, expand tests/docs, validate.

2026-07-09 07:01 CEST
- Action: Added snapshot enrichment helper, wired TUI snapshot action to fresh metadata collection, improved inspect output, and added focused tests.
- Commands: apply_patch; /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_snapshot_bundle.py topos/tests/test_ui_app.py -q.
- Files changed: topos/src/topos/snapshot/enrich.py; topos/src/topos/snapshot/bundle.py; topos/src/topos/ui/app.py; topos/tests/test_snapshot_bundle.py; topos/tests/test_ui_app.py; topos/docs/OPERATIONS.md; topos/handoff/reports/P15-LOG.md
- Result: Focused snapshot/UI tests passed: 18 passed in 4.92s.
- Follow-up: Run full validation, write report, commit.

2026-07-09 07:07 CEST
- Action: Completed full validation.
- Commands: /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q; find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile; /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke; /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch.
- Files changed: topos/handoff/reports/P15-LOG.md
- Result: Full suite passed (89 tests), py_compile clean, replay smoke passed, fixture JSON smoke produced schema_version=1 entities=8 host_metrics=20.
- Follow-up: Write final report and commit branch.
```

## Decisions

- Decision: Use injectable snapshot metadata collectors.
  Reason: Tests should not depend on Docker/systemd availability, and missing
  live providers must degrade into provider status rather than failing snapshot
  creation.
  Impact: Production defaults call bounded `systemctl show`/`docker inspect`;
  tests pass fixture callables into `ToposApp`.

## Blockers

- None currently.

## Validation

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 89 passed in 14.96s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# no output

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=20
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [ ] Feature branch committed.
