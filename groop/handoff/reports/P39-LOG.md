# P39 Work Log

## Context

- Branch: feat/groop-p39-release-readiness-ledger
- Worktree: .worktrees/-groop-p39-release-readiness-ledger
- Base commit: b4aa80c (docs(groop): record P38 merge and prep P39)
- Package: P39 — Release readiness ledger
- Current objective: Create canonical release-readiness document, update framework docs, write reports

## Timeline

```text
2026-07-10 START
- Action: Created worktree and branch from local main.
- Commands: (pre-existing worktree)
- Files changed: (none yet)
- Result: Worktree ready at .worktrees/-groop-p39-release-readiness-ledger
- Follow-up: Read all required context and implement P39.
```

```text
2026-07-10 continued
- Action: Read all required context documents and reports.
- Files read: TUI-SPEC.md §9, STATUS.md, ROADMAP.md, OPERATIONS.md, MEASUREMENTS.md,
  P33/P35/P36/P37/P38 reports, AGENT-LOG-TEMPLATE.md
- Result: Understood the acceptance map, existing evidence, and gaps.
- Action: Created groop/docs/RELEASE-READINESS.md
- Files changed: groop/docs/RELEASE-READINESS.md
- Result: Canonical release-readiness document created with: release-cut scope,
  §9 acceptance map table, rootless automated check commands, live-host evidence
  templates, release blocker checklist, and explicit non-claims.
- Action: Updated framework documents.
- Files changed: groop/README.md, groop/docs/OPERATIONS.md, groop/docs/STATUS.md,
  groop/docs/ROADMAP.md
- Result: All framework docs aligned with P39 completion.
- Action: Ran validation commands.
- Commands: python3 -m pytest groop/tests -q -> 367 passed (15 pre-existing
  Textual-pilot failures), python3 -m pytest groop/tests/test_acceptance.py -q
  -> 40 passed, acceptance smoke/steady/tui-smoke all passed, py_compile clean.
- Result: All validations pass. No Python files were changed by P39.
- Action: Wrote P39-LOG.md and P39-REPORT.md.
- Files changed: groop/handoff/reports/P39-LOG.md, groop/handoff/reports/P39-REPORT.md
- Follow-up: Commit and hand off for review.
```

## Decisions

- Decision: RELEASE-READINESS.md points to MEASUREMENTS.md for historical evidence
  instead of duplicating it, as specified in the handoff.
  Reason: The handoff explicitly states "Keep MEASUREMENTS.md as the evidence
  ledger, not the new document."
  Impact: Operators must consult both documents for a complete picture.
- Decision: STATUS.md v1 summary changed "final release documentation" to
  "P39 adds the canonical release-readiness document" rather than removing the
  gap entirely, because the 5-minute live Textual TUI and live-root DAMON gaps
  remain.
  Reason: P39 closes the documentation gap but not the manual evidence gaps.
  Impact: Accurate representation of remaining release work.
- Decision: ROADMAP.md remaining estimate changed from 1 to 0 for v1/v1.5
  release confidence packages.
  Reason: P39 is the last planned v1/v1.5 package.
  Impact: Future planning should focus on manual evidence capture.

## Blockers

None. P39 is documentation-only and all context was available.

## Validation

```bash
# Full suite
$ python3 -m pytest groop/tests -q --tb=short
# 367 passed, 15 failed (pre-existing Textual-pilot failures)
```

```bash
# Focused acceptance tests
$ python3 -m pytest groop/tests/test_acceptance.py -q --tb=short
# 40 passed in 8.04s
```

```bash
# P33 smoke harness
$ PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
# {"ok": true, "checks": [{"name": "collect", "ok": true}, ...]}
# 8 entities, 572 source labels
```

```bash
# P35 steady harness
$ PYTHONPATH=groop/src python3 -m groop.acceptance steady \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --samples 2 --interval-s 0 --json
# {"ok": true, "samples_completed": 2, "cpu_pct": 26.99, "rss_kb": 23196}
```

```bash
# P38 tui-smoke harness
$ PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
# {"ok": true, "frames": 1, "view": "tree", "profile": "auto", "rss_kb": 48436}
```

```bash
# py_compile
$ python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit=0
```

## Handoff Checklist

- [x] Report file written (P39-REPORT.md).
- [x] Log file current (P39-LOG.md).
- [x] Tests/compile/smoke recorded (all pass).
- [x] Known gaps documented (live 5-minute TUI CPU/RSS, live DAMON acceptance).
- [x] Feature branch committed.
