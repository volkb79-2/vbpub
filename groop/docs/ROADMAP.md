# groop Roadmap

This roadmap turns completed handoff findings and `TUI-SPEC.md` into the next
engineering slices. It is intentionally ordered for low regret: stabilize and
measure the current product before adding privileged infrastructure.

## Direction

1. **Certify v1/v1.5 before expanding scope.**
   The current code is a strong prototype, but release claims still need live
   acceptance evidence and UX hardening.
2. **Make compressed swap backend-aware before more tuning advice.**
   ZRAM, zswap, disk swap, and mixed setups need explicit labels so formulas and
   findings do not imply disk IO where the host is using RAM-backed swap.
3. **Keep root-owned state out of the ephemeral TUI.**
   DAMON control currently works from CLI/API, but BPF, root-only reads, and
   long-lived paddr should be daemon-owned before becoming defaults.
4. **Make every data source explainable.**
   Source labels, registry metadata, and drill-down explanations are part of the
   product, not decoration.
5. **Prefer additive provider interfaces.**
   BPF, GPU, ZFS, daemon attach, and future web UI should reuse the frame/model
   boundary instead of creating parallel schemas.

## Proposed Slices

```mermaid
flowchart TD
    P12[P12 v1/v1.5 acceptance + packaging] --> P13[P13 UI navigation + replay polish]
    P12 --> P14[P14 DAMON modal + live-root acceptance]
    P13 --> P15[P15 snapshot enrichment]
    P12 --> P19[P19 ZRAM + swap backend awareness]
    P19 --> P16[P16 daemon read broker for non-root full reads]
    P14 --> P16
    P16 --> P17[P17 BPF provider measurement gate]
    P17 --> P18[P18 BPF provider implementation]
    P16 --> P20[P20 daemon attach mode]
    P20 --> P22[P22 daemon deployment preflight]
    P22 --> P25[P25 daemon install plan]
    P13 --> P21[P21 v2 action gating skeleton]
    P19 --> P23[P23 ZRAM per-device drill-down]
    P13 --> P24[P24 Replay timestamp jump controls]
    P15 --> P26[P26 Snapshot progress UI]
    P19 --> P27[P27 Swap/refault terminology aliases]
    P6 --> P28[P28 I/O cap saturation]
    P21 --> P29[P29 Inspect-files safety skeleton]
    P20 --> P30[P30 Daemon default client UX]
    P30 --> P31[P31 Daemon client error guidance]
    P31 --> P32[P32 Daemon status command]
    P12 --> P33[P33 Release smoke harness]
    P33 --> P35[P35 Acceptance steady harness]
    P13 --> P34[P34 Host device banner]
    P13 --> P36[P36 CPU sparkline surface]
    P34 --> P37[P37 Network loss diagnostics]
    P35 --> P38[P38 TUI smoke evidence]
    P38 --> P39[P39 Release readiness ledger]
    P39 --> P40[P40 Textual 8 compat]
```

## Remaining Estimate

After P39, the roadmap is mostly in three buckets:

| Bucket | Estimated packages | Notes |
|---|---:|---|
| v1/v1.5 release confidence and UI polish | 0 remaining | P40 removed the full-suite blocker; only manual live-host evidence remains. |
| v2 privileged daemon/BPF/admin/file work | 5-7 | BPF daemon lifecycle, paddr daemon ownership, install execution/service hardening, executable admin actions, inspect-files content mode, systemd property governance. |
| Optional plugins / future surfaces | 3-4 | GPU, ZFS, CIU grouping/actions, web UI/API polish. |

Pragmatic estimate from the current state: a shippable v1/v1.5 release
candidate needs **1 remaining package** plus live-host acceptance
evidence. Implementing the broader roadmap end-to-end still looks like **9-13
small packages**, depending on whether "fully completed" includes optional
plugins and web UI.

## Near Term

### P12 — Release Hardening And Acceptance

Status: done. P12 records full tests, compile, fixture JSON, replay smoke,
package build, wheel install, version, and bounded once/json CPU/RSS evidence.

Remaining release evidence: full 5-minute live TUI CPU/RSS, live DAMON
acceptance, and any future BPF gate measurements.

### P13 — UI Navigation And Replay Polish

Status: done. Tree expand/collapse, replay controls/status, reserved v2 action
messaging, profile warning polish, operations docs, and focused Textual tests
landed in P13.

Remaining UX work: deeper key/profile customization can be carved later if needed.

### P24 - Replay Timestamp Jump Controls

Status: done. P24 adds replay first/last (`home`/`end`) and frame/timestamp jump
prompt (`j`) controls, `ReplayDriver.seek_timestamp()`, compact status/help
lines, and focused tests. The existing pause/step/speed model is preserved.

Handoff: `handoff/P24-replay-timestamp-jump.md`.
Report: `handoff/reports/P24-REPORT.md`.

### P14 — DAMON Control Modal And Live-Root Acceptance

Status: done with a live-root gap. P14 added Textual typed-confirmation modals
for vaddr and paddr, groop-owned cleanup controls, fixture safety tests, and
operations/measurement docs.

