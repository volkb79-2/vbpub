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
| v1 read-only TUI | 90-95% | medium | Core daily triage works. P33/P35/P38 provide rootless acceptance harnesses and P39 adds the canonical readiness document. P40 restores the green full suite under the managed Textual 8 environment. P41 automates strict rendered replay fidelity (383 passing tests plus one optional skip). P43 replaces the obsolete pre-1.0 resolver ceiling with textual>=8.2.8. Isolated local-artifact pipx/no-config acceptance now passes. Strict live performance and non-root gates remain. |
| v1.5 DAMON/snapshots/backend awareness | 90-95% | medium | Passive/control APIs, CLI paths, TUI typed-confirmation modals, snapshots, and ZRAM/swap-backend awareness with per-device drill-down exist with fixture tests. Real-root acceptance still needs a deliberate test host. |
| v2 daemon/BPF/admin actions | 65-70% | low | Provider abstractions, a read-only Unix-socket daemon, attach/deployment/status tooling, preview planning, validated Docker/systemd start/stop/restart execution, BPF gate/provider/snapshot bridge, bounded Docker/cgroup/journald inspect-files reads, and daemon-owned paddr lifecycle exist. Live BPF load/attach, broader actions, and GPU/ZFS plugins remain. |

P44 adds the daemon-owned paddr lifecycle — `[damon] paddr_enabled = true` starts
or adopts one groop-owned whole-host paddr session. Sessions created by the
current run stop on shutdown; verified adopted sessions persist. P45 adds
bounded descriptor-confined Docker/cgroup reads. P46 adds the narrowly
allowlisted executable admin kernel.

P47 adds a thread-safe daemon component health registry with byte-bounded,
redacted public error detail, a strictly validated read-only ``health-v1``
protocol operation, and
``groop daemon health [--json]``. The registry models collector, BPF snapshot
bridge, and paddr lifecycle states and wires P42/P44 transitions.

P48 adds a bounded journald inspection snapshot via fixed absolute
``/usr/bin/journalctl`` argv (``shell=False``), bounded timeout, and injectable
runner, extending the P45 inspect-files content read posture to systemd units.

P49 remains queued: structured `memory.high` governance through systemd.

P50 is done. The entity table now uses a MouseTable (DataTable subclass) with
clickable header sorting (toggle direction), one-click row drill-down and
highlight-driven selection,
native keyboard up/down/Enter, left/right tree collapse/expand, and stable
in-place refreshes. Twelve focused pilot tests use real mouse events for header
clicks, direction toggles, one-click row drill-down, empty rows, canonicalized
alias sorting, live/replay selection retention, and keyboard parity.

Daemon sampling is now request-independent with a background producer (P51):
`current` returns the latest published frame and changes as sampling advances;
`stream` reads from history with optional sequence/cursor. P52 adds a versioned,
bounded, peer-aware read API envelope over the P51 broker: a `hello`/negotiate
op, typed error codes, sensitivity metadata, `SO_PEERCRED` peer identity, an
injectable authorization hook, and proven resource bounds (request bytes, read
deadline, concurrent clients, response items/bytes). Legacy clients without a
`v` field continue to be served unchanged. Until P58 (MCP frontend) merges,
separate-frontend support is a protocol-complete but single-consumer claim.

P53 and P54 are queued, spec-only recording follow-ups: P53 adds a headless
`groop --record FILE --headless` driver reusing the existing P2
`RecordWriter`/frame stream with no `textual` import, and P54 adds `groop
report FILE --json` to compute a steady-state percentile/rate profile from a
P2-format recording. Until P53/P54 merge, unattended recording and
machine-readable steady-state profiles remain prototype-only claims.

P55 adds `--entities GLOB`/`--slice NAME`/`--metrics compact` collection-time
filtering and is **done** (see implemented section). P56 (``groop squeeze``)
remains queued: a guided stepped `memory.high` working-set measurement
absorbing the standalone `container-mempress.sh` workflow, gated through the
existing P21/P46 admin action posture with mandatory `memory.high` restore on
exit/SIGINT.

