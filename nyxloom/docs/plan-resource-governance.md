# Plan: resource governance — slices, agent containers, host separation

Status: active · 2026-07-27 · operator interview this session

How nyxloom's workloads are placed and bounded on a mixed-use host, and how
that stays separate from the operator's own interactive work.

## Verified facts (measured this session, not assumed)

| Fact | Evidence |
|---|---|
| A container does **not** inherit its caller's cgroup | `dockerd` is the parent process. Placement requires `--cgroup-parent`, compose `cgroup_parent:`, or the daemon-wide default. `besteffort.slice`'s own comment says "ciu governance injects it" for exactly this reason. |
| **A missing/typo'd slice runs the container UNBOUNDED** | `docker run --cgroup-parent=nyxloom-does-not-exist.slice` → accepted silently; systemd auto-creates a transient slice with no limits. **Fail-open.** |
| Weights bind only under contention | cgroup v2 `cpu.weight`/`io.weight` are proportional shares. An idle host lets a weight-1 cgroup use 100% of all cores. Hard ceilings need `cpu.max`/`io.max`. |
| `memory.max` / `memory.min` always bind | Unlike weights. `memory.min` is a protected reservation reclaim will not take — the tool for guaranteeing daemon survival. |
| Host slice weights today | `interactive.slice` CPUWeight **200** (MemoryLow=2G, Max=7G) · `dev-workloads.slice` **50** (Max=14G) · `besteffort.slice` **20** (Max=12G, MemorySwapMax=24G, IO caps, systemd-oomd) |
| Agent sessions are **processes**, not containers | `wrapper.launch_detached` → `os.fork()`. `--cgroup-parent` is a no-op for them today. This is what motivates containerising them (below). |

## D-G1 — Slice hierarchy: one leaf per workload instance

systemd derives the tree from dash-separated **names** — `nyxloom-gates-<id>.slice`
is automatically nested under `nyxloom-gates.slice` under `nyxloom.slice`. No
parent declarations, no ordering directives.

```
nyxloom.slice                     MemoryMax = whole-factory ceiling
├── nyxloom-daemon.slice          MemoryMin = <hot set>   ← protected reservation
├── nyxloom-agents.slice          MemoryMax = collective cap on ALL agents
│   ├── nyxloom-agents-<task>.slice     one per agent session
│   └── ...
└── nyxloom-gates.slice           CPUWeight low (batch)
    ├── nyxloom-gates-<task>.slice      one per gate run
    └── ...
```

**Why a leaf per instance** (operator decision): a single runaway agent is
bounded by its own leaf instead of consuming the whole agent budget and
starving its siblings. The intermediate parents still cap the aggregate, so
"N agents each misbehaving" has one collective ceiling *and* per-instance
fairness.

**Why the daemon gets `memory.min`, not just a weight:** the guarantee wanted
is "the daemon keeps running even when every agent misbehaves". Weights only
arbitrate CPU under contention; they do nothing for memory reclaim. A
protected reservation is the only mechanism that survives sibling pressure.

**A conservative floor ships now; measurement refines it later.** (Corrected
2026-07-27 — an earlier draft said "do not ship a guessed `MemoryMin`", which
was wrong. The risk is not symmetric:

- **too LOW → harmless.** You protect less than you could, but strictly more
  than nothing.
- **too HIGH → harmful.** `memory.min` is a *reservation*; a value above real
  usage permanently sterilises that memory from every sibling under pressure.

So only *optimistic* guesses are dangerous. `MemoryMin=128M` sits at or below
a Python daemon's realistic resident set and is negligible against a multi-GB
host budget, so it cannot meaningfully sterilise anything — and shipping
nothing while waiting for a measurement window would leave the daemon
completely unprotected in the meantime.)

**Still to refine:** run the daemon idle, then under a real dispatch wave, read
`memory.current` + `memory.stat` anon at both points, and raise `MemoryMin` to
the observed steady-state working set.

## D-G8 — Catching containers that name no slice, or a wrong one

Two distinct failure modes, two mechanisms:

| Failure | Mechanism |
|---|---|
| Container names **no** parent | `daemon.json` `"cgroup-parent": "dev-workloads.slice"` — the daemon-wide default (D-G7) |
| Container names a **wrong/typo'd** parent | the default does NOT help: an explicit value wins, and a non-existent slice is auto-created transiently with no limits |

For the second, the host-level catch-all is a **systemd drop-in on the scope
name prefix**. Every container docker starts gets a transient unit named
`docker-<id>.scope`, and systemd applies drop-ins from truncated-name
directories, so:

```
/etc/systemd/system/docker-.scope.d/50-default-limits.conf
[Scope]
MemoryMax=<host-wide backstop>
MemorySwapMax=<generous>
```

applies to **every** container scope regardless of which parent slice it named
— including a typo'd one. That is a genuine floor under the whole estate.

