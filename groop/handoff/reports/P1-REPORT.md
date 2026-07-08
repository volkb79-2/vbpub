# P1 Report — Collector Core

## What Was Built

- Added the `groop` Python package skeleton under `groop/src/groop`, with `pyproject.toml` and console script metadata.
- Implemented CONTRACTS model dataclasses plus canonical compact JSON helpers:
  `frame_to_jsonable()`, `frame_from_jsonable()`, and `validate_frame_metrics()`.
- Added a registry-backed metric catalog for P1 core cgroup, zswap, CPU, PSI, IO, pids, network placeholders, pressure placeholder, and host banner metrics.
- Implemented cgroup v2 tree walking from an injectable `cgroup_root`, entity kind/parent derivation, cgroup file parsing, missing/permission degradation, and cgroup metric collection.
- Ported the production zswap math:
  `rf_z/s = delta(zswpin) / dt`,
  `rf_d/s = max(0, delta(workingset_refault_anon) - delta(zswpin)) / dt`,
  `rf_f/s = delta(workingset_refault_file) / dt`,
  plus `ratio` and `swap_disk`.
- Implemented Docker metadata join for `docker-<64hex>.scope` keys with injectable `docker_inspect`.
- Implemented host facts from `/proc` and `/sys`, including meminfo, loadavg, uptime, PSI, zswap params/debugfs fallback, and disk swap estimate.
- Implemented process drilldown helper from `cgroup.procs` and `/proc/<pid>`.
- Implemented collector orchestration with per-entity raw counter state and reset handling.
- Implemented stdlib-only CLI path: `groop --once --json --cgroup-root PATH` via `python3 -m groop.cli`.
- Added gstammtisch-like cgroup fixture, golden JSONL frame, and focused tests for serialization, registry enforcement, cgroup collection, zswap/reset math, Docker join, process drilldown, permission/missing degradation, and CLI JSON.

## Deviations

- P1 emits P1-owned placeholders for `net_rx_bps`, `net_tx_bps`, and `pressure` as unavailable. P3/P6 are expected to populate those domains.
- The fixture intentionally keeps several parent/best-effort rows sparse to exercise missing-data degradation; complete realistic data is present for root, the game Docker scope, and the pak slice.
- Unlimited cgroup limits (`max`) serialize as `MetricValue(None, "unlimited")` after controller review, so downstream UI/replay can distinguish known infinity from unavailable data.
- `pytest` was not installed in the system Python. For validation only, pytest was installed into `/tmp/groop-pytest` and invoked through `PYTHONPATH`; no repository or system package files were modified.

## Proposed Contract Changes

- Implemented during controller review: `MetricSource` now includes `unlimited` for cgroup infinity states such as `memory.max=max`, `memory.high=max`, `pids.max=max`, and `cpu.max=max`.

## Test Evidence

`PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p1-collector/groop/src python3 -m pytest groop/tests -q`

```text
...........                                                              [100%]
11 passed in 0.21s
```

`PYTHONPATH=/tmp/vbpub-groop-p1-collector/groop/src python3 -m py_compile $(find groop/src/groop -name '*.py' | sort)`

```text
# no output; exit 0
```

`PYTHONPATH=/tmp/vbpub-groop-p1-collector/groop/src python3 -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch`

```text
schema_version 1
entities 8
game_swap_disk [38000000, 'derived']
```

## Known Gaps / Open Items

- No Textual/UI code by design.
- No record/replay file framing beyond the canonical frame serializer and golden JSONL fixture; P2 owns recording headers/readers/rings.
- Network provider and pressure diagnostics are placeholders until P3/P6.
- Host facts currently keep kernel release as implementation-local context rather than adding a non-metric field to `Frame`; add only if a later contract needs it.
