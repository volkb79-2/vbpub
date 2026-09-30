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

**Current placement architecture:** amendment A3 at the end of this document
supersedes the original D-20/D-25 assumption that cgprofile may create lane
leaves directly below `dev-gates.slice`. Treat A3 as authoritative for
placement and lifecycle; the earlier text records the design evolution. The
resource/admission role of `dev-gates.slice`, private namespaces, and the
R-36h non-interference rule remain in force.

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
capped by Docker at create (today). For **exec** and **bare-host** lanes,
which Docker cannot cap individually, run-gate asks `ctl start --place
--memory-high <request> --memory-max <ceiling>`. The original plan put a
profiler-owned leaf directly below `<gates slice>`; A3 replaces that physical
layout with a systemd-created delegated scope below the slice and a
profiler-owned `rg-<token>` leaf inside the scope. The daemon resolves the
scope's actual `ControlGroup` from systemd, moves the lane's PID subtree into
the owned leaf as the token resolver discovers it (contract §4.3), applies
limits and reads them back (`applied`, the S13.3.2 discipline), then restores
survivors and removes only its owned leaf/scope on `stop`. Result: exact
per-lane `memory.peak`, PSI and CPU even for exec and bare-host lanes (RG-57's
"devcontainer-wide" caveat disappears), and `memory.high` throttles instead
of killing — the lane slows and its own `memory.pressure` says so
(`throttled`).
*Why the profiler leaf is outside the container's existing runtime scope:*
enabling a controller in a container's cgroup requires
`cgroup.subtree_control`, after which that scope may hold no processes itself
(cgroup v2 "no internal processes"); every later `docker exec` (VS Code into
the devcontainer, run-gate into a tester) could fail with `EBUSY`. The leaf
therefore lives under a separate delegated scope nested in the gates slice,
not under Docker's scope and not directly under the systemd-owned slice.
Moving a pid out of the container's scope is safe: it stays in the
container's pid and mount namespaces (pid-1 death still kills it), only its
accounting moves.

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
CPU/memory deltas for container lanes, `os.wait4()` gives only final
lane-child accounting for bare-host lanes → cadence + derived ceiling only,
disclosed.

**D-23 — assay judges its own candidates the same way.** Default
`budget_per_candidate = "auto"` (D-17 layer 3, printed in the plan line);
per-test progress events and the cadence hint in the stream header; the
python runner exits via `os._exit(rc)` after `pytest.main` so a leaked
non-daemon thread cannot hang the process (the mutant then survives, forcing
the honest assertion); liveness (CPU-time growth of the child tree, sampled
from `/proc`) marks a candidate `hung` — distinct from `budget_exceeded`,
never `killed`; `run --resume --rejudge <id>,…` / `--rejudge-outcome
hung,budget_exceeded,error` re-judges without hand-deleting state files.

**D-24 — Host slices are mdt host-setup's; gate consumers fail closed when the
gates binding is unset.** *(amended by A1/D-29: only `dev-gates.slice` is mdt's;
`dev-infra.slice` and `CGROUP_PARENT_DEV_INFRA` are withdrawn.)* `dev-gates.slice`
is rendered/installed by
`modern-debian-tools-python-debug/host-setup/` (operator-installed, as every
other dev slice). The daemon's compose template authors `cgprofile.slice`
outright; run-gate and the other gate launchers require
`$CGROUP_PARENT_DEV_GATES`. Nothing may silently fall back to Docker's
unbounded default; `doctor` says which slice is in effect and why.

**D-25 — D-15 daemon safety is extended by whitelist, not relaxed.** Writable
paths: the sessions volume, DAMON sysfs, the daemon's own cgroup directory
(`memory.min` only), and the profiler-owned `rg-*` leaf inside a verified,
systemd-delegated lane scope (create, `cgroup.procs`, `memory.high`,
`memory.max`, `cpu.weight`, `cgroup.kill`, remove). The daemon may enable the
required controllers at the delegated scope root, but may not write cgroup
controls or create/remove children directly below `dev-gates.slice`; systemd
owns that slice. The narrow systemd bridge may create and attach to only the
per-token scope under the configured gates slice, after validating the loaded
unit and returned cgroup path. A production tier is unreachable by
construction; every cgroup write and systemd placement transition is an
`events.jsonl` row.

