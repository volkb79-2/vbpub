# nyxloom runtime process model & operability

> Status: design note · captured 2026-07-16 from live dogfood findings.
> Concerns the *running* daemon (container, PID tree, restart/crash behaviour,
> dashboard reachability) and the review merge-gate contract. Distinct from
> `SPEC.md` (the behavioural contract) and `ARCHITECTURE.md` (the module map).

## 1. The process tree (as deployed)

The `nyxloomd` container runs **host-network** with this entrypoint:

```
bash -c "rm -f nyxloomd.pid; exec python -m nyxloom.cli daemon"
                             ^^^^ the daemon REPLACES bash → it is container PID 1
```

Live tree (`docker top nyxloom-prod-nyxloomd`):

```
PID 1  python -m nyxloom.cli daemon          ← the daemon (container PID 1)
 └─ python -m nyxloom.cli daemon (wrapper)    ← one detached wrapper per attempt
     └─ claude -p <handoff> ... --model ...   ← the actual implementation/review agent
         └─ (its gate: docker run tester-unified ...)
```

Key facts:
- **The daemon is PID 1.** The `exec` in the entrypoint means there is no
  init/supervisor above it.
- **Agent wrappers are children of the daemon.** They are launched
  "detached" (own session/process-group) so that, per the daemon's design
  (`daemon.py`: *"Wrappers are detached and keep running across daemon
  restarts … never kill wrappers on shutdown"*), a daemon **process** restart
  can re-adopt them from their pidfiles. All authority is on disk (the
  append-only event log); the residency is an optimization — *"kill -9 on the
  daemon loses nothing."*

## 2. The restart / crash hazard (and the fix)

The detach design assumes the daemon can exit while **something else** keeps the
container (and thus the agents' PID namespace) alive. **That assumption is false
as deployed**, because the daemon *is* PID 1:

- **Planned restart** (`docker restart`, `ciu up` rebuild) tears the container
  down → PID 1 dies → every child agent dies with it.
- **Crash** (unhandled exception, OOM-kill, segfault of the daemon process) →
  PID 1 dies → **same outcome: all in-flight agents are killed.** This is the
  dangerous one: an unrelated daemon bug takes down every running agent.

So today, both a merge-driven code update *and* a daemon crash have the same
blast radius: total agent loss. The recorded state survives (event log), and
agents would be re-dispatched, but in-progress work (and its tokens) is lost.

### Why a signal-based self-reload is NOT sufficient

A `SIGHUP → os.execv(sys.executable, …)` handler reloads the (bind-mounted)
code **in place**, keeping PID 1 and leaving child agents untouched — good for a
**planned** zero-downtime reload. But a **crash never runs the handler**: the
process is already dying. `execv` therefore covers planned reloads only, **not
crash-without-consequence**. The user's instinct is correct.

### Recommended: an init (tini) + a daemon supervisor loop

Make the daemon a **non-PID-1 child**, so its death (planned or crash) does not
end the container:

```
PID 1  tini (or `docker run --init`)         ← reaps zombies, forwards signals; never crashes
 └─ supervisor: `while true; do python -m nyxloom.cli daemon; done`
     └─ the daemon                            ← may crash/restart freely
         └─ agent wrappers                    ← reparent to tini on daemon death; SURVIVE
```

Properties:
- **Crash-without-consequence:** the daemon crashes → its wrapper children
  reparent to tini (PID 1) and keep running → the supervisor respawns the
  daemon → it re-adopts the orphaned wrappers via their pidfiles (the mechanism
  the "detached wrappers survive restarts" design *already* implements but can't
  currently exercise). Agents never die from a daemon fault.
- **Planned code reload:** just let the daemon exit (or `kill` the daemon
  child); the supervisor respawns it on the new bind-mounted code — no container
  teardown, agents untouched. A `SIGHUP→execv` path becomes an optional
  optimization, not a necessity.

**Any reason not to use tini?** Essentially no. tini is ~10 KB, is exactly what
`docker run --init` installs, and is the standard fix for "my app is PID 1 and
shouldn't be." The only real work is the **supervisor loop** + confirming the
daemon's orphan re-adoption path (pidfile scan, the P14 "belt-and-braces"
wrapper.pid check) works when the parent is tini rather than a prior daemon —
which is the same code path, just finally reachable. Net: **tini gives both
crash- and restart-without-consequence; the signal approach gives only the
latter — prefer tini** (optionally + execv for zero-downtime reloads).

## 3. Dashboard reachability & VS Code port-forwarding

The daemon serves a read-only HTTP/SSE surface **loopback-only** at
`min(policy.http_port)` over projects (default **8942**). Because the container
is **host-network**, that binds `127.0.0.1:8942` on the **docker host** (the
`vb` machine). In-container `curl` → HTTP 302 (alive); from the host itself a
sysadmin browses it directly.

**Why VS Code / the devcontainer can't see it:**

| Container | Network | Sees `127.0.0.1:8942`? |
|-----------|---------|------------------------|
| `nyxloomd` | `host` (binds vb-host loopback) | yes (it's the binder) |
| devcontainer `dstdns-devcontainer-vb` | `bridge` (nets: bridge, ciu-test, dstdns-…, ntfy-…, **vbpub-fae1b8**) | **no — different netns** |

They share **no** network namespace, so the devcontainer's `localhost` is not
the host's. VS Code auto-forward (attached to the devcontainer) only sees ports
on the *devcontainer's* loopback — never the host-network daemon. A browser on
the SSH-client machine is two hops removed.

**Options to make VS Code forward it while staying non-LAN ("local only"):**
1. **Put the daemon's HTTP surface on a shared bridge network** the devcontainer
   already joins (e.g. `vbpub-fae1b8-network`) and bind it on that interface
   (not host-loopback). Then it's reachable from the devcontainer at
   `nyxloom-prod-nyxloomd:8942`, VS Code forwards it, and it stays off the LAN
   (bridge is internal to docker). **Trade-off:** any container on that network
   can reach it (vs. only the host loopback today) — acceptable if that network
   is trusted; add a bearer token if not. This is the cleanest fix but is a
   **stack change** (network mode + bind address) and interacts with why the
   daemon is host-network in the first place (host access to sibling ciu
   stacks / docker socket) — needs a deliberate decision.
2. **Keep host-network; sysadmin uses an SSH tunnel** from their machine to the
   vb host's `127.0.0.1:8942` (`ssh -L 8942:127.0.0.1:8942 vbhost`). No daemon
   change; works today; not "VS Code auto".
3. **`docker exec` from the devcontainer** for one-off inspection
   (`docker exec nyxloom-prod-nyxloomd curl 127.0.0.1:8942/…`). Debug-only.

`nyxloom doctor` now prints the URL + the host-network caveat so the port is at
least discoverable (2026-07-16).

## 4. Implications of a richer (React) UI

Today the surface is **server-rendered HTML + SSE** from `render.py`/`daemon.py`
(loopback, read-only, no build step). A React SPA with charts changes the
daemon's contract in a few ways (TLS explicitly out of scope):

- **A real JSON API, not HTML.** The SPA needs stable read endpoints
  (`/api/state`, `/api/events` SSE, `/api/tasks/<id>`) returning typed JSON —
  the same typed fields the notify layer already restricts to (ids/states/counts
  — never raw agent/log prose; the injection-boundary rule extends to the API).
- **Static asset serving.** Either ship a pre-built bundle the daemon serves
  from one directory (no Node in the container — mirror the "never a browser
  engine in the cockpit" rule; build in CI, serve static), or a separate static
  host. Keep the daemon dependency-free.
- **Reachability (see §3).** A browser must reach the surface → this forces the
  §3 decision (shared bridge network or tunnel). Charts don't change that; the
  network model does.
- **Writes stay narrow.** Any interactive control (the intake tab P30, decision
  replies) is a *guarded, loopback/bridge-only, input-untrusted* POST — one
  sanctioned write path, never shell, redacted. More UI ≠ more daemon authority.
- **No new state authority.** The event log stays the source of truth; the SPA
  is a pure projection. Charts are computed from the same events the dashboard
  already has — no schema change required, just JSON exposure.

Net: a React UI is mostly **additive** (a JSON/SSE API + static serving) and does
**not** require new daemon authority — but it *does* force the §3 reachability
decision and must honour the typed-fields/injection-boundary discipline.

## 5. The review merge-gate: a spec gap, not just a bug

Live incident (2026-07-16): a reviewer correctly **REJECTED** P26 (architectural
defect) in its report but exited its process cleanly; the wrapper recorded
`result: done`; the daemon's `FRONTIER_REVIEW` branch maps `done → MERGE_READY`;
the task was rubber-stamped and nearly merged.

Is this a spec gap or an implementation bug? **Both — rooted in the spec.**
`SPEC.md §7` asserts an *"independent merge-gating review"* and §8 treats
rejections as first-class (counting "≥2 review rejections in an area"), but it
**never specifies the verdict signalling mechanism** (how a reviewer communicates
approve/reject) nor a **fail-safe posture**. Left open, the implementation chose
the worst default: derive the verdict from **process exit** (a proxy that means
"the reviewer finished", not "the reviewer approved") and **fail open** (ambiguity
→ approve). A gate that cannot reliably distinguish approve from reject, and that
fails open, is not a gate.

**The contract this establishes (implemented by P33):**
- The verdict is an **explicit, machine-readable** signal in the durable
  artifact (`<task>-REVIEW.md`: `VERDICT: APPROVED|REJECTED — <reason>`), not a
  process-exit proxy.
- **Fail safe:** a missing, unreadable, or ambiguous verdict → **REVIEW_REJECTED**,
  never MERGE_READY. A forgotten signal can never approve-by-accident.
- Belt-and-braces: the existing `BLOCKED: rejected` final-line path is kept as a
  second signal.

This is a general principle for the factory: **every gate derived from an agent's
output must fail safe** (models are unreliable at mechanical self-signalling —
the same lesson as AUTHORING.md's mechanical-BLOCKED-escape-hatch). SPEC §7
should be amended to state the verdict mechanism + the fail-safe rule explicitly.

## Open follow-ups (backlog)

- tini + supervisor for crash/restart-without-consequence (§2). Daemon-core +
  stack change.
- Dashboard on a shared bridge network for VS Code forwarding (§3/§4). Stack
  change + security decision (token?).
- JSON/SSE API + static bundle serving for a React UI (§4).
- P33 (verdict fail-safe, §5) — in flight; amend SPEC §7 to match.
