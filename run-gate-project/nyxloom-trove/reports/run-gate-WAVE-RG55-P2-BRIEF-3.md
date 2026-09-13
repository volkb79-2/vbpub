# run-gate-WAVE-RG55-P2 — BRIEF-3 (successor continuation)

Checkpoint cut per E-008 (~tool-call limit reached), at a coherent
boundary: C2 is now FULLY DONE and gate-verified (not just unblocked —
built, wired, run, and a real bug it found already fixed), no code left
uncommitted, LOG/REPORT updated. Written for a FRESH successor — you have
no memory of this session. C3 is untouched in code but substantially
de-risked: this session spent its last stretch reading the exact
functions C3 must touch and the exact shape of the golden fixtures, and
that research is below so you do not have to re-derive it.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there
— see BRIEF-1/BRIEF-2 for the double-checkout hazard
(`/workspaces/vbpub/run-gate-project/`, no `.worktrees/`, is the MAIN
checkout and looks identical). `git -C /workspaces/vbpub status
--porcelain -- run-gate-project/` must show nothing after every commit.

Git log at hand-off:
```
45f2aa5a fix(rg55-p2): close tools/coverage_gate.py's own diff-coverage gaps found by assay-r1
42fc2d71 feat(rg55-p2): C2 -- assay-r1/r2/r3 + gate-full lanes for run-gate-project
554d1a1a docs(rg55-p2): checkpoint after C1-rework + C2 vendoring -- LOG, REPORT, BRIEF-2
f687a4ed chore(rg55-p2): vendor assay-6.1.1.pyz for run-gate-project's assay lanes
8c76ba3e fix(rw5): coverage_gate.py 0/0 diff reports SKIPPED, not refused or silently OK
```

