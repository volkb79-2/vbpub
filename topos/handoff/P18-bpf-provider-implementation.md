# P18 — Exact BPF network provider

**Cut:** v2. **Depends:** P16, P17. Branch:
`feat/topos-p18-bpf-provider`. Follow `topos/README.md` workflow protocol.

## Goal

Implement exact per-cgroup socket traffic accounting behind the existing network
provider interface, using the design and measurements from P17.

## Scope — in

1. BPF-owned provider path:
   - daemon/helper owns load/attach/pin lifecycle;
   - TUI reads counters through provider/status API.
2. Counter mapping:
   - cgroup id to entity key mapping in userspace;
   - per-cgroup rx/tx bytes and packets;
   - source label `net:BPF`.
3. Provider fallback:
   - if BPF unavailable, host/netns providers continue to work;
   - status explains why BPF is unavailable.
4. UI/help:
   - BPF limitations visible in glossary/drill-down;
   - provider status shown somewhere operator-visible.
5. Tests:
   - fixture/unit coverage for map parsing and provider fallback;
   - integration only where environment supports it.

## Scope — out

- Traffic shaping or prioritization.
- Packet capture.
- tc/nftables/DSCP governance.

## Acceptance

- Existing network provider tests remain green.
- BPF provider emits the same metric names and frame shape as other providers.
- `MEASUREMENTS.md` is updated with overhead evidence from the implementation.

## Notes

- BPF is exact for socket traffic at the cgroup hook; it is not a packet sniffer.
  Preserve that distinction in UI strings.
