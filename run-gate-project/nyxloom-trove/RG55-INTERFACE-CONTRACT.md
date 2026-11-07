# RG-55 interface contract — cgprofile daemon ⇄ run-gate (contract 1)

**Status: FROZEN 2026-09-12** by the vbpub controller (plan of record:
`WAVE-PLAN-2026-09-12-rg55-profiling.md` §4). Both packages code against
this file. A change after dispatch is a numbered controller ruling (RW-n in
`reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`) delivered to BOTH
implementers; nobody edits this file unilaterally. A verbatim copy lives at
`cgprofile/docs/RG55-INTERFACE-CONTRACT.md`. The cgprofile
`test_docs.py` mirror test checks byte identity in its `r0-r1` lane; its
`coverage-partial` lane deselects that test. Run-Gate's `selftest` also checks
the copies byte-for-byte in the monorepo layout (a monorepo without the sibling
file fails; only a standalone Run-Gate without the monorepo skips).

**Amendments:** RW-11 (2026-09-12, §4.3 basic-path file list: +`memory.max`,
+`memory.high`, 10 → 12 files); RW-21 (2026-09-12, scope `container`:
absolute-counter rule for `cpu`/`pressure`/`faults`/`events`, `§3`
nullability note for `peak_over_baseline_bytes`, `§7` new subsection); RG-156 /
plan 2026-11 host-service-sockets: per-level socket layout, dedicated groups,
no exec carrier, no legacy path fallback.
**v1.1 (RW-34, 2026-09-12, §8 below):** additive under `contract: 1` —
the socket-only control carrier (§8.1), `watch` (§8.2), placement (§8.3),
liveness + policy (§8.4), `host.gates_slice` (§8.5), `version.transports` (§8.6), Summary
`liveness`/`placement`/`watch` (§8.7), new error codes (§8.8). Producers
implemented it under the then-planned cgprofile 1.1.0 (P6) and run-gate
23.9.0 (P5). RW-434/RW-476 later combined these already-merged capabilities
in cgprofile's first 1.0.0 release. A v1 consumer talking to a v1.1 producer,
and vice versa, keeps working for this separate daemon control protocol:
every new field is optional and every new option is opt-in.
**RW-43 (2026-09-12, §4.3a):** bare-host lanes — `method: "rusage"`,
`scope: null`, `source: "rusage-maxrss"`, manifest/`meta.expected`
`source` provenance.
**RW-484–RW-487 (2026-10-07, §9):** native-R2 resource observer on a second,
observer-only Unix socket. Its initial request contract was 1. Run-Gate opts
in per Assay lane; Assay uses it only when its local cgroup namespace
specifically hides ancestors. The control socket and its verbs are unchanged.
**RW-569 (2026-10-12, §9):** resource-observer contract 2 (`resource-limits`,
consumed by Run-Gate and Assay) is supported. The current cgprofile 2.x daemon
also serves observer contract 3 (`window-open`/`window-close`,
`docs/PROTOCOL.md` §9), which Run-Gate does not use; observer contract-1
requests receive an explicit unsupported-contract refusal. The control
protocol has its own contracts 1 and 2 (`docs/PROTOCOL.md` §10). The combined
first cgprofile release is 1.0.0.
**RW-570 (2026-10-14):** the supported Run-Gate consumer is its installed
SemVer wheel. A copied project-local `run-gate.py` script is not supported;
compatibility checks use the installed `run-gate --version`.

Parties: **producer** = cgprofile's daemon (`cgprofiled`) and its client
(`cgprofilectl`); **consumer** = the installed Run-Gate wheel (stdlib-only
runtime), whose `ProfilerClient` sends control-protocol requests directly over
`/run/cgprofile/control/cgprofile.sock` (§8.1), or the configured socket path,
from any devcontainer, worktree or instance. **RG-156 (2026-11-04):** the
socket is the only carrier Run-Gate uses; its former `docker exec
cgprofile-host-daemon cgprofile ctl <verb> [args] --json` fallback, the
`[profile] transport` and `[profile] daemon` settings and
`RUN_GATE_PROFILE_TRANSPORT` were removed. Run-Gate never sends `report` or
`gc`. The `cgprofilectl` client remains the producer's own operator interface and
reference translator of the socket protocol.

---

## 1. Transport and I/O rules

1. The control protocol is newline-delimited JSON. `cgprofilectl` translates its
   command-line requests to this protocol; Run-Gate's stdlib-only
   `ProfilerClient` builds the same wire requests directly and connects to
   `/run/cgprofile/control/cgprofile.sock` (or the configured socket path).
   Consumers mount the corresponding control-level directory at that path.
2. **`cgprofilectl` CLI output:** stdout is exactly one JSON object (UTF-8, no
   other output). stderr is free text for diagnostics.
