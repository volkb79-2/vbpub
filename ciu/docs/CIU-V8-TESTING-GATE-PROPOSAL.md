# CIU v8 Proposal — Integrated Configuration Model, Deployment Graph, and Native Testing Gate

**Status:** PROPOSAL — not yet normative (the normative companion is `SPEC-V8.md` 8.0.0-draft.4; the worked examples are `v8-dstdns-demo/`)
**Author:** dstdns/vbpub joint design sessions (2026-08-22 → 2026-09-03); revision 2.x produced by the wholistic-integration pass with the operator interviewed live on every fork, hardened by two review rounds (§4.3.11); revision 3.0 produced by a fresh adversarial review of the whole design set (`CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`, 78 findings) with the operator interviewed on ten forks (§4.3.1); revision 3.1 produced by an **independent third-party review** (`CIU-V8-THIRD-PARTY-REVIEW-2026-09-02.md`, 35 findings T-01..T-35, seven alternative designs; dispositions in `CIU-V8-THIRD-PARTY-REVIEW-RESPONSE-2026-09-03.md`) with the operator deciding two forks (§4.3.13)
**Supersedes:** every prior revision of this file (1.5 through 2.1); the `[deploy.phases]` hand-ordered deployment model; the `[service.<n>] type/location` registry; the `[topology.*]` hand-declared routing tables; the `.ciu/` machine-owned directory convention; `ciu.env` as a configuration source; Jinja-templated declaration files; the `routes` render binding and the `init_requires`/`uses`/`after` edge keys of revision 2.x; the secret directive string grammar; the rendered file as the instance lock
**Target:** CIU v8.0.0 (breaking; `project.revision = 8` gates config acceptance)

**Proposal revision:** 3.1 (3.0 corrected in place; every change is traced to a T-finding in §4.3.13 and §4.7 X57–X72)
**Updated:** 2026-09-03

**Source documents integrated (all read in full):** this file at revision 2.1; `SPEC-V8.md` draft.2; `V8-REALIZATION-GRAPH.md`; `v8-dstdns-demo/` (65 files); ciu `docs/SPEC.md` 5.0.0 (v7, 4888 lines), `CHANGES.md` (through 7.11.0), `KNOWN_ISSUES_TODO_BACKLOG.md` (status table, CIU-72/73); `run-gate-project/SPEC.md` (R-01..R-38) and every `run-gate.toml` in the estate and in dstdns; assay `CHANGES.md` (B044 `assay lanes --json`), `src/assay/cli.py`, `runner.py`; a source-level dependency map of ciu, assay, run-gate, cmru, nyxloom and dstdns (review §2); the estate doctrine in `AGENTS.md`. Revision 2.1's own source list (dstdns decisions D-094..D-212, assay decisions through A-331, the v7 sources) stands behind the parts of this text that revision 3.0 did not change.

**How to read this document.** **Part 1** (§4.1, §4.3a, §4.4, §4.5, §4.6, §4.11) is the proposal itself, a self-contained statement of the v8 model. **Part 2** (§4.2, §4.3, §4.7, §4.8, §4.9, §4.10) is the rationale and audit trail: the inventory of everything considered, the reasoning walked through scenario by scenario, what the two interviews decided and why, every contradiction found and how it was resolved, what was dropped, and where the proposal knows it is incomplete. Where this document and `SPEC-V8.md` differ, the SPEC is the more precise statement and this document is to be corrected.

**Conventions.** `S<n>` cites a section of ciu `docs/SPEC.md` 5.0.0 (v7) only in Part 2, §4.6 and §4.11; `V8-S<n>.<m>` cites a rule of `SPEC-V8.md` draft.3; `R-nn` cites a finding of the 2026-09-02 review; `CIU-<n>` / `RG-<n>` / `B<nnn>` / `D-<nnn>` / `A-<nnn>` cite the ciu, run-gate and assay backlogs and the dstdns / assay decision records; `P<n>` cites a guiding principle from §4.1.1. Examples use dstdns's real names because dstdns is the consumer whose configuration was inventoried key by key.

---

# Part 1 — The Proposal

## 4.1 The v8 model

### 4.1.0 What v8 is, in one paragraph

A ciu consumer declares **what** it needs (*LogicalServices*), **how** each need can be satisfied at each realness level (*Realizations* — ciu stacks, external systems, or another instance's services), **where** things run (*Hosts*, *Networks*, *Layouts*), and **which** bundles a deployment includes. Every consumer of a capability declares a **binding** under a local name of its own choosing and says how the resolved address is to be **delivered** to it — as environment variables, or as data its templates read — exactly the way a secret declares its delivery. From those declarations ciu **derives** everything that used to be typed by hand and drift: every container/compose/hostname identity, the resolution of every binding (same instance, joined instance, cross-host, through a proxy, over mTLS), the publication of endpoints across hosts, the deployment order (waves) and the health gates between them, the readiness of transports, the facts minted by secrets, the contract of every capability (from what is bound to it), the deploy set for a chosen realness, and the facts the testing gate needs. Every declaration file is plain TOML that any tool can read; templates exist only for the artifacts other programs consume (compose files, application config files). Every derived value is written as data into `ciu.resolved.toml`, where templates, hooks, the built-in gate (`ciu gate`, run-gate's functionality lifted into ciu while run-gate stays available standalone) and assay read it. There is one identity derivation, one lock per instance (the checkout directory), one secrets file, one judge floor, no `.ciu/` directory, no hand-ordered phases, no `ciu.env` as a source of truth, no `routes` a template pulls by provider name, and no Jinja in any declaration.

### 4.1.1 Guiding principles (cited as P1–P11 throughout)

1. **P1 Single source of truth.** Every fact is declared in exactly one place; everything else derives or references it.
2. **P2 Fail fast.** A wrong or missing value refuses at the earliest point it can be checked: schema → `ciu check` → deploy. Never a silent default; the only defaults are *policy* defaults — correct in the absence of information and shadowing no fact.
3. **P3 Explicitness over magic.** Every derived value is visible in the rendered file and in `ciu check` output; nothing is built in that a file could declare (no built-in host, no built-in layout).
4. **P4 Mechanical checkability.** Prefer shapes a program validates completely (closed vocabularies, referential integrity, graph completeness).
5. **P5 Full preflight.** No error class that can be caught statically is discovered by a live deploy.
6. **P6 One derivation per identity.** Container name, hostname, compose key, compose project, network name, resolution host: one function, one place, used by every tool.
7. **P7 Minimal per-kind special-casing.** Adding a realization kind, a fact kind, or a secret source must not require carve-outs in every consumer of the shape.
8. **P8 Declaration separate from resolution.** What is needed is declared apart from how it is satisfied; the resolution is computed and recorded.
9. **P9 Config as data.** Templates substitute and expand data; they do not carry business logic. Layering is TOML deep-merge, not Jinja inheritance.
10. **P10 Nothing hidden.** Machine-owned state lives in visible, gitignored files a person can `cat` and `diff`; no hidden directories, no ambient environment as a config source.
11. **P11 Declarations are data; only artifacts are templates.** No declaration file is rendered. A machine can read, validate and rewrite every declaration; a template can only ever *consume* declarations and derived values.

### 4.1.2 Entity model

