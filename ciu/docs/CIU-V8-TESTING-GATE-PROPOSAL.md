# CIU v8 Proposal — Native Testing Gate, Logical Services, and Environment Instances

**Status:** PROPOSAL — not yet normative
**Author:** Derived from dstdns repair-program design sessions (2026-08-22–23)
**Supersedes (eventually):** run-gate-project standalone tool; current `[deploy.phases]` model and other config schema
**Target:** CIU v8.0.0 (breaking; `revision` key gates config acceptance)

**Proposal revision:** 1.5
**Updated:** 2026-08-24
---

## 0. Why this proposal exists

### 0.1 The problem this solves

Today, testing is bolted onto deployment as an afterthought:

- `run-gate.py` exists as a separate standalone script with its own config (`run-gate.toml`), duplicating environment facts that CIU already knows.
- Test lanes reference container names that CIU derives dynamically per worktree instance, creating a fragile cross-tool resolution boundary.
- Service realness (live vs mocked vs seeded) has no declarative home — it's implicit in which containers happen to be running.
- `[deploy.phases]` manually declares startup ordering that should be computed from the init-dependency graph.
- `[deploy.profiles]` conflates "which services to deploy together" (service groups) with "what topology shape" (single-host vs multi-host).
- There's no vocabulary for rigor beyond R3, no service realness taxonomy, and no way to select test scope based on changed files.

This proposal unifies all of it into one coherent model inside CIU, eliminating the cross-tool boundary entirely.

Concrete pain from dstdns's repair program (2026-08-22):

- SQL mutation lane debugging required five manual environment reproductions because assay swallowed subprocess output and run-gate had no provisioning capability.
- A role-privilege regression was invisible to the schema lane because the mutation helper didn't forward `SCHEMA_GATE_PW`; the lane reported green while privilege oracles silently failed to connect.
- `pg_dump` version mismatch (v17 client vs v18 server) caused equivalence artifacts to fail silently inside assay snapshots; surfaced only through manual archive inspection.
- The conjunction lane bug means nyxloom-dispatched agents in worktrees may judge the main checkout instead of their attempt tree — false-green depending on whether the consumer's pointer script remembers to `cd`.

### 0.2 What we lose

- `run-gate.py` as a standalone adoptable script for projects that don't use CIU.
- The clean conceptual separation between "provision infrastructure" and "invoke tests."

Mitigation: `ciu gate <lane>` remains a clean public subcommand. Internal coupling doesn't force external coupling.

---

## 1. Design evolution: reasoning trail

*This section preserves the analysis that led to the final design. Do not delete.*

### 1.1 The five-axis model of testing

Every test execution is determined by five independent dimensions:

| Axis | Question | Declared by | Resolved by |
|------|----------|-------------|-------------|
| **Environment** | Where does it execute? Which running instance? | Operator at `ciu up` time | CIU resolves from instance state |
| **Command** | What runs? | Project config (service/stack definitions) | CIU composes |
| **Rigor** | How well is it judged? | Intent preset or explicit flags | Assay (or future judge) |
| **Scope** | Which subset of tests? | Selection patterns + changed files | Gate module |
| **Realness** | Live services, mocks, or mix? | Per-service variant selection | Environment + override |

A lane is NOT these five things collapsed into one name. A lane is the *composition result*: one environment × one command × one rigor set × one scope × one realness mapping. Each axis is independently inspectable and independently changeable.

### 1.2 Rigor levels R0–R6

The current R0–R3 scale measures evidence quality:

| Level | Evidence type | What it proves |
|-------|--------------|----------------|
| **R0** | Exit code | Code runs without crashing |
| **R1** | Line+branch coverage | Every changed line was executed |
| **R2** | Mutation survival | Assertions detect behavior changes |
| **R3** | Canary (pass-on-good / fail-on-bad) | The gate itself is trustworthy |
| **R4** | Property/fuzz testing | Behavior holds under arbitrary inputs |
| **R5** | Formal verification / model checking | Proven correct against specification |
| **R6** | Production canary | Real traffic confirms expected behavior |

R4–R6 are proposed extensions. R4 (property-based testing) is immediately practical for DNS parsing, input validation, and protocol handling. R5 and R6 are domain-specific and expensive; they exist in the vocabulary so consumers can declare them without schema changes later.

**Key insight:** Rigor levels are independent flags, not a pipeline. Valid combinations include:
- R0 alone (smoke)
- R0,R1 (coverage)
- R0,R2 (mutation without coverage — SQL DDL lanes)
- R0,R3 (canary verification without mutation cost)
- R0,R1,R2,R3 (full quality)
- R0,R4 (property testing)

Any combination is valid if the underlying tool supports each level independently.

### 1.3 Named intents expand to rigor sets

Consumers don't set rigor flags directly — they express intent:

| Intent | Expands to | Purpose |
|--------|-----------|---------|
| `smoke` | R0 | Does it run at all? |
| `coverage` | R0,R1 | Did I execute my changes? |
| `quality` | R0,R1,R2 | Do assertions catch behavior changes? |
| `trust` | R0,R3 | Is the gate itself reliable? |
| `full` | R0,R1,R2,R3 | Everything |
| `property` | R0,R4 | Arbitrary-input robustness |

Explicit `--rigor R0,R2` overrides any intent for ad-hoc use.

### 1.4 Service realness taxonomy

Two categories with different vocabularies:

**Internal components** (things we build):

| Level | Meaning | Use case |
|-------|---------|----------|
| `mock` | Monkeypatched/stubbed implementation | Enables testing around unimplemented components |
| `live` | Real implementation running (container, process, whatever form) | Integration/e2e |

