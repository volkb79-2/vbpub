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
init_provides = [
  "pg:role/controller", "vault:secret/db/postgres/controller_password",
  "stack:infra/db-init:completed",  # stands in for pg:schema/dstdns — no such
                                     # ref kind exists (see "Full system trace"),
                                     # but this shipped one-shot-exit-0 probe
                                     # (verified live, not just documented) does
                                     # the same ordering job. Self-declared here
                                     # only because ciu's static lint requires
                                     # it (CIU-63) — the actual resolution never
                                     # reads this line.
]

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

Validated against dstdns's real fresh-bring-up chain (`ciu clean && ciu up`)
and re-checked under the stack-level model above. This section went through
three corrections; the trail matters more than usual here because the third
one reverses the second and first's own conclusion, so it's kept in full
rather than silently collapsed.

**Correction history (fold if you only want the current state):**
1. *Three waves.* First pass computed a topological sort of the *declared*
   graph — missing that every `GEN_TO_VAULT` user needs Vault live to write
   to, an edge none of them declared.
2. *Five waves, membership wrong.* db-core and db-init were splitting
   ownership of the database contract — db-core minted controller's
   role/password directly while only the schema routed through db-init,
   letting controller cheat down a wave for half its dependency.
   Consolidating onto db-init as the sole producer moved authentik/
   webapp-server/worker-db up a wave.
3. **Reversed on verification, not just re-derived:** both "confirmed ciu
   gaps" from passes 1 and 2 — vault-liveness, schema-completion — were
   claimed to be *inexpressible* in ciu. Neither is. `stack:<name>:healthy`
   and `stack:<name>:completed` are shipped, docker-inspect-based probes
   (`provisioning.py::_probe_stack`) — checked live against a real dstdns
   checkout's own running containers before trusting this, not inferred
   from source alone: `stack:infra/vault:healthy` and
   `stack:infra/db-init:completed` both resolved `satisfied=True` against
   real container state. This is the exact shape of ciu's own **CIU-45**
   (withdrawn 2026-08-21): "ciu can't express X" turning out to mean
   "dstdns never used the mechanism that already exists." **Both are now
   fixed in dstdns's own config** (dstdns@d1688765), not filed as ciu bugs.
   What *did* get filed (CIU-63) is narrower and real: `ciu check`'s static
   graph lint doesn't know `stack:*` refs resolve by live probe rather than
   a `provides` declaration, so using one forces the referenced stack to
   redundantly self-declare `provides = ["stack:X:..."]`, undocumented
   anywhere. The wave *structure* below is unchanged by this correction —
   only which mechanism explains each edge is.

![Wave 0: vault alone, the only genuinely self-contained bootstrap. Wave 1: db-core, redis, and consul, each needing vault live to GEN_TO_VAULT their own secrets, now declared via stack:infra/vault:healthy. Wave 2: db-init, now the sole producer of application-facing database facts, and docker-stats-exporter, which never touches the database. Wave 3: controller and authentik, both waiting on db-init rather than db-core directly, via stack:infra/db-init:completed. Wave 4: worker-io, which requires internal_dlq_token from controller — the edge that broke when both were declared in the same phase, shown highlighted.](assets/realization-graph/system-trace.svg)

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
this graph demands.

### What is actually happening, in order, on a clean start

The wave list above says *what depends on what*; it doesn't say what runs.
Reconstructed and verified against dstdns's own live containers
(2026-08-26), correcting the ownership this session started with:

```
wave 0  vault:
        container up → `vault operator init` (master key + unseal key
        share(s) + root token, generated entirely inside vault — the one
        node with no external dependency of any kind) → unseal with the
        first key share → enable the KV v2 secrets engine → post_compose
        hook mints per-service AppRoles (controller, webapp-server:
        role_id + secret_id, written into Vault) → self-declares
        provides = ["stack:infra/vault:healthy"] (CIU-63: required only to
        satisfy ciu check's static lint, never read by the actual probe).

wave 1  db-core, redis-core, consul-server, skywalking:
        NOW requires = ["stack:infra/vault:healthy"] each (dstdns@d1688765) —
        a real, previously-undeclared dependency: GEN_TO_VAULT needs a live,
        unsealed vault to write to, silently covered before only by
        hand-placed phase numbers.
        containers up → each stack's OWN init sub-container (Compose's
        depends_on, never ciu's graph) → GEN_TO_VAULT: db-core mints its own
        superuser password + minio root credentials; redis-core mints the
        shared redis password; consul-server's post_compose hook mints
        per-service ACL tokens; skywalking mints its own postgres password
        (this one also had NO `provides` declared at all until this session
        — dormant, since nothing yet requires it, but the same CIU-45-shaped
        omission vault's own AppRole credentials once had).

wave 2  db-init:
        requires wave-1's db (`pg:db/dstdns`) + db-core's admin/superuser
        access → connects as superuser → creates the application's OWN
        roles (controller, workerdb, webapp, authentik) + GENERATES +
        GEN_TO_VAULTs each role's password → runs schema migrations,
        writes the `schema_meta` marker row.
        self-declares provides = ["stack:infra/db-init:completed"] — the
        one-shot-exit-0 probe standing in for the still-nonexistent
        `pg:schema/*` ref kind (CIU-63 applies here too).

wave 3  authentik, controller, webapp-server, worker-db:
        NOW require = [..., "stack:infra/db-init:completed"] each
        (dstdns@d1688765) in addition to db-init's role/password facts —
        closing the schema-ordering gap for real, not leaving it as a noted
        risk. Each connects using its own db-init-minted role/password +
        redis-core's password + consul-server's token; controller
        additionally reads vault's AppRole (role_id/secret_id, wave 0) to
        authenticate for any live Authentik API call later; controller
        GEN_TO_VAULTs internal_dlq_token. Authentik does NOT need db-init's
        schema — it owns its own database via django_tenants, and only
        needs its own role/password from db-init.

wave 4  worker-io:
        reads redis-core's password (wave 1) + controller's
        internal_dlq_token (wave 3) — the exact edge that broke this
        session when both were declared in the same hand-placed phase.
```

**A residual race not closed by any of this:** if db-init writes
role/password to Vault before its own schema migration finishes, ciu's
preflight sees the expressible facts satisfied and lets a wave-3 consumer
start anyway — `stack:infra/db-init:completed` only fires once the whole
job exits, so this is closed for the *specific* case verified here, but
would reopen for any stack whose init job writes a checkable fact partway
through its own work rather than only at the very end. Worth naming as a
design constraint on init-job authors (finish all `provides`-relevant work
before exiting) rather than assuming the mechanism alone prevents it.

**Checked and confirmed fine:** authentik's own OIDC client bootstrap
(`oidc_client_id = "dstdns-ui"`) looked like it might be the same shape of
gap as db-init's schema, but isn't — controller and webapp-server fetch the
bootstrap token *lazily*, at the point of an actual Authentik-backed API
call, and fail loudly then if it isn't ready (a deliberate P120 design
choice, not an oversight). It correctly needs no graph edge at all.

**Two things flagged as not-yet-modeled, deliberately kept rather than
dropped (operator, 2026-08-26) — neither confirmed as a live dstdns gap,
both worth not losing:**
- **Multi-host VPN/transport readiness.** dstdns is single-host today —
  `infra/tailscale-node` sits at phase_0, disabled by default ("invoked by
  SPEC F's bootstrap action, not the standard `ciu up` phases"). The moment
  a host-profile-based multi-host deploy (§1.7/§4.2) is real, "is the
  transport mesh up between hosts" becomes its own wave-0-shaped
  dependency, structurally identical to vault's own bootstrap — nothing in
  this graph or the current V8 proposal models cross-host transport
  readiness as an init dependency at all.
- **Consul loading a "worker profile" before worker-io's own bootstrap.**
  Searched concretely for this and did not find a hard, boot-blocking read
  of Consul KV in worker-io's own code — dstdns's config hierarchy's L2
  (Consul KV) layer is live-watched and optional-override, not a hard
  read-before-start dependency, so this does not currently appear to be a
  real gap. Recorded as *checked, not confirmed* rather than dropped, since
  it was raised from broader system knowledge this investigation may not
  have fully reached.

**The original bug this whole model was built to prevent:** `controller`
(provides `internal_dlq_token`) and `worker-io` (requires it) were declared
in the *same* phase — ciu's phase-wide preflight validates a phase's
requirements before any stack in that phase deploys, so a same-phase
producer/consumer pair can never be satisfied. A long-lived vault instance
had masked this for weeks by already holding the secret from an earlier
generation; the first genuinely fresh bring-up in a long time surfaced it
for real. §4.3's computed topological sort (once built) makes this class of
bug structurally impossible to declare, not just easier to catch — and that
sort can only be trusted once the graph it's computed from is known
complete, which is exactly what CIU-64's "run check before up" and a future
automatic contract-conformance check (below) would establish.

## The check IS the prerequisite for dropping `[deploy.phases]`

Worth stating plainly since it's easy to read "extend `validate_config`" as
a nice-to-have alongside §4.3's phase-elimination ambition rather than a
dependency of it: a topological sort computed from the requires/provides
graph is only as trustworthy as the graph is *complete*. Every real bug this
session found (vault-liveness, schema-completion, the CIU-63 lint gap
itself) was a case of the graph silently NOT containing an edge that
mattered — and dstdns's hand-maintained `[deploy.phases]` happened to get
the ordering right anyway, masking the gap for weeks. Drop `[deploy.phases]`
before the graph-completeness check exists, and the topological sort
inherits every one of those silent gaps with nothing left to mask them.
`ciu check` extended to verify graph completeness (a realization's
aggregate `init_provides` actually covers its logical service's `contract`,
computed automatically, not hand-written per hook — see "Resolved/open"
below) is not a parallel nice-to-have; it is the thing that makes §4.3 safe
to ship at all.

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
- db-core and db-init consolidated onto one producer for the application-facing database contract (role, password, schema).
- The purpose of the init graph itself, clarified: not resilience to slow starts, but turning a real misconfiguration into an immediate, named failure at deploy time instead of a retry loop that can't distinguish "8 seconds from ready" from "never going to exist" — both look identical, a timeout. Same fail-fast principle as dstdns's own `AGENTS.md` §4.2, applied to bring-up ordering specifically.
- **Reversed, not just resolved:** vault-liveness and schema-completion are NOT ciu limitations — both already expressible via shipped `stack:*:healthy|completed` probes, live-verified, now applied in dstdns's own config (dstdns@d1688765). Filed instead: CIU-63 (the static lint's blindness to how `stack:*` actually resolves), CIU-64 (`ciu check` should run automatically before `ciu up`), CIU-65 (`validate_config` findings need WARN/ERROR severity, reusing `warn_policy.py`'s existing `exit_on` vocabulary) — all three in `KNOWN_ISSUES_TODO_BACKLOG.md`.

**Still open:**
- **Contract conformance at config time** — checked the current proposal and both backlog files: **not planned anywhere yet**. Should be an explicit addition; the natural home is extending ciu's existing `validate_config()` static preflight (S9.5) from per-hook checks to the graph itself — does a realization's aggregate `init_provides` actually cover its logical service's `contract`, checked without a live probe. This is also the actual prerequisite for §4.3 dropping `[deploy.phases]` — see the section above.
- **`pg:schema/*` ref kind** — `stack:infra/db-init:completed` is a working substitute for dstdns's specific case, but the underlying ref kind still doesn't exist; a stack whose completion doesn't map 1:1 to "the fact I actually care about" (e.g. a job that produces two independent facts at different points in its own run) still can't express the finer-grained dependency.
- **Credential rotation** — a genuinely separate axis. §4.3.1 scopes the topological sort to initialization only; nothing re-runs it later, and dstdns's actual mechanism (`expose_env` baking a secret into an environment variable at container start) needs a full restart to pick up a rotated value. Not addressed anywhere in the proposal; needs its own design.
- **Multi-host VPN/transport readiness** and **Consul worker-profile loading** — see the two callouts above. Neither confirmed as a live dstdns gap; both flagged so the question isn't lost once multi-host or a Consul-KV-dependent bootstrap actually lands.
