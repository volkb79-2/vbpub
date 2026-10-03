# CIU v8 Proposal — Integrated Configuration Model, Deployment Graph, and Native Testing Gate

**Status:** PROPOSAL — not yet normative. The normative companion `SPEC-V8.md` is still 8.0.0-draft.7 and lags this revision: it changes only after the operator signs off revision 4.1, and Appendix R lists every rule draft.8 must change. Until then, this revision is the newer statement for the rules Appendix R names, and draft.7 is the more precise one everywhere else. The worked examples are `v8-dstdns-demo/`; Appendix R.2 lists the demo files revision 4.0 makes stale.
**Author:** dstdns/vbpub joint design sessions (2026-08-22 → 2026-09-03); revision 2.x produced by the wholistic-integration pass with the operator interviewed live on every fork, hardened by two review rounds (§4.3.11); revision 3.0 produced by a fresh adversarial review of the whole design set (`CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`, 78 findings) with the operator interviewed on ten forks (§4.3.1); revision 3.1 produced by an **independent third-party review** (`CIU-V8-THIRD-PARTY-REVIEW-2026-09-02.md`, 35 findings T-01..T-35, seven alternative designs; dispositions in `CIU-V8-THIRD-PARTY-REVIEW-RESPONSE-2026-09-03.md`) with the operator deciding two forks (§4.3.13); revision 3.2 produced by the same reviewer's **round-2 delta audit** (`CIU-V8-THIRD-PARTY-REVIEW-ROUND2-2026-09-03.md`: the disposition audit of T-01..T-35 and ten new findings T2-01..T2-10; §4.3.14) together with two operator design answers of 2026-09-03 (the lease primitive, monorepo governance) and the decision to implement v8 as the new subproject `vbpub/ciu8` (§4.3.1, §4.4); revision 3.3 produced by **round 3** of the same reviewer (`CIU-V8-THIRD-PARTY-REVIEW-ROUND3-2026-09-03.md`, T3-01..T3-10; §4.3.15); revision 3.4 adds **host enrollment** on operator direction of 2026-09-03 (`CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2, CIU-93; §4.3.16), for v8 and as a v7 backport; revision 4.0 reconciles the design with what ciu 7.12–7.15.1 and run-gate 23.8.0–23.9.1 (rev 46) shipped after 2026-09-03, integrates RG-55 (the cgroup-profiler daemon contract 1 and its v1.1 amendment), and applies the operator's 2026-10-03 rulings — dstdns D-646/D-647 and the two interview rounds D-651 and D-652 on the reconciliation memo `CIU-V8-RECONCILIATION-2026-10.md` (§4.3.17); revision 4.1 applies the operator's third-round answers on revision 4.0's open questions (dstdns D-653; §4.3.17 "4.1 amendments")
**Supersedes:** every prior revision of this file (1.5 through 2.1); the `[deploy.phases]` hand-ordered deployment model; the `[service.<n>] type/location` registry; the `[topology.*]` hand-declared routing tables; the `.ciu/` machine-owned directory convention; `ciu.env` as a configuration source; Jinja-templated declaration files; the `routes` render binding and the `init_requires`/`uses`/`after` edge keys of revision 2.x; the secret directive string grammar; the rendered file as the instance lock; and, by revision 4.0, the posture that run-gate stays a parallel standalone gate, the owner token (`owner_id`, `ciu.owner`), the checkout-directory instance lock, `ciu.instance.json` as registry, the count-based instance budget, the slice-directory per-uid admission ledger, ad-hoc per-instance joins, and the 6-hex instance id
**Target:** CIU v8.0.0 (breaking; `project.revision = 8` gates config acceptance)

