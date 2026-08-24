# CIU v8 Proposal — Native Testing Gate, Logical Services, and Environment Instances

**Status:** PROPOSAL — not yet normative
**Author:** Derived from dstdns repair-program design sessions (2026-08-22–23)
**Supersedes (eventually):** run-gate-project standalone tool; current `[deploy.phases]` model and other config schema
**Target:** CIU v8.0.0 (breaking; `revision` key gates config acceptance)

**Proposal revision:** 1.1
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

When an assay lane command fails, the verdict says `COMMAND_FAILED` with zero stdout/stderr context. This forced five manual reproductions during dstdns P121 debugging. Assay should capture bounded output (≤64KB) from failed commands and persist it in the verdict artifact. Filed separately upstream; independent of this proposal.

#### 1.12 Higher Rigor

- Assay should provide evidence contracts for higher rigor, not become a property-testing/fuzzing engine. Hypothesis, proptest, ClusterFuzzLite, AFL-style tools, and domain-specific fuzzers remain better producers.
- The sensible boundary: specialized tools generate cases and produce structured evidence; Assay validates thresholds, binds evidence to commit/input, emits verdicts, and fails loudly. This also matches its existing non-goals and avoids reinventing mature tooling.
- For mutation rigor specifically, finishing B012 resume/checkpointing and provable sharding is higher value than inventing an “R4.” Property/fuzzing can later enter as another evidence tier/provider contract once CIU actually needs a second judge producer.

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

---

## 3. Logical services and realness

### 3.1 Declaration

Each logical service declares its kind and available realness variants:

```toml
# Metadata (shared across all variants)
[service.api-handler]
kind = "internal"                # "internal" | "3rd_party"
description = "HTTP request handler"

# Variant: live (real implementation)
[service.api-handler.live]
location = "applications/api-handler"
port = 8080
health = "/health"
init_requires = ["postgres:db/demo"]
depends_on = ["redis", "payment-api"]
allow_degraded_start = true      # default true

# Variant: mock (stub implementation)
[service.api-handler.mock]
implementation = "tests/mocks/api_handler_mock.py"
```

For third-party services:

```toml
[service.payment-api]
kind = "3rd_party"
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
```

Other idea: differentiate `[service.internal.*]` vs `[service.3rd_party.*]` up front for clarity. could there be other types than internal and 3rd party?

### 3.2 Validation rules

- Every sub-table key under `[service.<name>]` (other than `kind`, `description`) MUST match a valid realness level for that service's `kind`.
- Internal valid levels: `mock`, `live`
- Third-party valid levels: `mock`, `simulated`, `owned-seeded`, `live`
- At least one variant MUST be declared per service.
- `init_requires` references use S13 typed-reference grammar (unchanged).
- Secrets referenced in a variant MUST have corresponding sections in `ciu.secrets.toml` or Vault paths. Missing credentials fail at config-validation time when that realness level is selected, not at runtime.

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
services = ["postgres", "redis", "vault"]

[deploy.groups.app]
description = "Application layer"
services = ["api-handler", "notification-service"]

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
services = ["postgres", "redis"]

[deploy.profiles.two-host.hosts.host-b]
services = ["api-handler", "notification-service"]

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
[testing.selection.api-handler]
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
# LOGICAL SERVICES
# ------------------------------------------------------------

[service.api-handler]
kind = "internal"
description = "HTTP request handler"

[service.api-handler.live]
location = "applications/api-handler"
port = 8080
health = "/health"
init_requires = ["postgres:db/demo"]
depends_on = ["redis", "payment-api"]
allow_degraded_start = true

[service.api-handler.mock]
implementation = "tests/mocks/api_handler_mock.py"

[service.notification-service]
kind = "internal"
description = "Sends emails/SMS (not yet implemented)"

[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"

[service.postgres]
kind = "3rd_party"
description = "Primary relational database"

[service.postgres.owned-seeded]
location = "infra/postgres"
image = "timescale/timescaledb:pg18"
port = 5432
seed_data = "fixtures/seed.sql"

[service.payment-api]
kind = "3rd_party"
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

[service.redis]
kind = "3rd_party"
description = "Cache and pub/sub"

[service.redis.owned-seeded]
location = "infra/redis-core"
image = "redis:7-alpine"
port = 6379

# ------------------------------------------------------------
# SERVICE GROUPS
# ------------------------------------------------------------

[deploy.groups.core]
description = "Infrastructure foundation"
services = ["postgres", "redis"]

[deploy.groups.app]
description = "Application layer"
services = ["api-handler", "notification-service"]

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

[testing.selection.api-handler]
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
ciu gate --intent smoke --selection api-handler --environment none
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