P57 adds `--container NAME_OR_PREFIX` docker-name resolution wherever groop
takes a cgroup-path/entity identifier today and is **done**. The resolver
lives in `groop/collect/dockerjoin.py` and resolves against already-enriched
`Entity.docker` metadata — no new Docker API calls. `--container` is wired
into `inspect-files plan/read --target` and `action preview/execute --target`
as a mutually exclusive alternative to `--target`.

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
- Read-only Unix-socket daemon broker with request-independent background
  producer, bounded sequenced history, non-consuming current/stream fan-out,
  sequence/cursor semantics, deterministic start/stop/join lifecycle (P51),
  and a versioned, bounded, peer-aware read API envelope with typed error
  codes, sensitivity metadata, SO_PEERCRED peer identity, an injectable
  authorization hook, and proven resource bounds (P52).
- `groop --attach SOCKET` client mode with current-frame polling, `--once
  --json`, and UI smoke coverage.
- Daemon deployment preflight plus packaged systemd/tmpfiles templates for a
  root-owned, group-readable socket deployment.
- `groop daemon install-plan` command that renders a safe, non-mutating install
  plan for the packaged systemd and tmpfiles templates, with deterministic JSON
  and human-readable text output.
- Preview-only admin action planning for allowlisted Docker/systemd actions,
  gated by explicit `--admin` and optional preview-only JSONL audit logging.
- Gated admin action execution kernel (`groop action execute`) for validated
  Docker/systemd start/stop/restart targets: typed `--confirm EXECUTE`,
  production root gate, strict container/unit validation, fixed absolute argv,
  bounded timeout and pipe-drained output, mandatory fail-closed durable
  `/var/log/groop/actions.jsonl` audit (pre/post records), typed post-audit
  partial outcomes, and injected runner/clock/identity fixtures for
  zero-mutation test safety.
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
- Bounded read API for allowlisted inspect-files content with no-follow
  opens, stat-verified regular-file checks, ``Path.is_relative_to()`` path
  confinement, bounded bytes/lines, safe ``surrogateescape`` decoding, and
  deterministic JSON/text output via ``groop inspect-files read``.
  Docker JSON logs require a full 64-char hex container ID; cgroup files use
  the catalog-defined allowlist. Both kinds are gated on
  ``--inspect-files`` and ``--admin`` (default: disabled).
- Bounded journald inspection snapshot via ``groop inspect-files read``
  with ``--kind systemd-journal``: fixed absolute ``/usr/bin/journalctl``
  argv, ``shell=False``, ``--unit``/``--no-pager``/``--output=short-iso``,
  bounded timeout (default 30s, max 60s), bounded output, injectable runner
  for tests. Rejects option-like unit names (starting with ``-``). Timeout
  and nonzero exit return typed error — never fallback to arbitrary reads.
  Gated on ``--inspect-files`` and ``--admin`` (default: disabled).
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
- Daemon-owned paddr lifecycle: when `[damon] paddr_enabled = true`, the root
  daemon owns one audited whole-host paddr session for its lifetime with
  idempotent restart (adopts existing groop-owned markers), bounded startup
  failure, foreign-session safety, and graceful shutdown that stops only the
  daemon-run's owned session.
- CPU trend ASCII sparkline surface (`cpu_trend` column) using existing
  HistoryRing data, rendered as compact ASCII sparkline in entity table
  profiles at sufficient width, plus a reusable `groop/ui/sparkline.py`
  helper for ASCII-only trend rendering.
- Textual-native interactive entity table (`groop/ui/data_table.py`):
  clickable column headers sort with direction toggle (^/v indicators);
  row highlight updates `selected_key`; row click or Enter opens the
  same drill-down screen; empty placeholder rows never open a drill-down;
  native DataTable cursor for up/down/Enter with keyboard parity;
  left/right delegated for tree collapse/expand; sort direction shown
  in both column headers and status line; cursor restored stably across
  live and replay refreshes.
