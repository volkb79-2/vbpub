# P8 Report

## What was built

- Added passive DAMON collection in `groop/src/groop/damon/passive.py` with a read-only scanner over a configurable DAMON sysfs root (`/sys/kernel/mm/damon/admin/kdamonds` by default).
- Wired passive DAMON annotation into the collector after entity collection and before governance/diagnostics/UI use.
- Added `[damon]` config defaults in `groop.config`:
  - `hot_rate = 50.0`
  - `warm_rate = 5.0`
  - `cold_age = 30.0`
  - `idle_age = 120.0`
- Registered and emitted DAMON metrics:
  - `damon_hot_bytes`, `damon_warm_bytes`, `damon_cold_bytes`, `damon_idle_bytes`
  - `damon_hot_pct`, `damon_warm_pct`, `damon_cold_pct`, `damon_idle_pct`
  - `damon_sample_age_s`
  - `damon_mode`
- Updated the `damon` UI profile to use the registry-backed DAMON metrics and added a DAMON drill-down panel with:
  - mode / kdamond / context / scheme info
  - target pid coverage
  - sample age
  - hot/warm/cold/idle byte and percent bars
  - simple region-class histogram
- Added passive DAMON fixtures and behavior tests for:
  - vaddr attribution
  - paddr host-only metadata
  - missing DAMON root
  - classification math
  - stale sample age
  - read-only source audit

## Read-only evidence

- `groop/src/groop/damon/passive.py` only uses read-side filesystem access (`Path.read_text()`, `Path.stat()`, directory iteration).
- No DAMON control paths are written.
- Source audit command produced no matches:

```bash
rg -n "write_text|\\.write\\(|open\\([^)]*,\\s*['\\\"][^'\\\"]*[wa+][^'\\\"]*['\\\"]|commit" groop/src/groop/damon/passive.py
```

- On this host, the live `--once --json` smoke degraded DAMON fields to `unavail_perm`, which is expected passive behavior for an unreadable real sysfs tree. No privileged or mutating check was forced.

## Deviations / gaps

- Passive ingestion is strictly read-only, so it does **not** trigger `update_schemes_tried_regions`. It only reads `tried_regions` when a snapshot is already exposed.
- Multi-target vaddr contexts are attributed only when every readable `pid_target` resolves to the same entity. Mixed-entity contexts are left unattributed rather than duplicating one shared snapshot across multiple rows.
- paddr sessions are exposed as host/session metadata on the root entity drill-down only. They do not populate per-entity DAMON byte/percent metrics.

## Contract note

- Implemented an additive serialized `EntityFrame.damon` metadata block in `groop.model` so replayed frames can render the DAMON drill-down panel. Existing frame fields and metric serialization remain unchanged.
- Controller review documented this additive block in `groop/CONTRACTS.md` and
  added round-trip serialization coverage.

## Validation

```bash
PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p8-damon-passive/groop/src python3 -m pytest groop/tests -q
# 63 passed in 10.17s
```

```bash
PYTHONPATH=/tmp/vbpub-groop-p8-damon-passive/groop/src python3 -m py_compile $(find groop/src/groop -name '*.py' | sort)
# clean
```

```bash
PYTHONPATH=/tmp/vbpub-groop-p8-damon-passive/groop/src python3 -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
# emitted one JSON frame; live DAMON fields degraded to unavail_perm on this host
```

```bash
PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p8-damon-passive/groop/src python3 -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known open items

- If P9/P11 need richer DAMON replay/state, the additive `EntityFrame.damon` block should be documented formally in `CONTRACTS.md`.
- Mixed-entity vaddr sessions could be surfaced later as explicit “unattributed DAMON session” host metadata instead of being skipped.
