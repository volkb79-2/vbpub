<!-- status: design (P1 SHIPPED in run-gate rev 77); scope: run-gate + assay + fleetd/host-runner; operator decision 2026-11-25, questions answered 2026-11-25 (§8) -->
# Design: no constraining time budgets — progress and stall detection replace wall-time caps

## 0. The decision this document implements

Operator, 2026-11-25 (binding):

> NO TIME BUDGETS that constrain a run: things take as long as they take, as long
> as they make progress. Watch progress/stalls instead of capping wall time
> (`budget_per_candidate`, `LANE_TIMEOUT` and similar caps are not a reason to
> stop a progressing run). Split R2 into parts that run concurrently across the
> 3 hosts (v1001, r1002, controller gates slice): about 3x3 single-threaded runs.

Motivating case: `rgw-ciu-20261125T202211-v1001`. `ciu/assay.toml:111` declares
`budget_per_candidate = "5m"` (300 s) while the baseline command alone took
429.4 s (progress file, `command_finished`). Every candidate that was not
immediately killed hit the 300 s cap: the three `candidate_retry` events land
at `elapsed_s` 1711.3 / 2013.0 / 2314.7 — a 301 s cadence, i.e. one full cap
per attempt — each with `first_bucket = final_bucket = budget_exceeded`.
The run's own summary (`.assay/progress-ciu.jsonl`, 6 761 lines: 6 723 `test`,
19 `candidate`, 3 `candidate_retry`; `end` event:
`buckets {budget_exceeded: 3, crashed: 0, equivalent: 0, hung: 0, killed: 16, survived: 0}`)
and `result.json`
(`{"verdict": "BUDGET_EXCEEDED", "exit_code": 4, "reason": "LANE_TIMEOUT | ksm: ..."}`)
say the same thing: no surviving mutant could ever be judged, and the judge
was not hung (0 `hung`). Every cap in that stack was doing exactly what it was
declared to do — the cap was the wrong instrument.

The failure the replacement must still catch is assay B090: an unbounded
candidate hung 37 minutes with nobody watching (`assay/src/assay/config.py:408`).

Two kinds of limit are in scope and must not be conflated (rule of this design):

- **A cap on WORK** — total wall time of a lane, a candidate, a probe, a shard.
  These are what the operator removes.
- **A timeout on ONE operation** — an ssh round trip, a `docker exec` probe,
  a log or seed upload, a socket read. These are not caps on work (they bound a
  single syscall-shaped wait, and a healthy long run consists of many of them),
  they stay.

Grammar note for every table below: `ok` = does not stop a run, `stop` =
ends a run with a verdict.

## 1. Inventory — every wall-time cap that can stop a gate run today

### 1.1 run-gate (vbpub-core; repo root is `run-gate-project/`)

| # | name | file:line | default | enforced by | verdict on expiry |
|---|---|---|---|---|---|
| RG-1 | lane `budget` (container lane) | `run-gate-project/run_gate.py:17171-17181` (deadline build), `:17232-17239`, `:17446-17448` | per lane, `run-gate.toml` | `await_container`: `docker kill` at the deadline | `GateBudgetExceeded` (`:830-836`) → `BUDGET_EXCEEDED`, exit 4 (`:186-192`, `:21942-21946`) |
| RG-2 | lane `budget` (exec lane) | `:18612-18618`, `:18894-18919`, `:19058-19060` | per lane | in-runner GNU `timeout --preserve-status --kill-after=5s <budget>s`, then client grace | same, `BUDGET_EXCEEDED` |
| RG-3 | lane `budget` (bare-host lane) | `:19393-19404`, `:19514-19519` | per lane | `proc.wait(timeout=remaining)`, then `terminate_process_group` | same |
| RG-4 | lane `budget` re-attach continuation | `:17171-17181` | inherits the original admission clock | `await_container` | same (may fire immediately on a re-attach of an over-budget container) |
| RG-5 | `stall_timeout` (assay lane) | `:1741-1769` (legal on both kinds), `ProgressWatch._idle_bound :16961-16979` | absent = auto bound | `ProgressWatch` from the progress file | `fail_infra` → `ERROR`, exit 2 (`:18207`, `:19875`, `:20359`) |
| RG-6 | `stall_timeout` (command container lane) | `:1741-1769` (legal for both kinds), `LogStreamWatch :17212-17401` | absent = 300 s auto bound (P1) | log-line arrival age | `ERROR` exit 2, "STALLED" (`:18207`) |
| RG-7 | auto idle bound (assay lane, no declared key) | `:743` (600 s post-command), `:16979` (`max(300, 3*cadence)`), `:752` (the 300 s floor) | 300 s floor | `ProgressWatch(auto_bound=True)` armed on container/exec/bare-host lanes (`:17962`, `:18044`, `:19628`, `:20125`) | `ERROR` exit 2 |
| RG-8 | host-mode `stall_timeout` | was `:1443-1453`, `:2102-2105` — **CLOSED by P1 (run-gate rev 77)** | — | `StreamWatch` over the runner's own output stream; the RG-58 WARN and `doctor` 2d are retired with the fact they asserted | none left: the key is no longer inert anywhere |
| RG-9 | `lanes --check` outer/inner margin | `:217` (300 s), `:20133-20149` | 5 m | the check verb only | check finding (no run stops); `--strict` fails the estate-lint lane |
| RG-10 | `lanes --check` canary attempt ≤ lane budget | `:20152-20168` | — | check verb only | finding |
| RG-11 | admission wait | `:216`, `run_gate_admission.py` ticket | `10m` | `_admit_lane` | **not a stop**: WARN then proceeds (`:6766-6772`) |
| RG-12 | lock wait | `:215` | `10m` | `acquire_shared_locks` | wait then proceeds/discloses (not a stop) |
| RG-13 | sequence total budget | `:20696-20705` with `run_gate_admission.py:26` (`24*3600`) | sum of member budgets, or 86400 s for a member without one | admission ticket `run_budget_seconds` | reservation deadline; overrunning leaves the ticket stale |
| RG-14 | closeout analysis cap | `:594` (900 s), `:15148-15156` | 900 s, budget-derived | `timeout` around the analysis | closeout skipped, lane verdict unchanged |
| RG-15 | scratch prune age | `:11207-11211` | longest declared lane budget, else 86400 | `scratch prune` | deletes run dirs (not a lane stop) |
| RG-16 | cgprofile client timeouts | `:746`, `:774` | 5–30 s | daemon client | profile absent/of `basic`; lane unaffected |
| RG-17 | log capture on failure | `:223` | 60 s | `docker logs` capture | best-effort only |
| RG-18 | exec kill grace | `:219` | 5 s | stall/budget teardown | — |