**D-26 — ciu v8 converges on the same objects.** `testing.cgroup_slice`
defaults to the gates slice; S16.6.1's ledger sums live reservations AND the
daemon's live sessions (`ctl status`) against the gates slice, with its PSI;
LaneResult carries `resources_measured` + `liveness`; `ciu gate` exports the
token and may ask for placement of `host`/`exec` lanes exactly as run-gate
does. Recorded as SPEC-V8 Appendix D.7.

## 3. Current host capacity and delegated ownership layout (A1/D-29 + A3)

```
cgprofile.slice                 host deployment; MemoryMin=128M MemoryHigh=768M MemoryMax=1G
                                CPUWeight=100 IOWeight=50; no ManagedOOM kill
dev.slice                       IO ceiling for the whole dev estate; MemoryMin ceiling for guaranteed children
├── dev-interactive.slice       devcontainers, IDE, agents (unchanged)
├── dev-gates.slice             gate containers + delegated placement scopes  ← capacity object
│                               MemoryHigh=4G MemoryMax=6G MemorySwapMax=32G
│                               CPUWeight=20 IOWeight=10 ManagedOOM kill
│   └── rg-profile-<token>.scope   systemd-owned transient scope, Delegate=cpu,memory
│       └── rg-<token>/            cgprofile-owned lane leaf (processes + per-lane limits)
├── dev-background.slice        long-running dev stacks (dstdns …) (unchanged; gates leave it)
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

## 4. Placement and measurement flow — `run-gate assay-r2`

```
1  request  run-gate reads lane policy; requested memory/CPU comes from the lane,
            or the disclosed footprint-derived value; admission compares live reservations
            and gate-slice PSI/capacity before it asks for placement.
2  control  run-gate sends `ctl start --place` over the selected carrier
            (`/run/cgprofile/ctl.sock` or `docker exec`); both carry the same protocol.
3  bridge   the private-namespace daemon validates the target and records each PID's
            original unit/cgroup. Through the mounted host system bus it asks systemd
            to create a per-token transient scope in `dev-gates.slice` with delegation.
            It reads the scope's actual `ControlGroup` from systemd and verifies that
            this path is beneath the loaded gates slice's actual `ControlGroup`.
4  place    after verifying `Delegate=`, required controllers, and scope identity,
            cgprofile creates its `rg-<token>` child and stages target PIDs into it
            through the systemd manager API. Once the delegated root is empty it
            enables required controllers there, writes leaf limits and reads them
            back. It verifies host-proc cgroup membership before reporting applied.
            The gates-slice ceiling contains the staging interval. No PID/cgroup/
            network host namespace is joined; no child is created directly under
            the gates slice.
5  observe  the daemon samples the owned leaf (CPU, memory/peak/pressure, I/O,
            process activity) and applies progress policy; run-gate receives status,
            liveness and verdicts. For example, assay's progress stream supplies its
            own event cadence; host scheduling delay is not a failed-test verdict.
6  stop     before teardown, cgprofile restores every surviving PID to its recorded
            origin and verifies it left the owned leaf. It then removes only the exact
            token leaf and retires the scope only after it is empty. Failed restoration
            leaves the scope tracked and reports cleanup failure; it is never silently
            treated as a successful stop. Summary/history records the measured leaf.
7  failure  a placement refusal is disclosed and cannot change the test verdict
            (R-36h). A missing daemon/carrier selects the documented fallback; it does
            not claim per-lane placement or measurement that was not obtained.
