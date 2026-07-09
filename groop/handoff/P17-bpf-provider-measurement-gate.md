# P17 — BPF provider measurement gate and design

**Cut:** v2 foundation. **Depends:** P12, P16 preferred. Branch:
`feat/groop-p17-bpf-measurements`. Follow `groop/README.md` workflow protocol.

## Goal

Prepare exact BPF network accounting without prematurely shipping it. This
package produces the benchmark harness, design, and measurement evidence needed
before implementation or defaults.

## Scope — in

1. BPF design document:
   - cgroup_skb ingress/egress attach points;
   - map shape and keying strategy;
   - userspace cgroup-id-to-path mapping;
   - pin path `/sys/fs/bpf/groop/`;
   - limitations to show in UI/help.
2. Measurement harness:
   - baseline traffic without BPF;
   - same traffic with a fixture/no-op or prototype BPF path if available;
   - cgroup churn and attach/detach recovery procedure.
3. Update `MEASUREMENTS.md` with actual results or a blocked reason.
4. Provider contract check:
   - confirm current `Provider`/`NetSample` is sufficient;
   - propose additive metadata only if required.

## Scope — out

- Shipping a default BPF provider.
- Replacing netns/host providers.
- Daemon productionization.

## Acceptance

- `MEASUREMENTS.md` has the BPF gate section filled or explicitly blocked.
- No BPF state is left pinned after tests.
- No default behavior changes in `groop` live mode.

## Notes

- Do not store path strings in BPF. Path mapping belongs in userspace.
- BPF must be owned by a daemon/helper, not the ephemeral TUI.
