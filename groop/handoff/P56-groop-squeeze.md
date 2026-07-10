# P56 - `groop squeeze` (Guided Working-Set Measurement)

## Goal

Add `groop squeeze --target CGROUP_PATH [options]`: a guided, stepped
`memory.high` squeeze that measures a cgroup's real (hot) working set under
pressure, absorbing natively into groop the workflow already proven live by
the standalone script
`scripts/gstammtisch-guide/files/usr/local/sbin/container-mempress.sh`.

## Independence

Independent of P53 (headless record driver), P54 (steady-state report), and
P55 (collector filtering) — none of the three need to exist for this package
to be implemented, tested, or merged. It depends only on already-merged
plumbing: the collector's existing per-cgroup read helpers
(`src/groop/collect/cgroup.py`: `read_int`, `read_flat_kv`, `read_pressure`,
`read_text`) and the v2 admin action framework (`src/groop/actions/`: `P21`
preview skeleton, `P46` execution kernel, `P49` systemd `memory.high`
governance — all already merged/queued ahead of this package in the existing
roadmap, not blocking it). If P55 lands first, `groop squeeze` MAY reuse its
`--slice`/`--entities`-style target-glob validation helper for consistency,
but does not require it — `squeeze` takes one explicit `--target` cgroup path
today, same shape as the existing `action preview/execute --target` and
`inspect-files --target` flags (`src/groop/cli.py` lines ~203, ~209, ~489,
~495).

## Relationship to P49

