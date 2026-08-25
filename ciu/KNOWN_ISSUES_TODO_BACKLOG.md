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

Last updated: 2026-08-22 — backlog wave on
`feat/ciu-backlog-wave-39-42-46-47` shipped four fixes and CIU-25's git
half: **CIU-39 FIXED** (declared vendor baseline `[deploy.provenance]
vendor_images`; provenance documents now `schema_version: 2`; `verified-
match` reachable live — unblocks assay B004), **CIU-42 FIXED** (mechanism
reading adopted: `produced_by` ASK_VAULT producer declaration, S13.6
upfront refusal), **CIU-46 FIXED** (cutover per operator decision: the
basename fallback is WITHDRAWN — config-less shipped/reset deployments use
the workspace-identity compose project `{REPO_NAME}-{INSTANCE_ID}-{stack}`;
BREAKING, one-time migration in CONSUMERS §11), and **CIU-47 FIXED**
(S2.7 refined precedence extended to `PUBLIC_FQDN`). **CIU-25 partially
addressed**: `ciu worktree branches` ships the grounded GIT-layer survey +
prune (S16.8, `worktree.branches.v1`); the Docker-resource detector/reap
contract remains OPEN below. An independent three-reviewer adversarial pass
over the whole wave then landed hardening commits (`2a6176d4`..`040df76e`):
the branch-prune half-prune blocker, produced_by deployed-stack semantics,
Docker-canonical provenance comparison, shipped-mode root resolution, and
docs/example repairs — all gate-green at `040df76e`.

Previously, 2026-08-22 — released as **ciu-v6.4.0** (tag + wheel published;
CHANGES §[6.4.0]): CIU-41, CIU-43, CIU-44 and their adversarial-review repairs
went through the tester-unified gate and shipped. Backlog reconciliation the
same day: status table re-sorted by ID; two dstdns-P112 second-reproduction
notes that had been appended to CIU-23's WITHDRAWAL section relocated to
CIU-41 and CIU-42 where they belong; the dangling empty `### CIU-39 detail`
heading filled with a real stub. **CIU-46 FILED, OPEN** — the S6.4a residual:
a shipped stack under the legacy compose-project fallback escapes clean's
network/volume enumeration. **CIU-47 FILED, OPEN** — `PUBLIC_FQDN` ambient
adoption during generate, the masked-default family CIU-41 fixed for the
identity tuple.