| entity | identity | meaning | declared by | key relationships |
|---|---|---|---|---|
| *LogicalService* | name | A capability the system needs; its **contract** (endpoints and facts consumers may rely on) is **derived** from the bindings that target it | `ciu.toml` `[service.<n>]` | has 1..n *RealnessVariants*; referenced by *Bundles*, binding `to`, `exec_in`, `image_from`, `pki`, `vault.service`, lane `requires.healthy` |
| *RealnessVariant* | (LogicalService, level) | Which *Realization* stands in for the LogicalService at a realness level; `mock` is a variant with no Realization | `[service.<n>].<level>` (a string, or `{ realized_by, service }`) | `realized_by` → exactly one *Realization* |
| *Realization* | name (one namespace across kinds) | A concrete way to provide services: `ciu_stack`, `external`, `joined` | `ciu.toml` or `ciu.instance.toml` `[realization.<n>]` | contains 1..n *RealizedServices* (ciu_stack), exactly one **primary**; has 0..n *Endpoints* (external); references an *Instance* + *LogicalService* (joined) |
| *RealizedService* | (Realization, service key) | One deployable service inside a stack: image, replicas, bindings, endpoints, secrets, config files | `ciu.stack.toml` `[ciu_stack.<svc>]` | `binds.<local>` → *LogicalServices*; `provides` → typed facts; `depends_on` → siblings; owns *Endpoints*, *Secrets*, *ConfigFiles*, *HostDirs*; has a derived *Identity* |
| *Binding* | (consumer, local name) | A consumer's dependency on a LogicalService (optionally one endpoint), with a wait rule (`healthy`/`started`/`none`), a delivery (`env`/`template`/`none`) and the facts it relies on | `binds.<local>` on a RealizedService or a gate environment; `requires = [...]` as sugar | resolved to a *Resolution*; contributes to the target's derived contract |
| *Resolution* (derived) | (consumer, local name) | How ciu satisfied a binding: network, host, port, URL, TLS facts, readiness prerequisites, the variables it injects | rendered `[resolved.bindings.*]` | derived from *Layout* × *Networks* × *Endpoint* × *Realization kind* |
| *Endpoint* | (Realization, name) | A reachable port/URL with publication scope and allowed sources | `…endpoints.<e>` | `publish`; `allow_from` → *Networks*/*Hosts*; target of bindings |
| *TypedFact* | string `kind:selector` | A provable statement about live infrastructure | in `provides` (services, hook entries, external/joined realizations), in binding `facts`; **derived** from vault-stored generated secrets | probed by ciu; provider resolved through the graph |
| *Host* | name | A machine with one address per address-plane *Network* and a declared `fqdn` | `ciu.hosts.toml` `[hosts.<h>]` | has *Addresses*; placed in *Layouts* |
| *Network* | name | A reachability domain: an address plane, or a proxy; transport security; optionally a *Realization* that must be up | `[network.<n>]` | `realized_by` → *Realization*; `pki` → *LogicalService* |
| *Bundle* | name | A set of *LogicalServices* that deploy together; may include other bundles | `[bundles.<b>]` | `services`, `includes` |
| *Layout* | name | Placement: which bundles run on which *Hosts*, over which *Networks* each host reaches the others | `[layouts.<l>]` | `hosts.<h>.bundles` → *Bundles*; `hosts.<h>.reach` → *Networks* |
| *Instance* | `instance_id` (path hash) | One checkout's deployment; a zero-instance project (no Realizations) has none | `ciu.instance.toml` (operator) + `ciu.instance.generated.toml` (ciu) | selects *Layout*; records *RealnessVariants* per layout; declares `joined` *Realizations* |
| *Identity* (derived) | per RealizedService/replica | `container_name`, `hostname`, `compose_key`, `compose_project`, `network` | rendered `[resolved.identities.*]` | one function (P6) |
| *Wave* (derived) | ordinal | Realizations deployed together | rendered `[resolved] waves` | from binding edges, `depends_on`, derived edges |
| *Environment* (gate) | name | Where a lane runs: an ephemeral image, `exec` into a Realization's variant service, or the host; may carry env-delivered bindings | `[testing.environments.<e>]` | `exec_in`/`image_from` → *LogicalService* |
| *Lane* (gate) | name | One command, one judge invocation, or a sequence of lanes, with preconditions and caps | `[testing.lanes.<l>]` | `environment`; `requires`; `assay_lane`; `lanes` |

Relationship summary: a *Bundle* names *LogicalServices*; the instance's realness selection maps each to one *RealnessVariant*, hence one *Realization*; the **deploy set** is the closure of those Realizations. A *Layout* places bundles on *Hosts*; *Resolutions* are derived per binding from placement and *Networks*; *Waves* are derived from bindings. A consumer never names a provider's Realization, never forms an address, and never reads a provider by name in a template — it reads its own local name.

### 4.1.3 Files and layering

**Declaration chain (plain TOML, no rendering — P11):** `ciu.toml` (committed, the project's declarations) → `ciu.site.toml` (committed, optional, sparse site override) → `ciu.instance.toml` (gitignored, per instance, operator-owned: layout, bundles, label, joins, host-port overrides) → `ciu.instance.generated.toml` (gitignored, ciu-owned, rewritten whole: instance identity, host facts, build facts, realness records) → **`ciu.resolved.toml`** (gitignored; the merged declarations plus every derived table under `[resolved]`, written atomically; the one machine interface). Merge semantics: scalars and lists replace, tables merge; nothing is deleted by a layer — a service, secret, binding or lane goes away with `enabled = false`.

**Per stack:** `ciu.stack.toml` (plain TOML; services under `[ciu_stack.<svc>]`, stack-level secrets under `[ciu_stack.secrets.<k>]`, `[hooks]`, `[governance]`, consumer tables) → re-rooted into the merged view under `realization.<R>.services.<svc>` (there is no per-stack rendered TOML and no stack-level override file: a site or instance layer overrides a stack table by its merged path, `[realization.consul_server.services.consul.endpoints.http] publish = "host"`). Artifacts: `ciu.compose.yml.j2` → `ciu.compose.yml` (identity, network, label, secret, config-file, port, `depends_on`, healthcheck timing, binding variables and governance stanzas **injected**); config-file templates → `ciu.rendered/<svc>/<mirrored target path>` mounted by parent directory (the v7 S5.3a hardening kept); `ciu.state.toml` for hook state; `ciu.secret-copy.<svc>.<key>` temp copies.

**Templates** see a fixed context: `project`, `instance`, `host`, `ciu_stack` (own services with `identity`, merged `health`, resolved `endpoints`, and — per service — `binds.<local>` for template-delivered bindings), `realization` (the merged view of the deploy set, read-only), `state`, `stack_dir`, the stack's consumer tables, user tables, `registry`, `vault.paths`, and `secret()` in config-file templates only. `StrictUndefined`; no `env`; no loader.

**Identity source.** `[ciu.instance.generated] instance_id` (shared by every host of a layout because the file travels with the push bundle); `[ciu.host.generated]` (which layout host this machine is, its hostname, roots, uids, environment type — regenerated per host); `[ciu.instance.build]`; `[ciu.instance.realness.<layout>]`. `ciu env print` exports the same facts for shells; `ciu.env` is never read.

**Secrets.** One gitignored `ciu.secrets.toml`; **Host inventory** `ciu.hosts.toml` (gitignored; `~/.config/ciu/hosts.toml` user-global); **Registry** `ciu.instance.json`; **Gate** `ciu.gate.<lane>.json` + `ciu-gate-evidence/`; **Data** `ciu-data/`. Nothing under `.ciu/`. The gitignore list ciu verifies: `ciu.resolved.toml`, `ciu.instance.toml`, `ciu.instance.generated.toml`, `ciu.instance.json`, `ciu.compose.yml`, `ciu.state.toml`, `ciu.rendered/`, `ciu.secret-copy.*`, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.gate.*`, `ciu.env`, `ciu-data/`, the evidence directory.

**Why plain TOML everywhere (R-08).** Revision 2.x rendered every declaration through Jinja, which meant (a) no external validator, editor schema or third-party tool could read a ciu file, (b) a typo in a *declaration* surfaced as a template error, (c) ciu could not safely write into a file it also rendered (X38 moved generated facts out for exactly that reason, and then `instance add --join` wrote into the Jinja overlay anyway — R-06), and (d) stack files that read derived values (`routes`) forced a two-pass render with a recording stub (R-05). Once consumers declare bindings (§4.1.5) and secret paths reference a checked `[vault.paths]` table (§4.1.8), no declaration needs an expression: what was `{{ vault.paths.x }}` is `path = "x"`; what was a `{% for %}` generating twenty near-identical service tables is the one-line string form `live = "x"`. `ciu schema --json` then emits a JSON Schema for every declaration file from the same table-spec that drives the validator.

### 4.1.4 Identity — one derivation

Inputs: `project.name` (committed, literal), `instance_id` (generated), the *Realization* name (registry key), the service key (stack file), and an optional replica index. Output, computed by one function and **written as data**:

```toml
[resolved.identities.db_core.postgres]
container_name  = "dstdns-98535c-db-core-postgres"      # {project}-{instance}-{realization}-{service}, `_` → `-`
hostname        = "dstdns-98535c-db-core-postgres"
compose_key     = "db-core-postgres"                    # qualified: no bare-alias collision on the instance network
compose_project = "dstdns-98535c-db-core"
network         = "dstdns-98535c-network"

[resolved.identities.controller.controller]
container_name  = "dstdns-98535c-controller"            # service == realization → the service part is omitted
```

Rules: the derivation is the only place these strings are formed — templates read them (`{{ ciu_stack.postgres.identity.container_name }}`; `{{ realization.db_core.services.postgres.identity.container_name }}` from elsewhere), hooks read them from their context, the gate reads the same table. Templates may not set `container_name:`/`hostname:` (equal values tolerated and removed). **Uniqueness is checked, not structural** (R-14): the `_`→`-` mapping is injective per name but not across the concatenation (`db_core`+`postgres` and `db`+`core_postgres` collide), so stage 8 refuses a collision naming both derivations. **Ownership labels are fixed** `ciu.project`, `ciu.instance`, `ciu.realization`, `ciu.service`, `ciu.replica`, `ciu.managed-by` (R-15): `clean`, `reap` and `diagnose` enumerate by them, and no consumer setting can orphan a container; consumer labels are authored in templates. `deploy.environment_tag` and `deploy.labels.prefix` are gone. Replicas: `instances = N` yields `-1..-N` suffixes and per-replica identity rows templates iterate.

### 4.1.5 Topology and bindings: hosts, networks, endpoints, layouts → derived resolutions

Distance is never declared on a consumer or a provider; it falls out of *Layout* × *Networks* × the provider's *Endpoint* × the provider's kind. The consumer side is one concept — the **binding**:

```toml
# applications/controller/ciu.stack.toml
[ciu_stack.controller]
requires = ["app_schema"]                       # sugar: binds.app_schema = { to = "app_schema" } — an ordering edge, no data
endpoints.http = { port = 8080, protocol = "http", publish = "proxy", host_port = 8083, path = "/api/controller", allow_from = ["host.tsstammtisch"] }

[ciu_stack.controller.binds.database]           # local name: what the application sees
to = "main_db.sql"                              # <LogicalService>.<endpoint>
delivery = "env"                                # DATABASE_HOST, DATABASE_PORT, DATABASE_URL injected into the container
env_prefix = "DATABASE"
facts = ["pg:role/controller", "pg:db/dstdns"]  # what this consumer relies on → enters main_db's derived contract; probed before this wave

[ciu_stack.controller.binds.tracing]
to = "tracing.otlp"
wait = "none"                                   # runtime-only: a resolution, no ordering edge
delivery = "template"                           # {{ ciu_stack.controller.binds.tracing.url }} in the compose/config templates
```

`wait` ∈ `healthy` (default: the provider's variant service and the endpoint's owner must be healthy — completed for `one_shot` — before this service's wave) | `started` | `none`. `delivery` ∈ `env` | `template` | `none` (required when an endpoint is named; absent otherwise). A binding without an endpoint (`to = "app_schema"`) derives an edge and facts only — and therefore **never a publication**: revision 2.x's `init_requires` derived a route for every ordering dependency, and a route to an endpoint on another host published that endpoint on the provider host even when nothing read it (R-22).

**Hosts** (`ciu.hosts.toml`): `[hosts.<h>] local, fqdn, ssh_*, addresses.<network>, secrets.<entry>, activate.*`. The `fqdn` is *declared* (R-16). No built-in host: `ciu init` writes `[hosts.localhost] local = true`.

**Networks**: `[network.<n>] kind = address | proxy`, `realized_by`, `tls`, `pki`, `fqdn`, `description`; `instance` implicit.

**Endpoints**: `endpoints.<e> = { port, protocol, publish = instance|host|proxy, host_port, host_bind, allow_from, path }`; names unique per stack. **Publication is derived**: an `instance`-published endpoint is additionally published on the provider host, bound to the network address a cross-host *resolution with data* uses; nothing is published on a single host; `publish = "host"` = always; `publish = "proxy"` = fronted by a proxy network. `ciu check --layout L` prints the publication table (R-71).

**Bundles and layouts**: `[bundles.<b>] services, includes, compose_profiles, compose_env`; `[layouts.<l>] environment (free-form, optional), hosts.<h> = { bundles, reach }`. A layout is always explicit; a project with exactly one layout needs no `--layout` (a derivation from a singleton, reported). No built-in layout: `ciu init` writes `[layouts.local]`.

**Resolution** (`resolve(consumer, binding)`, V8-S7.8): (1) the target through the realness selection to a Realization and its endpoint; (2) `joined` → the reference's container on the reference's network; (3) `external` → the declared URL; (4) same host → container name on the instance network; (5) otherwise the first admitting network in the consumer host's `reach` — a proxy network when the endpoint is `publish = "proxy"`, an address network when both hosts have an address and `allow_from` admits; (6) transport facts from the network (TLS paths of derived certificate secrets, readiness prerequisites); (7) `url` for `http`/`https`/`udp`. Written per consumer and local name:

```toml
[resolved.bindings.controller.controller.database]       # consumer realization.service → local name
service = "main_db"  realization = "db_core"  endpoint = "sql"
network = "mesh"  host = "100.64.0.11"  port = 5432  delivery = "env"
variables = ["DATABASE_HOST", "DATABASE_PORT"]
requires = ["tailscale_node"]                              # derived readiness edge
# on `local`: network = "instance"  host = "dstdns-98535c-db-core-postgres"  port = 5432
```

The consumer's compose template contains no address at all for `env` delivery, and `{{ ciu_stack.controller.binds.tracing.url }}` for template delivery — identically in every deployment shape, and identically after `main_db` is re-pointed to a managed database, a seeded image or a simulator. **Gate environments bind the same way** (`[testing.environments.tester.binds.db] to = "main_db.sql" delivery = "env" env_prefix = "TEST_DB"`), which hands the facts to a lane process as environment variables — so assay needs only its existing `required-env:` facts and never reads ciu's file (R-03). CIU's own Vault client is the pseudo-consumer `ciu` with an implicit binding to `vault`.

**Remote deployment (push) — releases and receipts (rev 3.1, T-16/T-25).** `ciu push` builds, per host, a **release**: the closure of files that host needs plus a manifest (`ciu.release.json`: every file with its SHA-256 and mode, image digests, layout, instance, git revision), addressed by the manifest's digest. The release is staged at `<bundle_dir>/releases/<digest>.staging`, verified on the target, renamed, and `ciu activate apply` atomically switches `current` to it and keeps `previous`; `rollback` switches back and re-applies the previous release — never a bare `ciu down`. An interrupted transfer can no longer produce a mixed tree, and an exclusion that intersects the closure is refused. Push order is the layout's declaration order, checked against the cross-host graph; render-on-target is unchanged. Each host's successful `ciu up` writes a **receipt** (`ciu.receipt.json`: instance, layout, resolved-file digest, the services, completions, facts and publications that passed); `ciu activate` carries the receipts to the next host, and a consumer host accepts a remote fact only from a valid receipt — reachability alone marks it `assumed` and WARNs. What travels as secrets is a per-host **capsule** derived per source (§4.1.8).

### 4.1.6 Init graph, waves, health gate

**Edges** (all data, no phases): every binding with `wait ≠ none` → the target's variant service, the endpoint's owner, and the providers of its `facts`; `depends_on` siblings (rendered into compose with the derived condition); **derived** edges — every vault-sourced or vault-stored secret → the Vault realization; every vault path → its minter (a `from = "generate", store = "vault"` secret or a hook entry's `provides`; no minter is a static ERROR); every cross-host resolution over a network with `realized_by` → that transport on both ends; every resolution over a `tls ≠ none` network → the `pki` service. There is no `after` (a `requires` entry is the same edge, R-21) and no `uses` (a binding with `wait = "none"`).

**Contract conformance** is the completeness check for providers, now computed from consumption (R-19): the contract of `main_db` is the union of endpoints bound to it (`sql`) and the `facts` of those bindings; every declared variant — `live = "db_core"`, `seeded = "db_core_seeded"` — must provide all of it (endpoints exist; facts in `provides`, hook `provides`, or derived from vault-stored secrets), whether or not it is currently selected. Nobody types a contract, and a seeded stub that forgets an endpoint fails `ciu check` before anyone selects it. Facts a provider lists that nobody binds are INFO, not a warning.

**Primary and variant service, waves, gates, health** are unchanged from revision 2.1: a multi-service stack marks one `primary = true`; a variant may name which service carries the capability; Realizations deploy as units in topological waves (a Realization-level cycle is an ERROR naming the remedy); between waves ciu waits for every provider a later wave has an edge to (`gate_timeout` per service, derived from its healthcheck parameters); fact probes run at the consumer's wave inside the providing container; cross-host, reachability of the resolution is probed and the provider host's own gate is authoritative — under the supported `ciu activate apply` flow hosts run serially, which closes revision 2.1's gap 4c (R-24). **The per-Realization pipeline is now written out** (V8-S8.7, R-41): hostdirs → `pre_secrets` hooks → secrets and temp copies → `pre_compose` hooks (their `state` is visible to every later step of the same run) → config files → compose render and injection → `compose up` → `post_compose` hooks after the Realization's providers are healthy → wave gate and fact probes.

### 4.1.7 Realness

Levels `live`, `seeded` (prepared; `owned-seeded` renamed, R-29), `simulated`, `mock = {}`. Selection precedence is CLI > `[realness.pin]` > `[realness] default`; the per-layout record `[ciu.instance.realness.<layout>]` written at the first `ciu up` is a **constraint**, not a source (R-26): a selection that differs from it — by flag or by a changed pin — is refused until `ciu clean --vanilla`. Joins are a Realization kind declared in the plain-TOML instance file, written by `ciu instance add --join` with a round-trip writer (R-06); instance labels are unique per git family and a join names a label or an absolute path (R-27); a joined vault's token is read from the reference's store under the reference's shared lock (R-28).

### 4.1.8 Secrets

Declaration stays on the RealizedService (or once at stack level), with `delivery` mandatory — and the directive string grammar is replaced by **structured data** (R-30):

```toml
[ciu_stack.controller.secrets.postgres_password]
from = "vault"                                  # vault | generate | ask | file | host | ephemeral
path = "postgres_controller_password"           # a [vault.paths] key (checked) or a literal path containing '/'
delivery = "file"                               # → /run/secrets/postgres_password

[ciu_stack.controller.secrets.bootstrap_token]
from = "generate"
store = "vault"                                 # was GEN_TO_VAULT:<path>; derives the fact vault:secret/<path>
path = "controller_bootstrap_token"
delivery = "env"
env_name = "CONTROLLER_BOOTSTRAP_TOKEN"

[ciu_stack.postgres.secrets.workerdb_ddl_password]
from = "generate"  store = "vault"  path = "postgres_workerdb_ddl_password"  delivery = "none"     # minted here for others

[ciu_stack.exporter.secrets.consul_token]
from = "vault"  path = "consul_docker_stats_exporter_token"  delivery = "configfile"              # only secret("consul_token") in this service's config-file templates

[ciu_stack.nginx.secrets.tls_cert]
from = "host"  entry = "tls_cert_pem"  delivery = "file"                                        # the placement host's own entry
```

`delivery` ∈ `file` | `env` | `configfile` | `native` | `hook` (materialized for this stack's hooks only — was `consumed_by`) | `none`. `produced_by` is gone: the bundle that mints a path is derivable from the minter edge (R-31). `[vault.paths]` is a **reference table ciu reads** to resolve `path` keys, so a typo in a path name is refused with the closest candidates instead of becoming a wrong KV path (in revision 2.x the same table was "never read by ciu" and paths were Jinja-composed strings). The secret-free scan keeps v7's `/`-bearing rule (revision 2.x's character-class exemption also exempted `hunter2secret`). Values live in one `ciu.secrets.toml`, written atomically under the instance lock; `file` delivery bind-mounts temp copies; `env` delivery passes through the compose process environment, whose content is exactly enumerated; certificates for TLS networks are derived stack-level secrets satisfied by the `pki` provider's hook.

**What travels on push is a per-host capsule, derived per source (R-32, corrected by T-09).** Local and remote differ in exactly one thing — where the store is — so the sender builds `ciu.secrets.transport.toml` with what only it has: `generate`+`store = local` and `ask` values from the store, `file` values **read at push time** (the store never held them — revision 3.0 wrongly said "the stored value travels"), the target's own `host` entries; `ephemeral` and `native` never travel (a cross-host-shared secret cannot be ephemeral); `vault`-sourced values are fetched **by the target** when it has a derived resolution to the `vault` LogicalService, otherwise pre-fetched by the sender and put in the capsule — and when the sender cannot reach Vault either, push refuses naming host, key and both reachabilities. The target imports capsule entries as `source = "transport:…"` and **never refreshes them** (a transported `from = "vault"` row would otherwise try to refresh against a Vault it cannot reach). A project with no Vault ships everything local; one with a reachable Vault ships local-source entries only. `ciu check --layout L` prints, per host, what travels and why. This replaces both v7's fixed "target fetches" and revision 2.x's fixed "sender ships everything". Secret files are delivered from a per-service directory mounted at `/run/secrets` and refreshed by rename, so a running reader never sees a truncated value (T-24).

### 4.1.9 Instances, locking, lifecycle

**Every checkout is an instance** (a project with no Realizations has none — §4.1.10). `ciu instance init [--host] [--layout] [--bundles] [--label]` derives the identity (a hash of the physical path), writes the generated file, and creates `ciu.instance.toml` when absent. `ciu instance list/show/add/remove/exec/reap/lease` are the former worktree verbs (`lease` restored from v7 S16.9, R-44); `ciu instance exec --env <e>` runs in a gate environment after the **mount proof** (the container mounts *this* checkout at `workdir` — v7 S16.7's guard, restored, R-47). Budget and lease: `[ciu.instances] max_concurrent, lease_ttl_hours`; the lease holder is `ciu@<host.hostname>:<instance_id>` from the host facts (R-45).

**Mutex (R-42).** The lock object is the **checkout root directory** (`flock` on its descriptor): its inode is stable for the life of the checkout, so nothing ciu renders or git replaces can fork the mutex. Mutating verbs take it exclusively; the gate takes it shared for the duration of its lanes (so `up` cannot recreate a container under an `exec` lane, while several gates coexist under admission); read-only verbs take it shared while reading `ciu.resolved.toml`, which is written by temp-file and atomic rename and is therefore always complete or absent. Revision 2.x's rendered-file lock needed in-place rendering, a completion table, torn-file detection by every reader, an `fstat`/`stat` retry and "clean truncates never unlinks", and still lost the mutex to `git clean -x`; none of that exists now. Lock order: instance lock → git-common-dir registry lock → a joined reference's instance lock (shared). The store is written under the instance lock; there is no separate secrets lock and no per-stack lock. A dead holder's `flock` is released by the kernel; there is no lock-breaking verb. A filesystem that cannot `flock` a directory is refused by name rather than run unlocked.

**Lifecycle.** `ciu instance init` → `ciu check` (automatic before every mutating verb) → `ciu up` → `ciu gate …` → `ciu down` / `ciu clean [--vanilla]`; `clean` disconnects ciu's own container from the instance network before removing it and refuses while a joiner is attached (R-39).

**State posture (rev 3.1, T-11/T-17/T-18/T-19 — operator decision §4.3.13).** Authoritative instance state stays in visible, gitignored checkout files, as in v7; the spec now says plainly that deleting them (`git clean -x`) destroys the store, the instance file, the record and the realness records, and adds `ciu instance backup|restore`. What the posture costs is stated, not hidden: a moved worktree needs `instance init --move`; a 24-bit path-hash collision between checkouts is detected (every resource carries a `ciu.checkout` label and a mutating verb verifies ownership before deleting) rather than avoided; the physical path is proven by a sentinel bind before it is trusted. Locks are acquired in ascending instance-id order over a set resolved before mutation, join graphs must be acyclic, and gate shared-resource locks are directory locks on the owning stack directory — the three fixes that make the directory-lock design deadlock- and unlink-safe. The reviewer's registry-with-UUID alternative is recorded in §4.3a as rejected by decision.

### 4.1.10 The testing gate — `ciu gate` (run-gate's functionality lifted; run-gate stays standalone)

**Posture (R-01).** The estate's tools must stay usable standalone with no hard dependencies. run-gate's implementation (`run-gate.py`, stdlib, 180 tests) is lifted into `ciu/gate/` so that `[testing]` is expressed in the entity model; run-gate itself stays maintained in parallel for adopters that want a copied script and no ciu, and is aligned with v8's file layout as a run-gate item (its exec-mode container derivation reads the v7 rendered file). Both read the same `assay.toml`. Consequences ciu owes: a **zero-instance mode** (R-51) — a project whose `ciu.toml` holds only `[project]` and `[testing]` runs `ciu gate` with no instance, no lock, no rendered file, no docker unless a lane's environment is a container, and no judge unless a lane is an assay lane; **preflights per need** (R-02) — no startup check for a binary the verb does not use.

```toml
[testing]
inherit = "../ciu.toml"                        # environments only, one level — the monorepo's shared tester image declared once (run-gate R-22)
cgroup_slice = "ciu-gate.slice"
evidence_dir = "ciu-gate-evidence"
history = 20                                   # LaneResults kept per lane

[testing.judge]
version = ">=2.4"                              # required iff an assay lane exists; provenance always required

[testing.environments.tester]                  # exec into a running RealizedService of THIS instance
mode = "exec"
exec_in = "tester"                             # a LogicalService → its variant's service; identity derived; mount proof before every lane
forward_env = ["RUN_LIVE_TESTS"]

[testing.environments.clean]                   # ephemeral container on the instance network, in the slice
mode = "ephemeral"
image_from = "tester"
binds.db = { to = "main_db.sql", delivery = "env", env_prefix = "TEST_DB" }   # TEST_DB_HOST/PORT/URL for the lane process

[testing.lanes.unit]
kind = "command"
environment = "clean"
argv = ["pytest", "-q", "tests/unit"]
budget = "10m"
resources = { memory_max = "2G", memory_swap_max = "0", cpu_weight = 100, io_weight = 100 }

[testing.lanes.durable_dlq]
kind = "assay"
environment = "tester"
assay_lane = "durable_dlq"                     # ciu asks `assay lanes --json` for base_source/tools/env; never parses assay.toml
requires = { realness = { main_db = "live" }, healthy = ["main_db", "vault"] }
require_provenance = true                      # running images must match HEAD, else NOT_RUN/provenance-mismatch
resources = { memory_max = "4G", shared = ["main_db"] }

[testing.lanes.gate]
kind = "sequence"                              # one process, one LaneResult per member — no nested `ciu gate`
lanes = ["schema", "unit", "durable_dlq"]
stop_on = "FAIL"
```

Semantics: preconditions → `NOT_RUN` with a closed reason vocabulary (`realness-mismatch`, `service-down`, `environment-down`, `environment-mismatch`, `env-missing`, `dirty-tree`, `no-headroom`, `judge-floor`, `judge-provenance`, `provenance-mismatch`); `exec` lanes run in the target's cgroup and their caps must be ≤ its governance; admission by RAM headroom within the slice; the judge floor checked once per environment image per run; `--request-base` passed exactly to lanes whose assay lane reports `base_source = "request"` (`request_base` as a ciu key is gone, R-52); every LaneResult (`ciu.gate.<lane>.json`, `schema_version: 2`, plus a pruned history under the evidence directory) records outcome, timing, resources, judge provenance, `helpers` and the provenance of every required service (R-55). Sequence lanes (R-53) replace `argv = "ciu gate a && ciu gate b"` and remove run-gate's R-25 hazard; `[testing] inherit` (R-54) keeps the vbpub monorepo's `tester-unified` environment declared once. run-gate's remaining rules are carried **by behaviour** in V8-S16.4/S16.12 (rev 3.1, T-20 — revision 3.0 had carried them by citation only): ephemeral lanes run the detached create/start/wait/remove state machine (never `--rm`, so a failed container's evidence survives and status comes only from `docker wait`), git isolation uses a private writable config with `safe.directory`, the checkout is mounted at both its physical and its workdir path, path charset (R-04), evidence 0600 on failure (R-26), history (R-36). Further rev 3.1 corrections: admission is a locked transaction with reservations and exec-lane caps are reported as requested, not applied (T-21); the assay command's `--progress` takes a path under the evidence directory and CIU declares its minimum drivable judge (4.1.0), refusing a lower floor (T-08); a lane's available environment is defined (forward_env ∪ binding variables ∪ container env for exec) and the demo's tester governance was raised to its lanes' caps (T-12); `[testing.externals]` lets a zero-instance library declare an externally managed test database as a typed binding (T-31); `inherit` is recursive and cycle-checked with paths relative to the declaring file (T-32); LaneResults carry `status` and the `ciu/lane-result` header (T-07, T-33). CLI: `ciu gate [lanes…] [--list] [--dry-run] [--json] [--base REF] [--allow-dirty] [--check-env] [--worktree PATH] [--admission-wait D]`, `ciu gate doctor`; `validate-pointers` is `ciu check --gates`; `RUN_GATE_*` knobs become `CIU_GATE_*`.

### 4.1.11 `ciu check` in v8 (runs automatically before every mutating verb)

Fifteen stages with declared dependencies so that an early typo does not hide unrelated findings (R-71): 1 files · 2 parse (TOML + secret-free scan) · 3 schema · 4 references · 5 contracts (derived; every variant) · 6 graph · 7 topology (incl. publications, push order) · 8 identity (with the ambiguity message) · 9 secrets · 10 realness · 11 governance · 12 testing (`assay lanes --json` when a judge is reachable; inherit; sequences) · 13 hooks `--validate` · 14 registry validator · 15 live. `--layout L` prints the publication table and the bundle table; `--json` uses the common header every machine artifact shares (`api`, `api_version`, then `operation`, `status`, `findings[]`, `resolved`; R-49, refined per artifact by T-33). A zero-instance project runs stages 1–3, 11, 12, 14.

### 4.1.12 CLI verbs (v8) — every v7 verb has a disposition (R-61)

`ciu init` (scaffold, kept from v7 S19; writes the explicit host and layout — no built-ins) · `ciu instance init | list | show | add --join | remove | reap | lease | exec --env` · `ciu check` · `ciu render [--show-injected]` (the template-vs-artifact diff, R-37) · `ciu up` · `ciu dev --realization r` (v7 S5a's loop) · `ciu down` · `ciu clean [--vanilla]` · `ciu status [--live]` (absorbs `health`/`status`) · `ciu show bundles|layouts|services|realizations` (absorbs `profiles`/`layouts`) · `ciu gate …` · `ciu secrets show|reset|host|rotate-bootstrap` (absorbs `host-secrets`) · `ciu env print` · `ciu build` (v7 `bake`; stamps `org.opencontainers.image.revision` with `-dirty`, R-38) · `ciu push | activate` · `ciu ssh <host>` · `ciu provenance` · `ciu diagnose` · `ciu governance ksm|iops-baseline` · `ciu migrate [--check] [--secrets] [--hostdirs] [--config]` (absorbs `migration-check`; also the v7 hostdir relocation, R-36) · `ciu hook run` · `ciu schema --json` (absorbs `capabilities`; R-63) · `ciu query identity|binding|publications|capability|lanes --json` (narrow machine queries, T-35) · `ciu testing flatten` (T-32) · `ciu instance backup|restore` (T-11) · `ciu check --validate-hooks` (hook validation executes consumer code and is opt-in, T-23) · `ciu up --receipts|--require-receipts`, `ciu push --prune` (T-16/T-25) · `ciu doctor` · `ciu version` (prints the minimum judge). Exit codes keep v7's meanings (0 ok, 1 runtime, 2 config/usage refusal, 3 environment bootstrap) plus 4 lock contention and 5 remote failure (R-48); the gate keeps its own table.

### 4.1.13 Migration shape (v7 → v8; `ciu migrate`, stated in V8-Appendix A)

1. **Instance:** `ciu instance init` in every checkout; cockpit aliases switch to `eval "$(ciu env print)"`.
2. **Declaration files** (`--config`): `.j2` declarations become plain TOML with the new names; `{% set %}` constants inlined, `{{ vault.paths.x }}` → `path = "x"`, control flow expanded once and flagged; `[deploy]` → `[project]`, `[bundles]`, `[layouts]`, `[realness]`; the retired keys of §4.5 J deleted.
3. **Stack files:** `[ciu_stack.<svc>]`; `init_requires`/`uses`/`after` → `requires`/`binds.<local>`; `init_provides`/`[hooks.provides]` → `provides`; directives → structured secrets (the mapping table is in V8-Appendix A); `delivery` on every secret; consumer scalars into sub-tables; `primary`; `instances`.
4. **Templates:** delete injected stanzas; `routes.X.e.*` reads → `ciu_stack.<svc>.binds.<local>.*` (or nothing, with `env` delivery); identity/host reads → `identity.*`, `instance.*`, `host.*`; config-file templates the same, `secret()` needs `delivery = "configfile"`.
5. **Hooks:** `run(config, ctx)` → scripts on `ciu.hookkit` (`context()`, `emit()`, `wait_healthy()`, `wait_tcp()`, `secret_file()`, `--validate`); `apply_to_config` → `emit(state=…)`.
6. **Secrets state** (`--secrets`) and **host directories** (`--hostdirs`): imported and relocated without re-minting or deleting.
7. **Gate:** `run-gate.toml` → `[testing]` (`container_name` → `exec_in`; `memory` → `resources.memory_max`; pins → the judge floor; conjunctions → `sequence`; central config → `inherit`); `.assay/` verdicts → the evidence directory; run-gate itself stays available.
8. **dstdns specifics:** `app_identity.*` (~45 tables + 41 copies) deleted in favour of the merged `realization.*` view; the 17 stack files that read `routes` become bindings; 13 hooks rewritten on hookkit; `tests/test_deploy_phase_ordering.py` retires with the phases.

### 4.1.14 The minimal project (R-66)

```toml
# ciu.toml — written by `ciu init --stack web`
[project]
name = "hello"
revision = 8
[realness]
default = "live"
[service.web]
live = "web"
[realization.web]
kind = "ciu_stack"
location = "web"
[bundles.all]
services = ["web"]
[layouts.local]
hosts.localhost = { bundles = ["all"], reach = ["instance"] }
[testing.lanes.unit]
kind = "command"
environment = "host"
argv = ["pytest", "-q"]
```
plus `ciu.hosts.toml` (`[hosts.localhost] local = true`, gitignored, also written by `init`) and `web/ciu.stack.toml` (`[ciu_stack.web] image = "nginx:1.27"` and an endpoint). `ciu init --stack web && ciu instance init && ciu up && ciu gate unit`. A **zero-instance** project (cmru, nyxloom, the trivial vbpub adopters) is `[project]` + `[testing]` only, and `ciu gate` needs nothing else. What revision 2.x needed for the same result: nine tables, a label prefix, four health keys, a `contract`, and a hand-written gitignored hosts file in every fresh clone.

## 4.3a Where more than one design is genuinely valid

**A. Where machine-owned rendered artifacts live** — unchanged from revision 2.1: flat visible files next to the stack (adopted; `ciu.rendered/` is a visible nested directory, not a hidden one), or a per-instance state directory outside the repo for read-only checkouts.

**B. Lock target** — resolved (R-42, interview Q2): the checkout directory descriptor. Revision 2.1's alternative table is retired; the reasoning is in §4.3.7.

**C. Binding-carried credentials** — a binding could also deliver the provider's published secret under the consumer's local name (`DATABASE_PASSWORD` next to `DATABASE_HOST`), unifying endpoint and credential delivery the way service-binding specifications do. Considered and **deferred**: secrets keep their own declaration in v8.0.0 because the minter-edge model already gives every secret a provider and a delivery, and folding credentials into bindings would create a second path to the same value. Recorded in §4.10 for a later revision.

## 4.4 What still needs to be built

Each item names the owning tool, the shape, and why it does not exist today. `SPEC-V8.md` draft.3 is the acceptance reference for every row. Suggested checkpoints: **A** (V8-1, V8-14, V8-2, V8-11, V8-13, V8-5 — files, identity, lock, check: everything that needs no graph), **B** (V8-3, V8-4, V8-6, V8-7, V8-8, V8-16 — the model), **C** (V8-9, V8-10, V8-17, V8-18 — secrets, hooks, migration), **D** (V8-12, V8-19 — the gate and the neighbours), **E** (V8-20, V8-21, V8-22). The first dstdns stack converted is the tracer bullet before the rest.

| # | mechanism | owner | shape | why it doesn't exist yet |
|---|---|---|---|---|
| V8-1 | **Config schema v8 + `revision = 8` gate + `ciu schema --json`** (V8-S3, S3.8.4) | ciu `config_model.py` | closed-key validators for every table in §4.5 generated from one declarative table-spec; JSON Schema emitted from the same spec | today's validator covers phases/profiles/layouts, the S3.14 registry, the worktree table and secrets; no realization/network/binding/testing tables exist |
| V8-2 | **Instance identity source** (V8-S4.1, S14.2): `ciu instance init`, plain `ciu.instance.toml`, `ciu env print`, `ciu.env` no longer read | ciu `workspace_env.py`, `paths.py` | the generated file shipped as ciu-P47 (7.10.0); remaining: the plain instance file, the verb, and dropping `ciu.env` as an input | `ciu.env` remains the source (`paths.py:70/78`, `workspace_env.py:1167ff`) |
| V8-3 | **Realization registry + logical services + variants + derived contract + minter resolution** (V8-S5, S8.3) | ciu new `registry.py` | `[service.*]`, `[realization.*]`; contract from bindings; conformance of every variant; minter edges | S3.14's registry is flat; nothing checks provider coverage |
| V8-4 | **Stack file `ciu.stack.toml`: re-rooting, closed key set, merged view `services.<svc>`, site/instance overrides of stack tables** (V8-S3.6, S6) | ciu `config_model.py`, `engine.py` | parse `[ciu_stack.<svc>]`, bind under `realization.<n>.services`, expose the S3.5 contexts | one arbitrary root table per stack, no re-rooting |
| V8-5 | **Identity derivation + resolved table + compose enforcement + fixed `ciu.*` labels** (V8-S4, S11.3–S11.4) | ciu new `identity.py`; `composefile.py` | one function with elision and the ambiguity check; `[resolved.identities]`; stage 8 | `deploy.container_name` has no stack component (CIU-66); labels under a consumer prefix |
| V8-6 | **Topology: hosts (`fqdn`), networks, endpoints, bundles (`includes`), layouts + binding resolution + derived publication + `per_host` + TLS secrets** (V8-S7, S10.5) | ciu new `topology.py`; `hosts.py`, `deploy_pkg/layouts.py` | entities of §4.1.5; `resolve()`; layout-derived `ports:` from bindings with data only; `[resolved.bindings]`; publication table | layouts carry no networks/reach; hosts have no addresses; routes are consumer-typed |
| V8-7 | **Init graph from bindings, derived edges, waves, gates, the S8.7 pipeline, provider-resolved probes** (V8-S8) | ciu `provisioning.py`, `deploy.py`, `deploy_pkg/phases.py` | replace `ordered_phases`; bind/depends/derived edges; `gate_timeout`; ordered pipeline with state visibility | phases are hand-declared; the pipeline order is implicit |
| V8-8 | **Realness selection, record as constraint, joins via the plain instance file** (V8-S9) | ciu `deploy.py`, `worktree.py` | precedence resolver; record writer/refuser; `joined` kind reading the reference under a shared lock; round-trip TOML writer for `instance add` | no realness concept; S16.1 join generates `ref_services` |
| V8-9 | **Secrets v8** (V8-S10): structured sources, `[vault.paths]` resolution, `delivery` incl. `hook`/`configfile`, one store, temp copies, derived TLS secrets, per-source push rule | ciu `secrets/materialize.py`, `composefile.py` | one store file under the instance lock; delivery axis; bundle content derivation | stores are `.ciu/secrets/<name>`; directive strings parsed by regex |
| V8-10 | **`.ciu/` removal**: `ciu.rendered/` directory mounts, gitignore list check, KSM cache relocation, `ciu.hosts.toml`, `ciu.instance.json`, evidence dir | ciu `config_constants.py`, `composefile.py`, `governance.py`, `hosts.py` | rename targets; parent-directory mounts (v7 S5.3a kept) | `MACHINE_DIR = '.ciu'` wired through S1.6/S1.7/S4.9/S4.17/S4.26/S5.2/KSM |
| V8-11 | **Instance lock on the checkout directory + atomic rendered file + verb classes** (V8-S14.3–S14.4) | ciu `cli.py`, `config_model.py` | `flock` on the root directory fd; exclusive/shared/lock-free classes; `--wait`; filesystem refusal | no instance-wide lock exists |
| V8-12 | **`ciu gate`** (V8-S16; run-gate lifted): `[testing.*]` schema, zero-instance mode, `inherit`, environment bindings, `exec_in` + mount proof, `sequence` lanes, `assay lanes --json` as the only assay interface, provenance in LaneResults, history, cgroup admission, CLI, `CIU_GATE_*` | ciu new `gate/` package (from `run-gate.py`), tests lifted | §4.1.10 | run-gate re-derives identity from `ciu.global.toml`, pins by version triple, spawns nested gates for conjunctions |
| V8-13 | **`ciu check` 15 stages with dependency-based execution, publication/bundle tables, one JSON envelope** (V8-S15, S18.4) | ciu `cli.py`, `warn_policy.py` | stage table of §4.1.11 | `ciu check` runs 13 stages serially and stops at the first ERROR |
| V8-14 | **Plain-TOML declarations + strict template rendering for artifacts only + `--show-injected`** (V8-S3.2, S11.7) | ciu `config_model.py` | drop the TOML render sites; `StrictUndefined` for compose/config-file templates (shipped as CIU-74's fix in 7.x); the injection diff | every layer is rendered with Jinja |
| V8-15 | **`ciu instance` verb family + `lease` + unique labels + registry record v2** (V8-S14.6–S14.7, S18) | ciu `worktree.py`, `cli.py` | rename with alias; `lease`; label uniqueness | CIU-50 open |
| V8-16 | **Compose injection** (V8-S11): identity/network/alias/label/`CIU_*` env/binding variables/secret/port/depends_on/healthcheck-timing/config-dir/governance stanzas; template prohibitions; disabled-service pruning; replica blocks | ciu `composefile.py` | parse rendered YAML, inject, validate, re-serialize | templates hand-write everything |
| V8-17 | **Hooks v2 + `ciu.hookkit` + `ciu hook run` + hook templates rewritten** (V8-S12) | ciu `hooks_runner.py`, new `hookkit/` | subprocess JSON context v2 with resolved bindings; outputs; `--validate`; the helper package (stdlib only) | hooks are imported Python modules |
| V8-18 | **`ciu migrate` (config/secrets/hostdirs/gate) + `ciu init` v8 + `ciu doctor`** (V8-S19, App A) | ciu `cli.py`, new `migrate.py` | mechanical conversions with a report; explicit host/layout scaffold; environment report | `ciu init` writes v7 files; `migration-check` covers persist:secret only |
| V8-19 | **Neighbour alignment**: run-gate reads `ciu.resolved.toml` identities when present (RG item); assay consumer repoint (`derived:` optional, `required-env:` fed by environment bindings) | run-gate-project, dstdns `assay.toml` | one RG package; consumer edit | run-gate reads `ciu.global.toml [deploy]` only |
| V8-20 | **dstdns migration** (35 stacks, 13 hooks, templates, gate config) — `v8-dstdns-demo/` is the target shape | dstdns | per §4.1.13 | consumer work |
| V8-21 | **SPEC v8 promotion** — `SPEC-V8.md` becomes `SPEC.md` at the v8.0.0 cut; CONFIG.md/CONSUMERS.md regenerated from the table-spec; run-gate SPEC cross-referenced, not folded (run-gate stays) | ciu docs | — | — |
| V8-22 | **Verb dispositions**: `status`, `show`, `dev`, `ssh`, `provenance`, `governance ksm\|iops-baseline`, exit codes | ciu `cli.py` | §4.1.12 | v7 verbs with no v8 home in revision 2.x |
| V8-23 | **Releases and receipts** (V8-S17.3–S17.5): manifest + closure computation, staging/verification/atomic `current` switch, `previous`/rollback, secrets capsule per source, receipts written by `up` and carried by `activate`, remote-fact acceptance | ciu new `release.py`; `push`/`activate`/`up` | §4.1.5, §4.1.8 | v7 SPEC J rsyncs in place; no manifest, no receipt |
| V8-24 | **Gate conformance fixtures** shared with run-gate (third-party Alternative E): a versioned package of argv builders and black-box scenarios (detached execution, dual mount, git isolation, admission, sequences, progress/resume) that both `ciu gate` and `run-gate` must pass; no runtime dependency either way | vbpub `gate-conformance/`, run-gate-project, ciu | — | parity is asserted by citation today |
| V8-25 | **Query surface and artifact headers** (V8-S3.7.6, S18.4): `ciu query …`, `resolved.capabilities`, `api`/`api_version` on every artifact with the compatibility policy | ciu `cli.py`, renderer | §4.1.12 | one `schema_version` for unrelated artifacts in revision 3.0 |

## 4.5 Validation — every key, every level, the entire schema as v8 leaves it

Columns: key | table / level | type | reason for existence | owner | example. Closed vocabularies are spelled out; keys whose values are derived are in the read-only table C; `V8-S<n>.<m>` points at the normative rule.

### A. Project declarations (`ciu.toml` → `ciu.site.toml`)

#### A1 `[project]` and `[ciu]` (V8-S3.4)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `name` | `project` | name, literal | the one human-chosen identity component | consumer / identity, compose, gate | `"dstdns"` |
| `revision` | `project` | int = 8 | refuses a v7 config against v8 ciu at parse time (P2) | consumer / parser | `8` |
| `log_level` | `project` | `DEBUG\|INFO\|WARN\|ERROR` | verbosity | consumer / engine | `"INFO"` |
| `landscape_id` | `project` | `^[a-z][a-z0-9-]{0,62}$`, optional | shared identity of one landscape across instances; bound as `instance.landscape_id` | consumer / templates, hooks | `"dstdns-dev"` |
| `registry.url` / `registry.namespace` | `project.registry` | str / name | where project-built images live and how they are named | consumer / build, templates | `""` / `"dstdns"` |
| `health.interval` / `.timeout` / `.start_period` / `.retries` | `project.health` | duration / duration / duration / int — **policy defaults** 10s / 5s / 60s / 6 | defaults merged into every service's `health`; `timeout` is the probe timeout, not the gate budget | consumer / merge, injection | `start_period = "240s"` |
| `health.gate_timeout` | `project.health` | duration, optional | the inter-wave convergence budget when the derived default is wrong | consumer / gate | `"300s"` |
| `compose_env.<VAR>` | `project.compose_env` | str | consumer env passed to every compose process; identity facts never belong here | consumer / compose env | `DSTDNS_TELEMETRY = "on"` |
| `control.<flag>` | `project.control` | bool | named switches `enabled` may reference by name | consumer / filter | `enable_observability = true` |
| *(vendor image list)* | — | — | none (rev 3.1, T-29): a service with a `build` table is project-built, every other image is pulled — ownership sits next to the image | — | — |
| `standalone_root` / `require_fqdn` / `auto_connect_network` / `exit_on` / `user_tables` / `registry_validator` | `ciu` | bool / bool / bool / `WARN\|ERROR\|NEVER` / list / path | root lock; refuse a host without `fqdn`; attach the devcontainer; severity policy; consumer tables; `[registry]` validator | consumer / engine, check | `exit_on = "WARN"` |
| `max_concurrent` / `lease_ttl_hours` | `ciu.instances` | int ≥ 1 / number > 0, both optional | budget and lease across a git family | consumer / instance verbs | `3` / `72` |
| *(labels prefix, env defaults)* | — | — | none: ownership labels are fixed `ciu.*` (R-15); template data lives in user tables (R-13) | — | — |

#### A2 `[service.<n>]` — LogicalServices (V8-S5.2, S5.3)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `service` | table key `name` | the vocabulary every binding, bundle, `exec_in`, `image_from`, `pki`, `vault.service`, lane `requires.healthy` uses | consumer / everything | `[service.main_db]` |
| `description` | `service.<n>` | str, optional | human context | consumer / humans | — |
| `live` / `seeded` / `simulated` | `service.<n>.<level>` | str → realization, or table `{ realized_by, service }` | one variant per level; `service` says which service of a multi-service stack carries THIS capability (default: the primary) | consumer (instance file for joins) / resolver, graph, resolutions, gate | `live = { realized_by = "db_core", service = "minio" }` |
| `mock` | `service.<n>.mock` | `{}` | an in-process double is a legal selection; no Realization, no resolution, no edge | consumer / resolver | `mock = {}` |
| *(contract)* | — | — | **derived** from bindings (R-19): endpoints bound + `facts` declared by consumers; every variant is checked against it | ciu / stage 5 | — |

#### A3 `[realization.<n>]` (V8-S5.4)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `realization` | table key `name`; `hosts`/`ciu` reserved | identity component 3 | consumer (instance file for `joined`) / identity, graph | `db_core = { … }` |
| `kind` | `realization.<n>` | `ciu_stack\|external\|joined` | selects the per-kind keys and deploy behaviour (P7) | consumer / ciu | `"ciu_stack"` |
| `location` | `ciu_stack` | repo-relative dir, unique | binds the stack file to its name once | consumer / loader | `"infra/db-core"` |
| `per_host` | `ciu_stack` | bool | a daemon on every host whose bundles include it; never a binding target with data | consumer / placement, edges | `true` |
| `provides` | `external`, `joined` | list[TypedFact] | facts ciu cannot derive because it does not build the thing | consumer / conformance | `["pg:db/dstdns"]` |
| `instance` / `service` | `joined` | label or abs path / LogicalService | which instance's which capability is joined | operator or `ciu instance add` / join | `"primary"` / `"vault"` |
| `endpoints.<e>.url` / `.tls` / `.ca` | `external` | URL / `none\|tls\|mtls` / path | the address and transport facts of something ciu does not run | consumer / resolutions | `url = "https://api.stripe.com"` |
| `services.<svc>.…` / `secrets.…` / `hooks` / `governance` | `realization.<n>` in `ciu.site.toml` / `ciu.instance.toml` only | as E | site/instance override of a stack table by its merged path (the one override mechanism, R-10) | operator / merge | `[realization.consul_server.services.consul.endpoints.http] publish = "host"` |

#### A4 `[network.<n>]` (V8-S7.3)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `kind` | `network.<n>` | `address\|proxy` | address plane (hosts have addresses) vs FQDN-reached proxy | consumer / resolution | `"address"` |
| `realized_by` | `network.<n>` | realization, optional (required for `proxy`) | transport readiness: what must be up before resolutions over this network work | consumer / derived edges | `"tailscale_node"` |
| `tls` / `pki` | `network.<n>` | `none\|tls\|mtls` / LogicalService (required when `tls ≠ none`) | transport security is a link property inherited by every resolution; whose hook issues certificates | consumer / resolutions, derived TLS secrets | `"mtls"` / `"vault"` |
| `fqdn` | `proxy` | hostname | the public name proxied resolutions resolve to | consumer / resolutions | `"gstammtisch.dchive.de"` |
| `description` | `network.<n>` | str, optional | what the plane is; **no semantics** | consumer / humans | `"tailscale mesh"` |

#### A5 `[bundles.<b>]`, `[layouts.<l>]`, `[realness]` (V8-S7.5, S7.6, S9.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `services` / `includes` | `bundles.<b>` | list[LogicalService] / list[bundle] (acyclic) | a bundle = which capabilities deploy together; `includes` composes bundles so `all` need not restate eighteen names (R-67) | consumer / deploy set | `includes = ["core", "db", "apps"]` |
| `compose_profiles` / `compose_env.<VAR>` | `bundles.<b>` | list / str | compose-level activation and env the bundle needs; conflicts across selected bundles refuse | consumer / compose env | — |
| `environment` | `layouts.<l>` | str, optional, free-form | bound as `instance.environment`; ciu attaches no semantics (R-69) | consumer / templates, hooks | `"prod"` |
| `hosts.<h>.bundles` / `.reach` | `layouts.<l>.hosts.<h>` | list[bundle] / list[network] (non-empty; `instance` = this host only) | placement (order = push order); which networks reach the others, in preference order | consumer / placement, resolutions, push | `reach = ["mesh", "public"]` |
| `default` / `pin.<logical>` | `realness` | level / level | the level used when nothing more specific selects one (required; `ciu init` writes `live`); committed per-service overrides | consumer / resolver | `probe_targets = "simulated"` |

#### A6 `[vault]`, `[registry]`, `[governance]` (V8-S10.3, S3.4.6, S13)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `service` / `token_file` | `vault` | LogicalService / path | which Vault ciu's own client talks to (replaces the stack-path heuristic); token source #2 | consumer / secrets, edges | `"vault"` |
| `paths.<name>` | `vault.paths` | literal KV path | the **checked reference table** secret `path` keys resolve against (R-30) | consumer / secrets | `postgres_controller_password = "db/postgres/controller_password"` |
| `postgresql.database` | `registry.postgresql` | str | the app database for `pg:schema/*` probes | consumer / probes | `"dstdns"` |
| `<anything else>` | `registry` | table | project metadata validated only by `ciu.registry_validator` | consumer / consumer validator | — |
| `enabled` / `cgroup_parent` / `ksm_optin` / `exempt_services` / `memory_profile.*` | `governance` | bool / slice / `builtin\|path` / list / tables | governance switches, unchanged | consumer / governance | — |
| `memory_max` / `memory_swap_max` / `memory_high` / `memory_low` / `memory_min` / `cpu_weight` / `cpu_max` / `io_weight` / `pids_max` | `governance` (and per-stack) | the **shared resource key set** `RK` | each is the cgroup-v2 file it writes; `cpu_max` maps to compose `cpus` (CIU-90's key) | consumer / governance, gate | `memory_max = "2G"` |
| `io_*_max` / `device` / `baseline_path` | `governance` | int / str / path | device-level I/O caps only stacks have | consumer / governance | — |

#### A7 `[testing.*]` — the gate (V8-S16)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `inherit` | `testing` | path, optional | environments of another project's `ciu.toml` merged under this one's (monorepo DRY, run-gate R-22); recursive and cycle-checked, paths relative to the declaring file (T-32); lanes never inherit | consumer / gate | `"../ciu.toml"` |
| `externals.<n>.url` / `.env` / `.probe` | `testing.externals.<n>` | URL / table of variable names / `tcp\|http:<path>\|none` | a typed test dependency the project does not deploy; the only binding target a zero-instance project may use (T-31) | consumer / gate | `db = { env = { host = "TEST_DB_HOST", port = "TEST_DB_PORT" } }` |
| `cgroup_slice` / `evidence_dir` / `history` | `testing` | str / path / int | the slice every lane runs in; where artifacts and verdicts land (gitignored); LaneResults kept per lane | consumer / gate | — |
| `judge.version` | `testing.judge` | version floor; required iff an assay lane exists | the ONE judge pin; provenance always required | consumer / gate | `">=2.4"` |
| `environments.<e>.mode` | `testing.environments.<e>` | `ephemeral\|exec\|host` | where a lane's process lives (`host` built-in when not declared) | consumer / gate | `"exec"` |
| `environments.<e>.exec_in` | `exec` | LogicalService | the container to exec into, by capability; identity derived; health and **mount proof** required (R-47) | consumer / gate | `"tester"` |
| `environments.<e>.image` / `.image_from` | `ephemeral` | str / LogicalService | what to run; `image_from` reuses a service's image | consumer / gate | `image_from = "tester"` |
| `environments.<e>.forward_env` / `.extra_mounts` / `.workdir` / `.enabled` | `testing.environments.<e>` | list / list / path / bool | explicit allow-list of forwarded env; extra binds (dual-mount guard); where `{worktree}` lands | consumer / gate | — |
| `environments.<e>.binds.<local>` | `testing.environments.<e>.binds` | binding with `delivery = "env"` | infrastructure facts for the lane process as variables — assay reads `required-env:`, never ciu's file (R-03) | consumer / gate | `db = { to = "main_db.sql", delivery = "env", env_prefix = "TEST_DB" }` |
| `lanes.<l>.kind` / `.environment` / `.argv` / `.assay_lane` / `.lanes` / `.stop_on` / `.description` | `testing.lanes.<l>` | `command\|assay\|sequence` / env / list / assay lane / list[lane] / `FAIL\|never` / str | who produces the outcome; where; the command; the judge lane (invocation derived; `base_source` from `assay lanes --json`); sequence members (in-process, R-53) | consumer / gate, stage 12 | `kind = "sequence"` |
| `lanes.<l>.clean_tree` / `.budget` / `.required_env` / `.artifacts` / `.enabled` | `testing.lanes.<l>` | bool / duration / list / list / bool | evidence integrity; wall cap; must-have env; outputs | consumer / gate | `budget = "30m"` |
| `lanes.<l>.requires.realness` / `.healthy` | `testing.lanes.<l>.requires` | table logical → level / list[LogicalService] | preconditions from the record and the graph → `NOT_RUN/realness-mismatch` / `service-down` | consumer / gate | — |
| `lanes.<l>.require_provenance` | `testing.lanes.<l>` | bool | running images must match `HEAD` → `NOT_RUN/provenance-mismatch` (R-55); always recorded | consumer / gate | `true` |
| `lanes.<l>.resources.<RK>` / `.shared` | `testing.lanes.<l>.resources` | `RK` subset / list[LogicalService] | per-lane cgroup caps (admission by headroom); exclusive use of realizations | consumer / gate | — |
| *(request_base, central lanes, container_name, pins, assay_command)* | — | — | none: derived from `assay lanes --json`; environments inherit, lanes never; `exec_in`; the judge floor; the invocation is ciu's | — | — |

### B. Instance file (`ciu.instance.toml`, operator) and generated file (`ciu.instance.generated.toml`, ciu) — both gitignored, per instance (V8-S14.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `layout` / `bundles` / `label` | `ciu.instance` | layout / list[bundle] / str (unique per git family) | which placement this instance deploys; default bundle selection; a human name (never an identity; the join reference, R-27) | operator or `ciu instance init/add` / `ciu up`, listings, registry | `layout = "local"` |
| `host_ports."<realization>.<svc>.<endpoint>"` | `ciu.instance.host_ports` | int | per-instance host-port override so two instances on one machine can both publish | operator / publication | `"cadvisor.cadvisor.http" = 18080` |
| `[realization.<n>] kind = "joined" …`, `[service.<n>] <level> = "<n>"` | instance file | see A3, A2 | joins are instance-scoped declarations, appended by `ciu instance add --join` with a round-trip writer (R-06) | operator or ciu / join | see §4.1.7 |
| `[realization.<R>.services.<svc>.…]` | instance file | as E | per-instance override of a stack table by its merged path | operator / merge | — |
| `generated.instance_id` | `ciu.instance.generated` | str hex | identity component 2; identical on every host of a layout | ciu / everything | `"98535c"` |
| `host.generated.name` / `.hostname` / `.repo_root` / `.physical_repo_root` / `.env_type` / `.user_uid` / `.user_gid` / `.docker_gid` | `ciu.host.generated` | host / str / path / path / `devcontainer\|native\|github-actions` / int ×3 | host-local facts templates and hooks read as `host.*`; `hostname` is the lease holder (R-45); regenerated per host, never part of an identity | ciu / templates, hooks, engine | `env_type = "devcontainer"` |
| `build.build_version` / `.build_time` / `.images.<name>` | `ciu.instance.build` | str / datetime / digest | what `ciu build` produced | `ciu build` / templates, provenance | — |
| `realness.<layout>.<logical>` | `ciu.instance.realness.<layout>` | level | the durable record per layout — a **constraint** on later selections (R-26); other layouts' records stripped on push | ciu at first `up` / resolver, gate | `main_db = "seeded"` |
| *(public_fqdn)* | — | — | none: `fqdn` is declared per host (R-16) and read as `host.fqdn` | — | — |

### C. Rendered, derived, read-only (`ciu.resolved.toml` `[resolved]`) — a consumer writing any of these is refused (V8-S3.7)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `api` / `api_version` / `layout` / `host` / `environment` / `rendered_at` | `resolved` | `"ciu/resolved"` / int / str / str / str / datetime | names the artifact's schema and its own version (each artifact versions independently, T-33); what this render resolved for | ciu / assay, scripts, gate | `api_version = 1` |
| `capabilities.<logical>.level` / `.realization` / `.service` / `.container_name` / `.endpoints` | `resolved.capabilities` | … | the index for readers that start from a capability (`ciu query capability`, T-35) | ciu / scripts | — |
| `identities.<r>.<svc>.container_name` / `.hostname` / `.compose_key` / `.compose_project` / `.network` / `.replicas[]` | `resolved.identities` | str… | the one identity derivation, materialized (P6) | ciu / templates, hooks, gate | see §4.1.4 |
| `identities.<r>.<svc>.endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.path` / `.publications[]` | `resolved.identities…endpoints` | as declared + list of socket claims `{ scope, network?, bind, port, protocol }` | the endpoint facts joined instances and the gate read; `publications` = every declared or derived socket the layout made ciu publish (the unit of the collision check, T-26) | ciu / joins, gate, humans | `{ scope = "network", network = "mesh", bind = "100.64.0.11", port = 5432, protocol = "tcp" }` |
| `bindings.<consumer>.<local>.service` / `.realization` / `.endpoint` / `.network` / `.host` / `.port` / `.url` / `.path` / `.tls` / `.cert` / `.key` / `.ca` / `.requires` / `.delivery` / `.variables` | `resolved.bindings` | str… / list | how each binding was satisfied (§4.1.5); `<consumer>` = `<realization>.<svc>`, `env.<e>`, or `ciu` | ciu / templates (template delivery), injection (env delivery), probes, gate, assay `derived:` | see §4.1.5 |
| `networks.<n>.name` / `.kind` / `.realized_by` / `.fqdn` / `.tls` | `resolved.networks` | str… | every declared network plus the implicit `instance` one | ciu / templates | `name = "dstdns-98535c-network"` |
| `services.<logical>.level` / `.realization` / `.service` | `resolved.services` | level / realization / svc (absent for mock) | the selection actually used | ciu / gate, templates, joins | `"live"` / `"db_core"` |
| `placement.<r>.hosts` | `resolved.placement` | list[host] | placement result | ciu / resolutions | `["gstammtisch"]` |
| `waves` / `edges[]` / `gates.<k>.healthy` / `.completed` / `.facts` | `resolved` | list[list] / list of `{from,to,kind}` / lists | the ordering ciu used and every edge incl. derived (`kind ∈ bind\|depends\|secret→vault\|secret→minter\|network\|pki`) | ciu / `check --graph`, gate | see §4.1.6 |
| `governance.<r>.<svc>.*` | `resolved.governance` | `RK` | effective caps per container | ciu / humans, gate | — |
| `bundle.<host>.…` | `resolved.bundle` (with `--layout`) | table | what `ciu push` ships per host and why each store entry travels (R-32) | ciu / humans, push | — |

### D. Host inventory (`ciu.hosts.toml`, gitignored; `~/.config/ciu/hosts.toml` user-global) (V8-S7.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `local` | `hosts.<h>` | bool | marks this machine; no SSH facts required; no built-in `localhost` (written by `ciu init`) | operator / placement | `true` |
| `fqdn` | `hosts.<h>` | hostname, optional | the host's declared public name (`host.fqdn`; `require_fqdn`) | operator / templates, init | `"gstammtisch.dchive.de"` |
| `ssh_host` / `ssh_user` / `ssh_port` / `ssh_key` / `known_host` | `hosts.<h>` | str / str (`root`) / int (22) / path / str | push transport facts; `known_host` absence refused unless `CIU_SSH_INSECURE_TOFU=1` | operator / push, ssh | — |
| `bundle_dir` / `push_mode` / `bundle_excludes` / `docker_optional` | `hosts.<h>` | str / `auto\|rsync\|scp` / list / bool | bundle mechanics | operator / push | — |
| `activate.bootstrap` / `.apply` / `.health` / `.rollback` | `hosts.<h>.activate` | str | per-verb remote commands | operator / activate | `"ciu up --layout prod3"` |
| `secrets.<entry>` | `hosts.<h>.secrets` | table `{ from = ask\|generate\|file, … }` | host-scoped secrets, stored under `[secrets.hosts.<h>]`, consumed through `from = "host"` | operator / hosts, secrets | `tls_cert_pem = { from = "file", path = "/etc/ssl/edge.pem" }` |
| `addresses.<network>` | `hosts.<h>.addresses` | str | the host's address on each addressed network; the input to every cross-host resolution | operator / resolution | `mesh = "100.64.0.12"` |

### E. Stack file (`<location>/ciu.stack.toml`) (V8-S6, S10.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<svc>` | `ciu_stack` | table key `name`, not `secrets` | one RealizedService; identity component 4 | consumer / identity, compose | `[ciu_stack.postgres]` |
| `image` / `instances` / `one_shot` / `primary` / `enabled` | `ciu_stack.<svc>` | str / int ≥ 1 / bool / bool / bool or flag | the one image declaration (pulled unless `build` is declared); replica fan-out; runs-to-completion; the default variant service; conditional inclusion | consumer / compose, graph, gate | — |
| `build.context` / `.dockerfile` / `.args` / `.target` | `ciu_stack.<svc>.build` | dir / path / table / str | the service's image is project-built by `ciu build` from this context (ownership declared next to the image, T-29); the compose `build:` stanza is injected | consumer / build, injection, provenance | `build = { context = "../..", dockerfile = "applications/controller/Dockerfile" }` |
| `requires` | `ciu_stack.<svc>` | list[LogicalService] | sugar for bindings without data: an ordering edge (replaces `init_requires` on empty contracts and `after`) | consumer / graph | `["app_schema"]` |
| `binds.<local>.to` / `.wait` / `.delivery` / `.env_prefix` / `.facts` / `.enabled` | `ciu_stack.<svc>.binds.<local>` | target / `healthy\|started\|none` / `env\|template\|none` / envname / list[TypedFact] / bool | §4.1.5: the consumer's dependency under its own name; delivered like a secret; its `facts` form the target's contract | consumer / graph, resolution, injection, templates | see §4.1.5 |
| `provides` | `ciu_stack.<svc>` | list[TypedFact] | facts this service creates by means other than a vault-stored generated secret (replaces `init_provides`) | consumer / conformance, probes | `["pg:role/controller"]` |
| `depends_on` / `probe_user` / `aliases` / `host_network` | `ciu_stack.<svc>` | list[sibling] / str / list / bool | intra-stack start order; the superuser probes use; extra DNS names; `network_mode: host` | consumer / compose, probes | — |
| `health.interval` / `.timeout` / `.retries` / `.start_period` / `.gate_timeout` | `ciu_stack.<svc>.health` | durations / int | per-service healthcheck parameters (merged from `project.health`, **injected** into the rendered `healthcheck`, R-25) and the wave-gate budget | consumer / injection, gate | `start_period = "240s"` |
| `endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.host_bind` / `.allow_from` / `.path` | `ciu_stack.<svc>.endpoints.<e>` | int / `tcp\|udp\|http\|https` / `instance\|host\|proxy` / int / IP / list / str | §4.1.5; names unique per stack; publication derived from bindings with data | consumer / resolution, ports injection | `sql = { port = 5432, allow_from = ["network.mesh"] }` |
| `hostdir.<purpose>` / `.path` / `.uid` / `.mode` / `.seed` | `ciu_stack.<svc>.hostdir.<purpose>` | str (`""` = auto) / path / int / str / path | host directories; `vol-*` legacy directories refused until migrated (R-36) | consumer / engine | `data = ""` |
| `configfile.<n>.template` / `.target` / `.mode` / `.schema` | `ciu_stack.<svc>.configfile.<n>` | path / abs path / str / path | rendered into `ciu.rendered/<svc>/<mirrored path>`, mounted by parent directory (R-35) | consumer / injection | `template = "config.toml.j2"` |
| `secrets.<k>.from` / `.path` / `.field` / `.store` / `.var` / `.entry` / `.length` / `.charset` / `.delivery` / `.env_name` / `.mode` / `.uid` / `.enabled` | `ciu_stack.<svc>.secrets.<k>` and `ciu_stack.secrets.<k>` | `vault\|generate\|ask\|file\|host\|ephemeral` / paths key or literal / str / `local\|vault` / envname / str / int / str / `file\|env\|configfile\|native\|hook\|none` (**required**) / envname / str / int / bool | §4.1.8; structured, checked | consumer / secrets, compose | see §4.1.8 |
| `<consumer sub-tables>` | `ciu_stack.<svc>.<x>` | table | free-form service data; never read by ciu; not named `identity`/`health`/`endpoints`/`binds`/`hostdir`/`configfile`/`secrets` | consumer / templates | `[ciu_stack.controller.workflow]` |
| `pre_secrets` / `pre_compose` / `post_compose` | `hooks` | lists of path or `{ run, provides, service }` | lifecycle hooks (subprocess, JSON context v2, `--validate`); `provides` on the entry names the facts the script creates (replaces `[hooks.provides.<svc>]`, R-20) | consumer / hooks_runner | `post_compose = [{ run = "post_compose_vault.py", provides = ["vault:secret/vault/controller/role_id"] }]` |
| `*` | `governance` (stack-level) | as A6 | per-stack override | consumer / governance | — |
| *(state)* | `<location>/ciu.state.toml` | any non-secret | hook-persisted state; visible to the same run's later steps | ciu from hook outputs / hooks, templates | `initialized = true` |
| *(init_requires, uses, after, init_provides, `[hooks.provides]`, directive, consumed_by, produced_by)* | — | — | refused with a message naming the v8 form | — | — |

### F. Secrets file (`ciu.secrets.toml`, gitignored, CIU-owned, atomic) (V8-S10.6)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `value` / `source` / `created` | `secrets.<realization>.<svc>.<key>`, `secrets.<realization>.<key>`, `secrets.hosts.<h>.<entry>` | str / `<from>[:<path\|var\|entry>]` or `hook:<script>` / datetime | the materialized secret in one store; which source produced it | ciu / ciu, humans | `source = "generate:vault:db/postgres/admin"` |
| `secrets.<vault realization>.<primary>.root_token` / `.unseal_keys` | same shape | str / list | Vault bootstrap state, keyed by the resolved Vault realization; a joiner reads the reference's (R-28) | vault hook via outputs / ciu | — |

### G. assay lane TOML (`assay.toml`, owned by assay; never parsed by ciu)

Unchanged from revision 2.1's table G in content; the two v8-facing facts are: `judge.base_source = "request"` is read by ciu **through `assay lanes --json`** (never the file), and `[lanes.<n>.infrastructure]` facts should be `required-env:<VAR>` fed by an environment's `binds` — `derived:<path>` stays supported and targets `resolved.bindings.env.<e>.<local>.*` (R-03).

### H. run-gate lane TOML → v8 home (run-gate stays available; this is the mapping for adopters that move)

| run-gate key / knob | v8 home | note |
|---|---|---|
| `schema_version` | `project.revision` | one revision gate |
| `environments.<n>.image` / `.mode` / `.forward_env` / `.cgroup_slice` | `testing.environments.<e>.image`/`image_from`, `.mode`, `.forward_env`; `testing.cgroup_slice` | unchanged names |
| `environments.<n>.container_name` (and R-14a derivation) | `exec_in` + derived identity + mount proof | retired |
| central config, reserved lane names (R-22) | `[testing] inherit` (environments only) | kept as one level of inheritance |
| `lanes.<n>.kind/environment/argv/assay_lane/description/clean_tree/budget/required_env/artifacts` | `testing.lanes.<l>.*` | unchanged; `budget` enforced |
| conjunction lanes (`ciu gate a && …`) and R-25 | `kind = "sequence"` | in-process |
| `lanes.<n>.assay_command`, `pins.*`, `memory` | derived invocation; `testing.judge.version`; `resources.memory_max` | — |
| `lanes.<n>.resources.memory/memory_swap/cpu_weight/io_weight/shared` | `resources.memory_max/memory_swap_max/cpu_weight/io_weight/shared` | cgroup vocabulary |
| history / median (R-36) | `testing.history` + `ciu gate --list` | — |
| `RUN_GATE_*` | `CIU_GATE_*` | — |
| `--worktree`, `--allow-dirty`, `--check-env`, `doctor`, `validate-pointers`, `--list`, `--dry-run` | `ciu gate …`, `ciu gate doctor`, `ciu check --gates` | — |
| `.assay/verdict-<lane>.json` | `<evidence_dir>/<lane>/verdict.json` | — |

### I. Environment variables ciu reads in v8

`CIU_EXIT_ON`, `CIU_MAX_CONCURRENT_INSTANCES`, `CIU_SECRET_<VAR>`, `VAULT_TOKEN`, `CGROUP_PARENT_DEV_BACKGROUND`, `CIU_HOSTS_FILE`, `CIU_SSH_TRANSPORT`, `CIU_SSH_INSECURE_TOFU`, `CIU_KSM`, `CIU_GOV_BASELINE_PATH`, `CIU_SKIP_DOOD_PREFLIGHT`, `CIU_GATE_EXTRA_MOUNTS`, `CIU_GATE_MOUNT_ALIAS`, `CIU_GATE_EVIDENCE_DIR`, `CIU_GATE_CGROUPFS_ROOT`, `NO_COLOR`, `TERM`, `CIU_LOG_PREFIX_TIME_SHORT`; `HOSTNAME`, `REMOTE_CONTAINERS`, `WORKSPACE_DIR`, `GITHUB_ACTIONS`, `USER` during `instance init` only. Retired in addition to revision 2.1's list: `CIU_SKIP_DEPENDENCY_CHECK` (preflights are per need, R-02).

### J. Keys retired in v8 (the full drop list is §4.8)

Revision 2.1's list (`deploy.environment_tag`, `deploy.network_name`, `[deploy.phases]`, `[topology.*]`, `[service.<n>] type/location`, stack-level `requires/provides`, `<svc>.name`, `ports`, `resources`, `ciu.repo_root`, `[deploy.resources]`, `vault.stack_path`, `expose_env`, `shared_infra`, `auto_generated.*`, run-gate `pins`/`assay_command`/`memory`/`container_name`, `$VAR` in TOML, `exec_targets`, `env_required`, `[state]`) plus, retired by revision 3.0: `deploy.labels.prefix` (R-15), `deploy.env.defaults` (R-13), `[service.<n>] contract` (R-19), `init_requires` / `uses` / `after` / `init_provides` / `[hooks.provides]` (R-18, R-20, R-21), the `routes` binding and two-pass render (R-05), secret `directive` strings / `consumed_by` / `produced_by` (R-30, R-31), `request_base` (R-52), `public_fqdn` detection (R-16), `owned-seeded` (R-29), every `.j2` declaration file (R-08), the per-stack rendered `ciu.toml` and `ciu.toml.j2` override (R-09, R-10), `[ciu.instance.resolved.render] complete` and the rendered-file lock (R-42), `CIU_SKIP_DEPENDENCY_CHECK` (R-02), `facts_schema` (R-49).

## 4.6 Spec/schema check

**For every proposed table: does it exist, and what changes?** (`S<n>` = v7 SPEC 5.0.0; `V8-S<n>` = SPEC-V8 draft.3.)

| v8 table / key | exists today as | shape / meaning / owner change |
|---|---|---|
| `project.revision` | `revision` (S3) | value 8 |
| `[project]` | `[deploy]` | renamed and narrowed to identity, registry, health defaults, compose env, control flags, vendor images |
| `[service.<n>] <level> = …`, `mock = {}` | S3.14 `[service.<n>] type/location/description` | **shape change**: variants; no `contract` (derived) |
| `[realization.<n>] kind/location/per_host/provides/endpoints/instance/service` | S3.14 `location`; S16.1a `ref_services` | one namespace across kinds; `joined` replaces generated `ref_services` |
| `[ciu_stack.<svc>] … binds.<local>, requires, provides, endpoints` | S3.3 `[<root>.<svc>]` with `requires/provides/name/instances/env_required/hostdir/configfile/secrets` | **root fixed, key set closed, bindings new**: `requires/provides` → bindings and `provides`; `name` derived; `endpoints`, `one_shot`, `primary`, `aliases`, `host_network`, `probe_user`, per-service `health` new |
| secrets `from/path/store/…`, `delivery` | S4 directive strings; S4.19 `expose_env` | **shape change**: structured; mandatory delivery axis with six values |
| `ciu.secrets.toml` | S4.9 `.ciu/secrets/<name>`; `[state]` bootstrap (S9.4) | location and shape change; per-source push rule |
| `[network.<n>]`, derived TLS secrets, `pki:issuer/<n>` | `[topology] transport` (1.10 only) | new |
| `[hosts.<h>]` incl. `fqdn`, `addresses`, structured host secrets | S14.3 `.ciu.hosts.toml` `[deploy.hosts]` | additive keys; file and table renamed; `public_fqdn` detection retired |
| `[bundles.<b>] services/includes` | S7.4 `[deploy.profiles]` | **meaning change**: bundle of logical services; composition new |
| `[layouts.<l>.hosts.<h>] reach` | S7.5c layouts | additive `reach`; mandatory; `environment` free-form |
| `[realness] default/pin`, `[ciu.instance.realness]` | — | new; the record is a constraint |
| `[resolved.*]` in `ciu.resolved.toml` | `[ciu.instance.generated]` precedent (S3.1b) | derived tables in an atomically written rendered file |
| `ciu.instance.toml` / `ciu.instance.generated.toml` | `ciu.global.instance.toml.j2` + `ciu.instance.generated.toml` (7.10.0) | instance file becomes plain TOML; generated file unchanged in role |
| `[ciu.instances] max_concurrent/lease_ttl_hours`, `ciu.instance.json` | S16.3/S16.9 `[ciu.worktree]`, `ciu.worktree-instance.json` | rename; `exec_targets` retired (gate environments) |
| `[governance]` `RK` incl. `cpu_max` → `cpus` | S15 keys incl. `cpus` (CIU-90, 7.11.0) | rename to cgroup vocabulary |
| `[testing.*]` incl. `inherit`, `binds`, `sequence`, `require_provenance`, `history` | run-gate `run-gate.toml` + `RUN_GATE_*` | **owner change** for adopters that move; run-gate stays |
| `[vault] service`, `[vault.paths]` read | S4.16 `vault.stack_path` + basename heuristic; `[vault.paths]` unread | pointer by logical service; paths become a checked reference table |
| `ciu.rendered/<svc>/…` directory mounts | S5.3a | **kept** from v7 (revision 2.x had regressed to file mounts) |
| plain-TOML declarations, `ciu schema --json` | S3 `.j2` layers | **shape change** (P11) |
| directory-fd instance lock | S4.26 per-stack secret locks; S16 registry locks | new; no rendered-file lock |
| `ciu.hookkit`, hook context v2 | S9 in-process hooks | **model change** with a helper library |
| `ciu migrate`, `ciu init` v8, `ciu doctor`, `ciu status`, `ciu show`, `ciu dev`, `ciu ssh`, `ciu provenance` | v7 verbs S19, S13.7, S7.10, S5a, S14.1, S17.2 | kept with dispositions (R-61) |

**How this schema itself is validated mechanically** — unchanged in structure from revision 2.1: (1) closed-key validators generated from one declarative table-spec (now also the source of `ciu schema --json`); (2) S5.7 JSON-schema validation of rendered *application* config files, not stretched to ciu's own referential rules; (3) hook `--validate` findings with severity through `ciu.exit_on`. Plain-TOML declarations add a fourth layer for free: any external TOML validator, editor or third-party tool can check a ciu file against the emitted JSON Schema.

## 4.11 Non-breaking improvements to the existing tools

Status of revision 2.1's items: **N1** (auto `ciu check`, CIU-64), **N2** (severity findings, CIU-65), **N3** (`stack:*` self-satisfied, CIU-63), **N4** (`gate_timeout`, default-on gate, bounded poll, CIU-67/68), **N5** (`exec_targets` key, CIU-69), **N6** (provider-resolved probes, CIU-70, and CIU-89's override table), **N7** (`StrictUndefined`, CIU-74) — **shipped** in ciu 7.x. **N8** (container-name collision WARN, CIU-66 static half), **N9** (undocumented keys), **N10** (dead keys), **N15** (vault heuristic WARN), **N16** (`ciu render --json`), **N17** (dstdns cleanups) — still open and still safe. **N11** (single assay pin in run-gate) — superseded by RG-33's judge floor. **N12** (`--base` pass-through, RG-26) and **N14** (assay wave) — shipped. **N13** (run-gate `image_from_ciu`) — dropped: run-gate's exec derivation is replaced by V8-19's read of `ciu.resolved.toml` when present.

New, safe now:

| # | mechanism | tool | why it is safe | what it improves |
|---|---|---|---|---|
| N18 | run-gate: when a checkout carries `ciu.resolved.toml`, read `resolved.identities` for exec-mode container names; otherwise the v7 path (RG item, V8-19) | run-gate | additive lookup order | run-gate keeps working against v8 checkouts |
| N19 | `ciu schema --json` for the v7 tables from the existing validators' key sets | ciu | read-only verb | editor completion for v7 adopters; the table-spec V8-1 needs anyway |
| N20 | `ciu.hookkit` shipped as a v7 package with `wait_healthy`/`wait_tcp` wrappers over the existing `hooks_runner` probes, usable from in-process hooks | ciu | additive module | dstdns hooks stop hand-rolling polls before v8 |
| N21 | run-gate: `kind = "sequence"` lanes (in-process conjunction) | run-gate | new lane kind; string conjunctions still work | removes the R-25 override hazard for adopters that stay on run-gate |

---

# Part 2 — Design Rationale & Audit Trail

## 4.2 Inventory — every mechanism and idea, tagged

Tags: **SHIPPED**, **PROPOSED**, **CONTRADICTED**, **SUPERSEDED**, **QUESTIONABLE**. Revision 2.1's inventory rows (A1–A16 identity, B1–B18 config model, C1–C13 graph, D1–D8 realness, E1–E12 topology, F1–F8 secrets, G1–G5 locking, H1–H9 validation, I1–I20 gate) stand as recorded there and in git history (`2347191f`); rows changed or added by revision 3.0:

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| B4a | `[ciu_stack.<svc>]` root in a **Jinja** stack file; two-pass render for `routes` | rev 2.1 V8-S3.5.5 | PROPOSED, QUESTIONABLE | plain TOML; no derived value in a declaration (R-05, R-08) |
| B17a | `{% set %}` DRY maps and `{% for %}` in declarations | dstdns; rev 2.1 §4.3.3 "data, fine" | SHIPPED, QUESTIONABLE | not needed once bindings and `[vault.paths]` references exist; expanded by `ciu migrate` (R-08) |
| B19 | `ciu.global.instance.toml.j2` written by `instance add --join` | rev 2.1 V8-S9.5.5 vs X38 | CONTRADICTED | X41 → plain `ciu.instance.toml` |
| B20 | `{}` disables a table | rev 2.1 V8-S3.1.2 | PROPOSED (false under deep merge) | X42 → `enabled = false` only |
| C14 | `init_requires`/`uses`/`after` + template `routes.<X>` | rev 2.1 | PROPOSED, QUESTIONABLE | bindings (R-18) |
| C15 | hand-typed `contract` | rev 2.1 V8-S5.2 | PROPOSED, QUESTIONABLE | derived from bindings (R-19) |
| C16 | `[hooks.provides.<svc>]` next to `init_provides` | rev 2.1 | PROPOSED | `provides` on the hook entry (R-20) |
| C17 | ordering-only dependency publishes an endpoint | rev 2.1 S7.4.1 + S7.8.3 | PROPOSED (hazard) | publication only from bindings with data (R-22) |
| D9 | record in the selection precedence chain | rev 2.1 S9.3.1 vs S9.4.2 | CONTRADICTED | X43 → record as constraint |
| E13 | `public_fqdn` from a "public"-described address | rev 2.1 S14.2 | CONTRADICTED with S7.3 | X44 → declared `fqdn` |
| E14 | label prefix from the consumer | rev 2.1 S4.5 | PROPOSED, QUESTIONABLE | fixed `ciu.*` (R-15) |
| F9 | directive string grammar + Jinja paths + unread `[vault.paths]` | rev 2.1 S10.1 | PROPOSED, QUESTIONABLE | structured sources (R-30) |
| F10 | `consumed_by`, `produced_by` | rev 2.1 S10.2 | PROPOSED | `delivery = "hook"`; derived (R-31) |
| F11 | reduced store ships every push | rev 2.1 S17.3 vs v7 S14.2 | CONTRADICTED | X45 → per-source rule |
| G6 | rendered file as lock; in-place render; completion table; fstat retry | rev 2.1 S14.4 (operator F14) | PROPOSED, QUESTIONABLE | X46 → directory fd (interview Q2) |
| G7 | shared gate lock for nested conjunctions | rev 2.1 X27 | PROPOSED | kept for `up ‖ gate`; nesting removed by sequence lanes |
| H10 | 15 stages strictly serial | rev 2.1 S15.3 | PROPOSED | dependency-based (R-71) |
| H11 | `facts_schema` vs `schema_version` | rev 2.1 | PROPOSED | one envelope (R-49) |
| I21 | gate needs an instance even with zero stacks | rev 2.1 S14.4.1 vs S16.11 | CONTRADICTED | X47 → zero-instance mode |
| I22 | `request_base` on ciu lanes | rev 2.1 S16.5 (own CIU-72 note) | PROPOSED, QUESTIONABLE | `assay lanes --json` only (R-52) |
| I23 | central `[testing]` inheritance dropped | rev 2.1 §4.5 H | PROPOSED, QUESTIONABLE | `inherit` (R-54) |
| I24 | shell-string conjunction lanes | dstdns, demo | SHIPPED (hazard) | `sequence` (R-53) |
| I25 | provenance as a gate precondition | v7 S17.2 | SHIPPED (v7), missing in v8 | `require_provenance` (R-55) |
| I26 | exec-target mount proof | v7 S16.7 | SHIPPED (v7), missing in v8 | restored (R-47) |
| J1 | run-gate absorbed and frozen | rev 2.1 §4.3.2 | PROPOSED, CONTRADICTED by the standalone constraint | X48 → lifted, run-gate alive |
| J2 | subprocess hooks without helpers | rev 2.1 X39 | PROPOSED, QUESTIONABLE | hookkit (R-40) |
| J3 | file-level config-file mounts | rev 2.1 S6.9 vs v7 S5.3a | SHIPPED (v7) regressed | directory mounts (R-35) |
| J4 | hostdir path change without migration | rev 2.1 S6.8 | PROPOSED (data hazard) | `ciu migrate --hostdirs` (R-36) |
| J5 | v7 verbs without a disposition | rev 2.1 S18 | PROPOSED (gap) | R-61 |
| J6 | built-in `localhost`/`local` | review option | PROPOSED | rejected by the operator (Q10); `ciu init` writes them |
| J7 | binding-carried credentials | review idea | PROPOSED | deferred (§4.3a C) |

## 4.3 Elongated reasoning — the integrated design, walked through

### 4.3.1 What the interviews decided

**2026-08-30 (five rounds, revision 2.x)** — recorded in full in revision 2.1 §4.3.1 (git `2347191f`); in short: absorb run-gate (F1); identity as data (F2); network entities (F3); `joined` kind (F4); explicit layouts always; `[ciu_stack.<svc>]` root (F18/F18b); generic registry (F18c); phases dropped and waves written (F6); `delivery` mandatory (F5); cgroup keys (F7); image-baked judge floor (F8); rendered-file lock (F14 — **superseded 2026-09-02**); one endpoint shape (F11); `ciu gate` + `ciu instance`; flat rendered artifacts; overlay renamed.

**2026-09-02 (three rounds, revision 3.0)** — the review put ten forks to the operator; every question carried a recommendation and the options not chosen are in the review §4:

| fork | decided | the operator's reasoning, in short |
|---|---|---|
| Q1 standalone vs absorption | **lift run-gate's functionality into ciu; keep run-gate alive in parallel** | "for max synergy … possibly align with future changes in ciu v8" — the standalone constraint is satisfied by run-gate staying, and ciu owes a zero-ceremony mode |
| Q2 lock object | **checkout directory fd** | five mechanisms and a hole vs none |
| Q3 hook model | **subprocess + `ciu.hookkit`** | portability plus helpers |
| Q4/Q5 `routes` | **"do we want to keep `routes` at all? … think out of the box"** → **bindings + data-only declarations** | the operator asked for the cleanest schema and accepted breaking changes for it |
| Q6 renames | **all**: bundles, seeded, `[project]` + top-level tables, the new file names | — |
| Q7 ceremony | **all**, with a doubt on built-ins | "not sure about implicit localhost, if it makes schema harder to understand and use if you want remote hosts as well" |
| Q8/Q9 push secrets | question back ("is there a contradiction … there could be no vault … how is remote different from localhost?") → **derived per source + reachability** | the rule is per source, not per layout (§4.3.8a) |
| Q10 built-ins | **none; `ciu init` writes the host and layout** | explicit, checkable, consistent with "explicit always" for layouts |

Non-convergence: none.

**2026-09-03 (third-party review round, revision 3.1)** — two forks were put to the operator; the rest of T-01..T-35 was the author's to decide (§4.3.13, response document §1):

| fork | decided | the operator's reasoning, in short |
|---|---|---|
| authoritative instance state: registry under the git common dir with UUID identity (recommended) · keep in-checkout files (v7 posture) · hybrid | **keep in-checkout files** | visible files and v7 continuity; the local fixes (ownership labels, proven physical path, ordered locks, acyclic joins, directory gate locks, backup/restore) close the concrete failures |
| activation: manifest releases + receipts (recommended) · keep rsync-in-place · releases only | **manifest releases + receipts** | an interrupted transfer must not produce a mixed tree; rollback must name a real prior release; a remote fact must be backed by evidence |

### 4.3.2 The gate layer: absorb, and stay standalone

Revision 2.1 measured the adopter population (one real environment user, eight trivial files) and concluded that absorbing run-gate cost nothing. The operator's framing on 2026-09-02 added a constraint the measurement did not weigh: the tools are meant for third parties too, and a five-line host lane must not require a Python package with three runtime dependencies, an instance, a lock and a rendered file. Two things follow. First, run-gate is not frozen: it stays the zero-install, copied-script gate for whoever wants one, reads the same `assay.toml`, and gains what makes it interoperate with v8 checkouts (V8-19, N18, N21). Second, `ciu gate` must be usable at the same cost: a project with no Realizations has no instance at all — no `ciu.instance.toml`, no generated file, no rendered file, no lock — and ciu checks for docker, assay, git and Vault only where a verb or a lane actually needs them (R-02, R-51). The synergy the operator wanted is unchanged: an `exec` environment names a LogicalService, preconditions come from the record and the graph in process, caps use the governance vocabulary and code path, the judge is one floor plus the verdict's provenance, and — new in revision 3.0 — environments carry bindings so a lane's infrastructure facts arrive as environment variables (R-03), sequences run in one process (R-53), the monorepo declares its tester image once (R-54), and every LaneResult says whether the containers it tested match `HEAD` (R-55).

### 4.3.3 Identity, the stack-file root, and why declarations stopped being templates

Identity is unchanged from revision 2.1 (one derivation, data in the rendered file, the operator's F2) with two corrections: the injectivity claim was false and is now an honest check (R-14), and ownership labels are ciu's own namespace so that a consumer setting can never orphan a container (R-15).

The stack-file root `[ciu_stack.<svc>]` stands (F18). What changed is the file around it. Revision 2.1's own §4.3.3 had already found that Jinja was "used as a crutch" for identity assembly and replica fan-out and left it in place for data expansion. The review followed the remaining uses to their ends: `{{ vault.paths.x }}` inside directive strings (a path reference — now `path = "x"` against a checked table), `{% set %}` constants (inlined), `{% for %}` generating near-identical `[service.x]` tables (now one line each), and `{{ routes.* }}` in stack TOML (the cause of the two-pass render). With those gone, a declaration file has no expression left in it, and every argument against Jinja in declarations becomes decisive: an external validator can read the file; `ciu schema --json` can describe it; `ciu instance add` and `ciu migrate` can rewrite it round-trip; a typo is a schema finding, not a template error (P11).

### 4.3.4 From routes to bindings — the graph and the contract

Revision 2.1's consumer surface was `init_requires` (edge + route), `uses` (route only), `after` (edge only), and `routes.<X>.<e>.*` reads in templates and stack TOML, with a hand-typed `contract` on every LogicalService. Walking the demo showed the costs: seventeen stack files read `routes` (hence the two-pass render); consumers wrote a mapping hop (`[ciu_stack.controller.database] host = "{{ routes.main_db.sql.host }}"` then `{{ ciu_stack.controller.database.host }}` in compose) to give their templates a stable local name — which is the local-name idea done by hand; an ordering-only `init_requires` derived a route and therefore a cross-host publication nobody used (R-22); and the contract was a copy of the provider's own `provides` list, restated on the logical table, with the vault facts derivable from directives anyway (R-19).

The binding collapses this into the shape secrets already have: a consumer declares *what it needs* under *its own name* and *how it wants it delivered*. `to` names the capability (and optionally the endpoint); `wait` says whether the dependency orders the deploy (`healthy`, `started`) or is runtime-only (`none`, the old `uses`); `delivery` says whether ciu injects `PREFIX_HOST/PORT/URL` into the container (`env`) or binds the resolution for the templates (`template`) or nothing (`none`); `facts` says what the consumer relies on. `requires = [...]` is the sugar for bindings without data (the old `init_requires` on an empty contract, and `after`). The contract of a capability is then the union of what is bound to it — the only definition under which a check compares something a consumer actually depends on — and every declared variant is checked against it whether or not it is selected, so a seeded stub that lacks an endpoint fails `ciu check` today rather than on the first `--realness` switch. Providers still declare `provides` (facts by means other than a vault-stored generated secret) and hook entries carry their own `provides` (R-20); minter edges are derived as before. Publication now follows bindings with data only.

What was **not** adopted: stable DNS aliases by construction (the consumer hard-codes `main_db:5432` and ciu makes it true) — it cannot express external providers on non-standard ports or cross-host published ports without a local proxy; and binding-carried credentials (§4.3a C).

### 4.3.5 Realness, immutability, and the join

Unchanged in mechanism (revision 2.1 §4.3.5), corrected in three places: the record is a constraint, not a source in the precedence chain (R-26 — revision 2.1's S9.3.1 would have let a changed pin be silently overridden by the record while S9.4.2 promised an ERROR); joins name a unique label or an absolute path (R-27 — basename resolution was a third form whose meaning changed when a checkout was renamed); the joined-vault token path is written down (R-28). `owned-seeded` became `seeded`.

### 4.3.6 Topology — unchanged model, two corrections

The entity walk of revision 2.1 §4.3.6 (ten scenarios plus four additions) holds for bindings unchanged, because a binding with an endpoint resolves exactly as a route did. Corrections: `fqdn` is declared per host (R-16 — deriving it from a "public"-described address gave `description` the semantics S7.3 promised it would never have); a `per_host` capability may be `requires`'d but not bound with data (the old "no route to it").

### 4.3.7 Locking

Revision 2.1's lock discussion turned on `flock` binding an inode: tracked files are replaced by git, the overlay by ciu's atomic writer, so the rendered file — rendered in place — was chosen. The review counted what that choice cost: in-place rendering (never atomic), a completion table that must be the last table of the file, torn-file detection in every reader, an `fstat`/`stat` retry after acquisition, `clean` truncating instead of unlinking, and still an undetectable fork of the mutex when `git clean -x` unlinks the file (gap 4). The checkout directory has the property the design was looking for — an inode that is stable for the life of the checkout — without any of it: the rendered file goes back to temp-file-and-rename and is always complete or absent; readers need no marker; nothing git or ciu does to files touches the lock. The operator chose it (Q2). The shared class for the gate stays for the one interleaving that matters (`up` recreating a container under an `exec` lane); the nested-gate reason for it disappeared with sequence lanes.

### 4.3.8 The gate in detail

Unchanged from revision 2.1 §4.3.8 (judge floor + provenance, cgroup vocabulary, assay's role) with the revision 3.0 additions of §4.3.2. One interface to assay: `assay lanes --json` (B044, shipped in assay 4.x) yields lane names, `base_source`, `external_tools`, `argv0`, `env_required`; ciu neither parses `assay.toml` nor keeps a `request_base` key that could disagree with it (R-52). Provenance: v7's `ciu provenance` was a test-time question ("does this passing run describe the code I think it does?") that the gate can now answer per lane.

#### 4.3.8a Push secrets — the rule is per source

The operator's question back ("there could be no vault … how is remote different from localhost?") is the right frame. Local and remote differ in exactly one thing: where `ciu.secrets.toml` is. On localhost `up` has it next to it. A remote `up` runs from a bundle, so whatever only the sender knows must travel — generated local values (they must agree across hosts), asked values (there is no operator on the target), file values read on the sender. A `host` entry is read on the target by definition. A `vault` value lives in Vault, so the only question is *who fetches*: the target, if it has a derived resolution to the `vault` LogicalService (it is in the deploy set like any capability); otherwise the sender pre-fetches and ships. A project without Vault therefore ships its whole reduced store; a project whose Vault is reachable from every host ships local-source entries only. Neither v7's fixed "target fetches" nor revision 2.1's fixed "sender ships everything" covers both projects; the derived rule does, and `ciu check --layout` prints it per host so nothing travels invisibly (P3).

### 4.3.9 Naming decisions taken without a fork

`binds` / `to` / `wait` / `delivery` / `env_prefix` / `facts` for the binding keys; `from` / `store` / `path` / `var` / `entry` for secret sources; `requires.healthy` on lanes (was `requires.services`, one fewer sense of "service"); `[hosts]` (was `[deploy.hosts]`); `[resolved]` (was `[ciu.instance.resolved]`); `ciu.secret-copy.*`; `ciu.rendered/`; `schema_version` everywhere. Listed in §4.9 so they can be overturned cheaply.

### 4.3.10 Defects filed upstream during this pass

None in ciu's v7 code — the review was of the design set. One run-gate item is filed (exec-mode container derivation must read `ciu.resolved.toml` when present, V8-19/N18); one ciu backlog pointer records the design-set revision.

### 4.3.11 The revision 2.0 → 2.1 review rounds

Recorded in revision 2.1 §4.3.11 (git `2347191f`): the locking classes, derived vault facts and minter edges, proxy networks address-free, derived TLS secrets, the generated-file split, `primary`/variant service, `mock = {}`, derived publication, `per_host`, `healthy` defined once, `exec` caps validated, the last-table completion marker (now retired with the lock), `uses` (now a binding with `wait = "none"`), `ASK_HOST` (now `from = "host"`), `ciu.state.toml`, hooks as subprocesses (now with hookkit), `request_base` (now derived), two-pass render (now retired), `_` → `-`. Every item that revision 3.0 retired is listed in §4.8 with its reason.

### 4.3.12 The 2026-09-02 adversarial review (revision 2.1 → 3.0)

A fresh reviewer read the design set against the v7 specification, run-gate's specification and every adopter, the backlog, and a source-level dependency map, and returned 78 findings (`CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`): 5 blockers (R-01 the standalone constraint, R-06 the Jinja overlay written by ciu, R-26 the realness precedence contradiction, R-51 zero-stack mode needing an instance, and R-14's false structural claim counted as major), 22 major, the rest minor or notes. The operator was interviewed on the ten forks of §4.3.1. Everything accepted is in Part 1; three findings were resolved by keeping the status quo (R-33 store blast radius, R-46 the registry record's duplicated `instance_id`, R-68 the residual senses of "service"); one idea was deferred (§4.3a C). Because the accepted findings change the file set, the consumer surface (bindings), the secret grammar, the contract, the lock and the gate's posture, revision 3.0 and draft.3 are fresh texts rather than patches, and this section plus §4.7 X41–X56 are the trail from 2.1.

### 4.3.13 The independent third-party review (revision 3.0 → 3.1)

An independent reviewer with no history in the estate read draft.3, rev 3.0, the first review, the graph note and the demo against v7, run-gate's specification and the estate doctrine, derived prod3's closure, waves and publication table by hand, wrote the minimal project from the spec alone, and returned 35 findings with the verdict "not implementable as written" plus seven alternative designs. The author verified every finding against the text (response document §1: 31 hold as stated, 3 in part, 1 documentation) and accepted all 35 in some form. Two findings challenged operator decisions and went to the operator (§4.3.1); five reopened resolutions of the first review round (R-24, R-42, R-46, R-49, R-56 — response §4).

What the round changed, in order of weight: **(1)** deployment became transactional and evidence-bearing — manifested releases with an atomic `current` switch and a real rollback, a secrets capsule with explicit transport semantics, and receipts that carry a host's passed facts to the next host (T-09, T-16, T-25); **(2)** eleven rules that two conforming implementations would have read differently were fixed — compose-project uniqueness, the FQDN type, the hook entry object, network providers named by capability, `started` vs `healthy` at the gate, `ciu init`'s output, the LaneResult envelope, the assay argv and judge minimum, host-network resolution, the `git clean -x` promise (T-01..T-11); **(3)** the wave algorithm, the acceptance rule for selected leaves, socket-claim collisions, cgroup conversions with read-back, admission as a transaction, hook sandboxing and per-entry secrets, atomic secret directories, compose service-reference rewriting, the secret lint's honesty, image ownership on the service, per-service config-directory rules, externals, recursive inheritance, per-artifact API headers and `ciu query` (T-12..T-35). The demo had five defects of its own (tester governance below its lanes, a missing forwarded variable, a judge floor below the API the gate needs, a mesh port collision, a loopback-bound host publication) and its resolved example was hand-written; all are fixed and the example is now derived by the S8.4.1 rule.

What was **not** adopted, and why (response §3): the durable registry with UUID identity (operator decision — visible v7 posture kept, costs stated); SOPS/Vault Agent as requirements (the capsule covers transport, renewable secrets stay out of scope); a Service-Binding directory as a third delivery (no consumer yet; recorded as a gap); signed receipts (they ride the authenticated SSH channel); direct cgroup writes (Docker's adapter plus read-back first); a SQLite lock broker (ordered directory locks suffice on the supported filesystems); firewall generation from `allow_from` (CIU does not program hosts; it warns when the declaration is not consumed); renaming the entities (the operator approved the names one round earlier).

## 4.7 Contradictions found and resolved

X1–X40 are recorded in revision 2.1 §4.7 (git `2347191f`) and stand, except where a later row supersedes them (X24 by X46; X27 partially by X49; X39 by X50). New in revision 3.0:

| # | contradiction (both sides, sources) | resolution | reasoning pointer |
|---|---|---|---|
| X41 | rev 2.1 V8-S9.5.5 (`ciu instance add --join` writes the Jinja overlay) vs X38 (CIU-owned tables left the overlay because no round-trip-safe TOML+Jinja editor exists) | plain-TOML `ciu.instance.toml`, round-trip writer | R-06, §4.3.3 |
| X42 | rev 2.1 V8-S3.1.2 (`{}` disables a table) vs "tables merge recursively" (a merged `{}` is a no-op) | tables are never deleted by a layer; `enabled = false` | R-07 |
| X43 | rev 2.1 V8-S9.3.1 (record precedes pin) vs V8-S9.4.2 (a changed pin is an ERROR) | the record is a constraint | R-26 |
| X44 | rev 2.1 V8-S14.2 (`public_fqdn` = reverse DNS of the first "public"-described address) vs V8-S7.3 (`description` carries no semantics) | declared `[hosts.<h>] fqdn` | R-16 |
| X45 | v7 S14.2 (secrets resolve on the target) vs rev 2.1 V8-S17.3 (a reduced store ships every push) vs "there could be no vault" | per-source rule with reachability | R-32, §4.3.8a |
| X46 | operator F14 (rendered-file lock, rendered in place) vs the five mechanisms and gap 4 it forces | directory-fd lock (operator Q2) | R-42, §4.3.7 |
| X47 | rev 2.1 V8-S16.11 (`ciu gate` needs no `ciu up` in a zero-stack project) vs V8-S3.1.4 / S14.4.1 (overlay, generated file and a rendered file to lock are required) | zero-instance mode | R-51 |
| X48 | rev 2.1 §4.3.2 ("nobody but us") vs the operator's standalone / no-hard-dependency constraint | functionality lifted, run-gate stays | R-01, §4.3.2 |
| X49 | rev 2.1 X27 (shared gate lock class for nested `ciu gate` conjunctions) vs sequence lanes in one process | shared lock kept for `up ‖ gate` only | R-43, R-53 |
| X50 | rev 2.1 X39 (subprocess hooks) vs CIU-4 (a hook MUST NOT hand-roll a poll loop) with no helper | subprocess + `ciu.hookkit` | R-40 |
| X51 | rev 2.1 V8-S1.4 ("injective, because `name` forbids `-`") vs `db_core`+`postgres` = `db`+`core_postgres` | checked uniqueness with an ambiguity message | R-14 |
| X52 | rev 2.1 V8-S7.8.3 (a route for every `init_requires`) + S7.4.1 (publish what a route reaches) vs "explicit over magic" | publication only from bindings with data | R-22 |
| X53 | rev 2.1 §4.1.10 ("ciu never reads assay.toml beyond lane names") vs stage 12 parsing it with `tomllib` and calling `assay lanes --json`, and `request_base` restating `base_source` | `assay lanes --json` is the only interface; `request_base` dropped | R-52 |
| X54 | rev 2.1 V8-S6.9.1 (file-level bind of a rendered config file) vs v7 S5.3a (directory mounts because Docker creates a directory for a missing file) | directory mounts kept | R-35 |
| X55 | rev 2.1 V8-S18.1 (exit 2 = usage, 3 = lock) vs v7 S10.3 (2 = config validation, 3 = env bootstrap) relied on by wrappers | v7 meanings + 4 lock + 5 remote | R-48 |
| X56 | rev 2.1 §4.5 H ("no central lane config in v8") vs the vbpub monorepo's one shared `tester-unified` environment in ten files (R-22) | `[testing] inherit`, environments only | R-54 |
| X57 | draft.3 S4.2.1 (one `compose_project` per Realization) vs S4.3.1 ("every `compose_project` … unique") | uniqueness per namespace: projects among Realizations, names among services, keys/aliases on the network | T-01 |
| X58 | draft.3 S1.4 `hostname` (one label) vs S7.2/S7.3 `fqdn` typed as `hostname` and the demo's real FQDNs | `dns_name` type | T-02 |
| X59 | draft.3 S6.10 hook entry `{ run, provides }` (closed) vs its own prose "unless the entry also carries `service`" and the demo | closed entry object `{ run, service, provides, secrets }` | T-03 |
| X60 | draft.3 S7.3 `realized_by` = a Realization vs S7.3.2 "that Realization's variant service" (a Realization backing three capabilities has three) | `realized_by` names a LogicalService | T-04 |
| X61 | draft.3 S6.4/S8.2 `wait = "started"` = Running vs S8.5.1 waiting for healthy on every incoming edge | strongest predicate per edge | T-05 |
| X62 | draft.3 S19.1 (bare `init` writes realness/layout/bundle tables, no Realization) vs S16.11.1 (those tables are errors without a Realization) | bare init = zero-instance skeleton; `--stack` needs `--image`/`--from-compose` | T-06 |
| X63 | draft.3 S16.9 (LaneResult keys, no `status`) vs S18.4 ("every LaneResult" carries `status`) | `status` mapped from `outcome`; per-artifact `api` | T-07, T-33 |
| X64 | draft.3 S16.7.2 `--progress` without a path vs run-gate R-38; judge floor `>=2.4` vs `assay lanes --json` (3.2+) | path under the evidence dir; CIU's minimum judge 4.1.0 | T-08 |
| X65 | draft.3 S10.1 (`file` values are not stored) vs S17.3.1 ("the stored value travels"); transported `from = "vault"` rows vs S10.6.4 refresh on every up | the capsule with `transport:*` sources | T-09 |
| X66 | draft.3 S11.4 (no network for `host_network`) vs S7.8 step 4 (container name on the instance network) and `ports` injection on a host-network service | `host-gateway` resolution; no publication; `host_port`/`host_bind` refused | T-10 |
| X67 | draft.3 S14.4.5 ("regenerates it identically") vs S2.3.1/S3.1.4 (the deleted files are inputs) | honest S2.3.4; backup/restore; adoption by label — posture kept by the operator | T-11 |
| X68 | draft.3 S8.5.1 (gate only providers with incoming edges) + S8.5.5 (Running) vs a selected seeded/simulated leaf nobody binds | acceptance of every selected service; `verify` | T-15 |
| X69 | rev 3.0 R-24 ("serial activation closes cross-host sync") vs facts accepted on TCP reachability | receipts | T-16 |
| X70 | rev 3.0 R-42 ("directory lock: no hole") vs gate lock files in the checkout and an unordered two-instance lock cycle | ordered acquisition, acyclic joins, directory gate locks | T-19 |
| X71 | rev 3.0 R-56 (run-gate rules "carried by reference") vs `--rm`, `GIT_CONFIG_GLOBAL=/dev/null`, a single mount | the state machine, a writable config, the dual mount | T-20 |
| X72 | draft.3 S13.1 ("same numeric scale", "the cgroup file it writes") vs compose `memswap_limit` (total) and `cpu_shares` (a different scale) | written conversions and read-back; `requested`/`applied` | T-22 |

## 4.8 What to drop

Revision 2.1's drop list (§4.8, git `2347191f`) stands. Dropped by revision 3.0, each with the reason:

| idea | source | why |
|---|---|---|
| Jinja-rendered declaration files (`*.toml.j2`) | rev 2.x | no expression is needed in a declaration once bindings and path references exist; machine-unreadable; ciu cannot write them (P11, R-08) |
| the two-pass stack render and the `routes` render binding | rev 2.1 V8-S3.5.5, S7.8.2 | consumers read their own local names; declarations never read derived values (R-05, R-18) |
| `init_requires`, `uses`, `after` | rev 2.1 | one concept — the binding — with `wait`; `requires` is its sugar (R-18, R-21) |
| hand-typed `contract` | rev 2.1 V8-S5.2 | a copy of the provider's list; derived from consumption instead (R-19) |
| `init_provides` and `[hooks.provides.<svc>]` as two spellings | rev 2.1 | `provides` on services and on hook entries (R-20) |
| secret directive strings (`ASK_VAULT:…`, `GEN_TO_VAULT:…`, …), `consumed_by`, `produced_by` | v7 S4, rev 2.1 | structured sources checked against `[vault.paths]`; `delivery = "hook"`; producer derived (R-30, R-31) |
| the rendered file as lock, in-place render, `[…render] complete`, fstat retry, "clean truncates" | rev 2.1 V8-S14.4 | directory-fd lock needs none of them (R-42) |
| `request_base` on ciu lanes; `tomllib` parse of `assay.toml` | rev 2.1 | `assay lanes --json` (R-52) |
| shell-string conjunction lanes | dstdns | `sequence` lanes (R-53) |
| `deploy.labels.prefix` | v7, rev 2.1 | fixed `ciu.*` ownership labels (R-15) |
| `deploy.env.defaults` | v7 | consumer data in a ciu table (R-13) |
| `public_fqdn` detection | v7 S2.7, rev 2.1 | declared per host (R-16) |
| `owned-seeded` | rev 2.1 | `seeded` (R-29) |
| the per-stack rendered `ciu.toml` and the `ciu.toml.j2` stack override | rev 2.1 V8-S2.2, S3.1.3 | one resolved file; overrides through the merged path (R-09, R-10) |
| flat `ciu.rendered.<svc>.<cfg>` file mounts | rev 2.1 V8-S6.9 | v7 S5.3a's directory mounts (R-35) |
| `CIU_SKIP_DEPENDENCY_CHECK` and the startup docker check | rev 2.1 V8-S18.2 | preflights per need (R-02) |
| `facts_schema` | rev 2.1 | `schema_version` in one envelope (R-49) |
| built-in `localhost` host and `local` layout | review option | rejected by the operator; `ciu init` writes them (Q10) |
| freezing run-gate | rev 2.1 V8-18 | run-gate stays standalone (R-01) |
| closed `environment` vocabulary on layouts | rev 2.1 V8-S7.6 | no semantics; free-form (R-69) |
| "unclaimed fact" WARN | rev 2.1 V8-S5.3.2 | a provider's list is not a contract; INFO (R-19) |
| `[project] vendor_images` | rev 3.0 | ownership is declared on the service (`build`), never inferred from a list or a name (T-29) |
| `ciu.gate.shared-<name>.lock` files | rev 3.0 | an unlinkable lock splits; the owning stack directory is the lock (T-19) |
| `published_on = [networks]` | rev 3.0 | cannot represent host publications; socket claims (T-26, T-34) |
| one `schema_version` for every artifact | rev 3.0 | unrelated artifacts version independently; `api`/`api_version` (T-33) |
| `--rm` for ephemeral lanes; `GIT_CONFIG_GLOBAL=/dev/null` | rev 3.0 | evidence must survive a failed container; git needs a writable config (T-20) |
| in-place secret refresh | rev 3.0 | not atomic for readers; directory + rename (T-24) |
| `ciu check` executing hook `--validate` by default | rev 3.0 | consumer code in a "side-effect-free" verb; opt-in (T-23) |
| a global "secret-free" verdict | rev 3.0 | a heuristic cannot certify; it lints and names its comparisons (T-28) |
| rsync-in-place push; `ciu down` as rollback | rev 3.0 | mixed trees and no prior release; manifested releases (T-25) |
| the reviewer's UUID registry under the git common dir | third-party Alt. A | rejected by operator decision (§4.3.1, 2026-09-03); recorded for reopening if a collision or a `git clean -x` loss occurs |
| the reviewer's `optional = true` binding to a mocked capability | T-04 | an ordering-only binding to a mock already has no edge and no data; nothing is left to make optional |

## 4.9 Open product decisions

None. Every fork surfaced by the review was put to the operator (§4.3.1) and converged. The naming decisions revision 3.0 took alone (§4.3.9) are listed so they can be overturned cheaply; none changes the model.

## 4.10 Known gaps in this proposal

1. **Routes for multi-endpoint providers and replicas** (rev 2.1 gap 1) — unchanged: a stateful replicated provider needs a per-replica endpoint naming rule.
2. **`seeded` for stateful stacks** (gap 2) — the preparation workflow is a consumer concern; the hook that writes prepared credentials to Vault is named, not written.
3. **Lock semantics on network filesystems** — the directory lock is refused by name where `flock` is unsupported (V8-S14.4.6); no fallback is offered.
4. **Certificate issuance** (gap 4a) — delegated to the `pki` hook; the contract of `pki/<network>/<consumer>/{cert,key,ca}` is not specified.
5. **cgroup slices inside the devcontainer** (gap 5) — the live probe before V8-12 is still owed.
6. **Binding-carried credentials** (§4.3a C) — deferred; if adopted, a binding's `secrets = { password = "<provider secret key>" }` would deliver the provider's published secret under the consumer's prefix.
7. **`ciu.hookkit` argument shapes** — function names and contracts are specified (V8-S12.5); signatures are the implementer's.
8. **run-gate alignment** — V8-19/N18 is filed; until it ships, run-gate exec mode against a v8 checkout falls back to its v7 derivation and fails on the renamed file.
9. **Migration effort** (gap 9) — `ciu migrate` is specified mechanically (V8-App A); the expansion of Jinja control flow in declarations is best-effort and reports residues; dstdns's thirteen hooks are hand work.
10. **Scenario coverage** (gap 10) — not walked: multi-instance deployments of different projects sharing one host's mesh; Docker Desktop path semantics; the interaction of `inherit` with a project that is itself inherited from (forbidden, one level).
11. **`allow_from` enforcement** (gap 11) — declarative; no verification beyond the consumer's own tests.
12. **Provenance as adjudicated evidence** (gap 12) — LaneResults now carry per-service provenance; the assay-side attested-evidence contract (B004) is still assay's.
13. **The demo's hook scripts and config-file templates** are referenced by name, not written; the demo shows the declarations and compose templates only.
14. **State posture costs** (rev 3.1, operator decision): `git clean -x` still destroys the store; a moved worktree needs `--move`; a path-hash collision is refused, not avoided. Reopen the registry alternative (third-party A) if any of these bites in practice.
15. **Judge capability record** — CIU checks a version floor against its own minimum (4.1.0); an `assay --capabilities` record would let it check features instead (needs assay).
16. **Binding directories** (third-party C) as a third delivery, and **signed receipts** (D) if activation ever leaves the SSH channel — recorded, not adopted.
17. **Executable conformance of the demo** — the resolved example is derived by rule but not yet produced by a program; V8-13 must make `ciu check --graph` reproduce it and fail when it drifts.
