# CIU v8 design set — adversarial review, 2026-09-02

**Reviewed:** `CIU-V8-TESTING-GATE-PROPOSAL.md` rev 2.1, `SPEC-V8.md` 8.0.0-draft.2, `V8-REALIZATION-GRAPH.md`, `v8-dstdns-demo/` (65 files), against `docs/SPEC.md` 5.0.0 (the v7 specification, read in full), `run-gate-project/SPEC.md` (R-01..R-38), every `run-gate.toml` in the estate, `KNOWN_ISSUES_TODO_BACKLOG.md` (status table; CIU-72/73 in full), `CHANGES.md` (7.9.0–7.10.1), and a source-level dependency map of `ciu`, `assay`, `run-gate`, `cmru`, `nyxloom` and `/workspaces/dstdns` (§2).
**Reviewer:** fresh session, no memory of the design sessions that produced rev 2.x; the operator was interviewed live on every fork (§4).
**Outcome:** the entity model (LogicalService / Realization / RealizedService / Host / Network / Layout, derived identity, derived waves, realness record, one secrets store, no `.ciu/`) holds. Five structural findings and roughly seventy local ones led — with the operator's decisions in §4 — to a **fresh proposal (rev 3.0) and a fresh specification (8.0.0-draft.3)** rather than in-place edits: the schema surface changes in a way that touches most sections (declarations become plain data, consumers declare *bindings* instead of reading *routes*, secrets become structured, the contract is derived, the instance lock moves, the gate gains a zero-instance mode). The graph note is updated in place (rev 2) and the demo is rewritten to the new notation.

**How to read this document.** §1 is the verdict in one page. §2 records the dependency facts the review was asked to respect ("usable standalone, no hard dependencies"). §3 is the finding list — every finding carries *where*, *what is wrong*, *why it matters* and *what was done*, so the reasoning can be followed without the reviewer. §4 is the interview record, including the two questions the operator asked back and how they were answered. §5 maps rev 2.1 → rev 3.0 / draft.2 → draft.3 section by section. §6 lists what was deliberately **not** changed. §7 is what remains open after this round.

Finding IDs are `R-nn`; severity is **BLOCKER** (contradiction or unimplementable rule), **MAJOR** (a design defect with a concrete failure), **MINOR**, or **NOTE** (usability, wording, documentation). "Disposition" names the section of `SPEC-V8.md` draft.3 or the proposal rev 3.0 where the resolution lives.

---

## 1 Verdict

**What holds.** The split of *what is needed* (`[service.<n>]`) from *what provides it* (`[realization.<n>] kind = …`) with realness variants; one identity derivation written as data; hosts/networks/layouts with derived cross-host publication and derived waves; the per-layout realness record; the generated-file split (already shipped in ciu 7.10.0 as ciu-P47); one secrets store with mandatory `delivery`; cgroup-named resource keys; the judge as an image-baked floor plus verdict provenance; `ciu check` with stable rule ids. None of these was found wrong; every one of them survives in rev 3.0.

**What breaks, structurally (each expanded in §3):**