**Third-party services** (things we consume but didn't build):

| Level | Meaning | Use case |
|-------|---------|----------|
| `live` | Actual external service (Stripe API, Cloudflare DNS) | Production validation |
| `owned-seeded` | Our own instance with known state (local BIND9, local Stripe stub) | Deterministic integration |
| `simulated` | Dumb server over network, canned responses | Network-layer testing without real complexity |
| `mock` | In-process mock object | Fast unit tests |

**Critical insight:** mocking our own internal components enables integration testing of *other* services even when one component isn't implemented yet. The network is not the issue — the missing implementation is. A mock fills the gap.

#### 1.4a Realness selection semantics (normative)

Realness is resolved at `ciu up` time through a three-layer precedence:

**Layer 1: Config defaults.**

```toml
[testing.realness_defaults]
internal = "live"                # default for all internal services
3rd_party = "owned-seeded"       # default for all third-party services
```

These are the baseline: unless overridden, every internal service runs its
`live` variant and every third-party service runs its `owned-seeded` variant.
Only variants actually declared in `[service.<name>]` are eligible — a service
with only a `mock` variant cannot be selected as `live`.

**Layer 2: Per-service override in config.**

```toml
[testing.realness_overrides]
payment-api = "simulated"        # always simulated regardless of defaults
notification-service = "mock"   # always mocked
```

This lets consumers pin specific services to specific variants without
changing the global default.

**Layer 3: CLI override at up time.**

```bash
# Override one or more services for THIS invocation:
ciu up --group full --profile single-host --realness payment-api=live

# Override multiple:
ciu up --group full --profile two-host --realness payment-api=live,notification-service=mock
```

CLI overrides take precedence over config overrides, which take precedence
over defaults. The resolution is deterministic and inspectable via
`ciu plan` (§5.6) before any container starts.

**Testing intent influence on realness.**

A testing intent MAY carry a realness hint that adjusts Layer 1 defaults:

```toml
[testing.intents.smoke]
rigor = ["R0"]
realness_hint = { internal = "mock", 3rd_party = "mock" }

[testing.intents.trust]
rigor = ["R0", "R3"]
realness_hint = { internal = "live", 3rd_party = "live" }
```

The hint lowers realness toward mocking (faster, no credentials needed) or
raises it toward live (full integration). It NEVER overrides explicit CLI
flags or per-service config overrides — it only adjusts the *default* layer.
If an intent's hint requests `mock` but the service has no mock variant,
the hint is silently ignored for that service (it falls back to the config
default).

**Resolution order summary:**

1. CLI `--realness <svc>=<level>` → highest precedence
2. `[testing.realness_overrides].<svc>` → per-service pinning
3. Intent `realness_hint` (if intent selected AND variant exists)
4. `[testing.realness_defaults]` → type-level fallback
5. Error if no variant exists at any level for the service

### 1.5 Why named sub-tables for realness variants

```toml
# Option A (CHOSEN): level name IS the key
[service.payment-api.live]
base_url = "https://api.stripe.com"

[service.payment-api.mock]
implementation = "tests/mocks/payment_mock.py"

# Option B (REJECTED): array with realness field
[[service.payment-api]]
realness = "live"
base_url = "https://api.stripe.com"
```

Option A wins because:
- TOML enforces uniqueness (can't accidentally declare two `live` variants)
- Direct key lookup: `config["service"]["payment-api"]["live"]`
- Invalid level names are structurally obvious
- Templates access variants cleanly: `{{ service.payment_api.live.base_url }}`
- No iteration needed to find the right variant

### 1.6 Phases → init dependency graph

Current `[deploy.phases]` manually declares startup ordering. This conflates two different graphs:

**Init dependencies** (renamed from S13 `requires`/`provides`):
- "I cannot START until X exists"
- Driven by secret propagation requirements during initialization
- Startup order is COMPUTED by topological sort of this graph — never declared

**Usage dependencies:**
- "My functionality degrades without X but I can still run"
- Does NOT affect startup order
- Affects health/readiness reporting only
- `allow_degraded_start = true` (default): after init completes, services start regardless

After initialization finishes, all services can start in any order. Degraded service is acceptable; missing init prerequisite is not.

### 1.7 Profiles → two distinct concepts

Current `[deploy.profiles]` conflates two orthogonal concerns:

**Service groups** — which services you want up together:
```toml
[deploy.groups.core]
services = ["vault", "consul", "redis"]
```

**Deployment profiles** — topology shape:
```toml
[deploy.profiles.single-host]
description = "All on one host"

[deploy.profiles.three-host]
description = "Distributed across hosts"
```

An **environment instance** = one group × one profile × a lifecycle state. Multiple instances can share the same profile (classic multi-stack). Environments are runtime facts created by `ciu up`, not static declarations.

### 1.8 Topology vs service identity

Three categories of deployment-dependent facts:

| Category | Example | Lives in |
|----------|---------|----------|
| **Service identity** | port, health endpoint, image | `[service.<name>.<level>]` |
| **Network routing** | which host, VPN vs proxy, path prefix | `[deploy.profiles.<name>]` + `[topology.*]` |
| **Access method** | direct DNS, WireGuard IP, reverse-proxy path | Transport mechanism in topology |

The service declares: *"I listen on port 8080."*
The profile declares: *"worker-io runs on host B, reachable via WireGuard at 10.0.0.2:8080."*

Switching from WireGuard to nginx reverse proxy changes topology only. Service config untouched.

Cross-host external addresses (when VPN isn't used) are routing facts owned by topology, never by the service.

### 1.9 Conjunction lane flag forwarding bug (run-gate finding)

Fixed in `run-gate`, solution could be adopted.

### 1.10 Assay integration 

CIU should validate the verdict JSON against Assay’s packaged schema, preserve/pin the producing version, and treat exit status as routing—not proof. That avoids weakening evidence to “process returned zero.”

CIU-specific resource governance belongs in CIU, consistent with Assay’s explicit non-goal of being an orchestrator (assay/nyxloom-trove/2-product-definition.md:460). For infrastructure-dependent lanes, however, B013 is the key Assay gap.

#### 1.11 subprocess output capture (upstream ask)

**RESOLVED upstream as Assay B014** (shipped in assay-v2.3.0): bounded final stdout/stderr plus truncation counts for failed/timed-out commands. CIU should NOT duplicate this — lane-command diagnostics belong in the verdict, not in CIU's orchestration log. Consumer repos must verify their pinned Assay artifact includes B014 before relying on it.

#### 1.12 Higher Rigor

- Assay should provide evidence contracts for higher rigor, not become a property-testing/fuzzing engine. Hypothesis, proptest, ClusterFuzzLite, AFL-style tools, and domain-specific fuzzers remain better producers.
- The sensible boundary: specialized tools generate cases and produce structured evidence; Assay validates thresholds, binds evidence to commit/input, emits verdicts, and fails loudly. This also matches its existing non-goals and avoids reinventing mature tooling.
- For mutation rigor specifically, finishing B012 resume/checkpointing and provable sharding is higher value than inventing an “R4.” Property/fuzzing can later enter as another evidence tier/provider contract once CIU actually needs a second judge producer.

#### 1.13 Build version management (`[build]` + `ciu refresh`)

The current dstdns config carries `[build] python_version = "3.14"`,
`python_base_image`, etc. at global scope. These serve two purposes:
(1) build inputs read by Dockerfiles/bake via Jinja2, and (2) version pinning.

V8 formalizes this into a structured namespace:

```toml
[build.python]
version = "3.14"                        # CONSTRAINT — what we accept; never auto-changed
chosen_version = "3.14.8"              # filled by ciu refresh; the actual resolved version
possible_version_upstream = "3.15.0"   # always written; informational even when held
hold = false                             # true = chosen_version NOT updated; upstream still tracked
base_image = "python:{{ build.python.version }}-slim-trixie"

[build.python.libraries.pydantic]
version = ">=2.5,<3"                   # constraint (PEP 440 range or exact pin)
chosen_version = ""                     # resolved by ciu refresh within version constraints
possible_version_upstream = ""
hold = false

[build.node]
version = "22"                         # major-version constraint
chosen_version = "22.11.0"             # frozen because hold=true
possible_version_upstream = "24.0.0"   # still tracked so operator can see what's available
hold = true                              # never auto-bump chosen_version
```

A new `ciu refresh` verb queries upstream registries for EVERY declared
component and writes two fields:

| Field | Written by refresh? | When held? | Purpose |
|-------|--------------------:|-----------:|---------|
| `version` | NEVER | NEVER changed | The human-declared constraint |
| `chosen_version` | Yes, non-held only | NOT written (frozen) | Concrete resolved version CIU uses |
| `possible_version_upstream` | YES, all components including held | ALWAYS written | Best available upstream match — informational |

`hold = true` prevents `chosen_version` from being updated but does NOT
prevent `possible_version_upstream` from being written. An operator holding
a component still sees what upstream offers, enabling an informed decision
to un-hold later.

**Refresh output:**

```
ciu refresh report:
  UPDATED: build.python 3.14.7 → 3.14.8
  HELD: build.node held at 22.11.0; upstream available: 24.0.0
  UNCHANGED: build.python.libraries.httpx already at ==0.27.0
  Total: 1 updated · 1 held · 1 unchanged
```

```toml
[build]
min_release_age_days = 14        # default 0; prevents adopting just-released versions
channel = "stable"               # "stable" | "pre-release" | "all"
```

Per-component `min_release_age_days` override supported.

#### 1.14 User-declared namespaces (`ciu.user_tables`)

Consumers currently place application-domain tables at global top level
alongside CIU's reserved namespaces, creating collision risk.

V8 introduces:

```toml
ciu.user_tables = ["authentik", "auth", "workflow", "pubsub", "load_control", "build"]
```

- Listed top-level tables are consumer-owned; CIU passes them through to
  templates unchanged without validating shape.
- Any top-level table NOT listed AND NOT reserved MUST be absent — unknown
  tables are validation errors naming the key.
- The declaration itself is validated (valid TOML bare keys, no duplicates).

#### 1.15 Service identity model: stacks contain services

Services are declared in a **two-level hierarchy**: a logical STACK contains
one or more logical SERVICES. The compound key `<stack>.<service>` is the
globally unique service identifier. This mirrors Docker Compose's own naming:
container names become `<project>-<instance>-<stack>-<service>[-<replica>]`.

**Stack-level declaration** — defines WHAT CIU does with this stack and WHERE
to find it:

```toml
# A CIU-managed stack containing multiple services
[service.our_db_stack]
type = "CIU"
location = "infra/db-core"       # filesystem path to the stack directory

# A legacy compose project (no ciu config, pre-created docker-compose.yml)
[service.legacy_stack]
type = "COMPOSE"
location = "/opt/my_legacy_service"

# An external API consumed but never deployed here
[service.payment-api]
type = "EXTERNAL"

# Mock/simulation files only; never deployed as containers
[service.notification-service]
type = "IN_PROCESS"
```

**Service-level declaration** — realness variants live INSIDE the stack,
on each service:

```toml
# Services within a CIU-type stack
[service.our_db_stack.postgres.live]
port = 5432
image = "timescale/timescaledb-ha:pg18"

[service.our_db_stack.postgres.mock]
implementation = "tests/mocks/postgres_mock.py"

[service.our_db_stack.minio.live]
port = 9000
image = "minio/minio:latest"

# Services within a COMPOSE-type stack CAN have realness variants —
# one service might be replaced by a stub while others run live.
[service.legacy_stack.service1]
port = 1234                        # live: how we reach the real service

[service.legacy_stack.service1.mock]
implementation = "tests/mocks/legacy_service1_mock.py"

# EXTERNAL and IN_PROCESS types have no inner stack services;
# their realness variants sit directly on the entity:
[service.payment-api.live]
endpoint = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

[service.payment-api.mock]
implementation = "tests/mocks/stripe_mock.py"
```

**Type determines HOW CIU deploys the stack, not what realness variants
its services can have. Realness is always per-service and orthogonal to
the deployment mechanism.**

| Type | Has inner services? | How CIU deploys | Realness per service |
|------|--------------------|-----------------|---------------------|
| `CIU` | Yes | Full pipeline (render, secrets, hooks, compose) | `live`, `mock`, `owned-seeded`, `simulated` |
| `COMPOSE` | Yes | `docker compose up` on pre-existing file | `live`, `mock`, `simulated` |
| `EXTERNAL` | No (entity IS the service) | Nothing deployed; connection facts only | `live`, `owned-seeded`, `simulated`, `mock` |
| `IN_PROCESS` | No (entity IS the mock) | Nothing deployed; files referenced directly | `mock` only |

**Service reference format:** always `<stack>.<service>`. This is unique
because TOML enforces that a given stack name appears only once at the top
level, and service names within it are unique by TOML table rules.

No separate `logical_name` field is needed. If two stacks both have a
service called `postgres`, they are naturally distinct:
`our_db_stack.postgres` vs `other_stack.postgres`. Cross-stack references
in groups, init_requires, and testing selection use the full compound key.


**Problem.** Each stack directory needs per-service deployment config
(ports, env vars, hostdirs). Currently dstdns derives a root key from the
directory name (`infra/db-core` → `[db_core]`). This is implicit and fragile.

**V8 approach:** every stack declares its services under a reserved
top-level key `[local_stack]` in its own `ciu.defaults.toml.j2`. This is
NOT the global service registry — it is per-stack deployment wiring:

```toml
# infra/db-core/ciu.defaults.toml.j2
[local_stack.postgres]
port = 5432
image = "timescale/timescaledb-ha:pg18"
health_endpoint = "/ready"

[local_stack.minio]
port = 9000
image = "minio/minio:latest"
```

| Aspect | Global `[service.X]` | Stack `[local_stack.Y]` |
|--------|----------------------|--------------------------|
| Purpose | Logical identity + realness variants | Per-stack deployment wiring |
| Owner | CIU global config | Stack's own ciu.defaults.toml.j2 |
| Contains | type, location, init_requires | port, image, env, hostdir, hooks |
| Realness | Declared here | Not present — realness is a global concern |

Templates access local config as `{{ local_stack.postgres.port }}`;
cross-stack references still use `{{ topology.services.postgres.internal_host }}`.

**Mapping rules (normative):**

1. The `[local_stack.<name>]` key MUST match the logical service name from
   the global `[service.<name>]` registry exactly. This is the join key:
   global defines WHO the service is; local_stack defines HOW to run it.
2. One stack directory MAY declare multiple `[local_stack.*]` entries —
   this is normal when a stack deploys several services (e.g. db-core runs
   postgres, minio, and pgadmin).
3. The global `location` field points to the stack directory. Renaming or
   moving that directory requires updating only the global `location` value;
   the stack's own `[local_stack]` content is unchanged.
4. A `[local_stack.<name>]` entry for a service NOT declared in the global
   registry is valid — it means "this stack deploys something only it knows
   about." The global registry exists for cross-stack references and SSOT
   defaults, not as a gatekeeper for what a stack may run.
5. Hooks move from the old `[<root>.hooks]` convention to
   `[local_stack.<svc>.hooks]`, keeping hook declarations co-located with
   the service they configure.

#### 1.17 Topology: do we need endpoints/routes?

When everything runs on one internal Docker network, services reach peers
by container hostname + port. No routing table needed.

When multi-host / VPN / reverse-proxy enters, `[deploy.profiles.<name>.<host>]`
already answers "where". "How to reach" depends on transport mode:

```toml
[topology]
transport = "direct"             # "direct" (same network) | "wireguard" | "proxy"

# Only when transport != "direct":
[topology.hosts.host-a]
address = "10.0.0.1"
```

Optional per-service route overrides (reverse-proxy paths):
```toml
[deploy.profiles.two-host.routes.api]
path_prefix = "/api"
```

**Decision:** defer full endpoint/route modeling until a second multi-host
consumer exists. Profile + transport covers the immediate need. Most stacks
on an internal network never declare routes.

#### 1.18 One-shot completion semantics

Current `stack:<name>:healthy` probes container_name derived from basename,
which is fragile. The exit_code==0 special case works only when no healthcheck
exists — a healthcheck that passes before exit gives a false positive.

V8 adds `one_shot = true` on phase service entries. When true, the health
gate treats exit-0 as satisfied without Docker-health polling. Requires use
full stack path: `requires = "stack:infra/db-init:completed"` instead of
basename-only `stack:db-init:completed`.

#### 1.19 Unified configfile fan-out + compose enumeration

Currently dstdns declares separate configfile sections per replica AND
generates compose services via manual Jinja loops. Using both mechanisms
produces duplicate mounts.

V8 unifies: root-level `instances = N` on a service drives BOTH configfile
fan-out AND compose service enumeration. Templates iterate via a provided
instance-loop context rather than hand-writing `{% for %}` ranges.

#### 1.20 Environment variable declarations

Stacks currently inject `${VAR:-fallback}` or `${VAR:?message}` directly
into compose templates with no typed declaration of expected vars.

V8 adds an inline list on each local_stack entry:

```toml
[local_stack.postgres]
env_required = ["POSTGRES_PASSWORD_FILE", "POSTGRES_USER"]
```

CIU validates presence of every listed var before render and fails naming
missing variables. Known machine identity keys are auto-injected into Jinja
context as `{{ env.CONTAINER_UID }}` instead of raw Compose interpolation,
making the source explicit.

---

## 2. Config model

### 2.1 Single source file

Everything lives in `ciu.global.defaults.toml.j2` (existing name, new revision).

A `revision` key gates acceptance:

```toml
revision = 8    # CIU v8 requires revision >= 8; refuses lower
```

Old configs lacking this key are refused with a clear upgrade message. Hard cutover — no compatibility shim.

### 2.2 Rendering hierarchy unchanged

```
ciu.global.defaults.toml.j2     ← committed source template (repo root)
ciu.global.toml.j2              ← optional committed override template
ciu.global.toml                 ← rendered output (gitignored)
```

Per-stack overrides follow existing S3 merge chain.

Note: We should list used files and rendering on project-level as well to make hierarchy transparent. 


### 2.3 Testing declarations live in the same file

No separate `ciu.testing.toml`. Variable substitution works uniformly across services, topology, and testing sections because they share one rendering pass.

### 2.4 User table declaration

See §1.14. `ciu.user_tables` lives in the global config alongside other
`[ciu]` workspace switches.

### 2.5 Rendered-config validation preflight

V8 introduces a pre-render validation pass that catches silent-empty renders.
After Jinja2 rendering but before TOML parsing, CIU checks that template
expressions referencing a key path not present in the context produce an
error naming the missing path — not a silently empty render. Implemented by
wrapping Jinja2 `Undefined` to raise on attribute access; templates MUST
reference keys that exist or use `{% if x is defined %}` explicitly.

### 2.6 Registry schema enforcement — options analysis

`[registry.postgresql]`, `[registry.redis.users.*]`, etc. are free-form TOML
consumed by hooks with no CIU-level validation. A typo surfaces only at hook
runtime.

Three approaches:

**Option A: JSON Schema per registry table** — standard tooling, IDE support,
no Python dependency. Cons: JSON Schema for TOML is awkward; requires a
schema file per table type; consumers maintain both config and schema.

**Option B: Validated Pydantic models shipped with CIU** — CIU provides
models for the five built-in provisioning kinds (PostgreSQL, Redis, MinIO,
Consul, Vault). Hooks receive validated objects. Pros: type-safe,
self-documenting, errors carry key paths and expected types, fail-fast at
render time. Cons: couples CIU to specific shapes; new types require CIU
model updates.

**Option C: Consumer-declared Pydantic models (hook-side)** — hooks import
their own models from the consumer project's library. CIU passes raw dicts;
the hook validates on receipt. Pros: zero CIU coupling. Cons: no pre-render
validation — typos still surface only at hook runtime.

**Recommendation: Option B for well-known types, Option C for custom.**
CIU ships models for PostgreSQL, Redis, MinIO, Consul, Vault (the five
built-in provisioning ref kinds). These are stable enough to warrant
first-class schemas. For anything else, hooks validate on their own. This
gives fail-fast for the common case without making CIU responsible for
arbitrary consumer schemas.

### 2.7 `ciu config check` — full config validation with hook preflight

**Problem.** Today, hook-level typos and registry shape errors surface only
at hook runtime — after containers are already starting. The existing
`ciu check` verb validates only the provisioning graph (requires/provides).
It does NOT walk hooks, render configfiles, validate registry shapes, or
exercise any of the pipeline's later steps. A consumer must run a real
`ciu up --dry-run` to catch these, which still creates hostdirs and runs
pre_secrets hooks.

**Proposal: `ciu config check [--profile NAME] [--json]`.**

A read-only, side-effect-free validation pass that walks the ENTIRE config
pipeline in dry-run mode:

| Stage | What it validates | Side effects |
|-------|-------------------|-------------|
| 1. Render | Jinja2 + `$VAR` expansion + TOML parse (global chain, all selected stacks) | None |
| 2. Shape | Single root key (S3.5), reserved namespace collision (S3.7) | None |
| 3. Secrets | Directive grammar (S4), placement (S4.1/S4.5), name uniqueness (S4.6), Vault ordering (S7.6) | None |
| 4. Provisioning | requires/provides grammar (S13), graph lint, cycle detection | None |
| 5. Governance | Shape checks (S15.2), cgroup parent resolution | None |
| 6. Configfile | Template existence, target path validity, schema file existence (S5) | None |
| 7. Registry | Built-in Pydantic model validation (§2.6 Option B); consumer models via optional `validate(config)` callable | None |
| 8. Hooks — load | Every declared hook file exists and exports `run` or `Hook` with `run` method (S9.2) | None |
| 9. Hooks — preflight call | Call each hook's `validate_config(config, ctx)` method if it defines one; report errors without executing `run()` | None |
| 10. Compose render | Full compose template rendering with guarded config (S4.21) | Writes nothing; renders to memory |
| 11. Overlay | Generate overlay in memory; leak scan (S4.22) | Writes nothing |
| 12. Consumption | Declared-vs-consumed secret cross-check (S4.20) | None |

**Key design decision: the `validate_config` hook contract.**

Hooks that want preflight testing implement an OPTIONAL second entry point:

```python
# infra/db-core/post_compose_db.py

def run(config: dict, ctx) -> dict:
    """Normal execution — provisions users, databases."""
    ...

def validate_config(config: dict, ctx) -> list[str]:
    """Optional preflight. Return list of error strings (empty = OK).
    CIU calls this during `ciu config check` but NEVER during normal up.
    Accesses the same merged config and context as run(), so it can
    validate exactly what run() will consume."""
    errors = []
    if "database" not in config.get("registry", {}):
        errors.append("registry.database is missing")
    for user in ("controller", "workerdb"):
        if user not in config.get("registry", {}).get("postgresql", {}).get("users", {}):
            errors.append(f"registry.postgresql.users.{user} is missing")
    return errors
```

- `validate_config` is entirely optional. Hooks that don't define it are
  simply skipped in the hook-preflight stage (stage 9 above). No error.
- When defined, it receives the SAME merged config and HookContext as
  `run()`, including SecretGuard objects for secrets (which it can check
  exist by name without accessing values).
- It MUST NOT execute side effects (no Docker calls, no network access,
  no file writes). CIU does not enforce this technically — it is a contract,
  like "hooks don't mutate os.environ" — but violations would be caught
  because `config check` runs before any container exists.
- Return type is `list[str]`: empty = valid; non-empty = one error string
  per finding. CIU aggregates all findings across all hooks and reports
  them together.

**Output (prose):**
```
ciu config check: 3 stacks, 2 profiles, 14 services

  ✓ global config rendered
  ✓ infra/db-core shape valid
  ✓ secrets: 8 directives, all well-formed
  ✓ provisioning: 5 refs, no cycles
  ✓ governance: dev-background.slice resolved
  ✓ configfiles: 2 templates found, schemas present
  ✓ registry: postgresql validated (6 users)
  ✓ registry: redis validated (4 ACL entries)
  ✓ hooks: 3 files loaded, 2 define validate_config
    ✓ db-core/post_compose_db.py: no issues
    ✗ consul-server/post_compose_consul.py: registry.consul.acl.default_policy missing
  ✓ compose render: 3 stacks rendered, no leaks

  RESULT: FAIL (1 error)
```

**Output (`--json`):**
```json
{
  "schema_version": 1,
  "operation": "config-check",
  "status": "fail",
  "stages": [
    {"stage": "render", "status": "pass"},
    {"stage": "shape", "status": "pass", "stacks_checked": 3},
    {"stage": "registry", "status": "fail",
     "errors": [
       {"hook": "consul-server/post_compose_consul.py",
        "message": "registry.consul.acl.default_policy missing"}
     ]}
  ]
}
```

Exit codes follow S10.3: 0 = all pass, 1 = findings reported, 2 = config/validation error.

**Relationship to existing verbs:**

| Verb | What it does | What `config check` adds |
|------|-------------|--------------------------|
| `ciu check` | Provisioning graph lint + optional live probe | Everything else on this table |
| `ciu up --dry-run` | Full pipeline except compose up; still creates hostdirs, runs hooks | Zero side effects; no Docker needed |
| `ciu provenance` | Verifies running images against commit | Orthogonal — post-deployment evidence |

This verb makes `ciu up --dry-run` unnecessary as a validation tool and
gives CIU agents / CI pipelines a safe way to verify config correctness
without touching Docker or the filesystem beyond reading templates.

---

## 3. Logical services and realness

### 3.1 Declaration

The global config declares logical stacks and their services. Each stack has
a `type` that determines CIU behavior. Services within a stack declare
realness variants.

```toml
# ────────────────────────────────────────────────────
# CIU-managed stacks
# ────────────────────────────────────────────────────

[service.our_db_stack]
type = "CIU"
location = "infra/db-core"

[service.our_db_stack.postgres.live]
port = 5432
image = "timescale/timescaledb-ha:pg18"
init_requires = []                  # nothing needed before postgres starts
provides = ["pg:db/demo", "pg:role/controller"]

[service.our_db_stack.postgres.mock]
implementation = "tests/mocks/postgres_mock.py"

[service.our_db_stack.minio.live]
port = 9000
image = "minio/minio:latest"
provides = ["minio:user/worker-io"]

[service.our_api_stack]
type = "CIU"
location = "applications/api-handler"

[service.our_api_stack.api.live]
port = 8080
health = "/health"
init_requires = [
    "our_db_stack.postgres",       # reference by compound key
    "vault:secret/db/postgres/api_password",
]
depends_on = ["our_db_stack.redis", "payment-api"]
allow_degraded_start = true         # default true

[service.our_api_stack.api.mock]
implementation = "tests/mocks/api_handler_mock.py"

# ────────────────────────────────────────────────────
# COMPOSE-type stacks (pre-existing docker-compose.yml)
# ────────────────────────────────────────────────────

[service.legacy_stack]
type = "COMPOSE"
location = "/opt/my_legacy_service"

[service.legacy_stack.service1]
port = 1234                        # live: how we reach the real service

[service.legacy_stack.service1.simulated]
image = "wiremock/wiremock:latest"
stub_mappings = "fixtures/legacy_stubs/"

# ────────────────────────────────────────────────────
# EXTERNAL services (consumed, never deployed)
# ────────────────────────────────────────────────────

[service.payment-api]
type = "EXTERNAL"

[service.payment-api.live]
endpoint = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

[service.payment-api.mock]
implementation = "tests/mocks/stripe_mock.py"

# ────────────────────────────────────────────────────
# IN_PROCESS services (mock files only)
# ────────────────────────────────────────────────────

[service.notification-service]
type = "IN_PROCESS"

[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"
```

**Key structural rules:**

- Stack-level keys (`type`, `location`, `description`) sit directly under
  `[service.<stack_name>]`.
- Service-level realness variants are nested:
  `[service.<stack>.<svc>.<level>]`.
- For `EXTERNAL` and `IN_PROCESS` types there is no inner service layer —
  realness sits directly on the entity because it doesn't live inside a stack.
- Realness is ALWAYS per-service, regardless of stack type. A COMPOSE-type
  stack can have one service mocked while others run live. The `type`
  controls deployment mechanism; realness controls test posture.
- `init_requires` and `depends_on` reference OTHER services using the full
  compound key (`<stack>.<service>`) or external typed references
  (`vault:secret/...`, `pg:db/...`).

### 3.2 Validation rules

- Every sub-table key under `[service.<stack>.<svc>]` MUST match a valid
  realness level. Other key names are validation errors.
- Valid realness levels:
  - `CIU` and `COMPOSE` stacks: `live`, `mock`, `owned-seeded`, `simulated`
    per service
  - `EXTERNAL`: `live`, `owned-seeded`, `simulated`, `mock`
    (declared directly on the entity)
  - `IN_PROCESS`: `mock` (the entity IS a mock)
- At least one variant MUST be declared per service.
- `init_requires` references use S13 typed-reference grammar. Service-to-service
  references use the compound `<stack>.<service>` form.
- Secrets referenced in a variant MUST have corresponding entries in
  `ciu.secrets.toml` or Vault paths. Missing credentials fail at
  config-validation time when that realness level is selected.

### 3.3 Secret preflight

When an environment selects `payment-api = live`, CIU checks that `[payment-api] stripe_secret_key` exists in the resolved secret store before provisioning. Missing = refuse with message naming the service, variant, and expected key.

Mock and simulated variants that declare no secrets skip credential resolution entirely.

---

## 4. Deployment model

### 4.1 Service groups

Replace the service-selection half of old profiles:

```toml
[deploy.groups.core]
description = "Infrastructure foundation"
services = ["our_db_stack.postgres", "redis_stack.redis"]

[deploy.groups.app]
description = "Application layer"
services = ["our_api_stack.api", "notification-service"]

[deploy.groups.full]
description = "Everything"
services = ["core", "app"]       # groups can reference other groups
```

### 4.2 Deployment profiles

Own topology shape exclusively:

```toml
[deploy.profiles.single-host]
description = "All services on one dev host"
hosts = ["localhost"]

[deploy.profiles.two-host]
description = "DB on host-a, app on host-b"
hosts = ["host-a", "host-b"]

[deploy.profiles.two-host.hosts.host-a]
services = ["our_db_stack.postgres", "redis_stack.redis"]

[deploy.profiles.two-host.hosts.host-b]
services = ["our_api_stack.api", "notification-service"]

[topology]
mode = "wireguard"               # "wireguard" | "proxy" | "direct"

[topology.hosts.host-a]
wireguard_ip = "10.0.0.1"

[topology.hosts.host-b]
wireguard_ip = "10.0.0.2"
```

Profiles are reusable across multiple environments (multi-stack).

Note: Ideally we would not write e.g. `postgres` but directly reference the service we have defined.
Open question: Consider we want to consume a 3rd party service not live but as `owned-seeded`, we need to bring it up somewhere. If we add it to the profile it makes it not generic anymore. We could add a category `optional` or so, so if that service of type `owned-seeded` is needed, it would be started here. Also this means, every service which might need to get deployed, needs to be defined where to run in a profile, otherwise it is unknown how to handle it. Maybe we dont need `optional`, *what* is started is not decided by the profile, but what `ciu` decides which groups / services need to be started?

### 4.3 Startup ordering

#### 4.3.1 Frist-Time Initialization 

Computed from `init_requires` graph via topological sort. Only applies during initialization phase. 

#### 4.3.2 Regular start

- All services may start in any order
- Services with unmet `depends_on` report degraded health
- `allow_degraded_start` defaults to `true`; setting it to `false` means the service refuses to start if its usage deps aren't healthy

Note: to prohibit errors during start due to degredation, `ciu` should allow a safe/hinted start order for clean startup. 

No phases. No manual ordering.

### 4.4 Environment instances

Created at runtime by operator action:

```bash
ciu up --group full --profile single-host --name dev-john
ciu up --group app --profile two-host --name staging
```

Multiple instances can coexist (multi-stack). Each gets unique `INSTANCE_ID`, network, container prefix — existing S16 behavior.

Note: If lots of groups are defined it might be useful to allow exclusion (subtract) a group/service from start like `--group full,-excluded_service`

**Locking:**

```bash
ciu lock <instance-name>          # forbid ciu down
ciu unlock <instance-name>        # allow ciu down again
```

Lock settings can also default from config:

```toml
[deploy.profiles.single-host.locks]
down = true                       # require explicit unlock before ciu down
clean = true                      # require unlock AND interactive confirmation
```

Production deployments carry locks in config so a fresh `ciu up --profile production-lockdown` is protected from day one.

`ciu clean` always requires two interactive confirmations regardless of lock state.

### 4.5 Remote execution

Existing S14 remote-host transport handles deployment to remote machines. For Buildkite or CI runners:

```bash
# On buildkite agent (or via SSH to remote host)
ciu up --group full --profile single-host --name ci-$BUILDKITE_BUILD_NUMBER
ciu gate --intent quality --environment ci-$BUILDKITE_BUILD_NUMBER
ciu down --name ci-$BUILDKITE_BUILD_NUMBER
```

No special mechanism needed — the agent invokes the same CLI.

For remote test execution, CIU's S14 SSH transport provisions the stack on the remote host. The gate module either runs locally and connects over WireGuard/proxy, or SSHes to the remote host and executes there:

```toml
[testing]
execution = "local"              # default: run here, connect to remote stack
# execution = "remote"           # SSH to stack host and execute there
```

Note: Secrets for e.g. buildkite (infra 3rd party service) or hosts (for `ciu` deploy access) goes to central `ciu.secrets.toml`.

---

## 5. Testing gate module

### 5.1 Location

`ciu/src/ciu/gate.py` — native CIU module.

`run-gate-proejct` will be maintained as project in parallel. `ciu` gains its functionality and adapts/extends to leverage integration.

### 5.2 Invocation

```bash
ciu gate --intent quality --environment my-instance-name
ciu gate --intent smoke --scope tests/unit/test_api.py --environment none
ciu gate --rigor R0,R2 --selection database --environment integration-test
```

### 5.3 Dynamic lane assembly algorithm

Given intent + environment + changed files:

1. **Resolve intent** → rigor set (e.g., `quality` → `[R0, R1, R2]`)
2. **Match selections** → which test scopes fire based on git diff patterns
3. **Read environment** → what's actually running, which services at which realness
4. **Compose lanes** → for each selected scope, determine:
   - Execution target (container from environment, or host-mode if `--environment none`)
   - Required services and their realness levels (from environment + testing default_realness override)
   - Rigor judgment provider (assay subprocess for now; pluggable later)
5. **Validate feasibility** → every required capability must exist at requested level; credential preflight passes
6. **Execute** → run composed lanes under flock serialization

When a matched selection requires a realness level unavailable in the environment (e.g., integration scope needs `owned-seeded` postgres but everything is mocked):
- **Skip with warning** (default): scope not executed; CIU reports which scopes were skipped and why
- **Fail** (`--strict-realness`): refuse the entire gate rather than produce partial evidence

Note: what would "pluggable" mean for `ciu`, what are advantages?

### 5.4 Scope narrowing

Selection tables map changed-file patterns to test scopes:

```toml
[testing.selection.api]
patterns = ["src/api/**/*.py"]
scope = ["tests/unit/test_api.py", "tests/integration/test_api_flow.py"]

[testing.selection.database]
patterns = ["src/models/**/*.py", "migrations/**/*.sql"]
scope = ["tests/integration/test_db.py"]
```

Multiple matching selections union their scopes per lane. No pattern match → fall back to full scope for the intent's rigor set.

### 5.5 Default realness preferences

When the environment doesn't dictate realness for a service:

```toml
[testing]
default_realness = {
  internal = "live",
  3rd_party = "owned-seeded",
}
```

Unit tests typically override everything to `mock` via explicit `--environment none` semantics.

### 5.6 Assay integration

CIU is agnostic to assay's internals. Assay is invoked as a subprocess with pinned artifact, same as today. The interface is:

- Input: composed argv, environment facts (env vars), isolation policy
- Output: verdict JSON artifact at a well-known path
- Exit code: 0 = PASS, non-zero = anything else

Future: pluggable judge providers behind the same interface.

### 5.7 `ciu exec` — native container command execution

Today, consumer projects hand-roll wrapper scripts that shell out to `docker exec`
to run one-off commands inside a running service container (provision a test DB,
run a schema check, inspect state). Each wrapper duplicates container-name
resolution, user-identity mapping, network selection, and env forwarding — the
exact facts CIU already owns in its rendered config. This creates fragile,
unauditable shell glue that breaks on every rename or topology change.

`ciu exec` eliminates all of it:

```bash
# Run a one-off command inside a named service's container
ciu exec test-runner "python3 -m pytest tests/schema -q"

# Target a specific worktree instance by name
ciu exec --instance ci-build-42 test-runner "psql -c '\\dt'"

# Interactive shell
ciu exec --interactive controller bash
```

**What CIU resolves automatically (never re-derived by the caller):**

| Fact | Source | Why CIU owns it |
|------|--------|----------------|
| Container name | Rendered `[deploy.project_name]` + `[deploy.environment_tag]` + service name | Renaming or forking an instance must not break every script |
| User identity | Service-level `exec_user` declaration or platform default | UID/GID mapping is deployment policy, not per-script boilerplate |
| Network | Instance's resolved network from `[deploy.network_name]` | Services talk over instance-scoped networks, not host networking |
| Environment | Filtered subset of rendered `[service.<name>.env]` + explicit `--env KEY=VALUE` overrides | Required config must come from the same source as deployment |
| Working directory | Service-declared default or `--workdir` override | Consistent with how the service itself starts |

**Design rules:**

- No silent defaults: if the target service is not running, fail loudly naming
  the instance, service, and remedy (`ciu up --group ... --name ...`).
- No hardcoded container names anywhere in consumer code; the caller always
  references the *logical service name*, never a Docker-resolved name.
- Stdout/stderr stream transparently (no buffering); exit code passes through.
- `--dry-run` prints the full docker argv with redacted env values (same RG-19
  discipline as gate lanes).

**What gets deleted from dstdns once available:**

| Script | What it hand-rolls |
|--------|-------------------|
| `scripts/p128-assay-schema.sh` | Container name derivation, docker run, docker cp, docker exec |
| `scripts/p129-assay-schema.sh` | Same pattern for P129 scoped coverage |
| `scripts/schema-gate.sh` | Throwaway PG provisioning + schema apply |

All three become unnecessary when CIU can natively provision a disposable PG,
apply DDL, and expose its DSN to the test lane — which is exactly what the
template-database proposal in §10.2 describes.

### 5.7 Resource governance per lane and intent

Every test lane consumes host resources: RAM (the real contention risk), CPU time
(cgroup weights handle fair sharing without explicit limits), and I/O bandwidth.
CIU must carry resource declarations so gates can run in parallel where memory allows,
without degrading live/prod services sharing the same host.

#### Defaults from host config

CIU already resolves `cgroup_parent` via `resolve_cgroup_parent()` with no hardcoded
fallback. Extend this to full resource governance:

```toml
# Host-level defaults — every lane inherits unless overridden
[testing.resources.defaults]
cgroup_parent = "dev-background.slice"
memory = "1g"                     # hard RAM cap (mem_limit)
memory_swap = "16g"               # combined mem+swap; swap absorbs bursts without OOM
io_weight = 100                   # cgroup io.weight for blkio fairness
cpu_weight = 100                  # cgroup cpu.weight for CPU scheduling fairness
```

The `memory_swap` pattern follows cmru's proven approach (`CMRU_TESTER_MEMORY = "1g"`,
`CMRU_TESTER_MEMORY_SWAP = "16g"`): tight RAM prevents memory-pressure cascades into live
services, while ample swap absorbs transient bursts (dependency resolution, test fixtures)
without triggering OOM kills. CPU and I/O use cgroup weights rather than hard limits —
weights provide proportional fair-sharing under contention without throttling when the
host is idle.

Note: we also need support for IO bandwidth and IO iops limits. probably needs `bfq` enabled in kernel?
a parent slice might carry it, but we need to support setting it here, e.g. `riops_max`, `wiops_max`, ... 

#### Per-lane overrides

Lanes that need more or fewer resources declare overrides:

```toml
[testing.lanes.schema-gate]
resources.memory = "2g"          # schema tests need more than default
resources.io_weight = 200        # heavy DDL apply benefits from higher IO priority

[testing.lanes.sql-mutation]
resources.memory = "4g"          # multiple PG instances during mutation
resources.io_weight = 300        # repeated schema rebuilds are IO-heavy
resources.budget_per_candidate = "120s"
```

#### Rigor-based defaults

Resource requirements correlate with rigor level. CIU should provide sensible presets:

```toml
[testing.rigor_defaults.R0]      # smoke: fast, light
memory = "512m"

[testing.rigor_defaults.R2]      # mutation: slow, may need more RAM for parallel jobs
memory = "2g"
jobs = 4

[testing.rigor_defaults.R3]      # canary: needs live services but not extra RAM
memory = "1g"
```

Explicit per-lane values override rigor defaults, which override host defaults.

#### Gate admission policy (replacing global flock)

With resource governance in place, the single-gate-at-a-time flock becomes unnecessarily
restrictive. The rule changes from "one gate globally" to:

> A gate runs if its declared memory fits within the host's available dev-tier budget,
> AND it does not contend on shared infrastructure (same DB volume, same Redis instance).

Implementation:
- each lane declares `resources.memory`; CIU sums concurrent lanes' memory against the
  dev-tier slice's total;
- shared-infra detection: two lanes touching the same rendered service name cannot run
  concurrently regardless of memory;
- fully isolated instances (separate networks + separate volumes) run in parallel freely.

This replaces `/tmp/<project>-testrunner.lock` with resource-aware admission, while
preserving serialization for lanes that genuinely share state.

#### Observability

CIU logs at gate start/end: lane name, resolved cgroup_parent, memory/io/cpu weights,
actual peak RSS (from cgroup memory.peak after exit). This builds the data needed to tune
per-rigor defaults over time instead of guessing.

---

## 6. What gets deleted

| Artifact | Fate |
|----------|------|
| `run-gate-project/run-gate.py` | Absorbed into `ciu/src/ciu/gate.py` |
| `run-gate-project/run-gate.toml` | Replaced by sections in `ciu.global.defaults.toml.j2` |
| `run-gate-project/SPEC.md` | Superseded by this proposal's SPEC section |
| `[deploy.phases]` | Replaced by init-dependency topological sort |
| Old `[deploy.profiles]` (service lists) | Split into `[deploy.groups]` + `[deploy.profiles]` |
| dstdns vendored assay pyz pinning | Moves to CIU-managed (CIU-40 de-vendor half) |

---

## 7. Migration path

### 7.1 Version gating

New configs carry `revision = 8`. CIU v8 refuses configs without this key or with revision < 8. 
Until now CIU ignores unknown keys and continues working against old configs - no breakage until consumer upgrades both sides simultaneously.

### 7.2 Consumer cutover

Hard cutover once ready. nyxloom, dstdns, cmru, and any other consumers update their configs and invocation patterns atomically. The `revision` marker makes accidental mixing impossible.

### 7.3 Implementation phases

1. **Config schema extension:** Add `[service.*]`, `[deploy.groups]`, `[testing.*]` sections alongside existing keys. Validate structure. No behavior change yet.
2. **Gate module:** Port `run-gate` mechanics (exec-mode, mounts, pin verify, clean-tree) into `ciu/src/ciu/gate.py`. Read lanes from rendered config instead of `run-gate.toml`.
3. **Dynamic assembly:** Implement selection pattern matching, intent expansion, realness resolution, feasibility validation.
4. **Delete legacy:** Remove `[deploy.phases]`, old profiles format. Bump revision gate.
5. **Consumer migration:** Update all repos' configs and AGENTS.md pointers in one coordinated wave.

---

## 8. Full sample config

```toml
# ciu.global.defaults.toml.j2
revision = 7

[deploy]
project_name = "demo-app"
environment_tag = "{{ INSTANCE_ID }}"

# ------------------------------------------------------------
# BUILD VERSIONS (see §1.13)
# ------------------------------------------------------------
[build.python]
version = "3.14"
chosen_version = "3.14.8"
possible_version_upstream = "3.15.0"
hold = false
min_release_age_days = 14

# ------------------------------------------------------------
# LOGICAL SERVICES
# ------------------------------------------------------------

[service.our_api_stack]
type = "CIU"
description = "HTTP request handler"

[service.our_api_stack.api.live]
location = "applications/api-handler"
port = 8080
health = "/health"
init_requires = ["postgres:db/demo"]
depends_on = ["redis_stack.redis", "payment-api"]
allow_degraded_start = true

[service.our_api_stack.api.mock]
implementation = "tests/mocks/api_handler_mock.py"

[service.notification-service]
type = "IN_PROCESS"
description = "Sends emails/SMS (not yet implemented)"

[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"

[service.our_db_stack]
type = "CIU"
location = "infra/db-core"

[service.our_db_stack.postgres.owned-seeded]
image = "timescale/timescaledb:pg18"
port = 5432
seed_data = "fixtures/seed.sql"

[service.payment-api]
type = "EXTERNAL"
description = "External payment processor"

[service.payment-api.live]
base_url = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

[service.payment-api.owned-seeded]
location = "infra/payment-stub"
port = 8090
seed_data = "fixtures/payment_responses.json"
secrets = ["payment_stub_key"]

[service.payment-api.simulated]
image = "wiremock/wiremock:latest"
port = 8090
stub_mappings = "fixtures/wiremock_stubs/"

[service.payment-api.mock]
implementation = "tests/mocks/payment_mock.py"

[service.redis_stack]
type = "CIU"
location = "infra/redis-core"

[service.redis_stack.redis.owned-seeded]
image = "redis:7-alpine"
port = 6379

# ------------------------------------------------------------
# STACK-LEVEL SERVICE WIRING (see §1.16) — in each stack's ciu.defaults.toml.j2,
# NOT in the global file. Shown here for illustration.
# ------------------------------------------------------------

# [local_stack.postgres]
# port = 5432
# image = "timescale/timescaledb-ha:pg18"
# env_required = ["POSTGRES_PASSWORD_FILE"]
#
# [local_stack.minio]
# port = 9000
# image = "minio/minio:latest"

# ------------------------------------------------------------
# SERVICE GROUPS
# ------------------------------------------------------------

[deploy.groups.core]
description = "Infrastructure foundation"
services = ["our_db_stack.postgres", "redis_stack.redis"]

[deploy.groups.app]
description = "Application layer"
services = ["our_api_stack.api", "notification-service"]

[deploy.groups.full]
description = "Everything"
services = ["core", "app"]

# ------------------------------------------------------------
# DEPLOYMENT PROFILES (topology shape)
# ------------------------------------------------------------

[deploy.profiles.single-host]
description = "All on one host"
hosts = ["localhost"]

[deploy.profiles.two-host]
description = "DB on host-a, app on host-b"
hosts = ["host-a", "host-b"]

[topology]
transport = "wireguard"

[topology.hosts.localhost]

[topology.hosts.host-a]
wireguard_ip = "10.0.0.1"

[topology.hosts.host-b]
wireguard_ip = "10.0.0.2"

# ------------------------------------------------------------
# TESTING POLICY
# ------------------------------------------------------------

[testing]
default_realness = { internal = "live", 3rd_party = "owned-seeded" }

[testing.intents]
smoke    = { rigor = ["R0"] }
coverage = { rigor = ["R0", "R1"] }
quality  = { rigor = ["R0", "R1", "R2"] }
trust    = { rigor = ["R0", "R3"] }
full     = { rigor = ["R0", "R1", "R2", "R3"] }
property = { rigor = ["R0", "R4"] }

[testing.selection.api]
patterns = ["src/api/**/*.py"]
scope = ["tests/unit/test_api.py", "tests/integration/test_api_flow.py"]

[testing.selection.database]
patterns = ["src/models/**/*.py", "migrations/**/*.sql"]
scope = ["tests/integration/test_db.py"]

[testing.selection.payment]
patterns = ["src/payments/**/*.py"]
scope = ["tests/unit/test_payment.py", "tests/integration/test_payment_flow.py"]

# ------------------------------------------------------------
# SECRETS (in gitignored ciu.secrets.toml)
# [postgres] postgres_password = "..."
# [payment-api] stripe_secret_key = "sk_live_..."
# [payment-api] payment_stub_key = "stub_local_..."
# ------------------------------------------------------------
```

---

## 9. Invocation examples

```bash
# Fast feedback: touched src/api/routes.py
ciu gate --intent smoke --selection api --environment none
# → all services mocked, R0, seconds

# Standard quality gate
ciu up --group full --profile single-host --name ci-build-42
ciu gate --intent quality --environment ci-build-42
# → api-handler live, postgres owned-seeded, payment simulated, notification mocked
# → R0,R1,R2

# Pre-release with real payment API
ciu up --group full --profile two-host --name staging
ciu gate --intent full --environment staging
# → three-host WireGuard topology, payment-api live (credential preflight)
# → R0,R1,R2,R3

# Mutation-only ad-hoc
ciu gate --rigor R0,R2 --selection database --environment ci-build-42
```

---

## 10. Upstream asks filed separately

These improvements benefit the ecosystem regardless of v8 adoption timeline:

### 10.1 dstdns P129 — env-passthrough gap between run-gate and assay snapshots (2026-08-24)

**Problem.** A scoped R1 lane (`p129_enumeration_cursor`) required a derived CIU
instance identity (`P129_PHYSICAL_REPO_ROOT`) inside its lane command. The value
was correctly forwarded by `run-gate.toml` into the docker exec that runs the
assay CLI, but assay's own snapshot isolation creates a fresh ephemeral checkout
and runs the lane command there using ONLY the variables listed in the lane's
`env_passthrough`. Because `P129_PHYSICAL_REPO_ROOT` was absent from that list,
the command failed with a missing-required-env error even though run-gate had
forwarded it.

**Root cause.** Two independent env-forwarding layers exist:

1. run-gate → assay CLI (controlled by `[environments.*].forward_env`)
2. assay CLI → snapshot command (controlled by lane `env_passthrough`)

Neither layer is aware of the other's allow-list, so adding a variable at one
layer does not automatically make it available to the other. This is not an
assay bug — the snapshot isolation contract explicitly requires declared
passthrough for reproducibility — but it is a footgun when consumers must
coordinate two config files for one variable.

**dstdns fix.** Added the variable to both lists and corrected the CIU identity
derivation (`PROJECT_NAME = "dstdns"`, `ENVIRONMENT_TAG = sha256-prefix`).

**CIU-v8 resolution.** When CIU natively owns gate invocation (§5), this class
of bug disappears because:

- CIU injects instance identity directly into the runner environment from its
  own rendered state; no multi-layer forwarding needed.
- The rendered `ciu.global.toml` already contains every fact a lane needs;
  CIU-v8 should expose a structured subset of those facts to each lane command
  as environment variables or a well-known TOML sidecar, eliminating manual
  passthrough declarations entirely.
- Lane commands can read their own identity from the filesystem (a
  `.ciu-instance.json` written by CIU during stack bring-up) rather than
  requiring hash-derived env vars threaded through multiple tools.

### 10.2 SQL mutation testing — template databases and savepoint resets

**Problem.** dstdns's SQL mutation lanes provision a fresh throwaway PostgreSQL
container per invocation, apply the full DDL schema, run tests, then tear down.
For R2 mutation testing with dozens of candidates, this means dozens of full
schema builds — slow and IO-heavy.

**Two optimization strategies:**

a) **Template databases** — PostgreSQL's `CREATE DATABASE ... TEMPLATE`
   mechanism produces a byte-level clone of a pre-built schema in milliseconds.
   CIU could manage a "prepare step": bring up the DB container once, apply DDL,
   mark it as a template, then per-candidate tests clone from it instead of
   re-applying DDL.

b) **Savepoint-based step resets** — within a single test session, individual
   test cases that mutate state can wrap each step in a PostgreSQL SAVEPOINT
   and roll back after assertion, avoiding any cross-test contamination without
   restarting the container.