- Collector-level entity and metric filtering (P55): ``--entities GLOB``
  (repeatable, fnmatch), ``--slice NAME`` (subtree selector), and ``--metrics
  compact`` (gauge-family subset) added to the top-level parser, the
  ``Collector``, and all ``Collector(...)`` call sites in ``cli.py``.
  Entity filtering skips ``collect_cgroup()`` (sysfs reads) for excluded
  entities, with ancestor auto-inclusion for path completeness. Metric
  filtering uses registry-defined ``METRIC_GROUPS``/``COMPACT_GROUPS`` and
  is applied as the final step after all annotations.
  31 focused tests covering glob matching, slice subtree inclusion, ancestor
  correctness, compact field-set precision, collection-time pruning, and
  replay/attach rejection.

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
  rendered replay fidelity. P43 replaces the obsolete pre-1.0 resolver ceiling
  (textual>=0.58,<1) with textual>=8.2.8 and adds packaging-metadata regression
  tests proving the lower bound and absence of upper cap. `MEASUREMENTS.md` still needs strict live TUI
  performance, controlled drift, and docker-group non-root evidence.
  DAMON/daemon live evidence is required
  when those controlled/deployed capabilities are claimed; privileged BPF
  measurements are required before enabling BPF by default.
- **BPF network provider:** P18 implements the userspace BPF provider reading
  pinned-map JSON snapshots with cgroup-id-to-entity-key mapping, fallback, and
  fixture tests. P42 adds the daemon-side ``BpfSnapshotBridge`` that reads
  pinned BPF counter maps via ``bpftool`` and writes the P18 ``snapshot.json``
  contract atomically to a separate ``state_dir`` (default ``/run/groop/bpf``,
  **not** the bpffs pin root) with path confinement (``Path.is_relative_to``),
  decoding, cgroup mapping, last-good preservation, ``CalledProcessError``/
  ``TimeoutExpired`` bounded conversion, explicit raw byte array rejection,
  on-disk last-good restoration, and integration of ``BpfProvider`` at highest
  rank into the daemon Collector when the bridge is enabled.

## Not Implemented

- Production daemon installation execution and service hardening beyond the
  packaged operator templates plus safe P25 install plan.
- Live BPF ownership lifecycle (daemon/helper attach, pin, detach).
- `systemctl set-property` governance actions.
- Web UI.
- GPU and ZFS optional plugins.
- CIU stack grouping/actions.
- paddr auto-start / persistent daemon-owned paddr mode.
- Headless (non-Textual) `--record` driver and `groop report` steady-state
  profile command (queued: P53, P54).
- Guided stepped `memory.high` squeeze measurement (`groop squeeze`)
  (queued: P56).

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
| 11. Packaging | P12 built sdist/wheel and verified fresh-venv install; post-P40 controller evidence adds the required isolated local-wheel pipx install, version check, and empty-directory no-config replay smoke. P43 changes the published dependency from textual>=0.58,<1 to textual>=8.2.8, verified by source metadata, built-wheel METADATA, clean resolver installation, and packaging-metadata regression tests. |
| 12. v2 gating | Explicit admin-preview gating landed in P21: `groop action preview` with `--admin` required, no-execution guarantee, audit logging, and TUI reserved-key disabled messaging in P13. P45 adds gated bounded content reads via `groop inspect-files read`, also disabled by default. |
| 13. Unprivileged smoke | P33 provides `python -m groop.acceptance smoke`, P35 provides `python -m groop.acceptance steady`, and P38 provides `python -m groop.acceptance tui-smoke` for repeatable rootless safe-path evidence; fresh live-host results should be pasted into `MEASUREMENTS.md` before a release claim. |
| 14. Measurement gates | `MEASUREMENTS.md` records the P17 safe BPF gate and blocker; DAMON overhead and privileged live-BPF overhead gates are not recorded. |

## Current Quality Gate

Most recent combined P51/P52 validation:

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
# (see P52-REPORT.md for the focused and full-suite tails)
```

Also validated:

- P52 focused envelope/bounds/leak tests: `55 passed`.
- P51 focused daemon/client/health tests: `20 passed`.
- P47 focused component health tests: `49 passed`.
