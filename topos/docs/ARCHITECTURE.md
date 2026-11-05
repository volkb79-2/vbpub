# topos Architecture

`topos` is split around a stable frame contract: collectors and providers create
`Frame` objects, record/replay preserves them, and the UI renders them. Textual
is intentionally isolated under `src/topos/ui/`.

## Dataflow

```mermaid
flowchart LR
    Cgroup[cgroup v2 files] --> Collector
    Proc[/proc] --> Collector
    Zram[ZRAM sysfs + /proc/swaps] --> Collector
    Gpu[DRM sysfs /sys/class/drm] --> Collector
    Docker[Docker inspect] --> Collector
    DockerLabels[Docker Config.Labels] --> CiuDetect[CIU detection]
    CiuDetect --> Collector
    Systemd[systemctl show] --> Drift
    NetHost[host network provider] --> Collector
    Netns[netns provider] --> Collector
    BpfGate[BPF gate CLI] --> NetHost
    Damon[DAMON sysfs] --> DamonPassive

    Collector --> Frame[Frame model]
    DamonPassive --> Frame
    Drift --> Frame
    Diag[Diagnostics] --> Frame

    Frame --> Record[RecordWriter JSONL/zstd]
    Record --> Replay[RecordReader/ReplayDriver]
    Record --> Report[topos report steady-state profile]
    Replay --> UI
    Frame --> UI[Textual UI]
    Frame --> Snapshot[Incident snapshot bundle]
    Frame --> Broker[Daemon FrameBroker]
    Broker --> UnixApi[Versioned Unix read API]
    UnixApi --> Attach[Attached TUI / CLI]
    UnixApi --> Mcp[MCP frontend]
    UnixApi --> Http[Loopback HTTP gateway]
    Http -.-> Web[Web client, parked D-023]
```

## Module Map

| Module | Role |
|---|---|
| `model.py` | Dataclasses and canonical JSON serialization. |
| `registry.py` | Metric definitions, source semantics, help/glossary source. |
| `config.py` | TOML parsing and defaults. |
| `collect/` | cgroup, host (including ZFS ARC and GPU DRM), Docker (including CIU stack metadata detection), process, and collector orchestration. |
| `providers/` | Network provider abstraction and current host/netns providers. |
| `drift/` | systemd/live-origin classification and governance drift. |
| `diag/` | pressure score and findings rules. |
| `damon/` | passive DAMON parsing plus controlled vaddr/paddr session APIs. |
| `record/` | live stream, JSONL reader/writer, replay, and history ring. |
| `snapshot/` | incident bundle creation and inspection. |
| `daemon/` | Request-independent Unix-socket frame broker with background producer, bounded sequenced history, non-consuming fan-out, lifecycle (P51), and a versioned, bounded, peer-aware read API envelope with typed errors, sensitivity metadata, peer credentials, and proven resource bounds (P52). |
| `bpf_gate.py` | Safe no-op BPF preflight and baseline measurement helper. |
| `report.py` | Read-only steady-state profile computation from a P2-format recording: per-entity/per-slice p50/p95/max for key memory/PSI gauges plus derived rates. |
| `actions/` | Immutable action previews and one private, audit-first execution chain for catalog, governance, kill, and update verbs. Verb-specific gates are ordered pre-audit gates except documented post-audit revalidation required to preserve an existing audit trail. |
| `ui/` | Textual app, banner, table/tree, drill-down, host-memory status, keys. |
| `query/` | Unified bounded frame-query engine used by CLI and MCP (`topos query`, `report`, `compare`). |
| `procs/` | Bounded CPU/I/O-hot process model. No caller yet. |
| `mcp/` | Read-only MCP stdio frontend over the daemon socket. |
| `inspect_files/` | Bounded, admin-gated file and log inspection (`docs/INSPECT-FILES.md`). |

## Layering Rules

- `ui/` is the only package allowed to import Textual.
- `--once --json` must work without importing Textual.
- Frame serialization must go through `model.py`.
- Metrics emitted into frames must exist in `registry.py`.
- Kernel/docker/systemd read failures become source-labelled degraded values or
  metadata, not crashes or fabricated zeroes.
- Host compressed-swap backend classification belongs in `collect/host.py` or a
  small helper under `collect/`; per-cgroup ZRAM compression attribution is not
  available from current kernel files, so cgroup rows must remain source-labelled
  estimates on zram/mixed hosts.
- Mutating DAMON control paths require root, explicit confirmation, ownership
  markers, and audit logs.

## Runtime Modes

```mermaid
stateDiagram-v2
    [*] --> OnceJson: --once --json
    [*] --> LiveTUI: topos
    [*] --> RecordLive: --record FILE
    [*] --> ReplayTUI: --replay FILE
    [*] --> DamonCLI: damon stop/paddr start
    [*] --> SnapshotCLI: snapshot inspect FILE
    [*] --> BpfGateCLI: bpf gate

    LiveTUI --> FrameLoop
    RecordLive --> FrameLoop
    FrameLoop --> Collector
    Collector --> UI
    Collector --> RecordWriter

    ReplayTUI --> ReplayDriver
    ReplayDriver --> UI
```

