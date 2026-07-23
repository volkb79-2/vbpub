---
kind: product-definition
schema_version: 1
product_version: 1
features:
  - id: F001
    title: Collector core and metric registry (`--once --json`)
    acceptance:
      - When topos runs `--once --json` against fixture or live cgroup v2 data, it emits a schema-validated frame built entirely from registry-validated metric names.
    status: shipped
    milestone: M1
  - id: F002
    title: Reset-safe collector with graceful source degradation
    acceptance:
      - When a counter resets or a source is unavailable or unlimited, the collector reports a typed graceful state rather than a negative rate or a crash.
    status: shipped
    milestone: M2
  - id: F003
    title: Textual TUI navigation, banner, sparklines, and replay controls
    acceptance:
      - When an operator opens the TUI, tree/container view toggling, header-click sort, row drill-down, filtering, the host device banner, CPU sparklines, and replay controls (pause/step/speed/first/last/timestamp-jump) all work with keyboard and mouse parity.
    status: shipped
    milestone: M2
  - id: F004
    title: Record/replay fidelity over headered JSONL
    acceptance:
      - When a session is recorded (optionally zstd-compressed) and replayed, every returned tick's rendered cells are byte-identical to the original production-formatted output at a fixed profile and width, and a corrupt or truncated recording produces a typed exit-2 error rather than a raw traceback.
    status: shipped
    milestone: M2
  - id: F005
    title: Network provider abstraction (host truth + netns)
    acceptance:
      - When network metrics are collected, host-level truth and netns-approximated per-entity figures are both exposed through the same provider interface.
    status: shipped
    milestone: M2
  - id: F006
    title: Origin and drift detection against systemd-declared configuration
    acceptance:
      - When a cgroup value diverges from its systemd-declared configuration, a drift or raw-write finding is surfaced showing the effective value (e.g. `memory.min`).
    status: shipped
    milestone: M2
  - id: F007
    title: Diagnostics engine — pressure score, rules, I/O cap saturation, network loss
    acceptance:
      - When PSI pressure, I/O cap saturation, or host/interface network drops/errors cross a rule threshold, a typed finding is raised on the affected entity or host without claiming exact per-cgroup network-loss attribution.
    status: shipped
    milestone: M2
  - id: F008
    title: v1 release hardening, packaging, and rootless acceptance harnesses
    acceptance:
      - When the acceptance harnesses run (`python -m topos.acceptance smoke|steady|tui-smoke`), they produce deterministic wall/CPU/RSS evidence, and a clean-venv install resolves `textual>=8.2.8` with no artificial upper ceiling.
    status: shipped
    milestone: M2
  - id: F009
    title: Passive DAMON vaddr/paddr detection
    acceptance:
      - When DAMON is active on the host, topos reports read-only vaddr attribution and host paddr detection without mutating kernel state.
    status: shipped
    milestone: M3
  - id: F010
    title: Controlled DAMON sessions and daemon-owned paddr lifecycle
    acceptance:
      - When an operator starts a DAMON vaddr or paddr session via CLI/TUI typed confirmation, ownership is marked; the daemon adopts or stops only its own sessions across restart/shutdown, leaving foreign sessions untouched.
    status: shipped
    milestone: M3
  - id: F011
    title: Incident snapshot bundles with manifest, redaction, and progress UI
    acceptance:
      - When an operator triggers a snapshot (`x` in the TUI or the snapshot API), a bundle with bounded frame history, raw cgroup file copies, manifest hashes, and redaction is produced with non-duplicating progress status, and `topos snapshot inspect` reads it back.
    status: shipped
    milestone: M3
  - id: F012
    title: Compressed-swap backend awareness and per-device drill-down
    acceptance:
      - When zswap/zram/disk/mixed swap backends are active, the banner and per-device host-memory view use backend-aware labels and canonical/alias field names without misattributing swap type.
    status: shipped
    milestone: M3
  - id: F013
    title: Headless recording and steady-state reporting
    acceptance:
      - When `topos --record FILE --headless` drives a collection loop and `topos report FILE` computes a profile, per-entity p50/p95/max are produced for key gauges with `--window last:Ns|all|auto` selection, `--assert GROUP:METRIC:STAT` threshold gating (exit 1 on breach, exit 2 on malformed spec), and no textual import on the headless path.
    status: shipped
    milestone: M3
  - id: F014
    title: Collection-time entity/metric filtering and docker-name selectors
    acceptance:
      - When `--entities GLOB`, `--slice NAME`, `--container NAME_OR_PREFIX`, or `--metrics FIELD_OR_FAMILY,...` are supplied, the collector skips excluded entities' sysfs reads entirely and validates every selector token against the registry, rejecting the combination with `--replay`/`--attach`.
    status: shipped
    milestone: M3
  - id: F015
    title: Read-only Unix-socket daemon broker with sampling fan-out
    acceptance:
      - When multiple non-consuming clients connect to the daemon socket, each independently receives current/stream frames with bounded sequenced history and typed gap/shutdown semantics from one request-independent background producer.
    status: shipped
    milestone: M4
  - id: F016
    title: Versioned daemon read API with typed client
    acceptance:
      - When a client negotiates via `hello`, it receives capability-versioned, bounded, peer-identified (`SO_PEERCRED`) responses through the typed `DaemonClient`; legacy clients without a `v` field are served unchanged.
    status: shipped
    milestone: M4
  - id: F017
    title: Daemon attach and deployment UX
    acceptance:
      - When `--attach` is used with no explicit socket, it defaults to `/run/topos/topos.sock`; `topos daemon preflight|install-plan|current|status` and daemon client error messages give actionable next steps without performing installation or mutation.
    status: shipped
    milestone: M4
  - id: F018
    title: BPF measurement gate, network provider read-side, and snapshot bridge
    acceptance:
      - When a pinned BPF counter map exists, `BpfSnapshotBridge` atomically decodes and cgroup-maps it into the snapshot contract with path confinement and last-good preservation, while the safe gate itself never loads or pins BPF state.
    status: shipped
    milestone: M4
  - id: F019
    title: Bounded file and log inspection
    acceptance:
      - When both `--inspect-files` and `--admin` are set, only allowlisted, descriptor-confined, bounded reads of resolved Docker JSON logs, cgroup files, or a fixed-argv journald snapshot are permitted; no arbitrary path and no unbounded follow is reachable.
    status: shipped
    milestone: M4
  - id: F020
    title: Admin action execution kernel
    acceptance:
      - When an admin action (start/stop/restart/kill/update/`memory.high` set-property/`squeeze`) executes, it requires root, `--admin`, and typed `--confirm`, validates the target, uses fixed argv with bounded timeout, and writes a mandatory fail-closed pre/post audit record even on a partial-outcome failure.
    status: shipped
    milestone: M4
  - id: F021
    title: Daemon component health registry
    acceptance:
      - When `topos daemon health [--json]` is run, it returns a strictly `health-v1`-validated report of collector, BPF-bridge, and paddr component states.
    status: shipped
    milestone: M4
  - id: F022
    title: Daemon MCP frontend
    acceptance:
      - When an MCP client calls one of the four bounded read-only tools, results are capped at 4 MiB and redacted per sensitivity ceiling, and the live-daemon acceptance leg proves this against a real daemon rather than only an injected fake client.
    status: shipped
    milestone: M4
  - id: F023
    title: Versioned read HTTP gateway (provisional auth)
    acceptance:
      - When a loopback HTTP client requests a versioned read route, it receives the same bounded envelope as the Unix API with no mutation or CORS routes; its proxy-principal auth header is documented as provisional pending the accepted capability-token boundary.
    status: shipped
    milestone: M4
  - id: F024
    title: Docker action owner and protected-ID safety
    acceptance:
      - When a raw Docker action targets a Compose/CIU/Wings-owned or protected container (matched from one authorizing `docker inspect` by canonical full/short/name identity), the action is refused with a typed, audited reason naming the owner; ambiguous or conflicting labels and inspect failure fail closed with no name-only fallback.
    status: shipped
    milestone: M5
  - id: F025
    title: Shared fail-closed redaction enforcement point
    acceptance:
      - When the HTTP gateway or MCP frontend serializes a response above the sensitivity ceiling, both route through the same single enforcement point and typed marker; unclassified metrics and finding-prose values fail closed rather than leaking.
    status: shipped
    milestone: M5
  - id: F026
    title: CIU-grouped TUI end-to-end coverage
    acceptance:
      - When a real Textual pilot drives the TUI into `ciu-grouped` mode, rendered group headers, mixed-tier honesty, in-group sort reorder, and provably inert synthetic rows are all asserted against the mounted DataTable.
    status: shipped
    milestone: M5
  - id: F027
    title: Versioned daemon client health completion
    acceptance:
      - When `request_health_versioned()` is called, it reuses the typed envelope and legacy health parser to return a frozen `DaemonVersionedHealthResult` with a derived `overall_ok`.
    status: shipped
    milestone: M5
  - id: F028
    title: Unified bounded frame query core (`topos query`)
    acceptance:
      - When `topos query` runs against a recording or daemon history, it returns byte-identical payloads across both sources with coverage/gap/eviction/reset truth and pre-materialization row/point/byte bounds for all six declared value semantics.
    status: shipped
    milestone: M6
  - id: F029
    title: Human-readable query and report rendering
    acceptance:
      - When `topos query`/`topos report` run without `--json`, they print a deterministic ASCII table using the closed six-state value vocabulary (`missing`/`redacted`/`warming`/`stale`/`permission-denied`/`truncated`), with `--json`/`--table` mutually exclusive (exit 2 on conflict) and a real `0` never rendered blank.
    status: shipped
    milestone: M6
  - id: F030
    title: Visible source auto-selection and daemon backfill
    acceptance:
      - When no explicit source is given, topos prefers the daemon, visibly falls back to local collection when the daemon is unavailable, and immediately backfills available daemon history rather than starting cold.
    status: planned
    milestone: M7
  - id: F031
    title: Bounded CPU-hot and I/O-hot process projection
    acceptance:
      - When the process view is requested, the candidate set is the identity-safe union of CPU-hot, I/O-hot, pinned/selected, and recently-hot processes with `pidstat`-class CPU/fault/I/O/context-switch history, explicit coverage telemetry, and D-019's configured bounds.
    status: planned
    milestone: M7
  - id: F032
    title: Persistent capped history
    acceptance:
      - When the daemon runs continuously, history is retained under simultaneous age and byte caps across a five-minute RAM tier and a 24-hour/256-MiB disk tier, with measured write amplification and corruption recovery.
    status: planned
    milestone: M7
  - id: F033
    title: Lifecycle owner-chain protocol (full migration)
    acceptance:
      - When any action verb executes against Docker or systemd, it goes through one centralized, side-effect-free owner-discovery and authorization plan with no raw-runtime fallback, replacing the P87 stopgap's CLI-only wiring.
    status: planned
    milestone: M8
  - id: F034
    title: Shared detail-observation leases
    acceptance:
      - When an expensive or privileged detail provider is requested, it is granted a visible, expiring lease; safe providers may auto-lease while privileged providers require explicit manual activation.
    status: planned
    milestone: M8
  - id: F035
    title: Loopback web transport
    acceptance:
      - When the browser fixture connects, it presents a per-start random capability token over a same-origin, bounded-route connection, and production topos remains loopback-only with no published ports.
    status: planned
    milestone: M9
  - id: F036
    title: Lifecycle identity and incidents
    acceptance:
      - When a workload restarts or exits, its stable workload/incarnation identity, tombstone, and Previous-instance/Recent-exit links are derived in the shared capped store without polluting current totals.
    status: planned
    milestone: M9
  - id: F037
    title: React Overview and Explore routes
    acceptance:
      - When an operator opens the web UI, Overview and Explore render the same bounded, projected query results as the CLI, with persistent source/coverage status and truthful (non-misleading) charts.
    status: planned
    milestone: M10
  - id: F038
    title: React Entity, Incidents, and Compare routes
    acceptance:
      - When an operator compares entities, at most three may be selected at once, and process/I/O history, lifecycle/detail leases, and bounded incident evidence render consistently with the CLI contract.
    status: planned
    milestone: M11
  - id: F039
    title: ZFS ARC host provider
    acceptance:
      - When ZFS ARC is present on the host, size/target/max/min/hit-ratio metrics and a banner annotation are shown without a false memory-pressure reading and without claiming per-cgroup ARC attribution.
    status: shipped
    milestone: M12
  - id: F040
    title: GPU host provider
    acceptance:
      - When a DRM-exposed GPU is present, host-level VRAM total/used, busy percent, and card count are shown; a vendor without exposed facts (i915/nvidia) never renders identically to "no GPU".
    status: shipped
    milestone: M12
  - id: F041
    title: CIU stack metadata detection
    acceptance:
      - When a container carries CIU stack/phase Docker labels, they are parsed and numerically ordered (`phase_2` before `phase_10`) as provenanced metadata.
    status: shipped
    milestone: M12
  - id: F042
    title: CIU stack grouping in the TUI
    acceptance:
      - When the TUI's `ciu-grouped` mode groups entities by stack/phase, a group's tier (label/inferred/mixed) is the honest aggregate of its members, individually marked, and in-group `sort_by` is respected.
    status: shipped
    milestone: M12
  - id: F043
    title: Scenario-driven provider and comparison broadening
    acceptance:
      - When a named `docs/OPERATOR-QUESTIONS.md` scenario cannot be satisfied by existing providers, a new provider, live BPF lifecycle capability, or informational baseline comparison is added only against that named gap, never as speculative breadth.
    status: planned
    milestone: M13
