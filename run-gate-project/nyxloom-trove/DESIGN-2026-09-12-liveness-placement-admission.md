# Liveness, placement, admission — why budgets are ceilings, not detectors

**Design of record, 2026-09-12, RG-55 wave controller.** Operator directive
(~13:40Z): "hard budgets are a nice ceiling but if e.g. on old hardware things
are run, pre-set budgets will cause everything to fail; if progress can be
judged we do not need to rely on budgets; judging progress should happen
mechanically … should we have a separate slice to provide guarantees? should
the daemon get its own slice? run-gate (or assay) should name what they need?
the daemon running with root rights could also adjust cgroup settings? … write
up a design doc and example flow … persist our reasoning … fold this into our
planned work … RAM PSI is the only limit."

Decisions here continue the RG-55 plan's numbering (D-1..D-16 settled there)
as **D-17..D-26**; rulings live in `reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
(RW-29 adopts this document). Consumers: run-gate, cgroup-profiler (the
daemon), assay, mdt host-setup (host slices), ciu v8 (`ciu gate`).

## 1. What happened, and what each incident teaches

| when | fact | lesson |
|---|---|---|
| 2026-09-12 12:34Z (RW-26) | Claude Code's low-memory guard killed every TRACKED background command in two agent sessions ("stopped because the system is running low on memory"). It reads host free memory, not cgroups. It killed run-gate's owner process; the mutation container kept judging, orphaned, with nobody enforcing its 4 h budget. Cheap watchers were killed again every few minutes. | A lane's OWNER and its WATCHER must not live in the process tree of an interactive tool that reaps its children under pressure. The devcontainer is the wrong place for both. |
| 2026-09-12 13:22Z (RW-28) | A mutant flipped `threading.Thread(daemon=True)` → `False` in `lib/serve.py`. pytest ran every test, printed its summary, then hung at interpreter exit joining the thread: 0 % CPU, every thread in futex wait, 37 min lost. assay's `budget_per_candidate` was unset; the lane budget was unenforced (owner dead). | The signal was there ("runner reported completion, process alive, no CPU") and nobody read it. A time bound would have cut the loss but is the wrong instrument: the honest kill is an assertion on `thread.daemon`, and a fixed bound fails on slow hardware. |
| 2026-08-04 (memory `soulmask-memory-pressure-findings`) | `memory.low` is inert estate-wide (cgroup2 mounted without `memory_recursiveprot`; leaf scopes declare `memory.low=0`); only `memory.min` survives, and only when the leaf re-declares it. Gates share `dev-background.slice` with the ~4 GiB dstdns stack, so a gate starts with ~2 GiB before `memory.high`. "The user's own instinct was a separate `dev-gates.slice`; that remains unbuilt." | Guarantees must be `memory.min` on a leaf the daemon itself declares, or nothing. Gates need their own capacity object before admission can mean anything. |
| RG-55 plan D-1 | The profiler daemon sits in `dev-interactive.slice` ("never inside its own measurement"). | Right tier to AVOID (background); wrong tier to SHARE: interactive's `memory.high` (5 G) throttles the daemon whenever the IDE and agents bloat, exactly when liveness matters most. |

## 2. Principle and decisions

**D-17 — Bound the absence of progress, never the presence of duration.**
Three layers, each mechanical and hardware-independent:

1. *Liveness* — is it alive? Read the work's cgroup: `cpu.stat usage_usec`
   delta, `io.stat` bytes, `memory.current` movement, pid turnover in the
   subtree. Slow hardware makes deltas smaller, never zero. "No CPU and no
   I/O for N seconds" is a stall verdict whose N bounds idleness, not work.
2. *Cadence* — is it moving? Liveness misses a busy loop (100 % alive, going
   nowhere). Discrete progress events stay, but the PRODUCER publishes its own
   calibrated expectation: assay measures the baseline suite on the same host
   before judging, so it knows the suite wall time and the slowest test, and
   writes `expect_next_event_within_s` into the stream header and per-phase
   events. The consumer enforces the hint it was given, never a constant.
3. *Derived ceiling* — safety only. Per-candidate bound = max(3 × baseline,
   baseline + 60 s), computed on the host at run time. Absolute `budget`
   values remain scheduling caps ("never more than 4 h"), not correctness
   bounds. Lane budgets default from the lane's own history (footprint
   manifest duration median) when declared as `auto`.

Verdicts name their readings: `stalled` (no liveness for N s; last event
…), `runaway` (alive; no event within the cadence hint), `throttled` (the
lane's own `memory.pressure full` high under its `memory.high` — the request
was too small), `over_ceiling` (derived bound), `hung` (assay: runner
reported completion, process alive). The clock that measures N PAUSES while
host or gates-slice memory PSI is above threshold: time during which the CPU
was not actually available is not evidence of a stall.

**D-18 — The daemon is the estate's liveness oracle and cgroup actuator.** *(superseded in part by A1/D-27: the daemon is also the WATCHER and lives in its own `cgprofile.slice`, not under `dev.slice`.)*
It already samples every session's cgroup at 1 s; `ctl status` gains a
per-session `liveness` block (`last_activity_at`, `idle_for_seconds`,
`cpu_seconds`, `io_bytes`) computed from the samples it has. It survives the
devcontainer's guard because it is not in that process tree. It moves from
`dev-interactive.slice` to a new **`dev-infra.slice`** (D-24) and declares
`memory.min` on its OWN cgroup at start (root, `cgroupns=host`; the only way
a floor is real on this host).

**D-19 — Gates get their own capacity object: `dev-gates.slice`.** All lane
containers and every placed lane leaf (D-20) live under it; its `memory.max`
is what admission (RG-56 in run-gate, S16.6.1 in ciu v8) keys on; its
`memory.pressure` is the pressure signal ("swap usage is never the gate; PSI
is", D-6). The dstdns stack and other long-running dev stacks stay in
`dev-background.slice`. Sizing on this 16 GiB host: `MemoryHigh=4G`,
`MemoryMax=6G`, `MemorySwapMax=32G`, `CPUWeight=20`, `IOWeight=10`,
`ManagedOOMMemoryPressure=kill` (a gate is disposable; a stack is not).

**D-20 — Lanes name what they need; the daemon places them.** A lane's
request is `resources.memory`/`resources.cpus` (RG-48) or, when absent, the
footprint manifest's median × 1.5 (disclosed as derived). Ephemeral lanes are
capped by docker at create (today). For **exec** and **bare-host** lanes,
which docker cannot cap individually, run-gate asks `ctl start --place
--memory-high <request> --memory-max <ceiling>`: the daemon creates a leaf
`<gates slice>/rg-<token>/`, migrates the lane's pid subtree into it as the
token resolver discovers them (contract §4.3), applies the caps, reads them
back (`applied`, the S13.3.2 discipline), and on `stop` moves any survivors
back and removes the leaf. Result: exact per-lane `memory.peak`, PSI and CPU
even for exec and bare-host lanes (RG-57's "devcontainer-wide" caveat
disappears), and `memory.high` throttles instead of killing — the lane slows
and its own `memory.pressure` says so (`throttled`).
*Why a sibling leaf under the gates slice and never a child of the container's
scope:* enabling a controller in a container's cgroup requires
`cgroup.subtree_control`, after which the scope may hold no processes itself
(cgroup v2 "no internal processes"); every later `docker exec` (VS Code into
the devcontainer, run-gate into a tester) would fail with `EBUSY`. Moving a
pid OUT of a container's scope is safe: it stays in the container's pid and
mount namespaces (pid-1 death still kills it), only its accounting moves.

**D-21 — run-gate's owner is detached from the first byte.** *(DROPPED by A1/D-28: enforcement and the verdict record live in the daemon; the client is disposable.)* `run-gate <lane>`
forks the owner into its own session (`setsid`), writes the R-39 inflight
record (owner pid, token, log path), and the CLI merely ATTACHES (streams the
log; Ctrl-C detaches; `run-gate attach <lane>` re-attaches; `--foreground`
keeps today's behaviour for tests). A client killed by any guard never orphans
a lane; an owner killed by the kernel leaves a record the next invocation
reconciles (R-39) and the daemon's `gc` clears (session + leaf).

**D-22 — run-gate's ProgressWatch becomes a judge of progress, not of time.**
Inputs: the lane's progress stream (assay's NDJSON with cadence hints; a plain
lane's log stream), `ctl status` liveness + the leaf's `memory.pressure`, host
and gates-slice PSI. `stall_timeout` is reinterpreted as the idle bound (N in
D-17) and defaults to `auto` = max(300 s, 3 × the stream's own cadence hint);
the clock pauses under pressure; the verdict vocabulary of D-17 replaces "no
progress for N". Without a daemon: the basic sampler (RW-12, 5 s tick) gives
CPU/memory deltas for container lanes, `getrusage` gives nothing live for
bare-host lanes → cadence + derived ceiling only, disclosed.

**D-23 — assay judges its own candidates the same way.** Default
`budget_per_candidate = "auto"` (D-17 layer 3, printed in the plan line);
per-test progress events and the cadence hint in the stream header; the
python runner exits via `os._exit(rc)` after `pytest.main` so a leaked
non-daemon thread cannot hang the process (the mutant then survives, forcing
the honest assertion); liveness (CPU-time growth of the child tree, sampled
from `/proc`) marks a candidate `hung` — distinct from `budget_exceeded`,
never `killed`; `run --resume --rejudge <id>,…` / `--rejudge-outcome
hung,budget_exceeded,error` re-judges without hand-deleting state files.

**D-24 — Host slices are mdt host-setup's; every consumer degrades to today's
placement when the new env is unset.** *(amended by A1/D-29: only `dev-gates.slice` is mdt's; `dev-infra.slice` and `CGROUP_PARENT_DEV_INFRA` are withdrawn.)* `dev-infra.slice` and `dev-gates.slice`
are rendered/installed by `modern-debian-tools-python-debug/host-setup/`
(operator-installed, as every other slice). The daemon's compose template
uses `$CGROUP_PARENT_DEV_INFRA` when set, else `$CGROUP_PARENT_DEV_INTERACTIVE`
(today); run-gate uses `$CGROUP_PARENT_DEV_GATES` when set, else
`$CGROUP_PARENT_DEV_BACKGROUND` (today). Nothing breaks before the host is
updated; `doctor` says which slice is in effect and why.

**D-25 — D-15 daemon safety is extended by whitelist, not relaxed.** Writable
paths: the sessions volume, DAMON sysfs, the daemon's own cgroup directory
(`memory.min` only), and `<cgroupfs>/<gates slice>/rg-*/` (create, `cgroup.procs`,
`memory.high`, `memory.max`, `cpu.weight`, remove). A `--place` whose parent
is not the configured gates slice is refused (`place-refused:parent-not-gates-
slice`); a production tier is unreachable by construction; every cgroup write
is an `events.jsonl` row.

**D-26 — ciu v8 converges on the same objects.** `testing.cgroup_slice`
defaults to the gates slice; S16.6.1's ledger sums live reservations AND the
daemon's live sessions (`ctl status`) against the gates slice, with its PSI;
LaneResult carries `resources_measured` + `liveness`; `ciu gate` exports the
token and may ask for placement of `host`/`exec` lanes exactly as run-gate
does. Recorded as SPEC-V8 Appendix D.7.

## 3. Slice layout after this design (host, mdt host-setup)

```
dev.slice                       IO ceiling for the whole dev estate; MemoryMin ceiling for guaranteed children
├── dev-interactive.slice       devcontainers, IDE, agents            (unchanged)
├── dev-infra.slice   NEW       cgprofile-host-daemon, future estate daemons
│                               MemoryMin=256M MemoryHigh=768M MemoryMax=1G CPUWeight=100 IOWeight=50, no ManagedOOM kill
├── dev-gates.slice   NEW       every lane container + every placed lane leaf rg-<token>   ← capacity object
│                               MemoryHigh=4G MemoryMax=6G MemorySwapMax=32G CPUWeight=20 IOWeight=10 ManagedOOM kill
├── dev-background.slice        long-running dev stacks (dstdns …)   (unchanged; gates leave it)
├── dev-buildkitd.slice         (unchanged)
└── dev-memory_min_guaranteed.slice  (unchanged; individually governed workloads)
```

Why not `dev-memory_min_guaranteed.slice` for the daemon: that slice budgets
floors for individually governed workloads; the daemon needs a small floor AND
no OOM-kill AND a tier name that says "observes and acts", so operators and
ciu governance never put a workload next to it. Why not `MemoryLow`: inert on
this host until `memory_recursiveprot` is mounted (mdt's sweep can fix it, but
a design must not depend on it); `memory.min` declared by the leaf is real
today.

## 4. Example flow — `run-gate assay-r2` (a bare-host mutation lane) with the daemon up

```
1  client   run-gate assay-r2
            └─ forks OWNER (setsid), writes .run-gate/inflight/assay-r2.json {owner_pid, token, log}
            └─ ATTACHES: streams the owner's log; Ctrl-C detaches ("run-gate attach assay-r2" resumes)