```

The same flow applies to exec and bare-host lanes. Ephemeral containers remain
Docker-capped under `$CGROUP_PARENT_DEV_GATES`; they need not be moved into a
profiler-owned leaf unless a separate contract requires per-lane placement.

## 5. Contract amendment (RG55 interface contract v1.1 — controller-authored before P5/P6b)

**Landed 2026-09-12 as `RG55-INTERFACE-CONTRACT.md` §8 (RW-34).** Additive under `contract: 1` (unknown keys are ignored by consumers; goldens
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
├── dev-gates.slice   NEW   gate containers + delegated per-lane scopes — capacity object
│   └── rg-profile-<token>.scope   systemd-owned; Delegate=cpu,memory
│       └── rg-<token>/            cgprofile-owned lane leaf
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

## A2 — Transport (RW-31, operator ~14:50Z): `docker exec` and the socket are interchangeable carriers of one protocol

The operator asked for both transports to offer the FULL functionality, exec
staying the default now (no devcontainer rebuild, no session loss), the
socket built in parallel and shipped so the switch later is a flip.

**D-30 — One protocol, two carriers.** The serve loop speaks newline-
delimited JSON, one request per connection, on ONE listener,
`/run/cgprofile/ctl.sock`; the daemon stack bind-mounts the directory
`/run/cgprofile` to the same path on the host (`root:docker 0770`, socket
`0660`). Carriers:
- **exec** (contract v1, default today): `docker exec cgprofile-host-daemon
  cgprofile ctl <verb> --json` — the in-image client connects to the socket
  from inside; arrives as uid 0; always authorised. Trust boundary = docker
  socket access.
- **socket**: the consumer (run-gate in a devcontainer, `ciu gate`)
  connects to the mounted path with stdlib `socket`; trust boundary = the
  `docker` group (the same principals who can exec), plus an optional
  `SO_PEERCRED` uid allowlist (`CGPROFILE_ALLOW_UIDS`) for finer control.
Every verb, every response, every error code and the `contract` field are
identical on both. Streaming (`ctl watch <session>`: NDJSON until the session
ends, D-27) works on both — over exec it is one long-lived exec whose stdout
run-gate reads line by line, so the per-lane cost falls from one exec per
30 s poll to one exec per lane. Timeouts are per verb, not per carrier.
`ctl version` reports `transports: ["exec","socket"]` and the socket path.

**Client side (run-gate, ciu gate):** `[profile] transport = "auto" | "exec"
| "socket"` (env `RUN_GATE_PROFILE_TRANSPORT`); `auto` = connect to the
socket path if present, else exec; one request builder and one response
parser behind a two-line carrier seam; `doctor` probes BOTH carriers and
prints which one is live and why the other is not. Switching later is a
default flip; exec stays as a permanent fallback for hosts without the mount
(locked-down CI, a devcontainer created before the mount existed) because it
costs nothing to keep.

**What needs a rebuild:** only the devcontainer template's mount of
`/run/cgprofile` (mdt, P8 follow-up M5); until then exec is unchanged and
`auto` resolves to exec. Parity is proven WITHOUT a rebuild by a throwaway
probe container that mounts the directory (reviewer / P3 live probes run
every verb over both carriers against the same daemon and diff the JSON).

**Packages:** P6 (daemon: listener on the shared directory, dir/socket
permissions, peer-cred allowlist, `watch` streaming, `version.transports`,
contract v1.1 §1.1 amendment "transport-agnostic protocol, two carriers");
P8 M5 after its review (template mount line + docs); P5 (run-gate transport
seam, `auto`, `doctor` dual probe, `watch` consumption). TCP stays out
(D-15 `network_mode: none`; see the transport assessment recorded in RW-31).

## A3 — Placement ownership correction: delegated scope below `dev-gates.slice` (2026-09-30)

The P6 round-6 live probe is the decisive evidence for this correction
(`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-REVIEW-round6.md`,
blocker B2). The private-namespace daemon resolved a real target and the host
systemd manager accepted the requested unit/path shape, but refused to move
the process with `Process migration not available on non-delegated units.`
The loaded `dev-gates.slice` reports `Delegate=no`. The earlier direct-child
plan was therefore not merely missing a flag: it assigned two managers
overlapping ownership of the same cgroup subtree.

**D-31 — Keep the gates slice systemd-owned; delegate one per-lane scope to
cgprofile.** The gates slice remains the systemd-owned capacity boundary and
admission object. For each placed session, cgprofile asks the host systemd
manager to create a transient scope under that exact loaded slice with only
the required controllers delegated. The daemon uses the scope's actual
`ControlGroup` property as its management boundary, creates one
`rg-<token>` child leaf there, and owns only that child subtree. The scope
name is derived from a validated session token, and an existing unit with that
name is a collision/refusal, never something to adopt or clean up.

```text
run-gate / ciu in cockpit
   │  ctl protocol: mounted socket, or docker-exec carrier
   ▼
/run/cgprofile/ctl.sock ───────────────► cgprofile host daemon
                                         (cgprofile.slice; private PID,
                                          cgroup and network namespaces)
                                                   │
                                  host system D-Bus│ StartTransientUnit /
                                  mounted in daemon│ AttachProcessesToUnit /
                                                   │ query unit properties
                                                   ▼
                                          systemd Manager (PID 1)
                                                   │ creates/owns unit
                                                   ▼
