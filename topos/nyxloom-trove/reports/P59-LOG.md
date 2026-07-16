# P59 Work Log

## Context

- Branch: feat/topos-p59-container-entity-selector
- Worktree: .worktrees/topos-p59-container-entity-selector
- Base commit: main after P55 + P57 merge
- Package: P59
- Current objective: Wire --container as a third entity-selector on the collection path

## Timeline

```text
2026-07-13 UTC
- Action: Read handoff doc, README.md, cli.py, collector.py, dockerjoin.py, cgroup.py
- Files: topos/handoff/P59-container-entity-selector-composition.md, topos/README.md, topos/src/topos/cli.py, topos/src/topos/collect/collector.py, topos/src/topos/collect/dockerjoin.py
- Result: Understood existing codebase, P55/P57 patterns, and gstammtisch fixture

- Action: Batch 1 — Added --container flag to parse_args(), _filter_kwargs, rejection blocks, TODO update, ContainerResolveError handling
- Files changed: topos/src/topos/cli.py
- Result: Syntax OK, 914/914 existing tests pass

- Action: Batch 2 — Added container_selectors parameter to Collector.__init__, resolution logic in collect_once()
- Files changed: topos/src/topos/collect/collector.py
- Result: 914/914 existing tests pass

- Action: Batch 3 — Wrote test_p59_container_selector.py with 9 tests (8 required + 1 split)
- Files changed: topos/tests/test_p59_container_selector.py
- Result: 9/9 new tests pass, 923/923 full suite (2 skipped, 1 warning)

- Action: Updated README.md, STATUS.md, ROADMAP.md, CONTRACTS.md
- Files changed: topos/README.md, topos/docs/STATUS.md, topos/docs/ROADMAP.md, topos/CONTRACTS.md
- Result: All docs updated

- Action: Wrote LOG and REPORT
- Files changed: topos/handoff/reports/P59-LOG.md, topos/handoff/reports/P59-REPORT.md
```

## Decisions

- Decision: Resolution happens inside collector's collect_once() after enrich_entities(), not in cli.py
  Reason: Handoff requirement 2 — container names can only resolve after enrich_entities() populates Entity.docker. Pre-resolution would require a throwaway sweep.
  Impact: ContainerResolveError propagates from collect_once() and is caught in main() for --once paths. Live/record paths get the error propagated through the frame stream.

- Decision: Used a docker_inspect stub that returns data for both the GAME_KEY and OTHER_KEY docker scopes
  Reason: The gstammtisch fixture has two docker scope directories; the default P55 test stub (lambda _cid: None) leaves Entity.docker=None, which prevents resolution. Our stub populates DockerMeta for both containers.
  Impact: Tests 1-3, 7-8 use the shared stub. Test 5 uses a custom ambiguous-names stub.

- Decision: Added --container to the filtered recordings contract in CONTRACTS.md
  Reason: The handoff asks to update CONTRACTS.md if any selector-composition contract needs a line. --container extends the filtered-recording contract.

## Blockers

- None

## Validation

```bash
# Full suite (post-implementation)
python3 -m pytest topos/tests -q --tb=short
# 923 passed, 2 skipped, 1 warning in 121.18s
```

```bash
# Focused P59 tests
python3 -m pytest topos/tests/test_p59_container_selector.py -q --tb=long
# 9 passed, 1 warning in 0.37s
```

```bash
# py_compile on changed files
python3 -m py_compile topos/src/topos/cli.py
python3 -m py_compile topos/src/topos/collect/collector.py
python3 -m py_compile topos/tests/test_p59_container_selector.py
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.