**Upstream facilitation needed:**

| Layer | What should provide it |
|-------|----------------------|
| **Assay** | Declare `judge.mutation.database_template` naming a prepare-step artifact; assay manages template creation before baseline and clones per mutant |
| **CIU v8** | Own infrastructure provisioning: bring up DB container, execute prepare script, mark template ready; expose connection details to lanes via rendered config |
| **run-gate / ciu gate** | Pass `TEMPLATE_DB_DSN` to lane commands so they clone instead of building from scratch |

**Proposed config surface (CIU v8):**

```toml
[testing.lanes.sql-mutation]
database_template = "workflow-core-schema"
prepare_script = "scripts/schema-apply.sh"
reset_strategy = "template"          # "template" | "savepoint" | "container"

[testing.templates.workflow-core-schema]
image = "timescale/timescaledb-ha:pg18"
init_scripts = ["infra/db-init/init-scripts/*.sql"]
ready_query = "SELECT 1 FROM schema_meta WHERE key = 'schema_ready'"
```

CIU brings up the container, applies init scripts, verifies readiness via
`ready_query`, then marks the database as a PostgreSQL template. Each lane
execution clones from it. Assay coordinates the clone-per-mutant lifecycle
during R2 execution.

This eliminates the need for consumer projects to hand-roll docker provisioning
inside wrapper scripts (which is what dstdns currently does in
`scripts/p128-assay-schema.sh` and `scripts/p129-assay-schema.sh`) and makes
the pattern reusable across all projects with database-dependent mutation lanes.

