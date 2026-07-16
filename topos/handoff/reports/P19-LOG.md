# P19 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: `feat/topos-p19-zram-swap-backends`
- Worktree: `/tmp/vbpub-topos-p19-zram-swap-backends`
- Base commit: `1557632`
- Package: `P19`
- Current objective: Add read-only ZRAM and swap-backend awareness with
  host-level metrics, banner wording, fixtures/tests, and reports.

## Timeline

```text
2026-07-09 07:15 CEST
- Action: Created P19 worktree and inspected host collection, registry, banner, tests, and compressed-swap docs.
- Commands: git worktree add -b feat/topos-p19-zram-swap-backends /tmp/vbpub-topos-p19-zram-swap-backends main; sed/rg over host.py, registry.py, banner.py, tests, P19 handoff, docs.
- Files changed: topos/handoff/reports/P19-LOG.md
- Result: Implementation can be additive: classify host backend, add host ZRAM totals, preserve cgroup fields.
- Follow-up: Patch host collector/registry/banner/tests.

2026-07-09 07:31 CEST
- Action: Added host swap backend classification, zram sysfs parsing, registry entries, backend-aware banner line, synthetic host tests, and docs updates.
- Commands: apply_patch; /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_host_swap.py topos/tests/test_ui_banner.py topos/tests/test_model_registry.py -q.
- Files changed: topos/src/topos/collect/host.py; topos/src/topos/registry.py; topos/src/topos/ui/banner.py; topos/tests/test_host_swap.py; topos/tests/test_ui_banner.py; topos/docs/COMPRESSED-SWAP.md; topos/docs/OPERATIONS.md; topos/handoff/reports/P19-LOG.md
- Result: Focused tests passed: 9 passed in 0.07s.
- Follow-up: Run full validation, write report, commit.

2026-07-09 07:38 CEST
- Action: Completed full validation. Re-ran smoke commands with `PYTHONPATH=topos/src` after noticing the venv executable alone imported an earlier editable install.
- Commands: /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q; find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile; PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke; PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch.
- Files changed: topos/handoff/reports/P19-LOG.md
- Result: Full suite passed (93 tests), py_compile clean, replay smoke passed, fixture JSON smoke produced schema_version=1 entities=8 host_metrics=36 backend=[5, 'host'].
- Follow-up: Write final report and commit.
```

## Decisions

- Decision: Keep `swap_disk` and `host_disk_swap` names for compatibility but
  make new UI wording backend-aware.
  Reason: A full metric rename would be broad churn; P19 can fix user-facing
  interpretation first.
  Impact: ZRAM-only hosts show disk swap as zero and the banner labels backend
  state explicitly.

## Blockers

- None currently.

## Validation

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 93 passed in 14.59s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# no output

# PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto

# PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=36 backend=[5, 'host']
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [ ] Feature branch committed.
