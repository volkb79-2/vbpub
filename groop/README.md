# groop — host pressure inspector and cgroup forensics TUI

Implementation home for the tool specified in
`TUI-SPEC.md` (the **spec**; §-references in all handoff docs point there).
Read `CONTRACTS.md` before writing any code — it defines the interfaces every
package codes against.

Release cut (spec §0.1): **v0** collector proof → **v1** read-only TUI →
**v1.5** DAMON → **v2** BPF/daemon/actions. This directory now carries the
implemented v0/v1/v1.5 package slices; v2 remains roadmap work.
Stack: Python; Textual allowed ONLY under `src/groop/ui/` (spec §6.1, §6.4).

## Canonical documents

- `TUI-SPEC.md` — product intent and release-cut source of truth.
- `CONTRACTS.md` — frozen developer contracts for model, registry, providers,
  config, recording, and degradation behavior.
- `docs/STATUS.md` — current implementation state versus the spec.
- `docs/ROADMAP.md` — suggested next product slices and sequencing.
- `docs/ARCHITECTURE.md` — current dataflow and module map.
- `docs/OPERATIONS.md` — runbook for using groop safely today.
- `docs/COMPRESSED-SWAP.md` — zswap/zram/disk/mixed backend policy and metric
  semantics.
- `docs/BPF-NETWORK-ACCOUNTING.md` — v2 exact network accounting design and
  measurement-gate constraints.
- `MEASUREMENTS.md` — acceptance and overhead evidence ledger. BPF defaults,
  DAMON default increases, and release claims should be blocked on this file.
- `docs/RELEASE-READINESS.md` — release-candidate readiness surface mapping
  `TUI-SPEC.md` §9 gates to evidence sources, live-host templates, and
  explicit non-claims.
- `handoff/*.md` — implementation briefs. Completed packages also have
  `handoff/reports/*-REPORT.md`.

## Quickstart

```bash
pip install -e groop/
groop --once --json
groop
groop --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step
groop snapshot inspect /path/to/groop-incident-*.tar
```

Use `--config PATH` to point at an alternate TOML config, `--profile NAME` to
override the active UI column profile for one run, and `--record FILE` to record
the live TUI stream to JSONL while you inspect it.

Useful feature hotkeys in the TUI:

- `F5` / `t` toggles tree vs. container view.
- `p` cycles column profiles.
- `F6` / `s` cycles sort.
- `/` filters rows.
- `Enter` opens entity drill-down.
- `x` writes an incident snapshot for the selected row.
- `m` opens host-memory / paddr DAMON status.
- `F1` / `?` opens generated registry help.

## Work packages

