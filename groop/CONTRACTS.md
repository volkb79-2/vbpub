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
