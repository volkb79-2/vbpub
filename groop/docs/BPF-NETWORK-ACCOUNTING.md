# BPF Network Accounting Design

This document captures the v2 BPF network design for `groop` without changing
the live default behavior. It is the design target for P18, and the measurement
gate in P17 records whether the host can support that design safely.

## Goal

Provide exact per-cgroup socket traffic without replacing the current v1 host
and netns providers. The BPF path is a root-owned helper/daemon concern, not a
TUI concern.

## Attach Points

- Use one `cgroup_skb` ingress program and one `cgroup_skb` egress program.
- Attach as high in the cgroup tree as practical, ideally at the unified cgroup
  root, so descendants inherit coverage without per-container attach churn.
- The programs should be allow/pass only; they count, they do not block.

## Map Shape And Keying

The BPF side should keep keys numeric and small:

```text
key:
  cgroup_id: u64
  direction: ingress|egress
  family: ipv4|ipv6|other
  proto: tcp|udp|icmp|other

value:
  bytes: u64
  packets: u64
```

- Use per-CPU maps or another low-contention layout for high packet rates.
- Do not store path strings, container names, or Docker IDs in BPF.
- Keep protocol-family dimensions coarse first; finer buckets are optional later.

## Userspace Cgroup Mapping

Userspace owns the `cgroup_id -> EntityKey` mapping.

- The helper walks the live cgroup tree and records the canonical `EntityKey`
  for each discovered cgroup.
- The BPF counters are keyed by numeric cgroup id; userspace resolves them back
  to paths during collection and replay.
- Path mapping stays entirely in userspace. The kernel program only sees ids.
- The exact kernel mechanism used to obtain the cgroup id/path correspondence
  is an implementation detail, but the contract is stable: numeric id in,
  canonical cgroup path out.

## Ownership Model

- BPF state is owned by the daemon/helper or a small root-owned control process.
- The ephemeral TUI only reads provider status and counter snapshots.
- Multiple TUI sessions should share the same pinned programs/maps.

## Pin Path

- Pin all groop-owned programs, maps, and ownership markers under
  `/sys/fs/bpf/groop/`.
- Keep the pin root separate from the UI process lifetime so a crashed TUI does
  not orphan the BPF objects.

## Cleanup And Recovery

- On startup, the helper should audit `/sys/fs/bpf/groop/` for stale groop-owned
  pins and ownership markers.
- If a prior run crashed after pinning but before detach, the helper should
  detect the existing objects and either reuse them or detach/recreate them
  deterministically.
- On normal shutdown, detach both ingress and egress programs and unpin maps and
  programs that were created for the session.
- Recovery must be idempotent: a repeated stop should not damage foreign BPF
  state.

## UI And Help Limitations

The UI should describe the BPF provider as per-cgroup socket traffic, not as a
wire tap.

- It does not replace host interface counters.
- It may miss or undercount traffic generated outside normal socket paths.
- ARP and other non-IP traffic are outside the intended accounting scope.
- Forwarded or bridged traffic may need TC/XDP instead.
- Per-packet accounting has real overhead and must stay behind measurement
  gates.
- Source labels should remain explicit: `net:BPF`, `net:NS`, `net:HOST`, and
  `net:N/A`.

## Measurement Gate Notes

P17 adds a safe, unprivileged measurement helper that reports the BPF preflight
status and baseline host traffic without loading BPF or leaving pinned state.
That helper is the evidence gate for future live BPF work.

## Implementation Status

P42 (done) implements the daemon-side ``BpfSnapshotBridge`` that reads an
explicitly configured pinned BPF counter map via ``bpftool --json map dump pinned``
through an argv-only, injectable command runner. It decodes the logical dimensions
(cgroup id, direction, family, proto, bytes, packets), builds the ``cgroup_map``
from a configured cgroup-v2 root, and atomically writes the P18 ``snapshot.json``
contract with schema/version, generation timestamp, source/map metadata, ``maps``,
and ``cgroup_map`` fields. The bridge enforces path confinement, output bounds,
last-good preservation, and non-world-writable permissions. It integrates into
``groop daemon serve`` behind an explicit ``--bpf-root`` CLI option or
``[bpf_snapshot]`` config section, both disabled by default. BPF program
compilation and the privileged attach/pin/detach lifecycle remain future work.
