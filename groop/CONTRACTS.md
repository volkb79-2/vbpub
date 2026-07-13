# groop CONTRACTS — shared interfaces (frozen; changes need maintainer sign-off)

Spec: `TUI-SPEC.md`. These contracts implement spec
§3.2 (metric registry), §6.1 (layering), §6.2/§6.3 (permissions/degradation),
§3.8 (record/replay), and the network provider interface from §3.2/Appendix B.

## 1. Repository layout

```
groop/
  pyproject.toml            # package: groop; console script: groop
  src/groop/
    __init__.py             # __version__
    registry.py             # MetricSpec + REGISTRY (P1)
    model.py                # EntityKey, Entity, MetricValue, Finding, Frame (P1)
    cli.py                  # arg parsing; --once --json must work WITHOUT textual installed
    collect/                # P1 — stdlib only, no textual import anywhere below ui/
      cgroup.py             #   tree walk, cgroup file readers
      dockerjoin.py         #   docker metadata enrichment
      host.py               #   host banner facts
      zswapmath.py          #   refault split (port from soulmask-zswap-monitor.py)
      procs.py              #   per-entity process listing (drill-down data)
      collector.py          #   Collector orchestration + reset handling
    providers/              # P3 (+v2 BPF later)
      base.py               #   Provider ABC + NetSample
      net_host.py           #   tier-1 host/interface truth
      net_netns.py          #   tier-2 netns approximation
    record/                 # P2
      writer.py  reader.py  ring.py
    drift/                  # P4
      origin.py
    diag/                   # P6
      score.py  rules.py
    damon/                  # P8/P9 (v1.5)
      passive.py  control.py
    snapshot/               # P10 (v1.5)
      bundle.py
    ui/                     # P5 — the ONLY place textual may be imported
      app.py  banner.py  table.py  tree.py  drill.py  keys.py  theme.py
  tests/
    fixtures/cgroupfs/      # synthetic cgroup trees (plain dirs+files)
    fixtures/frames/        # golden JSONL frames
    ...
```

## 2. Entity model (`model.py`)

- `EntityKey = str` — canonical cgroup path **relative to the cgroup root**,
  e.g. `"system.slice/docker-<64hex>.scope"`, `"soulmask.slice/soulmask-paks.slice"`,
  `""` for the root. This is the join key EVERYWHERE (frames, ring, providers,
  UI rows, recordings).
- `Entity` (dataclass): `key: EntityKey`, `kind: {"root","slice","scope","service","other"}`,
  `parent: EntityKey|None`, `docker: DockerMeta|None`, `tier: str|None`
  (config-declared tier name), `is_protected: bool` (from config: protected
  services list).
- `DockerMeta`: `cid` (12-hex short), `full_id`, `name`, `image`,
  `compose_project: str|None`, `ptero_uuid: str|None` (container name IS the
  Wings server UUID when it parses as one).

## 3. Metric registry (`registry.py`) — spec §3.2

```python
@dataclass(frozen=True)
class MetricSpec:
    name: str            # snake_case, unique; JSONL field name
    unit: str            # "bytes","bytes/s","/s","%","us","count","ratio"
    kind: str            # "gauge" | "counter" | "derived"
    locality: str        # "local" | "subtree"   (what the kernel file means)
    branch_policy: str   # "kernel_subtree" | "local_only" | "child_sum" | "n/a"
    aggregatable: bool   # may be summed across siblings (False => never sum)
    sources: tuple[str, ...]   # kernel file(s)/API(s), documentation-grade
    glossary: str        # 1-3 sentences; F1 help + docs are GENERATED from this
    threshold_key: str | None  # config [thresholds] lookup key, if colorized
```

`REGISTRY: dict[str, MetricSpec]` is the single source of truth: column
tables, JSONL schema validation, glossary/help, and diagnostics inputs all
derive from it. A metric absent from the registry MUST NOT appear in a frame.

## 4. Metric values and frames

