# RG-55 interface contract — cgroup-profiler daemon ⇄ run-gate (contract 1)

**Status: FROZEN 2026-09-12** by the vbpub controller (plan of record:
`WAVE-PLAN-2026-09-12-rg55-profiling.md` §4). Both packages code against
this file. A change after dispatch is a numbered controller ruling (RW-n in
`reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`) delivered to BOTH
implementers; nobody edits this file unilaterally. A verbatim copy lives at
`scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md`; a test in each
project asserts the two copies are byte-identical.

**Amendments:** RW-11 (2026-09-12, §4.3 basic-path file list: +`memory.max`,
+`memory.high`, 10 → 12 files); RW-21 (2026-09-12, scope `container`:
absolute-counter rule for `cpu`/`pressure`/`faults`/`events`, `§3`
nullability note for `peak_over_baseline_bytes`, `§7` new subsection).

Parties: **producer** = cgroup-profiler's daemon (`cgprofile serve`) and its
client verb (`cgprofile ctl`), running inside the container
`cgprofile-host-daemon`; **consumer** = `run-gate.py` (stdlib-only, docker
CLI only), invoking `docker exec cgprofile-host-daemon cgprofile ctl <verb>
[args] --json` from any devcontainer, worktree or instance.

---

## 1. Transport and I/O rules

1. run-gate always passes `--json`. The daemon image puts `cgprofile` on
   `PATH`; `ctl` talks to the serve loop over `/run/cgprofile/ctl.sock`
   inside the daemon container (never exposed).
2. **stdout is exactly one JSON object** (UTF-8, no other output). stderr is
   free text for diagnostics; run-gate ignores it except that on a failure
   it quotes the LAST stderr line in its single warning.
3. **Exit codes:** `0` ok; `2` bad request (the JSON still carries
   `"ok": false, "error": {"code": "<kebab-code>", "message": "..."}`);
   `3` daemon fault (serve loop unreachable, sysfs error). Any non-zero
   exit, timeout, or unparsable stdout means "profiling unavailable for
   this call" to run-gate: ONE stderr warning per lane invocation, never a
   verdict change, never an exception (R-04, R-36h).
4. **Every response carries `"contract": 1`.** run-gate accepts contract
   major 1 only; a different value is "unavailable" (warning names both).
5. **Timeouts (run-gate side, `subprocess.run(..., timeout=)`):** `version`
   and `host` 5 s; `start` 10 s; `status` 5 s; `stop` 30 s. The daemon MUST
   answer `stop` within 30 s for a session of any length: the summary is
   maintained incrementally at sampling time, never recomputed from the
   series at stop.
6. **Units:** bytes as integers; seconds as floats rounded to 3 decimals;
   timestamps ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ`; PSI averages as the
   kernel prints them (percent, float, e.g. `6.83`); PSI totals converted
   from microseconds to seconds (`*_stall_seconds`).
7. **Absent is never zero.** A measurement the daemon could not take is
   `null`. Counters that were read but never incremented are `0`.
8. Names: daemon container `cgprofile-host-daemon`; image
   `ghcr.io/volkb79-2/cgprofile:<version>` (local build tag
   `cgprofile:local`); sessions root `/var/lib/cgprofile/sessions`
   (named volume `cgprofile-sessions`); session ids
   `s-<YYYYMMDDTHHMMSSZ>-<4 hex>`.

## 2. Verbs

### 2.1 `cgprofile ctl version --json`

```json
{"ok": true, "contract": 1, "cgprofile": "1.0.0",
 "daemon": {"name": "cgprofile-host-daemon", "started_at": "2026-09-12T10:00:00Z",
            "damon": "available", "damon_default": "on",
            "sessions_live": 2, "max_sessions": 16}}
