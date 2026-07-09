# P18 Work Log

## Context

- Branch: feat/groop-p18-bpf-provider
- Worktree: .worktrees/-groop-p18-bpf-provider
- Base commit: 6a9ebab (docs(groop): record P21 merge evidence)
- Package: P18 — Exact BPF network provider
- Current objective: Implement BPF provider behind existing provider boundary

## Timeline

```text
2026-07-10 UTC
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
  groop/tests/fixtures/bpf/snapshot.json (created)
  groop/tests/fixtures/bpf/no-bpf-root/ (empty, for unavailable test)
- Result: BPF provider parses snapshots, maps cgroup IDs to EntityKeys,
  emits net:BPF NetSamples, falls back with status/unavailable_reason
```
