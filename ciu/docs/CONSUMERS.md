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


## 5b-2. Reclaim a crashed dispatcher's Docker resources (`worktree reap`, S16.10)

`branches` is the Git half of the cleanup. `ciu worktree reap` is the Docker
half — containers, volumes and networks a crashed dispatcher or a forgotten
teardown left running. It is the only CIU verb that deletes resources it did
not create in the same command, so read what it will and will not touch
before you use `-y`.

**Survey first — it is a pure read.**

```
$ ciu worktree reap
docker resource reap — owned 2, lease-expired 1, checkout-missing 1, orphaned 0, partial-cleanup 0, unattributable 1, ambiguous 0

owned:
  myrepo-a1b2c3-api  3 container(s) 2 volume(s) 1 network(s)
      instance 'api' is registered, its checkout exists, and nothing says its claim has lapsed

lease-expired:
  myrepo-d4e5f6-api  3 container(s) 2 volume(s) 1 network(s)
      instance 'nightly-run' s held lease expired at 2026-08-24T02:00:00Z (holder ciu@builder:d4e5f6)

checkout-missing:
  myrepo-9f8e7d-api  2 container(s) 1 volume(s) 1 network(s)
      labelled ciu.instance=9f8e7d, claimed by no record and no registered checkout, and its own
      ciu.repo-root label (/repo/.worktrees/crashed) names a directory that no longer exists

unattributable:
  postgres-shared  1 container(s) 1 volume(s) 0 network(s)
      no `ciu.instance` label and no identity-form compose project name — CIU cannot prove whose
      these resources are, so it will never remove them

2 resource group(s) are provably disposable (checkout-missing=1, lease-expired=1); re-run with
-y/--yes to reap them (add --dry-run first to see the exact commands). 1 group(s) are
unattributable and are NEVER reaped ...
```

**See the exact commands, then run them.**

```bash
ciu worktree reap -y --dry-run          # prints, executes nothing
ciu worktree reap -y                    # reaps the four provable categories
ciu worktree reap -y --category orphaned  # narrow it further
```

What the categories guarantee:

- **`owned` is never reaped**, and it is deliberately generous: a valid or
  perpetual lease, *no* lease at all (a pre-lease schema-v1 record, or one
  that explicitly released its claim), or simply a registered checkout whose
  own `[ciu.instance.generated]` table declares that `instance_id` with no
  record at all (its `ciu.env` before 7.7.0 — §11b). Age never
  moves anything out of it — a year-old instance with a perpetual lease is
  owned, and a five-minute-old one whose lease lapsed is not.
- **`unattributable` and `ambiguous` are never reaped and cannot be
  selected.** `--category unattributable` is a refusal (exit 2), not a
  selection. There is no flag anywhere that forces them, because those
  categories mean exactly that no proof of ownership exists. Your unrelated
  compose projects on the same host land here and are safe.
- **A surviving checkout is disposed of by `ciu clean -y` run inside it**,
  never by a bare `docker rm`: `clean` knows the rendered config, the `vol-*`
  host directories and the privileged removal helper. If that clean fails,
  reap reports it and stops — it does not second-guess it.
  **What decides that, and what it costs when the answer is no (7.7.0).** The
  test is whether the checkout carries a `[ciu.instance.generated]` table —
  `ciu.env`'s readability before 7.7.0 (§11b), moved because `clean` now
  derives its network and its compose project from that table. A checkout that
  has only the legacy export is NOT refused: it falls through to the bare
  `docker rm` / volume / network removal, which leaves every `vol-*` hostdir on
  disk. The reap now says so in that group's notes, naming the checkout and the
  `ciu env generate` + `ciu clean` repair. If you have long-lived worktrees
  created before 7.7.0, run `ciu env generate` in them once — that writes the
  table and restores full `clean` delegation.
- **A shared network is never torn out from under a live instance.** If any
  container this pass did not just remove is still joined (the S16.1
  shared-infra case), the network is left standing and the result says so.

One group's failure never aborts the sweep: it becomes a `FAILED` line with
the real error, every other targeted group is still processed, and a partial
pass exits **1** in both `--json` and human output. The returned document is a
re-survey of the post-state, not the plan.

Automation allowlists `worktree.reap.v1` (and `worktree.lease.v1`, which it
consults) via `ciu capabilities --json`. The reap document is separately
versioned (`schema_version: 1`, operation `reap`, statuses
`survey`/`dry-run`/`reaped`/`partial`), and `counts` always carries all seven
categories including the zero-valued ones, so a consumer can key on them
without probing.

```bash
ciu worktree reap --json | jq '.groups[] | select(.category=="lease-expired") | .key'
```

Declare ownership explicitly rather than letting a TTL decide for you:

```bash
ciu worktree lease nightly-run --extend 48h   # bounded claim
ciu worktree lease bench-rig  --perpetual     # long-lived ON PURPOSE
ciu worktree lease scratch    --release       # claims nothing (still never reaped)
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

## 5e. Qualify a stack's `hostname:` and `internal_host` defaults (avoid the §3.6 cockpit-alias hazard)

When you author your own compose template's service and your own
`topology.services.<name>` declaration, qualify BOTH the same way
`container_name:` already is — a bare value resolves to whichever
same-shaped CIU instance's container Docker's resolver happens to answer
with once a second instance joins the network. See
[DESIGN-GUIDE.md](DESIGN-GUIDE.md)'s "Why bare `hostname:` / `internal_host`
defaults are dangerous (CIU-48/CIU-49, §3.6 cockpit-alias-ambiguity)" section
for why; this is the paste-able "what to write."

Your own `ciu.compose.yml.j2`:

```yaml
services:
  vault:
    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-vault
    hostname: {{ deploy.project_name }}-{{ deploy.environment_tag }}-vault   # same variables as container_name:
```

Your own `ciu.global.defaults.toml.j2` (or per-stack defaults):

```toml
[topology.services.vault]
internal_host = "{{ deploy.project_name }}-{{ deploy.environment_tag }}-vault"
internal_port = 8200
```

A stack freshly scaffolded via `ciu init` already writes the qualified
`hostname:` form (S19); this is the pattern to carry into any compose
template or `topology.services` declaration you author by hand yourself,
including ones that predate this default.

## 6. Start the selected instance, exactly (S16.6)

```console
$ ciu worktree up pkg-under-test
```

`up` resolves one `ready` managed record, reads **that** checkout's
`[ciu.instance.generated]` table by exact path (its `ciu.env` before 7.7.0 —
§11b), strips every inherited CIU identity/root/network key
from the ambient environment, overlays the target's own facts, and invokes
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
Compose project/service/network under the instance's own identity record
(§11b), requires
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
  workspace's own `[ciu.instance.generated]` overlay table — **not `ciu.env`,
  since CIU-75** (§11b); read identity from ctx, never from ambient env
  (a sourced sibling checkout's `ciu.env` is the CIU-41 contamination path).
- `ctx.instance_id` / `ctx.network` both `None` is ambiguous by itself —
  "genuinely unmanaged, no `ciu env generate` here" and "the record exists
  but CIU could not parse it" look identical. `ctx.identity_unreadable`
  (CIU-80) tells them apart: `False` in the genuinely-absent case, `True`
  only when the record is present but unreadable. A hook that needs to branch
  differently on "unknown" versus "absent" reads this field; one that
  doesn't care can ignore it (it defaults `False`).

  CIU-75 changed **which record** that flag is about, and sharpened it: it is
  now the overlay's generated table, and a path that exists and cannot be read
  at all — a directory where the record belongs, an unreadable mode — counts
  as unreadable. Under CIU-80 alone that case answered "absent" (`False`),
  because the check was `ciu.env.is_file()`. **A hook that was branching on
  `identity_unreadable` needs no change**; it just stops being lied to.

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
this checkout's `[ciu.instance.generated]` table — its `ciu.env` before
7.7.0, see §11b) — and `ciu clean` derives the SAME name from the
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
A `[ciu.instance.generated]` table with `repo_name`/`instance_id` must exist
in `ciu.global.worktree.toml.j2` for a config-less shipped `up` or `clean` to
name the project — `ciu env generate` writes it. **Since 7.7.0 that table, not
`ciu.env`, is what must be present**: a checkout carrying only the legacy
export can no longer name its own project (§11b).

**What clean does NOT remove, and the `--vanilla` opt-in (S6.4b, CIU-60).**
Ordinary `ciu clean` leaves your rendered `ciu.global.toml`, your `ciu.env`,
and your `ciu.global.worktree.toml.j2` exactly where they are — that has
always been true and does not change. When you want a workspace back at
freshly-CLONED state, ask for it explicitly:

```console
$ ciu clean --vanilla -y
[SUCCESS] clean complete
[INFO] --vanilla: removed ciu.global.toml, ciu.env, ciu.global.worktree.toml.j2
```

Only those three, only on an explicit `--vanilla`, and only when the teardown
above actually succeeded (a failed clean keeps them — since 7.7.0 it is
`ciu.global.worktree.toml.j2`'s generated table, not `ciu.env`, that carries
the identity your retry resolves from). Committed inputs are never touched. An
already-absent file is fine. **Note that this deletes any hand-authored
content in `ciu.global.worktree.toml.j2`** — service profiles, a shared-infra
join, your own sparse overrides. `ciu env generate` regenerates only the
CIU-owned `[ciu.instance.generated]` table, not your edits.

## 11a. Reading workspace identity in templates and shells (S3.1b, CIU-60)

In a **template**, read identity facts from the config chain, not from `env`:

```jinja
volumes = ["{{ ciu.instance.generated.physical_repo_root }}:/repo:ro"]
```

`{{ env.PHYSICAL_REPO_ROOT }}` is the raw process environment (S3.2). If the
shell running `ciu` once sourced a sibling checkout's `ciu.env` — a documented
convenience — that is the path it renders, silently, into your bind mount.
`ciu.instance.generated.*` comes from `ciu.global.worktree.toml.j2`, which
`ciu env generate` writes for THIS repo root; the six keys are `repo_name`,
`instance_id`, `network`, `physical_repo_root`, `repo_root`, `public_fqdn`.

You may keep your own tables and comments in that same file. CIU rewrites
only its own `[ciu.instance.generated]` table and preserves every other byte —
so hand-edits INSIDE that table are silently overwritten on the next
`env generate`, and hand-edits anywhere else are safe forever.

In a **shell**, `ciu env print` prints the existing `ciu.env` as `export`
lines:

```console
$ eval "$(ciu env print)"
$ echo "$DOCKER_NETWORK_INTERNAL"
myapp-abc123-network
```

It prints; it cannot change your shell by itself (no subprocess can), which is
why it is `print` and not `apply`/`source`. It generates nothing — if
`ciu.env` is missing it says so and names `ciu env generate`.

## 11b. Migrating off `ciu.env` as an identity source (CIU-75, ciu 7.7.0) — BREAKING

**What changed.** As of **ciu 7.7.0**, `ciu.global.worktree.toml.j2`'s
`[ciu.instance.generated]` table is the **only** place CIU itself reads your
instance identity from — these six facts:

| overlay fact | legacy `ciu.env` / shell name | what it is |
|---|---|---|
| `repo_name` | `REPO_NAME` | lowercased repository name, the compose-project prefix |
| `instance_id` | `INSTANCE_ID` | deterministic id for this checkout's path |
| `network` | `DOCKER_NETWORK_INTERNAL` | this instance's own Docker network |
| `physical_repo_root` | `PHYSICAL_REPO_ROOT` | host-visible repo root, for bind mounts (DooD) |
| `repo_root` | `REPO_ROOT` | repo root as this process sees it |
| `public_fqdn` | `PUBLIC_FQDN` | detected/configured FQDN; `""` when there is none |

That mapping is the whole translation between the two records — nothing else
in `ciu.env` is identity. Everything ELSE the file carries (`CONTAINER_UID`,
`DOCKER_GID`, `ENV_TYPE`, `PUBLIC_IP`, `PUBLIC_TLS_*`, `PYTHON_EXECUTABLE`,
`HOST_MDT_TMP`, …) describes the MACHINE, not the instance, and is unaffected
by this change.

Two consequences, and the second one surprises people:

1. `ciu.env` is a **legacy, write-only export** for identity. `ciu env
   generate` still writes it, identical key set and format, and no CIU code
   path takes an identity fact from it.
2. **Exporting an identity variable no longer overrides a run.** Every verb
   now seeds `REPO_NAME`/`INSTANCE_ID`/`DOCKER_NETWORK_INTERNAL`/
   `PHYSICAL_REPO_ROOT`/`REPO_ROOT`/`PUBLIC_FQDN` into its own environment
   from the table, overwriting whatever your shell had. That is the point: a
   shell that had `source`d a SIBLING checkout's `ciu.env` used to win, and
   containers joined the sibling's network. If you were deliberately
   overriding one of these six, change the record instead — `ciu env generate`
   (which still honors a pre-set value when it is consistent with what it
   derives) or edit the table.

**What does NOT break.** Sourcing still works, byte for byte, this release:

```console
$ ciu env generate            # writes ciu.env AND the overlay table
$ source ciu.env              # still works
$ eval "$(ciu env print)"     # the forward path — same values, quoted safely
```

You will see one new `[WARN]` deprecation notice per `ciu env generate` and
per `ciu env`. It changes no exit code and refuses nothing. **Which stream it
uses is a contract, not a detail:** the verb you TYPE (`ciu env generate`)
announces on stdout, while `ciu env` and any regeneration triggered from
inside another verb's startup announce on **stderr** — so `ciu env`'s
`key=value` stdout stays parseable and, more importantly, `ciu check --json`'s
document is never preceded by a warning line. If you stop maintaining
`ciu.env` and let CIU regenerate it, `ciu check --json | jq` keeps working.

**What DOES break — the pattern to look for.** Anything that treats `ciu.env`
as an *input* rather than a *shell convenience*: parsing it, grepping a value
out of it, or re-running `ciu env generate` and then reading the file back to
learn what CIU decided. Those consumers now hold a copy that CIU does not
consult, so nothing detects it going stale. Concretely:

| pattern | replace with |
|---|---|
| `grep -oP '(?<=^INSTANCE_ID=).*' ciu.env` (or any grep/awk/sed of the file) | `eval "$(ciu env print)"; echo "$INSTANCE_ID"` — or read `[ciu.instance.generated].instance_id` from `ciu.global.worktree.toml.j2` with a TOML parser |
| a **Python** helper that parses `ciu.env` into a dict (a vendored `load_workspace_env`, a `dotenv`-style loader) | the `read_ciu_identity` helper below — scan to `[ciu.instance.generated]`, then `tomllib` — or shell out to `ciu env print`. Do **not** `tomllib.loads` the whole overlay file: it is a Jinja template and your own sections may not be valid TOML |
| exporting `INSTANCE_ID=… ciu up` (or any of the six) to steer a run | change the record: `ciu env generate` (it still honors a consistent pre-set value) or edit the table. Since 7.7.0 the export is overwritten at startup |
| `ciu env generate` **then** read `ciu.env` back to discover the network / instance id | read the overlay table, which is what CIU itself now reads; the two are written from the same in-memory values by the same command |
| a **template** using `{{ env.PHYSICAL_REPO_ROOT }}` | `{{ ciu.instance.generated.physical_repo_root }}` — see §11a; this has been the correct form since CIU-60 and is now the only one CIU agrees with |
| `echo 'export CIU_SERVICES_PROFILE=…' >> ciu.env` | unaffected — that key is read from the process environment, not from the file; keep sourcing, or export it directly |

**Reading the facts yourself, without `ciu.env` at all.** The
`[ciu.instance.generated]` block is plain TOML by construction — CIU writes it
as quoted strings and owns exactly those bytes — but **the surrounding file is
a Jinja template and is yours to add to** (§11a: your own tables and comments
may live anywhere else in it). So do not hand the WHOLE file to `tomllib`. It
happens to work while everything you added is *also* valid TOML (Jinja inside
a quoted string is fine), and it stops working the moment you use the file as
the template it is — a `{% if %}` line, an unquoted `{{ … }}` value or a bare
`$VAR` each raise `TOMLDecodeError`, on a file CIU reads without complaint.
Scan to CIU's block first, exactly as CIU's own reader does:

```python
# ciu_identity.py — the same slice CIU itself reads (ciu >= 7.7.0)
import tomllib
from pathlib import Path