### 10.3 Secrets architecture — open design question

**Current state (S4):** six directives (`ASK_VAULT`, `GEN_TO_VAULT`,
`GEN_LOCAL`, `ASK_EXTERNAL`, `ASK_FILE`, `GEN_EPHEMERAL`) resolve secrets
into `.ciu/secrets/<name>` store files. Vault-backed directives require a
running Vault instance.

**V8 introduces `ciu.secrets.toml`** as a sibling of the global config file.
Its role and relationship to S4 needs explicit definition:

| Deployment mode | Where secrets live | How they reach containers |
|----------------|-------------------|--------------------------|
| **With Vault** | Vault KV2 (S4 directives unchanged) | Materialized to `.ciu/secrets/` by CIU, mounted via overlay |
| **Without Vault** | `ciu.secrets.toml` (gitignored) | Values read directly from this file; materialized to `.ciu/secrets/` |
| **Mixed** | Vault for infrastructure secrets; `ciu.secrets.toml` for 3rd-party API keys, dev credentials | Both paths feed into the same `.ciu/secrets/` store |

`ciu.secrets.toml` is NOT a replacement for S4 — it is an additional source.
When Vault is available, S4 directives work unchanged. When it is not,
`ciu.secrets.toml` fills the gap for projects that cannot run Vault.

