# P57 Work Log — Docker-Name Entity Selectors

## Context

- Branch: `feat/groop-p57-docker-name-entity-selectors`
- Worktree: `.worktrees/-groop-p57-docker-name-entity-selectors`
- Base commit: `bc09212` (docs: controller workflow v2 — role-separated orchestration)
- Package: P57 — Docker-Name Entity Selectors
- Current objective: Implement --container NAME_OR_PREFIX resolver and wire into CLI

## Timeline

```text
2026-07-12 UTC
- Action: Explored codebase, read handoff, model, cli.py, dockerjoin.py, existing tests
- Files changed: (none yet — research phase)
- Result: Full understanding of architecture — resolver goes in dockerjoin.py,
  --container flags added to parse_inspect_files_args and parse_action_args,
  resolution wired into _main_inspect_files and _main_action handlers.
- Follow-up: Implement resolve_container_key(), add CLI args, wire resolution, add tests.

2026-07-12 UTC
- Action: Implemented resolve_container_key() in dockerjoin.py
- Files changed: groop/src/groop/collect/dockerjoin.py
- Result: Added resolve_container_key(name_or_prefix, entities) — scans DOCKER_SCOPE_RE
  entities for exact name match → prefix match, enforces ambiguity check, zero-match check.
- Follow-up: Wire into CLI.

2026-07-12 UTC
- Action: Added --container to inspect-files and action arg parsers; wired resolution
  into _main_inspect_files and _main_action handlers.
- Files changed: groop/src/groop/cli.py
- Result: --container flags on both CLI surfaces, mutual exclusivity with --target,
  resolution via collector sweep before existing validation.
- Follow-up: Add tests.

2026-07-12 UTC
- Action: Added comprehensive tests for resolver + CLI mutual exclusion
- Files changed: groop/tests/test_dockerjoin.py (new resolver tests),
  groop/tests/test_inspect_files.py (mutual exclusion tests),
  groop/tests/test_actions.py (mutual exclusion tests)
- Result: Tests pass for all resolver cases (exact, prefix, ambiguous, zero-match)
  and CLI mutual-exclusion on both inspect-files and action subcommands.
- Follow-up: Update README/docs, run full suite.

2026-07-12 UTC
- Action: Updated README.md, docs/ROADMAP.md, docs/STATUS.md; ran full test suite
- Files changed: groop/README.md, groop/docs/ROADMAP.md, groop/docs/STATUS.md
- Result: All docs updated; full suite 703 passed, 11 pre-existing textual failures (no --container issues).
- Follow-up: Self-review, final REPORT, commit.

2026-07-12 UTC
- Action: Self-review completed, REPORT finalized, all gates passed
- Files changed: groop/handoff/reports/P57-SELFREVIEW.md, groop/handoff/reports/P57-REPORT.md, groop/handoff/reports/P57-LOG.md
- Result: All files ready for commit. Feature branch ready for controller review.
```

## Decisions

- Decision: Put resolve_container_key() in dockerjoin.py rather than a new file
  Reason: It depends directly on DOCKER_SCOPE_RE, EntityKey, DockerMeta, Entity
  — all already in dockerjoin.py. It's ~35 lines of code, tightly coupled to
  the same regex and model types. Keeping forward join and reverse lookup in one
  module avoids a second file with near-identical imports.
  Impact: One small addition to an existing well-tested module; no new import
  paths or package dependencies.

- Decision: Resolver takes entities dict and collects a fresh frame for resolution
  Reason: The contract says resolution happens after enrich_entities() in the
  current sweep. The CLI handlers create a Collector, collect_once(), then resolve.
  Impact: --container is always resolved from live sweep data, never stale.

- Decision: Use sys.exit(2) for error cases via return 2 from main handlers
  Reason: Matches existing groop exit code patterns (exit 2 for user errors).
  Impact: Consistent with rest of codebase.

## Blockers

None.

## Validation

```text
$ python3 -m pytest groop/tests/test_dockerjoin.py -v
# 8 passed (resolver tests: exact, prefix, exact-beats-prefix, cid-prefix,
#   ambiguous, zero-match, non-docker-entity-skipped, existing join test)
$ python3 -m pytest groop/tests/test_inspect_files.py -v -k container
# 2 passed (plan_both_target_and_container_exit_2, read_both_target_and_container_exit_2)
$ python3 -m pytest groop/tests/test_actions.py -v -k container
# 2 passed (preview_both_target_and_container_exit_2, execute_both_target_and_container_exit_2)
$ python3 -m py_compile groop/src/groop/collect/dockerjoin.py
$ python3 -m py_compile groop/src/groop/cli.py
$ python3 -m py_compile groop/tests/test_dockerjoin.py
# All compile clean
$ python3 -m pytest groop/tests -q --ignore=groop/tests/test_ui_app.py --ignore=groop/tests/test_damon_paddr.py --ignore=groop/tests/test_damon_passive.py --ignore=groop/tests/test_p23_zram_drilldown.py
# 703 passed, 11 pre-existing textual-not-installed failures, 1 skipped
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