**Proposal revision:** 4.1 (4.0 amended by the operator's round-3 answers, dstdns D-653: Q7 decided, admission charges the warm working set, Q15 reopened, Q16 decided as ciu config policy; traced in §4.3.17 "4.1 amendments" and §4.7 X113–X114. Revision 4.0 reconciled 3.4 with shipped ciu/run-gate behaviour and the 2026-10-03 operator rulings; every rev 4.0 change is traced in §4.3.17 and §4.7 X96–X112, and the SPEC-V8 changes it owes are Appendix R. Earlier revisions: every rev 3.1 change is traced to a T-finding in §4.3.13 and §4.7 X57–X72, rev 3.2 in §4.3.14 and X73–X84, rev 3.3 in §4.3.15 and X85–X94, rev 3.4 in §4.3.16 and X95)
**Updated:** 2026-10-03

**Source documents integrated.**
- *Revision 4.0 (2026-10-03); read in full unless a section is named:*
  - the reconciliation memo `CIU-V8-RECONCILIATION-2026-10.md` (vbpub@5160c0afa) and the consumer input `CIU-V8-CONSUMER-INPUT-DSTDNS-2026-10.md`;
  - dstdns `nyxloom-trove/decisions.md` D-646, D-647, D-651, and the round-2 record D-652 (operator answers to the memo's §6 Q1–Q14);
  - *revision 4.1:* dstdns D-653 (the round-3 answers); cgroup-profiler backlog CP-3 (DAMON paddr mode), CP-6 (DAMON series), CP-15 (reservation verb, vbpub@9b4bf835e); `lib/damon.py` (class thresholds) and `lib/summary.py` (the Summary's `damon` block);
  - `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` (contract 1 with RW-11/RW-21/RW-43, amendment v1.1 RW-34) and `DESIGN-2026-09-12-liveness-placement-admission.md` (D-17..D-32, amendments A1–A4);
  - run-gate `SPEC.md` R-04, R-10, R-29, R-35/R-35a/R-35b, R-39..R-44 at rev 46; `CHANGES.md` 23.8.0–23.9.1 and `[Unreleased]`; backlog RG-55..RG-77;
  - ciu `CHANGES.md` 7.12.0–7.15.1 and `[Unreleased]`; backlog CIU-94/95, CIU-104..106, CIU-110, CIU-115..119; `src/ciu/workspace.py`; `libraries/worktree` `SPEC.md` and `core.py`;
  - cgroup-profiler `CHANGES.md` and `lib/subtree.py`; `libraries/cli-extended` backlog CLI-EXT-02 (vbpub@ab52242e8);
  - SPEC-V8 draft.7 Appendix D.5–D.7, and the unmerged RG-55 note `c65ac2daa` (branch `rg55-p3-spec-v8-docs`);
  - dstdns `tools/test-runner/Dockerfile`, `tools/test-runner/ciu.compose.yml.j2` and `tests/conftest.py`; nyxloom `gate_runner.py` and `effects_gates.py` (how a gate's exit code is consumed).
- *Revision 3.4 and earlier:* this file at revision 2.1; `SPEC-V8.md` draft.2; `V8-REALIZATION-GRAPH.md`; `v8-dstdns-demo/` (65 files); ciu `docs/SPEC.md` 5.0.0 (v7, 4888 lines), `CHANGES.md` (through 7.11.0), `KNOWN_ISSUES_TODO_BACKLOG.md` (status table, CIU-72/73); `run-gate-project/SPEC.md` (R-01..R-38) and every `run-gate.toml` in the estate and in dstdns; assay `CHANGES.md` (B044 `assay lanes --json`), `src/assay/cli.py`, `runner.py`; a source-level dependency map of ciu, assay, run-gate, cmru, nyxloom and dstdns (review §2); the estate doctrine in `AGENTS.md`. Revision 2.1's own source list (dstdns decisions D-094..D-212, assay decisions through A-331, the v7 sources) stands behind the parts of this text that revision 3.0 did not change.

**How to read this document.** **Part 1** (§4.1, §4.3a, §4.4, §4.5, §4.6, §4.11) is the proposal itself, a self-contained statement of the v8 model. **Part 2** (§4.2, §4.3, §4.7, §4.8, §4.9, §4.10) is the rationale and audit trail: the inventory of everything considered, the reasoning walked through scenario by scenario, what each interview decided and why, every contradiction found and how it was resolved, what was dropped, and where the proposal knows it is incomplete. **Appendix R**, after Part 2, lists the SPEC-V8 changes revision 4.0 owes and the demo files it makes stale. Outside those rules, where this document and `SPEC-V8.md` differ, the SPEC is the more precise statement and this document is to be corrected.

**Conventions.** `S<n>` cites a section of ciu `docs/SPEC.md` 5.0.0 (v7) only in Part 2, §4.6 and §4.11; `V8-S<n>.<m>` cites a rule of `SPEC-V8.md` draft.7; `R-nn` cites a finding of the 2026-09-02 review; `CIU-<n>` / `RG-<n>` / `B<nnn>` / `D-<nnn>` / `A-<nnn>` cite the ciu, run-gate and assay backlogs and the dstdns / assay decision records; `P<n>` cites a guiding principle from §4.1.1. Examples use dstdns's real names because dstdns is the consumer whose configuration was inventoried key by key. Revision 4.0 adds: `Q<n>` cites a question of the reconciliation memo's §6 as answered in D-651/D-652; `RW-<n>` cites an RG-55 contract ruling; `vbpub@<hash>` / `dstdns@<hash>` cite commits. The dstdns instance id in examples is `hox0ju`, which 7.15.1 derives for `/workspaces/dstdns`. It is an example, never a literal anyone may depend on (CIU-119 (2)).

---

# Part 1 — The Proposal

## 4.1 The v8 model

### 4.1.0 What v8 is, in one paragraph

A ciu consumer declares **what** it needs (*LogicalServices*), **how** each need can be satisfied at each realness level (*Realizations* — ciu stacks, external systems, or another instance's services), **where** things run (*Hosts*, *Networks*, *Layouts*), and **which** bundles a deployment includes. Every consumer of a capability declares a **binding** under a local name of its own choosing and says how the resolved address is to be **delivered** to it — as environment variables, or as data its templates read — exactly the way a secret declares its delivery. From those declarations ciu **derives** everything that used to be typed by hand and drift: every container/compose/hostname identity, the resolution of every binding (same instance, joined instance, cross-host, through a proxy, over mTLS), the publication of endpoints across hosts, the deployment order (waves) and the health gates between them, the readiness of transports, the facts minted by secrets, the contract of every capability (from what is bound to it), the deploy set for a chosen realness, and the facts the testing gate needs. Every declaration file is plain TOML that any tool can read; templates exist only for the artifacts other programs consume (compose files, application config files). Every derived value is written as data into `ciu.resolved.toml`, where templates, hooks, the built-in gate (`ciu gate`, today's run-gate ported into ciu as the one gate implementation) and assay read it. There is one identity derivation (ciu 7.15's), one registry and lock authority (the git family's shared instance records), one secrets file, one judge declaration, one committed resource manifest, no `.ciu/` directory, no hand-ordered phases, no `ciu.env` as a source of truth, no `routes` a template pulls by provider name, and no Jinja in any declaration. Whether another lane or stack may start is decided by ciu against a host budget, from measured footprints the cgroup-profiler daemon supplies as data.

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
| *Instance* | `instance_id` (6 base36 chars, path hash; ciu 7.15.1) | One checkout's deployment; a zero-instance project (no Realizations) has none | the git family's shared record (`.workspace-instances/`, `libraries/worktree`) + `ciu.instance.toml` (operator) + `ciu.instance.generated.toml` (ciu) | selects *Layout*; records *RealnessVariants* per layout; applies one *JoinPreset* |
| *Identity* (derived) | per RealizedService/replica | `container_name`, `hostname`, `compose_key`, `compose_project`, `network` | rendered `[resolved.identities.*]` | one function (P6) |
| *Wave* (derived) | ordinal | Realizations deployed together | rendered `[resolved] waves` | from binding edges, `depends_on`, derived edges |
| *Environment* (gate) | name | Where a lane runs: an ephemeral container from an image or a LogicalService's image, `exec` into a Realization's variant service, or the host; may carry env-delivered bindings and *LaneServices* | `[testing.environments.<e>]` | `exec_in`/`image_from` → *LogicalService*; `services` → *LaneServices* |
| *Lane* (gate) | name | One command, one judge invocation, or a sequence of lanes, with preconditions and caps | `[testing.lanes.<l>]` | `environment`; `requires`; `assay_lane`; `lanes` |
| *LaneService* (gate) | (Environment, name) | A throwaway service, such as a database, started for one lane run and removed with it (RG-75) | `[testing.environments.<e>.services.<s>]` | `image_from` → *LogicalService* (its declared image; no running stack) |
| *JoinPreset* | name | A committed statement of which LogicalServices a linked-worktree instance borrows, and from which reference instance (Q9) | `ciu.toml` `[ciu.instances.join_presets.<p>]` | `services` → shareable *LogicalServices*; applied by `ciu instance init --join-preset` |
| *Footprint* | lane, or (Realization, service) | The measured memory and CPU demand of a lane or a container, distilled from profiled runs and committed (run-gate R-44 shape) | `ciu.footprint.json` (committed, written by ciu) | read by admission (§4.1.10a); replaces `memory_max` as the charge once measured (Q5) |

Relationship summary: a *Bundle* names *LogicalServices*; the instance's realness selection maps each to one *RealnessVariant*, hence one *Realization*; the **deploy set** is the closure of those Realizations. A *Layout* places bundles on *Hosts*; *Resolutions* are derived per binding from placement and *Networks*; *Waves* are derived from bindings. A consumer never names a provider's Realization, never forms an address, and never reads a provider by name in a template — it reads its own local name.

### 4.1.3 Files and layering

**Declaration chain (plain TOML, no rendering — P11):** `ciu.toml` (committed, the project's declarations) → `ciu.site.toml` (committed, optional, sparse site override) → `ciu.instance.toml` (gitignored, per instance, operator-owned: layout, bundles, label, joins, host-port overrides) → `ciu.instance.generated.toml` (gitignored, ciu-owned, rewritten whole: instance identity and build facts — true on every host, so it travels with a release unchanged) → `ciu.host.toml` (gitignored, ciu-owned, host-local facts; lives in the **state root**, never travels) → **`ciu.resolved.toml`** (gitignored; the merged declarations plus every derived table under `[resolved]`, written atomically; the one machine interface). Inherited policy tables (`[ciu] inherit`, §4.3.14) are merged underneath the chain. The git family's shared instance records (`<git-common-dir>/.workspace-instances/`, shared with cmru) sit beside the chain: they allocate the identity and are never merged into it.

**State root (rev 3.3, T3-02).** Every instance has one place for everything mutable: the checkout itself for a checkout (the operator's in-checkout posture), `<bundle_dir>/state/` for a release on a target — the host file, the instance record (with the realness records), the store, receipts, lease records, `ciu-data/`, the evidence directory and, in a release, the hook state. A release directory is byte-identical to its manifest for its whole life; only regenerable render outputs land in it. That is what lets a release switch or a rollback keep secrets, data and records (V8-S2.6). Merge semantics: scalars and lists replace, tables merge; nothing is deleted by a layer — a service, secret, binding or lane goes away with `enabled = false`.

**Per stack:** `ciu.stack.toml` (plain TOML; services under `[ciu_stack.<svc>]`, stack-level secrets under `[ciu_stack.secrets.<k>]`, `[hooks]`, `[governance]`, consumer tables) → re-rooted into the merged view under `realization.<R>.services.<svc>` (there is no per-stack rendered TOML and no stack-level override file: a site or instance layer overrides a stack table by its merged path, `[realization.consul_server.services.consul.endpoints.http] publish = "host"`). Artifacts: `ciu.compose.yml.j2` → `ciu.compose.yml` (identity, network, label, secret, config-file, port, `depends_on`, healthcheck timing, binding variables and governance stanzas **injected**); config-file templates → `ciu.rendered/<svc>/<mirrored target path>` mounted by parent directory (the v7 S5.3a hardening kept); `ciu.state.toml` for hook state; `ciu.secret-copy.<svc>.<key>` temp copies.

**Templates** see a fixed context: `project`, `instance`, `host`, `ciu_stack` (own services with `identity`, merged `health`, resolved `endpoints`, and — per service — `binds.<local>` for template-delivered bindings), `realization` (the merged view of the deploy set, read-only), `state`, `stack_dir`, the stack's consumer tables, user tables, `registry`, `vault.paths`, and `secret()` in config-file templates only. `StrictUndefined`; no `env`; no loader.

**Identity source.** `[ciu.instance.generated] instance_id`: shared by every host of a layout because the file travels with the release, and derived from the checkout path, so an outdated or mismatching file is regenerated in place (CIU-115). `[ciu.host]` in `ciu.host.toml`: which layout host this machine is, its hostname, uids and environment type, written per host into its state root; the checkout's roots are derived at every verb, never stored. Then `[ciu.instance.build]`, and the realness records in the instance record `ciu.instance.json`. `ciu env print` and `ciu resolve --json` export the same facts; `ciu.env` is never read, and no consumer re-derives an id (CIU-119 (2)).

**Secrets.** One gitignored `ciu.secrets.toml`; **Host inventory** `ciu.hosts.toml` (gitignored; `~/.config/ciu/hosts.toml` user-global); **Registry** the shared `libraries/worktree` records under `<git-common-dir>/.workspace-instances/` (allocation, family listing, leases; shared with cmru); **Instance record** `ciu.instance.json` in the state root (ciu's product record: realness records and backup references, never a lock or lease authority); **Resource manifest** `ciu.footprint.json` (committed); **Gate** `ciu.gate.<lane>.json` + `ciu-gate-evidence/`; **Data** `ciu-data/`. Nothing under `.ciu/`. The gitignore list ciu verifies: `ciu.resolved.toml`, `ciu.instance.toml`, `ciu.instance.generated.toml`, `ciu.host.toml`, `ciu.instance.json`, `ciu-leases/`, `ciu.compose.yml`, `ciu.state.toml`, `ciu.rendered/`, `ciu.secret-copy.*`, `ciu.secrets.toml`, `ciu.secrets.transport.toml`, `ciu.hosts.toml`, `ciu.gate.*`, `ciu.receipt.json`, `ciu.release.json`, `ciu.activation.json`, `ciu.inherited.toml`, `ciu.env`, `ciu-data/`, the evidence directory.

**Why plain TOML everywhere (R-08).** Revision 2.x rendered every declaration through Jinja, which meant (a) no external validator, editor schema or third-party tool could read a ciu file, (b) a typo in a *declaration* surfaced as a template error, (c) ciu could not safely write into a file it also rendered (X38 moved generated facts out for exactly that reason, and then `instance add --join` wrote into the Jinja overlay anyway — R-06), and (d) stack files that read derived values (`routes`) forced a two-pass render with a recording stub (R-05). Once consumers declare bindings (§4.1.5) and secret paths reference a checked `[vault.paths]` table (§4.1.8), no declaration needs an expression: what was `{{ vault.paths.x }}` is `path = "x"`; what was a `{% for %}` generating twenty near-identical service tables is the one-line string form `live = "x"`. `ciu schema --json` then emits a JSON Schema for every declaration file from the same table-spec that drives the validator.

### 4.1.4 Identity — one derivation

Inputs: `project.name` (committed, literal), `instance_id` (generated), the *Realization* name (registry key), the service key (stack file), and an optional replica index. Output, computed by one function and **written as data**:

```toml
[resolved.identities.db_core.postgres]
container_name  = "dstdns-hox0ju-db-core-postgres"      # {project}-{instance}-{realization}-{service}, `_` → `-`
hostname        = "dstdns-hox0ju-db-core-postgres"
compose_key     = "db-core-postgres"                    # qualified: no bare-alias collision on the instance network
compose_project = "dstdns-hox0ju-db-core"
network         = "dstdns-hox0ju-network"

[resolved.identities.controller.controller]
container_name  = "dstdns-hox0ju-controller"            # service == realization → the service part is omitted
```

Rules: the derivation is the only place these strings are formed — templates read them (`{{ ciu_stack.postgres.identity.container_name }}`; `{{ realization.db_core.services.postgres.identity.container_name }}` from elsewhere), hooks read them from their context, the gate reads the same table. Templates may not set `container_name:`/`hostname:` (equal values tolerated and removed). **Uniqueness is checked, not structural** (R-14): the `_`→`-` mapping is injective per name but not across the concatenation (`db_core`+`postgres` and `db`+`core_postgres` collide), so stage 8 refuses a collision naming both derivations. **Ownership labels are fixed** `ciu.project`, `ciu.instance`, `ciu.realization`, `ciu.service`, `ciu.replica`, `ciu.managed-by` (R-15): `clean`, `reap` and `diagnose` enumerate by them, and no consumer setting can orphan a container; consumer labels are authored in templates. `deploy.environment_tag` and `deploy.labels.prefix` are gone. Replicas: `instances = N` yields `-1..-N` suffixes and per-replica identity rows templates iterate.

**The instance id (rev 4.0, Q10).** `instance_id` is ciu 7.15.1's derivation, verbatim: six lower-case base-36 characters, `sha256(lexically canonical absolute path) mod 36^6`, computed by `libraries/worktree` `workspace_id_for_path` (vbpub@d1eb98770). The path is normalized for `.` and `..` but never resolved through symlinks, because it may name a host namespace this process cannot inspect. A ciu root below the git checkout root (a monorepo child) composes `<workspace_id>-<root_instance_id>` (`ciu/src/ciu/workspace.py` `root_identity_suffix`), so a v7.15 checkout keeps its id across the v8 cutover. The space is 36^6 ≈ 2.2·10^9, about 31 bits. Two live records that claim one id for different paths are refused at allocation with both paths named; nothing lengthens or replaces an id silently. There is no owner token. Deletion is guarded by the `ciu.checkout` label check and by the operator's `protected` flag (CIU-105's shape, §4.1.9). The derivation is not a consumer contract: consumers read `ciu env print` or `ciu resolve --json` (CIU-118, CIU-119 (2)). A future derivation change ships with repair-in-place of every outdated record (CIU-115) and with `ciu clean --identity <old>`, which removes exactly the resources labelled with a retired id.

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
# on `local`: network = "instance"  host = "dstdns-hox0ju-db-core-postgres"  port = 5432
```

The consumer's compose template contains no address at all for `env` delivery, and `{{ ciu_stack.controller.binds.tracing.url }}` for template delivery — identically in every deployment shape, and identically after `main_db` is re-pointed to a managed database, a seeded image or a simulator. **Gate environments bind the same way** (`[testing.environments.tester.binds.db] to = "main_db.sql" delivery = "env" env_prefix = "TEST_DB"`), which hands the facts to a lane process as environment variables — so assay needs only its existing `required-env:` facts and never reads ciu's file (R-03). CIU's own Vault client is the pseudo-consumer `ciu` with an implicit binding to `vault`.

**Remote deployment (push) — releases and receipts (rev 3.1, T-16/T-25; completed in rev 3.2, T2-01/T2-05).** `ciu push` builds, per host, a **release**: a mechanically computed closure — every declaration file, the instance files, every non-ignored file under each stack directory placed on that host, and every path a hook entry declares in `inputs` (hooks are arbitrary programs, so what they read outside their stack is declared, not guessed) — plus a manifest (`ciu.release.json`: every file with its SHA-256 and mode, every image with its id and repository digest, layout, instance, git revision), addressed by the manifest's digest. Images travel too: by registry digest when a registry is configured, else as a `docker save` archive verified on load; a name alone never stands for an image. The release is staged at `<bundle_dir>/releases/<digest>.staging`, verified on the target, renamed, and pointed to by `<bundle_dir>/candidate`; the release directory then stays byte-identical to its manifest for life, because everything mutable lives in the target's **state root** (`<bundle_dir>/state/`, §4.1.3). `ciu activate plan` writes an **activation manifest** — a fresh `activation_id` and, per host, the expected release digest and selection — and `ciu activate apply` runs, per host in layout order: the optional `bootstrap` host command, CIU's own **prepare** (the host file into the state root, `ciu check` in the target release), the host's `apply` command inside the candidate (or `--release <digest>`) with the manifest and the receipts so far, the host's `health` command, the receipt fetch, and only then the atomic `current`/`previous` switch — CIU's, never the host command's; any failure leaves the pointers where they were and CIU reports rather than compensates. `rollback` refuses without a `previous` and runs the same sequence for it: the previous release deploys itself, and a rollback that fails halfway leaves `current` unchanged (the host `rollback` command is gone: `ciu down` was never a rollback). An interrupted transfer can no longer produce a mixed tree, and an exclusion that intersects the closure is refused. Push order is the layout's declaration order, checked against the cross-host graph; render-on-target is unchanged. Each host's successful `ciu up` writes a **receipt** (`ciu.receipt.json`) whose **subject** is canonical and portable — the `activation_id`, instance, layout, the producing host, its release digest and selection — never the rendered file's digest, which `rendered_at` and host-local facts made unreproducible (T2-01); its body carries container incarnations, one-shot exits, per-fact observations, probes and publications. A consumer validates a provider's receipt against the manifest's entry **for that provider** (rev 3.3, T3-01 — every host runs its own release, so comparing with the consumer's own digest could never pass), requires the manifest's activation id, and treats an absent id as matching nothing; a required fact with no valid receipt is an ERROR by default and `--allow-assumed` is the explicit escape that records the hole in the evidence chain. Images are decided once per reference in an image map and travel by registry digest or verified archive under a release-unique immutable tag (T3-07). What travels as secrets is a per-host **capsule** derived per source, with local `generate`/`ask` values materialized on the sender first (§4.1.8).

**Enrollment — the step before all of this (rev 3.4, operator direction 2026-09-03; `CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2, CIU-93, V8-S7.2.4).** Both lines had specified transport, push and activation for an already-trusted host and left the first inventory row manual. `ciu host enroll <name>` on the control host generates an ed25519 key into the state root (`ciu-ssh/`) and prints one command for the target's admin: ciu's own `get.py`, pinned to the control's ciu version, with `enroll --authorized-key '<public key>' --controller <FQDN>`. The installer installs ciu (v8's `prepare` needs it on every target), refuses without an SSH server, creates the deploy user, appends the key once, and prints the host-key fingerprint. `ciu host enroll <name> --ssh-host ADDR --fingerprint …` then keyscans, refuses on mismatch, proves the login with `ciu version`, and writes the pinned row with a round-trip writer. No token, no callback, no listener, no cmru download backend: the operator's refinement of the filed design, which had carried a token-authenticated bootstrap URL and a callback to deliver two facts the operator had to confirm anyway. The same verb is backported to v7 (`SPEC.md` S14.7).

### 4.1.6 Init graph, waves, health gate

**Edges** (all data, no phases): every binding with `wait ≠ none` → the target's variant service, the endpoint's owner, and the providers of its `facts`; `depends_on` siblings (rendered into compose with the derived condition); **derived** edges — every vault-sourced or vault-stored secret → the Vault realization; every vault path → its minter (a `from = "generate", store = "vault"` secret or a hook entry's `provides`; no minter is a static ERROR); every cross-host resolution over a network with `realized_by` → that transport on both ends; every resolution over a `tls ≠ none` network → the `pki` service. There is no `after` (a `requires` entry is the same edge, R-21) and no `uses` (a binding with `wait = "none"`).

**Contract conformance** is the completeness check for providers, now computed from consumption (R-19): the contract of `main_db` is the union of endpoints bound to it (`sql`) and the `facts` of those bindings; every declared variant — `live = "db_core"`, `seeded = "db_core_seeded"` — must provide all of it (endpoints exist; facts in `provides`, hook `provides`, or derived from vault-stored secrets), whether or not it is currently selected. Nobody types a contract, and a seeded stub that forgets an endpoint fails `ciu check` before anyone selects it. Facts a provider lists that nobody binds are INFO, not a warning.

**Primary and variant service, waves, gates, health** are unchanged from revision 2.1: a multi-service stack marks one `primary = true`; a variant may name which service carries the capability; Realizations deploy as units in topological waves (a Realization-level cycle is an ERROR naming the remedy); between waves ciu waits for every provider a later wave has an edge to (`gate_timeout` per service, derived from its healthcheck parameters); fact probes run at the consumer's wave inside the providing container; cross-host, reachability of the resolution is probed and the provider host's own gate is authoritative — under the supported `ciu activate apply` flow hosts run serially, which closes revision 2.1's gap 4c (R-24). **The per-Realization pipeline is now written out** (V8-S8.7, R-41): hostdirs → `pre_secrets` hooks → secrets and temp copies → `pre_compose` hooks (their `state` is visible to every later step of the same run) → config files → compose render and injection → `compose up` → `post_compose` hooks after the Realization's providers are healthy → wave gate and fact probes.

### 4.1.7 Realness

Levels `live`, `seeded` (prepared; `owned-seeded` renamed, R-29), `simulated`, `mock = {}`. Selection precedence is CLI > `[realness.pin]` > `[realness] default`; the per-layout record `[ciu.instance.realness.<layout>]` written at the first `ciu up` is a **constraint**, not a source (R-26): a selection that differs from it — by flag or by a changed pin — is refused until `ciu clean --vanilla`. Joins are a Realization kind that `ciu instance init --join-preset` writes into the plain-TOML instance file, with a round-trip writer (R-06), from a committed preset (§4.1.7a); instance labels are unique per git family, and a preset's reference names a label or `primary` (R-27); a joined vault's token is read from the reference's store under the reference's shared lock (R-28).

### 4.1.7a Joins and shared infrastructure — committed presets (rev 4.0, Q9)

A linked worktree owns its test environment (D-647 #2): it runs its own stack and its own lanes, and it borrows from another instance only what the project has declared it may borrow. That declaration is a **join preset** in the committed `ciu.toml`. A preset is the only way a worktree instance joins anything; `ciu instance add --join` is removed.

```toml
[ciu.instances]
default_join_preset = "live"          # optional; `ciu instance init --no-join` opts out

[ciu.instances.join_presets.live]
reference = "primary"                 # the git family's primary checkout, or an instance label
services  = ["vault", "tracing"]      # LogicalServices; each must be shareable (below)

[service.vault]
live  = "vault"
share = { tenant = "vault-prefix" }   # each joiner gets its own prefix in the reference's Vault

[service.tracing]
live  = "skywalking"
share = { tenant = "telemetry-namespace" }
```

Rules:
- **Shareability is decided per service and recorded on the LogicalService.** A preset may name a service only if `[service.<n>] share` declares how each joining instance gets an isolated namespace in it. The vocabulary is closed:
  - `vault-prefix`, `vault-mount`: a per-instance KV prefix or mount;
  - `pg-database`: its own database and role on the shared server;
  - `telemetry-namespace`: the joiner's telemetry is tagged with its instance id and queried by it.

  A service with no tenant mechanism cannot be shared, and `ciu check` stage 12 refuses a preset that names one. ciu derives each tenant name from the joiner's `instance_id`. The provider's hook creates the namespace when the joiner is initialized and removes it at the joiner's `ciu clean`.
- **Sharing must pay for itself.** `ciu check` prints each shared service's committed footprint per preset, so a reviewer sees what the join saves. A container of a few tens of MiB is not worth the tenant machinery; only the heavy services are. For dstdns that is likely SkyWalking. Postgres becomes shareable through `pg-database`, which withdraws D-647 #3's "vault only, never postgres" (D-651 Q9).
- **The preset is checked once, not typed at every create.** ciu derives the reference's compose projects and service identities itself (CIU-116). `ciu check` on the primary reports a preset whose services the reference does not provide.
- **Liveness and clean-up are unchanged from V8-S9.5**: `clean` of the reference refuses while a joiner is attached (R-39).

Shortcomings:
- The tenant vocabulary covers the four kinds dstdns needs today. A new kind is a spec change, not a consumer setting.
- dstdns owes a per-service shareability analysis (footprints, and the namespace each service supports; D-651 Q9 follow-up) before it writes its preset.

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

**Every checkout is an instance** (a project with no Realizations has none — §4.1.10). `ciu instance init [--host] [--layout] [--bundles] [--label] [--join-preset P | --no-join]` derives the identity (§4.1.4), allocates or adopts the git family's shared record through `libraries/worktree`, writes the generated file, creates `ciu.instance.toml` when absent, and applies the join preset. `ciu instance list/show/remove/reap/lease` are the former worktree verbs (`lease` restored from v7 S16.9, R-44). Two verbs run commands in containers, both after the **mount proof** (the container mounts *this* checkout at its workdir — v7 S16.7's guard, R-47): `ciu gate exec [--env E]` in a gate environment (RG-70), and `ciu exec <realization>[:<svc>]` in any running service of this instance, the primary included (CIU-118). `ciu resolve --json` says which container, network and image a service is. There is no instance count: admission decides whether another stack may start (§4.1.10a). `[ciu.instances] lease_ttl_hours` bounds a worktree lease; the lease holder is `ciu@<host.hostname>:<instance_id>` from the host facts (R-45).

**Locks (rev 4.0, memo §3.5).** v8 uses the locks ciu 7.15 already ships and adds none beside them:
- The **family lock** of `libraries/worktree` serializes allocation of records and Git worktrees in the git common dir. cmru shares it. The library's rule that an adapter "must not implement a second generic Git lifecycle or lease authority" is why v8 has no `ciu-instances.lock`.
- The **instance lock** is 7.15's root lock: a `flock` on `<git-common-dir>/.workspace-instances/ciu-root-offset-<key>.lock`, keyed by the ciu root's offset in the checkout (`ciu/src/ciu/workspace.py` `root_lock`). Where no git family exists — a release on a target — it is a `flock` on the instance's state root directory, which outlives every release switch (this answers round-4 T4-01). Neither file is in the checkout, so `git clean -x` cannot fork the mutex. Mutating verbs take the instance lock exclusively. The gate takes it shared for the duration of its lanes, so `up` cannot recreate a container under an `exec` lane while several gates coexist under admission. Read-only verbs take it shared while reading `ciu.resolved.toml`, which is written by temp-file and atomic rename and is therefore always complete or absent.
- The **Realization lock** is a `flock` on the stack directory (unchanged).
- The **host admission lock** serializes only the decide-and-start step across every ciu on the host (§4.1.10a); whether it is a lock file, an ordering of reservations on the Docker daemon, or the profiler daemon is open decision Q15.

Lock order: every instance lock the verb needs — its own exclusively, joined references shared — in ascending instance-id order over a set resolved before mutation; then the family lock (allocation only); then stack-directory locks in ascending realization name; then, last and briefly, the host admission lock. The join graph must be acyclic. The store is written under the instance lock; there is no separate secrets lock. The kernel releases a dead holder's `flock`; there is no lock-breaking verb. A filesystem that cannot `flock` is refused by name rather than run unlocked.

**Lifecycle.** `ciu instance init` → `ciu check` (automatic before every mutating verb) → `ciu up` (admitted, §4.1.10a) → `ciu gate …` → `ciu down` / `ciu clean [--vanilla]`. `clean` disconnects ciu's own container from the instance network before removing it, and refuses while a joiner is attached (R-39). `ciu clean --identity <old>` removes the resources labelled with a retired id and nothing else (CIU-119 (3)). An instance whose `ciu.instance.toml` sets `protected = true` refuses `down` and `clean` without an explicit second flag, even for its own operator (CIU-105; shipped in 7.13.0 as `[deploy] protected`, vbpub@98957a129).

**State posture (operator decision §4.3.13; owner token withdrawn in rev 4.0, Q10).** Authoritative instance state stays in visible, gitignored checkout files, as in v7. Deleting them (`git clean -x`) destroys the store, the instance file and the instance record; `ciu instance backup|restore` exists for that (V8-S14.8). Allocation lives in the git family's shared records, which `git clean -x` does not touch. What the posture costs is stated, not hidden:
- A moved checkout has a new path, and therefore a new id. A move is **cold**: `instance init --move` is refused while any resource carries the old id (`ciu down && ciu clean` first, or `ciu clean --identity <old>` after the fact; named volumes cannot follow), and otherwise derives the new id and keeps the state.
- A copied tree at a new path derives its own id and sees none of the origin's resources. Its copied generated file is regenerated in place with a WARN naming old and new id; `--fresh` also discards the copied store.
- Two checkouts whose paths hash to one id are refused at allocation (§4.1.4). Every resource carries a `ciu.checkout` label, and a mutating verb verifies it before deleting.

Revisions 3.2–3.3 added a 128-bit owner token for the collision case. It is gone: the operator judged it too convoluted for the narrow threat it covered (SPEC-V8 Appendix D.5), and it never covered the case that actually happens — the rightful owner deleting their own instance by mistake. The `protected` flag covers that case.

**Canonical lock keys and `ciu lease` (rev 3.2, operator design answer A, 2026-09-03).** The two lock objects — the instance lock (the 7.15 root-lock file, above) and a Realization's stack directory (exclusive use of its containers) — are declared the **only** keys anyone uses: a third party takes the same `flock` on the same file or directory with util-linux and is correctly serialized against ciu, in v7 and v8 alike, instead of re-deriving a lock name from ciu's identity outputs (nyxloom's `Stack: exclusive`, dstdns's caller-side `flock`). `ciu lease acquire --exclusive|--shared [--realization r] [--purpose …] -- <cmd>` wraps them; `ciu lease status|wait` read and wait. Rev 3.3 (T3-04) made the key true rather than merely canonical: a **lock matrix** (V8-S14.4.9) says that every verb which mutates a Realization's containers or artifacts — `up`, `down`, `clean`, `dev`, `render`, `build` — takes that Realization's stack lock after the instance and registry locks, so an external holder of the directory is never overrun; a realization-only lease takes no instance lock at all (its own verb class); holders are recorded as lock-free atomic files under `ciu-leases/`, not in a shared record two leases could clobber; and the leased command inherits the held descriptors (`CIU_LEASE_FDS`) so a CIU verb run inside the lease locks the same open file description instead of deadlocking with its parent. A lane in an `exec` environment holds its target's stack-directory lock for its duration — one lane per container, run-gate's RG-39 lifted (V8-S16.5.7). A name-keyed lease was rejected: a lease must survive renames and exist before a render, and a directory git tracks does both. Host-wide admission serialization (§4.1.10a, Q15) is separate from both keys and is held only for a decide-and-start.

### 4.1.10 The testing gate — `ciu gate` (run-gate, ported; one implementation)

**Posture (rev 4.0; D-647 #1, D-651 Q1/Q2/Q8, D-652 Q3/Q13).** There is one gate implementation:
- **What it is.** `ciu gate` *is* today's run-gate — rev 46 (23.9.1) plus the five items dstdns needs (RG-67, RG-73, RG-74, RG-75, RG-76) — re-expressed in the v8 entity model. It is ported into `ciu8/src/ciu8/gate/` by copy-and-adapt, the ciu8 rule for reusable code. The port is not a rewrite from V8-S16's text: S16 is a 2026-09-02 subset of run-gate, and run-gate's current behaviour contradicts several of its rules (§4.3.17).
- **Build order.** The five items are built in run-gate v7 first, with their filed oracles. Those oracles plus V8-24's black-box scenarios become the port's parity tests. run-gate goes bug-fix-only once the ciu8 gate passes them, and is archived at 8.0.0. That build window is the only time two gate implementations exist, and it is not a supported dual path.
- **Standalone** now means the ciu wheel in **zero-instance mode**. A project whose `ciu.toml` holds only `[project]` and `[testing]` runs `ciu gate` with no instance, no lock and no rendered file; it needs docker only when a lane's environment is a container, and a judge only when a lane is an assay lane. Preflights are per need (R-02). run-gate-project is the main such consumer. The copied or symlinked `run-gate.py` distribution ends.
- **Hermetic lanes need no instance (Q8).** In a project with Realizations, an `ephemeral` or host-mode lane needs no `ciu instance init`. The gate derives the identity and the resolved image references read-only, writes no instance file and starts no stack. `exec` lanes and lanes with `requires` still need a rendered, running instance.
- **cmru** keeps its release transaction and calls `ciu gate <lane>`. Its tester container becomes the inherited ephemeral `tester-unified` environment (Q3).

```toml
[project]
trunk = "main"                                  # RG-74: a post-merge gate at a merge commit on the trunk compares against HEAD^1

[testing]
cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"    # R-10: declared slice > this variable > $CGROUP_PARENT_DEV_GATES; never a literal
history = 20                                    # LaneResults kept per lane

[testing.judge]                                 # Q12: exactly one of {command + sha256} or {source}, plus the floor
version = ">=7.2"
command = ["python3", "tools/assay/assay-7.2.0.pyz"]
sha256  = "<64-hex digest of the zipapp>"
import  = { environment = "hermetic", lanes = "all" }   # RG-76: one lane per `assay lanes --json` entry; [testing.lanes.<n>] overrides by name

[testing.environments.hermetic]                 # worktree-owned, no stack: the home of every lane that needs none
mode = "ephemeral"
image_from = "tester"                           # the tester's image, instance-scoped in a linked worktree (CIU-117)
env = { PYTHONPATH = "{worktree}:{worktree}/scripts" }   # the image bakes no checkout path (Q7 → A)

[testing.environments.hermetic.services.pg]     # RG-75: a throwaway database per lane run
image_from = "main_db"                          # the DECLARED engine image; no running stack is read
tmpfs = ["/home/postgres/pgdata"]
ready = ["pg_isready", "-U", "postgres"]
expose_env = { SCHEMA_GATE_DSN = "postgresql://postgres:{secret}@{host}:{port}/schemagate" }

[testing.environments.live]                     # exec into this instance's running tester: live lanes only
mode = "exec"
exec_in = "tester"
forward_env = ["RUN_LIVE_TESTS"]

[testing.lanes.unit]
kind = "command"
environment = "hermetic"
argv = ["pytest", "-q", "tests/unit"]
budget = "10m"                                  # a ceiling; the clock starts after admission and lock wait (RG-63)
resources = { memory_max = "2G", memory_swap_max = "0", cpu_weight = 100 }

[testing.lanes.schema]
kind = "command"
environment = "hermetic"
services = ["pg"]
argv = ["{worktree}/scripts/schema-apply.sh", "{worktree}"]
resources = { memory_max = "4G" }

[testing.lanes.release]
kind = "command"
environment = "live"
argv = ["pytest", "-q", "-m", "integration or e2e"]
required_env = ["RUN_LIVE_TESTS"]
requires = { realness = { main_db = "live" }, healthy = ["controller", "webapp_server"] }
require_provenance = true                       # running images must match HEAD, else NOT_RUN/provenance-mismatch
resources = { memory_max = "4G", shared = ["main_db"] }

[testing.lanes.gate]
kind = "sequence"                               # one process, one LaneResult per member; the base is resolved once (RG-74)
lanes = ["schema", "unit", "assay-dlq", "ui-unit"]   # assay-dlq and ui-unit are imported assay lanes
stop_on = "FAIL"
```

**Behaviour, as ported.** Each item names the run-gate rule it carries; the module split is the memo's §3.2.
- **Environments.**
  - `ephemeral` lanes run run-gate's detached state machine: create, start, follow logs, `docker wait`, collect, remove. They never use `--rm`, so a failed container's evidence survives, and status comes only from `docker wait` (R-15/R-17/R-26). Git runs with a private writable config and `safe.directory` (R-19a). Container lanes are placed in the gate tier (`$CGROUP_PARENT_DEV_GATES`, R-10, with R-11's LoadState check), never in the stack tier.
  - `exec` lanes run inside a running RealizedService of this instance after the mount proof, under that Realization's stack-directory lock, one lane per target container (V8-S16.5.7; run-gate R-41). A project that wants concurrent hermetic lanes runs them in `ephemeral` environments instead.
  - A third mode runs the lane as a host subprocess.

  Which *names* these modes carry, and whether an undeclared environment name has a built-in meaning, is open decision **Q11** (§4.9). run-gate's built-in `host` is a container and its `bare-host` is the subprocess (R-42); draft.7's `host` is the subprocess.
- **Linked worktrees.** A linked worktree's `.git` is a file naming `<git-common-dir>/worktrees/<w>`, an absolute path inside the primary checkout. Every container environment therefore also mounts the git common dir, read-write, at the path that file names. The path is derived from the gitfile, never configured. Read-write is needed because assay's repository snapshot runs `git worktree add`. Without the mount, dstdns measured exit 128 and `ERROR/GIT_FAILED`, with git-backed tests skipping under exit 0 (`tools/test-runner/ciu.compose.yml.j2`). ciu injects the same mounts into a persistent `exec_in` service of a linked-worktree instance. **The image contract (Q7 → A, D-653).**
- *Mounts.* The worktree is mounted at its own path, in both namespaces when they differ (R-23), plus the git common dir as above. The primary checkout is mounted the same way.
- *Paths.* `workdir` defaults to the checkout's path, and `env` supplies anything path-dependent, such as `PYTHONPATH`, through `{worktree}`.
- *Path rule.* An image a gate environment uses may not bake a checkout path: no `WORKDIR`, no `PYTHONPATH`, and no other `ENV` value naming a path inside any checkout of the git family.
- *Content rule.* It may not bake checkout content the lanes run either, such as a `COPY` of sources the tests import, or an editable install of a copied tree.
- *Validation.* `ciu check` stage 12 validates the contract. The path rule is proved from the image's own config (`WorkingDir`, `Env`, read through `docker image inspect` when the image is present, otherwise before the first lane runs). The content rule is a lint over the declared `build` Dockerfile (`COPY`/`ADD` of imported paths, editable installs); it reports and does not prove.

dstdns consequences:
- its test-runner Dockerfile drops the baked `WORKDIR`/`PYTHONPATH`;
- its editable `ddcli` install points at `/tmp/ddcli-build`, which the persistent runner's `/tmp` bind-mount hides at runtime, so that install is dead code there, and would be live but stale in an ephemeral container that mounts no `/tmp` (carve TEST-RUNNER-DDCLI-INSTALL, D-653).
- **Lane services (RG-75).** `[testing.environments.<e>.services.<s>]` starts a throwaway service per lane run, with run-scoped names and labels. The gate waits for its `ready` command, delivers its address and generated credential through `expose_env`, and removes it in the same `finally` as the lane container; re-attach reaps it. `image_from` reads a LogicalService's declared image from the resolved configuration, so no stack needs to run and no docker socket enters the lane.
- **Comparison base.** The order is: `--base`; else the fork commit in the worktree's shared record, used only while it is still an ancestor of `HEAD` (CIU-106, R-35a); else, at a merge commit on the `[project] trunk` branch, `HEAD^1` (RG-74(a)); else `merge-base HEAD @{upstream}` (R-35); else refuse. Every base passes R-35b's gate-safe charset. A sequence resolves the base once and passes it to every member whose assay lane reports `base_source = "request"` (RG-74(b), R-35).
- **Assay (§4.3.8).** `assay lanes --json` is the only interface to assay (V8-S16.7.1). Every assay invocation carries `--require-judge-provenance --resume --state-dir <git-family state root>/ciu-gate-state/<project-relative path>/ --progress <run_dir>/progress.jsonl`. The state directory survives a thrown-away worktree (R-38, RG-38/RG-49). `--request-base` goes exactly to `base_source = "request"` lanes. `--reuse-from`/`--rejudge` pass through (RG-66), and a failed assay lane leaves a failure digest (RG-72).
- **Re-attach (R-39).** A lane's container outlives a dead client (SIGKILL, a devcontainer restart). The inflight record is folded into `run.json`, which carries the owner tuple (pid, host, boot id, start ticks) and a completion marker (round-4 T4-09). The next invocation re-attaches to a live or exited container and collects it rather than starting a duplicate; `--fresh` overrides.
- **Liveness and budgets (R-40, SPEC-V8 D.7).** A budget is a ceiling, not a detector. Liveness comes from the profiler daemon's `watch` (§4.1.10a). Without a daemon it comes from the in-process progress- and log-stream watch, bounded by the lane's `stall_timeout` (R-40c/R-40f). The budget clock starts after admission and lock wait (RG-63).
- **Preconditions** → `NOT_RUN` with a closed reason vocabulary: `realness-mismatch`, `service-down`, `environment-down`, `environment-mismatch`, `env-missing`, `external-missing`, `external-down`, `dirty-tree`, `no-headroom`, `judge-floor`, `judge-provenance`, `provenance-mismatch`. A lane's required variables are compared with the environment's *available* set: `forward_env` ∪ binding variables ∪ lane-service `expose_env` ∪ the container env for exec.
- **Evidence.** Every invocation writes under its own `runs/<run_id>/` (128-bit random id, exclusive creation, a `run.json` manifest). Every LaneResult (`ciu.gate.<lane>.json`, `api = "ciu/lane-result"`) records outcome, timing, requested/applied/measured resources, admission, liveness, judge provenance, and the provenance of every required service (R-55). History and the per-lane footprint (§4.1.10a) are kept per lane. Evidence is 0600 on failure (R-26).
- **Configuration** is resolved from the selected checkout (`--worktree`), never from the CWD (run-gate RG-47/RG-65). `[ciu] inherit` (V8-S3.1.5) shares environments, the judge and the slice policy across a monorepo's projects; lanes never inherit. `[testing.externals]` gives a zero-instance library a typed external test database.
- **CLI** (memo §3.3):
  - `ciu gate [lanes…] [--worktree P] [--base REF] [--allow-dirty] [--dry-run] [--fresh] [--admission-wait D] [--override-admission] [--json] [-- extra-args]`; extra args reach only a lane that declares `extra_args = true` (RG-71);
  - `ciu gate --list` keeps run-gate R-01's three tab-separated columns, frozen because consumers parse them; `--list --json` adds last outcome, median duration and median peak;
  - `ciu gate history|footprint|doctor|exec`; `ciu check --gates` replaces `validate-pointers`;
  - run-gate's `RUN_GATE_*` knobs become declared keys, and test-only roots become `CIU_GATE_*_ROOT`;
  - the exit table is open decision **Q11**.

Shortcomings: the profiler daemon finds a lane's processes by the literal environment entry `RUN_GATE_PROFILE_SESSION=<token>` (cgroup-profiler `lib/subtree.py`). `ciu gate` therefore exports that run-gate-named variable until the contract renames it (§4.4 V8-31).

### 4.1.10a Resources: measurement, the committed manifest, admission (rev 4.0; charge refined in rev 4.1)

**Roles (D-651 Q4, D-652 Q6).** Three parties take part, and only ciu decides.
- **The cgroup-profiler daemon measures and serves data. It decides nothing.**
  - *What it is.* A host singleton (`cgprofile-host-daemon`) in its own `cgprofile.slice`. Its PID and cgroup namespaces are private; host `/proc` is mounted read-only at `/hostproc`, and host cgroup v2 is mounted for observation and guarded placement. It has no network and no docker socket (RG-55 contract §5; design D-29..D-32).
  - *How it is reached.* It speaks contract 1 over two carriers: `docker exec cgprofile-host-daemon cgprofile ctl <verb> --json`, and the socket `/run/cgprofile/ctl.sock` (v1.1 §8.1).
  - *What it serves.* Per-session live usage and summaries (`start`/`status`/`stop`), including the DAMON hot/warm/cold/idle classification of the lane's memory (contract §3 `damon`; the series are on disk as `damon.jsonl` and charted by `ctl report`, CP-6). A host snapshot with memory PSI, the `memory.max` of every `dev.slice` child, and the gates slice (`ctl host`, §2.4/§8.5). Liveness verdicts (`watch`, §8.2/§8.4). On request, per-lane placement (§8.3).
  - *Why ciu needs it.* It is the only vantage that sees the slices and the only source of warm working sets. From the devcontainer, where every gate starts, `/proc/self/cgroup` reads `0::/` and `/sys/fs/cgroup/dev.slice` is absent (live probe 2026-10-03).
  - *Running it is the operator's choice* (D-653 Q16). ciu works without it; see "Without the daemon" below.
- **The project commits the resource manifest** `ciu.footprint.json`, with per-lane and per-container entries.
  - *Scheduling field.* Each entry carries **`warm_set_bytes`**: the p90 over retained runs of the run's warm working set, where a run's warm set is the p90 of its per-sample `hot + warm` bytes. It also carries the DAMON thresholds the figure was classified under and `source = "damon"`.
  - *Informational fields.* Beside it are run-gate R-44's fields (`runs`, `scope`, `method`, `duration_s`, `memory_peak_bytes`, `memory_peak_over_baseline_bytes`, `cpu_cores`, `memory_full_stall_s`, each `{median, max}`). They are informational and are not what admission charges.
  - *Which runs count.* PASS runs and completed FAIL runs count (RG-68), and an absent entry means unknown.
  - *Writing it.* `ciu gate footprint --write [lane…]` writes lane entries, and `ciu footprint --write [realization…]` writes container entries, each for the named entries only (RG-69). Container entries come from bounded `container`-scope daemon sessions run while the stack does representative work.
- **Host capacity is ciu configuration** (D-652 Q6, D-653 Q15). ciu config carries what it needs to know about the host's resources and what share of them ciu may use:
  - per tier — gate lanes, stacks — the slice and the usable memory;
  - the memory-PSI threshold above which nothing new starts;
  - the no-daemon policy.

  Physical facts that can be read are read, never typed: RAM from the daemon's `ctl host` or `/proc/meminfo`, and a slice's `memory.max` from the daemon. A declared usable share above a readable ceiling is refused. Swap usage is never a signal (SPEC-V8 D.6). There is no hard coupling to mdt in either direction. mdt configures the slices today, and its `$CGROUP_PARENT_DEV_*` variables are one way ciu may learn their names, never a requirement. Where these facts live, and how the decide-and-start step is serialized host-wide, is open decision **Q15** (§4.9).

**Charge: the warm working set (D-653 Q4).** Admission charges each lane or container the memory it keeps *active*, not the memory it has touched. Cold and idle pages can be swapped out without harm, and thrashing starts only when the warm sets together exceed RAM, so a peak or total that counts cold pages over-charges and under-packs the host. What a start costs is read, never invented:
- **A measured lane** costs its manifest `warm_set_bytes`. For an exec lane DAMON follows the lane's own pid subtree through its token, so the figure is the lane's own warm set; the container's baseline is charged to its stack. An exec lane is charged to the tier its memory lands in: the stack tier, or the gate tier when placed.
- **A stack** costs the sum of its containers' `warm_set_bytes` over the containers `ciu up` would start.
- **Measured without DAMON** (a run whose profile has `damon: null`): the measured peak, `memory_peak_bytes.max`, is charged. The warm set is a subset of the resident memory a peak counts, so the peak is a safe over-charge until a DAMON run replaces it. This is the author's reading of D-653, not a ruling.
- **Unmeasured:** the declared ceiling `memory_max` (D-651 Q5) — the lane's own, which is mandatory (T3-10), or the container's effective `[governance] memory_max`.
- **Ceiling.** `memory_max` stays the hard ceiling applied to the cgroup whatever the charge. A container whose charge is below its ceiling may still grow to the ceiling, pushing its cold pages to swap.
- **Neither charge nor ceiling.** When admission is on, `ciu check` stage 11 refuses a container that has neither a charge nor a ceiling.

**Decide and start (D-651 Q4).** Two concurrent ciu runs that each decide "it fits" and then start would both start (TOCTOU). The decide-and-start step is therefore serialized across every ciu that can start work on the host. What carries that serialization — a lock, an ordering of reservations, or the daemon — is Q15. Within it ciu:
1. reads the live state: the reservations already admitted, plus, when the daemon runs, `ctl status` and `ctl host`;
2. checks `Σ admitted charges + this charge ≤ tier usable share`, and, when PSI is readable, `memory PSI full avg10 ≤ threshold`;
3. if both hold, starts the lane or stack and records its reservation as data (owner: instance, lane or realization, run id, pid, boot id; tier; charge and its source);
4. otherwise waits (`--admission-wait D`, with a periodic notice naming the readings), and finally refuses: a gate lane as `NOT_RUN/no-headroom`, `ciu up` with exit 4 (contention).

The serialization is held only for that step, never for a lane's or a stack's run. `--override-admission` starts anyway; the override is disclosed on stdout and recorded in the LaneResult or the up receipt. A reservation ends when its lane or stack stops, or when its owner is gone.

**What the daemon gives back.**
- *Profiling.* Each lane runs with a profiling session: `ctl start --target containerid:<id> --scope container|container-shared --token … --meta …`, with `stop` in the same `finally` as the container removal (R-43c). DAMON is on by default (`--damon on`; `[testing.profile] damon`). Without a daemon, run-gate's basic in-lane sampling and rusage fallbacks apply (R-43d, R-43i); they measure peaks, never warm sets. The summary goes into the LaneResult as `resources_measured`, beside `resources_requested` and `resources_applied`.
- *Placement.* Exec and host lanes may be **placed** (`--place`). The daemon has systemd create a delegated transient scope under the gates slice, and moves the lane's process subtree into a profiler-owned leaf below it. That gives an exec lane real `memory.high`/`memory.max`/`cpu.weight` caps where Docker can cap only the whole container, so V8-S16.6.4's "requested only" caveat ends for placed lanes. Ephemeral lanes stay Docker-capped (v1.1 §8.9 (3)).
- *Liveness.* The daemon's `watch` verdicts (`stalled`, `hung`, `runaway`, `throttled`, `over_ceiling`) drive the lane's stall path. `kill` is requested only where the contract allows it (§8.4).
- *The memory floor.* The `memory.min` floor CIU-94 writes to a container's scope (7.12.0, vbpub@a4f5aa94) stays. It protects memory and admits nothing; this corrects V8-S13.2.1.

**Without the daemon — a normal state, governed by ciu config (D-653 Q16).** With no daemon there are no live readings, no slice PSI and no new warm measurements. Committed manifest figures stay valid charges. This host ran no daemon on 2026-10-03. ciu applies the policy its config declares:
- `no_daemon = "count"`: at most a declared number of concurrent lanes or stacks per tier, counted over the admitted reservations under the same serialization (Q15).
- `no_daemon = "unbudgeted"`: start without a budget check and disclose it once per run. The kernel's ceilings (`memory_max`, the slice's `memory.max`) remain the only bounds.

Either way ciu says once per run which policy applied and why. It never refuses by default for want of a daemon. `ciu doctor` reports the daemon's absence as information, not as a failure.

Shortcomings:
- **DAMON's reach (v1).** DAMON monitors per-pid virtual address spaces (vaddr targets: the lane's pid subtree, rediscovered every interval). That is v1's only mode; the physical-address mode with cgroup-scoped DAMOS filters is cgprofile CP-3, open. Consequences:
  - memory a lane holds outside any mapping it owns is invisible: unmapped page cache, and tmpfs files such as a lane service's tmpfs data directory;
  - a detached child that leaves the token's subtree is under-attributed;
  - CP-3's paddr mode would see cgroup pages, but could not separate two lanes sharing one exec container.

  Where the warm figure is known to miss such memory, declare a larger `memory_max` and leave that lane or container unmeasured.
- **DAMON unavailable.** DAMON can be unavailable even when the daemon runs: `damon: "unavailable:<reason>"`, for example `sysfs read-only` or `module absent` (contract §2.1). The charge then falls back to the measured peak, as above.
- **What "warm" means is a threshold.** The defaults are hot ≥ 50 % access rate, warm ≥ 5 %, cold after 30 s idle and idle after 120 s (`lib/damon.py`). The manifest records the thresholds, and an entry classified under other thresholds is reported stale.
- **The warm figure needs one more Summary key.** Today the Summary reports hot and warm separately, each as `{peak, p90, median}`. `hot.p90 + warm.p90` approximates the p90 of their sum, but does not bound it. A combined per-sample series (`damon.warm_set_bytes`) is part of §4.4 V8-31; until it lands, the manifest records the approximation with `source = "damon-class-sum"`.
- **A stack container's warm set depends on its workload** during the measuring window. An idle measurement under-charges a container that is busy later. `ciu footprint --write` refuses a window shorter than a declared minimum, and the PSI threshold remains the backstop.
- **Placement figures are cgroup charges since placement, not total RSS.** Pages charged before a process moved stay with its origin (design A3), so a placed cap is not a hard cap on what the lane already holds.
- **Trust.** The daemon is a trusted, privileged host service, not a security boundary against its own compromise (design D-32).
- **v1.1 consumer unmerged.** The v1.1 consumer side (socket carrier, `watch`, placement) is not on run-gate's `main` yet: branch `rg55-p5-post-p6-20261001` is unmerged (verified 2026-10-03). The producer side (cgprofile 1.1.0, package P6) is merged but unreleased. The port takes the consumer from run-gate once it merges (Q2's order).

### 4.1.11 `ciu check` in v8 (runs automatically before every mutating verb)

Fifteen stages with declared dependencies so that an early typo does not hide unrelated findings (R-71): 1 files · 2 parse (TOML + secret-free scan) · 3 schema · 4 references · 5 contracts (derived; every variant) · 6 graph · 7 topology (incl. publications, push order) · 8 identity (with the ambiguity message) · 9 secrets · 10 realness · 11 governance (including admission prerequisites: a charge for every container and lane, §4.1.10a) · 12 testing (`assay lanes --json` when a judge is reachable; the judge declaration and imported lanes; inherit; sequences; lane services; join presets and shareability) · 13 hooks `--validate` · 14 registry validator · 15 live. `--layout L` prints the publication table and the bundle table; `--json` uses the common header every machine artifact shares (`api`, `api_version`, then `operation`, `status`, `findings[]`, `resolved`; R-49, refined per artifact by T-33). A zero-instance project runs stages 1–3, 11, 12, 14.

### 4.1.12 CLI verbs (v8) — every v7 verb has a disposition (R-61)

`ciu init` (scaffold, kept from v7 S19; writes the explicit host and layout — no built-ins) · `ciu instance init [--join-preset P | --no-join] [--move | --fresh] | list | show | remove | reap | lease` · `ciu check [--gates] [--validate-hooks]` (hook validation executes consumer code and is opt-in, T-23) · `ciu render [--show-injected]` (the template-vs-artifact diff, R-37) · `ciu up [--admission-wait D] [--override-admission]` · `ciu dev --realization r` (v7 S5a's loop) · `ciu down` · `ciu clean [--vanilla] [--identity <old>]` · `ciu status [--live]` (absorbs `health`/`status`) · `ciu show bundles|layouts|services|realizations|effective` (absorbs `profiles`/`layouts`; `effective` is the merged configuration with inherited tables marked by source, T-32/§4.3.14) · `ciu resolve [--realization r] [--service s] [--live] --json` and `ciu exec <realization>[:<svc>] -- cmd` (any service of this instance, the primary included; CIU-118) · `ciu gate [lanes…] | history | footprint | doctor | exec` (§4.1.10) · `ciu footprint [--write] [realization…]` (container footprints, §4.1.10a) · `ciu secrets show|reset|host|rotate-bootstrap` (absorbs `host-secrets`) · `ciu env print` · `ciu build` (v7 `bake`; stamps `org.opencontainers.image.revision` with `-dirty`, R-38; a linked worktree's project-built tags are instance-scoped, CIU-117) · `ciu push [--images|--prune]`, `ciu activate plan|apply --release`, `ciu up --activation-manifest|--receipts|--allow-assumed` (T-16/T-25, T2-01/T2-05, T3-01/T3-02) · `ciu ssh <host>` · `ciu provenance` · `ciu diagnose` · `ciu governance ksm|iops-baseline` · `ciu migrate [--check] [--secrets] [--hostdirs] [--config] [--gate]` (absorbs `migration-check`; also the v7 hostdir relocation, R-36; `--gate` is the run-gate conversion of §4.1.13) · `ciu hook run` · `ciu schema --json` (absorbs `capabilities`; R-63) · `ciu query identity|binding|publications|capability|lanes --json` (narrow machine queries, T-35) · `ciu instance backup|restore` (T-11; contract V8-S14.8) · `ciu lease acquire|status|wait` (the canonical lock keys as a verb, V8-S14.4.8–S14.4.9) · `ciu host enroll <name>` (a bare host to a pinned inventory row in two steps, no token and no callback; CIU-93, rev 3.4) · `ciu init --image|--from-compose|--service|--test-argv` (T-06 audit) · `ciu skills install|list|check|uninstall [--harness claude|agents|all]` (the wheel's packaged skills, §4.1.15) · `ciu doctor` (environment report: daemon reachability on both carriers, DAMON availability, slice visibility, the host capacity declaration and the admission serialization of Q15, `skills check`; an absent daemon is information, not a failure) · `ciu version` (prints the minimum judge; carries the estate CLI-compatibility surfaces of vbpub@05f373a40). Exit codes keep v7's meanings (0 ok, 1 runtime, 2 config/usage refusal, 3 environment bootstrap) plus 4 contention (a lock, or an admission refusal) and 5 remote failure (R-48). The gate's own exit table is open decision Q11 (§4.9).

### 4.1.13 Migration shape (v7 → v8; `ciu migrate`, stated in V8-Appendix A)

1. **Instance:** nothing to convert. v8 derives the id 7.15.1 derives and reads the same shared `.workspace-instances/` records, so the cutover is identity-neutral. `ciu instance init` writes the v8 generated file in every checkout and repairs an outdated one in place (CIU-115). Cockpit aliases switch to `eval "$(ciu env print)"`.
2. **Declaration files** (`--config`): `.j2` declarations become plain TOML with the new names; `{% set %}` constants inlined, `{{ vault.paths.x }}` → `path = "x"`, control flow expanded once and flagged; `[deploy]` → `[project]`, `[bundles]`, `[layouts]`, `[realness]`; the retired keys of §4.5 J deleted.
3. **Stack files:** `[ciu_stack.<svc>]`; `init_requires`/`uses`/`after` → `requires`/`binds.<local>`; `init_provides`/`[hooks.provides]` → `provides`; directives → structured secrets (the mapping table is in V8-Appendix A); `delivery` on every secret; consumer scalars into sub-tables; `primary`; `instances`.
4. **Templates:** delete injected stanzas; `routes.X.e.*` reads → `ciu_stack.<svc>.binds.<local>.*` (or nothing, with `env` delivery); identity/host reads → `identity.*`, `instance.*`, `host.*`; config-file templates the same, `secret()` needs `delivery = "configfile"`.
5. **Hooks:** `run(config, ctx)` → scripts on `ciu.hookkit` (`context()`, `emit()`, `wait_healthy()`, `wait_tcp()`, `secret_file()`, `--validate`); `apply_to_config` → `emit(state=…)`.
6. **Secrets state** (`--secrets`) and **host directories** (`--hostdirs`): imported and relocated without re-minting or deleting.
7. **Gate** (`--gate`, D-652 Q13): per repo, atomically. One commit runs `ciu migrate --gate`, deletes `run-gate.toml`, the `run-gate.py` copy or symlink and `.run-gate/`, rewrites the nyxloom `[gates.*]` pointers to `ciu gate <lane> --worktree {worktree}`, and re-measures footprints. The conversion:
   - `run-gate.toml` → `[testing.*]`;
   - restated assay lanes and pins → one `[testing.judge]` with `import`;
   - run-gate's `host` → a declared ephemeral environment, and `bare-host` → the subprocess mode (the names per Q11);
   - `bash -c "run-gate a && run-gate b"` conjunctions → `kind = "sequence"`;
   - `run-gate.root.toml` → a zero-instance root `ciu.toml` inherited through `[ciu] inherit`;
   - `run-gate.footprint.json` → the lane section of `ciu.footprint.json`.

   Lane history restarts (it is evidence, not state). Anything the converter cannot express — argv that is shell text, scripts that start containers — is printed as a residue report. Each repo has one gate path at every commit (AGENTS §4.1); the estate converts repo by repo while ciu8 is a separate console script.
8. **dstdns specifics:** `app_identity.*` (~45 tables + 41 copies) deleted in favour of the merged `realization.*` view; the 17 stack files that read `routes` become bindings; 13 hooks rewritten on hookkit; `tests/test_deploy_phase_ordering.py` retires with the phases. On the gate side dstdns deletes `scripts/gate-slot.sh`, `scripts/gate-base.sh`, the provisioning halves of `schema-gate.sh` and `sql-mutation-gate.sh`, the five `pNNN-assay-schema.sh` scripts, and the `.git` block of the test-runner compose template (memo §5).

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
environment = "host"                 # Q11 decides whether "host" is built in, and what it means
argv = ["pytest", "-q"]
```
plus `ciu.hosts.toml` (`[hosts.localhost] local = true`, gitignored, also written by `init`) and `web/ciu.stack.toml` (`[ciu_stack.web] image = "nginx:1.27"` and an endpoint). `ciu init --stack web && ciu instance init && ciu up && ciu gate unit`. A **zero-instance** project (run-gate-project, cmru, nyxloom, the trivial vbpub adopters) is `[project]` + `[testing]` only, and `ciu gate` needs nothing else. What revision 2.x needed for the same result: nine tables, a label prefix, four health keys, a `contract`, and a hand-written gitignored hosts file in every fresh clone.

### 4.1.15 Tool skills ship in the wheel (rev 4.0; D-647 #6, D-652 Q14)

ciu's agent skills (`ciu-cli`, `ciu-stack`, and the gate skill that replaces `run-gate-cli`) are package data in the ciu wheel. The skills an agent reads therefore always match the installed version, and a consumer-only checkout needs neither a vbpub checkout nor symlinks.
- `ciu skills install [--harness claude|agents|all] [--dest DIR] [--dry-run]` copies them into `~/.claude/skills/` (Claude Code) and `~/.agents/skills/` (Codex, opencode). Neither harness reads the other's directory (verified 2026-10-03). The copy is idempotent, and each copy is stamped with tool, version and content hash. A skill whose stamp names another tool is refused, never overwritten.
- `ciu skills list` shows each skill as installed, stale, locally modified or absent.
- `ciu skills check` exits non-zero on drift and is part of `ciu doctor`.
- `ciu skills uninstall` removes only ciu-stamped copies.

The verb group is not ciu's own code: it is `libraries/cli-extended`'s shared registration (CLI-EXT-02, vbpub@ab52242e8), adopted by every vbpub tool. Wheels cannot run post-install hooks, so the explicit install step stays; mdt's devcontainer finalize runs it.

Shortcomings: the skills live in source trees today (`ciu/.claude/skills/ciu-cli/`, `run-gate-project/.claude/skills/run-gate-cli/`; vbpub@33c0b0c2, 7.14.0), and CLI-EXT-02 is open.

## 4.3a Where more than one design is genuinely valid

**A. Where machine-owned rendered artifacts live** — unchanged from revision 2.1: flat visible files next to the stack (adopted; `ciu.rendered/` is a visible nested directory, not a hidden one), or a per-instance state directory outside the repo for read-only checkouts.

**B. Lock target** — resolved again in revision 4.0 (memo §3.5): the git family's shared records and 7.15's root-lock file, which replace rev 3.0's checkout-directory descriptor (interview Q2). The reasoning is in §4.3.7.

**C. Binding-carried credentials** — a binding could also deliver the provider's published secret under the consumer's local name (`DATABASE_PASSWORD` next to `DATABASE_HOST`), unifying endpoint and credential delivery the way service-binding specifications do. Considered and **deferred**: secrets keep their own declaration in v8.0.0 because the minter-edge model already gives every secret a provider and a delivery, and folding credentials into bindings would create a second path to the same value. Recorded in §4.10 for a later revision.

**D. How a linked worktree is mounted in a hermetic container, and what the image may bake** — two designs were genuinely valid; decided in revision 4.1 (Q7 → A, D-653): the worktree at its own path plus the git common dir, and no baked checkout path (§4.1.10). The comparison it was decided on is in §4.3.17.

**E. Which CLI vocabulary and exit table the merged gate keeps** — v8's draft contract or run-gate's; open decision Q11 (§4.9).

## 4.4 What still needs to be built

Each item names the owning tool, the shape, and why it does not exist today. `SPEC-V8.md` is the acceptance reference for every row once draft.8 carries Appendix R; until then, Appendix R overrides draft.7 for the rules it names. **Implementation home (operator decision 2026-09-03): the new subproject `vbpub/ciu8`** — console script `ciu8`, its own `cmru.toml`, gate and backlog; v7 stays in `ciu/` in maintenance; reusable v7 modules are copied into `ciu8/` and adapted, never imported; at 8.0.0 the cutover renames `ciu8` → `ciu` and archives v7 as `ciu7`. The v7 module names in the owner column are the material to copy from; the destination is always `ciu8/src/ciu8/…`. Suggested checkpoints: **A** (V8-1, V8-27, V8-14, V8-2, V8-11, V8-26, V8-13, V8-5 — files, inheritance, identity, lock and lease, check: everything that needs no graph), **B** (V8-3, V8-4, V8-6, V8-7, V8-8, V8-32, V8-16 — the model, joins and presets), **C** (V8-9, V8-10, V8-17, V8-18 — secrets, hooks, migration), **D** (V8-12, V8-24, V8-30, V8-31, V8-34, V8-35 — the gate port, its parity tests, resources and admission, the exec and image surfaces), **E** (V8-23, V8-29, V8-25, V8-28, V8-33, V8-37, V8-19, V8-20, V8-21, V8-22). The first dstdns stack converted is the tracer bullet before the rest. V8-29's v7 backport (§4.11 N23) runs on the v7 track independently of these checkpoints. **Phase A runs before all of these, on the v7 track (Q2):** run-gate gains RG-67, RG-73, RG-74, RG-75 and RG-76 with their oracles (V8-36). That relieves dstdns now and gives checkpoint D executable parity tests.

| # | mechanism | owner | shape | why it doesn't exist yet |
|---|---|---|---|---|
| V8-1 | **Config schema v8 + `revision = 8` gate + `ciu schema --json`** (V8-S3, S3.8.4) | ciu `config_model.py` | closed-key validators for every table in §4.5 generated from one declarative table-spec; JSON Schema emitted from the same spec | today's validator covers phases/profiles/layouts, the S3.14 registry, the worktree table and secrets; no realization/network/binding/testing tables exist |
| V8-2 | **Instance identity and state root** (V8-S4.1, S14.2, S2.6): 7.15.1's derivation verbatim (base36, nested-root composition, `libraries/worktree`); `ciu instance init`; plain `ciu.instance.toml`; the generated file (identity + build facts, repaired in place when outdated — CIU-115); the host file `ciu.host.toml`; the state-root resolution (checkout vs `<bundle_dir>/state/`); cold `--move`/`--fresh`; `ciu env print`; `ciu.env` no longer read | ciu `workspace.py`, `workspace_env.py`, `paths.py`; `libraries/worktree` (used, not copied) | the derivation and the shared records shipped in 7.15.1 (vbpub@d1eb98770, vbpub@b9cad87c3); remaining: the split, the state root, the verb, repair-in-place, dropping `ciu.env` as an input | `ciu.env` remains the source (`paths.py`, `workspace_env.py`); an outdated record fail-closes teardown (CIU-115) |
| V8-3 | **Realization registry + logical services + variants + derived contract + minter resolution** (V8-S5, S8.3) | ciu new `registry.py` | `[service.*]`, `[realization.*]`; contract from bindings; conformance of every variant; minter edges | S3.14's registry is flat; nothing checks provider coverage |
| V8-4 | **Stack file `ciu.stack.toml`: re-rooting, closed key set, merged view `services.<svc>`, site/instance overrides of stack tables** (V8-S3.6, S6) | ciu `config_model.py`, `engine.py` | parse `[ciu_stack.<svc>]`, bind under `realization.<n>.services`, expose the S3.5 contexts | one arbitrary root table per stack, no re-rooting |
| V8-5 | **Identity derivation + resolved table + compose enforcement + fixed `ciu.*` labels** (V8-S4, S11.3–S11.4) | ciu new `identity.py`; `composefile.py` | one function with elision and the ambiguity check; `[resolved.identities]`; stage 8 | `deploy.container_name` has no stack component (CIU-66); labels under a consumer prefix |
| V8-6 | **Topology: hosts (`fqdn`), networks, endpoints, bundles (`includes`), layouts + binding resolution + derived publication + `per_host` + TLS secrets** (V8-S7, S10.5) | ciu new `topology.py`; `hosts.py`, `deploy_pkg/layouts.py` | entities of §4.1.5; `resolve()`; layout-derived `ports:` from bindings with data only; `[resolved.bindings]`; publication table | layouts carry no networks/reach; hosts have no addresses; routes are consumer-typed |
| V8-7 | **Init graph from bindings, derived edges, waves, gates, the S8.7 pipeline, provider-resolved probes** (V8-S8) | ciu `provisioning.py`, `deploy.py`, `deploy_pkg/phases.py` | replace `ordered_phases`; bind/depends/derived edges; `gate_timeout`; ordered pipeline with state visibility | phases are hand-declared; the pipeline order is implicit |
| V8-8 | **Realness selection, record as constraint, joins via the plain instance file** (V8-S9) | ciu `deploy.py`, `worktree.py` | precedence resolver; record writer/refuser; `joined` kind reading the reference under a shared lock; round-trip TOML writer for `instance add` | no realness concept; S16.1 join generates `ref_services` |
| V8-9 | **Secrets v8** (V8-S10): structured sources, `[vault.paths]` resolution, `delivery` incl. `hook`/`configfile`, one store, temp copies, derived TLS secrets, per-source push rule | ciu `secrets/materialize.py`, `composefile.py` | one store file under the instance lock; delivery axis; bundle content derivation | stores are `.ciu/secrets/<name>`; directive strings parsed by regex |
| V8-10 | **`.ciu/` removal**: `ciu.rendered/` directory mounts, gitignore list check, KSM cache relocation, `ciu.hosts.toml`, `ciu.instance.json`, evidence dir | ciu `config_constants.py`, `composefile.py`, `governance.py`, `hosts.py` | rename targets; parent-directory mounts (v7 S5.3a kept) | `MACHINE_DIR = '.ciu'` wired through S1.6/S1.7/S4.9/S4.17/S4.26/S5.2/KSM |
| V8-11 | **Lock authorities + atomic rendered file + verb classes** (V8-S14.3–S14.4): the `libraries/worktree` family lock for allocation; 7.15's root lock as the instance lock (the state-root directory where no git family exists); exclusive/shared/realization-only/lock-free classes; `--wait`; filesystem refusal; ordered acquisition over joined references, the stack locks of every Realization a verb touches (S14.4.3, the lock matrix S14.4.9) and the host admission lock last | ciu `workspace.py` (`root_lock`), `cli.py`, `config_model.py` | — | the root lock exists (7.15.1), but no verb-class matrix uses it |
| V8-12 | **`ciu gate` — run-gate ported** (V8-S16, Appendix R): `[testing.*]` schema; zero-instance mode; hermetic lanes without instance init (Q8); externals; environment bindings, `env`, lane services (RG-75); `exec_in` + mount proof + per-target exclusivity (S16.5.7, R-41); the linked-worktree common-dir mount (§4.1.10, Q7); `sequence` lanes with one base (RG-74); `assay lanes --json` and lane import (RG-76); judge acquisition (Q12); `--state-dir`/`--progress` (R-38, RG-38); re-attach (R-39); liveness (R-40); the profiling client (R-43); history and footprint (R-36, R-44, RG-68/69); provenance in LaneResults; random run ids and run manifests (S16.9.4); the CLI and exit table (Q11); `CIU_GATE_*_ROOT` | ciu8 `gate/` modules `model`, `resolve`, `mounts`, `run/{detached,exec,host}`, `services`, `judge`, `base`, `admission`, `profile`, `evidence`, `cli` (memo §3.2), copied from `run_gate.py` rev 46 | §4.1.10 | run-gate is one 8841-line file outside the entity model; S16 is its 2026-09-02 subset |
| V8-13 | **`ciu check` 15 stages with dependency-based execution, publication/bundle tables, one JSON envelope** (V8-S15, S18.4) | ciu `cli.py`, `warn_policy.py` | stage table of §4.1.11 | `ciu check` runs 13 stages serially and stops at the first ERROR |
| V8-14 | **Plain-TOML declarations + strict template rendering for artifacts only + `--show-injected`** (V8-S3.2, S11.7) | ciu `config_model.py` | drop the TOML render sites; `StrictUndefined` for compose/config-file templates (shipped as CIU-74's fix in 7.x); the injection diff | every layer is rendered with Jinja |
| V8-15 | **`ciu instance` verb family + worktree lease + unique labels + `protected` + `clean --identity` + backup/restore** (V8-S14.6–S14.8, S4.1, S18) | ciu `worktree.py`, `cli.py` | rename with alias; `instance lease` on the library's leases; label uniqueness; `protected` (CIU-105's shape); `ciu clean --identity <old>` by label (CIU-119 (3)); cold `--move` and `--fresh` (S4.1.2); `backup`/`restore` with manifest | CIU-50 open; `clean` cannot address a retired id (the D-645 fallout: 32 containers and 3 volumes removed with raw `docker rm`) |
| V8-16 | **Compose injection** (V8-S11): identity/network/alias/label/`CIU_*` env/binding variables/secret/port/depends_on/healthcheck-timing/config-dir/governance stanzas; template prohibitions; disabled-service pruning; replica blocks | ciu `composefile.py` | parse rendered YAML, inject, validate, re-serialize | templates hand-write everything |
| V8-17 | **Hooks v2 + `ciu.hookkit` + `ciu hook run` + hook templates rewritten** (V8-S12) | ciu `hooks_runner.py`, new `hookkit/` | subprocess JSON context v2 with resolved bindings; outputs; `--validate`; the helper package (stdlib only) | hooks are imported Python modules |
| V8-18 | **`ciu migrate` (config/secrets/hostdirs/gate) + `ciu init` v8 + `ciu doctor`** (V8-S19, App A) | ciu `cli.py`, new `migrate.py` | mechanical conversions with a report; explicit host/layout scaffold; environment report | `ciu init` writes v7 files; `migration-check` covers persist:secret only |
| V8-19 | **run-gate retirement**: bug-fix-only once the ciu8 gate passes the parity tests (V8-24); archived at 8.0.0; the assay consumer repoint (`derived:` optional, `required-env:` fed by environment bindings) | run-gate-project, dstdns `assay.toml` | §4.1.10 | run-gate is the estate's gate today (18 of 19 consumers on the symlinked script) |
| V8-20 | **dstdns migration** (35 stacks, 13 hooks, templates, gate config) — `v8-dstdns-demo/` is the target shape | dstdns | per §4.1.13 | consumer work |
| V8-21 | **SPEC v8 promotion** — `SPEC-V8.md` becomes `SPEC.md` at the v8.0.0 cut; CONFIG.md/CONSUMERS.md regenerated from the table-spec; run-gate's SPEC folded into S16 through the port map (one implementation, D-647 #1) | ciu docs | — | — |
| V8-22 | **Verb dispositions**: `status`, `show`, `dev`, `ssh`, `provenance`, `governance ksm\|iops-baseline`, exit codes | ciu `cli.py` | §4.1.12 | v7 verbs with no v8 home in revision 2.x |
| V8-23 | **Releases and receipts** (V8-S17.3–S17.5): manifest over the declared closure (S17.3.1), image transport by digest or archive (S17.3.6), staging/verification, `candidate`, the CIU-owned `current`/`previous` switch and rollback (S17.4.1), secrets capsule per source with push-time materialization, the activation manifest (`activate plan`), the prepare → apply → health → receipt → switch state machine in both directions (S17.4.1), the state root on targets (S2.6), receipts validated against the manifest's provider entry (S17.4.3–S17.4.4), strict remote-fact acceptance with `--allow-assumed`, the reference-level image map with immutable release tags (S17.6.1, S17.3.6) | ciu new `release.py`; `push`/`activate`/`up` | §4.1.5, §4.1.8 | v7 SPEC J rsyncs in place; no manifest, no receipt |
| V8-24 | **Port parity tests**: the black-box scenarios (detached execution, dual mount, git isolation, admission, sequences, progress/resume, re-attach) plus the oracles of RG-67/73/74/75/76, run against run-gate and the ciu8 gate until the port is accepted, then against ciu8 only | ciu8 tests, run-gate-project | — | parity is asserted by citation today |
| V8-25 | **Query surface and artifact headers** (V8-S3.7.6, S18.4): `ciu query …`, `resolved.capabilities`, `api`/`api_version` on every artifact with the compatibility policy | ciu `cli.py`, renderer | §4.1.12 | one `schema_version` for unrelated artifacts in revision 3.0 |
| V8-26 | **Canonical lock keys + `ciu lease`** (V8-S14.4.7–S14.4.8, S16.5.7, S14.7.1): `lease acquire --exclusive\|--shared [--realization r] -- cmd`, `lease status\|wait`, lock-free holder records under `ciu-leases/`; the lock matrix S14.4.9 (every mutator takes the stack locks it touches); `CIU_LEASE_FDS` descriptor inheritance; the stack-directory lock as the exec-target lock; documented so third parties take the same `flock` on the same two keys (the instance root-lock file and the stack directory) | ciu `cli.py`, the lock module of V8-11 | §4.1.9 | nyxloom and dstdns re-derive lock names from identity outputs; run-gate's exec lock (R-41) is name-keyed under `/tmp` |
| V8-27 | **`[ciu] inherit`** (V8-S3.1.5, S3.4.7, S16.3, S16.11.1, S17.3.1): the closed inheritable list (`governance`, `project.health`, `testing.environments`, `testing.judge`, `testing.cgroup_slice_env`, `testing.profile`), recursive with cycle refusal, paths relative to the declaring file and bounded by the containing worktree, flattening into `ciu.inherited.toml` at push, an inherited judge floor unused without assay lanes, `ciu show effective` with source marks, `[governance]` permitted in a zero-instance root | ciu `config_model.py` (with V8-1's table-spec) | §4.3.14, §4.3.15 | v7 roots are islands: nyxloom's and pwmcp's roots carry no `[governance]` while dstdns's does; the vbpub tester environment is declared in `run-gate.root.toml` only |
| V8-28 | **Executable fixtures**: the dstdns demo, `examples/minimal/` and `examples/monorepo/` (a zero-instance root with inherited governance, tester environment and judge declaration; an assay child with a persistent tester whose `build.context` is the sibling `tester-unified/`; a command-only child) rendered and checked by the real tool; `ciu check --graph` reproduces `examples/ciu.resolved.toml.example`; the S3.8.6 documentation-conformance test over this proposal's companion spec | ciu8 tests | §4.10 items 17, 25 | every demo is derived by hand today |
| V8-29 | **Host enrollment** (V8-S7.2.4, S18; `CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2): `ciu host enroll <name>` step 1 (key pair into `ciu-ssh/`, the version-pinned `get.py enroll` one-liner) and step 2 (`--ssh-host --fingerprint`: keyscan, fingerprint match, `ciu version` over the new key, the pinned row through a round-trip writer); `--replace`, `--abort`, `--global`; depends on cmru KI-24 (the `get.py enroll` template subcommand) and on ciu shipping its own `get.py` | ciu8 `cli.py`, `hosts.py`; cmru `templates/get.py.tmpl` | §4.1.5, proposal §3–§5 and oracles O1–O6 | the first row is hand-written today; ciu ships no `get.py` |
| V8-30 | **Resources and admission** (Appendix R: S14.6, S16.6, S13.2.1, the host config): host capacity in ciu config (Q15); the committed `ciu.footprint.json` with a warm working-set figure per lane and container; the charge rule (the warm set; else the measured peak; else `memory_max` — D-653, Q5); host-wide serialization of decide-and-start and reservations as data (Q15); `--admission-wait`/`--override-admission`; `ciu footprint`; the no-daemon policy (`count` or `unbudgeted`, Q16) | ciu8 `gate/admission.py`, `gate/profile.py`, `up` | §4.1.10a | v7 admits by an instance count (`max_concurrent_instances`) plus a `memory.min` floor read from cgroupfs, which the devcontainer cannot see (CIU-94/95); run-gate R-29 reads the same invisible slice; RG-56 is open |
| V8-31 | **cgroup-profiler contract amendment** (a v1.2 under contract 1, by controller ruling): `ctl reserve\|release` — data-only reservation records with owner, tier, charge and expiry by owner liveness, listed in `ctl status` — needed if Q15 chooses option C and optional otherwise; a combined per-sample `hot + warm` series in the Summary (`damon.warm_set_bytes`), so the manifest's warm figure is a true p90; optionally, a tool-neutral name for the profiling-token variable. Filed as cgprofile CP-15 (vbpub@9b4bf835e) for the reservation half | `scripts/cgroup-profiler`, `RG55-INTERFACE-CONTRACT.md` | §4.1.10a Shortcomings | contract 1 registers demand only through a live session's `meta`, at most 16 sessions |
| V8-32 | **Join presets and shareable services** (Appendix R: S9.5, S5.2): `[ciu.instances.join_presets.<p>]`, `default_join_preset`, `[service.<n>] share`; tenant namespaces derived from the joiner's id, created and removed by the provider's hook; stage-12 checks | ciu8 `registry.py`, the `instance` verbs, hookkit | §4.1.7a | v7 S16.1's four per-invocation flags, which dstdns never used (CIU-116) |
| V8-33 | **`ciu skills`** through `libraries/cli-extended` CLI-EXT-02; skills as wheel package data | cli-extended, ciu8 `pyproject.toml` | §4.1.15 | skills live in source trees |
| V8-34 | **`ciu resolve` / `ciu exec`** for any instance (CIU-118) and `ciu gate exec` (RG-70), sharing the mount-proof code | ciu8 `cli.py`, `gate/resolve.py` | §4.1.9 | `ciu worktree exec` covers only managed worktrees' declared targets; dstdns carries five container-name derivations |
| V8-35 | **Instance-scoped project-built tags** (CIU-117): a linked worktree's `build`-declared images are tagged `<declared>-<instance_id>`; `ciu build` refuses to overwrite a tag the primary names | ciu8 `build`, the image map (S17.6.1) | §4.1.10 (`image_from`) | a worktree's bake overwrites the primary's tag |
| V8-36 | **Phase A on the v7 track** (Q2): RG-67 (per-environment count), RG-73 (ephemeral environments as per-worktree runners), RG-74 (trunk-merge base, sequence base propagation), RG-75 (lane services), RG-76 (judge-lane import), each with its filed oracles; RG-77 (the `--state-dir` contract written into the SPEC and skill) | run-gate-project | the backlog entries | filed 2026-10-03, open |
| V8-37 | **cmru calls `ciu gate`** (Q3): the release transaction runs `ciu gate <release-lane>` in its isolated release worktree; `cmru tester-gate` steps become `ciu gate <lane>` on the inherited `tester-unified` environment | cmru | — | cmru spawns and caps its own tester container |

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
| `trunk` | `project` | branch name, optional | the branch a post-merge gate runs on; at a merge commit there, the comparison base is `HEAD^1` (RG-74(a)) | consumer / gate | `"main"` |
| `registry.url` / `registry.namespace` | `project.registry` | str / name | where project-built images live and how they are named | consumer / build, templates | `""` / `"dstdns"` |
| `health.interval` / `.timeout` / `.start_period` / `.retries` | `project.health` | duration / duration / duration / int — **policy defaults** 10s / 5s / 60s / 6 | defaults merged into every service's `health`; `timeout` is the probe timeout, not the gate budget | consumer / merge, injection | `start_period = "240s"` |
| `health.gate_timeout` | `project.health` | duration, optional | the inter-wave convergence budget when the derived default is wrong | consumer / gate | `"300s"` |
| `compose_env.<VAR>` | `project.compose_env` | str | consumer env passed to every compose process; identity facts never belong here | consumer / compose env | `DSTDNS_TELEMETRY = "on"` |
| `control.<flag>` | `project.control` | bool | named switches `enabled` may reference by name | consumer / filter | `enable_observability = true` |
| *(vendor image list)* | — | — | none (rev 3.1, T-29): a service with a `build` table is project-built, every other image is pulled — ownership sits next to the image | — | — |
| `standalone_root` / `inherit` / `require_fqdn` / `auto_connect_network` / `exit_on` / `user_tables` / `registry_validator` / `secret_lint_allow` | `ciu` | bool / path / bool / bool / `WARN\|ERROR\|NEVER` / list / path / list[table path] | intended nesting; **the one inheritance mechanism** (closed list: `governance`, `project.health`, `testing.environments`, `testing.judge`, `testing.cgroup_slice_env`, `testing.profile`; recursive, explicit path, no walk-up; the path may leave a child root but never the containing worktree, and a release carries the tables flattened with source digests — V8-S3.1.5, rev 3.2/3.3); refuse a host without `fqdn`; attach the devcontainer; severity policy; consumer tables; `[registry]` validator; lint suppressions per table path (T2-03) | consumer / engine, check | `inherit = "../ciu.toml"` |
| `lease_ttl_hours` / `default_join_preset` / `join_presets.<p>.reference` / `.services` | `ciu.instances` | number > 0 / preset name / `primary` or an instance label / list[shareable LogicalService] | the worktree lease bound; the committed join presets, the only way a linked worktree borrows shared infrastructure (§4.1.7a, Q9). There is no instance count: admission decides (§4.1.10a) | consumer / instance verbs, stage 12 | `join_presets.live = { reference = "primary", services = ["vault"] }` |
| *(labels prefix, env defaults)* | — | — | none: ownership labels are fixed `ciu.*` (R-15); template data lives in user tables (R-13) | — | — |

#### A2 `[service.<n>]` — LogicalServices (V8-S5.2, S5.3)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `service` | table key `name` | the vocabulary every binding, bundle, `exec_in`, `image_from`, `pki`, `vault.service`, lane `requires.healthy` uses | consumer / everything | `[service.main_db]` |
| `description` | `service.<n>` | str, optional | human context | consumer / humans | — |
| `live` / `seeded` / `simulated` | `service.<n>.<level>` | str → realization, or table `{ realized_by, service, unchecked }` | one variant per level; `service` says which service of a multi-service stack carries THIS capability (default: the primary); `unchecked = true` (seeded/simulated only) accepts the variant on health alone when its derived contract is empty — the explicit alternative to `verify` (T2-02) | consumer (instance file for joins) / resolver, graph, resolutions, gate, stage 4 | `seeded = { realized_by = "db_core_seeded", unchecked = true }` |
| `verify` | `service.<n>` | list[TypedFact], optional | facts probed in the selected variant's provider at the end of `up`, at every level — the acceptance check for a prepared or simulated variant nobody binds; required (or `unchecked`) when such a variant is selected with an empty contract (T-15, T2-02) | consumer / acceptance | `verify = ["pg:db/dstdns", "pg:role/controller"]` |
| `mock` | `service.<n>.mock` | `{}` | an in-process double is a legal selection; no Realization, no resolution, no edge | consumer / resolver | `mock = {}` |
| `share.tenant` | `service.<n>.share` | `vault-prefix\|vault-mount\|pg-database\|telemetry-namespace`, optional | declares the service shareable, and how each joining instance gets its own namespace in it; absent = not shareable (§4.1.7a) | consumer / stage 12, provider hook | `share = { tenant = "pg-database" }` |
| *(contract)* | — | — | **derived** from bindings (R-19): endpoints bound + `facts` declared by consumers; every variant is checked against it | ciu / stage 5 | — |

#### A3 `[realization.<n>]` (V8-S5.4)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<n>` | `realization` | table key `name`; `hosts`/`ciu` reserved | identity component 3 | consumer (instance file for `joined`) / identity, graph | `db_core = { … }` |
| `kind` | `realization.<n>` | `ciu_stack\|external\|joined` | selects the per-kind keys and deploy behaviour (P7) | consumer / ciu | `"ciu_stack"` |
| `location` | `ciu_stack` | dir under the checkout root, unique, never shared across projects | binds the stack file to its name once; rendered artifacts and the Realization's lock live there, so a shared directory would alias instances — a monorepo shares an image through `build.context`, not a stack directory (§4.3.14) | consumer / loader | `"infra/db-core"` |
| `per_host` | `ciu_stack` | bool | a daemon on every host whose bundles include it; never a binding target with data | consumer / placement, edges | `true` |
| `provides` | `external`, `joined` | list[TypedFact] | facts ciu cannot derive because it does not build the thing — assertions, recorded as such | consumer / conformance | `["pg:db/dstdns"]` |
| `probe` | `external` | list[`http:` TypedFact] | facts ciu checks against the external endpoint before its consumers' wave — the checked counterpart of `provides` (T2-03) | consumer / gate | `probe = ["http:/health"]` |
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
| `memory_max` / `memory_swap_max` / `memory_high` / `memory_low` / `memory_min` / `cpu_weight` / `cpu_max` / `io_weight` / `pids_max` | `governance` (and per-stack) | the **shared resource key set** `RK` | each is the cgroup-v2 file it writes (`memory_min` is written to the container's transient scope and is a protection, never an admission charge — CIU-94); the effective `memory_max` is a container's admission charge until it is measured (Q5); `cpu_max` maps to compose `cpus` (CIU-90's key) | consumer / governance, gate | `memory_max = "2G"` |
| `io_*_max` / `device` / `baseline_path` | `governance` | int / str / path | device-level I/O caps only stacks have | consumer / governance | — |

#### A7 `[testing.*]` — the gate (V8-S16)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| *(inherit)* | — | — | none here: `[ciu] inherit` (A1, V8-S3.1.5) is the one mechanism, and `testing.environments`, `testing.judge`, `testing.cgroup_slice_env` and `testing.profile` are on its closed list. A monorepo declares its tester environment, judge and slice policy once (run-gate R-22, T-32); lanes, externals and evidence never inherit | — | — |
| `externals.<n>.url` / `.env` / `.probe` | `testing.externals.<n>` | URL / table of variable names / `tcp\|http:<path>\|none` | a typed test dependency the project does not deploy; the only binding target a zero-instance project may use (T-31) | consumer / gate | `db = { env = { host = "TEST_DB_HOST", port = "TEST_DB_PORT" } }` |
| `cgroup_slice_env` / `cgroup_slice` | `testing` | env-var name / slice name, both optional | where container lanes are placed: a declared slice wins, else the slice named by this variable, else `$CGROUP_PARENT_DEV_GATES` (run-gate R-10). A literal slice in a committed file shadows a host fact (AGENTS §4.2a) | consumer / gate | `cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"` |
| `evidence_dir` / `history` | `testing` | path / int | where run directories and LaneResults land (gitignored); LaneResults kept per lane | consumer / gate | — |
| `judge.version` / `.command` + `.sha256` / `.source` / `.import` | `testing.judge` | version floor / argv + 64-hex digest / path / `{ environment, lanes = "all"\|[names] }` | the ONE judge declaration (Q12). Exactly one of a pinned artifact (`command` + `sha256`, for external consumers) or a source tree installed from the judged worktree (`source`, estate-internal). The floor must be ≥ ciu's own minimum drivable judge; it is required when an effective assay lane exists, and permitted and unused otherwise (T3-03). `import` creates a lane per `assay lanes --json` entry in that environment, and an explicit `[testing.lanes.<n>]` overrides by name (RG-76). Provenance is always required | consumer / gate | `import = { environment = "hermetic", lanes = "all" }` |
| `environments.<e>.mode` | `testing.environments.<e>` | `ephemeral\|exec\|host`; the names and any built-in environment are open decision Q11 | where a lane's process lives | consumer / gate | `"ephemeral"` |
| `environments.<e>.exec_in` | `exec` | LogicalService | the container to exec into, by capability; identity derived; health and **mount proof** required (R-47) | consumer / gate | `"tester"` |
| `environments.<e>.image` / `.image_from` | `ephemeral` | str / LogicalService | what to run; `image_from` reuses a service's image, instance-scoped in a linked worktree (CIU-117) | consumer / gate | `image_from = "tester"` |
| `environments.<e>.forward_env` / `.env` / `.extra_mounts` / `.workdir` / `.enabled` | `testing.environments.<e>` | list / table str→str with `{worktree}` / list / path / bool | explicit allow-list of forwarded env; variables the lane gets, with `{worktree}` substituted (the image bakes no checkout path, Q7); extra binds (dual-mount guard); where `{worktree}` lands (default: the checkout's own path) | consumer / gate | `env = { PYTHONPATH = "{worktree}:{worktree}/scripts" }` |
| `environments.<e>.binds.<local>` | `testing.environments.<e>.binds` | binding with `delivery = "env"` | infrastructure facts for the lane process as variables — assay reads `required-env:`, never ciu's file (R-03) | consumer / gate | `db = { to = "main_db.sql", delivery = "env", env_prefix = "TEST_DB" }` |
| `environments.<e>.services.<s>.image` / `.image_from` / `.tmpfs` / `.ready` / `.expose_env` / `.resources` | `testing.environments.<e>.services.<s>` | str / LogicalService / list / argv / table with `{host}`, `{port}`, `{secret}` / `RK` subset | a throwaway service per lane run, removed with the lane (RG-75); `{secret}` is generated per run | consumer / gate | `image_from = "main_db"` |
| `lanes.<l>.kind` / `.environment` / `.argv` / `.assay_lane` / `.lanes` / `.stop_on` / `.description` | `testing.lanes.<l>` | `command\|assay\|sequence` / env / list / assay lane / list[lane] / `FAIL\|never` / str | who produces the outcome; where; the command; the judge lane (invocation derived; `base_source` from `assay lanes --json`); sequence members (in-process, R-53) | consumer / gate, stage 12 | `kind = "sequence"` |
| `lanes.<l>.clean_tree` / `.budget` / `.required_env` / `.artifacts` / `.enabled` / `.services` / `.extra_args` / `.stall_timeout` | `testing.lanes.<l>` | bool / duration / list / list / bool / list[lane service] / bool / duration | evidence integrity; the wall-clock ceiling, clocked after admission and lock wait (RG-63); must-have env; outputs; which lane services this lane starts; whether `-- args` are appended (RG-71); the stall bound used when no daemon watches the lane (R-40c/f) | consumer / gate | `budget = "30m"` |
| `lanes.<l>.requires.realness` / `.healthy` | `testing.lanes.<l>.requires` | table logical → level / list[LogicalService] | preconditions from the record and the graph → `NOT_RUN/realness-mismatch` / `service-down` | consumer / gate | — |
| `lanes.<l>.require_provenance` | `testing.lanes.<l>` | bool | running images must match `HEAD` → `NOT_RUN/provenance-mismatch` (R-55); always recorded | consumer / gate | `true` |
| `lanes.<l>.resources.<RK>` / `.shared` | `testing.lanes.<l>.resources` | `RK` subset (`memory_max` mandatory) / list[LogicalService] | per-lane cgroup caps; `memory_max` is the admission charge until measured (Q5); exclusive use of realizations | consumer / gate | — |
| `profile.enabled` / `.daemon` / `.interval` / `.damon` / `.transport`; lane `profile` | `testing.profile`; `testing.lanes.<l>.profile` | bool / container name / duration / bool / `auto\|socket\|exec`; bool or `{ enabled, damon }` | profiling against the cgroup-profiler daemon (run-gate R-43's `[profile]`; transport per contract v1.1 §8.1); a profile never changes a verdict | consumer / gate | `transport = "auto"` |
| `footprint.tolerance_pct` / `.max_age_days` | `testing.footprint` | int / int | how far a measured run may disagree with the committed manifest before it is reported; how old an entry may be before it is reported stale (run-gate `[footprint]`) | consumer / gate | `tolerance_pct = 25` |
| *(request_base, central lanes, container_name, pins, assay_command, a stack or gate count)* | — | — | none: derived from `assay lanes --json`; environments inherit, lanes never; `exec_in`; the judge declaration; the invocation is ciu's; admission replaces counts | — | — |

### B. Instance file (`ciu.instance.toml`, operator), generated file (`ciu.instance.generated.toml`, ciu) and host file (`ciu.host.toml`, ciu, state root) — all gitignored, per instance (V8-S14.2, S2.6)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `layout` / `bundles` / `label` | `ciu.instance` | layout / list[bundle] / str (unique per git family) | which placement this instance deploys; default bundle selection; a human name (never an identity; the join reference, R-27) | operator or `ciu instance init/add` / `ciu up`, listings, registry | `layout = "local"` |
| `protected` | `ciu.instance` | bool, default false | the operator marks an instance important: `down` and `clean` refuse without a second explicit flag, even in the owner's own checkout (CIU-105) | operator / `down`, `clean` | `protected = true` |
| `host_ports."<realization>.<svc>.<endpoint>"` | `ciu.instance.host_ports` | int | per-instance host-port override so two instances on one machine can both publish | operator / publication | `"cadvisor.cadvisor.http" = 18080` |
| `[realization.<n>] kind = "joined" …`, `[service.<n>] <level> = "<n>"`, `join_preset` | instance file | see A3, A2; preset name | joins are instance-scoped Realizations, written by `ciu instance init --join-preset` from a committed preset with a round-trip writer (R-06, §4.1.7a); `join_preset` records which | ciu / join | see §4.1.7a |
| `[realization.<R>.services.<svc>.…]` | instance file | as E | per-instance override of a stack table by its merged path | operator / merge | — |
| `generated.instance_id` | `ciu.instance.generated` | 6 lower-case base-36 chars (a monorepo child: `<workspace_id>-<root_instance_id>`) | identity component 2, identical on every host of a layout; derived from the path, so an outdated or mismatching file is regenerated in place (CIU-115) | ciu / everything | `"hox0ju"` |
| `name` / `hostname` / `env_type` / `user_uid` / `user_gid` / `docker_gid` | `ciu.host` in `ciu.host.toml` (state root) | host / str / `devcontainer\|native\|github-actions` / int ×3 | host-local facts templates and hooks read as `host.*`; `hostname` is the lease holder (R-45); written per host into its state root, never part of an identity, never in a release (T3-02); the checkout's roots are derived, not stored | ciu / templates, hooks, engine | `env_type = "devcontainer"` |
| `build.build_version` / `.build_time` / `.images.<reference>` | `ciu.instance.build` | str / datetime / `{ id, digest }` | what `ciu build` produced, per image reference (the image map, S17.6.1) | `ciu build` / templates, provenance, push | — |
| `realness.<layout>.<logical>` | `ciu.instance.json` (state root) | level | the durable record per layout — a **constraint** on later selections (R-26); per state root, never travels (T3-02) | ciu at first `up` / resolver, gate | `main_db = "seeded"` |
| *(public_fqdn)* | — | — | none: `fqdn` is declared per host (R-16) and read as `host.fqdn` | — | — |

### C. Rendered, derived, read-only (`ciu.resolved.toml` `[resolved]`) — a consumer writing any of these is refused (V8-S3.7)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `api` / `api_version` / `layout` / `host` / `environment` / `rendered_at` | `resolved` | `"ciu/resolved"` / int / str / str / str / datetime | names the artifact's schema and its own version (each artifact versions independently, T-33); what this render resolved for | ciu / assay, scripts, gate | `api_version = 1` |
| `capabilities.<logical>.level` / `.realization` / `.service` / `.container_name` / `.endpoints` | `resolved.capabilities` | … | the index for readers that start from a capability (`ciu query capability`, T-35) | ciu / scripts | — |
| `identities.<r>.<svc>.container_name` / `.hostname` / `.compose_key` / `.compose_project` / `.network` / `.replicas[]` | `resolved.identities` | str… | the one identity derivation, materialized (P6) | ciu / templates, hooks, gate | see §4.1.4 |
| `identities.<r>.<svc>.endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.path` / `.publications[]` | `resolved.identities…endpoints` | as declared + list of socket claims `{ scope, network?, bind, port, protocol }` | the endpoint facts joined instances and the gate read; `publications` = every declared or derived socket the layout made ciu publish (the unit of the collision check, T-26) | ciu / joins, gate, humans | `{ scope = "network", network = "mesh", bind = "100.64.0.11", port = 5432, protocol = "tcp" }` |
| `bindings.<consumer>.<local>.service` / `.realization` / `.endpoint` / `.network` / `.host` / `.port` / `.url` / `.path` / `.tls` / `.cert` / `.key` / `.ca` / `.requires` / `.delivery` / `.variables` | `resolved.bindings` | str… / list | how each binding was satisfied (§4.1.5); `<consumer>` = `<realization>.<svc>`, `env.<e>`, or `ciu` | ciu / templates (template delivery), injection (env delivery), probes, gate, assay `derived:` | see §4.1.5 |
| `networks.<n>.name` / `.kind` / `.realized_by` / `.fqdn` / `.tls` | `resolved.networks` | str… | every declared network plus the implicit `instance` one | ciu / templates | `name = "dstdns-hox0ju-network"` |
| `services.<logical>.level` / `.realization` / `.service` | `resolved.services` | level / realization / svc (absent for mock) | the selection actually used | ciu / gate, templates, joins | `"live"` / `"db_core"` |
| `placement.<r>.hosts` | `resolved.placement` | list[host] | placement result | ciu / resolutions | `["gstammtisch"]` |
| `waves` / `edges[]` / `gates.<k>.healthy` / `.completed` / `.facts` / `.probes[]` / `.assumed[]` | `resolved` | list[list] / list of `{from,to,kind}` / lists / list of `{ consumer, endpoint, protocol, address, vantage, result }` / list of `{ kind, subject, provider, host, reason }` | the ordering ciu used and every edge incl. derived (`kind ∈ bind\|depends\|secret→vault\|secret→minter\|network\|pki`); gate facts include binding `facts`, minter and pki facts; `probes` are the consumer-vantage host-network probes (T3-09); `assumed` is non-empty only under `--allow-assumed` (T2-01/T2-03) | ciu / `check --graph`, gate | see §4.1.6 |
| `images.<reference>.ownership` / `.build_owner` / `.expected_id` / `.repository_digest` | `resolved.images` | `project\|vendor` / `<R>.<svc>` / id / digest | the reference-level image map every service reads (T3-07) | ciu / build, push, activate | — |
| `governance.<r>.<svc>.*` | `resolved.governance` | `RK` | effective caps per container | ciu / humans, gate | — |
| `bundle.<host>.…` | `resolved.bundle` (with `--layout`) | table | what `ciu push` ships per host and why each store entry travels (R-32) | ciu / humans, push | — |

### D. Host inventory (`ciu.hosts.toml`, gitignored; `~/.config/ciu/hosts.toml` user-global) (V8-S7.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `local` | `hosts.<h>` | bool | marks this machine; no SSH facts required; no built-in `localhost` (written by `ciu init`) | operator / placement | `true` |
| `fqdn` | `hosts.<h>` | hostname, optional | the host's declared public name (`host.fqdn`; `require_fqdn`) | operator / templates, init | `"gstammtisch.dchive.de"` |
| `ssh_host` / `ssh_user` / `ssh_port` / `ssh_key` / `known_host` | `hosts.<h>` | str / str (`root`) / int (22) / path / str | push transport facts; `known_host` absence refused unless `CIU_SSH_INSECURE_TOFU=1`; written for a fresh host by `ciu host enroll` (V8-S7.2.4: the key under `ciu-ssh/`, the host key pinned after the operator confirmed its fingerprint) | operator or `ciu host enroll` / push, ssh | — |
| `bundle_dir` / `push_mode` / `bundle_excludes` / `docker_optional` | `hosts.<h>` | str (default `/opt/ciu`) / `auto\|rsync\|scp` / list / bool | the directory holding `releases/`, `candidate`, `current`, `previous` — never itself the `current` link (T2-05); transport; exclusions outside the closure only; no Docker checks and no images on this host | operator / push | — |
| `activate.bootstrap` / `.apply` / `.health` | `hosts.<h>.activate` | str | per-verb remote commands, run inside a release; the `current`/`previous` switch and rollback are CIU's own state machine, so there is no `rollback` command (T2-05) | operator / activate | `"ciu up --layout prod3"` |
| `secrets.<entry>` | `hosts.<h>.secrets` | table `{ from = ask\|generate\|file, … }` | host-scoped secrets, stored under `[secrets.hosts.<h>]`, consumed through `from = "host"` | operator / hosts, secrets | `tls_cert_pem = { from = "file", path = "/etc/ssl/edge.pem" }` |
| `addresses.<network>` | `hosts.<h>.addresses` | str | the host's address on each addressed network; the input to every cross-host resolution | operator / resolution | `mesh = "100.64.0.12"` |

### D2. Host capacity (ciu configuration; where it lives and how admission is serialized is open decision Q15)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `slice` / `slice_env` | `capacity.tiers.<t>` (`gates`, `stacks`) | slice name / env-var name | which slice the tier is: named here, or read from a variable ciu config names (mdt's `CGROUP_PARENT_DEV_*` is one possible source, never a requirement) | operator / admission, placement | `slice_env = "CGROUP_PARENT_DEV_GATES"` |
| `usable` | `capacity.tiers.<t>` | size or percent of RAM | the memory ciu may plan warm charges into for this tier; refused when above a readable ceiling (RAM, the slice's `memory.max` via the daemon) | operator / admission | `usable = "40%"` |
| `psi_full_avg10_max` | `capacity` | percent | memory pressure above which nothing new starts, when PSI is readable | operator / admission | `5.0` |
| `no_daemon` | `capacity` | `count\|unbudgeted` | the policy while the profiler daemon is absent, a normal state (Q16, D-653); never "refuse" | operator / admission | `"count"` |
| `max_concurrent` | `capacity.tiers.<t>` | int ≥ 1 | the per-tier count used under `no_daemon = "count"` | operator / admission | `2` |

### E. Stack file (`<location>/ciu.stack.toml`) (V8-S6, S10.2)

| key | table / level | type | reason for existence | owner | example |
|---|---|---|---|---|---|
| `<svc>` | `ciu_stack` | table key `name`, not `secrets` | one RealizedService; identity component 4 | consumer / identity, compose | `[ciu_stack.postgres]` |
| `image` / `instances` / `one_shot` / `primary` / `enabled` | `ciu_stack.<svc>` | str / int ≥ 1 / bool / bool / bool or flag | the one image declaration (pulled unless `build` is declared); replica fan-out; runs-to-completion; the default variant service; conditional inclusion | consumer / compose, graph, gate | — |
| `build.context` / `.dockerfile` / `.args` / `.target` | `ciu_stack.<svc>.build` | dir (may leave the stack and a child root, never the containing worktree — T3-03) / path / table / str | the service's image is project-built by `ciu build` from this context (ownership declared next to the image, T-29); the compose `build:` stanza is injected; one `build` per image reference, other services naming the same reference share it (T2-05); a context outside the stack directory is how a monorepo shares one Dockerfile (§4.3.14) | consumer / build, injection, provenance | `build = { context = "../..", dockerfile = "applications/controller/Dockerfile" }` |
| `requires` | `ciu_stack.<svc>` | list[LogicalService] | sugar for bindings without data: an ordering edge (replaces `init_requires` on empty contracts and `after`) | consumer / graph | `["app_schema"]` |
| `binds.<local>.to` / `.wait` / `.delivery` / `.env_prefix` / `.facts` / `.enabled` / `.probe` | `ciu_stack.<svc>.binds.<local>` | target / `healthy\|started\|none` / `env\|template\|none` / envname / list[TypedFact] / bool / `auto\|none` | §4.1.5: the consumer's dependency under its own name; delivered like a secret; its `facts` form the target's contract; `probe = "none"` is the explicit acknowledgement for a UDP host-network listener the gate cannot prove by connecting (T3-09) | consumer / graph, resolution, injection, templates | see §4.1.5 |
| `provides` | `ciu_stack.<svc>` | list[TypedFact] | facts this service creates by means other than a vault-stored generated secret (replaces `init_provides`) | consumer / conformance, probes | `["pg:role/controller"]` |
| `depends_on` / `probe_user` / `aliases` / `host_network` | `ciu_stack.<svc>` | list[sibling] / str / list / bool | intra-stack start order; the superuser probes use; extra DNS names; `network_mode: host` | consumer / compose, probes | — |
| `health.interval` / `.timeout` / `.retries` / `.start_period` / `.gate_timeout` | `ciu_stack.<svc>.health` | durations / int | per-service healthcheck parameters (merged from `project.health`, **injected** into the rendered `healthcheck`, R-25) and the wave-gate budget | consumer / injection, gate | `start_period = "240s"` |
| `endpoints.<e>.port` / `.protocol` / `.publish` / `.host_port` / `.host_bind` / `.listen` / `.allow_from` / `.path` | `ciu_stack.<svc>.endpoints.<e>` | int / `tcp\|udp\|http\|https` / `instance\|host\|proxy` / int / IP / IP (host-network services only, required there) / list / str | §4.1.5; names unique per stack; publication derived from bindings with data; `listen` is the address a host-network process itself binds — its socket claim, its reachability rule and the gate's live probe follow from it (T2-06) | consumer / resolution, ports injection | `sql = { port = 5432, allow_from = ["network.mesh"] }` |
| `hostdir.<purpose>` / `.path` / `.uid` / `.mode` / `.seed` | `ciu_stack.<svc>.hostdir.<purpose>` | str (`""` = auto) / path / int / str / path | host directories; `vol-*` legacy directories refused until migrated (R-36) | consumer / engine | `data = ""` |
| `configfile.<n>.template` / `.target` / `.mode` / `.schema` | `ciu_stack.<svc>.configfile.<n>` | path / abs path / str / path | rendered into `ciu.rendered/<svc>/<mirrored path>`, mounted by parent directory (R-35) | consumer / injection | `template = "config.toml.j2"` |
| `secrets.<k>.from` / `.path` / `.field` / `.store` / `.var` / `.entry` / `.length` / `.charset` / `.delivery` / `.env_name` / `.mode` / `.uid` / `.enabled` | `ciu_stack.<svc>.secrets.<k>` and `ciu_stack.secrets.<k>` | `vault\|generate\|ask\|file\|host\|ephemeral` / paths key or literal / str / `local\|vault` / envname / str / int / str / `file\|env\|configfile\|native\|hook\|none` (**required**) / envname / str / int / bool | §4.1.8; structured, checked | consumer / secrets, compose | see §4.1.8 |
| `<consumer sub-tables>` | `ciu_stack.<svc>.<x>` | table | free-form service data; never read by ciu; not named `identity`/`health`/`endpoints`/`binds`/`hostdir`/`configfile`/`secrets` | consumer / templates | `[ciu_stack.controller.workflow]` |
| `pre_secrets` / `pre_compose` / `post_compose` | `hooks` | lists of path or `{ run, service, provides, secrets, inputs }` | lifecycle hooks (subprocess, JSON context v2, `--validate`); `provides` on the entry names the facts the script creates (replaces `[hooks.provides.<svc>]`, R-20); `secrets` names the keys the entry may read — on `pre_secrets` only `file`/`ask`/`host`/local-`generate` sources, materialized for it first (T2-04); `inputs` names files outside the stack the release must carry (T2-05) | consumer / hooks_runner, release | `post_compose = [{ run = "post_compose_vault.py", provides = ["vault:secret/vault/controller/role_id"] }]` |
| `env_allow` | `hooks` | list[envname] | the stack's allow-list of variables forwarded from ciu's environment into hooks (clean environment otherwise, T-23; closed in the `[hooks]` key set, T2-03) | consumer / hooks_runner | `env_allow = ["GITHUB_API_URL"]` |
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

### H. run-gate lane TOML → v8 home (`ciu migrate --gate` converts it; run-gate is archived at 8.0.0)

| run-gate key / knob | v8 home | note |
|---|---|---|
| `schema_version` | `project.revision` | one revision gate |
| `run-gate.root.toml` (central config, R-22) | a zero-instance root `ciu.toml`, inherited through `[ciu] inherit` (closed list) | recursive, explicit path, no walk-up |
| `environments.<n>.image` / `.mode` / `.forward_env` | `testing.environments.<e>.image`/`image_from`, `.mode`, `.forward_env` | the mode names per Q11 |
| `environments.<n>.cgroup_slice` / `cgroup_slice_env` | `testing.cgroup_slice` / `testing.cgroup_slice_env` | R-10's order kept |
| `environments.<n>.container_name` (and the R-14a derivation) | `exec_in` + derived identity + mount proof | retired |
| the built-in `host` (a container, R-42) / `bare-host` | a declared ephemeral environment / the subprocess mode | Q11 |
| `lanes.<n>.kind/environment/argv/assay_lane/description/clean_tree/budget/required_env/artifacts/stall_timeout/profile` | `testing.lanes.<l>.*` | unchanged; `budget` enforced as a ceiling |
| conjunction lanes (`bash -c "run-gate a && run-gate b"`) and R-25 | `kind = "sequence"` | in-process; one base |
| `lanes.<n>.assay_command` + `pins.*` (external) / source mode (internal, R-08 rev 11) | `testing.judge.command` + `sha256` / `testing.judge.source` | one declaration (Q12) |
| one restated block per assay lane | `testing.judge.import` | RG-76 |
| `lanes.<n>.resources.memory/memory_swap/cpus/cpu_weight/io_weight/shared` | `resources.memory_max/memory_swap_max/cpu_max/cpu_weight/io_weight/shared` | cgroup vocabulary |
| `[profile]`, `[footprint]`, `run-gate.footprint.json` | `[testing.profile]`, `[testing.footprint]`, the lane section of `ciu.footprint.json` | re-measured at cutover |
| `.run-gate/history`, `.run-gate/inflight/` | the evidence directory's history and `runs/<run_id>/run.json` | history restarts (Q13) |
| `.run-gate/assay-state/<path>/` | `<git-family state root>/ciu-gate-state/<path>/` | RG-38 semantics, renamed |
| `.assay/progress-<lane>.jsonl`, `.assay/verdict-<lane>.json` | `<run_dir>/progress.jsonl`, `<run_dir>/verdict.json` | estate AGENTS' progress rule is rewritten at cutover |
| `RUN_GATE_EXTRA_MOUNTS` / `_MOUNT_ALIAS` / `_LOCK_DIR` / `_PROFILE` / `_HOST_IMAGE` / `_PROFILE_TRANSPORT` | `extra_mounts` / derived from mountinfo (R-12) / gone / `[testing.profile] enabled` / an environment's `image` / `[testing.profile] transport` | declared keys; test-only roots become `CIU_GATE_*_ROOT` |
| `RUN_GATE_PROFILE_SESSION` (exported into lanes) | kept verbatim | the daemon matches it by name (§4.1.10 Shortcomings) |
| `--worktree`, `--allow-dirty`, `--check-env`, `--fresh`, `--dry-run`, `--list`, `history`, `footprint`, `doctor`, `validate-pointers` | `ciu gate …`, `ciu gate history\|footprint\|doctor`, `ciu check --gates` | `--list` columns frozen |
| `--version` (the copied-script revision, R-00/R-31) | `ciu version` | one wheel version |

### I. Environment variables ciu reads in v8

`CIU_EXIT_ON`, `CIU_SECRET_<VAR>`, `VAULT_TOKEN`, `CGROUP_PARENT_DEV_BACKGROUND` (stack tier), `CGROUP_PARENT_DEV_GATES` (gate tier, R-10), `CIU_HOSTS_FILE`, `CIU_SSH_TRANSPORT`, `CIU_SSH_INSECURE_TOFU`, `CIU_KSM`, `CIU_GOV_BASELINE_PATH`, `CIU_SKIP_DOOD_PREFLIGHT`, `CIU_GATE_EVIDENCE_DIR`, `CIU_GATE_CGROUPFS_ROOT` / `CIU_GATE_PROC_ROOT` (test-only roots), `XDG_STATE_HOME` (backup destination root), `CIU_LEASE_FDS` (set by `ciu lease` for its command; read only to reuse an inherited lock), `NO_COLOR`, `TERM`, `CIU_LOG_PREFIX_TIME_SHORT`; `HOSTNAME`, `REMOTE_CONTAINERS`, `WORKSPACE_DIR`, `GITHUB_ACTIONS`, `USER` during `instance init` only. ciu **sets** `RUN_GATE_PROFILE_SESSION` in every profiled lane (the contract fixes the name). Retired in addition to revision 2.1's list: `CIU_SKIP_DEPENDENCY_CHECK` (preflights are per need, R-02); and by revision 4.0: `CIU_MAX_CONCURRENT_INSTANCES` (no count), `CIU_GATE_EXTRA_MOUNTS` and `CIU_GATE_MOUNT_ALIAS` (declared `extra_mounts`; the alias is derived), and `XDG_RUNTIME_DIR` as the admission-ledger root (the ledger is gone).

### J. Keys retired in v8 (the full drop list is §4.8)

Revision 2.1's list (`deploy.environment_tag`, `deploy.network_name`, `[deploy.phases]`, `[topology.*]`, `[service.<n>] type/location`, stack-level `requires/provides`, `<svc>.name`, `ports`, `resources`, `ciu.repo_root`, `[deploy.resources]`, `vault.stack_path`, `expose_env`, `shared_infra`, `auto_generated.*`, run-gate `pins`/`assay_command`/`memory`/`container_name`, `$VAR` in TOML, `exec_targets`, `env_required`, `[state]`) plus, retired by revision 3.0: `deploy.labels.prefix` (R-15), `deploy.env.defaults` (R-13), `[service.<n>] contract` (R-19), `init_requires` / `uses` / `after` / `init_provides` / `[hooks.provides]` (R-18, R-20, R-21), the `routes` binding and two-pass render (R-05), secret `directive` strings / `consumed_by` / `produced_by` (R-30, R-31), `request_base` (R-52), `public_fqdn` detection (R-16), `owned-seeded` (R-29), every `.j2` declaration file (R-08), the per-stack rendered `ciu.toml` and `ciu.toml.j2` override (R-09, R-10), `[ciu.instance.resolved.render] complete` and the rendered-file lock (R-42), `CIU_SKIP_DEPENDENCY_CHECK` (R-02), `facts_schema` (R-49). Retired by revision 4.0: `generated.owner_id` and the `ciu.owner` label (Q10), `[ciu.instances] max_concurrent` (D-647 #4), a literal `testing.cgroup_slice` as the default (R-10), `ciu instance add --join` and per-instance ad-hoc joins (Q9), `ciu instance exec --env` (→ `ciu gate exec`), and every run-gate key of table H.

## 4.6 Spec/schema check

**For every proposed table: does it exist, and what changes?** (`S<n>` = v7 SPEC 5.0.0; `V8-S<n>` = SPEC-V8 draft.7.)

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
| `[ciu.instances] lease_ttl_hours/join_presets/default_join_preset`, the shared `.workspace-instances/` records, `ciu.instance.json` | S16.3/S16.9 `[ciu.worktree]`, `ciu.worktree-instance.json`; 7.15.1's shared records (vbpub@b9cad87c3) | rename; the count `max_concurrent_instances` dropped for admission; the four `--shared-infra*` flags → committed presets (CIU-116); `exec_targets` retired; `ciu.instance.json` is a product record, not the registry |
| `[governance]` `RK` incl. `cpu_max` → `cpus` | S15 keys incl. `cpus` (CIU-90, 7.11.0) | rename to cgroup vocabulary |
| `[testing.*]` incl. `inherit`, `binds`, `services`, `sequence`, `require_provenance`, `history`, `judge.import`, `profile` | run-gate `run-gate.toml`, `run-gate.root.toml` + `RUN_GATE_*` | **owner change**: run-gate is ported into ciu and archived at 8.0.0 (one implementation) |
| `[vault] service`, `[vault.paths]` read | S4.16 `vault.stack_path` + basename heuristic; `[vault.paths]` unread | pointer by logical service; paths become a checked reference table |
| `ciu.rendered/<svc>/…` directory mounts | S5.3a | **kept** from v7 (revision 2.x had regressed to file mounts) |
| plain-TOML declarations, `ciu schema --json` | S3 `.j2` layers | **shape change** (P11) |
| instance lock = 7.15's root-lock file; family lock = `libraries/worktree`'s | `workspace.py` `root_lock` (7.15.1); S4.26 per-stack secret locks; S16 registry locks | adopted, not new; no rendered-file lock, no checkout-directory lock |
| the host capacity config, `ciu.footprint.json`, the host admission lock | v7: `max_concurrent_instances` + CIU-94's `memory.min` admission; run-gate R-29 + `run-gate.footprint.json` (R-44) | new homes; demand measured through the cgprofile daemon (RG-55) |
| `[service.<n>] share`, `[ciu.instances.join_presets]` | S16.1's `--shared-infra*` flags | committed and checked (Q9) |
| `ciu.hookkit`, hook context v2 | S9 in-process hooks | **model change** with a helper library |
| `ciu migrate`, `ciu init` v8, `ciu doctor`, `ciu status`, `ciu show`, `ciu dev`, `ciu ssh`, `ciu provenance` | v7 verbs S19, S13.7, S7.10, S5a, S14.1, S17.2 | kept with dispositions (R-61) |

**How this schema itself is validated mechanically** — unchanged in structure from revision 2.1: (1) closed-key validators generated from one declarative table-spec (now also the source of `ciu schema --json`); (2) S5.7 JSON-schema validation of rendered *application* config files, not stretched to ciu's own referential rules; (3) hook `--validate` findings with severity through `ciu.exit_on`. Plain-TOML declarations add a fourth layer for free: any external TOML validator, editor or third-party tool can check a ciu file against the emitted JSON Schema.

## 4.11 Non-breaking improvements to the existing tools

Status of revision 2.1's items: **N1** (auto `ciu check`, CIU-64), **N2** (severity findings, CIU-65), **N3** (`stack:*` self-satisfied, CIU-63), **N4** (`gate_timeout`, default-on gate, bounded poll, CIU-67/68), **N5** (`exec_targets` key, CIU-69), **N6** (provider-resolved probes, CIU-70, and CIU-89's override table), **N7** (`StrictUndefined`, CIU-74) — **shipped** in ciu 7.x. **N8** (container-name collision WARN, CIU-66 static half), **N9** (undocumented keys), **N10** (dead keys), **N15** (vault heuristic WARN), **N16** (`ciu render --json`), **N17** (dstdns cleanups) — still open and still safe. **N11** (single assay pin in run-gate) — superseded by RG-33's judge floor. **N12** (`--base` pass-through, RG-26) and **N14** (assay wave) — shipped. **N13** (run-gate `image_from_ciu`) — dropped: run-gate's exec derivation is replaced by V8-19's read of `ciu.resolved.toml` when present. Revision 4.0: **N18** (run-gate reads `ciu.resolved.toml`) is dropped with V8-19's alignment, because one implementation leaves no second reader to align; **N21** stays optional; **N22** shipped as run-gate R-41 (rev 35, a name-keyed `/tmp` lock).

New, safe now:

| # | mechanism | tool | why it is safe | what it improves |
|---|---|---|---|---|
| N18 | run-gate: when a checkout carries `ciu.resolved.toml`, read `resolved.identities` for exec-mode container names; otherwise the v7 path (RG item, V8-19) | run-gate | additive lookup order | run-gate keeps working against v8 checkouts |
| N19 | `ciu schema --json` for the v7 tables from the existing validators' key sets | ciu | read-only verb | editor completion for v7 adopters; the table-spec V8-1 needs anyway |
| N20 | `ciu.hookkit` shipped as a v7 package with `wait_healthy`/`wait_tcp` wrappers over the existing `hooks_runner` probes, usable from in-process hooks | ciu | additive module | dstdns hooks stop hand-rolling polls before v8 |
| N21 | run-gate: `kind = "sequence"` lanes (in-process conjunction) | run-gate | new lane kind; string conjunctions still work | removes the R-25 override hazard for adopters that stay on run-gate |
| N22 | run-gate: RG-39 internal exec-target lock — an exclusive `flock` taken after the sorted shared-infra locks and released in the same `finally`, keyed by the resolved container name (`/tmp/run-gate-exec-<name>.lock`, RG-20's file discipline) and, once N18 reads `ciu.resolved.toml`, by the owning stack directory of V8-S14.4.7 instead, so v7 run-gate and v8 `ciu gate`/`ciu lease` serialize against each other during the cutover; exec mode only (an ephemeral `docker run` is never shared) | run-gate | additive lock in a fixed global order; dry runs plan but never block | the caller-side `flock` of dstdns GUIDE §1 stops being required for correctness |
| N23 | **v7 backport of host enrollment** (`SPEC.md` S14.7; `CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2; CIU-93): `ciu host enroll` steps 1 and 2 on v7's `.ciu.hosts.toml`/`.ciu/secrets/hosts/<name>/` shapes, plus ciu's own `get.py` (`cmru get-py --project ciu`, committed and released) and cmru's `get.py enroll` template subcommand (KI-24) | ciu v7, cmru | additive verb and files; touches no existing S14 path; an explicit operator exception to v7's maintenance-only posture | dstdns P171 can enroll its remote seeded-infra host without a hand-written row |
| N24 | **Phase A in run-gate v7** (Q2): RG-67, RG-73, RG-74, RG-75 and RG-76 with their oracles, then RG-77's SPEC and skill text | run-gate | additive keys and lane kinds; nothing existing changes meaning | dstdns deletes `gate-slot.sh` and `gate-base.sh` and leaves Mode A now; the oracles become the port's parity tests |
| N25 | ciu v7: CIU-115 repair-in-place of outdated records, and `ciu clean --identity <old>` (CIU-119 (3)) | ciu v7 | a format migration of ciu's own artifact; a new clean selector | an upgrade stops being a manual chore in every worktree; retired ids become removable without raw `docker rm` |
| N26 | ciu v7: CIU-117 instance-scoped project-built tags; CIU-118 `ciu resolve`/`ciu exec` | ciu v7 | an opt-out flag; a read-only query plus an exec that follows S16.7 | dstdns deletes its five container-name derivations; a worktree can change its own runtime image |
| N27 | `skills install\|list\|check\|uninstall` in every vbpub tool through cli-extended CLI-EXT-02 (Q14) | cli-extended, each tool | an additive verb | skills match the installed version; no vbpub checkout or symlink needed |

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

Rows changed or added by revision 4.0:

| # | mechanism | source | tag | disposition |
|---|---|---|---|---|
| K1 | run-gate lifted and maintained in parallel (J1's disposition) | rev 3.0 R-01 | SUPERSEDED | one implementation: run-gate ported and archived (D-647 #1) |
| K2 | checkout-directory instance lock; `ciu.instance.json` registry + `ciu-instances.lock` | rev 3.0 X46, rev 3.2 | SUPERSEDED by SHIPPED | 7.15.1's root lock and `libraries/worktree` records (memo §3.5) |
| K3 | 6-hex instance id; owner token | draft.7 S4.1.1, S4.5 | CONTRADICTED by SHIPPED and by the operator | 7.15.1's base36 id verbatim; token dropped (Q10) |
| K4 | `[ciu.instances] max_concurrent` | draft.7 S14.6 | CONTRADICTED | admission by RAM (D-647 #4) |
| K5 | slice-directory admission ledger per uid | draft.7 S16.6.1 | CONTRADICTED | the slice is invisible from the devcontainer; ciu decides from daemon data and host config (Q4, Q6) |
| K6 | `memory_min` preflight-only | draft.7 S13.2.1 | CONTRADICTED by SHIPPED | CIU-94 writes it (7.12.0) |
| K7 | assay state under the judged tree's `.assay/`, unmanaged | draft.7 S16.7.2 | CONTRADICTED by SHIPPED | `--state-dir` (RG-38, rev 37) |
| K8 | default base `merge-base HEAD @{upstream}` | draft.7 S16.7.2 | CONTRADICTED by SHIPPED | the fork commit first (R-35a, CIU-106); trunk merge (RG-74) |
| K9 | `cgroup_slice` defaults to `governance.cgroup_parent` | draft.7 S16.2 | CONTRADICTED by SHIPPED | `$CGROUP_PARENT_DEV_GATES` (R-10, vbpub@41c1cafb3) |
| K10 | per-instance `instance add --join`; dstdns's "vault only" | draft.7 S9.5; D-647 #3 | SUPERSEDED | committed presets and per-service shareability (Q9) |
| K11 | the judge as a floor only, baked into the image | draft.7 S16.3, the demo | QUESTIONABLE | one `[testing.judge]` with a pin or a source (Q12) |
| K12 | profiling, re-attach, liveness, footprint, lane services, judge import | run-gate R-39..R-44, RG-75/76 | SHIPPED (run-gate) / PROPOSED | ported (§4.1.10, §4.1.10a) |
| K13 | skills in source trees | 7.14.0, vbpub@33c0b0c2 | SHIPPED, QUESTIONABLE | wheel package data + `skills install` (Q14) |

## 4.3 Elongated reasoning — the integrated design, walked through

### 4.3.1 What the interviews decided

**2026-08-30 (five rounds, revision 2.x)** — recorded in full in revision 2.1 §4.3.1 (git `2347191f`); in short: absorb run-gate (F1); identity as data (F2); network entities (F3); `joined` kind (F4); explicit layouts always; `[ciu_stack.<svc>]` root (F18/F18b); generic registry (F18c); phases dropped and waves written (F6); `delivery` mandatory (F5); cgroup keys (F7); image-baked judge floor (F8); rendered-file lock (F14 — **superseded 2026-09-02**); one endpoint shape (F11); `ciu gate` + `ciu instance`; flat rendered artifacts; overlay renamed.

**2026-09-02 (three rounds, revision 3.0)** — the review put ten forks to the operator; every question carried a recommendation and the options not chosen are in the review §4:

| fork | decided | the operator's reasoning, in short |
|---|---|---|
| Q1 standalone vs absorption | **lift run-gate's functionality into ciu; keep run-gate alive in parallel** — superseded on 2026-10-03 by D-647 #1, one implementation (§4.3.2, §4.3.17) | "for max synergy … possibly align with future changes in ciu v8" — the standalone constraint is satisfied by run-gate staying, and ciu owes a zero-ceremony mode |
| Q2 lock object | **checkout directory fd** — superseded in revision 4.0 by the shipped 7.15 lock and records (§4.3.7) | five mechanisms and a hole vs none |
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

**2026-09-03, later (round-2 delta audit → revision 3.2)** — no fork was put to the operator: the reviewer's one settled-decision challenge (T2-08, the unauthenticated `--move`) is a cost of the in-checkout posture and its fix — an owner token in the generated file — stays inside that posture and changes no identity, so it was accepted as the author's call (§4.3.14). Three operator decisions of the same day shape what follows: The owner token was withdrawn in revision 4.0 (Q10, §4.3.17).

| decision | decided | the operator's reasoning, in short |
|---|---|---|
| where v8 is implemented: a new subproject `vbpub/ciu8` with console script `ciu8` (recommended) · in place under `ciu/` · a long-lived branch | **new subproject `vbpub/ciu8`** — own cmru/gate/backlog; v7 `ciu/` maintenance-only; reusable v7 code copied and adapted, never imported; the 8.0.0 cutover renames `ciu8` → `ciu` and archives v7 as `ciu7` | the breaking scope and the dev effort make a parallel track cheaper than a shared tree; every §4.4 row names `ciu8/` as its destination |
| does ciu need a canonical instance exclusive-access primitive (nyxloom `Stack: exclusive`, dstdns's caller-side `flock`, RG-39)? | **yes — the two existing lock keys, documented as canonical, plus `ciu lease acquire\|status\|wait`** (V8-S14.4.7–S14.4.8); exec lanes serialize per target container (S16.5.7); a name-keyed lease rejected | a lease must survive renames and exist before a render; a directory git tracks does both, and a third party can take the same `flock` without ciu |
| monorepo-wide shared governance + worktree-scoped tester stacks (meta-root `ciu.global.defaults.toml.j2` with `autostart`?) | **`[ciu] inherit` for a closed list of policy tables; a per-project two-file tester stack over the shared Dockerfile; no meta-root, no `autostart`; zero-instance mode unchanged** (V8-S3.1.5, S6.2, S16.11.1) | explicit path over walk-up (defaults are hazards); bundles already say what `up` brings up; a root file owning every nested project's deploy set conflicts with nearest-root resolution; a shared stack *directory* was rejected after the rule text showed rendered artifacts and the lock live in it |

**2026-09-03, round 3 (revision 3.3)** — no fork was put to the operator. The reviewer's narrow reopening (T3-05: the owner token cannot prove a live move) was accepted as the author's call inside the posture: moves are cold, copies are refused, the token stays a collision mark (§4.3.15). The round also exposed, behind its first-deploy finding, that every mutable file would have lived inside an immutable release on a target; the **state root** (V8-S2.6) is the fix and does not touch the in-checkout posture for checkouts.

**2026-09-03, host enrollment (revision 3.4)** — the operator reviewed the filed enrollment design (CIU-93, revision 1: a token-authenticated bootstrap URL behind tls-edge, a callback, a cmru download-backend prerequisite) and proposed the simpler shape: `ciu host enroll <name>` prints a one-liner that fetches ciu's installer and runs it with the control host's public key and name, "not using bootstrap".

| fork | decided | the operator's reasoning, in short |
|---|---|---|
| token + callback (rev 1) · printed one-liner with the public key, fingerprint confirmed by the operator (rev 2) | **rev 2**, with three refinements from the author: the target-side mode is `enroll`, separate from the activation verbs; `--controller` names the key comment and never a callback (`from=` opt-in); the installer URL is version-pinned | the callback carried two facts the operator had to confirm anyway; no token, no endpoint, no cmru backend |
| v8 only · v8 and a v7 backport | **both** — V8-29 in v8, `SPEC.md` S14.7 as a v7 package (an explicit exception to v7's maintenance-only posture) | dstdns needs remote placement (P171) before ciu8 ships |

**2026-10-03, reconciliation (revision 4.0).** Two operator decision records, then a two-round interview on the reconciliation memo's fourteen questions (dstdns D-646/D-647, D-651, D-652):

| question | decided | the operator's reasoning, in short |
|---|---|---|
| D-646 Q1: one gate or two | **one: run-gate merges into ciu** (D-647 #1) | clean responsibilities; ciu had moved on, so the plan is reconciled first |
| D-646 Q2: Mode A | **retired as a gating mode; a worktree owns its test environment** (D-647 #2) | a worktree must be able to change its own test runtime |
| D-646 Q3: what a worktree may borrow | first "vault only, never postgres" (D-647 #3); **revised by Q9** | — |
| D-646 Q4: stack cap | **a RAM budget from measured per-container footprints, not a count** (D-647 #4) | the host is RAM-limited, and a few app containers cost little |
| D-646 Q5: identity | **7.15's derivation is final** (D-647 #5) | — |
| skills and instructions | **skills ship in each tool's wheel and install into both harness directories** (D-647 #6) | skill versions match the tool; no checkout or symlink dependency |
| Q1 standalone | **A: the ciu wheel in zero-instance mode**; run-gate-project is the main case; the copied script ends | "standalone" means a project that wants only the gate |
| Q2 build order | **A: RG-67/73/74/75/76 in run-gate now, then ported; their oracles become the parity tests** | relief for dstdns now, executable parity later |
| Q3 cmru tester-gate | **A: cmru calls `ciu gate`** | one place places and caps a test container |
| Q4 admission authority | **the operator's own model**: the daemon measures and provides data only; the project commits a resource manifest; ciu decides; an explicit override forces a start; the decide-and-start step takes a host-scoped lock, and each start registers its reservation with the daemon as data | the memo's "the daemon is the admission ledger" was not taken |
| Q5 unmeasured demand | **A: charged at `memory_max`** until measured | conservative and self-correcting |
| Q6 budget source | **host facts and "what may be used" in host-scoped ciu config**; the daemon only measures | RG-55 gives ciu the cgroup data; the decision stays with ciu |
| Q7 hermetic image contract | **A (round 3, D-653): the worktree at its own path plus the git common dir; images may not bake a checkout path; `ciu check` validates it** | — |
| Q8 hermetic lanes and init | **A: no instance init** | "a worktree owns its test environment" must cost nothing |
| Q9 shared infra | **A: committed presets only; shareability is decided per service**: worth it only for heavy services (for dstdns, likely SkyWalking), and only with per-tenant namespaces (a vault prefix or mount; a postgres database and role), which makes postgres shareable too | "vault only, never postgres" was too absolute |
| Q10 identity | **A: 7.15 verbatim; owner token dropped**; the `ciu.checkout` label and `protected` guard deletion; `clean --identity`; repair-in-place | — |
| Q11 CLI and exit codes | **open** (§4.9) | — |
| Q12 judge | **A: one `[testing.judge]`**: a pin (`command` + `sha256`) or a `source`, plus a floor; lanes imported | — |
| Q13 cutover | **A: atomic per repo**; lane history restarts | AGENTS §4.1 forbids a dual path |
| Q14 skills install | **A: `<tool> skills install` through cli-extended CLI-EXT-02** | DRY across seven tools |
| Q4 refined (round 3) | **admission charges the WARM footprint** (hot + warm working set), not peak or total memory; the manifest records it; `memory_max` stays the ceiling and the unmeasured charge | cold pages can be swapped harmlessly; thrashing starts only when warm sets exceed RAM |
| Q15 host facts (round 3) | **reopened**: revision 4.0's mdt-writes-`/etc/ciu/host.toml` assumption withdrawn; ciu config may carry host resources and the usable share; no hard coupling between ciu and mdt | nothing decided that host facts live in host files outside the environment |
| Q16 no daemon (round 3) | **a normal state, so a ciu config policy**: a per-tier count, or start unbudgeted and disclose; refuse-by-default not wanted | running the daemon is the operator's choice |

### 4.3.2 The gate layer: one implementation

**What revisions 3.0–3.4 chose, and what it cost.** They answered the 2026-09-02 standalone constraint ("every tool usable standalone, no hard dependency") by keeping run-gate alive beside a lifted `ciu gate`. The price of that synergy was two implementations, and the month that followed showed what it costs. Between 2026-09-02 and 2026-10-03 run-gate shipped re-attach (R-39), progress- and log-judged liveness (R-40), per-lane profiling and the footprint manifest (R-43, R-44), durable mutation resume (RG-38/RG-49), the fork-point base (R-35a), source-backed judges and the `host`/`bare-host` flip (R-42). None of it reached V8-S16. A gate specified once and implemented twice had diverged within a month, and run-gate now contradicts several S16 rules rather than merely being ahead of them (§4.3.17).

**The ruling.** The operator ruled one implementation (D-647 #1). The standalone constraint is answered by the cost of using the one gate, not by a second copy: a project with only `[project]` and `[testing]` installs the ciu wheel and runs `ciu gate` with no instance, no lock and no rendered file, with docker only for container lanes and a judge only for assay lanes (Q1). Every estate consumer already installs a toolchain beside its symlinked `run-gate.py`, so the pip install is the only new cost.

**How it is built.** The port is a copy-and-adapt of today's run-gate into modules under the v8 entity model, not a fresh implementation of S16; S16 is the older and smaller contract. The synergy the operator wanted in revision 3.0 is unchanged and now has one home:
- an `exec` environment names a LogicalService;
- preconditions come from the record and the graph, in process;
- caps use the governance vocabulary;
- the judge is one declaration plus the verdict's provenance;
- environments carry bindings and lane services;
- sequences run in one process;
- a monorepo declares its tester once;
- every LaneResult says whether the containers it tested match `HEAD`, and what the lane actually used.

### 4.3.3 Identity, the stack-file root, and why declarations stopped being templates

Identity is unchanged from revision 2.1 (one derivation, data in the rendered file, the operator's F2) with two corrections: the injectivity claim was false and is now an honest check (R-14), and ownership labels are ciu's own namespace so that a consumer setting can never orphan a container (R-15).

The stack-file root `[ciu_stack.<svc>]` stands (F18). What changed is the file around it. Revision 2.1's own §4.3.3 had already found that Jinja was "used as a crutch" for identity assembly and replica fan-out and left it in place for data expansion. The review followed the remaining uses to their ends: `{{ vault.paths.x }}` inside directive strings (a path reference — now `path = "x"` against a checked table), `{% set %}` constants (inlined), `{% for %}` generating near-identical `[service.x]` tables (now one line each), and `{{ routes.* }}` in stack TOML (the cause of the two-pass render). With those gone, a declaration file has no expression left in it, and every argument against Jinja in declarations becomes decisive: an external validator can read the file; `ciu schema --json` can describe it; `ciu instance init --join-preset` and `ciu migrate` can rewrite it round-trip; a typo is a schema finding, not a template error (P11).

### 4.3.4 From routes to bindings — the graph and the contract

Revision 2.1's consumer surface was `init_requires` (edge + route), `uses` (route only), `after` (edge only), and `routes.<X>.<e>.*` reads in templates and stack TOML, with a hand-typed `contract` on every LogicalService. Walking the demo showed the costs: seventeen stack files read `routes` (hence the two-pass render); consumers wrote a mapping hop (`[ciu_stack.controller.database] host = "{{ routes.main_db.sql.host }}"` then `{{ ciu_stack.controller.database.host }}` in compose) to give their templates a stable local name — which is the local-name idea done by hand; an ordering-only `init_requires` derived a route and therefore a cross-host publication nobody used (R-22); and the contract was a copy of the provider's own `provides` list, restated on the logical table, with the vault facts derivable from directives anyway (R-19).

The binding collapses this into the shape secrets already have: a consumer declares *what it needs* under *its own name* and *how it wants it delivered*. `to` names the capability (and optionally the endpoint); `wait` says whether the dependency orders the deploy (`healthy`, `started`) or is runtime-only (`none`, the old `uses`); `delivery` says whether ciu injects `PREFIX_HOST/PORT/URL` into the container (`env`) or binds the resolution for the templates (`template`) or nothing (`none`); `facts` says what the consumer relies on. `requires = [...]` is the sugar for bindings without data (the old `init_requires` on an empty contract, and `after`). The contract of a capability is then the union of what is bound to it — the only definition under which a check compares something a consumer actually depends on — and every declared variant is checked against it whether or not it is selected, so a seeded stub that lacks an endpoint fails `ciu check` today rather than on the first `--realness` switch. Providers still declare `provides` (facts by means other than a vault-stored generated secret) and hook entries carry their own `provides` (R-20); minter edges are derived as before. Publication now follows bindings with data only.

What was **not** adopted: stable DNS aliases by construction (the consumer hard-codes `main_db:5432` and ciu makes it true) — it cannot express external providers on non-standard ports or cross-host published ports without a local proxy; and binding-carried credentials (§4.3a C).

### 4.3.5 Realness, immutability, and the join

Unchanged in mechanism (revision 2.1 §4.3.5), corrected in three places: the record is a constraint, not a source in the precedence chain (R-26 — revision 2.1's S9.3.1 would have let a changed pin be silently overridden by the record while S9.4.2 promised an ERROR); joins name a unique label or an absolute path (R-27 — basename resolution was a third form whose meaning changed when a checkout was renamed); the joined-vault token path is written down (R-28). `owned-seeded` became `seeded`.

### 4.3.6 Topology — unchanged model, two corrections

The entity walk of revision 2.1 §4.3.6 (ten scenarios plus four additions) holds for bindings unchanged, because a binding with an endpoint resolves exactly as a route did. Corrections: `fqdn` is declared per host (R-16 — deriving it from a "public"-described address gave `description` the semantics S7.3 promised it would never have); a `per_host` capability may be `requires`'d but not bound with data (the old "no route to it").

### 4.3.7 Locking

**Revisions 2.1 and 3.0.** Revision 2.1 locked the rendered file. Revision 3.0 moved the lock to the checkout directory's descriptor, because its inode is stable for the life of the checkout and nothing git or ciu does to files touches it (the operator's Q2).

**What 7.15.1 shipped.** ciu 7.15.1 then shipped a different answer with the same property and more. The root lock is a `flock` on a file in the git common dir's `.workspace-instances/`, keyed by the ciu root's offset (`workspace.py` `root_lock`), and allocation is serialized by the `libraries/worktree` family lock that cmru shares. Neither file is in the checkout, so `git clean -x` cannot touch them; the lock does not depend on the checkout directory existing; and it is one lock authority for two tools. That last point is the library's own rule: "an adapter must not implement a second generic Git lifecycle or lease authority". Keeping v8's own directory lock and `ciu.instance.json` registry would have made v8 a second authority over the same checkouts as 7.15 and cmru.

**Round 4 and the remaining locks.** Round-4 T4-01 (a release-local directory inode does not serialize two releases of one instance) is answered on the same principle: where there is no git family — a release on a target — the instance lock is keyed on the stable state root, never on a release directory. The gate's shared class stays for the one interleaving that matters, `up` recreating a container under an `exec` lane. The host admission lock is new and separate, and its form (a lock file, an ordering on the Docker daemon, or the profiler daemon) is open decision Q15. It serializes only the decide-and-start step across every ciu on the host, so it is acquired last and held briefly (§4.1.10a).

### 4.3.8 The gate in detail

The gate is run-gate's behaviour in the entity model (§4.3.2). It keeps one interface to assay: `assay lanes --json` (B044) yields lane names, `base_source`, `external_tools`, `argv0` and `env_required`, and ciu neither parses `assay.toml` nor keeps a key that could disagree with it (R-52).

What revision 4.0 adds to the judge comes from the two modes run-gate already serves. External consumers pin an artifact by command and digest (dstdns's `tools/assay/assay-7.2.0.pyz`). Estate-internal projects install assay from the judged worktree's source (run-gate R-08 rev 11). One `[testing.judge]` carries either, plus the floor, and imports the lanes. dstdns's 118 restated lane blocks and 236 version sites thereby become one table (Q12, RG-76).

Progress goes to the run directory, so no two invocations share a file. Resume state goes to the git family's durable state root, so a thrown-away worktree keeps it. Provenance: v7's `ciu provenance` was a test-time question ("does this passing run describe the code I think it does?"), which the gate answers per lane.

#### 4.3.8a Push secrets — the rule is per source

The operator's question back ("there could be no vault … how is remote different from localhost?") is the right frame. Local and remote differ in exactly one thing: where `ciu.secrets.toml` is. On localhost `up` has it next to it. A remote `up` runs from a bundle, so whatever only the sender knows must travel — generated local values (they must agree across hosts), asked values (there is no operator on the target), file values read on the sender. A `host` entry is read on the target by definition. A `vault` value lives in Vault, so the only question is *who fetches*: the target, if it has a derived resolution to the `vault` LogicalService (it is in the deploy set like any capability); otherwise the sender pre-fetches and ships. A project without Vault therefore ships its whole reduced store; a project whose Vault is reachable from every host ships local-source entries only. Neither v7's fixed "target fetches" nor revision 2.1's fixed "sender ships everything" covers both projects; the derived rule does, and `ciu check --layout` prints it per host so nothing travels invisibly (P3).

### 4.3.9 Naming decisions taken without a fork

`binds` / `to` / `wait` / `delivery` / `env_prefix` / `facts` for the binding keys; `from` / `store` / `path` / `var` / `entry` for secret sources; `requires.healthy` on lanes (was `requires.services`, one fewer sense of "service"); `[hosts]` (was `[deploy.hosts]`); `[resolved]` (was `[ciu.instance.resolved]`); `ciu.secret-copy.*`; `ciu.rendered/`; `schema_version` everywhere. Listed in §4.9 so they can be overturned cheaply.

### 4.3.10 Defects filed upstream during this pass

None in ciu's v7 code — the review was of the design set. One run-gate item is filed (exec-mode container derivation must read `ciu.resolved.toml` when present, V8-19/N18); one ciu backlog pointer records the design-set revision. Revision 4.0 filed nothing new upstream. It rests on the items the 2026-10-03 consumer-input pass filed (ciu CIU-115..119, run-gate RG-73..77 and the notes on RG-67/RG-70, nyxloom NL-29, cli-extended CLI-EXT-02) and on the reconciliation memo. Two follow-ups it identifies are not yet filed: the cgroup-profiler reservation verb (§4.4 V8-31), and dstdns's baked `ddcli` source (§4.9 Q7).

### 4.3.11 The revision 2.0 → 2.1 review rounds

Recorded in revision 2.1 §4.3.11 (git `2347191f`): the locking classes, derived vault facts and minter edges, proxy networks address-free, derived TLS secrets, the generated-file split, `primary`/variant service, `mock = {}`, derived publication, `per_host`, `healthy` defined once, `exec` caps validated, the last-table completion marker (now retired with the lock), `uses` (now a binding with `wait = "none"`), `ASK_HOST` (now `from = "host"`), `ciu.state.toml`, hooks as subprocesses (now with hookkit), `request_base` (now derived), two-pass render (now retired), `_` → `-`. Every item that revision 3.0 retired is listed in §4.8 with its reason.

### 4.3.12 The 2026-09-02 adversarial review (revision 2.1 → 3.0)

A fresh reviewer read the design set against the v7 specification, run-gate's specification and every adopter, the backlog, and a source-level dependency map, and returned 78 findings (`CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`): 5 blockers (R-01 the standalone constraint, R-06 the Jinja overlay written by ciu, R-26 the realness precedence contradiction, R-51 zero-stack mode needing an instance, and R-14's false structural claim counted as major), 22 major, the rest minor or notes. The operator was interviewed on the ten forks of §4.3.1. Everything accepted is in Part 1; three findings were resolved by keeping the status quo (R-33 store blast radius, R-46 the registry record's duplicated `instance_id`, R-68 the residual senses of "service"); one idea was deferred (§4.3a C). Because the accepted findings change the file set, the consumer surface (bindings), the secret grammar, the contract, the lock and the gate's posture, revision 3.0 and draft.3 are fresh texts rather than patches, and this section plus §4.7 X41–X56 are the trail from 2.1.

### 4.3.13 The independent third-party review (revision 3.0 → 3.1)

An independent reviewer with no history in the estate read draft.3, rev 3.0, the first review, the graph note and the demo against v7, run-gate's specification and the estate doctrine, derived prod3's closure, waves and publication table by hand, wrote the minimal project from the spec alone, and returned 35 findings with the verdict "not implementable as written" plus seven alternative designs. The author verified every finding against the text (response document §1: 31 hold as stated, 3 in part, 1 documentation) and accepted all 35 in some form. Two findings challenged operator decisions and went to the operator (§4.3.1); five reopened resolutions of the first review round (R-24, R-42, R-46, R-49, R-56 — response §4).

What the round changed, in order of weight: **(1)** deployment became transactional and evidence-bearing — manifested releases with an atomic `current` switch and a real rollback, a secrets capsule with explicit transport semantics, and receipts that carry a host's passed facts to the next host (T-09, T-16, T-25); **(2)** eleven rules that two conforming implementations would have read differently were fixed — compose-project uniqueness, the FQDN type, the hook entry object, network providers named by capability, `started` vs `healthy` at the gate, `ciu init`'s output, the LaneResult envelope, the assay argv and judge minimum, host-network resolution, the `git clean -x` promise (T-01..T-11); **(3)** the wave algorithm, the acceptance rule for selected leaves, socket-claim collisions, cgroup conversions with read-back, admission as a transaction, hook sandboxing and per-entry secrets, atomic secret directories, compose service-reference rewriting, the secret lint's honesty, image ownership on the service, per-service config-directory rules, externals, recursive inheritance, per-artifact API headers and `ciu query` (T-12..T-35). The demo had five defects of its own (tester governance below its lanes, a missing forwarded variable, a judge floor below the API the gate needs, a mesh port collision, a loopback-bound host publication) and its resolved example was hand-written; all are fixed and the example is now derived by the S8.4.1 rule.

What was **not** adopted, and why (response §3): the durable registry with UUID identity (operator decision — visible v7 posture kept, costs stated); SOPS/Vault Agent as requirements (the capsule covers transport, renewable secrets stay out of scope); a Service-Binding directory as a third delivery (no consumer yet; recorded as a gap); signed receipts (they ride the authenticated SSH channel); direct cgroup writes (Docker's adapter plus read-back first); a SQLite lock broker (ordered directory locks suffice on the supported filesystems); firewall generation from `allow_from` (CIU does not program hosts; it warns when the declaration is not consumed); renaming the entities (the operator approved the names one round earlier).

### 4.3.14 The round-2 delta audit (revision 3.1 → 3.2)

The same reviewer audited every draft.4 disposition against the text and read the rewritten sections as implementer and attacker (`CIU-V8-THIRD-PARTY-REVIEW-ROUND2-2026-09-03.md`): 14 dispositions landed, 13 landed incompletely, 8 landed and broke another rule, and ten new findings T2-01..T2-10 followed, four of them blockers — verdict "materially better, still not implementable as written". Every finding was verified against the text (response document §6) and every one produced a change; T2-08, marked by the reviewer as a challenge to the in-checkout posture, was accepted without reopening the decision because its fix (a 128-bit owner token in the generated file, stamped on every resource) stays inside that posture and changes no identity. Two remarks of the audit did not hold as stated and are recorded as such: Appendix B was valid TOML (`location` appears once), and the demo declares eleven `build` tables by design — the twelfth service shares an image, which the reviewer rightly noticed the rules could not yet express (fixed under T2-05).

What the round changed, in order of weight: **(1)** receipts became evidence an implementer can check — a canonical **subject** (instance, layout, host, selection, release or plan digest, `activation_id`) instead of a whole-file digest that `rendered_at` and host-local facts made unreproducible, container incarnations and per-fact observations in the body, freshness bound to one activation, and missing proof refused by default with `--allow-assumed` as the explicit escape (T2-01); **(2)** acceptance is a partition — non-one-shots healthy, one-shots completed, nothing judged twice — and a seeded/simulated selection with an empty contract must say `verify` or `unchecked` (T2-02); **(3)** the eight surfaces draft.4 required in one rule and refused in another are in their closed sets, and a conformance test generated from the schema definition now guards the definition itself (T2-03); **(4)** a `pre_secrets` hook may consume only sources that exist before step 3 (T2-04); **(5)** releases have a computable closure (every non-ignored file under a placed stack plus declared hook `inputs`), image transport by registry digest or archive, a `candidate` pointer, a CIU-owned `current`/`previous` switch and a rollback that refuses without `previous` (T2-05); **(6)** host-network endpoints declare `listen`, claims canonicalize wildcards, and the gate probes the address (T2-06); **(7)** admission locks the slice's cgroup directory with a ledger keyed by the cgroup path, an exec target is used by one lane at a time on its stack-directory lock, and every run writes under its own `runs/<run_id>/` (T2-07); **(8)** the owner token (T2-08); **(9)** the exact `cpu_shares` inverse — the ceiling, verified over all 10 000 weights — and a direct `memory.swap.max` write when memory is unlimited, with enforceable-cap mismatches aborting (T2-09); **(10)** the lint's configfile exemption is per requested value, not per file (T2-10). The audit's incomplete rows were closed the same way: `init`'s full synopsis, push-time materialization of local `generate`/`ask` values, the backup contract (V8-S14.8), stage 12 against the available environment, and this document's own stale spellings (the old lock order, one-level inheritance, `schema_version`, the `>=2.4` judge floor).

**Two design answers** the operator asked for on 2026-09-03 landed in the same draft (§4.3.1): the **canonical lock keys and `ciu lease`** (V8-S14.4.7–S14.4.8, S16.5.7 — the checkout root and the owning stack directory are the only keys; a third party takes the same `flock`; exec lanes serialize per target container; run-gate's RG-39 adopts the same key), and **`[ciu] inherit`** for a closed list of policy tables (V8-S3.1.5 — governance, health defaults, tester environments, judge floor, slice; replaces `[testing] inherit`; a zero-instance root may carry `[governance]` for its inheritors; no walk-up, no meta-root, no `autostart`). The shared-tester half resolved differently from the handoff note's first answer: a shared stack *directory* was rejected once the rule text showed that rendered artifacts (`ciu.compose.yml`, `ciu.rendered/`, `ciu.state.toml`) and the Realization's lock live in that directory — two projects, or two worktree instances of one, would alias them. What a monorepo shares is the image build: a project keeps a two-file stack (`ciu.stack.toml` + `ciu.compose.yml.j2`) whose `build.context` is the monorepo's `tester-unified/` directory (V8-S6.2 lets a context leave the stack, never the checkout), and every worktree instance of that project gets its own governed tester from it. Nothing in v7 gains a mechanism for either half: the v7 posture is one `[governance]` table per root and one shared Dockerfile, both consumer edits (§4.10 item 22).

### 4.3.15 Round 3 (revision 3.2 → 3.3)

The reviewer audited draft.5's ten round-2 dispositions (3 landed, 2 incomplete, 5 landed-and-broke another rule) and returned T3-01..T3-10 — six blockers, four majors (`CIU-V8-THIRD-PARTY-REVIEW-ROUND3-2026-09-03.md`); the demo's topology layer was re-confirmed correct (38 files parse, five waves, 24 collision-free claims, the gate rows). Every finding was verified against the text and accepted (response §7). One reopened a decision narrowly (T3-05): the owner token cannot prove a *live* move, because Docker labels are immutable and a token in a copyable file is copied with the tree. Accepted as the author's call, inside the operator's posture: a move is now **cold** — refused while any resource carries the old owner, else a new id and a new token — a copied tree is refused rather than adopted (`--fresh` makes it its own instance), and `adopt --owner` is gone.

What the round changed, in order of weight: **(1) The state root (T3-02, generalized).** The reviewer showed that a target's host facts would modify a verified release; the same holds for the store, the data directories, the realness records and the hook state, all of which draft.5 placed under the checkout — which on a target *is* the immutable release directory. draft.6 gives every instance one **state root** (V8-S2.6): the checkout itself for a checkout (the in-checkout posture, unchanged), `<bundle_dir>/state/` for a release; every mutable file lives there, releases stay byte-identical to their manifests, and a switch or rollback keeps secrets, data, records and leases. `[ciu.host.generated]` became its own file `ciu.host.toml` (V8-S14.2) and the realness record moved into the instance record. **(2) Activation (T3-01, T3-02).** `ciu activate plan` writes an activation manifest with a fresh id and, per host, the expected release digest and selection; a consumer validates a provider's receipt against the manifest's entry **for the provider**, never against its own release (which is a different release), and an absent id matches nothing; `plan_digest` is gone. The per-host sequence is prepare (host file, check in the target release) → apply → health → receipt → pointer switch, identical for rollback, with pointers untouched on any failure. **(3) The monorepo pattern (T3-03).** `[ciu] inherit` and `build.context` may leave a child root but never the containing worktree; a release carries inherited policy flattened into `ciu.inherited.toml` with source digests; an inherited judge floor is permitted and unused in a project without assay lanes; the demo gains `examples/monorepo/` as the fixture the reviewer asked for. **(4) Locks (T3-04).** The lock matrix (V8-S14.4.9): every mutator takes the stack lock of every Realization it touches, so an external holder is never overrun; a realization-only lease takes no instance lock; lease records are lock-free atomic files; the leased command inherits the held descriptors (`CIU_LEASE_FDS`), so a CIU verb inside it cannot deadlock with its parent. **(5) Closed surfaces (T3-06).** `probes` rows with a closed result, `invalid-receipt`/`no-manifest` reasons, the `ciu/backup`, `ciu/activation`, `ciu/lease-record` and `ciu/run` APIs and the `XDG_*`/`CIU_LEASE_FDS` variables are in their sets, and V8-S3.8.6 makes the document's own enumerations a tested output of the implementation. **(6)** The image map is per reference and archive/none activation binds Compose to a verified id (T3-07); run ids are 128-bit random with exclusive creation and `ciu gate --resume` is withdrawn, since assay's resume is its own and content-keyed — CIU's progress path deviates deliberately from the run-gate `.assay/` convention (T3-08); host-network reachability is probed from the consumer's vantage and a UDP listener needs `probe = "none"` (T3-09); every admitted lane declares `memory_max`, every hard cap aborts on mismatch, and `io.max` is read back (T3-10).

### 4.3.16 Host enrollment (revision 3.4, operator direction 2026-09-03)

dstdns's D-097 had designed and deferred a self-hosted, token-authenticated `get.py --bootstrap-url` for enrolling fresh hosts; D-358 formalized it upstream as CIU-93 with a design (`CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 1) that needed a tls-edge endpoint, a single-use token, a callback and a second cmru download backend before it could work. The operator's reading: the callback exists only to deliver the host key and an address, and the proposal itself required the operator to confirm both — so print a one-liner with the public key instead. Revision 2 of the proposal is that design, with the author's three refinements (an `enroll` mode distinct from `bootstrap|apply|health`; `--controller` as a key comment, never a callback, `from=` opt-in; a version-pinned installer URL) and one consequence that revision 1 had missed: in v8 the `ciu` executable must exist on every target because `prepare` runs ciu there (V8-S17.4.1), so enrollment *is* also the ciu install — `get.py`'s normal job — and ciu must ship its own `get.py` (`cmru get-py --project ciu`). What was withdrawn: the token, the callback, the endpoint, the cmru backend as a prerequisite (a self-hosted mirror is `--installer-url`), and three of the four open questions. What is new: V8-S7.2.4 and the S18 row, `ciu-ssh/` in the state root, cmru KI-24 for the template subcommand, `SPEC.md` S14.7 for the v7 backport, and oracles O1–O6 in the proposal for both carves. The reviewer's attack surface is the trust story of §7 of that proposal: the `curl | python3` posture, the fingerprint-as-second-channel TOFU, the window between step 1 and step 2, the idempotency and privilege model of `get.py enroll`.

### 4.3.17 Revision 4.0 — 2026-10 reconciliation

**Why this revision exists.** Revision 3.4 was written against ciu 7.11 and run-gate rev 33 (2026-09-03). By 2026-10-03:
- ciu 7.12–7.15.1 had shipped a different identity, shared instance records, a `memory.min` admission floor, and worktree fork commits;
- run-gate 23.8–23.9.1 (rev 46) had shipped re-attach, liveness, profiling and the footprint manifest;
- the RG-55 profiling wave had frozen a daemon contract that the proposal never integrated;
- the operator had ruled on the tooling boundary (D-646/D-647).

The reconciliation memo (vbpub@5160c0afa) tagged every v8 rule against that state and put fourteen questions to the operator, who answered them in two rounds (D-651, D-652). This revision rewrites Part 1 wherever a decision or a shipped behaviour changed it, and removes the text it supersedes.

**Where the memo was not taken as written.**
- *Admission authority.* The memo recommended the daemon's registry as "the single RAM ledger" (memo §3.6, Q4 A). The operator chose a different split (D-651 Q4): the daemon measures and serves data and decides nothing, and ciu decides. A host-scoped lock closes the decide-and-start race this opens, and the reservation registered with the daemon is data for the next decision (§4.1.10a).
- *Budget source.* The memo had the budget READ from each slice's `memory.max` (memo §3.6, Q6 A). D-652 Q6 puts host capacity and "what may be used" in host-scoped ciu config. The slice's `memory.max` stays a fact the budget is checked against (§4.1.10a).
- *The daemon's namespaces.* The memo describes the daemon as "root, `cgroupns=host`" (memo §1 item 3, §3.6), repeating SPEC-V8 D.7 (3). The shipped daemon keeps its PID and cgroup namespaces private and observes the host through explicit binds (contract §5; design D-29..D-32; the unmerged SPEC-V8 note `c65ac2daa` says the same). This revision describes the shipped daemon.
- *Shared infra.* The memo's dstdns sketch joins vault only (memo §5, D-647 #3). D-651 Q9 revised that into a per-service shareability judgement with tenant namespaces (§4.1.7a).
- *`@{upstream}`.* The memo's base order drops `merge-base HEAD @{upstream}` (memo §3.2, `base.py`). Port parity keeps it (R-35); dropping it is a behaviour change to make in run-gate first, under Q2's order (§4.10 item 33).
- *Count fallback.* The memo puts a `max_concurrent` count on each environment as the daemon-absent fallback (memo §3.6, §4). Revision 4.1 settles it as ciu config policy (D-653 Q16): a per-tier count, or unbudgeted and disclosed (§4.1.10a).

**Trace of every changed passage.**

| passage | change | rests on |
|---|---|---|
| header | revision 4.0, sources, conventions; status relative to draft.7 | D-652 "Operator direction" |
| §4.1.0 | one gate implementation; one lock and registry authority; one judge declaration; admission | D-647 #1; memo §3.5; Q4, Q12 |
| §4.1.2 | Instance row (base36, shared records); Environment row; new LaneService, JoinPreset and Footprint entities | Q10, memo §2.2 S4.1.1; RG-75; Q9; Q4/Q5, R-44 |
| §4.1.3 | owner token out of the generated file; registry = shared records; `ciu.instance.json` a product record; the resource manifest | Q10; memo §2.2 S14.7; `libraries/worktree` SPEC; vbpub@b9cad87c3 |
| §4.1.4 | example ids `hox0ju`; the 7.15.1 derivation, nested-root composition, collision refusal, `clean --identity`, repair-in-place | Q10; vbpub@d1eb98770; `workspace.py` `root_identity_suffix`; CIU-115 (vbpub@e9eb0aac5), CIU-119 |
| §4.1.7, §4.1.7a | joins only through committed presets; per-service shareability with tenant namespaces | Q9 (D-651); CIU-116; memo §2.3 S9.5 |
| §4.1.9 | no instance count; `gate exec`/`exec`/`resolve`; locks = family lock + 7.15 root lock + stack directory + host admission lock; `protected`; cold move without a token | D-647 #4; CIU-118, RG-70; memo §3.5, T4-01; CIU-105 (vbpub@98957a129); Q10, SPEC-V8 D.5 |
| §4.1.10 | posture: one implementation, a port of rev 46 + Phase A; zero-instance standalone; cmru; hermetic lanes without init; the ported behaviour; mode names and exit table deferred to Q11; the mount rule split into a derived common-dir mount and Q7 | D-647 #1; Q1, Q2, Q3, Q7, Q8, Q11; memo §2.4, §3.1–§3.4; run-gate R-10, R-35a, R-38..R-43; RG-63, RG-66, RG-71, RG-72, RG-74, RG-75 |
| §4.1.10a | new: roles (the daemon measures; the project's manifest; host capacity; ciu decides), the charge rule, the host admission lock, reservations, the override, placement and liveness from the daemon, the daemon-absent case | D-651 Q4, Q5; D-652 Q6; D-647 #4; RG-55 contract 1 + v1.1; design D-17..D-32; CIU-94 (vbpub@a4f5aa94); live probe 2026-10-03 |
| §4.1.11 | the contents of stages 11 and 12 | Q4, Q9, Q12 |
| §4.1.12 | the verb list | CIU-118, RG-70, Q9, Q10, Q13, Q14; vbpub@05f373a40 |
| §4.1.13 | an identity-neutral instance step; the atomic per-repo gate cutover | Q10, Q13; CIU-115 |
| §4.1.14 | run-gate-project as a zero-instance project; a Q11 note | Q1, Q11 |
| §4.1.15 | new: skills in the wheel, `ciu skills` | D-647 #6; Q14; CLI-EXT-02 (vbpub@ab52242e8); vbpub@33c0b0c2 |
| §4.3a | B resolved again; D (Q7) and E (Q11) added | memo §3.5; Q7, Q11 |
| §4.4 | V8-2, V8-11, V8-12, V8-15, V8-19, V8-21, V8-24, V8-26, V8-27 rewritten; V8-30..V8-37 added; checkpoints; Phase A | the rows above; memo §3.2, §3.8 |
| §4.5 | A1 `trunk`, the inherit list, `[ciu.instances]`; A2 `share`; A6 `memory_min`; A7 rewritten; B `protected`, `instance_id`, joins; D2 new; H rewritten; I and J | as each row says |
| §4.6, §4.11 | the schema rows; N18 dropped, N22 shipped (R-41), N24–N27 added | memo §2.1 V8-19; run-gate RG-39's status |
| §4.2, §4.3.1, §4.3.2, §4.3.7, §4.3.8, §4.3.10 | inventory K1–K13; the 2026-10-03 decisions; one implementation; locking; the judge | as above |
| §4.7, §4.8 | X96–X112; the revision 4.0 drops | — |
| §4.9 | the open decisions Q7, Q11, Q15, Q16 | D-652 |
| §4.10 | items 5, 8, 14, 18, 19, 22, 23 updated; items 28–35 added | — |
| Appendix R | new: the SPEC-V8 change list; the stale demo files | D-652 "Operator direction" |

**Verification state.** Claims marked "live probe" or "verified" were checked on 2026-10-03 with read-only commands; no gate, container or stack was started.

*RG-55 merge state,* verified against vbpub `main` at vbpub@ab52242e8:
- the contract (frozen 2026-09-12; v1.1 RW-34) and its run-gate v1 consumer (packages P2 and P4: R-43, R-44, RG-57..RG-61) are released in run-gate 23.8.0–23.9.1;
- the producer cgprofile 1.1.0 (package P6: the socket carrier, `watch`, placement under a delegated scope) is merged and unreleased;
- **not merged**: the run-gate v1.1 consumer (P5: carrier selection, `watch`, placement requests) on branch `rg55-p5-post-p6-20261001`, 32 commits ahead (on `main`, `run_gate.py` has no socket or placement code); the P1 mutation-oracle hardening (`rg55-p1-final-20261001`); the SPEC-V8 note `c65ac2daa` (`rg55-p3-spec-v8-docs`);
- RG-56 (admission) is open.

*The memo's two UNVERIFIED claims:*
- The nested-root composition is in `workspace.py` (`root_identity_suffix`, read). Whether any estate monorepo child runs a stack under the composed id was not checked.
- `~/.config/ciu/host.toml` does not exist in v7 (no reference in `ciu/src/ciu/`), so the host config of §4.5 D2 is a new file.

*Derived, not executed:* the RG-73(b) mount collision (the Q7 record, below) follows from git's gitfile semantics and from the rule that a directory cannot be bind-mounted over a regular file.

#### 4.1 amendments (2026-10-03, dstdns D-653)

The operator answered revision 4.0's open questions in a third round (D-653, which also records cgprofile CP-15, vbpub@9b4bf835e, as V8-31's filing). Revision 4.1 applies them as targeted edits.

| passage | change | rests on |
|---|---|---|
| header | revision 4.1; D-653 and the cgprofile sources added | D-653 |
| §4.1.10 (linked worktrees) | Q7 decided: the worktree at its own path plus the git common dir; no baked checkout path or content; `ciu check` stage 12 validates the image contract; the dstdns consequences, with the `ddcli` finding corrected to "dead under the runner's `/tmp` mount" | D-653 Q7 |
| §4.1.10a | the charge is the warm (hot + warm) working set; the manifest's `warm_set_bytes`, its DAMON source (CP-6) and limits (vaddr v1 vs CP-3 paddr, unmapped page cache and tmpfs, unavailable DAMON, thresholds, the combined-series gap); `memory_max` stays the ceiling and the unmeasured charge; host capacity as ciu config with no mdt coupling; the serialization mechanism deferred to Q15; the no-daemon policy (`count` or `unbudgeted`, never refuse by default) | D-653 Q4, Q15, Q16; D-651 Q5; cgprofile `lib/damon.py`, `lib/summary.py` |
| §4.1.9, §4.1.12, §4.3.7 | the admission lock's form left to Q15; `ciu doctor`'s report | D-653 Q15 |
| §4.3a D | decided | D-653 Q7 |
| §4.4 V8-30, V8-31 | the warm charge and the Q15/Q16 wording; V8-31 filed as CP-15, plus a combined warm-set Summary key | D-653 |
| §4.5 A7, D2, I | A7's `env` row; D2 rewritten (capacity in ciu config, the `no_daemon` policy, no lock directory); `CIU_HOST_CONFIG` withdrawn | D-653 Q7, Q15, Q16 |
| §4.3.1 | Q7 decided; Q4 refined; Q15 reopened; Q16 decided | D-653 |
| §4.7 | X101 and X112 resolutions updated; X113, X114 added | D-653 |
| §4.9 | Q7 and Q16 leave the open list; Q15 re-posed with three realizations that keep ciu and mdt uncoupled; Q11 unchanged | D-653 |
| §4.10 | items 19, 29 and 35 rewritten; item 36 added | D-653 |
| Appendix R | S2.7, S15, S16.4, S16.6, S16.9 and S18.2 rows; R.2's compose row | D-653 |

**What was withdrawn from revision 4.0.**
- Q15's recommended option: mdt writing `/etc/ciu/host.toml` and creating `/run/ciu/`, which every container bind-mounts.
- With it, `CIU_HOST_CONFIG`, D2's `lock_dir`, and the "lock coverage" shortcoming.
- Q16's options B (refuse by default) and the reservation files under `/run/ciu/`.
- R-44's `memory_peak_bytes.max` as the admission charge.

**The record Q7 was decided on.** The comparison stays here as rationale, worked against dstdns's runner, whose image baked `WORKDIR /workspaces/dstdns` and `ENV PYTHONPATH=/workspaces/dstdns:/workspaces/dstdns/scripts`. A linked worktree is `/workspaces/dstdns/.worktrees/<w>`, and its `.git` file reads `gitdir: /workspaces/dstdns/.git/worktrees/<w>`.

| | **A (decided): the worktree at its own path; no baked checkout path** | **B: the worktree at the image root (RG-73(b) as filed)** |
|---|---|---|
| mounts for a dstdns worktree | `<w>` at `/workspaces/dstdns/.worktrees/<w>` (in both namespaces if they differ, R-23); the common dir at `/workspaces/dstdns/.git` | `<w>` at `/workspaces/dstdns`; the common dir must go elsewhere, e.g. `/git-common` |
| does git work | yes, natively: the gitfile's absolute path exists in the container | only with overrides. The worktree's own `.git` *file* sits at `/workspaces/dstdns/.git`, where the gitfile expects the common *directory*, and a directory cannot be bind-mounted over a file. So `GIT_DIR=/git-common/worktrees/<w>` and `GIT_COMMON_DIR` must be set (derived, not executed) |
| tools inside the lane | nothing special | every tool that runs git must honour the overrides: assay's repository snapshot (`git worktree add`), `scripts/coverage_gate.py`, the git-backed tests |
| snapshot bookkeeping | `git worktree add` records paths that are real in the devcontainer namespace (R-23) | snapshot paths are container-only (`/workspaces/dstdns/...`) and, seen from the host, name the *primary*; a `git worktree prune` from outside must clean them |
| image change for dstdns | delete the baked `WORKDIR` and `PYTHONPATH`: ciu sets `workdir` to the checkout path, and `env` supplies `PYTHONPATH` with `{worktree}`. Both are overridden at run time anyway, so today they are masked defaults (AGENTS §4.2a #3) | none |
| the primary checkout | identical to a worktree: mounted at its own path | identical: `/workspaces/dstdns` |
| the path a lane sees | `{worktree}`, which differs per worktree; argv must use `{worktree}` (dstdns's lanes already do) | always `/workspaces/dstdns`; absolute paths in tests keep working |
| code paths in ciu | one | one, plus the override set to maintain |
| matches what works today | yes: dstdns's Mode-B compose block (`tools/test-runner/ciu.compose.yml.j2`) is option A, generalized | no estate consumer runs it |

*Decided: A (D-653).* The case for it: it is git-native; the common-dir mount it needs is the one that already works; and the image edit it asks for removes two masked defaults. B leaves images untouched but moves the cost into every tool that runs git inside the lane, and a missed override fails as a false green (git-backed tests skip under exit 0) — the failure dstdns already measured.

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
| X73 | draft.4 S17.4.4 (a receipt is valid when its resolved-file digest equals "the consumer's own render of the provider host") vs S3.7.1/S14.2 (`rendered_at` and `[ciu.host.generated]` are in that file) | canonical receipt subject with plan/release digest and `activation_id`; strict by default, `--allow-assumed` | T2-01 |
| X74 | draft.4 S8.5.5 ("every service healthy" AND "every one-shot exited 0") vs S8.6.3 (an exited container is not Running) | the acceptance partition | T2-02 |
| X75 | draft.4 S2.4.1 `[ciu].secret_lint_allow`, S12.1 `[hooks].env_allow`, S8.5.3 `probe`, S16.2.2's reasons, S17.5's flags, S19's flags, S16.4.5's set vs the closed sets S3.4.7, S6.10, S5.4, S16.8, S18, S15.3 stage 12 | every surface added to its set; a conformance test generated from the definition (S3.8.5) | T2-03 |
| X76 | draft.4 S12.2 ("every listed key with its materialized value") vs S8.7 (materialization is step 3; `pre_secrets` is step 2) | the phase/source matrix | T2-04 |
| X77 | draft.4 S17.3.1 ("every file a hook references") vs hooks being arbitrary programs; S17.3.3 (only release and capsule transfer) vs a manifest that lists images; S17.4.1 (`<digest>`) vs an activation CLI with no digest; `[activate] rollback` vs "runs the previous release's apply" | declared closure, image transport, `candidate`, CIU-owned switch, host rollback withdrawn | T2-05 |
| X78 | draft.4 S7.4.7 (`bind = "*"`) vs S6.3.2 (recognizes only `0.0.0.0`) and an unproved listener address | declared `listen`, canonical address sets, live probe | T2-06 |
| X79 | draft.4 S16.6.1 (lock the evidence directory) vs S16.6.4 (count against the target container), and per-lane output paths shared by concurrent runs | capacity-object lock, exec-target exclusivity, run directories | T2-07 |
| X80 | draft.4 S4.5.3 ("a collision or a moved checkout … `--move`") vs the response's claim that collisions are refused | the owner token; `--move` proves, `adopt --owner` recovers | T2-08 |
| X81 | draft.4 S13.3 ("rounded") vs Docker's integer forward map; "`max` on either side yields `-1`" vs finite swap with unlimited memory | ceiling inverse; direct `memory.swap.max` | T2-09 |
| X82 | draft.4 S2.4.2 (a file-wide configfile exemption) vs S10.2.6 (only the delivered keys are legitimate there) | per-value suppression, counted in the output | T2-10 |
| X83 | handoff note answer B ("relax `location` to a path inside the same git repository") vs V8-S2.2/S16.5.3 (rendered artifacts and the lock live in the stack directory) | `location` stays under the checkout and unshared; `build.context` may leave the stack directory | §4.3.14 |
| X84 | draft.4 S16.2.1 `[testing] inherit` (environments only) vs the monorepo's need to share governance and the judge floor too, and S16.11.1 forbidding `[governance]` in the zero-instance root that would carry it | `[ciu] inherit` with a closed inheritable list; `[governance]` permitted in a zero-instance project | §4.3.14 |
| X85 | draft.5 S17.3.1 (a release per host) vs S17.4.4 ("its release digest equals the consumer's") — hosts run different releases | receipts validated against the activation manifest's entry for the provider | T3-01 |
| X86 | draft.5 S17.3.3 (a verified, renamed, immutable release) vs S14.2.3 (the target regenerates `[ciu.host.generated]` inside it) and S9.4/S10.6/S6.8/S6.10 (records, store, data and state under the checkout) | the state root; `ciu.host.toml`; the realness record in the instance record | T3-02 |
| X87 | draft.5 S17.4.1 (`bootstrap` runs in `current`) vs a first push that creates only `candidate`; rollback swapping pointers before its apply | prepare → apply → health → receipt → switch, both directions; pointers untouched on failure | T3-02 |
| X88 | draft.5 S6.2 (`build.context` inside the checkout) and S1.5 (nearest root) vs proposal §4.10 item 22 (`../../tester-unified` from a child) | reach = the containing worktree; inherited policy flattened into a release | T3-03 |
| X89 | draft.5 S16.3 (`[testing.judge]` forbidden without an assay lane) vs an inherited estate floor a child cannot delete (S3.1.2) | permitted and unused | T3-03 |
| X90 | draft.5 S14.4.7 (a "canonical" stack lock) vs S14.4.3 ("no per-stack lock") and mutators that never took it; S14.3 (lease = mutating class) vs S14.4.8 (`--realization` takes the stack lock instead) | the lock matrix; the realization-only class; lock-free lease records; `CIU_LEASE_FDS` | T3-04 |
| X91 | draft.5 S4.1.2 ("re-stamps `ciu.checkout`") vs Docker's immutable labels; a "copied tree" defended by a token that is copied with the tree | cold move; copies refused; `--fresh` | T3-05 |
| X92 | draft.5 S8.5.2a (`unprobed`), S14.8.1 (`ciu/backup`), S16.6.1/S14.8.1 (`XDG_*`) vs S8.5.4, S18.4, S18.2 | added to their sets; S3.8.6 documentation conformance | T3-06 |
| X93 | draft.5 S6.2 `image` row ("without `build` … pulled") vs S3.4.3/S6.2 `build` row (a shared reference is project-built) | the reference-level image map | T3-07 |
| X94 | draft.5 S16.7.2 ("so that assay finds its own progress") vs assay's resume state under `.assay/mutation-state/` keyed by candidate content and `--progress` being telemetry | `--resume` always, no CIU `--resume`, progress in the run directory (a stated deviation from the run-gate path convention) | T3-08 |
| X95 | enrollment proposal rev 1 (a token-authenticated bootstrap URL, a callback and a cmru backend to deliver the host key and an address) vs its own §3.3 (both facts operator-confirmed anyway) and draft.6 S17.4.1 (`ciu` must already exist on the target for `prepare`) | a two-step verb: the public key in a printed, version-pinned `get.py enroll` one-liner; the fingerprint confirmed by the operator; enrollment includes the ciu install | §4.3.16 |
| X96 | rev 3.4 §4.1.10 / R-01 (run-gate maintained in parallel) vs D-647 #1 (one gate implementation) | run-gate ported and archived; standalone = zero-instance mode in the ciu wheel | §4.3.2, Q1 |
| X97 | draft.7 S4.1.1 (6 hex of SHA-256) vs ciu 7.15.1 (`libraries/worktree` `workspace_id_for_path`: 6 base36 of `sha256 mod 36^6`) | 7.15.1 verbatim, including the nested-root composition | §4.1.4, Q10 |
| X98 | draft.7 S4.1.1/S4.5 (the owner token) vs SPEC-V8 D.5 (too convoluted, and it does not cover the owner's own mistake) | token dropped; `protected` + the `ciu.checkout` label | §4.1.9, Q10 |
| X99 | draft.7 S14.4.1 (lock = the checkout directory) and S14.7 (`ciu.instance.json` registry + `ciu-instances.lock`) vs 7.15.1's root lock and `.workspace-instances/` records shared with cmru, and the library's "no second authority" rule | adopt the shipped lock and records; `ciu.instance.json` becomes a product record | §4.3.7 |
| X100 | draft.7 S14.6 (a `max_concurrent` instance count) vs D-647 #4 (a RAM budget, not a count) | admission from measured footprints | §4.1.10a |
| X101 | draft.7 S16.6.1 (flock and ledger on the slice's cgroup directory) vs the devcontainer's `0::/` cgroup view with no `dev.slice` (live probe 2026-10-03) | slice facts come from the daemon; how decide-and-start is serialized host-wide is Q15 | §4.1.10a, Q15 |
| X102 | memo §3.6 (the daemon is the admission ledger) vs D-651 Q4 (the daemon measures only; ciu decides) | ciu decides under a host lock; reservations are registered as data | §4.1.10a, §4.3.17 |
| X103 | memo §3.6 (the budget READ from each slice's `memory.max`) vs D-652 Q6 (host facts and what may be used in host-scoped ciu config) | a declared budget, checked against the slice's `memory.max` read through the daemon | §4.1.10a |
| X104 | SPEC-V8 D.7 (3) and memo §1/§3.6 (the daemon "root, `cgroupns=host`", a leaf directly under the gates slice) vs RG-55 contract §5/§8.3 and design A3/D-31 (private namespaces; a systemd-delegated scope with a profiler leaf below it) | the shipped daemon | §4.1.10a |
| X105 | draft.7 S13.2.1 (`memory_min` preflight-only, never written) vs CIU-94 (7.12.0: written to the container scope and admitted against the slice floor) | written; a protection, not an admission charge | §4.1.10a, A6 |
| X106 | draft.7 S16.2 (`cgroup_slice` defaults to `governance.cgroup_parent`; the demo's literal `ciu-gate.slice`) vs run-gate R-10 and the dev-gates migration (vbpub@41c1cafb3) | `cgroup_slice_env`, defaulting to `$CGROUP_PARENT_DEV_GATES`, never a literal | A7 |
| X107 | draft.7 S16.7.2 (assay state under `.assay/mutation-state/`, "CIU neither reads nor manages") vs run-gate RG-38/RG-49 (`--state-dir` in the git family's durable root) | `--state-dir` kept, path renamed | §4.1.10 |
| X108 | draft.7 S16.7.2 (default base `merge-base HEAD @{upstream}`) vs run-gate R-35a/CIU-106 (the recorded fork commit first) and RG-74 (the trunk merge) | the ported order | §4.1.10 |
| X109 | draft.7 S16.4 (`host` = subprocess) vs run-gate R-42 (`host` = a container, `bare-host` = subprocess) | open: Q11 | §4.9 |
| X110 | draft.7 S16.8 (PASS 0 / FAIL 1 / ERROR 2 / NOT_RUN 3 / BUDGET 4) vs run-gate R-04/RG-11 (the lane's own status passes through; 2 = refusal; 3 = infrastructure) | open: Q11 | §4.9 |
| X111 | D-647 #3 (vault only, never postgres) vs D-651 Q9 (per-service shareability with tenant namespaces) | the later ruling | §4.1.7a |
| X112 | draft.7 S16.4.3/S16.12 (mount only the checkout) vs a linked worktree's gitfile naming a path in the primary's `.git` (dstdns: exit 128, GIT_FAILED) | the common-dir mount is derived and always made; the worktree is mounted at its own path (Q7 → A, D-653) | §4.1.10, §4.9 |
| X113 | rev 4.0 §4.1.10a and R-44 (charge = `memory_peak_bytes`, a peak that counts cold pages) vs D-653 Q4 (schedule on the warm working set) | the manifest's `warm_set_bytes` from DAMON; the peak only as a fallback over-charge | §4.1.10a |
| X114 | rev 4.0 Q15 A (mdt writes `/etc/ciu/host.toml` and `/run/ciu/`, bind-mounted) vs D-653 (no hard coupling between ciu and mdt; host facts may be ciu config) | withdrawn; host capacity is ciu config; the serialization mechanism is re-posed as Q15 | §4.9 |

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
| freezing run-gate | rev 2.1 V8-18 | run-gate stayed standalone (R-01); since revision 4.0 it is ported into ciu and archived at 8.0.0, not frozen in place |
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
| `[testing] inherit` (environments only) | rev 3.1 | one mechanism for every shared policy table: `[ciu] inherit` (§4.3.14) |
| `ciu testing flatten` | rev 3.1 | `ciu show effective` covers every inherited table, not only `[testing]` |
| `ciu up --require-receipts` | rev 3.1 | strict is the default; `--allow-assumed` is the explicit escape (T2-01) |
| the resolved-file digest as receipt validity | rev 3.1 | unreproducible across hosts and renders; the receipt subject (T2-01) |
| `[hosts.<h>.activate] rollback` | rev 3.1 | rollback is CIU's state machine, not a host command; `ciu down` was never a rollback (T2-05) |
| socket claim `bind = "*"` | rev 3.1 | outside the overlap relation; the declared `listen` address is inside it (T2-06) |
| `evidence_dir/.admitted/` reservations; per-lane `stdout.log`/`verdict.json`/`progress.jsonl` | rev 3.1 | keyed by the wrong object and shared by concurrent runs; a ledger keyed by the cgroup path and per-run directories (T2-07) |
| nearest rounding in the `cpu_shares` inverse | rev 3.1 | undershoots 4999 weights; the ceiling round-trips all 10 000 (T2-09) |
| a meta-root `ciu.global.defaults.toml.j2` with `autostart` (nyxloom design prompt, 2026-09-03) | held prompt | a root file owning every nested project's deploy set conflicts with nearest-root resolution and P3; bundles already say what `up` brings up; `[ciu] inherit` shares policy and a two-file stack shares the tester image (§4.3.14) |
| a shared stack `location` across projects | handoff note 2026-09-03 | the stack directory holds rendered artifacts and is the Realization's lock key; sharing it aliases instances (X83) |
| a name-keyed lease (`ciu lease <name>`) | 2026-09-03 | a lease must survive renames and exist before a render; the two directories do (V8-S14.4.7) |
| `ciu gate --resume` | rev 3.2 | assay resumes by itself from content-keyed state; a CIU run directory has no part in it (T3-08) |
| `ciu instance adopt --owner`; the live `--move` "re-stamp"; the "copied tree" guarantee | rev 3.2 | labels are immutable and a copied token proves nothing; moves are cold, copies are refused (T3-05) |
| `[ciu.host.generated]` inside the generated file; the realness record inside it | rev 3.x | host facts and records are per state root, not per release; `ciu.host.toml` and the instance record (T3-02) |
| `plan_digest`; `--activation ID` on `up`; comparing a receipt with the consumer's own release | rev 3.2 | the activation manifest with per-host expected entries (T3-01) |
| the per-service "without `build` = pulled" wording | rev 3.1 | the reference-level image map (T3-07) |
| the `.assay/progress-<lane>.jsonl` convention for `ciu gate` | run-gate estate directive | CIU creates no hidden directory and two runs never share a file; the LaneResult names the path (T3-08) |
| the host `bootstrap` command in `current` as the initializer of a target | rev 3.1 | CIU's own `prepare` in the target release; `bootstrap` stays as an optional prerequisite hook in `<bundle_dir>` (T3-02) |
| the enrollment token, bootstrap URL, callback and tls-edge endpoint (enrollment proposal rev 1, D-097) | CIU-93 rev 1 | they delivered two facts the operator confirms anyway; a printed one-liner with the public key and a console fingerprint do the same with no infrastructure (§4.3.16) |
| the cmru self-hosted download backend as an enrollment prerequisite | CIU-93 rev 1 §6 | `--installer-url` and a mirror cover GitHub-independence for the script; a self-hosted wheel backend stays an optional cmru item (§4.10 item 27) |
| `--host-hint`, polling for a callback, a separate `enroll --check` verb | CIU-93 rev 1 §7 | the operator supplies the address in step 2; nothing is waited for |
| the owner token `owner_id`, the `ciu.owner` label, adoption by token | rev 3.2–3.4 | Q10: the 7.15 id with allocation-time collision refusal, `ciu.checkout`, and `protected` |
| `[ciu.instances] max_concurrent`, `CIU_MAX_CONCURRENT_INSTANCES` | rev 3.0–3.4 | D-647 #4: admission by RAM |
| the checkout-directory instance lock; `ciu.instance.json` as registry; `ciu-instances.lock` | rev 3.0–3.4 | the shipped 7.15 lock and records (memo §3.5) |
| the per-uid admission ledger on the slice's cgroup directory; `XDG_RUNTIME_DIR` as its root | rev 3.2–3.4 | invisible from the devcontainer; ciu decides from daemon data (Q4) |
| `ciu instance add --join` and per-instance ad-hoc joins | rev 3.0–3.4 | committed presets only (Q9) |
| `ciu instance exec --env` | rev 3.0–3.4 | `ciu gate exec` (RG-70) and `ciu exec` (CIU-118) |
| run-gate as a parallel standalone gate; V8-19's alignment; N18; V8-24 as a standing parity contract | rev 3.0–3.4 | one implementation (D-647 #1); V8-24 survives as the port's parity tests |
| the judge floor as the only judge key; the judge baked into the tester image | rev 3.0–3.4, the demo | one `[testing.judge]` with a pin or a source (Q12) |
| `testing.cgroup_slice` defaulting to `governance.cgroup_parent` | rev 3.1–3.4 | R-10 (`$CGROUP_PARENT_DEV_GATES`) |
| "vault only, never postgres" as dstdns's join rule | D-647 #3 | revised by D-651 Q9 |

## 4.9 Open product decisions

Two questions are open (revision 4.1): Q11 from the reconciliation interview, which the controller is still discussing with the operator, and Q15, reopened by D-653. Q7 and Q16 were decided in round 3 (D-653) and moved into Part 1 (§4.1.10, §4.1.10a) and Appendix R; the record Q7 was decided on is in §4.3.17. Each open question lists the options with the recommendation first. The naming decisions taken without a fork (§4.3.9) stay listed so they can be overturned cheaply.

### Q11 — Whose CLI vocabulary and exit-code contract does the merged gate keep?

*Question for the operator:* at the 8.0.0 cutover, does `ciu gate` keep v8's draft contract — explicit environment modes and a closed exit table (A) — or run-gate's — lane-exit passthrough and a built-in `host` container (B) — or v8's names with run-gate's exit codes (C)?

| | **A (recommended): v8's contract** | **B: run-gate's contract** | **C: v8 names, run-gate exit codes** |
|---|---|---|---|
| environment modes | `ephemeral`, `exec`, `host` (= subprocess); every environment declared; no implicit names | a built-in `host` = a container with the default image (R-42); `bare-host` = subprocess; an undeclared `host` works | as A |
| exit codes | PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET_EXCEEDED 4; the lane's own exit code is kept in the LaneResult | the lane's own status passes through unchanged; 2 = configuration/refusal; 3 = execution-infrastructure failure (R-04, RG-11) | as B |
| a lane that did not run (a precondition) | 3 (`NOT_RUN`), distinct from FAIL | 2 (a refusal) | 2 |
| a test suite that collected nothing (pytest's 5) | 1 (FAIL); the 5 is kept in the LaneResult | 5 | 5 |
| relation to ciu's general table (S18.1: 2 config/usage, 3 environment bootstrap) | 3 means NOT_RUN in the gate, but bootstrap failure elsewhere | 2 and 3 mean nearly what they mean in S18.1 | as B |
| `--list` | three tab-separated columns, frozen (both sides agree) | same | same |
| who changes at cutover | every pointer and wrapper that names `host`/`bare-host` or branches on 2 or 3. `ciu migrate --gate` rewrites the declarations and the nyxloom `[gates.*]` pointers mechanically | nobody's exit handling; v8's own text (S16.4's `host`, S16.8) is rewritten, and `host` keeps meaning a container | the declarations |
| what scripts can branch on | NOT_RUN vs FAIL vs BUDGET, in the exit code | only the LaneResult distinguishes them | only the LaneResult |

*Today's consumers, under either contract:* nyxloom records the exit code and treats any non-zero code as failure (`gate_runner.py`, `effects_gates.py`); dstdns's `gate-slot.sh` reserves 75 and passes everything else through (Phase A deletes it); cmru and the 18 symlink consumers run their lanes under run-gate's codes.

*Recommendation: A.* It gives one closed vocabulary that a caller can branch on without parsing JSON, and the rewrite it costs is mechanical and happens once, inside the atomic per-repo cutover (Q13). Its one real cost is that `3` means NOT_RUN in the gate and environment-bootstrap failure in the rest of ciu. That is why the gate keeps its own table (S16.8), and `ciu gate doctor` reports bootstrap problems before a lane runs. B is the zero-migration option, but it keeps `host` meaning a container, which every v8 text and the minimal project contradict, and its exit code carries whatever the lane's tool happens to return. C mixes the two.

### Q15 — Where do host capacity facts live, and what serializes decide-and-start host-wide, with no hard coupling between ciu and mdt? (reopened in rev 4.1, D-653)

*Question for the operator:* revision 4.0 assumed that mdt writes `/etc/ciu/host.toml` and creates `/run/ciu/` for ciu to bind-mount. That assumption is withdrawn: nothing decided that host facts live in host files outside the environment, and ciu and mdt must each work without the other. Which realization should carry ciu's host capacity facts and the host-wide serialization that D-651 Q4 needs?

*Common to every option:*
- **What the facts are.** Per tier: the slice and the usable memory. Plus the memory-PSI threshold and the no-daemon policy (Q16).
- **Where the facts live.** In ciu's own configuration: the local host's row of the host inventory (`[hosts.<h>.capacity]` in `ciu.hosts.toml`, or the user-global `~/.config/ciu/hosts.toml`, §4.5 D/D2).
- **What is read, not declared.** RAM comes from the daemon's `ctl host` or `/proc/meminfo`, and a slice's `memory.max` from the daemon when it runs.
- **How slices are named.** A slice is named in ciu config, or through an environment variable ciu config names. mdt's `$CGROUP_PARENT_DEV_*` variables can feed that, and their absence on a host without mdt is not an error once the config names the slice.
- **RG-55's other route.** It lets ciu do more than read slices: the daemon can place lanes into a delegated scope under any verified slice, so the slice layout need not come from mdt at all.

| | **A (recommended): serialization through the Docker daemon** | **B: a declared per-environment share, with a local lock** | **C: the cgprofile daemon as the coordination point** |
|---|---|---|---|
| how decide-and-start is serialized | No lock file. Every admitted start is first a Docker object carrying reservation labels (`ciu.reservation.tier`, `.bytes`, `.source`, `.owner`): the lane container in its `created` state (the detached state machine already creates before it starts, R-15), the stack's containers from `compose create`, a labelled volume for a host-mode lane. ciu then lists every reservation of the tier on the Docker daemon, orders them by Docker's creation time and id, and starts its own only if the cumulative charge up to and including it fits. Otherwise it waits in place, keeping its position, and rechecks. Every ciu that agrees on the order makes the same decision | each environment (devcontainer, CI runner, host shell) declares the share of the host it may use, and `flock`s a file under its own runtime directory (`$XDG_RUNTIME_DIR/ciu/` or the state root) | an atomic compare-and-reserve verb on the daemon: ciu sends its charge and its tier budget; the daemon records the reservation only if the sum fits (CP-15 extended) |
| where the lock lives | nowhere: the Docker daemon every container-starting ciu already shares is the medium, across users and devcontainers | inside the environment; it serializes only that environment's own ciu runs | inside the daemon |
| works without mdt | yes | yes | yes, but not without the daemon |
| works without the cgprofile daemon | yes (charges from the manifest; Q16's policy) | yes | no: it falls back to B or to Q16's `unbudgeted` |
| correct across environments on one host | yes | only if the declared shares add up to at most the host's usable memory, which nothing checks | yes, while the daemon runs |
| relation to D-651 Q4 | reservations are data on Docker objects. The daemon still measures only, and `ctl reserve` (CP-15) becomes optional, used only to mirror reservations for observability. This replaces the D-651 controller inference "each start registers its reservation with the daemon" | as A, locally | closest to that inference, but the daemon then arbitrates races; it decides nothing about policy, yet it becomes load-bearing for every start |
| failure and recovery | a crashed client leaves a `created` container or volume holding its reservation until `ciu instance reap` or the gate's re-attach collects it; the owner tuple in the labels lets any ciu prove the owner gone | a dead holder's `flock` is released by the kernel | the daemon must expire reservations whose owner is gone; a daemon restart loses its in-memory state unless it persists it |
| cost to build | a two-phase protocol (create-with-labels, ordered check, start-or-wait) to specify and test, including clock-free ordering on one Docker daemon | smallest | a contract amendment plus a stateful daemon feature |
| what it cannot cover | work started outside ciu (a hand-run `docker run`); a host with several Docker daemons | other environments' work entirely | anything while the daemon is down |

*Recommendation: A.* It meets the no-coupling constraint with no shared file, no mount and no extra service. The coordination medium is the Docker daemon, which every ciu that can start a container already depends on, and Docker's own object listing is the cross-user, cross-devcontainer view a lock file could only give through a mount. Docker has no transactions; ordering by creation is what replaces a lock. The price is a protocol rather than a `flock`, and accepting that reservations are recorded on Docker objects rather than registered with the daemon. B is the right degenerate case for a host with one environment, and A reduces to it there. C keeps D-651 Q4's inference literally, but makes admission depend on a service the operator may choose not to run (Q16).

## 4.10 Known gaps in this proposal

1. **Routes for multi-endpoint providers and replicas** (rev 2.1 gap 1) — unchanged: a stateful replicated provider needs a per-replica endpoint naming rule.
2. **`seeded` for stateful stacks** (gap 2) — the preparation workflow is a consumer concern; the hook that writes prepared credentials to Vault is named, not written.
3. **Lock semantics on network filesystems** — the directory lock is refused by name where `flock` is unsupported (V8-S14.4.6); no fallback is offered.
4. **Certificate issuance** (gap 4a) — delegated to the `pki` hook; the contract of `pki/<network>/<consumer>/{cert,key,ca}` is not specified.
5. **cgroup slices inside the devcontainer** (gap 5) — answered by the live probe (2026-10-03): the slice is invisible from the devcontainer (`/proc/self/cgroup` = `0::/`, no `dev.slice`). Slice facts come from the profiler daemon (§4.1.10a).
6. **Binding-carried credentials** (§4.3a C) — deferred; if adopted, a binding's `secrets = { password = "<provider secret key>" }` would deliver the provider's published secret under the consumer's prefix.
7. **`ciu.hookkit` argument shapes** — function names and contracts are specified (V8-S12.5); signatures are the implementer's.
8. **run-gate alignment** — moot: there is one implementation (D-647 #1). What remains is the port's parity window (V8-24, V8-36).
9. **Migration effort** (gap 9) — `ciu migrate` is specified mechanically (V8-App A); the expansion of Jinja control flow in declarations is best-effort and reports residues; dstdns's thirteen hooks are hand work.
10. **Scenario coverage** (gap 10) — not walked: multi-instance deployments of different projects sharing one host's mesh; Docker Desktop path semantics; a project that is inherited from by several projects at different depths (`[ciu] inherit` is recursive and cycle-checked since rev 3.1/3.2, but no monorepo of that shape has been rendered).
11. **`allow_from` enforcement** (gap 11) — declarative; no verification beyond the consumer's own tests.
12. **Provenance as adjudicated evidence** (gap 12) — LaneResults now carry per-service provenance; the assay-side attested-evidence contract (B004) is still assay's.
13. **The demo's hook scripts and config-file templates** are referenced by name, not written; the demo shows the declarations and compose templates only.
14. **State posture costs** (rev 3.1, operator decision): `git clean -x` still destroys the store and the instance record; a moved checkout is a new id; a path-hash collision is refused at allocation, not avoided. Allocation and locks live in the git family's shared records, which `git clean -x` does not touch. Reopen the registry alternative (third-party A) if any of these bites in practice.
15. **Judge capability record** — CIU checks a version floor against its own minimum (4.1.0); an `assay --capabilities` record would let it check features instead (needs assay).
16. **Binding directories** (third-party C) as a third delivery, and **signed receipts** (D) if activation ever leaves the SSH channel — recorded, not adopted.
17. **Executable conformance of the demo** — the resolved example is derived by rule but not yet produced by a program; V8-13 must make `ciu check --graph` reproduce it and fail when it drifts.
18. **Exec parallelism** — the real consumer arrived (dstdns D-619/D-636, RG-67), and the answer is unchanged: an exec target is used by one lane at a time (V8-S16.5.7). Hermetic lanes move to `ephemeral` environments, and RG-67's exec count is a v7-only bridge.
19. **Admission across users** — closed for every ciu that takes part in the host-wide serialization Q15 chooses (§4.1.10a); under Q15 A that is every ciu sharing the host's Docker daemon, across users and devcontainers.
20. **Hand-started multi-host runs** (rev 3.2/3.3) — need the activation manifest from `ciu activate plan` on the sender and byte-identical files on every host; the supported flow is a release plus `ciu activate apply`.
21. **Image archives** (rev 3.2) — `--images archive` ships whole image tarballs per push; layer-level reuse needs a registry, which is the recommended mode for anything beyond a first deployment.
22. **The monorepo's consumer work** (rev 3.2, §4.3.14) — a vbpub-root zero-instance `ciu.toml` (replacing `run-gate.root.toml`) carrying `[governance]`, the tester environment and the judge declaration; `[ciu] inherit = "../ciu.toml"` in every subproject; a `tester/` two-file stack with `build.context = "../../tester-unified"` in the projects that want a persistent exec-mode tester. All of it lands after V8-1/V8-27 ship; nothing in v7 gains a mechanism for it — the v7 stop-gap is one copied `[governance]` table per root.
23. **Hidden `.assay/` in the judged tree** — resume state and progress no longer live there: run-gate's `--state-dir` (RG-38, shipped) moves resume state to the git family's durable root, and progress goes to the run directory (§4.1.10).
24. **Named volumes and moves** (rev 3.3) — a cold move keeps host directories (`ciu-data/` is in the state root and moves with the checkout) but not named volumes, which are per instance id; a project whose named-volume data must survive a rename migrates it by hand before `ciu clean`.
25. **The monorepo fixture is not yet executable** (rev 3.3) — `examples/monorepo/` is checked by hand like the rest of the demo until V8-28.
26. **A failed apply is not compensated** (rev 3.3) — CIU reports and leaves the pointers unchanged; the operator re-applies `current` or rolls back. Automatic compensation would need a definition of "the previous runtime state" that a compose project per instance does not have.
27. **GitHub-independence of the installer** (rev 3.4) — `ciu host enroll` prints a release-asset URL; an estate whose targets cannot reach GitHub serves the same `get.py` from its own mirror through `--installer-url`, but the *wheel* download inside `get.py install` still uses the GitHub Releases backend. A self-hosted backend is an optional cmru item, not a prerequisite (D-097's wish, deferred again with a reason).
28. **Round 4 is still unfolded** — T4-01..T4-10 (`CIU-V8-THIRD-PARTY-REVIEW-ROUND4-2026-09-03.md`: six blockers, four majors) need their own fold. Revision 4.0 answers T4-01 for the instance lock (the state-root key, §4.1.9) and the run-record half of T4-09 (the owner tuple and completion marker in `run.json`). The rest are open, including `CIU_LEASE_FDS` across sudo, ssh and `docker exec` (T4-09's other half) and the sentinel bind (T4-03).
29. **The reservation verb** — contract 1 has none (§4.1.10a). Filed as cgprofile CP-15 (vbpub@9b4bf835e); it is required only if Q15 chooses the daemon as the coordination point (option C).
30. **The profiling token's name** — `RUN_GATE_PROFILE_SESSION` survives in ciu until the contract renames it (V8-31).
31. **The v1.1 consumer is unmerged** — run-gate's P5 (socket carrier, `watch`, placement) is on an unmerged branch. The port takes it from run-gate once it merges; otherwise the ciu8 gate starts with the v1 client.
32. **dstdns's shareability analysis** — per service, from measured footprints and the namespace each service can provide (D-651 Q9 follow-up). It replaces the vault-only declaration before dstdns writes its preset.
33. **`@{upstream}` in the base order** — kept for port parity (§4.3.17). The memo's case for dropping it (the stale-origin hazard, RG-51) is a run-gate change first.
34. **Tenant provisioning** — the provider hook that creates and removes a joiner's namespace (`pg-database`, `vault-prefix`, …) is named, not specified. It needs a hookkit entry contract like V8-S12.5's.
35. **The warm figure** — the charge is the p90 of a run's per-sample `hot + warm` bytes, and the Summary does not yet report that sum (§4.1.10a Shortcomings, V8-31). Whether p90 over retained runs, or a median plus a margin, packs the host better is a question to measure once the manifest has data (RG-56's "measure first, decide later").
36. **dstdns test-runner image** — the baked `WORKDIR`/`PYTHONPATH` must go (Q7), and the editable `ddcli` install at `/tmp/ddcli-build` is dead code under the runner's `/tmp` bind-mount (carve TEST-RUNNER-DDCLI-INSTALL, D-653). Both are dstdns work, not ciu's.


---

## Appendix R — What revision 4.0 changes in `SPEC-V8.md` (draft.7 → draft.8)

`SPEC-V8.md` is not edited until the operator signs off revision 4.1 (D-652, D-653). This is the change list for draft.8, one row per rule. "Rests on" names the decision record, the reconciliation question as answered in D-651/D-652, the memo row, or the shipped commit. A row marked *(Q11)* or *(Q15)* waits for that open decision (§4.9).

### R.1 Rule changes

| rule | change | rests on |
|---|---|---|
| header, posture | one gate implementation; run-gate archived at 8.0.0; this draft follows proposal rev 4.0 | D-647 #1; Q1, Q2 |
| S1.5 | configuration is resolved from the selected checkout (`--worktree`), never from the CWD; carry run-gate RG-47/RG-65's oracle | memo §2.4 S16.12; RG-47, RG-65 |
| S2.6 | the state root also keys the instance lock where no git family exists (a release on a target) | memo §3.5; T4-01 |
| S2.6 / new S2.7 | host capacity as ciu configuration: per tier the slice (named, or through a named variable) and the usable share; the PSI threshold; the no-daemon policy `no_daemon = "count" \| "unbudgeted"` with per-tier counts; readable ceilings checked; no dependency on mdt; where it lives and how decide-and-start is serialized host-wide *(Q15)* | D-652 Q6; D-653 Q15, Q16 |
| S3.1.5, S3.4.7 | the inheritable list: `testing.cgroup_slice` → `testing.cgroup_slice_env`; add `testing.profile` | R-10; vbpub@41c1cafb3 |
| S3.4 (`[project]`) | add `trunk` | RG-74(a) |
| S4.1.1 | the 7.15.1 derivation verbatim (`workspace_id_for_path`: base36, lexical path), the nested-root composition, allocation-time collision refusal; drop `owner_id`; "not a consumer contract" | Q10; vbpub@d1eb98770; CIU-119 (1)(2) |
| S4.1.2 | cold `--move`/`--fresh` without the token steps; repair-in-place of an outdated or mismatching generated file with a WARN naming old and new id (a corrupt current-schema file still refuses) | Q10; CIU-115 (vbpub@e9eb0aac5) |
| S4.1.4 | unchanged in this list; T4-03 (the sentinel bind) is still to fold | memo §2.2 |
| S4.4 | identities as data are also served by `ciu resolve --json` | CIU-118 |
| S4.5 | delete `ciu.owner` and the token adoption and refusal rules (S4.5.3); keep the `ciu.checkout` ownership check before a delete; add the instance's `protected` flag | Q10; SPEC-V8 D.5; CIU-105 (vbpub@98957a129) |
| S5.2 | `[service.<n>] share = { tenant = … }` with the closed tenant vocabulary | Q9 |
| S6.2, S17.6.1 | a linked worktree's project-built tags are `<declared>-<instance_id>`; `ciu build` refuses to overwrite a tag the primary names; the checkout and common-dir mounts are injected into an `exec_in` service of a linked-worktree instance | CIU-117; memo §3.4 |
| S9.5 | joins only through `[ciu.instances.join_presets.<p>]` (`reference`, `services`) and `default_join_preset`; `ciu instance init --join-preset` / `--no-join`; remove `ciu instance add --join`; tenant namespaces derived from the joiner's id, created and removed by the provider's hook; stage-12 checks | Q9; CIU-116 |
| S13.2.1 | `memory_min` is written to the container's scope and admitted against the slice floor (a protection); the effective `memory_max` is a container's admission charge until measured | CIU-94 (vbpub@a4f5aa94); Q5 |
| S14.2 | the generated file holds `instance_id` and build facts only; `protected` lives in the instance file | Q10 |
| S14.4.1 | the instance lock = 7.15's root-lock file in `<git-common-dir>/.workspace-instances/` (the state root where no git family exists); the family lock = `libraries/worktree`'s | memo §3.5; `workspace.py` `root_lock` |
| S14.4.3, S14.4.9 | the lock order adds the family lock (allocation only) and, last, the host admission lock | memo §3.5; D-651 Q4 |
| S14.4.7–S14.4.8 | canonical keys = the root-lock file + the stack directory; T4-09's `CIU_LEASE_FDS` across sudo/ssh/`docker exec` is still to fold | memo §2.2; T4-09 |
| S14.6 | delete `max_concurrent`; keep `lease_ttl_hours`; add `join_presets` and `default_join_preset` | D-647 #4; Q9 |
| S14.6.3 | delete `ciu instance exec --env`; add `ciu gate exec` (S16.10) and `ciu exec`/`ciu resolve` (S18) | RG-70; CIU-118 |
| S14.7 | the registry is `libraries/worktree`'s `.workspace-instances/` records, shared with cmru; `ciu.instance.json` is ciu's product record (realness records), not a registry; delete `ciu-instances.lock`; the record carries the fork commit | memo §2.2 S14.7; vbpub@b9cad87c3; CIU-106 (vbpub@d683f6085) |
| S16.1 | the gate is run-gate rev 46 + Phase A, ported; S16.12's "by reference" list is replaced by the port map | D-647 #1; Q2; memo §3.1–§3.2 |
| S16.2 | `cgroup_slice` / `cgroup_slice_env` per R-10 (declared > variable > `$CGROUP_PARENT_DEV_GATES`, never a literal); add `[testing.profile]` and `[testing.footprint]` | R-10, R-43; vbpub@41c1cafb3 |
| S16.3 | `[testing.judge]`: exactly one of `command` + `sha256` or `source`, plus `version`; `import = { environment, lanes }`; explicit lanes override by name | Q12; RG-76; run-gate R-08 rev 11 |
| S16.4 | environment `env` with `{worktree}`; `services.<s>` lane services; the derived common-dir mount for linked worktrees; `workdir` defaults to the checkout path; the worktree mounted at its own path; an image may bake no checkout path or content, validated by `ciu check` stage 12 (Q7 → A); mode names and built-ins *(Q11)* | RG-73, RG-75; memo §3.4; D-653 Q7; Q11 |
| S16.4.5 | the available environment includes lane-service `expose_env` | RG-75 |
| S16.5 | add `services`, `extra_args`, `stall_timeout`, `profile`; `memory_max` stays mandatory; `budget` is a ceiling clocked after admission and lock wait | RG-75, RG-71, R-40c/f, R-43; RG-63 |
| S16.5.4 | a sequence resolves the comparison base once and passes it to every `base_source = "request"` member | RG-74(b) |
| S16.6 | rewrite: the roles (the daemon measures; the manifest; host capacity; ciu decides); the charge rule (the warm working set — p90 of DAMON `hot + warm`; else the measured peak; else `memory_max`); host-wide serialization of decide-and-start and reservations as data *(Q15)*; `--admission-wait`; `--override-admission`; `NOT_RUN/no-headroom`; placement of exec and host lanes; the no-daemon policy (`count` or `unbudgeted`, disclosed; never refuse by default); fold D.6 and D.7 (2)–(3) | D-651 Q4, Q5; D-652 Q6; D-653 Q4, Q15, Q16; RG-55 contract 1 + v1.1 |
| S16.6.4 | exec caps are requested-only unless the lane is placed | contract v1.1 §8.3 |
| S16.7.2 | `--state-dir <git-family state root>/ciu-gate-state/<path>/`; `--progress <run_dir>/progress.jsonl`; the base order `--base` > the fork commit (still an ancestor) > the trunk merge's `HEAD^1` > `merge-base HEAD @{upstream}` > refuse; R-35b's charset; `--reuse-from`/`--rejudge` passthrough | RG-38/RG-49; R-35, R-35a, R-35b; RG-74(a); RG-66 |
| S16.8 | the exit table *(Q11)* | Q11 |
| S16.9 | the LaneResult gains `resources_measured`, `liveness`, `placement`, `admission` and the failure digest; `run.json` gains the owner tuple and a completion marker; re-attach and collection (R-39) | SPEC-V8 D.6 (2), D.7 (4); T4-09; R-39; RG-72 |
| S16.9 (footprint manifest) | the manifest records `warm_set_bytes` (p90 over retained runs of each run's p90 `hot + warm`), the DAMON thresholds and its `source` (`damon` / `damon-class-sum`), beside R-44's peak fields, which become informational | D-653 Q4; CP-6; contract §3 `damon` |
| S15 (stage 12) | the image contract check: a gate environment's image may bake no checkout path (`WorkingDir`, `Env`); a lint over the declared Dockerfile for baked checkout content | D-653 Q7 |
| S16.9.2 | `--list` = three tab-separated columns, frozen; `--list --json` carries the last outcome, median duration and median peak | run-gate R-01 |
| S16.10 | verbs `ciu gate [lanes…]`, `history`, `footprint`, `doctor`, `exec`; flags `--fresh`, `--admission-wait`, `--override-admission`, `-- extra-args` | memo §3.3 |
| S16.11 | zero-instance mode is how a gate-only project uses the gate; hermetic lanes in an instance-bearing project need no init (identity and image references derived read-only; nothing written; nothing started) | Q1; Q8 |
| S16.12 | replace "rules lifted by reference" with the port map: R-39, R-40, R-41, R-43, R-44, R-30a–c, R-34, R-35a/b, R-38, R-08a, RG-44, RG-47/RG-65 | memo §2.4 |
| S18 | add `ciu resolve`, `ciu exec`, `ciu footprint`, `ciu clean --identity`, `ciu skills install\|list\|check\|uninstall`, `ciu migrate --gate`; remove `instance add --join` and `instance exec --env`; add the estate CLI-compatibility surfaces; fold T4-10 | CIU-118, CIU-119 (3), Q13, Q14; vbpub@05f373a40, vbpub@aa0e69fad |
| S18.1 | exit 4 = contention: a lock, or an admission refusal | D-651 Q4 |
| S18.2 | add `CGROUP_PARENT_DEV_GATES` and the test-only `CIU_GATE_*_ROOT`; ciu sets `RUN_GATE_PROFILE_SESSION`; remove `CIU_MAX_CONCURRENT_INSTANCES`, `CIU_GATE_EXTRA_MOUNTS`, `CIU_GATE_MOUNT_ALIAS` and `XDG_RUNTIME_DIR` | proposal §4.5 I |
| Appendix A | `ciu migrate --gate`: per repo, atomic, identity-neutral; the conversions of proposal §4.1.13 step 7; a residue report; history restarts | Q13; memo §3.8 Phase C |
| Appendix B | instance ids `hox0ju` (examples, not literals); the demo's `[testing]` per R.2 | CIU-119 (4) |
| Appendix D | close D.5 (owner token dropped), D.6 (RG-55 integrated, proposal §4.1.10a) and D.7 (liveness and placement per the shipped daemon, D-29..D-32); add D.8 (the round-4 rule map, once folded) and D.9 (this reconciliation) | Q10; D-651 Q4; memo §7 |

### R.2 Demo files revision 4.0 makes stale

`v8-dstdns-demo/` is out of scope for this revision and is not edited. These files no longer match it:

| file | what is stale |
|---|---|
| `ciu.toml` | `[testing] cgroup_slice = "ciu-gate.slice"` (a literal; → `cgroup_slice_env`); `[testing.judge]` as a floor only; the hermetic lanes (`unit`, `ui-unit`, the assay R0–R2 lanes) in the exec `tester` environment (→ an ephemeral environment); `schema` through `scripts/schema-gate.sh` in exec (→ a lane service); `[testing.environments.host]` *(Q11)*; `[ciu.instances] max_concurrent = 3` |
| `ciu.instance.generated.toml` | `98535c`; `owner_id` |
| `ciu.host.toml` | `98535c` |
| `ciu.instance.toml` | per-instance joins (→ a committed preset) |
| `examples/ciu.instance.joined.toml` | ad-hoc joins of vault, consul, redis, `main_db`, object_store, app_schema, idp, tracing and otel (→ one `join_presets` entry naming shareable services only) |
| `examples/ciu.instance.generated.joined.toml` | `owner_id` |
| `examples/ciu.resolved.toml.example` | `98535c`; the owner token |
| `tools/test-runner/ciu.stack.toml` | the judge baked into the image (the `ASSAY_FLOOR` build arg); the mount proof "at /workspace" |
| `tools/test-runner/ciu.compose.yml.j2` | the checkout mount (→ the worktree at its own path plus the common dir, Q7 → A) |
| `examples/minimal/ciu.toml` | `environment = "host"` as a built-in *(Q11)* |
| `examples/monorepo/ciu.toml` | `cgroup_slice = "dev-background.slice"` (a literal, and the stack tier); the judge as a floor only; `forward_env = ["CGROUP_PARENT_DEV_BACKGROUND"]` |
| `assay.toml` | `scripts/schema-gate-lane.sh` (→ the `schema` lane's service) |
| `README.md` | `98535c`; the join walkthrough |
