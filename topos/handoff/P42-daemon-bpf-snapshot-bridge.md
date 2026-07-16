# P42 - Daemon BPF Snapshot Bridge

## Goal

Connect the P18 BPF provider read side to daemon-owned pinned counter maps by
adding a safe, deterministic snapshot writer. This package consumes already
loaded and pinned maps; it does not load, attach, detach, or compile BPF
programs.

## Workflow

- Branch: `feat/topos-p42-daemon-bpf-snapshot-bridge`
- Worktree: `.worktrees/-topos-p42-daemon-bpf-snapshot-bridge`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P42-LOG.md` current
- Finish with `topos/handoff/reports/P42-REPORT.md` and focused commits

## Requirements

- Add a daemon-side module that reads one explicitly configured pinned counter
  map using `bpftool --json map dump pinned PATH` through an argv-only,
  injectable command runner. Never invoke a shell and never accept an arbitrary
  command from a client.
- Validate the map path is beneath the configured topos BPF root. Reject
  traversal, symlink escape, malformed JSON, nonzero commands, oversized
  output, invalid rows, negative counters, and unsupported key/value shapes
  with actionable status while preserving the last valid snapshot.
- Decode the documented P17/P18 logical dimensions: cgroup id, ingress/egress,
  IPv4/IPv6/other, TCP/UDP/ICMP/other, bytes, and packets. Keep the decoding
  seam explicit so fixtures can cover the bpftool JSON representation without
  requiring BPF privileges.
- Build `cgroup_map` from the configured cgroup-v2 root using a small injectable
  cgroup-id resolver. The production resolver must document and test the exact
  kernel identity assumption it uses; do not silently map uncertain ids.
- Emit the existing P18 `snapshot.json` contract with schema/version,
  generation timestamp, source/map metadata, `maps`, and `cgroup_map` fields.
  Write in the destination directory through a private temporary file, flush
  and fsync it, atomically replace `snapshot.json`, and keep permissions
  non-world-writable.
- Integrate periodic snapshot refresh into `topos daemon serve` only behind an
  explicit disabled-by-default CLI/config option. A missing `bpftool`, absent
  pins, or refresh failure must not crash the read-only frame broker; provider
  fallback and operator-visible status must remain clear.
- Keep daemon shutdown bounded and deterministic. Do not leave temporary files
  or background threads after normal shutdown.
- Update README, ROADMAP, STATUS, BPF design/operations documentation, and
  MEASUREMENTS with precise implemented/non-implemented boundaries.

## Acceptance

- Fixture tests prove decoding, cgroup mapping, atomic replacement, last-good
  preservation, path confinement, output bounds, command failure, and cleanup.
- Daemon tests prove the bridge is disabled by default and enabled refresh
  failures do not break `current`/`stream` frame service.
- The produced fixture snapshot is consumed by `BpfProvider` as `net:BPF`
  without a parallel schema adapter.
- Existing daemon, provider, CLI, acceptance, and full tests remain green.
- `py_compile` passes for all touched Python.

## Out Of Scope

- BPF C source, compilation, CO-RE packaging, or kernel verifier work.
- Loading, attaching, pinning, detaching, or deleting BPF programs/maps.
- Reusing or cleaning stale pins.
- Enabling BPF by default or claiming live overhead evidence.
- Client-triggered privileged operations or protocol expansion.

## Follow-Up Boundary

The next BPF package may add the allow-only `cgroup_skb` program artifact and
the daemon-owned attach/pin/recovery/detach lifecycle. It must consume this
snapshot bridge and retain the P17 measurement gate before any default change.