There is **no `LANE_TIMEOUT` reason code in run-gate**: that name lives in
assay (`assay/src/assay/errors.py:174`) and appears in run-gate only inside
disclosure text that quotes the judge (`run_gate.py:16791`, `:16813`). Remote
material reports it because the *judge's* reason string is copied into
`result.json`.

### 1.2 assay (owner: vbpub-assay)

| # | name | file:line | default | enforced by | verdict |
|---|---|---|---|---|---|
| AS-1 | lane `budget` | `runner.py:307-346` (`LaneDeadline`), CLI `cli.py:3458` | per lane | `LaneDeadline.remaining()` sampled by every inner step | `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, exit 4 |
| AS-2 | `budget = "unbounded"` | `config.py:401`, `_refuse_unbounded_without_unit_bounds :2621-2690` | — | allowed only when every unit carries its own bound | no raise (`runner.py:322-326`) |
| AS-3 | `judge.mutation.budget_per_candidate` | `config.py:403-413`, `:606`, resolved `runner.py:6002-6042`, applied `mutation.py:1347-1400` | omitted = `auto` = `max(3×baseline, baseline+60 s)`; `"none"` = unbounded | `LivenessRunner._poll` timeout branch (`liveness.py:2097-2105`) | `budget_exceeded` bucket, `BUDGET_EXCEEDED`/`LANE_TIMEOUT` per candidate |
| AS-4 | liveness idle bound (`pre_first_event`/`expect_next_event` from the baseline's own gaps) | `liveness.py:1142-1324`, decision code `:2050-2096` | calibrated per lane, CPU window 30 s, growth floor 1.0 s (`:672-673`) | `LivenessRunner` | `CANDIDATE_HUNG` (`errors.py:199`) — the B090 catch |
| AS-5 | `judge.canary.budget_per_attempt` | `config.py:816`, `:866-878`, applied `canary.py:718-740` | absent = lane budget | `LaneDeadline.tightened` | `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, later probes `not_attempted` |
| AS-6 | R2 baseline command | `config.py:394-400` (A-457) | `timeout=None` by design | caller-watched only | none from assay |

Note the ordering visible in the motivating artifact: the baseline is already
unbounded (AS-6) and took 429 s, which is longer than the *candidate* cap
(AS-3, 300 s). A cap smaller than the baseline of a normalised run cannot be a
useful judgement of a mutant run — this is the arithmetic defect the operator
decision removes.
| AS-7 | lane retry/`max_mutants` sentinel | `errors.py:180-190` | `max_mutants` | candidate discovery | `MUTANT_LIMIT_EXCEEDED` (not a time cap; listed for completeness) |

### 1.3 runner / remote-gate (`scripts/netcup/runner/` — TODAY's location)