3. **`cgprofilectl` CLI exit codes:** `0` ok; `2` bad request (the JSON still carries
   `"ok": false, "error": {"code": "<kebab-code>", "message": "..."}`);
   `3` daemon fault (serve loop unreachable, sysfs error). Any non-zero
   exit, timeout, or unparsable stdout is a failed `cgprofilectl` invocation.
   Run-Gate does not invoke `cgprofilectl`; it makes direct socket requests and
   handles unavailable profiling as specified in §8.1 (R-04, R-36h).
4. **Every response carries `"contract": 1`.** run-gate accepts contract
   major 1 only; a different value is "unavailable" (warning names both).
5. **Timeouts:** `version` and `host` 5 s; `start` 10 s; `status` 5 s;
   `stop` 30 s. Run-Gate applies these as socket timeouts; `cgprofilectl` applies
   them to its CLI requests. The daemon MUST answer `stop` within 30 s for a
   session of any length: the summary is maintained incrementally at sampling
   time, never recomputed from the series at stop.
6. **Units:** bytes as integers; seconds as floats rounded to 3 decimals;
   timestamps ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ`; PSI averages as the
   kernel prints them (percent, float, e.g. `6.83`); PSI totals converted
   from microseconds to seconds (`*_stall_seconds`).
7. **Absent is never zero.** A measurement the daemon could not take is
   `null`. Counters that were read but never incremented are `0`.
8. Names: native systemd service `cgprofiled.service`; reported `daemon.name`
   remains `cgprofile-host-daemon`; sessions root
   `/var/lib/cgprofile/sessions`; deployment authority is cgprofile's
   `docs/NATIVE-DEPLOYMENT.md`; session ids `s-<YYYYMMDDTHHMMSSZ>-<4 hex>`.

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
| `--token <str>` | no | the value run-gate exported as `RUN_GATE_PROFILE_SESSION` into the lane process (`[A-Za-z0-9._-]{8,64}`). With a token, the daemon first resolves positive PIDs directly in the target cgroup (including through host `/proc/<pid>/cgroup` when private-PID namespace translation makes `cgroup.procs` report zero), then selects exact-token owners and includes their descendants. It repeats discovery every interval. Without a token, it samples those direct target-cgroup PIDs. |
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

### 2.6 `cgprofile ctl report <session> --json` → `{"ok": true, "contract": 1, "path": "…/report.html"}` (cgprofile 1.x contract-1 behavior only; a 2.x daemon removes `report` and requires local rendering of contract-2 `export` data; Run-Gate never calls it — see `docs/PROTOCOL.md` §10).
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
`cgprofile/tests/fixtures/contract/`). The daemon's tests
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
   three parsers ported verbatim from `cgprofile/src/cgprofile/util.py`
   (`read_int`, `read_kv`, `read_pressure` — string variants; attributed in
   a comment). The basic summary uses the §3 key set with `method:
   "basic"`, `session: null`, `daemon: null`, `damon: null`,
   `host.slice: null`, `target.targets_seen: null`; `host.start/end` come
   from `/proc/pressure/memory` and `/proc/pressure/cpu` read directly
   (readable from the devcontainer). A lane whose container exits before the
   first sample records `resources: null` with `profile_error`.
3a. **Bare-host lanes (RW-27b, RW-43; run-gate ≥ 23.8.0):** a lane with
   no container is profiled through the daemon when run-gate itself runs
   inside a container (`--scope container-shared`, `--target` = run-gate's
   own container id, token exported into the child) and otherwise through
   the child's own resource usage: `method: "rusage"`, `scope: null`,
   `memory.peak_bytes = ru_maxrss × 1024` and `cpu.seconds = utime + stime`
   taken from `os.wait4(<lane pid>)` — the lane process and what it
   reaped, never run-gate's other children — with `memory.source:
   "rusage-maxrss"`; every field rusage cannot give (`pressure`, `damon`,
   `events`, `host.slice`, `target.targets_seen`) is `null`. The manifest
   entry of such a lane carries `"source": "rusage-maxrss"` next to its
   medians; `meta.expected` fed into `start` carries `"source"` (the
   manifest's) so admission (RG-56) knows the provenance. Consumers of
   the Summary MUST tolerate `scope: null`.
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
   run-gate: host memory PSI full avg10={x}% avg60={y}%[ | slice {name}: full avg10={z}%][ | profiler {socket_path} (cgprofile {ver}, damon {on|off|unavailable})]
   run-gate: profile session {id} (scope {scope}, baseline {n} MiB, damon {on|off|unavailable: reason})
   run-gate: footprint {lane}: peak {n} MiB[ (+{n} MiB over baseline)], p90 {n} MiB, {c} cores avg, {s} s stalled on memory (full)[, hot-set p90 {n} MiB]; history median peak {n} MiB ({k} runs)[ | manifest {n} MiB]
   run-gate: WARNING profiling: {reason} — {basic in-lane sampling only|no profile recorded}
   ```
