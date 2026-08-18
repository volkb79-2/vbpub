# cmru SPEC

CIU conventions apply: section numbers are stable identifiers (S-numbers).
Breaking changes to a section bump the wheel MAJOR and include the S-ID in the changelog.
RFC 2119 key words (MUST, SHOULD, MAY, etc.) are normative.

---

## S-CLI — CLI at a glance (the intuitive contract)

cmru is one CLI over a monorepo of independently-versioned **projects**. Everything a user
touches is named `cmru.*` so the association is unambiguous.

### Verbs, in the order you use them

```
cmru status                 # 1. preview: what changed + the next version (read-only)
cmru release                # 2. isolated transaction: prepare → gate → integrate → tag → build → publish
   ├─ cmru build            #    local-only transaction: prepare → gate → build_step → retain output
   └─ cmru publish          #    run an explicit project's push step
cmru worktrees              # discover retained failed build/release worktrees (read-only)
cmru changelog --project P --backfill-tag TAG  # migration: catalog an already-published tagged release
cmru cleanup --remove-assets 30d   # 3. prune old releases/images (optional)
cmru cleanup --project P --delete-unmanaged-release-tag TAG --yes
                                  # delete one old GitHub Release only, never its Git tag
cmru cleanup --project P --delete-build-output ID --yes
                                  # delete one exact local non-release output record
cmru cleanup --discard-build-worktree PATH --yes
                                  # discard one exact inspected failed build worktree
cmru version                      # print the CMRU version

cmru resolve --project P    # consumer: highest-semver published version  (read-only)
cmru get     --project P    # consumer: emit a standalone installer       (read-only)
cmru run     [--build --push ...]  # escape hatch: run explicit steps × projects
cmru run-step --config C --step S  # raw single-step runner (rarely needed)
```

**S-CLI.1** `release` is the normal path. A failed transaction retains its worktree for
inspection; `--resume <worktree>` is an explicit attempt to continue that exact retained
source transaction, not a durable publish-phase retry. cmru MUST NOT silently reuse, move,
or republish an existing tag. Durable post-tag publication recovery is not implemented; see
KI-06.

**S-CLI.2** `status` and `release` MUST operate only on the orchestrated set
(`orchestration.project_order`); a project is released only once it is listed there.

**S-CLI.3** Verbs that write to the host or source tree (`release`, `changelog`, `build`,
`publish`, `run`) MUST be
clearly distinguished in `--help` from read-only verbs (`status`, `resolve`, `get`).

**S-CLI.4 — Retained-worktree discovery.** `cmru worktrees` is read-only and derives the
current Git repository without loading a CMRU config. It MUST list every CMRU-managed
`cmru/release/*` and `cmru/build/*` worktree, including a path not visible through the current
bind-mount view. It MUST print the exact `--resume` or `--discard-build-worktree` command only
for a visible path; it MUST never guess a cleanup target.

**S-CLI.5 — Isolated release transaction.** `release` MUST NOT publish from the caller's
working tree. It acquires a repository-local exclusive lock, rejects local-only commits on
local `main` that the remote snapshot would omit (and warns if local `main` is behind), and
rejects any uncommitted change (tracked or untracked) under a project's own path for every
project in this run's scope (`--project <name>`, else every orchestrated project — the same
`project_order`-derived set `release` itself iterates, not the possibly-different
`orchestration.default_projects`) — skipped entirely for `--dry-run` (nothing is published, so
there is nothing to protect). `--allow-uncommitted` overrides this second check only; there is
no override for local-only commits. It then fetches `origin/main`, creates an ephemeral
`cmru/release/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>` worktree (KI-16; see S-CLI.5b) at that exact
remote commit, and re-execs there. All caller
working-tree edits that survive the preflight (i.e. that don't touch a released project's path)
are still ignored: they cannot enter the immutable remote snapshot regardless.

**S-CLI.5a — Projects release one after another, not in a shared batch.** Inside the worktree,
every changed project (`orchestration.project_order`, filtered to what actually changed) runs
its own full cycle — prepare → gate → promote → tag → build → publish — to completion before
the next project's cycle begins. Before any given project's tag or public artifact, cmru MUST
run that project's declared `run-tests` gate in its real gate environment, then fast-forward
`origin/main` from the worktree's current `HEAD` (a non-fast-forward remote update aborts
before that project's publication). The origin backup branch (durability: a crashed machine or
lost worktree still leaves an inspectable, resumable copy on origin) is pushed once up front
and refreshed again after each project's own promote, so it stays current with this run's
progress rather than forever holding only the pre-run base. This ordering is what lets a later
project (e.g. an OCI image) resolve an earlier project's (e.g. a wheel) brand-new release
within the same `cmru release` run, instead of always trailing one run behind.

