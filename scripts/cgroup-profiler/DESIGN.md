# cgroup-profiler — architecture and module contract

A container/cgroup performance profiler: it records CPU, memory, IO and
pressure over time for a set of targets, resolves what the **effective** cgroup
limits actually are (walking the ancestor chain, not just reading the leaf),
timestamps phase boundaries so activity can be correlated with the series, and
produces an interactive report at the end.

It is built to answer questions of the form *"when this gate runs, what happens
to that production container?"* — so observing a **victim** alongside the
**subject** is a first-class case, not an afterthought.

---

## CLI identity

Two console scripts ship in the `cgprofile` wheel: `cgpctl` (the client) and
`cgpd` (the daemon). Both are built on cli-extended and read their version
from the installed distribution (scm-derived from the `cgprofile-v` release
tag; an untagged build is `0.0.0-dev`). `--version` prints one identity line,
exits 0 and needs neither the daemon nor the `report` extra. At every nested
parser depth, help, usage and error output begin with the identity headline.
The reviewed command surface is `docs/CLI-SPEC.md`; the behavioural contract
is `docs/CGPCTL-SPEC.md`.

## 1. Why the shape is what it is

**A devcontainer cannot see the host cgroup tree.** `/sys/fs/cgroup` inside it
is a read-only, cgroup-namespaced view of its own subtree; sibling slices
simply do not exist there. Shuttling individual file reads across a container
boundary cannot sustain a 250 ms cadence, so the profiler never tries. There
are exactly two places the sampling code runs, and it is byte-identical in
both:

- **Direct mode** (`cgpctl run`): in the caller's own process tree, where the
  caller can see cgroupfs. Where it cannot, `run` refuses with one line naming
  the daemon alternative. `access.py` owns the proc-root selection.
- **The daemon** (`cgpd`): a native service on the host, which sees host
  `/proc` and the host cgroup tree directly. Callers in containers reach it
  through the bind-mounted control socket; they never need host views.

Container names, labels and `self` are resolved to immutable container IDs on
the Docker-aware caller (`cgpctl start --target container:NAME` runs
`docker inspect`); the daemon accepts only `containerid:`, `cgroup:` and
`slice:` targets and locates a container in the host cgroup tree by its ID. It
is never inferred from a namespace-relative `/proc/<pid>/cgroup` string, and
the daemon itself never uses Docker.

**Absent is not zero.** Every reader returns `None` for a file that does not
exist and for the cgroup sentinel `max`. A cgroup can vanish underneath the
sampler at any moment (a container exits mid-run) and a profiler that dies on
`ENOENT`, or that silently records 0 for "unlimited", is worse than no
profiler.

**Counters can go backwards.** A cgroup is recreated, a task migrates, a
container restarts. `util.rate()` returns `None` on a decreasing counter rather
than a huge negative spike. Nothing downstream may reintroduce that spike.

**Two dependency tiers, and the split is deliberate.**

- The **collector** (`access`, `targets`, `metrics`, `limits`, `sampler`,
  `events`, `store`, `caps`, `damon`, `phases`' mark IO, and — RG-55 — `serve`,
  `subtree`, `summary`) is **standard library only** (plus cli-extended for the
  command line). It is the half that runs in direct mode and inside the
  daemon, on a host under memory pressure, next to production. It must not
  need the `report` extra, must not import pandas to read a counter, and must
  keep working in a minimal venv.
- The **analyser and reports** (`analyze`, `report_html`, `report_md`,
  changepoint detection) run afterwards, outside, and **use real libraries**:
  pandas for resampling/correlation/grouping, plotly for the interactive
  report, matplotlib for the static twin, ruptures for changepoint detection,
  scipy/numpy underneath. Do not hand-roll any of that.

**The always-on daemon (RG-55).** `cgpd run` is a privileged, long-lived native
systemd service (`cgpd.service`, inside a bounded `cgprofile.slice`). Running
natively it sees the host PID namespace (so DAMON sysfs resolves host
`pid_target` values), host `/proc` and the host cgroup tree, and talks to host
systemd over the system bus for placement. It has no network use and no Docker
use. D-25's cgroup path whitelist is an application-level guard on ordinary
operations, not an OS boundary against arbitrary code execution in the
privileged daemon. Host PID visibility adds process-table visibility and
PID-operation authority; numeric PID signalling remains prohibited. D-31 asks
systemd to create one delegated scope beneath the verified gates slice per
placed lane; the daemon owns only its `rg-<token>` leaf beneath that scope,
never a child of the systemd-owned slice. D-32 deliberately keeps this
single-operator deployment broker-free; see `docs/DESIGN-GUIDE.md` and the
RG-55 design record for the honest threat boundary and deferred broker option.
run-gate talks to this daemon over a Unix socket per lane instead of spawning
a collector each time. `RG55-INTERFACE-CONTRACT.md` is the full wire contract
and `docs/PROTOCOL.md` the request side. The daemon is STILL collector-tier:
`serve.py` never imports pandas. It does not render reports; it streams a
stored session through the `export` operation (§4.13) and `cgpctl report`
renders the real interactive report in the caller's process, so the tier split
holds. Packaging, the unit and the install path are in
`docs/NATIVE-DEPLOYMENT.md`; the operator's-eye view is the README's
"Product overview"; this file stays the module-contract reference.

**Built once, never assembled at run time.** The wheel is built and installed
ahead of any profiling run; the `report` extra carries the analysis
dependencies with `>=` floors in `pyproject.toml`, the single source of truth.
No code path may pip-install, download, or fetch a CDN asset while profiling —
that would add exactly the load the tool exists to measure. `plotly.js` is
inlined into each report from the *installed* package, which is a local read,
not a fetch.

---

## 2. Layout