2  owner    loads run-gate.toml; request = lane.resources.memory
            | else footprint manifest median × 1.5 (disclosed "derived") | else none (admit without reservation, disclosed)
            prints: run-gate: host memory PSI full avg10=0.5% | gates slice: current 1.9G / high 4G / max 6G, full avg10=0.0%
3  admit    ctl status → live sessions [{token, expected/requested, liveness}], ctl host → gates slice readings
            sum(requests of live) + this request ≤ gates memory.high and PSI full avg10 < 5%  → admitted
            else WAIT (notice every 30 s naming the sessions and readings; clock paused while PSI high; bounded by --admission-wait)
            else REFUSE exit 2 naming the readings   (--allow-pressure bypasses, disclosed; --dry-run reports only)
4  start    RUN_GATE_PROFILE_SESSION=<token> exported into the child env
            ctl start --target containerid:<self> --scope container-shared --token <token> --damon on
                      --place --memory-high 1.5G --memory-max 2G
            daemon: mkdir <gates>/rg-<token>; caps written + read back (applied); token resolver migrates the lane's pids
                    as they appear; summary accumulates from the LEAF cgroup (exact peak, PSI, cpu; DAMON on the pids)
5  run      assay writes .assay/progress.jsonl:
              {"event":"plan","baseline_s":97.4,"slowest_test_s":6.1,"expect_next_event_within_s":18.3,"budget_per_candidate_s":292.2 (auto)}
              {"event":"test","nodeid":"tests/test_serve.py::test_x","outcome":"passed","duration_s":0.4}
              {"event":"candidate","index":47,"outcome":"hung","reason":"runner reported completion; no CPU growth for 60 s"}
            owner's ProgressWatch every 5 s (RW-12 tick):
              stream cadence: last event 3 s ago (hint 18.3 s)                                 → moving
              ctl status liveness: idle_for_seconds 0, cpu_seconds +4.9                        → alive
              leaf memory.pressure full avg10 0.0% under high 1.5G                             → not throttled
              gates PSI full avg10 0.3%                                                        → clock running
            verdict lines when something is wrong (each names the readings):
              run-gate: STALLED assay-r2 — no CPU/IO in rg-<token> for 300 s; last progress event 41 s before that
              run-gate: THROTTLED assay-r2 — rg-<token> memory.pressure full avg10 38% under memory.high 1.5G; raise resources.memory or accept the slowdown
              run-gate: RUNAWAY assay-r2 — no progress event for 62 s (cadence hint 18.3 s), CPU busy; assay's own per-candidate bound applies first
