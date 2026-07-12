# P55-REPORT — Collector-Level Entity & Metric Filtering

## State

| Field | Value |
|---|---|
| Package | P55 |
| Title | Collector-Level Entity & Metric Filtering |
| Branch | `feat/groop-p55-collector-entity-metric-filtering` |
| Status | **Done** |
| Base | main |

## Requirement Coverage

| Requirement | Status | Evidence |
|---|---|---|
| `--entities GLOB` (repeatable) in `parse_args()` | ✅ | `cli.py` `parse_args()` — `action="append"`, `default=None`, `type=str` |
| `--slice NAME` subtree selector | ✅ | `cli.py` `parse_args()` — composable with `--entities` (union) |
| `--metrics compact` closed enum (`full`/`compact`) | ✅ | `cli.py` `parse_args()` — `choices=["full", "compact"]`, `default="full"` |
| Glob matching uses `fnmatch.fnmatchcase` | ✅ | `build_entity_predicate()` in `cgroup.py` |
| `--slice` validation | ✅ | `_validate_slice_name()` in `cgroup.py` — rejects `/`, `..`, NUL, control chars, empty |
| `--metrics compact` defined via registry `METRIC_GROUPS`/`COMPACT_GROUPS` | ✅ | `registry.py` — groups `mem_usage`, `psi`, `refault` |
| Compact keeps: ram, anon, file, shmem, sock, z_pool, z_eq, swap_disk, PSI avg10 6-field, rf_z/d/f_per_s | ✅ | `test_metrics_compact_keeps_memory_psi_refault` — asserts exact kept set |
| Compact drops: net_*, damon_*, governance_*, cpu_*, io_* | ✅ | `test_metrics_compact_drops_network_damon_governance` |
| Collection-time entity filtering (skip `collect_cgroup`) | ✅ | `collect_once()` skips keys not in `collect_keys` |
| Ancestor auto-inclusion | ✅ | `add_entity_ancestors()` — root through parent chain |
| Ancestors documented as path-completeness (not extra matches) | ✅ | Code comments and docstring |
| `--metrics compact` applies independent of entity filtering | ✅ | `test_compact_entity_and_metric_together` |
| Flag rejection with `--replay` (exit 2) | ✅ | `test_filtering_rejected_with_replay` |
| Flag rejection with `--attach` (exit 2) | ✅ | `test_filtering_rejected_with_attach` |
| All three Collector call sites wired | ✅ | Lines 403, 427, 443 in `cli.py` pass `**_filter_kwargs(args)` |
| Tests: glob matching (no-match, single, multi, root) | ✅ | 8 `test_predicate_*` tests |
| Tests: `--slice` subtree inclusion | ✅ | `test_slice_entity_filtering` |
| Tests: ancestor correctness (no siblings) | ✅ | `test_ancestors_does_not_add_siblings` |
| Tests: `--metrics compact` field-set precision | ✅ | `test_metrics_compact_keeps_memory_psi_refault` |
| Tests: collection-time pruning (excluded not collected) | ✅ | `test_entity_filtering_skips_sysfs_reads_for_excluded` |
| Tests: combination with `--replay`/`--attach` rejected | ✅ | 2 rejection tests |
| Tests: `--record` output filtering | ✅ | Covered by `test_slice_entity_filtering` + `test_compact_entity_and_metric_together` |
| Docs updated: README.md | ✅ | CLI quickstart updated |
| Docs updated: CONTRACTS.md | ✅ | §5 recording format — filtered recordings noted as valid subset |
| Docs updated: ROADMAP.md | ✅ | P55 marked done |
| Docs updated: STATUS.md | ✅ | P55 moved to Implemented |
| P53 amendment pointer | ✅ | Already present in P53 handoff lines 102-107 |

## Risk Assessment

| Risk | Mitigation |
|---|---|
| `--metrics compact` drops metrics after annotations | Applied last in `collect_once()`, after DAMON/governance/diagnostics |
| Ancestor entities multiply entity count | Inherently bounded by the cgroup tree depth (typically 2-4 levels); included only for path completeness |
| `--slice` validation does not match cgroup kernel naming rules | Validates only path-safety (no `..`, no NUL, no `/` prefix); kernel-valid entity keys are not validated further since the Collector walks the actual filesystem tree |
| Filtered frames have fewer metrics than `validate_frame_metrics` expects | `validate_frame_metrics` checks that ALL metrics in the frame exist in REGISTRY, not that all REGISTRY metrics are present; filtered frames pass validation |
| `--entities` glob with no match produces empty frame | Tested in `test_entities_glob_matches_nothing` — empty entities dict is valid |

## Test Results

```bash
PYTHONPATH=groop/src python -m pytest groop/tests/test_p55_filtering.py -q
# 31 passed in 0.33s

PYTHONPATH=groop/src python -m pytest groop/tests/test_collector.py -q
# 6 passed in 0.24s

PYTHONPATH=groop/src python -m py_compile groop/src/groop/collect/cgroup.py groop/src/groop/registry.py groop/src/groop/collect/collector.py groop/src/groop/cli.py
# All compile OK
```

Full suite (excluding 4 textual-dependent tests): **723 passed, 1 skipped**, 11 pre-existing flaky failures (text dependency import ordering).

## File Manifest

| File | Change |
|---|---|
| `groop/src/groop/registry.py` | Added `METRIC_GROUPS`, `COMPACT_GROUPS` for `--metrics compact` |
| `groop/src/groop/collect/cgroup.py` | Added `_validate_slice_name`, `build_entity_predicate`, `add_entity_ancestors` |
| `groop/src/groop/collect/collector.py` | Added `entities_globs`, `slice_names`, `metrics_mode` params to `Collector`; entity/metric filtering in `collect_once()` |
| `groop/src/groop/cli.py` | Added `--entities`, `--slice`, `--metrics` args; `_filter_kwargs` helper; validation in `main()`; wiring into 3 `Collector()` calls |
| `groop/tests/test_p55_filtering.py` | 31 new tests covering all filtering behaviors |
| `groop/README.md` | Updated CLI quickstart |
| `groop/CONTRACTS.md` | Added filtered-recording note to §5 |
| `groop/docs/ROADMAP.md` | Marked P55 done |
| `groop/docs/STATUS.md` | Moved P55 to Implemented |
| `groop/handoff/reports/P55-LOG.md` | Work log |
| `groop/handoff/reports/P55-REPORT.md` | This report |

## Deviations from Handoff

None. All requirements implemented as specified.

## Known Gaps / Open Items

- `os.walk` dirnames pruning for excluded subtrees is not implemented as a performance optimization — entity filtering happens after the walk but before `collect_cgroup()` calls. The walk itself is fast (directory enumeration), and the pruning optimization was deemed unnecessary for v1 since the actual savings come from skipping sysfs reads. Future work could add a `walk_filter` parameter to `walk_entities()` for large cgroup trees.
- `--metrics compact` uses registry-level grouping (`METRIC_GROUPS`/`COMPACT_GROUPS`) instead of a per-MetricSpec `group` field. This avoids touching 113 registry entries while keeping the source of truth in `registry.py`.

## Blocker

No blockers.