```
scripts/cgroup-profiler/
  pyproject.toml          distribution `cgprofile`, console scripts, `report` extra
  cmru.toml               release contract (scm versioning, wheel artifact)
  src/cgprofile/
    cli/cgpctl.py         the client (cli-extended registry; verbs of CGPCTL-SPEC.md)
    cli/cgpd.py           the daemon entry: run | unit | config | reload
    cli/legacy.py         the collector child shared by `cgpctl run` and the daemon
    ctlwire.py            the client side of the control protocol
    direct.py             `cgpctl run`: direct-mode runner
    daemon_config.py      the cgpd.toml schema, loader, reload, overrides layer
    unitfiles.py          `cgpd unit print|check` renderer
    data/                 unit templates and the default config (package data)
    skills/cgpctl/        the packaged agent skill
    _damon/               vendored DAMON analysis (byte-synced by a test)
    model.py              shared dataclasses (Mark/Phase/Event/Series/Proposal/Analysis)
    util.py               file readers, unit parsing, rate(), ancestors(), formatting
    access.py             host-view detection, proc-root selection
    targets.py            target spec parsing, cgroup discovery, Membership
    metrics.py limits.py  readers and effective-limit resolution
    sampler.py phases.py events.py   the sampling loop, marks, event detection
    store.py damon.py caps.py        run store, DAMON (KdamondPool), cap restore
    analyze.py report_md.py report_html.py   analysis and renderers (report extra)
    subtree.py            token→pid-subtree resolver (§4.13)
    summary.py            the incremental Summary accumulator (§4.14)
    serve.py              SessionServer, the daemon's socket loop (§4.15)
    resource_observer.py observer_windows.py   the observer socket (contracts 2, 3)
  docs/                   CGPCTL-SPEC, CLI-SPEC (generated surface), PROTOCOL,
                          NATIVE-DEPLOYMENT, CONSUMERS, DESIGN-GUIDE
  README.md               product overview and quickstart
  ATTACH-GUIDE.md         how to profile a gate; "Profiling a run-gate lane"
  tests/
```

`util.py`, `access.py` and `targets.py` already exist — **read them before
writing anything**; they define the idioms the rest of the package follows.

---

## 3. Run directory

```
<out-dir>/<run-id>/
  manifest.json    run metadata, resolved targets, host facts, limit snapshots
  samples.jsonl    one JSON object per sample tick
  marks.jsonl      phase marks — appended by `cgpctl mark`, possibly from
                   inside another container sharing the directory
  events.jsonl     detected events
  damon.jsonl      DAMON region snapshots (only when --damon)
  report.html      interactive report
  report.md        markdown twin
  charts/*.png     static charts for the markdown twin
```

`marks.jsonl` is written by processes the profiler does not control. Appends
must be a single `write()` of one line under `O_APPEND` so concurrent writers
interleave cleanly; readers must tolerate a truncated final line.

### 3.1 Sample record

```jsonc
{
  "seq": 41,
  "t": 1754325600.123,        // wall clock epoch seconds
  "mono": 12.345,             // seconds since run start — the report's x-axis
  "cg": {
    "/dev.slice/dev-background.slice": {
      "mem":     {"current": 0, "peak": 0, "swap_current": 0, "swap_peak": 0, "zswap_current": 0},
      "memstat": {"anon": 0, "file": 0, "...": 0},
      "memev":   {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0},   // memory.events.local
      "memev_h": {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0},   // memory.events (hierarchical), since 1.1.0
      "swapev":  {"high": 0, "max": 0, "fail": 0},                           // memory.swap.events, since 1.1.0 (fail = swap-out allocation failures)
      "psi_mem": {"some_avg10": 0.0, "some_total": 0, "full_avg10": 0.0, "full_total": 0},
      "psi_cpu": {...}, "psi_io": {...},
      "cpu":     {"usage_usec": 0, "user_usec": 0, "system_usec": 0,
                  "nr_throttled": 0, "throttled_usec": 0},
      "io":      {"254:0": {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}},
      "pids":    {"current": 0, "peak": 0},
      "cgstat":  {"nr_descendants": 0, "nr_dying_descendants": 0}
    }
  },
  "proc": {"324357": {"rss_anon": 0, "rss_file": 0, "swap": 0,
                      "utime": 0, "stime": 0, "read_bytes": 0, "write_bytes": 0}},
  "host": {
    "meminfo": {"MemTotal": 0, "MemFree": 0, "MemAvailable": 0, "Zswap": 0, "Zswapped": 0, "...": 0},
    "vmstat":  {"pswpin": 0, "pswpout": 0, "zswpin": 0, "zswpout": 0, "zswpwb": 0, "...": 0},
    "psi":     {"memory": {...}, "cpu": {...}, "io": {...}},
    "ksm":     {"pages_sharing": 0, "general_profit": 0},
    "zswap":   {"max_pool_percent": 25, "compressor": "zstd"},
    "loadavg": [0.0, 0.0, 0.0],
    "cpu":     {"total_jiffies": 0, "idle_jiffies": 0}
  }
}
```

A group that could not be read is **absent**, never present-and-zero. Groups a
target did not ask for (`@metrics=mem+io`) are absent too.

---

## 4. Module contracts

Signatures below are binding — other agents code against them. Where a
docstring says *"must"*, a test asserts it.

### 4.1 `metrics.py` (agent A)

```python
GROUPS: tuple[str, ...] = ("mem", "memstat", "memev", "psi", "cpu", "io", "pids", "cgstat",
                           "swapev", "memev_h")   # the last two since 1.1.0 (CP-29)

def sample_cgroup(abs_path: str, groups: Optional[Set[str]] = None) -> Dict[str, Any]
def sample_proc(pid: int) -> Dict[str, Any]          # {} if the pid is gone
def sample_host(proc_root: str = "/proc", sys_root: str = "/sys") -> Dict[str, Any]
def cpu_cores_used(prev: Dict, cur: Dict, dt: float) -> Optional[float]
def io_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Dict[str, Optional[float]]]
def memstat_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Optional[float]]
```

