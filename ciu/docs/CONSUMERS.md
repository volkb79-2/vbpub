# CIU — Consumers guide: structured worktree control (HOW)

Worked examples for adopting CIU's machine-readable worktree surface. Read
this after [README.md](../README.md) (what) and [DESIGN-GUIDE.md](DESIGN-GUIDE.md)
(why); the normative contract is [SPEC.md](SPEC.md) (§S16.4, §S16.5).

Everything below is copy-paste-able. All examples assume a CIU-managed
worktree family: run `ciu worktree create` first (see below).

## 1. Declare the config a consumer needs (valid TOML)

A consumer project declares the ordinary global configuration that this
surface reads:

```toml
[ciu.worktree]
max_concurrent_instances = 3

[deploy]
project_name = "myapp"
environment_tag = "dev"
network_name = "$DOCKER_NETWORK_INTERNAL"
landscape_id = "prod-eu"
```

`landscape_id` is opt-in [S3.11](SPEC.md#s3--configuration-model): a
DNS-label-safe slug (`^[a-z][a-z0-9-]{0,62}$`) that a consumer renders its
Consul KV root (`dstdns/<landscape_id>/…`) and mesh ACL tags from.

## 2. Create a managed instance and read its lifecycle JSON

```console
$ ciu worktree create pkg-under-test --prefix myapp --feature pkg-under-test --json
{"schema_version": 1, "operation": "create", "status": "ready", "instance": {...}}
```

Every lifecycle verb (`create`, `ensure`, `adopt`, `add`) with `--json` emits
the same envelope. `status` is one of `allocating`, `ready`,
`recovery-required`; a `recovery-required` instance carries a closed
`recovery_status` of `checkout-incomplete`, `env-generation-failed`, or
`runtime-collision`. Resume a partial allocation with `ensure`.

## 3. Inspect one instance with fresh Git facts

```console
$ ciu worktree inspect pkg-under-test --json
{"schema_version": 1, "operation": "inspect", "status": "ready",
 "instance": {...}, "git": {"registered": true, "path": "...",
 "branch": "myapp-...", "detached": false, "primary": false,
 "head": "abc12345", "dirty": false}}
```

`git.*` is freshly read from Git, never inferred from the record. A record
whose checkout is gone, a branch mismatch, a duplicate logical identity, or
an unreadable `git status` is a refusal on stderr with exit 2 — never a
guessed value. `dirty` is true when `git status --porcelain` is non-empty.

## 4. List every managed instance

```console
$ ciu worktree list --json
{"schema_version": 1, "operation": "list", "status": "ready",
 "instances": [{"operation": "inspect", "status": "ready", "instance": {...},
 "git": {...}}]}
```

The prose `ciu worktree list` still shows every git worktree; `--json` shows
only managed instances, each with fresh Git facts.

## 5. Remove and read the removal document

```console
$ ciu worktree rm pkg-under-test --json
{"schema_version": 1, "operation": "remove", "status": "removed",
 "removed_path": "...", "instance": {...}}
```

`rm` runs `ciu clean` then `git worktree remove` (that order is normative,
[SPEC §S16](SPEC.md#s16--worktree-instances-ciu-worktree)). A failed clean or
git removal raises with exit 2 and **no** success document; the error names
the retained resources.

## 5b. Clean up forgotten branches — grounded, never age-based (S16.8)

After weeks of parallel worktree waves the repo accumulates merged branches
and their checkouts. `ciu worktree branches` proves what is safe instead of
guessing:

```console
$ ciu worktree branches
branch hygiene vs 'main' — 2 prunable, 0 merged-dirty, 3 unmerged, 1 managed-instance, 1 current, 1 base

prunable:
  fix/net-leak @ /repo/.worktrees/fix-net-leak  ahead 0 behind 4 changed 0 file(s)  last 2026-08-01

managed-instance:
  feat/api @ /repo/.worktrees/api  ahead 0 behind 2 changed 0 file(s)  last 2026-08-19  ciu:api(ready)

unmerged:
  feat/wip  ahead 3 behind 1 changed 12 file(s)  last 2026-08-21  ciu:wip(ready)
```

Survey only — nothing is removed. `-y` removes exactly the `prunable`
category. What the gating actually guarantees: **a candidate's checkout is
never destroyed by a refusal this command could have foreseen.** Three
read-only checks run before anything is touched — the base must be contained
in the primary checkout's HEAD (or origin/HEAD) or `-y` refuses outright; a
branch tracking an upstream that lacks its tip is reported `FAILED`; and a
branch not contained in the primary's HEAD (the HEAD `git branch -d` judges
against) is reported `FAILED`. Git can still refuse a step for a reason only
Git knows — that branch becomes a `FAILED` line and the prune continues with
the rest. Every outcome is printed (`removed:` / `FAILED: <branch> —
<reason>` lines), no failure aborts the run, and a partial prune exits
non-zero in **both** `--json` and human output — never a silent success. Git
re-verifies cleanliness and mergedness on every step; nothing is ever
force-deleted.

Run it from any checkout: every destructive Git command executes from the
PRIMARY worktree, so a linked checkout that is behind the mainline cannot
make merged branches look unmerged, and the checkout you are standing in is
never a candidate in its own run.

The categories are closed: `base`, `mainline` (the origin/HEAD default branch
— never prunable even when measured against another ref), `current` (the
primary checkout's branch, or the one you invoked from), `managed-instance`
(its checkout carries a CIU-managed instance record — **never** pruned here
at any lifecycle state, because a bare `git worktree remove` would destroy
the config that tells CIU what to clean; use `ciu worktree rm NAME`, which
runs `ciu clean` first), `prunable`, `merged-dirty` (merged but its checkout
has uncommitted work — decide by hand), and `unmerged`. Every branch carries
`ahead`/`behind`, `changed_files` vs the merge-base, last-commit date, and
its ciu instance linkage, so a human can rule on the rest. No age heuristic
exists anywhere in this command: a branch one minute old that is fully merged
is prunable, a branch six months old that is not is not.

Automation allowlists the capability id `worktree.branches.v1`
(`ciu capabilities --json`) instead of inferring the feature from SemVer;
the `--json` document is versioned (`schema_version: 2` — 1 predates the
`managed-instance` category, operations `branches`/`branches-prune`, statuses
`survey`/`pruned`/`partial`).

```bash
ciu worktree branches --json | jq '.branches[] | select(.category=="prunable")'
```


## 5c. Declare a cross-profile secret producer (`produced_by`, S13.6)

When ONE profile's provisioning writes the Vault path another stack reads,
declare it beside the directive so a partial selection refuses UPFRONT
naming the producer instead of failing at materialization with only the
bare path:

```toml
[controller.secrets]
bootstrap_token = { directive = "ASK_VAULT:authentik/bootstrap_token", produced_by = "identity" }
```

```console
$ CIU_SERVICES_PROFILE=core,db ciu up
[ERROR] Provisioning producers missing from the selection (S13.6):
  stack 'applications/controller': ASK_VAULT secret 'bootstrap_token' reads Vault path 'authentik/bootstrap_token', which is provisioned by profile 'identity' — none of its stacks are in your selection (core,db). Deploy the producer profile or its stacks, or seed the path out-of-band before deploying.
```

Producer presence is judged by DEPLOYED STACKS (the producer profile's
`stacks` list plus its phases' services), not the label — an alias profile
deploying the same stacks satisfies it. The value must name a profile in
`[deploy.profiles]`; a typo is a configuration error even when nothing else
would check it.

## 5d. Tell provenance which images are vendor artifacts (`[deploy.provenance]`, S17.5)

Vendor images (vault, authentik, consul…) carry no ciu bake, so
`ciu provenance` could never reach `verified-match` on deployments built
from them. Declare the exact references you expect to be third-party:

```toml
[deploy.provenance]
vendor_images = [
  "hashicorp/vault:1.15",
  "ghcr.io/goauthentik/server:2024.2.2",
]
```

A running container whose image equals a declared entry (compared on
Docker-canonical spellings) reports status `match`→commit or
`vendor-pinned`; the same image name at another reference is drift →
`mismatch`; anything undeclared and unlabelled stays `unlabelled`, visible
in every document. Provenance documents are emitted at `schema_version: 2`
— strict consumers refuse unknown members rather than guess.

## 6. Start the selected instance, exactly (S16.6)

```console
$ ciu worktree up pkg-under-test
```

`up` resolves one `ready` managed record, parses **that** checkout's
`ciu.env` by exact path, strips every inherited CIU identity/root/network key
from the ambient environment, overlays the target's own values, and invokes
CIU's existing up entry point in that root. The target's `REPO_ROOT`,
`INSTANCE_ID`, and `DOCKER_NETWORK_INTERNAL` must match the record — a
missing, mismatched, or not-ready instance refuses before anything starts.
The exact child exit code is returned. Plain `ciu up` from a shell inside the
primary checkout would run the PRIMARY instance, not this one; `worktree up`
exists precisely so a consumer cannot get that wrong.

## 7. Run exact argv in the selected root (no shell, no implicit up)

```console
$ ciu worktree exec pkg-under-test -- ./scripts/gate.sh --strict
$ ciu worktree exec pkg-under-test -- echo 'a b' '$(whoami)' ';' '-x' '*'
```

`exec` runs the exact argv (after the mandatory `--`) with **no shell** in
the selected checkout, under the same sanitized target environment as `up`.
Spaces, globs, `$()`, semicolons, and leading dashes arrive byte-for-byte at
the child; nothing is interpreted by a shell and nothing is misparsed as a
CIU flag. It **never** runs `up`, `render`, or `clean` implicitly — it is the
execute-in-this-exact-place primitive, so a non-container consumer can gate
against the checkout without starting anything. The child's exact exit code
is returned.

## 8. Run inside a declared container target (S16.7)

Declare the target in the selected instance's global config — a Git-safe
alias with exactly these four keys:

```toml
[ciu.worktree.exec_targets.tester]
stack = "test"                        # required non-empty string
service = "tester"                    # required non-empty string
workdir = "/workspace"                # required absolute container workdir
requires_worktree_mount = true        # optional boolean; true by default
```

```console
$ ciu worktree exec pkg-under-test --target tester -- ./scripts/gate.sh --strict
```

`exec --target` resolves the declared target's exact rendered stack and
Compose project/service/network under the instance's own `ciu.env`, requires
**exactly one already-running container** (zero or multiple refuse; `up` is
never started implicitly), and — by default — proves that container has a
bind mount whose host source is the selected Git worktree at a path
containing the declared `workdir` before running `docker exec -w WORKDIR
CONTAINER -- ARGV...` (no shell, exact argv, exact exit code). Set
`requires_worktree_mount = false` only for a deliberate non-source utility
container; it does not weaken project/service/network uniqueness.

## 9. Discover capabilities instead of guessing from the version

```console
$ ciu capabilities --json
{"schema_version": 1, "capabilities": ["worktree.exec-local.v1",
 "worktree.exec-target.v1", "worktree.identity.v1",
 "worktree.inspect.v1", "worktree.lifecycle-json.v1", "worktree.up.v1"]}
```

Allowlist the identifiers you depend on.

## Failure vocabulary, one place

`allocating` — allocation in progress; `ready` — a complete, closed runtime
identity; `recovery-required` — an interrupted allocation with a closed
`recovery_status`; `removed` — the terminal removal state. Every JSON document
carries `schema_version: 1` and a closed `operation`. Unknown shapes fail
fast.

## 10. Derive feature flags from the selected profile set (S3.12, CIU-44)

A stack that integrates with an optional upstream no longer hardcodes the
coupling — it reads the selection. Paste into any stack's
`ciu.defaults.toml.j2`:

```jinja
[myapp.my_service.features]
# on exactly when this invocation deploys infra/pwmcp (any profile name that
# selects it — the template sees the resolved STACK set, not the flag spelling):
enable_pwmcp_mcp = {{ 'infra/pwmcp' in ciu.deployed_stacks }}

[myapp.my_service.upstream]
{% if 'infra/vault' in ciu.deployed_stacks %}
host = "{{ vault.internal_host }}"
{% endif %}
```

Semantics worth knowing before you adopt (the facts merge into your config's
own `[ciu]` table — existing switches like `auto_connect_network` stay visible):

- `ciu.selected_profiles` is the ordered named profiles of THIS invocation
  (`[]` = default all-phases); `ciu.deployed_stacks` is the full stack set it
  will deploy — visible from EVERY selected stack's render, not just its own.
- `ciu dev` declares exactly its one target stack.
- Outside a deployment render (`ciu.*` used where no selection exists) the
  render FAILS with a Jinja `UndefinedError` naming `ciu` — you will never
  silently ship an empty-selection default.
- Hooks see the identical snapshot as `ctx.selected_profiles` /
  `ctx.deployed_stacks`, plus `ctx.instance_id` / `ctx.network` from this
  workspace's own `ciu.env` — read identity from ctx, never from ambient env
  (a sourced sibling checkout's `ciu.env` is the CIU-41 contamination path).

## 11. What `ciu clean` removes — and what it names (S6.4a, CIU-43)

```console
$ ciu clean -y                      # in a managed worktree instance
[INFO] Removed 2 network(s): myapp-abc123-network, myapp-dev-vault_default
[SUCCESS] clean complete
$ docker network ls | grep myapp-abc123   # → nothing; zero identity objects remain

$ ciu clean -y                      # in the MAIN workspace (no instance record)
[INFO] kept: myapp-abc123-network (workspace network of the main workspace (devcontainer residence))
[SUCCESS] clean complete (kept: myapp-abc123-network)
```

Contract: an S16 instance's clean leaves zero identity-scoped objects —
containers, volumes (including bare-project-prefix names like
`myapp-vault-data`, caught via the exact per-project compose-label pass),
networks including compose `*_default`. A lingering endpoint (your
devcontainer joined the instance network) is disconnected first; one that
cannot be disconnected is NAMED and the clean exits 1 — it is never silently
kept. The main workspace keeps its own workspace network (your devcontainer
lives on it) and says so twice: a `kept:` line and the final success line.

**Shipped stacks without deploy tags (CIU-46).** A stack deployed via
`ciu up --dir <stack> --shipped` on a checkout whose config sets neither
`deploy.project_name` nor `deploy.environment_tag` runs under the
workspace-identity compose project `REPO_NAME-INSTANCE_ID-<stack>` (from
this checkout's `ciu.env`) — and `ciu clean` derives the SAME name from the
same record, so its containers, `*_default` network, and named volumes are
removed like any other stack's:

```console
$ ciu up --dir vendor/vault --shipped
[INFO] [S8.7] deploy.project_name/environment_tag not set — shipped stack uses the workspace-identity compose project 'myapp-abc123-vault'
$ ciu clean -y
[INFO] Removing 2 container(s): myapp-abc123-vault-vault-1, myapp-abc123-vault-vault-init
[SUCCESS] clean complete
```

This is a breaking change against pre-6.5.0 behavior: the withdrawn fallback
let docker derive the project from the directory basename — identical for
every checkout of your repo (cross-checkout collisions) and invisible to
`ciu clean`. One-time migration for deployments created before this change:
tear the old-named objects down manually once
(`docker compose -p <old-basename> down -v --remove-orphans`, then
`docker network rm <old-basename>_default`), or re-up under a tagged config.
`ciu.env` with `REPO_NAME`/`INSTANCE_ID` must exist for a config-less
shipped `up` or `clean` to name the project — `ciu env generate` writes it.

## 12. The implementation gate (Assay-backed, S18)

CIU's gate is judged by the **released Assay CLI**, pinned and vendored in the
repository — not installed ambiently. You can reproduce the gate's evidence
locally (the container part still needs the operator's four-traps recipe):

```bash
# 1. Verify the pinned Assay artifact (fails the gate if it ever drifts)
sha256sum -c ciu/tools/assay/assay-2.3.0.pyz.sha256

# 2. Inspect the declared lane (validates config, runs nothing)
cd ciu && .venv/bin/python tools/assay/assay-2.3.0.pyz lanes --file assay.toml

# 3. Run the lane; Assay snapshots the commit, runs the full suite at 100%
#    line+branch, and judges the changed-line floor on base..HEAD (R1).
#    The verdict goes OUTSIDE the judged tree (gitignored .assay/).
cd ciu && mkdir -p .assay && \
  .venv/bin/python tools/assay/assay-2.3.0.pyz run ciu \
    --file assay.toml --verdict-json .assay/verdict-ciu.json
```

Contract notes for a consumer of the gate:

- The lane **refuses a dirty tree** (`NO_MEASUREMENT`/`DIRTY_TREE`): commit or
  stash uncommitted work (the untracked `_last-summary.txt` is gitignored by
  design — see DESIGN-GUIDE "clean-tree requirement").
- The coverage artifact and `.coverage` data file are gitignored repo-wide;
  the verdict is written under gitignored `.assay/`. Nothing else may be left
  untracked.
- The gate's exit status is the Assay job's own; a red lane is a red gate.
- The container slice comes only from `$CGROUP_PARENT_DEV_BACKGROUND`
  (verified `LoadState=loaded` before `docker run`, fail-closed) — see
  vbpub AGENTS.md "Manual tester-unified gate runs — the four traps" for a
  hand-rolled run.

## 13. Read the per-stack status report (`ciu status --json`, S7.10, CIU-QOL-6)

```console
$ ciu status --profile core --json
{
  "schema_version": 1,
  "profile": "core",
  "stacks": [
    {
      "path": "apps/vault",
      "name": "vault",
      "phase_key": "phase_1",
      "compose_project": "myapp-dev-vault",
      "containers": [
        {"name": "myapp-dev-vault-vault-1", "status": "healthy", "image": "hashicorp/vault:1.15"}
      ]
    },
    {
      "path": "apps/worker",
      "name": "worker",
      "phase_key": "phase_2",
      "compose_project": "myapp-dev-worker",
      "containers": []
    },
    {
      "path": "apps/not-yet-scaffolded",
      "name": "not-yet-scaffolded",
      "phase_key": "phase_3",
      "compose_project": null,
      "containers": []
    }
  ]
}
```

Reading this document: `apps/vault` has one running, healthy container.
`apps/worker`'s compose project resolved (`compose_project` is a string) but
`containers` is empty — its stack has not been started yet; that is a
legitimate result, not an error. `apps/not-yet-scaffolded`'s
`compose_project` is `null` — its stack directory does not exist on disk at
all, a DIFFERENT condition from "not started" and distinguished structurally
by the field, never collapsed into the same shape. `status` is
`health_pkg.classify`'s closed vocabulary
(`healthy`/`starting`/`unhealthy`/`no-healthcheck`/`not-found`); `image` is
the container's `Config.Image` verbatim, unnormalized.

If the Docker daemon itself cannot be reached, `ciu status` does NOT print
any of the above — it exits 2 with `[ERROR] ciu status: <reason>` on
stderr. A consumer scripting against this surface should treat a non-zero
exit as "no determination was made" and a `containers: []` row as "checked,
found nothing running" — the two must never be confused.

## 14. Preflight a config change without deploying (`ciu check`, S13.4a / S9.5)

`ciu check` walks the whole config pipeline **in memory**. It creates no
hostdir, materializes no secret, writes no rendered compose/overlay/configfile
(not even a `__pycache__` beside a hook it imports), executes no hook `run()`,
and never contacts Docker. Use it instead of `ciu up --dry-run` as a
validation tool: **`--dry-run` still creates hostdirs and still runs your
`pre_secrets`/`pre_compose`/`post_compose` hooks for real** — it only skips
`docker compose up`.

```console
$ ciu check --profile core          # prose, per stage
$ ciu check --profile core --json   # one versioned object
$ ciu check --profile core --live   # ALSO probe live provisioning state
```

Exit codes are S13.4's: `0` clean, `2` any static configuration error
(including every stage below), `1` **only** a `--live` probe failure. A static
failure short-circuits before any live probing happens.

### Teach a hook to validate its own config (`validate_config`, S9.5)

Any hook may add an OPTIONAL second entry point beside its `run`. CIU calls it
during `ciu check` and **never** during `ciu up`:

```python
# infra/db-core/post_compose_db.py

def run(config: dict, ctx) -> dict:
    """Normal execution — provisions users, databases."""
    ...


def validate_config(config: dict, ctx) -> list[str]:
    """Optional preflight. Return one error string per finding; [] = OK.

    Receives the SAME merged, guarded config and HookContext that run() gets,
    so it validates exactly what run() will consume — before any container
    exists.
    """
    errors: list[str] = []
    registry = config.get("registry", {})
    if "database" not in registry:
        errors.append("registry.database is missing")
    users = registry.get("postgresql", {}).get("users", {})
    for user in ("controller", "workerdb"):
        if user not in users:
            errors.append(f"registry.postgresql.users.{user} is missing")

    # Secrets appear as SecretGuard objects (S4.21): you can confirm one is
    # DECLARED by name, and you must never stringify it.
    if "admin_password" not in config.get("db_core", {}).get("secrets", {}):
        errors.append("db_core.secrets.admin_password is not declared")

    return errors
```

Rules worth knowing before you write one:

- **Return `list[str]`, never a bool.** `[]` means valid. A `True`/`False`
  return is reported as a contract violation, not read as a verdict. `None` is
  tolerated as "no findings".
- **`ctx.secret_file(name)` raises `KeyError` for every name.** `ciu check`
  materializes nothing, so there is no store file to point at. Validate that a
  secret is *declared* (as above); if your check genuinely needs to read a
  materialized secret's contents, it cannot run at check time — keep that part
  in `run()`.
- **`ctx.wait_healthy` / `ctx.wait_tcp` are `None`** at check time. Nothing is
  running, and a preflight must not perform I/O anyway.
- `ctx.stack_dir`, `ctx.repo_root`, `ctx.instance_id`, `ctx.network`,
  `ctx.selected_profiles` and `ctx.deployed_stacks` are all populated exactly
  as during a real run.
- **No side effects.** No Docker, no network, no writes. Like S9.4's
  env-mutation rule, this is a contract CIU does not sandbox.
- **An exception is your hook's finding, not everyone's.** If your
  `validate_config` raises, CIU reports it against that hook and keeps
  checking every other hook and stack.
- Defining `validate_config` is entirely optional; a hook without one is
  skipped with a note, never an error.
- Your hook file is imported **exactly once** per `ciu check` run, even when
  it is declared at several hook points or by several stacks.

### The `--json` envelope

```console
$ ciu check --json
{
  "schema_version": 1,
  "operation": "config-check",
  "status": "fail",
  "profile": "core",
  "stages": [
    {"stage": "render", "status": "pass", "findings": [],
     "notes": [{"message": "3 stack config(s) rendered"}]},
    {"stage": "shape", "status": "pass", "findings": [], "notes": []},
    {"stage": "secrets", "status": "pass", "findings": [], "notes": []},
    {"stage": "provisioning", "status": "pass", "findings": [], "notes": []},
    {"stage": "governance", "status": "pass", "findings": [], "notes": []},
    {"stage": "configfile", "status": "pass", "findings": [], "notes": []},
    {"stage": "hooks-load", "status": "pass", "findings": [], "notes": []},
    {"stage": "hooks-preflight", "status": "fail",
     "findings": [{"message": "registry.consul.acl.default_policy missing",
                   "stack": "infra/consul-server",
                   "hook": "post_compose_consul.py"}],
     "notes": []},
    {"stage": "compose-render", "status": "pass", "findings": [], "notes": []},
    {"stage": "leak-scan", "status": "pass", "findings": [], "notes": []},
    {"stage": "consumption", "status": "pass", "findings": [],
     "notes": [{"message": "[S4.20] declared secret 'ca_bundle' is consumed by no channel visible to `ciu check` ...",
                "stack": "infra/app"}]}
  ]
}
```

`stages` is a **list in pipeline order**, so a consumer can render it as-is.
`findings` fail the run; `notes` never do. A `--live` run adds a top-level
`"live": {"status": ..., "unsatisfied": [...]}` — deliberately not a stage,
because it is the one failure class that exits `1` rather than `2`.

Two behaviours to script against deliberately:

- **A declared-but-unconsumed secret is a note, not a failure.** `ciu up`
  itself only warns here, and `ciu check` cannot see the S5 configfile
  consumption channel without rendering — so treating it as red would both
  contradict the real pipeline and produce false positives.
- **Registry validation covers the two fields CIU itself reads — and only
  those** (S13.4b, stage 7): `[registry.postgresql].database` and
  `[registry.consul].token_vault_path`. CIU ships **no** model for your
  Redis/MinIO/Vault/PostgreSQL-users registry tables — it has never read one,
  so it has no shape to check and does not invent one. Do not read a green
  stage 7 as "my whole registry shape is correct". To validate the rest,
  declare `[ciu].registry_validator = "infra/registry_validate.py"` with a
  module-level `validate_registry(config) -> list[str]`; its findings fail the
  check the same way. Model validation needs the optional extra
  `pip install 'ciu[registry]'` — with a validated table declared and the
  extra missing, `ciu check` fails loudly naming it rather than skipping.

Under `--json` the check itself prints only the document, but the
orchestrator's own `[INFO]` lines still precede it on stdout (as with
`ciu graph --format json`); read the JSON object at the end of stdout.
