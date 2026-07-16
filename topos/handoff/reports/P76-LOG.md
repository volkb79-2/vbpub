# P76 Work Log

## Context

- Branch: feat/topos-p76-ciu-stack-metadata
- Worktree: .worktrees/topos-p76-ciu-stack-metadata
- Base commit: main (after P58 merge)
- Package: P76 - CIU stack metadata (detection + frame fields)
- Current objective: Teach the collector to recognize ciu-managed containers and attach stack/phase metadata to their entities.

## Timeline

```text
2026-07-13 14:42 UTC
- Action: Explored codebase — read handoff, README, CONTRACTS, dockerjoin.py, model.py, config.py, TUI-SPEC §4.3, CIU-DEPLOY.md S7.1/S7.8
- Commands: explore, read_file (multiple)
- Files read: topos/handoff/P76-ciu-stack-metadata.md, topos/README.md, topos/CONTRACTS.md,
  topos/src/topos/collect/dockerjoin.py, topos/src/topos/model.py, topos/src/topos/config.py,
  topos/TUI-SPEC.md §4.3, ciu/docs/CIU-DEPLOY.md S7.1/S7.8, topos/tests/test_dockerjoin.py,
  topos/tests/test_model_registry.py, topos/handoff/reports/P71-REPORT.md
- Result: Full understanding of required changes, extension points, and test patterns
- Follow-up: Implement CiuMeta dataclass and serialization

2026-07-13 14:52 UTC
- Action: Added CiuMeta dataclass to model.py with stack/phase_raw/phase/source fields
- Commands: edit_file, multi_edit, python3 -m py_compile topos/src/topos/model.py
- Files changed: topos/src/topos/model.py
  - Added CiuMeta dataclass (stack, phase_raw, phase, source)
  - Added ciu: CiuMeta|None field to Entity
  - Added ciu_to_jsonable/ciu_from_jsonable serialization helpers
  - Updated entity_to_jsonable/entity_from_jsonable
- Result: model.py compiles clean. CiuMeta is purely additive; existing frames without ciu parse fine.
- Follow-up: Add CiuConfig to config.py

2026-07-13 14:55 UTC
- Action: Added CiuConfig dataclass to config.py
- Commands: multi_edit, python3 -m py_compile topos/src/topos/config.py
- Files changed: topos/src/topos/config.py
  - Added CiuConfig with stack_roots (tuple of Path, defaults to empty)
  - Added ciu field to ToposConfig
  - Updated to_primitive() and load()
- Result: config.py compiles clean. stack_roots=() disables inference unless configured.
- Follow-up: Add CIU detection logic to dockerjoin.py

2026-07-13 14:58 UTC
- Action: Added CIU detection functions to dockerjoin.py
- Commands: edit_file, multi_edit, python3 -m py_compile topos/src/topos/collect/dockerjoin.py
- Files changed: topos/src/topos/collect/dockerjoin.py
  - Added PHASE_RE regex for phase_<N> parsing
  - Added CIU_CONTAINER_NAME_RE for ciu's ^<project>-<env>-<name>$ pattern
  - Added _parse_phase() helper — validates and returns (raw, int) or (None, None) for malformed
  - Added detect_ciu_from_labels() — label-confirmed tier (ciu.managed="true" check)
  - Added detect_ciu_inferred() — heuristic tier via compose project + name pattern match
  - Updated enrich_entities() with known_stack_roots parameter; populates Entity.ciu
- Result: All files compile clean. Two-tier detection with source discriminator.
- Follow-up: Write test suite

2026-07-13 15:04 UTC
- Action: Wrote comprehensive test suite test_ciu_metadata.py (55 tests)
- Commands: write_file, python3 -m pytest topos/tests/test_ciu_metadata.py -v -q
- Files changed: topos/tests/test_ciu_metadata.py (new)
  - 10 phase parsing tests (valid, malformed, None, whitespace)
  - 9 label-confirmed tests (all labels, partial, managed-only, malformed phase)
  - 7 inferred detection tests (match, no-match, empty roots, distinct source)
  - 3 negative non-ciu tests (plain container, managed=false, label vs inferred)
  - 3 phase ordering tests (numeric, string-fail demo, unknown sorts separately)
  - 4 malformed phase tests (no crash, alpha, missing, negative)
  - 2 grouping correctness tests (exact sets, empty)
  - 6 frame-schema compatibility tests (pre-P76 fixture, no-ciu serialization, round-trips)
  - 5 enrich_entities integration tests (label, inferred, no roots, inspect error, non-docker)
  - 3 config tests (defaults, custom, digest)
  - 3 honest absence tests (not-managed, managed, inspect-failure distinct)
- Result: 55/55 pass. Existing 11 tests also pass.
- Follow-up: Update documentation

2026-07-13 15:08 UTC
- Action: Updated CONTRACTS.md, ARCHITECTURE.md, STATUS.md, ROADMAP.md
- Files changed: topos/CONTRACTS.md, topos/docs/ARCHITECTURE.md,
  topos/docs/STATUS.md, topos/docs/ROADMAP.md
- Result: All docs updated to reflect CIU metadata implementation.
- Follow-up: Write LOG and REPORT, run gates, commit
```

## Decisions

- Decision: Use a parallel CiuMeta dataclass (not extending DockerMeta)
  Reason: The handoff explicitly allows either approach. A parallel dataclass on Entity.ciu is purely additive — no existing frame schema or DockerMeta consumers are affected.
  Impact: Entities without ciu metadata serialize without ciu keys (consumers tolerate absence). No golden frame regeneration needed.

- Decision: CiuConfig with stack_roots as tuple[Path, ...] defaulting to empty
  Reason: The handoff says "Default to a sensible discovery rule." An empty list means no inference without explicit config, which is safer than guessing — a host not running ciu won't get false positives.
  Impact: Operators must configure stack roots in config.toml for inference to work. Label-confirmed detection works unconditionally.

- Decision: `compose_project` serves as the inferred `stack` value
  Reason: In inference mode we don't know the actual stack directory path from ciu metadata. The compose project is the best approximation.
  Impact: Inferred stacks are labeled as compose-project names, not canonical ciu stack paths. This is acceptable per the handoff since the inferred tier is explicitly heuristic.

- Decision: No new registry metrics for CIU state
  Reason: CIU metadata is not a MetricValue — it's entity-level metadata like DockerMeta. The handoff doesn't require registry metrics and adding them would change the frame schema contract.
  Impact: CIU data lives in Entity.ciu, serialized through entity_to_jsonable. No registry changes needed.

## Blockers

None.

## Validation

```bash
# 55 new CIU metadata tests
$ PYTHONPATH=topos/src python3 -m pytest topos/tests/test_ciu_metadata.py -q
55 passed, 1 warning in 0.19s

# Existing tests still green
$ PYTHONPATH=topos/src python3 -m pytest topos/tests/test_dockerjoin.py topos/tests/test_model_registry.py -q
11 passed, 1 warning in 0.29s

# All files compile
$ python3 -m py_compile topos/src/topos/model.py topos/src/topos/config.py topos/src/topos/collect/dockerjoin.py topos/tests/test_ciu_metadata.py
# no output = clean
```

## Handoff Checklist

- [ ] Report file written.
- [ ] Log file current.
- [ ] Tests/compile/smoke recorded.
- [ ] Known gaps documented.
- [ ] Feature branch committed.
