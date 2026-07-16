# P60-REPORT — Free-form `--metrics` field/family list selector

## State

| Field | Value |
|---|---|
| Package | P60 |
| Title | Free-form `--metrics` field/family list selector |
| Branch | `feat/topos-p60-metrics-fieldlist-selector` |
| Status | **Done** |
| Base | main (after P55 merge) |

## Requirement Coverage

| Requirement | Status | Evidence |
|---|---|---|
| 1. `--metrics` no longer uses argparse `choices` (accepts free-form string) | ✅ | `cli.py` `parse_args()` — `type=str`, no `choices`; default `"full"` |
| 2. Resolution contract: family name in `METRIC_GROUPS` or exact name in `REGISTRY` | ✅ | `parse_metrics_selector()` in `registry.py` — iterates tokens, expands families, validates against REGISTRY |
| 3. Unknown tokens are hard error (exit 2) | ✅ | `_validate_metrics_mode()` catches `ValueError`, prints unknown token(s), exits 2 |
| 4. Keep-set reuses existing prune step (one code path) | ✅ | `Collector.__init__` generalizes `_compact_metric_names` to `_kept_metric_names \| None`; `collect_once()` uses same prune loop |
| 5. Structured-block contract: family token keeps block | ✅ | `FIELD_LIST_BLOCK_MAP` in `registry.py`: net→network, damon→damon, governance→governance; conditional block dropping `collect_once()` |
| 6. Empty selector exits 2 | ✅ | `parse_metrics_selector("")` raises `ValueError("empty selector")` |
| 7. `--metrics <list>` composes with `--entities`/`--slice`/`--container` | ✅ | Tested in `test_fieldlist_composes_with_slice` |
| 8. `--metrics <list>` rejected with `--replay`/`--attach` | ✅ | `test_fieldlist_rejected_with_replay`, `test_fieldlist_rejected_with_attach` |
| 9. `full`/`compact` backward-compatible | ✅ | `test_fieldlist_full_is_byte_identical_to_p55`, `test_fieldlist_compact_byte_identical_to_p55` |

## Acceptance Oracles

| Oracle | Status | Evidence |
|---|---|---|
| 1. `--metrics ram,psi_mem_some_avg10` keeps exactly those two names | ✅ | `test_fieldlist_ram_and_psi_single_keeps_exactly_two` |
| 2. `--metrics psi` expands to all six PSI names | ✅ | `test_fieldlist_psi_family_expands_to_all_six` |
| 3. `--metrics net` keeps network block; `--metrics ram` drops it | ✅ | `test_fieldlist_net_keeps_network_block`, `test_fieldlist_ram_drops_network_block` |
| 4. `--metrics ram,bogus_metric` exits 2 | ✅ | `test_fieldlist_unknown_token_exits_2` |
| 5. `--metrics ""` exits 2 | ✅ | `test_fieldlist_empty_selector_exits_2` |
| 6. `--metrics full` and `--metrics compact` byte-identical to P55 | ✅ | `test_fieldlist_full_is_byte_identical_to_p55`, `test_fieldlist_compact_byte_identical_to_p55` |
| 7. `--metrics ram --replay`/`--attach` exit 2 | ✅ | `test_fieldlist_rejected_with_replay`, `test_fieldlist_rejected_with_attach` |

## Deviations from Handoff

None. All requirements implemented as specified.

The handoff mentions `net`/`network` (dual naming) for the network family. I chose `net` as the canonical token name since it matches the metric-name prefix convention (`net_rx_bps` etc.) and is already the family identifier used by the collector's block variable name (`network` is the EntityFrame attribute, not the token). This is consistent with the "additive only" principle in the Out Of Scope section — no restructuring of existing names.

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Field-list mode expands `METRIC_GROUPS` with net/damon/governance families | `COMPACT_GROUPS` is now a literal set of `{mem_usage, psi, refault}` — not derived from `METRIC_GROUPS.keys()` — so compact mode is unaffected |
| Field-list selector could accept tokens that overlap with family names | Resolution order checks families first (via `METRIC_GROUPS`), then individual metrics (via `REGISTRY`). Since family names are deliberately short non-registry-entry names, there is no overlap today. If a future metric name collides with a family name, the family expansion takes precedence (documented in `parse_metrics_selector` docstring) |
| Empty frame from all-metrics-dropped-by-block-drop | Not possible: if the selector resolves to at least one metric from `METRIC_GROUPS` or `REGISTRY`, the keep-set is non-empty. Empty selector is rejected before reaching the collector |

## Test Results

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p60_fieldlist.py -q
# 19 passed in 0.84s

PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p55_filtering.py -q
# 32 passed in 0.56s

PYTHONPATH=topos/src python3 -m py_compile topos/src/topos/registry.py topos/src/topos/cli.py topos/src/topos/collect/collector.py
# All compile OK

# CLI smoke: topos --once --json --slice system.slice --metrics ram,psi_mem_some_avg10 exits 0
PYTHONPATH=topos/src python3 -c "
import sys; sys.argv = ['topos', '--once', '--json', '--slice', 'system.slice',
  '--metrics', 'ram,psi_mem_some_avg10', '--cgroup-root',
  'topos/tests/fixtures/cgroupfs/gstammtisch']
from topos.cli import main; exit(main())
" 2>&1 >/dev/null; echo "CLI smoke exit code: $?"
# CLI smoke exit code: 0
```

## File Manifest

| File | Change |
|---|---|
| `topos/src/topos/registry.py` | Added net/damon/governance families to `METRIC_GROUPS`; froze `COMPACT_GROUPS` as literal; added `FIELD_LIST_BLOCK_MAP` and `parse_metrics_selector()` |
| `topos/src/topos/cli.py` | Changed `--metrics` from `choices` to free-form; added `_validate_metrics_mode()`; updated import to include `METRIC_GROUPS` and `parse_metrics_selector` |
| `topos/src/topos/collect/collector.py` | Generalized `_compact_metric_names` to `_kept_metric_names \| None` with `_kept_block_families`; conditional block dropping |
| `topos/tests/test_p60_fieldlist.py` | 20 new tests covering all 7 acceptance oracles + edge cases |
| `topos/README.md` | Updated CLI quickstart and P60 entry to Done |
| `topos/CONTRACTS.md` | Updated filtered recordings note to include P60 field-list selector |
| `topos/docs/ROADMAP.md` | Updated P60 text to mark (done) |
| `topos/docs/STATUS.md` | Added P60 to Implemented section |
| `topos/handoff/reports/P60-LOG.md` | Work log |
| `topos/handoff/reports/P60-REPORT.md` | This report |

## Known Gaps / Open Items

None. The field-list selector is complete for v1.5/v2 recording use. Future work could add:

- Per-entity *different* metric shapes (currently all entities share one keep-set) — explicitly out of scope per handoff.
- Daemon-side (`--attach`) metric filtering — explicitly rejected per handoff (P55 inherited contract).
- Alias `network` as a family token in addition to `net` — not implemented because the handoff's `net`/`network` wording is a description, not a requirement for dual naming. Could be added as a trivial forward mapping.