P49 (`handoff/P49-systemd-memory-governance.md`, queued) adds a single
governed `memory.high` **set-property** action: preview/execute one
`systemctl set-property` value change, through the action-execution kernel's
root/admin/typed-confirmation/audit gates. `squeeze` is a different shape of
operation — a **continuous, timed, multi-step measurement loop** (potentially
dozens of `memory.high` writes over minutes) that must react to live PSI/
refault signals every step. Driving that cadence through a `systemctl
set-property` subprocess per step (P49's mechanism) is both too slow for a
`--delay`-scale loop and produces a distracting audit-log entry per step for
what is fundamentally one measurement session, not N distinct governance
decisions. `squeeze` therefore writes `memory.high` directly to cgroupfs
(`<cgroup>/memory.high`), matching `container-mempress.sh`'s approach, gated
by the same root/`--admin`/typed-confirmation/audit posture the action
framework already established (see Requirements below) rather than routing
through `systemctl set-property` or reusing P49's action kind. `squeeze`
audits the whole session (header + summary), not per-step raw writes, to
keep the audit log proportionate — see the audit requirement below.

## Workflow

- Branch: `feat/groop-p56-groop-squeeze`
- Worktree: `.worktrees/-groop-p56-groop-squeeze`
- Touch only `groop/**`; write P56-LOG.md/P56-REPORT.md; commit, do not merge.

## Requirements

- Add `groop squeeze --target CGROUP_PATH --admin --confirm TEXT [options]`,
  dispatched like the existing `groop action`/`groop daemon`/`groop snapshot`
  subcommands (own `parse_squeeze_args`/`_main_squeeze` in `cli.py`). Require
  root and `--admin` and a typed confirmation value, matching P46's execute
  gating pattern (`src/groop/actions/execute.py`: root-in-production,
  `--admin`, exact-match `--confirm`) — reuse those gate primitives/helpers
  rather than re-implementing root/admin/confirm checks a third time, citing
  the exact functions reused.
- Options, defaults drawn directly from `container-mempress.sh`'s proven
  values: `--step SIZE` (default `256M`), `--delay SECONDS` (default `15`;
  document that PSI `avg10` has a 10 s window so `--delay` below ~10 s
  degrades signal quality — same rationale as the script's comment), `--floor
  SIZE` (default `1G`), `--start SIZE` (default: current `memory.current`
  rounded up to the next `--step` boundary), `--relax-to SIZE|max` (default
  `max`), `--psi-some-limit PCT` (default `10`), `--psi-full-limit PCT`
  (default `5`), `--rf-limit N` (default `200`, refaults/s), `--log FILE`
  (default a groop-namespaced path, e.g. under the existing groop state/log
  convention — cite whatever convention `src/groop/actions/audit.py` or
  `docs/OPERATIONS.md` already establishes for groop-owned log locations
  rather than inventing `/var/log/mempress/...` fresh), `--force` (allow a
  target with `memory.min > 0`).
- Protocol (mirrors the script step-for-step): read current
  `memory.current`/`memory.min` via `read_int()`
  (`src/groop/collect/cgroup.py`); refuse targets with `memory.min > 0`
  unless `--force` (same "protected/prod workload" refusal as the script).
  Set `memory.high` to `--start` (or the computed default), then loop:
  write the current step's `memory.high`, sleep `--delay`, sample
  `memory.current`/`memory.stat:anon`/`memory.stat:zswapped` (via
  `read_flat_kv`), `memory.zswap.current`, `memory.swap.current`, the
  cumulative `memory.stat:workingset_refault_anon` counter (derive
  refaults/s as the delta over `--delay`, same as the script's `RF_RATE`
  calc), and `memory.pressure` PSI `some`/`full` `avg10` (via
  `read_pressure()`). Stop when `psi_some_avg10 > --psi-some-limit` OR
  `psi_full_avg10 > --psi-full-limit` OR `refaults/s > --rf-limit`, OR when
  the next step would go below `--floor`. Record the **last** `memory.high`
  value that showed no pressure signal as the squeeze point (≈ hot+warm
  working set), same semantics as the script's `SQUEEZE_POINT`.
- **Hard safety requirement — always restore `memory.high` on exit,
  including `SIGINT`:** install a `try/finally` (or equivalent
  context-manager) restore path that writes `--relax-to` back to
  `<cgroup>/memory.high` on normal completion, on any stop-threshold exit,
  and on `SIGINT`/`SIGTERM` — no code path may leave a lowered `memory.high`
  in place. Use an injectable signal-registration seam (same pattern P53
  specifies for its own clean-shutdown path) so the default test suite does
  not depend on real OS signal delivery. Test this explicitly: simulate
  `SIGINT` mid-loop and assert the restore write happened.
- Write a headered JSONL log — one `header` record (target, cgroup path,
  step/delay/floor/limits, start time), one `step` record per sample
  (step index, `memory.high` set, sampled `memory.current`/`anon`/
  `zswapped`/`z_pool`(`memory.zswap.current`)/`swap`, PSI `some`/`full`
  `avg10`, refaults/s, timestamp), and one `summary` record (stop reason,
  stop `memory.high`, squeeze point, final samples, restored-to value) — and
  make this log schema-compatible with groop's existing record schema
  (`src/groop/record/writer.py`/`RecordWriter`, the same P2 header+frame
  JSONL convention used by `--record`) so `groop report` (P54, whenever it
  merges) or a future replay path can consume it without a third parser. If
  the exact `Frame`/`EntityFrame` shape does not fit a stepped-squeeze
  record cleanly, define the minimal compatible envelope (same header
  contract: schema version, JSONL-per-line, optional `.zst`) and document
  the deliberate divergence rather than silently diverging.
- Audit the session (not each step) via the existing `AuditLog`
  (`src/groop/actions/audit.py`): one audit record at session start (target,
  parameters, confirm value, admin identity) and one at session end (stop
  reason, squeeze point, restored-to value) — proportionate to P46/P49's
  "audit the decision" posture without one record per `memory.high` write.
- Add fixture-cgroup-tree tests: full stepped-squeeze happy path against an
  injected reader/writer (no real cgroupfs), `memory.min > 0` refusal and
  `--force` override, PSI-threshold stop, refault-rate-threshold stop,
  floor-reached stop, `SIGINT`-mid-loop restore (the hard safety test),
  non-root/no-`--admin`/bad-`--confirm` refusals reusing P46's gate tests as
  a template, JSONL log header/step/summary shape, and no real
  `subprocess`/cgroupfs mutation in the default suite (injected runner only,
  matching P46/P48/P49's "no live host dependency in the normal suite"
  convention).
- Document usage guidance from the two live validation runs below, including
  the two-run stratification pattern (a first "warm boundary" run with
  default thresholds, then a tighter second "hot floor" run) as a
  recommended workflow, not an automated mode.
- Update `README.md` quickstart/CLI docs, `docs/OPERATIONS.md` (a new
  guarded-action runbook entry alongside P46/P49's), and
  `docs/ROADMAP.md`/`docs/STATUS.md` package entries.

## Live validation data (cite in docs)

Proven live on gstammtisch 2026-07-10 by the standalone script referenced
above. Two runs against the devcontainer's own cgroup scope found:

- **Warm boundary ≈ 1.8 GB**: refault rate hit 375/s at `memory.high=1536M`,
  the trigger for the first (looser-threshold) run's stop.
- **Hot floor ≈ 1.5 GB**: a second, tighter run found a refault-rate cliff of
  5810/s at `memory.high=1280M` — the point where the working set genuinely
  cannot compress further without heavy thrashing.
- Document this two-run stratification (default/looser thresholds first to
  find the warm boundary, then a tighter second pass to find the hot floor)
  as the recommended usage pattern in `docs/OPERATIONS.md`: one `squeeze`
  invocation finds *a* stop point; the two-run pattern separates "starts to
  hurt" from "genuinely cannot go lower."

## Out Of Scope

- TUI integration (a `squeeze` hotkey/modal) — CLI-only in v1, matching how
  P29/P45/P48's inspect-files and P21/P46/P49's actions started CLI-first
  before any TUI surface.
- Automatic two-run stratification as a single flag/mode — v1 documents the
  two-run pattern as operator guidance, not automated behavior.
- Daemon-side/remote squeeze (`--attach`) — local-root-only in v1, same
  boundary P46's execution kernel drew for its own actions.
- Reusing or modifying P49's `memory.high` set-property action kind — see
  "Relationship to P49" above; squeeze is a distinct direct-cgroupfs-write
  mechanism with its own gating, not a P49 caller.
- Any new steady-state percentile/report computation over the squeeze log —
  that is P54's concern if/when P54 is extended to consume this log shape;
  P56 only guarantees the log is schema-compatible enough to make that
  future extension straightforward.
