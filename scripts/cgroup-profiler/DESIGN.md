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

## 1. Why the shape is what it is

**The devcontainer cannot see the host cgroup tree.** `/sys/fs/cgroup` inside it
is a read-only, cgroup-namespaced view of its own subtree; `dev.slice`,
`wings.slice` and everything else simply do not exist there. Shuttling
individual file reads across a container boundary cannot sustain a 250 ms
cadence, so instead the profiler **re-executes its entire self** inside a
privileged helper container (`--privileged --user 0:0 --cgroupns=host
--pid=host`) with the repo and the output directory bind-mounted. Sampling code
is then byte-identical in both modes, and `access.py` is the only module that
knows the difference.

**Bind sources are host paths.** A path handed to the Docker daemon is resolved
on the host, not inside this container, so `/workspaces/vbpub` must be
translated to `/home/vb/volkb79-2/vbpub` before it can be mounted into the
helper. `access.host_path_map()` derives that from our own container's mount
table — it is discovered, never configured.

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
  `events`, `store`, `caps`, `damon`, `phases`' mark IO) is **standard library
  only**. It is the half that runs inside the helper container, on a host under
  memory pressure, next to production. It must not need a venv, must not import
  pandas to read a counter, and must keep working in a minimal image.
- The **analyser and reports** (`analyze`, `report_html`, `report_md`,
  changepoint detection) run afterwards, outside, and **use real libraries**:
  pandas for resampling/correlation/grouping, plotly for the interactive
  report, matplotlib for the static twin, ruptures for changepoint detection,
  scipy/numpy underneath. Do not hand-roll any of that.

**Built once, never assembled at run time.** Dependencies are pinned in
`requirements.txt` and installed into `venv/` by `./setup.sh`, ahead of any
profiling run. No code path may pip-install, download, or fetch a CDN asset
while profiling — that would add exactly the load the tool exists to measure.
`plotly.js` is inlined into each report from the *installed* package, which is
a local read, not a fetch.

---

## 2. Layout

```
scripts/cgroup-profiler/
  cgprofile              thin exec shim → venv/bin/python cgprofile.py
  cgprofile.py           CLI: run | attach | mark | report | targets | doctor
  setup.sh               build venv/ from pinned requirements (run once)
  requirements.txt       pinned: pandas, plotly, matplotlib, ruptures, scipy…
  DESIGN.md              this file
  README.md              what it is, quickstart
  ATTACH-GUIDE.md        how to wrap or attach it to a gate in any repo
  lib/
    model.py      ✅ shared dataclasses (Mark/Phase/Event/Series/Proposal/Analysis)
    util.py       ✅ file readers, unit parsing, rate(), ancestors(), formatting
    access.py     ✅ direct-vs-helper detection, host path map, helper spec
    targets.py    ✅ target spec parsing, cgroup discovery, Membership
    metrics.py    ← agent A
    limits.py     ← agent A
    sampler.py    ← agent B
    phases.py     ← agent B
    events.py     ← agent B
    store.py      ← agent C
    damon.py      ← agent C
    caps.py       ← agent C
    analyze.py    ← agent D
    report_md.py  ← agent D
    report_html.py← agent E
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
  marks.jsonl      phase marks — appended by `cgprofile mark`, possibly from
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
      "memev":   {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0},
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
GROUPS: tuple[str, ...] = ("mem", "memstat", "memev", "psi", "cpu", "io", "pids", "cgstat")

def sample_cgroup(abs_path: str, groups: Optional[Set[str]] = None) -> Dict[str, Any]
def sample_proc(pid: int) -> Dict[str, Any]          # {} if the pid is gone
def sample_host(proc_root: str = "/proc", sys_root: str = "/sys") -> Dict[str, Any]
def cpu_cores_used(prev: Dict, cur: Dict, dt: float) -> Optional[float]
def io_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Dict[str, Optional[float]]]
def memstat_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Optional[float]]
```

