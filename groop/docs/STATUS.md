# groop Implementation Status

This file describes the current implementation state against `TUI-SPEC.md`.
It is intentionally separate from the spec: the spec says where the product is
going; this file says what is true in the code today.

## Summary

Current state: **v0 complete, v1 mostly implemented, v1.5 mostly implemented,
v2 started as daemon/BPF foundation work.**

Approximate status:

| Release cut | Feature implementation | Release confidence | Notes |
|---|---:|---:|---|
| v0 collector proof | 100% | high | Collector/model/registry/`--once --json` are implemented and tested. |
| v1 read-only TUI | 90-95% | medium | Core daily triage works. P33/P35/P38 provide rootless acceptance harnesses and P39 adds the canonical readiness document. P40 restores the green full suite under the managed Textual 8 environment. P41 automates strict rendered replay fidelity (383 passing tests plus one optional skip). Isolated local-artifact pipx/no-config acceptance now passes. Strict live performance and non-root gates remain. |
| v1.5 DAMON/snapshots/backend awareness | 90-95% | medium | Passive/control APIs, CLI paths, TUI typed-confirmation modals, snapshots, and ZRAM/swap-backend awareness with per-device drill-down exist with fixture tests. Real-root acceptance still needs a deliberate test host. |
| v2 daemon/BPF/admin actions | 50-55% | low | Provider abstractions, safety patterns, a read-only Unix-socket daemon spike, daemon attach mode (including default-socket attach), daemon deployment preflight/templates/status, preview-only admin action planning, the BPF measurement/design gate, the BPF provider read side, the inspect-files safety skeleton, and daemon current/status commands exist; live BPF attach/snapshot writing, executable admin actions, GPU/ZFS plugins are not implemented. |

These percentages are engineering estimates, not release tags. The strongest
claim the repo can currently make is: **feature-complete prototype for v1/v1.5
core workflows, not yet production-certified.**

## Implemented

- Cgroup v2 collector with reset-safe rates and graceful unavailable/unlimited
  source labels.
- Canonical frame model and registry-backed metric validation.
- Docker metadata join for `docker-<64hex>.scope` entities.
- Host facts: meminfo, load, uptime, PSI, zswap fallback/debugfs support, and
  legacy disk-swap estimate.
- Process drill-down from `cgroup.procs` and `/proc/<pid>`.
- Record/replay using headered JSONL, plus optional zstd when available.
- Fixed-capacity numeric history ring.
- Network provider abstraction with host truth and netns approximation.
- Origin/drift detection for systemd-managed values versus live cgroup files.
- Pressure score and rule-based findings.
- Textual TUI with tree/container views, profiles, sorting, filtering, banner,
  entity drill-down, glossary, snapshot hotkey, and host-memory screen.
- Passive DAMON vaddr attribution and paddr host-session detection.
- Controlled DAMON vaddr and paddr start APIs/CLI with root guards, typed
  confirmation, ownership markers, and audit logs.
- Incident snapshots with bounded frame capture, raw cgroup copies, provider
  status, manifest hashes, redaction, and `groop snapshot inspect`.
- Read-only Unix-socket daemon broker spike with current/stream protocol and
  socket tests.
- `groop --attach SOCKET` client mode with current-frame polling, `--once
  --json`, and UI smoke coverage.
- Daemon deployment preflight plus packaged systemd/tmpfiles templates for a
  root-owned, group-readable socket deployment.
- `groop daemon install-plan` command that renders a safe, non-mutating install
  plan for the packaged systemd and tmpfiles templates, with deterministic JSON
  and human-readable text output.
- Preview-only admin action planning for allowlisted Docker/systemd actions,
  gated by explicit `--admin` and optional JSONL audit logging.
- Safe BPF network accounting gate (`groop bpf gate`) and v2 BPF design doc;
  the gate is no-op and never loads or pins BPF state.