| Pkg | Status | Title | Cut | Notes |
|-----|--------|-------|-----|-------|
| P1 | Done | Collector core + metric registry (`--once --json`) | v0 | Established model/registry/cgroup collector and fixture frame. Report: `handoff/reports/P1-REPORT.md`. |
| P2 | Done | Record / replay / history ring | v1 | Headered JSONL, optional zstd, compact numeric ring. Report: `handoff/reports/P2-REPORT.md`. |
| P3 | Done | Network providers (host truth + netns) | v1 | Provider abstraction is in place; BPF remains v2. Report: `handoff/reports/P3-REPORT.md`. |
| P4 | Done | Origin / drift detection | v1 | Finds raw-write/systemd drift and effective memory.min. Report: `handoff/reports/P4-REPORT.md`. |
| P5 | Done with later UX polish | Textual UI shell | v1 | Table/tree/drill/help landed; tree collapse/replay controls were completed in P13/P24. Report: `handoff/reports/P5-REPORT.md`. |
| P6 | Done with input gaps | Diagnostics engine | v1 | Pressure score/rules exist; true IO saturation and attributable network loss await richer providers. Report: `handoff/reports/P6-REPORT.md`. |
| P7 | Integrated, not release-certified | v1 integration + packaging | v1 | Full suite and editable install passed; spec §9 perf/RSS/pipx evidence still belongs in `MEASUREMENTS.md`. Report: `handoff/reports/P7-REPORT.md`. |
| P8 | Done | DAMON passive | v1.5 | Read-only vaddr attribution and host paddr detection. Report: `handoff/reports/P8-REPORT.md`. |
| P9 | Core done, live-root gap | DAMON controlled vaddr session | v1.5 | CLI/API, ownership safety, and typed-confirmation UI are covered; real-root acceptance remains open. Report: `handoff/reports/P9-REPORT.md`. |
| P10 | Done with enrichment gaps | Incident snapshots | v1.5 | Bundles, manifest, CLI inspect, TUI hotkey; live systemctl/docker enrichment can improve snapshots. Report: `handoff/reports/P10-REPORT.md`. |
| P11 | Core done, live-root gap | DAMON paddr host heat | v1.5 | Host heat metrics/banner/status, CLI/API start, and typed-confirmation UI exist; real-root acceptance remains open. Report: `handoff/reports/P11-REPORT.md`. |
| P12 | Done | Release hardening, acceptance evidence, packaging | v1/v1.5 | Tests, compile, fixture JSON, replay smoke, build, wheel install, and version checks recorded. Report: `handoff/reports/P12-REPORT.md`. |
| P13 | Done | UI navigation, replay controls, reserved action UX | v1 | Tree collapse/expand, replay status/controls, disabled v2 action messaging, profile warnings. Report: `handoff/reports/P13-REPORT.md`. |
| P14 | Done with live-root gap | DAMON control modal and live-root acceptance | v1.5 | TUI typed-confirmation modals and fixture safety tests landed; live-root acceptance remains deliberate test-host work. Report: `handoff/reports/P14-REPORT.md`. |
| P15 | Done with progress gap | Incident snapshot enrichment and UX | v1.5 | Fresh systemctl/docker metadata, richer inspect output, redaction/docs/tests; progress UI remains future polish. Report: `handoff/reports/P15-REPORT.md`. |
| P19 | Done | ZRAM and swap-backend awareness | v1.5 | Host backend classification, ZRAM metrics, banner/docs/tests landed; P23 added per-device drill-down. Report: `handoff/reports/P19-REPORT.md`. |
| P16 | Done as spike | Daemon read broker for non-root full reads | v1.5/v2 foundation | Read-only Unix-socket JSONL broker, tests, and daemon docs landed; attach/deployment follow-ups completed in P20/P22/P25. Report: `handoff/reports/P16-REPORT.md`. |
| P17 | Done | BPF provider measurement gate and design | v2 foundation | Safe unprivileged gate plus design doc; no default behavior change. Report: `handoff/reports/P17-REPORT.md`. |
| P18 | Done with live lifecycle gap | Exact BPF network provider read side | v2 | Userspace-only BPF provider reads daemon-style map snapshots, maps cgroup ids to entity keys, and falls back cleanly; live attach/pin/snapshot writer remains future daemon work. Report: `handoff/reports/P18-REPORT.md`. |
| P20 | Done | Daemon attach mode for non-root clients | v1.5/v2 foundation | Consume P16 daemon frames via `groop --attach SOCKET`; `--once --json` and UI smoke are supported. Report: `handoff/reports/P20-REPORT.md`. |
| P21 | Done | v2 admin action gating skeleton | v2 foundation | Preview/audit-only admin action safety skeleton; no command execution. Report: `handoff/reports/P21-REPORT.md`. |
| P22 | Done | Daemon deployment preflight and service templates | v1.5/v2 foundation | Safe preflight and packaged operator templates for root daemon plus group-readable socket. Handoff: `handoff/P22-daemon-deployment-preflight.md`. Report: `handoff/reports/P22-REPORT.md`. |
| P23 | Done | ZRAM per-device drill-down | v1.5 polish | Structured host_meta metadata, host-memory rendering, serialization round-trip, docs/tests. Report: `handoff/reports/P23-REPORT.md`. |
| P24 | Done | Replay timestamp jump controls | v1 polish | Add replay first/last and frame/timestamp jump controls. Handoff: `handoff/P24-replay-timestamp-jump.md`. Report: `handoff/reports/P24-REPORT.md`. |
| P25 | Done | Daemon deployment install plan | v1.5/v2 foundation | Render safe operator install steps for the packaged daemon service/tmpfiles templates. Handoff: `handoff/P25-daemon-install-plan.md`. Report: `handoff/reports/P25-REPORT.md`. |
| P26 | Done | Snapshot progress UI | v1.5 polish | Make TUI snapshot creation visibly running, guarded against duplicate starts, and statused on success/failure. Handoff: `handoff/P26-snapshot-progress-ui.md`. Report: `handoff/reports/P26-REPORT.md`. |
| P27 | Done | Swap/refault terminology aliases | v1.5 polish | Preserve canonical `swap_disk`/`rf_d_per_s` metrics while adding backend-aware aliases and labels. Handoff: `handoff/P27-swap-refault-aliases.md`. Report: `handoff/reports/P27-REPORT.md`. |
| P28 | Done | I/O cap saturation metric | v1 diagnostics polish | Populate dormant `io_cap_saturation_pct` from `io.max` and I/O rate counters. Handoff: `handoff/P28-io-cap-saturation.md`. Report: `handoff/reports/P28-REPORT.md`. |
| P29 | Done | Inspect-files safety skeleton | v2 foundation | Add disabled-by-default, read-only file/log inspection planning module and CLI plan command. No content reads, no subprocess execution, no host mutation. Handoff: `handoff/P29-inspect-files-safety-skeleton.md`. Report: `handoff/reports/P29-REPORT.md`. |
| P30 | Done | Daemon default client UX | v1.5/v2 daemon usability | Default-socket daemon attach and one-frame daemon current command for non-root clients. Handoff: `handoff/P30-daemon-default-client.md`. Report: `handoff/reports/P30-REPORT.md`. |
| P31 | Done | Daemon client error guidance | v1.5/v2 daemon usability | Add actionable preflight/install-plan guidance to attach/current daemon client failures. Handoff: `handoff/P31-daemon-client-error-guidance.md`. Report: `handoff/reports/P31-REPORT.md`. |
| P32 | Done | Daemon status command | v1.5/v2 daemon usability | Add read-only `groop daemon status` combining deployment preflight and current-frame protocol checks. Handoff: `handoff/P32-daemon-status-command.md`. Report: `handoff/reports/P32-REPORT.md`. |
| P33 | Done | Release smoke harness | v1/v1.5 release confidence | Add rootless `python -m groop.acceptance smoke` for deterministic safe-path evidence. Handoff: `handoff/P33-release-smoke-harness.md`. Report: `handoff/reports/P33-REPORT.md`. |
| P34 | Done | Host device banner | v1 polish | Adds host-level per-device network and block-device rate summaries to the system banner via `host_meta`; no per-cgroup attribution claim. Handoff: `handoff/P34-host-device-banner.md`. Report: `handoff/reports/P34-REPORT.md`. |
| P35 | Done | Acceptance steady harness | v1/v1.5 release confidence | Adds rootless multi-sample collector CPU/RSS evidence via `python -m groop.acceptance steady`; live Textual TUI measurement remains separate release evidence. Handoff: `handoff/P35-acceptance-steady-harness.md`. Report: `handoff/reports/P35-REPORT.md`. |
| P36 | Done | CPU sparkline surface | v1 polish | Adds stable ASCII CPU trend sparklines from existing UI history via a `cpu_trend` table column. Handoff: `handoff/P36-cpu-sparkline-surface.md`. Report: `handoff/reports/P36-REPORT.md`. |
| P37 | Done | Network loss diagnostics | v1/v2 bridge | Adds host/interface-scoped drop/error parsing, banner LOSS annotations, and root-entity diagnostics while preserving the BPF attribution boundary. Handoff: `handoff/P37-network-loss-diagnostics.md`. Report: `handoff/reports/P37-REPORT.md`. |
| P38 | Done | TUI smoke evidence harness | v1/v1.5 release confidence | Adds rootless `python -m groop.acceptance tui-smoke` evidence over the existing Textual `--ui-smoke` path, with child wall/CPU/RSS measurements and import-contract coverage. Handoff: `handoff/P38-tui-smoke-evidence.md`. Report: `handoff/reports/P38-REPORT.md`. |
| P39 | Done | Release readiness ledger | v1/v1.5 release confidence | Add canonical release-readiness docs tying spec §9 gates to tests, acceptance commands, measurements, and remaining manual evidence. Handoff: `handoff/P39-release-readiness-ledger.md`. Report: `handoff/reports/P39-REPORT.md`. |
| P40 | Done | Textual 8 test compatibility | v1/v1.5 release confidence | Restore the full UI suite under the managed Textual 8 environment without weakening behavior assertions. Handoff: `handoff/P40-textual-8-test-compatibility.md`. Report: `handoff/reports/P40-REPORT.md`. |