```python
@dataclass
class MetricValue:
    v: float | int | None      # None = non-numeric state; NEVER emit fake 0
    src: str                   # "exact"|"derived"|"netns"|"host"|"unlimited"|"unavail_perm"|"unavail_kernel"
    raw: int | None = None     # counters: raw cumulative, for reset detection
```

`src="unlimited"` means the kernel reported a known infinity state such as
`memory.max=max`, `memory.high=max`, `pids.max=max`, or `cpu.max=max`. It is not
an unavailable value; render it distinctly from `unavail_*`.

`Finding` (diagnostic output; P1 defines the shape, P6 fills it):

```python
@dataclass
class Finding:
    rule_id: str
    severity: str              # "info" | "warn" | "red"
    message: str
    remedy: str | None = None
    source_metrics: tuple[str, ...] = ()
    confidence: str = "exact"  # "exact" | "estimated" | "n/a"
```

`Frame` (one sample of the whole host):

```python
@dataclass
class Frame:
    schema_version: int        # = 1
    ts: float                  # epoch seconds
    interval_s: float          # actual elapsed since previous sample
    host: dict[str, MetricValue]        # banner facts (host_* metrics)
    entities: dict[EntityKey, EntityFrame]
    host_meta: dict[str, object] | None = None  # host-level non-metric details

@dataclass
class EntityFrame:
    entity: Entity             # embedded (recordings must be self-contained)
    metrics: dict[str, MetricValue]
    findings: list[Finding] = field(default_factory=list)  # filled by diag (P6)
    governance: dict[str, object] | None = None             # filled by drift (P4)
    network: dict[str, object] | None = None                # filled by net providers (P3)
    damon: dict[str, object] | None = None                  # filled by passive/control DAMON (P8+)
```

`EntityFrame.damon` is optional metadata for replayable DAMON drill-downs. P8
uses it for passive session summaries (`sessions`, `host_sessions`, target PID
coverage, region class histograms, sample age, kdamond/context identifiers).
Numeric table/sort/chart surfaces still use registry-backed `damon_*`
`MetricValue`s in `metrics`; consumers must tolerate absent `damon` metadata.

`Frame.host_meta` is optional additive metadata for replayable host-level
details that are not registry metrics, such as per-device ZRAM rows. Consumers
must tolerate it being absent, and producers must keep `Frame.host` strictly
registry-backed.

Rate/reset contract (P1): the Collector keeps the previous raw counters per
(EntityKey, metric). Rates are `(raw_now - raw_prev)/interval_s`. On counter
regression (cgroup recreated, cid reuse, kernel reset) emit `v=None` for that
interval and reseed — never a negative or absurd rate.

Serialization contract (P1 owns, every package reuses): `model.py` provides
`frame_to_jsonable()`, `frame_from_jsonable()`, and `validate_frame_metrics()`.
No package may hand-roll a second frame serializer. `MetricValue` serializes in
the compact form from §5 everywhere (`[v, src]` or `[v, src, raw]`) so
`--once --json`, recordings, replays, fixtures, and tests share one schema.

## 5. JSONL recording format (P2) — spec §3.8

- Line 1 header: `{"type":"header","schema_version":1,"groop_version":...,`
  `"host_id":...,"started_at":...,"config_digest":...}`
- Every subsequent line: `{"type":"frame", ...Frame serialized...}`;
  `MetricValue` serializes as `[v, src]` or `[v, src, raw]` (compact form).
- Reader yields `Frame` objects; replay MUST route through the same model
  objects the live collector produces (UI cannot tell live from replay).
- P2 MUST call the serialization helpers from `model.py`; it owns file framing
  and compression, not an alternate frame schema.
- Ring buffer (`ring.py`): fixed-capacity per-(entity,metric) numeric arrays
  (`array`/`memoryview`, NOT lists of Python floats), default profile 4h @ 5s
  (spec §3.5 budget: 20–40 MB).
- The `--headless` record driver (P53) reuses the same `RecordWriter`/JSONL
  format unchanged; no schema differences exist between TUI-recorded and
  headless-recorded output.