6  stop     ctl stop → Summary §7 (+ liveness, placement blocks); daemon moves survivors back, rmdir rg-<token>
            history record schema 2 (+ admission, placement, liveness); footprint line: "| peak 1.31 GiB (memory.peak, leaf) | manifest 1.2 GiB"
7  failure  daemon down      → no placement, no liveness; basic sampler (container lanes) / rusage (bare-host); cadence + derived ceiling; disclosed
            client killed    → owner continues; "run-gate attach assay-r2" re-attaches; verdict lands in history regardless
            owner killed     → R-39 stale record; next invocation reconciles; ctl gc removes the session and the leaf
            slice env unset  → today's placement (D-24), doctor says so
```

The same flow applies to an exec lane (`--target containerid:<tester>`, the
lane's pids placed out of the tester's scope into `rg-<token>`) and, minus
step 4's placement, to an ephemeral lane (docker caps at create under
`$CGROUP_PARENT_DEV_GATES`).

## 5. Contract amendment (RG55 interface contract v1.1 — controller-authored before P5/P6b)

Additive under `contract: 1` (unknown keys are ignored by consumers; goldens
extended): `status` sessions gain `liveness` and `placement`; `start` gains
`--place`, `--memory-high`, `--memory-max`, `--cpu-weight` and returns
`placement {leaf, applied}`; `host` gains the `gates_slice` block; Summary
gains optional `liveness` and `placement`; new error codes `place-refused:*`.

## 6. Packages, sequencing, releases

| pkg | scope | branch / worktree | starts | ships as |
|---|---|---|---|---|
| P7 | assay B091: D-23 (auto budget, per-test events + cadence hint, `os._exit` runner, `hung`, `--rejudge`) | `assay-liveness` / `.worktrees/assay-liveness` | now (parallel) | assay 6.2.0 (+ `.assay-inbox/release.json` to dstdns) |
| P8 | mdt host-setup: `dev-infra.slice`, `dev-gates.slice`, env keys, install/check/sweep, env export to devcontainers | `mdt-dev-slices` / `.worktrees/mdt-dev-slices` | now (parallel) | mdt host-setup (operator installs on the host) |
| P6 | cgprofile CP-4..CP-7, then CP-8 liveness (D-18) + CP-9 placement (D-20/D-25) + `dev-infra` compose fallback (D-24) after the v1.1 amendment | `rg55-followups-cgprofile` from the P1 tip | when P1's r2 container exits | cgprofile 1.1.0 |
| P5 | run-gate RG-56 admission + RG-62 (D-21 detached owner/attach, D-22 progress judge, gates-slice default, placement requests) | from main after P4 + P6 merge | after P4 and P6 | run-gate 23.9.0 |
| — | ciu SPEC-V8 Appendix D.7 (D-26) | main | now (controller) | docs |

Base packages (P1 → cgprofile 1.0.0, P2 → run-gate 23.7.0, P4 → 23.8.0) ship
unchanged first; RW-28's fixed `budget_per_candidate` stays as the stopgap
until P7 lands and is then replaced by `auto`.

## 7. Out of scope, on purpose

The Claude Code guard itself (client-side, not ours); production tiers (never
touched by placement, D-25); DAMON paddr (CP-3); retention tuning (CP-1);
socket transport (CP-2); nested cgroups inside container scopes (rejected,
D-20). Admission POLICY numbers stay "measure first": the thresholds above
are defaults with a documented reason, expected to be re-tuned from the first
weeks of footprint data.

## A1 — Amendment (RW-30, operator review ~14:25Z): boundaries corrected

The operator: "mdt is supposed to be only a devcontainer / cockpit. the `dev*`
slices are there to guarantee *load* is contained and limited to not interfere
with the rest of the host. on the other hand run-gate (ciu v8?) would gain a
root-daemon which would/should run as a deployment on the host and be consumed
by deployed run-gate (ciu v8?) in the devcontainer. who is the watcher which
needs guarantees? is it singleton or per-lane? i was thinking to place it as
service with the daemon." All three points are right; §2/§3/§6 above are
amended as follows (the original text stays so the reasoning trail is
visible).

**D-27 — The watcher is a singleton and it is the daemon.** A lane is a
SESSION inside the daemon (state, not a process). At `ctl start` run-gate
authors the policy: `--progress-stream <path as the lane sees it>` (the
daemon reads it through `/proc/<lane pid>/root/…`, no extra mounts),
`--idle-bound auto|<s>`, `--ceiling auto|<s>`, `--on-stall kill|report`. The
daemon judges liveness (its own cgroup samples) and cadence (the stream's own
hint), pauses its clock under gates-slice/host memory PSI, and ENFORCES:
`kill` = `cgroup.kill` on the lane's leaf (placed lanes) or the pid subtree,
then records `watch {state, verdict, readings}` in the session; `ctl status`
and `ctl stop` return it. run-gate maps the verdict to its exit codes and
history record. Without a daemon, run-gate's in-process ProgressWatch (D-22)
is the best-effort fallback.

**D-28 — run-gate's client is disposable (D-21 dropped).** With the daemon
enforcing, a client killed by any guard loses only its live log: the lane is
still bounded, the verdict is still recorded, and the next invocation
reconciles from the daemon's session record (R-39 reconcile → `ctl status`
/ `gc`). No detached owner, no `attach` verb; today's process model stays.

**D-29 — Slice boundaries follow ownership of the LOAD.**
- `dev.slice` (mdt host-setup) contains dev load and nothing else:
  `dev-gates.slice` (D-19) lives there because gates ARE dev load and the
  slice is their containment and the admission capacity object. mdt stays a
  devcontainer/cockpit template plus the host-side containment it always
  carried.
- The daemon is a HOST DEPLOYMENT, not dev load: it ships its own top-level
  **`cgprofile.slice`** with its deployment (`scripts/cgroup-profiler/infra/
  cgprofile.slice`, installed by the operator with the stack, precedent
  `srdm.slice` and nyxloom's `infra/slices/nyxloom-daemon.slice`):
  `MemoryMin=128M`, `MemoryHigh=768M`, `MemoryMax=1G`, `CPUWeight=100`,
  `IOWeight=50`, no ManagedOOM kill. The compose template authors
  `cgroup_parent: cgprofile.slice` outright (no environment variable; ciu
  honours an authored key). If the unit is not installed, systemd creates the
  slice implicitly without bounds and the daemon runs unprotected; `ctl host`
  and run-gate `doctor` say "cgprofile.slice has no unit: no memory floor".
  `dev-infra.slice` and `CGROUP_PARENT_DEV_INFRA` are withdrawn.
- Consumers of the daemon: run-gate now, `ciu gate` in v8, both from
  devcontainers via `docker exec` (D-2; CP-2 socket transport later).

Corrected layout:

```
cgprofile.slice          NEW, shipped by cgroup-profiler   cgprofile-host-daemon (watcher + oracle + actuator)
dev.slice                mdt host-setup — dev LOAD containment only
├── dev-interactive.slice   devcontainers, IDE, agents (unchanged)
├── dev-gates.slice   NEW   lane containers + placed lane leaves rg-<token> — capacity object
├── dev-background.slice    long-running dev stacks (unchanged; gates leave it)
├── dev-buildkitd.slice / dev-memory_min_guaranteed.slice (unchanged)
```

Package consequences: **P8** = `dev-gates.slice` only (env `CGROUP_PARENT_DEV_
GATES`, install/check/sweep/docs); **P6** adds `infra/cgprofile.slice`, the
authored `cgroup_parent`, the `ctl host` slice report, and CP-8 becomes the
WATCH role (`start --progress-stream/--idle-bound/--ceiling/--on-stall`,
`watch` block in `status`/`stop`, `cgroup.kill` enforcement, D-25 whitelist
gains `cgroup.kill` on `rg-*` leaves only); **P5** shrinks to RG-56 admission
+ RG-62 as policy author/consumer/reconciler with the in-process fallback
watch; SPEC-V8 D.7 item (5) reads "its own `cgprofile.slice`".