non_goals:
  - Cloning unbounded specialist-tool breadth (a second `top`/`iostat`/BPF suite) instead of closing named `docs/OPERATOR-QUESTIONS.md` gaps.
  - A second aggregation engine built independently in the MCP frontend, HTTP gateway, or browser instead of consuming the shared `topos/query/` core.
  - Multi-user daemon authorization beyond socket/token permissions before an explicitly shared (non-single-operator) deployment is decided.
  - Production daemon installation execution and service hardening beyond the packaged operator templates plus the non-mutating install-plan renderer.
  - Live BPF attach/pin/detach lifecycle ownership before the daemon/helper measurement and privilege work is done.
  - CIU-aware or other environment-specific action authorization implemented as an inferred special case ahead of the lifecycle owner-chain protocol (F033).
  - Any distro-matrix hardening work before the v2 cut is otherwise complete.
---

# Product definition v1

This definition distills the shipped v0-v2-foundation implementation plus the
in-flight operator-console frontier from `docs/ROADMAP.md` and `docs/STATUS.md`
into schema-tracked features. Status is derived from `docs/STATUS.md`/`README.md`
work-package table: merged packages are `shipped`, and un-carved or
dependency-blocked frontier work is `planned`. D-001 through D-019 in
`docs/DECISIONS-INBOX.md` are the closed product/architecture calls this
definition assumes; it does not restate them.

