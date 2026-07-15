# CIU-DEPLOY — Multi-Stack Orchestrator Guide

`ciu-deploy` sequences multiple stacks across deployment phases, driven by
`[deploy.phases.*]` and `[deploy.profiles.*]` in `ciu.global.toml`. Each stack
is started by delegating to `ciu`; `ciu-deploy` does no rendering itself.
Normative contract: [SPEC.md](SPEC.md).

---

## Quick Start

```bash
# Bootstrap workspace (once per machine)
ciu-deploy --generate-env
source ciu.env

# Render all stack TOMLs (fresh workspace preflight)
ciu-deploy --render-toml

# Deploy all phases (default: no --profile = all enabled phases)
ciu-deploy --deploy

# Deploy a specific host profile
ciu-deploy --profile core_infra --deploy

# Full restart
ciu-deploy --stop --clean --deploy

# Build images then deploy
ciu-deploy --build --deploy

# Health check after deploy
ciu-deploy --healthcheck
```

---

## Actions Table

Actions execute in the order given on the CLI. When no actions are specified,
`--deploy` is the default.

| Action | What it does | Exit on failure |
|---|---|---|
| `--render-toml` | Calls `ciu --render-toml` for each selected stack | Stops remaining stacks in that phase and all later phases [S7.3] |
| `--stop` | Stops containers by anchored label filter [S7.8]; preserves volumes | Logs, continues |
| `--clean` | `compose down -v` + removes `vol-*` dirs + rendered files | Logs, continues |
| `--build` | `docker buildx bake all --load` | Stops deploy |
| `--build-no-cache` | Same, with `--no-cache` | Stops deploy |
| `--deploy` | Starts stacks in phase/numeric order via `ciu` [S7.1] | Phase failed: skips rest of phase + later phases; exit 1 [S7.3] |
| `--healthcheck` | Polls health gate after deploy [S7.7] | exit 1 if gate does not pass |
| `--print-context` | Prints redacted global config JSON | — |

`--ignore-errors` continues on any phase failure but final exit code is still
1 [S7.3]. `--phases 1,2` restricts execution to named phase numbers.

---

## Host Profiles vs Compose Profiles [S7.4, S7.5, S7.5a]

These are two **distinct concepts** and must not be confused:

| Concept | Configured in | Selects | CLI / env |
|---|---|---|---|
| **Host profile** | `[deploy.profiles.<name>]` | Which **stacks** run on this host | `--profile <name>` or `CIU_HOST_PROFILE` in `ciu.env` |
| **Compose profile** | `compose_profiles = [...]` under a host profile entry | Which **services** inside a stack are activated | Sets `COMPOSE_PROFILES` env for that stack |

Host profiles from `test-repo/ciu.global.defaults.toml.j2`:

```toml
# Host A: the shared backbone
[deploy.profiles.core_infra]
phases = ["phase_1", "phase_2"]

# Host B: the app tier — uses topology_overrides to reach Host A's Vault [S7.4]
[deploy.profiles.workers]
phases = ["phase_3"]
[deploy.profiles.workers.topology_overrides.services.vault]
internal_host = "host-a.tailnet.example"   # external address of Host A

# Single-host convenience
[deploy.profiles.all]
phases = ["phase_1", "phase_2", "phase_3"]
```

A host profile with compose profiles (illustrative):

```toml
[deploy.profiles.staging]
phases = ["phase_2", "phase_3"]
compose_profiles = ["monitoring", "debug"]   # → COMPOSE_PROFILES=monitoring,debug
```

`[deploy.groups]` and `--groups` do **not** exist in v2. The validator rejects
`[deploy.groups]` with a pointer to profiles [S7.5].

---

## Multi-Host Workflow [S7.5a]

Each host carries a clone of the project, its own `ciu.env` (machine identity),
and sets `CIU_HOST_PROFILE` in `ciu.env` to control which stacks run.

**Order matters**: the admin executes manually, starting with the host that
provides the shared services (Vault, databases) before the hosts that consume them.

### Example: two hosts

**Host A** — core infrastructure:

```bash
# ciu.env on Host A:
# CIU_HOST_PROFILE=core_infra

ciu --generate-env           # detects Host A's identity
ciu-deploy --deploy          # runs phase_1 (Vault) + phase_2 (data)
```

**Host B** — worker tier (after Host A is healthy):

```bash
# ciu.env on Host B:
# CIU_HOST_PROFILE=workers

ciu --generate-env           # detects Host B's identity
ciu-deploy --deploy
# workers profile topology_overrides points Vault at Host A's external address
# CIU validates Vault reachability BEFORE starting anything [S7.6]
```

Cross-host reachability (published ports, VPN, tailnet) is the operator's
responsibility. CIU's Vault preflight [S7.6] tells Host B before any phase runs
whether its Vault address + token resolve.

---

## Phases and Phase Control [S7.1–S7.2]

Phase tables MUST be named `phase_<uint>` and are executed in **numeric** order
(not lexicographic — `phase_10` runs after `phase_9`) [S7.1]:

