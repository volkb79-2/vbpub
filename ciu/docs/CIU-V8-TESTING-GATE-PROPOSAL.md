# CIU v8 Proposal — Integrated Configuration Model, Deployment Graph, and Native Testing Gate

**Status:** PROPOSAL — not yet normative (the normative companion is `SPEC-V8.md`; the worked example is `v8-dstdns-demo/`)
**Author:** dstdns/vbpub joint design sessions (2026-08-22 → 2026-08-30); revision 2.x produced by the wholistic-integration pass (`CIU-V8-WHOLISTIC-INTEGRATION-PROMPT.md`) with the operator interviewed live on every fork, then hardened by a fresh adversarial review (§4.3.11)
**Supersedes:** every prior revision of this file (1.5 through 1.10); `run-gate-project` as a standalone tool (absorbed, §4.1.10); the `[deploy.phases]` hand-ordered deployment model; the `[service.<n>] type/location` registry; the `[topology.*]` hand-declared routing tables; the `.ciu/` machine-owned directory convention; `ciu.env` as a configuration source
**Target:** CIU v8.0.0 (breaking; `deploy.revision = 8` gates config acceptance)

**Proposal revision:** 2.1
**Updated:** 2026-08-30

**Source documents integrated (all read in full):** this file at revision 1.10; `V8-REALIZATION-GRAPH.md`; `CIU-V8-TESTING-GATE-ADVERSARIAL-REVIEW.md`; `CIU-V8-SPEC-RECONCILIATION.md`; ciu `KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-1..71), `docs/SPEC.md` (5.0.0), `docs/CONFIG.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`, and the sources `deploy.py`, `provisioning.py`, `composefile.py`, `config_model.py`, `worktree.py`, `workspace_env.py`, `secrets/*`, `warn_policy.py`, `hook_templates/post_compose_db.py`; assay `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `nyxloom-trove/4-backlog.md` (B001–B035), `decisions.md` (through A-331 on branch `feature/assay-b018-b019-b035-v8-synergy`), `src/assay/config.py`, `schemas/verdict.schema.json`; run-gate `SPEC.md`, `CONSUMERS.md`, `KNOWN_ISSUES_TODO_BACKLOG.md` (RG-1..23), `run-gate.py`; dstdns `nyxloom-trove/GUIDE.md`, `decisions.md` (D-094..D-212), `specs/*`, `ciu.global.defaults.toml.j2`, `ciu.global.toml.j2`, `assay.toml`, `run-gate.toml`, `ciu.env`, `hosts-avail.md`, and every stack `ciu.defaults.toml.j2` / `ciu.compose.yml.j2` (25 stacks + `tools/test-runner`).

**How to read this document.** It has two parts. **Part 1** (§4.1, §4.3a, §4.4, §4.5, §4.6, §4.11) is the proposal itself, written as a fresh, self-contained specification: a reader who only reads Part 1 has the whole v8 model. **Part 2** (§4.2, §4.3, §4.7, §4.8, §4.9, §4.10) is the design rationale and audit trail — the tagged inventory of everything considered, the reasoning walked through scenario by scenario, what the live interview decided and why, every contradiction found and how it was resolved, what was dropped, and where this proposal knows it is incomplete. Revision 2.x does not coexist with 1.5–1.10 the way each of those patched the last; nothing here requires consulting an earlier revision. Where this document and `SPEC-V8.md` differ, the SPEC is the more precise statement and this document is to be corrected.

**Conventions.** `S<n>` cites a section of ciu `docs/SPEC.md` 5.0.0 (the v7 specification) only in Part 2 and §4.6/§4.11; `V8-S<n>.<m>` cites a rule of `SPEC-V8.md`; `CIU-<n>` / `RG-<n>` / `B<nnn>` / `D-<nnn>` / `A-<nnn>` cite the ciu, run-gate, assay backlogs and the dstdns / assay decision records; `P<n>` cites a guiding principle from §4.1.1. Key names in `code`, entities in *Capitalised italics*. Examples use dstdns's real names because dstdns is the consumer whose config was inventoried key by key (§4.5).

---

# Part 1 — The Proposal

## 4.1 The v8 model

### 4.1.0 What v8 is, in one paragraph

A ciu consumer declares **what** it needs (*LogicalServices* with typed contracts), **how** each need can be satisfied at each realness level (*Realizations* — ciu stacks, external systems, or another instance's services), **where** things run (*Hosts*, *Networks*, *Layouts*), and **which** bundles a deployment includes (*Profiles*). From those declarations ciu **derives** everything that today is typed by hand and drifts: every container/compose/hostname identity, every route between services (same instance, joined instance, cross-host, through a proxy, over mTLS), the deployment order (waves) and the health gates between them, the readiness of transports, the facts minted by secret directives, the deploy set for a chosen realness, and the facts the testing gate needs. Every derived value is written as data into the rendered `ciu.global.toml`, where templates, hooks, the built-in gate (`ciu gate`, absorbing run-gate) and assay read it. There is one identity derivation, one lock per instance, one secrets file, one judge pin, no `.ciu/` directory, no hand-ordered phases, no `ciu.env` as a source of truth, and no Jinja value that is not backed by a file.

### 4.1.1 Guiding principles (cited as P1–P10 throughout)

1. **P1 Single source of truth.** Every fact is declared in exactly one place; everything else derives or references it.
2. **P2 Fail fast.** A wrong or missing value refuses at the earliest point it can be checked: schema → `ciu check` → deploy. Never a silent default.
3. **P3 Explicitness over magic.** Every derived value is visible in the rendered file and in `ciu check` output; a default that stands in for a fact that exists elsewhere is a hazard.
4. **P4 Mechanical checkability.** Prefer shapes a program validates completely (closed vocabularies, referential integrity, graph completeness).
5. **P5 Full preflight.** No error class that can be caught statically is discovered by a live deploy.
6. **P6 One derivation per identity.** Container name, hostname, compose key, compose project, network name, route host: one function, one place, used by every tool.
7. **P7 Minimal per-kind special-casing.** Adding a realization kind, a fact kind, or a secret directive must not require carve-outs in every consumer of the shape.
8. **P8 Declaration separate from resolution.** What is needed is declared apart from how it is satisfied; the resolution is computed and recorded.
9. **P9 Config as data.** Templates substitute and expand data; they do not carry the business logic. Layering is TOML deep-merge, not Jinja inheritance.
10. **P10 Nothing hidden.** Machine-owned state lives in visible, gitignored, flat files a person can `cat` and `diff`; no hidden directories, no ambient environment as a config source.

### 4.1.2 Entity model (ERD — entities and relationships before any key)

| entity | identity | meaning | declared by | key relationships |
|---|---|---|---|---|
| *LogicalService* | name | A capability the system needs, with a typed **contract** (facts a consumer may rely on) | global config `[service.<n>]` | has 1..n *RealnessVariants*; referenced by *Profiles*, *Lanes*, `init_requires`, `exec_in`, `image_from`, `pki`, `vault.service` |
| *RealnessVariant* | (LogicalService, level) | Which *Realization* stands in for the LogicalService at a realness level; `mock` is a variant with no Realization | global config `[service.<n>].<level>` | `realized_by` → exactly one *Realization* (absent for `mock`) |
| *Realization* | name (one namespace across kinds) | A concrete way to provide services: `ciu_stack` (a ciu-managed stack), `external` (nothing to bring up), `joined` (another instance's live realization) | global config or instance overlay `[realization.<n>]` | contains 1..n *RealizedServices* (ciu_stack), exactly one of them **primary**; has 0..n *Endpoints* (external); references an *Instance* + *LogicalService* (joined) |
| *RealizedService* | (Realization, service key) | One deployable service inside a stack: image, replicas, init edges, endpoints, secrets, config files | stack file `[ciu_stack.<svc>]` | `init_requires` → *LogicalServices*; `init_provides` → typed facts; `depends_on` → sibling services; owns *Endpoints*, *Secrets*, *ConfigFiles*, *HostDirs*; has a derived *Identity* |
| *Endpoint* | (Realization, name) — unique per Realization | A reachable port/URL with publication scope and allowed sources | stack file / global config `…endpoints.<e>` | `publish` scope; `allow_from` → *Networks*/*Hosts*; target of *Routes* |
| *TypedFact* | string `kind:selector` | A provable statement about live infrastructure (`pg:role/x`, `vault:secret/p`, `pki:issuer/n`, …) | in `contract`, `init_provides`, `[hooks] provides`, `provides`; **derived** from `GEN_TO_VAULT` directives | probed by ciu; provider resolved through the graph |
| *Host* | name | A machine (local or SSH-reachable) with one address per *Network* it sits on | `ciu.hosts.toml` `[deploy.hosts.<h>]` | has *Addresses*; placed in *Layouts* |
| *Network* | name | A reachability domain with a kind (`instance`, `lan`, `mesh`, `public`, `proxy`), transport security, and optionally a *Realization* that must be up for it to work | global config `[network.<n>]` | `realized_by` → *Realization*; `pki` → *LogicalService*; used by *Addresses*, `reach`, `allow_from` |
| *Profile* (bundle) | name | A set of *LogicalServices* that deploy together | global config `[deploy.profiles.<p>]` | `services` → *LogicalServices* |
| *Layout* | name | Placement: which bundles run on which *Hosts*, over which *Networks* they reach the others, in which environment | global config `[deploy.layouts.<l>]` | `hosts.<h>.bundles` → *Profiles*; `hosts.<h>.reach` → *Networks* |
| *Instance* | `instance_id` (path hash) | One checkout's deployment: identity, realness selection, joins, layout in use; the primary checkout is an instance without a parent | overlay `ciu.global.instance.toml.j2` (CIU-owned tables + operator tables) | selects *Layout*; records *RealnessVariants*; declares `joined` *Realizations* |
| *HostFacts* | per host | Host-local facts (paths, uids, FQDN, environment type) regenerated on each host | overlay `[ciu.host.generated]` | read by templates as `instance.*`, never part of an identity |
| *Identity* (derived) | per RealizedService (and replica) | `container_name`, `hostname`, `compose_key`, `compose_project`, `network` | rendered `[ciu.instance.resolved.realizations.*]` | computed from (project, instance_id, realization, service, replica) — P6 |
| *Route* (derived) | (consumer Realization, LogicalService, Endpoint) | How a consumer reaches an endpoint: network, host, port, URL, TLS facts, readiness prerequisites | rendered `[ciu.instance.resolved.routes.*]` | derived from *Layout* × *Networks* × *Endpoint* × *Realization kind* |
| *Wave* (derived) | ordinal | A set of Realizations deployed together; ordering from the init graph | rendered `[ciu.instance.resolved.waves]` | derived from `init_requires`/provides, `depends_on`, derived edges (secret→vault, secret→minter, network, pki, `after`) |
| *Environment* (gate) | name | Where a lane runs: an ephemeral image, `exec` into a Realization's primary service, or the host | global config `[testing.environments.<e>]` | `exec_in`/`image_from` → *LogicalService* |
| *Lane* (gate) | name | One test/judge invocation with preconditions and resource caps | global config `[testing.lanes.<l>]` | `environment` → *Environment*; `requires` → *LogicalServices* + realness; `assay_lane` → assay lane |
| *Judge* (gate) | — | The assay build the gate accepts: a version floor; provenance always required | global config `[testing.judge]` | verified against the environment's installed `assay` and each verdict's `judge_provenance` |

Relationship summary: a *Profile* names *LogicalServices*; the instance's realness selection maps each to one *RealnessVariant*, hence one *Realization*; the **deploy set** is the closure of those Realizations (a Realization no selected variant reaches is not deployed — no "everything needs a profile slot"). A *Layout* places bundles on *Hosts*; *Routes* are derived per consumer from placement and *Networks*; *Waves* are derived from init edges. Nothing about distance, ordering or naming is declared twice.

### 4.1.3 Files and layering

**Global chain (unchanged in mechanics, changed in contents):** `ciu.global.defaults.toml.j2` (committed, full defaults) → `ciu.global.toml.j2` (committed sparse override) → `ciu.global.instance.toml.j2` (gitignored, per instance, always present — the primary checkout has one too) → rendered `ciu.global.toml` (gitignored; contains everything above **plus** the derived `[ciu.instance.resolved.*]` tables, ending with `render_complete = true`). Merge semantics: scalars and lists replace, tables merge. Templates are rendered with `StrictUndefined`, no `env` context and no `$VAR` expansion: `project_name = "dstdns"` is a literal, paths and uids come from the overlay.

**Per stack:** `ciu.defaults.toml.j2` → `ciu.toml.j2` (sparse override) → rendered `ciu.toml`; `ciu.compose.yml.j2` → rendered `ciu.compose.yml` (identity, network, label, secret, config-file, port and `depends_on` stanzas **injected by ciu** — templates write only what is genuinely the stack's own); configfile renders as flat `ciu.rendered.<svc>.<cfgname>` next to the stack. **Render contexts** are fixed per file kind: a stack TOML file sees the merged global config plus `instance` (identity and host facts) and `routes` (its own derived routes) — never its own `ciu_stack` tables, which do not exist yet; compose and config-file templates additionally see `ciu_stack` (own services with `identity` and merged `health`), `realization` (the merged view of every stack), `stack_dir` (the host path of the directory being rendered, for stack-local bind mounts), and — config-file templates only — `secret("<key>")` for `configfile`-delivered secrets. There is no `env` context and no `$VAR` expansion in TOML layers.

**Stack file root.** A stack file declares its services under the fixed root `[ciu_stack.<svc>]` and stack-level shared secrets under `[ciu_stack.secrets.<key>]`. The stack **does not state its own name or location**; those are bound exactly once by the global registry entry `[realization.<n>] kind = "ciu_stack" location = "<dir>"`. At merge time ciu re-roots the file's tables under that registry node, so the **merged view** is `realization.<n>.<svc>` for every stack, while inside the stack's own templates `{{ ciu_stack.<svc>.* }}` names its own services — the file key and the render key are the same string. A stack file may also carry the reserved top-level tables `[hooks]`, `[governance]`, `[state]`; any other top-level table is consumer data passed through to templates. A service key may not be `hooks`, `governance`, `state`, `secrets`, `host`, `kind`, `location`, `endpoints`, `provides`, `instance` or `service`; a Realization may not be named `hosts`. A service table's key set is closed: consumer scalars live in a sub-table (`[ciu_stack.postgres.settings]`), never directly on the service.

**Identity source.** The overlay `ciu.global.instance.toml.j2` is hand-edited only (layout, bundles, label, joins, host-port overrides). CIU-owned facts live in a second, plain-TOML file merged after it, `ciu.instance.generated.toml`, rewritten whole by ciu: `[ciu.instance.generated] instance_id` (the instance identity, shared by every host of a layout because the file travels with the push bundle), `[ciu.host.generated]` (host-local facts: which layout host this machine is, roots, uids, public FQDN, environment type — regenerated on each host by `ciu instance init --host <h>`), `[ciu.instance.build]` (written by `ciu build`) and the per-layout realness records. `ciu.env` is no longer read by ciu; `ciu env print` exports the same facts for shells and legacy tooling (§4.1.12). Hook-persisted state lives in a flat `ciu.state.toml` per stack, never in the committed stack file.

**Secrets.** One gitignored `ciu.secrets.toml` at the repo root holds every materialized secret value (§4.1.8), written atomically; per-run temp copies `ciu.secret-temp-copy.<svc>.<key>.txt` are the bind-mount sources for `file` delivery.

**Host inventory.** `ciu.hosts.toml` (gitignored) replaces `.ciu.hosts.toml`; `~/.config/ciu/hosts.toml` replaces `~/.ciu/hosts.toml`.

**Instance registry.** `ciu.instance.json` (gitignored) is the per-checkout record the git-family budget, leases and joins enumerate.

**Nothing under `.ciu/`.** The directory is gone (P10). Every machine-owned file is a flat, visible, gitignored file with a `ciu.` prefix. The complete gitignore list ciu verifies at startup: `ciu.global.toml`, `ciu.global.instance.toml.j2`, `ciu.instance.generated.toml`, `ciu.instance.json`, `ciu.toml`, `ciu.compose.yml`, `ciu.state.toml`, `ciu.rendered.*`, `ciu.secrets.toml`, `ciu.secret-temp-copy.*`, `ciu.hosts.toml`, `ciu.gate.*`, `ciu.env`, `ciu-data/` (generated host directories), and the gate's `evidence_dir`. Build caches (KSM wrapper) live in `$XDG_CACHE_HOME/ciu/`. These files are ignored so they are never committed; a tool that deletes ignored files while an instance is up (`git clean -x`) destroys the instance lock and rendered state — documented, not detectable.

**Rendered file as lock.** The rendered `ciu.global.toml` is also the instance mutex (§4.1.9): mutating verbs take an exclusive `flock` on it and render **in place** (truncate + write on the locked descriptor, never temp + rename) so the inode — and the lock — survive the render; readers and the gate take a shared lock and refuse a file that does not end in `render_complete = true`. `ciu clean` truncates it, never unlinks it.

### 4.1.4 Identity — one derivation

Inputs: `deploy.project_name` (committed, literal), `instance_id` (overlay, generated), the *Realization* name (registry key), the service key (stack file), and an optional replica index. Output, computed by one function and **written as data** into the rendered file:

```toml
[ciu.instance.resolved.realizations.db_core.postgres.identity]
container_name  = "dstdns-98535c-db-core-postgres"      # {project}-{instance}-{realization}-{service}, `_` → `-` (DNS-safe; injective because names forbid `-`)
hostname        = "dstdns-98535c-db-core-postgres"      # == container_name
compose_key     = "db-core-postgres"                    # qualified: no bare-alias collision on the instance network (CIU-51)
compose_project = "dstdns-98535c-db-core"               # {project}-{instance}-{realization}
network         = "dstdns-98535c-network"               # the instance network; ciu creates it

[ciu.instance.resolved.realizations.controller.controller.identity]
container_name  = "dstdns-98535c-controller"            # service == realization → the service part is omitted
compose_key     = "controller"
compose_project = "dstdns-98535c-controller"
```

Rules: the derivation is the only place these strings are formed — templates read them (`{{ ciu_stack.postgres.identity.container_name }}` in the stack's own compose template; `{{ realization.db_core.postgres.identity.container_name }}` from elsewhere), hooks read them from their context, the gate reads them from the same table, assay's `derived:` facts point at them. Templates may not set `container_name:` or `hostname:` (a value equal to the derived one is tolerated and removed; a different one is refused) nor a label with a ciu-reserved key (`project`, `instance`, `realization`, `service`, `replica`, `managed-by` under the label prefix); `ciu check` also asserts global uniqueness of every derived `container_name` across the deploy set — structurally guaranteed by the four-part form, checked anyway (P4). `deploy.environment_tag` is retired: the instance id carries what the tag carried (CIU-50); a human-readable label may be attached to an instance (`[ciu.instance] label = "p146-fresh"`) and appears in listings, never in identities. Names must match `^[a-z][a-z0-9_]*$` for realization and service keys; hyphens in Docker-visible names come only from the separators the derivation inserts. A service may declare extra DNS `aliases` on the instance network (pwmcp's `pwmcp-mcp`) and `host_network = true` for `network_mode: host` daemons (tailscale), which then get no injected hostname.

Replicas: `instances = N` on a RealizedService yields `-1..-N` suffixes on `container_name`/`hostname` and `compose_key`, one service-level `compose_key` (compose DNS round-robins), and per-replica identity rows `identity.replicas[]`. Templates iterate `{{ ciu_stack.worker.identity.replicas }}` to emit one block per replica instead of hand-declaring `worker-db-1`/`worker-db-2` services.
### 4.1.5 Topology: hosts, networks, endpoints, layouts → derived routes

Access and transport are one concept modeled once (scenario 3): the **distance** between a consumer and a provider is never declared on either of them; it falls out of *Layout* (where each is placed) × *Networks* (what the two hosts share) × the provider's *Endpoint* (how it is published) × the provider's *Realization kind* (same instance, joined instance, external). The only declarations that carry distance are the endpoint's `publish`, `host_port` and `allow_from`, because those are properties of the endpoint itself.

**Hosts** (`ciu.hosts.toml`, gitignored; declared once, reused by every layout):

```toml
[deploy.hosts.localhost]
local = true                                   # this machine; no SSH facts
[deploy.hosts.gstammtisch]
ssh_host = "gstammtisch.dchive.de"  ssh_user = "ops"  known_host = "…"      # push facts as before
[deploy.hosts.gstammtisch.addresses]
mesh = "100.64.0.11"                           # one address per address-plane network the host sits on
public = "152.53.179.117"
[deploy.hosts.rs1002.addresses]
mesh = "100.64.0.12"
public = "203.0.113.12"
[deploy.hosts.tsstammtisch.addresses]
mesh = "100.64.0.13"
public = "203.0.113.13"
[deploy.hosts.nano1.addresses]
mesh = "100.64.0.14"                           # in the inventory, placed by no layout yet
```

**Networks** (global config). Two kinds only: an **address plane** hosts have addresses on, and a **proxy** (address-free, reached by FQDN). `instance` always exists implicitly (the compose network ciu creates, named by the identity derivation). Transport readiness and TLS are network properties inherited by every route over the network.

```toml
[network.mesh]
kind = "address"
description = "tailscale mesh"
realized_by = "tailscale_node"        # a per_host Realization; every route over this network gains a derived edge on it — on both ends
[network.public]
kind = "address"
description = "public internet"       # the proxy terminates TLS with its host-scoped certificate; no ciu-managed TLS here
[network.public_mtls]
kind = "address"
tls = "mtls"
pki = "vault"                         # the LogicalService whose contract carries pki:issuer/public_mtls; per-realization certs are derived secrets (§4.1.8)
[network.edge]
kind = "proxy"
realized_by = "reverse_proxy"
fqdn = "gstammtisch.dchive.de"        # consumers reach proxied endpoints as https://<fqdn> + endpoint.path
```

**Endpoints** (on the RealizedService, in the stack file; on the Realization for `external`). Endpoint names are unique per Realization (routes are keyed by LogicalService and endpoint). **Publication is derived from the layout**: an `instance`-published endpoint (the default) is reachable on the instance network and, whenever some cross-host route needs it, additionally published on the provider host bound to that host's address on the network the route uses — nothing is published on a single-host layout. `publish = "host"` means always published (on `host_bind`, default `0.0.0.0`); `publish = "proxy"` means fronted by a proxy network (the proxy's own hop is derived like any cross-host route).

```toml
[ciu_stack.postgres]
endpoints.sql = { port = 5432, protocol = "tcp", allow_from = ["network.mesh"] }
# on prod3: published as 100.64.0.11:5432:5432/tcp because controller@rs1002 routes to it over mesh; on local: not published at all
[ciu_stack.controller]
endpoints.http = { port = 8080, protocol = "http", publish = "proxy", host_port = 8083, path = "/api/controller", allow_from = ["host.tsstammtisch"] }
# fronted by the edge proxy; the proxy on tsstammtisch reaches it over mesh at 100.64.0.12:8083
[realization.internet]                # external: nothing to bring up; the endpoint is a URL plus transport facts
kind = "external"
[realization.internet.endpoints]
dns  = { url = "udp://1.1.1.1:53" }
http = { url = "https://example.org" }
```

**Profiles and layouts** (global config). Profiles are bundles of LogicalServices; layouts place bundles on hosts and say which networks each host may use to reach the others, in preference order. A layout is **always declared**, even for a single host. A `per_host = true` Realization (the tailscale node) may appear in the bundles of several hosts and runs on each; nothing routes *to* it.

```toml
[deploy.profiles]
mesh = { services = ["mesh_node"] }
core = { services = ["vault", "consul", "redis"] }
db   = { services = ["main_db", "object_store", "db_admin", "app_schema"] }
apps = { services = ["controller", "webapp_server", "webapp_ui"] }
edge = { services = ["reverse_proxy"] }

[deploy.layouts.local]                # laptop / CI: everything here, instance network only
environment = "dev"
hosts.localhost = { bundles = ["all", "test"], reach = ["instance"] }

[deploy.layouts.prod3]                # three hosts; declaration order = push order, verified against the cross-host init graph
environment = "prod"
hosts.gstammtisch  = { bundles = ["mesh", "core", "db", "identity", "worker-db"], reach = ["mesh"] }
hosts.rs1002       = { bundles = ["mesh", "apps", "worker-io", "observability"],  reach = ["mesh"] }
hosts.tsstammtisch = { bundles = ["mesh", "edge", "global-services"],             reach = ["mesh", "public"] }

[deploy.layouts.staging2]             # the SAME hosts reused with different bundles and transport
environment = "staging"
hosts.gstammtisch  = { bundles = ["mesh", "core", "db", "identity", "apps", "worker-io", "worker-db"], reach = ["mesh"] }
hosts.tsstammtisch = { bundles = ["mesh", "edge", "observability"],                                    reach = ["mesh", "public"] }
```

**Route derivation** (one function, `route(consumer_realization, logical, endpoint)`; V8-S7.8 is the normative statement):

1. Resolve the LogicalService through the instance's realness selection to a Realization *R*, the variant's service, and its endpoint *E* (a `mock` selection has no route; a template referencing one is refused).
2. *R* is `joined` → host = the reference instance's derived `container_name` for the service that declares *E* (read from the reference's rendered identity/endpoint table), network = the reference's instance network (the joiner's diverging services attach to it), port = `E.port`.
3. *R* is `external` → the declared URL and TLS facts, verbatim.
4. *R* is placed on the **same host** as the consumer → network = `instance`, host = `container_name` (or the service-level `compose_key` for replicated providers), port = `E.port`.
5. Otherwise walk the consumer host's `reach` in order and pick the first network that admits the pair: a proxy network admits when `E.publish = "proxy"` and the consumer is not that network's own proxy; an address network admits when both hosts have an address on it and `E.allow_from` (if set) admits the consumer host. Through a proxy: host = its `fqdn`, port = the proxy's own host-published `https` port, path = `E.path`. Otherwise: host = the provider host's address on that network, port = `E.host_port`, and the endpoint is published there (derived publication). No admitting network → `ciu check` ERROR naming both ends and the `reach` list.
6. Attach transport facts from the chosen network (`tls`; for `tls ≠ none` the `/run/secrets/tls_*` paths of the derived certificate secrets, §4.1.8) and readiness prerequisites (`realized_by` of the network on both ends, the `pki` service).
7. `path` is always copied; `url` is emitted only for `http`/`https` (`scheme://host:port`, without the path — a proxy prefix is not an application base path) and `udp`; `tcp` routes carry `host` and `port` only.

