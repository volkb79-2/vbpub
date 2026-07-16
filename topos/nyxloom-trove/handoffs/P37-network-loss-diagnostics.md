# P37 - Network Loss Diagnostics

## Goal

Populate a safe host-level network loss/error diagnostic surface from existing
kernel counters, while keeping per-cgroup attribution honest.

This addresses the remaining diagnostics input gap in `docs/STATUS.md`:
attributable per-cgroup network loss still belongs to v2 BPF/daemon work, but
host/interface drops/errors can be collected and shown now with explicit host
scope.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p37-network-loss-diagnostics`
- Worktree: `.worktrees/-topos-p37-network-loss-diagnostics`
- Branch from local `main`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P37-LOG.md` updated while working
- Finish with `topos/handoff/reports/P37-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `topos/README.md`
- `topos/CONTRACTS.md`
- `topos/TUI-SPEC.md` §3.0, §3.4a, §6.3
- `topos/docs/STATUS.md`
- `topos/src/topos/collect/host.py`
- `topos/src/topos/collect/collector.py`
- `topos/src/topos/diag/rules.py`
- `topos/src/topos/ui/banner.py`
- P34 implementation if it has merged before this package starts
- relevant host/banner/diagnostics tests

## Functional Requirements

Add host/interface-level network loss/error visibility:

- Parse `/proc/net/dev` error/drop counters per interface:
  - rx drops/errors;
  - tx drops/errors;
  - optional packet counters if already parsed by P34.
- Compute rates from deltas in `Collector`, with reset handling.
- Store dynamic interface details in `Frame.host_meta`, not dynamic registry
  metrics.
- Add a concise host-scope banner/status line only when loss/error rates are
  non-zero or when a degraded state is useful.
- Add diagnostics wording that is explicitly host/interface scoped, for example:
  "host interface eth0 is dropping packets; per-cgroup attribution requires BPF".
- Do not imply a specific cgroup caused the loss unless the provider source is
  BPF and the data actually supports it.

If P34 has not merged, coordinate with its `host_meta` shape rather than
inventing a parallel schema.

## Tests

Add focused tests covering:

- parsing `/proc/net/dev` drop/error counters;
- second sample computes drop/error rates;
- counter reset does not produce negative loss rates;
- banner/diagnostic wording is host-scoped and does not name a cgroup as cause;
- no output when loss/error rates are zero unless a degraded state is expected;
- frame JSON round-trip preserves the host metadata.

## Documentation

Update:

- `topos/docs/STATUS.md` diagnostics input notes.
- `topos/docs/BPF-NETWORK-ACCOUNTING.md` only if needed to clarify that exact
  per-cgroup attribution remains v2 BPF work.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- Live BPF attach/pin/detach lifecycle.
- Per-cgroup network loss attribution.
- Packet capture.
- eBPF program compilation.
- Network tuning advice or host mutation.
