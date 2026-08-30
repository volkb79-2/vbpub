# CIU v8 Specification

**Version:** 8.0.0-draft.2 · **Status:** DRAFT (ground-up specification for CIU v8; companion to `CIU-V8-TESTING-GATE-PROPOSAL.md` revision 2.x; worked example `v8-dstdns-demo/`) · **Date:** 2026-08-30

This document is self-contained. It defines what a v8 CIU implementation MUST do, in normative language (MUST / MUST NOT / SHOULD / MAY as in RFC 2119). Rules are numbered `S<section>.<rule>` so a refusal message, a test, or a review can cite exactly one rule. Every refusal an implementation emits MUST name the rule it enforces in the form `[S<n>.<m>]`.

---

## S1 Scope, vocabulary, invariants

### S1.1 What CIU does
CIU deploys a **project** — a set of Docker Compose stacks plus external and borrowed services — as isolated **instances** (one per checkout), on one or several **hosts**, at a chosen **realness** level per capability, and runs the project's **gate** (test lanes and judge lanes) against a deployed instance. CIU owns: configuration layering and rendering, identity, ordering, routing between services, secret materialization and delivery, host placement and remote push, resource governance, preflight validation, and gate execution. CIU does not own: the contents of a service's compose stanzas beyond what it injects (S11), application configuration semantics, secret rotation inside applications, certificate issuance, proxy policy, or the judgment of test evidence (the judge is `assay`, an external program).

### S1.2 Entities
| entity | identity | meaning |
|---|---|---|
| **Project** | `deploy.project_name` | the consumer repository |
| **Instance** | `instance_id` | one checkout's deployment; the primary checkout is an instance too |
| **LogicalService** | `[service.<n>]` | a capability with a typed **contract** |
| **RealnessVariant** | `[service.<n>].<level>` | which Realization (and which of its services) satisfies the capability at a level |
| **Realization** | `[realization.<n>]` | a concrete provider: `ciu_stack`, `external`, `joined` |
| **RealizedService** | `[ciu_stack.<svc>]` in a stack file | one deployable service of a `ciu_stack` |
| **Endpoint** | `endpoints.<e>` on a RealizedService or an external Realization | a reachable port or URL |
| **TypedFact** | `kind:selector` string | a provable statement about live infrastructure |
| **Host** | `[deploy.hosts.<h>]` | a machine with one address per Network |
| **Network** | `[network.<n>]` | a reachability domain: an address plane, or a proxy |
| **Profile** (bundle) | `[deploy.profiles.<p>]` | a set of LogicalServices that deploy together |
| **Layout** | `[deploy.layouts.<l>]` | placement of bundles on hosts and the networks each host reaches |
| **Identity** (derived) | per RealizedService/replica | container name, hostname, compose key, compose project, network |
| **Route** (derived) | (consumer Realization, LogicalService, Endpoint) | how a consumer reaches an endpoint |
| **Wave** (derived) | ordinal | a set of Realizations deployed together |
| **Environment** (gate) | `[testing.environments.<e>]` | where a lane's process runs; also the target of `ciu instance exec` |
| **Lane** (gate) | `[testing.lanes.<l>]` | one test or judge invocation with preconditions and caps |

### S1.3 Invariants (each later rule serves at least one)
- **I1 One source per fact.** A fact is declared in exactly one place; every other appearance is derived from it and marked derived.
- **I2 Fail at the earliest checkable point.** Schema → `ciu check` → deploy. No silent defaults for facts that exist elsewhere.
- **I3 Derived values are visible.** Every derived value is written as data into a rendered, cat-able file.
- **I4 Mechanical checkability.** Closed vocabularies, referential integrity, and graph completeness are verified by the tool, not by a reader.
- **I5 Nothing hidden.** No hidden directories; no ambient process environment as a configuration source; all machine-owned files are flat, visible, and gitignored by a fixed list.
- **I6 One derivation per identity.**
- **I7 Declaration is separate from resolution.** Needs are declared by capability; how they are satisfied is a selection recorded by the tool.

### S1.4 Names and grammars
- `name` := `^[a-z][a-z0-9_]*$` — LogicalService, Realization, RealizedService, Endpoint, Network, secret keys, configfile names, hostdir purposes. Profile, Layout, Environment and Lane names additionally allow `-` (`^[a-z][a-z0-9_-]*$`).
- `hostname` := `^[a-z][a-z0-9-]*$` for Host names. DNS-facing derived strings (S4.2) map `_` → `-` (injective, because `name` forbids `-`).
- `duration` := `^[0-9]+(ms|s|m|h)$`. `size` := `^[0-9]+(K|M|G|T)?$` (bytes, binary units) or `"max"`.
- `level` := `live` | `mock` | `owned-seeded` | `simulated`.
- `version-floor` := `^>=[0-9]+(\.[0-9]+){0,2}$`.
- TypedFact grammar (S5.6): `pg:db/<name>`, `pg:role/<name>`, `pg:schema/<name>`, `minio:user/<name>`, `minio:bucket/<name>`, `vault:secret/<path>[#field]`, `http:<path>`, `pki:issuer/<network>`.
- Reserved names: a Realization MUST NOT be named `hosts` or `ciu`; a RealizedService MUST NOT be keyed `hooks`, `governance`, `state`, `secrets`, `host`, `kind`, `location`, `endpoints`, `provides`, `instance`, `service`, `per_host`; a consumer sub-table of a service MUST NOT be named `identity`, `health`, `endpoints`, `hostdir`, `configfile`, `secrets`.

---

## S2 Files

### S2.1 Project root (every path relative to the checkout root)
| file | committed | written by | role |
|---|---|---|---|
| `ciu.global.defaults.toml.j2` | yes | consumer | full global defaults (S3) |
| `ciu.global.toml.j2` | yes, optional | consumer | sparse global override |
| `ciu.global.instance.toml.j2` | **no** | operator | per-instance overlay: layout, bundles, label, joins, host-port overrides (S14.2); hand-edited only |
| `ciu.instance.generated.toml` | no | ciu | CIU-owned facts merged after the overlay: instance identity, host facts, realness records, build facts (S14.2) |
| `ciu.global.toml` | no | ciu | rendered merged config + derived tables; the instance lock (S14.4) |
| `ciu.instance.json` | no | ciu | instance registry record (S14.7) |
| `ciu.hosts.toml` | no | operator | host inventory (S7.2, S17.1) |
| `ciu.secrets.toml` | no | ciu | materialized secrets store (S10.6) |
| `assay.toml` | yes | consumer | judge lanes (owned by assay; read by the gate for lane names only) |
| `ciu.gate.<lane>.json`, `ciu.gate.shared-<name>.lock` | no | ciu | LaneResults (S16.9) and shared-resource locks (S16.5.3) |
| `<evidence_dir>/` (default `ciu-gate-evidence/`) | no | ciu | gate artifacts and verdicts (S16.2) |
| `ciu-data/` | no | ciu | generated host directories (S6.8) |
| `ciu.env` | no | ciu (`ciu env print > ciu.env`) | derived shell export for humans and legacy tooling; **never read by ciu** |

User-global: `~/.config/ciu/hosts.toml` (S17.1), `$XDG_CACHE_HOME/ciu/` (build caches).

