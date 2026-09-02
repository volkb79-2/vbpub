# CIU v8 Specification

**Version:** 8.0.0-draft.3 · **Status:** DRAFT (ground-up specification for CIU v8; companion to `CIU-V8-TESTING-GATE-PROPOSAL.md` revision 3.x; worked examples `v8-dstdns-demo/`) · **Date:** 2026-09-02
**Supersedes:** 8.0.0-draft.2 (2026-08-30). The changes and their reasoning are recorded in `CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md` (findings R-01..R-78) and in the proposal's §4.3.12/§4.7 (X41..X56). This draft is a fresh text, not a patch: declaration files are plain TOML, consumers declare *bindings* instead of reading *routes*, secrets are structured tables, the contract of a LogicalService is derived, the instance lock is the checkout directory, and the gate has a zero-instance mode.

This document is self-contained. It defines what a v8 CIU implementation MUST do, in normative language (MUST / MUST NOT / SHOULD / MAY as in RFC 2119). Rules are numbered `S<section>.<rule>` so a refusal message, a test, or a review can cite exactly one rule. Every refusal an implementation emits MUST name the rule it enforces in the form `[S<n>.<m>]`. Rule numbers of draft.2 that were withdrawn are not reused; a rule that moved is cross-referenced in Appendix C.

---

## S1 Scope, vocabulary, invariants

### S1.1 What CIU does
CIU deploys a **project** — a set of Docker Compose stacks plus external and borrowed services — as isolated **instances** (one per checkout), on one or several **hosts**, at a chosen **realness** level per capability, and runs the project's **gate** (test lanes and judge lanes) either against a deployed instance or, for a project with no stacks, against nothing at all. CIU owns: configuration layering, identity, ordering, the resolution and delivery of every dependency a consumer declares (a *binding*), secret materialization and delivery, host placement and remote push, resource governance, preflight validation, and gate execution. CIU does not own: the contents of a service's compose stanzas beyond what it injects (S11), application configuration semantics, secret rotation inside applications, certificate issuance, proxy policy, or the judgment of test evidence (the judge is `assay`, an external program that CIU invokes only when a lane asks for it).

### S1.2 Glossary and entities
The word **service** is used in exactly these senses, always qualified when ambiguous: a *LogicalService* (`[service.<n>]`, a capability); a *RealizedService* (`[ciu_stack.<svc>]`, one container of a stack); the *variant service* (which RealizedService of a multi-service stack carries a capability, S5.2).

| entity | identity | meaning |
|---|---|---|
| **Project** | `project.name` | the consumer repository |
| **Instance** | `instance_id` | one checkout's deployment; the primary checkout is an instance too; a project with no Realizations has none (S16.11) |
| **LogicalService** | `[service.<n>]` | a capability consumers bind to; its **contract** is derived from what is bound (S5.3) |
| **RealnessVariant** | `[service.<n>].<level>` | which Realization (and which of its services) satisfies the capability at a level |
| **Realization** | `[realization.<n>]` | a concrete provider: `ciu_stack`, `external`, `joined` |
| **RealizedService** | `[ciu_stack.<svc>]` in a stack file | one deployable service of a `ciu_stack` |
| **Endpoint** | `endpoints.<e>` on a RealizedService or an external Realization | a reachable port or URL |
| **Binding** | `binds.<local>` on a RealizedService or a gate environment | a consumer's declared dependency on a LogicalService (optionally one of its endpoints), under a local name, with a wait rule and a delivery |
| **Resolution** (derived) | (consumer, local name) | how CIU satisfied a binding: network, host, port, URL, TLS facts, readiness prerequisites |
| **TypedFact** | `kind:selector` string | a provable statement about live infrastructure |
| **Host** | `[hosts.<h>]` | a machine with one address per address-plane Network |
| **Network** | `[network.<n>]` | a reachability domain: an address plane, or a proxy |
| **Bundle** | `[bundles.<b>]` | a set of LogicalServices that deploy together |
| **Layout** | `[layouts.<l>]` | placement of bundles on hosts and the networks each host reaches |
| **Identity** (derived) | per RealizedService/replica | container name, hostname, compose key, compose project, network |
| **Wave** (derived) | ordinal | a set of Realizations deployed together |
| **Environment** (gate) | `[testing.environments.<e>]` | where a lane's process runs; also the target of `ciu instance exec` |
| **Lane** (gate) | `[testing.lanes.<l>]` | one command, one judge invocation, or a sequence of lanes, with preconditions and caps |

### S1.3 Invariants (each later rule serves at least one)
- **I1 One source per fact.** A fact is declared in exactly one place; every other appearance is derived from it and marked derived.
- **I2 Fail at the earliest checkable point.** Schema → `ciu check` → deploy. No silent defaults for facts that exist elsewhere; the only defaults are policy defaults (values that are correct in the absence of information and shadow no fact — S3.4.4 names them).
- **I3 Derived values are visible.** Every derived value is written as data into a rendered, cat-able file.
- **I4 Mechanical checkability.** Closed vocabularies, referential integrity and graph completeness are verified by the tool, not by a reader.
- **I5 Nothing hidden.** No hidden directories; no ambient process environment as a configuration source; all machine-owned files are visible and gitignored by a fixed list.
- **I6 One derivation per identity.**
- **I7 Declaration is separate from resolution.** Needs are declared by capability under a consumer-chosen local name; how they are satisfied is computed and recorded.
- **I8 Declarations are data.** No declaration file is a template. Templates exist only for artifacts CIU hands to other programs (compose files, application config files).