Previously, 2026-08-21 — **CIU-41, CIU-43, CIU-44 marked FIXED** by the
consumer-wave branch `feat/ciu-consumer-wave-41-43-44`: S2.7 refined
precedence extended to the derived identity tuple during generate (CIU-41),
S6.4a identity-scoped network removal + compose-label volume pass with the
instance-vs-main split (CIU-43), and S3.12 deployment-selection facts in the
render/hook context (CIU-44). **CIU-45 WITHDRAWN**, same day it was filed.
dstdns's
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
| CIU-25 | No grounded stale worktree/stack detector and explicit reap transaction | Low | PARTIAL — git half SHIPPED (`ciu worktree branches`, S16.8 + `worktree.branches.v1`, 2026-08-22), **HOTFIXED 2026-08-25 (ciu-P28): four reproduced prune-safety defects in the released behaviour, see detail**; Docker-resource detector/reap remains OPEN (see detail) |
| CIU-26 | No live proof for CIU-23's PostgreSQL provider | Low | OBSOLETE |
| CIU-28 | Automation-safe worktree identity, allocation, adoption, and resume | Medium | FIXED — shipped `71f5ec79` (P04-P06), Assay-qualified in P07 (2026-08-20) |
| CIU-29 | Structured worktree control, capability discovery, exact up, and exact execution | Medium | FIXED — **P04–P06 SHIPPED** (S16.5–S16.7, checkpoint-B review 2026-08-19) + P07 qualification (2026-08-20), closes this row |
| CIU-34 | No `layout` object naming a host→bundles plan (dstdns config/landscape ask) | Medium | FIXED — `[deploy.layouts.<name>]` + `ciu up --layout` / `ciu layouts` (ciu-P10, S7.5c); **HOTFIXED 2026-08-25 (ciu-P29)** — the mutual-exclusion guard was abbreviation-blind and could silently deploy the wrong profile to every host, see the CIU-34 detail below |
| CIU-35 | No host-scoped home for pre-Vault local secrets (SSH bootstrap key, Tailscale authkey) | Medium | FIXED — `[deploy.hosts.<h>.secrets]` + `ciu host-secrets` (ciu-P11, S14.3a) |
| CIU-36 | No `landscape_id` identity dimension | Low | FIXED — S3.11 validation + docs (ciu-P08, 2026-08-19) |
| CIU-37 | Rendered app config not validatable against an app-provided JSON schema | Medium | FIXED — S5.7 schema-validated render (ciu-P09, 2026-08-19) |
| CIU-38 | No per-service Vault AppRole provisioning/delivery | Medium | OPEN — consumer-side-first (dstdns D-106); stays as the upstreaming ask |
| CIU-39 | `provenance` adjudicates vendor images ciu never built → `verified-match` unreachable live (was CIU-28 on main; blocks assay B004) | High | FIXED — `[deploy.provenance] vendor_images` declared baseline; running reference equal to a declaration is `vendor-pinned`, same-name/different-reference is vendor drift (`mismatch`); verdicts at `schema_version: 2`; digest pinning deliberately not built (S17.5, DESIGN-GUIDE; 2026-08-22). Unblocks assay B004's verified half |
| CIU-40 | Gate-layering refactor (estate D-110 + D-111): **`run-gate.py`** built as a vbpub mini-project (argparse, usage() with lane list + in-file revision, own tests for the docker/cgroup/pin-verify/clean-tree mechanics), reading a per-project **`run-gate.toml`** it alone parses (orchestration only; assay lanes reference `assay.toml` by name — judgment stays there); vbpub projects symlink it, external repos copy (revision = drift detector); nyxloom.toml [gates] becomes a thin argv pointer; overarching+project AGENTS.md name it the canonical entry IN the same carve; DE-VENDOR `tools/assay/*.pyz` once assay is baked into tester-unified from in-repo source (keep only the version pin) | Medium | FIXED — estate-wide adoption landed on vbpub main (`4c6eb2b6`, 2026-08-22); ciu's lane is now `./run-gate.py ciu`; de-vendor stays pending the assay image-bake |
| CIU-41 | `ciu env generate` silently inherits an ambient `DOCKER_NETWORK_INTERNAL`, so a fresh-worktree generate joins the MAIN stack's network (masked default; inconsistent with the S2.7 handling of `PHYSICAL_REPO_ROOT` in the same run) | Medium | FIXED — S2.7 refined precedence extended to the derived identity tuple (`REPO_NAME`/`INSTANCE_ID`/`DOCKER_NETWORK_INTERNAL`): ambient adopted only when consistent; mismatch → derived value + stderr warning naming the S16.1 `--shared-infra` remedy; post-generate bootstrap steps parse the just-written file by exact path (2026-08-21). Follow-up candidate (same contamination family, out of filed scope): PUBLIC_FQDN ambient adoption at generate time
| CIU-42 | No way to express that a stack's `ASK_VAULT` path is produced by another profile's provisioning — a partial profile selection (`core,db`) fails at the consuming stack with only the path name, not the missing producer | Low | FIXED — mechanism reading adopted: ASK_VAULT-only inline key `produced_by = "<profile>"`; a partial selection excluding the producer refuses upfront naming every unmet tuple (stack, secret, path, producer, selection, both remedies); unknown profile names are configuration errors; undeclared secrets keep today's behavior (S13.6, 2026-08-22) |
| CIU-43 | `ciu clean` reports `clean complete` while leaving instance-scoped networks behind (workspace network + compose `*_default`); by-design per `action_clean`'s docstring, a leak for ephemeral Mode-B instances | Medium | FIXED — S6.4a: clean removes the identity network (read from this workspace's own ciu.env) + each selected stack's `<compose-project>_default`, disconnecting lingering endpoints first (unremovable endpoint named, clean fails); instance-vs-main split (S16 record ⇒ unconditional, main keeps its workspace network but names the keep); post-clean invariant extended to networks from Docker STATE; volume pass gains an exact compose-label enumeration catching bare-project-prefix volumes (the 6.3.0 second reproduction's `<project>-vault-*` leak). Controlled-wrong oracle: no-op network removal fails the invariant (2026-08-21). Known residual: shipped stacks run under run_shipped's legacy-project fallback when project/env tags are absent, which escapes the compose-label enumeration — needs the same gate or label pass if a shipped stack ever hits it (RESOLVED by CIU-46's cutover, 2026-08-22)
| CIU-44 | Templates cannot see the SELECTED profile/stack set at render time: `CIU_SERVICES_PROFILE` is unset on the `--profile` argv path (`cli.py:1005-1015`; `workspace_env.py:875-877` leaves it commented out), so a feature flag like reverse-proxy's `enable_pwmcp_mcp` cannot be derived from "is infra/pwmcp deployed" and any render-time precondition is unreachable or always-fails. Ask: expose the resolved deployed-stack set (or profile list) to the Jinja context so a template can fail loudly when it references an undeployed upstream (§4.2a) | Medium | FIXED — S3.12: `ciu.selected_profiles` + `ciu.deployed_stacks` in every deployment render's Jinja context (up/dev/render-toml/preflight/check/graph), computed once per invocation and threaded unchanged to hooks (`ctx.selected_profiles`/`ctx.deployed_stacks` + `ctx.instance_id`/`ctx.network` from the workspace's own ciu.env); omitted elsewhere so references fail loudly; nothing persisted into ciu.env or exported to compose env (2026-08-21) |
| CIU-45 | ~~`requires` PROVISIONS rather than VERIFIES~~ — **misdiagnosis, see disposition below**. The lint rule is a plain `requires`/`provides` completeness check; a `post_compose` hook registering itself as a provider already ships (`infra/consul-server/ciu.defaults.toml.j2:9-17`). The actual dstdns failure was a missing `provides` array in one unrelated stack, fixed declaratively in-repo | — | **WITHDRAWN 2026-08-21** — see `## CIU-45` below for the full disposition; `dstdns/nyxloom-trove/decisions.md` D-170 |
| CIU-46 | Shipped stacks run under the legacy directory-derived compose project when `deploy.project_name`/`environment_tag` are absent (`engine.py run_shipped`), and clean's S6.4a enumeration then sees no projects at all — legacy-project networks/volumes survive a reported-clean teardown | Low | FIXED — **cutover, BREAKING**: the basename fallback is WITHDRAWN; config-less shipped/reset deployments derive the workspace-identity project `{REPO_NAME}-{INSTANCE_ID}-{stack}` from THIS checkout's ciu.env (`engine.identity_compose_project_name`, exact-path parsed), and clean enumerates the SAME names via the compose-label passes; missing/identity-less ciu.env refuses instead of silently skipping; no `-p`-less compose invocation remains anywhere in ciu; the S16.1 cannot-derive join refusal fell out as unreachable and is withdrawn. One-time migration for pre-existing deployments: CONSUMERS §11 (S6.4a item 7 + S8.7, 2026-08-22). Follow-up candidates named by the adversarial review: (a) non-round-tripping stack dirnames refuse (Vault/vault collision class closed); (b) two DIFFERENT dirs with the SAME normalized basename still collide in config-less mode — documented limit, tagged naming is the escape hatch; a stack-path label stamp at up would close it |
| CIU-47 | `ciu env generate` adopts an ambient `PUBLIC_FQDN` with no consistency check (`workspace_env.py` bare reads) — the same masked-default family CIU-41 fixed for the identity tuple; a main checkout's sourced `ciu.env` leaks its FQDN into a fresh worktree's generated file | Low | FIXED — S2.7 refined precedence extended to PUBLIC_FQDN: derived from THIS workspace's own inputs first (config entry → reverse DNS of the detected IP); ambient adopted only when equal or when detection yields no sourced value (offline host keeps the operator override silently); on mismatch the derived value is written and a warning names the ignored one; PUBLIC_FQDN joins GENERATED_IDENTITY_KEYS so post-generate steps act on the written record. PUBLIC_IP/PUBLIC_TLS_* stay plain pre-set-wins (out of scope). Controlled wrong: restoring the bare fallback fails oracle 1 (2026-08-22) |

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

**CIU-34 hotfix (ciu-P29, 2026-08-25) — the mutual-exclusion guard was
abbreviation-blind; `--layout=NAME` never dispatched.** A retrospective
adversarial review reproduced a **silent wrong-profile production deploy** in
already-released behaviour (layouts shipped v6.3.0; every release since is
affected). **The claim this row made above — "`--layout` is mutually exclusive
with `--host`/`--profile`/`--dir`/`--thin`/`--bootstrap`/`--rollback`
(prefix-aware, so `--profile=core` is caught too)" — was true only for exact
and `=` spellings and is corrected here.** Checkpoint C made the guard
prefix-aware for the `=` form but left it a denylist of EXACT flag names,
while the remote parser it exists to protect (`deploy.parse_args`) is built
without `allow_abbrev=False`, i.e. with argparse's default
`allow_abbrev=True`. An abbreviation therefore passed the local guard, was
forwarded verbatim after the layout's own `export CIU_SERVICES_PROFILE=...`,
and resolved on the remote: `ciu up --layout prod --prof=core` against a
3-host prod layout exited **0** having pushed to all three hosts, with
`backend` (bundles `db,worker-io`) deploying `core`. The guard now registers
the forbidden long options on a local parser with the SAME `allow_abbrev`
semantics the remote uses and lets argparse resolve the spelling before the
check runs — every abbreviation length covered by construction, the resolved
flags consumed rather than forwarded, the refusal naming the resolved flag and
landing before any inventory lookup (zero transport). Separately, the
`"--flag" in argv` verb-dispatch tests missed every `=` form:
`ciu up --layout=NAME` skipped the layout path entirely, and — worse, because
`ciu-deploy` declares `--host` for its help text but never reads it (S10.2) —
`ciu up --host=web` (and `down`/`health`/`render`) parsed cleanly and ran a
LOCAL deploy of the active profile instead of the intended SPEC-J push, exit 0
and silent. Dispatch is now exact-or-`=` on `--layout`/`--host`/`--dir` via
one shared `_flag_given` predicate, **plus argparse-resolved abbreviations for
`--host`** — see the round-2 correction immediately below. Evidence:
`_flag_given` / `_parse_layout_argv` in
`src/ciu/cli.py`; 96 added tests in `tests/tests/test_ciu_cli_layouts.py`
(19 → **115** in that file across both rounds, superseding the "19 CLI tests"
count stated in the checkpoint-C paragraph above), including the review's exact
3-host reproduction
asserting ZERO transport calls, every abbreviation length of all six forbidden
flags in both forms, and `--layout=`/`--host=`/`--dir=` producing push
sequences identical to their space forms; venv run
(`.venv/bin/python run-ciu-tests.py`), 2714 passed, 100% line+branch — the
iteration signal, not the ship gate. Docs: SPEC S7.5c + S10.4, CHANGES.md.

**CIU-34 hotfix, round 2 (adversarial review REJECT, same day).** The round-1
fix above made dispatch exact-or-`=` and documented the claim *"an abbreviation
still fails loudly at whichever parser it reaches, so it can never deploy the
wrong thing."* **That claim was FALSE for `--host`, and this entry corrects
it.** Because `deploy.py` declares `--host` and reads it nowhere, `--hos=edge-a`
/ `--ho=edge-a` / `--hos edge-a` / `--ho edge-a` all parsed CLEANLY downstream,
had the host silently discarded, and ran a LOCAL deploy — **16 of the 20
verb × spelling combinations across `up`/`down`/`health`/`render` returned exit
0 having contacted zero remote hosts.** The `=`-only fix did not close the
hazard, it narrowed it. `--host` dispatch is now abbreviation-aware, resolved
by argparse (registering all three dispatch modifiers on one parser, so an
abbreviation ambiguous BETWEEN them would stay loudly ambiguous rather than be
claimed by whichever branch is tested first). `--layout`/`--dir` remain
exact-or-`=` **deliberately, and this is the corrected rule**: the question is
never "is an abbreviation possible" but "what does the fall-through parser DO
with it" — `ciu-deploy` has no `--layout`/`--dir`, so `--lay x` and `--di=/srv`
are `unrecognized arguments` and `--d /srv` is genuinely ambiguous there
against `--define-root PATH`; all fail loudly, and widening them would invent a
divergence rather than close one. That premise is pinned against the REAL
`deploy.parse_args` by
`test_dispatch_abbreviation_premise_against_the_real_deploy_parser`, plus a
distinct-second-character invariant test so a future colliding flag fails at
authoring time. The round-2 tests wrap the genuine `deploy.main`/`parse_args`
rather than a stub — a stubbed probe is vacuous here, since a dispatch
regression would hit the stub instead of performing the real local deploy, and
would mask both the bug and the fix.

**Follow-up filed by the round-2 review — `deploy.py` declares a flag it never
reads (root cause of the above), OPEN.** `deploy.py:3592` registers
`--host NAME` with the help text "Remote host name (from hosts inventory):
push-deploy via SSH (SPEC J)", and **nothing in the file ever reads
`args.host`** (grep confirms zero consumers). It exists only so the flag shows
up in `ciu-deploy --help`. That is what turned every `--host` dispatch miss
from a loud error into a silent local deploy: a "lying" flag that documents a
behaviour it does not implement. Fixing the DISPATCH layer in `cli.py` closes
the hazard for the real invocation surface — the `ciu` verb CLI — which is why
ciu-P29 stopped there (`deploy.py` was `scope.forbid` for that package). The
dead flag itself remains, and anyone invoking `ciu-deploy` directly still gets
a silently-ignored `--host`. A small future cleanup package should either make
`ciu-deploy --host` do what its help says or remove the declaration and point
at `ciu up --host`; it should NOT simply be deleted without checking S10.2's
help-surface contract. Lower urgency than the dispatch hazard, but it is the
root cause and should not be lost.

**Follow-up spotted, NOT fixed here (candidate for its own entry).** `ciu
bake`'s `--profile`-vs-positional-targets mutual exclusion (`_bake` in
`src/ciu/cli.py`) uses the same exact-or-`=` predicate this hotfix just
replaced in the layout guard: `any(a == "--profile" or
a.startswith("--profile=") ...)`. `ciu bake --prof=core web` therefore does
NOT trip the conflict; `--prof=core` is instead treated as a positional build
TARGET and handed to `docker buildx bake`. That is a loud failure rather than
a silent wrong deploy, and `bake` is outside ciu-P29's scope, so it was left
alone — but it is the same latent class and should be closed with the same
argparse-resolution approach.

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