dev.slice/dev-gates.slice                         │
  └── rg-profile-<token>.scope  (systemd owns unit boundary; Delegate=cpu,memory)
        └── rg-<token>/         (cgprofile owns leaf, limits, sampling, kill)
                                  │
                                  └── cpu/memory/IO/liveness samples
                                      returned through the ctl protocol
```

The flow preserves two separate interfaces that are easy to conflate:

- `/run/cgprofile/ctl.sock` is the consumer-facing control socket. The
  devcontainer needs this directory bind-mounted so run-gate can use the
  socket carrier. It does not need the host system bus.
- `/run/dbus/system_bus_socket` is the daemon's host-management bridge. The
  daemon's Compose deployment mounts this path and uses systemd's manager
  API; it is not mounted into the cockpit. A read-only
  filesystem bind of a D-Bus socket does **not** make RPCs read-only, so the
  daemon's callable methods, unit-name/path validation, PID ownership checks,
  and bus authorization are security-critical, not incidental plumbing.

### Why the direct-child design failed

On cgroup v2, each subtree has one manager. systemd treats slices as inner
nodes in its unit tree: it places services/scopes below them and owns their
unit structure. `Delegate=` is supported for service and scope units, not for
slice units; allowing both systemd and cgprofile to create/remove children
under a slice would violate the single-writer ownership rule. This is why
setting `Delegate=yes` on `dev-gates.slice` is not the remedy, and why a raw
directory that root can happen to create below the slice is not a supported
ownership contract.

The round-6 probe made that architecture error observable: after the path was
corrected to an absolute unit subpath, the systemd manager still refused the
move because the destination unit was non-delegated. The private PID
namespace is a separate constraint: writing a host PID directly to
`cgroup.procs` from the daemon can fail with `ESRCH`, because that PID is not
addressable in the daemon's PID namespace. The systemd manager bridge solves
that PID-namespace mismatch without joining the host PID or cgroup namespace;
it does **not** make a non-delegated destination valid.

### Ownership and placement lifecycle

1. Before moving anything, validate the token and target, record each target
   process's stable identity (host PID plus a reuse-resistant start identity)
   and original unit/cgroup, and refuse daemon/self PIDs, unrelated PIDs,
   already-owned PIDs, or a token collision. Keep the gates slice's loaded
   state, actual `ControlGroup`, finite capacity, and required controller
   availability as preconditions.
2. Ask systemd over the host bus to create a transient scope beneath
   `dev-gates.slice`, with `Delegate=` limited to controllers the leaf needs
   (initially `cpu` and `memory`). Register the existing workload PIDs with
   the scope through the scope's `PIDs` property as part of transient-unit
   creation; then use `AttachProcessesToUnit` with that exact unit and child
   name to move them into the profiler leaf. Query the resulting unit's
   `Delegate` and `ControlGroup` properties. Do not derive the cgroup path
   from the unit name: require the returned path to be a strict descendant
   of the gates-slice path read from systemd, on the expected cgroup2 mount,
   and refuse any mismatch.
3. Create the token leaf only below that verified delegated root. systemd
   delegates ownership of descendants but keeps ownership of the scope
   itself. Attach target PIDs into the exact child using the manager API;
   verify the delegated scope root is then empty before enabling controllers
   there (cgroup v2's no-internal-process rule). systemd makes delegated
   controllers available but does not enable them on cgprofile's behalf.
   Apply leaf limits, read every requested value back, and verify each
   process's host-visible cgroup path before reporting `applied`. During this
   staging transition, the inherited `dev-gates.slice` ceiling remains the
   containment bound; the daemon must not claim per-leaf limits before
   read-back succeeds.
4. As the resolver discovers descendants, validate their process identity
   and attach them to the same token leaf. Reject a process that escaped the
   recorded target tree or belongs to another lane. Each placement, limit,
   refusal, and rollback transition is auditable; a partial start rolls back
   only PIDs and cgroups created for that token.
5. On ordinary stop, prevent new discovery, restore each surviving process
   to its recorded origin through the host manager, and verify restoration
   and leaf emptiness before removing the leaf. Retire/stop the transient
   scope only after it is empty, using the exact recorded unit identity.
   Never use stopping the scope as a shortcut to cleanup: that could turn a
   placement cleanup bug into an unintended workload kill. If restoration or
   verification fails, preserve the scope and its evidence, report cleanup
   failure, and do not claim a clean stop. A requested kill targets only the
   exact profiler-owned leaf and is verified against the lane and a sibling.

The API transition and controller-enable ordering are implementation gates,
not assumptions: prove them on the deployed systemd/kernel combination,
including process arrival during placement, process exit during restore,
scope disappearance/reconciliation, and partial-failure rollback. In
particular, do not mark placement `applied` until the real live probe confirms
the process is inside the intended leaf and the limit files read back as
requested.

### Alternatives considered and rejected

| option | decision and reason |
|---|---|
| Create `dev-gates.slice/rg-<token>` directly | Rejected. It bypasses systemd's unit ownership model; the live manager refused attachment because the slice is not delegated. Direct root writes do not repair that refusal or establish safe lifecycle ownership. |
| Set `Delegate=yes` on `dev-gates.slice` | Rejected. systemd does not support delegation on slice units; it would also make both systemd and cgprofile writers below the slice. |
| Put the lane leaf under the Docker/tester's existing scope | Rejected. Enabling controllers there can require the scope to become an empty internal node; that conflicts with container/runtime management and later `docker exec`. The independent delegated scope keeps the lane leaf outside that ownership domain while preserving the process's PID/mount namespaces. |
| Give the daemon host PID/cgroup namespaces | Rejected. It violates D-15 and expands ambient authority; host PID migration is instead mediated by systemd over its bus. The current P6 Compose's broad writable cgroupfs bind is separately a security gap, not an approved substitute for delegation; see the security gate below. |
| Mount the host system bus into the devcontainer | Rejected. The cockpit only needs the ctl protocol. Giving every cockpit process systemd RPC access is unnecessary authority; the bus stays daemon-only. |
| Give the privileged daemon the raw host system-bus socket | This is the current P6 bridge, not a security sandbox. A read-only socket mount does not restrict RPC methods, and daemon-side allowlists only constrain normal requests; they do not contain a compromised daemon. The scope layout does not by itself prove D-15 against daemon compromise. |
| Add a narrow host helper/broker instead of exposing the system bus to cgprofile | Deferred by D-32 for the current single-operator, rootful-Docker deployment. Keep it as a future option if the trust boundary changes or daemon-compromise containment becomes a requirement; see A4. |

The delegated-scope decision is about systemd ownership and placement, not
about reducing the daemon's host authority. D-32 records the operator's
current no-broker choice and its trust assumptions. The P6 Compose template
still has a privileged daemon, writable host cgroupfs, writable DAMON sysfs,
and the host system bus. Python path checks constrain normal application
behavior and are useful defense-in-depth; they are not an OS boundary against
arbitrary code execution in the daemon. Do not describe this arrangement as
least-privilege or as kernel-enforced D-15 containment.

The scope is a placement/ownership boundary, **not** a promise of reserved
CPU, quiet scheduling, or stable wall-clock runtime. `dev-gates.slice` limits
and contains gate load; the profiler supplies attribution and per-lane
accounting. Correctness and mutation verdicts must remain deterministic and
agnostic to host load/contention; elapsed time, throttling, or a missing
progress observation caused by scheduling pressure is not itself evidence
that a test is wrong. Explicit performance tests are the exception: they
measure performance by design. This is consistent with R-36h: profiling and
placement refusal cannot change the underlying test verdict.

Operational ceilings may stop or park work for host safety, but reaching a
wall-time ceiling is not a candidate result: it must never be translated into
`killed`, `survived`, `hung`, or an ordinary test failure without independent
behavioral evidence. Preserve the unfinished candidate as unjudged and
resumable. Likewise, absence of CPU-time growth alone cannot distinguish a
deadlock from a runnable process denied scheduler time. Liveness policy must
account for CPU pressure/run-queue delay as lost opportunity, and acceptance
must compare verdicts under quiet and deliberately contended host conditions.
If the implementation cannot make that distinction reliably, it must report
indeterminate/incomplete rather than manufacture a correctness verdict.

### Contract, host setup, and acceptance consequences

The v1.1 wire shape can continue to report `placement.leaf`, but §8.3's
current physical example and D-25's direct-child whitelist are stale: both
contract mirrors must be amended together before the revised placement is
implemented or reviewed. Session/recovery records must retain enough exact
scope identity (unit name and returned cgroup path) to restore or reconcile
after daemon restart; a glob over scope names is never a cleanup strategy.
At this writing the two checked-in mirrors already differ: the run-gate
project copy contains a paragraph describing direct `AttachProcessesToUnit`
placement into the non-delegated slice, which is absent from the
cgroup-profiler copy. Replace that obsolete paragraph in the same coordinated
edit and verify byte identity.
The host setup continues to own and bound `dev-gates.slice`; it must verify
that the loaded slice has the required controllers and finite ceilings, but
must **not** set `Delegate=yes` on that slice. Dynamic per-token scopes are
created by the daemon, not by adding a static slice or a cockpit unit. The
existing cockpit template and active dstdns config already bind-mount
`/run/cgprofile`; this is the consumer socket directory and is sufficient for
the socket carrier. Do not add `/run/dbus/system_bus_socket` to the cockpit.
The currently running container still needs a rebuild/recreate for the saved
bind mount to appear.

The acceptance evidence is live, not only a fake-D-Bus unit test: create a
scope under the real loaded gates slice; place a real lane process; verify the
host cgroup path and read-back limits; prove one lane can be killed without
touching its sibling or daemon; restore survivors to their original unit and
prove the scope/leaf are removed; inject failure at each transition and
prove no unrelated process is moved or killed. Keep the daemon's private
namespaces and the existing R-36h behavior throughout.

Systemd rationale: [Control Group Interface — single-writer ownership and
transient units](https://systemd.io/CONTROL_GROUP_INTERFACE/) and
[Control Group Delegation — Delegate is for services/scopes, not slices;
delegated controllers are enabled by the delegate](https://github.com/systemd/systemd/blob/main/docs/CGROUP_DELEGATION.md).

## A4 — Security disposition and future host-broker option (D-32, 2026-09-30)

**D-32 — Keep the current RG-55 implementation broker-free.** For this
deployment, accept cgprofile as trusted host infrastructure under the same
single-operator trust model as the rootful Docker Engine. The controller and
run-gate operator have unrestricted Docker API access (including through the
Docker-group socket); Docker documents that this access is root-level on a
rootful daemon. A broker would not protect the host from that operator, from
other code that inherits the same Docker API access, or from an administrator
who can replace the daemon container. Do not add a broker to imply protection
against principals that are already host administrators.

This decision does **not** claim that the daemon is harmless or that its
application checks contain a compromised process. The current no-broker
daemon has a private control protocol, private PID/cgroup/network namespaces,
no Docker socket, and no network. Its normal request path accepts defined
verbs and arguments, validates its targets, and guards cgroup writes. Those
are meaningful controls against malformed caller input and ordinary code
mistakes. They all execute within the daemon's own trust boundary, however.
If an attacker obtains arbitrary code execution in the daemon, Python-level
dispatch and write guards can be bypassed without invoking a shell. The
current deployment also grants the daemon `privileged: true`, writable host
cgroupfs, the host system-bus socket, and writable DAMON sysfs. The direct
system-bus bridge is therefore a consciously accepted authority path for
this trusted-host deployment, not a security sandbox and not a general claim
that D-15 is enforced against daemon compromise.

The trust paths differ as follows. The first diagram is the selected current
design: the public `ctl` protocol remains the control interface; the daemon
directly performs the host operations needed for placement, limit application,
restore, sampling, and DAMON. The scope/leaf shape is A3's delegated-scope
design; its live lifecycle still has to be proven before release.

```text
CIU / run-gate (trusted rootful-Docker administrator)
  ├── NDJSON over AF_UNIX /run/cgprofile/ctl.sock
  └── or Docker Engine API → docker exec → same ctl protocol
                              │
                              ▼
                    cgprofile host daemon
                    ├── reads host /proc and cgroup metrics
                    ├── system D-Bus → systemd Manager
                    │      create/inspect delegated scope;
                    │      attach/restore exact lane PIDs
                    ├── writes its selected cgroup leaf and limits
                    └── controls/reads DAMON sysfs
                              │
                              ▼
                    dev-gates.slice (systemd capacity boundary)
                      └── rg-profile-<token>.scope (systemd-owned)
                            └── rg-<token>/ (profiler-owned leaf)