**Open questions requiring further design:**

1. **Project-level secrets.** Where does a stack's own secret go?
   Options:
   - `<stack>/ciu.secrets.toml` (alongside ciu.defaults.toml.j2)
   - A `[secrets]` table in `ciu.toml.j2` (the sparse override layer)
   - Keep using S4 per-stack secret tables with `GEN_LOCAL`

2. **Vault bootstrap.** The Vault service itself needs its own unlock key
   after initialization. Currently dstdns stores this in the vault stack's
   `[state]` table. Should v8 formalize this pattern?

3. **Service-level access tokens.** For services that need a Consul token
   or Vault AppRole credentials at runtime, the current pattern is:
   hook provisions token → writes to Vault → service reads via ASK_VAULT.
   In a vaultless deployment, where does this go? Options:
   - `ciu.secrets.toml` at global level (shared across stacks)
   - `<stack>/ciu.secrets.toml` (per-stack)
   - Directly in compose environment (current `expose_env` escape hatch)

4. **Mounting into containers.** The proposal does NOT mount
   `ciu.secrets.toml` directly into containers — multiple services per
   stack may need different subsets of its values. Instead, CIU reads it
   during secret resolution and materializes individual files to
   `.ciu/secrets/<name>`, which are then mounted selectively per service.

**Status:** OPEN — needs design before v8 becomes normative. The
`ciu.secrets.toml` file itself is non-controversial; its precedence rules,
project-level placement, and interaction with existing S4 stores need
resolution.