7. **Config:** `[profile]` top-level table (central or project, whole-table
   shadowing per R-09): `enabled` (bool, default true), `socket_path`
   (absolute path, default `/run/cgprofile/control/cgprofile.sock`), `admission_wait`,
   `interval` (duration grammar of
   `budget`, default `"1s"`, passed as `--interval`), `damon` (bool, default
   true → `--damon on`). The former `daemon` and `transport` keys are
   refused by name (RG-156). Per lane: `profile = false` (bool) or a table
   `[lanes.<n>.profile]` with `enabled`/`damon`. `[footprint]`:
   `tolerance_pct` (int, default 25), `max_age_days` (int, default 30).
   Unknown keys refuse at load like every other table.

## 5. Daemon safety (D-15, restated for both parties)

The daemon refuses `--cap` and never instantiates `TempCaps`. Its host
mutation is limited to its own DAMON kdamonds and the opt-in placement writes
enumerated in §8.3; each placement cgroup write is checked by the D-25
whitelist and recorded. The daemon reads process metadata through the
configured `proc_root`; numeric PID signalling is never used for enforcement.

The production daemon is the native systemd service `cgprofiled.service`, not a
container. Its unit runs as `root`, sets `PrivateNetwork=yes` and
`Slice=cgprofile.slice`, and permits writes through `ReadWritePaths` including
`/sys/fs/cgroup` and `/sys/kernel/mm/damon`. The service's proc view comes
from the `proc_root` configuration, which cgprofiled exports as
`CGPROFILE_PROC_ROOT`. cgprofile's `docs/NATIVE-DEPLOYMENT.md` is the
deployment authority. Run-Gate never passes `--cgroupns=host` or `--pid=host`
to lane, gate, workload, helper, or test containers.

Systemd's `PrivateNetwork=yes` provides the service's network isolation; it
does not make the privileged daemon a sandbox against arbitrary compromise.
The systemd bus bridge exists because systemd owns delegated-scope lifecycle
and exact-unit attachment. It calls `AttachProcessesToUnit` for the verified
gates slice and `rg-<token>` subcgroup on placement, or the nearest systemd
unit and relative subgroup of the original scope on stop. Each move requires
a verified proc view, is checked against resulting proc cgroup membership,
and is recorded in `events.jsonl`. If it cannot enumerate or restore a
survivor, it retains the leaf and reports the original scope's
`cgroup.procs` path. This is not a general systemd control surface.

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

---

## 8. Amendment v1.1 (RW-34) — carriers, watch, placement, liveness

All of §8 is additive under `contract: 1`. Consumers ignore unknown keys;
producers never require a new option. Goldens in `fixtures/rg55/` are
extended by the producer package (P6) with one fixture per new shape and
verified byte-for-byte by the consumer package (P5); the existing goldens
stay byte-identical.

### 8.1 Socket-only carrier (D-30)

The `cgprofiled` serve loop speaks newline-delimited JSON, one request per
connection, on `/run/cgprofile/control/cgprofile.sock`. Its systemd socket
unit creates a `root:root` level directory with mode `0755` and a
`root:<configured dedicated control group>` socket with mode `0660`, then
passes one descriptor named `control` to `cgprofiled`. The default control group is
`cgprofile-control`; the observer has its own socket unit, dedicated group,
level directory and `observer` descriptor. These level groups are distinct,
and neither may be the Docker group or use its GID. Consumers mount only the
level directory they are entitled to. The supported host layout uses the
new per-level paths; no client falls back to the pre-cutover socket paths.

| carrier | how a request travels | who is authorised | default |
|---|---|---|---|
| `socket` | the consumer connects to `/run/cgprofile/control/cgprofile.sock` and writes ONE request line `{"verb": "<verb>", "args": {…}, "contract": 1}\n`; the daemon returns a JSON response object, then closes (streaming verbs: one object per line until end) | members of the configured dedicated control group and UID 0 can connect through the `0660` socket; when the daemon's `allow_uids` setting is non-empty, `SO_PEERCRED` also restricts peers to those UIDs plus UID 0; a refused peer gets `{"ok": false, "contract": 1, "error": {"code": "peer-refused", …}}` and the connection closes | only carrier |

