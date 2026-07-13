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
groop report groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
groop report groop/tests/fixtures/frames/gstammtisch-once.jsonl --json --window last:60s --group-by slice
groop squeeze --target /sys/fs/cgroup/system.slice/app.service --admin --confirm SQUEEZE
```

Use `--config PATH` to point at an alternate TOML config, `--profile NAME` to
override the active UI column profile for one run, and `--record FILE` to record
the live TUI stream to JSONL while you inspect it. For unattended (headless)
recording without the TUI, use `--record FILE --headless [--interval N]
[--duration S | --frames K]`. Headless mode drives the existing collector loop
without importing `textual`, so it works even when the UI dependencies are not
installed.

Use `--entities GLOB` (repeatable) to collect only entities whose `EntityKey`
matches a glob pattern, `--slice NAME` to include an entity subtree, and
`--metrics compact` to keep only the memory-gauge, PSI, and refault-rate metric
families. These flags apply to `--once`, the live TUI, and `--record` (both
TUI-driven and headless P53). They are rejected with `--replay` and `--attach`.

Use `--container NAME_OR_PREFIX` on `groop inspect-files plan/read --target` or
`groop action preview/execute --target` to resolve a Docker container name or
prefix to its cgroup path automatically instead of manually specifying
`--target`.

Use `groop report FILE --json` to compute a machine-readable steady-state
profile from a P2-format recording (JSONL or JSONL.zst). Per-entity p50/p95/max
for key memory/PSI gauges are computed, with derived rates from embedded raw
counters when the recorded live rate is ``None``. Use ``--window last:Ns`` to
restrict to the last N seconds of a recording, and ``--group-by slice`` to
roll entities up under their owning ``*.slice`` ancestor. This is the
steady-state profile input for the gstammtisch stack measurement program
(``scripts/gstammtisch-guide/plan-stack-resource-tuning.md`` PKG-3).

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
| P41 | Done | Rendered replay fidelity | v1 release confidence | Proves every recorded/replayed tick has byte-identical production-formatted cells at fixed width/profile. Handoff: `handoff/P41-rendered-replay-fidelity.md`. Report: `handoff/reports/P41-REPORT.md`. |
| P42 | Done | Daemon BPF snapshot bridge | v2 daemon/BPF | Safely translate an explicitly configured, already-pinned counter map into the existing P18 atomic snapshot contract; no load/attach/detach. Handoff: `handoff/P42-daemon-bpf-snapshot-bridge.md`. Report: `handoff/reports/P42-REPORT.md`. |
| P43 | Done | Current Textual dependency baseline | v1 packaging | Make normal installs resolve Textual 8.2.8 or newer without an artificial upper ceiling, backed by wheel/resolver and full-suite evidence. Handoff: `handoff/P43-textual-current-baseline.md`. Report: `handoff/reports/P43-REPORT.md`. |
| P44 | Done | Daemon-owned paddr lifecycle | v2 daemon/DAMON | Explicitly configured whole-host paddr is daemon-owned and audited; current-run sessions stop safely while verified adopted sessions remain persistent. Handoff: `handoff/P44-daemon-paddr-lifecycle.md`. Report: `handoff/reports/P44-REPORT.md`. |
| P45 | Done | Bounded inspect-files content | v2 inspection | Add gated, descriptor-confined, bounded regular-file reads for catalog-resolved Docker logs and cgroup files without arbitrary root reads. Handoff: `handoff/P45-inspect-files-bounded-content.md`. Report: `handoff/reports/P45-REPORT.md`. |
| P46 | Done | Admin action execution kernel | v2 actions | Execute only validated Docker/systemd start/stop/restart plans behind root, admin, typed-confirmation, strict timeout/argv, and mandatory fail-closed audit gates. Production audit is fixed at `/var/log/groop/actions.jsonl`; API-only fixture paths and injected runner/clock/identity seams are test-only. Handoff: `handoff/P46-admin-action-execution-kernel.md`. Report: `handoff/reports/P46-REPORT.md`. |
| P47 | Done | Daemon component health | v2 daemon | Thread-safe bounded collector/BPF/paddr health, strict `health-v1` protocol validation, and `groop daemon health [--json]`. Handoff: `handoff/P47-daemon-component-health.md`. Report: `handoff/reports/P47-REPORT.md`. |
| P48 | Done | Journald inspection snapshot | v2 inspection | Add a gated, fixed-argv, bounded non-following journal snapshot after P45. Handoff: `handoff/P48-inspect-files-journal-snapshot.md`. Report: `handoff/reports/P48-REPORT.md`. |
| P49 | Queued | systemd memory governance | v2 actions | Add structured, stale-safe `memory.high` set-property preview/execution after P46. Handoff: `handoff/P49-systemd-memory-governance.md`. |
| P50 | Done | Mouse table interactions | v1 UX | Textual-native one-click row drill-down and header sorting/reversal with keyboard parity, in-place refresh, alias handling, and live/replay fidelity coverage. Handoff: `handoff/P50-mouse-table-interactions.md`. Report: `handoff/reports/P50-REPORT.md`. |
| P51 | Done | Daemon sampling fan-out | v2 daemon | One request-independent producer serves fresh current frames and bounded sequenced history to non-consuming clients, with typed terminal/gap/shutdown state and P47 health integration. Handoff: `handoff/P51-daemon-sampling-fanout.md`. Report: `handoff/reports/P51-REPORT.md`. |
| P52 | Done | Versioned daemon read API | v2/v3 API | Add capability negotiation, bounded health/history/entity reads, sensitivity metadata, and peer identity for separate frontends. Handoff: `handoff/P52-versioned-daemon-read-api.md`. Report: `handoff/reports/P52-REPORT.md`. |
| P53 | **Done** | Headless record driver | v1.5 recording | CLI `--record --headless [--interval] [--duration|--frames]` drives the collector loop and RecordWriter without importing textual, with clean SIGINT/SIGTERM finalization, bounded stderr progress, and injectable signal tests. Report: `handoff/reports/P53-REPORT.md`. |
| P54 | **Done** | Steady-state report command | v1.5 recording | Add `groop report FILE [--window last:Ns\|all] [--group-by slice\|entity] --json` computing per-entity p50/p95/max for key gauges and deriving `_per_s` rates from embedded raw counters when the recorded live rate is `None`. Report: `handoff/reports/P54-REPORT.md`. |
| P55 | Done | Collector entity & metric filtering | v1.5/v2 recording | Add `--entities GLOB`/`--slice NAME` entity selectors and `--metrics compact` gauge subset at collection time, cutting sysfs reads and frame size for `--once` and any recording path. Compact also drops per-entity network/DAMON/governance blocks. Handoff: `handoff/P55-collector-entity-metric-filtering.md`. Report: `handoff/reports/P55-REPORT.md`. |
| P56 | **Done** | `groop squeeze` guided memory measurement | v2 actions | Add a guided, stepped `memory.high` squeeze that measures a cgroup's hot working set, with mandatory memory.high restore on exit/SIGINT and a groop-record-compatible JSONL log. Handoff: `handoff/P56-groop-squeeze.md`. Report: `handoff/reports/P56-REPORT.md`. |
| P57 | Done | Docker-name entity selectors | v1.5/v2 ergonomics | Add `--container NAME_OR_PREFIX`, resolved via the existing docker metadata join, wherever groop takes a cgroup-path/entity identifier. Handoff: `handoff/P57-docker-name-entity-selectors.md`. Report: `handoff/reports/P57-REPORT.md`. |
| P58 | Blocked (review) | Daemon MCP frontend | v2/v3 API | Add `groop mcp serve` (optional `groop[mcp]` extra, stdio transport): a read-only MCP server over the P52 typed adapter. Pass #2 REJECTED (not merged): 3 blockers — missing-extra exits 1 not 2, MAX_RESPONSE_BYTES cap never enforced but claimed, and the adapter hand-rolls its own socket/envelope path bypassing the P52 DaemonClient. Re-work needed; review on branch `feat/groop-p58-daemon-mcp-frontend` at `handoff/reports/P58-REVIEW.md`. Handoff: `handoff/P58-daemon-mcp-frontend.md`. |
| P59 | Queued | `--container` as an entity selector | v1.5/v2 ergonomics | Compose P57's `--container` name resolution into P55's `--entities`/`--slice` collection-path selectors (resolution moved into the collector sweep for post-enrich correctness). Handoff: `handoff/P59-container-entity-selector-composition.md`. |
| P60 | Queued | Free-form `--metrics` field/family list | v1.5/v2 recording | Generalize P55's `--metrics full\|compact` enum with an open comma-separated family/name selector, registry-validated, reusing the compact prune + block-drop path. Handoff: `handoff/P60-metrics-fieldlist-selector.md`. |
| P61 | Queued | Steady-state report threshold gating | v1.5 recording | Add repeatable `--assert GROUP:METRIC:STAT<=VALUE` to `groop report` (exit 1 on breach), evaluated over the already-computed P54 profile without recomputing it; absent group/metric and null STAT are breaches. Handoff: `handoff/P61-report-threshold-gating.md`. |
| P62 | Queued | Steady-state window auto-detection | v1.5 recording | Add `--window auto` to `groop report`: select the longest trailing window whose primary gauge coefficient-of-variation is within a pinned bound, then profile it via the existing P54 math. Serialize-with P61. Handoff: `handoff/P62-report-steady-state-autodetect.md`. |

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

## Standing package contracts (inherited by EVERY handoff)

Every handoff implicitly includes these; a handoff only restates one to
tighten it further. They are distilled from the controller corrections that
P1–P51 review commits actually had to make (see
`../docs/implementation-benchmark-P51.md` for the systematic study):

- **Input trust**: any value parsed from kernel/sysfs files, JSON frames,
  Docker/systemd metadata, or user input is runtime-validated
  (`isinstance`/`try-except`) before use. Blind `int()`/`float()` casts and
  `# type: ignore` used to silence a real type gap are review-rejected.