## Daemon Boundary

P51 introduces a request-independent background producer: the daemon starts a
producer thread before serving requests and stops it deterministically after the
server closes. `current` returns the latest published frame; `stream` reads from
published history with optional sequence/cursor. Multiple concurrent clients
observe the same sequence without accelerating, consuming, or starving each
other.

P52 adds a versioned, bounded, peer-aware read API envelope over the P51
broker. Requests carrying a `v` field are dispatched through `DaemonApi`
(single-line envelope response with `id` echo, typed error codes, sensitivity
metadata, and peer credentials). Requests without `v` flow through the P51
multi-line protocol unchanged. `SO_PEERCRED` is observed at accept time; an
injectable authorization hook may deny with a typed error.

```mermaid
flowchart LR
    RootDaemon[topos-daemon as root] --> Producer[background producer thread]
    Producer --> Store[bounded frame store with seq]
    RootDaemon --> Socket[Unix socket 0660 group topos]
    Store --> Socket
    Socket --> TUI[topos --attach]
    Socket --> AttachClient[attached TUI / CLI]
    Socket --> MCP[MCP frontend]
    Socket --> Gateway[loopback HTTP gateway]
    Gateway -.-> FutureWeb[web UI, parked D-023]
    RootDaemon --> BPF[BPF maps/programs]
    RootDaemon --> DamonState[DAMON ownership]
```

## Contract Pressure Points

- A future `EntityFrame.diagnostics` block could preserve exact pressure
  breakdowns instead of recomputing them in UI.
- `NetSample` may need optional traffic-class metadata from `[net.classes]`.
- Daemon frames carry sensitivity metadata (closed enum: public,
  operational, sensitive) in `metrics_meta` for non-root consumers; the
  metric compact form is unchanged.

## Product Convergence Boundaries (state as of 2026-11-05)

The design rules below stand; the state notes say how far the code is. The full
state table is `docs/STATUS.md`.

- **Frame-query engine (shipped, partly connected).** `query/` is one engine over
  bounded recording frames, with window coverage, gaps, resets, statistics and
  counter deltas. CLI and MCP use it. The TUI bypasses it. Daemon history as a
  source exists in `query/source.py` as a library only; `topos query` reads
  recordings only.
- **Process model (code exists, not connected).** `procs/` implements the bounded,
  incarnation-safe union of CPU-hot, I/O-hot, selected/pinned and recently-hot
  processes. No caller uses it. Process rows join to cgroup entities and never
  form a second accounting tree. The planned sampler replaces its collection
  (D-026).
- **Detail leases (not started).** Safe detail collection will use shared
  reference-counted/TTL leases; privileged or costly collection needs explicit
  manual activation and never starts from navigation alone.
- **Ownership (partly shipped).** Observed cgroup/unit/orchestrator ownership is
  immutable model provenance; configured policy/tags are a separate projection
  input and cannot alter accounting identity or authorization.
- **Lifecycle identity (not started).** A stable logical workload identity plus
  concrete incarnations, with Previous instance/Recent exit links that never
  contribute to current totals.
- **Owner-chain actions (partly shipped).** Actions route through an authoritative
  owner adapter and refuse ambiguity or unsupported operations; raw runtime
  fallback is forbidden. Docker and systemd verbs are wired to the CLI; the
  Compose, CIU and Wings adapters refuse. See `docs/LIFECYCLE-ADAPTERS.md`.
- **Persistent history (writer only).** `daemon/persist.py` writes an age- and
  byte-capped store, off by default; nothing reads it, and the broker serves an
  in-memory ring. The tiers and caps of D-025 supersede D-005.
- **Web (parked, D-023).** A browser client stays a separate frontend over
  projected, redacted read contracts with no daemon-socket, Docker, file or
  mutation access. No web code exists. SSH and forwarding are external concerns.

## Target layering (not implemented)

Target design is `TUI-SPEC.md` section 5; the plan is `docs/PLAN-2026-11-CONSOLE.md`.
Layers split by activity, and everything in this document above describes the
current Python-only dataflow.

| Layer | Runs | Language |
|---|---|---|
| Sampler (reads, rates, hot/warm/cold tiers, hard events, socket) | 24/7 per host | Rust, pending the PoC (D-026) |
| Slow enrichment (Docker identity, systemd origin) | Sampler, slower cadence | Rust |
| Query, findings, report, MCP, actions, squeeze | On demand | Python |
| TUI | Only while open | Python line-rendered widget; engine open vs ratatui PoC (D-028) |

`topos --once` would call the sampler so there is one collector. The daemon would
run under the tight envelope of D-024, which is not what the packaged
`topos.slice` sets today (256 MiB high, 512 MiB max).