1. **The user constraint is new.** The proposal absorbs run-gate on the reasoning "nobody but us would use a facts contract"; the operator now requires every tool to stay usable standalone with no hard dependencies. As specified, `ciu gate` needs an instance (overlay, `instance init`, a rendered file to lock) even for cmru's five-line host lane, and a `docker` binary at startup even for host-only lanes (R-01, R-02, R-51). *Decision: absorb the functionality, keep run-gate alive in parallel, and make a zero-instance, zero-docker gate mode mandatory.*
2. **`routes` forces the two-pass render and a consumer-side mapping hop.** Seventeen of twenty-seven demo stacks read `routes.*` inside the stack TOML, which is why S3.5.5 renders stack files twice with a recording stub and why gap 4d exists. Asked whether `routes` should exist at all, the operator asked for the cleanest schema. *Decision: consumers declare **bindings** under a local name, delivered like secrets (env or template); `routes`, `uses`, `init_requires`, `after`, the two-pass render and the hand-typed `contract` disappear* (R-05, R-18..R-22).
3. **Declarations are templates.** Because stack and global files are Jinja, no external tool can read them, StrictUndefined errors appear in *declarations*, and `ciu instance add --join` has to write into a Jinja overlay — the exact round-trip problem X38 moved the generated facts out for (R-06, R-08). *Decision: every declaration file is plain TOML; Jinja only in compose and config-file templates.*
4. **The rendered file as lock costs five mechanisms and leaves a hole.** In-place render, a completion marker, torn-file detection, an `fstat`/`stat` retry, "clean truncates never unlinks" — and `git clean -x` still forks the mutex (R-42). *Decision: `flock` on the checkout's directory descriptor; the rendered file goes back to atomic writes.*
5. **Hooks lost their helpers.** The subprocess model (a review-round decision, not the operator's) re-creates the CIU-4 regression: every hook hand-rolls readiness polling, and `apply_to_config` is gone (R-40). *Decision: subprocess JSON stays the contract; ciu ships `ciu.hookkit`.*

**What changed in the secrets posture.** v7 resolved secrets on the target; draft.2 ships a reduced plaintext store with every push. The operator's question ("is there a contradiction? there could be no vault") led to a rule that is *per source*: local-source values travel, Vault-sourced values are fetched by whoever has a derived route to `vault` (R-32).

**What was not changed.** Every operator decision of the 2026-08-30 interview that this review did not put back on the table (§6): absorb run-gate (kept, now with run-gate alive), identity as data, generic realization registry, `[ciu_stack.<svc>]` root, explicit layouts, phases dropped, `delivery` mandatory, cgroup keys, image-baked judge, no `.ciu/`, `ciu gate` + `ciu instance` verb names, flat rendered artifacts.

---

## 2 The standalone constraint and the measured dependency map

The operator's framing: the tools "are still meant to be usable standalone for 3rd party consumers so we must not have hard dependencies." The proposal (§4.3.2) argued the opposite for the gate. To judge the gap, the estate was mapped at source level (canonical trees only; worktrees excluded).

**run-gate adopters (12).** The vbpub root `run-gate.toml` declares the estate's only environment (`tester-unified`, image `tester-unified:local`) and zero lanes; ten project files inherit it (R-22 central inheritance) and declare 1–5 lanes each, all `command` or `assay`, none `exec`. **dstdns is the only `mode = "exec"` adopter** (1 environment, 28 lanes: 15 assay, 13 command) and declares no `container_name`, so run-gate derives it from `ciu.global.toml` (`run-gate.py:2597-2641`: judged-worktree file first, then `deploy.project_name` + `deploy.environment_tag`, fallback `deploy.network_name` minus `-network`). **That function is the single hard ciu→run-gate coupling in the estate.** Every vbpub `run-gate.py` is a symlink to `run-gate-project/run-gate.py`; dstdns uses the pip-installed binary. Invokers: nyxloom `[gates.*]` in five troves (dstdns: 9 gates, 12 invocations), cmru release steps in `cmru/cmru.toml` and `run-gate-project/cmru.toml`. `shared-ramdisk-depot-manager` gates through its own `tools/gate.sh`, not run-gate.

**assay → ciu.** No imports, no shell-outs (`assay/pyproject.toml` `dependencies = []`). One file-level read: `derived:` infrastructure facts resolve dotted paths in `<project_root>/ciu.global.toml` (`assay/src/assay/cli.py:673`, `runner.py:555-578`) — **only when a lane declares `[lanes.X.infrastructure]`; dstdns's 1005-line `assay.toml` declares none.** `assay lanes --json` (B044) exists with `base_source`, `external_tools`, `argv0`, `env_required`, `infrastructure_facts`, `budget` per lane. Current release 4.1.0.

**ciu → other tools.** None (docstring cross-references only). Runtime dependencies: `Jinja2`, `PyYAML`, `tomli_w`; extras `ssh` (paramiko), `schema` (jsonschema), `registry` (pydantic).

**cmru / nyxloom.** cmru deliberately duplicates `ciu.governance.check_slice_unit` ("cmru declares zero deps, so it cannot import ciu", `cmru/src/cmru/tester_gate.py:154`). nyxloom's gate runner substitutes `{worktree}` into an argv from `nyxloom.toml` and runs it; it hardcodes neither run-gate nor docker.

**dstdns's ciu consumption.** 35 stacks with `ciu.defaults.toml.j2` (12 `infra/`, 6 `infra-global/`, 6 `applications/`, 2 `tools/`, 7 legacy, 1 retired, root), 35 compose templates, 13 hook scripts. Verb references in scripts/docs: `up` 137, `clean` 67, `render` 66, `env` 44, `down` 33, `bake` 15, `check` 9, `health` 5, `dev` 4, `ssh` 3, `provenance` 1, `init` 1 — `bake`/`health`/`dev`/`ssh`/`provenance` appear in prose (GUIDE, handoffs, deployment guide), not in executable scripts. Hook API use: `ctx.secret_file` 23×, `ctx.repo_root` 2×, `apply_to_config` 2× (`infra/vault/post_compose_vault.py:389`, `infra-global/github-runner/pre_compose_hook.py:123`), `persist: "state"` in 4 hooks (16 facts), `persist: "secret"` 1×, `ctx.wait_healthy`/`ctx.wait_tcp` **0×** — ciu ships both probes (`hooks_runner.py:79-85`) and no dstdns hook uses them.

**Consequences the review enforces.** (a) `ciu gate` must run in a directory that holds only `[project] name` and `[testing]`, with no instance, no lock, no rendered file, and no `docker` unless a lane's environment is a container (R-51, R-02). (b) assay's `derived:` read is a *soft* dependency and stays optional; with env-delivered bindings, `required-env:` facts cover the same need without assay knowing ciu's file layout (R-03). (c) run-gate's exec-mode derivation reads a file that rev 3.0 renames; that is an RG backlog item, not a v8 blocker (R-01). (d) cmru's duplication of the slice check is the price of zero dependencies and stays.

---

## 3 Findings

### 3.1 Constraints and dependencies

**R-01 · BLOCKER · The absorption argument contradicts the new constraint.** *Where:* proposal §4.3.2 ("nobody but us would use a facts contract"), §4.4 V8-18 (run-gate frozen). *What:* the standalone requirement is not in the proposal or the wholistic prompt (one mention of "standalone", line 412, in another sense). *Why it matters:* freezing run-gate makes every external copy-script adopter depend on ciu (a Python package with three runtime dependencies) for a five-line host lane; that is a hard dependency by construction. *Done:* interview Q1 — absorb the functionality for maximum synergy, keep run-gate maintained in parallel and aligned later; rev 3.0 §4.1.10 and V8-18 rewritten; `[testing]` must be usable with zero stacks and zero instance (R-51). run-gate's exec-mode read of `ciu.global.toml [deploy]` breaks when the rendered file becomes `ciu.resolved.toml` — filed as an RG item (§7).

**R-02 · MAJOR · Hard dependencies are not gated by need.** *Where:* S18.2 (`CIU_SKIP_DEPENDENCY_CHECK` "skip the startup check for docker/docker compose binaries"), S16.3 (`[testing.judge] version` required), S14.1.1 (registry needs git). *What:* a host-only lane project needs docker installed or an env var; a command-only project must declare a judge floor it never uses. *Why:* both are hard dependencies on tools the project does not use. *Done:* draft.3 S18.3 — preflights are per verb and per lane environment (docker only for container lanes and stack verbs; assay only before an `assay` lane; git only for the registry; Vault only when a vault-sourced secret exists); `[testing.judge]` is required iff an assay lane exists.

**R-03 · MINOR · assay↔ciu coupling through file paths.** *Where:* demo `assay.toml:62-65` (`derived:ciu.instance.resolved.routes.test_runner.main_db.sql.host`). *What:* a dotted path into ciu's rendered file is a format coupling that breaks on every layout change (and the file is renamed by R-08); it has zero live adopters. *Done:* bindings with `delivery = "env"` on a gate environment hand the facts to the lane process as environment variables, so assay's existing `required-env:` suffices; `derived:` stays supported for readers that want it, repointed at `resolved.bindings.<env>.<local>.*` (draft.3 S16.4, S3.7).

**R-04 · NOTE · Zero-install script distribution (run-gate R-31) is not reproducible by ciu.** Accepted: that is what keeping run-gate alive is for; ciu documents the difference (rev 3.0 §4.1.10).

### 3.2 Files and the configuration model

**R-05 · MAJOR · The two-pass render exists only because stack TOML reads `routes`.** *Where:* S3.5.5, gap 4d, demo (17 of 27 stack files, e.g. `applications/controller/ciu.defaults.toml.j2:22`). *What:* routes depend on endpoints declared in stack files, so stack files are rendered twice with a recording stub, a "literal chains only" rule, and an unproven pass-1/pass-2 consistency requirement. *Why:* an entire mechanism plus an error class plus a stage-2 check, to let a declaration file read a derived value it could read in a template. *Done:* removed by R-18 (declarations never see derived values; draft.3 S3.5).

**R-06 · BLOCKER · The overlay is Jinja, but ciu writes it.** *Where:* S9.5.5 (`ciu instance add --join` "writes these tables into the overlay") vs X38 (CIU-owned tables moved out of the overlay because "no round-trip-safe TOML+Jinja editor" exists). *What:* the same contradiction X38 resolved for generated facts is reintroduced for joins. *Done:* the instance file is plain TOML (`ciu.instance.toml`, draft.3 S2.1, S14.2); `instance add --join` appends with a TOML round-trip writer.

**R-07 · MINOR · `{}` cannot disable a table under deep merge.** *Where:* S3.1.2 ("a falsy value (`false`, `""`, `[]`, `{}`) disables it") together with "tables merge recursively". *What:* merging `{}` into a table is a no-op; the sentence is false. v7 S3.1a listed only `false`/`""`/`[]`. *Done:* draft.3 S3.1.2 — tables cannot be removed by a layer; a service, secret, binding or lane is removed with `enabled = false`; `{}` is not a disabling value.

**R-08 · MAJOR · Declarations as templates are machine-unreadable and hazard-prone.** *Where:* S2.1/S2.2 (`*.toml.j2`), S3.2 (StrictUndefined in every layer), CIU-74. *What:* no TOML validator, editor schema or third-party tool can read a `.j2` declaration; a typo in a declaration surfaces as a Jinja error; `{% for %}` in declarations is the crutch §4.3.3 already called out. With bindings (R-18) and a checked `[vault.paths]` reference (R-30) nothing in a declaration file needs Jinja. *Done:* interview Q2 (schema) — plain-TOML declaration files, renamed: `ciu.toml` (defaults), `ciu.site.toml` (sparse site override), `ciu.instance.toml` (operator, per checkout), `ciu.instance.generated.toml` (ciu), `ciu.stack.toml` (stack), rendered `ciu.resolved.toml` (draft.3 S2). Jinja remains for `ciu.compose.yml.j2` and config-file templates. `ciu schema --json` emits a JSON Schema for the declaration files (R-63).

**R-09 · MINOR · The per-stack rendered `ciu.toml` duplicates the merged view.** *Where:* S2.2, S3.6.4. *What:* the root rendered file already carries every stack's tables re-rooted under `realization.<R>`; a second per-stack rendering of the same data is a second carrier (I1). *Done:* dropped; hooks and humans read `ciu.resolved.toml` (draft.3 S2.2).

**R-10 · MINOR · The stack-level override layer is a second override mechanism.** *Where:* S3.1.3 (`ciu.toml.j2` sparse stack override), demo consul ("host publishing left to `ciu.toml.j2` overrides"). *What:* because stack tables are re-rooted into the merged view, a site or instance file can override any stack table by its merged path. *Done:* one override mechanism: `ciu.site.toml` / `ciu.instance.toml` may carry `[realization.<R>.services.<svc>.…]` tables (draft.3 S3.1.3); the stack override file is gone.

**R-11 · MINOR · Twelve reserved service keys because the merged view mixes services with metadata.** *Where:* S1.4 (reserved list), S3.6.2 (`realization.R.<svc>` next to `kind`, `location`, `hosts`, `secrets`, `hooks`, …). *Done:* the merged view is `realization.<R>.services.<svc>`; the reserved-key list shrinks to the file-level names (`secrets`, `hooks`, `governance` at stack level) (draft.3 S3.6).

**R-12 · NOTE · `[deploy]` mixes project identity, labels, health defaults, template env, control flags, provenance, bundles, layouts, realness.** *Done:* interview — `[project]` for identity; `[bundles]`, `[layouts]`, `[realness]` top-level; `health` defaults under `[project.health]`; `deploy.env.defaults` dropped (consumer data, R-13); `deploy.labels.prefix` dropped (R-15) (draft.3 S3.3–S3.4).

**R-13 · NOTE · `deploy.env.defaults` is consumer template data inside a ciu-owned table.** ciu attaches no semantics; it belongs in a consumer table (`ciu.user_tables`). *Done:* dropped from the schema; `deploy.env.shared` becomes `[project.compose_env]` with its actual meaning in the name.

### 3.3 Identity

**R-14 · MAJOR · The injectivity claim is false.** *Where:* S1.4 ("`_` → `-` (injective, because `name` forbids `-`)"), S4.3.1 ("structurally guaranteed; checked"). *What:* the mapping of one *name* is injective; the *concatenation* with `-` separators is not: realization `db_core` + service `postgres` and realization `db` + service `core_postgres` both derive `p-i-db-core-postgres`; so does a single-service realization named `db_core_postgres` (elision). *Why:* stage 8 catches the collision, but the spec promises a structural guarantee it does not have, and the refusal would say "uniqueness" instead of "ambiguous boundary". *Done:* draft.3 S4.2.5 states the rule honestly (names may collide after mapping; stage 8 refuses with a message naming both derivations and the `_`/`-` cause); elision kept (operator's D5).

**R-15 · MAJOR · Ownership labels under a consumer-chosen prefix orphan resources on a prefix change.** *Where:* S4.5.1 (`<prefix>.instance` …), v7 S16.10 reap categories keyed on fixed `ciu.instance`/`ciu.repo-root`. *What:* `ciu instance reap` and `clean` enumerate by label; a consumer that changes `deploy.labels.prefix` between releases makes every running container "unattributable". *Done:* ownership labels are fixed `ciu.project`, `ciu.instance`, `ciu.realization`, `ciu.service`, `ciu.replica`, `ciu.managed-by`; `labels.prefix` is dropped; consumer labels come from templates (draft.3 S4.5).

**R-16 · MINOR · `public_fqdn` derived from a "public"-described address contradicts "description carries no semantics".** *Where:* S14.2 example comment vs S7.3 (`description` "CIU attaches no semantics"). *Done:* `[hosts.<h>] fqdn` is declared (a host fact the operator knows; DERIVE > READ > FAIL says read it, not detect it); `require_fqdn` checks presence for the rendered host (draft.3 S7.2, S14.2).

**R-17 · NOTE · The 63-character rule is stated on the service key.** S4.3.3 should bound the full derived string (replica suffix included). *Done:* draft.3 S4.3.3.

### 3.4 Contracts, bindings, ordering

**R-18 · MAJOR · `routes` + `init_requires` + `uses` + `after` + the consumer mapping hop are five spellings of one relation.** *Where:* S6.2 (`init_requires`, `uses`, `after`), S7.8 (`routes.<C>.<X>.<e>`), S3.5.5; demo `[ciu_stack.controller.database] host = "{{ routes.main_db.sql.host }}"` then compose `{{ ciu_stack.controller.database.host }}`. *What:* a consumer declares the dependency in one list, reads the address by provider name in a template (or, via the hop, in the stack TOML), and the ordering semantics are spread over three lists whose 2×2 matrix (route? edge?) the spec never draws. *Why it matters:* provider names leak into consumer templates (swap `main_db`'s realization: fine; rename the LogicalService: every template changes), and the address is *pulled* by templates instead of *delivered* the way secrets already are. *Done:* interview Q4/Q5 — **bindings**: `[ciu_stack.<svc>.binds.<local>] to = "<service>[.<endpoint>]"`, `wait = healthy|started|none`, `delivery = env|template|none`, `env_prefix`, `facts = [...]`. The consumer reads `binds.<local>.*` (template delivery) or nothing at all (env delivery); `requires = [...]` is sugar for bindings without data. Routes survive only as the *resolution* of bindings in the rendered file (`resolved.bindings.<C>.<local>`). Draft.3 S6.4, S7.8.

**R-19 · MAJOR · `contract` is hand-typed and restated.** *Where:* S5.2 (`contract` required), demo `[service.main_db] contract` (14 facts, of which 6 are `vault:secret/*` derived from `GEN_TO_VAULT` directives anyway and 8 are `pg:*` facts restated from `init_provides`). *What:* the contract is written by the *provider's author* on the *logical* table, so it is a copy of the provider's provides list — P1 violated for every entry — while the one thing a contract should say (what consumers rely on) is nowhere. *Done:* the contract is **derived from consumption**: for LogicalService X it is the union of endpoints bound to X and facts listed on bindings to X (`facts = ["pg:role/controller"]`) plus vault paths asked from X's minters; every variant of X must provide it (stage 5); facts nobody consumes are INFO. Draft.3 S5.3.

**R-20 · MINOR · `[hooks.provides.<svc>]` and `init_provides` have identical semantics.** *Where:* S8.3 ("facts — every fact in `init_provides`, in `[hooks.provides.<svc>]` …"), S8.7 (hooks run before the next wave's probes in both cases). *What:* two spellings distinguished only by *who* creates the fact, which ciu never acts on. *Done:* `provides` on the RealizedService and `provides` on the hook entry (`[[hooks.post_compose]] run = "…", provides = [...]`), one vocabulary, the hook form only says which script is responsible (draft.3 S6.10, S12.1).

**R-21 · MINOR · `after` is `requires` without facts — and in the new model `requires` has no facts either.** *Done:* `after` dropped; `requires` (edge, wait healthy/completed) covers it; `wait = "started"` on a binding covers the "start order only" case (draft.3 S6.4).

**R-22 · MAJOR · An ordering-only dependency forces a cross-host publication.** *Where:* S7.8.3 (a route is derived for every `init_requires` entry) + S7.4.1 (an endpoint reached by a cross-host route is *published* on the provider host). *What:* `init_requires = ["vault"]` for ordering only (consul's case in the demo) derives a route to vault's endpoint and, on `prod3`, publishes port 8200 on the mesh address although nothing reads the route. *Why:* a derived listening port nobody asked for is the opposite of "explicit over magic". *Done:* only a binding **with** an endpoint derives a resolution and hence a publication; `requires`/endpoint-less bindings derive edges only (draft.3 S7.4.1, S7.8.3). `ciu check --layout` prints the publication table (R-71).

**R-23 · NOTE · A Realization-level cycle refusal should name the remedy.** *Done:* draft.3 S8.4.1 message: "split realization X (services a, b) or declare the edge at stack level".

**R-24 · MINOR · Gap 4c (cross-host wave sync) is closed by the supported flow and should say so.** `ciu activate apply` runs hosts serially in layout order (S17.4), so host B's first wave starts after host A's `up` completed every gate. *Done:* draft.3 S8.4.3 states it; the residual risk is only hand-parallel bring-up.

**R-25 · MINOR · Healthcheck timing is template boilerplate.** *Where:* S6.6.4 (the template authors `healthcheck:` from `ciu_stack.<svc>.health.*`). *What:* four lines repeated per service that ciu already owns the values of, while ciu injects everything comparable. *Done:* when a template writes `healthcheck.test`, ciu injects `interval`/`timeout`/`retries`/`start_period` from the merged `health` table; a template that writes them must agree (draft.3 S11.4).

### 3.5 Realness and joins

**R-26 · BLOCKER · Selection precedence contradicts the record's immutability.** *Where:* S9.3.1 (CLI > record > pin > default) vs S9.4.2 ("an explicit selection (`--realness`, or a changed pin) that differs from the record is an ERROR"). *What:* if the record precedes the pin, a changed pin is silently overridden, not refused. *Done:* the record is a **constraint**, not a source: selection is CLI > pin > default; a result that differs from the layout's record is an ERROR until `clean --vanilla` (draft.3 S9.3, S9.4).

**R-27 · MINOR · Joined `instance` resolves label → checkout basename → path, and labels are not unique.** *Where:* S14.7.2. *Done:* labels unique per git family (enforced at `instance init`/`add`); a join names a label or an absolute path; basename resolution dropped (draft.3 S14.7.2).

**R-28 · MINOR · The joined-vault token path is unspecified.** A worktree joining the primary's vault has no `root_token` in its own store. *Done:* draft.3 S10.3.3 — a `joined` vault's token is read from the reference's store under the reference's shared lock, after `VAULT_TOKEN` and `token_file`.

**R-29 · NOTE · `owned-seeded` → `seeded`** (interview): one seeded kind, no hyphen in keys or CLI.

### 3.6 Secrets

**R-30 · MAJOR · The directive string DSL is parsed, regex-checked and Jinja-composed.** *Where:* S10.1 (seven directive prefixes with `:`/`#`/`,` sub-syntax), S2.4.1 (KV-path-shaped exemption `^[a-z0-9_./-]+$`, which also exempts `hunter2secret` — v7 S3.4a required a `/`), demo (`ASK_VAULT:{{ vault.paths.x }}`). *What:* a mini-language inside a string inside a template: three parsers for one declaration, and `[vault.paths]` "never read by ciu" is exactly the table ciu should check. *Done:* structured secrets — `from = vault|generate|ask|file|host|ephemeral`, `path` (a `[vault.paths]` key or a literal path containing `/`), `field`, `store = vault|local` (for `generate`), `var`, `entry`, `delivery`, `env_name`, `mode`, `uid`, `enabled`; `[vault.paths]` is a checked reference table (draft.3 S10.1–S10.3). The secret-free scan keeps v7's `/`-bearing rule.

**R-31 · MINOR · `consumed_by = "hook"` and `produced_by` are redundant.** `consumed_by` is a sixth delivery (`delivery = "hook"`); the producer bundle is derivable from the minter edge (v7 S13.6's refusal becomes stage 5's "no minter in the deploy set"). *Done:* both dropped (draft.3 S10.2).

**R-32 · MAJOR · Push secrets posture regressed and was decided per layout instead of per source.** *Where:* v7 S14.2 ("secrets resolve on the target; no resolved value transits the wire") vs S17.3 (a reduced plaintext store ships every push), gap 8 ("target-side flow not designed"). *Reasoning (as answered to the operator's question back):* local and remote differ in one thing — where the store is. A remote `up` runs from a bundle, so whatever only the sender knows must travel: `generate`+`store=local` values (identical on every host by definition), `ask` values (no operator on the target), `file` values read on the sender. `from = "host"` is read on the target by definition. `from = "vault"` values live in Vault, so the only question is who fetches: the target, if it has a derived route to the `vault` LogicalService; otherwise the sender pre-fetches and adds them to the shipped store. A project without Vault therefore ships its whole reduced store; one with a reachable Vault ships local-source entries only. *Done:* draft.3 S17.3 — bundle content derived per source and reachability; `ciu check --layout` lists which entries travel and why.

**R-33 · NOTE · One store next to the code in every worktree.** Blast radius of a single 0600 file leak vs v7's per-stack files — same class, accepted; the store is excluded from bundles except per R-32.

**R-34 · MINOR · `GEN_EPHEMERAL` across hosts is undefined.** *Done:* `from = "ephemeral"` is generated by the host that runs `up`, per run; a secret consumed by realizations placed on two hosts of one layout cannot be ephemeral (stage 9 ERROR) (draft.3 S10.1.4).

### 3.7 Compose rendering, config files, host directories

**R-35 · MAJOR · v7 S5.3a's mount hardening is lost.** *Where:* S6.9.1 (file-level bind of `ciu.rendered.<svc>.<name>`) vs v7 S5.3a (rendered config files are mounted by *parent directory* at a mirrored path because Docker silently creates a *directory* for a missing single-file bind source — a live incident). *What:* the flat naming makes the directory scheme impossible; a later `git clean -x` + `docker compose restart` recreates the hazard. *Done:* rendered config files live in a visible `ciu.rendered/<svc>/<mirrored target path>` directory and the parent directory is mounted (draft.3 S6.9). P10 forbids *hidden* directories, not nested visible ones.

**R-36 · MAJOR · The hostdir path change is a data migration nobody planned.** *Where:* S6.8 (`<physical_repo_root>/ciu-data/<R>/<svc>/<purpose>`) vs v7 S6.1 (`<stack>/vol-<service>-<purpose>`), Appendix A silent. *What:* a fresh v8 `up` initialises an *empty* data directory next to the old full one — silent data "loss" from the application's view. *Done:* `ciu migrate` relocates (or links) `vol-*` directories and refuses when both exist; `hostdir.<p> = "/abs/path"` pinning is the zero-move option (draft.3 S6.8.4, Appendix A).

**R-37 · MINOR · The overlay-vs-injection reversal is not on record.** v7 S8.1 rejected injection (anchors/aliases/comments destroyed; template-bug vs ciu-bug boundary blurred). Injection is right for v8 (one artifact, ~40 % shorter templates, anchors are expanded not lost) but the reversal must be recorded and the boundary kept inspectable. *Done:* rev 3.0 §4.7 X41; `ciu render --show-injected` prints the diff between template output and the final artifact (draft.3 S11.7).

**R-38 · MINOR · Provenance stamping and `CIU_IMAGE_REVISION` are missing from S11.4/S18.** v7 S17 stamps `org.opencontainers.image.revision` (with `-dirty`) at bake and injects the revision at up. *Done:* `ciu build` stamps; `up` injects `CIU_IMAGE_REVISION` and the identity env (`CIU_PROJECT`, `CIU_INSTANCE`, `CIU_REALIZATION`, `CIU_SERVICE`) (draft.3 S11.4, S18).

**R-39 · MINOR · The devcontainer's attachment to the primary's instance network vs `clean`.** v7 S6.4a kept the main network; S14.1.4 removes networks labelled to the instance. *Done:* `up` creates the network idempotently; `auto_connect_network` attaches ciu's own container on first need; `clean` disconnects it first and keeps the network only while another instance is attached (which is already an ERROR) (draft.3 S14.1.4).

### 3.8 Hooks

**R-40 · MAJOR · The hook model reversal drops CIU-4's helpers.** *Where:* S12 (subprocess, JSON stdin/stdout) vs v7 S9 (`run(config, ctx)` with `ctx.wait_healthy`/`wait_tcp`, `apply_to_config`, `validate_config`); X39 (a spec-review decision). *What:* language-agnostic hooks are a real gain for third parties, but every Python hook now re-implements readiness polling (CIU-4's regression class), `apply_to_config` has no successor, and dstdns's 13 hooks plus the shipped template library are rewritten. Measured (§2): dstdns hooks use `ctx.secret_file` 23×, `apply_to_config` 2×, the wait helpers 0×. *Done:* interview Q3 — subprocess JSON stays the only contract; ciu ships `ciu.hookkit` (`context()`, `wait_healthy()`, `wait_tcp()`, `emit()`, `validate()`), and the shipped hook templates are rewritten on it (draft.3 S12.5).

**R-41 · MAJOR · Render/hook ordering and state visibility are underspecified.** *Where:* S8.7 (six words: hooks → secrets → hooks → compose up → gate → hooks) vs v7 S8.3 (seventeen ordered steps). *What:* nothing says whether a `pre_compose` hook's `state` output is visible to the same run's config-file render (v7: yes, hooks precede the configfile render), where hostdir creation and the leak scan sit, or when `ciu.compose.yml` is written. *Done:* draft.3 S8.7 lists the per-realization pipeline in order with the visibility rule.

### 3.9 Instances, locking, registry

**R-42 · MAJOR · The rendered-file lock costs five mechanisms and leaves a hole.** *Where:* S14.4.1–S14.4.7, gap 4. *What:* in-place render (S14.4.2), the completion table as the last table (S14.4.2, X37), torn-file refusal by every reader, the `fstat`/`stat` retry (S14.4.4), `clean` truncating instead of unlinking (S14.4.6), and an undetectable fork of the mutex on external unlink (S14.4.7). Every one of them is a consequence of choosing a file that ciu itself rewrites as the lock object. `flock` on the checkout's directory descriptor has an inode that is stable for the life of the checkout, needs none of the five, and has no hole; the rendered file then returns to atomic temp+rename writes (always complete or absent). *Done:* interview Q2 — directory-fd lock (draft.3 S14.4); the operator's earlier preference (F14) is superseded on record (rev 3.0 §4.3.1).

**R-43 · MINOR · The shared gate lock class exists mainly for nested `ciu gate` conjunctions.** *Where:* X27, S14.3 ("including nested `ciu gate` invocations from a `host`-environment lane"), demo `[testing.lanes.gate] argv = "ciu gate schema && ciu gate unit && …"`. *What:* a conjunction as a shell string spawns nested gate processes, which is why the gate needed a lock class that coexists with itself, and why run-gate's R-25 override-reachability guard exists (`--worktree` must reach every sub-invocation). *Done:* `kind = "sequence"` lanes (R-53) remove nesting; the gate still takes the shared directory lock so `up` cannot recreate a container under an `exec` lane, and two operator-started gates coexist under admission (draft.3 S14.3, S16.5).

**R-44 · MINOR · `ciu instance lease` is missing.** v7 S16.9 `worktree lease --extend|--perpetual|--release`; S18 lists `reap` but no lease verb. *Done:* restored (draft.3 S18).

**R-45 · MINOR · The lease `holder` needs a hostname fact.** v7 used `DEVCONTAINER_NAME` from `ciu.env`, retired as an input. *Done:* `[ciu.host.generated] hostname` (draft.3 S14.2).

**R-46 · NOTE · `ciu.instance.json` duplicates `instance_id`.** Considered folding the record into the generated file; kept separate because other checkouts (reap, lease) mutate the record while the generated file is rewritten whole by this checkout's ciu. Accepted duplication of one value, documented (draft.3 S14.7).

**R-47 · MAJOR · The exec-target mount proof is lost.** *Where:* v7 S16.7 (before `exec`, prove via `docker inspect` that the container mounts *this* checkout's physical path at `workdir`; refuse when the primary's tree is mounted while a linked worktree is selected) vs S16.4 (`exec_in` names a LogicalService; nothing verifies the mount). *Why:* an `exec` lane can run tests against the wrong tree and exit 0. *Done:* draft.3 S16.4.3 — `exec` environments prove the mount before every lane and `ciu instance exec`.

**R-48 · MINOR · Exit codes renumbered against v7.** v7 S10.3: 0 / 1 runtime / 2 config-validation / 3 env-bootstrap; draft.2 S18.1: 0 / 1 refusal / 2 usage / 3 lock / 4 remote. Wrappers keyed on "2 = config error" misclassify silently. *Done:* v7 meanings kept, 4 = lock contention, 5 = remote failure; the gate keeps its own table (draft.3 S18.1).

**R-49 · MINOR · JSON envelopes are inconsistent.** `facts_schema` in the rendered file and LaneResult vs v7's `{schema_version, operation, status, …}` on every `--json` verb; `ciu check --json` states no envelope. *Done:* one envelope everywhere; the rendered file carries `schema_version` (draft.3 S3.7.1, S15.4, S16.9, S18.4).

**R-50 · NOTE · Root resolution is not stated once.** *Done:* draft.3 S1.5 — walk up to `ciu.toml`; `--root` explicit; no environment variable.

### 3.10 The gate

**R-51 · BLOCKER · Zero-stack mode still needs an instance.** *Where:* S16.11 (a zero-stack project "MAY declare `[testing]` … `ciu gate` then requires no `ciu up`") vs S3.1.4 (overlay and generated file MUST exist for every mutating verb), S14.4.1 (the gate opens the rendered file; "ENOENT = not rendered: the gate refuses"), S15.3 stage 1 (overlay present). *What:* cmru's gate (5 command lanes, host and tester-unified) would need `ciu instance init` and a render before its first lane, and an instance lock it has nothing to lock. *Done:* draft.3 S16.11 — a project whose declarations contain no `[realization]` is a **zero-instance project**: no overlay, no generated file, no rendered file, no lock, no docker unless a container environment is used; `[layouts]`/`[bundles]`/`[realness]` are absent, `ciu check` runs stages 1–3 and 12 only. `[testing] inherit` (R-54) makes the vbpub files three lines.

**R-52 · MINOR · `request_base` duplicates assay's `base_source`.** *Where:* S16.5 (`request_base`), the CIU-72 note in the same rule ("derivable via `assay lanes --json`"), §4.1.10 ("ciu never reads assay.toml beyond lane names" — yet stage 12 parses it with `tomllib` *and* calls `assay lanes --json`). *Done:* one interface to assay: `assay lanes --json` (lane names, `base_source`, `external_tools`, `argv0`, `env_required`); `request_base` and the `tomllib` read are dropped; without a reachable judge the assay checks of stage 12 are skipped, not failed (draft.3 S15.3, S16.7).

**R-53 · MINOR · Conjunction lanes as shell strings.** *Done:* interview — `kind = "sequence"`, `lanes = [...]`, `stop_on = FAIL|never`; one process, one LaneResult per member plus one for the sequence (draft.3 S16.5.4).

**R-54 · MAJOR · Central `[testing]` inheritance (run-gate R-22) is dropped.** *Where:* §4.5 H ("central config / lanes (R-22): none — no central lane config in v8"). *What:* the vbpub monorepo's one shared environment would be repeated in ten project files — the P1 violation the estate's own R-22 exists to avoid. *Done:* interview — `[testing] inherit = "<path to a ciu.toml>"` (environments only; lanes never inherit) (draft.3 S16.2.1).

**R-55 · MINOR · The provenance↔gate synergy is missing.** v7 S17.2 `ciu provenance` is a *test-time* gate ("does this passing run describe the code I think it does?"); absorbing run-gate makes it free. *Done:* every LaneResult records, per required service, the running image's revision label against `HEAD`; `require_provenance = true` on a lane turns a mismatch into `NOT_RUN/provenance-mismatch` (draft.3 S16.5.6, S16.8).

**R-56 · MINOR · Lifted run-gate rules must be carried by reference, not lost.** R-04 (path charset), R-19a (`GIT_CONFIG_GLOBAL` isolation), R-23 (dual-mount guard), R-26 (evidence 0600 on failure), R-36 (history/timing), R-14a retired, R-25 moot with sequence lanes. *Done:* draft.3 S16.12 lists each with its v8 home.

**R-57 · MINOR · Ephemeral lanes in a zero-instance project have no instance network.** *Done:* draft.3 S16.4.2 — no network attach when no instance exists; bindings on such an environment are an ERROR.

**R-58 · NOTE · Admission against `memory.max = max` is a no-op.** Documented (draft.3 S16.6.1).

**R-59 · NOTE · The judge floor check runs the image.** `assay --version` inside the environment is a container start per check; cache per image digest for the session (draft.3 S16.3.1).

**R-60 · NOTE · `[testing.lanes.gate]` in the demo re-passes `--worktree` by hand.** Obsolete with sequence lanes (demo rewritten).

### 3.11 Command line and verbs

**R-61 · MAJOR · Twelve v7 verbs are dropped without a disposition.** *Where:* v7 S10.1 lists `init`, `health`, `status`, `profiles`, `layouts`, `ksm`, `dev`, `ssh`, `iops-baseline`, `capabilities`, `host-secrets`, `provenance`, `migration-check`, `worktree lease`; S18 accounts for none of them. dstdns references `bake`/`health`/`dev`/`ssh`/`provenance` in its operating guides. *Done:* draft.3 S18 carries a disposition for each: `init` kept (R-62); `health` → `status --live`; `status` kept; `profiles`/`layouts` → `show bundles|layouts`; `ksm`, `iops-baseline` → `governance ksm|iops-baseline`; `dev` kept (`up --realization r --no-wait`); `ssh` kept; `capabilities` → `schema`; `host-secrets` → `secrets host`; `provenance` kept; `migration-check` → `migrate --check`; `worktree lease` → `instance lease`; `worktree` alias one release.

**R-62 · MAJOR · `ciu init` (v7 S19) — the adopter on-ramp — has no v8 home.** *Done:* draft.3 S19 — `ciu init` writes `ciu.toml` with `[project]`, `[hosts.localhost] local = true` (in `ciu.hosts.toml`), `[layouts.local]`, `[realness] default = "live"`, an empty `[testing]`, the gitignore set, and optionally one stack from a running compose file (interview: no built-in host or layout; the two lines are written explicitly).

**R-63 · NOTE · Machine usability additions.** `ciu schema --json` (JSON Schema for every declaration file from the one table-spec S3.8.4 already requires); `ciu doctor` (docker, cgroup, git, judge, hooks) for adopters; stable rule ids already present. *Done:* draft.3 S18.

**R-64 · NOTE · `ciu dev` (v7 S5a).** Kept as a thin verb: render + `compose up` of one realization without waiting on its gate, refusing when the realization's providers are not up (draft.3 S18).

**R-65 · NOTE · `ciu bake` → `ciu build`, keeping `-dirty` stamping** (R-38).

### 3.12 Ceremony and human usability

**R-66 · MAJOR · Hello-world ceremony.** *Where:* the minimal v8 project needs `[deploy]` (project_name, revision, `labels.prefix`, four health keys), `[deploy.realness] default`, a profile, a layout with `environment` and `hosts.localhost{bundles, reach}`, a gitignored `ciu.hosts.toml` with `[deploy.hosts.localhost] local = true`, `[service.x] contract = [] live.realized_by`, `[realization.x] kind location`, the stack file, the compose template, `ciu instance init`, `ciu up` — nine tables and one gitignored file that every fresh clone must create, against v7's three tables. *Done:* `ciu init` writes the explicit host and layout (interview: no built-ins); `[service.x] live = "x"` string form; `labels.prefix` dropped (R-15); health defaults are policy defaults (10s/5s/60s/6) — the one class of default the estate doctrine permits (correct in the absence of information, shadowing no fact); `[realness] default` stays required and is written by `init`; `[testing]` optional; `ciu instance init` selects a project's only layout (a derivation from a singleton, reported). A `minimal/` example ships with the demo (rev 3.0 App B).

**R-67 · NOTE · Profiles vs bundles; the `all` bundle restates eighteen services.** *Done:* `[bundles.<b>] services, includes` (one-level composition, acyclic) (interview; draft.3 S7.5).

**R-68 · NOTE · "service" is overloaded.** LogicalService table, variant `service`, joined `service`, `[vault] service`, `[ciu_stack.<svc>]`, `requires.services`, `exec_in`. The new schema removes two uses (`requires.services` → `requires.healthy`; the merged view says `services.<svc>`); the variant key stays `service` (it names one). A glossary opens draft.3 S1.2.

**R-69 · NOTE · Layout `environment` is a closed vocabulary ciu attaches no semantics to.** *Done:* optional free-form string, bound as `instance.environment`; templates that read it fail under StrictUndefined when unset (draft.3 S7.6).

**R-70 · NOTE · No small example.** The demo is dstdns-scale (27 realizations). *Done:* `v8-dstdns-demo/examples/minimal/` (one stack, one host lane) and the demo README restructured to lead with it.

**R-71 · NOTE · `ciu check` hides later stages behind earlier errors and never lists derived publications.** *Done:* stages with no data dependency run even after an earlier ERROR (3 needs 2; 5–9 need 4; 7 and 8 are independent); `ciu check --layout L` prints the publication table and the bundle content table (draft.3 S15.3, S15.4).

**R-72 · NOTE · Rule ids, `--json` records and the rendered file are the right machine surface.** Kept; envelopes unified (R-49).

### 3.13 Documents and process

**R-73 · NOTE · §4.11's non-breaking items are largely shipped.** CIU-63/64/65/67/68/69/70/74/89 are FIXED in ciu 7.x (N1–N8); rev 3.0 §4.11 marks them and keeps only the open ones.

**R-74 · NOTE · The proposal predates ciu 7.10.** The generated-file split shipped as ciu-P47 (`ciu.instance.generated.toml`, overlay renamed); the spec already matched. Noted in rev 3.0.

**R-75 · NOTE · CIU-72/73 italic annotations inside normative rules.** Folded into the rules (draft.3 S16).

**R-76 · NOTE · `V8-REALIZATION-GRAPH.md` uses superseded table names** (`[ciu_stack.<name>] location`, `init_provides` on the Realization, `stack:` refs, `[external.*]`). It is a dated design note whose value is the trace and the correction history; a rev-3 preface maps every term to its current form rather than rewriting the trace.

**R-77 · NOTE · Demo README D1 and D12 describe the two-pass/`routes` model.** Rewritten with the demo.

**R-78 · NOTE · The demo's `landscape_id` comment says "REQUIRED" while S3.4.1 says optional.** Optional; comment fixed in the demo rewrite.

---

## 4 Interview record (2026-09-02)

Three rounds, ten questions; every question carried a recommendation. Options not chosen are listed so the decision can be revisited.

| # | fork | options (recommended first) | decided | operator's words / reasoning |
|---|---|---|---|---|
| Q1 | standalone vs absorption | absorb + zero-ceremony gate · absorb + keep run-gate alive · reverse F1 | **absorb + keep run-gate alive** | "we add the functionality of run-gate to ciu for max synergy. we keep run-gate itself alive for now in parallel, possibly align with future changes in ciu v8." The zero-ceremony mode is required by the first half (R-51). |
| Q2 | lock object | directory fd · rendered file · dedicated `ciu.lock` | **directory fd** | five mechanisms and one hole vs none (R-42) |
| Q3 | hook model | subprocess + `ciu.hookkit` · v7 in-process · both | **subprocess + hookkit** | portability for third parties; helpers keep Python hooks short (R-40) |
| Q4 | stack files and `routes` | no `routes` in stack TOML · plain-TOML declarations · keep two-pass | **"do we want to keep `routes` at all? … think out of the box"** | answered by Q5 |
| Q5 | schema direction | bindings + data-only declarations · bindings with Jinja declarations · keep routes, templates only | **bindings + data-only declarations** | the shape shown in the preview is the shape of draft.3 S6 |
| Q6 | renames | `profiles→bundles` · `owned-seeded→seeded` · `[deploy]→[project]` + top-level bundles/layouts/realness · file names (`ciu.toml`, `ciu.site.toml`, `ciu.instance.toml`, `ciu.stack.toml`, `ciu.resolved.toml`) | **all four** | — |
| Q7 | ceremony | built-in localhost + implicit `local` · `sequence` lanes · `[testing] inherit` · `live = "x"` shorthand + `ciu init` | **all four, with a doubt on the first** | "not sure about implicit localhost, if it makes schema harder to understand and use if you want remote hosts as well." |
| Q8 | push secrets | resolve on target · bundle-carried store · per layout | **question back**: "is there a contradiction between 1 and 2? there could be no vault and secrets could not be in the vault if there was a vault. how is remote different from localhost?" | answered in R-32; re-asked as Q9 |
| Q9 | push secrets, restated | derived per source + reachability · sender always pre-fetches · target always fetches | **derived per source + reachability** | — |
| Q10 | localhost built-ins | no built-ins, `ciu init` writes them · built-ins with reserved names | **no built-ins; `ciu init` writes them** | consistent with the 2026-08-30 decision "explicit always" for layouts and with the estate's defaults-are-hazards doctrine |

Decisions from the 2026-08-30 interview that this round **did not reopen** are listed in §6. The one it superseded is F14 (lock object), on the operator's decision in Q2.

---

## 5 Change map: rev 2.1 → rev 3.0, draft.2 → draft.3

| area | rev 2.1 / draft.2 | rev 3.0 / draft.3 | findings |
|---|---|---|---|
| declaration files | `ciu.global.defaults.toml.j2`, `ciu.global.toml.j2`, `ciu.global.instance.toml.j2`, stack `ciu.defaults.toml.j2` + `ciu.toml.j2`, rendered `ciu.global.toml` + per-stack `ciu.toml` | plain TOML: `ciu.toml`, `ciu.site.toml`, `ciu.instance.toml`, `ciu.instance.generated.toml`, `ciu.stack.toml`; rendered `ciu.resolved.toml` only | R-06, R-08, R-09, R-10 |
| render | Jinja in every layer; two-pass stack render | Jinja only in `ciu.compose.yml.j2` and config-file templates; single pass | R-05, R-08 |
| top-level tables | `[deploy]` with profiles/layouts/realness/labels/env inside | `[project]`, `[service]`, `[realization]`, `[network]`, `[bundles]`, `[layouts]`, `[realness]`, `[vault]`, `[registry]`, `[governance]`, `[testing]`, `[ciu]`; `[resolved]` derived | R-12, R-13 |
| consumer dependencies | `init_requires`, `uses`, `after`, template `routes.<X>.<e>.*`, consumer mapping hop | `binds.<local>` (`to`, `wait`, `delivery`, `env_prefix`, `facts`), `requires` sugar; templates read `binds.<local>.*` or nothing | R-18, R-21, R-22 |
| contract | `[service.<n>] contract = [...]` required | derived from consumption; variants checked against it | R-19 |
| provides | `init_provides` + `[hooks.provides.<svc>]` | `provides` on services and on hook entries | R-20 |
| secrets | directive strings; `consumed_by`, `produced_by`; `[vault.paths]` unread | structured `from/path/field/store/var/entry` + `delivery` (incl. `hook`); `[vault.paths]` checked | R-30, R-31 |
| push | reduced store always ships | per-source rule: local-source travels; vault-sourced fetched where reachable | R-32 |
| identity | consumer label prefix; injectivity claimed | fixed `ciu.*` ownership labels; collision rule stated | R-14, R-15 |
| hosts | `[deploy.hosts.<h>]`; `public_fqdn` detected | `[hosts.<h>]` with declared `fqdn` | R-16 |
| realness | `owned-seeded`; record in the precedence chain | `seeded`; record is a constraint | R-26, R-29 |
| lock | rendered file, in place, marker, fstat retry | checkout directory fd; atomic rendered file | R-42 |
| hooks | subprocess JSON | subprocess JSON + `ciu.hookkit`; full pipeline order; state visibility | R-40, R-41 |
| config files | flat `ciu.rendered.<svc>.<cfg>`, file bind | `ciu.rendered/<svc>/<mirrored path>`, parent-dir bind | R-35 |
| gate | needs an instance; `request_base`; shell conjunctions; no inheritance | zero-instance mode; `assay lanes --json` only; `sequence` lanes; `[testing] inherit`; provenance in LaneResult; mount proof | R-51..R-57 |
| CLI | 13 verbs | full disposition table incl. `init`, `status`, `dev`, `ssh`, `provenance`, `migrate`, `schema`, `doctor`, `instance lease`; v7-compatible exit codes; one JSON envelope | R-48, R-49, R-61..R-64 |
| ceremony | ~9 tables + gitignored hosts file | `ciu init` scaffold; `live = "x"`; bundle `includes`; policy health defaults | R-66, R-67 |

---

## 6 Deliberately unchanged (operator decisions of 2026-08-30 that stand)

Absorbing run-gate's functionality (F1; now with run-gate alive in parallel) · identity as data in the rendered file, no Jinja callables (F2) · network entities with derived routes/publication (F3; routes now surface as binding resolutions) · `joined` realization kind written by `ciu instance add` (F4) · explicit layouts always (confirmed again in Q10) · `[ciu_stack.<svc>]` as the stack-file root, file key = render key (F18/F18b) · generic `[realization.<n>] kind` registry (F18c) · phases dropped, waves computed and written (F6) · `delivery` mandatory (F5) · cgroup-v2 resource keys (F7) · image-baked judge floor + provenance (F8) · one endpoint shape on every kind (F11) · `ciu gate` + `ciu instance` verb names · no `.ciu/`, flat rendered artifacts (a nested visible `ciu.rendered/` directory is not a hidden directory) · identity elision when `svc == R` (D5) · `per_host` realizations (D11) · derived cross-host publication (D16) · `[ciu.instance.host_ports]` overrides · the instance registry record `ciu.instance.json` (R-46).

Review-round decisions of rev 2.1 that stand: derived vault facts and minter edges (X29); proxy networks address-free (X30); derived TLS secrets (X31); the generated-file split (X32, shipped as ciu-P47); `primary`/variant service (round 2); `mock = {}`; one realness default plus pins; consumer scalars in sub-tables; `healthy` defined once; cross-host gating reachability-only with serial activation (R-24).

---

## 7 Open after this round

1. **run-gate alignment.** run-gate's exec-mode container derivation reads `ciu.global.toml [deploy]`; when a v8 checkout renders `ciu.resolved.toml` that path is gone. Filed as an RG backlog item: read `resolved.identities` from `ciu.resolved.toml` when present, keep the v7 path otherwise.
2. **`ciu.hookkit` API** is specified at the level of function names and contracts (draft.3 S12.5); argument shapes are for the implementer.
3. **Certificate issuance** for TLS networks stays a `pki` hook's job (gap 4a of rev 2.1, unchanged).
4. **cgroup slices inside the devcontainer** (gap 5 of rev 2.1) still needs the live probe before the gate package is carved.
5. **Per-replica endpoints** for stateful replicated providers (gap 1) remain unspecified.
6. **Binding-carried credentials** (a binding that also delivers the provider's published secret under the consumer's local name) were considered and left out: secrets keep their own declaration; the idea is recorded in rev 3.0 §4.10 for a later revision.
7. **The demo's hook scripts and config-file templates** are still referenced by name, not written; the hookkit rewrite of dstdns's 13 hooks is V8-19's work.