A route exists for every LogicalService a service lists in `init_requires` (ordering + route) or `uses` (route only, no wave edge — the tracing collector, a proxy's backends); a template that reaches for `routes.X` with X in neither list is refused. The result is written per consumer stack into the rendered file and bound as `routes` inside that stack's render context:

```toml
[ciu.instance.resolved.routes.controller.main_db.sql]      # consumer realization → logical → endpoint
network = "mesh"  host = "100.64.0.11"  port = 5432
requires = ["tailscale_node"]                                # derived readiness edge
# same instance, same host it reads:  network = "instance"  host = "dstdns-98535c-db-core-postgres"  port = 5432
[ciu.instance.resolved.routes.reverse_proxy.controller.http] # the proxy's hop to a fronted backend on another host
network = "mesh"  host = "100.64.0.12"  port = 8083  path = "/api/controller"  url = "http://100.64.0.12:8083"
requires = ["tailscale_node"]
```

and a consumer template writes `PGHOST={{ routes.main_db.sql.host }}` identically in every deployment shape. CIU's own Vault client is the pseudo-consumer `ciu` placed on the host running ciu (on the instance network it resolves container addresses through `docker inspect`, or attaches its own container when `ciu.auto_connect_network = true` in a devcontainer). `[topology.services.*]`, `topology_overrides`, `[topology.hosts]`, `[topology.routes]` and `[topology.external]` do not exist in v8; what they encoded is derived here.

**Remote deployment (push).** Push order is the layout's declaration order; `ciu check --layout prod3` refuses a layout whose earlier host has an init edge into a later one. Render-on-target is unchanged: each host renders its own `ciu.global.toml` from the same declarations with its own `[ciu.host.generated]`, so derived routes and publications are computed per host. The bundle carries the overlay and the generated file (so `instance_id` is shared; other layouts' realness records stripped), `ciu.hosts.toml`, and a **reduced** secrets store — only the Realizations placed on that host plus its own host-scoped entries, which are materialized on the control host before transfer.

### 4.1.6 Init graph, waves, health gate

**Edges** (all on RealizedServices, all data — no phases):