---

## 11. Open issues from adversarial review

These findings from the adversarial review remain relevant and must be
resolved before the proposal becomes normative.

### 11.1 Provenance must gate evidence (was M1)

A passing R0/R1/R2 verdict without provenance verification could describe
stale images. CIU-v8's gate module MUST either:

a) Require a passing `ciu provenance --json` verdict before running R1+,
   recording the provenance result in the test verdict JSON; or

b) Run provenance inline as part of the gate pipeline and fail-closed on
   mismatch unless `--skip-provenance` is explicitly passed.

Option (b) is recommended: it prevents the "I forgot to check" failure mode.

**Status:** OPEN — to be decided before normative.

### 11.2 Rigor provider contracts undefined (was M2)

R0–R6 are vocabulary labels but no contract defines HOW evidence is
attached to each level. What makes a verdict R1-compliant vs R0-only?

Each rigor level needs a provider interface:

```python
class RigorProvider(Protocol):
    level: str                    # "R0", "R1", etc.
    def execute(self, lane, environment) -> RigorEvidence
    def validate(self, evidence: RigorEvidence) -> bool
```

Without this, "R2 mutation testing" is aspirational, not enforceable.

**Status:** OPEN — to be decided before normative.

### 11.3 Assay artifact pinning (was M11)

Current lanes verify pinned assay artifacts by version + SHA256. V8 says
"de-vendor" but provides no replacement mechanism. Even if CIU manages the
assay installation, each verdict MUST record which assay version produced it.

