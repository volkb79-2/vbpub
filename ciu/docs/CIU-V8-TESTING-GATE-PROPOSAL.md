# CIU v8 Proposal — Native Testing Gate, Logical Services, and Environment Instances

**Status:** PROPOSAL — not yet normative
**Author:** Derived from dstdns repair-program design sessions (2026-08-22–23)
**Supersedes (eventually):** run-gate-project standalone tool; current `[deploy.phases]` model and other config schema
**Target:** CIU v8.0.0 (breaking; `revision` key gates config acceptance)

**Proposal revision:** 1.10 (§10.14/§11.7 added: M7 shared-infra sharpened, still open)
**Updated:** 2026-08-27 (§10.3 secrets architecture resolved + realization-kind
addressing corrected; §10.11 instance run mutex + §10.12 concurrency model +
§10.13 realness immutability added; B1 addressing debt reconciled throughout
§1.5/§1.15/§3.1–3.3/§4.1–4.2/§8 to `V8-REALIZATION-GRAPH.md`'s
entity/realization split; §11 bookkeeping corrected for M3/M8/M11; CIU's
native-vs-hook Vault boundary clarified in CONFIG.md; §1.17/§4.2/§8 topology
schema reconciled onto one key naming (`transport`/`address`) with CIU's
declarative-only scope stated — dstdns/vbpub joint session)
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

**Realness is fixed at deploy time for an already-running instance — this
resolution order never re-runs against a live one (added 2026-08-27, see
§10.13).** Everything above resolves what to select on THIS `ciu up`. It
does not describe (and previously left ambiguous) what happens if the
instance is already up under a different selection — §10.13 closes that
gap: `ciu up` refuses rather than silently reconfiguring.

### 1.5 Why named sub-tables for realness variants

*Updated 2026-08-27 for consistency with §3.1's entity/realization split —
the field shown is `realized_by` (a RealnessVariant pointing at its
Realization), not connection details inlined on the variant itself; the
schema-design point below is unaffected by that change, only the example is.*

```toml
# Option A (CHOSEN): level name IS the key
[service.payment-api.live]
realized_by = "external.payment_api_stripe"

[service.payment-api.mock]
implementation = "tests/mocks/payment_mock.py"

# Option B (REJECTED): array with realness field
[[service.payment-api]]
realness = "live"
realized_by = "external.payment_api_stripe"
```