```

The wire protocol to cgprofile is newline-delimited JSON, one request per
connection; streaming `watch` returns one JSON object per line. The socket
carrier and `docker exec` carrier carry the same verbs and response shapes.
The host system-bus interface is separate: systemd Manager method calls and
property queries perform unit lifecycle and namespace-safe PID moves. Direct
cgroupfs writes perform the leaf/controller/limit operations the daemon owns
under A3. DAMON uses its sysfs interface. A read-only bind of the bus socket
would not make those RPCs read-only; the current daemon mount is not such a
restriction in any case.

A future broker design would preserve the consumer-facing `ctl` API but move
the host mutation authority out of cgprofile:

```text
CIU / run-gate
  └── same ctl request over socket or Docker exec
        │
        ▼
  cgprofile daemon (unprivileged; read-only host observations)
        ├── samples host /proc and cgroup metrics
        ├── sends typed, private AF_UNIX requests to broker
        └── returns status / summaries over the unchanged ctl protocol
                    │
                    ▼
  host placement broker (small, separately confined, privileged service)
        ├── validates a per-run capability and process identity
        ├── system D-Bus → systemd Manager
        ├── host cgroupfs → create/move/limit/restore/kill exact scope
        └── DAMON authority is mediated here too, or by a separate
            narrowly privileged helper; it is not left writable in daemon
                    │
                    ▼
  dev-gates.slice → delegated scope → profiler-owned lane leaf
