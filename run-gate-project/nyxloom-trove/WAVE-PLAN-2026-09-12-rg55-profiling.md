# run-gate wave — RG-55 lane resource profiling (plan of record)

**Status: PLANNED 2026-09-12** from a four-round operator interview in the vbpub
controller session (Fable 5.1). Nothing is dispatched yet. This file is the
plan of record; the controller log for the wave is
`reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md` once dispatch begins.

Origin: `KNOWN_ISSUES_TODO_BACKLOG.md` **RG-55** (filed from dstdns's
test-improvement track, `vbpub@15322d20`) and dstdns
`docs/testing/RIGOR-COVERAGE-POLICY.md` "Resource-profiling and scheduling".

First consumer: dstdns — but **rollout to dstdns happens only after the tools
are polished here** (operator, round 1). When ready, adoption is done in a
`ciu worktree` off the then-current dstdns `main`, as its own dstdns package.
dstdns development continues in a parallel agent track meanwhile.

---

## 1. Facts the design rests on (verified 2026-09-12 in this devcontainer)

- dstdns runs all 44 lanes as `docker exec` into one long-lived ciu-managed
  `test-runner` container (`mode = "exec"`) or as `bare-host` conjunctions.
  No per-lane cgroup exists there; run-gate applies no `--memory`/`--cpus`
  on that path. The test-runner sits at 90% of its 1 GiB cap with memory PSI
  `full avg60 = 6.8%` and ~900 MB swapped — a live thrashing case.
- A lane container's own cgroup v2 files (`memory.peak`, `memory.current`,
  `memory.stat`, `cpu.stat`, `memory.pressure`, `cpu.pressure`,
  `io.pressure`, `pids.peak`) are readable with zero privilege via
  `docker exec <c> cat …` from this devcontainer (80 ms per read). Every
  lane already requires `bash` in its image, so `cat` is guaranteed. The
  cgroup vanishes the moment the container exits; `docker exec` fails after.
- Host-wide `/proc/pressure/{memory,cpu,io}` is readable from the
  devcontainer (global, not namespaced). Host slices are NOT visible
  (`cgroupns=private`, `0::/`) — which also makes run-gate's existing
  R-29 slice-memory admission read silently inert from here.
- DAMON is compiled into the host kernel (7.1.8, `/sys/kernel/mm/damon/admin`
  present) but needs root and a writable `/sys` — a privileged container,
  never the devcontainer. The kernel supports many kdamonds; the
  "one DAMON consumer at a time" limit is cgroup-profiler's current
  exclusive-take implementation (`lib/damon.py` `DamonSession`), not the
  kernel's.
- run-gate already has the "short history" RG-55 asks for:
  `.run-gate/history.json` (R-36, schema 1, keyed (lane, commit), bounded
  `keep`, median statistics, `history [LANE] [--json]`). assay has no
  history store and no resource data anywhere (zero-dependency contract
  A-005 forbids psutil etc.). The verdict file is latest-only.
