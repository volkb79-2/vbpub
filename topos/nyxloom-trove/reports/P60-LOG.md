# P60 Work Log

## Context

- Branch: `feat/topos-p60-metrics-fieldlist-selector`
- Worktree: `.worktrees/topos-p60-metrics-fieldlist-selector`
- Base commit: main (after P55 merge)
- Package: P60 - Free-form `--metrics` field/family list selector
- Current objective: Generalize P55's closed `--metrics full|compact` enum into an open comma-separated field/family list selector

## Timeline

```text
2026-07-13 12:00 UTC
- Action: Read handoff, existing codebase (registry.py, cli.py, collector.py, P55 tests/docs)
- Result: Understanding phase complete

2026-07-13 12:30 UTC
- Action: Added net/damon/governance families to METRIC_GROUPS; froze COMPACT_GROUPS as literal; added FIELD_LIST_BLOCK_MAP and parse_metrics_selector()
- Commands: PYTHONPATH=topos/src python3 -c "from topos.registry import parse_metrics_selector; ..."
- Files changed: topos/src/topos/registry.py
- Result: parse_metrics_selector() working; all test cases pass

2026-07-13 12:45 UTC
- Action: Changed --metrics from argparse choices to free-form string; added _validate_metrics_mode(); wired into main()
- Files changed: topos/src/topos/cli.py
- Result: CLI accepts free-form --metrics; validation rejects unknown/empty

2026-07-13 13:00 UTC
- Action: Generalized Collector pruning to handle field-list mode
- Files changed: topos/src/topos/collect/collector.py
- Result: Collector supports full/compact/field-list; 32 P55 tests still pass

2026-07-13 13:20 UTC
- Action: Wrote 19 tests in test_p60_fieldlist.py
- Commands: python -m pytest topos/tests/test_p60_fieldlist.py -q (19 passed)
- Files changed: topos/tests/test_p60_fieldlist.py
- Result: All 7 acceptance oracles covered; additional edge cases test

2026-07-13 13:40 UTC
- Action: Updated docs (README.md, CONTRACTS.md, ROADMAP.md, STATUS.md)
- Result: P60 documented as done

2026-07-13 14:00 UTC
- Action: Running full gates, writing LOG/REPORT, committing
```

## Decisions

- Decision: Add net/damon/governance as METRIC_GROUPS entries, not a separate dict
  Reason: The field-list resolution contract requires family tokens to resolve via METRIC_GROUPS. The alternative (separate FAMILIES dict) would split the source of truth.
  Impact: COMPACT_GROUPS must be a literal set (not derived from METRIC_GROUPS.keys()) to keep compact mode from expanding.
- Decision: FIELD_LIST_BLOCK_MAP lives in registry.py
  Reason: Handoff requirement says "document this mapping in one place (registry or a small module-level dict), not scattered."
  Impact: Single source of truth; collector and parser both import from registry.py.

## Blockers

None.

## Validation

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p60_fieldlist.py -q
# 19 passed in 0.84s

PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p55_filtering.py -q
# 32 passed in 0.56s

PYTHONPATH=topos/src python3 -m py_compile topos/src/topos/registry.py topos/src/topos/cli.py topos/src/topos/collect/collector.py
# All compile OK

PYTHONPATH=topos/src python3 -c "
import sys; sys.argv = ['topos', '--once', '--json', '--slice', 'system.slice',
  '--metrics', 'ram,psi_mem_some_avg10', '--cgroup-root',
  'topos/tests/fixtures/cgroupfs/gstammtisch']
from topos.cli import main; exit(main())
" 2>&1 >/dev/null; echo "CLI smoke exit code: $?"
# CLI smoke exit code: 0
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.

## Self-Review Findings (2026-07-13)

Adversarial read of the P60 diff against the handoff:

1. **Unused imports** caught: `json`, `Path`, `walk_entities`, `FIELD_LIST_BLOCK_MAP`, `parse_metrics_selector` were imported but unused in `test_p60_fieldlist.py`. Removed. `_docker_inspect` helper was also using `lambda _cid: None` which broke container resolution — replaced with a fixture-aware inspect stub.

2. **Missing `--container` composition test**: Requirement 6 says `--metrics <list>` composes with `--container` if P59 is merged (it is). Added `test_fieldlist_composes_with_container` — 20 tests now, up from 19.

3. **No hollow tests identified**: Every acceptance oracle asserts the OBSERVABLE outcome:
   - Oracle 1: asserts exact kept metric set (equality, not subset)
   - Oracle 2: asserts exact 6-name set (fails if single-name handling were used)
   - Oracle 3: asserts `eframe.network is not None` / `is None` on real frame objects
   - Oracle 4/5: subprocess with `returncode == 2` and `stderr` content check
   - Oracle 6: uses same `issubset`/block-dropping assertions as P55's own tests
   - Oracle 7: `main()` return value check with `rc == 2`
   
4. **Date in LOG/REPORT**: 2026-07-13 matches `date -u`. Counts (19→20 tests, 57→61 suite) are real.

5. **LOG/REPORT present**, ASCII, no dead code or scaffolding in the diff.
