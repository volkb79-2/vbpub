# CIU — known issues, TODO, and backlog

This is CIU's temporary canonical product issue tracker. Consumer projects keep
only pointers to issues filed here. Once nyxloom's per-entry backlog schema is
available, these open entries move to that format and
`nyxloom-trove/backlog.md` becomes canonical.

Normative behavior belongs in [`docs/SPEC.md`](docs/SPEC.md). A FIXED issue
means code, behavioral tests, SPEC, and user documentation landed together. A
WITHDRAWN issue means the claimed product behavior was removed or never
adopted after its premise was disproved; it must not remain described as a
shipped capability.

Last updated: 2026-08-21 — **CIU-45 WITHDRAWN**, same day it was filed. dstdns's
own fresh adversarial code review of the P120 package that filed it reproduced
the actual failure from source and found it was a misdiagnosis: a missing
`provides` array in one dstdns stack (`infra/vault`), not a ciu limitation —
the `post_compose`-hook-as-provider pattern this issue claimed was impossible
already ships in the same repo (`infra/consul-server`). Full disposition below
under `## CIU-45`; `dstdns/nyxloom-trove/decisions.md` D-170.

Previously, 2026-08-21 — **CIU-45 FILED, OPEN** from dstdns P120's O7 live
attempt (`dstdns/nyxloom-trove/reports/dstdns-P120-REPORT.md` §O7): `requires`
provisions rather than verifies, so a path a non-ciu hook provisions
out-of-band can never pass the static provisioning-graph lint. This is the
second dstdns config-wave upstream ask filed the same day as CIU-44 (both from
the same carve-review round, D-162).

Previously, 2026-08-20 — **CIU-41..43 FILED, OPEN** from dstdns P111's
Mode-B live pass (findings F2/F3/F4 in
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9): `ciu env generate`
silently inherits an ambient `DOCKER_NETWORK_INTERNAL` (CIU-41), no way to
express cross-profile `ASK_VAULT` producer dependencies (CIU-42), and
`ciu clean` leaves instance-scoped networks behind while reporting
`clean complete` (CIU-43).

Previously updated 2026-08-19 — merged from main: dstdns's five configuration/
landscape capability asks **CIU-34..38 FILED, OPEN** (renumbered from
CIU-29..33 on main because this branch had already allocated CIU-29; recorded
vbpub@b4d7c749), four of them carved as `ciu-P08..P11`
(`nyxloom-trove/handoffs/`, wave brief
`nyxloom-trove/ciu-config-wave-BRIEF-2026-08-19.md`); and assay's provenance
defect renumbered **CIU-28 → CIU-39** at this merge (this branch had also
independently allocated CIU-28 for worktree identity — assay-side references
updated the same day). Same day: **CIU-36 marked FIXED** by ciu-P08
(landscape_id validation + docs; S3.11) and **CIU-37 marked FIXED** by ciu-P09
(schema-validated configfile render; S5.7).

Last reconciled: 2026-08-17, automation-safe worktree lifecycle milestone.

## Current status