```

The broker protocol is **not designed or frozen**. Illustrative operations
would be `begin-placement(lease, verified-target, limits)`,
`attach-discovered-process(lease, verified-process-identity)`,
`restore(lease)`, and `kill(lease)`; they are not permission to pass arbitrary
PIDs, cgroup paths, unit names, D-Bus methods, shell commands, or limit
values. The broker would need independent state for active leases, a fixed
`dev-gates.slice` parent, strict controller/limit bounds, collision-resistant
scope ownership, replay/expiry rules, process identity checks resistant to
PID reuse, exact restore semantics, and auditable refusals. It must not trust
the daemon alone to assert that a target PID or unit belongs to an authorized
lane. A valid request can still disrupt the particular active lane it names;
the broker narrows authority, it does not make authorized operations
harmless.

| Threat or property | Current no-broker design | Properly separated future broker |
|---|---|---|
| Rootful Docker administrator / host root | Already has host-administrator authority; broker adds no boundary against them. | Same. They can replace services, access host resources, or bypass the broker. |
| Malformed `ctl` input / ordinary daemon logic error | Request schema, verb dispatch, target checks, and write guards can reject it. | Can add independent policy checks, but also adds another parser and privileged service to test. |
| Arbitrary code execution in cgprofile | Can attempt any operation exposed by its actual mounts/credentials, bypassing its in-process guards. | Bounded to broker's allowed operations only if all direct D-Bus/cgroup/DAMON/privileged bypasses are removed. |
| Arbitrary code execution in broker | Not applicable as a separate component. | High impact: broker compromise exposes the broker's host authority. Smaller scope and code may reduce likelihood, not impact. |
| Multi-user, less-trusted or rootless runner | Not the present trust model; direct host bridge treats cgprofile as trusted infrastructure. | Potentially valuable selective privilege elevation, but the system-manager/cgroup mapping and lease flow need live proof. |

Thus the broker's honest gain is **blast-radius reduction for a compromised
profiler process and independent enforcement of a narrow host API**. It does
not defend against the Docker administrator, eliminate bugs, prevent all
misuse of allowed lane operations, or make the broker immune to compromise.
It costs another privileged service, protocol, identity/lease design,
release/deployment lifecycle, monitoring path, and failure mode. If the
daemon keeps direct writable cgroup or DAMON paths or `privileged: true`, the
broker is bypassable and provides little containment. Conversely, a properly
split design must remove those privileges from cgprofile, not merely add a
broker alongside them.

Revisit D-32 before broadening the deployment to untrusted or multi-tenant
callers, exposing `ctl` to principals without Docker-admin authority, moving
to a rootless runner where narrow host placement is deliberately brokered, or
making containment of cgprofile compromise an explicit security requirement.
That change would require its own design decision, threat model, independent
broker review, negative tests for arbitrary paths/PIDs/units/limits and
replayed leases, live start/restore/kill/failure probes, and proof that
profiling failure still cannot change a test verdict under R-36h. Until then,
retain the no-broker implementation and document the accepted trust boundary
without calling it least privilege.