This inventory names where these caps live NOW. The shared host runner
(`fleet host …`, fleetd's P-fd-05, controller `CTL-51`) is where the remote
runner and its hosts/ssh configuration move to; `scripts/netcup` keeps only the
netcup API and holds no host keys (operator answer to §8 Q2).

| # | name | file:line | default | enforced by | verdict |
|---|---|---|---|---|---|
| RN-1 | `remote-gate run` ssh per-command timeout | `remote-gate.py:60` | 300 s | `Runner.run` | `rc=124`, error — a single-op timeout, stays |
| RN-2 | file/seed/bundle upload | `:71` (600 s), `:999`, `:1064` (1800 s) | 1800 s | `run_to_file` | transport error, stays |
| RN-3 | `--wait-psi` / `--wait-mem` | `:1337`, `:1340` | 900 s | job preamble | exit 75 refusal *before* the lane |
| RN-4 | poll give-up | `:1113` | 5 consecutive failed polls | `follow` | `GateError` exit 3 |
| RN-5 | `host-sat-queue` lane timeout | `host-sat-queue.sh:41` | `HOST_SAT_LANE_TIMEOUT_S=3600` | queue loop `:186-190` | `remote-gate cancel`, then `timeout-no-finish` after +600 s |
| RN-6 | job TERM trap | `remote-gate.py:634` | — | `remote-gate cancel`/queue timeout | `cancel_lane`; `rc=143`; **collect of the shard store is skipped** (see §6b) |

### 1.4 cmru

| # | name | file:line | default | enforced by | verdict |
|---|---|---|---|---|---|
| CM-1 | step no-progress watchdog | `cmru/src/cmru/runner.py:25` (`1800.0`), per-step/runner key `:223-259` | 1800 s of silence | `cli_extended.procio.run_streaming(no_progress_seconds=…, progress_paths=())` (`:487-496`) | step `watchdog` outcome, release gate fails |
| CM-2 | nested-dind readiness | `cmru/src/cmru/tester_gate.py:134`, `:638`, `:687-691` | 30 s / probe 10 s | `tester_gate` probes | probe failure (single-op, stays) |
| CM-3 | per-container memory/CPU/pids | `cmru.orchestration.toml:113-124` | not time | docker | OOM/pids, not a time cap |

CM-1 is the *only* real time cap in cmru's gate path and it is already a stall
watch, but its silence signal is output-only (`progress_paths=()`), so a
legitimately silent-but-progressing step (a long `docker build` with a buffered
writer, a quiet pytest) can be killed. That is a gap, not a policy.

### 1.5 Project declarations (today)

`grep -n "budget" --include=run-gate.toml` gives 135 command lanes and 29 assay
lanes; the largest declared wrappers are `cgprofile/run-gate.toml:160`
(`25h`), `cgprofile/run-gate.toml:112` (`1445m`), `nyxloom/run-gate.toml:48`
(`605m`), `vm-lab/run-gate.toml:87` (`280m`), `run-gate-project/run-gate.toml:136`
(`245m`), `ciu/run-gate.toml:27` (`485m`). Inner `assay.toml` lane budgets
include `cgprofile/assay.toml:28` (`24h`), `ciu/assay.toml:56` (`8h`),
`hostobs/assay.toml:285` (`4h`), `run-gate-project/assay.toml:96` (`4h`).

`budget_per_candidate` declarations today:
`ciu/assay.toml:111` (`5m`), `mdt-cockpit/assay.toml:52` (`4m`),
`nyxloom/assay.toml:219` (`120s`), `pwmcp/assay.toml:117` (`900s`),
`cgprofile/assay.toml:90` (`600s`), `truenas/tnt/assay.toml:123` (`900s`),
`run-gate-project/assay.toml:145` (`900s`), and the two `auto` lanes
(`cmru/assay.toml:305`, `vm-lab/assay.toml:55`).

`stall_timeout` today: exactly two lanes declare it
(`truenas/tnt/run-gate.toml:126`, `:148`); every other long lane relies on
RG-7's auto bound.

## 2. Progress signal and stall detection — what exists

Progress sources that exist today:

| unit | signal | source |
|---|---|---|
| assay lane (R0/R1/whole) | `--progress` phase stream, `command_running` heartbeat every 60 s (`README.md:648-660`) | `.assay/progress-<lane>.jsonl` |
| assay R2 candidate | new plugin event, stdout/stderr file growth, process-tree CPU growth over 30 s; PSI-credit-gated idle clock (`liveness.py:1974-1990` eligibility, `:2050-2096` decision) | liveness side files |
| container command lane | arrival age of `docker logs -f --timestamps` lines (`run_gate.py:17212-17401`) | log stream |
| exec command lane | since P1: arrival of the runner's own stdout chunks (`StreamWatch` over `OutputTee`) | output stream |
| bare-host lane | `ProgressWatch` (assay) — and, as a command lane, since P1 its own stdout arrivals | progress file / output stream |

Judgement quality already present:

- **Silence, never total elapsed.** `ProgressWatch._active_age` measures
  `now - last event`, the log watch measures line age, liveness measures idle inside
  *eligible* intervals only.
- **Pressure-independent.** run-gate pauses its idle clock when host PSI
  `full avg10 > 5`, crediting at most 1800 s of pause
  (`PRESSURE_PAUSE_MAX_SECONDS` in `ProgressWatch._active_age`);
  assay credits only intervals whose aggregate pressure/throttle counters did
  not move and never prorates a delta (`liveness.py:1974-1990`).
- **Disclosure.** run-gate prints which source judged the lane
  (`(source: progress file)` / `(source: log stream)` / `(source: output
  stream)`, `print_lane_bounds`) AND, since P1, whether the number is the
  operator's own (`source: declared`, never paused) or the default (`stall
  bound 300s (source: auto)`, paused under PSI), with the same facts recorded in
  `run_record["stall_bound"]`.

Gaps where nothing reports progress today:

| G | unit | gap | evidence |
|---|---|---|---|
| G1 | bare-host lane (and the `exec` command lane, same one-line runner) | **CLOSED by P1** — was: `stall_timeout` inert, no signal of any kind | shipped in run-gate rev 77: `OutputTee` chunk arrivals → `StreamWatch`; out-of-tree `host_mode_stall_timeout_inert_reason` is a labelled no-op |
| G2 | sequence lane | no own watch; only leaves' watches apply — **BY DECISION, not fixed**: a sequence composes members, and a heartbeat of "did a member finish" cannot show progress. `stall_timeout` on a sequence is REFUSED by name; the sequence validator returns before runner keys are read, so it has no runner disclosure or run-record stall bound | — |
| G3 | canary probe | liveness is native-R2 only | verdict schema `schemas/verdict.schema.json:2538` |
| G4 | cmru release/build step | watchdog sees output only | `runner.py:487-496` (`progress_paths=()`) |
| G5 | remote-gate follower | no independent stall detection | RG-46, `run_gate.py:16916-16917` |
| G6 | remote-gate shard store | no transport on a stopped run | §6b |
| G7 | shard → merge handover | nothing watches the gap between a shard finishing and its store arriving; the merge instead refuses on a missing store at start (`:14782-14799`) | `run_gate.py:14782-14799` |

## 3. Target behaviour and schema changes

### 3.1 The one rule that orders everything

> **A cap is removed ONLY where a stall watch exists.** Where none exists, the
> watch is an *earlier* package and the cap stays until that package ships.

### 3.2 Keys

| key | today | target | why |
|---|---|---|---|
| run-gate lane `budget` | hard stop | **stays**, grammar extended with `unbounded`; `unbounded` is *refused* on a lane whose kind has no watch (RG-8/G1/G2/G3), naming the kind and the missing watch | the refusal is the mechanical form of the rule above; a config file must never be able to declare away the only thing that stops a hung unit |
| run-gate lane `stall_timeout` | optional | **stays, becomes the primary knob**; auto bound (RG-7) becomes the default on every watchable kind so a lane that declares nothing is still watched | an absent key must not mean "unwatched" |
| assay lane `budget` | hard | **stays** (`unbounded` already legal, AS-2) | an R0/R1 lane is one command; its only bound is the whole command |
| `judge.mutation.budget_per_candidate` | hard per candidate | after P4: never ends a candidate while the no-progress watch (§7.1) is armed for that run (one WARN per run); stays **binding** wherever it is not armed | operator decision; today's AS-4 ends only an idle candidate (§7.1) |
| `judge.canary.budget_per_attempt` | hard | **stays** until G3 closes | no watch exists |
| `[profile] admission_wait`, `lock wait` | wait caps | stay | they do not stop a progressing run |

Rejected alternatives, with reasons:

- *Keep the caps and raise the numbers.* Rejected: the ciu case shows the cap
  and the suite's runtime race each other indefinitely; the next slow host
  re-opens the same defect, and a raised cap is indistinguishable from a
  correct one until it fires.
- *Delete `budget` outright.* Rejected: on kinds with no watch it is the only
  stop, so removal restores B090.
- *New `liveness: "auto"|"stall"|"none"` lane key.* Rejected for the first
  package: a second knob for a fact already derivable from `kind` invites
  disagreement between the knob and the watch. Reconsider only if a lane
  genuinely needs to run unwatched (then it is a refusal, stated by name).

### 3.3 Verdict and reason compatibility

`BUDGET_EXCEEDED` (exit 4) and assay's `LANE_TIMEOUT` **stay** in the closed
vocabulary — historical verdicts must keep parsing. Two additions, in this
order:

1. Stalls currently surface as `ERROR`/exit 2 via `fail_infra`
   (`:18207`, `:19875`, `:20359`): a genuine stall is *not* an infrastructure
   failure and must not be reported as one. First package: keep exit 2 but
   carry a machine-readable reason token `lane-stalled` in the run record and
   the message.
2. Later release: a closed `STALLED` verdict with its own exit code, plus
   assay mapping `CANDIDATE_HUNG` at candidate level to a lane-level
   `INCONCLUSIVE` summary instead of today's bucket-only accounting. This
   alters a closed vocabulary and needs its own release note.

### 3.4 Migration order (each step releasable alone)

1. **Watch first (run-gate).** Auto-bound arms on every watchable kind; bare-host
   and sequence get a watch; `unbounded` is *not yet* accepted. No cap removed.
2. **Watch first (assay).** The §7.1 no-progress bound and the long-trace fix;
   `progress` events carry `elapsed_s` (already `B065`); canary gets a liveness
   plugin or an honest "unwatched" disclosure. Runs without an armed watch
   (non-pytest, non-Python) keep their budgets; extending the watch to them is
   not scoped by any package here.
3. **Ignored-with-WARN (assay).** `budget_per_candidate` stops ending a candidate
   where the watch is armed for that run; the verdict records which applied.
4. **`unbounded` accepted (run-gate).** Only kinds proven in step 1; refused
   elsewhere. `lanes --check` gains a `budget-unbounded-unwatched` rule.
5. **Per-project tomls.** Remove or convert the caps of the long lanes
   (`cgprofile`, `ciu`, `nyxloom`, `run-gate-project`, `vm-lab`,
   `mdt-cockpit`), one project per change, each gated.
6. **Docs.** README/DESIGN-GUIDE/CONSUMERS for run-gate and assay in the same
   change as each behaviour (§ "user-facing docs" rule).

## 4. Sharding R2 across hosts

What exists: `--shard K/N` and `--merge-shards N` (STRICT merge, `run_gate.py:14676-14700`,
`:14756-14800`), a per-shard Assay store
(`<root>/assay-shards/<project>/<lane>/<assay_lane>/<K>of<N>`,
`:14756-14766`), `--carry-over`, `--merge-manifest` (`assay/src/assay/cli.py:531-560`),
`run-r2-campaign.sh --instances K [--shard-total T --shard-offset O]|--merge-only T`
(`scripts/netcup/runner/run-r2-campaign.sh:7-11`, `:464-530`), and
`remote-gate --collect-store` (`remote-gate.py:1337-1347`).

Missing for "one command, about 3 shards per host on 3 hosts":

- **No cross-host driver.** `run-r2-campaign.sh` is one host per invocation;
  `--shard-offset` exists precisely because a human starts the other hosts.
  `remote-gate` runs one lane per `run`, with no fleet verb. Its OWNER is not
  `scripts/netcup`: placing test-lane shards on hosts is the run-gate/fleetd
  boundary (§5 row P6, `CTL-51`), because `scripts/netcup` holds the netcup API
  and no host keys, and several ciu roots request runs from one fleetd.
- **No store transport.** Moving a shard store off a host is manual
  (`scripts/netcup/CONSUMERS.md:745-750`); a partial host ends `75 merge-pending`.
- **No per-shard stall watch across hosts.** Each host's own `run-gate` watches
  its shard; the controller only reads `job.log` and `status`, so a *quiet*
  shard looks identical to a *working* one (`remote-gate.py:1104-1130`).
- **The command-lane refusal** (§6a) blocks rollback v2's `kind = "command"`
  r2 lane from being sharded at all.

Target shape (one command, 3 hosts × 3 single-threaded shards) — a fleetd +
run-gate verb, not a `scripts/netcup` one:

```
fleet r2 <lane> --total 9 --hosts v1001,r1002,gates --jobs 1
  -> per host: the shared host runner starts `run-gate <lane> --shard K/9`
     (K = that host's offset .. +2)
  -> watch: per-shard stall (the shard's own run-gate), per-host job liveness,
     one fleet summary
  -> collect: every shard store packaged by the job itself (§6b), then one
     --merge-shards 9 run against stores that already arrived
```

The verb's name is the design's open detail; what is decided (§8 Q2) is that it
belongs to fleetd + run-gate, so the wheel that owns lane semantics and the
service that owns hosts and keys are the two halves — and run-gate still never
learns ssh.

Per-shard stall detection: the shard's `run-gate` keeps its own
`ProgressWatch` (progress file per shard, `assay_shard_state_dir`), the
controller adds a *fleet* watch that reads each shard's progress file age and
each host's `job.log` age, and pauses its clock on PSI the same way. A shard
that is silent past its bound is cancelled by its *own* client, and the fleet
reports which shard stalled and which store is missing — never a silent
`merge-pending`.

Decision: fleet stall detection is **per shard**, not per host. Alternative
rejected: one host-level "is anything moving" watch — it misses one stalled
shard of nine while its siblings keep the host log moving.

## 5. Packages, owners, sizes, risks

| # | package | owner | size | main risk |
|---|---|---|---|---|
| P1 | run-gate: auto-bound stall on every watchable kind; bare-host + sequence watches; disclosure of the source | vbpub-core | medium (~300 lines + tests) | a false STALL on a legitimately quiet phase; mitigated by PSI pause and the `max(300, 3×cadence)` floor |
| P2 | run-gate: `unbounded` accepted, refused on unwatched kinds; `lanes --check` rule | vbpub-core | small | `unbounded` on a lane whose watch is silently inert — refuse by kind, never by key presence |
| P3 | run-gate: stall reported as `lane-stalled`, then a `STALLED` verdict | vbpub-core + compatibility note | small/medium | closed verdict table change; needs its own release |
| P4 | assay: the no-progress bound of §7.1, long-trace fix, per-run arming with disclosure, rewritten `unbounded` admission rule; only then `budget_per_candidate` stops binding where the watch is armed; new evidence decision ships in the v21 cut (P-assay-17) | vbpub-assay (implements and reviews) | medium/large | a slow single unit mistaken for no progress; old judges reject the new evidence decision |
| P5 | assay: canary liveness (or an explicit unwatched disclosure) | vbpub-assay (implements and reviews) | medium | R3 units stay capped until this ships; do not remove `budget_per_attempt` first |
| P6 | runner: fleet driver, a fleetd + run-gate boundary (§8 Q2) — fleetd owns hosts/keys and serves several ciu roots, run-gate owns the shard semantics; store collection on cancel/term is vbpub-platform's own small package (§6b), which P6 depends on | fleetd + run-gate (shared host runner, `P-fd-05`; controller `CTL-51`) | large | a fleet without that collection loses a stopped shard's progress |
| P7 | (lower priority: rollback converts debian-install-v2's R2 into a native R2 assay lane, which `--shard` already serves) run-gate: `--shard` for command lanes (a command lane's shard = per-shard `--only-candidates`-equivalent or per-shard runner partitioning) | vbpub-core | medium/large | a command lane has no candidate model; naive sharding splits a suite by file and silently changes what a PASS means |
| P8 | cmru: feed the step watchdog `progress_paths` (build logs, gate progress) | cmru owner | small | watchdog stays, only its silence definition widens |
| P9 | project toml migration (6 projects) | per-project owner, one change each | small each | a converted lane losing its only bound on an old run-gate |

P6 also moves the remote-gate runner and its `hosts`/ssh configuration OUT of
`scripts/netcup` into the shared host runner; until that line lands,
`scripts/netcup` is where they are, which is a location fact, not an owner
decision (§8 Q2).

Ordering constraint: P1 before P2 (a rule that removes a cap needs the watch to
exist), P6 after platform's store-collection package and independent of P1..P5
and P7, P9 last.

## 6. The two reported gaps (verified)

**(a) `--shard` refuses `kind = "command"` lanes.**
`run-gate-project/run_gate.py:14679-14682`:

```
if lane.get("kind") != "assay":
    fail("--shard, --merge-shards, --carry-over, --merge-state,
          --only-candidates and --exclude-candidates are assay-only; ...")
```

So rollback v2's `r2` command lane cannot be split across hosts (as reported
by vbpub-rollback). The refusal is proven above; the specific lane is only
partly verified on this tree: `scripts/debian-rollback/run-gate.toml:28-37`
declares a single `offline` command lane, while the v2 branch
(`debian-rollback-zfs-poc-20261123`) carries four `kind = "command"` lanes
(`offline`, `prepare-zbm-input`, `build-zfs-iso`, `zfs-recovery-acceptance`)
and none literally named `r2` (`git show <branch>:scripts/debian-rollback/run-gate.toml`).
Every one of them hits the same refusal. Also relevant: `refuse_serialized_shard`
(`:3225-3243`) refuses an exec-mode or `resources.shared` lane, so "just sharding
the command lane" would need each shard in an ephemeral environment.
The rollback need is met without P7: debian-install-v2's R2 becomes a native R2
assay lane (main controller, 2026-11-25). P7 stays as a lower-priority package
for other command lanes; the honest scope statement is that a command lane has **no
candidate model**, so its shard must partition by a declared unit (test file,
target source, suite segment) supplied by the lane, never by a heuristic.

**(b) Cancelling a remote run does not collect its resume store.**
`remote-gate.py:634`:

```
trap 'log "terminated by cancel"; cancel_lane; rc=143; exit 143' TERM INT
```

`collect_stores` is called at `:765`, *after* the lane exits, in the job body.
The TERM path runs `cancel_lane` (`:604-607`, kills the lane pid inside the
cockpit) and exits, so the `--collect-store` archive is never written, and the
EXIT trap `finish` (`:608-633`) then removes the throwaway checkout. The
progress lives in that checkout, so a cancelled run loses it — exactly what
`--resume` needed. `host-sat-queue.sh:186-190` cancels through this same path,
so a queue timeout loses the store too.

Fix (not part of P6: vbpub-platform owns it as its own small package, and it
lands on TODAY's runner — the code that exists now, not the code the shared host
runner will move): collect before teardown on every exit path — the TERM trap must run
`collect_stores` (bounded, and never changing `rc`) *before* cancelling, or
`finish` must call it after the lane container is stopped and before
`rm -rf $CKDIR`. Same rule for `remote-gate cancel`, which currently only
signals (`:1309-1318`). Alternative rejected: rely on the operator to copy
stores by hand (`CONSUMERS.md:745-750`) — that is the current workaround and
the exact loss this gap records.

## 7. Required ordering rule — which watch covers which lane kind

State it before any cap removal. For each kind: the watch that must exist
before its cap may be dropped.

| lane kind | unit | watch | exists today? | cap may be dropped only after |
|---|---|---|---|---|
| native R2 (assay, ephemeral container) | candidate | assay `LivenessRunner` (`CANDIDATE_HUNG`, AS-4) + run-gate `ProgressWatch` on the progress file | **idle stalls only** (§7.1) | `budget_per_candidate` stops binding only after P4, per run where the watch is armed; the lane-level bound after P2 and P4 |
| native R2 (exec mode) | candidate | same | **idle stalls only** (§7.1) | same; `--shard` stays refused (`:3232-3236`) |
| assay R0/R1 lane (container) | one command | `ProgressWatch` on `--progress`, heartbeat 60 s | **yes** | lane `budget` may become `unbounded` after P2 |
| command lane, container | the command | `LogStreamWatch` (RG-41), armed on every such lane since P1 (not only where `stall_timeout` is declared) | **yes** | cap may be dropped after P2 |
| command lane, exec mode | the command | **yes** since P1: `StreamWatch` over the runner's own output stream (it has no container log to tail) | **yes** | cap may be dropped after P2 |
| command lane, bare-host (`bare-host`) | the command | **yes** since P1: same `StreamWatch` (G1 closed) | **yes** | cap may be dropped after P2 |
| sequence lane | members | leaves' own watches; the sequence itself declares none (G2, by decision) | partial (G2) — and settled: nothing to add | nothing removed at THIS level; the members' own rows decide |
| canary (R3 probe inside an assay lane) | probe | none (`verdict.schema.json:2538`) | **no** | `budget_per_attempt` stays until P5 |
| build / image bake (cmru steps, runner builds) | step | cmru procio watchdog, output-only (CM-1) | partial (G4) | step watchdog stays; only its silence definition widens (P8) |
| release gate (cmru `release`, `tester-gate`) | the whole gate | composition: member lanes' watches + CM-1 | partial | nothing removed; it inherits member decisions |
| remote shard (either kind) | shard | shard's own run-gate watch (which P1 makes unconditional) + the fleet watch | partial (G6/G7) | P6 (a fleetd + run-gate boundary) |

Nothing in this table is a reason to remove a cap today: the rows that still
have no watch (canary/R3, cmru build steps, remote shards, and the sequence row
that is none of its own) keep their caps until their package ships, and no cap
is removed by P1 at all — P1 only ARMS the watches.

### 7.1 Hole in the native R2 row: a candidate that never completes a unit

Added by the controller, corrected by the assay-side review of 2026-11-25
(vbpub-assay; verified there in assay at `17c61bbb0`).

The two native R2 rows say the watch exists. It exists for an **idle** stall
only. `liveness.py` decides a hang as `idle_for >= bound and not cpu_growing`
(`finish_hang` has the same term) and only with a complete resource trace;
`cpu_growing` stays true whenever the resource sample is not `clear`, and new
stdout/stderr counts as progress. So these candidates are never ended by
liveness: one that spins on CPU (a mutant that makes a loop endless), one that
loops while logging, and one under pressure that never clears. After 1024
resource samples without progress (about 17 minutes) the trace is marked
incomplete, so even a candidate that goes idle later is no longer `hung`.
Today the per-candidate budget hides all of this.

Rules P4 implements (assay):

- **Progress is a completed plugin event.** Neither CPU time nor output is
  progress for this bound (output may keep deferring the idle rule).
- **The no-progress bound is CPU time:** the candidate process tree's
  CPU-seconds since its last completed event (before and after the first one),
  starting value `max(3 x baseline_wall, baseline_wall + 60 s)` CPU-seconds.
  CPU accrues only when the candidate runs, so pressure needs no pause rule.
  The kill is bucket `hung` / `CANDIDATE_HUNG`, never `killed`, with a new
  resource-evidence decision `cpu-without-progress` (`mutation_store.py`
  accepts only the two idle decisions today, so it ships in the v21 cut).
  UNVERIFIED, P4 must test: that `candidate_cpu_s` stays monotonic when
  children exit.
- A blocked candidate that uses no CPU stays with the idle rule. If pressure
  never clears for it, only the lane or fleet watch can see it; disclosed.
- The long resource trace is decimated or summarised, not declared incomplete.
- **Armed per run, not per lane kind.** Liveness is injected only for some runs
  (`runner.py:6043-6062`): non-pytest argv, non-Python and plugin-less runs use
  the plain runner, and an incomplete resource source can never certify `hung`.
  There `budget_per_candidate` stays binding (or the lane is refused by name),
  and the verdict states which applied.
- The `budget = "unbounded"` admission rule (`config.py`
  `_refuse_unbounded_without_unit_bounds`) assumes each candidate is bounded;
  P4 rewrites it on "the no-progress watch is armed".
- Required P4 tests: spin without output; spin with output; spin under
  never-clearing pressure samples; idle after more than 1024 samples; one slow
  test below the CPU bound survives; a plugin-inactive run keeps the budget.

Until P4 ships, a lane keeps an explicit per-candidate budget of about three
times its measured baseline suite time. With `"none"` one spinning mutant runs
until the finite lane budget and ends the whole lane `BUDGET_EXCEEDED` (main
controller ruling, 2026-11-25; ciu applies it in CIU-235).

## 8. Operator questions — ANSWERED (operator, 2026-11-25; recorded here, not proposed)

The five questions were sent by the main controller with a recommendation each.
The operator's answers are below; where the answer differs from the recommendation,
the answer wins and the design is corrected where it had stated the other.

1. **`BUDGET_EXCEEDED` for a stopped shard — ANSWERED: `INCONCLUSIVE`.**
   A stalled shard's campaign verdict is `INCONCLUSIVE`, and it carries the
   missing-shard list. `BUDGET_EXCEEDED` is retained only for compatibility with
   historical verdicts (§3.3), never as this case's verdict.
2. **Where the fleet driver lives — ANSWERED: OVERRIDDEN, the fleet driver is
   NOT `scripts/netcup`.** The recommendation above was `scripts/netcup`; the
   operator overruled it. Placing test-lane shards on hosts is the
   **run-gate/fleetd boundary** whenever fleetd manages those hosts:
   `scripts/netcup` only provides the netcup API and holds **no host keys**; the
   remote gate runner and the hosts/ssh configuration move OUT of
   `scripts/netcup` in that line (the shared host runner, fleetd's own package
   P-fd-05 / controller `CTL-51`). Several ciu roots may request runs, so fleetd
   serves several clients rather than one repository's controller.
   Consequences elsewhere in this document: §1.3's inventory names where the
   runner lives TODAY, not where P6 puts it; §4's "no cross-host driver" and its
   target shape are owned by fleetd + run-gate (§5 row P6); §6b's store
   collection still lands on TODAY's runner, because it is a fix to the code
   that exists now, not to the code P6 will move.
3. **Retirement of `budget_per_candidate` — ANSWERED: deprecated now, removed
   one release after P4 and P5 are proven.** Ignore-with-WARN is the state in
   between; the removal is a later release, and the deprecation itself is
   stated in assay's own release note (vbpub-assay, A-457's line).
4. **Stall window for a lane of unknown cadence — ANSWERED: 300 s.**
   `DEFAULT_STALL_BOUND_SECONDS = 300.0` is the default and it is never below
   `3×` a published cadence: an assay lane with a published cadence keeps
   `max(300, 3× cadence)` (RG-7), and the R2 post-`command_finished` bound stays
   600 s. An unmeasured lane does NOT have to declare `stall_timeout`; it
   inherits 300 s and the disclosure says the bound is `auto`. This is P1's
   shipped behaviour (`run-gate-project/run_gate.py:740`, `stall_bound_facts`,
   `print_lane_bounds`).
   - **One exception the operator has not ruled on yet, reported rather than
     guessed:** a lane whose child buffers its own stdout is silent by this
     definition for its whole run, and 300 s will stop it. The escape is that
     lane declaring its own `stall_timeout` with the measured reason; P1 does not
     widen the default for it.
5. **Canary cost — ANSWERED: accept a disclosed exception until P5.** R3 stays
   capped at `budget_per_attempt` while R2 goes unbounded, and the run says so
   in its disclosure rather than pretending the whole lane is unbounded. P5
   (canary liveness, or an explicit unwatched disclosure) is what ends the
   exception; the cap must not be removed before it ships (§7's rule).

## 9. UNVERIFIED

- That `cli_extended.procio`'s watchdog treats streamed output as progress at
  all (`progress_paths=()` is proven; whether `on_line` resets the clock is
  **not** read in this session) — CM-1's real reach is therefore UNVERIFIED.
- That assay's canary probe truly has no liveness hook end to end (proven only
  by the verdict-schema producer note at `schemas/verdict.schema.json:2538`
  and the absent call sites; not traced through `canary.py` in full).
- The full runtime of the full suite for `ciu` (416 s is the prompt's figure;
  the artifact's own `command_finished` event says 429.4 s, so the two
  measurements differ by ~13 s and the gap is unexplained here).
- Which exact `kind = "command"` lane rollback v2 uses (`r2` is named in the
  report; the branch's checked-out toml was read by `git show` but the lane
  literally named `r2` is not present in that file).

## 10. What this design does not do

This document itself ships no behaviour change, no schema change and no test
change: it names the order in which another agent may change them, and the single
rule that decides when a cap may fall — a cap is removed only where a stall watch
exists, and a lane that declares `unbounded` without that watch is refused by
name.

**P1 has since shipped** (run-gate rev 77, package `P1` in §5): every watchable
lane kind is armed with a stall bound. What P1 newly ARMED is the *command*
kinds — the exec/bare-host runners that had no signal at all (RG-41's
`LogStreamWatch` already covered a container command lane), and the flat 300 s
default now reaches every command lane that declares nothing. An **assay** lane
was already armed before P1 with `ProgressWatch(auto_bound=True)` (RG-7: the
`max(300, 3x cadence)` / 600 s post-command rule), so for those lanes P1 changed
what is *disclosed and recorded*, not the number enforced. P1 removed no cap and
does not accept `budget = "unbounded"` (that is P2), which is exactly the
ordering the rule above demands. The rest of §5's packages are still design.