### S2.2 Stack directory (`location` of a `ciu_stack` Realization)
| file | committed | written by | role |
|---|---|---|---|
| `ciu.defaults.toml.j2` | yes | consumer | stack defaults (S6) |
| `ciu.toml.j2` | yes, optional | consumer | sparse stack override |
| `ciu.toml` | no | ciu | rendered stack config (this stack's tables as the compose render sees them, S3.6.4) |
| `ciu.compose.yml.j2` | yes | consumer | compose template (S11) |
| `ciu.compose.yml` | no | ciu | rendered compose file, injections applied; the file compose runs |
| `ciu.state.toml` | no | ciu (hook outputs) | hook-persisted non-secret state (S6.10, S12.3) |
| `ciu.rendered.<svc>.<cfg>` | no | ciu | rendered config file mounts (S6.9) |
| `ciu.secret-temp-copy.<svc>.<key>.txt`, `ciu.secret-temp-copy.<key>.txt` | no | ciu | per-run bind-mount sources for `file`-delivered secrets (S10.7) |
| hook scripts, config templates | yes | consumer | referenced by name from the stack file |

### S2.3 Gitignore list
S2.3.1 The following patterns MUST be ignored in every checkout, and `ciu check` stage 1 MUST verify each is ignored (`git check-ignore`) when the checkout is a git work tree: `ciu.global.toml`, `ciu.global.instance.toml.j2`, `ciu.instance.generated.toml`, `ciu.instance.json`, `ciu.toml`, `ciu.compose.yml`, `ciu.state.toml`, `ciu.rendered.*`, `ciu.secret-temp-copy.*`, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.gate.*`, `ciu.env`, `ciu-data/`, and the configured `testing.evidence_dir`. S2.3.2 A directory named `.ciu` anywhere under the checkout is an ERROR (`[S2.3] legacy .ciu directory present; run ciu secrets migrate`), except during `ciu secrets migrate`. S2.3.3 CIU MUST NOT create any hidden file or directory inside a checkout. S2.3.4 These files are ignored so that they are never committed; a tool that deletes ignored files (`git clean -x`) while an instance is up destroys the instance lock and rendered state — documented, not detectable (S14.4.7).

### S2.4 Secret-free rule
S2.4.1 Every committed template (`*.toml.j2`, `*.yml.j2`, configfile templates) MUST be scanned before rendering: a PEM block (`-----BEGIN`), or a key whose last `_`-separated component is one of `password`, `token`, `secret`, `api_key`, `credential`, `passphrase`, `private_key`, `key` paired with a literal string value of 8 or more characters that is not a directive, a `{{ … }}` or `${…}` reference, a `/run/secrets/` path, or a KV-path-shaped string (`^[a-z0-9_./-]+$`), is an ERROR (`[S2.4]`). S2.4.2 After rendering, `ciu.compose.yml` and every `ciu.rendered.*` file MUST be scanned for any store value (S10.6) of 8 or more characters appearing verbatim, except a config file whose service declares a `delivery = "configfile"` secret (S10.2.6); a match is an ERROR and the artifact MUST be deleted before the refusal is reported.

### S2.5 File modes
`ciu.secrets.toml` 0600; `ciu.secret-temp-copy.*` per the secret's `mode`/`uid` (default 0400, `instance.user_uid`); rendered files 0644; the overlay and the generated file 0644.

---

## S3 Configuration model

### S3.1 Layers and merge
S3.1.1 The global configuration is the deep-merge, in order, of: `ciu.global.defaults.toml.j2` → `ciu.global.toml.j2` (if present) → `ciu.global.instance.toml.j2` → `ciu.instance.generated.toml` (plain TOML, not a template). S3.1.2 Merge semantics: tables merge recursively; scalars and lists REPLACE; a key cannot be deleted — a falsy value (`false`, `""`, `[]`, `{}`) disables it. S3.1.3 A stack's configuration is the deep-merge of the global configuration with, in order, `<location>/ciu.defaults.toml.j2` → `<location>/ciu.toml.j2` (if present), with the stack file's tables **re-rooted** per S3.6. S3.1.4 The overlay and the generated file MUST exist for every mutating verb except `instance init`; absence is an ERROR (`[S3.1] no instance overlay; run ciu instance init`).

### S3.2 Rendering
S3.2.1 Each `.j2` layer is rendered as a Jinja2 template, then parsed as TOML. The Jinja environment MUST use `StrictUndefined`: any reference to an undefined name, attribute, or item is an ERROR naming the template and the expression (`[S3.2] undefined 'deploy.environment_tg' in ciu.global.defaults.toml.j2:71`). S3.2.2 The render context contains ONLY the names listed in S3.5 and the Jinja built-ins. There is NO `env` mapping and NO access to the process environment from a template. S3.2.3 CIU performs no `$VAR` expansion on TOML layers (a `$` is literal). In compose templates `${VAR}` sequences are left for Docker Compose interpolation and MUST name a variable in S11.6's set; `$$` is an escaped dollar and is ignored by the scan. S3.2.4 `{% set %}`, loops and conditionals are permitted (data expansion); `{% include %}`, `{% import %}`, `{% extends %}`, custom filters and functions are NOT provided (`[S3.2] template loader disabled`). S3.2.5 Rendering is deterministic: the same inputs MUST produce byte-identical outputs; rendered TOML is emitted with tables and keys in a fixed canonical order (S3.7.4).

### S3.3 Top-level tables of the global configuration
Closed set. Any other top-level table is an ERROR (`[S3.3] unknown top-level table 'foo'`) unless it is named in `ciu.user_tables`.

| table | defined in | owner |
|---|---|---|
| `deploy` | S3.4 | consumer |
| `service` | S5.2 | consumer (+ operator overlay rows for joins) |
| `realization` | S5.4 | consumer (+ operator overlay rows for joins) |
| `network` | S7.3 | consumer |
| `vault` | S10.3 | consumer |
| `registry` | S3.4.6 | consumer |
| `governance` | S13 | consumer |
| `testing` | S16 | consumer |
| `ciu` | S3.4.7 | consumer |
| `ciu.instance` | S14.2 | operator (overlay) + ciu (generated file) |
| `ciu.host` | S14.2 | ciu (generated file) |
| `ciu.instance.resolved` | S3.7 | ciu only (rendered file) |

### S3.4 `[deploy]` and its sub-tables
S3.4.1 `[deploy]`: `project_name` (name, required, literal), `revision` (integer, required, MUST equal `8` — `[S3.4] config revision 7 is not 8`), `log_level` (`DEBUG|INFO|WARN|ERROR`, default `INFO`), `landscape_id` (`^[a-z][a-z0-9-]{0,62}$`, optional). S3.4.2 `[deploy.registry]`: `url` (string, `""` = local daemon), `namespace` (name; required when any stack image or `ciu build` references it). S3.4.3 `[deploy.labels]`: `prefix` (string, required). S3.4.4 `[deploy.health]`: `interval`, `timeout`, `start_period` (durations), `retries` (integer ≥ 1), `gate_timeout` (duration, optional) — the defaults merged into every RealizedService's `health` table (S6.6). S3.4.5 `[deploy.env.defaults.<VAR>]` and `[deploy.env.shared.<VAR>]`: string values; `shared` is exported to every compose process (S11.6); `defaults` is template data. `[deploy.control.<flag>]`: booleans only. `[deploy.provenance] vendor_images`: list of image names that `ciu build` does not build (pulled; recorded by digest only). S3.4.6 `[deploy.profiles]`, `[deploy.layouts]`, `[deploy.realness]`: S7.5, S7.6, S9.2. `[registry.*]`: free-form consumer metadata; CIU reads only `registry.postgresql.database` (target database of `pg:schema/*` probes); the rest is validated by the consumer's `ciu.registry_validator` (S15.3 stage 14). S3.4.7 `[ciu]`: `standalone_root` (bool; when true, CIU refuses to treat a parent directory that is also a ciu root as this checkout's root — `[S3.4] nested inside ciu root <path>`), `require_fqdn` (bool; `instance init` refuses when no public FQDN can be determined for the host, S14.2.4), `auto_connect_network` (bool, default true; S7.8.7), `exit_on` (`WARN|ERROR|NEVER`, default `WARN`), `user_tables` (list of names, default `[]`), `registry_validator` (path, optional). `[ciu.instances]`: S14.6.

### S3.5 Render contexts
S3.5.1 **Global chain**: each `.j2` layer sees the merge of the layers before it (the defaults layer sees only its own `{% set %}` names). S3.5.2 **Stack file render** (`ciu.defaults.toml.j2`, `ciu.toml.j2`): the merged global configuration, `instance` (S3.5.4), `routes` (S7.8) for THIS stack, and the stack's own `hooks`, `governance`, `state` and consumer top-level tables by name (S3.5.6). It MUST NOT reference `ciu_stack`, `stack_dir`, `secret()` or `realization.<n>.<svc>` of other stacks (`[S3.5] ciu_stack is not available while the stack file renders`). S3.5.3 **Compose and configfile render**: everything in S3.5.2 plus `ciu_stack` (this stack's services with derived `identity`, merged `health`, resolved `allow_from_resolved`), `realization` (the merged view of every stack in the deploy set, S3.6 — read-only), `stack_dir` (the physical host path of the directory being rendered, for bind-mounting stack-local files), and — configfile templates only — the function `secret("<key>")` for secrets declared with `delivery = "configfile"` (S10.2.6). S3.5.4 `instance` is a read-only mapping assembled from `[ciu.instance.generated]`, `[ciu.host.generated]`, `[ciu.instance.build]` and `[ciu.instance.resolved]`: `id`, `project`, `label`, `layout`, `host`, `environment`, `network` (the instance network's name), `landscape_id`, `services` (the list of selected LogicalService names, for template guards), `repo_root`, `physical_repo_root`, `public_fqdn`, `env_type`, `user_uid`, `user_gid`, `docker_gid`, `build_version`, `build_time`. Every value is also present in a file, so `instance` adds no fact a file does not carry (I3). S3.5.5 **Two-pass stack rendering.** Because a stack file may read `routes` whose targets are endpoints declared in other stack files, stack files are rendered twice: pass 1 with `routes` bound to a recording stub (any attribute chain renders as `""` and is logged) to extract `endpoints`, `init_requires`, `uses`, `enabled` and `instances`; pass 2 with the real `routes`. A pass-1 access that is not a literal chain `routes.<X>.<e>.<key>` (for example `routes[x]`) is an ERROR (`[S3.5] non-literal route access`). S3.5.6 A stack's own top-level consumer table whose name equals a global top-level table (S3.3), a global user table, or one of its service keys is an ERROR.

### S3.6 Re-rooting and the merged view
S3.6.1 A stack file declares its services under the fixed root `[ciu_stack.<svc>]` (S6.1), MAY declare stack-level secrets under `[ciu_stack.secrets.<key>]` (S10.2), and MAY declare the reserved tables `[hooks]`, `[governance]`. S3.6.2 When the stack bound to Realization `R` is loaded, CIU MUST place its `ciu_stack` table at `realization.R.<svc>` (and `realization.R.secrets`) in the merged view, its reserved tables at `realization.R.hooks|governance`, its state (S6.10) at `realization.R.state`, and its consumer top-level tables at `realization.R.<table>`. S3.6.3 A service key in S1.4's reserved list is an ERROR (`[S3.6] reserved service key 'secrets'`). S3.6.4 The rendered `ciu.toml` of a stack contains the stack's own tables under `[ciu_stack.*]` exactly as the compose render sees them (identity, health, resolved allow-lists merged in).

### S3.7 Derived tables — `[ciu.instance.resolved]`
S3.7.1 CIU MUST write, on every render, the derived tables specified in S4.4 (identities and endpoints), S7.7 (placement), S7.8 (routes), S7.9 (networks), S8.8 (waves, gates, edges), S9.3 (selection), S13.4 (effective governance) into the rendered `ciu.global.toml` under `[ciu.instance.resolved]`, together with `facts_schema = 1`, `layout`, `host`, `environment`, and — as the LAST table of the file — `[ciu.instance.resolved.render] complete = true` (S14.4.2). S3.7.2 A `ciu.instance.resolved` table in any INPUT layer is an ERROR (`[S3.7] ciu.instance.resolved is derived`). S3.7.3 The derived tables are regenerated identically from the same inputs; they carry no state. S3.7.4 The rendered file is emitted in canonical order: input tables in S3.3 order, then `[ciu.instance.resolved]` sub-tables in the order listed in S3.7.1, keys sorted within each table.

### S3.8 Validation of the schema itself
S3.8.1 Every table in this specification has a closed key set; an unknown key is an ERROR naming the table and the key (`[S3.8] unknown key 'memroy_max' in [governance]`). Consumer data is admitted only where a rule says "consumer data" (sub-tables of a service, user tables, `[registry.*]`, `[vault.paths]`, stack-level consumer tables, `[deploy.env.*]`). S3.8.2 Types and vocabularies are as stated per rule; a violation is an ERROR naming the rule. S3.8.3 Referential rules (a name that must resolve) are enforced by `ciu check` stage 4 (S15.3). S3.8.4 The closed key sets SHOULD be defined once, declaratively, and both the validator and the documentation generated from that single definition (I1).

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
- `network = "{P}-{I}-network"` (the instance network; created by CIU)
S4.2.2 The service component is omitted when `svc == R`. S4.2.3 No other code path in CIU, in a template, in a hook, or in the gate MAY form these strings; they are read from `[ciu.instance.resolved.realizations.R.svc.identity]` (S4.4) or from the `ciu_stack.<svc>.identity` render binding. S4.2.4 A `per_host` Realization (S7.6.5) has the same identity on every host it runs on (names are per daemon).

### S4.3 Uniqueness and enforcement
S4.3.1 `ciu check` stage 8 MUST assert that every `container_name` and every `compose_project` in the deploy set is unique per host (structurally guaranteed; checked). S4.3.2 A rendered compose service that declares `container_name:` or `hostname:` with a value different from the derived one is an ERROR (`[S4.3] container_name 'x' differs from derived 'y'`); equal values are tolerated and removed; a `host_network = true` service (S6.2) has no injected `hostname`. S4.3.3 A service key that would produce a name longer than 63 characters is an ERROR.

### S4.4 Identity and endpoint facts (derived table)
```toml
[ciu.instance.resolved.realizations.<R>.<svc>.identity]
container_name = "…"  hostname = "…"  compose_key = "…"  compose_project = "…"  network = "…"
replicas = [ { index = 1, container_name = "…", compose_key = "…" }, … ]   # present only when instances > 1
[ciu.instance.resolved.realizations.<R>.<svc>.endpoints.<e>]
port = 5432  protocol = "tcp"  publish = "instance"  host_port = 5432  published_on = ["mesh"]  path = "/…"   # S7.4; joined instances route by this
```
S4.4.1 `hostname` always equals `container_name`. S4.4.2 The identity mapping is bound as `ciu_stack.<svc>.identity` in the stack's compose and configfile renders.

### S4.5 Labels
S4.5.1 CIU stamps every container it creates with `<prefix>.project`, `<prefix>.instance`, `<prefix>.realization`, `<prefix>.service`, `<prefix>.replica` (when replicated), `<prefix>.managed-by=ciu`, where `<prefix>` is `deploy.labels.prefix`. `ciu clean`, `ciu diagnose` and the gate enumerate resources by these labels only, never by name pattern. S4.5.2 A template label whose key is one of S4.5.1's is an ERROR (`[S4.5] label 'managed-by' is reserved`).
## S5 Services, contracts, realizations

### S5.1 Separation
S5.1.1 A consumer declares needs as LogicalServices (`[service.*]`) and providers as Realizations (`[realization.*]`); the only link is `realized_by` on a RealnessVariant. A stack file never names a LogicalService or a Realization (I7).

### S5.2 `[service.<n>]`
Keys: `description` (string, optional); `contract` (list of TypedFacts, required, may be empty); one sub-table or inline table per level (at least one): `live`, `owned-seeded`, `simulated` carry `realized_by` (name → `[realization.<r>]`, required) and optionally `service` (a service key of that `ciu_stack` Realization — the **variant service**: its health stands for the capability, its endpoints are the capability's endpoints, it is the exec target and image source; default = the Realization's primary service, S8.6); `mock` is an empty table (`mock = {}`) and MUST NOT carry `realized_by` (S9.1). S5.2.1 A level outside S1.4's vocabulary is an ERROR. S5.2.2 A `realized_by` that does not resolve is an ERROR (`[S5.2] service 'main_db' level 'live' realized_by 'db_cor' is not a realization`); a `service` that is not a service of that Realization is an ERROR. S5.2.3 The same Realization MAY back several LogicalServices and several levels.

### S5.3 Contract conformance
S5.3.1 For every variant `service.X.<level> → R` (levels other than `mock`), every fact in `contract(X)` MUST be provided by `R`: for `ciu_stack` by the union of the **declared** provides (`init_provides` of R's services, `[hooks.provides]`) and the **derived** provides (`vault:secret/<path>` for every `GEN_TO_VAULT:<path>` directive in R, S8.3); for `external` and `joined` by `provides` on the Realization (S5.4). A missing fact is an ERROR (`[S5.3] contract of 'main_db' fact 'pg:role/webapp' not provided by 'db_core'`). S5.3.2 A fact provided by some Realization that appears in no contract is a WARN (unclaimed fact) — except derived `vault:secret/*` facts, which directives consume. S5.3.3 An empty contract means: depending on X is depending on the variant service's health (S8.6).

### S5.4 `[realization.<n>]`
Common key: `kind` ∈ `ciu_stack | external | joined` (required). Per kind:
- `ciu_stack`: `location` (repo-relative directory containing `ciu.defaults.toml.j2` and `ciu.compose.yml.j2`, required); `per_host` (bool, default false; S7.6.5).
- `external`: `provides` (list of TypedFacts, default `[]`); `[realization.<n>.endpoints.<e>]`: `url` (URL, required; scheme default ports apply when the URL carries none), `tls` (`none|tls|mtls`, default from the scheme), `ca` (path, optional).
- `joined`: `instance` (instance name per S14.7.2, or an absolute path), `service` (LogicalService name in the reference instance), `provides` (optional; default = the reference's contract for that service, read at check/up time).
S5.4.1 A key from another kind's set is an ERROR (`[S5.4] 'location' is not valid for kind 'external'`). S5.4.2 A `ciu_stack` whose `location` lacks either required file is an ERROR. S5.4.3 Two Realizations MUST NOT share a `location` (rendered artifacts are per directory). S5.4.4 Plain compose files without a stack file are not a Realization kind: wrap them in a `ciu_stack` (a stack file with one service per compose service).

### S5.5 Deploy set
S5.5.1 The deploy set of an instance is the set of Realizations reached from the selected bundles: bundles → LogicalServices → selected variant (S9.3) → Realization. `external` and `joined` Realizations are in the deploy set but are not brought up (S8.7). S5.5.2 A Realization no selected variant reaches is not deployed and not rendered; a `ciu_stack` in that state is still loaded and schema-checked (I2). S5.5.3 A Realization all of whose services are disabled (S6.7) leaves the deploy set; a LogicalService whose selected Realization left it is an ERROR only when some `init_requires` or `uses` names it.

### S5.6 TypedFacts
| kind | selector | probe (live) |
|---|---|---|
| `pg:db/<name>` | database name | `SELECT 1 FROM pg_database WHERE datname=…` inside the provider container as `probe_user` |
| `pg:role/<name>` | role | `pg_roles` |
| `pg:schema/<name>` | schema in `registry.postgresql.database` | `information_schema.schemata` |
| `minio:user/<name>` | IAM user | `mc admin user info` inside the provider |
| `minio:bucket/<name>` | bucket | `mc ls` |
| `vault:secret/<path>[#field]` | KV path | read via CIU's Vault client at the route of the `vault` LogicalService (S10.3); `#field` is ignored for minter matching (S8.3) |
| `http:<path>` | GET `<path>` on the provider's first `http`/`https` endpoint returns 2xx | HTTP |
| `pki:issuer/<network>` | the provider issues certificates for network `<network>` (S10.5) | Vault read of `pki/<network>/ca` |
S5.6.1 The **provider container** of a fact is the RealizedService whose `init_provides` lists it, whose `GEN_TO_VAULT` directive derives it, or under whose key `[hooks.provides]` lists it; for contract facts of an empty-listing provider it is the variant service. CIU MUST NOT assume any service key. S5.6.2 `probe_user` (S6.2) names the database superuser used by `pg:*` probes; absent when a `pg:*` fact is probed → ERROR. S5.6.3 A probe result MUST distinguish `absent` (fact not present), `unreachable` (container not running / exec failed), and `starting` (container exists, not healthy), and report which.

---

## S6 Stack file — `[ciu_stack.<svc>]`

### S6.1 Root
S6.1.1 A stack file's service tables live under `[ciu_stack.<svc>]`; the same root is bound in the stack's compose render (`{{ ciu_stack.<svc>.… }}`). S6.1.2 Any other top-level table is either reserved (`hooks`, `governance`) or consumer data (S3.5.6). S6.1.3 A stack file MUST NOT contain `stack_name`, `name` or `location` keys at service level (the Realization name is bound by the registry; a service key equal to the Realization name is the S4.2.2 pattern and is fine).

### S6.2 Service keys (closed set; consumer scalars are NOT admitted here — put them in a sub-table)
| key | type | default | meaning |
|---|---|---|---|
| `image` | string `name[:tag][@digest]` | required | the one image declaration |
| `instances` | integer ≥ 1 | 1 | replica fan-out (S4.2.1) |
| `one_shot` | bool | false | runs to completion; gates wait for exit 0 |
| `primary` | bool | true iff the stack has one service | S8.6 |
| `enabled` | bool or `deploy.control` flag name | true | conditional inclusion (S6.7) |
| `init_requires` | list of LogicalService names | `[]` | ordering edge + route (S8.2) |
| `uses` | list of LogicalService names | `[]` | route only, no ordering edge (S7.8.3) |
| `init_provides` | list of TypedFacts | `[]` | S8.2 |
| `depends_on` | list of sibling service keys | `[]` | S8.2, S11.4 |
| `after` | list of LogicalService names | `[]` | S8.2 |
| `probe_user` | string | — | S5.6.2 |
| `aliases` | list of DNS labels | `[]` | extra aliases on the instance network (S11.4) |
| `host_network` | bool | false | `network_mode: host`; no hostname/network injection (S11.4) |
| `endpoints.<e>` | table | — | S6.3 |
| `health` | table | merged from `deploy.health` | S6.6 |
| `hostdir.<purpose>` | string or table | — | S6.8 |
| `configfile.<name>` | table | — | S6.9 |
| `secrets.<key>` | table | — | S10.2 |
| `<sub-table>` | table | — | consumer data (any name not reserved by S1.4) |
S6.2.1 A scalar key not in this table is an ERROR (`[S6.2] unknown key 'database' on [ciu_stack.postgres]; consumer data goes in a sub-table`). S6.2.2 A consumer sub-table named `identity`, `health`, `endpoints`, `hostdir`, `configfile` or `secrets` is an ERROR (`[S6.2] sub-table 'identity' is reserved on [ciu_stack.worker]`).

### S6.3 Endpoints — `[ciu_stack.<svc>.endpoints.<e>]`
`port` (integer 1..65535, required), `protocol` (`tcp|udp|http|https`, default `tcp`), `publish` (`instance|host|proxy`, default `instance`; S7.4), `host_port` (integer, default `port`; the host port used whenever the endpoint is published), `host_bind` (IP address; only with `publish = "host"`, default `0.0.0.0`), `allow_from` (list of `network.<n>` | `host.<h>`, optional), `path` (URL path, optional; required for `publish = "proxy"`; used by proxy routes and `http:` facts). S6.3.1 Endpoint names MUST be unique across all services of one stack (routes are keyed by LogicalService and endpoint, S7.8.1). S6.3.2 Two endpoints published on the same host of one layout MUST NOT share `host_port` (S7.4.5); before `up`, a `host_port` already published by another registered instance on the same machine (read from its rendered file) or already bound on the daemon is an ERROR (`[S6.3] host port 8080 in use by instance a1b2c3`); the overlay MAY override host ports per endpoint (S14.2). S6.3.3 `allow_from` targets MUST resolve.

### S6.4 Init edges
S6.4.1 `init_requires` and `uses` name LogicalServices; each MUST resolve to a variant in the instance's selection (S9.3); a name in both lists is an ERROR. S6.4.2 `init_provides` facts MUST match S5.6's grammar; a `vault:secret/<path>` that is also derived from a `GEN_TO_VAULT:<path>` directive in the same stack is an ERROR (declare it once — in the directive). S6.4.3 `depends_on` names MUST be sibling services of the same stack. S6.4.4 A service MUST NOT require or use a LogicalService its own Realization realizes (self-edge is an ERROR).

### S6.5 Replicas
S6.5.1 With `instances = N`, CIU renders one compose service per replica from ONE template block iterated by the template over `ciu_stack.<svc>.identity.replicas` (S11.3), renders each configfile once per replica (S6.9.3), and derives per-replica identities (S4.2.1). S6.5.2 Health and endpoints are per service; routes to a replicated service use the service-level `compose_key` (S7.8.6); a replicated service is healthy when every replica is (S8.6.4).

### S6.6 Health
S6.6.1 `[ciu_stack.<svc>.health]` keys: `interval`, `timeout`, `start_period` (durations), `retries` (integer), `gate_timeout` (duration). S6.6.2 CIU MUST merge `deploy.health` into every service's `health` table before any render, so `ciu_stack.<svc>.health.<k>` always resolves. S6.6.3 `gate_timeout` default = `start_period + interval × retries + 30s`. S6.6.4 The compose HEALTHCHECK itself is authored in the template from these values (S11.5); CIU reads container health from Docker (S8.6.4).

### S6.7 `enabled`
S6.7.1 A bool, or the name of a `[deploy.control]` flag (the flag's value applies). Expressions are not permitted. A disabled service is omitted from the graph, and CIU REMOVES its block from the rendered compose after the template renders (templates need not guard it, S11.4); a `depends_on` naming a disabled sibling is dropped with a WARN; a route to a LogicalService whose variant service is disabled is an ERROR at check time; a Realization all of whose services are disabled leaves the deploy set (S5.5.3).

### S6.8 Host directories — `[ciu_stack.<svc>.hostdir]`
`<purpose> = ""` (CIU-generated path `<physical_repo_root>/ciu-data/<R>/<svc>/<purpose>`), or `<purpose> = "/abs/path"`, or `<purpose> = { path = "…", uid = 1000, mode = "0750", seed = "relative/dir" }`. S6.8.1 CIU creates missing directories before compose runs and applies ownership from `uid` (default `instance.user_uid`) and mode through its privileged helper (an unprivileged CIU cannot `chown` to a foreign uid). S6.8.2 The resolved absolute host path is what the template reads as `ciu_stack.<svc>.hostdir.<purpose>`. S6.8.3 `ciu clean --vanilla` removes generated `ciu-data/<R>` directories through the same helper; `ciu clean` keeps them.

### S6.9 Config files — `[ciu_stack.<svc>.configfile.<name>]`
`template` (path relative to the stack directory, required), `target` (absolute in-container path, required), `mode` (default `"0440"`), `schema` (path to a JSON Schema, optional). S6.9.1 CIU renders `template` with the compose render context (S3.5.3) to `<location>/ciu.rendered.<svc>.<name>` and injects a read-only bind mount (absolute physical path, S11.4) to `target` into the rendered compose. S6.9.2 With `schema`, the rendered file MUST validate against it (ERROR otherwise). S6.9.3 Replicated services get `ciu.rendered.<svc>-<k>.<name>` per replica, rendered with `replica` bound to the replica's identity row.

### S6.10 Reserved tables and state
- `[hooks]`: `pre_secrets`, `pre_compose`, `post_compose` — lists of script paths (S12); `[hooks.provides] <svc> = [facts]` — facts hooks create, keyed by the service they are probed in (S8.6.3).
- `[governance]`: per-stack override of the global governance base (S13); shallow merge.
- `[ciu_stack.secrets.<key>]`: stack-level secrets (S10.2).
- State: hook-persisted non-secret data lives in `<location>/ciu.state.toml` (written by CIU from hook outputs, S12.3), is bound as `state` in the stack's render contexts, and is preserved across renders and `ciu clean`; `--vanilla` removes it. A key whose name matches S2.4.1's sensitive names is an ERROR (`[S6.10] secret-shaped key in state; use ciu.secrets.toml`). A `[state]` table in a stack file is an ERROR.

---

## S7 Topology

### S7.1 Model
Distance is never declared on a consumer or a provider. It is derived from **placement** (S7.6) × **networks** (S7.3) × the provider's **endpoint** (S6.3) × the provider's **kind** (S5.4). The only declarations that carry distance are an endpoint's `publish`, `host_port` and `allow_from`.

### S7.2 Hosts — `[deploy.hosts.<h>]` (in `ciu.hosts.toml`; S17.1 for lookup order)
Keys: `local` (bool; the machine running ciu; no SSH keys required), `ssh_host`, `ssh_user` (default `root`), `ssh_port` (default 22), `ssh_key` (path or secret directive), `known_host` (string, required unless `local` or `CIU_SSH_INSECURE_TOFU=1`), `bundle_dir` (default `/opt/ciu/current`), `push_mode` (`auto|rsync|scp`; `auto` = rsync when present on both ends, else scp), `bundle_excludes` (list, default `[".git"]`), `docker_optional` (bool; `activate health` skips Docker checks on this host), `[activate] bootstrap|apply|health|rollback` (command strings), `[secrets.<entry>]` (directives `ASK_EXTERNAL`/`GEN_LOCAL`/`ASK_FILE` only; stored under `[secrets.hosts.<h>]`, S10.4), `[addresses.<network>]` (string address per address-plane Network the host sits on). S7.2.1 Exactly one host MAY be `local = true`. S7.2.2 Every host named by a layout MUST exist.

### S7.3 Networks — `[network.<n>]`
Keys: `kind` (`address | proxy`, required), `realized_by` (Realization name, optional for `address`, required for `proxy`), `tls` (`none|tls|mtls`, default `none`), `pki` (LogicalService name; required when `tls ≠ none`), `fqdn` (hostname; required for `proxy`), `description` (optional — "tailscale mesh", "public internet"; CIU attaches no semantics to the wording). S7.3.1 The network `instance` exists implicitly for every instance (kind `address`, name per S4.2.1) and MUST NOT be declared. S7.3.2 A network with `realized_by` is **ready** on a host only when that Realization's variant service is healthy on that host (S8.3). S7.3.3 A `proxy`-kind network is **address-free**: hosts carry no address on it; it is selectable in `reach` by any host, and reaching through it resolves to the network's `fqdn` (S7.8). S7.3.4 `tls ≠ none` requires `pki`, whose contract MUST contain `pki:issuer/<n>` (S10.5).

### S7.4 Endpoint publication
S7.4.1 `publish = "instance"` (default): the endpoint is reachable on the instance network, and — **derived from the layout** — additionally published on the provider host, bound to the provider host's address on network `N`, for every network `N` over which some cross-host route (S7.8 step 5) reaches it: `ports: ["<addresses[host(R)][N]>:<host_port>:<port>/<tcp|udp>"]` (`http`/`https` map to `tcp`). On a single-host layout nothing is published. S7.4.2 `publish = "host"`: always published as `["<host_bind>:<host_port>:<port>/<tcp|udp>"]` in every layout. S7.4.3 `publish = "proxy"`: consumers reach the endpoint through a `proxy`-kind network in their `reach`; the **proxy itself** (the network's `realized_by`) reaches the endpoint by S7.8 rule 4 when placed on the same host, otherwise by S7.4.1's derived publication. What the proxy does with the route (TLS termination, authentication guards, rate limits) is the proxy stack's own configuration, rendered by its templates from `routes.*`; CIU carries no proxy policy. S7.4.4 `allow_from`: CIU resolves each entry to the set of addresses (all addresses of the named network's hosts, or the named host's addresses) and binds it as `ciu_stack.<svc>.endpoints.<e>.allow_from_resolved` (list of strings) for the stack's own templates. CIU does not program firewalls. S7.4.5 The set of (network, host_port) pairs published on one host of one layout MUST be collision-free (S6.3.2). S7.4.6 The publications derived for the rendered host are written to `[ciu.instance.resolved.realizations.<R>.<svc>.endpoints.<e>] published_on = [<networks>]` (S4.4).

### S7.5 Profiles — `[deploy.profiles.<p>]`
`services` (list of LogicalService names, required; MAY be empty only in a zero-stack project, S16.11), `compose_profiles` (list, optional), `env_overrides.<VAR>` (strings, optional). S7.5.1 Profiles do not nest. S7.5.2 Conflicting `env_overrides` values across selected profiles are an ERROR.

### S7.6 Layouts — `[deploy.layouts.<l>]`
`environment` (`dev|test|staging|prod`, required), `description` (optional), `hosts.<h>` (table per host, at least one): `bundles` (list of profile names; MAY be empty only in a zero-stack project), `reach` (list of network names, non-empty; `instance` allowed and means "this host only"). S7.6.1 A layout is always explicit; an instance with no `layout` selected (S14.2) is an ERROR. S7.6.2 Host declaration order is the push order (S17.2). S7.6.3 A Realization that is not `per_host` and is reached from the bundles of two hosts of one layout is an ERROR (`[S7.6] realization 'db_core' placed on both 'gstammtisch' and 'rs1002'`). S7.6.4 Every network in `reach` other than `instance` and `proxy`-kind networks MUST be one the host has an address on. S7.6.5 A `per_host = true` Realization (a transport daemon, a node exporter) MAY be reached from the bundles of several hosts of one layout and is deployed on each of them; no routes are derived **to** it (`[S7.6] per_host realization cannot be a route target`); network readiness (S7.3.2) and network/pki edges (S8.3) are evaluated per host.

### S7.7 Placement (derived)
`[ciu.instance.resolved.realizations.<R>] hosts = ["<h>", …]` for every `ciu_stack` Realization in the deploy set (one element unless `per_host`); `external` and `joined` Realizations have no host.

### S7.8 Route derivation (derived) — `route(C, X, e)` for consumer Realization `C`, LogicalService `X`, endpoint `e`
1. Resolve `X` to its selected Realization `R` (S9.3) — `mock` → ERROR (S9.3.4) — and `R`'s endpoint `e`: for a `ciu_stack`, an endpoint of the variant's service (S5.2), else of any service of `R` (names are unique per stack, S6.3.1); for `external`, an endpoint of the Realization. An endpoint that does not exist is an ERROR at check time when any template, `init_requires` or `uses` needs it. (A route whose `X` is a `per_host` Realization's capability is an ERROR, S7.6.5.)
2. `R.kind == joined`: read the reference instance's rendered `[ciu.instance.resolved.realizations.<R'>.<svc>]` for the service that declares `e` (S4.4); `network = <reference instance network name>`, `host = <that identity's container_name>` (or service `compose_key` when replicated), `port = e.port`, `requires = []`. The consumer's containers are attached to the reference network (S11.4).
3. `R.kind == external`: `url` as declared; `host`/`port`/`tls` parsed from it; `requires = []`.
4. `host(C) == host(R)`: `network = "instance"`, `host = identity(R, svc).container_name` (or service `compose_key` for replicated services), `port = e.port`.
5. Otherwise iterate `reach(host(C))` in order and pick the first network `N` that admits the pair: a `proxy`-kind `N` admits when `e.publish == "proxy"` and `C` is not `N.realized_by`; an `address`-kind `N` admits when both hosts have an address on `N` and `e.allow_from` (if set) admits `host(C)` on `N`. If `N` is `proxy`-kind: `host = N.fqdn`, `port` = the `host_port` of `N.realized_by`'s variant service's `publish = "host"` endpoint of protocol `https` (ERROR if it has none), `path = e.path`, `url = "https://<fqdn><path>"`, `requires += [N.realized_by]`. Else: `host = addresses[host(R)][N]`, `port = e.host_port`, and the endpoint is published on `N` (S7.4.1). No admitting `N` → ERROR (`[S7.8] no route from 'controller'@rs1002 to 'main_db.sql'@gstammtisch: no shared network in reach ["mesh"]`).
6. `requires` += `N.realized_by` (if any, for `address` networks) and `N.pki` (when `tls ≠ none`); `tls` from `N`; `cert`, `key`, `ca` = the `/run/secrets/tls_*` paths of the derived TLS secrets (S10.5) when `tls ≠ none`.
7. `path = e.path` is always copied when declared. For **direct** routes (steps 2–5 over an `address` network) `url` is emitted for `http`/`https` as `<scheme>://<host>:<port>` — the path is NOT appended, because a proxy prefix is not an application base path — and for `udp` as `udp://<host>:<port>`; for `tcp` no `url` is emitted. For **proxy** routes (step 5 through a `proxy` network) `url = "https://<fqdn><path>"` is the externally visible URL and includes the path.
S7.8.1 Routes are written as `[ciu.instance.resolved.routes.<C>.<X>.<e>]` with keys `network, host, port, url?, path?, tls?, cert?, key?, ca?, requires`. S7.8.2 In the render of stack `C`, `routes` is bound to `ciu.instance.resolved.routes.<C>`. S7.8.3 A route is derived for every (C, X, e) where C's services list X in `init_requires` or `uses`; a template reference to `routes.X.e` where X is in neither is an ERROR (`[S7.8] template routes to 'tracing' but no service declares it in init_requires or uses`); `uses` derives the route and no ordering edge. S7.8.4 Cross-host routes require the network to be **ready** (S7.3.2) before the consumer's wave starts (S8.3). S7.8.5 The route derivation is the only code path forming addresses for service-to-service reachability; CIU's own Vault client (S10.3), probes (S5.6) and the gate (S16) read routes. S7.8.6 Replicated providers: `host` is the service-level `compose_key` on the instance network (compose DNS), or the host address cross-host; per-replica routes are not derived. S7.8.7 **CIU's own vantage point**: the pseudo-consumer `ciu` is placed on the host running CIU; routes for it are derived like any consumer's; when the result is on the instance network, CIU resolves `container_name` to the container's address via `docker inspect` on a native host, or attaches its own container to the instance network when `ciu.auto_connect_network = true` and `env_type = devcontainer`.

### S7.9 Networks (derived)
`[ciu.instance.resolved.networks.<n>] name, kind, realized_by?, fqdn?, tls?` for every declared network and the implicit `instance` network (`name` = S4.2.1).
## S8 Ordering: init graph, waves, gates

### S8.1 Nodes
The graph's nodes are the RealizedServices of every `ciu_stack` Realization in the deploy set. `external` and `joined` Realizations are sinks that are always satisfied (their facts are asserted by `provides`, optionally probed).

### S8.2 Declared edges
- **init** — for every `init_requires` entry `X` on service `s`: an edge from `s` to every RealizedService of `X`'s selected Realization whose provides (declared or derived, S8.3 **facts**) intersect `contract(X)`, plus the variant's service (S5.2; the primary, S8.6, when the variant names none). When `contract(X)` is empty the edge goes to the variant's service only. An `init_requires` entry that a derived edge already implies is not a finding.
- **depends** — `depends_on` siblings; also rendered into compose (S11.4).
- **after** — `after` entries, resolved like `init_requires` but carrying no fact semantics; a redundant `after` (already implied) is a WARN.
- `uses` entries derive routes only (S7.8.3), never edges.

### S8.3 Derived edges (never declared; always listed in `edges`)
- **facts** — every fact in `init_provides`, in `[hooks.provides.<svc>]`, and every `vault:secret/<path>` derived from a `GEN_TO_VAULT:<path>` directive is attributed to its service; S5.3 conformance and minter resolution use the union.
- **secret→vault** — from every service that declares a secret whose directive is `ASK_VAULT` or `GEN_TO_VAULT` to the variant service of the Realization selected for the `vault` pointer (S10.3). Exempt: services of that Realization itself.
- **secret→minter** — from every service with `ASK_VAULT:<path>[#field]` to the service in the deploy set whose facts contain `vault:secret/<path>` (field ignored); an `ASK_VAULT:pki/<N>/…` path (S10.5) is satisfied by the provider of `pki:issuer/<N>`. No minter and no `external`/`joined` Realization providing it → ERROR (`[S8.3] nobody mints vault:secret/db/postgres/controller_password`).
- **network** — for every route of a consumer Realization `C` over an `address` network `N` with `realized_by = T`: from every service of `C` to `T`'s variant service on `host(C)`, and — when `T` is `per_host` — also on `host(R)` (the provider host's own `T` instance).
- **pki** — from every service with a route over a `tls ≠ none` network, and from every service with an endpoint reached over such a network, to the variant service of the `pki` LogicalService's Realization.

### S8.4 Waves
S8.4.1 A Realization's level is the maximum topological level of its services over the edge set of S8.2–S8.3, computed on the **Realization** graph (a Realization is deployed as a unit). A cycle at Realization level is an ERROR naming the cycle, even when the service-level graph is acyclic — deliberate: units deploy whole. S8.4.2 Wave `k` is the set of Realizations of level `k`. Within a wave, per host, stacks are brought up in name order. S8.4.3 Cross-host: wave `k` on any host starts only after every provider of an edge into wave `k` has passed its gate on its own host (S8.5); CIU running on one host waits for a remote provider by probing its routes (S8.5.3), not by SSH or `docker exec`.

### S8.5 Gates
S8.5.1 After bringing up wave `k`, CIU waits, per provider service `p` on this host that has an incoming edge from a later wave: `p` is **healthy** (S8.6.4) within `p.health.gate_timeout` (S6.6.3). Timeout → ERROR naming `p`, its last observed state, and the budget. S8.5.2 Before starting wave `k+1`, every TypedFact required by an edge into `k+1` whose provider is on THIS host is probed (S5.6) with a bounded poll of the same budget; `starting` and `unreachable` are retried within the budget, `absent` is retried within the budget and reported as absent when it expires. S8.5.3 For a provider on ANOTHER host CIU probes reachability only (TCP connect / TLS handshake / HTTP GET per endpoint protocol against the derived route) — the provider host's own gate (S8.5.1–S8.5.2) is authoritative for its facts; fact-level cross-host probing is not performed. S8.5.4 The gates are written as `[ciu.instance.resolved.gates.<k>] healthy = [...]  completed = [...]  facts = [...]`. S8.5.5 `ciu up` succeeds when every wave's gate passed and every non-`one_shot` service brought up on this host is `Running` at the end.

### S8.6 Primary service, variant service, hook-provided facts, health
S8.6.1 A stack with more than one service MUST mark exactly one `primary = true`; a single-service stack's only service is primary. S8.6.2 The primary service is the default **variant service** (S5.2): unless a variant names another service, it is the exec target and image source (S16.4), the target of empty-contract edges (S8.2), and the service whose health stands for the capability in gate predicates. S8.6.3 `[hooks.provides] <svc> = [facts]` lists facts a post_compose hook creates, keyed by the service in which they are probed. S8.6.4 **healthy**: a container whose rendered block declares a `healthcheck` is healthy when Docker reports `State.Health.Status == "healthy"`; a container without one is healthy when `State.Running` is true; a `one_shot` service is **completed** when its container exited with code 0. A replicated service is healthy when every replica is. Gate providers that are not `one_shot` MUST declare a healthcheck (S11.5).

### S8.7 What "bringing up" means per kind
`ciu_stack`: hooks `pre_secrets` → secrets materialization (S10) → hooks `pre_compose` → `docker compose -p <compose_project> --project-directory <location> -f ciu.compose.yml up -d --remove-orphans` → gate → hooks `post_compose` → fact probes for the next wave. `external`: optional reachability probe of each endpoint (`ciu up --probe-external`), otherwise nothing. `joined`: verify the reference (S9.5), attach the consumer's containers to the reference network at compose time (S11.4).

### S8.8 Derived tables
`[ciu.instance.resolved] waves = [[…], …]`, `edges = [{from, to, kind}]` with `kind ∈ init|depends|after|secret→vault|secret→minter|network|pki`, and `gates.<k>` (S8.5.4).

---

## S9 Realness

### S9.1 Levels
`live` (the real thing; its init graph runs), `owned-seeded` (a prepared Realization the project owns whose `pg:`/`minio:`-class contract facts hold by construction — declared in `init_provides`, no one-shot job needed; `vault:secret/*` contract facts MUST be minted by a hook that writes the prepared credentials to Vault, declared in `[hooks.provides]`; a `one_shot` service it does declare runs normally), `simulated` (a stub implementing the contract's protocol), `mock` (in-process double: no Realization, no routes, no edges).

### S9.2 Declaration
`[deploy.realness] default = "<level>"` (required), `pin.<logical> = "<level>"` (committed per-service pins, optional). `[service.<n>.<level>]` variants per S5.2; `[service.<n>.mock]` is declared as an empty table (`mock = {}`).

### S9.3 Selection
S9.3.1 For every LogicalService in the selected bundles, the level is the first defined of: `--realness <n>=<level>` on the command line; `[ciu.instance.realness.<layout>].<n>` (S9.4); `[deploy.realness.pin].<n>`; `[deploy.realness].default`. S9.3.2 The chosen level MUST have a variant on the service (`[S9.3] service 'payment_api' has no 'simulated' variant`). S9.3.3 The selection is written to `[ciu.instance.resolved.services.<n>] level, realization, service` (`realization`/`service` absent for `mock`). S9.3.4 An `init_requires`/`uses` on a service selected at `mock` yields no edge and no route; a template referencing `routes.<n>.*` of a mocked service is an ERROR at check time (`[S9.3] 'payment_api' is mocked; no route exists`).

### S9.4 Immutable record (per layout)
S9.4.1 The first `ciu up` for a layout writes the resolved selection of every LogicalService in the deploy set into the generated file as `[ciu.instance.realness.<layout>]` (CIU-owned, S14.2). S9.4.2 Thereafter, an explicit selection (`--realness`, or a changed pin) that differs from that layout's record is an ERROR (`[S9.4] layout 'local' already runs main_db=owned-seeded; ciu clean --vanilla to reselect`). Services not yet in the record (added bundles) are selected per S9.3 and appended. S9.4.3 `ciu clean` preserves the record; `ciu clean --vanilla` clears the current layout's record. S9.4.4 `ciu push` strips the records of other layouts from the bundle (S17.3).

### S9.5 Joined Realizations
S9.5.1 `[realization.<n>] kind = "joined" instance = <ref> service = <X>` in an instance's overlay borrows `X` from the reference instance `<ref>` (an instance name per S14.7.2, or an absolute path of a checkout). S9.5.2 The joiner's variant row `[service.<X>.<level>] realized_by = "<n>"` MUST name the level the reference **actually runs** (read from the reference's rendered `[ciu.instance.resolved.services.<X>]` at check/up time); a mismatch is an ERROR (`[S9.5] reference 'primary' runs main_db=owned-seeded, this instance declares live`). CIU records the reference's level in the joiner's record (S9.4). S9.5.3 The reference MUST be up (its rendered file complete, S14.4.2, and the borrowed variant service healthy per S8.6.4) or `ciu up` refuses. S9.5.4 CIU takes a shared lock on the reference's rendered file while reading it (S14.4). S9.5.5 `ciu instance add --join <ref> --services a,b,…` writes these tables into the overlay of the current checkout (it does not create a git worktree); a hand-written overlay is equivalent. S9.5.6 `ciu clean` on a reference whose instance network still has containers of another instance attached (`docker network inspect`) is an ERROR naming the joiner.

---

## S10 Secrets

### S10.1 Directives
| directive | meaning | store |
|---|---|---|
| `ASK_VAULT:<path>[#field]` | read from Vault at deploy time | copied into the store |
| `GEN_TO_VAULT:<path>` | generate once if the Vault path is absent, then read | store + Vault |
| `GEN_LOCAL:<name>` | generate once, project-local | store |
| `ASK_EXTERNAL:<VAR>[,<VAR>]` | from `CIU_SECRET_<VAR>` env, else the store, else an interactive prompt | store |
| `ASK_FILE:<path>` | read a pre-provisioned file | not stored (path recorded) |
| `ASK_HOST:<entry>` | the host-scoped secret `[deploy.hosts.<h>.secrets.<entry>]` of the host this stack is placed on (S10.4) | read from `[secrets.hosts.<h>]` |
| `GEN_EPHEMERAL` | generated per run, never stored | — |
S10.1.1 Secret keys match `name`. S10.1.2 A `GEN_TO_VAULT:<path>` directive derives the fact `vault:secret/<path>` provided by its service (S8.3). S10.1.3 An `ASK_HOST:<entry>` directive on a stack placed by any layout on a host without that entry is an ERROR at check time (`[S10.1] host 'rs1002' declares no secret 'tls_cert_pem'`).

### S10.2 Declaration — `[ciu_stack.<svc>.secrets.<key>]` and `[ciu_stack.secrets.<key>]`
Keys: `directive` (required), `delivery` (`file | env | configfile | native | none`, **required**), `env_name` (env name; required when `delivery = "env"`), `mode` (default `"0400"`), `uid` (default `instance.user_uid`), `consumed_by` (`"hook"` — materialized for hooks only, not delivered), `produced_by` (profile name — the bundle that mints it, when a consumer bundle is deployed without the producer), `enabled` (bool or `[deploy.control]` flag name, default true — a disabled secret is neither materialized nor delivered and derives no edge). S10.2.1 `file`: the value is written to a temp copy (S10.7) and mounted read-only at `/run/secrets/<key>` in the declaring service (service-level) or in every service of the stack whose rendered compose block references `/run/secrets/<key>` (stack-level). S10.2.2 `env`: the value is passed as `env_name` through the compose process environment (S11.6); the rendered compose MUST reference it as `${env_name}`; ciu check stage 9 lists every env-delivered secret (restart-bound). S10.2.3 `native`: the application fetches the secret itself; CIU materializes nothing and delivers nothing; the row exists so the dependency (and the edges) are declared. S10.2.4 `none`: minted/registered by this stack for others; nothing is delivered here. S10.2.5 A secret key declared at both stack level and on a service of the same stack is an ERROR. S10.2.6 `configfile`: the value is available only through `secret("<key>")` in the declaring service's configfile templates (S3.5.3, S6.9); the rendered config file is then a secret-bearing artifact — mode 0400, owner per `uid`, exempt from the S2.4.2 scan by declaration, and removed with the temp copies on `down`/`clean`. `secret()` is never available in compose templates or stack TOML files.

### S10.3 Vault pointer and consumer paths
`[vault] service = "<logical>"` (required when any `*_VAULT` directive exists anywhere in the deploy set — `[S10.3] vault directives present but [vault].service is not declared`), `token_file` (path, optional; token source #2 after `VAULT_TOKEN`, before the bootstrap token in the store), `[vault.paths.<name>] = "<kv path>"` (consumer data: a DRY table for directive paths; never read by CIU). S10.3.1 CIU's Vault client connects to the route derived for the pseudo-consumer `ciu` (S7.8.7) to the `vault` LogicalService's first `http`/`https` endpoint. S10.3.2 The Vault bootstrap values (root token, unseal keys) are stored at `[secrets.<vault realization>.<variant service>.root_token|unseal_keys]` by the vault stack's hook through the hook output contract (S12.3), never in state.

### S10.4 Host-scoped secrets
`[deploy.hosts.<h>.secrets.<entry>]` (S7.2) accept `ASK_EXTERNAL`, `GEN_LOCAL` and `ASK_FILE`; they are materialized on the CIU host (prompting if needed) before a push (S17.3), stored under `[secrets.hosts.<h>.<entry>]` (the realization name `hosts` is reserved), and pushed only to host `<h>`. A service consumes one through a secret declared with `directive = "ASK_HOST:<entry>"` and any delivery (S10.1).

### S10.5 TLS material for `mtls`/`tls` networks
S10.5.1 For every Realization `C` with a route over a network `N` with `tls ≠ none`, and for every Realization `R` with an endpoint reached over such a network, CIU derives stack-level `file` secrets `tls_cert`, `tls_key` (both) and `tls_ca` (consumers, and providers when `mtls`) with directives `ASK_VAULT:pki/<N>/<realization>/{cert,key,ca}`; routes and the provider's endpoint table carry their `/run/secrets/tls_*` paths. S10.5.2 The `pki` LogicalService's contract MUST contain the fact `pki:issuer/<N>`, provided by the Realization whose hook issues certificates for the network (a `[hooks.provides]` fact); the derived `ASK_VAULT:pki/<N>/…` paths are satisfied by that provider (S8.3), and the **pki** edge orders it. Issuance itself is the hook's job (CIU runs no CA).

### S10.6 The store — `ciu.secrets.toml`
```toml
[secrets.<realization>.<service>.<key>]   # or [secrets.<realization>.<key>] for stack-level secrets
value = "…"  source = "<directive as declared, or hook:<script>>"  created = 2026-08-30T10:11:12Z
```
S10.6.1 One file per instance, mode 0600, written atomically (temp + rename) under the project secrets lock (S14.4.3). S10.6.2 `GEN_EPHEMERAL` values are never stored. S10.6.3 `ciu secrets show [--values]`, `ciu secrets reset <realization>[.<service>][.<key>]`, `ciu secrets migrate` (imports v7 stores; refuses to re-mint). S10.6.4 A value is refreshed from Vault on every `ciu up` for `ASK_VAULT`; `GEN_*` values are generated once and kept.

### S10.7 Temp copies
S10.7.1 For `file` delivery CIU writes `<location>/ciu.secret-temp-copy.<svc>.<key>.txt` (stack-level: `ciu.secret-temp-copy.<key>.txt`) before compose runs, mode/uid per S10.2, and removes them on `ciu down`/`clean`. S10.7.2 The file is rewritten in place on refresh so a running container's bind-mounted inode observes the new value.

### S10.8 Static rules (ciu check stage 9)
`delivery` present on every secret; `env_name` present and unique per stack for `env`; vault pointer per S10.3; every `ASK_VAULT` path has a minter or an asserting Realization (S8.3); `ASK_HOST` entries exist on every placement host (S10.1.3); `produced_by` names a profile; secret-shaped keys outside `secrets` tables refused (S6.10); env-delivered secrets listed as WARN.

---

## S11 Compose rendering

### S11.1 Pipeline
For each `ciu_stack` in the deploy set (in wave order), CIU renders `ciu.compose.yml.j2` with the compose context (S3.5.3), parses the result as YAML, removes disabled services (S6.7), applies the injections of S11.4, validates per S11.5, and writes `ciu.compose.yml`. The rendered file is the ONLY file passed to `docker compose` (`-f ciu.compose.yml -p <compose_project> --project-directory <location>`).

### S11.2 Template obligations
A template MUST write, per service: `image` (from `ciu_stack.<svc>.image`), and whatever compose fields the service genuinely needs (`command`, `environment`, `volumes`, `cap_add`, `restart`, `user`, `healthcheck`, extra `labels`, `ulimits`, `sysctls`, `devices`, `build` for project-built images — `image` stays required as the tag `ciu build` produces). It MUST use the service key as the compose service name (`services: <svc>:`), or `{{ r.compose_key }}` per replica block (S6.5); CIU renames the former to `compose_key` (S4.2).

### S11.3 Template prohibitions
A template MUST NOT write: `container_name`, `hostname`, `networks` (service-level or top-level), `secrets`, `depends_on`, `cgroup_parent`/`mem_limit`/`cpu_shares`/`oom_score_adj`/`pids_limit`, `ports`, `expose`, a label with a reserved key (S4.5.2), `name:` under `volumes:` that does not start with `{{ instance.project }}-{{ instance.id }}-`, `${VAR:-default}` or `${VAR:?…}` forms, `{{ env.* }}`. A violation is an ERROR naming the field (`[S11.3] template sets container_name for 'postgres'`), except `container_name`/`hostname` equal to the derived value (S4.3.2, removed silently). Replicated services (S6.5) MUST be emitted by iterating `ciu_stack.<svc>.identity.replicas`, one block per replica.

### S11.4 Injections (applied to the parsed YAML)
Per service (by service key, and per replica block): `container_name` and `hostname` (S4.2; `hostname` omitted when `host_network = true`); `networks: { <instance network>: { aliases: [<service compose_key>, <replica compose_key>, <container_name>] + aliases } }` plus the reference instance network for services of a Realization that has routes to `joined` services (omitted for `host_network`); `labels` (S4.5); `depends_on: { <sibling compose_key>: { condition: … } }` from `depends_on` (`service_completed_successfully` when the sibling is `one_shot`, else `service_healthy` when the sibling's RENDERED block carries a `healthcheck`, else `service_started`; a disabled sibling is dropped with a WARN); `secrets: [ { source: <key>, target: /run/secrets/<key>, mode } ]` and the top-level `secrets: { <key>: { file: <absolute physical path of the temp copy> } }` for `file` delivery; `ports:` per S7.4 (derived publications and `publish = "host"`); read-only bind mounts for config files (S6.9, absolute physical paths); governance fields (S13.3); `network_mode: host` when `host_network = true`. Top-level: `networks: { <instance network>: { external: true, name: … } }` (and the reference network); `name: <compose_project>`. Every path CIU writes into the file is absolute under `physical_repo_root`.

### S11.5 Validation of the rendered compose
Secret-free scan (S2.4.2); every `${VAR}` reference is in the allowed set (S11.6); every service has an `image`; a `healthcheck` is present for every gate provider that is not `one_shot` (`[S11.5] 'postgres' is a provider but declares no healthcheck`); YAML re-serialized deterministically.

### S11.6 Compose process environment and `${VAR}`
S11.6.1 The environment passed to `docker compose` consists of exactly: `deploy.env.shared.*`, the selected profiles' `env_overrides`, `COMPOSE_PROFILES` (from `compose_profiles`), and the `env_name` values of `env`-delivered secrets. Nothing else is forwarded; a template that needs a runtime switch declares it as data (a consumer sub-table) or as a secret. S11.6.2 A `${VAR}` in the rendered compose that is not in that set is an ERROR (`[S11.6] compose references ${FOO}, which no source provides`).

### S11.7 Rendered artifacts
`ciu.compose.yml` (S2.2), `ciu.rendered.<svc>.<cfg>`, temp secret copies. `ciu render` writes them without deploying; `ciu up` writes them under the instance lock immediately before compose runs.

---

## S12 Hooks

### S12.1 Lifecycle
`[hooks] pre_secrets`, `pre_compose`, `post_compose`: lists of executable paths relative to the stack directory (or absolute). Each runs, in list order, at its phase of S8.7, as a subprocess with the working directory set to the stack directory. A missing script or a non-zero exit aborts the run (`[S12.1] hook post_compose_vault.py exited 3`). `[hooks.provides]` per S8.6.3.

### S12.2 Context
Hooks receive a JSON document on stdin (`ciu_hook_context` version 1) containing: `phase`, `instance` (S3.5.4), the stack's rendered config (`ciu_stack` tables with identity and health), `routes` (S7.8) for the stack, `realization` (merged view of the deploy set), `secrets` (this stack's secret keys and their materialized values for keys with `consumed_by = "hook"` or `delivery != "none"`), and `state` (S6.10). They MUST NOT read `ciu.env`, the process environment for identity facts, or the rendered global file directly — every fact they need is in the context (I5).

### S12.3 Outputs
A hook MAY print a JSON document on stdout: `{"state": {...}}` merges into `<location>/ciu.state.toml` (S6.10; secret-shaped keys refused); `{"secrets": {"<key>": "<value>"}}` stores values under `[secrets.<realization>.<primary>.<key>]` with `source = "hook:<script>"` (S10.6) — the only way a hook writes a secret; `{"facts": ["…"]}` asserts `[hooks.provides]` facts were created (recorded, then probed per S8.5).

### S12.4 Validation hooks
S12.4.1 A hook script MAY support the argument `--validate`: `ciu check` stage 13 runs every hook of every stack in the deploy set with `--validate` and the S12.2 context minus secret values on stdin, and expects `{"findings": [{"severity": "WARN"|"ERROR", "message": "…", "rule": "…"}]}` on stdout (an empty list or no output = no findings; a hook that exits non-zero under `--validate` is itself an ERROR). S12.4.2 Severities map through `ciu.exit_on` (S15.2). S12.4.3 Validation runs MUST be side-effect-free; CIU runs them with a wall budget of 30 s each.
## S13 Resource governance

### S13.1 Vocabulary (shared by stacks and gate lanes)
The **resource key set** `RK` = `memory_max`, `memory_swap_max`, `memory_high`, `memory_low`, `memory_min`, `cpu_weight` (1..10000), `cpu_max` (`"<quota> <period>"` or `"max"`), `io_weight` (1..10000), `pids_max`. Sizes per S1.4. Every key is named after the cgroup-v2 controller file it governs (`_` for `.`); there are no alternative spellings — S13.3 is the only place a key is mapped onto a compose field.

### S13.2 `[governance]` (global base; per-stack `[governance]` shallow-merges over it)
Keys: `enabled` (bool, default false), `cgroup_parent` (slice name; `""` = `$CGROUP_PARENT_DEV_BACKGROUND`, required non-empty when enabled), `ksm_optin` (`builtin` | path), `exempt_services` (list of `<realization>.<svc>`), `memory_profile.default.ksm` / `memory_profile.services.<svc>.ksm` (`preload|wrapper|off`), all of `RK`, plus `io_read_iops_max`, `io_write_iops_max`, `io_read_bps_max`, `io_write_bps_max` (integers; `0` = derive from the baseline), `device` (block device; `""` = autodetect), `baseline_path` (path; `CIU_GOV_BASELINE_PATH` overrides). S13.2.1 `memory_min` is preflight-only: host `MemAvailable` below the sum of the deploy set's `memory_min` is an ERROR before compose runs. S13.2.2 Unknown keys are an ERROR (S3.8.1).

### S13.3 Application to containers
With `enabled = true`, CIU injects into every non-exempt service block: `cgroup_parent`, `mem_limit` ← `memory_max`, `memswap_limit` ← `memory_swap_max`, `mem_reservation` ← `memory_low`, `cpu_shares` ← `cpu_weight` (same numeric scale), `blkio_config` ← `io_*`, `pids_limit` ← `pids_max`; `memory_high` and `cpu_max` are written to the container's cgroup after start (compose has no field) and recorded in the effective table. S13.3.1 KSM: `ksm_optin = "builtin"` builds the shim into `$XDG_CACHE_HOME/ciu/ksm/` and bind-mounts it; `wrapper`/`preload`/`off` per service (`CIU_KSM=off` disables for one run).

### S13.4 Effective governance (derived)
`[ciu.instance.resolved.governance.<realization>.<svc>]` lists the effective values applied to each container (after merge and exemptions).

---

## S14 Instances

### S14.1 Lifecycle
`ciu instance init` → (`ciu check`, automatic) → `ciu up` → `ciu gate …` → `ciu down` | `ciu clean [--vanilla]`. S14.1.1 `init` writes the generated file (S14.2), creates the overlay from a template when absent (with `--layout`/`--bundles`/`--label` filling the operator table), and on a git checkout writes the instance record (S14.7). S14.1.2 `up` renders, locks, deploys per S8, writes the realness record (S9.4). S14.1.3 `down` stops containers and removes the temp secret copies; everything else stays on disk. S14.1.4 `clean` removes containers, networks, named volumes labeled to the instance, temp secret copies, rendered artifacts (the S2.3 list minus the overlay, the generated file, `ciu.secrets.toml`, `ciu.hosts.toml`, `ciu.instance.json`, `ciu.state.toml`, `ciu-data/`), truncates the rendered global file to zero bytes, and preserves the realness records; `--vanilla` additionally clears the current layout's record, the store, `ciu.state.toml` files, `ciu-data/` (S6.8.3) and the instance record; it refuses while another instance's containers are attached to this instance's network (S9.5.6).

### S14.2 Overlay and generated file
The **overlay** `ciu.global.instance.toml.j2` is hand-edited only:
```toml
[ciu.instance]
layout = "local"                # required for every mutating verb (S7.6.1)
bundles = ["all", "test"]       # default bundle selection for `ciu up`
label = "primary"               # free text; `ciu instance list` shows it
[ciu.instance.host_ports]       # optional per-instance host-port overrides: "<realization>.<svc>.<endpoint>" = port
"cadvisor.cadvisor.http" = 18080
# plus [realization.<n>] kind = "joined" … and [service.<n>.<level>] rows for joins (S9.5)
```
The **generated file** `ciu.instance.generated.toml` is CIU-owned, plain TOML, rewritten whole by CIU, merged after the overlay:
```toml
[ciu.instance.generated]        # instance IDENTITY — identical on every host of a layout (the file travels with the bundle)
instance_id = "98535c"
[ciu.host.generated]            # HOST-LOCAL facts — regenerated on each host by `ciu instance init --host <h>`
name = "rs1002"                 # which [deploy.hosts.<h>] this machine is (`localhost` for the local host)
repo_root = "/opt/ciu/current"
physical_repo_root = "/opt/ciu/current"
public_fqdn = "rs1002.dchive.de"   # reverse DNS of the host's first `public`-described address, else ""
env_type = "native"             # devcontainer | native | github-actions
user_uid = 1000
user_gid = 1000
docker_gid = 988
[ciu.instance.build]            # written by `ciu build` (S18): version, time, image digests
build_version = "2026.08.30-9f3c1a2"
build_time = "2026-08-30T11:02:41Z"
[ciu.instance.realness.local]   # S9.4, one table per layout
main_db = "live"
```
S14.2.1 A hand edit to the generated file is overwritten without notice (the file carries that warning). S14.2.2 `ciu instance init --host <h>` sets `[ciu.host.generated].name`; without the flag, the host with `local = true` (S7.2.1) is assumed and MUST exist. S14.2.3 The overlay and the generated file ship with the bundle on `ciu push` (S17.3) so `instance_id` is identical across hosts; `[ciu.host.generated]` is then regenerated on the target by `init --host <h>` (activate bootstrap, S17.4). S14.2.4 `require_fqdn = true` (S3.4.7) makes an empty `public_fqdn` an ERROR at init.

### S14.3 Verb classes
- **Mutating (exclusive lock):** `instance init|add|remove|reap`, `up`, `down`, `clean`, `render`, `push`, `activate`, `build`, `secrets reset|migrate|rotate-bootstrap`.
- **Gate (shared lock):** `gate` (including nested `ciu gate` invocations from a `host`-environment lane; they coexist under S16.6 admission).
- **Read-only (shared lock while reading the rendered file):** `check`, `env print`, `instance list|show|exec`, `diagnose`, `secrets show`, `version`, `help`.

### S14.4 The instance lock
S14.4.1 The lock object is the rendered `ciu.global.toml`. Mutating verbs open it `O_CREAT|O_RDWR` and take `flock(LOCK_EX)`; gate and read-only verbs open it `O_RDONLY` (ENOENT or zero length = *not rendered*: read-only verbs report it, the gate refuses) and take `flock(LOCK_SH)`. S14.4.2 A mutating verb renders **in place** (truncate + write on the locked descriptor; never temp + rename) and writes `[ciu.instance.resolved.render] complete = true` as the LAST table; a non-empty file without it is *torn*: readers refuse it (`[S14.4] rendered config incomplete — a render is in progress or was interrupted; re-run ciu render`), and a mutating verb re-renders it. An empty file (after `clean`, or before the first render) is *not rendered* and is permitted before a mutating verb. S14.4.3 Lock order (deadlock-free by construction): instance lock → stack directory descriptor (`flock` on `<location>`, the per-stack secret phase) → project secrets lock (`flock` on the repo root directory descriptor; `ciu.secrets.toml` itself is written atomically and is never the lock) → S14.7 git-common-dir locks → a joined reference's instance lock (`LOCK_SH`, S9.5.4). The automatic `ciu check` inside a mutating verb reuses the verb's descriptor and takes no lock of its own. S14.4.4 After acquiring, CIU MUST compare `fstat(fd)` with `stat(path)`; on inode mismatch (the file was replaced or unlinked meanwhile) it closes, re-opens and retries (three attempts, then ERROR). S14.4.5 Contention: fail fast (`[S14.4] instance 98535c is locked by another ciu process; pass --wait`), naming the holder's pid and verb when `/proc/locks` and `/proc/<pid>/cmdline` make them available; `--wait[=<duration>]` blocks. A dead holder releases the lock by itself (`flock` is kernel-owned); there is no `lock break` verb. S14.4.6 `ciu clean` and `--vanilla` truncate the rendered file and never unlink it. S14.4.7 An unlink by an external tool (`git clean -x`) forks the mutex; CIU cannot detect this and the S2.3 list is documented as "ignore, do not delete while an instance is up".

### S14.5 Interleavings prevented
Two `up`; `up` ‖ `gate` (a gate lane exec-ing into a container being recreated); `instance init` ‖ render; `clean` ‖ `up`; two first-`up` realness selections; `secrets migrate` ‖ `up`.

### S14.6 `[ciu.instances]` — budget and lease (one closed key set)
`max_concurrent` (integer ≥ 1, optional; ambient override `CIU_MAX_CONCURRENT_INSTANCES`), `lease_ttl_hours` (number > 0, optional; absent = no lease). S14.6.1 The budget counts instances of one git family (S14.7) with a live record; `up` beyond the budget is an ERROR naming the holders. S14.6.2 A lease is renewed by every mutating verb and expires after `lease_ttl_hours`; `ciu instance reap` removes expired instances' containers and records (never their checkouts). S14.6.3 `ciu instance exec --env <e> -- <cmd>` runs a command in a gate environment (S16.4) — there is no separate exec-target table.

### S14.7 Instance registry
S14.7.1 Every checkout in a git family registers `ciu.instance.json` (gitignored; in S2.3's list) with `instance_id`, `path`, `created`, `lease_until` (the label is read from the overlay, not duplicated), and the family is enumerated through the git common directory (a lock file `ciu-instances.lock` there serializes allocation and budget). S14.7.2 An instance name in `[realization.<n>] instance` resolves to: the `label` of a registered instance, else the basename of a registered checkout path, else an absolute path; ambiguity is an ERROR.

---

## S15 `ciu check`

### S15.1 Invocation
`ciu check [--live] [--layout L] [--host H] [--bundles …] [--realness …] [--graph] [--gates] [--json]`. Runs automatically before every mutating verb with the verb's selection (`--no-check` skips; the skip is printed). Side-effect-free except stage 15's live probes (network reads).

### S15.2 Severities and exit
Findings are `ERROR` or `WARN`. `ciu.exit_on` ∈ `WARN|ERROR|NEVER` decides the exit code of the standalone verb: `WARN` → non-zero on any finding; `ERROR` → non-zero on errors only; `NEVER` → always zero (findings still printed). Inside a mutating verb, any ERROR aborts the verb and WARNs are printed; `ciu.exit_on` does not make a WARN abort a deploy. Every finding names its rule (`[S<n>.<m>]`), the file and table or line where applicable, and — for referential findings — the candidate names that would have matched (edit distance ≤ 2).

### S15.3 Stages (in order; a stage runs only if the previous produced no ERROR)
| # | stage | rules |
|---|---|---|
| 1 | files | S2.1–S2.3 (gitignore list; overlay and generated file present — skipped for `instance init`; no `.ciu/` — skipped for `secrets migrate`); rendered file empty, complete, or torn (S14.4.2) |
| 2 | render | S3.2 (strict undefined; no env context; template loader disabled); S3.5 (context violations, two-pass route access); S2.4.1 secret-free templates |
| 3 | schema | S3.3, S3.8 closed key sets, types, vocabularies, grammars, `revision`; reserved names (S1.4) |
| 4 | references | every name resolves: `realized_by`, variant `service`, `init_requires`, `uses`, `after`, `depends_on` (siblings), `enabled` flags, `produced_by`, `exec_in`, `image_from`, `requires.services`, `resources.shared`, `vault.service`, `network.realized_by`, `network.pki`, `allow_from`, `profiles.services`, `layouts.hosts.*.bundles`, `layouts.hosts.*.reach`, joined `instance`/`service`; `location` exists with both files and its root is `ciu_stack`; no shared `location` (S5.4.3); exactly one primary (S8.6.1); endpoint names unique per stack (S6.3.1); no unknown scalar on a service table (S6.2.1) |
| 5 | contracts | S5.3 conformance over declared + derived provides; unclaimed facts (WARN); minter resolution for every `ASK_VAULT` path (S8.3) |
| 6 | graph | S8.4 cycles; waves; redundant `after` (WARN); mocked services referenced by routes (S9.3.4); template routes without `init_requires`/`uses` (S7.8.3) |
| 7 | topology | S7.2.2 hosts exist; `local` uniqueness; addresses for every `address` network in `reach` (S7.6.4); placement (S7.6.3, S7.6.5); every derived route resolves (S7.8 step 5 refusals); proxy networks have `fqdn`, `realized_by`, and an `https` host-published endpoint (S7.8 step 5); TLS networks have `pki` with `pki:issuer/<n>`; published (network, host_port) pairs unique per host (S7.4.5); push order consistent with cross-host edges (S17.2) |
| 8 | identity | S4.3 uniqueness; template `container_name`/`hostname` equal derived or absent; reserved labels (S4.5.2); compose keys qualified |
| 9 | secrets | S10.8 |
| 10 | realness | S9.3.2; consistency with the layout's record (S9.4.2); joined references resolvable and level-consistent (S9.5.2, requires the reference's rendered file) |
| 11 | governance & resources | S13 keys and ranges; slice resolvable; `memory_min` headroom (WARN statically, ERROR at up); `exec` lane caps ≤ target governance (S16.6.4) |
| 12 | testing | S16: every `assay_lane` names a lane in `assay.toml` (lane names read with a TOML parser; nothing else interpreted); environments/requires/shared resolve; `required_env ⊆ forward_env` for container environments; judge floor satisfiable by the installed judge (when reachable); `evidence_dir` ignored and writable. *CIU-72: when the judge is reachable, also `assay lanes --json` (assay B044) → per-lane `external_tools`/`argv0` present in the lane's environment, and `base_source` agrees with the lane's `request_base`* |
| 13 | hooks | S12.4 `--validate` |
| 14 | registry validator | `ciu.registry_validator` |
| 15 | live (`--live`) | S8.5 probes for the selected waves; cross-host reachability; joined references up; host ports free (S6.3.2) |

### S15.4 Output
Human-readable by default (grouped by stage, ERRORs first); `--json` emits `{stage, rule, severity, message, file, table, candidates}` records and the derived tables of S3.7 under `resolved`. `--graph` prints waves and edges (kind-annotated). `--gates` restricts to stage 12 plus the gate's own preconditions (S16.5).

---

## S16 The gate — `ciu gate`

### S16.1 Model
A **lane** runs one command (or one judge invocation) in an **environment**, subject to **preconditions** (realness, service health), under **resource caps** enforced through cgroup v2 and an **admission** step, and produces a **LaneResult**. Lanes are declared in the global configuration; assay lanes additionally name a lane in `assay.toml` (assay's own file and schema; CIU reads it only to verify lane names).

### S16.2 `[testing]`
`cgroup_slice` (slice name; default = `governance.cgroup_parent`), `evidence_dir` (directory for artifacts and verdicts, default `ciu-gate-evidence/` — gitignored; CIU verifies it is ignored).

### S16.3 `[testing.judge]`
`version` (`version-floor`, required). S16.3.1 Before any assay lane, CIU runs `assay --version` in the lane's environment and refuses when the version does not satisfy the floor (`[S16.3] judge 2.3.1 < floor >=2.4`). S16.3.2 Every verdict MUST carry `judge_provenance` (CIU passes `--require-judge-provenance`); a verdict without it is a lane ERROR. There is no opt-out.

### S16.4 `[testing.environments.<e>]`
`mode` (`ephemeral | exec | host`, required). `exec`: `exec_in` (LogicalService; the container is its variant's service (S5.2) via the derived identity; NOT_RUN/`environment-down` when not healthy per S8.6.4). `ephemeral`: `image` (string) or `image_from` (LogicalService → its variant service's `image`); the container runs `--rm` on the instance network in the slice. `host`: a plain subprocess. Common: `forward_env` (list of env names allowed into the lane from CIU's environment), `extra_mounts` (list of `host:container[:mode]`), `workdir` (default: the checkout root inside the container). S16.4.1 `host` is also available implicitly as an environment name when no environment table defines it. S16.4.2 `required_env ⊆ forward_env` is required for container environments only. *CIU-73: an environment also provides a lane's dependency closure — an offline package cache (npm, Go modules) baked into the image or mounted via `extra_mounts`; assay's snapshot carries committed objects only, so a JS lane rebuilds its in-tree `node_modules` offline from the committed lockfile (assay B041 (a)) or declares `isolation.link_paths` (B041 (b), recorded in the verdict). CIU-72: a lane's tool needs (`assay lanes --json` → `external_tools`/`argv0`) are checked against the environment at `ciu check` stage 12.*

### S16.5 `[testing.lanes.<l>]`
`kind` (`command | assay`, required), `environment` (name, required), `argv` (list, `command`; `{worktree}` is substituted with the checkout root as seen inside the environment), `assay_lane` (string, `assay`), `request_base` (bool, `assay` only, default false: pass `--request-base`, S16.7), `description`, `clean_tree` (bool, default true — a dirty tree is NOT_RUN/`dirty-tree`; `--allow-dirty` overrides), `budget` (duration, enforced: the lane is killed and reported `BUDGET_EXCEEDED`), `required_env` (list; missing → NOT_RUN/`env-missing`), `artifacts` (list of paths copied into `evidence_dir/<lane>/`), `requires = { realness = { <logical> = <level> }, services = [<logical>…] }`, `resources = { <RK subset>…, shared = [<logical>…] }`. S16.5.1 `requires.realness` compares against the current layout's record (NOT_RUN/`realness-mismatch`). S16.5.2 `requires.services` requires each named service's variant service to be healthy per S8.6.4 (NOT_RUN/`service-down`). S16.5.3 `resources.shared` names LogicalServices whose Realizations the lane uses exclusively: lanes sharing a name serialize on `ciu.gate.shared-<realization>.lock` in the checkout that OWNS the Realization (the reference checkout for `joined` Realizations), so worktrees sharing a database serialize against each other. *CIU-72: `request_base` is derivable from the assay lane's own `judge.base_source` (`assay lanes --json`, assay B044) — one fact, one spelling; when restated here it must agree, else `[S16.5] request_base = false but assay lane '<l>' delegates its base`.*

### S16.6 Admission and caps
S16.6.1 Before starting, a lane's `memory_max` is checked against the slice's current `memory.max` minus the sum of running lanes' `memory_max`; insufficient headroom waits up to `--admission-wait` (default 10 m), then NOT_RUN/`no-headroom`. S16.6.2 `ephemeral` lanes run with `--cgroup-parent <slice>` and their caps written to their cgroup before the process starts; `host` lanes run in a child cgroup of the slice. S16.6.3 `CIU_GATE_CGROUPFS_ROOT` overrides the cgroupfs mount for tests. S16.6.4 `exec` lanes run inside the target container's cgroup: their `resources` are validated to be ≤ the container's effective governance (S13.4; `[S16.6] lane 'unit' asks memory_max 3G but 'tester' is capped at 800M`) and admission counts against the container's `memory.max`; caps that must differ require an `ephemeral` environment.

### S16.7 Assay invocation
`assay run <assay_lane> --file assay.toml --require-judge-provenance --verdict-json <evidence_dir>/<lane>/verdict.json [--request-base <REF>]`, executed inside the environment with the checkout mounted at `workdir`. S16.7.1 `--request-base` is passed exactly when the CIU lane declares `request_base = true`; `REF` is `--base` if given, else the merge-base of `HEAD` and the checkout's upstream branch (`[S16.7] no upstream; pass --base`). S16.7.2 The lane's outcome is the verdict's `outcome`; `judge_provenance` and the resolved `REF` are copied into the LaneResult.

### S16.8 Outcome vocabulary
`PASS`, `FAIL`, `ERROR`, `NOT_RUN` (with reason ∈ `realness-mismatch | service-down | environment-down | env-missing | dirty-tree | no-headroom | judge-floor | judge-provenance`), `BUDGET_EXCEEDED`. Exit codes: PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET_EXCEEDED 4. A conjunction lane's exit is its command's.

### S16.9 LaneResult — `ciu.gate.<lane>.json`
`{ facts_schema: 1, lane, kind, environment, instance_id, layout, started, ended, outcome, reason?, exit_code, budget, resources_applied, request_base?, judge_provenance?, verdict_path?, artifacts: [...], preconditions: {...} }`. Written under `evidence_dir/<lane>/` as well. *CIU-72: add `helpers?: [...]`, copied verbatim from the verdict — the Go statement-position oracle's identity lives there (assay A-230a/A-239); a Go LaneResult without it is not reproducible.*

### S16.10 CLI
`ciu gate [<lane>…] [--list] [--dry-run] [--json] [--base REF] [--allow-dirty] [--check-env] [--worktree PATH] [--admission-wait D]`; `--worktree` runs against another checkout's instance (its overlay decides identity); `--check-env` reports the environment/precondition state without running; `ciu gate doctor` reports the slice, cgroupfs writability, judge version and provenance status.

### S16.11 Zero-stack projects
A project with no `[realization]` entries MAY declare `[testing]` with `ephemeral` and `host` environments only (`exec_in` needs a Realization); `[deploy.layouts.local] hosts.localhost = { bundles = [], reach = ["instance"] }` and `[deploy.profiles]` MAY be empty; `ciu gate` then requires no `ciu up`.
## S17 Remote deployment

### S17.1 Host inventory lookup
`ciu.hosts.toml` in the checkout root, else `CIU_HOSTS_FILE`, else `~/.config/ciu/hosts.toml`; the first found is used entirely (no merging).

### S17.2 Static checks per layout
For `--layout L`: every host named exists; addresses cover `reach`; every cross-host route resolves (S7.8); host declaration order is consistent with the cross-host init graph — a host whose bundles have an edge into a host declared after it is an ERROR (`[S17.2] rs1002 needs core on gstammtisch, declared later`).

### S17.3 Push
`ciu push --layout L [--host H]` builds, per target host, a bundle of the checkout excluding `bundle_excludes`, `ciu-data/`, and every S2.3 artifact EXCEPT the overlay, the generated file (with the realness records of other layouts stripped, S9.4.4), `ciu.hosts.toml`, and a **reduced** `ciu.secrets.toml` containing only `[secrets.<R>.*]` for Realizations placed on that host plus `[secrets.hosts.<H>.*]`; host-scoped entries are materialized on the CIU host (prompting if needed) before transfer. The bundle is transferred to `bundle_dir` with `push_mode`, in layout order. S17.3.1 The overlay and generated file travel so that `instance_id` is shared; the target regenerates `[ciu.host.generated]` (S14.2.3).

### S17.4 Activate
`ciu activate --layout L [--host H] <bootstrap|apply|health|rollback>` runs the host's `[activate]` command over SSH in `bundle_dir`, in layout order, stopping at the first failure. The conventional `bootstrap` is `ciu instance init --host <H> && ciu check --layout L`; `apply` is `ciu up --layout L`. Render-on-target: each host renders its own `ciu.global.toml` from the same sources with `[ciu.host.generated].name = H`, so derived routes and publications are computed per host.

### S17.5 Cross-host runtime
CIU on a host deploys only the Realizations placed on that host (S7.7) and waits for remote providers by probing routes (S8.5.3). A host never opens SSH or `docker exec` to another host during `up`.

---

## S18 Command line

| verb | class | notes |
|---|---|---|
| `ciu instance init [--host H] [--move] [--layout L] [--bundles …] [--label X]` | mutating | S14.1, S14.2 |
| `ciu instance list \| show [<name>] \| add --join <ref> --services … \| remove <name> \| reap \| exec --env <e> -- <cmd>` | list/show/exec read-only; others mutating | S14.6, S14.7, S9.5.5 |
| `ciu check …` | read-only | S15 |
| `ciu render [--layout L] [--host H]` | mutating | S11.7 |
| `ciu up [--layout L] [--host H] [--bundles b,…] [--realness s=l,…] [--wait[=D]] [--probe-external] [--no-check]` | mutating | S8, S9 |
| `ciu down [--realization r]` | mutating | S14.1.3 |
| `ciu clean [--vanilla]` | mutating | S14.1.4 |
| `ciu gate …` | gate | S16.10 |
| `ciu secrets show [--values] \| reset <sel> \| migrate \| rotate-bootstrap` | show read-only; others mutating | S10.6 |
| `ciu env print` | read-only | prints `export`-lines for `instance.*` facts and the instance network; the only producer of `ciu.env` |
| `ciu build [--realization r]` | mutating | builds project-owned images (services with `build:`) tagged per `deploy.registry`; writes `[ciu.instance.build]` (`build_version` = `<date>-<git short sha>`, `build_time`, image digests); images in `deploy.provenance.vendor_images` are pulled, never built |
| `ciu push \| activate …` | mutating | S17 |
| `ciu diagnose [--json]` | read-only | container/log/health summary by labels |
| `ciu version` | read-only | prints tool version and `facts_schema` |
Global flags: `--json` where listed, `--layout`/`--host` default from the overlay and generated file, `--realization r` restricts a verb to one Realization.

### S18.1 Exit codes
0 success; 1 refusal with findings (`ciu check` policy); 2 usage; 3 lock contention; 4 remote failure; gate codes per S16.8.

### S18.2 Environment variables CIU reads
| variable | meaning |
|---|---|
| `CIU_EXIT_ON` | ambient fallback for `ciu.exit_on` (config wins) |
| `CIU_MAX_CONCURRENT_INSTANCES` | ambient budget override never written to a file (S14.6) |
| `CIU_SECRET_<VAR>` | `ASK_EXTERNAL` non-interactive input (S10.1) |
| `VAULT_TOKEN` | Vault token source #1 (S10.3) |
| `CGROUP_PARENT_DEV_BACKGROUND` | governance parent / gate slice when `cgroup_parent = ""` (S13.2) |
| `CIU_HOSTS_FILE` | host inventory path (S17.1) |
| `CIU_SSH_TRANSPORT` | `openssh` (default) or `paramiko` for push/activate |
| `CIU_SSH_INSECURE_TOFU` | `1` = accept an unknown host key once (S7.2) |
| `CIU_KSM` | `off` disables KSM for one run (S13.3.1) |
| `CIU_GOV_BASELINE_PATH` | overrides `governance.baseline_path` (S13.2) |
| `CIU_SKIP_DOOD_PREFLIGHT` | `1` = skip the docker-outside-of-docker mount check at startup (tests only) |
| `CIU_SKIP_DEPENDENCY_CHECK` | `1` = skip the startup check for `docker`/`docker compose` binaries |
| `CIU_GATE_EXTRA_MOUNTS` | additional `host:container[:mode]` mounts for every container lane (S16.4), comma-separated |
| `CIU_GATE_MOUNT_ALIAS` | `host=container` path alias applied to `{worktree}` substitution when the checkout is mounted under a different path in the lane container |
| `CIU_GATE_EVIDENCE_DIR` | overrides `testing.evidence_dir` (S16.2) |
| `CIU_GATE_CGROUPFS_ROOT` | overrides the cgroupfs mount (S16.6.3) |
| `NO_COLOR`, `TERM` | output styling |
| `CIU_LOG_PREFIX_TIME_SHORT` | `1` = short timestamps in log prefixes |
| `HOSTNAME`, `REMOTE_CONTAINERS`, `WORKSPACE_DIR`, `GITHUB_ACTIONS`, `USER` | environment detection during `instance init` only (S14.2) |
No other variable influences behavior; none is a configuration source.

---

## S19 Refusal catalogue (normative identifiers)
Every refusal is `[S<n>.<m>] <message>`; the message MUST name the offending file/table/key and, where a name failed to resolve, the closest candidates. Implementations MUST keep the rule identifier stable across releases; a rule that is withdrawn keeps its number retired. The catalogue is the set of `[S…]` identifiers in this document; `ciu check --json` emits `rule` for each finding so tests can assert on it.

---

## Appendix A — Adopting v8 from a v7 checkout (shape of the migration; not implemented by v8.0.0)
1. `ciu instance init --layout local --bundles …` in every checkout (writes the generated file and the overlay; `ciu.env` becomes an export).
2. Global config: set `revision = 8`; introduce `[service.*]` (with variant `service` where one stack backs several capabilities), `[realization]` (`per_host` for transport daemons), `[network.*]`, bundles as `[deploy.profiles.<p>] services`, explicit `[deploy.layouts.*]`, `[deploy.realness] default`, `[vault] service`, `[testing.*]`; remove `deploy.environment_tag`, `deploy.network_name`, `deploy.environment`, `[deploy.phases]`, `[topology.*]`, `[service.<n>] type/location`, `[deploy.resources]`, `ciu.repo_root/physical_repo_root`, `[ciu.worktree.exec_targets]`; rename governance keys to `RK`; rename `[ciu.worktree] max_concurrent_instances` → `[ciu.instances] max_concurrent`; replace `$VAR` references with literals or `instance.*`.
3. Stack files: re-root to `[ciu_stack.<svc>]`; `requires`/`provides` → `init_requires` (ordering + route) or `uses` (route only) / `init_provides` (facts not derivable from directives) / `[hooks.provides.<svc>]`; add `endpoints` (`publish` only for always-published or proxied endpoints — cross-host publication is derived); add `delivery` (and `env_name`) to every secret; move consumer scalars into sub-tables; mark `primary`; replace hand-declared replicas with `instances`; drop `name`, `stack_name`, `image_name`/`image_tag`, `internal_port`, `[<svc>.ports]`, `[<svc>.resources]`, `[state]` (moves to `ciu.state.toml`).
4. Compose templates: remove `container_name`, `hostname`, `networks`, `secrets`, `depends_on`, `ports`, `expose`, reserved labels, the `x-defaults` label/resource anchors, `${VAR:-…}`/`${VAR:?…}` forms, `{{ env.* }}`; replace identity/topology reads with `ciu_stack.*.identity`, `routes.*`, `instance.*`; replicated services iterate `identity.replicas`. Config-file templates need the same re-rooting (`<root>.*` → `ciu_stack.<svc>.*`, `deploy.environment_tag` → `instance.id`, `topology.*`/`app_identity.*` → `routes.*`/`realization.*`), and every `secret()` call requires `delivery = "configfile"` on that secret.
5. `ciu secrets migrate`: imports `.ciu/secrets/*` and `[state]` Vault values into `ciu.secrets.toml`, moves non-secret `[state]` into `ciu.state.toml`, then deletes `.ciu/`.
6. Gate: `run-gate.toml` → `[testing.*]`; `pins`/`assay_command`/`memory`/`container_name` removed; `request_base = true` on lanes whose assay lane delegates its base; verdict paths move to `evidence_dir`; `assay.toml` `derived:` facts repointed at `ciu.instance.resolved.*`.

## Appendix B — Worked example
`ciu/docs/v8-dstdns-demo/` renders dstdns (27 Realizations, 4 hosts, 4 layouts, 8 lanes) in this notation, including a hand-written excerpt of the derived tables. Its README lists the decisions the conversion took and the places where the demo deliberately deviates from the v7 sources.
