# Attaching cgprofile to a gate

How to profile a gate, build, or validation run in any repo on this host, and
how to make its phases legible in the report. Nothing here requires changing
the gate itself — the wrapper form works on a gate that knows nothing about
this tool. Phase marks are the optional upgrade.

There is deliberately **no gate integration code** in this package: no exit
codes to interpret, no policy file, no CI hooks. `cgprofile` observes and
reports; whether a run was acceptable stays the calling gate's decision.

---

## 0. Once per host

```bash
pip install 'cgprofile[report]'   # cgprofilectl; the report extra renders reports locally
cgprofilectl doctor                      # socket, group, versions, observer dir, report extra
```

`doctor` names what this process can reach and the exact fix for what it cannot
(for example the group that owns the daemon's socket). `cgprofilectl run` and
`cgprofilectl mark` need no daemon; every other verb talks to the host daemon's socket
(`--socket`, `$CGPCTL_SOCKET`, else
`/run/cgprofile/control/cgprofile.sock`).

Two ways to profile, one CLI:

| Mode | Verb | Needs | Result |
|---|---|---|---|
| Direct | `cgprofilectl run -- CMD` | cgroupfs visible to the caller; no daemon | a run directory with the report inputs |
| Daemon | `cgprofilectl start --target T` ... `cgprofilectl stop ID` | access to the daemon's socket | a stored session, rendered on demand with `cgprofilectl report ID` |

Direct mode profiles the wrapped command in the caller's own cgroup subtree.
Everything that observes something else (a container, a cgroup, a slice), places
a lane, watches liveness or keeps results beyond the run is a daemon session.

---

## 1. Wrap the gate (no gate changes)

The simplest useful form. Profile the gate locally (direct mode, needs the host
cgroup view):

```bash
cgprofilectl run --phase gate --propagate-exit \
  -- ../../shared-ramdisk-depot-manager/tools/gate.sh
```

- `cgprofilectl run` profiles the wrapped command itself (target `self@follow`: the
  command and the containers it spawns afterwards). To profile another target
  or to watch neighbours, use a daemon session (`cgprofilectl start --target ...`,
  section 2).
- With `--propagate-exit` the wrapped command's exit code becomes `cgprofilectl`'s exit
  code, so this drops into an existing script without changing its semantics;
  without it `cgprofilectl run` exits 0 and prints the run directory and the command's
  status.

Where the estate's gates run (a gate tier slice, `dev-gates.slice` by default):

| repo | gate | lands in |
|---|---|---|
| `shared-ramdisk-depot-manager` | `tools/gate.sh` | `$CGROUP_PARENT_DEV_GATES` |
| `cmru` | `src/cmru/tester_gate.py` | `CMRU_TESTER_CGROUP_PARENT` → gates tier |
| `ciu` | `src/ciu/governance.py` | `[<root>.governance].cgroup_parent` → same |

SRDM and CMRU gate launchers resolve to `dev-gates.slice`, so
`cgprofilectl start --target slice:dev-gates.slice` covers that gate workload. CIU's
governance value remains the background tier for long-running application
stacks. A stack started deliberately by a gate should be profiled separately
under `dev-background.slice` when needed.

---

## 2. Attach to something already running

```bash
cgprofilectl start --target container:my-service          # prints the session id
cgprofilectl watch s-20261104T101500Z-9f01                 # stream readings until it ends
cgprofilectl stop s-20261104T101500Z-9f01                  # finalize; prints the summary
cgprofilectl report s-20261104T101500Z-9f01                # export from the daemon and render locally
```

A daemon session is bounded by `stop` (or the daemon's stall policy:
`--idle-bound`, `--ceiling`, `--on-stall`). `cgprofilectl sessions` lists live and
stored sessions, `cgprofilectl show ID` prints the live counters or the stored summary.
`cgprofilectl start --dry-run --target T` resolves a target and prints its effective
limits without starting anything. Targets are `containerid:<64hex>`,
`container:NAME` (resolved client-side with `docker inspect` when docker is
reachable), `cgroup:/abs/path` and `slice:NAME.slice`. `sessions`, `show`,
`prune` and `config` need a daemon speaking ctl contract 2; against an older
daemon they exit 3 with the cause `contract-unsupported`.

---

## 3. Phase marks — the optional upgrade

Marks are what turn a flat timeline into "startup / warm-up / job A / idle".
`cgprofilectl run` exports `CGPROFILE_RUN_DIR` (the run directory; the mark channel
lives there) into the wrapped command's environment and puts the directory of
`cgprofilectl` first on its `PATH`.

So a gate that wants to label its own phases does:

```bash
cgprofilectl mark "restoring fixture db"
cgprofilectl mark "running suite"
cgprofilectl mark "teardown complete" --kind event
```

`--kind phase` (the default) opens a new phase and closes the previous one.
`--kind event` is a point annotation that does not change the phase.

Guard it so the gate still runs unprofiled:

```bash
mark() { [ -n "${CGPROFILE_RUN_DIR:-}" ] && cgprofilectl mark "$@" || true; }
mark "building image"
```

`cgprofilectl mark` needs no daemon and no host cgroup tree — it appends one line to
a file. That is why it works from inside a gate container.

### Marking from inside a container

The mark channel is a file in the run directory, so a container only needs that
directory bind-mounted and `cgprofilectl` installed (it needs nothing else for `mark`):

```bash
docker run --rm \
  --cgroup-parent="$CGROUP_PARENT_DEV_GATES" \
  -v "$CGPROFILE_RUN_DIR:$CGPROFILE_RUN_DIR" \
  -e CGPROFILE_RUN_DIR \
  my-gate-image \
  sh -c 'cgprofilectl mark "suite start"; run-the-suite'   # cgprofilectl installed in my-gate-image
```

Appends are a single `write()` under `O_APPEND`, so several containers marking
at once interleave cleanly rather than corrupting each other.

---

## 4. Reading the report

```
runs/run-YYYYmmdd-HHMMSS-xxxxxxxx/
  report.html    interactive — zoom, pan, hover; open it in a browser
  report.md      static twin with PNGs, for pasting into a ledger or PR
  samples.jsonl  raw data, if you want to do your own analysis
  events.jsonl   what the profiler thought was worth flagging
```

The interactive report is one self-contained file and needs no server and no
network. plotly.js alone is 4.7 MiB of that; a measured 20-second capture of a
slice with 29 cgroups came to 6.9 MiB total. `cgprofilectl report --md-only` writes
the static twin without the interactive file when size matters.

Read it in this order:

1. **The effective-limits table.** This is usually the surprise. It shows both
   what the cgroup declares and what its ancestors actually impose — "your
   container says `memory.max=12G` but `dev-interactive.slice` says 8G" is the
   sort of thing that explains a whole class of mystery.
2. **The phase bands.** Line up the memory/IO shape against what the workload
   was doing.
3. **The events.** Every one carries the numbers that triggered it.
4. **The proposals.** Each carries its evidence and a confidence of `observed`,
   `inferred`, or `speculative`. Treat `speculative` as a question, not a
   recommendation.

---

## 5. Testing a limit without editing host units

The client never writes cgroup limits. To A/B a lane under a tighter ceiling,
start its session with placement: the daemon creates a transient scope under
its verified gates slice, moves the lane's processes into a leaf and sets the
leaf's controls, then restores everything at `stop`.

```bash
cgprofilectl start --target "containerid:$CONTAINER_ID" --scope container-shared \
  --token "$TOKEN" --place --memory-max 2147483648 --cpu-weight 50
```

The response reports the values the kernel accepted, not the values requested.
A request above the gates slice's own ceiling is refused as
`place-refused:over-slice`. See section 9 for the limits of leaf accounting. To
observe a neighbouring slice or cgroup next to the subject, start a second
read-only session with `--target slice:NAME.slice` or `--target cgroup:/path`;
observe targets are never placed and never killed.

---

## 6. DAMON

`cgprofilectl start --damon on|off` adds a working-set hot/warm/cold breakdown
alongside the counters; omitting it uses the daemon's `damon_default`. The
daemon owns a shared DAMON pool that supports concurrent sessions, and
`cgprofilectl host` reports whether DAMON is available. DAMON is optional evidence:
if it cannot start, the session and the profiled workload continue without the
series. `cgprofilectl run` (direct mode) samples the counters only.

---

## 7. Cost

The profiler is meant to be run on a host that is already under pressure, so
its own footprint is part of the design:

- Direct-mode sampling (`cgprofilectl run`) is adaptive: 250 ms while anything is
  moving, backing off to 2 s when quiet. **Read that literally: it backs off
  only when *nothing* in the sampled set is moving.** A tier that also holds a
  large service stack never goes quiet. Point the target at what you actually
  care about; a narrower target is both cheaper and easier to read.
- Daemon sessions sample at the daemon's `interval` (default 1 s, a runtime-
  tunable key; `start --interval` overrides it per session, clamped to
  [0.25, 30] s).