**C1, C1-rework, C2 are ALL DONE.** `assay.toml` (`[lanes.r1]`/`[lanes.r2]`,
RW-8's `source_roots = ["."]` scoping), `tools/canary-run.sh`,
`run-gate.toml`'s `assay-r1`/`assay-r2`/`assay-r3`/`gate-full` lanes,
`cmru.toml`'s release-gate comment, README's "Gate and evidence" section
— all written, wired, and gate-verified live:
- `selftest --allow-dirty`: **834 passed, 3 skipped**, diff-coverage
  SKIPPED (no `run-gate.py` lines in this branch's diff yet), exit 0.
- `./run-gate.py --base main assay-r1`: **PASS, exit 0** (after this
  session fixed 4 real coverage gaps in `tools/coverage_gate.py` that the
  FIRST live run found — see the REPORT's "A real finding" subsection;
  this is not decoration, it is proof the broadened judge scope actually
  catches things `selftest` structurally cannot).
- `./run-gate.py assay-r3 --allow-dirty`: **PASS, exit 0** (the canary
  correctly proves the gate rejects a broken `duration_stats`).
- `doctor`: 0 failures.
- `assay-r2` (mutation, ~15min serial per `assay plan r2`'s own estimate,
  budget 4h) deliberately NOT run — handoff's own rule: "once at the very
  end", i.e. whichever session makes the FINAL commit of this whole
  package runs it once, right before returning for good.

Full detail, exact commands, and quoted JSON: REPORT's "C2" section (new
this session) and LOG's "Session 3" + "Commit 3"/"Commit 4" sections.

## What NOT to re-read

Everything BRIEF-1/BRIEF-2 already marked read/skipped stays that way.
This session additionally read (do NOT re-read): the interface contract
in full (all 7 sections — keep the exact §7 formulas and §3 key set at
hand, they are the actual spec you implement against, not re-derivable
from memory); `fixtures/rg55/README.md` in full (the by-hand arithmetic
proof); the golden fixture FILES themselves — `frames.json`,
`frames/0/**` through `frames/4/**` (a real fake-cgroupfs tree — see
"Fixture shape" below, you do not need to re-list it, just read specific
files as you implement each parser); `summary-v1.json`,
`summary-container-v1.json`, `summary-basic-v1.json`, `host-v1.json`,
`start-v1.json`, `status-v1.json`, `stop-v1.json`, `version-v1.json`,
`error-v1.json`; `scripts/cgroup-profiler/lib/util.py` lines 60-165
(`read_text`/`read_lines`/`read_int`/`read_kv`/`read_flat_keyed`/
`read_pressure`/`read_proc_meminfo` — you need `read_int`/`read_kv`/
`read_pressure` only, string variants, see "Parsers" below);
`run-gate.py` lines 1-190 (constants, `GateError`/`GateInfraError`,
`_check_keys`/`_validate_environment`), 190-372 (`LANE_KEYS`,
`_validate_lane`, the `pins` validation you will mirror for `[profile]`),
402-437 (`_validate_config`, `load_config`), 640-780 (slice
resolution/`check_slice_memory_admission` — read for context, C3 does not
touch this), 900-1153 (history store: `start_run_record`,
`finish_run_record`, `_apply_record`, `_write_json_atomic` — C4 territory
but C3's inflight-record fields interact with it), 1677-1721
(`duration_stats` — the exact function `tools/canary-run.sh` already
targets; C4 generalizes this to `series_stats`, not touched this session),
3908-4135 (`LogStreamWatch`, `print_lane_bounds`, `make_progress_watch`),
4137-4291 (`await_container` — READ THIS ONE CAREFULLY, see "The hard part"
below), 4294-4348 (`disown_run_record`, `promote_follower`), 4351-4396
(`follow_container`), 4399-4612 (`resolve_inflight` — long, five-way
branch on owner-liveness × container-state × commit-match × `--fresh`;
you do not need to modify its DECISION logic, only make sure a re-attach's
profiling state travels with it), 4613-4746 (`run_container_lane`),
4823-4894 (`run_exec_lane` — the Popen rewrite target), 4897-4923
(`run_bare_host_lane`). `run-gate.py`'s line 15 is a single ~40 KB
changelog comment spanning every prior revision — read it ONCE if you
need revision history context, never again after (grep with `awk 'NR!=15'`
first if you need to search the file by content, or every match on that
line floods your context with the whole comment).

## Fixture shape (so you don't have to re-discover this)

`nyxloom-trove/fixtures/rg55/frames/<0..4>/` is a REAL fake cgroup-v2 +
`/proc` tree, one snapshot per simulated second:
```
frames/<k>/damon.json                                          {"hot":N,"warm":N,"cold":N,"idle":N} bytes
frames/<k>/proc/loadavg                                        "3.00 2.80 2.50 2/456 12340"
frames/<k>/proc/meminfo                                         MemTotal:/MemFree:/... (kB)
frames/<k>/proc/pressure/{cpu,memory,io}                        "some avg10=.. avg60=.. avg300=.. total=USEC\nfull ..."
frames/<k>/dev.slice/memory.{current,high,max}                  slice-level, "max" or int
frames/<k>/dev.slice/cpu.pressure
frames/<k>/dev.slice/dev-background.slice/memory.{current,high,max,pressure}
frames/<k>/dev.slice/dev-background.slice/cpu.pressure
frames/<k>/dev.slice/dev-interactive.slice/{memory.*,cpu.pressure}   (unrelated sibling slice, ignore for Summary math)
frames/<k>/dev.slice/dev-background.slice/docker-<64hex>.scope/
    cgroup.procs            pid list (daemon-only; basic path never reads this)
    cpu.pressure            container-level PSI
    cpu.stat                usage_usec/user_usec/system_usec/nr_periods/nr_throttled/throttled_usec
    io.pressure
    memory.current
    memory.events.local     low/high/max/oom/oom_kill
    memory.high             int or "max"
    memory.max              int or "max"
    memory.peak             int (container's own high-water mark; ONLY meaningful for scope="container")
    memory.pressure
    memory.stat             anon/file/kernel_stack/shmem/file_mapped/file_dirty/file_writeback/
                             pgfault/pgmajfault/pgrefill/pgscan/pgsteal/pgactivate/
                             workingset_refault_anon/workingset_refault_file/
                             workingset_activate_anon/workingset_activate_file
    memory.swap.current
    pids.current
    pids.peak
```
This is EXACTLY `contract §4.3`'s file list (`memory.current
memory.peak memory.swap.current memory.stat cpu.stat memory.pressure
cpu.pressure io.pressure memory.events.local pids.peak`), one level under
`/sys/fs/cgroup/` from inside the container's own namespace — i.e.
`docker exec <lane container> sh -c 'cat /sys/fs/cgroup/memory.current
...'` reads these bare filenames directly, no `dev.slice/.../docker-<id>.
scope/` prefix (that prefix is only how the HOST sees it / how this
fixture tree is laid out for the daemon's benefit).

`host-v1.json`/`status-v1.json`/`start-v1.json`/`version-v1.json`/
`stop-v1.json`/`error-v1.json` are static golden RESPONSES (`ProfilerClient`
test fixtures for the fake docker shim to serve) — not frame-driven.

**Key numbers you can sanity-check any implementation against** (from
`fixtures/rg55/README.md`'s own "Headline numbers" section — copy this
table into your own scratch notes, it is the fastest smoke test): peak
(container-shared) = 734003200 B; peak (container) = 796917760 B; p90 =
734003200 B; median = 629145600 B; cores_avg = 1.0; cores_max = 2.0;
memory_full_stall_seconds (container) = 4.8s; host memory_full_stall_seconds
= 1.8s; limit_drift = 1; memory_high_breach = 1; oom_kill = 0; pids.peak = 5.

**Two gotchas already worked out, do not re-derive:**
1. With exactly 5 samples, nearest-rank p90 (`ceil(0.9*5)=5=N`) ALWAYS
   equals the sample maximum — `p90_bytes == peak_bytes` for
   `container-shared` scope is not a fixture bug.
2. `summary-basic-v1.json`'s `host.slice` is `null` and `target.
   targets_seen` is `null` — **the basic path NEVER computes `host.slice`
   at all** (contract §4.3 says so explicitly), which means
   `ResourceAccumulator` in "basic" mode needs NO slice-level sampling
   whatsoever — one whole axis of complexity (slice cgroupfs reads) simply
   does not exist for C3. Only the DAEMON (P1, a different package)
   computes `host.slice`.

## Parsers to port (contract §4.3, attribute in a comment)

From `scripts/cgroup-profiler/lib/util.py` — STRING variants (the daemon
reads files by path; run-gate gets text back from a `docker exec ... cat`
pipeline and must parse that text, never open a path itself):

```python
def _profile_parse_int(text: str | None) -> int | None:
    """Ported from scripts/cgroup-profiler/lib/util.py:read_int (string
    variant — run-gate reads via `docker exec ... cat`, never a path
    directly). 'max' and empty read back as None (absent is never zero,
    contract §1.7)."""
    if text is None:
        return None
    text = text.strip()
    if text in ("", "max"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _profile_parse_kv(text: str | None) -> dict[str, int]:
    """Ported from .../read_kv (string variant). Non-integer values are
    skipped, not fatal — a newer kernel field must not blind the rest."""
    out: dict[str, int] = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


def _profile_parse_pressure(text: str | None) -> dict[str, float]:
    """Ported from .../read_pressure (string variant). {'some avg10=.. avg60=..
    avg300=.. total=..', 'full ...'} -> {'some_avg10': .., 'some_total': ..,
    'full_avg10': .., 'full_total': ..} — total is MICROSECONDS (contract
    §1.6 converts to seconds only in the final Summary, not here)."""
    out: dict[str, float] = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]  # "some" | "full"
        for kv in parts[1:]:
            key, _, val = kv.partition("=")
            try:
                out[f"{kind}_{key}"] = float(val)
            except ValueError:
                continue
    return out
```
Verified these three are sufficient for every §4.3 file: `memory.current`/
`memory.peak`/`memory.swap.current`/`pids.peak`/`memory.max`/`memory.high`
→ `_profile_parse_int` (memory.max/high can be `"max"` → `None`, contract
§1.7 "absent is never zero" — a `null` `memory_max_bytes` in the eventual
Summary... actually contract §3 does not list `memory.max`/`memory.high`
as Summary fields directly — they only feed `events.limit_drift` (a
sample-pair CHANGE detector, §7: "`limit_drift` = number of sample pairs
where `memory.max` or `memory.high` changed" — compare the raw per-sample
values, "max" vs "max" is unchanged, "max" vs an int IS a change, two
different ints IS a change); `memory.stat`/`cpu.stat`/
`memory.events.local` → `_profile_parse_kv`; `memory.pressure`/
`cpu.pressure`/`io.pressure` → `_profile_parse_pressure`.

## Config layer (straightforward, do this first)

Constants — insert after `PROGRESS_POLL_SECONDS` (~line 107), before
`class GateError` (~line 110):
```python
PROFILE_DAEMON_DEFAULT = "cgprofile-host-daemon"
PROFILE_SAMPLE_SECONDS = 5          # state the reason: docker-exec overhead
                                     # per tick vs. sample resolution, HOST
                                     # LOAD §6 (8 cores, shared production host)
PROFILE_CTL_TIMEOUTS = {"version": 5, "host": 5, "start": 10,
                        "status": 5, "stop": 30}   # contract §1.5, verbatim
PROFILE_CONTRACT = 1                # contract §1.4
PROFILE_TOKEN_ENV = "RUN_GATE_PROFILE_SESSION"     # contract §4.1
```

`LANE_KEYS` (line 195) gains `"profile"`. `_validate_config`'s top-level
key set (line 404, currently `{"schema_version", "environments", "lanes",
"history"}`) gains `"profile"` and `"footprint"`, each with its own
`_validate_*_policy` function mirroring `_validate_history_policy`
(line 375) EXACTLY in shape (whole-table shadowing, R-09 — a project
`[profile]` table replaces the central one ENTIRELY if present, never a
per-key merge; same pattern `resolve_history_keep` line 391 already
implements, write an analogous `resolve_profile_settings` for `[profile]`
and something similar for `[footprint]` when C5 needs it — C3 only needs
the validator + a settings resolver, not the footprint one).

`[profile]` keys: `enabled` (bool, default true), `daemon` (str, default
`PROFILE_DAEMON_DEFAULT`), `interval` (the SAME `_validate_budget` grammar
as `budget`/`stall_timeout`, default `"1s"`), `damon` (bool, default
true). `[footprint]` keys (C5, but cheap to validate now so C3's lane
validation doesn't need a second pass later): `tolerance_pct` (int >= 0,
default 25), `max_age_days` (int >= 1, default 30).

Per-lane `profile` key, inside `_validate_lane` (line 217): legal shapes
are `profile = false` (bool — `true` is refused BY NAME as redundant,
since enabled-by-default is already the policy; direct the operator to
delete the key or use the table form) or a table `[lanes.<n>.profile]`
with `enabled`/`damon` (both optional bools). Write
`resolve_profile_settings(lane, cfg, cfg_path, central, central_path) ->
dict` (always fully populated — `enabled`/`daemon`/`interval`/`damon`,
defaults filled in) mirroring `resolve_environment`'s shape (line 470).

## ProfilerClient (design drafted, not yet written)

One `docker exec <daemon> cgprofile ctl <verb> ... --json` per method,
via `subprocess.run(capture_output=True, text=True, timeout=
PROFILE_CTL_TIMEOUTS[verb])`. Contract rule 2 promises stdout is EXACTLY
one JSON object REGARDLESS of exit code — so parse JSON FIRST, exit code
SECOND: try `json.loads(stdout)`, check `contract == PROFILE_CONTRACT`,
check `doc.get("ok")`, and only fall back to a raw exit-code/stderr-tail
message when JSON parsing itself fails or times out. This one design
choice handles every one of the test spec's per-test overrides (exit 2 +
`error-v1`, exit 3, hang, garbage stdout, `contract: 2`) with ONE code
path rather than a per-exit-code branch tree:

```python
class ProfilerClient:
    """RG55-INTERFACE-CONTRACT.md §1-2. Every method returns
    (parsed_json | None, failure_reason | None) -- NEVER raises, NEVER
    changes a lane's verdict (R-04/R-36h). The caller prints ONE warning
    per lane invocation from the reason string; `resources: null` +
    `profile_error: <reason>` is the record-level consequence, decided by
    the CALLER, not this class."""

    def __init__(self, docker: str, daemon: str):
        self.docker, self.daemon = docker, daemon

    def _ctl(self, verb: str, *args: str) -> tuple[dict | None, str | None]:
        argv = [self.docker, "exec", self.daemon, "cgprofile", "ctl", verb,
               *args, "--json"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=PROFILE_CTL_TIMEOUTS[verb])
        except subprocess.TimeoutExpired:
            return None, f"`cgprofile ctl {verb}` timed out after {PROFILE_CTL_TIMEOUTS[verb]}s"
        except OSError as exc:
            return None, f"`cgprofile ctl {verb}` could not be run: {exc}"
        stderr_tail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        try:
            doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None, (f"`cgprofile ctl {verb}` produced unparsable stdout "
                          f"(exit {proc.returncode}); stderr: {stderr_tail}")
        if not isinstance(doc, dict):
            return None, f"`cgprofile ctl {verb}` returned non-object JSON (exit {proc.returncode})"
        if doc.get("contract") != PROFILE_CONTRACT:
            return None, (f"`cgprofile ctl {verb}` reports contract "
                          f"{doc.get('contract')!r}, this client accepts "
                          f"{PROFILE_CONTRACT} only")
        if not doc.get("ok", False):
            err = doc.get("error") or {}
            return None, (f"`cgprofile ctl {verb}` refused "
                          f"({err.get('code', 'unknown')}): "
                          f"{err.get('message', stderr_tail)}")
        return doc, None

    def version(self): return self._ctl("version")
    def host(self):    return self._ctl("host")

    def status(self, session: str | None = None):
        return self._ctl("status", *([session] if session else []))

    def start(self, container_id: str, scope: str, *, token: str | None = None,
             damon: bool | None = None, interval: float | None = None,
             meta: dict | None = None):
        args = ["--target", f"containerid:{container_id}", "--scope", scope]
        if token: args += ["--token", token]
        if damon is not None: args += ["--damon", "on" if damon else "off"]
        if interval is not None: args += ["--interval", str(interval)]
        args += ["--meta", json.dumps(meta or {})]
        return self._ctl("start", *args)

    def stop(self, session: str):
        return self._ctl("stop", session)
```
NOT yet validated against the fake-docker-shim test harness (that shim
needs a NEW branch, per the handoff's test spec §3: `exec
cgprofile-host-daemon cgprofile ctl <verb> …` answering the golden
fixture, with per-test overrides — extend `fake_docker`/
`fake_docker_executing` in `tests/test_run_gate.py`, do not build a third
shim helper).

## ResourceAccumulator (design drafted, not yet written)

Pure arithmetic, no docker/filesystem access — `BasicSampler` (not yet
designed in detail; it owns the `docker exec ... sh -c 'for f in ...; do
echo "== $f"; cat ... 2>/dev/null; done'` invocation and MUST parse its
multi-file output by splitting on the `== <path>` markers it prints, then
feed each per-tick reading into this class) calls `add_sample` once per
tick, `finish()` computes the exact schema-1 Summary per contract §7.

Constructor needs `scope` (`"container"` | `"container-shared"` — governs
the peak-bytes formula, §7's first bullet), and returns `method: "basic"`
always (the daemon computes its own Summary server-side and hands it back
verbatim via `stop`; this class exists ONLY for the basic-fallback path).

Per-tick container reading needed (from the three parsers above, keyed
exactly as `_profile_parse_kv`/`_profile_parse_pressure` naturally
produce them — do not re-key, wire the parser output straight through):
`memory_current` (int), `memory_peak` (int|None, container scope only),
`memory_swap_current` (int|None), `memory_stat` (dict: `anon`, `file`,
`pgmajfault`, `workingset_refault_anon`, `workingset_refault_file`),
`cpu_stat` (dict: `usage_usec`, `throttled_usec`, `nr_throttled`),
`memory_pressure`/`cpu_pressure`/`io_pressure` (dicts:
`some_total`/`full_total` in MICROSECONDS — convert to seconds only in
`finish()`, per §7's own `/1e6` formulas), `memory_events_local` (dict:
`oom_kill`, `high`), `memory_max`/`memory_high` (int|`"max"`|None — keep
the RAW parsed value, not just an int, because `limit_drift` needs to
detect "max"→int and int→int transitions, not just "did the int change"),
`pids_peak` (int|None, last-read wins per §7).

Per-tick host reading: `/proc/pressure/memory` and `/proc/pressure/cpu`
parsed via `_profile_parse_pressure` — only the FIRST and LAST sample's
avg10/avg60/full_avg10/full_avg60 land in `host.start`/`host.end`; the
`total` fields at first/last feed `host.memory_full_stall_seconds`/
`host.memory_some_stall_seconds` (delta / 1e6). `host.slice` is ALWAYS
`null` for basic (see "Fixture shape" gotcha #2 above) — do not build any
slice-sampling path into this class at all.

`baseline_bytes` = the FIRST sample's `memory_current` (§7: "sample 0 ...
supplies baseline_bytes" — true for BOTH scopes, contract §3's
"`baseline_bytes` is `memory.current` at session start in both scopes").

§7 formulas to implement EXACTLY (re-read the contract's §7 verbatim
before coding — this list is a checklist, not a restatement you should
trust over the source):
- `memory.peak_bytes`: scope `container` → LAST read of `memory_peak`;
  `container-shared` → max over all samples' `memory_current`.
  `peak_over_baseline_bytes` = `peak_bytes - baseline_bytes`, floored at 0
  (use `max(0, ...)`, not a bare subtraction — a scope="container" run
  where the daemon/sampler started AFTER a spike could otherwise go
  negative).
- `p90_bytes`/`median_bytes`: nearest-rank over SAMPLED `memory_current`
  ONLY (never `memory_peak`, even for scope="container" — the fixture
  README's own gotcha #1 above is the reason this distinction is
  load-bearing): sort ascending, `rank = ceil(p/100 * N)` (1-based, N =
  sample count), take that element. `median` is p=50. Implement with
  `math.ceil`, integer rank, `sorted_values[rank - 1]` — no interpolation,
  matching the contract's own "so two stdlib implementations agree
  byte-for-byte" requirement.
- `swap_peak_bytes`/`anon_peak_bytes`/`file_peak_bytes`: max over samples.
- `cpu.seconds` = `(usage_usec[-1] - usage_usec[0]) / 1e6`; `cores_avg =
  seconds / duration_seconds`; `cores_max` = max over CONSECUTIVE sample
  pairs of `(delta_usage_usec / 1e6) / delta_t` (delta_t is normally
  `interval_seconds`, but see "the hard part" below re: real-world jitter
  — the fixture's frames are exactly 1s apart by construction, so this
  detail does not bite the golden-fixture test, but a REAL basic-path run
  samples every 5s with real wall-clock drift between `docker exec`
  calls; consider timestamping each sample with `time.monotonic()` at
  collection time rather than assuming a fixed interval, so `cores_max`
  stays honest under real scheduling jitter — NOT covered by any golden
  fixture, your own judgment call, record it either way).
  `throttled_seconds` = `(throttled_usec[-1] - throttled_usec[0]) / 1e6`;
  `nr_throttled` = `nr_throttled[-1] - nr_throttled[0]`.
- `pressure.*_stall_seconds` = `(total[-1] - total[0]) / 1e6` per PSI
  file/kind (memory some+full, cpu some only, io some+full — per contract
  §3's exact key set, NOT every kind of every file).
- `faults.*` = deltas (last - first) of `pgmajfault`/
  `workingset_refault_anon`/`workingset_refault_file`.
- `pids.peak` = last read.
- `events.oom_kill`/`memory_high_breach` = deltas of `memory_events_local`
  `oom_kill`/`high`; `limit_drift` = COUNT of sample-PAIRS (not a single
  delta) where `memory_max` OR `memory_high`'s raw value changed between
  consecutive samples (see fixtures/rg55/README.md's own worked example:
  memory.high changes at exactly one transition → `limit_drift = 1`, even
  though `memory.max` never changes at all in that fixture — the two
  fields are OR'd together per pair, not counted separately).
- A field whose inputs were unreadable at EITHER end of a delta is `null`,
  never a delta against nothing (§7's own explicit rule) — thread `None`
  through every delta computation rather than treating a missing reading
  as 0.
- `round(x, 3)` on every float; bytes are NEVER rounded (they're already
  integers).

**Test this class FIRST, standalone, against the golden frames directly**
(read `frames/<k>/...` files yourself in the test, bypass BasicSampler/
docker entirely) — reproduce `summary-basic-v1.json` byte-for-byte via
`json.dumps(..., sort_keys=True)` equality or a dict `==`, for BOTH scope
values against `summary-v1.json` (container-shared) and
`summary-container-v1.json` (container). This is the single highest-value
test in all of C3: if it passes, the arithmetic is provably right and
everything else (BasicSampler's docker-exec plumbing, the await_container
wiring) is "just" plumbing around a proven core.

## Token generation + wiring points (not started)

`secrets.token_hex(16)` per invocation (import `secrets` — not currently
imported at the top of `run-gate.py`, add it alphabetically with the
other stdlib imports at line ~17-30). `-e RUN_GATE_PROFILE_SESSION=<token>`
must be appended in the SAME place `forward_env` values are appended:
- `run_container_lane` (line ~4669-4672): `for key in env.get("forward_env",
  []): ... argv += ["-e", f"{key}={value}"]` — add the token flag
  immediately after this loop, BEFORE `argv += [env["image"], "bash",
  "-c", inner]` (line 4680).
- `run_exec_lane` (line ~4867-4870): same pattern, `for key in
  (CGROUP_ENV_VAR, *env.get("forward_env", [])): ...` — add after, before
  `argv += [name, "bash", "-c", inner]` (line 4871).

Record `profile_token` in the inflight record (`run_container_lane`'s
`write_inflight_record(...)` call, line ~4709-4742 — add a `profile_token`
key to that dict) and `profile_session` after a successful `start` (this
needs a SECOND write to the inflight record post-start, or folding the
`start` call INTO `run_container_lane` before `await_container` is
invoked — your call how to sequence it, but the record must reflect
`profile_session` before `await_container`'s own re-attach/stall paths
might need to read it back for `stop`).

## The hard part: `await_container`'s polling granularity

`await_container` (line 4137) currently polls via `proc.wait(timeout=
PROGRESS_POLL_SECONDS)` in a `while True` loop (line 4196,
`PROGRESS_POLL_SECONDS = 30`). The handoff wants the basic sampler
ticking every `PROFILE_SAMPLE_SECONDS = 5` DURING that same wait — i.e. a
container running for 12 seconds must produce >= 2 samples (the C3 test
spec's own red-first requirement) despite `await_container`'s own poll
interval being 6x coarser. Two shapes are viable, THIS SESSION DID NOT
CHOOSE ONE — read `await_container`'s full body (4137-4291) before you
decide, its stall-detection and log-draining ordering (RW-15/RW-27/
round-3-review, all documented in `__revision__ = 40`'s changelog at line
15) is exactly the kind of adversarially-hardened logic a naive change
can silently break:

1. **Shrink the inner `proc.wait(timeout=...)` to `PROFILE_SAMPLE_SECONDS`
   when profiling is active**, sampling on every wake and separately
   tracking "has `PROGRESS_POLL_SECONDS` elapsed since the last progress-
   watch poll" with its own counter — minimal structural change, but two
   independent timers sharing one loop is exactly the kind of thing prior
   rounds of review on this function have found subtle bugs in (RW-27's
   own re-attach clock-seeding bug, found on a similarly "obviously
   correct" first cut).
2. **Sample from a SEPARATE background thread** (mirroring
   `LogStreamWatch`'s own daemon-thread pattern, line 3908) that ticks
   independently of `await_container`'s main wait loop entirely — keeps
   the existing 30s poll loop untouched (lower regression risk on
   already-hardened stall/log-drain logic) but adds a second thread whose
   lifecycle (start/join/exception-swallowing) must be as carefully
   reasoned about as `LogStreamWatch`'s own (daemon=True, `join()`'s
   documented caller precondition, the round-2/round-3 stall-vs-drain
   ordering bugs already found and fixed there once).

Recommendation (not a decision, just this session's lean): (2) is safer
given how much adversarial-review history is already invested in
`await_container`'s exact control flow — a new thread is an ADDITION next
to proven logic; shrinking the shared poll interval is a MODIFICATION of
it. Whichever you pick, the stop/finalize call (`ctl stop` or the basic
sampler's own final sample+summary) goes in the SAME `finally` block that
already calls `docker rm -f` (line 4253), BEFORE it — this ordering is
non-negotiable per contract §4.2 and the handoff's own text.

`run_exec_lane`'s rewrite (blocking `subprocess.run` at line 4892 →
`Popen` + `proc.wait(timeout=PROFILE_SAMPLE_SECONDS)` loop) is the
SIMPLER of the two rewrites — this function has none of
`await_container`'s stall/re-attach/follower machinery, it is a single
straight-line exec. Consider doing this one FIRST as your red-first proof
(the handoff's own suggested test: "a test that asserts >= 2 samples
during a 12 s fake exec fails before, passes after") before tackling
`await_container`'s more delicate rewrite.

## Checkpoint suggestion for whoever picks this up

Given the size of what remains (config layer -> ProfilerClient ->
ResourceAccumulator, each independently testable and each a reasonable
green sub-cluster to stop at), consider landing THREE commits rather than
one big one: (1) config + constants + parsers, tested; (2) ProfilerClient
+ ResourceAccumulator, tested standalone against the golden fixtures
(no docker wiring yet); (3) BasicSampler + the `await_container`/
`run_exec_lane`/`run_container_lane` wiring, tested via the fake docker
shim's new `cgprofile ctl` branch. Each is independently revertable if a
later one turns out wrong, and each is a legitimate checkpoint boundary
under E-008 if you run out of budget mid-C3.

## Process notes (unchanged from BRIEF-1/BRIEF-2, restated for a fresh session)

- Edit tool only; `git -C <worktree> commit -F <msgfile> --only --
  <paths>`; both trailers — match whatever YOUR session's own current
  attribution instruction says.
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from
  `<worktree>/run-gate-project`, verdict read in a SEPARATE step. Targeted
  `pytest tests/test_run_gate.py -k <cluster>` while iterating (the whole
  suite takes ~118s — do not run it after every tiny edit).
- HOST LOAD (handoff §6): 8 cores shared with a production game server;
  serial pytest only; at most 2 gate containers estate-wide (`docker ps
  --format '{{.Image}}'` for `tester-unified:local` first — this
  session's own lanes are all bare-host and started none); `docker update
  --cpus=3` right after launching any container you DO start for the live
  acceptance probes (handoff §4, still fully unstarted — needs C3).
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary, write `-BRIEF-4.md`, update LOG/REPORT, commit,
  return.

## Retention prompt (paste into your own `/compact` if you need to compact mid-C3)

```
KEEP: which of the three suggested C3 sub-commits (config+parsers /
client+accumulator / sampler+wiring) are done vs in-progress, with commit
hashes for each done one; the exact §7 formula subtleties already worked
out (nearest-rank percentile on sampled memory.current NEVER memory.peak;
peak_over_baseline floored at 0; limit_drift is an OR over BOTH
memory.max/memory.high per sample-pair; host.slice is ALWAYS null for
basic-path); the await_container polling-granularity decision once made
(which of the two shapes, and why); any new decision asks and their
resolution; HOST LOAD container-count state; commit hashes for C1/
C1-rework/C2 (all done, don't re-verify unless something looks broken).
DROP: the full fixture-shape listing and parser source code (both
re-readable in one call from disk / this brief); the ProfilerClient/
ResourceAccumulator draft code above once you've actually written and
tested the real versions -- keep only "matches the golden fixtures,
commit <hash>" as the pointer.
```
