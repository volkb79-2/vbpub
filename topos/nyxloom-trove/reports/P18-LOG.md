# P18 Work Log

## Context

- Branch: feat/topos-p18-bpf-provider
- Worktree: .worktrees/-topos-p18-bpf-provider
- Base commit: 6a9ebab (docs(topos): record P21 merge evidence)
- Package: P18 - Exact BPF network provider read side
- Current objective: Implement BPF provider behind existing provider boundary

## Timeline

```text
2026-07-09 CEST
- Action: Create worktree and read all required handoff/docs/source files
- Action: Read CONTRACTS.md provider interface, net_host.py, net_netns.py,
  collector.py, test_network_providers.py, test_collector.py, bpf_gate.py
- Decision: BPF provider reads structured JSON snapshot files representing
  pinned BPF map state. On this host, no BPF is available, so the provider
  uses fixture files. The snapshot format matches the BPF design doc's
  key/value shape: cgroup_id, direction, family, proto, bytes, packets.
- Action: Create net_bpf.py with BpfProvider class
- Action: Create test fixtures at tests/fixtures/bpf/
- Action: Write tests in test_network_providers.py
- File(s): topos/src/topos/providers/net_bpf.py (created)
  topos/tests/fixtures/bpf/working/snapshot.json (created)
  topos/tests/fixtures/bpf/unavailable/ (empty, for unavailable test)
- Result: BPF provider parses snapshots, maps cgroup IDs to EntityKeys,
  emits net:BPF NetSamples, falls back with status/unavailable_reason

2026-07-09 CEST
- Action: Controller review polish before merge.
- Decision: Label P18 as the BPF provider read side with live attach/snapshot
  writing still pending; expose BPF rates as `MetricValue.src="bpf"` instead of
  generic `derived`.
- Files changed: `topos/src/topos/model.py`,
  `topos/src/topos/collect/collector.py`, `topos/src/topos/providers/__init__.py`,
  `topos/src/topos/providers/net_bpf.py`, `topos/src/topos/registry.py`,
  `topos/tests/test_network_providers.py`, P18 docs/reports.
- Commands:
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests/test_network_providers.py -q` -> 15 passed in 0.16s
  - `/tmp/vbpub-topos-p17-venv/bin/python -m py_compile ...` -> clean
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch` -> schema=1 entities=8 host=36
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests -q` -> 147 passed in 25.14s
- Result: Controller validation passed.

2026-07-09 CEST
- Action: Merged P18 into `main` and reran validation from the main checkout.
- Commands:
  - `git merge --no-ff feat/topos-p18-bpf-provider -m "Merge topos P18 BPF provider read side"`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests/test_network_providers.py -q` -> 15 passed in 0.15s
  - `/tmp/vbpub-topos-p17-venv/bin/python -m py_compile ...` -> clean
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch` -> schema=1 entities=8 host=36
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests -q` -> 147 passed in 25.68s
- Files changed: `topos/docs/STATUS.md`, `topos/handoff/reports/P18-LOG.md`, `topos/handoff/reports/P18-REPORT.md`.
- Result: P18 merged and validated on `main`.
- Follow-up: Commit post-merge evidence.
```