## Feature-to-package traceability

Preserved for audit trail (source: `README.md` work-packages table and
`docs/ROADMAP.md`'s Executable frontier). Package IDs are `topos/handoff/`
history, not part of the schema.

| Feature | Packages |
|---|---|
| F001 | P1 |
| F002 | P1, P4 |
| F003 | P5, P13, P24, P34, P36, P50 |
| F004 | P2, P41, P79 |
| F005 | P3 |
| F006 | P4 |
| F007 | P6, P28, P37 |
| F008 | P7, P12, P33, P35, P38, P39, P40, P43 |
| F009 | P8 |
| F010 | P9, P11, P14, P44 |
| F011 | P10, P15, P26 |
| F012 | P19, P23, P27 |
| F013 | P53, P54, P61, P62, P70 |
| F014 | P55, P57, P59, P60 |
| F015 | P16, P51 |
| F016 | P52, P63 |
| F017 | P20, P22, P25, P30, P31, P32 |
| F018 | P17, P18, P42 |
| F019 | P29, P45, P48 |
| F020 | P21, P46, P49, P56, P72, P78 |
| F021 | P47 |
| F022 | P58, P75 |
| F023 | P67 |
| F024 | P87 |
| F025 | P81 |
| F026 | P86 |
| F027 | P66 |
| F028 | P88 |
| F029 | P65 |
| F030 | P89 |
| F031 | P90 |
| F032 | P91 |
| F033 | P93 |
| F034 | P94 |
| F035 | P92 |
| F036 | P95 |
| F037 | P73 |
| F038 | P77 |
| F039 | P71 |
| F040 | P74 |
| F041 | P76 |
| F042 | P83 |
| F043 | P64, and P18's live-lifecycle residual |

P69 (web UI scoping, docs-only) fed D-001..D-003 and the F035/F037/F038 carves
directly; it is process provenance, not a feature of its own. P68, P80, and P82
were deleted during the 2026-07-15 reconciliation and are not represented here
(see `docs/BRANCH-DISPOSITION.md`). P84/P85 (gate-environment pinning, flaky-UI
timing fix) are test-infrastructure hygiene, not product capability, and are
likewise not represented as features.