```toml
[deploy.phases.phase_1]
name = "Vault"
services = [
  { path = "infra/vault", name = "Vault secrets management", enabled = true },
]

[deploy.phases.phase_2]
name = "Data"
services = [
  { path = "infra/redis-core", name = "Redis cache", enabled = true },
  { path = "infra/db-core", name = "Database core (Postgres and MinIO)", enabled = true },
]

[deploy.phases.phase_3]
name = "Apps"
services = [
  { path = "applications/app-config", name = "Application configuration", enabled = "enable_app" },
]
```

`name` is an operator-facing display label. It can contain spaces and does not
identify a Compose service or container. `path` identifies the stack.
`enabled` is a `bool` or the **name** of a flag in `[deploy.control]`. Unknown
flag name = abort. Expressions are forbidden (v1 `eval()` withdrawn) [S7.2]:

```toml
[deploy.control]
enable_app = true   # flip to false to disable phase_3's service
```

An intentionally ephemeral one-shot stack can opt out of later orchestration
health checks without opting out of deployment. `health` is a strict boolean
and defaults to `true`:

```toml
[[deploy.phases.phase_3.services]]
path = "jobs/schema-init"
name = "Apply database schema"
enabled = true
health = false  # deploy and enforce its run, but do not expect it to persist
```

### Shipped services [S8.6]

A service entry MAY carry `shipped = true` (a plain bool; non-bool = abort,
S7.2) to route that stack through its hand-written `docker-compose.yml` instead
of CIU's rendered `ciu.compose.yml`. The stack still participates in phase
ordering and the health gate exactly like a native one; CIU just runs the
pre-shipped compose (loading `ciu.env`, ensuring the network, DooD preflight)
without the secret / overlay / configfile steps:

```toml
[deploy.phases.phase_2]
name = "Data"
services = [
  { path = "infra/redis-core", name = "redis", enabled = true },
  { path = "vendor/legacy",    name = "legacy", enabled = true, shipped = true },
]
```

This lets a fleet mix CIU-managed stacks and plain `docker-compose.yml` stacks
in the same phased deployment. See [CIU.md](CIU.md#dual-shipping-s85s86) for the
single-stack `ciu --shipped` equivalent.

---

## Vault Preflight and Token Source Order [S7.6, S4.16]

If the active selection includes stacks with `ASK_VAULT` / `GEN_TO_VAULT`
directives, CIU checks before any phase runs that:

1. The Vault stack is in an earlier phase of the same selection, **or**
2. A Vault token resolves via the source order:
   `VAULT_TOKEN` env → `vault.token_file` config → vault stack's `ciu.toml [state].root_token`

No resolvable token + vault-backed directives present = abort before starting
anything [S7.6].

The preflight reads rendered `ciu.toml` files. In a fresh workspace, render first:

```bash
ciu-deploy --render-toml    # then run --deploy
```

---

## Health Gate Semantics [S7.7]

`--healthcheck` (also runs automatically at the end of `--deploy`) polls each
service's Docker health status until `--health-timeout` or all services are
`healthy`.

| Container status | Gate result |
|---|---|
| `healthy` | Passes |
| `starting` / pending | **Does not pass** — gate polls and eventually fails on timeout |
| `unhealthy` | Immediate fail |
| No healthcheck | Reported as `no-healthcheck` (warning), not silently passing |

The gate reads every selected stack's rendered `ciu.compose.yml` (or its
shipped `docker-compose.yml`) and checks the exact `container_name` of every
active Compose service. Compose `profiles` are filtered using the same
entry-level and host-profile selections as deployment. This is important for
stacks such as a database core that deploy several containers from one phase
entry. The human-readable phase service `name` is never converted into a
container name.

Entries marked `health = false` contribute no targets. When that excludes the
entire selection, CIU prints a clear informational result and passes without
starting an empty polling loop.

CIU fails with an authoring error rather than guessing when the rendered model
is absent or an active service has no concrete `container_name`; run
`ciu render` first and give every health-gated service an explicit identity.
Expected containers are inspected by exact name, never substring [S7.8].

---

## Compose Process Environment [S8.2]

The environment passed to `docker compose` for each stack is exactly:

```
os.environ (which includes ciu.env)
+ PWD
+ COMPOSE_PROFILES (when set by a host profile's compose_profiles)
+ expose_env secrets (per-secret opt-in, discouraged — S4.19)
```

TOML config flattening (`ENV_<KEY>` / `UPPER_SNAKE` placeholders) is
**withdrawn** in v2 — all non-secret values reach the compose template via
Jinja2 at render time [S8.2].

---

## Registry Preflight [S7.9]

When `deploy.registry.url` is set, CIU verifies that Docker credentials for
that registry exist (Docker config `auths` / `credHelpers` lookup) before any
compose run. Verification failure aborts the entire deploy. The v1
`docker login --get-credentials` call is withdrawn [S7.9].
