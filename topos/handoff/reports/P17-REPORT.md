# P17 Report

## What Was Built

P17 is complete as a measurement-gate and design slice, with no default BPF provider and no live behavior change.

- Added `topos/docs/BPF-NETWORK-ACCOUNTING.md` covering the cgroup_skb ingress/egress attach model, numeric keying, userspace cgroup-id mapping, `/sys/fs/bpf/topos/`, daemon/helper ownership, cleanup/recovery, and UI/help limitations.
- Added `topos/src/topos/bpf_gate.py`, a safe unprivileged gate helper that probes the environment, collects a baseline host-network snapshot, and reports blocked live-BPF preflight without loading or pinning anything.
- Added `topos bpf gate` to `topos/src/topos/cli.py` as an explicit non-default entry point.
- Added focused tests in `topos/tests/test_bpf_gate.py`.
- Updated `topos/MEASUREMENTS.md` with the concrete safe-run evidence and the live-BPF blocker reason.
- Updated `topos/README.md`, `topos/docs/ROADMAP.md`, `topos/docs/STATUS.md`, and `topos/docs/ARCHITECTURE.md` so P17 is no longer listed as merely proposed and the BPF design/gate is discoverable from the canonical docs.

## Deviations

- The handoff asked for live BPF load evidence if available. On this host, live loading was blocked by missing `bpftool`, non-root uid, and an unwritable `/sys/fs/bpf/topos` pin root, so the gate remains intentionally safe/no-op.
- The harness is a CLI helper rather than a daemon worker. That keeps the gate unprivileged by default and avoids any default behavior change.

## Proposed Contract Changes

None.

`Provider.status()` already has room for future BPF metadata, and `NetSample` already carries `source_label`, `confidence`, `aggregation`, and `unavailable_reason` enough for a later `net:BPF` implementation.

## Test Evidence

```bash
/tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests/test_bpf_gate.py -q
# 2 passed in 0.07s

/tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests -q
# 98 passed in 15.38s

/tmp/vbpub-topos-p17-venv/bin/python -m py_compile topos/src/topos/cli.py topos/src/topos/bpf_gate.py topos/tests/test_bpf_gate.py
# passed

/tmp/vbpub-topos-p17-venv/bin/topos bpf gate --proc-root topos/tests/fixtures/procfs/network --json
# safe no-op
# blocked live BPF load: bpftool missing, uid 1003 not root, /sys/fs/bpf/topos not writable
# baseline rx=15100 tx=27100 rx_pkts=151 tx_pkts=191

/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 98 passed in 15.28s

PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli bpf gate --proc-root topos/tests/fixtures/procfs/network --json
# safe no-op JSON, baseline rx=15100 tx=27100 rx_pkts=151 tx_pkts=191
```

## Known Gaps / Open Items

- Live BPF attach/detach validation still needs a privileged host with `bpftool` and a writable bpffs pin root.
- P18 still needs the exact BPF provider implementation.
- The safe gate intentionally does not create or clean up any BPF objects, because it is only the evidence gate.

## Controller Merge Review

- Feature commit reviewed and amended: `16060be`.
- Merge commit: `cf718d1`.
- Post-merge validation from `main`:
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_bpf_gate.py -q` -> `2 passed in 0.07s`
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q` -> `98 passed in 15.40s`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli bpf gate --proc-root topos/tests/fixtures/procfs/network --json` -> safe no-op JSON
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch` -> `schema_version=1 entities=8 host_metrics=36`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke` -> `ui smoke ok frames=1 view=tree profile=auto`