Rules: (1) the control and observer levels are separate endpoints; the
control endpoint accepts daemon control verbs and the observer endpoint
accepts only read-only observer requests; (2) `args` are the long-option
names without dashes (`scope`, `token`, `damon`, `interval`, `meta` as a JSON
object not a string, `place`, `memory_high`, …); `cgprofilectl` is the reference translator;
(2a) on `start`, omitting `damon` selects the daemon's configured
`damon_default`; if the key is present its value must be `on` or `off`, and
`null` is refused with `bad-argument`;
(3) timeouts are per verb (§1.5), not per carrier; on the socket the
consumer applies the same numbers as socket timeouts; (4) each initial
request line, including `watch`'s request, is at most 1,048,576 bytes
including the newline and must arrive within an absolute 25 s deadline from
accept (trickle bytes do not extend it); an oversized line receives
`bad-argument`, an incomplete line at deadline or EOF is closed without
dispatch, and the accepted `watch` stream itself has no server-side timeout;
(5) the exec carrier is unsupported: `version.transports.exec` is `false`
under control contract 1 and omitted under contract 2. Run-Gate (RG-156) is
socket only. When a request/response operation cannot reach the socket,
Run-Gate degrades as for any unavailable profiler (basic or rusage profile,
one warning) and names the CP-31 cause tag, classified from errno and the
socket directory because nobody answers. The watch thread reports its raw
exception text:
`socket-not-mounted` (directory absent), `socket-missing` (directory present,
node gone), `daemon-refusing` (node present, nothing listening) or
`socket-permission`. `RUN_GATE_PROFILE_TRANSPORT` is refused by name.

### 8.2 `cgprofile ctl watch <session> --json` (streaming, D-27)

Streams one JSON object per line until the session ends (stop, kill, or
daemon shutdown), then exits 0. The request and stream use the control socket.
Lines:
```json
{"contract": 1, "event": "reading", "session": "s-…", "at": "…", "elapsed_seconds": 91.2,
 "live": {…as in status…}, "liveness": {…§8.4…}, "placement": {…§8.3…} | null}
{"contract": 1, "event": "verdict", "session": "s-…", "at": "…",
 "watch": {"state": "stalled"|"runaway"|"over_ceiling"|"hung"|"throttled"|"ok",
           "verdict": "killed"|"reported"|"none", "reason": "…", "readings": <int>}}
{"contract": 1, "event": "end", "session": "s-…", "at": "…", "reason": "stopped"|"killed"|"daemon-shutdown"}
```
Cadence: one `reading` every `--watch-interval` seconds (default 30,
clamped [5, 300]); `verdict` lines only on a state change; exactly one
`end`. The consumer reads the stream on the same socket connection; Run-Gate
uses an idle timeout of `3 × watch-interval` and re-attaches once on expiry.
Unknown session → exit 2 `unknown-session` as a single line.

### 8.3 Placement (D-20, D-25, D-31) — `start` options and the `placement` block

New `start` options (all optional, all ignored without `--place`):

| option | meaning |
|---|---|
| `--place` | ask systemd to create a delegated transient scope directly under the verified gates slice, then create a profiler-owned leaf `<scope>/rg-<token>/` and migrate the token's pid subtree into it as the resolver discovers pids (§4.3); requires `--token`; refused (`place-refused:no-token`) without one. A scope-name collision or failed/mismatched systemd scope is refused and never adopted. |
| `--memory-high <bytes>` | `memory.high` on the leaf (throttle point for memory charges attributed to that leaf) |
| `--memory-max <bytes>` | `memory.max` on the leaf (ceiling for charges attributed to that leaf); refused when `> gates_slice.memory_max_bytes` (`place-refused:over-slice`). It is not a total-RSS cap for pages charged before placement. |
| `--cpu-weight <1..10000>` | `cpu.weight` on the leaf |

At start, the daemon journals the initial token PID identities and their
original units, asks systemd to create the transient scope with those PIDs,
and reads the resulting scope path and delegation properties back from the
manager. Before the initial transfer it inventories the complete cgroup
subtree of each recorded origin systemd unit. If moving the selected PIDs
would leave a source unit with no live process, placement is refused before
the transfer as `place-refused:origin-scope-would-empty`; it repeats this
check immediately before each later move from an origin unit. Unknown or
inconsistent membership refuses as `place-refused:identity-unavailable`.
This is a pre-move observation, not a lease: an unrelated process can exit
between the check and systemd's transfer. If the source unit disappears in
that race, the daemon never guesses a broader restoration destination; it
retains the incomplete journal and reports the placement error. It creates
the leaf only below the verified scope, moves each validated PID into it
through systemd, and enables controllers only after the scope root is empty.
Later descendants follow the same identity-checked path.
The gates slice and scope remain systemd-owned; cgprofile creates/removes only
its exact leaf. On stop, surviving processes are restored to their recorded
origins before the leaf is removed and the empty scope is stopped. Failed or
indeterminate restoration preserves the scope, leaf, and recovery record.