Option A wins because:
- TOML enforces uniqueness (can't accidentally declare two `live` variants)
- Direct key lookup: `config["service"]["payment-api"]["live"]`
- Invalid level names are structurally obvious
- Templates access variants cleanly: `{{ service.payment_api.live.realized_by }}`
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

> **Note (2026-08-26, dstdns/vbpub joint design session — updated after
> further verification the same day): the `<stack>.<service>` addressing
> below is flawed, not merely worth reconsidering.** It couples a logical
> service's identity to wherever it physically runs today — rename the
> stack, or move the database to a managed provider, and every consumer's
> `init_requires` has to change with it, even though nothing about what the
> consumer *wants* changed. `V8-REALIZATION-GRAPH.md` in this directory
> works through the fix: split the logical service (`[service.<name>]`,
> stable) from its realization (`[ciu_stack.<name>]` / `[external.<name>]`,
> physical), joined by a `realized_by` pointer on a per-realness-level
> variant — the same flat-name-join shape §1.16's `[local_stack.<name>]`
> already establishes for a different purpose, reused here as precedent.
>
> Same session also re-validated §4.3's topological-sort claim against a
> real fresh dstdns bring-up and initially found what looked like two
> genuine ciu gaps (an undeclared vault-liveness dependency for every
> `GEN_TO_VAULT` consumer; `pg:schema/dstdns`'s completion having no
> expressible ref kind). Both turned out, on further verification, to be
> the same class of mistake as ciu's own withdrawn **CIU-45**: a working
> mechanism (`stack:<name>:healthy|completed`, resolved by a live
> docker-inspect probe) already existed and dstdns simply never declared
> it — fixed in dstdns's own config (dstdns@d1688765), not a ciu defect.
> What *is* a real, newly-filed ciu gap: `ciu check`'s static graph lint
> doesn't know `stack:*` refs resolve by live probe rather than a
> `provides` declaration (**CIU-63**), `ciu check` doesn't run automatically
> before `ciu up` (**CIU-64**), and `validate_config()` findings have no
> WARN/ERROR severity (**CIU-65**) — all three in
> `KNOWN_ISSUES_TODO_BACKLOG.md`. Read `V8-REALIZATION-GRAPH.md` in full
> before treating the `<stack>.<service>` addressing below as settled — this
> note is deliberately not a substitute for it.
>
> **Applied 2026-08-27:** the fix this note promised is below — every
> example in this section, and in §3 and §8, now uses the entity/realization
> split rather than the flawed compound-key form. `type` as a field is gone
> too (per `V8-REALIZATION-GRAPH.md`'s own resolution): which top-level table
> an entry sits under — `[ciu_stack.*]`, `[compose_stack.*]`, `[external.*]`
> — IS its type, so a validator rejects fields that don't belong (a
> `location` on an `[external.*]` entry) for free, rather than needing a
> second check against a separate `type` value.

**Realizations** — WHAT the physical thing is and WHERE to find it. One
entry per stack (`location` + the stack's *aggregate* `init_provides`,
regardless of which container inside it actually produced a given fact —
see `V8-REALIZATION-GRAPH.md` on why that internal detail doesn't cross the
boundary), plus one `RealizedService` sub-entry per named container that
needs its own deployment facts (`image`, `port`, `one_shot`,
`init_requires`):

```toml
[ciu_stack.our_db_stack]
location = "infra/db-core"       # filesystem path to the stack directory
init_provides = ["pg:db/demo", "pg:role/controller", "minio:user/worker-io"]

[ciu_stack.our_db_stack.postgres]
port = 5432
image = "timescale/timescaledb-ha:pg18"

[ciu_stack.our_db_stack.minio]
port = 9000
image = "minio/minio:latest"

# Instance named `compose_stack` (not e.g. `legacy_stack`) so the kind
# prefix and instance name match at a glance — see §3.1.
[compose_stack.compose_stack]
location = "/opt/my_legacy_service"

[compose_stack.compose_stack.service1]
port = 1234                        # live: how we reach the real service

[external.payment_api_stripe]
base_url = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

[ciu_stack.api_handler_stack]
location = "applications/api-handler"

[ciu_stack.api_handler_stack.api]
port = 8080
health = "/health"
init_requires = ["our_main_db", "vault:secret/db/postgres/api_password"]
depends_on = ["our_object_store", "payment-api"]  # usage deps: logical names too, never stacks
allow_degraded_start = true
```

**LogicalServices** — the stable name every consumer depends on, uniform
regardless of what realizes them. `realized_by` always names a *Realization*
(a stack or an external entry), never a specific `RealizedService` inside
one — the realization's aggregate `init_provides` is what gets checked
against the logical service's `contract`, not any one container's own
output. The value is kind-qualified (`"ciu_stack.our_db_stack"`, not
`"our_db_stack"` — see §3.1's correction, applied here too): Realization
names live in three separate top-level tables, so a bare name has no
defined way to say which one it means the moment the same name exists in
more than one:

```toml
[service.our_main_db]
description = "The application's database"
contract = ["pg:db/demo", "pg:role/controller"]

[service.our_main_db.live]
realized_by = "ciu_stack.our_db_stack"

[service.our_main_db.mock]
implementation = "tests/mocks/postgres_mock.py"

[service.our_object_store]
contract = ["minio:user/worker-io"]

[service.our_object_store.live]
realized_by = "ciu_stack.our_db_stack"        # same stack backs two distinct logical services

[service.our_api]
description = "HTTP request handler"

[service.our_api.live]
realized_by = "ciu_stack.api_handler_stack"

[service.our_api.mock]
implementation = "tests/mocks/api_handler_mock.py"

[service.legacy_service_1]
description = "A service reached through a legacy compose project"

[service.legacy_service_1.live]
realized_by = "compose_stack.compose_stack"

[service.legacy_service_1.mock]
implementation = "tests/mocks/legacy_service1_mock.py"

[service.payment-api]
description = "External payment processor"

[service.payment-api.live]
realized_by = "external.payment_api_stripe"

[service.payment-api.mock]
implementation = "tests/mocks/stripe_mock.py"

[service.notification-service]
description = "Sends emails/SMS (not yet implemented)"

[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"
```

A `LogicalService` with only a `.mock` variant (`notification-service`
above) needs no Realization table at all — there is nothing to locate on
disk or connect to.

**What table a Realization lives under determines how CIU deploys it —
never a `type` field, and it says nothing about which realness levels the
LogicalServices pointing at it may declare, since realness is a property of
the logical side, not the physical one:**

| Realization table | How CIU deploys |
|---|---|
| `[ciu_stack.*]` | Full pipeline (render, secrets, hooks, compose) |
| `[compose_stack.*]` | `docker compose up` on a pre-existing file |
| `[external.*]` | Nothing deployed; connection facts only |
| *(none)* | Nothing deployed; a LogicalService with only `.mock`, files referenced directly |

**Service reference format:** always the bare logical name —
`our_main_db`, `our_api`, `payment-api` — never a stack path and never a
compound key. `init_requires`, `depends_on`, deployment groups, and testing
selection all reference logical names exclusively. Renaming, moving, or
re-realizing a stack is a one-line `realized_by` change; no consumer's
reference ever needs to follow it.


**Problem.** Each stack directory needs per-service deployment config
(ports, env vars, hostdirs). Currently dstdns derives a root key from the
directory name (`infra/db-core` → `[db_core]`). This is implicit and fragile.

**Reconciled with the entity/realization split above (2026-08-27):** this
used to propose a THIRD, separate per-stack layer (`[local_stack.<name>]`),
joined to the global `[service.<name>]` registry by matching key name. That
join is now redundant — `[ciu_stack.<stack>.<svc>]` (or
`[compose_stack.<stack>.<svc>]`) declared directly in the stack's own file
*is already* the compound, globally-addressable key a `realized_by` pointer
targets. There is no second name to keep in sync, so the join step this
section originally existed to define no longer has anything to do. The part
worth keeping — a stack's own file owning its own deployment wiring — is
preserved exactly:

```toml
# infra/db-core/ciu.defaults.toml.j2
[ciu_stack.our_db_stack.postgres]
port = 5432
image = "timescale/timescaledb-ha:pg18"
health_endpoint = "/ready"

[ciu_stack.our_db_stack.minio]
port = 9000
image = "minio/minio:latest"
```

Cross-stack references still use
`{{ topology.services.postgres.internal_host }}`.

**Rules (normative), carried forward minus the join step they existed only
to support:**

1. One stack directory MAY declare multiple `[ciu_stack.<stack>.<svc>]`
   entries — normal when a stack deploys several containers (e.g. db-core
   running postgres, minio, and pgadmin).
2. The Realization's own `location` (declared once, on the bare
   `[ciu_stack.<stack>]` table) points to the stack directory. Renaming or
   moving that directory requires updating only that one `location` value;
   every RealizedService sub-table under it is unaffected.
3. A `RealizedService` with no `LogicalService` pointing at it via
   `realized_by` is valid — it means "this stack runs something nothing
   else depends on." The global registry exists for cross-stack references,
   not as a gatekeeper for what a stack may run.
4. Hooks move from the old `[<root>.hooks]` convention to
   `[ciu_stack.<stack>.<svc>.hooks]`, keeping hook declarations co-located
   with the service they configure.

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

**Superseded 2026-08-27 (B1-shaped reconciliation, same pattern as the
addressing debt elsewhere in this doc) — this section's own "defer" stance
no longer holds, but its OWN key naming does.** This was written first and
deliberately kept minimal ("defer full endpoint/route modeling"); §4.2 and
§8's full sample config were written later and went ahead and built the
fuller version anyway (per-host addresses, worked two-host/three-host
examples) without ever circling back to say so — leaving a stale "defer"
note contradicted by the sections that came after it, and (initially
mis-diagnosed while fixing this) an apparent naming split. Checked
directly rather than assumed: `transport` is actually used here AND in
§8's full sample config; only §4.2 alone drifted to `mode`. **Resolved:
`transport` is the key name** (§1.17 and §8 agree; §4.2 is the outlier,
corrected to match) — **`address` is the per-host field name** (from here;
`wireguard_ip`, which had crept into both §4.2 and §8, is dropped
everywhere — it only made sense for one of `transport`'s three values, and
a proxy-mode host's reachable address was never meaningfully "a WireGuard
IP"). §4.2 and §8 are corrected to match this section, not the other way
around — this section's schema is normative.

**Scope boundary (added 2026-08-27, same shape as CONFIG.md's clarified
Vault boundary): CIU is declarative-only here — it never provisions the
transport itself.** `[topology]` states facts CIU's own templates and
`ciu check` can read and validate; it does not cause CIU to configure a
WireGuard interface, exchange keys, or set up a reverse proxy. Establishing
the actual tunnel/proxy is entirely a host-provisioning or hook-authored
concern, outside CIU's own source — the same boundary as Vault: CIU knows
addresses, not how the network under them came to exist.

**New `ciu check` rule (added 2026-08-27):** two static completeness
checks, config-only, no live probe — (1) every host named in a
`[deploy.profiles.<name>.hosts]` list has a matching
`[topology.hosts.<host>]` entry whenever `transport != "direct"`; (2) every
`[topology.hosts.<host>]` entry declares `address` whenever
`transport != "direct"`. §2.7's 12-stage table has no row for
deployment/topology shape yet — this needs one added there, not shoehorned
into an existing stage; flagging rather than guessing which number it
should be, since that table's own ordering wasn't re-examined as part of
this change. An incomplete topology declaration should become a
`ciu check` failure, not a `ciu up`-time surprise, once that's wired in.

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

V8 adds an inline list on each RealizedService entry (corrected 2026-08-27:
this section still showed the pre-reconciliation `[local_stack.*]` layer
§1.15 established is redundant — `env_required` lives directly on the
`[ciu_stack.<stack>.<svc>]`/`[compose_stack.<stack>.<svc>]` table itself,
same as every other per-container fact):

```toml
[ciu_stack.our_db_stack.postgres]
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
| 3. Secrets | Directive grammar (S4), placement (S4.1/S4.5), name uniqueness (S4.6), Vault ordering (S7.6), Vault presence vs. directives (below, added 2026-08-27) | None |
| 4. Provisioning | requires/provides grammar (S13), graph lint, cycle detection | None |
| 5. Governance | Shape checks (S15.2), cgroup parent resolution | None |
| 6. Configfile | Template existence, target path validity, schema file existence (S5) | None |
| 7. Registry | Built-in Pydantic model validation (§2.6 Option B); consumer models via optional `validate(config)` callable | None |
| 8. Hooks — load | Every declared hook file exists and exports `run` or `Hook` with `run` method (S9.2) | None |
| 9. Hooks — preflight call | Call each hook's `validate_config(config, ctx)` method if it defines one; report errors without executing `run()` | None |
| 10. Compose render | Full compose template rendering with guarded config (S4.21) | Writes nothing; renders to memory |
| 11. Overlay | Generate overlay in memory; leak scan (S4.22) | Writes nothing |
| 12. Consumption | Declared-vs-consumed secret cross-check (S4.20) | None |

**Vault presence becomes a static, config-only fact — not something only
discovered at runtime (added 2026-08-27, operator observation).** Today
(S4.16, shipped), a project with `GEN_TO_VAULT`/`ASK_VAULT` directives but
no resolvable Vault token aborts only when `materialize_secrets()` actually
runs — a live-path failure, well into `ciu up`. But whether a project has
Vault at all is already fully determined by config alone:
`[topology.services.vault]`/`[vault].stack_path` either declares one or
doesn't (see CONFIG.md's clarified `[vault]` boundary — CIU's own native
Vault awareness is exactly this address-plus-token-plus-KV2-client, nothing
more). So stage 3 gains a static rule: **if no Vault is declared, every
`GEN_TO_VAULT`/`ASK_VAULT` directive anywhere in the config is an
unconditional `ciu config check` failure, naming the stack, directive, and
secret** — not "will fail at runtime if a token can't be found," but
"cannot ever succeed, known from config alone, before any container
starts." This makes "does this project use Vault" a config-derived fact
rather than an implicit runtime discovery: declare the pointer and Vault
directives work; omit it and they're rejected immediately, with a clear
reason, at the same time as every other static config error.

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

**Reconciled with `V8-REALIZATION-GRAPH.md` (2026-08-27) — B1's addressing
debt closed.** This section previously declared logical stacks and services
under one compound-keyed `[service.<stack>.<svc>.<level>]` table, with a
`type` field selecting deployment behavior. `V8-REALIZATION-GRAPH.md`
(2026-08-26) identified this as a real flaw, not a style preference: it
couples a consumer's dependency reference to wherever a service happens to
run *today*, so moving or renaming a stack forces every consumer's
`init_requires` to change too, even though nothing the consumer actually
wants changed. The declaration below is the fix, applied throughout this
document as of this date (§1.15, §8, and this section) — see
`V8-REALIZATION-GRAPH.md` for the full worked derivation; what follows is
the normative shape.

The global config declares three kinds of things, in three different
top-level tables, never joined by a `type` field:

- **`LogicalService`** (`[service.<name>]`) — the stable identity every
  consumer depends on. Carries `description` and a `contract` (the typed
  facts any realization must provide). No deployment detail ever lives
  here.
- **`RealnessVariant`** (`[service.<name>.<level>]`, `level` one of `live`,
  `mock`, `owned-seeded`, `simulated`) — either `realized_by = "<kind>.<name>"`
  (points at a Realization, kind-qualified — see below) or
  `implementation = "<path>"` (a mock needs nothing else).
- **Realization** (`[ciu_stack.<name>]`, `[compose_stack.<name>]`, or
  `[external.<name>]`) — the physical thing satisfying it: `location` (the
  first two only) plus the stack's aggregate `init_provides`. Each may carry
  `RealizedService` sub-tables (`[ciu_stack.<name>.<svc>]` etc.) for
  per-container facts (`image`, `port`, `one_shot`, `init_requires`) when a
  stack runs more than one thing.

**`realized_by` is kind-qualified, not a bare name (corrected 2026-08-27).**
`V8-REALIZATION-GRAPH.md`'s own worked examples wrote `realized_by = "db_core"`
— a bare name — and this document's first pass at reconciling B1 copied that
convention verbatim. It has a real ambiguity: with three separate top-level
tables (`ciu_stack`, `compose_stack`, `external`), nothing stops
`ciu_stack.foo` and `external.foo` from both existing, and a bare
`realized_by = "foo"` would have no defined way to say which one it means —
none of the three governing documents states a rule for that case, because
none of them anticipated needing one. Qualifying the reference
(`realized_by = "external.payment_api_stripe"`) removes the ambiguity by
construction instead of requiring a new cross-table global-uniqueness
validation rule to police it. This is a correction to the examples below,
not a new field — `realized_by`'s existing single string value now always
carries its Realization's own table path.

```toml
# ────────────────────────────────────────────────────
# Realizations — CIU-managed stack
# ────────────────────────────────────────────────────

[ciu_stack.our_db_stack]
location = "infra/db-core"
init_provides = ["pg:db/demo", "pg:role/controller", "minio:user/worker-io"]

[ciu_stack.our_db_stack.postgres]
port = 5432
image = "timescale/timescaledb-ha:pg18"
init_requires = []                  # nothing needed before postgres starts

[ciu_stack.our_db_stack.minio]
port = 9000
image = "minio/minio:latest"

[ciu_stack.api_handler_stack]
location = "applications/api-handler"

[ciu_stack.api_handler_stack.api]
port = 8080
health = "/health"
init_requires = ["our_main_db", "vault:secret/db/postgres/api_password"]
depends_on = ["our_object_store", "payment-api"]
allow_degraded_start = true         # default true

# ────────────────────────────────────────────────────
# Realization — COMPOSE stack (pre-existing docker-compose.yml).
# Instance named `compose_stack` (not e.g. `legacy_stack`) deliberately —
# echoing the kind prefix in the example's own instance name makes it
# unmistakable at a glance which table you're reading, at the cost of the
# repeated word in `[compose_stack.compose_stack]` below.
# ────────────────────────────────────────────────────

[compose_stack.compose_stack]
location = "/opt/my_legacy_service"

[compose_stack.compose_stack.service1]
port = 1234                        # live: how we reach the real service

# ────────────────────────────────────────────────────
# Realization — external, never deployed
# ────────────────────────────────────────────────────

[external.payment_api_stripe]
endpoint = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

# ────────────────────────────────────────────────────
# LogicalServices — the names every consumer actually references
# ────────────────────────────────────────────────────

[service.our_main_db]
contract = ["pg:db/demo", "pg:role/controller"]

[service.our_main_db.live]
realized_by = "ciu_stack.our_db_stack"

[service.our_main_db.mock]
implementation = "tests/mocks/postgres_mock.py"

[service.our_object_store]
contract = ["minio:user/worker-io"]

[service.our_object_store.live]
realized_by = "ciu_stack.our_db_stack"

[service.our_api]
[service.our_api.live]
realized_by = "ciu_stack.api_handler_stack"

[service.our_api.mock]
implementation = "tests/mocks/api_handler_mock.py"

[service.legacy_service_1]
[service.legacy_service_1.live]
realized_by = "compose_stack.compose_stack"

[service.legacy_service_1.simulated]
image = "wiremock/wiremock:latest"
stub_mappings = "fixtures/legacy_stubs/"

[service.payment-api]
[service.payment-api.live]
realized_by = "external.payment_api_stripe"

[service.payment-api.mock]
implementation = "tests/mocks/stripe_mock.py"

# A LogicalService with only `.mock` needs no Realization at all —
# nothing to locate on disk, nothing to connect to.
[service.notification-service]
[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"
```

**Key structural rules:**

- Realizations, RealnessVariants, and LogicalServices are three separate
  top-level table families — never nested inside one compound key.
- `RealizedService` sub-tables exist only when a stack needs per-container
  facts; a Realization with nothing container-specific to say (e.g. a
  one-service stack) may carry those facts directly on its bare
  `[ciu_stack.<name>]` table instead.
- Realness is ALWAYS a property of the LogicalService, never the
  Realization. A COMPOSE-backed logical service can be `mock` while a
  CIU-backed one runs `live` — which top-level table backs it says nothing
  about which realness levels are meaningful for it.
- `init_requires` and `depends_on` reference OTHER LogicalServices by their
  bare name (`our_main_db`, `payment-api`) or external typed references
  (`vault:secret/...`, `pg:db/...`) — never a stack path, never a compound
  key, and never `[external.<name>]`/`[ciu_stack.<name>]` directly even
  when the dependency happens to be external (confirmed 2026-08-27,
  operator question: shouldn't a dependency on an external service address
  the Realization instead? No — that would reintroduce exactly the coupling
  the entity/realization split exists to remove: if `geo-location`'s
  backing later moved from `[external.geo_ip_provider]` to a self-hosted
  `[ciu_stack.geo_ip_self_hosted]`, every consumer referencing the
  Realization directly would need to change; a consumer referencing the
  bare LogicalService name is unaffected — only its `realized_by` pointer
  changes). A `RealizedService`'s OWN `init_requires` (e.g. a one-shot job
  needing another stack's admin access first) may reference a LogicalService
  the same way.
- `realized_by`, by contrast, IS kind-qualified (`"ciu_stack.our_db_stack"`,
  not `"our_db_stack"`) — the opposite rule from the bullet above, and
  deliberately so: `LogicalService` names live in exactly one table
  (`service.*`), so a bare reference to one is never ambiguous; Realization
  names live in three separate tables, so a bare reference to one is
  ambiguous the moment the same name is reused across two of them.
- **A dependency on an `[external.*]`-realized LogicalService never affects
  startup order, even when declared as `init_requires` (added 2026-08-27,
  surfaced by asking "how does a real consumer — not a test — declare it
  needs a 3rd-party service, e.g. a worker calling a geo-location API").**
  The mechanism is the SAME as any other dependency —
  `[ciu_stack.worker_stack.worker] depends_on = ["geo-location"]` or
  `init_requires = ["geo-location"]`, referencing `[service.geo-location]`
  exactly like `api_handler_stack.api` already references `payment-api`
  above — nothing new is needed to express "I use this 3rd-party service."
  But an `[external.*]` Realization has no container to wait for starting,
  so the reference does no topological-sort work at all when it resolves to
  one. What it DOES still do: (a) trigger §3.3's secret preflight (the
  consumer isn't allowed to start until the external's declared secrets
  resolve), and (b) if declared as `depends_on` rather than `init_requires`,
  feed into degraded-health reporting exactly as any other usage dependency
  does. `init_requires` naming an external is therefore meaningful only for
  its secret-preflight effect, never for ordering — worth stating
  explicitly rather than leaving a reader to assume it participates in the
  topological sort the way an internal dependency does.

**Note on the `simulated` realness variant above
(`legacy_service_1.simulated` carrying `image`/`stub_mappings` directly,
with no `realized_by`):** this is a judgment call, not something
`V8-REALIZATION-GRAPH.md` itself worked out — its own examples only cover
`live`/`mock` variants pointing at real infra or carrying `implementation`.
A `simulated` variant that runs an actual container (a wiremock double) is
itself a small Realization in every sense that matters, so the fully
consistent shape would give it its own `[ciu_stack.<synthetic-name>]` (or
`[compose_stack.*]`) entry and point to it via `realized_by`, the same as
any other variant. Inlining `image`/`stub_mappings` straight onto the
variant, as shown, is simpler for a one-container stub but is an
inconsistency worth resolving explicitly before this becomes normative,
not a settled part of the model.

### 3.2 Validation rules

- Every sub-table key under `[service.<name>]` MUST match a valid realness
  level (`live`, `mock`, `owned-seeded`, `simulated`). Other key names are
  validation errors.
- At least one variant MUST be declared per LogicalService.
- A variant MUST carry either `realized_by` (pointing at an existing
  `[ciu_stack.*]`/`[compose_stack.*]`/`[external.*]` entry) or
  `implementation` (a mock) — never both, never neither.
- `init_requires`/`depends_on`/typed references use S13's grammar; any bare
  name in them MUST resolve to a declared `[service.<name>]` — a reference
  to a stack or compound key is a validation error, not a lookup miss.
- A Realization's aggregate `init_provides` is checked against every
  LogicalService's `contract` that points at it via `realized_by` — missing
  coverage is a validation error naming the logical service, the
  realization, and the uncovered fact. (Per `V8-REALIZATION-GRAPH.md`'s own
  "Still open" list, this contract-conformance check is not yet
  implemented anywhere — it's the actual prerequisite for §4.3 dropping
  `[deploy.phases]` safely, not an optional nice-to-have.)
- Secrets referenced in a variant MUST have corresponding entries under that
  variant's realization's `[ciu_stack.<stack>.<entity>.secrets]` table (or
  `[external.<name>.secrets]`) or a Vault path, per §10.3. Missing
  credentials fail at config-validation time when that realness level is
  selected.

### 3.3 Secret preflight

When an environment selects `payment-api = live`, CIU resolves
`service.payment-api.live.realized_by` to `external.payment_api_stripe`,
then checks that `[external.payment_api_stripe.secrets] stripe_secret_key`
exists in the resolved secret store before provisioning. Missing = refuse
with message naming the logical service, its realization, the variant, and
the expected key.

Mock and simulated variants that declare no secrets skip credential
resolution entirely.

---

## 4. Deployment model

### 4.1 Service groups

Replace the service-selection half of old profiles:

```toml
[deploy.groups.core]
description = "Infrastructure foundation"
services = ["our_main_db", "our_cache"]

[deploy.groups.app]
description = "Application layer"
services = ["our_api", "notification-service"]

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
services = ["our_main_db", "our_cache"]

[deploy.profiles.two-host.hosts.host-b]
services = ["our_api", "notification-service"]

[topology]
transport = "wireguard"          # "direct" | "wireguard" | "proxy" — see §1.17
                                  # (this table's schema is normative there)

[topology.hosts.host-a]
address = "10.0.0.1"

[topology.hosts.host-b]
address = "10.0.0.2"
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

**`--name` is a human-facing alias, never the identity (clarified
2026-08-27).** `INSTANCE_ID` (existing S16 behavior) remains the one
canonical, unique identifier per rendered `ciu.global.toml` — it's what
containers, networks, and every other mechanical name derive from, exactly
as today. `--name` records a purely cosmetic label (`dev-john`, `staging`)
pointing at that `INSTANCE_ID`, used only for human-facing addressing —
`ciu lock <instance-name>` below resolves the name to its `INSTANCE_ID`
before doing anything, the same way a Docker container's human-readable
name resolves to its ID. No container name, network name, or any other
mechanical identifier is ever derived from `--name` itself.

This does NOT mean one checkout can run two *concurrent* named instances of
the same project — `INSTANCE_ID` is one per rendered config, so two
genuinely concurrent instances of the same project still require two
checkouts (a new worktree each), same as today. The two example commands
above are two *separate* invocations (different times, or different
checkouts) being given different aliases, not a claim that they coexist
from one checkout. The real "multiple simultaneously-running instances,
same checkout, no new worktree" case is a monorepo holding more than one
*independent* `ciu.global.defaults.toml.j2` root (e.g. this very repo:
`vbpub/ciu`, `vbpub/cmru`, `vbpub/assay`, each a distinct CIU project living
under one git checkout) — each gets its own `INSTANCE_ID` because they're
different projects, not because one project was instantiated twice.

Multiple instances can coexist (multi-stack). Each gets unique `INSTANCE_ID`, network, container prefix — existing S16 behavior.

**An Environment's identity includes its realness selection, not just its
`INSTANCE_ID` (added 2026-08-27).** "Environment (instance)" is this
document's existing name for the whole running collection under one
`INSTANCE_ID` — and per §10.13, that collection is a *specific realness
materialization*: fixed for the instance's whole lifetime, never live-swapped.
No new noun is introduced for this — "Environment" already means it; this
just states the connection explicitly, since nothing did before. Whether the
*declared selection* deserves its own name distinct from its *materialization*
(echoing the LogicalService/Realization split) is a genuine open question,
not resolved here — see §10.13.

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
# REALIZATIONS — the physical things (see §3.1; entity/realization split)
# ------------------------------------------------------------

[ciu_stack.our_api_stack]
location = "applications/api-handler"
description = "HTTP request handler"

[ciu_stack.our_api_stack.api]
port = 8080
health = "/health"
init_requires = ["our_main_db"]
depends_on = ["our_cache", "payment-api"]
allow_degraded_start = true

[ciu_stack.our_db_stack]
location = "infra/db-core"

[ciu_stack.our_db_stack.postgres]
image = "timescale/timescaledb:pg18"
port = 5432
seed_data = "fixtures/seed.sql"

[ciu_stack.redis_stack]
location = "infra/redis-core"

[ciu_stack.redis_stack.redis]
image = "redis:7-alpine"
port = 6379

[external.payment_api_stripe]
base_url = "https://api.stripe.com"
secrets = ["stripe_secret_key"]

[ciu_stack.payment_api_stub]
location = "infra/payment-stub"

[ciu_stack.payment_api_stub.stub]
port = 8090
seed_data = "fixtures/payment_responses.json"
secrets = ["payment_stub_key"]

# ------------------------------------------------------------
# LOGICAL SERVICES — the names every consumer, group, and selection
# actually references
# ------------------------------------------------------------

[service.our_api]
[service.our_api.live]
realized_by = "ciu_stack.our_api_stack"

[service.our_api.mock]
implementation = "tests/mocks/api_handler_mock.py"

[service.notification-service]
description = "Sends emails/SMS (not yet implemented)"

[service.notification-service.mock]
implementation = "tests/mocks/notification_mock.py"

[service.our_main_db]
[service.our_main_db.owned-seeded]
realized_by = "ciu_stack.our_db_stack"

[service.our_cache]
[service.our_cache.owned-seeded]
realized_by = "ciu_stack.redis_stack"

[service.payment-api]
description = "External payment processor"

[service.payment-api.live]
realized_by = "external.payment_api_stripe"

[service.payment-api.owned-seeded]
realized_by = "ciu_stack.payment_api_stub"

[service.payment-api.simulated]
image = "wiremock/wiremock:latest"      # inline; same open judgment call as §3.1's
port = 8090                              # legacy_service_1.simulated — not yet settled
stub_mappings = "fixtures/wiremock_stubs/"

[service.payment-api.mock]
implementation = "tests/mocks/payment_mock.py"

# ------------------------------------------------------------
# PER-STACK DEPLOYMENT WIRING (see §1.15) — declared directly in each
# stack's own ciu.defaults.toml.j2 as [ciu_stack.<stack>.<svc>], not in
# the global file and not through any separate `[local_stack.*]` layer
# (that layer's join is redundant once the compound key is itself the
# global address — §1.15). Shown here for illustration only:
# ------------------------------------------------------------

# infra/db-core/ciu.defaults.toml.j2:
# [ciu_stack.our_db_stack.postgres]
# port = 5432
# image = "timescale/timescaledb-ha:pg18"
# env_required = ["POSTGRES_PASSWORD_FILE"]
#
# [ciu_stack.our_db_stack.minio]
# port = 9000
# image = "minio/minio:latest"

# ------------------------------------------------------------
# SERVICE GROUPS
# ------------------------------------------------------------

[deploy.groups.core]
description = "Infrastructure foundation"
services = ["our_main_db", "our_cache"]

[deploy.groups.app]
description = "Application layer"
services = ["our_api", "notification-service"]

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
address = "10.0.0.1"

[topology.hosts.host-b]
address = "10.0.0.2"

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
# SECRETS (in gitignored ciu.secrets.toml — addressed by realization,
# not logical name; see §10.3)
# [ciu_stack.our_db_stack.postgres.secrets]
# postgres_password = "..."
#
# [external.payment_api_stripe.secrets]
# stripe_secret_key = "sk_live_..."
#
# [ciu_stack.payment_api_stub.stub.secrets]
# payment_stub_key = "stub_local_..."
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

### 10.3 Secrets architecture — RESOLVED (2026-08-27, dstdns/vbpub joint session)

**Current state (S4):** six directives (`ASK_VAULT`, `GEN_TO_VAULT`,
`GEN_LOCAL`, `ASK_EXTERNAL`, `ASK_FILE`, `GEN_EPHEMERAL`) resolve secrets
into `.ciu/secrets/<name>` store files. Vault-backed directives require a
running Vault instance. This remains shipped, unchanged behavior — nothing
below alters S4 itself.

**V8 introduces `ciu.secrets.toml`** as a sibling of the global config file:

| Deployment mode | Where secrets live | How they reach containers |
|----------------|-------------------|--------------------------|
| **With Vault** | Vault KV2 (S4 directives unchanged) | Materialized into the owning stack's own folder (below), mounted via overlay |
| **Without Vault** | `ciu.secrets.toml` (gitignored) | Values read directly from this file; materialized the same way |
| **Mixed** | Vault for infrastructure secrets; `ciu.secrets.toml` for 3rd-party API keys, dev credentials | Both paths feed the same per-stack materialized-copy layer |

**Core principle — two coexisting SSoTs, not a replacement.** `ciu.secrets.toml`
is not an alternative to Vault, and Vault is not mandatory. A project may
declare a secret's authoritative value in *either* store, per-secret. Neither
store is subordinate to the other — `ciu.secrets.toml` fills exactly the gap
Vault can't (bootstrap-before-Vault-exists, and projects that never run Vault
at all), while Vault remains authoritative for anything with a live-fetch
path (AppRole/SM2 in particular — see (3) below). This is worth stating as
its own rule because it's easy to misread "vaultless fallback" as "second
choice": it isn't. Whichever store holds a given secret's value, that store
is that secret's SSoT.

The four open questions below are now resolved:

**1. Project-level secrets — resolved.** `ciu.secrets.toml` lives at
project/repo root, one file, addressed with the *same* addressing scheme
`V8-REALIZATION-GRAPH.md` already uses for realizations — whichever
realization kind actually backs the entity (`[ciu_stack.<stack>.<entity>]`,
`[compose_stack.<stack>.<entity>]`, or `[external.<entity>]`, per §3.1) —
rather than inventing a parallel `<stack>/ciu.secrets.toml`, a `[secrets]`
table in the sparse override layer, or a secrets-only naming scheme that
ignores which realization kind is actually in play. A secret sits at the
exact address its own realization does, whichever kind that is, so nothing
needs a second lookup path to find where a given entity's material lives.
One refinement beyond that reuse: secrets are nested under a reserved
`.secrets` sub-key of that address (`[ciu_stack.vault_stack.vault.secrets]`),
not injected as bare keys directly into the realization table — this avoids
a secret name ever colliding with a reserved realization key
(`init_provides`, `contract`, etc.) purely by chance. **This one nesting
detail is an editorial call made while writing this section, not something
explicitly settled in conversation — worth a second look before it's
treated as final.** (This paragraph was updated 2026-08-27 to name all
three realization kinds — it originally said only `ciu_stack`, written
before the `ciu_stack`/`compose_stack`/`external` split existed anywhere in
this document; see the corrected examples below.)

A stack may declare more than one secret; the table just grows more keys:

```toml
# ciu.secrets.toml (gitignored, project-root sibling of ciu.global.toml)

[ciu_stack.postgres_stack.postgres.secrets]
postgres_password = "..."
postgres_replication_password = "..."

[external.payment_api_stripe.secrets]
stripe_secret_key = "sk_live_..."

[ciu_stack.payment_api_stub.stub.secrets]
payment_stub_key = "stub_local_..."
```

(Updated 2026-08-27 to match §3.1's realization-kind addressing, adopted
in this document after this section was originally written: this section
predates the `ciu_stack`/`compose_stack`/`external` split, so its examples
originally addressed everything as `ciu_stack.*` — including secrets that
actually belong to an `external` or `compose_stack` realization, like
Stripe above. Same table-per-key rule as before, now with the correct
realization-kind prefix.)

**2. Vault bootstrap — resolved.** Root_token/unseal_key move OFF the
`[state]` mechanism and into an addressed secrets table like any other
secret:

```toml
[ciu_stack.vault_stack.vault.secrets]
root_token = "hvs...."
unseal_key = "..."
initialized = true
initialized_at = "2026-08-27T00:00:00Z"
```

Why move it: today's `[state]` channel is a general hook-persistence
mechanism (arbitrary structured value, optional `apply_to_config`
round-trip) that Vault's bootstrap secrets piggyback on *because* the
six-directive grammar can't express a multi-field value — not because
`[state]` is where secrets belong. That conflates two different concerns
(hook state vs. secret storage) in one channel. `[state]` itself is **not**
removed — a hook that genuinely wants to remember non-secret state (a
counter, a timestamp, a flag) keeps using it exactly as today. Only the
secret-shaped values move out.

**3. Service-level access tokens — resolved, plus one clarifying insight.**
Same answer as (1): an addressed `ciu_stack.<stack>.<entity>.secrets` table,
not a separate global-vs-per-stack fork. The clarifying insight: **an AppRole
bootstrap credential (a Vault `secret_id`, single-use, `secret_id_num_uses=1`)
is not a special kind of thing that needs its own path — it is an ordinary
ciu/docker-layer secret, materialized through the exact same mechanism as
any other value in this table.** SM2 (per-service Vault AppRole) does not
change how CIU delivers *that one bootstrap credential*; it changes what
happens *after* the service reads it — the service trades it for a Vault
client token and, from that point on, is fetching every other secret live
from Vault, with CIU no longer in the loop at all. CIU's own job never
grows past "materialize one more entry in the table"; it just becomes the
last entry a given service ever needs from CIU.

```toml
[ciu_stack.controller_stack.controller.secrets]
vault_approle_secret_id = "..."   # single-use; consumed once, then irrelevant
```

**4. Mounting into containers — resolved, with a delivery-mode axis added.**
Two independent questions were previously conflated here: *where* the
materialized copy lives, and *how* the container receives it. Both are now
answered:

- **Why a materialized copy still exists at all:** centralizing the SSoT
  doesn't remove the need for one. `ciu.secrets.toml` is never mounted
  directly — a stack's containers each need only their own subset of it,
  and Compose (outside Swarm mode, which this deployment model doesn't use)
  has no secret-delivery path that isn't a literal file on disk; its
  `secrets:` block is sugar over exactly that. *Considered and rejected:*
  writing values into Docker's own Swarm-native secret store (created via
  `docker secret create`, encrypted at rest, referenced by name with no
  file) so nothing ever touches disk — rejected because it's Swarm-only,
  and adopting Swarm is a far larger orchestration change than this section
  is about. So CIU still reads the SSoT during secret resolution and
  materializes one file per secret, same as it does for S4 today — only
  *where* that file lands changes.
- **Where:** every materialized secret — S4 Vault-backed values and
  `ciu.secrets.toml` values alike — moves out of the hidden nested
  `<stack>/.ciu/secrets/<name>` layout into a plain visible sibling of the
  relevant stack's `ciu.defaults.toml.j2` — no `.ciu/` subdirectory at all.

  **Naming — corrected 2026-08-27, reversing what this bullet said
  before.** It previously claimed `<service>.<key>` (the *consuming*
  container's own name plus the secret's bare key) was collision-free
  because the stack's own folder already disambiguates. That's true only
  when the secret materializes into *its own owning stack's* folder — it
  breaks the moment a folder receives a copy of a secret it doesn't own,
  which is exactly the `[external.*]` case below: two *unrelated* secrets
  (from two different realizations, or even a coincidental same-named key
  under two different `[external.*]` entries) can both be consumed by the
  same stack and would collide under the old rule, since nothing about
  `<service>.<key>` encodes which realization the value actually came from.

  The fix: the filename always carries the secret's own fully-qualified SoT
  address — `ciu.secret-temp-copy.<realization-kind>.<realization-name>.<key>.txt`
  (e.g. `ciu.secret-temp-copy.external.geo_ip_provider.geo_api_key.txt`) —
  never the consuming service's own name. This is unique by construction
  everywhere, not just within one folder: TOML itself already forbids two
  entries at the same `[<kind>.<name>.secrets]` address, so no two secrets
  can ever produce the same filename, regardless of which folder(s) they
  land in or how many different consumers pull from the same source. One
  rule for every case — no separate reasoning needed for "secret in its own
  stack's folder" vs. "secret in a consumer's folder" the way the old
  wording required. (The `GEN_LOCAL` "unrelated stacks share a secret"
  case still lands at true repo root under its own locator name, same as
  today — that part is unaffected.)

  **Where copies land, unchanged by this correction:** a `ciu_stack`/
  `compose_stack`-owned secret's copy still lives in that stack's own
  folder (Compose needs it within reach of that stack's own compose
  context) — the folder no longer does any disambiguation work, it's now
  purely mechanical placement. An `[external.*]` secret — no `location`, no
  folder of its own — lands in *every consuming stack's* own folder instead
  (one copy per consumer, same "Compose needs a file per container"
  reasoning as always), each carrying the identical, fully-qualified
  filename.
- **Lifecycle:** "temporarily" does not mean "deleted the instant the
  container starts." A crash-looping or `docker compose restart`ed container
  re-reads its mount and needs the file to still be there. Fast removal
  right after first use would be more SSoT-pure and marginally more secure,
  but restart-safety wins the trade-off — the file persists exactly as
  today's `.ciu/secrets/*` files do: untouched by `ciu down`, wiped only by
  `ciu clean` / `ciu secrets reset`. The `-temp-copy-` naming carries the
  "this is disposable, not authoritative" signal instead of an actually
  short-lived file.
- **How (new axis — delivery mode):** not every service accepts a
  file-based secret. CIU must offer all of: (a) a plain environment variable
  (still supported, despite being the weaker option — plain env vars leak
  via `docker inspect`, `/proc/<pid>/environ`, and crash/error-reporting
  dumps that include the process environment — but some services simply
  don't support anything else), (b) a `_FILE`-suffixed env var pointing at
  the materialized copy (the pattern most official images already expect,
  and the **recommended default**), (c) a fixed path the target app expects.
  Native Vault SDK / Vault Agent consumption (no file, no env var — the
  service talks to Vault directly) is a fourth mode, but it's the one CIU
  is genuinely not a party to (see (3)) — not a delivery mode CIU implements,
  just the boundary past which CIU's job is already done. Default is file
  delivery; env-var delivery is an explicit per-secret opt-in:

```toml
[compose_stack.compose_stack.service1.secrets]
db_password = "..."                            # default: file delivery
api_key = { value = "...", delivery = "env" }  # this app only accepts env
```

(`compose_stack.compose_stack.service1` reuses §3.1's own COMPOSE-type
example rather than inventing a fresh name, and is addressed as
`compose_stack.*` accordingly — same realization-kind correction as above.
The doubled `compose_stack` is §3.1's own deliberate renaming, not a typo —
see its note there.)

**Status:** RESOLVED — the design above is ready to fold into the normative
v8 spec. One nesting detail is flagged inline above (the `.secrets`
sub-key) as this session's own editorial call rather than something
explicitly agreed in conversation, and should get an explicit look before
being locked in.

See §10.11 for a related but *separate* decision made in the same session:
a new whole-run instance mutex. It shares the "one file, one instance" spirit
of this section but is not a secrets mechanism and doesn't reuse any of the
files described above.

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

**Status:** RESOLVED (bookkeeping fix, 2026-08-27) — §10.5 "Judge
distribution" already carries a complete worked design (`[testing.judge]`
version/source/sha256, CIU resolves+verifies+mounts the pinned artifact,
records both judge and runner-image digests in the ExecutionManifest and
verdict). This entry was never updated to point at it; the design itself
was not missing.

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
- `requires_services = ["our_api"]` — only meaningful when these logical services are deployed
- Per-package roots for monorepo subprojects with independent test suites

The base diff computation should use Assay's B012 changed-lines mode
(`base..HEAD`) rather than raw file lists, so whitespace-only changes don't
trigger full gate runs.

**Status:** OPEN — refine before implementation; coordinate with Assay's
changed-file detection (B014 bounded output tails).

### 11.5 Default skip-on-realness-mismatch false-green risk (was M3)

Skip-with-warning as a default lets a lane report success on partial
evidence, inverting run-gate/assay's existing fail-safe posture.

**Status:** RESOLVED (bookkeeping fix, 2026-08-27) — never had an entry
here, but §10.6 ("Lane realness requirements") and §10.7 ("NOT_RUN
semantics") substantively resolve exactly this: a lane whose required
realness variant isn't met is assigned `NOT_RUN` with a named reason and is
"NEVER silently downgraded to a different variant"; ship intents fail
closed on any required `NOT_RUN`. The concern was addressed elsewhere in
the document before this section existed to track it.

**Extension (2026-08-27):** an explicitly requested realness or rigor that
cannot be fulfilled must fail regardless of the invoking intent's default
`not_run_policy` — an explicit ask is a stronger signal than any intent
default and must never silently degrade to a warning. See §10.7's added
rule for the mechanics.

### 11.6 Concurrency budget and locking incomplete (was M8)

S16.3 coordinates family-wide cold starts through shared locks; the
original review asked how named environments, locks, budgets, shared
networks, and parallel CI all interact — nothing explained it in one place.

**Status:** RESOLVED (2026-08-27) — §10.11 (instance mutex) and §10.12
(concurrency model — how all five locking mechanisms compose, acquisition
order, and why S4.26 stays as defense-in-depth) now cover this directly.

### 11.7 Shared infrastructure absent (was M7)

S16.1 shared-infra join for worktrees is ignored by v8's deployment model;
common multi-stack optimization risked being lost.

**Status:** OPEN, sharpened not resolved (2026-08-27) — see §10.14. The
problem turns out bigger than "add a cross-reference": S16.1 is welded to
`ciu worktree add`, and the real "multiple instances, one checkout, no new
worktree" case (a monorepo with independent ciu-roots — this repo is the
live example) never calls that verb, so shared-infra join has no way to
apply there today regardless of anything v8 does. Two options laid out in
§10.14, neither chosen: keep it worktree-scoped (simple, accepts the gap)
or generalize it to an environment-instance property (real design work,
several sub-questions un-explored).

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

**Coverage warning: `requires_realness` vs. transitive dependencies
(added 2026-08-27, operator ask).** Nothing above checks whether a lane's
`requires_realness` is actually *complete*. A lane naming `worker` doesn't
automatically also cover `geo-location`, even though `worker`'s own
`depends_on`/`init_requires` names it — the lane author has to add it
separately, and nothing today catches the omission if they forget.

`ciu check` gains a new warning class for this: for every service named in
a lane's `requires_realness`, walk that service's RealizedService
`depends_on` + `init_requires` closure transitively (the same LogicalService
reference-following the topological sort already does for startup
ordering) and flag any dependency not also present in the lane's
`requires_realness` keys. This is config-only — no live environment needed
— so it runs wherever every other `ciu check` validation does, not only at
gate time.

Two severities, matching the distinction §1.6 already draws between init
and usage dependencies:
- An uncovered `init_requires` dependency: the consumer under test cannot
  even start without it, so a lane that never named it is resting on an
  unstated assumption about what realness it's actually getting.
- An uncovered `depends_on` dependency: the consumer can run degraded
  without it, so silence is more defensible — but still worth surfacing,
  since a test that ends up exercising degraded-mode behavior by accident
  rather than by design is a real failure mode too.

Reuses the WARN/ERROR severity vocabulary CIU-65 already proposes for
`validate_config` findings (§1.15's note above) rather than inventing a
third one — once that lands, this check's two levels map onto it directly.

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

**An explicit request always fails closed, regardless of `not_run_policy`
(2026-08-27).** The policy above governs the intent's own *default* posture
toward a `NOT_RUN` arising from ordinary selection — nothing was explicitly
asked for, so the ambient default decides. It does not govern the case
where the operator explicitly asked for a specific realness or rigor —
`ciu gate --rigor R2 ...`, or a realness pinned via `--realness
<svc>=<level>` (§1.4) that a lane's `requires_realness` then can't satisfy —
and that exact request cannot be fulfilled. An explicit ask is a stronger
signal than any intent default: silently downgrading it to a warning means
the operator asked for something specific and the system quietly gave them
something else, which is worse than asking for nothing and getting the
intent's own default. So: **a `NOT_RUN` caused by an explicitly requested
realness/rigor is always FAIL, even under `not_run_policy = "warn"`.** Only
a `NOT_RUN` arising from the intent's own inferred/default selection is
subject to `not_run_policy` at all. (This is a gate-layer rule. The
deployment-layer `--realness` CLI override at `ciu up` time, §1.4, has the
same open question — no failure mode is stated there yet for an explicit
override naming a variant the service doesn't declare — but that's a
separate gap from this one, not resolved by this addition.)

---

### 10.8 Authored config vs derived state

To prevent ambiguity about where truth lives:

**Authored (SSOT):**
- `ciu.global.defaults.toml.j2` — repository-authoritative baseline
- `ciu.global.toml.j2` — intentional committed override
- `<stack>/ciu.defaults.toml.j2` — stack-authoritative baseline
- `<stack>/ciu.toml.j2` — optional stack override
- `ciu.secrets.toml` — gitignored secret values for vaultless/mixed-mode
  projects; coexists with Vault as a second SSoT, never subordinate to it
  (§10.3)

**Derived (never authored):**
- `ciu.global.toml` — rendered output of the chain above
- `.ciu-execution-manifest.json` — compiled gate plan (§10.4)
- `<stack>/ciu.secret-temp-copy.<realization-kind>.<realization-name>.<key>.txt`
  — materialized secret copies, named by the secret's own fully-qualified
  SoT address (never the consuming service's name — see §10.3's correction),
  landing in whichever stack folder(s) need a copy (S4 Vault-backed AND
  `ciu.secrets.toml`-sourced alike), superseding the old hidden
  `<stack>/.ciu/secrets/<name>` layout
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

---

### 10.11 Instance-level run mutex (2026-08-27, dstdns/vbpub joint session)

**This is a new capability, not a relocation of an existing one.** ciu's
only lock today, S4.26 (`<stack>/.ciu/lock` / `<repo-root>/.ciu/lock`), is
held solely for the duration of `materialize_secrets()` — it serializes
concurrent *secret writes*, then releases. Nothing in ciu today serializes
the rest of a `ciu up`/`ciu down` run (compose apply, hooks, health checks)
against a second concurrent invocation against the same instance. Before
proposing this, it's worth being explicit that ciu does **not** currently
guarantee "only one ciu instance is running" in any general sense — that
guarantee doesn't exist yet, and this section proposes adding it, not
documenting something already true.

**Proposal:** a second, separate, whole-run mutex — held from the start of
`ciu up`/`ciu down` until it exits, not just during materialization.

**Scope: per `INSTANCE_ID`, not per git-common-dir.** `INSTANCE_ID`
(`engine.py`, `worktree.py::lease_holder`) already identifies one checkout —
each worktree instance renders its own `ciu.global.toml` and gets its own
identity (CIU-60). The mutex lives beside that instance's own rendered
config, one lock per `INSTANCE_ID`. This is deliberately the *opposite*
scope from git-common-dir: git-common-dir is the one thing every worktree of
a checkout family shares, which is exactly why today's worktree
allocation/budget locks (`worktree.py`) use it — they need family-wide
serialization for bookkeeping that really is shared. A run mutex does not
share that need. Scoping it at git-common-dir would mean a `ciu up` running
in the primary checkout blocks every one of its worktrees (and vice versa)
from running at all — directly defeating the reason worktree-based
multi-stack setups exist. Scoping it per-`INSTANCE_ID` means the primary
checkout and every worktree spawned from it each get an independent mutex;
none blocks any other. Concurrent `ciu up` invocations *against the same
instance* are the only thing this serializes.

**Relationship to S4.26:** these remain two distinct locks, not one file
wearing two jobs. §10.3's materialization change relocates *where* the
per-stack secret copies live (out of `.ciu/secrets/`, into the stack's own
folder) but does not change S4.26's own scope — it stays exactly what it is
today, a brief per-stack (or per-repo-root, for the rare stack-less secret)
write-race guard, just without the `.ciu/` prefix on its own sentinel file
now that the surrounding layout has dropped it too. It is still a different
file, held for a much shorter time, than the new whole-run mutex below.

**Lock target: `ciu.global.defaults.toml.j2`, not a new sentinel file, and
not the rendered `ciu.global.toml`.** Grabbing the lock at process start and
releasing only at exit — or on a crash, via `flock`'s own auto-release when
the holding process's file descriptor closes — is exactly right, and is not
a new mechanism: it's how S4.26 and the worktree allocation/budget locks
already behave (`fcntl.flock` in `worktree.py`), just extended to cover the
whole run instead of one narrow section of it. The question is only which
file to hold that flock against. Two candidates were compared:

- `ciu.global.toml` (rendered, gitignored, no `.j2`) — **rejected**: other
  ciu code paths (`env generate`, `render`) read and rewrite this file
  mid-run, so a long-held exclusive lock on it risks ciu self-contending
  with its own other commands, not just blocking a second `ciu up`/`down`.
- `ciu.global.defaults.toml.j2` — **adopted**. Checked directly against the
  source rather than assumed: every reference to this file (`cli.py`,
  `dev.py`, `workspace_env.py`) is a *read*, used specifically to walk up
  and detect the repo root (S1.1 — it's the canonical repo-root marker, not
  merely "a" config file). The only write site anywhere in ciu is
  `scaffold.py`, a one-time `ciu init`-style creation, never touched during
  `ciu up`/`down`. So a whole-run exclusive lock on it contends with
  nothing else ciu does mid-run. It also gets the right scope for free:
  since it's a git-tracked file, every worktree already has its *own* copy
  checked out at its own root — flock on one worktree's copy cannot block
  another worktree's copy, because they're different inodes at different
  paths. No new file to create or manage; the existing repo-root marker
  file already has exactly the right lifetime, presence guarantee, and
  per-checkout scope this mutex needs.

**Left open for implementation:** whether the mutex is default-on or an
opt-in `[ciu.instance]` config knob, and the exact CLI/error UX when a
second invocation finds it held. Neither was decided in the conversation
this section comes from.

---

### 10.12 Concurrency model — how the locking mechanisms compose (was M8, 2026-08-27)

M8 flagged that CIU's locking primitives were never reconciled in one
place. By the time §10.11 lands there are five, at four different scopes,
not two:

| Mechanism | Scope | Duration | Guards |
|---|---|---|---|
| S4.26 secrets lock | per-stack (or per-repo-root for `GEN_LOCAL`/shared secrets) | brief — only during `materialize_secrets()` | write races on secret files |
| Worktree allocation lock | git-common-dir (whole worktree family) | brief — around allocation bookkeeping | two worktrees racing to claim the same new instance identity |
| S16.3 budget lock | git-common-dir (whole worktree family) | brief — checked immediately before `docker compose up`, before any container starts | the family-wide `max_concurrent_instances` count |
| §5.7 gate admission | per-lane, computed — not a flock | for the lane's own execution | memory budget + shared-infra name collisions between concurrently *running* gates |
| §10.11 instance mutex | per-`INSTANCE_ID` | the whole guarded subcommand's run | two invocations touching the same instance's own state at once |

**Instance mutex reach — resolved as "every mutating/lifecycle subcommand,"
not literally every subcommand.** The chosen scope was "every ciu
subcommand touching the instance" — but taken completely literally that
includes `ciu gate`, and a gate run held under the same mutex as `ciu up`
would make §5.7's own concurrent-gate-admission model impossible: two lanes
against the same instance could never overlap even when the resource
budget has room, defeating the entire section written to allow exactly
that. **This is flagged as a correction to the literal scope just chosen,
not a silent narrowing of it** — §5.7 was written independently, in an
earlier part of this same document, and nobody had checked it against this
scope decision until now. The reconciled reading: the mutex covers `ciu
up`, `ciu down`, `ciu clean`, `ciu secrets reset`, `ciu lock`/`unlock` —
anything that changes what the instance *is* — while `ciu gate` and other
read-only inspection commands (`ciu secrets list`, `ciu check`) are
excluded and left to their own governance (§5.7's resource-based admission
for gates; nothing needed for pure reads).

**S4.26 becomes provably redundant under this scope, and is kept anyway.**
Every code path that can trigger `materialize_secrets()` now runs inside a
mutex-guarded subcommand, so a second concurrent materialization race
against the same stack can no longer happen — the mutex already prevents it
one layer up. Removing S4.26 was considered and rejected: it is cheap,
already implemented, and correct; keeping a working, narrow safety net that
has become provably unnecessary under *today's* call graph costs nothing,
while removing it bets that no future code path will ever call into
materialization outside a mutex-guarded entry point. That is not a bet
worth taking to save a few lines of already-shipped code.

**Fixed acquisition order.** Precedent already exists for stating this
explicitly — S4.26 itself documents a fixed stack-then-project order across
its own two locks specifically to avoid deadlocks. Extended to the full set:

1. Worktree allocation lock / S16.3 budget lock (family-wide, momentary) —
   acquired and released first, before anything instance-specific is
   attempted. A budget-exhausted family fails fast here, before ever
   contending for an instance mutex.
2. §10.11 instance mutex — acquired next, held for the subcommand's whole run.
3. S4.26 secrets lock — acquired, as needed, as a sub-step inside an
   already-mutex-held run.

Because the family-wide locks are always released before the instance
mutex is even requested, and the instance mutex's own critical section
never itself tries to acquire a family-wide lock, there is no cycle between
them — no ordering-driven deadlock is possible, the same property S4.26's
existing two-lock ordering already relies on.

---

### 10.13 Realness is immutable per environment instance (2026-08-27, dstdns/vbpub joint session)

**The question this resolves:** is it ever desirable to swap a running
instance's realness selection in place — worker was talking to a live
`controller`, now make it talk to a mock, without tearing anything down?

**No — and the reasoning is more fundamental than the addressing mechanics
below.** A live swap risks state divorced from reality: if `controller` ran
`live` and wrote real rows into `our_main_db`, then realness swaps to
`mock`, that data doesn't vanish — it sits there, inconsistent with
whatever the new selection assumes, and a test running against it draws
conclusions from a hybrid state nothing declared or intended. This alone
settles it independent of any network-addressing concern: **realness is
selected once, at `ciu up` time, and is fixed for that instance's entire
life.** Changing it is not a live operation — it is `ciu down` (or `ciu
clean`, when the previous run's state must not leak into the next one) 
followed by `ciu up` with the new selection.

**What this dissolves, and what it leaves standing.** §1.15/§3.1's
entity/realization split raised a real question during this session: if
`controller`'s `live` realization and its `simulated` realization are
different `RealizedService` tables (different compose service keys), does
`container_name()`'s naming (`{project}-{env_tag}-{service_name}`, unchanged
since v5/v6) produce a different address for each, breaking a consumer that
expects the address to stay put? Immutability dissolves the *live*-swap
version of that concern entirely — there is no in-flight moment to make
transparent, because the instance is always rebuilt, never reconfigured
underneath itself. What survives is a *weaker* version: a consumer's own
build/config shouldn't need regenerating every time a *different* `ciu up`
picks a different realness combination for the same logical dependency.
Whether that's worth solving by keying compose service naming off the
LogicalService name rather than the RealizedService's own name (as
originally proposed) is now a build-time convenience question, not a
correctness one — left open, lower stakes than first framed.

**Consequence 1 — realness selection must be durable, inspectable state,
not just the CLI flags of whatever `ciu up` happened to run.** §1.4a's
resolution order (CLI > per-service override > intent hint > defaults)
describes what gets *selected*; nothing currently records what an
already-running instance's selection *was*, past the ephemeral argv of the
invocation that created it. Without a durable record, "what realness is
this instance running?" has no authoritative answer once the terminal that
ran `ciu up` is gone.

**Consequence 2 — `ciu up` must refuse, not silently reconfigure, on a
realness mismatch against an already-running instance.** This is the
natural extension of §10.11's instance mutex: while holding the per-`INSTANCE_ID`
lock, compare the requested selection (resolved via §1.4a) against the
durably recorded one. Match → proceed normally (this is just an ordinary
`ciu up`, e.g. restarting after a crash). Mismatch → refuse, naming which
service(s) differ and instructing `ciu down` (or `ciu clean`) first. This
also interacts with §4.4's existing lock mechanism: a `ciu lock`ed instance
already can't `ciu down`, so it transitively can't have its realness
changed either — consistent, not a new rule, just worth stating.

**Consequence 3 — this likely needs its own verb surface, not just a flag
on `ciu up` (proposed, not fully settled).** Something in the shape of
`ciu realness set <svc>=<level>` — a single place realness selection is
declared, distinct from the ordinary up/down lifecycle, so "what is this
instance's selection" is a first-class, queryable fact rather than
something reconstructed from `ciu up`'s own history. Exact CLI shape,
whether `--realness` on `ciu up` remains the entry point that records the
durable state versus a wholly separate verb, and how this interacts with
`ciu plan` (§5.6, already used to preview resolution before any container
starts) are open — this section states the requirement, not the final
design.

**Terminology:** see §4.4 — "Environment (instance)" already names the
whole running collection; this section is what makes explicit that an
Environment's identity now includes which realness selection it was
materialized from.

---

### 10.14 Shared infrastructure (was M7) — problem sharpened, NOT resolved (2026-08-27)

**This section exists so the reasoning already worked out isn't lost —
it is explicitly OPEN, same status as M1/M2/M4/M6/M9, not a decision.**

**What S16.1 does today (shipped).** `ciu worktree add NAME --shared-infra
REF --shared-infra-services S1[,S2] --shared-infra-ref-projects R1[,R2]
--profile P1[,P2]` lets a *new* worktree skip standing up its own copy of
heavy, rarely-diverging infrastructure (identity, secrets, observability,
reverse-proxy) by joining an *existing* reference worktree's network
instead — only the new instance's own "diverging-tier" containers gain a
second network membership. Validated twice: at `add` time (proves every
reference project has a live container before the checkout even exists)
and again at `ciu up` time (re-proves liveness, re-checks the network
hasn't changed). It exists so ten parallel package-worktrees don't each
need their own Vault/Consul/Redis.

**The gap:** v8's deployment model (§4 — groups, profiles, environment
instances via `ciu up --name`) never mentions shared-infra join at all —
not even a note saying "unaffected, still S16.1 as shipped."

**Why this isn't a simple cross-reference fix — sharpened via a concrete
example.** v8 deliberately separates "environment instance" (a runtime
`ciu up` concept) from "worktree" (a git concept). The obvious guess for
"multiple simultaneously-running instances, one checkout, no new
worktree" — two *named* instances of the *same* project sharing a
checkout — turns out not to be real: `INSTANCE_ID` is one per rendered
config (§4.4), so two concurrent instances of the same project still need
two checkouts regardless. The real version of that scenario is a monorepo
holding more than one *independent* `ciu.global.defaults.toml.j2` root —
this very repo is the live example (`vbpub/ciu`, `vbpub/cmru`,
`vbpub/assay`, each its own project, each its own `INSTANCE_ID`, no new
worktree needed since they already coexist).

**The consequence that actually matters:** that monorepo-multiple-roots
scenario never calls `ciu worktree add` at all — the roots already
coexist from the start, nobody "adds" a worktree for them. S16.1's
shared-infra join is welded specifically to that verb. So today, if
`cmru` wanted to join `ciu`'s already-running shared infra rather than
standing up its own, there is **no way to request that at all**, regardless
of anything v8 changes — not a design gap in v8's model, a gap in the
verb the capability is attached to.

**The fork, not yet decided:**
- **(a) Keep it worktree-scoped, as shipped.** Simplest — v8 gets a note
  saying shared-infra join is unaffected, still only available via
  `ciu worktree add`. Accepts that the monorepo-multiple-roots case simply
  cannot use it.
- **(b) Generalize it to an environment-instance property** — e.g.
  `ciu up --name X --shared-infra REF ...`, the same flags moved (or
  duplicated) onto the verb that actually creates instances in the new
  model, so any new instance — worktree-created or a pre-existing monorepo
  root — can request it. Real design work: does the add-time/up-time
  validation split still make sense when there's no `add` step? Does
  "diverging-tier vs. reference-tier" need a home in `[deploy.groups]`
  (raised, never explored further)?

Neither option is chosen here. This section's job is to make sure the next
person picking this up starts from the sharpened problem, not from
scratch.