Remaining gate: run live-root acceptance on a deliberate test host and record
the results in `MEASUREMENTS.md`. `damon_stat` conflict handling remains
conservative/read-only.

### P15 — Snapshot Enrichment

Status: done. P15 added fresh systemctl/docker metadata collection, richer
inspect output, hash failure reporting, redaction tests, and operations docs.
The snapshot progress gap was closed by P26.

### P26 - Snapshot Progress UI

Status: done. P26 makes TUI snapshot creation visibly running (immediate status
update), guarded against duplicate concurrent starts, and reports success/failure
through the status line without changing bundle contents. Focused tests cover
the progress flag, duplicate-start guard, success path reporting, and handled
exception failure reporting.

Handoff: `handoff/P26-snapshot-progress-ui.md`.
Report: `handoff/reports/P26-REPORT.md`.

### P19 — ZRAM And Swap-Backend Awareness

Status: done with a terminology-alias gap. P19 detects active
zswap/zram/disk/mixed backends, adds host-level ZRAM metrics, corrects banner
wording, and documents the per-cgroup attribution boundary. P23 closed the
per-device drill-down gap.

Aliases landed in P27; canonical keys preserved, backend-aware labels added, diagnostic wording updated.

### P27 - Swap/Refault Terminology Aliases

Status: done. P27 keeps canonical frame keys stable while allowing
clearer `swap_dev`, `rf_dev_per_s`, and `rf_dev` profile/UI aliases and
backend-aware labels/diagnostic wording.

Handoff: `handoff/P27-swap-refault-aliases.md`.
Report: `handoff/reports/P27-REPORT.md`.

### P28 - I/O Cap Saturation

Status: done. P28 populates the existing diagnostics input
`io_cap_saturation_pct` from `io.max` and I/O rate counters, leaving
network-loss attribution as the remaining diagnostics input gap.

Handoff: `handoff/P28-io-cap-saturation.md`.
Report: `handoff/reports/P28-REPORT.md`.

### P29 - Inspect-Files Safety Skeleton

Status: done. P29 adds a disabled-by-default, read-only file/log inspection
planning module (`groop inspect-files plan`) with explicit --inspect-files
and --admin gating, three allowlisted plan kinds (docker-json-log,
systemd-journal, cgroup-files), deterministic JSON/text rendering, path/argv
safety validation, and structural no-subprocess/no-file-read guarantees.

Handoff: `handoff/P29-inspect-files-safety-skeleton.md`.
Report: `handoff/reports/P29-REPORT.md`.

### P30 - Daemon Default Client UX

Status: done. P30 makes `--attach` use the packaged default socket
(`/run/groop/groop.sock`) when no explicit path is given, and adds
`groop daemon current --socket PATH [--pretty-json]` as a read-only one-frame
retrieval command. No install execution, systemd mutation, protocol changes,
or daemon-side privilege changes.

Handoff: `handoff/P30-daemon-default-client.md`.
Report: `handoff/reports/P30-REPORT.md`.

### P31 - Daemon Client Error Guidance

Status: done. P31 adds a shared `_format_daemon_error()` helper that preserves
original error text and adds actionable next steps: preflight/install-plan for
the default socket, preflight --socket for custom sockets, and compatible-daemon
guidance for protocol/response errors. Both `--attach` and `daemon current` use
the same helper.

Handoff: `handoff/P31-daemon-client-error-guidance.md`.
Report: `handoff/reports/P31-REPORT.md`.

### P32 - Daemon Status Command

Status: done. P32 adds a read-only `groop daemon status` command that combines
P22 preflight checks with a P30/P31 current-frame protocol check, so non-root
users can tell whether the default daemon deployment is usable without falling
back to live collection.

Handoff: `handoff/P32-daemon-status-command.md`.
Report: `handoff/reports/P32-REPORT.md`.

### P33 - Release Smoke Harness

Status: done. P33 adds a rootless `python -m groop.acceptance smoke` module for
repeatable safe-path release evidence: one-frame collection, serialization,
optional replay summary, wall/CPU/RSS measurement, and paste-friendly JSON/text
output.

Handoff: `handoff/P33-release-smoke-harness.md`.
Report: `handoff/reports/P33-REPORT.md`.

### P34 - Host Device Banner

Status: done. P34 adds host-level per-device network and block-device rate
summaries to the system banner using `Frame.host_meta`, without claiming
per-cgroup attribution. It intentionally keeps block/network fixture data
deterministic and excludes `loop*`, `ram*`, `zram*`, `veth*`, bridge, docker,
and loopback devices from the banner summary.

Handoff: `handoff/P34-host-device-banner.md`.
Report: `handoff/reports/P34-REPORT.md`.

### P35 - Acceptance Steady Harness

Status: done. P35 extends the P33 acceptance module with a rootless
multi-sample collector loop that records wall/CPU/RSS evidence and optional
threshold checks. This is collector steady-state evidence, not a replacement for
the final live Textual TUI measurement.

Handoff: `handoff/P35-acceptance-steady-harness.md`.
Report: `handoff/reports/P35-REPORT.md`.

### P36 - CPU Sparkline Surface