`start` response gains
```json
"placement": {"requested": true, "leaf": "/dev.slice/dev-gates.slice/rg-profile-<token>.scope/rg-<token>",
              "applied": {"memory.high": 805306368, "memory.max": 1073741824, "cpu.weight": 100},
              "pids_moved": 0, "error": null}
```
`applied` holds the values READ BACK from the leaf after writing (never the
requested numbers); an ordinary refused placement is `placement.error =
"place-refused:<why>"` with `leaf: null` and the session still starts.
Explicit `--on-stall kill` is the exception: shared-scope kill requires a
token and `--place`, and a successful verified leaf; if placement is refused,
the request is rejected as `bad-policy` before a live session is created.
Container-scope kill similarly requires its exact runtime cgroup to be beneath
the verified, bounded gates slice; at enforcement, `cgroup.events` must
confirm `populated 1` (including descendants). The gates slice is discovered from the
daemon's `--gates-slice` (default `dev-gates.slice` under `dev.slice`);
when it does not exist on the host the answer is `place-refused:no-gates-slice`.
Daemon safety (D-15/D-25, amended by RW-35(a), RW-379, RW-380 and RW-386):
systemd owns the gates slice and each transient scope boundary. The daemon
uses systemd's manager API only to create the exact token-derived scope,
query and verify its properties, move validated PIDs into its exact child,
restore them to their recorded origins, and stop that exact scope after it
is empty. The cgroup write whitelist is `+memory +cpu +pids` to the delegated
scope's `cgroup.subtree_control`; `memory.high`, `memory.max`, `cpu.weight`,
and `cgroup.kill` on that session's `rg-<token>` leaf; `cgroup.kill` on the
exact container runtime cgroup only when its leaf name proves the requested
64-hex container ID and the resolved path is beneath the verified, bounded
gates slice; and each recorded origin's `cgroup.procs` solely to restore the
matching PID. Leaf creation/removal is limited to that exact child. The
daemon never writes the gates slice's `cgroup.subtree_control` or creates a
lane child directly under that slice. No sibling, ancestor, Docker
membership, or unrelated resource-limit write is authorized. Any other
cgroup write is refused before opening the path. Writes and successful
systemd-mediated moves are recorded in `events.jsonl`. Placement refusal
does not normally fail `start`; the explicit kill-policy exception above is
`bad-policy`. At `stop`, the daemon restores survivors to their per-process
recorded origins, verifies restoration and scope/leaf emptiness, removes the
leaf, then retires the exact scope. Failed verification preserves the scope,
leaf, and recovery record rather than claiming cleanup.

When placement succeeds, the leaf is the physical measurement source while
the original target cgroup remains the logical sample key. CPU, I/O, PID,
and pressure values come from the leaf. Cgroup memory counters and
`memory.high`/`memory.max` are charge-based: cgroup v2 does not transfer
pre-existing page charges when a process moves. These fields are not total
process RSS or a hard cap on all memory already resident in the lane.

### 8.4 Liveness and policy (D-17, D-22, D-27)

`status` (per session) and `watch` readings gain
```json
"liveness": {"last_activity_at": "…", "idle_for_seconds": 12.0,
             "cpu_seconds": 143.2, "cpu_seconds_recent": 0.9,
             "io_bytes": 58720256, "stream": {"path": "…", "last_event_at": "…"|null,
             "last_event": "candidate"|null, "cadence_hint_seconds": 45.0|null} | null,
             "paused_for_seconds": 0.0, "pause_reason": null|"host-psi"|"slice-psi"}
```
New `start` options (policy authored by the consumer):

| option | meaning |
|---|---|
| `--progress-stream <path>` | the lane's own progress NDJSON path AS THE LANE SEES IT; the daemon reads it through `/proc/<lane pid>/root/<path>` — no extra mount. The stream's `plan` event field `expect_next_event_within_s` (assay B091) is the cadence hint |
| `--idle-bound auto\|<seconds>` | `auto` = `max(300, 3 × cadence_hint)` (D-22); the stall clock PAUSES while gates-slice or host memory `full avg10 > 5` |
| `--ceiling auto\|<seconds>` | `auto` = `3 × meta.expected.duration_s` when known, else none; a session over its ceiling is `over_ceiling` |
| `--on-stall kill\|report` | `kill` = `cgroup.kill` on the exact, populated target container cgroup (`scope=container`), or on the verified token leaf (`scope=container-shared`, requiring token + explicit successful placement); no PID signals or fallback; a runtime refusal is `reported`; `report` = verdict `reported`, nothing killed; default `report` |

States: `ok`; `stalled` (idle bound exceeded with no stream event and no
CPU growth ≥ 1 s over the trailing 30 s); `hung` (the stream reported
completion — its last event is terminal — and the pid subtree is still
alive 30 s later); `runaway` (idle bound exceeded WITH CPU growth — the lane
is busy but silent; killed only when over ceiling); `throttled` (the leaf's
`memory.pressure full avg10 > 20` while `memory.high` is applied — never
killed, reported); `over_ceiling`.