⚠️ **Needs one verification step on the host** (requires root, so it was not
tested from the devcontainer): confirm the drop-in is actually picked up for
transient docker scopes, e.g. `systemctl show docker-<id>.scope -p MemoryMax`
on a running container after installing it. Do not assume — the whole reason
this section exists is that the obvious-looking mechanism (naming a slice)
fails open.

Rejected: a host service that periodically scans for cgroups outside known
slices and retro-applies limits. Reactive, races container startup, and adds a
daemon whose own failure mode is silent.

## D-G0 — LIVE FINDING: the daemon is already configured for a slice that does not exist

`nyxloomd/ciu.toml` has declared, since before this plan existed:

```toml
[nyxloomd.runtime]
cgroup_parent = "nyxloom.slice"
```

**`nyxloom.slice` has never existed on this host.** The installed units are
`besteffort`, `dev-workloads`, `interactive`, `soulmask-*`, `wings-*` — no
`nyxloom*` anything.

Per the fail-open behaviour measured in the facts table above, that means
systemd has been auto-creating a transient `nyxloom.slice` with **no limits**,
every time the daemon started. The daemon has therefore been running
**completely unbounded** while its config file made it look correctly placed —
no error, no warning, no log line, for as long as this setting has existed.

This is the single best argument for the whole design:

- It is not a hypothetical hazard. It is **live, in production, right now**.
- Installing the slice templates (`infra/slices/`) is therefore **not a new
  feature** — it is completing a configuration that already exists and has been
  silently ineffective.
- `doctor`'s existence check (D-G2) is what turns "silently unbounded" into a
  loud failure. Without it, the next such typo is equally invisible.
- It is also a textbook instance of canonical **L18**: a degraded outcome
  ("unbounded transient slice") rendering identically to the healthy one
  ("placed in the slice I asked for"), one layer down in the infrastructure.

Note the placement key lives in `[<stack>.runtime]`, **not** in a
`[governance]` table — governance is a separate ciu mechanism (S15) that
injects `cgroup_parent`/`mem_limit`/`mem_reservation`/`blkio_config` and only
fills keys the author did not set. nyxloom uses the runtime key; dstdns uses
governance. Both are valid; do not assume one from the other. (This document
previously asserted nyxloomd had no placement at all — that was wrong, from
grepping `ciu.global.toml` and the compose files but not the stack's own
`ciu.toml`.)

**Two ciu defaults that would bite nyxloom if governance is ever enabled here:**

- `cgroup_parent` defaults to `besteffort.slice` (CPUWeight 20). Enabling
  governance without setting it would put the *dispatcher* in the most
  deferential slice on the host — the opposite of what a latency-sensitive
  daemon needs.
- `device` defaults to `""` = autodetect via `findmnt --target /var/lib/docker`,
  which **silently fails** in this devcontainer (docker-outside-of-docker has no
  such mount), so blkio caps would be skipped. dstdns sets `/dev/vda`
  explicitly for exactly this reason.

Also: ciu's `mem_reservation` maps to compose's soft `memory.low`, **not**
`memory.min`. It does not replace the slice unit's `MemoryMin` — they are
complementary.

## D-G2 — Who may do what (the root boundary)

nyxloom **can** set cgroup values: `--cgroup-parent`, `--memory`,
`--memory-swap`, `--cpu-weight`, `--device-*-bps` are all Docker API calls,
and the daemon already holds the Docker socket.

nyxloom **cannot** install slices: writing `/etc/systemd/system/*.slice` +
`systemctl daemon-reload` needs host root, which the daemon does not and must
not have.

Therefore: **ship templates, verify via `doctor`, install via an explicit
operator verb.** Never auto-install — it would also clobber the runtime IO
caps `setup-cgroups.sh` applies from measured `io-baseline.env` values.

**`doctor` verification is MANDATORY, not cosmetic** — see the fail-open fact
above. A typo in a slice name does not error; it silently removes every limit.
`doctor` must assert each configured slice exists as a real unit, and fail
closed when it does not. This is the same "error path aliasing a benign
result" class as canonical **L18**, one layer down in the infrastructure.

## D-G3 — Placement follows the DECLARING config, not the image

Answering the dstdns question directly: a project whose gate already declares
its own placement (dstdns → `ciu` governance → `besteffort.slice`) keeps that
placement when nyxloom runs it. nyxloom does not and should not override it —
canonical **L16**: nyxloom requires an *interface*, never mandates infra.

So **yes: the same image can run in different slices simultaneously**, and that
is correct. A `tester-unified` container spawned by `ciu` lands in
`besteffort.slice`; one spawned by nyxloom for a project that opted into
nyxloom's placement lands under `nyxloom-gates.slice`. Placement is a property
of *who declared the run*, not of the image.