- **Error disclosure**: no raw exception text, secrets, or filesystem paths
  cross a socket, CLI, or tool-output boundary — typed, bounded errors only.
- **Bounds are enforced, then proven**: enumerate what each bound covers
  (per-request/per-client/aggregate); never silently clamp out-of-range
  values; every bound gets a test that actually violates it and asserts the
  observable outcome — verify the mechanism, not its constant.
- **Test seams are Python-API-only**: injectable seams (signals, readers,
  runners, clocks) must not surface as production CLI flags.
- **No hollow tests**: assert the behavioral contract on the observable
  artifact (rendered cells, written file re-parsed by the real reader,
  actual thread liveness), not mock-call bookkeeping; never weaken existing
  tests to make new code pass.
- **Golden fixtures**: if collector/frame output changes, regenerate affected
  golden recordings via the documented command in the same package.
- **Deterministic machine output**: sorted keys, explicit `json.dumps`
  separators, pinned float rounding wherever output is diffed or replayed.
- **Operator-facing commands/templates** are parameterized and render exactly
  what any preview/plan mode shows — no ad-hoc shell substitutions.
- **Hygiene**: ASCII by default; no dead code, unused imports, or leftover
  scaffolding in the final diff.
- **Gates**: focused tests AND the full suite with `-W error`, the full-suite
  command wrapped in `timeout` (a hung gate is a finding, never a pass);
  `py_compile` on changed files; `git diff --check`. The REPORT states which
  environment every result came from — an agent-env green suite is evidence,
  not the verdict; the controller's rerun decides.