```
`damon` is `"available"` or `"unavailable:<reason>"` (e.g.
`unavailable:sysfs read-only`, `unavailable:module absent`).

### 2.2 `cgprofile ctl start … --json`

Arguments (all long options; run-gate never relies on positionals):

| option | required | meaning |
|---|---|---|
| `--target containerid:<64 hex>` | yes | the no-daemon target form. run-gate resolves the id with `docker inspect --format '{{.Id}}' <name>` BEFORE calling. The daemon resolves the cgroup itself (`targets.find_container_cgroup`, both docker cgroup drivers). |
| `--scope container\|container-shared` | yes | `container` = an ephemeral lane container (the cgroup IS the lane). `container-shared` = an exec-mode lane inside a long-lived container (cgroup numbers are container-wide, baseline-subtracted; pid attribution via `--token`). |
| `--token <str>` | no | the value run-gate exported as `RUN_GATE_PROFILE_SESSION` into the lane process (`[A-Za-z0-9._-]{8,64}`). With a token, the daemon resolves the lane's pid subtree = every pid in the cgroup whose `/proc/<pid>/environ` carries `RUN_GATE_PROFILE_SESSION=<token>`, plus their descendants, re-discovered every discovery interval. Without a token, subtree = all pids in the cgroup. |
| `--damon on\|off` | no | default = the daemon's `damon_default`. |
| `--interval <seconds>` | no | default 1.0; clamped to [0.25, 30]. |
| `--meta <json object as one string>` | yes | keys: `lane` (str), `project` (str, effective project dir), `worktree` (str), `commit` (40-hex or null), `run_gate_revision` (int), `kind` (`"command"\|"assay"`), `expected` (null or `{"memory_peak_median_bytes": int\|null, "hot_set_p90_bytes": int\|null, "cpu_cores_avg": float\|null, "duration_median_s": float\|null}` taken from the footprint manifest when present). Unknown keys are stored verbatim, never rejected. |

Response:
```json
{"ok": true, "contract": 1, "session": "s-20260912T101500Z-7f3a", "reused": false,
 "started_at": "2026-09-12T10:15:00Z", "scope": "container-shared",
 "damon": "on", "interval_seconds": 1.0,
 "target": {"container_id": "fc2c…", "cgroup": "/dev.slice/dev-background.slice/docker-fc2c….scope",
            "baseline_memory_bytes": 536870912, "pids_at_start": 3, "token": "…" }}
```
`damon` here is `"on"`, `"off"`, or `"unavailable:<reason>"` — an
unavailable DAMON never fails `start`. **Idempotent by (target, token):** a
second `start` with the same container id AND the same non-null token
returns the live session with `"reused": true` (the re-attach/follower
safety net). Errors (exit 2): `target-not-found` (no cgroup for that id —
the container already exited), `too-many-sessions` (live sessions ==
`max_sessions`, default 16), `bad-argument`. A token whose pids have not
appeared yet is NOT an error (`targets_seen` reports what was found).

### 2.3 `cgprofile ctl status [<session>] --json`

Without a session: the registry plus a host snapshot.
```json
{"ok": true, "contract": 1, "at": "…",
 "sessions": [
   {"session": "s-…", "started_at": "…", "scope": "container", "elapsed_seconds": 91.2,
    "target": {"container_id": "…", "cgroup": "…", "token": "…"|null, "targets_seen": 14},
    "meta": { …as given at start… },
    "live": {"memory_current_bytes": 700000000, "memory_peak_bytes": 746586112,
             "cpu_cores_recent": 1.9, "samples": 91,
             "damon": {"status": "on", "hot_bytes_recent": 190000000} | null}}
 ],
 "host": <HostSnapshot>}
```
With a session id: the same single session object under `"session": {…}`
plus `host`. Unknown id → exit 2 `unknown-session`.

### 2.4 `cgprofile ctl host --json` → `{"ok": true, "contract": 1, "host": <HostSnapshot>}`

**HostSnapshot** (every leaf nullable when unreadable):
```json
{"at": "…", "loadavg": [3.64, 3.35, 3.0],
 "meminfo": {"total_bytes": …, "available_bytes": …, "swap_total_bytes": …, "swap_free_bytes": …},
 "pressure": {
   "memory": {"some_avg10": 0.53, "some_avg60": 0.88, "some_avg300": 0.92, "some_total_seconds": 5761.75,
              "full_avg10": 0.53, "full_avg60": 0.88, "full_avg300": 0.92, "full_total_seconds": 4648.02},
   "cpu": { …same keys… }, "io": { …same keys… }},
 "slices": {
   "dev-background.slice": {"cgroup": "/dev.slice/dev-background.slice",
      "memory_current_bytes": …, "memory_max_bytes": null, "memory_high_bytes": …,
      "memory_swap_current_bytes": …, "pressure": {"memory": {…}, "cpu": {…}}},
   "dev-interactive.slice": {…}, "dev.slice": {…}}}