HEADER = "[ciu.instance.generated]"


def read_ciu_identity(ciu_root) -> dict[str, str]:
    """The six facts, or {} when this checkout has never been generated.

    Raises ValueError on all FOUR of the present-but-unreadable cases CIU
    itself refuses (OS read error, non-UTF-8 byte, malformed TOML, non-string
    fact). Do not collapse those into {}: "not a CIU instance" and "this
    instance's identity cannot be determined" call for different behaviour on
    your side too.
    """
    path = Path(ciu_root) / "ciu.global.worktree.toml.j2"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}                      # never generated here — a real state
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc

    start = next((i for i, line in enumerate(lines) if line.strip() == HEADER), None)
    if start is None:
        return {}                      # your own overlay, no CIU table yet
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("[")), len(lines))
    try:
        block = tomllib.loads("\n".join(lines[start:end]))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"malformed {HEADER} in {path}: {exc}") from exc

    facts = block["ciu"]["instance"]["generated"]
    for key, value in facts.items():
        # The fourth indeterminacy case, and the easiest to skip: every
        # generated fact is a string by construction, so a bare number here
        # (a hand-edit, a bad writer) would otherwise flow into a compose
        # project name or a docker label as str(int) — silently wrong instead
        # of loudly refused. CIU's own reader refuses it; so must yours.
        if not isinstance(value, str):
            raise ValueError(
                f"{HEADER}.{key} in {path} is {type(value).__name__}, "
                "not a string"
            )
    return facts
