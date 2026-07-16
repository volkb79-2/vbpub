# P7 Report

## What Was Built

- Finalized `topos` CLI modes in [src/topos/cli.py](/tmp/vbpub-topos-p7-integration/topos/src/topos/cli.py):
  - `topos --version`
  - `--config PATH` via shared `topos.config.load(path)`
  - `--profile NAME` for UI/replay runs
  - live TUI (`topos`)
  - `--once --json`
  - `--record FILE` as live TUI + recording
  - `--replay FILE [--speed N|--step]`
  - hidden `--ui-smoke`
- Added a shared live annotated frame stream in [src/topos/record/live.py](/tmp/vbpub-topos-p7-integration/topos/src/topos/record/live.py) so the live path is:
  `collector -> network/drift/diagnostics in collector.collect_once() -> record writer -> UI consumer`.
- Made the slow-sample policy explicit and testable:
  - sample timing uses `sleep = max(0, interval - sample_time)`
  - overruns skip sleep and never try to catch up with negative delays
- Extended UI profile handling in [src/topos/ui/app.py](/tmp/vbpub-topos-p7-integration/topos/src/topos/ui/app.py) and [src/topos/ui/table.py](/tmp/vbpub-topos-p7-integration/topos/src/topos/ui/table.py):
  - config default still works
  - CLI override works
  - custom config-defined profiles work
- Added P7 integration coverage in [tests/test_record.py](/tmp/vbpub-topos-p7-integration/topos/tests/test_record.py):
  - fixture-root live loop over multiple frames
  - record -> replay canonical equality
  - UI smoke over replay
  - config path/default profile override
  - custom profile override
  - live `--record FILE` UI recording
  - version smoke
- Added a README quickstart section in [README.md](/tmp/vbpub-topos-p7-integration/topos/README.md).

## Deviations / Gaps

- No package version bump was made; `--version` reports the existing package version `0.1.0`.
- Spec §9 item 11 mentions `pipx install` from an sdist/wheel. I executed the requested clean-venv editable-install smoke (`pip install -e`) and verified the console script; I did not build/test an sdist/wheel or run `pipx`.
- Real-host root verification was not executed. Current user is non-root (`uid 1003`, user `vb`), and the task explicitly disallowed sudo or host mutation.
- `strace` is not available in this environment, so the v1 no-write assurance is based on code audit/grep rather than syscall tracing.

## Validation

### Required gates

```text
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p7-integration/topos/src python3 -m pytest topos/tests -q
57 passed in 9.70s
```

```text
PYTHONPATH=/tmp/vbpub-topos-p7-integration/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)
# exited 0
```

```text
PYTHONPATH=/tmp/vbpub-topos-p7-integration/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# exited 0; wrote valid JSON
```

```text
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p7-integration/topos/src python3 -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### Packaging / console script

```text
python3 -m venv /tmp/topos-p7-venv
/tmp/topos-p7-venv/bin/pip install -e /tmp/vbpub-topos-p7-integration/topos
/tmp/topos-p7-venv/bin/topos --version
topos 0.1.0
/tmp/topos-p7-venv/bin/topos --replay /tmp/vbpub-topos-p7-integration/topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### Real-host safe read-only smoke

```text
id -u && id -un
1003
vb
```

```text
id -nG
vb docker
```

```text
TIMEFMT='elapsed=%E user=%U sys=%S'; time env PYTHONPATH=/tmp/vbpub-topos-p7-integration/topos/src python3 -m topos.cli --once --json >/tmp/topos-p7-host-once.json
elapsed=0.80s user=0.27s sys=0.34s
```

```text
wc -c /tmp/topos-p7-host-once.json
338025 /tmp/topos-p7-host-once.json
```

```text
env PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p7-integration/topos/src python3 -m topos.cli --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### No-write audit for v1 paths

```text
rg -n "write_text\\(|\\.write_text\\(|open\\([^\\n]*['\\\"][wa]\\b|open\\([^\\n]*mode=['\\\"][wa]\\b|os\\.write\\(|subprocess\\.(run|Popen)\\([^\\n]*(systemctl set-property|tee |echo >|printf >)" topos/src/topos
# exited 1 (no matches)
```

`strace` was not installed (`command -v strace` returned nothing), so this report
uses source audit evidence for the v1 read-only guarantee.

## Acceptance Checklist

| Spec §9 item | Status | Evidence / note |
|---|---|---|
| 1. Performance | Not fully executed | No 5-minute `pidstat` run was captured in this noninteractive session. The real-host non-root smoke completed in `0.80s`, but that is not a substitute for the full steady-state acceptance measurement. |
| 2. Memory | Partially evidenced | `tests/test_record.py::test_history_ring_storage_budget_uses_numeric_arrays` passed and keeps the ring under the expected budget in the tested configuration. No separate live RSS measurement was captured on the real host. |
| 3. Reset handling | Pass | Existing collector reset test remained green: `tests/test_collector.py::test_second_sample_computes_rates_and_counter_reset_degrades`. |
| 4. Finding-D detection | Pass | Existing origin/drift tests remained green, including raw-write drift detection in `tests/test_origin.py`. |
| 5. Non-container visibility | Pass | Existing collector/UI fixtures remained green; `soulmask-paks.slice` continues to appear as a first-class entity with process drill-down coverage. |
| 6. Graceful degradation | Pass | Existing fixture degradation tests remained green across collector/diagnostics/network coverage. |
| 7. Metric registry semantics | Pass | Full suite green; serializer and UI/registry tests still pass. |
| 8. Diagnostics | Pass | Diagnostics tests remained green; replay still preserves/recomputes findings as intended. |
| 9. Network labels | Pass | Existing network provider tests remained green. |
| 10. Record/replay fidelity | Pass | Added `test_live_fixture_stream_records_and_replays_canonical_frames`. |
| 11. Packaging | Partially executed | Clean-venv editable install and console script smoke passed. `pipx` / sdist / wheel smoke not executed. |
| 12. v2 gating | Not applicable to this P7 v1 slice | No v2 admin surface was added here. |
| 13. Unprivileged-mode smoke | Pass | Ran real-host non-root UI and `--once --json` smokes as user `vb` in groups `vb docker`; both started without a password prompt or crash. |
| 14. MEASUREMENTS gates | Deferred by spec | No BPF or DAMON-default enablement work in this slice. |

## Known Open Items

- Capture the full spec §9 item 1 CPU measurement with a steady-state interactive run and `pidstat` or equivalent on the target host.
- Capture a live RSS measurement for item 2 on the target host.
- Optionally add a wheel/sdist + `pipx` packaging check if the release process needs stricter packaging evidence than the editable-install smoke used here.