- Filtered recordings (P55/P59): `--entities`/`--slice`/`--container`/`--metrics compact` produce
   frames with fewer ``entities`` keys and/or fewer ``metrics`` per entity. These
   filtered frames are a valid subset of the existing P2 schema: they serialize
   and deserialize identically, pass ``validate_frame_metrics()``, and replay
   as normal frames. No new schema or format version is introduced. ``--container``
   resolves inside the collector sweep (not pre-resolved in ``cli.py``) to ensure
   post-enrichment Docker metadata accuracy.

## 6. Provider interface (P3, v2-BPF-ready) — spec §3.2/App. B

```python
@dataclass
class NetSample:
    rx_bytes: int|None; tx_bytes: int|None
    rx_pkts: int|None;  tx_pkts: int|None
    proto: dict|None            # optional {tcp:{...},udp:{...}}
    source_label: str           # "net:BPF"|"net:NS"|"net:HOST"|"net:N/A"
    confidence: str             # "exact"|"estimated"|"n/a"
    aggregation: str            # "exact"|"private_ns_only"|"none"
    unavailable_reason: str|None

class Provider(Protocol):
    name: str
    def collect(self, entities: dict[EntityKey, Entity]) -> dict[EntityKey, NetSample]: ...
    def status(self) -> dict: ...   # loaded/attached/last_read/errors
```

Branch aggregation of netns samples ONLY when every child has a distinct
private netns (aggregation="private_ns_only" and the caller proved it);
otherwise the branch shows `net:N/A`.

## 7. Config — spec §3.7/§7

TOML at `$XDG_CONFIG_HOME/groop/config.toml`; parse with `tomllib`; every key
optional with shipped defaults. Packages read config ONLY through a shared
`groop.config.load()` (P1 provides it). Thresholds live under `[thresholds]`,
tiers/protected services under `[tiers]` — P6 and UI color rules consume the
same keys.

## 8. Degradation + permissions — spec §6.2/§6.3

Every kernel/docker read wraps errors into `src="unavail_perm"` or
`"unavail_kernel"` MetricValues. Non-root: collector still runs; root-only
files degrade per the spec matrix. No crash, no zero-fabrication. `cli.py`
must import textual lazily so `--once --json` works with no UI deps installed.

## 9. Testing conventions

- Fixture cgroup trees: plain directories under `tests/fixtures/cgroupfs/<case>/`
  mirroring `/sys/fs/cgroup` (files with realistic content, incl. missing-file
  and permission-denied cases via chmod 000 in test setup).
- The collector takes `cgroup_root: Path` as a parameter (default
  `/sys/fs/cgroup`) precisely so fixtures can substitute it.
- Golden frames: `tests/fixtures/frames/*.jsonl` — P1 generates, all other
  packages consume. Regenerating goldens is a reviewed change.
- Docker join in tests: `dockerjoin.py` accepts an injectable
  `docker_inspect: Callable` — fixtures provide canned JSON; no docker daemon
  in CI/tests.

## 10. Daemon read API envelope (P52)

`groop/src/groop/daemon/api.py` implements a versioned, bounded, peer-aware
read API over the P51 `FrameBroker`. The envelope is additive: legacy
requests without a `v` field continue to be served by the P51 multi-line
protocol unchanged (compatibility mode, documented in `docs/DAEMON.md`).

### Envelope shape

Every request carries `id` (opaque client string, echoed verbatim in the
response), `op` (closed set), and `v` (protocol version integer). Every
response carries the echoed `id`, `ok` boolean, and on failure a typed
`error` object (`code` from the closed enum below, safe `message`). On
success the response carries `result`.

```json
{"id":"client-1","op":"hello","v":1}
{"id":"client-1","ok":true,"result":{...}}
{"id":"client-1","ok":false,"error":{"code":"unknown_op","message":"..."}}
```

### Protocol version and capabilities