### S1.4 Names and grammars
- `name` := `^[a-z][a-z0-9_]*$` — LogicalService, Realization, RealizedService, Endpoint, Network, Binding local names, secret keys, config-file names, hostdir purposes. Bundle, Layout, Environment and Lane names additionally allow `-` (`^[a-z][a-z0-9_-]*$`).
- `hostname` := `^[a-z][a-z0-9-]*$` for Host names.
- `envname` := `^[A-Z][A-Z0-9_]*$` for environment-variable names and `env_prefix`.
- `duration` := `^[0-9]+(ms|s|m|h)$`. `size` := `^[0-9]+(K|M|G|T)?$` (bytes, binary units) or `"max"`.
- `level` := `live` | `seeded` | `simulated` | `mock`.
- `version-floor` := `^>=[0-9]+(\.[0-9]+){0,2}$`.
- `target` (a binding's `to`) := `<name>` (a LogicalService) or `<name>.<name>` (a LogicalService and one of its endpoints).
- TypedFact grammar (S5.6): `pg:db/<name>`, `pg:role/<name>`, `pg:schema/<name>`, `minio:user/<name>`, `minio:bucket/<name>`, `vault:secret/<path>[#field]`, `http:<path>`, `pki:issuer/<network>`.
- Reserved names: a Realization MUST NOT be named `hosts` or `ciu`; a RealizedService MUST NOT be keyed `secrets`; a consumer sub-table of a service MUST NOT be named `identity`, `health`, `endpoints`, `binds`, `hostdir`, `configfile`, `secrets`; a binding local name MUST NOT equal another binding's local name of the same service.

### S1.5 Root resolution
S1.5.1 The checkout root is the nearest ancestor of the working directory (inclusive) that contains `ciu.toml`; `--root <dir>` overrides it. No environment variable names the root. S1.5.2 With `[ciu] standalone_root = true` in that file, an ancestor that also contains `ciu.toml` is ignored (`[S1.5] nested inside ciu root <path>`); without it, the nearest wins.

---

## S2 Files

### S2.1 Project root (every path relative to the checkout root)
| file | committed | written by | role |
|---|---|---|---|
| `ciu.toml` | yes | consumer | the project's declarations (S3); plain TOML |
| `ciu.site.toml` | yes, optional | consumer | sparse site override of any declared table, including re-rooted stack tables (S3.1.3) |
| `ciu.instance.toml` | **no** | operator (and `ciu instance init/add`) | per-instance operator declarations: layout, bundles, label, joins, host-port overrides (S14.2); plain TOML, round-trip-edited by CIU only to append or replace whole tables |
| `ciu.instance.generated.toml` | no | ciu | CIU-owned facts merged after the instance file: instance identity, host facts, realness records, build facts (S14.2) |
| `ciu.resolved.toml` | no | ciu | the rendered merged configuration plus every derived table under `[resolved]` (S3.7); written atomically; the machine interface for every reader |
| `ciu.instance.json` | no | ciu | instance registry record (S14.7) |
| `ciu.hosts.toml` | no | operator | host inventory (S7.2, S17.1) |
| `ciu.secrets.toml` | no | ciu | materialized secrets store (S10.6) |
| `assay.toml` | yes | consumer | judge lanes (owned by assay; CIU never parses it, S16.7) |
| `ciu.gate.<lane>.json`, `ciu.gate.shared-<name>.lock` | no | ciu | the last LaneResult per lane (S16.9) and shared-resource locks (S16.5.3) |
| `<evidence_dir>/` (default `ciu-gate-evidence/`) | no | ciu | gate artifacts, verdicts, LaneResult history (S16.2) |
| `ciu-data/` | no | ciu | generated host directories (S6.8) |
| `ciu.env` | no | ciu (`ciu env print > ciu.env`) | derived shell export for humans and legacy tooling; **never read by ciu** |

User-global: `~/.config/ciu/hosts.toml` (S17.1), `$XDG_CACHE_HOME/ciu/` (build caches).

### S2.2 Stack directory (`location` of a `ciu_stack` Realization)
| file | committed | written by | role |
|---|---|---|---|
| `ciu.stack.toml` | yes | consumer | the stack's declarations (S6); plain TOML |
| `ciu.compose.yml.j2` | yes | consumer | compose template (S11) |
| `ciu.compose.yml` | no | ciu | rendered compose file, injections applied; the file compose runs |
| `ciu.state.toml` | no | ciu (hook outputs) | hook-persisted non-secret state (S6.10, S12.3) |
| `ciu.rendered/<svc>/<mirrored target path>` | no | ciu | rendered config files, mounted by parent directory (S6.9) |
| `ciu.secret-copy.<svc>.<key>`, `ciu.secret-copy.<key>` | no | ciu | per-run bind-mount sources for `file`-delivered secrets (S10.7) |
| hook scripts, config templates | yes | consumer | referenced by name from the stack file |

There is no per-stack rendered TOML: the stack's tables as CIU resolved them are in `ciu.resolved.toml` under `realization.<R>` (S3.6).

### S2.3 Gitignore list
S2.3.1 The following patterns MUST be ignored in every checkout, and `ciu check` stage 1 MUST verify each is ignored (`git check-ignore`) when the checkout is a git work tree: `ciu.resolved.toml`, `ciu.instance.toml`, `ciu.instance.generated.toml`, `ciu.instance.json`, `ciu.compose.yml`, `ciu.state.toml`, `ciu.rendered/`, `ciu.secret-copy.*`, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.gate.*`, `ciu.env`, `ciu-data/`, and the configured `testing.evidence_dir`. S2.3.2 A directory named `.ciu` anywhere under the checkout is an ERROR (`[S2.3] legacy .ciu directory present; run ciu migrate`), except during `ciu migrate`. S2.3.3 CIU MUST NOT create any hidden file or directory inside a checkout. S2.3.4 Deleting these files while an instance is up (`git clean -x`) loses rendered state but not the instance lock (S14.4); `ciu render` recreates every file in this list except the store, the instance file, the generated file, the record and `ciu-data/`.

### S2.4 Secret-free rule
S2.4.1 Every committed template (`ciu.compose.yml.j2`, config-file templates) and every declaration file MUST be scanned before use: a PEM block (`-----BEGIN`), or a key whose last `_`-separated component is one of `password`, `token`, `secret`, `credential`, `passphrase`, or whose last two components are `api_key`, `private_key`, `secret_key`, `access_key` (a bare `key` is not sensitive: `work_stream_key = "dstdns:streams:…"` is a Redis key name), paired with a literal string value of 8 or more characters that is not a `{{ … }}` or `${…}` reference, not a `/run/secrets/` path, and not a `/`-bearing KV path (`^[a-z0-9_.-]+(/[a-z0-9_.-]+)+$`), is an ERROR (`[S2.4]`). S2.4.2 After rendering, `ciu.compose.yml` and every file under `ciu.rendered/` MUST be scanned for any store value (S10.6) of 8 or more characters appearing verbatim, except a config file whose service declares a `delivery = "configfile"` secret (S10.2.6); a match is an ERROR and the artifact MUST be deleted before the refusal is reported.

### S2.5 File modes
`ciu.secrets.toml` 0600; `ciu.secret-copy.*` per the secret's `mode`/`uid` (default 0400, `instance.user_uid`); rendered files 0644; `ciu.instance.toml` and the generated file 0644; LaneResults written on failure 0600 (S16.9.3).

---

## S3 Configuration model

### S3.1 Layers and merge
S3.1.1 The global configuration is the deep-merge, in order, of: `ciu.toml` → `ciu.site.toml` (if present) → `ciu.instance.toml` → `ciu.instance.generated.toml`. All four are plain TOML; none is rendered (I8). S3.1.2 Merge semantics: tables merge recursively; scalars and lists REPLACE; a table cannot be deleted by a later layer — a service, secret, binding, endpoint, lane or environment is removed with `enabled = false` (S6.7), and `{}` is a no-op. S3.1.3 A stack's declarations are the deep-merge of the global configuration with `<location>/ciu.stack.toml`, whose tables are **re-rooted** per S3.6 before the merge; a site or instance layer MAY therefore carry `[realization.<R>.services.<svc>.…]`, `[realization.<R>.secrets.…]`, `[realization.<R>.hooks]` or `[realization.<R>.governance]` tables, which merge over the stack file's tables — the one override mechanism for stack declarations. S3.1.4 The instance file and the generated file MUST exist for every mutating verb except `instance init`; absence is an ERROR (`[S3.1] no instance file; run ciu instance init`) — except in a zero-instance project (S16.11), where neither exists and no verb but `check`, `gate`, `schema`, `doctor`, `version` and `init` is permitted.

### S3.2 Templates
S3.2.1 Only `ciu.compose.yml.j2` and the config-file templates named by `configfile.<n>.template` are rendered, as Jinja2 with `StrictUndefined`: any reference to an undefined name, attribute or item is an ERROR naming the template and the expression (`[S3.2] undefined 'binds.databse' in applications/controller/ciu.compose.yml.j2:31`). S3.2.2 The render context contains ONLY the names listed in S3.5 and the Jinja built-ins. There is NO `env` mapping and NO access to the process environment from a template. S3.2.3 In compose templates `${VAR}` sequences are left for Docker Compose interpolation and MUST name a variable in S11.6's set; `$$` is an escaped dollar. `${VAR:-…}` and `${VAR:?…}` forms are an ERROR (S11.3). S3.2.4 `{% set %}`, loops and conditionals are permitted (data expansion); `{% include %}`, `{% import %}`, `{% extends %}`, custom filters and functions are NOT provided (`[S3.2] template loader disabled`). S3.2.5 Rendering is deterministic: the same inputs MUST produce byte-identical outputs. S3.2.6 A `.j2` suffix on a declaration file name (`ciu.toml.j2`, `ciu.stack.toml.j2`, `ciu.global.*.toml.j2`) is an ERROR naming Appendix A (`[S3.2] declaration files are plain TOML in v8; run ciu migrate`).

### S3.3 Top-level tables of the global configuration
Closed set. Any other top-level table is an ERROR (`[S3.3] unknown top-level table 'foo'`) unless it is named in `ciu.user_tables`.

| table | defined in | owner |
|---|---|---|
| `project` | S3.4 | consumer |
| `service` | S5.2 | consumer (+ instance-file rows for joins) |
| `realization` | S5.4 | consumer (+ instance-file rows for joins; + site/instance overrides of re-rooted stack tables, S3.1.3) |
| `network` | S7.3 | consumer |
| `bundles` | S7.5 | consumer |
| `layouts` | S7.6 | consumer |
| `realness` | S9.2 | consumer |
| `vault` | S10.3 | consumer |
| `registry` | S3.4.6 | consumer |
| `governance` | S13 | consumer |
| `testing` | S16 | consumer |
| `ciu` | S3.4.7 | consumer |
| `ciu.instance` | S14.2 | operator (instance file) + ciu (generated file) |
| `ciu.host` | S14.2 | ciu (generated file) |
| `resolved` | S3.7 | ciu only (rendered file) |

`hosts` is the top-level table of `ciu.hosts.toml` (S7.2), never of the global configuration.

### S3.4 `[project]` and `[ciu]`
S3.4.1 `[project]`: `name` (name, required, literal), `revision` (integer, required, MUST equal `8` — `[S3.4] config revision 7 is not 8`), `log_level` (`DEBUG|INFO|WARN|ERROR`, default `INFO`), `landscape_id` (`^[a-z][a-z0-9-]{0,62}$`, optional; consumer fact bound as `instance.landscape_id`). S3.4.2 `[project.registry]`: `url` (string, `""` = local daemon), `namespace` (name; required when any stack image or `ciu build` references it). S3.4.3 `[project.compose_env.<VAR>]`: string values exported to every compose process (S11.6). `[project.control.<flag>]`: booleans only (S6.7). `[project] vendor_images`: list of image names that `ciu build` does not build (pulled; recorded by digest only). S3.4.4 `[project.health]`: `interval` (default `10s`), `timeout` (default `5s`), `start_period` (default `60s`), `retries` (default `6`), `gate_timeout` (optional) — the defaults merged into every RealizedService's `health` table (S6.6). These four are **policy defaults** (I2): they shadow no fact and are correct for a service that declares nothing. S3.4.5 There is no `[project.env.defaults]`: template data that CIU attaches no semantics to lives in a consumer user table. S3.4.6 `[registry.*]`: free-form consumer metadata; CIU reads only `registry.postgresql.database` (target database of `pg:schema/*` probes); the rest is validated by the consumer's `ciu.registry_validator` (S15.3 stage 14). S3.4.7 `[ciu]`: `standalone_root` (bool, S1.5.2), `require_fqdn` (bool; `instance init` refuses when the rendered host declares no `fqdn`, S7.2), `auto_connect_network` (bool, default true; S7.8.7), `exit_on` (`WARN|ERROR|NEVER`, default `WARN`), `user_tables` (list of names, default `[]`), `registry_validator` (path, optional). `[ciu.instances]`: S14.6.

### S3.5 Template contexts
S3.5.1 **Compose and config-file render** of the stack bound to Realization `R` sees exactly: `project` (S3.4), `instance` (S3.5.2), `host` (S3.5.3), `ciu_stack` (this stack's services with derived `identity`, merged `health`, resolved `endpoints` incl. `allow_from_resolved` and `published_on`, and — per service — `binds.<local>` bound to that binding's resolution (S7.8) when its delivery is `template`), `realization` (the merged view of every Realization in the deploy set, S3.6 — read-only), `networks` (the derived network table, S7.9 — `networks.edge.fqdn`), `state` (S6.10), `stack_dir` (the physical host path of the directory being rendered), the stack's own consumer top-level tables by name, every global user table by name, `registry`, `vault.paths`, and — config-file templates only — the function `secret("<key>")` for secrets declared with `delivery = "configfile"` (S10.2.6). Inside a service's replica loop, `replica` is bound to the replica's identity row (S6.5). S3.5.2 `instance` is a read-only mapping assembled from `[ciu.instance]`, `[ciu.instance.generated]`, `[ciu.instance.build]` and `[resolved]`: `id`, `project`, `label`, `layout`, `environment` (S7.6), `network` (the instance network's name), `landscape_id`, `services` (the list of selected LogicalService names, for template guards), `build_version`, `build_time`. S3.5.3 `host` is a read-only mapping of `[ciu.host.generated]` (S14.2): `name`, `hostname`, `fqdn`, `repo_root`, `physical_repo_root`, `env_type`, `user_uid`, `user_gid`, `docker_gid`, plus `addresses.<network>` from the host inventory. S3.5.4 A binding with `delivery = "env"` or `"none"` is NOT bound in any template; referencing it is an undefined-name ERROR (S3.2.1) whose message names the binding's delivery. S3.5.5 A stack's own top-level consumer table whose name equals a global top-level table (S3.3), a global user table, or a name in S3.5.1's context is an ERROR.

### S3.6 Re-rooting and the merged view
S3.6.1 A stack file declares its services under the fixed root `[ciu_stack.<svc>]` (S6.1), MAY declare stack-level secrets under `[ciu_stack.secrets.<key>]` (S10.2), and MAY declare the reserved tables `[hooks]`, `[governance]`. S3.6.2 When the stack bound to Realization `R` is loaded, CIU MUST place its service tables at `realization.R.services.<svc>`, its stack-level secrets at `realization.R.secrets`, its reserved tables at `realization.R.hooks|governance`, its state (S6.10) at `realization.R.state`, and its consumer top-level tables at `realization.R.<table>`. S3.6.3 A consumer top-level table named `services`, `secrets`, `hooks`, `governance`, `state`, `kind`, `location`, `per_host`, `provides`, `endpoints`, `instance`, `service` or `hosts` is an ERROR (`[S3.6] reserved stack table 'services'`). S3.6.4 The merged view is what `ciu.resolved.toml` carries under `[realization.<R>]` and what templates and hooks read as `realization`.

### S3.7 Derived tables — `[resolved]`
S3.7.1 CIU MUST write, on every render, the derived tables specified in S4.4 (identities and endpoints), S7.7 (placement), S7.8 (binding resolutions), S7.9 (networks), S8.8 (waves, gates, edges), S9.3 (selection), S13.4 (effective governance), S17.3 (bundle contents when rendered with `--layout`) into `ciu.resolved.toml` under `[resolved]`, together with `schema_version = 2`, `layout`, `host`, `environment` and `rendered_at`. S3.7.2 A `resolved` table in any INPUT layer is an ERROR (`[S3.7] resolved is derived`). S3.7.3 The derived tables are regenerated identically from the same inputs; they carry no state. S3.7.4 The rendered file is emitted in canonical order: input tables in S3.3 order, then `[resolved]` sub-tables in the order listed in S3.7.1, keys sorted within each table. S3.7.5 The rendered file is written by temp-file and atomic rename (S14.4.2); a reader therefore sees a complete file or none.

### S3.8 Validation of the schema itself
S3.8.1 Every table in this specification has a closed key set; an unknown key is an ERROR naming the table and the key (`[S3.8] unknown key 'memroy_max' in [governance]`). Consumer data is admitted only where a rule says "consumer data" (sub-tables of a service, user tables, `[registry.*]`, `[vault.paths]`, stack-level consumer tables). S3.8.2 Types and vocabularies are as stated per rule; a violation is an ERROR naming the rule. S3.8.3 Referential rules (a name that must resolve) are enforced by `ciu check` stage 4 (S15.3). S3.8.4 The closed key sets MUST be defined once, declaratively; the validator, the documentation and `ciu schema --json` (a JSON Schema for `ciu.toml`, `ciu.site.toml`, `ciu.instance.toml`, `ciu.stack.toml` and `ciu.hosts.toml`) are generated from that single definition (I1). Referential and graph rules are not expressible in JSON Schema and are not claimed by it.

---

## S4 Identity

### S4.1 Instance identity
S4.1.1 `ciu instance init` derives `instance_id` as the first 6 hex characters of SHA-256 over the checkout's **physical** absolute path (the host path, resolved through the container's mount table when running inside a container), and writes it into `[ciu.instance.generated]` of `ciu.instance.generated.toml`; host-local facts go into `[ciu.host.generated]` of the same file (S14.2). S4.1.2 Without `--host`, re-running `init` on a checkout whose physical path changed is an ERROR unless `--move` is given (`[S4.1] physical path changed; instance identity would change`). With `--host <h>` on a checkout whose generated file already carries an `instance_id` (a pushed bundle, S17.3), only `[ciu.host.generated]` is regenerated and no path comparison is made. S4.1.3 The primary checkout is an instance like any other.

### S4.2 The derivation function
S4.2.1 Let `R' = R.replace("_", "-")` and `svc' = svc.replace("_", "-")`. For every RealizedService `svc` of a `ciu_stack` Realization `R` in project `P` with instance id `I`:
- `compose_project = "{P}-{I}-{R'}"`
- `compose_key = "{R'}-{svc'}"` when `svc ≠ R`, else `"{R'}"`
- `container_name = hostname = "{P}-{I}-{R'}-{svc'}"` when `svc ≠ R`, else `"{P}-{I}-{R'}"`
- with `instances = N > 1`: replica `k` (1..N) has `container_name = hostname = <base>-{k}` and `compose_key = <base_key>-{k}`; the service-level `compose_key` (`<base_key>`) is injected as an alias on every replica (S11.4) so it resolves to all replicas
- `network = "{P}-{I}-network"` (the instance network; created by CIU, S14.1.2)
S4.2.2 The service component is omitted when `svc == R`. S4.2.3 No other code path in CIU, in a template, in a hook, or in the gate MAY form these strings; they are read from `[resolved.identities.R.svc]` (S4.4) or from the `ciu_stack.<svc>.identity` render binding. S4.2.4 A `per_host` Realization (S7.6.5) has the same identity on every host it runs on (names are per daemon). S4.2.5 The `_` → `-` mapping is injective per name but the concatenation is not: `db_core` + `postgres`, `db` + `core_postgres` and a single-service Realization `db_core_postgres` all derive `…-db-core-postgres`. Uniqueness is therefore **checked, not structural** (S4.3.1); the refusal MUST print both derivations and name the ambiguous boundary.

### S4.3 Uniqueness and enforcement
S4.3.1 `ciu check` stage 8 MUST assert that every `container_name` and every `compose_project` in the deploy set is unique per host (`[S4.3] 'dstdns-98535c-db-core-postgres' is derived for db_core.postgres and for db.core_postgres`). S4.3.2 A rendered compose service that declares `container_name:` or `hostname:` with a value different from the derived one is an ERROR (`[S4.3] container_name 'x' differs from derived 'y'`); equal values are tolerated and removed; a `host_network = true` service (S6.2) has no injected `hostname`. S4.3.3 A derived `container_name` (replica suffix included) longer than 63 characters is an ERROR naming the service.

### S4.4 Identity and endpoint facts (derived table)
```toml
[resolved.identities.<R>.<svc>]
container_name = "…"  hostname = "…"  compose_key = "…"  compose_project = "…"  network = "…"
replicas = [ { index = 1, container_name = "…", compose_key = "…" }, … ]   # present only when instances > 1
[resolved.identities.<R>.<svc>.endpoints.<e>]
port = 5432  protocol = "tcp"  publish = "instance"  host_port = 5432  published_on = ["mesh"]  path = "/…"   # S7.4; joined instances resolve by this
```
S4.4.1 `hostname` always equals `container_name`. S4.4.2 The identity mapping is bound as `ciu_stack.<svc>.identity` in the stack's compose and config-file renders.

### S4.5 Labels
S4.5.1 CIU stamps every container it creates with the fixed labels `ciu.project`, `ciu.instance`, `ciu.realization`, `ciu.service`, `ciu.replica` (when replicated), `ciu.managed-by=ciu`. `ciu clean`, `ciu instance reap`, `ciu diagnose` and the gate enumerate resources by these labels only, never by name pattern; the label namespace does not depend on any consumer setting, so a consumer change can never orphan a resource. S4.5.2 A template label whose key starts with `ciu.` is an ERROR (`[S4.5] label namespace ciu.* is reserved`). Consumer labels are authored in templates from `ciu_stack.<svc>.identity.*`.

---

## S5 Services, realizations, the derived contract

### S5.1 Separation
S5.1.1 A consumer declares needs as bindings to LogicalServices (`[service.*]`) and providers as Realizations (`[realization.*]`); the only link is `realized_by` on a RealnessVariant. A stack file never names a LogicalService it *realizes* and never names a Realization (I7); it names LogicalServices only as binding targets.

### S5.2 `[service.<n>]`
Keys: `description` (string, optional); one entry per level (at least one): `live`, `seeded`, `simulated` — either a string naming a Realization (`live = "db_core"`) or a table `{ realized_by = "<r>", service = "<svc>" }` where `service` names a service key of that `ciu_stack` Realization — the **variant service**: its health stands for the capability, its endpoints are the capability's endpoints, it is the exec target and image source (default = the Realization's primary service, S8.6); `mock` is an empty table (`mock = {}`) and MUST NOT carry `realized_by` (S9.1). S5.2.1 A level outside S1.4's vocabulary is an ERROR. S5.2.2 A `realized_by` that does not resolve is an ERROR (`[S5.2] service 'main_db' level 'live' realized_by 'db_cor' is not a realization`); a `service` that is not a service of that Realization is an ERROR. S5.2.3 The same Realization MAY back several LogicalServices and several levels. S5.2.4 There is no `contract` key: the contract is derived (S5.3).

### S5.3 The derived contract and conformance
S5.3.1 The **contract** of LogicalService `X` is derived from consumption across the deploy set (every RealizedService and every gate environment in the selected bundles and lanes): `endpoints(X)` = the set of endpoint names `e` for which some binding declares `to = "X.e"`; `facts(X)` = the union of `facts` lists of bindings whose `to` names `X` (S6.4). S5.3.2 For every declared variant `service.X.<level> → R` (levels other than `mock`, whether selected or not), `R` MUST provide the contract: every `e ∈ endpoints(X)` is an endpoint of `R` (for a `ciu_stack`, of any of its services — names are unique per stack, S6.3.1); every `f ∈ facts(X)` is in `provides(R)` — for a `ciu_stack` the union of the declared `provides` of its services (S6.2), the `provides` of its hooks (S6.10), and the **derived** facts `vault:secret/<path>` for every secret with `from = "generate", store = "vault"` in `R` (S10.1.2); for `external` and `joined`, `provides` on the Realization (S5.4). A missing endpoint or fact is an ERROR naming the variant (`[S5.3] service 'main_db' variant 'seeded' (db_core_seeded) does not provide endpoint 'sql' bound by controller.database`). S5.3.3 A fact or endpoint some Realization provides that no binding consumes is INFO, never a finding (unlike draft.2's "unclaimed fact" WARN — the provider's list is not a contract). S5.3.4 An empty contract means: depending on `X` is depending on the variant service's health (S8.6). S5.3.5 A binding to `X` selected at `mock` yields no edge and no resolution; if its `delivery` is not `none` it is an ERROR at check time (`[S9.3] 'payment_api' is mocked; binding controller.payments has delivery env`).

### S5.4 `[realization.<n>]`
Common key: `kind` ∈ `ciu_stack | external | joined` (required). Per kind:
- `ciu_stack`: `location` (repo-relative directory containing `ciu.stack.toml` and `ciu.compose.yml.j2`, required); `per_host` (bool, default false; S7.6.5).
- `external`: `provides` (list of TypedFacts, default `[]`); `[realization.<n>.endpoints.<e>]`: `url` (URL, required; scheme default ports apply when the URL carries none), `tls` (`none|tls|mtls`, default from the scheme), `ca` (path, optional).
- `joined`: `instance` (an instance label per S14.7.2, or an absolute path), `service` (LogicalService name in the reference instance), `provides` (optional; default = the reference's `provides` for that service's selected Realization, read at check/up time).
S5.4.1 A key from another kind's set is an ERROR (`[S5.4] 'location' is not valid for kind 'external'`). S5.4.2 A `ciu_stack` whose `location` lacks either required file is an ERROR. S5.4.3 Two Realizations MUST NOT share a `location`. S5.4.4 Plain compose files without a stack file are not a Realization kind: wrap them in a `ciu_stack` (a stack file with one service per compose service).

### S5.5 Deploy set
S5.5.1 The deploy set of an instance is the set of Realizations reached from the selected bundles: bundles → LogicalServices → selected variant (S9.3) → Realization. `external` and `joined` Realizations are in the deploy set but are not brought up (S8.7). S5.5.2 A Realization no selected variant reaches is not deployed and not rendered; a `ciu_stack` in that state is still loaded and schema-checked (I2). S5.5.3 A Realization all of whose services are disabled (S6.7) leaves the deploy set; a LogicalService whose selected Realization left it is an ERROR only when some binding names it. S5.5.4 A binding target `X` that is in no selected bundle is an ERROR naming the bundles that contain it (`[S5.5] controller.tracing binds 'tracing', selected by no bundle (bundle 'observability' has it)`).

### S5.6 TypedFacts
| kind | selector | probe (live) |
|---|---|---|
| `pg:db/<name>` | database name | `SELECT 1 FROM pg_database WHERE datname=…` inside the provider container as `probe_user` |
| `pg:role/<name>` | role | `pg_roles` |
| `pg:schema/<name>` | schema in `registry.postgresql.database` | `information_schema.schemata` |
| `minio:user/<name>` | IAM user | `mc admin user info` inside the provider |
| `minio:bucket/<name>` | bucket | `mc ls` |
| `vault:secret/<path>[#field]` | KV path | read via CIU's Vault client at the resolution of CIU's own binding to the `vault` LogicalService (S10.3); `#field` is ignored for minter matching (S8.3) |
| `http:<path>` | GET `<path>` on the provider's first `http`/`https` endpoint returns 2xx | HTTP |
| `pki:issuer/<network>` | the provider issues certificates for network `<network>` (S10.5) | Vault read of `pki/<network>/ca` |
S5.6.1 The **provider container** of a fact is the RealizedService whose `provides` lists it, whose vault-stored generated secret derives it, or whose hook entry's `provides` lists it (S6.10); for facts of an empty-listing provider it is the variant service. CIU MUST NOT assume any service key. S5.6.2 `probe_user` (S6.2) names the database superuser used by `pg:*` probes; absent when a `pg:*` fact is probed → ERROR. S5.6.3 A probe result MUST distinguish `absent` (fact not present), `unreachable` (container not running / exec failed), and `starting` (container exists, not healthy), and report which.

---

## S6 Stack file — `ciu.stack.toml`

### S6.1 Root
S6.1.1 A stack file's service tables live under `[ciu_stack.<svc>]`; the same root is bound in the stack's compose render (`{{ ciu_stack.<svc>.… }}`). S6.1.2 Any other top-level table is either reserved (`hooks`, `governance`) or consumer data (S3.5.5). S6.1.3 A stack file MUST NOT contain `stack_name`, `name` or `location` keys at service level (the Realization name is bound by the registry; a service key equal to the Realization name is the S4.2.2 pattern and is fine). S6.1.4 A stack file is plain TOML; a `{{` sequence anywhere in a string value is a WARN naming the value (a migrated template expression that will not be rendered).

### S6.2 Service keys (closed set; consumer scalars are NOT admitted here — put them in a sub-table)
| key | type | default | meaning |
|---|---|---|---|
| `image` | string `name[:tag][@digest]` | required | the one image declaration |
| `instances` | integer ≥ 1 | 1 | replica fan-out (S4.2.1) |
| `one_shot` | bool | false | runs to completion; gates wait for exit 0 |
| `primary` | bool | true iff the stack has one service | S8.6 |
| `enabled` | bool or `project.control` flag name | true | conditional inclusion (S6.7) |
| `requires` | list of LogicalService names | `[]` | sugar for bindings without data (S6.4.4) |
| `binds.<local>` | table | — | S6.4 |
| `provides` | list of TypedFacts | `[]` | facts this service brings into existence by means other than a vault-stored generated secret (S5.3.2) |
| `depends_on` | list of sibling service keys | `[]` | S8.2, S11.4 |
| `probe_user` | string | — | S5.6.2 |
| `aliases` | list of DNS labels | `[]` | extra aliases on the instance network (S11.4) |
| `host_network` | bool | false | `network_mode: host`; no hostname/network injection (S11.4) |
| `endpoints.<e>` | table | — | S6.3 |
| `health` | table | merged from `project.health` | S6.6 |
| `hostdir.<purpose>` | string or table | — | S6.8 |
| `configfile.<name>` | table | — | S6.9 |
| `secrets.<key>` | table | — | S10.2 |
| `<sub-table>` | table | — | consumer data (any name not reserved by S1.4) |
S6.2.1 A scalar key not in this table is an ERROR (`[S6.2] unknown key 'database' on [ciu_stack.postgres]; consumer data goes in a sub-table`). S6.2.2 A consumer sub-table named `identity`, `health`, `endpoints`, `binds`, `hostdir`, `configfile` or `secrets` is an ERROR. S6.2.3 The keys `init_requires`, `init_provides`, `uses`, `after` are refused with a message naming their v8 form (Appendix A).

### S6.3 Endpoints — `[ciu_stack.<svc>.endpoints.<e>]`
`port` (integer 1..65535, required), `protocol` (`tcp|udp|http|https`, default `tcp`), `publish` (`instance|host|proxy`, default `instance`; S7.4), `host_port` (integer, default `port`; the host port used whenever the endpoint is published), `host_bind` (IP address; only with `publish = "host"`, default `0.0.0.0`), `allow_from` (list of `network.<n>` | `host.<h>`, optional), `path` (URL path, optional; required for `publish = "proxy"`; used by proxy resolutions and `http:` facts). S6.3.1 Endpoint names MUST be unique across all services of one stack (bindings target `<service>.<endpoint>`, S6.4). S6.3.2 Two endpoints published on the same host of one layout MUST NOT share `host_port` (S7.4.5); before `up`, a `host_port` already published by another registered instance on the same machine (read from its rendered file) or already bound on the daemon is an ERROR (`[S6.3] host port 8080 in use by instance a1b2c3`); the instance file MAY override host ports per endpoint (S14.2). S6.3.3 `allow_from` targets MUST resolve.

### S6.4 Bindings — `[ciu_stack.<svc>.binds.<local>]`
A binding is the one way a consumer depends on a LogicalService. Keys:
| key | type | default | meaning |
|---|---|---|---|
| `to` | `target` (S1.4) | required | `"<service>"` (no data: ordering and facts only) or `"<service>.<endpoint>"` (data: the endpoint is resolved and delivered) |
| `wait` | `healthy \| started \| none` | `healthy` | `healthy`: an ordering edge; the provider's variant service (and the endpoint's owning service) must be healthy — or completed for `one_shot` — before this service's wave; `started`: an edge, the provider's containers must be running; `none`: no edge, a resolution only (a runtime-only dependency: a tracing collector, a proxy's backends) |
| `delivery` | `env \| template \| none` | required when `to` names an endpoint; MUST be absent or `none` otherwise | `env`: CIU injects `<env_prefix>_HOST`, `<env_prefix>_PORT`, `<env_prefix>_URL` (when the resolution has a `url`), `<env_prefix>_PATH` (when it has a `path`), `<env_prefix>_TLS_CERT/_KEY/_CA` (when it has TLS paths) into the service's compose `environment:` (S11.4) and into a gate lane's process (S16.4); `template`: the resolution is bound as `ciu_stack.<svc>.binds.<local>` in the stack's templates (S3.5.1); `none`: nothing is delivered (the edge and facts still apply) |
| `env_prefix` | `envname` | required for `env` | the variable prefix |
| `facts` | list of TypedFacts | `[]` | facts this consumer relies on from the target; they enter the target's contract (S5.3) and are probed before this service's wave (S8.5.2) |
| `enabled` | bool or `project.control` flag name | true | a disabled binding derives nothing |
S6.4.1 `to` MUST name a LogicalService of the deploy set (S5.5.4) and, when an endpoint is named, that endpoint MUST exist on the selected variant (S5.3.2 covers every variant statically). S6.4.2 A service MUST NOT bind a LogicalService its own Realization realizes (self-binding is an ERROR). S6.4.3 Two bindings of one service MAY target the same LogicalService (different endpoints or different deliveries). S6.4.4 `requires = ["x", "y"]` is exactly equivalent to `binds.x = { to = "x" }`, `binds.y = { to = "y" }` (local name = target name, `wait = "healthy"`, no data); declaring both a `requires` entry and a `binds` entry with the same local name is an ERROR. The rendered file shows the expanded form (I3). S6.4.5 `env` delivery requires that the resolution can be formed at render time for the rendered host (S7.8); a resolution that depends on a joined instance's live state is read at render time under the reference's shared lock (S9.5.4). S6.4.6 The variables an `env` binding injects MUST NOT collide with any `env_name` of an `env`-delivered secret or with another binding's variables of the same service (`[S6.4] DATABASE_HOST injected twice`). S6.4.7 `env` delivery injects into the declaring service's compose block only; services of one stack that share an environment block (a compose anchor) or that name the variables differently use `template` delivery and write the variables themselves.

### S6.5 Replicas
S6.5.1 With `instances = N`, CIU renders one compose service per replica from ONE template block iterated by the template over `ciu_stack.<svc>.identity.replicas` (S11.3), renders each config file once per replica (S6.9.3), and derives per-replica identities (S4.2.1). S6.5.2 Health, endpoints and bindings are per service; resolutions to a replicated provider use the service-level `compose_key` (S7.8.6); a replicated service is healthy when every replica is (S8.6.4).

### S6.6 Health
S6.6.1 `[ciu_stack.<svc>.health]` keys: `interval`, `timeout`, `start_period` (durations), `retries` (integer), `gate_timeout` (duration). S6.6.2 CIU MUST merge `project.health` into every service's `health` table before any render, so `ciu_stack.<svc>.health.<k>` always resolves. S6.6.3 `gate_timeout` default = `start_period + interval × retries + 30s`. S6.6.4 The compose `healthcheck.test` is authored in the template; CIU injects `interval`, `timeout`, `retries` and `start_period` from the merged `health` table (S11.4) and refuses a template value that differs from them (`[S6.6] healthcheck.interval 30s differs from health.interval 10s`). CIU reads container health from Docker (S8.6.4).

### S6.7 `enabled`
S6.7.1 A bool, or the name of a `[project.control]` flag (the flag's value applies). Expressions are not permitted. A disabled service is omitted from the graph, and CIU REMOVES its block from the rendered compose after the template renders (templates need not guard it, S11.4); its tables stay bound in the render context with `enabled` resolved to `false`, so a template MAY guard shared blocks (`{% if ciu_stack.tls_proxy.enabled %}`) and a config file of a disabled service is not rendered; a `depends_on` naming a disabled sibling is dropped with a WARN; a binding whose target's variant service is disabled is an ERROR at check time; a Realization all of whose services are disabled leaves the deploy set (S5.5.3). S6.7.2 `enabled` on a secret (S10.2), a binding (S6.4), a lane or an environment (S16) has the same form.

### S6.8 Host directories — `[ciu_stack.<svc>.hostdir]`
`<purpose> = ""` (CIU-generated path `<physical_repo_root>/ciu-data/<R>/<svc>/<purpose>`), or `<purpose> = "/abs/path"`, or `<purpose> = { path = "…", uid = 1000, mode = "0750", seed = "relative/dir" }`. S6.8.1 CIU creates missing directories before compose runs and applies ownership from `uid` (default `instance.user_uid`) and mode through its privileged helper. S6.8.2 The resolved absolute host path is what the template reads as `ciu_stack.<svc>.hostdir.<purpose>`. S6.8.3 `ciu clean --vanilla` removes generated `ciu-data/<R>` directories through the same helper; `ciu clean` keeps them. S6.8.4 A v7 `vol-<service>-<purpose>` directory under a stack whose service declares a generated hostdir of the same purpose is an ERROR at `up` (`[S6.8] legacy data directory infra/db-core/vol-postgres-data exists; run ciu migrate --hostdirs or pin hostdir.data to it`); `ciu migrate --hostdirs` moves it into `ciu-data/` (same filesystem: rename; otherwise copy and verify) and never deletes the source until the copy is verified.

### S6.9 Config files — `[ciu_stack.<svc>.configfile.<name>]`
`template` (path relative to the stack directory, required), `target` (absolute in-container path, required), `mode` (default `"0440"`), `schema` (path to a JSON Schema, optional). S6.9.1 CIU renders `template` with the compose render context (S3.5.1) to `<location>/ciu.rendered/<svc>/<target path with its leading slash removed>` and injects a read-only bind mount of the **parent directory** `<location>/ciu.rendered/<svc>/<dirname of target>` to `<dirname of target>` in the container (absolute physical paths, S11.4). Mounting by directory means a missing rendered file can never be turned into an empty directory by the daemon (the v7 S5.3a hazard). S6.9.2 Two config files of one service whose targets share a parent directory are rendered into the same mounted directory; a target that is a parent directory of another service's mount or of a hostdir is an ERROR. Because the mounted directory hides whatever the image ships there, a config file for a program whose directory the image populates (`/etc/nginx/mime.types`) targets a subdirectory (`/etc/nginx/ciu/nginx.conf`) and the template points the program at it (`command: nginx -c …`); `ciu check` WARNs when a target's parent is a well-known image-populated directory (`/etc/nginx`, `/etc/postgresql`, `/usr/share/nginx/html`). S6.9.3 With `schema`, the rendered file MUST validate against it (ERROR otherwise). S6.9.4 Replicated services get `ciu.rendered/<svc>-<k>/…` per replica, rendered with `replica` bound to the replica's identity row.

### S6.10 Reserved tables and state
- `[hooks]`: `pre_secrets`, `pre_compose`, `post_compose` — lists whose entries are either a script path (string) or a table `{ run = "<path>", provides = [<TypedFacts>] }` (S12). `provides` on a hook entry lists facts the script creates; they are attributed to the stack's primary service unless the entry also carries `service = "<svc>"` (the service in whose container they are probed).
- `[governance]`: per-stack override of the global governance base (S13); shallow merge.
- `[ciu_stack.secrets.<key>]`: stack-level secrets (S10.2).
- State: hook-persisted non-secret data lives in `<location>/ciu.state.toml` (written by CIU from hook outputs, S12.3), is bound as `state` in the stack's render contexts and hook contexts, and is preserved across renders and `ciu clean`; `--vanilla` removes it. A key whose name matches S2.4.1's sensitive names is an ERROR (`[S6.10] secret-shaped key in state; use the secrets output`). A `[state]` table in a stack file is an ERROR.

---

## S7 Topology

### S7.1 Model
Distance is never declared on a consumer or a provider. It is derived from **placement** (S7.6) × **networks** (S7.3) × the provider's **endpoint** (S6.3) × the provider's **kind** (S5.4). The only declarations that carry distance are an endpoint's `publish`, `host_port` and `allow_from`.

### S7.2 Hosts — `[hosts.<h>]` (in `ciu.hosts.toml`; S17.1 for lookup order)
Keys: `local` (bool; the machine running ciu; no SSH keys required), `fqdn` (hostname; the host's public name, optional — required for the rendered host when `ciu.require_fqdn = true`), `ssh_host`, `ssh_user` (default `root`), `ssh_port` (default 22), `ssh_key` (path), `known_host` (string, required unless `local` or `CIU_SSH_INSECURE_TOFU=1`), `bundle_dir` (default `/opt/ciu/current`), `push_mode` (`auto|rsync|scp`), `bundle_excludes` (list, default `[".git"]`), `docker_optional` (bool; `activate health` skips Docker checks on this host), `[activate] bootstrap|apply|health|rollback` (command strings), `[secrets.<entry>]` (host-scoped secrets: tables with `from ∈ ask | generate | file` and the keys of S10.1; stored under `[secrets.hosts.<h>]`, S10.4), `[addresses.<network>]` (string address per address-plane Network the host sits on). S7.2.1 Exactly one host MAY be `local = true`. S7.2.2 Every host named by a layout MUST exist. S7.2.3 There is no built-in host; `ciu init` writes `[hosts.localhost] local = true` (S19).

### S7.3 Networks — `[network.<n>]`
Keys: `kind` (`address | proxy`, required), `realized_by` (Realization name, optional for `address`, required for `proxy`), `tls` (`none|tls|mtls`, default `none`), `pki` (LogicalService name; required when `tls ≠ none`), `fqdn` (hostname; required for `proxy`), `description` (optional; CIU attaches no semantics to the wording). S7.3.1 The network `instance` exists implicitly for every instance (kind `address`, name per S4.2.1) and MUST NOT be declared. S7.3.2 A network with `realized_by` is **ready** on a host only when that Realization's variant service is healthy on that host (S8.3). S7.3.3 A `proxy`-kind network is **address-free**: hosts carry no address on it; it is selectable in `reach` by any host, and resolving through it yields the network's `fqdn` (S7.8). S7.3.4 `tls ≠ none` requires `pki`, whose provider MUST provide `pki:issuer/<n>` (S10.5).

### S7.4 Endpoint publication
S7.4.1 `publish = "instance"` (default): the endpoint is reachable on the instance network, and — **derived from the layout** — additionally published on the provider host, bound to the provider host's address on network `N`, for every network `N` over which some cross-host **binding resolution with data** (a binding whose `to` names this endpoint, S7.8 step 5) reaches it: `ports: ["<addresses[host(R)][N]>:<host_port>:<port>/<tcp|udp>"]` (`http`/`https` map to `tcp`). A binding without an endpoint never publishes anything. On a single-host layout nothing is published. S7.4.2 `publish = "host"`: always published as `["<host_bind>:<host_port>:<port>/<tcp|udp>"]` in every layout. S7.4.3 `publish = "proxy"`: consumers reach the endpoint through a `proxy`-kind network in their `reach`; the **proxy itself** (the network's `realized_by`) reaches the endpoint by S7.8 rule 4 when placed on the same host, otherwise by S7.4.1's derived publication. What the proxy does with a resolution (TLS termination, authentication guards, rate limits) is the proxy stack's own configuration, rendered by its templates from its own `binds.*`; CIU carries no proxy policy. S7.4.4 `allow_from`: CIU resolves each entry to the set of addresses (all addresses of the named network's hosts, or the named host's addresses) and binds it as `ciu_stack.<svc>.endpoints.<e>.allow_from_resolved` (list of strings) for the stack's own templates. CIU does not program firewalls. S7.4.5 The set of (network, host_port) pairs published on one host of one layout MUST be collision-free (S6.3.2). S7.4.6 The publications derived for the rendered host are written to `[resolved.identities.<R>.<svc>.endpoints.<e>] published_on = [<networks>]` (S4.4) and listed by `ciu check --layout L` as the publication table (S15.4).

### S7.5 Bundles — `[bundles.<b>]`
`services` (list of LogicalService names; MAY be empty), `includes` (list of bundle names whose services are added; one level of composition — an included bundle's own `includes` are followed, cycles are an ERROR), `compose_profiles` (list, optional), `compose_env.<VAR>` (strings, optional). S7.5.1 The effective service set of a bundle is `services` ∪ the effective sets of `includes`. S7.5.2 Conflicting `compose_env` values across selected bundles are an ERROR.

### S7.6 Layouts — `[layouts.<l>]`
`environment` (free-form string, optional; bound as `instance.environment`; CIU attaches no semantics), `description` (optional), `hosts.<h>` (table per host, at least one): `bundles` (list of bundle names; MAY be empty only in a zero-instance project), `reach` (list of network names, non-empty; `instance` allowed and means "this host only"). S7.6.1 A layout is always explicit; an instance with no `layout` selected (S14.2) is an ERROR; when a project declares exactly one layout, `ciu instance init` selects it and says so. There is no built-in layout; `ciu init` writes `[layouts.local]` (S19). S7.6.2 Host declaration order is the push order (S17.2). S7.6.3 A Realization that is not `per_host` and is reached from the bundles of two hosts of one layout is an ERROR (`[S7.6] realization 'db_core' placed on both 'gstammtisch' and 'rs1002'`). S7.6.4 Every network in `reach` other than `instance` and `proxy`-kind networks MUST be one the host has an address on. S7.6.5 A `per_host = true` Realization (a transport daemon, a node exporter) MAY be reached from the bundles of several hosts of one layout and is deployed on each of them; no binding with data may target its capability (`[S7.6] per_host realization cannot be a binding target with an endpoint`); network readiness (S7.3.2) and network/pki edges (S8.3) are evaluated per host.

### S7.7 Placement (derived)
`[resolved.placement.<R>] hosts = ["<h>", …]` for every `ciu_stack` Realization in the deploy set (one element unless `per_host`); `external` and `joined` Realizations have no host.

### S7.8 Binding resolution (derived) — `resolve(C, b)` for consumer `C` (a Realization, a gate environment, or the pseudo-consumer `ciu`) and binding `b` with `to = X[.e]`
1. Resolve `X` to its selected Realization `R` (S9.3) — `mock` → no resolution (S5.3.5). Without an endpoint the resolution carries `service`, `realization` and `requires` only (step 6) and stops here. With `e`: for a `ciu_stack`, `e` is an endpoint of the service of `R` that declares it (names are unique per stack, S6.3.1); for `external`, an endpoint of the Realization. (A target whose Realization is `per_host` is an ERROR, S7.6.5.)
2. `R.kind == joined`: read the reference instance's rendered `[resolved.identities.<R'>.<svc>]` for the service that declares `e` (S4.4); `network = <reference instance network name>`, `host = <that identity's container_name>` (or service `compose_key` when replicated), `port = e.port`, `requires = []`. The consumer's containers are attached to the reference network (S11.4).
3. `R.kind == external`: `url` as declared; `host`/`port`/`tls` parsed from it; `requires = []`.
4. `host(C) == host(R)`: `network = "instance"`, `host = identity(R, svc).container_name` (or service `compose_key` for replicated services), `port = e.port`.
5. Otherwise iterate `reach(host(C))` in order and pick the first network `N` that admits the pair: a `proxy`-kind `N` admits when `e.publish == "proxy"` and `C` is not `N.realized_by`; an `address`-kind `N` admits when both hosts have an address on `N` and `e.allow_from` (if set) admits `host(C)` on `N`. If `N` is `proxy`-kind: `host = N.fqdn`, `port` = the `host_port` of `N.realized_by`'s variant service's `publish = "host"` endpoint of protocol `https` (ERROR if it has none), `path = e.path`, `url = "https://<fqdn><path>"`, `requires += [N.realized_by]`. Else: `host = addresses[host(R)][N]`, `port = e.host_port`, and the endpoint is published on `N` (S7.4.1). No admitting `N` → ERROR (`[S7.8] no route from 'controller'@rs1002 to 'main_db.sql'@gstammtisch: no shared network in reach ["mesh"]`).
6. `requires` += `N.realized_by` (if any, for `address` networks) and `N.pki` (when `tls ≠ none`); `tls` from `N`; `cert`, `key`, `ca` = the `/run/secrets/tls_*` paths of the derived TLS secrets (S10.5) when `tls ≠ none`.
7. `path = e.path` is always copied when declared. For **direct** resolutions (steps 2–5 over an `address` network) `url` is emitted for `http`/`https` as `<scheme>://<host>:<port>` — the path is NOT appended, because a proxy prefix is not an application base path — and for `udp` as `udp://<host>:<port>`; for `tcp` no `url` is emitted. For **proxy** resolutions `url = "https://<fqdn><path>"` includes the path.
S7.8.1 Resolutions are written as `[resolved.bindings.<C>.<local>]` (for a stack, `<C>` is `<R>.<svc>`; for a gate environment, `env.<e>`; for CIU itself, `ciu`) with keys `service, realization, endpoint?, network?, host?, port?, url?, path?, tls?, cert?, key?, ca?, requires, delivery, variables?` (`variables` = the names an `env` delivery injects). S7.8.2 In the render of stack `C`, `ciu_stack.<svc>.binds.<local>` is bound to the resolution for every `template`-delivered binding of `<svc>`. S7.8.3 A resolution exists only for a declared binding; there is no other way for a consumer to obtain an address (I7). S7.8.4 Cross-host resolutions require the network to be **ready** (S7.3.2) before the consumer's wave starts (S8.3). S7.8.5 The resolution function is the only code path forming addresses for service-to-service reachability; CIU's own Vault client (S10.3), probes (S5.6) and the gate (S16) obtain addresses through bindings of the pseudo-consumer `ciu` or of an environment. S7.8.6 Replicated providers: `host` is the service-level `compose_key` on the instance network (compose DNS), or the host address cross-host; per-replica resolutions are not derived. S7.8.7 **CIU's own vantage point**: the pseudo-consumer `ciu` (and every `host`-mode gate environment) is placed on the host running CIU; its bindings are derived implicitly (to `vault` when any vault-sourced secret exists; to every `requires.healthy` target of a host lane); when the result is on the instance network, CIU resolves `container_name` to the container's address via `docker inspect` on a native host, or attaches its own container to the instance network when `ciu.auto_connect_network = true` and `env_type = devcontainer`.

### S7.9 Networks (derived)
`[resolved.networks.<n>] name, kind, realized_by?, fqdn?, tls?` for every declared network and the implicit `instance` network (`name` = S4.2.1).

---

## S8 Ordering: init graph, waves, gates

### S8.1 Nodes
The graph's nodes are the RealizedServices of every `ciu_stack` Realization in the deploy set. `external` and `joined` Realizations are sinks that are always satisfied (their facts are asserted by `provides`, optionally probed).

### S8.2 Declared edges
- **bind** — for every binding `b` of service `s` with `wait ≠ none` and target `X[.e]`: an edge from `s` to the variant service of `X`'s selected Realization, to the service that declares `e` (when named), and to every RealizedService of that Realization whose provides intersect `b.facts`. `wait = "started"` edges are satisfied by `Running`; `wait = "healthy"` edges by S8.6.4. `requires` entries are bindings (S6.4.4).
- **depends** — `depends_on` siblings; also rendered into compose (S11.4).
- `wait = "none"` bindings derive resolutions only, never edges.

### S8.3 Derived edges (never declared; always listed in `edges`)
- **facts** — every fact in a service's `provides`, in a hook entry's `provides`, and every `vault:secret/<path>` derived from a `from = "generate", store = "vault"` secret is attributed to its service — for a stack-level secret (`[ciu_stack.secrets.<key>]`) and for a hook entry without `service`, to the stack's primary service; S5.3 conformance and minter resolution use the union.
- **secret→vault** — from every service that declares a secret with `from = "vault"` or `store = "vault"` to the variant service of the Realization selected for the `vault` pointer (S10.3). Exempt: services of that Realization itself.
- **secret→minter** — from every service with a `from = "vault"` secret at path `P` to the service in the deploy set whose facts contain `vault:secret/P` (field ignored); a `pki/<N>/…` path (S10.5) is satisfied by the provider of `pki:issuer/<N>`. No minter and no `external`/`joined` Realization providing it → ERROR (`[S8.3] nobody mints vault:secret/db/postgres/controller_password`).
- **network** — for every resolution of a consumer Realization `C` over an `address` network `N` with `realized_by = T`: from every service of `C` to `T`'s variant service on `host(C)`, and — when `T` is `per_host` — also on `host(R)`.
- **pki** — from every service with a resolution over a `tls ≠ none` network, and from every service with an endpoint reached over such a network, to the variant service of the `pki` LogicalService's Realization.

### S8.4 Waves
S8.4.1 A Realization's level is the maximum topological level of its services over the edge set of S8.2–S8.3, computed on the **Realization** graph (a Realization is deployed as a unit). A cycle at Realization level is an ERROR naming the cycle and the remedy (`[S8.4] cycle db_core → vault → db_core through db_core.postgres_init→vault.vault and vault.vault→db_core.postgres; split one realization or turn one binding into wait = "none"`), even when the service-level graph is acyclic — deliberate: units deploy whole. S8.4.2 Wave `k` is the set of Realizations of level `k`. Within a wave, per host, stacks are brought up in name order. S8.4.3 Cross-host: wave `k` on any host starts only after every provider of an edge into wave `k` has passed its gate on its own host (S8.5); CIU running on one host waits for a remote provider by probing its resolutions (S8.5.3), not by SSH or `docker exec`. `ciu activate apply` runs hosts serially in layout order (S17.4), so under the supported flow a consumer host's first wave begins after its provider hosts completed every wave; concurrent hand-started `up` runs on several hosts rely on S8.5.3 alone.

### S8.5 Gates
S8.5.1 After bringing up wave `k`, CIU waits, per provider service `p` on this host that has an incoming edge from a later wave: `p` is **healthy** (S8.6.4) within `p.health.gate_timeout` (S6.6.3). Timeout → ERROR naming `p`, its last observed state, and the budget. S8.5.2 Before starting wave `k+1`, every TypedFact required by an edge into `k+1` (binding `facts`) whose provider is on THIS host is probed (S5.6) with a bounded poll of the same budget; `starting` and `unreachable` are retried within the budget, `absent` is retried within the budget and reported as absent when it expires. S8.5.3 For a provider on ANOTHER host CIU probes reachability only (TCP connect / TLS handshake / HTTP GET per endpoint protocol against the derived resolution) — the provider host's own gate is authoritative for its facts. S8.5.4 The gates are written as `[resolved.gates.<k>] healthy = [...]  completed = [...]  facts = [...]`. S8.5.5 `ciu up` succeeds when every wave's gate passed and every non-`one_shot` service brought up on this host is `Running` at the end.

### S8.6 Primary service, variant service, health
S8.6.1 A stack with more than one service MUST mark exactly one `primary = true`; a single-service stack's only service is primary. S8.6.2 The primary service is the default **variant service** (S5.2): unless a variant names another service, it is the exec target and image source (S16.4), the target of endpoint-less bindings (S8.2), the container in which hook-provided facts are probed unless the hook entry names a service (S6.10), and the service whose health stands for the capability in gate predicates. S8.6.3 **healthy**: a container whose rendered block declares a `healthcheck` is healthy when Docker reports `State.Health.Status == "healthy"`; a container without one is healthy when `State.Running` is true; a `one_shot` service is **completed** when its container exited with code 0. A replicated service is healthy when every replica is. Gate providers that are not `one_shot` MUST declare a healthcheck (S11.5).

### S8.7 Bringing up one `ciu_stack` Realization (the pipeline, in order)
For each Realization of a wave, in name order, on the host it is placed on:
1. **hostdirs** — create and chown per S6.8.
2. **`pre_secrets` hooks** (S12) — may emit `state` and `secrets`; a `secrets` output here is the only way to satisfy a `from = "hook"` secret (S10.1) in the same run.
3. **secrets** — materialize per S10 (store under the instance lock); write temp copies (S10.7).
4. **`pre_compose` hooks** — may emit `state` and `secrets`; every `state` output is merged into `ciu.state.toml` immediately and is visible to steps 5–8 of this run.
5. **config files** — render per S6.9 with the S3.5.1 context (including step-4 state), validate schemas, scan (S2.4.2).
6. **compose** — render, inject, validate per S11; write `ciu.compose.yml`.
7. **`docker compose -p <compose_project> --project-directory <location> -f ciu.compose.yml up -d --remove-orphans`**.
8. **`post_compose` hooks** — run after every service of this Realization that any edge targets is healthy or completed (S8.5.1's budget); may emit `state`, `secrets`, `facts`.
Then, once every Realization of the wave completed step 8: the wave gate (S8.5.1 for remaining providers) and the fact probes for edges into the next wave (S8.5.2). `external`: optional reachability probe of each endpoint (`ciu up --probe-external`), otherwise nothing. `joined`: verify the reference (S9.5) and attach the consumer's containers to the reference network at compose time (S11.4). `ciu render` performs steps 1, 5 and 6 only (no hooks, no secrets beyond what the store already holds — a missing value is an ERROR naming `ciu up`).

### S8.8 Derived tables
`[resolved] waves = [[…], …]`, `edges = [{from, to, kind}]` with `kind ∈ bind|depends|secret→vault|secret→minter|network|pki`, and `gates.<k>` (S8.5.4).

---

## S9 Realness

### S9.1 Levels
`live` (the real thing; its init graph runs), `seeded` (a prepared Realization the project owns whose `pg:`/`minio:`-class facts hold by construction — declared in `provides`, no one-shot job needed; `vault:secret/*` facts MUST be minted by a hook that writes the prepared credentials to Vault, declared as the hook entry's `provides`; a `one_shot` service it does declare runs normally), `simulated` (a stub implementing the contract's protocol), `mock` (in-process double: no Realization, no resolutions, no edges).

### S9.2 Declaration
`[realness] default = "<level>"` (required; `ciu init` writes `"live"`), `pin.<logical> = "<level>"` (committed per-service pins, optional). `[service.<n>.<level>]` variants per S5.2; `[service.<n>.mock]` is declared as an empty table (`mock = {}`).

### S9.3 Selection
S9.3.1 For every LogicalService in the selected bundles, the level is the first defined of: `--realness <n>=<level>` on the command line; `[realness.pin].<n>`; `[realness].default`. S9.3.2 The chosen level MUST have a variant on the service (`[S9.3] service 'payment_api' has no 'simulated' variant`). S9.3.3 The selection is written to `[resolved.services.<n>] level, realization, service` (`realization`/`service` absent for `mock`). S9.3.4 A binding to a service selected at `mock` yields no edge and no resolution (S5.3.5).

### S9.4 Immutable record (per layout)
S9.4.1 The first `ciu up` for a layout writes the resolved selection of every LogicalService in the deploy set into the generated file as `[ciu.instance.realness.<layout>]` (CIU-owned, S14.2). S9.4.2 The record is a **constraint on selection, not a source**: thereafter, a selection per S9.3.1 that differs from that layout's record for any recorded service is an ERROR naming the source of the difference (`[S9.4] layout 'local' already runs main_db=seeded; --realness main_db=live conflicts; ciu clean --vanilla to reselect`, or `… pin main_db=live conflicts …`). Services not yet in the record (added bundles) are selected per S9.3 and appended. S9.4.3 `ciu clean` preserves the record; `ciu clean --vanilla` clears the current layout's record. S9.4.4 `ciu push` strips the records of other layouts from the bundle (S17.3).

### S9.5 Joined Realizations
S9.5.1 `[realization.<n>] kind = "joined" instance = <ref> service = <X>` in an instance file borrows `X` from the reference instance `<ref>` (an instance label per S14.7.2, or an absolute path of a checkout). S9.5.2 The joiner's variant row `[service.<X>] <level> = "<n>"` MUST name the level the reference **actually runs** (read from the reference's rendered `[resolved.services.<X>]` at check/up time); a mismatch is an ERROR (`[S9.5] reference 'primary' runs main_db=seeded, this instance declares live`). CIU records the reference's level in the joiner's record (S9.4). S9.5.3 The reference MUST be up (its rendered file present and the borrowed variant service healthy per S8.6.3) or `ciu up` refuses. S9.5.4 CIU takes a shared lock on the reference's checkout directory (S14.4) while reading its rendered file and, for a joined `vault`, its store (S10.3.3). S9.5.5 `ciu instance add --join <ref> --services a,b,…` appends these tables to `ciu.instance.toml` of the current checkout with a round-trip TOML writer that preserves the operator's other tables and comments (it does not create a git worktree); a hand-written instance file is equivalent. S9.5.6 `ciu clean` on a reference whose instance network still has containers of another instance attached (`docker network inspect`) is an ERROR naming the joiner.

---

## S10 Secrets

### S10.1 Sources — the `from` key
| `from` | keys | meaning | store |
|---|---|---|---|
| `vault` | `path` (required), `field` (optional) | read from Vault at deploy time | copied into the store; refreshed on every `up` |
| `generate` | `store` (`local` default, or `vault`), `path` (required when `store = "vault"`), `length` (integer, default 32), `charset` (`alnum` default, `hex`, `printable`) | generate once; with `store = "vault"` only when the Vault path is absent, then read | store (+ Vault) |
| `ask` | `var` (`envname`, required) | from `CIU_SECRET_<var>` in CIU's environment, else the store, else an interactive prompt | store |
| `file` | `path` (absolute, required) | read a pre-provisioned file on the host that runs `up` | not stored (path recorded) |
| `host` | `entry` (required) | the host-scoped secret `[hosts.<h>.secrets.<entry>]` of the host this stack is placed on (S10.4) | read from `[secrets.hosts.<h>]` |
| `ephemeral` | `length`, `charset` | generated per run by the host that runs `up`, never stored | — |
| `hook` | — | produced by a `pre_secrets` hook of the same stack in the same run (a credential fetched at deploy time — a runner registration token); the hook emits it under this key (S12.3); absent at materialization → ERROR naming the key and the hooks that ran | store |
S10.1.1 Secret keys match `name`. S10.1.2 A `from = "generate", store = "vault"` secret at `path` derives the fact `vault:secret/<resolved path>` provided by its service (S8.3). S10.1.3 A `from = "host"` secret on a stack placed by any layout on a host without that entry is an ERROR at check time (`[S10.1] host 'rs1002' declares no secret 'tls_cert_pem'`). S10.1.4 A `from = "ephemeral"` secret consumed by services placed on two hosts of one layout is an ERROR (the value could not agree); use `generate`. S10.1.5 The `path` of a `vault` or vault-stored `generate` secret is either a key of `[vault.paths]` (no `/` in it) or a literal KV path (contains `/`); a `/`-free value that is not a `[vault.paths]` key is an ERROR naming the closest keys (`[S10.1] path 'postgres_controler_password' is not in [vault.paths] (did you mean postgres_controller_password?)`). The resolved literal path is what the store, the facts and the minter edges use. S10.1.6 A key `directive` is refused with a message naming Appendix A's mapping.

### S10.2 Declaration — `[ciu_stack.<svc>.secrets.<key>]` and `[ciu_stack.secrets.<key>]`
Keys: `from` (required, S10.1) and its source keys; `delivery` (`file | env | configfile | native | hook | none`, **required**); `env_name` (`envname`; required when `delivery = "env"`); `mode` (default `"0400"`); `uid` (default `instance.user_uid`); `enabled` (bool or `[project.control]` flag name, default true — a disabled secret is neither materialized nor delivered and derives no edge or fact). S10.2.1 `file`: the value is written to a temp copy (S10.7) and mounted read-only at `/run/secrets/<key>` in the declaring service (service-level) or in every service of the stack whose rendered compose block references `/run/secrets/<key>` (stack-level). S10.2.2 `env`: the value is passed as `env_name` through the compose process environment (S11.6); the rendered compose MUST reference it as `${env_name}`; ciu check stage 9 lists every env-delivered secret (restart-bound). S10.2.3 `native`: the application fetches the secret itself; CIU materializes nothing and delivers nothing; the row exists so the dependency (and the edges) are declared. S10.2.4 `none`: minted or registered by this stack for others; nothing is delivered here and the value is not handed to hooks. S10.2.5 `hook`: materialized and handed to this stack's hooks in their context (S12.2), delivered to no container. S10.2.6 `configfile`: the value is available only through `secret("<key>")` in the declaring service's config-file templates (S3.5.1, S6.9); the rendered config file is then a secret-bearing artifact — mode 0400, owner per `uid`, exempt from the S2.4.2 scan by declaration, and removed with the temp copies on `down`/`clean`. `secret()` is never available in compose templates. S10.2.7 A secret key declared at both stack level and on a service of the same stack is an ERROR. S10.2.8 The keys `consumed_by` and `produced_by` are refused (`[S10.2] produced_by is derived from the minter edge; consumed_by = "hook" is delivery = "hook"`).

### S10.3 Vault pointer and consumer paths
`[vault] service = "<logical>"` (required when any secret with `from = "vault"` or `store = "vault"` exists anywhere in the deploy set — `[S10.3] vault secrets present but [vault].service is not declared`), `token_file` (path, optional; token source #2 after `VAULT_TOKEN`, before the bootstrap token in the store), `[vault.paths.<name>] = "<kv path>"` (a reference table CIU **reads** to resolve `path` keys, S10.1.5; every value MUST be a literal KV path; a value no secret references is INFO). S10.3.1 CIU's Vault client connects through the pseudo-consumer `ciu`'s implicit binding to the `vault` LogicalService's first `http`/`https` endpoint (S7.8.7). S10.3.2 The Vault bootstrap values (root token, unseal keys) are stored at `[secrets.<vault realization>.<variant service>.root_token|unseal_keys]` by the vault stack's hook through the hook output contract (S12.3), never in state. S10.3.3 Token sources, in order: `VAULT_TOKEN`; `token_file`; the bootstrap token in this instance's store; for a `joined` vault, the bootstrap token in the **reference's** store, read under the reference's shared lock (S9.5.4). None → ERROR naming the four sources.

### S10.4 Host-scoped secrets
`[hosts.<h>.secrets.<entry>]` (S7.2) accept `from ∈ ask | generate | file`; they are materialized on the CIU host (prompting if needed) before a push (S17.3), stored under `[secrets.hosts.<h>.<entry>]` (the realization name `hosts` is reserved), and pushed only to host `<h>`. A service consumes one through a secret declared with `from = "host", entry = "<entry>"` and any delivery (S10.1).

### S10.5 TLS material for `mtls`/`tls` networks
S10.5.1 For every Realization `C` with a resolution over a network `N` with `tls ≠ none`, and for every Realization `R` with an endpoint reached over such a network, CIU derives stack-level `file` secrets `tls_cert`, `tls_key` (both) and `tls_ca` (consumers, and providers when `mtls`) with `from = "vault"` at paths `pki/<N>/<realization>/{cert,key,ca}`; resolutions and the provider's endpoint table carry their `/run/secrets/tls_*` paths. S10.5.2 The `pki` LogicalService's provider MUST provide the fact `pki:issuer/<N>` (a hook entry's `provides`); the derived `pki/<N>/…` paths are satisfied by that provider (S8.3), and the **pki** edge orders it. Issuance itself is the hook's job (CIU runs no CA).

### S10.6 The store — `ciu.secrets.toml`
```toml
[secrets.<realization>.<service>.<key>]   # or [secrets.<realization>.<key>] for stack-level secrets
value = "…"  source = "vault:db/postgres/controller_password"  created = 2026-09-02T10:11:12Z   # source: <from>[:<resolved path|var|entry>] or hook:<script>
```
S10.6.1 One file per instance, mode 0600, written atomically (temp + rename) under the instance lock (S14.4). S10.6.2 `ephemeral` values are never stored. S10.6.3 `ciu secrets show [--values]`, `ciu secrets reset <realization>[.<service>][.<key>]`, `ciu secrets host <h> <entry>` (materialize one host-scoped entry), `ciu migrate --secrets` (imports v7 stores; refuses to re-mint). S10.6.4 A value is refreshed from Vault on every `ciu up` for `from = "vault"`; generated values are generated once and kept.

### S10.7 Temp copies
S10.7.1 For `file` delivery CIU writes `<location>/ciu.secret-copy.<svc>.<key>` (stack-level: `ciu.secret-copy.<key>`) before compose runs, mode/uid per S10.2, and removes them on `ciu down`/`clean`. S10.7.2 The file is rewritten in place on refresh so a running container's bind-mounted inode observes the new value.

### S10.8 Static rules (ciu check stage 9)
`from` and `delivery` present on every secret; source keys complete per S10.1; `env_name` present and unique per stack for `env` and disjoint from binding variables (S6.4.6); vault pointer per S10.3; every `vault` path resolves (S10.1.5) and has a minter or an asserting Realization (S8.3); `host` entries exist on every placement host (S10.1.3); `ephemeral` not shared cross-host (S10.1.4); secret-shaped keys outside `secrets` tables refused (S6.10); env-delivered secrets listed as WARN.

---

## S11 Compose rendering

### S11.1 Pipeline
For each `ciu_stack` in the deploy set (in wave order), CIU renders `ciu.compose.yml.j2` with the compose context (S3.5.1), parses the result as YAML, removes disabled services (S6.7), applies the injections of S11.4, validates per S11.5, and writes `ciu.compose.yml`. The rendered file is the ONLY file passed to `docker compose` (`-f ciu.compose.yml -p <compose_project> --project-directory <location>`).

### S11.2 Template obligations
A template MUST write, per service: `image` (from `ciu_stack.<svc>.image`), and whatever compose fields the service genuinely needs (`command`, `environment`, `volumes`, `cap_add`, `restart`, `user`, `healthcheck.test`, extra `labels`, `ulimits`, `sysctls`, `devices`, `build` for project-built images — `image` stays required as the tag `ciu build` produces). It MUST use the service key as the compose service name (`services: <svc>:`), or `{{ replica.compose_key }}` per replica block (S6.5); CIU renames the former to `compose_key` (S4.2).

### S11.3 Template prohibitions
A template MUST NOT write: `container_name`, `hostname`, `networks` (service-level or top-level), `secrets`, `depends_on`, `cgroup_parent`/`mem_limit`/`cpus`/`cpu_shares`/`oom_score_adj`/`pids_limit`, `ports`, `expose`, a label under `ciu.*` (S4.5.2), `name:` under `volumes:` that does not start with `{{ instance.project }}-{{ instance.id }}-`, `${VAR:-default}` or `${VAR:?…}` forms, `{{ env.* }}`, an `environment` entry whose name is injected by a binding (S6.4.6). A violation is an ERROR naming the field (`[S11.3] template sets container_name for 'postgres'`), except `container_name`/`hostname` equal to the derived value (S4.3.2, removed silently). Replicated services (S6.5) MUST be emitted by iterating `ciu_stack.<svc>.identity.replicas`, one block per replica.

### S11.4 Injections (applied to the parsed YAML)
Per service (by service key, and per replica block): `container_name` and `hostname` (S4.2; `hostname` omitted when `host_network = true`); `networks: { <instance network>: { aliases: [<service compose_key>, <replica compose_key>, <container_name>] + aliases } }` plus the reference instance network for services of a Realization that has resolutions to `joined` services (omitted for `host_network`); `labels` (S4.5); `environment` entries `CIU_PROJECT`, `CIU_INSTANCE`, `CIU_REALIZATION`, `CIU_SERVICE`, `CIU_REPLICA` (when replicated), `CIU_IMAGE_REVISION` (the git revision `ciu build` stamped for a project-built image, else absent), and every `env`-delivered binding's variables (S6.4); `depends_on: { <sibling compose_key>: { condition: … } }` from `depends_on` (`service_completed_successfully` when the sibling is `one_shot`, else `service_healthy` when the sibling's RENDERED block carries a `healthcheck`, else `service_started`; a disabled sibling is dropped with a WARN); `healthcheck.interval/timeout/retries/start_period` from the merged `health` table when the template wrote `healthcheck.test` (S6.6.4); `secrets: [ { source: <key>, target: /run/secrets/<key>, mode } ]` and the top-level `secrets: { <key>: { file: <absolute physical path of the temp copy> } }` for `file` delivery; `ports:` per S7.4 (derived publications and `publish = "host"`); read-only bind mounts of rendered config directories (S6.9, absolute physical paths); governance fields (S13.3); `network_mode: host` when `host_network = true`. Top-level: `networks: { <instance network>: { external: true, name: … } }` (and the reference network); `name: <compose_project>`. Every path CIU writes into the file is absolute under `physical_repo_root`.

### S11.5 Validation of the rendered compose
Secret-free scan (S2.4.2); every `${VAR}` reference is in the allowed set (S11.6); every service has an `image`; a `healthcheck` is present for every gate provider that is not `one_shot` (`[S11.5] 'postgres' is a provider but declares no healthcheck`); YAML re-serialized deterministically.

### S11.6 Compose process environment and `${VAR}`
S11.6.1 The environment passed to `docker compose` consists of exactly: `project.compose_env.*`, the selected bundles' `compose_env`, `COMPOSE_PROFILES` (from `compose_profiles`), and the `env_name` values of `env`-delivered secrets. Nothing else is forwarded; a template that needs a runtime switch declares it as data (a consumer sub-table) or as a secret. S11.6.2 A `${VAR}` in the rendered compose that is not in that set is an ERROR (`[S11.6] compose references ${FOO}, which no source provides`).

### S11.7 Rendered artifacts and inspection
`ciu.compose.yml` (S2.2), `ciu.rendered/`, temp secret copies. `ciu render` writes them without deploying; `ciu up` writes them under the instance lock immediately before compose runs (S8.7 step 6). `ciu render --show-injected [--realization r]` prints, per service, the unified diff between the template's output and the final artifact, so the boundary between a template bug and a CIU bug stays inspectable (the reason v7 S8.1 preferred overlay files).

---

## S12 Hooks

### S12.1 Lifecycle
`[hooks] pre_secrets`, `pre_compose`, `post_compose`: lists of entries (S6.10), each an executable path relative to the stack directory (or absolute). Each runs, in list order, at its step of S8.7, as a subprocess with the working directory set to the stack directory, `stdin` = the context (S12.2), `stdout` = its outputs (S12.3), `stderr` passed through to CIU's log. A missing script or a non-zero exit aborts the run (`[S12.1] hook post_compose_vault.py exited 3`). The process environment of a hook is CIU's own environment minus every `CIU_SECRET_*` variable; a hook MUST NOT read identity facts from it (I5).

### S12.2 Context
Hooks receive a JSON document on stdin (`ciu_hook_context`, `schema_version: 2`) containing: `phase`; `project`, `instance` and `host` (S3.5.2–S3.5.3); `stack_dir`; `realization` (the name of the stack's Realization); `services` (this stack's services as the compose render sees them: identity, health, endpoints with `published_on`, and every binding's resolution regardless of its delivery — a hook is not a container); `secrets` (this stack's secret keys and their materialized values for keys with `delivery ∈ hook | file | env | configfile | native`; `none` excluded); `state` (S6.10); `deploy_set` (the merged view of every Realization in the deploy set, S3.6, read-only); and `resolved` — the whole derived table of S3.7 (waves, edges, selection, `networks` incl. proxy `fqdn`s, every binding resolution, and `bindings.ciu.vault`, CIU's own vault resolution from the host's vantage point, S7.8.7 — the address a hook that writes Vault uses). They MUST NOT read `ciu.env`, the process environment for identity facts, or the rendered global file directly — every fact they need is in the context.

### S12.3 Outputs
A hook MAY print a JSON document on stdout (`schema_version: 2`): `{"state": {...}}` merges into `<location>/ciu.state.toml` (S6.10; secret-shaped keys refused) and is visible to every later step of the same run (S8.7); `{"secrets": {"<key>": "<value>"}}` stores values under `[secrets.<realization>.<primary>.<key>]` with `source = "hook:<script>"` (S10.6) — the only way a hook writes a secret; a key that is declared with `from = "hook"` is thereby satisfied (from a `pre_secrets` hook, S8.7 step 2), a key declared with any other source is an ERROR (`[S12.3] hook emits 'redis_password' but its source is generate`), an undeclared key is a bootstrap value (S10.3.2); `{"facts": ["…"]}` asserts the entry's `provides` facts were created (recorded, then probed per S8.5). Anything else on stdout is an ERROR naming the hook (a hook that wants to log writes to stderr).

### S12.4 Validation hooks
S12.4.1 A hook script MAY support the argument `--validate`: `ciu check` stage 13 runs every hook of every stack in the deploy set with `--validate` and the S12.2 context minus secret values on stdin, and expects `{"findings": [{"severity": "WARN"|"ERROR", "message": "…", "rule": "…"}]}` on stdout (an empty list or no output = no findings; a hook that exits non-zero under `--validate` is itself an ERROR). S12.4.2 Severities map through `ciu.exit_on` (S15.2). S12.4.3 Validation runs MUST be side-effect-free; CIU runs them with a wall budget of 30 s each.

### S12.5 The helper library — `ciu.hookkit`
S12.5.1 CIU ships an importable Python package `ciu.hookkit` with no dependency on the rest of `ciu` beyond the standard library, so a hook that imports it runs wherever `ciu` is installed and can be vendored. It MUST provide at least: `context()` (parse stdin into an object with attribute access; `ctx.services.<svc>.identity.container_name`, `ctx.services.<svc>.binds.<local>.host`, `ctx.secrets["key"]`, `ctx.state`, `ctx.phase`); `emit(state=None, secrets=None, facts=None)` (write the S12.3 document once, at exit); `wait_healthy(service, timeout=None)` and `wait_tcp(host, port, timeout=None)` and `wait_http(url, timeout=None, expect=range(200, 300))` (bounded polls that raise with the last observed state — a hook MUST NOT hand-roll a poll loop, the CIU-4 rule); `docker_exec(service, argv, input=None)` (exec into one of this stack's containers by service key); `secret_file(key)` (the temp-copy path of a `file`-delivered secret); `is_validate()` and `findings()` (the `--validate` protocol). S12.5.2 The hook templates CIU ships (`ciu init --stack`) are written on `ciu.hookkit`. S12.5.3 `ciu hook run <realization> <phase> [--validate] [--dry-run]` runs one stack's hooks of one phase outside `ciu up` with the same context, for authoring and tests.

---

## S13 Resource governance

### S13.1 Vocabulary (shared by stacks and gate lanes)
The **resource key set** `RK` = `memory_max`, `memory_swap_max`, `memory_high`, `memory_low`, `memory_min`, `cpu_weight` (1..10000), `cpu_max` (`"<quota> <period>"` or `"max"`), `io_weight` (1..10000), `pids_max`. Sizes per S1.4. Every key is named after the cgroup-v2 controller file it governs (`_` for `.`); there are no alternative spellings — S13.3 is the only place a key is mapped onto a compose field.

### S13.2 `[governance]` (global base; per-stack `[governance]` shallow-merges over it)
Keys: `enabled` (bool, default false), `cgroup_parent` (slice name; `""` = `$CGROUP_PARENT_DEV_BACKGROUND`, required non-empty when enabled), `ksm_optin` (`builtin` | path), `exempt_services` (list of `<realization>.<svc>`), `memory_profile.default.ksm` / `memory_profile.services.<svc>.ksm` (`preload|wrapper|off`), all of `RK`, plus `io_read_iops_max`, `io_write_iops_max`, `io_read_bps_max`, `io_write_bps_max` (integers; `0` = derive from the baseline), `device` (block device; `""` = autodetect), `baseline_path` (path; `CIU_GOV_BASELINE_PATH` overrides). S13.2.1 `memory_min` is preflight-only: host `MemAvailable` below the sum of the deploy set's `memory_min` is an ERROR before compose runs. S13.2.2 Unknown keys are an ERROR (S3.8.1).

### S13.3 Application to containers
With `enabled = true`, CIU injects into every non-exempt service block: `cgroup_parent`, `mem_limit` ← `memory_max`, `memswap_limit` ← `memory_swap_max`, `mem_reservation` ← `memory_low`, `cpu_shares` ← `cpu_weight` (same numeric scale), `cpus` ← `cpu_max` (quota ÷ period as a decimal; `"max"` injects nothing), `blkio_config` ← `io_*`, `pids_limit` ← `pids_max`; `memory_high` is written to the container's cgroup after start (compose has no field) and recorded in the effective table. S13.3.1 KSM: `ksm_optin = "builtin"` builds the shim into `$XDG_CACHE_HOME/ciu/ksm/` and bind-mounts it; `wrapper`/`preload`/`off` per service (`CIU_KSM=off` disables for one run).

### S13.4 Effective governance (derived)
`[resolved.governance.<realization>.<svc>]` lists the effective values applied to each container (after merge and exemptions).

---

## S14 Instances

### S14.1 Lifecycle
`ciu instance init` → (`ciu check`, automatic) → `ciu up` → `ciu gate …` → `ciu down` | `ciu clean [--vanilla]`. S14.1.1 `init` writes the generated file (S14.2), creates `ciu.instance.toml` when absent (with `--layout`/`--bundles`/`--label` filling the operator table; a project with exactly one layout needs no `--layout`, S7.6.1), and on a git checkout writes the instance record (S14.7). S14.1.2 `up` renders, locks, creates the instance network (idempotent, labelled per S4.5), deploys per S8, writes the realness record (S9.4). S14.1.3 `down` stops containers and removes the temp secret copies; everything else stays on disk. S14.1.4 `clean` removes containers, named volumes labelled to the instance, temp secret copies, rendered artifacts (the S2.3 list minus the instance file, the generated file, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.instance.json`, `ciu.state.toml`, `ciu-data/`), disconnects CIU's own container from the instance network when `auto_connect_network` attached it, and removes the network; `--vanilla` additionally clears the current layout's record, the store, `ciu.state.toml` files, `ciu-data/` (S6.8.3) and the instance record; it refuses while another instance's containers are attached to this instance's network (S9.5.6).

### S14.2 Instance file and generated file
The **instance file** `ciu.instance.toml` is the operator's (CIU appends or replaces whole tables with a round-trip writer, S9.5.5, and never rewrites it whole):
```toml
[ciu.instance]
layout = "local"                # required for every mutating verb (S7.6.1)
bundles = ["all", "test"]       # default bundle selection for `ciu up`
label = "primary"               # unique per git family (S14.7.2); `ciu instance list` shows it; never part of an identity
[ciu.instance.host_ports]       # optional per-instance host-port overrides: "<realization>.<svc>.<endpoint>" = port
"cadvisor.cadvisor.http" = 18080
# plus [realization.<n>] kind = "joined" … and [service.<n>] <level> = "<n>" rows for joins (S9.5)
```
The **generated file** `ciu.instance.generated.toml` is CIU-owned, plain TOML, rewritten whole by CIU, merged after the instance file:
```toml
[ciu.instance.generated]        # instance IDENTITY — identical on every host of a layout (the file travels with the bundle)
instance_id = "98535c"
[ciu.host.generated]            # HOST-LOCAL facts — regenerated on each host by `ciu instance init --host <h>`
name = "rs1002"                 # which [hosts.<h>] this machine is (the `local = true` host without --host)
hostname = "rs1002"             # the machine's own hostname (lease holder, S14.6.2)
repo_root = "/opt/ciu/current"
physical_repo_root = "/opt/ciu/current"
env_type = "native"             # devcontainer | native | github-actions
user_uid = 1000
user_gid = 1000
docker_gid = 988
[ciu.instance.build]            # written by `ciu build` (S18): version, time, image digests
build_version = "2026.09.02-9f3c1a2"
build_time = "2026-09-02T11:02:41Z"
[ciu.instance.realness.local]   # S9.4, one table per layout
main_db = "live"
```
S14.2.1 A hand edit to the generated file is overwritten without notice (the file carries that warning). S14.2.2 `ciu instance init --host <h>` sets `[ciu.host.generated].name`; without the flag, the host with `local = true` (S7.2.1) is assumed and MUST exist. S14.2.3 The instance file and the generated file ship with the bundle on `ciu push` (S17.3) so `instance_id` is identical across hosts; `[ciu.host.generated]` is then regenerated on the target by `init --host <h>` (activate bootstrap, S17.4). S14.2.4 `require_fqdn = true` (S3.4.7) makes a rendered host without `fqdn` (S7.2) an ERROR at init.

### S14.3 Verb classes
- **Mutating (exclusive lock):** `instance init|add|remove|reap|lease`, `up`, `down`, `clean`, `render`, `build`, `push`, `activate`, `secrets reset|host|rotate-bootstrap`, `migrate`, `dev`.
- **Gate (shared lock for the duration of the lanes):** `gate` — several `ciu gate` processes coexist under S16.6 admission; a `sequence` lane runs its members in-process (S16.5.4), so no gate ever spawns another `ciu gate`.
- **Read-only (shared lock while reading the rendered file, none afterwards):** `check`, `env print`, `instance list|show|exec`, `diagnose`, `status`, `secrets show`, `provenance`, `schema`, `doctor`, `version`, `help`.
- **Lock-free:** every verb in a zero-instance project (S16.11); `init`; `schema`; `version`; `help`.

### S14.4 The instance lock
S14.4.1 The lock object is the **checkout root directory**: CIU opens it (`O_RDONLY | O_DIRECTORY`) and takes `flock(LOCK_EX)` for mutating verbs, `flock(LOCK_SH)` for gate and read-only verbs. The directory's inode is stable for the life of the checkout; no file CIU or git rewrites is ever the lock. S14.4.2 The rendered file is written by temp-file and atomic rename under `LOCK_EX`; a reader that finds no `ciu.resolved.toml` reports *not rendered* (read-only verbs say so; the gate refuses in a project with Realizations, S16.11). There is no completion marker and no torn state. S14.4.3 Lock order (deadlock-free by construction): instance lock → git-common-dir registry lock (S14.7) → a joined reference's instance lock (`LOCK_SH`, S9.5.4). The store (S10.6) is written under the instance lock; there is no separate secrets lock and no per-stack lock. The automatic `ciu check` inside a mutating verb reuses the verb's descriptor and takes no lock of its own. S14.4.4 Contention: fail fast (`[S14.4] instance 98535c is locked by another ciu process; pass --wait`), naming the holder's pid and verb when `/proc/locks` and `/proc/<pid>/cmdline` make them available; `--wait[=<duration>]` blocks. A dead holder releases the lock by itself (`flock` is kernel-owned); there is no `lock break` verb. S14.4.5 `git clean -x` or a manual delete of `ciu.resolved.toml` while an instance is up does not affect the lock; readers see *not rendered* until the next `ciu render`, which regenerates it identically (S3.7.3). S14.4.6 On a filesystem where `flock` on a directory is unsupported (some network filesystems), CIU refuses every locking verb naming the filesystem (`[S14.4] cannot lock <root>: flock unsupported on nfs`) rather than running unlocked.

### S14.5 Interleavings prevented
Two `up`; `up` ‖ `gate` (a gate lane exec-ing into a container being recreated); `instance init` ‖ render; `clean` ‖ `up`; two first-`up` realness selections; `migrate` ‖ `up`; a hook writing the store while `secrets reset` runs.

### S14.6 `[ciu.instances]` — budget and lease (one closed key set)
`max_concurrent` (integer ≥ 1, optional; ambient override `CIU_MAX_CONCURRENT_INSTANCES`), `lease_ttl_hours` (number > 0, optional; absent = no lease). S14.6.1 The budget counts instances of one git family (S14.7) with a live record; `up` beyond the budget is an ERROR naming the holders. S14.6.2 A lease is renewed by every mutating verb and expires after `lease_ttl_hours`; the holder is recorded as `ciu@<host.hostname>:<instance_id>`; `ciu instance lease --extend [D] | --perpetual | --release` manages it; `ciu instance reap` removes expired instances' containers and records (never their checkouts). S14.6.3 `ciu instance exec --env <e> -- <cmd>` runs a command in a gate environment (S16.4) after the environment's mount proof (S16.4.3) — there is no separate exec-target table.

### S14.7 Instance registry
S14.7.1 Every checkout in a git family registers `ciu.instance.json` (gitignored; in S2.3's list) with `schema_version`, `instance_id`, `path`, `label`, `created`, `lease_until`; the family is enumerated through the git common directory (a lock file `ciu-instances.lock` there serializes allocation and budget). The `instance_id` is duplicated from the generated file on purpose: the record is mutated by other checkouts (reap, lease), the generated file only by this checkout's CIU. S14.7.2 An instance label MUST be unique within a git family (`instance init`/`add` refuse a duplicate); a join's `instance` value (S9.5.1) resolves to a registered label, else to an absolute path; nothing else.

---

## S15 `ciu check`

### S15.1 Invocation
`ciu check [--live] [--layout L] [--host H] [--bundles …] [--realness …] [--graph] [--gates] [--json]`. Runs automatically before every mutating verb with the verb's selection (`--no-check` skips; the skip is printed). Side-effect-free except stage 15's live probes (network reads).

### S15.2 Severities and exit
Findings are `ERROR`, `WARN` or `INFO`. `ciu.exit_on` ∈ `WARN|ERROR|NEVER` decides the exit code of the standalone verb: `WARN` → non-zero on any ERROR or WARN; `ERROR` → non-zero on errors only; `NEVER` → always zero (findings still printed). Inside a mutating verb, any ERROR aborts the verb and WARNs are printed; `ciu.exit_on` does not make a WARN abort a deploy. Every finding names its rule (`[S<n>.<m>]`), the file and table or line where applicable, and — for referential findings — the candidate names that would have matched (edit distance ≤ 2).

### S15.3 Stages
A stage runs unless a stage it depends on produced an ERROR (dependencies in the last column); independent stages run so that one typo does not hide unrelated findings.
| # | stage | rules | needs |
|---|---|---|---|
| 1 | files | S2.1–S2.3 (gitignore list; instance and generated file present — skipped for `instance init` and in zero-instance projects; no `.ciu/` — skipped for `migrate`; no `.j2` declaration files, S3.2.6) | — |
| 2 | parse | every declaration file parses as TOML; S2.4.1 secret-free scan of declarations and templates | — |
| 3 | schema | S3.3, S3.8 closed key sets, types, vocabularies, grammars, `revision`; reserved names (S1.4); refused legacy keys (S6.2.3, S10.1.6, S10.2.8) | 2 |
| 4 | references | every name resolves: `realized_by`, variant `service`, binding `to` (service and endpoint), `depends_on` (siblings), `enabled` flags, `exec_in`, `image_from`, `requires.healthy`, `resources.shared`, `vault.service`, `network.realized_by`, `network.pki`, `allow_from`, `bundles.services`/`includes`, `layouts.hosts.*.bundles`, `layouts.hosts.*.reach`, joined `instance`/`service`, `testing.inherit`; `location` exists with both files and its root is `ciu_stack`; no shared `location` (S5.4.3); exactly one primary (S8.6.1); endpoint names unique per stack (S6.3.1); no unknown scalar on a service table (S6.2.1); `vault.paths` references (S10.1.5) | 3 |
| 5 | contracts | S5.3 conformance of every variant against the derived contract; minter resolution for every vault path (S8.3); provided-but-unconsumed facts (INFO) | 4 |
| 6 | graph | S8.4 cycles; waves; bindings to mocked services with data (S5.3.5); `requires`/`binds` local-name clashes (S6.4.4) | 4 |
| 7 | topology | S7.2.2 hosts exist; `local` uniqueness; addresses for every `address` network in `reach` (S7.6.4); placement (S7.6.3, S7.6.5); every resolution with data resolves (S7.8 step 5 refusals); proxy networks have `fqdn`, `realized_by`, and an `https` host-published endpoint; TLS networks have `pki` with `pki:issuer/<n>`; published (network, host_port) pairs unique per host (S7.4.5); push order consistent with cross-host edges (S17.2); `fqdn` present when required (S14.2.4) | 4 |
| 8 | identity | S4.3 uniqueness with the ambiguity message (S4.2.5); template `container_name`/`hostname` equal derived or absent; `ciu.*` labels (S4.5.2); compose keys qualified; name length (S4.3.3) | 4 |
| 9 | secrets | S10.8 | 4 |
| 10 | realness | S9.3.2; consistency with the layout's record (S9.4.2); joined references resolvable and level-consistent (S9.5.2, requires the reference's rendered file) | 4 |
| 11 | governance & resources | S13 keys and ranges; slice resolvable; `memory_min` headroom (WARN statically, ERROR at up); `exec` lane caps ≤ target governance (S16.6.4) | 3 |
| 12 | testing | S16: environments/requires/shared/binds resolve; `required_env ⊆ forward_env` for container environments; `sequence` members exist and are acyclic; `[testing.judge]` present iff an assay lane exists; when the judge is reachable in an environment: `assay lanes --json` → every `assay_lane` exists, its `external_tools`/`argv0` are present in the environment, its `env_required ⊆ forward_env`; `evidence_dir` ignored and writable; `inherit` target parses and carries only `[testing.environments]` (S16.2.1) | 3 |
| 13 | hooks | S12.4 `--validate` | 4 |
| 14 | registry validator | `ciu.registry_validator` | 3 |
| 15 | live (`--live`) | S8.5 probes for the selected waves; cross-host reachability; joined references up; host ports free (S6.3.2); mount proof for `exec` environments (S16.4.3) | 7 |
In a zero-instance project (S16.11) only stages 1–3, 11, 12 and 14 run.

### S15.4 Output
Human-readable by default (grouped by stage, ERRORs first); `--json` emits the S18.4 envelope with `findings: [{stage, rule, severity, message, file, table, candidates}]` and the derived tables of S3.7 under `resolved`. `--graph` prints waves and edges (kind-annotated). `--gates` restricts to stage 12 plus the gate's own preconditions (S16.5). `--layout L` additionally prints the **publication table** (every endpoint published on every host of `L`, with the binding that caused it) and the **bundle table** (what `ciu push` would ship to each host, S17.3).

---

## S16 The gate — `ciu gate`

### S16.1 Model
A **lane** runs one command, one judge invocation, or a sequence of other lanes, in an **environment**, subject to **preconditions** (realness, service health, provenance), under **resource caps** enforced through cgroup v2 and an **admission** step, and produces a **LaneResult**. Lanes are declared in `ciu.toml`; assay lanes additionally name a lane in `assay.toml` (assay's own file and schema; CIU never parses it — it asks `assay lanes --json`, S16.7).

### S16.2 `[testing]`
`inherit` (path to another project's `ciu.toml`, relative to this file, optional; S16.2.1), `cgroup_slice` (slice name; default = `governance.cgroup_parent`), `evidence_dir` (directory for artifacts and verdicts, default `ciu-gate-evidence/` — gitignored; CIU verifies it is ignored), `history` (integer ≥ 1, default 20: LaneResults kept per lane under `evidence_dir/<lane>/history/`). S16.2.1 **Inheritance.** When `inherit` is set, `[testing.environments.*]` of the named file are merged *under* this file's (this file wins per key); nothing else is inherited — lanes, judge, slice and evidence directory are always the project's own. The inherited file MUST itself carry no `inherit` (one level). An environment that names `exec_in` cannot be inherited (it names a LogicalService of another project) — ERROR at stage 12.

### S16.3 `[testing.judge]`
`version` (`version-floor`, required iff any lane has `kind = "assay"`; forbidden otherwise). S16.3.1 Before the first assay lane of a run in a given environment, CIU runs `assay --version` in that environment and refuses when the version does not satisfy the floor (`[S16.3] judge 2.3.1 < floor >=2.4`); the result is cached per environment image digest for the run. S16.3.2 Every verdict MUST carry `judge_provenance` (CIU passes `--require-judge-provenance`); a verdict without it is a lane ERROR. There is no opt-out.

### S16.4 `[testing.environments.<e>]`
`mode` (`ephemeral | exec | host`, required). `exec`: `exec_in` (LogicalService; the container is its variant service (S5.2) via the derived identity; NOT_RUN/`environment-down` when not healthy per S8.6.3). `ephemeral`: `image` (string) or `image_from` (LogicalService → its variant service's `image`); the container runs `--rm` in the slice and, when an instance exists, on the instance network. `host`: a plain subprocess. Common: `forward_env` (list of env names allowed into the lane from CIU's environment), `extra_mounts` (list of `host:container[:mode]`), `workdir` (default: the checkout root inside the container), `binds.<local>` (bindings per S6.4 with `delivery = "env"` only — the variables are injected into the lane's process environment; `wait` is meaningless here and refused), `enabled`. S16.4.1 `host` is also available implicitly as an environment name when no environment table defines it. S16.4.2 In a zero-instance project (S16.11), `exec` environments and `binds` are an ERROR and `ephemeral` containers attach to no network beyond Docker's default. S16.4.3 **Mount proof.** Before running a lane in an `exec` environment, and before `ciu instance exec`, CIU MUST verify through `docker inspect` that the target container bind-mounts this checkout's `physical_repo_root` at `workdir`; a container that mounts another checkout (the primary's tree while a linked worktree is selected) → NOT_RUN/`environment-mismatch` naming both paths. S16.4.4 An environment provides a lane's dependency closure: an offline package cache (npm, Go modules) baked into the image or mounted via `extra_mounts`; assay's snapshot carries committed objects only, so a lane that needs generated dependencies rebuilds them offline from the committed lockfile or declares assay's `isolation.link_paths`. S16.4.5 `required_env ⊆ forward_env` is required for container environments only.

### S16.5 `[testing.lanes.<l>]`
`kind` (`command | assay | sequence`, required), `environment` (name; required for `command` and `assay`; forbidden for `sequence`), `argv` (list, `command`; `{worktree}` is substituted with the checkout root as seen inside the environment), `assay_lane` (string, `assay`), `lanes` (list of lane names, `sequence`), `stop_on` (`FAIL` default | `never`, `sequence`), `description`, `clean_tree` (bool, default true — a dirty tree is NOT_RUN/`dirty-tree`; `--allow-dirty` overrides), `budget` (duration, enforced: the lane is killed and reported `BUDGET_EXCEEDED`), `required_env` (list; missing → NOT_RUN/`env-missing`), `artifacts` (list of paths copied into `evidence_dir/<lane>/`), `requires = { realness = { <logical> = <level> }, healthy = [<logical>…] }`, `require_provenance` (bool, default false; S16.5.6), `resources = { <RK subset>…, shared = [<logical>…] }`, `enabled`. S16.5.1 `requires.realness` compares against the current layout's record (NOT_RUN/`realness-mismatch`). S16.5.2 `requires.healthy` requires each named service's variant service to be healthy per S8.6.3 (NOT_RUN/`service-down`). S16.5.3 `resources.shared` names LogicalServices whose Realizations the lane uses exclusively: lanes sharing a name serialize on `ciu.gate.shared-<realization>.lock` in the checkout that OWNS the Realization (the reference checkout for `joined` Realizations), so worktrees sharing a database serialize against each other. S16.5.4 A `sequence` lane runs its members in order **in the same process**, each with its own admission, preconditions and LaneResult; with `stop_on = "FAIL"` the first member whose outcome is not PASS ends the sequence; the sequence's outcome is PASS iff every member ran and passed, else the first non-PASS member's outcome; `--worktree`, `--base` and `--allow-dirty` apply to every member. A sequence MUST NOT contain itself transitively and MUST NOT name a lane of another project. S16.5.5 There is no `request_base` key: CIU passes `--request-base` exactly to lanes whose assay lane reports `base_source = "request"` in `assay lanes --json` (S16.7). S16.5.6 **Provenance.** For every service in `requires.healthy` and for an `exec` environment's target, CIU records in the LaneResult the running container's image revision (`org.opencontainers.image.revision`, stamped by `ciu build`, S17.6) against the checkout's `HEAD`; with `require_provenance = true` a mismatch or a `-dirty` revision is NOT_RUN/`provenance-mismatch`; without it, a mismatch is a WARN line in the result.

### S16.6 Admission and caps
S16.6.1 Before starting, a lane's `memory_max` is checked against the slice's current `memory.max` minus the sum of running lanes' `memory_max`; insufficient headroom waits up to `--admission-wait` (default 10 m), then NOT_RUN/`no-headroom`. A slice whose `memory.max` is `max` admits everything (admission is then a record, not a gate). S16.6.2 `ephemeral` lanes run with `--cgroup-parent <slice>` and their caps written to their cgroup before the process starts; `host` lanes run in a child cgroup of the slice. S16.6.3 `CIU_GATE_CGROUPFS_ROOT` overrides the cgroupfs mount for tests. S16.6.4 `exec` lanes run inside the target container's cgroup: their `resources` are validated to be ≤ the container's effective governance (S13.4; `[S16.6] lane 'unit' asks memory_max 3G but 'tester' is capped at 800M`) and admission counts against the container's `memory.max`; caps that must differ require an `ephemeral` environment.

### S16.7 Assay invocation
S16.7.1 CIU's only interface to assay's lane file is `assay lanes --json --file assay.toml`, run in the lane's environment: it yields lane names, `base_source`, `external_tools`, `argv0`, `env_required`, `budget`. CIU MUST NOT parse `assay.toml` itself. S16.7.2 A lane runs `assay run <assay_lane> --file assay.toml --require-judge-provenance --verdict-json <evidence_dir>/<lane>/verdict.json --resume --progress [--request-base <REF>]`, executed inside the environment with the checkout mounted at `workdir`. `--request-base` is passed exactly when `base_source = "request"`; `REF` is `--base` if given, else the merge-base of `HEAD` and the checkout's upstream branch (`[S16.7] no upstream; pass --base`). S16.7.3 The lane's outcome is the verdict's `outcome`; `judge_provenance`, `helpers` (when present) and the resolved `REF` are copied into the LaneResult verbatim.

### S16.8 Outcome vocabulary
`PASS`, `FAIL`, `ERROR`, `NOT_RUN` (with reason ∈ `realness-mismatch | service-down | environment-down | environment-mismatch | env-missing | dirty-tree | no-headroom | judge-floor | judge-provenance | provenance-mismatch`), `BUDGET_EXCEEDED`. Exit codes of `ciu gate`: PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET_EXCEEDED 4 — the gate's table, distinct from S18.1.

### S16.9 LaneResult — `ciu.gate.<lane>.json`
`{ schema_version: 2, operation: "gate", lane, kind, environment, project, instance_id?, layout?, started, ended, duration_s, outcome, reason?, exit_code, budget, resources_applied, members?: [<lane names>], request_base?, judge_provenance?, helpers?, verdict_path?, artifacts: [...], preconditions: {...}, provenance: { <service>: { image_revision, head, match } } }`. S16.9.1 The file is the last result; the same document is appended under `evidence_dir/<lane>/history/<started>.json`, pruned to `testing.history`. S16.9.2 `ciu gate --list` shows, per lane, the last outcome, duration and the median duration over the history. S16.9.3 A LaneResult and every artifact written for a non-PASS outcome are mode 0600 (evidence may contain secrets echoed by a failing test).

### S16.10 CLI
`ciu gate [<lane>…] [--list] [--dry-run] [--json] [--base REF] [--allow-dirty] [--check-env] [--worktree PATH] [--admission-wait D]`; `--worktree` runs against another checkout's instance (its instance file decides identity; the path charset rule of S16.12 applies); `--check-env` reports the environment/precondition state without running; `ciu gate doctor` reports the slice, cgroupfs writability, docker reachability, judge version and provenance status per environment.

### S16.11 Zero-instance projects
S16.11.1 A project whose declarations contain no `[realization]` table is a **zero-instance project**: it has no instance, no lock, no instance file, no generated file and no rendered file; `[service]`, `[network]`, `[bundles]`, `[layouts]`, `[realness]`, `[vault]`, `[governance]` (except `cgroup_parent`) are an ERROR if present. S16.11.2 `ciu gate` reads `ciu.toml` (and `ciu.site.toml`) directly, runs `ephemeral` and `host` lanes, writes LaneResults and evidence, and needs `docker` only when an `ephemeral` environment is used and `assay` only in the environment of an assay lane (S18.3). S16.11.3 `ciu check` runs stages 1–3, 11, 12 and 14 (S15.3). S16.11.4 The minimal such project is `[project] name = "x" revision = 8` plus `[testing]` (Appendix B).

### S16.12 Rules lifted from run-gate (kept by reference)
- **Path charset** (RG R-04): a checkout path (or `--worktree` path) containing characters outside `[A-Za-z0-9._/-]` is refused before any container is started.
- **Git isolation** (RG R-19a): container lanes run with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_SYSTEM=/dev/null` unless `forward_env` names them.
- **Dual-mount guard** (RG R-23): an `extra_mounts` entry whose container path is `workdir` or a parent of it is an ERROR.
- **Evidence on failure** (RG R-26): S16.9.3.
- **History** (RG R-36): S16.9.1–S16.9.2.
- **Override reachability** (RG R-25): moot — a sequence lane's members run in-process with the parent's flags (S16.5.4).
- **Container derivation from `ciu.global.toml`** (RG R-14a): retired — `exec_in` names a LogicalService; identity is derived (S4).

---

## S17 Remote deployment

### S17.1 Host inventory lookup
`ciu.hosts.toml` in the checkout root, else `CIU_HOSTS_FILE`, else `~/.config/ciu/hosts.toml`; the first found is used entirely (no merging).

### S17.2 Static checks per layout
For `--layout L`: every host named exists; addresses cover `reach`; every binding resolution with data resolves (S7.8); host declaration order is consistent with the cross-host init graph — a host whose bundles have an edge into a host declared after it is an ERROR (`[S17.2] rs1002 needs core on gstammtisch, declared later`).

### S17.3 Push and what travels
`ciu push --layout L [--host H]` builds, per target host `H`, a bundle of the checkout excluding `bundle_excludes`, `ciu-data/`, and every S2.3 artifact EXCEPT: `ciu.instance.toml`; `ciu.instance.generated.toml` (with the realness records of other layouts stripped, S9.4.4); `ciu.hosts.toml`; and a **reduced** `ciu.secrets.toml` whose content is derived **per secret source**: S17.3.1 For every secret of a Realization placed on `H` (and every stack-level and derived TLS secret of it): `from = "generate"` with `store = "local"`, `from = "ask"`, `from = "file"` → the stored value travels (only the sender has it); `from = "host"` → the entry `[secrets.hosts.<H>.<entry>]` travels, materialized on the CIU host first (S10.4); `from = "ephemeral"` → nothing; `from = "vault"` or `store = "vault"` → nothing when `H` has a derived resolution for the pseudo-consumer `ciu@H` to the `vault` LogicalService (the target fetches, S10.3), else the sender fetches the value now and it travels. S17.3.2 `ciu check --layout L` prints, per host, every entry that travels and why (S15.4); a `from = "ask"` value that is absent from the store is prompted for before transfer. S17.3.3 The bundle is transferred to `bundle_dir` with `push_mode`, in layout order; the store is written on the target with mode 0600. S17.3.4 The instance file and generated file travel so that `instance_id` is shared; the target regenerates `[ciu.host.generated]` (S14.2.3).

### S17.4 Activate
`ciu activate --layout L [--host H] <bootstrap|apply|health|rollback>` runs the host's `[activate]` command over SSH in `bundle_dir`, in layout order, stopping at the first failure. The conventional `bootstrap` is `ciu instance init --host <H> && ciu check --layout L`; `apply` is `ciu up --layout L`. Render-on-target: each host renders its own `ciu.resolved.toml` from the same sources with `[ciu.host.generated].name = H`, so derived resolutions and publications are computed per host.

### S17.5 Cross-host runtime
CIU on a host deploys only the Realizations placed on that host (S7.7) and waits for remote providers by probing resolutions (S8.5.3). A host never opens SSH or `docker exec` to another host during `up`.

### S17.6 Build provenance
`ciu build` stamps every project-built image with `org.opencontainers.image.revision` = the git commit (`-dirty` appended when the tree is dirty) and `org.opencontainers.image.created`, records `[ciu.instance.build]` (`build_version` = `<date>-<git short sha>`, `build_time`, image digests), and images in `project.vendor_images` are pulled, never built. `ciu provenance [--json]` compares every running container of the instance against `HEAD` (the v7 S17 verdict vocabulary `match | dirty | mismatch | vendor | unstamped`).

---

## S18 Command line

| verb | class | notes / v7 disposition |
|---|---|---|
| `ciu init [--project NAME] [--stack DIR [--from-compose FILE]]` | lock-free | scaffold (S19); v7 S19 kept |
| `ciu instance init [--host H] [--move] [--layout L] [--bundles …] [--label X]` | mutating | S14.1, S14.2 (replaces `ciu env generate`) |
| `ciu instance list \| show [<label>] \| add --join <ref> --services … \| remove <label> \| reap \| lease --extend [D] \| --perpetual \| --release \| exec --env <e> -- <cmd>` | list/show/exec read-only; others mutating | S14.6, S14.7, S9.5.5; `ciu worktree …` accepted as an alias for one release |
| `ciu check …` | read-only | S15 |
| `ciu render [--layout L] [--host H] [--show-injected]` | mutating | S11.7 |
| `ciu up [--layout L] [--host H] [--bundles b,…] [--realness s=l,…] [--wait[=D]] [--probe-external] [--no-check]` | mutating | S8, S9 |
| `ciu dev --realization r` | mutating | render + `compose up` of one Realization without waiting on its wave gate; refuses when a provider of an edge from `r` is not up (v7 S5a's dev loop) |
| `ciu down [--realization r]` | mutating | S14.1.3 |
| `ciu clean [--vanilla]` | mutating | S14.1.4 |
| `ciu status [--live] [--json]` | read-only | instance summary: layout, selection, waves, container state by label; `--live` runs the S8.5 predicates (absorbs v7 `health` and `status`) |
| `ciu show bundles \| layouts \| services \| realizations [--json]` | read-only | listings from the merged configuration (absorbs v7 `profiles`, `layouts`) |
| `ciu gate …` | gate | S16.10 |
| `ciu secrets show [--values] \| reset <sel> \| host <h> <entry> \| rotate-bootstrap` | show read-only; others mutating | S10.6 (absorbs v7 `host-secrets`) |
| `ciu env print` | read-only | prints `export`-lines for `instance.*`, `host.*` facts and the instance network; the only producer of `ciu.env` |
| `ciu build [--realization r]` | mutating | S17.6 (v7 `bake`) |
| `ciu push \| activate …` | mutating | S17 |
| `ciu ssh <host> [-- <cmd>]` | read-only (no lock) | opens a shell or runs a command on an inventory host with its declared SSH facts (v7 S14.1) |
| `ciu provenance [--json]` | read-only | S17.6 (v7 S17.2) |
| `ciu diagnose [--json]` | read-only | container/log/health summary by labels |
| `ciu governance ksm \| iops-baseline …` | mutating | v7 `ksm`, `iops-baseline` under one verb |
| `ciu migrate [--check] [--secrets] [--hostdirs] [--config]` | mutating | Appendix A; `--check` reports what a migration would change (v7 `migration-check`) |
| `ciu hook run <realization> <phase> [--validate] [--dry-run]` | read-only for `--validate`/`--dry-run`, else mutating | S12.5.3 |
| `ciu schema [--json] [--file ciu.toml\|ciu.stack.toml\|…]` | lock-free | S3.8.4 (absorbs v7 `capabilities`) |
| `ciu doctor [--json]` | lock-free | environment report: docker, compose, cgroup v2, git, judge per environment, hookkit importable, filesystem lock support (S14.4.6) |
| `ciu version` | lock-free | prints tool version and `schema_version` |
Global flags: `--json` where listed, `--layout`/`--host` default from the instance file and generated file, `--realization r` restricts a verb to one Realization, `--root DIR` (S1.5).

### S18.1 Exit codes
0 success; 1 runtime failure; 2 configuration or usage refusal (findings printed; `ciu check` policy); 3 environment bootstrap failure (a required tool or the daemon is missing, S18.3); 4 lock contention; 5 remote failure. Meanings 0–3 are v7 S10.3's. `ciu gate` uses S16.8's table.

### S18.2 Environment variables CIU reads
| variable | meaning |
|---|---|
| `CIU_EXIT_ON` | ambient fallback for `ciu.exit_on` (config wins) |
| `CIU_MAX_CONCURRENT_INSTANCES` | ambient budget override never written to a file (S14.6) |
| `CIU_SECRET_<VAR>` | `from = "ask"` non-interactive input (S10.1) |
| `VAULT_TOKEN` | Vault token source #1 (S10.3.3) |
| `CGROUP_PARENT_DEV_BACKGROUND` | governance parent / gate slice when `cgroup_parent = ""` (S13.2) |
| `CIU_HOSTS_FILE` | host inventory path (S17.1) |
| `CIU_SSH_TRANSPORT` | `openssh` (default) or `paramiko` for push/activate/ssh |
| `CIU_SSH_INSECURE_TOFU` | `1` = accept an unknown host key once (S7.2) |
| `CIU_KSM` | `off` disables KSM for one run (S13.3.1) |
| `CIU_GOV_BASELINE_PATH` | overrides `governance.baseline_path` (S13.2) |
| `CIU_SKIP_DOOD_PREFLIGHT` | `1` = skip the docker-outside-of-docker mount check (tests only) |
| `CIU_GATE_EXTRA_MOUNTS` | additional `host:container[:mode]` mounts for every container lane (S16.4), comma-separated |
| `CIU_GATE_MOUNT_ALIAS` | `host=container` path alias applied to `{worktree}` substitution when the checkout is mounted under a different path in the lane container |
| `CIU_GATE_EVIDENCE_DIR` | overrides `testing.evidence_dir` (S16.2) |
| `CIU_GATE_CGROUPFS_ROOT` | overrides the cgroupfs mount (S16.6.3) |
| `NO_COLOR`, `TERM` | output styling |
| `CIU_LOG_PREFIX_TIME_SHORT` | `1` = short timestamps in log prefixes |
| `HOSTNAME`, `REMOTE_CONTAINERS`, `WORKSPACE_DIR`, `GITHUB_ACTIONS`, `USER` | environment detection during `instance init` only (S14.2) |
No other variable influences behavior; none is a configuration source. There is no `CIU_SKIP_DEPENDENCY_CHECK`: preflights are per need (S18.3).

### S18.3 Preflights (per verb, per need — never at import time)
`docker` and `docker compose`: required by `up`, `down`, `clean`, `dev`, `build`, `status --live`, `diagnose`, `instance exec`, `instance reap`, and by `gate` only for lanes whose environment is `ephemeral` or `exec` (checked when the lane is admitted, reported as NOT_RUN/`environment-down` naming the missing binary). `git`: required by the instance registry (`instance init` on a git checkout), `clean_tree`, `--base` and provenance; a non-git checkout is an instance without a registry record (budget and leases do not apply). `assay`: required only inside the environment of an assay lane (S16.3.1). `ssh`/`rsync`: `push`, `activate`, `ssh`. Vault reachability: `up`/`render` only when a vault-sourced secret must be materialized. A missing prerequisite exits 3 naming the verb and the binary.

### S18.4 JSON envelope
Every `--json` output and every LaneResult is `{ schema_version: 2, operation: "<verb>", status: "ok" | "findings" | "error", ...verb-specific keys }`. `ciu.resolved.toml` carries `resolved.schema_version = 2`. A reader MUST refuse a `schema_version` it does not know.

---

## S19 Scaffolding — `ciu init`
S19.1 `ciu init [--project NAME]` in an empty directory or a checkout without `ciu.toml` writes: `ciu.toml` with `[project] name = "<NAME or directory basename>"`, `revision = 8`, `[realness] default = "live"`, `[layouts.local] hosts.localhost = { bundles = ["all"], reach = ["instance"] }`, `[bundles.all] services = []`, an empty `[testing]`; `ciu.hosts.toml` with `[hosts.localhost] local = true`; and the S2.3 gitignore patterns (appended to `.gitignore`, comment-normalized, idempotent). Nothing is implicit: what `init` writes is what the project runs on. S19.2 `--stack DIR [--from-compose FILE]` additionally writes `<DIR>/ciu.stack.toml` (one `[ciu_stack.<svc>]` per compose service with `image` copied and every prohibited stanza of S11.3 removed into a comment) and `<DIR>/ciu.compose.yml.j2`, registers `[realization.<basename>] kind = "ciu_stack" location = "<DIR>"` and `[service.<basename>] live = "<basename>"`, and adds the service to `bundles.all`. S19.3 `ciu init --gate-only` writes only `[project]` and `[testing]` (a zero-instance project, S16.11). S19.4 `init` refuses to overwrite an existing declaration file (`[S19] ciu.toml exists`).

---

## S20 Refusal catalogue (normative identifiers)
Every refusal is `[S<n>.<m>] <message>`; the message MUST name the offending file/table/key and, where a name failed to resolve, the closest candidates. Implementations MUST keep the rule identifier stable across releases; a rule that is withdrawn keeps its number retired. The catalogue is the set of `[S…]` identifiers in this document; `ciu check --json` emits `rule` for each finding so tests can assert on it.

---

## Appendix A — Adopting v8 from a v7 checkout (`ciu migrate`)
`ciu migrate --check` reports every step below without writing; `ciu migrate` performs the mechanical ones and prints the rest.
1. **Instance:** `ciu instance init --layout local` in every checkout (primary included); it writes the generated file and `ciu.instance.toml`; `ciu.env` becomes an export. Cockpit aliases switch to `eval "$(ciu env print)"`.
2. **Declaration files** (`--config`): `ciu.global.defaults.toml.j2` → `ciu.toml`, `ciu.global.toml.j2` → `ciu.site.toml`, `ciu.global.instance.toml.j2` → `ciu.instance.toml`, `<stack>/ciu.defaults.toml.j2` (+ `ciu.toml.j2`) → `<stack>/ciu.stack.toml`. Jinja in a declaration is resolved where it is data (`{% set %}` constants are inlined; `{{ vault.paths.x }}` becomes `path = "x"`) and reported as a residue where it is control flow (a `{% for %}` generating tables is expanded once and flagged). `revision = 8`; `[deploy]` → `[project]`; `[deploy.profiles]` → `[bundles]`; `[deploy.layouts]` → `[layouts]` (`reach = ["instance"]` added); `[deploy.realness]` → `[realness]`; remove `deploy.environment_tag`, `deploy.network_name`, `deploy.environment`, `deploy.labels`, `deploy.env.defaults`, `[deploy.phases]`, `[topology.*]`, `[service.<n>] type/location`, `[deploy.resources]`, `ciu.repo_root/physical_repo_root`, `[ciu.worktree.exec_targets]`; write `[service.*]` variants, `[realization]` (`per_host` for transport daemons), `[network.*]`, `[vault] service`, `[testing.*]`; rename `[governance]` keys to `RK`; `[ciu.worktree] max_concurrent_instances` → `[ciu.instances] max_concurrent`.
3. **Stack files:** re-root to `[ciu_stack.<svc>]`; `requires`/`provides` → bindings (`init_requires` → `requires` or `binds.<n>` with an endpoint; `uses` → `binds.<n> = { to = …, wait = "none", delivery = "template" }`; `after` → `requires`; `init_provides` → `provides`; `[hooks.provides.<svc>]` → `provides` on the hook entry with `service`); add `endpoints`; secrets: `ASK_VAULT:<p>[#f]` → `from = "vault", path = "<p>"[, field = "<f>"]`; `GEN_TO_VAULT:<p>` → `from = "generate", store = "vault", path = "<p>"`; `GEN_LOCAL:<n>` → `from = "generate"`; `ASK_EXTERNAL:<V>` → `from = "ask", var = "<V>"`; `ASK_FILE:<p>` → `from = "file", path = "<p>"`; `ASK_HOST:<e>` → `from = "host", entry = "<e>"`; `GEN_EPHEMERAL` → `from = "ephemeral"`; `consumed_by = "hook"` → `delivery = "hook"`; `produced_by` dropped; `delivery` (and `env_name`) added to every secret; consumer scalars into sub-tables; mark `primary`; replace hand-declared replicas with `instances`; drop `name`, `stack_name`, `image_name`/`image_tag`, `internal_port`, `[<svc>.ports]`, `[<svc>.resources]`, `[state]` (moves to `ciu.state.toml`).
4. **Compose and config-file templates:** remove `container_name`, `hostname`, `networks`, `secrets`, `depends_on`, `ports`, `expose`, `ciu.*` labels, healthcheck timing fields, `${VAR:-…}`/`${VAR:?…}` forms, `{{ env.* }}`; replace identity/topology reads with `ciu_stack.*.identity`, `ciu_stack.<svc>.binds.<local>.*`, `instance.*`, `host.*`; replicated services iterate `identity.replicas` with `replica`; config-file templates re-root the same way and every `secret()` call requires `delivery = "configfile"`; `ciu.rendered.<svc>.<cfg>` reads become paths under `ciu.rendered/<svc>/`.
5. **Hooks:** `run(config, ctx)` → a script on `ciu.hookkit` (S12.5); `apply_to_config` → `emit(state=…)` (visible to the same run's renders, S8.7); `persist: "secret"` → `emit(secrets=…)`; `validate_config` → `--validate`.
6. **Secrets state** (`--secrets`): imports `.ciu/secrets/*` and `[state]` Vault bootstrap values into `ciu.secrets.toml` (never re-minting), moves non-secret `[state]` into `ciu.state.toml`, deletes `.ciu/`.
7. **Host directories** (`--hostdirs`): S6.8.4.
8. **Gate:** `run-gate.toml` → `[testing.*]`: `environments.<n>.container_name` → `exec_in = "<LogicalService>"`; `image` kept or `image_from`; `lanes.<n>.memory` → `resources.memory_max`; `pins.*` → `[testing.judge] version` (a floor); `assay_command` dropped (derived); `request_base` never existed as a key; central config (R-22) → `[testing] inherit`; conjunction lanes → `kind = "sequence"`; `RUN_GATE_*` → `CIU_GATE_*`; `.assay/verdict-*.json` → `evidence_dir`; `assay.toml` `derived:` facts (if any) repointed at `resolved.bindings.env.<e>.<local>.*` or replaced by `required-env:` facts fed from environment `binds`.
9. **run-gate stays available** for projects that keep it; both tools read the same `assay.toml`.

## Appendix B — Worked examples
**Minimal (one stack, one host lane), `v8-dstdns-demo/examples/minimal/`:**
```toml
# ciu.toml
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
```toml
# ciu.hosts.toml (gitignored; written by ciu init)
[hosts.localhost]
local = true
```
```toml
# web/ciu.stack.toml
[ciu_stack.web]
image = "nginx:1.27"
endpoints.http = { port = 80, protocol = "http", publish = "host", host_port = 8080 }
```
`ciu init && ciu instance init && ciu up && ciu gate unit`. A zero-instance project is the same `ciu.toml` without `[service]`, `[realization]`, `[bundles]`, `[layouts]`, `[realness]` and without `ciu.hosts.toml`.

**dstdns:** `v8-dstdns-demo/` renders dstdns (27 Realizations, 4 hosts, 4 layouts, 9 lanes) in this notation, including a hand-written excerpt of the derived tables. Its README lists the decisions the conversion took and where the demo deliberately deviates from the v7 sources.

## Appendix C — Rule map draft.2 → draft.3
S3.5.5 (two-pass render) withdrawn · S6.4 (init edges) → S6.4 (bindings) · S7.8 (routes) → S7.8 (binding resolution) · S8.2 init/after edges → bind edges · S8.6.3 (`[hooks.provides]`) → S6.10 · S9.3.1 precedence → S9.3.1/S9.4.2 (record as constraint) · S10.1 directives → S10.1 sources · S14.4 (rendered-file lock) → S14.4 (directory lock) · S16.5 `request_base` withdrawn → S16.5.5 · S16.11 rewritten · S18.1 renumbered to v7 · `deploy.*` → `project.*`, `bundles`, `layouts`, `realness` · `[deploy.hosts]` → `[hosts]` · `ciu.instance.resolved` → `resolved` · `owned-seeded` → `seeded`.