Status: done. P36 adds stable ASCII CPU trend sparklines using existing
UI history data through a `cpu_trend` virtual table column, improving the quick
trend-read promised by the spec without changing collector/model contracts.

Handoff: `handoff/P36-cpu-sparkline-surface.md`.
Report: `handoff/reports/P36-REPORT.md`.

### P37 - Network Loss Diagnostics

Status: done. P37 adds host/interface-scoped drop/error diagnostics from
`/proc/net/dev`, NET banner LOSS annotations, and a root-entity diagnostic
finding while keeping exact per-cgroup attribution reserved for v2 BPF/daemon
work.

Handoff: `handoff/P37-network-loss-diagnostics.md`.
Report: `handoff/reports/P37-REPORT.md`.

### P38 - TUI Smoke Evidence Harness

Status: done. P38 adds a rootless `python -m groop.acceptance tui-smoke`
command that exercises the existing Textual `--ui-smoke` path in a child
process, records wall/CPU/RSS evidence, and preserves the acceptance module's
no-Textual-import-on-import contract.

Handoff: `handoff/P38-tui-smoke-evidence.md`.
Report: `handoff/reports/P38-REPORT.md`.

### P39 - Release Readiness Ledger

Status: done. P39 adds a canonical release-readiness document mapping
`TUI-SPEC.md` §9 gates to tests, acceptance commands, measurements, and
remaining manual live-host evidence. This is the last planned v1/v1.5 release
confidence package before manual live-host evidence capture.

Handoff: `handoff/P39-release-readiness-ledger.md`.
Report: `handoff/reports/P39-REPORT.md`.

### P40 - Textual 8 Test Compatibility

Status: done. P40 replaces direct dependence on the removed `Static.renderable`
attribute with a version-compatible `_static_text()` helper using the public
`Static.render()` method. All 23 UI
tests pass under Textual 8.2.8 / Python 3.14 without weakening behavior
assertions or adding version skips/xfails.

Handoff: `handoff/P40-textual-8-test-compatibility.md`.
Report: `handoff/reports/P40-REPORT.md`.

### P23 - ZRAM Per-Device Drill-Down

Status: done. P23 preserves per-device zram state as structured host-level frame
metadata (`Frame.host_meta["zram_devices"]`) and renders it in the host-memory
surface. It does not claim per-cgroup zram compression or physical-memory
attribution, because the kernel does not expose those values per cgroup.

Handoff: `handoff/P23-zram-device-drilldown.md`.
Report: `handoff/reports/P23-REPORT.md`.

## Medium Term

### P16 — Daemon Read Broker For Non-Root Full Reads

Status: done as a spike. P16 added a read-only Unix-socket JSON-lines broker,
current/stream protocol, bounded in-memory history, socket tests, and daemon
threat-model docs.

Remaining work: authorization hardening on a real host and any production
packaging beyond the packaged templates plus P25 install plan.

### P17 — BPF Measurement Gate

Status: done. The safe unprivileged measurement helper and design doc landed,
and `MEASUREMENTS.md` now records the live-BPF blocker on this host.

### P18 — BPF Network Provider

Goal: implement exact per-cgroup socket counters behind the existing provider
interface, owned by daemon/helper state rather than by the TUI.

### P20 — TUI Attach Mode

Status: done. `groop --attach <socket>` now consumes daemon frames over the
P16 socket protocol, preserves the same UI model as standalone live mode, and
supports `--once --json` plus UI smoke.

Handoff: `handoff/P20-daemon-attach-mode.md`.

### P22 — Daemon Deployment Preflight

Status: done. `groop daemon preflight`, packaged systemd/tmpfiles templates,
and the deployment checklist landed for deliberate root-daemon setup with a
group-readable socket.

Handoff: `handoff/P22-daemon-deployment-preflight.md`.

Remaining work: any extra host-specific hardening the operator wants on top of
the read-only socket boundary and P25 install plan.

### P25 - Daemon Deployment Install Plan

Status: done. P25 renders a safe, non-mutating install plan for the
packaged systemd and tmpfiles templates so operators can deploy the root daemon
deliberately and then verify it with P22 preflight.

Handoff: `handoff/P25-daemon-install-plan.md`.
Report: `handoff/reports/P25-REPORT.md`.

## Later

### P21 — Admin Action Gating Skeleton

Status: done. P21 adds disabled-by-default, preview-only admin action planning
with explicit `--admin`, exact argv previews, and optional audit logging,
without executing Docker/systemd commands.

Handoff: `handoff/P21-admin-action-gating-skeleton.md`.

- Real Docker/systemd action execution.
- `systemctl set-property` governance edits.
- Docker/CIU action integration.
- File/log/content inspection behind explicit `--inspect-files`.
- GPU and ZFS optional providers.
- Web UI over daemon API.

## Open Product Decisions

- Is v1.5 allowed to ship with CLI-only DAMON start and TUI notices, or must the
  full modal land before a release tag?
- How important is exact BPF network accounting compared with improving
  diagnostics, snapshots, and UI usability?
- Should `groop` target a local package release first (`pipx` from wheel), or
  remain a repo-local tool until daemon/BPF work starts?