`psi` is one group producing three keys (`psi_mem`, `psi_cpu`, `psi_io`).
`memev` is `memory.events.local` (this level's own breaches); `memev_h` is the
hierarchical `memory.events` (self plus descendants), kept as a separate group
so a parent's count never dilutes the per-level reading. `swapev` is
`memory.swap.events` (`high`, `max`, `fail`); `fail` counts swap ALLOCATION
failures (a swap-out refused because `memory.swap.max` or the host's swap was
exhausted; never a swap-in) and is the only cgroup-level evidence of swap
exhaustion. Both read `{}` when the file is absent, never zeros, like every
other group.

Target option grammar (`cgprofile/targets.py` `_parse_options`): the options of a
`--target`/`--observe` spec are the text after the LAST `@`, and only when
every comma-separated item's key is one of `follow`, `nofollow`, `metrics`,
`as`, `role`, `subpath`; otherwise the whole text is the value, because `@` is
legal in a cgroup name (`/user.slice/user-1000.slice/user@1000.service`). A
trailing bare `@` is the escape for a name whose last `@`-segment is itself an
option word (`cgroup:/a@follow@` is the path `/a@follow` with no options).

`memstat_rates` must expose at minimum, all per second and all `None`-safe:
`pgfault`, `pgmajfault`, `pgscan`, `pgsteal`, `pswpin`, `pswpout`, `zswpin`,
`zswpout`, `zswpwb`, `workingset_refault_anon`, `workingset_refault_file`.

Those last four are the ones that answer the motivating question. **`rfd/s`**
(anon refaults actually served from *disk* swap) is
`max(0, Δworkingset_refault_anon − Δzswpin) / Δt`, matching the host's existing
`soulmask-zswap-monitor.py` definition — reproduce that formula exactly so the
two tools can be read side by side. **`rfz/s`** is `Δzswpin / Δt`, **`rff/s`**
is `Δworkingset_refault_file / Δt`.

### 4.2 `limits.py` (agent A)

```python
@dataclass
class LimitSet:                     # one cgroup's own declared values
    memory_min: Optional[int]; memory_low: Optional[int]
    memory_high: Optional[int]; memory_max: Optional[int]
    memory_swap_max: Optional[int]; memory_swap_high: Optional[int]
    memory_zswap_max: Optional[int]; memory_zswap_writeback: Optional[bool]
    cpu_quota_usec: Optional[int]; cpu_period_usec: Optional[int]
    cpu_weight: Optional[int]
    io_max: Dict[str, Dict[str, int]]; io_weight: Optional[int]
    io_bfq_weight: Optional[int]; pids_max: Optional[int]

@dataclass
class Effective:
    cgroup: str
    chain: List[Tuple[str, LimitSet]]        # leaf-first, index 0 is the cgroup
    memory_max: Optional[int];  memory_max_by: Optional[str]
    memory_high: Optional[int]; memory_high_by: Optional[str]
    memory_swap_max: Optional[int]; memory_swap_max_by: Optional[str]
    strict_min: int; recursive_min: int
    strict_low: int; recursive_low: int
    protection_mode: str                     # "strict" | "recursive"
    cpu_cores: Optional[float]; cpu_cores_by: Optional[str]
    io_max: Dict[str, Dict[str, int]]; io_max_by: Dict[str, str]
    pids_max: Optional[int]
    warnings: List[str]

def read_limits(abs_path: str) -> LimitSet
def mount_flags(proc_root: str = "/proc") -> Set[str]
def effective(cgroup: str, root: str = CGROUP_ROOT,
              flags: Optional[Set[str]] = None) -> Effective
def fingerprint(eff: Effective) -> str       # stable hash for drift detection
def describe(eff: Effective) -> List[str]    # human lines for the report
```

**Caps (`memory.max`, `memory.high`, `memory.swap.max`, `cpu.max`, `io.max`,
`pids.max`) are tightest-wins over the whole chain.** `util.tightest()` already
does this; record *which* ancestor bound it in the `*_by` field, because "your
container says 12G but `dev-interactive.slice` says 8G" is the single most
useful thing this tool prints.

**Protections (`memory.min`, `memory.low`) are not caps and need both readings:**

- `strict_*` = `min(declared over the chain)`. A zero anywhere makes it zero.
  This is what actually holds when the cgroup2 mount lacks
  `memory_recursiveprot`.
- `recursive_*` = `min(declared over the chain, skipping zeros)`. This is what
  would hold *with* `memory_recursiveprot` and no competing siblings.

`protection_mode` is `"recursive"` when `memory_recursiveprot` is in
`mount_flags()`, else `"strict"`. When the two readings differ **and** the mode
is `strict`, emit a warning naming the ancestor whose protection is being
discarded — this is a real, silent misconfiguration and the report must shout
about it.

`mount_flags()` parses `/proc/mounts` for the cgroup2 line and returns its
options as a set.

### 4.3 `store.py` (agent C)

```python
def new_run_id(prefix: str = "run", when: Optional[float] = None) -> str   # run-YYYYmmdd-HHMMSS-xxxxxxxx
class RunDir:
    def __init__(self, base: str, run_id: Optional[str] = None, create: bool = True)
    path: str; run_id: str
    def stream_path(self, name: str) -> str
    def append(self, name: str, obj: Dict) -> None      # single O_APPEND write of one line
    def read(self, name: str) -> Iterator[Dict]         # skips a truncated final line
    def write_manifest(self, obj: Dict) -> None         # atomic: tmp + os.replace
    def read_manifest(self) -> Dict
    def latest(base: str) -> Optional["RunDir"]         # staticmethod — newest run under base
```

### 4.4 `sampler.py` (agent B)

```python
@dataclass
class SamplerConfig:
    hot_interval: float = 0.25
    idle_interval: float = 2.0
    backoff: float = 1.5          # multiply toward idle when quiet
    hot_threshold: float = 0.15   # activity score above this ⇒ hot
    discovery_interval: float = 2.0
    max_duration: Optional[float] = None

def activity_score(prev: Dict, cur: Dict, dt: float) -> float   # 0.0 … 1.0
class Sampler:
    def __init__(self, membership, config, sample_fn, clock=time.monotonic, sleep=time.sleep)
    def tick(self) -> Dict          # take one sample, return the record
    def run(self, should_stop: Callable[[], bool], on_sample, on_topology) -> None
```

The adaptive rule, exactly:

- `activity_score` is the max across sampled cgroups of four normalised signals,
  each clamped to `[0,1]`: `|Δmemory.current|/dt ÷ 8 MiB/s`, CPU cores used ÷
  cores available, `(Δrbytes+Δwbytes)/dt ÷ 50 MiB/s`, and `psi some_avg10 ÷ 20`.
- score ≥ `hot_threshold` ⇒ next interval = `hot_interval`.
- otherwise next interval = `min(idle_interval, current × backoff)`.
- any event, any topology change, or any phase mark ⇒ snap straight to
  `hot_interval` on the next tick.

`clock` and `sleep` are injected so the whole loop is unit-testable with a fake
clock and **no real sleeping** — tests must not take wall-clock seconds.

Series produced this way are irregular by construction; nothing may assume a
fixed step. `analyze.py` resamples.

### 4.5 `phases.py` (agent B)

`Mark` and `Phase` live in `cgprofile/model.py`. Import them; do not redeclare.

```python
def emit_mark(run_path: str, name: str, kind: str = "phase", meta: Optional[Dict] = None,
              when: Optional[float] = None) -> Mark
def load_marks(run_path: str) -> List[Mark]
def phases_from_marks(marks: List[Mark], t_start: float, t_end: float) -> List[Phase]
```

Mark IO (`emit_mark` / `load_marks` / `phases_from_marks`) is **collector-tier:
standard library only**. Changepoint detection lives in `analyze.py` and is
**library work — see §4.9**; `phases.py` does not implement it.

`phases_from_marks`: a `phase` mark opens a phase and closes the previous one;
the first phase runs from `t_start`; the last to `t_end`. `kind="event"` marks
are point annotations, not boundaries.

### 4.6 `events.py` (agent B)

`Event` and `SEVERITIES` live in `cgprofile/model.py`. Import them; do not redeclare.

```python
@dataclass
class DetectorConfig:
    psi_some_warn: float = 20.0; psi_some_serious: float = 50.0
    swap_out_warn_mb_s: float = 20.0
    victim_rfd_warn: float = 10.0        # the host monitor's "low tens" line
    victim_rfd_serious: float = 50.0

class Detector:
    def __init__(self, config: DetectorConfig, limits: Dict[str, "Effective"])
    def observe(self, prev: Dict, cur: Dict, dt: float) -> List[Event]
    def topology(self, appeared: List[str], disappeared: List[str], t: float,
                 mono: float) -> List[Event]
    def limits_changed(self, cgroup: str, old: "Effective", new: "Effective",
                       t: float, mono: float) -> List[Event]
```

Kinds to detect: `memory_high_breach` (`memory.events.high` increased),
`memory_max_breach`, `oom_kill`, `swap_fail` (`memory.swap.events.fail`
increased: a swap-out allocation refused by `memory.swap.max` or host swap
exhaustion, since 1.1.0), `psi_spike`, `swap_burst`, `zswap_refault`,
`cpu_throttled`, `limit_drift`, `cgroup_appeared`, `cgroup_disappeared`,
`victim_pressure` (an *observer*-role target whose `rfd/s` crosses the warn
line — the correlation event the whole tool exists for).

Every event carries the numbers that triggered it in `data`; the report shows
them on hover.

### 4.7 `damon.py` (agent C)

```python
def available() -> bool
def damo_usable() -> bool
@dataclass
class DamonTarget: kind: str; pid: Optional[int]; label: str   # kind: "vaddr" | "paddr"
class DamonSession:
    def __init__(self, targets: List[DamonTarget], sample_us: int = 100_000,
                 aggr_us: int = 2_000_000, kdamond_idx: int = 0)
    def __enter__/__exit__          # always stops the kdamond, even on error
    def collect(self) -> List[Dict] # classified regions + summary
```

**Use `SysfsInterface` from the vendored `cgprofile._damon.damon_analysis`
directly — do not shell out to `damo`.** `Monitor` in that library depends on
the `damo` executable, whose location is host-specific, so `Monitor` is
unusable here. `SysfsInterface` and `Classifier` are pure sysfs/pure python
and are the parts to reuse. The copy is byte-synced with
`scripts/damon-analysis/lib/damon_analysis.py` by a test; DAMON stays optional
evidence, so the profiler runs when the kernel facility is absent.

DAMON is one shared kernel facility: acquire `nr_kdamonds`, and **always**
release it on exit — including on `SIGINT`/`SIGTERM`. Writing `1` to
`nr_kdamonds` creates kdamond `0`, and writing `0` tears it down.

### 4.8 `caps.py` (agent C)

```python
@dataclass
class CapChange: cgroup: str; file: str; old: Optional[str]; new: str
class TempCaps:
    def __init__(self, changes: Dict[str, Dict[str, str]], root: str = CGROUP_ROOT)
    def __enter__(self) -> List[CapChange]
    def __exit__(self, *exc) -> None          # restores every applied change
    applied: List[CapChange]
```

Must refuse up front (raise, do not half-apply) when the cgroup root is not
writable or any named cgroup does not exist. Must restore on `SIGINT`/`SIGTERM`
as well as on normal exit — an aborted profiling run must never leave a
production host with a limit the operator did not set. Restoring a value that
was `max` means writing back the literal string `max`.

### 4.9 `analyze.py` (agent D) — **pandas + ruptures**

`Series`, `Proposal` and `Analysis` already exist in `cgprofile/model.py`. Import
them; do not redeclare them.

```python
def to_frame(samples: Iterable[Dict]) -> "pandas.DataFrame"   # tidy long form
def build_series(df) -> List[Series]
def resample_frame(df, step: str) -> "pandas.DataFrame"       # df.resample(step)
def detect_changepoints(df, columns, penalty: float = 8.0,
                        min_gap: float = 5.0) -> List[float]
def auto_phases(df, known: Sequence[Phase], t_start, t_end) -> List[Phase]
def correlate(df, observers, subjects) -> List[Dict]
def build(run: "RunDir") -> Analysis
def make_proposals(analysis: Analysis) -> List[Proposal]
```

**Use pandas for every reshape.** `to_frame` builds one tidy DataFrame indexed
by a `TimedeltaIndex` of `mono`; resampling is `df.resample(step).mean()`,
per-phase statistics are a `groupby` over a phase column assigned with
`pd.cut`, and correlation is `df.corr()`. Do not write a resampler, a rolling
window, or a correlation by hand.

**Use `ruptures` for changepoint detection.** `ruptures.Pelt(model="rbf")`
fitted on the z-scored matrix of the named columns, `.predict(pen=penalty)`,
then drop boundaries closer together than `min_gap` seconds. `auto_phases`
names the result `auto-1…auto-n` and drops any boundary within `min_gap` of a
mark-derived one — an explicit label always wins over an inferred one.

`correlate`: for each observer-role target, Pearson correlation (`DataFrame.corr`)
between its `rfd/s` and `psi_mem some_avg10` and each subject target's memory
and IO rates, on the common resampled grid, **reporting `n` alongside `r`**.
State the coefficient and the sample count; do not dress a 30-sample
correlation up as a finding.

`make_proposals` derives from measurements only, and every proposal names the
evidence that produced it. Cases it must cover when the data supports them:
a slice whose children's `memory.max` sum exceeds host RAM; `memory_recursiveprot`
absent while an ancestor declares protection a leaf does not claim; a target
pinned between its own `memory.min` and `memory.high`; a subject and an
unrelated long-lived stack sharing one tier. Each proposal ships concrete
commands or unit-file lines under `change`, and a `confidence` of
`observed` / `inferred` / `speculative`.

### 4.9a Descendant summarisation (`analyze.py`)

A named target or observer is always plotted in full: the reader asked for it
by name. A cgroup that arrived only because of `@follow` did not, and on a real
host there can be dozens of them.

```python
def summarise_descendants(series: List[Series], named: Set[str],
                          top_n: int = 6) -> List[Series]
```

- Named targets and observers: untouched.
- Followed descendants, per metric per panel: keep the `top_n` by peak absolute
  value; fold the remainder into one series labelled `other (N cgroups)`.
- **How the tail folds depends on the metric, and getting this wrong invents
  data.** Additive units (`bytes`, `bytes/s`, `count`, `count/s`, `cores`) sum.
  Non-additive ones (`pct` — PSI is a saturation percentage, and `ratio`) must
  **not** be summed; take the max and label it `other (max of N)`.
- A fold across a timestamp where some members are `None` uses the members that
  are present, and is `None` only when all are.

The full per-cgroup data stays in `samples.jsonl.gz` either way — this bounds
the *report*, never the capture.

### 4.10 `report_md.py` (agent D)

```python
def render(analysis: Analysis, out_dir: str) -> str    # returns report.md path
```

Markdown twin plus matplotlib PNGs into `charts/`. matplotlib 3.11 and numpy
2.4 are available; use `matplotlib.use("Agg")`. Colours come from the palette
in §5 — the same slots as the HTML so the two reports read as one thing.
Every chart also appears as a markdown table (or a link to the CSV) so no
value is chart-only.

### 4.11 `report_html.py` (agent E) — **plotly**

```python
def render(analysis: Analysis, out_path: str, title: Optional[str] = None,
           plotlyjs: str = "inline") -> str
```

**Build the charts with plotly; write no charting code by hand.** There is no
hand-rolled canvas, no custom zoom maths, no bespoke tooltip engine in this
file — plotly already does all of it, correctly, and the job here is to
configure it well.

- `plotly.subplots.make_subplots(rows=…, shared_xaxes=True,
  vertical_spacing=…)`, one row per panel present in `analysis.groups()`.
- `fig.update_layout(hovermode="x unified")` — that *is* the required
  crosshair-plus-one-tooltip-listing-every-series behaviour.
- Zoom (wheel + drag-select), pan, double-click reset, legend toggling, and PNG
  export come from plotly's modebar; enable scroll zoom via
  `config={"scrollZoom": True, "displaylogo": False}`.
- Resolution selector: `fig.update_layout(updatemenus=[…])` swapping between
  pre-computed raw / 1 s / 5 s / 30 s trace sets (resampled in `analyze.py`,
  not here). A `rangeslider` on the bottom axis gives horizontal movement
  within the chosen window.
- Phase bands: `fig.add_vrect(x0, x1, annotation_text=…, layer="below",
  line_width=0)`.
- Event markers: a `Scatter` trace in marker mode with `hovertext` carrying
  kind, severity, target, message and the triggering numbers — a trace, not
  `add_vline`, so the events are hoverable and legend-toggleable.
- `fig.write_html(out_path, include_plotlyjs=plotlyjs, full_html=True,
  config=…)`. `plotlyjs="inline"` yields one self-contained ~5 MiB file;
  `plotlyjs="directory"` writes `plotly.min.js` beside the report for a slim
  variant. Either way the bytes come from the **installed package** — a local
  read, never a CDN.

Around the figure, hand-written HTML escaped with `html.escape` (**jinja2 is
NOT a dependency of this project** — an earlier draft of this file wrongly said
it was) for:

- a header with run id, duration, targets, and the effective-limits table;
- the events list and the proposals, each with its evidence and confidence;
- a **table view** per panel in a collapsed `<details>`, so no value is
  reachable only by hovering;
- light and dark via `prefers-color-scheme` **and** a `data-theme` toggle that
  wins in both directions — pass the matching plotly template
  (`plotly_white` / `plotly_dark`) and re-style on toggle;
- wide content scrolling inside its own container; the page body never scrolls
  horizontally.

Labels (cgroup paths, container names) are untrusted input: render them through
jinja2's autoescaping or `textContent`, never string-concatenated into HTML.

Hard rules from the house data-viz standard, all of which apply here:

- **Never a dual-axis plot.** Two measures of different scale get two panels.
- Series colour follows the entity; filtering series must not repaint the
  survivors.
- ≥2 series ⇒ a legend is always present; direct-label selectively (the
  extreme, the endpoint), never a number on every point.
- Thin marks: 2 px lines, hairline solid grid (never dashed), recessive axes.
- Labels are untrusted data (cgroup paths, container names) — insert with
  `textContent`, never `innerHTML` concatenation.
- Hit targets ≥ 24 px.
- Past 8 series in a panel, fold the tail into "Other" — never generate a 9th
  hue.

### 4.12 `cli/cgpctl.py` and `cli/cgpd.py` (integrators — not an agent's files)

The grammar is the cli-extended registry of each module; the generated
inventory is `docs/CLI-SPEC.md` and the behaviour is `docs/CGPCTL-SPEC.md`.
In outline:

```
cgpctl run [--phase N] [--out-dir D] [--propagate-exit] -- <command>   direct mode
cgpctl mark <name> [--kind phase|event] [--meta JSON]                  appends to $CGPROFILE_RUN_DIR
cgpctl report <session> | --run-dir D   [--out D] [--html-only|--md-only|--export-only D]
cgpctl start --target T [...] / stop / watch / show / sessions / status / host / prune
cgpctl config show | set KEY VALUE [--persist] | reset KEY|--all
cgpctl doctor | skills install|uninstall|check|list
cgpd run | unit print|check | config check | reload [--reset-overrides]
```

The daemon's settings are the keys of `cgpd.toml` (`daemon_config.py`), not
command-line flags.

---

### 4.13 `subtree.py` (RG-55 C2) — token → pid-subtree resolver

`SubtreeResolver(cgroup, cgroup_root, token, proc_root)`: direct target-cgroup
PIDs come from `cgroup.procs`, or from host `/proc/<pid>/cgroup` resolution
when the selected PID view cannot represent those entries (for example, a
private PID namespace renders them as zero). A pid belongs to
the profiled lane iff it is directly in the target cgroup and
`/proc/<pid>/environ` (NUL-separated, `errors="replace"`) carries
`RUN_GATE_PROFILE_SESSION=<token>`, plus every descendant of such a pid
(walked via `/proc/<pid>/task/*/children` when the kernel exposes it, else
a ppid map built from `/proc/*/stat`, parsed past the last `)` since a
command containing spaces/parens would otherwise misalign the fixed-field
parse). `refresh()` re-resolves from scratch and returns the current set;
`current_pids` is the last-refreshed cache. `targets_seen` (owned by the
caller, `summary.SummaryAccumulator`) is the union of every pid ever
returned across the session's life, not merely the current set — a worker
that exits mid-session still counts. An unreadable `environ` (a zombie, a
pid that vanished between `cgroup.procs` and the `/proc/<pid>/environ`
read) is skipped, never raised — exactly the "absent is not zero, and
neither is it fatal" rule §1 states for the rest of this package.

### 4.14 `summary.py` (RG-55 C1) — the incremental Summary accumulator

`SummaryAccumulator` implements `RG55-INTERFACE-CONTRACT.md` §3/§7 exactly:
fed one tick at a time (`add_sample(cgroup, host, slice_cgroup, damon,
pids)`), it produces the Summary document `ctl stop` returns
(`finalize(ended_at)`) in O(1) wall time regardless of session length — the
one O(samples) cost (nearest-rank percentiles) is paid incrementally, one
append per tick, never re-scanned at `stop` time (the contract's own 30 s
budget for `stop`, §1.5, depends on this). Percentiles are nearest-rank
over the STORED per-sample values (ints; memory for hours of 1 Hz sampling
is cheap enough not to bound). Null discipline is the same rule `util.py`'s
readers already follow, applied to every derived field: a delta or
percentile whose *inputs* were ever unreadable is `null`, never computed
against a substituted zero. `sample_target_cgroup`/`sample_slice_cgroup`
are thin wrappers around `metrics.sample_cgroup` (plus the two limit-drift
fields, `memory.max`/`memory.high`, that group does not itself read — see
that function's own docstring for why summary.py reads them directly
rather than threading `limits.py` through the sampler).

### 4.15 `serve.py` (RG-55 C4/C5) — the session server + CLI verbs

`SessionServer` listens on a Unix socket (JSON-lines request/response, one
request per connection — `_accept_loop`/`_handle_connection`), running one
sampling thread per live session at a FIXED cadence (the adaptive
hot/idle back-off §4.4 describes for `cgpctl run` is deliberately off
here — the contract's own computation rules, §7, assume samples land every
`interval_seconds` with no gaps). It owns: the in-memory + on-disk session
registry (restart recovery finalizes any session a previous daemon process
left `"live"` — `_recover_orphans`), retention (`keep_sessions`/
`keep_days`, run on every `stop` and on demand via `gc`), the
`WRITABLE_ROOTS` host-mutation boundary (the sessions directory and the
DAMON admin sysfs root — every raw write this module or `damon.py` issues
directly is checked against it; `RunDir`'s own writes are already confined
by construction), and SIGTERM/SIGINT handling (`serve_forever` — every
live session is finalized `aborted: "daemon-stopped"` before the socket
closes). `cli/cgpd.py` (§4.12) is the CLI layer; the client's wire module
(`ctlwire.py`) is a one-document-in/one-document-out socket call, nothing more
(`watch` excepted).

**Reports and the tier split.** This module never imports pandas and has no
report verb. The `export` operation streams a stored session as four digest-
verified parts (`docs/PROTOCOL.md` section 10.3) and `cgpctl report` rebuilds
a run directory from them and renders it in the caller's process with the
`report` extra. For that to work, every session directory this module writes
is ALSO a valid `store.RunDir` in the §3/§4.3 sense — `_manifest_for` carries
`run_id`/`started`/`ended`/`duration`/`targets`/`host`/`config` alongside
the RG-55 registry fields, and `_on_session_sample` persists `samples`
keyed by cgroup path (`{"cg": {cgroup: entry}, "mono": …}`), the same
shape `cmd_collect`'s own collector writes. `limits` was `{}` and `damon.jsonl` unread by `analyze.py` at P1's close
(CP-7/CP-6, both filed as backlog then) — the RG-55 P6 follow-ups below
closed both.

### 4.15a RG-55 P6 follow-ups (D-27..D-32) — watch, delegated placement,
the socket carrier, `cgprofile.slice`

Full rationale is the design doc of record on `main`,
`run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-
admission.md` §A1 (D-27..D-29), §A2 (D-30), A3 (D-31), and A4 (D-32); the wire shapes are
`RG55-INTERFACE-CONTRACT.md` §8 and this project's own
`docs/PROTOCOL.md`. Summary, for a reader of this file alone:

- **D-27 — the watcher is a singleton, and it is the daemon.** Every OTHER
  process that might judge a lane's liveness (run-gate's own in-process
  stall watcher, chief among them) dies with that process; the daemon
  outlives any one consumer AND already reads the lane's cgroup on a fixed
  cadence through the host `/proc` and cgroup tree. The daemon runs natively
  in the host PID namespace so DAMON sysfs can resolve host `pid_target`
  values. Host PID visibility is not granted to
  workload/gate/test containers; the D-15 boundary still forbids
  numeric PID signalling and uses systemd for delegated-scope ownership.
  Implemented as `cgprofile/liveness.py`'s `LivenessTracker` (the state machine:
  `ok`/`stalled`/`hung`/`runaway`/`throttled`/`over_ceiling`, the idle
  clock's pause condition, §8.4) wired into `cgprofile/serve.py`'s discovery
  cadence (`_observe_liveness`) and the streaming `ctl watch` verb (§8.2).
  CP-8.