## Completed Package Order

P1 was merged first, P2–P6 built v1, P7 integrated v1, and P8–P11 added v1.5
DAMON/snapshot work. New work should branch from current `main` using the same
worktree protocol below.

## Workflow protocol (every package agent MUST follow this)

- **Worktree + branch**: work in a dedicated git worktree on a feature branch
  named `feat/groop-<pkg>-<slug>`, e.g.
  `git worktree add -b feat/groop-p1-collector .worktrees/-groop-p1-collector main`.
  The worktree MUST live under the repo-root `.worktrees/` directory, using a
  path like `.worktrees/-groop-<pkg>-<slug>`, and MUST branch from local
  `main`. `.worktrees/` is gitignored; do not edit or commit package work
  directly in the main checkout.
- **Scope**: touch only `groop/**`. No edits to other vbpub areas, no host
  changes, no root, no docker mutations. The collector reads live
  `/sys/fs/cgroup` only in ad-hoc manual testing; automated tests use
  fixtures.
- **Contracts are frozen**: if your package needs an interface change in
  `CONTRACTS.md`, propose it in your report — do NOT silently change shared
  interfaces. Additive, package-private code is yours to shape.
- **Quality gates before handover**: `python3 -m pytest groop/tests -q` green;
  `python3 -m py_compile` clean on all new files; `groop --once --json`
  (or the package's own entry point) demonstrably runs.
- **Engineering bar**: keep package code modern, typed where it clarifies
  contracts, and DRY. Shared behavior belongs in `src/groop/` helpers, not in
  copied package-local parsers or serializers. Tests should cover behavior and
  edge cases, not just import smoke.
- **Handover**: finish with (a) focused commits on the feature branch, the
  last one summarizing the package; (b) a report file
  `groop/handoff/reports/<PKG>-REPORT.md` containing: what was built, deviations
  from the handoff doc, proposed contract changes (if any), test evidence
  (command + output tail), known gaps/open items; (c) your final message =
  that report, so review + merge can proceed without archaeology.
- **Resumability log**: every package must also keep
  `groop/handoff/reports/<PKG>-LOG.md` updated while working. Use
  `handoff/AGENT-LOG-TEMPLATE.md`. The log records actions, commands, files
  changed, decisions, blockers, and next steps; do not include private
  chain-of-thought. Update it before long-running tests and before handoff so a
  controller can resume safely after a session limit.
- **Controller review**: the session controller reviews the branch diff,
  validates the report, runs the relevant gates from a clean checkout, fixes or
  sends back issues, then merges to `main` with a focused merge/commit. Later
  packages branch only after their declared dependencies are merged.

## Reference deployment

gstammtisch (Debian 13, cgroup v2, zswap, Pterodactyl/Wings game server).
Degradation on other hosts must be graceful (spec §6.3), but no distro matrix
work before v2 (spec §10).
