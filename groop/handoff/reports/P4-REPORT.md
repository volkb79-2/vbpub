# P4 Report

## What was built

- Added `src/groop/drift/origin.py` plus `src/groop/drift/__init__.py`.
- Added a post-collect governance annotator that:
  - reads `systemctl show` through an injectable runner;
  - classifies `mem_min`, `mem_low`, `mem_high`, `mem_max`, `cpu_weight`, and `io_weight` as `systemd_unit`, `systemd_runtime_dropin`, `raw_write`, `docker_default`, or `unset`;
  - marks drift with `warn`/`red` severity using the protected-entity effective-floor rule from the spec;
  - computes `effective_memory_min` from the managed ancestor chain and stores detailed explainable metadata in `EntityFrame.governance`.
- Integrated the annotator into `Collector.collect_once()` without changing frame serialization or replay code.
- Added live `io_weight` collection from `io.weight`.
- Added registry metrics:
  - `io_weight`
  - `governance_origin`
  - `governance_drift`
  - `effective_memory_min`
- Added fixture-backed tests for:
  - clean runtime-dropin ownership;
  - raw-write drift against a recorded systemd value;
  - ancestor-capped protected `memory.min` turning red;
  - transient-slice / missing-unit footgun.
- Refreshed the golden frame fixture to include governance metadata and the new metrics.

## Deviations from the handoff doc

- The registry now carries numeric summary metrics (`governance_origin`, `governance_drift`) while the full string classification stays in the `governance` block. This keeps the frozen `MetricValue` contract intact while still exposing sortable registry-backed governance state.
- Effective `memory.min` intentionally ignores the cgroup root entity when clamping descendants. Including the root's default `0` would collapse every protected descendant to `0`, which is not the intended Finding-A check from the spec's ancestor-slice language.

## Proposed contract changes

- None required.

## Test evidence

`PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p4-drift/groop/src python3 -m pytest groop/tests -q`

Tail:

```text
..........................
...
29 passed in 2.29s
```

`PYTHONPATH=/tmp/vbpub-groop-p4-drift/groop/src python3 -m py_compile $(find groop/src/groop -name '*.py' | sort)`

Tail:

```text
[no output]
```

`PYTHONPATH=/tmp/vbpub-groop-p4-drift/groop/src python3 -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch`

Tail:

```text
exit 0; emitted a valid single-line JSON frame (schema_version=1).
```

## Known gaps / open items

- The injectable `systemctl show` runner makes tests deterministic, but ad-hoc collection against fixture cgroup roots still reads the host's real systemd state unless a custom runner is injected. That is acceptable for the current read-only quality gate, but a future fixture/replay-facing CLI flag for canned systemd data would make manual fixture runs fully reproducible.