### 8.5 `host.gates_slice`

`HostSnapshot` gains
```json
"gates_slice": {"name": "dev-gates.slice", "cgroup": "/dev.slice/dev-gates.slice",
                "present": true, "memory_max_bytes": 6442450944, "memory_high_bytes": 4294967296,
                "memory_current_bytes": …, "memory_swap_current_bytes": …,
                "pressure": {"memory": {…}}, "leaves": ["rg-<token>", …],
                "sessions_live": 1} | {"name": "dev-gates.slice", "present": false}
```
`leaves` enumerates profiler-owned leaf names found below verified
`rg-profile-*.scope` units; a direct `rg-*` directory below the slice is not
a valid placement. `sessions_live` counts those leaves. `present: true`
requires systemd to report the expected slice as loaded from
a non-transient unit, with the expected `ControlGroup` and finite positive
`memory.max` and `cpu.max`. A directory created for an unknown slice is
reported as absent and cannot be used for placement.
and the daemon reports its OWN slice as `"daemon_slice": {"cgroup": "…",
"memory_min_bytes": …, "memory_high_bytes": …}` (null leaves when the unit
is not installed — `ctl host` and run-gate `doctor` say "unbounded").

### 8.6 `version.transports`

```json
"transports": {"exec": false, "socket": {"path": "/run/cgprofile/control/cgprofile.sock", "listening": true,
               "allow_uids": [] , "peer_cred": true}}
```

The `exec` carrier is unsupported; contract 2 omits the `exec` key. The
additive `version.resource_observer` object advertises the separate
native-R2 listener: `{"contract": 2, "contracts": [2, 3],
"path": "/run/cgprofile/observer/cgprofile.sock", "listening": true}`.
Its contract numbers are independent of the control protocol's contract
number; `listening` reflects whether the observer socket was bound
successfully. Control contract 2 adds `export`, `config`, `config-set`, and
`config-reset`, removes `report`, and is specified in cgprofile's
`docs/PROTOCOL.md` section 10.

### 8.7 Summary additions (schema 1, optional keys)

`liveness` (the final §8.4 block), `placement` (the final §8.3 block),
`watch` (`{state, verdict, reason, readings, policy: {idle_bound_s,
ceiling_s, on_stall, progress_stream}}`). run-gate copies them into
`resources` verbatim; history stats gain nothing new in v1.1.

`damon_cleanup` (cgprofile 1.1.0, additive; present only when a DAMON owner
was closed for the session): `{confirmed: bool, slot: int|null, quarantined:
bool}`. `confirmed: false` means the kdamond teardown could not be verified
(it may still be active); `quarantined: true` means the daemon will not reuse
that slot. It is a separate fact from `damon.status`: an unverified teardown
and "DAMON off" must not look alike. It never changes the workload verdict
and the daemon still answers `stop` `ok: true` with the summary. A consumer
that needs a clean DAMON registry must check `confirmed`.

`stop_forced` (cgprofile 1.1.0, additive): `"sampler-still-running"` when the
daemon finalized the session while its sampling worker was still alive
(bounded escalation after repeated `stop-incomplete`, or shutdown). Resources
are released and the manifest is `aborted`. Both keys are optional Summary
keys; the contract number stays 1 and the Summary schema stays v1.1.

### 8.8 New error codes

`peer-refused` (socket carrier), `stop-incomplete` (a `stop` did not finish
finalizing; a retry is safe while the session remains open),
`place-refused:no-token`,
`place-refused:over-slice`, `place-refused:no-gates-slice`,
`place-refused:write-failed:<file>`, `place-refused:scope-unavailable`
(systemd refused or could not verify the delegated scope),
`place-refused:no-target-pids`, `place-refused:no-host-proc-view`,
`place-refused:identity-unavailable`, `place-refused:recovery-state-unavailable`,
`place-refused:origin-scope-would-empty` (the requested transfer would empty
an original systemd unit and make exact restoration unavailable),
`place-refused:parent-not-gates-slice` (scope path or ownership failed
verification), `bad-policy` (an unparsable policy
option, or a requested kill that cannot be safely enforced — exit 2, the
session is NOT started), `not-streaming` (`watch`
requested over a carrier state that cannot stream — never expected, kept
for completeness), `busy` (additive, cgprofile 1.2.0: the daemon is at a
bounded-admission limit or shutting down; retry shortly).

### 8.9 Consumer obligations added by v1.1 (run-gate, P5)

1. Socket only per §8.1 (RG-156); `doctor` probes the socket and prints the
   CP-31 cause tag when it is unreachable.