- `PROTOCOL_VERSION = 1`; `PROTOCOL_VERSIONS = (1,)`.
- `CAPABILITIES = ("hello","current","history","entity","health")`. Every
  served op is listed and every listed op is served. An unlisted op is
  rejected with `unknown_op` before the authorization hook runs.

### Error code enum (closed)

`bad_request`, `unknown_op`, `unknown_field`, `invalid_type`,
`non_finite`, `out_of_range`, `malformed_cursor`, `oversized_request`,
`oversized_response`, `request_timeout`, `server_busy`, `unavailable`,
`denied`, `not_found`, `protocol_version`, `internal`.

A response error NEVER carries a raw exception, secret, filesystem path, or
arbitrary exception text. The P47 `sanitize_public_text` helper is reused so
the P51 safety contract persists through the new envelope.

### Sensitivity enum (closed)

Every metric in a response carries exactly one sensitivity level:

| Level | Meaning | Example metrics |
|---|---|---|
| `public` | Host banner facts; safe for any local consumer | `host_mem_total`, `host_load1` |
| `operational` | Standard cgroup telemetry | `ram`, `cpu_pct`, `io_r_bps` |
| `sensitive` | Process-identity/counts; privacy-relevant | `cgroup_procs`, `pids_current`, `pids_max`, `pids_events_max_per_s` |

The level is attached in `metrics_meta` alongside registry-derived
`unit`/`kind`/`locality`/`glossary` so a web/MCP consumer can render without
duplicating registry prose.

### Read-only ops

- `hello` — protocol version(s), capabilities, daemon identity, and current
  limits (max request bytes, max response items, history capacity).
- `current` — latest atomic `(sequence, frame)` plus `metrics_meta`.
- `history` — bounded by sequence cursor OR by time window (`since_ts`
  inclusive, `until_ts` exclusive); each form returns explicit
  `gap`/`oldest_seq`/`latest_seq`/`next_cursor` metadata identical to the P51
  legacy `stream` op. The two forms are mutually exclusive.
- `entity` — one entity's frame/model data plus registry metadata. Resolves
  ONLY against daemon-approved in-memory frame data; `key` is validated (no
  absolute path, `..`, NUL, or control chars) and never reaches a filesystem
  path, registry lookup by arbitrary key, command, or sysfs/procfs read.
- `health` — P47 component health through the new envelope.

### Peer identity and authorization

- `SO_PEERCRED` (pid/uid/gid) is observed at accept time on every
  connection and attached to the connection context. It appears in every
  audit/rate-limit record produced for that client.
- An authorization hook (`Callable[[PeerCredentials, str], tuple[ErrorCode,
  str] | None]`) is injectable for tests. Default policy: socket-group read
  access enforced by the OS (mode 0660 root:groop). The hook receives
  `(peer, op)` and may deny with a typed error. Mutation-shaped ops are
  rejected before the hook runs.
- Peer-credential read failure (platform or race): the connection is served
  anonymously (`peer=None`); the daemon never refuses on a best-effort
  introspection race.

### Resource bounds (enforced, not declared)

`ApiLimits` validates every bound at construction; out-of-range values raise
(`TypeError`/`ValueError`) and are never silently clamped. Bounds cover:

| Bound | Scope | Enforcement mechanism |
|---|---|---|
| `max_request_bytes` | per request | `rfile.readline(cap+1)`; over-cap or missing newline → `oversized_request` |
| `request_timeout_s` | per request read | `socket.settimeout()` on the connection; idle past deadline → `request_timeout` |
| `max_clients` | aggregate | `BoundedSemaphore` acquired in `process_request`; N+1th → `server_busy` |
| `max_response_items` | per response | history `limit` validated against this cap → `out_of_range` |
| `max_response_bytes` | per response | serialized response byte length checked; over → `oversized_response` |
| `history_capacity` | aggregate | `deque(maxlen=...)` in `FrameBroker` |

Each bound has a test that violates it for real and asserts the observable
outcome (actual thread counts, actual byte lengths, actual refused
connections) — not just the constant.