Minimum viable solution: add `judge.version` and `judge.sha256` fields to
the verdict JSON schema. CIU records these from whatever assay binary it
invoked. This is an Assay backlog item (verdict schema extension).

**Status:** OPEN — filed as Assay upstream ask; CIU-v8 must record the
producing judge identity in every verdict.

### 11.4 Changed-file scope refinement (was M4)

The current selection model uses glob patterns matched against changed
files. For monorepos this is underspecified:

```toml
[testing.selection.api]
patterns = ["src/api/**/*.py"]
base_ref = "origin/main"          # default: merge-base with origin/main
exclude_patterns = [
    "**/__pycache__/**",
    "*.pyc",
    "docs/**",                     # docs changes don't trigger api tests
]
scope = ["tests/unit/test_api.py"]
```

Additional fields worth considering:
- `min_changed_lines = 5` — skip scope if fewer than N lines changed (noise filter)
- `requires_services = ["our_api_stack.api"]` — only meaningful when these services are deployed
- Per-package roots for monorepo subprojects with independent test suites

The base diff computation should use Assay's B012 changed-lines mode
(`base..HEAD`) rather than raw file lists, so whitespace-only changes don't
trigger full gate runs.

**Status:** OPEN — refine before implementation; coordinate with Assay's
changed-file detection (B014 bounded output tails).

