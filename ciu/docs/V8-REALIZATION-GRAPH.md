# V8 provisioning model — the realization graph

**Status:** design note, feeding `CIU-V8-TESTING-GATE-PROPOSAL.md` §3.1/§4.3
**Session:** dstdns/vbpub joint design discussion, 2026-08-26
**Problem this resolves:** §3.1's worked examples nest a service's realness
variants inside the stack that happens to run it today
(`service.our_db_stack.postgres.live`). That is an addressing scheme, not an
identity — rename the stack, or move the database to a managed provider, and
every consumer's `init_requires` has to follow. This note splits the
**entity** a consumer depends on from the **row** that currently satisfies
it, so only the join changes when the backend does.

It also resolves the tension between §3.1 (compound `<stack>.<service>`
addressing) and §1.16 (a flat logical name joined to per-stack
`[local_stack.<name>]` wiring by matching key) — two different addressing
schemes drafted in different sections of the same proposal. The model below
is the §1.16 shape, carried all the way through.

## The mechanism

![A consumer's init_requires points only at the logical service our_main_db, which lists a contract of typed facts. Its .live realness variant is realized_by db_core, a CIU-managed stack whose init_provides satisfies the logical contract without db_core ever referencing the logical name back. Its .owned-seeded variant is realized_by an external service instead, satisfying the same contract a different way. Only the realized_by pointer changes if the backend moves.](assets/realization-graph/mechanism.svg)

`controller` never references `db_core` or `db_service4` by name — only
`our_main_db`. Swapping which realization backs `.live` is a one-line change
inside the logical layer; nothing in the realization layer or in any
consumer's config needs to know it happened. `init_provides` lives on the
stack itself, not on a dedicated sub-entity — whether a role gets created by
a one-shot job or by Postgres's own entrypoint script is exactly the kind of
internal detail ciu doesn't need to see.

## Entities

Five things, not six — `InitJob` doesn't earn a separate table (see below).

| Entity | TOML shape | Contains | Notes |
|---|---|---|---|
| **LogicalService** | `[service.<name>]` | `description`, `contract` (typed facts any realization must provide) | The stable name every consumer depends on. No deployment detail — no port, no image, no host path. |
| **RealnessVariant** | `[service.<name>.<level>]` | `realized_by` (points at a Realization) *or* `implementation` (a file path, for `mock`) | One row per realness level (`live`, `mock`, `owned-seeded`, `simulated`). The only place the indirection lives. |
| **Realization** | `[ciu_stack.<name>]` / `[external.<name>]` | `location` (ciu_stack only), connection facts (external only), aggregate `init_provides` | `type` is dropped as a field — it's which top-level table the entry sits under, so a validator rejects fields that don't belong (a location on an external service, a servername on a stack) for free. |
| **RealizedService** | `[ciu_stack.<stack>.<svc>]` | `image`, `port`, …, `one_shot`, `init_requires` | A container inside a stack, long-running or one-shot. `one_shot` is the *only* thing that distinguished "InitJob" as its own concept — it tells ciu's health gate to trust exit-0, not a Docker healthcheck. Sibling ordering within the stack is Compose's `depends_on`, never this model's concern. |
| **Typed fact** | `"kind:selector"` string | — | Not a table — a value that appears on both sides. Produced by exactly one Realization's `init_provides`; consumed by any number of `contract`/`init_requires` entries. The only thing that crosses a realization boundary. |

**Why fact strings stay literal, not Jinja references to a shared path
registry:** `ciu check`'s graph lint reads `contract`/`init_requires`/
`init_provides` without a render pass (S13.4a — "entirely in memory: no
hostdir, no materialized secret ... no hook run()"). A dedicated cross-check
validator catches drift against the canonical path registry instead — the
same pattern dstdns's own `test_secret_directive_parity.py` already runs
today for its pre-v8 `requires`/`provides` arrays, not a new mechanism.

**Where `init_requires` on a plain `RealizedService` shows up:** not a rare
case — it's the default above the foundational tier. Vault, Postgres, Redis,
and Consul are self-sufficient (each generates its own credentials).
Everything else needs cross-stack facts just to boot, with no dedicated init
job involved: authentik's own container reads its Postgres password and
Redis password from db-core/redis-core at container start (Django needs them
to run migrations); the same is true of controller, webapp-server,
worker-db, and docker-stats-exporter.

## Worked example (dstdns)

**Corrected — db-core does not create the role.** An earlier version of
this example had db-core both minting controller's role/password *and*
being the sole realization for `our_main_db`, with db-init only contributing
the schema as a separate edge. That splits ownership of one contract across
two producers and lets a consumer's dependency cheat down a wave for half
of it. One producer now: db-core shrinks to generic infra + admin access;
db-init owns every application-facing database fact — role, password, *and*
schema — matching "db-init seeds the schema and the application's own
users, and persists their credentials in Vault; only then can the
controller read the credential from Vault."

```toml
# ── db-core: generic infra only. No app roles, no app passwords. ──
[service.our_db_infra]
contract = ["pg:db/dstdns"]  # an empty database + admin access — nothing app-facing

[service.our_db_infra.live]
realized_by = "db_core"

[ciu_stack.db_core]
location = "infra/db-core"
init_provides = ["pg:db/dstdns", "vault:secret/db/postgres/superuser_password"]
# db-core's OWN GEN_TO_VAULT still needs vault live — an undeclared edge
# today (see "Full system trace" below), omitted here for clarity.

# ── our_main_db: what "the application's database" means — role,
#    password, AND schema, one producer, never db-core directly ──
[service.our_main_db]
contract = ["pg:role/controller", "vault:secret/db/postgres/controller_password", "pg:schema/dstdns"]

[service.our_main_db.live]
realized_by = "db-init"  # NOT db_core

[ciu_stack.db-init]
init_requires = ["our_db_infra"]  # needs db-core's admin access first
init_provides = ["pg:role/controller", "vault:secret/db/postgres/controller_password"]
# pg:schema/dstdns is ALSO produced here but can't be declared — no ref
# kind exists yet. Ordering rests on the hand-placed phase number.

# ── vault: mints the AppRole controller authenticates with ──
[service.our_vault]
contract = ["vault:secret/vault/controller/role_id", "vault:secret/vault/controller/secret_id"]

[service.our_vault.live]
realized_by = "vault_stack"

[ciu_stack.vault_stack]
init_provides = ["vault:secret/vault/controller/role_id", "vault:secret/vault/controller/secret_id"]

# ── controller: names only logical services, never db_core, db-init, or vault_stack ──
[ciu_stack.controller_stack.controller]
init_requires = ["our_main_db", "our_vault", "vault:secret/redis/password"]
init_provides = ["vault:secret/internal/internal_dlq_token"]  # self-generated; not required, provided

# ── worker-io: waits on a fact controller itself produces ──
[ciu_stack.worker_io_stack.worker_io]
init_requires = ["vault:secret/internal/internal_dlq_token", "vault:secret/redis/password"]
```

The bare `"our_main_db"` / `"our_vault"` / `"our_db_infra"` entries are a
coarse reference, resolved as "everything that logical service's
currently-selected realness variant needs to be considered ready,"
recursively. Each only ever names a *logical* service; the compound
`<stack>.<service>` form from §3.1's worked examples has no remaining use
once every cross-boundary edge routes through a fact or a logical name.
`our_db_infra` only ever appears in *db-init's own* `init_requires` —
controller has no reason to know it exists.

## Full system trace

Validated against dstdns's real fresh-bring-up chain (`ciu clean && ciu up`,
2026-08-25) and re-checked under the stack-level model above.

**Corrected twice after review.** First pass: three waves, computed as a
topological sort of the *declared* graph — missing the fact that every
`GEN_TO_VAULT` user needs Vault live first (Vault's own bootstrap,
`post_compose_vault.py`, is the only node that only ever `docker exec`s into
its own container). Second pass: db-core and db-init were still splitting
ownership of the same database contract — db-core minted controller's role
and password directly (wave 1) while only the schema routed through db-init
(wave 2), letting controller cheat down a wave for half its dependency.
Consolidating onto one producer — db-core shrinks to generic infra + admin
access, db-init owns every application-facing fact (role, password, *and*
schema) — moves authentik, webapp-server, and worker-db from wave 2 to
wave 3, alongside controller. The wave *count* stays five; the membership
doesn't:

![Wave 0: vault alone, the only genuinely self-contained bootstrap. Wave 1: db-core, redis, and consul, each needing vault live to GEN_TO_VAULT their own secrets, an edge none of them declare. Wave 2: db-init, now the sole producer of application-facing database facts, and docker-stats-exporter, which never touches the database. Wave 3: controller and authentik, both waiting on db-init rather than db-core directly. Wave 4: worker-io, which requires internal_dlq_token from controller — the edge that broke when both were declared in the same phase, shown highlighted.](assets/realization-graph/system-trace.svg)

```
wave 0: vault                                          (genuinely self-contained)
wave 1: consul-server, db-core, redis-core, skywalking (GEN_TO_VAULT needs vault live)
wave 2: db-init, docker-stats-exporter                 (db-init: sole producer of role/password/schema)
wave 3: authentik, controller, webapp-server, worker-db (all wait on db-init, never db-core directly)
wave 4: worker-io                                      (needs controller's internal_dlq_token)
```

Five real waves, not the nine phase numbers dstdns happens to declare
today — every declared phase still sits at or after its computed wave, so
the current hand-maintained list stays safe, just more granular than even
this corrected graph demands.

**Two confirmed gaps, not one — deliberately distinguished:** role and
password ARE expressible in ciu today (real ref kinds; the fix was purely
which stack's config declares them). Schema is NOT expressible at all —
that's the genuine ciu limitation, unchanged by the ownership fix. A
residual race survives even after consolidating ownership: if db-init
writes role/password to Vault before its own schema migration finishes,
ciu's preflight would see the expressible facts satisfied and let
controller start anyway. Fixing who owns a fact and fixing whether ciu can
check it are two separate problems.
- `db-init`'s schema creation (`pg:schema/dstdns`) is produced but required
  by nothing in the graph — `pg:schema/*` still isn't an expressible ref
  kind. Ordering today rests on a hand-placed phase number plus a runtime
  `schema_meta` poll as backstop. This is the edge §8.1 of dstdns's own
  `docs/spec/spec-ciu-provisioning-model.md` already asked for in
  2026-06-23 — still open.
- **New, found on review:** a `GEN_TO_VAULT`/`ASK_VAULT` directive should
  *automatically* imply `init_requires` on the vault store being live —
  right now nothing does, and it silently works today only because the
  hand-placed phase numbers happen to order vault first. This should be
  derived by ciu from the directive itself, not authored per stack, the
  same way an import implies build order without anyone writing it
  down — the same class of fix as the schema gap, just upstream of it.

**Checked and confirmed fine:** authentik's own OIDC client bootstrap
(`oidc_client_id = "dstdns-ui"`) looked like it might be the same shape of
gap, but isn't — controller and webapp-server fetch the bootstrap token
*lazily*, at the point of an actual Authentik-backed API call, and fail
loudly then if it isn't ready (a deliberate P120 design choice, not an
oversight). Unlike db-init's schema, this one correctly needs no graph edge
at all.

**The original bug this model was built to prevent:** `controller`
(provides `internal_dlq_token`) and `worker-io` (requires it) were declared
in the *same* phase on 2026-08-25 — ciu's phase-wide preflight validates a
phase's requirements before any stack in that phase deploys, so a same-phase
producer/consumer pair can never be satisfied. A long-lived vault instance
had masked this for weeks by already holding the secret from an earlier
generation; the first genuinely fresh bring-up in a long time surfaced it
for real. §4.3's computed topological sort (once built) makes this class of
bug structurally impossible to declare, not just easier to catch.

## Resolved / open

**Resolved this session:**
- `realized_by`, not `realizes` — the pointer reads from the abstract side toward the concrete side.
- No `fulfills` field. A realization never references the logical name above it — one-directional pointing only, so re-pointing `realized_by` can never leave a stale back-reference.
- Intra-stack container ordering (`postgres_init` waiting on `postgres`) is Compose's `depends_on`. It never enters ciu's graph — only what a stack provides to the *outside* does.
- `type` dropped as a field in favor of table choice (`[ciu_stack.*]` vs `[external.*]` vs `[compose_stack.*]`).
- `init_provides` lives on the Realization, not a dedicated init entity.
- `kind = "init"` dropped — `one_shot` is the only field that changes ciu's actual behavior.
- Fact strings stay literal, validated by a dedicated cross-check, not Jinja references.
- Port/endpoint lives on the Realization, resolved per-consumer the same way `topology.services.*.internal_host` already works today.

**Still open:**
- **Contract conformance at config time** — checked the current proposal and both backlog files: **not planned anywhere yet**. Should be an explicit addition; the natural home is extending ciu's existing `validate_config()` static preflight (S9.5) from per-hook checks to the graph itself — does a realization's aggregate `init_provides` actually cover its logical service's `contract`, checked without a live probe.
- **`pg:schema/*` ref kind** — a confirmed real gap in dstdns's current graph, unchanged since dstdns's own spec first asked for it.
- **Automatic vault-liveness dependency** — found on review, not in the first pass: `GEN_TO_VAULT`/`ASK_VAULT` directives should imply an `init_requires` on the vault store being live, derived by ciu itself. Currently every stack that mints its own secrets is missing this edge.
- **Credential rotation** — a genuinely separate axis. §4.3.1 scopes the topological sort to initialization only; nothing re-runs it later, and dstdns's actual mechanism (`expose_env` baking a secret into an environment variable at container start) needs a full restart to pick up a rotated value. Not addressed anywhere in the proposal; needs its own design.

**Resolved on second review:**
- db-core and db-init consolidated onto one producer for the application-facing database contract (role, password, schema) — see "Full system trace" above.
- The purpose of the init graph itself, clarified: not resilience to slow starts, but turning a real misconfiguration into an immediate, named failure at deploy time instead of a retry loop that can't distinguish "8 seconds from ready" from "never going to exist" — both look identical, a timeout. Same fail-fast principle as dstdns's own `AGENTS.md` §4.2, applied to bring-up ordering specifically rather than config values generally.