- Swap/refault terminology aliases layer (`groop/ui/aliases.py`) resolving
  `swap_dev` -> `swap_disk` and `rf_dev_per_s`/`rf_dev`/`rf_d` ->
  `rf_d_per_s` in configured profiles, with backend-aware display labels (`SWAP_DEV`,
  `RF_DEV/S`) and diagnostic wording that avoids overclaiming physical disk
  on zram/mixed hosts.
- Disabled-by-default, read-only file/log inspection planning module
  (`groop/src/groop/inspect_files/`) with explicit --inspect-files and --admin
  gating and deterministic JSON/text plans via `groop inspect-files plan`.
- Default-socket daemon attach (`--attach` with no path defaults to
  `/run/groop/groop.sock`) and `groop daemon current --socket PATH
  [--pretty-json]` one-frame read-only daemon command.
- Daemon client error guidance with actionable next steps: preflight/install-plan
  for default socket failures, preflight --socket for custom socket failures,
  and compatible-daemon/log guidance for protocol/response errors.
- `groop daemon status` read-only deployment/protocol check combining preflight
  results with one current-frame daemon protocol request.
- Rootless release smoke harness via `python -m groop.acceptance smoke`, with
  one-frame collection, serialization, optional replay summary, and wall/CPU/RSS
  measurements.
- Rootless steady-state collector harness via `python -m groop.acceptance steady`,
  with multi-sample wall/CPU/RSS measurement, entity-count bounds, threshold
  checks, JSON/text output, and collection-error reporting.
- CPU trend ASCII sparkline surface (`cpu_trend` column) using existing
  HistoryRing data, rendered as compact ASCII sparkline in entity table
  profiles at sufficient width, plus a reusable `groop/ui/sparkline.py`
  helper for ASCII-only trend rendering.

## Partially Implemented

- **System banner / trend surface:** host verdict, pressure summary, paddr heat,
  per-device network/disk rate summaries (P34), and host/interface LOSS
  annotations (P37) exist. CPU trend sparklines are implemented in the entity
  table via P36; banner-level CPU breakdown sparklines remain optional polish.
- **Compressed swap:** zswap host/cgroup metrics, host ZRAM totals,
  `/proc/swaps` backend classification, mixed-backend banner wording, and
  per-device ZRAM drill-down are implemented. Backend-aware aliases and
  diagnostic wording landed in P27. See
  `docs/COMPRESSED-SWAP.md`.
- **Tree view:** full tree rendering and expand/collapse state exist.
- **Replay UI:** replay feeds the same UI with mode/status, pause/resume,
  stepping, speed controls, first/last jump, frame/timestamp jump prompt,
  and smoke testing. Timestamp jump controls landed in P24.
- **Custom profiles:** named profile lists work and unsupported configured
  columns are surfaced as ignored metadata.
- **Diagnostics inputs:** pressure score, `io_cap_saturation_pct`, and
  host/interface-level network drop/error diagnostics (P37) work; attributable
  per-cgroup network loss remains v2 BPF work.
- **DAMON controls:** underlying APIs, CLI, and TUI typed-confirm modals are
  fixture-tested. Live-root acceptance still needs a deliberate test host.
- **Snapshots:** snapshot bundles include bounded frame history, cgroup files,
  provider status, fresh systemctl/docker metadata where available, redaction,
  CLI inspect, hash verification, and a nonblocking progress/status UI with
  duplicate-start guard (P26).
- **Acceptance evidence:** P12 records tests, packaging, fixture JSON, replay
  smoke, wheel install, version, and bounded once/json CPU/RSS. P33 adds a
  repeatable rootless acceptance smoke command. P35 adds a rootless multi-sample
  collector steady harness. P38 adds a rootless TUI smoke evidence harness via
  subprocess. P39 adds the canonical release-readiness document mapping §9
  gates to evidence sources. P17 records the safe BPF gate and current live-BPF
  blocker. P18 records the fixture-tested BPF provider implementation.
  P40 removed the Textual 8 full-suite blocker, and P41 closes automated
  rendered replay fidelity. `MEASUREMENTS.md` still needs strict live TUI
  performance, controlled drift, and docker-group non-root evidence.
  DAMON/daemon live evidence is required
  when those controlled/deployed capabilities are claimed; privileged BPF
  measurements are required before enabling BPF by default.