2. Every lane with a daemon session opens ONE `watch` (thread or long
   exec), consumes readings, maps the verdict: `killed` → the lane's stall
   exit path (R-40) with the daemon's reason in the record; `reported` →
   a WARNING line and no verdict change (R-36h). For `scope=container-shared`,
   request `kill` only when the lane's already-derived resource plan requests
   placement and the daemon confirms the verified leaf; otherwise request
   `report` and keep the lane-local watchdog as verdict authority. Do not add
   placement just to enable kill. For `scope=container`, kill is confined to
   the exact container cgroup under the gates slice and leaves its existing
   Docker limits unchanged. Without a daemon the in-process ProgressWatch
   (D-22) is the fallback and says so once.
3. Placement requests only for exec and bare-host lanes (D-20); ephemeral
   containers stay docker-capped; requested numbers are disclosed as
   `resources.memory`/manifest-derived (`× 1.5`, "derived").
4. History/inflight records gain `watch` and `placement` (nullable).

## 9. Native-R2 resource observer (RW-484–RW-487)

The daemon exposes a second AF_UNIX listener at
`/run/cgprofile/observer/cgprofile.sock`. It is distinct from
`/run/cgprofile/control/cgprofile.sock`; a read-only bind of the control socket would still
expose its control verbs. The observer listener belongs to the existing
cgprofile daemon and performs no placement or cgroup write.

### 9.1 Request and response

One newline-delimited JSON request per connection. Observer contract 2
(`resource-limits`, consumed by Run-Gate and Assay) returns event-scope
metadata and local event counters. cgprofile 2.x also serves observer
contract 3 (`window-open`/`window-close`, described in `docs/PROTOCOL.md`
section 9), which Run-Gate does not use. The control listener has its own
contracts 1 and 2 (see `docs/PROTOCOL.md` section 10); observer contract 1 is
not supported.

```json
{"verb":"resource-limits","args":{"thread_id":1234},"contract":2}
```

The request has exactly the three wire keys shown, exactly one argument
(`thread_id`), and a positive integer native TID. Under observer contract 2,
`resource-limits` is the supported verb. Observer contract 3 dispatches
`window-open` and `window-close` as described in `docs/PROTOCOL.md` section 9;
it does not handle `resource-limits`.
The complete request line, including its terminating newline, is bounded to
4096 bytes. A larger complete line or an unterminated request that grows past
that limit receives `bad-argument` and is never dispatched. The server
enforces one absolute five-second deadline; a request arriving at the deadline
is not dispatched. The daemon requires `SO_PEERCRED`; the existing daemon UID
allowlist, when configured, also applies to this listener.

Success has this shape (values abbreviated):

```json
{"ok":true,"contract":2,"cgroup_version":2,"thread_id":1234,
 "gates_slice":"/dev.slice/dev-gates.slice",
 "pids_events_scope":"hierarchical","memory_events_scope":"hierarchical",
 "cgroups":[
  {"path":"/dev.slice/dev-gates.slice/rg-abc","mount_id":42,
   "pids_max_present":true,"pids_max":null,
   "memory_max_present":true,"memory_max":1073741824,
   "pids_events":{"max":0},
   "memory_events":{"max":0,"oom":0,"oom_kill":0,"oom_group_kill":0},
   "pids_events_local":{"max":0},
   "memory_events_local":{"max":0,"oom":0,"oom_kill":0,"oom_group_kill":0}}
 ]}
```

`pids_events_scope` is always `hierarchical` in a successful response. The
observer refuses contract 2 when `pids_localevents` is enabled: in that mode
the pids event records a failed fork in the cgroup where the process attempted
it, including failures caused by an ancestor's limit, and cannot identify which
`pids.max` enforced the failure. `memory_events_scope` is `hierarchical` or
`local`, derived from the verified mount options. The `.local` maps are always
local, regardless of those options. When a local memory file is unavailable
but the regular memory file is local under the verified mount mode, that
regular file supplies equivalent local evidence. If `memory_localevents` is
active and the candidate has a memory event file, the observer refuses contract
2 when the candidate has child cgroups, since its local file then cannot prove
the whole candidate subtree.

`cgroups` is ordered from the peer's leaf through every ancestor to `/`.
Each entry carries the canonical hierarchy path, selected mount ID, limit
presence/value pairs, and event maps (or `null` when the controller exposes
no such event file). An absent limit is `present:false,value:null`; an
unlimited limit is `present:true,value:null`. A finite limit requires its
matching regular event file and effective local event evidence: the `.local` file or, when the
regular file is already local under the mount mode, that regular file.
The response contains no PID/TID list, process listing, caller-selected path,
or sibling cgroup data. For contract-2 requests, once a valid request
envelope is dispatched, an error object uses observer contract 2, including
when the request uses an unsupported version or is malformed, with
`bad-argument`, `peer-refused`, `observer-refused`, or `daemon-error` as
applicable. Peer-policy refusals that occur before the request is read, and
transport/framing errors before a valid envelope is available, also use
observer contract 2. Contract-3 replies and errors carry contract 3, as
specified in `docs/PROTOCOL.md` section 9. Observer contract-1 clients must
upgrade; the control protocol uses its independent contracts 1 and 2.