- **Patch discipline**: additive, focused diffs; propose wholesale rewrites
  or doc restructuring in the REPORT instead of committing them.

## Handoff authoring guide (controller-side)

What separated near-clean packages (P26, P31) from heavy-repair ones
(P42–P51), per the review-commit history and the P51 four-model benchmark —
where all four models made the *same* omissions, proving spec gaps dominate
model choice:

- **Contracts over capabilities**: state invariants that must hold in
  failure/terminal states ("stop() returns only when the thread is dead"),
  not just feature lists. Implicit semantics WILL be filled in wrong, the
  same way, by every model.
- **One acceptance oracle per requirement**: name the exact assertion that
  proves it, preferably one that fails against the wrong mechanism (e.g. a
  sample count where nearest-rank and interpolation percentiles differ).
- **Name an in-repo exemplar** module/pattern to imitate; both near-clean
  packages had one.
- **Bound the context**: a "Context To Read First" list keeps a Flash-class
  agent from surveying the tree or, worse, improvising from the wrong file.
- **Number the adversarial tests** so completeness is checkable at review.
- **Scale richness to risk**: concurrency/protocol/privilege slices get the
  full optimized-P51 treatment; a narrow, fixture-testable, exemplar-backed
  slice stays lean — over-specification inflates patches (the optimized-P51
  run produced an 18-file rewrite whose reconciliation ate its quality gain).
