# SPEC G — CMRU Agent + Controller

| | |
|---|---|
| **Spec ID** | G |
| **Repo** | `vbpub/cmru` (generic — zero consumer/project knowledge) |
| **Owns** | Seam 2 (`ProjectAdapter` / `DesiredStateBackend` interfaces) + the desired-state / observed-state protocol |
| **Consumes** | Seam 5 (Consul KV path layout + ACL identities + `auto_config`) — defined by **SPEC H** |
| **Depends on** | **SPEC A** installer library (download/verify/atomic-install/rollback); a stock **Consul client agent** running on the host |
| **Implemented-by (downstream)** | `ProjectAdapter` is implemented by **dstdns** in **SPEC F**; Consul KV/ACL provided by **SPEC H** |
| **Status** | Ready to implement. Highest-risk spec in the set — keep it thin, lean on reuse. |

> **Worktree directive (do all work here — never modify `/workspaces/vbpub` directly):**
> ```
> Worktree: create a git worktree for branch `feat/cmru-agent-controller` at
> /tmp/vbpub-cmru-agent-controller and do all work there:
>   git worktree add -b feat/cmru-agent-controller /tmp/vbpub-cmru-agent-controller main
> ```
> Base the branch on the current local `main` of `vbpub` (it carries the unreleased `get.py`
> migration that later specs build on). Run the cmru test suite from inside the worktree.

---

## 1. Goal

Add two generic console entry points to the **existing** cmru distribution:

- **`cmru-agent`** — a *thin* reconciler that runs on each managed host. It pulls **signed
  desired state** from the host's **local** Consul client agent, and drives a project
  adapter (which, for dstdns, calls `ciu up --profile …`). It never decides *what* to run —
  it converges the host to the controller's declared desired state.
- **`cmru-controller`** — an operator/server-side tool that **assigns** desired state
  (maps service profiles → registered hosts), orchestrates **rollout waves** with global
  phase barriers, and gates production behind canary + approval.

Both are generic: they contain **no dstdns topology**. Consul is desired-state backend #1;
the dstdns CIU adapter is project adapter #1.

---

## 2. Critical design principle — reuse the stock Consul client agent

**Do not build membership, transport security, server discovery, gossip, or WAN long-poll.**
Each managed host already runs a standard **Consul client agent** (SPEC H stands it up). That
agent provides, for free:

- cluster membership + server discovery (gossip),
- a **local** HTTP API at `http://127.0.0.1:8500` with **blocking KV queries**, sessions, and
  locks,
