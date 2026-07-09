# P18 Work Log

## Context

- Branch: feat/groop-p18-bpf-provider
- Worktree: .worktrees/-groop-p18-bpf-provider
- Base commit: 6a9ebab (docs(groop): record P21 merge evidence)
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
- File(s): groop/src/groop/providers/net_bpf.py (created)
  groop/tests/fixtures/bpf/working/snapshot.json (created)
  groop/tests/fixtures/bpf/unavailable/ (empty, for unavailable test)
- Result: BPF provider parses snapshots, maps cgroup IDs to EntityKeys,
  emits net:BPF NetSamples, falls back with status/unavailable_reason

2026-07-09 CEST
- Action: Controller review polish before merge.
- Decision: Label P18 as the BPF provider read side with live attach/snapshot
  writing still pending; expose BPF rates as `MetricValue.src="bpf"` instead of
  generic `derived`.
- Files changed: `groop/src/groop/model.py`,
  `groop/src/groop/collect/collector.py`, `groop/src/groop/providers/__init__.py`,
  `groop/src/groop/providers/net_bpf.py`, `groop/src/groop/registry.py`,
  `groop/tests/test_network_providers.py`, P18 docs/reports.
- Commands:
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests/test_network_providers.py -q` -> 15 passed in 0.16s
  - `/tmp/vbpub-groop-p17-venv/bin/python -m py_compile ...` -> clean
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch` -> schema=1 entities=8 host=36
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests -q` -> 147 passed in 25.14s
- Result: Controller validation passed.

2026-07-09 CEST
- Action: Merged P18 into `main` and reran validation from the main checkout.
- Commands:
  - `git merge --no-ff feat/groop-p18-bpf-provider -m "Merge groop P18 BPF provider read side"`
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests/test_network_providers.py -q` -> 15 passed in 0.15s
  - `/tmp/vbpub-groop-p17-venv/bin/python -m py_compile ...` -> clean
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch` -> schema=1 entities=8 host=36
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests -q` -> 147 passed in 25.68s
- Files changed: `groop/docs/STATUS.md`, `groop/handoff/reports/P18-LOG.md`, `groop/handoff/reports/P18-REPORT.md`.
- Result: P18 merged and validated on `main`.
- Follow-up: Commit post-merge evidence.
```