- **Declare out-of-scope explicitly** — silence reads as permission.
- **Stamp the machine-readable header** (workflow v2): every carved handoff
  starts with a `Tier / Depends-on / Base / Session-hint / Serialize-with /
  Escalate-if` block per `../docs/controller-workflow-v2.md` §7. The
  controller parses headers only — it never reads handoff bodies. Keep ≥5
  planned handoffs carved ahead when scoped work exists; respect the
  `.CARVE_LOCK` protocol (v2 §8).

## Standing escalation rule (BLOCKED exits)

Escalation is **mechanical, not introspective** — do not ask an agent to
reflect on whether a task suits its expertise (P51: four models, identical
omissions, zero flagged uncertainty). Instead, every implementation agent
inherits this trigger rule:

> If a named contract cannot be met as specified, or the work requires
> touching files the handoff forbids, or an `Escalate-if:` condition in the
> handoff header fires: STOP. Write `BLOCKED: <reason>` to the LOG, commit
> the branch as-is, and exit. Do not improvise a workaround.

A BLOCKED exit is a first-class outcome the controller routes to a higher
tier — it is what makes cheap-model-first dispatch safe.

## Self-review pass (pass #1 — advisory, never a merge gate)

After the implementation commit, the controller resumes the SAME agent
session (`reasonix run -c` / `opencode run -s <id>` / `codex resume`) with
this standing prompt (handoffs may append package-specific probes under a
`Self-review probes:` heading — do not restate this template per handoff):

> Implementation is committed. Now switch roles: you are reviewing your own
> diff against the handoff. Do not re-read your reasoning — read the DIFF.
> Check mechanically, and fix what you find, committing fixes separately:
> 1. Every gate command in the handoff was actually run, in the required
>    environment, and the REPORT quotes real output (no reconstructed
>    numbers, no future-tense claims like "will pass after merge").
> 2. Every file in the diff is inside the declared scope; nothing in scope
>    was silently skipped (walk the handoff's numbered requirements 1-by-1).
> 3. Every numbered adversarial test exists and asserts the OBSERVABLE
>    outcome, not mock bookkeeping. Name any test that would still pass if
>    the mechanism under test were deleted — that is a hollow test; fix it.
> 4. Dates, counts, and paths in LOG/REPORT are real (today is <DATE>).
> 5. LOG, REPORT present; ASCII; no dead code/scaffolding in the diff.
> Write findings (including "none") to handoff/reports/P<NN>-SELFREVIEW.md,
> commit to the feature branch.

Known limit (set expectations, and measure): same-session self-review has the
maximum correlated blind spot. **Trial metric:** the frontier pass #2 records
`flagged-by-pass-1: yes/no` per finding in the REPORT; after ~4 packages, if
overlap <~25%, demote this pass to a plain checklist runner or drop it.

## Reference deployment

gstammtisch (Debian 13, cgroup v2, zswap, Pterodactyl/Wings game server).
Degradation on other hosts must be graceful (spec §6.3), but no distro matrix
work before v2 (spec §10).