- **service registration + health checks** (this *is* "the host registered and reports
  ready/standby"),
- ACL + TLS-to-server, with the node's identity issued via Consul **`auto_config`**.

Therefore the `cmru-agent` reconciler **talks only to `127.0.0.1:8500`** and implements
blocking KV reads with the Python **standard library** (`urllib.request`):

```
GET /v1/kv/<key>?index=<N>&wait=300s        # long-poll; returns header X-Consul-Index
```

This preserves cmru's **zero-dependency** contract (`pyproject.toml` `dependencies = []`):
the agent imports no third-party packages and shells out to external CLIs (`consul`,
`minisign`, `ciu`, `tailscale`, and optionally `cosign`) for everything else.

> **Do NOT import** the dstdns watcher `libs/common/src/common/consul_settings.py`. It is in
> the *consumer* repo (wrong dependency direction), is a separate release unit, and uses
> `aiohttp`. **Mirror its blocking-query pattern** (index tracking, `wait=`, deep-merge of
> KV layers) in stdlib instead. It is a good reference for the loop shape only.

---

## 3. Frozen contracts (Seam 2 + protocol) — implement exactly

### 3.1 Interfaces

```python
class ProjectAdapter:
    def validate(self, desired, installed_release) -> None: ...
    def prepare(self, desired, release_root) -> None: ...     # includes pre-network transport-join
    def apply_step(self, step) -> "StepResult": ...           # e.g. ciu up --profile ...
    def health(self, step) -> "HealthResult": ...
    def rollback(self, previous) -> None: ...

class DesiredStateBackend:
    def enroll(self, node_id, seed) -> "NodeIdentity": ...
    def watch_desired(self, node_id, index, wait) -> tuple["DesiredState", int]: ...
    def acquire_lock(self, node_id, generation) -> "LockHandle": ...
    def publish_observed(self, node_id, state) -> None: ...
```

- `ProjectAdapter` is an **ABC** in cmru; the concrete dstdns adapter is shipped *inside the
  verified dstdns release* and loaded from the installed release entrypoint (SPEC A's
  `entrypoint`, e.g. `scripts/bootstrap.py`). cmru must **never** import a project module
  directly — it loads the adapter from the trusted, signature-verified release root.
- `DesiredStateBackend` is an **ABC**; `ConsulBackend` is the only v1 implementation.
- **Desired state MUST NOT select an arbitrary executable or argv.** `apply_step` dispatches
  only to enumerated adapter actions; there is no shell/command field anywhere in the
  protocol.

### 3.2 Desired-state protocol (data only)

```json
{"schema_version":1,"generation":42,"action":"update",
 "release":{"tag":"<tag>","manifest_url":"...","manifest_sha256":"..."},
 "profiles":["worker-io"],"config_hash":"...","plan_id":"...","step_id":"phase-40.nano1.worker-io"}
```

- Allowed `action`: `install | update | rollback | hold`. **No shell action.**
- `profiles` is the ordered CIU service-profile list the adapter will pass as repeated
  `--profile` flags (Seam 4 / SPEC C). The agent treats it as opaque data.

### 3.3 Observed state (written by the agent)

Reports: `applied_generation`, `release_digest`, `adapter_phase`, `health`
(`healthy|degraded|failed|applying`), `timestamps` (started/finished), `error_class`,
`exit_code`, and a **redacted** human message. Never include secrets/tokens.

### 3.4 Authenticity model (state clearly in the doc)

Trust comes from two independent layers — document both:

1. **Consul ACL write-guard** — only the `cmru-controller` identity may write a node's
   `…/nodes/<node>/desired`; the node agent has read-only on it (SPEC H ACL policies). So a
   host cannot forge its own desired state.
2. **minisign-signed manifest (authenticity root)** — desired state references a manifest by
   `manifest_url` + `manifest_sha256`; the manifest is minisign-signed (SPEC B) and pins
   images by digest. The agent (via the SPEC A installer) verifies sha256 **and** the
   minisign signature against the public key from the enrollment seed **before** install.
3. *(Optional, defense-in-depth)* the controller MAY also write a detached minisign
   signature of the desired-state JSON at `…/nodes/<node>/desired.sig`; if present, the
   agent verifies it. Recommend implementing the verify path but treating layer 2 as the
   authority.

---

## 4. Console entry points

Add to `cmru/pyproject.toml`:

```toml
[project.scripts]
cmru            = "cmru.cli:main"            # existing
cmru-agent      = "cmru.agent.cli:main"      # new
cmru-controller = "cmru.controller.cli:main" # new
```

- `cmru-agent enroll` — register the node with the backend (service registration + write
  initial standby observed state); persist `node_id` + identity references to the state dir.
- `cmru-agent run` — long-running reconcile loop (the daemon).
- `cmru-agent once` — single reconcile pass then exit (for tests / cron fallback).
- `cmru-agent status` — print current observed state + last applied generation.
- `cmru-controller publish|approve|status|hold|rollback` — see §7.

---

## 5. Suggested module layout (cmru/src/cmru/)

```
agent/__init__.py
agent/cli.py             # cmru-agent verb dispatch
agent/reconciler.py      # the reconcile loop (§6)
agent/backend.py         # DesiredStateBackend ABC + dataclasses (NodeIdentity, LockHandle, EnrollmentSeed)
agent/consul_backend.py  # ConsulBackend: urllib calls to 127.0.0.1:8500 (blocking KV, session, lock, register)
agent/adapter.py         # ProjectAdapter ABC + load_adapter(release_root) (import from verified entrypoint)
agent/protocol.py        # DesiredState / ObservedState dataclasses + strict schema validation
agent/selfupdate.py      # self-update handoff (§6.4)
agent/state.py           # state-dir paths, observed.json read/write, locking
controller/__init__.py
controller/cli.py        # cmru-controller verb dispatch
controller/planner.py    # landscape plan → ordered (global phase, host set, profile subset) steps
controller/rollout.py    # publish/approve/hold/rollback against the backend
```

Keep these as **internal modules** so the agent could later be split into its own wheel if
operational evidence justifies it (§8).

---

## 6. Agent reconciliation loop (`agent/reconciler.py`)

State dir: system scope `/var/lib/cmru-agent/`, user scope `$XDG_STATE_HOME/cmru-agent/`.
Files: `node_id`, `identity` (token file path reference), `observed.json`, `current_generation`.

### 6.1 The loop (per iteration)

1. **Long-poll own desired state.** `backend.watch_desired(node_id, index, "300s")` →
   `GET /v1/kv/cmru/landscapes/<ls>/nodes/<node>/desired?index=<N>&wait=300s`. Track
   `X-Consul-Index`; if unchanged, loop. Decode base64 `Value` → JSON.
2. **Verify schema + signature.** Validate against `protocol.py` (reject unknown keys /
   bad enums — fail closed). If a `desired.sig` is present, verify it (minisign). Refuse on
   any failure; publish `error_class=invalid_desired` and keep current state.
3. **Idempotency check.** If `desired.generation <= observed.applied_generation` **and**
   release digest + `config_hash` + `step_id` match observed → **no-op** (do nothing,
   re-arm the watch). Duplicate generations MUST be safe.
4. **Acquire host session/lock.** `backend.acquire_lock(node_id, generation)` — create a
   Consul **session** (`PUT /v1/session/create` with `TTL`, `Behavior:"delete"`,
   `LockDelay`), then `PUT /v1/kv/cmru/landscapes/<ls>/locks/<node>?acquire=<session>`. If
   not acquired, back off and retry.
5. **Ensure release present.** If the release for `desired.release.tag` is not already
   installed, call the **SPEC A installer library** to download + verify
   (sha256 + minisign) + atomically install into `<root>/releases/<tag>`. Never re-implement
   install/rollback here — reuse the installer.
6. **Load + dispatch the adapter action.** `load_adapter(release_root)` then run **only** the
   action enumerated by `desired.action`:
   - `install`/`update` → `adapter.validate()` → `adapter.prepare()` (this runs the
     pre-network **transport-join** for first bring-up, see SPEC F/H) → `adapter.apply_step()`
     for each plan step (e.g. `ciu up --profile core --profile worker-io`).
   - `rollback` → `adapter.rollback(previous)` + installer atomic `current` revert.
   - `hold` → make no changes; just refresh observed/health.
7. **Local health.** `adapter.health(step)`; classify `healthy|degraded|failed`.
8. **Publish observed + release lock.** `backend.publish_observed(node_id, observed)`
   (`PUT /v1/kv/.../observed`), then release the lock + destroy the session.
9. **Re-arm** the watch with the new index.

### 6.2 Failure / outage behavior

- On **Consul outage** (connection refused / 5xx / timeout): keep the current healthy
  state, exponential backoff + retry, **never guess a new target**. Do not tear down running
  services because the control plane is unreachable.
- On **session expiry / agent crash mid-apply**: on restart, re-read observed; if a
  generation was partially applied, the installer's atomic `current` swap means the host is
  either on the old or new release (never a torn state); re-run the adapter action to
  converge. Apply steps must be **idempotent**.
- On **adapter failure**: publish `health=failed` + `error_class`/`exit_code`; do **not**
  advance `applied_generation`. The controller's wave barrier (§7) will stop the rollout.

### 6.3 Service registration (standby)

At `enroll` and on each loop, register/refresh a Consul service `cmru-agent` with a TTL
health check (`PUT /v1/agent/service/register`). A node with no `…/desired` key set yet is
**standby** — the controller sees it via `GET /v1/catalog/service/cmru-agent` and may assign
profiles. This is the "undefined hosts enter ready/standby" behavior.

### 6.4 Self-update (`agent/selfupdate.py`)

When desired state pins a **new cmru version** for the agent itself: stage the new cmru wheel
into a fresh venv under `<root>/venv-<version>`, write a `pending-selfupdate` marker, then
hand off via a small stable **systemd** wrapper — **never overwrite the running interpreter
in place**. Ship a minimal unit template (no systemd units exist in the repos today):

```ini
# packaging/cmru-agent.service  (template, rendered at enroll)
[Unit]
Description=CMRU reconciler agent
After=network-online.target consul.service
[Service]
Type=simple
ExecStart=/opt/dstdns/venv/bin/cmru-agent run --scope system
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

Self-update updates the venv/`current` reference and calls `systemctl restart cmru-agent`
(or exits cleanly and relies on `Restart=always`). Keep it minimal; a rootless/user variant
uses `systemctl --user` or a container restart policy.

---

## 7. Controller (`controller/`)

- **`publish --landscape <ls> --plan <plan.toml>`** — `planner.py` turns the landscape into
  an ordered list of steps `(global phase, host set, local service/profile subset)` and
  writes the plan spec + per-node desired state for the first wave (the canary). Supports
  cross-host ordering: host A phase 1 → host B phase 2 → host A phase 3.
- **Wave barrier**: after writing desired to a wave's nodes, poll their observed state until
  **all required nodes report healthy**; only then advance. Stop the plan on any required-node
  failure. Offline nodes stay pending (do not start dependents early).
- **Canary + approval**: canary/dev waves run automatically; **production waves require
  `cmru-controller approve --plan <id>`** after the canary is healthy.
- **`hold --plan <id>`** — pause; **`status`** — render catalog (registered/standby/assigned)
  + observed; **`rollback --plan <id> [--to <tag>]`** — write a **new desired generation**
  with `action=rollback` (never mutable git reset / tag movement).
- The controller writes only **data** to KV; it never pushes commands. (The landscape UI that
  drives the controller is a separate planned feature — out of scope here.)

---

## 8. Packaging decision

- cmru core stays **stdlib-only**; the agent/controller are **also stdlib-only**
  (`urllib` + `subprocess`), so **no `[agent]` optional-extra is required** for v1. If later
  evidence demands a third-party dependency, gate it behind
  `[project.optional-dependencies] agent = [...]` then — not now.
- Keep `agent/` and `controller/` as clean internal modules with no coupling to release
  tooling, so they can split into a separate `cmru-agent` wheel later without API churn.

---

## 9. Out of scope (owned elsewhere — reference, do not implement)

- Consul **KV path layout**, **ACL identities/policies**, and **`auto_config`** setup → **SPEC H**.
- The concrete **dstdns CIU adapter** (`ProjectAdapter` impl) + transport-join → **SPEC F**.
- The **installer** download/verify/atomic-install/rollback library → **SPEC A** (reuse it).
- The **bundle/manifest/minisign** signing → **SPEC B** (reuse verify).
- The **landscape UI** (profile→host assignment surface) → planned feature/TODO.

---

## 10. Acceptance criteria & tests

Tests run via the cmru pytest suite (`cmru/tests/`). Mock the Consul HTTP API with a local
`http.server` fixture (and, where available, a real `consul agent -dev` integration lane).

- **Enrollment + scoped identity**: `enroll` registers the service and writes standby
  observed; the node uses its provisioned per-node token.
- **Isolation**: with SPEC H policies, host A's token **cannot** read/write host B's
  `nodes/<B>/*` paths (assert 403).
- **Blocking watch**: index tracking works; reconnect after a dropped long-poll; **Consul
  outage** → current state retained, backoff retried, no target guessed.
- **Idempotency**: re-delivering the same generation is a no-op; partially-applied generation
  converges on restart (no torn state, thanks to atomic install).
- **Refusal**: invalid signature / unknown-key schema / disallowed `action` is rejected;
  `error_class=invalid_desired` published.
- **No arbitrary execution**: assert there is no code path that executes a string/argv from
  desired state.
- **Controller**: canary auto-applies; production blocked until `approve`; phase barrier waits
  for all required nodes; a failed wave stops the plan; `hold` pauses; `rollback` emits a new
  generation; cross-host A→B→A ordering honored.
- **Self-update**: staged new wheel + restart handoff; running interpreter never overwritten
  in place; agent comes back on the new version and resumes reconciliation.

---

## 11. Done definition

- `cmru-agent` and `cmru-controller` entry points exist; the reconcile loop, `ConsulBackend`,
  `ProjectAdapter` ABC + loader, protocol validation, and controller rollout are implemented.
- cmru remains zero-dependency; all new code is stdlib + subprocess to external CLIs.
- All §10 tests pass in the cmru suite; SPEC.md gains a section documenting the agent/
  controller contract and the desired/observed protocol.
- The doc's interfaces (§3) match what SPEC F (adapter) and SPEC H (Consul/ACL) consume —
  if you must change a signature, update those specs' references in the same change.