```

```console
$ python3 -c 'from ciu_identity import read_ciu_identity as r; print(r(".")["network"])'
myapp-abc123-network
```

If you would rather not carry any parser at all, `eval "$(ciu env print)"`
gives you the same six values under their shell names (plus the machine facts)
and stays correct if the record ever moves again.

**Known-affected consumers, from a real sweep (2026-08-31, re-audited at
`dstdns@96fcf762`).** The primary consumer repo, `dstdns`, was swept for this
pattern. Everything below keeps working this release; all of it should be
re-pointed before `ciu.env` stops being written:

1. **`scripts/ciu/workspace_env.py`** — a vendored stub that parses `ciu.env`
   into a dict (`load_workspace_env` / `ensure_workspace_env`), reachable as
   `ciu.workspace_env` only inside the test-runner container, which puts
   `scripts/` on `PYTHONPATH` and has no real `ciu` wheel installed. This is
   the most load-bearing one: a **second implementation** of a read CIU has
   now moved. It should read the overlay's `[ciu.instance.generated]` table
   instead — same checkout, no `ciu` import needed (see the helper above).
   **Three importers, not one:** `scripts/config_helper.py:30` (uses
   `DOCKER_NETWORK_INTERNAL`), `scripts/url_builder.py:18` (uses `REPO_ROOT`),
   and `tests/smoke/test-deployment-validation.py:144-148`, whose `sys.path`
   hack adds `scripts` but imports bare `workspace_env` — one directory too
   shallow, so that probe has only ever raised `ModuleNotFoundError` into a
   swallowed "Failed to read ciu.env" message. Fix that while re-pointing it.
2. **`scripts/ciu/config_constants.py`** — the sibling stub the two live
   importers above also depend on (`url_builder.py:17`,
   `config_helper.py:31`, both for `GLOBAL_CONFIG_RENDERED`). Any migration of
   (1) touches this file's package too. Note `scripts/render_template.py:20`
   imports `STACK_CONFIG_ACTIVE` from it, which exists in neither the stub nor
   real ciu — that module is already unimportable, and four `infra/*/start.sh`
   scripts invoke it. Independent of CIU-75, but it is found by the same
   sweep.
3. **Shell: nine `source` statements across eight files, and no key
   extraction anywhere.** `scripts/ciu-env.sh:66` is the canonical loader
   (asserting `REPO_ROOT`/`PHYSICAL_REPO_ROOT` at `:68`; sourced in turn by
   `scripts/devcontainer-exec.sh:28` and `scripts/admin-debug-exec.sh:26`),
   plus `.vscode/run-ciu-render.sh:12`, `.vscode/run-ciu-render-all.sh:13`,
   `.vscode/run-deploy.sh:16`, `.vscode/copilot-cmd.sh:53`,
   `env-workspace-setup-generate.sh:256`, and
   `.devcontainer/finalize.post.d/10-dstdns-ciu.sh` **twice** (`:29`, and
   `:68` inside the `~/.bashrc` block it writes, which sources `ciu.env` into
   every interactive shell).

   **The ninth is the one to migrate first:**
   `scripts/devcontainer-exec.sh:83-104` (`get_network_name`, called at `:148`
   and `:183`) sources `ciu.env` *specifically to fetch*
   `DOCKER_NETWORK_INTERNAL`, and hard-fails when the file is absent. It is
   live code that consumes one identity fact, i.e. exactly the shape this
   section is about — a whole-file `source` only incidentally. It is also why
   a naive sweep misses sites: the source target is the variable `"$env_file"`,
   not the literal `ciu.env`.

   All nine keep working; `eval "$(ciu env print)"` is the forward form. Note
   `ciu-env.sh:48-50` documents "ciu.env wins over anything already in the
   environment" — that precedence now AGREES with CIU's own (§11b consequence
   2), where before 7.7.0 the two could disagree.
4. **`nyxloom-trove/handoffs/dstdns-P147-vertical-corpus-e2e.md:473`** — the
   one grep-the-file recipe in the repo (`grep -oP
   '(?<=^CIU_INSTANCE_ID=).*' ciu.env`), in a handoff doc rather than in
   executable code, and *already* wrong: the key is `INSTANCE_ID`, and
   `CIU_INSTANCE_ID` has never existed. It should read the overlay.
5. **`.github/workflows/ciu-env-cicd-test.yml:57,80`** — `cat ciu.env` and
   uploads it as a CI artifact. Diagnostic only, but it no longer shows what
   CIU reads; the overlay is the file worth capturing now.

**What does NOT need changing, and why it is worth knowing:** dstdns's
templates consume all six identity keys as `$VAR`
(`ciu.global.defaults.toml.j2:51,52,74,75,76,134,135,136,431,438…`, plus the
`${PHYSICAL_REPO_ROOT:?…}` guards in a dozen compose templates). Those are
resolved from the process environment CIU itself seeds from the overlay
(§11b consequence 2), so they are not only unaffected — they are now *more*
correct, because a stale sibling value can no longer reach them.

If you maintain another consumer, the sweep that finds these is:

```console
$ git grep -nE '(grep|awk|sed|cut|open|read_text|dotenv)[^\n]*ciu\.env'
$ git grep -nE 'ciu env generate' -A3 | grep -n 'ciu\.env'
```

**Sanity check after migrating.** Two checks, and the second is the one that
actually proves something:

```console
$ ciu env generate
$ rm ciu.env
$ ciu status --json | head -3        # still names your compose projects
$ ciu clean -y                       # still finds and removes your network
```

That one is weaker than it looks — CIU regenerates the export it no longer
reads, so a passing run does not by itself prove where the answer came from.
This one does:

```console
$ DOCKER_NETWORK_INTERNAL=not-my-network ciu check --json | jq -r '.[0].network // empty'
myapp-abc123-network                 # the RECORD wins; the export did nothing
```

If either now fails — or if that second command echoes `not-my-network` back
at you — the failure is CIU's, not yours: file it.

**Finally: `.gitignore`.** `ciu.global.worktree.toml.j2` must be gitignored
and must not be deleted casually. It has always been declared gitignored
(S3.1b), but it is now the only record of your instance identity. A checkout
that loses it cannot name its own compose project, network or ownership
labels until `ciu env generate` puts it back. `ciu clean --vanilla` removes it
deliberately (that is a full reset); plain `ciu clean` preserves it.

## 12. The implementation gate (Assay-backed, S18)

CIU's gate is judged by the **released Assay CLI**, pinned and vendored in the
repository — not installed ambiently. You can reproduce the gate's evidence
locally (the container part still needs the operator's four-traps recipe):

```bash
# 1. Verify the pinned Assay artifact (fails the gate if it ever drifts)
sha256sum -c ciu/tools/assay/assay-3.2.0.pyz.sha256

# 2. Inspect the declared lane (validates config, runs nothing)
cd ciu && .venv/bin/python tools/assay/assay-3.2.0.pyz lanes --file assay.toml

# 3. Run the lane; Assay snapshots the commit, runs the full suite at 100%
#    line+branch, and judges the changed-line floor on base..HEAD (R1).
#    The verdict goes OUTSIDE the judged tree (gitignored .assay/).
cd ciu && mkdir -p .assay && \
  .venv/bin/python tools/assay/assay-3.2.0.pyz run ciu \
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

### You no longer have to remember to run it (`ciu up`'s own preflight, S13.4c)

Since CIU-64, **`ciu up` runs this same static pipeline itself, by default**,
before STEP 1 — before any hostdir, secret or container exists — and refuses
on any ERROR-severity finding with exit `2`, exactly as it already refuses on
an `[S7.x]` provisioning-graph failure. Nothing opts in.

```console
$ ciu up --profile core
[INFO] Preflight: running `ciu check`'s static validation (S13.4a, CIU-64)
...
[INFO]   [x] hooks-preflight: fail
[ERROR]         infra/db-core: [post_compose_db.py] registry.database is missing
[ERROR] [S13.4a] `ciu check` found ERROR-severity finding(s) — refusing to deploy
        before anything starts. ...

$ ciu up --profile core --skip-check      # break-glass, and it says so
[WARN] --skip-check: skipping the `ciu check` static preflight (S13.4a,
       break-glass) — a configuration error it would have refused now surfaces
       mid-deploy, after stacks have already started
```

You still run `ciu check` explicitly when you want the `--json` envelope, the
`--live` probes, or a validation pass with no deploy attached.

### Teach a hook to validate its own config (`validate_config`, S9.5)

Any hook may add an OPTIONAL second entry point beside its `run`. CIU calls it
during `ciu check` **and** during `ciu up`'s automatic preflight above — the
same side-effect-free call; your `run()` is never invoked by either:

```python
# infra/db-core/post_compose_db.py

def run(config: dict, ctx) -> dict:
    """Normal execution — provisions users, databases."""
    ...


def validate_config(config: dict, ctx) -> list:
    """Optional preflight. Return one finding per problem; [] = OK.

    Receives the SAME merged, guarded config and HookContext that run() gets,
    so it validates exactly what run() will consume — before any container
    exists.
    """
    findings: list = []
    registry = config.get("registry", {})
    if "database" not in registry:
        # A bare string is an ERROR: it blocks `ciu check` (exit 2) and
        # refuses `ciu up`. This is the pre-CIU-65 shape and still means
        # exactly what it always did.
        findings.append("registry.database is missing")
    users = registry.get("postgresql", {}).get("users", {})
    for user in ("controller", "workerdb"):
        if user not in users:
            findings.append(f"registry.postgresql.users.{user} is missing")

    # A (severity, message) pair lets a finding be worth knowing WITHOUT
    # being must-block. WARN is printed as a note and blocks nothing.
    if "readonly" not in users:
        findings.append(
            ("WARN", "registry.postgresql.users.readonly is absent — the "
                     "reporting sidecar will fall back to the owner role")
        )

    # Secrets appear as SecretGuard objects (S4.21): you can confirm one is
    # DECLARED by name, and you must never stringify it.
    if "admin_password" not in config.get("db_core", {}).get("secrets", {}):
        findings.append("db_core.secrets.admin_password is not declared")

    return findings
```

Rules worth knowing before you write one:

- **Return a `list` of findings, never a bool.** `[]` means valid. A
  `True`/`False` return is reported as a contract violation, not read as a
  verdict. `None` is tolerated as "no findings".
- **Each finding is a message string, or a `(severity, message)` pair**
  (a 2-element tuple *or* list — both work). The severity vocabulary is
  exactly **`WARN`** and **`ERROR`**, matched case- and
  whitespace-insensitively, so `"warn"` and `" Error "` are both fine. A bare
  string means `ERROR`. Anything else — `"warning"`, `"info"`, `"NEVER"` — is
  REFUSED as its own ERROR finding naming the two accepted values, rather
  than guessed at: a typo must never quietly downgrade a blocking finding.
  (`NEVER` is excluded on purpose. It is an `ciu.exit_on` *threshold*, not
  something a finding can be.)
- **`ERROR` blocks, `WARN` does not.** An ERROR fails the `hooks-preflight`
  stage: `ciu check` exits 2 and `ciu up`'s preflight refuses. A WARN is
  recorded as a stage NOTE — it prints as `note: [WARN] …`, appears in the
  `--json` envelope's `notes` array with the same `stack`/`hook` keys a
  finding carries, and changes no exit code and blocks no deploy.
  `ciu.exit_on` / `$CIU_EXIT_ON` deliberately do NOT affect this routing:
  your machine-readable check verdict must not change with ambient shell
  state.
- **`ctx.secret_file(name)` raises `KeyError` for every name.** `ciu check`
  materializes nothing, so there is no store file to point at. Validate that a
  secret is *declared* (as above); if your check genuinely needs to read a
  materialized secret's contents, it cannot run at check time — keep that part
  in `run()`.
- **`ctx.wait_healthy` / `ctx.wait_tcp` are `None`** at check time. Nothing is
  running, and a preflight must not perform I/O anyway.
- `ctx.stack_dir`, `ctx.repo_root`, `ctx.instance_id`, `ctx.network`,
  `ctx.identity_unreadable` (CIU-80 — see §10 above), `ctx.selected_profiles`
  and `ctx.deployed_stacks` are all populated exactly as during a real run.
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

## 15. Adopt a shipped hook template instead of hand-writing one (`ciu init --hooks`, S19.1)

`ciu init` can copy a hook implementation straight out of CIU's own hook
template library (`ciu.hook_templates`) into a stack it scaffolds, instead of
you writing one from scratch:

```console
$ ciu init --project-name myapp --stacks db-core --hooks post_compose_db
wrote ciu.global.defaults.toml.j2
wrote applications/db-core/ciu.defaults.toml.j2
wrote applications/db-core/ciu.compose.yml.j2
wrote applications/db-core/hooks/post_compose_db.py
```

`--hooks NAME1,NAME2` copies each named template into **every** stack this
same invocation scaffolds (here, just `db-core`), at
`<stack_dir>/hooks/<name>.py`. Read the first line of the copy — it is a
stamp CIU writes at copy time, not part of the template's own code:

```console
$ head -1 applications/db-core/hooks/post_compose_db.py
# ciu-hook-template: post_compose_db.py rev=1
```

That `rev=1` is the template's `template_revision` **at the moment you
copied it** — your own copy from here is yours to edit freely; CIU never
touches it again. A future revision-comparison feature can read this exact
line back to tell you when upstream has moved past the revision you copied.

Two things worth knowing before you wire it up:

- **The copy is inert until you declare it.** `ciu init` writes the file; it
  does not add `[<root>.hooks].post_compose = ["./hooks/post_compose_db.py"]`
  to your stack's `ciu.defaults.toml.j2` — add that yourself, the same way
  any other hook is declared (S9.1).
- **An unknown template name refuses before anything is written**, naming
  the bad name and the available list (exit 2); so does `--hooks` given with
  no `--stacks` to copy into. Like every other `ciu init` output file, a copy
  is never silently overwritten — an existing `hooks/post_compose_db.py`
  makes the whole run refuse, naming it.

CIU ships one reference template today, `post_compose_db.py` — deliberately
generic (a database-readiness wait via `ctx.wait_healthy` plus a
`ctx.secret_file` presence check) and honest about not being a real
PostgreSQL/MySQL/etc. bootstrap. Treat it as a starting point to copy and
extend with your own provisioning logic.

## 16. Fan a service out by `instances` instead of hand-rolling a compose loop (V8-PREP-6)

Before this, a replicated service needed a hand-written compose loop AND,
separately, a configfile section that either restated the same count or
relied on the S5.3 base-selector mechanism to fan a single shared render
out — two independently-maintained mechanisms that could silently disagree.
V8-PREP-6 unifies them: declare `instances` once, and BOTH the compose
template's own loop and the configfile's per-instance render read from the
one CIU-resolved count.

```toml
[myapp.api]
instances = 3    # sibling of [myapp.api.configfile.*], not inside it

[myapp.api.configfile.main]
template  = "config.toml.j2"
target    = "/etc/api/config.toml"
instances = 3    # REQUIRED restatement -- see the warning box below
```

The compose template reads the resolved count from `ciu.instances`, never
from `myapp.api.instances` directly (that only works when the count is
declared at the service level; `ciu.instances` also carries the resolved
value when a configfile is the ONLY thing that declared it):

```jinja
services:
{% for i in range(1, ciu.instances.api + 1) %}
  api-{{ i }}:
    image: {{ myapp.api.image_name }}:{{ myapp.api.image_tag }}
    environment:
      - API_INSTANCE=api-{{ i }}
{% endfor %}
```

`{% for i in range(1, ciu.instances.api + 1) %}` naming `api-{{ i }}` is the
sanctioned loop shape — 1-based, matching exactly what
`_configfile_mount_services` already understands for the configfile side
(S5.3/S7.5b). CIU does **not** generate compose service blocks itself: your
own `{% for %}` loop still writes the YAML, now driven by one CIU-resolved
count instead of a value you'd otherwise have to keep in sync by hand. A
template that only needs to know WHETHER a service fans out (not by how
much) checks membership: `{% if 'api' in ciu.instances %}`. A service with
no declared `instances` anywhere (service-level or configfile-level) is
simply absent from the mapping — never present with a value of `1`.

Unlike `ciu.selected_profiles`/`ciu.deployed_stacks` (§10 above), which fail
loudly with a Jinja `UndefinedError` when referenced outside a deployment
render, `ciu.instances` itself is **always present** in every deployment
render's context — an empty mapping `{}` when nothing anywhere declares a
fan-out count, never an absent name (CIU-74). This is deliberate: `'api' in
ciu.instances` is the sanctioned way to ask "does this service fan out at
all", and that idiom must keep working with no `instances` declared anywhere
— an absent name would make the membership test itself raise under CIU's
`StrictUndefined` render environment (see the note below).

> **CIU-74 (StrictUndefined):** every Jinja render in CIU raises
> `jinja2.UndefinedError` on a reference to an undefined name, attribute, or
> item at any depth — a mistyped leaf like `{{ deploy.environment_tg }}`
> (missing the `_tag`) now fails the render instead of silently producing
> `dstdns--postgres`. `ciu.instances` being always-defined-but-possibly-empty
> is what keeps the membership idiom above legal under that same strictness.

> **The configfile's `instances = 3` above is required, not decoration.** A
> configfile that OMITS `instances` while its service declares one > 1
> REFUSES (S7.5e) — it does not silently inherit the service-level count.
> Before V8-PREP-6, an omitted configfile-level `instances` meant "render
> once, let the S5.3 base-selector mechanism fan the same shared file out to
> every replica"; silently reinterpreting that omission as "render N
> independent times" the moment a service declares `instances` would be a
> silent behavior change for any stack already shaped this way. Restate the
> same value explicitly to opt into the new per-instance render (as above),
> or don't declare `instances` on `[myapp.api]` at all if this configfile is
> intentionally a single shared render. See
> [SPEC.md S7.5e](SPEC.md#s7--orchestration-ciu-up)
> and [CHANGES.md](../CHANGES.md) — the `applications/workers` test-repo
> stack had exactly this shape and was migrated to this pattern as part of
> landing V8-PREP-6.

Declaring a service-level default and a configfile-level value that
DISAGREE (e.g. `instances = 3` on `[myapp.api]` but `instances = 5` on its
configfile) refuses too, naming both values — the two mechanisms can never
silently drift apart.

## 17. Migrate a hand-rolled `internal_host` override to `--shared-infra-ref-services` (S16.1a, CIU-49/CIU-52)

Before CIU-52 shipped the reference-service addressing described in
[CONFIG.md's shared-infra join example](CONFIG.md#shared-infra-join-example-s161)
(specifically its `--shared-infra-ref-services` sub-section, S16.1a), a
consumer joining a reference instance's shared infra could reach it —
`--shared-infra` connects the network — but had no CIU-derived NAME to call
its service by. The one real case on file (CIU-49) is dstdns's
`dstdns-mstest` worktree template, which hand-types the reference vault's
already-qualified container name straight into its own override:

```toml
internal_host = "dstdns-mstest-f2d1cb-vault"  # instance config: scoped (GUIDE 3.6)
```

That value is correct on the day someone types it and never checked again.
If the reference instance is ever re-created under a new identity — a new
`INSTANCE_ID`, a new network, the ordinary result of a `ciu worktree rm`
followed by a fresh `add` — the container this string names is simply gone,
and nothing here re-checks it: the frozen literal ships forward unchanged,
and every worktree template copied from `dstdns-mstest` afterward carries
the same stale name one copy-paste further from the instance it was
actually true for.

`--shared-infra-ref-services` replaces the hand-typed literal with a
CIU-derived, re-authenticated one. Joining the same reference and addressing
its vault by the alias `vault` (values below are illustrative — the fixture
used to verify this example, not the mstest environment's own actual
project/instance identity):

```console
$ ciu worktree add mstest --base main --profile core \
    --shared-infra primary-ref \
    --shared-infra-services api \
    --shared-infra-ref-projects idp-dev-idp \
    --shared-infra-ref-services vault
worktree ready: /repo/.worktrees/mstest
  next: cd /repo/.worktrees/mstest && ciu up
```

produces this instance's own `[topology.services.vault]` block, derived —
never hand-typed — from the reference's live, rendered configuration:

```toml
[topology.services.vault]
internal_host = "dstdns-aaaaaa-vault"
internal_port = 8200
```

The exact flag grammar, the `[ciu.instance.shared_infra.ref_services.vault]`
sub-table this is derived from, and the full derivation/authentication
mechanism are CONFIG.md's material (cross-referenced above), not repeated
here.

> **What the migration buys you.** The hand-typed override is checked
> exactly once — by whoever typed it, against whatever was running that
> day. `ref_services` is re-derived at every `add`/`create`/`ensure`/`adopt`
> that declares it, and re-authenticated against live Docker state again at
> every `ciu up` that joins the network: CIU re-renders the reference's own
> config, re-derives its qualified container name, and re-proves that exact
> container is live on the reference's network before writing or trusting
> it. A reference re-created under a new identity fails loudly at the next
> `ciu up` (CONFIG.md's "never hand-edit the recorded `container`" note); a
> hand-typed override instead fails silently, whenever the application next
> happens to need that connection.

This is specifically the case of naming a REFERENCE instance's service
after joining its network (S16.1a). Qualifying a stack's OWN
`internal_host`/`hostname:` default — no shared-infra join involved — is
the separate case covered in §5e above.

## 18. Write a stack's `build.context` (and `dockerfile`) as repo-root-relative (S8.1a, CIU-71)

Every compose invocation CIU makes passes `--project-directory <repo root>`
(S8.1a), so a relative `build.context` in a stack's compose template
resolves against the **repo root**. This is NOT the convention CIU's other
relative paths follow — hostdirs, `ASK_FILE` secret sources, and configfile
schema/template paths all resolve **stack-dir-relative** (see the note
below) — `build.context` is the deliberate exception, because a Dockerfile
`COPY` of a repo-shared asset needs the repo root, the same way a
consumer-repo build would. It does NOT resolve against the compose file's
own directory, which is `docker compose`'s default when no
`--project-directory` is given.

**`dockerfile:` moves with `context:`.** Compose resolves a `build.dockerfile`
path relative to `build.context`, not relative to `--project-directory`
directly (the same rule `ciu dev`'s own `_build_dev_image` applies —
`src/ciu/dev.py`'s `Path(context) / dockerfile`). Once `context` becomes
repo-root-relative, an UNSET or bare `dockerfile: Dockerfile` now looks for
`<repo root>/Dockerfile`, not `<stack dir>/Dockerfile` — almost never what a
stack author wants. **Both keys need to move together.**

**`ciu dev`'s `[<root>.dev].build.context`/`dockerfile` share this exact
convention (CIU-79).** `ciu dev` runs a plain `docker build`, not `docker
compose`, so there is no `--project-directory` flag to reach for — CIU
resolves `context` to an absolute repo-root-relative path itself before
building the `docker build` argv, then joins `dockerfile` onto that same
resolved context, matching this section's rule exactly. A `[<root>.dev]`
profile's `build.context = "."` therefore means the same thing a stack's
`ciu.compose.yml.j2` `build.context = "."` does — the repo root, not the
stack dir — so a stack that dual-ships both a production `build` block and
a `[<root>.dev].build` block can write `context`/`dockerfile` identically in
both places.

> **This is a BREAKING change for `ciu dev`, not purely additive** (unlike
> the rest of this bundle): every `[<root>.dev].build` written before
> CIU-79 assumed `context`/`dockerfile` resolved against the STACK DIR (the
> bug this fix closes), so a profile whose Dockerfile lives in the stack
> directory — the common shape, since that was the only one that ever
> worked — now fails to find it. An UNSET or bare `dockerfile: Dockerfile`
> (or an explicit `context = "."` with no `dockerfile` override) now looks
> for `<repo root>/Dockerfile`, not `<stack dir>/Dockerfile` — almost never
> what a stack author wants, same as the compose-side migration note below.
> **If you have an existing `[<root>.dev].build` block**, either move the
> Dockerfile to the repo root, or point `dockerfile` at its real,
> repo-root-relative location: `dockerfile = "<stack-path>/Dockerfile"`
> (e.g. `dockerfile = "apps/api/Dockerfile"` for a stack at `apps/api/`) —
> both keys move together, exactly as the compose-side rule above states.
> There is no dstdns/vbpub consumer stack affected today (no shipped
> `.j2`/fixture declares `[<root>.dev].build`), but a downstream consumer's
> own repo may have one.

Concretely, if a stack directory `infra/mock-targets/` declares:

```toml
# infra/mock-targets/ciu.defaults.toml.j2
[mock_targets.image]
build_context = "."
dockerfile = "infra/mock-targets/Dockerfile"
```

and its Dockerfile `COPY`s a path relative to the **repo root**:

```dockerfile
# infra/mock-targets/Dockerfile
COPY tests/fixtures/mock_data /data/mock_data
```

then `tests/fixtures/mock_data` MUST live at the repo root
(`<repo>/tests/fixtures/mock_data`), not inside `infra/mock-targets/`. This
is the natural, repo-root-relative way to write it — matching how
`build.context` now resolves — and it is what `ciu up` actually runs
against. Live-verified (see `nyxloom-trove/reports/ciu-P37-REPORT.md`): the
`dockerfile:` line above is not optional — omitting it and leaving the
compose default (`Dockerfile`, resolved against the now-repo-root context)
fails `failed to read dockerfile: open Dockerfile: no such file or
directory`.

**`.env` also relocates.** Compose loads a bare `.env` file from the
**project directory** (S8.1a) ONLY — it does not also check the compose
file's own directory as a fallback. So a stack-local `.env` beside
`ciu.compose.yml` stops being read at all once `--project-directory` points
at the repo root: it is dropped unconditionally, whether or not a
repo-root `.env` exists (live-verified: with a stack-local `.env` only and
no repo-root `.env`, the variable it set came back unset post-fix, not
"shadowed" by anything). CIU itself never depends on this (it always passes
the compose process environment explicitly, S8.2, and never relies on
compose's own bare-`.env` loading), so this only matters for a stack with
its OWN stack-local `.env` a maintainer authored outside CIU's secret/config
mechanisms — check for one before upgrading.

> **If you are migrating a stack that carried a workaround for the pre-CIU-71
> bug** (a stack-relative `build_context`, e.g. `"../.."`, in an untracked
> `ciu.toml.j2` override, to compensate for compose resolving `.` against the
> compose file's own directory): remove the override and go back to the
> plain repo-root-relative form (`"."` or a repo-root-relative subpath) for
> BOTH `build_context` AND `dockerfile` — a stack that only moved
> `build_context` back and left `dockerfile` stack-relative (or unset,
> defaulting to `Dockerfile`) breaks the same way the live repro did, just
> one field over. Also check for a stack-local `.env`: if one exists beside
> the compose file, move its values into CIU's own config/secret mechanisms
> or into a repo-root `.env` — it will no longer be read from its old
> location at all.

This applies to BOTH the native `up` path and the `--shipped` passthrough
(S8.6) — a maintainer's own pre-shipped `docker-compose.yml` gets the same
`--project-directory` treatment as a CIU-rendered `ciu.compose.yml`.

**Note on CIU's OTHER relative paths (S8.1a):** `build.context`/`dockerfile`
are the ONE thing CIU deliberately makes repo-root-relative via
`--project-directory`. Everything else CIU resolves relative to a path
(hostdir `vol-*` directories, `ASK_FILE` secret sources, configfile
schema/template paths) resolves **stack-dir-relative**, not repo-root-
relative — the two conventions genuinely differ within the same stack. See
S8.1a for the full accounting and why.