`psi` is one group producing three keys (`psi_mem`, `psi_cpu`, `psi_io`).

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
def new_run_id(prefix: str = "run", when: Optional[float] = None) -> str   # run-YYYYmmdd-HHMMSS-xxxx
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

`Mark` and `Phase` live in `lib/model.py`. Import them; do not redeclare.

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

`Event` and `SEVERITIES` live in `lib/model.py`. Import them; do not redeclare.

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
`memory_max_breach`, `oom_kill`, `psi_spike`, `swap_burst`, `zswap_refault`,
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

**Use `SysfsInterface` from `scripts/damon-analysis/lib/damon_analysis.py`
directly — do not shell out to `damo`.** That venv's `damo` has a shebang
pointing at a host path (`/home/vb/…/venv/bin/python3`) and is not executable
from inside a container; `Monitor` in that library depends on it, so `Monitor`
is unusable here. `SysfsInterface` and `Classifier` are pure sysfs/pure python
and are the parts to reuse. Import via `sys.path` insertion relative to this
file (`../../damon-analysis/lib`), guarded so the profiler still runs when
damon-analysis is absent.

DAMON is one shared kernel facility: acquire `nr_kdamonds`, and **always**
release it on exit — including on `SIGINT`/`SIGTERM`. Verified working from the
helper container: writing `1` to `nr_kdamonds` creates kdamond `0`, and writing
`0` tears it down.

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

`Series`, `Proposal` and `Analysis` already exist in `lib/model.py`. Import
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

### 4.12 `cgprofile.py` (integrator — not an agent's file)

```
cgprofile run     [--target …] [--observe …] -- <command>     wrapper mode
cgprofile attach  [--target …] [--duration S] [--until-file F] attach mode
cgprofile mark    <name> [--kind phase|event] [--run-dir D]
cgprofile report  [--run-dir D] [--html] [--md]
cgprofile targets [--target …]                                 resolve and print
cgprofile doctor                                               access/DAMON check
```

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

## 6. Environment facts (verified on this host — do not re-derive)

- Host: 16 GiB RAM, ~70 GiB swap, zswap `zstd` at 25 % pool, KSM on.
- cgroup v2 at `/sys/fs/cgroup`, mounted **without `memory_recursiveprot`**.
- Devcontainer runs in `dev-interactive.slice`; gates run in
  `dev-background.slice` (`$CGROUP_PARENT_DEV_BACKGROUND`).
- Helper container verified working:
  `docker run --rm -i --privileged --user 0:0 --cgroupns=host --pid=host …`
  gives rw host cgroupfs, host `/proc`, and writable DAMON sysfs.
- Workspace bind: host `/home/vb/volkb79-2/vbpub` → `/workspaces/vbpub`.
- Python 3.14.6 in both the devcontainer and the helper image. The
  devcontainer's own interpreter is itself a venv (`/home/vscode/.venv`), so
  `python3 -m venv --system-site-packages` inherits the *base* install and
  silently loses matplotlib — this tool's `venv/` is fully self-contained for
  that reason.
- `scripts/damon-analysis/venv/bin/damo` is **not executable here** (host-path
  shebang). Use `SysfsInterface`/`Classifier` from that library, not `Monitor`.
- The network **is** available, but nothing may be installed or fetched at run
  time — build with `./setup.sh` first (see §1).

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
- Any new dependency goes in `requirements.txt` and is installed by `setup.sh`
  — never at run time.
- Comments explain *why*, at the density of `util.py`/`targets.py`. No
  banner comments restating the signature.
- Tests are `pytest`, live in `tests/`, use `tmp_path` and synthetic fixtures,
  and must not touch the real `/sys/fs/cgroup`, spawn a container, or sleep.
  Every reader takes a root path so a fake tree can be pointed at it.
- Never widen a limit, write to a cgroup, or start a container from a code path
  the user did not ask for.