### 10.4 Execution manifest

**Problem.** Today, gate configuration is split across two independently
authored files (`run-gate.toml` with its `forward_env`, and `assay.toml`
with its `env_passthrough`). Adding a variable at one layer without updating
the other causes silent failures (dstdns P129, §10.1). More broadly, the
facts needed to execute a lane are scattered across config layers, CLI flags,
and runtime state — nothing captures them as a single resolved snapshot.

**Proposal: CIU compiles an immutable ExecutionManifest per gate run.**

The manifest contains ONLY resolved facts — no templates, no references,
no env-var placeholders:

| Field | Source |
|-------|--------|
| selected worktree path | S16 instance record |
| effective project root | resolved from worktree + offset |
| Git common dir | `git rev-parse --git-common-dir` |
| commit / base_ref / diff scope | gate request + git merge-base |
| container/service targets | resolved from `[deploy.groups]` + `[deploy.profiles]` |
| network name | instance identity (S2) |
| user / cwd / argv / timeout | service config + lane declaration |
| resource limits | `[testing.resources]` resolution chain |
| realness map | per-service resolved variant (§1.4a) |
| credential handles | secret NAMES only (never values) |
| artifact paths | output directory, verdict path, coverage file |
| judge identity | version + SHA-256 of resolved assay/judge binary |
| isolation policy | cgroup parent, network mode, mount exclusions |

Secrets stay out of the manifest. It stores secret NAMES plus a redacted
audit fingerprint (e.g. SHA-256 of concatenated values). CIU injects actual
values at execution time via environment variables or mounted files.

This eliminates the dual-allowlist problem because `forward_env` and
`env_passthrough` become COMPILATION OUTPUTS of the manifest, not
independently authored contracts. The manifest lists exactly which env vars
the lane command needs; CIU injects them; no second tool re-filters.

The manifest itself is serialized as JSON at a well-known path inside the
test-runner container (`.ciu-execution-manifest.json`). Lane commands can
read it to discover their own identity, endpoints, and scope without
requiring hash-derived env vars threaded through multiple tools (§10.1
resolution).

---

### 10.5 Judge distribution

**Problem.** Consumer repos currently vendor assay zipapps
(`tools/assay/*.pyz`) with SHA sidecars. This couples consumer releases to
assay releases and creates stale-pin drift.

**Proposal: two-tier delivery model.**

**For non-CIU consumers:** unchanged from today. Ship/install Assay as a
normal wheel or standalone CLI artifact with explicit version/hash pin.
No repo-vendoring required; pip install from GitHub Releases works.

**For CIU consumers:** CIU owns a central judge resolution record:

```toml
[testing.judge]
name = "assay"
version = "==2.3.0"              # constraint, like [build.python]
source = "github-releases"       # trusted source enum
sha256 = "dbbcf2..."             # expected digest of the resolved artifact
cache_dir = "~/.ciu/cache/judges"  # shared across worktrees
```

At gate preparation, CIU:
1. Resolves the version constraint against available releases
2. Verifies SHA-256 of the downloaded/cached artifact
3. Either mounts the verified artifact into the test-runner container,
   OR requires a runner image already carrying the pinned judge
4. Records both the judge digest AND the runner image digest in the
   ExecutionManifest and verdict

Consumer repos declare POLICY (which version, which source, trust
constraints); CIU materializes the VERIFIED TOOL. No vendored zipapps in
consumer repos.

This replaces the current model where each repo pins and verifies its own
copy. Version upgrades happen by changing one line in global config, not
by repinning files across multiple repos.

### 10.6 Lane realness requirements (demand-driven selection)

§1.4a defines how CIU SELECTS realness variants for deployment. This section
defines how LANES declare what they REQUIRE. These are complementary:

- §1.4a answers: "when `ciu up` runs, which variant does each service use?"
- This section answers: "for THIS lane to produce valid evidence, which
  variant must each service be?"

**Lanes declare capability requirements, not infrastructure recipes:**

```toml
[testing.lanes.payment-flow]
rigor = ["R0", "R2"]
requires_realness = { database = "owned-seeded", payment-api = "simulated" }
```

CIU validates at feasibility admission:
1. Is the required variant declared in the service's config?
2. Does the selected environment actually run that variant?
3. Are credentials available for that variant?

If any requirement cannot be met, the lane is assigned **NOT_RUN** with a
reason naming the unsatisfied requirement. The lane is NEVER silently
downgraded to a different variant — a mock-based test result presented as
integration evidence is worse than no result.

This means the resolution order from §1.4a gains a validation step after
selection: lanes check their `requires_realness` against what was actually
selected and refuse to run on mismatch.

---

### 10.7 NOT_RUN semantics

The gate report must distinguish three outcomes per lane:

| Status | Meaning |
|--------|---------|
| **PASS** | Evidence produced and satisfied policy |
| **FAIL** | Executed but failed; evidence shows the failure |
| **NOT_RUN** | Selected/admissible-in-principle but not executed, with reason |

Mid-command failures remain FAIL or ERROR — they must never become skips.
A command that ran and crashed IS evidence.

NOT_RUN reasons are closed vocabulary:

| Reason | Example |
|--------|---------|
| `missing-environment` | No running instance for the named environment |
| `realness-mismatch` | Required variant not deployed (e.g. needs live Stripe but got mock) |
| `missing-credentials` | Secret referenced by the lane's realness variant unavailable |
| `unavailable-runner` | Test runner container/image not available |
| `no-matched-scope` | Changed files don't match any pattern in this scope |

**Ship intents fail closed on any required NOT_RUN.** A release gate with
a NOT_RUN lane has incomplete evidence and must not pass. Fast-feedback
intents may surface NOT_RUN as a warning while still passing, because the
purpose is quick iteration, not certification.

```toml
[testing.intents.quality]
rigor = ["R0", "R1", "R2"]
not_run_policy = "fail"           # default for ship gates

[testing.intents.smoke]
rigor = ["R0"]
not_run_policy = "warn"           # fast feedback can tolerate gaps
```

---

### 10.8 Authored config vs derived state

To prevent ambiguity about where truth lives:

**Authored (SSOT):**
- `ciu.global.defaults.toml.j2` — repository-authoritative baseline
- `ciu.global.toml.j2` — intentional committed override
- `<stack>/ciu.defaults.toml.j2` — stack-authoritative baseline
- `<stack>/ciu.toml.j2` — optional stack override

**Derived (never authored):**
- `ciu.global.toml` — rendered output of the chain above
- `.ciu-execution-manifest.json` — compiled gate plan (§10.4)
- `.ciu/secrets/*` — materialized secret values
- `.ciu/ciu.compose.overlay.yml` — machine-derived wiring

The ExecutionManifest is a COMPILATION of the authored config plus runtime
facts. It is never hand-edited. Changing behavior requires changing the
authored source, re-rendering, and re-compiling.

This distinction MUST be normative so tooling (and agents) understand that
modifying the manifest directly is meaningless — it will be overwritten at
the next gate preparation.

---

### 10.9 Gate contracts and ownership boundaries

CIU-v8 introduces four outer contracts that structure the gate lifecycle:

| Contract | Owner | Purpose |
|----------|-------|---------|
| **GateRequest** | CIU | What gate the operator/agent desires: intent, environment name, rigor overrides, scope overrides |
| **ExecutionManifest** | CIU | The resolved plan: every fact needed to execute (§10.4) |
| **LaneResult** | CIU + judge | Per-lane orchestration outcome: status, timing, embedded Assay verdict |
| **GateReport** | CIU | Aggregate: all LaneResults, policy decision (pass/fail), resource usage summary |

Ownership boundaries:

- **CIU owns:** GateRequest schema, ExecutionManifest schema,
  GateReport schema, and the outer envelope of LaneResult (status, timing,
  artifact paths).
- **Assay owns:** verdict internals inside LaneResult (coverage data,
  mutation results, output tails). CIU embeds the verdict verbatim without
  parsing or restructuring it.
- **Shared protocol:** the cross-tool envelope (how CIU invokes assay and
  receives results) carries explicit semantic versioning. Both sides
  validate against it.

These schemas initially live normatively in the CIU v8 spec. Once another
judge or orchestrator appears, they should be extracted into a small
versioned gate-contract package.

**Assay verdict embedding:** CIU adopts Assay's existing verdict schema as
the embedded judge record inside LaneResult. It does NOT invent a competing
format. If Assay extends its verdict schema (e.g. B014 output capture),
CIU passes it through unchanged.

---

### 10.10 Base commit from gate request, not lane config

**Problem.** dstdns P128 revealed that `assay.toml` hardcodes a base commit
in its R1 lane configuration. In a worktree-based workflow, the correct
base depends on WHERE the gate runs (which branch, which merge-base), not
on a static value in lane config.

**Proposal: the base ref is part of the GateRequest, not lane policy.**

Lane configuration declares only WHETHER changed-line judging is required:

```toml
[testing.lanes.api-changed-lines]
changed_line_judging = true       # "judge only lines I changed"
# NO base_ref here — that comes from the request
```

The GateRequest supplies the actual base:

```bash
ciu gate --intent coverage --environment ci-build-42 --base origin/main
```

CIU resolves it via `git merge-base` and writes the resolved commit into
the ExecutionManifest. The lane reads it from there, not from its own config.

This makes the same lane definition reusable across branches, PRs, and
local development without editing config per context.