- **D-28 — the run-gate client is disposable.** A consumer authors POLICY
  at `start` (`--progress-stream`/`--idle-bound`/`--ceiling`/`--on-stall`)
  and never re-implements the judgement itself; §8.9's consumer obligation
  (one `watch` per lane, `killed` → the lane's own stall-exit path,
  `reported` → a warning line) is the whole of what a caller owes.
- **D-29 — `cgprofile.slice`, the daemon's own containment.** A systemd unit
  rendered by `cgpd unit print` and installed by the host's installer
  (`docs/NATIVE-DEPLOYMENT.md` section 4), never software the daemon writes
  itself; the `host` verb's `daemon_slice`/`gates_slice` blocks (§8.5) report presence
  rather than assuming installation happened. Since 1.1.0 (Sol B4) the daemon
  does not merely report: `serve` refuses to start unless the daemon's OWN
  cgroup (derived from the host proc view) is under `cgprofile.slice` and that
  slice's `memory.max` is finite and positive (unlimited, zero, empty,
  unreadable and non-numeric all refuse), because an implicitly auto-created
  slice is unbounded and a "governed-looking" daemon without limits is the
  silent-default hazard. Unprovable counts as unbounded.
- **D-30 — the socket is the only carrier.** `/run/cgprofile/ctl.sock` is
  the daemon's control surface, `SO_PEERCRED`-gated (the `allow_uids` key of
  `cgpd.toml`), bind-mountable into any container that should speak the
  protocol (`docs/PROTOCOL.md` §1). There is no `docker exec` carrier and no
  in-image client. CP-2.