| ID | Summary | Severity | Status |
|---|---|---:|---|
| CIU-23 | PostgreSQL-specific worktree data-isolation provider was grounded in a false consumer premise | Medium | WITHDRAWN |
| CIU-25 | No grounded stale worktree/stack detector and explicit reap transaction | Low | OPEN — later milestone |
| CIU-26 | No live proof for CIU-23's PostgreSQL provider | Low | OBSOLETE |
| CIU-28 | Automation-safe worktree identity, allocation, adoption, and resume | Medium | FIXED — shipped `71f5ec79` (P04-P06), Assay-qualified in P07 (2026-08-20) |
| CIU-29 | Structured worktree control, capability discovery, exact up, and exact execution | Medium | FIXED — **P04–P06 SHIPPED** (S16.5–S16.7, checkpoint-B review 2026-08-19) + P07 qualification (2026-08-20), closes this row |
| CIU-34 | No `layout` object naming a host→bundles plan (dstdns config/landscape ask) | Medium | FIXED — `[deploy.layouts.<name>]` + `ciu up --layout` / `ciu layouts` (ciu-P10, S7.5c) |
| CIU-35 | No host-scoped home for pre-Vault local secrets (SSH bootstrap key, Tailscale authkey) | Medium | FIXED — `[deploy.hosts.<h>.secrets]` + `ciu host-secrets` (ciu-P11, S14.3a) |
| CIU-40 | Gate-layering refactor (estate D-110 + D-111): **`run-gate.py`** built as a vbpub mini-project (argparse, usage() with lane list + in-file revision, own tests for the docker/cgroup/pin-verify/clean-tree mechanics), reading a per-project **`run-gate.toml`** it alone parses (orchestration only; assay lanes reference `assay.toml` by name — judgment stays there); vbpub projects symlink it, external repos copy (revision = drift detector); nyxloom.toml [gates] becomes a thin argv pointer; overarching+project AGENTS.md name it the canonical entry IN the same carve; DE-VENDOR `tools/assay/*.pyz` once assay is baked into tester-unified from in-repo source (keep only the version pin) | Medium | OPEN — decided 2026-08-20 (dstdns D-110/D-111+amendment); **handoff READY: `run-gate-project/HANDOFF-P01-build-and-adopt-ciu.md`** (build tool + ciu adoption; de-vendor stays pending the assay image-bake) |
| CIU-36 | No `landscape_id` identity dimension | Low | FIXED — S3.11 validation + docs (ciu-P08, 2026-08-19) |
| CIU-37 | Rendered app config not validatable against an app-provided JSON schema | Medium | FIXED — S5.7 schema-validated render (ciu-P09, 2026-08-19) |
| CIU-38 | No per-service Vault AppRole provisioning/delivery | Medium | OPEN — consumer-side-first (dstdns D-106); stays as the upstreaming ask |
| CIU-39 | `provenance` adjudicates vendor images ciu never built → `verified-match` unreachable live (was CIU-28 on main; blocks assay B004) | High | OPEN |
| CIU-41 | `ciu env generate` silently inherits an ambient `DOCKER_NETWORK_INTERNAL`, so a fresh-worktree generate joins the MAIN stack's network (masked default; inconsistent with the S2.7 handling of `PHYSICAL_REPO_ROOT` in the same run) | Medium | OPEN — filed 2026-08-20 (dstdns P111 F2) |
| CIU-42 | No way to express that a stack's `ASK_VAULT` path is produced by another profile's provisioning — a partial profile selection (`core,db`) fails at the consuming stack with only the path name, not the missing producer | Low | OPEN — filed 2026-08-20 (dstdns P111 F3); doc-gap and mechanism-gap readings both presented |
| CIU-43 | `ciu clean` reports `clean complete` while leaving instance-scoped networks behind (workspace network + compose `*_default`); by-design per `action_clean`'s docstring, a leak for ephemeral Mode-B instances | Medium | OPEN — filed 2026-08-20 (dstdns P111 F4; reproduced, consumer-documented in dstdns GUIDE §3.3); SECOND reproduction 2026-08-21 on **6.3.0** (dstdns P116 O9, D-154 R5): exit 0 + `clean complete`, four instance-owned objects left incl. the bare-`<branch>-`-prefixed Vault volumes — so the volume pass is affected too, not only networks |
| CIU-44 | Templates cannot see the SELECTED profile/stack set at render time: `CIU_SERVICES_PROFILE` is unset on the `--profile` argv path (`cli.py:1005-1015`; `workspace_env.py:875-877` leaves it commented out), so a feature flag like reverse-proxy's `enable_pwmcp_mcp` cannot be derived from "is infra/pwmcp deployed" and any render-time precondition is unreachable or always-fails. Ask: expose the resolved deployed-stack set (or profile list) to the Jinja context so a template can fail loudly when it references an undeployed upstream (§4.2a) | Medium | OPEN — filed 2026-08-21 (dstdns P120 carve review B4, D-162 DA-B) |
| CIU-45 | ~~`requires` PROVISIONS rather than VERIFIES~~ — **misdiagnosis, see disposition below**. The lint rule is a plain `requires`/`provides` completeness check; a `post_compose` hook registering itself as a provider already ships (`infra/consul-server/ciu.defaults.toml.j2:9-17`). The actual dstdns failure was a missing `provides` array in one unrelated stack, fixed declaratively in-repo | — | **WITHDRAWN 2026-08-21** — see `## CIU-45` below for the full disposition; `dstdns/nyxloom-trove/decisions.md` D-170 |