On success: the origin backup branch and the local worktree/branch are removed, and the
caller's local `main` is synced with `origin/main`: a fast-forward when local main hasn't
moved (the common case), or a `git rebase` when it has (e.g. ongoing work in another terminal
while the release built) — rebase, not merge, to stay consistent with the rest of this
pipeline, which is fast-forward-only end to end (`promote_workspace`'s push,
`revert_promotion`'s push, the "local main not ahead" precondition below); no other step here
ever produces a merge commit. Safe to replay because a release only ever commits declared,
mechanical generated paths (S-REL.4a), never hand-edited source, so local commits essentially
never touch the same files. The rebase is aborted, leaving local main untouched, only on a
genuine content conflict.

```
Before the release:
  origin/main:     ──●(base)
  local main:      ──●(base)                          [repo_root's own checkout — untouched]
  release branch:  (does not exist yet)

The release runs entirely inside an ISOLATED WORKTREE — a separate checkout on
its own cmru/release/<id> branch. repo_root's own `main` is never checked out,
never touched, during any of this. push_backup_branch runs once, up front
(origin gets a copy of this branch for durability, before any project starts).
Each changed project then promotes SEPARATELY, one after another (S-CLI.5a) —
shown here for two projects, "alpha" then "beta":

  release branch:  ──●(base)──●(A1: alpha's prep/tag commit, if any)
                                │
                      promote_workspace → git push origin HEAD:refs/heads/main
                                ▼
  origin/main:     ──●(base)──●(A1)          ← alpha fully tagged/built/published here;
                                                checkpoint records A1 as the last full success

  release branch:  ──●(base)──●(A1)──●(B1: beta's prep commit, if any)
                                       │
                           promote_workspace → git push origin HEAD:refs/heads/main
                                       ▼
  origin/main:     ──●(base)──●(A1)──●(B1)   ← beta fully tagged/built/published here;
                                                checkpoint advances to B1
  local main:      ──●(base)                 ← still here; nothing touched it

Meanwhile, if the caller committed their own work locally while the release built:
  local main:      ──●(base)──●(D1)──●(D2)             [unrelated local work]

sync_local_main rebases local main onto the new origin/main tip, once, after every
project in this run has finished:
  local main:      ──●(base)──●(A1)──●(B1)──●(D1')──●(D2')    ← D1/D2 replayed (new hashes), linear
```

Deleting the release branch on success (both locally and its origin backup) is cleanup of a
now-redundant ref — A1/B1 are already permanently part of `origin/main`'s history, so the
branch's job is done. That deletion does nothing to `local main` by itself; `sync_local_main`
is the only step that touches it.

On failure: the local worktree/branch and its origin backup are retained for inspection —
`release` never resumes one automatically; the caller explicitly chooses `--resume <path>` to
continue that exact attempt, or lets the next `release` invocation start fresh instead (the
normal/default case: gates must re-validate against a fresh snapshot rather than a debugged,
possibly hand-edited one — see S-REL.4a). Because projects release one after another
(S-CLI.5a), a failing project's own promotion — if it landed before the failure — is reverted
*without disturbing any earlier project in the same run that already fully released*: cmru
tracks a checkpoint (the commit as of the last project to fully succeed, written after each
project's complete cycle, and seeded to this run's own `base` before the loop starts — so a
`--resume` reusing the same branch/token never reads a stale checkpoint left over from an
earlier, different attempt on that token) and reverts only `(checkpoint, origin/main]`, never
the whole transaction's `(base, origin/main]` range. If the failing project is the first one in
the run and never got as far as its own promote, the checkpoint equals `base` and this degrades
to "nothing to revert" — the classic all-or-nothing case. (The checkpoint tracks source-tree
commits only: a project with no `prepare` step commits nothing of its own, so the checkpoint can
still equal `base` even after that project's tag and published artifact are real — those are
untouched regardless, since a source-tree `git revert` never touches tags/Releases/registry
pushes.) The revert itself is always a plain
`git revert` commit pushed on top of `origin/main` — never a force-push or history rewrite. It
is skipped, requiring manual cleanup, if it does not apply cleanly or if `origin/main` has
advanced past the release since promotion (a concurrent push landed on top). Local `main` is
synced with `origin/main` (fast-forward or rebase, as above) regardless of outcome.
On a later `release` invocation (fresh or via `--resume`), each already-fully-released project
in the failed attempt shows as unchanged (S12.2 is tag-based) and is skipped automatically —
only the reverted project and anything after it in `project_order` are attempted again.

**`--abandon <path>|all-previous`** discards a retained attempt instead of resuming it, then
proceeds with a normal fresh release in the same invocation: its origin backup branch, local
worktree/branch, and scope marker are removed (never touching `origin/main` — a retained
attempt's gates ran, if at all, before promote, so there is nothing there to undo). `all-previous`
abandons every retained worktree whose recorded project scope overlaps this run's — `--project X`
narrows that to just `X`; otherwise it's the full `orchestration.default_projects`. Worktrees
retained before this feature existed (no recorded scope) are left for an explicit `--abandon
<path>`. `--resume` and `--abandon` are mutually exclusive. `cmru.release.sh` never abandons
a failed worktree implicitly: inspect it, resume it explicitly when appropriate, or explicitly
request `--abandon <path>|all-previous` after its logs and artifacts are no longer needed.

The repository-root secret document is copied mode `0600`, never committed.

**S-CLI.5b — Transaction branch/worktree naming (KI-16).** Every `cmru/release/*` and
`cmru/build/*` transaction this tool creates is named:

```
branch:     cmru/<purpose>/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>
directory:  .worktrees/cmru-<purpose>-<YYYYMMDD-HHMMSS>-<scope>-<uuid8>
```

`<purpose>` is `release` or `build`. `<YYYYMMDD-HHMMSS>` is UTC, for chronological sort.
`<scope>` is the `--project` value when the run is scoped, sanitised to `[a-z0-9-]`, else
`all`. `<uuid8>` is 8 hex characters from `uuid4()` and MUST NOT be removed or made
deterministic: cleanup (`remove_workspace`, `abandon_workspace`) depends on a transaction being
able to assume it exclusively owns the name it created, so the scheme MUST stay
collision-free even for two runs on the same scope in the same second. The directory name is
the branch name with every `/` replaced by `-`, 1:1 derivable in both directions — never
computed by `mkdtemp`-then-`rmdir`. `git worktree add` on its own is NOT sufficient here: it
fails closed on a non-empty existing directory but silently ADOPTS an empty one, which would
violate "this ONE transaction exclusively owns the name it created" if a uuid8 collision or a
stale leftover ever left an empty directory in the way — the code MUST therefore refuse
explicitly (any existing path, empty or not) before ever calling `git worktree add`.

Both `startswith("cmru/release/")` and `startswith("cmru/build/")` are preserved by this
scheme, so every existing refspec/glob keeps working, and a worktree retained under the OLDER
`cmru/<purpose>/<12-hex>` naming remains just as discoverable (`cmru worktrees`,
`list_cmru_workspaces`), resumable (`--resume`), and removable as one created under the new
scheme — nothing in discovery or cleanup parses the directory name; only the branch prefix and
whatever `git worktree list --porcelain` itself reports are load-bearing.

### File conventions (all `cmru.`-prefixed)

| File | Tracked? | Purpose |
|---|---|---|
| `<project>/cmru.toml` | committed | Complete portable product contract. **No secrets.** |
| `cmru.orchestration.toml` | committed | Optional estate ordering/dependencies/cleanup only. |
| `cmru.secret.toml` | gitignored | Repository credential document; optional explicit per-project overrides (see S2.4). |
| `cmru.project.sample.toml` | committed | Template for a project contract (no secrets). |
| `cmru.vars` | gitignored | Generated `KEY=VALUE` build vars a step emits for later steps. |
| `cmru` console script | installed | Canonical portable entry point for every verb. |
| `cmru.release.sh` | committed | vbpub-only convenience wrapper for the complete estate release. |
| `cmru/build-initial-standalone.sh` | committed | Fresh-checkout bootstrap that builds the first CMRU wheel without CMRU installed. |

**S-CLI.4** The names `release.toml`, `release.sample.toml`, `.release-vars`,
`build-push.toml`, `release-all.py`, `release-runner.py` are **retired and removed** — no
legacy remains. The installed `cmru` console script is the only general release entry
point; vbpub additionally keeps `cmru.release.sh` as a convenience wrapper.

---

## S0 — Terminology

| Term | Definition |
|---|---|
| **project** | A named unit of releasable work within a monorepo (e.g., `ciu`, `tls-edge`, `pwmcp`). |
| **artifact** | The published output of a build step: `wheel`, `oci-image`, `tarball`, or `bundle`. |
| **prefix** | The per-project tag prefix, e.g., `tls-edge-v`. Uniquely identifies a project on the Releases page. |
| **tag** | An immutable git tag of the form `<prefix><semver>`, e.g., `tls-edge-v0.2.0`. |
| **release** | A GitHub Releases entry whose `tag_name` equals a `<prefix><semver>` tag. |
| **sidecar** | A `.sha256` file uploaded alongside an artifact containing its `sha256sum -c`-compatible checksum. |
| **latest.json** | A thin pointer file (`<prefix>latest/latest.json`) recording the highest-semver tag, no asset duplication. |
| **runner** | The cmru component that executes a single build step in a reproducible, logged environment. |
| **host** | A release storage provider implementing the `ReleaseHost` interface (S11). |
| **resolver** | The cmru component that returns `{version, tag, asset, sha256, url}` for the highest-semver release. |
| **get.py** | A per-project emitted Python 3 bootstrap installer implementing the S6 contract (ships inside the artifact). |

---

## S1 — Project & Artifact Model

cmru manages N independent projects, each with its own semver line, all sharing **one** GitHub Releases page per repository.

**S1.1** Each project has a `prefix` that MUST be unique within the repository. Tags take the form `<prefix><semver>` (e.g., `tls-edge-v0.2.0`). Tags are immutable once pushed; updating a tag is a violation of this SPEC.

**S1.2** Supported artifact types:

| Type | Description | Source |
|---|---|---|
| `wheel` | Python distribution wheel (`.whl`) | `python -m build` |
| `oci-image` | Container image | a project-declared image build command |
| `tarball` | Archive (`.tar.xz`, `.tar.gz`) | `tar` + custom build |
| `bundle` | Deterministic release bundle (`.tar.xz`) + `manifest.json` + `manifest.json.minisig` | project allowlist + cmru bundler |

**S1.3** An artifact name is an inventory label, not a release-host profile. A project
MUST state its real publication behavior in its explicit `push` command. Where that
command publishes a GitHub Release asset, it MUST upload the artifact and a `.sha256`
sidecar containing one `sha256sum -c`-compatible line.

**S1.6** The `bundle` artifact is a **triple**: a deterministic `<name>.tar.xz` archive
(byte-identical across builds from the same commit and `SOURCE_DATE_EPOCH`), a canonical
`manifest.json` (Seam 3 schema; see S9.5), and a detached Ed25519 signature
`manifest.json.minisig`. The manifest is the root of authenticity for remote
deployment: it pins every content-addressed asset (wheel sha256, image digest) so the
installer (SPEC A) can verify the entire release transitively from a single trusted
signature check.

A `bundle` (or `tarball`) MAY additionally declare **per-interpreter variants** (S-REL.6):
one release tag then carries N distinct triples, one per variant (e.g. a `py39` and a
`py311` bundle, each with version-locked C-extension wheels). Each variant's assets are
named `<tag>-<variant>.tar.xz` (+ its `.sha256`, and for `bundle` its `manifest.json` +
`manifest.json.minisig`). With **no** declared variants the artifact is a single
`<tag>.tar.xz` triple exactly as before.

**S1.4** OCI image projects SHOULD publish an immutable image reference and verify its
manifest digest. Whether CMRU also mints a Git tag is controlled only by
`project.release.git_tag`; `oci-image` does not silently choose either policy.

**S1.5** N projects, one Releases page is the first differentiator. The `prefix` mechanism is the key: the resolver (S5) and get.py (S6) filter by prefix, so projects never interfere with each other.

---

## S-REL — Release model

A `cmru release` separates generic source policy from project-owned artifact behavior:

**S-REL.1 — Versioning** (`[project.version].strategy`): `scm` | `counter` | `file:PATH`
| `external:VAR` | `none`. It determines version discovery only. `external:VAR` reads its
value from transaction-local `cmru.vars` written by `steps.prepare`; `none` leaves identity
to the declared project commands.

**S-REL.2 — Outputs and tag policy.** `[project].artifacts` is a non-empty inventory of
`wheel`, `oci-image`, `tarball`, and/or `bundle`. It never produces a command. The required
`[project.release].git_tag` boolean alone determines whether CMRU mints and pushes
`<prefix><version>`. `version.strategy = "none"` requires `git_tag = false`; every other
combination is deliberate project policy.

**S-REL.3 — CMRU is the orchestrator; the project owns the *how*.** cmru only performs the
**generic** git/host side-effects it can do for any project — mint+push `<prefix><semver>`,
commit declared generated paths, push the commit. The artifact-specific work (build the
wheel/image/bundle, create the GitHub Release + upload assets, push to ghcr, write
`latest.json`) is performed by the **project's own required step commands**. cmru never
hardcodes a project's file paths or infers a step from an artifact label.

**S-REL.4a — Prepared source is source-first and fail-closed.** A `steps.prepare` command
MAY derive a version or regenerate mechanical source inputs. Every tracked output MUST be
declared in `release.commit_generated`; cmru rejects undeclared writes, commits only declared
paths, gates that commit, fast-forwards remote main, and only then tags/builds/publishes.
Projects that derive a version MUST use `external:VAR` so cmru owns the annotated tag.
Every managed project receives the project-relative `CHANGES.md` generated output by default.
CMRU derives the project-scoped commit range, inserts one marked section at that document's
`<!-- cmru: release history -->` marker (creating the document and marker when absent), commits
it before the gate, and refuses to overwrite a hand-authored same-version section. A tagged
release uses the pending version as its heading. A no-tag release uses a
`source-<short-sha>` heading and persists the exact source end revision; its next entry starts
after that cursor, excluding the generated history and every declared mechanical output. A
no-tag release whose `steps.prepare` changed declared mechanical output still records a
metadata-only entry even when its source range is empty; this distinguishes a real new image
from a clean retained resume. A resumed retained transaction recognizes its marked entry (or
finds no new source or generated output beyond the cursor) and does not duplicate it.
`release.changelog = "path/CHANGES.md"` selects a different project-relative filename;
`release.changelog = false` is the explicit opt-out.

`cmru changelog --project P --backfill-tag <prefix><version>` is the one-time migration for a
tag that predates source-first history. It writes a generated `backfilled-after-release` entry
to the current source tree and never moves the immutable tag; the caller reviews and commits
that migration explicitly.

**S-REL.4c — Unmanaged-release cleanup.** `cmru cleanup --project P
--delete-unmanaged-release-tag TAG --yes` is a migration-only operation for an old GitHub
Release outside P's normal `<prefix>-v<semver>`/`<prefix>-latest` lifecycle. It requires the
explicit project namespace and confirmation (or `--dry-run`), deletes exactly one GitHub
Release with that tag, and MUST NOT delete its Git tag. A managed release is rejected; normal
project cleanup remains the sole operation allowed to delete managed Releases and tags.

**S-REL.4d — Local-build cleanup.** `cmru cleanup --project P --delete-build-output ID --yes`
deletes only the exact commit-addressed local build record identified by its `build.json`;
`--dry-run` is the non-mutating preview. `cmru cleanup --discard-build-worktree PATH --yes`
deletes only an exact, visible `cmru/build/*` worktree under this repository's managed
`.worktrees/` directory. Neither operation accepts a glob, an age range, an inferred latest
record, or a release worktree. A missing, incomplete, symlinked, or unauthenticated target MUST
fail rather than widen deletion.

**S-REL.4b — Release declaration.** `[project.release]` MUST contain `git_tag` and
`build_step`. `build_step` MUST name an explicit `[steps.<name>]` command. Optional
`commit_generated = ["<project-relative path>", …]` lists mechanical tracked outputs CMRU
may commit; optional `artifact_dirs` lists directories eligible for explicit retention.

**S-REL.5 — Reproducibility / commit model.** The isolated worktree starts clean, so wheels
cannot inherit unrelated caller dirt through setuptools-scm. cmru auto-commits **only**
declared mechanical outputs, never hand-edited source. Every project follows the same
prepare → gate → backup-push → promote → optional tag → `build_step` → `push` frame;
the project commands define what the last two phases do.
`backup-push` (S-CLI.5) is a durability step only — it pushes the validated branch to origin
under its own name, never touching `main`; the fast-forward of `main` remains a separate,
subsequent step.

**S-REL.6 — Multi-variant releases (per-interpreter artifact matrix).** A `bundle` or
`tarball` project MAY declare N named **variants** so that ONE release tag publishes one
artifact per variant. This exists for artifacts that cannot be interpreter-agnostic — e.g.
a bundle that carries version-locked C-extension wheels, where a single archive cannot serve
both a py39 and a py311 host.

- **Declaration** (`[[project.variants]]`, S2): each entry has a required, filename-safe
  `name` (V22), and optional `build_arg` (a build-time knob the project's build step consumes)
  and `label` (a human description surfaced by the installer). **Zero declared variants ⇒ the
  exact single-asset behaviour of prior versions** (no naming, latest.json, or installer change).
- **Asset naming** is deterministic: `<prefix>-v<version>-<name><suffix>` (e.g.
  `naf-v1.0.0-py39.tar.xz`), each with its `.sha256` sidecar, and for `bundle` a per-variant
  `manifest.json` + `manifest.json.minisig`, all uploaded under the single `<prefix>-v<version>`
  release. cmru MUST NOT publish two variants under two different tags.
- **latest.json** records the full variant list and every hash (see S5.3), so a consumer can
  enumerate and verify variants without listing the release's assets.
- **Resolution** is by `(tag, variant)`: `find_artifact(..., variant=<name>, suffix=<suffix>)`
  narrows a multi-variant `dist/` to exactly one file, so a build that produced several
  `<prefix>-v*` artifacts no longer trips the ">1 match" guard; genuine duplicates within a
  single variant still error.
- **Selection is explicit at install time** (S6.12): the target host has no interpreter to
  auto-detect, so the operator names the variant. cmru MUST NOT pick a silent default.

The single-asset publish keystone (`publish_versioned`) is unchanged; the variant matrix is a
separate keystone (`publish_versioned_variants`) so the legacy path is provably untouched.

---

## S2 — Config Schema

CMRU has exactly two non-overlapping documents (select a non-default path only with
`--config`). A portable product owns `<project>/cmru.toml`; it contains every fact
and command needed to test, build, publish, retain, and release that product in a fresh
repository root. An optional repository-root `cmru.orchestration.toml` names only those
project documents, their order/dependencies, and cleanup policy. The repository-root
secret document is the credential baseline; a selected project may explicitly overlay it
from its own folder as defined in S2.4. Secrets are never committed.

**S2.1** The config MUST be validated on startup. An invalid config MUST cause an exit 2 (S8).

**S2.2 — Project document** (`<project>/cmru.toml`):

An orchestration run may declare shared non-secret build/gate inputs once in
`[orchestration.defaults.env]`.  Those values are resolved first, then a
project's `[env]` deliberately overrides a key only when it has a distinct
requirement.  A project run directly has no estate policy to invent, so it
must declare or receive every required input explicitly.

```toml
schema_version = 1

[github]
owner = "your-github-owner"        # required
repo = "your-repository"           # required
owner_type = "user"                # required: user | org

[targets]
host     = "github"               # required: provider for releases
registry = ["ghcr.io"]            # list: image registries to push to (S11)

[env]
CMRU_WHEEL_BUILDER_IMAGE = "wheel-builder@sha256:<digest>"       # required by wheel-build
CMRU_TESTER_UNIFIED_IMAGE = "tester-unified@sha256:<digest>"     # required by tester-gate
CMRU_TESTER_MEMORY = "3g"                                         # required by tester-gate
CMRU_TESTER_MEMORY_SWAP = "16g"                                   # required by tester-gate
CMRU_TESTER_CPUS = "1.5"                                          # required by tester-gate
CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian@sha256:<digest>"         # required by tester-gate
# CMRU_TESTER_DIND_IMAGE = "docker@sha256:<digest>"               # required with --enable-docker

[project]
id          = "example"           # required, lowercase project id
description = "consumer-facing product summary"
template_revision = 4              # required for `cmru standards` conformance
prefix      = "<name>-v"          # required: tag prefix
artifacts   = ["wheel"]           # required: wheel | oci-image | tarball | bundle
scm_dist    = "<name>"            # optional: python dist name (for wheel type)

[project.version]
strategy = "scm"                  # scm | file:PATH | counter | external:VAR | none
paths    = ["shared-input/"]      # optional project-relative extra watch paths
bump     = "conventional"         # conventional | patch

[project.release]
git_tag = true                              # required; only source of tag policy
build_step = "build"                         # required; one declared [steps.<name>]
commit_generated = ["generated-input.json"]  # project-relative, mechanical only
artifact_dirs = ["dist"]                     # required only when retaining artifacts
# changelog defaults to "CHANGES.md". Override only for another project-relative path;
# `changelog = false` is the explicit opt-out.

[steps.run-tests]
quiet = true                        # every step MUST declare console detail policy
commands = [{ label = "example gate", argv = ["cmru", "tester-gate", "--cwd", ".", "--", "/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"], cwd = "." }]

[steps.build]
quiet = true
commands = [{ label = "example build", argv = ["python3", "build.py"], cwd = "." }]
clean_dirs = ["dist"]
required_env = ["SOURCE_DATE_EPOCH"]
env_command = ["python3", "scripts/derived-env.py"] # argv; stdout must be KEY=VALUE
bake_set_prefix = "base.args."
bake_set_vars = ["OCI_VERSION"]
no_cache_env = "BUILD_NO_CACHE"

[steps.push]
quiet = true
commands = [{ label = "example publish", argv = ["python3", "publish.py"], cwd = "." }]

[project.installer]                 # inputs for the emitted get.py installer (S6)
install_dir_system = "/opt/<name>"        # system-scope root
install_dir_user   = "<name>"             # leaf under $XDG_DATA_HOME/<name>
asset_suffix       = ".tar.xz"            # release asset filename suffix
entrypoint         = "scripts/adapter.py" # project adapter, relative to release root (optional)
required_commands  = ["python3", "docker", "minisign"]   # checked pre-network (exit 3)
preserve           = ["shared/host.toml"] # paths kept in <root>/shared/ across updates
manifest_name      = "manifest.json"      # manifest file inside the bundle
signature_name     = "manifest.json.minisig"  # minisign signature for manifest

[[project.installer.wheels]]         # bundled wheels to install into private venv
path         = "vendor/cmru-*.whl"  # glob inside the release bundle
distribution = "cmru"               # pip distribution name

[[project.installer.wheels]]
path         = "vendor/ciu-*.whl"
distribution = "ciu"

# Optional per-interpreter variants (S-REL.6). Zero entries ⇒ single-asset behaviour.
# Each variant publishes one asset (<prefix>-v<version>-<name><suffix>) under the same tag;
# the operator selects one at install time via get.py --variant <name>.
[[project.variants]]
name      = "py39"                 # required: filename-safe token (V22); used in the asset name
build_arg = "PYTHON_VERSION=3.9"   # optional: build knob the project's build step consumes
label     = "Python 3.9 (glibc)"   # optional: human description shown by the installer prompt

[[project.variants]]
name      = "py311"
build_arg = "PYTHON_VERSION=3.11"
label     = "Python 3.11 (glibc)"

# NOTE: `[projects]`, `artifact`, `cwd`, project aliases, `[project.oci]`, and
# cmru.build.toml are retired and rejected. There is no compatibility parser.
```

**S2.2a — Orchestration document** (`cmru.orchestration.toml`) is a separate,
strictly estate-level document:

```toml
schema_version = 1
[orchestration]
project_order = ["example"]
default_projects = ["example"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"

[orchestration.project.example]
config = "example/cmru.toml"         # project-relative, exact filename
depends_on = []

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["example-latest"]
ghcr_packages = ["*"]
ghcr_delete_packages = []
```

**S2.3** One strict reader validates every CMRU verb before it interprets the config.
Unknown and retired keys MUST be rejected (exit 2); required fields MUST be present.
`cmru standards` additionally reports the project-template revision, release-history policy,
required release gate, and summary-only default step output. Where a command invokes
`tester-gate`, it requires explicit image/resource/probe `[env]` inputs; where it invokes
`wheel-build`, it requires an explicit wheel-builder image. A Docker-enabled tester gate
also requires its nested-Docker image. `--update` may update only the
project TOML revision marker; it MUST NOT rewrite project-owned command bodies. A project document MUST explicitly declare
`run-tests`, `push`, and the named `release.build_step`; every step MUST explicitly set
`quiet = true|false`. A standards-conforming project MUST set `quiet = true` for every
declared step; `--show-run-details` is the explicit live-detail override.

**S2.4** Token resolution, so project `cmru.toml` stays secret-free:
1. `GITHUB_PUSH_PAT` env var, then `GITHUB_TOKEN` env var.
2. Deep merge repository-root `cmru.secret.toml` with the selected
   `<project>/cmru.secret.toml`; the nearer project table wins. The merged
   `[github].token` is the credential.

For an orchestration invocation, the root is the directory containing
`cmru.orchestration.toml`. For a portable one-project invocation, it is the directory
containing that project's `cmru.toml`. The secret grammar is strict:

```toml
# <repository-root>/cmru.secret.toml
[github]
token = "…"                         # repository-wide credential

# <project>/cmru.secret.toml (optional explicit override)
[github]
token = "…"
```

`[github].token` in a committed `cmru.toml` is rejected. There is no fallback
credential source. `tester-gate` likewise has no built-in inputs: it requires `--image`
or `CMRU_TESTER_UNIFIED_IMAGE`, plus explicit memory, combined memory/swap, CPU, and
host-systemd probe-image values (CLI options or the effective `[env]`). `--enable-docker` additionally
requires a nested-Docker image. `wheel-build` requires `CMRU_WHEEL_BUILDER_IMAGE`; CMRU
does not fall back to the cockpit's Python environment.

If none is found and a write verb is invoked, cmru MUST exit 3 (V10).

**S2.5 — Cleanup selectors are explicit.** In `cmru.orchestration.toml`, an
empty `cleanup.release_tag_prefixes` or `cleanup.ghcr_packages` list selects
nothing. Only an explicit `"*"` selects every release or package. CMRU MUST
never reinterpret an empty destructive selector as a wildcard.

---

## S3 — Single Runner Contract

Every build step MUST be executed through the cmru runner. The orchestrator MUST NOT invoke build commands directly.

**S3.1** Required runner capabilities:

| Capability | Description |
|---|---|
| `login` | Pre-step registry/host authentication |
| `required_env` | Fail if listed env vars are absent (exit 3, S8) |
| `clean_dirs` | Wipe output directories before build |
| `env_command` | Explicit argv which prints `KEY=VALUE` lines; no shell sourcing |
| `bake --set` | Inject build args into Docker buildx bake |
| `no_cache_env` | An explicit env flag which appends `--no-cache` |
| `per-step logs` | Each step writes to its own log file |
| `reproducible-env` | Set `SOURCE_DATE_EPOCH` from HEAD commit timestamp |

**S3.2** The project document’s `[steps.<name>]` uses an explicit `commands` list and
may use the runner controls below. No second runner config exists:

```toml
commands      = [{ label = "build", argv = ["docker", "buildx", "bake", "all"], cwd = "." }]
login         = { registry = "ghcr.io", username_env = "GITHUB_USERNAME", token_env = "GITHUB_PUSH_PAT", required = true }
required_env  = ["GITHUB_TOKEN"]
clean_dirs    = ["dist/"]
env_command   = ["python3", "scripts/derived-env.py"]
no_cache_env  = "BUILD_NO_CACHE"
bake_set_prefix = "base.args."
bake_set_vars = ["OCI_VERSION"]
```

**S3.3** The runner MUST set `SOURCE_DATE_EPOCH` to the Unix timestamp of the HEAD commit before every step.

**S3.4** Step logs MUST be line-flushed and written to the stable path
`<project>/logs/cmru/<step>.log`; a normal run overwrites that path. `--log-append`
MUST insert `\n---\n` before the new record. The root `cmru.release.sh` wrapper overwrites
`cmru.release.log` by default and includes the full subprocess transcript even while the
console is quiet. By default the orchestration console shows labels, elapsed time, known
test-framework success evidence, and failure excerpts only. `--show-run-details` streams
the raw project output to the console as well. The runner MUST flush every received line;
it sets `PYTHONUNBUFFERED=1` for Python children, while non-Python programs remain responsible
for their own stdout buffering. `--log-prefix-time-short` is a process-wide presentation
choice: every CMRU line that already starts `[INFO]`, `[WARN]`, or `[ERROR]` is emitted as
`HH:MM:SS [TYPE] …`, including transaction-child output. Interactive terminals colour those
three severity tokens green/yellow/red; redirected stdout/stderr is deliberately ANSI-free so
the stable logs and machine consumers retain plain text.

**S3.5 — Transaction evidence lifecycle.** Release failure MUST retain its worktree, logs,
and artifacts for inspection/resume. Successful release MUST remove the worktree by default.
`--retain-logs-on-release` moves its project logs to
`<project>/logs/cmru-release/<immutable-id>/`; `--retain-artifacts-on-release` moves the
declared `project.release.artifact_dirs` to `<project>/artifacts/<immutable-id>/` and writes
`release.json` with source commit and SHA-256 inventory. `cmru build` MUST use an isolated
`cmru/build/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>` worktree (S-CLI.5b). On child success it MUST copy that project's logs to
`<project>/logs/<commit-date>_<full-commit>/` and every declared
`project.release.artifact_dirs` directory to
`<project>/artifacts/<commit-date>_<full-commit>/`, write a `build.json` SHA-256
inventory, then remove the worktree. The coordinate is the built HEAD's UTC commit timestamp
and full SHA; an existing coordinate is an error, never an overwrite. `build.json` MUST record
that publication is forbidden and any tracked source-tree changes, so it cannot be confused with
a release candidate. A child or retention failure MUST retain the worktree and print its exact
path for debugging; use `cmru worktrees` to discover it and the exact cleanup verb after
inspection.

---

## S4 — Publication boundary

**S4.1** `cmru publish` requires a resolved publication credential (exit 3 when absent),
then runs the selected project's explicit `push` step through the unified runner. It does not
discover an artifact, choose a host, or infer a release-asset policy.

`publish` is a low-level caller-worktree operation. It is not the second half of
`cmru build`: a normal build retains artifacts as explicitly non-publishable local records,
not at the caller's declared push input. The source-first composable operation is `cmru release`; see
[`KI-10`](../KNOWN_ISSUES_TODO_BACKLOG.md#ki-10--cmru-build-artifacts-cannot-safely-feed-cmru-publish--open-decision-required).

**S4.2** A project command that publishes a GitHub Release asset MUST create a `.sha256`
sidecar in `sha256sum -c` format and bind the release to the build commit. It SHOULD include
the artifact digest in release notes; an OCI publisher SHOULD also verify the final registry
manifest digest.

**S4.3** A project that maintains a `<prefix>latest/latest.json` pointer MUST update it in
the same explicit push contract. `cmru resolve` can consume that pointer, but CMRU does not
invent one for a project that did not choose it.

**S4.4** CMRU's reusable wheel/tarball handler commands implement the S4.2/S4.3 GitHub
Release convention. Projects with another publication mechanism must provide equivalent
consumer-verifiable evidence in their own step and documentation.

**S4.5** Dev builds (untagged commits, version contains `.dev`) MUST NOT mint a `<prefix>-v`
release. They MAY publish to a project-owned development channel.

**S4.6** A project that publishes OCI to GHCR SHOULD document the one-time package-visibility
operation. GitHub currently offers no usable API for changing container-package visibility;
CMRU must not report a visibility change as an enforced release guarantee.

---

## S5 — Resolver

The resolver implements differentiator #2: highest-semver selection, replacing GitHub's single repo-global "Latest" badge.

**S5.1** `cmru resolve --project <name>` returns `{version, tag, asset, sha256, url}` for the highest-semver release matching `prefix`.

**S5.2** Semver comparison MUST be numeric-aware per segment: `r10 > r2 > r1` (not lexicographic).

**S5.3** If `latest.json` exists for the project, the resolver SHOULD use it as the primary source (one API call vs. paginated scan). Format:

```json
{
  "project": "tls-edge",
  "version": "0.2.0",
  "tag": "tls-edge-v0.2.0",
  "asset": "tls-edge-v0.2.0.tar.xz",
  "sha256": "<hex>",
  "url": "https://github.com/…/releases/download/tls-edge-v0.2.0/tls-edge-v0.2.0.tar.xz"
}
```

For a **multi-variant** release (S-REL.6) the pointer instead records a `variants` array —
one entry per interpreter variant, each with its own `asset`, `sha256`, `url`, and optional
`label` — and carries no single top-level `asset`/`sha256` (there is no single artifact):

```json
{
  "project": "naf",
  "version": "1.0.0",
  "tag": "naf-v1.0.0",
  "variants": [
    {"name": "py39",  "asset": "naf-v1.0.0-py39.tar.xz",  "sha256": "<hex>", "url": "https://…/naf-v1.0.0-py39.tar.xz",  "label": "Python 3.9"},
    {"name": "py311", "asset": "naf-v1.0.0-py311.tar.xz", "sha256": "<hex>", "url": "https://…/naf-v1.0.0-py311.tar.xz", "label": "Python 3.11"}
  ]
}
```

**S5.4** Fallback if latest.json is absent or stale: scan releases via host API, filter by prefix, select max semver.

**S5.5** `--format` flag: `json` (default), `env` (shell-sourceable `KEY=value` lines), `url` (bare download URL).

---

## S6 — get.py Contract (Transactional Installer)

The emitted `get.py` is a per-project **transactional** bootstrap that handles install,
update, rollback, and status. Unlike a curl-only bootstrap, `get.py` ships **inside** the
release artifact, so `<project> update` works out of the box. Configuration lives in
`[project.installer]` (see S2).

**S6.1** `cmru get-py --project <name> --config cmru.toml` emits a standalone Python 3
installer to stdout. The output is a rendering of `templates/get.py.tmpl` with
`[[VARNAME]]` placeholders replaced from the `[installer]` config. The rendering is
deterministic (byte-identical for identical config). Any unreplaced `[[...]]` placeholder
triggers a warning.

**S6.2** Commands emitted:

```
get.py install  --config HOST.toml [--version TAG] [--scope system|user]
get.py update   [--version TAG] [--scope system|user]
get.py status   [--scope system|user]
get.py rollback [--version TAG] [--scope system|user]
```

**S6.3** Transactional pipeline (install / update):

1. **Pre-flight** — check `required_commands` BEFORE any network I/O (exit 3 if missing).
2. **Resolve** — resolve the highest-semver `TAG_PREFIX*` release via the GitHub Releases API,
   or use `--version`. Public requests carry **no** Authorization header. Private assets are
   resolved by API asset-ID with the Authorization header stripped before the CDN redirect.
3. **Download** — fetch `<tag><asset_suffix>` + its `.sha256` sidecar. For a multi-variant
   release the selected variant (S6.12) changes the asset name to
   `<tag>-<variant><asset_suffix>` (+ matching `.sha256`).
4. **Verify SHA256** — recompute and compare; mismatch → exit 1, before extraction.
5. **Verify minisign** — if `--manifest-pubkey` is supplied (or pubkey in host config),
   extract `manifest_name` + `signature_name` from the bundle and run
   `minisign -Vm manifest.json -P <pubkey>` (or `-p <pubkey-file>`). Failure → exit 1.
6. **Stage** — extract into `<root>/releases/<tag>.staging/` with `filter="data"` (py≥3.12)
   plus a pre-scan that rejects: absolute paths, `..` traversal, device nodes, absolute
   symlinks, and symlink/hardlink traversal escapes.
7. **Install wheels** — if `installer.wheels` is non-empty, create `<root>/venv` via
   `python3 -m venv` and `venv/bin/pip install --no-index <wheel>` for each glob match.
   Wheel sha256s from the manifest are verified before pip install (exit 1 on mismatch).
8. **Invoke adapter** (`bootstrap` on install, `apply` on update) — if `entrypoint` is set.
   Non-zero exit aborts before the `current` swap (previous release stays live).
9. **Atomic swap** — `os.symlink` to a temp name + `os.replace` onto `current`.
10. **Finalize** — rename `.staging` → final release dir; prune old releases (keep 2 by default).

**S6.4** Release layout:

```
<root>/releases/<tag>/    # immutable dir per installed version
<root>/current            # symlink → releases/<current-tag>  (atomic swap)
<root>/shared/            # preserved config/state (never inside releases/)
<root>/venv/              # private interpreter; bundled wheels live here
```

`<root>` = `install_dir_system` (system scope) or `$XDG_DATA_HOME/<install_dir_user>` /
`~/.local/share/<install_dir_user>` (user scope).

**S6.5** Preserve: files in `installer.preserve` are copied to `<root>/shared/` before
staging and symlinked back into the new release dir after extraction. They survive across
updates and rollbacks.

**S6.6** Rollback: `get.py rollback [--version TAG]` re-points `current` to the previous
(or named) release dir and re-runs the adapter with `action=rollback`.

**S6.7** Scope-exclusive lock (`flock` on `<root>/.lock`) serialises concurrent invocations.
SIGINT/SIGTERM handler cleans up staging dir and releases the lock.

**S6.8** Adapter invocation contract (Seam 1):

```
<root>/venv/bin/python <root>/current/<entrypoint> <action> \
    --release-root <root>/releases/<tag> \
    --config <root>/shared/host.toml \
    --manifest <root>/releases/<tag>/manifest.json
```

`<action>` ∈ `{bootstrap, apply, health, rollback}`. Non-zero adapter exit → exit 1.
The GitHub token is **stripped** from the child-process environment.

**S6.9** The installer is Python 3 **stdlib-only** (urllib/tarfile/hashlib/argparse/fcntl);
no third-party dependencies. `minisign`, `docker`, and the project adapter are shelled out.

**S6.10** Auth (token) precedence: `--github-token` (warns: leaks via ps/history) >
`--github-token-file FILE` (rejected if loose perms / wrong owner) > `--github-token-stdin`
> `CMRU_GITHUB_TOKEN` / `GITHUB_TOKEN` env. Token is never logged in full.

**S6.11** `install_dir_user` degrades gracefully: if `entrypoint` is empty and `wheels`
is empty, no adapter is called and no venv is created (tls-edge minimal path).

**S6.13** `--version <TAG>` pins the install to a specific tag (bare semver or full tag). Arguments go to the right side of the pipe (`curl … | sudo python3 - install --version …`), so there is no env-var-across-pipe footgun.

**S6.12** **Variant selection (multi-variant releases, S-REL.6).** When the emitted `get.py`
carries a non-empty `VARIANTS` list, the operator MUST select one at install/update time —
the target webhoster has no interpreter to auto-detect, so there is **no silent default**.
Resolution order (first hit wins):

1. `--variant NAME` — explicit; rejected with the available list if unknown (exit 2).
2. A variant remembered from a prior install (persisted at `<root>/shared/.variant`), so
   `update` stays on the host's interpreter unless `--variant` overrides it.
3. On an interactive TTY: a numbered prompt listing each variant's `name` (and `label`).
4. Otherwise: a fatal error (exit 2) that lists the available variants.

The chosen variant is written to `<root>/shared/.variant` (preserved across updates) and
drives the download asset name (`<tag>-<variant><asset_suffix>`, S6.3 step 3). `update` is a
no-op ("already at …") only when **both** the resolved version **and** the selected variant
already match what is installed — so `update --variant OTHER` at the current version
re-installs the other variant rather than being short-circuited. When `VARIANTS` is empty
every step above is skipped and the installer behaves **byte-for-byte** as before (single
asset `<tag><asset_suffix>`).

---

## S7 — External supply-chain tooling (not yet a CMRU config feature)

CMRU does **not** accept a `[project.delegated]` table. Earlier documentation
advertised one even though no release lifecycle invoked it; the strict schema rejects it.
No missing-tool path may silently skip a requested security or packaging operation.

Candidate integrations and their required contracts are recorded in KI-04. Before one is
added, it MUST have an explicit artifact/digest input, an output location and publication
rule, a fail-closed prerequisite policy, provenance binding, and an end-to-end release test.
The source-first `CHANGES.md` transaction record remains the CMRU-native release history;
`git-cliff` is not a replacement for it.

---

## S8 — Exit Codes

cmru uses a four-value exit code scheme identical to CIU S10.3:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Build or publish failure (artifact error, upload failed, tag push failed) |
| `2` | Configuration error (missing required field, unknown key, parse error) |
| `3` | Missing prerequisite (required environment variable, installer command, or external tool absent) |

---

## S9 — Reproducibility

**S9.1** `SOURCE_DATE_EPOCH` MUST be set to the Unix timestamp of the HEAD commit before every build step (runner responsibility, S3.3).

**S9.2** OCI image labels `org.opencontainers.image.created` etc. MUST be sourced from HEAD commit metadata, not `date`.

**S9.3** For the `scm` versioning strategy, the clean version string (no `.dev`) is only emitted on an annotated tag. Untagged builds MUST produce a dev suffix.

**S9.3a** `cmd_wheel_build` (`cmru/src/cmru/handlers.py`) MUST require an explicit
`CMRU_WHEEL_BUILDER_IMAGE` and bind-mount the checkout's git common directory into that
builder container, not only the
project subtree. A release worktree's own `.git` is a file pointing to an *absolute path
outside that subtree* (`gitdir: <repo_root>/.git/worktrees/<name>`); mounting only the subtree
makes that pointer unresolvable. CMRU therefore requires a resolvable Git worktree before it
invokes the builder; it never accepts a static fallback version. `_wheel_builder_git_mount_args`
supplies this mount and is a no-op
(nothing extra to mount) for an ordinary non-worktree checkout, where the common dir is already
covered by the existing subtree mount.

**S9.4** Given the same source commit and toolchain pin, two independent builds MUST produce byte-identical artifacts (deterministic build contract). For the `bundle` profile specifically:

- Archive membership comes from an explicit git-tracked allowlist (never a recursive walk).
- Every `TarInfo` is normalized: `mtime = SOURCE_DATE_EPOCH`; `uid = gid = 0`;
  `uname = gname = ""`; mode = `0o644` (files) / `0o755` (executable files);
  members sorted by path in byte order.
- Compression: `tarfile` with `mode="w:xz"` (fixed format; no timestamp in container).
- Hard excludes applied belt-and-suspenders: `.git`, `.ciu`, rendered `*.toml`, `ciu.env`,
  `minisign.key`, `__pycache__`, `*.pyc`, `*.log`, `*.pem/.key/.crt` and similar.
  The source-package metadata file `pyproject.toml` is the deliberate exception when
  its parent path is explicitly allowlisted (for example, a bundled companion client).
- The production `run_bundle()` path MUST use this normalized writer for `xztar`; it
  must not bypass membership filtering through `copytree`/`make_archive`.
- A **build-twice gate** in the test suite asserts identical sha256 across two builds from
  the same `SOURCE_DATE_EPOCH`; flipping the epoch asserts the digest changes.

**S9.5** `manifest.json` MUST be serialized canonically so it is itself deterministic:
UTF-8, `sort_keys=True`, `separators=(",", ":")` (compact, no spaces), trailing newline.
Two builds of the same inputs MUST produce byte-identical `manifest.json`. The `created`
field is derived from `SOURCE_DATE_EPOCH` (`datetime.fromtimestamp(epoch, tz=UTC)`), never
wall-clock time.

---

## S10 — Validation Catalog

_This section enumerates all config validation rules. Each rule references the section that defines the requirement._

| ID | Rule | Exit |
|---|---|---|
| V01 | `[github].owner` is present and non-empty | 2 |
| V02 | `[github].repo` is present and non-empty | 2 |
| V03 | `[github].owner_type` is `"user"` or `"org"` | 2 |
| V04 | `[targets].host` is a known provider (S11) | 2 |
| V05 | Each project document has a unique `prefix` in an orchestration set | 2 |
| V06 | `artifacts` contains only `wheel\|oci-image\|tarball\|bundle` | 2 |
| V07 | `version.strategy` is `scm`, `file:<path>`, `counter`, `external:<VAR>`, or `none` | 2 |
| V08 | `version.bump` is `conventional` or `patch` | 2 |
| V09 | No unknown keys at any config level (including `[getsh]` — retired; use `[installer]`) | 2 |
| V10 | `GITHUB_PUSH_PAT`/`GITHUB_TOKEN`, repository-root ignored token document, or selected project ignored token overlay present (for publish) | 3 |
| V11 | All `required_env` vars present before step execution | 3 |
| V13 | `[installer].install_dir_system` is required when `[installer]` is present | 2 |
| V14 | `[installer].install_dir_user` is required when `[installer]` is present | 2 |
| V15 | `[installer.wheels[*]].path` and `.distribution` are required | 2 |
| V16 | `installer.required_commands` are checked before network I/O (exit 3) | 3 |
| V17 | Token file for `--github-token-file` must be owned by current user and chmod 600 | 2 |
| V22 | `[[project.variants]].name` is present, unique, and filename-safe (`[A-Za-z0-9][A-Za-z0-9._-]*`); unknown variant keys are rejected | 2 |

---

## S11 — Targets & Host Abstraction

**S11.1** `ReleaseHost` interface. Any release host provider MUST implement:

```python
class ReleaseHost:
    def create_release(self, tag, name, body, commitish, draft, prerelease) -> str: ...
    def upload_asset(self, release_id, path, content_type) -> str: ...
    def list_releases(self, prefix) -> list[dict]: ...
    def resolve_latest(self, prefix) -> dict: ...
    def download_url(self, tag, asset_name) -> str: ...
```

**S11.2** v1 ships only the GitHub implementation. Gitea/Forgejo and S3/MinIO object-store are fast-follow; new hosts MUST implement S11.1, not be hard-coded.

**S11.3** `[targets].registry` is a list of OCI registries. The runner MUST push one image to each registry in a single `docker buildx bake` invocation using bake's tag matrix.

**S11.4** GH Enterprise is nearly free: `api_base` is already a parameter on the GitHub implementation.

---

## S12 — Versioning & Release Trigger

**S12.1** `cmru status` performs a dry-run: for each project, reports whether the subtree changed since last `<prefix>-v*` tag and what version would be minted.

**S12.2** Change detection: a project is eligible for release iff `git log <last_tag>..HEAD -- <paths>` is non-empty after excluding CMRU release-control files and generated release-history documents. If no prior tag exists, the project is always eligible (first release).

**S12.2a — The release plan's baseline MUST reflect the pushed repository (KI-12a).**
`git tag --list` alone returns local-only refs; a hand-made, never-pushed tag would
otherwise silently become `<last_tag>` in S12.2's own comparison for every operator who runs
the identical command on the identical commit — contradicting the isolation S-CLI.5 already
establishes for the rest of the transaction. Chosen fix: keep the local `git tag --list` read
(no unconditional network dependency for every caller), but when computing the isolated
release transaction's own plan (`cli.py`'s release-plan computation, not `cmru status` or
`cmru changelog`), additionally verify against `origin` in both directions `git ls-remote`
can be wrong about:

1. **Object, not just name.** A local tag whose NAME exists on `origin` can still point at a
   DIFFERENT commit there — checking only ref existence would pass a hand-made tag created
   locally over an already-published one, and every later decision (the version this tag
   implies, S12.2b's tag-vs-HEAD comparison) would then silently run against the wrong,
   local-only object. The selected `<last_tag>` MUST resolve to the exact same commit locally
   and on `origin` (`git ls-remote --exit-code --tags origin refs/tags/<tag>
   refs/tags/<tag>^{}` — one call covers annotated and lightweight tags, comparing the
   resolved SHA either way against local `git rev-parse <tag>^{commit}`); a name match with an
   object mismatch refuses, naming both SHAs, distinct from "absent entirely".
2. **Origin, not just local, may be ahead.** `origin` MAY carry a higher matching tag than
   this local clone has ever fetched (another operator's release, never pulled here). Using a
   stale local maximum would derive a version that already exists and fail mid-release, after
   `origin/main` has already been promoted for that project — exactly the "ahead" half-completed
   state S12.2b aborts on, just reached a different way. Checked via `git ls-remote --tags
   origin refs/tags/<prefix>*`, compared against the local maximum by the same semver ordering
   — even when the local clone has no matching tag at all (a believed-first-release that
   `origin` secretly already has one for). A newer/different remote tag refuses with a "fetch
   tags and re-run" remedy.

`git ls-remote` is a network call in an otherwise-offline-capable path; an unreachable `origin`
is therefore its own distinct refusal in both checks above (never conflated with "tag not
found" or "nothing published yet", and never silently ignored).

**S12.2b — A tag AHEAD of the snapshot commit MUST abort; a tag EQUAL to it is a benign,
informative skip, never an error (KI-12b).** Once S12.2's `git log <last_tag>..HEAD -- <paths>`
is found empty for a project with a prior tag, `<last_tag>`'s commit relative to the commit
being evaluated (HEAD) is exactly one of three states — `git merge-base --is-ancestor` alone
cannot tell the first two apart, so the release plan resolves and compares the commit objects
directly:

1. **Equal** — `<last_tag>`'s commit IS HEAD. This is the ordinary, expected state immediately
   after any successful release (nothing has landed anywhere in the repository since). It MUST
   NOT abort and MUST NOT be treated as evidence of a hand-made tag: it is reported as an
   informative skip naming the tag, e.g. `Unchanged, skipping: demo (already released as
   demo-v1.0.0 at the snapshot commit; nothing new since)`.
2. **Ahead** — `<last_tag>`'s commit is a strict descendant of HEAD: the tag exists (and is
   pushed — S12.2a already ruled out the unpushed case) on a commit that is not yet in this
   snapshot's history at all. This is the genuine anomaly: almost always a previous release that
   tagged and pushed this project but failed before promoting `origin/main` to that commit (a
   half-completed release). The isolated release transaction's plan computation MUST abort with
   an error naming the project, the tag, the snapshot commit, that likely cause, and the remedy
   (continuing would silently produce an empty release for a project that already has unpromoted
   work waiting). `cmru release --allow-tag-ahead-of-head` downgrades this one refusal — and only
   this one — back to an ordinary skip, for the deliberate case (`--allow-tag-at-head` is a
   deprecated alias kept for compatibility: after this three-state fix the "equal" case never
   aborts, so the old name no longer describes what the flag actually overrides).
3. **Behind** — `<last_tag>`'s commit is a strict ancestor of HEAD (the ordinary case: some
   other project's commits moved HEAD forward, nothing under this project's own paths changed).
   Indistinguishable from, and handled identically to, S12.2's plain "genuinely unchanged" skip.

`cmru status` and `cmru changelog` are previews/migrations, not the release plan itself, and
keep S12.2's plain skip-silently behaviour for all three states (no informative message, no
abort).

**S12.2c — cmru owns tag creation.** A cmru-managed project's `<prefix>-v*` tags MUST only be
created by cmru's own versioning strategies (S12.5). A hand-made tag is indistinguishable from
a completed release: an unpushed one is exactly S12.2a's refusal, and a pushed one sitting
ahead of the snapshot commit is exactly S12.2b's "ahead" abort (whose far more common real cause
is actually a half-completed cmru release, not a hand-made tag — but the tool cannot tell them
apart from git state alone). Never tag a cmru-managed project by hand.

**S12.2d — A release-plan refusal (S12.2a/S12.2b) is a typed, clean failure that discards its
worktree.** No project's `prepare`/gate/promote/tag cycle has started when the plan itself
refuses — nothing was gated, promoted, or tagged, and the durability backup branch (S-CLI.5a)
was never pushed either, since it is pushed only after the plan is accepted. The isolated
release transaction MUST therefore surface this as a clean operator-facing `[ERROR]` message
(never a raw Python traceback) and discard the just-created worktree/branch exactly like a
successful release would — never retain it the way a genuine mid-release failure is retained
for inspection (S-CLI.1), since there would be nothing there to inspect. This exit still uses
the ordinary `1` ("build or publish failure") from S8's four-value scheme — S8 is not extended
for this — the transaction records the refusal as its own state (alongside the existing scope
marker, S-CLI.5a) so the parent process can tell "refused before starting" apart from "failed
after starting" without a new exit code.

**S12.3** Change detection always watches the project directory. Additional project-relative shared paths MAY be listed in `project.version.paths`.

**S12.4** Version bump rules (in priority order):
1. `--set-version <v>` — explicit override.
2. `--major` / `--minor` — force bump level.
3. `conventional` strategy: scan commits since last tag; `feat:` → minor, `BREAKING CHANGE` or `!` → major, all else → patch.
4. `patch` strategy: always increment patch.

**S12.5** Versioning strategies:

| Strategy | Mechanism | Commit? |
|---|---|---|
| `scm` | Tag HEAD; setuptools_scm reads it | No extra commit |
| `file:<PATH>` | Write version to file, commit, then tag | Yes (one bump commit) |
| `counter` | Find latest `-r<N>` suffix, increment; tag HEAD | No extra commit |
| `external:VAR` | Read VAR from `<cwd>/cmru.vars` after prepare; tag HEAD | Prepare commit, if changed |

**S12.6** Dev builds: when HEAD is untagged, the version MUST be `X.Y.Z.devN+g<hash>`. These MUST NOT produce a `<prefix>-v` tag or immutable release.

**S12.7** `cmru release` MUST use the isolated transaction in S-CLI.5. It rejects dirty
release inputs, not unrelated caller paths; the child worktree itself MUST remain clean
except for declared generated paths at their permitted lifecycle point.

**S12.8** Commit/tag ordering: for `file` strategy — write VERSION, stage, commit, then tag. For `scm`/`counter` — tag HEAD directly. In all cases: tag first, then build, then publish.

---

## S14 — Explicit OCI Command Library

`python3 -m cmru.handlers oci-image-build --cwd . --bake-file docker-bake.hcl --target all`
and `oci-image-push` are optional commands a project may place in its required build and
push steps. CMRU never selects them automatically and has no `[project.oci]` table.

The normal helper path verifies Docker/Buildx, uses Docker's native credential store, then
runs `docker buildx bake -f <bake-file> <target> --load` or `--push`. A publishing
transaction preflights its GitHub credential before source or host state changes; a handler
also refuses missing Docker prerequisites with exit 3.

`--repack` remains deliberately fail-closed with exit 2 before login, Docker, or filesystem
mutation. It is not a supported CMRU release feature. A project that needs OCI repacking
MUST own an explicit tested command and its reproducibility, resource-governance, digest
verification, and runtime-smoke evidence. MDT is the estate example; its implementation is
not silently generalized as a CMRU profile.

---

## S13 — Reserved / Out of Scope

The following are explicitly **out of scope** for cmru v1 and MUST NOT be implemented:

- macOS/Windows code signing (Authenticode, Apple notarization).
- FTP/SFTP deploy targets (e.g., netcup `deploy.zip`). These are deploy operations, not releases.
- New release hosts beyond GitHub v1 (fast-follow, via S11 interface only).
- Adding an external supply-chain tool without the artifact/digest, output, prerequisite,
  provenance, and end-to-end-release contracts required by S7. The source-first release
  history required by S-REL.4a is CMRU's own transaction record.