- **D-31 — systemd owns the gates slice and delegated per-lane scope.**
  `--place` asks the host manager to create a token-derived transient scope
  directly beneath the verified gates slice. After reading its actual
  `ControlGroup` and delegation properties back, cgprofile creates and owns
  only `rg-<token>` below that scope. It moves verified PIDs through the
  systemd manager bridge, reads back leaf controls, and restores only
  same-identity survivors to their recorded origins before removing the leaf
  and stopping the empty scope. The durable journal supports fail-closed
  restart recovery. Direct placement under the non-delegated slice is
  rejected. The ownership rationale and lifecycle are in
  [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md#why-placement-uses-a-delegated-scope).
- **Memory accounting is charge-based.** Leaf memory counters and
  `memory.high`/`memory.max` constrain charges attributed to the leaf, not
  total RSS; pre-existing page charges do not migrate with a process. See
  [`docs/CONSUMERS.md`](docs/CONSUMERS.md#resource-accounting-with-placement).
- **D-32 — no broker for the current trust boundary.** The privileged daemon
  retains direct system-bus and writable-cgroupfs authority. Its application
  guard does not contain arbitrary code execution; the current rootful Docker
  operator already has host-administrator authority. A future confined broker
  may reduce blast radius only if every daemon bypass, including system-bus,
  cgroupfs, and DAMON access, is removed. Full tradeoffs are in the RG-55
  design record and `docs/DESIGN-GUIDE.md`.

### 4.16 `observer_windows.py` and `ancestor_events.py` (CP-39, CP-40)

**Measurement windows (observer contract 3).** The daemon is the single source
of per-subject resource numbers, so consumers carry no fallback reader.
`WindowRegistry` (one per `SessionServer`) holds open windows, binds each to its
peer through the same `SO_PEERCRED` and TID proof as contract 2
(`resource_observer.resolve_window_peer`), and samples them all on ONE daemon
thread; request handlers only open, read counters and close, so a slow window
cannot delay another peer's answer. Two modes exist because exact cgroup numbers
need an exclusive leaf: `process-tree` (sampled RSS, `/proc/<pid>/io` lower
bound, root CPU at close) works anywhere; `cgroup` (per-fd reset `memory.peak`,
`cpu.stat`, `io.stat`, PSI deltas) is exact only while `cgroup.procs` holds
nothing but the root's tree, the peer and the peer's ancestors, and degrades to
`SHARED_CGROUP` otherwise. A window keeps maxima and sums, never a series, and
is not persisted: its id carries a per-daemon epoch so a stale id answers
`window-lost`. The wire is in [`docs/PROTOCOL.md`](docs/PROTOCOL.md#9-observer-contract-3-measurement-windows).

- **Why a single sampler thread.** Threads scale with windows (up to 4096), file
  descriptors with cgroup-mode windows (two each). One thread plus one shared
  `/proc` scan per round bounds both; a window whose peer died is dropped so it
  cannot hold a slot.
- **Why the sampling gap is accepted.** RSS peaks and foreign PIDs shorter than
  the interval are missed; `rss_peak_bytes` is documented as a lower bound and a
  window always takes a first sample at open.
- **Kernel capability is a reported fact, not a guess.** The per-file
  `memory.peak` reset is attempted at open; where the kernel refuses, the two
  peaks are null with `PEAK_RESET_UNSUPPORTED` and `kernel.peak_reset` is false.

**Ancestor memory events.** `ancestor_events.py` reads the local `memory.events`
of every ancestor from a target's parent up to `dev.slice`. The Summary reports
the delta between session start and finalization as
`ancestors_memory_events_local`; a window reports the delta over the window in
its `context`. An ancestor's local `high` or `max` event is direct evidence that
reclaim was driven from above the lane; host-wide PSI is not (on the shared host
the production service keeps it high).

---

## 5. Palette (both reports use exactly these)

Categorical slots, in fixed order — assign by entity, never cycle past 8:

| slot | light | dark |   | slot | light | dark |
|---|---|---|---|---|---|---|
| 1 | `#2a78d6` | `#3987e5` | | 5 | `#e87ba4` | `#d55181` |
| 2 | `#eb6834` | `#d95926` | | 6 | `#008300` | `#008300` |
| 3 | `#1baf7a` | `#199e70` | | 7 | `#4a3aa7` | `#9085e9` |
| 4 | `#eda100` | `#c98500` | | 8 | `#e34948` | `#e66767` |

Status (fixed, never themed, never used for a series):
`good #0ca30c`, `warning #fab219`, `serious #ec835a`, `critical #d03b3b`.

Chrome:

| role | light | dark |
|---|---|---|
| chart surface | `#fcfcfb` | `#1a1a19` |
| page plane | `#f9f9f7` | `#0d0d0d` |
| primary ink | `#0b0b0b` | `#ffffff` |
| secondary ink | `#52514e` | `#c3c2b7` |
| muted (axis) | `#898781` | `#898781` |
| gridline | `#e1e0d9` | `#2c2c2a` |
| baseline/axis | `#c3c2b7` | `#383835` |

Type: `system-ui, -apple-system, "Segoe UI", sans-serif` throughout.
`tabular-nums` only in tables and axis ticks, never on a large standalone
number.

---

## 6. Environment assumptions

- cgroup v2 at `/sys/fs/cgroup`. The effective-limit logic reads the mount
  options (`nsdelegate`, `memory_recursiveprot`, `memory_localevents`,
  `pids_localevents`) at run time and never assumes them.
- The estate's tier slices are `dev-interactive.slice` (cockpits),
  `dev-gates.slice` (gate and lane containers, and the placed `rg-*` leaves)
  and `dev-background.slice` (long-running application stacks). The names are
  `cgpd.toml` defaults (`gates_slice`, `observe_slices`), never assumptions.
- Python 3.11 or newer. The `report` extra is installed into the client's
  environment, not the daemon's.
- A `venv` created with `--system-site-packages` from an interpreter that is
  itself a venv inherits the *base* install and silently loses packages; the
  install lanes therefore use a clean venv.
- Use `SysfsInterface`/`Classifier` from the vendored DAMON library
  (`cgprofile._damon`), not the `damo` executable.
- The network may be available, but nothing is installed or fetched at run
  time (§1).

---

## 6a. Known limitations (measured, not assumed)

- **`io_max_by` attributes per device, not per field.** When two ancestors each
  cap a *different* field of the same device (one tighter on `rbps`, the other
  on `wbps`), `describe()` names only one of them. Both are genuinely binding.
  The memory and CPU attributions — the ones the report leads with — do not
  have this problem.
- **The report contains `https://` strings but fetches nothing.** plotly's
  bundle carries map-tile attribution URLs (OpenStreetMap, Carto, Mapbox) and a
  `unpkg.com` icon template inside its maplibre code path. No chart here is a
  map, so none of it is ever requested. "Self-contained" means nothing is
  fetched at render or view time — not that the string `https` is absent.
- **A slice with `@follow` produces a lot of series.** Measured: `@follow` on
  `dev-background.slice` on this host resolves 29 cgroups and 435 series,
  because that tier also holds an unrelated 26-container stack — 1742 plotly
  traces and a 6.9 MB report. See §4.9a for the summarisation rule that bounds
  this.
- **DAMON is a single shared kernel facility.** Two profiling runs with
  `--damon` at once will fight over it.

## 7. Conventions

- Python 3.11+ syntax, `from __future__ import annotations`, full type hints.
- Respect the two dependency tiers from §1: collector modules import **nothing
  outside the standard library**; analysis and report modules use the pinned
  libraries freely and should reach for them rather than write the algorithm.
- Any new dependency goes in the matching `pyproject.toml` extra, then is
  declared with a `>=` floor and a documented reason — never at run time.
- Comments explain *why*, at the density of `util.py`/`targets.py`. No
  banner comments restating the signature.
- Tests are `pytest`, live in `tests/`, use `tmp_path` and synthetic fixtures,
  and must not touch the real `/sys/fs/cgroup`, spawn a container, or sleep.
  Every reader takes a root path so a fake tree can be pointed at it.
- Never widen a limit, write to a cgroup, or start a container from a code path
  the user did not ask for.