The approved milestone decisions and serial package order are in
[`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) and
[`nyxloom-trove/roadmap.md`](nyxloom-trove/roadmap.md).

## How issues get here

Capture the originating observation, then re-verify it against the provider and
the alleged consumer before treating it as a product requirement. Every open
entry must state:

1. the observed mechanism and a live or source-grounded reproduction;
2. why CIU, rather than a consumer, owns the behavior;
3. the public contract and refusal states;
4. behavioral oracles including a controlled wrong implementation; and
5. the SPEC owner.

Defaults follow the estate rule: derive a fact, read its authoritative source,
or fail. Do not use a literal or ambient value as a substitute for information
available from the selected worktree or its configuration.

## CIU-23 — withdraw PostgreSQL-specific data isolation

**Disposition:** WITHDRAWN on 2026-08-17. Package A removed the implementation
after re-verification disproved its consumer premise.

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** verbatim recurrence in the very next package — `ciu env generate` inherited main's `DOCKER_NETWORK_INTERNAL` and would have silently produced a Mode-A stack; caught only because the P111 write-up primed the operator's agent to check. Two consecutive packages → priority bump warranted.

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** `core,db` again could not start controller/webapp-server without the two identity-profile `ASK_VAULT` paths; resolved the same way (disposable placeholders in the instance's own Vault, disclosed). Two consecutive packages → priority bump warranted.

### What was claimed

CIU-23 claimed that dstdns's `scripts/schema-gate.sh` demonstrated the need for
a uniquely named database on a shared PostgreSQL server. CIU consequently
shipped:

- `worktree add --data-isolation <profile>`;
- `DataIsolationProvisioner` and a default `PostgresProvisioner`;
- `CIU_DATA_ISOLATION_ENTITY`, `CIU_DATA_ISOLATION_PROFILE`, and
  `CIU_DATA_ISOLATION_DSN`; and
- S16.2 create/drop ordering around `worktree rm`.

### Re-verification

The premise was false when the issue was filed. dstdns committed its schema
gate on 2026-08-10, one day before CIU-23, and that original commit explicitly
uses a disposable PostgreSQL container. It rejects a scratch database on the
existing instance because obtaining CREATEDB requires the PostgreSQL superuser
and would put gate activity and deployed data in one blast radius.

No estate consumer uses the CIU flag, provider classes, or emitted environment
fields. The implementation is not a general data-slot abstraction: its profile
is a local Docker container name, it assumes the `postgres` administrative
user, it creates/drops a database without applying consumer schema, and its DSN
does not establish the connectivity/authentication facts a consumer needs.

### Required withdrawal

Remove the CLI flag, provider protocol/default, env fields, create/drop paths,
tests that assert the withdrawn behavior, and S16.2 user-facing contract. Keep
the historical record in Git/release history and state plainly in migration
notes that the next release removes a recently shipped but unused API.

A future general provision/drop hook requires a real consumer and a new issue;
it must be project-declared rather than a PostgreSQL default embedded in CIU.

**SPEC ownership:** remove S16.2 and reserve no replacement behavior.

### CIU-34..38 detail: five asks from dstdns's configuration/landscape decision — OPEN

**Filed by:** dstdns controller session, 2026-08-19, out of the configuration /
landscape / remote-deployment decision recorded in
`dstdns/docs/spec-configuration-and-landscape.md` (D-094…D-101). Per this file's
rule the asks live here; dstdns keeps only the pointer (spec §11). None is a
defect in shipped behaviour — each is a capability the decided model needs from
its deploy tool. Verified against `docs/CONFIG.md` + `src/` before filing (a
feature dstdns has not adopted is not a feature ciu is missing).

**CIU-34 — `layout`.** **FIXED** on 2026-08-19 (ciu-P10): `[deploy.layouts.<name>]`
now names a host→bundles plan plus the deployment's `environment`
(closed `dev|test|staging|prod`, the durable home of the environment value —
dstdns D-105 Q2). `ciu up --layout <name>` resolves + validates the layout
(unknown layout / bad `environment` / unknown bundle / unknown host / empty
hosts table → tagged `[S7.5c]` abort before any transport opens), then drives
the SPEC-J push (S14.2) to each host in declaration order with
`CIU_SERVICES_PROFILE` set to the host's bundles and
`CIU_LAYOUT` / `CIU_LAYOUT_HOST` / `CIU_DEPLOY_ENVIRONMENT` exported to the
remote command; a host failure aborts naming the not-yet-deployed remainder.
`--layout` is mutually exclusive with `--host`/`--profile`/`--dir`/`--thin`/
`--bootstrap`/`--rollback` (prefix-aware, so `--profile=core` is caught too —
see checkpoint C below); `ciu layouts` lists declarations. Evidence:
`Layout`/`resolve_layout`/`list_layouts` in `src/ciu/deploy_pkg/layouts.py`
(18 model tests in `tests/tests/test_ciu_deploy_layouts.py`, 19 CLI tests in
`tests/tests/test_ciu_cli_layouts.py` — fake ssh seams only, no live
transport); venv run (`.venv/bin/python run-ciu-tests.py`), 100% line+branch
— the iteration signal, not the ship gate; tester-unified gate run by the
controller at checkpoint review. Docs: SPEC S7.5c, CONFIG.md
`[deploy.layouts.<name>]` section, CHANGES.md. **Checkpoint C review
(2026-08-20)** found and fixed 3 blocking findings against the original
ciu-P10 merge: an empty `bundles = []` list was accepted and resolved to
"deploy every phase" on the remote (`resolve_profiles`' empty-list fallback,
the same shape as the 2026-07-16 dstdns incident) instead of being refused;
the `--host`/`--profile` mutual-exclusion check missed the `--profile=core`
equals form and didn't guard `--dir`/`--thin`/`--bootstrap`/`--rollback` at
all; and the push implementation was duplicated between `--host` and
`--layout` (already drifted — the layout path lacked the `docker_optional`
advisory) and is now one shared `_push_host` helper. Pre-checkpoint-C baseline
was 16 model / 12 CLI tests, not the 14 model tests this row previously
claimed (the P10 LOG's own count of 13 CLI tests was also off by one — see
its appended correction note); the 18/19 above are current-tree totals after
checkpoint C's added tests.

**CIU-35 — host-scoped local secrets.** **FIXED** on 2026-08-19 (ciu-P11):
`[deploy.hosts.<h>.secrets]` now holds `ASK_EXTERNAL`/`GEN_LOCAL` entries
(SSH bootstrap key, Tailscale single-use authkey) resolvable *before* any
Vault exists on the target, later movable to Vault by the existing
directives. Entries are parsed with the existing `directives.parse_value`
(read-only) and only the two kinds are accepted at host scope — any other
directive is a tagged `[S14.3a]` error naming host+entry+reason.
`materialize_host_secrets` persists under the project store's
`hosts/<host>/<entry_name>` namespace (0700 dirs, atomic write, flock; the
per-stack global-uniqueness rule S4.6 deliberately does not apply across host
namespaces). `get_host` validates the subtable but pops it before return —
transport callers never see directives. `ciu host-secrets <host>
[--materialize | --list | --path NAME] [-y]` is explicit-only and never
prints values; nothing materializes implicitly inside `ssh`/`up --host`.
Evidence: 32 tests in `tests/tests/test_ciu_host_secrets.py` (fake seams,
tmp_path stores; closed-kind refusal, pop-before-return, store namespace,
resolution order, no-value-printing, no implicit materialization); venv run
(`.venv/bin/python run-ciu-tests.py`), 100% line+branch — the iteration
signal, not the ship gate; tester-unified gate run by the controller at
checkpoint review. Docs: SPEC S14.3a, CONFIG.md section + pre-Vault rationale
+ worked example, CHANGES.md. Also documented: the `CIU_SECRET_<NAME>` env
override is NOT host-scoped — the same exported value lands in every host's
namespace (known limitation, unsafe for single-use keys). **Checkpoint C
review (2026-08-20)** found and fixed 1 blocking finding (P11-B1): a pasted
value instead of a directive (e.g. a Tailscale authkey typo'd into
`[deploy.hosts.<h>.secrets]`) flowed verbatim into
`directives.parse_value`'s "[S4.2] Unknown directive '<token>'" message,
which `hosts.py` re-raised unchanged and the CLI printed to stderr — from
every `get_host()` caller, not just `ciu host-secrets`. `hosts.py` now raises
a fixed, non-leaking `[S14.3a]` reason instead of interpolating the upstream
message. Pre-checkpoint-C baseline was 31 tests, not the 30 this row
previously claimed (the P11 LOG's own count of 31 was already correct); the
32 above is the current-tree total after checkpoint C's added test.

**CIU-36 — `landscape_id` dimension.** A first-class identity value (beside
project/instance) exposed to templates and to S16 worktree instances, so a
consumer can render its Consul KV root (`dstdns/<landscape_id>/…`) and mesh ACL
tags from one source. **FIXED** on 2026-08-19 (ciu-P08): `[deploy].landscape_id`
is now validated as a DNS-label-safe slug (`^[a-z][a-z0-9-]{0,62}$`) on the
final merged global config (incl. the worktree overlay) with a tagged S3.11
abort, and documented in CONFIG.md + SPEC.md S3.11 with an explicit
disambiguation from the configfile-context `instance_id` (a per-service replica
index, not the workspace `INSTANCE_ID`). Evidence: 6 behavioral tests in
`tests/tests/test_ciu_config_model_landscape.py`; gate 100% line+branch.
Templates read it via `{{ deploy.landscape_id }}` with no plumbing change.

**CIU-37 — schema-validated render.** `[<root>.<service>.configfile.app]` (or the
render step) accepts `schema = "path/to/config-schema.json"` and validates the
rendered TOML against it, failing the render with the key path — the app's
generated JSON schema is the source, ciu only checks. **FIXED** on 2026-08-19
(ciu-P09): optional `schema` key per configfile entry, validated against the
app's JSON Schema (Draft 2020-12, TOML targets only) immediately after the
atomic write and before mount emission; violation names service, configfile
(per-instance suffix when `instances > 1`), and key path, and removes the
invalid rendered file. `jsonschema` is an optional extra (`ciu[schema]`);
declared schemas fail loudly when it is absent, never silently skip. Evidence:
10 behavioral tests in `tests/tests/test_ciu_configfile_schema.py`; gate 100%
line+branch. Runs on the up/dev path (engine step 12) — `ciu render` renders
TOML configs only; a dedicated `ciu render --configfiles` verb remains a
possible follow-up candidate (not in this package).

**CIU-38 — per-service AppRole.** Vault stack provisions one AppRole + policy per
declared service and a template helper delivers `role_id` + a `secret_id` file
path into that service's rendered config (no secret VALUES rendered). dstdns
decided runtime Vault fetch (SM2, D-098); if this lands upstream dstdns consumes
it, otherwise dstdns builds it locally and notes the delta here.

### CIU-39 detail
## CIU-25 — stale worktree/stack detection and reap

**Status:** OPEN, deliberately outside the current milestone.

`worktree rm` cleans before removing a checkout when it runs, but a crashed
dispatcher or forgotten teardown can leave containers and volumes running.
The old proposal to infer staleness from process lifetime or elapsed time is
not grounded: long-lived worktrees can be legitimate, and a missing process is
not proof that an instance is abandoned.

Before carving this issue, define an explicit ownership/lease signal and a
transactional reap contract. A future implementation must distinguish at least:

- registered and operator-owned;
- registered with an expired explicit lease;
- Git registration present but checkout path missing;
- Docker resources present but no CIU identity record; and
- a partially failed earlier cleanup.

It must not destroy resources based only on age, basename similarity, or a
missing local process. CIU-28's identity record is a prerequisite substrate,
not itself permission to reap.

**Proposed SPEC ownership:** S16.4 after a separate product decision.

## CIU-26 — deferred PostgreSQL proof

**Disposition:** OBSOLETE on 2026-08-17 because CIU-23 was withdrawn.

CIU-26 asked for a live PostgreSQL proof of the default provider. Building that
lane would validate an unused, incorrectly grounded abstraction. Package A
removed the provider. CIU-26 is therefore OBSOLETE, not FIXED: no
live-provider claim remains to prove.

## CIU-28 — automation-safe worktree identity and lifecycle

**Reported by:** nyxloom/vbpub, 2026-08-17, while qualifying CIU as an automated
environment provider.

### Observed gap

The current `worktree add NAME` conflates a logical identity, branch, directory
basename, and lookup key. It always creates a new branch, cannot adopt or resume
an existing checkout, and persists no durable lifecycle record. Runtime
`INSTANCE_ID` is only six SHA-256 hex characters derived from physical path;
collision checking currently occurs only in a later S16.3 deployment-cap path.

### Required contract

1. Preserve simple `worktree add NAME` behavior for people while separating
   logical name, display name, branch, Git worktree path, and CIU-root offset in
   the internal/public model.
2. Persist a schema-versioned, atomic, non-secret record at
   `<target-ciu-root>/ciu.worktree-instance.json`. It owns logical identity,
   allocation timestamp, requested Git/path facts, runtime identity, selected
   profile/shared-infra presence, and one closed lifecycle state:
   `allocating`, `ready`, or `recovery-required`. Current HEAD is derived, not
   frozen in the record. Credentials and DSNs are forbidden.
3. Add a sparse, non-secret, gitignored
   `<target-ciu-root>/ciu.global.worktree.toml.j2` merged after the committed
   global defaults and project override. It owns durable per-worktree global
   configuration, including selected service profiles and shared-infrastructure
   intent, and survives both `ciu clean` and `ciu env generate`. `ciu.env`
   returns to generated machine/runtime facts only; the lifecycle record does
   not become a second authority for overlay values.
4. Logical names are unique within one Git worktree family; independent clones
   may reuse them. Host runtime/network identities must still reject collision.
5. Support explicit create-new, adopt-existing, and idempotent ensure/resume.
   Create refuses an occupied identity before side effects. Adopt is the only
   operation allowed to take ownership of unmanaged state. Ensure reuses an
   exact ready match and completes only a mechanically recognizable interrupted
   CIU-owned allocation. Mismatch refuses; repair is explicit.
6. Generated display names use UTC
   `<prefix>-<YYYYMMDD_HHMMSS>-<feature-description>`. Prefix means project OR
   component, supplied by the caller. Generated branch and directory basename
   are exactly equal. Allocate under the Git-family lock and add a suffix only
   for an actual same-second collision. Resume retains the original name.
7. Before Git/env side effects, reject conflicting logical identity, target
   path, or active branch. After generating the target's own `ciu.env` but
   before marking ready, reject duplicate `INSTANCE_ID` or network identity.
   A partial attempt remains inspectable and cannot masquerade as ready.
8. Lifecycle operations provide schema-versioned JSON with closed status and
   recovery vocabularies. Human output remains presentation only.

### Behavioral oracles

- Two generated allocations sharing an injected UTC second receive distinct
  names under one family lock; a retry of either resolves its original record.
- Matching ensure creates no branch, directory, env, or record write. A
  one-field branch/path/logical mismatch refuses.
- A forced runtime-hash collision refuses before ready and leaves a declared
  recovery state, never two usable instances with one network identity.
- An existing unmanaged checkout is refused by ensure and accepted only by
  explicit adopt after all facts validate.
- Nested CIU roots retain the exact Git-root-to-CIU-root offset in every
  checkout; no code treats the Git root as the CIU root by convenience.
- Regenerating `ciu.env` and running `ciu clean` preserve the worktree overlay;
  the selected profiles/shared-infrastructure intent still resolve from it.

**SPEC ownership:** replace/extend S16 identity and lifecycle.

## CIU-29 — structured control and exact execution

**Reported by:** nyxloom/vbpub, 2026-08-17, from the same qualification.

### Observed gap

Lifecycle output is prose-only, there is no exact inspect result, and automation
must source `ciu.env` or infer features from SemVer. There is also no operation
that explicitly starts a selected worktree instance or executes in its exact
local/container environment without inherited sibling-root contamination.

### Required contract

1. Add versioned JSON for lifecycle, list, inspect, and remove. Report logical
   identity, display/branch/path facts, CIU-root offset, primary/detached state,
   lifecycle state, current Git revision, runtime ID/network, selected profile,
   and non-secret optional-feature presence. Partial failures name exact
   retained resources and a closed recovery status.
2. Add a versioned `ciu capabilities --json` document with closed identifiers
   for public machine contracts. Its schema version is independent of package
   SemVer. Consumers allowlist capabilities; unknown identifiers are not
   interpreted as compatible.
3. Add `ciu worktree up <logical-id>`. It resolves one managed record, parses
   that target's `ciu.env` by exact path, replaces conflicting inherited CIU
   root/identity variables, and invokes CIU's existing up path for that target.
   It is the explicit start operation.
4. Add `ciu worktree exec <logical-id> -- <argv>` for non-container consumers.
   It uses the selected checkout/CIU root and exact target env, no shell, and
   propagates the child exit code. It never starts or cleans anything.
5. Add `ciu worktree exec <logical-id> --target <alias> -- <argv>` for container
   consumers. Aliases are declared in project config and are the only
   automation selection surface; arbitrary service selection is forbidden.
   Each alias declares an exact stack, service, workdir, and optional
   `requires_worktree_mount = false` (default is true).
6. Resolve a target against the selected instance's exact Compose project,
   service label, and own network. Zero or multiple running containers refuse.
   When mount verification is enabled, inspect Docker mounts and prove the
   selected worktree maps to the declared container workdir before execution.
   Invoke `docker exec` without a shell and propagate the exact exit code.
7. No exec mode implicitly invokes `up`. Nyxloom requires a container alias for
   cockpit-doctrine projects; CIU retains local exec for non-container users.
8. Every command accepts/resolves an explicit CIU root and reports it in JSON.
   Ambient `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, network, instance, and profile
   values from a sibling checkout cannot redirect the selected operation.

### Boundary with Assay and workflow tools

CIU owns WHERE: exact checkout, generated environment, stack/container, and
argv transport. Assay owns evidence judgment. Nyxloom owns workflow policy and
the decision to require container targets. CIU neither imports Assay nor parses
its verdict. A caller may run a pinned Assay artifact as the child argv.

### Behavioral oracles

- Similar basenames and stale ambient root variables cannot redirect inspect,
  up, local exec, or container exec.
- Missing/malformed record or env refuses before any child or Docker mutation.
- Local and container argv preserve argument boundaries and representative exit
  codes without `shell=True`.
- Target alias absence, zero/multiple label matches, wrong instance network,
  and wrong/missing worktree mount each refuse before payload execution.
- Capability output changes only with a reviewed public contract and contains
  no inferred future compatibility.

**SPEC ownership:** S16 machine interface and versioned capability schema; S17
continues to own provenance semantics.

## CIU-41 — `ciu env generate` silently inherits an ambient `DOCKER_NETWORK_INTERNAL`

**Filed by:** dstdns P111 (auth-config-cutover, Mode-B live pass), 2026-08-20.
Provenance: `dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9 F2 and §11
item 5. Reproduced live, then source-confirmed against `src/ciu/workspace_env.py`
before filing.

### Observed mechanism and reproduction

In the dstdns devcontainer, `~/.bashrc` sources the MAIN checkout's `ciu.env`,
so every shell — interactive and agent alike — carries
`DOCKER_NETWORK_INTERNAL=dstdns-98535c-network`. Running `ciu env generate` in a
fresh worktree (`/workspaces/dstdns/.worktrees/p111-auth-cutover`):

- derived a correct fresh `INSTANCE_ID` (`e893b0`) from the physical-path hash;
- but **kept the ambient network name**: the generated `ciu.env` carried the
  MAIN stack's `dstdns-98535c-network` instead of the derived
  `p111-auth-cutover-e893b0-network`.

Net effect: an intended Mode-B (own-network) instance silently becomes a Mode-A
attach — containers running worktree code join the main stack's network. The
SAME run handles exactly this contamination species correctly for
`PHYSICAL_REPO_ROOT` (S2.7 refined precedence: a pre-set env value wins only
when consistent with the mountinfo-derived value, else the derived value is
used and a stderr warning names the ignored ambient one), so the handling is
internally inconsistent.

Source mechanism: `_compute_network_name` (`src/ciu/workspace_env.py`, ~line
563) returns
`os.environ.get("DOCKER_NETWORK_INTERNAL", network_name)` — the ambient value
wins unconditionally, with no consistency check and no warning. Parallel bare
env reads exist at ~761/931/974. This is the **masked default** anti-pattern
(dstdns AGENTS §4.2a #3): invisible in every interactive shell because the
ambient value is correct for the main checkout, surfacing only in the one
context where it matters — a generate for a different workspace — which is
exactly the non-interactive agent context. It is also the same defect family
S2.7's docstring records for `PHYSICAL_REPO_ROOT` (the 2026-07 dstdns→nyxloom
leak); the network name never received the fix.

**Workaround used in the field:**
`env -u DOCKER_NETWORK_INTERNAL -u INSTANCE_ID -u PHYSICAL_REPO_ROOT -u REPO_ROOT -u REPO_NAME ciu env generate`
→ correct `p111-auth-cutover-e893b0-network`.

### Why CIU owns it

`env generate` is the identity-computation verb; its output is the record every
later ciu command trusts (S16's worktree cross-checks compare the record
AGAINST `ciu.env`, so a contaminated generate poisons the identity at birth and
the cross-checks then defend the wrong value). A consumer cannot fix this by
documentation: any consumer whose login shell sources a checkout's `ciu.env` —
the documented convenience pattern — has the ambient value in every derived
shell.

### Proposed contract

Extend the S2.7 refined-precedence pattern from `PHYSICAL_REPO_ROOT` to the
derived identity tuple (`REPO_NAME` / `INSTANCE_ID` /
`DOCKER_NETWORK_INTERNAL`) during `env generate`: a pre-set value wins ONLY
when consistent with the value derived for THIS repo root; on mismatch, use the
derived value and warn on stderr naming the ignored ambient value. (Stricter
alternative reading: generate's entire job is computing fresh identity, so it
ignores ambient identity values outright and takes overrides only via explicit
flags; ambient-env precedence would remain for the read path of
already-generated workspaces.)

### Oracles

- Generate in a worktree with the main instance's `DOCKER_NETWORK_INTERNAL`
  exported → generated `ciu.env` carries the derived
  `<repo>-<instance>-network`, and a warning names the ignored ambient value.
- Generate with a consistent pre-set value (equal to derived) → silent, output
  unchanged.
- Controlled wrong implementation: restoring the bare
  `os.environ.get(..., derived)` fallback must fail the first oracle.

**SPEC ownership:** S2 (workspace environment), extending S2.7's precedence
contract to the derived identity values.

## CIU-42 — cross-profile `ASK_VAULT` producers are inexpressible; partial profile selections fail with only the path name

**Filed by:** dstdns P111 (Mode-B live pass), 2026-08-20. Provenance:
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9 F3 and §11 item 4.

### Observed mechanism and reproduction

A Mode-B instance deployed with `CIU_SERVICES_PROFILE="core,db"` (per dstdns
GUIDE §3.4b), then incrementally
`ciu up --dir applications/{controller,webapp-server}`. Both app stacks declare
`ASK_VAULT` secrets that only the `identity` profile's provisioning (or a hook)
writes — `authentik/bootstrap_token` (controller) and
`vault/webapp-server/token` (webapp-server) — and `ASK_VAULT` correctly refuses
when the path is absent. So a `core,db` selection cannot start the app tier at
all, and the refusal names the missing *path* but not its *producer*. The
identity profile is multi-GB (Authentik) and the host had 2.9 GB available, so
"just deploy identity" was not an option. Resolved in the field by seeding both
paths in the INSTANCE's own Vault with disposable placeholders — consistent
with the stacks' own comments ("an identity-less deploy has nothing for this
check to protect") and touching no shared state.

### Two readings — both presented deliberately

1. **Doc gap.** Partial-profile selections that exclude a producing profile are
   simply unsupported without manual seeding; then ciu's documentation
   (CONFIG.md, secrets × profiles interaction) should say so and describe the
   placeholder-seeding recipe as the sanctioned pattern. (dstdns is folding the
   recipe into its own GUIDE §3.4b regardless — the consumer-side half of this
   finding.)
2. **Mechanism gap.** A stack cannot DECLARE that an `ASK_VAULT:<path>` is
   produced by another profile's provisioning. With such a declaration (e.g. a
   `produced_by = "<profile>"` annotation beside the directive, or an S13
   typed reference), `ciu up` under a partial selection could refuse UPFRONT
   naming the missing producer — "`authentik/bootstrap_token` is provisioned
   by profile `identity`, which is not in your selection; deploy it or seed
   the path" — instead of failing at the individual consuming stack with only
   the bare path. S13 (`requires`/`provides`) already has the right vocabulary
   shape.

### Why CIU owns it

`ASK_VAULT`'s refusal contract and profile selection are both ciu's contracts;
the consumer can document around their interaction but cannot express the
dependency to the tool.

### Oracles (mechanism reading)

- A partial selection missing a declared producer refuses pre-deploy, naming
  producer profile + path + the seeding alternative.
- A selection including the producer profile is unaffected.
- An undeclared `ASK_VAULT` keeps today's behavior exactly.
- Controlled wrong implementation: dropping the declaration lookup regresses to
  the bare-path refusal and must fail the first oracle.

**SPEC ownership:** S4 (`ASK_VAULT` refusal contract) + S13 (declaration) if
the mechanism reading is chosen; CONFIG.md only if the doc reading is chosen.

## CIU-43 — `ciu clean` leaves instance-scoped networks behind while reporting `clean complete`

**Filed by:** dstdns P111 (Mode-B live pass teardown), 2026-08-20. Provenance:
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §8.3 and §9 F4. Reproduced
live; source-checked against `src/ciu/deploy.py` before filing. dstdns GUIDE
§3.3 has carried this consumer-side for a while ("a success message is not
sufficient"); this filing moves it upstream.

### Observed mechanism and reproduction

After a full Mode-B pass (instance `e893b0`, container/volume prefix
`p111-auth-cutover-e893b0-`), `ciu clean -y` from the worktree under its own
`ciu.env` printed `clean complete`. Leftover check by exact instance prefix:

- containers: none. instance-prefixed volumes: none.
- networks: **two remained** — `p111-auth-cutover-e893b0-network` (the
  workspace network `env generate`/`up` creates via `ensure_workspace_network`)
  and `p111-auth-cutover-e893b0-vault_default` (the compose-created default
  network of the vault stack).

Manual fallback per dstdns GUIDE §3.3: disconnect the named
`dstdns-devcontainer-vb` endpoint from the first network, then
`docker network rm` on both fully-resolved names — after which zero objects
with the prefix remained.

Secondary observation from the same teardown (D-130 amendment in the REPORT):
the named Vault volumes `p111-auth-cutover-vault-{data,logs}` carry the
PROJECT prefix (`<branch>-vault-*`), not the instance prefix, and also
survived — worth checking whether `action_clean`'s project-prefixed volume
pass covers nested/sibling compose projects' volume naming, though the
headline of this issue is networks.

### Design-vs-regression — both readings

`action_clean`'s own docstring (`src/ciu/deploy.py`, ~line 1679) says the
network survival is deliberate: "Network removal is NOT performed (v1 had no
explicit --clean-networks; the network is left in place)." That is defensible
for the long-lived MAIN workspace, whose network the devcontainer itself stays
connected to. It is wrong for ephemeral Mode-B worktree instances: every
instance creates identity-scoped networks that nothing ever removes, so they
accumulate one teardown at a time — and `clean complete` overstates what
happened either way. Note CIU-19 ("instance-scoped cleanup", FIXED, S6.4)
covered containers and volumes; networks were left outside its scope. And even
granting the deliberate-keep reading, the compose-created `*_default` network
is not covered by the stated v1 rationale at all — `docker compose down`
normally removes the networks it created, so its survival suggests the
per-stack reset path isn't reaching compose's own network cleanup (plausibly
because step 1 already force-removed the containers, or because an external
endpoint — the cockpit — pinned it).

### Proposed contract

A full `ciu clean` removes the identity-scoped networks it (or its compose
runs) created: disconnect lingering endpoints it can name (or refuse, naming
them), then remove. If keeping the invoking workspace's own network is desired
(the devcontainer-residence case), keep it explicitly and SAY so — the success
message must name anything deliberately left behind, never claim `clean
complete` over surviving identity-scoped objects. A `--clean-only-networks` /
`--keep-network` flag pair is one shape; unconditional removal for S16 worktree
instances plus keep-with-notice for the main workspace is another.

### Oracles

- Mode-B instance `up` → `clean` leaves ZERO Docker objects carrying the
  instance identity (containers, volumes, networks — including compose
  `*_default` names).
- `clean` with the devcontainer still connected to the instance network either
  disconnect-then-removes or refuses naming the endpoint — never silently
  keeps.
- Main-workspace `clean` that deliberately keeps the workspace network names it
  in output.
- Controlled wrong implementation: restoring today's no-network-removal path
  must fail the first oracle.

**SPEC ownership:** S6.4 cleanup semantics.

## CIU-45 — WITHDRAWN: `requires` does not "provision rather than verify"

**Disposition:** WITHDRAWN on 2026-08-21, one day after filing. The finding itself is void — not
superseded, not already fixed, but based on a misdiagnosis that a second, independent reproduction
disproved.

**What was claimed:** that ciu's provisioning-graph lint demands a declarative `GEN_TO_VAULT` row
for every `requires` entry, so a Vault path minted entirely out-of-band by a `post_compose` hook
(never by a `GEN_TO_VAULT` directive) could never satisfy it — and that no mechanism exists for a
`post_compose` hook to register itself as a provider in the graph.

**Why it's false:** `ciu/src/ciu/provisioning.py:90-113`'s lint rule is "every `requires` ref
appears in some stack's `provides` array" — a plain declarative string list in a stack's own
`ciu.defaults.toml.j2`, unrelated to `GEN_TO_VAULT`/secret-directive machinery. A `post_compose`
hook registering itself as a provider is not a missing capability; it is the SHIPPED, already-used
pattern at `infra/consul-server/ciu.defaults.toml.j2:9-17` in the very consumer repo that filed this
issue — a hook mints per-service Vault tokens and the stack declares `provides = ["vault:secret/
consul/<svc>/token", ...]` alongside it. dstdns P120's actual failure was that a DIFFERENT stack
(`infra/vault`) simply never added its own `provides` array for the AppRole credentials its hook
mints — a six-line, in-repo, declarative omission, not a ciu gap. No runtime provisioning was ever
attempted in the original reproduction (`docker ps -a` showed zero containers); the static preflight
lint had already refused before any hook ran, so "provisions rather than verifies" was never
actually observed, only inferred.

**Reproduction that found this:** dstdns's own fresh adversarial code reviewer, dispatched blind
against the P120 package that filed this issue, independently re-derived the failure from source
(`provisioning.py`) rather than trusting the original report, proved the six-line fix restores
`ciu check` to green on every profile, and named the exact in-repo precedent above. Full account:
`dstdns/nyxloom-trove/decisions.md` D-170.

**Lesson for future filings:** a "ciu structurally cannot express X" claim needs an actual grep for
the mechanism named in ciu's own error text before it is trusted, not just confirmation that a
predicted refusal occurred. The consumer's own repo already had two working examples of the pattern
this issue claimed was impossible.

## Compact resolved index

Detailed history for closed work lives in the normative SPEC, release notes,
archived handoffs/reports, and Git history rather than this active tracker.

| IDs | Disposition | Normative/result pointer |
|---|---|---|
| CIU-1 | NOT A GAP | S5 already supplied config render/mount behavior |
| CIU-2–CIU-8 | FIXED and released | S3.1a, S4.20, S5.3, S5a, S6.4, S9.3, S10.4 |
| CIU-9 | FIXED | S6.4 DooD physical-path removal |
| CIU-10 | FIXED | S2 workspace-root reconciliation |
| CIU-11 | FIXED | S1.2 standalone-root enforcement |
| CIU-12 | FIXED | S14.6 docker-optional push/activate |
| CIU-13 | FIXED | S15.10 governance merge |
| CIU-14–CIU-15 | FIXED | S15.11 logical-vs-physical KSM validation |
| CIU-16–CIU-17 | FIXED | CLI version verb and KSM override documentation |
| CIU-18 | FIXED | S17.2 provenance enforcement |
| CIU-19 | FIXED | S6.4 instance-scoped cleanup |
| CIU-20 | FIXED | S17.3 structured provenance verdict |
| CIU-21 | FIXED | S17.4 in-container image revision |
| CIU-22 | FIXED | S16.1 shared-infrastructure join |
| CIU-24 | FIXED | S16.3 worktree concurrency budget |
| CIU-27 | FIXED | S17.2 explicit no-preflight break-glass behavior |
| CIU-36 | FIXED | S3.11 `[deploy].landscape_id` validation + docs |
| CIU-37 | FIXED | S5.7 schema-validated configfile render (`ciu[schema]` extra) |

`CIU-COMMENT-ENV` is fixed under S3.2: environment expansion ignores TOML
comments while preserving comment text.