### 9.2 Peer binding and path derivation

The daemon takes PID and UID only from `SO_PEERCRED`. It proves the requested
TID is uniquely present in that peer's live task set, checks the host-to-
namespace `NSpid` mapping, and requires all peer threads to share one cgroup,
effective UID, filesystem credentials, and capability state. It rechecks
process start time and cgroup membership after sampling. The daemon derives
the cgroup path from procfs; requests cannot name another PID or path. It
verifies `cgroup.procs` DAC permissions for the peer at every sampled
ancestor, the hierarchy's mount identity, and the path's membership beneath
the configured gates slice before returning counters.

Checking every ancestor's `cgroup.procs` also closes thread-only migration:
Linux's [cgroup-v2 migration rules](https://docs.kernel.org/admin-guide/cgroup-v2.html#core-interface-files)
require a writer moving a TID through `cgroup.threads` to have write access to
`cgroup.procs` at the source/destination common ancestor. Since the observer
walks the peer's ancestry through `/`, a peer that passes the check cannot
move a process or thread to an unsampled sibling cgroup. The observer does not
need to enumerate or trust sibling destinations.

The daemon's cgroup namespace is private. For an outside peer, procfs can
therefore report a path with leading `..` components. The daemon accepts such
a path only when the full-root cgroup2 mount's `mountinfo` root consists of
exactly the same number of leading parent components. It strips only that
verified prefix. A `/` mount root permits no parent components. A mismatched
prefix, non-root mount, nested mount/overmount, noncanonical path, or path
outside the gates slice refuses the query.

### 9.3 Consumer rules

Run-Gate's closed `resource_observer = "cgprofile"` lane key is legal only
for an Assay lane or command lane in an ephemeral environment. The explicit lane
declaration is the opt-in; command wrappers that launch Assay R2 preserve the
inherited endpoint variable for that process. The observer remains a
read-only query bound to the command's own peer. Before launch Run-Gate
uses `lstat` on the `/run/cgprofile/observer` directory and socket. It
requires the directory to be a `root:root` directory, not writable by group
or other, and searchable by group; it does not require its GID to match the
socket GID or connect to the observer socket. The socket unit creates the
socket as `root:<configured dedicated observer group>` with mode `0660`
(default group: `cgprofile-observer`). Run-Gate checks that it is root-owned,
group-connectable, not accessible to other users, and uses a non-root GID
distinct from both the host Docker GID and control socket GID. Inside the
lane, the observer endpoint is `/run/cgprofile-observer/cgprofile.sock`.
Run-Gate mounts the directory read-only into the test container, so the socket
path resolves again after daemon restart. It grants that dedicated GID and
sets `ASSAY_RESOURCE_OBSERVER_SOCKET` to the in-container path. Observer-backed
lanes declare per-container memory and PID ceilings and refuse arbitrary extra
mounts. They do not mount the control socket, change PID/cgroup/network
namespaces, or add a Docker socket.

Before launch, Run-Gate's `resource_observer_version_preflight` requires
`resource_observer.contract == 2`, the fixed observer socket path, and
`listening is true` in the version response. For a present control socket,
Run-Gate checks before connecting that it is a
Unix socket with a non-zero GID distinct from the host Docker GID. It accepts
UID 0 without supplementary group membership because the tester runner uses
`--user 0:0`; other callers must have the control GID as their effective GID
or in their supplementary groups. A refusal names `cgprofile-control` and
says to recreate the devcontainer with its `--group-add`. Run-Gate obtains the
host Docker GID from `RUN_GATE_HOST_DOCKER_GID` first, then
`MDT_HOST_DOCKER_GID` supplied by the host launcher. A malformed selected
value fails; if neither is set, the control preflight names both variables.
Run-Gate does not infer this GID from the Docker API socket owner or its own
group database, because a proxy or relay may use a different GID on that
socket.

Assay first attempts its existing local B145 reader. It uses the configured
observer only when the local reader specifically proves that the private
cgroup namespace hides ancestors. Other local failures do not fall through.
The daemon accepts only a peer whose derived leaf is a descendant of the
configured gates slice. It returns that peer's complete canonical ancestry
through `/`, with per-path resource limits and event counters; it exposes no
descendant or sibling cgroups. Assay compares the candidate's regular event
counters and each ancestor's local counters around the candidate. A
hierarchical-only ancestor change can come from a sibling and does not block
the candidate; a positive ancestor-local change without a candidate event is
ambiguous and aborts the lane before assigning a candidate bucket. Any
endpoint, identity, path, mount, counter, or attribution uncertainty remains
`ERROR/EXEC_FAILED`, never a kill or survivor.