- Data volume follows the cgroup count: a capture measured at 89 KiB per sample
  across 29 cgroups. Samples are gzipped on the way to disk (5.9x measured),
  which turns a ~600 MB half-hour run into ~100 MB, still a real number to
  budget for on a small disk. The daemon bounds its store by retention
  (`keep_sessions`, `keep_days`; `cgprofilectl prune`).
- The collector and the daemon are standard-library only and hold no series in
  memory: every sample is appended and forgotten.
- Analysis and rendering happen **after** the run, in the caller's process
  (`cgprofilectl report`), never in the daemon and never during profiling.
- Nothing is installed, downloaded, or built at run time. Without the `report`
  extra collection still works and only the report step complains.

---

## 8. Profiling a run-gate lane

This is the daemon mode RG-55 added: a long-lived, privileged native service
(`cgprofiled`, see the README's "Product overview" and
`docs/NATIVE-DEPLOYMENT.md`) that run-gate talks to over a Unix socket, per
lane invocation, instead of spawning a collector each time. `RG55-INTERFACE-CONTRACT.md` is the full
wire contract; this section is the short version of how a lane gets found.

**By container id + token (the normal case).** Before starting a lane
container/exec, run-gate generates `token = secrets.token_hex(16)` and
exports it as `RUN_GATE_PROFILE_SESSION=<token>` into the lane process,
then resolves the container id with `docker inspect --format '{{.Id}}'`
and calls:

```bash
cgprofilectl start \
  --target "containerid:$CONTAINER_ID" \
  --scope container \
  --token "$TOKEN" \
  --damon on \
  --meta '{"lane":"gate","project":"...","worktree":"...","commit":null,"run_gate_revision":0,"kind":"command","expected":null}' \
  --json
```

`--scope container-shared` is the exec-mode twin for a long-lived container.
The token identifies a PID subtree (walked from `/proc/<pid>/environ`,
re-discovered every 2 s) for target counts and DAMON. Memory and CPU still
come from the shared cgroup; their deltas can include unrelated concurrent
work. `memory.current` is baseline-subtracted because `memory.peak` there is
lifetime, not per-lane. `start` is idempotent by
`(container id, token)`: a retried/duplicate call returns the same live
session (`"reused": true`) rather than starting a second one.

**By session id.** The `start` response's `"session"` field
(`s-<timestamp>-<4 hex>`) is what every later call names —
`cgprofilectl show <session>`, `cgprofilectl stop <session>` (run in the same `finally` as
container teardown, before `docker rm -f`), `cgprofilectl report <session>` for a
human afterwards. `stop` is idempotent too: a second call on an
already-finished session returns the same stored summary with
`"already_stopped": true`, never an error. A session belongs to the uid that
started it; stopping another uid's session is root-only.

**When the daemon is unavailable.** A connection failure (exit 3, with a cause
tag such as `socket-missing` or `socket-permission`), a timeout, or an
unparsable answer means "profiling unavailable for this call". run-gate's own
basic fallback (`method: "basic"`, sampling the lane container's own cgroup
directly, no daemon involved) takes over silently; see the contract §4.3.
`cgprofilectl doctor` names the cause and the fix. Direct mode (`cgprofilectl run`,
sections 1 and 3) never talks to the daemon.

## 9. Liveness watch and placement, from a consumer's view (D-20/D-27/D-31, §8.2-§8.4)

Two OPTIONAL, independent `start` upgrades sit on top of section 8's basic
attach — neither is required to get a Summary, and neither changes what
`stop`/`report` return when unused.

**Liveness policy** adds a daemon-side stall observation (the motivation
RG-55 exists for — see CP-8): pass `--progress-stream`, `--idle-bound`,
`--ceiling` and/or `--on-stall kill|report` on `start`. The daemon outlives
the caller's own process, so its observation survives a caller crash — the
one thing an in-process watcher cannot do. A consumer that wants to observe the
judgement live (rather than poll `cgprofilectl show`) holds ONE `cgprofilectl watch
<session>` open per lane — the one verb that streams more than one
response per connection (`docs/PROTOCOL.md` §1's documented exception to
"one request per connection, then close"). Three line shapes only:
`reading` (informational, every `--watch-interval` seconds), `verdict`
(only on a state change — this is what a consumer acts on:
`verdict == "killed"` means the daemon successfully wrote `cgroup.kill` to
the authorized boundary; `verdict == "reported"` is a warning line, nothing
was killed, and the lane remains the caller's to manage), `end` (exactly one,
last). An unparsable or unenforceable requested policy is `bad-policy` —
`start` refuses and no session exists, so a typo or missing safe boundary is
caught before the lane runs, not after.

**Placement** (`--place [--memory-high] [--memory-max] [--cpu-weight]`)
asks systemd for an `rg-profile-<token>.scope` directly beneath the verified
gates slice, then puts the lane's pid subtree in cgprofile's own child leaf
(`<scope>/rg-<token>`) instead of sharing whatever cgroup the lane
container/exec session already had. The daemon reads the scope path back from
systemd; the slice and scope remain systemd-owned, while cgprofile owns only
the leaf. For `scope=container-shared`, this is
the prerequisite for `--on-stall kill` to use `cgroup.kill` atomically; the
daemon never walks the resolved PID list to send signals. For `scope=container`,
the exact target container cgroup is the kill boundary when its identity is
proven beneath the verified gates slice, so no extra lane leaf is needed. At
enforcement time, `cgroup.events` must also report `populated 1` for that exact
boundary; missing, malformed, or empty population evidence is reported rather
than certified as a kill.
From a consumer's view placement is optional: an ordinary placement refusal
(`place-refused:*` in `placement.error`, `leaf: null`) still leaves a normal
unplaced session. If the same request explicitly requires a shared-scope
kill, however, refusal means `bad-policy` and no live session, since there is
no safe enforcement boundary. `applied` in the
response is what the KERNEL reports back after the write, never an echo
of what was asked for — if a consumer needs to know whether a cap actually
took, that is the field to read, not its own `--memory-high` argument.
Leaf memory counters and limits describe cgroup charges, not total process
RSS: pages faulted before placement keep their prior charge and are not
transferred when a process moves. `memory.max` is therefore not a hard cap on
all memory already resident in a shared lane; see `docs/CONSUMERS.md` for the
adoption implication.