- `init_requires = ["main_db", "vault"]` — LogicalService names. Requiring a logical service means: its variant service is **healthy** (a container with a healthcheck reports `healthy`; one without reports `Running`; a `one_shot` service has exited 0) **and** every TypedFact in its contract holds. Fact-level partial requirements are not expressible: requiring the whole contract is conservative and keeps the grammar to one shape (P7). `uses = [...]` declares a runtime-only dependency: a route, no ordering.
- `init_provides = ["pg:role/controller", "pg:schema/app"]` — TypedFacts a service brings into existence by means other than a secret directive (typically a `one_shot = true` init job).
- **Derived provides**: every `GEN_TO_VAULT:<path>` directive yields the fact `vault:secret/<path>` on its service — the 17 paths db-core mints are never typed twice (P1).
- `[hooks.provides] <svc> = [...]` — facts a post_compose hook creates (AppRole ids, Consul tokens, MinIO IAM users), keyed by the service in which they are probed.
- Contract conformance: for every `[service.X.<level>] realized_by = R`, `contract(X)` ⊆ declared + derived provides of R (or `provides` of an external/joined R) — otherwise `ciu check` ERROR naming the uncovered fact. Facts nobody's contract claims are a WARN (unclaimed), except derived `vault:secret/*` facts, which directives consume.
- `depends_on = ["postgres"]` — sibling service in the same stack; rendered into compose `depends_on` with the condition derived from the sibling's rendered block (`service_completed_successfully` when `one_shot`, `service_healthy` when it carries a healthcheck, else `service_started`).
- **Derived edges** (never declared, always listed by `ciu check --graph` and in the rendered `edges`): every service with any `*_VAULT` directive → the variant service of the Realization behind `[vault] service` (the Vault Realization's own services excepted); every `ASK_VAULT:<path>` → the service in the deploy set that mints `vault:secret/<path>` (no minter and no asserting external/joined Realization → ERROR; `pki/<N>/…` paths are satisfied by the `pki:issuer/<N>` provider); every route over a network with `realized_by` → that Realization's variant service on the consumer's host **and** on the provider's host; every route over a `tls ≠ none` network → the `pki` service; a `joined` Realization → nothing (it is somebody else's wave).
- `after = ["<logical>"]` — the only manual ordering escape, validated like `init_requires` but carrying no contract semantics; `ciu check` WARNs when an `after` is already implied (an `init_requires` a derived edge implies is not a finding — it documents intent).

**Primary and variant service.** A stack with more than one service marks exactly one `primary = true` (a single-service stack's only service is primary). A variant may name a different service of the same stack (`object_store.live = { realized_by = "db_core", service = "minio" }`); that **variant service** is what carries the capability: its health stands for it, its endpoints are its endpoints, it is the gate's `exec_in` target and `image_from` source, and the target of empty-contract edges. The primary is the default when a variant names none, and always the probe container for hook-provided facts declared under its key.

**Waves.** Realizations are deployed as units; a Realization's level is the maximum level of its services; waves are the topological levels of the Realization graph (a cycle at Realization level is an ERROR naming the cycle even when the service graph is acyclic — deliberate: units deploy whole). Waves are written to the rendered file so consumers and the gate read the ordering ciu actually used:

```toml
[ciu.instance.resolved]
waves = [["tailscale_node"], ["vault"], ["consul_server", "redis_core", "db_core"], ["db_init", "authentik"], ["controller"], ["worker_io", "worker_db"], ["reverse_proxy"]]
edges = [ { from = "controller.controller", to = "db_core.postgres", kind = "init" }, { from = "controller.controller", to = "vault.vault", kind = "secret→vault" }, … ]
[ciu.instance.resolved.gates.2]        # what ciu waits for before starting wave 3 (this host's share)
healthy   = ["db_core.postgres", "db_core.minio", "consul_server.consul", "redis_core.redis"]
completed = ["db_core.postgres_init", "db_core.minio_init"]
```

**Health gate.** Between waves ciu waits for every service on this host that a later wave has an edge to (derived from the same graph — the gate is on by default whenever the graph has an edge, CIU-68 closed by construction). Two distinct timeouts (CIU-67): the container `HEALTHCHECK` probe timeout (`health.timeout`, a few seconds, per service) and `health.gate_timeout` — the budget the gate waits for convergence, defaulting to `start_period + interval × retries + 30 s`, declared per service only to tighten it. `deploy.health` supplies the defaults; ciu merges them into every service's `health` table before any render, so templates author their `healthcheck:` stanza from `ciu_stack.<svc>.health.*` without conditionals; a gate provider that is not `one_shot` must declare one. Fact probes (`pg:role/…`, `vault:secret/…`) run at the consumer's wave for providers on the same host with the same bounded poll (`starting`/`unreachable` retried, `absent` reported as such when the budget expires); for providers on another host CIU probes reachability of the derived route only — the provider host's own gate is authoritative for its facts (no SSH, no cross-host `docker exec`).

**Probe targets are resolved, not hardcoded.** A fact is probed inside the container that *provides* it — the RealizedService whose `init_provides` or directive derives it, or the service under whose key `[hooks.provides]` lists it — using the derived identity; nothing assumes a service is keyed `postgres` or that the superuser is `postgres` (the provider declares `probe_user`).

### 4.1.7 Realness

Levels: `live` (the real realization; its init graph runs), `owned-seeded` (a **prepared** realization the project owns — its own stack or image with configuration and data baked in — whose `pg:`/`minio:`-class contract facts hold by construction and are listed in `init_provides`; its `vault:secret/*` contract facts are written to Vault by a hook, declared in `[hooks.provides]`), `simulated` (a stub realization implementing the contract's protocol), `mock` (an in-process double: declared as an empty variant `mock = {}` with no Realization; consumers depending on it get no edge and no route). `owned-seeded` and `simulated` are real Realizations like any other (no inline image/stub blocks).

Selection (`ciu up --realness main_db=owned-seeded …`) resolves per LogicalService with precedence CLI > `[ciu.instance.realness.<layout>]` (the instance's durable record for that layout) > `[deploy.realness.pin].<logical>` (committed pins) > `[deploy.realness] default = "live"`. There is no per-category default: a project that wants a different default for one class of services pins those services. The **deploy set** is the closure of the selected Realizations; `ciu check` refuses a selection whose level has no variant declared for that service (P2).

Immutability: the first `ciu up` of a layout writes the resolved selection into the CIU-owned generated file, keyed by layout, so a record made on a laptop never governs production:

```toml
[ciu.instance.realness.local]     # written by ciu at first `ciu up --layout local`; do not hand-edit
main_db       = "owned-seeded"
probe_targets = "simulated"
```

Any later mutating verb with a conflicting explicit selection refuses (`[realness] layout 'local' already runs main_db=owned-seeded; run ciu clean --vanilla to reselect`). The record is data, visible, survives `ciu clean` (only `--vanilla` clears the current layout's record), and `ciu push` strips other layouts' records from the bundle.

**Shared-infra join as a realization kind.** A joining instance declares, in its own overlay:

```toml
[realization]
primary_vault = { kind = "joined", instance = "primary", service = "vault" }   # instance: a registered instance's label or checkout basename, or an absolute path
[service.vault]
live.realized_by = "primary_vault"     # the level the reference ACTUALLY runs; a mismatch is refused, and ciu records the reference's level here
```

`ciu instance add --join primary --services vault,main_db` writes exactly these tables into the overlay (it creates no git worktree; a monorepo root hand-writes the same). At `ciu up` ciu reads the reference's rendered `[ciu.instance.resolved]` under a shared lock, refuses if the reference is not up or runs the service at a different level, records the reference's level in the joiner's own record, and derives routes as "reference's container name on the reference's instance network". A reference cannot `clean` while a joiner's containers are still attached to its network. Nothing is inherited implicitly and nothing is accepted "as good enough": the realness record never lies.

### 4.1.8 Secrets

Declaration stays on the RealizedService (or, for a secret several services of one stack share, once at stack level under `[ciu_stack.secrets.<key>]`), directive grammar unchanged (`ASK_VAULT`, `GEN_TO_VAULT`, `GEN_LOCAL`, `ASK_EXTERNAL`, `ASK_FILE`, `GEN_EPHEMERAL`) plus `ASK_HOST:<entry>` for host-scoped secrets, with `delivery` **mandatory** — there is no default (P2, P3):

```toml
[ciu_stack.controller.secrets]
postgres_password  = { directive = "ASK_VAULT:{{ vault.paths.postgres_controller_password }}", delivery = "file" }            # → /run/secrets/postgres_password (bind-mounted temp copy)
bootstrap_token    = { directive = "GEN_TO_VAULT:{{ vault.paths.controller_bootstrap_token }}", delivery = "env", env_name = "CONTROLLER_BOOTSTRAP_TOKEN" }   # process env (restart-bound; listed by ciu check)
vault_role_id      = { directive = "ASK_VAULT:{{ vault.paths.controller_vault_role_id }}", delivery = "env", env_name = "CONTROLLER_VAULT_ROLE_ID" }          # the AppRole bootstrap credential …
runtime_secrets    = { directive = "ASK_VAULT:{{ vault.paths.authentik_bootstrap_token }}", delivery = "native" }              # … and what the app fetches itself with it (declared for the edge, delivered by nobody)
[ciu_stack.postgres.secrets]
workerdb_ddl_password = { directive = "GEN_TO_VAULT:{{ vault.paths.postgres_workerdb_ddl_password }}", delivery = "none" }     # minted here for others; nothing delivered here
[ciu_stack.exporter.secrets]
consul_token = { directive = "ASK_VAULT:{{ vault.paths.consul_docker_stats_exporter_token }}", delivery = "configfile" }       # only `secret("consul_token")` inside this service's configfile templates sees it
[ciu_stack.nginx.secrets]
tls_cert = { directive = "ASK_HOST:tls_cert_pem", delivery = "file" }                                                        # the placement host's own entry from ciu.hosts.toml
```

`delivery` ∈ `file` | `env` | `configfile` | `native` | `none`; a secret may carry `enabled` like a service. `configfile` exists for applications that cannot read `_FILE` indirection: the rendered `ciu.rendered.<svc>.<cfg>` then is a secret-bearing artifact (mode 0400, removed on down/clean, exempt from the secret-free scan by declaration); `secret()` is never available in compose templates or stack TOML files.

Values live in **one** gitignored file, `ciu.secrets.toml`, written atomically under the project secrets lock (an `flock` on the repo-root directory descriptor — the store itself is never the lock):

```toml
[secrets.db_core.postgres.controller_password]     # [secrets.<realization>.<service>.<key>], or [secrets.<realization>.<key>] for stack-level secrets
value = "…"  source = "GEN_TO_VAULT:db/postgres/controller_password"  created = 2026-08-30T10:11:12Z
[secrets.vault.vault.root_token]                   # Vault bootstrap state, keyed by the resolved Vault Realization and its variant service — written by the vault hook's output (source = "hook:post_compose_vault.py")
[secrets.hosts.tsstammtisch.tls_cert_pem]          # host-scoped entries (the realization name `hosts` is reserved); pushed only to that host
```

At `ciu up`, `file` delivery writes `ciu.secret-temp-copy.<svc>.<key>.txt` (mode 0400, owner per `uid`) next to the stack and the rendered `ciu.compose.yml` mounts it under `secrets:` by absolute host path (stack-level: into every service whose rendered block references `/run/secrets/<key>`); `env` delivery passes the value through the compose process environment — which consists of exactly `deploy.env.shared`, the selected profiles' `env_overrides`, `COMPOSE_PROFILES` and the `env_name` values, nothing else — where the template references it as `${env_name}`. The rendered compose file therefore never contains a value, and `ciu check` scans every rendered artifact for any store value appearing verbatim. `GEN_EPHEMERAL` values are never stored. Why the compose YAML still exists as a file: `docker compose` consumes YAML, hooks and diagnostics need the same file compose ran, and remote hosts render on target — the instance TOML is the *data* about secrets (which key, which delivery), the YAML is the *artifact* docker reads. Vault bootstrap state (root token, unseal keys) moves out of `[state]`; hook state lives in a flat `ciu.state.toml` per stack and refuses secret-shaped keys.

**Certificates for TLS networks.** For every Realization with a route over a `tls ≠ none` network `N` (consumers) and every Realization with an endpoint reached over it (providers), ciu derives stack-level `file` secrets `tls_cert`/`tls_key` (and `tls_ca`) with directives `ASK_VAULT:pki/<N>/<realization>/{cert,key,ca}`; routes and the provider's endpoint table carry their `/run/secrets/tls_*` paths; the `pki` service's contract must carry `pki:issuer/<N>`, provided by the hook that issues certificates, which also satisfies those `ASK_VAULT` paths (ciu runs no CA).

Static rules: a `*_VAULT` directive anywhere with no `[vault] service = "<logical>"` pointer is a `ciu check` ERROR; the Vault Realization is the selected variant of that logical service — no stack-name heuristics; `[vault.paths]` is a consumer DRY table ciu never reads. Rotation is out of ciu's scope by design: a secret the application fetches and refreshes itself is declared `native`, and ciu's schema places nothing in the way of that path (scenario 8).
### 4.1.9 Instances, locking, lifecycle

**Every checkout is an instance.** `ciu instance init [--host <h>] [--layout L] [--bundles …]` (replacing `ciu env generate`) derives the identity exactly as before (a hash of the physical path), creates the hand-edited overlay from a template when absent, and writes the CIU-owned generated file — the instance identity, the host-local facts of the machine it ran on, and later the build facts and realness records:

```toml
# ciu.instance.generated.toml — CIU-owned, plain TOML, merged after the overlay, rewritten whole
[ciu.instance.generated]       # instance IDENTITY — identical on every host of a layout (the file travels with the push bundle)
instance_id = "98535c"
[ciu.host.generated]           # HOST-LOCAL facts — regenerated on each host by `ciu instance init --host <h>`; never part of an identity
name = "localhost"             # which [deploy.hosts.<h>] this machine is
repo_root = "/workspaces/dstdns"
physical_repo_root = "/home/vb/volkb79-2/dstdns"
public_fqdn = "gstammtisch.dchive.de"
env_type = "devcontainer"
user_uid = 1003  user_gid = 1003  docker_gid = 994
[ciu.instance.build]           # written by `ciu build`
build_version = "2026.08.30-9f3c1a2"  build_time = "2026-08-30T11:02:41Z"
[ciu.instance.realness.local]  # §4.1.7, one table per layout
main_db = "live"
```

The overlay `ciu.global.instance.toml.j2` holds operator data only: `[ciu.instance] layout = "local"  bundles = ["all", "test"]  label = "primary"`, optional `[ciu.instance.host_ports]` overrides, and any `joined` Realizations with their variant rows — so a hand-edited Jinja file is never machine-rewritten. `ciu instance list/show/add/remove/exec/reap` are the former worktree verbs renamed (CIU-50); `ciu worktree …` remains as an alias for one release; `ciu instance exec --env <e>` runs a command in a gate environment, so there is no separate exec-target table. The budget/lease table becomes `[ciu.instances] max_concurrent = 3  lease_ttl_hours = 72` (one closed key set, CIU-69). The per-checkout registry record is `ciu.instance.json`.

**Mutex.** Verbs fall in three classes. *Mutating* verbs (`up`, `down`, `clean`, `render`, `push`, `activate`, `build`, `instance init/add/remove/reap`, `secrets reset/migrate/rotate-bootstrap`) open the rendered `ciu.global.toml` with `O_CREAT|O_RDWR`, take an exclusive `flock`, and render in place so the inode — and therefore the lock — is stable; the render ends with the table `[ciu.instance.resolved.render] complete = true` as the last table of the file. The *gate* (`ciu gate`, including nested `ciu gate` calls from a host-environment conjunction lane) takes a **shared** lock: gates coexist with each other under the gate's own admission, and block only against mutating verbs. *Read-only* verbs (`check`, `env print`, `instance list/show/exec`, `diagnose`, `secrets show`) open the file read-only with a shared lock: an absent or empty file means *not rendered* (permitted before a mutating verb — `ciu clean` truncates to zero bytes), a non-empty file without the completion table is *torn* and refused. The automatic `ciu check` inside a mutating verb reuses the verb's descriptor. After acquiring, ciu compares `fstat` of the descriptor with `stat` of the path and retries if the file was replaced meanwhile. Contention fails fast (`[lock] instance 98535c is locked by another ciu process; pass --wait`, naming pid and verb when `/proc/locks` allows); a dead holder's `flock` is released by the kernel, so there is no lock-breaking verb. Lock order when several are needed (deadlock-free by construction): instance lock → stack directory descriptor (the per-stack secret phase) → project secrets lock (an `flock` on the repo-root directory descriptor; `ciu.secrets.toml` is written atomically and is never the lock) → git-common-dir registry locks → a joined reference's instance lock (shared). The interleavings this closes: two `ciu up`; `up` ‖ `gate` exec lane; `instance init` ‖ render; `clean` ‖ `up`; two first-`up` realness selections; `secrets migrate` ‖ `up`.

**Lifecycle.** `ciu instance init` → `ciu check` (automatic before every mutating verb, CIU-64; stage 1 skips "overlay present" for `instance init` and "no `.ciu/`" for `secrets migrate`, the two verbs that create their own preconditions) → `ciu up [--layout L] [--bundles …] [--realness …]` → `ciu gate …` → `ciu down` / `ciu clean [--vanilla]`. `ciu clean` removes containers, networks, volumes labeled to the instance, temp secret copies and rendered artifacts (by the gitignore list) and truncates the rendered global file; it preserves the overlay, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.instance.json` and the realness record; `--vanilla` clears the record, the store and the registry entry. Leases and the concurrency budget are unchanged in mechanics.

### 4.1.10 The testing gate — `ciu gate` (run-gate absorbed)

run-gate's implementation (`run-gate.py`, 1703 stdlib lines, 180 tests) moves into `ciu/gate/` with its tests; `run-gate-project` is frozen at v23 and deprecated after one overlap release; the estate's lane vocabulary (lanes, environments, budgets, artifacts, admission, NOT_RUN) is kept. What changes is that every fact run-gate re-derived is now read from the instance's rendered file, and the schema is expressed in the entity model.

```toml
[testing]
cgroup_slice = "ciu-gate.slice"                # every lane runs inside this slice; default = governance.cgroup_parent
evidence_dir = "ciu-gate-evidence"             # artifacts and verdicts; must be gitignored (ciu verifies)

[testing.judge]
version = ">=2.4"                              # a FLOOR (estate version policy); the tester image installs the newest release satisfying it. Provenance is always required.

[testing.environments.tester]                  # exec into a running RealizedService of THIS instance
mode = "exec"
exec_in = "tester"                             # a LogicalService → its variant's service; container from the derived identity, must be healthy → else NOT_RUN/environment-down
forward_env = ["RUN_LIVE_TESTS"]
extra_mounts = ["/var/run/docker.sock:/var/run/docker.sock"]

[testing.environments.clean]                   # ephemeral container on the instance network, in the slice
mode = "ephemeral"
image_from = "tester"                          # reuse a RealizedService's image (one fact, P1) — or image = "dstdns/test-runner:latest"
# `host` is a built-in environment (a plain subprocess) unless a table redefines it

[testing.lanes.unit]
kind = "command"
environment = "clean"
argv = ["pytest", "-q", "tests/unit"]
budget = "10m"                                 # enforced: BUDGET_EXCEEDED when exhausted
clean_tree = true
artifacts = ["coverage.json"]
resources = { memory_max = "2G", memory_swap_max = "0", cpu_weight = 100, io_weight = 100 }

[testing.lanes.durable_dlq]
kind = "assay"
environment = "tester"
assay_lane = "durable_dlq"                     # ciu runs: assay run durable_dlq --file assay.toml --require-judge-provenance --verdict-json <evidence_dir>/durable_dlq/verdict.json [--request-base REF]
request_base = false                           # true → pass --request-base (the assay lane declares judge.base_source = "request"); ciu never reads assay.toml beyond lane names
required_env = ["RUN_LIVE_TESTS"]
requires = { realness = { main_db = "live" }, services = ["main_db", "vault"] }
resources = { memory_max = "4G", shared = ["main_db"] }   # admission serializes lanes sharing a name on ciu.gate.shared-<realization>.lock
```

Semantics: `requires.realness` mismatching the layout's record → `NOT_RUN/realness-mismatch`; a `requires.services` entry whose variant service is not healthy (a container without a healthcheck counts as healthy when running; `one_shot` when completed) → `NOT_RUN/service-down`; an `exec` lane runs inside the target container's cgroup, so its `resources` must be ≤ that container's governance (caps that must differ need an `ephemeral` environment); the judge floor is checked against `assay --version` in the environment before any lane runs, and each verdict's `judge_provenance` is copied into the LaneResult (`ciu.gate.<lane>.json`, also under `evidence_dir/<lane>/`) together with the resolved request base — ciu computes no digest of its own (B018/A-327). `--request-base REF` is passed to lanes whose assay lane declares `judge.base_source = "request"` (B019/A-328); `REF` is `--base` if given, else the merge-base of `HEAD` and the checkout's upstream (no upstream → refusal). Resource keys are the cgroup-v2 names with underscores (`memory_max`, `memory_swap_max`, `memory_high`, `memory_low`, `memory_min`, `cpu_weight`, `cpu_max`, `io_weight`, `pids_max`), the same set `[governance]` uses for stacks (governance adds device-level `io_*_max`, `device`, `baseline_path`), enforced by the one cgroupfs admission path RG-20 shipped — admission by RAM headroom within the slice, never a global serialization. `required_env ⊆ forward_env` is enforced for container environments only (a host lane has no forwarding). Zero-stack mode: a project with no Realizations declares only `[deploy] project_name`, `[testing]`, and a `local` layout with empty bundles; `ciu gate` then needs no `ciu up`. `ciu gate --list`, `--dry-run`, `--json`, `--worktree PATH` (run against another checkout's instance), `--allow-dirty`, `--check-env`, `--admission-wait D`, and `ciu gate doctor` cover run-gate's CLI; `validate-pointers` becomes `ciu check --gates`; run-gate's env knobs survive as `CIU_GATE_EXTRA_MOUNTS`, `CIU_GATE_MOUNT_ALIAS`, `CIU_GATE_EVIDENCE_DIR`, `CIU_GATE_CGROUPFS_ROOT`. There is no central lane config in v8; assay's own `[lanes.<n>.where]` stays reserved and unread by assay (WHERE is the gate's).

### 4.1.11 `ciu check` in v8 (runs automatically before every mutating verb)

| stage | checks | refusal class |
|---|---|---|
| 1 files | gitignore list present; overlay present (skipped for `instance init`); no `.ciu/` directories (skipped for `secrets migrate`); rendered file not torn; `revision = 8` | ERROR |
| 2 render | Jinja with `StrictUndefined`, no `env` context, no loader; TOML parse; secret-free scan of templates **and** rendered artifacts; stack TOML files do not read `ciu_stack.*` | ERROR |
| 3 schema | closed key sets per table (§4.5), types, closed vocabularies, name grammar, no consumer scalars on service tables | ERROR |
| 4 references | every `realized_by`, variant `service`, `init_requires`, `after`, `depends_on` (siblings), `enabled` flag, `produced_by` profile, `exec_in`, `image_from`, `requires.services`, `resources.shared`, `vault.service`, `network.realized_by`, `network.pki`, `allow_from`, `profiles.services`, `layouts.hosts.*.bundles`/`reach`, joined `instance`/`service` resolves; stack `location` exists with both files and its root is `ciu_stack`; no reserved service keys; no shared `location`; exactly one `primary` per multi-service stack; endpoint names unique per Realization; no Realization named `hosts` | ERROR |
| 5 contracts | contract conformance over declared + derived provides (§4.1.6); unclaimed facts (WARN); a minter for every `ASK_VAULT` path | ERROR / WARN |
| 6 graph | cycles; waves computed; redundant `after` (WARN); routes to mocked services; derived edges listed | ERROR / WARN |
| 7 topology | every layout host exists with an address on every non-`instance`, non-proxy network in `reach`; one placement per Realization; every derived route resolves (`publish`, shared network, `allow_from`); proxy networks have `fqdn` and `realized_by`; TLS networks have `pki` with `pki:issuer/<n>`; `host_port` unique per host across the deploy set; push order consistent with cross-host edges | ERROR |
| 8 identity | derived names unique across the deploy set; rendered compose `container_name`/`hostname` equal the derived values or absent; compose keys qualified | ERROR |
| 9 secrets | `delivery` present on every secret; `env_name` present and unique per stack for `env`; vault pointer present when any `*_VAULT`; every `ASK_VAULT` path has a minter; env-delivered secrets listed | ERROR / WARN |
| 10 realness | selection valid for every service; consistent with `[ciu.instance.realness]`; joined references resolvable and level-consistent | ERROR |
| 11 governance/resources | cgroup keys valid and in range; slice resolvable; `memory_min` headroom | ERROR / WARN |
| 12 testing | every `assay_lane` names a lane in `assay.toml` (lane names parsed with `tomllib`, nothing else interpreted); environments/requires/shared resolve; `required_env ⊆ forward_env` for container environments; judge floor satisfiable by the installed judge; `evidence_dir` ignored and writable | ERROR |
| 13 hooks | `validate_config(config, ctx) -> list[Finding(severity, message)]` (S9.5 with CIU-65's severity via `ciu.exit_on`) | per finding |
| 14 registry validator | `ciu.registry_validator` (S13.4b), unchanged | ERROR |
| 15 live (`--live`, and inside `ciu up`) | health/completed/fact probes with bounded poll; cross-host reachability; joined references up | ERROR |

Stage 6's static check is complete without carve-outs: there is no `stack:*` ref kind to special-case (CIU-63) because dependencies are LogicalService names whose truth is decided by the graph, and fact probes are live by definition.

### 4.1.12 CLI verbs (v8)

`ciu instance init [--host H] [--move] [--label L] | list | show | add --join <ref> --services … | remove | exec --target <alias> | reap` (S16 renamed; `worktree` alias one release) · `ciu check [--live] [--layout L] [--host H] [--graph] [--gates] [--json]` · `ciu render` · `ciu up [--layout L] [--host H] [--bundles b,…] [--realness s=l,…] [--wait[=D]] [--probe-external] [--no-check]` · `ciu down [--realization r]` · `ciu clean [--vanilla]` · `ciu gate [lanes…] [--base REF] [--dry-run] [--list] [--json] [--worktree PATH] [--allow-dirty] [--check-env] | doctor` · `ciu secrets show [--values] | reset <sel> | migrate | rotate-bootstrap` · `ciu env print` (derived export of the instance and host facts plus the instance network — the only producer of `ciu.env`) · `ciu build [--realization r]` (project-owned images via `deploy.registry`; writes `[ciu.instance.build]` with version, time and image digests; `vendor_images` are pulled, never built) · `ciu diagnose` · `ciu push | activate` (§4.1.5). `--realization r` restricts a verb to one Realization (replaces per-directory invocation). There is no lock-breaking verb: `flock` is released by the kernel when its holder dies.

### 4.1.13 Migration shape (v7 → v8; stated, not implemented)

1. **Instance:** run `ciu instance init` in every checkout (primary included); it writes the overlay (renamed from `ciu.global.worktree.toml.j2` — the old name is accepted with a WARN for one release, then refused) and stops writing `ciu.env`. Cockpit aliases switch to `eval "$(ciu env print)"`.
2. **Global config:** `revision = 8`; `project_name` becomes a literal (no `$REPO_NAME`); delete `deploy.environment_tag`, `deploy.network_name`, `deploy.environment`, `[deploy.phases]`, `[deploy.groups]`, `[topology.*]`, `[service.<n>] type/location`, `[deploy.resources]`, `ciu.repo_root`/`physical_repo_root`; write `[service.*]` contracts and variants (with `service = …` where one stack backs several capabilities), `[realization]`, `[network.*]`, `[deploy.profiles.*] services`, `[deploy.layouts.*]` (explicit, even for one host), `[deploy.realness] default`, `[vault] service`, `[testing.*]`; rename `[governance]` keys to the cgroup vocabulary; `[ciu.worktree] max_concurrent_instances` → `[ciu.instances] max_concurrent`.
3. **Stack files:** re-root `[<root>.<svc>]` → `[ciu_stack.<svc>]`; move consumer scalars into sub-tables (never named `identity`/`health`/`endpoints`/`hostdir`/`configfile`/`secrets`); move stack-level `requires`/`provides` onto the services as `init_requires` (ordering + route) or `uses` (route only) / `init_provides` (facts not derivable from directives) / `[hooks.provides.<svc>]` (hook-minted facts); mark `primary` in multi-service stacks and `per_host` on transport daemons; add `endpoints` (`publish` only for always-published or proxied endpoints — cross-host publication is derived); add `delivery` (and `env_name`) to every secret; declare shared secrets once at stack level; drop hand-declared replica services in favor of `instances`; drop `name`, `stack_name`, inlined `image_name`/`internal_port` copies, `env_required`, and `[state]` (hook state moves to `ciu.state.toml`).
4. **Compose templates:** delete `container_name`, `hostname`, `networks`, `secrets`, `depends_on`, `ports`, the `x-defaults` label/resource anchors, `${VAR:-fallback}` forms and `{{ env.* }}`; replace hand-assembled names with `{{ ciu_stack.x.identity.* }}`, `topology.services.*`/`app_identity.*` reads with `routes.*`/`realization.*`, `${CONTAINER_UID}`/`auto_generated.*` with `instance.*`; replicated services iterate `identity.replicas`. **Config-file templates** (`config.toml.j2`) need the same re-rooting (`webapp_server.*` → `ciu_stack.server.*`, `deploy.environment_tag` → `instance.id`, `topology.*` → `routes.*`), and every `secret()` call needs `delivery = "configfile"` on its secret.
5. **Secrets state:** `ciu secrets migrate` copies `.ciu/secrets/*` (per stack and project) and `[state]` Vault bootstrap values into `ciu.secrets.toml`, then deletes `.ciu/`. No secret is re-minted. `PUBLIC_TLS_*_PEM` become host-scoped secrets (`ASK_FILE`) on the proxy's host.
6. **Gate:** `run-gate.toml` → `[testing.*]` (mechanical: environments/lanes keep their names; `pins`/`assay_command`/`container_name` deleted; `memory` → `resources.memory_max`; `RUN_GATE_*` env knobs → `CIU_GATE_*`); `assay.toml` `derived:` facts repointed at `ciu.instance.resolved.*`; verdicts move to `evidence_dir`; the vendored `.pyz` and its sha256 files deleted once the image carries the judge.
7. **dstdns specifics:** `app_identity.*` (~45 tables) and its 41 inlined copies are deleted — the merged `realization.*` view replaces them; `[vault.paths]` stays as a consumer user table; the two `landscape_id` values collapse to one; `deploy.phases` phase-level `enabled` flags (unread today) disappear with the phases; `tests/test_deploy_phase_ordering.py` retires; `pgadmin4-server.json`'s bare `"Host": "postgres"` becomes the derived hostname through a configfile; pwmcp's consumer aliases become `aliases`; tailscale declares `host_network = true`.

## 4.3a Where more than one design is genuinely valid

The interview converged on every fork it was asked (§4.3). Two questions remain deployment-dependent rather than resolvable in general; they are presented as choices, with a recommendation.

**A. Where machine-owned rendered artifacts live.**

| alternative | pros | cons | pick when |
|---|---|---|---|
| **Flat visible files next to the stack** (`ciu.rendered.<svc>.<cfg>`, `ciu.compose.yml`, temp secret copies) — *adopted default* | nothing hidden (P10); bind-mount sources stay inside the checkout so render-on-target needs no host-path facts; one gitignore list drives `ciu clean` | more files in a stack directory; a stack with many config files gets noisy | the common case; any checkout that is bind-mounted into containers |
| Per-instance state directory outside the repo (`$XDG_STATE_HOME/ciu/<instance_id>/`) | pristine checkout; one directory to wipe | bind-mount sources move out of the repo, so every host needs the state path as a fact; `ciu clean` scope becomes a directory again | read-only or shared checkouts where nothing may be written beside the sources |

Recommendation: flat files for the common case; the state directory only if a deployment forbids writes inside the checkout.

**B. Lock target.**

| alternative | pros | cons | pick when |
|---|---|---|---|
| **`flock` on the rendered `ciu.global.toml`, rendered in place, readers shared and read-only** — *adopted* | the file that holds the resolved state is the thing you hold to change it; no new file; the operator's preference | requires in-place rendering, a completion table, an `fstat`/`stat` check, and `clean` must truncate, not unlink; an external unlink forks the mutex | default |
| `flock` on the instance root directory fd | no file at all; inode stable for the life of the checkout; immune to render mechanics and external unlinks | less obvious to an operator (`lsof` shows a directory); directory-locking semantics differ on some network filesystems | checkouts on filesystems where in-place rewrite of the rendered file is undesirable, or where `git clean -x` is part of the workflow |

Recommendation: the rendered file; the directory fd if a consumer's filesystem or workflow makes the rendered file an unsafe lock object.

Topology/transport was flagged as the most likely candidate for this treatment; the interview converged on the network-entity model (§4.1.5) and it needs no alternatives.
## 4.4 What still needs to be built

Each item names the owning tool, the shape, and why it does not exist today (checked against source, not assumed). Order is a suggested serial carve; packages are sized for one implementer session each unless noted. `SPEC-V8.md` is the acceptance reference for every row.

| # | mechanism | owner | shape | why it doesn't exist yet |
|---|---|---|---|---|
| V8-1 | **Config schema v8 + `revision = 8` gate** (V8-S3) | ciu `config_model.py` | closed-key validators for every table in §4.5, generated from one declarative table-spec; `deploy.revision` mismatch refuses | today's validator covers phases/profiles/layouts, the S3.14 service registry, the worktree table and secrets; no registry/network/realization/testing tables exist (`config_model.py:170-180`, `:804-905`) |
| V8-2 | **Instance identity source** (V8-S4.1, S14.2): `ciu instance init [--host] [--layout] [--bundles]`, the hand-edited overlay plus the CIU-owned `ciu.instance.generated.toml` (`[ciu.instance.generated]`, `[ciu.host.generated]`, `[ciu.instance.build]`, per-layout realness records), `ciu env print`, `ciu.env` no longer read | ciu `workspace_env.py`, `paths.py` | S2.7 derivation unchanged; writer targets the generated file; readers of `ciu.env` (`paths.py:70/78`, `workspace_env.py:1167ff`) read the merged config | CIU-60 put six facts in the overlay but `ciu.env` remains the source (`workspace_env.py:1134-1220` writer; `paths.py` exact-path reader) |
| V8-3 | **Generic realization registry + logical services + variants (with `service`) + contract conformance over declared and derived provides** (V8-S5, S8.3) | ciu `config_model.py`, new `registry.py` | `[service.*]`, `[realization.*]` parsing; `GEN_TO_VAULT` → derived facts; `[hooks] provides`; conformance; minter resolution | S3.14's registry is flat with no contracts or variants (`config_model.py:838`); nothing checks provider coverage; `lint_graph` string-matches `provides` (CIU-63) |
| V8-4 | **Stack file re-rooting, reserved keys, closed service key set, merged view** (V8-S3.6, S6) | ciu `config_model.py` (`validate_stack_shape` at `:980`), `engine.py` | parse `[ciu_stack.<svc>]` + `[ciu_stack.secrets]`, bind under `realization.<n>`, expose `ciu_stack`/`routes`/`instance`/`stack_dir` in the right contexts | today one arbitrary root table per stack (S3.3/S3.5), no re-rooting, cross-stack facts hand-mirrored in dstdns `app_identity.*` |
| V8-5 | **Identity derivation + resolved table + compose enforcement** (V8-S4, S11.3–S11.4) | ciu new `identity.py`; `composefile.py`, `deploy.py:162` | one function (with service-name elision); `[ciu.instance.resolved.realizations.*.identity/endpoints]`; qualified compose keys; replica aliases; stage 8 | `deploy.container_name` has no stack component (CIU-66); templates hand-assemble names (70/68 refs in dstdns); run-gate re-derives (`run-gate.py:1319`) |
| V8-6 | **Networks/hosts/endpoints/layouts + route derivation + derived publication + `per_host` + `uses` + `allow_from` + TLS secrets** (V8-S7, S10.5) | ciu new `topology.py`; `hosts.py`, `deploy_pkg/layouts.py` | entities of §4.1.5; `route()` incl. proxy and joined cases; layout-derived `ports:`; `[ciu.instance.resolved.routes.*]`/`networks.*`; `routes` binding; derived `tls_*` secrets; stage 7 | layouts exist (`layouts.py:76`) but carry no networks/reach; hosts have no addresses (`hosts.py:40`); routes are consumer-typed `[topology.services]` (`providers.py:65` reads only vault's) |
| V8-7 | **Init graph, derived edges, waves, gates, bounded probes, provider-resolved probes** (V8-S8, S5.6) | ciu `provisioning.py`, `deploy.py:600-679`, `deploy_pkg/phases.py` | replace `ordered_phases` with wave computation; secret→vault/minter, network, pki edges; primary service; `gate_timeout`; poll; provider lookup | phases are hand-declared (`phases.py:30-46`); `lint_graph` string-matches (`provisioning.py:134-156`, CIU-63); probes hardcode `postgres`/`minio` (`provisioning.py:345-410`, CIU-70); one-shot probe (CIU-68); timeout conflated (CIU-67) |
| V8-8 | **Realness selection, immutable record, joined realizations** (V8-S9) | ciu `deploy.py`, `worktree.py` (S16.1 attach path) | precedence resolver; `[ciu.instance.realness]` writer/refuser; `joined` kind reading the reference's resolved table under a shared lock and recording its level | no realness concept exists; S16.1 join is `ciu worktree add`-scoped with `ref_services` generated (`worktree.py:745-807`) |
| V8-9 | **Secrets v8** (V8-S10): `ciu.secrets.toml`, mandatory `delivery` (five values incl. `configfile`/`none`), `env_name`, stack-level secrets, `HOST:` directive, temp copies, generated compose `secrets:` stanzas, `secret()` in configfile renders only, `ciu secrets migrate` | ciu `secrets/materialize.py`, `composefile.py:398/1074` | one store file, atomic, under a directory-fd lock; delivery axis; overlay YAML folded into the rendered compose | stores are `.ciu/secrets/<name>` files (S4.9); `expose_env` is the only non-file path (S4.19); overlay YAML generated (S4.17) |
| V8-10 | **`.ciu/` removal** (V8-S2): flat rendered artifacts, gitignore list check, KSM cache relocation, directory-fd stack lock, `ciu.hosts.toml`, `ciu.instance.json`, evidence dir | ciu `config_constants.py:71`, `composefile.py:677-868`, `governance.py` (KSM), `engine.py` S1.7 check, `hosts.py` | rename targets; new startup check | `MACHINE_DIR = '.ciu'` is wired through S1.6/S1.7/S4.9/S4.17/S4.26/S5.2/KSM; hosts file is `.ciu.hosts.toml` |
| V8-11 | **Instance mutex on the rendered file** (V8-S14.3–S14.4): in-place render with the completion table, exclusive/shared classes, read-only readers, `fstat` check, `--wait` | ciu `config_model.py:519 render_global_chain`, `cli.py` | `O_CREAT` + `flock` before render; `[ciu.instance.resolved.render] complete = true` last; readers `O_RDONLY` + shared | no instance-wide lock exists; only S4.26 per-stack secret locks and S16 git-common-dir locks |
| V8-12 | **`ciu gate`** (V8-S16; run-gate lifted): `[testing.*]` schema, `exec_in` via variant service, ephemeral on the instance network, `host` built-in, cgroup vocabulary + headroom admission, judge floor + provenance, request-base, LaneResult envelope, evidence dir, CLI surface, `CIU_GATE_*` knobs, zero-stack mode, stage 12 | ciu new `gate/` package (from `run-gate.py`), tests lifted | see §4.1.10 | run-gate is a separate tool re-deriving identity (`run-gate.py:1319-1361`), pinning by version triple, invoking assay only via `assay_command`, writing `.assay/` |
| V8-13 | **`ciu check` auto-run + severity findings + 15 stages** (V8-S15, S12.4) | ciu `cli.py`, `hooks_runner.py`, `warn_policy.py` | `Finding(severity, message, rule)`; auto before mutating verbs with the two stage-1 exemptions; stage table of §4.1.11; `--json` with rule ids | `ciu check` is manual (CIU-64); `validate_config` returns `list[str]` (CIU-65) |
| V8-14 | **Strict rendering** (V8-S3.2): `StrictUndefined`, no `env` context, no `$VAR` expansion, no loader | ciu `config_model.py:386` | `Environment(undefined=StrictUndefined)`; contexts per V8-S3.5 | default `Undefined` renders typos empty (CIU-74); `env` is raw `os.environ` (CIU-60 doctrine); `$VAR` expanded from ambient env |
| V8-15 | **`ciu instance` verb family + `[ciu.instances]` table + registry record** (V8-S14.6–S14.7, S18) | ciu `worktree.py`, `cli.py` | rename with alias; closed key set incl. `exec_targets`; `ciu.instance.json` | CIU-50 open; CIU-69 |
| V8-16 | **Compose injection** (V8-S11): identity/network/alias/label/secret/port/depends_on/configfile/governance stanzas injected; template prohibitions; disabled-service pruning; replica blocks | ciu `composefile.py` | parse rendered YAML, inject, validate, re-serialize | today templates hand-write everything; the S4.17 overlay adds only secrets |
| V8-17 | **assay wave merge + consumer repoint** | assay (`feature/assay-b018-b019-b035-v8-synergy`, not merged), dstdns `assay.toml` | merge/release the wave; `derived:` paths → `ciu.instance.resolved.*` | wave shipped on a branch only; dstdns `derived:` targets `ciu.deploy.network_name` |
| V8-18 | **run-gate deprecation** | run-gate-project | freeze at v23; README pointer; adopters migrate (8 trivial files, dstdns) | — |
| V8-19 | **dstdns migration** (25 stacks, global config, secrets state, compose and config-file templates, hooks, gate config) — `v8-dstdns-demo/` is the target shape | dstdns | per §4.1.13 and the demo's README | consumer work; sized by the 41 inlined identity copies, 19 compose templates, 8 configfile templates and 12 hook scripts |
| V8-20 | **SPEC v8 promotion** — `SPEC-V8.md` becomes `SPEC.md` at the v8.0.0 cut; CONFIG.md/CONSUMERS.md regenerated from the table-spec; run-gate SPEC folded in as the gate section | ciu docs | — | SPEC 5.0.0 documents the v7 model |

## 4.5 Validation — every key, every level, the entire schema as v8 leaves it

Columns: key | table / level | type | reason for existence | owner (who writes / who reads) | example. "Reason" is the justification for the key's independent existence; where a key survives only for convenience it is marked **drop candidate** and appears in §4.8. Closed vocabularies are spelled out. Keys whose values are derived are in the read-only tables (C) — a consumer that writes them is refused. `V8-S<n>.<m>` points at the normative rule.

### A. Global config (`ciu.global.defaults.toml.j2` → `ciu.global.toml.j2`)

#### A1 `[deploy]` — deployment-wide facts (V8-S3.4)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `project_name` | `deploy` | str `^[a-z][a-z0-9_]*$`, literal | the one human-chosen identity component; every derived name starts with it | consumer writes / ciu identity, compose, gate read | `"dstdns"` |
| `revision` | `deploy` | int, must be `8` | refuses a v7 config against v8 ciu and vice versa at parse time (P2) | consumer / ciu parser | `8` |
| `log_level` | `deploy` | str `DEBUG\|INFO\|WARN\|ERROR` | compose/engine verbosity; not derivable | consumer / ciu engine | `"INFO"` |
| `landscape_id` | `deploy` | str `^[a-z][a-z0-9-]{0,62}$`, optional | shared identity of one landscape across instances (Consul KV root); cannot be derived from a path hash | consumer / templates, hooks | `"dstdns-dev"` |
| `registry.url` / `registry.namespace` | `deploy.registry` | str / name | where project-built images live and how they are named (`<namespace>/<name>:<tag>`); one spelling for `ciu build`, provenance and templates | consumer / build, templates | `""` / `"dstdns"` |
| `labels.prefix` | `deploy.labels` | str | namespace for the Docker labels ciu stamps, so `clean`/`diagnose` enumerate by label | consumer / ciu engine | `"de.vxxu.volkb79"` |
| `health.interval` / `.retries` / `.start_period` / `.timeout` | `deploy.health` | duration / int / duration / duration | defaults merged into every service's `health` table; `timeout` is the HEALTHCHECK *probe* timeout (one attempt), distinct from the gate budget (CIU-67) | consumer / merge, templates | `"10s"` / `6` / `"240s"` / `"5s"` |
| `health.gate_timeout` | `deploy.health` | duration, optional | the inter-wave convergence budget when the derived default (`start_period + interval×retries + 30s`) is wrong for a deployment | consumer / ciu gate | `"300s"` |
| `env.shared.<VAR>` | `deploy.env.shared` | str | consumer-defined env passed to every compose process; identity facts no longer belong here | consumer / compose env | `DSTDNS_TELEMETRY = "on"` |
| `env.defaults.<VAR>` | `deploy.env.defaults` | str | template-context defaults for service `environment:` blocks | consumer / templates | `PYTHONUNBUFFERED = "1"` |
| `control.<flag>` | `deploy.control` | bool | named switches a service's or secret's `enabled` may reference by name — keeps expressions out of config | consumer / ciu filter | `enable_observability = true` |
| `provenance.vendor_images` | `deploy.provenance` | list[str] | image provenance exemptions; not derivable | consumer / `ciu build`/provenance | `["hashicorp/vault"]` |

#### A2 `[service.<n>]` — LogicalServices (WHAT) (V8-S5.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `service` | table key `^[a-z][a-z0-9_]*$` | the vocabulary every reference uses (profiles, `init_requires`, `exec_in`, `image_from`, `pki`, `vault.service`, gate `requires`) | consumer / everything | `[service.main_db]` |
| `description` | `service.<n>` | str, optional | human context in `ciu check` output | consumer / humans | `"main application database"` |
| `contract` | `service.<n>` | list[TypedFact] (may be empty) | the facts a consumer may rely on; drives contract conformance and live probes | consumer / ciu graph, probes | `["pg:role/controller", "pg:db/dstdns"]` |
| `live` / `owned-seeded` / `simulated` | `service.<n>.<level>` | table: `realized_by` (→ `realization.<r>`, required), `service` (svc key of that realization, optional) | one variant per realness level; `service` says which service of a multi-service stack carries THIS capability (default: the stack's primary) | consumer (overlay for joins) / resolver, graph, routes, gate | `live = { realized_by = "db_core", service = "minio" }` |
| `mock` | `service.<n>.mock` | empty table `{}` | declares that an in-process double is a legal selection; no Realization, no route, no edge | consumer / resolver | `mock = {}` |

#### A3 `[realization.<n>]` — HOW (one namespace across kinds; `hosts` is reserved) (V8-S5.4)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `realization` | table key `^[a-z][a-z0-9_]*$` | the stack/external/join name; identity component 3 | consumer (or overlay for `joined`) / identity, graph, templates | `db_core = { … }` |
| `kind` | `realization.<n>` | str `ciu_stack\|external\|joined` | selects the per-kind fields and the deploy behavior; one field instead of kind-named root tables (P7) | consumer / ciu | `"ciu_stack"` |
| `location` | `realization.<n>` (`ciu_stack`) | str, repo-relative dir, unique | binds the stack file to its name **once**; the file never states it | consumer / loader | `"infra/db-core"` |
| `per_host` | `realization.<n>` (`ciu_stack`) | bool, default false | a daemon that runs on every host whose bundles include it (transport nodes, node exporters); nothing routes to it | consumer / placement, network edges | `true` |
| `provides` | `realization.<n>` (`external`, `joined`) | list[TypedFact] | facts ciu cannot derive because it does not build the thing; asserted for conformance (joined: defaults to the reference's contract) | consumer / conformance | `["pg:db/dstdns"]` |
| `instance` | `realization.<n>` (`joined`) | str: registered label, checkout basename, or absolute path | which instance's realization is joined | operator or `ciu instance add` / join | `"primary"` |
| `service` | `realization.<n>` (`joined`) | str → LogicalService in the reference | which of the reference's services is joined | same | `"vault"` |
| `endpoints.<e>.url` / `.tls` / `.ca` | `realization.<n>.endpoints.<e>` (`external`) | str URL / `none\|tls\|mtls` / path | the reachable address and transport facts of something ciu does not run | consumer / routes | `url = "https://api.stripe.com"` |

#### A4 `[network.<n>]` (V8-S7.3)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `network` | table key; `instance` reserved (implicit) | a reachability domain hosts have addresses on (proxy networks are address-free) | consumer / routes, layouts | `[network.mesh]` |
| `kind` | `network.<n>` | str `address\|proxy` | decides route form (host address + host port vs FQDN + path); nothing else distinguishes address planes | consumer / route derivation | `"address"` |
| `description` | `network.<n>` | str, optional | what the plane is ("tailscale mesh"); ciu attaches no semantics | consumer / humans | `"tailscale mesh"` |
| `realized_by` | `network.<n>` | str → realization, optional (required for `proxy`) | transport readiness: the daemon/proxy that must be up before routes over this network work | consumer / derived edges | `"tailscale_node"` |
| `tls` | `network.<n>` | str `none\|tls\|mtls`, default `none` | transport security is a link property, inherited by every route | consumer / routes, derived TLS secrets | `"mtls"` |
| `pki` | `network.<n>` | str → LogicalService, required when `tls != none` | whose hook issues certificates for this network (`pki:issuer/<n>` in its contract) | consumer / routes, graph | `"vault"` |
| `fqdn` | `network.<n>` (`proxy`) | str, required for `proxy` | the public name routes through a proxy resolve to | consumer / routes | `"gstammtisch.dchive.de"` |

#### A5 `[deploy.profiles.<p>]`, `[deploy.layouts.<l>]`, `[deploy.realness]` (V8-S7.5, S7.6, S9.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `services` | `deploy.profiles.<p>` | list[LogicalService] (empty only in a zero-stack project) | a bundle = which capabilities deploy together; realizations follow from realness | consumer / deploy set | `["vault", "main_db"]` |
| `compose_profiles` | `deploy.profiles.<p>` | list[str], optional | compose-level profile activation the bundle needs | consumer / compose env | `["debug"]` |
| `env_overrides.<VAR>` | `deploy.profiles.<p>` | str, optional | bundle-specific compose env; conflicting values across selected bundles refuse | consumer / compose env | `LOG_LEVEL = "DEBUG"` |
| `environment` | `deploy.layouts.<l>` | str `dev\|test\|staging\|prod` | the one place "which environment" is declared | consumer / templates, hooks | `"prod"` |
| `description` | `deploy.layouts.<l>` | str, optional | human context | consumer / humans | `"three-host production"` |
| `hosts.<h>.bundles` | `deploy.layouts.<l>.hosts.<h>` | list[profile] (empty only in a zero-stack project) | placement; declaration order = push order | consumer / placement, push | `["core"]` |
| `hosts.<h>.reach` | `deploy.layouts.<l>.hosts.<h>` | list[network], non-empty; `instance` = this host only | which networks this host may use to reach others, in preference order | consumer / route derivation | `["mesh", "public"]` |
| `default` | `deploy.realness` | level | the level used when nothing more specific selects one; explicit so `live` is never an unstated assumption | consumer / resolver | `"live"` |
| `pin.<logical>` | `deploy.realness.pin` | level | committed per-service override of the default | consumer / resolver | `probe_targets = "simulated"` |

#### A6 `[vault]`, `[registry]`, `[governance]`, `[ciu]`, `[ciu.instances]` (V8-S10.3, S3.4.6, S13, S3.4.7, S14.6)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `service` | `vault` | str → LogicalService | names the Vault ciu's own providers talk to; replaces the stack-path/basename heuristic | consumer / secret providers, derived edges | `"vault"` |
| `token_file` | `vault` | path, optional | token source #2 for ciu's Vault client | consumer / providers | `"conf/vault-token"` |
| `paths.<name>` | `vault` | str | **consumer user table** (DRY for directive paths); ciu does not read it | consumer / templates | `db_controller = "db/postgres/controller_password"` |
| `postgresql.database` | `registry.postgresql` | str | the app database for `pg:schema/*` probes | consumer / probes | `"dstdns"` |
| `consul.token_vault_path` | `registry.consul` | str, default `consul/acl/tokens/{svc}` | where Consul tokens land for `consul:token/*` facts | consumer / probes | default |
| `<anything else>` | `registry` | table | project metadata validated only by `ciu.registry_validator` | consumer / consumer validator | `registry.postgresql.users.*` |
| `enabled` / `cgroup_parent` / `ksm_optin` / `exempt_services` / `memory_profile.*` | `governance` | bool / slice (`""` = env) / `builtin\|path` / list / tables | cgroup + KSM governance switches, unchanged in meaning | consumer / governance | `cgroup_parent = "dev-background.slice"` |
| `memory_max` / `memory_swap_max` / `memory_high` / `memory_low` / `memory_min` / `cpu_weight` / `cpu_max` / `io_weight` / `pids_max` | `governance` (and per-stack `[governance]`) | size / size / size / size / size / int / str / int / int | the **shared resource key set** (same names as lane `resources`): each is the cgroup-v2 file it writes; `memory_min` is preflight-only host headroom | consumer / governance, gate | `memory_max = "2G"` |
| `io_read_iops_max` / `io_write_iops_max` / `io_read_bps_max` / `io_write_bps_max` / `device` / `baseline_path` | `governance` | int (`0` = derive) / str (`""` = autodetect) / path | device-level I/O caps only stacks have | consumer / governance | `device = "/dev/vda"` |
| `require_fqdn` / `require_certs` / `standalone_root` / `auto_connect_network` | `ciu` | bool | refuse an instance without FQDN/TLS material when needed; root lock; attach the devcontainer to the instance network | consumer / instance init, engine | `false` |
| `exit_on` | `ciu` | str `WARN\|ERROR\|NEVER` | severity policy for `ciu check` findings (CIU-65 reuses it) | consumer / check | `"WARN"` |
| `user_tables` | `ciu` | list[str], default `[]` | consumer-owned top-level tables ciu must not reject | consumer / validator | `["workflow", "pubsub"]` |
| `registry_validator` | `ciu` | path | consumer validator for `[registry]` (check stage 14) | consumer / check | `"scripts/registry_check.py"` |
| `max_concurrent` | `ciu.instances` | int ≥ 1, optional | budget across a git family (ambient `CIU_MAX_CONCURRENT_INSTANCES` overrides) | consumer / `instance init/up` | `3` |
| `lease_ttl_hours` | `ciu.instances` | number > 0, optional; absent = no lease | expiry opt-in; no default on purpose | consumer / lease | `72` |
| *(exec targets)* | — | — | none: `ciu instance exec --env <e>` reuses `[testing.environments]` (an `exec` environment IS a declared target) | — | — |

#### A7 `[testing.*]` — the gate (V8-S16)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `cgroup_slice` | `testing` | str, optional (default `governance.cgroup_parent`) | the default cgroup every lane runs in; bounds interference with everything else on the host | consumer / gate admission | `"ciu-gate.slice"` |
| `evidence_dir` | `testing` | path, default `ciu-gate-evidence` | where artifacts and verdicts land (no hidden `.assay/`); must be gitignored | consumer / gate | `"ciu-gate-evidence"` |
| `judge.version` | `testing.judge` | version floor `>=X[.Y]` | the ONE judge pin (estate floor policy); verified against the installed judge; provenance from each verdict is always required | consumer / gate | `">=2.4"` |
| `environments.<e>.mode` | `testing.environments.<e>` | str `ephemeral\|exec\|host` | where a lane's process lives (`host` is built-in when not declared) | consumer / gate | `"exec"` |
| `environments.<e>.exec_in` | `testing.environments.<e>` (`exec`) | str → LogicalService | the container to exec into, by capability (its variant's service; identity derived, health required) | consumer / gate | `"tester"` |
| `environments.<e>.image` / `.image_from` | `testing.environments.<e>` (`ephemeral`) | str / str → LogicalService | what to run; `image_from` reuses a service's image so the fact is declared once | consumer / gate | `image_from = "tester"` |
| `environments.<e>.forward_env` / `.extra_mounts` / `.workdir` | `testing.environments.<e>` | list[env name] / list[`host:container[:mode]`] / path | explicit allow-list of forwarded env; extra bind mounts (the docker socket for conjunction lanes); where `{worktree}` lands | consumer / gate | `forward_env = ["RUN_LIVE_TESTS"]` |
| `lanes.<l>.kind` / `.environment` / `.argv` / `.assay_lane` / `.request_base` / `.description` | `testing.lanes.<l>` | `command\|assay` / env / list / assay lane / bool / str | who produces the outcome; where; the command; the judge lane (invocation derived); whether to pass `--request-base` (ciu never reads assay.toml beyond lane names) | consumer / gate, stage 12 | `assay_lane = "durable_dlq"` |
| `lanes.<l>.clean_tree` / `.budget` / `.required_env` / `.artifacts` | `testing.lanes.<l>` | bool (default true) / duration (enforced) / list ⊆ `forward_env` (container envs) / list[path] | evidence integrity; wall-clock cap; must-have env; outputs to collect | consumer / gate | `budget = "30m"` |
| `lanes.<l>.requires.realness` / `.services` | `testing.lanes.<l>.requires` | table logical → level / list[LogicalService] | preconditions read from the instance's record and the graph → `NOT_RUN/realness-mismatch` / `service-down` | consumer / gate | `{ realness = { main_db = "live" }, services = ["main_db"] }` |
| `lanes.<l>.resources.<RK>` / `.shared` | `testing.lanes.<l>.resources` | the shared resource key set / list[LogicalService] | per-lane cgroup caps (admission by headroom); exclusive use of realizations (per-name lock) | consumer / gate | `{ memory_max = "4G", shared = ["main_db"] }` |

### B. Instance overlay (`ciu.global.instance.toml.j2`, hand-edited) and generated file (`ciu.instance.generated.toml`, CIU-owned) — both gitignored, per instance (V8-S14.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `layout` / `bundles` / `label` | `ciu.instance` (overlay) | layout / list[profile] / str | which placement this instance deploys; default bundle selection; a human name for listings (never an identity) | operator or `ciu instance add` / `ciu up`, listings, registry | `layout = "local"` |
| `host_ports."<realization>.<svc>.<endpoint>"` | `ciu.instance.host_ports` (overlay) | int | per-instance host-port override so two instances on one machine can both publish | operator / publication | `"cadvisor.cadvisor.http" = 18080` |
| `[realization.<n>] kind = "joined" …`, `[service.<n>.<level>]` | overlay | see A3, A2 | joins are instance-scoped declarations | operator or `ciu instance add` / join | see §4.1.7 |
| `generated.instance_id` | `ciu.instance.generated` (generated file) | str hex | identity component 2; derived from the physical path; identical on every host of a layout | ciu / identity, everything | `"98535c"` |
| `host.generated.name` | `ciu.host.generated` (generated file) | host name | which `[deploy.hosts.<h>]` this machine is (render-on-target) | ciu / placement, routes | `"rs1002"` |
| `host.generated.repo_root` / `.physical_repo_root` / `.public_fqdn` / `.env_type` / `.user_uid` / `.user_gid` / `.docker_gid` | `ciu.host.generated` | path / path / str / `devcontainer\|native\|github-actions` / int / int / int | host-local facts templates and hooks read as `instance.*`; regenerated per host, never part of an identity | ciu / templates, hooks, engine | `env_type = "devcontainer"` |
| `build.build_version` / `.build_time` / `.images.<name>` | `ciu.instance.build` (generated file) | str / datetime / digest | what `ciu build` produced; the renderer copies them (render stays deterministic) | `ciu build` / templates, provenance | `build_version = "2026.08.30-9f3c1a2"` |
| `realness.<layout>.<logical>` | `ciu.instance.realness.<layout>` (generated file) | level | durable record of the first selection per layout (and of a joined reference's level); other layouts' records are stripped on push | ciu writes at first `up` / resolver, gate | `main_db = "owned-seeded"` |

### C. Rendered, derived, read-only (`ciu.global.toml` `[ciu.instance.resolved]`) — a consumer writing any of these is refused (V8-S3.7)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `facts_schema` / `render.complete` | `ciu.instance.resolved` / `ciu.instance.resolved.render` (the LAST table of the file) | int / bool | lets readers refuse a shape they don't understand and a torn render | ciu / assay, scripts, readers | `1` / `true` |
| `networks.<n>.name` / `.kind` / `.realized_by` / `.fqdn` / `.tls` | `ciu.instance.resolved.networks.<n>` | str… | every declared network plus the implicit `instance` one with its derived name | ciu / templates, assay `derived:` | `name = "dstdns-98535c-network"` |
| `layout` / `host` / `environment` | `ciu.instance.resolved` | str | what this render resolved for | ciu / gate, hooks | `"prod3"` / `"rs1002"` / `"prod"` |
| `services.<logical>.level` / `.realization` | `ciu.instance.resolved.services.<logical>` | level / realization (absent for mock) | the selection actually used | ciu / gate, templates, joins | `"live"` / `"db_core"` |
| `realizations.<r>.hosts` | `ciu.instance.resolved.realizations.<r>` | list[host] | placement result (one element unless `per_host`) | ciu / routes | `["gstammtisch"]` |
| `realizations.<r>.<svc>.identity.container_name` / `.hostname` / `.compose_key` / `.compose_project` / `.network` / `.replicas[]` | `…<svc>.identity` | str… | the one identity derivation, materialized (P6) | ciu / templates, hooks, gate, assay `derived:` | see §4.1.4 |
| `realizations.<r>.<svc>.endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.path` / `.published_on` | `…<svc>.endpoints.<e>` | as declared + list[network] | the endpoint facts joined instances and the gate read; `published_on` = the networks the layout made ciu publish it on | ciu / joins, gate, humans | `published_on = ["mesh"]` |
| `routes.<consumer>.<logical>.<endpoint>.network` / `.host` / `.port` / `.url` / `.path` / `.tls` / `.cert` / `.key` / `.ca` / `.requires` | `ciu.instance.resolved.routes.<c>.<l>.<e>` | str / str / int / str (http/https/udp only) / str / str / paths / list | how the consumer reaches the endpoint (§4.1.5); bound as `routes` in the consumer's render | ciu / templates, probes | see §4.1.5 |
| `waves` / `edges[]` / `gates.<k>.healthy` / `.completed` / `.facts` | `ciu.instance.resolved` | list[list] / list of `{from,to,kind}` / lists | the ordering ciu used and every edge incl. derived ones (`kind ∈ init\|depends\|after\|secret→vault\|secret→minter\|network\|pki`) | ciu / `ciu check --graph`, gate, consumers | see §4.1.6 |
| `governance.<r>.<svc>.*` | `ciu.instance.resolved.governance` | the resource key set | effective caps applied per container | ciu / humans, gate | — |
| `build.build_version` / `.build_time` | `ciu.instance.resolved.build` | str / datetime | build facts templates read as `instance.build_*` | ciu / templates | `"2026.08.30-9f3c1a2"` |

### D. Host inventory (`ciu.hosts.toml`, gitignored; `~/.config/ciu/hosts.toml` user-global) (V8-S7.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `local` | `deploy.hosts.<h>` | bool | marks this machine; no SSH facts required | operator / placement | `true` |
| `ssh_host` / `ssh_user` / `ssh_port` / `ssh_key` / `known_host` | `deploy.hosts.<h>` | str / str (`root`) / int (22) / directive or path / str | push transport facts; `known_host` absence refused unless `CIU_SSH_INSECURE_TOFU=1` | operator / push | `"ops@core-b"` |
| `bundle_dir` / `push_mode` / `bundle_excludes` / `docker_optional` | `deploy.hosts.<h>` | str (`/opt/ciu/current`) / `auto\|rsync\|scp` / list / bool | bundle mechanics, unchanged | operator / `ciu push` | `"/opt/ciu/current"` |
| `activate.bootstrap` / `.apply` / `.health` / `.rollback` | `deploy.hosts.<h>.activate` | str | per-verb remote commands | operator / `ciu activate` | `"ciu up --layout prod3"` |
| `admin` | `deploy.hosts.<h>.admin` | table | merged only with `--admin` | operator / hosts | — |
| `secrets.<entry>` | `deploy.hosts.<h>.secrets` | directive (`ASK_EXTERNAL`/`GEN_LOCAL`/`ASK_FILE`) | host-scoped secrets, stored under `[secrets.hosts.<h>]`, consumed by services through `HOST:<entry>` | operator / hosts, secrets | `tls_cert_pem = "ASK_FILE:/etc/ssl/edge.pem"` |
| `addresses.<network>` | `deploy.hosts.<h>.addresses` | str (IP or hostname) | the host's address on each addressed network; the input to every cross-host route | operator / route derivation | `mesh = "100.64.0.12"` |

### E. Stack file (`<location>/ciu.defaults.toml.j2` → `ciu.toml.j2` → `ciu.toml`) (V8-S6, S10.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<svc>` | `ciu_stack` | table key `^[a-z][a-z0-9_]*$`, not reserved | one RealizedService; identity component 4 | consumer / identity, compose | `[ciu_stack.postgres]` |
| `image` | `ciu_stack.<svc>` | str | the one place the image is declared (replaces `image_name`+`image_tag` copies) | consumer / compose, build, `image_from` | `"postgres:16"` |
| `instances` | `ciu_stack.<svc>` | int ≥ 1, default 1 | replica fan-out with derived per-replica identities | consumer / identity, templates | `2` |
| `one_shot` | `ciu_stack.<svc>` | bool, default `false` | the service runs to completion; gates wait for exit 0 | consumer / graph, gate | `true` |
| `primary` | `ciu_stack.<svc>` | bool; required once in a multi-service stack | the service whose health stands for the Realization by default | consumer / graph, gate | `true` |
| `enabled` | `ciu_stack.<svc>` | bool or `deploy.control` flag name | conditional inclusion without expressions | consumer / deploy set | `"enable_observability"` |
| `init_requires` | `ciu_stack.<svc>` | list[LogicalService] | init-time dependency by capability: route + ordering edge (§4.1.6) | consumer / graph, routes | `["vault", "main_db"]` |
| `uses` | `ciu_stack.<svc>` | list[LogicalService] | runtime-only dependency: route, no ordering edge (a tracing collector, a proxy's backends) | consumer / routes | `["tracing"]` |
| `init_provides` | `ciu_stack.<svc>` | list[TypedFact] | facts this service creates by means other than a directive | consumer / graph, probes | `["pg:role/controller"]` |
| `depends_on` | `ciu_stack.<svc>` | list[sibling svc] | intra-stack start order rendered into compose | consumer / compose | `["postgres"]` |
| `after` | `ciu_stack.<svc>` | list[LogicalService], optional | manual ordering escape with no contract semantics | consumer / graph | `["consul"]` |
| `probe_user` | `ciu_stack.<svc>` | str, optional | the DB superuser ciu's `pg:*` probe uses inside this provider | consumer / probes | `"postgres"` |
| `aliases` | `ciu_stack.<svc>` | list[DNS label] | extra names on the instance network (a consumer contract with older clients) | consumer / compose injection | `["pwmcp-mcp"]` |
| `host_network` | `ciu_stack.<svc>` | bool | `network_mode: host` daemons (no hostname/network injection) | consumer / compose injection | `true` |
| `health.interval` / `.timeout` / `.retries` / `.start_period` / `.gate_timeout` | `ciu_stack.<svc>.health` | durations / int | per-service healthcheck parameters (merged from `deploy.health`) and the wave-gate budget | consumer / templates, gate | `start_period = "240s"` |
| `endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.host_bind` / `.allow_from` / `.path` | `ciu_stack.<svc>.endpoints.<e>` | int / `tcp\|udp\|http\|https` / `instance\|host\|proxy` / int / IP / list[`network.<n>`\|`host.<h>`] / str | §4.1.5; names unique per stack; `instance` publication is derived per layout (bound to the network address a cross-host route uses), `host` = always | consumer / routes, ports injection, allow-list render | `sql = { port = 5432, allow_from = ["network.mesh"] }` |
| `hostdir.<purpose>` / `.path` / `.uid` / `.mode` / `.seed` | `ciu_stack.<svc>.hostdir.<purpose>` | str (`""` = auto) / path / int / str / path | host directories, unchanged | consumer / engine | `data = ""` |
| `configfile.<n>.template` / `.target` / `.mode` / `.schema` | `ciu_stack.<svc>.configfile.<n>` | path / abs path / str (`0440`) / path | rendered config mounts (to `ciu.rendered.<svc>.<n>`) | consumer / compose injection | `template = "config.toml.j2"` |
| `secrets.<k>.directive` / `.delivery` / `.env_name` / `.mode` / `.uid` / `.consumed_by` / `.produced_by` / `.enabled` | `ciu_stack.<svc>.secrets.<k>` and `ciu_stack.secrets.<k>` (stack-level, shared) | str / `file\|env\|configfile\|native\|none` (**required**) / env name (required for `env`) / str / int / `hook` / profile / bool or flag | §4.1.8; `delivery` mandatory | consumer / secrets, compose | see §4.1.8 |
| `env.<VAR>`, `<consumer sub-tables>` | `ciu_stack.<svc>.<x>` | table | free-form service data (settings, feature flags); never read by ciu; may not be named `identity`, `health`, `endpoints`, `hostdir`, `configfile`, `secrets` | consumer / templates | `[ciu_stack.controller.workflow]` |
| `pre_secrets` / `pre_compose` / `post_compose` | `hooks` (stack-level reserved table) | list[path] ×3 | lifecycle hooks (subprocesses with a JSON context; `--validate` for check) | consumer / hooks_runner | `post_compose = ["./post_compose_db.py"]` |
| `provides.<svc>` | `hooks.provides` | list[TypedFact] | facts the hooks create, keyed by the service in which they are probed | consumer / graph, probes | `minio = ["minio:user/worker-io"]` |
| `*` | `governance` (stack-level) | as A6 | per-stack override of the global base (shallow merge) | consumer / governance | `memory_max = "4G"` |
| *(state)* | `<location>/ciu.state.toml` (not the stack file) | any (non-secret) | hook-persisted state preserved across renders and `clean`; secret-shaped keys refused; a `[state]` table in a stack file is refused | ciu from hook outputs / hooks, templates as `state.*` | `initialized = true` |

### F. Secrets file (`ciu.secrets.toml`, gitignored, CIU-owned, atomic) (V8-S10.6)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `value` / `source` / `created` | `secrets.<realization>.<svc>.<key>` (service-level), `secrets.<realization>.<key>` (stack-level), `secrets.hosts.<h>.<entry>` (host-scoped) | str / directive / datetime | the materialized secret in one store; which directive produced it; audit + rotation bookkeeping | ciu / ciu, humans | `source = "GEN_TO_VAULT:db/postgres/admin"` |
| `secrets.<vault realization>.<primary>.root_token` / `.unseal_keys` | same shape | str / list | Vault bootstrap state moved out of `[state]`; keyed by the RESOLVED vault realization, never a hardcoded name | vault hook via the hook API / hook | — |

### G. assay lane TOML (`assay.toml`, schema v2 — consumed by the gate; owned by assay)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `schema_version` | top | int = 2 | refuses a lane file the judge doesn't understand | consumer / assay | `2` |
| `lanes.<n>.scope` / `.rigor` / `.enforcement` | `lanes.<n>` | `S0..S4` / list `R0..R3` / `gate\|advisory` | WHAT is judged, how hard, and whether it blocks (assay's WHAT) | consumer / assay | `"S1"` / `["R0","R1"]` / `"gate"` |
| `lanes.<n>.argv` / `.env` / `.env_passthrough` / `.env_required` | `lanes.<n>` | list / table / list / list ⊆ passthrough | the HOW of the subject command; `env_required` = passthrough precondition | consumer / assay | — |
| `lanes.<n>.budget` / `.allow_argv_append` | `lanes.<n>` | duration / bool (required) | lane wall budget; explicit stance on argv extension | consumer / assay | `"20m"` / `false` |
| `lanes.<n>.environment_command` | `lanes.<n>` | list[str], optional | preflight probe of the gate environment | consumer / assay | — |
| `lanes.<n>.infrastructure.<fact>` | `lanes.<n>.infrastructure` | `required-env:<VAR>` or `derived:<path>` | injects instance facts into an isolated lane; `derived:` paths target `ciu.instance.resolved.*` | consumer / assay reads rendered `ciu.global.toml` | `db_host = "derived:ciu.instance.resolved.routes.test_runner.main_db.sql.host"` |
| `lanes.<n>.where` | `lanes.<n>` | opaque table | reserved for WHERE; parsed, never interpreted — the gate owns WHERE | consumer / nobody | — |
| `lanes.<n>.isolation.snapshot_selection` / `.unsafe_symlink_omissions` | `lanes.<n>.isolation` | `repository\|…` / list | required for R1+; what is snapshotted | consumer / assay | `"repository"` |
| `lanes.<n>.judge.language` / `.source_roots` / `.fail_under` / `.allow_excluded` / `.mode` / `.targets` / `.require_branch` | `lanes.<n>.judge` | str / list / float / bool / `changed_lines\|whole_target` / list / bool | R1/R2 judging scope and thresholds | consumer / assay | — |
| `lanes.<n>.judge.base` / `.base_source` | `lanes.<n>.judge` | git rev / `declared\|request` | comparison base; `request` delegates to `ciu gate --base` — declaring both refused | consumer / assay | `base_source = "request"` |
| `lanes.<n>.judge.coverage.format` / `.artifact` | `lanes.<n>.judge.coverage` | str / path | R1 evidence shape | consumer / assay | — |
| `lanes.<n>.judge.mutation.jobs` / `.max_mutants` / `.operators` / `.budget_per_candidate` / `.equivalence_artifact` / `.kill_signal_artifact` / `.shard_index` / `.shard_count` | `lanes.<n>.judge.mutation` | int / int / list / duration / path / path / int / int | R2 execution bounds; `shard_*` are inert today — **drop candidate on assay's side** | consumer / assay | — |
| `lanes.<n>.judge.canary.mechanism` / `.target` | `lanes.<n>.judge.canary` | str / path | R3 | consumer / assay | — |
| `lanes.<n>.judge.attestation_dir` / `.evidence[].source` / `.evidence[].key` | `lanes.<n>.judge` | path / `attested` / str | adjudicated external evidence — ciu provenance can feed it | consumer / assay | — |

### H. run-gate lane TOML → v8 home (every key and knob accounted for; the file itself retires)

| run-gate key / knob | v8 home | note |
|---|---|---|
| `schema_version` | `deploy.revision` | one revision gate for the whole config |
| `environments.<n>.image` | `testing.environments.<e>.image` / `image_from` | unchanged / derived |
| `environments.<n>.cgroup_slice` | `testing.cgroup_slice` (default) | one slice for all lanes was the stated intent |
| `environments.<n>.mode` | `testing.environments.<e>.mode` | + `host` built-in |
| `environments.<n>.container_name` | derived from `exec_in` | R-14a derivation retired (P6) |
| `environments.<n>.forward_env` | same | unchanged; `required_env ⊆ forward_env` for container environments only |
| `lanes.<n>.kind/environment/argv/assay_lane/description/clean_tree/budget/required_env/artifacts` | `testing.lanes.<l>.*` | unchanged names; `budget` enforced |
| `lanes.<n>.assay_command` | derived invocation (`assay run <lane> --file assay.toml --require-judge-provenance --verdict-json <evidence_dir>/<lane>/verdict.json [--request-base]`) | ciu owns the judge contract; the image carries the judge |
| `lanes.<n>.pins.<tool>.version/.sha256` | `testing.judge.version` + verdict `judge_provenance` | one floor pin; digest recorded from the verdict (B018) |
| `lanes.<n>.memory` | `testing.lanes.<l>.resources.memory_max` | legacy key retired |
| `lanes.<n>.resources.memory/memory_swap/cpu_weight/io_weight/shared` | `resources.memory_max/memory_swap_max/cpu_weight/io_weight/shared` | cgroup vocabulary; `shared` names LogicalServices; lock file `ciu.gate.shared-<realization>.lock` |
| `RUN_GATE_EXTRA_MOUNTS` / `RUN_GATE_MOUNT_ALIAS` / `RUN_GATE_EVIDENCE_DIR` / `RUN_GATE_CGROUPFS_ROOT` | `testing.environments.<e>.extra_mounts` + `CIU_GATE_EXTRA_MOUNTS`; `CIU_GATE_MOUNT_ALIAS`; `testing.evidence_dir` + `CIU_GATE_EVIDENCE_DIR`; `CIU_GATE_CGROUPFS_ROOT` | knobs keep their meaning under the ciu prefix |
| central config / lanes (R-22), reserved lane names | none — no central lane config in v8 | each project declares its own `[testing]` |
| `--worktree`, `--allow-dirty`, `--check-env`, `doctor`, `validate-pointers`, `--list`, `--dry-run` | `ciu gate --worktree/--allow-dirty/--check-env/--list/--dry-run`, `ciu gate doctor`, `ciu check --gates` | CLI surface preserved |
| `.assay/verdict-<lane>.json` | `<evidence_dir>/<lane>/verdict.json` | no hidden directory (P10) |

### I. Environment variables ciu reads in v8

| variable | reason | reader |
|---|---|---|
| `CIU_EXIT_ON` | ambient fallback for `ciu.exit_on` (config wins) | check |
| `CIU_MAX_CONCURRENT_INSTANCES` | ambient budget override never written to a file | instance budget |
| `CIU_SECRET_<VAR>` | `ASK_EXTERNAL` non-interactive input | secrets |
| `VAULT_TOKEN` | Vault token source #1 | providers |
| `CGROUP_PARENT_DEV_BACKGROUND` | governance parent / gate slice when `cgroup_parent = ""` | governance, gate |
| `CIU_HOSTS_FILE`, `CIU_SSH_TRANSPORT`, `CIU_SSH_INSECURE_TOFU` | host inventory / SSH transport switches | hosts, push |
| `CIU_KSM`, `CIU_GOV_BASELINE_PATH`, `CIU_SKIP_DOOD_PREFLIGHT`, `CIU_SKIP_DEPENDENCY_CHECK` | operator switches (all prefixed) | governance, engine |
| `CIU_GATE_EXTRA_MOUNTS`, `CIU_GATE_MOUNT_ALIAS`, `CIU_GATE_EVIDENCE_DIR`, `CIU_GATE_CGROUPFS_ROOT` | run-gate's knobs under the ciu prefix | gate |
| `NO_COLOR`, `TERM`, `CIU_LOG_PREFIX_TIME_SHORT` | output | output |
| `HOSTNAME`, `REMOTE_CONTAINERS`, `WORKSPACE_DIR`, `GITHUB_ACTIONS`, `USER` | environment detection during `instance init` only — the result is written to `[ciu.host.generated]` and never re-read ambiently | instance init |

Retired as config inputs: `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `INSTANCE_ID`, `REPO_NAME`, `DOCKER_NETWORK_INTERNAL`, `CONTAINER_UID/GID`, `DOCKER_UID/GID`, `USER_*`, `PUBLIC_*`, `ENV_TYPE`, `IS_*`, `HOST_MDT_TMP`, `PYTHON_EXECUTABLE`, `PIP_EXECUTABLE`, `CIU_SERVICES_PROFILE`, `CIU_HOST_PROFILE`, `CIU_GOV_READ_IOPS`, `RUN_GATE_*` (all become overlay facts, derived values, `ciu env print` outputs, or `CIU_GATE_*`).

### J. Keys retired in v8 (each with the reason; the full drop list is §4.8)

`deploy.environment_tag` (instance id is the identity), `deploy.network_name` (derived), `deploy.environment` (never read; layout owns it), `[deploy.phases.*]` (derived waves), `[deploy.groups]` (rejected today; profiles are bundles), `deploy.profiles.<p>.phases/.stacks/.topology_overrides` (derived / replaced by routes), `[topology.*]` (derived routes), `[service.<n>].type/.location` (registry split), `<root>.requires/.provides` at stack level (per-service `init_*`, `[hooks] provides`, derived directive facts), `<root>.<svc>.name` (derived), `<root>.stack_name` (never read), `<root>.<svc>.ports/.resources` (endpoints / governance), `ciu.repo_root/.physical_repo_root` (host facts), `ciu.workspace_env_file` (dead), `[deploy.resources]` (never read; governance owns caps), `deploy.db_service_name` (the hook reads the provider from its context), `vault.stack_path` (pointer by logical service), `expose_env` (delivery axis), `[ciu.instance.shared_infra.*]` incl. `ref_services` (joined realizations), `auto_generated.*` (host facts; `build_*` become `ciu.instance.resolved.build.*`), `deploy.health.timeout`'s second meaning (split), run-gate `pins`/`assay_command`/`memory`/`container_name`, `$VAR` references in TOML layers (literals or `instance.*`), `[ciu.worktree.exec_targets]` (gate environments), `<root>.<svc>.env_required` (secrets' `env_name` and lane `forward_env`), `[state]` in stack files (`ciu.state.toml`).
## 4.6 Spec/schema check

**For every proposed table: does it exist, and what changes?** (`S<n>` = v7 SPEC 5.0.0; `V8-S<n>` = SPEC-V8.)

| v8 table / key | exists today as | shape / meaning / owner change |
|---|---|---|
| `deploy.revision` | `revision` (S3, CONFIG.md) | value 8; unchanged owner |
| `[service.<n>] contract`, `<level> = { realized_by, service }`, `mock = {}` | S3.14 `[service.<n>] type/location/description` | **shape change**: `type`/`location` move to `[realization]`; contracts, variants and the per-variant `service` are new |
| `[realization.<n>] kind/location/provides/endpoints/instance/service` | S3.14 `location`; S16.1a `ref_services` (for `joined`); S13 stack-level `provides` | **shape change**: one namespace across kinds; `joined` replaces the generated `ref_services`; `provides` moves to services (`init_provides`), hooks (`[hooks] provides`), directives (derived) or external/joined realizations |
| `[ciu_stack.<svc>] …` | S3.3 `[<root>.<svc>]` with `requires/provides/name/instances/env_required/hostdir/configfile/secrets` | **root fixed and key set closed**; `requires/provides` → `init_requires/init_provides` per service with logical names; `name` derived; `env_required` → `required_env`; `endpoints`, `after`, `one_shot`, `primary`, `aliases`, `host_network`, `probe_user`, per-service `health.*` new; consumer scalars move to sub-tables |
| `[ciu_stack.secrets.<key>]` | S4 stack-level `[<root>.secrets]` | kept as the stack-level form; shared secrets mount by reference |
| `[ciu_stack.<svc>.endpoints.<e>]` | `internal_port` consumer fields; `[topology.services.<n>].internal_port` (S4.16 vault) | new table; replaces both; `ports:` injected from it |
| `secrets.<k>.delivery/env_name/enabled`, `HOST:<entry>` directive | S4.19 `expose_env` (env delivery), file default; `.ciu.hosts.toml` secrets unreachable from services | **meaning change**: explicit axis, mandatory, five values; host-scoped secrets consumable |
| `ciu.secrets.toml` `[secrets.*]` | S4.9 `.ciu/secrets/<name>` files; `[state]` Vault bootstrap (S9.4) | **owner unchanged (ciu)**, location and shape change; bootstrap keyed by the resolved Vault realization |
| `[network.<n>]` incl. `tls`/`pki`, derived `tls_*` secrets, `pki:issuer/<n>` fact | `[topology] transport` (proposal 1.10 only); tailscale hook peer gate (dstdns) | new |
| `[deploy.hosts.<h>.addresses]`, `local`, `ASK_FILE` host secrets | S14.3 hosts (no addresses) | additive keys; file renamed `ciu.hosts.toml` |
| `[deploy.profiles.<p>] services` | S7.4 `stacks`/`phases`/`compose_profiles`/`env_overrides`/`topology_overrides` | **meaning change**: bundle of logical services; `stacks`/`phases`/`topology_overrides` retired; `compose_profiles`/`env_overrides` kept |
| `[deploy.layouts.<l>.hosts.<h>] reach` | S7.5c layouts (`environment`, `bundles`) | additive `reach`; layout becomes mandatory |
| `[deploy.realness] default/pin`, `[ciu.instance.realness]` | — | new; the latter CIU-written |
| `[ciu.instance.resolved.*]` incl. `render_complete` | `[ciu.instance.generated]` (S3.1b) precedent; `ciu.deployed_stacks` (S3.12) | new derived tables in the **rendered** file (regenerated identically each render — the one case the rendered file is right for) |
| `[ciu.instance.generated]` / `[ciu.host.generated]` | S3.1b (six keys, one table) | split: instance identity vs host-local facts; becomes the identity source |
| `[ciu.instances] max_concurrent/lease_ttl_hours/exec_targets`, `ciu.instance.json` | S16.3/S16.9/S16.7 `[ciu.worktree]`, `ciu.worktree-instance.json` | rename; closed key set fixed (CIU-69) |
| `[governance]` resource key set | S15 `mem_limit/mem_swap_limit/mem_reservation/mem_min/read_iops/write_iops/io_weight/read_bps/write_bps` (undocumented in CONFIG.md) | **rename** to cgroup vocabulary shared with lanes; semantics unchanged |
| `[testing.*]` incl. `evidence_dir`, `environments.<e>.extra_mounts/workdir`, `host` built-in, `CIU_GATE_*` | run-gate `run-gate.toml` `[environments]/[lanes]` + `RUN_GATE_*` (RG SPEC) | **owner change** (ciu); `exec_in` replaces `container_name` derivation (R-14a); `resources` renamed; `pins`/`assay_command`/`memory` retired; `requires`, `judge`, `cgroup_slice` new |
| `[vault] service` | S4.16 `vault.stack_path` + basename heuristic (`deploy.py:389`) | replaces the heuristic |
| `deploy.health.gate_timeout`, `ciu_stack.<svc>.health.*` | `deploy.health.timeout` dual use (CIU-67); per-stack `[<root>.health]` consumer tables | new key; `timeout` keeps the probe meaning; per-service health tables become ciu-owned |
| `after`, `uses`, `per_host`, `[hooks.provides.<svc>]`, `stack_dir` render binding, two-pass stack render | — | new |
| `ciu.instance.generated.toml`, `ciu.state.toml` | `[ciu.instance.generated]` inside the overlay (S3.1b); `[state]` in the stack file (S3.4) | CIU-owned facts and hook state move to plain files ciu can rewrite whole without round-tripping a Jinja template |
| `ASK_HOST:<entry>` directive, `testing.lanes.<l>.request_base` | S14.3a host secrets (unreachable from services); run-gate read of `judge.base_source` | new directive; the gate stops reading assay.toml beyond lane names |
| `compose_stack` kind, `[ciu.instances.exec_targets]`, `required_env` on services, `ciu lock break` | S16.7 exec targets; S8.2a `env_required` | **not introduced / retired** (a compose file without a stack file wraps in a `ciu_stack`; exec uses gate environments; env is never a config input; `flock` needs no breaking) |

**How this schema itself is validated mechanically.** Three layers, each already present in ciu in some form:

1. **Closed-key validators per table** (`config_model.py` pattern: `_validate_worktree_table`, `validate_service_registry`, `validate_stack_provisioning`) — extended to every table in §4.5 and generated from one declarative table-spec so that adding a key is one row, not a new validator (P7; V8-S3.8.4). This is the mechanism the proposal *extends*, and it checks the proposal's own surface: stages 3–4 of §4.1.11 are exactly these validators plus referential integrity.
2. **S5.7 schema-validated configfile render** stays what it is — a JSON-schema check on a *rendered application config* — and is not stretched to validate ciu's TOML: ciu's tables have referential rules (a `realized_by` must resolve, a contract must be covered, a route must exist) that a JSON schema cannot express, so using S5.7 for them would give the false confidence §4.6 exists to avoid. The derived `[ciu.instance.resolved]` tables are validated by construction and cross-checked once (stage 8 uniqueness).
3. **S9.5 `validate_config`** is the consumer extension point, upgraded per CIU-65 to `list[Finding]` with `severity ∈ {WARN, ERROR}` and a `rule` id, mapped through `warn_policy.py`'s `ciu.exit_on` enum, and run inside the automatic `ciu check` before every mutating verb (CIU-64). Consumer semantics ciu cannot know (dstdns's Consul KV roots, two `landscape_id` spellings) are theirs to check here.

CIU-63's blindness disappears rather than being patched: there is no ref kind the static lint cannot judge, because ordering refs are logical names (graph-resolved), directive facts are derived, and fact probes are live by definition. What still needs a **live** check and cannot be static is listed honestly in stage 15 and in §4.10.

## 4.11 Non-breaking improvements to the existing tools (ship now, on the current schema)

| # | mechanism | tool | why it is safe | what it improves / unblocks |
|---|---|---|---|---|
| N1 | `ciu check` runs automatically before `ciu up` (opt-out `--no-check`) — CIU-64 | ciu | pure additive: check is side-effect-free; opt-out preserves old behavior | every static error caught before a deploy (P5) |
| N2 | `validate_config` findings with severity (`Finding(severity, message)`; bare `str` still accepted as ERROR) — CIU-65 | ciu | backward-compatible return type | hooks can warn without blocking |
| N3 | `lint_graph` recognizes `stack:<path>:healthy\|completed` as self-satisfied when the path resolves to a declared stack — CIU-63 (b) | ciu | removes a false refusal only; no accepted config becomes refused | dstdns drops four redundant self-`provides` |
| N4 | `deploy.health.gate_timeout` (default = old behavior) and gate default-on when any `stack:*` ref exists, plus bounded poll on `starting` — CIU-67/68 | ciu | new key optional; default-on only self-selects in configs that already need it | bare `ciu up` stops failing fresh deploys (D-212) |
| N5 | `WORKTREE_TABLE_KEYS` gains `exec_targets` — CIU-69 | ciu | widening only | S16.7 usable alongside a budget/lease |
| N6 | `pg:`/`minio:` probes resolve their container from the stack that `provides` the ref, with "container absent" distinguished from "role absent" — CIU-70 | ciu | consumers keyed `postgres`/`minio` see identical results | correct probes for any service key; honest failure reasons |
| N7 | `StrictUndefined` for all three render sites, with `ciu.instances` always present (possibly empty) so S7.5b's `'api' in ciu.instances` keeps working — CIU-74 | ciu | a template that renders today with an empty leaf is a latent bug, not a supported feature; ship behind a `ciu.strict_templates = true` opt-in for one release, default on the next | typos refuse instead of naming a container `dstdns--postgres` |
| N8 | `deploy.container_name` collision WARN: two stacks in one deploy set declaring the same service key — CIU-66 (static half) | ciu | WARN only | surfaces the collision class before v8's structural fix |
| N9 | Document the undocumented keys: `governance.mem_*`/`io_*`/`device`/`baseline_path`, `vault.token_file`, phase-entry `shipped`/`profiles`/`env_overrides`, profile `compose_profiles`/`env_overrides`, `deploy.registry.url`, `deploy.db_service_name` (hook template), `[ciu.worktree.exec_targets]`, `auto_generated.*`, env vars `CIU_KSM`, `CIU_GOV_*`, `CIU_SKIP_DOOD_PREFLIGHT`, `CIU_ADOPT_LEGACY_PROJECT`, `SKIP_DEPENDENCY_CHECK` | ciu CONFIG.md | docs only | closes 30+ undocumented rows found by the key inventory |
| N10 | `ciu.workspace_env_file` removed from `test-repo`; `[deploy.phases.phase_N] name/description/enabled` documented as **not read** (or phase-level `enabled` honored) | ciu | dead/unread keys | no config carries silent no-ops |
| N11 | `run-gate`: single assay pin — derive the `.pyz` filename and sha256 path from `pins.assay.version` (D-211 drift class) | run-gate | additive: explicit `assay_command` still wins | 30 spellings in dstdns collapse to 10 |
| N12 | `run-gate`: `--base REF` passed through to `assay run --request-base` for lanes that declare `judge.base_source = "request"` (filed 2026-08-30 as **RG-26** — RG-24 is the exec-mode container-resolution bug; the delegating lanes are DERIVED via `assay lanes --json` (assay B044, RG-25), never a run-gate key) | run-gate | new flag | B019 usable before v8 |
| N13 | `run-gate`: `environments.<n>.image_from_ciu = "<app_identity path>"` or a documented rule to derive the image from `ciu.global.toml` | run-gate | optional key | `test-runner` image spelled once instead of three times |
| N14 | assay: merge and release the v8 wave (`judge_provenance`, `--request-base`, r2 `mode/targets`); repin dstdns; document `env_required`, `environment_command`, `judge.canary.target`, `judge.attestation_dir`, `judge.evidence[]` in the README key list | assay | the wave is complete with a real gate transcript; docs additive | ciu's gate needs nothing else from assay |
| N15 | ciu: `vault.stack_path` honored before the basename heuristic and the heuristic emits a WARN naming the matched dir | ciu | behavior-preserving | makes the heuristic visible (P3) |
| N16 | ciu: a `--json` view of the merged config (`ciu render --json`) | ciu | additive | lets run-gate/assay/cockpit scripts read facts without re-parsing templates |
| N17 | Consumer-side (dstdns) cleanups this inventory found, actionable now: delete the unread `[deploy.resources]`, `deploy.environment`, phase-level `enabled`, `ciu.workspace_env_file`; reconcile `deploy.landscape_id` vs `registry.consul.deploy.landscape_id`; declare vault's port once; create `pg:role/workerdb_ddl` or drop it from `provides`; claim or stop minting `consul/cmru/controller/token` | dstdns | config hygiene | removes four silent no-ops, one two-valued fact and two contract/data mismatches |
---

# Part 2 — Design Rationale & Audit Trail

Everything from here on is the record of *how* Part 1 was reached: the inventory of every mechanism considered, the reasoning walked through step by step (including what the live interview decided and why), every contradiction and its resolution, what was dropped, what remains open, and where this proposal knows it is thin. Part 2 narrates correction history where that helps a reader reconstruct a judgment; Part 1 deliberately does not.

## 4.2 Inventory — every mechanism and idea, tagged

Tags: **SHIPPED** (exists in source/SPEC), **PROPOSED** (exists only in a proposal document), **CONTRADICTED** (two sources disagree — resolved in §4.7), **SUPERSEDED** (a later shipped mechanism replaced it), **QUESTIONABLE** (shipped, but violates a principle — a drop/change candidate). Configuration keys were inventoried separately, 349 rows across seven surfaces (ciu stack TOML 40, ciu global + `ciu.env` 130, secrets 24, assay lanes 46, run-gate lanes 28, cross-tool duplicated facts 30, environment variables 51); their dispositions are the §4.5 tables and the §4.8 drop list, and the raw findings that mattered are folded into the rows below.

### A. Identity and addressing

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| A1 | `container_name() = {project}-{env_tag}-{service}` | `deploy.py:162`, S7.7/S7.8 | SHIPPED, QUESTIONABLE | no stack component (CIU-66); one of four independent derivations → the single derivation of §4.1.4 |
| A2 | template-authored `container_name:`/`hostname:` = `{{ deploy.project_name }}-{{ deploy.environment_tag }}-<n>` | dstdns compose templates (70/68 refs) | SHIPPED, QUESTIONABLE | hand-repeated per service; must agree with A1 by convention → derived, and enforced equal (stage 8) |
| A3 | `[topology.services.<n>].internal_host = "$REPO_NAME-$INSTANCE_ID-<svc>"` | dstdns global; CONFIG.md S4.16/S7.4 | SHIPPED, QUESTIONABLE | third derivation, consumer-typed; ciu reads only vault's → derived routes |
| A4 | run-gate `resolve_container_name = {project}-{tag}-{env_name}` | `run-gate.py:1319` (R-14a) | SHIPPED, QUESTIONABLE | fourth derivation; the environment NAME stands in for a service → `exec_in` + identity table |
| A5 | compose project `{project}-{env_tag}-{stack}` / `{REPO_NAME}-{INSTANCE_ID}-{stack}` | S8.7, CIU-46 | SHIPPED | two shapes from one tuple → one (`{project}-{instance}-{realization}`) |
| A6 | `qname()` Jinja global | CIU-51 | PROPOSED | rejected: not cat-able (X16) |
| A7 | four-part identity `<project>-<instance>-<stack>-<service>[-<replica>]` | CIU-66; 1.10 §1.15 | PROPOSED | adopted as THE derivation |
| A8 | `environment_tag` → instance id | CIU-50 | PROPOSED | adopted (v8-timed) |
| A9 | `[ciu.instance.generated]` in the overlay | S3.1b, CIU-60 | SHIPPED | kept and extended; becomes the identity source |
| A10 | `ciu.worktree-instance.json` (lifecycle, lease) | S16, S16.9 | SHIPPED | kept (renamed with the verb family) |
| A11 | `INSTANCE_ID` = physical-path hash | S2.7 | SHIPPED | kept; `--name` becomes a label |
| A12 | `landscape_id` | S3.11 | SHIPPED | kept |
| A13 | compose bare-key DNS alias hazard | Docker; CIU-51 | SHIPPED (hazard) | qualified compose keys |
| A14 | `stack:<path>:healthy\|completed` resolves the dir basename to a container | `provisioning.py:477-505`; S13.2 | SHIPPED, QUESTIONABLE | per SPEC ("another container") but misleading for multi-service stacks → `stack:` refs retired (X17) |
| A15 | `pg:`/`minio:` probes hardcode `postgres`/`minio` (+ `-U postgres`) | `provisioning.py:345-410` | SHIPPED, QUESTIONABLE | defect CIU-70 filed; v8 provider-resolved probes |
| A16 | Vault stack found by `vault.stack_path` OR dir basename starting with `vault` | `deploy.py:389` | SHIPPED, QUESTIONABLE | heuristic → `[vault] service` |

### B. Config model and layering

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| B1 | four-layer model (`ciu.env` / templates / worktree overlay / `.ciu` overlays) | CONFIG.md, S3 | SHIPPED | three TOML layers + rendered file; `ciu.env` and `.ciu/` retired |
| B2 | merge chain; lists replace | S3.1a/S3.3 | SHIPPED | kept |
| B3 | `[state]` preserved; Vault bootstrap in `[state]` | S3.4, S9.4 | SHIPPED | `[state]` kept for non-secrets; bootstrap → `ciu.secrets.toml` |
| B4 | one non-reserved root table per stack; `local_stack` preferred | S3.3/S3.5/S3.7, PREP-4 | SHIPPED (partial) | fixed root `[ciu_stack.<svc>]` (F18) |
| B5 | `[service.<n>] type/location/description` registry + WARN lint | S3.14/S3.15 | SHIPPED, SUPERSEDED | split into `[service]` (WHAT) and `[realization]` (HOW) |
| B6 | `ciu.user_tables` + reserved global tables | S3.13 | SHIPPED | kept; v8 default empty |
| B7 | `[app_identity.<cat>.<proj>.<svc>] name/image_name/image_tag/internal_port` (~45 tables) + 41 inlined copies | dstdns D-210 | SHIPPED (consumer) | the duplication hotspot; replaced by the merged `realization.*` view |
| B8 | `[vault.paths]` (40 entries) | CONFIG.md | SHIPPED | consumer user table; ciu never reads it; kept as such |
| B9 | `[registry.*]` free-form + two ciu-read keys | S13.4b | SHIPPED | kept |
| B10 | `[ciu.ports]`, `ciu.repo_root`/`physical_repo_root`, `[deploy.resources]` | dstdns | SHIPPED (consumer, unread) | dropped |
| B11 | `revision = 8` gate | 1.10 §2.1 | PROPOSED | adopted |
| B12 | StrictUndefined | 1.10 §2.5, QOL-10 | PROPOSED | adopted; defect CIU-74 filed for v7 |
| B13 | `[build.python]` + `ciu refresh` | 1.10 §1.13, PREP-2 | PROPOSED, QUESTIONABLE | out of scope for this proposal (separate track) |
| B14 | `instances = N` fan-out | S7.5d/e | SHIPPED | kept; identity per replica derived |
| B15 | ciu `env_required` (unwired) | S8.2a | SHIPPED (unwired) | wired, renamed `required_env` (X19) |
| B16 | `${VAR:-fallback}` compose interpolation | dstdns | SHIPPED (hazard) | withdrawn (QOL-10) |
| B17 | Jinja `{% set %}` DRY maps in global defaults | dstdns | SHIPPED | fine — data, not logic (§4.3.3) |
| B18 | Jinja rendered with bare `Template()`: no loader, no filters, default `Undefined`, raw `os.environ` as `env` | `config_model.py:386`; S3.2 | SHIPPED, QUESTIONABLE | strict; `env` context dropped |

### C. Provisioning graph and ordering

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| C1 | `[deploy.phases.phase_N]` hand order | S7.1 | SHIPPED | dropped; waves derived (F6) |
| C2 | `requires`/`provides` typed refs + `lint_graph` + per-phase live probe | S13 | SHIPPED | fact grammar kept; refs become logical names; lint becomes graph resolution |
| C3 | `stack:*:completed` + `one_shot` (declaration only) | PREP-5 | SHIPPED (partial) | `one_shot` drives `completed` gates |
| C4 | per-phase JIT probe, zero retry | S13.3, CIU-68 | SHIPPED (defect) | bounded poll |
| C5 | health gate off by default; `deploy.health.timeout` dual use | CIU-67/68 | SHIPPED (defect) | gate implied by edges; `gate_timeout` |
| C6 | `produced_by` cross-profile producer | S13.6 | SHIPPED | kept on secrets (a bundle may own a producer) |
| C7 | Vault ordering preflight via stack-name heuristic | `deploy.py` vault preflight | SHIPPED, QUESTIONABLE | derived edge directive → Vault realization |
| C8 | contract-conformance check | V8-RG "still open" | PROPOSED | required (stage 5) |
| C9 | V8-RG: "`pg:schema/*` doesn't exist" | V8-RG vs S13.2 | CONTRADICTED | X14 |
| C10 | dstdns `test_deploy_phase_ordering.py` stand-in | D-210 | SHIPPED (consumer) | retires with phases |
| C11 | compose `depends_on` intra-stack | V8-RG | SHIPPED | kept; rendered from `depends_on` |
| C12 | transport readiness as an init dependency | V8-RG open; dstdns tailscale peer gate | PROPOSED | `[network.<n>] realized_by` |
| C13 | phase-level `enabled` declared 9× in dstdns, read by nothing | dstdns; `phases.py:64` | SHIPPED (consumer, unread) | disappears with phases; N17 |

### D. Realness and logical services

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| D1 | LogicalService / RealnessVariant / Realization / RealizedService / typed fact | V8-RG, 1.10 §3.1 | PROPOSED | adopted, with the generic realization registry |
| D2 | levels internal {mock, live}; third-party {live, owned-seeded, simulated, mock} | 1.10 §1.4 | PROPOSED | adopted; `owned-seeded` = prepared realization (interview) |
| D3 | selection precedence | 1.10 §1.4a | PROPOSED | adopted (CLI > record > pin > default) |
| D4 | realness immutable per instance; durable record | 1.10 §10.13 | PROPOSED | adopted; record in the overlay |
| D5 | inline `simulated` image/stub block | 1.10 §3.1 | PROPOSED, QUESTIONABLE | dropped |
| D6 | external realization never orders startup | 1.10 §3.1 | PROPOSED | adopted |
| D7 | `requires_realness` + transitive warning | 1.10 §10.6 | PROPOSED | `[testing.lanes.<l>.requires]` |
| D8 | `NOT_RUN` closed vocabulary | 1.10 §10.7 | PROPOSED | kept in the gate |

### E. Deployment shape and topology

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| E1 | `[deploy.profiles.<n>] phases/stacks/compose_profiles/env_overrides/topology_overrides` | S7.4/S7.5 | SHIPPED | profiles = bundles of logical services; `phases/stacks/topology_overrides` retired |
| E2 | `[deploy.layouts.<n>]` environment + hosts.<h>.bundles | S7.5c | SHIPPED | the placement entity; gains `reach`; mandatory |
| E3 | `[deploy.hosts.<h>]` inventory (`.ciu.hosts.toml`) | S14.3 | SHIPPED | Host entity; gains `addresses`, `local`; file renamed |
| E4 | `[topology] transport` + `[topology.hosts]` | 1.10 §1.17 | PROPOSED, QUESTIONABLE | dropped (X5) |
| E5 | `[topology.services/routes/external]` | SPEC-RECON 2c; dstdns | SHIPPED, QUESTIONABLE | derived routes replace them |
| E6 | `topology_overrides` per profile | S7.5a | SHIPPED | replaced by derivation |
| E7 | S16.1 shared-infra join + S16.1a `ref_services` | S16.1 | SHIPPED | generalized as `joined` realizations |
| E8 | SPEC J push (render-on-target) | S14.2 | SHIPPED | kept; static order check + live reachability probe added |
| E9 | tailscale-node stack + hook peer gate | dstdns | SHIPPED (consumer) | the `realized_by` of the mesh network |
| E10 | `[deploy.profiles.<n>.locks]`, `ciu lock/unlock` | 1.10 §4.4 | PROPOSED | instance mutex instead (§4.1.9); `ciu lock break` only |
| E11 | `ciu up --name` alias | 1.10 §4.4 | PROPOSED | a label in the overlay |
| E12 | worktree budget/lease | S16.3/S16.9 | SHIPPED | kept under `[ciu.instances]` |

### F. Secrets

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| F1 | six directives, stores, S4.26 lock | S4 | SHIPPED | directives kept; stores → one file; lock → directory fd |
| F2 | `expose_env` dominant in dstdns | S4.19; dstdns | SHIPPED, QUESTIONABLE | `delivery` axis, mandatory |
| F3 | `ciu.secrets.toml`, temp copies, delivery file/env/fixed/native | 1.10 §10.3 | PROPOSED | adopted; overlay YAML folded into the rendered compose |
| F4 | Vault bootstrap out of `[state]` | 1.10 §10.3 | PROPOSED | adopted |
| F5 | AppRole secret_id as an ordinary secret (SM2) | 1.10 §10.3, D-098 | PROPOSED / SHIPPED (consumer) | adopted (`native` for the app-fetched rest) |
| F6 | host-scoped secrets | S14.3a | SHIPPED | kept |
| F7 | Vault-presence static rule | 1.10 §2.7 | PROPOSED | adopted (stage 9) |
| F8 | rotation out of scope | S4.12, D-098 | SHIPPED (stance) | confirmed; schema does not obstruct |

### G. Locking and concurrency

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| G1 | S4.26 secret locks; S16 allocation and budget locks | SPEC | SHIPPED | kept (targets moved off `.ciu/`) |
| G2 | instance run mutex on `ciu.global.defaults.toml.j2` | 1.10 §10.11 | PROPOSED, CONTRADICTED | X24 → rendered file, in place |
| G3 | acquisition order | 1.10 §10.12 | PROPOSED | adopted (§4.1.9) |
| G4 | RG-20 admission (cgroupfs `memory.max` + per-name flock) | run-gate R-29 | SHIPPED | lifted into `ciu gate` |
| G5 | dstdns `flock /tmp/dstdns-testrunner.lock` | GUIDE §1 | SHIPPED (consumer) | retired by G4 |

### H. Validation

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| H1 | `ciu check` 13 stages, side-effect-free, `--json` | S13.4a | SHIPPED | 15 stages (§4.1.11); automatic |
| H2 | `validate_config -> list[str]` | S9.5 | SHIPPED | severity (CIU-65) |
| H3 | check not auto before up | CIU-64 | SHIPPED (gap) | auto |
| H4 | registry validator | S13.4b | SHIPPED | kept |
| H5 | S5.7 configfile schema | S5.7 | SHIPPED | kept; not stretched to ciu's own tables (§4.6) |
| H6 | topology completeness rule | 1.10 §1.17 | PROPOSED | replaced by stage 7 |
| H7 | cross-tool reference validation | prompt §4.6; run-gate validate-pointers | PROPOSED (new) | stage 12 |
| H8 | `WORKTREE_TABLE_KEYS` refuses `exec_targets` | `worktree.py:4009` vs S16.7 | SHIPPED (defect) | CIU-69 filed; fixed in v8's key set |
| H9 | leaf-typo templates render empty | `config_model.py:386` vs S3.12 | SHIPPED (defect) | CIU-74 filed; v8 strict |

### I. Testing gate

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| I1 | `ciu gate`/`ciu test` absorbing run-gate | 1.10 §5, §6 | PROPOSED, CONTRADICTED | X1 → absorbed (F1) |
| I2 | intents → rigor sets | 1.10 §1.3 | PROPOSED | policy a consumer writes as lane selections; not schema |
| I3 | changed-file → lane selection tables | 1.10 §5.4, §11.4 | PROPOSED | impact selection is the caller's (assay §7); not in v8 |
| I4 | `ciu exec` | 1.10 §5.7a | PROPOSED, SUPERSEDED | S16.7 |
| I5 | `[testing.resources]` | 1.10 §5.7b | PROPOSED, SUPERSEDED | RG-20 + cgroup vocabulary |
| I6 | ExecutionManifest | 1.10 §10.4 | PROPOSED | becomes the resolved table (`facts_schema`) |
| I7 | judge download/cache | 1.10 §10.5 | PROPOSED, CONTRADICTED | X9 → image-baked |
| I8 | `requires_realness` / `NOT_RUN` | 1.10 §10.6/10.7 | PROPOSED | lane `requires` |
| I9 | authored vs derived lists | 1.10 §10.8 | PROPOSED | adopted as the A/C split of §4.5 |
| I10 | GateRequest/ExecutionManifest/LaneResult/GateReport | 1.10 §10.9 | PROPOSED | LaneResult = `ciu.gate.<lane>.json`; GateReport = consumer/nyxloom |
| I11 | base from request | 1.10 §10.10, B019 | PROPOSED | shipped by assay (A-328); `ciu gate --base` |
| I12 | template DBs | 1.10 §10.2, B020 | PROPOSED | ciu prepare Realization; B020 hook stays assay's |
| I13 | provenance as adjudicated evidence | 1.10 §11.1, B004 | PROPOSED | gap #12 |
| I14 | RigorProvider protocol | 1.10 §11.2 | PROPOSED, QUESTIONABLE | dropped (X11) |
| I15 | lane timing persistence | CIU-55 | PROPOSED | the gate records timing in each LaneResult |
| I16 | assay `[lanes.<n>.where]` reserved | assay DG §12 | SHIPPED (reserved) | stays reserved; WHERE is the gate's |
| I17 | assay `[infrastructure] derived:` from `ciu.global.toml` | B013 | SHIPPED | kept; paths repoint at the resolved table |
| I18 | run-gate exec name from `ciu.global.toml [deploy]` | R-14a | SHIPPED | replaced by `exec_in` + identity |
| I19 | version triple (version + filename + sha256) | D-211 | SHIPPED (hazard) | one floor pin + provenance |
| I20 | assay v8 wave: `judge_provenance`, `base_source=request`, r2 `mode/targets`, schema v8 | branch `feature/assay-b018-b019-b035-v8-synergy` (A-327..A-331) | SHIPPED (unmerged) | consumed as-is; merge is V8-17 |

## 4.3 Elongated reasoning — the integrated design, walked through

### 4.3.1 What the interview decided (five rounds, 2026-08-30)

The operator was asked every fork live. Recording the decisions first makes the rest of §4.3 readable as "why this answer", not "what was the answer".

| fork | asked as | decided | the operator's reasoning, in short |
|---|---|---|---|
| F1 gate owner | keep three tools + facts contract (recommended) / absorb run-gate / thin wrapper | **absorb** | "nobody but us would use a facts contract; absorbing lets us modify run-gate for full synergy; the trivial adopters should use ciu v8 for their gate calls; we're free to redesign `[testing]`; the rendered TOML holding the instance data is fine so we do things once" — accepted after the measurements in §4.3.2 |
| F2 identity | materialized table (recommended) / `qname()` / both | **materialized table**, with a correction: identity facts move fully into the overlay TOML, `ciu.env` becomes legacy output; "the main repo is also a worktree — just a special one" | full TOML; no ambient env |
| F3 topology | network entities + derived routes (recommended) / §1.17 enum / hand-declared | **network entities**, after seeing single-host and three-host samples; corrections: hosts declared once and reused by several layouts (confirmed "that's your layouts"), the model must cover "mTLS full public" (→ `tls`/`pki` on networks), and `owned-seeded` is a *prepared* test realization, not the live stack |
| F4 join | `joined` realization kind (recommended) / worktree-scoped | **`joined` kind**; confirmed that `ciu worktree add` writes this table |
| single-host layout | implicit `local` (recommended) / explicit always | **explicit always** — "easy checkable for ciu and shows usage for the user" |
| F18 stack root | fixed `[services.<svc>]` (recommended) / `[ciu_stack.<stack>.<svc>]` / bare tables | **`[ciu_stack.<svc>]`** — "the stack doesn't know/have a logical name; that comes from the global level; bare tables collide" |
| F6 phases | drop + compute (recommended) / optional override / keep | **drop + compute**, plus: write the computed waves to the rendered TOML "like the instance data, so results are visible and usable" |
| F5 secrets default | no default (recommended) / file / env | **no default — `delivery` mandatory** |
| F18b own-stack reference | `stack.<svc>` alias (recommended) / full path / file key = render key | **file key = render key** (`{{ ciu_stack.postgres }}`) — "would match what we just decided for the stack file" |
| F7 resources | S15 names (recommended) / RG-20 names / keep both | **cgroup keys** — "the main point is putting lane runs in a default cgroup for tests and applying a per-lane `memory_max` to limit interference/eviction" |
| F8 judge | image-baked + pin + provenance (recommended) / download / vendored | **image-baked**, with: building the image resolves the newest release satisfying a floor like `>=2.4` |
| F14 mutex | default-on / opt-in | first: "I thought only one ciu process per instance was allowed and we used a lock on `ciu.global.defaults.toml.j2`" → facts supplied (no such mutex exists; S4.26 is per-stack secret-phase only); then: "the rendered `ciu.global.toml` — ciu is the only one rendering it; if ciu grabs a lock on it no other ciu can start; alternative the overlay" → **rendered file, rendered in place** (inode constraint explained, §4.3.7) |
| F18c registry root | generic `[realization.<n>] kind=` (recommended) / kind-named + refuse collisions / kind-named + `[services]` root | **generic registry** |
| F11 endpoints | one `endpoints.<e>` shape on every kind (recommended) / external keeps `url` | **one shape** |
| verb names | `ciu gate` + `ciu instance` (recommended) / `ciu test` + `worktree` / `gate` + `worktree` | **`ciu gate` + `ciu instance`** |
| `.ciu/` removal | flat `ciu.rendered.*` files (recommended) / state dir outside the repo / keep `.ciu/` for renders | **flat files**; the operator introduced the constraint itself ("we want to get rid of the folder `.ciu/` — that's why the move to `ciu.secrets.toml`") and asked for a configfile example (given: S5 app config mounts, 8 in dstdns) |
| overlay name | rename to `ciu.global.instance.toml.j2` (recommended) / keep | **rename** |
| compose overlay files (mid-write question) | "why `<stack>/.ciu/ciu.compose.overlay.yml` / `ciu.compose.yml`? can't the instance TOML carry this, with secrets in the secrets file?" | answered: the overlays existed only to *add* stanzas to a consumer-owned compose file; v8 renders one compose file (stanzas included) and the instance TOML carries the data, not the YAML docker consumes (§4.1.8) |

Non-convergence: none. The two deployment-dependent choices (§4.3a) were not forks the operator declined; they are places where the right answer depends on the consumer's filesystem or write policy.

After the interview the operator asked for three more things, answered without further forks: a review of the written proposal (a fresh adversarial reviewer, §4.3.11), a full recreation of dstdns's configuration in v8 notation with a three-host deployment (`v8-dstdns-demo/`, whose README records every decision the converters had to take), and a ground-up specification (`SPEC-V8.md`, itself reviewed by a second fresh reviewer). The operator's standing instruction for that phase — "if you find any contradictions, gaps, unneeded complexity, fix and iterate; make your own decisions if any, note what you decided for later review" — is why §4.3.11 and the demo README carry explicit decision lists rather than further questions.

### 4.3.2 The gate layer: why absorbing won after "keep three tools" was recommended

The first recommendation (keep run-gate, add a versioned instance-facts contract) rested on three claims: run-gate has nine adopters, five of them non-ciu; merging removes only one identity duplicate while a facts contract removes all; and the estate's WHAT/HOW/WHERE doctrine placed WHERE in a separate tool. Measuring changed the picture. Of the nine adopters, **only dstdns declares any environment** (1 env, 18 lanes, 10 assay, 1 exec); every other adopter has zero environments and 1–5 plain command lanes; every adopter lives in the same devcontainer where ciu is installed; and `run-gate.py` is 1703 stdlib lines with 180 tests that lift into a `ciu/gate/` package nearly verbatim. So the "adopter population" argument was one real consumer plus eight trivial files, and the install-footprint argument did not apply. The facts table survives either way because assay's `derived:` already reads the rendered file — which answered the operator's "nobody but us" point honestly: assay is a consumer of it, but its *versioning* burden only exists across a tool boundary, and absorption removes that boundary.

What absorption buys that a contract cannot: `[testing]` expressed in the entity model — an exec environment names a *LogicalService* (`exec_in`) rather than deriving a container name from a naming convention; lane preconditions are the instance's realness record and the graph's health, in process; resource caps use the same cgroup vocabulary and code path as stack governance; the judge pin is one floor checked against the same provenance the verdict carries. What it costs: a new requirement that `ciu gate` works with zero stacks (cheap), one overlap release for run-gate, an explicit reversal of D-110/111 recorded in dstdns, and gate fixes shipping on ciu's cadence (cmru makes that cheap). The operator chose absorption with that ledger in front of them.

### 4.3.3 Identity, the stack-file root, and the Jinja verdict

**One derivation.** The inventory found identity formed in four places (A1–A4) plus dstdns's hand-mirrored `app_identity.*` tables (B7: ~45 tables and 41 inlined copies of `name`/`image_name`/`image_tag`/`internal_port`), and the cross-tool table found the vault hostname derived two different ways that "must agree". P6 says one function; the question was only how its outputs reach templates and other tools. CIU-51's `qname()` fails P3/P1 in a specific way: a function's output exists only inside the templates that call it, so run-gate and assay would re-implement it — which is today. CIU-60's doctrine ("no bespoke Jinja global; every value backed by a file an operator can `cat`") is that same argument stated for the identity facts. The DESIGN-GUIDE rejected the *rendered* file as the home for `generated` facts because it has no state preservation; derived identity is the one class of value that is re-derived identically on every render, so the rendered file is exactly right for it, while durable selections (realness, joins, label) stay in the overlay. The operator's correction closed the loop: if the overlay is the identity source, `ciu.env` stops being a source at all and becomes an export.

**Stack-file root.** The proposal's `[ciu_stack.<stack>.<svc>]` made a stack repeat its own name; the operator's principle — the stack doesn't know its logical or stack name, both come from the global level — is P1 applied to naming. `[ciu_stack.<svc>]` as a fixed root is collision-free *within a file* and kind-marking, but the merge chain puts stack tables into the same dict as the global config, and dstdns has a stack named `vault` with a service named `vault`; with a `[ciu_stack.<stack>]` registry the two would be one table. Re-rooting stack tables under the registry node fixes the merged view (`realization.db_core.postgres`); making the registry generic (`[realization.<n>] kind=…`) keeps `ciu_stack` free to mean "my services" in a stack's own render context, which is the file-key-equals-render-key property the operator asked for in F18b. A side effect is the disappearance of `app_identity.*`: the merged view *is* the cross-stack fact table, derived instead of typed.

**Is Jinja the right mechanism, used well?** Measured: ciu renders with a bare `jinja2.Template` — no loader, no filters, default `Undefined` (CIU-74), raw `os.environ` as `env`. dstdns's layering is TOML deep-merge, not Jinja inheritance (zero `{% extends %}`); its global defaults use `{% set %}` for DRY constants (41 refs — data, fine); its 19 compose templates carry 65 `if/for` constructs, most of them replica loops and identity assembly. Verdict: **Jinja is the right mechanism for the job it should have — expanding data into compose YAML and application config files — and it was being used as a crutch for two things ciu should own: identity assembly (now derived, P6) and replica fan-out (now `instances` with derived per-replica identities).** The remaining control flow in templates is conditional inclusion of optional services and loops over data lists, which is templating, not business logic (P9). The fixes are to harden the environment (strict undefined, no ambient `env`) and to give templates the data they were assembling by hand — not to add Jinja features (no includes/macros/filters are proposed) and not to replace Jinja.

### 4.3.4 From phases to a computed graph — and whether completeness is actually specified

D-210's failure (a correct `requires` graph in the wrong hand-declared phase) is the P1 violation in its purest form: ordering declared twice. The proposal already wanted computed waves; V8-RG's open item was whether the graph is *complete enough* to compute from, i.e. whether every real ordering constraint is an edge. The v8 edge set is: `init_requires` (by capability, resolved through the realness selection to a Realization and its providers), `depends_on` (intra-stack, rendered to compose), and three **derived** edge classes that were previously either hand-declared or invisible — secret directive → Vault realization (replacing both the `requires = ["stack:infra/vault:healthy"]` self-declarations and the basename heuristic), network readiness (`realized_by`, the tailscale case V8-RG could not express), and PKI for TLS networks. Contract conformance (stage 5) is the completeness check for *providers*: every fact a consumer may rely on is covered by some provider in the selected realization, or `ciu check` names the uncovered fact. Cycles are refused. The manual `after` escape exists for the one thing a graph cannot know — an ordering constraint with no fact behind it — and `ciu check` warns when it is redundant so it cannot silently become a second ordering system.

V8-RG's claim that `pg:schema/*` did not exist was wrong (S13.2 has had it since 4.2); its underlying point — "schema *applied by the init job*" is not the same fact as "schema exists" — is right, and in v8 it is expressed as the init job being a `one_shot` provider whose `completed` state gates consumers, with the fact itself in its `init_provides`.

The two health-gate defects (CIU-67 conflated timeouts, CIU-68 gate off by default with a one-shot probe) stop being configuration hazards: the gate is implied by edges, and the convergence budget is derived from the target's own healthcheck parameters.

### 4.3.5 Realness, immutability, and the join — one axis or two?

Shared-infra join and realness selection interact but are not the same axis: realness answers "which realization stands in for this capability"; a join answers "whose instance's realization". Making the join a Realization *kind* collapses the interaction: the joiner declares `[service.our_vault.live] realized_by = "primary_vault"` and `[realization.primary_vault] kind = "joined" instance = "primary" service = "our_vault"` — so it picks the level (its own variant) and the instance (the join target) explicitly, and the reference's actual level is verified at `ciu up` against the joiner's declaration (`accept_levels` widens it). Nothing is inherited implicitly. The mechanism generalizes for free to monorepo roots and non-git instances (the `instance` field takes a path), which the worktree-scoped design could not do. `ciu instance add --join` is one writer of the canonical table — the operator's F4 follow-up question ("would the config be written this way as a result of `ciu worktree` being called?") is answered yes, and the hand-written form is identical.

Immutability (§4.1.7) is recorded as data in the overlay rather than in `instance.json` because the gate and templates need to read it through the ordinary merge chain, and because it must survive `ciu clean` but not `--vanilla` — exactly `[ciu.instance.generated]`'s existing preservation rule.

### 4.3.6 Topology as a first-class concern — ERD walk against every scenario

The entities (§4.1.2) came before any key: *Host* (addresses per network), *Network* (kind, readiness, TLS), *Endpoint* (publication, allowed sources), *Layout* (placement + reach), *Route* (derived). Walking the ten scenarios against them:

1. **Vault consumers** — GEN_TO_VAULT minters, ASK_VAULT readers, hook-minted AppRoles: the directive → Vault edge is derived; the hook's outputs are the vault stack's `init_provides`; the consumer's `init_requires = ["our_vault"]` covers them by contract conformance; ciu's own Vault client reads `routes.our_vault.api` so it works cross-host too. *Works.*
2. **Remote deployment** — controller on app-c, DB on core-b: route over `mesh` with the derived readiness edge on `tailscale_node`; the endpoint must be `publish = "host"`; push order verified against the graph; each host renders its own routes. *Works; V8-RG's "not modeled" was correct and is now modeled.*
3. **Access/transport as one concept** — same instance / cross-stack / joined / cross-host / proxy: one `route()` function, five outcomes; the consumer declaration never changes. *Works.*
4. **Firewall-scoped proxy** — `allow_from` on the endpoint, resolved to addresses from the named networks/hosts and handed to the stack's template. *Works; ciu stays declarative.*
5. **Realness selector** — `--realness payment_api=owned-seeded` selects a prepared Realization; the deploy set is the closure, so "every service needs a profile slot" is false by construction. *Works.*
6. **Join × realness** — explicit level + explicit instance; verified, never inherited. *Works.*
7. **Service-name collision** — four-part identity; qualified compose keys; uniqueness asserted anyway. *Works.*
8. **Rotation non-obstruction** — `native` delivery for app-fetched secrets; env-delivered secrets listed as restart-bound. *Works; the schema places nothing in the way.*
9. **Cross-tool references** — in process after absorption; `assay_lane` names checked against `assay.toml` lane names (parsed, not re-validated); `derived:` paths validated against the resolved table. *Works.*
10. **Additions** — replica routes (compose DNS over one key; gap #1 for per-replica endpoints); single-stack `ciu up --dir` requires a registered Realization (breaking, deliberate); cross-root joins by path; health gate timing (D-212) closed by derivation. *Works, with gap #1 recorded.*

The mTLS-over-public case the operator raised fell out without a new entity: `tls`/`pki` are properties of the *Network*, so a layout whose hosts `reach = ["public"]` gets mTLS routes with certificate facts and a derived PKI edge. Hosts declared once and reused by `prod`/`staging`/`mtls-public` is what layouts are.

### 4.3.7 Locking and the end of `.ciu/`

No instance-wide mutex exists today (S4.26 serializes a stack's *secret phase*; S16 locks allocation and budget). Five interleavings motivated one (§4.1.9), the sharpest being `ciu up` recreating containers while a `ciu gate` exec lane runs inside them — a false FAIL with a misleading reason — and two concurrent first `ciu up` runs both passing the "no realness recorded yet" check. The lock-target discussion turned on one mechanical fact the proposal had missed: `flock` binds an inode, and both files the earlier text named get *replaced* (a tracked template by `git checkout`; the overlay by ciu's own temp-and-`os.replace` writer). The operator's preference for locking one of the globals is sound for the rendered file if — and only if — it is rendered in place, which is acceptable because it is regenerable; the overlay keeps atomic replacement because it holds the identity. The directory-fd alternative has no such constraint and is kept in §4.3a.

`.ciu/` removal came from the operator as a standing intent ("that's why the move to `ciu.secrets.toml`"). Everything under it was enumerated from SPEC (S1.6, S4.9, S4.17, S4.26, S5.2, KSM cache) and each got a flat, visible home or was folded into the one rendered compose file — which also answered the mid-write question about the overlay YAMLs.

### 4.3.8 The gate in detail: judge, resources, assay

The assay v8 wave (unmerged, `feature/assay-b018-b019-b035-v8-synergy`) shipped exactly what 1.10 §10.5/§10.10/§11.3 needed from assay: `judge_provenance` (absent-or-complete, wheel/zipapp identified, source trees refused — A-327), `judge.base_source = "request"` + `--request-base` with three loud mismatch refusals (A-328), and r2 judging scope at the v7→v8 cut (A-329). So ciu's gate computes no digest, passes two flags, and copies the verdict's provenance into the LaneResult. The judge pin is a floor (`>=2.4`) because the estate's version policy is floors with a reason, the image build resolves the newest satisfying release, and the exact identity is in the verdict — three spellings of one version become one floor and one recorded fact. B009 (image-baked) beats §10.5's download/cache because a second distribution channel next to the image is a second thing to drift.

Resources: three vocabularies (RG-20, S15, §5.7b) for one concept; the operator chose the cgroup-v2 names because the *purpose* is cgroup placement — a default slice for all lanes plus a per-lane `memory_max` — and the keys should say what they write. S15's undocumented keys (`mem_limit`…) are renamed in the same move.

Assay itself needs no further change for v8: `[lanes.<n>.where]` stays reserved (WHERE is the gate's), `derived:` paths repoint at the resolved table on the consumer side, B020's prepare/clone hook stays assay's narrow ask with the lifecycle owned by a ciu Realization. The one assay-side item this pass would raise is documentary (six loader keys absent from the README key list, N14).

### 4.3.9 Naming decisions taken without a fork

`required_env` (ciu adopts the gate's spelling; assay's identically named key means something else and is assay's — X19); `ciu.hosts.toml` (the `.ciu` prefix dies with the directory); `[ciu.instances]` (follows the verb rename); `image_from` (one fact for the tester image, which dstdns spells three ways today); `memory_low` for `mem_reservation` (the cgroup file it writes). Each is listed in §4.9 so it can be overturned cheaply.

### 4.3.10 Defects filed upstream during this pass

CIU-69 (`WORKTREE_TABLE_KEYS` vs S16.7 `exec_targets`, reproduced), CIU-70 (`pg:`/`minio:` probes hardcode `postgres`/`minio`, source-confirmed), CIU-74 (leaf-typo templates render empty against S3.12's fail-loud promise, reproduced: `dstdns--postgres`). The `stack:<path>` basename resolution (A14) was *not* filed: SPEC S13.2 documents the ref as "another container", so it is a design gap (resolved in v8 by retiring the ref kind), not a contract violation. Phase-level `enabled` was not filed: it is a consumer-invented key ciu never documented (N17).

### 4.3.11 The adversarial review round and what it changed (revision 2.0 → 2.1)

A fresh reviewer (no memory of the design session) read revision 2.0 against the v7 sources, run-gate's SPEC, assay's branch and dstdns's config and returned 32 findings; the demo conversion of 25 stacks by three fresh converters surfaced a further dozen. All but four were accepted; the four that were not are recorded with the reason. What changed, grouped by the structure it affected:

**Locking.** The single exclusive lock would have deadlocked dstdns's own conjunction lane (`ciu gate gate` spawning `ciu gate schema …` on the same instance) and reversed RG-20's headroom-based admission with a global serialization. Fix: three lock classes — mutating verbs exclusive, gate and readers shared — plus a `render_complete` marker as the last bytes of the rendered file (a torn render is now detectable by readers, which the earlier text had promised but not specified), an `fstat`/`stat` retry after acquisition, and the project secrets lock moved off `ciu.secrets.toml` (an atomically replaced file cannot be a lock — the same inode argument the design had already made against the overlay) onto the repo-root directory descriptor.

**Facts and secrets.** Revision 2.0 made `init_requires` the only consumer edge and told stacks to type every `vault:secret/*` fact into `init_provides` — a P1 violation (db-core mints 17 paths) and a P5 regression (an `ASK_VAULT` reader that forgot the coarse edge passed every static stage and failed live, exactly D-210's class, which v7's string matcher had caught). Fix: `GEN_TO_VAULT:<path>` derives the fact, `ASK_VAULT:<path>` derives an edge to the minter, "no minter" is a static ERROR, hook-minted facts get a home (`[hooks] provides`), and `delivery` gains `none` (minted for others) and `configfile` (the converters found `secret()` in dstdns's config-file templates, which the compose-only delivery model had no answer for). Stack-level shared secrets, which every dstdns stack uses, get their v8 form; `HOST:<entry>` lets a service consume a host-scoped secret; `fixed` is gone.

**Topology.** Rule 5 of the route derivation could never select a `proxy`-kind network (address-free) and handed the proxy its own FQDN; the mTLS scenario had TLS on the route but no mechanism producing certificates, and the sample layout placed `core-b` on a network it had no address on. Fix: proxy networks are address-free and selectable by `reach`; the proxy hop is host-published; `pki:issuer/<n>` facts and derived per-consumer `tls_*` secrets; `host_port` uniqueness and push-order consistency added to stage 7; `url` only for `http`/`https`/`udp`. The demo then proved the point once more: `prod3` fronts services on other hosts, so their endpoints had to become `publish = "proxy"` with a host port and an `allow_from` scoping the proxy host — the converters had left them instance-only.

**Identity and instances.** `[ciu.instance.generated]` mixed the instance id (identical on every host of a layout) with host-local facts (paths, uids, FQDN) that render-on-target cannot share; split into `[ciu.instance.generated]` and `[ciu.host.generated]`, with the overlay travelling in the push bundle and `instance init --host <h>` regenerating the host half. The elision of the service component when it equals the realization name came from writing the demo (`dstdns-98535c-controller` reads as a name; `…-controller-controller` does not). Multi-service stacks needed a **primary** service (gate predicate, exec target, probe container) and, because `db_core` backs three capabilities, a per-variant `service` key. `aliases` and `host_network` came from pwmcp and tailscale.

**Schema hygiene.** `compose_stack` was a kind with no services, endpoints or provides — dropped; `mock` got an explicit empty-variant form; the internal/third-party default split, `accept_levels` (which let the realness record lie), `require_provenance` (an opt-out from the one pin), `probe`, `generated.project` and `ciu graph` were removed as complexity that bought no scenario; consumer scalars on service tables are refused (dstdns had them everywhere) and ciu-owned sub-table names are reserved (a converter found `[worker_io.identity] geo_zone`). run-gate's remaining surface (`RUN_GATE_*` knobs, `--worktree`, `--allow-dirty`, `--check-env`, `doctor`, host lanes exempt from the `forward_env` rule) was mapped rather than silently dropped, and the assay invocation corrected (the lane is positional; verdicts land in a visible evidence directory).

**Round 2 — the specification review.** A second fresh reviewer read `SPEC-V8.md` against the demo and walked both layouts, the joined worktree, the replicated worker and the proxy; 45 findings, all but three accepted. The structural ones: `prod3` could not deploy because every core endpoint was instance-only and rule 5 demanded a host port — **publication is now derived from the layout** (an endpoint reached cross-host over a network is published on the provider host bound to that network's address; nothing is published on a single host; `publish = "host"` means always); the mesh node needed to run on every host — **`per_host` Realizations**; "healthy" was undefined and services without a healthcheck could never satisfy it — **defined once** (`healthy` status, or running when no healthcheck, or exit 0 when `one_shot`); `exec` lanes cannot take a `--cgroup-parent` — their caps are validated against the target container's governance; `render_complete` as a bare key was invalid TOML after sub-tables — it is the **last table** `[ciu.instance.resolved.render]`; CIU-owned tables inside a hand-edited Jinja overlay needed a round-trip-safe editor — they moved to a **plain generated file** merged after the overlay; the realness record travelled to production inside the overlay — it is **per layout** and other layouts' records are stripped on push; runtime-only routes (a tracing collector, a proxy's backends) were permanent WARNs under the demo's own `exit_on = "WARN"` — **`uses`** declares a route without an ordering edge and an undeclared template route is an ERROR; host-scoped secrets had no consumer — **`ASK_HOST:<entry>`**; `[state]` in a committed stack file had nowhere to persist — **`ciu.state.toml`**; hook-provided facts were probed in the wrong container — `[hooks.provides]` is **keyed by service**; two incompatible hook models — the **subprocess model** everywhere (`--validate`); the gate silently read `assay.toml`'s `base_source` — **`request_base = true`** on the CIU lane; stack files reading `routes` was circular — a **two-pass** stack render with a recording stub; underscores in DNS-facing names — **`_` → `-`** in derived identities; `ciu lock break` could break nothing (`flock` dies with its holder) — dropped, and readers open the file read-only; `ciu-data/` was committable and pushable — gitignored and excluded; `exec_targets` duplicated gate environments — `ciu instance exec --env`; network kinds carried no rule — collapsed to `address | proxy` with a free-text description; cross-host fact gating was contradictory — cross-host is reachability-only and the provider host's own gate is authoritative.

**Not accepted in round 2.** (1) A `SEED_TO_VAULT` directive for seeded realizations — a hook that writes the prepared credentials (`[hooks.provides]`) covers it with an existing mechanism. (2) `address_key` to let two networks share one address plane — a host simply lists the same address under both network names. (3) Dropping `deploy.control`/`enabled` for whole-stack inclusion — bundles express *which capabilities*, `enabled` expresses *optional parts of a stack* (cadvisor under an observability flag); both stay, and a Realization whose services are all disabled simply leaves the deploy set.

**Not accepted in round 1.** (1) Keeping `generated.public_fqdn` out of the overlay — it is a host-local fact and moved to `[ciu.host.generated]` instead of being deleted. (2) An `alias_of` key for shared secrets — stack-level declaration covers it with one mechanism. (3) Making `ciu check` detect an external unlink of the rendered file — it cannot; documented as the one known hole of the file-lock design, with the directory-fd alternative kept in §4.3a. (4) Warning on `init_requires` entries a derived edge already implies — a coarse edge documents intent and carries contract semantics; only redundant `after` warns.
## 4.7 Contradictions found and resolved

| # | contradiction (both sides, sources) | resolution | reasoning pointer | forced by source/SPEC or decided in the interview |
|---|---|---|---|---|
| X1 | Proposal 1.10 §5.1 "run-gate will be maintained as a project in parallel" vs §6 "run-gate.py absorbed into ciu/src/ciu/gate.py; run-gate.toml replaced" | Absorb (one implementation lifted with its tests; run-gate frozen; D-110/111 reversed on record) | §4.3.2 | interview (F1), after measuring that only dstdns uses environments |
| X2 | 1.10 §2.1/§7.1 `revision = 8` vs §8 sample `revision = 7` | 8 | — | source consistency |
| X3 | `[external.*] base_url` (1.10 §1.15, §8) vs `endpoint` (1.10 §3.1) | one shape for every kind: `endpoints.<e>.url` | §4.3.6 | interview (F11) |
| X4 | `[testing.realness_defaults]`/`[testing.realness_overrides]` (1.10 §1.4a) vs `[testing] default_realness` (1.10 §5.5, §8) | realness is a deploy concern fixed at `ciu up`: `[deploy.realness] default/pin/category` + instance record | §4.3.5 | forced by 1.10 §10.13 (immutability at up) |
| X5 | three topology shapes: `{{ topology.services.x.internal_host }}` kept (1.10 §1.15) vs `[topology] transport` + hosts (1.10 §1.17) vs SPEC-RECON 2c endpoints+routes split vs V8-RG "port lives on the Realization" | routes are derived from Layout × Networks × Endpoint; `topology.*` does not exist in v8 | §4.3.6 | interview (F3) — converged on the entity model |
| X6 | 1.10 §4.2 `[deploy.profiles.<n>.hosts.<h>] services` (profiles own placement) vs SHIPPED S7.5c `[deploy.layouts.<n>.hosts.<h>] bundles` | layouts are the placement entity; profiles are bundles | §4.3.6 | forced by source (S7.5c shipped; adversarial M6) |
| X7 | 1.10 §1.7/§4.1 `[deploy.groups]` vs SHIPPED S7.5 "`[deploy.groups]` is REJECTED by the v2 validator" | no `groups`; profiles = bundles | §4.3.6 | forced by source |
| X8 | 1.10 §5.7b `[testing.resources.defaults] memory/…` vs SHIPPED RG-20 `[lanes.<n>.resources] memory/memory_swap/cpu_weight/io_weight/shared` vs SHIPPED S15 `mem_limit/mem_swap_limit/…` | one cgroup-named vocabulary for lanes and stacks | §4.3.8 | interview (F7): "align to cgroup keys" |
| X9 | 1.10 §10.5 ciu downloads/caches the judge vs estate decision B009 (image-baked judge, vendored pyz retired) vs dstdns reality (vendored 2.4.2 pyz, 30 spellings) | image-baked wheel; one floor pin; provenance from the verdict | §4.3.8 | interview (F8) |
| X10 | 1.10 §11.3 "record judge sha256 in the verdict" vs assay schema v7 `unevaluatedProperties: false` | assay shipped `judge_provenance` in v8 (A-327); ciu copies it into the LaneResult, computes nothing | §4.3.8 | forced by source (assay wave) |
| X11 | 1.10 §11.2 RigorProvider protocol vs assay's LanguageAdapter + `claims[]` (shipped) | dropped; assay already is the provider contract | §4.8 | forced by source |
| X12 | 1.10 §10.2 assay `judge.mutation.database_template` vs assay non-goal "not an orchestrator; never a DSN" (DG §11, B020) | prepare/clone lifecycle is a ciu Realization concern; B020's narrow hook stays assay's | §4.3.8 | forced by source |
| X13 | 1.10 §5.7a `ciu exec` (new verb) vs SHIPPED S16.7 `ciu worktree exec --target` | superseded by the shipped verb (`ciu instance exec`) | §4.8 | forced by source |
| X14 | V8-RG "`pg:schema/*` ref kind doesn't exist" vs SPEC S13.2 / `provisioning.py` `_PG_RE = pg:(role\|db\|schema)` | the kind exists; what was missing is "schema *applied* by an init job", which is a `one_shot` provider's completion | §4.3.4 | forced by source |
| X15 | V8-RG "nothing models cross-host transport readiness" vs SPEC J shipped | V8-RG is right: SPEC J moves bundles, it does not model tunnel readiness; `[network.<n>] realized_by` models it | §4.3.6 | forced by source; shape from interview (F3) |
| X16 | CIU-51 `qname()` Jinja global vs CIU-60 doctrine (no bespoke Jinja global; every value backed by a file) | identity is data in the rendered file; no Jinja callables | §4.3.3 | interview (F2) |
| X17 | 1.10 §1.18 `requires = "stack:infra/db-init:completed"` (full path) vs `_stack_container_name` resolving the dir basename to a container (SPEC S13.2 "another container") | `stack:*` refs do not exist in v8; `init_requires` names LogicalServices | §4.3.4 | forced by source |
| X18 | 1.10 §10.14 join "worktree-scoped" vs "instance property" (undecided) | `joined` realization kind in the overlay; `ciu instance add` writes it | §4.3.5 | interview (F4) |
| X19 | ciu `env_required` (S8.2a compose env presence) vs assay `env_required` (passthrough precondition) vs run-gate `required_env` — one name, three tools, two spellings | ciu adopts `required_env` (matches the gate it now contains); assay's key is assay's | §4.3.9 | decided by this pass (naming; not a fork) |
| X20 | CIU-QOL-4 "`custom_http_port` moves to `[service.<name>.<level>].port`" vs entity model (port is a RealizedService fact) | ports live on endpoints of RealizedServices/external realizations, never on variants | §4.3.6 | ERD |
| X21 | dstdns `deploy.environment = "dev"` (not read by ciu) vs S7.5c `deploy.layouts.<n>.environment` | the layout carries environment; layouts mandatory | §4.3.6 | forced by source + interview (explicit layout) |
| X22 | dstdns `registry.consul.deploy.landscape_id = "dstdns-default"` vs `deploy.landscape_id = "dstdns-dev"` | consumer cleanup (N17); ciu validates only `deploy.landscape_id` | §4.11 | consumer-side |
| X23 | S16.3 `WORKTREE_TABLE_KEYS` closed set vs S16.7 `[ciu.worktree.exec_targets]` in the same table | defect, filed CIU-69; v8 key set includes all three | §4.11 | source (reproduced) |
| X24 | 1.10 §10.11 mutex on `ciu.global.defaults.toml.j2` vs `flock` inode semantics (git replaces tracked files; atomic renames replace the overlay) | lock on the rendered file, rendered in place | §4.3.7 | interview (F14) with the inode constraint explained |
| X25 | S3.12/S7.5b "a template referencing an absent fact fails loudly (UndefinedError)" vs `render_jinja2_text` using the default `Undefined` (leaf typos render empty) | defect, filed CIU-74; v8 `StrictUndefined` | §4.11 | source (reproduced) |
| X26 | 1.10 §8 stack file root `[ciu_stack.<stack>.<svc>]` (stack repeats its name) vs interview "the stack doesn't know its name" vs merged-namespace collision of `[ciu_stack.<svc>]` with a `[ciu_stack.<stack>]` registry (dstdns `vault`/`vault`) | stack file `[ciu_stack.<svc>]`; registry is the generic `[realization.<n>] kind=…`; merged view re-rooted | §4.3.3 | interview (F18, F18b, F18c) |
| X27 | rev 2.0 exclusive instance lock for `gate` vs dstdns's conjunction lane (`ciu gate gate` → nested `ciu gate …`, RG-25 shape) and RG-20's headroom admission | gate and readers take a shared lock; only mutating verbs exclusive | §4.3.11 | review (accepted) |
| X28 | rev 2.0 project secrets lock on `ciu.secrets.toml` vs the design's own inode argument (an atomically replaced file cannot be a lock) | lock on the repo-root directory descriptor; the store stays atomic | §4.3.11 | review |
| X29 | rev 2.0 "`init_requires` is the only consumer edge; facts are typed into `init_provides`" vs P1 (db-core's 17 minted paths) and P5 (an `ASK_VAULT` reader with a missing coarse edge fails live) | directive-derived facts and minter edges; "no minter" is static | §4.3.11 | review |
| X30 | rev 2.0 route rule 5 (both hosts need an address) vs `proxy` networks having none; the proxy handed its own FQDN | proxy networks address-free and selectable; the proxy hop host-published | §4.3.11 | review |
| X31 | rev 2.0 mTLS routes carrying certificate paths vs no fact, directive or delivery producing certificates | `pki:issuer/<n>` + derived per-consumer `tls_*` secrets | §4.3.11 | review |
| X32 | rev 2.0 `[ciu.instance.generated]` mixing the shared instance id with host-local paths/uids vs render-on-target | split into instance and host tables; overlay travels with the bundle | §4.3.11 | review |
| X33 | converters: `publish = "instance"` on the endpoints the prod3 proxy fronts from another host vs rule 5 | `publish = "proxy"` + `host_port` + `allow_from` on the fronted endpoints | §4.3.11 | demo |
| X34 | converters: `secret()` in dstdns's config-file templates vs a compose-only delivery model | `delivery = "configfile"`; the rendered config file is a declared secret-bearing artifact | §4.3.11 | demo |
| X35 | spec draft.1 route rule 5 (`port = host_port`, cross-host to an instance-only endpoint is an ERROR) vs the demo's prod3 (every core endpoint instance-only) | publication derived from the layout; `publish = "host"` = always | §4.3.11 | spec review |
| X36 | spec draft.1 "one placement per Realization" vs the mesh node in every host's bundles and network edges "on both ends" | `per_host` Realizations | §4.3.11 | spec review |
| X37 | spec draft.1 `render_complete = true` "as the last bytes" vs TOML (a bare key after sub-tables belongs to the last sub-table) | `[ciu.instance.resolved.render] complete = true` as the last table | §4.3.11 | spec review |
| X38 | spec draft.1 CIU-owned tables "rewritten whole" inside a hand-edited Jinja overlay vs no round-trip-safe TOML+Jinja editor | CIU-owned facts in `ciu.instance.generated.toml`, merged after the overlay | §4.3.11 | spec review |
| X39 | spec draft.1 hooks as executables with JSON stdin/stdout vs `validate_config` as an imported Python function | subprocess model with `--validate` | §4.3.11 | spec review |
| X40 | spec draft.1 `exec` lanes with `--cgroup-parent` vs `docker exec` joining the container's own cgroup | caps validated against the container's governance; differing caps need `ephemeral` | §4.3.11 | spec review |

## 4.8 What to drop

| idea | source | why |
|---|---|---|
| Absorbing run-gate *and* keeping run-gate.toml via a shim | interview option | two schemas for one implementation; the trivial adopters migrate in minutes |
| `[topology] transport = direct\|wireguard\|proxy` global enum + `[topology.hosts]` | 1.10 §1.17/§4.2/§8 | one enum cannot express per-link transport or readiness; a second host table duplicates `[deploy.hosts]` |
| `[topology.services.*].internal_host` hand-declared routes; `topology_overrides` | S4.16/S7.4/S7.5a; dstdns | third re-implementation of identity; replaced by derived routes |
| `[deploy.phases.*]` and phase-level `name/description/enabled` | S7.1; dstdns | ordering declared twice (D-210's bug class); phase-level `enabled` was never read |
| `[deploy.groups]`; profiles-with-hosts | 1.10 §1.7/§4.1/§4.2 | rejected by the shipped validator; layouts already are placement |
| `[testing.resources.defaults]`, rigor presets | 1.10 §5.7b | RG-20 shipped the mechanism; presets are policy a consumer writes as numbers |
| `ciu exec` verb | 1.10 §5.7a | shipped as `ciu worktree exec --target` (S16.7) with declared targets only |
| Judge download/cache (`[testing.judge] source="github-releases"`, `cache_dir`) | 1.10 §10.5 | B009 bakes the judge into the image; provenance identifies it |
| Version triple pin (version + `.pyz` filename + sha256 file) | run-gate SPEC / dstdns | 30 spellings of one fact (D-211); floor + provenance replaces it |
| `assay_command` per lane | run-gate SPEC | ciu owns the judge contract and derives the invocation |
| RigorProvider protocol | 1.10 §11.2 | assay's adapters and `claims[]` already are that contract |
| `judge.mutation.database_template` in assay | 1.10 §10.2 | assay is not a provisioner (B020); ciu owns prepare/clone |
| Inline `simulated` realization (`image`, `stub_mappings` on the variant) | 1.10 §3.1 | a stub is a Realization like any other (P7) |
| `[service.<n>] type/location` registry (S3.14/S3.15) | SPEC | superseded by the WHAT/HOW split |
| `[ciu.instance.shared_infra.*]` incl. generated `ref_services` | S16.1/S16.1a | superseded by `joined` realizations declared in the overlay |
| `deploy.environment_tag` | S7.7/S8.7 | the instance id is the identity (CIU-50); a label covers the human need |
| `ciu.env` as a source; `deploy.env.shared` identity copies; `auto_generated.*`; `ciu.repo_root`/`physical_repo_root` | S2, S3.9, dstdns | four+ carriers of one fact; the overlay is the source |
| `.ciu/` directory, `.ciu.hosts.toml`, `~/.ciu/` | S1.6/S1.7/S4.9/S4.17/S4.26/S5.2 | hidden machine state (P10); flat visible files instead |
| `expose_env` as the implicit delivery path | S4.19 | replaced by the mandatory `delivery` axis |
| `vault.stack_path` + "dir basename starts with vault" | S4.16, `deploy.py:389` | heuristic; replaced by `[vault] service` |
| `stack:<name>:healthy\|completed` ref kind and its self-`provides` workaround | S13.2, CIU-63 | replaced by LogicalService names in `init_requires` |
| `<root>.<svc>.name`, `<root>.stack_name`, `image_name`/`image_tag` pairs, `app_identity.*` mirror | dstdns | derived identity / single `image` key |
| `qname()` Jinja global | CIU-51 | not cat-able; identity is data |
| `ciu.workspace_env_file`, `[deploy.resources]`, `deploy.registry.namespace`, `deploy.environment` | ciu test-repo / dstdns | never read |
| Jinja ambient `env` context (`{{ env.VAR }}`) | S3.2 | ambient trust the doctrine (CIU-60) already rejected for hooks |
| assay `judge.mutation.shard_index/shard_count` lane keys | assay config.py (inert, B026 note) | validated and echoed, read by nothing — assay-side drop candidate (noted, not filed: assay's call) |
| `[ciu.ports]`, `custom_http_port` on variants (CIU-QOL-4) | dstdns / backlog | ports are endpoints |
| `${VAR:-fallback}` compose interpolation | dstdns (QOL-10) | silent defaults for facts that exist elsewhere |
| B1 "`--name` alias in identities" | 1.10 §4.4 | a label, never an identity component (identities are derived from the path hash) |
| `compose_stack` realization kind | rev 2.0 | a kind with no services, endpoints, edges or provides; a compose file without a stack file wraps in a `ciu_stack` |
| `[deploy.realness] default = { internal, third_party }` + `category.*` | rev 2.0 | two key sets for one decision; one default level plus pins loses no scenario |
| `accept_levels` on joined variants | rev 2.0 | let the realness record record a level the reference does not run; the joiner declares the actual level |
| `delivery = "fixed"` | 1.10 §10.3 | a legacy constant in a breaking redesign; no scenario needs it |
| `testing.judge.require_provenance` | rev 2.0 | `false` made the one pin unverifiable (P2); provenance is always required |
| `realization.<n>.probe` (external) | rev 2.0 | duplicates the live cross-host reachability probe |
| `generated.project` "drift copy" | rev 2.0 | a second carrier of `deploy.project_name` |
| `ciu graph` verb | rev 2.0 | duplicates `ciu check --graph` |
| `$VAR` expansion in TOML layers | S3.2 | ambient environment as a config source (P10); literals and `instance.*` instead |
| Per-service `ports:` in compose templates | v7 templates | derived from endpoints and injected; a template's `ports:` could disagree with `publish` |
| `[ciu.instances.exec_targets]` | S16.7 | duplicates the gate's environments (`exec_in`); `ciu instance exec --env <e>` reuses them |
| `required_env` on services (v7 `env_required`) | S8.2a | the only way to make arbitrary process env a configuration input; secrets' `env_name` already guarantees presence and lanes forward env explicitly |
| `ciu lock break` | rev 2.1 | `flock` is released by the kernel when its holder dies; there is nothing to break |
| `[state]` in stack files | S3.4 | a committed template cannot persist; hook state lives in `ciu.state.toml` |
| Network kinds `lan\|mesh\|public` | rev 2.1 | no rule distinguished them; `address \| proxy` plus a free-text description |
| `consul:token/*`, `consul:kv/*` fact kinds | S13.2 | unused by any consumer (Consul tokens are Vault facts); grammar kept minimal |
| `testing.judge.require_provenance`, `realization.<n>.probe`, `generated.project` | rev 2.0 | see §4.3.11 round 1 |

## 4.9 Open product decisions

None. Every fork surfaced by the inventory, the contradictions and the scenario walks was put to the operator live (§4.3.1) and converged. The small naming decisions this pass took alone — `required_env` as ciu's spelling (X19), `ciu.hosts.toml` for the host inventory, `[ciu.instances]` for the renamed budget table, `image_from` on ephemeral environments, `memory_low` for the old `mem_reservation` — are listed here so they can be overturned cheaply; none changes the model.

## 4.10 Known gaps in this proposal

1. **Routes for multi-endpoint providers and replicas** are specified (`routes.<logical>.<endpoint>`; compose DNS over one qualified key) but not tracer-bulleted; a stateful replicated provider (a Postgres primary/replica pair) needs a per-replica endpoint naming rule this proposal has not written.
2. **`owned-seeded` for stateful stacks** is defined semantically (prepared data, facts by construction) but the image/volume preparation workflow (how the seeded state is built and versioned) is a consumer concern this proposal only names.
3. **Lock semantics on non-Linux and network filesystems.** `flock` on the in-place-rendered file is sound on Linux local/overlay filesystems (the only ones the estate runs); NFS/SMB behavior is untested. The directory-fd alternative is documented (§4.3a B).
4. **External unlink of the rendered file.** A crash mid-render is now detectable (`render_complete` marker, V8-S14.4.2), but a tool that deletes ignored files while an instance is up (`git clean -x`) silently forks the mutex; ciu cannot detect it. Documented in the gitignore rule; the directory-fd alternative has no such hole.
4a. **Certificate issuance for TLS networks** is delegated to the `pki` service's hook (V8-S10.5); the hook contract (what `pki/<network>/<consumer>/{cert,key,ca}` must contain, renewal) is not specified here.
4b. **The seeded realization's Vault facts** are provided by a hook that writes the image's baked credentials to Vault (the demo's `post_compose_seed_vault.py`); "by construction" covers `pg:`/`minio:` facts only. The hook is named, not written.
4c. **Cross-host wave synchronization is reachability-based.** A consumer host waits for a remote provider's endpoint to answer, not for the provider host's fact probes; a provider that accepts connections before its init job has finished could admit a consumer early. The provider host's own gate makes this unlikely for `one_shot`-gated providers (the endpoint belongs to the same wave) but it is not proven; an `http:` readiness fact on the provider is the mitigation a consumer can declare today.
4d. **Two-pass stack rendering** (a recording stub for `routes` in pass 1) is specified but its interaction with Jinja conditionals on route values (`{% if routes.x.y.host %}`) is only defined as "literal chains render as empty strings"; a template that branches on a route value in pass 1 may declare different endpoints in the two passes — `ciu check` must refuse a pass-1/pass-2 difference in the extracted keys (stated in V8-S3.5.5's spirit, to be tested).
5. **cgroup slices inside the devcontainer.** RG-20 measured cgroupfs writes for `memory.max`; `cpu.max`, `io.weight`, `pids.max` inheritance and the default `testing.cgroup_slice` creation from within a container need a live probe on the mdt host before V8-12 is carved.
6. **assay wave not merged.** §4.1.10 relies on `judge_provenance` and `--request-base` as shipped on the branch (A-327/A-328); if the reviewer changes their shape, the LaneResult envelope follows.
7. **Zero-stack mode** (`ciu gate` for projects with no Realizations) is specified by exclusion; the exact minimal config and the behavior of `ciu check` stages 4–10 on an empty graph need a fixture.
8. **SPEC J secrets on target.** The bundle carries `ciu.secrets.toml` only if it exists on the control host; a target-side `ASK_EXTERNAL` flow for secrets that must not leave the target is not designed.
9. **Migration effort** for dstdns (25 stacks, 19 compose templates, 41 inlined identity copies, 30 pin spellings) is enumerated, not sized; the first migrated stack should be a tracer bullet before the rest.
10. **Scenario coverage.** Ten scenarios plus four additions were walked (§4.3.6), and the demo exercised the model against 27 realizations and four layouts. Not walked: multi-instance deployments of *different* projects sharing one host's mesh (cross-project routes), Windows/macOS Docker Desktop path semantics for `physical_repo_root`, and a `compose_stack` realization that itself needs secrets (today only ciu stacks get the secrets pipeline).
11. **`allow_from` rendering** is declarative (resolved addresses handed to the stack's own template); no verification that the stack actually enforces it exists beyond the consumer's own tests.
12. **Provenance as adjudicated evidence** (1.10 §11.1, B004): ciu's image provenance (S17) can be emitted as an attested evidence artifact for assay lanes; the exact key/format contract is deferred to assay's B004 design.
