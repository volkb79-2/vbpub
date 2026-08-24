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
branch hygiene vs 'main' — 2 prunable, 0 merged-dirty, 3 unmerged, 1 current, 1 base

prunable:
  fix/net-leak @ /repo/.worktrees/fix-net-leak  ahead 0 behind 4 changed 0 file(s)  last 2026-08-01

unmerged:
  feat/wip  ahead 3 behind 1 changed 12 file(s)  last 2026-08-21  ciu:wip(ready)
```

Survey only — nothing is removed. `-y` removes exactly the `prunable`
category, gated twice so it can never half-prune: the base must be contained
in a checkout's HEAD (or origin/HEAD) or `-y` refuses before touching
anything, and a branch tracking an upstream that lacks its tip is reported
`FAILED` before its checkout is touched. Every outcome is printed
(`removed:` / `FAILED: <branch> — <reason>` lines) and a partial prune exits
non-zero — never a silent success. Git re-verifies cleanliness and mergedness
on every step; nothing is ever force-deleted. The categories are closed:
`base`, `mainline` (the origin/HEAD default branch — never prunable even
when measured against another ref), `current` (the primary checkout's
branch), `prunable`, `merged-dirty` (merged but its checkout has uncommitted
work — decide by hand), and `unmerged`. Every branch carries `ahead`/
`behind`, `changed_files` vs the merge-base, last-commit date, and its ciu
instance linkage, so a human can rule on the rest. No age heuristic exists
anywhere in this command: a branch one minute old that is fully merged is
prunable, a branch six months old that is not is not.

Automation allowlists the capability id `worktree.branches.v1`
(`ciu capabilities --json`) instead of inferring the feature from SemVer;
the `--json` document is versioned (`schema_version: 1`, operations
`branches`/`branches-prune`, statuses `survey`/`pruned`/`partial`).

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