### CIU-39 detail — `verified-match` is unreachable for vendor images

Renumbered from main's CIU-28 at the 2026-08-19 merge (assay-side references
updated the same day). Mechanism (source: `deploy.py` `_provenance_result`):
a container's provenance status comes from its
`org.opencontainers.image.revision` label vs the checkout commit; an image
ciu never built (pulled from a vendor registry — dstdns runs vault,
authentik, consul this way) has no such label, is `unlabelled`, and never
contributes `match`. A deployment whose containers are all vendor artifacts
can therefore never leave `not-verified-no-evidence`: provenance has no
disposition that says "this image is a pinned vendor artifact, expected
unlabelled". Blocks assay B004 (coverage-judge qualification on pinned
artifacts).

This entry predates this tracker's five-point format; before it is carved,
capture the live reproduction (one `ciu provenance` run against an
all-vendor deployment) and decide the contract shape — candidates: a
declared vendor-baseline source (digest pin file) provenance can verify
against, or an explicit per-container `vendor-pinned` disposition distinct
from `unlabelled` — per [How issues get here](#how-issues-get-here).

**SPEC ownership:** S17 (provenance semantics).

### CIU-39 disposition — FIXED 2026-08-22

The live reproduction was never captured against a running all-vendor
deployment (no docker daemon in the carving environment); the contract was
decided from source + the assay B004 requirements instead, and the
controlled-wrong oracles pin the semantics. Operator decisions: declared
REFERENCES (not a digest pin file — DESIGN-GUIDE records why), and a schema
bump to `schema_version: 2` with the seven CIU-20 fixtures kept frozen as the
historical v1 grammar record and nine new v2 fixtures carrying the
assertions. `[deploy.provenance] vendor_images`; reference equality →
`vendor-pinned`; same canonical image NAME at another reference → vendor drift
(`mismatch`; references compared on Docker-canonical spellings); undeclared
unlabelled stays `unlabelled` in the document and contributes nothing — a
forgotten bake stays VISIBLE per container, while the overall flips from
no-evidence-WARN to green exactly as an own-image match always made it.
Malformed declarations refuse exit 2 — a silently ignored declaration would certify
exactly the deployment it was written to vouch for. `verified-match` is now
reachable for all-vendor deployments; B004's remaining blocker is assay-side
(`PROVENANCE_UNVERIFIED`, A-276).

## CIU-25 — stale worktree/stack detection and reap

**Status:** PARTIAL (2026-08-22) — the GIT half shipped as
`ciu worktree branches` (SPEC S16.8, capability `worktree.branches.v1`):
a closed seven-category survey (base/mainline/current/managed-instance/
prunable/merged-dirty/unmerged) with per-branch attributes (#changed files
vs the merge-base, ahead/behind, last-commit age, dirty, ciu-instance
linkage), and `-y` removing EXACTLY the Git-provable prunable category
(worktree remove + `branch -d`, both re-verified by Git itself). No age
heuristic, no process inference — the constraints below are honored by
construction. The Docker-resource half (containers/volumes of a crashed
instance) remains OPEN with the same ownership/lease precondition.

**HOTFIX 2026-08-25 (ciu-P28)** — the git half as first shipped (v7.0.0,
`c92377fb`) violated the "must not destroy resources" constraint above in
four ways, each reproduced end-to-end by two independent retrospective
adversarial reviews: `-y` bare-removed the checkout of a LIVE CIU-managed
instance with no `ciu clean` first (the exact orphaned-container /
stranded-root-owned-`vol-*` outcome this entry exists to prevent); it judged
mergedness against the invoking linked worktree's HEAD instead of the
primary's, falsely reporting merged branches unmerged AFTER destroying their
checkouts; it self-destructed when invoked from a checkout whose own branch
was prunable, aborting the whole run with no document and silently skipping
every later candidate; and `--json` exited 0 on a partial prune. Fixed by
the new `managed-instance` category (never pruned — use `ciu worktree rm`),
a primary-worktree-rooted destructive pass with a third read-only
HEAD pre-check, an invoking-checkout guard plus a no-escape per-branch loop,
and one hoisted exit-code decision. Document `schema_version` is now `2`.
See `nyxloom-trove/reports/ciu-P28-hotfix-worktree-branches-prune-safety-LOG.md`.
**The Docker-resource half must not repeat this shape**: any future reap that
touches a MANAGED instance goes through clean-then-remove, never a bare
resource deletion.

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

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** verbatim recurrence in the very next package — `ciu env generate` inherited main's `DOCKER_NETWORK_INTERNAL` and would have silently produced a Mode-A stack; caught only because the P111 write-up primed the operator's agent to check. Two consecutive packages → priority bump warranted.

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

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** `core,db` again could not start controller/webapp-server without the two identity-profile `ASK_VAULT` paths; resolved the same way (disposable placeholders in the instance's own Vault, disclosed). Two consecutive packages → priority bump warranted.

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

## CIU-46 — shipped stacks under the legacy compose-project fallback escape clean's S6.4a enumeration

**Filed by:** ciu controller session, 2026-08-22, from the residual recorded
in CIU-43's FIXED row during the consumer-wave adversarial review.
Source-grounded: `engine.py` `run_shipped` (~line 1638) falls back to
`shipped_project = None` (legacy directory-derived compose project) with only
a warning when `deploy.project_name`/`environment_tag` are absent — shipped
mode does not require a full deploy config (S8.5) — while clean's
`_stack_compose_projects` (`deploy.py`) returns `[]` under exactly that
condition, so the S6.4a network/volume enumeration never learns the legacy
project name and its `*_default` network and label-prefixed volumes survive a
reported-clean teardown.

### Why CIU owns it

S6.4a's contract is "clean leaves zero identity-scoped Docker objects, or
fails loudly". The shipped path silently re-creates the CIU-43 leak species
for any shipped stack deployed without tags; the consumer cannot fix this
from its side — the enumeration is ciu's.

### Proposed contract

When clean encounters a selected shipped stack whose compose project cannot
be derived (tags absent), either (a) enumerate the legacy directory-derived
project name with the same compose-label filters and clean it, or (b) refuse
clean for that stack naming the missing tags — never silently skip. Match
whatever `run_shipped`'s fallback actually named at up time.

### Oracles

- Shipped stack deployed via the legacy fallback (no tags) → clean removes
  its `*_default` network and label-prefixed volumes, or refuses naming the
  tags; `clean complete` over a survivor is the controlled-wrong
  implementation and must fail the oracle.
- Tagged shipped stacks keep today's exact behavior.

**SPEC ownership:** S6.4a (cleanup semantics), extending its shipped-stack
coverage.

## CIU-47 — `env generate` adopts an ambient `PUBLIC_FQDN` with no consistency check

**Filed by:** ciu controller session, 2026-08-22, from the follow-up
candidate named out-of-scope in CIU-41's FIXED row. Source-grounded:
`workspace_env.py` reads `PUBLIC_FQDN` from the ambient environment at
generate time (bare `os.environ.get` at ~lines 278 and 1060) with no
comparison against anything derived for THIS workspace — the identical
masked-default shape CIU-41 fixed for `REPO_NAME`/`INSTANCE_ID`/
`DOCKER_NETWORK_INTERNAL`. Any consumer whose login shell sources a main
checkout's `ciu.env` (the documented convenience pattern) carries main's
`PUBLIC_FQDN` into a fresh worktree's generated file, where it becomes the
worktree's recorded public name.

### Why CIU owns it

Same reasoning as CIU-41: `env generate` is the identity-computation verb and
its output is the record every later command trusts; the S2.7 refined
precedence now covers the identity tuple but deliberately stopped at the
filed scope, leaving this sibling unfixed.

### Proposed contract

Extend S2.7 refined precedence to `PUBLIC_FQDN`: during generate, an ambient
value is adopted only when consistent with the value this workspace's own
inputs derive; on mismatch use the derived value and warn on stderr naming
the ignored ambient value. (Note `PUBLIC_FQDN` is host-derived rather than
path-derived — decide what "consistent with this workspace" means: likely
re-detection via the same detection path, with an explicit flag as the only
override.)

### Oracles

- Generate in a worktree with main's `PUBLIC_FQDN` exported → generated
  `ciu.env` carries the workspace-derived value and a warning names the
  ignored ambient one.
- Consistent pre-set value → silent, output unchanged.
- Controlled wrong implementation: restoring the bare
  `os.environ.get("PUBLIC_FQDN", ...)` fallback must fail the first oracle.

**SPEC ownership:** S2 (workspace environment), extending S2.7.

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