```
Slices enumerated: `dev.slice` and every `*.slice` directly under it, plus
every slice named by `serve --observe-slices a.slice,b.slice` (the game
tier). `memory_max_bytes` is `null` for `max`.

### 2.5 `cgprofile ctl stop <session> --json`

```json
{"ok": true, "contract": 1, "session": "s-…", "already_stopped": false,
 "summary": <Summary>,
 "session_dir": "/var/lib/cgprofile/sessions/s-…",
 "series": {"samples": "samples.jsonl.gz", "damon": "damon.jsonl" | null,
            "events": "events.jsonl", "host": "host.jsonl", "manifest": "manifest.json",
            "summary": "summary.json"}}
```
Idempotent: stopping a stopped session returns the stored summary with
`"already_stopped": true`. Unknown id → exit 2 `unknown-session`. `stop`
also runs retention (`gc`) for finished sessions; a live session is never
pruned.

### 2.6 `cgprofile ctl report <session> --json` → `{"ok": true, "contract": 1, "path": "…/report.html"}` (report tier inside the image; may take longer than 30 s — run-gate never calls it).
### 2.7 `cgprofile ctl gc --json` → `{"ok": true, "contract": 1, "removed": ["s-…"], "kept": 42}`.

## 3. Summary (schema 1) — the object run-gate copies into `resources`

Exact key set; every leaf nullable per §1.7 unless marked `0`-counter.

**Nullability note (RW-21):** in scope `container`, `memory.peak_over_baseline_bytes`
is **always** `null` — not a read failure and not §1.7's "absent", but
structurally not meaningful when the profiled cgroup IS the lane (see §7's
"Scope `container`: absolute counters" subsection). Consumers (history,
footprint manifest, disclosure lines) must render this null as "not
applicable" (e.g. `-` in a table), never as a `profile_error` or a missing
measurement.

```json
{
 "schema": 1,
 "session": "s-…", "daemon": {"name": "cgprofile-host-daemon", "version": "1.0.0"},
 "scope": "container" | "container-shared",
 "method": "daemon",
 "started_at": "…", "ended_at": "…", "duration_seconds": 316.204,
 "interval_seconds": 1.0, "samples": 316,
 "target": {"container_id": "…", "cgroup": "…", "token": "…"|null, "targets_seen": 14},
 "memory": {
   "peak_bytes": 746586112,
   "source": "memory.peak" | "sampled-max",
   "baseline_bytes": 536870912,
   "peak_over_baseline_bytes": 209715200,
   "p90_bytes": 700000000, "median_bytes": 640000000,
   "swap_peak_bytes": 0, "anon_peak_bytes": 700000000, "file_peak_bytes": 120000000
 },
 "cpu": {"seconds": 412.7, "cores_avg": 1.31, "cores_max": 2.94,
         "throttled_seconds": 3.2, "nr_throttled": 41},
 "pressure": {"memory_some_stall_seconds": 6.1, "memory_full_stall_seconds": 4.8,
              "cpu_some_stall_seconds": 12.0, "io_some_stall_seconds": 1.2, "io_full_stall_seconds": 0.9},
 "faults": {"pgmajfault": 1032, "workingset_refault_anon": 0, "workingset_refault_file": 2210},
 "pids": {"peak": 37},
 "damon": null | {
   "status": "on" | "unavailable", "reason": null | "…", "kdamond": 1,
   "targets_seen": 14, "samples": 300,
   "hot_bytes":  {"peak": 214000000, "p90": 190000000, "median": 120000000},
   "warm_bytes": {"peak": …, "p90": …, "median": …},
   "cold_bytes": {"peak": …, "p90": …, "median": …},
   "idle_bytes": {"peak": …, "p90": …, "median": …},
   "thresholds": {"hot_rate_pct": …, "warm_rate_pct": …, "cold_age_s": …, "idle_age_s": …}
 },
 "host": {
   "start": {"memory_pressure": {"some_avg10": …, "full_avg10": …, "some_avg60": …, "full_avg60": …},
             "cpu_pressure": {"some_avg10": …, "full_avg10": …, "some_avg60": …, "full_avg60": …},
             "loadavg1": 3.6},
   "end":   { …same keys… },
   "memory_full_stall_seconds": 12.1, "memory_some_stall_seconds": 15.0,
   "slice": {"name": "dev-background.slice", "memory_full_stall_seconds": 4.0, "memory_peak_bytes": …}
 },
 "events": {"oom_kill": 0, "limit_drift": 0, "memory_high_breach": 0}
}
```
Semantics: for scope `container`, `memory.peak_bytes` is the cgroup's
`memory.peak` (exact, `source = "memory.peak"`); for `container-shared` it
is the max of sampled `memory.current` (`source = "sampled-max"`) because
the shared container's `memory.peak` is lifetime. `baseline_bytes` is
`memory.current` at session start in both scopes. `cpu.seconds` is the
`cpu.stat usage_usec` delta; `cores_avg = seconds / duration_seconds`;
`cores_max` = max per-interval rate. Pressure and fault fields are deltas
over the session. `host.*_stall_seconds` are host-wide `/proc/pressure/
memory` total deltas; `host.slice` is the slice the target cgroup lives
under. `events` come from `memory.events.local` deltas and the limit
watcher (`0` when read and unchanged, `null` only when unreadable).

**Golden example:** `fixtures/rg55/summary-v1.json` (next to this file and
vendored byte-identical into `run-gate-project/tests/fixtures/rg55/` and
`scripts/cgroup-profiler/tests/fixtures/contract/`). The daemon's tests
assert a fake-cgroupfs session produces exactly this shape; run-gate's
tests assert this exact document round-trips into `history.json` schema 2
and the `footprint` manifest.

## 4. run-gate obligations

1. **Token:** before starting a lane container or exec, generate
   `token = secrets.token_hex(16)` and pass `-e
   RUN_GATE_PROFILE_SESSION=<token>` on `docker run` (ephemeral) and
   `docker exec` (exec lanes), in the same position `forward_env` variables
   are passed. Record `profile_token` and, after `start`, `profile_session`
   in the inflight record (R-39a).
2. **Order:** `docker run -d` / `docker exec` first, then `docker inspect
   --format '{{.Id}}'`, then `ctl start`. For exec lanes the container id is
   the persistent runner's. `ctl stop` runs in the same `finally` as the
   container removal, BEFORE `docker rm -f`, and also on the stall path
   (R-40) and on Ctrl-C (bounded by §1.5, best-effort, at most once).
3. **Basic fallback** (`method: "basic"`) when `ctl version` fails or
   `start` fails: run-gate samples the LANE container's own cgroup every
   `PROFILE_SAMPLE_SECONDS = 5` with ONE `docker exec <lane container> sh -c
   'cat …'` reading `memory.current memory.peak memory.max memory.high
   memory.swap.current memory.stat cpu.stat memory.pressure cpu.pressure
   io.pressure memory.events.local pids.peak` (12 files, RW-11 — `memory.max`
   and `memory.high` are required so the basic path can compute
   `events.limit_drift` the same way the daemon does) under
   `/sys/fs/cgroup/`, parsed with the
   three parsers ported verbatim from `scripts/cgroup-profiler/lib/util.py`
   (`read_int`, `read_kv`, `read_pressure` — string variants; attributed in
   a comment). The basic summary uses the §3 key set with `method:
   "basic"`, `session: null`, `daemon: null`, `damon: null`,
   `host.slice: null`, `target.targets_seen: null`; `host.start/end` come
   from `/proc/pressure/memory` and `/proc/pressure/cpu` read directly
   (readable from the devcontainer). A lane whose container exits before the
   first sample records `resources: null` with `profile_error`.
4. **Record shapes (history schema 2):** each `latest`/history entry gains
   `resources` (Summary | null), `profile_error` (string | null),
   `profile_ref` (`{"daemon": name, "session": id, "session_dir": path}` |
   null). `stats.passes`/`stats.completed` gain `memory_peak_bytes`,
   `memory_peak_over_baseline_bytes`, `hot_set_p90_bytes`, `cpu_cores_avg`,
   `memory_full_stall_seconds`, each `{count, min, median, max}` (median,
   never mean). Schema-1 stores are read as-is (`resources: null`), written
   back as schema 2 on the next write.
5. **Footprint manifest (`run-gate.footprint.json`, schema 1, tracked):**
   ```json
   {"schema": 1, "generated_by": "run-gate", "revision": 41,
    "distilled_at": "…", "from_commit": "…", "keep": 10,
    "lanes": {"<lane>": {
       "runs": 8, "completed_runs": 9, "scope": "container-shared", "method": "daemon",
       "duration_s": {"median": 316.2, "max": 479.0},
       "memory_peak_bytes": {"median": 746586112, "max": 812000000},
       "memory_peak_over_baseline_bytes": {"median": 209715200, "max": 260000000},
       "hot_set_bytes": {"p90_median": 190000000, "p90_max": 214000000},
       "cpu_cores": {"avg_median": 1.3, "max": 2.9},
       "memory_full_stall_s": {"median": 4.8, "max": 9.1},
       "last_commit": "…", "last_at": "…"}}}
   ```
   Numbers come from history-eligible PASS entries only (`completed_runs`
   counts completed fails as well); a lane without an eligible profiled run
   is OMITTED (absent = unknown). Keys sorted, `indent=2`, trailing newline.
6. **Disclosure lines** (stdout, exact shapes; `{…}` substituted; optional
   segments in `[…]` appear only when known):
   ```
   run-gate: host memory PSI full avg10={x}% avg60={y}%[ | slice {name}: full avg10={z}%][ | profiler {daemon} (cgprofile {ver}, damon {on|off|unavailable})]
   run-gate: profile session {id} (scope {scope}, baseline {n} MiB, damon {on|off|unavailable: reason})
   run-gate: footprint {lane}: peak {n} MiB[ (+{n} MiB over baseline)], p90 {n} MiB, {c} cores avg, {s} s stalled on memory (full)[, hot-set p90 {n} MiB]; history median peak {n} MiB ({k} runs)[ | manifest {n} MiB]
   run-gate: WARNING profiling: {reason} — {basic in-lane sampling only|no profile recorded}
   ```
7. **Config:** `[profile]` top-level table (central or project, whole-table
   shadowing per R-09): `enabled` (bool, default true), `daemon` (container
   name, default `cgprofile-host-daemon`), `interval` (duration grammar of
   `budget`, default `"1s"`, passed as `--interval`), `damon` (bool, default
   true → `--damon on`). Per lane: `profile = false` (bool) or a table
   `[lanes.<n>.profile]` with `enabled`/`damon`. `[footprint]`:
   `tolerance_pct` (int, default 25), `max_age_days` (int, default 30).
   Unknown keys refuse at load like every other table.

## 5. Daemon safety (D-15, restated for both parties)

The daemon never mutates the host: `serve` refuses `--cap` and never
instantiates `TempCaps`; every host mount except the sessions volume is
read-only from the container's point of view except the DAMON sysfs
directory, which it writes only to create/commit/stop its own kdamonds;
`--network none`; no docker socket inside the daemon (that is why targets
are `containerid:` and the consumer resolves ids). run-gate never passes
`--cgroupns=host` or `--pid=host` to anything.

## 6. Test fixtures shared by both packages

- `fixtures/rg55/summary-v1.json` — golden Summary (§3).
- `fixtures/rg55/host-v1.json` — golden HostSnapshot (§2.4).
- `fixtures/rg55/start-v1.json`, `stop-v1.json`, `status-v1.json`,
  `version-v1.json`, `error-v1.json` — golden responses.
- `fixtures/rg55/cgroupfs/` — a fake cgroup v2 tree (one scope under
  `dev.slice/dev-background.slice/`, the slices, `memory.*`, `cpu.stat`,
  `*.pressure`, `memory.events.local`, `pids.peak`) that BOTH the daemon's
  sampler tests and run-gate's basic-path tests read, so the two paths are
  proven against the same bytes.

The controller lands these fixtures with this contract; each package copies
them into its own tests tree (byte-identical, asserted by a test in each
project against `run-gate-project/nyxloom-trove/fixtures/rg55/`).

## 7. Computation rules (both implementations MUST agree; the golden fixtures are computed by these)

- Samples are taken every `interval_seconds`; sample 0 is taken at `start` (it supplies `baseline_bytes`). Samples are s_0..s_n, n ≥ 1; `samples` = n+1.
- `duration_seconds` = wall clock from `started_at` to `ended_at`.
- `memory.peak_bytes`: scope `container` → the LAST read of `memory.peak`; scope `container-shared` → max over s_0..s_n of `memory.current`. `peak_over_baseline_bytes` = `peak_bytes − baseline_bytes`, floored at 0.
- `p90_bytes` / `median_bytes`: nearest-rank percentile over the sampled `memory.current` values: sort ascending, rank = ceil(p/100 × N) (1-based), take that element; median is p = 50. No interpolation, so two stdlib implementations agree byte-for-byte.
- `swap_peak_bytes`, `anon_peak_bytes`, `file_peak_bytes`: max over samples of `memory.swap.current`, `memory.stat anon`, `memory.stat file`.
- `cpu.seconds` = (usage_usec_n − usage_usec_0) / 1e6; `cores_avg` = seconds / duration_seconds; `cores_max` = max over consecutive sample pairs of (Δusage_usec / 1e6) / Δt; `throttled_seconds` = Δthrottled_usec / 1e6; `nr_throttled` = Δnr_throttled (all from `cpu.stat`).
- `pressure.*_stall_seconds` = (total_n − total_0) / 1e6 from the container's `memory.pressure` (some, full), `cpu.pressure` (some), `io.pressure` (some, full).
- `faults.*` = deltas of `memory.stat` `pgmajfault`, `workingset_refault_anon`, `workingset_refault_file`.
- `pids.peak` = the last read of `pids.peak`.
- `events`: `oom_kill` and `memory_high_breach` = deltas of `memory.events.local` `oom_kill` and `high`; `limit_drift` = number of sample pairs where `memory.max` or `memory.high` changed.
- `host.start` / `host.end` = `/proc/pressure/memory` and `/proc/pressure/cpu` avg10/avg60 (some and full) and loadavg[0] at s_0 and s_n; `host.memory_*_stall_seconds` = Δ of `/proc/pressure/memory` totals / 1e6; `host.slice.memory_full_stall_seconds` = Δ of the slice's `memory.pressure` full total / 1e6; `host.slice.memory_peak_bytes` = max sampled slice `memory.current`.
- `damon.*_bytes` `{peak, p90, median}`: over the DAMON aggregation samples of the classified bytes per class (hot/warm/cold/idle), nearest-rank as above; `damon.samples` counts those aggregation samples.
- Floats are `round(x, 3)`; bytes are never rounded. A field whose inputs were unreadable at either end of a delta is `null` (never a delta against nothing).
- Basic-path (run-gate, `method: "basic"`) uses exactly these rules over its own 5-second samples; only the key set differs as §4.3 says.

### Scope `container`: absolute counters (RW-21)

In scope `container` the cgroup was created for the lane itself, so its
cumulative counters already measure the lane alone from its first
instruction; reading them as deltas from `s_0` drops everything the lane did
between cgroup creation and the first sample (measured: a 3.3× CPU
understatement on a real probe — a 120 MiB lane's own `cpu.stat usage_usec`
lifetime total was 310371 usec, but the delta-from-`s_0` rule reported only
93000 usec of it). Therefore, for scope `container` **only**, the following
fields are the **last successful read**, not a delta. RW-7 ("last read" =
the last read that **succeeded**, skipping trailing nulls) applies to every
item below:

- `cpu.seconds` = `usage_usec` at the last successful read / 1e6 (not a
  delta). `throttled_seconds` (still `/1e6`) and `nr_throttled` likewise:
  the last successful read's `cpu.stat` values, not a delta.
- `pressure.*_stall_seconds` (memory some/full, cpu some, io some/full) =
  the last successful read's totals / 1e6 (not a delta from `s_0`).
- `faults.*` (`pgmajfault`, `workingset_refault_anon`,
  `workingset_refault_file`) = the last successful read's raw values (not a
  delta).
- `events.oom_kill` and `events.memory_high_breach` = the last successful
  read of `memory.events.local`'s `oom_kill` / `high` fields (not a delta).
- `limit_drift` is **unaffected**: it stays a count of sample-pairs where
  `memory.max` or `memory.high` changed — it was never itself a cumulative
  counter read as a delta, so RW-21 does not touch it.
- `cores_avg` = `cpu.seconds / duration_seconds`, using the absolute
  `cpu.seconds` above. `cores_max` is **unaffected** (a per-interval rate,
  already not a delta-from-`s_0` quantity).
- `memory.peak_bytes` is **unaffected** — already the last read of
  `memory.peak` under the original rule, not a delta.
- `baseline_bytes` remains `memory.current` at `s_0`, now **informational
  only**: it is not subtracted from anything in this scope.
- `peak_over_baseline_bytes` is **always `null`** in scope `container` (see
  §3's nullability note) — not meaningful when the profiled cgroup IS the
  lane. The footprint manifest and history tolerate this null (rendered as
  `-`, excluded from medians rather than counted as 0).

Scope `container-shared` is **unchanged**: every field above keeps the
original delta-from-`s_0` (or sampled-max / nearest-rank percentile) rule.
Host fields (`host.*`) keep the delta rule in **both** scopes — host
counters measure the whole host or slice, never the lane, so there is no
"cgroup created for the lane" argument for them.