The mechanism is a `{cgroup_parent}` placeholder in the gate argv, mirroring
the existing `{worktree}` substitution:

```toml
[governance]
enabled = true
slice_gate   = "nyxloom-gates.slice"
slice_agent  = "nyxloom-agents.slice"
slice_daemon = "nyxloom-daemon.slice"

[gates.tester-unified]
argv = ["bash","-c","docker run --rm {cgroup_parent} -v ... "]
```

`{cgroup_parent}` substitutes `--cgroup-parent=<slice>` when governance is on,
and the **empty string** when it is off — so a project that never opts in gets
byte-identical behaviour, and a project with its own governance simply omits
the placeholder.

## D-G4 — Per-project resource requirements vs host hardware

The hard part, stated by the operator: a project's needs come from its own
code, but the safe absolute numbers come from the host.

**Resolution: absolute limits are HOST-owned and live in the slice units; the
project declares nothing absolute.** A project moving to a different host then
needs no change at all. This is already how the estate works — dstdns's ciu
governance sets a per-container `mem_limit` as a first-pass ceiling, and its
own comment records that `besteffort.slice`'s `MemoryMax` "is the real
host-safety backstop regardless of any per-container value (cgroup v2 accounts
child usage against the parent slice)."

Per-container values remain an optional refinement for a project that has
measured itself; they never substitute for the slice ceiling.

Target shape for gate leaves (operator guidance): cap RAM, allow generous
swap, cap read/write IOPS at **60–80% of measured device ceilings** (baselines
exist from the devcontainer mdt installation — reuse `io-baseline.env` rather
than re-measuring).

## D-G5 — Agent CLI sessions run in containers

Today they are forked processes, so no cgroup placement is possible and the
sandbox is whatever the CLI implements. Containerising them gives:

- **cgroup enforcement** — a real leaf slice per session (D-G1).
- **isolation** — mount only the dedicated worktree.
- **relaxed in-container permissions** — the container *is* the boundary, so
  the CLI no longer needs its own restrictive sandbox. This directly fixes the
  failure hit this session: codex emitted `BLOCKED` because its sandbox saw the
  worktree's `.git` as read-only, so it could not commit or gate its own work.
- **session reuse across worktrees** — session state persists in a named
  volume, not in the worktree, so a resumed session can attach to a different
  worktree later.

One image carries all four CLIs (`claude`, `codex`, `reasonix`, `opencode`),
reused per session. See `infra/agent-cli/Dockerfile`.

**Auth is an install-time step.** Subscription-backed CLIs (claude, codex)
need an interactive login once; the resulting credentials live in the
persistent volume so every later session inherits them. This belongs in the
local install routine, not in any dispatch path.

## D-G6 — Interactive vs nyxloom-controlled: different slices, different shapes

| Workload | Containerised? | Slice |
|---|---|---|
| Operator's interactive CLI session | **No** — plain process in the devcontainer | `interactive.slice` (CPUWeight 200) |
| nyxloom-dispatched agent session | Yes | `nyxloom-agents-<task>.slice` |
| Any test/gate run a CLI shells out to | Yes — project-provided | `nyxloom-gates-<task>.slice`, or the project's own |

The operator's own session must **never** land in a `nyxloom-*` slice. This
falls out for free: those slices are only ever reached by something that names
them explicitly.

## D-G7 — Cross-CLI default placement (`daemon.json`)

For containers started by tools nobody wired up (ad-hoc `docker run`/`build`,
new CLIs), set a daemon-wide default:

```jsonc
// /etc/docker/daemon.json
{ "cgroup-parent": "dev-workloads.slice" }
```

**This sets placement only — never limits.** Memory/swap/IOPS still require
explicit per-container flags or compose. The two layers are complementary, not
alternatives: the default is the safety net for the unconfigured; explicit
governance supplies limits for known workload classes.

Requires a `dockerd` restart (restarts running containers) — schedule it.

Rejected: a `docker` shim earlier on `PATH`. It gives finer per-role control
without touching dockerd, but it is invisible machinery and silently misses
any tool that talks to the Docker socket directly instead of exec'ing the
`docker` binary.

## Sequencing

```
G1  slice unit templates + `doctor` existence check (fail-closed) + install verb
G2  [governance] config section + {cgroup_parent} placeholder in gate argv
G3  agent-CLI image + per-session container dispatch + state volume  [biggest]
G4  daemon memory measurement window -> set MemoryMin
G5  daemon.json default placement (needs a dockerd restart window)
```

G1 and G2 are independent of G3 and can land first. G4 gates nothing but must
precede any `MemoryMin` value being committed.

## Explicitly not doing

- Auto-installing slice units from the daemon (needs root; would clobber
  `setup-cgroups.sh`'s measured runtime IO caps).
- Absolute resource numbers inside project config (host-owned, see D-G4).
- A `docker` PATH shim (see D-G7).
