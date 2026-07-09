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
- `MEASUREMENTS.md` — acceptance and overhead evidence ledger. BPF defaults,
  DAMON default increases, and release claims should be blocked on this file.
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
| P5 | Done with UX gaps | Textual UI shell | v1 | Table/tree/drill/help exist; tree expand/collapse and richer replay controls remain future UX work. Report: `handoff/reports/P5-REPORT.md`. |
| P6 | Done with input gaps | Diagnostics engine | v1 | Pressure score/rules exist; true IO saturation and attributable network loss await richer providers. Report: `handoff/reports/P6-REPORT.md`. |
| P7 | Integrated, not release-certified | v1 integration + packaging | v1 | Full suite and editable install passed; spec §9 perf/RSS/pipx evidence still belongs in `MEASUREMENTS.md`. Report: `handoff/reports/P7-REPORT.md`. |
| P8 | Done | DAMON passive | v1.5 | Read-only vaddr attribution and host paddr detection. Report: `handoff/reports/P8-REPORT.md`. |
| P9 | Core done, UI modal pending | DAMON controlled vaddr session | v1.5 | CLI/API and ownership safety are covered; full Textual typed-confirmation modal and real-root acceptance remain open. Report: `handoff/reports/P9-REPORT.md`. |
| P10 | Done with enrichment gaps | Incident snapshots | v1.5 | Bundles, manifest, CLI inspect, TUI hotkey; live systemctl/docker enrichment can improve snapshots. Report: `handoff/reports/P10-REPORT.md`. |
| P11 | Core done, UI modal pending | DAMON paddr host heat | v1.5 | Host heat metrics/banner/status and CLI/API start exist; full modal and live-root acceptance remain open. Report: `handoff/reports/P11-REPORT.md`. |
| P12 | Done | Release hardening, acceptance evidence, packaging | v1/v1.5 | Tests, compile, fixture JSON, replay smoke, build, wheel install, and version checks recorded. Report: `handoff/reports/P12-REPORT.md`. |
| P13 | Done | UI navigation, replay controls, reserved action UX | v1 | Tree collapse/expand, replay status/controls, disabled v2 action messaging, profile warnings. Report: `handoff/reports/P13-REPORT.md`. |
| P14 | Done with live-root gap | DAMON control modal and live-root acceptance | v1.5 | TUI typed-confirmation modals and fixture safety tests landed; live-root acceptance remains deliberate test-host work. Report: `handoff/reports/P14-REPORT.md`. |
| P15 | Done with progress gap | Incident snapshot enrichment and UX | v1.5 | Fresh systemctl/docker metadata, richer inspect output, redaction/docs/tests; progress UI remains future polish. Report: `handoff/reports/P15-REPORT.md`. |
| P19 | Done with drill-down gap | ZRAM and swap-backend awareness | v1.5 | Host backend classification, ZRAM metrics, banner/docs/tests landed; per-device drill-down remains future polish. Report: `handoff/reports/P19-REPORT.md`. |
| P16 | Done as spike | Daemon read broker for non-root full reads | v1.5/v2 foundation | Read-only Unix-socket JSONL broker, tests, and daemon docs landed; attach mode/package unit remain future work. Report: `handoff/reports/P16-REPORT.md`. |
| P17 | Proposed | BPF provider measurement gate and design | v2 foundation | Benchmarks and design before implementation/defaults. Handoff: `handoff/P17-bpf-provider-measurement-gate.md`. |
| P18 | Proposed | Exact BPF network provider | v2 | Implement `net:BPF` after P16/P17 evidence. Handoff: `handoff/P18-bpf-provider-implementation.md`. |

## Completed Package Order

P1 was merged first, P2–P6 built v1, P7 integrated v1, and P8–P11 added v1.5
DAMON/snapshot work. New work should branch from current `main` using the same
worktree protocol below.

## Workflow protocol (every package agent MUST follow this)

- **Worktree + branch**: work in a dedicated git worktree on a feature branch
  named `feat/groop-<pkg>-<slug>`, e.g.
  `git worktree add -b feat/groop-p1-collector /tmp/vbpub-groop-p1-collector main`.
  The worktree MUST be outside this main checkout, under `/tmp`, and MUST branch
  from local `main`. Never commit package work directly to `main`.
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
