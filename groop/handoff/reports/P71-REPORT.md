# P71 REPORT — ZFS ARC Host Provider

## What was built

A read-only ZFS ARC host provider that teaches groop about ARC memory. On a ZFS
host, the ARC can hold many GB of RAM that `MemAvailable` counts as unavailable,
so groop's host memory banner would report severe memory pressure on a host that
is actually fine. This package adds:

**Five new host metrics** in the registry (`host_zfs_arc_size`, `host_zfs_arc_target`,
`host_zfs_arc_max`, `host_zfs_arc_min`, `host_zfs_arc_hit_ratio`) following the
`host_zswap_*` pattern (locality=local, branch_policy=n/a, aggregatable=False,
sources naming the kstat path, real glossary sentences).

**Collection function** `_zfs_arc_metrics()` in `collect/host.py` that reads
`/proc/spl/kstat/zfs/arcstats`, parses the three-column kstat format defensively,
and returns `MetricValue` objects. On hosts without ZFS the file is absent and
all five metrics emit `v=None, src="unavail_kernel"` — never `0`.

**Hit-ratio rate computation** over the sample interval using module-level state
that carries raw `hits`/`misses` counters. The rate is `hits_delta / (hits_delta +
misses_delta)`. Counter regression (pool export/import) emits `v=None` and reseeds
— never a negative or absurd ratio. The raw hit counter is carried in
`MetricValue.raw`.

**Banner annotation** in `ui/banner.py`: a line like `ARC 12.0GiB/32.0GiB (hit 97%)`
appears only when ZFS is present. No new panel, no new hotkey, no layout redesign.

**`host_meta["zfs_arc"]`** populated with all integer kstat fields for drill-down
when ZFS is present. Absent on non-ZFS hosts (consumers tolerate absence per
CONTRACTS §4).

**Fixture** `tests/fixtures/procfs/zfs/arcstats` with realistic values (12 GB ARC
size, 32 GB max, 5B hits / 150M misses, ~97% hit ratio).

**11 tests** covering all 6 acceptance oracles.

**Documentation updates**: ARCHITECTURE.md, ROADMAP.md, STATUS.md, README.md,
COMPRESSED-SWAP.md.

## Deviations from the handoff doc

None. The implementation follows the handoff exactly.

## Proposed contract changes

None. The new metrics are additive host-level metrics following the established
`host_*` pattern. No interfaces or contracts were modified.

## Test evidence

**Environment:** This host has no ZFS (absent-path is the live-validatable case).

```bash
$ PYTHONPATH=groop/src python3 -W ignore -m pytest groop/tests/test_zfs_arc.py -q -W ignore -v
============================= test session starts ==============================
...
collected 11 items

groop/tests/test_zfs_arc.py ...........                                  [100%]
============================== 11 passed in 0.33s ==============================
```

All 11 tests pass:

| Oracle | Test | Status |
|---|---|---|
| 1. Present ZFS | `test_zfs_arc_present_fixture_exact_values` | Pass |
| 2. Absent ZFS | `test_zfs_arc_absent_fixture_all_unavail` | Pass |
| 3. Malformed (truncated) | `test_zfs_arc_malformed_truncated` | Pass |
| 3. Malformed (non-numeric) | `test_zfs_arc_malformed_non_numeric` | Pass |
| 3. Malformed (missing size) | `test_zfs_arc_malformed_missing_size` | Pass |
| 4. Hit-ratio rate (two sweeps) | `test_zfs_arc_hit_ratio_rate_over_two_sweeps` | Pass |
| 4. Counter regression | `test_zfs_arc_hit_ratio_counter_regression` | Pass |
| 4. Zero delta | `test_zfs_arc_hit_ratio_no_delta_regression` | Pass |
| 5. Banner present | `test_zfs_arc_banner_present` | Pass |
| 5. Banner absent | `test_zfs_arc_banner_absent` | Pass |
| 6. Non-ZFS unaffected | `test_zfs_arc_non_zfs_fixtures_unaffected` | Pass |

**Existing tests remain green** (47 tests, 0 failures):

```bash
$ PYTHONPATH=groop/src python3 -W ignore -m pytest \
  groop/tests/test_host_swap.py \
  groop/tests/test_p23_zram_drilldown.py \
  groop/tests/test_ui_banner.py \
  groop/tests/test_zfs_arc.py \
  groop/tests/test_collector.py -q -W ignore
...............................................                          [100%]
47 passed in 0.68s
```

**`--once --json`** works on this non-ZFS host:

```bash
$ PYTHONPATH=groop/src python3 -m groop.cli --once --json | python3 -c "import sys,json; d=json.load(sys.stdin); print({k:d['host'][k] for k in d['host'] if 'zfs' in k})"
{'host_zfs_arc_hit_ratio': [None, 'unavail_kernel'], 'host_zfs_arc_max': [None, 'unavail_kernel'], 'host_zfs_arc_min': [None, 'unavail_kernel'], 'host_zfs_arc_size': [None, 'unavail_kernel'], 'host_zfs_arc_target': [None, 'unavail_kernel']}
```

**py_compile** clean on all changed files:

```bash
$ python3 -m py_compile groop/src/groop/collect/host.py groop/src/groop/registry.py groop/src/groop/ui/banner.py
# no output = clean
```

**git diff --check** clean:

```bash
$ git diff --check
# no output = clean
```

## Known gaps / open items

- **Golden frames**: The handoff oracle 6 says "if `--once --json` output changes
  for existing fixtures, regenerate the goldens." Since this is a purely additive
  package (non-ZFS fixtures are unaffected), existing goldens do not need
  regeneration. The `test_zfs_arc_non_zfs_fixtures_unaffected` test verifies this.
- **L2ARC, ZIL, SLOG metrics**: Explicitly out of scope per the handoff.
- **Per-cgroup ARC attribution**: Impossible from kernel files; explicitly not
  attempted per the handoff.
- **Diagnostics rules that act on ARC size**: Explicitly out of scope per the
  handoff. A future package could add rules like "ARC is squeezing your workload."
- **ZFS on the review host**: The review host has no ZFS, so the absent-path is
  the only live-validatable path. The present-path rests on the fixture, which
  asserts exact values (oracle 1) to provide confidence.