- cgroup-profiler's collector tier (`lib/{util,metrics,sampler,targets,
  damon,store,events,phases,limits,caps,access}.py`) is stdlib-only by
  contract (DESIGN.md §1); the report tier needs pandas/plotly. Tests: 905
  functions, 100% line+branch, assay R2 mutation + R3 canary lanes. Not
  cmru-registered; no Dockerfile; helper mode reuses the devcontainer image.
- ciu governance injects `cgroup_parent` only when the compose author did
  not set it (`governance.py` ~1439); compose service keys such as
  `privileged`, `pid`, `cgroupns` are not validated or stripped by ciu.
  pwmcp is the template for an image+stack project under cmru
  (`build-push.py`, `docker-bake.hcl`, `bundle.toml`, `ciu.*.j2`,
  artifacts `oci-image` + `bundle`).
- dstdns installs run-gate as a pip wheel (23.6.2, `__revision__ = 40`),
  not a copy. `cmru/tools/assay/assay-6.1.1.pyz` (+ `.sha256`) is the
  vendorable judge for a new assay lane in run-gate-project.
- Uncommitted in the shared checkout: a one-line fix in
  `scripts/cgroup-profiler/cgprofile.py:685` (`cmd_targets` helper spec
  uses `DEFAULT_OUT`, not `HERE`) from another session — P1 must fold it in
  or wait for its owner to commit; do not clobber it.

## 2. Decisions (operator interview, 2026-09-12)

| # | decision | ruling |
|---|---|---|
| D-1 | **Profiler = a daemon evolved from cgroup-profiler** (`cgprofile serve`), one per host, root, `--privileged --cgroupns=host --pid=host --network none`, in `dev-interactive.slice` (never inside its own measurement), repo-built image. | chosen (recommended) |
| D-2 | **Transport = `docker exec` into the daemon** (`cgprofile ctl … --json`). No devcontainer rebuild; works from every worktree/instance; run-gate stays docker-CLI-only and stdlib-only. The daemon also listens on a Unix socket inside its container so a mounted-socket transport can be added later without daemon changes. | chosen |
| D-3 | **DAMON hot/warm/cold ON for every profiled lane by default**; daemon-level off-switch and per-lane `profile.damon = false` opt-out. One kdamond per session (multiplexed by the daemon). Overhead is MEASURED in the wave and recorded, never assumed. "Anticipating hot RAM need is key." | chosen |
| D-4 | **Lane model: support exec now, design for per-lane containers.** Exec lanes = container-scope cgroup deltas (attributable because R-41 serializes lanes per container) + per-pid DAMON attribution (per-lane even in a shared container). Ephemeral lanes = exact per-lane numbers. dstdns migrates to per-lane containers under ciu v8's `ciu gate` (S16) — this wave writes the SPEC-V8 appendix note; no `network` support is added to run-gate's ephemeral path. | chosen |
| D-5 | **Runners in scope**: exec-mode (dstdns), ephemeral container (every vbpub project — where the wave is tested). **Bare-host lanes are NOT profiled in v1** (duration + host-pressure context only); pid attribution through the env token (§4.3) is filed as a follow-up. Conjunction lanes record nothing of their own. | operator selected exec; ephemeral required to test here |
| D-6 | **Admission: measure now, registry now, decide next wave.** The daemon keeps a live session registry + host/slice PSI; run-gate prints a host-pressure line at lane start and a footprint line at lane end. Admission (wait/refuse on rising memory PSI or footprint sum) = **RG-56**, designed on the registry once real data exists. Swap usage is never the gate; PSI is. | chosen |
| D-7 | **Fallback when the daemon is absent**: run-gate samples the lane container's own cgroup via `docker exec <lane> cat …` (`method: "basic"`: peak, PSI stall, cpu.stat; no DAMON, no host view), warns once naming the daemon. Never blocks or changes a verdict. | chosen |
| D-8 | **Series live on the daemon's volume** (`sessions/<id>/`), bounded by retention (count + age); the daemon image carries the report tier so `cgprofile report <session>` renders the existing interactive HTML on demand. **The distilled numbers live with the code**: history record summary (gitignored, per worktree) AND a **committed footprint manifest** (D-9). | operator: "distilled info available with our code, independent of a running daemon; series/diagrams can be queried" |
| D-9 | **`run-gate.footprint.json`**, tracked next to `run-gate.toml`, generated by `run-gate footprint --write` from history-eligible entries; `run-gate footprint [LANE] [--json]` prints it; `doctor` warns on divergence from live history beyond a tolerance or on staleness. This is what decides "start now or queue" in a fresh worktree. | chosen |
| D-10 | **Lifecycle: ciu-managed stack now** (`scripts/cgroup-profiler/ciu.*.j2`, standalone root, host-singleton name, explicit interactive-tier `cgroup_parent` authored in the compose template — no ciu change expected; verify by rendering). **cmru registration** of cgroup-profiler in the pwmcp shape (image to ghcr, first release `1.0.0`). | operator chose ciu-managed over self-managed |
| D-11 | **Rigor: assay-judged R0–R3 on both projects.** cgroup-profiler's lanes (100% line+branch, assay R2, R3 canary) stay green including the daemon. run-gate-project gets **RG-53 fixed** (branch-aware diff judge + 0/0 refusal) and **new assay lanes** (bare-host, vendored `assay-6.1.1.pyz`, `base_source = "request"`): R1 changed-line line+branch, R2 mutation of changed lines, R3 canary. | chosen |
| D-12 | **Related work folded in**: RG-53; **RG-48** (`resources.cpus` → `--cpus` on ephemeral lanes + `doctor` warns on `-n auto` without a bound); **SPEC drift** (stall_timeout "assay only" text after RG-41; R-07 env keys; R-08 duplicated paragraph; RG-41 rule id backfill); **`doctor` names the inert R-29 slice read** and reads slice usage/PSI through the daemon when present. Required by earlier choices: interactive-tier ciu stack + singleton naming; cmru registration; SPEC-V8 appendix note. | all selected |
| D-13 | **Packaging: contract first, two parallel packages, then integration.** P0 (controller) freezes the interface contract; P1 (daemon) ∥ P2 (run-gate) against it (P2 tests use a fake `docker exec` daemon); P3 integration + live probes + releases + dstdns adoption brief. | chosen |
| D-14 | **Roles**: Sonnet implementers (one per package, serialized within a package, E-008 checkpoint clause); a FRESH adversarial reviewer per package (Opus xhigh, never a fork), 3-round cap, blind diff read first, live probes mandatory. Controller = this session. | operator: "implementation through sonnet, always run adversarial review" |
| D-15 | **Safety**: the daemon performs NO host-mutating operation — `serve` refuses `--cap`/`TempCaps`; host mounts read-only except its own sessions volume; no docker socket inside the daemon (run-gate resolves the 64-hex container id and passes `containerid:` — the no-daemon target variant). Rationale: the 2026-09-09 host-reboot incident (`cgroupns-host-loop-device-host-hang-incident`). | controller ruling |
| D-16 | **Versions**: run-gate → 23.7.0, `__revision__ = 41`, SPEC `R-43`+ (next free; note RG-41 has no rule id yet — backfill). cgroup-profiler → `cgprofile-v1.0.0` (first cmru release, image `ghcr.io/volkb79-2/cgprofile`). If cmru judges RG-53's semantic change breaking, 24.0.0 is acceptable. | controller ruling |

## 3. Architecture

```
devcontainer (any worktree)                          host
 run-gate lane <L>                                    cgprofile-host-daemon (privileged, pid=host, cgroupns=host)
   docker inspect -> container id                        serve loop, Unix socket (internal)
   docker run/exec … -e RUN_GATE_PROFILE_SESSION=<tok>   session registry  {s-17: lane, worktree, target, started, expected}
   docker exec daemon cgprofile ctl start --json  ----->  cgroup sampler @1s (container cgroup, baseline-subtracted for exec)
   … lane runs; every 30 s: ctl status (registry, PSI)   pid-subtree resolver (token in /proc/<pid>/environ, followed)
   docker exec daemon cgprofile ctl stop <s> --json <---  DAMON kdamond #n (vaddr targets = subtree pids) -> hot/warm/cold
   history.json <- summary; footprint line printed        host sampler (/proc/pressure/*, slices, meminfo, loadavg)
                                                          sessions/<id>/{manifest,samples.jsonl.gz,damon.jsonl,events.jsonl,summary.json}
 fallback (daemon absent): docker exec <lane> cat /sys/fs/cgroup/… @5s  -> method "basic"
```

### 3.1 Scopes and methods (disclosed on every record)

| lane runner | `scope` | cgroup numbers | DAMON | `method` |
|---|---|---|---|---|
| ephemeral container | `container` | the lane's own cgroup: exact `memory.peak`, `cpu.stat`, PSI totals | all pids in the cgroup | `daemon` or `basic` |
| exec into shared container | `container-shared` | the container's cgroup, **baseline at lane start subtracted**, max/p90 of samples; attribution relies on R-41 (one lane per container at a time) | the lane's pid subtree found by env token | `daemon` or `basic` |
| bare-host | `none` (v1) | — | — | host PSI context only; follow-up backlog item |
| conjunction | — | members record their own | — | — |

### 3.2 What a record carries (summary schema, byte and second units)

`memory` (peak, baseline, peak-over-baseline, p90, swap peak, `source: memory.peak | sampled-max`),
`cpu` (seconds, cores_avg, cores_max, throttled_seconds), `pressure` (container memory full/some
stall seconds, cpu some, io full — deltas over the lane), `faults` (pgmajfault, workingset refault
anon/file deltas), `damon` (`status on|off|unavailable` + reason, targets_seen, hot/warm/cold/idle
bytes each `{peak, p90, median}`) or null, `host` (memory PSI some/full avg10+avg60 and loadavg at
start and end, host memory full-stall seconds over the lane), `events` (oom_kill, limit_drift
counts), plus `scope`, `method`, `interval_seconds`, `samples`, `session`, `daemon {name, version}`.

### 3.3 Where it lands

- `.run-gate/history.json` **schema 2**: every `latest`/history record gains
  `resources` (summary | null), `profile_error` (string | null),
  `profile_ref` ({daemon, session} | null). `stats.passes` / `stats.completed`
  gain `memory_peak_bytes`, `hot_set_p90_bytes`, `cpu_cores_avg`,
  `memory_full_stall_seconds`, each `{count, min, median, max}` (median, never
  mean — R-36d). Schema-1 stores are read as-is with `resources: null`; the
  file is rewritten as schema 2 on the next write. Human table gains
  PEAK / HOT / CORES / STALL columns.
- `run-gate.footprint.json` (tracked): per lane `runs`, `duration_s
  {median,max}`, `memory_peak {median,max}`, `hot_set {p90}`, `cpu_cores
  {avg,max}`, `memory_full_stall_s {median}`, `scope`, plus `distilled_at`,
  `from_commit`, `revision`. Stable key order. Derived from `passes` only;
  `completed` count reported.
- Daemon volume: `sessions/<id>/` as in §3, retention defaults
  `--keep-sessions 200 --keep-days 14`, `gc` on every stop, an in-flight
  session is never pruned.

### 3.4 Disclosure lines (stdout, R-36h discipline: never a verdict change)

```
run-gate: host memory PSI full avg10=0.5% avg60=0.9% | slice dev-background: full avg10=6.8% | profiler cgprofile-host-daemon (damon on)
run-gate: profile session s-17 (scope container-shared, baseline 512 MiB)
run-gate: footprint assay-dlq: peak 712 MiB (+200 MiB over baseline), p90 690 MiB, 1.3 cores avg,
          4.8 s stalled on memory (full), hot-set p90 190 MiB; history median peak 690 MiB (8 runs)
run-gate: WARNING profiler daemon not running — basic in-lane sampling only (start it: cd scripts/cgroup-profiler && ciu up)
```

## 4. The interface contract (P0 — frozen by the controller before dispatch)

Written to `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` (and
mirrored verbatim into `scripts/cgroup-profiler/docs/`), then FROZEN: a
change after dispatch is a controller ruling logged with a number, sent to
both implementers.

### 4.1 `cgprofile ctl` (all output ONE JSON document on stdout; exit 0 ok, 2 bad request, 3 daemon fault)

- `start --target containerid:<64hex> [--token <string>] [--damon on|off]
  [--interval <s>] --meta '<json>'` → `{ok, session, started_at, damon:
  "on"|"off"|"unavailable:<reason>", target: {...}}`. `--meta` carries
  lane, project, worktree, commit, run-gate revision, expected footprint (if
  a manifest exists) — stored in the registry and the session manifest.
- `stop <session>` → `{ok, summary: {...§3.2...}, session_dir, series: {...}}`.
  Idempotent; a second stop returns the stored summary.
- `status [<session>]` → registry (live sessions with meta + expected
  footprint) and host snapshot (PSI memory/cpu/io some+full avg10/60/300,
  loadavg, meminfo essentials, per-slice memory.current/max/high and
  memory.pressure for every slice under `dev.slice` and the game tier).
- `host` → the host snapshot alone (run-gate's start-of-lane line; `doctor`).
- `report <session>` → renders `report.html` (report tier inside the image).
- `gc` → retention. `version` → `{cgprofile, contract}`.
- Contract version field on every response (`contract: 1`). run-gate refuses
  a daemon whose contract major differs (one warning, then fallback).

### 4.2 run-gate obligations

- Passes `-e RUN_GATE_PROFILE_SESSION=<token>` on `docker run` and `docker
  exec` (same pattern as `forward_env`); resolves the container's 64-hex id
  via `docker inspect` before `start`; calls `stop` in the same `finally`
  that removes/cleans the container, BEFORE `docker rm -f`; on the stall path
  (R-40) and on Ctrl-C the stop still runs (bounded, best-effort).
- Re-attach (R-39): a follower reads the inflight record's session id and
  re-adopts it; a collected (already exited) container has no session data
  → `resources: null`, `profile_error: "collected after exit"`.
- Every daemon/basic failure degrades to ONE stderr warning (R-04/R-36h).
- Sampling in the basic path: `PROFILE_SAMPLE_SECONDS = 5` (module
  constant with a reason), independent of `PROGRESS_POLL_SECONDS = 30`.

### 4.3 Target resolution (daemon side)

`containerid:` → cgroup path (`targets.find_container_cgroup`, both docker
cgroup drivers). Scope `container` = all pids in that cgroup. Scope
`container-shared` = the pid subtree whose environment carries the token
(scan `cgroup.procs` pids' `/proc/<pid>/environ`, then follow descendants at
the discovery interval). DAMON vaddr targets = the subtree's pids, updated on
topology change. Bare-host attribution later reuses the same token against
the devcontainer's cgroup (follow-up item).

### 4.4 DAMON multiplexing

The daemon owns `nr_kdamonds`: one kdamond per session, an index pool that
never shrinks below the highest live index (shrinking tears down
higher-indexed kdamonds — `lib/damon.py` teardown note), released indices
reused. DAMON unavailable (module absent, sysfs ro) → `damon.status =
"unavailable"` with reason, session continues. Measured per-kdamond overhead
(daemon CPU, host) is recorded in P1's REPORT and in the daemon's README.

## 5. Packages

### P0 — controller (this session, before any dispatch)
1. Write and freeze the interface contract (§4).
2. File **RG-56** (admission on the registry; next wave) and **RG-57**
   (bare-host lane attribution via the env token) in run-gate's backlog;
   create `scripts/cgroup-profiler/nyxloom-trove/backlog/` via the `backlog`
   skill for the daemon's own items (retention policy tuning, socket
   transport, DAMOS-based paddr mode).
3. Write the SPEC-V8 appendix note (D.6: per-lane containers as the
   profiling-grade lane model; the daemon registry as the S16.6 admission
   input; `ciu gate` passing the profile token; LaneResult carrying
   `resources_measured`).
4. Create worktrees: `.worktrees/rg55-profiler-daemon` (branch
   `rg55-profiler-daemon`) and `.worktrees/rg55-run-gate-client` (branch
   `rg55-run-gate-client`), both from `main`. Check the uncommitted
   `cgprofile.py:685` fix first (§1).
5. Dispatch P1 and P2 in parallel with the `dispatch` skill's implementer
   template, the HOST LOAD block (§7), the checkpoint clause, and the
   records paths (§8).

### P1 — cgroup-profiler daemon (Sonnet; reviewer fresh Opus xhigh)
Scope:
- `cgprofile serve` (long-lived session server on the collector tier;
  sessions, registry, host sampler, DAMON multiplexing, retention, summary
  computation incl. p90/median over samples, `summary.json`), `cgprofile
  ctl` (thin client over the internal Unix socket), `report`/`gc`/`version`.
- `serve` refuses `--cap`; `access.py` helper mode untouched.
- `Dockerfile` (python 3.14 slim base matching DESIGN.md §6; venv with the
  report tier; collector on system python), `build-push.py` +
  `docker-bake.hcl` in the pwmcp shape, `cmru.toml` (prefix `cgprofile-v`,
  artifacts `oci-image` [+ wheel if cheap]), root `cmru.orchestration.toml`
  registration, first release `--set-version 1.0.0` (controller runs it).
- ciu stack: `ciu.global.defaults.toml.j2`/`ciu.defaults.toml.j2`/
  `ciu.compose.yml.j2` (standalone root; `project_name = "cgprofile"`,
  fixed `environment_tag = "host"` → container `cgprofile-host-daemon`;
  service authored with `privileged: true`, `pid: host`, `cgroupns: host`,
  `network_mode: none`, `cgroup_parent: <interactive slice from env>`,
  `restart: unless-stopped`, named volume `cgprofile-sessions`); README
  section "Running the daemon" + ATTACH-GUIDE section "Profiling a run-gate
  lane by container id / session".
- Fold in the uncommitted `cmd_targets` fix (§1) if its owner has not
  committed it by branch time (verify, do not clobber).
- Tests: 100% line+branch on every new module; the existing r0-r1/r2/r3
  lanes green; a fake-sysfs DAMON multiplex test (two sessions, index pool,
  no shrink below live); a fake-cgroupfs session test producing the exact
  §3.2 summary; contract-schema tests (every `ctl` response validated
  against the contract's JSON shapes).
- Live acceptance (P1's own, under the host rule): `ciu up` the daemon for
  real; `ctl start` against a throwaway `docker run` container with a known
  allocation pattern (e.g. 100 MiB held 6 s) → summary within tolerance;
  DAMON on → hot bytes reported; measured daemon overhead recorded.
Forbid: any host-mutating write outside the sessions volume; touching
run-gate; touching ciu source (if the stack needs a ciu change, STOP and
report — CIU-107 is the controller's to file).

### P2 — run-gate client + history 2 + footprint + related items (Sonnet; reviewer fresh Opus xhigh)
Scope (one package, ordered commits):
1. **RG-53**: `tools/coverage_gate.py` reads `missing_branches`; refuses
   `total_changed_exec == 0` (exit 2 naming the base and the three known
   routes to 0/0) unless `--allow-empty-diff`; CHANGES breaking note.
2. **Assay lanes for run-gate-project** (D-11): vendor
   `tools/assay/assay-6.1.1.pyz` + sha256 from cmru; `assay.toml` with an
   R0+R1 lane (`pytest tests -q --cov=. --cov-branch --cov-report=json`,
   line AND branch) and an R0+R2 lane (`base_source = "request"`, judge
   python, source roots = the script, tests/tools excluded, targeted
   suite subset acceptable, budget 4h) — both `environment = "bare-host"`
   (the suite's mountinfo self-reference); an R3 canary lane in the
   cgroup-profiler/cmru `canary-run`/`coverage_canary` shape. `selftest`
   stays the cmru release gate; the assay lanes are run once before every
   merge of this wave and recorded.
3. **Profiling client** (R-43): session start/stop around the three arrival
   paths of `await_container` and around `run_exec_lane` (rewritten from
   blocking `subprocess.run` to `Popen` + wait loop, so the basic sampler
   and the `status` poll have a tick); env token; `docker inspect` id;
   basic fallback sampler with the three ported parsers (attributed to
   `lib/util.py`); `[profile]` config table (`enabled`, `interval`,
   `damon`, `daemon` container name — default `cgprofile-host-daemon`) and
   per-lane `profile = false` / `profile.damon = false`; disclosure lines.
4. **History schema 2** (R-36 amendments) + stats + human table.
5. **`footprint` verb** (R-44) + `run-gate.footprint.json` + `doctor`
   divergence/staleness warnings (`[footprint] tolerance_pct = 25`,
   `max_age_days = 30`).
6. **RG-48**: `resources.cpus` (lane or environment) → `--cpus` on
   ephemeral lanes; `doctor` warns on `-n auto` with no cpu bound.
7. **`doctor`**: profiler daemon presence/version/contract/DAMON state;
   names the inert R-29 slice read and, with the daemon up, reads slice
   usage + PSI through `ctl host`.
8. SPEC: `R-43` profiling, `R-44` footprint manifest, RG-48 under R-29,
   RG-41 rule id backfill, drift fixes (stall_timeout text, R-07 keys,
   R-08 duplicate). README/CONSUMERS/LANE-AUTHORING sections. CHANGES
   `[Unreleased]`. Backlog rows → FIXED with measured evidence.
   `__revision__ = 41`.
Tests: `tests/test_run_gate.py` for every behaviour, red-first where a
pre-fix implementation is expressible (the current `run_exec_lane` IS the
controlled wrong implementation for "no tick"); a fake `docker exec` daemon
shim honoring the contract (extend `fake_docker`); fake cgroupfs for the
basic path (extend `_fake_cgroupfs`); schema-1 → 2 read migration; the RG-27
outlier/dirty-run traps re-proven for the new stats; diff coverage 100%
line+branch (now real); assay R1/R2/R3 lanes green.
Live acceptance (P2's own, fake daemon only): one real ephemeral
`tester-unified:local` lane profiled through the basic path; one real
exec-mode probe against a throwaway `sleep infinity` container declared as
an exec environment. Real-daemon probes are P3's.
Forbid: touching cgroup-profiler; deciding admission policy; any daemon
call that can block a gate beyond the bounded timeout.

### P3 — integration, live probes, releases, adoption brief (controller + Sonnet)
1. Merge P1 (after its ACCEPT) `--no-ff`; `cmru release --project
   cgroup-profiler --set-version 1.0.0`; `ciu up` the daemon from main.
2. Merge P2 `--no-ff`; run its assay R1/R2/R3 lanes once on the merge tip;
   `cmru release --project run-gate-project --set-version 23.7.0`; clear
   `[Unreleased]` by hand; `pip install --upgrade` the wheel into
   `/home/vscode/.venv`; verify `run-gate --help` prints rev 41.
3. Live probes with the REAL daemon (recorded in the wave REPORT): cmru's
   `assay` lane (ephemeral, DAMON on) end to end; an exec-mode lane against
   a throwaway shared container with two sequential lanes (baseline
   subtraction proven); re-attach path; stall path; daemon down → basic
   fallback; `footprint --write` on run-gate-project's own store after ≥3
   runs; `doctor` output.
4. Measure and record DAMON per-session overhead and the daemon's own
   footprint; if overhead exceeds 5% of a lane's CPU or the host shows a
   PSI rise attributable to it, flip D-3's default to opt-in and say so.
5. dstdns adoption brief → `run-gate-project/nyxloom-trove/reports/
   run-gate-WAVE-RG55-DSTDNS-ADOPTION-BRIEF.md` + a `.assay-inbox`-style
   notify: pip upgrade; the daemon must be up on the host (one per host,
   started from the vbpub checkout); lanes profile automatically; after a
   few runs `run-gate footprint --write` and commit; policy doc section
   update; the scheduler consumer reads `run-gate footprint --json` and
   `cgprofile ctl status` until RG-56; `sql-mutation` / `cw2b_schema` as
   the first proof case (its `jobs` 2→1 was already a cgprofile finding).
   NOT dispatched here — dstdns adopts in its own `ciu worktree` later.
6. Memory + MEMORY.md update; worktree teardown; backlog housekeeping.

## 6. Review protocol (every package, no size exception)

Fresh reviewer session (Opus xhigh), never `subagent_type: "fork"`; blind
diff read before the LOG/REPORT; adversarial: hunt the controlled-wrong
implementations named in each package; run the package's gate itself and
read the verdict in a separate step (LESSONS L4); one live probe per new
docker argv shape; 3-round cap, then controller ruling. Round records:
`…-REVIEW-round<n>.md` in the package's reports dir.

## 7. HOST LOAD (binding for every agent prompt)

8 cores shared with a production game server; PSI is the signal, never
`free`/`uptime` alone. pytest SERIAL only, always `nice -n 19 ionice -c 3`;
targeted files while iterating; the whole suite at most once per checkpoint
and once before the return. Gate containers: at most 2 across the estate
(dstdns's live allowance; back off to 1 if `/proc/pressure/memory` full
avg10 rises), `docker update --cpus=3 <id>` right after launch, remove in a
`finally`. No build/pip/wheel/image step concurrent with a suite run. The
privileged daemon container is exempt from the count but must be idle
(`ctl status` shows no sessions) before any measurement probe. Never pass
`--cgroupns=host`/`--pid=host` to anything but the daemon.

## 8. Records

- Controller: `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
  (rulings RW-n numbered from RW-1 for this wave).
- P1: `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-{LOG,REPORT,BRIEF-n,REVIEW-round<n>}.md`.
- P2: `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-{LOG,REPORT,BRIEF-n,REVIEW-round<n>}.md`.
- P3: `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-REPORT.md` (+ the adoption brief).
- Contract: `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` (frozen).

## 9. Rules for implementers

File edits through the Edit tool, never sed/python rewrite scripts. Commit
with `git -C <worktree> commit -F <msgfile> --only -- <paths>` (new files
`git add`ed first); never `cd /workspaces/vbpub` before a git command; never
a bare `git stash`; trailers `Co-Authored-By: Claude Fable 5.1
<noreply@anthropic.com>` and the session's `Claude-Session:` line. Check
what is MISSING from a commit (`git ls-files <dir> | wc -l` after adding a
package). No push, no merge, no release — the controller does those.
Decision asks the contract and rulings do not settle go in the REPORT and
the return message; never decide product questions on silence. Checkpoint
(E-008): ARM at ~120k context or ~60 tool calls, CUT at the next coherent
boundary, write `-BRIEF-n.md`, commit, return; the controller dispatches a
fresh successor.

## 10. Risks and open points (tracked, not blocking)

- DAMON per-kdamond overhead unknown on this host → measured in P1/P3; D-3
  default may flip (P3 step 4).
- Exec-lane attribution depends on R-41 (one lane per container) and on the
  env-token subtree scan; a lane that spawns detached daemons outside the
  subtree is under-attributed — disclosed via `targets_seen`.
- RG-53 changes pass/fail semantics for every consumer of the vendored diff
  judge (topos pattern) — breaking note; consumers re-copy on their own.
- The privileged daemon is a new root-equivalent service on a shared host:
  D-15 (no mutation, no socket, network none, read-only mounts) is the
  boundary; the reviewer must attack it.
- ciu stack: fixed `environment_tag = "host"` is a deliberate singleton
  (CIU-104 reasoning applies in reverse); if ciu refuses privileged/pid keys
  at render, file CIU-107 and fall back to a documented `docker run` until
  it lands.
- run-gate-project's new assay R2 lane is expensive (each candidate reruns a
  ~100–160 s suite); it is a pre-merge lane, not the cmru release gate.
- Footprint manifest freshness is a human loop (`--write` + commit);
  `doctor` only warns.

## 11. Goal prompt (verbatim, for the controller session)

See the final message of the planning session; a copy is kept at the end of
this file for durability.

---

### Goal prompt copy

```
GOAL: realize RG-55 (lane resource profiling) per the plan of record
/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md
so that dstdns can adopt it as first consumer LATER (do not touch /workspaces/dstdns).

Read the plan first, then execute P0 -> P1 || P2 -> P3 exactly as written; decisions D-1..D-16
are settled — do not re-open them. Where the plan and a discovered fact conflict, rule in the
controller log (RW-n) and continue; ask the operator only for a product question the plan and
its rulings do not settle, and never stop on silence.

Roles: you are the controller (design, contract, rulings, merges, releases, memory). Implementers
are Sonnet, dispatched with the `dispatch` skill's templates plus the plan's HOST LOAD block and
records paths; one implementer per package, checkpoint clause on. Every package gets a FRESH
adversarial reviewer (Opus xhigh, never a fork), 3-round cap, live probes required, then merge
--no-ff and release under the standing vbpub authorization (memory: vbpub-operator-standing-
authorization); "shipped" = merged + cmru-released + devcontainer-installed (run-gate) / daemon
up from main (cgprofile).

Quality bar (non-negotiable): 100% line AND branch coverage on every changed line (RG-53 fixed
first so the judge is real), assay-judged R1/R2/R3 lanes green before each merge, R-36h
best-effort discipline (profiling never blocks or changes a verdict), D-15 daemon safety.

Host rule: PSI is the signal; serial nice/ionice pytest; <= 2 gate containers estate-wide, cap 3
CPUs; one measurement probe at a time with the daemon idle.

Done when: cgprofile-v1.0.0 released with the daemon running as the ciu-managed host singleton;
run-gate 23.7.0 (rev 41) released and installed; live probes recorded in
run-gate-WAVE-RG55-REPORT.md incl. measured DAMON overhead; run-gate.footprint.json written for
run-gate-project; RG-55 marked FIXED, RG-56/RG-57 filed, SPEC-V8 D.6 note landed; the dstdns
adoption brief written (not dispatched); memory + MEMORY.md updated; worktrees torn down.
Report the versions installed and anything left out, with reasons.
```