- **BPF network provider:** P18 implements the userspace BPF provider reading
  pinned-map JSON snapshots with cgroup-id-to-entity-key mapping, fallback, and
  fixture tests. The live BPF ownership lifecycle (daemon attach/pin/detach) and
  kernel BPF program compilation are still daemon work and not implemented.

## Not Implemented

- Production daemon installation execution and service hardening beyond the
  packaged operator templates plus safe P25 install plan.
- Live BPF ownership lifecycle (daemon/helper attach, pin, detach).
- Executable Docker/systemd admin actions: update/start/stop/restart/kill.
- `systemctl set-property` governance actions.
- Web UI.
- GPU and ZFS optional plugins.
- CIU stack grouping/actions.
- paddr auto-start / persistent daemon-owned paddr mode.

## Acceptance Status

| Spec §9 item | Current status |
|---|---|
| 1. CPU performance | Bounded once/json CPU smoke recorded in P12; P35 rootless multi-sample collector steady harness exists and has fixture evidence; required 5-minute steady-state Textual TUI run still needed. |
| 2. Memory budget | Bounded once/json max RSS recorded in P12; P35 steady harness records collector RSS; live Textual TUI RSS measurement still needed. |
| 3. Counter reset handling | Covered by tests. |
| 4. Finding-D raw-write drift | Covered by tests; live destructive acceptance not run. |
| 5. Non-container visibility | Covered by fixtures and UI tests. |
| 6. Graceful degradation | Covered by focused tests; more host matrix evidence would help. |
| 7. Registry semantics | Covered by registry/model tests and branch-policy labels. |
| 8. Diagnostics | Covered by tests; host/interface network loss is covered by P37. Exact per-cgroup network-loss attribution remains v2 BPF work. |
| 9. Network labels | Covered by provider tests. |
| 10. Record/replay fidelity | P41 compares row keys, column identities, and every production-formatted plain-text cell for three annotated ticks returned by `ReplayDriver.play(step=True)`. JSONL passes; compressed JSONL is the same parametrized gate and skips when optional zstandard is absent. |
| 11. Packaging | P12 built sdist/wheel and verified fresh-venv install; post-P40 controller evidence adds the required isolated local-wheel pipx install, version check, and empty-directory no-config replay smoke. |
| 12. v2 gating | Explicit admin-preview gating landed in P21: `groop action preview` with `--admin` required, no-execution guarantee, audit logging, and TUI reserved-key disabled messaging in P13. |
| 13. Unprivileged smoke | P33 provides `python -m groop.acceptance smoke`, P35 provides `python -m groop.acceptance steady`, and P38 provides `python -m groop.acceptance tui-smoke` for repeatable rootless safe-path evidence; fresh live-host results should be pasted into `MEASUREMENTS.md` before a release claim. |
| 14. Measurement gates | `MEASUREMENTS.md` records the P17 safe BPF gate and blocker; DAMON overhead and privileged live-BPF overhead gates are not recorded. |

## Current Quality Gate

Most recent full-suite validation after P41:

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 383 passed, 1 skipped in 47.81s
```

Also validated:

- Focused table/record/fidelity tests: `19 passed, 1 skipped in 9.57s`.
- Focused rendered fidelity tests: `1 passed, 1 skipped in 0.27s`.
- Focused acceptance tests: `40 passed in 7.28s`.
- P41 TUI smoke: exit `0`, `ok: true`, `frames: 1`, `view: tree`, `profile: auto`.
- Full-source `py_compile`.
